#!/usr/bin/env python3
"""OPFiles (OPF) store-level integrity validator: the reusable `opf doctor` engine (OPF core-tooling U6).

Offline, stdlib only, fail-closed, parse-only and read-only over the store. U2 (`_opf_schema`) validates
ONE record at a time and U3 (`_opf_release`) validates ONE ledger at a time; neither can see the whole
store, so the class of spec-MANDATED cross-record, historical, archive, view, changelog, and topology
invariants the integrity layer owns (OPF-SPEC 11) has no owner. THIS unit builds that whole-store layer as
a reusable ENGINE, `validate_store`, that REUSES the U1/U2/U3/U4/U5/U8 primitives and never
re-implements per-record or per-ledger logic. It is the engine the future `opf doctor` VERB will drive; the
verb itself is deliberately NOT wired here (see the sequencing note below).

Routing model (the structural cure for the hollow-validator class). The spec-11 integrity roster is a
CLOSED tuple, `REQUIRED_CHECKS`. Every required check calls `rep.ran(<check-id>)` exactly once, whatever it
finds. `result()` reconciles the emitted set against `REQUIRED_CHECKS`: a check that silently stops running
is a missing id, which reconciliation routes to CANNOT-EVALUATE naming it, so a skipped check can never read
as VALID. Each check routes to exactly one of: GRADE (evaluable from parsed TOML, contained filesystem
listings, or a supplied observation; a defect is `rep.finding` -> INVALID); FAIL-CLOSED CANNOT-EVALUATE (an
input the engine structurally lacks, git tracking state, the real remote, a prior committed snapshot, or the
product root, was not supplied -> `rep.cant` naming it, never a silent VALID); or DISCLOSED RESIDUAL (a
spec-17 item no deterministic parse-only gate can reach, reported in `residuals`, never able to let a broken
store certify VALID). Both `findings` and `cannot_evaluate` stay surfaced whatever dominates, so a finding is
never masked by a missing anchor. CANNOT-EVALUATE dominates INVALID, which dominates VALID.

Git-derived facts arrive ONLY via the inert `observations` argument (produced by a future git-aware doctor
step; the self-test builds it directly). The module runs no git, subprocess, network, clock, or cwd access,
and every declared input is read through U1's contained (dir-fd, no-follow) readers, so a swapped symlink is
refused rather than followed off-tree. FAIL CLOSED EVERYWHERE: any unreadable, unparseable, exotic, or
malformed declared input, and any malformed injected observation, is a CANNOT-EVALUATE that NAMES the input,
never a silent empty pass; every declared read refuses a non-regular (FIFO/device/socket) entry before
opening it, the containment walk is depth-bounded, and a top-level barrier turns any unexpected error into a
CANNOT-EVALUATE, so a hostile-shaped store never crashes, hangs, or blocks the engine. That non-regular
refusal is an lstat-then-open check, sound under the store's single-writer model (spec 5.7); a writer that
swaps a regular file for a FIFO in the window between the lstat and the open is the disclosed
concurrent-mutation residual, not covered by a parse-only reader. Time and memory are
proportional to the bytes actually read through the contained
readers; this engine's own loops are sized by the ids and bytes actually present, never by a declared
integer (a `WL-10**9` id, a `10**9` high-water, or a `["WL-1","WL-10**9"]` span expands no loop here). A
composed U3 helper may construct a LAZY `range` over a declared worklog span, but it is iterated only
against the present ids and short-circuits at the first gap, so a declared numeric field still cannot
amplify the run into an enumeration of the interval.

CRITICAL SEQUENCING (honoured by this unit): the live `doctor` verb is NOT CLI-wired here. A prerequisite
hardening step (VC-4-HARDEN) has not landed, and wiring a live store verb before it is a known finding
(PD-OPF-PASSA-DEPTH option B, F-373). U6 therefore lands as (1) this module plus (2) its self-test wired
into the `opf-check` self-test leg ONLY, exactly as the earlier units did. Assurance rides `--self-test`
over synthetic whole stores; the observation seam under test is exercised directly.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_check.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import bisect
import collections
import hashlib
import ipaddress
import os
import re
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_worklog   # noqa: E402  worklog shape authority
import _opf_store     # noqa: E402  shared homes and operand boundaries
import _journal        # noqa: E402  contained no-follow parent-open primitive (reused for listing / raw reads)
import _opf_emit       # noqa: E402  U8 canonical emitter (the per-record digest basis)
import _opf_views      # noqa: E402  U4 deterministic view planner (byte-drift comparison)
import _opf_changelog  # noqa: E402  U5 changelog range-coverage + freeze gates (composed here)
# U1 supplies the outcome model, the contained readers, the manifest validator, the taxonomy, and helpers.
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, RESOLVED, MANIFEST_NAME, WORKING_DIRNAME, StoreError,
    _read_toml_contained, _open_store_root_fd, _open_root_fd, validate_manifest, classify_target,
    _sorted_key_names, _safe_display, _is_contained_relpath,
    BASELINE_TYPES, MODULE_TYPES, IMPORTER_TYPES, KNOWN_MODULES, SUPPORTED_SPEC_VERSION,
)
# U2 supplies the record validator, the counter guards, the transition validator, and the type specs.
from _opf_schema import (  # noqa: E402
    validate_record, validate_counters, check_unique_ids, check_ids_within_counters, check_monotonic,
    validate_transition, parse_status, BASELINE_SPECS, ACTOR_KINDS, LINK_RELS, _valid_id_shape,
    SUPPORTED_SCHEMA, _valid_timestamp, IMPORTED_NAMESPACES, IMPORTED_PREFIX, parse_series_id,
)
# U3 supplies the ledger validators, the frozen/rotation/partition guards, and the across-time append-only.
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id,
    check_frozen_coverage, check_ids_partition, check_rotation_only_released, _verify_append_only, _wl_num,
    parse_semver, _parse_span, _valid_digest, RELEASE_KEYS,
)

# Fixed store-tree file / directory names (OPF-SPEC 4.2 layout; all lowercase machine source).
COUNTERS_NAME = "counters.toml"
VERSION_NAME = "version.toml"
WORKLOG_NAME = _opf_worklog.LEGACY_NAME
LEASE_NAME = "lease.toml"                  # present only while the single-writer lease is held (spec 5.7)
# The managed bootstrap provenance a coupled D2b `opf init` writes (OPF-INIT-D2B "Bootstrap Provenance",
# base spec 1.2.0): a managed machine-store leaf when present, never required (an upgraded D2a store has
# none, and none is ever fabricated for it).
INIT_PROVENANCE_NAME = "init.toml"
ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_NAME = "archive.toml"
EVIDENCE_FORMAT = _opf_store.EVIDENCE_INVENTORY_FORMAT
INDEX_SUFFIX = ".index.toml"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")
# The legacy imports staging tree `.working/imports`, store scope and a sibling of the machine subdir: the
# control root store_control_roots(1) reserves (spec 14.1). Nothing writes it since the import engine was
# removed, but the reservation stands, so containment still registers it and grades its interior.
IMPORTS_REL = _opf_store.store_control_roots(1)[0]


def _is_import_run_id(name):
    """Whether `name` is an `imp-<UTCSTAMP>Z-<hash16>` run id, the import-home grammar _opf_store owns."""
    try:
        _opf_store._home_run("import", name)
    except ValueError:
        return False
    return True

# The containment walk is bounded by an explicit depth ceiling so a pathologically deep directory chain
# fails closed to a CANNOT-EVALUATE rather than a RecursionError (B6). A real store tree is a few levels
# deep (.working/<machine>/archive/<YYYY>/, imports/<run-id>/<sub>/), far below this ceiling.
_CONTAINMENT_MAX_DEPTH = 64

# Closed keysets for the shapes this unit defines (see the module docstring).
INDEX_TOP_KEYS = frozenset({"schema", "record"})
# The per-record registry row (OPF-SPEC 9:839, 13): id, state, path, digest, all REQUIRED and CLOSED. The
# earlier {id, digest} shape under-read the row; this schema-release unit fixes it (F10).
PERRECORD_ROW_KEYS = frozenset({"id", "state", "path", "digest"})
ARCHIVE_TOP_KEYS = frozenset({"schema", "moved", "worklog_moved"})
ARCHIVE_MOVED_KEYS = frozenset({"id", "type", "destination"})
ARCHIVE_WLMOVED_KEYS = frozenset({"span", "destination"})
# The single-writer lease payload (spec 5.7): lease.toml is present only while the lease is held, carrying
# the holder, the operation, and an acquired-at timestamp read from the clock. This schema-release unit
# DEFINES the closed shape (the operation layer that writes it ships in a later release; spec 5.7/1).
LEASE_TOP_KEYS = frozenset(("schema", "holder", "operation", "acquired_at"))
# The enabled roster types that are LEDGERS, not index/per-record slots: the worklog home is worklog.toml
# (a fixed ledger), so it never has a `<type>.index.toml` or a `<type>/<id>.toml` body. A ledger type is
# therefore never a managed leaf under either clause (F2), mirroring the name != "worklog" skip in
# _gather_active_records.
_LEDGER_TYPES = frozenset(("worklog",))
# The recognized keys of the inert observations object (git-derived facts); an unknown key is a malformed
# injection surfaced fail-closed, never silently dropped (F12).
_OBSERVATION_KEYS = frozenset({"tracked", "actual_remote", "prior"})

# The single-source type -> normative-namespace roster (baseline + every module-tier + importer type), used
# to grade a moved-row `type` against its id namespace (F12) and to bound counters completeness.
_ROSTER_NAMESPACES = dict(BASELINE_TYPES)
for _t, (_ns, _mod) in MODULE_TYPES.items():
    _ROSTER_NAMESPACES[_t] = _ns
_ROSTER_NAMESPACES.update(IMPORTER_TYPES)
# The set of valid roster namespaces (baseline + module-tier + importer), used to validate an injected
# prior snapshot's counter namespaces against the authoritative roster rather than the observation's own
# word (F6; guard-input-soundness).
_ROSTER_NS_SET = frozenset(_ROSTER_NAMESPACES.values())

# A record whose schema has not shipped in the baseline validator is a named CANNOT-EVALUATE deferral,
# never graded INVALID (F4; round-5 F-2). This covers the module tier (schemas land in U2M) AND the
# importer-only legacy_fragment type (deprecated for new stores, spec 8.1: no record schema ships in the
# baseline validator, so records in an existing store stay readable as a named deferral).
_SCHEMA_DEFERRAL = ("record schema not available to the baseline validator; deferred (module-tier schemas "
                    "land in U2M; legacy_fragment is deprecated for new stores and has no baseline "
                    "record schema)")


def _schema_deferred(tname):
    """True when `tname` is an enabled module-tier OR importer type whose record schema has not shipped
    (it is in MODULE_TYPES or IMPORTER_TYPES but has no BASELINE_SPECS entry). Its records are a named
    CANNOT-EVALUATE deferral, not graded INVALID by the baseline-only validator (F4; round-5 F-2 extends
    this from the module tier to the importer legacy_fragment type)."""
    return tname not in BASELINE_SPECS and (tname in MODULE_TYPES or tname in IMPORTER_TYPES)

# The closed spec-11 integrity roster. Every required check calls rep.ran(<id>) once; result() reconciles
# the emitted set against this tuple, so a silently-skipped check becomes CANNOT-EVALUATE, never VALID.
REQUIRED_CHECKS = (
    "C-MANIFEST", "C-PROFILES", "C-ROSTER", "C-RECORDS", "C-PERRECORD-RECONCILE", "C-ARCHIVE-ENUM",
    "C-VERSION-LEDGER", "C-COUNTERS", "C-ROTATION", "C-LEASE", "C-ID-SPACE", "C-RECEIPTS",
    "C-HANDOFF",
    "C-DECISION-CHAINS", "C-LINKS", "C-FROZEN-COVERAGE", "C-NO-DELETION", "C-PARTITION", "C-CONTIGUITY",
    "C-TRACKED", "C-SYNC-AGREE", "C-CONTAINMENT", "C-VIEW-DRIFT", "C-VERSION-FILE", "C-CHANGELOG-GATES",
    "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-HISTORY-RESURRECTION",
)
_REQUIRED_SET = frozenset(REQUIRED_CHECKS)
# Checks a homes-2 store also requires (spec 4.2), inserted after C-ARCHIVE-ENUM. A legacy (homes 1) store's
# roster, and so its report, is exactly REQUIRED_CHECKS.
HOMES2_CHECKS = ("C-EVIDENCE-ENUM",)
# The imported-content checks (C-IMPORTED-*, spec 8.2): the content grading of the imported-series
# leaves, deferred until the spec 1.3.0 activation lands them, so EMPTY today. While it is empty the
# activation tripwire in _check_containment routes any store that declares the imported series under
# supporting tooling (_opf_store.imported_series_declared, at either homes generation) to
# CANNOT-EVALUATE (the leaves would be registered as managed while nothing reads their content), and
# the self-test asserts imported_content_checks_landed below: once SUPPORTED_SPEC_VERSION reaches
# IMPORTED_SPEC_VERSION this roster must be non-empty, NEW ids in the exact imported-check grammar
# (_IMPORTED_ID_RE below), disjoint from REQUIRED_CHECKS and HOMES2_CHECKS, and inside
# required_checks(h) for EVERY homes generation h from 1 through SUPPORTED_HOMES (activating the
# imported series and activating homes 2 are separate gates; today an active series is graded
# against required_checks(1)), so raising that
# constant alone, or with a placeholder roster, cannot ship. At activation these ids join the
# required roster of every generation the way HOMES2_CHECKS joins the homes-2 roster: the wiring
# must place the imported ids inside required_checks(h) for EVERY supported generation while
# they stay disjoint from REQUIRED_CHECKS and HOMES2_CHECKS themselves, so any OTHER wiring (even a
# legitimate-looking alternative such as an imported-keyed roster) reads red, which fails
# closed: a red on a legitimate alternative, never a green on a wiring nothing grades.
# C-IMPORTED-PROVENANCE is already implemented (_check_imported_provenance): the driver runs it exactly
# when the bound roster carries it, so it starts grading when this wiring lands, and not before. It grades
# every series the containment flag admits (_opf_store.imported_series_declared, at either homes
# generation), over every row of each readable imported leaf, a row outside its leaf series included.
IMPORTED_CONTENT_CHECKS = ()
# The exact imported-check id grammar, matched in full: the C-IMPORTED- prefix, then one or more
# runs of upper-case ASCII letters joined by single hyphens. The class is the one every id in
# REQUIRED_CHECKS and HOMES2_CHECKS uses after its C-, and it admits the four imported-series checks
# spec 8.3 names (C-IMPORTED-SCHEMA, C-IMPORTED-IDS, C-IMPORTED-PROVENANCE, C-IMPORTED-SEGREGATION).
# An id carrying whitespace, a lower-case or mixed-case prefix, or any other character is refused;
# it is never stripped or case-folded into a match.
_IMPORTED_ID_RE = re.compile(r"^C-IMPORTED-[A-Z]+(?:-[A-Z]+)*\Z")


def required_checks(homes):
    """The closed required-check roster for a store's active homes generation."""
    if homes < 2:
        return REQUIRED_CHECKS
    at = REQUIRED_CHECKS.index("C-ARCHIVE-ENUM") + 1
    return REQUIRED_CHECKS[:at] + HOMES2_CHECKS + REQUIRED_CHECKS[at:]


def imported_content_checks_landed():
    """The tooling-side imported-series activation gate, asserted by the self-test at the SHIPPED
    constants and exercised there under controlled versions, rosters, and wirings. True while the
    imported series is unsupported, judged through _opf_store.imported_series_supported, the
    SAME support comparison the imported_series_declared gate reads (one shared helper, so the
    gate and this predicate cannot disagree about support): the
    content checks stay deferred, and the runtime tripwire in _check_containment covers a store that
    declares the imported series under supporting tooling while this content-check roster stays empty
    (a populated roster disarms it).
    Once support reaches the threshold, True ONLY when
    IMPORTED_CONTENT_CHECKS is a non-empty roster of NEW ids, each a full match of the exact
    imported-check grammar _IMPORTED_ID_RE (the C-IMPORTED- prefix, then upper-case ASCII letter
    runs joined by single hyphens; an id carrying whitespace, a lower-case or mixed-case prefix, or
    any other character is refused, never stripped or case-folded into a match), disjoint from
    REQUIRED_CHECKS and HOMES2_CHECKS (a placeholder reusing an existing id such as C-CONTAINMENT
    implements no imported-content grading, so reuse never stands in), and inside
    required_checks(h) for EVERY homes generation h from 1 through SUPPORTED_HOMES. Activating the
    imported series (SUPPORTED_SPEC_VERSION) and activating homes 2 (SUPPORTED_HOMES) are SEPARATE
    gates: today an active series is graded against required_checks(1), so ids wired only into the
    homes-2 roster would bind no generation an active store is actually graded against. Raising
    the support constant without landing the checks, or landing a placeholder roster, therefore
    fails the suite through this predicate, and the runtime tripwire never stands alone. This
    predicate grades ROSTER STRUCTURE only -- the ids exist, match the grammar, are NEW, and are
    wired into every generation -- never what a registered check reads: a wired check that grades
    nothing is for review and for the report reconciliation (an id that never runs reads
    CANNOT-EVALUATE), not for this gate. Once the imported series is supported, the
    EVERY-generation bound assumes SUPPORTED_HOMES >= 1: below one supported generation the
    predicate refuses outright (fail closed) instead of reading the empty generation range
    vacuously True. While the series is unsupported the deferral answers first, whatever
    SUPPORTED_HOMES holds."""
    if not _opf_store.imported_series_supported():
        return True
    ids = set(IMPORTED_CONTENT_CHECKS)
    if not ids:
        return False
    if not all(isinstance(cid, str) and _IMPORTED_ID_RE.fullmatch(cid) for cid in ids):
        return False
    if ids & (set(REQUIRED_CHECKS) | set(HOMES2_CHECKS)):
        return False
    # Below one supported generation the all() below is vacuously True: refuse instead.
    if _opf_store.SUPPORTED_HOMES < 1:
        return False
    return all(ids <= set(required_checks(h))
               for h in range(1, _opf_store.SUPPORTED_HOMES + 1))


# --- source-integrity vs deliverable-drift partition (PR-C, the render --write source gate) ---------
# The three DELIVERABLE_DRIFT_CHECKS grade the GENERATED public deliverables (the declared views, the root
# VERSION deliverable, and the curated CHANGELOG.md). `opf render --write` REGENERATES the views and the
# VERSION deliverable, so those checks are its OUTPUT, not its precondition: a mutating render gates on
# SOURCE integrity (the remaining required checks) and then makes the deliverables current. The source set is
# derived by SUBTRACTION, so an unclassified FUTURE required check defaults into the must-pass source set
# (the safe direction; guard-input-soundness), and the partition is drift-gated for exactness/disjointness
# in self_test beside the closed-roster reconciliation.
#
# STANDING RULE (never-advertise; TRUST-change-tracking-ext): C-CHANGELOG-GATES is AUTHORED content render
# does NOT generate, and an UNOWNED C-VERSION-FILE state (a stale VERSION the manifest does not declare as a
# view) is likewise not render's to regenerate or delete. Their adopter-facing messages direct the maintainer
# to edit the authored artefact (or declare the VERSION view / remove the file) and re-run `opf doctor`, and
# must NEVER gain an `opf render --write` remedy hint.
DELIVERABLE_DRIFT_CHECKS = frozenset({"C-VIEW-DRIFT", "C-VERSION-FILE", "C-CHANGELOG-GATES"})
SOURCE_INTEGRITY_CHECKS = _REQUIRED_SET - DELIVERABLE_DRIFT_CHECKS


def source_integrity_ok(result, accepted=None):
    """True IFF every SOURCE_INTEGRITY_CHECK graded EXACTLY "PASS" and the report carries no unattributed or
    internal fault. This is the predicate render()'s --write SOURCE gate calls: it EXCLUDES the deliverable-
    drift checks (which render regenerates) while failing CLOSED on a source check that is FINDING,
    CANNOT-EVALUATE, or never ran (result() force-appends CANNOT-EVALUATE for a skipped required check), and
    on ANY internal/unattributed fault (a duplicate ran(), an unknown check id, or a finding/cant emitted
    with no current check on an early-return path). Without `accepted` the predicate reads the per-check
    verdict MAP and the `unattributed` list, never a message, so the guard's input can genuinely answer the
    question asked of it (guard-input-soundness). An empty/malformed result reads as not-ok (fail-closed).

    `accepted` (default None: no exception) is a predicate over CANNOT-EVALUATE messages that the CALLER has
    verified independently: `opf record` passes one naming exactly the transition it just checked with the
    known actor (spec 8.8). A source check graded CANNOT-EVALUATE then counts as passing only when it
    carries at least one message, every message is a cannot-evaluate (never a finding), and each satisfies
    `accepted`. A FINDING, a never-run check, and an unattributed fault still fail."""
    if getattr(result, "unattributed", None):
        return False
    checks = getattr(result, "checks", None) or {}
    cannot = frozenset(getattr(result, "cannot_evaluate", None) or ())
    by_check = getattr(result, "by_check", None) or {}

    def passing(cid):
        if checks.get(cid) == "PASS":
            return True
        if accepted is None or checks.get(cid) != "CANNOT-EVALUATE":
            return False
        msgs = by_check.get(cid) or []
        return bool(msgs) and all(m in cannot and accepted(m) for m in msgs)

    return all(passing(cid) for cid in source_checks(result))


def source_checks(result):
    """The SOURCE_INTEGRITY checks a result is gated on, in roster order: SOURCE_INTEGRITY_CHECKS, plus the
    homes-2 checks when the report carries one. result() grades every check its homes roster requires, so a
    homes-2 report always carries them, and a legacy report never does."""
    checks = getattr(result, "checks", None) or {}
    homes = 2 if any(cid in checks for cid in HOMES2_CHECKS) else 1
    return tuple(cid for cid in required_checks(homes) if cid not in DELIVERABLE_DRIFT_CHECKS)


# Disclosed by-design residuals (OPF-SPEC 17): the sub-checks a parse-only, read-only whole-store engine
# cannot soundly reach from its inputs, or that another layer owns. They are reported alongside every result
# and never fold into the gradeable status. The membership test is that no residual may ever let a broken
# store certify VALID; every stored byte is graded at steady state (an import in progress surfaces the
# active run's interior to the partial-import triage set instead), so none of these can hide a defect.
_RESIDUALS = (
    "Backup, access, and hosting discipline of the tracking repository (spec 17): the tracked-store check "
    "verifies version control, not the repository's durability; the local-only pattern places durability "
    "wholly on the adopter's recorded backup.",
    "The single-writer lease propagation window (spec 5.7/17): the overlapping divergence check that "
    "catches a two-system collision after the fact is the operation layer's, not this parse-only engine's.",
    "A host provider's behaviour beyond the named-host egress bound (spec 17).",
    "Product-repo review-flow quality for public deliverables (spec 5.8/17): required to exist, not gated "
    "here; changelog coverage and freeze grade, prose quality does not.",
    "A second store no pointer names (spec 17): resolution is aimed by the pointer, so an unnamed store "
    "placed elsewhere is undiscoverable.",
    "Dedicated-sync-target freshness (behind / ahead / divergence, spec 5.7): needs a fetch a parse-only, "
    "read-only validator must not perform; it is the operation layer's consistency contract. No broken "
    "store state hides behind it, because every stored byte is graded at steady state.",
    "Declared-but-unsupported profiles (spec 16): named in unevaluated_profiles, enforced only by a "
    "profile-aware tool.",
    "Module-tier record schema validation (spec 8.5): the baseline record validator knows only the "
    "baseline specs, so a module-tier record is a named CANNOT-EVALUATE deferral (deferred to U2M) rather "
    "than graded here; every such record is still surfaced, so none can hide a defect.",
    "Byte-level view drift for a per-record store that declares views (spec 5.8/10): U4's view planner "
    "does not yet support the per-record layout, so C-VIEW-DRIFT is a named CANNOT-EVALUATE there, never "
    "a silent pass.",
    "An unregistered path at the store location while import_status == 'partial' (spec 14.2): its stray "
    "bytes are surfaced to the partial-import triage set rather than graded (store-wide, matching spec "
    "14.2's 'at the store location'), since a migration in progress legitimately holds not-yet-reconciled "
    "paths; at steady state every such byte is graded.",
    "Per-record rotation period-correspondence (spec 12): a non-worklog archived record carries no "
    "worklog-span or release field in its envelope, so rotation is bound per BUCKET (the bucket carries a "
    "released worklog span) rather than per record to its own resolution period; this is the maximal "
    "binding the data model supports. No broken store hides behind it: a bucket with moved records but no "
    "span is a finding, and every archived worklog span must fall within a released range.",
    "Immutable-record body preservation across time (spec 8.5) when the prior committed snapshot carries "
    "no body digest for a created-terminal record: it is a named CANNOT-EVALUATE (never a silent VALID on "
    "a rewritten immutable body), verified only when the prior supplies the digest (codex-3).",
)
# Disclosed only on a homes-2 report, so a legacy report's residuals are unchanged.
_HOMES2_RESIDUALS = (
    "Journal contents are not inspected by doctor. A staged plan does not prove recoverability. "
    "Homes-2 evidence inventories establish local membership, not actor authenticity; losing a whole "
    "bundle, inventory and payload together, requires independent history to detect.",
)


class StoreValidation:
    """The whole-store outcome (the U1 ManifestValidation idiom, one level up). `status` reduces to a
    0/1/2 exit code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE); CANNOT-EVALUATE dominates INVALID, which
    dominates VALID, so an unreadable input is never masked by an otherwise-clean read. `checks` is the
    ordered per-check verdict map; `triage` is the spec-11/14.2 partial-import surface; `evaluated_profiles`
    and `unevaluated_profiles` name the profile scope (spec 16); `residuals` are the disclosed by-design
    uncovered sub-checks, reported but never changing the status."""
    __slots__ = ("status", "findings", "cannot_evaluate", "residuals", "checks", "triage",
                 "evaluated_profiles", "unevaluated_profiles", "by_check", "unattributed")

    def __init__(self, status, findings=None, cannot_evaluate=None, residuals=None, checks=None,
                 triage=None, evaluated_profiles=None, unevaluated_profiles=None,
                 by_check=None, unattributed=None):
        self.status = status
        self.findings = findings or []
        self.cannot_evaluate = cannot_evaluate or []
        self.residuals = residuals or []
        self.checks = checks or {}
        self.triage = triage or []
        self.evaluated_profiles = evaluated_profiles or []
        self.unevaluated_profiles = unevaluated_profiles or []
        # PR-C ADDITIVE attribution (no existing field, verdict, status, or roster behaviour changes):
        # `by_check` maps a check id to the source-attributed message strings it emitted (so a caller can
        # print EXACTLY the messages a given check owns, no prefix parsing); `unattributed` collects any
        # message with no current check plus the duplicate-ran / unknown-id internal faults, so a caller can
        # fail closed on an internal fault the per-check map does not carry.
        self.by_check = by_check or {}
        self.unattributed = unattributed or []


def exit_code(result):
    """Reduce a StoreValidation to a 0/1/2 exit code for the future doctor verb. Only the exact VALID
    status yields 0; any unrecognized/malformed status fails CLOSED to 2, never a two-valued fall-through
    reading a bad status as success (ROUND-2 codex-2 s1; guard-input-soundness)."""
    if result.status == VALID:
        return 0
    if result.status == INVALID:
        return 1
    return 2


class _Report:
    """A fail-closed accumulator plus the check-routing registry. `ran` registers a required check and makes
    it the current attribution target; `finding` / `cant` record a defect / a cannot-evaluate and attribute
    it to the current check. `result` reconciles the emitted check set against REQUIRED_CHECKS: a required
    check that never registered `ran` is routed to CANNOT-EVALUATE naming it, so a silently-skipped check is
    never a pass."""
    __slots__ = ("findings", "cannot", "residuals", "checks", "triage", "_current", "by_check",
                 "unattributed", "required")

    def __init__(self):
        self.findings = []
        self.cannot = []
        self.residuals = list(_RESIDUALS)
        self.checks = {}                  # insertion-ordered check-id -> "PASS" / "FINDING" / "CANNOT-EVALUATE"
        self.triage = []
        self._current = None
        self.by_check = {}                # check-id -> list of the source-attributed message strings it emitted
        self.unattributed = []            # no-current findings/cants + duplicate-ran / unknown-id internal faults
        self.required = REQUIRED_CHECKS   # the closed roster; a homes-2 store widens it via require_homes

    def require_homes(self, homes):
        # Bind the roster to the store's homes generation once the manifest names it. A homes-2 check that
        # then never runs is CANNOT-EVALUATE, and a legacy report keeps exactly REQUIRED_CHECKS.
        self.required = required_checks(homes)
        if homes >= 2:
            self.residuals.extend(_HOMES2_RESIDUALS)

    def ran(self, check_id):
        # Register a required check as executed and make it the attribution target. A second ran() for the
        # same id is an internal fault (a check double-counted), fail-closed: it is recorded as a report-wide
        # cannot AND as an `unattributed` entry so source_integrity_ok refuses it (the verdict in the map is
        # deliberately left unchanged, so this fault is invisible to a bare per-check scan without it).
        if check_id in self.checks:
            msg = "internal: check {!r} was run more than once".format(check_id)
            self.cannot.append(msg)
            self.unattributed.append(msg)
        else:
            self.checks[check_id] = "PASS"
        self._current = check_id

    def finding(self, msg):
        self.findings.append(msg)
        self._attribute("FINDING", msg)

    def cant(self, msg):
        self.cannot.append(msg)
        self._attribute("CANNOT-EVALUATE", msg)

    def triage_path(self, msg):
        self.triage.append(msg)

    def _attribute(self, status, msg):
        # Attribute the verdict AND the message to the current check. A finding/cant emitted with NO current
        # check (an early-return path that cant()s before any ran(), e.g. the obs_err or unresolved-store
        # paths) is UNATTRIBUTED: recorded in `unattributed` so source_integrity_ok fails closed on it,
        # since no per-check entry would otherwise carry it.
        cid = self._current
        if cid is None:
            self.unattributed.append(msg)
            return
        self.by_check.setdefault(cid, []).append(msg)
        cur = self.checks.get(cid)
        if cur is None or status == "CANNOT-EVALUATE":
            self.checks[cid] = status
        elif cur == "PASS":
            self.checks[cid] = "FINDING"

    def result(self, evaluated_profiles=None, unevaluated_profiles=None):
        # Reconcile the emitted check ids against the closed REQUIRED_CHECKS roster. A required check that
        # never registered ran() is routed to CANNOT-EVALUATE naming it; an unknown id that somehow ran is a
        # fail-closed internal fault. Neither can read as a pass.
        for cid in self.required:
            if cid not in self.checks:
                msg = ("internal: required check {!r} did not run; routed to CANNOT-EVALUATE "
                       "(a skipped check is never a pass)".format(cid))
                self.cannot.append(msg)
                self.by_check.setdefault(cid, []).append(msg)   # so the source gate can print the reason
                self.checks[cid] = "CANNOT-EVALUATE"
        required = frozenset(self.required)
        for cid in list(self.checks):
            if cid not in required:
                msg = ("internal: an unknown check id {!r} was run (not in REQUIRED_CHECKS; "
                       "fail-closed)".format(cid))
                self.cannot.append(msg)
                self.unattributed.append(msg)                   # an unknown id has no roster verdict to carry it
        ordered = {cid: self.checks[cid] for cid in self.required}
        for cid in self.checks:
            if cid not in ordered:
                ordered[cid] = self.checks[cid]
        if self.cannot:
            status = CANNOT_EVALUATE
        elif self.findings:
            status = INVALID
        else:
            status = VALID
        return StoreValidation(status, self.findings, self.cannot, self.residuals, ordered, self.triage,
                               sorted(evaluated_profiles or []), sorted(unevaluated_profiles or []),
                               by_check=self.by_check, unattributed=self.unattributed)


# --- a lightweight record descriptor for the cross-record checks -------------------------------------

class _Rec:
    __slots__ = ("id", "rtype", "namespace", "state", "qual", "links", "actor_kind", "location", "scopes",
                 "body")

    def __init__(self, rid, rtype, namespace, state, qual, links, actor_kind, location, scopes, body=None):
        self.id = rid                 # the raw id value (a str for a well-formed record)
        self.rtype = rtype            # the record's type name
        self.namespace = namespace    # the two-letter namespace, or None when the id is malformed
        self.state = state            # the parsed status state (or "recorded" for a worklog entry)
        self.qual = qual              # the status qualifier ("proposed") or None
        self.links = links            # list of (rel, target-id) tuples with a string rel
        self.actor_kind = actor_kind  # the actor.kind string, or None
        self.location = location      # "active" or "archive/<YYYY>"
        self.scopes = scopes          # a block's scoped ids (list), or [] (spec 8.5)
        self.body = body              # the raw record table (immutable-body preservation check; codex-3)


def _make_rec(table, rtype, location):
    """Build a best-effort _Rec from a parsed record table. A malformed id yields namespace None (the
    cross-record checks skip it); the per-record schema faults are reported separately by validate_record."""
    rid = table.get("id")
    shape = _valid_id_shape(rid)
    namespace = shape[0] if shape is not None else None
    spec = BASELINE_SPECS.get(rtype)
    if rtype == "worklog":
        state, qual = "recorded", None            # the reduced envelope carries no status (spec 8.3)
    else:
        state = qual = None
        status = table.get("status")
        if spec is not None and isinstance(status, str):
            parsed, _err = parse_status(status, spec)
            if parsed is not None:
                state, qual = parsed
    links = []
    lk = table.get("links")
    if isinstance(lk, list):
        for l in lk:
            if isinstance(l, dict) and isinstance(l.get("rel"), str):
                links.append((l.get("rel"), l.get("id")))
    actor = table.get("actor")
    akind = actor.get("kind") if isinstance(actor, dict) and isinstance(actor.get("kind"), str) else None
    scopes = table.get("scopes")
    scopes = list(scopes) if isinstance(scopes, list) else []
    return _Rec(rid, rtype, namespace, state, qual, links, akind, location, scopes, table)


def _status_string(state, qual):
    """Reconstruct a status token from a parsed (state, qualifier), or None when the state is unparseable."""
    if state is None:
        return None
    return "{}/{}".format(state, qual) if qual else state


# --- contained reads and listings (U1's no-follow discipline) ----------------------------------------

def _rel(*parts):
    """Join store-relative path components with '/'. Every component is a fixed name or a value already
    range/shape-checked by the caller; the result is read no-follow beneath the store root fd."""
    return "/".join(parts)


def _read_toml(root_fd, relpath, rep):
    """Read a contained TOML file beneath root_fd. Returns (data, "ok"|"absent"|"error"); on a StoreError
    (unreadable, unparseable, a refused symlink) it records a CANNOT-EVALUATE naming the input and returns
    (None, "error"). Absence is (None, "absent") and left to the caller to grade."""
    try:
        data = _read_toml_contained(root_fd, relpath)
    except StoreError as exc:
        rep.cant("cannot read {}: {}".format(relpath, exc))
        return None, "error"
    except _journal.JournalError as exc:
        # _read_toml_contained runs its lstat step OUTSIDE its StoreError wrapper, so a JournalError from
        # _check_rel (a '.'/'..'/control-char/trailing-slash relpath) or a non-ENOENT stat surfaces here
        # rather than escaping validate_store. Mirrors U7's _read_toml guard (guard-input-soundness; B5).
        rep.cant("cannot read {} ({})".format(relpath, exc))
        return None, "error"
    if data is None:
        return None, "absent"
    return data, "ok"


def _read_bytes(fd, relpath, rep):
    """Read a contained regular file's raw bytes beneath fd, no-follow. Returns (bytes, "ok"),
    (None, "absent"), or (None, "error") with a CANNOT-EVALUATE already recorded. Used for the public
    deliverables (VERSION, CHANGELOG.md) and view targets, which are not TOML."""
    try:
        st = _journal._lstat_contained(fd, relpath)
    except _journal.JournalError as exc:
        rep.cant("cannot stat {} ({})".format(relpath, exc))
        return None, "error"
    if st is None:
        return None, "absent"
    if not stat.S_ISREG(st.st_mode):
        # Refuse a FIFO/device/socket/dir BEFORE _read_contained opens it: a FIFO O_RDONLY with no writer
        # blocks forever (B4; check-fails-closed-on-unreadable, SECA resource-bounds).
        rep.cant("cannot read {} (present but not a regular file; an exotic entry, never opened)".format(
            relpath))
        return None, "error"
    try:
        raw, _st = _journal._read_contained(fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        rep.cant("cannot read {} ({})".format(relpath, exc))
        return None, "error"
    return raw, "ok"


def _close_fd_quietly(fd):
    """Close a descriptor on a cleanup / teardown path, swallowing an OSError so a close that raises
    (EINTR / EIO / EBADF) during teardown cannot crash the validator. The store verdict is already computed
    (or an exception is already in flight) by the time these closes run, so a cleanup-close irregularity is
    never itself the store's verdict (fail-closed teardown; S1-F1 / S4-F3).

    Single close (P1, #378): the descriptor is closed with exactly ONE os.close. If it raises, the number
    counts as released and is never touched again: no fstat or fcntl probe, no second close. close(2) on
    Linux "always releases the file descriptor early in the close operation, freeing it for reuse", and
    retrying "is the wrong thing to do, since this may cause a reused file descriptor from another thread to
    be closed" (man 2 close, "Dealing with error returns from close()"); an fstat that finds the number
    open after a failed close is looking at whatever reused it, so a probe-then-reclose recovery closed
    another owner's descriptor. The error is swallowed silently. _journal._close_fd_quietly carries the same
    body. Residual (disclosed, #377): a platform that keeps the descriptor on a failed close (HP-UX, and
    POSIX.1-2024 as close(2) reports it) leaks it until exit; Linux does not, and macOS is assumed not to."""
    try:
        os.close(fd)
    except OSError:
        pass                                              # released either way (close(2)); never re-touched


def _list_contained(root_fd, reldir):
    """The immediate real subdirectory and regular-file names of `reldir` beneath root_fd, listed
    no-follow. Returns (subdirs, files) sorted, or (None, None) when `reldir` is absent. Raises StoreError
    (fail-closed) when `reldir` is present but not a directory, is a refused symlink, or contains an exotic
    entry (neither a directory nor a regular file), so a swapped or exotic store entry is never silently
    skipped. Mirrors _opf_store._immediate_subdirs, extended to also collect regular files."""
    try:
        pfd, name = _journal._open_parent(root_fd, reldir)
    except FileNotFoundError:
        return None, None
    except (OSError, _journal.JournalError) as exc:
        raise StoreError("cannot open the parent of {} ({})".format(reldir, exc))
    try:
        try:
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            return None, None
        except NotADirectoryError as exc:
            raise StoreError("{} is present but is not a directory ({})".format(reldir, exc))
        except OSError as exc:
            raise StoreError("cannot open {} no-follow ({})".format(reldir, exc))
        try:
            subdirs, files = [], []
            try:
                entries = sorted(os.listdir(dfd))
            except OSError as exc:
                # An I/O error listing the opened directory (EIO, ENOMEM, EBADF from an exotic fd) is a
                # fail-closed StoreError naming the directory, never a raw OSError escaping the barrier
                # (S1-F1); it routes through _list_dir to a named CANNOT-EVALUATE like the os.stat case.
                raise StoreError("cannot list {} no-follow ({})".format(reldir, exc))
            for entry in entries:
                try:
                    est = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
                except OSError as exc:
                    raise StoreError("cannot stat {}/{} ({})".format(reldir, entry, exc))
                if stat.S_ISDIR(est.st_mode):
                    subdirs.append(entry)
                elif stat.S_ISREG(est.st_mode):
                    files.append(entry)
                else:
                    raise StoreError("{}/{} is neither a directory nor a regular file (exotic store "
                                     "entry; fail-closed)".format(reldir, entry))
            return subdirs, files
        finally:
            _close_fd_quietly(dfd)
    finally:
        _close_fd_quietly(pfd)


def _list_dir(root_fd, reldir, rep):
    """_list_contained with StoreError mapped to a CANNOT-EVALUATE naming the directory. A directory or
    entry name can carry control characters, so both the path and the error are rendered through
    _safe_display rather than interpolated raw (F13)."""
    try:
        return _list_contained(root_fd, reldir)
    except StoreError as exc:
        rep.cant("cannot list {}: {}".format(_safe_display(reldir), _safe_display(str(exc))))
        return None, None


def _record_digest(body):
    """The per-record digest `sha256:<hex>` over U8's canonical serialization of a record body (the digest
    the per-record registry row carries; OPF-SPEC 13:999-1001). Reuses `_opf_emit.emit`, which admits the
    full record subset INCLUDING a native TOML date/datetime/time under a registered `x-<vendor>` table
    (spec 8.7); U3's `_canonical` rejects those, so using it here over-fired to CANNOT-EVALUATE on a valid
    store (F11). Raises `_opf_emit.EmitError` on an out-of-subset body, which the caller maps to
    CANNOT-EVALUATE, never a silent mismatch."""
    return "sha256:" + hashlib.sha256(_opf_emit.emit(body).encode("utf-8")).hexdigest()


def _under_any(p, prefixes):
    """True when path `p` equals, or lies beneath, any store-relative prefix in `prefixes`."""
    for u in prefixes:
        if p == u or p.startswith(u + "/"):
            return True
    return False


def _canonical_contained(p):
    """The canonical store-relative form of a CONTAINED path (one _is_contained_relpath admits): empty and
    '.' components dropped and lexical '..' resolved, so a non-canonical but contained [unmanaged]
    declaration (a trailing slash, a './' or '//' form, or a lexical '..') matches the canonical paths the
    walk produces rather than covering nothing and over-firing its legacy content as unregistered (F4). The
    caller confirms containment first, so no component can pop above the root; the reduction cannot escape."""
    out = []
    for comp in p.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            if out:
                out.pop()
        else:
            out.append(comp)
    return "/".join(out)


def _bracketed_host_ok(tok):
    """True only when `tok` is a well-formed bracketed host `[addr]` (RFC 3986 host): the token is
    bracketed and its interior parses as an IPv6 literal. Brackets in a URI/scp host delimit an IPv6
    address SPECIFICALLY (RFC 3986 IP-literal); an IPv4 dotted-quad is never bracketed, so `[127.0.0.1]`
    is a malformed authority, not a resolvable host, and must canonicalize to nothing rather than a
    spurious ('[127.0.0.1]', path) pair that could falsely satisfy C-SYNC-AGREE (codex round-12 F1:
    `ipaddress.ip_address()` accepting an IPv4 inside brackets was the structural bug; require IPv6 for
    the bracket form). A malformed interior (`[nonsense]`, `[2001/db8::1]`) is likewise unresolvable
    (codex round-6; guard-input-soundness: a membership question about a host is answered by parsing the
    address as its bracket form promises, not by matching the bracket tokens)."""
    if not (tok.startswith("[") and tok.endswith("]")):
        return False
    inner = tok[1:-1]
    if not inner:
        return False
    try:
        ipaddress.IPv6Address(inner)     # the bracket form is an IPv6 literal ONLY; an IPv4 in brackets is rejected
    except ValueError:                   # AddressValueError subclasses ValueError (IPv4/junk interior -> unresolvable)
        return False
    return True


def _dns_or_ipv4_ok(name):
    """True only when an UNBRACKETED host `name` (port and brackets already handled by the caller) is a
    STRUCTURALLY valid bare IPv4 literal or DNS name. The character allowlist (_HOST_AUTHORITY_ALLOWED)
    bounds the code points; this bounds the STRUCTURE, so a host that is character-clean but structurally
    malformed (an empty DNS label from `..`, a label over 63 characters, an over-long name) is
    UNRESOLVABLE -> the caller returns None -> CANNOT-EVALUATE, never a spurious host that could falsely
    satisfy C-SYNC-AGREE even when the manifest target and observed remote strings match (codex round-12
    F1). This COMPLETES the host class the allowlist began: chars (character set) + structure (this) =
    the whole host surface, so a newly-hostile structural class is refused by a positive grammar rather
    than a fresh negative clause. A bare IPv4 dotted-quad is a valid host as-is; any other form is
    validated as a DNS name per RFC 1035: total length <= 253, no leading or trailing dot, and every
    label non-empty and <= 63 characters (so `a..b` yields an empty label and is rejected). The caller
    has already stripped any `:port` and excluded the bracketed IPv6 form, so `name` here carries neither
    a port nor brackets.

    The DNS-name structure enforces the RFC 1035/1123 LDH ("letters-digits-hyphen") shape the allowlist's
    character set cannot by itself: every label is non-empty and <= 63 characters, AND no label begins or
    ends with a HYPHEN (`-lead`, `trail-`, a hyphen-edge interior label). The FINAL (top-level) label is
    never ALL-NUMERIC: such a name is neither a valid bare IPv4 literal (handled above) nor a resolvable
    hostname, which is also how an IPv4-LOOKALIKE is refused here. An out-of-range octet (`256.0.113.5`) or a
    leading-zero octet (`203.0.113.05`) makes IPv4Address raise, so the string falls through to the DNS path
    as a dotted run of numeric labels; its all-numeric final label is rejected, so it is accepted neither as
    a bare IPv4 nor as a DNS name, never a spurious host that could falsely satisfy C-SYNC-AGREE (round-15
    F3, completing the host structural class the round-12 fix began)."""
    try:
        ipaddress.IPv4Address(name)      # a bare IPv4 dotted-quad is a valid host as-is
        return True
    except ValueError:
        pass
    if not name or len(name) > 253:      # RFC 1035 total-name ceiling; an empty name is not a host
        return False
    if name.startswith(".") or name.endswith("."):   # no leading/trailing dot (an empty first/last label)
        return False
    labels = name.split(".")
    for label in labels:
        if not label or len(label) > 63:  # every DNS label is non-empty (rejects `..`) and <= 63 characters
            return False
        if label.startswith("-") or label.endswith("-"):   # LDH: no label begins or ends with a hyphen
            return False
    # The final (top-level) label is never all-numeric: a bare all-numeric name is neither a valid IPv4
    # literal (handled above) nor a resolvable hostname, and this is how an IPv4-lookalike with an
    # out-of-range or leading-zero octet (which IPv4Address rejects) is refused rather than admitted as a
    # DNS name. isascii() pairs with isdigit() so only ASCII 0-9 count (the allowlist already bounds the
    # character set; this mirrors the port-parse idiom and never treats a Unicode digit as numeric).
    if labels[-1].isascii() and labels[-1].isdigit():
        return False
    return True


# The ONLY characters a git remote host authority (host[:port], userinfo already stripped) may carry:
# DNS/IPv4 hostname characters [A-Za-z0-9.-], the ':' that separates a port (and appears inside an IPv6
# literal), and the '[' ']' that bracket an IPv6 literal (its hex digits and '.' are already in the DNS
# set). This is an ALLOWLIST: any other code point is rejected by default.
_HOST_AUTHORITY_ALLOWED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-:[]")


def _host_authority_ok(authority):
    """The `host[:port]` authority (userinfo already stripped) carries ONLY characters from the host
    ALLOWLIST (_HOST_AUTHORITY_ALLOWED): DNS/IPv4 hostname characters, the port/IPv6 ':', and the IPv6
    brackets. ANY other character -- whitespace, an ASCII control or DEL, a Unicode FORMAT/zero-width/bidi
    character (category Cf, e.g. U+200B ZERO WIDTH SPACE or U+202E RIGHT-TO-LEFT OVERRIDE), or any other
    non-ASCII code point -- is never valid in a host or a port, so a remote carrying one is a forged or
    mis-parsed authority and must be UNRESOLVABLE (the caller returns None -> CANNOT-EVALUATE) rather than a
    spurious host that could falsely satisfy C-SYNC-AGREE even when the manifest target and observed remote
    strings match. This ALLOWLIST replaces the earlier whitespace/control BLOCKLIST, which admitted every
    character it did not enumerate and so had to be widened at each new hostile class (port/bracket ->
    whitespace/control -> Cf/zero-width/bidi: the host-validation whack-a-mole, round-10 F1). An allowlist
    structurally ends it: a newly-hostile character class is outside the allowlist by default rather than
    needing a fresh blocklist clause (guard-input-soundness: the WHOLE host token is validated by a positive
    grammar, not a growing set of negatives). The bracket-BALANCE, IPv6-literal parse (_bracketed_host_ok),
    and port-RANGE checks stay in _canonical_remote: this bounds only the character set; the structure is
    still validated there. An empty authority carries no character (all() is vacuously True) and is handled
    by the caller's own emptiness guard (host falsiness), unchanged."""
    return all(ch in _HOST_AUTHORITY_ALLOWED for ch in authority)


def _canonical_remote(url):
    """Canonicalize a git remote URL to a (host, path) pair for host+path equivalence (spec 5.5/5.6),
    covering https/http/ssh/git scheme URLs and scp-style git@host:path, with or without a trailing
    '.git'. The scheme-URL host RETAINS its port (host:port is part of the endpoint identity, so
    ssh://h:2222/p and ssh://h:2223/p are distinct; codex-4); a scp-style form carries no port. Returns
    None when the string is not a git remote form the equivalence can canonicalize; such a residual form is
    disclosed rather than silently matched (disclose-guard-residuals; F8)."""
    if not isinstance(url, str):
        return None
    s = url.strip()
    if not s:
        return None
    if "://" in s:
        scheme, rest = s.split("://", 1)
        if scheme.lower() not in ("https", "http", "ssh", "git"):
            return None
        if "/" not in rest:
            return None
        authority, path = rest.split("/", 1)
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        # Reject a whitespace/control character anywhere in the host:port authority: it is never a valid
        # host and marks a forged/mis-parsed remote, unresolvable rather than a spurious host (codex round-8).
        if not _host_authority_ok(authority):
            return None
        host = authority     # KEEP the port: host:port is part of the endpoint identity (codex-4),
        # EXCEPT a port that spells the scheme's DEFAULT names the same endpoint as the port-less form
        # (spec 5.6 host+path equivalence): https://h:443/p, ssh://h:22/p and github:org/repo are one
        # endpoint. A NON-default port (2222 vs 2223) stays distinct (F3); match only a trailing numeric
        # default so a bracketed IPv6 host is never mis-split.
        # An UNBRACKETED multi-colon authority is an IPv6 literal that MUST be bracketed
        # (ssh://[2001:db8::22]/p); unbracketed it is unresolvable rather than mis-read with a trailing
        # '::22' consumed as a default port (F5b).
        _default_port = {"https": "443", "http": "80", "ssh": "22", "git": "9418"}.get(scheme.lower())
        if host.startswith("["):
            # A bracketed IPv6 literal: a missing ']', an EMPTY '[]' authority, or junk before the ':' is
            # unresolvable (ROUND-2 codex-2 / Fable F3), never a mis-canonicalized ('[::1', path) or ('[]',
            # path) pair that could falsely agree. '[' is at index 0, so rb == 1 means empty brackets.
            rb = host.find("]")
            if rb <= 1 or host[rb + 1:rb + 2] not in ("", ":"):
                return None
            hostpart, portsep, hport = host[:rb + 1], host[rb + 1:rb + 2], host[rb + 2:]
            # The bracketed interior must be a well-formed IP literal; '[nonsense]' is unresolvable
            # rather than a spurious host that could falsely agree (codex round-6).
            if not _bracketed_host_ok(hostpart):
                return None
        else:
            # A STRAY bracket in an UNBRACKETED authority (`ssh://host]/p`) is not a valid host: the only
            # legitimate brackets delimit an IPv6 literal and are handled by the startswith('[') branch above,
            # so any '[' or ']' here is an unbalanced/stray bracket, unresolvable rather than a spurious
            # 'host]' endpoint that could falsely agree (codex round-8; whole-host-token validation).
            if "[" in host or "]" in host:
                return None
            # An UNBRACKETED multi-colon authority is an IPv6 literal that MUST be bracketed; unbracketed it
            # is unresolvable rather than mis-read with a trailing '::22' consumed as a port (F5b).
            if host.count(":") > 1:
                return None
            hostpart, portsep, hport = host.rpartition(":")
            # STRUCTURAL host validation (codex round-12 F1): the bare host must be a well-formed bare IPv4
            # literal or DNS name; a character-clean but structurally malformed host (`a..b`, a label over 63
            # chars, an over-long name) is unresolvable rather than a spurious pair that could falsely satisfy
            # C-SYNC-AGREE. When no ':' is present rpartition leaves the whole host in `hport`, so the bare
            # host is `hostpart` only when a port separator was found; the port itself is range-checked below.
            if not _dns_or_ipv4_ok(hostpart if portsep else hport):
                return None
        # A port is a run of ASCII digits: a default port (incl. a leading-zero spelling, ':022' == ':22')
        # names the port-less endpoint and is dropped; a non-default numeric port is re-emitted without
        # leading zeros so its spelling cannot cause a false disagreement (ROUND-2 codex-4). A colon whose
        # tail is not ASCII digits (a bracket fragment, or hostile Unicode digits int() would reject with a
        # ValueError) is not a port: unresolvable rather than a crash escaping the barrier (ROUND-2 codex-2).
        if portsep:
            if not (hostpart and hport.isascii() and hport.isdigit()):
                return None
            try:
                port_i = int(hport)
            except ValueError:
                # An all-ASCII-digit run long enough to trip CPython's int-conversion digit limit (>4300
                # digits) is not a real port: unresolvable rather than a ValueError escaping the barrier
                # (ROUND-2 codex-2, the LENGTH sibling of the Unicode-digit case the isdigit guard closes).
                return None
            # A TCP port is 1..65535; an out-of-range run of digits ('0', '65536', '99999999') is not a
            # real port, so it is unresolvable rather than a spurious 'host:65536' endpoint that could
            # falsely agree (codex round-6; a range membership answered by a bounds test, not the tokens).
            if not (1 <= port_i <= 65535):
                return None
            if _default_port is not None and port_i == int(_default_port):
                host = hostpart
            else:
                host = "{}:{}".format(hostpart, port_i)
    else:
        # scp-style [user@]host:path: the colon before any slash separates host from path. A bracketed
        # IPv6 host ([addr]) carries colons INSIDE the brackets that are part of the address, not the
        # separator, so the separator is the colon immediately after the closing bracket; a malformed
        # bracket or a missing host:path colon is unresolvable (returns None, disclosed; gemini round-7).
        # The userinfo boundary is found from the FRONT: the host '[' sits at the start or immediately
        # after the userinfo '@'. The PATH may itself contain '@' (git@[addr]:path@x), so rfind would land
        # in the path and skip the bracket branch, mis-splitting on a colon inside the address (F5a).
        lb = s.find("[")
        if lb != -1 and (lb == 0 or s[lb - 1] == "@"):
            rb = s.find("]", lb)
            # A missing ']', an EMPTY '[]' authority (rb == lb + 1), or a bracket not immediately followed
            # by the host:path colon is unresolvable rather than a mis-split ('[]', path) pair (Fable F3).
            if rb == -1 or rb == lb + 1 or s[rb + 1:rb + 2] != ":":
                return None
            colon = rb + 1
        else:
            colon = s.find(":")
        slash = s.find("/")
        if colon == -1 or (slash != -1 and slash < colon):
            return None
        authority, path = s[:colon], s[colon + 1:]
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        host = authority
        # Reject a whitespace/control character anywhere in the host authority: never a valid host, and the
        # scp-style sibling of the scheme-URL check above (codex round-8; whole-host-token validation).
        if not _host_authority_ok(host):
            return None
        # A scp-style bracketed host carries no port, so the whole authority is the '[addr]' token;
        # its interior must be a well-formed IP literal. 'git@[nonsense]:p' is unresolvable rather than
        # a spurious host (codex round-6; class sibling of the scheme-URL bracket check above).
        if host.startswith("["):
            if not _bracketed_host_ok(host):
                return None
        else:
            # A STRAY bracket in an UNBRACKETED scp host ('git@host]:p') is unbalanced and not a valid host:
            # unresolvable rather than a spurious 'host]' endpoint (codex round-8, scp sibling of the
            # scheme-URL stray-bracket check).
            if "[" in host or "]" in host:
                return None
            # STRUCTURAL host validation (codex round-12 F1, scp sibling of the scheme-URL check): an scp host
            # carries no port (the ':' is the host:path separator), so the whole authority is the bare host;
            # it must be a well-formed bare IPv4 literal or DNS name, else unresolvable -> CANNOT-EVALUATE.
            if not _dns_or_ipv4_ok(host):
                return None
    if not host:
        return None
    p = path.strip("/")
    if p.endswith(".git"):
        p = p[:-len(".git")]
    if not p:
        return None
    return (host.lower(), p)


def _target_remote_canon(target):
    """The canonical (host, path) forms a REMOTE target (spec 5.5) names, for host+path agreement against
    an observed remote. Returns (canon_set, unresolved): `canon_set` is the set of canonical forms (empty
    when a remote target names no canonicalizable form), and `unresolved` lists the raw target forms the
    equivalence could not canonicalize (disclosed, never silently matched; F8). Returns (None, []) for a
    LOCAL (dir:/path) target that names no git remote."""
    if target.kind in ("github", "gitlab"):
        host = "github.com" if target.kind == "github" else "gitlab.com"
        val = target.value.strip().lstrip("/")
        if val.endswith(".git"):
            val = val[:-len(".git")]
        if not val:
            return set(), [target.value]
        return {(host, val)}, []
    if target.kind == "git":
        c = _canonical_remote(target.value)
        if c is None:
            return set(), [target.value]
        return {c}, []
    return None, []


def _canon_sets_agree(a_canon, b_canon, kind):
    """True when two canonical (host, path) remote sets share an endpoint (spec 5.6). For a github/gitlab
    target the org/repo PATH compares case-INSENSITIVELY (those hosts resolve org/repo without regard to
    case; the host is already lowercased by _canonical_remote / _target_remote_canon), so a case-only
    difference is not a disagreement (F5); any other host stays case-sensitive, a disclosed residual since
    an arbitrary git host may be case-sensitive. BOTH legs of C-SYNC-AGREE (the committed-pointer leg and
    the actual-remote leg) share this one fold so they cannot diverge (round-16 F-3)."""
    if not a_canon.isdisjoint(b_canon):
        return True
    # github.com / gitlab.com resolve the org/repo PATH case-insensitively: this is a property of the HOST,
    # not of how the target was spelled, so a git: URL naming github.com folds exactly as the github:
    # shorthand does (ROUND-2 codex-3 s2; the host is already lowercased by the canonicalizers). Any other
    # host stays case-sensitive, a disclosed residual since an arbitrary git host may be case-sensitive.
    _ci = ("github.com", "gitlab.com")
    a_fold = {(h, pth.lower()) if h in _ci else (h, pth) for h, pth in a_canon}
    b_fold = {(h, pth.lower()) if h in _ci else (h, pth) for h, pth in b_canon}
    return not a_fold.isdisjoint(b_fold)


# --- index parsing (shape defined here) --------------------------------------------------------------

def _index_rows(data, where, rep):
    """Validate an `<type>.index.toml` file's top shape and return its `[[record]]` rows, or None
    (CANNOT-EVALUATE) when the file is not a table, carries an unsupported schema, or its `record` value
    is not an array. An unknown top-level key is an INVALID finding (closed keyset)."""
    if not isinstance(data, dict):
        rep.cant("{}: index is not a table".format(where))
        return None
    extra = set(data) - INDEX_TOP_KEYS
    if extra:
        rep.finding("{}: unknown top-level key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
    if "schema" not in data:
        rep.finding("{}: index is missing the required `schema` key (a closed index declares its "
                    "schema; spec 13)".format(where))
    else:
        sch = data.get("schema")
        if type(sch) is not int or sch != SUPPORTED_SCHEMA:
            rep.cant("{}: schema {} is not the supported version {} (fail-closed; do not parse under v{} "
                     "assumptions)".format(where, _safe_display(sch), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))
            return None
    records = data.get("record", [])
    if not isinstance(records, list):
        rep.cant("{}: [[record]] is not an array of tables".format(where))
        return None
    return records


def _gather_inline(tname, rows, where, registered_vendors, rep):
    """Gather the inline-layout records of one type index: each row IS the full record table, validated by
    validate_record. Returns the _Rec list; per-record faults are reported (INVALID) or, for an
    unidentifiable row, recorded CANNOT-EVALUATE. Attributed to C-RECORDS by the caller's current focus."""
    recs = []
    for i, row in enumerate(rows):
        rw = "{}#{}".format(where, i + 1)
        if not isinstance(row, dict):
            rep.cant("{}: record row is not a table".format(rw))
            continue
        rv = validate_record(row, expected_type=tname, registered_vendors=registered_vendors)
        if rv.status == CANNOT_EVALUATE:
            rep.cant("{}: {}".format(rw, "; ".join(rv.findings)))
            continue
        if rv.status != VALID:
            for f in rv.findings:
                rep.finding("{}: {}".format(rw, f))
        recs.append(_make_rec(row, tname, "active"))
    return recs


def _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, where, registered_vendors, rep, recon, schema_deferred=False):
    """Gather the per-record-layout records of one type: each registry row is `{id, state, path, digest}`,
    its body lives at `<type>/<id>.toml`, and the row must bind to the body (id, state, digest; OPF-SPEC
    9:839, 13). Record-body SCHEMA faults are emitted directly (C-RECORDS focus); the RECONCILIATION
    problems (row keyset, path canonicality, missing / orphan bodies, id/state binding, digest mismatch)
    are collected into `recon` for the caller to replay under C-PERRECORD-RECONCILE. Reconciliation is
    BIDIRECTIONAL: a registry row with no body file, and a body file with no registry row, are both
    findings."""
    recs = []
    seen_ids = set()
    for i, row in enumerate(rows):
        rw = "{}#{}".format(where, i + 1)
        if not isinstance(row, dict):
            recon.append(("cant", "{}: registry row is not a table".format(rw)))
            continue
        extra = set(row) - PERRECORD_ROW_KEYS
        missing = PERRECORD_ROW_KEYS - set(row)
        if extra:
            recon.append(("finding", "{}: unknown registry-row key(s): {}".format(
                rw, ", ".join(_sorted_key_names(extra)))))
        if missing:
            recon.append(("finding", "{}: registry row is missing required key(s): {} (the row is "
                          "{{id, state, path, digest}}; spec 9:839)".format(
                              rw, ", ".join(_sorted_key_names(missing)))))
        rid = row.get("id")
        shape = _valid_id_shape(rid)
        if shape is None or shape[0] != namespace:
            recon.append(("finding", "{}: registry row id {} is not a well-formed {}-<n> id (spec 8.2)".format(
                rw, _safe_display(rid), namespace)))
            continue
        seen_ids.add(rid)
        canonical_path = "{}/{}.toml".format(tname, rid)
        prow = row.get("path")
        if not (isinstance(prow, str) and _is_contained_relpath(prow) and prow == canonical_path):
            recon.append(("finding", "{}: registry row path {} is not the contained canonical path {!r} "
                          "(spec 9/13)".format(rw, _safe_display(prow), canonical_path)))
        body_rel = _rel(machine_rel, tname, "{}.toml".format(rid))   # the canonical path (equality enforced above)
        body, bst = _read_toml(root_fd, body_rel, rep)
        if bst == "error":
            continue
        if bst == "absent":
            recon.append(("finding", "{}: registry row {!r} resolves to no record file {} (an index row "
                          "with no record; spec 13:999-1001)".format(rw, rid, body_rel)))
            continue
        if not isinstance(body, dict):
            recon.append(("cant", "{}: record file {} is not a table".format(rw, body_rel)))
            continue
        if schema_deferred:
            # round-7 BLOCKER: a schema-deferred per-record type still gets FULL structural reconciliation
            # (row/body binding, path canonicality, digest, and the reverse orphan-body scan below); only the
            # record CONTENT schema defers to a named CANNOT-EVALUATE, so an orphan or mis-bound body can
            # never certify VALID through the deferral path.
            rep.cant("{}: {}".format(body_rel, _SCHEMA_DEFERRAL))
        else:
            rv = validate_record(body, expected_type=tname, registered_vendors=registered_vendors)
            if rv.status == CANNOT_EVALUATE:
                rep.cant("{}: {}".format(body_rel, "; ".join(rv.findings)))
            elif rv.status != VALID:
                for f in rv.findings:
                    rep.finding("{}: {}".format(body_rel, f))
        # F8: bind the registry row to the body it names (id then state), before seating it into the maps.
        body_id = body.get("id")
        if body_id != rid:
            recon.append(("finding", "{}: registry row id {!r} does not match record-file id {} (an FN-1 "
                          "row over an FN-2 body; the row is not seated; spec 13)".format(
                              rw, rid, _safe_display(body_id))))
            continue
        row_state = row.get("state")
        body_status = body.get("status")
        if row_state != body_status:
            recon.append(("finding", "{}: registry row state {} does not match record status {} (spec "
                          "9/13)".format(rw, _safe_display(row_state), _safe_display(body_status))))
        try:
            want = _record_digest(body)
        except _opf_emit.EmitError as exc:
            recon.append(("cant", "{}: cannot compute the per-record digest ({})".format(body_rel, exc)))
            want = None
        if want is not None and row.get("digest") != want:
            recon.append(("finding", "{}: per-record digest mismatch for {!r} (the index row and the record "
                          "file disagree; spec 13:999-1001)".format(rw, rid)))
        recs.append(_make_rec(body, tname, "active"))
    # Reverse direction: every record file under <type>/ must have a registry row.
    _subdirs, files = _list_dir(root_fd, _rel(machine_rel, tname), rep)
    if files is not None:
        for fn_ in files:
            if not fn_.endswith(".toml"):
                continue
            fid = fn_[:-len(".toml")]
            if fid not in seen_ids:
                recon.append(("finding", "{}/{}: record file {!r} has no registry row in {} (a record with "
                              "no index row; spec 13:999-1001)".format(tname, fn_, fid, where)))
    return recs


def _gather_active_records(root_fd, machine_rel, enabled_types, layout, registered_vendors, rep):
    """Gather every active non-worklog record across the enabled type indexes. Returns (recs, recon) where
    `recon` carries the per-record-layout reconciliation problems for C-PERRECORD-RECONCILE. A declared
    enabled type with no `<type>.index.toml` is an INVALID finding (a declared input is absent; spec 11)."""
    recs = []
    recon = []
    # Read the enabled-type roster PLUS any importer type (legacy_fragment) whose index is PRESENT. Per the
    # decoupled D6 design an importer type is never declared in the manifest, so it is absent from
    # enabled_types; yet a PRESENT importer index must still be READ and content-schema-deferred (PRC-F1/N-1:
    # merely recognizing the containment leaf without reading it laundered arbitrary bytes and rogue records
    # at that reserved name). An importer index is accepted-if-present, never required: a PRESENT index is
    # read + structurally graded (envelope, ids seated for id-space/counters/no-deletion) + content-schema
    # deferred (the _SCHEMA_DEFERRAL cant), while an ABSENT index is NOT a finding, exactly mirroring the
    # C-COUNTERS optional_namespaces treatment.
    read_roster = dict(enabled_types)
    for _imp_tname, _imp_ns in IMPORTER_TYPES.items():
        read_roster.setdefault(_imp_tname, _imp_ns)
    for tname in sorted(name for name in read_roster if name != "worklog"):
        namespace = read_roster[tname]
        idx_rel = _rel(machine_rel, "{}{}".format(tname, INDEX_SUFFIX))
        data, st = _read_toml(root_fd, idx_rel, rep)
        if st == "error":
            continue
        if st == "absent":
            if tname in IMPORTER_TYPES and tname not in enabled_types:
                # An importer type is accepted-if-present, never required: an absent index is not a finding
                # (a store with no import legitimately carries no legacy_fragment index).
                continue
            rep.finding("enabled type {!r} has no {}{} (a declared input is absent; spec 11)".format(
                tname, tname, INDEX_SUFFIX))
            continue
        rows = _index_rows(data, idx_rel, rep)
        if rows is None:
            continue
        if _schema_deferred(tname):
            # F4/round-5 F-2: a record whose schema has not shipped in the baseline validator (a module-tier
            # type, or the importer legacy_fragment type) defers its CONTENT schema to a named
            # CANNOT-EVALUATE rather than the baseline validator's false "not a supported type".
            if layout == "per-record":
                # round-7 BLOCKER: per-record bodies still need STRUCTURAL reconciliation (orphan bodies,
                # row/body id-state-digest binding, path canonicality, the reverse body-file scan) even when
                # the content schema is deferred, so a valid-id .toml body with no registry row is a finding,
                # never a silently-managed leaf. Only the content schema defers (inside _gather_perrecord).
                recs.extend(_gather_perrecord(root_fd, machine_rel, tname, namespace, rows, idx_rel,
                                              registered_vendors, rep, recon, schema_deferred=True))
            else:
                # Inline: the index rows ARE the records, so there are no separate body files to reconcile;
                # defer the content schema and seat the ids best-effort so the id-space, no-deletion, and
                # counters-completeness checks still see them.
                if rows:
                    rep.cant("{}: {} schema-deferred record(s) of type {!r}; {}".format(
                        idx_rel, len(rows), tname, _SCHEMA_DEFERRAL))
                for row in rows:
                    if isinstance(row, dict):
                        recs.append(_make_rec(row, tname, "active"))
            continue
        if layout == "per-record":
            recs.extend(_gather_perrecord(root_fd, machine_rel, tname, namespace, rows, idx_rel,
                                          registered_vendors, rep, recon))
        else:
            recs.extend(_gather_inline(tname, rows, idx_rel, registered_vendors, rep))
    return recs, recon


def _worklog_legacy_conflict(_relpath):
    """C-CONTAINMENT grades the stray directory; keep inspecting the declared legacy source."""
    # Only load_worklog_at's generation-1 branch invokes this policy.
    return None


def _gather_worklog(root_fd, relpath, registered_vendors, rep, required, machine_rel=None,
                    *, propagate_manifest_failure=False, supported_profiles=None, manifest_model=None):
    """Read and validate a worklog.toml (active or an archive bucket) through U3's validate_worklog, and
    return its WL-number -> entry map. A required (active) worklog that is absent is CANNOT-EVALUATE; an
    archive-bucket worklog that is absent returns None (the caller only reads it when the bucket has one).
    The enclosing traversal receives manifest failures before diagnostic translation so it can
    attribute the original diagnostics and stop dependent archive reads; an ordinary ledger
    failure still permits archive diagnostics."""
    try:
        if machine_rel is None:       # explicitly named archive bucket, never shape-probed (M7)
            data = _opf_worklog.load_archive_worklog_at(root_fd, relpath)
        else:
            data = _opf_worklog.load_worklog_at(
                root_fd, machine_rel, required=False, on_legacy_conflict=_worklog_legacy_conflict,
                supported_profiles=supported_profiles, manifest_model=manifest_model)
        st = "absent" if data is None else "present"
    except _opf_worklog.ManifestShapeError as exc:
        if propagate_manifest_failure:
            raise
        rep.cant("{} is absent (the store manifest is required; spec 4.5)".format(exc.relpath)
                 if exc.missing else "{}: {}".format(exc.relpath, exc))
        return None
    except _opf_worklog.ManifestValidationError as exc:
        if propagate_manifest_failure:
            raise
        if exc.status == CANNOT_EVALUATE:
            rep.cant("{}: {}".format(exc.relpath, exc))
        else:
            rep.cant("{} is not evaluated: {} failed manifest validation "
                     "(see C-MANIFEST)".format(relpath, exc.relpath))
        return None
    except _opf_worklog.ManifestReadError as exc:
        if propagate_manifest_failure:
            raise
        # Match _read_toml's manifest diagnostic, not the worklog ledger's path.
        rep.cant("cannot read {}: {}".format(exc.relpath, exc))
        return None
    except _opf_worklog.WorklogError as exc:
        rep.cant(str(exc))
        return None
    except StoreError as exc:
        rep.cant("cannot read {}: {}".format(relpath, exc))
        return None
    except _journal.JournalError as exc:
        rep.cant("cannot read {} ({})".format(relpath, exc))
        return None
    if st == "absent":
        if required:
            rep.cant("{} is absent (the active worklog ledger is required; spec 6.2)".format(relpath))
        return None
    wv = validate_worklog(data, registered_vendors=registered_vendors)
    if wv.status == CANNOT_EVALUATE:
        rep.cant("{}: {}".format(relpath, "; ".join(wv.findings)))
        return None
    if wv.status != VALID:
        for f in wv.findings:
            rep.finding("{}: {}".format(relpath, f))
    by_id, idf = _entries_by_id(data)
    if by_id is None:
        rep.cant("{}: {}".format(relpath, "; ".join(idf)))
        return None
    for f in idf:
        rep.finding("{}: {}".format(relpath, f))
    return by_id


# --- the archive (C-ARCHIVE-ENUM / C-ROTATION and the merged worklog) --------------------------------

def _parse_archive_manifest(data, where, rep):
    """Parse and validate an `archive.toml` (shape defined here). Returns (moved, worklog_moved) where
    `moved` is a list of (id, type, destination) triples and `worklog_moved` a list of (span, destination),
    or (None, None) when the file is not a table, carries an unsupported/absent schema, or an array is
    malformed (each CANNOT-EVALUATE). A malformed individual row is an INVALID finding."""
    if not isinstance(data, dict):
        rep.cant("{}: archive.toml is not a table".format(where))
        return None, None
    extra = set(data) - ARCHIVE_TOP_KEYS
    if extra:
        rep.finding("{}: archive.toml unknown top-level key(s): {}".format(
            where, ", ".join(_sorted_key_names(extra))))
    sch = data.get("schema")
    if type(sch) is not int or sch != SUPPORTED_SCHEMA:
        rep.cant("{}: archive.toml schema {} is not the supported version {} (fail-closed)".format(
            where, _safe_display(sch), SUPPORTED_SCHEMA))
        return None, None
    moved_raw = data.get("moved", [])
    if not isinstance(moved_raw, list):
        rep.cant("{}: [[moved]] is not an array of tables".format(where))
        return None, None
    moved = []
    for i, m in enumerate(moved_raw):
        rw = "{} moved #{}".format(where, i + 1)
        if not isinstance(m, dict):
            rep.cant("{}: is not a table".format(rw))
            return None, None
        me = set(m) - ARCHIVE_MOVED_KEYS
        if me:
            rep.finding("{}: unknown key(s): {}".format(rw, ", ".join(_sorted_key_names(me))))
        mid, mtype, dest = m.get("id"), m.get("type"), m.get("destination")
        if _valid_id_shape(mid) is None:
            rep.finding("{}: id {} is not a well-formed <NS>-<n> id (spec 8.2)".format(rw, _safe_display(mid)))
            continue
        if not isinstance(mtype, str) or not mtype:
            rep.finding("{}: type must be a non-empty string (spec 12)".format(rw))
            continue
        if not isinstance(dest, str) or not dest:
            rep.finding("{}: destination must be a non-empty string".format(rw))
            continue
        moved.append((mid, mtype, dest))
    wl_raw = data.get("worklog_moved", [])
    if not isinstance(wl_raw, list):
        rep.cant("{}: [[worklog_moved]] is not an array of tables".format(where))
        return None, None
    wl_moved = []
    for i, s in enumerate(wl_raw):
        rw = "{} worklog_moved #{}".format(where, i + 1)
        if not isinstance(s, dict):
            rep.cant("{}: is not a table".format(rw))
            return None, None
        se = set(s) - ARCHIVE_WLMOVED_KEYS
        if se:
            rep.finding("{}: unknown key(s): {}".format(rw, ", ".join(_sorted_key_names(se))))
        span, dest = s.get("span"), s.get("destination")
        if not (isinstance(span, list) and len(span) == 2):
            rep.finding("{}: span must be a two-element [WL-a, WL-b] array".format(rw))
            continue
        if not isinstance(dest, str) or not dest:
            rep.finding("{}: destination must be a non-empty string".format(rw))
            continue
        wl_moved.append((span, dest))
    return moved, wl_moved


def _dest_index_has_id(root_fd, machine_rel, dest, target_id, rep):
    """True when the store-relative index `dest` (an `archive.toml` moved destination) actually carries a
    row for `target_id`. A dest that escapes the store, is unreadable, or is absent is fail-closed: it
    records a finding / cannot-evaluate and returns False, so an enumerated id whose destination cannot be
    confirmed is never read as present."""
    if not _is_contained_relpath(dest):
        rep.finding("C-ARCHIVE-ENUM: archive destination {!r} is not a contained store-relative path "
                    "(spec 14.2); NEVER opened".format(dest))
        return False
    full = _rel(machine_rel, dest)
    data, st = _read_toml(root_fd, full, rep)
    if st == "error":
        return False
    if st == "absent":
        rep.finding("C-ARCHIVE-ENUM: archive destination {!r} does not exist".format(dest))
        return False
    rows = _index_rows(data, full, rep)
    if rows is None:
        return False
    for row in rows:
        if isinstance(row, dict) and row.get("id") == target_id:
            return True
    return False


def _archived_rotatable(rec):
    """Rotation eligibility of a non-worklog archived record. Returns True (rotatable: an UNQUALIFIED
    TERMINAL state, OPF-SPEC 12:972), False (must never rotate: non-terminal or proposal-qualified,
    OPF-SPEC 12:978), or None when the record's type has no released schema (a module-tier record; rotation
    eligibility is deferred to U2M, never graded here; F4)."""
    if _schema_deferred(rec.rtype):
        return None
    spec = BASELINE_SPECS.get(rec.rtype)
    if spec is None:
        return False
    return rec.state in spec.terminal and rec.qual is None


def _staged_run_roots(homes):
    """The staging roots whose runs substantiate a partial import: the imports tree, plus the typed
    import and ingest homes in a homes-2 store."""
    roots = (IMPORTS_REL,)
    if homes >= 2:
        roots += tuple(_opf_store.STAGING_REL + "/" + kind for kind in ("import", "ingest"))
    return roots


# The run subdirectories that hold staged `{schema, record}` indexes (`<type>.index.toml`, `worklog.toml`).
_STAGED_ID_SUBDIRS = ("candidate", "fragments")


def _staged_ids(root_fd, homes, rep):
    """The ids staged under every run in the staging roots, for the C-ID-SPACE uniqueness union (spec 11:
    uniqueness spans active, archive, and staging). Read only, through the contained no-follow readers: each
    `<run>/candidate|fragments/<type>.index.toml` or `worklog.toml` must be a closed `{schema, record}`
    index at the supported schema whose rows are tables carrying a well-formed id. Nothing is validated
    beyond what uniqueness needs. An unreadable listing or file, an exotic entry, and any malformed staged
    index are CANNOT-EVALUATE naming the input, never skipped, so a staged id cannot drop out of the union.
    The staged layout has no nesting: ANY directory under candidate/ or fragments/ is CANNOT-EVALUATE naming
    it, whether it sits at a staged index path (`<type>.index.toml` or `worklog.toml` as a directory) or is
    an unknown subdirectory, since its content is not enumerated and could hide staged ids. A regular file
    with neither an index name nor the worklog name stages nothing and is left to C-CONTAINMENT."""
    ids = []
    for staging_rel in _staged_run_roots(homes):
        runs, _files = _list_dir(root_fd, staging_rel, rep)
        for run in runs or ():
            for sub in _STAGED_ID_SUBDIRS:
                sub_rel = _rel(staging_rel, run, sub)
                subs, names = _list_dir(root_fd, sub_rel, rep)
                for entry in subs or ():
                    what = ("is a directory at a staged index path" if entry.endswith(INDEX_SUFFIX)
                            or entry == WORKLOG_NAME else "is an unknown subdirectory")
                    rep.cant("{}: {} under a staged run's {}/; the staged-id enumeration reads only regular "
                             "index files there, so its content could hide staged ids (fail-closed)".format(
                                 _safe_display(_rel(sub_rel, entry)), what, sub))
                for entry in names or ():
                    if not (entry.endswith(INDEX_SUFFIX) or entry == WORKLOG_NAME):
                        continue
                    where = _safe_display(_rel(sub_rel, entry))
                    data, st = _read_toml(root_fd, _rel(sub_rel, entry), rep)
                    if st == "absent":
                        # Listed as a regular file but gone at read: the tree changed under the walk, so the
                        # staged ids it held are unknown (fail-closed, never an empty contribution).
                        rep.cant("{}: staged index was listed but is absent at read (fail-closed)".format(where))
                    if st != "ok":
                        continue        # an error is already CANNOT-EVALUATE
                    extra = set(data) - INDEX_TOP_KEYS
                    sch = data.get("schema")
                    records = data.get("record")
                    if extra:
                        rep.cant("{}: staged index carries unknown top-level key(s): {} (fail-closed)".format(
                            where, ", ".join(_sorted_key_names(extra))))
                    elif type(sch) is not int or sch != SUPPORTED_SCHEMA:
                        rep.cant("{}: staged index schema {} is not the supported version {} "
                                 "(fail-closed)".format(where, _safe_display(sch), SUPPORTED_SCHEMA))
                    elif not isinstance(records, list):
                        rep.cant("{}: staged index [[record]] is missing or not an array of tables "
                                 "(fail-closed)".format(where))
                    else:
                        for i, row in enumerate(records):
                            rid = row.get("id") if isinstance(row, dict) else None
                            if _valid_id_shape(rid) is None:
                                rep.cant("{}#{}: staged record is not a table carrying a well-formed id "
                                         "(fail-closed)".format(where, i + 1))
                            else:
                                ids.append(rid)
    return ids


def _has_active_import_run(root_fd, machine_rel, rep, homes=1):
    """Substantiate partial imports by a named staged run carrying plan.toml.

    A legacy store (homes 1) reads only the imports tree. A homes-2 store also recognizes import
    and ingest runs in their typed staging homes, which share the staged-plan contract. This
    establishes staging presence, not journal integrity or recoverability. Other kinds cannot
    substantiate partial imports until their plan readers are registered. Listing errors are
    cannot-evaluate. machine_rel is retained for call-site symmetry.
    """
    for imports_rel in _staged_run_roots(homes):
        subdirs, _files = _list_dir(root_fd, imports_rel, rep)
        for d in subdirs or ():
            if not _is_import_run_id(d):
                continue
            _rsubs, rfiles = _list_dir(root_fd, _rel(imports_rel, d), rep)
            if rfiles is not None and "plan.toml" in rfiles:
                return True
    return False


def _archive_unmanaged(rep, partial_active, path):
    """Grade an unregistered path found in the archive tree: a finding at steady state, a triage entry only
    when a partial import is SUBSTANTIATED by an active run (codex-2; spec 12/14.2). C-CONTAINMENT skips the
    archive subtree, so the archive's own stray-path grading is owned here (B2), matching C-ARCHIVE-ENUM's
    existing unexpected-bucket-file flag."""
    if partial_active:
        rep.triage_path("unregistered path {!r} in the archive tree (an import or migration is in "
                        "progress; triage per spec 14.2)".format(path))
    else:
        rep.finding("C-ARCHIVE-ENUM: unregistered path {!r} in the archive tree is neither an OPF-managed "
                    "archive artefact nor a <YYYY> bucket (spec 12/14.2)".format(path))


def _validate_archive(root_fd, machine_rel, enabled_types, registered_vendors, import_status, rep, homes=1):
    """Walk the archive tree, validating each bucket's archive.toml, its archived non-worklog records, and
    its archived worklog, and reconciling the enumeration BIDIRECTIONALLY against what is actually present
    (OPF-SPEC 12/13). Enumeration and record-schema faults attribute to the caller's C-ARCHIVE-ENUM focus;
    the rotation-eligibility (F4) findings are collected into a returned list for C-ROTATION. Returns
    (archive_recs, archive_worklogs, rotatable) where archive_worklogs maps a year to its WL-number -> entry
    map. Archive absent means no rotation (clean); a present bucket with no archive.toml is CANNOT-EVALUATE,
    never 'no rotation happened'. No loop is sized by a declared span endpoint (F15)."""
    archive_recs = []
    archive_worklogs = {}
    rotatable = []
    # codex-2: a partial import triages archive stray paths only when SUBSTANTIATED by an active run; a
    # stale partial with no running import grades them as findings (never a triage pass that disables
    # grading), matching C-CONTAINMENT.
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep, homes)
    archive_rel = _rel(machine_rel, ARCHIVE_DIRNAME)
    years, top_files = _list_dir(root_fd, archive_rel, rep)
    if years is None:
        return archive_recs, archive_worklogs, rotatable   # no archive/ tree: no rotation has happened (clean)
    for fn_ in (top_files or []):
        # A regular file directly under archive/ is unmanaged: only <YYYY> bucket directories belong here.
        # C-CONTAINMENT skips the archive subtree, so its stray-path grading lives here (spec 12/14.2; B2).
        _archive_unmanaged(rep, partial_active, _rel(archive_rel, fn_))
    for year in years:
        bucket = _rel(archive_rel, year)
        bucket_rel = _rel(ARCHIVE_DIRNAME, year)      # bucket path RELATIVE to machine_rel (dest space)
        if not (year.isdigit() and len(year) == 4):
            rep.finding("C-ARCHIVE-ENUM: archive bucket {!r} is not a <YYYY> calendar-year directory "
                        "(spec 12)".format(year))
        sub, bfiles = _list_dir(root_fd, bucket, rep)
        if bfiles is None:
            rep.cant("archive bucket {} could not be listed".format(bucket))
            continue
        for d in (sub or []):
            # A subdirectory inside a bucket is unmanaged: a bucket holds only files (archive.toml,
            # worklog.toml, <type>.index.toml), and its contents are never listed, so the whole subtree is
            # graded as one unregistered path (spec 12/14.2; B2).
            _archive_unmanaged(rep, partial_active, _rel(bucket, d))
        if ARCHIVE_MANIFEST_NAME not in bfiles:
            rep.cant("archive bucket {} has no {} (cannot evaluate the rotation; spec 12)".format(
                bucket, ARCHIVE_MANIFEST_NAME))
            continue
        amf, ast = _read_toml(root_fd, _rel(bucket, ARCHIVE_MANIFEST_NAME), rep)
        if ast != "ok":
            continue
        moved, wl_moved = _parse_archive_manifest(amf, bucket, rep)
        if moved is None:
            continue
        if moved and not wl_moved:
            # gemini/claude round-11 BLOCKER: per-bucket rotation binding. A bucket that rotates non-worklog
            # records MUST carry an archived worklog span to bind them to a released period (spec 12).
            # archive_wl_ids aggregates GLOBALLY, so the caller's released-only check can be satisfied by
            # ANOTHER bucket's span; the binding is therefore enforced here, per bucket.
            rotatable.append("C-ROTATION: {} record(s) are rotated to bucket {!r} with no archived worklog "
                             "span to bind them to a released period (spec 12)".format(len(moved), bucket_rel))
        enumerated_ids = {mid for mid, _mtype, _dest in moved}

        # Archived non-worklog records actually present in this bucket's <type>.index.toml files.
        present_types = {}     # id -> the type the record actually is under this bucket
        for fn_ in bfiles:
            if fn_ in (ARCHIVE_MANIFEST_NAME, WORKLOG_NAME):
                continue
            if not fn_.endswith(INDEX_SUFFIX):
                # A non-index file in a bucket (only archive.toml, worklog.toml, and <type>.index.toml
                # belong) is an unregistered path; route it through the SAME substantiated-partial triage
                # as its siblings (_archive_unmanaged for root files and bucket subdirs), so the store-wide
                # partial relaxation (spec 14.2) is honoured here too rather than hard-failing (F2).
                _archive_unmanaged(rep, partial_active, _rel(bucket, fn_))
                continue
            tname = fn_[:-len(INDEX_SUFFIX)]
            if tname not in enabled_types:
                # F1: a `<type>.index.toml` whose <type> is not an enabled roster type is unmanaged. The
                # active tree flags this via managed_file and C-CONTAINMENT skips the archive, so grade it
                # here (a stray junk.index.toml can no longer escape; spec 12/14.2).
                rep.finding("C-ARCHIVE-ENUM: archive bucket {} carries {!r}, whose type {!r} is not "
                            "an enabled roster type (an unmanaged archive index; spec 12/8.1)".format(
                                bucket, fn_, tname))
                continue
            idx_rel = _rel(bucket, fn_)
            data, st = _read_toml(root_fd, idx_rel, rep)
            if st != "ok":
                continue
            rows = _index_rows(data, idx_rel, rep)
            if rows is None:
                continue
            deferred = _schema_deferred(tname)
            if deferred and rows:
                # F4/round-5 F-2: an archived record whose schema has not shipped in the baseline validator
                # (module-tier, or importer legacy_fragment) is deferred to a named CANNOT-EVALUATE rather
                # than the baseline validator's false "not a supported type".
                rep.cant("{}: {} archived schema-deferred record(s) of type {!r}; {}".format(
                    idx_rel, len(rows), tname, _SCHEMA_DEFERRAL))
            for i, row in enumerate(rows):
                rw = "{}#{}".format(idx_rel, i + 1)
                if not isinstance(row, dict):
                    rep.cant("{}: archived record row is not a table".format(rw))
                    continue
                if not deferred:
                    rv = validate_record(row, expected_type=tname, registered_vendors=registered_vendors)
                    if rv.status == CANNOT_EVALUATE:
                        rep.cant("{}: {}".format(rw, "; ".join(rv.findings)))
                        continue
                    if rv.status != VALID:
                        for f in rv.findings:
                            rep.finding("{}: {}".format(rw, f))
                rec = _make_rec(row, tname, "archive/{}".format(year))
                archive_recs.append(rec)
                if isinstance(rec.id, str) and _valid_id_shape(rec.id) is not None:
                    present_types[rec.id] = tname
                    elig = _archived_rotatable(rec)
                    if elig is None:
                        pass     # module-tier: rotation eligibility deferred to U2M (F4), never graded here
                    elif not elig:
                        rotatable.append("C-ROTATION: archived record {!r} ({}) is not in an unqualified "
                                         "terminal state and must never rotate (spec 12:972-979)".format(
                                             rec.id, tname))
        # Archived worklog (release-based rotation).
        wl_map = {}
        if WORKLOG_NAME in bfiles:
            wl_map = _gather_worklog(root_fd, _rel(bucket, WORKLOG_NAME), registered_vendors, rep,
                                     required=False) or {}
        archive_worklogs[year] = wl_map

        # F5 / F12 non-worklog reconciliation, BOTH directions.
        for mid, mtype, dest in moved:
            shape = _valid_id_shape(mid)
            id_ns = shape[0] if shape is not None else None
            norm_ns = _ROSTER_NAMESPACES.get(mtype)
            if norm_ns is None:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} declares type {!r}, which is not a roster type "
                            "(spec 8.1)".format(mid, mtype))
            elif id_ns is not None and id_ns != norm_ns:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} declares type {!r} (namespace {}) but the id "
                            "namespace is {} (spec 8.1)".format(mid, mtype, norm_ns, id_ns))
            want_base = "{}{}".format(mtype, INDEX_SUFFIX)
            expected_dest = _rel(bucket_rel, want_base)
            if dest != expected_dest:
                # M2(a): a rotation lands in its DECLARING bucket's own type index, never in the active tree
                # or another bucket. Exact-path equality subsumes the basename check and confines the
                # destination to archive/<year>/ (spec 12); the wrong destination is not then opened.
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} destination {!r} is not this bucket's type index "
                            "{!r} (a rotation lands in its declaring bucket; spec 12)".format(
                                mid, dest, expected_dest))
            elif not _dest_index_has_id(root_fd, machine_rel, dest, mid, rep):
                rep.finding("C-ARCHIVE-ENUM: enumerated moved id {!r} is absent from its destination {!r} "
                            "(a rotation must land where it says; spec 12:981-982)".format(mid, dest))
            found_type = present_types.get(mid)
            if found_type is not None and found_type != mtype:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} declares type {!r} but the archived record found "
                            "under it is a {} (spec 12)".format(mid, mtype, found_type))
        for rid in sorted(present_types):
            if rid not in enumerated_ids:
                rep.finding("C-ARCHIVE-ENUM: archived record {!r} in {} is not enumerated in archive.toml "
                            "(a record cannot quietly vanish under rotation; spec 12:981-982)".format(
                                rid, bucket))

        # F5 worklog-span reconciliation, BOTH directions, arithmetic (no span expansion; F15). Endpoints
        # are parsed; spans are sorted and checked non-overlapping; a two-pointer sweep matches the sorted
        # PRESENT ids against the sorted spans, so cost is O(spans + present ids), never the span width.
        spans = []
        expected_wl_dest = _rel(bucket_rel, WORKLOG_NAME)
        for span, dest in wl_moved:
            if not _is_contained_relpath(dest):
                rep.finding("C-ARCHIVE-ENUM: worklog_moved destination {!r} in {} is not a contained "
                            "store-relative path (spec 12); the destination is never opened".format(dest, bucket))
            elif dest != expected_wl_dest:
                # M2(b): the span reconciliation reads the bucket's OWN worklog.toml regardless of the
                # declared destination, so a wrong destination is otherwise decorative. Require equality to
                # the declaring bucket's worklog (spec 12).
                rep.finding("C-ARCHIVE-ENUM: worklog_moved destination {!r} in {} is not this bucket's "
                            "worklog {!r} (a worklog rotation lands in its bucket's own worklog; spec "
                            "12)".format(dest, bucket, expected_wl_dest))
            a, b = _wl_num(span[0]), _wl_num(span[1])
            if a is None or b is None or a > b:
                rep.finding("C-ARCHIVE-ENUM: worklog_moved span {} in {} is not a well-formed [WL-a, WL-b] "
                            "range (spec 8.2/12)".format(_safe_display(span), bucket))
                continue
            spans.append((a, b, dest))
        spans.sort()
        for i in range(1, len(spans)):
            if spans[i][0] <= spans[i - 1][1]:
                rep.finding("C-ARCHIVE-ENUM: worklog_moved spans overlap in {} (WL-{}..WL-{} and "
                            "WL-{}..WL-{}; a WL id lives in one span, spec 12)".format(
                                bucket, spans[i - 1][0], spans[i - 1][1], spans[i][0], spans[i][1]))
        present_sorted = sorted(wl_map)
        si = 0
        for n in present_sorted:
            while si < len(spans) and spans[si][1] < n:
                si += 1
            if si >= len(spans) or not (spans[si][0] <= n <= spans[si][1]):
                rep.finding("C-ARCHIVE-ENUM: archived worklog WL-{} in {} is not enumerated in a "
                            "worklog_moved span (a record cannot vanish under rotation; spec "
                            "12:981-982)".format(n, bucket))
        present_set = set(present_sorted)
        for a, b, dest in spans:
            lo = bisect.bisect_left(present_sorted, a)
            hi = bisect.bisect_right(present_sorted, b)
            in_range = present_sorted[lo:hi]          # bounded by PRESENT ids, never by the span width
            width = b - a + 1
            if len(in_range) < width:
                first_missing = a + len(in_range)
                for idx, val in enumerate(in_range):
                    if val != a + idx:
                        first_missing = a + idx
                        break
                rep.finding("C-ARCHIVE-ENUM: worklog_moved span WL-{}..WL-{} in {} enumerates {} id(s) not "
                            "present in {!r}; the first missing is WL-{} (spec 12:981-982)".format(
                                a, b, bucket, width - len(in_range), dest, first_missing))
    return archive_recs, archive_worklogs, rotatable


# --- the cross-record checks -------------------------------------------------------------------------

def _check_receipts(recs, by_id, rep):
    """C-RECEIPTS (R1): the done<->backlog_item receipt is one-to-one in BOTH directions (OPF-SPEC 8.5).
    Every ratified backlog_item (unqualified `done`) has exactly one done receipt; every done's receipt_of
    target resolves to a backlog_item ratified at `done`."""
    bi_state = {r.id: (r.state, r.qual) for r in recs if r.rtype == "backlog_item" and isinstance(r.id, str)}
    done_targets = {}
    for r in recs:
        if r.rtype == "done" and isinstance(r.id, str):
            done_targets[r.id] = [tid for rel, tid in r.links if rel == "receipt_of" and isinstance(tid, str)]
    bi_to_dones = {}
    for did, targets in done_targets.items():
        for tid in targets:
            bi_to_dones.setdefault(tid, []).append(did)
    for bid, (state, qual) in bi_state.items():
        if state == "done" and qual is None:
            n = len(bi_to_dones.get(bid, ()))
            if n == 0:
                rep.finding("C-RECEIPTS: ratified backlog_item {!r} (at 'done') has no done receipt (the "
                            "receipt is one-to-one; spec 8.5)".format(bid))
            elif n > 1:
                rep.finding("C-RECEIPTS: backlog_item {!r} has {} done receipts; the receipt is one-to-one "
                            "(spec 8.5)".format(bid, n))
    for did, targets in done_targets.items():
        for tid in targets:
            st = bi_state.get(tid)
            if st is None:
                tgt = by_id.get(tid)
                if tgt is not None and tgt.rtype != "backlog_item":
                    rep.finding("C-RECEIPTS: done {!r} receipt_of -> {!r} is not a backlog_item (spec "
                                "8.1/8.5)".format(did, tid))
            else:
                state, qual = st
                if not (state == "done" and qual is None):
                    rep.finding("C-RECEIPTS: done {!r} receipt_of -> backlog_item {!r} which is not ratified "
                                "at 'done' (state {!r}; spec 8.5)".format(did, tid, state))


def _check_handoff(recs, rep):
    """C-HANDOFF (R2): at most one `current` handoff across the whole store (OPF-SPEC 8.5)."""
    current = [r.id for r in recs if r.rtype == "handoff" and r.state == "current" and r.qual is None]
    if len(current) > 1:
        rep.finding("C-HANDOFF: {} handoffs are 'current'; at most one current handoff exists across the "
                    "store (spec 8.5)".format(len(current)))


def decision_chains(recs, by_id):
    """The pending_decision supersession chains THAT HAVE A RESOLUTION (OPF-SPEC 8.5), as (members, current)
    pairs in record order. A chain is a connected component over `supersedes` links, whatever their
    direction; a current effective resolution is a decided (unqualified) pending_decision that no other
    supersedes. A wholly-undecided chain has no resolution yet and is not listed. The one derivation shared
    by C-DECISION-CHAINS and opf record's pre-publication chain check (spec 8.8), so the verb never judges a
    chain by a rule of its own."""
    pds = [r for r in recs if r.rtype == "pending_decision" and isinstance(r.id, str)]
    parent = {r.id: r.id for r in pds}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    superseded = set()
    for r in pds:
        for rel, tid in r.links:
            if rel == "supersedes" and isinstance(tid, str) and tid in parent:
                union(r.id, tid)
                superseded.add(tid)
    components = {}
    for pid in parent:
        components.setdefault(find(pid), []).append(pid)
    chains = []
    for _root, members in components.items():
        decided = [m for m in members if by_id.get(m) is not None
                   and by_id[m].state == "decided" and by_id[m].qual is None]
        if not decided:
            continue     # a wholly-undecided chain has no resolution yet: zero current is legitimate
        chains.append((members, [m for m in decided if m not in superseded]))
    return chains


def archived_records(root_fd, machine_rel, enabled_types, registered_vendors, import_status, homes=1):
    """The archived non-worklog records exactly as the doctor seats them beside the active records for its
    cross-record checks (C-DECISION-CHAINS among them): the C-ARCHIVE-ENUM walk itself (_validate_archive),
    never a second reader, so opf record's pre-publication chain check (spec 8.8) and the doctor judge a
    chain over one record set. Returns (archive_recs, problems): `problems` lists every cannot-evaluate,
    finding and C-ROTATION message the walk raised, so an archive input that is missing, unreadable or
    malformed is reported there, never skipped, and a caller judging a chain before a write refuses on any
    problem. A store with no archive tree has rotated nothing: ([], [])."""
    rep = _Report()
    rep.ran("C-ARCHIVE-ENUM")
    archive_recs, _worklogs, rotatable = _validate_archive(
        root_fd, machine_rel, enabled_types, registered_vendors, import_status, rep, homes=homes)
    return archive_recs, rep.cannot + rep.findings + rotatable


def _check_decision_chains(recs, by_id, rep):
    """C-DECISION-CHAINS (R3): exactly one current effective resolution per pending_decision supersession
    chain THAT HAS A RESOLUTION (OPF-SPEC 8.5), over the chains decision_chains derives. For a chain
    carrying at least one decided member, the count of current resolutions must be exactly one, in BOTH
    directions: zero (the sole resolution superseded by a withdrawn or still-open successor) is a fail-open
    the count-only-over-one form missed (F14). A wholly-undecided chain legitimately has zero and is not
    flagged."""
    for _members, current in decision_chains(recs, by_id):
        if len(current) != 1:
            rep.finding("C-DECISION-CHAINS: a pending_decision supersession chain with a decided resolution "
                        "has {} current effective resolutions; exactly one exists per chain (spec 8.5)".format(
                            len(current)))


def _check_links(recs, by_id, all_ids, rep):
    """C-LINKS (R4): every links relation target resolves to an existing record, with the closed type
    constraints (OPF-SPEC 8.6). `follows` and `relates` relate records to records with a closed vocabulary,
    so a dangling `follows`/`relates` is a finding too ("advisory" constrains a link's EFFECT on state, not
    its right to resolve; F13). receipt_of targets a backlog_item; supersedes targets the same type; the
    rest need only an existing target. Every block's `scopes` id is resolved against the same union
    (spec 8.5: a block scopes enumerated record IDs; an unresolvable scope corrupts the actionability
    join)."""
    for r in recs:
        for rel, tid in r.links:
            if rel not in LINK_RELS or not isinstance(tid, str):
                continue
            if tid not in all_ids:
                rep.finding("C-LINKS: {} {} link -> {!r} resolves to no record in the store (a dangling "
                            "link; spec 8.6)".format(r.id, rel, tid))
                continue
            target = by_id.get(tid)
            if target is None:
                continue
            if rel == "receipt_of" and target.rtype != "backlog_item":
                rep.finding("C-LINKS: {} receipt_of -> {!r} is a {}, not a backlog_item (spec 8.1/8.5)".format(
                    r.id, tid, target.rtype))
            elif rel == "supersedes" and target.rtype != r.rtype:
                rep.finding("C-LINKS: {} (type {!r}) supersedes -> {!r} of type {!r}; supersession links "
                            "the same type (spec 8.6)".format(r.id, r.rtype, tid, target.rtype))
    for r in recs:
        if r.rtype == "block":
            for sid in r.scopes:
                if isinstance(sid, str) and sid not in all_ids:
                    rep.finding("C-LINKS: block {} scopes {!r} which resolves to no record in the store "
                                "(spec 8.5)".format(r.id, sid))


def _check_worklog_contiguous(numbers, rep):
    """C-CONTIGUITY: read across active + archive the worklog tiles from WL-1 with no gap (OPF-SPEC 6.1),
    so the released spans map to real coverage. A pairwise scan over the sorted present numbers finds the
    first gap; nothing is sized by a declared high-water (F15)."""
    nums = sorted(n for n in numbers if isinstance(n, int) and not isinstance(n, bool))
    if not nums:
        return
    if nums[0] != 1:
        rep.finding("C-CONTIGUITY: the worklog does not start at WL-1 (first present is WL-{}; span tiling "
                    "starts at WL-1, spec 6.1)".format(nums[0]))
    for i in range(1, len(nums)):
        if nums[i] != nums[i - 1] + 1:
            rep.finding("C-CONTIGUITY: the worklog (active + archive) is not contiguous; WL-{} is missing "
                        "(a gap breaks span tiling, spec 6.1/11)".format(nums[i - 1] + 1))
            break


def _imported_counter_namespaces(enabled_types):
    """The namespaces whose imported counter row the doctor requires once the imported series is active
    (spec 8.2): one per enabled type except legacy_fragment, i.e. every enabled normative namespace the
    imported series may carry (IMPORTED_NAMESPACES refuses LF, the reserved TX and the unassigned CL)."""
    return frozenset(ns for ns in enabled_types.values() if ns in IMPORTED_NAMESPACES)


def _gather_imported_rows(root_fd, machine_rel, enabled_types, rep):
    """Read the imported-series leaves for the per-series id checks (spec 8.2/8.3). Each enabled type except
    legacy_fragment keeps its imported rows inline in the machine store in either layout (spec 4.2): the
    [[record]] rows of <type>.imported.index.toml, and the [[entry]] rows of worklog.imported.toml.
    Returns (rows, unread, misfiled): `rows` holds (counter_key, id, row) for every row whose id lies in its
    own leaf series, imported:<NS>-<n> over the leaf type namespace, in leaf then row order; `unread` holds
    the counter keys whose leaf could not be read (a CANNOT-EVALUATE is already recorded), so no later check
    reads an unread leaf as an empty one. An ABSENT leaf contributes no rows, and every id its counter
    allocated is still owed (C-NO-DELETION). A row outside its leaf series (a clean id, another namespace,
    a non-string or malformed id) is a finding here, the series grammar, and joins `misfiled` as
    (counter_key, "<leaf> <member>[<index>]", row) instead of `rows`: the id checks never read it, but
    C-IMPORTED-PROVENANCE still verifies its import table, so every row of a readable leaf is read. The rest
    of the imported envelope is C-IMPORTED-SCHEMA work, never graded here."""
    rows, unread, misfiled = [], set(), []
    for tname in sorted(enabled_types):
        ns = enabled_types[tname]
        if ns not in IMPORTED_NAMESPACES:
            continue
        key = IMPORTED_PREFIX + ns
        if tname in _LEDGER_TYPES:
            rel, member = _rel(machine_rel, _opf_store.IMPORTED_WORKLOG_NAME), "entry"
        else:
            rel, member = _rel(machine_rel, tname + _opf_store.IMPORTED_INDEX_SUFFIX), "record"
        data, st = _read_toml(root_fd, rel, rep)
        if st == "absent":
            continue
        if st != "ok":
            unread.add(key)
            continue
        arr = data.get(member, []) if isinstance(data, dict) else None
        if not isinstance(arr, list):
            rep.cant("{} carries no readable [[{}]] array, so its imported ids cannot be graded (spec "
                     "8.2/8.3)".format(rel, member))
            unread.add(key)
            continue
        for i, row in enumerate(arr):
            rid = row.get("id") if isinstance(row, dict) else None
            shape = parse_series_id(rid)
            if shape is None or shape[0] != key:
                rep.finding("C-ID-SPACE: {} {}[{}] id {} is not an {}-<n> id; an imported leaf holds only "
                            "its own type series (spec 8.2)".format(rel, member, i, _safe_display(rid), key))
                misfiled.append((key, "{} {}[{}]".format(rel, member, i), row))
                continue
            rows.append((key, rid, row))
    return rows, unread, misfiled


def _check_imported_worklog_contiguous(numbers, rep):
    """C-CONTIGUITY on the imported:WL number line, graded apart from the clean worklog (spec 8.2: contiguity
    evaluates each series independently): the imported worklog tiles from imported:WL-1 with no gap. The two
    lines never merge, so an imported gap never breaks clean release tiling and an imported entry never fills
    a clean gap (clean release spans tile only the clean WL line, spec 8.2). A pairwise scan over the sorted
    distinct present numbers, never sized by a high-water (F15); a duplicate is the C-ID-SPACE finding."""
    nums = sorted(set(numbers))
    if not nums:
        return
    if nums[0] != 1:
        rep.finding("C-CONTIGUITY: the imported worklog does not start at imported:WL-1 (first present is "
                    "imported:WL-{}; each series tiles its own number line, spec 8.2)".format(nums[0]))
    for i in range(1, len(nums)):
        if nums[i] != nums[i - 1] + 1:
            rep.finding("C-CONTIGUITY: the imported worklog is not contiguous; imported:WL-{} is missing (the "
                        "imported line is graded apart from clean release tiling, spec 8.2)".format(
                            nums[i - 1] + 1))
            break


def _check_imported_no_deletion(rows, unread, high, floor, namespaces, rep):
    """C-NO-DELETION over the imported series (spec 8.2/8.3): for each required imported counter row, the
    present imported ids must be exactly floor+1 .. high-water on that row own number line, apart from the
    clean one. Imported records never rotate (spec 12), so the imported leaves are their only location. The
    same O(present) arithmetic as the clean scan: an overhang COUNT above the max present id, then the first
    gap below it, never a range() expansion of the high-water (F15). A key whose leaf is unread, or whose
    counter is absent or malformed (the C-COUNTERS finding), is skipped here, never read as a deletion."""
    present = {}
    for key, rid, _row in rows:
        present.setdefault(key, set()).add(parse_series_id(rid)[2])
    for key in sorted(IMPORTED_PREFIX + ns for ns in namespaces):
        if key in unread:
            continue
        hw = high.get(key)
        if not (isinstance(hw, int) and not isinstance(hw, bool) and hw >= 0):
            continue
        base = floor.get(key, 0)
        nums = sorted(n for n in present.get(key, ()) if n > base)
        max_present = nums[-1] if nums else base
        if hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated {}-<n> id(s) above {}-{} are absent from the imported "
                        "leaves (counters {} high-water is {}; imported records are immutable after "
                        "publication, spec 8.2/8.3)".format(hw - max_present, key, key, max_present, key, hw))
        expected = base + 1
        for n in nums:
            if n != expected:
                rep.finding("C-NO-DELETION: {}-{} is absent from the imported leaves (an allocated id between "
                            "1 and {}-{} cannot be deleted; spec 8.2/8.3)".format(key, expected, key, max_present))
                break
            expected += 1


def _check_imported_append_only(prior_imported, rows, unread, rep):
    """C-HISTORY-APPEND-ONLY over the imported leaves (spec 8.3: imported records and their worklog entries
    MUST be immutable after publication; a correction is a fresh import run). Every imported id in the prior
    committed snapshot must still be present with an unchanged canonical body digest (_record_digest, the
    digest the prior carries). A prior that carries no imported baseline is a named CANNOT-EVALUATE, never a
    silent pass; an id whose leaf is unread is skipped, its CANNOT-EVALUATE already recorded."""
    if prior_imported is None:
        rep.cant("C-HISTORY-APPEND-ONLY: the prior committed snapshot carries no imported-series baseline; "
                 "imported immutability across time (spec 8.3) is not evaluated")
        return
    current = {}
    for _key, rid, row in rows:
        current.setdefault(rid, row)
    for rid in sorted(prior_imported):
        if parse_series_id(rid)[0] in unread:
            continue
        row = current.get(rid)
        if row is None:
            rep.finding("C-HISTORY-APPEND-ONLY: prior imported record {!r} is absent from the imported leaves "
                        "(imported records are immutable after publication; spec 8.3)".format(rid))
            continue
        try:
            digest = _record_digest(row)
        except _opf_emit.EmitError as exc:
            rep.cant("C-HISTORY-APPEND-ONLY: cannot canonicalize imported record {!r} to compare against the "
                     "prior digest ({})".format(rid, exc))
            continue
        if digest != prior_imported[rid]:
            rep.finding("C-HISTORY-APPEND-ONLY: imported record {!r} changed since the prior committed snapshot "
                        "(imported records and their worklog entries are immutable after publication; a "
                        "correction is a fresh import run, spec 8.3)".format(rid))


def _verify_immutable_body(rid, cur, prior_digests, rep):
    """C-HISTORY-RESURRECTION: a created-terminal record (done / worklog / autonomous_decision / reference)
    is immutable in BODY too (OPF-SPEC 8.5). The prior snapshot carries only (type, status), so body
    preservation is verifiable only when the prior supplies a body digest for the record: a changed body is
    then a finding, and an ABSENT digest is a named CANNOT-EVALUATE (a disclosed boundary), never a silent
    VALID on a rewritten immutable body (codex-3)."""
    pdig = prior_digests.get(rid) if isinstance(prior_digests, dict) else None
    if not isinstance(pdig, str):
        rep.cant("C-HISTORY-RESURRECTION: immutable record {!r} body preservation (spec 8.5) cannot be "
                 "verified; the prior snapshot carries no body digest for it (a disclosed boundary)".format(
                     rid))
        return
    body = getattr(cur, "body", None)
    if not isinstance(body, dict):
        rep.cant("C-HISTORY-RESURRECTION: immutable record {!r} body is unavailable to compare against the "
                 "prior digest".format(rid))
        return
    try:
        cur_dig = _record_digest(body)
    except _opf_emit.EmitError as exc:
        rep.cant("C-HISTORY-RESURRECTION: cannot canonicalize the current body of immutable record {!r} to "
                 "compare against the prior digest ({})".format(rid, exc))
        return
    if cur_dig != pdig:
        rep.finding("C-HISTORY-RESURRECTION: immutable record {!r} body changed since the prior committed "
                    "snapshot (a created-terminal record does not change; spec 8.5)".format(rid))


def _check_resurrection(prior_records, prior_digests, by_id, all_ids, rep):
    """C-HISTORY-RESURRECTION: for every id in the prior committed snapshot, confirm it did not resurrect
    across time (OPF-SPEC 8.4/13). A prior unqualified-terminal state now in any other state is a definite
    finding (actor-independent). A created-terminal record's BODY is additionally checked for preservation
    (immutable-body, spec 8.5; codex-3). A non-terminal status change is judged across ALL four actor kinds:
    legal for every actor passes; illegal for every actor is a finding; a transition legal for some actors
    but illegal for others is actor-DEPENDENT and, since the last-transition actor is not identifiable from
    the envelope, is CANNOT-EVALUATE (never accept-if-any-actor; codex-6); a rejection-shaped transition
    needing a pre-proposal state the snapshot cannot supply is likewise CANNOT-EVALUATE. Every durable prior
    id must remain present in active or archive (the across-time complement of the no-deletion check)."""
    for rid in sorted(prior_records):
        ptype, pstatus = prior_records[rid]
        if rid not in all_ids:
            rep.finding("C-HISTORY-RESURRECTION: prior record {!r} is absent from both the active store and "
                        "the archive (a durable id cannot vanish; spec 12/13)".format(rid))
            continue
        cur = by_id.get(rid)
        if cur is None:
            continue
        spec = BASELINE_SPECS.get(ptype)
        if spec is None:
            rep.cant("C-HISTORY-RESURRECTION: prior record {!r} names unsupported type {!r}".format(rid, ptype))
            continue
        pparsed, perr = parse_status(pstatus, spec)
        if pparsed is None:
            rep.cant("C-HISTORY-RESURRECTION: prior record {!r} status {!r} is unparseable ({})".format(
                rid, pstatus, perr))
            continue
        p_state, p_qual = pparsed
        immutable_body = not spec.transitions     # created-terminal type: body-immutable (spec 8.5)
        terminal_unqual = p_state in spec.terminal and p_qual is None
        cur_status = _status_string(cur.state, cur.qual)
        status_changed = cur_status is not None and cur_status != pstatus
        if terminal_unqual and status_changed:
            rep.finding("C-HISTORY-RESURRECTION: prior record {!r} was at unqualified terminal {!r} but is "
                        "now {!r}; a terminal record does not change (no resurrection; spec 8.4)".format(
                            rid, pstatus, cur_status))
            continue
        if immutable_body:
            # a created-terminal record keeps its status AND its body; verify the body against the prior
            # digest, or record the disclosed cannot-evaluate boundary when none is supplied (codex-3).
            _verify_immutable_body(rid, cur, prior_digests, rep)
            continue
        if cur_status is None or not status_changed:
            continue     # an unparseable current status is the schema check's; an unchanged status is clean
        statuses = [validate_transition(ptype, pstatus, cur_status, ak).status for ak in ACTOR_KINDS]
        if all(s == VALID for s in statuses):
            continue
        if all(s == INVALID for s in statuses):
            rep.finding("C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} is illegal for "
                        "every actor kind (spec 8.4/8.5)".format(rid, pstatus, cur_status))
            continue
        if any(s == CANNOT_EVALUATE for s in statuses):
            rep.cant("C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} cannot be verified "
                     "without the pre-proposal state (spec 8.4)".format(rid, pstatus, cur_status))
            continue
        rep.cant("C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} is legal for some actor "
                 "kinds but illegal for others (actor-dependent); the last-transition actor is not "
                 "identifiable from the prior snapshot (spec 8.4/8.5)".format(rid, pstatus, cur_status))


# Homes-2 durable evidence (C-EVIDENCE-ENUM, spec 4.2). Each imported/<kind>/<run-id>/ bundle carries
# its own immutable inventories, which its writer derives from the run's transaction record or receipt,
# so an evidence commit changes only its bundle folder. Ingest publication writes the base inventory
# and a promotion phase inventory claiming its receipt. Inventories establish membership, not
# authenticated actor history, and doctor reads no journal to reconcile them.

def _evidence_claim(bundle, kind, run_id, path):
    """Validate one inventory row path against what its bundle may claim: a member of the bundle itself
    other than a bundle-root inventory, a default Move destination, or, for an adoption bundle, a retire
    preimage of the same run."""
    _opf_store._home_file(path)
    if path.startswith(bundle + "/"):
        member = path[len(bundle) + 1:]
        if "/" not in member and _opf_store.is_evidence_inventory_name(member):
            raise ValueError("an inventory cannot list a bundle inventory: {!r}".format(path))
        return
    if path.startswith(_opf_store.ARCHIVE_REL + "/moved/"):
        return
    if kind == "adoption" and path.startswith("{}/adoption/{}/".format(_opf_store.ARCHIVE_REL, run_id)):
        return
    raise ValueError("bundle {!r} cannot claim {!r}".format(bundle, path))


class _LegacyIngestInventory(ValueError):
    """Recognized unsupported format: a named finding, not an unreadable inventory."""


def _evidence_rows(bundle, kind, run_id, doc):
    """Shared schema/path validation for doctor and the adoption apply shell; never upgrades old bytes."""
    if isinstance(doc, dict) and doc.get("format") == "opf.ingest.evidence-inventory/v1":
        raise _LegacyIngestInventory("legacy-ingest-inventory: old-format ingest inventory is unsupported; "
                                     "refused without migration or rewrite")
    if (not isinstance(doc, dict) or set(doc) != {"format", "file"}
            or doc["format"] != EVIDENCE_FORMAT or not isinstance(doc["file"], list)):
        raise ValueError("an inventory holds exactly format {!r} and a file array".format(EVIDENCE_FORMAT))
    for row in doc["file"]:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise ValueError("file rows require exactly path, size and sha256")
        _evidence_claim(bundle, kind, run_id, row["path"])
        if type(row["size"]) is not int or row["size"] < 0:
            raise ValueError("size must be a nonnegative integer")
        if not isinstance(row["sha256"], str) or not _HEX64_RE.fullmatch(row["sha256"]):
            raise ValueError("sha256 must be 64 lowercase hex digits")
    return doc["file"]


def _check_evidence(root_fd, homes, rep):
    """C-EVIDENCE-ENUM: reconcile the homes-2 evidence homes against their per-bundle inventories.

    A legacy (homes 1) store has no evidence homes: nothing is read, and C-CONTAINMENT grades those
    paths as before. In homes 2 every payload file under imported/ and archive/ (every file other than a
    bundle-root inventory) must be claimed by exactly one inventory row and match its recorded size and
    digest; each inventory is itself schema-checked. Unlisted or unclaimed entries, a bundle with no
    inventory, missing listed files, and a recognized legacy ingest inventory are findings.
    An unreadable or malformed input, and a bundle with
    a phase inventory but no inventory.toml, cannot evaluate; a malformed inventory stops the
    reconciliation, since its claims are unknown. Deleting a whole bundle, inventory and payload
    together, is outside this local snapshot check; history coverage is separate. Reads use the contained
    readers and their per-file cap, with a bounded walk.
    """
    if homes < 2:
        return
    imported, archive = _opf_store.IMPORTED_REL, _opf_store.ARCHIVE_REL
    expected = {}
    bundles = []
    budget = [0]
    failed = [False]

    def listing(rel, depth, required):
        # A directory reached through its parent's listing is required: its absence is a race.
        if depth > _CONTAINMENT_MAX_DEPTH:
            rep.cant("C-EVIDENCE-ENUM: depth ceiling at {!r}".format(rel))
            return None, None
        try:
            subdirs, files = _list_contained(root_fd, rel)
        except StoreError as exc:
            rep.cant("C-EVIDENCE-ENUM: cannot list {}: {}".format(_safe_display(rel), _safe_display(str(exc))))
            return None, None
        if subdirs is None:
            if required:
                rep.cant("C-EVIDENCE-ENUM: listed directory {!r} vanished".format(rel))
            return None, None
        budget[0] += len(subdirs) + len(files)
        if budget[0] > (1 << 20):
            rep.cant("C-EVIDENCE-ENUM: entry ceiling at {!r}".format(rel))
            return None, None
        return subdirs, files

    def strays(rel, names, what):
        for name in names or ():
            rep.finding("C-EVIDENCE-ENUM: off-inventory {} {!r}".format(what, _rel(rel, name)))

    def read_inventory(bundle, kind, run_id, rel):
        doc, state = _read_toml(root_fd, rel, rep)
        if state != "ok":
            if state == "absent":
                rep.cant("C-EVIDENCE-ENUM: listed inventory {!r} vanished".format(rel))
            failed[0] = True
            return
        try:
            for row in _evidence_rows(bundle, kind, run_id, doc):
                if row["path"] in expected:
                    raise ValueError("{!r} is claimed more than once".format(row["path"]))
                expected[row["path"]] = row
        except _LegacyIngestInventory as exc:
            rep.finding("C-EVIDENCE-ENUM: {} (inventory {!r})".format(exc, rel))
            failed[0] = True
        except (TypeError, ValueError) as exc:
            rep.cant("C-EVIDENCE-ENUM: malformed inventory {!r}: {}".format(rel, exc))
            failed[0] = True

    kinds, loose = listing(imported, 0, False)
    strays(imported, loose, "file")
    for kind in kinds or ():
        kind_rel = _rel(imported, kind)
        if kind not in _opf_store.STAGING_KINDS:
            rep.finding("C-EVIDENCE-ENUM: unknown evidence kind directory {!r}".format(kind_rel))
            continue
        runs, loose = listing(kind_rel, 1, True)
        strays(kind_rel, loose, "file")
        for run_id in runs or ():
            try:
                bundle = _opf_store.evidence_run(kind, run_id)
            except ValueError:
                rep.finding("C-EVIDENCE-ENUM: invalid evidence bundle directory {!r}".format(
                    _rel(kind_rel, run_id)))
                continue
            subdirs, files = listing(bundle, 2, True)
            if subdirs is None:
                continue
            inventories = [name for name in files if _opf_store.is_evidence_inventory_name(name)]
            if not inventories:
                rep.finding("C-EVIDENCE-ENUM: evidence bundle {!r} has no inventory".format(bundle))
            elif "inventory.toml" not in inventories:
                # A later phase's inventory never stands in for the first: the bundle's founding claims
                # are unknown, so the reconciliation cannot evaluate rather than grade the phase alone.
                rep.cant("C-EVIDENCE-ENUM: evidence bundle {!r} has a phase inventory but no "
                         "inventory.toml".format(bundle))
                failed[0] = True
            for name in inventories:
                read_inventory(bundle, kind, run_id, _rel(bundle, name))
            bundles.append((bundle, subdirs, [name for name in files if name not in inventories]))
    if failed[0]:
        return    # refused inventories have unknown claims; keep their recorded grade, never reconcile partially
    directories = set()
    for path in expected:
        parent = path.rsplit("/", 1)[0]
        while parent not in directories and parent != WORKING_DIRNAME:
            directories.add(parent)
            parent = parent.rsplit("/", 1)[0]
    seen = set()

    def members(rel, depth, subdirs=None, files=None):
        if subdirs is None:
            subdirs, files = listing(rel, depth, True)
        for name in files or ():
            full = _rel(rel, name)
            if full not in expected:
                rep.finding("C-EVIDENCE-ENUM: off-inventory file {!r}".format(full))
                continue
            raw, status = _read_bytes(root_fd, full, rep)
            if status == "absent":
                continue    # reported below with every other missing listed file
            seen.add(full)
            row = expected[full]
            if status == "ok" and (len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]):
                rep.finding("C-EVIDENCE-ENUM: size or digest mismatch {!r}".format(full))
        for name in subdirs or ():
            full = _rel(rel, name)
            if full in directories:
                members(full, depth + 1)
            else:
                rep.finding("C-EVIDENCE-ENUM: off-inventory directory {!r}".format(full))

    for bundle, subdirs, files in bundles:
        members(bundle, 2, subdirs, files)
    tops, loose = listing(archive, 0, False)
    strays(archive, loose, "file")
    for name in tops or ():
        top = _rel(archive, name)
        if name == "moved":
            members(top, 1)
        elif name == "adoption":
            runs, loose = listing(top, 1, True)
            strays(top, loose, "file")
            strays(top, [r for r in runs or () if _rel(top, r) not in directories], "directory")
            for run_id in runs or ():
                if _rel(top, run_id) in directories:
                    members(_rel(top, run_id), 2)
        else:
            rep.finding("C-EVIDENCE-ENUM: off-inventory directory {!r}".format(top))
    for path in sorted(set(expected) - seen):
        rep.finding("C-EVIDENCE-ENUM: missing file {!r}".format(path))


# Imported-series provenance (C-IMPORTED-PROVENANCE, spec 8.3/8.8). An imported record's import table names the
# preserved original it was read from, and the import writer publishes that original in its own run's bundle,
# .working/imported/import/<run-id>/originals/<source-path>, beside the bundle's inventory.toml.
_IMPORT_EVIDENCE_HOME = _rel(_opf_store.IMPORTED_REL, "import")
_IMPORT_ORIGINALS_DIRNAME = "originals"


def _is_preserved_original(run_id, source):
    """True when `run_id` is an import run id and `source` is a canonical store-relative file path inside that
    run's preserved-originals folder, .working/imported/import/<run-id>/originals/<source-path> (spec 8.3/8.8).
    A path anywhere else, another run's originals included, never anchors a record's provenance."""
    try:
        originals = _rel(_opf_store.evidence_run("import", run_id), _IMPORT_ORIGINALS_DIRNAME)
        _opf_store._home_file(source)
    except ValueError:
        return False
    return source.startswith(originals + "/")


def _read_bundle_inventory(root_fd, kind, run_id, check_id, rep, rel=None, listed=None):
    """Read one inventory of the `kind` evidence bundle `run_id`, its inventory.toml unless `rel` names a phase
    inventory at the bundle root, through the contained reader and validate it with the shared evidence-row
    rules (_evidence_rows: the closed opf.evidence.inventory/v1 shape and the paths the bundle may claim). Its
    rows join `listed` when given, so a bundle's inventories share one claim map. Returns (rows by path, "ok"),
    (None, "absent"), or (None, "error") with a CANNOT-EVALUATE already recorded under `check_id`: an unreadable
    or malformed inventory, a recognized legacy ingest inventory, or a path claimed twice leaves the bundle's
    claims unknown (spec 4.2).

    A legacy ingest inventory is a CANNOT-EVALUATE here, where C-EVIDENCE-ENUM records the named
    legacy-ingest-inventory finding. That finding is C-EVIDENCE-ENUM's own, a homes-2 check this reader does not
    stand in for, and spec 4.2 sets no homes-1 grade for such a bundle. These readers need the bundle's claims
    to register or verify its files, and a refused legacy inventory has none they can read, so they fail
    closed on an unknown rather than record another check's finding."""
    rel = _opf_store.evidence_inventory(kind, run_id) if rel is None else rel
    doc, state = _read_toml(root_fd, rel, rep)
    if state != "ok":
        return None, state
    listed = {} if listed is None else listed
    try:
        for row in _evidence_rows(_opf_store.evidence_run(kind, run_id), kind, run_id, doc):
            if row["path"] in listed:
                raise ValueError("{!r} is claimed more than once".format(row["path"]))
            listed[row["path"]] = row
    except (TypeError, ValueError) as exc:
        rep.cant("{}: malformed {} bundle inventory {!r}: {}".format(check_id, kind, rel, exc))
        return None, "error"
    return listed, "ok"


def _check_imported_provenance(root_fd, rows, unread, rep):
    """C-IMPORTED-PROVENANCE (spec 8.3/14.1): re-verify the provenance of every imported row the per-series read
    gathered (`rows`, the (counter key, id, row) triples of _gather_imported_rows, then its misfiled triples,
    which name a row outside its leaf series by its leaf and index) against the preserved evidence.

    Each row's import table must name an import run id, a source inside that run's originals/ folder, and a
    64-hex source_sha256; any other provenance is a finding. For each distinct (run, source) pair the original is
    read once, contained, no-follow and under the product-file cap (_read_bytes), every distinct source_sha256 its
    rows record must equal the digest of those bytes, and the original must be listed by its bundle's
    inventory.toml. Each cited bundle's inventory.toml is re-read, and every file it lists is re-read against its
    recorded size and digest, so the check needs no other layer: C-EVIDENCE-ENUM reads nothing on homes 1 (spec
    4.2/14.1), and on homes 2 it also reconciles the whole evidence tree. The driver calls this for every series
    the containment flag admits (_opf_store.imported_series_declared), on homes 1 as on homes 2.

    Mismatched evidence is a finding. Missing or unreadable evidence (an absent original or listed file, a
    symlink or other non-regular entry, an oversize file, or an absent, unreadable or malformed inventory) is a
    CANNOT-EVALUATE, never a pass (spec 8.3: missing or unreadable evidence fails closed). A series whose leaf
    could not be read (`unread`) is named as not evaluated rather than passed."""
    for key in sorted(unread):
        rep.cant("C-IMPORTED-PROVENANCE: the provenance of the {} records is not evaluated, because their "
                 "imported leaf could not be read (spec 8.3)".format(key))
    cited = {}
    for _key, rid, row in rows:
        imp = row.get("import") if isinstance(row, dict) else None
        run_id, source, claim = ((imp.get("run"), imp.get("source"), imp.get("source_sha256"))
                                 if isinstance(imp, dict) else (None, None, None))
        if not (isinstance(claim, str) and _HEX64_RE.fullmatch(claim) and _is_preserved_original(run_id, source)):
            rep.finding("C-IMPORTED-PROVENANCE: imported record {!r} carries no verifiable provenance: its import "
                        "table must name an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run, a source under that run's "
                        "{}/<run-id>/{}/ folder, and a 64-hex source_sha256 (spec 8.3/8.8)".format(
                            rid, _IMPORT_EVIDENCE_HOME, _IMPORT_ORIGINALS_DIRNAME))
            continue
        cited.setdefault(run_id, {}).setdefault(source, {}).setdefault(claim, []).append(rid)
    measured = {}

    def measure(path):
        # Each evidence path is read once, so an original its inventory also lists is never read or named twice.
        if path not in measured:
            raw, status = _read_bytes(root_fd, path, rep)
            if status == "absent":
                rep.cant("C-IMPORTED-PROVENANCE: evidence file {!r} is missing, so it cannot be verified (spec "
                         "8.3: missing evidence fails closed)".format(path))
            measured[path] = (len(raw), hashlib.sha256(raw).hexdigest()) if status == "ok" else None
        return measured[path]

    for run_id in sorted(cited):
        inventory = _opf_store.evidence_inventory("import", run_id)
        listed, state = _read_bundle_inventory(root_fd, "import", run_id, "C-IMPORTED-PROVENANCE", rep)
        if state == "absent":
            rep.cant("C-IMPORTED-PROVENANCE: {!r} is missing, so the bundle its imported records cite cannot be "
                     "verified (spec 8.3/8.8)".format(inventory))
        for path in sorted(listed or ()):
            got = measure(path)
            if got is not None and got != (listed[path]["size"], listed[path]["sha256"]):
                rep.finding("C-IMPORTED-PROVENANCE: {!r} does not match the size and digest {!r} records for it "
                            "(spec 4.2/14.1)".format(path, inventory))
        for source in sorted(cited[run_id]):
            claims = cited[run_id][source]
            if listed is not None and source not in listed:
                rep.finding("C-IMPORTED-PROVENANCE: the preserved original {!r} cited by {} is not listed in {!r} "
                            "(spec 8.8)".format(source, ", ".join(repr(r) for r in sorted(
                                {r for rids in claims.values() for r in rids})), inventory))
            got = measure(source)
            if got is None:
                continue
            for claim in sorted(claims):
                if claim != got[1]:
                    rep.finding("C-IMPORTED-PROVENANCE: {} record source_sha256 {} for {!r}, but the preserved "
                                "original hashes to {} (spec 8.3)".format(
                                    ", ".join(repr(r) for r in claims[claim]), claim, source, got[1]))


# --- containment (C-CONTAINMENT, OPF-SPEC 14.2) ------------------------------------------------------

# The pure classification `classify_containment` returns, the SINGLE authority `_check_containment`
# (below) consumes, so a future reader of adoption content cannot diverge from it by construction
# (F10-1). `view_targets` is the store-scope contained view spec
# destinations (managed leaves, matched by equality); `valid_unmanaged` the surviving [unmanaged]
# declarations (canonicalized, contained-only, collision-filtered, matched by subtree containment);
# `malformed` / `colliding` the messages for a malformed entry (a CANNOT-EVALUATE) and a colliding
# declaration (a FINDING; it covers NOTHING), which each caller re-emits or raises in its own idiom;
# `managed_file` the tree-walk managed test; and `layout` / `perrecord_body_dirs` / `archive_root` /
# `imports_root` the derived structures the walk needs; `homes` the store's active homes generation and
# `control_roots` / `evidence_roots` the store control and homes-2 evidence roots it registers.
# `worklog_errors` holds generation failures separately from malformed [unmanaged] entries.
# `evidence_homes` holds the evidence homes a homes-1 store registers once it declares the imported series
# under supporting tooling (_evidence_homes: the walk registers their contents from the bundle inventories),
# or is empty.
ContainmentClassification = collections.namedtuple(
    "ContainmentClassification",
    ("view_targets", "valid_unmanaged", "malformed", "colliding", "managed_file", "layout",
     "perrecord_body_dirs", "archive_root", "imports_root", "control_roots", "evidence_roots", "homes",
     "worklog_errors", "evidence_homes"))

# The homes-1 evidence registration (spec 4.2/8.8): activated tooling registers on homes 1 the evidence
# bundles .working/imported/<kind>/<run-id>/, the adoption archive .working/archive/adoption/<run-id>/ and the
# Move destination .working/archive/moved/. A file there registers only when a bundle inventory claims it.
_ADOPTION_ARCHIVE_HOME = _rel(_opf_store.ARCHIVE_REL, "adoption")
_MOVED_HOME = _rel(_opf_store.ARCHIVE_REL, "moved")


def _evidence_homes():
    """The homes-1 evidence homes, each a graded container: one .working/imported/<kind> home per evidence
    kind (the vocabulary is read at call time), then the adoption archive and the Move destination."""
    return (tuple(_rel(_opf_store.IMPORTED_REL, kind) for kind in _opf_store.STAGING_KINDS)
            + (_ADOPTION_ARCHIVE_HOME, _MOVED_HOME))


def classify_containment(manifest_data, machine_rel, imported=False):
    """Pure, side-effect-free derivation of the C-CONTAINMENT managed-set classification from the manifest
    and the machine-store relpath (spec 14.2). It does NO I/O and holds no `rep`: a malformed [unmanaged]
    entry is collected into `malformed` (the checker re-emits it as a CANNOT-EVALUATE) and a colliding
    declaration into `colliding` (the checker re-emits it as a C-CONTAINMENT finding; a colliding
    declaration covers NOTHING). This is the SINGLE authority `_check_containment` derives its
    adoption-content managed set from, so a second consumer cannot diverge from it by construction (F10-1).
    `enabled_types` and `layout` are derived internally from `manifest_data` by the SAME pure helpers the
    caller uses (D2), so the classification cannot drift from the manifest the caller validated.
    `imported` is the EXPLICIT imported-series registration flag (spec 8.2/4.2): the caller derives it
    through _opf_store.imported_series_declared, which requires BOTH the declared spec_version AND
    this tooling's own support (SUPPORTED_SPEC_VERSION at or above IMPORTED_SPEC_VERSION), so it is
    False for every store this tooling validates today, the accepted 2.0.0 / homes = 2 reservation
    included. A homes-1 store can set it, and the doctor driver grades the imported series under the
    same derived value, so the leaves registered here are the leaves graded. When True, the imported
    managed leaves join the managed set, and on a homes-1 store each
    evidence home (_evidence_homes: every .working/imported/<kind>, .working/archive/adoption and
    .working/archive/moved) becomes a graded container (`evidence_homes`) whose contents the walk
    registers from the bundle inventories; the default False keeps every current store's
    grading unchanged, so the registration ships inert until activation raises
    SUPPORTED_SPEC_VERSION to 1.3.0. A known gap, left for a later change: on such a homes-1 store an
    [unmanaged] declaration that equals or lies within .working/staging/ or .working/journals/
    (.working/staging, .working/journals/record/x), or that lies within .working/imported/ or
    .working/archive/ and names, contains and sits inside none of the evidence homes
    (.working/imported/unknown, .working/archive/loose.md), is valid, and the walk skips what it covers,
    although spec 4.2 makes all four reserved children (archive/, imported/, staging/, journals/)
    store-tree control and spec 14.2 forbids an [unmanaged] declaration that equals, contains or lies
    within them. On homes 2 all four are control roots, so each such declaration collides there. Returns
    a ContainmentClassification."""
    mrel = machine_rel
    dp = manifest_data.get("opf") if isinstance(manifest_data, dict) else None
    layout = dp.get("layout") if isinstance(dp, dict) else None
    types_tbl = manifest_data.get("types") if isinstance(manifest_data, dict) else None
    enabled_types = _authoritative_types(_enabled_modules(manifest_data), types_tbl)
    view_targets = set()
    views = manifest_data.get("views") if isinstance(manifest_data, dict) else None
    if isinstance(views, dict):
        for name in views:
            if not isinstance(name, str):
                continue
            try:
                _opf_views._resolve_view(name)   # a RECOGNIZED view name (pure name check, no I/O)
            except _opf_views.ViewsError:
                # round-14 C3: an UNRECOGNIZED view name marks NO managed target, so a crafted manifest view
                # name cannot turn an arbitrary store path into a "managed" view target and defeat
                # C-CONTAINMENT; the rogue path is then graded as a stray by the walk, keeping containment
                # self-sound rather than reliant on the sibling C-VIEW-DRIFT cannot-evaluate.
                continue
            scope, dest = _opf_views._spec_destination(name)
            if scope == "store" and _is_contained_relpath(dest):
                view_targets.add(dest)
    malformed = []
    unmanaged = []
    um = manifest_data.get("unmanaged") if isinstance(manifest_data, dict) else None
    if isinstance(um, dict) and isinstance(um.get("paths"), list):
        for p in um["paths"]:
            if isinstance(p, str) and _is_contained_relpath(p):
                # Normalize to the canonical contained form so a non-canonical declaration covers the
                # legacy content it names rather than nothing (F4); this also makes the collision check
                # below see a declaration of a managed path in any spelling.
                unmanaged.append(_canonical_contained(p))
            else:
                # guard-input-soundness (round-14 C4): a malformed [unmanaged] entry (non-string, or a
                # non-contained / escaping path) is a named CANNOT-EVALUATE, not a silent drop; a malformed
                # control input is surfaced, never quietly removed.
                malformed.append(
                    "C-CONTAINMENT: [unmanaged] path entry {} is not a contained store-relative string "
                    "(spec 14.2); the unmanaged declaration cannot be evaluated".format(_safe_display(p)))
    worklog_errors = []
    try:
        worklog2 = _opf_worklog.generation(manifest_data) == 2
    except _opf_worklog.WorklogError as exc:
        worklog_errors.append("C-CONTAINMENT: worklog generation cannot be evaluated: " + str(exc))
        worklog2 = False
    ledger_names = frozenset({MANIFEST_NAME, COUNTERS_NAME, VERSION_NAME, LEASE_NAME,
                              INIT_PROVENANCE_NAME} | (set() if worklog2 else {WORKLOG_NAME}))
    # Importer namespaces (legacy_fragment) are schema-deferred and, per the decoupled D6 design, are NOT
    # declared in the manifest [types]; their type index (e.g. legacy_fragment.index.toml) is therefore a
    # managed leaf IF PRESENT even without a declaration, mirroring C-COUNTERS' optional_namespaces
    # (accepted-if-present, never required). Ledger importer types (none today) are excluded.
    importer_index_types = frozenset(IMPORTER_TYPES) - _LEDGER_TYPES
    # The imported-series managed leaves (spec 8.2/4.2): part of the managed set ONLY under the
    # explicit `imported` registration flag (never true today: imported_series_declared also requires
    # SUPPORTED_SPEC_VERSION to reach IMPORTED_SPEC_VERSION, so even the reserved 2.0.0 / homes = 2
    # declaration C-MANIFEST accepts stays inactive until activation raises it), and
    # admitted ONLY directly in the machine store. The names derive from the enabled-type roster in
    # _opf_store, so the leaf set cannot drift from the imported id/counter namespace set; the
    # importer-only legacy_fragment contributes no leaf.
    imported_leaves = _opf_store.imported_leaf_names(enabled_types) if imported else frozenset()
    archive_root = _rel(mrel, ARCHIVE_DIRNAME)
    # imports is store-scope (`.working/imports`), a SIBLING of the machine subdir (disjoint from mrel), read
    # from IMPORTS_REL, the legacy control root _opf_store reserves (spec 14.1). The walk starts at
    # `.working/`, so this root is reached and graded exactly as before, one level up.
    imports_root = IMPORTS_REL
    # A legacy store (homes 1) registers only the imports tree; the homes-2 names grade as ordinary paths.
    homes = _opf_store.homes_generation(manifest_data)
    control_roots = _opf_store.store_control_roots(homes)
    evidence_roots = (_opf_store.IMPORTED_REL, _opf_store.ARCHIVE_REL) if homes >= 2 else ()
    # The evidence homes (spec 4.2/8.8): activated tooling registers the evidence bundles, the adoption archive
    # and the Move destination on homes 1, so under the `imported` flag each is a graded container there and an
    # [unmanaged] declaration can neither name, contain, nor sit inside one. A homes-2 store already skips every
    # evidence root, which C-EVIDENCE-ENUM reconciles, so it needs none.
    evidence_homes = _evidence_homes() if imported and homes < 2 else ()

    def managed_leaf(p):
        # A STRICT managed-leaf test: a ledger, an enabled type index, a per-record body, or a declared
        # store-scope view target. It deliberately EXCLUDES the `_under_any(p, unmanaged)` clause so the
        # collision check can ask whether an unmanaged declaration names a managed leaf without the
        # declaration trivially matching itself (F2).
        if p in view_targets:
            return True
        prefix = mrel + "/"
        if p.startswith(prefix):
            r = p[len(prefix):]
            if worklog2 and r.startswith(_opf_worklog.DIRECTORY_NAME + "/"):
                return _opf_worklog.parse_worklog_filename(r.split("/", 1)[1]) is not None
            if "/" not in r:
                if r in ledger_names:
                    return True
                if r in imported_leaves:
                    # An imported-series leaf (worklog.imported.toml or <type>.imported.index.toml)
                    # is managed only HERE, directly in the machine store; a deeper path of the same
                    # name stays a stray, and while `imported` is False the set is empty, so a 1.2.0
                    # store grades these names exactly as before (spec 8.2/4.2).
                    return True
                if r.endswith(INDEX_SUFFIX):
                    t = r[:-len(INDEX_SUFFIX)]
                    # worklog is a ledger only (home: worklog.toml), so worklog.index.toml is NEVER a
                    # managed leaf (F2); a ledger type has no `<type>.index.toml`.
                    if t not in _LEDGER_TYPES and (t in enabled_types or t in importer_index_types):
                        return True
            elif layout == "per-record":
                head, tail = r.split("/", 1)
                # Generation-1 worklog has no per-record bodies (F2): entries live in worklog.toml, not in
                # worklog/<id>.toml.
                if "/" not in tail and tail.endswith(".toml") and head in enabled_types \
                        and head not in _LEDGER_TYPES \
                        and _valid_id_shape(tail[:-len(".toml")]) is not None:
                    return True
        return False

    def managed_file(p):
        # The tree-walk test: a file UNDER a VALID declared-unmanaged subtree stays covered (not flagged),
        # plus every strict managed leaf. A rejected declaration is absent from valid_unmanaged, so it
        # covers nothing (F1).
        if _under_any(p, valid_unmanaged):
            return True
        return managed_leaf(p)

    # An unmanaged declaration is a finding when it NAMES a managed leaf, when it CONTAINS one (it equals or
    # is an ancestor of the machine root, the archive or imports roots, a per-record type-body directory, or
    # a store-scope view target), or when it lies strictly WITHIN a fully-graded container (the archive or
    # imports subtree, or a per-record type-body directory) where every path is already graded as managed or
    # a finding (round-5 F-1/F-4: a declaration inside such a container would shield strays beneath it).
    # Merely lying UNDER the machine dir is otherwise fine (codex-7: a legacy file, or a legacy directory
    # outside every graded container, that names no managed slot is VALID; a file contains nothing). Only a
    # declaration that survives covers a subtree in the walk, so a REJECTED declaration never launders it.
    # Type directories are reserved in EVERY layout: in per-record a non-ledger type's dir holds its
    # <id>.toml managed leaves; a LEDGER type's dir (e.g. worklog/) must NEVER exist (its home is
    # <type>.toml); and in INLINE layout NO <type>/ dir is legal (records live in <type>.index.toml). So an
    # unmanaged declaration of, or within, any <type>/ dir is a collision in every layout (round-11
    # per-record worklog/; round-13 the inline analog). A ledger type's INDEX file (<type>.index.toml) is
    # likewise a never-legal slot and is reserved so it cannot be declared unmanaged to shield a rogue file.
    type_body_dirs = tuple(_rel(mrel, t) for t in sorted(enabled_types))
    # The per-record body dirs to RECURSE are the non-ledger type dirs only: a ledger type (worklog) keeps
    # its entries in <type>.toml, so a <ledgertype>/ dir must NEVER exist and is graded as a stray even when
    # empty, never recursed as a managed namespace where an empty one would escape grading (ROUND-2 Fable
    # F-1a). type_body_dirs (all enabled types) still drives the reservation below.
    perrecord_body_dirs = tuple(_rel(mrel, t) for t in sorted(enabled_types) if t not in _LEDGER_TYPES)
    ledger_index_files = tuple(_rel(mrel, t + INDEX_SUFFIX) for t in sorted(enabled_types)
                               if t in _LEDGER_TYPES)
    graded_containers = (archive_root,) + control_roots + type_body_dirs + evidence_homes
    managed_dir_prefixes = ((mrel, archive_root) + control_roots + type_body_dirs + ledger_index_files
                            + evidence_homes)
    colliding = []
    valid_unmanaged = []
    for u in unmanaged:
        if (managed_leaf(u)
                or any(_under_any(pfx, (u,)) for pfx in managed_dir_prefixes)
                or _under_any(u, graded_containers)
                or any(_under_any(vt, (u,)) for vt in view_targets)):
            colliding.append(
                "C-CONTAINMENT: unmanaged path {!r} collides with a managed store path (an "
                "unmanaged declaration cannot name or contain a managed file; spec 14.2)".format(u))
        else:
            valid_unmanaged.append(u)

    return ContainmentClassification(
        view_targets=view_targets, valid_unmanaged=valid_unmanaged, malformed=malformed,
        colliding=colliding, managed_file=managed_file, layout=layout,
        perrecord_body_dirs=perrecord_body_dirs, archive_root=archive_root, imports_root=imports_root,
        control_roots=control_roots, evidence_roots=evidence_roots, homes=homes,
        worklog_errors=worklog_errors, evidence_homes=evidence_homes)


# The legacy import staging area `<machine>/imports` (`.working/toml/imports` under the default layout): where
# import runs staged before OPF-IMPORTS-RELOCATE moved them to the store-scope `.working/imports`. The retired
# C-STAGING guard refused it so an old staging run surfaced for manual review and was never silently ignored;
# the guard now lives in C-CONTAINMENT, independent of the [unmanaged] machinery.
_LEGACY_IMPORTS_DIRNAME = "imports"


def _legacy_import_staging(machine_rel):
    """The legacy import staging path `<machine>/imports` and the review text its CANNOT-EVALUATE carries.
    C-CONTAINMENT refuses both an entry of that name in the machine dir (a directory, empty or not, or a
    file; a symlink or exotic entry already makes the machine-dir listing CANNOT-EVALUATE) and a surviving
    [unmanaged] declaration of the path or of a path under it, so an old staging run is surfaced for review
    by hand (no automatic migration) and an [unmanaged] declaration cannot launder it into a VALID store."""
    review = ("a legacy import staging area (the import-run location before the relocation to {!r}; the "
              "import engine is retired): review its content by hand, then remove or relocate it (no "
              "automatic migration)").format(IMPORTS_REL)
    return _rel(machine_rel, _LEGACY_IMPORTS_DIRNAME), review


def _check_containment(root_fd, machine_rel, manifest_data, import_status, rep, imported=False):
    """C-CONTAINMENT: recursively walk `.working/`, matching every regular file against the managed set
    (the ledgers, the enabled non-ledger type indexes, per-record bodies, the archive tree, declared
    store-scope view targets, and files under a valid declared [unmanaged] path). A path in neither set is
    a finding at steady state; at a SUBSTANTIATED `import_status = "partial"` (an active imports/<run-id>
    run present) it goes to `triage` instead (spec 11/14.2). The imports subtree interior is walked, not
    skipped, so a leftover run or stray bytes is graded (F3). An unmanaged declaration that names or
    contains a managed path is a finding. Listing failure is CANNOT-EVALUATE. The managed-set classification
    (view targets, the collision-filtered valid_unmanaged, and the malformed / colliding declarations) is
    derived by the pure `classify_containment`, the SINGLE authority for adoption content (F10-1).
    enabled_types / layout are derived inside that helper from the manifest (D2), so they are no longer
    passed in. `imported` is the imported-series registration flag classify_containment documents
    (_opf_store.imported_series_declared, which a homes-1 store can satisfy, the flag the driver grades the
    imported series under); the walk itself needs no imported awareness (the imported leaves are plain managed
    leaves directly in the machine store, spec 8.2/4.2), except that on a homes-1 store the flag also
    registers the evidence homes (spec 4.2/8.8): each .working/imported/<kind>/<run-id>/ bundle registers
    its inventory.toml, each inventory-<phase>.toml at its root, and the files those inventories list, in
    the bundle, under the Move destination .working/archive/moved/, or, for an adoption bundle, under its
    run's adoption archive .working/archive/adoption/<run-id>/. Every other path under .working/imported/
    and .working/archive/ stays unregistered. While IMPORTED_CONTENT_CHECKS is empty, a store that sets
    the flag is routed to CANNOT-EVALUATE (the activation tripwire below): registration without content
    grading never certifies."""
    cls = classify_containment(manifest_data, machine_rel, imported=imported)
    # The activation tripwire (spec 8.2/4.2): a store that declares the imported series under supporting
    # tooling, at either homes generation, while every imported-content check (C-IMPORTED-*) is still
    # deferred must never certify, or the imported leaves would be
    # registered as managed while nothing reads their content (the PRC-F1 N-1 blind spot, reopened
    # at activation). A CANNOT-EVALUATE, not a finding: the store is not known bad, the tooling is
    # known incomplete. The spec 1.3.0 activation lands the checks, populates
    # IMPORTED_CONTENT_CHECKS, and so disarms this.
    if imported and not IMPORTED_CONTENT_CHECKS:
        rep.cant("C-CONTAINMENT: the store declares the imported series under supporting tooling but every "
                 "imported-content check (C-IMPORTED-*) is still deferred; the imported leaves would be "
                 "certified unread, so the store cannot be evaluated (spec 8.2/4.2)")
    # Re-emit the classifier's collected messages in the ORIGINAL order (all malformed CANNOT-EVALUATEs, then
    # all colliding findings), exactly as the inline classification emitted them before the extraction.
    for msg in cls.malformed + cls.worklog_errors:
        rep.cant(msg)
    for msg in cls.colliding:
        rep.finding(msg)
    legacy_rel, legacy_review = _legacy_import_staging(machine_rel)
    for u in cls.valid_unmanaged:
        if _under_any(u, (legacy_rel,)):
            rep.cant("C-CONTAINMENT: [unmanaged] path {!r} declares {!r}, {}".format(
                u, legacy_rel, legacy_review))
    mrel = machine_rel
    view_targets = cls.view_targets
    valid_unmanaged = cls.valid_unmanaged
    managed_file = cls.managed_file
    layout = cls.layout
    perrecord_body_dirs = cls.perrecord_body_dirs
    archive_root = cls.archive_root
    imports_root = cls.imports_root
    staging_root = _opf_store.STAGING_REL
    # A legacy store keeps its legacy grading: staging/, journals/, imported/ and archive/ are ordinary
    # paths there, graded as unregistered like any other. Homes 2 registers them (spec 4.2).
    homes2 = cls.homes >= 2
    staged_roots = (imports_root, staging_root) if homes2 else (imports_root,)
    kind_roots = tuple(staging_root + "/" + k for k in _opf_store.STAGING_KINDS) if homes2 else ()
    skipped_roots = cls.evidence_roots + ((_opf_store.JOURNALS_REL,) if homes2 else ())
    evidence_homes = cls.evidence_homes
    # bundle_files and bundle_dirs are what the bundle inventories register; unread_dirs are directories whose
    # claims are unknown, never walked; claimed maps each registered path to the bundle that claims it. The
    # climb from a registered file stops at the store tree, the evidence roots or an evidence home.
    bundle_files, bundle_dirs, unread_dirs, claimed = set(), set(), set(), {}
    evidence_tops = {WORKING_DIRNAME, _opf_store.IMPORTED_REL, _opf_store.ARCHIVE_REL} | set(evidence_homes)

    def register_bundle(kind, run_id):
        # One evidence bundle registers from its own inventories (spec 4.2/8.8): inventory.toml, each
        # inventory-<phase>.toml at the bundle root, the files they list, and their directories. A row may list
        # a member of the bundle, a Move destination, or, for an adoption bundle, a file of its run's adoption
        # archive (_evidence_claim). A bundle with no inventory at all registers nothing, so the walk grades it
        # as one unregistered entry. A bundle that cannot be listed, whose phase inventory has no inventory.toml
        # to extend (a phase inventory never stands in for the first), whose inventories are unreadable or
        # malformed, or that claims a path twice, between its own inventories or against another bundle, has
        # unknown claims: a CANNOT-EVALUATE that leaves ungraded the bundle and every directory it may claim
        # into, never graded on a guess.
        bundle = _opf_store.evidence_run(kind, run_id)
        reach = (bundle, _MOVED_HOME) + ((_rel(_ADOPTION_ARCHIVE_HOME, run_id),) if kind == "adoption" else ())
        listed, state = _read_bundle_inventory(root_fd, kind, run_id, "C-CONTAINMENT", rep)
        if state == "error":
            unread_dirs.update(reach)
            return
        try:
            _subdirs, names = _list_contained(root_fd, bundle)
        except StoreError as exc:
            rep.cant("C-CONTAINMENT: cannot list evidence bundle {}: {}".format(
                _safe_display(bundle), _safe_display(str(exc))))
            unread_dirs.update(reach)
            return
        phases = [_rel(bundle, name) for name in names or ()
                  if name != "inventory.toml" and _opf_store.is_evidence_inventory_name(name)]
        if listed is None:
            if phases:
                rep.cant("C-CONTAINMENT: evidence bundle {!r} has a phase inventory but no inventory.toml, so its "
                         "claims cannot be read (spec 4.2)".format(bundle))
                unread_dirs.update(reach)
            return
        for phase in phases:
            _rows, state = _read_bundle_inventory(root_fd, kind, run_id, "C-CONTAINMENT", rep, phase, listed)
            if state != "ok":
                if state == "absent":
                    rep.cant("C-CONTAINMENT: listed inventory {!r} vanished".format(phase))
                unread_dirs.update(reach)
                return
        twice = sorted(path for path in listed if path in claimed)
        if twice:
            rep.cant("C-CONTAINMENT: evidence bundle {!r} claims {!r}, which {!r} already claims; a path claimed "
                     "twice cannot evaluate (spec 4.2)".format(bundle, twice[0], claimed[twice[0]]))
            unread_dirs.update(reach)
            return
        bundle_dirs.add(bundle)
        bundle_files.add(_opf_store.evidence_inventory(kind, run_id))
        bundle_files.update(phases)
        for path in listed:
            claimed[path] = bundle
            bundle_files.add(path)
            parent = path.rsplit("/", 1)[0]
            while parent not in bundle_dirs and parent not in evidence_tops:
                bundle_dirs.add(parent)
                parent = parent.rsplit("/", 1)[0]

    def list_claims_home(rel, reach):
        # A directory whose bundles cannot be listed leaves their claims unknown: a CANNOT-EVALUATE, and neither
        # it nor anywhere those bundles may claim into (`reach`) is walked. Returns its subdirectory names.
        try:
            subdirs, _files = _list_contained(root_fd, rel)
        except StoreError as exc:
            rep.cant("C-CONTAINMENT: cannot list evidence home {}: {}".format(
                _safe_display(rel), _safe_display(str(exc))))
            unread_dirs.update(reach)
            return ()
        return subdirs or ()

    def register_evidence():
        # Every bundle registers before the walk, so no path's grade depends on whether the walk reaches it
        # before the bundle that claims it. A folder that is not a known kind, or not a run of its kind, registers
        # nothing, so the walk grades it as one unregistered entry.
        kinds = list_claims_home(_opf_store.IMPORTED_REL,
                                 (_opf_store.IMPORTED_REL, _MOVED_HOME, _ADOPTION_ARCHIVE_HOME))
        for kind in kinds:
            if kind not in _opf_store.STAGING_KINDS:
                continue
            home = _rel(_opf_store.IMPORTED_REL, kind)
            for run_id in list_claims_home(home, (home, _MOVED_HOME) + (
                    (_ADOPTION_ARCHIVE_HOME,) if kind == "adoption" else ())):
                try:
                    _opf_store.evidence_run(kind, run_id)
                except ValueError:
                    continue
                register_bundle(kind, run_id)

    unmanaged_files = []

    def managed_dir(full):
        # A subdir that is a legitimate managed namespace to RECURSE rather than flag: the machine-store
        # root, the imports root and its run interior (graded/triaged per F3), a per-record type-body dir,
        # or an ancestor of a store-scope view target. (archive_root and valid_unmanaged subtrees are
        # handled by the caller's skip.) Anything else under the store is an UNREGISTERED directory, graded
        # as a stray even when empty (round-14: the walk previously graded files only, so an empty rogue
        # directory, or a tree of only empty dirs, escaped grading entirely).
        if full == mrel or _under_any(full, staged_roots):
            return True
        if evidence_homes and full in evidence_tops:
            return True
        if full in bundle_dirs:
            return True
        if full == _rel(mrel, _opf_worklog.DIRECTORY_NAME):
            try:
                return _opf_worklog.generation(manifest_data) == 2
            except _opf_worklog.WorklogError:
                return False
        if layout == "per-record" and full in perrecord_body_dirs:
            return True
        return any(vt == full or vt.startswith(full + "/") for vt in view_targets)

    def walk(reldir, depth):
        if depth > _CONTAINMENT_MAX_DEPTH:
            # A pathologically deep directory chain fails closed rather than overflowing the recursion
            # (B6): the deeper tree is not evaluated, so this is a CANNOT-EVALUATE, never a silent pass.
            rep.cant("C-CONTAINMENT: directory nesting under {!r} exceeds the containment-walk ceiling of "
                     "{} (fail-closed; the deeper tree is not evaluated, so no RecursionError)".format(
                         reldir, _CONTAINMENT_MAX_DEPTH))
            return
        subdirs, files = _list_dir(root_fd, reldir, rep)
        if subdirs is None and files is None:
            return
        if reldir == mrel and _LEGACY_IMPORTS_DIRNAME in subdirs + files:
            rep.cant("C-CONTAINMENT: {!r} is present and is {}".format(legacy_rel, legacy_review))
        if not subdirs and not files and reldir not in staged_roots + kind_roots \
                and _under_any(reldir, staged_roots):
            # An EMPTY directory strictly under the imports interior has no file to flag, so it would
            # escape grading entirely (round-14 graded files only; F1). Grade the directory itself as an
            # unregistered path, exactly as any other unregistered path is: a finding at steady state and
            # a triage entry under a substantiated partial (the sorted loop below routes it). The imports
            # ROOT itself, holding no runs, is a legitimate empty namespace and stays clean.
            unmanaged_files.append(reldir)
            return
        if homes2 and reldir == staging_root:
            for name in sorted(set(subdirs) - set(_opf_store.STAGING_KINDS) | set(files)):
                rep.finding("C-CONTAINMENT: invalid staging kind {!r}".format(name))
        for f in files:
            full = reldir + "/" + f
            if not (managed_file(full) or full in bundle_files):
                unmanaged_files.append(full)
        for d in subdirs:
            full = reldir + "/" + d
            # Journal contents are outside doctor coverage; only journal validation reads them.
            # Evidence bytes have their own exact inventory check, distinct from record rotation.
            if full in skipped_roots:
                continue
            if full in unread_dirs:
                continue     # the claims on it could not be read: the CANNOT-EVALUATE is recorded
            if _under_any(full, valid_unmanaged) or full == archive_root:
                continue     # a valid declared-unmanaged subtree is never read; the archive is C-ARCHIVE-ENUM's
            if not managed_dir(full):
                # An unregistered directory is itself a stray path (round-14): grade the whole subtree as
                # one unregistered entry and do not recurse. Flagging each nested file would be redundant,
                # and an EMPTY rogue directory has no file to flag at all.
                unmanaged_files.append(full)
                continue
            # The imports/<run-id> interior is NOT blanket-skipped (F3): at steady state a leftover run or
            # any stray bytes under it is an unregistered path, and at a substantiated partial the active
            # run interior goes to triage. Walking it grades every file there.
            walk(full, depth + 1)

    # codex-2: a partial status is substantiated only by an import/migration ACTUALLY running (an
    # imports/<run-id> run present; spec 11:943). Without one the partial declaration cannot license the
    # triage posture that would disable steady-state grading, so it is itself a finding and the stray paths
    # grade as findings, never a silent triage pass.
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep,
                                                                            cls.homes)
    if import_status == "partial" and not partial_active:
        rep.finding("C-CONTAINMENT: the manifest declares import_status partial but no active "
                    "imports/<run-id> run is present; a partial status is substantiated only by an import "
                    "or migration actually running (spec 11:943/14.2)")
    if evidence_homes:
        register_evidence()
    walk(WORKING_DIRNAME, 0)
    for p in sorted(unmanaged_files):
        if partial_active:
            rep.triage_path("unregistered path {!r} at the store location (an import or migration is in "
                            "progress; triage per spec 14.2)".format(p))
        else:
            rep.finding("C-CONTAINMENT: unregistered path {!r} at the store location is neither OPF-managed "
                        "nor enumerated as unmanaged (spec 14.2/11)".format(p))


def _check_lease(root_fd, machine_rel, rep):
    """C-LEASE: validate lease.toml when present (spec 5.7/11). The lease is present only WHILE HELD, so its
    ABSENCE is clean (no lease held). A present-but-unreadable/unparseable lease is CANNOT-EVALUATE naming
    it (schema validity of what exists; spec 11). A parseable lease with a malformed payload is a finding:
    the closed shape carries a schema, a non-empty holder, a non-empty operation, and an RFC 3339 UTC
    acquired-at timestamp (spec 5.7)."""
    lease_rel = _rel(machine_rel, LEASE_NAME)
    data, st = _read_toml(root_fd, lease_rel, rep)
    if st == "error":
        return                       # _read_toml already recorded the CANNOT-EVALUATE naming the input
    if st == "absent":
        return                       # no lease held: a present-only-while-held artefact is clean when absent
    if not isinstance(data, dict):
        rep.finding("C-LEASE: {} is not a table (the single-writer lease payload; spec 5.7)".format(
            lease_rel))
        return
    extra = set(data) - LEASE_TOP_KEYS
    if extra:
        rep.finding("C-LEASE: {} carries unknown key(s): {} (the closed lease shape; spec 5.7)".format(
            lease_rel, ", ".join(_sorted_key_names(extra))))
    sch = data.get("schema")
    if type(sch) is not int or sch != SUPPORTED_SCHEMA:
        rep.finding("C-LEASE: {} schema {} is not the supported version {} (spec 5.7)".format(
            lease_rel, _safe_display(sch), SUPPORTED_SCHEMA))
    holder = data.get("holder")
    if not (isinstance(holder, str) and holder):
        rep.finding("C-LEASE: {} holder must be a non-empty string (the lease holder; spec 5.7)".format(
            lease_rel))
    operation = data.get("operation")
    if not (isinstance(operation, str) and operation):
        rep.finding("C-LEASE: {} operation must be a non-empty string (spec 5.7)".format(lease_rel))
    if not _valid_timestamp(data.get("acquired_at")):
        rep.finding("C-LEASE: {} acquired_at must be an RFC 3339 UTC timestamp read from the clock "
                    "(spec 5.7)".format(lease_rel))


# --- observations (the inert git-derived facts the caller injects) -----------------------------------

def _wellformed_release_row(row):
    """True when `row` is a structurally well-formed release row: the per-row shape validate_version
    enforces (keys EXACTLY RELEASE_KEYS, a valid SemVer version, an RFC 3339 date, a well-formed
    worklog_span, and a sha256 coverage_digest). Cross-row tiling, uniqueness, and monotonicity are NOT
    applied: this decides only whether an INJECTED prior row can serve as the append-only immutability
    baseline, so a well-formed row that merely DIFFERS from the current ledger still reaches the graded
    rewrite check (a real history rewrite), while a dict of garbage keys is a malformed observation routed
    to CANNOT-EVALUATE rather than a false rewrite finding (F5; guard-input-soundness)."""
    if not isinstance(row, dict):
        return False
    if set(row) != RELEASE_KEYS:
        return False
    if parse_semver(row.get("version")) is None:
        return False
    if not _valid_timestamp(row.get("date")):
        return False
    scratch = []
    if _parse_span(row.get("worklog_span"), scratch, "prior release row") is False or scratch:
        return False
    if not _valid_digest(row.get("coverage_digest")):
        return False
    return True


def _normalize_prior(prior):
    """Validate the injected prior committed snapshot: {releases: list, counters_high: {ns: int},
    records: {id: (type, status)}, digests: {id: str} (optional), imported: {imported id: digest} (optional,
    spec 8.3)}. An "imported:<NS>" counter is returned apart as imported_high, and the imported member with
    its imported_malformed verdict, for the driver to grade where the imported series is active (spec 8.2).
    Every record id is shape-validated and
    its namespace reconciled against its declared type's roster namespace, so a malformed id or a
    namespace/type mismatch makes the WHOLE prior malformed -> CANNOT-EVALUATE naming the observation,
    never a store INVALID (codex-9/F4). Returns the normalized dict or None when any field is malformed
    (which the caller routes to CANNOT-EVALUATE, never a permissive pass)."""
    if not isinstance(prior, dict):
        return None
    releases = prior.get("releases")
    counters_high = prior.get("counters_high")
    records = prior.get("records")
    digests = prior.get("digests")
    if not isinstance(releases, list) or not isinstance(counters_high, dict) or not isinstance(records, dict):
        return None
    if digests is not None and not isinstance(digests, dict):
        return None
    # M1: validate CONTENTS, so a malformed prior routes to CANNOT-EVALUATE naming the observation rather
    # than flowing into a graded INVALID that falsely accuses the store. A release row must be a table; a
    # counter high-water must be a genuine non-negative int (never a bool); a record value must be a
    # (type, status) pair of strings.
    for row in releases:
        if not _wellformed_release_row(row):
            return None
    norm_high = {}
    imported_high = {}
    for ns, val in counters_high.items():
        # An "imported:<NS>" counter over the imported namespaces is held apart (spec 8.2): the driver grades
        # it only where the imported series is active and reads it as a malformed prior everywhere else.
        imported_key = (isinstance(ns, str) and ns.startswith(IMPORTED_PREFIX)
                        and ns[len(IMPORTED_PREFIX):] in IMPORTED_NAMESPACES)
        if not isinstance(ns, str) or (ns not in _ROSTER_NS_SET and not imported_key):
            return None
        if not (isinstance(val, int) and not isinstance(val, bool) and val >= 0):
            return None
        (imported_high if imported_key else norm_high)[ns] = val
    norm_records = {}
    for rid, val in records.items():
        if not isinstance(rid, str):
            return None
        if not (isinstance(val, (list, tuple)) and len(val) == 2
                and isinstance(val[0], str) and isinstance(val[1], str)):
            return None
        # codex-9/F4: the id must be a valid shape AND its namespace must reconcile against the declared
        # type's roster namespace; a malformed id or a known-type namespace mismatch makes the WHOLE prior
        # malformed (CANNOT-EVALUATE naming it), never a store INVALID (resurrection / durable-id-vanished).
        shape = _valid_id_shape(rid)
        if shape is None:
            return None
        expected_ns = _ROSTER_NAMESPACES.get(val[0])
        if expected_ns is not None and shape[0] != expected_ns:
            return None
        norm_records[rid] = (val[0], val[1])
    norm_digests = {}
    if isinstance(digests, dict):
        for did, dval in digests.items():
            if not isinstance(did, str) or not isinstance(dval, str):
                return None
            norm_digests[did] = dval
    # The optional `imported` member: {imported id: canonical body digest} of the imported leaves at the
    # prior commit (spec 8.3). Malformed contents are recorded, not refused here: the member was ignored
    # before the imported series, and the driver refuses it only where the series is active.
    imported = prior.get("imported")
    norm_imported, imported_malformed = None, False
    if imported is not None:
        norm_imported = {}
        if not isinstance(imported, dict):
            imported_malformed = True
        else:
            for iid, dval in imported.items():
                shape = parse_series_id(iid)
                if (shape is None or shape[0] == shape[1] or shape[1] not in IMPORTED_NAMESPACES
                        or not isinstance(dval, str)):
                    imported_malformed = True
                    break
                norm_imported[iid] = dval
    return {"releases": list(releases), "counters_high": norm_high, "records": norm_records,
            "digests": norm_digests, "imported_high": imported_high, "imported": norm_imported,
            "imported_malformed": imported_malformed}


def _normalize_observations(observations):
    """Validate the injected, inert observations object (all fields optional). Returns (obs, error): `obs`
    is a plain dict holding only the recognized, shape-checked fields, and `error` is None on success or a
    fail-closed message when the object itself is malformed. A recognized field present but malformed is
    DROPPED so its check routes to CANNOT-EVALUATE naming the missing input (guard-input-soundness)."""
    if observations is None:
        return {}, None
    if not isinstance(observations, dict):
        return {}, ("observations must be an inert table of git-derived facts, not {} (fail-closed; a "
                    "malformed observation is cannot-evaluate)".format(type(observations).__name__))
    unknown = set(observations) - _OBSERVATION_KEYS
    if unknown:
        # F12: an unrecognized observation key is a malformed injection (a typo silently demotes an
        # intended observation to "absent"); surface it fail-closed rather than dropping it.
        return {}, ("observations carries unrecognized key(s): {} (fail-closed; an unknown observation "
                    "key is a malformed injection, never silently dropped)".format(
                        ", ".join(_sorted_key_names(unknown))))
    obs = {}
    tracked = observations.get("tracked")
    if isinstance(tracked, str) and tracked in ("tracked", "untracked"):
        obs["tracked"] = tracked
    elif "tracked" in observations:
        obs["tracked_malformed"] = True     # supplied but rejected: the cant says "malformed", not "absent"
    actual_remote = observations.get("actual_remote")
    if isinstance(actual_remote, str):
        obs["actual_remote"] = actual_remote
    elif "actual_remote" in observations:
        obs["actual_remote_malformed"] = True
    prior = observations.get("prior")
    if prior is not None:
        norm = _normalize_prior(prior)
        if norm is not None:
            obs["prior"] = norm
        else:
            obs["prior_malformed"] = True    # supplied but rejected; the history checks say "malformed"
    return obs, None


# --- the whole-store validator -----------------------------------------------------------------------

def _attribute_manifest_failure(rep, exc):
    """Revisit manifest attribution without registering C-MANIFEST a second time."""
    current = rep._current
    rep._current = "C-MANIFEST"
    try:
        if isinstance(exc, _opf_worklog.ManifestValidationError):
            if exc.status == CANNOT_EVALUATE:
                rep.cant("{}: {}".format(exc.relpath, exc))
            else:
                for finding in exc.findings:
                    rep.finding("manifest: {}".format(finding))
        elif isinstance(exc, _opf_worklog.ManifestShapeError):
            rep.cant("{} is absent (the store manifest is required; spec 4.5)".format(exc.relpath)
                     if exc.missing else "{}: {}".format(exc.relpath, exc))
        else:
            rep.cant("cannot read {}: {}".format(exc.relpath, exc))
    finally:
        rep._current = current


def validate_store(resolution, supported_profiles=None, *, observations=None, ancestral_floor=None):
    """Validate a RESOLVED store's whole-store integrity (OPF-SPEC 11). `resolution` is the object
    _opf_store.resolve_store returns; a resolution that is not RESOLVED is CANNOT-EVALUATE. `observations`
    is the inert, all-optional git-derived-facts object the git-aware caller (the future doctor verb; the
    self-test now) injects: `tracked` ("tracked"/"untracked"), `actual_remote` (the store repository's real
    push URL), and `prior` (the prior committed snapshot, already parsed). The module reads every declared
    input through U1's contained no-follow readers and reuses the U2/U3/U4/U5/U8 primitives; it owns the
    cross-record, historical, archive, view, changelog, and topology invariants no single-record or
    single-ledger pass can see. Returns a StoreValidation (VALID / INVALID / CANNOT-EVALUATE) plus the
    per-check verdicts, the partial-import triage surface, the profile scope, and the disclosed residuals.
    `ancestral_floor` is the {namespace: high-water} a B6 re-adoption seeded its counters from (the init
    health gate passes its validated plan's seed); ids at or below it are ancestral, never restored, so
    C-NO-DELETION does not read their absence as a deletion. None (every other caller) is a zero floor.
    """
    rep = _Report()
    if resolution is None or getattr(resolution, "status", None) != RESOLVED:
        got = getattr(resolution, "status", None)
        rep.cant("store is not resolved (status {!r}); nothing to validate".format(got))
        return rep.result()
    machine_rel = resolution.machine_rel
    if not isinstance(machine_rel, str) or not machine_rel:
        rep.cant("resolved store carries no machine-store path")
        return rep.result()
    obs, obs_err = _normalize_observations(observations)
    if obs_err is not None:
        rep.cant(obs_err)
    try:
        root_fd = _open_store_root_fd(resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        rep.cant("cannot open the resolved store root {} ({})".format(resolution.store_root, exc))
        return rep.result()
    product_root = getattr(resolution, "product_root", None)
    product_root_fd = None
    if product_root is not None:
        try:
            product_root_fd = _open_root_fd(product_root)
        except OSError:
            product_root_fd = None     # the product-scope checks route to cannot-evaluate naming the input
    pointer_target = getattr(resolution, "target", None)   # the parsed committed/override pointer Target
    pointer_source = getattr(resolution, "pointer_source", None)   # "default"/"committed"/"local-override"
    # Topology is classified by RESOLVED LOCATION, not pointer provenance: a store whose root IS the product
    # root (the default, or a self-pointer such as `dir:.`) is IN-REPO and rides the product remote, never a
    # relocated local-only store (codex-8).
    in_repo = pointer_source == "default"
    if not in_repo and product_root is not None:
        try:
            in_repo = os.path.samefile(resolution.store_root, product_root)
        except OSError:
            in_repo = False
    evaluated_profiles, unevaluated_profiles = [], []
    try:
        evaluated_profiles, unevaluated_profiles = _validate_opened_store(
            root_fd, product_root_fd, machine_rel, supported_profiles, obs, pointer_target, pointer_source,
            in_repo, rep, ancestral_floor)
    except Exception as exc:
        # Top-level fail-closed barrier (B6): any unexpected error (a RecursionError from a hostile-shaped
        # store, or any other escape no specific guard anticipated) becomes a CANNOT-EVALUATE naming the
        # failure, never a raw crash, so the "fail closed everywhere" contract holds
        # (check-fails-closed-on-unreadable).
        rep.cant("internal: store validation raised an unexpected {} and fails closed to CANNOT-EVALUATE "
                 "({})".format(type(exc).__name__, exc))
    finally:
        # The store verdict is already computed by the barrier above; a descriptor close that raises
        # (EINTR / EIO / an invalid fd) during teardown must not crash the validator (S4-F3, outside B6).
        _close_fd_quietly(root_fd)
        if product_root_fd is not None:
            _close_fd_quietly(product_root_fd)
    return rep.result(evaluated_profiles, unevaluated_profiles)


def _validate_opened_store(root_fd, product_root_fd, machine_rel, supported_profiles, obs, pointer_target,
                           pointer_source, in_repo, rep, ancestral_floor=None):
    # --- C-MANIFEST: identify the store, derive enabled types / vendors / layout ----------------------
    rep.ran("C-MANIFEST")
    manifest_rel = _rel(machine_rel, MANIFEST_NAME)
    manifest_data, st = _read_toml(root_fd, manifest_rel, rep)
    if st != "ok":
        if st == "absent":
            rep.cant("{} is absent (the store manifest is required; spec 4.5)".format(manifest_rel))
        return [], []
    manifest_model = _opf_worklog.ManifestModel(
        root_fd, machine_rel, manifest_data, supported_profiles)
    mv = manifest_model.validation
    if mv.status == CANNOT_EVALUATE:
        rep.cant("{}: {}".format(manifest_rel, "; ".join(mv.findings)))
        return [], []
    for f in mv.findings:
        rep.finding("manifest: {}".format(f))

    # --- C-PROFILES: name the evaluated / unevaluated profile scope (spec 16) -------------------------
    rep.ran("C-PROFILES")
    unevaluated_profiles = list(manifest_model.unevaluated_profiles)
    evaluated_profiles = list(manifest_model.evaluated_profiles)
    if mv.findings or mv.status != VALID:
        # No manifest-dependent source is safe to traverse after any finding.
        rep.ran("C-RECORDS")
        rep.cant("active worklog source under {} is not evaluated: {} failed manifest "
                 "validation (see C-MANIFEST)".format(machine_rel, manifest_rel))
        return evaluated_profiles, unevaluated_profiles

    dp = manifest_data.get("opf") if isinstance(manifest_data, dict) else None
    layout = dp.get("layout") if isinstance(dp, dict) else None
    import_status = dp.get("import_status") if isinstance(dp, dict) else None
    types_tbl = manifest_data.get("types") if isinstance(manifest_data, dict) else None
    vendors = manifest_data.get("vendors") if isinstance(manifest_data, dict) else None
    reg = vendors.get("registered") if isinstance(vendors, dict) else None
    registered_vendors = frozenset(reg) if isinstance(reg, list) and all(isinstance(x, str) for x in reg) \
        else frozenset()
    enabled_modules = _enabled_modules(manifest_data)
    homes = _opf_store.homes_generation(manifest_data)
    rep.require_homes(homes)
    # The imported-series flag, derived ONCE (_opf_store.imported_series_declared: the declared spec_version
    # and this tooling's imported-series support, at either homes generation, so every store graded today
    # is inactive and the imported grading ships inert). It is the ONLY input C-COUNTERS, C-ID-SPACE,
    # C-NO-DELETION, C-CONTIGUITY, C-IMPORTED-PROVENANCE, the across-time checks and C-CONTAINMENT's
    # registration read for the imported series, so a store whose containment registers the imported leaves
    # also has them graded (spec 4.2/8.3). The prior imported baseline (_opf_observe) and the record verb's
    # counter proof (_opf_record) read the same helper. Only C-EVIDENCE-ENUM, which reconciles the whole
    # evidence tree, stays on homes 2 (spec 4.2: it reads nothing on homes 1).
    imported = _opf_store.imported_series_declared(manifest_data)

    # --- C-ROSTER: reconcile the manifest [types] against the authoritative roster (spec 9/8.1) -------
    rep.ran("C-ROSTER")
    required_types = set(BASELINE_TYPES) | {t for t, (ns, mod) in MODULE_TYPES.items()
                                            if mod in enabled_modules}
    declared_types = set(types_tbl) if isinstance(types_tbl, dict) else set()
    for tname in sorted(required_types):
        if tname not in declared_types:
            rep.finding("C-ROSTER: enabled type {!r} is not registered in the manifest [types] (baseline "
                        "types are always enabled; spec 9/8.1)".format(tname))
    enabled_types = _authoritative_types(enabled_modules, types_tbl)

    # --- C-RECORDS: gather + validate every active record; collect per-record reconciliation ----------
    rep.ran("C-RECORDS")
    active_recs, recon = _gather_active_records(root_fd, machine_rel, enabled_types, layout,
                                                registered_vendors, rep)
    try:
        active_worklog = _gather_worklog(
            root_fd, _rel(machine_rel, WORKLOG_NAME), registered_vendors, rep,
            required=True, machine_rel=machine_rel, propagate_manifest_failure=True,
            manifest_model=manifest_model) or {}
    except (_opf_worklog.ManifestShapeError, _opf_worklog.ManifestValidationError,
            _opf_worklog.ManifestReadError) as exc:
        _attribute_manifest_failure(rep, exc)
        rep.cant("{} is not evaluated: {} failed manifest validation "
                 "(see C-MANIFEST)".format(_rel(machine_rel, WORKLOG_NAME), exc.relpath))
        return evaluated_profiles, unevaluated_profiles

    # --- C-PERRECORD-RECONCILE: replay the bidirectional index<->body reconciliation (spec 13) --------
    rep.ran("C-PERRECORD-RECONCILE")
    for kind, msg in recon:
        if kind == "cant":
            rep.cant(msg)
        else:
            rep.finding(msg)

    # --- C-ARCHIVE-ENUM: walk + reconcile the archive; collect rotation-eligibility for C-ROTATION -----
    rep.ran("C-ARCHIVE-ENUM")
    archive_recs, archive_worklogs, rotatable = _validate_archive(root_fd, machine_rel, enabled_types,
                                                                  registered_vendors, import_status, rep,
                                                                  homes=homes)

    # --- C-EVIDENCE-ENUM (homes 2 only): reconcile the evidence homes against their inventories -------
    if homes >= 2:
        rep.ran("C-EVIDENCE-ENUM")
        _check_evidence(root_fd, homes, rep)

    # --- the merged worklog (active + archive together; spec 12:982-983) and the id maps --------------
    merged = {}
    active_wl_ids = set(active_worklog)
    archive_wl_ids = set()
    for n, entry in active_worklog.items():
        merged[n] = entry
    for _year, wl_map in archive_worklogs.items():
        for n, entry in wl_map.items():
            archive_wl_ids.add(n)
            merged[n] = entry

    wl_recs = []
    for n, entry in active_worklog.items():
        wl_recs.append(_make_rec(entry, "worklog", "active"))
    for year, wl_map in archive_worklogs.items():
        for n, entry in wl_map.items():
            wl_recs.append(_make_rec(entry, "worklog", "archive/{}".format(year)))

    recs = active_recs + archive_recs + wl_recs
    by_id = {}
    all_ids = set()
    for r in recs:
        if isinstance(r.id, str):
            all_ids.add(r.id)
            by_id.setdefault(r.id, r)

    # --- C-VERSION-LEDGER: version.toml (structural, spec 6.1) ----------------------------------------
    rep.ran("C-VERSION-LEDGER")
    version_data, vst = _read_toml(root_fd, _rel(machine_rel, VERSION_NAME), rep)
    if vst == "absent":
        rep.cant("{} is absent (the version ledger is required; spec 6.1)".format(
            _rel(machine_rel, VERSION_NAME)))
        version_data = None
    elif vst == "ok":
        vv = validate_version(version_data)
        if vv.status == CANNOT_EVALUATE:
            rep.cant("version.toml: {}".format("; ".join(vv.findings)))
            version_data = None
        elif vv.status != VALID:
            for f in vv.findings:
                rep.finding("version.toml: {}".format(f))
    else:
        version_data = None

    # --- C-COUNTERS: counters.toml with the known-namespace completeness control (spec 8.2) -----------
    rep.ran("C-COUNTERS")
    known_ns = frozenset(enabled_types.values())
    high = {}
    counters_ok = False
    counters_data, cst = _read_toml(root_fd, _rel(machine_rel, COUNTERS_NAME), rep)
    if cst == "absent":
        rep.finding("C-COUNTERS: {} is absent (one monotonic high-water per namespace is required; spec "
                    "8.2)".format(_rel(machine_rel, COUNTERS_NAME)))
    elif cst == "ok":
        # Importer/quarantine namespaces (IMPORTER_TYPES, i.e. LF) are ACCEPTED-if-present but NOT
        # REQUIRED: a store scaffolded before LF's deprecation (spec 8.1) carries an LF counter from
        # opf init while legacy_fragment stays out of the manifest [types] (schema-deferred), so LF is
        # absent from known_ns; passing it as optional keeps such a store clean without requiring LF on
        # a store that lacks it, as a new store does (spec 8.2).
        # With the imported series active (spec 8.2) the doctor also REQUIRES one "imported:<NS>" row per
        # enabled type except legacy_fragment, each an independent number line; inactive, an imported key
        # stays a finding exactly as before.
        high, cfindings = validate_counters(
            counters_data, known_namespaces=known_ns,
            optional_namespaces=frozenset(IMPORTER_TYPES.values()),
            imported_namespaces=_imported_counter_namespaces(enabled_types) if imported else None)
        for f in cfindings:
            rep.finding("counters.toml: {}".format(f))
        counters_ok = not cfindings

    # --- C-ROTATION: rotation eligibility (F4) + released-span-only rotation (spec 12) ----------------
    rep.ran("C-ROTATION")
    for msg in rotatable:
        rep.finding(msg)
    if archive_wl_ids:
        if version_data is None:
            rep.cant("C-ROTATION: the version ledger did not evaluate; released-only rotation is not "
                     "checkable")
        else:
            for f in check_rotation_only_released(archive_wl_ids, version_data):
                rep.finding("C-ROTATION: {}".format(f))

    # --- C-LEASE: the single-writer lease payload, present only while held (spec 5.7) -----------------
    rep.ran("C-LEASE")
    _check_lease(root_fd, machine_rel, rep)

    # --- C-ID-SPACE (R6/H4): store-wide uniqueness + ids-within-counters -------------------------------
    rep.ran("C-ID-SPACE")
    committed_ids = [r.id for r in active_recs if isinstance(r.id, str)]
    committed_ids += [r.id for r in archive_recs if isinstance(r.id, str)]
    committed_ids += ["WL-{}".format(n) for n in active_worklog]
    for _year, wl_map in archive_worklogs.items():
        committed_ids += ["WL-{}".format(n) for n in wl_map]
    # Uniqueness spans active + archive + staging (spec 11). At steady state containment grades any leftover
    # run interior as unregistered, but under a SUBSTANTIATED partial it only triages it, so the staged ids
    # are enumerated here and join the union; without this a staged id duplicating a committed one would
    # validate clean. ids-within-counters stays scoped to the committed store: a staged id is pending
    # promotion and may sit above the committed high-water.
    staged_ids = []
    if import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep, homes):
        staged_ids = _staged_ids(root_fd, homes, rep)
    for f in check_unique_ids(committed_ids + staged_ids):
        rep.finding("C-ID-SPACE: {}".format(f))
    for f in check_ids_within_counters(committed_ids, high):
        rep.finding("C-ID-SPACE: {}".format(f))
    # The imported series is graded on its OWN number lines (spec 8.2: each series independently): read from
    # the imported leaves only, unique within the series, and each id within its own "imported:<NS>"
    # high-water, never the clean one. The clean union above never holds an imported id.
    imported_rows, imported_unread, imported_misfiled = [], set(), []
    if imported:
        imported_rows, imported_unread, imported_misfiled = _gather_imported_rows(
            root_fd, machine_rel, enabled_types, rep)
        imported_ids = [rid for _key, rid, _row in imported_rows]
        for f in check_unique_ids(imported_ids, imported_series=True):
            rep.finding("C-ID-SPACE: imported series: {}".format(f))
        for f in check_ids_within_counters(imported_ids, high, imported_series=True):
            rep.finding("C-ID-SPACE: imported series: {}".format(f))

    # --- the cross-record checks ----------------------------------------------------------------------
    rep.ran("C-RECEIPTS")
    _check_receipts(recs, by_id, rep)
    rep.ran("C-HANDOFF")
    _check_handoff(recs, rep)
    rep.ran("C-DECISION-CHAINS")
    _check_decision_chains(recs, by_id, rep)
    rep.ran("C-LINKS")
    _check_links(recs, by_id, all_ids, rep)

    # --- the frozen-span / no-deletion / partition / contiguity checks (active + archive together) ----
    rep.ran("C-FROZEN-COVERAGE")
    if version_data is not None:
        for f in check_frozen_coverage(version_data, merged):
            rep.finding("C-FROZEN-COVERAGE: {}".format(f))
    else:
        rep.cant("C-FROZEN-COVERAGE: the version ledger did not evaluate; frozen coverage is not checkable")

    rep.ran("C-NO-DELETION")
    # No expansion of the high-water into an expected range (F15): an id is never deleted or reused, so the
    # present ids of a namespace must equal 1..high-water. Every namespace, WL included, gets an O(1)
    # arithmetic overhang for the max..high-water side, reporting the COUNT of allocated ids absent from
    # both locations, never an enumerated list, and an O(present) scan for a gap below the max. A B6
    # re-adoption's ancestral floor lifts the base of the expected range to floor+1: ids 1..floor were
    # allocated before the store was retired and counter preservation restores no record, while an id
    # allocated above the floor stays owed. A malformed floor, including one past the signed 64-bit range
    # a counters ledger can carry, is CANNOT-EVALUATE, never a silently wider pass.
    floor = {}
    if ancestral_floor is not None:
        if isinstance(ancestral_floor, dict) and all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                and 0 <= v <= (1 << 63) - 1
                for k, v in ancestral_floor.items()):
            floor = ancestral_floor
        else:
            rep.cant("C-NO-DELETION: the ancestral floor is malformed; the seeded-store deletion floor is "
                     "not evaluable")
    present_wl = sorted(merged)
    wl_hw = high.get("WL")
    if isinstance(wl_hw, int) and not isinstance(wl_hw, bool) and wl_hw >= 0:
        max_present = max(present_wl[-1] if present_wl else 0, floor.get("WL", 0))
        if wl_hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated worklog id(s) above WL-{} are absent from both the "
                        "active worklog and the archive (counters WL high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(wl_hw - max_present, max_present, wl_hw))
        # The gap below the max is scanned here too, from the floor: C-CONTIGUITY tiles from WL-1 and knows
        # no ancestral floor, so it cannot own a deletion from between floor+1 and the max.
        base = floor.get("WL", 0)
        expected = base + 1
        for n in (n for n in present_wl if n > base):
            if n != expected:
                rep.finding("C-NO-DELETION: WL-{} is absent from both the active worklog and the archive "
                            "(an allocated id between 1 and WL-{} cannot be deleted; spec 12/13)".format(
                                expected, max_present))
                break
            expected += 1
    # Every OTHER roster namespace likewise owns BOTH sides here: an overhang above the max present id AND
    # any gap below it (an id deleted from between). The present set is the committed active + archive
    # records for the namespace (B1); staging is excluded because it legitimately mints ids above the
    # committed high-water pending promotion (not durable). Both scans are O(present) arithmetic, never a
    # range() expansion of the high-water (F15).
    present_by_ns = {}
    for r in active_recs + archive_recs:
        if isinstance(r.id, str):
            sh = _valid_id_shape(r.id)
            if sh is not None:
                present_by_ns.setdefault(sh[0], set()).add(sh[1])
    for ns in sorted(known_ns):
        if ns == "WL":
            continue
        hw = high.get(ns)
        if not (isinstance(hw, int) and not isinstance(hw, bool) and hw >= 0):
            continue     # an absent / malformed counter is C-COUNTERS' finding, not this check's
        base = floor.get(ns, 0)
        # A present id at or below the ancestral floor is NOT reported by C-NO-DELETION: a
        # re-adoption restores no ancestral record, so such an id is id-REUSE (spec 8.2), not a
        # deletion; the guard leaves it to the id-space / contiguity checks (this `n > base` filter,
        # and the WL scan's above, skip it deliberately).
        nums = sorted(n for n in present_by_ns.get(ns, ()) if n > base)
        max_present = nums[-1] if nums else base
        if hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated {}-<n> id(s) above {}-{} are absent from both the "
                        "active store and the archive (counters {} high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(hw - max_present, ns, ns, max_present, ns, hw))
        expected = base + 1
        for n in nums:
            if n != expected:
                rep.finding("C-NO-DELETION: {}-{} is absent from both the active store and the archive (an "
                            "allocated id between 1 and {}-{} cannot be deleted; spec 12/13)".format(
                                ns, expected, ns, max_present))
                break
            expected += 1

    # The imported series owns BOTH sides per imported counter row too, on its own number line (spec 8.2/8.3).
    if imported:
        _check_imported_no_deletion(imported_rows, imported_unread, high, floor,
                                    _imported_counter_namespaces(enabled_types), rep)

    rep.ran("C-PARTITION")
    for f in check_ids_partition(active_wl_ids, archive_wl_ids):
        rep.finding("C-PARTITION: {}".format(f))

    rep.ran("C-CONTIGUITY")
    _check_worklog_contiguous(merged.keys(), rep)
    # imported:WL tiles its own line, apart from the clean tiling above (spec 8.2): `merged` stays clean-only,
    # so clean release spans, frozen coverage and the changelog gates never read an imported entry.
    if imported and IMPORTED_PREFIX + "WL" not in imported_unread:
        _check_imported_worklog_contiguous([parse_series_id(rid)[2] for key, rid, _row in imported_rows
                                            if key == IMPORTED_PREFIX + "WL"], rep)

    # --- C-TRACKED / C-SYNC-AGREE: topology, from the injected observations (spec 5.1/5.6) ------------
    rep.ran("C-TRACKED")
    tracked = obs.get("tracked")
    if tracked is None:
        reason = ("the `tracked` observation was supplied but malformed" if obs.get("tracked_malformed")
                  else "no `tracked` observation was supplied")
        rep.cant("C-TRACKED: {}; the tracked-store requirement (spec 5.1) is not evaluable by a parse-only "
                 "engine".format(reason))
    elif tracked == "untracked":
        rep.finding("C-TRACKED: the resolved store is not under version control (spec 5.1)")

    rep.ran("C-SYNC-AGREE")
    store_tbl = manifest_data.get("store") if isinstance(manifest_data, dict) else None
    sync_target = store_tbl.get("sync_target") if isinstance(store_tbl, dict) else ""
    if sync_target is None:
        sync_target = ""
    actual = obs.get("actual_remote")
    actual_reason = ("the `actual_remote` observation was supplied but malformed"
                     if obs.get("actual_remote_malformed") else "no `actual_remote` observation was supplied")
    actual_present = isinstance(actual, str) and actual.strip() != ""
    if not isinstance(sync_target, str):
        rep.cant("C-SYNC-AGREE: [store].sync_target is not a string")
    elif sync_target != "":
        # Pattern DEDICATED: the manifest names a dedicated sync target (spec 5.5). The store repository's
        # actual remote must agree with it, and a committed pointer that names a disagreeing remote is a
        # finding; the actual-remote leg is REQUIRED, so an absent observation is a cant, never a pass (F7).
        try:
            tgt = classify_target(sync_target)
        except StoreError as exc:
            rep.finding("C-SYNC-AGREE: [store].sync_target {!r} does not classify as a target (spec "
                        "5.5): {}".format(sync_target, exc))
        else:
            tcanon, tunresolved = _target_remote_canon(tgt)   # canonical (host, path) forms, None (local)
            for raw in tunresolved:
                rep.cant("C-SYNC-AGREE: the sync_target remote form {!r} cannot be canonicalized for "
                         "host+path agreement (a disclosed residual; spec 5.5)".format(raw))
            # Leg 2: a committed pointer that names a REMOTE disagreeing with the sync_target (B3).
            if pointer_target is not None and not pointer_target.local:
                pcanon, pun = _target_remote_canon(pointer_target)
                for raw in pun:
                    # Symmetric with the sync_target unresolved-forms cant (F7): a committed pointer naming a
                    # REMOTE the equivalence cannot canonicalize is a disclosed CANNOT-EVALUATE, never a
                    # silent leg-2 pass (ROUND-2 Fable F-4 / codex-3 s5).
                    rep.cant("C-SYNC-AGREE: the committed pointer remote form {!r} cannot be canonicalized "
                             "for host+path agreement against the sync_target (a disclosed residual; spec "
                             "5.6)".format(raw))
                if tcanon and pcanon and not _canon_sets_agree(pcanon, tcanon, tgt.kind):
                    rep.finding("C-SYNC-AGREE: the committed pointer remote {!r} does not agree with the "
                                "manifest sync_target {!r} (spec 5.6)".format(pointer_target.value, sync_target))
                elif pcanon and tcanon is None:
                    # A dedicated LOCAL sync_target (dir:/path -> tcanon is None) names NO remote, so a
                    # committed pointer that names a canonicalizable REMOTE contradicts it. Symmetric with
                    # the actual-remote leg's local-target-with-a-remote finding (leg 3) and the
                    # relocated-local committed-pointer-remote finding, so leg 2 does not silently pass a
                    # remote pointer against a local sync_target (Fable F2).
                    rep.finding("C-SYNC-AGREE: the manifest sync_target {!r} is a local target but the "
                                "committed pointer names a remote {} target {!r} (spec 5.6)".format(
                                    sync_target, pointer_target.kind, pointer_target.value))
            # Leg 3: the store repository's actual remote agrees with the RESOLVED sync_target. Equivalent
            # URL forms (https / ssh scp-style git@host:path, with or without a trailing .git) compare by
            # canonical host+path, so a valid shorthand is not rejected by a naive strip-compare (F8).
            if actual is None:
                rep.cant("C-SYNC-AGREE: {}; the sync-target / remote agreement (spec 5.6) is required "
                         "for a dedicated sync target and is not evaluable by a parse-only engine".format(
                             actual_reason))
            elif tcanon is None:
                # a local dir:/path sync target with an observed remote cannot be canonicalized to compare
                if actual_present:
                    rep.finding("C-SYNC-AGREE: [store].sync_target {!r} is a local target but the store "
                                "repository has an actual remote {!r} (spec 5.6)".format(sync_target, actual))
            elif not actual_present:
                rep.finding("C-SYNC-AGREE: the manifest declares a dedicated sync_target {!r} but the "
                            "store repository has no actual remote (spec 5.6)".format(sync_target))
            else:
                acanon = _canonical_remote(actual)
                if acanon is None:
                    # a form the equivalence cannot canonicalize is disclosed, never a silent mismatch (F8)
                    rep.cant("C-SYNC-AGREE: the actual remote {!r} cannot be canonicalized for "
                             "host+path agreement against the sync_target (a disclosed residual; spec "
                             "5.6)".format(actual))
                elif not tcanon:
                    rep.cant("C-SYNC-AGREE: the sync_target {!r} names no canonicalizable remote form "
                             "to compare against the actual remote (a disclosed residual; spec 5.5)".format(
                                 sync_target))
                else:
                    # The committed-pointer leg (above) and this actual-remote leg share ONE fold
                    # (_canon_sets_agree) so they cannot diverge on github/gitlab case (round-16 F-3).
                    if not _canon_sets_agree({acanon}, tcanon, tgt.kind):
                        rep.finding("C-SYNC-AGREE: the manifest sync_target {!r} does not match the store "
                                    "repository's actual remote {!r} (spec 5.6)".format(sync_target, actual))
    elif in_repo:
        # Pattern IN-REPO: sync_target == "" and the store root IS the product repo root (the default, or a
        # self-pointer such as `dir:.`; codex-8). It rides the product repository and shares its remote, so
        # a present actual remote is EXPECTED, not a disagreement -> PASS (F3). Durability is the disclosed
        # local-only residual (spec 5.7/17). A MALFORMED actual_remote observation is still an unreadable
        # input, not a pass: it routes to CANNOT-EVALUATE naming the observation (codex-5).
        if obs.get("actual_remote_malformed"):
            rep.cant("C-SYNC-AGREE: {}; an in-repo store shares the product repository's remote, but the "
                     "actual-remote observation cannot be read (spec 5.6)".format(actual_reason))
    else:
        # Pattern RELOCATED LOCAL-ONLY: sync_target == "" but a pointer names a store root OUTSIDE the
        # product repo, so it claims NO dedicated remote. A present actual remote is an unrecorded push
        # destination (a spec-5.6 disagreement); an ABSENT or MALFORMED observation cannot verify the
        # no-remote claim, so it is a cant, never a pass (F7). A committed pointer that itself names a
        # remote likewise contradicts the no-remote claim.
        if actual is None:
            rep.cant("C-SYNC-AGREE: {}; the store claims no dedicated remote (a relocated local-only "
                     "store) yet that claim cannot be verified without the actual-remote observation "
                     "(spec 5.6)".format(actual_reason))
        elif actual_present:
            rep.finding("C-SYNC-AGREE: the manifest declares no sync_target (relocated local-only) but the "
                        "store repository has an actual remote {!r} (an unrecorded push destination; "
                        "spec 5.6)".format(actual))
        if pointer_target is not None and not pointer_target.local:
            rep.finding("C-SYNC-AGREE: the manifest declares no sync_target (relocated local-only) but the "
                        "committed pointer names a remote {} target {!r} (spec 5.6)".format(
                            pointer_target.kind, pointer_target.value))

    # --- C-IMPORTED-PROVENANCE: re-read each cited original and bundle inventory (spec 8.3/14.1) -------
    # It runs exactly when the bound roster carries it, the way C-EVIDENCE-ENUM runs with the homes-2 roster,
    # so the id and its run cannot disagree. No roster carries it until the spec 1.3.0 activation wires the
    # imported-content checks into required_checks, so it ships inert. Where it runs, it grades the imported
    # series the containment flag admits (`imported`, at either homes generation) from the rows C-ID-SPACE
    # gathered under that flag, those outside their leaf series included, so a pass covers every row of
    # every readable leaf. On homes 1, where every 1.3.0 store is (homes 2 needs spec_version 2.0.0 and spec
    # 4.2 migrates no homes-1 store), it needs no other layer: it re-reads each cited bundle inventory and
    # every file listed there itself, since C-EVIDENCE-ENUM reads nothing until homes 2 (spec 4.2/14.1). A
    # store whose declaration cannot be read never reaches this point: C-MANIFEST fails it closed above.
    if "C-IMPORTED-PROVENANCE" in rep.required:
        rep.ran("C-IMPORTED-PROVENANCE")
        if imported:
            _check_imported_provenance(root_fd, imported_rows + imported_misfiled, imported_unread, rep)

    # --- C-CONTAINMENT: whole-tree path / unmanaged-path containment (spec 14.2/11) -------------------
    rep.ran("C-CONTAINMENT")
    # The imported-series registration flag (spec 8.2/4.2) is the driver's own `imported`, derived once
    # above, so the leaves and bundles containment registers are exactly the series the checks above grade.
    # False for every store this tooling validates: _opf_store.imported_series_declared answers False until
    # activation raises SUPPORTED_SPEC_VERSION to IMPORTED_SPEC_VERSION, which C-MANIFEST alone does not
    # guarantee (it accepts the reserved 2.0.0 / homes = 2 declaration today, spec 4.2).
    _check_containment(root_fd, machine_rel, manifest_data, import_status, rep, imported=imported)

    # --- C-VIEW-DRIFT: byte-level drift of every declared deterministic view (spec 5.8/10) ------------
    rep.ran("C-VIEW-DRIFT")
    if isinstance(views_tbl := (manifest_data.get("views") if isinstance(manifest_data, dict) else None),
                  dict) and len(views_tbl) > 0:
        try:
            planned = _opf_views.plan_views(
                root_fd, machine_rel, on_legacy_conflict=_worklog_legacy_conflict,
                manifest_model=manifest_model)
        except _opf_views.ViewsManifestError as exc:
            _attribute_manifest_failure(rep, exc.manifest_error)
            rep.cant("C-VIEW-DRIFT is not evaluated: {} failed manifest validation "
                     "(see C-MANIFEST)".format(exc.manifest_error.relpath))
            planned = None
        except _opf_views.ViewsError as exc:
            rep.cant("C-VIEW-DRIFT: {}".format(exc))
            planned = None
        if planned is not None:
            for name, scope, dest_rel, text in sorted(planned, key=lambda p: p[0]):
                if scope == "product":
                    if product_root_fd is None:
                        rep.cant("C-VIEW-DRIFT: view {!r} targets the product root, which is unavailable "
                                 "to this run".format(name))
                        continue
                    fd = product_root_fd
                else:
                    fd = root_fd
                raw, bst = _read_bytes(fd, dest_rel, rep)
                if bst == "error":
                    continue
                if bst == "absent":
                    rep.finding("C-VIEW-DRIFT: view {!r} target {!r} is missing (spec 5.8/10)".format(
                        name, dest_rel))
                elif raw != text.encode("utf-8"):
                    rep.finding("C-VIEW-DRIFT: view {!r} target {!r} has drifted from its generated source "
                                "(spec 5.8/10)".format(name, dest_rel))

    # --- C-VERSION-FILE: the root VERSION deliverable, byte-exact (spec 6.1/5.8) -----------------------
    rep.ran("C-VERSION-FILE")
    if product_root_fd is None:
        rep.cant("C-VERSION-FILE: the product root is unavailable; the root VERSION deliverable (spec 6.1) "
                 "is not evaluable")
    elif version_data is None:
        rep.cant("C-VERSION-FILE: the version ledger did not evaluate; the expected VERSION bytes cannot be "
                 "derived")
    else:
        releases = version_data.get("release") if isinstance(version_data, dict) else None
        releases = releases if isinstance(releases, list) else []
        if releases:
            try:
                expected = _opf_views.render_version_file({"version": {"releases": releases}})
            except _opf_views.ViewsError as exc:
                rep.cant("C-VERSION-FILE: {}".format(exc))
                expected = None
            if expected is not None:
                raw, bst = _read_bytes(product_root_fd, "VERSION", rep)
                if bst == "absent":
                    rep.finding("C-VERSION-FILE: the root VERSION deliverable is missing (spec 6.1/5.8)")
                elif bst == "ok" and raw != expected.encode("utf-8"):
                    rep.finding("C-VERSION-FILE: the root VERSION deliverable has drifted from the latest "
                                "release's bytes (spec 6.1/5.8)")
        else:
            # Zero releases: no VERSION is rendered and none is required, so a PRESENT root VERSION file is
            # byte drift (a stale deliverable), not an ungraded skip (spec 6.1/5.8; N3). An absent VERSION
            # is correct here.
            raw, bst = _read_bytes(product_root_fd, "VERSION", rep)
            if bst == "ok":
                rep.finding("C-VERSION-FILE: a root VERSION deliverable is present but the version ledger "
                            "has no release; no VERSION is rendered with zero releases (spec 6.1/5.8)")

    # --- C-CHANGELOG-GATES: compose U5 over the MERGED worklog (spec 7.1/7.2, F1) ---------------------
    rep.ran("C-CHANGELOG-GATES")
    if product_root_fd is None:
        rep.cant("C-CHANGELOG-GATES: the product root is unavailable; CHANGELOG.md (spec 5.8) is not "
                 "evaluable")
    elif version_data is None:
        rep.cant("C-CHANGELOG-GATES: the version ledger did not evaluate; the changelog gates cannot run")
    else:
        merged_doc = {"schema": SUPPORTED_SCHEMA, "entry": [merged[n] for n in sorted(merged)]}
        raw, bst = _read_bytes(product_root_fd, _opf_changelog.CHANGELOG_REL, rep)
        if bst == "absent":
            rep.finding("C-CHANGELOG-GATES: {} is absent (a required public deliverable; spec 5.8)".format(
                _opf_changelog.CHANGELOG_REL))
        elif bst == "ok":
            try:
                changelog_text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                rep.cant("C-CHANGELOG-GATES: {} is not valid UTF-8 ({})".format(
                    _opf_changelog.CHANGELOG_REL, exc))
                changelog_text = None
            if changelog_text is not None:
                cr = _opf_changelog.run_gates(version_data, merged_doc, changelog_text, registered_vendors)
                if cr.status == CANNOT_EVALUATE:
                    for f in cr.findings:
                        rep.cant("C-CHANGELOG-GATES: {}".format(f))
                elif cr.status == _opf_changelog.FINDING:
                    for f in cr.findings:
                        rep.finding("C-CHANGELOG-GATES: {}".format(f))

    # --- the across-time checks, from the injected prior committed snapshot (spec 6.1/8.2/8.4/13) -----
    prior = obs.get("prior")
    prior_malformed = obs.get("prior_malformed")
    # The imported parts of the prior are graded only where the series is active (spec 8.2/8.3). Inactive,
    # an imported counter key keeps its former verdict, a malformed prior, and the imported member is
    # ignored as before; active, a malformed imported member makes the whole prior malformed.
    if prior is not None and (prior["imported_malformed"] if imported else prior["imported_high"]):
        prior, prior_malformed = None, True
    prior_reason = ("the prior committed snapshot observation was supplied but malformed"
                    if prior_malformed else "no prior committed snapshot was supplied")
    rep.ran("C-HISTORY-APPEND-ONLY")
    if prior is None:
        rep.cant("C-HISTORY-APPEND-ONLY: {}; across-time release immutability (spec 6.1/13) is not "
                 "evaluated".format(prior_reason))
    elif version_data is None:
        rep.cant("C-HISTORY-APPEND-ONLY: the current version ledger did not evaluate; append-only across "
                 "time is not checkable")
    else:
        cur_releases = version_data.get("release")
        cur_releases = cur_releases if isinstance(cur_releases, list) else []
        ap = []
        _verify_append_only(prior["releases"], cur_releases, ap)
        for f in ap:
            rep.finding("C-HISTORY-APPEND-ONLY: {}".format(f))
    if imported and prior is not None:
        # Append-only over the imported leaves (spec 8.3), independent of the version ledger: every prior
        # imported record stays present and byte-identical in its canonical form.
        _check_imported_append_only(prior["imported"], imported_rows, imported_unread, rep)

    rep.ran("C-HISTORY-COUNTERS")
    if prior is None:
        rep.cant("C-HISTORY-COUNTERS: {}; counter non-regression (spec 8.2) is not evaluated".format(
            prior_reason))
    elif not counters_ok:
        rep.cant("C-HISTORY-COUNTERS: the current counters did not evaluate; counter non-regression is not "
                 "checkable")
    else:
        # Both number lines on an active series (spec 8.2): an "imported:<NS>" high-water is compared on its
        # own line. Inactive, the prior holds no imported key (see above), so this is the clean comparison.
        for f in check_monotonic({**prior["counters_high"], **prior["imported_high"]}, high,
                                 imported_series=imported):
            rep.finding("C-HISTORY-COUNTERS: {}".format(f))

    rep.ran("C-HISTORY-RESURRECTION")
    if prior is None:
        rep.cant("C-HISTORY-RESURRECTION: {}; no-resurrection across time (spec 8.4) is not "
                 "evaluated".format(prior_reason))
    else:
        _check_resurrection(prior["records"], prior.get("digests") or {}, by_id, all_ids, rep)

    return evaluated_profiles, unevaluated_profiles


def _enabled_modules(manifest_data):
    """The set of ENABLED [modules] (spec 9). validate_manifest already graded the table; this reads the
    booleans to derive the module-tier roster for C-ROSTER and the counters completeness control."""
    modules = manifest_data.get("modules") if isinstance(manifest_data, dict) else None
    enabled = set()
    if isinstance(modules, dict):
        for name, val in modules.items():
            if name in KNOWN_MODULES and val is True:
                enabled.add(name)
    return enabled


def _authoritative_types(enabled_modules, declared_types):
    """The type -> NORMATIVE-namespace roster the store must carry: the twelve baseline types, each
    module-tier type whose module is enabled, and any importer-only type (legacy_fragment) the manifest
    actually declares (present only after an import). Namespaces are U1's normative ones, never the
    manifest's declared values (validate_manifest already graded those; guard-input-soundness)."""
    roster = dict(BASELINE_TYPES)
    for tname, (ns, module) in MODULE_TYPES.items():
        if module in enabled_modules:
            roster[tname] = ns
    for tname, ns in IMPORTER_TYPES.items():
        if isinstance(declared_types, dict) and tname in declared_types:
            roster[tname] = ns
    return roster


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Whole-store integrity invariants over synthetic stores (one clean inline leg, one clean per-record
    leg, an unanchored fail-closed leg, and at least one discriminating vector per invariant class). Judged
    ONLY on the returned status / finding / check VALUES, never by grepping printed output (the
    isolate-verifiers rule). Each violation vector is a one-dimension mutation of a fixture that first
    validates VALID. The closed-roster reconciliation fails whenever a required check's `rep.ran(...)` is
    removed; each SOLE-layer enforcing branch additionally has a discriminating vector that flips back to
    VALID (or to a per-check FINDING verdict) when only its enforcement is removed. An overlapping
    defence-in-depth branch (for example C-PARTITION over a cross-location worklog duplicate that
    C-ID-SPACE also catches) is covered by its sibling layer rather than a sole vector, by design. No real
    git is used: validate_store reads no git, and the
    observation seam is exactly what is under test. Returns 0 clean, 1 on a failed check, 2 on a fail-closed
    error."""
    import tempfile
    import shutil
    import copy
    import itertools

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _opf_release import compute_span_digest
    from _opf_store import resolve_store

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-CHECK SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    TS = "2026-06-01T00:00:00Z"

    def wl(n):
        return {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
                "actor": {"kind": "maintainer"}, "kind": "added", "summary": "w{}".format(n)}

    def envelope(rid, rtype, status, **over):
        r = {"id": rid, "type": rtype, "status": status, "title": "t",
             "created_at": TS, "updated_at": TS, "actor": {"kind": "maintainer"}}
        r.update(over)
        return r

    def bi(n, status):
        return envelope("BI-{}".format(n), "backlog_item", status)

    def dn(n, bi_id):
        return envelope("DN-{}".format(n), "done", "recorded", links=[{"rel": "receipt_of", "id": bi_id}])

    def ho(n):
        return envelope("HO-{}".format(n), "handoff", "current")

    def ref(n):
        return envelope("RF-{}".format(n), "reference", "recorded",
                        refs=[{"kind": "doc", "locator": "x", "note": "n"}])

    def pd(n, status="decided", supersedes=None):
        r = envelope("PD-{}".format(n), "pending_decision", status)
        if status == "decided":
            r.update(decision="x", decided_at=TS, decided_by="maintainer")
        if supersedes is not None:
            r["links"] = [{"rel": "supersedes", "id": supersedes}]
        return r

    def fn(n):
        return envelope("FN-{}".format(n), "finding", "open")

    def cn(n, status="proposed", **over):
        r = envelope("CN-{}".format(n), "contribution", status,
                     recipient="peer", dedup_class="rule-fix", content_digest="sha256:x")
        r.update(over)
        return r

    def md(n, **over):
        r = envelope("MD-{}".format(n), "maintainer_decision", "recorded", decision="x")
        r.update(over)
        return r

    def pp(n, status="active", **over):
        r = envelope("PP-{}".format(n), "preference_pattern", status, context="c", rationale="r")
        r.update(over)
        return r

    def base_manifest(layout="inline", types=None, views=None):
        if types is None:
            types = {t: {"namespace": ns} for t, ns in (
                ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
                ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
                ("autonomous_decision", "AD"), ("block", "BL"), ("contribution", "CN"),
                ("maintainer_decision", "MD"), ("preference_pattern", "PP"))}
        m = {
            "opf": {"standard": "opf", "spec_version": SUPPORTED_SPEC_VERSION, "layout": layout,
                           "posture": "required", "import_status": "none"},
            "store": {"sync_target": ""},
            "types": types,
            "vendors": {"registered": []},
            "archive": {"period": "year"},
        }
        if views is not None:
            m["views"] = views
        return m

    def idx(records):
        return {"schema": 1, "record": records}

    def counters(**over):
        c = {"BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0, "BL": 0, "HO": 1, "RF": 0,
             "CN": 0, "MD": 0, "PP": 0}
        c.update(over)
        return {"schema": 1, "counters": c}

    full_wl = {n: wl(n) for n in (1, 2, 3, 4)}
    dig12 = compute_span_digest(full_wl, (1, 2))
    dig34 = compute_span_digest(full_wl, (3, 4))

    def rel_row(v, a, b, dig):
        return {"version": v, "date": TS, "worklog_span": ["WL-{}".format(a), "WL-{}".format(b)],
                "coverage_digest": dig}

    # A CHANGELOG whose headings tile the ledger (unreleased first, then descending), and the version
    # summaries whose published freeze digests match those entries. Computed via U5's own helpers so the
    # clean fixture passes the composed changelog gates.
    def make_changelog(published_tokens):
        lines = ["# Changelog", ""]
        lines += ["## unreleased", ""]
        for tok in published_tokens:
            lines += ["## {}".format(tok), "", "- {} notes".format(tok), ""]
        return "\n".join(lines) + "\n"

    def freeze_digests(changelog_text, tokens):
        entries, _f = _opf_changelog._changelog_entries(changelog_text)
        emap = {}
        for tok, text in entries:
            emap.setdefault(tok, []).append(text)
        return {tok: _opf_changelog.freeze_digest(emap[tok][0]) for tok in tokens}

    def version_two_release():
        text = make_changelog(["1.1.0", "1.0.0"])
        digs = freeze_digests(text, ["1.1.0", "1.0.0"])
        vd = {"schema": 1,
              "release": [rel_row("1.0.0", 1, 2, dig12), rel_row("1.1.0", 3, 4, dig34)],
              "summary": [{"covers": "1.0.0", "status": "published", "digest": digs["1.0.0"]},
                          {"covers": "1.1.0", "status": "published", "digest": digs["1.1.0"]},
                          {"covers": "unreleased", "status": "working"}]}
        return vd, text

    clean_version, clean_changelog = version_two_release()

    def clean_machine():
        return {
            "manifest.toml": base_manifest(),
            "counters.toml": counters(),
            "backlog_item.index.toml": idx([bi(1, "done"), bi(2, "open")]),
            "done.index.toml": idx([dn(1, "BI-1")]),
            "finding.index.toml": idx([]),
            "pending_decision.index.toml": idx([]),
            "handoff.index.toml": idx([ho(1)]),
            "reference.index.toml": idx([]),
            "autonomous_decision.index.toml": idx([]),
            "block.index.toml": idx([]),
            "contribution.index.toml": idx([]),
            "maintainer_decision.index.toml": idx([]),
            "preference_pattern.index.toml": idx([]),
            "worklog.toml": {"schema": 1, "entry": [wl(3), wl(4)]},
            "version.toml": copy.deepcopy(clean_version),
            "archive/2026/archive.toml": {"schema": 1, "moved": [],
                                          "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                             "destination": "archive/2026/worklog.toml"}]},
            "archive/2026/worklog.toml": {"schema": 1, "entry": [wl(1), wl(2)]},
        }

    def clean_product():
        return {"VERSION": "1.1.0\n", "CHANGELOG.md": clean_changelog}

    def clean_prior():
        return {"tracked": "tracked",
                "prior": {"releases": [rel_row("1.0.0", 1, 2, dig12), rel_row("1.1.0", 3, 4, dig34)],
                          "counters_high": counters()["counters"],
                          "records": {"BI-1": ("backlog_item", "done"), "BI-2": ("backlog_item", "open"),
                                      "DN-1": ("done", "recorded"), "HO-1": ("handoff", "current")},
                          "digests": {"DN-1": _record_digest(dn(1, "BI-1"))}}}

    base = Path(tempfile.mkdtemp(prefix="opf-check-selftest-")).resolve()
    counter = [0]

    def build(machine, product=None, working=None):
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / ".working" / "toml").mkdir(parents=True)
        for rel, doc in machine.items():
            p = root / ".working" / "toml" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")
        for rel, doc in (product or clean_product()).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(doc, bytes):
                p.write_bytes(doc)
            else:
                p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")
        for rel, doc in (working or {}).items():
            p = root / ".working" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")
        return root

    def run(machine, product=None, working=None, obs="clean"):
        res = resolve_store(build(machine, product, working))
        if res.status != RESOLVED:
            return None
        observations = clean_prior() if obs == "clean" else obs
        return validate_store(res, observations=observations)

    # run_bounded: the shared bounded-child watchdog, imported from _opf_emit so the fork/timer/pipe
    # hardening lives in ONE place across the three OPF self-tests and cannot diverge again (its prior
    # per-file copies drifted: the fork-less, SIGALRM-unblock, and parent-reap fixes had to be re-carried
    # by hand). Callers here still pre-check hasattr(os, "fork") and fail their setup closed where it is
    # absent; the shared helper additionally returns a SETUP-ERROR sentinel on a fork-less host.
    run_bounded = _opf_emit.run_bounded

    try:
        # --- clean inline store ----------------------------------------------------------------------
        clean = run(clean_machine())
        check("clean-resolved", clean is not None)
        check("clean-valid", clean is not None and clean.status == VALID)
        check("clean-no-findings", clean is not None and not clean.findings and not clean.cannot_evaluate)
        check("clean-discloses-residuals", clean is not None and bool(clean.residuals))
        check("clean-checks-cover-roster",
              clean is not None and set(clean.checks) == set(REQUIRED_CHECKS)
              and all(v == "PASS" for v in clean.checks.values()))

        # --- PR-C: the source-integrity / deliverable-drift partition, attribution, and the render predicate.
        # The partition is derived by SUBTRACTION, so it must EXHAUST the roster and stay DISJOINT; drift-gated
        # here beside the roster-coverage vector so an unclassified new required check defaults into the
        # must-pass source set rather than silently escaping the source gate (change-carries-check).
        check("prc-partition-exhausts-roster",
              SOURCE_INTEGRITY_CHECKS | DELIVERABLE_DRIFT_CHECKS == _REQUIRED_SET)
        check("prc-partition-disjoint", not (SOURCE_INTEGRITY_CHECKS & DELIVERABLE_DRIFT_CHECKS))
        check("prc-partition-deliverables-are-required",
              DELIVERABLE_DRIFT_CHECKS <= _REQUIRED_SET and len(DELIVERABLE_DRIFT_CHECKS) == 3)
        # source_integrity_ok over the clean store: every source check PASSes and nothing is unattributed.
        check("prc-predicate-clean-source-ok", clean is not None and source_integrity_ok(clean))
        # A store whose ONLY defect is a DELIVERABLE-drift FINDING still passes the source gate (render
        # regenerates that deliverable). Synthesized directly from the clean map so all 26 source checks read
        # PASS while a deliverable check is FINDING and nothing is unattributed.
        if clean is not None:
            _srcok = dict(clean.checks); _srcok["C-VIEW-DRIFT"] = "FINDING"
            check("prc-predicate-deliverable-finding-passes",
                  source_integrity_ok(StoreValidation(INVALID, checks=_srcok)))
            # A SOURCE check that is CANNOT-EVALUATE refuses the predicate.
            _srcbad = dict(clean.checks); _srcbad["C-MANIFEST"] = "CANNOT-EVALUATE"
            check("prc-predicate-source-cannot-eval-refuses",
                  not source_integrity_ok(StoreValidation(CANNOT_EVALUATE, checks=_srcbad)))
            # A non-empty `unattributed` refuses the predicate REGARDLESS of the per-check map (an internal
            # fault the map does not carry).
            check("prc-predicate-unattributed-refuses",
                  not source_integrity_ok(StoreValidation(VALID, checks=dict(clean.checks),
                                                          unattributed=["synthetic internal fault"])))
        # Attribution: a finding is recorded under its current check in `by_check`; a cant() with NO current
        # check lands in `unattributed`; a duplicate ran() is an internal fault in `unattributed`.
        _rp = _Report()
        _rp.ran("C-VIEW-DRIFT"); _rp.finding("C-VIEW-DRIFT: synthetic drift")
        check("prc-by-check-attributes-finding",
              _rp.result().by_check.get("C-VIEW-DRIFT") == ["C-VIEW-DRIFT: synthetic drift"])
        _rp2 = _Report(); _rp2.cant("no-current early-return cannot-evaluate")
        check("prc-unattributed-collects-no-current",
              "no-current early-return cannot-evaluate" in _rp2.unattributed)
        _rp3 = _Report(); _rp3.ran("C-MANIFEST"); _rp3.ran("C-MANIFEST")
        check("prc-duplicate-ran-is-unattributed",
              any("run more than once" in m for m in _rp3.unattributed))

        # --- unanchored leg: no observations -> exactly the topology/history anchors cannot-evaluate ---
        unanch = run(clean_machine(), obs=None)
        check("unanchored-cannot-eval", unanch is not None and unanch.status == CANNOT_EVALUATE)
        check("unanchored-tracked-named", unanch is not None
              and any("C-TRACKED" in m for m in unanch.cannot_evaluate))
        check("unanchored-prior-named", unanch is not None
              and any("prior committed snapshot" in m for m in unanch.cannot_evaluate))
        check("unanchored-clean-checks-pass", unanch is not None
              and unanch.checks.get("C-RECORDS") == "PASS" and unanch.checks.get("C-ID-SPACE") == "PASS")

        # --- clean per-record store (F11 over-fire regression: a native TOML date under x-vendor) -----
        def fn_date(n):
            return envelope("FN-{}".format(n), "finding", "open",
                            **{"x-acme": {"seen": __import__("datetime").date(2026, 6, 1)}})
        import datetime as _dt   # noqa: F401 (imported for the x-vendor date fixture body)
        d1 = _record_digest(fn(1))
        d2 = _record_digest(fn_date(2))
        pr_machine = clean_machine()
        pr_machine["manifest.toml"] = base_manifest(layout="per-record")
        pr_machine["manifest.toml"]["vendors"] = {"registered": ["x-acme"]}
        pr_machine["counters.toml"] = counters(FN=2, BI=0, DN=0, WL=0, HO=0)
        pr_machine["backlog_item.index.toml"] = idx([])
        pr_machine["done.index.toml"] = idx([])
        pr_machine["handoff.index.toml"] = idx([])
        pr_machine["finding.index.toml"] = {"schema": 1, "record": [
            {"id": "FN-1", "state": "open", "path": "finding/FN-1.toml", "digest": d1},
            {"id": "FN-2", "state": "open", "path": "finding/FN-2.toml", "digest": d2}]}
        pr_machine["finding/FN-1.toml"] = fn(1)
        pr_machine["finding/FN-2.toml"] = fn_date(2)
        pr_machine["worklog.toml"] = {"schema": 1, "entry": []}
        pr_machine["version.toml"] = {"schema": 1, "release": [],
                                      "summary": [{"covers": "unreleased", "status": "working"}]}
        pr_machine["archive/2026/archive.toml"] = {"schema": 1, "moved": [], "worklog_moved": []}
        pr_machine["archive/2026/worklog.toml"] = {"schema": 1, "entry": []}
        pr_product = {"CHANGELOG.md": make_changelog([])}          # no releases: no VERSION, unreleased-only
        pr_obs = {"tracked": "tracked",
                  "prior": {"releases": [], "counters_high": counters(FN=2, BI=0, DN=0, WL=0, HO=0)["counters"],
                            "records": {"FN-1": ("finding", "open"), "FN-2": ("finding", "open")}}}
        prc = run(pr_machine, product=pr_product, obs=pr_obs)
        check("per-record-clean-valid", prc is not None and prc.status == VALID)
        check("per-record-clean-no-cant", prc is not None and not prc.cannot_evaluate)
        # --- round-5 containment / deferral / sync fixes (F-1..F-5) ---------------------------------
        # F-1/F-4: an [unmanaged] declaration inside a fully-graded container (a per-record type-body dir,
        # the imports subtree, or an archive bucket) must not launder strays beneath it.
        for _udecl, _stray in ((".working/toml/finding", "toml/finding/evil.bin"),
                               (".working/toml/finding/sub", "toml/finding/sub/deep.toml"),
                               (".working/imports/leftover", "imports/leftover/junk.bin"),
                               (".working/toml/archive/2026", "toml/archive/2026/evil.bin")):
            _cc = copy.deepcopy(pr_machine)
            _cc["manifest.toml"]["unmanaged"] = {"paths": [_udecl]}
            _rc = run(_cc, product=pr_product, obs=pr_obs, working={_stray: "x\n"})
            check("unmanaged-in-container-collision:" + _udecl,
                  _rc is not None and any("collides" in f for f in _rc.findings))
            check("unmanaged-in-container-invalid:" + _udecl, _rc is not None and _rc.status == INVALID)
        # gemini MINOR: a valid legacy DIRECTORY outside every managed namespace covers files nested under
        # it (VALID); a regression in the valid-unmanaged subtree coverage would flip this to INVALID.
        _legd = clean_machine()
        _legd["manifest.toml"] = base_manifest()
        _legd["manifest.toml"]["unmanaged"] = {"paths": [".working/legacy-dir"]}
        check("unmanaged-subtree-dir-valid",
              run(_legd, working={"legacy-dir/old-note.md": "x\n"}).status == VALID)
        # F-5a: C-NO-DELETION below-max GAP (FN-1 and FN-3 present, high-water 3 -> FN-2 absent), distinct
        # from the overhang branch the existing vector exercises.
        _b1g = clean_machine()
        _b1g["finding.index.toml"] = idx([envelope("FN-1", "finding", "open"),
                                          envelope("FN-3", "finding", "open")])
        _b1g["counters.toml"] = counters(FN=3)
        _b1gr = run(_b1g)
        check("b1-nonwl-gap-below-max-invalid", _b1gr is not None and _b1gr.status == INVALID)
        check("b1-nonwl-gap-below-max-named",
              _b1gr is not None and any("C-NO-DELETION" in f and "FN-2" in f for f in _b1gr.findings))
        # F-2: a declared importer legacy_fragment type defers to a named CANNOT-EVALUATE (it is deprecated for
        # new stores and has no baseline record schema), never a false INVALID.
        _lff = clean_machine()
        _lff["manifest.toml"] = base_manifest()
        _lff["manifest.toml"]["types"]["legacy_fragment"] = {"namespace": "LF"}
        _lff["counters.toml"] = counters(LF=1)
        _lff["legacy_fragment.index.toml"] = idx([envelope("LF-1", "legacy_fragment", "quarantined")])
        _lf_prior = copy.deepcopy(clean_prior()["prior"])
        _lf_prior["counters_high"] = dict(_lf_prior["counters_high"], LF=1)
        _lf_prior["records"]["LF-1"] = ("legacy_fragment", "quarantined")
        _lfr = run(_lff, obs={"tracked": "tracked", "prior": _lf_prior})
        check("legacy-fragment-deferred-cannot-eval", _lfr is not None and _lfr.status == CANNOT_EVALUATE)
        check("legacy-fragment-deferred-named",
              _lfr is not None and any("legacy_fragment" in m and "deferred" in m for m in _lfr.cannot_evaluate))
        # STEP 5 (checks group): the three 1.1.0 baseline types (contribution/CN, maintainer_decision/MD,
        # preference_pattern/PP) now carry shipped BASELINE_SPECS schemas, so C-RECORDS GRADES their records
        # rather than deferring them (contrast the module-tier / legacy_fragment deferral above). A clean
        # populated store validates VALID; a one-dimension mutation of each named per-record invariant
        # (the maintainer_decision exemplifies-target and the contribution delivery bundle) flips INVALID,
        # proving the baseline validator is wired for these types through the doctor.
        _md = clean_machine()
        _md["counters.toml"] = counters(MD=1, PP=1)
        _md["preference_pattern.index.toml"] = idx([pp(1)])
        _md["maintainer_decision.index.toml"] = idx([md(1, links=[{"rel": "exemplifies", "id": "PP-1"}])])
        check("baseline-md-pp-graded-valid", run(_md).status == VALID)
        _mdx = clean_machine()
        _mdx["counters.toml"] = counters(MD=1, PP=1)
        _mdx["preference_pattern.index.toml"] = idx([pp(1)])
        _mdx["maintainer_decision.index.toml"] = idx([md(1, links=[{"rel": "exemplifies", "id": "BI-1"}])])
        _mdxr = run(_mdx)
        check("baseline-md-exemplifies-nonpp-invalid", _mdxr is not None and _mdxr.status == INVALID)
        check("baseline-md-exemplifies-nonpp-named",
              _mdxr is not None and any("exemplifies" in f for f in _mdxr.findings))
        _cn = clean_machine()
        _cn["counters.toml"] = counters(CN=1)
        _cn["contribution.index.toml"] = idx([cn(1)])
        check("baseline-contribution-graded-valid", run(_cn).status == VALID)
        _cnx = clean_machine()
        _cnx["counters.toml"] = counters(CN=1)
        _cnx["contribution.index.toml"] = idx([cn(1, status="sent")])
        _cnxr = run(_cnx)
        check("baseline-contribution-delivery-bundle-invalid", _cnxr is not None and _cnxr.status == INVALID)
        check("baseline-contribution-delivery-bundle-named",
              _cnxr is not None and any("delivery" in f for f in _cnxr.findings))
        # F-3: a remote spelling the scheme DEFAULT port names the same endpoint as the port-less shorthand
        # (VALID); a NON-default port stays distinct (INVALID).
        _dpf = clean_machine()
        _dpf["manifest.toml"] = base_manifest()
        _dpf["manifest.toml"]["store"] = {"sync_target": "github:org/repo"}
        check("sync-default-port-valid",
              run(_dpf, obs={"tracked": "tracked", "actual_remote": "https://github.com:443/org/repo.git",
                             "prior": clean_prior()["prior"]}).status == VALID)
        check("sync-nondefault-port-invalid",
              run(_dpf, obs={"tracked": "tracked", "actual_remote": "https://github.com:8443/org/repo.git",
                             "prior": clean_prior()["prior"]}).status == INVALID)

        # round-7 BLOCKER: a schema-deferred PER-RECORD type still gets structural reconciliation. An orphan
        # body (a valid-id .toml with no registry row) under a deferred type is a finding, not VALID.
        _orf = copy.deepcopy(pr_machine)
        _orf["manifest.toml"]["types"]["legacy_fragment"] = {"namespace": "LF"}
        _orf["counters.toml"] = counters(FN=2, BI=0, DN=0, WL=0, HO=0, LF=0)
        _orf["legacy_fragment.index.toml"] = idx([])
        _orf_obs = {"tracked": "tracked",
                    "prior": {"releases": [],
                              "counters_high": counters(FN=2, BI=0, DN=0, WL=0, HO=0, LF=0)["counters"],
                              "records": {"FN-1": ("finding", "open"), "FN-2": ("finding", "open")}}}
        _oro = run(_orf, product=pr_product, obs=_orf_obs,
                   working={"toml/legacy_fragment/LF-99.toml": "x\n"})
        check("perrecord-deferred-orphan-invalid", _oro is not None and _oro.status == INVALID)
        check("perrecord-deferred-orphan-named",
              _oro is not None and any("LF-99" in f and "no registry row" in f for f in _oro.findings))
        # gemini round-7: a bracketed IPv6 scp-form remote canonicalizes to the same (host, path) as its
        # ssh:// equivalent instead of mis-splitting on a colon inside the brackets; a malformed bracket is
        # unresolvable (None), disclosed rather than mis-matched.
        check("canonical-ipv6-scp-matches-ssh",
              _canonical_remote("git@[2001:db8::1]:repo") is not None
              and _canonical_remote("git@[2001:db8::1]:repo") == _canonical_remote("ssh://[2001:db8::1]/repo"))
        check("canonical-ipv6-malformed-bracket-none", _canonical_remote("git@[2001:db8::1:repo") is None)
        # gemini round-9 BLOCKER: a record rotated to the archive with NO archived worklog span to bind it
        # to a released period is a C-ROTATION finding, not laundered VALID (the released-only check keys on
        # the archived worklog ids, which are empty here). Reuse the per-record clean fixture (empty
        # archive, so archive_wl_ids stays empty) and add one present moved finding record to the bucket.
        _rrf = copy.deepcopy(pr_machine)
        _rrf["counters.toml"] = counters(FN=3, BI=0, DN=0, WL=0, HO=0)
        _rrf["archive/2026/archive.toml"] = {"schema": 1, "worklog_moved": [],
            "moved": [{"id": "FN-3", "type": "finding", "destination": "archive/2026/finding.index.toml"}]}
        _rrf["archive/2026/finding.index.toml"] = idx([envelope("FN-3", "finding", "resolved")])
        _rr_obs = {"tracked": "tracked",
                   "prior": {"releases": [],
                             "counters_high": counters(FN=3, BI=0, DN=0, WL=0, HO=0)["counters"],
                             "records": {"FN-1": ("finding", "open"), "FN-2": ("finding", "open"),
                                         "FN-3": ("finding", "resolved")}}}
        _rr = run(_rrf, product=pr_product, obs=_rr_obs)
        check("rotation-no-worklog-span-named",
              _rr is not None and any("C-ROTATION" in f and "no archived worklog span" in f for f in _rr.findings))
        # gemini/claude round-11 BLOCKER: PER-BUCKET rotation binding. A mixed-bucket store where one bucket
        # has a worklog span (global archive_wl_ids non-empty) must still flag a DIFFERENT bucket that
        # rotated records with no worklog span of its own.
        _mb = clean_machine()
        _mb["counters.toml"] = counters(FN=1)
        _mb["finding.index.toml"] = idx([])
        _mb["archive/2027/archive.toml"] = {"schema": 1, "worklog_moved": [],
            "moved": [{"id": "FN-1", "type": "finding", "destination": "archive/2027/finding.index.toml"}]}
        _mb["archive/2027/finding.index.toml"] = idx([envelope("FN-1", "finding", "resolved")])
        _mbr = run(_mb)
        check("rotation-mixed-bucket-unbound-named",
              _mbr is not None and any("C-ROTATION" in f and "2027" in f and "no archived worklog span" in f
                                       for f in _mbr.findings))
        # gemini round-11 BLOCKER: a rogue per-record worklog directory (worklog is ledger-only; its home is
        # worklog.toml) declared [unmanaged] must NOT launder a rogue body beneath it; the ledger type dir is
        # a reserved collision.
        _wd = copy.deepcopy(pr_machine)
        _wd["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/worklog"]}
        _wdr = run(_wd, product=pr_product, obs=pr_obs, working={"toml/worklog/WL-1.toml": "x\n"})
        check("unmanaged-worklog-dir-invalid", _wdr is not None and _wdr.status == INVALID)
        check("unmanaged-worklog-dir-collision",
              _wdr is not None and any("collides" in f for f in _wdr.findings))
        # gemini round-11 MINOR: an illegal-for-every-actor cross-time transition (backlog_item open -> the
        # terminal done, skipping active) is a C-HISTORY-RESURRECTION finding; exercises the illegal-for-all
        # block, distinct from the terminal-prior resurrection vector.
        _iaf = clean_machine()
        _iaf["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "done")])
        _iaf_obs = {"tracked": "tracked",
                    "prior": {"releases": clean_prior()["prior"]["releases"],
                              "counters_high": clean_prior()["prior"]["counters_high"],
                              "records": {"BI-1": ("backlog_item", "done"), "BI-2": ("backlog_item", "open"),
                                          "DN-1": ("done", "recorded"), "HO-1": ("handoff", "current")},
                              "digests": clean_prior()["prior"]["digests"]}}
        _iafr = run(_iaf, obs=_iaf_obs)
        check("history-illegal-for-all-named",
              _iafr is not None and any("C-HISTORY-RESURRECTION" in f and "illegal for every actor" in f
                                        for f in _iafr.findings))
        # round-14 C1: INLINE layout reserves <type>/ dirs too - a rogue <type>/ body dir declared
        # [unmanaged] must not launder (the inline analog of the per-record worklog-dir case).
        _inl = clean_machine()
        _inl["manifest.toml"] = base_manifest()
        _inl["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/worklog"]}
        _inlr = run(_inl, working={"toml/worklog/WL-1.toml": "x\n"})
        check("unmanaged-inline-type-dir-invalid", _inlr is not None and _inlr.status == INVALID)
        check("unmanaged-inline-type-dir-collision",
              _inlr is not None and any("collides" in f for f in _inlr.findings))
        # round-14 (ledger-index): a rogue <ledgertype>.index.toml declared [unmanaged] must not be shielded;
        # the ledger index file is a never-legal reserved slot.
        _lix = clean_machine()
        _lix["manifest.toml"] = base_manifest()
        _lix["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/worklog.index.toml"]}
        _lixr = run(_lix, working={"toml/worklog.index.toml": "x\n"})
        check("unmanaged-ledger-index-invalid", _lixr is not None and _lixr.status == INVALID)
        check("unmanaged-ledger-index-collision",
              _lixr is not None and any("collides" in f for f in _lixr.findings))
        # round-14 C4: a malformed [unmanaged] entry (non-contained / escaping path) is a named
        # CANNOT-EVALUATE, not a silent drop.
        _mal = clean_machine()
        _mal["manifest.toml"] = base_manifest()
        _mal["manifest.toml"]["unmanaged"] = {"paths": ["/etc/passwd"]}
        _malr = run(_mal)
        check("unmanaged-malformed-path-cannot-eval", _malr is not None and _malr.status == CANNOT_EVALUATE)
        check("unmanaged-malformed-path-named",
              _malr is not None and any(
                  "[unmanaged].paths entry '/etc/passwd' must be a root-relative path" in m
                  for m in _malr.by_check["C-MANIFEST"]))
        # round-14 empty-dir: an unregistered EMPTY directory under the machine store (no files at all) must
        # be graded as a stray, not silently laundered (the walk previously graded files only).
        _ed_root = build(clean_machine())
        os.makedirs(str(_ed_root / ".working" / "toml" / "rogue_empty_dir"), exist_ok=False)
        _edr = validate_store(resolve_store(_ed_root), observations=clean_prior())
        check("empty-rogue-dir-invalid", _edr.status == INVALID)
        check("empty-rogue-dir-named",
              any("rogue_empty_dir" in f and "unregistered" in f for f in _edr.findings))
        # round-14 C3: a crafted manifest view NAME (unrecognized) must not turn an arbitrary store path
        # into a managed view target; the rogue path is graded by C-CONTAINMENT (self-sound), not laundered.
        _c3 = clean_machine()
        _c3["manifest.toml"] = base_manifest(views={"x/y/EVIL.toml": {
            "kind": "deterministic", "sources": ["worklog"], "target": ".working/x/y/EVIL.toml"}})
        _c3r = run(_c3, working={"x/y/EVIL.toml": "x\n"})
        check("c3-crafted-view-name-graded",
              _c3r is not None and any("C-CONTAINMENT" in f and "unregistered" in f and ".working/x" in f
                                       for f in _c3r.findings))
        # R9-1 (ported from the retired ingest gate's f91 cross-run, checker leg only): a recognized view's
        # store-scope spec destination is a managed LEAF matched by equality, never a managed subtree. A
        # DIRECTORY at `.working/TODO.md` is recursed as a view-target ancestor, so the child it hides is an
        # unregistered C-CONTAINMENT finding named under C-CONTAINMENT's own attribution (not merely the
        # whole-store exit, which the wrong-type read of the destination already pins at CANNOT-EVALUATE).
        # Flip: skipping a subdirectory that equals a view target (`full in view_targets` beside the
        # skipped roots) silently prunes the child and turns the C-CONTAINMENT leg PASS.
        _f91v = {"TODO.md": {"kind": "composed", "sources": ["backlog_item", "block"],
                             "target": ".working/TODO.md"}}
        _f91d = clean_machine()
        _f91d["manifest.toml"] = base_manifest(views=_f91v)
        _f91dr = run(_f91d, working={"TODO.md/inner.md": "hidden\n"})
        check("f91-dir-at-view-dest-cannot-evaluate",
              _f91dr is not None and exit_code(_f91dr) == 2)
        check("f91-dir-at-view-dest-child-named",
              _f91dr is not None and _f91dr.checks.get("C-CONTAINMENT") == "FINDING"
              and any(".working/TODO.md/inner.md" in m and "unregistered" in m
                      for m in _f91dr.by_check.get("C-CONTAINMENT", [])))
        # Companion: a REGULAR FILE at the same destination is the managed view output, excluded from
        # C-CONTAINMENT (no finding names it), and with the golden rendered the store is VALID.
        _f91f = clean_machine()
        _f91f["manifest.toml"] = base_manifest(views=_f91v)
        _f91f_root = build(_f91f, clean_product())
        _f91f_res = resolve_store(_f91f_root)
        _f91f_fd = _open_store_root_fd(_f91f_res.store_root, _f91f_res.pointer_source != "default")
        try:
            _f91f_text = {n: t for n, _s, _d, t in _opf_views.plan_views(
                _f91f_fd, _f91f_res.machine_rel)}.get("TODO.md")
        finally:
            os.close(_f91f_fd)
        (_f91f_root / ".working" / "TODO.md").write_text(_f91f_text or "", encoding="utf-8")
        _f91fr = validate_store(resolve_store(_f91f_root), observations=clean_prior())
        check("f91-regular-file-at-view-dest-excluded",
              _f91f_text is not None and _f91fr.status == VALID
              and _f91fr.checks.get("C-CONTAINMENT") == "PASS"
              and not any(".working/TODO.md" in m for m in _f91fr.by_check.get("C-CONTAINMENT", [])))
        # round-14 C5: a partial import substantiated only by a WELL-FORMED run (carrying plan.toml). A
        # run-id-named dir WITHOUT plan.toml does not substantiate, so a stray is graded, not triaged.
        _c5 = clean_machine()
        _c5["manifest.toml"] = base_manifest()
        _c5["manifest.toml"]["opf"]["import_status"] = "partial"
        _c5r = run(_c5, working={"stray.md": "x",
                                 "imports/imp-20260601T000000Z-0123456789abcdef/junk.txt": "x"})
        check("c5-partial-no-plan-not-substantiated",
              _c5r is not None and _c5r.status == INVALID
              and any("no active" in f and "partial" in f for f in _c5r.findings))
        # digest byte flipped -> INVALID (proves the digest still bites over the x-vendor-date body)
        prm = copy.deepcopy(pr_machine)
        prm["finding.index.toml"]["record"][0]["digest"] = "sha256:" + "b" * 64
        r = run(prm, product=pr_product, obs=pr_obs)
        check("per-record-digest-flip-invalid", r.status == INVALID)
        check("per-record-digest-flip-named",
              any("C-PERRECORD-RECONCILE" in f or "digest mismatch" in f for f in r.findings))
        # two-field row (missing state, path) -> INVALID
        prk = copy.deepcopy(pr_machine)
        prk["finding.index.toml"]["record"][0] = {"id": "FN-1", "digest": d1}
        check("per-record-missing-keys-invalid", run(prk, product=pr_product, obs=pr_obs).status == INVALID)
        # unknown fifth key -> INVALID
        pru = copy.deepcopy(pr_machine)
        pru["finding.index.toml"]["record"][0]["extra"] = "x"
        check("per-record-unknown-key-invalid", run(pru, product=pr_product, obs=pr_obs).status == INVALID)
        # FN-1 row over an FN-2 body -> INVALID
        prb = copy.deepcopy(pr_machine)
        prb["finding/FN-1.toml"] = envelope("FN-9", "finding", "open")
        check("per-record-id-binding-invalid", run(prb, product=pr_product, obs=pr_obs).status == INVALID)
        # row state disagreeing with the body status -> INVALID
        prs = copy.deepcopy(pr_machine)
        prs["finding.index.toml"]["record"][0]["state"] = "fixed"
        check("per-record-state-binding-invalid", run(prs, product=pr_product, obs=pr_obs).status == INVALID)
        # non-canonical path -> INVALID
        prp = copy.deepcopy(pr_machine)
        prp["finding.index.toml"]["record"][0]["path"] = "finding/FN-9.toml"
        check("per-record-path-invalid", run(prp, product=pr_product, obs=pr_obs).status == INVALID)

        # --- changelog gates ------------------------------------------------------------------------
        check("changelog-absent-invalid", run(clean_machine(), product={"VERSION": "1.1.0\n"}).status == INVALID)
        broken_cl = make_changelog(["1.1.0"])   # drops the "## 1.0.0" heading: a coverage gap
        digs = freeze_digests(broken_cl, ["1.1.0"])
        check("changelog-coverage-gap-invalid",
              run(clean_machine(), product={"VERSION": "1.1.0\n", "CHANGELOG.md": broken_cl}).status == INVALID)
        edited_cl = clean_changelog.replace("- 1.1.0 notes", "- 1.1.0 EDITED after publish")
        r = run(clean_machine(), product={"VERSION": "1.1.0\n", "CHANGELOG.md": edited_cl})
        check("changelog-freeze-edit-invalid", r.status == INVALID)
        check("changelog-freeze-named", any("C-CHANGELOG-GATES" in f for f in r.findings))
        check("changelog-non-utf8-cannot-eval",
              run(clean_machine(), product={"VERSION": "1.1.0\n",
                                            "CHANGELOG.md": b"\xff\xfe bad"}).status == CANNOT_EVALUATE)

        # --- VERSION deliverable --------------------------------------------------------------------
        r = run(clean_machine(), product={"VERSION": "9.9.9\n", "CHANGELOG.md": clean_changelog})
        check("version-file-drift-invalid", r.status == INVALID)
        check("version-file-drift-named", any("C-VERSION-FILE" in f for f in r.findings))
        check("version-file-missing-invalid",
              run(clean_machine(), product={"CHANGELOG.md": clean_changelog}).status == INVALID)

        # --- view drift (declare a WORKLOG.md deterministic view; render the golden, then flip) ------
        vm = clean_machine()
        vm["manifest.toml"] = base_manifest(views={"WORKLOG.md": {
            "kind": "deterministic", "sources": ["worklog"], "target": ".working/WORKLOG.md"}})
        root_v = build(vm, clean_product())
        res_v = resolve_store(root_v)
        sfd = _open_store_root_fd(res_v.store_root, res_v.pointer_source != "default")
        try:
            planned_v = _opf_views.plan_views(sfd, res_v.machine_rel)
        finally:
            os.close(sfd)
        wl_text = {name: text for name, _s, _d, text in planned_v}.get("WORKLOG.md")
        (root_v / ".working" / "WORKLOG.md").write_text(wl_text, encoding="utf-8")
        check("view-clean-valid",
              validate_store(resolve_store(root_v), observations=clean_prior()).status == VALID)
        (root_v / ".working" / "WORKLOG.md").write_text(wl_text + "x", encoding="utf-8")
        rv = validate_store(resolve_store(root_v), observations=clean_prior())
        check("view-drift-invalid", rv.status == INVALID)
        check("view-drift-named", any("C-VIEW-DRIFT" in f for f in rv.findings))

        # --- topology (tracked / sync / containment) ------------------------------------------------
        check("untracked-invalid",
              run(clean_machine(), obs={"tracked": "untracked", "prior": clean_prior()["prior"]}).status == INVALID)
        junk = run(clean_machine(), working={"junk.md": "x\n"})
        check("undeclared-file-invalid", junk.status == INVALID)
        check("undeclared-file-named", any("C-CONTAINMENT" in f for f in junk.findings))
        # A partial import SUBSTANTIATED by an active imports/<run-id> run triages stray paths (VALID):
        # the stray junk and the active run interior both land in triage, never a finding (codex-2).
        RUNID = "imp-20260601T000000Z-0123456789abcdef"
        pm = clean_machine()
        pm["manifest.toml"] = base_manifest()
        pm["manifest.toml"]["opf"]["import_status"] = "partial"
        partial = run(pm, working={"junk.md": "x", "imports/{}/plan.toml".format(RUNID): "schema = 1"})
        check("partial-import-triage-not-finding",
              partial.status == VALID and any("junk.md" in t for t in partial.triage))
        # Staged ids join the C-ID-SPACE uniqueness union under a substantiated partial (spec 11: uniqueness
        # across active, archive, and staging). Containment only triages the run interior there, so a
        # staged BI-1 duplicating committed BI-1 is caught by the staged-id enumeration alone; without
        # _staged_ids this store validates VALID. The companion stages a fresh id and stays VALID, and a
        # staged row with no id is CANNOT-EVALUATE, never skipped.
        def staged(rows):
            sp = clean_machine()
            sp["manifest.toml"] = base_manifest()
            sp["manifest.toml"]["opf"]["import_status"] = "partial"
            return run(sp, working={"imports/{}/plan.toml".format(RUNID): "schema = 1",
                                    "imports/{}/candidate/backlog_item.index.toml".format(RUNID): idx(rows)})
        stdup = staged([bi(1, "open")])
        check("staged-duplicate-id-invalid", stdup is not None and stdup.status == INVALID)
        check("staged-duplicate-id-named",
              stdup is not None
              and any(f.startswith("C-ID-SPACE: duplicate id 'BI-1'") for f in stdup.findings)
              and stdup.checks.get("C-ID-SPACE") == "FINDING")
        stnew = staged([bi(3, "open")])
        check("staged-fresh-id-valid", stnew is not None and stnew.status == VALID)
        stbad = staged([{"type": "backlog_item"}])
        check("staged-malformed-row-cant",
              stbad is not None and stbad.status == CANNOT_EVALUATE
              and stbad.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE")
        # A DIRECTORY under candidate/ or fragments/ is never skipped: at a staged index path (here holding a
        # duplicate BI-1) or as an unknown subdirectory, its content is not enumerated, so the store is
        # CANNOT-EVALUATE naming it under C-ID-SPACE. Flip: iterating only the listed files there (the
        # round-3 defect) validates each of these VALID, since containment only triages the run interior.
        def staged_tree(files):
            sp = clean_machine()
            sp["manifest.toml"] = base_manifest()
            sp["manifest.toml"]["opf"]["import_status"] = "partial"
            working = {"imports/{}/plan.toml".format(RUNID): "schema = 1"}
            working.update(("imports/{}/{}".format(RUNID, k), v) for k, v in files.items())
            return run(sp, working=working)
        for label, sub, entry in (("index", "candidate", "backlog_item.index.toml"),
                                  ("worklog", "fragments", WORKLOG_NAME),
                                  ("unknown", "candidate", "nested")):
            stdir = staged_tree({"{}/{}/backlog_item.index.toml".format(sub, entry): idx([bi(1, "open")])})
            check("staged-dir-{}-cant".format(label),
                  stdir is not None and stdir.status == CANNOT_EVALUATE
                  and stdir.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE"
                  and any("{}/{}".format(sub, entry) in m and "could hide staged ids" in m
                          for m in stdir.by_check.get("C-ID-SPACE", [])))
        # The same directory per homes-2 staging root (staging/import and staging/ingest), at the
        # _staged_ids level: homes 2 is latent in this build, so no shipped store reaches those roots
        # through validate_store. The regular-file control in each root still contributes its id.
        for root_kind in ("imports", "staging/import", "staging/ingest"):
            for shape in ("dir", "file"):
                tail = "candidate/backlog_item.index.toml"
                if shape == "dir":
                    tail += "/backlog_item.index.toml"
                sroot = build(clean_machine(),
                              working={"{}/{}/{}".format(root_kind, RUNID, tail): idx([bi(1, "open")])})
                sfd = os.open(str(sroot), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    srep = _Report()
                    srep.ran("C-ID-SPACE")
                    sids = _staged_ids(sfd, 2, srep)
                finally:
                    os.close(sfd)
                if shape == "dir":
                    check("staged-ids-dir-{}-cant".format(root_kind.replace("/", "-")),
                          sids == [] and srep.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE"
                          and any("is a directory at a staged index path" in m for m in srep.cannot))
                else:
                    check("staged-ids-file-{}-read".format(root_kind.replace("/", "-")),
                          sids == ["BI-1"] and not srep.cannot)
        # A staged index listed as a regular file but absent at read (the tree changed under the walk) is
        # CANNOT-EVALUATE naming it, never an empty contribution. The contained reader is patched to report
        # that one index absent, as a removal between the listing and the read would. Flip: treating the
        # absent read as a skip validates this store VALID, since no staged id joins the union.
        gone = "imports/{}/candidate/backlog_item.index.toml".format(RUNID)
        real_reader = _read_toml_contained

        def vanishing_reader(fd, relpath):
            if relpath.endswith(gone):
                return None
            return real_reader(fd, relpath)

        globals()["_read_toml_contained"] = vanishing_reader
        try:
            stgone = staged([bi(1, "open")])
        finally:
            globals()["_read_toml_contained"] = real_reader
        check("staged-index-absent-at-read-cant",
              stgone is not None and stgone.status == CANNOT_EVALUATE
              and stgone.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE"
              and any(gone in m and "listed but is absent at read" in m
                      for m in stgone.by_check.get("C-ID-SPACE", [])))
        # codex-2: a STALE partial with no active run does not launder; the stray path grades as a finding
        # and the unsubstantiated declaration is itself flagged -> INVALID.
        stale = clean_machine()
        stale["manifest.toml"] = base_manifest()
        stale["manifest.toml"]["opf"]["import_status"] = "partial"
        sr = run(stale, working=dict([("junk.md", "x")]))
        check("partial-no-active-run-invalid", sr.status == INVALID)
        check("partial-no-active-run-named",
              any("no active" in f and "partial" in f for f in sr.findings))
        um = clean_machine()
        um["manifest.toml"] = base_manifest()
        um["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/manifest.toml"]}
        check("unmanaged-collision-invalid", run(um).status == INVALID)
        # F2: a valid declared unmanaged path (contained, non-colliding) validates VALID; the round-1
        # collision test over-fired on every unmanaged declaration (it matched itself).
        uv = clean_machine()
        uv["manifest.toml"] = base_manifest()
        uv["manifest.toml"]["unmanaged"] = {"paths": [".working/legacy.md"]}
        check("unmanaged-declared-valid", run(uv, working={"legacy.md": "x\n"}).status == VALID)
        sm = clean_machine()
        sm["manifest.toml"] = base_manifest()
        sm["manifest.toml"]["store"] = {"sync_target": "git:git@example.com:store.git"}
        check("sync-mismatch-invalid",
              run(sm, obs={"tracked": "tracked", "actual_remote": "git:git@example.com:OTHER.git",
                           "prior": clean_prior()["prior"]}).status == INVALID)
        check("sync-no-remote-cannot-eval",
              run(sm, obs={"tracked": "tracked", "prior": clean_prior()["prior"]}).status == CANNOT_EVALUATE)

        # --- F1/F2/codex-7 containment by construction ---------------------------------------------
        # F1: an [unmanaged] entry that is an ANCESTOR of the managed tree (".working" contains it) is a
        # collision AND does not launder a stray path -> INVALID with both findings.
        anc = clean_machine()
        anc["manifest.toml"] = base_manifest()
        anc["manifest.toml"]["unmanaged"] = dict(paths=[".working"])
        ar = run(anc, working=dict([("stray.md", "x")]))
        check("unmanaged-ancestor-invalid", ar.status == INVALID)
        check("unmanaged-ancestor-collision-named", any("collides" in f for f in ar.findings))
        check("unmanaged-ancestor-no-launder", any("stray.md" in f for f in ar.findings))
        # F1: the machine dir itself declared unmanaged is a collision (it equals a managed prefix).
        mdir = clean_machine()
        mdir["manifest.toml"] = base_manifest()
        mdir["manifest.toml"]["unmanaged"] = dict(paths=[".working/toml"])
        check("unmanaged-machine-dir-invalid",
              run(mdir, working=dict([("stray2.md", "x")])).status == INVALID)
        # codex-7: a declared [unmanaged] file INSIDE the machine dir that names no managed slot is VALID
        # (merely lying under the machine dir is not a collision; spec 14.2 Keep).
        leg = clean_machine()
        leg["manifest.toml"] = base_manifest()
        leg["manifest.toml"]["unmanaged"] = dict(paths=[".working/toml/legacy.txt"])
        check("unmanaged-inside-machine-valid",
              run(leg, working=dict([("toml/legacy.txt", "legacy")])).status == VALID)
        # F2: worklog is a ledger only, so worklog.index.toml is never a managed leaf -> INVALID.
        wli = clean_machine()
        wli["worklog.index.toml"] = idx([dict(id="WL-999", anything="goes")])
        rwli = run(wli)
        check("worklog-index-leaf-invalid", rwli.status == INVALID)
        check("worklog-index-leaf-named",
              any("C-CONTAINMENT" in f and "worklog.index.toml" in f for f in rwli.findings))
        # PRC-F1: a present importer-type index (legacy_fragment.index.toml) is a managed leaf even when the
        # manifest does not declare the type (decoupled D6, schema-deferred); C-CONTAINMENT must NOT flag it.
        # Without the importer_index_types recognition this flips to a C-CONTAINMENT finding on the LF index.
        lfi = clean_machine()
        lfi["legacy_fragment.index.toml"] = idx([])
        rlfi = run(lfi)
        check("importer-index-leaf-not-flagged",
              not any("C-CONTAINMENT" in f and "legacy_fragment.index.toml" in f for f in rlfi.findings))
        # PRC-F1 read-side (N-1): a PRESENT NON-EMPTY undeclared importer index is now READ by
        # _gather_active_records and content-schema-deferred (the _SCHEMA_DEFERRAL cant under C-RECORDS), so it
        # is GRADED rather than laundered. Without the read of a present importer index (round-2 recognized the
        # containment leaf but read nothing), a rogue LF index drew no C-RECORDS message and passed clean - the
        # N-1 blind spot. The assertion: C-RECORDS emitted the deferral message for the LF index AND the store
        # is not VALID (graded).
        lfr = clean_machine()
        lfr["legacy_fragment.index.toml"] = idx([dict(id="LF-1", body="quarantined fragment")])
        rlfr = run(lfr)
        check("importer-index-content-graded",
              rlfr.status != VALID
              and any("legacy_fragment" in m and "schema-deferred" in m
                      for m in rlfr.by_check.get("C-RECORDS", [])))
        # OPF-IMPORTED-SERIES IS-2: the imported-series managed leaves (spec 8.2/4.2), behind the
        # explicit `imported` activation flag. ACTIVE (imported=True, the spec_version >= 1.3.0 store):
        # worklog.imported.toml and each enabled non-ledger type <type>.imported.index.toml are managed
        # leaves DIRECTLY in the machine store; the importer-only legacy_fragment contributes none (its
        # LF namespace is outside the imported series), and a deeper path of a leaf name stays a stray.
        # The pure legs drive classify_containment directly; the full-doctor legs below cover the
        # INACTIVE grading (the 1.2.0 store and the accepted 2.0.0 / homes = 2 reservation) and,
        # with SUPPORTED_SPEC_VERSION patched to IMPORTED_SPEC_VERSION inside the vector, the
        # ACTIVATED grading end to end through the driver's own derived flag.
        _is2_m = base_manifest()
        _is2_m["types"]["legacy_fragment"] = {"namespace": "LF"}
        _is2_on = classify_containment(_is2_m, ".working/toml", imported=True)
        check("containment-imported-leaf-managed-when-active",
              _is2_on.managed_file(".working/toml/worklog.imported.toml")
              and _is2_on.managed_file(".working/toml/backlog_item.imported.index.toml"))
        check("containment-lf-imported-leaf-never-managed",
              not _is2_on.managed_file(".working/toml/legacy_fragment.imported.index.toml")
              and _is2_on.managed_file(".working/toml/legacy_fragment.index.toml"))
        check("containment-imported-leaf-deep-stray",
              not _is2_on.managed_file(".working/toml/a/worklog.imported.toml")
              and not _is2_on.managed_file(".working/toml/archive/backlog_item.imported.index.toml"))
        # One over-refusal guard (activation must not disturb the clean series: its leaves stay
        # managed) and one over-admission guard (activation must not admit an imported name beyond
        # the exact enabled-type roster: the reserved-excluded transaction gets no leaf).
        check("containment-active-clean-leaves-unchanged",
              _is2_on.managed_file(".working/toml/worklog.toml")
              and _is2_on.managed_file(".working/toml/backlog_item.index.toml")
              and not _is2_on.managed_file(".working/toml/transaction.imported.index.toml"))
        # INACTIVE (the explicit flag False, every 1.2.0 store): the same names stay unregistered
        # strays, pure and full-doctor alike, so an old imported leaf cannot register itself before
        # activation (red here if the flag or its default ever admits them early).
        _is2_off = classify_containment(_is2_m, ".working/toml")
        check("containment-imported-leaf-unmanaged-when-inactive",
              not _is2_off.managed_file(".working/toml/worklog.imported.toml")
              and not _is2_off.managed_file(".working/toml/backlog_item.imported.index.toml"))
        _is2_store = clean_machine()
        _is2_store["worklog.imported.toml"] = {"schema": 1, "entry": []}
        _is2_store["backlog_item.imported.index.toml"] = idx([])
        _is2_run = run(_is2_store)
        check("containment-imported-leaf-stray-when-inactive",
              _is2_run is not None and _is2_run.status == INVALID
              and any("C-CONTAINMENT" in f and "worklog.imported.toml" in f and "unregistered" in f
                      for f in _is2_run.findings)
              and any("backlog_item.imported.index.toml" in f and "unregistered" in f
                      for f in _is2_run.findings))
        # The accepted 2.0.0 / homes = 2 reservation (spec 4.2) is the ONE store C-MANIFEST
        # validates today with a spec_version above IMPORTED_SPEC_VERSION, so it is exactly where a
        # declaration-only gate would activate early: the imported leaves must stay unregistered
        # strays there too (red if imported_series_declared ever reads the declaration alone), and
        # the clean pair itself stays VALID (the planted leaves, never the declaration, cause the
        # findings; the planted bytes are not even TOML, so a VALID verdict would certify files
        # nothing read).
        _is2_h2 = clean_machine()
        _is2_h2["manifest.toml"]["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
        _is2_h2["manifest.toml"]["opf"]["homes"] = 2
        _is2_h2_clean = run(copy.deepcopy(_is2_h2))
        check("containment-homes2-pair-clean-valid",
              _is2_h2_clean is not None and _is2_h2_clean.status == VALID)
        _is2_h2["worklog.imported.toml"] = "deliberately invalid TOML ["
        _is2_h2["backlog_item.imported.index.toml"] = "garbage = ["
        _is2_h2_run = run(_is2_h2)
        check("containment-imported-leaf-stray-on-homes2-pair",
              _is2_h2_run is not None and _is2_h2_run.status == INVALID
              and any("C-CONTAINMENT" in f and "worklog.imported.toml" in f and "unregistered" in f
                      for f in _is2_h2_run.findings)
              and any("backlog_item.imported.index.toml" in f and "unregistered" in f
                      for f in _is2_h2_run.findings))
        # The leaf roster agrees with the imported id/counter namespace set: over the full section-8.1
        # roster (every module enabled, legacy_fragment declared), the types granted a leaf are exactly
        # those whose normative namespace is in _opf_schema.IMPORTED_NAMESPACES, the worklog ledger as
        # worklog.imported.toml and every other as its <type>.imported.index.toml (spec 8.2: both
        # rosters derive from the one section-8.1 taxonomy and cannot drift).
        _is2_roster = _authoritative_types(set(KNOWN_MODULES), {"legacy_fragment": {"namespace": "LF"}})
        check("imported-leaf-roster-agrees-with-namespaces",
              _opf_store.imported_leaf_names(_is2_roster)
              == frozenset({(_opf_store.IMPORTED_WORKLOG_NAME if t in _LEDGER_TYPES
                             else t + _opf_store.IMPORTED_INDEX_SUFFIX)
                            for t, ns in _is2_roster.items() if ns in IMPORTED_NAMESPACES}))
        # ACTIVATED full-doctor fixtures: SUPPORTED_SPEC_VERSION is patched to
        # IMPORTED_SPEC_VERSION inside the vector (the spec 1.3.0 activation, the same per-vector
        # patch idiom check_opf_homes uses for SUPPORTED_HOMES), so a spec_version 1.3.0 store
        # validates and the driver-derived flag is True end to end. The activated store carries the
        # zero imported counter row of every enabled type (spec 8.2), which C-COUNTERS requires once
        # the series is graded, and its run's prior carries the empty imported baseline the observer
        # reads for such a store.
        _is2_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The clean activated store with both imported leaves present REGISTERS them: no
            # unregistered-stray finding (red when EITHER flag-forwarding step, the driver to
            # _check_containment or _check_containment to classify_containment, is removed, because
            # the leaves then grade as unregistered strays), and the activation tripwire routes the
            # run to CANNOT-EVALUATE with the deferred C-IMPORTED-* roster as the ONLY cant, never
            # VALID, because no imported-content check is wired yet (red if an activated store ever
            # certifies while the imported-content checks are deferred).
            _is2_act = clean_machine()
            _is2_act["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            _is2_act["counters.toml"]["counters"].update(
                {"imported:" + ns: 0 for ns in BASELINE_TYPES.values()})
            _is2_act["worklog.imported.toml"] = {"schema": 1, "entry": []}
            _is2_act["backlog_item.imported.index.toml"] = idx([])
            _is2_act_obs = clean_prior()
            _is2_act_obs["prior"]["imported"] = {}
            _is2_act_run = run(copy.deepcopy(_is2_act), obs=_is2_act_obs)
            check("containment-imported-leaf-registered-when-activated",
                  _is2_act_run is not None and not _is2_act_run.findings
                  and _is2_act_run.status == CANNOT_EVALUATE
                  and len(_is2_act_run.cannot_evaluate) == 1
                  and "C-IMPORTED" in _is2_act_run.cannot_evaluate[0])
            # The leaf roster the classification derives is the ENABLED types, never the full
            # section-8.1 roster: a disabled module type (release is not enabled here) gets no leaf,
            # so its imported index stays an unregistered stray even while activated (red if the
            # imported_leaf_names call is ever fed the full roster instead of enabled_types). The
            # activation-tripwire cant dominates the finding, so the status is asserted as
            # not-VALID, and the discriminating assertion is the unregistered finding itself.
            _is2_act["release.imported.index.toml"] = idx([])
            _is2_dis_run = run(_is2_act)
            check("containment-disabled-type-imported-leaf-stray-when-activated",
                  _is2_dis_run is not None and _is2_dis_run.status != VALID
                  and any("C-CONTAINMENT" in f and "release.imported.index.toml" in f
                          and "unregistered" in f for f in _is2_dis_run.findings))
            # Writer/doctor single authority (F10-1): plan_write_scope passes the SAME derived flag
            # to the SAME classify_containment, so on an activated manifest whose [unmanaged]
            # declaration names an imported leaf BOTH refuse the collision, naming the same finding
            # (red when the write guard falls back to the default flag: it would then plan writes
            # over a declaration the doctor flags as colliding).
            import _opf_write_guard
            _is2_wg_m = base_manifest()
            _is2_wg_m["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            _is2_wg_m["unmanaged"] = dict(paths=[".working/toml/worklog.imported.toml"])
            _is2_wg_cls = classify_containment(
                _is2_wg_m, ".working/toml",
                imported=_opf_store.imported_series_declared(_is2_wg_m))
            try:
                _opf_write_guard.plan_write_scope(".working/toml", _is2_wg_m,
                                                  (".working/toml/manifest.toml",), "upgrade")
                _is2_wg_refused = None
            except _opf_write_guard.WriteGuardError as exc:
                _is2_wg_refused = str(exc)
            check("containment-writer-doctor-agree-on-imported-collision",
                  bool(_is2_wg_cls.colliding) and _is2_wg_refused is not None
                  and all(msg in _is2_wg_refused for msg in _is2_wg_cls.colliding))
            # The SAME agreement in the doctor's own idiom, not a direct classify call: the FULL
            # doctor run over the colliding declaration emits the C-CONTAINMENT collision
            # finding, and plan_write_scope refuses the SAME manifest naming that collision
            # verbatim (red when the write guard falls back to the default flag while the
            # doctor-derived flag is True: the writer would then plan writes over a
            # declaration the doctor flags as colliding).
            _is2_agx = clean_machine()
            _is2_agx["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            _is2_agx["manifest.toml"]["unmanaged"] = dict(
                paths=[".working/toml/worklog.imported.toml"])
            _is2_agx_run = run(copy.deepcopy(_is2_agx))
            try:
                _opf_write_guard.plan_write_scope(".working/toml", _is2_agx["manifest.toml"],
                                                  (".working/toml/manifest.toml",), "upgrade")
                _is2_agx_refused = None
            except _opf_write_guard.WriteGuardError as exc:
                _is2_agx_refused = str(exc)
            _is2_agx_coll = [f for f in (_is2_agx_run.findings if _is2_agx_run is not None else ())
                             if "collides" in f]
            check("containment-writer-doctor-agree-on-doctor-run-when-activated",
                  _is2_agx_run is not None and _is2_agx_run.status != VALID
                  and any("worklog.imported.toml" in f for f in _is2_agx_coll)
                  and _is2_agx_refused is not None
                  and all(f in _is2_agx_refused for f in _is2_agx_coll))
            # The activation tripwire end to end, the garbage-leaf probe fail-closed: both
            # imported leaves hold bytes that are not even TOML, and with every imported-content
            # check deferred nothing reads them, so a VALID verdict would certify files nothing
            # read. The tripwire routes the ACTIVE store to CANNOT-EVALUATE naming the deferred
            # checks (red if an activated store with unread imported leaves ever certifies).
            _is2_p1 = clean_machine()
            _is2_p1["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            _is2_p1["worklog.imported.toml"] = "deliberately invalid TOML ["
            _is2_p1["backlog_item.imported.index.toml"] = "garbage = ["
            _is2_p1_run = run(_is2_p1)
            check("containment-activated-garbage-leaf-never-certified",
                  _is2_p1_run is not None and _is2_p1_run.status == CANNOT_EVALUATE
                  and any("C-IMPORTED" in m and "unread" in m
                          for m in _is2_p1_run.cannot_evaluate))
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _is2_prev_ssv
        # The writer/doctor agreement in the OTHER direction, at the SHIPPED support (the derived
        # flag False): an [unmanaged] declaration of an imported-leaf NAME is a legitimate
        # declaration while the series is inactive (the name is not a managed leaf), so the FULL
        # doctor run certifies the store with the declared file covered, and plan_write_scope
        # plans the SAME manifest without refusing (red when the write guard forces or defaults
        # the flag ACTIVE: it would then refuse a declaration the doctor certifies, the
        # over-refusal divergence; fail-closed, but a divergence all the same).
        _is2_agi = clean_machine()
        _is2_agi["manifest.toml"]["unmanaged"] = dict(paths=[".working/toml/worklog.imported.toml"])
        _is2_agi["worklog.imported.toml"] = "unmanaged legacy bytes, never read ["
        _is2_agi_run = run(copy.deepcopy(_is2_agi))
        try:
            _is2_agi_scope = _opf_write_guard.plan_write_scope(
                ".working/toml", _is2_agi["manifest.toml"],
                (".working/toml/manifest.toml",), "upgrade")
        except _opf_write_guard.WriteGuardError:
            _is2_agi_scope = None
        check("containment-writer-doctor-agree-inactive-unmanaged-imported-name",
              _is2_agi_run is not None and _is2_agi_run.status == VALID
              and not any("worklog.imported.toml" in f for f in _is2_agi_run.findings)
              and _is2_agi_scope is not None
              and ".working/toml/manifest.toml" in _is2_agi_scope["store"])
        # The tooling-side tripwire at the SHIPPED constants (red on a bare activation): once
        # SUPPORTED_SPEC_VERSION reaches IMPORTED_SPEC_VERSION the imported-content checks must
        # exist, be NEW ids in the exact id grammar (never a placeholder reusing an id from
        # REQUIRED_CHECKS or HOMES2_CHECKS), and sit inside required_checks(h) for EVERY homes
        # generation h from 1 through SUPPORTED_HOMES. Activating the imported series and
        # activating homes 2 are SEPARATE gates: today an active series is graded against
        # required_checks(1), so membership in the homes-2 roster alone binds nothing. The
        # assertion calls the module predicate imported_content_checks_landed itself, so this
        # shipped-constants call and the controlled vectors below exercise the SAME code, and
        # the runtime tripwire above never stands alone.
        check("imported-content-checks-required-once-supported",
              imported_content_checks_landed())
        # The SAME predicate under controlled versions, rosters, and wirings (every patched name
        # restored in finally; no store fixture runs inside this block). The empty, out-of-roster,
        # reused-id, grammar and spelling, collision, battery, homes-2-only, omission, and
        # no-generation vectors assert the False side at a raised support, and each arm is
        # discriminated ON ITS OWN: deleting the non-empty, membership, grammar, disjointness, or
        # generation-floor condition from the predicate, one at a time, reds a committed vector
        # in an ordinary suite run. The generated batteries below also pin the grammar,
        # collision, and membership tests against narrowing to some sorted ranks or tuple
        # positions; their comment states the exact coverage and the set-order residual.
        _icc_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        _icc_prev_homes = _opf_store.SUPPORTED_HOMES
        _icc_prev_roster = IMPORTED_CONTENT_CHECKS
        _icc_prev_rc = required_checks
        _icc_prev_req = REQUIRED_CHECKS
        _icc_prev_h2 = HOMES2_CHECKS
        _icc_g = globals()
        try:
            # Unsupported tooling defers, whatever the roster holds (the deferral arm) and
            # whatever SUPPORTED_HOMES holds: with no supported generation the deferral still
            # answers first, so this vector reds a predicate that tests the generation floor
            # before the deferral.
            _opf_store.SUPPORTED_SPEC_VERSION = "1.2.0"
            _icc_unsup_empty = imported_content_checks_landed()
            _icc_g["IMPORTED_CONTENT_CHECKS"] = ("C-IMPORTED-PLACEHOLDER",)
            _icc_unsup_placeholder = imported_content_checks_landed()
            _opf_store.SUPPORTED_HOMES = 0
            _icc_unsup_nohomes = imported_content_checks_landed()
            _opf_store.SUPPORTED_HOMES = _icc_prev_homes
            check("imported-content-gate-defers-while-unsupported",
                  _icc_unsup_empty and _icc_unsup_placeholder and _icc_unsup_nohomes)
            # Supported tooling, empty roster: red (the non-empty arm).
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            _icc_g["IMPORTED_CONTENT_CHECKS"] = ()
            check("imported-content-gate-empty-roster-red-once-supported",
                  not imported_content_checks_landed())
            # Supported, a NEW id nothing wires into any required roster: red (the membership
            # arm; this is the vector a deleted membership condition reds).
            _icc_g["IMPORTED_CONTENT_CHECKS"] = ("C-IMPORTED-PLACEHOLDER",)
            check("imported-content-gate-out-of-roster-red-once-supported",
                  not imported_content_checks_landed())
            # Supported, a placeholder reusing an EXISTING required id: red (the disjointness
            # and prefix arms TOGETHER; C-CONTAINMENT sits inside every required roster, so the
            # membership arm alone would pass it, and each of the two arms is discriminated on
            # its own by the three vectors below).
            _icc_g["IMPORTED_CONTENT_CHECKS"] = ("C-CONTAINMENT",)
            check("imported-content-gate-reused-id-red-once-supported",
                  not imported_content_checks_landed())
            # ONLY the grammar arm refuses a FRESH id outside the exact grammar wired into every
            # required roster (a NEW id, so disjointness passes; wired, so membership passes): an
            # unprefixed id, a lower-case or a mixed-case prefix, a leading space, surrounding
            # spaces, a leading tab, and then ids that keep the exact prefix, so a prefix test
            # accepts them: a trailing space, a trailing tab, a trailing line feed (a $-anchored
            # match accepts it), an embedded space, a lower-case letter, a digit, a non-ASCII
            # upper-case letter, the bare prefix, and a trailing hyphen. The rows stay red when
            # the test folds case (cid.upper()) or strips whitespace (cid.strip()).
            _icc_spelled = ("C-NEWCONTENT", "c-imported-A", "C-Imported-A", " C-IMPORTED-A",
                            " C-IMPORTED-A ", "\tC-IMPORTED-A", "C-IMPORTED-A ", "C-IMPORTED-A\t",
                            "C-IMPORTED-A\n", "C-IMPORTED-A B", "C-IMPORTED-a", "C-IMPORTED-A2",
                            "C-IMPORTED-\u00c9", "C-IMPORTED-", "C-IMPORTED-A-")
            # Canonical controls in the SAME wiring read True: the four imported-series checks
            # spec 8.3 names and a two-run id, so no red above comes from the wiring, and the
            # grammar admits the ids it is derived from.
            _icc_canonical = ("C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE",
                              "C-IMPORTED-SEGREGATION", "C-IMPORTED-A-B")
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc, _w=_icc_spelled + _icc_canonical: _rc(homes) + _w)
            _icc_unpref_one = []
            for _icc_cid in _icc_spelled:
                _icc_g["IMPORTED_CONTENT_CHECKS"] = (_icc_cid,)
                _icc_unpref_one.append(imported_content_checks_landed())
            check("imported-content-gate-unprefixed-new-id-red-once-supported",
                  _icc_unpref_one == [False] * 15)
            _icc_canonical_one = []
            for _icc_cid in _icc_canonical:
                _icc_g["IMPORTED_CONTENT_CHECKS"] = (_icc_cid,)
                _icc_canonical_one.append(imported_content_checks_landed())
            check("imported-content-gate-canonical-id-green-once-supported",
                  _icc_canonical_one == [True] * 5)
            # Generated batteries for the multi-id arms (a one-id roster cannot tell all() from
            # any(), a subset from an overlap, or any-collides from all-collide). _icc_battery
            # takes literal rows of (sorted rank, offender, ids) and a stage step that wires the
            # offender in as the arm under test needs, then evaluates every row in every tuple
            # order of its ids (_icc_orders, built here from itertools.permutations and never
            # shared with the predicate: two orders of two ids, six of three, 24 of four). It
            # reads True only when every evaluation gives the wanted verdict, every roster is a
            # reordering of its row ids with the offender at the written rank, the written
            # (roster size, rank) placements are exactly the ones stated, and the stated number
            # of distinct (offender, roster) cases was evaluated.
            # Membership and collision batteries: three id sets, C-IMPORTED-A and -B, -A to -C,
            # and -A to -D, with the offender in turn each id of its set, 118 cases each. Every
            # roster is evaluated once with each of its ids as the sole offender, so a test
            # narrowed to ids picked from the roster alone (tuple positions, sorted ranks, the
            # set iteration order) that leaves out an id of a two-, three- or four-id roster
            # misses the offender in at least one case and reads True there, under every hash
            # seed; the membership battery also reds an overlap, the collision batteries a test
            # refusing only an all-colliding roster. Residual: a narrowing that leaves out only
            # ids from the fifth on escapes, because no roster here holds five or more ids.
            # Grammar battery: whether an id matches the grammar belongs to the id, not to the
            # wiring, so the offender cannot rotate within one set; the rows instead place it at
            # every sorted rank of a two-, a three- and a four-id roster, 11 sets, 130 cases. An
            # unprefixed id sorts first (C-AFRESHCONTENT, C-BASECONTENT) or last (C-NEWCONTENT,
            # C-PLAINCONTENT, C-ZBAD) among C-IMPORTED- ids, and an id that keeps the prefix but
            # breaks the grammar can sort between two canonical ids (C-IMPORTED-L with a trailing
            # space sorts after C-IMPORTED-L and before C-IMPORTED-M). Red under any(), and under
            # a test of any fixed sorted ranks or tuple positions that leaves out a rank or a
            # position of a two-, three- or four-id roster. Ranks 0 and 2 are the endpoints of a
            # three-id roster only: a test of those two ranks passes the four-id roster
            # C-IMPORTED-A, -B, -C and C-ZBAD and the middle-rank rows, so it is red here, and so
            # is a test of the minimum and maximum alone (equivalent to a full prefix test, but not
            # to the grammar). Residuals: a narrowing that misses only ranks or positions from the
            # fifth on escapes, because no roster here holds five or more ids; and a test
            # narrowed to the first id in SET iteration order escapes under any hash seed that
            # iterates the offender of every set first in every order, so it is not red under
            # every seed.
            _icc_orders = {n: tuple(itertools.permutations(range(n))) for n in (2, 3, 4)}
            _icc_pair = ("C-IMPORTED-A", "C-IMPORTED-B")
            _icc_trio = ("C-IMPORTED-A", "C-IMPORTED-B", "C-IMPORTED-C")
            _icc_quad = ("C-IMPORTED-A", "C-IMPORTED-B", "C-IMPORTED-C", "C-IMPORTED-D")
            _icc_rotated = ((0, "C-IMPORTED-A", _icc_pair), (1, "C-IMPORTED-B", _icc_pair),
                            (0, "C-IMPORTED-A", _icc_trio), (1, "C-IMPORTED-B", _icc_trio),
                            (2, "C-IMPORTED-C", _icc_trio),
                            (0, "C-IMPORTED-A", _icc_quad), (1, "C-IMPORTED-B", _icc_quad),
                            (2, "C-IMPORTED-C", _icc_quad), (3, "C-IMPORTED-D", _icc_quad))
            _icc_rotated_placed = ((2, 0), (2, 1), (3, 0), (3, 1), (3, 2),
                                   (4, 0), (4, 1), (4, 2), (4, 3))
            _icc_grammar_rows = (
                (0, "C-AFRESHCONTENT", ("C-AFRESHCONTENT", "C-IMPORTED-A")),
                (1, "C-ZBAD", ("C-IMPORTED-A", "C-ZBAD")),
                (0, "C-AFRESHCONTENT", ("C-AFRESHCONTENT", "C-IMPORTED-A", "C-IMPORTED-B")),
                (0, "C-BASECONTENT", ("C-BASECONTENT", "C-IMPORTED-D", "C-IMPORTED-E")),
                (1, "C-IMPORTED-L ", ("C-IMPORTED-L", "C-IMPORTED-L ", "C-IMPORTED-M")),
                (2, "C-NEWCONTENT", ("C-IMPORTED-F", "C-IMPORTED-G", "C-NEWCONTENT")),
                (2, "C-PLAINCONTENT", ("C-IMPORTED-H", "C-IMPORTED-K", "C-PLAINCONTENT")),
                (0, "C-AFRESHCONTENT",
                 ("C-AFRESHCONTENT", "C-IMPORTED-A", "C-IMPORTED-B", "C-IMPORTED-C")),
                (1, "C-IMPORTED-A ",
                 ("C-IMPORTED-A", "C-IMPORTED-A ", "C-IMPORTED-B", "C-IMPORTED-C")),
                (2, "C-IMPORTED-B ",
                 ("C-IMPORTED-A", "C-IMPORTED-B", "C-IMPORTED-B ", "C-IMPORTED-C")),
                (3, "C-ZBAD", ("C-IMPORTED-A", "C-IMPORTED-B", "C-IMPORTED-C", "C-ZBAD")))
            _icc_grammar_placed = ((2, 0), (2, 1), (3, 0), (3, 0), (3, 1), (3, 2), (3, 2),
                                   (4, 0), (4, 1), (4, 2), (4, 3))

            def _icc_battery(rows, ranks, stage, count, want):
                verdicts, cases = [], set()
                placed = sorted((len(ids), rank) for rank, _, ids in rows) == sorted(ranks)
                for rank, offender, ids in rows:
                    stage(offender)
                    for order in _icc_orders[len(ids)]:
                        roster = tuple(ids[i] for i in order)
                        _icc_g["IMPORTED_CONTENT_CHECKS"] = roster
                        verdicts.append(imported_content_checks_landed())
                        cases.add((offender, roster))
                        placed = (placed and sorted(roster) == sorted(ids)
                                  and sorted(roster).index(offender) == rank)
                return (placed and len(verdicts) == count and len(cases) == count
                        and verdicts == [want] * count)

            def _icc_unwire(offender):
                wired = tuple(cid for cid in _icc_quad if cid != offender)
                _icc_g["required_checks"] = (
                    lambda homes, _rc=_icc_prev_rc, _w=wired: _rc(homes) + _w)

            def _icc_collide_required(offender):
                _icc_g["REQUIRED_CHECKS"] = _icc_prev_req + (offender,)

            def _icc_collide_homes2(offender):
                _icc_g["HOMES2_CHECKS"] = _icc_prev_h2 + (offender,)

            # ONLY the disjointness arm refuses: the offender ALSO sits in REQUIRED_CHECKS, then in
            # HOMES2_CHECKS, while EVERY id is wired into every required roster, so membership
            # passes. Each patched roster is staged back after its battery and restored again in
            # finally.
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc, _w=_icc_quad: _rc(homes) + _w)
            check("imported-content-gate-required-collision-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_collide_required, 118, False))
            _icc_g["REQUIRED_CHECKS"] = _icc_prev_req
            check("imported-content-gate-homes2-collision-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_collide_homes2, 118, False))
            _icc_g["HOMES2_CHECKS"] = _icc_prev_h2
            # ONLY the grammar arm refuses: each set holds one fresh offender outside the
            # grammar, and every id is wired into every required roster.
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc, _w=tuple(sorted(
                    {cid for _, _, ids in _icc_grammar_rows for cid in ids})): _rc(homes) + _w)
            check("imported-content-gate-multi-id-unprefixed-red-once-supported",
                  _icc_battery(_icc_grammar_rows, _icc_grammar_placed, lambda offender: None,
                               130, False))
            # ONLY the membership arm refuses: the offender is the one id left unwired.
            check("imported-content-gate-multi-id-partly-wired-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_unwire, 118, False))
            # Green control over the same 118 cases: every id wired and nothing colliding reads
            # True, so no red above comes from the roster size or the order.
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc, _w=_icc_quad: _rc(homes) + _w)
            check("imported-content-gate-multi-id-wired-green-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, lambda offender: None, 118, True))
            # Supported, the legitimate activation wiring: a NEW id joined into
            # required_checks(h) for every generation reads True, and the SAME id wired ONLY
            # into the homes-2 roster stays red while generation 1 is supported, because an
            # active series is graded against required_checks(1) today.
            _icc_g["IMPORTED_CONTENT_CHECKS"] = ("C-IMPORTED-CONTENT",)
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc: _rc(homes) + ("C-IMPORTED-CONTENT",))
            check("imported-content-gate-legitimate-roster-green-once-supported",
                  imported_content_checks_landed())
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc:
                _rc(homes) + (("C-IMPORTED-CONTENT",) if homes >= 2 else ()))
            check("imported-content-gate-homes2-only-wiring-red-once-supported",
                  not imported_content_checks_landed())
            # The bound is EVERY supported generation, never the top or the bottom generation
            # alone: with SUPPORTED_HOMES raised to 2, the homes-2-only wiring stays red
            # (generation 1 is still unwired), a generation-1-only wiring stays red (generation
            # 2 is unwired), omitting EACH supported generation in turn while wiring the others
            # stays red, and only the every-generation wiring reads True (red when the checked
            # range collapses, e.g. to (1,)).
            _opf_store.SUPPORTED_HOMES = 2
            check("imported-content-gate-homes2-only-wiring-red-at-homes-2",
                  not imported_content_checks_landed())
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc:
                _rc(homes) + (("C-IMPORTED-CONTENT",) if homes < 2 else ()))
            check("imported-content-gate-generation1-only-wiring-red-at-homes-2",
                  not imported_content_checks_landed())
            _icc_omissions = []
            for _icc_skip in range(1, _opf_store.SUPPORTED_HOMES + 1):
                _icc_g["required_checks"] = (
                    lambda homes, _rc=_icc_prev_rc, _skip=_icc_skip:
                    _rc(homes) if homes == _skip else _rc(homes) + ("C-IMPORTED-CONTENT",))
                _icc_omissions.append(imported_content_checks_landed())
            check("imported-content-gate-each-generation-omission-red-at-homes-2",
                  _icc_omissions == [False] * _opf_store.SUPPORTED_HOMES)
            _icc_g["required_checks"] = (
                lambda homes, _rc=_icc_prev_rc: _rc(homes) + ("C-IMPORTED-CONTENT",))
            check("imported-content-gate-every-generation-green-at-homes-2",
                  imported_content_checks_landed())
            # With the series still supported, below one supported generation the EVERY-generation
            # range is empty: the predicate refuses outright rather than reading the vacuous all()
            # True (fail closed).
            _opf_store.SUPPORTED_HOMES = 0
            check("imported-content-gate-no-supported-generation-red",
                  not imported_content_checks_landed())
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _icc_prev_ssv
            _opf_store.SUPPORTED_HOMES = _icc_prev_homes
            _icc_g["IMPORTED_CONTENT_CHECKS"] = _icc_prev_roster
            _icc_g["required_checks"] = _icc_prev_rc
            _icc_g["REQUIRED_CHECKS"] = _icc_prev_req
            _icc_g["HOMES2_CHECKS"] = _icc_prev_h2
        # The per-series id grading (spec 8.2/8.3), keyed on the containment flag alone
        # (_opf_store.imported_series_declared: the declared spec_version and this tooling's imported-series
        # support, at either homes generation). These vectors grade the 2.0.0 / homes = 2 store, so BOTH
        # tooling gates are patched inside the block (the check_opf_homes per-vector idiom), SUPPORTED_HOMES
        # to 2 and SUPPORTED_SPEC_VERSION to IMPORTED_SPEC_VERSION, so the store grades at homes 2 and the
        # driver derives the flag True end to end; both are restored in finally. The homes-1 1.3.0 store is
        # graded under the same flag (its vectors follow the unread-leaf one). Containment then registers the
        # imported leaves, and with no imported-content check landed its activation tripwire routes every
        # active store to CANNOT-EVALUATE: the vectors judge the per-check verdicts, and each control asserts
        # that the tripwire is its ONLY non-passing verdict.
        _i3_ns = sorted(set(BASELINE_TYPES.values()))

        def _i3_machine(imp=None, drop=(), leaves=None):
            m = clean_machine()
            m["manifest.toml"]["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
            m["manifest.toml"]["opf"]["homes"] = 2
            rows = {"imported:" + ns: 0 for ns in _i3_ns}
            rows.update(imp or {})
            for key in drop:
                rows.pop(key, None)
            m["counters.toml"]["counters"].update(rows)
            m.update(leaves or {})
            return m

        def _i3_row(rid, **over):
            r = {"id": rid, "type": "backlog_item", "status": "done", "title": "t",
                 "actor": {"kind": "importer", "id": "assistant"}}
            r.update(over)
            return r

        def _i3_wl(n):
            return {"id": "imported:WL-{}".format(n), "type": "worklog", "status": "recorded",
                    "actor": {"kind": "importer", "id": "assistant"}, "kind": "added",
                    "summary": "i{}".format(n)}

        def _i3_obs(imported=None, high=None):
            o = clean_prior()
            o["prior"]["imported"] = {} if imported is None else imported
            o["prior"]["counters_high"].update(high or {})
            return o

        def _i3_run(machine, obs=None):
            return run(machine, obs=_i3_obs() if obs is None else obs)

        def _i3_bad(r):
            return {c for c, v in r.checks.items() if v != "PASS"} if r is not None else None

        def _i3_msgs(r, cid):
            return r.by_check.get(cid, []) if r is not None else []

        def _i3_tripwire(r):
            # C-CONTAINMENT is CANNOT-EVALUATE with the activation tripwire as its only message.
            msgs = _i3_msgs(r, "C-CONTAINMENT")
            return (r is not None and r.checks.get("C-CONTAINMENT") == "CANNOT-EVALUATE" and len(msgs) == 1
                    and "C-IMPORTED" in msgs[0] and "unread" in msgs[0])

        def _i3_tripwire_only(r):
            return _i3_tripwire(r) and _i3_bad(r) == {"C-CONTAINMENT"} and not r.findings

        _i3_bi2 = {"backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1"), _i3_row("imported:BI-2")])}
        _i3_prev_homes = _opf_store.SUPPORTED_HOMES
        _i3_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The clean active store with a zero row for every imported counter (spec 8.2) passes every
            # check but the containment tripwire (red when the flag stays False: the imported rows are then
            # C-COUNTERS findings).
            _i3_clean = _i3_run(_i3_machine())
            check("imported-active-clean-only-tripwire", _i3_tripwire_only(_i3_clean))
            # C-COUNTERS requires the imported rows on an active store (red when imported_namespaces is not
            # passed: the missing row is then no finding, and with every row dropped only the tripwire is
            # left). As for a missing clean row, the overall status is dominated by the correlated
            # C-HISTORY-COUNTERS cant (the current counters did not evaluate), so discriminate on the
            # per-check verdict.
            _i3_miss = _i3_run(_i3_machine(drop=("imported:BI",)))
            check("imported-counter-row-missing-finding",
                  _i3_miss is not None and _i3_miss.checks.get("C-COUNTERS") == "FINDING"
                  and [f for f in _i3_miss.findings if "imported" in f]
                  == ["counters.toml: [counters] is missing a high-water for imported namespace "
                      "'imported:BI' (spec 8.2)"])
            _i3_none = _i3_run(_i3_machine(drop=tuple("imported:" + ns for ns in _i3_ns)))
            check("imported-counter-rows-all-missing-finding",
                  _i3_none is not None and _i3_none.status == CANNOT_EVALUATE
                  and _i3_bad(_i3_none) == {"C-COUNTERS", "C-HISTORY-COUNTERS", "C-CONTAINMENT"}
                  and _i3_tripwire(_i3_none)
                  and _i3_none.checks.get("C-COUNTERS") == "FINDING"
                  and sum("missing a high-water for imported namespace" in f for f in _i3_none.findings)
                  == len(_i3_ns) == 12)
            # legacy_fragment has no imported counter row: its LF counter stays required as a clean row and
            # no imported:LF row is asked for, while a present imported:LF row is refused (spec 8.2).
            _i3_lf = _i3_machine()
            _i3_lf["manifest.toml"]["types"]["legacy_fragment"] = {"namespace": "LF"}
            _i3_lf["counters.toml"]["counters"]["LF"] = 0
            _i3_lf["legacy_fragment.index.toml"] = idx([])
            _i3_lfr = _i3_run(copy.deepcopy(_i3_lf))
            _i3_lf["counters.toml"]["counters"]["imported:LF"] = 0
            _i3_lfx = _i3_run(_i3_lf)
            check("imported-counter-no-lf-row",
                  _i3_lfr is not None and _i3_lfr.checks.get("C-COUNTERS") == "PASS"
                  and _i3_lfx is not None and _i3_lfx.checks.get("C-COUNTERS") == "FINDING"
                  and any("imported:LF" in f for f in _i3_msgs(_i3_lfx, "C-COUNTERS")))
            # The leaf control: two imported rows within imported:BI = 2 grade clean in every imported-aware
            # check; only the containment tripwire remains (the leaf is registered, never a stray, here).
            _i3_ok = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves=_i3_bi2))
            check("imported-leaf-control-only-tripwire", _i3_tripwire_only(_i3_ok))
            # C-ID-SPACE per series: an imported id above its OWN imported high-water (the clean BI line is 2
            # and holds BI-1 and BI-2) is an unreserved id (red when the imported ids are not graded).
            _i3_above = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2))
            check("imported-id-above-high-water-finding",
                  _i3_above is not None and _i3_above.checks.get("C-ID-SPACE") == "FINDING"
                  and _i3_msgs(_i3_above, "C-ID-SPACE") == ["C-ID-SPACE: imported series: id "
                                                            "\"imported:BI-2\" exceeds the imported:BI high-water 1 "
                                                            "(unreserved id; spec 8.2)".replace(chr(34), chr(39))])
            _i3_dup = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves={"backlog_item.imported.index.toml": idx(
                [_i3_row("imported:BI-1"), _i3_row("imported:BI-2"), _i3_row("imported:BI-2")])}))
            check("imported-duplicate-id-finding",
                  _i3_dup is not None and any("imported series: duplicate id" in f and "imported:BI-2" in f
                                              for f in _i3_msgs(_i3_dup, "C-ID-SPACE")))
            # The series grammar: an imported leaf holds only its own type imported series, never a clean id
            # or another namespace (spec 8.2).
            _i3_for = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={"backlog_item.imported.index.toml": idx(
                [_i3_row("imported:BI-1"), _i3_row("BI-2"), _i3_row("imported:DN-1")])}))
            check("imported-leaf-foreign-series-id-finding",
                  _i3_for is not None and _i3_for.checks.get("C-ID-SPACE") == "FINDING"
                  and len([f for f in _i3_msgs(_i3_for, "C-ID-SPACE") if "is not an imported:BI-<n> id" in f]) == 2)
            # C-NO-DELETION on the imported line: an overhang above the max present id, a gap below it, and a
            # vanished leaf all owe their allocated ids (red when the imported scan is removed).
            _i3_del = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1")])}))
            check("imported-deleted-record-finding",
                  _i3_del is not None and _i3_del.checks.get("C-NO-DELETION") == "FINDING"
                  and any("1 allocated imported:BI-<n> id(s) above imported:BI-1" in f
                          for f in _i3_msgs(_i3_del, "C-NO-DELETION")))
            _i3_gap = _i3_run(_i3_machine(imp={"imported:BI": 3}, leaves={"backlog_item.imported.index.toml": idx(
                [_i3_row("imported:BI-1"), _i3_row("imported:BI-3")])}))
            check("imported-deleted-record-gap-finding",
                  _i3_gap is not None and any("imported:BI-2 is absent from the imported leaves" in f
                                              for f in _i3_msgs(_i3_gap, "C-NO-DELETION")))
            _i3_gone = _i3_run(_i3_machine(imp={"imported:BI": 2}))
            check("imported-deleted-leaf-finding",
                  _i3_gone is not None and _i3_bad(_i3_gone) == {"C-NO-DELETION", "C-CONTAINMENT"}
                  and _i3_tripwire(_i3_gone)
                  and any("2 allocated imported:BI-<n> id(s) above imported:BI-0" in f
                          for f in _i3_msgs(_i3_gone, "C-NO-DELETION")))
            # imported:WL tiles apart from the clean worklog (spec 8.2). An imported gap is the imported line
            # finding only: clean contiguity, frozen coverage and the changelog gates still pass (red when the
            # imported numbers join the clean scan, or when the imported scan is removed).
            _i3_wlg = _i3_run(_i3_machine(imp={"imported:WL": 3}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [_i3_wl(1), _i3_wl(3)]}}))
            check("imported-wl-gap-does-not-break-clean-tiling",
                  _i3_wlg is not None
                  and [f for f in _i3_msgs(_i3_wlg, "C-CONTIGUITY")] == [
                      "C-CONTIGUITY: the imported worklog is not contiguous; imported:WL-2 is missing (the "
                      "imported line is graded apart from clean release tiling, spec 8.2)"]
                  and _i3_wlg.checks.get("C-FROZEN-COVERAGE") == "PASS"
                  and _i3_wlg.checks.get("C-CHANGELOG-GATES") == "PASS")
            # The reverse: a clean worklog gap stays the clean finding, never filled or renamed by a contiguous
            # imported line that holds the missing number, imported:WL-3 (red when the lines merge).
            _i3_rv = _i3_machine(imp={"imported:WL": 3}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [_i3_wl(1), _i3_wl(2), _i3_wl(3)]}})
            _i3_rv["worklog.toml"] = {"schema": 1, "entry": [wl(4)]}
            _i3_rvr = _i3_run(_i3_rv)
            check("clean-wl-gap-not-filled-by-imported-line",
                  _i3_rvr is not None and [f for f in _i3_msgs(_i3_rvr, "C-CONTIGUITY")] == [
                      "C-CONTIGUITY: the worklog (active + archive) is not contiguous; WL-3 is missing (a gap "
                      "breaks span tiling, spec 6.1/11)"])
            _i3_wl1 = _i3_run(_i3_machine(imp={"imported:WL": 2}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [_i3_wl(2)]}}))
            check("imported-wl-not-from-one-finding",
                  _i3_wl1 is not None and any("does not start at imported:WL-1" in f
                                              for f in _i3_msgs(_i3_wl1, "C-CONTIGUITY")))
            # Append-only history over the imported leaves (spec 8.3): an unchanged prior imported record
            # passes; an edited or vanished one is a finding; a prior with no imported baseline is a named
            # CANNOT-EVALUATE; an imported counter regression is a C-HISTORY-COUNTERS finding.
            _i3_one = {"backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1")])}
            _i3_dig = _record_digest(_i3_row("imported:BI-1"))
            _i3_hok = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_one),
                              obs=_i3_obs({"imported:BI-1": _i3_dig}, {"imported:BI": 1}))
            check("imported-history-unchanged-passes", _i3_tripwire_only(_i3_hok))
            _i3_hed = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1", title="edited")])}),
                obs=_i3_obs({"imported:BI-1": _i3_dig}))
            check("imported-record-edited-after-commit-finding",
                  _i3_hed is not None and _i3_hed.checks.get("C-HISTORY-APPEND-ONLY") == "FINDING"
                  and any("imported:BI-1" in f and "changed since the prior committed snapshot" in f
                          for f in _i3_msgs(_i3_hed, "C-HISTORY-APPEND-ONLY")))
            _i3_hgo = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_one),
                              obs=_i3_obs({"imported:BI-1": _i3_dig, "imported:BI-2": _i3_dig}))
            check("imported-record-vanished-after-commit-finding",
                  _i3_hgo is not None and any("imported:BI-2" in f and "is absent from the imported leaves" in f
                                              for f in _i3_msgs(_i3_hgo, "C-HISTORY-APPEND-ONLY")))
            _i3_nob = clean_prior()
            _i3_hnb = _i3_run(_i3_machine(), obs=_i3_nob)
            check("imported-prior-without-baseline-cannot-evaluate",
                  _i3_hnb is not None and _i3_hnb.status == CANNOT_EVALUATE
                  and _i3_hnb.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                  and any("no imported-series baseline" in m for m in _i3_hnb.cannot_evaluate))
            _i3_hrg = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_one),
                              obs=_i3_obs({"imported:BI-1": _i3_dig}, {"imported:BI": 3}))
            check("imported-counter-regressed-finding",
                  _i3_hrg is not None and _i3_hrg.checks.get("C-HISTORY-COUNTERS") == "FINDING"
                  and any("imported:BI" in f and "regressed 3 to 1" in f
                          for f in _i3_msgs(_i3_hrg, "C-HISTORY-COUNTERS")))
            _i3_hmf = _i3_run(_i3_machine(), obs=_i3_obs({"BI-1": _i3_dig}))
            check("imported-prior-malformed-member-when-active",
                  _i3_hmf is not None and _i3_hmf.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                  and any("malformed" in m for m in _i3_msgs(_i3_hmf, "C-HISTORY-APPEND-ONLY")))
            # An unreadable imported leaf, or one with no [[record]] array, is a named CANNOT-EVALUATE and is
            # never read as an empty leaf: neither its allocated ids nor a prior imported record are reported
            # deleted (spec 11: an unreadable input fails closed; red when an unread leaf joins the
            # no-deletion or the append-only scan).
            _i3_unread = []
            for _i3_text in ("record = [ not toml", "schema = 1\nrecord = 1\n"):
                _i3_ur = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves={
                    "backlog_item.imported.index.toml": _i3_text}), obs=_i3_obs({"imported:BI-1": _i3_dig}))
                _i3_unread.append(
                    _i3_ur is not None and _i3_ur.status == CANNOT_EVALUATE
                    and _i3_ur.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE"
                    and any("backlog_item.imported.index.toml" in m for m in _i3_ur.cannot_evaluate)
                    and not any("imported:BI" in m for m in _i3_msgs(_i3_ur, "C-NO-DELETION")
                                + _i3_msgs(_i3_ur, "C-HISTORY-APPEND-ONLY")))
            check("imported-unread-leaf-cannot-evaluate-never-deleted", _i3_unread == [True, True])
            # A 1.3.0 store on homes 1 declares the imported series under supporting tooling, so it is graded
            # as the homes-2 store above is (spec 4.2/8.3), on the homes-1 roster, which has no C-EVIDENCE-ENUM.
            # The clean control, two rows within imported:BI = 2, leaves only the containment tripwire; an
            # imported id above its own high-water is the C-ID-SPACE finding (each red when the grading is keyed
            # on homes 2 again: the imported counter rows are then C-COUNTERS findings and the leaf is unread).
            def _i3_homes1(machine):
                machine["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
                del machine["manifest.toml"]["opf"]["homes"]
                return machine

            _i3_h1ok = _i3_run(_i3_homes1(_i3_machine(imp={"imported:BI": 2}, leaves=_i3_bi2)))
            check("imported-grading-on-homes1-130-store-clean-only-tripwire",
                  _i3_tripwire_only(_i3_h1ok) and "C-EVIDENCE-ENUM" not in _i3_h1ok.checks)
            _i3_h1r = _i3_run(_i3_homes1(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2)))
            check("imported-grading-on-homes1-130-store",
                  _i3_h1r is not None and _i3_h1r.checks.get("C-COUNTERS") == "PASS"
                  and _i3_msgs(_i3_h1r, "C-ID-SPACE") == ["C-ID-SPACE: imported series: id "
                                                          "\"imported:BI-2\" exceeds the imported:BI high-water 1 "
                                                          "(unreserved id; spec 8.2)".replace(chr(34), chr(39))])
            # Homes 2 supported WITHOUT imported-series support (SUPPORTED_SPEC_VERSION back at its shipped
            # value, which a 1.2.0 store needs to pass C-MANIFEST). Support alone never activates: a 1.2.0
            # store without the homes key grades generation 1, needs no imported row, and keeps the former
            # prior handling. The 2.0.0 / homes = 2 store grades generation 2, but the flag is False, so an
            # imported counter row stays the former C-COUNTERS finding and the leaves are never graded (red
            # when the grading reads the homes-2 generation alone).
            _opf_store.SUPPORTED_SPEC_VERSION = _i3_prev_ssv
            _i3_sup = run(clean_machine())
            check("imported-grading-off-without-homes2-declaration",
                  _i3_sup is not None and _i3_sup.status == VALID)
            _i3_h2 = run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2))
            check("imported-grading-off-at-homes2-without-imported-support",
                  _i3_h2 is not None and _i3_h2.checks.get("C-EVIDENCE-ENUM") == "PASS"
                  and _i3_h2.checks.get("C-COUNTERS") == "FINDING"
                  and _i3_h2.checks.get("C-ID-SPACE") == "PASS" and _i3_h2.checks.get("C-NO-DELETION") == "PASS")
        finally:
            _opf_store.SUPPORTED_HOMES = _i3_prev_homes
            _opf_store.SUPPORTED_SPEC_VERSION = _i3_prev_ssv
        # The homes-2-keyed imported_series_active predicate is retired: the driver, containment, the prior
        # imported baseline and the record verb's counter proof all read _opf_store.imported_series_declared.
        # A parallel change that still carries the old definition can merge without a textual conflict, so
        # the module must not define it again (red when it does).
        import ast
        import inspect
        _i3_defs = [n for n in ast.parse(inspect.getsource(sys.modules[__name__])).body
                    if isinstance(n, ast.FunctionDef) and n.name == "imported_series_active"]
        check("imported-series-active-retired", _i3_defs == [])
        # The declaration alone never activates either: at the shipped SUPPORTED_SPEC_VERSION this tooling
        # does not support the imported series, so an imported counter row is still the former C-COUNTERS
        # finding and the leaves are never read (red when the flag reads the declaration without tooling
        # support).
        _i3_off = run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2))
        check("imported-grading-off-at-shipped-support",
              _i3_off is not None and _i3_off.checks.get("C-COUNTERS") == "FINDING"
              and _i3_off.checks.get("C-ID-SPACE") == "PASS" and _i3_off.checks.get("C-NO-DELETION") == "PASS")
        # Inactive prior handling is unchanged: an imported counter key keeps the malformed-prior verdict,
        # and an imported member is ignored.
        _i3_pk = clean_prior()
        _i3_pk["prior"]["counters_high"]["imported:BI"] = 0
        _i3_pkr = run(clean_machine(), obs=_i3_pk)
        _i3_pm = clean_prior()
        _i3_pm["prior"]["imported"] = "not a table"
        _i3_pmr = run(clean_machine(), obs=_i3_pm)
        check("imported-prior-inert-on-homes1",
              _i3_pkr is not None and _i3_pkr.checks.get("C-HISTORY-COUNTERS") == "CANNOT-EVALUATE"
              and any("malformed" in m for m in _i3_msgs(_i3_pkr, "C-HISTORY-COUNTERS"))
              and _i3_pmr is not None and _i3_pmr.status == VALID)
        # C-IMPORTED-PROVENANCE (spec 8.3/8.8/14.1). The fixture is one import bundle,
        # .working/imported/import/<run-id>/, holding the preserved original under originals/ and an inventory.toml
        # that lists it; each imported row's import table cites the run, the original and its source_sha256.
        _p_run = "imp-20260601T000000Z-0123456789abcdef"
        _p_bundle = _opf_store.evidence_run("import", _p_run)
        _p_inv = _opf_store.evidence_inventory("import", _p_run)
        _p_src = _p_bundle + "/originals/notes/TODO.md"
        _p_other = _p_bundle + "/originals/notes/OTHER.md"
        _p_text = "- carry the import\n"

        def _p_sha(text):
            return hashlib.sha256(text.encode("utf-8")).hexdigest()

        def _p_w(path):
            # build() writes its `working` keys beneath .working/.
            return path[len(WORKING_DIRNAME) + 1:]

        def _p_bundle_files(files=None, listed=None):
            # `files` maps each bundle path present on disk to its text; `listed` holds the (path, text) rows the
            # inventory records, by default exactly the files present.
            files = {_p_src: _p_text} if files is None else files
            listed = sorted(files.items()) if listed is None else listed
            out = {_p_w(p): t for p, t in files.items()}
            out[_p_w(_p_inv)] = {"format": EVIDENCE_FORMAT, "file": [
                {"path": p, "size": len(t.encode("utf-8")), "sha256": _p_sha(t)} for p, t in listed]}
            return out

        def _p_row(n, **imp_over):
            imp = {"source": _p_src, "source_sha256": _p_sha(_p_text), "run": _p_run, "imported_at": TS}
            imp.update(imp_over)
            return _i3_row("imported:BI-{}".format(n), **{"import": imp})

        def _p_machine(*rows):
            return _i3_machine(imp={"imported:BI": len(rows)},
                               leaves={"backlog_item.imported.index.toml": idx(list(rows))})

        def _p_doctor(machine, working, after=None):
            root = build(machine, None, working)
            if after is not None:
                after(root)
            res = resolve_store(root)
            return validate_store(res, observations=_i3_obs()) if res.status == RESOLVED else None

        def _p_symlink_original(root):
            # The original becomes a symlink to identical bytes outside the store tree.
            target = root / "outside.md"
            target.write_text(_p_text, encoding="utf-8")
            (root / _p_src).unlink()
            os.symlink(str(target), str(root / _p_src))

        _p_g = globals()
        _p_prev_rc = required_checks
        _p_prev_homes = _opf_store.SUPPORTED_HOMES
        _p_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            # The active imported series (both tooling gates raised, as in the per-series id vectors) on the 2.0.0
            # / homes = 2 store; required_checks is patched to carry C-IMPORTED-PROVENANCE, standing in for the
            # activation wiring, and restored in finally.
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # Before the roster carries the id the check never runs: the stale-digest store below reports no
            # C-IMPORTED-PROVENANCE verdict and no unknown-id fault (red when the run is not gated on the roster).
            _p_new = "- carry the import, edited\n"
            _p_flip_args = (_p_machine(_p_row(1, source_sha256=_p_sha(_p_new)), _p_row(2)),
                            _p_bundle_files({_p_src: _p_new}))
            _p_unwired = _p_doctor(*_p_flip_args)
            check("provenance-not-run-before-roster-wiring",
                  _p_unwired is not None and "C-IMPORTED-PROVENANCE" not in _p_unwired.checks
                  and _i3_tripwire_only(_p_unwired))
            _p_g["required_checks"] = lambda homes, _rc=_p_prev_rc: _rc(homes) + ("C-IMPORTED-PROVENANCE",)
            # The clean control: two rows citing one listed, matching original pass, and only the activation
            # tripwire remains (red when the check over-fires on sound evidence).
            _p_ok = _p_doctor(_p_machine(_p_row(1), _p_row(2)), _p_bundle_files())
            check("provenance-clean-bundle-passes",
                  _p_ok is not None and _p_ok.checks.get("C-IMPORTED-PROVENANCE") == "PASS"
                  and _i3_tripwire_only(_p_ok))
            # A byte flip the bundle inventory was re-sealed over: the original and its inventory row agree, so
            # C-EVIDENCE-ENUM passes, and only imported:BI-2 still records the old source_sha256. The provenance
            # check is the sole layer that sees it, and it names that row alone (red when the digest comparison
            # is removed, or reads only the first row citing the pair).
            _p_flip = _p_doctor(*_p_flip_args)
            check("provenance-byte-flip-finding",
                  _p_flip is not None and _i3_bad(_p_flip) == {"C-CONTAINMENT", "C-IMPORTED-PROVENANCE"}
                  and _i3_tripwire(_p_flip)
                  and _i3_msgs(_p_flip, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: 'imported:BI-2' record source_sha256 {} for {!r}, but the preserved "
                      "original hashes to {} (spec 8.3)".format(_p_sha(_p_text), _p_src, _p_sha(_p_new))])
            # A missing original, still listed, cannot be verified: CANNOT-EVALUATE naming it once, never a pass
            # (red when an absent evidence file is skipped).
            _p_miss = _p_doctor(_p_machine(_p_row(1)), _p_bundle_files({}, listed=[(_p_src, _p_text)]))
            check("provenance-original-missing-cannot-evaluate",
                  _p_miss is not None and _p_miss.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and _i3_msgs(_p_miss, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: evidence file {!r} is missing, so it cannot be verified (spec 8.3: "
                      "missing evidence fails closed)".format(_p_src)])
            # A symlinked original is never followed, even to identical bytes: CANNOT-EVALUATE naming it (red
            # when the read follows the link, which then matches and passes).
            _p_sym = _p_doctor(_p_machine(_p_row(1)), _p_bundle_files(), after=_p_symlink_original)
            _p_sym_msgs = _i3_msgs(_p_sym, "C-IMPORTED-PROVENANCE")
            check("provenance-symlinked-original-cannot-evaluate",
                  _p_sym is not None and _p_sym.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and len(_p_sym_msgs) == 1 and _p_src in _p_sym_msgs[0] and "not a regular file" in _p_sym_msgs[0]
                  and _p_sym_msgs[0] in _p_sym.cannot_evaluate)
            # Provenance anchors only in the cited run's own originals/ folder: a live store path, a malformed run
            # id, and another run's originals are each a finding, and nothing they name is read (red when the
            # originals/ containment test is dropped: the live path is then read and graded instead).
            _p_live = ".working/notes/TODO.md"
            _p_elsewhere = _opf_store.evidence_run("import", "imp-20260601T000000Z-fedcba9876543210")
            _p_out_w = _p_bundle_files()
            _p_out_w[_p_w(_p_live)] = _p_text
            _p_out = _p_doctor(_p_machine(_p_row(1, source=_p_live), _p_row(2, run="imp-not-a-run"),
                                          _p_row(3, source=_p_elsewhere + "/originals/notes/TODO.md")), _p_out_w)
            check("provenance-source-outside-run-originals-finding",
                  _p_out is not None and _i3_msgs(_p_out, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: imported record {!r} carries no verifiable provenance: its import "
                      "table must name an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run, a source under that run's "
                      ".working/imported/import/<run-id>/originals/ folder, and a 64-hex source_sha256 (spec "
                      "8.3/8.8)".format("imported:BI-{}".format(n)) for n in (1, 2, 3)])
            # The original must be listed by its bundle inventory, even when its bytes match its record (red when
            # the listing requirement is removed).
            _p_unl = _p_doctor(_p_machine(_p_row(1)), _p_bundle_files(
                {_p_src: _p_text, _p_other: "other\n"}, listed=[(_p_other, "other\n")]))
            check("provenance-original-unlisted-finding",
                  _p_unl is not None and _i3_msgs(_p_unl, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: the preserved original {!r} cited by 'imported:BI-1' is not listed in "
                      "{!r} (spec 8.8)".format(_p_src, _p_inv)])
            # A cited bundle without its inventory.toml, or with a malformed one, cannot be verified:
            # CANNOT-EVALUATE naming the inventory (red when the absent inventory is skipped, or a malformed one
            # read as an empty listing).
            _p_ni_w = _p_bundle_files()
            del _p_ni_w[_p_w(_p_inv)]
            _p_ni = _p_doctor(_p_machine(_p_row(1)), _p_ni_w)
            check("provenance-inventory-missing-cannot-evaluate",
                  _p_ni is not None and _p_ni.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and _i3_msgs(_p_ni, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: {!r} is missing, so the bundle its imported records cite cannot be "
                      "verified (spec 8.3/8.8)".format(_p_inv)])
            _p_mf_w = _p_bundle_files()
            _p_mf_w[_p_w(_p_inv)] = {"format": EVIDENCE_FORMAT, "file": [{"path": _p_src, "size": 1}]}
            _p_mf = _p_doctor(_p_machine(_p_row(1)), _p_mf_w)
            check("provenance-inventory-malformed-cannot-evaluate",
                  _p_mf is not None and _p_mf.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and _i3_msgs(_p_mf, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: malformed import bundle inventory {!r}: file rows require exactly "
                      "path, size and sha256".format(_p_inv)])
            # An unread imported leaf is named as not evaluated for provenance, never passed (red when the unread
            # series is skipped silently).
            _p_ur = _p_doctor(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": "record = [ not toml"}), _p_bundle_files())
            check("provenance-unread-leaf-cannot-evaluate",
                  _p_ur is not None and _p_ur.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and _i3_msgs(_p_ur, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: the provenance of the imported:BI records is not evaluated, because "
                      "their imported leaf could not be read (spec 8.3)"])
            # A row outside its leaf series is still a row of a readable leaf: C-ID-SPACE records the series
            # finding, and C-IMPORTED-PROVENANCE reads the row's import table, naming the row by its leaf and
            # index, never a pass over a row it did not read. An imported:DN id in the backlog leaf citing a
            # path outside the store, a malformed digest and a malformed run is a provenance finding (red when the
            # misfiled rows are not passed to the check, which then passes).
            _p_leaf_row = ".working/toml/backlog_item.imported.index.toml record[0]"
            _p_mis = _p_doctor(_i3_machine(leaves={"backlog_item.imported.index.toml": idx([_i3_row(
                "imported:DN-1", **{"import": {"source": "/etc/passwd", "source_sha256": "zz", "run": "bogus"}})])}),
                {})
            check("provenance-misfiled-row-read",
                  _p_mis is not None and _p_mis.checks.get("C-ID-SPACE") == "FINDING"
                  and _p_mis.checks.get("C-IMPORTED-PROVENANCE") == "FINDING"
                  and _i3_msgs(_p_mis, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: imported record {!r} carries no verifiable provenance: its import "
                      "table must name an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run, a source under that run's "
                      ".working/imported/import/<run-id>/originals/ folder, and a 64-hex source_sha256 (spec "
                      "8.3/8.8)".format(_p_leaf_row)])
            # A misfiled row with well-formed provenance has its original re-read: a clean id in the imported leaf
            # recording a stale source_sha256 is the digest finding (red when the misfiled rows are not passed to
            # the check).
            _p_mis2 = _p_doctor(_i3_machine(leaves={"backlog_item.imported.index.toml": idx([_i3_row("BI-9", **{
                "import": {"source": _p_src, "source_sha256": _p_sha(_p_new), "run": _p_run,
                           "imported_at": TS}})])}), _p_bundle_files())
            check("provenance-misfiled-row-original-reverified",
                  _p_mis2 is not None and _p_mis2.checks.get("C-ID-SPACE") == "FINDING"
                  and _i3_msgs(_p_mis2, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: {!r} record source_sha256 {} for {!r}, but the preserved original "
                      "hashes to {} (spec 8.3)".format(_p_leaf_row, _p_sha(_p_new), _p_src, _p_sha(_p_text))])
            # A row that is not a table carries no import table at all: a provenance finding naming it, never a
            # fault (red when a non-table row is read as a table, which faults the run).
            _p_mis3 = _p_doctor(_i3_machine(leaves={
                "backlog_item.imported.index.toml": "schema = 1\nrecord = [1]\n"}), {})
            check("provenance-misfiled-non-table-row-finding",
                  _p_mis3 is not None and _p_mis3.checks.get("C-IMPORTED-PROVENANCE") == "FINDING"
                  and not any(m.startswith("internal:") for m in _p_mis3.cannot_evaluate)
                  and _i3_msgs(_p_mis3, "C-IMPORTED-PROVENANCE") == [
                      "C-IMPORTED-PROVENANCE: imported record {!r} carries no verifiable provenance: its import "
                      "table must name an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run, a source under that run's "
                      ".working/imported/import/<run-id>/originals/ folder, and a 64-hex source_sha256 (spec "
                      "8.3/8.8)".format(_p_leaf_row)])
        finally:
            _p_g["required_checks"] = _p_prev_rc
            _opf_store.SUPPORTED_HOMES = _p_prev_homes
            _opf_store.SUPPORTED_SPEC_VERSION = _p_prev_ssv
        # The inventory re-read is the provenance check's own evidence coverage, since C-EVIDENCE-ENUM reads nothing
        # on homes 1 (spec 4.2/14.1). Driven directly over a bundle with no other check present, one imported row
        # citing the original.

        def _p_direct(working, row):
            root = build(clean_machine(), None, working)
            rep = _Report()
            rep.ran("C-IMPORTED-PROVENANCE")
            fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                _check_imported_provenance(fd, [("imported:BI", "imported:BI-1", row)], set(), rep)
            finally:
                os.close(fd)
            return rep

        def _p_mismatch(path):
            return ("C-IMPORTED-PROVENANCE: {!r} does not match the size and digest {!r} records for it (spec "
                    "4.2/14.1)".format(path, _p_inv))

        # The matching original passes, and a listed file whose bytes differ from its row is the one finding (red
        # when the re-read of the listed files is removed).
        _p_rep = _p_direct(_p_bundle_files({_p_src: _p_text, _p_other: "edited\n"},
                                           listed=[(_p_src, _p_text), (_p_other, "other\n")]), _p_row(1))
        check("provenance-inventory-payload-mismatch-finding",
              _p_rep.cannot == [] and _p_rep.findings == [_p_mismatch(_p_other)])
        # A listed file whose digest matches its row but whose size does not is the same finding (red when the
        # re-read compares the digest alone).
        _p_sz_w = _p_bundle_files({_p_src: _p_text, _p_other: "other\n"})
        for _p_sz_row in _p_sz_w[_p_w(_p_inv)]["file"]:
            if _p_sz_row["path"] == _p_other:
                _p_sz_row["size"] += 1
        _p_sz = _p_direct(_p_sz_w, _p_row(1))
        check("provenance-inventory-size-mismatch-finding",
              _p_sz.cannot == [] and _p_sz.findings == [_p_mismatch(_p_other)])
        # An inventory that claims one path twice has unknown claims: CANNOT-EVALUATE naming it, even though the
        # original it lists twice matches (red when the claimed-twice refusal is removed and the later row wins).
        _p_tw = _p_direct(_p_bundle_files(listed=[(_p_src, _p_text), (_p_src, _p_text)]), _p_row(1))
        check("provenance-inventory-claimed-twice-cannot-evaluate",
              _p_tw.findings == [] and _p_tw.cannot == [
                  "C-IMPORTED-PROVENANCE: malformed import bundle inventory {!r}: {!r} is claimed more than "
                  "once".format(_p_inv, _p_src)])
        # A source spelled through a parent component, <bundle>/originals/../inventory.toml, is no canonical path
        # inside the originals folder however it starts: a provenance finding, and nothing is read (red when the
        # canonical-path test is reduced to a prefix match: the path is then read and graded instead).
        _p_dd = _p_direct(_p_bundle_files(), _p_row(1, source=_p_bundle + "/originals/../inventory.toml"))
        check("provenance-source-parent-component-finding",
              _p_dd.cannot == [] and _p_dd.findings == [
                  "C-IMPORTED-PROVENANCE: imported record 'imported:BI-1' carries no verifiable provenance: its "
                  "import table must name an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run, a source under that run's "
                  ".working/imported/import/<run-id>/originals/ folder, and a 64-hex source_sha256 (spec "
                  "8.3/8.8)"])
        # A homes-1 store that declares the imported series under supporting tooling (imported_series_declared)
        # is graded as the homes-2 store is (spec 4.2/8.3): containment registers its imported leaves and
        # bundles, and the driver gathers its imported rows under the same flag. With the roster carrying the
        # check and the activation tripwire disarmed, as activation would leave them (both patched, restored in
        # finally), C-IMPORTED-PROVENANCE re-reads each cited original on homes 1. The store carries the zero
        # imported counter row of every enabled type (spec 8.2), imported:BI at the rows it holds, and its run's
        # prior carries the empty imported baseline the observer reads for it.
        _h1_prev_rc = required_checks
        _h1_prev_icc = IMPORTED_CONTENT_CHECKS
        _h1_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION

        def _h1_machine(*rows):
            m = clean_machine()
            m["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            m["counters.toml"]["counters"].update({"imported:" + ns: 0 for ns in _i3_ns})
            m["counters.toml"]["counters"]["imported:BI"] = len(rows)
            m["backlog_item.imported.index.toml"] = idx(list(rows))
            return m

        def _h1_run(machine, working=None):
            return run(machine, working=working, obs=_i3_obs())

        def _h1_graded(r, status, findings, cannot):
            # C-IMPORTED-PROVENANCE ran, and the run's findings and CANNOT-EVALUATEs are exactly `findings` and
            # `cannot`, all of them that check's, so no other check is non-passing.
            return (r is not None and r.status == status and r.findings == findings
                    and r.cannot_evaluate == cannot
                    and _i3_bad(r) == ({"C-IMPORTED-PROVENANCE"} if findings or cannot else set())
                    and r.checks.get("C-IMPORTED-PROVENANCE") is not None)

        try:
            _p_g["required_checks"] = lambda homes, _rc=_h1_prev_rc: _rc(homes) + ("C-IMPORTED-PROVENANCE",)
            _p_g["IMPORTED_CONTENT_CHECKS"] = ("C-IMPORTED-PROVENANCE",)
            # The control, at the shipped support constant: a store that does not declare the series under
            # supporting tooling has nothing to grade, so the check passes and the store is VALID (red when the
            # check grades a store that does not declare the series).
            _h1_plain = run(clean_machine())
            check("provenance-homes1-undeclared-passes",
                  _h1_plain is not None and _h1_plain.status == VALID
                  and _h1_plain.checks.get("C-IMPORTED-PROVENANCE") == "PASS")
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # Two rows citing one listed original whose bytes match their source_sha256: the check passes and the
            # homes-1 1.3.0 store is VALID (red when the grading is keyed on homes 2 again, which records the store
            # as not evaluated).
            _h1_ok = _h1_run(_h1_machine(_p_row(1), _p_row(2)), working=_p_bundle_files())
            check("provenance-homes1-matching-original-passes",
                  _h1_graded(_h1_ok, VALID, [], []) and _h1_ok.checks.get("C-IMPORTED-PROVENANCE") == "PASS")
            # The preserved original no longer matches the source_sha256 its record carries (the inventory was
            # re-sealed over the new bytes): the C-IMPORTED-PROVENANCE finding naming the record, and nothing
            # else (red when the grading is keyed on homes 2 again).
            _h1_stale = _h1_run(_h1_machine(_p_row(1)), working=_p_bundle_files({_p_src: _p_new}))
            check("provenance-homes1-stale-digest-finding", _h1_graded(_h1_stale, INVALID, [
                "C-IMPORTED-PROVENANCE: 'imported:BI-1' record source_sha256 {} for {!r}, but the preserved "
                "original hashes to {} (spec 8.3)".format(_p_sha(_p_text), _p_src, _p_sha(_p_new))], []))
            # A cited run with no evidence at all is the spec 8.3 missing-evidence CANNOT-EVALUATE, naming the
            # absent inventory and original, never a pass (red when the grading is keyed on homes 2 again).
            _h1_none = _h1_run(_h1_machine(_p_row(1)))
            check("provenance-homes1-no-evidence-cannot-evaluate", _h1_graded(_h1_none, CANNOT_EVALUATE, [], [
                "C-IMPORTED-PROVENANCE: {!r} is missing, so the bundle its imported records cite cannot be "
                "verified (spec 8.3/8.8)".format(_p_inv),
                "C-IMPORTED-PROVENANCE: evidence file {!r} is missing, so it cannot be verified (spec 8.3: "
                "missing evidence fails closed)".format(_p_src)]))
            # An empty imported series has nothing to verify: the check passes and the store is VALID (red when
            # the grading is keyed on homes 2 again).
            _h1_empty = _h1_run(_h1_machine())
            check("provenance-homes1-empty-series-passes",
                  _h1_graded(_h1_empty, VALID, [], []) and _h1_empty.checks.get("C-IMPORTED-PROVENANCE") == "PASS")
            # A store whose declaration cannot be read stays CANNOT-EVALUATE: a spec_version that is not a
            # version string, one that is not SemVer, and none at all each fail C-MANIFEST, and nothing past it is
            # graded, so the stale original beside them is never passed or graded (red when the driver grades
            # on past a manifest it could not read). Unchanged by the homes-1 grading; it guards that the
            # grading opens no route around the declaration.
            _h1_unread = []
            for _h1_decl in (13, "one.three", None):
                _h1_um = _h1_machine(_p_row(1))
                if _h1_decl is None:
                    del _h1_um["manifest.toml"]["opf"]["spec_version"]
                else:
                    _h1_um["manifest.toml"]["opf"]["spec_version"] = _h1_decl
                _h1_ur = _h1_run(_h1_um, working=_p_bundle_files({_p_src: _p_new}))
                _h1_unread.append(
                    _h1_ur is not None and _h1_ur.status == CANNOT_EVALUATE
                    and _h1_ur.checks.get("C-MANIFEST") in ("FINDING", "CANNOT-EVALUATE")
                    and "C-IMPORTED-PROVENANCE" not in _h1_ur.checks
                    and not any("C-IMPORTED-PROVENANCE" in m or "imported series" in m or "unregistered" in m
                                for m in _h1_ur.findings + _h1_ur.cannot_evaluate))
            check("provenance-homes1-unreadable-declaration-cannot-evaluate", _h1_unread == [True, True, True])
        finally:
            _p_g["required_checks"] = _h1_prev_rc
            _p_g["IMPORTED_CONTENT_CHECKS"] = _h1_prev_icc
            _opf_store.SUPPORTED_SPEC_VERSION = _h1_prev_ssv
        # Evidence registration (spec 4.2/8.8). On a homes-1 store that declares the imported series under
        # supporting tooling, the containment walk registers each .working/imported/<kind>/<run-id>/ bundle's
        # inventory.toml, its root inventory-<phase>.toml files and the files they list, in the bundle, under the
        # Move destination .working/archive/moved/, or, for an adoption bundle, under its run's adoption archive
        # .working/archive/adoption/<run-id>/, and everything else under .working/imported/ and .working/archive/
        # stays unregistered. SUPPORTED_SPEC_VERSION is
        # patched to IMPORTED_SPEC_VERSION inside the block (restored in finally), so the 1.3.0 store validates
        # and the containment flag is True; the activation tripwire then holds the store at CANNOT-EVALUATE, and
        # the vectors judge the C-CONTAINMENT findings. The store carries the zero imported counter row of every
        # enabled type (spec 8.2) and each run's prior the empty imported baseline, since the flag also grades the
        # imported series.

        def _r_machine():
            m = clean_machine()
            m["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            m["counters.toml"]["counters"].update({"imported:" + ns: 0 for ns in _i3_ns})
            return m

        def _r_run(working):
            return run(_r_machine(), working=working, obs=_i3_obs())

        def _r_unregistered(r):
            return sorted(f for f in (r.findings if r is not None else ()) if "unregistered path" in f)

        def _r_paths(*paths):
            return sorted("C-CONTAINMENT: unregistered path {!r} at the store location is neither OPF-managed nor "
                          "enumerated as unmanaged (spec 14.2/11)".format(p) for p in paths)

        def _r_only_tripwire(r):
            # Everything registered: no finding, and the activation tripwire is the only CANNOT-EVALUATE.
            return (r is not None and not r.findings and len(r.cannot_evaluate) == 1
                    and _i3_msgs(r, "C-CONTAINMENT") == r.cannot_evaluate and "C-IMPORTED" in r.cannot_evaluate[0])

        _r_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The listed original and the inventory register: no finding, and the tripwire is the only
            # CANNOT-EVALUATE (red when the bundle is not registered: .working/imported is then a stray).
            _r_ok = _r_run(_p_bundle_files())
            check("import-bundle-listed-file-registered",
                  _r_ok is not None and not _r_ok.findings and len(_r_ok.cannot_evaluate) == 1
                  and _i3_msgs(_r_ok, "C-CONTAINMENT") == _r_ok.cannot_evaluate
                  and "C-IMPORTED" in _r_ok.cannot_evaluate[0])
            # Only what the inventories list registers: an unlisted original, a loose bundle file, an unlisted
            # directory, and an inventory-shaped file outside the phase grammar (an upper-case phase) are each an
            # unregistered path (red when the bundle registers wholesale, or any inventory-* name as a phase).
            _r_un_w = _p_bundle_files()
            for _r_extra in ("/originals/notes/extra.md", "/notes.txt", "/scratch/a.txt"):
                _r_un_w[_p_w(_p_bundle + _r_extra)] = "x\n"
            _r_un_w[_p_w(_p_bundle + "/inventory-Later.toml")] = {"format": EVIDENCE_FORMAT, "file": []}
            _r_un = _r_run(_r_un_w)
            check("import-bundle-unlisted-file-finding",
                  _r_un is not None and _r_unregistered(_r_un) == _r_paths(
                      _p_bundle + "/originals/notes/extra.md", _p_bundle + "/notes.txt", _p_bundle + "/scratch",
                      _p_bundle + "/inventory-Later.toml"))
            # A phase inventory (spec 4.2) registers as the base inventory does: inventory-promote.toml and the
            # promotion receipt it lists register, so the store has no finding and the tripwire is its only
            # CANNOT-EVALUATE (red when a phase inventory and its listed files stay unregistered).
            _r_receipt = _p_bundle + "/receipt.toml"
            _r_phase_inv = _p_bundle + "/inventory-promote.toml"

            def _r_phase_files(phase_doc, receipt=True):
                out = _p_bundle_files()
                if receipt:
                    out[_p_w(_r_receipt)] = "schema = 1\n"
                out[_p_w(_r_phase_inv)] = phase_doc
                return out

            def _r_phase_doc(*rows):
                return {"format": EVIDENCE_FORMAT, "file": [
                    {"path": p, "size": len(t.encode("utf-8")), "sha256": _p_sha(t)} for p, t in rows]}

            _r_ph = _r_run(_r_phase_files(_r_phase_doc((_r_receipt, "schema = 1\n"))))
            check("import-bundle-phase-inventory-registered",
                  _r_ph is not None and not _r_ph.findings and len(_r_ph.cannot_evaluate) == 1
                  and _i3_msgs(_r_ph, "C-CONTAINMENT") == _r_ph.cannot_evaluate
                  and "C-IMPORTED" in _r_ph.cannot_evaluate[0])
            # A malformed phase inventory leaves the bundle's claims unknown: a C-CONTAINMENT CANNOT-EVALUATE
            # naming it, and the bundle is not graded on a guess, so the receipt is no finding (red when the
            # phase inventory is skipped, which leaves it and the receipt unregistered).
            _r_phb = _r_run(_r_phase_files(
                {"format": EVIDENCE_FORMAT, "file": [{"path": _r_receipt}]}))
            check("import-bundle-malformed-phase-inventory-cannot-evaluate",
                  _r_phb is not None and not _r_phb.findings and len(_r_phb.cannot_evaluate) == 2
                  and all(m in _i3_msgs(_r_phb, "C-CONTAINMENT") for m in _r_phb.cannot_evaluate)
                  and "C-CONTAINMENT: malformed import bundle inventory {!r}: file rows require exactly path, "
                      "size and sha256".format(_r_phase_inv) in _r_phb.cannot_evaluate)
            # A bundle's inventories share one claim map: a phase inventory claiming the original inventory.toml
            # already lists claims it twice, a CANNOT-EVALUATE naming the phase inventory (red when the
            # claimed-twice refusal is removed, which registers the bundle and passes it).
            _r_phd = _r_run(_r_phase_files(_r_phase_doc((_p_src, _p_text)), receipt=False))
            check("import-bundle-phase-inventory-claim-twice-cannot-evaluate",
                  _r_phd is not None and not _r_phd.findings and len(_r_phd.cannot_evaluate) == 2
                  and "C-CONTAINMENT: malformed import bundle inventory {!r}: {!r} is claimed more than "
                      "once".format(_r_phase_inv, _p_src) in _i3_msgs(_r_phd, "C-CONTAINMENT"))
            # A bundle with no inventory registers nothing: the bundle is one unregistered entry (red when an
            # absent inventory registers the bundle folder).
            _r_ni_w = _p_bundle_files()
            del _r_ni_w[_p_w(_p_inv)]
            _r_ni = _r_run(_r_ni_w)
            check("import-bundle-without-inventory-unregistered",
                  _r_ni is not None and _r_unregistered(_r_ni) == _r_paths(_p_bundle))
            # A row may name a Move destination outside the bundle (spec 4.2), and the file registers there, with
            # its directories: no finding, and the tripwire is the only CANNOT-EVALUATE (red when a row outside the
            # bundle registers nothing, which leaves .working/archive an unregistered path).
            _r_moved = ".working/archive/moved/notes/x.md"
            _r_mv = _r_run(_p_bundle_files({_p_src: _p_text, _r_moved: "x\n"}))
            check("evidence-bundle-move-destination-registered", _r_only_tripwire(_r_mv))
            # An unreadable or malformed inventory leaves the bundle's claims unknown: a C-CONTAINMENT
            # CANNOT-EVALUATE naming it, and the bundle is not graded on a guess (red when it is walked anyway).
            _r_bad = []
            for _r_doc in ("file = [ not toml", {"format": EVIDENCE_FORMAT, "file": [{"path": _p_src}]}):
                _r_bad_w = _p_bundle_files()
                _r_bad_w[_p_w(_p_inv)] = _r_doc
                _r_badr = _r_run(_r_bad_w)
                _r_bad.append(_r_badr is not None and not _r_badr.findings
                              and len(_r_badr.cannot_evaluate) == 2
                              and all(m in _i3_msgs(_r_badr, "C-CONTAINMENT") for m in _r_badr.cannot_evaluate)
                              and any(_p_inv in m for m in _r_badr.cannot_evaluate))
            check("import-bundle-malformed-inventory-cannot-evaluate", _r_bad == [True, True])
            # Every evidence kind's bundle registers from its own inventory as an import bundle does (spec 4.2): a
            # listed receipt and a listed payload file in an ingest, adoption, layout or preview bundle are no
            # finding (red, per kind, when that kind's home is not registered).
            _r_suffix = "-20260601T000000Z-0123456789abcdef"
            _r_runs = {"ingest": "imp" + _r_suffix, "adoption": "adopt" + _r_suffix, "layout": "layout" + _r_suffix,
                       "preview": "preview" + _r_suffix}

            def _r_kind_files(kind, run_id, files):
                # One `kind` bundle whose inventory.toml lists exactly `files`, a path -> text map.
                out = {_p_w(p): t for p, t in files.items()}
                out[_p_w(_opf_store.evidence_inventory(kind, run_id))] = _r_phase_doc(*sorted(files.items()))
                return out

            for _r_kind in ("ingest", "adoption", "layout", "preview"):
                _r_kb = _opf_store.evidence_run(_r_kind, _r_runs[_r_kind])
                _r_kr = _r_run(_r_kind_files(_r_kind, _r_runs[_r_kind], {
                    _r_kb + "/receipt.toml": "schema = 1\n", _r_kb + "/payload/notes.md": "n\n"}))
                check("evidence-bundle-registered-" + _r_kind, _r_only_tripwire(_r_kr))
            # An adoption bundle registers what it lists under its own run's adoption archive (spec 4.2), so a
            # retire preimage there is no finding (red when the adoption archive is not registered).
            _r_adopt = _r_runs["adoption"]
            _r_arch = _opf_store.retire_preimage(_r_adopt, "legacy/OLD.md")
            _r_aa = _r_run(_r_kind_files("adoption", _r_adopt, {
                _r_arch: "old\n", _opf_store.evidence_run("adoption", _r_adopt) + "/receipt.toml": "schema = 1\n"}))
            check("adoption-archive-registered", _r_only_tripwire(_r_aa))
            # Only what the spec registers: an unknown evidence kind, a folder that is not a run of its kind, loose
            # files under .working/imported/, a kind home and .working/archive/, another folder under
            # .working/archive/, an unclaimed file or folder under the Move destination, and an adoption archive
            # run no adoption bundle claims each stay an unregistered path (red when a whole evidence home is
            # admitted as a managed namespace).
            _r_st_w = _p_bundle_files()
            for _r_stray in ("imported/unknown/x.md", "imported/import/not-a-run/x.md",
                             "imported/ingest/adopt" + _r_suffix + "/x.md", "imported/loose.md",
                             "imported/import/loose.md", "archive/loose.md", "archive/other/x.md",
                             "archive/moved/unclaimed.md", "archive/moved/sub/x.md",
                             "archive/adoption/" + _r_adopt + "/x.md"):
                _r_st_w[_r_stray] = "x\n"
            _r_st = _r_run(_r_st_w)
            check("evidence-home-strays-graded",
                  _r_st is not None and _r_unregistered(_r_st) == _r_paths(
                      ".working/imported/unknown", ".working/imported/import/not-a-run",
                      ".working/imported/ingest/adopt" + _r_suffix, ".working/imported/loose.md",
                      ".working/imported/import/loose.md", ".working/archive/loose.md", ".working/archive/other",
                      ".working/archive/moved/unclaimed.md", ".working/archive/moved/sub",
                      ".working/archive/adoption/" + _r_adopt))
            # A bundle with a phase inventory but no inventory.toml cannot evaluate (spec 4.2: a phase inventory
            # never stands in for the first): a C-CONTAINMENT CANNOT-EVALUATE naming the bundle, and no
            # unregistered-path finding (red when such a bundle is graded as one unregistered entry).
            _r_po_w = _r_phase_files(_r_phase_doc((_r_receipt, "schema = 1\n")))
            del _r_po_w[_p_w(_p_inv)]
            _r_po = _r_run(_r_po_w)
            check("evidence-bundle-phase-inventory-only-cannot-evaluate",
                  _r_po is not None and not _r_po.findings and len(_r_po.cannot_evaluate) == 2
                  and "C-CONTAINMENT: evidence bundle {!r} has a phase inventory but no inventory.toml, so its "
                      "claims cannot be read (spec 4.2)".format(_p_bundle) in _i3_msgs(_r_po, "C-CONTAINMENT"))
            # A listing that fails during registration leaves claims unknown even when a later listing would
            # succeed, so only the first listing of `target` fails here (_list_contained patched, restored in
            # finally). A bundle whose own listing fails after its kind home was listed has unknown phase
            # inventories: the registration records a C-CONTAINMENT CANNOT-EVALUATE naming it and leaves it
            # unwalked (red when the failed listing registers the bundle from inventory.toml alone: its phase
            # inventory and receipt are then unregistered findings, and no CANNOT-EVALUATE names the failure).
            _r_prev_lc = _list_contained

            def _r_first_listing_fails(target, working):
                hits = []

                def listing(root_fd, reldir):
                    if reldir == target and not hits:
                        hits.append(reldir)
                        raise StoreError("simulated listing failure")
                    return _r_prev_lc(root_fd, reldir)

                try:
                    _p_g["_list_contained"] = listing
                    r = _r_run(working)
                finally:
                    _p_g["_list_contained"] = _r_prev_lc
                return (r is not None and hits == [target] and not r.findings and len(r.cannot_evaluate) == 2
                        and "C-CONTAINMENT: cannot list evidence {} {!r}: 'simulated listing failure'".format(
                            "bundle" if target == _p_bundle else "home", target) in _i3_msgs(r, "C-CONTAINMENT"))

            check("evidence-bundle-listing-failure-cannot-evaluate", _r_first_listing_fails(
                _p_bundle, _r_phase_files(_r_phase_doc((_r_receipt, "schema = 1\n")))))
            # A kind home whose listing fails leaves its bundles' claims unknown the same way: a C-CONTAINMENT
            # CANNOT-EVALUATE naming it, and the home is not walked (red when the failed listing registers nothing
            # silently: the bundle is then one unregistered entry, and no CANNOT-EVALUATE names the failure).
            check("evidence-home-listing-failure-cannot-evaluate", _r_first_listing_fails(
                _IMPORT_EVIDENCE_HOME, _p_bundle_files()))
            # A failed listing also leaves ungraded every directory the unlisted bundles may claim into. Each vector
            # puts a claimed file in each such directory, so leaving one out grades that file as unregistered: the
            # Move destination after a kind-home failure (red when the kind home's reach omits it), the adoption
            # archive and the Move destination after the adoption kind-home failure (red when that reach omits
            # either), and both after a failure listing .working/imported itself (red when its reach omits either).
            check("evidence-home-listing-failure-leaves-move-destination-ungraded", _r_first_listing_fails(
                _IMPORT_EVIDENCE_HOME, _p_bundle_files({_p_src: _p_text, _r_moved: "x\n"})))
            _r_adopt_files = _r_kind_files("adoption", _r_adopt, {
                _r_arch: "old\n", ".working/archive/moved/adopted/OLD.md": "moved\n"})
            check("adoption-home-listing-failure-leaves-archive-and-move-destination-ungraded", _r_first_listing_fails(
                _rel(_opf_store.IMPORTED_REL, "adoption"), _r_adopt_files))
            _r_root_w = _p_bundle_files({_p_src: _p_text, _r_moved: "x\n"})
            _r_root_w.update(_r_adopt_files)
            check("imported-root-listing-failure-leaves-evidence-reach-ungraded", _r_first_listing_fails(
                _opf_store.IMPORTED_REL, _r_root_w))
            # A phase inventory that vanishes between the bundle listing and its read leaves the bundle's claims
            # unknown: a C-CONTAINMENT CANNOT-EVALUATE naming it, and the bundle, whose receipt only that inventory
            # listed, is not graded (red when the vanished inventory is passed over: the bundle then registers from
            # inventory.toml alone, its receipt is an unregistered finding, and no CANNOT-EVALUATE names the loss).
            # The bundle's first listing removes the inventory once it is listed (_list_contained patched, restored
            # in finally).
            _r_vanished = []

            def _r_vanishing(root_fd, reldir):
                listing = _r_prev_lc(root_fd, reldir)
                if reldir == _p_bundle and not _r_vanished:
                    _r_vanished.append(reldir)
                    os.unlink(_r_phase_inv, dir_fd=root_fd)
                return listing

            try:
                _p_g["_list_contained"] = _r_vanishing
                _r_va = _r_run(_r_phase_files(_r_phase_doc((_r_receipt, "schema = 1\n"))))
            finally:
                _p_g["_list_contained"] = _r_prev_lc
            check("evidence-bundle-phase-inventory-vanished-cannot-evaluate",
                  _r_va is not None and _r_vanished == [_p_bundle] and not _r_va.findings
                  and len(_r_va.cannot_evaluate) == 2
                  and "C-CONTAINMENT: listed inventory {!r} vanished".format(_r_phase_inv)
                  in _i3_msgs(_r_va, "C-CONTAINMENT"))
            # A legacy-format ingest inventory (opf.ingest.evidence-inventory/v1) on homes 1 is refused without
            # migration or rewrite: a C-CONTAINMENT CANNOT-EVALUATE naming it, and neither its bundle nor the Move
            # destination it claims into is graded (red when the legacy inventory is read as absent: the bundle and
            # the moved file are then unregistered findings).
            _r_lg_run = _r_runs["ingest"]
            _r_lg_inv = _opf_store.evidence_inventory("ingest", _r_lg_run)
            _r_lg_rows = {_opf_store.evidence_run("ingest", _r_lg_run) + "/payload/notes.md": "n\n", _r_moved: "x\n"}
            _r_lg_w = {_p_w(p): t for p, t in _r_lg_rows.items()}
            _r_lg_w[_p_w(_r_lg_inv)] = dict(_r_phase_doc(*sorted(_r_lg_rows.items())),
                                             format="opf.ingest.evidence-inventory/v1")
            _r_lg = _r_run(_r_lg_w)
            check("evidence-bundle-legacy-ingest-inventory-cannot-evaluate",
                  _r_lg is not None and not _r_lg.findings and len(_r_lg.cannot_evaluate) == 2
                  and "C-CONTAINMENT: malformed ingest bundle inventory {!r}: legacy-ingest-inventory: old-format "
                      "ingest inventory is unsupported; refused without migration or rewrite".format(_r_lg_inv)
                  in _i3_msgs(_r_lg, "C-CONTAINMENT"))
            # A path that two bundles claim is claimed twice (spec 4.2): a C-CONTAINMENT CANNOT-EVALUATE naming
            # both bundles, and neither the later bundle nor the Move destination is graded (red when each bundle
            # keeps its own claim map: both then register, and only the tripwire is left).
            _r_ing_b = _opf_store.evidence_run("ingest", _r_runs["ingest"])
            _r_cc_w = _p_bundle_files({_p_src: _p_text, _r_moved: "x\n"})
            _r_cc_w.update(_r_kind_files("ingest", _r_runs["ingest"], {_r_moved: "x\n"}))
            _r_cc = _r_run(_r_cc_w)
            check("evidence-bundle-cross-claim-twice-cannot-evaluate",
                  _r_cc is not None and not _r_cc.findings and len(_r_cc.cannot_evaluate) == 2
                  and "C-CONTAINMENT: evidence bundle {!r} claims {!r}, which {!r} already claims; a path claimed "
                      "twice cannot evaluate (spec 4.2)".format(_r_ing_b, _r_moved, _p_bundle)
                  in _i3_msgs(_r_cc, "C-CONTAINMENT"))
            # A bundle whose claims are unknown leaves ungraded everywhere it may claim into: a malformed adoption
            # inventory leaves its bundle, its run's adoption archive and the Move destination unwalked, so a
            # preimage and a moved file there are no finding (red when only the bundle itself is left ungraded:
            # those files are then graded as unregistered on a guess).
            _r_ur = _r_run({
                _p_w(_r_arch): "old\n", _p_w(_r_moved): "x\n",
                _p_w(_opf_store.evidence_inventory("adoption", _r_adopt)): {"format": EVIDENCE_FORMAT,
                                                                              "file": [{"path": _r_arch}]}})
            check("evidence-bundle-unknown-claims-leave-their-reach-ungraded",
                  _r_ur is not None and not _r_ur.findings and len(_r_ur.cannot_evaluate) == 2
                  and "C-CONTAINMENT: malformed adoption bundle inventory {!r}: file rows require exactly path, size "
                      "and sha256".format(_opf_store.evidence_inventory("adoption", _r_adopt))
                  in _i3_msgs(_r_ur, "C-CONTAINMENT"))
            # A symlinked import home: the registration's listing of .working/imported meets it and names it as a
            # C-CONTAINMENT CANNOT-EVALUATE, and the failure does not escape the check or fault the run (red when
            # it escapes). That the link is never followed rests on _list_contained's no-follow open, which this
            # vector does not discriminate: a registration that followed it would meet the same CANNOT-EVALUATE.

            def _r_symlink_home(root):
                home = root / _IMPORT_EVIDENCE_HOME
                home.rename(root / "elsewhere")
                os.symlink(str(root / "elsewhere"), str(home))

            _r_sl = _p_doctor(_r_machine(), _p_bundle_files(), after=_r_symlink_home)
            check("import-home-symlink-listing-failure-does-not-escape",
                  _r_sl is not None and _r_sl.checks.get("C-CONTAINMENT") == "CANNOT-EVALUATE"
                  and not _r_sl.findings
                  and not any(m.startswith("internal:") for m in _r_sl.cannot_evaluate)
                  and any(".working/imported" in m and "exotic" in m for m in _i3_msgs(_r_sl, "C-CONTAINMENT")))
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _r_prev_ssv
        # Inactive (the shipped constants), the evidence homes register nothing: .working/imported stays one
        # unregistered entry exactly as before (red when the registration ignores the flag).
        _r_off = run(clean_machine(), working=_p_bundle_files())
        check("import-bundle-registration-inert-when-inactive",
              _r_off is not None and _r_off.status == INVALID
              and _r_unregistered(_r_off) == _r_paths(".working/imported"))
        # The import home is a graded container on homes 1 once the store declares the imported series under
        # supporting tooling (the `imported` flag classify_containment receives): an [unmanaged] declaration
        # inside it or containing it collides, and the same declarations stay valid while inactive (red when
        # the home is not added to the graded containers).
        _r_um = base_manifest()
        _r_um["unmanaged"] = dict(paths=[_p_bundle + "/originals", ".working/imported"])
        _r_um_on = classify_containment(_r_um, ".working/toml", imported=True)
        _r_um_off = classify_containment(_r_um, ".working/toml")
        check("import-home-unmanaged-declaration-collides-when-active",
              len(_r_um_on.colliding) == 2 and _r_um_on.valid_unmanaged == []
              and _IMPORT_EVIDENCE_HOME in _r_um_on.evidence_homes
              and _r_um_off.colliding == [] and len(_r_um_off.valid_unmanaged) == 2
              and _r_um_off.evidence_homes == ())
        # Every other evidence home spec 4.2 registers on homes 1 is a graded container under the same flag: an
        # [unmanaged] declaration inside another kind home, naming one, inside the Move destination or an
        # adoption archive run, or containing .working/archive collides, and the same declarations stay valid
        # while inactive (red when those homes are not added to the graded containers).
        _r_eh_paths = [".working/imported/ingest/x", ".working/imported/adoption", ".working/archive/moved/sub",
                       ".working/archive/adoption/adopt-20260601T000000Z-0123456789abcdef", ".working/archive"]
        _r_eh = base_manifest()
        _r_eh["unmanaged"] = dict(paths=list(_r_eh_paths))
        _r_eh_on = classify_containment(_r_eh, ".working/toml", imported=True)
        _r_eh_off = classify_containment(_r_eh, ".working/toml")
        check("evidence-homes-unmanaged-declaration-collides-when-active",
              len(_r_eh_on.colliding) == len(_r_eh_paths) and _r_eh_on.valid_unmanaged == []
              and _r_eh_off.colliding == [] and _r_eh_off.valid_unmanaged == _r_eh_paths)
        # The write guard refuses those declarations too, through the flag it derives itself
        # (imported_series_declared): on a homes-1 store that declares the imported series under supporting
        # tooling, plan_write_scope refuses an [unmanaged] declaration inside another kind home, inside the Move
        # destination, or naming an adoption archive run, each with exactly its collision, and plans the same
        # manifest at the shipped support constant (red when the guard omits classification.colliding, or
        # classifies without the flag).
        import _opf_write_guard
        _r_wg_paths = (".working/imported/ingest/x", ".working/archive/moved/sub",
                       ".working/archive/adoption/adopt-20260601T000000Z-0123456789abcdef")

        def _r_wg_plan(path):
            m = base_manifest()
            m["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            m["unmanaged"] = dict(paths=[path])
            try:
                return _opf_write_guard.plan_write_scope(".working/toml", m, (".working/toml/manifest.toml",),
                                                         "upgrade")
            except _opf_write_guard.WriteGuardError as exc:
                return str(exc)

        _r_wg_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            _r_wg_on = [_r_wg_plan(p) for p in _r_wg_paths]
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _r_wg_prev_ssv
        _r_wg_off = [_r_wg_plan(p) for p in _r_wg_paths]
        check("evidence-homes-unmanaged-declaration-refused-by-write-guard",
              _r_wg_on == ["cannot plan upgrade writes: C-CONTAINMENT: unmanaged path {!r} collides with a managed "
                           "store path (an unmanaged declaration cannot name or contain a managed file; spec "
                           "14.2)".format(p) for p in _r_wg_paths]
              and all(isinstance(s, dict) and ".working/toml/manifest.toml" in s["store"] for s in _r_wg_off))
        # The registration is the homes-1 one: generation 1 registers every evidence home, and generation 2, whose
        # evidence roots C-EVIDENCE-ENUM reconciles, registers none (red when the generation bound admits 2).
        _r_h2 = base_manifest()
        _r_h2["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
        _r_h2["opf"]["homes"] = 2
        _r_prev_homes = _opf_store.SUPPORTED_HOMES
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _r_h2_cls = classify_containment(_r_h2, ".working/toml", imported=True)
        finally:
            _opf_store.SUPPORTED_HOMES = _r_prev_homes
        check("import-home-registered-on-homes1-only",
              _r_h2_cls.homes == 2 and _r_h2_cls.evidence_homes == ()
              and classify_containment(base_manifest(), ".working/toml", imported=True).evidence_homes
              == _evidence_homes())
        # F2 (per-record): worklog/<WL-n>.toml is never a per-record body -> INVALID; a garbage body control
        # is still caught.
        wlr = copy.deepcopy(pr_machine)
        wlr["worklog/WL-9.toml"] = "schema = 1"
        check("worklog-perrecord-body-invalid",
              run(wlr, product=pr_product, obs=pr_obs).status == INVALID)
        gbc = copy.deepcopy(pr_machine)
        gbc["finding/garbage.toml"] = "schema = 1"
        check("perrecord-garbage-body-invalid",
              run(gbc, product=pr_product, obs=pr_obs).status == INVALID)
        # F3: the imports/<run-id> interior is graded at steady state (import_status none): stray bytes
        # under a run dir and a leftover run each -> INVALID.
        RUNID0 = "imp-20260601T000000Z-0123456789abcdef"
        imp1 = clean_machine()
        check("imports-stray-bytes-none-invalid",
              run(imp1, working={"imports/{}/candidate/evil.bin".format(RUNID0): "x"}).status == INVALID)
        imp2 = clean_machine()
        check("imports-leftover-run-none-invalid",
              run(imp2, working={"imports/{}/plan.toml".format(RUNID0): "schema = 1"}).status == INVALID)
        # OPF-IMPORTS-RELOCATE, after the import engine's removal: a run at the OLD machine-subdir path
        # `.working/toml/imports/` (a `machine`-dict fixture) is a legacy import staging area. The retired
        # C-STAGING legacy-location guard lives on in C-CONTAINMENT: the store is CANNOT-EVALUATE with a
        # message naming the path for review by hand (the cant DOMINATES), so an old staging run is never
        # silently ignored, and the walk still grades the path as unregistered. Flip: dropping the
        # legacy-staging guard in _check_containment leaves only the finding (INVALID).
        LEGRUN = "imp-20260601T000000Z-0123456789abcdef"
        LEGREL = ".working/toml/imports"

        def legacy_named(res):
            return res is not None and any(
                "C-CONTAINMENT" in m and repr(LEGREL) in m and "legacy import staging area" in m
                and "by hand" in m for m in res.by_check.get("C-CONTAINMENT", []) if m in res.cannot_evaluate)

        _lg = clean_machine()
        _lg["imports/{}/plan.toml".format(LEGRUN)] = "schema = 1"
        _lgr = run(_lg)
        check("legacy-imports-check-cannot-eval",
              _lgr is not None and _lgr.status == CANNOT_EVALUATE and legacy_named(_lgr))
        check("legacy-imports-check-named",
              _lgr is not None and any("C-CONTAINMENT" in f and "toml/imports" in f and "unregistered" in f
                                       for f in _lgr.findings)
              and "C-STAGING" not in _lgr.checks)
        # declaring the OLD path under [unmanaged] does NOT launder it (restored from the retired C-STAGING
        # vector): the guard is independent of the unmanaged-path machinery, so the store stays
        # CANNOT-EVALUATE. Flip: without the guard the declaration covers the run and the store is VALID.
        _lgu = clean_machine()
        _lgu["manifest.toml"] = base_manifest()
        _lgu["manifest.toml"]["unmanaged"] = dict(paths=[LEGREL])
        _lgu["imports/{}/plan.toml".format(LEGRUN)] = "schema = 1"
        _lgur = run(_lgu)
        check("legacy-imports-unmanaged-not-launderable",
              _lgur is not None and _lgur.status == CANNOT_EVALUATE and legacy_named(_lgur))
        # the declaration alone, of the OLD path or of a path under it, is CANNOT-EVALUATE too, with nothing
        # staged there. Flip: without the guard both validate VALID (the bare declaration covers nothing).
        _lgd = []
        for decl in (LEGREL, LEGREL + "/" + LEGRUN):
            _d = clean_machine()
            _d["manifest.toml"] = base_manifest()
            _d["manifest.toml"]["unmanaged"] = dict(paths=[decl])
            _lgd.append((decl, run(_d)))
        check("legacy-imports-unmanaged-declaration-cannot-eval",
              all(r is not None and r.status == CANNOT_EVALUATE and legacy_named(r)
                  and any(repr(decl) in m for m in r.cannot_evaluate) for decl, r in _lgd))
        # Companion: an UNRELATED [unmanaged] declaration still validates VALID, including a machine-dir file
        # whose name merely begins with `imports` and a store-scope legacy file, so the guard matches the
        # path and its subtree only. Flip: a guard that fired on any declaration, or on a bare string prefix,
        # turns this red.
        _lgo = clean_machine()
        _lgo["manifest.toml"] = base_manifest()
        _lgo["manifest.toml"]["unmanaged"] = dict(paths=[LEGREL + "-notes.md", ".working/legacy.md"])
        _lgor = run(_lgo, working=dict([("toml/imports-notes.md", "notes\n"), ("legacy.md", "x\n")]))
        check("legacy-imports-unrelated-unmanaged-valid",
              _lgor is not None and _lgor.status == VALID and _lgor.checks.get("C-CONTAINMENT") == "PASS")
        # an OLD-path-only run does NOT substantiate import_status = "partial": the unsubstantiated-partial
        # finding fires (imports is read at the NEW root, which is empty) AND the legacy cant dominates.
        _lgp = clean_machine()
        _lgp["manifest.toml"] = base_manifest()
        _lgp["manifest.toml"]["opf"]["import_status"] = "partial"
        _lgp["imports/{}/plan.toml".format(LEGRUN)] = "schema = 1"
        _lgpr = run(_lgp)
        check("legacy-imports-partial-not-substantiated",
              _lgpr is not None and _lgpr.status == CANNOT_EVALUATE and legacy_named(_lgpr)
              and any("no active" in f and "partial" in f for f in _lgpr.findings))

        # --- C-LEASE (codex-1): the single-writer lease payload (spec 5.7) --------------------------
        def lease(**over):
            payload = dict(schema=1, holder="run-abc", operation="sync", acquired_at=TS)
            payload.update(over)
            return payload
        lz = clean_machine()
        lz["lease.toml"] = lease()
        check("lease-wellformed-valid", run(lz).status == VALID)
        lz = clean_machine()
        lz["lease.toml"] = "this is not valid toml === ["
        check("lease-corrupt-cannot-eval", run(lz).status == CANNOT_EVALUATE)
        lz = clean_machine()
        lz["lease.toml"] = lease(acquired_at="not-a-timestamp")
        rlz = run(lz)
        check("lease-bad-timestamp-invalid", rlz.status == INVALID)
        check("lease-bad-timestamp-named", any("C-LEASE" in f for f in rlz.findings))
        lz = clean_machine()
        lz["lease.toml"] = lease(extra="x")
        check("lease-unknown-key-invalid", run(lz).status == INVALID)
        lz = clean_machine()
        lz["lease.toml"] = lease(holder="")
        check("lease-empty-holder-invalid", run(lz).status == INVALID)

        # --- cross-record -----------------------------------------------------------------------------
        f = clean_machine()
        f["done.index.toml"] = idx([dn(1, "BI-1"), dn(2, "BI-1")])
        f["counters.toml"] = counters(DN=2)
        check("r1-two-dones-same-bi-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["done.index.toml"] = idx([])
        f["counters.toml"] = counters(DN=0)
        check("r1-ratified-bi-no-done-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["done.index.toml"] = idx([dn(1, "BI-99")])
        check("r1-receipt-target-unresolved-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["handoff.index.toml"] = idx([ho(1), ho(2)])
        f["counters.toml"] = counters(HO=2)
        check("r2-two-current-handoffs-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([pd(1), pd(2, supersedes="PD-1"), pd(3, supersedes="PD-1")])
        f["counters.toml"] = counters(PD=3)
        check("r3-two-current-resolutions-invalid", run(f).status == INVALID)
        f = clean_machine()
        # a sole decided resolution superseded by a WITHDRAWN successor -> zero current (F14)
        f["pending_decision.index.toml"] = idx([pd(1), pd(2, status="withdrawn", supersedes="PD-1")])
        f["counters.toml"] = counters(PD=2)
        check("r3-zero-current-resolution-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([pd(1, supersedes="PD-99")])
        f["counters.toml"] = counters(PD=1)
        check("r4-dangling-supersedes-invalid", run(f).status == INVALID)
        f = clean_machine()
        # a dangling 'follows' now resolves (F13): finding, not silently advisory
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open",
                                                 links=[{"rel": "follows", "id": "FN-99"}])])
        f["counters.toml"] = counters(FN=1)
        check("r4-dangling-follows-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["block.index.toml"] = idx([envelope("BL-1", "block", "active", scopes=["FN-99"])])
        f["counters.toml"] = counters(BL=1)
        check("r4-block-scope-unresolved-invalid", run(f).status == INVALID)
        # F9 (C-LINKS sole layer): a supersedes link whose target is a DIFFERENT type is a finding.
        f = clean_machine()
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open",
                                                 links=[{"rel": "supersedes", "id": "BI-2"}])])
        f["counters.toml"] = counters(FN=1)
        r = run(f)
        check("r4-supersedes-cross-type-invalid", r.status == INVALID)
        check("r4-supersedes-cross-type-named",
              any("supersedes" in x and "same type" in x for x in r.findings))
        # F14 (C-DECISION-CHAINS discrimination): a wholly-undecided supersession chain (all open /
        # withdrawn) is legitimately zero-current -> VALID; removing the `if not decided: continue` guard
        # would flip it INVALID.
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([pd(1, status="open"),
                                                pd(2, status="withdrawn", supersedes="PD-1")])
        f["counters.toml"] = counters(PD=2)
        check("pd-wholly-undecided-chain-valid", run(f).status == VALID)

        # --- id space / counters ----------------------------------------------------------------------
        f = clean_machine()
        f["worklog.toml"] = {"schema": 1, "entry": [wl(2), wl(3), wl(4)]}
        check("r6-duplicate-id-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["counters.toml"] = counters(WL=3)
        check("r6-id-above-high-water-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["counters.toml"] = "this is not valid toml === ["
        check("r6-corrupt-counters-cannot-eval", run(f).status == CANNOT_EVALUATE)
        f = clean_machine()
        del f["finding.index.toml"]
        check("roster-missing-index-invalid", run(f).status == INVALID)

        # --- frozen / partition / no-deletion / contiguity --------------------------------------------
        f = clean_machine()
        edited = wl(1)
        edited["summary"] = "EDITED after freeze"
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [edited, wl(2)]}
        check("f1-edited-frozen-archive-entry-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["counters.toml"] = counters(WL=5)
        check("f2-id-in-neither-location-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(3), wl(4)]}
        check("f3-id-in-both-invalid", run(f).status == INVALID)

        # --- rotation / archive -----------------------------------------------------------------------
        one_ver, one_cl = version_two_release()   # rebuild a consistent 1-release ledger for f4
        one_cl = make_changelog(["1.0.0"])
        one_digs = freeze_digests(one_cl, ["1.0.0"])
        one_version = {"schema": 1, "release": [rel_row("1.0.0", 1, 2, dig12)],
                       "summary": [{"covers": "1.0.0", "status": "published", "digest": one_digs["1.0.0"]},
                                   {"covers": "unreleased", "status": "working"}]}
        f = clean_machine()
        f["version.toml"] = one_version
        f["worklog.toml"] = {"schema": 1, "entry": [wl(4)]}
        f["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
                                          "worklog_moved": [{"span": ["WL-1", "WL-3"],
                                                             "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(2), wl(3)]}
        r = run(f, product={"VERSION": "1.0.0\n", "CHANGELOG.md": one_cl})
        check("f4-tail-id-rotated-invalid", r.status == INVALID)
        check("f4-tail-id-rotated-named", any("C-ROTATION" in x for x in r.findings))
        f = clean_machine()
        f["handoff.index.toml"] = idx([])
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "HO-1", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/handoff.index.toml"] = idx([ho(1)])
        check("f4-non-terminal-rotated-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["archive/2026/reference.index.toml"] = idx([ref(9)])
        f["counters.toml"] = counters(RF=9)
        check("f5-archived-record-not-enumerated-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "reference", "destination": "archive/2026/reference.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/reference.index.toml"] = idx([])
        check("f5-enumerated-id-absent-from-dest-invalid", run(f).status == INVALID)
        f = clean_machine()
        # F12: a moved row whose declared type disagrees with its id namespace
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/handoff.index.toml"] = idx([])
        f["counters.toml"] = counters(RF=9)
        check("f12-moved-type-mismatch-invalid", run(f).status == INVALID)
        f = clean_machine()
        f["archive/2027/worklog.toml"] = {"schema": 1, "entry": []}
        check("f5-bucket-without-archive-toml-cannot-eval", run(f).status == CANNOT_EVALUATE)
        wm = clean_machine()
        wm["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
                                           "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                              "destination": "../../outside.toml"}]}
        check("f9-worklog-dest-escape-invalid", run(wm).status == INVALID)

        # --- across-time (prior committed snapshot) ---------------------------------------------------
        pobs = copy.deepcopy(clean_prior())
        pobs["prior"]["releases"][0]["version"] = "0.9.0"   # a rewritten historical release row
        check("history-rewrite-invalid", run(clean_machine(), obs=pobs).status == INVALID)
        pobs = copy.deepcopy(clean_prior())
        pobs["prior"]["counters_high"]["WL"] = 9            # prior WL 9 -> current 4 is a regression
        check("history-counter-regress-invalid", run(clean_machine(), obs=pobs).status == INVALID)
        pobs = copy.deepcopy(clean_prior())
        pobs["prior"]["records"]["FN-7"] = ("finding", "fixed")   # prior terminal, now absent -> vanished id
        check("history-vanished-id-invalid", run(clean_machine(), obs=pobs).status == INVALID)
        # a prior finding at unqualified 'fixed' now 'open' in the store -> resurrection
        f = clean_machine()
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open")])
        f["counters.toml"] = counters(FN=1)
        pobs = copy.deepcopy(clean_prior())
        pobs["prior"]["records"]["FN-1"] = ("finding", "fixed")
        pobs["prior"]["counters_high"]["FN"] = 1
        r = run(f, obs=pobs)
        check("history-resurrection-invalid", r.status == INVALID)
        check("history-resurrection-named", any("C-HISTORY-RESURRECTION" in x for x in r.findings))

        # --- range-bounding: a huge declared high-water and span must NOT expand (F15). The engine must
        # terminate promptly (the overhang finding carries a COUNT, not a list), so the two vectors run
        # behind a bounded child (S7-F1): a range-expansion REGRESSION would otherwise hang or OOM the
        # suite in-process; here it trips a deterministic TIMEOUT/OOM sentinel the INVALID assertion
        # catches. Requires os.fork; a fork-less host fails the setup check closed. ----------------------
        if not hasattr(os, "fork"):
            check("f15-bounded-runner-available", False)   # fail-closed: the range vectors need a bounded child
        else:
            f = clean_machine()
            f["counters.toml"] = counters(WL=10 ** 9)
            big_obs = copy.deepcopy(clean_prior())
            big_obs["prior"]["counters_high"]["WL"] = 10 ** 9
            _hw_root = build(f)
            _hw_status = run_bounded(
                lambda: validate_store(resolve_store(_hw_root), observations=big_obs).status)
            check("f15-huge-high-water-bounded-invalid", _hw_status == INVALID)
            f = clean_machine()
            one_cl2 = make_changelog(["1.0.0"])
            one_digs2 = freeze_digests(one_cl2, ["1.0.0"])
            f["version.toml"] = {"schema": 1, "release": [rel_row("1.0.0", 1, 2, dig12)],
                                 "summary": [{"covers": "1.0.0", "status": "published",
                                              "digest": one_digs2["1.0.0"]},
                                             {"covers": "unreleased", "status": "working"}]}
            f["worklog.toml"] = {"schema": 1, "entry": [wl(3), wl(4)]}
            f["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
                                              "worklog_moved": [{"span": ["WL-1", "WL-1000000000"],
                                                                 "destination": "archive/2026/worklog.toml"}]}
            _sp_root = build(f, product={"VERSION": "1.0.0\n", "CHANGELOG.md": one_cl2})   # span WL-1..WL-1e9
            _sp_status = run_bounded(
                lambda: validate_store(resolve_store(_sp_root), observations=clean_prior()).status)
            check("f15-huge-span-bounded-invalid", _sp_status == INVALID)

            # --- F7 bounded-runner hardening (fail pre-fix, pass post-fix) ----------------------------
            import signal as _sig7
            import time as _t7
            # (a) a SETUP failure (a bound could NOT be installed) must yield a DISTINCT SETUP-ERROR
            # sentinel, NEVER the thunk's normal result run unbounded. Force setitimer to raise in the
            # child (the patched module carries across the fork); a reverted `except: pass` would swallow
            # it and return "F7-NORMAL".
            _real_setitimer = _sig7.setitimer
            _sig7.setitimer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("f7-injected-setup-failure"))
            try:
                _setup = run_bounded(lambda: "F7-NORMAL")
            finally:
                _sig7.setitimer = _real_setitimer
            check("f7-setup-failure-yields-setup-error-not-normal", _setup.startswith("SETUP-ERROR"))
            # Change hostile signal state only inside an outer bounded child.
            # The parent never borrows a caller timer or discards pending SIGALRM.
            def _hostile_child(blocked):
                if blocked:
                    _sig7.pthread_sigmask(_sig7.SIG_BLOCK, {_sig7.SIGALRM})
                else:
                    _sig7.signal(_sig7.SIGALRM, _sig7.SIG_IGN)
                return run_bounded(lambda: (_t7.sleep(3), "F7-SLEPT-THROUGH")[1],
                                   timeout_s=1)

            check("f7-inherited-ignored-sigalrm-still-times-out",
                  run_bounded(lambda: _hostile_child(False)) == "TIMEOUT")
            if hasattr(_sig7, "pthread_sigmask"):
                check("f7-inherited-blocked-sigalrm-still-times-out",
                      run_bounded(lambda: _hostile_child(True)) == "TIMEOUT")

        # --- io fail-closed ---------------------------------------------------------------------------
        f = clean_machine()
        f["version.toml"] = "not valid toml === ["
        check("io-unreadable-version-cannot-eval", run(f).status == CANNOT_EVALUATE)
        f = clean_machine()
        f["archive/2026/worklog.toml"] = "not valid toml === ["
        check("io-unreadable-archive-bucket-cannot-eval", run(f).status == CANNOT_EVALUATE)
        check("malformed-observations-cannot-eval",
              run(clean_machine(), obs="not-a-table").status == CANNOT_EVALUATE)

        # --- registry reconciliation: a check that never ran is routed to CANNOT-EVALUATE (vector 16) --
        rep2 = _Report()
        for cid in REQUIRED_CHECKS:
            if cid == "C-LINKS":
                continue
            rep2.ran(cid)
        res2 = rep2.result()
        check("registry-missing-check-cannot-eval", res2.status == CANNOT_EVALUATE)
        check("registry-missing-check-named", any("C-LINKS" in m for m in res2.cannot_evaluate))

        # --- N1 sole-layer discrimination vectors ----------------------------------------------------
        # C-ROSTER (sole layer): a baseline type missing from the manifest [types] is caught by C-ROSTER
        # alone (validate_manifest only requires at least one registered type).
        rmf = clean_machine()
        rmf["manifest.toml"] = base_manifest(types={t: {"namespace": ns} for t, ns in (
            ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"),
            ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
            ("autonomous_decision", "AD"), ("block", "BL"))})   # 'finding' registration omitted
        rmr = run(rmf)
        check("c-roster-missing-type-registration-invalid", rmr is not None and rmr.status == INVALID)
        check("c-roster-missing-type-registration-named",
              rmr is not None and any("C-ROSTER" in f for f in rmr.findings))

        # C-NO-DELETION (non-worklog namespace, B1): FN-2 allocated (high-water 2) but only FN-1 present in
        # active + archive and not named in the prior snapshot: a deleted non-worklog id.
        b1f = clean_machine()
        b1f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open")])
        b1f["counters.toml"] = counters(FN=2)
        b1r = run(b1f)
        check("b1-nonwl-deleted-id-invalid", b1r is not None and b1r.status == INVALID)
        check("b1-nonwl-deleted-id-named",
              b1r is not None and any("C-NO-DELETION" in f and "FN" in f for f in b1r.findings))

        # C-CONTIGUITY (pure worklog gap, sole layer): WL-1, WL-3 present with high-water 3 and no
        # duplicate, over a zero-release store so no frozen span / coverage / rotation check co-fires.
        cgf = copy.deepcopy(pr_machine)
        cgf["worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(3)]}
        cgf["counters.toml"] = counters(FN=2, BI=0, DN=0, WL=3, HO=0)
        cg_obs = {"tracked": "tracked",
                  "prior": {"releases": [],
                            "counters_high": counters(FN=2, BI=0, DN=0, WL=3, HO=0)["counters"],
                            "records": {"FN-1": ("finding", "open"), "FN-2": ("finding", "open")}}}
        cgr = run(cgf, product=pr_product, obs=cg_obs)
        check("c-contiguity-pure-gap-invalid", cgr is not None and cgr.status == INVALID)
        check("c-contiguity-pure-gap-named",
              cgr is not None and any("C-CONTIGUITY" in f for f in cgr.findings))

        # C-COUNTERS (absent high-water): a known namespace missing its counter is flagged at the check
        # level (the overall status is dominated by the correlated history-counters cant, so discriminate
        # on the per-check verdict).
        ccf = clean_machine()
        ccf["counters.toml"] = {"schema": 1, "counters": {
            "BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0, "BL": 0, "HO": 1}}   # 'RF' omitted
        ccr = run(ccf)
        check("c-counters-missing-namespace-flagged",
              ccr is not None and ccr.checks.get("C-COUNTERS") == "FINDING")

        # C-COUNTERS (LF in an existing store, round-2): an older store may carry an LF
        # (legacy_fragment) counter from its `opf init` while legacy_fragment stays OUT of the manifest [types]
        # (schema-deferred), so LF is not a known/required namespace. It is an ACCEPTED-if-present importer
        # namespace, NOT a "not a known namespace" finding: doctor must pass it. Without the optional-
        # namespaces wiring (C-COUNTERS passing optional_namespaces=IMPORTER_TYPES) this vector flips to a
        # C-COUNTERS FINDING flagging LF, the round-1 fresh-init doctor / render failure this fix closes.
        lfi = clean_machine()
        lfi["counters.toml"] = counters(LF=0)
        lfir = run(lfi)
        check("c-counters-fresh-init-lf-accepted",
              lfir is not None and lfir.checks.get("C-COUNTERS") == "PASS")
        check("c-counters-fresh-init-lf-no-unknown-ns-finding",
              lfir is not None and not any("LF" in f and "not a known namespace" in f
                                           for f in lfir.findings))

        # C-STAGING retired: C-ID-SPACE's uniqueness union is the committed store only. A leftover run under
        # imports/<run-id>/ holding a candidate BI-1 that duplicates the committed BI-1 is graded by
        # C-CONTAINMENT (an unregistered path, INVALID), never as a duplicate id. Flip: restoring the staged
        # enumeration into the union adds the C-ID-SPACE duplicate-id finding.
        stf = clean_machine()
        stfr = run(stf, working={
            "imports/imp-20260601T000000Z-0123456789abcdef/candidate/backlog_item.index.toml":
                idx([bi(1, "open")])})
        check("c-id-space-committed-only-roster",
              stfr is not None and "C-STAGING" not in stfr.checks and "C-STAGING" not in REQUIRED_CHECKS
              and stfr.checks.get("C-ID-SPACE") == "PASS")
        check("c-id-space-leftover-run-contained",
              stfr is not None and stfr.status == INVALID
              and any("C-CONTAINMENT" in f and "imports/imp-20260601T000000Z-0123456789abcdef" in f
                      for f in stfr.findings)
              and not any("C-ID-SPACE" in f for f in stfr.findings))
        # The committed union still catches an active/archive duplicate: HO-1 archived while the active
        # handoff index still holds HO-1 is a named C-ID-SPACE duplicate-id finding.
        idd = clean_machine()
        idd["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "HO-1", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        idd["archive/2026/handoff.index.toml"] = idx([ho(1)])
        iddr = run(idd)
        check("c-id-space-active-archive-duplicate-named",
              iddr is not None and iddr.checks.get("C-ID-SPACE") == "FINDING"
              and any("C-ID-SPACE" in f and "duplicate id 'HO-1'" in f for f in iddr.findings))

        # --- M2: archive destinations confined + verified --------------------------------------------
        m2a = clean_machine()
        m2a["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "BI-1", "type": "backlog_item", "destination": "backlog_item.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        check("m2-moved-dest-outside-bucket-invalid", run(m2a).status == INVALID)
        m2b = clean_machine()
        m2b["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
                                            "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                               "destination": "counters.toml"}]}
        check("m2-worklog-dest-wrong-invalid", run(m2b).status == INVALID)

        # --- B2: an unregistered path anywhere in the archive tree is a containment finding -----------
        b2a = clean_machine()
        b2a["archive/stray.txt"] = "x\n"
        b2ar = run(b2a)
        check("b2-stray-file-under-archive-invalid",
              b2ar.status == INVALID and any("archive tree" in f for f in b2ar.findings))
        b2b = clean_machine()
        b2b["archive/2026/junkdir/smuggled.toml"] = idx([])
        b2br = run(b2b)
        check("b2-subdir-in-bucket-invalid",
              b2br.status == INVALID and any("archive tree" in f for f in b2br.findings))
        # F1: a stray <x>.index.toml whose type is not an enabled roster type no longer escapes grading in
        # an archive bucket (the round-1 fail-open); an empty/junk index becomes an INVALID finding.
        f1a = clean_machine()
        f1a["archive/2026/junk.index.toml"] = idx([])
        f1ar = run(f1a)
        check("f1-stray-index-in-bucket-invalid",
              f1ar is not None and f1ar.status == INVALID
              and any("not an enabled roster type" in f for f in f1ar.findings))

        # --- N3: a stray root VERSION with an empty release ledger ------------------------------------
        n3m = copy.deepcopy(pr_machine)
        n3p = {"CHANGELOG.md": make_changelog([]), "VERSION": "9.9.9\n"}
        n3r = run(n3m, product=n3p, obs=pr_obs)
        check("n3-empty-release-stray-version-invalid", n3r is not None and n3r.status == INVALID)
        check("n3-empty-release-stray-version-named",
              n3r is not None and any("C-VERSION-FILE" in f for f in n3r.findings))

        # --- B3 / F3 / F7 / F8: C-SYNC-AGREE three-pattern model -------------------------------------
        gm = clean_machine()
        gm["manifest.toml"] = base_manifest()
        gm["manifest.toml"]["store"] = {"sync_target": "github:org/repo.git"}
        # DEDICATED: the github: shorthand agrees with the git@ and the https remote forms (F8).
        check("sync-shorthand-match-valid",
              run(gm, obs={"tracked": "tracked", "actual_remote": "git@github.com:org/repo.git",
                           "prior": clean_prior()["prior"]}).status == VALID)
        check("sync-shorthand-https-match-valid",
              run(gm, obs={"tracked": "tracked", "actual_remote": "https://github.com/org/repo",
                           "prior": clean_prior()["prior"]}).status == VALID)
        # F5: GitHub / GitLab resolve the org/repo path case-insensitively, so a case-only difference agrees.
        gmc = clean_machine()
        gmc["manifest.toml"] = base_manifest()
        gmc["manifest.toml"]["store"] = {"sync_target": "github:Org/Repo.git"}
        check("sync-shorthand-case-insensitive-valid",
              run(gmc, obs={"tracked": "tracked", "actual_remote": "git@github.com:org/repo.git",
                            "prior": clean_prior()["prior"]}).status == VALID)
        # codex-4: a scheme-URL remote PORT is part of the endpoint identity, so distinct ports disagree.
        gmp = clean_machine()
        gmp["manifest.toml"] = base_manifest()
        gmp["manifest.toml"]["store"] = {"sync_target": "git:ssh://git@example.com:2222/ops.git"}
        check("sync-port-mismatch-invalid",
              run(gmp, obs={"tracked": "tracked", "actual_remote": "ssh://git@example.com:2223/ops.git",
                            "prior": clean_prior()["prior"]}).status == INVALID)
        check("sync-port-match-valid",
              run(gmp, obs={"tracked": "tracked", "actual_remote": "ssh://git@example.com:2222/ops.git",
                            "prior": clean_prior()["prior"]}).status == VALID)
        # IN-REPO DEFAULT (pointer_source == "default", sync_target == ""): a present actual remote is
        # EXPECTED (the store rides the product repo), so it PASSES -> VALID (F3 over-fire fix).
        check("sync-in-repo-default-remote-valid",
              run(clean_machine(), obs={"tracked": "tracked", "actual_remote": "git@github.com:x/y.git",
                                        "prior": clean_prior()["prior"]}).status == VALID)
        # codex-8: a self-pointer `dir:.` resolves store_root == product_root, so the store is IN-REPO and
        # rides the product remote -> a present remote is EXPECTED -> VALID (NOT relocated local-only).
        def self_pointer_product():
            p = clean_product()
            p[".opf.toml"] = "[store]\ntarget = \"dir:.\"\n"
            return p
        spr = validate_store(
            resolve_store(build(clean_machine(), self_pointer_product())),
            observations={"tracked": "tracked", "actual_remote": "git@github.com:x/y.git",
                          "prior": clean_prior()["prior"]})
        check("sync-self-pointer-in-repo-remote-valid", spr is not None and spr.status == VALID)
        # codex-5: a MALFORMED actual_remote in the IN-REPO topology is an unreadable input -> CANNOT-
        # EVALUATE naming it, never a silent pass.
        cm5 = run(clean_machine(), obs={"tracked": "tracked", "actual_remote": 123,
                                        "prior": clean_prior()["prior"]})
        check("sync-in-repo-malformed-remote-cannot-eval", cm5 is not None and cm5.status == CANNOT_EVALUATE)
        # A GENUINELY relocated store (a dir: pointer to a store root OUTSIDE the product repo, so
        # store_root != product_root): a present remote is an unrecorded push destination -> INVALID; an
        # absent observation cannot verify the no-remote claim -> cant, never a pass (F7).
        def build_relocated(machine, product=None):
            counter[0] += 1
            p_root = base / "prod-{:02d}".format(counter[0])
            s_root = base / "store-{:02d}".format(counter[0])
            (s_root / ".working" / "toml").mkdir(parents=True)
            for rel, doc in machine.items():
                p = s_root / ".working" / "toml" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")
            p_root.mkdir(parents=True)
            (p_root / ".opf.toml").write_text(
                "[store]\ntarget = \"dir:{}\"\n".format(s_root), encoding="utf-8")
            for rel, doc in (product or clean_product()).items():
                p = p_root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")
            return p_root
        rlr = validate_store(
            resolve_store(build_relocated(clean_machine())),
            observations={"tracked": "tracked", "actual_remote": "git@github.com:x/y.git",
                          "prior": clean_prior()["prior"]})
        check("sync-relocated-remote-invalid", rlr is not None and rlr.status == INVALID)
        check("sync-relocated-remote-named",
              rlr is not None and any("relocated local-only" in f for f in rlr.findings))
        rla = validate_store(
            resolve_store(build_relocated(clean_machine())),
            observations={"tracked": "tracked", "prior": clean_prior()["prior"]})
        check("sync-relocated-absent-cannot-eval", rla is not None and rla.status == CANNOT_EVALUATE)
        # round-16 (F-5b / round-15 MINOR): the relocated-local-only COMMITTED-POINTER-REMOTE leg is
        # unreachable via resolve_store (a remote pointer resolves to CANNOT-EVALUATE before validate_store
        # sees it), so exercise it with a hand-set remote Target on an otherwise-resolved relocated store: a
        # relocated store (sync_target="") whose committed pointer names a REMOTE is a C-SYNC-AGREE finding.
        _rp_res = resolve_store(build_relocated(clean_machine()))
        _rp_res.target = classify_target("github:org/repo")
        _rpr = validate_store(_rp_res, observations={"tracked": "tracked", "actual_remote": "",
                                                     "prior": clean_prior()["prior"]})
        check("sync-relocated-remote-pointer-named",
              _rpr is not None and any("committed pointer names a remote" in f for f in _rpr.findings))

        # --- M1: a malformed prior observation is CANNOT-EVALUATE (naming it malformed), not INVALID ---
        mp = copy.deepcopy(clean_prior())
        mp["prior"]["releases"] = [None] + mp["prior"]["releases"]
        mpr = run(clean_machine(), obs=mp)
        check("m1-malformed-prior-releases-cannot-eval", mpr is not None and mpr.status == CANNOT_EVALUATE)
        check("m1-malformed-prior-named",
              mpr is not None and any("malformed" in m for m in mpr.cannot_evaluate))
        mp2 = copy.deepcopy(clean_prior())
        mp2["prior"]["counters_high"]["WL"] = "four"
        check("m1-malformed-prior-counters-cannot-eval",
              run(clean_machine(), obs=mp2).status == CANNOT_EVALUATE)
        # F5: a dict-but-garbage prior release row is a MALFORMED observation (cant naming it), not a
        # graded C-HISTORY-APPEND-ONLY rewrite finding against a clean store.
        gp = copy.deepcopy(clean_prior())
        gp["prior"]["releases"][0] = {"garbage": True}
        gpr = run(clean_machine(), obs=gp)
        check("f5-garbage-prior-release-cannot-eval", gpr is not None and gpr.status == CANNOT_EVALUATE)
        check("f5-garbage-prior-release-named",
              gpr is not None and any("malformed" in m for m in gpr.cannot_evaluate))
        # F6: a prior counters namespace outside the roster is a MALFORMED observation (cant), not a
        # graded C-HISTORY-COUNTERS finding against a clean store.
        bp = copy.deepcopy(clean_prior())
        bp["prior"]["counters_high"]["ZZ"] = 5
        bpr = run(clean_machine(), obs=bp)
        check("f6-bogus-prior-namespace-cannot-eval", bpr is not None and bpr.status == CANNOT_EVALUATE)
        # F5 corollary: a well-formed prior release row that merely DIFFERS from the current ledger still
        # reaches the graded rewrite check (a real history rewrite), not swallowed as malformed.
        rp = copy.deepcopy(clean_prior())
        rp["prior"]["releases"][0]["coverage_digest"] = "sha256:" + "c" * 64
        rpr = run(clean_machine(), obs=rp)
        check("f5-wellformed-prior-rewrite-invalid", rpr is not None and rpr.status == INVALID)

        # --- B5: a '..' moved destination is refused (confinement), never an uncaught JournalError ----
        b5 = clean_machine()
        b5["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "reference",
                       "destination": "archive/2026/../2026/reference.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        b5["archive/2026/reference.index.toml"] = idx([ref(9)])
        b5["counters.toml"] = counters(RF=9)
        b5r = run(b5)   # must RETURN, not crash
        check("b5-dotdot-moved-dest-no-crash",
              b5r is not None and b5r.status in (INVALID, CANNOT_EVALUATE))

        # --- B4: a FIFO planted as a declared read target fails closed to CANNOT-EVALUATE, never an
        # unbounded BLOCK on the reader. The validate_store call runs behind a bounded child (S7-F2): a
        # regression that opened the FIFO for a blocking read would HANG the suite in-process; here it trips
        # a TIMEOUT sentinel the assertion rejects. The setup needs os.mkfifo AND os.fork, so a host missing
        # either fails the setup check closed rather than silently skipping the no-hang guarantee. ---------
        if not (hasattr(os, "mkfifo") and hasattr(os, "fork")):
            check("b4-fifo-setup-available", False)   # fail-closed: cannot assert the no-hang property here
        else:
            fifo_root = build(clean_machine(), clean_product())
            (fifo_root / "CHANGELOG.md").unlink()
            os.mkfifo(str(fifo_root / "CHANGELOG.md"))

            def _fifo_probe():
                fr = validate_store(resolve_store(fifo_root), observations=clean_prior())
                if (fr is not None and fr.status == CANNOT_EVALUATE
                        and any("regular file" in m for m in fr.cannot_evaluate)):
                    return "CANT-REGFILE"
                return "status={}".format(None if fr is None else fr.status)

            check("b4-fifo-target-cannot-eval-not-hang", run_bounded(_fifo_probe) == "CANT-REGFILE")

        # --- S1-F1: an OSError from os.listdir inside _list_contained maps to a fail-closed StoreError
        # (routed by _list_dir to a named CANNOT-EVALUATE), never a raw OSError escaping the walk; pre-fix
        # only the os.stat leg was wrapped. Exercised by forcing os.listdir to raise over a real dir fd. ---
        _s1_dir = base / "s1-listdir"
        (_s1_dir / "sub").mkdir(parents=True)
        _s1_fd = os.open(str(_s1_dir), os.O_RDONLY | os.O_DIRECTORY)
        _s1_orig_listdir = os.listdir

        def _s1_boom_listdir(*_a, **_k):
            raise OSError(5, "EIO (self-test injected)")

        try:
            os.listdir = _s1_boom_listdir
            try:
                _list_contained(_s1_fd, "sub")
                _s1_outcome = "no-raise"
            except StoreError:
                _s1_outcome = "storeerror"
            except OSError:
                _s1_outcome = "oserror"
        finally:
            os.listdir = _s1_orig_listdir
            _close_fd_quietly(_s1_fd)
        check("s1-listdir-oserror-is-storeerror", _s1_outcome == "storeerror")

        # --- S1-F1 (close leg): the _list_contained `finally` closes are guarded, so a teardown close that
        # raises does not escape as a raw OSError; pre-fix the raw os.close there propagated (reaching a
        # CANNOT-EVALUATE only via the far B6 barrier, spuriously downgrading a VALID store). Patch os.close
        # to really-close-then-raise over a real dir walk and confirm _list_contained still returns its
        # listing without raising. ------------------------------------------------------------------------
        _s1c_dir = base / "s1-close"
        (_s1c_dir / "sub" / "child").mkdir(parents=True)
        (_s1c_dir / "sub" / "afile").write_text("x\n", encoding="utf-8")
        _s1c_fd = os.open(str(_s1c_dir), os.O_RDONLY | os.O_DIRECTORY)
        _s1c_real_close = os.close

        def _s1c_boom_close(fd):
            try:
                _s1c_real_close(fd)
            except OSError:
                pass
            raise OSError(9, "EBADF (self-test injected)")

        try:
            os.close = _s1c_boom_close
            try:
                _s1c_sub, _s1c_files = _list_contained(_s1c_fd, "sub")
                _s1c_outcome = "returned"
            except BaseException:                          # noqa: BLE001 (any escape means the guard failed)
                _s1c_sub = _s1c_files = None
                _s1c_outcome = "raised"
        finally:
            os.close = _s1c_real_close
            _s1c_real_close(_s1c_fd)
        check("s1-listcontained-finally-close-guarded",
              _s1c_outcome == "returned" and _s1c_sub == ["child"] and _s1c_files == ["afile"])

        # --- P1 (#378): _close_fd_quietly closes ONCE and never touches the number again. close(2) on
        # Linux releases the number even when the close then reports EINTR or EIO, and another thread may
        # reuse it at once, so the old fstat-then-reclose recovery (round 6) could close that thread's
        # descriptor. The shared _journal harness injects exactly that: a real close, an unrelated
        # descriptor put on the freed number (inline, and from a real second thread), then the error.
        # Green: the reuser still owns its number (REUSE), the helper made exactly one close and no fstat
        # of the number afterwards (PROBE), and the error is swallowed. Each vector is red under RECLOSE
        # (the old body put back) by REUSE alone and under an fstat-only probe by PROBE alone; each V1
        # vector also under an fcntl F_GETFD probe by PROBE alone, where fcntl exists (2 more runs). The
        # harness's own watch check runs first (1 run): every watched call on a released number is
        # recorded. ----
        _q_failures, _q_runs = _journal._st_close_check(globals(), _journal._st_helper_vectors(globals()))
        for _q_failure in _q_failures:
            print("  close vector: {}".format(_q_failure), file=sys.stderr)
        check("p1-close-fd-quietly-single-close",
              not _q_failures and _q_runs == (11 if _journal._st_fcntl() else 9))

        # --- P1 (one close; man 2 close): _close_fd_quietly closes ONCE and never touches the number again.
        # V1, deterministic reuse: the injected close really releases the fd, dup2s an unrelated file onto
        # the freed number, and only then raises EINTR (and, separately, EIO). The helper must swallow it
        # (RULE), make exactly one os.close and no os.fstat on that number afterwards (PROBE), and leave the
        # unrelated descriptor open with its own (st_dev, st_ino) (REUSE). The RECLOSE flip puts the old
        # fstat-then-reclose body back and must turn the vector red by REUSE; the PROBE flip (one close, then
        # an fstat of the released number, no second close; #378's P flip) must turn it red by PROBE alone.
        # This is #377's own lane, kept beside the #378 harness run above. --------------------------------
        _q_dir = base / "q-close"
        _q_dir.mkdir()
        (_q_dir / "other").write_bytes(b"unrelated")
        _q_real_close, _q_real_fstat = os.close, os.fstat

        def _q_reclose(fd):                                # RECLOSE: the pre-P1 confirm-then-reclose body
            try:
                os.close(fd)
                return
            except OSError:
                pass
            try:
                os.fstat(fd)
            except OSError:
                return
            try:
                os.close(fd)
            except OSError:
                pass

        def _q_probe(fd):                                  # PROBE: fstat after the failed close, no reclose
            try:
                os.close(fd)
                return
            except OSError:
                pass
            try:
                os.fstat(fd)
            except OSError:
                pass

        def _q_vector(helper, err):
            """The failed tags of one V1 run of `helper` (empty: green)."""
            fd = os.open(str(_q_dir), os.O_RDONLY | os.O_DIRECTORY)
            other = os.open(str(_q_dir / "other"), os.O_RDONLY)
            want = _q_real_fstat(other)
            seen = {"closes": 0, "probes": 0, "fired": False}

            def close(n):
                if n == fd:
                    seen["closes"] += 1
                    if not seen["fired"]:
                        seen["fired"] = True
                        _q_real_close(n)
                        os.dup2(other, n)                  # an unrelated file takes the freed number
                        raise OSError(err, "self-test injected: released, then the number was reused")
                return _q_real_close(n)

            def fstat(n, *args, **kwargs):
                if n == fd and seen["fired"]:
                    seen["probes"] += 1
                return _q_real_fstat(n, *args, **kwargs)

            failed = []
            os.close, os.fstat = close, fstat
            try:
                helper(fd)
            except OSError:
                failed.append("rule")
            finally:
                os.close, os.fstat = _q_real_close, _q_real_fstat
            if not seen["fired"]:
                failed.append("injection")
            if seen["closes"] != 1 or seen["probes"]:
                failed.append("probe")
            try:
                now = _q_real_fstat(fd)
            except OSError:
                failed.append("reuse")                     # the unrelated descriptor was closed
            else:
                if (now.st_dev, now.st_ino) != (want.st_dev, want.st_ino):
                    failed.append("reuse")                 # another file holds the number: never closed here
                else:
                    _q_real_close(fd)                      # still the unrelated file this vector put there
            _q_real_close(other)
            return failed

        for _q_err, _q_tag in ((4, "eintr"), (5, "eio")):
            check("p1-close-fd-quietly-one-close-no-probe-reuse-" + _q_tag,
                  _q_vector(_close_fd_quietly, _q_err) == [])
            _q_red = _q_vector(_q_reclose, _q_err)
            check("p1-close-fd-quietly-reclose-flip-red-by-reuse-" + _q_tag,
                  "reuse" in _q_red and set(_q_red) <= {"reuse", "probe"})
            check("p1-close-fd-quietly-probe-flip-red-by-probe-alone-" + _q_tag,
                  _q_vector(_q_probe, _q_err) == ["probe"])

        # --- S4-F3: validate_store's `finally` block closes its store / product-root descriptors OUTSIDE
        # the B6 barrier, so a close that raises during teardown (EINTR / EIO / an invalid fd) must be
        # swallowed rather than crash the validator after the verdict is already computed. Exercised by
        # patching os.close to really close then raise for the whole call: post-fix the guarded finally
        # swallows it and validate_store still returns a verdict; pre-fix the raw os.close propagated. Every
        # production close routes through _close_fd_quietly, so the ONLY unguarded raw close pre-fix is the
        # finally under test. -----------------------------------------------------------------------------
        _s4_root = build(clean_machine(), clean_product())
        _s4_res_in = resolve_store(_s4_root)               # resolve BEFORE patching os.close
        _s4_real_close = os.close

        def _s4_boom_close(fd):
            try:
                _s4_real_close(fd)                         # really release the fd (no leak) ...
            except OSError:
                pass
            raise OSError(9, "EBADF (self-test injected)")  # ... then raise, as a hostile teardown close would

        try:
            os.close = _s4_boom_close
            try:
                _s4r = validate_store(_s4_res_in, observations=clean_prior())
                _s4_outcome = "returned" if _s4r is not None else "none"
            except OSError:
                _s4_outcome = "raised"
        finally:
            os.close = _s4_real_close
        check("s4-validate-store-finally-close-guarded", _s4_outcome == "returned")

        # --- B6: a pathologically deep directory chain UNDER A RECURSED namespace (the imports interior)
        # fails closed to CANNOT-EVALUATE, never a RecursionError. A deep chain OUTSIDE a managed namespace
        # is now flagged as an unregistered directory at its first level (round-14), so the ceiling is
        # exercised here via the imports interior, which the walk does descend. -----------
        deep_root = build(clean_machine(), clean_product())
        deep = deep_root / ".working" / "imports" / "imp-20260601T000000Z-0123456789abcdef"
        for _ in range(_CONTAINMENT_MAX_DEPTH + 16):
            deep = deep / "d"
        deep.mkdir(parents=True)
        dr = validate_store(resolve_store(deep_root), observations=clean_prior())
        check("b6-deep-nesting-cannot-eval-not-crash",
              dr is not None and dr.status == CANNOT_EVALUATE
              and any("ceiling" in m for m in dr.cannot_evaluate))

        # --- F4: a module-tier record is a named CANNOT-EVALUATE deferral (deferred to U2M), never a
        #         false INVALID from the baseline-only record validator -----------------------------
        mm_types = {t: {"namespace": ns} for t, ns in (
            ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
            ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
            ("autonomous_decision", "AD"), ("block", "BL"),
            ("artifact", "AR"), ("gate_run", "GR"), ("release", "RL"), ("waiver", "WV"))}
        mm = clean_machine()
        mm["manifest.toml"] = base_manifest(types=mm_types)
        mm["manifest.toml"]["modules"] = {"delivery_assurance": True}
        mm["counters.toml"] = counters(AR=1, GR=0, RL=0, WV=0)
        mm["artifact.index.toml"] = idx([envelope("AR-1", "artifact", "recorded")])
        mm["gate_run.index.toml"] = idx([])
        mm["release.index.toml"] = idx([])
        mm["waiver.index.toml"] = idx([])
        mmr = run(mm)
        check("f4-module-record-deferred-cannot-eval", mmr is not None and mmr.status == CANNOT_EVALUATE)
        check("f4-module-record-deferral-named",
              mmr is not None and any("U2M" in m for m in mmr.cannot_evaluate))
        check("f4-module-record-not-false-invalid",
              mmr is not None and not any("not a supported record type" in f for f in mmr.findings))

        # ===== draft-2 discriminating vectors ========================================================
        # F4/codex-9: a malformed prior record id is a MALFORMED observation (cant), never store INVALID.
        mid = copy.deepcopy(clean_prior())
        mid["prior"]["records"]["not-an-id!"] = ("finding", "open")
        r = run(clean_machine(), obs=mid)
        check("f4-malformed-prior-record-id-cannot-eval", r is not None and r.status == CANNOT_EVALUATE)
        check("f4-malformed-prior-record-id-named",
              r is not None and any("malformed" in m for m in r.cannot_evaluate))
        # F4/codex-9: a prior record id whose namespace disagrees with its declared type is MALFORMED.
        nsm = copy.deepcopy(clean_prior())
        nsm["prior"]["records"]["BI-1"] = ("finding", "fixed")
        check("f4-prior-record-ns-type-mismatch-cannot-eval",
              run(clean_machine(), obs=nsm).status == CANNOT_EVALUATE)
        # codex-3: a created-terminal record whose BODY was rewritten while its status is unchanged -> a
        # finding, verified against the prior body digest.
        f = clean_machine()
        rw_dn = dn(1, "BI-1")
        rw_dn["title"] = "rewritten immutable receipt"
        f["done.index.toml"] = idx([rw_dn])
        r = run(f)
        check("history-immutable-body-rewrite-invalid", r is not None and r.status == INVALID)
        check("history-immutable-body-rewrite-named",
              r is not None and any("body changed" in x for x in r.findings))
        # codex-3: with NO prior body digest for a created-terminal record, body preservation is a named
        # CANNOT-EVALUATE boundary, never a silent VALID.
        nd = copy.deepcopy(clean_prior())
        nd["prior"]["digests"] = {}
        r = run(clean_machine(), obs=nd)
        check("history-immutable-body-no-digest-cannot-eval", r is not None and r.status == CANNOT_EVALUATE)
        check("history-immutable-body-no-digest-named",
              r is not None and any("body preservation" in m for m in r.cannot_evaluate))
        # codex-6: an actor-DEPENDENT transition (legal for a maintainer, illegal for an assistant) is
        # CANNOT-EVALUATE (the last-transition actor is not identifiable), never accept-if-any-actor.
        f = clean_machine()
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "fixed")])
        f["counters.toml"] = counters(FN=1)
        ad6 = copy.deepcopy(clean_prior())
        ad6["prior"]["records"]["FN-1"] = ("finding", "open")
        ad6["prior"]["counters_high"]["FN"] = 1
        r = run(f, obs=ad6)
        check("history-actor-dependent-transition-cannot-eval", r is not None and r.status == CANNOT_EVALUATE)
        # codex-10: C-RECEIPTS second loop - a done receipt targeting a NON-ratified backlog_item (active).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(1, "active"), bi(2, "open")])
        rc10 = copy.deepcopy(clean_prior())
        rc10["prior"]["records"]["BI-1"] = ("backlog_item", "active")
        r = run(f, obs=rc10)
        check("receipts-target-not-ratified-invalid", r is not None and r.status == INVALID)
        check("receipts-target-not-ratified-named",
              r is not None and any("not ratified" in x for x in r.findings))
        # gemini: a moved row whose destination index FILE is entirely absent -> a finding (not a cant).
        f = clean_machine()
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "reference", "destination": "archive/2026/reference.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["counters.toml"] = counters(RF=9)
        # archive/2026/reference.index.toml is deliberately NOT created (destination file absent)
        r = run(f)
        check("archive-dest-file-absent-invalid", r is not None and r.status == INVALID)
        check("archive-dest-file-absent-named", r is not None and any("does not exist" in x for x in r.findings))
        # F6.1: inline record schema-fault forwarding (_gather_inline) - a finding missing its title.
        f = clean_machine()
        bad_fn = fn(1)
        del bad_fn["title"]
        f["finding.index.toml"] = idx([bad_fn])
        f["counters.toml"] = counters(FN=1)
        check("inline-record-schema-fault-invalid", run(f).status == INVALID)
        # F6.2: C-MANIFEST finding forwarding - an unknown top-level manifest table.
        f = clean_machine()
        mbad = base_manifest()
        mbad["bogus_table"] = {"x": 1}
        f["manifest.toml"] = mbad
        bad_manifest = run(f)
        check("manifest-unknown-table-invalid", bad_manifest.checks["C-MANIFEST"] == "FINDING"
              and "manifest: unknown top-level table(s): bogus_table" in bad_manifest.findings)
        check("manifest-unknown-table-stops-dependent-checks",
              bad_manifest.status == CANNOT_EVALUATE
              and bad_manifest.checks["C-RECORDS"] == CANNOT_EVALUATE
              and bad_manifest.checks["C-ARCHIVE-ENUM"] == CANNOT_EVALUATE)
        # F6.5: index unknown-top-level-key finding (_index_rows).
        f = clean_machine()
        ibad = idx([bi(1, "done"), bi(2, "open")])
        ibad["bogus"] = 1
        f["backlog_item.index.toml"] = ibad
        check("index-unknown-key-invalid", run(f).status == INVALID)
        # F6.4: worklog_moved span-overlap (two spans jointly covering the present archived worklog ids).
        f = clean_machine()
        f["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"},
                              {"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        r = run(f)
        check("worklog-moved-overlap-invalid", r is not None and r.status == INVALID)
        check("worklog-moved-overlap-named", r is not None and any("overlap" in x for x in r.findings))
        # F6.6: an unknown observation key is a malformed injection -> CANNOT-EVALUATE, never dropped.
        r = run(clean_machine(), obs={"tracked": "tracked", "prior": clean_prior()["prior"], "bogus_key": 1})
        check("unknown-observation-key-cannot-eval", r is not None and r.status == CANNOT_EVALUATE)
        check("unknown-observation-key-named",
              r is not None and any("unrecognized" in m for m in r.cannot_evaluate))

        # ===== round-16 Fable-retrofix discriminating vectors (F-1..F-6) ==============================
        RUNID16 = "imp-20260601T000000Z-0123456789abcdef"
        # F-1: an EMPTY unregistered directory under the imports interior is graded like any other stray
        # (round-14 graded FILES only, so an empty rogue dir laundered to VALID). Steady state -> INVALID.
        _f1a = build(clean_machine())
        os.makedirs(str(_f1a / ".working" / "imports" / "rogue-empty"), exist_ok=False)
        _f1ar = validate_store(resolve_store(_f1a), observations=clean_prior())
        check("imports-empty-rogue-dir-none-invalid", _f1ar.status == INVALID)
        check("imports-empty-rogue-dir-named",
              any("C-CONTAINMENT" in f and "imports/rogue-empty" in f and "unregistered" in f
                  for f in _f1ar.findings))
        # a tree of ONLY-empty dirs under imports is still graded (its deepest empty leaf) -> INVALID.
        _f1b = build(clean_machine())
        os.makedirs(str(_f1b / ".working" / "imports" / "a" / "b" / "c"), exist_ok=False)
        _f1br = validate_store(resolve_store(_f1b), observations=clean_prior())
        check("imports-empty-tree-none-invalid", _f1br.status == INVALID)
        # under a SUBSTANTIATED partial (an active run carries plan.toml) an empty dir under imports
        # triages (VALID), never a finding, exactly as files under imports already do.
        _f1c = clean_machine()
        _f1c["manifest.toml"] = base_manifest()
        _f1c["manifest.toml"]["opf"]["import_status"] = "partial"
        _f1croot = build(_f1c, working={"imports/{}/plan.toml".format(RUNID16): "schema = 1"})
        os.makedirs(str(_f1croot / ".working" / "imports" / "empty-under-partial"),
                    exist_ok=False)
        _f1cr = validate_store(resolve_store(_f1croot), observations=clean_prior())
        check("imports-empty-dir-partial-triaged",
              _f1cr.status == VALID and any("empty-under-partial" in t for t in _f1cr.triage))
        # the empty imports ROOT itself (no runs) is a legitimate empty namespace and stays clean.
        _f1d = build(clean_machine())
        os.makedirs(str(_f1d / ".working" / "imports"), exist_ok=False)
        _f1dr = validate_store(resolve_store(_f1d), observations=clean_prior())
        check("imports-empty-root-valid", _f1dr.status == VALID)

        # F-2: a stray (non-index) file in an archive bucket routes through the SAME substantiated-partial
        # triage as its siblings: a finding at steady state, a triage entry under a substantiated partial.
        _f2 = clean_machine()
        _f2["archive/2026/strayfile.txt"] = "x\n"
        _f2r = run(_f2)
        check("archive-bucket-stray-file-steady-invalid",
              _f2r is not None and _f2r.status == INVALID
              and any("C-ARCHIVE-ENUM" in f and "strayfile.txt" in f for f in _f2r.findings))
        _f2p = clean_machine()
        _f2p["manifest.toml"]["opf"]["import_status"] = "partial"
        _f2p["archive/2026/strayfile.txt"] = "x\n"
        _f2pr = run(_f2p, working={"imports/{}/plan.toml".format(RUNID16): "schema = 1"})
        check("archive-bucket-stray-file-partial-triaged",
              _f2pr is not None and _f2pr.status == VALID
              and any("strayfile.txt" in t for t in _f2pr.triage))

        # F-3: the committed-pointer leg (leg 2) applies the SAME github/gitlab case fold as the
        # actual-remote leg. A dedicated sync_target differing only in case from the committed pointer
        # AGREES -> VALID (the actual remote also agrees).
        _f3 = clean_machine()
        _f3["manifest.toml"] = base_manifest()
        _f3["manifest.toml"]["store"] = {"sync_target": "github:Org/Repo"}
        _f3res = resolve_store(build(_f3))
        _f3res.target = classify_target("github:org/repo")
        _f3r = validate_store(_f3res, observations={"tracked": "tracked",
                                                    "actual_remote": "git@github.com:org/repo.git",
                                                    "prior": clean_prior()["prior"]})
        check("sync-pointer-leg-case-insensitive-valid", _f3r is not None and _f3r.status == VALID)

        # F-4: a non-canonical but CONTAINED [unmanaged] declaration (trailing slash, '//', or lexical
        # '..') is normalized so it covers the legacy content it names rather than over-firing it as
        # unregistered (a false INVALID). Without the fix each of these is INVALID.
        for _ncp in (".working/legacy-dir/", ".working//legacy-dir", ".working/x/../legacy-dir"):
            _f4 = clean_machine()
            _f4["manifest.toml"] = base_manifest()
            _f4["manifest.toml"]["unmanaged"] = {"paths": [_ncp]}
            check("unmanaged-noncanonical-not-false-invalid:" + _ncp,
                  run(_f4, working={"legacy-dir/old-note.md": "x\n"}).status == VALID)

        # F-6a: C-CONTIGUITY "does not start at WL-1" is the SOLE layer catching a worklog whose first
        # present id is not WL-1 (the below-max side; the overhang side is C-NO-DELETION). A zero-release
        # store holding only WL-2, WL-3 (high-water 3, contiguous among themselves) is INVALID solely via
        # this branch; deleting it flips the store VALID.
        _f6a = copy.deepcopy(pr_machine)
        _f6a["worklog.toml"] = {"schema": 1, "entry": [wl(2), wl(3)]}
        _f6a["counters.toml"] = counters(FN=2, BI=0, DN=0, WL=3, HO=0)
        _f6a_obs = {"tracked": "tracked",
                    "prior": {"releases": [],
                              "counters_high": counters(FN=2, BI=0, DN=0, WL=3, HO=0)["counters"],
                              "records": {"FN-1": ("finding", "open"), "FN-2": ("finding", "open")}}}
        _f6ar = run(_f6a, product=pr_product, obs=_f6a_obs)
        check("c-contiguity-start-not-wl1-invalid", _f6ar is not None and _f6ar.status == INVALID)
        check("c-contiguity-start-not-wl1-named",
              _f6ar is not None and any("C-CONTIGUITY" in f and "does not start at WL-1" in f
                                        for f in _f6ar.findings))
        # F-6b: the "archived worklog WL-n not enumerated in a worklog_moved span" branch is the SOLE
        # layer catching an archived worklog id no span accounts for. A bucket holding WL-1, WL-2 whose
        # span enumerates only WL-1 is INVALID solely via it.
        _f6b = clean_machine()
        _f6b["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
            "worklog_moved": [{"span": ["WL-1", "WL-1"], "destination": "archive/2026/worklog.toml"}]}
        _f6br = run(_f6b)
        check("archive-wl-unenumerated-invalid", _f6br is not None and _f6br.status == INVALID)
        check("archive-wl-unenumerated-named",
              _f6br is not None and any("C-ARCHIVE-ENUM" in f and "WL-2" in f and "not enumerated" in f
                                        for f in _f6br.findings))
        # F-6c: the committed-pointer leg (leg 2) is the SOLE layer catching a committed pointer whose
        # remote genuinely disagrees with a dedicated sync_target while the actual remote (leg 3) AGREES
        # -> INVALID; deleting leg 2 flips it VALID. Pairs with the F-3 fold fix.
        _f6c = clean_machine()
        _f6c["manifest.toml"] = base_manifest()
        _f6c["manifest.toml"]["store"] = {"sync_target": "github:Org/Repo"}
        _f6cres = resolve_store(build(_f6c))
        _f6cres.target = classify_target("github:other/repo")
        _f6cr = validate_store(_f6cres, observations={"tracked": "tracked",
                                                      "actual_remote": "git@github.com:org/repo.git",
                                                      "prior": clean_prior()["prior"]})
        check("sync-pointer-leg-disagree-invalid", _f6cr is not None and _f6cr.status == INVALID)
        check("sync-pointer-leg-disagree-named",
              _f6cr is not None and any("committed pointer remote" in f and "does not agree" in f
                                        for f in _f6cr.findings))

        # Fable F2: a dedicated LOCAL sync_target (dir:/path -> tcanon is None) whose committed pointer names
        # a canonicalizable REMOTE is a C-SYNC-AGREE finding (leg 2), symmetric with the actual-remote leg's
        # local-target-with-a-remote finding. Pre-fix leg 2 short-circuited on `tcanon and pcanon` (tcanon
        # None) and passed the remote pointer silently. Hand-set the remote Target, since resolve_store
        # rejects a remote committed pointer before validate_store sees it.
        _lt = clean_machine()
        _lt["manifest.toml"] = base_manifest()
        _lt["manifest.toml"]["store"] = {"sync_target": "dir:/some/where"}
        _ltres = resolve_store(build(_lt))
        _ltres.target = classify_target("github:org/repo")
        _ltr = validate_store(_ltres, observations={"tracked": "tracked", "actual_remote": "",
                                                    "prior": clean_prior()["prior"]})
        check("sync-local-target-remote-pointer-invalid", _ltr is not None and _ltr.status == INVALID)
        check("sync-local-target-remote-pointer-named",
              _ltr is not None and any("is a local target but the" in f
                                       and "committed pointer names a remote" in f for f in _ltr.findings))

        # F-5: _canonical_remote exotic edges.
        # (a) an scp bracketed-IPv6 host whose PATH itself contains '@' must not mis-split on a colon
        # inside the brackets; it canonicalizes to the same (host, path) as the ssh:// equivalent.
        check("canonical-ipv6-scp-at-in-path-matches-ssh",
              _canonical_remote("git@[2001:db8::1]:path@x") is not None
              and _canonical_remote("git@[2001:db8::1]:path@x")
              == _canonical_remote("ssh://[2001:db8::1]/path@x"))
        # (b) an UNBRACKETED multi-colon (IPv6-looking) scheme authority is unresolvable (None), never
        # mis-read with a trailing '::22' consumed as a default port.
        check("canonical-unbracketed-ipv6-none", _canonical_remote("ssh://2001:db8::22/p") is None)
        # (c) a default port written with leading zeros names the default endpoint (== the port-less form).
        check("canonical-leading-zero-default-port-folds",
              _canonical_remote("ssh://h:022/p") is not None
              and _canonical_remote("ssh://h:022/p") == _canonical_remote("ssh://h/p"))

        # ===== ROUND-2 retrofix discriminating vectors ==============================================
        # The exit_code retrofix hardened the FALLTHROUGH: only the exact VALID status yields 0 and any
        # unrecognized/malformed status fails CLOSED to 2 (never a two-valued fall-through that read a bad
        # status as success). The VALID->0 and INVALID->1 explicit branches were satisfied by the
        # pre-retrofix reducer too, so those pins discriminate nothing about this fix and were removed
        # (F12). The UNKNOWN-status vector is the sole discriminator: it exercises the hardened fallthrough,
        # failing (returning 0) under the pre-retrofix reducer and returning 2 only with the fix in place.
        check("r2-exit-code-unknown-fails-closed", exit_code(StoreValidation("UNKNOWN-STATUS")) == 2)
        check("r2-canonical-scheme-bad-bracket-none", _canonical_remote("ssh://[::1/p") is None)
        check("r2-canonical-scheme-bad-bracket2-none",
              _canonical_remote("ssh://[2001/db8::1]:org/repo") is None)
        check("r2-canonical-nondefault-leading-zero-folds",
              _canonical_remote("ssh://h:02222/p") is not None
              and _canonical_remote("ssh://h:02222/p") == _canonical_remote("ssh://h:2222/p"))
        check("r2-canonical-nondefault-port-significant",
              _canonical_remote("ssh://h:23/p") != _canonical_remote("ssh://h/p"))
        check("r2-canonical-ipv6-scp-at-path-preserved",
              _canonical_remote("git@[2001:db8::1]:path@x") == ("[2001:db8::1]", "path@x"))
        check("r2-canonical-unicode-port-none", _canonical_remote("ssh://h:\u00b2/p") is None)
        # Fable F1: an ASCII-digit port long enough to trip CPython's int() digit limit (>4300) is
        # unresolvable, never a ValueError escaping the canonicalizer (the LENGTH sibling of the Unicode
        # case above). Pre-fix this call raised ValueError out of _canonical_remote.
        check("r2-canonical-long-port-none",
              _canonical_remote("ssh://h:" + "1" * 5000 + "/p") is None)
        # Fable F3: an EMPTY bracketed authority '[]' is unresolvable in every form (scheme with/without a
        # port, and scp), never a mis-canonicalized ('[]', path) pair. Pre-fix these returned ('[]', 'p').
        check("f3-canonical-empty-bracket-scheme-port-none", _canonical_remote("ssh://[]:22/p") is None)
        check("f3-canonical-empty-bracket-scheme-noport-none", _canonical_remote("ssh://[]/p") is None)
        check("f3-canonical-empty-bracket-scp-none", _canonical_remote("git@[]:p") is None)
        # regression: a real bracketed IPv6 authority still canonicalizes (the empty-bracket guard is tight)
        check("f3-canonical-nonempty-bracket-preserved",
              _canonical_remote("ssh://[2001:db8::1]/p") == ("[2001:db8::1]", "p"))
        # ROUND-6 codex: an out-of-range port and a malformed bracketed host are cannot-evaluate (None),
        # never a spurious endpoint that yields a clean C-SYNC-AGREE. Pre-fix these returned a (host, path)
        # pair (('host:65536','org/repo'), ('[nonsense]','org/repo'), etc). Removing either guard fails these.
        check("r6-canonical-port-over-range-none", _canonical_remote("ssh://host:65536/org/repo") is None)
        check("r6-canonical-port-zero-none", _canonical_remote("ssh://host:0/org/repo") is None)
        check("r6-canonical-port-huge-none", _canonical_remote("ssh://host:99999999/p") is None)
        check("r6-canonical-bad-bracket-scheme-none", _canonical_remote("ssh://[nonsense]/org/repo") is None)
        check("r6-canonical-bad-bracket-scp-none", _canonical_remote("git@[nonsense]:org/repo") is None)
        # regression: an in-range non-default port and a valid IPv6 bracket still canonicalize.
        check("r6-canonical-port-max-preserved",
              _canonical_remote("ssh://host:65535/p") == ("host:65535", "p"))
        check("r6-canonical-good-bracket-preserved",
              _canonical_remote("ssh://[2001:db8::22]/org/repo") == ("[2001:db8::22]", "org/repo"))
        # ROUND-8 codex (finding 2): the WHOLE host token is validated. A host carrying WHITESPACE, a
        # CONTROL character, or a STRAY/unbalanced bracket is not a real host, so it is CANNOT-EVALUATE
        # (None), never a spurious ('h ost', ...) / ('host\nforged', ...) / ('host]', ...) pair that could
        # falsely satisfy C-SYNC-AGREE when the manifest target and observed remote strings match. Each
        # reverted host-token guard flips one of these red.
        check("r8-canonical-host-space-none", _canonical_remote("ssh://h ost/p") is None)
        check("r8-canonical-host-tab-none", _canonical_remote("ssh://ho\tst/p") is None)
        check("r8-canonical-host-newline-none", _canonical_remote("ssh://host\nFORGED/p") is None)
        check("r8-canonical-host-control-none", _canonical_remote("ssh://ho\x01st/p") is None)
        check("r8-canonical-host-stray-bracket-scheme-none", _canonical_remote("ssh://host]/p") is None)
        check("r8-canonical-host-stray-open-bracket-scheme-none", _canonical_remote("ssh://ho[st/p") is None)
        check("r8-canonical-host-stray-bracket-scp-none", _canonical_remote("git@host]:p") is None)
        check("r8-canonical-host-space-scp-none", _canonical_remote("git@ho st:p") is None)
        # regression: an ordinary host, a userinfo form, and a valid IPv6 bracket are UNAFFECTED by the token
        # validation (no false positive on a legitimate host).
        check("r8-canonical-plain-host-preserved", _canonical_remote("ssh://good.host/p") == ("good.host", "p"))
        check("r8-canonical-userinfo-host-preserved",
              _canonical_remote("ssh://git@good.host/p") == ("good.host", "p"))
        check("r8-canonical-bracket-host-still-ok",
              _canonical_remote("git@[2001:db8::1]:path@x") == ("[2001:db8::1]", "path@x"))
        # ROUND-10 F1 (MODERATE): the host-token validation is now an ALLOWLIST, so a Unicode FORMAT
        # character (category Cf: zero-width, bidi) in the host is CANNOT-EVALUATE (None), never a clean
        # (host, path) pair that could falsely satisfy C-SYNC-AGREE. The earlier blocklist (whitespace /
        # control / DEL only) admitted these and canonicalized `ssh://ho<ZWSP>st/p` to ('ho<ZWSP>st', 'p');
        # each check reverts red if the allowlist is weakened back to a blocklist. Covers a zero-width space
        # (U+200B) and a right-to-left override (U+202E) in both the scheme-URL and scp host positions.
        check("f1-canonical-host-zwsp-scheme-none", _canonical_remote("ssh://ho\u200bst/p") is None)
        check("f1-canonical-host-bidi-scheme-none", _canonical_remote("ssh://ho\u202est/p") is None)
        check("f1-canonical-host-zwsp-scp-none", _canonical_remote("git@ho\u200bst:p") is None)
        check("f1-canonical-host-bidi-scp-none", _canonical_remote("git@ho\u202est:p") is None)
        # regression: the round-8/9 whitespace/control/stray-bracket forms STILL reject under the allowlist,
        # and a legitimate bracketed IPv6 literal carrying a non-default PORT is still preserved (the
        # allowlist admits the brackets, ':' and digits; the structure checks in _canonical_remote pass it).
        check("f1-canonical-host-space-still-none", _canonical_remote("ssh://h ost/p") is None)
        check("f1-canonical-host-newline-still-none", _canonical_remote("ssh://host\nFORGED/p") is None)
        check("f1-canonical-ipv6-port-preserved",
              _canonical_remote("ssh://[2001:db8::22]:2222/p") == ("[2001:db8::22]:2222", "p"))
        # ROUND-12 F1 (MODERATE): host STRUCTURAL validation completes the host class (chars + structure).
        # After the character allowlist, the host STRUCTURE is validated: an empty DNS label (`a..b`), a
        # label over 63 characters, and an IPv4 address inside brackets (`[127.0.0.1]`) are each a malformed
        # remote and must be CANNOT-EVALUATE (None), never a spurious (host, path) pair that could falsely
        # satisfy C-SYNC-AGREE even when the manifest target and observed remote strings match. Pre-fix each
        # returned a pair (('a..b','p'), (64-char label, 'p'), ('[127.0.0.1]','p')); reverting the structural
        # checks (_dns_or_ipv4_ok, or _bracketed_host_ok's IPv6-only parse) reds them. Covered in both the
        # scheme-URL and scp host positions.
        check("f1r12-canonical-empty-dns-label-scheme-none", _canonical_remote("ssh://a..b/p") is None)
        check("f1r12-canonical-empty-dns-label-scp-none", _canonical_remote("git@a..b:p") is None)
        check("f1r12-canonical-oversize-label-scheme-none",
              _canonical_remote("ssh://" + "a" * 64 + ".example/p") is None)
        check("f1r12-canonical-oversize-label-scp-none",
              _canonical_remote("git@" + "a" * 64 + ".example:p") is None)
        check("f1r12-canonical-ipv4-in-brackets-scheme-none", _canonical_remote("ssh://[127.0.0.1]/p") is None)
        check("f1r12-canonical-ipv4-in-brackets-scp-none", _canonical_remote("git@[127.0.0.1]:p") is None)
        check("f1r12-canonical-leading-dot-none", _canonical_remote("ssh://.lead/p") is None)
        check("f1r12-canonical-trailing-dot-none", _canonical_remote("ssh://trail./p") is None)
        # regression (legit controls the structural checks must NOT red): a valid DNS host, a bracketed IPv6
        # literal carrying a non-default port, a bare IPv4 dotted-quad (scheme and scp), and a maximal 63-char
        # label all still canonicalize to a real (host, path) pair.
        check("f1r12-canonical-dns-host-preserved", _canonical_remote("ssh://good.host/p") == ("good.host", "p"))
        check("f1r12-canonical-ipv6-port-preserved",
              _canonical_remote("ssh://[2001:db8::1]:2222/p") == ("[2001:db8::1]:2222", "p"))
        check("f1r12-canonical-bare-ipv4-scheme-preserved",
              _canonical_remote("ssh://203.0.113.5/p") == ("203.0.113.5", "p"))
        check("f1r12-canonical-bare-ipv4-scp-preserved",
              _canonical_remote("git@203.0.113.5:p") == ("203.0.113.5", "p"))
        check("f1r12-canonical-max-label-preserved",
              _canonical_remote("ssh://" + "a" * 63 + ".example/p") == ("a" * 63 + ".example", "p"))
        # ROUND-15 F3 (MINOR): the host structural class is COMPLETED. 15 RFC-invalid forms still
        # canonicalized clean after round-12: a DNS label with a HYPHEN EDGE (LDH: a label may not begin or
        # end with '-'), an ALL-NUMERIC final label (neither a valid IPv4 literal nor a resolvable hostname),
        # and an IPv4-LOOKALIKE with an out-of-range (>255) or leading-zero octet (IPv4Address rejects it, so
        # it fell through as a dotted numeric DNS name). Each must now be CANNOT-EVALUATE (None), never a
        # spurious (host, path) pair that could falsely satisfy C-SYNC-AGREE even when the manifest target and
        # observed remote strings match. Pre-fix each returned a clean pair; reverting the LDH/all-numeric
        # checks in _dns_or_ipv4_ok reds them. Covered in both the scheme-URL and scp host positions. IP
        # fixtures use RFC-5737 doc-range or plainly-invalid octets (never a private RFC-1918 address).
        for _bad in ("ssh://-lead.example/p", "git@-lead.example:p",        # hyphen-edge: leading '-'
                     "ssh://trail-.example/p", "git@trail-.example:p",      # hyphen-edge: trailing '-'
                     "ssh://mid.-inner.example/p", "ssh://a-.b.example/p",  # hyphen-edge: interior label
                     "ssh://host.123/p", "git@host.123:p",                  # all-numeric final label
                     "ssh://example.0/p",                                   # all-numeric final label (single digit)
                     "ssh://256.0.113.5/p", "git@256.0.113.5:p",            # IPv4-lookalike: octet > 255
                     "ssh://999.999.999.999/p", "git@999.999.999.999:p",    # IPv4-lookalike: all octets > 255
                     "ssh://203.0.113.05/p", "git@203.0.113.05:p"):         # IPv4-lookalike: leading-zero octet
            check("f3r15-canonical-rfc-invalid-host-none:" + _bad, _canonical_remote(_bad) is None)
        # regression (legit controls the structural checks must NOT red): a hyphen INTERIOR to a label, an
        # all-numeric NON-final label (valid under a real final label), and a bare IPv4 all still canonicalize.
        check("f3r15-canonical-hyphen-interior-preserved",
              _canonical_remote("ssh://a-b.example/p") == ("a-b.example", "p"))
        check("f3r15-canonical-numeric-nonfinal-label-preserved",
              _canonical_remote("ssh://123.example/p") == ("123.example", "p"))
        check("f3r15-canonical-bare-ipv4-still-preserved",
              _canonical_remote("ssh://203.0.113.5/p") == ("203.0.113.5", "p"))
        _r2wd = build(pr_machine, product=pr_product)
        os.makedirs(str(_r2wd / ".working" / "toml" / "worklog"), exist_ok=False)
        _r2wdr = validate_store(resolve_store(_r2wd), observations=pr_obs)
        check("r2-empty-perrecord-ledger-dir-invalid", _r2wdr.status == INVALID)
        check("r2-empty-perrecord-ledger-dir-named",
              any("C-CONTAINMENT" in f and "worklog" in f and "unregistered" in f for f in _r2wdr.findings))
        check("r2-perrecord-clean-still-valid",
              run(pr_machine, product=pr_product, obs=pr_obs).status == VALID)
        _r2lg = clean_machine()
        _r2lg["manifest.toml"] = base_manifest()
        _r2lg["manifest.toml"]["store"] = {"sync_target": "github:org/repo"}
        _r2lgres = resolve_store(build(_r2lg))
        _r2lgres.target = classify_target("git:not-a-canonicalizable-url")
        _r2lgr = validate_store(_r2lgres, observations={"tracked": "tracked",
                                                        "actual_remote": "git@github.com:org/repo.git",
                                                        "prior": clean_prior()["prior"]})
        check("r2-pointer-uncanon-remote-cannot-eval", _r2lgr is not None and _r2lgr.status == CANNOT_EVALUATE)
        check("r2-pointer-uncanon-remote-named",
              _r2lgr is not None and any("committed pointer remote form" in m and "cannot be canonicalized" in m
                                         for m in _r2lgr.cannot_evaluate))
        _r2lz = clean_machine()
        _r2lz["lease.toml"] = dict(schema=1, holder="run-abc", operation="", acquired_at=TS)
        _r2lzr = run(_r2lz)
        check("r2-lease-empty-operation-invalid", _r2lzr.status == INVALID)
        check("r2-lease-empty-operation-named",
              any("C-LEASE" in f and "operation" in f for f in _r2lzr.findings))
        _r2rn = clean_machine()
        _r2rn["done.index.toml"] = idx([])
        _r2rn["counters.toml"] = counters(DN=0)
        _r2rn_prior = copy.deepcopy(clean_prior())
        del _r2rn_prior["prior"]["records"]["DN-1"]
        del _r2rn_prior["prior"]["digests"]["DN-1"]
        _r2rn_prior["prior"]["counters_high"]["DN"] = 0
        _r2rnr = run(_r2rn, obs=_r2rn_prior)
        check("r2-ratified-bi-no-done-isolated-invalid", _r2rnr is not None and _r2rnr.status == INVALID)
        check("r2-ratified-bi-no-done-isolated-named",
              _r2rnr is not None and any("C-RECEIPTS" in f and "no done receipt" in f
                                         for f in _r2rnr.findings))
        _r2gs = clean_machine()
        _r2gs["manifest.toml"] = base_manifest()
        _r2gs["manifest.toml"]["store"] = {"sync_target": "git:https://github.com/Org/Repo"}
        check("r2-git-scheme-github-case-valid",
              run(_r2gs, obs={"tracked": "tracked", "actual_remote": "https://github.com/org/repo",
                          "prior": clean_prior()["prior"]}).status == VALID)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-CHECK SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-CHECK SELF-TEST: PASS ({} whole-store integrity checks)".format(checked))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(self_test())
