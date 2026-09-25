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
  - `apply_import(...) -> ApplyResult`: APPLY-PROMOTION (OPF-IMPORT-APPLY, PR-C). Validates an accepted
    staged run and promotes its candidate to the active store through the crash-durable, lock-guarded
    `_journal.run_transaction` cutover (D1 relocates the preserved sources + acceptance to a durable archive;
    D2/D3 keep the journal, the idempotency signal, and `transaction.toml` outside `.working/`; D4 composes
    the candidate through validate_store over a tempdir preview; D5 requires the assembled views reproducible;
    the staging run dir is deleted as the terminal journaled step). Fail-closed throughout. `check_opf_import.py`
    is the accompanying gate over a staged run, the scan layer, and the per-run transaction record.

Offline, stdlib only, fail-closed. This module takes an operator-enumerated set of legacy SOURCE files
and an untrusted MAPPING PLAN, validates both, mints record ids from the store's counters, and STAGES a
byte-canonical candidate under `.working/imports/<run-id>/` (store scope, a sibling of the machine subdir,
which carries TOML records only; spec 14.1). Its writes are confined to the `.working/imports/` staging ROOT
(created if absent; spec 14 stages under `.working/imports/<run-id>/`, so the staging root is part of the
staging area, not the active store) and the new run directory beneath it: the active store, its
`counters.toml`, its indexes, its archive, and the sources are read-only inputs to scan/plan/review/stage; only the apply-promotion cutover (apply_import, PR-C) writes them. It
composes U1 (store resolution + manifest), U2 (record envelope + counters + id helpers), U3 (the worklog
release-boundary gate), and U8 (the constrained-subset canonical emitter) rather than re-deriving them.

