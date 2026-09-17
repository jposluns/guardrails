#!/usr/bin/env python3
"""OPF unit U7: import operation layer (scan / plan / review / apply) + staging (the live `import` verb is
wired by OPF-IMPORT-VERB, in opf.py `_cmd_import`).

OPF-IMPORT-OPS adds the operation layer beneath the `opf import [--root DIR] (--scan --set FILE | --plan
--set FILE | --review <run-id> --actor NAME (--decisions FILE | --interactive) | --apply <run-id>)`
grammar (wired by OPF-IMPORT-VERB in opf.py), composing the settled `stage_import` staging primitive rather than replacing
it (spec 14.1, Fable-synthesized plan):
  - `scan_import(product_root, import_set) -> ScanResult`: a deterministic, digest-stamped, READ-ONLY
    enumeration of the declared import set (one whole-file fragment per source; the baseline extractor).
  - `plan_import(product_root, import_set, *, proposals=None, now, run_nonce) -> PlanResult`: scans, applies
    the deterministic whole-file baseline classification (every fragment `unmapped`, `origin = "baseline"`,
    preserved as a legacy_fragment; model proposals stay INERT), stages the candidate via `stage_import`,
    and writes the inventory + proposals.toml + IMPORT-REPORT.md review surface (spec 4.2).
  - `review_import(product_root, run_id, *, actor, decisions, now) -> ReviewResult`: the `--review`
    acceptance-capture core (spec 14.1). It records an attributed, per-fragment accept/reject decision set
    into a canonical-JSON `acceptance.json` bound to the exact run (run id + plan digest + inventory digest),
    validating completeness + the origin/proposed_state echo and requiring an explicit accept for every
    model_proposal resting mapping. It NEVER mutates the active store and NEVER re-plans (a reject makes a
    later apply refuse). `review_import_interactive` is a thin TTY front-end funnelling into the same core.
  - `apply_import(...) -> ApplyResult`: promotion is DEFERRED to the OPF-IMPORT-APPLY unit (fail-closed
    CANNOT-EVALUATE that mutates nothing; see its docstring for the candidate-context prerequisite).
    `check_opf_import.py` is the accompanying gate over a staged run and the scan layer.

Offline, stdlib only, fail-closed. This module takes an operator-enumerated set of legacy SOURCE files
and an untrusted MAPPING PLAN, validates both, mints record ids from the store's counters, and STAGES a
byte-canonical candidate under `.working/imports/<run-id>/` (store scope, a sibling of the machine subdir,
which carries TOML records only; spec 14.1). Its writes are confined to the `.working/imports/` staging ROOT
(created if absent; spec 14 stages under `.working/imports/<run-id>/`, so the staging root is part of the
staging area, not the active store) and the new run directory beneath it: the active store, its
`counters.toml`, its indexes, its archive, and the sources are read-only inputs, never written. It
composes U1 (store resolution + manifest), U2 (record envelope + counters + id helpers), U3 (the worklog
release-boundary gate), and U8 (the constrained-subset canonical emitter) rather than re-deriving them.

Sequencing (build plan): U7 landed the operation layer as MODULE + SELF-TEST first, like U4/U5; the live
`import` verb wiring landed next (OPF-IMPORT-VERB, opf.py `_cmd_import` + its main() dispatch branch),
composing this operation layer unchanged. The former dispatch-deferral vector (F-373 / VC-4-HARDEN) is
converted to its verb-wiring successor here: a bare `opf.py import` with no mode still exits 2 and stages
nothing (the grammar's exactly-one-mode rule), and `--apply <run-id>` still exits 2 mutating nothing (the
apply-promotion stub stays deferred to the OPF-IMPORT-APPLY unit), so PR-C's promotion landing is again a
conscious edit to this test rather than a silent drift.

Promotion (mutating the active store, advancing counters.toml, flipping import_status) is OUT OF SCOPE:
U7 stages proposals only. Staged ids are PROPOSALS; the sole durable reservation is counters.toml, which
promotion advances under the store lease (a single writer). The R6 sibling-run union is checked once
before the run-dir claim and RE-CHECKED after it (excluding this run's own dir), which NARROWS the
concurrent-proposal window but, absent a lock, does not eliminate it: a true TOCTOU interleaving between
two racing proposals can still leave both to fail closed or, narrowly, neither to block
(disclose-guard-residuals). Concurrent proposals are durably reconciled only at promotion, under the
counters.toml lease.

Write mechanism: U7 composes `_journal.apply_ops` DIRECTLY (no store lock, no `run_transaction`): staging
touches no shared mutable state, and the exclusive run-dir `mkdir` is the atomic pool claim. apply_ops
gives the contained no-follow parent walks, O_EXCL creates, staged-digest re-verification against the
emitted bytes, fsync, and umask-independent modes. The lock and the crash-durable journal belong to
promotion.

Reader contracts DEFINED here (the spec pins `imports/<run-id>/` but not the on-disk storage shapes; each
is disclosed for the finalizer, per disclose-guard-residuals):
  - a per-type index is `<machine>/<type>.index.toml` carrying `schema = 1` and a `[[record]]` array of
    full records; an absent file is zero records for that type, an unparseable one is CANNOT-EVALUATE;
  - an archive year is `<machine>/archive/<year>/archive.toml` carrying a NON-EMPTY `moved = [{id =
    <id>, destination = <contained relpath>}, ...]`; each destination is machine-relative and must open
    no-follow as a complete record or homogeneous record index carrying that exact id. Spec 12 also
    requires worklog-span entries but does not define their on-disk entry schema; U7 explicitly refuses
    every worklog-span archive entry until that schema releases. A bare-id, missing destination, missing
    or mismatched destination payload, empty `moved`, or worklog-span entry is CANNOT-EVALUATE;
  - a sibling staging run mirrors this run's layout; every `*.index.toml` and `worklog.toml` under its
    `candidate/` and `fragments/` enumerates staged ids (an incomplete sibling, no report.toml, still
    enumerates what it wrote; an unreadable/unparseable sibling artefact is CANNOT-EVALUATE);
  - the run-id grammar `imp-<UTCSTAMP>Z-<hash16>` is U7-defined (a spec follow-on is proposed).

The untrusted PLAN (spec 14.1, inert data staged verbatim as plan.toml):
  {
    "fragments": { <source-path>: [ {"span": [start, end], "state": <one of MAPPING_STATES>,
                                     "origin": <one of _ORIGIN_VALUES>,     # REQUIRED provenance (spec 14.1)
                                     "record": <candidate model, NO id>,   # mapped / split
                                     "target": <existing id>,              # duplicate
                                     "note": <str>}, ... ] },
    "worklog":  [ <worklog candidate model, NO id>, ... ],   # optional
    "version":  {...}                                        # optional; PRESENCE is a v1 deferral
  }
Per source the spans TILE [0, len) exactly (sorted, gap-free, overlap-free, half-open, ending at the
observed byte length; an empty source carries one explicit [0, 0] row), so "nothing is dropped" is an
arithmetic invariant over observed bytes. A plan candidate that carries an `id` is a finding: an untrusted
plan does not allocate from the pool (guard-input-soundness).

Disclosed v1 residuals: sources must be UTF-8 decodable (a non-UTF-8 declared source is CANNOT-EVALUATE,
never a silent skip); a candidate may link to an EXISTING record but candidate-to-candidate links are
unsupported; the worklog gate is the release-boundary check (check_no_append_into_released), with the
whole-store rotation/deletion partition reconciliation left to promotion (U7 rotates and deletes nothing);
a version-ledger (14.3) candidate is refused CANNOT-EVALUATE. Staged byte-form is re-emitted at promotion
into the store's canonical inline form under VC-4-HARDEN with model-equality proven there.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import copy
import datetime
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained (dir-fd, no-follow) readers/apply + JournalError + probe
import _opf_store      # noqa: E402  U1: resolution, manifest, containment helpers, contained TOML read
import _opf_schema     # noqa: E402  U2: record envelope + counters + id allocation/uniqueness helpers
import _opf_release    # noqa: E402  U3: the worklog release-boundary gate
import _opf_emit       # noqa: E402  U8: the constrained-subset canonical emitter (byte-canon-clean)
# _opf_observe is imported LAZILY inside _gather_review_context (its hardened git-subprocess idiom is used
# only for opportunistic review context): a module-top import would form a circular import through
# _opf_check, and the context gather runs long after every module has loaded.

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: tools/_opf_import.py requires Python 3.11+ (tomllib).")


# --- fixed names, vocabularies, and the outcome model ------------------------------------------------

CLEAN = 0            # staged: a full pass
FINDING = 1          # a validation finding (R6 collision, bad plan/candidate, plan-supplied id): stage nothing
CANNOT_EVALUATE = 2  # unreadable/malformed/exotic/out-of-subset: stage nothing (fail-closed)

IMPORTS_DIRNAME = "imports"
# The import-run staging tree is store-scope (`.working/imports/`), a sibling of the machine subdir, NOT
# under it: the machine store carries TOML records only (OPF-SPEC 14.1). This single constant is the one
# path authority both U7 (staging) and the checker consume, so the two cannot drift.
IMPORTS_REL = "{}/{}".format(_opf_store.WORKING_DIRNAME, IMPORTS_DIRNAME)   # ".working/imports"
ARCHIVE_DIRNAME = "archive"
DIR_MODE = 0o755
FILE_MODE = 0o644
SCHEMA = 1

# The closed mapping-state vocabulary (spec 14.1). mapped/split mint a candidate record; duplicate names
# an existing record; every other state quarantines as a legacy_fragment.
MAPPING_STATES = ("mapped", "split", "duplicate", "ambiguous", "incomplete", "unmapped",
                  "ignored", "cannot_evaluate")
_CANDIDATE_STATES = frozenset({"mapped", "split"})
_DUPLICATE_STATE = "duplicate"
_QUARANTINE_STATES = frozenset(MAPPING_STATES) - _CANDIDATE_STATES - {_DUPLICATE_STATE}

# The run-id grammar (U7-defined; a spec follow-on is proposed). No separator, dot segment, or control
# character, so the id is a safe single path component.
_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")
_RUN_DESCRIPTOR_FORMAT = "opf-import-run-v1"

# An archive-year directory name is a 4-digit year (spec 12 rotates by year; ARCHIVE_PERIODS = ("year",)).
# A name outside this grammar is a phantom partition, refused rather than silently enumerated (CLASS 3(c)).
_ARCHIVE_YEAR_RE = re.compile(r"^[0-9]{4}\Z")

LF_TYPE = "legacy_fragment"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")

# Closed plan keyset and closed fragment-row keyset.
_PLAN_KEYS = frozenset({"fragments", "worklog", "version"})
_FRAGMENT_ROW_KEYS = frozenset({"span", "state", "record", "target", "note", "origin"})

# The closed provenance vocabulary (spec 14.1): who a mapping came from. `baseline` is the deterministic
# whole-file classifier; `model_proposal` is an inert AI suggestion accepted into a resting state; and
# `human_revision` is an operator-supplied mapping on a re-plan. Every plan fragment row and every
# mappings.toml row carries a required `origin`, so the acceptance/apply layer can enforce that a
# model-proposed resting mapping was explicitly accepted (spec 14.1); a missing or out-of-vocabulary origin
# is refused as a finding, never defaulted (an omitted origin would silently bypass that acceptance gate).
_ORIGIN_VALUES = ("baseline", "model_proposal", "human_revision")
_ORIGIN_SET = frozenset(_ORIGIN_VALUES)
_BASELINE_ORIGIN = "baseline"
_MODEL_PROPOSAL_ORIGIN = "model_proposal"

# The resting states a promoted mapping comes to rest in (spec 14.1): a `mapped`/`split` candidate, a
# `duplicate` naming an existing record, or an `ignored` fragment. A model_proposal-origin mapping resting
# in any of these requires an explicit accepting decision (no blanket accept); the remaining quarantine
# states are not "at rest" in that sense.
_RESTING_STATES = frozenset(_CANDIDATE_STATES | {_DUPLICATE_STATE, "ignored"})

# --- operation-layer (scan / plan) fixed names and vocabularies (OPF-IMPORT-OPS) --------------------
# The deterministic SCAN inventory format and the whole-file baseline EXTRACTOR (spec 14.1). The
# baseline extractor yields exactly ONE fragment per source, spanning [0, len) (an empty source is one
# [0, 0] fragment), so raw bytes are accounted for with no format parser invented; a finer, versioned
# extractor is a disclosed follow-on. These identifiers are folded into every digest so a future
# extractor produces a distinguishable inventory (the fragment id is content-inclusive; Fable adjudication).
INVENTORY_FORMAT = "opf-import-inventory-v1"
EXTRACTOR_ID = "whole-file"
EXTRACTOR_VERSION = 1
_FRAGMENT_DESCRIPTOR_FORMAT = "opf-import-fragment-v1"

# The plan_import review surface (spec 4.2) and the frozen scan inventory, staged as review artefacts
# beside the promotion candidate. Machine artefacts stay TOML (the store's native canonical form, the
# _opf_emit byte-canonical emitter), matching mappings.toml/plan.toml/report.toml rather than importing a
# second (JSON) canonicalizer (Fable residual #1: canonical JSON was adopted in the plan over TOML; this
# build reconciles to the house TOML convention and flags the choice for the finalizer).
INVENTORY_NAME = "inventory.toml"
REPORT_MD_NAME = "IMPORT-REPORT.md"

# The machine-readable model-proposal register staged beside the inventory (spec 14.1). Canonical _opf_emit
# TOML (the store's native form), so IMPORT-REPORT.md is byte-reproducible from inventory.toml +
# proposals.toml + run id and the review path reads machine data only, never the human-readable report.
PROPOSALS_NAME = "proposals.toml"

# The attributed acceptance record (spec 14.1), captured by `--review` and bound to the exact run. It is the
# one CANONICAL-JSON artefact in an otherwise-TOML run dir (the approved acceptance design names JSON; the
# canonical-JSON idiom is already in-repo, so no second canonicalizer enters). Producer discipline mirrors
# the TOML _emit_bytes: sorted keys, compact separators, ensure_ascii, exactly one trailing newline, a size
# ceiling, and a round-trip check. Digests are recorded as "sha256:"+lowercase-hex, matching the store.
ACCEPTANCE_NAME = "acceptance.json"
ACCEPTANCE_FORMAT = "opf.import.acceptance/v1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
# The reviewed_at contract is the producer's fixed-width RFC-3339 UTC form YYYY-MM-DDThh:mm:ssZ. strptime
# alone accepts non-zero-padded fields ("2026-9-9T1:2:3Z"), so a strict fixed-width regex (anchored
# \A...\Z, no trailing-newline slack) gates the shape and a value-validity strptime parse gates the values.
_RFC3339_UTC_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_DECISION_VERBS = frozenset({"accept", "reject"})

# The closed keyset of an inert model proposal (spec 14.1 untrusted plan data). A proposal is a SUGGESTED
# mapping recorded verbatim in the review surface; it is NEVER fed to the staging classifier as a resting
# candidate state, so it can neither mint a record nor select a write destination without a later,
# attributed human acceptance (the acceptance-capture + apply-promotion unit; a disclosed spec gap).
_PROPOSAL_KEYS = frozenset({"source_path", "span", "suggested_state", "note"})


_MODULE_SCHEMA_REFUSAL = (
    "a module-tier record cannot be fully validated until the module schemas release, spec 8.5; "
    "U7 stages only against base-tier stores"
)


class _LegacyFragmentSpec(_opf_schema.TypeSpec):
    """The importer-only quarantine type's schema (spec 8.1/8.5). U2's TypeSpec.__init__ binds its
    namespace via BASELINE_TYPES[name], which carries no `legacy_fragment` entry yet, so U7 constructs the
    LF spec directly against IMPORTER_TYPES and routes LF records through the shared validate_record
    machinery (the designed `specs` extension point) rather than a re-derived parallel validator. The
    long-term home is a U2 IMPORTER_SPECS seam plus an LF branch in _validate_type_specific (proposed
    follow-on); until it lands, U7 validates the four provenance fields itself (_validate_lf_provenance).
    State grammar (spec 8.5): quarantined > resolved | ignored. The four provenance fields are spec-pinned
    (spec 14.1); `body` is the optional extracted UTF-8 fragment text."""
    def __init__(self):
        self.name = LF_TYPE
        self.namespace = _opf_schema.IMPORTER_TYPES[LF_TYPE]   # "LF", the section-8.1 binding
        self.initial = "quarantined"
        self.working = frozenset()
        self.terminal = frozenset({"resolved", "ignored"})
        self.transitions = {"quarantined": frozenset({"resolved", "ignored"})}
        self.proposable = frozenset({"resolved", "ignored"})
        self.gated = frozenset()   # legacy_fragment has no gated states: its proposable states are terminal
        self.extra_keys = frozenset({"source_path", "source_digest", "span", "run_id", "body"})
        self.reduced = False
        self.states = frozenset({"quarantined", "resolved", "ignored"})


def _roster():
    """The type roster U7 validates candidates and quarantine records against: the nine baseline types
    plus the importer-only legacy_fragment. A single LF instance is built per call (cheap, immutable)."""
    roster = dict(_opf_schema.BASELINE_SPECS)
    roster[LF_TYPE] = _LegacyFragmentSpec()
    return roster


class StageResult:
    """The inert result of a staging attempt, judged by its verdict (never by grepping output)."""
    __slots__ = ("verdict", "findings", "run_id", "run_rel", "mapping_states", "staged_ids",
                 "new_high_water", "promotion_ready", "migration_incomplete")

    def __init__(self, verdict, findings=None, run_id=None, run_rel=None, mapping_states=None,
                 staged_ids=None, new_high_water=None, promotion_ready=False,
                 migration_incomplete=False):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.run_id = run_id
        self.run_rel = run_rel                    # store-relative path to the staged run dir (None unless staged)
        self.mapping_states = mapping_states or {}
        self.staged_ids = staged_ids or []
        self.new_high_water = new_high_water or {}
        self.promotion_ready = promotion_ready
        self.migration_incomplete = migration_incomplete


class ScanResult:
    """The inert result of a read-only SCAN (spec 14.1 enumeration). Judged by its verdict, never by
    grepping output. On a clean scan `inventory` is the frozen, canonical inventory payload (carrying its
    own `inventory_digest`), `sources` and `fragments` its record lists, and `inventory_digest` the
    reproducibility anchor. A scan writes NOTHING and allocates no run id (SECI-preview-has-no-side-effects):
    it is a preview, and the plan step persists the authoritative inventory."""
    __slots__ = ("verdict", "findings", "inventory", "inventory_digest", "sources", "fragments")

    def __init__(self, verdict, findings=None, inventory=None, inventory_digest=None,
                 sources=None, fragments=None):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.inventory = inventory or {}
        self.inventory_digest = inventory_digest
        self.sources = sources or []
        self.fragments = fragments or []


class PlanResult:
    """The inert result of a PLAN (scan + deterministic classification + staging). Judged by its verdict.
    On a clean plan the run is staged under `.working/imports/<run-id>/` with `import_status` remaining the
    store's `partial`-equivalent staging posture (the run dir carries a promotion candidate; the active
    store is untouched, spec 14.1); `run_rel` is the store-relative run dir, `inventory_digest` the frozen
    scan anchor, and `report_rel` the store-relative IMPORT-REPORT.md review surface (spec 4.2)."""
    __slots__ = ("verdict", "findings", "run_id", "run_rel", "inventory_digest", "report_rel",
                 "migration_incomplete")

    def __init__(self, verdict, findings=None, run_id=None, run_rel=None, inventory_digest=None,
                 report_rel=None, migration_incomplete=False):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.run_id = run_id
        self.run_rel = run_rel
        self.inventory_digest = inventory_digest
        self.report_rel = report_rel
        self.migration_incomplete = migration_incomplete


class ApplyResult:
    """The inert result of an APPLY-PROMOTION attempt. Judged by its verdict. `promoted` is never inferred
    from the verdict alone (a caller reads this field). See apply_import: promotion is DEFERRED to the
    OPF-IMPORT-APPLY unit, so this result is always a fail-closed CANNOT-EVALUATE that mutates nothing."""
    __slots__ = ("verdict", "findings", "promoted", "outcome", "restore_ref")

    def __init__(self, verdict, findings=None, promoted=False, outcome=None, restore_ref=None):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.promoted = promoted
        self.outcome = outcome                    # promoted / aborted / rejected / noop_already_complete
        self.restore_ref = restore_ref


class ReviewResult:
    """The inert result of a `--review` acceptance-capture attempt. Judged by its verdict, never by grepping
    output. On a clean review the attributed `acceptance.json` is staged into the run dir (`acceptance_rel`
    is its store-relative path, `reviewed_at` the captured RFC-3339 instant, `decisions_count` the number of
    per-fragment decisions recorded); a validation finding (incomplete coverage, an unknown/duplicate
    fragment, an echo mismatch, or a model_proposal resting mapping without an explicit accept) is verdict 1
    and writes nothing; an unresolved store, a missing/malformed/not-promotion-ready run, or a write failure
    is verdict 2 (fail-closed). No live-store write ever occurs (review captures a decision record only)."""
    __slots__ = ("verdict", "findings", "run_id", "acceptance_rel", "reviewed_at", "decisions_count")

    def __init__(self, verdict, findings=None, run_id=None, acceptance_rel=None, reviewed_at=None,
                 decisions_count=0):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.run_id = run_id
        self.acceptance_rel = acceptance_rel      # store-relative path to the staged acceptance.json
        self.reviewed_at = reviewed_at
        self.decisions_count = decisions_count


class _StageError(Exception):
    """An input the staging step cannot use, carrying the verdict so a plan/candidate finding (verdict 1)
    and an unreadable/malformed/exotic input (verdict 2) are distinguished at the raise site rather than
    collapsed. Callers convert it into a StageResult."""
    def __init__(self, verdict, message):
        super().__init__(message)
        self.verdict = verdict
        self.message = message


def _finding(msg):
    return _StageError(FINDING, msg)


def _cannot(msg):
    return _StageError(CANNOT_EVALUATE, msg)


# --- argument and helper guards ----------------------------------------------------------------------

def _require_utc(now):
    if not isinstance(now, datetime.datetime):
        raise _cannot("now must be a timezone-aware UTC datetime, got {}".format(type(now).__name__))
    if now.tzinfo is None or now.utcoffset() != datetime.timedelta(0):
        raise _cannot("now must be a timezone-aware UTC instant (a zero offset); a naive or non-UTC "
                      "datetime is refused (timestamp-from-clock)")


def _require_nonce(run_nonce):
    if not isinstance(run_nonce, str) or not run_nonce:
        raise _cannot("run_nonce must be a non-empty string")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in run_nonce):
        raise _cannot("run_nonce carries a control character")


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _emit_bytes(model, where):
    """Emit a model to canonical, byte-canonical, round-tripped TOML bytes (U8). An EmitError (a value
    outside the constrained subset, or a failed round trip) is CANNOT-EVALUATE, fail-closed."""
    try:
        return _opf_emit.emit_checked(model).encode("utf-8")
    except _opf_emit.EmitError as exc:
        raise _cannot("{}: not byte-canonically emittable ({})".format(where, exc))


def _rfc3339(now):
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- store-relative contained reads (all fail-closed to CANNOT-EVALUATE) -----------------------------

def _read_toml(store_root_fd, rel):
    """A contained TOML read (U1): the parsed dict, or None when absent. A StoreError is CANNOT-EVALUATE.
    A JournalError surfacing from the underlying contained lstat/read is likewise CANNOT-EVALUATE,
    fail-closed (CLASS 1): _opf_store._read_toml_contained runs its lstat step OUTSIDE its own StoreError
    wrapper, so a non-ENOENT OSError the root now converts to a JournalError (EACCES, ENAMETOOLONG, ELOOP)
    reaches here rather than escaping stage_import as a raw error."""
    try:
        return _opf_store._read_toml_contained(store_root_fd, rel)
    except _opf_store.StoreError as exc:
        raise _cannot(str(exc))
    except _journal.JournalError as exc:
        raise _cannot("cannot read {} ({})".format(rel, exc))
    except ValueError as exc:
        # ValueError family, at the PARSE locus: tomllib raises a bare ValueError on a store-TOML integer
        # literal over CPython's 4300-digit string-conversion ceiling (counters, index, archive, worklog).
        # Convert it to the module's fail-closed CANNOT-EVALUATE HERE, at the specific parse call, so an
        # unrelated internal invariant ValueError elsewhere in stage_import is NOT laundered into a
        # malformed-input verdict (no-concealed-failure).
        raise _cannot("cannot parse {} ({})".format(rel, exc))


def _close_fd(fd, rel):
    """CLASS 1: close a directory/file handle opened for a store read, converting a close-time OSError
    (EIO, EBADF) to the module's fail-closed CANNOT-EVALUATE. Safe in a `finally`: on the normal path it is
    a clean no-op; when os.close itself errors it raises _cannot, so a read whose handle could not be closed
    cleanly fails closed rather than returning as though it had completed. Both a _cannot already in flight
    and a close-time _cannot carry the same CANNOT-EVALUATE verdict, so no fs error is silently swallowed and
    the verdict never degrades to a clean pass."""
    try:
        os.close(fd)
    except OSError as exc:
        raise _cannot("cannot close a store-read handle for {} ({})".format(rel, exc))


def _validate_tier_record(rec, expected_type, roster, registered_vendors, where):
    """Validate a record only when U7 has its complete schema.

    Baseline and importer-tier records route through _opf_schema.validate_record. Module-tier records
    are refused rather than partially validated because their complete state and type-specific schemas
    do not ship until the module-schemas release (spec 8.5). Unknown types are likewise refused.
    Returns a findings list; callers convert a non-empty list to CANNOT-EVALUATE."""
    if not isinstance(expected_type, str) or not expected_type:
        return ["{}: record type is missing or not a non-empty string ({!r}); cannot validate it "
                "(fail-closed)".format(where, expected_type)]
    spec = roster.get(expected_type)
    if spec is not None:
        rv = _opf_schema.validate_record(rec, expected_type=expected_type, specs=roster,
                                         registered_vendors=registered_vendors)
        if rv.status != _opf_store.VALID:
            return list(rv.findings) or ["record is not VALID for type {!r}".format(expected_type)]
        return []
    if expected_type in _opf_store.MODULE_TYPES:
        return [_MODULE_SCHEMA_REFUSAL]
    return ["unsupported type {!r}: no schema to validate it against (fail-closed)".format(expected_type)]


def _record_ids(data, where, roster=None, expected_type=None, registered_vendors=frozenset()):
    """Extract ids from a closed `{schema = 1, record = [...]}` index, fail-closed.

    An absent file is zero records. A present file must contain only `schema` and `record`, carry the
    supported schema, and provide an array of record tables. With a roster and expected type, baseline
    and importer records receive their complete schema validation. A known module-tier index may be
    absent or genuinely empty, but ANY non-empty module-tier index is CANNOT-EVALUATE because U7 cannot
    validate those records until the module schemas release (spec 8.5). An unknown index type is refused
    even when empty."""
    if data is None:
        return []

    have_authority = roster is not None and expected_type is not None
    raw_records = data.get("record")
    # Guard-input-soundness: in an authority context (roster present) a non-empty record array whose
    # expected type is missing, None, or not a non-empty string cannot be validated or module-screened;
    # fail closed BEFORE any roster.get / `in MODULE_TYPES` membership test (a non-string/unhashable type
    # would otherwise disable authority or raise an uncaught TypeError).
    if (roster is not None and isinstance(raw_records, list) and raw_records
            and (not isinstance(expected_type, str) or not expected_type)):
        raise _cannot("{}: a present index's record type is missing or not a non-empty string ({!r}); "
                      "U7 cannot validate or module-screen untyped records (fail-closed)".format(
                          where, expected_type))
    if (have_authority and expected_type in _opf_store.MODULE_TYPES
            and isinstance(raw_records, list) and raw_records):
        raise _cannot("{}: {}".format(where, _MODULE_SCHEMA_REFUSAL))

    extra = set(data) - {"schema", "record"}
    if extra:
        raise _cannot("{}: a present index carries unknown top-level key(s): {} (a `{{schema, record}}` "
                      "index is closed)".format(where, ", ".join(_opf_store._sorted_key_names(extra))))
    schema = data.get("schema")
    if type(schema) is not int or schema != SCHEMA:
        raise _cannot("{}: a present index must carry `schema = {}` (got {!r}); a malformed or absent "
                      "schema is not zero records".format(where, SCHEMA, schema))
    recs = data.get("record")
    if not isinstance(recs, list):
        raise _cannot("{}: a present index must carry a `record` array of tables (missing or non-list "
                      "`record` is malformed, not zero records)".format(where))
    if have_authority and expected_type not in roster and expected_type not in _opf_store.MODULE_TYPES:
        raise _cannot("{}: index type {!r} is not a supported record type; an empty or unsupported index is "
                      "not zero records (fail-closed)".format(where, expected_type))

    ids = []
    for i, rec in enumerate(recs):
        if not isinstance(rec, dict):
            raise _cannot("{}: record[{}] is not a table".format(where, i))
        rid = rec.get("id")
        if _opf_schema._valid_id_shape(rid) is None:
            raise _cannot("{}: record[{}] carries a malformed id {!r}".format(where, i, rid))
        if have_authority:
            tier_findings = _validate_tier_record(
                rec, expected_type, roster, registered_vendors,
                "{} record[{}]".format(where, i))
            if tier_findings:
                raise _cannot("{}: record[{}] does not satisfy its complete {} contract ({})".format(
                    where, i, expected_type, "; ".join(tier_findings)))
        ids.append(rid)
    return ids


def _index_ids(store_root_fd, machine_rel, type_name, roster, registered_vendors=frozenset()):
    """Read an active inline index through _record_ids.

    Complete schemas validate baseline/importer records. A non-empty module-tier index is refused,
    while an absent or genuinely empty module-tier index contributes no ids."""
    rel = "{}/{}.index.toml".format(machine_rel, type_name)
    return _record_ids(_read_toml(store_root_fd, rel), rel, roster, type_name, registered_vendors)


def _refuse_nonempty_module_indexes(store_root_fd, machine_rel, roster,
                                    registered_vendors=frozenset()):
    """Preflight every known active module-index pathname before ordinary active-store readers run.

    The scan covers _opf_store.MODULE_TYPES directly rather than trusting the enabled-type work-list.
    Absent and well-formed empty indexes are allowed; malformed indexes and every non-empty module index
    are CANNOT-EVALUATE. _record_ids repeats the same refusal on later reads, closing a preflight/read race."""
    for type_name in sorted(_opf_store.MODULE_TYPES):
        rel = "{}/{}.index.toml".format(machine_rel, type_name)
        data = _read_toml(store_root_fd, rel)
        if data is not None:
            _record_ids(data, rel, roster, type_name, registered_vendors)


def _archived_destination_ids(data, where, roster, registered_vendors=frozenset()):
    """Return ids from one opened archived destination payload, validating its complete supported shape.

    A destination is either one full record table or a homogeneous `{schema, record}` index. Baseline and
    importer records are validated through their complete schemas. Module records are refused because
    their complete schemas have not released. Worklog spans never reach this reader: _archive_ids refuses
    them explicitly before opening a destination."""
    if not isinstance(data, dict):
        raise _cannot("{}: archived destination is not a TOML table".format(where))
    if "id" in data and "record" in data:
        raise _cannot("{}: archived destination ambiguously carries both a record id and an index".format(
            where))

    if "id" in data:
        expected_type = data.get("type")
        findings = _validate_tier_record(
            data, expected_type, roster, registered_vendors, where)
        if findings:
            raise _cannot("{}: archived record does not satisfy its complete contract ({})".format(
                where, "; ".join(findings)))
        return [data["id"]]

    if "record" in data:
        recs = data.get("record")
        if not isinstance(recs, list):
            return _record_ids(data, where)
        if not recs:
            return _record_ids(data, where)
        first = recs[0]
        expected_type = first.get("type") if isinstance(first, dict) else None
        if not isinstance(expected_type, str) or not expected_type:
            raise _cannot("{}: an archived index's first record omits a valid string `type`; U7 cannot "
                          "validate or module-screen an untyped archived record (fail-closed)".format(where))
        ids = _record_ids(data, where, roster, expected_type, registered_vendors)
        duplicate_findings = _opf_schema.check_unique_ids(ids)
        if duplicate_findings:
            raise _cannot("{}: archived destination index has duplicate ids ({})".format(
                where, "; ".join(duplicate_findings)))
        return ids

    raise _cannot("{}: archived destination is neither a full record nor a `{{schema, record}}` "
                  "index".format(where))


def _archive_ids(store_root_fd, machine_rel, roster, registered_vendors=frozenset()):
    """Enumerate ids across `<machine>/archive/<year>/archive.toml`, fail-closed.

    Archive years are real no-follow directories named by four digits. Each archive.toml is a closed
    `{schema, moved}` table whose `moved` value is a non-empty array. A record entry is exactly
    `{id, destination}`; its destination is machine-relative, is prefixed with `machine_rel`, is opened
    no-follow through _read_toml, and must be a complete supported record or homogeneous index carrying
    that id exactly once.

    Spec 12 additionally requires every moved worklog span, but does not define its archive-entry or
    destination schema. U7 therefore explicitly refuses entries carrying `span` or `worklog_span`, and
    refuses an individual WL id because worklog rotation is span-based. It never seats a partially
    understood worklog archive entry."""
    archive_rel = "{}/{}".format(machine_rel, ARCHIVE_DIRNAME)
    years = _dir_entries_no_symlink(store_root_fd, archive_rel)
    if years is None:
        return []

    ids = []
    for year in years:
        if not _ARCHIVE_YEAR_RE.match(year):
            raise _cannot("{}/{}: an archive-year directory name must be a 4-digit year (spec 12); a "
                          "malformed year name is a phantom partition, fail-closed".format(
                              archive_rel, year))
        rel = "{}/{}/archive.toml".format(archive_rel, year)
        data = _read_toml(store_root_fd, rel)
        if data is None:
            raise _cannot("{}: an archive year must carry a parseable archive.toml (spec 12)".format(rel))

        unknown = set(data) - {"moved", "schema"}
        if unknown:
            raise _cannot("{}: an archive.toml carries unknown top-level key(s): {} (a `{{schema, moved}}` "
                          "archive is closed; spec 12)".format(
                              rel, ", ".join(_opf_store._sorted_key_names(unknown))))
        asc = data.get("schema")
        if asc is not None and (type(asc) is not int or asc != SCHEMA):
            raise _cannot("{}: archive schema {!r} is not the supported schema {}".format(
                rel, asc, SCHEMA))
        moved = data.get("moved")
        if not isinstance(moved, list) or not moved:
            raise _cannot("{}: `moved` must be a NON-EMPTY array enumerating every moved record or "
                          "worklog span and its destination (spec 12); an empty or non-list `moved` is "
                          "an archive year with no archived content".format(rel))

        for j, entry in enumerate(moved):
            if not isinstance(entry, dict):
                raise _cannot("{}: moved[{}] must be a table enumerating moved content and its "
                              "destination (spec 12)".format(rel, j))
            if "span" in entry or "worklog_span" in entry:
                raise _cannot("{}: moved[{}] is a worklog-span archive entry; U7 cannot fully validate "
                              "worklog-span archives until their on-disk entry and destination schemas "
                              "release, so it refuses the archive (spec 12)".format(rel, j))

            entry_extra = set(entry) - {"id", "destination"}
            if entry_extra:
                raise _cannot("{}: moved[{}] carries unknown key(s): {} (a record entry is a closed "
                              "{{id, destination}}; spec 12)".format(
                                  rel, j, ", ".join(_opf_store._sorted_key_names(entry_extra))))
            mid = entry.get("id")
            shape = _opf_schema._valid_id_shape(mid)
            if shape is None:
                raise _cannot("{}: moved[{}] id {!r} is malformed".format(rel, j, mid))
            if shape[0] == "WL":
                raise _cannot("{}: moved[{}] names individual worklog id {!r}, but spec 12 archives "
                              "worklog spans; U7 cannot fully validate the undisclosed span shape and "
                              "therefore refuses it".format(rel, j, mid))
            if shape[0] not in _opf_schema.RECORD_NAMESPACES:
                raise _cannot("{}: moved[{}] id {!r} uses namespace {!r} bound to no record type (a phantom "
                              "archive entry; spec 8.1/8.2)".format(rel, j, mid, shape[0]))

            dest = entry.get("destination")
            if not (isinstance(dest, str) and dest and _opf_store._is_contained_relpath(dest)):
                raise _cannot("{}: moved[{}] ({}) must carry a `destination` naming its archived record's "
                              "contained location (spec 12); a moved id with no destination is a malformed "
                              "archive entry, fail-closed".format(rel, j, mid))

            destination_rel = "{}/{}".format(machine_rel, dest)
            destination_data = _read_toml(store_root_fd, destination_rel)
            if destination_data is None:
                raise _cannot("{}: moved[{}] ({}) names missing archived destination {}; an id cannot be "
                              "seated without its archived record (spec 12)".format(
                                  rel, j, mid, destination_rel))
            destination_ids = _archived_destination_ids(
                destination_data, destination_rel, roster, registered_vendors)
            if destination_ids.count(mid) != 1:
                raise _cannot("{}: moved[{}] names id {!r}, but archived destination {} carries that id "
                              "{} times; the destination must carry the exact id once (spec 12)".format(
                                  rel, j, mid, destination_rel, destination_ids.count(mid)))
            ids.append(mid)
    return ids


def _list_contained(store_root_fd, rel):
    """The immediate entry names of a directory beneath the store root, listed no-follow, or None when the
    directory is absent. CANNOT-EVALUATE on any read error or a refused symlink (fail-closed listing). CLASS
    1: os.listdir and the handle closes are converted to CANNOT-EVALUATE at their own sites, so an EACCES/EIO
    listing never reads as an empty directory and a close error never returns as though the read completed."""
    try:
        pfd, name = _journal._open_parent(store_root_fd, rel)
    except FileNotFoundError:
        return None
    except (OSError, _journal.JournalError) as exc:
        raise _cannot("cannot open parent of {} ({})".format(rel, exc))
    try:
        try:
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _cannot("cannot list {} no-follow ({})".format(rel, exc))
        try:
            try:
                names = os.listdir(dfd)   # CLASS 1: an EACCES/EIO here is fail-closed, never empty
            except OSError as exc:
                raise _cannot("cannot list {} ({})".format(rel, exc))
        finally:
            _close_fd(dfd, rel)
        return sorted(names)
    finally:
        _close_fd(pfd, rel)


def _dir_entries_no_symlink(store_root_fd, rel):
    """The immediate entry names of a directory beneath the store root, listed no-follow, or None when the
    directory is absent. A SYMLINK, or any non-directory entry, is CANNOT-EVALUATE (fail-closed): an entry
    that cannot be classified as a real subdirectory must refuse rather than vanish from the uniqueness union,
    where U1's _immediate_subdirs would silently drop it (correct for store DISCOVERY, wrong for the union;
    F4/B3). Used for BOTH the `imports/` sibling-run sweep and the `archive/` year enumeration. CLASS 1:
    os.listdir, os.stat, and the handle closes are each converted to CANNOT-EVALUATE at their own sites, so an
    EACCES/EIO on the enumeration never reads as an empty directory."""
    try:
        pfd, name = _journal._open_parent(store_root_fd, rel)
    except FileNotFoundError:
        return None
    except (OSError, _journal.JournalError) as exc:
        raise _cannot("cannot open parent of {} ({})".format(rel, exc))
    try:
        try:
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _cannot("cannot list {} no-follow ({})".format(rel, exc))
        try:
            try:
                entries = os.listdir(dfd)   # CLASS 1: an EACCES/EIO here is fail-closed, never empty
            except OSError as exc:
                raise _cannot("cannot list {} ({})".format(rel, exc))
            out = []
            for entry in sorted(entries):
                try:
                    est = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
                except OSError as exc:
                    raise _cannot("cannot stat {}/{} ({})".format(rel, entry, exc))
                if stat.S_ISLNK(est.st_mode):
                    raise _cannot("{}/{} is a symlink; a symlinked entry cannot be enumerated for the "
                                  "uniqueness union (fail-closed, never silently omitted)".format(rel, entry))
                if not stat.S_ISDIR(est.st_mode):
                    raise _cannot("{}/{} is not a directory (a real subdirectory entry was expected; "
                                  "fail-closed)".format(rel, entry))
                out.append(entry)
            return out
        finally:
            _close_fd(dfd, rel)
    finally:
        _close_fd(pfd, rel)


def _sibling_ids(store_root_fd, machine_rel, roster, registered_vendors=frozenset(), skip_run_id=None):
    """Enumerate staged ids across sibling runs under `.working/imports/`.

    Every candidate/fragments index is read through _record_ids. Baseline and importer records receive
    complete validation; ANY non-empty module-tier sibling index is CANNOT-EVALUATE until the module
    schemas release. Unknown index types and malformed sibling artefacts are refused. An incomplete
    sibling still enumerates artefacts already written. Symlinked/non-directory entries are refused.
    `skip_run_id` excludes this run during the post-claim re-check. `machine_rel` is retained to feed the
    fail-closed legacy-location guard below (the ONLY consumer of it here)."""
    # Fail-closed legacy-location guard (OPF-IMPORTS-RELOCATE): import runs now stage at `.working/imports/`,
    # so the OLD machine-subdir path `.working/toml/imports/` must never carry a run. ANY object there
    # (directory empty or not, regular file, or symlink) is CANNOT-EVALUATE, never silently ignored: the
    # enumerator below reads only the new root, so a legacy run's staged ids would otherwise vanish from the
    # R6 uniqueness union (a fail-OPEN id-collision hazard) or be triaged away under a new-path partial. No
    # automatic dual-location fallback and no automatic migration (settled greenfield): the content is
    # surfaced for manual review and relocation. An existence predicate is total over the input space (no
    # run-id-grammar edge, symlink/file/dir uniform), so it cannot false-fire (the machine-name reservation
    # removes the only legitimate claimant of the name, and `imports` is not a declarable type).
    legacy_rel = "{}/{}".format(machine_rel, IMPORTS_DIRNAME)
    try:
        legacy_st = _journal._lstat_contained(store_root_fd, legacy_rel)
    except _journal.JournalError as exc:
        raise _cannot("cannot probe the legacy import-run location {} ({}); fail-closed".format(
            legacy_rel, exc))
    if legacy_st is not None:
        raise _cannot(
            "an import run exists at the legacy location {!r}; since the relocation import runs stage under "
            "{!r} and the machine store carries TOML records only (spec 14.1). The legacy content needs "
            "manual review and relocation (no automatic dual-location fallback, no automatic "
            "migration).".format(legacy_rel, IMPORTS_REL))
    imports_rel = IMPORTS_REL
    runs = _dir_entries_no_symlink(store_root_fd, imports_rel)
    if runs is None:
        return []

    ids = []
    for run in runs:
        if skip_run_id is not None and run == skip_run_id:
            continue
        for sub in ("candidate", "fragments"):
            sub_rel = "{}/{}/{}".format(imports_rel, run, sub)
            names = _list_contained(store_root_fd, sub_rel)
            if names is None:
                continue
            for entry in names:
                if entry.endswith(".index.toml"):
                    expected_type = entry[:-len(".index.toml")]
                elif entry == "worklog.toml":
                    expected_type = "worklog"
                else:
                    continue
                rel = "{}/{}".format(sub_rel, entry)
                ids.extend(_record_ids(
                    _read_toml(store_root_fd, rel), rel, roster, expected_type,
                    registered_vendors))
    return ids


def _worklog_ids(store_root_fd, machine_rel, roster=None, registered_vendors=frozenset()):
    """The WL ids of the ACTIVE worklog.toml (`[[entry]]` rows, spec 6.2), fail-closed. Absent -> zero. CLASS
    3(i/ii): the present worklog is held to its COMPLETE contract via _opf_release.validate_worklog, the
    authoritative U3 worklog validator, so it fails closed on exactly what the release module fails closed on:
    an unknown top-level key (the closed {schema, entry} keyset, which the former hand-rolled reader did NOT
    enforce), a malformed schema, a non-list `entry`, a malformed entry, or a duplicate WL id. A non-VALID
    worklog is CANNOT-EVALUATE, never accepted into the uniqueness union or resolved as a valid duplicate/link
    target. The active worklog is NOT an `{schema, record}` index (its rows are `[[entry]]`), so _record_ids
    does not read it; this reader honours the U3 worklog.toml shape and returns the canonical WL-<n> id
    strings validate_worklog proved well-formed. The `roster` parameter is retained for call-site symmetry
    with the other tier readers; worklog is a baseline type, so validate_worklog validates it against U2's
    baseline worklog spec directly and does not need the extended roster. Disclosed residual: manifest-
    registered custom worklog kinds are outside this build's manifest surface (validate_worklog's built-in
    kind vocabulary is the accepted set here), so a promoted entry using a custom registered kind fails closed
    to CANNOT-EVALUATE rather than being wrongly accepted."""
    rel = "{}/worklog.toml".format(machine_rel)
    data = _read_toml(store_root_fd, rel)
    if data is None:
        return []
    wv = _opf_release.validate_worklog(data, registered_vendors=registered_vendors)
    if wv.status != _opf_release.VALID:
        raise _cannot("{}: worklog does not satisfy its complete contract ({})".format(
            rel, "; ".join(wv.findings)))
    return ["WL-{}".format(n) for n in wv.entry_ids]


def _require_inline_layout(store_root_fd, machine_rel):
    """B2: U7's active-store readers assume the INLINE storage layout, where `<type>.index.toml` holds the
    full `[[record]]` array (spec 9). Under the `per-record` layout `<type>.index.toml` is only a registry
    of {id, state, path, digest} rows and the records live one-per-file under `<type>/`, which this build's
    inline readers do not enumerate: reading them blind would MISS every per-record id from the R6 union (a
    minted id could silently collide) and mis-resolve a duplicate/link target against an id whose record
    file this reader never confirmed (a phantom target). The store's declared layout is therefore read from
    the AUTHORITATIVE manifest [opf].layout (guard-input-soundness); any layout other than `inline`,
    or an absent/malformed opf table, is CANNOT-EVALUATE, fail-closed, never a partial inline read of
    a non-inline store. (per-record support is a disclosed follow-on.)"""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    data = _read_toml(store_root_fd, manifest_rel)
    if data is None:
        raise _cannot("{}: the store manifest is absent; the storage layout cannot be determined "
                      "(spec 9)".format(manifest_rel))
    opf = data.get("opf")
    layout = opf.get("layout") if isinstance(opf, dict) else None
    if layout != "inline":
        raise _cannot("{}: storage layout {!r} is unsupported; U7's inline active-store readers stage only "
                      "an `inline`-layout store (spec 9), so a non-inline layout is fail-closed (never a "
                      "partial inline read that would miss per-record ids or admit a phantom target)".format(
                          manifest_rel, layout))


def _active_types(store_root_fd, machine_rel):
    """The names of every ENABLED active record type whose `<type>.index.toml` the whole-store id union and
    the duplicate/candidate-link existence authority must scan (B2): the baseline types PLUS each
    module-tier type whose module is enabled in the store's [modules] config, PLUS the importer-only
    legacy_fragment (a promoted quarantine record lives in the active store, so its id is SEEN, not read as
    absent). `worklog` is EXCLUDED here (its ids live in worklog.toml, read via _worklog_ids). The enabled
    module set is derived from the AUTHORITATIVE manifest [modules] table through U1's own
    _validate_modules (guard-input-soundness); an unreadable or malformed [modules] config is
    CANNOT-EVALUATE, never a PARTIAL union that silently omits an enabled module-tier type."""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    data = _read_toml(store_root_fd, manifest_rel)
    if data is None:
        raise _cannot("{}: the store manifest is absent; the enabled active-type set cannot be determined "
                      "(spec 9)".format(manifest_rel))
    findings = []
    enabled = _opf_store._validate_modules(data.get("modules"), findings)
    if findings:
        raise _cannot("{}: [modules] is malformed ({}); the enabled active-type set cannot be determined "
                      "(fail-closed, never a partial union)".format(manifest_rel, "; ".join(findings)))
    names = set(_opf_store.BASELINE_TYPES)
    names.discard("worklog")
    names |= {t for t, (_ns, module) in _opf_store.MODULE_TYPES.items() if module in enabled}
    names |= set(_opf_store.IMPORTER_TYPES)   # legacy_fragment: importer-only, never module-gated (spec 8.1)
    return names


def _registered_vendors(store_root_fd, machine_rel):
    """The manifest's registered `x-<vendor>` namespace set (U1 [vendors].registered): the allow-set a
    promoted active record's extension tables are validated against when the union/existence authority
    reads it under the full record contract (CLASS 3). Derived from the AUTHORITATIVE manifest through U1's
    own _validate_vendors (guard-input-soundness); a malformed [vendors] is CANNOT-EVALUATE, never a
    partial allow-set that would false-reject a legitimately-extended promoted record. Read once per stage
    and shared by the union, the existence set, and the post-claim re-check."""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    data = _read_toml(store_root_fd, manifest_rel)
    if data is None:
        raise _cannot("{}: the store manifest is absent; the registered vendor set cannot be determined "
                      "(spec 9)".format(manifest_rel))
    findings = []
    registered = _opf_store._validate_vendors(data.get("vendors"), findings)
    if findings:
        raise _cannot("{}: [vendors] is malformed ({}); the registered vendor set cannot be determined "
                      "(fail-closed)".format(manifest_rel, "; ".join(findings)))
    return frozenset(registered)


def _active_store_ids(store_root_fd, machine_rel, active_types, roster,
                      registered_vendors=frozenset()):
    """Enumerate every supported id in the active inline store plus archive.

    Baseline/importer indexes and worklog receive complete validation. Module indexes may be absent or
    empty only; any non-empty module index is refused. Archive entries are seated only after their
    destination payload opens and confirms the exact id."""
    ids = []
    for type_name in sorted(active_types):
        ids.extend(_index_ids(store_root_fd, machine_rel, type_name, roster, registered_vendors))
    ids.extend(_worklog_ids(store_root_fd, machine_rel, roster, registered_vendors))
    ids.extend(_archive_ids(store_root_fd, machine_rel, roster, registered_vendors))
    return ids


def _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids,
                      registered_vendors=frozenset(), skip_run_id=None):
    """The R6 uniqueness union (spec 11): this run's minted ids plus every id already present anywhere a
    minted id could collide, the WHOLE active store (every ENABLED active type index, the active worklog,
    the archive) and every sibling staging run. One assembly path, shared by the pre-write check and the
    post-claim re-check (B2/F3/F4/F2). CLASS 3: `roster` + `registered_vendors` carry the full record
    contract every tier's records are validated against, so a malformed record ANYWHERE fails closed rather
    than entering the union silently. `skip_run_id`, when set, excludes that run's own dir from the sibling
    sweep (the re-check, so a run does not self-collide on its just-written indexes)."""
    union = list(minted_ids)
    union.extend(_active_store_ids(store_root_fd, machine_rel, active_types, roster, registered_vendors))
    union.extend(_sibling_ids(store_root_fd, machine_rel, roster, registered_vendors,
                              skip_run_id=skip_run_id))
    return union


# --- plan validation (spec 14.1) ---------------------------------------------------------------------

def _validate_plan_shape(plan):
    if not isinstance(plan, dict):
        raise _cannot("plan must be a table")
    extra = set(plan) - _PLAN_KEYS
    if extra:
        raise _finding("plan has unknown key(s): {}".format(
            ", ".join(_opf_store._sorted_key_names(extra))))
    if "version" in plan:
        # A version-ledger (14.3) candidate is a v1 deferral, refused fail-closed rather than silently
        # dropped (disclose-guard-residuals).
        raise _cannot("plan carries a `version` (14.3) candidate; version-ledger staging is deferred in "
                      "this build (fail-closed)")
    fragments = plan.get("fragments")
    if not isinstance(fragments, dict):
        raise _finding("plan.fragments must be a table of source-path -> fragment rows")
    return fragments


def _tile_spans(rows, source_len, where):
    """Confirm the rows' spans tile [0, source_len) exactly: sorted, gap-free, overlap-free, half-open,
    ending at the observed byte length; an empty source carries exactly one explicit [0, 0] row. Any
    violation is a finding. Returns the ordered list of (start, end, row_index)."""
    spans = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _finding("{}: fragment row {} is not a table".format(where, i))
        extra = set(row) - _FRAGMENT_ROW_KEYS
        if extra:
            raise _finding("{}: fragment row {} has unknown key(s): {}".format(
                where, i, ", ".join(_opf_store._sorted_key_names(extra))))
        span = row.get("span")
        if (not isinstance(span, list) or len(span) != 2
                or not all(type(x) is int for x in span)):
            raise _finding("{}: fragment row {} span must be a [start, end] pair of integers".format(where, i))
        start, end = span
        if not (0 <= start <= end):
            raise _finding("{}: fragment row {} span [{}, {}] is not a valid half-open range".format(
                where, i, start, end))
        spans.append((start, end, i))
    spans.sort()
    if source_len == 0:
        if not (len(spans) == 1 and spans[0][0] == 0 and spans[0][1] == 0):
            raise _finding("{}: an empty source must carry exactly one explicit [0, 0] row".format(where))
        return [(0, 0, spans[0][2])]
    cursor = 0
    for start, end, _idx in spans:
        if start != cursor:
            raise _finding("{}: spans do not tile [0, {}) at offset {} (gap or overlap; got start {})".format(
                where, source_len, cursor, start))
        cursor = end
    if cursor != source_len:
        raise _finding("{}: spans end at {} but the source is {} bytes (nothing may be dropped)".format(
            where, cursor, source_len))
    return spans


# --- the staging step --------------------------------------------------------------------------------

def stage_import(product_root, import_set, plan, *, now, run_nonce):
    """Validate an enumerated import set against an untrusted mapping plan and, on a full pass, stage the
    byte-canonical candidate under `.working/imports/<run-id>/` (store scope; spec 14.1). Writes only the
    `.working/imports/` staging root (created if absent) and the new run directory beneath it; the active
    store, its counters, indexes, archive, and the sources are read-only. Returns a StageResult; fail-closed
    on anything unreadable, malformed, exotic, or outside the supported subset, never a silent clean pass."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return StageResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        _require_utc(now)
        _require_nonce(run_nonce)
        if not (isinstance(import_set, (list, tuple)) and all(isinstance(p, str) for p in import_set)):
            raise _cannot("import_set must be a list of source-path strings")
        # MINOR-5: product_root is the only public argument not type-guarded; a non-path value would flow
        # into resolve_store as an uncaught TypeError rather than the module's controlled verdict 2. Guard it
        # like now/run_nonce/import_set so the argument-guard posture stays total (a path str or os.PathLike).
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path string or os.PathLike, got {}".format(
                type(product_root).__name__))

        try:
            resolution = _opf_store.resolve_store(product_root)
            if resolution.status != _opf_store.RESOLVED:
                raise _cannot("store did not resolve ({}: {})".format(resolution.status, resolution.detail))
            mv = _opf_store.load_manifest(resolution)
        except ValueError as exc:
            # ValueError family, at the store-TOML PARSE locus: an oversized integer literal in the MANIFEST
            # (read via resolve/load_manifest, not the _read_toml wrapper) makes tomllib raise a bare
            # ValueError. Convert it to CANNOT-EVALUATE HERE, at the parse boundary, so the removal of the
            # former function-wide `except ValueError` does not let a store-parse ValueError escape while an
            # unrelated internal ValueError still propagates as a real error (no-concealed-failure).
            raise _cannot("cannot parse store manifest for {!r} ({})".format(product_root, exc))
        if mv.status != _opf_store.VALID:
            raise _cannot("store manifest is not VALID ({}: {})".format(
                mv.status, "; ".join(mv.findings)))
        machine_rel = resolution.machine_rel

        try:
            product_root_fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
        except OSError as exc:
            # M2: an OSError opening the PRODUCT root AFTER resolution (e.g. a permission revocation racing
            # the resolved read) is the module's fail-closed CANNOT-EVALUATE, never an escape from
            # stage_import (consistent with M10 and the documented outcome contract).
            raise _cannot("cannot open product root {!r} ({})".format(product_root, exc))
        try:
            sources = _read_sources(product_root_fd, import_set)
        finally:
            os.close(product_root_fd)

        try:
            store_root_fd = _opf_store._open_store_root_fd(
                resolution.store_root, resolution.pointer_source != "default")
        except OSError as exc:
            # M2: likewise an OSError re-opening the STORE root after resolution and manifest validation
            # have themselves opened it is CANNOT-EVALUATE, not an uncaught escape from stage_import.
            raise _cannot("cannot open store root {!r} ({})".format(resolution.store_root, exc))
        try:
            return _stage_resolved(store_root_fd, machine_rel, sources, plan, now, run_nonce)
        finally:
            os.close(store_root_fd)
    except _StageError as exc:
        return StageResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        # CLASS 1: a non-ENOENT OSError is converted to a JournalError at the root (_journal._lstat_at, now
        # fail-closed like its _read_contained sibling). That JournalError can reach stage_import from any
        # fs stat/read path that is not already wrapped closer in, notably _write_run's direct
        # _lstat_contained on the imports/ root and any store read whose lstat step _opf_store leaves
        # unwrapped. Surface it as the module's CANNOT-EVALUATE verdict rather than let it escape
        # stage_import's outcome contract (never a raw error, never a silent clean pass).
        return StageResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        # CLASS 1 (class-complete backstop): ANY OSError reaching here from ANY read path not already
        # converted closer in (a raw os.fstat/os.dup/os.close on a store, source, sibling, or archive read,
        # or a path a future edit adds) becomes the module's fail-closed CANNOT-EVALUATE verdict, never an
        # uncaught escape from stage_import's outcome contract and never a silent clean pass. The primary
        # readers convert their own os.read/os.listdir/os.close at the site (regions A/B/F); this backstop
        # guarantees the remaining reachable os.* calls (enumerated in the draft) cannot escape.
        return StageResult(CANNOT_EVALUATE, ["fail-closed on a filesystem read error: {}".format(exc)])
    except RecursionError as exc:
        # CLASS 3 (class-complete backstop, recursion): a plan model nested past the interpreter recursion
        # limit overflows copy.deepcopy at the candidate/worklog mint (the U8 emitter is iterative and bounds
        # no depth), raising RecursionError outside the OSError/ValueError families. It is a malformed,
        # out-of-subset input the outcome contract owes verdict 2, never an uncaught crash (SECA resource-
        # bounds: recursion is bounded and fails safe). The stack has unwound to stage_import by the time this
        # handler runs, so building the fail-closed StageResult has ample headroom.
        return StageResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