Sequencing (build plan): U7 landed the operation layer as MODULE + SELF-TEST first, like U4/U5; the live
`import` verb wiring landed next (OPF-IMPORT-VERB, opf.py `_cmd_import` + its main() dispatch branch),
composing this operation layer unchanged, and PR-C landed apply-promotion (`apply_import`) as the real,
journaled, verified-restore cutover that promotes an accepted run to the active store. A bare `opf.py import`
with no mode still exits 2 and stages nothing (the grammar's exactly-one-mode rule).

Staging still mutates nothing durable: `stage_import`/`plan_import` stage PROPOSALS only, and the sole durable
reservation is counters.toml, which PROMOTION (`apply_import`) advances under the import writer lock (a single
writer), alongside merging the candidate indexes, flipping `import_status`, relocating the preserved sources,
and deleting the staging run dir, all in one journaled transaction. The R6 sibling-run union is checked once
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
import time
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
    sys.exit("error: opf/tools/_opf_import.py requires Python 3.11+ (tomllib).")


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

# --- apply-promotion (OPF-IMPORT-APPLY, PR-C) fixed locations -----------------------------------------
# Every apply-promotion control artefact lives at the STORE ROOT under `.aiqt/`, OUTSIDE `.working/`, so it
# (a) survives the terminal deletion of the staging run dir and (b) never enters the store containment walk
# (which is rooted at `.working/`), exactly as migrate.py's `.aiqt/migration/journal` does (D2/D3, the ruled
# PR-C design). All are store-root relative, contained no-follow paths (reachable from the store-root fd the
# journal + apply_ops walk). The idempotency truth is the crash-durable `_journal` frames; `transaction.toml`
# is the gate-readable projection of the terminal state, per run.
IMPORT_OPS_REL = ".aiqt/import"                        # import-promotion ops root (journal + per-run txn record)
IMPORT_JOURNAL_REL = ".aiqt/import/journal"            # the crash-durable journal root (txn dirs beneath it)
IMPORT_ARCHIVE_REL = ".aiqt/import-archive"            # D1: durable preserved-source + acceptance archive root
TRANSACTION_NAME = "transaction.toml"
TRANSACTION_FORMAT = "opf.import.transaction/v1"
# The state machine of the per-run transaction record (the gate-readable projection): prepared is written
# before publication, published during the mutation, complete on the validated finish. A shape-valid record
# (see _validate_transaction_record) that binds this run and is present at `complete` is the idempotency
# signal (a re-apply is a noop_already_complete); a malformed or foreign record fails closed, never a false
# no-op (PRC-F2).
_TRANSACTION_STATES = ("prepared", "published", "complete")

# D4 composition gate: the check ids whose CANNOT-EVALUATE the disclosed-benign set tolerates over the
# assembled candidate PREVIEW. The git-observation checks cannot evaluate when there is no live git
# observation to read: over the throwaway tempdir preview (not a git repo) and, for post-publish validation,
# when apply supplies observations=None (mid-transaction, no git read). C-SYNC-AGREE is such an
# observation-dependent check (it needs the actual-remote observation for a relocated local-only store), so
# its observation-MISSING cannot-evaluate is deferred to the authoritative `opf doctor` run like the others;
# its real FINDINGS (an unrecorded remote, a remote-naming pointer) are NOT cants and still block (PRC-F5a:
# this closes the false post-publish failure a relocated local-only pointer store produced with
# observations=None). C-RECORDS / C-PERRECORD-RECONCILE / C-HISTORY-RESURRECTION cannot evaluate when the
# store carries importer-tier legacy_fragment records whose record schema the baseline validator defers
# (spec 8.5 / _opf_check._schema_deferred). ANY OTHER cannot-evaluate, or ANY INVALID finding, is a real
# composition failure that aborts the promotion (fail-closed). Only the pre-render D4 pass may defer
# attributed C-VIEW-DRIFT findings; full D4 is required after the view overlay.
_PREVIEW_GIT_OBSERVATION_CANTS = frozenset({
    "C-TRACKED", "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-HISTORY-RESURRECTION", "C-SYNC-AGREE"})
_PREVIEW_SCHEMA_DEFERRAL_CANTS = frozenset({
    "C-RECORDS", "C-PERRECORD-RECONCILE", "C-HISTORY-RESURRECTION"})
_PREVIEW_BENIGN_CANTS = _PREVIEW_GIT_OBSERVATION_CANTS | _PREVIEW_SCHEMA_DEFERRAL_CANTS

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
_IMPORTER_PROPOSAL_ORIGIN = "importer_proposal"   # MIG-PR3 decision 4: deterministic-importer suggestions,
                                                  # machine-distinguishable from AI model_proposal rows.
# The closed provenance vocabulary for a proposals.toml ROW (distinct from the plan-fragment _ORIGIN_SET,
# which governs {fragments:[{origin}]} and is unchanged). A later --review (PR-A) reads this set.
_PROPOSAL_ORIGIN_VALUES = (_MODEL_PROPOSAL_ORIGIN, _IMPORTER_PROPOSAL_ORIGIN)

# MIG-PR3 root-ingest staging: the additive artefacts a disposition plan stages beside the baseline
# candidate, and the marker that makes apply_import refuse to promote an ingest plan (PR-A/PR-C absent).
CANDIDATES_DRAFT_NAME = "candidates_draft.toml"
INGEST_ACTIONS_NAME = "ingest-actions.toml"   # presence => non-promotable ingest plan (fail-closed at apply)
INGEST_ACTIONS_FORMAT = "opf-ingest-plan-actions-v1"
_INGEST_ACTION_KINDS = ("keep", "move")

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

# --- The FROZEN ingest review-evidence bundle ------------------------------------------------------------
# The review/acceptance step validates a FROZEN staged snapshot (the ratified acceptance model), so the
# planner freezes the review evidence that ingest acceptance capture reads into ONE additive,
# store-native TOML artefact staged beside the promotion candidate. It EMBEDS the review-only inputs that
# are otherwise NOT staged (the original triaged worksheet with its recomputed digest, the ingest-options /
# --include declaration incl. explicit-dest-vs-default, the scoped resolution crosswalk, and each migrate
# row's importer selection + validated result count, preserving a zero-candidate / zero-proposal result),
# and BINDS the already-staged artefacts by DIGEST rather than duplicating them (the baseline plan via the
# report's plan_digest, the frozen inventory, ingest-actions.toml, candidates_draft.toml, proposals.toml).
# It NEVER redefines plan_digest (acceptance carries an explicit ingest/action binding, not a new digest
# authority). Its presence is ALSO a non-promotable ingest marker (recognized at apply) and routes an ingest
# run to durable ingest acceptance capture (recognized at both review entry points). Canonical _opf_emit
# TOML, matching the house convention (the plan's canonical-JSON residual is reconciled to TOML here too).
INGEST_REVIEW_NAME = "ingest-review.toml"
INGEST_REVIEW_FORMAT = "opf-ingest-review-bundle-v2"
# The ingest-only artefacts whose presence makes a run a NON-PROMOTABLE root-ingest plan in this build.
# apply_import refuses on ANY of these BEFORE any journal/lock work; the review entry points route a run
# carrying ANY of these to ingest acceptance capture (durable evidence home only), never to ordinary
# fragment review. ingest-actions.toml stays the FIRST-staged marker; the others are recognized for
# defence in depth (a partially-staged leftover, or any unforeseen ordering, is still refused; marginal
# cost one lstat each).
_INGEST_RUN_MARKERS = (INGEST_ACTIONS_NAME, CANDIDATES_DRAFT_NAME, INGEST_REVIEW_NAME)

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
    from the verdict alone (a caller reads this field); `outcome` distinguishes promoted / aborted / rejected
    / noop_already_complete, and `restore_ref` records the transaction id + journal location + observed HEAD
    the verified restore rests on. See apply_import for the fail-closed ten-step promotion."""
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
    a non-inline store. (per-record support is a disclosed follow-on.) Returns the manifest it read, so a
    caller derives the store's homes generation from this same authoritative read."""
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
    return data


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
                lf = _lf_record(rid, sp, source["sha256"], start, end, run_id, stamp, frag_body)
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


# --- shared staged-run model builders (single authority; MIG-PR4b RE-DERIVATION) ------------------------
# Each pure builder below is the ONE place a deterministic staged-run artefact's shape is defined. It is
# called by BOTH the producer (_stage_resolved / _write_run / plan_import) and the MIG-PR4b review gate
# (check_opf_import._verify_ingest_review_model), which RE-DERIVES the baseline artefacts of an ingest run
# (run.toml, plan.toml, mappings.toml, the legacy_fragment index, report.toml) from the preserved source bytes
# and requires the staged bytes to EQUAL the re-derivation, so the producer and the checker cannot drift.

def _baseline_plan(sources):
    """The deterministic whole-file BASELINE plan over enumerated sources (each a dict carrying `path` and
    `size`): one fragment per source, span [0, size), classified `unmapped` with the baseline origin (nothing
    is mechanically mapped, spec 14.1)."""
    return {"fragments": {s["path"]: [{"span": [0, s["size"]], "state": "unmapped",
                                       "origin": _BASELINE_ORIGIN}]
                          for s in sources}}


def _run_toml_model(run_id, stamp, run_nonce, sources):
    """The run.toml model: the run identity, its staging instant and nonce, and each source's identity
    {path, sha256 (bare hex), size}, sorted by path."""
    return {"schema": SCHEMA, "run_id": run_id, "staged_at": stamp, "nonce": run_nonce,
            "source": [{"path": s["path"], "sha256": s["sha256"], "size": s["size"]}
                       for s in sorted(sources, key=lambda s: s["path"])]}


def _mappings_model(mapping_states):
    """The mappings.toml model from the per-source mapping states (source_path -> [{span, state, target,
    origin}]): one row per span, sources sorted, a `target` carried only when it is a well-formed id."""
    mapping_rows = []
    for sp in sorted(mapping_states):
        for m in mapping_states[sp]:
            row = {"source_path": sp, "span": list(m["span"]), "state": m["state"],
                   "origin": m["origin"]}
            if _opf_schema._valid_id_shape(m.get("target")) is not None:
                row["target"] = m["target"]
            mapping_rows.append(row)
    return {"schema": SCHEMA, "mapping": mapping_rows}


def _counters_model(working_high):
    """The candidate/counters.toml model: the advanced high-water map."""
    return {"schema": SCHEMA, "counters": working_high}


def _lf_record(rid, sp, source_sha256, start, end, run_id, stamp, frag_body):
    """One legacy_fragment quarantine record: the minted id, the importer actor, the provenance quad
    (source_path, source_digest, span, run_id), the staging instant, and the fragment body (the source's raw
    bytes over [start, end), decoded by the caller)."""
    return {
        "id": rid, "type": LF_TYPE, "status": "quarantined",
        "title": "Legacy fragment from {} [{}:{}]".format(sp, start, end),
        "created_at": stamp, "updated_at": stamp,
        "actor": {"kind": "importer"},
        "source_path": sp, "source_digest": source_sha256,
        "span": [start, end], "run_id": run_id,
        "body": frag_body,
    }


def _run_report_model(run_id, plan_bytes, inventory_digest, state_counts, lf_present, all_body):
    """The report.toml model (the promotion-ready marker): the plan digest over the staged plan.toml bytes,
    the inventory digest, the non-zero state counts, and the digest of every staged candidate artefact in
    `all_body` (run-relative suffix -> bytes), sorted by suffix."""
    return {
        "schema": SCHEMA, "run_id": run_id, "verdict": CLEAN, "promotion_ready": True,
        "migration_incomplete": bool(lf_present),
        "plan_digest": "sha256:" + _sha256_hex(plan_bytes), "inventory_digest": inventory_digest,
        "counts": {k: v for k, v in state_counts.items() if v},
        "artifact": [{"path": suffix, "sha256": _sha256_hex(data)}
                     for suffix, data in sorted(all_body.items())],
    }


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
    files["run.toml"] = _emit_bytes(_run_toml_model(run_id, stamp, run_nonce, sources), "run.toml")
    files["plan.toml"] = plan_bytes
    files["mappings.toml"] = _emit_bytes(_mappings_model(mapping_states), "mappings.toml")
    files["candidate/counters.toml"] = _emit_bytes(_counters_model(working_high), "candidate/counters.toml")
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
    _, inventory_digest, _, _ = _build_inventory(sources)
    files_report = _emit_bytes(
        _run_report_model(run_id, plan_bytes, inventory_digest, state_counts, bool(lf_records), all_body),
        "report.toml")

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


def _sort_proposal_rows(rows):
    """The producer's DETERMINISTIC proposal order over validated, provenance-tagged rows (each carrying its
    `_origin`): (source_path bytes, span start, span end, origin). The single ordering authority, shared by
    plan_import and the MIG-PR4b review gate, which re-derives the staged proposals.toml from it."""
    return sorted(rows, key=lambda p: (p["source_path"].encode("utf-8"), p["span"][0], p["span"][1],
                                       p["_origin"]))


def _proposals_model(run_id, proposal_rows):
    """The proposals.toml model the producer stages from validated, sorted rows: each row verbatim as
    _validate_proposals normalizes it, stamped with its provenance `origin` (default model_proposal). The
    single construction authority, shared by _write_plan_artifacts and the MIG-PR4b review gate."""
    return {
        "schema": SCHEMA, "run_id": run_id,
        "proposal": [{k: v for k, v in dict(p, origin=p.get("_origin", _MODEL_PROPOSAL_ORIGIN)).items()
                      if k != "_origin"} for p in proposal_rows],
    }


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
        # E3 (C1 render fix): the source_path is UNTRUSTED staged data rendered into a Markdown code span, so
        # it is escaped for the Markdown/terminal sink here in the report BODY exactly as the ingest section
        # escapes its values (a crafted path could otherwise carry a C0/C1/DEL terminal-control byte, or a
        # backtick, into the reviewer's terminal). Applied identically at plan and gate time, so byte
        # reproduction is unaffected (a normal path is unchanged). fragment_id/digest are hex, not attacker text.
        lines.append("- `{}` [{}:{}] state=unmapped fragment_id=`{}` digest=`{}`".format(
            _ingest_md_escape(frag["source_path"]), frag["span"][0], frag["span"][1], frag["fragment_id"],
            frag["fragment_digest"]))
    lines += ["", "## Proposals (inert; require attributed acceptance)", ""]
    if not proposals:
        lines.append("- (none)")
    else:
        for p in proposals:
            suffix = " note={!r}".format(p["note"]) if p["note"] else ""
            # E3 (C1 render fix): the proposal source_path is UNTRUSTED staged data in a code span, escaped for
            # the Markdown/terminal sink like the fragment source_path above (note is already repr-escaped).
            lines.append("- `{}` [{}:{}] suggested_state={} origin={}{}".format(
                _ingest_md_escape(p["source_path"]), p["span"][0], p["span"][1], p["suggested_state"],
                p.get("_origin", _MODEL_PROPOSAL_ORIGIN), suffix))
    lines.append("")
    return "\n".join(lines) + "\n"


def _ingest_md_escape(s):
    """Display-safety encoder for an UNTRUSTED staged value rendered into IMPORT-REPORT.md: neutralize
    control / terminal-control bytes (a crafted path could otherwise carry an ANSI escape into the
    reviewer's terminal) and the Markdown code-span delimiter (a backtick could break out of a `code span`).
    Deterministic, and applied identically by the plan-time render and the gate recompute, so it never
    perturbs the byte reproduction the gate asserts (SECI-output-encoding; disclose-guard-residuals: it
    encodes for the Markdown/terminal sink, it is not a general sanitizer). Applied to the UNTRUSTED
    source_path values in BOTH renders: the ordinary report body's fragment and proposal source_paths
    (`_render_report_md`, E3) AND every untrusted value in the ingest review section (`_render_ingest_review_md`),
    so no untrusted staged value reaches the reviewer's terminal raw from either surface. It does NOT cover
    hex-only derived fields (fragment_id / digests) or the note (already repr-escaped)."""
    if not isinstance(s, str):
        s = str(s)
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "`":
            out.append("\\u0060")
        elif o < 0x20 or o == 0x7f or 0x80 <= o <= 0x9f:
            # C0 (< 0x20), DEL (0x7f) AND the C1 control range (0x80-0x9f: CSI 0x9b, OSC 0x9d, etc.), which a
            # terminal interprets as control sequences no less than C0; a crafted staged value could otherwise
            # carry a C1-introduced escape into the reviewer's terminal. Applied identically at plan and gate
            # time, so byte reproduction is unaffected.
            out.append("\\x{:02x}".format(o))
        else:
            out.append(ch)
    return "".join(out)


def _ingest_render_model(run_id, crosswalk, migrate, actions=None, candidates=None):
    """Build the deterministic ingest presentation from frozen authority.
    Every file has a disposition unit. Every migrate file has a conversion unit, including zero-result
    conversions. Display labels are derived; persisted decisions join on (kind, scope, source_path)."""
    cw = sorted(
        ({"scope": r["scope"], "source_path": r["source_path"],
          "resolved_source_path": r["resolved_source_path"], "disposition": r["disposition"]}
         for r in crosswalk),
        key=lambda r: (r["source_path"].encode("utf-8"), r["scope"], r["resolved_source_path"]))
    mig = sorted(
        ({"scope": m["scope"], "source_path": m["source_path"],
          "resolved_source_path": m["resolved_source_path"], "importer_kind": m["importer_kind"],
          "candidate_count": m["candidate_count"], "proposal_count": m["proposal_count"], "loss": m["loss"]}
         for m in migrate),
        key=lambda m: (m["source_path"].encode("utf-8"), m["scope"]))
    # Each displayed unit id is KIND-PREFIXED (`<kind>:<scope>:<source_path>`), so a conversion unit and a
    # disposition unit for a file whose name happens to spell another unit's id (`a.md#conversion` beside
    # `a.md`) can never share an id; the kind is a closed token without a colon and the scope a closed token,
    # so the prefix parse is unambiguous. Uniqueness is then ASSERTED, not assumed: a duplicate id (only a
    # duplicated frozen (scope, source_path) row can yield one) is CANNOT-EVALUATE at plan time and a located
    # FINDING at the gate, never a report that names two decisions with one id.
    units = []
    for r in cw:
        units.append({"unit_id": "disposition:{}:{}".format(r["scope"], r["source_path"]),
                      "kind": "disposition", "scope": r["scope"], "source_path": r["source_path"],
                      "disposition": r["disposition"]})
    for m in mig:
        units.append({"unit_id": "conversion:{}:{}".format(m["scope"], m["source_path"]),
                      "kind": "conversion", "scope": m["scope"], "source_path": m["source_path"],
                      "importer_kind": m["importer_kind"], "candidate_count": m["candidate_count"],
                      "proposal_count": m["proposal_count"]})
    unit_ids = [u["unit_id"] for u in units]
    if len(set(unit_ids)) != len(unit_ids):
        raise _cannot("ingest review decision-unit ids are not unique ({}); a duplicated frozen (scope, "
                      "source_path) row cannot be rendered as distinct decisions (fail-closed)".format(
                          sorted({i for i in unit_ids if unit_ids.count(i) > 1})))
    units.sort(key=lambda u: (u["unit_id"].encode("utf-8"), u["kind"]))
    return {"run_id": run_id, "crosswalk": cw, "migrate": mig, "decision_units": units,
            "actions": actions or [], "candidates": candidates or []}


def _render_ingest_review_md(review_model):
    """OPF-MIGRATE MIG-PR4b: render the DETERMINISTIC ingest review section of IMPORT-REPORT.md from the
    validated review model ONLY (a pure function of the model, per the acyclic binding graph: the report
    derives from the model and participates in no digest). Composed with `_render_report_md` at both the
    plan-time render and the gate byte-reproduction check. Reviewability is displayed SEPARATELY from
    promotion status (the section claims no validation it did not observe; a gate pass is coherence + sound
    bindings, never promotable). Disposition-row, migrate/draft/proposal, and decision-unit counts are kept
    SEPARATE (a count carries its predicate).
    Every untrusted value is escaped for the Markdown/terminal sink (_ingest_md_escape)."""
    run_id = review_model["run_id"]
    cw = review_model["crosswalk"]
    mig = review_model["migrate"]
    units = review_model["decision_units"]
    e = _ingest_md_escape
    by_disp = {}
    for r in cw:
        by_disp[r["disposition"]] = by_disp.get(r["disposition"], 0) + 1
    disp_summary = " ".join("{}={}".format(e(d), by_disp[d]) for d in sorted(by_disp)) or "none"
    draft_total = sum(m["candidate_count"] for m in mig)
    proposal_total = sum(m["proposal_count"] for m in mig)
    lines = [
        "## Ingest review: {}".format(e(run_id)),
        "",
        "GENERATED from the frozen ingest-review bundle (spec 14.1); do not hand-edit (regenerating this",
        "section byte-reproduces it). REVIEWABILITY is displayed SEPARATELY from PROMOTION status. This",
        "section is rendered at plan time and asserts NO validation: the snapshot's coherence and its",
        "bindings over the staged bytes are established only by running the staged-run import gate, and",
        "a recorded review does not authorize disposition execution in this build.",
        "",
        "reviewability: staged; semantic validation required (run the staged-run import gate)",
        "promotion status: refused (disposition execution is not available in this build)",
        "",
        "### Dispositions ({} rows: {})".format(len(cw), disp_summary),
        "",
    ]
    for r in cw:
        lines.append("- `{}` scope={} disposition={} resolved=`{}`".format(
            e(r["source_path"]), e(r["scope"]), e(r["disposition"]), e(r["resolved_source_path"])))
    lines += ["", "### Migrate importers ({} rows; drafts={} proposals={})".format(
        len(mig), draft_total, proposal_total), ""]
    if not mig:
        lines.append("- (none)")
    else:
        for m in mig:
            lines.append("- `{}` importer={} candidates={} proposals={}".format(
                e(m["source_path"]), e(m["importer_kind"]), m["candidate_count"], m["proposal_count"]))
    lines += ["", "### Decision units ({}; derived from the frozen bundle, displayed only)".format(
        len(units)), ""]
    if not units:
        lines.append("- (none)")
    else:
        for u in units:
            if u["kind"] == "disposition":
                detail = "disposition={}".format(e(u["disposition"]))
            else:
                detail = "importer={} candidates={} proposals={}".format(
                    e(u["importer_kind"]), u["candidate_count"], u["proposal_count"])
            lines.append("- unit=`{}` kind={} scope={} source=`{}` {}".format(
                e(u["unit_id"]), e(u["kind"]), e(u["scope"]), e(u["source_path"]), detail))
    lines += ["", "### Frozen action, conversion, and loss authority", ""]
    payload = {k: review_model[k] for k in ("actions", "candidates", "migrate")}
    lines.append(e(_emit_acceptance_bytes(payload).decode("ascii").rstrip()))
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
    proposals_model = _proposals_model(run_id, proposal_rows)
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


def _write_ingest_artifacts(product_root, run_rel, run_id, candidates_draft, ingest_actions, source_sizes):
    """Stage the MIG-PR3 root-ingest artefacts (ingest-actions.toml + candidates_draft.toml) additively into
    the run dir plan_import just staged, through the same contained, no-follow, fsync'd, digest-verified
    apply_ops pass _write_plan_artifacts uses. candidates_draft.toml holds the id-less importer candidate
    drafts (records mint at apply, MIG-PR5); ingest-actions.toml records the inert keep/move pending actions
    AND is the non-promotable marker apply_import refuses on (PR-A/PR-C absent). ORDERING IS LOAD-BEARING
    (round-2 P1-1): the refusal marker is the FIRST staged ingest artefact, ordered ahead of
    candidates_draft.toml, and plan_import calls this writer BEFORE the review artefacts (inventory.toml /
    proposals.toml / IMPORT-REPORT.md), so a staging failure at ANY point leaves either a run that is not
    reviewable (no inventory/proposals, so no acceptance can be captured) or a run already carrying the
    marker (refused at apply); a promotable draft-without-marker leftover cannot exist. Neither artefact
    mutates the active store. Fail-closed on any write error, cap overflow, or non-emittable model."""
    resolution = _opf_store.resolve_store(product_root)
    if resolution.status != _opf_store.RESOLVED:
        raise _cannot("store did not resolve for the ingest-artefact write ({}: {})".format(
            resolution.status, resolution.detail))
    cand_model = {"schema": SCHEMA, "run_id": run_id, "candidate": candidates_draft}
    actions_model = {"format": INGEST_ACTIONS_FORMAT, "schema": SCHEMA, "run_id": run_id,
                     "action": ingest_actions}
    cand_bytes = _emit_bytes(cand_model, CANDIDATES_DRAFT_NAME)
    act_bytes = _emit_bytes(actions_model, INGEST_ACTIONS_NAME)
    for name, data in ((CANDIDATES_DRAFT_NAME, cand_bytes), (INGEST_ACTIONS_NAME, act_bytes)):
        if len(data) > _opf_store.MAX_STORE_READ_BYTES:
            raise _cannot("{} is {} bytes, over the {}-byte contained store-read cap (rejected at the "
                          "producer boundary)".format(name, len(data), _opf_store.MAX_STORE_READ_BYTES))
    cand_rel = run_rel + "/" + CANDIDATES_DRAFT_NAME
    act_rel = run_rel + "/" + INGEST_ACTIONS_NAME
    content = {cand_rel: cand_bytes, act_rel: act_bytes}
    # P1-1: the NON-PROMOTABLE refusal marker (ingest-actions.toml) is staged FIRST, so a failure
    # between the two ops leaves marker-without-draft (recognized and refused as an ingest run), never
    # draft-without-marker (which apply_import would not recognize as ingest).
    ops = [{"op": "create", "path": act_rel,
            "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(act_bytes)}},
           {"op": "create", "path": cand_rel,
            "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(cand_bytes)}}]

    def staged_reader(op):
        return content[op["path"]]
    try:
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} for the ingest-artefact write ({})".format(
            resolution.store_root, exc))
    try:
        _journal.apply_ops(store_root_fd, ops, staged_reader)
    except _journal.JournalError as exc:
        raise _cannot("ingest-artefact write failed ({}); the staged candidate is intact, the ingest "
                      "surface incomplete".format(exc))
    finally:
        os.close(store_root_fd)


def _ingest_run_marker(store_root_fd, run_rel, homes):
    """MIG-PR4a: return the name of the FIRST ingest-only marker present in the run dir (a no-follow
    `_lstat_contained` over `_INGEST_RUN_MARKERS`), or None when none is present. The durable home is
    consulted only in generation 2 (`homes`, the store's derived generation): in generation 1
    `.working/imported` is ordinary content, never probed. Fail-closed: a
    JournalError from the contained lstat (EACCES/ELOOP/a wrong-type control path) PROPAGATES so the
    caller routes it to CANNOT-EVALUATE, never a silent "no marker" that reads a run as promotable/
    reviewable. Recognizing the run as ingest is a no-follow inspection bound to the trusted store-root
    descriptor (symlink-resolution: never a re-resolved string-path existence check)."""
    for name in _INGEST_RUN_MARKERS:
        if _journal._lstat_contained(store_root_fd, run_rel + "/" + name) is not None:
            return name
    run_id = run_rel.rsplit("/", 1)[-1]
    if (homes == INGEST_HOMES_GENERATION
            and _journal._lstat_contained(store_root_fd, _ingest_acceptance_home(run_id)) is not None):
        return "durable import evidence"
    if homes != INGEST_HOMES_GENERATION:
        return None
    # A staged acceptance that is not a readable regular file, or does not decode, is no marker: the ordinary
    # gate diagnoses it (acceptance-*) with main's verdict routing.
    st = _journal._lstat_contained(store_root_fd, run_rel + "/" + ACCEPTANCE_NAME)
    if st is None or not stat.S_ISREG(st.st_mode):
        return None
    try:
        raw, _st = _journal._read_contained(store_root_fd, run_rel + "/" + ACCEPTANCE_NAME)
    except _journal.JournalError:
        return None
    return ACCEPTANCE_NAME if _acceptance_marks_ingest(raw) else None


def _acceptance_marks_ingest(raw):
    """Whether staged acceptance bytes carry an ingest acceptance, decoded exactly as main's ordinary
    acceptance reader decodes them (json.loads over UTF-8). Undecodable bytes are no marker; the ordinary
    grading reports them. Strict decoding (_strict_json) is for ingest evidence only."""
    try:
        acc = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return False
    return isinstance(acc, dict) and ("ingest" in acc or acc.get("format") == INGEST_ACCEPTANCE_FORMAT)


def _write_ingest_review_bundle(product_root, run_rel, run_id, review_inputs):
    """OPF-MIGRATE MIG-PR4a: FREEZE the ingest review evidence into ONE additive `ingest-review.toml` staged
    into the run dir plan_import just staged, through the SAME contained, no-follow, fsync'd, digest-verified
    apply_ops pass _write_plan_artifacts / _write_ingest_artifacts use. It never mutates the active store.

    ORDERING (round-2 P1-1 extended, ratified A5): plan_import calls this LAST, AFTER the refusal marker
    (ingest-actions.toml, staged first) and every required payload (candidates_draft.toml, inventory.toml,
    proposals.toml, report.toml) are durable. The bundle then RE-READS each payload (a marker alone is
    insufficient) and BINDS it by its digest recomputed over the STAGED bytes, and RE-CHECKS the staged
    source identities (run.toml) against the frozen resolution crosswalk BEFORE it finalizes, so a bundle
    is never finalized over an incomplete or drifted snapshot. It BINDS the baseline plan and the inventory
    by the report's OWN digests (A1: it never redefines plan_digest). It EMBEDS the review-only inputs that
    are NOT otherwise staged: the original triaged worksheet with its recomputed digest (A2), the
    ingest-options / --include declaration incl. explicit-dest-vs-default (a move option's absent dest_path
    IS the archive default), the scoped resolution crosswalk ((scope, source_path) -> resolved
    product-relative source), and each migrate row's importer selection + validated result counts (a
    zero-candidate / zero-proposal migrate is preserved as an explicit row). Fail-closed throughout: any
    missing payload, unreadable record, source-identity drift, cap overflow, or non-emittable model is
    CANNOT-EVALUATE (an _StageError the caller maps uniformly), leaving the run non-promotable."""
    resolution = _opf_store.resolve_store(product_root)
    if resolution.status != _opf_store.RESOLVED:
        raise _cannot("store did not resolve for the ingest review-bundle write ({}: {})".format(
            resolution.status, resolution.detail))
    try:
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} for the ingest review-bundle write ({})".format(
            resolution.store_root, exc))
    try:
        # 1. BIND the baseline plan + inventory by the report's OWN digests (never redefine plan_digest).
        report = _read_toml(store_root_fd, run_rel + "/report.toml")
        if not isinstance(report, dict):
            raise _cannot("ingest review bundle: staged run has no readable report.toml (cannot bind)")
        plan_digest = report.get("plan_digest")
        inventory_digest = report.get("inventory_digest")
        for label, dig in (("plan_digest", plan_digest), ("inventory_digest", inventory_digest)):
            if not (isinstance(dig, str) and _DIGEST_RE.match(dig)):
                raise _cannot("ingest review bundle: report.toml {} is missing or malformed "
                              "(cannot bind the frozen snapshot)".format(label))
        # 2. RE-READ each required staged payload and BIND it by its digest recomputed over the staged bytes
        #    (a marker alone is insufficient): absence/unreadability is fail-closed CANNOT-EVALUATE.
        bound = {}
        for name in (INGEST_ACTIONS_NAME, CANDIDATES_DRAFT_NAME, PROPOSALS_NAME, INVENTORY_NAME):
            try:
                data, _pst = _journal._read_contained(store_root_fd, run_rel + "/" + name)
            except _journal.JournalError as exc:
                raise _cannot("ingest review bundle: required payload {} is missing or unreadable ({}); "
                              "the frozen snapshot is incomplete (fail-closed)".format(name, exc))
            bound[name] = "sha256:" + _sha256_hex(data)
            if name == INVENTORY_NAME:
                _inventory_bytes = data
        # 3. RE-CHECK staged source identities against the frozen crosswalk over BOTH staged source records.
        #    inventory.toml's identities are parsed from the SAME bytes bound in step 2 (never a second read),
        #    so the identities validated are provably the bytes whose digest the bundle binds: a split-read
        #    between the binding read and the identity read cannot bind one snapshot while validating another.
        #    run.toml is not digest-bound, so it is read once here. A duplicate source-path row is rejected so
        #    a last-wins reduction cannot mask a drifted non-final occurrence (F-MIG-PR4A-DUP-PATH-MASK).
        expected = review_inputs["expected"]
        try:
            _inventory_doc = tomllib.loads(_inventory_bytes.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            # RecursionError too (F-TOML-BARE-VALUEERROR-CLASS): tomllib raises it, a RuntimeError and not a
            # ValueError, on a deeply nested array or inline table; _read_toml's contained reader maps it too.
            # ValueError family, at the PARSE locus (decode + tomllib.loads over the in-memory staged bytes):
            # UnicodeDecodeError and tomllib.TOMLDecodeError are ValueError subclasses, and tomllib ALSO
            # raises a BARE ValueError on an integer literal over CPython's 4300-digit string-conversion
            # ceiling. _read_toml converts the same family to CANNOT-EVALUATE at its own parse locus; match it
            # so a huge-integer staged inventory.toml fails closed to verdict 2 rather than raising. The try
            # wraps only the parse, so no unrelated internal ValueError is laundered (no-concealed-failure).
            raise _cannot("ingest review bundle: staged {} is unreadable ({}); the frozen snapshot is "
                          "incomplete (fail-closed)".format(INVENTORY_NAME, exc))
        for _src_name, _src_doc in (("run.toml", _read_toml(store_root_fd, run_rel + "/run.toml")),
                                    (INVENTORY_NAME, _inventory_doc)):
            srcs = _src_doc.get("source") if isinstance(_src_doc, dict) else None
            if not isinstance(srcs, list):
                raise _cannot("ingest review bundle: staged {} carries no source list (cannot re-check "
                              "the frozen source identities)".format(_src_name))
            staged_ident = {}
            for s in srcs:
                if not (isinstance(s, dict) and isinstance(s.get("path"), str)
                        and isinstance(s.get("sha256"), str) and type(s.get("size")) is int):
                    raise _cannot("ingest review bundle: staged {} carries a malformed source row "
                                  "(cannot re-check the frozen source identities)".format(_src_name))
                staged_ident[s["path"]] = ("sha256:" + s["sha256"], s["size"])
            if len(staged_ident) != len(srcs):
                raise _cannot("ingest review bundle: staged {} carries a duplicate source path (a last-wins "
                              "reduction could mask a drifted row; fail-closed)".format(_src_name))
            if set(staged_ident) != set(expected):
                raise _cannot("ingest review bundle: staged {} source set {} does not equal the frozen "
                              "crosswalk set {} (the snapshot drifted under staging; fail-closed)".format(
                                  _src_name, sorted(staged_ident), sorted(expected)))
            for resolved, ident in expected.items():
                if staged_ident.get(resolved) != ident:
                    raise _cannot("ingest review bundle: staged {} source {!r} identity {} does not match "
                                  "the frozen crosswalk identity {} (the snapshot drifted under staging; "
                                  "fail-closed)".format(_src_name, resolved, staged_ident.get(resolved), ident))
    finally:
        os.close(store_root_fd)

    # 4. ASSEMBLE the bundle (A1 canonicalization/version: a closed format token + schema version).
    bundle = {
        "format": INGEST_REVIEW_FORMAT, "schema": SCHEMA, "run_id": run_id,
        "include_declared": review_inputs["include"] is not None,
        "include": list(review_inputs["include"] or []),
        "binding": {
            "plan_digest": plan_digest, "inventory_digest": inventory_digest,
            "ingest_actions_digest": bound[INGEST_ACTIONS_NAME],
            "candidates_draft_digest": bound[CANDIDATES_DRAFT_NAME],
            "proposals_digest": bound[PROPOSALS_NAME],
            "inventory_toml_digest": bound[INVENTORY_NAME],
        },
        "worksheet": review_inputs["worksheet"],
        "options": review_inputs["options"],
        "crosswalk": review_inputs["crosswalk"],
        "migrate": review_inputs["migrate"],
    }
    bundle_bytes = _emit_bytes(bundle, INGEST_REVIEW_NAME)
    if len(bundle_bytes) > _opf_store.MAX_STORE_READ_BYTES:
        raise _cannot("{} is {} bytes, over the {}-byte contained store-read cap; the frozen review bundle "
                      "would be unreadable (rejected at the producer boundary)".format(
                          INGEST_REVIEW_NAME, len(bundle_bytes), _opf_store.MAX_STORE_READ_BYTES))
    # 5. STAGE additively via the same contained, no-follow, fsync'd, digest-verified apply_ops primitive.
    rel = run_rel + "/" + INGEST_REVIEW_NAME
    ops = [{"op": "create", "path": rel,
            "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(bundle_bytes)}}]

    def staged_reader(op):
        return bundle_bytes
    try:
        store_root_fd = _opf_store._open_store_root_fd(
            resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} for the ingest review-bundle write ({})".format(
            resolution.store_root, exc))
    try:
        _journal.apply_ops(store_root_fd, ops, staged_reader)
    except _journal.JournalError as exc:
        raise _cannot("ingest review-bundle write failed ({}); the staged candidate is intact, the review "
                      "surface incomplete".format(exc))
    finally:
        os.close(store_root_fd)


def _load_staged_ingest_for_review(store_root_fd, run_rel):
    """Read the bounded frozen bundle without following links.
    A missing bundle returns None. An unsupported version or malformed bundle refuses with fresh-run
    advice. Structural validity is not semantic validity; the staged-run gate recomputes its bindings."""
    bundle = _read_toml(store_root_fd, run_rel + "/" + INGEST_REVIEW_NAME)
    if bundle is None:
        return None
    return _validate_staged_ingest_bundle(bundle, run_rel.rsplit("/", 1)[-1])


# Round-4 F5: the CLOSED shapes of the frozen ingest-review.toml bundle (the top-level table and its binding
# table), exactly the keys _write_ingest_review_bundle assembles. The structural validator refuses a key
# outside them, so a field the producer never writes can never ride along unexamined.
_INGEST_REVIEW_KEYS = frozenset(("format", "schema", "run_id", "include_declared", "include", "binding",
                                 "worksheet", "options", "crosswalk", "migrate"))
_INGEST_BINDING_KEYS = frozenset(("plan_digest", "inventory_digest", "ingest_actions_digest",
                                  "candidates_draft_digest", "proposals_digest", "inventory_toml_digest"))


def _validate_staged_ingest_bundle(bundle, run_id):
    """MIG-PR4b: the PURE structural validator of a parsed `ingest-review.toml` bundle for the run named
    `run_id` (no filesystem read), shared by `_load_staged_ingest_for_review` (the store-bound review path)
    and the staged-run gate, which reads the bundle through its OWN descriptor bound to the SUPPLIED run
    directory (check_opf_import) so the gate never classifies a run by a reconstructed conventional path.
    Returns the bundle when structurally well-formed; raises CANNOT-EVALUATE when it is malformed."""
    if not isinstance(bundle, dict):
        raise _cannot("ingest review bundle: {} is not a table (malformed)".format(INGEST_REVIEW_NAME))
    unknown = set(bundle) - _INGEST_REVIEW_KEYS
    if unknown:
        raise _cannot("ingest review bundle: carries field(s) outside the producer's closed shape: {} "
                      "(malformed)".format(", ".join(_opf_store._sorted_key_names(unknown))))
    if bundle.get("format") != INGEST_REVIEW_FORMAT:
        raise _cannot("ingest review bundle: unsupported format; re-plan from original inputs and retain "
                      "the old run as evidence (required {!r})".format(INGEST_REVIEW_FORMAT))
    if not (type(bundle.get("schema")) is int and bundle.get("schema") == SCHEMA):
        raise _cannot("ingest review bundle: schema is not {!r} (malformed)".format(SCHEMA))
    if bundle.get("run_id") != run_id:
        raise _cannot("ingest review bundle: run_id does not name this run dir (malformed)")
    binding = bundle.get("binding")
    if not isinstance(binding, dict):
        raise _cannot("ingest review bundle: binding is not a table (malformed)")
    unknown = set(binding) - _INGEST_BINDING_KEYS
    if unknown:
        raise _cannot("ingest review bundle: binding carries field(s) outside the producer's closed shape: {} "
                      "(malformed)".format(", ".join(_opf_store._sorted_key_names(unknown))))
    for k in sorted(_INGEST_BINDING_KEYS):
        v = binding.get(k)
        if not (isinstance(v, str) and _DIGEST_RE.match(v)):
            raise _cannot("ingest review bundle: binding.{} is missing or not a 'sha256:'+64-hex "
                          "digest (malformed)".format(k))
    if not isinstance(bundle.get("include_declared"), bool):
        raise _cannot("ingest review bundle: include_declared is not a bool (malformed)")
    inc = bundle.get("include")
    if not (isinstance(inc, list) and all(isinstance(p, str) for p in inc)):
        raise _cannot("ingest review bundle: include is not a list of strings (malformed)")
    ws = bundle.get("worksheet")
    if not (isinstance(ws, dict) and isinstance(ws.get("format"), str)
            and type(ws.get("schema")) is int and isinstance(ws.get("row"), list)
            and all(isinstance(r, dict) for r in ws["row"])
            and isinstance(ws.get("worksheet_digest"), str) and _DIGEST_RE.match(ws["worksheet_digest"])):
        raise _cannot("ingest review bundle: worksheet is not a well-formed embedded worksheet (malformed)")
    opts = bundle.get("options")
    if not (isinstance(opts, dict) and isinstance(opts.get("format"), str)
            and type(opts.get("schema")) is int and isinstance(opts.get("option"), list)
            and all(isinstance(o, dict) for o in opts["option"])):
        raise _cannot("ingest review bundle: options is not a well-formed embedded options doc (malformed)")
    cross = bundle.get("crosswalk")
    if not isinstance(cross, list):
        raise _cannot("ingest review bundle: crosswalk is not an array (malformed)")
    for i, row in enumerate(cross):
        if not (isinstance(row, dict)
                and all(isinstance(row.get(k), str)
                        for k in ("scope", "source_path", "resolved_source_path", "disposition"))):
            raise _cannot("ingest review bundle: crosswalk[{}] is malformed".format(i))
    mig = bundle.get("migrate")
    if not isinstance(mig, list):
        raise _cannot("ingest review bundle: migrate is not an array (malformed)")
    for i, row in enumerate(mig):
        if not (isinstance(row, dict)
                and all(isinstance(row.get(k), str)
                        for k in ("scope", "source_path", "resolved_source_path", "importer_kind"))
                and all(type(row.get(k)) is int and row.get(k) >= 0
                        for k in ("candidate_count", "proposal_count"))):
            raise _cannot("ingest review bundle: migrate[{}] is malformed".format(i))
        import _opf_importers
        loss_findings = _opf_importers._validate_source_entry(row.get("loss"), "migrate[{}].loss".format(i))
        if loss_findings:
            raise _cannot("; ".join(loss_findings))
    return bundle


def plan_import(product_root, import_set, *, proposals=None, importer_proposals=None,
                candidates_draft=None, ingest_actions=None, ingest_review_inputs=None, now, run_nonce):
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
    the acceptance-capture + apply-promotion unit: an ATTRIBUTED human acceptance is captured by `opf import
    --review` (a canonical acceptance.json with an origin/provenance tag; PR-A) and enforced at apply-promotion
    (PR-C); plan itself never auto-rests a proposal. Returns a PlanResult; fail-closed throughout.

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

        # MIG-PR3: importer-sourced proposals validate through the SAME confinement gate as model proposals
        # (guard-input-soundness: a proposal this layer stages is one _validate_proposals accepts), but carry
        # the distinct importer_proposal provenance (decision 4). Model and importer rows may not name the same
        # (source_path, span): a single logical span has one provenance, never two conflicting tags.
        model_rows = [dict(p, _origin=_MODEL_PROPOSAL_ORIGIN) for p in proposal_rows]
        imp_rows = [dict(p, _origin=_IMPORTER_PROPOSAL_ORIGIN)
                    for p in _validate_proposals(importer_proposals, source_sizes)]
        seen_span = {}
        for r in model_rows + imp_rows:
            key = (r["source_path"], r["span"][0], r["span"][1])
            if key in seen_span and seen_span[key] != r["_origin"]:
                return PlanResult(FINDING, ["proposal collision: {} [{}:{}] is tagged both {} and {} "
                                            "(one span, one provenance)".format(r["source_path"], r["span"][0],
                                             r["span"][1], seen_span[key], r["_origin"])])
            seen_span[key] = r["_origin"]
        all_proposal_rows = _sort_proposal_rows(model_rows + imp_rows)

        # Deterministic baseline plan: one whole-file fragment per source, classified `unmapped` (nothing
        # mechanically mapped). Handed to the settled staging classifier, which mints a legacy_fragment
        # quarantine record per fragment and stages the byte-canonical candidate run dir.
        plan = _baseline_plan(scan.sources)
        result = stage_import(product_root, import_set, plan, now=now, run_nonce=run_nonce)
        if result.verdict != CLEAN:
            return PlanResult(result.verdict, result.findings, run_id=result.run_id,
                              run_rel=result.run_rel, migration_incomplete=result.migration_incomplete)

        report_md = _render_report_md(scan.inventory_digest, scan.fragments, all_proposal_rows, result.run_id)
        # MIG-PR4b: for an ingest run, COMPOSE the deterministic ingest review section onto IMPORT-REPORT.md
        # at plan time, from the SAME frozen inputs the bundle will hold, so the gate's byte-reproduction
        # check (ingest-report-reproducibility) has a frozen target. The render is a pure function of the
        # (crosswalk, migrate) inputs; it participates in no digest (acyclic binding graph). Marker-first
        # staging order below is unchanged (the section is only appended to the report bytes here).
        if ingest_review_inputs is not None:
            report_md = report_md + _render_ingest_review_md(_ingest_render_model(
                result.run_id, ingest_review_inputs["crosswalk"], ingest_review_inputs["migrate"],
                ingest_actions, candidates_draft))
        # P1-1: for an ingest run the identity/refusal marker (ingest-actions.toml, staged as the FIRST
        # ingest artefact inside _write_ingest_artifacts) becomes durable BEFORE the review artefacts
        # (inventory.toml / proposals.toml) exist, so at every point where a partial staging failure can
        # leave a leftover run, that run is either not reviewable (review requires inventory + proposals,
        # so no acceptance can be captured over it) or already recognized and refused as an ingest run.
        if candidates_draft is not None or ingest_actions is not None:
            _write_ingest_artifacts(product_root, result.run_rel, result.run_id,
                                    candidates_draft or [], ingest_actions or [], source_sizes)
        _write_plan_artifacts(product_root, result.run_rel, result.run_id, scan.inventory,
                              report_md.encode("utf-8"), all_proposal_rows)
        # MIG-PR4a: FREEZE the ingest review evidence LAST, after the refusal marker and every required
        # payload above are durable, so the bundle-finalization re-read + source-identity recheck can never
        # complete over an incomplete snapshot (ratified A5; see _write_ingest_review_bundle). Additive and
        # opt-in: an ordinary (non-ingest) plan_import caller passes ingest_review_inputs=None and keeps its
        # existing behaviour exactly.
        if ingest_review_inputs is not None:
            _write_ingest_review_bundle(product_root, result.run_rel, result.run_id, ingest_review_inputs)
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



INGEST_ACCEPTANCE_FORMAT = "opf.import.acceptance/v2"
INGEST_ACCEPTANCE_BLOCK = "opf.ingest.acceptance/v1"
INGEST_HOMES_GENERATION = 2


def _strict_json(raw):
    """Bounded JSON with unique object keys and no non-finite numbers."""
    if len(raw) > _opf_store.MAX_STORE_READ_BYTES:
        raise ValueError("JSON exceeds the contained read cap")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key: " + key)
            out[key] = value
        return out

    def constant(value):
        raise ValueError("non-finite JSON number: " + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _require_gate_results(results, expected, require_pass=True):
    """A gate must return its exact registry, with strictly typed results. A caller with its own located
    failing-check message checks the shape only (require_pass=False)."""
    if (len(expected) != len(set(expected)) or not isinstance(results, dict)
            or set(results) != set(expected)):
        raise _cannot("import gate returned an incomplete or inconsistent check registry")
    for cid in expected:
        value = results[cid]
        if not (type(value) is tuple and len(value) == 2
                and type(value[0]) is bool and type(value[1]) is str):
            raise _cannot("import gate returned a malformed result for " + cid)
    failed = [cid for cid in expected if not results[cid][0]]
    if failed and require_pass:
        raise _cannot("import gate failed: " + ", ".join(failed))


def _store_homes(resolution):
    """The store's homes generation, derived ONCE per entry point from the authoritative manifest through
    _opf_store.homes_generation (never a constant); every gate call and snapshot that entry point makes
    receives this value."""
    manifest = _opf_store.load_manifest(resolution)
    if manifest.status != _opf_store.VALID or not isinstance(manifest.base, dict):
        raise _cannot("the store manifest is not VALID; its homes generation cannot be derived")
    return _opf_store.homes_generation({"opf": manifest.base})


def _import_run_locations(run_id, homes):
    """Store-relative locations of a staged import run, shared by the gate (_ingest_store_fd) and every PR4c
    entry point. Generation 1 has only the legacy location: the homes-2 staging names are ordinary store
    paths there. The first location is where staging writes a run."""
    legacy = "{}/{}".format(IMPORTS_REL, run_id)
    if type(homes) is int and homes == 1:
        return (legacy,)
    if type(homes) is int and homes == 2:
        return (legacy, _opf_store.stage_run("ingest", run_id), _opf_store.stage_run("import", run_id))
    raise ValueError("unknown homes generation: {!r}".format(homes))


def _import_run_dir(resolution, run_id, homes):
    return os.path.join(resolution.store_root, _import_run_locations(run_id, homes)[0])


def _require_review_gate(run_dir, homes):
    try:
        import check_opf_import as gate
        _require_gate_results(gate.check_staged_run(run_dir, homes=homes), gate.EXPECTED_CHECKS)
    except _StageError:
        raise
    except Exception as exc:
        raise _cannot("import gate unavailable or malformed ({!r})".format(exc))


def _validate_frozen_losses(rd, bundle, docs):
    """Validate frozen loss against staged bytes; never invoke an importer."""
    import _opf_importers as importers
    # The bytes come from the migrate row's resolved source in run.toml (the identity the gate binds to the
    # worksheet and to the preserved bytes), never from the loss entry's own digest, so a loss entry that
    # describes another preserved source fails its digest binding rather than validating against it.
    staged = {s["path"]: s["sha256"] for s in docs["run.toml"]["source"]}
    for m in bundle["migrate"]:
        loss = m["loss"]
        problems = importers._validate_source_entry(loss, "frozen loss")
        if problems:
            raise _cannot("; ".join(problems))
        bound = staged.get(m["resolved_source_path"])
        if bound is None:
            raise _cannot("frozen conversion {} resolves to no staged source".format(m["source_path"]))
        raw = rd.read_bytes("sources/" + bound)
        source = {"path": m["resolved_source_path"], "raw": raw, "sha256": _sha256_hex(raw), "size": len(raw)}
        candidates = [{k: c[k] for k in ("draft_ref", "type", "record")}
                      for c in docs[CANDIDATES_DRAFT_NAME]["candidate"]
                      if c["source_path"] == m["source_path"]]
        proposals = [{k: v for k, v in p.items() if k != "origin"}
                     for p in docs[PROPOSALS_NAME]["proposal"]
                     if p["source_path"] == m["resolved_source_path"]]
        result = importers.ImporterResult(
            CLEAN, kind=m["importer_kind"], source_path=source["path"], lossy=loss,
            candidates=candidates, proposals=proposals,
            clean=not any(s["class"] in importers._UNCLEAN_CLASSES for s in loss["span"]))
        verdict, findings = importers.validate_importer_output(result, source)
        if verdict != CLEAN:
            raise _cannot("frozen conversion {}: {}".format(m["source_path"], "; ".join(findings)))


def _ingest_acceptance_home(run_id):
    """The durable home is provisioned by layout activation, never by review."""
    return _opf_store.evidence_run("import", run_id)


def _ingest_authority(bundle, docs):
    """Derive typed decisions and removal intent from validated frozen evidence."""
    import _opf_importers as importers
    key = lambda r: (r["scope"], r["source_path"])
    actions = {key(a): a for a in docs[INGEST_ACTIONS_NAME]["action"]}
    migrations = {key(m): m for m in bundle["migrate"]}
    rows = {key(r): r for r in bundle["worksheet"]["row"]}
    units, removals = [], []
    for c in bundle["crosswalk"]:
        identity = {"scope": c["scope"], "source_path": c["source_path"]}
        row = rows[key(c)]
        action = actions.get(key(c))
        removal = None
        if c["disposition"] != "keep":
            removal = dict(identity, resolved_source_path=c["resolved_source_path"],
                           disposition=c["disposition"], sha256=row["sha256"], size=row["size"])
            if action is not None:
                removal["destination"] = action["dest_path"]
            else:
                removal["destination"] = (_ingest_acceptance_home(bundle["run_id"]) + "/originals/"
                                          + c["resolved_source_path"])
            removals.append(removal)
        authority = {"source": row, "resolved_source_path": c["resolved_source_path"],
                     "action": action, "source_removal": removal}
        units.append(dict(identity, kind="disposition", authority=authority))
        if c["disposition"] == "migrate":
            m = migrations[key(c)]
            candidates = [d for d in docs[CANDIDATES_DRAFT_NAME]["candidate"]
                          if d["source_path"] == c["source_path"]]
            proposals = [p for p in docs[PROPOSALS_NAME]["proposal"]
                         if p["source_path"] == c["resolved_source_path"]]
            unresolved = any(s["class"] in importers._UNCLEAN_CLASSES for s in m["loss"]["span"])
            outcome = "retained-quarantine" if unresolved or not candidates else "conversion-reviewed"
            units.append(dict(identity, kind="conversion", authority={
                "importer_kind": m["importer_kind"], "candidates": candidates, "proposals": proposals,
                "loss": m["loss"], "outcome": outcome}))
    units.sort(key=lambda u: (u["kind"], u["scope"], u["source_path"]))
    return units, removals


def _freeze_ingest_bytes(rd):
    """Use one bounded byte snapshot for validation, presentation, and digest construction."""
    raw, total = {}, 0
    for name, kind in sorted(rd.tree.items()):
        if kind == "file":
            data = rd.read_bytes(name)
            total += len(data)
            if total > _opf_store.MAX_STORE_READ_BYTES:
                raise _cannot("combined ingest review payload exceeds the contained read cap")
            raw[name] = data

    class Frozen:
        path = rd.path
        tree = dict(rd.tree)
        def read_bytes(self, name):
            if name not in raw:
                raise _cannot("frozen review artefact is missing or non-regular: " + name)
            return raw[name]
        def load_toml(self, name):
            try:
                return tomllib.loads(self.read_bytes(name).decode("utf-8"))
            except (ValueError, RecursionError) as exc:
                raise _cannot("frozen review artefact is malformed: {} ({})".format(name, exc))
    return Frozen()


def _ingest_snapshot(rd, homes):
    """Revalidate and hash the full frozen payload, not the narrower render projection. `homes` is the
    store's derived generation; the binding records the generation the model was validated under."""
    rd = _freeze_ingest_bytes(rd)
    import check_opf_import as gate
    bundle = _validate_staged_ingest_bundle(rd.load_toml(INGEST_REVIEW_NAME), rd.path.name)
    docs = {name: rd.load_toml(name) for name in
            ("run.toml", "report.toml", INVENTORY_NAME, INGEST_ACTIONS_NAME, CANDIDATES_DRAFT_NAME,
             PROPOSALS_NAME, "mappings.toml")}
    result = gate._verify_ingest_review_model(rd, bundle, docs["run.toml"],
                                             docs["report.toml"], docs[INVENTORY_NAME], homes)
    _require_gate_results(result, gate._INGEST_CHECK_IDS)
    # An unsupplied generation passes the scope check above only as the legacy generation 1.
    homes = 1 if homes is None else homes
    payload = {name: "sha256:" + _sha256_hex(rd.read_bytes(name))
               for name, kind in sorted(rd.tree.items()) if kind == "file"}
    units, removals = _ingest_authority(bundle, docs)
    sources = {s["path"]: rd.read_bytes("sources/" + s["sha256"]).decode("utf-8")
               for s in docs["run.toml"]["source"]}
    model = {"bundle": bundle, "payload": payload, "sources": sources,
             "units": units, "source_removals": removals}
    binding = dict(bundle["binding"], bundle_format=bundle["format"],
                   bundle_digest=payload[INGEST_REVIEW_NAME],
                   review_model_digest="sha256:" + _sha256_hex(_emit_acceptance_bytes(model)),
                   report_digest=payload["report.toml"], presentation_digest=payload[REPORT_MD_NAME],
                   homes_generation=homes,
                   evidence_home=_ingest_acceptance_home(rd.path.name), source_removals=removals)
    fragments = {}
    mappings = {(m["source_path"], tuple(m["span"])): m for m in docs["mappings.toml"]["mapping"]}
    for f in docs[INVENTORY_NAME]["fragment"]:
        m = mappings[(f["source_path"], tuple(f["span"]))]
        fragments[f["fragment_id"]] = {"fragment_id": f["fragment_id"], "origin": m["origin"],
                                       "proposed_state": m["state"]}
    return {"run_id": rd.path.name, "binding": binding, "units": units,
            "fragments": fragments, "model": model}


def _validate_ingest_review_decisions(decisions, snapshot):
    """Join on typed identity; require exact echoes and one explicit verb per unit."""
    import check_opf_import as gate
    if not isinstance(decisions, list):
        return ["ingest units must be an array"], []
    expected = {(u["kind"], u["scope"], u["source_path"]): u for u in snapshot["units"]}
    if len(expected) != len(snapshot["units"]):
        return ["review model contains duplicate typed units"], []
    seen, findings, normalized = set(), [], []
    for d in decisions:
        if not (isinstance(d, dict) and set(d) == {"kind", "scope", "source_path", "authority",
                                                  "decision", "note"}
                and all(isinstance(d[k], str) for k in ("kind", "scope", "source_path", "decision", "note"))):
            findings.append("malformed ingest decision")
            continue
        key = (d["kind"], d["scope"], d["source_path"])
        if key not in expected or key in seen:
            findings.append("unknown or duplicate typed unit: {!r}".format(key))
            continue
        seen.add(key)
        echo = {k: d[k] for k in ("kind", "scope", "source_path", "authority")}
        if not gate._strict_eq(echo, expected[key]) or d["decision"] not in _DECISION_VERBS:
            findings.append("wrong authority echo or decision for {!r}".format(key))
            continue
        normalized.append(dict(expected[key], decision=d["decision"], note=d["note"]))
    if seen != set(expected):
        findings.append("every typed ingest unit needs an explicit decision")
    normalized.sort(key=lambda d: (d["kind"], d["scope"], d["source_path"]))
    return findings, normalized


def _ingest_fragment_decisions(decisions, snapshot):
    fragments = snapshot["fragments"]
    identities = {fid: fid for fid in fragments}
    metadata = {fid: {"origin": d["origin"], "state": d["proposed_state"]} for fid, d in fragments.items()}
    return _validate_review_decisions(decisions, identities, metadata)


def validate_ingest_acceptance(snapshot, acceptance):
    """Return binding findings, completeness findings, and rejection separately."""
    import check_opf_import as gate
    if not isinstance(acceptance, dict):
        return ["acceptance must be an object"], ["no decisions"], False
    base = dict(acceptance)
    ingest = base.pop("ingest", None)
    base["format"] = ACCEPTANCE_FORMAT
    binding = _validate_acceptance(base)
    if acceptance.get("format") != INGEST_ACCEPTANCE_FORMAT:
        binding.append("ingest requires acceptance v2")
    if acceptance.get("run_id") != snapshot["run_id"]:
        binding.append("acceptance belongs to another run")
    for name in ("plan_digest", "inventory_digest"):
        if acceptance.get(name) != snapshot["binding"][name]:
            binding.append(name + " is stale")
    if not (isinstance(ingest, dict) and set(ingest) == {"format", "binding", "units"}
            and ingest.get("format") == INGEST_ACCEPTANCE_BLOCK):
        return binding + ["malformed ingest acceptance block"], ["no valid unit decisions"], False
    if not gate._strict_eq(ingest["binding"], snapshot["binding"]):
        binding.append("ingest binding is stale; re-plan and retain this run as evidence")
    complete, _normalized = _validate_ingest_review_decisions(ingest["units"], snapshot)
    submitted = acceptance.get("decisions")
    if not isinstance(submitted, list):
        complete.append("fragment decisions must be an array")
    else:
        fragment_findings, _normalized = _ingest_fragment_decisions(submitted, snapshot)
        complete.extend(fragment_findings)
    all_decisions = list(submitted) if isinstance(submitted, list) else []
    all_decisions += ingest["units"] if isinstance(ingest["units"], list) else []
    rejected = any(isinstance(d, dict) and d.get("decision") == "reject" for d in all_decisions)
    return binding, complete, rejected


def _read_ingest_acceptance(root_fd, run_id):
    """Read the durable acceptance only; absence is distinct from an unreadable home."""
    home = _ingest_acceptance_home(run_id)
    st = _journal._lstat_contained(root_fd, home)
    if st is None:
        return None
    fd = _journal._open_dir_contained(root_fd, home)
    try:
        names = os.listdir(fd)
        if any(n.startswith(ACCEPTANCE_NAME + ".tmp-") for n in names):
            raise _cannot("interrupted acceptance write in " + home + "; retain evidence and create a fresh run")
        st = _journal._lstat_contained(fd, ACCEPTANCE_NAME)
        if st is None:
            return None
        raw, _st = _journal._read_contained(fd, ACCEPTANCE_NAME)
        try:
            record = _strict_json(raw)
        except (ValueError, RecursionError) as exc:
            raise _cannot("durable acceptance is malformed ({}); create a fresh run".format(exc))
        # None is reserved for observed absence: a present record that decodes to JSON null, or to any other
        # non-object, is malformed evidence, never "not yet reviewed".
        if not isinstance(record, dict):
            raise _cannot("durable acceptance is not a JSON object; retain it and create a fresh run")
        return record
    finally:
        os.close(fd)


def _require_ingest_homes(homes):
    """Require layout activation, not just the existence of an operator-created directory. `homes` is the
    generation _store_homes derived from the authoritative manifest (so SUPPORTED_HOMES gates it); the
    value returned is the one the capture's binding carries."""
    if type(homes) is not int or homes != INGEST_HOMES_GENERATION:
        raise _cannot("durable acceptance writing requires activated homes 2; the legacy layout is read-only here")
    return homes


def _capture_ingest_review(resolution, run_id, actor, decisions, ingest, clock):
    """Capture only to a provisioned durable home after the final binding comparison."""
    import check_opf_import as gate
    homes = _store_homes(resolution)
    run_dir = _import_run_dir(resolution, run_id, homes)
    _require_review_gate(run_dir, homes)
    rd = gate._RunDir(run_dir)
    try:
        snapshot = _ingest_snapshot(rd, homes)
    finally:
        rd.close()
    if not isinstance(ingest, dict) or set(ingest) != {"format", "binding", "units"}:
        raise _cannot("ingest review requires a schema-2 envelope carrying the displayed binding and units")
    if not gate._strict_eq(ingest["binding"], snapshot["binding"]):
        raise _finding("submitted review is stale; no decisions were rebound")
    errors, units = _validate_ingest_review_decisions(ingest["units"], snapshot)
    fragment_errors, decisions = _ingest_fragment_decisions(decisions, snapshot)
    errors.extend(fragment_errors)
    if errors:
        raise _finding("; ".join(errors))
    try:
        stamp = clock() if clock is not None else datetime.datetime.now(datetime.timezone.utc)
        _require_utc(stamp)
    except Exception as exc:
        raise _cannot("capture clock unavailable ({})".format(exc))
    model = {"format": INGEST_ACCEPTANCE_FORMAT, "run_id": run_id,
             "plan_digest": snapshot["binding"]["plan_digest"],
             "inventory_digest": snapshot["binding"]["inventory_digest"],
             "actor": {"declared": actor, "context": _gather_review_context(resolution)},
             "reviewed_at": _rfc3339(stamp), "signature": None, "decisions": decisions,
             "ingest": dict(ingest, units=units)}
    binding, complete, _rejected = validate_ingest_acceptance(snapshot, model)
    if binding or complete:
        raise _finding("; ".join(binding + complete))
    data = _emit_acceptance_bytes(model)
    # A final full gate includes any prior durable acceptance. Repair never bypasses its failure.
    _require_review_gate(run_dir, homes)
    rd = gate._RunDir(run_dir)
    try:
        current = _ingest_snapshot(rd, homes)
    finally:
        rd.close()
    if not gate._strict_eq(snapshot["binding"], current["binding"]):
        raise _finding("review changed during capture; no decisions were rebound")
    # Re-derive just before the write, as apply does: a manifest changed during capture writes nothing.
    if _store_homes(resolution) != homes:
        raise _cannot("the store manifest's homes generation changed during capture; nothing captured")
    _require_ingest_homes(homes)
    home = _ingest_acceptance_home(run_id)
    fd = _opf_store._open_store_root_fd(resolution.store_root, resolution.pointer_source != "default")
    try:
        try:
            hfd = _journal._open_dir_contained(fd, home)
        except (OSError, _journal.JournalError) as exc:
            raise _cannot("durable evidence home is not provisioned by layout activation ({}): {}".format(home, exc))
        os.close(hfd)
        prior = _read_ingest_acceptance(fd, run_id)
        if prior is not None:
            prior_binding, prior_complete, prior_rejected = validate_ingest_acceptance(current, prior)
            if prior_binding or prior_complete:
                raise _cannot("prior acceptance changed during capture; retain it and create a fresh run")
            # A recorded rejection is attributed evidence: any capture over it (a relabel to accept, a swapped
            # reject, or a re-reject by another actor) would replace that evidence, so capture refuses.
            if prior_rejected:
                raise _cannot("a recorded rejection requires a fresh plan; it is retained as evidence and "
                              "cannot be replaced or relabelled in place")
    finally:
        os.close(fd)
    rel = _stage_acceptance(resolution, home, data)
    return ReviewResult(CLEAN, run_id=run_id, acceptance_rel=rel, reviewed_at=model["reviewed_at"],
                        decisions_count=len(decisions) + len(units))


def _ingest_review_envelope(snapshot):
    """Export undecided typed units. A template never supplies an acceptance verb."""
    return {"schema": 2, "run_id": snapshot["run_id"],
            "decisions": list(snapshot["fragments"].values()),
            "ingest": {"format": INGEST_ACCEPTANCE_BLOCK, "binding": snapshot["binding"],
                       "units": [dict(u, note="") for u in snapshot["units"]]}}


def _interactive_ingest_review(resolution, run_id, actor, now, stdin, stdout, clock):
    import check_opf_import as gate
    homes = _store_homes(resolution)
    run_dir = _import_run_dir(resolution, run_id, homes)
    _require_review_gate(run_dir, homes)
    rd = gate._RunDir(run_dir)
    try:
        snapshot = _ingest_snapshot(rd, homes)
    finally:
        rd.close()
    envelope = _ingest_review_envelope(snapshot)
    stdout.write(_ingest_md_escape(_emit_acceptance_bytes(snapshot["model"]).decode("ascii")) + "\n")
    try:
        for d in envelope["decisions"] + envelope["ingest"]["units"]:
            stdout.write(_ingest_md_escape(_emit_acceptance_bytes(d).decode("ascii")) + " accept/reject? ")
            stdout.flush()
            line = stdin.readline()
            verb = line.strip().lower()
            if verb not in ("a", "accept", "r", "reject"):
                raise _cannot("interactive input ended or was invalid; nothing captured")
            d["decision"] = "accept" if verb in ("a", "accept") else "reject"
            stdout.write("note (blank to skip): ")
            stdout.flush()
            note = stdin.readline()
            if not note:
                raise _cannot("interactive input ended before the note; nothing captured")
            d["note"] = note.strip()
    except (KeyboardInterrupt, EOFError):
        raise _cannot("interactive review interrupted; nothing captured")
    return review_import(resolution.product_root, run_id, actor=actor, now=now,
                         decisions=envelope["decisions"], ingest=envelope["ingest"], clock=clock)


def _ingest_acceptance_explanation(resolution, run_id):
    """Only a freshly validated durable record may supply attribution in the refusal."""
    import check_opf_import as gate
    try:
        homes = _store_homes(resolution)
        run_dir = _import_run_dir(resolution, run_id, homes)
        _require_review_gate(run_dir, homes)
        rd = gate._RunDir(run_dir)
        try:
            snapshot = _ingest_snapshot(rd, homes)
            acceptance = None   # generation 1 has no durable home to consult
            if homes == INGEST_HOMES_GENERATION:
                fd = gate._ingest_store_fd(rd, homes)
                if fd is None:
                    raise _cannot("durable acceptance cannot be located for this run")
                try:
                    acceptance = _read_ingest_acceptance(fd, run_id)
                finally:
                    os.close(fd)
        finally:
            rd.close()
        if acceptance is None:
            return "No review has been recorded."
        binding, complete, rejected = validate_ingest_acceptance(snapshot, acceptance)
        if binding or complete:
            return "Recorded review is invalid; retain it and create a fresh run."
        return "Recorded by {} at {}; model {}; rejection={}.".format(
            _ingest_md_escape(acceptance["actor"]["declared"]), acceptance["reviewed_at"],
            snapshot["binding"]["review_model_digest"], rejected)
    except Exception as exc:
        return "Review could not be validated ({}).".format(_ingest_md_escape(str(exc)))


def ingest_review_aid(product_root, run_id, previous_run=None):
    """Export a template and a read-only comparison with prior durable acceptance.

    Comparison tolerates stale bindings, never repairs them or copies decisions. An unreadable old
    record is reported as such; its old content cannot be reconstructed from a digest.
    """
    import check_opf_import as gate
    for rid in (run_id,) + ((previous_run,) if previous_run is not None else ()):
        if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):
            raise _cannot("review aid requires valid run identifiers")
    resolution = _resolve_store_for_review(product_root)
    homes = _store_homes(resolution)
    run_dir = _import_run_dir(resolution, run_id, homes)
    _require_review_gate(run_dir, homes)
    rd = gate._RunDir(run_dir)
    try:
        snapshot = _ingest_snapshot(rd, homes)
    finally:
        rd.close()
    output = {"template": _ingest_review_envelope(snapshot), "model": snapshot["model"]}
    if previous_run is not None and homes != INGEST_HOMES_GENERATION:
        output["comparison"] = {"status": "cannot-evaluate",
                                "detail": "homes generation 1 has no durable acceptance home"}
    elif previous_run is not None:
        fd = _opf_store._open_store_root_fd(resolution.store_root, resolution.pointer_source != "default")
        try:
            try:
                previous = _read_ingest_acceptance(fd, previous_run)
            except (_StageError, _journal.JournalError, OSError) as exc:
                output["comparison"] = {"status": "cannot-evaluate", "detail": str(exc)}
            else:
                if not isinstance(previous, dict) or not isinstance(previous.get("ingest"), dict):
                    output["comparison"] = {"status": "cannot-evaluate", "detail": "old ingest acceptance unavailable"}
                else:
                    output["comparison"] = _ingest_review_changes(previous["ingest"], output["template"]["ingest"])
        finally:
            os.close(fd)
    return output


def _ingest_review_changes(previous, current):
    """Compare recorded authority, including typed echoes; never transfer review verbs."""
    old = {k: previous.get(k) for k in ("format", "binding", "units")}
    new = {k: current.get(k) for k in ("format", "binding", "units")}
    for obj in (old, new):
        if isinstance(obj["units"], list):
            obj["units"] = [{k: v for k, v in u.items() if k not in ("decision", "note")}
                            if isinstance(u, dict) else u for u in obj["units"]]
    import difflib
    before = json.dumps(old, sort_keys=True, indent=2, ensure_ascii=True).splitlines()
    after = json.dumps(new, sort_keys=True, indent=2, ensure_ascii=True).splitlines()
    return {"status": "compared", "diff": list(difflib.unified_diff(
        before, after, fromfile="recorded authority", tofile="current authority", lineterm=""))}


def review_import(product_root, run_id, *, actor, decisions, now, ingest=None, clock=None):
    """Capture an attributed acceptance record over a staged import run (spec 14.1), writing
    `acceptance.json` into the run dir for an ordinary run, or only into the durable evidence home for an
    ingest run. This is the batch (decisions-list) contract surface; the interactive
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
            homes = _store_homes(resolution)
            marker = _ingest_run_marker(store_root_fd, run_rel, homes)
            if marker is not None:
                try:
                    return _capture_ingest_review(resolution, run_id, actor, decisions, ingest, clock)
                except _StageError:
                    raise
                except Exception as exc:
                    raise _cannot("ingest capture could not be evaluated ({!r})".format(exc))
            if ingest is not None:
                raise _cannot("an ordinary run cannot consume an ingest decision envelope")
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
            gate_results = check_opf_import.check_staged_run(run_dir_path, homes=homes)
            _require_gate_results(gate_results, check_opf_import.EXPECTED_CHECKS, require_pass=False)
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


def review_import_interactive(product_root, run_id, *, actor, now, in_stream=None, out_stream=None, clock=None):
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
            marker = _ingest_run_marker(store_root_fd, run_rel, _store_homes(resolution))
            if marker is not None:
                try:
                    return _interactive_ingest_review(resolution, run_id, actor, now, stdin, stdout, clock)
                except _StageError:
                    raise
                except Exception as exc:
                    raise _cannot("ingest interactive review could not be evaluated ({!r})".format(exc))
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


# --- apply-promotion helpers (OPF-IMPORT-APPLY, PR-C ruled full design D1-D5 + terminal delete) --------

def _txn_record_rel(run_id):
    """Store-relative path of the per-run transaction record (the gate-readable projection), OUTSIDE
    `.working/` so it survives the terminal run-dir deletion (D2/D3)."""
    return "{}/{}/{}".format(IMPORT_OPS_REL, run_id, TRANSACTION_NAME)


def _txn_record_intent_problem(intent_ops, rec_rel, rec_bytes):
    """Round-7 (codex round-6 MEDIUM): the ONE shared bind of a transaction record to its journal INTENT, used
    by BOTH apply's idempotency reconcile (R4-C1) and the gate (check_opf_import._journal_binds_record) so the
    two cannot drift. The sole producer (_build_publication_ops) journals EXACTLY ONE op on the record path: a
    `create` whose poststate is {kind "file", mode FILE_MODE, content-sha256 of the record bytes}. So the INTENT
    ops touching `rec_rel` must be exactly that one op. Any other op on the path (a duplicate or conflicting
    create/write, a later remove/rmdir/mkdir), a create whose poststate is not exactly that shape (kind absent,
    a missing / changed mode, an extra key), or no op at all is a problem: the journal then contradicts itself
    or the record, so it cannot certify it. An op path is matched by its segment-normalized form, so a
    non-canonical alias of the record path ('./', '//', 'x/..') counts as touching it rather than slipping past
    a literal comparison; a non-object op or a non-string path is a malformed INTENT, also a problem. Returns ''
    when bound, else the located reason (naming the transaction record)."""
    import posixpath
    if not isinstance(intent_ops, list):
        return "the INTENT carries no op list, so it records no create of the transaction record {}".format(
            rec_rel)
    touching = []
    for op in intent_ops:
        if not (isinstance(op, dict) and isinstance(op.get("path"), str)):
            return ("the INTENT carries a malformed op (not an object with a string path), so it cannot bind "
                    "the transaction record {}".format(rec_rel))
        path = op["path"]
        if path == rec_rel or posixpath.normpath(path).lstrip("/") == rec_rel:
            touching.append(op)
    if len(touching) != 1:
        return ("the INTENT records {} ops on the transaction record {} ({}), not exactly the one create the "
                "producer journals".format(len(touching), rec_rel,
                                           ", ".join(repr(o.get("op")) for o in touching) or "none"))
    rec_op = touching[0]
    post = rec_op.get("poststate")
    sha = _sha256_hex(rec_bytes)
    if not (rec_op.get("op") == "create" and rec_op.get("path") == rec_rel and isinstance(post, dict)
            and set(post) == {"kind", "mode", "content-sha256"} and post.get("kind") == "file"
            and type(post.get("mode")) is int and post.get("mode") == FILE_MODE
            and isinstance(post.get("content-sha256"), str) and _HEX64_RE.match(post["content-sha256"])):
        return ("the INTENT's op on the transaction record {} is not the producer's create (path {!r}, op {!r}, "
                "poststate {!r}; expected create at exactly that path, of kind 'file', mode {:o}, and a "
                "content-sha256)".format(rec_rel, rec_op.get("path"), rec_op.get("op"), post, FILE_MODE))
    if post["content-sha256"] != sha:
        return ("the transaction record {} bytes do not hash to the create the INTENT recorded (modified after "
                "publication, or never journaled)".format(rec_rel))
    return ""


def _validate_transaction_record(txn, run_id):
    """Validate a per-run transaction record's SHAPE: a table with a strict-int schema == SCHEMA, the exact
    TRANSACTION_FORMAT, a run_id that binds THIS run, a known transaction state, sha256-shaped plan and
    inventory digests, and allocation + restore_ref tables. Returns (ok, detail). `type(x) is int` excludes
    the bool slip (True == 1). Shared by the apply idempotency no-op (a malformed or foreign record is never
    a verified no-op; fail-closed) and the check_opf_import transaction-schema gate, so the two cannot drift."""
    if not isinstance(txn, dict):
        return False, "transaction record is not a table"
    pd = txn.get("plan_digest")
    iv = txn.get("inventory_digest")
    if not (type(txn.get("schema")) is int and txn.get("schema") == SCHEMA):
        return False, "transaction schema is not the integer {}".format(SCHEMA)
    if txn.get("format") != TRANSACTION_FORMAT:
        return False, "format is not {!r}".format(TRANSACTION_FORMAT)
    if txn.get("run_id") != run_id:
        return False, "run_id does not name this run"
    if txn.get("state") not in _TRANSACTION_STATES:
        return False, "state {!r} is not a transaction state".format(txn.get("state"))
    if not (isinstance(pd, str) and _DIGEST_RE.match(pd)):
        return False, "plan_digest is not a 'sha256:'+64-hex digest"
    if not (isinstance(iv, str) and _DIGEST_RE.match(iv)):
        return False, "inventory_digest is not a 'sha256:'+64-hex digest"
    if not (isinstance(txn.get("txn_id"), str) and txn.get("txn_id")):
        return False, "txn_id is missing or not a non-empty string"
    allocation = txn.get("allocation")
    if not isinstance(allocation, dict):
        return False, "allocation is not a table"
    for _ns, _ids in allocation.items():
        if not (isinstance(_ns, str) and _ns.strip() and isinstance(_ids, list)
                and all(isinstance(_i, str) and _i.strip() for _i in _ids)):   # PRC-F2 r3: non-blank ns key + non-blank id members; an EMPTY list is legit (a touched-but-unminted namespace, producer emits {ns: []})
            return False, "allocation entry {!r} is not a namespace -> list-of-id-strings mapping".format(_ns)
    restore = txn.get("restore_ref")
    if not isinstance(restore, dict):
        return False, "restore_ref is not a table"
    if not (isinstance(restore.get("txn_id"), str) and restore.get("txn_id")):
        return False, "restore_ref.txn_id is missing or not a non-empty string"
    if not (isinstance(restore.get("journal_rel"), str) and restore.get("journal_rel")):
        return False, "restore_ref.journal_rel is missing or not a non-empty string"
    return True, ""


def _archive_run_rel(run_id):
    """Store-relative path of the durable preserved-source + acceptance archive for a run (D1), OUTSIDE
    `.working/`."""
    return "{}/{}".format(IMPORT_ARCHIVE_REL, run_id)


def _claim_apply_lock(journal_root, jr_fd, root_fd):
    """Atomically claim the import-promotion journal lock, recover-aware, mirroring migrate._claim_recover_lock.
    Returns 'acquired' (this apply owns the lock and every prior journal is reconciled terminal),
    'possibly-live' (a live owner holds it: NEVER seized), or raises JournalError fail-closed. An absent lock
    is acquired via O_EXCL; a confirmed-dead stale lease is reconciled-then-broken under the fcntl arbitration
    primitive only after every journal validates terminal; a possibly-live owner is refused."""
    owner = _journal.read_lock_owner(journal_root)
    if owner is None:
        _journal.acquire_lock(journal_root, session_id="import-apply")
        return "acquired"
    if not _journal.owner_confirmed_dead(owner):
        return "possibly-live"
    return _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd, session_id="import-apply")


def _recover_open_txns(jr_fd, journal_root, root_fd):
    """Reconcile every existing transaction dir to a terminal state before proceeding (idempotent). A prior
    crash that rolled forward leaves a COMPLETE frame (the store carries the promotion); one that rolled back
    restores the prestate. Returns the mapping txn-name -> outcome. JournalError propagates fail-closed."""
    outcomes = {}
    for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
        outcomes[txn_dir.name] = _journal.recover(jr_fd, txn_dir, root_fd)
    return outcomes


def _merged_index_bytes(store_root_fd, live_rel, candidate_rel, where):
    """Merge a staged candidate `{schema, record}` index onto its live counterpart and return the byte-
    canonical merged bytes. Live records come first (order preserved), the candidate records are appended.
    A missing live index contributes zero records (a first import of the type); a malformed live or candidate
    index is CANNOT-EVALUATE (fail-closed, never a partial merge). The candidate index MUST be present and
    non-empty (the caller only merges a type that staged records)."""
    cand = _read_toml(store_root_fd, candidate_rel)
    if cand is None:
        raise _cannot("{}: staged candidate index {} is absent (malformed run; cannot promote)".format(
            where, candidate_rel))
    cand_recs = cand.get("record")
    if not isinstance(cand_recs, list) or not cand_recs:
        raise _cannot("{}: staged candidate index {} carries no record array (malformed run)".format(
            where, candidate_rel))
    live = _read_toml(store_root_fd, live_rel)
    if live is None:
        live_recs = []
    else:
        live_recs = live.get("record")
        if not isinstance(live_recs, list):
            raise _cannot("{}: live index {} is malformed (record is not an array; cannot promote)".format(
                where, live_rel))
    return _emit_bytes({"schema": SCHEMA, "record": list(live_recs) + list(cand_recs)}, where)


def _flip_import_status_bytes(store_root_fd, manifest_rel):
    """Return the live manifest bytes with `[opf].import_status` set to "complete" (the validated-finish
    lifecycle flip, spec 9.2/OPF-SPEC 1166-1177). Parses the manifest with tomllib and RE-EMITS it through the
    canonical _opf_emit emitter with only import_status changed, so ANY valid TOML form of the directive
    (single- or double-quoted value, trailing comment, quoted key, reordered keys) promotes rather than only
    the bare `import_status = "..."` an earlier regex matched. emit_checked asserts model-equality against a
    reparse of its output, so no field is silently dropped or reordered; every other key/table/value is
    preserved at the MODEL level, though canonical re-emission normalizes formatting and does NOT preserve
    comments or byte-exact layout (the store manifest is itself a machine-managed _opf_emit artefact, so on a
    real store this is byte-idempotent except for import_status). Fail-closed (CANNOT-EVALUATE, never a silent
    no-op) when the manifest is unreadable or unparseable, when [opf].import_status is missing or not a string,
    or when the changed model falls outside the emitter's supported subset (a first-party emit failure)."""
    try:
        raw, _st = _journal._read_contained(store_root_fd, manifest_rel)
    except _journal.JournalError as exc:
        raise _cannot("cannot read the live manifest {} ({}); cannot promote".format(manifest_rel, exc))
    import tomllib   # lazy: stdlib TOML reader; the writer is _opf_emit's canonical emitter
    try:
        model = tomllib.loads(raw.decode("utf-8"))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise _cannot("cannot parse the live manifest {} ({}); cannot promote".format(manifest_rel, exc))
    opf_tbl = model.get("opf")
    if not (isinstance(opf_tbl, dict) and isinstance(opf_tbl.get("import_status"), str)):
        raise _cannot("{}: [opf].import_status is missing or not a string; cannot flip to complete "
                      "(malformed manifest; fail-closed)".format(manifest_rel))
    # PRC-N1: flip the PARSED value and RE-EMIT through the canonical emitter opf init uses, so ANY valid
    # manifest formatting (single-quoted value, trailing comment, quoted key, reordered keys) promotes. The
    # earlier regex matched only `import_status = "value"` and fail-closed on every other valid form.
    # emit_checked is order-independent and reparses its output, so the result is the canonical manifest with
    # import_status = "complete".
    opf_tbl["import_status"] = "complete"
    try:
        return _opf_emit.emit_checked(model).encode("utf-8")
    except Exception as exc:  # noqa: BLE001  a first-party emit failure is fail-closed, never a bad promotion
        raise _cannot("cannot re-emit the live manifest {} with import_status=complete ({}); cannot "
                      "promote".format(manifest_rel, exc))


def _minted_by_namespace(store_root_fd, run_rel, machine_rel, candidate_types):
    """Group every minted candidate id (candidate/<type>.index.toml + fragments/legacy_fragment.index.toml +
    candidate/worklog.toml) by its two-letter namespace, fail-closed. Returns {ns: [ids...]}. A malformed id
    is CANNOT-EVALUATE (the caller cannot bind a freshness base to an unparseable allocation)."""
    by_ns = {}
    rels = ["candidate/{}.index.toml".format(t) for t in sorted(candidate_types)]
    rels.append("fragments/legacy_fragment.index.toml")
    rels.append("candidate/worklog.toml")
    for suffix in rels:
        data = _read_toml(store_root_fd, run_rel + "/" + suffix)
        if data is None:
            continue
        recs = data.get("record")
        if not isinstance(recs, list):
            raise _cannot("staged candidate {} is malformed (record is not an array; cannot promote)".format(
                suffix))
        for rec in recs:
            rid = rec.get("id") if isinstance(rec, dict) else None
            shape = _opf_schema._valid_id_shape(rid)
            if shape is None:
                raise _cannot("staged candidate {} carries a record with a malformed id {!r} (cannot "
                              "promote)".format(suffix, rid))
            by_ns.setdefault(shape[0], []).append(rid)
    return by_ns


def _preview_composition_findings(preview_root, *, defer_view_drift=False):
    """D4 composition gate: resolve + validate_store over the assembled candidate PREVIEW, returning a list of
    the REAL findings that must abort the promotion. Empty list == the composition is sound. Observations are
    omitted (the throwaway tempdir preview is not a git repo, a disclosed omission). A tolerated result is
    VALID, or CANNOT-EVALUATE whose ONLY tolerated cants are (a) a git-observation omission over the throwaway
    preview (tolerated by check id, _PREVIEW_GIT_OBSERVATION_CANTS) and (b) a schema-deferral check EVERY one
    of whose cant messages carries the _SCHEMA_DEFERRAL marker (importer/module schema deferral). The tolerance
    is REASON-aware, not blind to the check id: a non-deferral cant under a deferral check id -- e.g. a missing
    or malformed REQUIRED worklog ledger surfacing under C-RECORDS -- is a REAL finding (PRC-F4). ANY INVALID
    finding, ANY unattributed fault, or ANY other cannot-evaluate is a real finding (fail-closed: a store the
    validator could not resolve is itself a finding). With defer_view_drift=True, only FINDING messages
    attributed exclusively to C-VIEW-DRIFT wait for the preview overlay; no CANNOT-EVALUATE is relaxed.
    The caller must run full D4 after rendering and before publication."""
    import _opf_check   # lazy: _opf_check imports _opf_views/_opf_import transitively; call-time avoids a cycle
    try:
        res = _opf_store.resolve_store(Path(preview_root))
        if res.status != _opf_store.RESOLVED:
            return ["candidate preview did not resolve as a store ({}: {})".format(res.status, res.detail)]
        result = _opf_check.validate_store(res, observations=None)
    except Exception as exc:  # noqa: BLE001  a validator escape over the preview is a fail-closed real finding
        return ["candidate preview validation raised ({!r}); fail-closed".format(exc)]
    findings = list(getattr(result, "findings", []) or [])
    findings += ["unattributed: {}".format(m) for m in (getattr(result, "unattributed", []) or [])]
    checks = getattr(result, "checks", None) or {}
    by_check = getattr(result, "by_check", {}) or {}
    # Only the render output may wait for regeneration, never a source finding or a cant.
    # Use the validator's attribution, not a diagnostic prefix. Shared/unattributed messages remain.
    if defer_view_drift and checks.get("C-VIEW-DRIFT") == "FINDING":
        drift_messages = set(by_check.get("C-VIEW-DRIFT", []))
        other_messages = set(getattr(result, "unattributed", []) or [])
        for cid, messages in by_check.items():
            if cid != "C-VIEW-DRIFT":
                other_messages.update(messages)
        findings = [m for m in findings if m not in drift_messages or m in other_messages]
    deferral_marker = _opf_check._SCHEMA_DEFERRAL
    for cid, verdict in checks.items():
        if verdict != "CANNOT-EVALUATE":
            continue
        cid_msgs = by_check.get(cid, []) or []
        # A git-observation cant is benign over the throwaway preview (no git repo); tolerated by check id.
        if cid in _PREVIEW_GIT_OBSERVATION_CANTS:
            continue
        # A schema-deferral check is tolerated ONLY when EVERY one of its cant messages is a genuine schema
        # deferral (carries the _SCHEMA_DEFERRAL marker). A non-deferral cant under the same check id -- e.g. a
        # missing/malformed REQUIRED worklog ledger surfacing under C-RECORDS -- is a REAL finding, fail-closed
        # (PRC-F4: the tolerance is reason-aware, so a genuine missing-input cannot-evaluate cannot ride the
        # deferral check id into a clean composition gate).
        if (cid in _PREVIEW_SCHEMA_DEFERRAL_CANTS and cid_msgs
                and all(deferral_marker in m for m in cid_msgs)):
            continue
        findings.append("composition check {} is CANNOT-EVALUATE outside the disclosed-benign set "
                        "({})".format(cid, "; ".join(cid_msgs)))
    return findings


def _preview_render_reproducible(preview_root):
    """D5 view reproducibility over the assembled PREVIEW: run `opf render --check` over the preview and return
    (ok, needs_write). A view-feeding promoted record leaves the copied-from-live views stale, which --check
    reports as drift. Returns (True, False) when the views are already reproducible (0), (True, True) when a
    re-render is required (drift, 1), or (False, _) when the check cannot evaluate (2, fail-closed)."""
    import _opf_views   # lazy: mirrors the render dispatch's own call-time import (circular through _opf_check)
    try:
        rc = _opf_views.render(["--root", str(preview_root), "--check"])
    except Exception as exc:  # noqa: BLE001  a render-check escape over the preview is fail-closed
        return False, False
    if rc == _opf_views.EXIT_OK:
        return True, False
    if rc == _opf_views.EXIT_DRIFT:
        return True, True
    return False, False


def _render_preview_view_targets(preview_root, store_root_fd, machine_rel, product_root):
    """Plan store-view drift without writing. Product VERSION drift remains out of scope.

    Targets come from plan_views and are reconciled with the engine's name-bound destination.
    A pointer preview whose machine location differs is refused; this does not implement relocation.
    """
    import _opf_views
    preview_fd = None
    product_fd = None
    try:
        res = _opf_store.resolve_store(Path(preview_root))
        if (res.status != _opf_store.RESOLVED or res.pointer_source != "default"
                or Path(res.store_root) != Path(preview_root) or res.machine_rel != machine_rel):
            raise _cannot("candidate view planner cannot bind a self-contained preview at the live "
                          "machine location; pointer-preview relocation is not implemented")
        preview_fd = _opf_store._open_store_root_fd(res.store_root, False)
        planned = _opf_views.plan_views(preview_fd, res.machine_rel)
        files = {}
        seen = set()
        for name, scope, dest_rel, text in planned:
            _opf_views._resolve_view(name)
            if (scope, dest_rel) != _opf_views._spec_destination(name):
                raise _cannot("candidate view planner returned a non-spec destination")
            if (scope, dest_rel) in seen:
                raise _cannot("candidate view planner returned a duplicate destination")
            seen.add((scope, dest_rel))
            data = text.encode("utf-8")
            if scope == "product":
                if product_fd is None:
                    product_fd = _opf_store._open_root_fd(Path(product_root))
                for fd in (product_fd, preview_fd):
                    st = _journal._lstat_contained(fd, dest_rel)
                    current = None if st is None else _journal._read_contained(fd, dest_rel)[0]
                    if current != data:
                        raise _finding("promotion would drift the product-root VERSION deliverable; "
                                       "not re-rendered in this build; nothing promoted")
                continue
            if scope != "store":
                raise _cannot("candidate view planner returned an unsupported scope")
            st = _journal._lstat_contained(store_root_fd, dest_rel)
            current = None if st is None else _journal._read_contained(store_root_fd, dest_rel)[0]
            if current != data:
                files[dest_rel] = data
        return files
    except (_opf_views.ViewsError, _journal.JournalError, OSError, ValueError,
            TypeError, AttributeError, RecursionError) as exc:
        raise _cannot("candidate view planning cannot evaluate ({}); nothing promoted".format(exc))
    finally:
        if product_fd is not None:
            _journal._close_fd_quietly(product_fd)
        if preview_fd is not None:
            _journal._close_fd_quietly(preview_fd)


def _overlay_preview_view_files(preview_root, view_files):
    """Overlay planned bytes only in the disposable preview, through contained no-follow handles."""
    root_fd = _opf_store._open_store_root_fd(Path(preview_root), False)
    try:
        for rel, data in sorted(view_files.items()):
            pfd, name = _journal._open_parent(root_fd, rel)
            fd = None
            try:
                st = _journal._lstat_at(pfd, name)
                flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if st is None:
                    flags |= os.O_CREAT | os.O_EXCL
                fd = os.open(name, flags, FILE_MODE, dir_fd=pfd)
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise _cannot("candidate view overlay refuses a non-regular or multiply-linked target")
                os.ftruncate(fd, 0)
                _journal._write_all(fd, data)
            finally:
                if fd is not None:
                    _journal._close_fd_quietly(fd)
                _journal._close_fd_quietly(pfd)
    finally:
        _journal._close_fd_quietly(root_fd)


def _reconcile_preview_views(preview_root, store_root_fd, machine_rel, product_root):
    """Establish source composition, overlay store views, and witness before live publication.

    Only attributed C-VIEW-DRIFT findings may wait for the overlay. All source checks retain D4's
    reason-aware policy, and full D4 must pass afterward. Planning before D4 is read-only; no preview
    view is changed before D4 establishes the sources. Pre-existing live drift is still refused.
    """
    live_ok, live_drift = _preview_render_reproducible(product_root)
    if not live_ok:
        raise _cannot("live view reproducibility cannot be evaluated; nothing promoted")
    if live_drift:
        raise _finding("live views already drift before promotion; reconcile them before import apply")
    render_ok, needs_write = _preview_render_reproducible(preview_root)
    if not render_ok:
        raise _cannot("candidate render reproducibility could not be evaluated over the "
                      "assembled preview (`opf render --check` cannot-evaluate); fail-closed")
    view_files = (_render_preview_view_targets(
        preview_root, store_root_fd, machine_rel, product_root) if needs_write else {})
    if needs_write and not view_files:
        raise _cannot("candidate render reports drift without a publishable store-view target")
    comp_findings = _preview_composition_findings(preview_root, defer_view_drift=needs_write)
    if comp_findings:
        raise _finding("candidate composition gate: the assembled store is not doctor-composable "
                       "({}); nothing promoted".format("; ".join(comp_findings)))
    if needs_write:
        _overlay_preview_view_files(preview_root, view_files)
        if _preview_render_reproducible(preview_root) != (True, False):
            raise _cannot("candidate view witness failed: regenerated preview is not render-check "
                          "clean; nothing promoted")
        comp_findings = _preview_composition_findings(preview_root)
        if comp_findings:
            raise _finding("candidate composition after view regeneration failed ({}); nothing "
                           "promoted".format("; ".join(comp_findings)))
    return view_files


def _read_json_acceptance(store_root_fd, run_rel):
    """Read + parse the staged acceptance.json (canonical JSON), fail-closed. Returns the parsed object, or
    None when absent (a run with no acceptance is not promotion-ready, surfaced by the caller). A present but
    unreadable/malformed acceptance is CANNOT-EVALUATE."""
    acc_rel = run_rel + "/" + ACCEPTANCE_NAME
    st = _journal._lstat_contained(store_root_fd, acc_rel)
    if st is None:
        return None
    try:
        raw, _st = _journal._read_contained(store_root_fd, acc_rel)
    except _journal.JournalError as exc:
        raise _cannot("cannot read acceptance.json ({}); cannot promote".format(exc))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _cannot("acceptance.json is not valid JSON ({}); cannot promote".format(exc))


def _staged_candidate_types(store_root_fd, run_rel):
    """The record-type names staged under `candidate/<type>.index.toml` (mapped/split candidates), excluding
    the counters/worklog artefacts. Legacy_fragment is staged separately under `fragments/`, so it is not
    included here."""
    names = _list_contained(store_root_fd, run_rel + "/candidate")
    if names is None:
        return set()
    return {n[:-len(".index.toml")] for n in names if n.endswith(".index.toml")}


def _baseline_freshness_findings(live_high, cand_high, count_minted):
    """Findings when the live counters no longer equal the plan-time base (spec 8.2). At plan the candidate
    high-water is the live base advanced by the minted count, so live[ns] must still equal cand[ns] -
    minted[ns] for every namespace. Any inequality (the live store advanced since the plan), a namespace with
    minted or live ids but no candidate high-water, or a candidate high-water below its minted count is a
    finding (re-plan). Returns a findings list."""
    findings = []
    for ns in sorted(set(cand_high) | set(live_high) | set(count_minted)):
        minted_n = count_minted.get(ns, 0)
        live_n = live_high.get(ns, 0)
        if ns not in cand_high:
            findings.append("namespace {!r} carries minted/live ids but no candidate high-water".format(ns))
            continue
        base = cand_high[ns] - minted_n
        if base < 0:
            findings.append("candidate high-water {}={} is below its minted count {}".format(
                ns, cand_high[ns], minted_n))
            continue
        if live_n != base:
            findings.append("namespace {!r}: live counter {} does not equal the plan-time base {} "
                            "(candidate {} minus minted {})".format(ns, live_n, base, cand_high[ns], minted_n))
    return findings


def _build_promotion_machine_files(store_root_fd, machine_rel, run_rel, candidate_types, cand_high):
    """Assemble the live-relative machine files a promotion writes, as {store-relative path -> byte-canonical
    bytes}: counters.toml set to the candidate high-water, manifest.toml with import_status flipped to
    complete, each candidate type index merged onto its live counterpart, and (when present) the merged
    legacy_fragment index. A staged worklog candidate is REFUSED fail-closed: the staged candidate worklog is
    a `{schema, record}` array while the live worklog is a `{schema, [[entry]]}` ledger, so promoting it needs
    a record->entry transform that is a DISCLOSED PR-C residual not implemented in this draft (plan_import
    produces no worklog candidates, so the real e2e is unaffected)."""
    files = {}
    files["{}/counters.toml".format(machine_rel)] = _emit_bytes(
        {"schema": SCHEMA, "counters": cand_high}, "counters.toml")
    files["{}/manifest.toml".format(machine_rel)] = _flip_import_status_bytes(
        store_root_fd, "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME))
    for t in sorted(candidate_types):
        live_rel = "{}/{}.index.toml".format(machine_rel, t)
        cand_rel = run_rel + "/candidate/{}.index.toml".format(t)
        files[live_rel] = _merged_index_bytes(store_root_fd, live_rel, cand_rel, "{} index".format(t))
    lf_cand_rel = run_rel + "/fragments/legacy_fragment.index.toml"
    if _journal._lstat_contained(store_root_fd, lf_cand_rel) is not None:
        live_rel = "{}/{}.index.toml".format(machine_rel, LF_TYPE)
        files[live_rel] = _merged_index_bytes(store_root_fd, live_rel, lf_cand_rel, "legacy_fragment index")
    if _journal._lstat_contained(store_root_fd, run_rel + "/candidate/worklog.toml") is not None:
        raise _cannot("staged run carries worklog candidates; promoting a staged `{schema, record}` worklog "
                      "into the live `{schema, [[entry]]}` worklog ledger is a disclosed PR-C residual not "
                      "implemented in this draft (fail-closed; plan_import produces no worklog candidates)")
    return files


def _assemble_preview(resolution, machine_rel, machine_files, preview_dir, shutil, promoted_run_id, homes=1):
    """Copy the live store into a throwaway tempdir PREVIEW and overlay the merged machine files, so the
    preview models the post-promotion store. It ignores the VCS dir and the store-root `.aiqt/` ops trees (at
    the store root only), and drops ONLY the run being promoted from `.working/imports/` -- publication
    deletes exactly that one run and leaves any SIBLING staging run in place, so the preview must RETAIN
    siblings and let C-CONTAINMENT grade them rather than hide the whole imports tree (PRC-F5b). resolve_store
    (preview_dir) then discovers the machine store exactly as it does live. The store-root OPF pointer files
    (`.opf.toml`/`.opf.local.toml`) are NEUTRALIZED (dropped at the store root only), so the preview resolves as
    a self-contained DEFAULT store that binds to ITSELF (product root == store root == preview) rather than
    following an absolute `[store].target` pointer BACK to the original store and grading the wrong target
    (PRC-F5ptr). Disclosed residual: this copytree assumes the live store is DEFAULT-resolution (the machine
    subdir sits at the store root); a live pointer store (product root != store root) needs the machine tree
    relocated into the preview, which the finalizer must handle (flagged)."""
    store_root = str(resolution.store_root)

    def _ignore(dirpath, names):
        drop = set()
        # Drop the VCS dir and the store-root .aiqt ops trees ONLY at the store root (never a same-named dir
        # nested deeper): a nested `.git`/`.aiqt` is not promoted store content, so it is COPIED into the
        # preview and graded by C-CONTAINMENT rather than hidden from the D4 gate (PRC-F5). The drop set is
        # sourced from `_opf_store.STORE_ROOT_CONTROL_DIRS`, the single store-topology authority `_opf_ingest`
        # also derives its store-root control exclusion from, so the two cannot mirror-drift.
        if os.path.abspath(dirpath) == os.path.abspath(store_root):
            drop |= set(n for n in names if n in _opf_store.STORE_ROOT_CONTROL_DIRS)
            # Also neutralize the store-root OPF pointer files: the D4 gate runs resolve_store(preview),
            # which reads a `[store].target` pointer FIRST, so a copied pointer whose target is an absolute
            # path would redirect resolution BACK to the ORIGINAL store and grade the wrong target. Dropping
            # them makes the preview a self-contained DEFAULT store (product root == store root == preview),
            # so composition grades the preview itself (PRC-F5ptr).
            drop |= set(n for n in names if n in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL))
        # Drop ONLY the run being promoted from `.working/imports/` (publication deletes exactly that run),
        # keeping any sibling staging run so the preview models the true post-promotion store and D4 grades a
        # leftover sibling rather than the whole imports tree being hidden wholesale (PRC-F5b).
        # Anchor to the STORE-ROOT imports dir by ABSOLUTE path, not a basename pair (PRC-F5b round-4):
        # a basename check (".../imports" whose parent basename is ".working") also matches a nested
        # `.working/imports` planted DEEPER inside a staging run, which could drop a same-named entry
        # there. The store-root anchor drops the promoted run ONLY from the real imports root.
        imports_root_abs = os.path.join(os.path.abspath(store_root),
                                         _opf_store.WORKING_DIRNAME, IMPORTS_DIRNAME)
        if os.path.abspath(dirpath) == imports_root_abs \
                and promoted_run_id is not None and promoted_run_id in names:
            drop.add(promoted_run_id)
        # Homes 2 only: a legacy store's journals/ is an ordinary store path the preview must grade.
        working_abs = os.path.join(os.path.abspath(store_root), _opf_store.WORKING_DIRNAME)
        if homes >= 2 and os.path.abspath(dirpath) == working_abs:
            drop.update(n for n in names if n == _opf_store.JOURNALS_DIRNAME)
        # Homes 2 only: exclude exactly this preview destination. Same-named nested directories and
        # sibling staging runs remain visible. This is inert while previews are outside the store.
        preview_abs = os.path.abspath(preview_dir)
        if homes >= 2 and os.path.abspath(dirpath) == os.path.dirname(preview_abs):
            drop.update(n for n in names if n == os.path.basename(preview_abs))
        return drop

    shutil.copytree(store_root, preview_dir, ignore=_ignore, dirs_exist_ok=True, symlinks=True)
    for live_rel, data in machine_files.items():
        p = Path(preview_dir) / live_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _build_publication_ops(store_root_fd, machine_rel, run_rel, run_id, machine_files, acceptance,
                           plan_digest, inventory_digest, minted, txn_id, restore_ref, observed_head):
    """Build the ONE journaled publication op set + its staged-content map. In dependency order:
    (D1) relocate the preserved source bodies and acceptance.json to the durable `.aiqt/import-archive/<run>`;
    publish the merged machine files (write when the live file exists, create when new); (D2/D3) write the
    per-run transaction.toml under `.aiqt/import/<run>`; then delete the staging run dir as the TERMINAL step
    (its files, then its subdirs, then the run dir). reversed(ops) is the valid preimage-rollback order."""
    ops = []
    content = {}
    arch_rel = _archive_run_rel(run_id)

    # D1: relocate the preserved sources + acceptance to the durable archive (create ops; prior-absence).
    if _journal._lstat_contained(store_root_fd, IMPORT_ARCHIVE_REL) is None:
        ops.append({"op": "mkdir", "path": IMPORT_ARCHIVE_REL, "poststate": {"kind": "dir", "mode": DIR_MODE}})
    ops.append({"op": "mkdir", "path": arch_rel, "poststate": {"kind": "dir", "mode": DIR_MODE}})
    ops.append({"op": "mkdir", "path": arch_rel + "/sources", "poststate": {"kind": "dir", "mode": DIR_MODE}})
    # PRC-F3: the source bodies are content-addressed (filename == sha256 of the bytes) and their sha256s
    # are recorded in the digest-bound inventory. Before relocating them to the durable archive (and deleting
    # the run) apply REVALIDATES the staged payload against that recorded set: every recorded source has a
    # body file whose bytes hash to its content-address filename, and there is no extra, missing, or corrupt
    # body. A changed body under its original filename, a missing body ("zero source bodies"), or an extra
    # file each fails closed rather than archiving an unverified payload. (The narrower review loader binds
    # the digests; this closes the archived-bytes integrity gap it does not itself cover.)
    inventory = _read_toml(store_root_fd, run_rel + "/" + INVENTORY_NAME)
    if not isinstance(inventory, dict) or not isinstance(inventory.get("source"), list):
        raise _cannot("staged run inventory.toml is missing or malformed at apply (cannot revalidate the "
                      "preserved source bodies; fail-closed)")
    expected_bodies = set()
    for srec in inventory["source"]:
        if not (isinstance(srec, dict) and isinstance(srec.get("sha256"), str)):
            raise _cannot("staged inventory carries a malformed source record (cannot revalidate bodies)")
        expected_bodies.add(srec["sha256"])
    seen_bodies = set()
    for name in (_list_contained(store_root_fd, run_rel + "/sources") or []):
        src_body_rel = run_rel + "/sources/" + name
        st = _journal._lstat_contained(store_root_fd, src_body_rel)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _cannot("staged source body {} is missing or not a regular file (cannot relocate)".format(
                src_body_rel))
        raw, _st = _journal._read_contained(store_root_fd, src_body_rel)
        if _sha256_hex(raw) != name:
            raise _cannot("staged source body {} does not match its content-address (sha256) filename; the "
                          "preserved payload is corrupt or tampered, cannot promote (fail-closed)".format(
                              src_body_rel))
        seen_bodies.add(name)
        dst = arch_rel + "/sources/" + name
        ops.append({"op": "create", "path": dst,
                    "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(raw)}})
        content[dst] = raw
    if seen_bodies != expected_bodies:
        raise _cannot("staged source bodies {} do not match the digest-bound inventory's recorded sources {} "
                      "(missing, extra, or renamed body files; fail-closed)".format(
                          sorted(seen_bodies), sorted(expected_bodies)))
    acc_raw, _ast = _journal._read_contained(store_root_fd, run_rel + "/" + ACCEPTANCE_NAME)
    acc_dst = arch_rel + "/" + ACCEPTANCE_NAME
    ops.append({"op": "create", "path": acc_dst,
                "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(acc_raw)}})
    content[acc_dst] = acc_raw

    # Publish the merged machine files (write existing, create new). A write op preserves the target's own
    # mode (its poststate carries no mode, matching migrate's op shape); a create op pins FILE_MODE.
    for live_rel in sorted(machine_files):
        data = machine_files[live_rel]
        exists = _journal._lstat_contained(store_root_fd, live_rel) is not None
        if exists:
            poststate = {"kind": "file", "content-sha256": _sha256_hex(data)}
        else:
            poststate = {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(data)}
        ops.append({"op": "write" if exists else "create", "path": live_rel, "poststate": poststate})
        content[live_rel] = data

    # D2/D3: the per-run transaction record (the gate-readable projection of the terminal state).
    txn_dir_rel = "{}/{}".format(IMPORT_OPS_REL, run_id)
    if _journal._lstat_contained(store_root_fd, txn_dir_rel) is None:
        ops.append({"op": "mkdir", "path": txn_dir_rel, "poststate": {"kind": "dir", "mode": DIR_MODE}})
    txn_model = {
        "schema": SCHEMA, "format": TRANSACTION_FORMAT, "run_id": run_id, "state": "complete",
        "txn_id": txn_id, "plan_digest": plan_digest, "inventory_digest": inventory_digest,
        "allocation": {ns: list(minted[ns]) for ns in sorted(minted)},
        # PRC R3-C1: the completed record self-describes its DURABLE archive so a later no-op can revalidate
        # the immutable evidence by type + content, not mere existence. `archived_sources` is the content-
        # address set relocated above (each entry == the sha256 of a preserved body / its filename), and
        # `acceptance_sha256` is the archived acceptance.json's content hash. (OPF import is unshipped, so no
        # legacy record lacks these; forward-compatible.)
        "archived_sources": sorted(expected_bodies),
        "acceptance_sha256": _sha256_hex(acc_raw),
        "restore_ref": {"txn_id": restore_ref["txn_id"], "journal_rel": restore_ref["journal_rel"],
                        "observed_head": observed_head},
    }
    txn_bytes = _emit_bytes(txn_model, "transaction.toml")
    txn_rel = _txn_record_rel(run_id)
    ops.append({"op": "create", "path": txn_rel,
                "poststate": {"kind": "file", "mode": FILE_MODE, "content-sha256": _sha256_hex(txn_bytes)}})
    content[txn_rel] = txn_bytes

    # TERMINAL: delete the staging run dir. Files first (children), then subdirs, then the run dir (parents).
    file_ops = []
    subdir_ops = []
    for name in (_list_contained(store_root_fd, run_rel) or []):
        entry_rel = run_rel + "/" + name
        st = _journal._lstat_contained(store_root_fd, entry_rel)
        if st is None:
            continue
        if stat.S_ISDIR(st.st_mode):
            for iname in (_list_contained(store_root_fd, entry_rel) or []):
                irel = entry_rel + "/" + iname
                ist = _journal._lstat_contained(store_root_fd, irel)
                if ist is None:
                    continue
                if stat.S_ISDIR(ist.st_mode):
                    raise _cannot("unexpected nested directory {} under the staging run; cannot enumerate "
                                  "for deletion (fail-closed)".format(irel))
                file_ops.append({"op": "remove", "path": irel, "poststate": {"kind": "absent"}})
            subdir_ops.append({"op": "rmdir", "path": entry_rel, "poststate": {"kind": "absent"}})
        else:
            file_ops.append({"op": "remove", "path": entry_rel, "poststate": {"kind": "absent"}})
    ops.extend(file_ops)
    ops.extend(subdir_ops)
    ops.append({"op": "rmdir", "path": run_rel, "poststate": {"kind": "absent"}})
    return ops, content


def _unmanaged_paths(ops, machine_rel, run_rel, run_id, extra_allowed=frozenset()):
    """Step-7 unmanaged-path pre-flight: every op path must be an OPF-managed store path (under the machine
    subdir), the import-ops or archive tree (`.aiqt/import` / `.aiqt/import-archive`, outside `.working/`), or
    the staging run dir being deleted. extra_allowed contains exact planner-validated view destinations,
    permitted only for write/create, never a directory prefix. Any other path is refused. Returns the
    sorted set of offending paths."""
    allowed_prefixes = (machine_rel + "/", IMPORT_ARCHIVE_REL + "/", IMPORT_OPS_REL + "/")
    bad = set()
    for op in ops:
        p = op["path"]
        if p in extra_allowed and op["op"] in ("write", "create"):
            continue
        if p == run_rel or p.startswith(run_rel + "/"):
            continue
        if p in (IMPORT_ARCHIVE_REL, IMPORT_OPS_REL):
            continue
        if any(p.startswith(pre) for pre in allowed_prefixes):
            continue
        bad.add(p)
    return sorted(bad)


def apply_import(product_root, run_id, *, accepted_plan_digest=None, now=None):
    """APPLY-PROMOTION (OPF-IMPORT-APPLY, PR-C): validate an ACCEPTED staged import run and promote its
    candidate to the active store through the crash-durable, lock-guarded `_journal.run_transaction` cutover,
    then delete the staging run as the terminal journaled step, leaving a doctor-composable store (its
    validate_store is INVALID-free; after an LF promotion `opf doctor` still reports the disclosed
    legacy_fragment schema-deferral as CANNOT-EVALUATE, so the store is composable, not a clean exit-0).
    Returns an ApplyResult; `promoted`/`outcome` are read from the result, never inferred from the verdict.

    The ruled full fail-closed design (D1-D5 + terminal delete):
      - D1: the preserved source bodies (`sources/<sha256>`) and `acceptance.json` are RELOCATED to a durable
        non-`.working/` archive (`.aiqt/import-archive/<run-id>/`) inside the same journaled transaction
        BEFORE the run dir is deleted, so spec 14.2 preservation survives (no data loss).
      - D2/D3: the journal, the idempotency signal, and `transaction.toml` live OUTSIDE `.working/`
        (`.aiqt/import/...`, mirroring migrate), so they survive the run-dir deletion and never trip the
        store containment walk. Idempotency (re-apply -> noop_already_complete) is read from the retained
        transaction record + journal, not the deleted run dir.
      - D4: the composition gate assembles the merged store into a machine-subdir-carrying tempdir PREVIEW and
        requires validate_store to report NO real finding, tolerating ONLY the disclosed-benign cannot-
        evaluates (importer/module schema-deferral + git-observation omission). Before rendering, only
        attributed C-VIEW-DRIFT findings may wait; the full gate is required after the overlay.
      - D5: promotion plans store-scope views and journals changed bytes with the records; render --check
        over the augmented preview must report no drift before publication.
      - TERMINAL: the staging run dir is deleted as the last journaled op set, so `import_status = complete`
        leaves no unregistered staging tree for the containment check to find.

    Fail-closed throughout (SECI-fail-closed; SECA-verified-restore-path): a failure at any step returns the
    mapped verdict and never advances a live counter or leaves a half-published store without the journal's
    verified preimage restore. The staged report/plan digest is the AUTHORITATIVE binding; accepted_plan_digest
    is an optional extra assertion checked only when not None (the CLI does not forward it today).

    D5 reconciles store-scope view drift with the read-only plan_views engine, witnesses the augmented
    preview with render --check and full D4, and includes only changed view bytes in the same journal as
    the records. Before the overlay, D4 defers only attributed C-VIEW-DRIFT findings; source findings and
    non-benign cannot-evaluates still refuse. Existing live view drift must be reconciled before apply.
    The public render --write strict source gate is unchanged and is not called by this transaction.

    DISCLOSED RESIDUALS: product-root VERSION drift is refused, including on default stores; this build
    publishes store-scope views only. Pointer-preview relocation remains unimplemented. Post-publication
    verification failure reports promoted=True: COMPLETE is already durable, so it is not an automatic
    rollback. The journal and import lock provide recoverable cutover under the existing cooperating-
    writer contract, not simultaneous multi-file visibility to unlocked readers."""
    resolution = None
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return ApplyResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)], promoted=False, outcome="aborted")
    try:
        _require_utc(now)
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path string or os.PathLike, got {}".format(
                type(product_root).__name__))
        # Validate run_id as an identifier BEFORE any path use (guard-input-soundness): a value outside the
        # grammar is CANNOT-EVALUATE, never a path fragment.
        if not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
            raise _cannot("run-id {!r} does not match the run-id grammar (fail-closed)".format(run_id))
        if accepted_plan_digest is not None and not (
                isinstance(accepted_plan_digest, str) and _DIGEST_RE.match(accepted_plan_digest)):
            raise _cannot("accepted_plan_digest, when supplied, must be a 'sha256:'+64-hex digest")

        resolution = _resolve_store_for_review(product_root)
        machine_rel = resolution.machine_rel
        run_rel = "{}/{}".format(IMPORTS_REL, run_id)
        # MIG-PR3/PR-4a: a root-ingest disposition plan carries keep/move pending actions and
        # importer_proposal suggestions that this build cannot promote (attributed review is PR-A, ingest
        # apply is PR-C). Its primary marker is ingest-actions.toml (staged first); candidates_draft.toml and
        # the MIG-PR4a frozen review bundle (ingest-review.toml) are ALSO ingest-only, so ANY of them is
        # recognized (_INGEST_RUN_MARKERS, defence in depth: a partially-staged leftover from an older build,
        # or any unforeseen ordering, that carries a later artefact without the marker is still refused;
        # marginal cost one lstat each). Refuse fail-closed BEFORE any journal/lock work, distinct
        # cannot-evaluate, mutating nothing (required-step-remains-required: the block is not weakened just
        # because the baseline candidate alone would look promotable).
        try:
            _ingest_root_fd = _opf_store._open_store_root_fd(
                resolution.store_root, resolution.pointer_source != "default")
        except OSError as exc:
            raise _cannot("cannot open store root {!r} ({})".format(resolution.store_root, exc))
        try:
            marker_name = _ingest_run_marker(_ingest_root_fd, run_rel, _store_homes(resolution))
        finally:
            os.close(_ingest_root_fd)
        if marker_name is not None:
            detail = _ingest_acceptance_explanation(resolution, run_id)
            raise _cannot("run {!r} is a root-ingest disposition plan (carries {}); ingest disposition "
                          "execution is unavailable; nothing promoted. {}".format(run_id, marker_name, detail))
        journal_root = Path(resolution.store_root) / IMPORT_JOURNAL_REL

        try:
            root_fd = _opf_store._open_store_root_fd(
                resolution.store_root, resolution.pointer_source != "default")
        except OSError as exc:
            raise _cannot("cannot open store root {!r} ({})".format(resolution.store_root, exc))
        jr_fd = None
        we_hold_lock = False   # this apply owns the writer lock (never release another owner's)
        retain_lock = False    # keep the lock held for a later `recover` (rollback-incomplete only)
        try:
            # --- step 2: journal + exclusive writer lock (recover-aware), then reconcile any open txn -------
            try:
                _journal.ensure_journal_dirs(root_fd, IMPORT_JOURNAL_REL)
                jr_fd = _journal.open_journal_root_fd(root_fd, IMPORT_JOURNAL_REL)
            except (_journal.JournalError, OSError) as exc:
                raise _cannot("cannot prepare the import-promotion journal ({}); fail-closed".format(exc))
            try:
                claim = _claim_apply_lock(journal_root, jr_fd, root_fd)
                if claim == "possibly-live":
                    owner = _journal.read_lock_owner(journal_root)
                    raise _cannot("another import apply holds the promotion lock (pid {}); NOT seized "
                                  "(fail-closed)".format((owner or {}).get("pid")))
                we_hold_lock = True   # from here every exit path releases the lock in the finally below,
                                      # except the rollback-incomplete path which sets retain_lock
                _recover_open_txns(jr_fd, journal_root, root_fd)

                # --- step 3/4: idempotency reconcile BEFORE any write -----------------------------------
                # The retained transaction record is the durable idempotency signal (it survives the run-dir
                # deletion). It is trusted ONLY when it is shape-valid AND binds THIS run (run_id): a malformed
                # record (e.g. a bare {state=complete}) or one naming a different run is NOT a verified no-op;
                # it fails closed rather than reporting a false promotion (PRC-F2). A shape-valid, run-bound
                # record at state=complete is the idempotent no-op; a partial/torn state was reconciled by the
                # journal recover above.
                # R5-C1 (single-read TOCTOU close): read the retained record's RAW BYTES exactly ONCE here and
                # use those same bytes for BOTH every identity/archive check below AND the R4-C1 journal bind,
                # so no interleaving can validate the archive against a first read while the journal-bind
                # authenticates a divergent second read. Absence is preserved identically to the prior
                # _read_toml path: a missing record (or parent) lstats as None and falls through to the normal
                # staged path; a present-but-unreadable or unparseable record is fail-closed CANNOT-EVALUATE,
                # never a silent no-op. require_single_link mirrors _read_toml's contained reader (F-R17-A1).
                noop_rec_rel = _txn_record_rel(run_id)
                try:
                    noop_rec_st = _journal._lstat_contained(root_fd, noop_rec_rel)
                except (_journal.JournalError, OSError) as exc:
                    raise _cannot("cannot stat the retained transaction record {} ({}); fail-closed".format(
                        noop_rec_rel, exc))
                if noop_rec_st is None:
                    txn_record = None
                    noop_rec_raw = None
                else:
                    # R6-C2: the SINGLE authenticated read (R5-C1) still holds the 1 MiB store-read cap the
                    # prior _read_toml_contained route enforced, so an oversized record is refused BEFORE it is
                    # read+parsed rather than after the hash rejects it. This mirrors _opf_store's two-check
                    # idiom: a PRE-read fast reject bound to the already-captured lstat size (before the open),
                    # and a POST-read check on the bytes actually read (a store file swapped or grown between the
                    # lstat and the open). _read_contained's generic 16 MiB cap remains the outer bound; this
                    # restores the tighter store cap the no-op single-read otherwise dropped (SECA
                    # resource-bounds; check-fails-closed-on-unreadable).
                    if noop_rec_st.st_size > _opf_store.MAX_STORE_READ_BYTES:
                        raise _cannot("the retained transaction record {} is {} bytes, over the {}-byte "
                                      "store-read cap before it is read; an oversized record is fail-closed "
                                      "before any open, never a silent no-op".format(
                                          noop_rec_rel, noop_rec_st.st_size, _opf_store.MAX_STORE_READ_BYTES))
                    try:
                        noop_rec_raw, _noop_rec_readst = _journal._read_contained(
                            root_fd, noop_rec_rel, require_single_link=True)
                    except (_journal.JournalError, OSError) as exc:
                        raise _cannot("cannot read the retained transaction record {} ({}); a present record "
                                      "that cannot be read is fail-closed, never a silent no-op".format(
                                          noop_rec_rel, exc))
                    if len(noop_rec_raw) > _opf_store.MAX_STORE_READ_BYTES:
                        raise _cannot("the retained transaction record {} read {} bytes, over the {}-byte "
                                      "store-read cap after reading (a raced swap or growth past the pre-open "
                                      "size); an oversized record is fail-closed before parse, never a silent "
                                      "no-op".format(
                                          noop_rec_rel, len(noop_rec_raw), _opf_store.MAX_STORE_READ_BYTES))
                    try:
                        txn_record = tomllib.loads(noop_rec_raw.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                        raise _cannot("cannot parse the retained transaction record {} ({}); a present but "
                                      "unparseable record is fail-closed, never a silent no-op".format(
                                          noop_rec_rel, exc))
                if txn_record is not None:
                    ok, detail = _validate_transaction_record(txn_record, run_id)
                    if not ok:
                        raise _cannot("the retained transaction record for run {} is not a valid record for "
                                      "this run ({}); it cannot certify an idempotent no-op and the run must be "
                                      "reconciled through recover (fail-closed; never renumber)".format(
                                          run_id, detail))
                    if txn_record.get("state") == "complete":
                        # PRC-F2: honour the caller's optional accepted_plan_digest assertion on the no-op path
                        # too (the normal path checks it at load, but the no-op returns before that load). The
                        # completed record retains the run's plan_digest, so a caller asserting a DIFFERENT
                        # plan digest is a stale binding and must NOT read as a verified no-op.
                        if accepted_plan_digest is not None \
                                and accepted_plan_digest != txn_record.get("plan_digest"):
                            raise _finding("accepted_plan_digest {!r} does not match the completed run's "
                                           "recorded plan digest {!r} (stale binding; the named run completed "
                                           "under a different plan)".format(
                                               accepted_plan_digest, txn_record.get("plan_digest")))
                        # The completed PROJECTION record certifies an idempotent no-op ONLY while the run's own
                        # DURABLE evidence still backs it (never on the record independently): (a) the run's
                        # journal is present and TERMINAL, and (b) the durable import archive (acceptance.json +
                        # the preserved source bodies) still exists. A completed record whose journal was
                        # deleted/torn or whose archive payload is gone cannot certify a no-op; it fails closed
                        # for reconciliation through recover rather than reporting a false promoted no-op.
                        noop_txn_id = txn_record.get("txn_id")   # _validate_transaction_record proved it a non-empty str
                        try:
                            noop_jstate = _journal.classify_state(jr_fd, journal_root / noop_txn_id)
                        except _journal.JournalError as exc:
                            raise _cannot("the durable journal for completed run {} (txn {}) is unreadable "
                                          "({}); a completed record with no readable journal cannot certify an "
                                          "idempotent no-op (reconcile through recover; fail-closed)".format(
                                              run_id, noop_txn_id, exc))
                        if noop_jstate != "complete":
                            raise _cannot("the durable journal for completed run {} (txn {}) is absent or "
                                          "non-terminal (state {!r}); a completed record with no terminal "
                                          "journal cannot certify an idempotent no-op (reconcile through "
                                          "recover; fail-closed)".format(run_id, noop_txn_id, noop_jstate))
                        # R3-C2: classify_state proves the journal is TERMINAL, not WHOSE run it certifies. A
                        # completed record whose txn_id projects at ANOTHER run's complete journal (a copied or
                        # re-pointed record) would otherwise read as a false no-op, so the durable INTENT frame
                        # must BIND THIS run: its own txn id, the header unit (run_id), the import-apply kind,
                        # and the plan_digest the record carries. A missing/malformed INTENT or any mismatch is
                        # fail-closed (reconcile through recover). This reads the INTENT the same way recover
                        # does (read_frames + _first), guarded against a JournalError read failure.
                        try:
                            noop_frames, _trn, _gl = _journal.read_frames(jr_fd, journal_root / noop_txn_id)
                        except _journal.JournalError as exc:
                            raise _cannot("the durable journal INTENT for completed run {} (txn {}) is "
                                          "unreadable ({}); a completed record whose journal identity cannot be "
                                          "read cannot certify an idempotent no-op (reconcile through recover; "
                                          "fail-closed)".format(run_id, noop_txn_id, exc))
                        noop_intent = _journal._first(noop_frames, _journal.F_INTENT)
                        noop_hdr = noop_intent.get("header") if isinstance(noop_intent, dict) else None
                        if not (isinstance(noop_intent, dict) and isinstance(noop_hdr, dict)
                                and noop_intent.get("txn") == noop_txn_id
                                and noop_hdr.get("unit") == run_id
                                and noop_hdr.get("kind") == "import-apply"
                                and noop_hdr.get("plan_digest") == txn_record.get("plan_digest")):
                            raise _cannot("the durable journal for completed run {} (txn {}) does not bind this "
                                          "run: its INTENT frame is missing/malformed or names a different "
                                          "run, kind, or plan digest; a projection pointing at another run's "
                                          "journal cannot certify an idempotent no-op (reconcile through "
                                          "recover; fail-closed)".format(run_id, noop_txn_id))
                        # R4-C1: bind the WHOLE record to the immutable journal, so R3-C1's trust in the
                        # record's archived_sources/acceptance_sha256 is warranted. FIX B proved the INTENT
                        # binds this run; that same INTENT's `ops` list records the create of THIS run's
                        # transaction.toml with its content-sha256 (_build_publication_ops), so the live
                        # record certifies a no-op only while its bytes still hash to that journal-recorded
                        # digest. A record modified after publication (even a self-consistent tamper of its
                        # own fields) no longer matches, so it cannot certify a no-op (reconcile through
                        # recover; fail-closed). capture_preimages adds prestate to each op but leaves the
                        # poststate untouched, so the recorded content-sha256 is the create's own.
                        # Round-7: the INTENT ops on the record path must be EXACTLY the producer's one create
                        # (kind "file", FILE_MODE, the hash), never a first-match over create/write: a later
                        # conflicting write/remove, a duplicate, or a create re-kinded "absent" is a journal that
                        # contradicts itself or the record. The SAME helper binds the gate, so the two cannot
                        # drift. R5-C1: it binds the SINGLE authenticated read captured at step 3/4 (never a
                        # second read of the live record), so the exact bytes validated for identity and the
                        # archive below are the bytes the journal authenticates here; a two-read interleaving
                        # can no longer hand divergent bytes to the archive check and the journal bind.
                        noop_bind = _txn_record_intent_problem(noop_intent.get("ops"), noop_rec_rel, noop_rec_raw)
                        if noop_bind:
                            raise _cannot("the durable journal for completed run {} (txn {}) does not bind its "
                                          "retained transaction record: {}; it cannot certify an idempotent "
                                          "no-op (reconcile through recover; fail-closed)".format(
                                              run_id, noop_txn_id, noop_bind))
                        # R3-C1: the durable archive must be present AND type/content-valid, not merely exist.
                        # The completed record self-describes its archive (FIX A: archived_sources +
                        # acceptance_sha256); a record lacking either recorded digest cannot certify a no-op. It
                        # is revalidated with the SAME contained no-follow readers the promote path uses,
                        # restricted to the immutable archive (never the live counters/indexes): acceptance is a
                        # regular file whose bytes hash to the recorded acceptance_sha256, and sources is a
                        # directory whose content-addressed regular-file bodies each hash to their own filename
                        # and whose name set exactly equals the recorded archived_sources set (a wrong type, a
                        # symlink, a corrupt body, or a missing/extra/renamed body each fails closed).
                        noop_arch_rel = _archive_run_rel(run_id)
                        noop_acc_sha = txn_record.get("acceptance_sha256")
                        noop_arch_srcs = txn_record.get("archived_sources")
                        if not (isinstance(noop_acc_sha, str) and _HEX64_RE.match(noop_acc_sha)
                                and isinstance(noop_arch_srcs, list)
                                and all(isinstance(s, str) and _HEX64_RE.match(s) for s in noop_arch_srcs)):
                            raise _cannot("the completed record for run {} carries no valid durable-evidence "
                                          "digests (archived_sources + acceptance_sha256); a completed record "
                                          "that cannot describe its own durable archive cannot certify an "
                                          "idempotent no-op (reconcile through recover; fail-closed)".format(
                                              run_id))
                        acc_st = _journal._lstat_contained(root_fd, noop_arch_rel + "/" + ACCEPTANCE_NAME)
                        if acc_st is None or not stat.S_ISREG(acc_st.st_mode):
                            raise _cannot("the durable import archive for completed run {} is missing its "
                                          "acceptance, or the acceptance is not a regular file; a completed "
                                          "record with no valid durable archive cannot certify an idempotent "
                                          "no-op (reconcile through recover; fail-closed)".format(run_id))
                        try:
                            noop_acc_raw, _accst = _journal._read_contained(
                                root_fd, noop_arch_rel + "/" + ACCEPTANCE_NAME)
                        except _journal.JournalError as exc:
                            raise _cannot("the durable archived acceptance for completed run {} is unreadable "
                                          "({}); a completed record whose durable archive cannot be read cannot "
                                          "certify an idempotent no-op (reconcile through recover; fail-closed)"
                                          .format(run_id, exc))
                        if _sha256_hex(noop_acc_raw) != noop_acc_sha:
                            raise _cannot("the durable archived acceptance for completed run {} does not match "
                                          "its recorded content hash; the immutable archive is corrupt or "
                                          "tampered, so it cannot certify an idempotent no-op (reconcile through "
                                          "recover; fail-closed)".format(run_id))
                        srcs_st = _journal._lstat_contained(root_fd, noop_arch_rel + "/sources")
                        if srcs_st is None or not stat.S_ISDIR(srcs_st.st_mode):
                            raise _cannot("the durable import archive for completed run {} is missing its "
                                          "preserved-source directory, or it is not a directory; a completed "
                                          "record with no valid durable archive cannot certify an idempotent "
                                          "no-op (reconcile through recover; fail-closed)".format(run_id))
                        # R6-C1: capture the listing ONCE and fail closed on None. _list_contained returns
                        # None on a FileNotFoundError at the sources dir, an OBSERVED disappearance. The dir was
                        # just S_ISDIR-confirmed above, so a None here is a genuine race/fault disappearance, not
                        # normal absence; the prior `or []` collapsed it to an empty listing, which for a
                        # ZERO-SOURCE completed record (archived_sources == []) then equalled the recorded empty
                        # set and false-cleaned the no-op over a durable sources dir that vanished under
                        # revalidation. An unreadable/absent durable directory is a fail-closed cannot-evaluate,
                        # never nothing-to-check (check-fails-closed-on-unreadable).
                        noop_src_names = _list_contained(root_fd, noop_arch_rel + "/sources")
                        if noop_src_names is None:
                            raise _cannot("the durable archived sources directory for completed run {} "
                                          "disappeared at revalidation (it was confirmed present, then could "
                                          "not be listed as present); a completed record whose durable sources "
                                          "cannot be enumerated cannot certify an idempotent no-op (reconcile "
                                          "through recover; fail-closed)".format(run_id))
                        noop_seen_bodies = set()
                        for bname in noop_src_names:
                            body_rel = noop_arch_rel + "/sources/" + bname
                            body_st = _journal._lstat_contained(root_fd, body_rel)
                            if body_st is None or not stat.S_ISREG(body_st.st_mode):
                                raise _cannot("the durable archived source body {} for completed run {} is not "
                                              "a regular file; the immutable archive is malformed, so it cannot "
                                              "certify an idempotent no-op (reconcile through recover; "
                                              "fail-closed)".format(body_rel, run_id))
                            try:
                                body_raw, _bst = _journal._read_contained(root_fd, body_rel)
                            except _journal.JournalError as exc:
                                raise _cannot("the durable archived source body {} for completed run {} is "
                                              "unreadable ({}); a completed record whose durable archive cannot "
                                              "be read cannot certify an idempotent no-op (reconcile through "
                                              "recover; fail-closed)".format(body_rel, run_id, exc))
                            if _sha256_hex(body_raw) != bname:
                                raise _cannot("the durable archived source body {} for completed run {} does "
                                              "not match its content-address (sha256) filename; the immutable "
                                              "archive is corrupt or tampered, so it cannot certify an "
                                              "idempotent no-op (reconcile through recover; fail-closed)".format(
                                                  body_rel, run_id))
                            noop_seen_bodies.add(bname)
                        if noop_seen_bodies != set(noop_arch_srcs):
                            raise _cannot("the durable archived source bodies for completed run {} do not match "
                                          "the record's archived_sources set (missing, extra, or renamed body); "
                                          "the immutable archive is incomplete or tampered, so it cannot certify "
                                          "an idempotent no-op (reconcile through recover; fail-closed)".format(
                                              run_id))
                        return ApplyResult(CLEAN, [], promoted=True, outcome="noop_already_complete",
                                           restore_ref=txn_record.get("restore_ref"))
                    raise _cannot("a transaction record for run {} exists in a non-complete state {!r}; the "
                                  "run must be reconciled through recover before re-apply (fail-closed; never "
                                  "renumber)".format(run_id, txn_record.get("state")))

                # --- step 3: load the staged run fail-closed (reuse the review loader's digest binding) ----
                plan_digest, inventory_digest, frag_by_id, key_meta, _ordered = \
                    _load_staged_run_for_review(root_fd, run_rel)
                if accepted_plan_digest is not None and accepted_plan_digest != plan_digest:
                    raise _finding("accepted_plan_digest {!r} does not match the staged plan digest {!r} "
                                   "(stale binding; re-plan)".format(accepted_plan_digest, plan_digest))

                # --- step 3.5 (PRC finding-2): re-run the FULL staged-artifact integrity gate over the run,
                # exactly as review_import does before capturing acceptance, so apply promotes with the SAME
                # validation completeness as review rather than only the narrower loader's plan/inventory
                # binding. This binds every candidate artifact to its recorded digests (artifact-digest-
                # integrity, lf-bijection/quad, inventory- and report-binding digests, source-preservation,
                # acceptance-*), catching a candidate tampered or corrupted between review and apply before any
                # promotion. At pre-promotion the transaction-schema/consistency checks pass ("run not yet
                # applied"); a valid reviewed run passes every check.
                run_dir_path = os.path.join(str(resolution.store_root), run_rel)
                # The store's generation, derived once from the authoritative manifest for this gate and
                # step 6; step 6's layout read must agree with it.
                homes = _opf_store.homes_generation(
                    _read_toml(root_fd, "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)))
                try:
                    import check_opf_import   # lazy: mirrors review_import's call-time import (circular)
                    gate_results = check_opf_import.check_staged_run(run_dir_path, homes=homes)
                    gate_findings = sorted(cid for cid, (ok, _d) in gate_results.items() if not ok)
                    if gate_findings:
                        raise _finding("staged run fails the import-operation gate at apply; not promotable "
                                       "until it is a coherent, promotion-ready run (failing checks: {})".format(
                                           ", ".join(gate_findings)))
                except _StageError:
                    raise
                except Exception as exc:
                    raise _cannot("the import-operation gate could not be loaded, raised, or returned a "
                                  "malformed result over the staged run at apply ({!r}); cannot promote".format(
                                      exc))

                # --- step 5: acceptance validation + binding ------------------------------------------------
                acceptance = _read_json_acceptance(root_fd, run_rel)
                if acceptance is None:
                    raise _cannot("run {} has no acceptance.json; it is not promotion-ready (capture a review "
                                  "with `opf import --review` first)".format(run_id))
                schema_findings = _validate_acceptance(acceptance)
                if schema_findings:
                    raise _cannot("acceptance.json is malformed ({}); cannot promote".format(
                        "; ".join(schema_findings)))
                if acceptance.get("run_id") != run_id:
                    raise _finding("acceptance.json binds run {!r}, not {!r} (stale binding; re-review)".format(
                        acceptance.get("run_id"), run_id))
                if acceptance.get("plan_digest") != plan_digest \
                        or acceptance.get("inventory_digest") != inventory_digest:
                    raise _finding("acceptance.json digests do not bind the staged run (stale acceptance; "
                                   "re-review the current plan)")
                dec_findings, _normalized = _validate_review_decisions(
                    acceptance.get("decisions", []), frag_by_id, key_meta)
                if dec_findings:
                    raise _finding("acceptance decisions do not validate against the staged run ({}); a "
                                   "rejected or incomplete acceptance blocks promotion (re-plan with "
                                   "origin=human_revision)".format("; ".join(dec_findings)))
                if any(d.get("decision") == "reject" for d in acceptance.get("decisions", [])
                       if isinstance(d, dict)):
                    return ApplyResult(FINDING,
                                       ["the acceptance record carries a reject decision; promotion is "
                                        "refused (resolution is a fresh `opf import --plan` carrying a "
                                        "human_revision mapping, never a silent re-label)"],
                                       promoted=False, outcome="rejected")

                # --- step 6: baseline freshness (derived base; no base fingerprint in report.toml) ---------
                active_types = _active_types(root_fd, machine_rel)
                roster = _roster()
                registered_vendors = _registered_vendors(root_fd, machine_rel)
                if _opf_store.homes_generation(_require_inline_layout(root_fd, machine_rel)) != homes:
                    raise _cannot("the store manifest's homes generation changed during apply; cannot promote")
                cand_counters = _read_toml(root_fd, run_rel + "/candidate/counters.toml")
                if cand_counters is None:
                    raise _cannot("staged run has no candidate/counters.toml (malformed run; cannot promote)")
                cand_high, cfind = _opf_schema.validate_counters(cand_counters)
                if cfind:
                    raise _cannot("staged candidate counters are malformed ({}); cannot promote".format(
                        "; ".join(cfind)))
                live_counters = _read_toml(root_fd, "{}/counters.toml".format(machine_rel))
                if live_counters is None:
                    raise _cannot("the live store has no counters.toml; cannot promote (spec 8.2)")
                live_high, lfind = _opf_schema.validate_counters(live_counters)
                if lfind:
                    raise _cannot("live counters are malformed ({}); cannot promote".format("; ".join(lfind)))
                candidate_types = _staged_candidate_types(root_fd, run_rel)
                minted = _minted_by_namespace(root_fd, run_rel, machine_rel, candidate_types)
                count_minted = {ns: len(ids) for ns, ids in minted.items()}
                freshness = _baseline_freshness_findings(live_high, cand_high, count_minted)
                if freshness:
                    raise _finding("baseline freshness: {} (the live store advanced since the plan; re-plan "
                                   "against the current store)".format("; ".join(freshness)))
                # Defence-in-depth: no minted id may already be seated in the live active/archive id set.
                live_ids = set(_active_store_ids(root_fd, machine_rel, active_types, roster,
                                                 registered_vendors))
                collide = sorted({rid for ids in minted.values() for rid in ids} & live_ids)
                if collide:
                    raise _finding("baseline freshness: minted id(s) {} are already seated in the live store "
                                   "(collision; re-plan)".format(", ".join(collide)))

                # --- step 8 (restore context) + step 9 (assemble, D4 composition, D5 render) ----------------
                observed_head = ""
                try:
                    import _opf_observe   # lazy: hardened git idiom (see _gather_review_context)
                    obs, _notes = _opf_observe.gather(resolution)
                    if isinstance(obs, dict):
                        prior = obs.get("prior")
                        if isinstance(prior, dict) and isinstance(prior.get("head"), str):
                            observed_head = prior["head"]
                except Exception:  # noqa: BLE001  git context is advisory; the journal preimages are the restore
                    observed_head = ""

                machine_files = _build_promotion_machine_files(
                    root_fd, machine_rel, run_rel, candidate_types, cand_high)
                import tempfile
                import shutil
                preview_dir = tempfile.mkdtemp(prefix="opf-import-apply-preview-")
                try:
                    _assemble_preview(resolution, machine_rel, machine_files, preview_dir, shutil, run_id,
                                      homes=homes)
                    view_files = _reconcile_preview_views(
                        preview_dir, root_fd, machine_rel, product_root)
                    if set(view_files) & set(machine_files):
                        raise _cannot("planned view destination collides with a machine publication target")
                    machine_files.update(view_files)
                    published_views = frozenset(view_files)
                finally:
                    shutil.rmtree(preview_dir, ignore_errors=True)

                # --- step 7 + step 10: build the journaled publication op set and publish -------------------
                txn_id = "import-{}.{}.{}".format(run_id, os.getpid(), time.time_ns())
                restore_ref = {"txn_id": txn_id, "journal_rel": IMPORT_JOURNAL_REL,
                               "observed_head": observed_head}
                ops, content = _build_publication_ops(
                    root_fd, machine_rel, run_rel, run_id, machine_files, acceptance,
                    plan_digest, inventory_digest, minted, txn_id, restore_ref, observed_head)
                # step 7: unmanaged-path pre-flight over the computed write set (defence in depth; every op
                # path must be an OPF-managed store path, the import-ops/archive tree, or the staging run dir).
                unmanaged = _unmanaged_paths(ops, machine_rel, run_rel, run_id, published_views)
                if unmanaged:
                    raise _cannot("promotion write set touches non-managed path(s): {} (fail-closed; no "
                                  "default action)".format(", ".join(unmanaged)))

                def staged_reader(op):
                    return content[op["path"]]

                header = {"unit": run_id, "kind": "import-apply", "plan_digest": plan_digest}
                txn_dir = journal_root / txn_id
                try:
                    _journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                             staged_reader, session_id="import-apply")
                except _journal.JournalError as exc:
                    # run_transaction rolled back from the durable preimages (verified restore) unless the
                    # rollback itself failed; classify and surface fail-closed, retaining evidence on an open
                    # (rollback-incomplete) journal for a later recover.
                    try:
                        state = _journal.classify_state(jr_fd, txn_dir)
                    except _journal.JournalError:
                        state = "open"
                    if state in ("nothing-opened", "rolled-back"):
                        return ApplyResult(CANNOT_EVALUATE,
                                           ["promotion aborted and rolled back to the pre-apply store ({}); "
                                            "nothing promoted".format(exc)],
                                           promoted=False, outcome="aborted", restore_ref=restore_ref)
                    retain_lock = True   # rollback incomplete: hold the lock for `recover`, do not release
                    return ApplyResult(CANNOT_EVALUATE,
                                       ["promotion FAILED and the rollback did not complete ({}); the "
                                        "transaction is left open under the retained lock for `recover`; "
                                        "fail-closed".format(exc)],
                                       promoted=False, outcome="aborted", restore_ref=restore_ref)

                # --- post-publish verification (defence in depth; identical bytes -> passes) ---------------
                # Grade the LIVE (now-promoted) store by resolving from product_root, which re-resolves both a
                # default store (product root == store root) AND a pointer store correctly, so post-publish
                # validation runs on EVERY store, not only default-resolution ones (PRC-F5: a pointer store is
                # the common case -- an `opf init` store carries a committed pointer). A fresh resolve reads the
                # post-publication content.
                post_findings = _preview_composition_findings(str(product_root))
                if _preview_render_reproducible(product_root) != (True, False):
                    post_findings.append("live render --check did not certify reproducible views")
                if post_findings:
                    return ApplyResult(CANNOT_EVALUATE,
                                       ["post-publish verification failed over the LIVE store ({}); "
                                        "the promotion committed; inspect the retained journal before "
                                        "recovery (no automatic rollback of COMPLETE)".format(
                                            "; ".join(post_findings))],
                                       promoted=True, outcome="promoted", restore_ref=restore_ref)
                return ApplyResult(CLEAN, [], promoted=True, outcome="promoted", restore_ref=restore_ref)
            finally:
                # Release the writer lock on EVERY exit path we hold it, except the rollback-incomplete path
                # (retain_lock) which keeps it for `recover`; never release a lock a live owner holds (the
                # possibly-live path never set we_hold_lock). A release failure here does NOT overturn an
                # already-formed result: the promotion (or abort) has committed and its ApplyResult stands.
                # release_lock can raise EITHER _journal.JournalError OR a bare OSError (from its os.unlink), so
                # BOTH are caught here (PRC-F6 round-3): letting an OSError escape to the outer handler would
                # flip a committed promotion to promoted=False, exactly the overturn this block must prevent.
                # The un-released lock becomes a stale lock; once THIS apply's process exits (the normal case)
                # its recorded owner is dead, so the NEXT apply's _claim_apply_lock sees a dead owner,
                # reconciles, and re-acquires -- recoverable. A still-LIVE owner is classified possibly-live and
                # NOT seized (the concurrency lease), which is the same-process re-entry edge, not the normal
                # process-exit path.
                if we_hold_lock and not retain_lock:
                    try:
                        _journal.release_lock(journal_root)
                    except (_journal.JournalError, OSError):
                        pass   # recoverable: a dead-owner stale lock is reconciled by the next apply (see above)
                if jr_fd is not None:
                    # PRC-N2 round-4: a descriptor-close OSError on this cleanup path must not overturn a
                    # committed promotion (the F6 hazard: a raw os.close raising in `finally` reaches the
                    # outer handler and flips promoted=True to aborted). _close_fd_quietly swallows it.
                    _journal._close_fd_quietly(jr_fd)
        finally:
            _journal._close_fd_quietly(root_fd)
    except _StageError as exc:
        return ApplyResult(exc.verdict, [exc.message], promoted=False,
                           outcome="rejected" if exc.verdict == FINDING else "aborted")
    except _journal.JournalError as exc:
        return ApplyResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)], promoted=False, outcome="aborted")
    except OSError as exc:
        return ApplyResult(CANNOT_EVALUATE, ["fail-closed on a filesystem error: {}".format(exc)],
                           promoted=False, outcome="aborted")
    except RecursionError as exc:
        return ApplyResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)],
                           promoted=False, outcome="aborted")