def _read_sources(product_root_fd, import_set):
    """Read each declared source as raw bytes through the contained no-follow discipline, lstat-gated so a
    directory or FIFO is refused BEFORE any (possibly blocking) open. Returns an ordered list of dicts
    {path, raw, body, sha256, size}. A missing declared member, an exotic type, a symlinked component, or a
    non-UTF-8 body is CANNOT-EVALUATE (absence is clean only OUTSIDE the declared set)."""
    seen = set()
    out = []
    for rel in import_set:
        if rel in seen:
            raise _finding("import_set names {!r} more than once".format(rel))
        seen.add(rel)
        if not _opf_store._is_contained_relpath(rel):
            raise _cannot("source path {!r} is not a contained repo-relative path".format(rel))
        try:
            st = _journal._lstat_contained(product_root_fd, rel)
        except _journal.JournalError as exc:
            # _is_contained_relpath accepts a normalizable internal '..' (e.g. sub/../a.txt) that
            # _open_parent's _check_rel then rejects. Convert that JournalError into the module's
            # fail-closed StageResult contract rather than letting it escape stage_import (F10).
            raise _cannot("source path {!r} is not a clean contained path ({})".format(rel, exc))
        except UnicodeEncodeError as exc:
            # ValueError family, at the filesystem-name-codec locus: a declared source path carrying a lone
            # surrogate passes the lexical/control-character guards but makes the os.stat filename encode
            # raise UnicodeEncodeError (a ValueError subclass). Convert it HERE, at the fs-name locus, so the
            # removal of the former function-wide `except ValueError` still gives a verdict-2 for this
            # exotic-but-real input while an unrelated internal ValueError propagates (no-concealed-failure).
            raise _cannot("source path {!r} is not encodable for this filesystem ({})".format(rel, exc))
        if st is None:
            raise _cannot("declared source {!r} is absent (a declared member is never nothing-to-do)".format(rel))
        if not stat.S_ISREG(st.st_mode):
            raise _cannot("declared source {!r} is not a regular file (type-gated before open)".format(rel))
        try:
            raw, _fst = _journal._read_contained(product_root_fd, rel)
        except _journal.JournalError as exc:
            raise _cannot("cannot read source {!r} ({})".format(rel, exc))
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _cannot("source {!r} is not UTF-8 decodable ({}); a non-UTF-8 source is unsupported in "
                          "this build (fail-closed, disclosed residual)".format(rel, exc))
        out.append({"path": rel, "raw": raw, "body": body,
                    "sha256": _sha256_hex(raw), "size": len(raw)})
    return out


def _run_id(sources, plan_bytes, now, run_nonce):
    """`imp-<UTCSTAMP>Z-<hash16>`: the stamp is the injected instant, the hash covers a canonical
    descriptor of the sources, the plan bytes, and the nonce. Identical inputs give a byte-identical id;
    different sources, plan, or nonce give a different id."""
    descriptor = {
        "format": _RUN_DESCRIPTOR_FORMAT,
        "nonce": run_nonce,
        "plan_sha256": _sha256_hex(plan_bytes),
        "source": [{"path": s["path"], "sha256": s["sha256"], "size": s["size"]}
                   for s in sorted(sources, key=lambda s: s["path"])],
    }
    digest = _sha256_hex(_emit_bytes(descriptor, "run descriptor"))[:16]
    run_id = "imp-{}-{}".format(now.strftime("%Y%m%dT%H%M%SZ"), digest)
    if not _RUN_ID_RE.match(run_id):
        raise _cannot("computed run-id {!r} does not match the grammar (fail-closed)".format(run_id))
    return run_id


def _validate_lf_provenance(rec, where):
    """The four spec-14.1 provenance fields U2's _validate_type_specific does not yet cover (the LF branch
    is a proposed U2 follow-on): source_path a contained relpath, source_digest 64-hex lowercase, span a
    two-int [start, end] with 0 <= start <= end, run_id a non-empty control-free string. A finding each."""
    sp = rec.get("source_path")
    if not (isinstance(sp, str) and _opf_store._is_contained_relpath(sp)):
        raise _finding("{}: legacy_fragment.source_path must be a contained relpath".format(where))
    sd = rec.get("source_digest")
    if not (isinstance(sd, str) and _HEX64_RE.match(sd)):
        raise _finding("{}: legacy_fragment.source_digest must be a 64-hex lowercase digest".format(where))
    span = rec.get("span")
    if not (isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)
            and 0 <= span[0] <= span[1]):
        raise _finding("{}: legacy_fragment.span must be a [start, end] pair with 0 <= start <= end".format(where))
    rid = rec.get("run_id")
    if not (isinstance(rid, str) and rid and not any(ord(c) < 0x20 or ord(c) == 0x7f for c in rid)):
        raise _finding("{}: legacy_fragment.run_id must be a non-empty control-free string".format(where))


def _validate_candidate_model(rec, roster, where):
    """A plan-supplied candidate model becomes a record ONLY after U7 mints its id; a candidate carrying
    its own `id` is a finding (an untrusted plan does not allocate from the pool). Returns its declared
    type namespace, or raises a finding. The type must be a mintable roster type (never legacy_fragment,
    which U7 mints itself)."""
    if not isinstance(rec, dict):
        raise _finding("{}: candidate model is not a table".format(where))
    if "id" in rec:
        raise _finding("{}: a candidate model may not carry an `id`; U7 mints ids (spec 8.2)".format(where))
    rtype = rec.get("type")
    if not isinstance(rtype, str) or rtype not in roster:
        raise _finding("{}: candidate `type` {!r} is not a supported record type".format(where, rtype))
    if rtype == LF_TYPE:
        raise _finding("{}: a candidate may not declare type legacy_fragment (U7 mints quarantine "
                       "records itself)".format(where))
    return roster[rtype].namespace


def _stage_resolved(store_root_fd, machine_rel, sources, plan, now, run_nonce):
    """The core: validate the plan against the sources, mint ids, build the candidate models, run the R6
    and counters gates, and stage the byte-canonical run directory. Returns a StageResult."""
    roster = _roster()
    stamp = _rfc3339(now)

    plan_bytes = _emit_bytes(plan, "plan")           # also proves the plan is emittable (else CANNOT-EVALUATE)
    fragments = _validate_plan_shape(plan)
    source_by_path = {s["path"]: s for s in sources}

    # Every declared source has a fragments entry and vice versa (a fragments key naming an undeclared
    # source is a finding).
    for sp in fragments:
        if sp not in source_by_path:
            raise _finding("plan.fragments names {!r}, which is not in the import set".format(sp))
    for sp in source_by_path:
        if sp not in fragments:
            raise _finding("source {!r} has no plan.fragments entry (every source must be mapped)".format(sp))

    run_id = _run_id(sources, plan_bytes, now, run_nonce)
    run_rel = "{}/{}".format(IMPORTS_REL, run_id)   # store-scope `.working/imports/<run-id>` (spec 14.1)

    # --- counters: validate, then mint above the recorded high-water --------------------------------
    counters_rel = "{}/counters.toml".format(machine_rel)
    counters_data = _read_toml(store_root_fd, counters_rel)
    if counters_data is None:
        raise _cannot("{}: the store has no counters.toml; U7 mints from it (spec 8.2)".format(counters_rel))
    high_water, cfindings = _opf_schema.validate_counters(counters_data)
    if cfindings:
        raise _cannot("{}: {}".format(counters_rel, "; ".join(cfindings)))
    working_high = dict(high_water)

    def mint(ns, where):
        # A namespace U7 must mint into that counters.toml does not track is CANNOT-EVALUATE: allocating
        # from a missing counter would read high-water 0 and could reuse an existing id (spec 8.2). A store
        # that accepts imports into a namespace declares its counter (initialized to 0).
        if ns not in working_high:
            raise _cannot("{}: counters.toml does not track namespace {!r}; declare it (initialized to 0) "
                          "to accept imports into it (spec 8.2)".format(where, ns))
        rid, new_n = _opf_schema.next_id(working_high, ns)
        working_high[ns] = new_n
        return rid

    # B2: U7's active-store readers are written for the INLINE layout; a non-inline store is fail-closed
    # BEFORE any active index is read, so a per-record store cannot slip through as a partial inline read.
    _require_inline_layout(store_root_fd, machine_rel)
    # B2: the ENABLED active-type set the whole-store id union and the duplicate/candidate-link existence
    # authority scan, derived from the store's [modules] config (fail-closed on a malformed config, never a
    # partial union). Computed once, shared by the union, the existence set, and the post-claim re-check.
    active_types = _active_types(store_root_fd, machine_rel)
    # CLASS 3: the registered x-<vendor> allow-set every promoted active record's full contract is
    # validated against, from the authoritative manifest (guard-input-soundness). Shared by the union, the
    # existence/target authority, and the re-check so a malformed record at ANY tier is CANNOT-EVALUATE.
    registered_vendors = _registered_vendors(store_root_fd, machine_rel)

    # Refuse every non-empty active module index before any ordinary active index, worklog, or archive
    # reader can seat an id. _record_ids repeats the refusal during later reads to close a race.
    _refuse_nonempty_module_indexes(
        store_root_fd, machine_rel, roster, registered_vendors)

    # --- walk the plan, minting candidate and quarantine records ------------------------------------
    candidate_records = {}   # type_name -> [record model]
    lf_records = []
    worklog_records = []
    minted_ids = []
    mapping_states = {}
    state_counts = {s: 0 for s in MAPPING_STATES}
    existing_lookup = None    # lazily built id set for duplicate-target / candidate-link existence (B2/B7)

    def existing_ids():
        # The active + archive existence authority a `duplicate` target and a candidate link resolve
        # against (NOT siblings: a candidate must reference a durable existing record, not a sibling
        # proposal). Built once, on first use. CLASS 3: every active record is contract-validated, so a
        # malformed active record is CANNOT-EVALUATE rather than a resolvable target.
        nonlocal existing_lookup
        if existing_lookup is None:
            existing_lookup = _existing_id_set(store_root_fd, machine_rel, active_types, roster,
                                               registered_vendors)
        return existing_lookup

    for sp in sorted(fragments):
        rows = fragments[sp]
        if not isinstance(rows, list):
            raise _finding("plan.fragments[{!r}] must be an array of fragment rows".format(sp))
        source = source_by_path[sp]
        spans = _tile_spans(rows, source["size"], "plan.fragments[{!r}]".format(sp))
        mapping_states[sp] = []
        for (start, end, idx) in spans:
            row = rows[idx]
            state = row.get("state")
            if not isinstance(state, str) or state not in MAPPING_STATES:
                raise _finding("plan.fragments[{!r}] row {} has an unknown state {!r}".format(sp, idx, state))
            where = "plan.fragments[{!r}] row {} ({})".format(sp, idx, state)
            # B6: reject any field the state does not use. span/state/note are common to every state;
            # `record` is applicable ONLY to a mapped/split candidate row and `target` ONLY to a duplicate;
            # a quarantine state carries neither. An inapplicable field (a `target` on an unmapped row, a
            # `record` on a duplicate) is untrusted plan data that would otherwise stage clean while
            # contradicting the state, so it is a finding, never silently dropped (spec 14.1). This
            # subsumes the earlier F9 target-on-mapped/split check.
            allowed_keys = {"span", "state", "note", "origin"}
            if state in _CANDIDATE_STATES:
                allowed_keys.add("record")
            elif state == _DUPLICATE_STATE:
                allowed_keys.add("target")
            inapplicable = set(row) - allowed_keys
            if inapplicable:
                raise _finding("{}: fragment row carries field(s) {} not applicable to its state "
                               "(`record` only on mapped/split, `target` only on duplicate; "
                               "span/state/note are common)".format(
                                   where, ", ".join(_opf_store._sorted_key_names(inapplicable))))
            # CLASS 4: type-validate EVERY plan/row field, not a subset. span (an int pair) and state (a
            # MAPPING_STATES member) are validated above; `record` and `target` are validated in their
            # state branches below; `note`, common to every state, must be a string when present. A
            # wrong-typed but EMITTABLE note (an int, a bool, a flat array) would otherwise stage verbatim
            # into plan.toml as promotion-ready, contradicting spec 14.1's fully-validated plan (test 13
            # covers only a non-emittable note refused at the emitter boundary).
            if "note" in row and not isinstance(row.get("note"), str):
                raise _finding("{}: fragment row `note` must be a string when present (spec 14.1)".format(
                    where))
            # Provenance (spec 14.1): every fragment row carries a required `origin` from the closed
            # vocabulary. A missing or out-of-vocabulary origin is a finding, never defaulted: an omitted
            # origin would silently bypass the acceptance gate a model_proposal-origin resting mapping owes.
            origin = row.get("origin")
            if not isinstance(origin, str) or origin not in _ORIGIN_SET:
                raise _finding("{}: fragment row `origin` must be one of {} (spec 14.1 provenance; a "
                               "missing or out-of-vocabulary origin is refused)".format(
                                   where, ", ".join(_ORIGIN_VALUES)))
            state_counts[state] += 1
            target = row.get("target")
            mapping_states[sp].append({"span": [start, end], "state": state, "target": target,
                                       "origin": origin})

            if state in _CANDIDATE_STATES:
                # A mapped/split row mints its OWN id (B6 already refused a contradictory `target`). CLASS 5:
                # the candidate is a DEEP, independent copy of the plan model, so the staged candidate never
                # shares a mutable with the staged plan snapshot (plan.toml, emitted from `plan` above) and
                # neither can be perturbed through the other.
                model = row.get("record")
                ns = _validate_candidate_model(model, roster, where)
                rtype = model["type"]
                rid = mint(ns, where)
                rec = copy.deepcopy(model)
                rec["id"] = rid
                # CLASS 6 (record-level source provenance): EVERY mapped/split candidate carries a
                # record-level provenance ref (source path + content digest + span + run id), so nothing
                # accepted has an untraceable origin. CLASS 2: created_at and updated_at describe the
                # original item's history and are never fabricated from the staging clock. The importer
                # exception may admit an omitted created_at when provenance is present; updated_at remains
                # required by validate_record, so its omission is refused. A legacy_fragment is a new
                # importer artefact and legitimately receives this import's observed clock instant.
                # CLASS 2 (created_at set-once vs updated_at mutable, the omission asymmetry): created_at
                # records a CREATION instant, set once and never rewritten, so spec 8.3 lets an importer
                # OMIT it (recording the unknown via provenance) and the record still validates. updated_at
                # records the LAST-MUTATION instant, which every later edit rewrites, so spec 8.3 requires
                # it with NO importer exception. That asymmetry is WHY an omitted created_at stages with the
                # field omitted here, while an omitted updated_at is REFUSED by validate_record below (never
                # fabricated from the staging clock; CLASS 2b). Extending the importer exception to
                # updated_at would need an _opf_schema envelope change with store-wide blast radius and is
                # DECIDED AGAINST: U7 holds spec 8.3 as-is (the maintainer owns any 8.3 adjustment).
                omitted = [f for f in ("created_at", "updated_at") if f not in rec]
                note = "imported fragment [{}:{}] of {} (sha256:{}) in run {}".format(
                    start, end, source["path"], source["sha256"], run_id)
                if omitted:
                    note += "; original {} unknown".format(" and ".join(omitted))
                prov = {"kind": "path", "locator": source["path"], "note": note}
                refs = rec.get("refs")
                if isinstance(refs, list):
                    rec["refs"] = list(refs) + [prov]
                elif refs is None:
                    rec["refs"] = [prov]
                # a non-list `refs` is left untouched for validate_record to reject as malformed
                rv = _opf_schema.validate_record(rec, expected_type=rtype, specs=roster,
                                                 registered_vendors=registered_vendors)   # MINOR (a)
                if rv.status != _opf_store.VALID:
                    raise _finding("{}: candidate is not a valid {} ({})".format(
                        where, rtype, "; ".join(rv.findings)))
                # B7: a candidate may LINK to an EXISTING record, but candidate-to-candidate links are
                # unsupported: every links[].id must resolve in the active/archive existence authority (the
                # same set a duplicate target resolves against). An unresolved link (a dangling id, or a link
                # to an id minted only in this import set) is a finding: a candidate with a dangling link is
                # not a fully-validated candidate (spec 14.1). validate_record already proved each link
                # well-formed, so link.get("id") is a valid id string here.
                for link in rec.get("links", []):
                    lid = link.get("id")
                    if lid not in existing_ids():
                        raise _finding("{}: candidate links to {!r}, which resolves to no existing active "
                                       "or archived record (candidate-to-candidate links are "
                                       "unsupported)".format(where, lid))
                candidate_records.setdefault(rtype, []).append(rec)
                minted_ids.append(rid)
            elif state == _DUPLICATE_STATE:
                if _opf_schema._valid_id_shape(target) is None:
                    raise _finding("{}: a duplicate must name an existing record id in `target`".format(where))
                if target not in existing_ids():
                    raise _finding("{}: duplicate target {!r} names no existing active or archived "
                                   "record".format(where, target))
            else:  # a quarantine state
                rid = mint("LF", where)
                # F6: extract the fragment body from the RAW BYTES using the byte offsets, THEN decode, so a
                # byte span never indexes decoded code-points (which drops or corrupts a non-ASCII body). A
                # span that splits a multi-byte UTF-8 sequence is a malformed span: decode fail-closed and
                # raise a finding rather than emit corrupted or lossy text.
                try:
                    frag_body = source["raw"][start:end].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise _finding("{}: span [{}, {}] splits a multi-byte UTF-8 sequence in {!r}; a "
                                   "fragment span must fall on character boundaries ({})".format(
                                       where, start, end, sp, exc))
                lf = {
                    "id": rid, "type": LF_TYPE, "status": "quarantined",
                    "title": "Legacy fragment from {} [{}:{}]".format(sp, start, end),
                    "created_at": stamp, "updated_at": stamp,
                    "actor": {"kind": "importer"},
                    "source_path": sp, "source_digest": source["sha256"],
                    "span": [start, end], "run_id": run_id,
                    "body": frag_body,
                }
                _validate_lf_provenance(lf, where)
                rv = _opf_schema.validate_record(lf, expected_type=LF_TYPE, specs=roster)
                if rv.status != _opf_store.VALID:
                    raise _finding("{}: quarantine record is not a valid legacy_fragment ({})".format(
                        where, "; ".join(rv.findings)))
                lf_records.append(lf)
                minted_ids.append(rid)

    # --- worklog candidates (optional) --------------------------------------------------------------
    wl_candidates = plan.get("worklog")
    if wl_candidates is not None:
        if not isinstance(wl_candidates, list):
            raise _finding("plan.worklog must be an array of worklog candidate models")
        for i, model in enumerate(wl_candidates):
            where = "plan.worklog[{}]".format(i)
            if not isinstance(model, dict) or "id" in model:
                raise _finding("{}: a worklog candidate must be a table with no `id` (U7 mints)".format(where))
            rid = mint("WL", where)
            rec = copy.deepcopy(model)   # CLASS 5: independent of the plan snapshot
            rec["id"] = rid
            # CLASS 2: a worklog entry's `date` is a HISTORICAL event time. When the plan supplies none it
            # is UNKNOWN and is NEVER stamped from the staging clock (timestamp-from-clock). The reduced
            # worklog envelope requires `date` with NO importer exception, so an omitted date leaves
            # validate_record to REFUSE the entry as incomplete rather than U7 fabricating one; the plan must
            # carry the historical date from the source.
            rv = _opf_schema.validate_record(rec, expected_type="worklog", specs=roster,
                                             registered_vendors=registered_vendors)   # MINOR (a)
            if rv.status != _opf_store.VALID:
                raise _finding("{}: candidate is not a valid worklog entry ({})".format(
                    where, "; ".join(rv.findings)))
            worklog_records.append(rec)
            minted_ids.append(rid)

    # The worklog release-boundary gate runs ONLY when worklog entries were actually minted: an absent OR
    # explicitly-empty worklog list stages nothing into the worklog, so there is no new WL number to gate
    # and the version ledger is not consulted (relaxing an over-strict rule that an explicitly-empty list
    # still demanded version.toml; the ledger gates NEW entries against an already-released span, spec 6.2).
    # When entries ARE minted the gate is TWO checks: U3's full structural validation of the ledger (a
    # malformed EXISTING ledger is an unusable input, not a plan defect: CANNOT-EVALUATE), then the
    # release-boundary check (a new entry never lands inside an already-released span: a finding). The
    # whole-store rotation/deletion partition is a promotion concern (U7 rotates and deletes nothing).
    if worklog_records:
        version_rel = "{}/version.toml".format(machine_rel)
        version_data = _read_toml(store_root_fd, version_rel)
        if version_data is None:
            raise _cannot("{}: worklog candidates need the store's version ledger (spec 6.2)".format(version_rel))
        vv = _opf_release.validate_version(version_data)          # F8: U3's full structural validation first
        if vv.status == _opf_release.CANNOT_EVALUATE:
            raise _cannot("{}: version ledger does not evaluate ({})".format(
                version_rel, "; ".join(vv.findings)))
        if vv.status != _opf_release.VALID:
            raise _cannot("{}: version ledger is structurally invalid ({})".format(
                version_rel, "; ".join(vv.findings)))
        wl_numbers = [_opf_schema._valid_id_shape(r["id"])[1] for r in worklog_records]
        wl_findings = _opf_release.check_no_append_into_released(version_data, wl_numbers)
        if wl_findings:
            raise _finding("worklog release-boundary: {}".format("; ".join(wl_findings)))

    # --- R6 uniqueness over the WHOLE active store + archive + siblings + minted ids (spec 11) -------
    # One assembly path scans every supported active index, the active worklog, verified archive
    # destinations, and every sibling run. A non-empty module-tier index, unreadable/symlinked tier, or
    # archive destination that cannot confirm its declared id is CANNOT-EVALUATE, never partially seated.
    dup_findings = _opf_schema.check_unique_ids(
        _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids, registered_vendors))
    if dup_findings:
        raise _finding("R6 id uniqueness: {}".format("; ".join(dup_findings)))

    # MAJOR-4 (spec 8.2, counters never regress below an allocated id): the declared counters high-water is
    # the durable reservation promotion advances; a high-water BELOW an id already seated in the active store
    # or archive is a regressed, corrupt basis. The R6 union above already refuses the case where a MINTED id
    # lands ON an existing one (verdict 1); this catches the residual where the minted ids clear every
    # existing id yet the declared basis still under-states the store (counter 0 with an existing BI-5,
    # minting BI-1), which would otherwise certify a promotion-ready candidate counters.toml that
    # under-reserves the namespace and sets up avoidable collisions at promotion. The declared high-water is
    # a passed premise validated against the authoritative observed ids already in hand (guard-input-
    # soundness); an under-statement is CANNOT-EVALUATE (a store inconsistent with its own indexes is an
    # unusable basis). Placed AFTER the union so a genuine collision keeps the author's verdict-1 finding.
    # (Re-reads the durable ids the union already scanned; a later change could thread them to avoid it.)
    regressed = _counters_regressed_below_existing(
        _active_store_ids(store_root_fd, machine_rel, active_types, roster, registered_vendors),
        high_water, counters_rel)
    if regressed:
        raise _cannot("; ".join(regressed))

    # --- counters soundness: monotonic advance, minted ids within the advanced high-water -----------
    mono = _opf_schema.check_monotonic(high_water, working_high)
    if mono:
        raise _finding("counters monotonicity: {}".format("; ".join(mono)))
    within = _opf_schema.check_ids_within_counters(minted_ids, working_high)
    if within:
        raise _finding("counters reservation: {}".format("; ".join(within)))

    # --- build the byte-canonical run and stage it (two-pass claim + re-check, F2) -------------------
    run_rel_result = _write_run(store_root_fd, machine_rel, run_id, run_rel, plan_bytes, sources, now,
                                run_nonce, mapping_states, candidate_records, lf_records, worklog_records,
                                working_high, state_counts, active_types, roster, minted_ids,
                                registered_vendors)
    return StageResult(CLEAN, [], run_id=run_id, run_rel=run_rel_result, mapping_states=mapping_states,
                       staged_ids=minted_ids, new_high_water=working_high, promotion_ready=True,
                       migration_incomplete=bool(lf_records))


def _existing_id_set(store_root_fd, machine_rel, active_types, roster,
                     registered_vendors=frozenset()):
    """Build the durable existing-id authority for duplicate targets and candidate links.

    Baseline/importer active records and worklog entries are fully validated. Non-empty module indexes
    are refused rather than used as targets. Archived ids enter the authority only after their destination
    record/index opens and confirms that exact id."""
    ids = set()
    for type_name in active_types:
        ids.update(_index_ids(store_root_fd, machine_rel, type_name, roster, registered_vendors))
    ids.update(_worklog_ids(store_root_fd, machine_rel, roster, registered_vendors))
    ids.update(_archive_ids(store_root_fd, machine_rel, roster, registered_vendors))
    return ids


def _counters_regressed_below_existing(durable_ids, high_water, where):
    """Findings for every namespace whose declared counters high-water is below a durable id already seated
    in it, or that is absent from counters.toml entirely while a durable id is seated in it (an absent
    counter reserves nothing, so it is below any allocated id: the class-sibling of the tracked-but-low
    case) (spec 8.2: counters never regress beneath an allocated id). `durable_ids` are the active-store and
    archive ids (promoted, durable reservations); sibling staging ids are excluded (a proposal reserves
    nothing). A malformed durable id is not this gate's concern (the active/archive readers already refuse it
    to CANNOT-EVALUATE upstream), so it is skipped here. Returns a findings list; the caller routes a
    non-empty list to CANNOT-EVALUATE, a corrupt counters basis being an unusable store input."""
    worst = {}
    for rid in durable_ids:
        shape = _opf_schema._valid_id_shape(rid)
        if shape is None:
            continue
        ns, n = shape[0], shape[1]
        # An absent counter reserves nothing, so a namespace MISSING from counters.toml is below any
        # allocated id exactly as a tracked counter sitting beneath a seated id is (spec 8.2). Both leave
        # counters under-stating the store: the absent-namespace door is the class-sibling of the
        # tracked-but-low case and fails closed the same way (a durable id in an untracked namespace was
        # otherwise skipped and the corrupt store certified promotion-ready).
        if (ns not in high_water or n > high_water[ns]) and n > worst.get(ns, 0):
            worst[ns] = n
    findings = []
    for ns in sorted(worst):
        if ns in high_water:
            findings.append(
                "{}: counters high-water {}={} is below the durable id {}-{} already seated in the store "
                "(spec 8.2: a counter never regresses beneath an allocated id; a regressed counter is a "
                "corrupt basis, fail-closed)".format(where, ns, high_water[ns], ns, worst[ns]))
        else:
            findings.append(
                "{}: counters.toml does not track namespace {!r}, but the durable id {}-{} is already "
                "seated in the store (spec 8.2: a namespace with an allocated id must declare its counter; "
                "an untracked namespace under-states the store, a corrupt basis, fail-closed)".format(
                    where, ns, ns, worst[ns]))
    return findings


def _write_run(store_root_fd, machine_rel, run_id, run_rel, plan_bytes, sources, now, run_nonce,
               mapping_states, candidate_records, lf_records, worklog_records, working_high, state_counts,
               active_types, roster, minted_ids, registered_vendors=frozenset()):
    """Emit every file as byte-canonical U8 output (source BODIES verbatim), then stage the run directory
    in two ordered apply_ops passes. Pass 1 claims the exclusive run-dir mkdir (the atomic pool claim: a
    pre-existing run directory is refused) and writes this run's index and provenance files, so its minted
    ids become visible on disk under its own run dir. The sibling+active union is then RE-CHECKED, EXCLUDING
    this run's own dir, and only on a clean re-check is report.toml (the promotion-ready marker) written
    last in pass 2. A colliding id surfaced by the re-check means a concurrent proposal raced this one: no
    report.toml is written and the partial run dir is left as named evidence (CANNOT-EVALUATE). The
    re-check NARROWS the concurrent-proposal window but, absent a lock, does not eliminate it (disclosed in
    the module docstring; the sole durable reservation is counters.toml under the promotion lease)."""
    stamp = _rfc3339(now)

    files = {}   # run-relative-suffix -> byte-canonical U8 TOML bytes
    files["run.toml"] = _emit_bytes({
        "schema": SCHEMA, "run_id": run_id, "staged_at": stamp, "nonce": run_nonce,
        "source": [{"path": s["path"], "sha256": s["sha256"], "size": s["size"]}
                   for s in sorted(sources, key=lambda s: s["path"])],
    }, "run.toml")
    files["plan.toml"] = plan_bytes
    mapping_rows = []
    for sp in sorted(mapping_states):
        for m in mapping_states[sp]:
            row = {"source_path": sp, "span": list(m["span"]), "state": m["state"],
                   "origin": m["origin"]}
            if _opf_schema._valid_id_shape(m.get("target")) is not None:
                row["target"] = m["target"]
            mapping_rows.append(row)
    files["mappings.toml"] = _emit_bytes({"schema": SCHEMA, "mapping": mapping_rows}, "mappings.toml")
    files["candidate/counters.toml"] = _emit_bytes(
        {"schema": SCHEMA, "counters": working_high}, "candidate/counters.toml")
    for rtype in sorted(candidate_records):
        files["candidate/{}.index.toml".format(rtype)] = _emit_bytes(
            {"schema": SCHEMA, "record": candidate_records[rtype]}, "candidate/{}.index.toml".format(rtype))
    if worklog_records:
        files["candidate/worklog.toml"] = _emit_bytes(
            {"schema": SCHEMA, "record": worklog_records}, "candidate/worklog.toml")
    if lf_records:
        files["fragments/legacy_fragment.index.toml"] = _emit_bytes(
            {"schema": SCHEMA, "record": lf_records}, "fragments/legacy_fragment.index.toml")

    # PRODUCER-BOUNDARY ceiling reconciliation: every emitted store-TOML file staged here is later read
    # back through the CONTAINED store reader (_opf_store._read_toml_contained), which REFUSES anything over
    # MAX_STORE_READ_BYTES (1 MiB) fail-closed. The emitter permits far larger output, so a candidate whose
    # emitted bytes exceed that cap would stage and PROMOTE cleanly yet be UNREADABLE afterwards; worse, the
    # post-claim sibling sweep excludes THIS run (skip_run_id), so nothing downstream would catch it. Reject
    # the oversized record HERE, at the producer boundary before promotion, with a clear cannot-evaluate
    # finding, so no store reader is ever handed an index it will refuse (guard-input-soundness; fail at the
    # producer, never hand a consumer an input it structurally cannot read). The raw content-addressed
    # `sources/<sha256>` bodies below are NOT store TOML (never parsed by that reader) and are exempt.
    for _suffix in sorted(files):
        _n = len(files[_suffix])
        if _n > _opf_store.MAX_STORE_READ_BYTES:
            raise _cannot("candidate {} is {} bytes, over the {}-byte contained store-read cap; the staged "
                          "record would be unreadable after promotion (rejected at the producer boundary "
                          "before promotion)".format(_suffix, _n, _opf_store.MAX_STORE_READ_BYTES))

    # F7: preserve each source's FULL original bytes in the run, content-addressed as `sources/<sha256>`
    # (spec 14.2: the original's full content is preserved in the import run). Stored VERBATIM, not through
    # U8 (a raw body, not TOML), so a fully-mapped source's content still survives here; the digest is the
    # source sha256, re-verified by apply_ops. Identical-content sources dedupe to one file.
    source_bodies = {"sources/{}".format(s["sha256"]): s["raw"] for s in sources}

    all_body = dict(files)
    all_body.update(source_bodies)

    # The promotion-ready binding surface (spec 14.1): the plan digest over the staged plan.toml bytes and
    # the inventory digest over the deterministic enumeration of these sources. Both are echoed here as
    # top-level keys so the acceptance record binds {run_id, plan_digest, inventory_digest} against one
    # authoritative marker, and a regenerated plan (a new run, hence new bytes) invalidates a prior
    # acceptance by construction. inventory_digest is a pure function of the sources (the same value
    # plan_import's scan computes), so report.toml carries it for every stage path.
    plan_digest = "sha256:" + _sha256_hex(plan_bytes)
    _, inventory_digest, _, _ = _build_inventory(sources)
    report = {
        "schema": SCHEMA, "run_id": run_id, "verdict": CLEAN, "promotion_ready": True,
        "migration_incomplete": bool(lf_records),
        "plan_digest": plan_digest, "inventory_digest": inventory_digest,
        "counts": {k: v for k, v in state_counts.items() if v},
        "artifact": [{"path": suffix, "sha256": _sha256_hex(data)}
                     for suffix, data in sorted(all_body.items())],
    }
    files_report = _emit_bytes(report, "report.toml")

    content = {run_rel + "/" + suffix: data for suffix, data in all_body.items()}

    # --- pass 1: claim the exclusive run dir and stage every file EXCEPT report.toml ------------------
    ops = []
    imports_rel = IMPORTS_REL   # store-scope `.working/imports` staging root (spec 14.1)
    imports_st = _journal._lstat_contained(store_root_fd, imports_rel)
    if imports_st is None:
        # The `.working/imports/` staging ROOT (spec 14) is created if absent: part of the staging area, not
        # the active store (F1); its parent `.working` always exists on a resolved store. A race surfaces as
        # a JournalError -> CANNOT-EVALUATE.
        ops.append({"op": "mkdir", "path": imports_rel, "poststate": {"kind": "dir", "mode": DIR_MODE}})
    elif not stat.S_ISDIR(imports_st.st_mode):
        raise _cannot("{} exists but is not a directory (fail-closed)".format(imports_rel))
    ops.append({"op": "mkdir", "path": run_rel, "poststate": {"kind": "dir", "mode": DIR_MODE}})
    ops.append({"op": "mkdir", "path": run_rel + "/candidate",
                "poststate": {"kind": "dir", "mode": DIR_MODE}})
    ops.append({"op": "mkdir", "path": run_rel + "/sources",
                "poststate": {"kind": "dir", "mode": DIR_MODE}})
    if lf_records:
        ops.append({"op": "mkdir", "path": run_rel + "/fragments",
                    "poststate": {"kind": "dir", "mode": DIR_MODE}})
    for suffix in sorted(all_body):
        path = run_rel + "/" + suffix
        ops.append({"op": "create", "path": path,
                    "poststate": {"kind": "file", "mode": FILE_MODE,
                                  "content-sha256": _sha256_hex(all_body[suffix])}})

    def staged_reader(op):
        return content[op["path"]]

    try:
        _journal.apply_ops(store_root_fd, ops, staged_reader)
    except _journal.JournalError as exc:
        # A pre-existing run directory (exclusive mkdir refusal), a mid-write failure, or a racing tree:
        # fail-closed. A partial run dir is left as named evidence (no report.toml); its on-disk ids
        # enumerate into a later R6 union.
        raise _cannot("staging apply failed ({}); nothing promoted, any partial run left as "
                      "evidence".format(exc))

    # F2: re-check the union AFTER the claim, EXCLUDING this run's own dir (else it self-collides on its
    # just-written indexes). A collision now means a concurrent proposal raced this one: report.toml is
    # withheld and the partial run dir stays as named evidence. A true TOCTOU interleaving remains
    # (disclosed in the module docstring); it is not single-process reproducible, so the self-test drives
    # _uniqueness_union directly.
    recheck = _opf_schema.check_unique_ids(
        _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids,
                          registered_vendors, skip_run_id=run_id))
    if recheck:
        raise _cannot("R6 re-check after run-dir claim: a concurrent proposal collides ({}); report.toml "
                      "withheld, partial run left as named evidence".format("; ".join(recheck)))

    # --- pass 2: report.toml LAST, the promotion-ready marker, only on a clean re-check ---------------
    report_path = run_rel + "/report.toml"
    content[report_path] = files_report
    report_ops = [{"op": "create", "path": report_path,
                   "poststate": {"kind": "file", "mode": FILE_MODE,
                                 "content-sha256": _sha256_hex(files_report)}}]
    try:
        _journal.apply_ops(store_root_fd, report_ops, staged_reader)
    except _journal.JournalError as exc:
        raise _cannot("report.toml write failed after a clean re-check ({}); partial run left as "
                      "evidence".format(exc))
    return run_rel


# --- the operation layer: scan / plan / apply-promotion (OPF-IMPORT-OPS) -----------------------------

def _fragment_id(source_path, source_digest, start, end):
    """A deterministic, content-inclusive fragment id (Fable adjudication over a location-only id): a
    16-hex digest over the canonical descriptor tuple (extractor version, source path, source digest,
    span). Content-inclusive so each fragment quad is self-verifying and a changed source yields a
    different id, which the plan/apply freshness bindings rely on. `frag-` prefixed so it reads as an id,
    never a path component."""
    descriptor = {
        "format": _FRAGMENT_DESCRIPTOR_FORMAT,
        "extractor_version": EXTRACTOR_VERSION,
        "source_path": source_path,
        "source_digest": source_digest,
        "span": [start, end],
    }
    return "frag-" + _sha256_hex(_emit_bytes(descriptor, "fragment descriptor"))[:16]


def _build_inventory(sources):
    """Assemble the deterministic, canonical inventory from the enumerated sources. Sources sort by
    unsigned UTF-8 path bytes; the whole-file baseline extractor yields exactly one fragment per source,
    span [0, size) (an empty source is one [0, 0] fragment); fragments sort by (path bytes, start, end).
    The `inventory_digest` is computed over the canonical emission of the payload EXCLUDING its own digest
    field, so two scans over identical inputs (in any declaration order) produce a byte-identical inventory
    and an equal digest. Returns (inventory-with-digest, digest, source_records, fragment_records)."""
    ordered = sorted(sources, key=lambda s: s["path"].encode("utf-8"))
    source_records = [{"path": s["path"], "size": s["size"], "sha256": s["sha256"]} for s in ordered]
    fragment_records = []
    for s in ordered:
        # Whole-file baseline: the single fragment's bytes ARE the whole source, so the fragment digest
        # equals the source digest (an empty source: sha256(b"") for both). A finer extractor would differ.
        fragment_records.append({
            "fragment_id": _fragment_id(s["path"], s["sha256"], 0, s["size"]),
            "source_path": s["path"],
            "source_digest": s["sha256"],
            "span": [0, s["size"]],
            "fragment_digest": s["sha256"],
        })
    payload = {
        "format": INVENTORY_FORMAT,
        "extractor": {"id": EXTRACTOR_ID, "version": EXTRACTOR_VERSION},
        "source": source_records,
        "fragment": fragment_records,
    }
    digest = "sha256:" + _sha256_hex(_emit_bytes(payload, "inventory"))
    inventory = dict(payload)
    inventory["inventory_digest"] = digest
    return inventory, digest, source_records, fragment_records


def scan_import(product_root, import_set):
    """Deterministically ENUMERATE the declared import set into a digest-stamped inventory (spec 14.1),
    read-only. Resolves the store first (the init-first precondition: an unresolved or invalid store is
    CANNOT-EVALUATE, so `--scan` cannot run against a location with no store), then reads each declared
    source through the SAME contained no-follow enumerator staging uses (`_read_sources`), so scan, plan,
    and stage share one enumeration path and cannot drift. Every declared input is enumerated in full; an
    unreadable, absent, symlinked, exotic, or non-UTF-8 declared source is a refusing failure naming the
    entry (never a silent skip or an empty inventory). Writes NOTHING and allocates no run id
    (SECI-preview-has-no-side-effects). Returns a ScanResult; fail-closed on anything unreadable or exotic.

    Disclosed residual (inherited from the settled staging pipeline): a non-UTF-8 declared source is
    CANNOT-EVALUATE, matching `stage_import` (the whole pipeline is UTF-8 in this build); a raw-bytes
    extractor is a follow-on. `now`-free by design (Fable adjudication): the inventory carries no
    timestamp, run id, nonce, or absolute root, so it is byte-identical across runs; runtime metadata
    belongs to the plan envelope, not the deterministic inventory."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return ScanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path string or os.PathLike, got {}".format(
                type(product_root).__name__))
        if not (isinstance(import_set, (list, tuple)) and all(isinstance(p, str) for p in import_set)):
            raise _cannot("import_set must be a list of source-path strings")
        # Init-first precondition: resolve and validate the store before enumerating (an unresolved store
        # is CANNOT-EVALUATE, exit 2), even though enumeration reads the product tree, not the store.
        try:
            resolution = _opf_store.resolve_store(product_root)
            if resolution.status != _opf_store.RESOLVED:
                raise _cannot("store did not resolve ({}: {}); run `opf init` first".format(
                    resolution.status, resolution.detail))
            mv = _opf_store.load_manifest(resolution)
        except ValueError as exc:
            raise _cannot("cannot parse store manifest for {!r} ({})".format(product_root, exc))
        if mv.status != _opf_store.VALID:
            raise _cannot("store manifest is not VALID ({}: {})".format(mv.status, "; ".join(mv.findings)))

        try:
            product_root_fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
        except OSError as exc:
            raise _cannot("cannot open product root {!r} ({})".format(product_root, exc))
        try:
            sources = _read_sources(product_root_fd, import_set)
        finally:
            os.close(product_root_fd)

        inventory, digest, source_records, fragment_records = _build_inventory(sources)
        return ScanResult(CLEAN, inventory=inventory, inventory_digest=digest,
                          sources=source_records, fragments=fragment_records)
    except _StageError as exc:
        return ScanResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        return ScanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return ScanResult(CANNOT_EVALUATE, ["fail-closed on a filesystem read error: {}".format(exc)])
    except RecursionError as exc:
        return ScanResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


def _validate_proposals(proposals, source_sizes):
    """Validate an optional set of INERT model proposals (spec 14.1 untrusted plan data) and return them
    sorted deterministically. A proposal is a SUGGESTED mapping recorded verbatim in the review surface,
    never fed to the classifier: it can neither mint a record nor select a write destination. Each is a
    closed `{source_path, span, suggested_state[, note]}` table whose `source_path` MUST name a scanned
    source (confinement: a proposal cannot target a path outside the enumerated set), whose `span` is a
    two-int half-open range within [0, size], whose `suggested_state` is a MAPPING_STATES member, and whose
    `note` (optional) is a string. A malformed proposal is a finding (untrusted data validated at the
    boundary), never silently dropped or accepted. `source_sizes` maps scanned path -> size."""
    if proposals is None:
        return []
    if not isinstance(proposals, (list, tuple)):
        raise _finding("proposals must be a list of inert model-proposal tables")
    out = []
    for i, p in enumerate(proposals):
        where = "proposals[{}]".format(i)
        if not isinstance(p, dict):
            raise _finding("{}: a proposal must be a table".format(where))
        extra = set(p) - _PROPOSAL_KEYS
        if extra:
            raise _finding("{}: proposal carries unknown key(s): {} (a proposal is a closed "
                           "{{source_path, span, suggested_state, note}})".format(
                               where, ", ".join(_opf_store._sorted_key_names(extra))))
        sp = p.get("source_path")
        if not (isinstance(sp, str) and sp in source_sizes):
            raise _finding("{}: proposal.source_path {!r} names no scanned source (a proposal is confined "
                           "to the enumerated import set; it cannot target an arbitrary path)".format(
                               where, sp))
        span = p.get("span")
        if (not isinstance(span, list) or len(span) != 2 or not all(type(x) is int for x in span)
                or not (0 <= span[0] <= span[1] <= source_sizes[sp])):
            raise _finding("{}: proposal.span must be a [start, end] half-open range within [0, {}]".format(
                where, source_sizes[sp]))
        st = p.get("suggested_state")
        if not isinstance(st, str) or st not in MAPPING_STATES:
            raise _finding("{}: proposal.suggested_state {!r} is not a mapping state".format(where, st))
        if "note" in p and not isinstance(p["note"], str):
            raise _finding("{}: proposal.note must be a string when present".format(where))
        out.append({"source_path": sp, "span": [span[0], span[1]], "suggested_state": st,
                    "note": p.get("note", "")})
    out.sort(key=lambda p: (p["source_path"].encode("utf-8"), p["span"][0], p["span"][1]))
    return out


def _render_report_md(inventory_digest, fragments, proposals, run_id):
    """Render IMPORT-REPORT.md (spec 4.2), the human review surface, DETERMINISTICALLY from the frozen
    inventory and the inert proposals, so regenerating it byte-reproduces it (a derivative, never
    independently authored; generated-artefact-source-only). Every source fragment is enumerated with its
    deterministic mapping state (the whole-file baseline is `unmapped`: nothing is mechanically mapped, so
    everything is preserved as a legacy_fragment). Model proposals are listed as INERT suggestions that a
    later attributed step may accept; accepting one is never automatic here (spec 14.1)."""
    lines = [
        "# OPF import review: {}".format(run_id),
        "",
        "GENERATED from the staged inventory and plan (spec 4.2); do not hand-edit (regenerating this file",
        "byte-reproduces it). Every source fragment is enumerated with its deterministic mapping state. In",
        "the whole-file baseline nothing is mechanically mapped, so every fragment is preserved as a",
        "legacy_fragment. Model proposals below are INERT untrusted suggestions (spec 14.1): accepting one",
        "is a later, attributed step (the acceptance-capture + apply-promotion unit), never automatic here.",
        "",
        "inventory digest: `{}`".format(inventory_digest),
        "",
        "## Fragments (deterministic state = unmapped; preserved as legacy_fragment)",
        "",
    ]
    for frag in fragments:
        lines.append("- `{}` [{}:{}] state=unmapped fragment_id=`{}` digest=`{}`".format(
            frag["source_path"], frag["span"][0], frag["span"][1], frag["fragment_id"],
            frag["fragment_digest"]))
    lines += ["", "## Model proposals (inert; require attributed acceptance)", ""]
    if not proposals:
        lines.append("- (none)")
    else:
        for p in proposals:
            suffix = " note={!r}".format(p["note"]) if p["note"] else ""
            lines.append("- `{}` [{}:{}] suggested_state={} origin=model_proposal{}".format(
                p["source_path"], p["span"][0], p["span"][1], p["suggested_state"], suffix))
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_plan_artifacts(product_root, run_rel, run_id, inventory, report_md_bytes, proposal_rows):
    """Stage the plan REVIEW artefacts (inventory.toml + proposals.toml + IMPORT-REPORT.md) into the run dir
    a clean `stage_import` just created, as an additive create-only pass through the same contained,
    no-follow, fsync'd, digest-verified `_journal.apply_ops` primitive staging uses. These are review inputs,
    not the promotion candidate (report.toml, written last by stage_import, remains the promotion-ready
    marker and enumerates only the candidate set); they never mutate the active store. inventory.toml and
    proposals.toml are byte-canonical store TOML held under the contained store-read cap; IMPORT-REPORT.md is
    markdown (never read by the TOML reader). proposals.toml records the validated model proposals verbatim,
    each stamped `origin = "model_proposal"`, so the review path reads machine data and the report is
    byte-reproducible from inventory.toml + proposals.toml + run id. Fail-closed on any write error."""
    resolution = _opf_store.resolve_store(product_root)
    if resolution.status != _opf_store.RESOLVED:
        raise _cannot("store did not resolve for the plan review-artefact write ({}: {})".format(
            resolution.status, resolution.detail))
    inv_bytes = _emit_bytes(inventory, INVENTORY_NAME)
    if len(inv_bytes) > _opf_store.MAX_STORE_READ_BYTES:
        raise _cannot("{} is {} bytes, over the {}-byte contained store-read cap; the staged inventory "
                      "would be unreadable (rejected at the producer boundary)".format(
                          INVENTORY_NAME, len(inv_bytes), _opf_store.MAX_STORE_READ_BYTES))
    # proposals.toml: the validated proposal rows exactly as _validate_proposals normalizes them, each
    # stamped origin = "model_proposal" (their machine-readable resting provenance).
    proposals_model = {
        "schema": SCHEMA, "run_id": run_id,
        "proposal": [dict(p, origin=_MODEL_PROPOSAL_ORIGIN) for p in proposal_rows],
    }
    prop_bytes = _emit_bytes(proposals_model, PROPOSALS_NAME)
    if len(prop_bytes) > _opf_store.MAX_STORE_READ_BYTES:
        raise _cannot("{} is {} bytes, over the {}-byte contained store-read cap; the staged proposals "
                      "would be unreadable (rejected at the producer boundary)".format(
                          PROPOSALS_NAME, len(prop_bytes), _opf_store.MAX_STORE_READ_BYTES))
    inv_rel = run_rel + "/" + INVENTORY_NAME
    prop_rel = run_rel + "/" + PROPOSALS_NAME
    md_rel = run_rel + "/" + REPORT_MD_NAME
    content = {inv_rel: inv_bytes, prop_rel: prop_bytes, md_rel: report_md_bytes}
    ops = [
        {"op": "create", "path": inv_rel,
         "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(inv_bytes)}},
        {"op": "create", "path": prop_rel,
         "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(prop_bytes)}},
        {"op": "create", "path": md_rel,
         "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(report_md_bytes)}},
    ]

    def staged_reader(op):
        return content[op["path"]]

    try:
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} for the plan review-artefact write ({})".format(
            resolution.store_root, exc))
    try:
        _journal.apply_ops(store_root_fd, ops, staged_reader)
    except _journal.JournalError as exc:
        raise _cannot("plan review-artefact write failed ({}); the staged candidate is intact, the review "
                      "surface incomplete".format(exc))
    finally:
        os.close(store_root_fd)


def plan_import(product_root, import_set, *, proposals=None, now, run_nonce):
    """Produce a candidate mapping PLAN over a scanned import set and STAGE it under
    `.working/imports/<run-id>/` (spec 14.1), plus the review surface. This is the operation-layer plan
    step; it composes the read-only `scan_import` (the single enumeration source of truth, so a plan is
    never built over a stale inventory) with the settled `stage_import` staging primitive, then writes the
    inventory and IMPORT-REPORT.md review artefacts beside the candidate. It never mutates the active store.

    Deterministic classification (spec 14.1): in the whole-file baseline nothing is mechanically mapped, so
    every fragment classifies to `unmapped` and is preserved as a legacy_fragment quarantine record (the
    strongest no-drop posture; nothing is lost). Model `proposals` are INERT untrusted plan data: they are
    validated (confined to the scanned set, schema-bound) and recorded verbatim in IMPORT-REPORT.md as
    suggestions, and are NEVER fed to the classifier as a resting candidate state. A proposal reaches a
    resting `mapped`/`split`/`duplicate`/`ignored` state only through a later ATTRIBUTED human acceptance in
    the acceptance-capture + apply-promotion unit (a disclosed spec gap: no acceptance-capture mechanism nor
    provenance/origin tag exists in the staged plan schema today, so this build cannot enforce acceptance at
    apply and therefore does not auto-rest any proposal now). Returns a PlanResult; fail-closed throughout.

    `now`/`run_nonce` are injected (clock-read, never guessed) and, per the settled staging contract,
    compose the deterministic run id; the deterministic inventory excludes them."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return PlanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        _require_utc(now)
        _require_nonce(run_nonce)
        scan = scan_import(product_root, import_set)
        if scan.verdict != CLEAN:
            # A scan finding/cannot-evaluate is the plan's outcome: no plannable inventory, nothing staged.
            return PlanResult(scan.verdict, scan.findings)
        source_sizes = {s["path"]: s["size"] for s in scan.sources}
        proposal_rows = _validate_proposals(proposals, source_sizes)

        # Deterministic baseline plan: one whole-file fragment per source, classified `unmapped` (nothing
        # mechanically mapped). Handed to the settled staging classifier, which mints a legacy_fragment
        # quarantine record per fragment and stages the byte-canonical candidate run dir.
        plan = {"fragments": {s["path"]: [{"span": [0, s["size"]], "state": "unmapped",
                                           "origin": _BASELINE_ORIGIN}]
                              for s in scan.sources}}
        result = stage_import(product_root, import_set, plan, now=now, run_nonce=run_nonce)
        if result.verdict != CLEAN:
            return PlanResult(result.verdict, result.findings, run_id=result.run_id,
                              run_rel=result.run_rel, migration_incomplete=result.migration_incomplete)

        report_md = _render_report_md(scan.inventory_digest, scan.fragments, proposal_rows, result.run_id)
        _write_plan_artifacts(product_root, result.run_rel, result.run_id, scan.inventory,
                              report_md.encode("utf-8"), proposal_rows)
        return PlanResult(CLEAN, run_id=result.run_id, run_rel=result.run_rel,
                          inventory_digest=scan.inventory_digest,
                          report_rel=result.run_rel + "/" + REPORT_MD_NAME,
                          migration_incomplete=result.migration_incomplete)
    except _StageError as exc:
        return PlanResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        return PlanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return PlanResult(CANNOT_EVALUATE, ["fail-closed on a filesystem read error: {}".format(exc)])
    except RecursionError as exc:
        return PlanResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