# --- self-test ---------------------------------------------------------------------------------------

def _self_test_d5(check, build_apply_store, bi_candidate, review_accept_all, now):
    """Physical D5 vectors; run only in a writable, isolated self-test scratch tree."""
    import subprocess
    from unittest.mock import patch
    import _opf_views

    mirror = ".working/BACKLOG_ITEM-INDEX.md"

    def require(name, condition):
        check(name, condition)
        if not condition:
            raise RuntimeError("D5 fixture prerequisite failed: " + name)

    def snapshot(root):
        result = {}
        def unreadable(exc):
            raise exc
        for directory, dirs, files in os.walk(root, onerror=unreadable, followlinks=False):
            for name in sorted(dirs + files):
                path = Path(directory) / name
                st = path.lstat()
                rel = str(path.relative_to(root))
                if stat.S_ISDIR(st.st_mode):
                    result[rel] = ("dir", stat.S_IMODE(st.st_mode))
                elif stat.S_ISREG(st.st_mode):
                    result[rel] = ("file", stat.S_IMODE(st.st_mode), path.read_bytes())
                else:
                    raise RuntimeError("D5 fixture contains a non-regular entry: " + rel)
        return result

    def planned_bytes(root):
        res = _opf_store.resolve_store(root)
        require("D5-resolved", res.status == _opf_store.RESOLVED)
        fd = _opf_store._open_store_root_fd(res.store_root, res.pointer_source != "default")
        try:
            return {dest: text.encode("utf-8")
                    for _name, scope, dest, text in _opf_views.plan_views(fd, res.machine_rel)
                    if scope == "store"}
        finally:
            os.close(fd)

    def fixture(mapped=True):
        root, machine = build_apply_store()
        model = tomllib.loads((machine / "manifest.toml").read_text(encoding="utf-8"))
        model["views"]["BACKLOG_ITEM-INDEX.md"] = {
            "kind": "deterministic", "sources": ["backlog_item"], "target": mirror}
        (machine / "manifest.toml").write_text(_opf_emit.emit_checked(model), encoding="utf-8")
        res = _opf_store.resolve_store(root)
        require("D5-seed-render", _opf_views._render_resolved_store(root, res, False) == 0)
        require("D5-seed-check", _preview_render_reproducible(root) == (True, False))
        if mapped:
            # Two whole-file fragments: mapped BI plus LF schema deferral. The review loader's
            # one-to-one inventory/mapping correspondence is preserved.
            (root / "b.txt").write_bytes(b"quarantine companion")
            sources = ["a.txt", "b.txt"]
            scan = scan_import(root, sources)
            require("D5-scan", scan.verdict == CLEAN)
            plan = {"fragments": {
                "a.txt": [{"span": [0, (root / "a.txt").stat().st_size], "state": "mapped",
                           "origin": "human_revision", "record": bi_candidate()}],
                "b.txt": [{"span": [0, (root / "b.txt").stat().st_size], "state": "unmapped",
                           "origin": "baseline"}],
            }}
            staged = stage_import(root, sources, plan, now=now, run_nonce="d5-mapped")
            require("D5-stage", staged.verdict == CLEAN and bool(staged.run_id))
            report = _render_report_md(scan.inventory_digest, scan.fragments, [], staged.run_id)
            _write_plan_artifacts(root, staged.run_rel, staged.run_id, scan.inventory,
                                  report.encode("utf-8"), [])
        else:
            staged = plan_import(root, ["a.txt"], now=now, run_nonce="d5-quarantine")
            require("D5-quarantine-plan", staged.verdict == CLEAN and bool(staged.run_id))
        require("D5-review", review_accept_all(root, staged.run_id).verdict == CLEAN)
        return root, machine, staged.run_id

    def apply_capturing(root, run):
        captured = []
        original = _journal.run_transaction
        def capture(*args, **kwargs):
            captured.extend(copy.deepcopy(args[5]))
            return original(*args, **kwargs)
        with patch.object(_journal, "run_transaction", side_effect=capture):
            result = apply_import(root, run, now=now)
        return result, captured

    root, machine, run = fixture()
    before_views = planned_bytes(root)
    result, ops = apply_capturing(root, run)
    require("D5-view-feeding-promotes", result.verdict == CLEAN and result.promoted
            and result.outcome == "promoted")
    after_views = planned_bytes(root)
    expected_drift = {p for p in after_views if before_views.get(p) != after_views[p]}
    check("D5-mirror-and-named-views-feed",
          mirror in expected_drift and ".working/BACKLOG.md" in expected_drift
          and b"BI-1" in (root / mirror).read_bytes())
    check("D5-published-view-set-exact",
          {op["path"] for op in ops if op["path"] in after_views} == expected_drift)
    check("D5-published-byte-canon",
          all((root / p).read_bytes() == data for p, data in after_views.items()))
    check("D5-live-render-check", _preview_render_reproducible(root) == (True, False))
    check("D5-lf-also-promoted",
          len(tomllib.loads((machine / "legacy_fragment.index.toml").read_text())["record"]) == 1)
    check("D5-view-before-terminal-delete",
          ops[-1]["op"] == "rmdir" and ops[-1]["path"] == IMPORTS_REL + "/" + run)
    noop_before = snapshot(root)
    with patch(__name__ + "._reconcile_preview_views", side_effect=AssertionError("no-op rendered")):
        noop = apply_import(root, run, now=now)
    check("D5-idempotent-noop", noop.verdict == CLEAN and noop.promoted
          and noop.outcome == "noop_already_complete" and snapshot(root) == noop_before)

    # Witness disagreement must stop before run_transaction, after an actual preview overlay.
    root, machine, run = fixture()
    before = snapshot(root / ".working")
    original_check = _preview_render_reproducible
    calls = []
    def disagree(path):
        if Path(path) != root:
            calls.append(str(path))
            if len(calls) == 2:
                return True, True
        return original_check(path)
    with patch(__name__ + "._preview_render_reproducible", side_effect=disagree), \
            patch(__name__ + "._overlay_preview_view_files",
                  wraps=_overlay_preview_view_files) as overlay, \
            patch.object(_journal, "run_transaction", wraps=_journal.run_transaction) as transaction:
        result = apply_import(root, run, now=now)
    check("D5-witness-fails-closed", result.verdict == CANNOT_EVALUATE and not result.promoted
          and len(calls) == 2 and overlay.call_count == 1 and transaction.call_count == 0
          and any("witness failed" in msg for msg in result.findings))
    check("D5-witness-preserves-store", snapshot(root / ".working") == before
          and not (root / _archive_run_rel(run)).exists()
          and not (root / _txn_record_rel(run)).exists())

    # A second D4 pass is independently load-bearing.
    root, machine, run = fixture()
    before = snapshot(root / ".working")
    original_composition = _preview_composition_findings
    calls = []
    def broken_post(path, **kwargs):
        calls.append(kwargs.get("defer_view_drift", False))
        if len(calls) == 2:
            return ["injected post-overlay composition fault"]
        return original_composition(path, **kwargs)
    with patch(__name__ + "._preview_composition_findings", side_effect=broken_post), \
            patch.object(_journal, "run_transaction", wraps=_journal.run_transaction) as transaction:
        result = apply_import(root, run, now=now)
    check("D5-post-overlay-D4-required", result.verdict == FINDING and not result.promoted
          and calls == [True, False] and transaction.call_count == 0
          and snapshot(root / ".working") == before)

    # Immediate exception rollback, then rollback-incomplete with retained lock and later recovery.
    for target_kind, incomplete in (("view", False), ("records", False), ("view", True)):
        root, machine, run = fixture()
        before = snapshot(root / ".working")
        original_transaction = _journal.run_transaction
        original_kill = _journal._kill_point
        original_restore = _journal._restore_preimage
        fired = []
        def failing_transaction(*args, **kwargs):
            publication_ops = args[5]
            target = (mirror if target_kind == "view"
                      else str(machine.relative_to(root)) + "/counters.toml")
            index = next(i for i, op in enumerate(publication_ops) if op["path"] == target)
            require("D5-target-is-write", publication_ops[index]["op"] == "write")
            def fail_after_view(name):
                if name == "after-apply-{}".format(index):
                    fired.append((
                        (root / mirror).read_bytes() != before["BACKLOG_ITEM-INDEX.md"][2],
                        len(tomllib.loads((machine / "backlog_item.index.toml").read_text())["record"])))
                    raise _journal.JournalError("D5 injected failure after publication write")
                return original_kill(name)
            def fail_restore(*rargs, **rkwargs):
                if incomplete and rargs[3]["path"] == mirror:
                    raise _journal.JournalError("D5 injected view restore failure")
                return original_restore(*rargs, **rkwargs)
            with patch.object(_journal, "_kill_point", side_effect=fail_after_view), \
                    patch.object(_journal, "_restore_preimage", side_effect=fail_restore):
                return original_transaction(*args, **kwargs)
        with patch.object(_journal, "run_transaction", side_effect=failing_transaction):
            result = apply_import(root, run, now=now)
        require("D5-cutover-failure-reached", len(fired) == 1 and fired[0][0]
                and (target_kind != "records" or fired[0][1] == 1)
                and result.verdict == CANNOT_EVALUATE and not result.promoted
                and result.outcome == "aborted")
        journal_root = root / IMPORT_JOURNAL_REL
        rfd = _opf_store._open_store_root_fd(root, False)
        jfd = _journal.open_journal_root_fd(rfd, IMPORT_JOURNAL_REL)
        try:
            txn_dir = journal_root / result.restore_ref["txn_id"]
            check("D5-rollback-classification-" + target_kind + "-" + str(incomplete),
                  _journal.classify_state(jfd, txn_dir) == ("open" if incomplete else "rolled-back"))
            if incomplete:
                require("D5-rollback-retains-lock", _journal.read_lock_owner(journal_root) is not None)
                check("D5-view-recovery", _journal.recover(jfd, txn_dir, rfd) == "rolled-back")
                check("D5-view-recovery-idempotent", _journal.recover(jfd, txn_dir, rfd) == "terminal")
                _journal.release_lock(journal_root)
            else:
                check("D5-rollback-releases-lock", _journal.read_lock_owner(journal_root) is None)
        finally:
            os.close(jfd)
            os.close(rfd)
        check("D5-rollback-restores-records-and-views-" + target_kind + "-" + str(incomplete),
              snapshot(root / ".working") == before)

    # Real crash: derive the kill index from this dispatch's op list, never a guessed ordinal.
    root, machine, run = fixture()
    before = snapshot(root / ".working")
    before_mirror = (root / mirror).read_bytes()
    child = """
import datetime
import os
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import _opf_import as imp
import _journal
now = datetime.datetime.fromisoformat(sys.argv[5])
original = imp._build_publication_ops
def arm(*args, **kwargs):
    ops, content = original(*args, **kwargs)
    index = next(i for i, op in enumerate(ops) if op['path'] == sys.argv[4])
    assert ops[index]['op'] == 'write'
    os.environ[_journal.KILL_ENV] = 'after-apply-{}'.format(index)
    return ops, content
imp._build_publication_ops = arm
result = imp.apply_import(Path(sys.argv[2]), sys.argv[3], now=now)
raise SystemExit(result.verdict)
"""
    cp = subprocess.run([sys.executable, "-I", "-B", "-c", child,
                         str(Path(__file__).resolve().parent), str(root), run, mirror, now.isoformat()],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    require("D5-killed-after-view", cp.returncode == 137
            and (root / mirror).read_bytes() != before_mirror)
    journal_root = root / IMPORT_JOURNAL_REL
    rfd = _opf_store._open_store_root_fd(root, False)
    jfd = _journal.open_journal_root_fd(rfd, IMPORT_JOURNAL_REL)
    try:
        txns = _journal._journal_txn_dirs(jfd, journal_root)
        require("D5-crash-journal-enumeration", len(txns) == 1)
        require("D5-crash-open", _journal.classify_state(jfd, txns[0]) == "open")
        require("D5-claim-recovers-dead-owner", _claim_apply_lock(journal_root, jfd, rfd) == "acquired")
        check("D5-crash-recovery-restores", snapshot(root / ".working") == before
              and _journal.classify_state(jfd, txns[0]) == "rolled-back")
        check("D5-crash-recovery-idempotent",
              _recover_open_txns(jfd, journal_root, rfd) == {txns[0].name: "terminal"})
        _journal.release_lock(journal_root)
    finally:
        os.close(jfd)
        os.close(rfd)
    retry = apply_import(root, run, now=now)
    check("D5-retry-after-crash", retry.verdict == CLEAN and retry.promoted
          and _preview_render_reproducible(root) == (True, False))

    allowed = frozenset({mirror})
    unmanaged_ops = [{"op": "write", "path": p} for p in (
        mirror, ".working/imports/other", ".working/x", mirror + ".bak", mirror + "/child")]
    check("D5-exact-allow-set",
          _unmanaged_paths(unmanaged_ops, ".working/toml", IMPORTS_REL + "/this", "this", allowed)
          == sorted(op["path"] for op in unmanaged_ops[1:]))
    check("D5-default-allow-set",
          _unmanaged_paths(unmanaged_ops, ".working/toml", IMPORTS_REL + "/this", "this")
          == sorted(op["path"] for op in unmanaged_ops))
    check("D5-view-delete-not-allowed",
          _unmanaged_paths([{"op": "remove", "path": mirror}],
                           ".working/toml", IMPORTS_REL + "/this", "this", allowed) == [mirror])

    root, machine, run = fixture(mapped=False)
    views_before = planned_bytes(root)
    with patch(__name__ + "._render_preview_view_targets",
               side_effect=AssertionError("quarantine requested view regeneration")):
        result, ops = apply_capturing(root, run)
    check("D5-quarantine-no-view-ops", result.verdict == CLEAN and result.promoted
          and not any(op["path"] in views_before for op in ops)
          and all((root / p).read_bytes() == data for p, data in views_before.items()))

    # Version candidates are currently refused by staging. Simulate their future merged payload
    # at the builder seam to exercise apply's product-scope guard without changing the staging contract.
    root, machine, run = fixture()
    before = snapshot(root / ".working")
    original_builder = _build_promotion_machine_files
    def version_candidate(*args, **kwargs):
        files = original_builder(*args, **kwargs)
        manifest_rel = args[1] + "/manifest.toml"
        model = tomllib.loads(files[manifest_rel].decode("utf-8"))
        model["views"]["VERSION"] = {"kind": "deterministic", "sources": ["version"], "target": "VERSION"}
        files[manifest_rel] = _emit_bytes(model, "manifest.toml")
        files[args[1] + "/version.toml"] = _emit_bytes({
            "schema": SCHEMA, "summary": [],
            "release": [{"version": "0.1.0", "date": "2026-01-01T00:00:00Z",
                         "worklog_span": [], "coverage_digest": _opf_release.coverage_digest([])}],
        }, "version.toml")
        return files
    with patch(__name__ + "._build_promotion_machine_files", side_effect=version_candidate), \
            patch.object(_journal, "run_transaction", wraps=_journal.run_transaction) as transaction:
        result = apply_import(root, run, now=now)
    check("D5-VERSION-refusal", result.verdict == FINDING and not result.promoted
          and any("promotion would drift the product-root VERSION deliverable; "
                  "not re-rendered in this build" in msg for msg in result.findings)
          and transaction.call_count == 0 and snapshot(root / ".working") == before
          and not (root / "VERSION").exists())


    # Attribute the temporary output deferral precisely; never tolerate a source fault by its text.
    import _opf_check
    res = _opf_store.resolve_store(root)
    cases = [
        ("view-finding", "C-VIEW-DRIFT", "FINDING", "view drift", [], False),
        ("view-cannot", "C-VIEW-DRIFT", "CANNOT-EVALUATE", "unreadable view", [], True),
        ("source-finding", "C-RECORDS", "FINDING", "C-VIEW-DRIFT: misleading text", [], True),
        ("source-cannot", "C-RECORDS", "CANNOT-EVALUATE", "missing required worklog", [], True),
        ("schema-deferral", "C-RECORDS", "CANNOT-EVALUATE", _opf_check._SCHEMA_DEFERRAL, [], False),
        ("unattributed", "C-VIEW-DRIFT", "FINDING", "view drift", ["internal fault"], True),
    ]
    for label, cid, verdict, message, unattributed, expected_bad in cases:
        checks = {name: "PASS" for name in _opf_check.REQUIRED_CHECKS}
        checks[cid] = verdict
        report = _opf_check.StoreValidation(
            _opf_check.CANNOT_EVALUATE if verdict == "CANNOT-EVALUATE" else _opf_check.INVALID,
            findings=[message] if verdict == "FINDING" else [],
            cannot_evaluate=[message] if verdict == "CANNOT-EVALUATE" else [],
            checks=checks, by_check={cid: [message]}, unattributed=unattributed)
        with patch.object(_opf_check, "validate_store", return_value=report):
            check("D5-attribution-" + label,
                  bool(_preview_composition_findings(root, defer_view_drift=True)) == expected_bad)
            if label == "view-finding":
                check("D5-default-D4-still-refuses-drift", bool(_preview_composition_findings(root)))

    # A plan may not rebind a valid view name to a staging sibling.
    fd = _opf_store._open_store_root_fd(root, False)
    try:
        with patch.object(_opf_views, "plan_views",
                          return_value=[("BACKLOG_ITEM-INDEX.md", "store",
                                         ".working/imports/other", "bad\n")]):
            try:
                _render_preview_view_targets(root, fd, res.machine_rel, root)
            except _StageError as exc:
                check("D5-rebound-target-refused", exc.verdict == CANNOT_EVALUATE)
            else:
                check("D5-rebound-target-refused", False)
    finally:
        os.close(fd)

    # A post-COMPLETE fault must not be reported as an aborted, unpromoted transaction.
    root, machine, run = fixture()
    live_calls = []
    def post_fault(path):
        if Path(path) == root:
            live_calls.append(str(path))
            if len(live_calls) == 2:
                return False, False
        return original_check(path)
    with patch(__name__ + "._preview_render_reproducible", side_effect=post_fault):
        result = apply_import(root, run, now=now)
    check("D5-post-publish-failure-keeps-promoted",
          result.verdict == CANNOT_EVALUATE and result.promoted
          and result.outcome == "promoted" and len(live_calls) == 2
          and (root / _txn_record_rel(run)).is_file())


def _self_test_pi(check, build_store, snapshot, now, nonce):
    """MIG-PR3 additive plan inputs and the pre-journal apply boundary."""
    import shutil
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def fixture():
        root = None
        try:
            root, machine = build_store(sources={"a.txt": "aaaa"})
            yield root, machine
        finally:
            if root is not None:
                shutil.rmtree(root)

    def planned(root, machine, **kwargs):
        result = plan_import(root, ["a.txt"], now=now, run_nonce=nonce, **kwargs)
        check("clean", result.verdict == CLEAN and bool(result.run_id))
        if result.verdict != CLEAN or not result.run_id:
            return None
        return machine.parent / "imports" / result.run_id

    def read(run, name):
        return tomllib.loads((run / name).read_text(encoding="utf-8"))

    def provenance():
        importer = {"source_path": "a.txt", "span": [0, 2], "suggested_state": "mapped"}
        model = dict(importer, span=[2, 4])
        with fixture() as (root, machine):
            run = planned(root, machine, importer_proposals=[importer], proposals=[model])
            if run is not None:
                rows = read(run, "proposals.toml")["proposal"]
                check("distinct-origins", len(rows) == 2 and
                      {(tuple(p["span"]), p["origin"]) for p in rows} ==
                      {((0, 2), "importer_proposal"), ((2, 4), "model_proposal")})
        with fixture() as (root, machine):
            result = plan_import(root, ["a.txt"], importer_proposals=[importer],
                                 proposals=[importer], now=now, run_nonce=nonce)
            check("same-span-tagged-twice", result.verdict == FINDING)
            check("collision-nothing-staged", not (machine.parent / "imports").exists())

    def additive():
        draft = {"source_path": "a.txt", "importer_kind": "github-tasklist",
                 "type": "backlog_item", "title": "Draft only"}
        action = {"kind": "keep", "scope": "declared", "source_path": "a.txt",
                  "unmanaged_path": "a.txt", "sha256": "sha256:" + _sha256_hex(b"aaaa"), "size": 4}
        with fixture() as (plain_root, plain_machine), fixture() as (root, machine):
            plain = planned(plain_root, plain_machine)
            before = snapshot(machine)
            run = planned(root, machine, candidates_draft=[draft], ingest_actions=[action])
            if plain is None or run is None:
                return
            baseline, augmented = snapshot(plain), snapshot(run)
            check("baseline-candidate-present",
                  bool(snapshot(plain / "candidate")) and
                  bool(read(plain, "fragments/legacy_fragment.index.toml")["record"]))
            check("additive-file-set", set(augmented) == set(baseline) |
                  {"candidates_draft.toml", "ingest-actions.toml"})
            check("baseline-byte-intact", all(augmented.get(k) == v for k, v in baseline.items()))
            check("draft-payload", read(run, "candidates_draft.toml")["candidate"] == [draft])
            check("action-payload", read(run, "ingest-actions.toml")["action"] == [action])
            check("report-shape-unchanged", read(run, "report.toml") == read(plain, "report.toml"))
            check("active-store-unchanged", snapshot(machine) == before)

    def refuse():
        with fixture() as (root, machine):
            run = planned(root, machine, ingest_actions=[])
            if run is None:
                return
            check("empty-marker-present", read(run, "ingest-actions.toml")["action"] == [])
            before = snapshot(root)
            with patch.object(_journal, "ensure_journal_dirs",
                              wraps=_journal.ensure_journal_dirs) as journal, \
                 patch.object(sys.modules[__name__], "_claim_apply_lock",
                              wraps=_claim_apply_lock) as lock:
                result = apply_import(root, run.name, now=now)
            check("refused-before-journal-and-lock", result.verdict == CANNOT_EVALUATE
                  and result.promoted is False and not journal.called and not lock.called)
            check("no-journal-dir", not (root / IMPORT_JOURNAL_REL).exists())
            check("no-acceptance", not (run / "acceptance.json").exists())
            check("writes-nothing", snapshot(root) == before)

    outer_check = check
    for label, test in (
            ("Pi-importer-proposal-origin", provenance),
            ("Pi-ingest-artifacts-additive", additive),
            ("Pi-apply-refuses-ingest-marker", refuse)):
        check = lambda suffix, cond, label=label: outer_check(label + "/" + suffix, cond)
        test()



def _memory_ingest_run():
    """Synthetic staged bytes for read-only gate tests; no filesystem writer is used."""
    import _opf_ingest as ingest
    import _opf_importers as importers
    import tomllib

    now = datetime.datetime(2026, 9, 9, 12, tzinfo=datetime.timezone.utc)
    raw = b"- [ ] synthetic task\n"
    source = {"path": "legacy/tasks.md", "raw": raw, "sha256": _sha256_hex(raw), "size": len(raw)}
    plan = _baseline_plan([source])
    plan_bytes = _emit_bytes(plan, "plan")
    rid = _run_id([source], plan_bytes, now, "memory")
    stamp = _rfc3339(now)
    inventory, inv_digest, _sources, _frags = _build_inventory([source])
    r = dict(ingest._row(source["path"], "declared", "sha256:" + source["sha256"], len(raw)),
             disposition="migrate")
    worksheet, _digest = ingest._build_worksheet([r])
    output = importers.run_importer("github-tasklist", source)
    migrate = [dict(ingest.derive_migrate_scaffold(r, source["path"], "github-tasklist"),
                    candidate_count=len(output.candidates), proposal_count=len(output.proposals), loss=output.lossy)]
    crosswalk = [ingest.derive_crosswalk_row(r, source["path"])]
    candidates = [dict(c, source_path=source["path"], importer_kind="github-tasklist") for c in output.candidates]
    proposals = _sort_proposal_rows([dict(p, _origin=_IMPORTER_PROPOSAL_ORIGIN) for p in output.proposals])
    files = {}
    def put(name, obj):
        files[name] = _emit_bytes(obj, name)
    put("run.toml", _run_toml_model(rid, stamp, "memory", [source]))
    files["plan.toml"] = plan_bytes
    put("mappings.toml", _mappings_model({source["path"]: [
        {"span": [0, len(raw)], "state": "unmapped", "target": None, "origin": "baseline"}]}))
    put("candidate/counters.toml", {"schema": SCHEMA, "counters": {"LF": 1}})
    put("fragments/legacy_fragment.index.toml", {"schema": SCHEMA, "record": [
        _lf_record("LF-1", source["path"], source["sha256"], 0, len(raw), rid, stamp, raw.decode())]})
    files["sources/" + source["sha256"]] = raw
    put("report.toml", _run_report_model(rid, plan_bytes, inv_digest, {"unmapped": 1}, True, dict(files)))
    put(INVENTORY_NAME, inventory)
    put(INGEST_ACTIONS_NAME, {"format": INGEST_ACTIONS_FORMAT, "schema": SCHEMA, "run_id": rid, "action": []})
    put(CANDIDATES_DRAFT_NAME, {"schema": SCHEMA, "run_id": rid, "candidate": candidates})
    put(PROPOSALS_NAME, _proposals_model(rid, proposals))
    files[REPORT_MD_NAME] = (_render_report_md(inv_digest, inventory["fragment"], proposals, rid)
                            + _render_ingest_review_md(_ingest_render_model(
                                rid, crosswalk, migrate, [], candidates))).encode()
    binding = {"plan_digest": "sha256:" + _sha256_hex(plan_bytes), "inventory_digest": inv_digest}
    for name, field in ((INGEST_ACTIONS_NAME, "ingest_actions_digest"),
                        (CANDIDATES_DRAFT_NAME, "candidates_draft_digest"),
                        (PROPOSALS_NAME, "proposals_digest"), (INVENTORY_NAME, "inventory_toml_digest")):
        binding[field] = "sha256:" + _sha256_hex(files[name])
    put(INGEST_REVIEW_NAME, {"format": INGEST_REVIEW_FORMAT, "schema": SCHEMA, "run_id": rid,
        "include_declared": True, "include": [source["path"]], "binding": binding, "worksheet": worksheet,
        "options": {"format": ingest.OPTIONS_FORMAT, "schema": SCHEMA, "option": [
            {"scope": "declared", "source_path": source["path"], "importer_kind": "github-tasklist"}]},
        "crosswalk": crosswalk, "migrate": migrate})
    class Reader:
        path = Path("/synthetic") / IMPORTS_REL / rid
        tree = dict({name: "file" for name in files}, candidate="dir", fragments="dir", sources="dir")
        def read_bytes(self, name):
            return files[name]
        def load_toml(self, name):
            return tomllib.loads(files[name].decode())
        def kind(self, name):
            return self.tree.get(name)
    return Reader(), files


def _self_test_ingest_acceptance(check):
    """In-memory discriminators; each named flip removes the condition the assertion needs."""
    import copy
    import io
    from unittest.mock import patch
    import check_opf_import as gate
    import _opf_importers as importers
    import opf as cli

    rid = "imp-20260909T120000Z-0000000000000000"
    digest = "sha256:" + "0" * 64
    unit = {"kind": "disposition", "scope": "declared", "source_path": "a#conversion",
            "authority": {"disposition": "keep", "source_removal": None}}
    snapshot = {"run_id": rid, "binding": {"plan_digest": digest, "inventory_digest": digest,
                "homes_generation": 2, "report_digest": digest, "presentation_digest": digest},
                "units": [unit], "fragments": {}, "model": {}}
    decision = dict(unit, decision="accept", note="")
    acceptance = {"format": INGEST_ACCEPTANCE_FORMAT, "run_id": rid,
                  "plan_digest": digest, "inventory_digest": digest,
                  "actor": {"declared": "reviewer", "context": {"os_user": "", "git_identity": "", "hostname": ""}},
                  "reviewed_at": "2026-09-09T12:00:00Z", "signature": None, "decisions": [],
                  "ingest": {"format": INGEST_ACCEPTANCE_BLOCK, "binding": snapshot["binding"], "units": [decision]}}

    def verdict(a):
        binding, complete, rejected = validate_ingest_acceptance(snapshot, a)
        return not binding and not complete, rejected

    reader, files = _memory_ingest_run()
    reads = {}
    original_read = reader.read_bytes
    def counted_read(name):
        reads[name] = reads.get(name, 0) + 1
        return original_read(name)
    reader.read_bytes = counted_read
    actual = _ingest_snapshot(reader, 1)
    check("accept-single-byte-snapshot", set(reads) == set(files) and all(n == 1 for n in reads.values()))
    reader.read_bytes = original_read
    check("accept-full-model", actual["binding"]["review_model_digest"].startswith("sha256:"))
    check("accept-removal-bound", len(actual["binding"]["source_removals"]) == 1
          and actual["binding"]["source_removals"][0]["destination"].startswith(
              _ingest_acceptance_home(reader.path.name) + "/originals/"))
    before_model = actual["binding"]["review_model_digest"]
    counters = reader.load_toml("candidate/counters.toml")
    counters["counters"]["BI"] = 1
    files["candidate/counters.toml"] = _emit_bytes(counters, "counters")
    report = reader.load_toml("report.toml")
    for entry in report["artifact"]:
        entry["sha256"] = _sha256_hex(files[entry["path"]])
    files["report.toml"] = _emit_bytes(report, "report")
    check("accept-full-model-counters", _ingest_snapshot(reader, 1)["binding"]["review_model_digest"] != before_model)
    # Flip: a refusal that comes only from the TOML parser admits the parse-valid report.toml tail;
    # IMPORT-REPORT.md is never parsed, so its tail reaches only the byte-reproducibility check.
    for name, tail in (("report.toml", b"# parse-valid, not byte-re-derivable\n"),
                       (REPORT_MD_NAME, b"corruption")):
        prior = files[name]
        files[name] += tail
        try:
            if name == "report.toml":
                check("accept-report-tail-parses",
                      tomllib.loads(files[name].decode()) == tomllib.loads(prior.decode()))
            _ingest_snapshot(reader, 1)
            refused = False
        except _StageError:
            refused = True
        finally:
            files[name] = prior
        check("accept-report-recompute-" + name, refused)
    prior = files[INGEST_REVIEW_NAME]
    files[INGEST_REVIEW_NAME] = prior.replace(b"opf-ingest-review-bundle-v2", b"opf-ingest-review-bundle-v1")
    try:
        _ingest_snapshot(reader, 1)
        refused = False
    except _StageError:
        refused = True
    finally:
        files[INGEST_REVIEW_NAME] = prior
    check("accept-old-bundle-replan", refused)
    check("accept-schema-positive", verdict(acceptance) == (True, False))
    rejected = copy.deepcopy(acceptance)
    rejected["ingest"]["units"][0]["decision"] = "reject"
    check("accept-reject-is-valid", verdict(rejected) == (True, True))
    # Flip: comparing only plan/inventory loses homes/report/presentation and every added binding.
    for name in snapshot["binding"]:
        a = copy.deepcopy(acceptance)
        a["ingest"]["binding"][name] = "changed"
        check("accept-binding-" + name, verdict(a)[0] is False)
    for units in ([], [decision, decision], [dict(decision, source_path="other")],
                  [dict(decision, authority={})], [dict(decision, decision=[])], [{"group": "all"}]):
        a = copy.deepcopy(acceptance)
        a["ingest"]["units"] = units
        check("accept-unit-totality", verdict(a)[0] is False)
    for name, value in (("format", ACCEPTANCE_FORMAT), ("signature", "forged"),
                        ("reviewed_at", "not a date"), ("run_id", rid + "0"), ("unexpected", 1)):
        a = copy.deepcopy(acceptance)
        a[name] = value
        check("accept-schema-" + name, verdict(a)[0] is False)
    # Flip: the schema-1 reader, duplicate-key decoder, or bool-as-int admission fails one of these legs.
    envelope = _ingest_review_envelope(snapshot)
    raw = _emit_acceptance_bytes(envelope)
    check("accept-envelope-v2", cli._import_decode_decisions(raw, rid) == envelope)
    oversized = copy.deepcopy(envelope)
    oversized["ingest"]["units"][0]["note"] = "x" * _opf_store.MAX_STORE_READ_BYTES
    hostile = [b'{"schema":2,' + raw[1:],
               json.dumps({"schema": True, "run_id": rid, "decisions": []}).encode(),
               json.dumps(oversized).encode()]
    for value in hostile:
        try:
            cli._import_decode_decisions(value, rid)
            refused = False
        except ValueError:
            refused = True
        check("accept-envelope-discriminator", refused)
    for raw in (b'{"schema":2,"schema":2}', b'{"schema":true}', b'{"schema":1.0}',
                b"[" * 2000 + b"]" * 2000, b" " * (_opf_store.MAX_STORE_READ_BYTES + 1)):
        try:
            cli._import_decode_decisions(raw, rid)
            refused = False
        except (ValueError, RecursionError):
            refused = True
        check("accept-envelope-refuses", refused)
    check("accept-envelope-v1", cli._import_decode_decisions(
        _emit_acceptance_bytes({"schema": 1, "run_id": rid, "decisions": []}), rid) == [])
    # Flip: an ordinary schema-1 --decisions file read under the store-read cap (or strictly decoded) refuses
    # this file main accepts, and a generic wrapper loses main's located run-binding message.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        row = dict(fragment_id="f", decision="accept", origin="o", proposed_state="s", note="x" * 4096)
        rows = [row] * (_opf_store.MAX_STORE_READ_BYTES // 4096 + 8)
        big = os.path.join(tmp, "big.json")
        with open(big, "w", encoding="utf-8") as fh:
            json.dump(dict(schema=1, run_id=rid, decisions=rows), fh)
        try:
            big_ok = cli._import_read_decisions(big, rid) == rows
        except ValueError:
            big_ok = False
        check("decisions-v1-main-size", os.path.getsize(big) > _opf_store.MAX_STORE_READ_BYTES and big_ok)
        other = os.path.join(tmp, "other.json")
        with open(other, "w", encoding="utf-8") as fh:
            json.dump(dict(schema=1, run_id=rid + "0", decisions=[]), fh)
        try:
            cli._import_read_decisions(other, rid)
            message = ""
        except ValueError as exc:
            message = str(exc)
        check("decisions-v1-run-id-message", message.startswith("--decisions file run_id ")
              and "does not match the --review run-id" in message)
        # Flip: routing a schema-2 file through the schema-1 reader refuses this valid envelope (and would
        # decode the duplicate key below leniently).
        v2 = os.path.join(tmp, "v2.json")
        with open(v2, "wb") as fh:
            fh.write(_emit_acceptance_bytes(envelope))
        try:
            v2_ok = cli._import_read_decisions(v2, rid) == envelope
        except ValueError:
            v2_ok = False
        check("decisions-v2-envelope", v2_ok)
        dup = os.path.join(tmp, "dup.json")
        with open(dup, "wb") as fh:
            fh.write(_emit_acceptance_bytes(envelope).rstrip()[:-1] + b', "run_id": "' + rid.encode() + b'"' + b"}")
        try:
            cli._import_read_decisions(dup, rid)
            refused = False
        except ValueError:
            refused = True
        check("decisions-v2-strict", refused)
    check("accept-template-undecided", all("decision" not in u for u in envelope["ingest"]["units"]))
    # Flip: a statement placed above the docstring leaves the import verb undocumented (__doc__ is None).
    check("accept-cli-import-doc", cli._cmd_import.__doc__ is not None)
    # Flip: a subset pass, an extra result, a bool-like value, and a duplicate registry must each refuse.
    for results, registry in (({}, ("a",)), ({"a": (True, "")}, ("a", "b")),
                              ({"a": (1, "")}, ("a",)), ({"a": (True, 1)}, ("a",)),
                              ({"a": (True, "")}, ("a", "a")), (None, ("a",))):
        try:
            _require_gate_results(results, registry)
            refused = False
        except _StageError:
            refused = True
        check("accept-gate-contract", refused)
    _require_gate_results({"a": (True, "")}, ("a",))
    # Flip: the old shared format and count-only loss check fail the frozen-loss negatives; selecting the
    # validated bytes by the loss entry's own digest admits the cross-source substitution.
    raw = b"- [ ] synthetic task\n"
    source = {"path": "a", "raw": raw, "sha256": _sha256_hex(raw), "size": len(raw)}
    result = importers.run_importer("github-tasklist", source)
    check("accept-loss-fixture", result.verdict == CLEAN)
    row = {"scope": "declared", "source_path": "a", "resolved_source_path": "a",
           "importer_kind": "github-tasklist", "candidate_count": len(result.candidates),
           "proposal_count": len(result.proposals), "loss": result.lossy}
    docs = {"run.toml": {"source": [{"path": "a", "sha256": source["sha256"], "size": len(raw)}]},
            CANDIDATES_DRAFT_NAME: {"candidate": [dict(c, source_path="a") for c in result.candidates]},
            PROPOSALS_NAME: {"proposal": [dict(p, origin=_IMPORTER_PROPOSAL_ORIGIN) for p in result.proposals]}}
    # A second preserved source whose own importer output is self-consistent under the same path "a".
    other_raw = b"- [ ] a different, longer synthetic task\n"
    other = importers.run_importer("github-tasklist", dict(
        source, raw=other_raw, sha256=_sha256_hex(other_raw), size=len(other_raw)))
    check("accept-loss-other-fixture", other.verdict == CLEAN)
    staged = {"sources/" + source["sha256"]: raw, "sources/" + _sha256_hex(other_raw): other_raw}
    class Reader:
        def read_bytes(self, name):
            if name not in staged:
                raise _cannot("unexpected staged read: " + name)
            return staged[name]
    _validate_frozen_losses(Reader(), {"migrate": [row]}, docs)
    for mutation in ("shrink-proposal", "loss-gap", "loss-overlap", "missing-loss", "extra-loss",
                     "cross-source"):
        m, ds = copy.deepcopy(row), copy.deepcopy(docs)
        if mutation == "shrink-proposal":
            ds[PROPOSALS_NAME]["proposal"][0]["span"][1] -= 1
        elif mutation == "loss-gap":
            m["loss"]["span"][0]["range"][0] = 1
        elif mutation == "loss-overlap":
            m["loss"]["span"].append(copy.deepcopy(m["loss"]["span"][0]))
        elif mutation == "missing-loss":
            m.pop("loss")
        elif mutation == "cross-source":
            m["loss"] = copy.deepcopy(other.lossy)
            ds[CANDIDATES_DRAFT_NAME]["candidate"] = [dict(c, source_path="a") for c in other.candidates]
            ds[PROPOSALS_NAME]["proposal"] = [dict(p, origin=_IMPORTER_PROPOSAL_ORIGIN) for p in other.proposals]
        else:
            m["loss"]["extra"] = {}
        try:
            _validate_frozen_losses(Reader(), {"migrate": [m]}, ds)
            refused = False
        except (KeyError, _StageError):
            refused = True
        check("accept-loss-" + mutation, refused)
    zero = dict(row, candidate_count=0, proposal_count=0)
    cw = [{"scope": "declared", "source_path": "a", "resolved_source_path": "a", "disposition": "migrate"}]
    rendered = _ingest_render_model(rid, cw, [zero])
    check("accept-zero-explicit-conversion", len(rendered["decision_units"]) == 2)
    check("accept-home-constructor", _ingest_acceptance_home(rid) == _opf_store.evidence_run("import", rid))
    # Flip: a manifest-only activation test (ignoring SUPPORTED_HOMES) admits the first row, a declaration
    # the store's derived generation grades as 1 while this tooling supports only homes 1.
    homes2 = dict(homes=2, spec_version=_opf_store.HOMES2_SPEC_VERSION)
    for base, supported, allowed in ((homes2, 1, False), (homes2, 2, True),
                                     (dict(homes=True, spec_version="2.0.0"), 2, False),
                                     (dict(homes=1, spec_version="1.1.0"), 2, False), (dict(), 2, False)):
        manifest = _opf_store.ManifestValidation(_opf_store.VALID, base=base)
        with patch.object(_opf_store, "load_manifest", return_value=manifest), \
                patch.object(_opf_store, "SUPPORTED_HOMES", supported):
            try:
                admitted = _require_ingest_homes(_store_homes(None)) == 2
            except _StageError:
                admitted = False
        check("accept-homes-generation", admitted is allowed)
    # Flip: the legacy location plus homes-2 staging names for generation 1 lets the gate treat an ordinary
    # `.working/staging/...` ancestor as the store root.
    check("accept-run-location-legacy-only", _import_run_locations(rid, 1) == (IMPORTS_REL + "/" + rid,))
    check("accept-run-location-homes2", _import_run_locations(rid, 2)[1:] == (
        _opf_store.stage_run("ingest", rid), _opf_store.stage_run("import", rid)))

    changed = copy.deepcopy(envelope["ingest"])
    changed["binding"]["homes_generation"] = 3
    check("accept-diff-aid", _ingest_review_changes(acceptance["ingest"], changed)["diff"] != [])
    # accept-explanation-invalid runs on the activated homes-2 fixture (_self_test_ingest_capture_homes2).
    resolution = _opf_store.Resolution(_opf_store.RESOLVED, store_root="/synthetic", product_root="/synthetic")
    # The gate imports `_opf_import`; under a direct-script run this module is `__main__`, a distinct module
    # object. Flip: patching sys.modules[__name__] here leaves the gate's reader unmocked on descriptor 900,
    # so the direct-script run fails while the imported run passes.
    import _opf_import as consumed

    class DurableReader:
        path = Path(rid)
    with patch.object(gate, "_ingest_store_fd", return_value=900), \
            patch.object(consumed, "_read_ingest_acceptance", return_value=acceptance), \
            patch.object(consumed, "_ingest_snapshot", return_value=snapshot), \
            patch.object(_opf_store, "SUPPORTED_HOMES", 2), patch.object(os, "close"):
        checks = gate._ingest_acceptance_checks(DurableReader(), 2)
        check("accept-durable-gate", all(ok for ok, detail in checks.values())
              and checks["ingest-acceptance-binding"][1] == "review recorded; execution unavailable")
        with patch.object(gate, "_INGEST_EVIDENCE_REGISTRY", ()):
            checks = gate._ingest_acceptance_checks(DurableReader(), 2)
            check("accept-durable-registry", checks["ingest-acceptance-binding"][0] is False)
    # Flip: dropping the generation guard probes the store from generation 1 (this locator raises).
    with patch.object(gate, "_ingest_store_fd", side_effect=AssertionError("generation 1 probed")):
        checks = gate._ingest_acceptance_checks(DurableReader(), 1)
        check("accept-durable-legacy-not-probed", all(ok and detail.startswith("not applicable")
                                                      for ok, detail in checks.values()))
        # Flip: validating the registry before the generation early exit fails generation 1 on a registry
        # it never consults.
        with patch.object(gate, "_INGEST_EVIDENCE_REGISTRY", ()):
            checks = gate._ingest_acceptance_checks(DurableReader(), 1)
        check("accept-durable-registry-legacy", all(ok and detail.startswith("not applicable")
                                                    for ok, detail in checks.values()))
    # Flip: dropping the None refusal in _ingest_acceptance_checks reads from no descriptor and loses the
    # located refusal: a detached ingest run still refuses in homes 2.
    with patch.object(gate, "_ingest_store_fd", return_value=None), patch.object(_opf_store, "SUPPORTED_HOMES", 2):
        checks = gate._ingest_acceptance_checks(DurableReader(), 2)
        check("accept-durable-detached-refused", not any(ok for ok, detail in checks.values())
              and "detached run" in checks["ingest-acceptance-binding"][1])
    with patch.object(_journal, "_lstat_contained", return_value=object()), \
            patch.object(_journal, "_open_dir_contained", return_value=900), \
            patch.object(os, "listdir", return_value=["acceptance.json.tmp-interrupted"]), \
            patch.object(os, "close"):
        try:
            _read_ingest_acceptance(901, rid)
            refused = False
        except _StageError:
            refused = True
        check("accept-durable-temp-refused", refused)
    run_rel = IMPORTS_REL + "/" + rid
    # Flip: probing the durable home in generation 1 routes an ordinary run to ingest capture (the None leg).
    # Flip: reading a non-regular staged acceptance (the S_ISREG guard dropped) marks the directory leg, and
    # propagating the contained-read JournalError (the unreadable leg) aborts where main's gate reports it.
    regular = os.stat_result((stat.S_IFREG | 0o644,) + (0,) * 9)
    directory = os.stat_result((stat.S_IFDIR,) + (0,) * 9)
    unreadable = _journal.JournalError("injected unreadable acceptance")
    for name, homes, expected, st, read in (
            (_ingest_acceptance_home(rid), 2, "durable import evidence", regular, None),
            (_ingest_acceptance_home(rid), 1, None, regular, None),
            (run_rel + "/" + ACCEPTANCE_NAME, 1, None, regular, None),
            (run_rel + "/" + ACCEPTANCE_NAME, 2, ACCEPTANCE_NAME, regular, None),
            (run_rel + "/" + ACCEPTANCE_NAME, 2, None, directory, None),
            (run_rel + "/" + ACCEPTANCE_NAME, 2, None, regular, unreadable)):
        with patch.object(_journal, "_lstat_contained",
                          side_effect=lambda fd, path: st if path == name else None), \
                patch.object(_journal, "_read_contained", return_value=(_emit_acceptance_bytes(acceptance), None),
                             side_effect=read):
            try:
                found = _ingest_run_marker(900, run_rel, homes)
            except _journal.JournalError:
                found = "raised"
            check("accept-only-marker", found == expected)
    # Flip: the strict decoder rejects duplicate members and misses this ingest marker.
    check("accept-marker-main-decoder", _acceptance_marks_ingest(
        b'{"ingest": null, "ingest": {}}'))
    # Intake and clock tests use explicit in-memory boundaries. They are not filesystem-writer evidence.
    class TTY(io.StringIO):
        def isatty(self):
            return True
    resolution.pointer_source = "default"
    envelope = _ingest_review_envelope(snapshot)
    with patch.object(gate, "_RunDir") as reader, \
            patch.object(sys.modules[__name__], "_store_homes", return_value=1), \
            patch.object(sys.modules[__name__], "_require_review_gate"), \
            patch.object(sys.modules[__name__], "_ingest_snapshot", return_value=snapshot), \
            patch.object(sys.modules[__name__], "review_import") as submit:
        submit.return_value = ReviewResult(CLEAN)
        _interactive_ingest_review(resolution, rid, "reviewer", None, TTY("accept\n\n"), io.StringIO(), None)
        sent = submit.call_args.kwargs
        check("accept-intake-parity", sent["ingest"]["binding"] == envelope["ingest"]["binding"]
              and sent["ingest"]["units"] == [decision])
        for text in ("", "wrong\n", "accept\n"):
            try:
                _interactive_ingest_review(resolution, rid, "reviewer", None, TTY(text), io.StringIO(), None)
                refused = False
            except _StageError:
                refused = True
            check("accept-intake-eof-invalid", refused)


    stamp = datetime.datetime(2026, 9, 9, 12, 1, tzinfo=datetime.timezone.utc)
    installed = []
    def writer(resolution, home, data):
        installed.append((home, _strict_json(data)))
        return home + "/" + ACCEPTANCE_NAME
    with patch.object(gate, "_RunDir"), \
            patch.object(sys.modules[__name__], "_store_homes", return_value=1), \
            patch.object(sys.modules[__name__], "_require_review_gate"), \
            patch.object(sys.modules[__name__], "_ingest_snapshot", return_value=snapshot), \
            patch.object(sys.modules[__name__], "_require_ingest_homes"), \
            patch.object(sys.modules[__name__], "_gather_review_context",
                         return_value=acceptance["actor"]["context"]), \
            patch.object(sys.modules[__name__], "_read_ingest_acceptance", return_value=None), \
            patch.object(sys.modules[__name__], "_stage_acceptance", side_effect=writer), \
            patch.object(_opf_store, "_open_store_root_fd", return_value=900), \
            patch.object(_journal, "_open_dir_contained", return_value=901), patch.object(os, "close"):
        captured = _capture_ingest_review(resolution, rid, "reviewer", [], acceptance["ingest"], lambda: stamp)
        check("accept-clock-composition", captured.verdict == CLEAN
              and installed[0][1]["reviewed_at"] == _rfc3339(stamp)
              and installed[0][0] == _opf_store.evidence_run("import", rid))
        current = copy.deepcopy(snapshot)
        current["binding"]["homes_generation"] = 3
        with patch.object(sys.modules[__name__], "_ingest_snapshot", side_effect=[snapshot, current]):
            try:
                _capture_ingest_review(resolution, rid, "reviewer", [], acceptance["ingest"], lambda: stamp)
                refused = False
            except _StageError as exc:
                refused = exc.verdict == FINDING
            check("accept-final-compare", refused and len(installed) == 1)


def _self_test_ingest_capture_run(root, run, now, check):
    """Exercise durable capture on planner fixtures; the fixture provisions the inactive home."""
    import copy
    import io
    from unittest.mock import patch
    import check_opf_import as gate

    rd = gate._RunDir(run)
    try:
        # The planner fixture is a legacy store: its derived generation is 1.
        snapshot = _ingest_snapshot(rd, 1)
        # Flip: binding the constant INGEST_HOMES_GENERATION instead of the derived value records 2 here.
        check("accept-binding-derived-homes", snapshot["binding"]["homes_generation"] == 1)
        with patch.object(_opf_store, "SUPPORTED_HOMES", 2):
            check("accept-binding-homes2", _ingest_snapshot(rd, 2)["binding"]["homes_generation"] == 2)
    finally:
        rd.close()
    envelope = _ingest_review_envelope(snapshot)
    decisions = [dict(d, decision="accept", note="") for d in envelope["decisions"]]
    ingest = dict(envelope["ingest"], units=[dict(d, decision="accept", note="") for d in snapshot["units"]])
    stamp = now + datetime.timedelta(seconds=30)

    def capture(block=ingest, actor="reviewer"):
        return review_import(root, run.name, actor=actor, decisions=decisions,
                             ingest=block, now=now, clock=lambda: stamp)

    # Legacy-refusal control (generation 1, the shipped SUPPORTED_HOMES): capture never writes, and the
    # durable home is ordinary content that neither the gate nor capture reads.
    home = Path(root) / _ingest_acceptance_home(run.name)
    home.mkdir(parents=True)
    # Flip: dropping the activation call lets capture write into this operator-created home on the legacy
    # fixture manifest; the homes-2 capture below, over the same home, is the positive control.
    check("accept-home-not-activated", capture().verdict == CANNOT_EVALUATE
          and not (home / ACCEPTANCE_NAME).exists())
    # Flip: grading `.working/imported` in generation 1 (dropping both the gate's generation-1 routing and
    # the guard in _ingest_acceptance_checks) reads this ordinary JSON-null file as a malformed record.
    (home / ACCEPTANCE_NAME).write_bytes(b"null\n")
    legacy = gate.check_staged_run(run)
    check("accept-legacy-home-not-probed", all(ok for ok, detail in legacy.values())
          and legacy["ingest-acceptance-binding"][1].startswith("not applicable"))
    # Flip: the explanation's or the aid's generation guard dropped reads this ordinary file as a record.
    check("accept-explanation-legacy-not-probed", _ingest_acceptance_explanation(
        _resolve_store_for_review(root), run.name) == "No review has been recorded.")
    check("accept-aid-legacy-not-probed", ingest_review_aid(root, run.name, previous_run=run.name)["comparison"]
          == {"status": "cannot-evaluate", "detail": "homes generation 1 has no durable acceptance home"})
    (home / ACCEPTANCE_NAME).unlink()
    home.rmdir()
    with _self_test_homes2_active(root):
        _self_test_ingest_capture_homes2(root, run, now, check, stamp)


def _self_test_homes2_active(root):
    """Activate homes 2 over a fixture store the way apply's A11 test does: declare `homes = 2` in the
    manifest and patch SUPPORTED_HOMES, HOMES2_SPEC_VERSION and validate_manifest (homes is not yet a
    manifest schema key). The manifest bytes are restored on exit."""
    import contextlib
    import tomllib
    from unittest.mock import patch
    resolution = _opf_store.resolve_store(root)
    path = Path(resolution.store_root) / resolution.machine_rel / _opf_store.MANIFEST_NAME
    original = path.read_bytes()
    model = tomllib.loads(original.decode("utf-8"))
    model["opf"]["homes"] = 2
    real_validate = _opf_store.validate_manifest

    def validate_declared(data, *args, **kwargs):
        # Validate the rest as shipped, then keep the declaration in the validated base: _store_homes
        # derives the generation from that base.
        opf = data.get("opf") if isinstance(data, dict) else None
        if not (isinstance(opf, dict) and "homes" in opf):
            return real_validate(data, *args, **kwargs)
        result = real_validate(dict(data, opf=dict((k, v) for k, v in opf.items() if k != "homes")),
                               *args, **kwargs)
        if isinstance(result.base, dict):
            result.base = dict(result.base, homes=opf["homes"])
        return result

    @contextlib.contextmanager
    def active():
        path.write_text(_opf_emit.emit_checked(model), encoding="utf-8")
        try:
            with patch.object(_opf_store, "SUPPORTED_HOMES", 2), \
                    patch.object(_opf_store, "HOMES2_SPEC_VERSION", _opf_store.SUPPORTED_SPEC_VERSION), \
                    patch.object(_opf_store, "validate_manifest", validate_declared):
                yield
        finally:
            path.write_bytes(original)
    return active()


def _self_test_ingest_capture_homes2(root, run, now, check, stamp):
    """Positive capture control on an activated homes-2 store (no activation patch on capture)."""
    import copy
    import io
    from unittest.mock import patch
    import check_opf_import as gate

    rd = gate._RunDir(run)
    try:
        snapshot = _ingest_snapshot(rd, 2)
    finally:
        rd.close()
    envelope = _ingest_review_envelope(snapshot)
    decisions = [dict(d, decision="accept", note="") for d in envelope["decisions"]]
    ingest = dict(envelope["ingest"], units=[dict(d, decision="accept", note="") for d in snapshot["units"]])

    def capture(block=ingest, actor="reviewer"):
        return review_import(root, run.name, actor=actor, decisions=decisions,
                             ingest=block, now=now, clock=lambda: stamp)

    check("accept-homes2-derived", _store_homes(_resolve_store_for_review(root)) == 2)
    # Flip: creating homes inside review would turn this refusal into a write.
    check("accept-home-not-provisioned", capture().verdict == CANNOT_EVALUATE)
    home = Path(root) / _ingest_acceptance_home(run.name)
    home.mkdir(parents=True)
    before = {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
    result = capture()
    check("accept-ingest-positive", result.verdict == CLEAN)
    acc_path = home / ACCEPTANCE_NAME
    if result.verdict != CLEAN:
        return
    saved = acc_path.read_bytes()
    acc = _strict_json(saved)
    check("accept-clock-final", acc["reviewed_at"] == _rfc3339(stamp))
    check("accept-record-homes2", acc["ingest"]["binding"]["homes_generation"] == 2)
    after = {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
    after.pop(str(acc_path.relative_to(root)))
    check("accept-only-durable-write", before == after and not (run / ACCEPTANCE_NAME).exists())
    # Flip: the gate call `_ingest_acceptance_checks(rd)` (homes dropped) reaches _gate_homes unsupplied
    # while SUPPORTED_HOMES is 2 and fails all six acceptance checks; a generation guard that skipped
    # generation 2 would report "not applicable" instead of the evaluated durable record.
    full = gate.check_staged_run(run, homes=2)
    check("accept-full-gate-homes2", set(full) == set(gate.EXPECTED_CHECKS)
          and len(full) == len(gate.EXPECTED_CHECKS) and all(full[cid][0] for cid in gate.EXPECTED_CHECKS)
          and full["ingest-acceptance-binding"][1] == "review recorded; execution unavailable")
    # Flip: an unsupplied generation is legacy only while no later generation can be active.
    check("accept-gate-homes-unsupplied", gate.check_staged_run(run)["ingest-acceptance-binding"][0] is False)
    # Flip: an entry point that derives no generation (or drops it before _require_review_gate or
    # _ingest_snapshot) reaches the gate unsupplied and refuses here.
    try:
        aid = ingest_review_aid(root, run.name)
        aid_ok = aid["template"]["ingest"]["binding"]["homes_generation"] == 2
    except _StageError:
        aid_ok = False
    check("accept-aid-homes-threaded", aid_ok)
    check("accept-explanation-homes-threaded", _ingest_acceptance_explanation(
        _resolve_store_for_review(root), run.name).startswith("Recorded by"))
    # Flip: dropping _require_review_gate from the explanation attributes this valid record after the injected
    # gate failure; the call above is the positive control.
    with patch.object(sys.modules[__name__], "_require_review_gate", side_effect=_cannot("injected gate failure")):
        explained = _ingest_acceptance_explanation(_resolve_store_for_review(root), run.name)
    check("accept-explanation-invalid", explained.startswith("Review could not be validated")
          and "injected gate failure" in explained)
    # Flip: a review or apply marker probe given generation 1 instead of the derived 2 misses this durable
    # record once the staged markers are gone, and routes the run down the ordinary path.
    stripped = {name: (run / name).read_bytes() for name in _INGEST_RUN_MARKERS if (run / name).is_file()}
    for name in stripped:
        (run / name).unlink()
    try:
        reviewed = capture()
        check("accept-review-marker-homes-threaded", reviewed.verdict == CANNOT_EVALUATE and bool(stripped)
              and reviewed.findings[0].startswith("import gate failed") and acc_path.read_bytes() == saved)
        class TTY(io.StringIO):
            def isatty(self):
                return True
        prompts = io.StringIO()
        markers = []
        real_marker = _ingest_run_marker

        def probe(*args):
            marker = real_marker(*args)
            markers.append(marker)
            return marker

        with patch.object(sys.modules[__name__], "_ingest_run_marker", side_effect=probe):
            interactive = review_import_interactive(root, run.name, actor="reviewer", now=now,
                                                    in_stream=TTY("accept\n\n"), out_stream=prompts)
        check("accept-review-marker-homes-threaded-interactive", interactive.verdict == CANNOT_EVALUATE
              and markers == ["durable import evidence"] and prompts.getvalue() == ""
              and interactive.findings[0].startswith("import gate failed") and acc_path.read_bytes() == saved)
        applied = apply_import(root, run.name, now=now)
        check("accept-apply-marker-homes-threaded", applied.verdict == CANNOT_EVALUATE
              and "carries durable import evidence" in applied.findings[0])
    finally:
        for name, data in stripped.items():
            (run / name).write_bytes(data)
    # Flip: capture without the pre-write re-derivation writes a homes-2 record after the manifest changed.
    derived = iter([2, 2])
    with patch.object(sys.modules[__name__], "_store_homes", side_effect=lambda _r: next(derived, 1)):
        changed = capture(actor="second")
    check("accept-capture-homes-rederived", changed.verdict == CANNOT_EVALUATE
          and "changed during capture" in changed.findings[0] and acc_path.read_bytes() == saved)
    # Flip: stale binding admission, gate omission, and a pre-rename overwrite each violate byte preservation.
    stale = copy.deepcopy(ingest)
    stale["binding"]["homes_generation"] = 1   # the activated fixture's derived generation is 2
    check("accept-stale-submission", capture(stale).verdict == FINDING and acc_path.read_bytes() == saved)
    with patch.object(os, "rename", side_effect=OSError("injected pre-rename failure")):
        check("accept-writer-pre-rename", capture(actor="second").verdict == CANNOT_EVALUATE
              and acc_path.read_bytes() == saved)
    # Flip: accepting a coherent model changed during the last gate silently rebinds human decisions.
    current = copy.deepcopy(snapshot)
    current["binding"]["homes_generation"] = 3
    with patch.object(sys.modules[__name__], "_require_review_gate"), \
            patch.object(sys.modules[__name__], "_ingest_snapshot", side_effect=[snapshot, current]):
        check("accept-prompt-mutation", capture().verdict == FINDING and acc_path.read_bytes() == saved)
    for payload in (None, acc, dict(acc, format="unknown")):
        if payload is None:
            acc_path.unlink()
        else:
            acc_path.write_bytes(_emit_acceptance_bytes(payload))
        before_apply = {str(p): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
        applied = apply_import(root, run.name, now=now)
        check("accept-apply-routing", applied.verdict == CANNOT_EVALUATE and applied.promoted is False
              and before_apply == {str(p): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()})
    acc_path.write_bytes(saved)
    # Flip: without the TTY guard this piped stream carries a complete review and is captured; the TTY run
    # over the same answers is the positive control.
    class TTY(io.StringIO):
        def isatty(self):
            return True
    answers = "accept\n\n" * (len(envelope["decisions"]) + len(envelope["ingest"]["units"]))
    piped = review_import_interactive(root, run.name, actor="second", now=now,
                                      in_stream=io.StringIO(answers), out_stream=io.StringIO(),
                                      clock=lambda: stamp)
    check("accept-intake-non-tty", piped.verdict == CANNOT_EVALUATE and acc_path.read_bytes() == saved)
    attended = review_import_interactive(root, run.name, actor="reviewer", now=now,
                                         in_stream=TTY(answers), out_stream=io.StringIO(),
                                         clock=lambda: stamp)
    check("accept-intake-tty-control", attended.verdict == CLEAN)
    acc_path.write_bytes(saved)
    reject = copy.deepcopy(ingest)
    reject["units"][0]["decision"] = "reject"
    check("accept-reject-captured", capture(reject).verdict == CLEAN)
    # A recorded rejection is valid evidence (the gate grades well-formedness, as for ordinary runs); it is
    # never promotable because apply refuses every ingest run before its gate.
    # Flip: failing the acceptance checks on `rejected` fails this gate and loses the explained rejection.
    rejected_gate = gate.check_staged_run(run, homes=2)
    check("accept-reject-gate-valid", all(ok for ok, detail in rejected_gate.values())
          and rejected_gate["ingest-acceptance-binding"][1] == "recorded rejection; not promotable")
    check("accept-reject-explained", _ingest_acceptance_explanation(
        _resolve_store_for_review(root), run.name).endswith("rejection=True."))
    before_apply = {str(p): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
    applied = apply_import(root, run.name, now=now)
    check("accept-reject-not-promoted", applied.verdict == CANNOT_EVALUATE and applied.promoted is False
          and before_apply == {str(p): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()})
    check("accept-reject-needs-fresh-plan", capture().verdict == CANNOT_EVALUATE)
    rejected_bytes = acc_path.read_bytes()
    # Flip: an aggregate "some reject remains" guard admits both captures and replaces the attributed reject.
    check("accept-reject-not-replaced", capture(reject, actor="second").verdict == CANNOT_EVALUATE
          and acc_path.read_bytes() == rejected_bytes)
    if len(ingest["units"]) > 1:
        swapped = copy.deepcopy(ingest)
        swapped["units"][1]["decision"] = "reject"
        check("accept-reject-not-swapped", capture(swapped).verdict == CANNOT_EVALUATE
              and acc_path.read_bytes() == rejected_bytes)
    # A stale existing record is never repaired in place, even with otherwise complete new decisions.
    corrupt = _strict_json(acc_path.read_bytes())
    corrupt["ingest"]["binding"]["report_digest"] = "sha256:" + "f" * 64
    acc_path.write_bytes(_emit_acceptance_bytes(corrupt))
    prior = acc_path.read_bytes()
    check("accept-repair-fresh-run", capture().verdict == CANNOT_EVALUATE and acc_path.read_bytes() == prior)
    # Flip: decoding JSON null to None reads this present record as "not yet reviewed", so the gate passes it
    # and capture replaces it.
    acc_path.write_bytes(b"null\n")
    check("accept-null-record-gate",
          gate.check_staged_run(run, homes=2).get("ingest-acceptance-binding", (True, ""))[0] is False)
    # Flip: an unvalidated generation (3, "2", True, 1.0 each unequal to 2 or equal to 1) grades this ingest
    # run as legacy and passes the malformed durable record "not applicable".
    for bad in (3, "2", True, 1.0):
        graded = gate.check_staged_run(run, homes=bad)
        check("accept-gate-homes-invalid", not any(graded[cid][0] for cid in (
            "acceptance-schema", "ingest-acceptance-binding", "ingest-acceptance-completeness")))
    check("accept-null-record-kept", capture().verdict == CANNOT_EVALUATE
          and acc_path.read_bytes() == b"null\n")


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
    _self_test_ingest_acceptance(check)

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

    def build_apply_store(src=b"legacy source body here"):
        """A DOCTOR-COMPOSABLE store that accepts quarantine imports, for the apply-promotion behaviour
        checks: the init-canonical baseline (all baseline types + ledgers + empty indexes), PLUS an LF counter
        and an empty legacy_fragment index, PLUS the rendered views. Per the DECOUPLED D6 design the manifest
        does NOT declare `legacy_fragment` (it is schema-deferred, kept out of manifest [types]); the store is
        doctor-composable because C-CONTAINMENT recognizes a present importer-type index without a declaration
        (mirroring C-COUNTERS' optional_namespaces) and C-RECORDS defers the LF schema. This is the real
        post-#263 init state, so the apply vectors exercise the true quarantine-promotion path rather than a
        fixture that masked the undeclared-LF containment finding (PRC-F1). The views are rendered through the
        U4 engine DIRECTLY (no git), so validate_store returns only the disclosed-benign git-observation
        cannot-evaluates and apply's D4 composition gate passes."""
        import _opf_init
        import _opf_views
        counter[0] += 1
        root = base / "apply-{:02d}".format(counter[0])
        mdir = root / ".working" / "toml"
        mdir.mkdir(parents=True)
        model = _opf_init._manifest_model()
        (mdir / "manifest.toml").write_text(_opf_emit.emit_checked(model), encoding="utf-8")
        nss = list(_opf_store.BASELINE_TYPES.values()) + ["LF"]
        (mdir / "counters.toml").write_text(
            _opf_emit.emit_checked({"schema": SCHEMA, "counters": {n: 0 for n in nss}}), encoding="utf-8")
        (mdir / "version.toml").write_text(_opf_init.build_version(), encoding="utf-8")
        (mdir / "worklog.toml").write_text(_opf_init.build_worklog(), encoding="utf-8")
        for t in _opf_init.INDEX_TYPES:
            (mdir / "{}.index.toml".format(t)).write_text(_opf_init.build_index(t), encoding="utf-8")
        (mdir / "legacy_fragment.index.toml").write_text(
            _opf_emit.emit_checked({"schema": SCHEMA, "record": []}), encoding="utf-8")
        (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (root / "a.txt").write_bytes(src if isinstance(src, bytes) else src.encode("utf-8"))
        res = _opf_store.resolve_store(root)
        _opf_views._render_resolved_store(root, res, False, capture={"preimages": {}})
        return root, mdir

    def review_accept_all(root, run_id, verb_for_first="accept"):
        """Load the staged run's ordered fragments and capture an accept-all acceptance (optionally rejecting
        the FIRST fragment, to exercise the apply reject-block path). Returns the ReviewResult."""
        res = _opf_store.resolve_store(root)
        fd = _opf_store._open_store_root_fd(res.store_root, res.pointer_source != "default")
        try:
            _pd, _iv, _fb, _km, ordered = _load_staged_run_for_review(
                fd, "{}/{}".format(IMPORTS_REL, run_id))
        finally:
            os.close(fd)
        decs = [{"fragment_id": o["fragment_id"],
                 "decision": verb_for_first if i == 0 else "accept",
                 "origin": o["origin"], "proposed_state": o["proposed_state"]}
                for i, o in enumerate(ordered)]
        return review_import(root, run_id, actor="apply-tester", decisions=decs, now=NOW)

    # R4-C2 (self-test hermeticity): neutralize an INHERITED journal crash-injection var for the whole
    # run. A promote fixture (D23 and the crash-recovery vectors) drives capture_preimages/publish, each
    # of which calls _journal._kill_point; an inherited AIQT_JOURNAL_KILL would os._exit the process mid
    # fixture setup. Save+clear it here and restore it in the finally below. A vector that sets the var
    # DELIBERATELY sets+unsets it locally within its own block; this only removes an inherited value.
    _saved_kill_env = os.environ.pop(_journal.KILL_ENV, None)
    try:
        _self_test_d5(check, build_apply_store, bi_candidate, review_accept_all, NOW)
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
        # usage error (exactly one mode required) -> exit 2 and stages nothing; and `--apply <run-id>` on an
        # UNREVIEWED run dispatches onto the now-real apply_import, which finds no acceptance.json and is not
        # promotion-ready -> exit 2 (cannot-evaluate), mutating nothing (the 3-apply vector below drives that
        # real apply path). Both cases exercise the live
        # dispatcher through opf.py (the CLI round-trip lives in opf.py's own opf-cli self-test leg).
        opf_py = str(Path(__file__).resolve().parent / "opf.py")
        root4, machine4 = build_store(sources={"a.txt": src})
        cp_nomode = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--root", str(root4)],
                                   capture_output=True)
        check("3-verb-no-mode-exits-2", cp_nomode.returncode == 2)
        check("3-verb-no-mode-stages-nothing", not (machine4.parent / "imports").exists())
        # Stage a real run over root4, then `--apply` it WITHOUT a review: apply_import (PR-C, now real) finds
        # no acceptance.json, so the run is not promotion-ready -> exit 2, and the store machine tree is
        # byte-unchanged (nothing promoted). This exercises the live CLI dispatch onto the REAL apply layer;
        # the full promoted/idempotent/reject behaviour is covered by the module A1-A6 checks above and the
        # opf-cli self-test's own promoted/no-op vectors. An INDEPENDENT run-id-grammar literal for the operand.
        plan4 = plan_import(root4, ["a.txt"], now=NOW, run_nonce="verb-apply-pin")
        check("3-apply-plan-staged", plan4.verdict == 0 and bool(plan4.run_id))
        machine4_before = snapshot(machine4)
        cp_apply = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--apply",
                                   plan4.run_id or "imp-00000000T000000Z-0000000000000000",
                                   "--root", str(root4)], capture_output=True)
        check("3-apply-unreviewed-exits-2", cp_apply.returncode == 2)
        check("3-apply-unreviewed-mutates-nothing", snapshot(machine4) == machine4_before)

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

        _self_test_pi(check, build_store, snapshot, NOW, NONCE)

        # P4 init-first + fail-closed: planning a location with no store is cannot-evaluate.
        noroot2 = base / "noopf2-{:02d}".format(counter[0] + 998)
        noroot2.mkdir(parents=True)
        (noroot2 / "a.txt").write_text("aaaa", encoding="utf-8")
        check("P4-plan-no-store-cannot-eval",
              plan_import(noroot2, ["a.txt"], now=NOW, run_nonce=NONCE).verdict == 2)

        # --- OPF-IMPORT-APPLY (PR-C): apply_import promotion behaviour, driven through apply_import over
        # doctor-composable synthetic stores (Group B). The deferred-stub A1 pin is CONSCIOUSLY converted to
        # these real-promotion checks (change-carries-check): the stub returned verdict 2 for every run, so a
        # promoted (verdict 0) / rejected (verdict 1) / noop outcome flips red against the pre-PR-C stub. ----
        # A1 (happy path): plan a quarantine import, review accept-all, promote. verdict 0, promoted, and the
        # ruled D1/D2/D3 + terminal-delete artefacts are all present; the store is left doctor-composable.
        rootA1, mA1 = build_apply_store()
        prA1 = plan_import(rootA1, ["a.txt"], now=NOW, run_nonce="apply-a1")
        check("A1-plan-clean", prA1.verdict == 0 and bool(prA1.run_id))
        rvA1 = review_accept_all(rootA1, prA1.run_id)
        check("A1-review-clean", rvA1.verdict == 0)
        import check_opf_import as _gate_spy
        from unittest.mock import patch as _patch
        with _patch.object(_gate_spy, "check_staged_run", wraps=_gate_spy.check_staged_run) as spy:
            apA1 = apply_import(rootA1, prA1.run_id, now=NOW)
        # Flip: the pre-promotion gate called without the derived generation records no `homes` here.
        check("A1-apply-gate-homes", [c.kwargs.get("homes") for c in spy.call_args_list] == [1])
        check("A1-apply-promoted",
              apA1.verdict == 0 and apA1.promoted is True and apA1.outcome == "promoted")
        runA1 = mA1.parent / "imports" / (prA1.run_id or "X")
        check("A1-run-dir-deleted", not runA1.exists())                                    # TERMINAL delete
        check("A1-journal-outside-working", (rootA1 / ".aiqt" / "import" / "journal").is_dir())  # D2
        txnA1 = rootA1 / ".aiqt" / "import" / (prA1.run_id or "X") / "transaction.toml"
        check("A1-txn-record-outside-working", txnA1.is_file())                            # D3
        if txnA1.is_file():
            txn = tomllib.loads(txnA1.read_text())
            check("A1-txn-state-complete",
                  txn.get("state") == "complete" and txn.get("run_id") == prA1.run_id)
        archA1 = rootA1 / ".aiqt" / "import-archive" / (prA1.run_id or "X")                 # D1
        check("A1-archive-acceptance-relocated", (archA1 / "acceptance.json").is_file())
        src_bodies = list((archA1 / "sources").iterdir()) if (archA1 / "sources").is_dir() else []
        check("A1-original-preserved",
              len(src_bodies) == 1 and src_bodies[0].read_bytes() == b"legacy source body here")
        lf_liveA1 = mA1 / "legacy_fragment.index.toml"
        lf_recsA1 = tomllib.loads(lf_liveA1.read_text()).get("record", []) if lf_liveA1.is_file() else []
        check("A1-lf-promoted",
              len(lf_recsA1) == 1 and lf_recsA1[0].get("body") == "legacy source body here")
        check("A1-counter-advanced",
              tomllib.loads((mA1 / "counters.toml").read_text())["counters"].get("LF") == 1)
        check("A1-status-complete",
              tomllib.loads((mA1 / "manifest.toml").read_text())["opf"]["import_status"] == "complete")

        # A2 (idempotency): re-apply a completed run -> noop_already_complete, exit 0, NO further mutation.
        a1_after = snapshot(mA1)
        apA2 = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2-idempotent-noop",
              apA2.verdict == 0 and apA2.outcome == "noop_already_complete" and apA2.promoted is True)
        check("A2-idempotent-no-mutation", snapshot(mA1) == a1_after)
        # A2b/A2c (PRC-F2): the retained transaction record is trusted as a no-op ONLY when it is shape-valid
        # AND binds THIS run. A bare {state=complete} (missing the format/run_id/digest bindings) and a
        # shape-valid record whose run_id names a DIFFERENT run each fail closed (verdict 2, not promoted),
        # never a false no-op. Without the _validate_transaction_record guard on the no-op path both returned
        # verdict 0 / promoted True.
        valid_txn_text = txnA1.read_text() if txnA1.is_file() else ""
        txnA1.write_text('state = "complete"\n', encoding="utf-8")
        apA2b = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2b-malformed-record-fail-closed", apA2b.verdict == 2 and apA2b.promoted is False)
        foreign_txn_text = valid_txn_text.replace(prA1.run_id, "apply-not-this-run") if prA1.run_id else ""
        txnA1.write_text(foreign_txn_text, encoding="utf-8")
        apA2c = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2c-foreign-run-fail-closed", apA2c.verdict == 2 and apA2c.promoted is False)
        txnA1.write_text(valid_txn_text, encoding="utf-8")   # restore the true record for any later reuse
        # A2d (PRC-F2): a shape-valid record with a MALFORMED allocation member (a namespace mapped to a
        # non-list) is not a verified no-op; the stricter _validate_transaction_record rejects it. Without the
        # member validation this returned a false promoted no-op.
        bad_txn = tomllib.loads(valid_txn_text)
        bad_txn["allocation"] = {"LF": 17}
        txnA1.write_text(_opf_emit.emit(bad_txn), encoding="utf-8")
        apA2d = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2d-malformed-allocation-fail-closed", apA2d.verdict != 0 and apA2d.promoted is False)
        txnA1.write_text(valid_txn_text, encoding="utf-8")
        # A2e (PRC-F2): a caller accepted_plan_digest that does NOT match the completed run's recorded plan
        # digest is a stale binding, refused on the no-op path too (the normal path checks it at load, but the
        # no-op returns before that load). Without the no-op digest check this returned a false promoted no-op.
        apA2e = apply_import(rootA1, prA1.run_id, accepted_plan_digest="sha256:" + "e" * 64, now=NOW)
        check("A2e-stale-caller-digest-fail-closed", apA2e.verdict != 0 and apA2e.promoted is False)
        # A2f (PRC-F2 r3): a BLANK allocation id member (empty OR whitespace-only) is not a valid id; the
        # `and _i.strip()` guard in _validate_transaction_record rejects it on the no-op path. Runs on rootA1
        # BEFORE A-f6 (which leaks a promotion lock into rootA1) so the guard is reached, not masked by a
        # stale-lock abort. Without .strip() a whitespace-only id rode through as a false promoted no-op
        # (round-4's bare `and _i` accepted "   "; A2d covers only the non-list shape).
        bad_txn_empty = tomllib.loads(valid_txn_text)
        bad_txn_empty["allocation"] = {"LF": ["   "]}
        txnA1.write_text(_opf_emit.emit(bad_txn_empty), encoding="utf-8")
        apA2f = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2f-blank-id-member-fail-closed", apA2f.verdict != 0 and apA2f.promoted is False)
        txnA1.write_text(valid_txn_text, encoding="utf-8")   # restore the true record
        # A2g (PRC-F2 r3): a BLANK allocation namespace name is rejected by the `_ns.strip()` guard (the real
        # producer emits only 2-letter namespaces, never ""). Without _ns.strip() an empty key rode through.
        bad_txn_ns = tomllib.loads(valid_txn_text)
        bad_txn_ns["allocation"] = {"": ["LF-0001"]}
        txnA1.write_text(_opf_emit.emit(bad_txn_ns), encoding="utf-8")
        apA2g = apply_import(rootA1, prA1.run_id, now=NOW)
        check("A2g-blank-namespace-fail-closed", apA2g.verdict != 0 and apA2g.promoted is False)
        txnA1.write_text(valid_txn_text, encoding="utf-8")   # restore the true record
        # A-f6 (PRC-F6): a release_lock failure (an OSError from its os.unlink) does NOT overturn an
        # already-committed result. Inject an OSError at release and re-apply the completed run (the no-op path
        # still runs the finally): the no-op result STANDS. Without the broadened (JournalError, OSError) catch
        # the OSError escaped the finally and the outer handler flipped the committed result to verdict 2.
        _orig_release_f6 = _journal.release_lock
        def _boom_release_f6(*_a, **_k):
            raise OSError("injected release failure")
        _journal.release_lock = _boom_release_f6
        try:
            apF6 = apply_import(rootA1, prA1.run_id, now=NOW)
        finally:
            _journal.release_lock = _orig_release_f6
        check("f6-release-oserror-does-not-overturn",
              apF6.verdict == 0 and apF6.outcome == "noop_already_complete" and apF6.promoted is True)

        # D23 (re-QA): a CLEAN no-op binds to the run's DURABLE evidence, not the projection record alone. On a
        # fresh promote the re-apply is a verified no-op; but if the run's durable journal txn dir is deleted, or
        # the archived acceptance payload is removed, the completed record can no longer certify an idempotent
        # no-op and apply fails closed (reconcile through recover), never a false promoted no-op. Without the
        # durable-evidence binding on the no-op path both broken stores returned verdict 0 / promoted True.
        rootD23j, mD23j = build_apply_store()
        prD23j = plan_import(rootD23j, ["a.txt"], now=NOW, run_nonce="apply-d23j")
        review_accept_all(rootD23j, prD23j.run_id)
        apD23j0 = apply_import(rootD23j, prD23j.run_id, now=NOW)          # real promote
        apD23j1 = apply_import(rootD23j, prD23j.run_id, now=NOW)          # baseline no-op is CLEAN
        check("D23-baseline-noop-clean",
              apD23j0.verdict == 0 and apD23j1.verdict == 0
              and apD23j1.outcome == "noop_already_complete" and apD23j1.promoted is True)
        txnD23j = tomllib.loads((rootD23j / ".aiqt" / "import" / (prD23j.run_id or "X")
                                 / "transaction.toml").read_text())
        jdirD23j = rootD23j / ".aiqt" / "import" / "journal" / txnD23j.get("txn_id", "X")
        shutil.rmtree(jdirD23j)                                           # durable journal for THIS run gone
        apD23j = apply_import(rootD23j, prD23j.run_id, now=NOW)
        check("D23-noop-requires-durable-journal",
              apD23j.verdict != 0 and apD23j.promoted is False)
        rootD23a, mD23a = build_apply_store()
        prD23a = plan_import(rootD23a, ["a.txt"], now=NOW, run_nonce="apply-d23a")
        review_accept_all(rootD23a, prD23a.run_id)
        apD23a0 = apply_import(rootD23a, prD23a.run_id, now=NOW)          # real promote
        apD23a1 = apply_import(rootD23a, prD23a.run_id, now=NOW)          # baseline no-op is CLEAN
        check("D23-archive-baseline-noop-clean",
              apD23a0.verdict == 0 and apD23a1.verdict == 0 and apD23a1.promoted is True)
        (rootD23a / ".aiqt" / "import-archive" / (prD23a.run_id or "X") / "acceptance.json").unlink()
        apD23a = apply_import(rootD23a, prD23a.run_id, now=NOW)
        check("D23-noop-requires-durable-archive",
              apD23a.verdict != 0 and apD23a.promoted is False)

        # R3-C1/R3-C2 (round-4): a CLEAN no-op binds the run's durable evidence by TYPE + CONTENT + IDENTITY,
        # not mere existence. Each case is a fresh real promote whose baseline re-apply is a verified no-op; a
        # targeted corruption of the IMMUTABLE archive (wrong type, symlink, corrupt bytes, missing/renamed
        # body) or a journal-identity mismatch then fails closed, never a false promoted no-op. Without the
        # round-4 checks each corrupted store returned verdict 0 / promoted True.
        def _fresh_promoted_d23(nonce):
            r, m = build_apply_store()
            pr = plan_import(r, ["a.txt"], now=NOW, run_nonce=nonce)
            review_accept_all(r, pr.run_id)
            ap0 = apply_import(r, pr.run_id, now=NOW)     # real promote
            ap1 = apply_import(r, pr.run_id, now=NOW)     # baseline no-op is CLEAN (intact evidence)
            return r, pr, ap0, ap1

        def _arch_d23(root, pr):
            return root / ".aiqt" / "import-archive" / (pr.run_id or "X")

        # R3-C1: archive acceptance.json replaced by a DIRECTORY (wrong type) -> fail-closed.
        rC1t, prC1t, ap0C1t, ap1C1t = _fresh_promoted_d23("apply-d23c1t")
        check("D23-c1-acc-type-baseline-clean",
              ap0C1t.verdict == 0 and ap1C1t.verdict == 0 and ap1C1t.promoted is True)
        accC1t = _arch_d23(rC1t, prC1t) / "acceptance.json"
        accC1t.unlink(); accC1t.mkdir()
        apC1t = apply_import(rC1t, prC1t.run_id, now=NOW)
        check("D23-noop-rejects-acceptance-wrong-type", apC1t.verdict != 0 and apC1t.promoted is False)

        # R3-C1: archive sources DIR replaced by a regular FILE (wrong type) -> fail-closed.
        rC1s, prC1s, _ap0C1s, ap1C1s = _fresh_promoted_d23("apply-d23c1s")
        check("D23-c1-sources-type-baseline-clean", ap1C1s.verdict == 0 and ap1C1s.promoted is True)
        srcsC1s = _arch_d23(rC1s, prC1s) / "sources"
        shutil.rmtree(srcsC1s); srcsC1s.write_bytes(b"not a directory")
        apC1s = apply_import(rC1s, prC1s.run_id, now=NOW)
        check("D23-noop-rejects-sources-wrong-type", apC1s.verdict != 0 and apC1s.promoted is False)

        # R3-C1: archive acceptance.json replaced by a dangling SYMLINK -> fail-closed (no-follow).
        rC1l, prC1l, _ap0C1l, ap1C1l = _fresh_promoted_d23("apply-d23c1l")
        check("D23-c1-acc-symlink-baseline-clean", ap1C1l.verdict == 0 and ap1C1l.promoted is True)
        accC1l = _arch_d23(rC1l, prC1l) / "acceptance.json"
        accC1l.unlink(); accC1l.symlink_to("does-not-exist")
        apC1l = apply_import(rC1l, prC1l.run_id, now=NOW)
        check("D23-noop-rejects-acceptance-symlink", apC1l.verdict != 0 and apC1l.promoted is False)

        # R3-C1: archive acceptance.json bytes corrupted (sha mismatch vs the recorded acceptance_sha256).
        rC1c, prC1c, _ap0C1c, ap1C1c = _fresh_promoted_d23("apply-d23c1c")
        check("D23-c1-acc-corrupt-baseline-clean", ap1C1c.verdict == 0 and ap1C1c.promoted is True)
        (_arch_d23(rC1c, prC1c) / "acceptance.json").write_text("corrupted-acceptance", encoding="utf-8")
        apC1c = apply_import(rC1c, prC1c.run_id, now=NOW)
        check("D23-noop-rejects-corrupt-acceptance", apC1c.verdict != 0 and apC1c.promoted is False)

        # R3-C1: a recorded source body deleted (dir kept) -> the archived set no longer equals the record.
        rC1m, prC1m, _ap0C1m, ap1C1m = _fresh_promoted_d23("apply-d23c1m")
        check("D23-c1-missing-body-baseline-clean", ap1C1m.verdict == 0 and ap1C1m.promoted is True)
        for _b in (_arch_d23(rC1m, prC1m) / "sources").iterdir():
            _b.unlink()
        apC1m = apply_import(rC1m, prC1m.run_id, now=NOW)
        check("D23-noop-rejects-missing-source-body", apC1m.verdict != 0 and apC1m.promoted is False)

        # R3-C1: a source body overwritten with bytes that do NOT match its content-address filename.
        rC1b, prC1b, _ap0C1b, ap1C1b = _fresh_promoted_d23("apply-d23c1b")
        check("D23-c1-corrupt-body-baseline-clean", ap1C1b.verdict == 0 and ap1C1b.promoted is True)
        for _b in (_arch_d23(rC1b, prC1b) / "sources").iterdir():
            _b.write_bytes(b"tampered body bytes")
        apC1b = apply_import(rC1b, prC1b.run_id, now=NOW)
        check("D23-noop-rejects-corrupt-source-body", apC1b.verdict != 0 and apC1b.promoted is False)

        # R3-C2: a completed record for run A whose txn_id names run B's COMPLETE journal. Build two real
        # promotes A and B, copy B's terminal journal into A's store, and point A's record txn_id at it. A's
        # own archive is left INTACT, so ONLY the journal-identity binding (INTENT header unit != A) rejects
        # it; without the binding the projection read as a false promoted no-op.
        rFa, prFa, _ap0Fa, ap1Fa = _fresh_promoted_d23("apply-d23f-a")
        rFb, prFb, _ap0Fb, ap1Fb = _fresh_promoted_d23("apply-d23f-b")
        check("D23-foreign-baseline-clean",
              ap1Fa.verdict == 0 and ap1Fa.promoted is True and ap1Fb.verdict == 0 and ap1Fb.promoted is True)
        txnFa_path = rFa / ".aiqt" / "import" / (prFa.run_id or "X") / "transaction.toml"
        recFa = tomllib.loads(txnFa_path.read_text())
        recFb = tomllib.loads((rFb / ".aiqt" / "import" / (prFb.run_id or "X")
                               / "transaction.toml").read_text())
        txnB_id = recFb.get("txn_id")
        shutil.copytree(rFb / ".aiqt" / "import" / "journal" / txnB_id,
                        rFa / ".aiqt" / "import" / "journal" / txnB_id)   # B's terminal journal into A's store
        recFa["txn_id"] = txnB_id                                        # A's record now names B's journal
        txnFa_path.write_text(_opf_emit.emit(recFa), encoding="utf-8")
        apFor = apply_import(rFa, prFa.run_id, now=NOW)
        check("D23-noop-rejects-foreign-journal", apFor.verdict != 0 and apFor.promoted is False)

        # R4-C1 (round-5): with the journal INTACT and the record + archive kept SELF-CONSISTENT, a tamper
        # of the record's own fields is caught ONLY by binding the WHOLE record to the immutable journal.
        # Each case below edits the LIVE transaction.toml AND (where relevant) the archive together so they
        # agree, so R3-C1's archive validation passes and ONLY R4-C1 (live record bytes != journal-recorded
        # content hash) rejects it. Without R4-C1 each returned a false promoted no-op (verdict 0).
        def _live_txn_path_d23(root, pr):
            return root / ".aiqt" / "import" / (pr.run_id or "X") / "transaction.toml"

        # tampered archived_sources: drop one preserved body AND remove it from the record's set, so record
        # and archive still AGREE; only the record-to-journal binding sees the change.
        rT1, prT1, _ap0T1, ap1T1 = _fresh_promoted_d23("apply-d23r4-src")
        check("D23-r4-src-baseline-clean", ap1T1.verdict == 0 and ap1T1.promoted is True)
        _srcs_dir_T1 = _arch_d23(rT1, prT1) / "sources"
        _dropped_T1 = sorted(p.name for p in _srcs_dir_T1.iterdir())[0]
        (_srcs_dir_T1 / _dropped_T1).unlink()
        _recT1 = tomllib.loads(_live_txn_path_d23(rT1, prT1).read_text())
        _recT1["archived_sources"] = [s for s in _recT1.get("archived_sources", []) if s != _dropped_T1]
        _live_txn_path_d23(rT1, prT1).write_text(_opf_emit.emit(_recT1), encoding="utf-8")
        apT1 = apply_import(rT1, prT1.run_id, now=NOW)
        check("D23-noop-rejects-tampered-record-sources", apT1.verdict != 0 and apT1.promoted is False)

        # tampered acceptance: replace the archived acceptance bytes AND update the record's acceptance_sha256
        # to match them, so record and archive still AGREE; only the record-to-journal binding sees it.
        rT2, prT2, _ap0T2, ap1T2 = _fresh_promoted_d23("apply-d23r4-acc")
        check("D23-r4-acc-baseline-clean", ap1T2.verdict == 0 and ap1T2.promoted is True)
        _new_acc_T2 = b"replacement acceptance bytes (self-consistent record+archive)"
        (_arch_d23(rT2, prT2) / "acceptance.json").write_bytes(_new_acc_T2)
        _recT2 = tomllib.loads(_live_txn_path_d23(rT2, prT2).read_text())
        _recT2["acceptance_sha256"] = _sha256_hex(_new_acc_T2)
        _live_txn_path_d23(rT2, prT2).write_text(_opf_emit.emit(_recT2), encoding="utf-8")
        apT2 = apply_import(rT2, prT2.run_id, now=NOW)
        check("D23-noop-rejects-tampered-record-acceptance", apT2.verdict != 0 and apT2.promoted is False)

        # tampered other field: change a well-shaped record field (inventory_digest) to another well-shaped
        # value, leaving the archive untouched. The record still passes _validate_transaction_record and
        # FIX B (plan_digest unchanged), so only the record-to-journal binding rejects it.
        rT3, prT3, _ap0T3, ap1T3 = _fresh_promoted_d23("apply-d23r4-field")
        check("D23-r4-field-baseline-clean", ap1T3.verdict == 0 and ap1T3.promoted is True)
        _recT3 = tomllib.loads(_live_txn_path_d23(rT3, prT3).read_text())
        _recT3["inventory_digest"] = "sha256:" + ("d" * 64)   # well-shaped, different from the recorded value
        _live_txn_path_d23(rT3, prT3).write_text(_opf_emit.emit(_recT3), encoding="utf-8")
        apT3 = apply_import(rT3, prT3.run_id, now=NOW)
        check("D23-noop-rejects-tampered-record-field", apT3.verdict != 0 and apT3.promoted is False)

        # Round-7 (codex round-6 MEDIUM, class width with the gate): the no-op bind took the FIRST create/write
        # INTENT op on the record path and checked only its hash, so a genuine terminal journal re-encoded with
        # ONE INTENT mutation (record and archive untouched) still returned a promoted no-op. The shared helper
        # now requires EXACTLY the producer's one create (kind "file", FILE_MODE, the hash). The re-encode-only
        # control proves the harness re-framing alone leaves the no-op clean.
        def _mutate_intent_r7(nonce, mutate):
            r, pr, _ap0, ap1 = _fresh_promoted_d23(nonce)
            rec_rel = _txn_record_rel(pr.run_id)
            txn_id = tomllib.loads((r / rec_rel).read_text()).get("txn_id")
            jfd = os.open(str(r / IMPORT_JOURNAL_REL), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                frames, torn, _gl = _journal.read_frames(jfd, txn_id)
            finally:
                os.close(jfd)
            if torn or [t for t, _o in frames] != [_journal.F_INTENT, _journal.F_COMPLETE]:
                raise OSError("harness: the genuine journal is not a clean [INTENT, COMPLETE] sequence")
            mutate(frames[0][1]["ops"], rec_rel)
            (r / IMPORT_JOURNAL_REL / txn_id / "frames.log").write_bytes(b"".join(
                _journal._frame(ft, json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=True).encode()) for ft, obj in frames))
            return ap1, apply_import(r, pr.run_id, now=NOW)

        def _rec_op_r7(ops, rel):
            return [o for o in ops if o.get("path") == rel][0]

        ap1R7, apR7 = _mutate_intent_r7("apply-r7-ctl", lambda ops, rel: None)
        check("R7-reencoded-intent-noop-clean",
              ap1R7.verdict == 0 and apR7.verdict == 0 and apR7.outcome == "noop_already_complete")
        for _r7name, _r7mut in (
                ("create-kind-absent", lambda ops, rel: _rec_op_r7(ops, rel)["poststate"].update(kind="absent")),
                ("second-write", lambda ops, rel: ops.append(
                    {"op": "write", "path": rel, "poststate": {"kind": "file", "content-sha256": "e" * 64}})),
                ("duplicate-create", lambda ops, rel: ops.append(json.loads(json.dumps(_rec_op_r7(ops, rel))))),
                ("later-remove", lambda ops, rel: ops.append(
                    {"op": "remove", "path": rel, "poststate": {"kind": "absent"}})),
                ("create-mode-changed", lambda ops, rel: _rec_op_r7(ops, rel)["poststate"].update(mode=0o600)),
                ("alias-path-remove", lambda ops, rel: ops.append(
                    {"op": "remove", "path": rel.replace("/", "/./", 1), "poststate": {"kind": "absent"}}))):
            ap1R7, apR7 = _mutate_intent_r7("apply-r7-" + _r7name, _r7mut)
            check("R7-noop-rejects-intent-" + _r7name,
                  ap1R7.verdict == 0 and apR7.verdict != 0 and apR7.promoted is False
                  and "transaction record" in " ".join(str(f) for f in apR7.findings))

        # R5-C1 (round-6): the no-op path reads the retained record EXACTLY ONCE. The prior two-read code
        # parsed the record on a FIRST read (identity + FIX C archive validation) and authenticated a SECOND
        # read against the journal, so an interleaving that returned a TAMPERED record T on the first read and
        # the ORIGINAL R on the second passed the journal-bind (on R) while validating the archive against T ->
        # a false-clean no-op WITHOUT forging the journal. The single-read fix reads T once and BOTH parses and
        # journal-binds those same bytes, so T's hash != the journal-recorded hash -> fail-closed. This
        # simulates the interleaving by monkeypatching the record reader to return T on the FIRST record read
        # and R on any SECOND read; the archive is dropped to a body T's archived_sources omits, so T is
        # self-consistent with the tampered archive (only R4-C1's whole-record bind, on the single read,
        # rejects it). Verified against a temporary two-read revert: the OLD code false-cleans this vector.
        rRS, prRS, _ap0RS, ap1RS = _fresh_promoted_d23("apply-d23r5-swap")
        check("D23-r5-swap-baseline-clean", ap1RS.verdict == 0 and ap1RS.promoted is True)
        _rec_rel_RS = _txn_record_rel(prRS.run_id)
        _srcs_dir_RS = _arch_d23(rRS, prRS) / "sources"
        _dropped_RS = sorted(p.name for p in _srcs_dir_RS.iterdir())[0]
        (_srcs_dir_RS / _dropped_RS).unlink()                     # archive now omits one preserved body
        _recRS = tomllib.loads(_live_txn_path_d23(rRS, prRS).read_text())
        _recRS["archived_sources"] = [s for s in _recRS.get("archived_sources", []) if s != _dropped_RS]
        _tamper_RS = _opf_emit.emit(_recRS).encode("utf-8")       # T: self-consistent with the tampered archive
        _orig_rc_RS = _journal._read_contained
        _rc_calls_RS = {"n": 0}
        def _swap_rc_RS(root_fd, relpath, *a, **k):
            if relpath == _rec_rel_RS:
                _rc_calls_RS["n"] += 1
                if _rc_calls_RS["n"] == 1:
                    _r, _st = _orig_rc_RS(root_fd, relpath, *a, **k)   # real stat; substitute tampered bytes
                    return _tamper_RS, _st
                return _orig_rc_RS(root_fd, relpath, *a, **k)          # ORIGINAL R on any second read
            return _orig_rc_RS(root_fd, relpath, *a, **k)
        _journal._read_contained = _swap_rc_RS
        try:
            apRS = apply_import(rRS, prRS.run_id, now=NOW)
        finally:
            _journal._read_contained = _orig_rc_RS
        check("D23-noop-rejects-record-read-swap", apRS.verdict != 0 and apRS.promoted is False)

        # R6-C1 (round-6): the no-op archive revalidation captures the sources listing ONCE and fails closed
        # when _list_contained returns None. _list_contained returns None on a FileNotFoundError at the sources
        # dir (an OBSERVED disappearance AFTER the S_ISDIR check just above); the prior `or []` collapsed that
        # to an empty listing, which for a ZERO-SOURCE completed record (archived_sources == []) equalled the
        # recorded empty set and FALSE-CLEANED the no-op over a durable sources dir that vanished under
        # revalidation. Build a real zero-source promote (import_set == [], so archived_sources == []); its
        # intact repeat no-op stays CLEAN (a present-but-empty sources dir lists [] from _list_contained, which
        # is NOT None). Then monkeypatch _list_contained to return None ONLY for that run's archive sources rel
        # (simulating the race disappearance at the enumeration boundary) and confirm the repeat apply fails
        # closed, NOT a no-op. Verified against a temporary `or []` revert: the OLD code false-cleans (verdict 0).
        rootZ, mZ = build_apply_store()
        prZ = plan_import(rootZ, [], now=NOW, run_nonce="apply-d23r6-zero")
        rvZ = review_accept_all(rootZ, prZ.run_id)
        apZ0 = apply_import(rootZ, prZ.run_id, now=NOW)          # real zero-source promote
        apZ1 = apply_import(rootZ, prZ.run_id, now=NOW)          # intact zero-source no-op is CLEAN
        _txnZ = tomllib.loads((rootZ / ".aiqt" / "import" / (prZ.run_id or "X")
                               / "transaction.toml").read_text())
        check("D23-r6-zero-source-noop-clean",
              rvZ.verdict == 0 and apZ0.verdict == 0 and apZ0.promoted is True
              and apZ1.verdict == 0 and apZ1.promoted is True
              and apZ1.outcome == "noop_already_complete" and _txnZ.get("archived_sources") == [])
        _srcs_rel_Z = _archive_run_rel(prZ.run_id) + "/sources"
        _orig_lc_Z = _list_contained
        def _none_lc_Z(store_root_fd, rel):
            if rel == _srcs_rel_Z:
                return None                                     # the sources dir vanished after the S_ISDIR check
            return _orig_lc_Z(store_root_fd, rel)
        globals()["_list_contained"] = _none_lc_Z
        try:
            apZd = apply_import(rootZ, prZ.run_id, now=NOW)
        finally:
            globals()["_list_contained"] = _orig_lc_Z
        check("D23-noop-rejects-disappeared-sources-dir", apZd.verdict != 0 and apZd.promoted is False)

        # R6-C2 (round-6): the no-op single-read (R5-C1) restores the 1 MiB store-read cap the prior
        # _read_toml_contained route enforced, as TWO independent guards (mirroring _opf_store's two-check
        # idiom): a PRE-read reject bound to the already-captured lstat size (an oversized record refused
        # BEFORE any read), and a POST-read reject on the bytes actually read (a record that lstats under cap
        # but reads over it, a raced swap/growth past the pre-open size). Each guard carries a DISTINCT stable
        # substring ("store-read cap before it is read" vs "store-read cap after reading") so it is
        # independently assertable, and each has its OWN change-carries-check discriminator below. F1 (round-2):
        # the prior single combined discriminator only asserted the shared "store-read cap" text and tripped
        # the PRE check first, so it stayed green with EITHER guard alone removed (the other emitted a
        # "store-read cap" message); it could not pin the reject-BEFORE-read property R6-C2 exists for. The two
        # discriminators below each fail closed when ONLY their own guard is removed. Verified per-guard against
        # temporary single-guard reverts (in a /dev/shm scratch): reverting ONLY the pre-read check fails ONLY
        # -preread (the post-read check then emits an "after reading" message, no "before it is read", and the
        # record IS read so the read-count is non-zero); reverting ONLY the post-read check fails ONLY -postread
        # (the raced-growth vector is then read within the 16 MiB cap and rejected LATER by a DIFFERENT guard:
        # here the TOML parse refuses the injected bytes, a valid-TOML growth would instead meet the R4-C1
        # hash mismatch, so no "after reading" message); reverting R6-C1 still fails ONLY -disappeared-sources.
        #
        # -preread: grow the LIVE transaction.toml past MAX_STORE_READ_BYTES with a huge trailing TOML comment
        # (the record fields stay valid; tomllib ignores the comment), so its real lstat size exceeds the cap.
        # Count every _read_contained of the record rel; the PRE-read check must fire BEFORE any such read, so
        # the record is NEVER read (count == 0). This pins reject-before-read, the property a refactor could
        # otherwise silently drop back to read-then-reject with the suite green.
        rOv, prOv, _ap0Ov, ap1Ov = _fresh_promoted_d23("apply-d23r6-big")
        check("D23-r6-oversized-baseline-clean", ap1Ov.verdict == 0 and ap1Ov.promoted is True)
        _rec_rel_Ov = _txn_record_rel(prOv.run_id)
        _rec_path_Ov = _live_txn_path_d23(rOv, prOv)
        _pad_Ov = b"\n# " + b"x" * (_opf_store.MAX_STORE_READ_BYTES + 16) + b"\n"
        _rec_path_Ov.write_bytes(_rec_path_Ov.read_bytes() + _pad_Ov)   # valid record + huge comment, over cap
        _orig_rc_Ov = _journal._read_contained
        _rc_reads_Ov = {"n": 0}
        def _count_rc_Ov(root_fd, relpath, *a, **k):
            if relpath == _rec_rel_Ov:
                _rc_reads_Ov["n"] += 1
            return _orig_rc_Ov(root_fd, relpath, *a, **k)
        _journal._read_contained = _count_rc_Ov
        try:
            apOvP = apply_import(rOv, prOv.run_id, now=NOW)
        finally:
            _journal._read_contained = _orig_rc_Ov
        check("D23-noop-rejects-oversized-record-preread",
              apOvP.verdict == 2 and apOvP.promoted is False
              and _rc_reads_Ov["n"] == 0
              and any("store-read cap before it is read" in f for f in apOvP.findings))

        # -postread: the PRE check cannot see a record that lstats UNDER cap but READS over it. Take a fresh
        # CLEAN baseline no-op, then monkeypatch the record reader to return >cap bytes for the record rel ONLY
        # (the real lstat stays under cap, so the PRE check passes), so ONLY the POST-read length check fires.
        rOw, prOw, _ap0Ow, ap1Ow = _fresh_promoted_d23("apply-d23r6-grow")
        check("D23-r6-oversized-postread-baseline-clean", ap1Ow.verdict == 0 and ap1Ow.promoted is True)
        _rec_rel_Ow = _txn_record_rel(prOw.run_id)
        _orig_rc_Ow = _journal._read_contained
        _over_bytes_Ow = b"# raced growth past the pre-open size\n" + b"y" * (_opf_store.MAX_STORE_READ_BYTES + 16)
        def _grow_rc_Ow(root_fd, relpath, *a, **k):
            if relpath == _rec_rel_Ow:
                _r, _st = _orig_rc_Ow(root_fd, relpath, *a, **k)   # real (under-cap) stat; over-cap bytes
                return _over_bytes_Ow, _st
            return _orig_rc_Ow(root_fd, relpath, *a, **k)
        _journal._read_contained = _grow_rc_Ow
        try:
            apOwP = apply_import(rOw, prOw.run_id, now=NOW)
        finally:
            _journal._read_contained = _orig_rc_Ow
        check("D23-noop-rejects-oversized-record-postread",
              apOwP.verdict == 2 and apOwP.promoted is False
              and any("store-read cap after reading" in f for f in apOwP.findings))

        # A3 (acceptance required): a planned-but-UNREVIEWED run is not promotion-ready -> exit 2, and the
        # aborted apply releases the writer lock (a following good apply is not blocked). Mutates nothing.
        rootA3, mA3 = build_apply_store()
        prA3 = plan_import(rootA3, ["a.txt"], now=NOW, run_nonce="apply-a3")
        a3_before = snapshot(mA3)
        apA3 = apply_import(rootA3, prA3.run_id, now=NOW)
        check("A3-unreviewed-cannot-eval", apA3.verdict == 2 and apA3.promoted is False)
        check("A3-unreviewed-mutates-nothing", snapshot(mA3) == a3_before)
        review_accept_all(rootA3, prA3.run_id)
        apA3b = apply_import(rootA3, prA3.run_id, now=NOW)   # lock not leaked by the abort above
        check("A3-lock-not-leaked", apA3b.verdict == 0 and apA3b.promoted is True)

        # Flip: disabling step 6's generation re-check promotes after this manifest read changes homes.
        rootHG, mHG = build_apply_store()
        prHG = plan_import(rootHG, ["a.txt"], now=NOW, run_nonce="apply-homes-change")
        rvHG = review_accept_all(rootHG, prHG.run_id)
        beforeHG = snapshot(rootHG)
        real_layout = _require_inline_layout

        def changed_layout(fd, machine_rel):
            manifest = real_layout(fd, machine_rel)
            return dict(manifest, opf=dict(manifest["opf"], homes=2, spec_version=_opf_store.HOMES2_SPEC_VERSION))

        with _patch.object(_opf_store, "SUPPORTED_HOMES", 2), \
                _patch.object(sys.modules[__name__], "_require_inline_layout", side_effect=changed_layout) as readHG:
            apHG = apply_import(rootHG, prHG.run_id, now=NOW)
        check("accept-apply-homes-rederived", rvHG.verdict == CLEAN and readHG.call_count == 1
              and apHG.verdict == CANNOT_EVALUATE and apHG.promoted is False and apHG.outcome == "aborted"
              and "homes generation changed during apply" in apHG.findings[0]
              and snapshot(rootHG) == beforeHG)

        # A4 (reject blocks): a reject decision in the acceptance record blocks promotion -> exit 1
        # (outcome=rejected), and nothing is promoted or mutated.
        rootA4, mA4 = build_apply_store()
        prA4 = plan_import(rootA4, ["a.txt"], now=NOW, run_nonce="apply-a4")
        rvA4 = review_accept_all(rootA4, prA4.run_id, verb_for_first="reject")
        check("A4-review-with-reject-captured", rvA4.verdict == 0)
        a4_before = snapshot(mA4)
        apA4 = apply_import(rootA4, prA4.run_id, now=NOW)
        check("A4-reject-blocks",
              apA4.verdict == 1 and apA4.outcome == "rejected" and apA4.promoted is False)
        check("A4-reject-mutates-nothing", snapshot(mA4) == a4_before)

        # A5: pre-existing corrupted views remain a refusal. D5 checks the live baseline before
        # deferring candidate view drift, so existing damage still aborts -> exit 1, nothing promoted.
        rootA5, mA5 = build_apply_store()
        for _view in (rootA5 / ".working").glob("*.md"):
            _view.write_text(_view.read_text() + "\nINJECTED DRIFT\n", encoding="utf-8")
        prA5 = plan_import(rootA5, ["a.txt"], now=NOW, run_nonce="apply-a5")
        review_accept_all(rootA5, prA5.run_id)
        a5_before = snapshot(mA5)
        apA5 = apply_import(rootA5, prA5.run_id, now=NOW)
        check("A5-composition-gate-fail-closed", apA5.verdict == 1 and apA5.promoted is False)
        check("A5-composition-mutates-nothing", snapshot(mA5) == a5_before)

        # A6 (run-id grammar): a run-id outside the grammar is cannot-evaluate BEFORE any path use.
        apA6 = apply_import(rootA1, "not-a-run-id", now=NOW)
        check("A6-bad-run-id-cannot-eval", apA6.verdict == 2 and apA6.promoted is False)

        # A7 (PRC-F3): a source body tampered after review (bytes changed, content-address filename kept)
        # fails closed at apply; _build_publication_ops revalidates every preserved body against its
        # content-address (and the digest-bound inventory set) before archival + terminal delete. Without the
        # content-address verify the corrupt body would be archived and the run promoted.
        rootA7, mA7 = build_apply_store()
        prA7 = plan_import(rootA7, ["a.txt"], now=NOW, run_nonce="apply-a7")
        review_accept_all(rootA7, prA7.run_id)
        srcdirA7 = mA7.parent / "imports" / (prA7.run_id or "X") / "sources"
        bodiesA7 = sorted(srcdirA7.iterdir()) if srcdirA7.is_dir() else []
        a7_before = snapshot(mA7)
        if bodiesA7:
            bodiesA7[0].write_bytes(b"TAMPERED bytes that do not match the content-address filename")
        apA7 = apply_import(rootA7, prA7.run_id, now=NOW)
        check("A7-tampered-body-fail-closed",
              bool(bodiesA7) and apA7.verdict != 0 and apA7.promoted is False)
        check("A7-tampered-body-mutates-nothing", snapshot(mA7) == a7_before)
        # A-finding2 (PRC finding-2): apply now runs the FULL staged-artifact gate (check_staged_run), so a
        # staged artifact tampered AFTER review (run.toml bytes changed without refreshing the report digest)
        # is caught by artifact-digest-integrity before promotion -- not only the source bodies F3 relocates.
        # Without the apply-time gate the narrower loader passed and the tampered run promoted.
        rootF2g, mF2g = build_apply_store()
        prF2g = plan_import(rootF2g, ["a.txt"], now=NOW, run_nonce="apply-f2g")
        review_accept_all(rootF2g, prF2g.run_id)
        runtomlF2g = mF2g.parent / "imports" / (prF2g.run_id or "X") / "run.toml"
        f2g_before = snapshot(mF2g)
        tampered_f2g = runtomlF2g.is_file()
        if tampered_f2g:
            runtomlF2g.write_text(runtomlF2g.read_text() + "\n# tampered after review\n", encoding="utf-8")
        apF2g = apply_import(rootF2g, prF2g.run_id, now=NOW)
        check("finding2-tampered-artifact-fail-closed",
              tampered_f2g and apF2g.verdict != 0 and apF2g.promoted is False)
        check("finding2-tampered-artifact-mutates-nothing", snapshot(mF2g) == f2g_before)

        # A8 (PRC-F4): a store missing its REQUIRED worklog ledger surfaces a C-RECORDS cannot-evaluate with NO
        # _SCHEMA_DEFERRAL marker; the D4 composition predicate treats it as a REAL finding, never tolerating it
        # by the C-RECORDS check id. Without the reason-aware tolerance the missing-worklog cant rode the
        # deferral id into an empty (falsely clean) finding list.
        rootA8, mA8 = build_apply_store()
        (mA8 / "worklog.toml").unlink()
        a8_findings = _preview_composition_findings(str(rootA8))
        check("A8-missing-worklog-is-a-finding", any("C-RECORDS" in f for f in a8_findings))

        # A9 (PRC-F5b): _assemble_preview drops .git/.aiqt ONLY at the store root; a nested `.working/.aiqt` is
        # COPIED into the preview so the D4 gate can grade it, not silently dropped. Without the store-root
        # scoping the nested rogue path was absent from the preview and hidden from composition.
        import shutil as _shutil_a9
        rootA9, mA9 = build_apply_store()
        (rootA9 / ".working" / ".aiqt").mkdir(parents=True, exist_ok=True)
        (rootA9 / ".working" / ".aiqt" / "rogue.txt").write_text("rogue", encoding="utf-8")
        resA9 = _opf_store.resolve_store(rootA9)
        prevA9 = base / "preview-a9"
        _assemble_preview(resA9, str(mA9.relative_to(rootA9)), {}, str(prevA9), _shutil_a9, None)
        check("A9-nested-aiqt-copied-to-preview",
              (prevA9 / ".working" / ".aiqt" / "rogue.txt").is_file())
        # A10 (PRC-F5b): _assemble_preview drops ONLY the promoted run from .working/imports, RETAINING any
        # sibling staging run so D4 grades it (publication deletes only the promoted run). Without the
        # run-scoped drop the whole imports tree was hidden and a leftover sibling escaped the pre-publish gate.
        import shutil as _shutil_a10
        rootA10, mA10 = build_apply_store()
        prom_run_a10 = "run-promoted-0001"
        sib_run_a10 = "run-sibling-0002"
        (rootA10 / ".working" / "imports" / prom_run_a10).mkdir(parents=True, exist_ok=True)
        (rootA10 / ".working" / "imports" / prom_run_a10 / "x.toml").write_text("schema = 1", encoding="utf-8")
        (rootA10 / ".working" / "imports" / sib_run_a10).mkdir(parents=True, exist_ok=True)
        (rootA10 / ".working" / "imports" / sib_run_a10 / "y.toml").write_text("schema = 1", encoding="utf-8")
        resA10 = _opf_store.resolve_store(rootA10)
        prevA10 = base / "preview-a10"
        _assemble_preview(resA10, str(mA10.relative_to(rootA10)), {}, str(prevA10), _shutil_a10, prom_run_a10)
        check("A10-promoted-run-dropped-sibling-retained",
              not (prevA10 / ".working" / "imports" / prom_run_a10).exists()
              and (prevA10 / ".working" / "imports" / sib_run_a10 / "y.toml").is_file())
        # A11: apply hands the preview the store's homes generation, derived from the manifest the inline-
        # layout gate reads: a legacy store stays generation 1 and an activated homes-2 store reaches the
        # homes-2 preview. The spy stops apply at the preview, before anything is promoted.
        from unittest.mock import patch as _patch_a11

        class _PreviewReached(BaseException):
            pass

        def _a11_generation(generation, supported=None):
            # `generation` selects the manifest; `supported` (default: the same) is the activated tooling.
            root, mdir = build_apply_store()
            planned = plan_import(root, ["a.txt"], now=NOW, run_nonce="apply-a11-{}".format(generation))
            review_accept_all(root, planned.run_id)
            if generation == 2:
                model = tomllib.loads((mdir / "manifest.toml").read_text(encoding="utf-8"))
                model["opf"]["homes"] = 2
                (mdir / "manifest.toml").write_text(_opf_emit.emit_checked(model), encoding="utf-8")
            seen = []
            real_validate = _opf_store.validate_manifest

            def spy(*_args, **kwargs):
                seen.append(kwargs.get("homes", 1))
                raise _PreviewReached

            def validate_declared(data, *args, **kwargs):
                # The homes declaration is not yet a manifest schema key: validate the rest as shipped.
                opf = data.get("opf") if isinstance(data, dict) else None
                if isinstance(opf, dict) and "homes" in opf:
                    data = dict(data, opf=dict((k, v) for k, v in opf.items() if k != "homes"))
                return real_validate(data, *args, **kwargs)

            with _patch_a11.object(_opf_store, "SUPPORTED_HOMES", generation if supported is None else supported), \
                    _patch_a11.object(_opf_store, "HOMES2_SPEC_VERSION", _opf_store.SUPPORTED_SPEC_VERSION), \
                    _patch_a11.object(_opf_store, "validate_manifest", validate_declared), \
                    _patch_a11.object(sys.modules[__name__], "_assemble_preview", spy):
                try:
                    apply_import(root, planned.run_id, now=NOW)
                except _PreviewReached:
                    pass
            return seen

        check("A11-legacy-preview-generation", _a11_generation(1) == [1])
        check("A11-homes2-preview-generation", _a11_generation(2) == [2])
        # Activated tooling alone activates nothing: a legacy manifest still hands the preview generation 1.
        check("A11-activated-legacy-preview-generation", _a11_generation(1, supported=2) == [1])

        # ======================= round-4 fix discriminators (change-carries-check) =======================
        # The round-4 fixes (N1 TOML-aware manifest flip, F5b store-root imports anchor, N2 close-quietly
        # cleanup) each land with a check that fails when the fix is absent. (The F2 non-empty-id discriminator
        # A2f sits with the A2-series no-op checks above, before A-f6 leaks a lock into rootA1.)

        # N1flip (PRC-N1 round-4): _flip_import_status_bytes is TOML-aware and re-emits canonically, so it
        # flips import_status="complete" for ANY valid manifest form, not only the bare double-quoted
        # `import_status = "x"` the old regex matched. A single-quoted import_status (valid TOML the
        # double-quote-only regex missed) flips cleanly. Without the tomllib rewrite this raised _cannot
        # (0 regex matches -> "expected exactly one `import_status` directive").
        rootN1, mN1 = build_apply_store()
        manifest_relN1 = "{}/manifest.toml".format(mN1.relative_to(rootN1))   # ".working/toml/manifest.toml"
        canonN1 = (mN1 / "manifest.toml").read_text(encoding="utf-8")
        check("N1flip-canonical-double-quoted-present", 'import_status = "none"' in canonN1)
        altN1 = canonN1.replace('import_status = "none"', "import_status = 'none'", 1)   # single-quoted form
        (mN1 / "manifest.toml").write_text(altN1, encoding="utf-8")
        resN1 = _opf_store.resolve_store(rootN1)
        fdN1 = _opf_store._open_store_root_fd(resN1.store_root, resN1.pointer_source != "default")
        try:
            flippedN1 = _flip_import_status_bytes(fdN1, manifest_relN1)
        finally:
            os.close(fdN1)
        check("N1flip-differently-formatted-flips-to-complete",
              tomllib.loads(flippedN1.decode("utf-8"))["opf"]["import_status"] == "complete")

        # N1bigint (F-TOML-BARE-VALUEERROR-CLASS): a live manifest carrying an integer literal past CPython's
        # 4300-digit string-conversion ceiling makes tomllib raise a BARE ValueError (not TOMLDecodeError),
        # so the flip's parse must convert the whole ValueError family to CANNOT-EVALUATE, never raise.
        # The store fd is opened BEFORE the over-long line is appended (resolve_store reads the manifest
        # too). Reverting the parse locus to (UnicodeDecodeError, TOMLDecodeError) lets the ValueError
        # escape (vN1big == "escaped"). The digit limit is PINNED to the default 4300 (test-hermeticity)
        # so an ambient unlimited setting cannot parse the literal cleanly; restored in finally.
        # N1deep: the same class's RecursionError member. A 1200-deep nested array makes tomllib raise
        # RecursionError (a RuntimeError, not a ValueError); it too must be CANNOT-EVALUATE. The recursion
        # limit is pinned to the CPython default 1000 alongside the digit limit (test-hermeticity).
        for n1_label, n1_line in (("N1bigint-over-long-integer", "over_long = " + "9" * 4400 + "\n"),
                                  ("N1deep-nested-array", "deep = " + "[" * 1200 + "]" * 1200 + "\n")):
            rootN1big, mN1big = build_apply_store()
            manifest_relN1big = "{}/manifest.toml".format(mN1big.relative_to(rootN1big))
            resN1big = _opf_store.resolve_store(rootN1big)
            fdN1big = _opf_store._open_store_root_fd(resN1big.store_root, resN1big.pointer_source != "default")
            _n1big_prev_idlimit = sys.get_int_max_str_digits()
            _n1big_prev_reclimit = sys.getrecursionlimit()
            sys.set_int_max_str_digits(4300)
            sys.setrecursionlimit(1000)
            try:
                with open(mN1big / "manifest.toml", "a", encoding="utf-8") as fh:
                    fh.write(n1_line)
                try:
                    _flip_import_status_bytes(fdN1big, manifest_relN1big)
                    vN1big = "flipped"
                except _StageError as exc:
                    vN1big = exc.verdict
                except (ValueError, RecursionError):
                    vN1big = "escaped"
            finally:
                sys.setrecursionlimit(_n1big_prev_reclimit)
                sys.set_int_max_str_digits(_n1big_prev_idlimit)
                os.close(fdN1big)
            check(n1_label + "-manifest-cannot-eval", vN1big == CANNOT_EVALUATE)

        # N2a (PRC-N2 round-4, unit): _journal._close_fd_quietly swallows a close-time OSError rather than
        # propagating it. A double close (the second os.close raises EBADF and fstat confirms the fd gone)
        # returns cleanly; a raw os.close would raise EBADF out to the caller.
        _rp_n2, _wp_n2 = os.pipe()
        os.close(_wp_n2)
        os.close(_rp_n2)                       # first, real close
        _n2a_raised = False
        try:
            _journal._close_fd_quietly(_rp_n2)   # second close: EBADF; fstat EBADF -> confirmed gone, no raise
        except OSError:
            _n2a_raised = True
        check("N2a-close-quietly-swallows-oserror", _n2a_raised is False)

        # N2d (re-QA): _close_fd_quietly's DIAGNOSTIC path must NEVER raise. Drive it to the fail-surface branch
        # (both os.close calls raise while fstat proves the fd still open) AND make the stderr WRITE itself raise
        # OSError (a broken stderr): the helper must RETURN, not propagate. Without the try/except around the
        # final print, a broken-stderr OSError escapes the helper and, at the apply cleanup call sites, reaches
        # the outer `except OSError` and overturns a committed promotion.
        _rp_n2d, _wp_n2d = os.pipe()                     # a real, open fd so the helper's fstat confirms it live
        _saved_close_n2d = os.close
        _saved_stderr_n2d = sys.stderr
        class _BrokenStderr_n2d:
            def write(self, *_a, **_k):
                raise OSError(errno.EIO, "broken stderr write")
            def flush(self, *_a, **_k):
                raise OSError(errno.EIO, "broken stderr flush")
        def _close_raises_n2d(_fd):
            raise OSError(errno.EIO, "injected close failure")
        _n2d_raised = False
        try:
            os.close = _close_raises_n2d                  # BOTH closes in the helper now raise
            sys.stderr = _BrokenStderr_n2d()             # ... and the diagnostic write raises too
            try:
                _journal._close_fd_quietly(_rp_n2d)
            except BaseException:
                _n2d_raised = True
        finally:
            os.close = _saved_close_n2d
            sys.stderr = _saved_stderr_n2d
        os.close(_rp_n2d)                                # real cleanup of the still-open fds
        os.close(_wp_n2d)
        check("N2d-close-quietly-diagnostic-nonthrow", _n2d_raised is False)

        # N2b (PRC-N2 round-4, behavioural): a descriptor-close OSError on the apply cleanup path does NOT
        # overturn a committed promotion. Inject an OSError on the FIRST close of the journal-root fd (jr_fd),
        # then promote a reviewed run: _close_fd_quietly swallows the raise (fstat confirms the fd, retries)
        # and the CLEAN result stands. Without the _close_fd_quietly routing the raw os.close raised into the
        # outer OSError handler and flipped promoted=True to aborted. `fired` asserts the injection ran.
        rootN2, mN2 = build_apply_store()
        prN2 = plan_import(rootN2, ["a.txt"], now=NOW, run_nonce="apply-n2")
        review_accept_all(rootN2, prN2.run_id)
        _saved_ojr_n2 = _journal.open_journal_root_fd
        _saved_close_n2 = os.close
        _n2b = {"jr_fd": None, "fired": False}
        def _capture_ojr_n2(_rfd, _rel):
            _fd = _saved_ojr_n2(_rfd, _rel)
            _n2b["jr_fd"] = _fd
            return _fd
        def _close_n2(_fd):
            if _fd == _n2b["jr_fd"] and not _n2b["fired"]:
                _n2b["fired"] = True
                raise OSError(errno.EIO, "injected jr_fd cleanup-close error")
            return _saved_close_n2(_fd)
        _journal.open_journal_root_fd = _capture_ojr_n2
        os.close = _close_n2
        try:
            apN2 = apply_import(rootN2, prN2.run_id, now=NOW)
        finally:
            os.close = _saved_close_n2
            _journal.open_journal_root_fd = _saved_ojr_n2
        check("N2b-cleanup-close-oserror-does-not-overturn",
              apN2.verdict == 0 and apN2.promoted is True and apN2.outcome == "promoted"
              and _n2b["fired"] is True)

        # N2c (PRC-N2 r3): the N2 fix routes BOTH cleanup closes (jr_fd then root_fd) through _close_fd_quietly.
        # N2b guards the jr_fd routing; this guards the root_fd routing (reverting ONLY root_fd otherwise leaves
        # the suite green). Arm AFTER jr_fd's cleanup close so the injected OSError lands on root_fd's close (the
        # next close, in the outer finally); the committed promotion must stand. Without _close_fd_quietly on
        # root_fd the raw os.close raises into the outer OSError handler and flips promoted=True to aborted.
        rootN2c, mN2c = build_apply_store()
        prN2c = plan_import(rootN2c, ["a.txt"], now=NOW, run_nonce="apply-n2c")
        review_accept_all(rootN2c, prN2c.run_id)
        _saved_ojr_n2c = _journal.open_journal_root_fd
        _saved_close_n2c = os.close
        _n2c = {"jr_fd": None, "jr_closed": False, "fired": False}
        def _capture_ojr_n2c(_rfd, _rel):
            _fd = _saved_ojr_n2c(_rfd, _rel)
            _n2c["jr_fd"] = _fd
            return _fd
        def _close_n2c(_fd):
            if _fd == _n2c["jr_fd"] and not _n2c["jr_closed"]:
                _n2c["jr_closed"] = True
                return _saved_close_n2c(_fd)               # let jr_fd close normally (N2b covers it)
            if _n2c["jr_closed"] and not _n2c["fired"]:
                _n2c["fired"] = True
                raise OSError(errno.EIO, "injected root_fd cleanup-close error")
            return _saved_close_n2c(_fd)
        _journal.open_journal_root_fd = _capture_ojr_n2c
        os.close = _close_n2c
        try:
            apN2c = apply_import(rootN2c, prN2c.run_id, now=NOW)
        finally:
            os.close = _saved_close_n2c
            _journal.open_journal_root_fd = _saved_ojr_n2c
        check("N2c-rootfd-close-oserror-does-not-overturn",
              apN2c.verdict == 0 and apN2c.promoted is True and apN2c.outcome == "promoted"
              and _n2c["fired"] is True)

        # F5bN (PRC-F5b round-4): the promoted-run drop is anchored to the STORE-ROOT imports dir by ABSOLUTE
        # path, not a basename pair, so a directory named like the promoted run planted inside a NESTED
        # `.working/imports` deeper in a staging run is NOT dropped (only the real store-root imports/<run> is).
        # Without the abspath anchor the basename check also matched the nested `.working/imports` and dropped
        # the same-named entry there. (A10 above covers store-root sibling retention; this covers the nested
        # same-named entry the basename check would have wrongly dropped.)
        import shutil as _shutil_f5bn
        rootF5bn, mF5bn = build_apply_store()
        prom_run_f5bn = "run-promoted-9001"
        (rootF5bn / ".working" / "imports" / prom_run_f5bn).mkdir(parents=True, exist_ok=True)
        (rootF5bn / ".working" / "imports" / prom_run_f5bn / "r.toml").write_text("schema = 1", encoding="utf-8")
        nested_f5bn = (rootF5bn / ".working" / "imports" / "run-sib-9002" / ".working" / "imports" / prom_run_f5bn)
        nested_f5bn.mkdir(parents=True, exist_ok=True)
        (nested_f5bn / "nested.toml").write_text("schema = 1", encoding="utf-8")
        resF5bn = _opf_store.resolve_store(rootF5bn)
        prevF5bn = base / "preview-f5bn"
        _assemble_preview(resF5bn, str(mF5bn.relative_to(rootF5bn)), {}, str(prevF5bn), _shutil_f5bn, prom_run_f5bn)
        check("F5bN-nested-imports-not-dropped",
              not (prevF5bn / ".working" / "imports" / prom_run_f5bn).exists()
              and (prevF5bn / ".working" / "imports" / "run-sib-9002" / ".working" / "imports"
                   / prom_run_f5bn / "nested.toml").is_file())

        # F5ptr (re-QA): _assemble_preview NEUTRALIZES the store-root OPF pointer files, so resolve_store over
        # the preview binds to the preview ITSELF (a self-contained default store) rather than following a copied
        # `[store].target` pointer BACK to the original store and grading the wrong target at the D4 gate. Build
        # the preview from a default store carrying a committed `.opf.toml` whose ABSOLUTE target names a SECOND
        # valid store: without the pointer drop resolve_store(preview) follows it OUT to that second store.
        import shutil as _shutil_f5p
        rootF5p, mF5p = build_apply_store()
        otherF5p, _mOtherF5p = build_apply_store()       # a second, resolvable store the pointer will name
        resF5p = _opf_store.resolve_store(rootF5p)        # default resolution captured BEFORE the pointer lands
        (rootF5p / _opf_store.POINTER_REL).write_text(
            '[store]\ntarget = "{}"\n'.format(otherF5p), encoding="utf-8")   # absolute target -> the other store
        prevF5p = base / "preview-f5ptr"
        _assemble_preview(resF5p, str(mF5p.relative_to(rootF5p)), {}, str(prevF5p), _shutil_f5p, None)
        resolvedF5p = _opf_store.resolve_store(prevF5p)
        prev_abs_f5p = os.path.abspath(str(prevF5p))
        sr_abs_f5p = os.path.abspath(str(resolvedF5p.store_root)) if resolvedF5p.store_root is not None else ""
        check("F5ptr-preview-pointer-neutralized",
              resolvedF5p.status == _opf_store.RESOLVED
              and (sr_abs_f5p == prev_abs_f5p or sr_abs_f5p.startswith(prev_abs_f5p + os.sep)))

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
        # R4-C2: restore the inherited crash-injection var (if any) elapsed-agnostic; a value the run
        # itself leaked is dropped, an inherited one is put back exactly as found.
        if _saved_kill_env is None:
            os.environ.pop(_journal.KILL_ENV, None)
        else:
            os.environ[_journal.KILL_ENV] = _saved_kill_env

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
    print("usage: _opf_import.py --self-test (a library module; the import verb is wired via opf.py)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