# --- acceptance capture (`--review`): the attributed decision record (spec 14.1) -----------------------

def _emit_acceptance_bytes(model, where="acceptance.json"):
    """Emit an acceptance model to canonical JSON bytes: UTF-8, sorted keys, compact separators,
    ensure_ascii, plus exactly one trailing newline. Mirrors the _emit_bytes producer-boundary discipline:
    an un-serializable model or a failed round trip is CANNOT-EVALUATE, and the emitted bytes are held under
    the same contained store-read cap so nothing is staged that a reader would later refuse (fail at the
    producer, never hand a consumer an input it cannot read; guard-input-soundness)."""
    try:
        text = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise _cannot("{}: not canonical-JSON emittable ({})".format(where, exc))
    data = (text + "\n").encode("utf-8")
    if len(data) > _opf_store.MAX_STORE_READ_BYTES:
        raise _cannot("{} is {} bytes, over the {}-byte contained store-read cap; the staged acceptance "
                      "would be unreadable (rejected at the producer boundary)".format(
                          where, len(data), _opf_store.MAX_STORE_READ_BYTES))
    try:
        if json.loads(text) != model:
            raise _cannot("{}: canonical JSON did not round-trip".format(where))
    except ValueError as exc:
        raise _cannot("{}: canonical JSON did not round-trip ({})".format(where, exc))
    return data


def _validate_acceptance(model, where="acceptance.json"):
    """Validate a parsed acceptance model against the `opf.import.acceptance/v1` schema, fail-closed. Returns
    a findings list (empty when valid); the caller routes a non-empty list per its own verdict. Structural
    only: it proves the record is well-formed and internally typed, NOT that its digests bind the run (that
    is the binding check) nor that the named actor is authentic (out of scope by design, spec 14.1)."""
    f = []
    if not isinstance(model, dict):
        return ["{}: acceptance record is not an object".format(where)]
    allowed = {"format", "run_id", "plan_digest", "inventory_digest", "actor", "reviewed_at",
               "decisions", "signature"}
    extra = set(model) - allowed
    if extra:
        f.append("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    if model.get("format") != ACCEPTANCE_FORMAT:
        f.append("{}: format must be {!r}".format(where, ACCEPTANCE_FORMAT))
    if not (isinstance(model.get("run_id"), str) and _RUN_ID_RE.match(model["run_id"])):
        f.append("{}: run_id is missing or not a valid run-id".format(where))
    for dk in ("plan_digest", "inventory_digest"):
        if not (isinstance(model.get(dk), str) and _DIGEST_RE.match(model[dk])):
            f.append("{}: {} must be a 'sha256:'+64-hex digest".format(where, dk))
    actor = model.get("actor")
    if not isinstance(actor, dict):
        f.append("{}: actor must be an object".format(where))
    else:
        if set(actor) - {"declared", "context"}:
            f.append("{}: actor carries unknown key(s)".format(where))
        decl = actor.get("declared")
        if not (isinstance(decl, str) and decl.strip()):
            f.append("{}: actor.declared must be a non-empty string (the required, self-asserted "
                     "reviewer)".format(where))
        elif any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in decl):
            f.append("{}: actor.declared carries a control character".format(where))
        ctx = actor.get("context")
        if not isinstance(ctx, dict) or set(ctx) - {"os_user", "git_identity", "hostname"}:
            f.append("{}: actor.context must be an object of os_user/git_identity/hostname".format(where))
        elif not all(isinstance(ctx.get(k), str) for k in ("os_user", "git_identity", "hostname")):
            f.append("{}: actor.context fields must be strings (opportunistic, may be empty)".format(where))
    ra = model.get("reviewed_at")
    if not (isinstance(ra, str) and ra):
        f.append("{}: reviewed_at must be a non-empty RFC-3339 UTC string".format(where))
    else:
        # A well-formed RFC-3339 UTC timestamp in the producer's emitted form (YYYY-MM-DDThh:mm:ssZ): a
        # non-empty but malformed value (e.g. "garbage") is a finding, not merely a non-empty check. The
        # strict fixed-width regex (anchored \A...\Z) gates the SHAPE, rejecting non-zero-padded or oddly-
        # shaped fields ("2026-9-9T1:2:3Z") that strptime would otherwise accept; the strptime parse then
        # gates the VALUES (an impossible month/day/time). Both must hold.
        if not _RFC3339_UTC_RE.match(ra):
            f.append("{}: reviewed_at is not a well-formed RFC-3339 UTC timestamp "
                     "(YYYY-MM-DDThh:mm:ssZ)".format(where))
        else:
            try:
                datetime.datetime.strptime(ra, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                f.append("{}: reviewed_at is not a well-formed RFC-3339 UTC timestamp "
                         "(YYYY-MM-DDThh:mm:ssZ)".format(where))
    if "signature" in model and model["signature"] is not None:
        f.append("{}: signature is reserved and unused in v1 (must be absent or null)".format(where))
    decisions = model.get("decisions")
    if not isinstance(decisions, list):
        f.append("{}: decisions must be an array".format(where))
    else:
        for i, d in enumerate(decisions):
            dw = "{} decision[{}]".format(where, i)
            if not isinstance(d, dict):
                f.append("{}: not an object".format(dw)); continue
            if set(d) - {"fragment_id", "decision", "origin", "proposed_state", "note"}:
                f.append("{}: unknown key(s)".format(dw))
            if not (isinstance(d.get("fragment_id"), str) and d["fragment_id"]):
                f.append("{}: fragment_id must be a non-empty string".format(dw))
            # isinstance guards BEFORE each membership test: this validator runs over an UNTRUSTED parsed
            # acceptance.json (the gate calls it on staged bytes), so an unhashable value ([] or {}) for any
            # of these fields would otherwise raise an uncaught TypeError; a malformed value is a finding.
            if not (isinstance(d.get("decision"), str) and d["decision"] in _DECISION_VERBS):
                f.append("{}: decision must be 'accept' or 'reject'".format(dw))
            if not (isinstance(d.get("origin"), str) and d["origin"] in _ORIGIN_SET):
                f.append("{}: origin is not a mapping origin".format(dw))
            if not (isinstance(d.get("proposed_state"), str) and d["proposed_state"] in MAPPING_STATES):
                f.append("{}: proposed_state is not a mapping state".format(dw))
            if "note" in d and not isinstance(d["note"], str):
                f.append("{}: note must be a string when present".format(dw))
    return f


def _resolve_store_for_review(product_root):
    """Resolve and manifest-validate the store for a review, fail-closed (an unresolved or invalid store is
    CANNOT-EVALUATE with the init-first message; a manifest parse error likewise). Returns the resolution."""
    try:
        resolution = _opf_store.resolve_store(product_root)
        if resolution.status != _opf_store.RESOLVED:
            raise _cannot("store did not resolve ({}: {}); run `opf init` first".format(
                resolution.status, resolution.detail))
        mv = _opf_store.load_manifest(resolution)
    except ValueError as exc:
        raise _cannot("cannot parse store manifest for {!r} ({})".format(product_root, exc))
    if mv.status != _opf_store.VALID:
        raise _cannot("store manifest is not VALID ({}: {})".format(mv.status, "; ".join(mv.findings)))
    return resolution


def _load_staged_run_for_review(store_root_fd, run_rel):
    """Load a staged run's machine artefacts for review, fail-closed. A run with no report.toml is
    not-promotion-ready (CANNOT-EVALUATE); a missing or malformed run.toml/mappings.toml/inventory.toml/
    proposals.toml is a malformed run (CANNOT-EVALUATE). Confirms the run is COHERENT and promotion-ready
    before capture: report.run_id must name this run dir, report.verdict must be a Python int equal to 0
    (CLEAN, so a bool verdict cannot slip past `False == 0`) and report.promotion_ready True, run.toml and
    plan.toml must PARSE (not merely hash), run.toml must be a COHERENT run descriptor binding this dir (its
    run_id names this run, schema is SCHEMA, source is an array; not merely a non-empty table), and
    proposals.toml must bind this run (its run_id names this dir). Confirms the acceptance BINDING at source:
    report.toml's plan_digest must recompute over
    the staged plan.toml bytes, and its inventory_digest must recompute over inventory.toml's canonical
    payload, so acceptance can never bind a digest that does not match the staged artefacts. Builds the
    fragment correspondence: each inventory
    fragment (keyed by its content-inclusive fragment_id) must correspond one-to-one, by (source_path,
    span), to exactly one mapping row (the whole-file baseline plan_import produces); a run whose inventory
    fragments and mapping rows are not in 1:1 correspondence is not reviewable in v1 (CANNOT-EVALUATE, a
    disclosed residual). Returns (plan_digest, inventory_digest, frag_by_id, key_meta, ordered_fragments)
    where key_meta maps (source_path, span) -> {origin, state} and ordered_fragments is a fragment_id-sorted
    list of {fragment_id, source_path, span, origin, proposed_state}."""
    report = _read_toml(store_root_fd, run_rel + "/report.toml")
    if report is None:
        raise _cannot("staged run has no report.toml (not promotion-ready; cannot review)")
    mappings = _read_toml(store_root_fd, run_rel + "/mappings.toml")
    inventory = _read_toml(store_root_fd, run_rel + "/inventory.toml")
    proposals = _read_toml(store_root_fd, run_rel + "/proposals.toml")
    for name, val in (("mappings.toml", mappings), ("inventory.toml", inventory),
                      ("proposals.toml", proposals)):
        if val is None:
            raise _cannot("staged run is missing {} (malformed or not a plan run; cannot review)".format(
                name))
    plan_digest = report.get("plan_digest")
    inventory_digest = report.get("inventory_digest")
    if not (isinstance(plan_digest, str) and _DIGEST_RE.match(plan_digest)):
        raise _cannot("report.toml plan_digest is missing or malformed (cannot bind acceptance)")
    if not (isinstance(inventory_digest, str) and _DIGEST_RE.match(inventory_digest)):
        raise _cannot("report.toml inventory_digest is missing or malformed (cannot bind acceptance)")

    # Coherent, promotion-ready run (guard-input-soundness): review captures an acceptance that BINDS a
    # decision record to a run, so it refuses unless the run is a COHERENT, promotion-ready run matching its
    # own dir. A digest that recomputes over garbage bytes (an unparseable plan.toml whose plan_digest was
    # recomputed to match) still fails this coherence gate. report.run_id must name THIS run dir;
    # report.verdict must be 0 (CLEAN) and report.promotion_ready must be True (the promotion-ready marker);
    # the required run.toml and plan.toml must PARSE (they are parsed here, not only hashed), and run.toml
    # must be a non-empty table. Any failure is CANNOT-EVALUATE and NO acceptance is captured.
    run_id = run_rel.rsplit("/", 1)[-1]
    if report.get("run_id") != run_id:
        raise _cannot("report.toml run_id does not name this run dir (not a coherent run; cannot review)")
    # report.verdict must be a Python INT equal to CLEAN, not merely == CLEAN: Python's `False == 0` is
    # True, so a bool verdict would slip a `report.verdict = false` run past a bare `!= CLEAN` compare.
    # Require the integer type (`type(x) is int` excludes bool) so an incoherent non-int verdict cannot
    # read as promotion-ready (F3).
    if type(report.get("verdict")) is not int or report.get("verdict") != CLEAN \
            or report.get("promotion_ready") is not True:
        raise _cannot("staged run is not promotion-ready (report.toml verdict/promotion_ready; cannot "
                      "review)")
    run_tbl = _read_toml(store_root_fd, run_rel + "/run.toml")
    if run_tbl is None:
        raise _cannot("staged run is missing run.toml (malformed or not a plan run; cannot review)")
    if not isinstance(run_tbl, dict) or not run_tbl:
        raise _cannot("staged run run.toml is not a non-empty table (malformed run; cannot review)")
    # run.toml must be a COHERENT run descriptor that BINDS this dir, not merely a non-empty table: a bare
    # non-empty check let an unrelated table (e.g. `unrelated = true`) pass as a coherent run. _write_run
    # stamps the run identity as run.toml's `run_id` alongside `schema` and the `source` array, so validate
    # that identity and shape here (F3): run_id must name THIS run dir, schema must be SCHEMA, and source
    # must be an array. schema is compared strict-int (`type(x) is int`, bool excluded) for the same bool-slip
    # reason as verdict above: SCHEMA is 1, so a `schema = true` run would satisfy `True == 1` and slip a bare
    # `!= SCHEMA` compare, binding acceptance on an incoherent run descriptor (R5-F2, the class sibling of the
    # verdict guard).
    if run_tbl.get("run_id") != run_id:
        raise _cannot("staged run run.toml run_id does not name this run dir (not a coherent run "
                      "descriptor; cannot review)")
    if not (type(run_tbl.get("schema")) is int and run_tbl.get("schema") == SCHEMA) \
            or not isinstance(run_tbl.get("source"), list):
        raise _cannot("staged run run.toml is not a coherent run descriptor (schema/source shape; cannot "
                      "review)")
    # proposals.toml must BIND this run too: its run_id was previously unchecked here, so a proposals.toml
    # carrying another run's id (a foreign proposal set) bound acceptance to the wrong run (F3).
    if proposals.get("run_id") != run_id:
        raise _cannot("staged run proposals.toml run_id does not name this run dir (run binding unchecked; "
                      "cannot review)")
    # plan.toml must PARSE as TOML (parse it, not only hash it): _read_toml raises CANNOT-EVALUATE on an
    # unparseable plan.toml and returns None when absent.
    if _read_toml(store_root_fd, run_rel + "/plan.toml") is None:
        raise _cannot("staged run is missing plan.toml (malformed or not a plan run; cannot review)")

    # Recompute-and-confirm the two binding digests over the STAGED bytes (guard-input-soundness): the
    # syntax check above proves only that report.toml's digests are well-formed, not that they bind THIS
    # run. report.toml's plan_digest must recompute over the staged plan.toml bytes, and its
    # inventory_digest must recompute over inventory.toml's canonical payload (the same emission
    # _build_inventory / the inventory-digest gate use, excluding the inventory_digest field itself). Only
    # after both recomputes agree may review bind acceptance to these digests.
    try:
        plan_bytes, _pst = _journal._read_contained(store_root_fd, run_rel + "/plan.toml")
    except _journal.JournalError as exc:
        raise _cannot("staged run plan.toml is missing or unreadable ({}); cannot review".format(exc))
    if "sha256:" + _sha256_hex(plan_bytes) != plan_digest:
        raise _cannot("report plan_digest does not match the staged plan.toml")
    inv_payload = {k: v for k, v in inventory.items() if k != "inventory_digest"}
    if "sha256:" + _sha256_hex(_emit_bytes(inv_payload, "inventory")) != inventory_digest:
        raise _cannot("report inventory_digest does not match the staged inventory.toml")

    inv_frags = inventory.get("fragment")
    map_rows = mappings.get("mapping")
    if not isinstance(inv_frags, list) or not isinstance(map_rows, list):
        raise _cannot("staged run inventory.fragment or mappings.mapping is not an array (malformed run)")

    def _key(sp, span):
        return (sp, (span[0], span[1]))

    frag_by_id = {}
    inv_keys = set()
    for i, fr in enumerate(inv_frags):
        if not (isinstance(fr, dict) and isinstance(fr.get("fragment_id"), str) and fr["fragment_id"]
                and isinstance(fr.get("source_path"), str)
                and isinstance(fr.get("span"), list) and len(fr["span"]) == 2
                and all(type(x) is int for x in fr["span"])):
            raise _cannot("inventory fragment[{}] is malformed (cannot correlate for review)".format(i))
        fid = fr["fragment_id"]
        if fid in frag_by_id:
            raise _cannot("inventory carries a duplicate fragment_id {!r} (malformed run)".format(fid))
        key = _key(fr["source_path"], fr["span"])
        if key in inv_keys:
            raise _cannot("inventory carries a duplicate (source_path, span) (malformed run)")
        inv_keys.add(key)
        frag_by_id[fid] = key

    key_meta = {}
    for i, row in enumerate(map_rows):
        # isinstance(str) guards BEFORE each membership test: these rows are UNTRUSTED staged data, and an
        # unhashable value ([] or {}) for `state`/`origin` would otherwise raise an uncaught TypeError at the
        # frozenset membership test (`_ORIGIN_SET` is a frozenset); a malformed row is a located CANNOT-
        # EVALUATE, never a crash routed to a top-level backstop.
        if not (isinstance(row, dict) and isinstance(row.get("source_path"), str)
                and isinstance(row.get("span"), list) and len(row["span"]) == 2
                and all(type(x) is int for x in row["span"])
                and isinstance(row.get("state"), str) and row.get("state") in MAPPING_STATES
                and isinstance(row.get("origin"), str) and row.get("origin") in _ORIGIN_SET):
            raise _cannot("mappings row[{}] is malformed (cannot correlate for review)".format(i))
        key = _key(row["source_path"], row["span"])
        if key in key_meta:
            raise _cannot("mappings carries a duplicate (source_path, span) row (malformed run)")
        key_meta[key] = {"origin": row["origin"], "state": row["state"]}

    if inv_keys != set(key_meta):
        raise _cannot("inventory fragments and mapping rows are not in 1:1 (source_path, span) "
                      "correspondence; this run shape is not reviewable in v1 (fail-closed residual)")

    ordered = []
    for fid in sorted(frag_by_id):
        sp, span = frag_by_id[fid]
        meta = key_meta[(sp, span)]
        ordered.append({"fragment_id": fid, "source_path": sp, "span": [span[0], span[1]],
                        "origin": meta["origin"], "proposed_state": meta["state"]})
    return plan_digest, inventory_digest, frag_by_id, key_meta, ordered


def _gather_review_context(resolution):
    """Opportunistic, unauthenticated context enrichment for an acceptance record: the OS user, a
    best-effort git identity read through the hardened `_opf_observe` config-discovery idiom, and the
    hostname. Every field is best-effort and NEVER required: any failure records an empty string. This is
    corroborating detail, not an authentication claim (spec 14.1: the actor is self-asserted and the tooling
    does not authenticate it). Values are stripped of control characters and length-capped so exotic bytes
    never enter the canonical-JSON record."""
    import getpass
    import socket
    ctx = {"os_user": "", "git_identity": "", "hostname": ""}
    try:
        ctx["os_user"] = getpass.getuser()
    except Exception:  # noqa: BLE001  opportunistic: any failure degrades to the empty string
        pass
    try:
        ctx["hostname"] = socket.gethostname()
    except Exception:  # noqa: BLE001  opportunistic
        pass
    try:
        import _opf_observe   # lazy: avoids a module-top circular import through _opf_check (see the import block); INSIDE this try so a broken/absent first-party _opf_observe degrades git_identity to "" per the best-effort contract
        git = _opf_observe._git_path()
        if git:
            name = _opf_observe._run_git_config_discovery(git, resolution.store_root,
                                                          ["config", "user.name"])
            email = _opf_observe._run_git_config_discovery(git, resolution.store_root,
                                                           ["config", "user.email"])
            n = name.out.decode("utf-8", "replace").strip() if (name.completed and name.rc == 0) else ""
            e = email.out.decode("utf-8", "replace").strip() if (email.completed and email.rc == 0) else ""
            if e:
                ctx["git_identity"] = "{} <{}>".format(n, e).strip()
            elif n:
                ctx["git_identity"] = n
    except Exception:  # noqa: BLE001  opportunistic: a git read never blocks or fails a review
        pass
    for k in list(ctx):
        v = ctx[k] if isinstance(ctx[k], str) else ""
        ctx[k] = "".join(ch for ch in v if ord(ch) >= 0x20 and ord(ch) != 0x7f)[:256]
    return ctx


def _validate_review_decisions(decisions, frag_by_id, key_meta):
    """Validate the operator decision list against the run's fragments (findings = verdict 1). Each decision
    is a well-formed {fragment_id, decision, origin, proposed_state[, note]} table; exactly one decision per
    inventory fragment (a missing, unknown, or duplicate fragment_id is a finding); each decision's echoed
    origin/proposed_state must match the staged mapping row (an echo mismatch is a finding); and every
    model_proposal-origin mapping resting in a resting state requires an explicit `accept` (no blanket
    accept). Returns (findings, normalized_decisions) where the normalized decisions echo the AUTHORITATIVE
    staged origin/proposed_state (never the caller's self-report) and are sorted by fragment_id."""
    findings = []
    seen = set()
    normalized = []
    for i, d in enumerate(decisions):
        dw = "decision[{}]".format(i)
        if not isinstance(d, dict):
            findings.append("{}: not a table".format(dw)); continue
        if set(d) - {"fragment_id", "decision", "origin", "proposed_state", "note"}:
            findings.append("{}: carries an unknown key".format(dw))
        fid = d.get("fragment_id")
        if not isinstance(fid, str) or fid not in frag_by_id:
            findings.append("{}: fragment_id {!r} names no inventory fragment".format(dw, fid)); continue
        if fid in seen:
            findings.append("{}: duplicate decision for fragment {!r}".format(dw, fid)); continue
        seen.add(fid)
        verb = d.get("decision")
        # isinstance guard BEFORE the membership test: an unhashable verb ([] or {}) would otherwise raise
        # an uncaught TypeError at `verb not in _DECISION_VERBS`; a malformed verb is a finding, not a crash.
        if not isinstance(verb, str) or verb not in _DECISION_VERBS:
            findings.append("{}: decision must be 'accept' or 'reject'".format(dw)); continue
        meta = key_meta[frag_by_id[fid]]
        if d.get("origin") != meta["origin"]:
            findings.append("{}: echoed origin {!r} does not match the staged mapping origin {!r}".format(
                dw, d.get("origin"), meta["origin"]))
        if d.get("proposed_state") != meta["state"]:
            findings.append("{}: echoed proposed_state {!r} does not match the staged mapping state "
                            "{!r}".format(dw, d.get("proposed_state"), meta["state"]))
        note = d.get("note", "")
        if not isinstance(note, str):
            findings.append("{}: note must be a string when present".format(dw)); note = ""
        normalized.append({"fragment_id": fid, "decision": verb, "origin": meta["origin"],
                           "proposed_state": meta["state"], "note": note})
    for fid, key in frag_by_id.items():
        if fid not in seen:
            findings.append("fragment {!r} has no decision (every plan fragment needs an explicit "
                            "accept or reject)".format(fid))
    # Model-proposal acceptance gate (spec 14.1): a model_proposal-origin mapping resting in a resting state
    # must be explicitly accepted; a reject or an omission of it is a finding (no blanket accept).
    accepted = {n["fragment_id"] for n in normalized if n["decision"] == "accept"}
    for fid, key in frag_by_id.items():
        meta = key_meta[key]
        if meta["origin"] == _MODEL_PROPOSAL_ORIGIN and meta["state"] in _RESTING_STATES \
                and fid not in accepted:
            findings.append("fragment {!r} is a model_proposal resting in {!r} and requires an explicit "
                            "accept (no blanket acceptance of model proposals)".format(fid, meta["state"]))
    normalized.sort(key=lambda n: n["fragment_id"])
    return findings, normalized


def _stage_acceptance(resolution, run_rel, acceptance_bytes):
    """Stage acceptance.json into the run dir RECOVERABLY. The new bytes are written to a FRESH sibling temp
    file through a journaled contained create (the _write_plan_artifacts pattern: O_EXCL, no-follow,
    staged-digest verify, fsync, umask-independent mode), then a SINGLE atomic rename installs them over any
    prior acceptance.json (with a parent-dir fsync for durability). rename(2) is atomic, so a re-review's
    prior acceptance survives BYTE-INTACT through any failure before the rename (the earlier
    [remove, create] destroy-then-recreate window, where an injected mid-write failure left a 0-byte or
    absent record, is gone). A prior entry that is not a regular file is fail-closed. The temp is
    CONTENT-verified, not merely identity-checked: immediately after apply_ops its bytes are re-read
    NO-FOLLOW through the contained store handle and confirmed to hash to the intended acceptance digest (a
    regular file), and the temp PATHNAME the rename consumes is re-read NO-FOLLOW through the run-dir dir_fd
    immediately before the rename and re-confirmed to hash to that same intended digest. So a swap for a
    DIFFERENT regular file during or after apply_ops (a same-size substitution a bare st_ino/st_size check
    could miss), and a symlink or non-regular swap, are both caught fail-closed rather than installed as
    acceptance.json (F1/F2/N2). This NARROWS the substitution window to the irreducible content-verify ->
    rename gap; it does not close it. The residual same-store-staging-writer rename race is a DISCLOSED
    residual SUBSUMED by the acceptance-authenticity residual (OPF-SPEC 14.1: fabrication by any principal
    with write access to the staging area is out of gate scope; such a principal can forge acceptance.json
    directly), backstopped by apply's own independent re-validation of the promoted record. A PRE-rename
    failure leaves the prior byte-intact; a POST-rename parent-dir fsync failure leaves the NEW record
    installed with uncertain durability (it does NOT claim the prior is intact, N3). Never mutates the active
    store; keeps the staging model's DIRECT apply_ops posture (no store lock)."""
    acc_rel = run_rel + "/" + ACCEPTANCE_NAME
    # A unique sibling temp so the create's O_EXCL never collides and a stale temp from a prior crash cannot
    # be adopted; it lives in the run dir (a sibling of acceptance.json), so the rename is same-directory.
    tmp_name = "{}.tmp-{}-{}".format(ACCEPTANCE_NAME, os.getpid(), _sha256_hex(os.urandom(16))[:16])
    tmp_rel = run_rel + "/" + tmp_name
    try:
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} for the acceptance write ({})".format(
            resolution.store_root, exc))

    def _remove_temp():
        # Best-effort contained cleanup of the staged temp on a failure path; never touches acceptance.json.
        try:
            pfd2, name2 = _journal._open_parent(store_root_fd, tmp_rel)
        except OSError:
            return
        try:
            os.unlink(name2, dir_fd=pfd2)
        except OSError:
            pass
        finally:
            os.close(pfd2)

    try:
        prior = _journal._lstat_contained(store_root_fd, acc_rel)
        if prior is not None and not stat.S_ISREG(prior.st_mode):
            raise _cannot("a prior {} is not a regular file; refusing to replace it".format(ACCEPTANCE_NAME))
        # Stage the new bytes into the temp file. The prior acceptance.json is untouched at this step, so a
        # failure here (a hostile tree, an I/O error) leaves it byte-intact.
        ops = [{"op": "create", "path": tmp_rel,
                "poststate": {"kind": "file", "mode": FILE_MODE,
                              "content-sha256": _sha256_hex(acceptance_bytes)}}]
        content = {tmp_rel: acceptance_bytes}

        def staged_reader(op):
            return content[op["path"]]

        intended_digest = _sha256_hex(acceptance_bytes)
        try:
            _journal.apply_ops(store_root_fd, ops, staged_reader)
        except _journal.JournalError as exc:
            _remove_temp()
            raise _cannot("acceptance write failed ({}); the prior acceptance is intact".format(exc))
        # Content-verify the just-created temp (F1): re-read its bytes NO-FOLLOW through the contained store
        # handle (_read_contained confirms a regular file on the OPENED fd, fail-closed on a symlink/
        # non-regular swap) and confirm they hash to the intended acceptance digest. This catches a swap for
        # a DIFFERENT regular file during or after apply_ops that a bare st_ino/st_size identity check could
        # miss (a same-size substitution), never adopting the substituted bytes as acceptance.json.
        try:
            created_bytes, _created_st = _journal._read_contained(store_root_fd, tmp_rel)
        except _journal.JournalError as exc:
            _remove_temp()
            raise _cannot("the acceptance temp is missing or not a regular file after staging ({}); the "
                          "prior acceptance is intact".format(exc))
        if _sha256_hex(created_bytes) != intended_digest:
            _remove_temp()
            raise _cannot("the acceptance temp content does not match the intended acceptance bytes after "
                          "staging (a different-content substitution); the prior acceptance is intact")
        # Atomic cutover: a single rename installs the staged temp over any prior acceptance.json, contained
        # beneath the run dir's pre-opened no-follow parent handle, then fsync that directory for durability.
        try:
            pfd, acc_final = _journal._open_parent(store_root_fd, acc_rel)
        except OSError as exc:
            _remove_temp()
            raise _cannot("cannot open the run dir to install the acceptance ({})".format(exc))
        try:
            # NARROW the TOCTOU swap window (N2/F2): O_EXCL/O_NOFOLLOW protected the temp CREATE, but
            # os.rename consumes the temp PATHNAME, which a same-store writer could swap for a symlink or a
            # different regular file between create and rename, so the rename would install the wrong object
            # as acceptance.json. Immediately before the rename, re-read the temp NO-FOLLOW through the SAME
            # run-dir dir_fd the rename uses (_read_at confirms a regular file on the OPENED fd, fail-closed
            # on a symlink/non-regular swap) and re-confirm its bytes hash to the intended acceptance digest.
            # This does not CLOSE the window: the residual content-verify -> rename race is the disclosed
            # residual documented above, subsumed by the acceptance-authenticity out-of-scope residual.
            try:
                pre_bytes, _pre_st = _journal._read_at(pfd, tmp_name, tmp_rel)
            except _journal.JournalError as exc:
                _remove_temp()
                raise _cannot("cannot re-read the acceptance temp before install ({}); the prior "
                              "acceptance is intact".format(exc))
            if _sha256_hex(pre_bytes) != intended_digest:
                _remove_temp()
                raise _cannot("the acceptance temp was swapped before install (content does not match the "
                              "staged acceptance bytes); the prior acceptance is intact")
            # The rename is its own step so the parent-dir fsync failure below is NOT folded into the
            # rename-failure handler (N3): a PRE-rename failure leaves the prior byte-intact.
            try:
                os.rename(tmp_name, acc_final, src_dir_fd=pfd, dst_dir_fd=pfd)
            except OSError as exc:
                _remove_temp()
                raise _cannot("acceptance install (rename) failed ({}); the prior acceptance is "
                              "intact".format(exc))
            # POST-rename (N3): the new record is ALREADY installed and the prior is already replaced, so a
            # parent-dir fsync failure here leaves the NEW record in place with uncertain durability. It must
            # NOT claim the prior is intact, and it must NOT remove the temp (the temp pathname is now the
            # installed record).
            try:
                os.fsync(pfd)
            except OSError as exc:
                raise _cannot("acceptance installed but its directory fsync failed ({}); the new record is "
                              "in place, its durability uncertain (the prior record is already "
                              "replaced)".format(exc))
        finally:
            os.close(pfd)
    finally:
        os.close(store_root_fd)
    return acc_rel


def review_import(product_root, run_id, *, actor, decisions, now):
    """Capture an attributed acceptance record over a staged import run (spec 14.1), writing
    `acceptance.json` into the run dir. This is the batch (decisions-list) contract surface; the interactive
    front-end funnels into it. It NEVER mutates the active store and NEVER re-plans: it records accept/reject
    decisions and binds them to the exact run (run id + plan digest + inventory digest), so a regenerated
    plan (a new run) invalidates a prior acceptance by construction. A recorded reject makes a later apply
    refuse (resolution is a fresh plan carrying a human_revision mapping, never a silent re-label here).

    Fail-closed: the staged run is loaded fail-closed (missing/malformed run, or a run without report.toml,
    is CANNOT-EVALUATE); the run_id is validated against the run-id grammar as an identifier BEFORE any path
    use; a required, self-asserted `actor` string is required (its authenticity is out of scope, spec 14.1).
    The run's coherence is judged by ONE authoritative source: after the fast fail-closed pre-checks above,
    review REQUIRES the staged-run gate (check_opf_import.check_staged_run) to pass EVERY check before it
    captures acceptance; any gate FINDING is CANNOT-EVALUATE (verdict 2) naming the failing check(s) and
    writes nothing (R4-F1). Decisions are then validated for completeness (one per fragment) and echo
    cross-checked against the staged mappings/inventory, and every model_proposal resting mapping requires an
    explicit accept; any such problem is a FINDING (verdict 1) that writes nothing. Returns a ReviewResult."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        _require_utc(now)
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path string or os.PathLike, got {}".format(
                type(product_root).__name__))
        if not (isinstance(actor, str) and actor.strip()):
            raise _cannot("--actor is required: a non-empty, self-asserted reviewer identity (spec 14.1)")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in actor):
            raise _cannot("--actor carries a control character")
        if not isinstance(decisions, (list, tuple)):
            raise _cannot("decisions must be a list of decision tables")
        # Validate the run_id as an identifier BEFORE it is ever used as a path fragment (guard-input-
        # soundness): a value outside the grammar is CANNOT-EVALUATE, never a path traversal.
        if not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
            raise _cannot("run-id {!r} does not match the run-id grammar (fail-closed)".format(run_id))

        resolution = _resolve_store_for_review(product_root)
        run_rel = "{}/{}".format(IMPORTS_REL, run_id)
        try:
            store_root_fd = _opf_store._open_store_root_fd(
                resolution.store_root, resolution.pointer_source != "default")
        except OSError as exc:
            raise _cannot("cannot open store root {!r} ({})".format(resolution.store_root, exc))
        try:
            plan_digest, inventory_digest, frag_by_id, key_meta, _ordered = \
                _load_staged_run_for_review(store_root_fd, run_rel)
        finally:
            os.close(store_root_fd)

        # Single authoritative coherence source (R4-F1): the staged-run gate owns the run's structural and
        # invariant coherence, so review REQUIRES every gate check to PASS before it captures acceptance,
        # rather than hand-extending a subset of those checks here (which kept leaving gaps: a malformed
        # run.toml source record, a source path with no covering mapping, or a bad proposals schema each
        # cleared the loader's fast pre-checks yet check_staged_run diagnoses each). The fast fail-closed
        # pre-checks in _load_staged_run_for_review above run FIRST as an early defence-in-depth layer; this
        # delegation then inherits the gate's full coherence findings. check_opf_import is imported LAZILY
        # inside the function: a module-top import would form a circular import (check_opf_import imports
        # _opf_import), the same reason _gather_review_context imports _opf_observe lazily. No recursion:
        # check_staged_run runs the 16 structural checks over one run dir and never calls a module self-test.
        # States: on a first review acceptance.json is absent, so the gate's acceptance-* checks pass "not yet
        # reviewed"; on a re-review a prior valid acceptance is present and its acceptance-* checks pass. Any
        # FINDING is a CANNOT-EVALUATE naming the failing check(s), and NO acceptance is written.
        run_dir_path = os.path.join(resolution.store_root, run_rel)
        # Defence-in-depth (R5-F1, widened R7-F1): the WHOLE gate-delegation step (load + call + result-read)
        # is fail-closed to CANNOT-EVALUATE (verdict 2); it must never propagate. R5-F1 originally guarded only
        # the call; R7-F1 widens the guard to the lazy import and the result-read too, the class-siblings it
        # missed. So a broken/absent gate module (ImportError/SyntaxError on the lazy import), a raise inside
        # check_staged_run (a shape the gate does not yet guard), OR a contract violation returning a non-dict
        # (an AttributeError from gate_results.items()) each become verdict 2 rather than an uncaught
        # exception. Catch Exception only, so KeyboardInterrupt/SystemExit stay uncaught; the resulting _cannot
        # is handled by the outer _StageError branch. The import stays lazy (a module-top import would form a
        # circular import: check_opf_import imports _opf_import), now inside the guard. R8-F1 widens the guard
        # once more to the gate_findings result-read AND the `", ".join(gate_findings)` FINDING-raise: a gate
        # result carrying a NON-STRING failing-check id (a first-party contract violation) would raise an
        # uncaught TypeError at the join if it sat outside the try, escaping the "malformed gate result ->
        # verdict 2" intent. The deliberate `_cannot(...)` for a normally-failing gate is a _StageError, so it
        # is let through unwrapped (its own message preserved); only an UNEXPECTED exception (the TypeError, an
        # AttributeError, an ImportError) becomes the malformed-gate CANNOT-EVALUATE. Catch Exception only, so
        # KeyboardInterrupt/SystemExit stay uncaught.
        try:
            import check_opf_import   # lazy: avoids a module-top circular import (see _gather_review_context)
            gate_results = check_opf_import.check_staged_run(run_dir_path)
            gate_findings = sorted(cid for cid, (ok, _detail) in gate_results.items() if not ok)
            if gate_findings:
                raise _cannot("staged run fails the import-operation gate; not reviewable until it is a "
                              "coherent, promotion-ready run (failing gate checks: {})".format(
                                  ", ".join(gate_findings)))
        except _StageError:
            raise
        except Exception as exc:
            raise _cannot("the import-operation gate could not be loaded, raised, or returned a malformed "
                          "result evaluating the staged run ({!r}); cannot review".format(exc))

        findings, normalized = _validate_review_decisions(decisions, frag_by_id, key_meta)
        if findings:
            return ReviewResult(FINDING, findings, run_id=run_id)

        reviewed_at = _rfc3339(now)
        model = {
            "format": ACCEPTANCE_FORMAT, "run_id": run_id,
            "plan_digest": plan_digest, "inventory_digest": inventory_digest,
            "actor": {"declared": actor, "context": _gather_review_context(resolution)},
            "reviewed_at": reviewed_at, "decisions": normalized,
        }
        schema_findings = _validate_acceptance(model)
        if schema_findings:
            raise _cannot("internal: composed acceptance record is invalid ({})".format(
                "; ".join(schema_findings)))
        acceptance_bytes = _emit_acceptance_bytes(model)
        acc_rel = _stage_acceptance(resolution, run_rel, acceptance_bytes)
        return ReviewResult(CLEAN, run_id=run_id, acceptance_rel=acc_rel, reviewed_at=reviewed_at,
                            decisions_count=len(normalized))
    except _StageError as exc:
        return ReviewResult(exc.verdict, [exc.message], run_id=run_id if isinstance(run_id, str) else None)
    except _journal.JournalError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["fail-closed on a filesystem error: {}".format(exc)])
    except RecursionError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


def review_import_interactive(product_root, run_id, *, actor, now, in_stream=None, out_stream=None):
    """A minimal interactive review front-end: prompt accept/reject (and an optional note) per fragment over
    a TTY, then funnel the collected decisions into review_import (the batch path is the contract surface,
    so this loop is kept deliberately thin). It REFUSES to start when stdin is not a TTY (CANNOT-EVALUATE):
    an unattended or piped invocation must use the batch --decisions path, so a review is never captured
    from an ambient, non-interactive stream by accident. All validation and the write live in review_import;
    this only gathers the per-fragment verbs."""
    stdin = in_stream if in_stream is not None else sys.stdin
    stdout = out_stream if out_stream is not None else sys.stdout
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        return ReviewResult(CANNOT_EVALUATE,
                            ["--interactive requires a TTY on stdin; use --decisions <file> for a "
                             "non-interactive (batch) review (fail-closed)"],
                            run_id=run_id if isinstance(run_id, str) else None)
    try:
        _journal.require_containment()
        if not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
            return ReviewResult(CANNOT_EVALUATE,
                                ["run-id does not match the run-id grammar (fail-closed)"])
        resolution = _resolve_store_for_review(product_root)
        run_rel = "{}/{}".format(IMPORTS_REL, run_id)
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
        try:
            _pd, _id, _frag_by_id, _km, ordered = _load_staged_run_for_review(store_root_fd, run_rel)
        finally:
            os.close(store_root_fd)
    except _StageError as exc:
        return ReviewResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return ReviewResult(CANNOT_EVALUATE, ["fail-closed on a filesystem error: {}".format(exc)])

    decisions = []
    stdout.write("Reviewing import run {} ({} fragment(s)).\n".format(run_id, len(ordered)))
    for frag in ordered:
        prompt = "  {} [{}:{}] origin={} state={}: accept/reject? ".format(
            frag["source_path"], frag["span"][0], frag["span"][1], frag["origin"],
            frag["proposed_state"])
        stdout.write(prompt)
        stdout.flush()
        line = stdin.readline()
        if not line:
            return ReviewResult(CANNOT_EVALUATE, ["interactive input ended before every fragment was "
                                                  "decided (fail-closed; nothing captured)"], run_id=run_id)
        verb = line.strip().lower()
        if verb in ("a", "accept"):
            verb = "accept"
        elif verb in ("r", "reject"):
            verb = "reject"
        else:
            return ReviewResult(CANNOT_EVALUATE, ["unrecognized decision {!r} (expected accept/reject); "
                                                  "nothing captured".format(line.strip())], run_id=run_id)
        stdout.write("  note (optional, blank to skip): ")
        stdout.flush()
        note = (stdin.readline() or "").strip()
        decisions.append({"fragment_id": frag["fragment_id"], "decision": verb,
                          "origin": frag["origin"], "proposed_state": frag["proposed_state"],
                          "note": note})
    return review_import(product_root, run_id, actor=actor, decisions=decisions, now=now)


def apply_import(product_root, run_id, *, accepted_plan_digest=None, now=None):
    """APPLY-PROMOTION: validate an accepted staged run and promote its candidate atomically to the active
    store. DEFERRED to the OPF-IMPORT-APPLY unit (the Fable plan's PR2, its critical slice); this entry is a
    fail-closed CANNOT-EVALUATE that mutates NOTHING, so the reserved capability can never read as a silent
    no-op or a partial promotion (SECI-fail-closed; SECA-verified-restore-path). It is a deliberate,
    surfaced deferral (never a silent scope cut), for two reasons that must be resolved before a truthful
    promotion contract can be built:

      1. ACCEPTANCE / PROVENANCE MODEL (a spec gap requiring a maintainer design decision). Spec 14.1
         requires model proposals to be inert untrusted plan data promoted only through an ATTRIBUTED human
         acceptance. No acceptance-capture mechanism (`acceptance.json`), and no origin/provenance tag on
         the staged plan schema (its keyset is closed: {span, state, record, target, note}), exists today.
         Enforcing the acceptance rule at apply therefore requires first establishing that model (an
         `acceptance.json` contract + a plan-schema origin tag), which reshapes the settled staging schema
         and is the maintainer's to settle.

      2. CANDIDATE-CONTEXT VALIDATION. Apply must grade the fully-assembled candidate store with the U6
         `validate_store` engine and regenerate the U4 views over it BEFORE any live write, without ever
         swapping the live store to test a candidate. That candidate-context adapter is not yet built.

    The promotion MECHANISM itself is already available and composes cleanly (confirmed pre-flight): the
    crash-durable, lock-guarded cutover primitive `_journal.run_transaction` (with `acquire_lock`,
    `capture_preimages`, preimage rollback, and `recover`) delivers the single-writer, journaled,
    verified-restore publication the plan's section 1.3/3.3 describe. What remains for PR2 is the acceptance
    model, the candidate-context doctor/render adapter, the baseline-freshness binding, and the
    import_status partial->complete lifecycle flip, driven through that primitive."""
    return ApplyResult(
        CANNOT_EVALUATE,
        ["apply_import (import promotion) is deferred to the OPF-IMPORT-APPLY unit and is not yet "
         "implemented; the acceptance/provenance model (spec 14.1) and the candidate-context validation "
         "adapter are unresolved prerequisites. Fail-closed: the active store is never mutated."],
        promoted=False, outcome="aborted")


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Import-staging invariants over synthetic stores. Judged on the returned verdict values, never by
    grepping output. Fixtures live under a private tempdir removed in a finally; the injected instant and
    nonce are fixed, so ids and bytes are deterministic. Follows the U1 self-test idiom."""
    import errno
    import tempfile
    import shutil
    import subprocess

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-IMPORT SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
    NONCE = "selftest-nonce"

    # An INDEPENDENT run-id oracle: this literal re-checks that a PRODUCED run-id conforms to the expected
    # grammar, independent of the production _RUN_ID_RE. It does NOT catch a broadened _RUN_ID_RE: _run_id
    # builds the id from a fixed format string (a strftime stamp plus a 16-hex digest) that always conforms,
    # so the production regex is a belt-and-suspenders guard the public API cannot drive to reject. The
    # oracle pins the id SHAPE the finalizer will parse, nothing more.
    INDEP_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")

    def manifest_text():
        return "\n".join([
            "[opf]", 'standard = "opf"',
            'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
            'layout = "inline"', 'posture = "required"', 'import_status = "none"',
            "", "[store]", 'sync_target = ""',
            "", "[modules]", "governance = true",
            "", "[types.backlog_item]", 'namespace = "BI"',
            "", "[vendors]", 'registered = []', "",
        ]) + "\n"

    def counters_text(**ns):
        lines = ["schema = 1", "", "[counters]"]
        for k, v in ns.items():
            lines.append("{} = {}".format(k, v))
        return "\n".join(lines) + "\n"

    base = Path(tempfile.mkdtemp(prefix="opf-import-selftest-")).resolve()
    counter = [0]

    def build_store(counters="BI=0,LF=0,WL=0", sources=None, extra=None, working_extra=None):
        """A default-resolution store with a valid manifest + counters, plus optional source files, extra
        MACHINE-relative store files (`extra=` {rel-under-.working/toml: text}), and extra WORKING-relative
        store files (`working_extra=` {rel-under-.working: text}). The working-relative channel is EXPLICIT
        (never a silent reroute of `imports/`-prefixed keys) so a fixture that stages under the store-scope
        `.working/imports/` tree is written there deliberately, not inferred (OPF-IMPORTS-RELOCATE)."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        working = root / ".working"
        machine = working / "toml"
        machine.mkdir(parents=True)
        (machine / "manifest.toml").write_text(manifest_text(), encoding="utf-8")
        kv = {}
        for part in counters.split(","):
            if part:
                k, v = part.split("=")
                kv[k] = int(v)
        (machine / "counters.toml").write_text(counters_text(**kv), encoding="utf-8")
        for rel, text in (sources or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        for rel, text in (extra or {}).items():
            p = machine / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, text in (working_extra or {}).items():
            p = working / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root, machine

    def bi_candidate():
        # A source-provided candidate model. It supplies updated_at (a real source instant) but omits
        # created_at, so the importer created_at exception applies (U7 attaches a provenance ref) while
        # updated_at is NEVER fabricated (CLASS 2): a candidate lacking updated_at is a finding, tested
        # directly by the CLASS 2 sibling vector below.
        return {"type": "backlog_item", "status": "open", "title": "Imported item",
                "actor": {"kind": "importer"}, "updated_at": "2026-01-01T00:00:00Z"}

    def bi_record_text(rid="BI-1"):
        """A complete backlog_item record suitable for an index row or archived record file."""
        return ('id = "{}"\ntype = "backlog_item"\nstatus = "open"\ntitle = "x"\n'
                'created_at = "2026-01-01T00:00:00Z"\n'
                'updated_at = "2026-01-01T00:00:00Z"\n'
                'actor = {{ kind = "maintainer" }}\n').format(rid)

    def bi_index_text(rid="BI-1"):
        """A complete inline backlog_item index."""
        return "schema = 1\n\n[[record]]\n" + bi_record_text(rid)

    def plan_mapped(source_len):
        return {"fragments": {"a.txt": [
            {"span": [0, source_len], "state": "mapped", "origin": "baseline", "record": bi_candidate()}]}}

    def snapshot(machine):
        """A byte snapshot of the machine tree (`.working/toml/`) for a before/after comparison. Since the
        relocation the staging area lives at `.working/imports/` (a SIBLING of the machine subdir, never
        under it), so the machine tree is the pure ACTIVE store: a clean stage leaves it byte-identical, and
        `snapshot(machine) == before` is the strongest machine-subdir-untouched proof (F1/F11.3). A snapshot
        meant to catch an out-of-run-dir write UNDER imports must re-root at `.working` (imports is no longer
        reachable from the machine tree)."""
        snap = {}
        for p in sorted(machine.rglob("*")):
            if not p.is_file():
                continue
            snap[str(p.relative_to(machine))] = p.read_bytes()
        return snap

    try:
        # 1: positive stage (mapped + unmapped): verdict 0, run dir + files present, LF quarantine for the
        # unmapped fragment carrying all four provenance fields, active store + counters unchanged.
        src = "hello world body"
        root, machine = build_store(sources={"a.txt": src})
        before = snapshot(machine)
        rows = [{"span": [0, 5], "state": "mapped", "origin": "baseline", "record": bi_candidate()},
                {"span": [5, len(src)], "state": "unmapped", "origin": "baseline"}]
        res = stage_import(root, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce=NONCE)
        check("1-positive-clean", res.verdict == 0)
        check("1-run-id-grammar", bool(res.run_id and INDEP_RUN_ID_RE.match(res.run_id)))
        check("1-migration-incomplete", res.migration_incomplete is True)
        # OPF-IMPORTS-RELOCATE: the run stages at the STORE-scope `.working/imports/<run-id>` path, asserted
        # with an INDEPENDENT literal (never derived from the production IMPORTS_REL constant, so a wrong
        # relocation cannot satisfy its own expectation). Pre-relocation run_rel was `.working/toml/imports/
        # <run-id>`, so this literal flips red against pre-edit code.
        check("1-run-rel-new-path", res.run_rel == ".working/imports/" + (res.run_id or "MISSING"))
        run_dir = machine.parent / "imports" / (res.run_id or "MISSING")
        check("1-run-dir-exists", run_dir.is_dir())
        check("1-report-present", (run_dir / "report.toml").is_file())
        check("1-candidate-present", (run_dir / "candidate" / "backlog_item.index.toml").is_file())
        check("1-lf-present", (run_dir / "fragments" / "legacy_fragment.index.toml").is_file())
        if (run_dir / "fragments" / "legacy_fragment.index.toml").is_file():
            lf = tomllib.loads((run_dir / "fragments" / "legacy_fragment.index.toml").read_text())
            rec0 = lf["record"][0]
            check("1-lf-provenance", all(k in rec0 for k in
                  ("source_path", "source_digest", "span", "run_id", "body")))
            check("1-lf-body", rec0["body"] == src[5:])
        # B5: the source model (bi_candidate) supplies no created_at, so the staged record OMITS it (never
        # a fabricated staging-clock stamp) and carries an import-provenance ref instead (timestamp-from-
        # clock: an earlier event's instant is unknown, recorded via provenance, never guessed).
        if (run_dir / "candidate" / "backlog_item.index.toml").is_file():
            crec = tomllib.loads(
                (run_dir / "candidate" / "backlog_item.index.toml").read_text())["record"][0]
            check("1-candidate-no-fabricated-created-at", "created_at" not in crec)
            check("1-candidate-has-provenance-ref",
                  isinstance(crec.get("refs"), list) and bool(crec.get("refs")))
        # The FULL machine tree is byte-identical across a clean stage: since the relocation the machine
        # subdir carries no staging area, so this is the strongest machine-subdir-untouched proof (F1/F11.3).
        # Pre-relocation staging wrote `.working/toml/imports/<run-id>` INTO the machine tree, so this
        # full-identity assertion flips red against pre-edit code.
        check("1-store-unchanged", snapshot(machine) == before)
        check("1-no-imports-under-machine", not (machine / "imports").exists())
        # F1/F11.3: the ONLY entry under the store-scope `.working/imports/` root is this run's dir (the
        # staging root plus one run dir); nothing outside `.working/imports/<run-id>/` was written.
        imports_children = sorted(d.name for d in (machine.parent / "imports").iterdir())
        check("1-only-run-dir-under-imports", imports_children == [res.run_id])
        check("1-two-ids", len(res.staged_ids) == 2)

        # PRODUCER-BOUNDARY ceiling: a candidate that is otherwise VALID but whose emitted index would
        # exceed the CONTAINED store-read cap (_opf_store.MAX_STORE_READ_BYTES, 1 MiB) is rejected HERE,
        # before promotion, as a cannot-evaluate (verdict 2), never staged as an index the store reader
        # would later refuse (and which the post-claim sibling sweep, excluding this run, would not catch).
        # A 2 MiB single-line title passes record validation (no length cap) yet blows the emitted candidate
        # past the cap. Reverted (no producer-boundary check), staging would PASS (verdict 0) and promote an
        # unreadable record; this vector then flips red.
        big_root, _big_machine = build_store(sources={"a.txt": src})
        _big_cand = dict(bi_candidate(), title="x" * (2 * 1024 * 1024))
        _big_plan = {"fragments": {"a.txt": [
            {"span": [0, len(src)], "state": "mapped", "origin": "baseline", "record": _big_cand}]}}
        _big_res = stage_import(big_root, ["a.txt"], _big_plan, now=NOW, run_nonce=NONCE)
        check("producer-ceiling-oversized-candidate-cannot-evaluate", _big_res.verdict == 2)
        check("producer-ceiling-names-store-read-cap",
              any("store-read cap" in f for f in _big_res.findings))

        # 2: determinism: identical inputs give a byte-identical id; a different nonce gives a different id.
        root2, machine2 = build_store(sources={"a.txt": src})
        res2 = stage_import(root2, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce=NONCE)
        check("2-deterministic-id", res2.run_id == res.run_id and res2.verdict == 0)
        root3, machine3 = build_store(sources={"a.txt": src})
        res3 = stage_import(root3, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce="other")
        check("2-nonce-changes-id", res3.run_id != res.run_id and res3.verdict == 0)

        # 3: verb wiring (OPF-IMPORT-VERB PR-B, converted from the F-373 dispatch-deferral vector). The
        # `import` verb is now WIRED (opf.py `_cmd_import`), but a bare `opf.py import` with NO mode is a
        # usage error (exactly one mode required) -> exit 2 and stages nothing; and `--apply <run-id>` wires
        # onto the STILL-DEFERRED apply_import stub -> exit 2 (cannot-evaluate) and mutates nothing, so
        # PR-C's promotion landing is again a conscious edit to this vector. Both cases exercise the live
        # dispatcher through opf.py (the CLI round-trip lives in opf.py's own opf-cli self-test leg).
        opf_py = str(Path(__file__).resolve().parent / "opf.py")
        root4, machine4 = build_store(sources={"a.txt": src})
        cp_nomode = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--root", str(root4)],
                                   capture_output=True)
        check("3-verb-no-mode-exits-2", cp_nomode.returncode == 2)
        check("3-verb-no-mode-stages-nothing", not (machine4.parent / "imports").exists())
        # Stage a real run over root4, then `--apply` it: the deferred stub exits 2 and the store machine
        # tree is byte-unchanged (nothing promoted). An INDEPENDENT run-id-grammar literal for the operand.
        plan4 = plan_import(root4, ["a.txt"], now=NOW, run_nonce="verb-apply-pin")
        check("3-apply-plan-staged", plan4.verdict == 0 and bool(plan4.run_id))
        machine4_before = snapshot(machine4)
        cp_apply = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--apply",
                                   plan4.run_id or "imp-00000000T000000Z-0000000000000000",
                                   "--root", str(root4)], capture_output=True)
        check("3-apply-stub-exits-2", cp_apply.returncode == 2)
        check("3-apply-stub-mutates-nothing", snapshot(machine4) == machine4_before)

        # 4: R6 active collision: a fully-valid active index already carries the id next_id will mint (BI-1
        # above the BI=0 high-water) -> verdict 1, nothing written. The active record is now routed through
        # the complete contract (CLASS 3), so the collision is proven against a VALID store record (M11).
        root5, machine5 = build_store(sources={"a.txt": src},
                                      extra={"backlog_item.index.toml": bi_index_text()})
        res5 = stage_import(root5, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("4-active-collision-finding", res5.verdict == 1)
        check("4-active-collision-no-run", not (machine5.parent / "imports").exists())

        # 5: an archived collision is real only when the declared machine-relative destination opens and
        # carries the exact archived id.
        root6, machine6 = build_store(
            sources={"a.txt": src},
            extra={
                "archive/2026/archive.toml":
                    'moved = [{ id = "BI-1", destination = '
                    '"archive/2026/backlog_item/BI-1.toml" }]\n',
                "archive/2026/backlog_item/BI-1.toml": bi_record_text("BI-1"),
            })
        res6 = stage_import(root6, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("5-archive-collision-finding", res6.verdict == 1)

        # 6: R6 sibling collision: a pre-built sibling run carries the id -> verdict 1; an unparseable
        # sibling artefact -> verdict 2. The sibling record is a FULLY VALID backlog_item so it survives the
        # complete-contract sibling validation (B4) and its id is scanned into the union.
        sib = {"imports/imp-20260101T000000Z-0000000000000000/candidate/backlog_item.index.toml":
               bi_index_text()}
        root7, machine7 = build_store(sources={"a.txt": src}, working_extra=sib)
        res7 = stage_import(root7, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("6-sibling-collision-finding", res7.verdict == 1)
        bad_sib = {"imports/imp-20260101T000000Z-0000000000000000/candidate/backlog_item.index.toml":
                   "this is not = valid toml ["}
        root8, machine8 = build_store(sources={"a.txt": src}, working_extra=bad_sib)
        res8 = stage_import(root8, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("6-sibling-unparseable-cannot-eval", res8.verdict == 2)

        # 7: counters: a malformed high-water (a bool) -> verdict 2; a missing minting namespace -> 2.
        root9, machine9 = build_store(sources={"a.txt": src})
        (machine9 / "counters.toml").write_text("schema = 1\n\n[counters]\nBI = true\nLF = 0\nWL = 0\n",
                                                encoding="utf-8")
        res9 = stage_import(root9, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("7-bad-counter-cannot-eval", res9.verdict == 2)
        root10, machine10 = build_store(counters="LF=0,WL=0", sources={"a.txt": src})  # no BI counter
        res10 = stage_import(root10, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("7-missing-ns-cannot-eval", res10.verdict == 2)

        # 8: a plan-supplied id on a candidate -> verdict 1.
        bad = bi_candidate(); bad["id"] = "BI-9"
        root11, machine11 = build_store(sources={"a.txt": src})
        res11 = stage_import(root11, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                       "record": bad}]}}, now=NOW, run_nonce=NONCE)
        check("8-plan-supplied-id-finding", res11.verdict == 1)

        # 9: an invalid candidate (bad status for the type) -> verdict 1.
        badstat = bi_candidate(); badstat["status"] = "not-a-state"
        root12, machine12 = build_store(sources={"a.txt": src})
        res12 = stage_import(root12, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                       "record": badstat}]}}, now=NOW, run_nonce=NONCE)
        check("9-invalid-candidate-finding", res12.verdict == 1)

        # 10: tiling violations each -> verdict 1 (gap, beyond EOF, reversed); an empty source needs one
        # explicit [0, 0] row.
        root13, machine13 = build_store(sources={"a.txt": src})
        gap = [{"span": [0, 3], "state": "unmapped", "origin": "baseline"}, {"span": [5, len(src)], "state": "unmapped", "origin": "baseline"}]
        check("10-gap-finding",
              stage_import(root13, ["a.txt"], {"fragments": {"a.txt": gap}}, now=NOW, run_nonce=NONCE).verdict == 1)
        beyond = [{"span": [0, len(src) + 5], "state": "unmapped", "origin": "baseline"}]
        root14, machine14 = build_store(sources={"a.txt": src})
        check("10-beyond-eof-finding",
              stage_import(root14, ["a.txt"], {"fragments": {"a.txt": beyond}}, now=NOW, run_nonce=NONCE).verdict == 1)
        rev = [{"span": [5, 2], "state": "unmapped", "origin": "baseline"}]
        root15, machine15 = build_store(sources={"a.txt": src})
        check("10-reversed-finding",
              stage_import(root15, ["a.txt"], {"fragments": {"a.txt": rev}}, now=NOW, run_nonce=NONCE).verdict == 1)
        root16, machine16 = build_store(sources={"empty.txt": ""})
        ok_empty = {"fragments": {"empty.txt": [{"span": [0, 0], "state": "unmapped", "origin": "baseline"}]}}
        check("10-empty-source-clean",
              stage_import(root16, ["empty.txt"], ok_empty, now=NOW, run_nonce=NONCE).verdict == 0)

        # 11: containment: a '..' source and an absolute source -> verdict 2 before any open; a symlinked
        # source component -> verdict 2, link target untouched.
        root17, machine17 = build_store(sources={"a.txt": src})
        check("11-dotdot-cannot-eval",
              stage_import(root17, ["../a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        check("11-absolute-cannot-eval",
              stage_import(root17, ["/etc/passwd"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        root18, machine18 = build_store(sources={"real.txt": src})
        os.symlink("real.txt", str(root18 / "link.txt"))
        check("11-symlink-source-cannot-eval",
              stage_import(root18, ["link.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # 12: a missing declared source -> verdict 2; a directory declared as a source -> verdict 2; a
        # non-UTF-8 source -> verdict 2.
        root19, machine19 = build_store(sources={"a.txt": src})
        check("12-missing-source-cannot-eval",
              stage_import(root19, ["gone.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        (root19 / "adir").mkdir()
        check("12-dir-source-cannot-eval",
              stage_import(root19, ["adir"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        root20, machine20 = build_store(sources={"a.txt": b"\xff\xfe not utf8"})
        check("12-non-utf8-cannot-eval",
              stage_import(root20, ["a.txt"], plan_mapped(9), now=NOW, run_nonce=NONCE).verdict == 2)

        # 13: emitter boundary: a plan value outside the U8 subset (a nested array) -> verdict 2, nothing
        # staged.
        root21, machine21 = build_store(sources={"a.txt": src})
        badplan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline",
                                            "note": [[1, 2]]}]}}
        res21 = stage_import(root21, ["a.txt"], badplan, now=NOW, run_nonce=NONCE)
        check("13-emitter-boundary-cannot-eval", res21.verdict == 2)
        check("13-emitter-no-run", not (machine21.parent / "imports").exists())

        # 14: run-dir refusal: a pre-created run directory -> verdict 2, pre-existing content byte-intact.
        root22, machine22 = build_store(sources={"a.txt": src})
        pre = stage_import(root22, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        rid = pre.run_id
        shutil.rmtree(machine22.parent / "imports")
        target = machine22.parent / "imports" / rid
        target.mkdir(parents=True)
        (target / "sentinel").write_text("keep", encoding="utf-8")
        res22 = stage_import(root22, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("14-run-dir-refusal-cannot-eval", res22.verdict == 2)
        check("14-run-dir-sentinel-intact", (target / "sentinel").read_text() == "keep")

        # 15: naive/non-UTC now and a malformed nonce -> verdict 2.
        root23, machine23 = build_store(sources={"a.txt": src})
        naive = datetime.datetime(2026, 9, 9, 12, 0, 0)
        check("15-naive-now-cannot-eval",
              stage_import(root23, ["a.txt"], plan_mapped(len(src)), now=naive, run_nonce=NONCE).verdict == 2)
        check("15-bad-nonce-cannot-eval",
              stage_import(root23, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce="").verdict == 2)

        # 16: a version-ledger candidate in the plan -> verdict 2 (deferral).
        root24, machine24 = build_store(sources={"a.txt": src})
        vplan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline"}]},
                 "version": {"schema": 1}}
        check("16-version-deferral-cannot-eval",
              stage_import(root24, ["a.txt"], vplan, now=NOW, run_nonce=NONCE).verdict == 2)

        # 17: a duplicate whose target names no existing record -> verdict 1; one that resolves -> clean.
        # F11.4: the active record is a fully valid backlog_item (all envelope fields), so the "clean"
        # fixture is a genuine store, asserted with validate_record.
        valid_bi_index = bi_index_text()
        check("17-active-record-valid", _opf_schema.validate_record(
            tomllib.loads(valid_bi_index)["record"][0], expected_type="backlog_item",
            specs=_roster()).status == _opf_store.VALID)
        root25, machine25 = build_store(sources={"a.txt": src}, counters="BI=1,LF=0,WL=0",
                                        extra={"backlog_item.index.toml": valid_bi_index})
        dup_bad = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline",
                                            "target": "BI-999"}]}}
        check("17-duplicate-dangling-finding",
              stage_import(root25, ["a.txt"], dup_bad, now=NOW, run_nonce=NONCE).verdict == 1)
        dup_ok = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline",
                                           "target": "BI-1"}]}}
        check("17-duplicate-resolves-clean",
              stage_import(root25, ["a.txt"], dup_ok, now=NOW, run_nonce=NONCE).verdict == 0)

        # 18: a worklog candidate minted INTO an already-released span -> verdict 1 via the release gate;
        # minted into the unreleased tail -> clean.
        # F8: a GENUINELY valid ledger (no unknown `covers` release key; coverage_digest carries the
        # mandatory sha256:<64 hex> prefix), so 18-worklog-tail-clean is clean for the right reason.
        version_text = ('schema = 1\n\n[[release]]\nversion = "1.0.0"\ndate = "2026-01-01T00:00:00Z"\n'
                        'worklog_span = ["WL-1", "WL-5"]\n'
                        'coverage_digest = "sha256:' + ("0" * 64) + '"\n')
        check("18-ledger-valid", _opf_release.validate_version(
            tomllib.loads(version_text)).status == _opf_release.VALID)
        wl_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline"}]},
                   "worklog": [{"date": "2026-02-01T00:00:00Z", "kind": "added",
                                "summary": "imported note", "actor": {"kind": "importer"}}]}
        root26, machine26 = build_store(counters="BI=0,LF=0,WL=0", sources={"a.txt": src},
                                        extra={"version.toml": version_text})
        # WL high-water 0 -> minted WL-1, inside the released span [WL-1, WL-5]: a finding.
        resw = stage_import(root26, ["a.txt"], wl_plan, now=NOW, run_nonce=NONCE)
        check("18-worklog-into-released-finding", resw.verdict == 1)
        root27, machine27 = build_store(counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
                                        extra={"version.toml": version_text})
        # WL high-water 5 -> minted WL-6, in the unreleased tail: clean.
        resw2 = stage_import(root27, ["a.txt"], wl_plan, now=NOW, run_nonce=NONCE)
        check("18-worklog-tail-clean", resw2.verdict == 0)

        # F2: the post-claim re-check consults the SAME union helper (_uniqueness_union), excluding this
        # run's own dir (skip_run_id) so it does not self-collide on its just-written indexes. Drive the
        # helper directly (a true concurrent race is not single-process reproducible; residual disclosed in
        # the module docstring). The integration proof that skip_run_id is honoured is test 1: without it,
        # the re-check would see this run's own on-disk BI-1 twice and turn the clean stage into verdict 2.
        sib_run = "imp-20260101T000000Z-0000000000000000"
        sibx = {"imports/{}/candidate/backlog_item.index.toml".format(sib_run): bi_index_text()}
        rootA, machineA = build_store(sources={"a.txt": src}, working_extra=sibx)
        resolA = _opf_store.resolve_store(rootA)
        fdA = _opf_store._open_store_root_fd(resolA.store_root, resolA.pointer_source != "default")
        try:
            active_typesA = _active_types(fdA, resolA.machine_rel)
            hit = _opf_schema.check_unique_ids(
                _uniqueness_union(fdA, resolA.machine_rel, active_typesA, _roster(), ["BI-1"],
                                  skip_run_id=None))
            miss = _opf_schema.check_unique_ids(
                _uniqueness_union(fdA, resolA.machine_rel, active_typesA, _roster(), ["BI-1"],
                                  skip_run_id=sib_run))
        finally:
            os.close(fdA)
        check("F2-recheck-detects-on-disk-collision", bool(hit))
        check("F2-recheck-skips-own-run", not miss)

        # F3: an id whose namespace does not match the index it sits in (a BI id in a done index) is a
        # malformed store under the full active contract (CLASS 3): CANNOT-EVALUATE, never an id-only read
        # (id-namespace binds type binds index file, spec 8.1/8.2, so a legitimately-placed id is only ever
        # in its own type's index; a legitimate same-index collision is covered by test 4). Still
        # fail-closed: nothing is staged.
        rootF3, machineF3 = build_store(sources={"a.txt": src},
                                        extra={"done.index.toml": 'schema = 1\n\n[[record]]\nid = "BI-1"\n'})
        resF3 = stage_import(rootF3, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("F3-misplaced-id-cannot-eval", resF3.verdict == 2)
        check("F3-misplaced-id-no-run", not (machineF3.parent / "imports").exists())

        # F4: a SYMLINKED sibling run is CANNOT-EVALUATE (fail-closed), never silently dropped.
        realsib = base / "real-sibling"
        (realsib / "candidate").mkdir(parents=True)
        (realsib / "candidate" / "backlog_item.index.toml").write_text(
            'schema = 1\n\n[[record]]\nid = "BI-1"\ntype = "backlog_item"\nstatus = "open"\ntitle = "x"\n',
            encoding="utf-8")
        rootF4, machineF4 = build_store(sources={"a.txt": src})
        (machineF4.parent / "imports").mkdir()
        os.symlink(str(realsib), str(machineF4.parent / "imports" / sib_run))
        check("F4-symlink-sibling-cannot-eval",
              stage_import(rootF4, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # F5: a present-but-contract-malformed sibling index is CANNOT-EVALUATE, not zero records; an
        # explicit empty index (schema = 1, record = []) still reads as zero and stages clean.
        def _sib_index(text):
            return {"imports/{}/candidate/backlog_item.index.toml".format(sib_run): text}
        rootF5a, mF5a = build_store(sources={"a.txt": src}, working_extra=_sib_index("schema = 2\n"))
        check("F5-bad-schema-cannot-eval",
              stage_import(rootF5a, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootF5b, mF5b = build_store(sources={"a.txt": src}, working_extra=_sib_index("schema = 1\n"))
        check("F5-missing-record-cannot-eval",
              stage_import(rootF5b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootF5c, mF5c = build_store(sources={"a.txt": src}, working_extra=_sib_index("schema = 1\nrecord = []\n"))
        check("F5-empty-index-clean",
              stage_import(rootF5c, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 0)

        # F6: fragment bodies are extracted from RAW BYTES then decoded. Source "éX" (bytes c3 a9 58):
        # mapped [0,2] (the é), quarantine [2,3] (X) -> the staged legacy_fragment body is "X".
        eX = "éX"   # 3 UTF-8 bytes
        rootF6, mF6 = build_store(sources={"a.txt": eX})
        f6_plan = {"fragments": {"a.txt": [
            {"span": [0, 2], "state": "mapped", "origin": "baseline", "record": bi_candidate()},
            {"span": [2, 3], "state": "unmapped", "origin": "baseline"}]}}
        resF6 = stage_import(rootF6, ["a.txt"], f6_plan, now=NOW, run_nonce=NONCE)
        check("F6-byte-span-clean", resF6.verdict == 0)
        if resF6.run_id:
            lfp = mF6.parent / "imports" / resF6.run_id / "fragments" / "legacy_fragment.index.toml"
            check("F6-byte-span-body-X",
                  lfp.is_file() and tomllib.loads(lfp.read_text())["record"][0]["body"] == "X")
        # a span splitting the multi-byte é ([0,1]) is refused fail-closed (not silently emptied).
        rootF6b, mF6b = build_store(sources={"a.txt": eX})
        f6b_plan = {"fragments": {"a.txt": [
            {"span": [0, 1], "state": "unmapped", "origin": "baseline"}, {"span": [1, 3], "state": "unmapped", "origin": "baseline"}]}}
        check("F6-split-multibyte-finding",
              stage_import(rootF6b, ["a.txt"], f6b_plan, now=NOW, run_nonce=NONCE).verdict == 1)

        # F7: a FULLY mapped source's full original content is preserved in the run (spec 14.2), under
        # sources/. The marker appears in the preserved-source artefact, which pre-fix exists nowhere.
        marker = "UNIQUE-LEGACY-BODY-7f1c9a"
        rootF7, mF7 = build_store(sources={"a.txt": marker})
        resF7 = stage_import(rootF7, ["a.txt"], plan_mapped(len(marker)), now=NOW, run_nonce=NONCE)
        check("F7-fully-mapped-clean", resF7.verdict == 0)
        if resF7.run_id:
            run_dir7 = mF7.parent / "imports" / resF7.run_id
            src_dir7 = run_dir7 / "sources"
            hits = [p for p in run_dir7.rglob("*") if p.is_file() and marker.encode() in p.read_bytes()]
            check("F7-source-preserved",
                  src_dir7.is_dir() and bool(hits) and all(p.parent == src_dir7 for p in hits))

        # F8: a STRUCTURALLY INVALID ledger (unknown `covers` release key + prefix-less coverage_digest)
        # with a worklog candidate -> CANNOT-EVALUATE, even when the boundary check alone would pass.
        invalid_version_text = ('schema = 1\n\n[[release]]\nversion = "1.0.0"\n'
                                'date = "2026-01-01T00:00:00Z"\nworklog_span = ["WL-1", "WL-5"]\n'
                                'covers = "1.0.0"\ncoverage_digest = "' + ("0" * 64) + '"\n')
        rootF8, mF8 = build_store(counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
                                  extra={"version.toml": invalid_version_text})
        check("F8-invalid-ledger-cannot-eval",
              stage_import(rootF8, ["a.txt"], wl_plan, now=NOW, run_nonce=NONCE).verdict == 2)

        # F9: a `target` on a mapped/split row is contradictory -> a finding, nothing staged.
        f9_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                            "record": bi_candidate(), "target": "BI-999"}]}}
        rootF9, mF9 = build_store(sources={"a.txt": src})
        resF9 = stage_import(rootF9, ["a.txt"], f9_plan, now=NOW, run_nonce=NONCE)
        check("F9-mapped-target-finding", resF9.verdict == 1)
        check("F9-mapped-target-no-run", not (mF9.parent / "imports").exists())

        # F10: an accepted lexical internal '..' source (sub/../a.txt) -> verdict 2, NO uncaught raise.
        rootF10, mF10 = build_store(sources={"a.txt": src})
        check("F10-internal-dotdot-cannot-eval",
              stage_import(rootF10, ["sub/../a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # F11.1: apply_ops must not write THROUGH a symlink (fd-relative, no-follow). A run-id symlink to an
        # external dir is refused and the external dir is never written into. Positive-property vector: an
        # unsafe path-based writer would resolve the link and populate the target, failing this assertion.
        rootS, machineS = build_store(sources={"a.txt": src})
        rid_s = stage_import(rootS, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).run_id
        shutil.rmtree(machineS.parent / "imports")
        external = base / "external-link-target"
        external.mkdir()
        (machineS.parent / "imports").mkdir()
        os.symlink(str(external), str(machineS.parent / "imports" / rid_s))
        resS = stage_import(rootS, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("F11.1-symlink-run-dir-refused", resS.verdict == 2)
        check("F11.1-symlink-target-untouched", list(external.iterdir()) == [])

        # --- OPF-IMPORTS-RELOCATE: the fail-closed legacy-location guard ---------------------------------
        # An import-run tree at the OLD machine-subdir path `.working/toml/imports/` (a `machine`-relative
        # `extra=` fixture) is CANNOT-EVALUATE (verdict 2) with the legacy message, never silently staged at
        # the new root. Existence predicate: a valid run, a bare empty dir, and a symlink at the old path each
        # fire. Bite: pre-relocation there was NO guard and staging itself lived at the old path, so every
        # vector here flips against pre-edit production code.
        legacy_sib = {"imports/{}/candidate/backlog_item.index.toml".format(sib_run): bi_index_text()}
        rootLG, mLG = build_store(sources={"a.txt": src}, extra=legacy_sib)
        resLG = stage_import(rootLG, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("REL-legacy-run-cannot-eval", resLG.verdict == 2)
        check("REL-legacy-run-message", any("legacy location" in f for f in resLG.findings))
        check("REL-legacy-run-nothing-at-new-root", not (mLG.parent / "imports").exists())
        # a bare EMPTY machine/imports/ dir fires the existence predicate (not run-shape).
        rootLGe, mLGe = build_store(sources={"a.txt": src})
        (mLGe / "imports").mkdir()
        check("REL-legacy-empty-dir-cannot-eval",
              stage_import(rootLGe, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        # a SYMLINK at machine/imports fires too (lstat sees a present entry, never followed).
        rootLGs, mLGs = build_store(sources={"a.txt": src})
        _lg_ext = base / "legacy-symlink-target"
        _lg_ext.mkdir()
        os.symlink(str(_lg_ext), str(mLGs / "imports"))
        check("REL-legacy-symlink-cannot-eval",
              stage_import(rootLGs, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # --- B2/B3/B4/B6/B7/M2 discrimination + the worklog-ledger relaxation (M11) ----------------------

        # MINOR: a `duplicate` targeting a valid ACTIVE worklog WL-n resolves (the existence set reads
        # worklog.toml via _worklog_ids, not a nonexistent worklog.index.toml).
        wl_active = ('schema = 1\n\n[[entry]]\nid = "WL-1"\ndate = "2026-01-01T00:00:00Z"\n'
                     'kind = "added"\nsummary = "seed"\nactor = { kind = "maintainer" }\n')
        rootWL, mWL = build_store(counters="BI=0,LF=0,WL=1", sources={"a.txt": src},
                                  extra={"worklog.toml": wl_active})
        dup_wl = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline", "target": "WL-1"}]}}
        check("minor-worklog-duplicate-resolves-clean",
              stage_import(rootWL, ["a.txt"], dup_wl, now=NOW, run_nonce=NONCE).verdict == 0)

        # B3: a symlinked archive-year entry is CANNOT-EVALUATE, never silently omitted from the union.
        realyear = base / "real-archive-year"
        realyear.mkdir()
        (realyear / "archive.toml").write_text('moved = ["BI-1"]\n', encoding="utf-8")
        rootB3, mB3 = build_store(sources={"a.txt": src})
        (mB3 / "archive").mkdir()
        os.symlink(str(realyear), str(mB3 / "archive" / "2026"))
        check("B3-symlink-archive-year-cannot-eval",
              stage_import(rootB3, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # B4: a present SIBLING index must satisfy its COMPLETE record contract or be CANNOT-EVALUATE. A
        # record missing envelope fields, a record whose `type` disagrees with its file, and an unknown
        # top-level key are each verdict 2 (pre-fix each was silently accepted as an id-only read).
        def _sib_cand(text):
            return {"imports/{}/candidate/backlog_item.index.toml".format(sib_run): text}
        rootB4a, mB4a = build_store(sources={"a.txt": src},
                                    working_extra=_sib_cand('schema = 1\n\n[[record]]\nid = "BI-2"\n'
                                                            'type = "backlog_item"\nstatus = "open"\ntitle = "x"\n'))
        check("B4-sibling-missing-envelope-cannot-eval",
              stage_import(rootB4a, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootB4b, mB4b = build_store(
            sources={"a.txt": src},
            working_extra=_sib_cand(bi_index_text().replace('type = "backlog_item"', 'type = "finding"')))
        check("B4-sibling-type-disagreement-cannot-eval",
              stage_import(rootB4b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootB4c, mB4c = build_store(sources={"a.txt": src},
                                    working_extra=_sib_cand('schema = 1\nunknown_top = 1\nrecord = []\n'))
        check("B4-sibling-unknown-top-key-cannot-eval",
              stage_import(rootB4c, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        # a fully-valid sibling record is scanned (its id enters the union) and stages the non-colliding run.
        rootB4d, mB4d = build_store(sources={"a.txt": src}, working_extra=_sib_cand(bi_index_text("BI-9")))
        check("B4-valid-sibling-clean",
              stage_import(rootB4d, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 0)

        # B6: a plan row carrying a field its state does not use is a finding (untrusted plan fully
        # validated, spec 14.1): a `target` on an unmapped row, a `record` on a duplicate.
        b6a = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline", "target": "BI-1"}]}}
        rootB6, mB6 = build_store(sources={"a.txt": src})
        resB6 = stage_import(rootB6, ["a.txt"], b6a, now=NOW, run_nonce=NONCE)
        check("B6-unmapped-with-target-finding", resB6.verdict == 1)
        check("B6-unmapped-with-target-no-run", not (mB6.parent / "imports").exists())
        b6b = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline",
                                        "record": bi_candidate(), "target": "BI-1"}]}}
        rootB6b, mB6b = build_store(sources={"a.txt": src})
        check("B6-duplicate-with-record-finding",
              stage_import(rootB6b, ["a.txt"], b6b, now=NOW, run_nonce=NONCE).verdict == 1)

        # B7: a candidate linking to a NONEXISTENT record is a finding; candidate-to-candidate links are
        # unsupported (a link to an id minted only in this import set resolves nowhere in active/archive).
        link_bad = bi_candidate(); link_bad["links"] = [{"rel": "relates", "id": "BI-999"}]
        rootB7, mB7 = build_store(sources={"a.txt": src})
        resB7 = stage_import(rootB7, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                       "record": link_bad}]}}, now=NOW, run_nonce=NONCE)
        check("B7-dangling-candidate-link-finding", resB7.verdict == 1)
        check("B7-dangling-candidate-link-no-run", not (mB7.parent / "imports").exists())
        # a candidate linking to an EXISTING active record resolves clean (it mints BI-2 above BI=1).
        link_ok = bi_candidate(); link_ok["links"] = [{"rel": "relates", "id": "BI-1"}]
        rootB7b, mB7b = build_store(sources={"a.txt": src}, counters="BI=1,LF=0,WL=0",
                                    extra={"backlog_item.index.toml": bi_index_text()})
        check("B7-existing-link-clean",
              stage_import(rootB7b, ["a.txt"],
                           {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                     "record": link_ok}]}},
                           now=NOW, run_nonce=NONCE).verdict == 0)

        # M2: an OSError from the post-resolution store-root re-open (AFTER resolution and manifest
        # validation have each opened the store root: calls 1 and 2) is converted to the documented
        # verdict-2 StageResult, never an uncaught escape. Only stage_import's own re-open (call 3) is
        # failed, so resolution and manifest validation still succeed.
        rootM2, mM2 = build_store(sources={"a.txt": src})
        _saved_open = _opf_store._open_store_root_fd
        _open_calls = [0]
        def _fail_third_open(store_root, pointer):
            _open_calls[0] += 1
            if _open_calls[0] >= 3:
                raise PermissionError("simulated post-resolution store-root open failure")
            return _saved_open(store_root, pointer)
        _opf_store._open_store_root_fd = _fail_third_open
        try:
            resM2 = stage_import(rootM2, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        finally:
            _opf_store._open_store_root_fd = _saved_open
        check("M2-post-resolution-oserror-cannot-eval", resM2.verdict == 2)
        check("M2-post-resolution-oserror-no-run", not (mM2.parent / "imports").exists())

        # MINOR: an explicitly-empty worklog candidate list mints no worklog entry, so the version ledger
        # is NOT required (the release-boundary gate has no new WL number to check); the run stages clean.
        rootWLE, mWLE = build_store(sources={"a.txt": src})   # no version.toml
        wle_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline"}]}, "worklog": []}
        check("empty-worklog-no-ledger-clean",
              stage_import(rootWLE, ["a.txt"], wle_plan, now=NOW, run_nonce=NONCE).verdict == 0)

        # ============================ round-3 QA discriminating vectors ============================

        # CLASS 1 (a): a non-ENOENT OSError on a SOURCE lstat fails closed to verdict 2, never an uncaught
        # OSError escape. A >NAME_MAX (255) path component makes os.stat raise ENAMETOOLONG (not ENOENT),
        # which _journal._lstat_at now converts to a JournalError that _read_sources surfaces. Reverting
        # _lstat_at's broad-OSError branch lets the raw OSError escape stage_import (caught here as
        # "escaped"), so the check fails.
        rootC1, mC1 = build_store(sources={"a.txt": src})
        longname = "z" * 300
        try:
            vC1 = stage_import(rootC1, [longname], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict
        except OSError:
            vC1 = "escaped"
        check("C1-source-nonenoent-oserror-cannot-eval", vC1 == 2)

        # CLASS 1 (b): the STORE-read path fails closed on a non-ENOENT OSError too. _read_toml is driven
        # directly with an over-NAME_MAX store-relative path: the lstat inside _opf_store._read_toml_contained
        # raises ENAMETOOLONG, converted at the root to a JournalError and to CANNOT-EVALUATE by _read_toml.
        # Reverting _lstat_at's branch (raw OSError) OR _read_toml's JournalError catch lets it escape.
        rootC1b, mC1b = build_store(sources={"a.txt": src})
        resolC1b = _opf_store.resolve_store(rootC1b)
        fdC1b = _opf_store._open_store_root_fd(resolC1b.store_root, resolC1b.pointer_source != "default")
        try:
            long_rel = "{}/{}".format(resolC1b.machine_rel, "z" * 300)
            try:
                _read_toml(fdC1b, long_rel)
                vC1b = "no-raise"
            except _StageError as exc:
                vC1b = exc.verdict
            except (OSError, _journal.JournalError):
                vC1b = "escaped"
        finally:
            os.close(fdC1b)
        check("C1-store-read-nonenoent-oserror-cannot-eval", vC1b == 2)

        # CLASS 2 (a): a worklog candidate with no `date` is REFUSED (verdict 1), never stamped from the
        # staging clock. Reverting the setdefault("date", stamp) fabrication would stage it clean.
        rootC2, mC2 = build_store(counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
                                  extra={"version.toml": version_text})
        wl_nodate = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline"}]},
                     "worklog": [{"kind": "added", "summary": "imported note",
                                  "actor": {"kind": "importer"}}]}
        check("C2-worklog-no-date-finding",
              stage_import(rootC2, ["a.txt"], wl_nodate, now=NOW, run_nonce=NONCE).verdict == 1)

        # CLASS 2 (b): a mapped candidate with no updated_at is REFUSED (updated_at is required with no
        # importer exception), never stamped. Reverting setdefault("updated_at", stamp) would stage it.
        cand_no_upd = {"type": "backlog_item", "status": "open", "title": "Imported item",
                       "actor": {"kind": "importer"}}   # no created_at AND no updated_at
        rootC2b, mC2b = build_store(sources={"a.txt": src})
        check("C2-candidate-no-updated-at-finding",
              stage_import(rootC2b, ["a.txt"],
                           {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                     "record": cand_no_upd}]}},
                           now=NOW, run_nonce=NONCE).verdict == 1)

        # CLASS 3 (b): a present-but-malformed ACTIVE record is CANNOT-EVALUATE, never a resolvable
        # duplicate/link target. A backlog_item with a bad status is invalid; a duplicate targeting it fails
        # closed. Pre-fix the structural id-only read seated it as a resolvable target (verdict 0).
        bad_active = ('schema = 1\n\n[[record]]\nid = "BI-1"\ntype = "backlog_item"\n'
                      'status = "not-a-state"\ntitle = "x"\ncreated_at = "2026-01-01T00:00:00Z"\n'
                      'updated_at = "2026-01-01T00:00:00Z"\nactor = { kind = "maintainer" }\n')
        rootC3b, mC3b = build_store(sources={"a.txt": src}, counters="BI=1,LF=0,WL=0",
                                    extra={"backlog_item.index.toml": bad_active})
        dup_bad_active = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline",
                                                   "target": "BI-1"}]}}
        check("C3-malformed-active-target-cannot-eval",
              stage_import(rootC3b, ["a.txt"], dup_bad_active, now=NOW, run_nonce=NONCE).verdict == 2)

        # CLASS 3 (c): a malformed archive-year directory name is a phantom partition, CANNOT-EVALUATE.
        # Pre-fix "notayear" was enumerated and its moved BI-1 collided with the minted BI-1 (verdict 1).
        rootC3c, mC3c = build_store(sources={"a.txt": src},
                                    extra={"archive/notayear/archive.toml": 'moved = ["BI-1"]\n'})
        check("C3-malformed-archive-year-cannot-eval",
              stage_import(rootC3c, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        # a phantom top-level key in an archive.toml is likewise CANNOT-EVALUATE (closed keyset).
        rootC3d, mC3d = build_store(sources={"a.txt": src},
                                    extra={"archive/2025/archive.toml": 'moved = ["BI-1"]\nphantom = 1\n'})
        check("C3-archive-unknown-key-cannot-eval",
              stage_import(rootC3d, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # CLASS 4: a wrong-typed but EMITTABLE note (an int) is a finding, never staged as promotion-ready.
        # Reverting the note type check stages it clean (the int emits fine, unlike test 13's nested array).
        c4_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline", "note": 5}]}}
        rootC4, mC4 = build_store(sources={"a.txt": src})
        resC4 = stage_import(rootC4, ["a.txt"], c4_plan, now=NOW, run_nonce=NONCE)
        check("C4-wrong-typed-note-finding", resC4.verdict == 1)
        check("C4-wrong-typed-note-no-run", not (mC4.parent / "imports").exists())

        # CLASS 5 (isolation contract guard): the staged candidate is a DEEP, independent copy of the plan
        # model, so it cannot share a mutable with the staged plan snapshot, and staging treats the caller's
        # plan as read-only. This probe locks the observable isolation contract: the candidate carries the
        # minted id + provenance enrichment while plan.toml preserves the ORIGINAL model, and the caller's
        # plan object is unchanged. (Honest scope note: because on-disk artefacts are frozen at emit and the
        # current code only replaces top-level keys, deep-vs-shallow copy has no black-box-observable
        # divergence today; this vector guards the contract rather than discriminating the copy depth.)
        c5_rec = {"type": "backlog_item", "status": "open", "title": "Imported item",
                  "actor": {"kind": "importer"}, "updated_at": "2026-01-01T00:00:00Z",
                  "refs": [{"kind": "url", "locator": "https://example.invalid/x", "note": "orig"}]}
        c5_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline", "record": c5_rec}]}}
        c5_before = copy.deepcopy(c5_plan)
        rootC5, mC5 = build_store(sources={"a.txt": src})
        resC5 = stage_import(rootC5, ["a.txt"], c5_plan, now=NOW, run_nonce=NONCE)
        check("C5-isolation-clean", resC5.verdict == 0)
        check("C5-input-plan-read-only", c5_plan == c5_before)
        if resC5.run_id:
            c5_run = mC5.parent / "imports" / resC5.run_id
            staged_cand = tomllib.loads(
                (c5_run / "candidate" / "backlog_item.index.toml").read_text())["record"][0]
            staged_plan_rec = tomllib.loads(
                (c5_run / "plan.toml").read_text())["fragments"]["a.txt"][0]["record"]
            check("C5-candidate-enriched",
                  bool(staged_cand.get("id")) and len(staged_cand.get("refs", [])) == 2)
            check("C5-plan-snapshot-original",
                  "id" not in staged_plan_rec
                  and staged_plan_rec.get("refs") == c5_before["fragments"]["a.txt"][0]["record"]["refs"])

        # ======================= round-4 QA discriminating vectors =======================

        # CLASS 1 (os.read): _journal._read_fd converts a read-time OSError to a JournalError at the one choke
        # point every contained reader routes bytes through. Drive it directly with a patched os.read.
        rpipe, wpipe = os.pipe()
        _saved_osread = os.read
        os.read = (lambda fd, n: (_ for _ in ()).throw(OSError(errno.EIO, "simulated read error")))
        try:
            try:
                _journal._read_fd(rpipe)
                vread = "no-raise"
            except _journal.JournalError:
                vread = "journal-error"
            except OSError:
                vread = "raw-oserror"
        finally:
            os.read = _saved_osread
            os.close(rpipe); os.close(wpipe)
        check("C1-osread-converts-to-journalerror", vread == "journal-error")

        # CLASS 1 (os.listdir on imports/ and archive/): both directory enumerators, and _list_contained,
        # convert an os.listdir OSError to the module's fail-closed CANNOT-EVALUATE (_StageError verdict 2),
        # never an empty listing. Drive each directly with a patched os.listdir over real store dirs.
        rootL, mL = build_store(sources={"a.txt": src})
        # imports is store-scope (`.working/imports`), archive stays machine-rooted (`.working/toml/archive`).
        (mL.parent / "imports").mkdir(); (mL / "archive").mkdir()
        resolL = _opf_store.resolve_store(rootL)
        fdL = _opf_store._open_store_root_fd(resolL.store_root, resolL.pointer_source != "default")
        _saved_listdir = os.listdir
        imports_probe_rel = "{}/imports".format(_opf_store.WORKING_DIRNAME)   # ".working/imports" (store scope)
        archive_probe_rel = "{}/archive".format(resolL.machine_rel)           # archive stays machine-rooted
        def _probe_listdir(rel):
            try:
                _dir_entries_no_symlink(fdL, rel)
                return "no-raise"
            except _StageError as exc:
                return exc.verdict
            except OSError:
                return "escaped"
        try:
            os.listdir = (lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "simulated listdir error")))
            v_imp, v_arc = _probe_listdir(imports_probe_rel), _probe_listdir(archive_probe_rel)
            try:
                _list_contained(fdL, imports_probe_rel)
                v_lc = "no-raise"
            except _StageError as exc:
                v_lc = exc.verdict
            except OSError:
                v_lc = "escaped"
        finally:
            os.listdir = _saved_listdir
            os.close(fdL)
        check("C1-listdir-imports-cannot-eval", v_imp == 2)
        check("C1-listdir-archive-cannot-eval", v_arc == 2)
        check("C1-listdir-list-contained-cannot-eval", v_lc == 2)

        # CLASS 1 (boundary backstop): a RAW OSError from a read path NOT converted at its own site (here a
        # patched _journal._read_contained, whose OSError bypasses _read_sources' JournalError-only catch) is
        # caught by stage_import's top-level OSError backstop as verdict 2, never an uncaught escape.
        # Reverting the backstop lets the raw OSError escape (caught here as "escaped").
        rootBK, mBK = build_store(sources={"a.txt": src})
        _saved_rc = _journal._read_contained
        _journal._read_contained = (lambda root_fd, relpath, **_kw:
                                    (_ for _ in ()).throw(OSError(errno.EIO, "simulated raw read-path error")))
        try:
            try:
                vbk = stage_import(rootBK, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict
            except OSError:
                vbk = "escaped"
        finally:
            _journal._read_contained = _saved_rc
        check("C1-boundary-backstop-cannot-eval", vbk == 2)
        check("C1-boundary-backstop-no-run", not (mBK.parent / "imports").exists())

        # CLASS 3 (empty/unsupported sibling index): an index whose TYPE is unsupported is CANNOT-EVALUATE even
        # when its record list is EMPTY (an empty or unsupported index is never zero records). Pre-fix an empty
        # record list skipped the per-record type check, so the unsupported type slipped past as clean.
        sib_unsup = {"imports/{}/candidate/not_a_type.index.toml".format(sib_run): "schema = 1\nrecord = []\n"}
        rootC3u, mC3u = build_store(sources={"a.txt": src}, working_extra=sib_unsup)
        check("C3-empty-unsupported-index-cannot-eval",
              stage_import(rootC3u, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # CLASS 3 (active worklog unknown top-level key): the active worklog reader routes through
        # _opf_release.validate_worklog, whose closed {schema, entry} keyset rejects an unknown top-level key
        # as CANNOT-EVALUATE. Pre-fix the hand-rolled reader ignored unknown top-level keys and read ids anyway.
        wl_unknown = ('schema = 1\nrogue_top = 1\n\n[[entry]]\nid = "WL-1"\ndate = "2026-01-01T00:00:00Z"\n'
                      'kind = "added"\nsummary = "seed"\nactor = { kind = "maintainer" }\n')
        rootC3w, mC3w = build_store(counters="BI=0,LF=0,WL=1", sources={"a.txt": src},
                                    extra={"worklog.toml": wl_unknown})
        check("C3-worklog-unknown-key-cannot-eval",
              stage_import(rootC3w, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # CLASS 3 (phantom archive target): a moved id whose namespace is bound to NO record type is a phantom
        # archive entry; the existence authority never resolves a duplicate against it. Pre-fix the shape-only
        # check seated "ZZ-1" as an existing target (verdict 0); post-fix the phantom fails closed (verdict 2).
        rootC3p, mC3p = build_store(sources={"a.txt": src},
                                    extra={"archive/2026/archive.toml":
                                           'moved = [{ id = "ZZ-1", destination = '
                                           '"archive/2026/x/ZZ-1.toml" }]\n'})
        dup_phantom = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "origin": "baseline",
                                                "target": "ZZ-1"}]}}
        check("C3-phantom-archive-target-cannot-eval",
              stage_import(rootC3p, ["a.txt"], dup_phantom, now=NOW, run_nonce=NONCE).verdict == 2)

        # CLASS 6 (record-level source provenance): a FULLY-timestamped mapped candidate (both created_at and
        # updated_at supplied, so no importer omission) STILL carries a record-level source-provenance ref
        # (source path + content digest + span + run id). Pre-fix a candidate that supplied its timestamps got
        # NO ref (provenance was attached only on a created_at omission).
        cand_full = {"type": "backlog_item", "status": "open", "title": "Imported item",
                     "actor": {"kind": "importer"}, "created_at": "2026-01-01T00:00:00Z",
                     "updated_at": "2026-01-02T00:00:00Z"}
        rootC6, mC6 = build_store(sources={"a.txt": src})
        resC6 = stage_import(rootC6, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                       "record": cand_full}]}}, now=NOW, run_nonce=NONCE)
        check("C6-fully-timestamped-clean", resC6.verdict == 0)
        if resC6.run_id:
            c6rec = tomllib.loads((mC6.parent / "imports" / resC6.run_id / "candidate"
                                   / "backlog_item.index.toml").read_text())["record"][0]
            src_digest = _sha256_hex(src.encode("utf-8"))
            check("C6-candidate-provenance-present",
                  isinstance(c6rec.get("refs"), list) and any(
                      isinstance(rf, dict) and rf.get("kind") == "path"
                      and src_digest in str(rf.get("note", ""))
                      and "imported fragment" in str(rf.get("note", "")) for rf in c6rec["refs"]))

        # MINOR (a): a manifest-registered x-<vendor> extension on an imported candidate is ACCEPTED (the
        # candidate- and worklog-minting validate_record calls now receive the manifest's registered vendor
        # set). Pre-fix the empty default false-rejected it (verdict 1). The SAME extension WITHOUT
        # registration is rejected, proving the allow-set is enforced rather than ignored.
        cand_vendor = {"type": "backlog_item", "status": "open", "title": "Imported item",
                       "actor": {"kind": "importer"}, "updated_at": "2026-01-01T00:00:00Z",
                       "x-acme": {"ticket": "ACME-1"}}
        cand_vendor_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                     "record": cand_vendor}]}}
        rootMV, mMV = build_store(sources={"a.txt": src})
        (mMV / "manifest.toml").write_text(
            manifest_text().replace("registered = []", 'registered = ["x-acme"]'), encoding="utf-8")
        check("minor-a-registered-vendor-accepted",
              stage_import(rootMV, ["a.txt"], cand_vendor_plan, now=NOW, run_nonce=NONCE).verdict == 0)
        rootMVn, mMVn = build_store(sources={"a.txt": src})
        check("minor-a-unregistered-vendor-rejected",
              stage_import(rootMVn, ["a.txt"], cand_vendor_plan, now=NOW, run_nonce=NONCE).verdict == 1)

        # MINOR (b): the candidate/worklog mint sites use copy.deepcopy, so a NESTED field of the staged record
        # and of the plan model are independent objects; a shallow copy would share them. This discriminates
        # deep vs shallow on the copy primitive the mint sites rely on: mutating a nested field of the copy
        # never shows through to the source, and vice-versa (a shallow copy fails BOTH directions). (Scope
        # note, kept honest: because the production mint path replaces only TOP-LEVEL keys on the copy, deep-
        # vs-shallow has no black-box divergence through stage_import today; this pins the copy-depth contract
        # the sites depend on so a later nested mutation cannot silently bleed across the plan/candidate
        # boundary. The C5 vector above continues to lock the on-disk enrichment/snapshot separation.)
        nested_model = {"type": "backlog_item", "status": "open", "title": "t",
                        "actor": {"kind": "importer"}, "updated_at": "2026-01-01T00:00:00Z",
                        "refs": [{"kind": "url", "locator": "https://example.invalid/x", "note": "orig"}]}
        deep_fwd = copy.deepcopy(nested_model)
        deep_fwd["actor"]["kind"] = "maintainer"
        deep_fwd["refs"][0]["note"] = "MUTATED-COPY"
        check("minor-b-deep-copy-forward-independent",
              nested_model["actor"]["kind"] == "importer" and nested_model["refs"][0]["note"] == "orig")
        deep_rev = copy.deepcopy(nested_model)
        nested_model["actor"]["kind"] = "assistant"
        nested_model["refs"][0]["note"] = "MUTATED-SOURCE"
        check("minor-b-deep-copy-reverse-independent",
              deep_rev["actor"]["kind"] == "importer" and deep_rev["refs"][0]["note"] == "orig")

        # ======================= round-7 QA discriminating vectors =======================

        # B1: any non-empty active or sibling module-tier index is refused because U7 has no complete
        # module schemas. A base-tier-only store still stages. The active refusal must occur before any
        # ordinary active index/worklog/archive reader is reached.
        module_index = (
            'schema = 1\n\n[[record]]\nid = "MA-9"\ntype = "maintainer_action"\n'
            'status = "open"\ntitle = "x"\ncreated_at = "2026-01-01T00:00:00Z"\n'
            'updated_at = "2026-01-01T00:00:00Z"\nactor = { kind = "maintainer" }\n')
        rootB1a, mB1a = build_store(
            sources={"a.txt": src},
            extra={"maintainer_action.index.toml": module_index})
        sibling_module = {
            "imports/{}/candidate/maintainer_action.index.toml".format(sib_run): module_index
        }
        rootB1s, mB1s = build_store(sources={"a.txt": src}, working_extra=sibling_module)
        rootB1b, mB1b = build_store(sources={"a.txt": src})
        rootB1p, mB1p = build_store(sources={"a.txt": src})
        (mB1p / "manifest.toml").write_text(
            manifest_text().replace('layout = "inline"', 'layout = "per-record"'),
            encoding="utf-8")

        _b1_mod = sys.modules[__name__]
        _b1_saved_readers = {
            "_index_ids": _b1_mod._index_ids,
            "_worklog_ids": _b1_mod._worklog_ids,
            "_archive_ids": _b1_mod._archive_ids,
        }
        _b1_reader_hits = []

        def _b1_spy(name, func):
            def _wrapped(*args, **kwargs):
                _b1_reader_hits.append(name)
                return func(*args, **kwargs)
            return _wrapped

        for _reader_name, _reader_func in _b1_saved_readers.items():
            setattr(_b1_mod, _reader_name, _b1_spy(_reader_name, _reader_func))
        try:
            resB1a = stage_import(
                rootB1a, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
            resB1p = stage_import(
                rootB1p, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        finally:
            for _reader_name, _reader_func in _b1_saved_readers.items():
                setattr(_b1_mod, _reader_name, _reader_func)

        check("B1-active-module-record-cannot-eval", resB1a.verdict == 2)
        check("B1-active-module-refusal-is-explicit",
              any(_MODULE_SCHEMA_REFUSAL in finding for finding in resB1a.findings))
        check("B1-module-and-per-record-refuse-before-active-readers",
              resB1p.verdict == 2 and _b1_reader_hits == [])
        check("B1-sibling-module-record-cannot-eval",
              stage_import(rootB1s, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)
        check("B1-base-tier-store-stages",
              stage_import(rootB1b, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 0)

        # B2: U7's inline active-store readers stage ONLY an `inline`-layout store; a `per-record`-layout
        # store is CANNOT-EVALUATE, fail-closed, before any active index is read. Pre-fix the layout was
        # never consulted, so a per-record store staged clean (verdict 0) while the inline reader silently
        # MISSED the per-record ids.
        def _per_record_manifest():
            return manifest_text().replace('layout = "inline"', 'layout = "per-record"')
        rootB2p, mB2p = build_store(sources={"a.txt": src})
        (mB2p / "manifest.toml").write_text(_per_record_manifest(), encoding="utf-8")
        check("B2-per-record-layout-cannot-eval",
              stage_import(rootB2p, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        # the hazard made concrete: a per-record store with a real record file under <type>/ that the inline
        # reader cannot see. Pre-fix U7 mints BI-1 and never sees the existing per-record BI-1 -> a colliding
        # id silently admitted (verdict 0); post-fix the store is fail-closed (verdict 2).
        rootB2h, mB2h = build_store(sources={"a.txt": src})
        (mB2h / "manifest.toml").write_text(_per_record_manifest(), encoding="utf-8")
        (mB2h / "backlog_item").mkdir()
        (mB2h / "backlog_item" / "BI-1.toml").write_text(
            'id = "BI-1"\ntype = "backlog_item"\nstatus = "open"\ntitle = "x"\n', encoding="utf-8")
        check("B2-per-record-hidden-collision-cannot-eval",
              stage_import(rootB2h, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # B3: record archive entries require a non-empty moved list, a destination, and an opened
        # destination payload carrying the exact id. Worklog-span archives are explicitly refused until
        # their on-disk schema releases.
        arc_path = "archive/2026/backlog_item/BI-1.toml"
        arc_entry = (
            'moved = [{ id = "BI-1", destination = "'
            + arc_path + '" }]\n')

        rootB3n, mB3n = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={"archive/2026/archive.toml": 'moved = ["BI-1"]\n'})
        check("B3-archive-no-destination-cannot-eval",
              stage_import(rootB3n, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        rootB3e, mB3e = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={"archive/2026/archive.toml": "moved = []\n"})
        check("B3-archive-empty-moved-cannot-eval",
              stage_import(rootB3e, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        rootB3missing, mB3missing = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={"archive/2026/archive.toml": arc_entry})
        check("B3-archive-missing-destination-cannot-eval",
              stage_import(rootB3missing, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        rootB3mismatch, mB3mismatch = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={
                "archive/2026/archive.toml": arc_entry,
                arc_path: bi_record_text("BI-2"),
            })
        check("B3-archive-destination-id-mismatch-cannot-eval",
              stage_import(rootB3mismatch, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        rootB3span, mB3span = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=5",
            extra={"archive/2026/archive.toml":
                   'moved = [{ worklog_span = ["WL-1", "WL-5"], '
                   'destination = "archive/2026/worklog.toml" }]\n'})
        resB3span = stage_import(
            rootB3span, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("B3-worklog-span-explicitly-refused",
              resB3span.verdict == 2
              and any("worklog-span archive entry" in finding
                      for finding in resB3span.findings))

        archive_payload = {
            "archive/2026/archive.toml": arc_entry,
            arc_path: bi_record_text("BI-1"),
        }
        rootB3c, mB3c = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra=archive_payload)
        check("B3-archive-real-destination-clean",
              stage_import(rootB3c, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 0)

        rootB3x, mB3x = build_store(sources={"a.txt": src}, extra=archive_payload)
        check("B3-archive-real-destination-collision-finding",
              stage_import(rootB3x, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 1)

        # Guard-input-soundness: an archived destination whose record `type` is a non-hashable TOML value
        # (type = []) must be CANNOT-EVALUATE, never an uncaught TypeError from roster.get([]) /
        # `[] in MODULE_TYPES` (codex's escape). An archived INDEX whose first record OMITS `type` must
        # likewise be CANNOT-EVALUATE, never a typeless record seated unvalidated (claude's module bypass,
        # and the broader baseline bypass). The non-hashable `type = []` is written as inline TOML text (the
        # fixture writer emits raw text verbatim), so the exotic value is cleanly constructible.
        rootB3nh, mB3nh = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={
                "archive/2026/archive.toml": arc_entry,
                arc_path: 'id = "BI-1"\ntype = []\n',
            })
        check("B3-archive-nonhashable-type-cannot-eval",
              stage_import(rootB3nh, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        ma_arc_path = "archive/2026/maintainer_action/MA-1.toml"
        rootB3tm, mB3tm = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={
                "archive/2026/archive.toml":
                    'moved = [{ id = "MA-1", destination = "' + ma_arc_path + '" }]\n',
                ma_arc_path: 'schema = 1\n\n[[record]]\nid = "MA-1"\n',
            })
        check("B3-archive-index-typeless-module-cannot-eval",
              stage_import(rootB3tm, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        fn_arc_path = "archive/2026/finding/FN-1.toml"
        rootB3tb, mB3tb = build_store(
            sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
            extra={
                "archive/2026/archive.toml":
                    'moved = [{ id = "FN-1", destination = "' + fn_arc_path + '" }]\n',
                fn_arc_path: 'schema = 1\n\n[[record]]\nid = "FN-1"\n',
            })
        check("B3-archive-index-typeless-baseline-cannot-eval",
              stage_import(rootB3tb, ["a.txt"], plan_mapped(len(src)),
                           now=NOW, run_nonce=NONCE).verdict == 2)

        # M-a: the record-level source-provenance ref's COMPONENTS are discriminated exactly (kind, locator,
        # and the note's span + source path + content digest + run id). A missing or wrong component fails
        # this exact match. (Reuses C6's fully-timestamped candidate: span [0, len(src)], source a.txt.)
        rootMa, mMa = build_store(sources={"a.txt": src})
        cand_full_a = {"type": "backlog_item", "status": "open", "title": "Imported item",
                       "actor": {"kind": "importer"}, "created_at": "2026-01-01T00:00:00Z",
                       "updated_at": "2026-01-02T00:00:00Z"}
        resMa = stage_import(rootMa, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                                       "record": cand_full_a}]}}, now=NOW, run_nonce=NONCE)
        check("M-a-clean", resMa.verdict == 0)
        if resMa.run_id:
            ma_rec = tomllib.loads((mMa.parent / "imports" / resMa.run_id / "candidate"
                                    / "backlog_item.index.toml").read_text())["record"][0]
            ma_digest = _sha256_hex(src.encode("utf-8"))
            ma_expected_note = "imported fragment [0:{}] of a.txt (sha256:{}) in run {}".format(
                len(src), ma_digest, resMa.run_id)
            ma_paths = [rf for rf in ma_rec.get("refs", [])
                        if isinstance(rf, dict) and rf.get("kind") == "path"]
            check("M-a-provenance-components-exact",
                  len(ma_paths) == 1 and ma_paths[0].get("locator") == "a.txt"
                  and ma_paths[0].get("note") == ma_expected_note)

        # M-b: the manifest's registered x-<vendor> allow-set is threaded to the WORKLOG mint site too. A
        # worklog candidate carrying x-acme: registered -> accepted (verdict 0), unregistered -> rejected
        # (verdict 1). Reverting `registered_vendors=registered_vendors` at the worklog mint call would
        # false-reject the registered case (verdict 1), failing M-b-worklog-registered-vendor-accepted.
        wl_vendor = {"date": "2026-02-01T00:00:00Z", "kind": "added", "summary": "imported note",
                     "actor": {"kind": "importer"}, "x-acme": {"ticket": "ACME-1"}}
        wl_vendor_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "origin": "baseline"}]},
                          "worklog": [wl_vendor]}
        rootMbR, mMbR = build_store(counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
                                    extra={"version.toml": version_text})
        (mMbR / "manifest.toml").write_text(
            manifest_text().replace("registered = []", 'registered = ["x-acme"]'), encoding="utf-8")
        check("M-b-worklog-registered-vendor-accepted",
              stage_import(rootMbR, ["a.txt"], wl_vendor_plan, now=NOW, run_nonce=NONCE).verdict == 0)
        rootMbN, mMbN = build_store(counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
                                    extra={"version.toml": version_text})
        check("M-b-worklog-unregistered-vendor-rejected",
              stage_import(rootMbN, ["a.txt"], wl_vendor_plan, now=NOW, run_nonce=NONCE).verdict == 1)

        # M-c: capture each production deepcopy RESULT. Because stage_import enriches that same result
        # after deepcopy returns, compare it after staging to the parsed candidate/worklog record, including
        # the minted id and candidate provenance. Then mutate each source model to prove nested separation.
        mc_cand = {"type": "backlog_item", "status": "open", "title": "t",
                   "actor": {"kind": "importer"}, "updated_at": "2026-01-01T00:00:00Z"}
        mc_wl = {"date": "2026-02-01T00:00:00Z", "kind": "added", "summary": "n",
                 "actor": {"kind": "importer"}}
        mc_plan = {
            "fragments": {
                "a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline", "record": mc_cand}]
            },
            "worklog": [mc_wl],
        }
        rootMc, mMc = build_store(
            counters="BI=0,LF=0,WL=5", sources={"a.txt": src},
            extra={"version.toml": version_text})
        mc_caps = []
        _mc_real_deepcopy = copy.deepcopy

        def _mc_spy(obj, *args, **kwargs):
            result = _mc_real_deepcopy(obj, *args, **kwargs)
            mc_caps.append((obj, result))
            return result

        copy.deepcopy = _mc_spy
        try:
            resMc = stage_import(rootMc, ["a.txt"], mc_plan, now=NOW, run_nonce=NONCE)
        finally:
            copy.deepcopy = _mc_real_deepcopy

        mc_cand_caps = [(source, result) for source, result in mc_caps if source is mc_cand]
        mc_wl_caps = [(source, result) for source, result in mc_caps if source is mc_wl]
        mc_staged_cand = None
        mc_staged_wl = None
        if resMc.run_id:
            mc_run = mMc.parent / "imports" / resMc.run_id / "candidate"
            mc_staged_cand = tomllib.loads(
                (mc_run / "backlog_item.index.toml").read_text())["record"][0]
            mc_staged_wl = tomllib.loads(
                (mc_run / "worklog.toml").read_text())["record"][0]

        check("M-c-clean", resMc.verdict == 0)
        check("M-c-candidate-mint-result-equals-parsed-staged-record",
              len(mc_cand_caps) == 1
              and mc_cand_caps[0][1] == mc_staged_cand
              and isinstance(mc_staged_cand.get("id"), str)
              and isinstance(mc_staged_cand.get("refs"), list)
              and bool(mc_staged_cand["refs"]))
        check("M-c-worklog-mint-result-equals-parsed-staged-record",
              len(mc_wl_caps) == 1
              and mc_wl_caps[0][1] == mc_staged_wl
              and isinstance(mc_staged_wl.get("id"), str))

        mc_leaked = True
        if mc_cand_caps and mc_wl_caps:
            cand_source, cand_result = mc_cand_caps[0]
            wl_source, wl_result = mc_wl_caps[0]
            cand_source["actor"]["kind"] = "MUTATED-CAND"
            wl_source["actor"]["kind"] = "MUTATED-WL"
            mc_leaked = (
                cand_result["actor"]["kind"] != "importer"
                or wl_result["actor"]["kind"] != "importer")
        check("M-c-nested-source-mutation-does-not-leak", not mc_leaked)

        # M-d: a handle-close OSError at the stage_import boundary (the raw os.close of the store-root fd,
        # after staging completes) is converted by the top-level OSError backstop to CANNOT-EVALUATE, never
        # an uncaught escape. The close failure is armed ONLY after _stage_resolved returns, so every reader
        # and resolve/manifest close runs normally and only the boundary close fails. Reverting the boundary
        # OSError backstop lets the raw close OSError escape (caught here as "escaped").
        rootMd, mMd = build_store(sources={"a.txt": src})
        _md_mod = sys.modules[__name__]
        _md_saved_sr = _md_mod._stage_resolved
        _md_saved_close = os.close
        _md_armed = [False]
        _md_leaked_fd = []
        def _md_arm_after(*a, **k):
            _r = _md_saved_sr(*a, **k)
            _md_armed[0] = True
            return _r
        def _md_close(fd):
            if _md_armed[0]:
                _md_armed[0] = False
                _md_leaked_fd.append(fd)
                raise OSError(errno.EIO, "simulated handle-close failure at the stage_import boundary")
            return _md_saved_close(fd)
        _md_mod._stage_resolved = _md_arm_after
        os.close = _md_close
        try:
            try:
                vMd = stage_import(rootMd, ["a.txt"], plan_mapped(len(src)),
                                   now=NOW, run_nonce=NONCE).verdict
            except OSError:
                vMd = "escaped"
        finally:
            os.close = _md_saved_close
            _md_mod._stage_resolved = _md_saved_sr
            for _fd in _md_leaked_fd:
                try:
                    _md_saved_close(_fd)
                except OSError:
                    pass
        check("M-d-boundary-close-failure-cannot-eval", vMd == 2)

        # ======================= fix-forward discriminating vectors (G-series) =======================

        # G1 (ValueError family, tomllib int ceiling): a store-TOML integer literal over CPython's 4300-digit
        # string-conversion ceiling raises a bare ValueError from tomllib, OUTSIDE the OSError family the
        # backstop enumerated. The class-complete ValueError backstop converts it to verdict 2, never an
        # uncaught crash. Reverting the `except ValueError` backstop lets the raw ValueError escape.
        # PIN the int-str-conversion limit to the default (4300) around this block (test-hermeticity): the
        # ValueError these vectors exercise is raised only when tomllib converts the 5000-digit literal and
        # trips CPython's base-10 digit limit. A hostile ambient of 0 (unlimited) or 5001 would parse the
        # 5000-digit int cleanly, so the counters vector would never reach the ValueError backstop (its
        # verdict would not be 2), and the manifest vector would reach verdict 2 by an unrelated structural
        # path rather than the ValueError backstop it means to discriminate. At the pinned 4300 both trip the
        # limit, so both exercise the ValueError backstop and reverting it lets the raw ValueError escape
        # (vG1/vG1m == "escaped"). Restored in finally.
        _g1_prev_idlimit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            big_int = "9" * 5000
            rootG1, mG1 = build_store(sources={"a.txt": src})
            (mG1 / "counters.toml").write_text(
                "schema = 1\n\n[counters]\nBI = " + big_int + "\nLF = 0\nWL = 0\n", encoding="utf-8")
            try:
                vG1 = stage_import(rootG1, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict
            except ValueError:
                vG1 = "escaped"
            check("G1-huge-int-counters-cannot-eval", vG1 == 2)
            # the same ceiling in the MANIFEST (read via load_manifest/resolve, not the _read_toml wrapper) is
            # caught by the SAME top-level backstop, proving the fix covers every store-TOML read surface.
            rootG1m, mG1m = build_store(sources={"a.txt": src})
            (mG1m / "manifest.toml").write_text(manifest_text() + "big = " + big_int + "\n", encoding="utf-8")
            try:
                vG1m = stage_import(rootG1m, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict
            except ValueError:
                vG1m = "escaped"
            check("G1-huge-int-manifest-cannot-eval", vG1m == 2)
        finally:
            sys.set_int_max_str_digits(_g1_prev_idlimit)

        # G2 (ValueError family, filesystem name codec): a declared source path carrying a lone surrogate
        # passes the lexical containment and control-character guards but makes the os.stat filename encode
        # raise UnicodeEncodeError (a ValueError subclass), outside the OSError family. Verdict 2, never a
        # crash. Reverting the ValueError backstop lets the raw UnicodeEncodeError escape.
        rootG2, mG2 = build_store(sources={"a.txt": src})
        try:
            vG2 = stage_import(rootG2, ["a\ud800.txt"], plan_mapped(len(src)),
                               now=NOW, run_nonce=NONCE).verdict
        except (ValueError, UnicodeEncodeError):
            vG2 = "escaped"
        check("G2-surrogate-source-path-cannot-eval", vG2 == 2)

        # G3 (recursion backstop): a plan candidate model nested past the interpreter recursion limit
        # overflows copy.deepcopy at the mint (the emitter is iterative and bounds no depth), raising
        # RecursionError outside the OSError/ValueError families; the backstop converts it to verdict 2 and
        # stages nothing. Reverting the RecursionError backstop lets the raw crash escape.
        g3_deep = {}
        g3_cur = g3_deep
        for _ in range(3000):
            g3_cur["deep"] = {}
            g3_cur = g3_cur["deep"]
        g3_cand = bi_candidate()
        g3_cand["title"] = g3_deep
        g3_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "origin": "baseline",
                                            "record": g3_cand}]}}
        rootG3, mG3 = build_store(sources={"a.txt": src})
        try:
            vG3 = stage_import(rootG3, ["a.txt"], g3_plan, now=NOW, run_nonce=NONCE).verdict
        except RecursionError:
            vG3 = "escaped"
        check("G3-deep-nested-plan-cannot-eval", vG3 == 2)
        check("G3-deep-nested-plan-no-run", not (mG3.parent / "imports").exists())

        # G7 (no-concealed-failure, ValueError narrowing): an INTERNAL invariant ValueError raised PAST the
        # store-parse/fs-codec boundary (here from _stage_resolved, a programming failure, not malformed
        # input) must PROPAGATE as a real error, never be laundered into a malformed-input verdict 2. The
        # ValueError-family handling now lives at the specific parse loci (_read_toml, resolve/load_manifest,
        # the _read_sources fs-name codec), so no function-wide `except ValueError` remains to conceal an
        # internal bug. Reverting to the former function-wide backstop makes stage_import return verdict 2
        # here instead of raising, so this check FAILS without the fix.
        rootG7, _mG7 = build_store(sources={"a.txt": src})
        _real_stage_resolved = _stage_resolved
        globals()["_stage_resolved"] = lambda *a, **k: (_ for _ in ()).throw(
            ValueError("INTERNAL INVARIANT BUG (not malformed input)"))
        try:
            g7_propagated = False
            try:
                stage_import(rootG7, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
            except ValueError:
                g7_propagated = True
        finally:
            globals()["_stage_resolved"] = _real_stage_resolved
        check("G7-internal-valueerror-propagates-not-concealed", g7_propagated)

        # G4 (spec 8.2, counters never regress): a store whose counters high-water is BELOW an id already
        # seated in the active store (BI=0 with an existing BI-5), where the minted BI-1 clears every existing
        # id so the R6 union does NOT fire, is a corrupt counters basis: CANNOT-EVALUATE, nothing staged.
        # Pre-fix it staged verdict 0 promotion_ready with an under-stated new_high_water. Reverting the
        # regression gate re-opens the fail-open.
        rootG4, mG4 = build_store(sources={"a.txt": src},
                                  extra={"backlog_item.index.toml": bi_index_text("BI-5")})
        resG4 = stage_import(rootG4, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("G4-counters-regressed-cannot-eval", resG4.verdict == 2)
        check("G4-counters-regressed-no-run", not (mG4.parent / "imports").exists())
        # the gate refuses ONLY a genuine regression: a high-water AT or ABOVE the observed durable id still
        # stages (BI=5 with an existing BI-5, minting BI-6 above it), so a well-formed high store is not
        # false-rejected.
        rootG4b, mG4b = build_store(sources={"a.txt": src}, counters="BI=5,LF=0,WL=0",
                                    extra={"backlog_item.index.toml": bi_index_text("BI-5")})
        check("G4-counters-covered-clean",
              stage_import(rootG4b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 0)

        # G5 (argument-guard symmetry): a non-path product_root is verdict 2 like every other refused
        # argument (now/run_nonce/import_set), never an uncaught TypeError from resolve_store. Reverting the
        # product_root type guard lets the TypeError escape.
        for g5_bad in (12345, None):
            try:
                vG5 = stage_import(g5_bad, ["a.txt"], plan_mapped(len(src)),
                                   now=NOW, run_nonce=NONCE).verdict
            except TypeError:
                vG5 = "escaped"
            check("G5-nonpath-product-root-cannot-eval-{!r}".format(g5_bad), vG5 == 2)

        # G6 (spec 8.2, counters absent-namespace regression: the class-sibling of G4 through the ABSENT
        # counter door): counters.toml omits BI entirely while the active store carries BI-5; an LF-only
        # plan never mints BI so the R6 union does not fire. Corrupt basis -> CANNOT-EVALUATE, nothing
        # staged. Pre-fix the gate inspected only namespaces present in high_water, so the untracked
        # namespace slipped through as verdict 0 promotion_ready with BI omitted from new_high_water.
        lf_only = dict(fragments=dict())
        lf_only["fragments"]["a.txt"] = [dict(span=[0, len(src)], state="unmapped", origin="baseline")]
        rootG6, mG6 = build_store(counters="LF=0,WL=0", sources=dict([("a.txt", src)]),
                                  extra=dict([("backlog_item.index.toml", bi_index_text("BI-5"))]))
        resG6 = stage_import(rootG6, ["a.txt"], lf_only, now=NOW, run_nonce=NONCE)
        check("G6-counters-absent-namespace-regressed-cannot-eval", resG6.verdict == 2)
        check("G6-counters-absent-namespace-no-run", not (mG6.parent / "imports").exists())
        # the gate refuses ONLY a genuine untracked-namespace regression: an LF-only plan against a store
        # with NO seated BI id (BI legitimately absent from counters) still stages clean.
        rootG6b, mG6b = build_store(counters="LF=0,WL=0", sources=dict([("a.txt", src)]))
        check("G6-absent-namespace-no-durable-id-clean",
              stage_import(rootG6b, ["a.txt"], lf_only, now=NOW, run_nonce=NONCE).verdict == 0)

        # --- OPF-IMPORT-OPS: scan_import (read-only enumeration) --------------------------------------
        # S1 positive scan: a clean two-source store enumerates both, one whole-file fragment each, with a
        # digest and a fragment_id; the store is byte-untouched (scan is read-only).
        rootS1, mS1 = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        s1_before = snapshot(mS1)
        sc1 = scan_import(rootS1, ["a.txt", "b.txt"])
        check("S1-scan-clean", sc1.verdict == 0)
        check("S1-scan-two-sources", len(sc1.sources) == 2 and len(sc1.fragments) == 2)
        check("S1-scan-whole-file-span",
              all(f["span"] == [0, sz] for f, sz in zip(
                  sorted(sc1.fragments, key=lambda f: f["source_path"]), (4, 6))))
        check("S1-scan-has-digest", bool(sc1.inventory_digest and sc1.inventory_digest.startswith("sha256:")))
        check("S1-scan-read-only", snapshot(mS1) == s1_before)
        check("S1-scan-no-imports-written", not (mS1.parent / "imports").exists())

        # S2 determinism: the SAME inputs in REVERSED declaration order yield a byte-identical inventory
        # digest (sorted by path bytes; declaration order does not perturb it). Reverting the sort reds.
        rootS2, _mS2 = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        sc2 = scan_import(rootS2, ["b.txt", "a.txt"])
        check("S2-scan-order-independent-digest",
              sc2.verdict == 0 and sc2.inventory_digest == sc1.inventory_digest)
        # a DIFFERENT source body changes the digest (the digest actually covers content).
        rootS2b, _mS2b = build_store(sources={"a.txt": "AAAA", "b.txt": "bbbbbb"})
        sc2b = scan_import(rootS2b, ["a.txt", "b.txt"])
        check("S2-scan-content-changes-digest",
              sc2b.verdict == 0 and sc2b.inventory_digest != sc1.inventory_digest)

        # S3 fail-closed: an unreadable/absent/symlinked/exotic declared source is cannot-evaluate (verdict
        # 2), never an empty or partial inventory. An absent declared member and a symlinked source both red.
        rootS3, _mS3 = build_store(sources={"a.txt": "aaaa"})
        check("S3-scan-absent-declared-cannot-eval",
              scan_import(rootS3, ["a.txt", "missing.txt"]).verdict == 2)
        rootS3b, _mS3b = build_store(sources={"a.txt": "aaaa"})
        os.symlink("a.txt", str(rootS3b / "link.txt"))
        check("S3-scan-symlink-source-cannot-eval", scan_import(rootS3b, ["link.txt"]).verdict == 2)
        # an empty (readable) source is a clean zero-length whole-file fragment [0, 0], never a refusal.
        rootS3c, _mS3c = build_store(sources={"empty.txt": ""})
        scE = scan_import(rootS3c, ["empty.txt"])
        check("S3-scan-empty-source-clean",
              scE.verdict == 0 and scE.fragments and scE.fragments[0]["span"] == [0, 0])

        # S4 init-first: scanning a location with NO resolvable store is cannot-evaluate (verdict 2).
        noroot = base / "noopf-{:02d}".format(counter[0] + 999)
        (noroot).mkdir(parents=True)
        (noroot / "a.txt").write_text("aaaa", encoding="utf-8")
        check("S4-scan-no-store-cannot-eval", scan_import(noroot, ["a.txt"]).verdict == 2)

        # --- OPF-IMPORT-OPS: plan_import (scan + baseline classify + stage + review surface) -----------
        # P1 positive plan: a clean store stages a run whose every source fragment is unmapped -> a
        # legacy_fragment per source; inventory.toml + IMPORT-REPORT.md review artefacts present; the
        # ACTIVE store (machine subdir) is byte-untouched.
        rootP1, mP1 = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        p1_before = snapshot(mP1)
        pr1 = plan_import(rootP1, ["a.txt", "b.txt"], now=NOW, run_nonce=NONCE)
        check("P1-plan-clean", pr1.verdict == 0)
        check("P1-plan-run-id-grammar", bool(pr1.run_id and INDEP_RUN_ID_RE.match(pr1.run_id)))
        check("P1-plan-migration-incomplete", pr1.migration_incomplete is True)  # all-unmapped -> all LF
        check("P1-plan-active-store-unchanged", snapshot(mP1) == p1_before)
        p1_dir = mP1.parent / "imports" / (pr1.run_id or "MISSING")
        check("P1-plan-run-dir", p1_dir.is_dir())
        check("P1-plan-inventory-present", (p1_dir / "inventory.toml").is_file())
        check("P1-plan-report-present", (p1_dir / "IMPORT-REPORT.md").is_file())
        check("P1-plan-report-rel", pr1.report_rel == ".working/imports/" + (pr1.run_id or "M")
              + "/IMPORT-REPORT.md")
        if (p1_dir / "fragments" / "legacy_fragment.index.toml").is_file():
            lfp = tomllib.loads((p1_dir / "fragments" / "legacy_fragment.index.toml").read_text())
            check("P1-plan-one-lf-per-source", len(lfp["record"]) == 2)
        # the persisted inventory digest matches the scan's, and the report cites it.
        if (p1_dir / "inventory.toml").is_file():
            invp = tomllib.loads((p1_dir / "inventory.toml").read_text())
            check("P1-plan-inventory-digest-matches", invp.get("inventory_digest") == pr1.inventory_digest)
        if (p1_dir / "IMPORT-REPORT.md").is_file():
            md = (p1_dir / "IMPORT-REPORT.md").read_text()
            check("P1-plan-report-cites-digest", pr1.inventory_digest in md)
            check("P1-plan-report-byte-reproducible",
                  md == _render_report_md(pr1.inventory_digest,
                                          scan_import(rootP1, ["a.txt", "b.txt"]).fragments, [], pr1.run_id))

        # P2 determinism: identical inputs -> identical run id; a different nonce -> a different run id.
        rootP2, _mP2 = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        pr2 = plan_import(rootP2, ["a.txt", "b.txt"], now=NOW, run_nonce=NONCE)
        check("P2-plan-deterministic-run-id", pr2.verdict == 0 and pr2.run_id == pr1.run_id)
        rootP2b, _mP2b = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        pr2b = plan_import(rootP2b, ["a.txt", "b.txt"], now=NOW, run_nonce="other-nonce")
        check("P2-plan-nonce-changes-run-id", pr2b.verdict == 0 and pr2b.run_id != pr1.run_id)

        # P3 inert proposals: a well-formed proposal is RECORDED in the report as an inert suggestion but
        # NEVER auto-rested (the fragment stays unmapped -> legacy_fragment); a proposal targeting a path
        # outside the scanned set is a finding (confinement); a bad suggested_state is a finding.
        rootP3, mP3 = build_store(sources={"a.txt": "aaaa"})
        good_prop = [{"source_path": "a.txt", "span": [0, 4], "suggested_state": "mapped",
                      "note": "looks like a backlog item"}]
        pr3 = plan_import(rootP3, ["a.txt"], proposals=good_prop, now=NOW, run_nonce=NONCE)
        check("P3-plan-with-proposal-clean", pr3.verdict == 0)
        p3_dir = mP3.parent / "imports" / (pr3.run_id or "MISSING")
        if (p3_dir / "IMPORT-REPORT.md").is_file():
            md3 = (p3_dir / "IMPORT-REPORT.md").read_text()
            check("P3-proposal-recorded-inert", "model_proposal" in md3 and "suggested_state=mapped" in md3)
        if (p3_dir / "candidate").is_dir():
            # inert: NO candidate index minted from the proposal; the fragment is quarantined as LF.
            check("P3-proposal-not-auto-rested",
                  not (p3_dir / "candidate" / "backlog_item.index.toml").is_file()
                  and (p3_dir / "fragments" / "legacy_fragment.index.toml").is_file())
        rootP3b, _mP3b = build_store(sources={"a.txt": "aaaa"})
        bad_target = [{"source_path": "outside.txt", "span": [0, 1], "suggested_state": "mapped"}]
        check("P3-proposal-confinement-finding",
              plan_import(rootP3b, ["a.txt"], proposals=bad_target, now=NOW, run_nonce=NONCE).verdict == 1)
        rootP3c, _mP3c = build_store(sources={"a.txt": "aaaa"})
        bad_state = [{"source_path": "a.txt", "span": [0, 4], "suggested_state": "renamed"}]
        check("P3-proposal-bad-state-finding",
              plan_import(rootP3c, ["a.txt"], proposals=bad_state, now=NOW, run_nonce=NONCE).verdict == 1)

        # P4 init-first + fail-closed: planning a location with no store is cannot-evaluate.
        noroot2 = base / "noopf2-{:02d}".format(counter[0] + 998)
        noroot2.mkdir(parents=True)
        (noroot2 / "a.txt").write_text("aaaa", encoding="utf-8")
        check("P4-plan-no-store-cannot-eval",
              plan_import(noroot2, ["a.txt"], now=NOW, run_nonce=NONCE).verdict == 2)

        # --- OPF-IMPORT-OPS: apply_import (promotion) is DEFERRED, fail-closed, mutates nothing ---------
        rootA1, mA1 = build_store(sources={"a.txt": "aaaa"})
        prA = plan_import(rootA1, ["a.txt"], now=NOW, run_nonce=NONCE)
        a1_before = snapshot(mA1)
        appl = apply_import(rootA1, prA.run_id or "imp-x", accepted_plan_digest="sha256:0", now=NOW)
        check("A1-apply-deferred-cannot-eval", appl.verdict == 2 and appl.promoted is False)
        check("A1-apply-mutates-nothing", snapshot(mA1) == a1_before)

        # --- OPF-IMPORT-VERB PR-A: origin provenance schema ------------------------------------------
        # A plan fragment row missing `origin`, or carrying an out-of-vocabulary origin, is a finding (the
        # required spec-14.1 provenance key); a valid baseline plan stages and its mappings.toml rows and
        # report.toml carry origin and the plan/inventory binding digests. Reverting the origin requirement
        # would stage the missing-origin plan clean (verdict 0), flipping O-missing-origin-finding.
        rootO, mO = build_store(sources={"a.txt": src})
        no_origin = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]}}
        check("O-missing-origin-finding",
              stage_import(rootO, ["a.txt"], no_origin, now=NOW, run_nonce=NONCE).verdict == 1)
        rootOb, mOb = build_store(sources={"a.txt": src})
        bad_origin = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped",
                                               "origin": "guessed"}]}}
        check("O-bad-origin-finding",
              stage_import(rootOb, ["a.txt"], bad_origin, now=NOW, run_nonce=NONCE).verdict == 1)
        rootOc, mOc = build_store(sources={"a.txt": src})
        resOc = stage_import(rootOc, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped",
                                                       "origin": "baseline"}]}}, now=NOW, run_nonce=NONCE)
        check("O-valid-origin-clean", resOc.verdict == 0)
        if resOc.run_id:
            o_dir = mOc.parent / "imports" / resOc.run_id
            mrows = tomllib.loads((o_dir / "mappings.toml").read_text())["mapping"]
            check("O-mappings-carry-origin", bool(mrows) and all(r.get("origin") in _ORIGIN_SET
                                                                 for r in mrows))
            rep = tomllib.loads((o_dir / "report.toml").read_text())
            check("O-report-binding-digests",
                  bool(_DIGEST_RE.match(rep.get("plan_digest", "")))
                  and bool(_DIGEST_RE.match(rep.get("inventory_digest", ""))))

        # --- proposals.toml: machine-readable model proposals staged beside the inventory --------------
        pp_prop = [{"source_path": "a.txt", "span": [0, 4], "suggested_state": "mapped", "note": "n"}]
        rootPP, mPP = build_store(sources={"a.txt": "aaaa"})
        ppr = plan_import(rootPP, ["a.txt"], proposals=pp_prop, now=NOW, run_nonce=NONCE)
        pp_dir = mPP.parent / "imports" / (ppr.run_id or "MISSING")
        check("PP-proposals-staged", ppr.verdict == 0 and (pp_dir / "proposals.toml").is_file())
        if (pp_dir / "proposals.toml").is_file():
            props = tomllib.loads((pp_dir / "proposals.toml").read_text())
            check("PP-proposals-origin-stamped",
                  props.get("run_id") == ppr.run_id
                  and [p.get("origin") for p in props.get("proposal", [])] == ["model_proposal"])
        # a plan with NO proposals still stages an (empty) proposals.toml, so the review path always reads
        # machine data.
        rootPPe, mPPe = build_store(sources={"a.txt": "aaaa"})
        ppe = plan_import(rootPPe, ["a.txt"], now=NOW, run_nonce=NONCE)
        ppe_dir = mPPe.parent / "imports" / (ppe.run_id or "MISSING")
        check("PP-empty-proposals-staged", (ppe_dir / "proposals.toml").is_file())

        # --- review_import (--review acceptance capture): the attributed decision record ---------------
        import io

        def all_decisions(run_dir, verb="accept"):
            inv = tomllib.loads((run_dir / "inventory.toml").read_text())
            mp = tomllib.loads((run_dir / "mappings.toml").read_text())
            by_key = {(r["source_path"], tuple(r["span"])): r for r in mp["mapping"]}
            out = []
            for fr in inv["fragment"]:
                row = by_key[(fr["source_path"], tuple(fr["span"]))]
                out.append({"fragment_id": fr["fragment_id"], "decision": verb,
                            "origin": row["origin"], "proposed_state": row["state"]})
            return out

        def rewrite_report_digest(run_dir, rel_path):
            """After mutating an enumerated artefact, refresh its digest in report.toml so the staged-run
            gate's artifact-digest-integrity check stays coherent and the TARGET coherence check is the one
            that fires (the keep-other-fields-coherent discriminator discipline; report.toml is not in its
            own artefact list, so refreshing it never disturbs the digest checks)."""
            rep = tomllib.loads((run_dir / "report.toml").read_text())
            new_sha = _sha256_hex((run_dir / rel_path).read_bytes())
            for entry in rep.get("artifact", []):
                if entry.get("path") == rel_path:
                    entry["sha256"] = new_sha
            (run_dir / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")

        rootR1, mR1 = build_store(sources={"a.txt": "aaaa", "b.txt": "bbbbbb"})
        pr = plan_import(rootR1, ["a.txt", "b.txt"], now=NOW, run_nonce=NONCE)
        r1_dir = mR1.parent / "imports" / (pr.run_id or "MISSING")
        r1_before = snapshot(mR1)
        decs = all_decisions(r1_dir)
        rr = review_import(rootR1, pr.run_id, actor="Reviewer", decisions=decs, now=NOW)
        check("R1-review-clean", rr.verdict == 0 and rr.decisions_count == 2)
        check("R1-acceptance-present", (r1_dir / "acceptance.json").is_file())
        # Review captures a decision record only; the ACTIVE store (machine subdir) is byte-untouched.
        check("R1-active-store-unchanged", snapshot(mR1) == r1_before)
        if (r1_dir / "acceptance.json").is_file():
            acc_bytes = (r1_dir / "acceptance.json").read_bytes()
            acc = json.loads(acc_bytes.decode("utf-8"))
            report_r1 = tomllib.loads((r1_dir / "report.toml").read_text())
            check("R1-acceptance-schema-valid", _validate_acceptance(acc) == [])
            check("R1-acceptance-binds-run",
                  acc["run_id"] == pr.run_id and acc["plan_digest"] == report_r1["plan_digest"]
                  and acc["inventory_digest"] == pr.inventory_digest)
            check("R1-actor-recorded",
                  acc["actor"]["declared"] == "Reviewer"
                  and set(acc["actor"]["context"]) == {"os_user", "git_identity", "hostname"})
            check("R1-canonical-json-bytes", acc_bytes == _emit_acceptance_bytes(acc))

            # _validate_acceptance strengthening: reviewed_at must be a well-formed RFC-3339 UTC timestamp
            # (a non-empty "garbage" now FINDINGs); a trailing newline in the run_id or either digest is
            # rejected (the \Z-anchored regexes, not $); and a control char in actor.declared FINDINGs.
            def _acc_findings(**overrides):
                a = copy.deepcopy(acc)
                for k, v in overrides.items():
                    a[k] = v
                return _validate_acceptance(a)
            check("R1-acc-reviewed-at-garbage-finding", _acc_findings(reviewed_at="garbage") != [])
            # N5: an unpadded/oddly-shaped reviewed_at ("2026-9-9T1:2:3Z") that strptime alone accepts is a
            # FINDING under the strict fixed-width \A...\Z regex; a well-formed padded value stays clean.
            check("R1-acc-reviewed-at-unpadded-finding",
                  _acc_findings(reviewed_at="2026-9-9T1:2:3Z") != [])
            check("R1-acc-reviewed-at-wellformed-clean",
                  _acc_findings(reviewed_at="2026-09-09T01:02:03Z") == [])
            check("R1-acc-run-id-trailing-newline-finding",
                  _acc_findings(run_id=acc["run_id"] + "\n") != [])
            check("R1-acc-plan-digest-trailing-newline-finding",
                  _acc_findings(plan_digest=acc["plan_digest"] + "\n") != [])
            check("R1-acc-inv-digest-trailing-newline-finding",
                  _acc_findings(inventory_digest=acc["inventory_digest"] + "\n") != [])
            acc_decl = copy.deepcopy(acc)
            acc_decl["actor"]["declared"] = "bad\x01name"
            check("R1-acc-declared-control-char-finding", _validate_acceptance(acc_decl) != [])
        # re-review REPLACES the acceptance record (a new actor, reject decisions) in place.
        rr2 = review_import(rootR1, pr.run_id, actor="Second", decisions=all_decisions(r1_dir, "reject"),
                            now=NOW)
        check("R1-re-review-replaces", rr2.verdict == 0)
        if (r1_dir / "acceptance.json").is_file():
            acc2 = json.loads((r1_dir / "acceptance.json").read_text())
            check("R1-re-review-new-record",
                  acc2["actor"]["declared"] == "Second"
                  and all(d["decision"] == "reject" for d in acc2["decisions"]))

        # F3: a mid-replace failure leaves the PRIOR acceptance.json BYTE-INTACT (recoverable temp+rename,
        # not the old destroy-then-recreate). Patch the journaled write to raise; the verdict is
        # cannot-evaluate and the prior record survives unchanged (never 0-byte or absent).
        if (r1_dir / "acceptance.json").is_file():
            prior_acc_bytes = (r1_dir / "acceptance.json").read_bytes()
            _saved_apply_ops = _journal.apply_ops

            def _boom_apply_ops(*_a, **_k):
                raise _journal.JournalError("injected mid-replace failure")

            _journal.apply_ops = _boom_apply_ops
            try:
                rr_fail = review_import(rootR1, pr.run_id, actor="Third",
                                        decisions=all_decisions(r1_dir, "accept"), now=NOW)
            finally:
                _journal.apply_ops = _saved_apply_ops
            check("R1-replace-failure-cannot-eval", rr_fail.verdict == 2)
            check("R1-replace-failure-prior-intact",
                  (r1_dir / "acceptance.json").is_file()
                  and (r1_dir / "acceptance.json").read_bytes() == prior_acc_bytes)

        # N2: a same-store writer swaps the staged temp for a DANGLING SYMLINK after the O_EXCL create but
        # before the rename. The pre-rename no-follow content re-verify (a regular file on the OPENED fd via
        # _read_at, hash == intended digest) catches it: verdict 2, acceptance.json is NOT the symlink, and
        # the prior record is byte-intact. Without the re-verify the rename would install the symlink AS
        # acceptance.json.
        if (r1_dir / "acceptance.json").is_file():
            prior_swap_bytes = (r1_dir / "acceptance.json").read_bytes()
            _saved_apply_ops_n2 = _journal.apply_ops

            def _swap_temp_apply_ops(root_fd, ops, reader, *_a, **_k):
                _saved_apply_ops_n2(root_fd, ops, reader)   # the real O_EXCL create of the temp
                for tmp in r1_dir.glob(ACCEPTANCE_NAME + ".tmp-*"):
                    tmp.unlink()
                    tmp.symlink_to("acceptance-swap-target-does-not-exist")

            _journal.apply_ops = _swap_temp_apply_ops
            try:
                rr_swap = review_import(rootR1, pr.run_id, actor="Swapper",
                                        decisions=all_decisions(r1_dir, "accept"), now=NOW)
            finally:
                _journal.apply_ops = _saved_apply_ops_n2
            check("N2-temp-swap-cannot-eval", rr_swap.verdict == 2)
            check("N2-acceptance-not-symlink",
                  (r1_dir / "acceptance.json").is_file()
                  and not (r1_dir / "acceptance.json").is_symlink())
            check("N2-temp-swap-prior-intact",
                  (r1_dir / "acceptance.json").read_bytes() == prior_swap_bytes)

        # F1/F2: a same-store writer swaps the staged temp for a DIFFERENT-CONTENT REGULAR FILE after the
        # O_EXCL create (during or after apply_ops, before capture). The content-verify (re-read no-follow,
        # confirm the bytes hash to the intended acceptance digest) catches it where a bare st_ino/st_size
        # identity check could miss a same-size substitution: verdict 2 (content-digest mismatch), the prior
        # record byte-intact, and acceptance.json is NOT the substituted bytes. This is defence-in-depth; the
        # residual content-verify -> rename race is a disclosed residual subsumed by the acceptance-
        # authenticity out-of-scope residual (a staging-write principal can forge acceptance.json directly).
        if (r1_dir / "acceptance.json").is_file():
            prior_sub_bytes = (r1_dir / "acceptance.json").read_bytes()
            substitute_bytes = b"substituted-different-content-regular-file"
            _saved_apply_ops_f1 = _journal.apply_ops

            def _swap_content_apply_ops(root_fd, ops, reader, *_a, **_k):
                _saved_apply_ops_f1(root_fd, ops, reader)   # the real O_EXCL create of the temp
                for tmp in r1_dir.glob(ACCEPTANCE_NAME + ".tmp-*"):
                    tmp.write_bytes(substitute_bytes)       # swap for a DIFFERENT-content regular file

            _journal.apply_ops = _swap_content_apply_ops
            try:
                rr_sub = review_import(rootR1, pr.run_id, actor="Substituter",
                                       decisions=all_decisions(r1_dir, "accept"), now=NOW)
            finally:
                _journal.apply_ops = _saved_apply_ops_f1
            check("F1-content-swap-cannot-eval", rr_sub.verdict == 2)
            check("F1-content-swap-prior-intact",
                  (r1_dir / "acceptance.json").read_bytes() == prior_sub_bytes)
            check("F1-content-swap-not-substituted",
                  (r1_dir / "acceptance.json").read_bytes() != substitute_bytes)

        # N3: a POST-rename parent-dir fsync failure leaves the NEW record installed with uncertain
        # durability; it must NOT be reported as a pre-rename failure ("the prior acceptance is intact").
        # Inject a directory-fsync failure that fires ONLY once the cutover rename has consumed the temp (the
        # sibling temp is gone), isolating the post-rename dir-fsync path from the apply_ops pre-rename dir
        # fsync (which runs while the temp still exists). Patch only os.fsync (os.rename must keep its
        # identity, or the dir-fd containment probe fails closed): verdict 2, the new record IS installed,
        # and the message says the new record is in place / durability uncertain, never the prior is intact.
        if (r1_dir / "acceptance.json").is_file():
            _saved_fsync_n3 = os.fsync
            _n3 = {"armed": False}

            def _fsync_after_cutover(fd):
                if _n3["armed"]:
                    try:
                        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                    except OSError:
                        is_dir = False
                    if is_dir and not list(r1_dir.glob(ACCEPTANCE_NAME + ".tmp-*")):
                        raise OSError("injected post-rename dir fsync failure")
                return _saved_fsync_n3(fd)

            os.fsync = _fsync_after_cutover
            _n3["armed"] = True
            try:
                rr_n3 = review_import(rootR1, pr.run_id, actor="N3fsync",
                                      decisions=all_decisions(r1_dir, "accept"), now=NOW)
            finally:
                os.fsync = _saved_fsync_n3
            check("N3-post-rename-fsync-cannot-eval", rr_n3.verdict == 2)
            if (r1_dir / "acceptance.json").is_file():
                acc_n3 = json.loads((r1_dir / "acceptance.json").read_text())
                check("N3-post-rename-new-record-installed",
                      acc_n3["actor"]["declared"] == "N3fsync"
                      and all(d["decision"] == "accept" for d in acc_n3["decisions"]))
            check("N3-post-rename-message-not-prior-intact",
                  bool(rr_n3.findings) and "durability uncertain" in rr_n3.findings[0]
                  and "the prior acceptance is intact" not in rr_n3.findings[0])

        # findings (verdict 1): incomplete coverage, an unknown fragment, a duplicate, an echo mismatch.
        check("R-review-incomplete-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=decs[:-1], now=NOW).verdict == 1)
        unknown = decs + [dict(decs[0], fragment_id="frag-" + ("0" * 16))]
        check("R-review-unknown-fragment-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=unknown, now=NOW).verdict == 1)
        dup = decs + [dict(decs[0])]
        check("R-review-duplicate-fragment-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=dup, now=NOW).verdict == 1)
        wrong_echo = [dict(decs[0], proposed_state="mapped")] + decs[1:]
        check("R-review-echo-mismatch-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=wrong_echo, now=NOW).verdict == 1)
        # an unhashable decision verb ([] or {}) is a FINDING, not an uncaught TypeError at the membership
        # test (F5): without the isinstance guard this review would raise and crash the self-test.
        unhashable_verb = [dict(decs[0], decision=[])] + [dict(d) for d in decs[1:]]
        check("R-review-unhashable-verb-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=unhashable_verb, now=NOW).verdict == 1)
        unhashable_verb2 = [dict(decs[0], decision={})] + [dict(d) for d in decs[1:]]
        check("R-review-unhashable-verb-dict-finding",
              review_import(rootR1, pr.run_id, actor="R", decisions=unhashable_verb2, now=NOW).verdict == 1)

        # cannot-evaluate (verdict 2): a missing actor, a malformed run-id, a not-staged run, and a non-TTY
        # interactive invocation.
        check("R-review-missing-actor-cannot-eval",
              review_import(rootR1, pr.run_id, actor="", decisions=decs, now=NOW).verdict == 2)
        check("R-review-bad-runid-cannot-eval",
              review_import(rootR1, "not-a-run-id", actor="R", decisions=[], now=NOW).verdict == 2)
        check("R-review-no-run-cannot-eval",
              review_import(rootR1, "imp-20260101T000000Z-0000000000000000", actor="R",
                            decisions=[], now=NOW).verdict == 2)
        check("R-interactive-non-tty-cannot-eval",
              review_import_interactive(rootR1, pr.run_id, actor="R", now=NOW,
                                        in_stream=io.StringIO(""), out_stream=io.StringIO()).verdict == 2)

        # F2: review recomputes the binding digests over the staged bytes. A run with plan.toml removed is
        # cannot-evaluate (a required artefact is absent); a run whose report.toml plan_digest is replaced
        # with a well-formed but wrong digest is cannot-evaluate (the recompute over plan.toml disagrees),
        # and NO acceptance is written. Without the recompute the second would bind a digest that names no
        # staged plan.toml.
        rootF2a, mF2a = build_store(sources={"a.txt": "aaaa"})
        prf2a = plan_import(rootF2a, ["a.txt"], now=NOW, run_nonce=NONCE)
        f2a_dir = mF2a.parent / "imports" / (prf2a.run_id or "MISSING")
        (f2a_dir / "plan.toml").unlink()
        check("R-review-plan-toml-removed-cannot-eval",
              review_import(rootF2a, prf2a.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("R-review-plan-toml-removed-no-acceptance", not (f2a_dir / "acceptance.json").is_file())

        rootF2b, mF2b = build_store(sources={"a.txt": "aaaa"})
        prf2b = plan_import(rootF2b, ["a.txt"], now=NOW, run_nonce=NONCE)
        f2b_dir = mF2b.parent / "imports" / (prf2b.run_id or "MISSING")
        rep_f2b = tomllib.loads((f2b_dir / "report.toml").read_text())
        rep_f2b["plan_digest"] = "sha256:" + ("0" * 64)
        (f2b_dir / "report.toml").write_text(_opf_emit.emit(rep_f2b), encoding="utf-8")
        check("R-review-plan-digest-mismatch-cannot-eval",
              review_import(rootF2b, prf2b.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("R-review-plan-digest-mismatch-no-acceptance", not (f2b_dir / "acceptance.json").is_file())

        # N1: review captures acceptance ONLY over a COHERENT, promotion-ready run matching its dir. Four
        # single-mutation discriminators, each verdict 2 with NO acceptance written.
        # (a) an UNPARSEABLE plan.toml whose report.plan_digest is recomputed to match the garbage bytes: the
        #     bytes-hash still agrees, but plan.toml no longer PARSES, so review is cannot-evaluate.
        rootN1a, mN1a = build_store(sources={"a.txt": "aaaa"})
        prn1a = plan_import(rootN1a, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1a_dir = mN1a.parent / "imports" / (prn1a.run_id or "MISSING")
        garbage = b"not valid TOML!"
        (n1a_dir / "plan.toml").write_bytes(garbage)
        rep_n1a = tomllib.loads((n1a_dir / "report.toml").read_text())
        rep_n1a["plan_digest"] = "sha256:" + _sha256_hex(garbage)
        (n1a_dir / "report.toml").write_text(_opf_emit.emit(rep_n1a), encoding="utf-8")
        check("N1-plan-unparseable-cannot-eval",
              review_import(rootN1a, prn1a.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-plan-unparseable-no-acceptance", not (n1a_dir / "acceptance.json").is_file())
        # (b) an EMPTY run.toml (parses as an empty table, so not a coherent run).
        rootN1b, mN1b = build_store(sources={"a.txt": "aaaa"})
        prn1b = plan_import(rootN1b, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1b_dir = mN1b.parent / "imports" / (prn1b.run_id or "MISSING")
        (n1b_dir / "run.toml").write_text("", encoding="utf-8")
        check("N1-empty-run-toml-cannot-eval",
              review_import(rootN1b, prn1b.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-empty-run-toml-no-acceptance", not (n1b_dir / "acceptance.json").is_file())
        # (c) a report.run_id naming a DIFFERENT (valid) run-id, not this run dir.
        rootN1c, mN1c = build_store(sources={"a.txt": "aaaa"})
        prn1c = plan_import(rootN1c, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1c_dir = mN1c.parent / "imports" / (prn1c.run_id or "MISSING")
        rep_n1c = tomllib.loads((n1c_dir / "report.toml").read_text())
        rep_n1c["run_id"] = "imp-20260101T000000Z-0000000000000000"
        (n1c_dir / "report.toml").write_text(_opf_emit.emit(rep_n1c), encoding="utf-8")
        check("N1-report-run-id-mismatch-cannot-eval",
              review_import(rootN1c, prn1c.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-report-run-id-mismatch-no-acceptance", not (n1c_dir / "acceptance.json").is_file())
        # (d) a report.verdict/promotion_ready that is not the promotion-ready marker (verdict 2, False).
        rootN1d, mN1d = build_store(sources={"a.txt": "aaaa"})
        prn1d = plan_import(rootN1d, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1d_dir = mN1d.parent / "imports" / (prn1d.run_id or "MISSING")
        rep_n1d = tomllib.loads((n1d_dir / "report.toml").read_text())
        rep_n1d["verdict"] = 2
        rep_n1d["promotion_ready"] = False
        (n1d_dir / "report.toml").write_text(_opf_emit.emit(rep_n1d), encoding="utf-8")
        check("N1-not-promotion-ready-cannot-eval",
              review_import(rootN1d, prn1d.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-not-promotion-ready-no-acceptance", not (n1d_dir / "acceptance.json").is_file())

        # F3: N1's coherence gate is run-descriptor IDENTITY, not a bare non-empty table / `!= CLEAN`
        # compare. Three further single-mutation discriminators, each verdict 2 with NO acceptance written.
        # (e) an UNRELATED run.toml (a non-empty table that is not this run's descriptor) is not coherent:
        #     it carries no run_id naming this dir.
        rootN1e, mN1e = build_store(sources={"a.txt": "aaaa"})
        prn1e = plan_import(rootN1e, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1e_dir = mN1e.parent / "imports" / (prn1e.run_id or "MISSING")
        (n1e_dir / "run.toml").write_text("unrelated = true\n", encoding="utf-8")
        check("N1-unrelated-run-toml-cannot-eval",
              review_import(rootN1e, prn1e.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-unrelated-run-toml-no-acceptance", not (n1e_dir / "acceptance.json").is_file())
        # (f) a proposals.toml whose run_id names a DIFFERENT (valid) run: the run binding is unmet, so a
        #     foreign proposal set can no longer bind acceptance to this run.
        rootN1f, mN1f = build_store(sources={"a.txt": "aaaa"})
        prn1f = plan_import(rootN1f, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1f_dir = mN1f.parent / "imports" / (prn1f.run_id or "MISSING")
        props_n1f = tomllib.loads((n1f_dir / "proposals.toml").read_text())
        props_n1f["run_id"] = "imp-20260101T000000Z-0000000000000000"
        (n1f_dir / "proposals.toml").write_text(_opf_emit.emit(props_n1f), encoding="utf-8")
        check("N1-proposals-run-id-mismatch-cannot-eval",
              review_import(rootN1f, prn1f.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-proposals-run-id-mismatch-no-acceptance", not (n1f_dir / "acceptance.json").is_file())
        # (g) a report.verdict of `false` (a bool) must NOT pass as CLEAN via Python's `False == 0`: the
        #     integer-type check (`type(x) is int` excludes bool) makes it cannot-evaluate.
        rootN1g, mN1g = build_store(sources={"a.txt": "aaaa"})
        prn1g = plan_import(rootN1g, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1g_dir = mN1g.parent / "imports" / (prn1g.run_id or "MISSING")
        rep_n1g = tomllib.loads((n1g_dir / "report.toml").read_text())
        rep_n1g["verdict"] = False
        (n1g_dir / "report.toml").write_text(_opf_emit.emit(rep_n1g), encoding="utf-8")
        check("N1-bool-verdict-cannot-eval",
              review_import(rootN1g, prn1g.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N1-bool-verdict-no-acceptance", not (n1g_dir / "acceptance.json").is_file())
        # (h) R5-F2: a run.toml `schema` of `true` (a bool) must NOT pass as SCHEMA via Python's `True == 1`
        #     (SCHEMA is 1). This is the COHERENT-tamper case: the report.toml run.toml digest is refreshed so
        #     the pre-check gate's artifact-digest-integrity stays green and the loader's strict-int schema
        #     guard is the check under test (an attacker with staging write access can update the digest too).
        #     FULL decisions are supplied, so a passing loader would CAPTURE acceptance (verdict 0); the strict-
        #     int guard (`type(x) is int` excludes bool) is what keeps the incoherent run cannot-evaluate with
        #     NO acceptance. Sibling of the (g) verdict bool-slip; the class-width miss R4->R5 re-found.
        rootN1h, mN1h = build_store(sources={"a.txt": "aaaa"})
        prn1h = plan_import(rootN1h, ["a.txt"], now=NOW, run_nonce=NONCE)
        n1h_dir = mN1h.parent / "imports" / (prn1h.run_id or "MISSING")
        n1h_decs = all_decisions(n1h_dir)
        run_n1h = tomllib.loads((n1h_dir / "run.toml").read_text())
        run_n1h["schema"] = True
        (n1h_dir / "run.toml").write_text(_opf_emit.emit(run_n1h), encoding="utf-8")
        rewrite_report_digest(n1h_dir, "run.toml")
        check("N1-bool-schema-cannot-eval",
              review_import(rootN1h, prn1h.run_id, actor="R", decisions=n1h_decs, now=NOW).verdict == 2)
        check("N1-bool-schema-no-acceptance", not (n1h_dir / "acceptance.json").is_file())

        # F4 (module): a nested-unhashable span ([[], []]) in a staged mapping row or inventory fragment is a
        # located CANNOT-EVALUATE at the review loader, never an uncaught TypeError at the (source_path,
        # tuple(span)) key. The U8 emitter refuses a nested array, so it is injected as raw text (only
        # corruption or a staging-write attacker produces such a span). The mapping-row case is caught by the
        # loader's int-element span guard; the inventory-fragment case is caught fail-closed at the
        # inventory-digest recompute (the emitter refuses the nested-array payload) ahead of the guard.
        def inject_nested_span_text(path, span):
            old = "span = [{}, {}]".format(span[0], span[1])
            txt = path.read_text()
            if old not in txt:
                raise OSError("harness: could not locate {!r} to inject a nested span".format(old))
            path.write_text(txt.replace(old, "span = [[], []]", 1), encoding="utf-8")

        rootN4c, mN4c = build_store(sources={"a.txt": "aaaa"})
        prn4c = plan_import(rootN4c, ["a.txt"], now=NOW, run_nonce=NONCE)
        n4c_dir = mN4c.parent / "imports" / (prn4c.run_id or "MISSING")
        mp_n4c = tomllib.loads((n4c_dir / "mappings.toml").read_text())
        inject_nested_span_text(n4c_dir / "mappings.toml", mp_n4c["mapping"][0]["span"])
        check("N4-mapping-nested-span-cannot-eval",
              review_import(rootN4c, prn4c.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N4-mapping-nested-span-no-acceptance", not (n4c_dir / "acceptance.json").is_file())

        rootN4d, mN4d = build_store(sources={"a.txt": "aaaa"})
        prn4d = plan_import(rootN4d, ["a.txt"], now=NOW, run_nonce=NONCE)
        n4d_dir = mN4d.parent / "imports" / (prn4d.run_id or "MISSING")
        inv_n4d = tomllib.loads((n4d_dir / "inventory.toml").read_text())
        inject_nested_span_text(n4d_dir / "inventory.toml", inv_n4d["fragment"][0]["span"])
        check("N4-fragment-nested-span-cannot-eval",
              review_import(rootN4d, prn4d.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        check("N4-fragment-nested-span-no-acceptance", not (n4d_dir / "acceptance.json").is_file())

        # N4 (module): an UNHASHABLE mapping origin/state ([] or {}) in a staged run is a located CANNOT-
        # EVALUATE at the review mapping-row validation, never an uncaught TypeError at the (frozenset)
        # membership test. The origin case is the real crash flip (_ORIGIN_SET is a frozenset); the state
        # case is the defensive sibling (MAPPING_STATES is a tuple, so already a finding, now guarded too).
        rootN4a, mN4a = build_store(sources={"a.txt": "aaaa"})
        prn4a = plan_import(rootN4a, ["a.txt"], now=NOW, run_nonce=NONCE)
        n4a_dir = mN4a.parent / "imports" / (prn4a.run_id or "MISSING")
        mp_n4a = tomllib.loads((n4a_dir / "mappings.toml").read_text())
        mp_n4a["mapping"][0]["origin"] = []
        (n4a_dir / "mappings.toml").write_text(_opf_emit.emit(mp_n4a), encoding="utf-8")
        check("N4-mapping-origin-unhashable-cannot-eval",
              review_import(rootN4a, prn4a.run_id, actor="R", decisions=[], now=NOW).verdict == 2)
        rootN4b, mN4b = build_store(sources={"a.txt": "aaaa"})
        prn4b = plan_import(rootN4b, ["a.txt"], now=NOW, run_nonce=NONCE)
        n4b_dir = mN4b.parent / "imports" / (prn4b.run_id or "MISSING")
        mp_n4b = tomllib.loads((n4b_dir / "mappings.toml").read_text())
        mp_n4b["mapping"][0]["state"] = []
        (n4b_dir / "mappings.toml").write_text(_opf_emit.emit(mp_n4b), encoding="utf-8")
        check("N4-mapping-state-unhashable-cannot-eval",
              review_import(rootN4b, prn4b.run_id, actor="R", decisions=[], now=NOW).verdict == 2)

        # model_proposal acceptance gate: a mapping resting in a resting state with origin=model_proposal
        # requires an explicit accept. Hand-edit one mapping row to model_proposal/ignored (a quarantine AND
        # resting state, so lf-bijection stays coherent), then accept -> clean, reject -> finding. Since
        # review now delegates to the staged-run gate (R4-F1), refresh the mappings.toml report digest so the
        # gate's artifact-digest-integrity stays green and this run remains a coherent, reviewable run.
        rootRM, mRM = build_store(sources={"a.txt": "aaaa"})
        prm = plan_import(rootRM, ["a.txt"], now=NOW, run_nonce=NONCE)
        rm_dir = mRM.parent / "imports" / (prm.run_id or "MISSING")
        mp_rm = tomllib.loads((rm_dir / "mappings.toml").read_text())
        mp_rm["mapping"][0]["origin"] = "model_proposal"
        mp_rm["mapping"][0]["state"] = "ignored"
        (rm_dir / "mappings.toml").write_text(_opf_emit.emit(mp_rm), encoding="utf-8")
        rewrite_report_digest(rm_dir, "mappings.toml")
        check("RM-model-proposal-accept-clean",
              review_import(rootRM, prm.run_id, actor="R", decisions=all_decisions(rm_dir, "accept"),
                            now=NOW).verdict == 0)
        check("RM-model-proposal-reject-finding",
              review_import(rootRM, prm.run_id, actor="R", decisions=all_decisions(rm_dir, "reject"),
                            now=NOW).verdict == 1)

        # R4-F1: review REQUIRES the staged-run gate to pass before capturing acceptance, so a run that clears
        # the loader's fast pre-checks but is diagnosed by check_staged_run is now CANNOT-EVALUATE (verdict 2)
        # with NO acceptance written, rather than capturing acceptance on an incoherent run. Each discriminator
        # is a single mutation reviewed with otherwise-VALID decisions: before the fix each captured acceptance
        # (verdict 0); after it, the inherited gate finding makes review cannot-evaluate. A COHERENT control run
        # still reviews cleanly.
        # (a) run.toml source records malformed (source = ["invalid"]): the loader accepts source-is-a-list,
        #     but the gate's mapping-totality/source-preservation checks diagnose the malformed record. Refresh
        #     run.toml's report digest so a coherence check, not artifact-digest-integrity, is what fires.
        rootG1, mG1 = build_store(sources={"a.txt": "aaaa"})
        prg1 = plan_import(rootG1, ["a.txt"], now=NOW, run_nonce=NONCE)
        g1_dir = mG1.parent / "imports" / (prg1.run_id or "MISSING")
        g1_decs = all_decisions(g1_dir)
        run_g1 = tomllib.loads((g1_dir / "run.toml").read_text())
        run_g1["source"] = ["invalid"]
        (g1_dir / "run.toml").write_text(_opf_emit.emit(run_g1), encoding="utf-8")
        rewrite_report_digest(g1_dir, "run.toml")
        check("G1-gate-malformed-source-cannot-eval",
              review_import(rootG1, prg1.run_id, actor="R", decisions=g1_decs, now=NOW).verdict == 2)
        check("G1-gate-malformed-source-no-acceptance", not (g1_dir / "acceptance.json").is_file())
        # (b) a run.toml source path with no covering mapping (its declared size exceeds the tiled spans): the
        #     loader does not cross-check run.toml sizes against mappings, but the gate's mapping-totality does.
        #     Its sha256 is unchanged, so source-preservation stays green and only mapping-totality fires.
        rootG2, mG2 = build_store(sources={"a.txt": "aaaa"})
        prg2 = plan_import(rootG2, ["a.txt"], now=NOW, run_nonce=NONCE)
        g2_dir = mG2.parent / "imports" / (prg2.run_id or "MISSING")
        g2_decs = all_decisions(g2_dir)
        run_g2 = tomllib.loads((g2_dir / "run.toml").read_text())
        run_g2["source"][0]["size"] = run_g2["source"][0]["size"] + 1
        (g2_dir / "run.toml").write_text(_opf_emit.emit(run_g2), encoding="utf-8")
        rewrite_report_digest(g2_dir, "run.toml")
        check("G2-gate-uncovered-source-cannot-eval",
              review_import(rootG2, prg2.run_id, actor="R", decisions=g2_decs, now=NOW).verdict == 2)
        check("G2-gate-uncovered-source-no-acceptance", not (g2_dir / "acceptance.json").is_file())
        # (c) proposals.toml schema = 999: the loader checks only the proposals run_id binding, but the gate's
        #     proposals-artifact check rejects a wrong schema. proposals.toml is not in report's artefact list,
        #     so no digest refresh is needed and only proposals-artifact fires.
        rootG3, mG3 = build_store(sources={"a.txt": "aaaa"})
        prg3 = plan_import(rootG3, ["a.txt"], now=NOW, run_nonce=NONCE)
        g3_dir = mG3.parent / "imports" / (prg3.run_id or "MISSING")
        g3_decs = all_decisions(g3_dir)
        props_g3 = tomllib.loads((g3_dir / "proposals.toml").read_text())
        props_g3["schema"] = 999
        (g3_dir / "proposals.toml").write_text(_opf_emit.emit(props_g3), encoding="utf-8")
        check("G3-gate-bad-proposals-schema-cannot-eval",
              review_import(rootG3, prg3.run_id, actor="R", decisions=g3_decs, now=NOW).verdict == 2)
        check("G3-gate-bad-proposals-schema-no-acceptance", not (g3_dir / "acceptance.json").is_file())
        # (d) control: a COHERENT run (no mutation) still reviews cleanly, gate-pass then decisions-valid, so
        #     acceptance is written (verdict 0). This anchors the flip: the mutations above, not the delegation
        #     itself, are what turn review cannot-evaluate.
        rootG4, mG4 = build_store(sources={"a.txt": "aaaa"})
        prg4 = plan_import(rootG4, ["a.txt"], now=NOW, run_nonce=NONCE)
        g4_dir = mG4.parent / "imports" / (prg4.run_id or "MISSING")
        rrg4 = review_import(rootG4, prg4.run_id, actor="R", decisions=all_decisions(g4_dir), now=NOW)
        check("G4-gate-coherent-run-clean", rrg4.verdict == 0 and (g4_dir / "acceptance.json").is_file())
        # (e) R5-F1 defence-in-depth: if check_staged_run itself RAISES (a shape the gate does not guard),
        #     review must return CANNOT-EVALUATE (verdict 2) with NO acceptance and no propagated
        #     exception, never crash. Monkeypatch the lazily-imported gate to raise, review a COHERENT run,
        #     then restore the original in a finally so the patch cannot leak to later checks.
        rootG5, mG5 = build_store(sources={"a.txt": "aaaa"})
        prg5 = plan_import(rootG5, ["a.txt"], now=NOW, run_nonce=NONCE)
        g5_dir = mG5.parent / "imports" / (prg5.run_id or "MISSING")
        g5_decs = all_decisions(g5_dir)
        import check_opf_import as _chk_g5
        _orig_g5 = _chk_g5.check_staged_run

        def _raise_g5(_run_dir):
            raise RuntimeError("G5: injected gate crash")

        _chk_g5.check_staged_run = _raise_g5
        try:
            rrg5 = review_import(rootG5, prg5.run_id, actor="R", decisions=g5_decs, now=NOW)
        finally:
            _chk_g5.check_staged_run = _orig_g5
        check("G5-gate-raise-cannot-eval", rrg5.verdict == 2)
        check("G5-gate-raise-no-acceptance", not (g5_dir / "acceptance.json").is_file())
        check("G5-gate-restored", _chk_g5.check_staged_run is _orig_g5)
        # (f) R7-F1 widened defence-in-depth: the WHOLE gate-delegation step (load + call + result-read) is
        #     fail-closed, not just the call G5 covers. Here the gate RETURNS a NON-DICT (a contract
        #     violation) instead of raising: the result-read `gate_results.items()` would raise an uncaught
        #     AttributeError if it sat OUTSIDE the widened try, so review must still return CANNOT-EVALUATE
        #     (verdict 2) with NO acceptance and no propagated exception. Both a None and an empty-list return
        #     are exercised; restore in a finally so the patch cannot leak to later checks. FLIP: move the
        #     gate_findings result-read back outside the try and these G6 checks crash with an uncaught
        #     AttributeError instead of returning verdict 2.
        for _g6_tag, _g6_ret in (("none", None), ("list", [])):
            rootG6, mG6 = build_store(sources={"a.txt": "aaaa"})
            prg6 = plan_import(rootG6, ["a.txt"], now=NOW, run_nonce=NONCE)
            g6_dir = mG6.parent / "imports" / (prg6.run_id or "MISSING")
            g6_decs = all_decisions(g6_dir)
            import check_opf_import as _chk_g6
            _orig_g6 = _chk_g6.check_staged_run
            _chk_g6.check_staged_run = lambda _run_dir, _r=_g6_ret: _r
            try:
                rrg6 = review_import(rootG6, prg6.run_id, actor="R", decisions=g6_decs, now=NOW)
            finally:
                _chk_g6.check_staged_run = _orig_g6
            check("G6-{}-gate-nondict-cannot-eval".format(_g6_tag), rrg6.verdict == 2)
            check("G6-{}-gate-nondict-no-acceptance".format(_g6_tag),
                  not (g6_dir / "acceptance.json").is_file())
            check("G6-{}-gate-restored".format(_g6_tag), _chk_g6.check_staged_run is _orig_g6)
        # (f2) R8-F1 widened defence-in-depth: the gate_findings result-read AND the ", ".join(gate_findings)
        #     FINDING-raise now sit INSIDE the widened guard. A gate result dict with a NON-STRING failing-check
        #     id (a first-party contract violation) would raise an uncaught TypeError at the join if it sat
        #     outside the try; review must instead return CANNOT-EVALUATE (verdict 2) with NO acceptance and no
        #     propagated exception. Monkeypatch the gate to return {123: (False, ...)} (a non-string failing id;
        #     sorted() succeeds on the single element, so the crash lands at the join, not the sort); restore in
        #     a finally. FLIP: move the `if gate_findings: raise` back OUTSIDE the try and this crashes with an
        #     uncaught TypeError instead of verdict 2.
        rootG8, mG8 = build_store(sources={"a.txt": "aaaa"})
        prg8 = plan_import(rootG8, ["a.txt"], now=NOW, run_nonce=NONCE)
        g8_dir = mG8.parent / "imports" / (prg8.run_id or "MISSING")
        g8_decs = all_decisions(g8_dir)
        import check_opf_import as _chk_g8
        _orig_g8 = _chk_g8.check_staged_run
        _chk_g8.check_staged_run = lambda _run_dir: {123: (False, "a non-string failing-check id")}
        try:
            rrg8 = review_import(rootG8, prg8.run_id, actor="R", decisions=g8_decs, now=NOW)
        finally:
            _chk_g8.check_staged_run = _orig_g8
        check("G8-gate-nonstring-id-cannot-eval", rrg8.verdict == 2)
        check("G8-gate-nonstring-id-no-acceptance", not (g8_dir / "acceptance.json").is_file())
        check("G8-gate-restored", _chk_g8.check_staged_run is _orig_g8)
        # (g) R7-F2 best-effort-import contract: _gather_review_context promises every field is best-effort
        #     and NEVER required (any failure records ""). Its lazy `import _opf_observe` must sit INSIDE the
        #     git-discovery try, so a broken/absent first-party _opf_observe degrades git_identity to ""
        #     rather than propagating. Force that import to fail (sys.modules[...] = None makes
        #     `import _opf_observe` raise ImportError) and assert the helper RETURNS with git_identity "" while
        #     os_user/hostname stay best-effort populated. Restore sys.modules in a finally. FLIP: move the
        #     import back OUTSIDE the try and this call raises an uncaught ImportError, crashing the self-test
        #     (rc=1) instead of degrading.
        rootG7, mG7 = build_store(sources={"a.txt": "aaaa"})
        resolG7 = _opf_store.resolve_store(rootG7)

        def _norm_g7(v):  # mirror _gather_review_context's control-char strip + length cap
            return "".join(ch for ch in v if ord(ch) >= 0x20 and ord(ch) != 0x7f)[:256]

        import getpass as _getpass_g7
        import socket as _socket_g7
        try:
            _user_g7 = _norm_g7(_getpass_g7.getuser())
        except Exception:  # noqa: BLE001  mirror the helper's best-effort degrade
            _user_g7 = ""
        try:
            _host_g7 = _norm_g7(_socket_g7.gethostname())
        except Exception:  # noqa: BLE001
            _host_g7 = ""
        _missing_g7 = object()
        _saved_observe_g7 = sys.modules.get("_opf_observe", _missing_g7)
        sys.modules["_opf_observe"] = None  # makes `import _opf_observe` raise ImportError
        try:
            ctxG7 = _gather_review_context(resolG7)
        finally:
            if _saved_observe_g7 is _missing_g7:
                del sys.modules["_opf_observe"]
            else:
                sys.modules["_opf_observe"] = _saved_observe_g7
        check("G7-observe-import-fail-returns-three-keys",
              set(ctxG7) == {"os_user", "git_identity", "hostname"})
        check("G7-observe-import-fail-git-identity-degraded-empty", ctxG7["git_identity"] == "")
        check("G7-observe-import-fail-os-user-best-effort", ctxG7["os_user"] == _user_g7)
        check("G7-observe-import-fail-hostname-best-effort", ctxG7["hostname"] == _host_g7)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-IMPORT SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-IMPORT SELF-TEST: PASS ({} import operation-layer + staging checks)".format(checked[0]))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_import.py --self-test (a library module; the import verb stays unwired)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
