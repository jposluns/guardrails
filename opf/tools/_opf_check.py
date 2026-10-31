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
import tomllib
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
    validate_imported_record,
)
# U3 supplies the ledger validators, the frozen/rotation/partition guards, and the across-time append-only.
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id,
    check_frozen_coverage, check_ids_partition, check_rotation_only_released, _verify_append_only, _wl_num,
    parse_semver, _parse_span, _valid_digest, RELEASE_KEYS, RELEASE_OPTIONAL_KEYS, _imported_row_findings,
)

# Fixed store-tree file / directory names (OPF-SPEC 4.2 layout; all lowercase machine source).
COUNTERS_NAME = _opf_store.COUNTERS_NAME
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
# The four imported-series checks spec 8.3 names, which activation MUST add to the doctor roster (spec
# 4.2). They join the roster only for a store whose imported series is ACTIVE (imported_series_active
# below), appended after its homes roster, so an inactive store's roster is unchanged (its report can
# still gain the section 8.6 firewall residuals, which are keyed on provenance, not on activation; see
# _FIREWALL_RESIDUALS). All four are graded here on an active store; result()'s roster reconciliation still routes
# any of them that does not run to CANNOT-EVALUATE (a check that never runs is never a pass), so no
# active store certifies VALID on a partial imported roster. While this roster is
# non-empty the activation tripwire in _check_containment stays disarmed, and the self-test asserts
# imported_content_checks_landed below: once SUPPORTED_SPEC_VERSION reaches IMPORTED_SPEC_VERSION this
# roster must be non-empty, NEW ids in the exact imported-check grammar (_IMPORTED_ID_RE below),
# disjoint from REQUIRED_CHECKS and HOMES2_CHECKS, and inside required_checks(h, imported=True) for
# EVERY homes generation h from 1 through SUPPORTED_HOMES, so emptying it, or swapping in a
# placeholder, cannot ship once the series is supported. Any OTHER wiring (even a legitimate-looking
# alternative such as a roster keyed on the generation as well) reads red, which fails closed: a red
# on a legitimate alternative, never a green on a wiring nothing grades.
IMPORTED_CHECKS = ("C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE", "C-IMPORTED-SEGREGATION")
# The exact imported-check id grammar, matched in full: the C-IMPORTED- prefix, then one or more
# runs of upper-case ASCII letters joined by single hyphens. The class is the one every id in
# REQUIRED_CHECKS and HOMES2_CHECKS uses after its C-, and it admits the four imported-series checks
# spec 8.3 names (C-IMPORTED-SCHEMA, C-IMPORTED-IDS, C-IMPORTED-PROVENANCE, C-IMPORTED-SEGREGATION).
# An id carrying whitespace, a lower-case or mixed-case prefix, or any other character is refused;
# it is never stripped or case-folded into a match.
_IMPORTED_ID_RE = re.compile(r"^C-IMPORTED-[A-Z]+(?:-[A-Z]+)*\Z")


def required_checks(homes, imported=False):
    """The closed required-check roster for a store's active homes generation, with IMPORTED_CHECKS
    appended when its imported series is active (`imported`, the imported_series_active flag)."""
    if homes < 2:
        roster = REQUIRED_CHECKS
    else:
        at = REQUIRED_CHECKS.index("C-ARCHIVE-ENUM") + 1
        roster = REQUIRED_CHECKS[:at] + HOMES2_CHECKS + REQUIRED_CHECKS[at:]
    return roster + IMPORTED_CHECKS if imported else roster


def imported_series_active(manifest_data):
    """The imported-series activation flag (spec 8.2/4.2), keyed on the homes-2 declaration: True only
    when the store is graded at homes 2 (_opf_store.homes_generation: this tooling activates homes 2
    and the manifest declares exactly homes = 2 with spec_version 2.0.0) AND that declaration carries
    the imported series under this tooling's own support (_opf_store.imported_series_declared: a
    declared spec_version of IMPORTED_SPEC_VERSION or later, with SUPPORTED_SPEC_VERSION at or above
    it). The 1.3.0 imported-series content activates only inside the 2.0.0 declaration, so a homes-1
    store never activates it, whatever spec_version it declares, and raising either tooling constant
    alone activates nothing. False for every store this tooling validates today (SUPPORTED_HOMES is
    1). Every imported-series reader takes this ONE flag: the doctor driver derives it once for the
    required-check roster, the four imported checks, C-COUNTERS, C-ID-SPACE, C-NO-DELETION, C-CONTIGUITY
    and the across-time checks; classify_containment derives it internally from the manifest for the
    C-CONTAINMENT registration and every consumer of the classification (the write guard,
    _opf_write_guard.plan_write_scope, included), with _check_containment reading it again only for
    its emptied-roster tripwire; and the observer's prior imported baseline (_opf_observe._reconstruct_prior,
    which reads _reconstruct_imported only where it holds) and the record counter proof
    (_opf_record._counter_state) read it too. So they cannot disagree on which stores carry
    the imported series or on the imported managed set. homes2_carries_imported_series, asserted by
    the self-test at the shipped constants, makes this flag read exactly the homes-2 grade
    (homes_generation >= 2) on every tooling that can ship homes 2."""
    return (_opf_store.homes_generation(manifest_data) >= 2
            and _opf_store.imported_series_declared(manifest_data))


def imported_content_checks_landed():
    """The tooling-side imported-series activation gate, asserted by the self-test at the SHIPPED
    constants and exercised there under controlled versions, rosters, and wirings. True while the
    imported series is unsupported, judged through _opf_store.imported_series_supported, the
    SAME support comparison the imported_series_declared gate reads (one shared helper, so the
    gate and this predicate cannot disagree about support): imported_series_active is then False
    for every store, and the runtime tripwire in _check_containment covers an ACTIVE imported
    series whose roster is empty (a populated roster disarms it).
    Once support reaches the threshold, True ONLY when
    IMPORTED_CHECKS is a non-empty roster of NEW ids, each a full match of the exact
    imported-check grammar _IMPORTED_ID_RE (the C-IMPORTED- prefix, then upper-case ASCII letter
    runs joined by single hyphens; an id carrying whitespace, a lower-case or mixed-case prefix, or
    any other character is refused, never stripped or case-folded into a match), disjoint from
    REQUIRED_CHECKS and HOMES2_CHECKS (a placeholder reusing an existing id such as C-CONTAINMENT
    implements no imported-content grading, so reuse never stands in), and inside
    required_checks(h, imported=True) for EVERY homes generation h from 1 through SUPPORTED_HOMES.
    An active series is graded only at homes 2 (imported_series_active), but required_checks appends
    the imported ids whatever the generation, so this predicate keeps the stricter every-generation
    bound: a variant wiring them into one generation's imported roster only reads red, a red on a
    legitimate alternative rather than a green on an unwired generation. Raising
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
    ids = set(IMPORTED_CHECKS)
    if not ids:
        return False
    if not all(isinstance(cid, str) and _IMPORTED_ID_RE.fullmatch(cid) for cid in ids):
        return False
    if ids & (set(REQUIRED_CHECKS) | set(HOMES2_CHECKS)):
        return False
    # Below one supported generation the all() below is vacuously True: refuse instead.
    if _opf_store.SUPPORTED_HOMES < 1:
        return False
    return all(ids <= set(required_checks(h, imported=True))
               for h in range(1, _opf_store.SUPPORTED_HOMES + 1))


def homes2_carries_imported_series():
    """The tooling-side coupling of the homes-2 and imported-series activations (spec 4.2/8.2),
    asserted by the self-test at the SHIPPED constants: a tooling that activates homes 2
    (_opf_store.SUPPORTED_HOMES at or above 2) must also support the imported series
    (_opf_store.imported_series_supported, the ONE support comparison), since the series activates
    only inside the 2.0.0 / homes = 2 declaration. Under this coupling imported_series_active reads
    exactly the homes-2 grade (_opf_store.homes_generation >= 2) for every manifest: the graded
    declaration is HOMES2_SPEC_VERSION, at or above IMPORTED_SPEC_VERSION, so the declared arm holds,
    and this predicate supplies the support arm. C-CONTAINMENT's registration, its tripwire and the
    write guard therefore open on every graded homes-2 store, and raising SUPPORTED_HOMES without
    SUPPORTED_SPEC_VERSION at or above IMPORTED_SPEC_VERSION fails the suite instead of shipping a
    homes-2 store whose imported leaves grade as unregistered strays. True while homes 2 is inactive
    (SUPPORTED_HOMES exactly 1). Reads every constant through the _opf_store module (never this
    module's by-value SUPPORTED_SPEC_VERSION import) and fails closed: a SUPPORTED_HOMES that is
    missing, not an int (a bool included) or below 1, or a SUPPORTED_SPEC_VERSION or
    IMPORTED_SPEC_VERSION that is missing, not a string, or not a bare SemVer, answers False rather
    than raising."""
    homes = getattr(_opf_store, "SUPPORTED_HOMES", None)
    if type(homes) is not int or homes < 1:
        return False
    if homes < 2:
        return True
    for name in ("SUPPORTED_SPEC_VERSION", "IMPORTED_SPEC_VERSION"):
        version = getattr(_opf_store, name, None)
        if not isinstance(version, str) or _opf_store._parse(version) is None:
            return False
    return _opf_store.imported_series_supported()


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
    homes-2 checks when the report carries one, plus IMPORTED_CHECKS when it carries one. result() grades
    every check its roster requires, so a homes-2 report always carries the homes-2 checks and an active
    imported series' report always carries the imported ones; a legacy report carries neither."""
    checks = getattr(result, "checks", None) or {}
    homes = 2 if any(cid in checks for cid in HOMES2_CHECKS) else 1
    imported = any(cid in checks for cid in IMPORTED_CHECKS)
    return tuple(cid for cid in required_checks(homes, imported) if cid not in DELIVERABLE_DRIFT_CHECKS)


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
    "Journal contents are not inspected by doctor, except that it lists the id reservation home of each "
    "journal kind and reads each reservation file's bytes, for the reserved numbers alone; that read is the "
    "one exception spec section 4.2 makes to doctor excluding journals, read-only and for the section 8.2 "
    "counter checks alone. A staged plan does not prove recoverability. "
    "Homes-2 evidence inventories establish local membership, not actor authenticity; losing a whole "
    "bundle, inventory and payload together, requires independent history to detect.",
    "A reserved number that no present record carries is treated as issued only where the prior committed "
    "snapshot (HEAD) held no record with that id; where it held one, the absence is a C-NO-DELETION finding, "
    "and where no prior enumerating its ids was observed it is CANNOT-EVALUATE. Doctor sees the working "
    "tree, the reservations and that one commit only, so a record published and deleted again with no "
    "commit holding it in between (before the first commit, or after HEAD) leaves just its reservation, "
    "which no check can tell from an orphaned one, and a deletion already committed in HEAD is visible only "
    "to a doctor run before that commit.",
    "A worklog number an orphaned reservation holds passes C-NO-DELETION and C-CONTIGUITY here, but no "
    "release span can cover it, since a span covers only entries that exist and must tile (spec 6.1). The "
    "release cut reads no reservation and refuses the unreleased tail while such a number stands below the "
    "highest worklog entry, so a VALID doctor result does not mean the unreleased tail can be cut.",
)
# The section 8.6 authority firewall is keyed on provenance (an importer actor.kind), never on whether the
# imported series is active, so these are disclosed on the report of every store that holds an
# importer-authored clean record or an active imported series; a store with neither holds no record the
# firewall governs, and its residuals are unchanged. The firewall arms this engine grades (the receipt
# firewall and the pending_decision chain arm, _check_importer_receipts and _check_importer_chains, and
# C-HANDOFF's importer exclusion) are part of section 8.6 only, so their PASS must never read as full
# section 8.6 coverage: the section 8.6 obligations this engine is known to leave ungraded, or to grade
# only through the views a store declares, are named here, beside the one acceptance it cannot judge
# beyond its shape. The self-test pins these entries, so changing or dropping one fails it.
_FIREWALL_RESIDUALS = (
    "Section 8.6 firewall obligations this engine grades only in part or not at all, so a pass of the "
    "firewall arms it grades (the receipt firewall, the pending_decision chain arm and the at-most-one-"
    "current handoff exclusion) is not full section 8.6 coverage. This engine has no actionability or "
    "decision-register join of its own: importer-authored blocks granting a current stop, importer-"
    "authored open or active backlog items joining actionability, and the decision register listing an "
    "importer-authored decision as an effective resolution are graded only through C-VIEW-DRIFT, which "
    "holds each declared view to the views planner, whose actionability join (the TODO.md, BACKLOG.md and "
    "PIPELINE.md views) and decision-resolution join (the DECISIONS.md and DECISIONS.toml views) exclude "
    "importer-authored records; a store that declares none of those views leaves these obligations "
    "ungraded. An importer-authored record discharging a required supersession outside a pending_decision "
    "chain is not graded, nor is a required supersession that no record discharges at all; and an "
    "importer-authored record satisfying a current approval obligation is not interpreted by any check.",
    "The section 8.6 current-state joins over the module-tier types (an active waiver, a held "
    "session_lease, an active mode) and field-borne authority (a gate_run verdict, a tier_assessment "
    "outcome, an artifact's promoted state, a release's published state, a maintainer_action's done) are "
    "not graded, because no record schema for those types ships in the baseline validator: a store holding "
    "such a clean record is CANNOT-EVALUATE under the module-tier schema deferral, and an imported one is a "
    "C-IMPORTED-SCHEMA finding, so neither certifies VALID until those schemas ship. No check of this "
    "engine joins preference_pattern records into current state: C-RECORDS grades their records and C-LINKS "
    "resolves links to them, but nothing reads a ratified active status as current authority. Where the "
    "store declares a DECISIONS.md view, C-VIEW-DRIFT holds it to the views planner, which holds an "
    "importer-authored pattern out of Active and lists it only under Imported history; beyond that view, "
    "the firewall obligation on an importer-authored active preference_pattern binds the readers of those "
    "records.",
    "The section 8.6 receipt remedy is accepted on its machine-checkable shape alone: a maintainer-"
    "authored clean maintainer_decision whose `corrects` link targets the ratified done item. Whether its "
    "decision text records that the item's completion stands is prose the specification gives no "
    "machine-checkable test for, so no validator grades it. A later record that links back to overturn "
    "that decision is not read either, since the specification names no link relation that overturns a "
    "maintainer_decision, so an overturned remedy is still accepted.",
)
# Disclosed only on a report whose imported series is active, so an inactive report's residuals are
# unchanged: the imported-series part of section 8.6 that no check grades. The self-test pins it too.
_IMPORTED_RESIDUALS = (
    "Section 8.6 bars the imported series from supplying current authority through a link, an extension, "
    "or an adoption approval. The link part is graded (C-IMPORTED-SCHEMA refuses an imported-to-clean "
    "link); authority carried by an extension field or an adoption approval on an imported record is not "
    "interpreted by any check of this engine.",
)
# Disclosed only on a report of a homes-1 store that has adopted (its adoption home exists), so a legacy
# report residuals are unchanged.
_ADOPTING_RESIDUALS = (
    "Homes-1 adoption control area (spec 4.2, 17): an admitted adoption run is registered at file level "
    "only, each path its committed inventories list; its recorded Move destinations are registered only "
    "once its retirement is recorded, and every other path under the adoption homes is graded. Admission "
    "and retirement are decided from the committed evidence bundle and run archive alone, whose listed "
    "bundle, archive and Move-root bytes are verified at their recorded digests; a Move-root row is "
    "admitted only as a move destination the run's own plan records. A retirement is recorded only by a "
    "sealed retirement inventory in the shape the retirement-phase transaction derives from its own "
    "create ops: one row per move row of the plan, occupying or not, whose recorded Move destination lies "
    "beneath .working/archive/moved/, naming that destination at the moved source's plan digest and "
    "nothing else (the record of a plan without such a move row lists no files), no row naming a path the "
    "base inventory also claims and each listed destination holding its recorded bytes live; before a "
    "recorded retirement, a file at a plan Move destination beneath .working/archive/moved/ is a "
    "finding. The machine-local journal is "
    "never read, so a clone grades as the original store. The evidence check detects accidents: an "
    "interrupted apply, a hand edit that is non-canonical or that changes listed bytes, and a misplaced "
    "or stale record. It does not detect deliberate forgery: a hand edit that re-emits a canonical, "
    "self-consistent record is forgery and is not detected. Import and ingest "
    "records stay unregistered paths until their own activation, a vanished frozen source before its "
    "recorded retirement is a failure, and a migrate source stays bounded adoption state until the import "
    "that retires it is read.",
)


class StoreValidation:
    """The whole-store outcome (the U1 ManifestValidation idiom, one level up). `status` reduces to a
    0/1/2 exit code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE); CANNOT-EVALUATE dominates INVALID, which
    dominates VALID, so an unreadable input is never masked by an otherwise-clean read. `checks` is the
    ordered per-check verdict map; `triage` is the spec-11/14.2 partial-import surface; `evaluated_profiles`
    and `unevaluated_profiles` name the profile scope (spec 16); `residuals` are the disclosed by-design
    uncovered sub-checks, reported but never changing the status. `migration_incomplete` (additive) names
    each bounded adoption-state path reported rather than failed (spec 11/14.2); it never changes the status."""
    __slots__ = ("status", "findings", "cannot_evaluate", "residuals", "checks", "triage",
                 "evaluated_profiles", "unevaluated_profiles", "by_check", "unattributed",
                 "migration_incomplete")

    def __init__(self, status, findings=None, cannot_evaluate=None, residuals=None, checks=None,
                 triage=None, evaluated_profiles=None, unevaluated_profiles=None,
                 by_check=None, unattributed=None, migration_incomplete=None):
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
        self.migration_incomplete = migration_incomplete or []


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
                 "unattributed", "required", "migration_incomplete", "toml_reads")

    def __init__(self):
        self.findings = []
        self.cannot = []
        self.residuals = list(_RESIDUALS)
        self.checks = {}                  # insertion-ordered check-id -> "PASS" / "FINDING" / "CANNOT-EVALUATE"
        self.triage = []
        self._current = None
        self.by_check = {}                # check-id -> list of the source-attributed message strings it emitted
        self.unattributed = []            # no-current findings/cants + duplicate-ran / unknown-id internal faults
        self.required = REQUIRED_CHECKS   # the closed roster; homes 2 or an imported series widens it
        self.migration_incomplete = []    # bounded adoption-state paths: reported, never a status change
        self.toml_reads = {}              # relpath -> the raw bytes _read_toml read (the first read per path)

    def require_homes(self, homes, imported=False):
        # Bind the roster to the store's homes generation and imported-series activation once the manifest
        # names them. A homes-2 or imported check that then never runs is CANNOT-EVALUATE, and a legacy
        # report keeps exactly REQUIRED_CHECKS.
        self.required = required_checks(homes, imported)
        if homes >= 2:
            self.residuals.extend(_HOMES2_RESIDUALS)
        if imported:
            self.disclose_firewall()
            self.residuals.extend(_IMPORTED_RESIDUALS)

    def disclose_firewall(self):
        # The section 8.6 firewall residuals, once per report: on an active imported series, and on any
        # store whose clean records include an importer-authored one (the firewall is keyed on provenance).
        if _FIREWALL_RESIDUALS[0] not in self.residuals:
            self.residuals.extend(_FIREWALL_RESIDUALS)

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

    def migration_incomplete_path(self, msg):
        # A bounded adoption-state path (spec 11/14.2): reported rather than failed, so, like triage, it is
        # never attributed to a check verdict and never changes the status.
        self.migration_incomplete.append(msg)

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
                               by_check=self.by_check, unattributed=self.unattributed,
                               migration_incomplete=self.migration_incomplete)


# --- a lightweight record descriptor for the cross-record checks -------------------------------------

class _Rec:
    __slots__ = ("id", "rtype", "namespace", "state", "qual", "links", "actor_kind", "location", "scopes",
                 "body", "unparsed")

    def __init__(self, rid, rtype, namespace, state, qual, links, actor_kind, location, scopes, body=None,
                 unparsed=frozenset()):
        self.id = rid                 # the raw id value (a str for a well-formed record)
        self.rtype = rtype            # the record's type name
        self.namespace = namespace    # the two-letter namespace, or None when the id is malformed
        self.state = state            # the parsed status state (or "recorded" for a worklog entry)
        self.qual = qual              # the status qualifier ("proposed") or None
        self.links = links            # list of (rel, target-id) tuples with a string rel
        self.actor_kind = actor_kind  # the actor.kind when ACTOR_KINDS holds it, else None
        self.location = location      # "active", "archive/<YYYY>", or "imported" (an imported leaf row)
        self.scopes = scopes          # a block's scoped ids (list), or [] (spec 8.5)
        self.body = body              # the raw record table, read by the immutable-body preservation check
        self.unparsed = unparsed      # the fields ("status", "links", "scopes", "actor") that did not parse


def _make_rec(table, rtype, location):
    """Build a best-effort _Rec from a parsed record table. A malformed id yields namespace None (the
    cross-record checks skip it); the per-record schema faults are reported separately by validate_record.
    `unparsed` names each field a cross-record check reads that could not be interpreted: "status", the
    status of a type with a shipped schema that is absent, not a string or does not parse; "links", a
    links value that is not an array of tables, each with a rel in the closed vocabulary and a string
    id; "scopes", a block's scopes value that is not an array of strings; and "actor", an actor that is
    absent or not a table, or whose kind is absent, not a string, or a string the schema does not admit
    (ACTOR_KINDS; a lookalike such as 'Importer' or 'importer-legacy'); actor_kind is then None, so a
    lookalike never reads as the importer kind and never earns its exemption. A check over the readable
    fields of such a record cannot stand in for one over the record, so each check that reads the field
    grades the population as not fully read (fail-closed): the current-state joins over status, links and
    scopes, C-LINKS over links and scopes. The actor kind is the section 8.6 provenance, so an
    uninterpreted one makes C-RECEIPTS and C-DECISION-CHAINS CANNOT-EVALUATE over their types and stops
    the firewall arm that reads it (_firewall_partial); C-HANDOFF alone counts such a record, since an
    uninterpreted kind can only add to its at-most-one count. The per-record schema findings name the
    defect itself."""
    rid = table.get("id")
    shape = _valid_id_shape(rid)
    namespace = shape[0] if shape is not None else None
    spec = BASELINE_SPECS.get(rtype)
    unparsed = set()
    if rtype == "worklog":
        state, qual = "recorded", None            # the reduced envelope carries no status (spec 8.3)
    else:
        state = qual = None
        if spec is not None:
            status = table.get("status")
            parsed = None
            if isinstance(status, str):
                parsed, _err = parse_status(status, spec)
            if parsed is not None:
                state, qual = parsed
            else:
                unparsed.add("status")
    links = []
    lk = table.get("links")
    if isinstance(lk, list):
        for l in lk:
            if isinstance(l, dict) and isinstance(l.get("rel"), str):
                links.append((l.get("rel"), l.get("id")))
                if not isinstance(l.get("id"), str):
                    unparsed.add("links")
                elif l.get("rel") not in LINK_RELS:
                    unparsed.add("links")
            else:
                unparsed.add("links")
    elif lk is not None:
        unparsed.add("links")
    actor = table.get("actor")
    akind = actor.get("kind") if isinstance(actor, dict) else None
    if not isinstance(akind, str) or akind not in ACTOR_KINDS:   # a lookalike kind is unknown provenance
        akind = None
        unparsed.add("actor")
    scopes = table.get("scopes")
    if rtype == "block" and scopes is not None:
        if not isinstance(scopes, list):
            unparsed.add("scopes")
        elif not all(isinstance(s, str) for s in scopes):
            unparsed.add("scopes")
    scopes = list(scopes) if isinstance(scopes, list) else []
    return _Rec(rid, rtype, namespace, state, qual, links, akind, location, scopes, table, frozenset(unparsed))


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
    (None, "error"). Absence is (None, "absent") and left to the caller to grade. The bytes of a file read
    are kept on the report (the first read per path; the worklog ledger reads in _gather_worklog feed the
    same cache), so the canonical-bytes leg proves the bytes the checks graded and never reads them again
    (the manifest and each worklog ledger are read once per validation; store discovery reads the
    manifest separately, before it)."""
    try:
        found = _read_toml_contained(root_fd, relpath, with_raw=True)
    except StoreError as exc:
        rep.cant("cannot read {}: {}".format(relpath, exc))
        return None, "error"
    except _journal.JournalError as exc:
        # _read_toml_contained runs its lstat step OUTSIDE its StoreError wrapper, so a JournalError from
        # _check_rel (a '.'/'..'/control-char/trailing-slash relpath) or a non-ENOENT stat surfaces here
        # rather than escaping validate_store. Mirrors U7's _read_toml guard (guard-input-soundness; B5).
        rep.cant("cannot read {} ({})".format(relpath, exc))
        return None, "error"
    if found is None:
        return None, "absent"
    raw, data = found
    rep.toml_reads.setdefault(relpath, raw)
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
    never itself the store's verdict (fail-closed teardown).

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
                # fail-closed StoreError naming the directory, never a raw OSError escaping the
                # barrier; it routes through _list_dir to a named CANNOT-EVALUATE like the os.stat case.
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
            # quiet for an OSError; any other exception goes through the one first-interrupt selection,
            # so an interrupt in flight here keeps propagating as itself (_journal._yield_close_exceptions)
            listing_inflight = _journal._in_flight_in(sys._getframe())
            try:
                _close_fd_quietly(dfd)
            except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
                _journal._yield_close_exceptions(listing_inflight, [exc])
    finally:
        parent_inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_quietly(pfd)
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(parent_inflight, [exc])


def _list_dir(root_fd, reldir, rep, unread=None, types=()):
    """_list_contained with StoreError mapped to a CANNOT-EVALUATE naming the directory. A directory or
    entry name can carry control characters, so both the path and the error are rendered through
    _safe_display rather than interpolated raw, and no control character reaches the terminal. On that
    error the record `types` the directory holds join `unread` (when given): a listing that failed leaves
    their population incomplete."""
    try:
        return _list_contained(root_fd, reldir)
    except StoreError as exc:
        rep.cant("cannot list {}: {}".format(_safe_display(reldir), _safe_display(str(exc))))
        if unread is not None:
            unread.update(types)
        return None, None


def _record_digest(body):
    """The per-record digest `sha256:<hex>` over U8's canonical serialization of a record body (the digest
    the per-record registry row carries; OPF-SPEC 13:999-1001). Reuses `_opf_emit.emit`, which admits the
    full record subset INCLUDING a native TOML date/datetime/time under a registered `x-<vendor>` table
    (spec 8.7); U3's `_canonical` rejects those, so using it here over-fired to CANNOT-EVALUATE on a valid
    store (F11). Raises `_opf_emit.EmitError` on an out-of-subset body, which the caller maps to
    CANNOT-EVALUATE, never a silent mismatch."""
    return "sha256:" + hashlib.sha256(_opf_emit.emit(body).encode("utf-8")).hexdigest()


def _record_digest_matches(body, recorded):
    """True iff `recorded` is the per-record digest of `body` (_record_digest), or the digest of the same
    canonical serialization as an emitter before the U+2028/U+2029 escape spelled it (the escape of each
    written as the literal character, _opf_emit.prior_separator_spelling), so a digest recorded over a body
    holding one of those characters before that escape still reconciles. Either spelling is a function of
    the parsed body alone, so a changed body matches neither. Raises `_opf_emit.EmitError` as _record_digest
    does."""
    text = _opf_emit.emit(body)
    if recorded == "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest():
        return True
    prior = _opf_emit.prior_separator_spelling(text)
    return prior != text and recorded == "sha256:" + hashlib.sha256(prior.encode("utf-8")).hexdigest()


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


def _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, where, registered_vendors, rep, recon, schema_deferred=False,
                      unread=None):
    """Gather the per-record-layout records of one type: each registry row is `{id, state, path, digest}`,
    its body lives at `<type>/<id>.toml`, and the row must bind to the body (id, state, digest; OPF-SPEC
    9:839, 13). Record-body SCHEMA faults are emitted directly (C-RECORDS focus); the RECONCILIATION
    problems (row keyset, path canonicality, missing / orphan bodies, id/state binding, digest mismatch)
    are collected into `recon` for the caller to replay under C-PERRECORD-RECONCILE. Reconciliation is
    BIDIRECTIONAL: a registry row with no body file, and a body file with no registry row, are both
    findings. A body file with no registry row, or a type directory that cannot be listed, also puts
    `tname` in `unread` (when given), since a record the store carries was then not seated; the caller
    adds it for a registry row that is not seated."""
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
            matched = _record_digest_matches(body, row.get("digest"))
        except _opf_emit.EmitError as exc:
            recon.append(("cant", "{}: cannot compute the per-record digest ({})".format(body_rel, exc)))
            matched = None
        if matched is False:
            recon.append(("finding", "{}: per-record digest mismatch for {!r} (the index row and the record "
                          "file disagree; spec 13:999-1001)".format(rw, rid)))
        recs.append(_make_rec(body, tname, "active"))
    # Reverse direction: every record file under <type>/ must have a registry row.
    _subdirs, files = _list_dir(root_fd, _rel(machine_rel, tname), rep, unread, (tname,))
    if files is not None:
        for fn_ in files:
            if not fn_.endswith(".toml"):
                continue
            fid = fn_[:-len(".toml")]
            if fid not in seen_ids:
                recon.append(("finding", "{}/{}: record file {!r} has no registry row in {} (a record with "
                              "no index row; spec 13:999-1001)".format(tname, fn_, fid, where)))
                if unread is not None:
                    unread.add(tname)
    return recs


def _gather_active_records(root_fd, machine_rel, enabled_types, layout, registered_vendors, rep, unread=None):
    """Gather every active non-worklog record across the enabled type indexes. Returns (recs, recon) where
    `recon` carries the per-record-layout reconciliation problems for C-PERRECORD-RECONCILE. A declared
    enabled type with no `<type>.index.toml` is an INVALID finding (a declared input is absent; spec 11).
    Every type whose records could not all be seated (an index that is unreadable, malformed or absent, or
    a row or record file that is skipped) joins `unread` (when given), so a cross-record check over that
    type reports CANNOT-EVALUATE rather than a pass over a partial population."""
    if unread is None:
        unread = set()
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
            unread.add(tname)
            continue
        if st == "absent":
            if tname in IMPORTER_TYPES and tname not in enabled_types:
                # An importer type is accepted-if-present, never required: an absent index is not a finding
                # (a store with no import legitimately carries no legacy_fragment index).
                continue
            rep.finding("enabled type {!r} has no {}{} (a declared input is absent; spec 11)".format(
                tname, tname, INDEX_SUFFIX))
            unread.add(tname)
            continue
        rows = _index_rows(data, idx_rel, rep)
        if rows is None:
            unread.add(tname)
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
                seated = _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, idx_rel,
                                           registered_vendors, rep, recon, schema_deferred=True, unread=unread)
            else:
                # Inline: the index rows ARE the records, so there are no separate body files to reconcile;
                # defer the content schema and seat the ids best-effort so the id-space, no-deletion, and
                # counters-completeness checks still see them.
                if rows:
                    rep.cant("{}: {} schema-deferred record(s) of type {!r}; {}".format(
                        idx_rel, len(rows), tname, _SCHEMA_DEFERRAL))
                seated = [_make_rec(row, tname, "active") for row in rows if isinstance(row, dict)]
        elif layout == "per-record":
            seated = _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, idx_rel,
                                       registered_vendors, rep, recon, unread=unread)
        else:
            seated = _gather_inline(tname, rows, idx_rel, registered_vendors, rep)
        # Each gatherer seats at most one record per row, so a shorter result means a row was skipped.
        if len(seated) != len(rows):
            unread.add(tname)
        recs.extend(seated)
    return recs, recon


def _imported_leaf_rows(data, where, rows_key, rep):
    """Validate an imported leaf's top shape and return its rows, or None (CANNOT-EVALUATE). Both leaf
    shapes are closed {schema, <rows_key>} tables: `record` for a <type>.imported.index.toml, exactly the
    inline index shape _index_rows reads, and `entry` for worklog.imported.toml, the worklog ledger shape.
    A value that is not a table, an unsupported schema, or a rows value that is not an array is
    CANNOT-EVALUATE; an unknown top-level key or an absent `schema` is an INVALID finding."""
    if not isinstance(data, dict):
        rep.cant("{}: imported leaf is not a table".format(where))
        return None
    extra = set(data) - {"schema", rows_key}
    if extra:
        rep.finding("{}: unknown top-level key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
    if "schema" not in data:
        rep.finding("{}: imported leaf is missing the required `schema` key (a closed leaf declares its "
                    "schema; spec 4.2/13)".format(where))
    else:
        sch = data.get("schema")
        if type(sch) is not int or sch != SUPPORTED_SCHEMA:
            rep.cant("{}: schema {} is not the supported version {} (fail-closed; do not parse under v{} "
                     "assumptions)".format(where, _safe_display(sch), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))
            return None
    rows = data.get(rows_key, [])
    if not isinstance(rows, list):
        rep.cant("{}: [[{}]] is not an array of tables".format(where, rows_key))
        return None
    return rows


def _gather_imported_records(root_fd, machine_rel, enabled_types, registered_vendors, rep):
    """C-IMPORTED-SCHEMA (spec 8.3): read the imported-series leaves inline (the first imported-series
    release keeps them inline in either store layout, spec 4.2) and validate every row through
    _opf_schema.validate_imported_record, the closed imported envelope and its missingness rules. The
    leaves are one per enabled type except legacy_fragment, named through _opf_store.imported_leaf_names,
    the authority the containment registration reads: <type>.imported.index.toml with [[record]] rows,
    and worklog.imported.toml with [[entry]] rows for the worklog ledger. An imported row NEVER enters the
    _SCHEMA_DEFERRAL seam (spec 8.3: a conforming imported series can reach VALID), so a type whose clean
    records defer (a module-tier type) is graded by the imported validator, which refuses a type it holds
    no schema for. An absent leaf is an INVALID finding (spec 4.2: init and upgrade create every leaf, and
    a later-enabled type gains its leaf in the same act as its clean index); an unreadable or unparseable
    leaf, an unsupported schema, and a row the validator cannot identify are CANNOT-EVALUATE; an envelope
    or missingness defect is an INVALID finding. Returns (recs, complete): the _Rec list of the rows
    (location "imported") for the cross-series checks, and whether every imported row was seated.
    `complete` is False when a leaf is unreadable, absent or malformed, or a row could not be identified:
    the imported row set is then partial, so a cross-series resolution over it (C-LINKS) must not read
    as a pass (the row skipped here could carry, or be, a link or scope target)."""
    recs = []
    complete = True
    for tname in sorted(enabled_types):
        names = _opf_store.imported_leaf_names((tname,))
        if not names:
            continue                      # legacy_fragment: the imported series refuses LF (spec 8.1/8.2)
        (leaf,) = names
        leaf_rel = _rel(machine_rel, leaf)
        data, st = _read_toml(root_fd, leaf_rel, rep)
        if st == "error":
            complete = False
            continue
        if st == "absent":
            rep.finding("enabled type {!r} has no imported leaf {} (an imported-series store holds one for "
                        "every enabled type except legacy_fragment; spec 4.2)".format(tname, leaf))
            complete = False
            continue
        rows = _imported_leaf_rows(data, leaf_rel, "entry" if tname in _LEDGER_TYPES else "record", rep)
        if rows is None:
            complete = False
            continue
        for i, row in enumerate(rows):
            rw = "{}#{}".format(leaf_rel, i + 1)
            rv = validate_imported_record(row, expected_type=tname, registered_vendors=registered_vendors)
            if rv.status == CANNOT_EVALUATE:
                rep.cant("{}: {}".format(rw, "; ".join(rv.findings)))
                complete = False
                continue
            if rv.status != VALID:
                for f in rv.findings:
                    rep.finding("{}: {}".format(rw, f))
            recs.append(_make_rec(row, tname, "imported"))
    return recs, complete


def _worklog_legacy_conflict(_relpath):
    """C-CONTAINMENT grades the stray directory; keep inspecting the declared legacy source."""
    # Only load_worklog_at's generation-1 branch invokes this policy.
    return None


def _gather_worklog(root_fd, relpath, registered_vendors, rep, required, machine_rel=None,
                    *, propagate_manifest_failure=False, supported_profiles=None, manifest_model=None,
                    unread=None):
    """Read and validate a worklog.toml (active or an archive bucket) through U3's validate_worklog, and
    return its WL-number -> entry map. A required (active) worklog that is absent is CANNOT-EVALUATE; an
    archive-bucket worklog that is absent returns None (the caller only reads it when the bucket has one).
    The enclosing traversal receives manifest failures before diagnostic translation so it can
    attribute the original diagnostics and stop dependent archive reads; an ordinary ledger
    failure still permits archive diagnostics. An entry the map leaves out (not a table, no well-formed
    WL id, or a reused id) puts "worklog" in `unread` (when given), since its links are then never read;
    a caller that gets None back adds it the same way."""
    # The ledger bytes feed the report's read cache, so the canonical-bytes leg proves the exact bytes
    # graded here and never reads the file again (one content read per worklog path per run).
    def note_read(read_rel, raw):
        rep.toml_reads.setdefault(read_rel, raw)

    try:
        if machine_rel is None:       # explicitly named archive bucket, never shape-probed (M7)
            data = _opf_worklog.load_archive_worklog_at(root_fd, relpath, on_read=note_read)
        else:
            data = _opf_worklog.load_worklog_at(
                root_fd, machine_rel, required=False, on_legacy_conflict=_worklog_legacy_conflict,
                supported_profiles=supported_profiles, manifest_model=manifest_model, on_read=note_read)
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
    if idf and unread is not None:
        unread.add("worklog")      # each _entries_by_id finding is an entry left out of the map
    return by_id


# --- the archive (C-ARCHIVE-ENUM / C-ROTATION and the merged worklog) --------------------------------

def _parse_archive_manifest(data, where, rep, dropped=None, wl_dropped=None):
    """Parse and validate an `archive.toml` (shape defined here). Returns (moved, worklog_moved) where
    `moved` is a list of (id, type, destination) triples and `worklog_moved` a list of (span, destination),
    or (None, None) when the file is not a table, carries an unsupported/absent schema, or an array is
    malformed (each CANNOT-EVALUATE). A malformed individual row is an INVALID finding and is left out of
    the returned lists; when `dropped` is given, each such moved row is appended to it as its raw
    (id, type, destination), and when `wl_dropped` is given, each such worklog_moved row's raw destination
    is appended there, so the caller can grade the population the row names as not reconciled, against the
    declared destination where that is still a usable path."""
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
            if dropped is not None:
                dropped.append((mid, mtype, dest))
            continue
        if not isinstance(mtype, str) or not mtype:
            rep.finding("{}: type must be a non-empty string (spec 12)".format(rw))
            if dropped is not None:
                dropped.append((mid, mtype, dest))
            continue
        if not isinstance(dest, str) or not dest:
            rep.finding("{}: destination must be a non-empty string".format(rw))
            if dropped is not None:
                dropped.append((mid, mtype, dest))
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
            if wl_dropped is not None:
                wl_dropped.append(dest)
            continue
        if not isinstance(dest, str) or not dest:
            rep.finding("{}: destination must be a non-empty string".format(rw))
            if wl_dropped is not None:
                wl_dropped.append(dest)
            continue
        wl_moved.append((span, dest))
    return moved, wl_moved


def _moved_row_types(mid, mtype, enabled_types):
    """The enabled types whose record population a moved row that was not reconciled leaves incomplete:
    the type it declares and the type its id's namespace binds, each when it names an enabled type, so a
    missing, non-string or mislabelled type still reaches the record's own joins. When neither names
    one, the row could stand for any record, so every enabled type is returned."""
    types = set()
    if isinstance(mtype, str) and mtype in enabled_types:
        types.add(mtype)
    shape = _valid_id_shape(mid)
    if shape is not None:
        types.update(t for t, ns in enabled_types.items() if ns == shape[0])
    if not types:
        types.update(enabled_types)
    return types


def _dropped_dest_key(dest):
    """How a dropped row's raw declared destination classifies the population the row names. A row the
    manifest parse drops for a malformed id, type or span still carries the field that says WHERE the
    record (or the entries) went, and discarding it can turn a record genuinely held there into a false
    absence. Returns the canonical contained form when `dest` is a usable contained path (the row is then
    classified exactly as a kept wrong-destination row, against the counted set once the whole tree is
    walked), False when it is a non-empty string that is not contained (never opened, so it can never be
    a counted location: the record may exist exactly there, and its types belong in `unread`), and None
    when the row carries no usable destination string at all (it names no location, so its types belong
    in `unconfirmed` alone)."""
    if not isinstance(dest, str) or not dest:
        return None
    if not _is_contained_relpath(dest):
        return False
    return _canonical_contained(dest)


def _dest_index_has_id(root_fd, machine_rel, dest, target_id, rep):
    """True when the store-relative index `dest` (an `archive.toml` moved destination the caller has
    already required to be a contained path equal to the declaring bucket's own type index, probed only
    where that bucket's scan counted nothing at the path) actually carries a row for `target_id`; False
    when it was read, or is absent, and carries none; None when it could not be read. An absent
    destination records a finding and returns False (an absent file holds no row); an unreadable or
    malformed one is fail-closed to None, so an enumerated id whose destination cannot be confirmed is
    never read as present. The caller tells the returns apart: a destination the probe confirmed absent
    (or read whole) without the row leaves its type unconfirmed, one never read leaves a record source
    unread, and a probe HIT is a record the walk counted nowhere (the tree changed between the listing
    and the probe), likewise a record source unread, never a seat."""
    full = _rel(machine_rel, dest)
    data, st = _read_toml(root_fd, full, rep)
    if st == "error":
        return None
    if st == "absent":
        rep.finding("C-ARCHIVE-ENUM: archive destination {!r} does not exist".format(dest))
        return False
    rows = _index_rows(data, full, rep)
    if rows is None:
        return None
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


def _archive_triaged_file(archive_rel, path):
    """Whether _validate_archive routes the file `path` under `archive_rel` through _archive_unmanaged: a
    file directly under archive/, any file beneath a bucket subdirectory, or a bucket file that is neither
    archive.toml, worklog.toml nor a <type>.index.toml. Every other archive file (a bucket's manifest,
    worklog or index, whatever the bucket's name) is never only triaged there: it is graded, or named in a
    finding or a CANNOT-EVALUATE, at any import status."""
    parts = path[len(archive_rel) + 1:].split("/")
    if len(parts) != 2:
        return True
    return parts[1] not in (ARCHIVE_MANIFEST_NAME, WORKLOG_NAME) and not parts[1].endswith(INDEX_SUFFIX)


def _archive_triaged_dir(archive_rel, path):
    """Whether _validate_archive grades the directory `path` under `archive_rel` as one path through
    _archive_unmanaged without listing it: a subdirectory of a bucket, or any directory beneath one. The
    archive root and each bucket directly under it are listed there, so neither is."""
    return path != archive_rel and len(path[len(archive_rel) + 1:].split("/")) >= 2


def _validate_archive(root_fd, machine_rel, enabled_types, registered_vendors, import_status, rep, homes=1,
                      unread=None, unconfirmed=None, active_counted=None):
    """Walk the archive tree, validating each bucket's archive.toml, its archived non-worklog records, and
    its archived worklog, and reconciling the enumeration BIDIRECTIONALLY against what is actually present
    (OPF-SPEC 12/13). Enumeration and record-schema faults attribute to the caller's C-ARCHIVE-ENUM focus;
    the rotation-eligibility (F4) findings are collected into a returned list for C-ROTATION. Returns
    (archive_recs, archive_worklogs, rotatable) where archive_worklogs maps a year to its WL-number -> entry
    map. Archive absent means no rotation (clean); a present bucket with no archive.toml is CANNOT-EVALUATE,
    never 'no rotation happened'. No loop is sized by a declared span endpoint. The walk sorts every type
    it cannot vouch for into `unread` or `unconfirmed` (each when given; `unconfirmed` falls back to
    `unread`) by ONE principle: a moved row's types stay in the graded `unconfirmed` set only when its
    declared destination is a location that was opened at that path and read in full by a scan that
    counts its ids toward a type's population, so the walk knows definitively whether the id is there.
    The walk records those locations as it goes (each bucket's enabled type indexes and its worklog, plus
    the active ones `active_counted` supplies) and classifies each row only after the whole tree is
    walked, since a counted location can be a bucket walked later; the declaring bucket's own expected
    location, settled by the time its rows are reached, is additionally probed where the bucket's listing
    counted nothing at the path, a destination the probe confirms absent being likewise known to hold no
    row. Every declared location outside the counted set (never opened, an uncontained path, one whose
    ids were not counted toward a record population - a worklog named by a moved row, an index whose
    declared type the bucket scan skips, or a probe hit where the listing counted nothing - or one that
    could not be read in full) puts the types _moved_row_types names into `unread` instead: the record
    may exist exactly where nothing was counted, so only the `unread` types leave an id's presence
    unknown and the presence scans over the ids that were read stand for every other type. A declared
    destination is compared with the counted set through its canonical contained form
    (_canonical_contained), so a non-canonical spelling of a counted location still matches it. A row
    dropped as malformed keeps its declared destination where that is still a usable string
    (_dropped_dest_key): a contained one is classified exactly as a kept wrong-destination row, an
    uncontained one puts its types in `unread`, and only a row with no usable destination string names
    no readable location, so it adds the types it could stand for to `unconfirmed` alone, never to
    `unread`: wherever every location a record of such a type can live was counted, the id's presence is
    still known (a location that failed to read already put its type in `unread` itself), so one junk
    field never turns a namespace's presence gates into CANNOT-EVALUATE. Beyond the rows, `unread` also takes the type of an archived index that is
    unreadable or malformed or whose row is skipped, every enabled type for a bucket or archive tree that
    cannot be walked, and "worklog" when the bucket's worklog.toml is listed but cannot be read or an
    entry is left out of its map. `unconfirmed` also takes the types of a row whose declared type
    disagrees with its id's namespace even where the counted destination carries the id: the id's
    presence is known, but which of the two types the record is cannot be told, so both typed populations
    stay incomplete for the current-state joins. The archived worklog follows the same principle: a
    worklog_moved span that is malformed (one dropped whole included, through its kept destination) or
    enumerates an entry the bucket's worklog lacks adds "worklog" to `unconfirmed` when its declared
    destination is a counted ledger (one read in full, or one a listed bucket does not carry, which
    holds no entries), and to `unread` when it names any other destination the entries could be held
    at. `active_counted` (when given) maps each active location the caller's
    collectors read in full to the ids counted there, keyed store-relative exactly as an archive.toml
    destination names it (`<type>.index.toml`, `worklog.toml`); only membership is read for a ledger.
    Without it no active location is counted, so a destination naming one stays in `unread`."""
    if unread is None:
        unread = set()
    if unconfirmed is None:
        unconfirmed = unread
    # The locations whose ids a full read counted toward a population: `counted_ids` maps each counted
    # type index to the well-formed ids it seats, and `counted_wl` holds each counted worklog ledger.
    # Both are keyed store-relative, exactly as a destination names them; a declared destination is
    # graded against the matching set only, since an index's ids are never counted as worklog entries nor
    # a ledger's as records.
    counted_ids = {}
    counted_wl = set()
    for loc in (active_counted or {}):
        if loc == WORKLOG_NAME or loc.endswith("/" + WORKLOG_NAME):
            counted_wl.add(loc)
        else:
            counted_ids[loc] = active_counted[loc]
    pending_moved = []      # (types the row could stand for, declared destination), classified after the walk
    pending_wl = []         # declared worklog_moved destinations, classified after the walk
    archive_recs = []
    archive_worklogs = {}
    rotatable = []
    # codex-2: a partial import triages archive stray paths only when SUBSTANTIATED by an active run; a
    # stale partial with no running import grades them as findings (never a triage pass that disables
    # grading), matching C-CONTAINMENT.
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep, homes)
    archive_rel = _rel(machine_rel, ARCHIVE_DIRNAME)
    years, top_files = _list_dir(root_fd, archive_rel, rep, unread, enabled_types)
    if years is None:
        # An ABSENT archive/ tree means no rotation has happened (clean). A tree that EXISTS but cannot
        # be listed takes the same return, with the cant and every enabled type already recorded unread
        # by _list_dir above, so nothing downstream reads the unwalked tree as rotation-free.
        return archive_recs, archive_worklogs, rotatable
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
            unread.update(enabled_types)
            continue
        for d in (sub or []):
            # A subdirectory inside a bucket is unmanaged: a bucket holds only files (archive.toml,
            # worklog.toml, <type>.index.toml), and its contents are never listed, so the whole subtree is
            # graded as one unregistered path (spec 12/14.2; B2).
            _archive_unmanaged(rep, partial_active, _rel(bucket, d))
        if ARCHIVE_MANIFEST_NAME not in bfiles:
            rep.cant("archive bucket {} has no {} (cannot evaluate the rotation; spec 12)".format(
                bucket, ARCHIVE_MANIFEST_NAME))
            unread.update(enabled_types)
            continue
        amf, ast = _read_toml(root_fd, _rel(bucket, ARCHIVE_MANIFEST_NAME), rep)
        if ast != "ok":
            unread.update(enabled_types)
            continue
        dropped, wl_dropped = [], []
        moved, wl_moved = _parse_archive_manifest(amf, bucket, rep, dropped, wl_dropped)
        if moved is None:
            unread.update(enabled_types)
            continue
        for mid, mtype, dest in dropped:
            # A row dropped as malformed still carries the field that says where the record went. Where
            # that destination is a usable contained path, the row is classified exactly as a kept
            # wrong-destination row (against the counted set once the whole tree is walked), so a record
            # genuinely held at the declared location never takes an absence finding; an uncontained
            # string names a location that is never opened, where the record may exist (`unread`). Only
            # a row with no usable destination string names no readable location: each type it could
            # stand for stays graded wherever every location a record of that type can live was read in
            # full and counted (a location that failed to read already put its type in `unread` itself),
            # while the rotation it names is unreconciled, so those types' populations are incomplete
            # for the current-state joins.
            key = _dropped_dest_key(dest)
            row_types = _moved_row_types(mid, mtype, enabled_types)
            if key is None:
                unconfirmed.update(row_types)
            elif key is False:
                unread.update(row_types)
            else:
                pending_moved.append((row_types, key))
        for dest in wl_dropped:
            # A worklog_moved row dropped whole follows the same rule over the counted ledgers.
            key = _dropped_dest_key(dest)
            if key is None:
                unconfirmed.add("worklog")
            elif key is False:
                unread.add("worklog")
            else:
                pending_wl.append(key)
        if moved and not wl_moved:
            # Per-bucket rotation binding: a bucket that rotates non-worklog records MUST carry an archived
            # worklog span to bind them to a released period (spec 12). archive_wl_ids aggregates GLOBALLY,
            # so the caller's released-only check can be satisfied by ANOTHER bucket's span; the binding is
            # therefore enforced here, per bucket.
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
                unread.add(tname)
                continue
            rows = _index_rows(data, idx_rel, rep)
            if rows is None:
                unread.add(tname)
                continue
            deferred = _schema_deferred(tname)
            if deferred and rows:
                # F4/round-5 F-2: an archived record whose schema has not shipped in the baseline validator
                # (module-tier, or importer legacy_fragment) is deferred to a named CANNOT-EVALUATE rather
                # than the baseline validator's false "not a supported type".
                rep.cant("{}: {} archived schema-deferred record(s) of type {!r}; {}".format(
                    idx_rel, len(rows), tname, _SCHEMA_DEFERRAL))
            idx_ids, idx_counted = set(), True
            for i, row in enumerate(rows):
                rw = "{}#{}".format(idx_rel, i + 1)
                if not isinstance(row, dict):
                    rep.cant("{}: archived record row is not a table".format(rw))
                    unread.add(tname)
                    idx_counted = False
                    continue
                if not deferred:
                    rv = validate_record(row, expected_type=tname, registered_vendors=registered_vendors)
                    if rv.status == CANNOT_EVALUATE:
                        rep.cant("{}: {}".format(rw, "; ".join(rv.findings)))
                        unread.add(tname)
                        idx_counted = False
                        continue
                    if rv.status != VALID:
                        for f in rv.findings:
                            rep.finding("{}: {}".format(rw, f))
                rec = _make_rec(row, tname, "archive/{}".format(year))
                archive_recs.append(rec)
                if isinstance(rec.id, str) and _valid_id_shape(rec.id) is not None:
                    present_types[rec.id] = tname
                    idx_ids.add(rec.id)
                    elig = _archived_rotatable(rec)
                    if elig is None:
                        pass     # module-tier: rotation eligibility deferred to U2M (F4), never graded here
                    elif not elig:
                        rotatable.append("C-ROTATION: archived record {!r} ({}) is not in an unqualified "
                                         "terminal state and must never rotate (spec 12:972-979)".format(
                                             rec.id, tname))
            if idx_counted:
                # Read in full, every row's id counted toward the type's population: a moved destination
                # naming this index is graded against the ids counted here.
                counted_ids[_rel(bucket_rel, fn_)] = idx_ids
        # Archived worklog (release-based rotation).
        wl_map = {}
        if WORKLOG_NAME in bfiles:
            wl_track = set()
            wl_map = _gather_worklog(root_fd, _rel(bucket, WORKLOG_NAME), registered_vendors, rep,
                                     required=False, unread=wl_track)
            unread.update(wl_track)
            if wl_map is None:
                # A listed bucket worklog that is unreadable, invalid or gone at read: its entries were
                # not read.
                unread.add("worklog")
                wl_map = {}
            elif not wl_track:
                counted_wl.add(_rel(bucket_rel, WORKLOG_NAME))
        else:
            # A listed bucket with no worklog.toml: the location holds no entries, so a span naming it is
            # classified over a counted (empty) ledger.
            counted_wl.add(_rel(bucket_rel, WORKLOG_NAME))
        archive_worklogs[year] = wl_map

        # F5 / F12 non-worklog reconciliation, BOTH directions.
        for mid, mtype, dest in moved:
            shape = _valid_id_shape(mid)
            id_ns = shape[0] if shape is not None else None
            norm_ns = _ROSTER_NAMESPACES.get(mtype)
            # Every type the row could stand for: the declared one and the one its id's namespace binds.
            row_types = _moved_row_types(mid, mtype, enabled_types)
            mislabelled = False
            if norm_ns is None:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} declares type {!r}, which is not a roster type "
                            "(spec 8.1)".format(mid, mtype))
            elif id_ns is not None and id_ns != norm_ns:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} declares type {!r} (namespace {}) but the id "
                            "namespace is {} (spec 8.1)".format(mid, mtype, norm_ns, id_ns))
                mislabelled = True
            want_base = "{}{}".format(mtype, INDEX_SUFFIX)
            expected_dest = _rel(bucket_rel, want_base)
            # The row's types stay in the graded `unconfirmed` set ONLY when the declared destination is a
            # location a scan read in full while counting its ids toward a type's population (or one the
            # probe below confirms holds no row), so the walk knows definitively whether the id is there.
            # Every other declared location (never opened, opened but its ids not counted, or not readable
            # in full) puts the types in `unread` instead: the record may exist exactly where nothing was
            # counted, so a graded presence scan over the ids that were read could report a false deletion.
            if not _is_contained_relpath(dest):
                # An uncontained destination, including one that compares equal to an expected path built
                # from a declared type carrying path segments, is refused before any path is built from
                # it: it is never opened and can never be a counted location.
                rep.finding("C-ARCHIVE-ENUM: archive destination {!r} is not a contained store-relative "
                            "path (spec 14.2); never opened".format(dest))
                unread.update(row_types)
            elif dest != expected_dest:
                # A rotation lands in its DECLARING bucket's own type index, never in the active tree or
                # another bucket (spec 12), so the wrong destination is a finding either way; whether the
                # row stays graded depends on whether some scan counted that location, which can be a
                # bucket walked later, so the row is classified after the whole walk, through its
                # canonical contained form: the counted set is keyed canonical, and a non-canonical
                # spelling of a counted location must match it rather than read as never counted.
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} destination {!r} is not this bucket's type index "
                            "{!r} (a rotation lands in its declaring bucket; spec 12)".format(
                                mid, dest, expected_dest))
                pending_moved.append((row_types, _canonical_contained(dest)))
            elif dest in counted_ids:
                # This bucket's scan read the declared destination in full and counted its ids, so the
                # id's presence there is known without a second read.
                if mid not in counted_ids[dest]:
                    rep.finding("C-ARCHIVE-ENUM: enumerated moved id {!r} is absent from its destination "
                                "{!r} (a rotation must land where it says; spec 12:981-982)".format(
                                    mid, dest))
                    unconfirmed.update(row_types)
                elif mislabelled:
                    # The counted destination carries the id, but under a type its id's namespace does not
                    # bind: the id's presence is known, while which of the two types the record is cannot
                    # be told, so both typed populations stay incomplete for the current-state joins.
                    unconfirmed.update(row_types)
            else:
                seated = _dest_index_has_id(root_fd, machine_rel, dest, mid, rep)
                if seated is None:
                    # The destination could not be read at the probe: whether the id is held there is
                    # unread, never a graded absence, so the row's state is worded as such.
                    rep.cant("C-ARCHIVE-ENUM: enumerated moved id {!r} destination {!r} could not be read "
                             "at the probe; whether the id is held there is unread (spec "
                             "12:981-982)".format(mid, dest))
                elif not seated:
                    rep.finding("C-ARCHIVE-ENUM: enumerated moved id {!r} is absent from its destination {!r} "
                                "(a rotation must land where it says; spec 12:981-982)".format(mid, dest))
                if mtype not in enabled_types or seated is not False:
                    # Nothing counted this location's ids: a declared type outside the enabled roster
                    # (whose index the bucket scan skips even when present), a destination that cannot be
                    # read, or a probe HIT at a path the listing counted nothing at (the tree changed
                    # under the walk, so the record the probe saw was counted nowhere). The id's presence
                    # stays unknown.
                    unread.update(row_types)
                else:
                    # The probe confirmed the destination absent (or read it whole) without the row where
                    # the scan counted nothing: the id is known to be held nowhere counted, so the
                    # presence scans stay graded while the type's population is incomplete for the
                    # current-state joins.
                    unconfirmed.update(row_types)
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
                # Never opened, so it can never match a counted ledger; the raw spelling classifies it.
                dest_key = dest
            else:
                # The counted ledgers are keyed canonical, so the lookup and every pending entry read
                # the canonical contained form; a non-canonical spelling of a counted ledger matches it.
                dest_key = _canonical_contained(dest)
                if dest != expected_wl_dest:
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
                # The entries it names cannot be reconciled. The moved-row principle decides the set,
                # against the counted ledgers once the whole walk is done: a span landing in a counted
                # ledger names entries only read (or absent) locations could hold, so the worklog stays
                # graded; one landing anywhere else names entries that may be held at a destination this
                # walk never opens.
                pending_wl.append(dest_key)
                continue
            spans.append((a, b, dest, dest_key))
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
        for a, b, dest, dest_key in spans:
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
                # The store says those entries were rotated here, yet the bucket's worklog does not hold
                # them. The moved-row principle decides the set, against the counted ledgers once the
                # whole walk is done: a span landing in a counted ledger had its declared destination read
                # in full (or holds no entries), so an entry absent from every counted ledger is absent
                # and the worklog stays graded; one landing anywhere else names a destination this walk
                # never opens, where the entries may still be held.
                pending_wl.append(dest_key)
    # Classified only now, after the whole walk: a declared destination can be a location a later bucket's
    # scan counted (or an active one the caller's collectors did). Each contained pending entry is already
    # in its canonical form, as the counted sets are keyed. A declared location in the counted set
    # leaves the id's (or the entries') presence known, so the presence scans stay graded while the
    # unreconciled rotation keeps the row's populations incomplete for the current-state joins; any other
    # declared location was counted by nothing, so the record may exist exactly where nothing was read.
    for row_types, dest in pending_moved:
        if dest in counted_ids:
            unconfirmed.update(row_types)
        else:
            unread.update(row_types)
    for dest in pending_wl:
        if dest in counted_wl:
            unconfirmed.add("worklog")
        else:
            unread.add("worklog")
    return archive_recs, archive_worklogs, rotatable


# --- the cross-record checks -------------------------------------------------------------------------

def _check_receipts(recs, by_id, rep, unread=frozenset()):
    """C-RECEIPTS (R1): the done<->backlog_item receipt is one-to-one (OPF-SPEC 8.5). The section 8.6
    firewall applies here as in every current-state join, keyed on provenance alone (the maintainer
    ruling: OPF has never been released, so no store predates the firewall): a record whose `actor.kind`
    is exactly `importer`, and an imported-series row, neither owes a receipt nor confers one. Only the
    exact kind is exempt. A backlog_item or done whose actor kind is absent, unreadable or a kind the
    schema does not admit, a lookalike such as 'Importer' included (_make_rec's `unparsed` "actor"), is
    not known to be importer-authored or not, so the join is then CANNOT-EVALUATE naming those types,
    never a pass; it still runs, reading each such record as not importer-authored, and a finding it
    raises over them stands beside the CANNOT-EVALUATE. The clean receipts are therefore the clean dones
    not authored by an importer, and the join grades three rules over them. At most one:
    every clean backlog_item ratified at unqualified `done`, importer-authored ones included, has at most
    one clean receipt, so two clean receipts naming one ratified importer-authored item are a finding; an
    importer-authored done is never counted, so two of them on an importer-authored item, or one beside a
    clean receipt, raise no count finding. At least one: every non-importer backlog_item ratified at
    unqualified `done` has a clean receipt; an importer-authored ratified item is owed none, and an
    importer-authored done never satisfies a non-importer item. The target: a clean receipt's receipt_of
    resolves among every clean backlog_item, importer-authored ones included, to one ratified at `done`,
    so a clean receipt naming an importer-authored item not ratified at `done` is a finding, as for a
    receipt naming any other unratified item. A non-importer item whose every receipt is importer-authored
    is not reported here: the driver runs _check_importer_receipts beside this join on every store, and
    that arm reports it, or accepts in its place the remedy spec 8.6 defines (a maintainer-authored
    `corrects` decision), so the one defect is reported once and the remedy is never refused. When the
    backlog_item or done records could not all be read or confirmed, or a seated one's status or links
    could not be interpreted (`unread` from the collectors, widened by _make_rec's `unparsed`), the join is
    CANNOT-EVALUATE and is not run: a missing or unreadable receipt or target could hide a defect or report
    a false one."""
    clean = [r for r in recs if r.location != "imported"]
    no_kind = {r.rtype for r in clean if "actor" in r.unparsed} & {"backlog_item", "done"}
    if no_kind:
        rep.cant("C-RECEIPTS: {} (see C-RECORDS), so whether each is importer-authored (spec 8.6) is not "
                 "known; the receipt join is not evaluated as a pass, and a finding it raises reads each such "
                 "record as not importer-authored (spec 8.5)".format(
                     _partial_causes(frozenset(), frozenset(), "", no_kind)))
    unread = unread | {r.rtype for r in clean if r.unparsed - {"actor"}}
    if unread & {"backlog_item", "done"}:
        rep.cant("C-RECEIPTS: the backlog_item and done records could not all be read, confirmed or "
                 "interpreted (see C-RECORDS, C-PERRECORD-RECONCILE and C-ARCHIVE-ENUM); the receipt join is "
                 "not evaluated over a partial set (spec 8.5)")
        return
    joined = [r for r in recs if r.location != "imported" and r.actor_kind != "importer"]
    bi_state = {r.id: (r.state, r.qual) for r in joined if r.rtype == "backlog_item" and isinstance(r.id, str)}
    # The receipt targets: every clean backlog_item, importer-authored ones included. Where an
    # importer-authored record shares a non-importer item's id, the non-importer item's state stands, so
    # an importer-authored duplicate never makes a clean item read as ratified.
    target_state = {r.id: (r.state, r.qual) for r in recs
                    if r.location != "imported" and r.rtype == "backlog_item" and isinstance(r.id, str)}
    target_state.update(bi_state)
    # The items _check_importer_receipts grades: every receipt_of naming them comes from an importer-authored
    # clean done, read exactly as that arm reads its receipts. An imported-series row is skipped here as in
    # the join: it confers no receipt, so it never hands an item to that arm or keeps one from it.
    receipt_kinds = {}
    for r in clean:
        if r.rtype == "done":
            for rel, tid in r.links:
                if rel == "receipt_of" and isinstance(tid, str):
                    receipt_kinds.setdefault(tid, []).append(r.actor_kind)
    arm_graded = {tid for tid, kinds in receipt_kinds.items() if all(k == "importer" for k in kinds)}
    done_targets = {}
    for r in joined:
        if r.rtype == "done" and isinstance(r.id, str):
            done_targets[r.id] = [tid for rel, tid in r.links if rel == "receipt_of" and isinstance(tid, str)]
    bi_to_dones = {}
    for did, targets in done_targets.items():
        for tid in targets:
            bi_to_dones.setdefault(tid, []).append(did)
    # The at-most-one limit counts the clean non-importer receipts naming every ratified target,
    # importer-authored items included; only a non-importer item is owed a receipt.
    for bid, (state, qual) in target_state.items():
        if state == "done" and qual is None:
            n = len(bi_to_dones.get(bid, ()))
            if n == 0 and bid in bi_state and bid not in arm_graded:
                rep.finding("C-RECEIPTS: ratified backlog_item {!r} (at 'done') has no done receipt (the "
                            "receipt is one-to-one; spec 8.5)".format(bid))
            elif n > 1:
                rep.finding("C-RECEIPTS: backlog_item {!r} has {} done receipts; the receipt is one-to-one "
                            "(spec 8.5)".format(bid, n))
    for did, targets in done_targets.items():
        for tid in targets:
            st = target_state.get(tid)
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


def _check_handoff(recs, rep, unread=frozenset()):
    """C-HANDOFF (R2): at most one `current` handoff among clean records authored by non-importer actors
    (OPF-SPEC 8.5). Section 8.6 keys the importer firewall "on provenance rather than on series alone": it
    "covers every record whose `actor.kind` is `importer`, in the imported series or written into the
    clean series by the pre-1.3.0 legacy importer", and "every current-state join, including the section
    8.5 at-most-one-current handoff rule, MUST evaluate only clean records authored by non-importer
    actors". OPF has never been released, so no store predates that firewall, and it applies on every
    store: this check applies the exemption keyed on provenance alone, in agreement with the record
    writer's current-handoff join (_opf_record._current_handoffs) and the HANDOFF view
    (_opf_views.render_handoff), which apply it the same way. An imported-series row never counts either
    (_validate_opened_store builds the graded record list from the active, archive and worklog records and
    never adds an imported-series row; the skip matches that routing for a direct caller). Only the exact
    kind `importer` is exempt: a record whose actor kind cannot be read, or is a lookalike (another case,
    a prefix or an extension) the schema does not admit, reads as None and counts, fail-closed
    (validate_record already names the unknown or unreadable actor), never a silent pass; its "actor"
    entry in _make_rec's `unparsed` is therefore not a cause here, since counting it can only add to the
    count. When the handoff records could not all be read or confirmed (`unread`, from the collectors), or
    a seated handoff's status or links could not be interpreted (_make_rec's `unparsed`: a record read as 'not
    current' only because its status is unreadable is a partial count, never a pass), the check is
    CANNOT-EVALUATE; a count above one is still reported, since a record not read or not interpreted can
    only add to it."""
    unread = unread | {r.rtype for r in recs if r.location != "imported" and r.unparsed - {"actor"}}
    current = [r.id for r in recs if r.rtype == "handoff" and r.state == "current" and r.qual is None
               and r.location != "imported"
               and r.actor_kind != "importer"]
    if len(current) > 1:
        rep.finding("C-HANDOFF: {} clean handoffs not authored by a known importer are 'current'; at "
                    "most one current handoff exists among clean records not authored by a known "
                    "importer (spec 8.5/8.6)".format(len(current)))
    if "handoff" in unread:
        rep.cant("C-HANDOFF: the handoff records could not all be read, confirmed or interpreted (see "
                 "C-RECORDS, C-PERRECORD-RECONCILE and C-ARCHIVE-ENUM); "
                 "the at-most-one-current count is not evaluated over a partial set (spec 8.5)")


def decision_chains(recs, by_id, firewall=True):
    """The pending_decision supersession chains THAT HAVE A RESOLUTION (OPF-SPEC 8.5), as (members, current)
    pairs in record order. A chain is the connected component section 8.8 defines: every clean
    pending_decision is seated, importer-authored ones included, and every `supersedes` link joins its two
    members, whatever its direction and whoever authored it. A current effective resolution is a decided
    (unqualified) pending_decision that no other supersedes. A chain with no decided member has no
    resolution yet and is not listed. The section 8.6 firewall applies here as in every current-state
    join, keyed on provenance alone (the maintainer ruling: OPF has never been released, so no store
    predates the firewall): an importer-authored pending_decision is never a current effective resolution,
    and its `supersedes` links remove no current authority, so a non-importer decision it supersedes stays
    current, while a non-importer decision superseding it is the section 8.6 remedy. A maintainer PD-1
    that an importer-authored PD-2 supersedes is therefore its chain's one resolution, and a maintainer
    PD-3 that then supersedes PD-2 is a second one in the same chain (spec 8.5 allows one). A chain whose
    only decided members are importer-authored has no resolution here and is not listed; the firewall arm
    (_check_importer_chains) reports it. Only the exact kind `importer` is exempt; a record whose actor
    kind is absent, unreadable or a lookalike the schema does not admit (None) is read as not
    importer-authored. Its provenance is not known, so the doctor never passes a chain over it:
    C-DECISION-CHAINS is then CANNOT-EVALUATE naming it, and the firewall arm does not run. The one
    derivation shared by C-DECISION-CHAINS and opf record's pre-publication chain check (spec 8.8), so the
    verb never judges a chain by a rule of its own and both apply the firewall the same way (the verb
    derives over a record of unreadable or lookalike kind as this does). `firewall` False derives the same
    chains with an importer-authored decision resolving and superseding as any other does, for the one
    reader that must see where an importer-authored decision stands as the current resolution
    (_check_importer_chains)."""
    pds = [r for r in recs if r.rtype == "pending_decision" and isinstance(r.id, str)
           and r.location != "imported"]
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
                if not firewall or r.actor_kind != "importer":
                    superseded.add(tid)
    components = {}
    for pid in parent:
        components.setdefault(find(pid), []).append(pid)
    chains = []
    for _root, members in components.items():
        decided = [m for m in members if by_id.get(m) is not None
                   and by_id[m].state == "decided" and by_id[m].qual is None
                   and (not firewall or by_id[m].actor_kind != "importer")]
        if not decided:
            continue     # a chain with no decided member has no resolution yet: zero current is legitimate
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


def _check_decision_chains(recs, by_id, rep, unread=frozenset()):
    """C-DECISION-CHAINS (R3): exactly one current effective resolution per pending_decision supersession
    chain THAT HAS A RESOLUTION (OPF-SPEC 8.5), over the chains decision_chains derives. For a chain
    carrying at least one decided member, the count of current resolutions must be exactly one, in BOTH
    directions: zero (the sole resolution superseded by a withdrawn or still-open successor) is a fail-open
    the count-only-over-one form missed (F14). A wholly-undecided chain legitimately has zero and is not
    flagged. The section 8.6 provenance firewall lives in decision_chains itself, so the writer's
    pre-publication chain check applies it identically. When the pending_decision records could not all
    be read or confirmed (`unread`, from the collectors), or a seated one's status or links could not be
    interpreted (_make_rec's `unparsed`), the check is CANNOT-EVALUATE and is not run: a member not read or
    not interpreted can split, join or resolve a chain. A pending_decision whose actor kind is absent,
    unreadable or a lookalike the schema does not admit (`unparsed` "actor") is not known to be
    importer-authored or not, so the check is then CANNOT-EVALUATE naming it, never a pass; it still runs,
    decision_chains reading such a record as not importer-authored, and a finding it raises over them
    stands beside the CANNOT-EVALUATE."""
    clean = [r for r in recs if r.location != "imported"]
    no_kind = {r.rtype for r in clean if "actor" in r.unparsed} & {"pending_decision"}
    if no_kind:
        rep.cant("C-DECISION-CHAINS: {} (see C-RECORDS), so whether each is importer-authored (spec 8.6) is "
                 "not known; the chains are not evaluated as a pass, and a finding they raise reads each such "
                 "record as not importer-authored (spec 8.5)".format(
                     _partial_causes(frozenset(), frozenset(), "", no_kind)))
    unread = unread | {r.rtype for r in clean if r.unparsed - {"actor"}}
    if "pending_decision" in unread:
        rep.cant("C-DECISION-CHAINS: the pending_decision records could not all be read, confirmed or "
                 "interpreted (see C-RECORDS, C-PERRECORD-RECONCILE and C-ARCHIVE-ENUM); the chains are not "
                 "evaluated over a partial set (spec 8.5)")
        return
    for _members, current in decision_chains(recs, by_id):
        if len(current) != 1:
            rep.finding("C-DECISION-CHAINS: a pending_decision supersession chain with a decided resolution "
                        "has {} current effective resolutions; exactly one exists per chain (spec 8.5)".format(
                            len(current)))


def _check_links(recs, by_id, all_ids, rep, imported_recs, imported_targets, unread=frozenset(),
                 imported_complete=True):
    """C-LINKS (R4): every links relation target resolves to an existing record, with the closed type
    constraints (OPF-SPEC 8.6). `follows` and `relates` relate records to records with a closed vocabulary,
    so a dangling `follows`/`relates` is a finding too ("advisory" constrains a link's EFFECT on state, not
    its right to resolve; F13). receipt_of targets a backlog_item; supersedes targets the same type; the
    rest need only an existing target. Every block's `scopes` id is resolved against the same union
    (spec 8.5: a block scopes enumerated record IDs; an unresolvable scope corrupts the actionability
    join). Links and scopes resolve over the union of both series (spec 8.6): `imported_recs` are the
    imported rows C-IMPORTED-SCHEMA read (none when no imported series is read), an `imported:<NS>-<n>`
    target resolves against them, and the links and block `scopes` of those rows are resolved too, so a
    dangling link or scope from either series is a finding. An imported row's link to a clean id is
    refused by C-IMPORTED-SCHEMA and is not resolved here. The imported rows join this resolution only;
    every other cross-record check reads clean records alone. A link or scope resolves only to one of
    `imported_targets`, the rows of `imported_recs` that the per-series id grading also reads as members
    of their own leaf series (spec 8.2), so a row C-ID-SPACE refuses as outside its series (a clean id, or
    another type's namespace, in an imported leaf) never makes a link or scope resolve. This check reads
    every record of BOTH series as a link source and as a target, so its source set is complete only when
    every collection was: when any record type could not be read or confirmed in full (`unread`, from the
    collectors), the imported-row collection skipped an unreadable, absent or malformed leaf or an
    unidentifiable row (`imported_complete` False), or a record of either series carries a links or scopes
    value that could not be interpreted (_make_rec's `unparsed`), the check is CANNOT-EVALUATE and is not
    run, and its message names every one of those causes that holds. A target could lie in the unread
    population (a false dangling finding) and an unread or uninterpreted record's own links or scopes could
    dangle unseen (a false pass), so neither direction is evaluated over a partial set."""
    link_fields = {"links", "scopes"}
    uninterpreted = {r.rtype for r in recs if r.unparsed & link_fields}
    imported_uninterpreted = any(r.unparsed & link_fields for r in imported_recs)
    if unread or uninterpreted or not imported_complete or imported_uninterpreted:
        causes = []
        if unread:
            causes.append("the records of {} could not all be read or confirmed".format(
                ", ".join(repr(t) for t in sorted(unread))))
        if uninterpreted:
            causes.append("the links or scopes of {} records could not be interpreted".format(
                ", ".join(repr(t) for t in sorted(uninterpreted))))
        if not imported_complete:
            causes.append("the imported series could not all be read")
        if imported_uninterpreted:
            causes.append("the links or scopes of imported-series records could not be interpreted")
        rep.cant("C-LINKS: {} (see C-RECORDS, C-PERRECORD-RECONCILE, C-ARCHIVE-ENUM and C-IMPORTED-SCHEMA); "
                 "link and scope resolution is not evaluated over a partial set (spec 8.5/8.6)".format(
                     "; ".join(causes)))
        return
    imported_by_id = {}
    for r in imported_targets:
        if isinstance(r.id, str):
            imported_by_id.setdefault(r.id, r)
    for r in list(recs) + list(imported_recs):
        for rel, tid in r.links:
            if rel not in LINK_RELS or not isinstance(tid, str):
                continue
            imported_target = tid.startswith(IMPORTED_PREFIX)
            if r.location == "imported" and not imported_target:
                continue
            if imported_target:
                target = imported_by_id.get(tid)
                if target is None:
                    rep.finding("C-LINKS: {} {} link -> {!r} resolves to no record in either series (a "
                                "dangling link over the union of both series; spec 8.6)".format(r.id, rel, tid))
                    continue
            elif tid not in all_ids:
                rep.finding("C-LINKS: {} {} link -> {!r} resolves to no record in the store (a dangling "
                            "link; spec 8.6)".format(r.id, rel, tid))
                continue
            else:
                target = by_id.get(tid)
            if target is None:
                continue
            if rel == "receipt_of" and target.rtype != "backlog_item":
                rep.finding("C-LINKS: {} receipt_of -> {!r} is a {}, not a backlog_item (spec 8.1/8.5)".format(
                    r.id, tid, target.rtype))
            elif rel == "supersedes" and target.rtype != r.rtype:
                rep.finding("C-LINKS: {} (type {!r}) supersedes -> {!r} of type {!r}; supersession links "
                            "the same type (spec 8.6)".format(r.id, r.rtype, tid, target.rtype))
    for r in list(recs) + list(imported_recs):
        if r.rtype == "block":
            for sid in r.scopes:
                if isinstance(sid, str) and sid not in all_ids and sid not in imported_by_id:
                    rep.finding("C-LINKS: block {} scopes {!r} which resolves to no record in either "
                                "series (spec 8.5)".format(r.id, sid))


# A run id suffix in every journal kind run grammar: with the prefix of a kind it names a placeholder run, so
# the reservation home of each kind is derived from the one path authority (_opf_store.allocation_record).
_RESERVATION_PLACEHOLDER = "-00000000T000000Z-0000000000000000"


def _reservation_run(kind, name):
    """The run id that a file name in the reservation home of kind binds (the run id plus .toml), or None
    when the name is not one (_opf_store.allocation_record is the one grammar authority)."""
    if not name.endswith(".toml"):
        return None
    try:
        _opf_store.allocation_record(kind, name[:-len(".toml")])
    except ValueError:
        return None
    return name[:-len(".toml")]


class _PrefixedReport:
    """Forwards a contained reader's CANNOT-EVALUATE to the report under one check's message prefix, so a
    read failure the reader words itself still names the check it fails."""
    __slots__ = ("_rep", "_prefix")

    def __init__(self, rep, prefix):
        self._rep = rep
        self._prefix = prefix

    def cant(self, msg):
        self._rep.cant(self._prefix + msg)


class _TeeReport:
    """Forwards a finding or CANNOT-EVALUATE to the report unchanged, under whichever check is current, and
    keeps a copy in `emitted`, so a second check that the spec gives the same obligation (C-IMPORTED-IDS
    over the imported series' own id grading, spec 8.2/8.3) replays exactly what the ONE grading emitted
    rather than grading it again. Its toml_reads is the report's own read cache, so a TOML read through
    the tee (_read_toml) feeds that cache exactly as a direct read does, never failing on a missing one."""
    __slots__ = ("_rep", "emitted")

    def __init__(self, rep):
        self._rep = rep
        self.emitted = []

    @property
    def toml_reads(self):
        return self._rep.toml_reads

    def finding(self, msg):
        self._rep.finding(msg)
        self.emitted.append(("finding", msg))

    def cant(self, msg):
        self._rep.cant(msg)
        self.emitted.append(("cant", msg))


def _reserved_numbers(root_fd, rep):
    """Homes 2: the numbers the id reservations hold, per series key as {series key: set of numbers}, and
    whether every reservation evaluated. C-NO-DELETION and C-CONTIGUITY skip such a number only where no
    present record carries it and the prior committed snapshot held no record with that id
    (_split_reserved), so this reads each reservation read-only and for that purpose alone. Spec 8.2 at
    2.0.0 defines the reservation, the next-ID derivation and this grading (the doctor treats a reserved
    number as issued), and spec 4.2 names this read-only reservation read as the one exception to its
    rule that doctor excludes journals. The grading does not reach the release: no span can cover a
    reserved worklog number that no entry carries (spec 6.1), so the release cut refuses a tail with one
    below its highest entry however doctor grades it. The homes are the allocations/ home under journals/
    of every kind in _opf_store.JOURNAL_KINDS (read at call time), each derived through
    _opf_store.allocation_record, listed and read through the contained no-follow readers; no journal
    frame, projection or run directory is opened. A reservation is judged as the record writer judges
    one: its file name binds a run id of its own kind, and its bytes equal the exact canonical payload
    (_opf_journal.reservation_payload) that its name and content declare. A staging leftover of a
    reservation publication (a run that died before its exclusive link) carries no reservation and is
    skipped unread. Owner uniqueness holds for the number each reservation records (its run's high-water
    per key): that (series key, number) has one owner across every kind's home, so a second reservation
    recording it is a conflict naming both files. A residual: a run that claimed several numbers under one
    key records only the highest, so another run recording one of the lower numbers overlaps it unseen
    (this doctor cannot read a number no reservation records). An underivable or unlistable home, an entry
    that is not a reservation, an unreadable, absent-at-read, malformed or non-canonical reservation, and
    a conflict are each a CANNOT-EVALUATE under C-NO-DELETION naming it, and the result is then
    ({}, False), so the caller grades as though nothing were reserved, never a wider pass. A [claims]
    value is the number its run reserved under that key; a run that claimed several numbers under one key
    records only its new high-water, so if it never published, its lower numbers still read as missing
    (conservative). An imported-series key reserves on its own imported number line (spec 8.2: the next-ID
    derivation applies per series key), so its numbers are returned under the quoted key: the imported
    deletion and contiguity checks read them where the series is active, and any other key is simply never
    looked up."""
    # Deferred: _opf_journal imports _opf_oplock, which imports this module.
    import _opf_journal
    import _opf_oplock
    owners = {}      # (series key, number) -> the reservation that claims it
    ok = True
    read_rep = _PrefixedReport(rep, "C-NO-DELETION: ")
    for kind in _opf_store.JOURNAL_KINDS:
        try:
            home = _opf_store.allocation_record(
                kind, _opf_store._HOME_RUN_PREFIXES[kind] + _RESERVATION_PLACEHOLDER).rsplit("/", 1)[0]
        except (KeyError, ValueError) as exc:
            rep.cant("C-NO-DELETION: the journal kind {} has no reservation home this build can derive ({}); "
                     "the reserved numbers are unknown (fail-closed)".format(
                         _safe_display(kind), _safe_display(str(exc))))
            ok = False
            continue
        try:
            subdirs, files = _list_contained(root_fd, home)
        except StoreError as exc:
            rep.cant("C-NO-DELETION: cannot list the id reservations in {}: {} (fail-closed)".format(
                home, _safe_display(str(exc))))
            ok = False
            continue
        for entry in subdirs or ():
            rep.cant("C-NO-DELETION: {} is a directory, not an id reservation (fail-closed)".format(
                _safe_display(_rel(home, entry))))
            ok = False
        for entry in files or ():
            head, marker, _tail = entry.rpartition(_opf_oplock._STAGING_MARKER)
            if (marker and entry.startswith(".") and _opf_oplock._is_staging_name(entry, head[1:])
                    and _reservation_run(kind, head[1:]) is not None):
                continue     # a publication that died before its link left no reservation; never read
            rel = _rel(home, entry)
            run_id = _reservation_run(kind, entry)
            if run_id is None:
                rep.cant("C-NO-DELETION: {} is not an id reservation of a {} run, nor a staging leftover of "
                         "one (fail-closed)".format(_safe_display(rel), kind))
                ok = False
                continue
            raw, st = _read_bytes(root_fd, rel, read_rep)
            if st == "absent":
                rep.cant("C-NO-DELETION: the id reservation {} was listed but is absent at read "
                         "(fail-closed)".format(rel))
            if st != "ok":
                ok = False
                continue
            try:
                model = tomllib.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
                # ValueError: tomllib.TOMLDecodeError and an over-long integer literal; RecursionError and
                # MemoryError: deep nesting and a many-part key.
                rep.cant("C-NO-DELETION: the id reservation {} is malformed ({}) (fail-closed)".format(
                    rel, _safe_display(str(exc))))
                ok = False
                continue
            claims = model.get("claims")
            try:
                expected = _opf_journal.reservation_payload(kind, run_id, model.get("operation_id"), claims)
            except (_journal.JournalError, _opf_emit.EmitError) as exc:
                rep.cant("C-NO-DELETION: the id reservation {} is malformed ({}) (fail-closed)".format(
                    rel, _safe_display(str(exc))))
                ok = False
                continue
            if raw != expected:
                rep.cant("C-NO-DELETION: the id reservation {} differs from the exact canonical reservation "
                         "its name and content declare (a reservation is never rewritten, spec 8.2; "
                         "fail-closed)".format(rel))
                ok = False
                continue
            for key, number in claims.items():
                owner = owners.setdefault((key, number), rel)
                if owner != rel:
                    rep.cant("C-NO-DELETION: the id reservations {} and {} both claim {} under the series key "
                             "{} (a reserved number has one owner; fail-closed)".format(owner, rel, number, key))
                    ok = False
    if not ok:
        return {}, False
    reserved = {}
    for key, number in owners:
        reserved.setdefault(key, set()).add(number)
    return reserved, True


def _prior_holdings(obs):
    """What the prior committed snapshot (HEAD) held, for the reserved-number exemption: ((the (namespace,
    number) of each of its type-index ids, its `held` {namespace: sorted disjoint (lo, hi) intervals}),
    None), or (None, the reason) when no prior that enumerates every id it held was observed. The
    type-index records alone cannot decide it: they carry neither the worklog nor the archive."""
    prior = obs.get("prior")
    if prior is None:
        return None, ("the prior committed snapshot observation was supplied but malformed"
                      if obs.get("prior_malformed") else "no prior committed snapshot was supplied")
    if prior.get("held") is None:
        return None, ("the prior committed snapshot does not enumerate every id it held (its worklog and "
                      "archive included)")
    ids = {shape for shape in map(_valid_id_shape, prior["records"]) if shape is not None}
    return (ids, prior["held"]), None


def _prior_held(holdings, ns, n):
    """Whether the prior committed snapshot held a record with the id <ns>-<n> (an interval lookup, never
    a range expansion; F15)."""
    ids, held = holdings
    if (ns, n) in ids:
        return True
    spans = held.get(ns, ())
    i = bisect.bisect_right(spans, n, key=lambda span: span[0])
    return i > 0 and n <= spans[i - 1][1]


def _imported_prior_holdings(obs):
    """What the prior committed snapshot held on the imported number lines, for the reserved-number
    exemption: ((the (series key, number) of every id its imported baseline enumerates, {}), None), or
    (None, the reason) when no usable baseline was observed. Imported records never rotate (spec 12), so
    the baseline enumerates every imported id the prior held, and the empty interval table lets
    _prior_held and _split_reserved grade an imported key exactly as a clean namespace. A prior whose
    imported member is absent or malformed decides nothing: the exemption is then CANNOT-EVALUATE
    (_report_undecided), never granted."""
    prior = obs.get("prior")
    if prior is None:
        return None, ("the prior committed snapshot observation was supplied but malformed"
                      if obs.get("prior_malformed") else "no prior committed snapshot was supplied")
    if prior["imported_malformed"]:
        return None, "the prior committed snapshot observation was supplied but malformed"
    if prior["imported"] is None:
        return None, "the prior committed snapshot carries no imported-series baseline"
    ids = set()
    for rid in prior["imported"]:
        shape = parse_series_id(rid)
        ids.add((shape[0], shape[2]))
    return (ids, {}), None


def _split_reserved(ns, reserved, present, holdings):
    """Of the numbers the id reservations hold under ns that no present record carries, (issued,
    undecided). Issued: the prior committed snapshot held no record with that id, so it is an orphaned
    reservation's number, never published there, and is not owed. Undecided: no usable prior was
    observed, so whether a record was published and deleted cannot be established. A number the prior
    held is in neither: a reservation is irrevocable and outlives the publication it preceded, so a
    record the prior held and the store now lacks is graded missing exactly as without reservations."""
    issued, undecided = set(), set()
    for n in reserved:
        if n in present:
            continue
        if holdings is None:
            undecided.add(n)
        elif not _prior_held(holdings, ns, n):
            issued.add(n)
    return issued, undecided


def _report_undecided(check_id, ns, numbers, reason, rep):
    """One CANNOT-EVALUATE naming how many reserved numbers no usable prior can decide, and the first."""
    if numbers:
        rep.cant("{}: {} reserved {} number(s) that no present record carries, the first {}-{}, cannot be told "
                 "from deleted records: {}; the reserved-number exemption is not granted (fail-closed)".format(
                     check_id, len(numbers), ns, ns, min(numbers), reason))


def _unreserved(expected, present, skipped):
    """The first number from expected up to the present number that is not skipped (an issued or undecided
    reserved number, _split_reserved). It steps over skipped numbers only, so the scan is sized by the
    reservations actually read, never by a gap (F15)."""
    while expected < present and expected in skipped:
        expected += 1
    return expected


def _unreserved_overhang(high, max_present, skipped):
    """How many allocated numbers in (max_present, high] are not skipped (_split_reserved): the
    allocated-but-absent count above the highest present number, counted over the reservations read,
    never by expanding the range (F15)."""
    return high - max_present - sum(1 for n in skipped if max_present < n <= high)


def _check_worklog_contiguous(numbers, rep, skipped=frozenset()):
    """C-CONTIGUITY: read across active + archive the worklog tiles from WL-1 with no gap (OPF-SPEC 6.1),
    so the released spans map to real coverage. A pairwise scan over the sorted present numbers finds the
    first gap; nothing is sized by a declared high-water (F15). A skipped worklog number (an orphaned
    reservation's, or one no usable prior can decide, which the caller reports; _split_reserved) is not
    reported missing: the scan steps over skipped numbers only. That grades the store, not the release: no
    span can cover a skipped number, so the release cut, which reads no reservation, still refuses a tail
    with one below its highest entry (disclosed in _HOMES2_RESIDUALS)."""
    nums = sorted(n for n in numbers if isinstance(n, int) and not isinstance(n, bool))
    if not nums:
        return
    if _unreserved(1, nums[0], skipped) != nums[0]:
        rep.finding("C-CONTIGUITY: the worklog does not start at WL-1 (first present is WL-{}; span tiling "
                    "starts at WL-1, spec 6.1)".format(nums[0]))
    for i in range(1, len(nums)):
        missing = _unreserved(nums[i - 1] + 1, nums[i], skipped)
        if missing != nums[i]:
            rep.finding("C-CONTIGUITY: the worklog (active + archive) is not contiguous; WL-{} is missing "
                        "(a gap breaks span tiling, spec 6.1/11)".format(missing))
            break


def _imported_counter_namespaces(enabled_types):
    """The namespaces whose imported counter row the doctor requires once the imported series is active
    (spec 8.2): one per enabled type except legacy_fragment, i.e. every enabled normative namespace the
    imported series may carry (IMPORTED_NAMESPACES refuses LF, the reserved TX and the unassigned CL)."""
    return frozenset(ns for ns in enabled_types.values() if ns in IMPORTED_NAMESPACES)


def _gather_imported_rows(root_fd, machine_rel, enabled_types, rep, strays=None):
    """Read the imported-series leaves for the per-series id checks (spec 8.2/8.3). Each enabled type except
    legacy_fragment keeps its imported rows inline in the machine store in either layout (spec 4.2): the
    [[record]] rows of <type>.imported.index.toml, and the [[entry]] rows of worklog.imported.toml.
    Returns (rows, unread): `rows` holds (counter_key, id, row) for every row whose id lies in its own leaf
    series, imported:<NS>-<n> over the leaf type namespace, in leaf then row order; `unread` holds the
    counter keys whose leaf could not be read (a CANNOT-EVALUATE is already recorded here, and every later
    per-series check that skips such a key discloses its own), so no later check
    reads an unread leaf as an empty one. An ABSENT leaf contributes no rows, and every id its counter
    allocated is still owed (C-NO-DELETION). A row outside its leaf series (a clean id, another namespace,
    a non-string or malformed id) is a finding here, the series grammar; the rest of the imported envelope
    is C-IMPORTED-SCHEMA work, never graded here. `strays`, when supplied, collects (label, row) for each
    such row, so C-IMPORTED-PROVENANCE still re-verifies the original a row outside its series names."""
    rows, unread = [], set()
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
                if strays is not None:
                    strays.append(("{} {}[{}]".format(rel, member, i), row))
                continue
            rows.append((key, rid, row))
    return rows, unread


def _check_imported_worklog_contiguous(numbers, rep, skipped=frozenset()):
    """C-CONTIGUITY on the imported:WL number line, graded apart from the clean worklog (spec 8.2: contiguity
    evaluates each series independently): the imported worklog tiles from imported:WL-1 with no gap. The two
    lines never merge, so an imported gap never breaks clean release tiling and an imported entry never fills
    a clean gap (clean release spans tile only the clean WL line, spec 8.2). A pairwise scan over the sorted
    distinct present numbers, never sized by a high-water (F15); a duplicate is the C-ID-SPACE finding. A
    skipped number (an orphaned imported:WL reservation's, or one no usable baseline can decide, which the
    caller reports; _split_reserved) is not reported missing, exactly as on the clean worklog line."""
    nums = sorted(set(numbers))
    if not nums:
        return
    if _unreserved(1, nums[0], skipped) != nums[0]:
        rep.finding("C-CONTIGUITY: the imported worklog does not start at imported:WL-1 (first present is "
                    "imported:WL-{}; each series tiles its own number line, spec 8.2)".format(nums[0]))
    for i in range(1, len(nums)):
        missing = _unreserved(nums[i - 1] + 1, nums[i], skipped)
        if missing != nums[i]:
            rep.finding("C-CONTIGUITY: the imported worklog is not contiguous; imported:WL-{} is missing (the "
                        "imported line is graded apart from clean release tiling, spec 8.2)".format(missing))
            break


def _check_imported_no_deletion(rows, unread, high, floor, namespaces, reserved, holdings, no_holdings, rep):
    """C-NO-DELETION over the imported series (spec 8.2/8.3): for each required imported counter row, the
    present imported ids must be exactly floor+1 .. high-water on that row own number line, apart from the
    clean one. Imported records never rotate (spec 12), so the imported leaves are their only location. The
    same O(present) arithmetic as the clean scan: an overhang COUNT above the max present id, then the first
    gap below it, never a range() expansion of the high-water (F15). A number an id reservation holds under
    the key is skipped exactly as on the clean lines (spec 8.2: recovery treats a reserved number as issued),
    only where no present row carries it and the prior imported baseline (`holdings`,
    _imported_prior_holdings) held no record with that id (_split_reserved); one no usable baseline can
    decide is a named CANNOT-EVALUATE (_report_undecided), never granted. A key whose leaf is unread is a
    named CANNOT-EVALUATE for this check, never read as a deletion and never a silent per-series pass; a key
    whose counter is absent or malformed (the C-COUNTERS finding) is skipped here."""
    present = {}
    for key, rid, _row in rows:
        present.setdefault(key, set()).add(parse_series_id(rid)[2])
    for key in sorted(IMPORTED_PREFIX + ns for ns in namespaces):
        if key in unread:
            rep.cant("C-NO-DELETION: the {} imported leaf could not be read; whether its allocated ids "
                     "are all present is not evaluated (spec 8.2/8.3)".format(key))
            continue
        hw = high.get(key)
        if not (isinstance(hw, int) and not isinstance(hw, bool) and hw >= 0):
            continue
        base = floor.get(key, 0)
        nums = sorted(n for n in present.get(key, ()) if n > base)
        max_present = nums[-1] if nums else base
        issued, undecided = _split_reserved(key, reserved.get(key, ()), present.get(key, ()), holdings)
        skipped = issued | undecided
        owed = _unreserved_overhang(hw, max_present, skipped)
        if owed > 0:
            rep.finding("C-NO-DELETION: {} allocated {}-<n> id(s) above {}-{} are absent from the imported "
                        "leaves (counters {} high-water is {}; imported records are immutable after "
                        "publication, spec 8.2/8.3)".format(owed, key, key, max_present, key, hw))
        _report_undecided("C-NO-DELETION", key, [n for n in undecided if base < n <= hw], no_holdings, rep)
        expected = base + 1
        for n in nums:
            expected = _unreserved(expected, n, skipped)
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
    silent pass; an unread leaf makes its series a named CANNOT-EVALUATE for this check too (its prior ids
    are skipped rather than read as deleted, and the skip is disclosed, never a silent per-series pass)."""
    if prior_imported is None:
        rep.cant("C-HISTORY-APPEND-ONLY: the prior committed snapshot carries no imported-series baseline; "
                 "imported immutability across time (spec 8.3) is not evaluated")
        return
    for key in sorted(unread):
        rep.cant("C-HISTORY-APPEND-ONLY: the {} imported leaf could not be read; imported immutability "
                 "across time (spec 8.3) is not evaluated for that series".format(key))
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


def _check_imported_ids(emitted, counters_data, cst, namespaces, rep):
    """C-IMPORTED-IDS (spec 8.3: it "checks the series grammar, counters and no-deletion"). The imported
    series' own id grading runs ONCE, per series as spec 8.2 requires, inside C-ID-SPACE (the series
    grammar through _gather_imported_rows, uniqueness, and each id within its own "imported:<NS>"
    high-water) and C-NO-DELETION (_check_imported_no_deletion); `emitted` is what that one grading
    emitted, captured by _TeeReport, and is replayed here under this check's id, so the two attributions
    cannot disagree. A leaf that grading could not read is replayed as its CANNOT-EVALUATE. The imported
    counter rows are graded here from the one counters.toml read C-COUNTERS made (`counters_data`,
    `cst`), through validate_counters over the quoted "imported:<NS>" rows alone: one per enabled type
    except legacy_fragment (`namespaces`), each a genuine high-water (spec 8.2). An absent counters.toml
    owes every row (a finding); an unreadable one, or one whose [counters] is not a table under the
    supported schema, is CANNOT-EVALUATE, never graded under assumptions it does not declare."""
    for kind, msg in emitted:
        if kind == "cant":
            rep.cant("C-IMPORTED-IDS: " + msg)
        else:
            rep.finding("C-IMPORTED-IDS: " + msg)
    if cst == "error":
        rep.cant("C-IMPORTED-IDS: {} could not be read; the imported counter rows (spec 8.2) are not "
                 "evaluated".format(COUNTERS_NAME))
        return
    if cst == "absent":
        rep.finding("C-IMPORTED-IDS: {} is absent; the imported series requires a counter row for each of "
                    "{} (spec 8.2)".format(COUNTERS_NAME, ", ".join(sorted(IMPORTED_PREFIX + ns
                                                                          for ns in namespaces))))
        return
    counters = counters_data.get("counters", {}) if isinstance(counters_data, dict) else None
    schema = counters_data.get("schema") if isinstance(counters_data, dict) else None
    if not isinstance(counters, dict) or type(schema) is not int or schema != SUPPORTED_SCHEMA:
        rep.cant("C-IMPORTED-IDS: {} carries no [counters] table under the supported schema {}, so the "
                 "imported counter rows (spec 8.2) are not evaluated (see C-COUNTERS)".format(
                     COUNTERS_NAME, SUPPORTED_SCHEMA))
        return
    rows = {k: v for k, v in counters.items() if isinstance(k, str) and k.startswith(IMPORTED_PREFIX)}
    _high, findings = validate_counters({"schema": SUPPORTED_SCHEMA, "counters": rows},
                                        known_namespaces=frozenset(), imported_namespaces=namespaces)
    for f in findings:
        rep.finding("C-IMPORTED-IDS: {}: {}".format(COUNTERS_NAME, f))


def _is_preserved_original(source, run):
    """Whether `source` names a preserved original of the import run `run`: a canonical store-relative
    file path (_opf_store._home_file: no empty, `.` or `..` component, no absolute, drive or backslash
    form, no control character) under .working/imported/import/<run>/originals/ (spec 8.3, 8.8). The
    same binding validate_imported_record grades as a finding; C-IMPORTED-PROVENANCE reads only a path
    that passes it, so no other store file is ever read as an original."""
    if type(source) is not str or not _is_import_run_id(run):
        return False
    try:
        _opf_store._home_file(source)
    except ValueError:
        return False
    return source.startswith(_opf_store.evidence_run("import", run) + "/originals/")


def _check_imported_provenance(root_fd, rows, strays, unread, rep):
    """C-IMPORTED-PROVENANCE (spec 8.3: it "re-reads each preserved original and verifies
    `source_sha256`", and "Missing or unreadable evidence MUST fail closed"). Every imported row the
    per-series reader returned (_gather_imported_rows: `rows` in its own leaf series, `strays` outside
    it) names its preserved original in import.source (_is_preserved_original). Each original is read
    once, through the contained no-follow byte reader, and its SHA-256 is compared with the recorded
    import.source_sha256 of every row naming it: a mismatch is a finding, and so is an absent original,
    as a missing listed evidence file is under C-EVIDENCE-ENUM (spec 4.2). An unread leaf, a row that is
    not a table, a row whose provenance names no preserved original of its own run or carries no
    64-hex source_sha256 (the C-IMPORTED-SCHEMA finding), and an original the reader refuses (a
    non-regular entry, a symlink, an over-cap file) are each CANNOT-EVALUATE, never a pass over evidence
    nothing read."""
    prefix = "C-IMPORTED-PROVENANCE: "
    for key in sorted(unread):
        rep.cant(prefix + "the {} imported leaf could not be read; the preserved originals its records "
                 "name are not re-verified (spec 8.3)".format(key))
    read = {}
    for label, row in [("{!r}".format(rid), row) for _key, rid, row in rows] + list(strays):
        if not isinstance(row, dict):
            rep.cant(prefix + "imported row {} is not a table, so the original it names is not re-verified "
                     "(spec 8.3)".format(label))
            continue
        imp = row.get("import")
        imp = imp if isinstance(imp, dict) else {}
        source, digest = imp.get("source"), imp.get("source_sha256")
        if not (_is_preserved_original(source, imp.get("run"))
                and isinstance(digest, str) and _HEX64_RE.fullmatch(digest)):
            rep.cant(prefix + "imported record {} names no preserved original of its own import run with a "
                     "64-hex source_sha256 (see C-IMPORTED-SCHEMA), so its provenance is not verified "
                     "(spec 8.3/8.8)".format(label))
            continue
        if source not in read:
            raw, st = _read_bytes(root_fd, source, _PrefixedReport(rep, prefix))
            read[source] = (st, hashlib.sha256(raw).hexdigest() if st == "ok" else None)
        st, actual = read[source]
        if st == "absent":
            rep.finding(prefix + "imported record {} names the preserved original {}, which is absent "
                        "(missing evidence fails closed; spec 8.3)".format(label, _safe_display(source)))
        elif st == "ok" and actual != digest:
            rep.finding(prefix + "imported record {}: the preserved original {} does not hash to its "
                        "import.source_sha256 {} (spec 8.3)".format(label, _safe_display(source), digest))


# The relations a clean record may use to an imported record (spec 8.6: "Clean-to-imported links MUST be
# limited to `relates`, `derives_from`, `follows`, and same-type `supersedes`").
_CLEAN_TO_IMPORTED_RELS = frozenset({"relates", "derives_from", "follows", "supersedes"})


def _check_imported_segregation(recs, by_id, unevaluated, rep, unread=frozenset()):
    """C-IMPORTED-SEGREGATION (spec 8.3: it "enforces section 8.6"; spec 8.6: it "enforces this firewall
    over importer-authored records in both series"), graded on a store whose imported series is active.
    It grades the section 8.6 rules a parse-only doctor decides from the clean record set `recs` (active,
    archived, and worklog entries) alone:
      - clean-to-imported links: a clean record's link to an imported:<NS>-<n> id uses `relates`,
        `derives_from`, `follows`, or `supersedes` to the same type. Namespaces map one-to-one to types
        within each series (spec 8.2), so the type is judged by namespace and the target need not resolve
        here; resolution over the union of both series (spec 8.6: "a dangling cross-series link MUST be a
        finding") is C-LINKS work. A clean `done` record's `receipt_of` to an imported id falls
        outside the permitted set, so it is refused here too ("A clean `done` record's `receipt_of` MUST
        target a clean backlog item"). The targets of these links exist only in the imported leaves, so
        this arm runs only with the imported series active;
      - the receipt firewall (_check_importer_receipts) and the decision-chain arm
        (_check_importer_chains), keyed on provenance. The firewall covers every importer-authored record,
        whether or not the imported series is active (spec 8.6), so on a store whose series is inactive,
        where this check is not on the roster (spec 4.2), the driver grades those two arms under C-RECEIPTS
        and C-DECISION-CHAINS, the joins they guard, exactly as C-HANDOFF's join excludes importer-authored
        handoffs on every store.
    The imported-to-clean link refusal is per-record (validate_imported_record, under
    C-IMPORTED-SCHEMA), and an imported row never joins `recs`, so no imported record enters a clean
    join here; the link rule and both arms skip one a direct caller passes, so an imported row's own
    links are never graded as a clean record's. The section 8.6 obligations this engine is known not to
    grade (blocks granting a stop, actionability, supersession discharge outside a pending_decision
    chain, the module-tier current-state joins, field-borne authority, approval, and extension or
    adoption-approval authority) are named in _FIREWALL_RESIDUALS and _IMPORTED_RESIDUALS, so a PASS
    never reads as full section 8.6 coverage.
    The check never passes over a partial population (spec 8.6: missing or unreadable evidence fails
    closed); each of these causes is a CANNOT-EVALUATE naming it:
      - `unevaluated` names the clean-record readers (C-RECORDS, C-PERRECORD-RECONCILE, C-ARCHIVE-ENUM)
        that did not evaluate; the records they could not seat are not graded, and the rules still run
        over the records in `recs` unless the next cause stops one. That one message covers both
        provenance-keyed arms, so they are handed no `unevaluated` of their own here;
      - `unread` names the record types the collectors could not read in full, including where a reader
        reports only a finding (a record left unseated, a moved row its destination does not confirm),
        and _make_rec's `unparsed` names the records whose status, links, scopes or actor kind could not
        be interpreted. The link rule reads every clean record's links, so any unread type, or any record
        whose links could not be interpreted, makes it CANNOT-EVALUATE; it still grades the links it
        read, since each such finding is that record's own. The receipt firewall reads the backlog_item,
        done and maintainer_decision records, and the decision-chain arm the pending_decision records:
        when one of an arm's types is unread, or a record of one has a status or links value that could
        not be interpreted or an actor kind that is absent, unreadable or a lookalike the schema does not
        admit (its provenance, so whether the firewall bars it, is not known), that arm is CANNOT-EVALUATE
        naming each cause and is not run, since a receipt, remedy or chain member not read or not
        interpreted could hide a finding or raise a false one. Each arm applies this itself
        (_firewall_partial), so the driver's inactive-store path, which grades the arms under C-RECEIPTS
        and C-DECISION-CHAINS, applies it too."""
    if unevaluated:
        rep.cant("C-IMPORTED-SEGREGATION: {} did not evaluate, so the authority firewall (spec 8.6) is not "
                 "evaluated over the clean records it could not seat".format(", ".join(unevaluated)))
    clean = [r for r in recs if r.location != "imported"]
    links_uninterpreted = {r.rtype for r in clean if "links" in r.unparsed}
    if unread or links_uninterpreted:
        rep.cant("C-IMPORTED-SEGREGATION: {} (see C-RECORDS, C-PERRECORD-RECONCILE and C-ARCHIVE-ENUM); the "
                 "clean-to-imported link rule (spec 8.6) is not evaluated over the records not read or not "
                 "interpreted".format(_partial_causes(unread, links_uninterpreted, "links")))
    for r in clean:
        own_ns = _ROSTER_NAMESPACES.get(r.rtype)
        for rel, tid in r.links:
            shape = parse_series_id(tid)
            if shape is None or shape[0] == shape[1]:
                continue                  # a clean or malformed target: C-RECORDS and C-LINKS grade it
            if rel not in _CLEAN_TO_IMPORTED_RELS:
                rep.finding("C-IMPORTED-SEGREGATION: clean record {} links {} -> {!r}; a clean-to-imported "
                            "link is limited to relates, derives_from, follows and same-type supersedes "
                            "(spec 8.6)".format(_safe_display(r.id), _safe_display(rel), tid))
            elif rel == "supersedes" and shape[1] != own_ns:
                rep.finding("C-IMPORTED-SEGREGATION: clean record {} (type {!r}) supersedes -> {!r} of "
                            "another type; a clean-to-imported supersedes is same-type only (spec "
                            "8.6)".format(_safe_display(r.id), r.rtype, tid))
    _check_importer_receipts(recs, rep, "C-IMPORTED-SEGREGATION", unread)
    _check_importer_chains(recs, by_id, rep, "C-IMPORTED-SEGREGATION", unread)


def _partial_causes(unread, uninterpreted, fields, no_kind=frozenset()):
    # The causes a fail-closed rule names, in the C-LINKS wording: the types whose records were not all
    # read or confirmed, the types whose records carry a `fields` value that could not be interpreted, then
    # the types whose records carry an actor kind that is absent, unreadable or not one ACTOR_KINDS holds.
    causes = []
    if unread:
        causes.append("the records of {} could not all be read or confirmed".format(
            ", ".join(repr(t) for t in sorted(unread))))
    if uninterpreted:
        causes.append("the {} of {} records could not be interpreted".format(
            fields, ", ".join(repr(t) for t in sorted(uninterpreted))))
    if no_kind:
        causes.append("the actor kind of {} records is absent or unreadable, or not a kind the schema "
                      "admits".format(", ".join(repr(t) for t in sorted(no_kind))))
    return "; ".join(causes)


def _firewall_partial(recs, rep, cid, unread, types, rule):
    # The gate a section 8.6 firewall arm applies before it runs, on either driver path: when one of the
    # arm's record `types` was not read in full (`unread`), or a clean record of one carries a status or
    # links value that could not be interpreted or an actor kind that is absent, unreadable or not one the
    # schema admits (_make_rec's `unparsed`), the arm is CANNOT-EVALUATE naming each cause, and True tells
    # the arm not to run.
    clean = [r for r in recs if r.location != "imported" and r.rtype in types]
    missing = unread & types
    uninterpreted = {r.rtype for r in clean if r.unparsed - {"actor"}}
    no_kind = {r.rtype for r in clean if "actor" in r.unparsed}
    if not (missing or uninterpreted or no_kind):
        return False
    rep.cant("{}: {} (see C-RECORDS, C-PERRECORD-RECONCILE and C-ARCHIVE-ENUM); the {} (spec 8.6) is not "
             "evaluated over a partial set".format(
                 cid, _partial_causes(missing, uninterpreted, "status or links", no_kind), rule))
    return True


def _check_importer_receipts(recs, rep, cid, unread=frozenset(), unevaluated=()):
    """The section 8.6 receipt firewall over the clean record set `recs`, keyed on provenance and reported
    under `cid` (C-IMPORTED-SEGREGATION with the imported series active, C-RECEIPTS otherwise): "a clean
    backlog item reaching ratified `done` MUST hold a clean receipt authored by a non-importer actor". A
    non-importer item at unqualified `done` whose every receipt is importer-authored is a finding; one with
    no receipt at all is the C-RECEIPTS finding and is not repeated. An importer-authored item is not
    graded here, since spec 8.6 lets its legacy receipt satisfy it as recorded history. The one exception
    spec 8.6 defines is accepted: "a maintainer-authored clean `maintainer_decision` that links `corrects`
    to that item and records that its completion stands MUST satisfy the item's receipt obligation in
    place of the withdrawn receipt ... doctor and the upgrade MUST accept it". Its machine-checkable shape
    is the maintainer-authored (never importer-authored) clean maintainer_decision whose `corrects` link
    targets the item; whether its text records that the completion stands, and whether a later record
    overturns it, are not machine-checkable from the spec, so any such decision satisfies
    (_FIREWALL_RESIDUALS discloses both). The arm reads the backlog_item, done and maintainer_decision
    records, so when one of those types is in `unread` or a record of one has a status, links or actor
    kind that could not be interpreted, it is CANNOT-EVALUATE naming the cause and is not run
    (_firewall_partial): a receipt or remedy whose author is unknown neither confers nor withholds.
    `unevaluated` names the clean-record checks that did not evaluate: when one of those types is also in
    `unread`, the arm is CANNOT-EVALUATE naming them too, never a pass, and is not run; with only other
    types unread they do not stop the arm. An imported-series row is skipped, as in the
    receipt join: it is no receipt and no remedy, and owes no receipt (the driver never passes one; the
    skip is the backstop for a direct caller)."""
    if unevaluated and unread & {"backlog_item", "done", "maintainer_decision"}:
        rep.cant("{}: {} did not evaluate, so the receipt firewall (spec 8.6) is not evaluated over the "
                 "clean records it could not seat".format(cid, ", ".join(unevaluated)))
    if _firewall_partial(recs, rep, cid, unread, {"backlog_item", "done", "maintainer_decision"},
                         "receipt firewall"):
        return
    clean = [r for r in recs if r.location != "imported"]
    receipts = {}
    remedied = set()
    for r in clean:
        if r.rtype == "done":
            for rel, tid in r.links:
                if rel == "receipt_of" and isinstance(tid, str):
                    receipts.setdefault(tid, []).append(r)
        elif r.rtype == "maintainer_decision" and r.actor_kind == "maintainer":
            # The spec 8.6 remedy shape: a maintainer-authored (importer-authored confers nothing) clean
            # maintainer_decision linking `corrects` to the item whose only receipt is importer-authored.
            for rel, tid in r.links:
                if rel == "corrects" and isinstance(tid, str):
                    remedied.add(tid)
    for r in clean:
        if (r.rtype != "backlog_item" or not isinstance(r.id, str) or r.state != "done" or r.qual is not None
                or r.actor_kind == "importer"):
            continue
        mine = receipts.get(r.id, ())
        if mine and r.id not in remedied and all(d.actor_kind == "importer" for d in mine):
            rep.finding("{}: ratified backlog_item {!r} is authored by a non-importer actor but its only "
                        "receipt(s) {} are importer-authored; it MUST hold a clean receipt authored by a "
                        "non-importer actor, or a maintainer-authored maintainer_decision correcting it "
                        "(spec 8.6)".format(cid, r.id, ", ".join(sorted(_safe_display(d.id) for d in mine))))


def _check_importer_chains(recs, by_id, rep, cid, unread=frozenset(), unevaluated=()):
    """The section 8.6 decision-chain arm over the clean record set `recs`, keyed on provenance and
    reported under `cid` (C-IMPORTED-SEGREGATION with the imported series active, C-DECISION-CHAINS
    otherwise): "An importer-authored decision MUST NOT be treated as the current effective resolution of a
    clean `pending_decision` chain; resolving such a chain now MUST take a new clean decision linking
    back." Both derivations of decision_chains give the same chains; only the firewalled one, which
    C-DECISION-CHAINS counts, keeps an importer-authored decision from resolving or superseding. A chain
    that derivation gives at least one current effective resolution is resolved by a non-importer
    decision, and C-DECISION-CHAINS judges it there (one is a pass, two or more a finding), so this arm
    skips it: a maintainer decision that an importer-authored one supersedes stays the chain's
    resolution. Any other chain whose current effective resolution, derived with the firewall off, is an
    importer-authored pending_decision is a finding, since no non-importer decision resolves it and
    resolving it now takes a new clean decision linking back: a state-bearing status on an
    importer-authored record confers nothing now (spec 8.6). That is a chain whose only decided members
    are importer-authored, or one whose non-importer decisions are all superseded by non-importer ones
    (C-DECISION-CHAINS also reports its zero current resolutions). A resolution by any other actor is
    not graded here. When the pending_decision records
    are in `unread`, or one has a status, links or actor kind that could not be interpreted, the arm is
    CANNOT-EVALUATE naming the cause and is not run (_firewall_partial): a member not read, or one whose
    author is unknown, could be the importer-authored resolution or hide it. `unevaluated` is read as in
    _check_importer_receipts: when the pending_decision records are also in `unread`, the arm is
    CANNOT-EVALUATE naming the checks that did not evaluate too, never a pass, and is not run."""
    if unevaluated and "pending_decision" in unread:
        rep.cant("{}: {} did not evaluate, so the decision-chain firewall (spec 8.6) is not evaluated "
                 "over the clean records it could not seat".format(cid, ", ".join(unevaluated)))
    if _firewall_partial(recs, rep, cid, unread, {"pending_decision"}, "decision-chain firewall"):
        return
    resolved = set()
    for members, current in decision_chains(recs, by_id):
        if current:
            resolved.update(members)
    for members, current in decision_chains(recs, by_id, firewall=False):
        if resolved.intersection(members):
            continue
        for pid in current:
            cur = by_id.get(pid)
            if cur is not None and cur.actor_kind == "importer":
                rep.finding("{}: importer-authored pending_decision {!r} is the current effective resolution "
                            "of a clean pending_decision chain; resolving it now takes a new clean decision "
                            "linking back (spec 8.6)".format(cid, pid))


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
        preserved = _record_digest_matches(body, pdig)
    except _opf_emit.EmitError as exc:
        rep.cant("C-HISTORY-RESURRECTION: cannot canonicalize the current body of immutable record {!r} to "
                 "compare against the prior digest ({})".format(rid, exc))
        return
    if not preserved:
        rep.finding("C-HISTORY-RESURRECTION: immutable record {!r} body changed since the prior committed "
                    "snapshot (a created-terminal record does not change; spec 8.5)".format(rid))


def _check_resurrection(prior_records, prior_digests, by_id, all_ids, rep, source_unread=frozenset()):
    """C-HISTORY-RESURRECTION: for every id in the prior committed snapshot, confirm it did not resurrect
    across time (OPF-SPEC 8.4/13). A prior unqualified-terminal state now in any other state is a definite
    finding (actor-independent). A created-terminal record's BODY is additionally checked for preservation
    (immutable-body, spec 8.5; codex-3). A non-terminal status change is judged across ALL four actor kinds:
    legal for every actor passes; illegal for every actor is a finding; a transition legal for some actors
    but illegal for others is actor-DEPENDENT and, since the last-transition actor is not identifiable from
    the envelope, is CANNOT-EVALUATE (never accept-if-any-actor; codex-6); a rejection-shaped transition
    needing a pre-proposal state the snapshot cannot supply is likewise CANNOT-EVALUATE. Every durable prior
    id must remain present in active or archive (the across-time complement of the no-deletion check). A prior
    id absent from a type with a record source that could not be read (`source_unread`, from the collectors)
    is not read as vanished: the presence check is CANNOT-EVALUATE for that type, never a false finding. A
    type whose sources were all read is graded even where a rotation left it unconfirmed, since an id absent
    from every source read is absent. Reachability: `opf doctor` builds its prior from git, and that prior
    never carries a worklog id (the worklog is a ledger, not a type index), so the worklog half of this
    gate is reached only by a caller that supplies the observations itself."""
    unknown = set()
    for rid in sorted(prior_records):
        ptype, pstatus = prior_records[rid]
        if rid not in all_ids and ptype in source_unread:
            unknown.add(ptype)
            continue
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
    if unknown:
        rep.cant("C-HISTORY-RESURRECTION: the records of {} could not all be read (see C-RECORDS and "
                 "C-ARCHIVE-ENUM); prior record presence is not evaluated over a partial set (spec "
                 "12/13)".format(", ".join(repr(t) for t in sorted(unknown))))


# Homes-2 durable evidence (C-EVIDENCE-ENUM, spec 4.2). Each imported/<kind>/<run-id>/ bundle carries
# its own immutable inventories, which its writer derives from the run's transaction record or receipt,
# so an evidence commit changes only its bundle folder. Ingest publication writes the base inventory
# and a promotion phase inventory claiming its receipt. Inventories establish membership, not
# authenticated actor history, and doctor reads no journal to reconcile them.

def _evidence_claim(bundle, kind, run_id, path):
    """Validate one inventory row path against what its bundle may claim: a member of the bundle itself
    other than a bundle-root inventory, a Move destination (any path beneath .working/archive/moved/,
    default or explicit), or, for an adoption bundle, a retire preimage of the same run. (This docstring
    was corrected in review round 7 as a deliberate, documentation-only exception to the byte-identical
    protected set; the body is unchanged.)"""
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


ADOPTION_BASE_PHASE = "base"    # the [adoption] identity phase of inventory.toml (spec 4.2)
ADOPTION_PLAN_NAME = "plan.toml"   # the adoption bundle's own sealed plan (spec 4.2, 14.1)


def _inventory_phase(name):
    """The identity phase a bundle-root inventory file name carries (spec 4.2): `inventory.toml` is the
    base phase; `inventory-<phase>.toml` is that later phase, whose name is never `base` (the base phase
    name is reserved, so one phase spelling never answers to two file names). Raises ValueError."""
    if not _opf_store.is_evidence_inventory_name(name):
        raise ValueError("{!r} is not an evidence inventory name".format(name))
    if name == "inventory.toml":
        return ADOPTION_BASE_PHASE
    phase = name[len("inventory-"):-len(".toml")]
    if phase == ADOPTION_BASE_PHASE:
        raise ValueError("a later phase inventory is never named {!r}: the base phase name is reserved "
                         "for inventory.toml (spec 4.2)".format(name))
    return phase


def _adoption_identity(run_id, doc):
    """The validated (phase, plan_digest) identity of ONE adoption inventory (spec 4.2): the [adoption]
    table holds exactly the bundle's own run id, a phase name and a sha256:<64 lowercase hex> plan
    digest. Anything else raises ValueError, so a copied, misplaced or malformed record never evaluates;
    the phase-to-file-name and plan-digest-to-proven-plan bindings are each caller's own check (this
    helper sees neither the file name nor the plan)."""
    block = doc.get("adoption") if isinstance(doc, dict) else None
    if not isinstance(block, dict) or set(block) != {"run_id", "phase", "plan_digest"}:
        raise ValueError("the [adoption] identity table holds exactly run_id, phase and plan_digest "
                         "(spec 4.2)")
    if block["run_id"] != run_id:
        raise ValueError("the [adoption] identity names run {!r}, not the bundle run {!r} (an inventory "
                         "copied from another run, spec 4.2)".format(block["run_id"], run_id))
    phase = block["phase"]
    if not isinstance(phase, str) or (phase != ADOPTION_BASE_PHASE and not (
            _opf_store.is_evidence_inventory_name("inventory-{}.toml".format(phase)))):
        raise ValueError("the [adoption] identity phase {!r} is not a phase name (spec 4.2)".format(phase))
    digest = block["plan_digest"]
    if (not isinstance(digest, str) or not digest.startswith("sha256:")
            or not _HEX64_RE.fullmatch(digest[len("sha256:"):])):
        raise ValueError("the [adoption] identity plan_digest is not a sha256:<64 lowercase hex> digest "
                         "(spec 4.2)")
    return phase, digest


def _adoption_plan_digest(root_fd, bundle, run_id, rep):
    """(digest, bytes): the plan_digest of an adoption bundle's own plan.toml, proven from ONE read of
    its bytes, and those exact bytes, so the caller checks the plan's inventory row against the bytes
    the seal proof read, never a second read (spec 4.2). An absent or unreadable plan, and every refusal
    of _prove_adoption_plan, raises ValueError, so no inventory of that bundle evaluates as this run's
    record."""
    rel = _rel(bundle, ADOPTION_PLAN_NAME)
    raw, state = _read_bytes(root_fd, rel, rep)
    if state == "absent":
        raise ValueError("the adoption bundle has no {} to prove its inventories' plan digest against "
                         "(spec 4.2)".format(ADOPTION_PLAN_NAME))
    if state != "ok":
        raise ValueError("the adoption bundle's {} is unreadable, so no plan digest is proven (spec "
                         "4.2)".format(ADOPTION_PLAN_NAME))
    return _prove_adoption_plan(raw, run_id), raw


def _prove_adoption_plan(raw, run_id):
    """The plan_digest an adoption bundle's plan.toml bytes prove (spec 4.2): canonical TOML naming the
    bundle's run id, whose plan_digest re-seals it (the planner's seal: the digest of the canonical
    emission of the plan without its plan_digest). Every adoption inventory's [adoption] identity must
    name this digest. Pure over bytes its caller read once, and shared by the doctor and the apply
    side's bundle verifier; a non-canonical, foreign-run or unsealed plan raises ValueError."""
    try:
        plan = tomllib.loads(raw.decode("utf-8"))
        canonical = _opf_emit.emit_checked(plan).encode("utf-8")
        body = dict(plan)
        claimed = body.pop("plan_digest", None)
        sealed = "sha256:" + hashlib.sha256(_opf_emit.emit_checked(body).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, RecursionError, MemoryError, _opf_emit.EmitError) as exc:
        raise ValueError("the adoption bundle's {} is malformed TOML ({})".format(ADOPTION_PLAN_NAME, exc))
    if canonical != raw or claimed != sealed:
        raise ValueError("the adoption bundle's {} is not a canonical plan whose plan_digest seals its own "
                         "bytes (an edited plan, spec 4.2)".format(ADOPTION_PLAN_NAME))
    if plan.get("run_id") != run_id:
        raise ValueError("the adoption bundle's {} names run {!r}, not the bundle run {!r} (a plan of "
                         "another run, spec 4.2)".format(ADOPTION_PLAN_NAME, plan.get("run_id"), run_id))
    return sealed


def _evidence_rows(bundle, kind, run_id, doc):
    """Shared schema/path validation for doctor and the adoption apply shell; never upgrades old bytes.
    An adoption inventory additionally holds exactly one [adoption] identity table, validated here
    against the bundle's own run id (spec 4.2); an inventory of any other kind never carries one."""
    if isinstance(doc, dict) and doc.get("format") == "opf.ingest.evidence-inventory/v1":
        raise _LegacyIngestInventory("legacy-ingest-inventory: old-format ingest inventory is unsupported; "
                                     "refused without migration or rewrite")
    keys = {"format", "file", "adoption"} if kind == "adoption" else {"format", "file"}
    if (not isinstance(doc, dict) or set(doc) != keys
            or doc["format"] != EVIDENCE_FORMAT or not isinstance(doc["file"], list)):
        if kind == "adoption":
            raise ValueError("an adoption inventory holds exactly format {!r}, an [adoption] identity "
                             "table and a file array (spec 4.2)".format(EVIDENCE_FORMAT))
        raise ValueError("an inventory holds exactly format {!r} and a file array".format(EVIDENCE_FORMAT))
    if kind == "adoption":
        _adoption_identity(run_id, doc)
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
    reconciliation, since its claims are unknown. An adoption inventory's [adoption] identity must name
    the phase its file name carries and the plan digest its bundle's own sealed plan.toml proves; a
    foreign identity, or a bundle whose plan.toml is absent or does not prove, cannot evaluate. The
    plan's seal proof and its inventory row are checked against ONE read of its bytes, so a plan swapped
    between two reads never combines into a clean result.
    Deleting a whole bundle, inventory and payload together, is outside this local snapshot check;
    history coverage is separate. Reads use the contained readers and their per-file cap, with a
    bounded walk.
    """
    if homes < 2:
        return
    imported, archive = _opf_store.IMPORTED_REL, _opf_store.ARCHIVE_REL
    expected = {}
    bundles = []
    budget = [0]
    failed = [False]
    plans = {}    # adoption bundle -> (its proven plan digest, the bytes proven), or the ValueError raised
    proven = {}   # plan.toml path -> the bytes its seal proof read, the ONE read its row is checked against

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
            if kind == "adoption":
                phase, plan_digest = _adoption_identity(run_id, doc)
                named_phase = _inventory_phase(rel.rsplit("/", 1)[-1])
                if phase != named_phase:
                    raise ValueError("the [adoption] identity names phase {!r}, not the {!r} phase its "
                                     "file name carries (an inventory copied from another phase, "
                                     "spec 4.2)".format(phase, named_phase))
                if bundle not in plans:
                    try:
                        plans[bundle] = _adoption_plan_digest(root_fd, bundle, run_id, rep)
                    except ValueError as exc:
                        plans[bundle] = exc
                    else:
                        proven[_rel(bundle, ADOPTION_PLAN_NAME)] = plans[bundle][1]
                if isinstance(plans[bundle], ValueError):
                    raise plans[bundle]
                if plan_digest != plans[bundle][0]:
                    raise ValueError("the [adoption] identity names plan digest {!r}, not its bundle's own "
                                     "sealed plan's {!r} (an inventory of another run or plan, spec "
                                     "4.2)".format(plan_digest, plans[bundle][0]))
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
            if full in proven:
                raw, status = proven[full], "ok"    # the bytes the seal proof read: never a second read
            else:
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
# `managed_dir` is the manifest-derived half of the walk's directory test (a directory it enters rather
# than grading as one unregistered path), shared with the canonical-bytes leg.
ContainmentClassification = collections.namedtuple(
    "ContainmentClassification",
    ("view_targets", "valid_unmanaged", "malformed", "colliding", "managed_file", "layout",
     "perrecord_body_dirs", "archive_root", "imports_root", "control_roots", "evidence_roots", "homes",
     "worklog_errors", "managed_dir"))


def classify_containment(manifest_data, machine_rel):
    """Pure, side-effect-free derivation of the C-CONTAINMENT managed-set classification from the manifest
    and the machine-store relpath (spec 14.2). It does NO I/O and holds no `rep`: a malformed [unmanaged]
    entry is collected into `malformed` (the checker re-emits it as a CANNOT-EVALUATE) and a colliding
    declaration into `colliding` (the checker re-emits it as a C-CONTAINMENT finding; a colliding
    declaration covers NOTHING). This is the SINGLE authority `_check_containment` derives its
    adoption-content managed set from, so a second consumer cannot diverge from it by construction (F10-1).
    `enabled_types` and `layout` are derived internally from `manifest_data` by the SAME pure helpers the
    caller uses (D2), so the classification cannot drift from the manifest the caller validated.
    The imported-series activation (spec 8.2/4.2) is DERIVED HERE from `manifest_data` through
    imported_series_active, which requires the graded homes-2 declaration AND this tooling's own
    imported-series support (SUPPORTED_SPEC_VERSION at or above IMPORTED_SPEC_VERSION), so it is False
    for every store this tooling validates today, the accepted 2.0.0 / homes = 2 reservation included.
    There is NO caller-supplied activation flag: every consumer gets the manifest-derived activation, so
    a consumer cannot read an activated store as inactive by omitting an argument (the former optional
    parameter defaulted to False and allowed exactly that divergence). When activation holds, the
    imported managed leaves join the managed set; until it does, every current store's grading is
    unchanged, so the registration ships inert until activation raises both SUPPORTED_HOMES and
    SUPPORTED_SPEC_VERSION. Returns a ContainmentClassification."""
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
    # The imported-series managed leaves (spec 8.2/4.2): part of the managed set ONLY while the store's
    # imported series is ACTIVE, derived right here from the manifest (never true today:
    # imported_series_active requires the graded homes-2 declaration and SUPPORTED_SPEC_VERSION at or
    # above IMPORTED_SPEC_VERSION, so even the reserved 2.0.0 / homes = 2 declaration C-MANIFEST accepts
    # stays inactive until activation), and admitted ONLY directly in the machine store. The names derive
    # from the enabled-type roster in _opf_store, so the leaf set cannot drift from the imported
    # id/counter namespace set; the importer-only legacy_fragment contributes no leaf.
    imported = imported_series_active(manifest_data)
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
    # Homes 2 registers .working/.gitignore, the managed-block carrier, as a managed control file
    # (spec 4.2), recognized by its exact path alone: containment never reads it, so its content is
    # not graded here, and drift of the block is the writer's post-write check
    # (_opf_write_guard.verify_homes_gitignore_effective). A legacy store grades it as before.
    control_files = frozenset({WORKING_DIRNAME + "/.gitignore"}) if homes >= 2 else frozenset()

    def managed_leaf(p):
        # A STRICT managed-leaf test: a ledger, an enabled type index, a per-record body, a declared
        # store-scope view target, or a homes-2 control file. It deliberately EXCLUDES the
        # `_under_any(p, unmanaged)` clause so the collision check can ask whether an unmanaged
        # declaration names a managed leaf without the declaration trivially matching itself (F2).
        if p in view_targets:
            return True
        if p in control_files:
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
                    # name stays a stray, and while `imported` is False the set is empty, so an
                    # inactive store grades these names exactly as before (spec 8.2/4.2).
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
    graded_containers = (archive_root,) + control_roots + type_body_dirs
    managed_dir_prefixes = (mrel, archive_root) + control_roots + type_body_dirs + ledger_index_files
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
    # The legacy store registers only the imports tree; homes 2 also registers the staging tree (spec 4.2).
    staged_roots = (imports_root, _opf_store.STAGING_REL) if homes >= 2 else (imports_root,)

    def managed_dir(full):
        # A directory the walk enters as a managed namespace rather than grading as one unregistered path:
        # the machine-store root, a staged root and its run interior, the generation-2 worklog directory, a
        # per-record type-body dir, or an ancestor of a store-scope view target. The adoption namespaces an
        # adopting store adds come from the adoption state, so _check_containment adds them itself.
        if full == mrel or _under_any(full, staged_roots):
            return True
        if full == _rel(mrel, _opf_worklog.DIRECTORY_NAME):
            return worklog2
        if layout == "per-record" and full in perrecord_body_dirs:
            return True
        return any(vt == full or vt.startswith(full + "/") for vt in view_targets)

    return ContainmentClassification(
        view_targets=view_targets, valid_unmanaged=valid_unmanaged, malformed=malformed,
        colliding=colliding, managed_file=managed_file, layout=layout,
        perrecord_body_dirs=perrecord_body_dirs, archive_root=archive_root, imports_root=imports_root,
        control_roots=control_roots, evidence_roots=evidence_roots, homes=homes,
        worklog_errors=worklog_errors, managed_dir=managed_dir)


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


def _adoption_state(root_fd, machine_rel, in_repo, rep):
    """The homes-1 adoption state through the read-only `_opf_adopt_state` reader (spec 4.2, 11, 14.2, 17),
    or None after a CANNOT-EVALUATE: any fault inside the reader is a named cannot-evaluate, never a silent
    fallback to the not-adopting grading. The reader is imported lazily, so the doctor gains no top-level
    import of the adoption modules."""
    try:
        import _opf_adopt_state
        return _opf_adopt_state.adoption_state(root_fd, machine_rel, in_repo=in_repo)
    except Exception as exc:  # noqa: BLE001  fail-closed barrier around the whole reader
        rep.cant("C-CONTAINMENT: the adoption state cannot be read ({}); fail-closed".format(
            _safe_display(repr(exc))))
        return None


def _check_canonical_store_files(root_fd, machine_rel, manifest_data, rep, import_status):
    """C-CONTAINMENT's canonical-bytes leg, run by the driver right after _check_containment and attributed
    to the same check: a machine-store TOML file must be the canonical emission of its own parsed content,
    the byte-reproduction rule each sanctioned writer re-proves before a rewrite (spec 5.7, 9.2). A hand
    edit that leaves the content readable (an appended comment, re-flowed formatting) is therefore a
    finding here, at the next doctor run and over a pre-commit snapshot, rather than passing until the
    next write refuses it. A file an earlier emitter wrote with a literal U+2028 or U+2029 inside a basic
    string passes, as it does for the writers (_opf_emit.matches_store_bytes). A file whose bytes a check
    already read (rep.toml_reads: _read_toml and the worklog ledger reads) is proven from those same bytes
    and never read again, so the manifest and each worklog ledger keep one content read per validation
    (store discovery reads the manifest separately, before it); a file no check read is read here, once.
    The walk covers only the machine store, never the evidence homes or the journals beside it. NOT every
    machine-store TOML file is graded, by design: a path under a valid [unmanaged] declaration and the
    legacy imports staging are skipped (_check_containment grades the declaration, never the files under
    it, and refuses the staging area); during a partial import substantiated by an active imports/<run-id>
    run (the same predicate _check_containment and _validate_archive use) only the stray paths those walks
    triage are skipped: outside the archive tree, a file outside the classifier's managed set (the
    containment walk's stray, which it triages, or in an adopting store grades as a finding that already
    fails the store), and inside it, a path _validate_archive routes through _archive_unmanaged
    (_archive_triaged_file). A directory either walk grades as one such path without listing it (outside
    the archive, one the classifier's managed_dir does not admit; inside it, a bucket subdirectory,
    _archive_triaged_dir) is not entered, since every file beneath it is such a stray, so a symlink, a
    listing fault or deep nesting inside it cannot turn the store CANNOT-EVALUATE. A triaged stray is
    therefore never a canonical-bytes finding, while a bucket's archive.toml, worklog.toml and
    <type>.index.toml, which _validate_archive grades, keep this check during the partial import. A file
    that cannot be read or parsed is left to the check that reads it, EXCEPT init.toml, whose only reader
    is this leg, so its faults are graded here (unreadable is a CANNOT-EVALUATE, unparseable a finding),
    never a silent pass. A non-canonical managed or archived file is advised to be restored from the
    integration base; an unregistered stray has no base version, so its finding advises removal or
    re-admission through opf instead. It proves serialization only: a hand edit that leaves canonical
    bytes is not detected here."""
    cls = classify_containment(manifest_data, machine_rel)
    skipped = tuple(cls.valid_unmanaged) + (_legacy_import_staging(machine_rel)[0],)
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep,
                                                                           cls.homes)
    init_rel = _rel(machine_rel, INIT_PROVENANCE_NAME)

    def walk(reldir, depth):
        if depth > _CONTAINMENT_MAX_DEPTH:
            rep.cant("C-CONTAINMENT: directory nesting under {!r} exceeds the containment-walk ceiling of "
                     "{}; its TOML files are not proven canonical (fail-closed)".format(
                         _safe_display(reldir), _CONTAINMENT_MAX_DEPTH))
            return
        subdirs, files = _list_dir(root_fd, reldir, rep)
        if subdirs is None and files is None:
            return
        for f in files:
            full = reldir + "/" + f
            if not f.endswith(".toml") or _under_any(full, skipped):
                continue
            managed = cls.managed_file(full)
            archived = _under_any(full, (cls.archive_root,))
            if partial_active and (_archive_triaged_file(cls.archive_root, full) if archived else not managed):
                # Under a substantiated partial import, skip only the strays those walks own: outside the
                # archive, a file the containment walk triages (or, adopting, reports as a finding); inside
                # it, a path _validate_archive sends to _archive_unmanaged. A bucket's archive.toml,
                # worklog.toml and <type>.index.toml are graded there, so they keep this check.
                continue
            raw = rep.toml_reads.get(full)
            try:
                if raw is None:
                    raw = _opf_store._read_store_bytes_contained(root_fd, full)
            except (StoreError, _journal.JournalError) as exc:
                if full == init_rel:
                    rep.cant("C-CONTAINMENT: machine-store file {!r} cannot be read ({}); no other check "
                             "reads it, so its canonical form cannot be evaluated (spec 5.7/9.2)".format(
                                 full, _safe_display(str(exc))))
                continue     # unreadable: graded by the check that reads it (see the docstring)
            if raw is None:
                continue     # gone between the listing and the read: absence is never graded here
            try:
                # The bytes a check already graded (or this leg's own single read), parsed afresh (never
                # the shared model a check may hold), so a file is never read twice in one run.
                model = _opf_store._parse_store_toml(full, raw)
            except (StoreError, _journal.JournalError) as exc:
                if full == init_rel:
                    rep.finding("C-CONTAINMENT: machine-store file {!r} cannot be parsed ({}), so no "
                                "sanctioned writer produced it and no other check grades it "
                                "(spec 5.7/9.2)".format(full, _safe_display(str(exc))))
                continue     # unparseable: graded by the check that reads it (see the docstring)
            try:
                fresh = _opf_emit.emit_checked(model).encode("utf-8")
            except _opf_emit.EmitError as exc:
                rep.finding("C-CONTAINMENT: machine-store file {!r} holds content the canonical emitter "
                            "cannot write ({}), so no sanctioned writer produced it (spec 5.7/9.2)".format(
                                full, _safe_display(str(exc))))
                continue
            if not _opf_emit.matches_store_bytes(raw, fresh):
                if managed or archived:
                    rep.finding("C-CONTAINMENT: machine-store file {!r} is not in canonical emitted form "
                                "(it carries comments or non-canonical serialization, which no sanctioned "
                                "writer produces); restore it from the integration base and redo the "
                                "change through opf (spec 5.7/9.2)".format(full))
                else:
                    rep.finding("C-CONTAINMENT: machine-store file {!r} is not in canonical emitted form "
                                "(it carries comments or non-canonical serialization, which no sanctioned "
                                "writer produces); it is an unregistered path with no base version, so "
                                "remove it or re-admit its content through opf (spec 14.2, 5.7/9.2)".format(
                                    full))
        for d in subdirs:
            full = reldir + "/" + d
            if _under_any(full, skipped):
                continue
            if partial_active and (_archive_triaged_dir(cls.archive_root, full)
                                   if _under_any(full, (cls.archive_root,)) else not cls.managed_dir(full)):
                # Under a substantiated partial import, a directory those walks grade as one path without
                # listing it is not entered either: outside the archive, one the containment walk does not
                # enter (it triages it, or, adopting, reports it as a finding); inside it, a bucket
                # subdirectory (_archive_unmanaged). Every file beneath it is a stray the file test above
                # already skips, so listing it could only add a CANNOT-EVALUATE that grading never needs.
                continue
            walk(full, depth + 1)

    walk(machine_rel, 0)


# This tooling's adoption journal home: the parent of the adoption engine's journal root
# (_opf_adopt_apply.JOURNAL_REL is journal_root("adoption")), derived from the shared constructor.
_ADOPTION_JOURNAL_HOME = _opf_store.journal_root("adoption").rsplit("/", 1)[0]


def _adoption_journal_present(root_fd):
    """True when this tooling's adoption journal home (`.working/journals/adoption/`) is a real directory
    beneath root_fd: ONE no-follow lstat of that home, never a listing or a read of the journal itself (spec
    4.2: the doctor never reads the machine-local journal). Absent, of another type, or unobservable reads
    False, so a legacy store's journals/ keeps its legacy grading: a fault grades, never exempts."""
    try:
        st = _journal._lstat_contained(root_fd, _ADOPTION_JOURNAL_HOME)
    except (_journal.JournalError, OSError):
        return False
    return st is not None and stat.S_ISDIR(st.st_mode)


def _check_containment(root_fd, machine_rel, manifest_data, import_status, rep, *, in_repo=None):
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
    passed in, and so is the imported-series activation (imported_series_active), so no caller can
    read an activated store as inactive by omitting a flag; the walk itself needs no imported
    awareness (the imported leaves are plain managed leaves directly in the machine store, spec
    8.2/4.2; C-IMPORTED-SCHEMA reads their content). Were IMPORTED_CHECKS empty, an ACTIVE imported
    series would be routed to CANNOT-EVALUATE (the activation tripwire below): registration without
    content grading never certifies. On homes 1 a store that has adopted also registers, at file
    level, each admitted adoption run's committed-evidence record paths and reports its frozen
    sources as bounded adoption state (`_opf_adopt_state`); `in_repo` (keyword only, unknown by
    default) is the store topology the adoption plan identity is checked against."""
    cls = classify_containment(manifest_data, machine_rel)
    # The SAME activation predicate classify_containment just derived its managed set from (the ONE
    # function imported_series_active), read again only for the tripwire; the two reads cannot disagree.
    imported = imported_series_active(manifest_data)
    # The activation tripwire (spec 8.2/4.2): an ACTIVE imported series with an EMPTY imported roster
    # must never certify, or the imported leaves would be registered as managed while nothing reads
    # their content (the PRC-F1 N-1 blind spot, reopened at activation). A CANNOT-EVALUATE, not a
    # finding: the store is not known bad, the tooling is known incomplete. The populated
    # IMPORTED_CHECKS roster disarms this; it stays as the guard against emptying that roster.
    if imported and not IMPORTED_CHECKS:
        rep.cant("C-CONTAINMENT: the imported series is active but every imported-content check "
                 "(C-IMPORTED-*) is still deferred; the imported leaves would be certified "
                 "unread, so the store cannot be evaluated (spec 8.2/4.2)")
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
    valid_unmanaged = cls.valid_unmanaged
    managed_file = cls.managed_file
    archive_root = cls.archive_root
    imports_root = cls.imports_root
    staging_root = _opf_store.STAGING_REL
    # A legacy store keeps its legacy grading: staging/, journals/, imported/ and archive/ are ordinary
    # paths there, graded as unregistered like any other. Homes 2 registers them (spec 4.2). The one
    # legacy exception is this tooling's adoption journal: a coupled first adoption journals at
    # .working/journals/adoption/ under the engine's held lock before any adoption evidence exists, so
    # once that home is a real directory the store has started an adoption and the walk leaves journals/
    # out (containment makes no recovery claim, spec 4.2); a store without it, which has started no
    # adoption, grades journals/ exactly as before (_adoption_journal_present).
    homes2 = cls.homes >= 2
    staged_roots = (imports_root, staging_root) if homes2 else (imports_root,)
    kind_roots = tuple(staging_root + "/" + k for k in _opf_store.STAGING_KINDS) if homes2 else ()
    skipped_roots = cls.evidence_roots + ((_opf_store.JOURNALS_REL,) if homes2 else ())
    # Homes-1 adoption control area and bounded adoption state (spec 4.2, 11, 14.2, 17), read by the read-only
    # _opf_adopt_state reader. Without the adoption home it is NOT_ADOPTING after one lstat and every line
    # below grades exactly as before. Homes 2 registers its evidence roots wholesale above.
    adoption = None if homes2 else _adoption_state(root_fd, machine_rel, in_repo, rep)
    if homes2:
        # Homes 2 keeps adoption evidence in its own home (spec 4.2), and its grading on base 2.0.0
        # has not landed: the evidence-root skip above would pass that home unread, so a store
        # holding an entry of that name cannot be evaluated. The no-follow listing of the evidence
        # root finds a directory or a file of that name; any other entry type refuses the listing.
        imported_dirs, imported_files = _list_dir(root_fd, _opf_store.IMPORTED_REL, rep)
        if "adoption" in (imported_dirs or []) + (imported_files or []):
            rep.cant("C-CONTAINMENT: the adoption home .working/imported/adoption/ exists, and "
                     "adoption grading on base 2.0.0 is not yet implemented (spec 4.2; lands with "
                     "the adoption-journal move)")
    adopting = adoption is not None and adoption.adopting
    frozen = adoption.frozen if adopting else {}
    adoption_dirs = frozenset()
    if adopting:
        import _opf_adopt_state
        for msg in adoption.cannot:
            rep.cant(msg)
        for msg in adoption.findings:
            rep.finding(msg)
        rep.residuals.extend(_ADOPTING_RESIDUALS)
        # QA round 1: registration is at FILE level (each committed-inventory-listed path and recorded
        # Move destination), never a whole tree, so a stray under an admitted run's homes is graded.
        registered_files = frozenset(adoption.registered)
        # spec 14.2: an [unmanaged] declaration MUST NOT equal, contain or lie within the control area. A
        # colliding one is a finding and covers nothing, so every path beneath it is graded.
        control = _opf_adopt_state.CONTROL_ROOTS
        collided = [u for u in valid_unmanaged
                    if _under_any(u, control) or any(_under_any(c, (u,)) for c in control)]
        for u in collided:
            rep.finding("C-CONTAINMENT: unmanaged path {!r} equals, contains or lies within the OPF control "
                        "area ({}) of an adopting store, so it covers nothing (spec 14.2)".format(
                            u, ", ".join(repr(c) for c in control)))
        if collided:
            valid_unmanaged = [u for u in valid_unmanaged if u not in collided]
            classified_file = managed_file

            def managed_file(p):
                if _under_any(p, collided) and not _under_any(p, valid_unmanaged):
                    return False
                return classified_file(p)
        # Walked as namespaces so that every entry beneath them other than a registered FILE is graded:
        # the adoption homes themselves, each strict ancestor directory of a registered file (so a stray
        # beside it is graded one by one), and each strict ancestor directory of a LIVE frozen source (an
        # ancestor whose frozen source is gone is graded as one unregistered entry). With no admitted run
        # the namespaces keep the legacy whole-entry grading.
        live = [p for p, src in frozen.items() if src.grade != "absent"]
        covered = list(registered_files) + live
        adoption_dirs = frozenset(_opf_adopt_state.ADOPTION_NAMESPACES if adoption.runs else ()) | frozenset(
            p.rsplit("/", i)[0] for p in covered for i in range(1, p.count("/")))
        if registered_files:
            graded_file = managed_file

            def managed_file(p):
                return p in registered_files or graded_file(p)

    unmanaged_files = []

    def managed_dir(full):
        # A subdir that is a legitimate managed namespace to RECURSE rather than flag: the machine-store
        # root, the imports root and its run interior (graded/triaged per F3), a per-record type-body dir,
        # or an ancestor of a store-scope view target. (archive_root and valid_unmanaged subtrees are
        # handled by the caller's skip.) Anything else under the store is an UNREGISTERED directory, graded
        # as a stray even when empty (round-14: the walk previously graded files only, so an empty rogue
        # directory, or a tree of only empty dirs, escaped grading entirely).
        # The manifest-derived namespaces are the classifier's (cls.managed_dir, which the canonical-bytes
        # leg shares); an adopting store's adoption namespaces are added here.
        return full in adoption_dirs or cls.managed_dir(full)

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
            if not managed_file(full):
                unmanaged_files.append(full)
        for d in subdirs:
            full = reldir + "/" + d
            # Journal contents are outside containment; only journal validation reads them. Doctor reads
            # just the id reservations, for the reserved numbers (_reserved_numbers).
            # Evidence bytes have their own exact inventory check, distinct from record rotation.
            if full in skipped_roots:
                continue
            if full == _opf_store.JOURNALS_REL and _adoption_journal_present(root_fd):
                continue     # a legacy store's adoption journal home: probed only once journals/ is met
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
    walk(WORKING_DIRNAME, 0)
    for p in sorted(unmanaged_files):
        if p in frozen:
            continue      # a frozen source is graded below from the adoption state, whatever the status
        # Ruling R-a (QA round 1, codex MAJOR 1): the partial triage posture is for legacy (non-adopting)
        # stores only; in an adopting store every unenumerated path stays a finding at required.
        if partial_active and not adopting:
            rep.triage_path("unregistered path {!r} at the store location (an import or migration is in "
                            "progress; triage per spec 14.2)".format(p))
        else:
            rep.finding("C-CONTAINMENT: unregistered path {!r} at the store location is neither OPF-managed "
                        "nor enumerated as unmanaged (spec 14.2/11)".format(p))
    # spec 11/14.2: a frozen source whose live bytes match its plan digest is bounded adoption state, reported
    # rather than failed at any import_status; a drifted or vanished one fails at required, even under a
    # substantiated partial (never triage). A source the reader could not grade is already a cannot-evaluate.
    for p in sorted(frozen):
        src = frozen[p]
        if src.grade == "bounded":
            rep.migration_incomplete_path(
                "migration_incomplete: frozen {} source {!r} of adoption run {}: {}; bounded adoption state "
                "until its retirement is recorded (spec 11/14.2)".format(src.disposition, p, src.run_id, src.detail))
        elif src.grade in ("drifted", "absent"):
            rep.finding("C-CONTAINMENT: frozen {} source {!r} of adoption run {}: {} (spec 11/14.2)".format(
                src.disposition, p, src.run_id, src.detail))
            # QA round 1 (codex MEDIUM 3): the approved source remains unresolved, so the report carries
            # migration_incomplete as well as the required-grade failure above (spec 11).
            rep.migration_incomplete_path(
                "migration_incomplete: frozen {} source {!r} of adoption run {} remains unresolved: {} "
                "(spec 11)".format(src.disposition, p, src.run_id, src.detail))


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
    enforces (keys EXACTLY RELEASE_KEYS plus any RELEASE_OPTIONAL_KEYS, a valid SemVer version, an RFC
    3339 date, a well-formed worklog_span, a sha256 coverage_digest, and the optional imported flag under
    the shared _imported_row_findings rule). Cross-row tiling, uniqueness, and monotonicity are NOT
    applied: this decides only whether an INJECTED prior row can serve as the append-only immutability
    baseline, so a well-formed row that merely DIFFERS from the current ledger still reaches the graded
    rewrite check (a real history rewrite), while a dict of garbage keys is a malformed observation routed
    to CANNOT-EVALUATE rather than a false rewrite finding (F5; guard-input-soundness)."""
    if not isinstance(row, dict):
        return False
    if set(row) - RELEASE_OPTIONAL_KEYS != RELEASE_KEYS:
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
    if _imported_row_findings(row, "prior release row"):
        return False
    return True


def _normalize_prior(prior):
    """Validate the injected prior committed snapshot: {releases: list, counters_high: {ns: int},
    records: {id: (type, status)}, digests: {id: str} (optional), imported: {imported id: digest} (optional,
    spec 8.3), held: {ns: [[lo, hi], ...]} (optional)}. An "imported:<NS>" counter is returned apart as
    imported_high, and the imported member with its imported_malformed verdict, for the driver to grade
    where the imported series is active (spec 8.2). Every record id is shape-validated and
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
    # `held` (optional): every id the prior commit held, active or archived, worklog included, as
    # {namespace: [[lo, hi], ...]} inclusive intervals; only the reserved-number exemption reads it, and its
    # absence makes that exemption CANNOT-EVALUATE. Each bound is an id number in TOML's integer range.
    held = prior.get("held")
    norm_held = None
    if held is not None:
        if not isinstance(held, dict):
            return None
        norm_held = {}
        for ns, spans in held.items():
            if not isinstance(ns, str) or ns not in _ROSTER_NS_SET or not isinstance(spans, (list, tuple)):
                return None
            rows = []
            for span in spans:
                if not (isinstance(span, (list, tuple)) and len(span) == 2
                        and all(type(v) is int and 1 <= v <= (1 << 63) - 1 for v in span) and span[0] <= span[1]):
                    return None
                rows.append((span[0], span[1]))
            norm_held[ns] = _merge_spans(rows)
    return {"releases": list(releases), "counters_high": norm_high, "records": norm_records,
            "digests": norm_digests, "imported_high": imported_high, "imported": norm_imported,
            "imported_malformed": imported_malformed, "held": norm_held}


def _merge_spans(spans):
    """Inclusive (lo, hi) intervals sorted and merged, so adjacent or overlapping ones become one and a
    lookup can bisect them."""
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


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
        # (EINTR / EIO / an invalid fd) during teardown must not crash the validator (outside B6).
        # Any other exception a close raises never abandons the other close, and goes through the one
        # first-interrupt selection, so an interrupt in flight here keeps propagating as itself.
        inflight, raised = _journal._in_flight_in(sys._getframe()), []
        try:
            _close_fd_quietly(root_fd)
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            raised.append(exc)
        if product_root_fd is not None:
            try:
                _close_fd_quietly(product_root_fd)
            except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
                raised.append(exc)
        _journal._yield_close_exceptions(inflight, raised)
    return rep.result(evaluated_profiles, unevaluated_profiles)


# The cause each check that reads the worklog entries names when "worklog" is in the collectors' `unread` set:
# an entry not read could hide a defect from it or make it report a false one.
_WORKLOG_UNREAD = ("the worklog entries (active + archive) could not all be read or confirmed (see C-RECORDS "
                   "and C-ARCHIVE-ENUM)")


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
    # The imported-series activation flag, derived ONCE here (imported_series_active: the graded homes-2
    # declaration and this tooling's imported-series support, so every store graded today is inactive)
    # for the roster, the four imported checks, C-COUNTERS, C-ID-SPACE, C-NO-DELETION, C-CONTIGUITY and
    # the across-time checks, so the content grading and the per-series id grading cannot disagree about
    # which stores carry the series; C-CONTAINMENT's classification derives its own activation inside
    # classify_containment from the SAME predicate, so the leaf registration cannot diverge from them
    # either.
    imported = imported_series_active(manifest_data)
    rep.require_homes(homes, imported)

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
    unread = set()          # the record types whose population a collector could not read or confirm in full
    active_recs, recon = _gather_active_records(root_fd, machine_rel, enabled_types, layout,
                                                registered_vendors, rep, unread=unread)
    try:
        active_worklog = _gather_worklog(
            root_fd, _rel(machine_rel, WORKLOG_NAME), registered_vendors, rep,
            required=True, machine_rel=machine_rel, propagate_manifest_failure=True,
            manifest_model=manifest_model, unread=unread)
    except (_opf_worklog.ManifestShapeError, _opf_worklog.ManifestValidationError,
            _opf_worklog.ManifestReadError) as exc:
        _attribute_manifest_failure(rep, exc)
        rep.cant("{} is not evaluated: {} failed manifest validation "
                 "(see C-MANIFEST)".format(_rel(machine_rel, WORKLOG_NAME), exc.relpath))
        return evaluated_profiles, unevaluated_profiles
    if active_worklog is None:
        # The active ledger was absent, unreadable or invalid (its cant is recorded): its entries, each a
        # link source and target, were not read.
        unread.add("worklog")
        active_worklog = {}

    # --- C-PERRECORD-RECONCILE: replay the bidirectional index<->body reconciliation (spec 13) --------
    rep.ran("C-PERRECORD-RECONCILE")
    for kind, msg in recon:
        if kind == "cant":
            rep.cant(msg)
        else:
            rep.finding(msg)

    # --- C-IMPORTED-SCHEMA (an active imported series only): every imported row against the imported
    # envelope, never through the schema deferral (spec 8.3) ------------------------------------------
    imported_recs = []
    imported_complete = True
    if imported:
        rep.ran("C-IMPORTED-SCHEMA")
        imported_recs, imported_complete = _gather_imported_records(root_fd, machine_rel, enabled_types,
                                                                    registered_vendors, rep)

    # --- C-ARCHIVE-ENUM: walk + reconcile the archive; collect rotation-eligibility for C-ROTATION -----
    rep.ran("C-ARCHIVE-ENUM")
    # The walk records into its own sets, joined to `unread` below, so C-ROTATION, which reads the archived
    # worklog alone, can tell an archived worklog left unread or unconfirmed from an active ledger that was
    # not read.
    archive_unread, archive_unconfirmed = set(), set()
    # The active locations the collectors above read in full and counted, keyed store-relative exactly as
    # an archive.toml destination names them, so the walk can grade a moved row or span whose declared
    # destination is an active index or the active ledger: a type's index when its population was read and
    # confirmed whole, and the active ledger when every entry was read into its map.
    active_counted = {}
    for _tname in enabled_types:
        if _tname != "worklog" and _tname not in unread:
            active_counted["{}{}".format(_tname, INDEX_SUFFIX)] = {
                r.id for r in active_recs if r.rtype == _tname and isinstance(r.id, str)}
    if "worklog" not in unread:
        active_counted[WORKLOG_NAME] = set(active_worklog)
    archive_recs, archive_worklogs, rotatable = _validate_archive(root_fd, machine_rel, enabled_types,
                                                                  registered_vendors, import_status, rep,
                                                                  homes=homes, unread=archive_unread,
                                                                  unconfirmed=archive_unconfirmed,
                                                                  active_counted=active_counted)
    # The record types with a record source (an index, a worklog, a bucket or a destination) that could not
    # be read: an id of one may be held where nothing was read. `unread` also holds the types a rotation
    # leaves unconfirmed though every source was read, so a current-state join over one is not a pass,
    # while the ids that were read still decide whether a durable id is present.
    source_unread = unread | archive_unread
    archive_unread |= archive_unconfirmed
    unread |= archive_unread

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
    if "worklog" in archive_unread:
        # An archived entry not read could lie beyond the released end; an archived id read beyond it is
        # still reported.
        rep.cant("C-ROTATION: the archived worklog entries could not all be read or confirmed (see "
                 "C-ARCHIVE-ENUM); released-only rotation is not evaluated over a partial set (spec 12)")
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
    if "worklog" in unread:
        # An entry not read can only add a duplicate or an id above its high-water, so a defect among the
        # ids read is still reported.
        rep.cant("C-ID-SPACE: {}; worklog id uniqueness and ids-within-counters are not evaluated over a "
                 "partial set (spec 8.2)".format(_WORKLOG_UNREAD))
    # The imported series is graded on its OWN number lines (spec 8.2: each series independently): read from
    # the imported leaves only, unique within the series, and each id within its own "imported:<NS>"
    # high-water, never the clean one. The clean union above never holds an imported id. The imported
    # grading here and under C-NO-DELETION reports through one _TeeReport, so C-IMPORTED-IDS replays exactly
    # what it emitted (spec 8.3 gives that check the same series grammar, counters and no-deletion).
    imported_rows, imported_unread, imported_strays = [], set(), []
    imported_tee = _TeeReport(rep)
    if imported:
        imported_rows, imported_unread = _gather_imported_rows(root_fd, machine_rel, enabled_types,
                                                               imported_tee, imported_strays)
        imported_ids = [rid for _key, rid, _row in imported_rows]
        for f in check_unique_ids(imported_ids, imported_series=True):
            imported_tee.finding("C-ID-SPACE: imported series: {}".format(f))
        for f in check_ids_within_counters(imported_ids, high, imported_series=True):
            imported_tee.finding("C-ID-SPACE: imported series: {}".format(f))

    # --- the cross-record checks ----------------------------------------------------------------------
    # The section 8.6 firewall is keyed on provenance, never on activation (spec 8.6: it covers every
    # importer-authored record). With the imported series active C-IMPORTED-SEGREGATION grades its receipt
    # and decision-chain arms below; otherwise that check is off the roster (spec 4.2), so the two arms
    # report under C-RECEIPTS and C-DECISION-CHAINS, the joins they guard, with the same `unread` set, so
    # each arm is CANNOT-EVALUATE over a partial set on either path. A store holding an importer-authored
    # clean record discloses the firewall residuals either way. Either way the two arms also fail closed on
    # a clean-record reader that did not evaluate (spec 8.3/8.6): `unseated` names those readers, and here
    # an arm whose own record types are unread is then CANNOT-EVALUATE naming them, never a pass.
    unseated = [cid for cid in ("C-RECORDS", "C-PERRECORD-RECONCILE", "C-ARCHIVE-ENUM")
                if rep.checks.get(cid) == "CANNOT-EVALUATE"]
    if any(r.actor_kind == "importer" for r in recs):
        rep.disclose_firewall()
    rep.ran("C-RECEIPTS")
    _check_receipts(recs, by_id, rep, unread)
    if not imported:
        _check_importer_receipts(recs, rep, "C-RECEIPTS", unread, unseated)
    rep.ran("C-HANDOFF")
    _check_handoff(recs, rep, unread)
    rep.ran("C-DECISION-CHAINS")
    _check_decision_chains(recs, by_id, rep, unread)
    if not imported:
        _check_importer_chains(recs, by_id, rep, "C-DECISION-CHAINS", unread, unseated)
    rep.ran("C-LINKS")
    # C-LINKS reads the rows C-IMPORTED-SCHEMA read, and resolves a target only among those the per-series
    # id grading above reads as series members, keyed by (series key, id) so a same-id row in another leaf
    # never stands in for one. It reads every record of both series, so it receives the whole collectors'
    # `unread` set, and the imported side counts as complete only when BOTH imported collectors read
    # every leaf and row (a leaf the per-series grading could not read also starves imported_targets).
    imported_members = {(key, rid) for key, rid, _row in imported_rows}
    imported_targets = [r for r in imported_recs if isinstance(r.id, str)
                        and (IMPORTED_PREFIX + str(enabled_types.get(r.rtype)), r.id) in imported_members]
    _check_links(recs, by_id, all_ids, rep, imported_recs, imported_targets, unread,
                 imported_complete and not imported_unread)
    # --- C-IMPORTED-SEGREGATION (an active imported series only): the section 8.6 authority firewall over
    # the clean record set the cross-record checks above read, fail-closed on any clean-record reader that
    # did not evaluate and on the collectors' `unread` set, which the firewall widens by _make_rec's
    # `unparsed` as the joins do (spec 8.3/8.6); its provenance-keyed arms run on an inactive store too
    # (above) ------
    if imported:
        rep.ran("C-IMPORTED-SEGREGATION")
        _check_imported_segregation(recs, by_id, unseated, rep, unread)

    # --- the frozen-span / no-deletion / partition / contiguity checks (active + archive together) ----
    # Each reads the merged worklog, so each is CANNOT-EVALUATE naming the unread worklog whenever "worklog" is
    # in `unread`. A check whose finding an unread entry could make false is not run; one whose finding an
    # unread entry can only add to still reports the entries read.
    rep.ran("C-FROZEN-COVERAGE")
    if version_data is None:
        rep.cant("C-FROZEN-COVERAGE: the version ledger did not evaluate; frozen coverage is not checkable")
    if "worklog" in unread:
        # A covered entry not read reads as absent from its span, and an archived copy not read could
        # change the digest either way.
        rep.cant("C-FROZEN-COVERAGE: {}; frozen coverage is not evaluated over a partial set (spec "
                 "6.1/13)".format(_WORKLOG_UNREAD))
    elif version_data is not None:
        for f in check_frozen_coverage(version_data, merged):
            rep.finding("C-FROZEN-COVERAGE: {}".format(f))

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
    # Homes 2: a number an id reservation holds is skipped here and under C-CONTIGUITY only where no present
    # record carries it and the prior committed snapshot held no record with that id (an orphaned
    # reservation; _split_reserved). A reservation outlives the publication it preceded, so a number whose
    # record the prior held is owed exactly as without reservations, and one no usable prior can decide is
    # CANNOT-EVALUATE, never granted. A reservation that does not evaluate is CANNOT-EVALUATE, graded as
    # though nothing were reserved, never a silently wider pass.
    reserved, reservations_ok = _reserved_numbers(root_fd, rep) if homes >= 2 else ({}, True)
    holdings, no_holdings = _prior_holdings(obs)
    # The imported number lines take the SAME exemption, decided against the prior imported baseline
    # (spec 8.2 derives the next ID per series key, the quoted "imported:<NS>" ones included, so an
    # imported reservation's number is issued on its own line exactly as a clean one is on its own).
    imp_holdings, imp_no_holdings = _imported_prior_holdings(obs) if imported else (None, None)
    present_wl = sorted(merged)
    wl_hw = high.get("WL")
    wl_issued, wl_undecided = _split_reserved("WL", reserved.get("WL", ()), merged, holdings)
    wl_skipped = wl_issued | wl_undecided
    if "worklog" in source_unread:
        # The same source-unread set the typed namespaces read below: an entry not read reads as deleted,
        # so the worklog line is not scanned. A worklog a rotation left merely unconfirmed is still
        # scanned: every worklog source was read, so an entry absent from them all is absent.
        rep.cant("C-NO-DELETION: {}; worklog no-deletion is not evaluated over a partial set (spec "
                 "12/13)".format(_WORKLOG_UNREAD))
    elif isinstance(wl_hw, int) and not isinstance(wl_hw, bool) and wl_hw >= 0:
        max_present = max(present_wl[-1] if present_wl else 0, floor.get("WL", 0))
        owed = _unreserved_overhang(wl_hw, max_present, wl_skipped)
        if owed > 0:
            rep.finding("C-NO-DELETION: {} allocated worklog id(s) above WL-{} are absent from both the "
                        "active worklog and the archive (counters WL high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(owed, max_present, wl_hw))
        # The gap below the max is scanned here too, from the floor: C-CONTIGUITY tiles from WL-1 and knows
        # no ancestral floor, so it cannot own a deletion from between floor+1 and the max.
        base = floor.get("WL", 0)
        _report_undecided("C-NO-DELETION", "WL", [n for n in wl_undecided if base < n <= wl_hw], no_holdings,
                          rep)
        expected = base + 1
        for n in (n for n in present_wl if n > base):
            expected = _unreserved(expected, n, wl_skipped)
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
        ns_unread = sorted(t for t, p in enabled_types.items() if p == ns and t in source_unread)
        if ns_unread:
            # A record not read reads as deleted, so the namespace's line is not scanned. A type a rotation
            # left unconfirmed is still scanned: every source was read and counted, so an id absent from
            # them all is absent.
            rep.cant("C-NO-DELETION: the records of {} could not all be read (see C-RECORDS and "
                     "C-ARCHIVE-ENUM); {}-<n> no-deletion is not evaluated over a partial set (spec "
                     "12/13)".format(", ".join(repr(t) for t in ns_unread), ns))
            continue
        base = floor.get(ns, 0)
        # A present id at or below the ancestral floor is NOT reported by C-NO-DELETION: a
        # re-adoption restores no ancestral record, so such an id is id-REUSE (spec 8.2), not a
        # deletion; the guard leaves it to the id-space / contiguity checks (this `n > base` filter,
        # and the WL scan's above, skip it deliberately).
        nums = sorted(n for n in present_by_ns.get(ns, ()) if n > base)
        max_present = nums[-1] if nums else base
        ns_issued, ns_undecided = _split_reserved(ns, reserved.get(ns, ()), present_by_ns.get(ns, ()), holdings)
        ns_skipped = ns_issued | ns_undecided
        owed = _unreserved_overhang(hw, max_present, ns_skipped)
        if owed > 0:
            rep.finding("C-NO-DELETION: {} allocated {}-<n> id(s) above {}-{} are absent from both the "
                        "active store and the archive (counters {} high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(owed, ns, ns, max_present, ns, hw))
        _report_undecided("C-NO-DELETION", ns, [n for n in ns_undecided if base < n <= hw], no_holdings, rep)
        expected = base + 1
        for n in nums:
            expected = _unreserved(expected, n, ns_skipped)
            if n != expected:
                rep.finding("C-NO-DELETION: {}-{} is absent from both the active store and the archive (an "
                            "allocated id between 1 and {}-{} cannot be deleted; spec 12/13)".format(
                                ns, expected, ns, max_present))
                break
            expected += 1

    # The imported series owns BOTH sides per imported counter row too, on its own number line (spec 8.2/8.3),
    # with the reserved-number exemption read per imported key.
    if imported:
        _check_imported_no_deletion(imported_rows, imported_unread, high, floor,
                                    _imported_counter_namespaces(enabled_types), reserved,
                                    imp_holdings, imp_no_holdings, imported_tee)

    # --- C-IMPORTED-IDS and C-IMPORTED-PROVENANCE (an active imported series only): the per-series id
    # grading above replayed with the imported counter rows, then every preserved original the imported
    # rows name re-read against its source_sha256 (spec 8.3) -------------------------------------------
    if imported:
        rep.ran("C-IMPORTED-IDS")
        _check_imported_ids(imported_tee.emitted, counters_data, cst,
                            _imported_counter_namespaces(enabled_types), rep)
        rep.ran("C-IMPORTED-PROVENANCE")
        _check_imported_provenance(root_fd, imported_rows, imported_strays, imported_unread, rep)

    rep.ran("C-PARTITION")
    for f in check_ids_partition(active_wl_ids, archive_wl_ids):
        rep.finding("C-PARTITION: {}".format(f))
    if "worklog" in unread:
        # An entry not read can only add an id held in both locations, so one found among the ids read is
        # still reported.
        rep.cant("C-PARTITION: {}; the one-location partition is not evaluated over a partial set (spec "
                 "12)".format(_WORKLOG_UNREAD))

    rep.ran("C-CONTIGUITY")
    if not reservations_ok:
        rep.cant("C-CONTIGUITY: the id reservations did not evaluate (see C-NO-DELETION), so a reserved "
                 "worklog number cannot be told from a missing one (fail-closed)")
    if "worklog" in unread:
        # An entry not read reads as a gap, so the clean tiling is not scanned; the imported line below is
        # graded on its own leaf.
        rep.cant("C-CONTIGUITY: {}; worklog contiguity is not evaluated over a partial set (spec "
                 "6.1)".format(_WORKLOG_UNREAD))
    else:
        _report_undecided("C-CONTIGUITY", "WL",
                          [n for n in wl_undecided if present_wl and n < present_wl[-1]], no_holdings, rep)
        _check_worklog_contiguous(merged.keys(), rep, wl_skipped)
    # imported:WL tiles its own line, apart from the clean tiling above (spec 8.2): `merged` stays clean-only,
    # so clean release spans, frozen coverage and the changelog gates never read an imported entry. A number
    # an imported:WL reservation holds is skipped exactly as a reserved clean worklog number is, decided
    # against the prior imported baseline. An unread
    # imported worklog leaf is a named CANNOT-EVALUATE for this check, never a silent per-series pass.
    if imported:
        imp_wl_key = IMPORTED_PREFIX + "WL"
        if imp_wl_key in imported_unread:
            rep.cant("C-CONTIGUITY: the imported:WL imported leaf could not be read; imported worklog "
                     "contiguity (spec 8.2) is not evaluated")
        else:
            imp_wl_present = {parse_series_id(rid)[2] for key, rid, _row in imported_rows
                              if key == imp_wl_key}
            imp_wl_issued, imp_wl_undecided = _split_reserved(
                imp_wl_key, reserved.get(imp_wl_key, ()), imp_wl_present, imp_holdings)
            _report_undecided("C-CONTIGUITY", imp_wl_key,
                              [n for n in imp_wl_undecided if imp_wl_present and n < max(imp_wl_present)],
                              imp_no_holdings, rep)
            _check_imported_worklog_contiguous(imp_wl_present, rep, imp_wl_issued | imp_wl_undecided)

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

    # --- C-CONTAINMENT: whole-tree path / unmanaged-path containment (spec 14.2/11) -------------------
    rep.ran("C-CONTAINMENT")
    # The imported-series activation flag (spec 8.2/4.2) is derived inside classify_containment from
    # the SAME imported_series_active the roster read above, so no flag is forwarded here and the
    # managed set registers the imported leaves exactly where C-IMPORTED-SCHEMA and the per-series
    # id checks grade them. False for every store this tooling validates today, which C-MANIFEST
    # alone does not guarantee (it accepts the reserved 2.0.0 / homes = 2 declaration today, spec
    # 4.2): the flag stays False until activation raises both SUPPORTED_HOMES and
    # SUPPORTED_SPEC_VERSION.
    _check_containment(root_fd, machine_rel, manifest_data, import_status, rep, in_repo=in_repo)
    _check_canonical_store_files(root_fd, machine_rel, manifest_data, rep, import_status)

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
            if changelog_text is not None and "worklog" in unread:
                # The gates recompute each release's coverage over the merged worklog, so an entry not read
                # reads as absent from its release.
                rep.cant("C-CHANGELOG-GATES: {}; the changelog gates are not evaluated over a partial "
                         "set (spec 7.1/7.2)".format(_WORKLOG_UNREAD))
            elif changelog_text is not None:
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
        _check_resurrection(prior["records"], prior.get("digests") or {}, by_id, all_ids, rep, source_unread)

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
    from _opf_release import compute_span_digest, release_cut
    from _opf_store import resolve_store
    import _opf_init

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

    # The tomllib parse-site floor (tools/check_python_floor.py, tomllib leg): _prove_adoption_plan
    # disposes of RecursionError, MemoryError and the ValueError family at its parse, refusing as its
    # contract ValueError. Deep nesting feeds a real RecursionError; MemoryError shares the handler
    # (the static floor scan is its per-site guard). Regression guard: RecursionError was already in
    # the handler before the floor change, which added only MemoryError, so this case guards behaviour
    # that already held.
    try:
        _prove_adoption_plan(("x = " + "[" * 6000 + "]" * 6000).encode("utf-8"),
                             "adopt-20260917T120000Z-0123456789abcdef")
        deep_why = None
    except ValueError as exc:
        deep_why = str(exc)
    check("adoption-plan-deep-nesting-refused-as-valueerror",
          deep_why is not None and "malformed TOML" in deep_why)

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
        # The base declaration comes from the production builder (_opf_init._manifest_model), so the
        # fixture follows the tooling's constants rather than restating them.
        m = {
            "opf": dict(_opf_init._manifest_model()["opf"], layout=layout),
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
        m = {
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
        # The builders' imported-series documents for this manifest (spec 4.2/8.2): its empty imported
        # leaves and zero imported counter rows, none while its imported series is inactive.
        m.update(_opf_init.imported_leaf_documents(m["manifest.toml"]))
        m["counters.toml"]["counters"].update(
            {IMPORTED_PREFIX + ns: 0
             for ns in _opf_init.imported_counter_namespaces(m["manifest.toml"]) or ()})
        return m

    def clean_product():
        return {"VERSION": "1.1.0\n", "CHANGELOG.md": clean_changelog}

    def clean_prior():
        return {"tracked": "tracked",
                "prior": {"releases": [rel_row("1.0.0", 1, 2, dig12), rel_row("1.1.0", 3, 4, dig34)],
                          "counters_high": counters()["counters"],
                          "records": {"BI-1": ("backlog_item", "done"), "BI-2": ("backlog_item", "open"),
                                      "DN-1": ("done", "recorded"), "HO-1": ("handoff", "current")},
                          "digests": {"DN-1": _record_digest(dn(1, "BI-1"))},
                          "held": {"BI": [[1, 2]], "DN": [[1, 1]], "HO": [[1, 1]], "WL": [[1, 4]]}}}

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
        # body (a valid-id .toml with no registry row) under a deferred type is a finding, never VALID; the
        # orphan also leaves that type's population unread, so the store is CANNOT-EVALUATE (the resolution
        # checks over an incomplete population are not evaluated), with the orphan finding named beside it.
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
        check("perrecord-deferred-orphan-cannot-evaluate",
              _oro is not None and _oro.status == CANNOT_EVALUATE
              and _oro.checks.get("C-PERRECORD-RECONCILE") == "FINDING")
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
        # A registry digest taken over the earlier spelling (U+2028 and U+2029 written literally, as the
        # emitter wrote them before it escaped both) still reconciles. A digest over a naive text replacement,
        # which also rewrites the text after an escaped backslash, is no emitter's spelling: a mismatch.
        def _sha_text(text):
            return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        sep_fn = envelope("FN-1", "finding", "open", summary="a" + chr(0x2028) + "b" + chr(0x2029) + "c")
        sep_prior = _opf_emit.emit(sep_fn).replace("\\u2028", chr(0x2028)).replace("\\u2029", chr(0x2029))
        prl = copy.deepcopy(pr_machine)
        prl["finding/FN-1.toml"] = sep_fn
        prl["finding.index.toml"]["record"][0]["digest"] = _sha_text(sep_prior)
        check("per-record-prior-separator-digest-valid",
              sep_prior != _opf_emit.emit(sep_fn) and run(prl, product=pr_product, obs=pr_obs).status == VALID)
        slash_fn = envelope("FN-1", "finding", "open", summary="x\\u2028y")
        prn = copy.deepcopy(pr_machine)
        prn["finding/FN-1.toml"] = slash_fn
        prn["finding.index.toml"]["record"][0]["digest"] = _sha_text(
            _opf_emit.emit(slash_fn).replace("\\u2028", chr(0x2028)))
        prn_r = run(prn, product=pr_product, obs=pr_obs)
        check("per-record-naive-separator-digest-invalid",
              prn_r.status == INVALID and any("digest mismatch" in f for f in prn_r.findings))
        # C-HISTORY-RESURRECTION: a prior body digest taken over the earlier spelling still proves the
        # immutable body unchanged; the same digest against a changed body is still a finding.
        sep_dn = dict(dn(1, "BI-1"), summary="done" + chr(0x2028) + "now")
        hist = clean_machine()
        hist["done.index.toml"] = idx([sep_dn])
        hist_obs = copy.deepcopy(clean_prior())
        hist_obs["prior"]["digests"]["DN-1"] = _sha_text(
            _opf_emit.emit(sep_dn).replace("\\u2028", chr(0x2028)))
        hist_r = run(hist, obs=hist_obs)
        check("history-immutable-body-prior-separator-digest-valid",
              hist_r is not None and hist_r.status == VALID)
        hist["done.index.toml"] = idx([dict(sep_dn, summary="done" + chr(0x2028) + "later")])
        hist_r = run(hist, obs=hist_obs)
        check("history-immutable-body-prior-separator-digest-changed-invalid",
              hist_r is not None and any("C-HISTORY-RESURRECTION" in f and "body changed" in f
                                         for f in hist_r.findings))
        # two-field row (missing state, path) -> INVALID
        prk = copy.deepcopy(pr_machine)
        prk["finding.index.toml"]["record"][0] = {"id": "FN-1", "digest": d1}
        check("per-record-missing-keys-invalid", run(prk, product=pr_product, obs=pr_obs).status == INVALID)
        # unknown fifth key -> INVALID
        pru = copy.deepcopy(pr_machine)
        pru["finding.index.toml"]["record"][0]["extra"] = "x"
        check("per-record-unknown-key-invalid", run(pru, product=pr_product, obs=pr_obs).status == INVALID)
        # FN-1 row over an FN-9 body: the binding finding is named, the row is not seated, and the store
        # is CANNOT-EVALUATE (an unseated row leaves the type's population unread for the resolution checks).
        prb = copy.deepcopy(pr_machine)
        prb["finding/FN-1.toml"] = envelope("FN-9", "finding", "open")
        _prb_run = run(prb, product=pr_product, obs=pr_obs)
        check("per-record-id-binding-fails-closed",
              _prb_run is not None and _prb_run.status == CANNOT_EVALUATE
              and any("does not match record-file id" in f for f in _prb_run.findings))
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
        # The section 8.6 firewall over the declared views: this engine has no actionability or
        # decision-register join of its own, so C-VIEW-DRIFT grades those obligations by holding each
        # declared view to the views planner, whose joins exclude importer-authored records
        # (_FIREWALL_RESIDUALS names that reach). On each store below the committed view is first the
        # planner's own text, which certifies, then the stale text a planner whose provenance predicate
        # pair admits every record renders, a C-VIEW-DRIFT finding: an importer-authored active block
        # scoping the maintainer item BI-2 hides it from TODO (red when the planner's actionability join
        # reads importer-authored blocks), an importer-authored open BI-2 is listed in TODO as actionable
        # (red when is_actionable drops its provenance predicate), and an importer-authored PD-2 that
        # supersedes the maintainer PD-1 is listed in DECISIONS as the effective resolution (red when
        # render_decisions and join_resolution admit importer-authored decisions).
        def _vd_plan(root, name):
            vres = resolve_store(root)
            vfd = _open_store_root_fd(vres.store_root, vres.pointer_source != "default")
            try:
                vplanned = _opf_views.plan_views(vfd, vres.machine_rel)
            finally:
                os.close(vfd)
            return {n: t for n, _s, _d, t in vplanned}.get(name)

        def _vd_unfiltered(root, name):
            vsaved = _opf_views._FILTER_PREDICATES
            _opf_views._FILTER_PREDICATES = dict(vsaved, importer_authored=lambda: (lambda r: False),
                                                 not_importer_authored=lambda: (lambda r: True))
            try:
                return _vd_plan(root, name)
            finally:
                _opf_views._FILTER_PREDICATES = vsaved

        def _vd_drift(machine, name, sources):
            vm = copy.deepcopy(machine)
            vm["manifest.toml"] = base_manifest(views={name: {
                "kind": "composed", "sources": list(sources), "target": ".working/" + name}})
            vroot = build(vm, clean_product())
            vtext, vstale = _vd_plan(vroot, name), _vd_unfiltered(vroot, name)
            (vroot / ".working" / name).write_text(vtext, encoding="utf-8")
            vgood = validate_store(resolve_store(vroot), observations=clean_prior())
            (vroot / ".working" / name).write_text(vstale, encoding="utf-8")
            vbad = validate_store(resolve_store(vroot), observations=clean_prior())
            return (vgood is not None and vgood.status == VALID
                    and all(x in vgood.residuals for x in _FIREWALL_RESIDUALS)
                    and vbad is not None and vbad.status == INVALID
                    and {c for c, v in vbad.checks.items() if v != "PASS"} == {"C-VIEW-DRIFT"}
                    and vbad.by_check.get("C-VIEW-DRIFT")
                    == ["C-VIEW-DRIFT: view {!r} target {!r} has drifted from its generated source (spec "
                        "5.8/10)".format(name, ".working/" + name)])

        vm = clean_machine()
        vm["block.index.toml"] = idx([envelope("BL-1", "block", "active", scopes=["BI-2"],
                                               actor={"kind": "importer"})])
        vm["counters.toml"] = counters(BL=1)
        check("view-drift-importer-block-grants-no-stop", _vd_drift(vm, "TODO.md", ("backlog_item", "block")))
        vm = clean_machine()
        vm["backlog_item.index.toml"] = idx([bi(1, "done"), dict(bi(2, "open"), actor={"kind": "importer"})])
        check("view-drift-importer-item-not-actionable", _vd_drift(vm, "TODO.md", ("backlog_item", "block")))
        vm = clean_machine()
        vm["pending_decision.index.toml"] = idx([pd(1), dict(pd(2, supersedes="PD-1"), actor={"kind": "importer"})])
        vm["counters.toml"] = counters(PD=2)
        check("view-drift-importer-decision-not-effective",
              _vd_drift(vm, "DECISIONS.md", ("pending_decision", "autonomous_decision", "maintainer_decision",
                                             "preference_pattern")))

        # --- topology (tracked / sync / containment) ------------------------------------------------
        check("untracked-invalid",
              run(clean_machine(), obs={"tracked": "untracked", "prior": clean_prior()["prior"]}).status == INVALID)
        junk = run(clean_machine(), working={"junk.md": "x\n"})
        check("undeclared-file-invalid", junk.status == INVALID)
        check("undeclared-file-named", any("C-CONTAINMENT" in f for f in junk.findings))
        # The canonical-bytes leg: a machine-store TOML file that still parses but is not the canonical
        # emission of its own content (a comment appended to the manifest, a trailing blank line in a nested
        # archive file) is a named C-CONTAINMENT finding, while a file holding a literal U+2028 inside a basic
        # string, the spelling an earlier emitter wrote, still passes. Flip: dropping the leg's call from the
        # driver turns both findings VALID.
        _canon = "is not in canonical emitted form"
        _cm = clean_machine()
        _cm["manifest.toml"] = _opf_emit.emit(_cm["manifest.toml"]) + "# a hand comment\n"
        _cmr = run(_cm)
        check("canonical-manifest-comment-invalid", _cmr is not None and _cmr.status == INVALID)
        check("canonical-manifest-comment-named",
              _cmr is not None and _cmr.checks.get("C-CONTAINMENT") == "FINDING"
              and len(_cmr.findings) == 1 and "'.working/toml/manifest.toml'" in _cmr.findings[0]
              and _canon in _cmr.findings[0])
        _ca = clean_machine()
        _ca["archive/2026/worklog.toml"] = _opf_emit.emit(_ca["archive/2026/worklog.toml"]) + "\n"
        _car = run(_ca)
        check("canonical-archive-blank-line-named",
              _car is not None and _car.status == INVALID
              and any("'.working/toml/archive/2026/worklog.toml'" in m and _canon in m
                      for m in _car.by_check.get("C-CONTAINMENT", [])))
        _cl = clean_machine()
        _cl_dn = dict(dn(1, "BI-1"), summary="done" + chr(0x2028) + "now")
        _cl_text = _opf_emit.emit(idx([_cl_dn]))
        _cl["done.index.toml"] = _cl_text.replace("\\u2028", chr(0x2028))
        _cl_obs = copy.deepcopy(clean_prior())
        _cl_obs["prior"]["digests"]["DN-1"] = _record_digest(_cl_dn)
        _clr = run(_cl, obs=_cl_obs)
        check("canonical-prior-separator-spelling-valid",
              _cl["done.index.toml"] != _cl_text and _clr is not None and _clr.status == VALID)
        # One content read per path: the leg proves the manifest and each worklog ledger from the bytes
        # the grading checks already read (rep.toml_reads), so one validation reads each of those files'
        # content exactly once. Flip: dropping the byte reuse in the leg, or the worklog read routing in
        # _gather_worklog, makes a second read appear here.
        _rcres = resolve_store(build(clean_machine()))
        _rcounts = {}
        _real_bytes_read = _opf_store._read_store_bytes_contained

        def _counting_bytes_read(fd, relpath, allowance=None):
            _rcounts[relpath] = _rcounts.get(relpath, 0) + 1
            return _real_bytes_read(fd, relpath, allowance)

        _opf_store._read_store_bytes_contained = _counting_bytes_read
        try:
            _rcv = validate_store(_rcres, observations=clean_prior())
        finally:
            _opf_store._read_store_bytes_contained = _real_bytes_read
        check("canonical-single-read-per-path",
              _rcv is not None and _rcv.status == VALID
              and _rcounts.get(".working/toml/manifest.toml") == 1
              and _rcounts.get(".working/toml/worklog.toml") == 1
              and _rcounts.get(".working/toml/archive/2026/worklog.toml") == 1)
        # init.toml's ONLY reader is this leg, so its faults are graded here rather than left to a check
        # that never comes: a canonical file passes, an unparseable one is a named finding, an unreadable
        # one a named CANNOT-EVALUATE. Flip: the former silent "graded elsewhere" skip read all three as
        # the canonical case.
        _ciok = clean_machine()
        _ciok["init.toml"] = {"schema": 1}
        _ciokr = run(_ciok)
        check("canonical-init-canonical-valid", _ciokr is not None and _ciokr.status == VALID)
        _cibad = clean_machine()
        _cibad["init.toml"] = "= not toml\n"
        _cibadr = run(_cibad)
        check("canonical-init-unparseable-named",
              _cibadr is not None and _cibadr.status == INVALID
              and any("'.working/toml/init.toml'" in m and "cannot be parsed" in m
                      for m in _cibadr.by_check.get("C-CONTAINMENT", [])))
        _cird = clean_machine()
        _cird["init.toml"] = {"schema": 1}
        _real_bytes_read2 = _opf_store._read_store_bytes_contained

        def _failing_bytes_read(fd, relpath, allowance=None):
            if relpath.endswith("/init.toml"):
                raise StoreError("synthetic unreadable init.toml")
            return _real_bytes_read2(fd, relpath, allowance)

        _opf_store._read_store_bytes_contained = _failing_bytes_read
        try:
            _cirdr = run(_cird)
        finally:
            _opf_store._read_store_bytes_contained = _real_bytes_read2
        check("canonical-init-unreadable-cant",
              _cirdr is not None and _cirdr.status == CANNOT_EVALUATE
              and any("'.working/toml/init.toml'" in m and "cannot be read" in m
                      for m in _cirdr.cannot_evaluate))
        # The emitter-refusal finding: parseable content the canonical emitter cannot write (a nested
        # array) is a named finding, since no sanctioned writer could have produced the file. Flip:
        # reducing the EmitError branch to a bare continue drops the named finding.
        _ce = clean_machine()
        _ce["emitref.toml"] = "a = [[1]]\n"
        _cer = run(_ce)
        check("canonical-emitter-refusal-named",
              _cer is not None and _cer.status == INVALID
              and any("'.working/toml/emitref.toml'" in m and "canonical emitter cannot write" in m
                      for m in _cer.by_check.get("C-CONTAINMENT", [])))
        # The skip list: a TOML file under a VALID [unmanaged] declaration, and the legacy imports
        # staging interior, are _check_containment's to grade, never canonical-bytes findings. Flip:
        # emptying the skip list turns the unmanaged store INVALID and names the staging interior file.
        _cun = clean_machine()
        _cun["manifest.toml"] = base_manifest()
        _cun["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/vendorarea"]}
        _cun["vendorarea/notes.toml"] = "a = 1 # a hand comment\n"
        _cunr = run(_cun)
        check("canonical-unmanaged-skip-valid", _cunr is not None and _cunr.status == VALID)
        _cst = clean_machine()
        _cst["imports/old/run.toml"] = "a = 1 # a hand comment\n"
        _cstr = run(_cst)
        check("canonical-legacy-staging-skip-ungraded",
              _cstr is not None
              and any("'.working/toml/imports'" in m for m in _cstr.findings + _cstr.cannot_evaluate)
              and not any("imports/old/run.toml" in m for m in _cstr.findings))
        # A non-canonical stray at steady state is still a canonical finding, but one that never advises
        # restoring an integration-base version the stray does not have; a registered file (the manifest
        # vector above) keeps the restore advice.
        _cj = clean_machine()
        _cj["junk.toml"] = "a = 1 # a hand comment\n"
        _cjr = run(_cj)
        check("canonical-stray-no-base-advice",
              _cjr is not None and _cjr.status == INVALID
              and any("'.working/toml/junk.toml'" in m and _canon in m
                      and "integration base" not in m and "no base version" in m
                      for m in _cjr.by_check.get("C-CONTAINMENT", []))
              and any("unregistered path '.working/toml/junk.toml'" in m for m in _cjr.findings))
        # A partial import SUBSTANTIATED by an active imports/<run-id> run triages stray paths (VALID):
        # the stray junk and the active run interior both land in triage, never a finding (codex-2).
        RUNID = "imp-20260601T000000Z-0123456789abcdef"
        pm = clean_machine()
        pm["manifest.toml"] = base_manifest()
        pm["manifest.toml"]["opf"]["import_status"] = "partial"
        partial = run(pm, working={"junk.md": "x", "imports/{}/plan.toml".format(RUNID): "schema = 1"})
        check("partial-import-triage-not-finding",
              partial.status == VALID and any("junk.md" in t for t in partial.triage))
        # The same substantiated partial with a NON-CANONICAL stray TOML file in the machine store: the
        # containment walk only TRIAGES the stray, so the canonical-bytes leg leaves it alone too and the
        # store stays VALID with the stray in triage. Flip: dropping the leg's import-status exemption
        # turns this store INVALID on the triaged path.
        _cp = clean_machine()
        _cp["manifest.toml"] = base_manifest()
        _cp["manifest.toml"]["opf"]["import_status"] = "partial"
        _cp["legacy.toml"] = "a = 1 # a hand comment\n"
        _cpr = run(_cp, working={"junk.md": "x", "imports/{}/plan.toml".format(RUNID): "schema = 1"})
        check("canonical-partial-stray-triaged-valid",
              _cpr is not None and _cpr.status == VALID
              and any("'.working/toml/legacy.toml'" in t for t in _cpr.triage))
        # The exemption covers only what those walks triage. Under the same substantiated partial, a
        # bucket's worklog.toml and archive.toml are graded by _validate_archive, never triaged, so a
        # non-canonical one is still the named canonical-bytes finding, while a non-index stray file in a
        # bucket (an _archive_unmanaged path) stays in triage and the store VALID. Flip: widening the
        # exemption back to every file outside the classifier's managed set turns the first two VALID;
        # dropping it turns the third INVALID.
        def _partial_archive(rel, edit):
            pa = clean_machine()
            pa["manifest.toml"] = base_manifest()
            pa["manifest.toml"]["opf"]["import_status"] = "partial"
            pa[rel] = edit(pa.get(rel))
            return run(pa, working={"imports/{}/plan.toml".format(RUNID): "schema = 1"})

        for label, rel, edit in (
                ("worklog-blank-line", "archive/2026/worklog.toml", lambda m: _opf_emit.emit(m) + "\n"),
                ("manifest-comment", "archive/2026/archive.toml",
                 lambda m: _opf_emit.emit(m) + "# a hand comment\n")):
            _par = _partial_archive(rel, edit)
            check("canonical-partial-archive-{}-invalid".format(label),
                  _par is not None and _par.status == INVALID
                  and any("'.working/toml/{}'".format(rel) in m and _canon in m
                          and "integration base" in m
                          for m in _par.by_check.get("C-CONTAINMENT", [])))
        _pas = _partial_archive("archive/2026/notes.toml", lambda _m: "a = 1 # a hand comment\n")
        check("canonical-partial-archive-stray-triaged-valid",
              _pas is not None and _pas.status == VALID
              and any("'.working/toml/archive/2026/notes.toml'" in t for t in _pas.triage))
        # The archive scope itself, path by path: a bucket's manifest, worklog and index (in any bucket
        # name) are graded; a file directly under archive/, a non-index bucket file and anything beneath a
        # bucket subdirectory are the _archive_unmanaged paths.
        _ar = ".working/toml/archive"
        check("canonical-archive-triage-scope",
              not any(_archive_triaged_file(_ar, _ar + "/" + p) for p in (
                  "2026/archive.toml", "2026/worklog.toml", "2026/finding.index.toml", "x/worklog.toml",
                  "2026/junk.index.toml"))
              and all(_archive_triaged_file(_ar, _ar + "/" + p) for p in (
                  "notes.toml", "2026/notes.toml", "2026/sub/archive.toml", "2026/sub/deeper/worklog.toml",
                  "2026/archive.toml.toml")))
        # A stray DIRECTORY under the same substantiated partial: the containment walk grades an
        # unregistered directory, and the archive walk a bucket subdirectory, as one triaged path and never
        # lists it, so the leg does not enter it either. A symlink inside it, a chain deeper than the walk
        # ceiling, or a listing failure leaves the store VALID with the directory in triage. Flip: entering
        # every directory again turns each of these CANNOT-EVALUATE.
        def _partial_stray_dir(rel, shape):
            sd = clean_machine()
            sd["manifest.toml"] = base_manifest()
            sd["manifest.toml"]["opf"]["import_status"] = "partial"
            sd[rel + "/notes.toml"] = "a = 1 # a hand comment\n"
            sd_root = build(sd, working={"imports/{}/plan.toml".format(RUNID): "schema = 1"})
            sd_dir = sd_root / ".working" / "toml" / rel
            if shape == "symlink":
                os.symlink("nowhere", str(sd_dir / "link"))
            elif shape == "deep":
                for _ in range(_CONTAINMENT_MAX_DEPTH + 16):
                    sd_dir = sd_dir / "d"
                sd_dir.mkdir(parents=True)
            sd_res = resolve_store(sd_root)
            if shape != "unlistable":
                return validate_store(sd_res, observations=clean_prior())
            from unittest.mock import patch as _sd_patch
            sd_rel, real_list = ".working/toml/" + rel, _list_contained

            def refused_list(fd, reldir):
                if reldir == sd_rel:
                    raise StoreError("synthetic unlistable stray directory")
                return real_list(fd, reldir)

            with _sd_patch.dict(globals(), {"_list_contained": refused_list}):
                return validate_store(sd_res, observations=clean_prior())

        for label, rel, shape in (
                ("stray-dir-symlink", "stray", "symlink"), ("stray-dir-deep", "stray", "deep"),
                ("stray-dir-unlistable", "stray", "unlistable"),
                ("bucket-subdir-symlink", "archive/2026/stray", "symlink"),
                ("bucket-subdir-deep", "archive/2026/stray", "deep"),
                ("bucket-subdir-unlistable", "archive/2026/stray", "unlistable")):
            _psd = _partial_stray_dir(rel, shape)
            check("canonical-partial-{}-valid".format(label),
                  _psd is not None and _psd.status == VALID and not _psd.cannot_evaluate
                  and any("'.working/toml/{}'".format(rel) in t for t in _psd.triage))
        # The directory exemption covers only those triaged directories: under the same substantiated
        # partial, a per-record type-body directory is a managed namespace the containment walk enters, so a
        # non-canonical record body inside it is still the named finding; and the archive root and each
        # bucket are listed by the archive walk, so the leg enters them too. Flip: skipping every directory
        # under the partial turns the body store VALID.
        _pb = copy.deepcopy(pr_machine)
        _pb["manifest.toml"]["opf"]["import_status"] = "partial"
        _pb["finding/FN-1.toml"] = _opf_emit.emit(fn(1)) + "# a hand comment\n"
        _pbr = run(_pb, product=pr_product, obs=pr_obs,
                   working={"imports/{}/plan.toml".format(RUNID): "schema = 1"})
        check("canonical-partial-managed-dir-entered",
              _pbr is not None and _pbr.status == INVALID
              and any("'.working/toml/finding/FN-1.toml'" in m and _canon in m
                      for m in _pbr.by_check.get("C-CONTAINMENT", [])))
        check("canonical-archive-dir-triage-scope",
              not any(_archive_triaged_dir(_ar, _ar + p) for p in ("", "/2026", "/x"))
              and all(_archive_triaged_dir(_ar, _ar + "/" + p)
                      for p in ("2026/sub", "2026/sub/deeper", "x/sub")))
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

        def vanishing_reader(fd, relpath, **kwargs):
            if relpath.endswith(gone):
                return None
            return real_reader(fd, relpath, **kwargs)

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
        # DERIVED activation. ACTIVE (the activated 2.0.0 / homes = 2 store, both tooling gates patched
        # inside the vector): worklog.imported.toml and each enabled non-ledger type
        # <type>.imported.index.toml are managed leaves DIRECTLY in the machine store; the importer-only
        # legacy_fragment contributes none (its LF namespace is outside the imported series), and a
        # deeper path of a leaf name stays a stray. The pure legs drive classify_containment directly,
        # each as the bare two-argument call (the classification derives the activation from the
        # manifest itself; a caller-supplied flag with an inactive default let an omitting consumer
        # grade an activated store's imported leaves as strays); the full-doctor legs below cover
        # the INACTIVE grading (the 1.2.0 store and the accepted 2.0.0 / homes = 2 reservation) and
        # the ACTIVATED grading end to end through the driver.
        _is2_m = base_manifest()
        _is2_m["types"]["legacy_fragment"] = {"namespace": "LF"}
        _is2_act_m = copy.deepcopy(_is2_m)
        _is2_act_m["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
        _is2_act_m["opf"]["homes"] = 2
        _is2_prev_gates = (_opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION)
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            _is2_on = classify_containment(_is2_act_m, ".working/toml")
        finally:
            _opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION = _is2_prev_gates
        # The omission vector: the bare call on an ACTIVATED manifest yields the ACTIVATED
        # classification (red on a caller-supplied activation flag whose default is inactive: this
        # exact call then grades the imported leaves as unregistered strays).
        check("containment-imported-derived-on-omission-activated",
              _is2_on.homes == 2
              and _is2_on.managed_file(".working/toml/worklog.imported.toml"))
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
        # INACTIVE (the derived activation False at the SHIPPED constants, every 1.2.0 store): the same
        # names stay unregistered strays, pure and full-doctor alike, so an old imported leaf cannot
        # register itself before activation (red here if the derivation ever admits them early).
        _is2_off = classify_containment(_is2_m, ".working/toml")
        check("containment-imported-leaf-unmanaged-when-inactive",
              not _is2_off.managed_file(".working/toml/worklog.imported.toml")
              and not _is2_off.managed_file(".working/toml/backlog_item.imported.index.toml"))
        # The paired controls for the omission vector above, the SAME bare call: the 2.0.0 / homes = 2
        # pair derives an INACTIVE series both at the shipped constants (the accepted reservation) and
        # with SUPPORTED_HOMES alone raised (support still below IMPORTED_SPEC_VERSION), so the
        # derivation requires BOTH tooling gates, never the declaration or one gate alone.
        _is2_res = classify_containment(_is2_act_m, ".working/toml")
        _is2_prev_h = _opf_store.SUPPORTED_HOMES
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _is2_half = classify_containment(_is2_act_m, ".working/toml")
        finally:
            _opf_store.SUPPORTED_HOMES = _is2_prev_h
        check("containment-imported-derived-control-inactive",
              _is2_res.homes == 1
              and not _is2_res.managed_file(".working/toml/worklog.imported.toml")
              and _is2_half.homes == 2
              and not _is2_half.managed_file(".working/toml/worklog.imported.toml"))
        # The activation can no longer be supplied (or starved) by a caller at all: a keyword or a
        # third positional argument is a TypeError, so a stale flag cannot override the
        # manifest-derived activation and the former omission divergence cannot be reintroduced
        # without reddening here.
        def _is2_refused(thunk):
            try:
                thunk()
            except TypeError:
                return True
            return False
        check("containment-activation-argument-refused",
              _is2_refused(lambda: classify_containment(_is2_m, ".working/toml", imported=True))
              and _is2_refused(lambda: classify_containment(_is2_m, ".working/toml", True)))
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
        # strays there too (red if imported_series_active ever reads the declaration alone), and
        # the clean pair itself stays VALID (the planted leaves, never the declaration, cause the
        # findings; the planted bytes are not even TOML, so a VALID verdict would certify files
        # nothing read).
        _is2_h2 = clean_machine()
        _is2_h2["manifest.toml"]["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
        _is2_h2["manifest.toml"]["opf"]["homes"] = 2
        _is2_h2_clean = run(copy.deepcopy(_is2_h2))
        check("containment-homes2-pair-clean-valid",
              _is2_h2_clean is not None and _is2_h2_clean.status == VALID)
        # At the shipped constants the 2.0.0 / homes = 2 pair grades at generation 1, so its
        # .working/.gitignore is an unregistered stray like any other unenumerated path (red when the
        # control-file registration ignores the graded generation).
        _is2_gi_legacy = run(copy.deepcopy(_is2_h2),
                             working={".gitignore": _opf_store.render_homes_gitignore()})
        check("containment-gitignore-stray-at-generation-1",
              _is2_gi_legacy is not None and _is2_gi_legacy.status == INVALID
              and any("C-CONTAINMENT" in f and ".working/.gitignore" in f and "unregistered" in f
                      for f in _is2_gi_legacy.findings))
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
        # ACTIVATED full-doctor fixtures. The imported series activates only inside the 2.0.0 / homes = 2
        # declaration (imported_series_active), so BOTH tooling gates are patched inside the vector,
        # SUPPORTED_HOMES to 2 and SUPPORTED_SPEC_VERSION to IMPORTED_SPEC_VERSION (the per-vector patch
        # idiom check_opf_homes uses for SUPPORTED_HOMES), and the store declares the 2.0.0 pair, so the
        # driver-derived flag is True end to end. act_machine() is that store with every imported leaf
        # present and empty: one per enabled type except legacy_fragment (spec 4.2), the worklog's as
        # worklog.imported.toml, plus the zero high-water every imported counter needs once active
        # (spec 8.2). _is2_prior() is the prior with the empty imported baseline the across-time
        # checks need on an active store.
        def act_machine():
            m = clean_machine()
            m["manifest.toml"]["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
            m["manifest.toml"]["opf"]["homes"] = 2
            for leaf in _opf_store.imported_leaf_names(m["manifest.toml"]["types"]):
                m[leaf] = ({"schema": 1, "entry": []} if leaf == _opf_store.IMPORTED_WORKLOG_NAME
                           else idx([]))
            m["counters.toml"]["counters"].update(
                {"imported:" + ns: 0 for ns in sorted(set(BASELINE_TYPES.values()))})
            return m

        def _is2_prior():
            o = clean_prior()
            o["prior"]["imported"] = {}
            return o

        # Imported rows in the closed imported envelope (spec 8.3), each VALID under
        # validate_imported_record as built: a backlog item, and a worklog entry whose omitted `detail`
        # carries its missingness row. `over` replaces or adds fields. Every row names the one preserved
        # original IMP_SOURCE at its real digest; imp_evidence() plants that original and the
        # inventory.toml claiming it, so C-IMPORTED-PROVENANCE re-reads real bytes and C-EVIDENCE-ENUM
        # reconciles the bundle (spec 4.2/8.3). `original` replaces the planted bytes only, leaving the
        # inventory consistent with them, so a changed original is the provenance finding alone.
        IMP_RUN = "imp-20260601T000000Z-0123456789abcdef"
        IMP_SOURCE = ".working/imported/import/{}/originals/TODO.md".format(IMP_RUN)
        IMP_ORIGINAL = "- [ ] an imported backlog item\n"
        IMP_SHA256 = hashlib.sha256(IMP_ORIGINAL.encode("utf-8")).hexdigest()

        def imp_evidence(original=IMP_ORIGINAL):
            raw = original.encode("utf-8")
            bundle = _opf_store.evidence_run("import", IMP_RUN)[len(WORKING_DIRNAME) + 1:]
            return {IMP_SOURCE[len(WORKING_DIRNAME) + 1:]: original,
                    bundle + "/inventory.toml": {"format": EVIDENCE_FORMAT, "file": [
                        {"path": IMP_SOURCE, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]}}

        def act_run(machine, obs=None, working=None):
            return run(machine, working=dict(imp_evidence(), **(working or {})),
                       obs=_is2_prior() if obs is None else obs)

        def imp_row(rid, rtype, status, **over):
            r = {"id": rid, "type": rtype, "status": status, "title": "t",
                 "actor": {"kind": "importer", "id": "import-assistant"},
                 "import": {"source": IMP_SOURCE, "source_sha256": IMP_SHA256, "run": IMP_RUN,
                            "imported_at": TS}}
            r.update(over)
            return r

        def imp_bi(n, status="open", **over):
            r = imp_row("imported:BI-{}".format(n), "backlog_item", status, created_at=TS, updated_at=TS)
            r.update(over)
            return r

        def imp_wl(n, **over):
            r = imp_row("imported:WL-{}".format(n), "worklog", "recorded", date=TS, kind="added",
                        summary="w{}".format(n),
                        unrecorded=[{"field": "detail", "reason": "not_recorded_in_source"}])
            r.update(over)
            return r

        # The imported checks this release grades (all four of spec 8.3); any OTHER id of IMPORTED_CHECKS
        # would never run, so on an active store result()'s roster reconciliation would route it to
        # CANNOT-EVALUATE. pending_only(res) holds when those reconciliation cants are the ONLY cants
        # (with every check landed, when there is no cant at all): an active store never certifies on a
        # partial imported roster, and nothing else is uncertain.
        _imp_landed = ("C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE", "C-IMPORTED-SEGREGATION")
        _imp_pending = tuple(cid for cid in IMPORTED_CHECKS if cid not in _imp_landed)

        def pending_only(res):
            return (res is not None and len(res.cannot_evaluate) == len(_imp_pending)
                    and all(res.checks.get(cid) == "CANNOT-EVALUATE"
                            and any(repr(cid) in m and "did not run" in m for m in res.cannot_evaluate)
                            for cid in _imp_pending))

        def imp_msgs(res):
            return res.by_check.get("C-IMPORTED-SCHEMA", []) if res is not None else []

        _is2_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        _is2_prev_homes = _opf_store.SUPPORTED_HOMES
        _is3_g = globals()
        _is3_prev_roster = IMPORTED_CHECKS
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The clean activated store with every imported leaf present REGISTERS them: no
            # unregistered-stray finding (red when the derivation inside classify_containment is
            # removed or weakened, because the leaves then grade as unregistered strays).
            # C-IMPORTED-SCHEMA reads the empty leaves and passes, and the only cants are the
            # reconciliation's for any imported check not landed, so the activated store is never
            # VALID on a partial imported roster and is VALID once all four land. The prior carries
            # the empty imported baseline the across-time checks require once active.
            _is2_act = act_machine()
            _is2_act_run = run(copy.deepcopy(_is2_act), obs=_is2_prior())
            check("containment-imported-leaf-registered-when-activated",
                  _is2_act_run is not None and not _is2_act_run.findings
                  and _is2_act_run.status == (CANNOT_EVALUATE if _imp_pending else VALID)
                  and _is2_act_run.checks.get("C-IMPORTED-SCHEMA") == "PASS"
                  and pending_only(_is2_act_run))
            # Homes 2 registers .working/.gitignore as a managed control file (spec 4.2) and grades
            # none of its content: a drifted managed block is no C-CONTAINMENT finding (red when the
            # registration is dropped, the file then being an unregistered stray, and red when drift
            # grading is added here, since the writer's post-write check owns drift).
            _is2_gi_run = run(copy.deepcopy(_is2_act), obs=_is2_prior(), working={
                ".gitignore": "# adopter\n# >>> opf-managed >>>\n/imported/\n# <<< opf-managed <<<\n"})
            check("containment-homes2-gitignore-content-not-graded",
                  _is2_gi_run is not None and not _is2_gi_run.findings
                  and _is2_gi_run.checks.get("C-CONTAINMENT") == "PASS" and pending_only(_is2_gi_run))
            # Homes 2 keeps adoption evidence at .working/imported/adoption/ (spec 4.2), and its
            # grading on base 2.0.0 has not landed, so a store holding that home, even empty, is
            # C-CONTAINMENT CANNOT-EVALUATE (red when the branch is removed: the evidence-root skip
            # passes the home unread and the store reads VALID). The same store without the home
            # passes C-CONTAINMENT and stays VALID (red when the branch fires on the store alone).
            _is2_ad_root = build(copy.deepcopy(_is2_act))
            (_is2_ad_root / WORKING_DIRNAME / "imported" / "adoption").mkdir(parents=True)
            _is2_ad_res = resolve_store(_is2_ad_root)
            _is2_ad_run = (validate_store(_is2_ad_res, observations=_is2_prior())
                           if _is2_ad_res.status == RESOLVED else None)
            check("containment-homes2-adopting-cannot-evaluate",
                  _is2_ad_run is not None and _is2_ad_run.status == CANNOT_EVALUATE
                  and not _is2_ad_run.findings
                  and _is2_ad_run.checks.get("C-CONTAINMENT") == "CANNOT-EVALUATE"
                  and any(m == "C-CONTAINMENT: the adoption home .working/imported/adoption/ exists, "
                               "and adoption grading on base 2.0.0 is not yet implemented (spec 4.2; "
                               "lands with the adoption-journal move)"
                          for m in _is2_ad_run.cannot_evaluate))
            # A regular file of that name is found by the listing's file arm and is the same
            # C-CONTAINMENT CANNOT-EVALUATE (red when the file arm is dropped: the file is then only an
            # off-inventory C-EVIDENCE-ENUM finding, and C-CONTAINMENT passes).
            _is2_adf_run = run(copy.deepcopy(_is2_act), obs=_is2_prior(),
                               working={"imported/adoption": "not a directory\n"})
            check("containment-homes2-adoption-file-cannot-evaluate",
                  _is2_adf_run is not None
                  and _is2_adf_run.checks.get("C-CONTAINMENT") == "CANNOT-EVALUATE"
                  and any(m == "C-CONTAINMENT: the adoption home .working/imported/adoption/ exists, "
                               "and adoption grading on base 2.0.0 is not yet implemented (spec 4.2; "
                               "lands with the adoption-journal move)"
                          for m in _is2_adf_run.cannot_evaluate))
            _is2_noad_run = run(copy.deepcopy(_is2_act), obs=_is2_prior())
            check("containment-homes2-no-adoption-home-pass",
                  _is2_noad_run is not None and not _is2_noad_run.findings
                  and _is2_noad_run.checks.get("C-CONTAINMENT") == "PASS"
                  and _is2_noad_run.status == (CANNOT_EVALUATE if _imp_pending else VALID)
                  and pending_only(_is2_noad_run))
            # The roster of an activated 2.0.0 store is the homes-2 roster plus the four imported
            # checks (spec 4.2 / 8.3), in that order, and the report carries exactly it: the three not
            # yet landed are CANNOT-EVALUATE by reconciliation, never silently absent (red when the
            # roster drops the imported ids, or a check is skipped without its reconciliation).
            _h2_at = REQUIRED_CHECKS.index("C-ARCHIVE-ENUM") + 1
            _h2_roster = REQUIRED_CHECKS[:_h2_at] + HOMES2_CHECKS + REQUIRED_CHECKS[_h2_at:]
            check("imported-roster-includes-four-checks-when-active",
                  IMPORTED_CHECKS == ("C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE",
                                      "C-IMPORTED-SEGREGATION")
                  and required_checks(2, True) == _h2_roster + IMPORTED_CHECKS
                  and required_checks(2) == _h2_roster and required_checks(1) == REQUIRED_CHECKS
                  and _is2_act_run is not None
                  and tuple(_is2_act_run.checks) == required_checks(2, True)
                  and pending_only(_is2_act_run))
            # The leaf roster the classification derives is the ENABLED types, never the full
            # section-8.1 roster: a disabled module type (release is not enabled here) gets no leaf,
            # so its imported index stays an unregistered stray even while activated (red if the
            # imported_leaf_names call is ever fed the full roster instead of enabled_types). The
            # unregistered finding itself makes the status not-VALID and is also the discriminating
            # assertion.
            _is2_act["release.imported.index.toml"] = idx([])
            _is2_dis_run = run(_is2_act, obs=_is2_prior())
            check("containment-disabled-type-imported-leaf-stray-when-activated",
                  _is2_dis_run is not None and _is2_dis_run.status != VALID
                  and any("C-CONTAINMENT" in f and "release.imported.index.toml" in f
                          and "unregistered" in f for f in _is2_dis_run.findings))
            # Writer/doctor single authority (F10-1): plan_write_scope reads the SAME
            # classify_containment, which derives the SAME activation from the manifest, so on an
            # activated manifest whose [unmanaged] declaration names an imported leaf BOTH refuse the
            # collision, naming the same finding (red when either consumer could read the series as
            # inactive: it would then plan writes over a declaration the doctor flags as colliding).
            import _opf_write_guard
            _is2_wg_m = base_manifest()
            _is2_wg_m["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
            _is2_wg_m["opf"]["homes"] = 2
            _is2_wg_m["unmanaged"] = dict(paths=[".working/toml/worklog.imported.toml"])
            _is2_wg_cls = classify_containment(_is2_wg_m, ".working/toml")
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
            # verbatim (red when the write guard's classification could read the series as
            # inactive while the doctor's reads it as active: the writer would then plan
            # writes over a declaration the doctor flags as colliding).
            _is2_agx = act_machine()
            _is2_agx["manifest.toml"]["unmanaged"] = dict(
                paths=[".working/toml/worklog.imported.toml"])
            _is2_agx_run = run(copy.deepcopy(_is2_agx), obs=_is2_prior())
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
            # The garbage-leaf probe, fail-closed: both imported leaves hold bytes that are not even
            # TOML, so a VALID verdict would certify files nothing read. C-IMPORTED-SCHEMA reads them
            # and routes each to a CANNOT-EVALUATE naming it (red if an activated store with unread
            # imported leaves ever certifies).
            _is2_p1 = act_machine()
            _is2_p1["worklog.imported.toml"] = "deliberately invalid TOML ["
            _is2_p1["backlog_item.imported.index.toml"] = "garbage = ["
            _is2_p1_run = run(_is2_p1, obs=_is2_prior())
            check("containment-activated-garbage-leaf-never-certified",
                  _is2_p1_run is not None and _is2_p1_run.status == CANNOT_EVALUATE
                  and _is2_p1_run.checks.get("C-IMPORTED-SCHEMA") == "CANNOT-EVALUATE"
                  and all(any("cannot read .working/toml/" + leaf in m for m in imp_msgs(_is2_p1_run))
                          for leaf in ("worklog.imported.toml", "backlog_item.imported.index.toml")))
            # The activation tripwire stays the guard against an EMPTIED imported roster: with
            # IMPORTED_CHECKS patched to () an activated store is CANNOT-EVALUATE with the tripwire's
            # own message naming the deferred C-IMPORTED-* checks (red when the tripwire is removed;
            # C-IMPORTED-SCHEMA then runs as an unknown id, a different fault this vector does not
            # accept in its place).
            _is3_g["IMPORTED_CHECKS"] = ()
            try:
                _is2_tw_run = run(act_machine(), obs=_is2_prior())
            finally:
                _is3_g["IMPORTED_CHECKS"] = _is3_prev_roster
            check("containment-imported-tripwire-on-empty-roster",
                  _is2_tw_run is not None and _is2_tw_run.status == CANNOT_EVALUATE
                  and any("C-IMPORTED" in m and "unread" in m
                          for m in _is2_tw_run.by_check.get("C-CONTAINMENT", [])))

            # --- OPF-IMPORTED-SERIES IS-3a: C-IMPORTED-SCHEMA over the imported leaves (spec 8.3) ------
            # A conforming imported row reaches VALID end to end: C-IMPORTED-SCHEMA passes and adds no
            # cant of its own, never the _SCHEMA_DEFERRAL seam (spec 8.3), and with every imported
            # check landed the activated store carries the full roster with no cant at all, so the
            # store is VALID (red when imported rows are routed to the deferral, which reads
            # CANNOT-EVALUATE).
            _v = act_machine()
            _v["backlog_item.imported.index.toml"] = idx([imp_bi(1)])
            _v["worklog.imported.toml"] = {"schema": 1, "entry": [imp_wl(1)]}
            _v["counters.toml"]["counters"].update({"imported:BI": 1, "imported:WL": 1})
            _v_run = act_run(_v)
            check("imported-valid-row-reaches-valid",
                  _v_run is not None and not _v_run.findings
                  and _v_run.checks.get("C-IMPORTED-SCHEMA") == "PASS" and pending_only(_v_run)
                  and not any(_SCHEMA_DEFERRAL in m for m in _v_run.cannot_evaluate)
                  and _v_run.status == VALID
                  and tuple(_v_run.checks) == _h2_roster + IMPORTED_CHECKS)
            # The canonical-bytes leg over an ACTIVATED imported leaf: the same conforming store with a
            # comment appended to backlog_item.imported.index.toml is the one named canonical-bytes
            # finding, with the restore advice of a managed file, while every imported check still runs
            # to PASS. The imported id grading reads the leaves through a _TeeReport, so this is red when
            # the tee does not forward the report's read cache (the run then fails closed with every later
            # check, C-CONTAINMENT and the C-IMPORTED-* checks included, never run) and when the leg is
            # dropped from the driver (VALID).
            _vc = copy.deepcopy(_v)
            _vc["backlog_item.imported.index.toml"] = (
                _opf_emit.emit(_vc["backlog_item.imported.index.toml"]) + "# a hand comment\n")
            _vc_run = act_run(_vc)
            check("canonical-imported-leaf-comment-named-when-activated",
                  _vc_run is not None and _vc_run.status == INVALID and pending_only(_vc_run)
                  and len(_vc_run.findings) == 1
                  and "'.working/toml/backlog_item.imported.index.toml'" in _vc_run.findings[0]
                  and _canon in _vc_run.findings[0] and "integration base" in _vc_run.findings[0]
                  and _vc_run.checks.get("C-CONTAINMENT") == "FINDING"
                  and all(_vc_run.checks.get(cid) == "PASS" for cid in IMPORTED_CHECKS)
                  and tuple(_vc_run.checks) == _h2_roster + IMPORTED_CHECKS)
            # An omitted non-exempt field with no missingness row is an INVALID finding naming the
            # leaf, the row and the field (red when rows are deferred, which reads CANNOT-EVALUATE,
            # or not validated, which reads PASS).
            _mu_row = imp_bi(1)
            del _mu_row["created_at"]
            _mu = act_machine()
            _mu["backlog_item.imported.index.toml"] = idx([_mu_row])
            _mu["counters.toml"]["counters"]["imported:BI"] = 1
            _mu_run = run(_mu, obs=_is2_prior())
            check("imported-missing-unrecorded-row-finding",
                  _mu_run is not None and _mu_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any(f.startswith(".working/toml/backlog_item.imported.index.toml#1: imported "
                                       "record 'imported:BI-1': ")
                          and "'created_at'" in f and "no unrecorded row" in f for f in imp_msgs(_mu_run)))
            # An imported status never takes /proposed (spec 8.3): done/proposed is a legal clean
            # backlog_item status, so only the imported rule refuses it.
            _pr = act_machine()
            _pr["backlog_item.imported.index.toml"] = idx([imp_bi(1, status="done/proposed")])
            _pr["counters.toml"]["counters"]["imported:BI"] = 1
            _pr_run = run(_pr, obs=_is2_prior())
            check("imported-proposed-status-finding",
                  _pr_run is not None and _pr_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("takes /proposed" in f for f in imp_msgs(_pr_run)))
            # worklog.imported.toml rows are [[entry]] rows validated as the worklog type: an imported
            # worklog row's status is fixed at recorded (spec 8.3; red when the worklog leaf is read
            # under the index shape, or its rows go unvalidated).
            _wo = act_machine()
            _wo["worklog.imported.toml"] = {"schema": 1, "entry": [imp_wl(1, status="open")]}
            _wo["counters.toml"]["counters"]["imported:WL"] = 1
            _wo_run = run(_wo, obs=_is2_prior())
            check("imported-worklog-row-finding",
                  _wo_run is not None and _wo_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any(f.startswith(".working/toml/worklog.imported.toml#1: imported record "
                                       "'imported:WL-1': ") and "not a worklog state" in f
                          for f in imp_msgs(_wo_run)))
            # One unparseable leaf in an otherwise conforming store: C-IMPORTED-SCHEMA is
            # CANNOT-EVALUATE naming that leaf, and nothing is a finding (red when the leaf is not
            # read, which reads PASS).
            _up = act_machine()
            _up["backlog_item.imported.index.toml"] = "garbage = ["
            _up_run = run(_up, obs=_is2_prior())
            check("imported-leaf-unparseable-cannot-evaluate",
                  _up_run is not None and _up_run.status == CANNOT_EVALUATE and not _up_run.findings
                  and _up_run.checks.get("C-IMPORTED-SCHEMA") == "CANNOT-EVALUATE"
                  and any(m.startswith("cannot read .working/toml/backlog_item.imported.index.toml")
                          for m in imp_msgs(_up_run)))
            # An absent leaf of an enabled type is an INVALID finding (spec 4.2: every enabled type
            # but legacy_fragment holds one); a declared legacy_fragment owes none (red when the
            # absence passes, or when LF is not excluded).
            _ab = act_machine()
            del _ab["finding.imported.index.toml"]
            _ab_run = run(_ab, obs=_is2_prior())
            check("imported-leaf-absent-finding",
                  _ab_run is not None and _ab_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("'finding' has no imported leaf finding.imported.index.toml" in f
                          for f in imp_msgs(_ab_run)))
            _lf = act_machine()
            _lf["manifest.toml"]["types"]["legacy_fragment"] = {"namespace": "LF"}
            _lf["counters.toml"]["counters"]["LF"] = 0
            _lf["legacy_fragment.index.toml"] = idx([])
            _lf_run = run(_lf, obs=_is2_prior())
            check("imported-lf-owes-no-leaf",
                  _lf_run is not None and not _lf_run.findings
                  and _lf_run.checks.get("C-IMPORTED-SCHEMA") == "PASS" and pending_only(_lf_run))
            # The closed leaf shape, one vector per arm: an unknown top-level key and an absent schema
            # are findings; an unsupported schema and a rows value that is not an array are
            # CANNOT-EVALUATE (each red when its arm is removed).
            _sh = act_machine()
            _sh["backlog_item.imported.index.toml"] = dict(idx([]), colour="teal")
            _sh_run = run(_sh, obs=_is2_prior())
            check("imported-leaf-unknown-top-key-finding",
                  _sh_run is not None and _sh_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("backlog_item.imported.index.toml: unknown top-level key(s): colour" in f
                          for f in imp_msgs(_sh_run)))
            _sh = act_machine()
            _sh["worklog.imported.toml"] = {"entry": []}
            _sh_run = run(_sh, obs=_is2_prior())
            check("imported-leaf-schema-absent-finding",
                  _sh_run is not None and _sh_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("worklog.imported.toml: imported leaf is missing the required `schema` key" in f
                          for f in imp_msgs(_sh_run)))
            _sh = act_machine()
            _sh["backlog_item.imported.index.toml"] = {"schema": 2, "record": [imp_bi(1)]}
            _sh["counters.toml"]["counters"]["imported:BI"] = 1
            _sh_run = run(_sh, obs=_is2_prior())
            check("imported-leaf-unsupported-schema-cannot-evaluate",
                  _sh_run is not None and _sh_run.checks.get("C-IMPORTED-SCHEMA") == "CANNOT-EVALUATE"
                  and any("backlog_item.imported.index.toml: schema 2 is not the supported version" in m
                          for m in imp_msgs(_sh_run)))
            _sh = act_machine()
            _sh["worklog.imported.toml"] = {"schema": 1, "entry": "not an array"}
            _sh_run = run(_sh, obs=_is2_prior())
            check("imported-leaf-rows-not-array-cannot-evaluate",
                  _sh_run is not None and _sh_run.checks.get("C-IMPORTED-SCHEMA") == "CANNOT-EVALUATE"
                  and any("worklog.imported.toml: [[entry]] is not an array of tables" in m
                          for m in imp_msgs(_sh_run)))
            # A module-tier type's imported row is graded by the imported validator, which holds no
            # module schema and so refuses the type: an INVALID finding, never the module-schema
            # deferral its clean index takes (spec 8.3; red when imported rows reuse the clean
            # deferral route).
            _mt = act_machine()
            _mt_types = {"artifact": "AR", "gate_run": "GR", "release": "RL", "waiver": "WV"}
            _mt["manifest.toml"]["modules"] = {"delivery_assurance": True}
            _mt["counters.toml"]["counters"].update(
                {"AR": 0, "GR": 0, "RL": 0, "WV": 0,
                 "imported:AR": 1, "imported:GR": 0, "imported:RL": 0, "imported:WV": 0})
            for _t, _ns in _mt_types.items():
                _mt["manifest.toml"]["types"][_t] = {"namespace": _ns}
                _mt[_t + ".index.toml"] = idx([])
            for _leaf in _opf_store.imported_leaf_names(_mt_types):
                _mt[_leaf] = idx([])
            _mt["artifact.imported.index.toml"] = idx([imp_row("imported:AR-1", "artifact", "recorded",
                                                               created_at=TS, updated_at=TS)])
            _mt_run = run(_mt, obs=_is2_prior())
            check("imported-module-type-row-never-deferred",
                  _mt_run is not None and _mt_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any(f.startswith(".working/toml/artifact.imported.index.toml#1: ")
                          and "'artifact' is not a supported record type" in f for f in imp_msgs(_mt_run))
                  and not any(_SCHEMA_DEFERRAL in m for m in imp_msgs(_mt_run)))
            # The section 8.6 tripwire for the module-tier current-state joins: a clean importer-authored
            # waiver at `active` on an active store never certifies, because its type's schema is deferred
            # (red when the inline deferral cant is removed, which seats the waiver ungraded and VALID).
            # When the module schemas ship, this vector fails until their joins carry the firewall filter.
            _wv = act_machine()
            _wv["manifest.toml"]["modules"] = {"delivery_assurance": True}
            _wv["counters.toml"]["counters"].update(
                {"AR": 0, "GR": 0, "RL": 0, "WV": 1,
                 "imported:AR": 0, "imported:GR": 0, "imported:RL": 0, "imported:WV": 0})
            for _t, _ns in _mt_types.items():
                _wv["manifest.toml"]["types"][_t] = {"namespace": _ns}
                _wv[_t + ".index.toml"] = idx([])
            for _leaf in _opf_store.imported_leaf_names(_mt_types):
                _wv[_leaf] = idx([])
            _wv["waiver.index.toml"] = idx([envelope("WV-1", "waiver", "active", actor={"kind": "importer"})])
            _wv_run = run(_wv, obs=_is2_prior())
            check("imported-active-importer-waiver-not-valid",
                  _wv_run is not None and _wv_run.status != VALID
                  and any(m.startswith(".working/toml/waiver.index.toml: 1 schema-deferred record(s) of type "
                                       "'waiver'") and _SCHEMA_DEFERRAL in m for m in _wv_run.cannot_evaluate))
            # Each row is validated against its LEAF's type: a finding row planted in the backlog-item
            # leaf is refused (red when the leaf type is not passed as the expected type); a row that
            # is not a table is CANNOT-EVALUATE naming it (red when that verdict is dropped); and a
            # registered x-<vendor> table passes, the manifest's registered vendors reaching the
            # validator (red when they do not, which reads it as an unknown key).
            _ty = act_machine()
            _ty["backlog_item.imported.index.toml"] = idx([imp_row("imported:FN-1", "finding", "open",
                                                                   created_at=TS, updated_at=TS)])
            _ty_run = run(_ty, obs=_is2_prior())
            check("imported-row-type-must-match-leaf",
                  _ty_run is not None and _ty_run.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("type 'finding' does not match the expected type 'backlog_item'" in f
                          for f in imp_msgs(_ty_run)))
            _nt = act_machine()
            _nt["backlog_item.imported.index.toml"] = "schema = 1\nrecord = [1]\n"
            _nt_run = run(_nt, obs=_is2_prior())
            check("imported-row-not-a-table-cannot-evaluate",
                  _nt_run is not None and _nt_run.checks.get("C-IMPORTED-SCHEMA") == "CANNOT-EVALUATE"
                  and (".working/toml/backlog_item.imported.index.toml#1: imported record is not a "
                       "table (fail-closed)") in imp_msgs(_nt_run))
            _vd = act_machine()
            _vd["manifest.toml"]["vendors"] = {"registered": ["x-acme"]}
            _vd["backlog_item.imported.index.toml"] = idx([imp_bi(1, **{"x-acme": {"lane": "history"}})])
            _vd["counters.toml"]["counters"]["imported:BI"] = 1
            _vd_run = act_run(_vd)
            check("imported-row-registered-vendor-accepted",
                  _vd_run is not None and not _vd_run.findings
                  and _vd_run.checks.get("C-IMPORTED-SCHEMA") == "PASS" and pending_only(_vd_run))

            # --- Links over the union of both series (spec 8.6) -------------------------------------
            # The store holds clean BI-1 (done), BI-2 and BI-3, and imported BI-1, WL-1 and PP-1; the
            # clean BI-3 and the imported BI-2 each carry the one link under test.
            def xs_machine(clean_link=None, imported_link=None):
                m = act_machine()
                m["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open"), envelope(
                    "BI-3", "backlog_item", "open", **({"links": [clean_link]} if clean_link else {}))])
                m["counters.toml"]["counters"].update({"BI": 3, "imported:BI": 2, "imported:WL": 1,
                                                       "imported:PP": 1})
                m["backlog_item.imported.index.toml"] = idx([imp_bi(1), imp_bi(
                    2, **({"links": [imported_link]} if imported_link else {}))])
                m["worklog.imported.toml"] = {"schema": 1, "entry": [imp_wl(1)]}
                m["preference_pattern.imported.index.toml"] = idx([imp_row(
                    "imported:PP-1", "preference_pattern", "active", created_at=TS, updated_at=TS,
                    context="when X", rationale="because Y")])
                return m

            # Each fixture runs against the empty imported baseline the across-time checks need on an
            # active store, with the imp_evidence bundle holding the original every imported row names,
            # so the imported counters above, that baseline and that bundle leave the link under test as
            # the only defect the per-series id grading or C-IMPORTED-PROVENANCE could see.
            def xs_run(m):
                return act_run(m)

            def xs_resolved(res):
                return (res is not None and not res.findings and res.checks.get("C-LINKS") == "PASS"
                        and pending_only(res))

            def xs_dangling(src, rel, tid):
                return ["C-LINKS: {} {} link -> {!r} resolves to no record in either series (a dangling "
                        "link over the union of both series; spec 8.6)".format(src, rel, tid)]

            _xs_refused = "a clean-to-imported link is limited to relates, derives_from, follows, and same-type"
            # Clean to imported: each permitted relation resolves over the union, and nothing is a
            # finding (red when the per-record validator refuses the imported id, or when C-LINKS
            # resolves clean ids alone).
            for _rel in ("relates", "derives_from", "follows", "supersedes"):
                check("clean-to-imported-{}-resolves".format(_rel),
                      xs_resolved(xs_run(xs_machine({"rel": _rel, "id": "imported:BI-1"}))))
            # Every other relation is refused per record while its target resolves, so the refusal is
            # the schema's and never a dangling-link side effect; C-IMPORTED-SEGREGATION refuses the
            # same link under the section 8.6 firewall, and those two are the only findings (red when
            # the relation limit is dropped).
            for _rel, _tid in (("resolves", "imported:BI-1"), ("remediates", "imported:BI-1"),
                               ("receipt_of", "imported:BI-1"), ("corrects", "imported:BI-1"),
                               ("exemplifies", "imported:PP-1")):
                _xs_run = xs_run(xs_machine({"rel": _rel, "id": _tid}))
                check("clean-to-imported-{}-refused".format(_rel),
                      _xs_run is not None and _xs_run.status != VALID
                      and _xs_run.checks.get("C-LINKS") == "PASS"
                      and len(_xs_run.findings) == 2 and _xs_refused in _xs_run.findings[0]
                      and _xs_run.findings[1]
                      == ("C-IMPORTED-SEGREGATION: clean record 'BI-3' links {!r} -> {!r}; a clean-to-imported "
                          "link is limited to relates, derives_from, follows and same-type supersedes (spec "
                          "8.6)".format(_rel, _tid)))
            # A supersedes of another type is refused per record, and C-LINKS, resolving the imported
            # target, names the same-type rule as well, beside the C-IMPORTED-SEGREGATION same-type
            # refusal (red when the per-record same-type limit is dropped, or when C-LINKS never
            # reaches the imported target's type).
            _xs_run = xs_run(xs_machine({"rel": "supersedes", "id": "imported:WL-1"}))
            check("clean-to-imported-cross-type-supersedes-refused",
                  _xs_run is not None and _xs_run.status != VALID and len(_xs_run.findings) == 3
                  and any(_xs_refused in f for f in _xs_run.findings)
                  and "C-LINKS: BI-3 (type 'backlog_item') supersedes -> 'imported:WL-1' of type 'worklog'; "
                      "supersession links the same type (spec 8.6)" in _xs_run.findings
                  and "C-IMPORTED-SEGREGATION: clean record 'BI-3' (type 'backlog_item') supersedes -> "
                      "'imported:WL-1' of another type; a clean-to-imported supersedes is same-type only "
                      "(spec 8.6)" in _xs_run.findings)
            # A permitted link to an imported id that resolves in neither series is a C-LINKS finding
            # (red when C-LINKS skips an unresolved imported target).
            _xs_run = xs_run(xs_machine({"rel": "relates", "id": "imported:BI-9"}))
            check("clean-to-imported-dangling-finding",
                  _xs_run is not None and _xs_run.checks.get("C-LINKS") == "FINDING"
                  and _xs_run.findings == xs_dangling("BI-3", "relates", "imported:BI-9"))
            # A row the per-series id grading refuses as outside its leaf series (an imported:FN id in
            # the backlog item leaf) is no link target: the link stays a C-LINKS dangling finding beside
            # the C-ID-SPACE refusal (red when C-LINKS resolves over every row C-IMPORTED-SCHEMA read).
            _xs_m = xs_machine({"rel": "relates", "id": "imported:FN-1"})
            _xs_m["backlog_item.imported.index.toml"]["record"].append(imp_bi(3, id="imported:FN-1"))
            _xs_run = xs_run(_xs_m)
            check("clean-to-imported-misplaced-row-not-a-target",
                  _xs_run is not None and _xs_run.checks.get("C-LINKS") == "FINDING"
                  and xs_dangling("BI-3", "relates", "imported:FN-1")[0] in _xs_run.findings
                  and any(f.startswith("C-ID-SPACE:") and "'imported:FN-1'" in f for f in _xs_run.findings))
            # Imported to imported: every declared relation resolves, subject to its type constraints
            # (red when C-LINKS reports an existing imported target as dangling; the imported finding
            # vectors below are the ones that go red when it reads clean records alone).
            for _rel in LINK_RELS:
                _tid = "imported:PP-1" if _rel == "exemplifies" else "imported:BI-1"
                check("imported-to-imported-{}-resolves".format(_rel),
                      xs_resolved(xs_run(xs_machine(imported_link={"rel": _rel, "id": _tid}))))
            # The type constraints hold across imported rows too: a supersedes of another type and a
            # receipt_of to a non-backlog item are C-LINKS findings (red when the links of imported rows
            # are not resolved).
            _xs_run = xs_run(xs_machine(imported_link={"rel": "supersedes", "id": "imported:WL-1"}))
            check("imported-to-imported-cross-type-supersedes-finding",
                  _xs_run is not None and _xs_run.checks.get("C-IMPORTED-SCHEMA") == "PASS"
                  and _xs_run.findings == [
                      "C-LINKS: imported:BI-2 (type 'backlog_item') supersedes -> 'imported:WL-1' of type "
                      "'worklog'; supersession links the same type (spec 8.6)"])
            _xs_run = xs_run(xs_machine(imported_link={"rel": "receipt_of", "id": "imported:WL-1"}))
            check("imported-to-imported-receipt-of-non-backlog-finding",
                  _xs_run is not None and _xs_run.checks.get("C-IMPORTED-SCHEMA") == "PASS"
                  and _xs_run.findings == [
                      "C-LINKS: imported:BI-2 receipt_of -> 'imported:WL-1' is a worklog, not a backlog_item "
                      "(spec 8.1/8.5)"])
            _xs_run = xs_run(xs_machine(imported_link={"rel": "relates", "id": "imported:BI-9"}))
            check("imported-to-imported-dangling-finding",
                  _xs_run is not None and _xs_run.checks.get("C-IMPORTED-SCHEMA") == "PASS"
                  and _xs_run.findings == xs_dangling("imported:BI-2", "relates", "imported:BI-9"))
            # Imported to clean is refused by C-IMPORTED-SCHEMA whether or not the clean target exists,
            # and C-LINKS does not resolve it, so the refusal is the one finding, for each relation a
            # clean record may carry across the series (red when C-LINKS resolves an imported row's
            # clean target and reports the dangling one again).
            for _rel in ("relates", "derives_from", "follows", "supersedes"):
                for _name, _tid in (("existing", "BI-1"), ("dangling", "BI-9")):
                    _xs_run = xs_run(xs_machine(imported_link={"rel": _rel, "id": _tid}))
                    check("imported-to-clean-{}-{}-refused".format(_rel, _name),
                          _xs_run is not None and _xs_run.checks.get("C-LINKS") == "PASS"
                          and len(_xs_run.findings) == 1
                          and "an imported-to-clean link MUST be refused" in _xs_run.findings[0])
            # A clean link into an imported namespace the series refuses (the importer-only LF) is the
            # per-record validator's finding, and the target, resolving in neither series, is a C-LINKS
            # dangling finding beside it; the doctor reports both (red when the namespace refusal is
            # dropped from the link validator, which leaves only the dangling finding).
            _xs_run = xs_run(xs_machine({"rel": "relates", "id": "imported:LF-1"}))
            check("clean-to-imported-refused-namespace-doctor-finding",
                  _xs_run is not None and _xs_run.status != VALID
                  and _xs_run.checks.get("C-LINKS") == "FINDING"
                  and any("which the imported series refuses" in f for f in _xs_run.findings))

            # --- Block scopes over the union of both series (spec 8.5) ------------------------------
            # An imported block may scope either series (spec 8.1/8.2), so one scoping an existing
            # imported id and an existing clean id resolves with no finding. This positive vector is
            # acceptance coverage only: a scopes loop that never examined the imported block would also
            # emit no finding here. The revert guard is imported-block-scopes-dangling-finding below,
            # which goes red when the scopes loop reads clean records alone.
            _bs = act_machine()
            _bs["backlog_item.imported.index.toml"] = idx([imp_bi(1)])
            _bs["block.imported.index.toml"] = idx([imp_row(
                "imported:BL-1", "block", "active", created_at=TS, updated_at=TS,
                scopes=["imported:BI-1", "BI-1"])])
            _bs["counters.toml"]["counters"].update({"imported:BI": 1, "imported:BL": 1})
            _bs_run = xs_run(_bs)
            check("imported-block-scopes-union-resolves",
                  _bs_run is not None and not _bs_run.findings
                  and _bs_run.checks.get("C-LINKS") == "PASS" and pending_only(_bs_run))
            # An imported block's scope that resolves in neither series is a C-LINKS finding, one per
            # unresolved id (red when the scopes loop reads clean records alone and never sees the
            # imported block).
            _bs = act_machine()
            _bs["block.imported.index.toml"] = idx([imp_row(
                "imported:BL-1", "block", "active", created_at=TS, updated_at=TS,
                scopes=["imported:BI-9", "FN-99"])])
            _bs["counters.toml"]["counters"]["imported:BL"] = 1
            _bs_run = xs_run(_bs)
            check("imported-block-scopes-dangling-finding",
                  _bs_run is not None and _bs_run.checks.get("C-LINKS") == "FINDING"
                  and _bs_run.findings == [
                      "C-LINKS: block imported:BL-1 scopes 'imported:BI-9' which resolves to no record "
                      "in either series (spec 8.5)",
                      "C-LINKS: block imported:BL-1 scopes 'FN-99' which resolves to no record in "
                      "either series (spec 8.5)"])
            # A clean block's scope naming a missing imported id is a C-LINKS finding over the union
            # (beside the per-record shape finding: a clean block's scopes stay clean-shaped), never a
            # silent pass (red when an unresolved scope is skipped).
            _bs = act_machine()
            _bs["block.index.toml"] = idx([envelope("BL-1", "block", "active", scopes=["imported:BI-9"])])
            _bs["counters.toml"]["counters"]["BL"] = 1
            _bs_run = xs_run(_bs)
            check("clean-block-scopes-dangling-imported-finding",
                  _bs_run is not None and _bs_run.checks.get("C-LINKS") == "FINDING"
                  and ("C-LINKS: block BL-1 scopes 'imported:BI-9' which resolves to no record in "
                       "either series (spec 8.5)") in _bs_run.findings)
            # A clean block's scope naming an EXISTING imported id resolves over the union, so C-LINKS
            # adds no dangling finding beside the per-record shape finding (red when the clean block's
            # scopes resolve against clean ids alone).
            _bs = act_machine()
            _bs["backlog_item.imported.index.toml"] = idx([imp_bi(1)])
            _bs["block.index.toml"] = idx([envelope("BL-1", "block", "active",
                                                    scopes=["BI-1", "imported:BI-1"])])
            _bs["counters.toml"]["counters"].update({"BL": 1, "imported:BI": 1})
            _bs_run = xs_run(_bs)
            check("clean-block-scopes-imported-target-resolves",
                  _bs_run is not None and _bs_run.checks.get("C-LINKS") == "PASS"
                  and not any(f.startswith("C-LINKS:") for f in _bs_run.findings))
            # C-LINKS resolves over BOTH series, so its source set is complete only when the imported-row
            # collection was: an unreadable leaf, an absent leaf, and a row the imported validator cannot
            # identify each leave C-LINKS CANNOT-EVALUATE, never a pass over the rows that were read (red
            # when the collection's completeness is not carried into C-LINKS; the skipped row could carry,
            # or be, a link or scope target).
            def _xs_links_unread(res):
                return (res is not None and res.checks.get("C-LINKS") == "CANNOT-EVALUATE"
                        and any("link and scope resolution is not evaluated" in m
                                for m in res.by_check.get("C-LINKS", [])))
            _xl = act_machine()
            _xl["backlog_item.imported.index.toml"] = "garbage = ["
            check("r4-links-cannot-evaluate-when-imported-leaf-unreadable", _xs_links_unread(xs_run(_xl)))
            _xl = act_machine()
            del _xl["backlog_item.imported.index.toml"]
            check("r4-links-cannot-evaluate-when-imported-leaf-missing", _xs_links_unread(xs_run(_xl)))
            _xl = act_machine()
            _xl["backlog_item.imported.index.toml"] = idx(["not a table"])
            check("r4-links-cannot-evaluate-when-imported-row-unidentifiable",
                  _xs_links_unread(xs_run(_xl)))

            def _xs_links_imported_unread(res):
                return (_xs_links_unread(res)
                        and any("the imported series could not all be read" in m
                                for m in res.by_check.get("C-LINKS", [])))
            # The two imported collectors read each leaf separately, so one read can fail while the other
            # succeeds (a leaf replaced between them). Each half of C-LINKS's imported completeness is
            # driven alone by failing the leaf read inside one collector only: the C-IMPORTED-SCHEMA
            # collector (red when its unreadable-leaf branch stops marking the collection partial), and the
            # per-series id collector (red when the caller stops reading that collector's unread set). A
            # leaf at an unsupported schema is malformed to the C-IMPORTED-SCHEMA collector alone, since
            # the id collector reads its rows without the schema (red when the malformed-leaf branch stops
            # marking the collection partial).
            _real_read_toml = _read_toml

            def _leaf_unreadable_in(collector_name):
                _real_collector = globals()[collector_name]

                def _failing_read(fd, relpath, rep_):
                    if relpath.endswith("backlog_item.imported.index.toml"):
                        rep_.cant("cannot read {}: synthetic: unreadable at this read".format(relpath))
                        return None, "error"
                    return _real_read_toml(fd, relpath, rep_)

                def _collector(*args, **kwargs):
                    globals()["_read_toml"] = _failing_read
                    try:
                        return _real_collector(*args, **kwargs)
                    finally:
                        globals()["_read_toml"] = _real_read_toml
                globals()[collector_name] = _collector
                try:
                    return xs_run(act_machine())
                finally:
                    globals()[collector_name] = _real_collector
            check("r4-links-cannot-evaluate-when-schema-collector-leaf-unreadable",
                  _xs_links_imported_unread(_leaf_unreadable_in("_gather_imported_records")))
            check("r4-links-cannot-evaluate-when-id-collector-leaf-unreadable",
                  _xs_links_imported_unread(_leaf_unreadable_in("_gather_imported_rows")))
            _xl = act_machine()
            _xl["backlog_item.imported.index.toml"] = {"schema": 2, "record": []}
            check("r4-links-cannot-evaluate-when-imported-leaf-schema-unsupported",
                  _xs_links_imported_unread(xs_run(_xl)))
            # An imported row whose links value C-LINKS cannot interpret (a rel outside the closed
            # vocabulary, which C-IMPORTED-SCHEMA refuses while still seating the row) leaves C-LINKS
            # CANNOT-EVALUATE, never a pass that skipped the link (red when C-LINKS ignores the unparsed
            # links of imported rows).
            _xl_run = xs_run(xs_machine(imported_link={"rel": "relates-to", "id": "imported:BI-9"}))
            check("r4-links-cannot-evaluate-when-imported-links-uninterpreted",
                  _xs_links_unread(_xl_run)
                  and any("the links or scopes of imported-series records could not be interpreted" in m
                          for m in _xl_run.by_check.get("C-LINKS", [])))
            # With a clean type unread and the imported series partial, the C-LINKS message names both
            # causes, compared in full (red when either is dropped).
            _xl = act_machine()
            _xl["finding.index.toml"] = {"schema": 1, "record": "FN-1"}
            _xl["backlog_item.imported.index.toml"] = "garbage = ["
            _xl_run = xs_run(_xl)
            check("r4-links-message-names-clean-and-imported-causes",
                  _xl_run is not None and _xl_run.by_check.get("C-LINKS") == [
                      "C-LINKS: the records of 'finding' could not all be read or confirmed; the imported "
                      "series could not all be read (see C-RECORDS, C-PERRECORD-RECONCILE, C-ARCHIVE-ENUM "
                      "and C-IMPORTED-SCHEMA); link and scope resolution is not evaluated over a partial set "
                      "(spec 8.5/8.6)"])
            # The writer/doctor agreement on a declaration-only activation: a 1.3.0 store on homes 1
            # whose [unmanaged] names an imported-leaf name certifies, the name not being a
            # managed leaf there, and plan_write_scope plans the SAME manifest without refusing (red
            # when the write guard reads the declared spec_version alone: it would then refuse a
            # declaration the doctor certifies).
            _h1u = clean_machine()
            _h1u["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            _h1u["manifest.toml"]["unmanaged"] = dict(paths=[".working/toml/worklog.imported.toml"])
            _h1u["worklog.imported.toml"] = "unmanaged legacy bytes, never read ["
            _h1u_run = run(copy.deepcopy(_h1u))
            try:
                _h1u_scope = _opf_write_guard.plan_write_scope(
                    ".working/toml", _h1u["manifest.toml"], (".working/toml/manifest.toml",), "upgrade")
            except _opf_write_guard.WriteGuardError:
                _h1u_scope = None
            check("containment-writer-doctor-agree-on-homes1-130-unmanaged-imported-name",
                  _h1u_run is not None and _h1u_run.status == VALID
                  and _h1u_scope is not None and ".working/toml/manifest.toml" in _h1u_scope["store"])
            # The imported series activates only inside the graded 2.0.0 / homes = 2 declaration.
            # A spec_version 1.3.0 store on homes 1, which C-MANIFEST accepts at this
            # support, keeps the legacy roster and grades every imported leaf as an unregistered stray
            # (red when the flag reads the declared spec_version alone).
            _h1 = act_machine()
            _h1["manifest.toml"]["opf"]["spec_version"] = _opf_store.IMPORTED_SPEC_VERSION
            del _h1["manifest.toml"]["opf"]["homes"]
            _h1_run = run(_h1)
            check("imported-series-inactive-on-homes1-130-store",
                  _h1_run is not None and tuple(_h1_run.checks) == REQUIRED_CHECKS
                  and any("C-CONTAINMENT" in f and "worklog.imported.toml" in f and "unregistered" in f
                          for f in _h1_run.findings))
            # The pure flag over its arms, the support threshold sampled just below, at, and just
            # above IMPORTED_SPEC_VERSION, and the support level also sampled at the homes-2 spec
            # version HOMES2_SPEC_VERSION: True only for the 2.0.0 / homes = 2 pair with both
            # tooling gates raised (red when either arm, or the threshold, moves).
            _pair = {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION, "homes": 2}}
            _flag_rows = (
                (2, "1.3.0", _pair, True), (2, "1.3.1", _pair, True), (2, "1.2.99", _pair, False),
                (2, "1.2.0", _pair, False), (1, "1.3.0", _pair, False),
                (2, "1.3.0", {"opf": {"spec_version": "1.3.0"}}, False),
                (2, "1.3.0", {"opf": {"spec_version": "1.3.0", "homes": 2}}, False),
                (2, "1.3.0", {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION}}, False),
                (2, "1.3.0", None, False),
                (2, _opf_store.HOMES2_SPEC_VERSION, _pair, True),
                (2, _opf_store.HOMES2_SPEC_VERSION, {"opf": {"spec_version": "1.3.0"}}, False))
            _flag_got = []
            for _fh, _fv, _fm, _want in _flag_rows:
                _opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION = _fh, _fv
                _flag_got.append(imported_series_active(_fm) is _want)
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            check("imported-series-active-keyed-on-homes2-declaration",
                  _flag_got == [True] * len(_flag_rows))
            # Homes 2 activated WITHOUT imported-series support (the child flip the record and homes
            # suites run): the 2.0.0 store keeps the plain homes-2 roster and its imported leaves stay
            # unregistered strays (red when the flag reads the homes-2 declaration alone).
            _opf_store.SUPPORTED_SPEC_VERSION = _is2_prev_ssv
            _ns_run = run(act_machine())
            check("imported-series-inactive-at-homes2-without-support",
                  _ns_run is not None and tuple(_ns_run.checks) == required_checks(2)
                  and any("C-CONTAINMENT" in f and "worklog.imported.toml" in f and "unregistered" in f
                          for f in _ns_run.findings))
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _is2_prev_ssv
            _opf_store.SUPPORTED_HOMES = _is2_prev_homes
            _is3_g["IMPORTED_CHECKS"] = _is3_prev_roster
        # The render source gate follows the imported roster: a report that carries the imported
        # checks is gated on them too, so a C-IMPORTED-SCHEMA finding refuses source integrity (red
        # when source_checks drops the imported ids).
        _src_imp = dict.fromkeys(required_checks(2, True), "PASS")
        _src_imp["C-IMPORTED-SCHEMA"] = "FINDING"
        check("source-gate-imported-schema-finding-refuses",
              source_checks(StoreValidation(INVALID, checks=_src_imp))
              == tuple(cid for cid in required_checks(2, True) if cid not in DELIVERABLE_DRIFT_CHECKS)
              and not source_integrity_ok(StoreValidation(INVALID, checks=_src_imp)))
        # The writer/doctor agreement in the OTHER direction, at the SHIPPED support (the derived
        # flag False): an [unmanaged] declaration of an imported-leaf NAME is a legitimate
        # declaration while the series is inactive (the name is not a managed leaf), so the FULL
        # doctor run certifies the store with the declared file covered, and plan_write_scope
        # plans the SAME manifest without refusing (red when the write guard's classification
        # reads the series as ACTIVE here: it would then refuse a declaration the doctor
        # certifies, the over-refusal divergence; fail-closed, but a divergence all the same).
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
        # SUPPORTED_SPEC_VERSION reaches IMPORTED_SPEC_VERSION the imported checks must exist, be
        # NEW ids in the exact id grammar (never a placeholder reusing an id from REQUIRED_CHECKS
        # or HOMES2_CHECKS), and sit inside required_checks(h, imported=True) for EVERY homes
        # generation h from 1 through SUPPORTED_HOMES. An active series is graded only at homes 2
        # (imported_series_active), but the predicate keeps the every-generation bound, so a
        # wiring into one generation's imported roster alone reads red. The
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
        _icc_prev_roster = IMPORTED_CHECKS
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
            _icc_g["IMPORTED_CHECKS"] = ()
            _icc_unsup_empty = imported_content_checks_landed()
            _icc_g["IMPORTED_CHECKS"] = ("C-IMPORTED-PLACEHOLDER",)
            _icc_unsup_placeholder = imported_content_checks_landed()
            _opf_store.SUPPORTED_HOMES = 0
            _icc_unsup_nohomes = imported_content_checks_landed()
            _opf_store.SUPPORTED_HOMES = _icc_prev_homes
            check("imported-content-gate-defers-while-unsupported",
                  _icc_unsup_empty and _icc_unsup_placeholder and _icc_unsup_nohomes)
            # Supported tooling, empty roster: red (the non-empty arm).
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            _icc_g["IMPORTED_CHECKS"] = ()
            check("imported-content-gate-empty-roster-red-once-supported",
                  not imported_content_checks_landed())
            # Supported, a NEW id nothing wires into any required roster: red (the membership
            # arm; this is the vector a deleted membership condition reds). The shipped
            # required_checks wires whatever IMPORTED_CHECKS holds, so the unwired roster is a
            # wiring that ignores its imported flag, the one-argument roster before IS-3a.
            _icc_g["IMPORTED_CHECKS"] = ("C-IMPORTED-PLACEHOLDER",)
            _icc_g["required_checks"] = lambda homes, imported=False, _rc=_icc_prev_rc: _rc(homes)
            check("imported-content-gate-out-of-roster-red-once-supported",
                  not imported_content_checks_landed())
            _icc_g["required_checks"] = _icc_prev_rc
            # Supported, a placeholder reusing an EXISTING required id: red (the disjointness
            # and prefix arms TOGETHER; C-CONTAINMENT sits inside every required roster, so the
            # membership arm alone would pass it, and each of the two arms is discriminated on
            # its own by the three vectors below).
            _icc_g["IMPORTED_CHECKS"] = ("C-CONTAINMENT",)
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
                lambda homes, imported=False, _rc=_icc_prev_rc, _w=_icc_spelled + _icc_canonical:
                _rc(homes) + (_w if imported else ()))
            _icc_unpref_one = []
            for _icc_cid in _icc_spelled:
                _icc_g["IMPORTED_CHECKS"] = (_icc_cid,)
                _icc_unpref_one.append(imported_content_checks_landed())
            check("imported-content-gate-unprefixed-new-id-red-once-supported",
                  _icc_unpref_one == [False] * 15)
            _icc_canonical_one = []
            for _icc_cid in _icc_canonical:
                _icc_g["IMPORTED_CHECKS"] = (_icc_cid,)
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
                        _icc_g["IMPORTED_CHECKS"] = roster
                        verdicts.append(imported_content_checks_landed())
                        cases.add((offender, roster))
                        placed = (placed and sorted(roster) == sorted(ids)
                                  and sorted(roster).index(offender) == rank)
                return (placed and len(verdicts) == count and len(cases) == count
                        and verdicts == [want] * count)

            def _icc_unwire(offender):
                wired = tuple(cid for cid in _icc_quad if cid != offender)
                _icc_g["required_checks"] = (
                    lambda homes, imported=False, _rc=_icc_prev_rc, _w=wired:
                    _rc(homes) + (_w if imported else ()))

            def _icc_collide_required(offender):
                _icc_g["REQUIRED_CHECKS"] = _icc_prev_req + (offender,)

            def _icc_collide_homes2(offender):
                _icc_g["HOMES2_CHECKS"] = _icc_prev_h2 + (offender,)

            # ONLY the disjointness arm refuses: the offender ALSO sits in REQUIRED_CHECKS, then in
            # HOMES2_CHECKS, while EVERY id is wired into every required roster, so membership
            # passes. Each patched roster is staged back after its battery and restored again in
            # finally.
            _icc_g["required_checks"] = (
                lambda homes, imported=False, _rc=_icc_prev_rc, _w=_icc_quad:
                _rc(homes) + (_w if imported else ()))
            check("imported-content-gate-required-collision-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_collide_required, 118, False))
            _icc_g["REQUIRED_CHECKS"] = _icc_prev_req
            check("imported-content-gate-homes2-collision-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_collide_homes2, 118, False))
            _icc_g["HOMES2_CHECKS"] = _icc_prev_h2
            # ONLY the grammar arm refuses: each set holds one fresh offender outside the
            # grammar, and every id is wired into every required roster.
            _icc_g["required_checks"] = (
                lambda homes, imported=False, _rc=_icc_prev_rc, _w=tuple(sorted(
                    {cid for _, _, ids in _icc_grammar_rows for cid in ids})):
                _rc(homes) + (_w if imported else ()))
            check("imported-content-gate-multi-id-unprefixed-red-once-supported",
                  _icc_battery(_icc_grammar_rows, _icc_grammar_placed, lambda offender: None,
                               130, False))
            # ONLY the membership arm refuses: the offender is the one id left unwired.
            check("imported-content-gate-multi-id-partly-wired-red-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, _icc_unwire, 118, False))
            # Green control over the same 118 cases: every id wired and nothing colliding reads
            # True, so no red above comes from the roster size or the order.
            _icc_g["required_checks"] = (
                lambda homes, imported=False, _rc=_icc_prev_rc, _w=_icc_quad:
                _rc(homes) + (_w if imported else ()))
            check("imported-content-gate-multi-id-wired-green-once-supported",
                  _icc_battery(_icc_rotated, _icc_rotated_placed, lambda offender: None, 118, True))
            # Supported, the legitimate activation wiring: the SHIPPED roster under the SHIPPED
            # required_checks, which appends IMPORTED_CHECKS to every generation's imported roster,
            # reads True (red when required_checks stops honouring its imported flag). The same
            # id wired ONLY into the homes-2 imported roster stays red while generation 1 is
            # supported: the predicate keeps the every-generation bound although an active series
            # is graded only at homes 2, a red on a legitimate alternative, never a green on an
            # unwired generation.
            _icc_g["IMPORTED_CHECKS"] = _icc_prev_roster
            _icc_g["required_checks"] = _icc_prev_rc
            check("imported-content-gate-legitimate-roster-green-once-supported",
                  imported_content_checks_landed())
            _icc_g["IMPORTED_CHECKS"] = ("C-IMPORTED-CONTENT",)
            _icc_g["required_checks"] = (
                lambda homes, imported=False, _rc=_icc_prev_rc:
                _rc(homes) + (("C-IMPORTED-CONTENT",) if imported and homes >= 2 else ()))
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
                lambda homes, imported=False, _rc=_icc_prev_rc:
                _rc(homes) + (("C-IMPORTED-CONTENT",) if imported and homes < 2 else ()))
            check("imported-content-gate-generation1-only-wiring-red-at-homes-2",
                  not imported_content_checks_landed())
            _icc_omissions = []
            for _icc_skip in range(1, _opf_store.SUPPORTED_HOMES + 1):
                _icc_g["required_checks"] = (
                    lambda homes, imported=False, _rc=_icc_prev_rc, _skip=_icc_skip:
                    _rc(homes) if homes == _skip or not imported
                    else _rc(homes) + ("C-IMPORTED-CONTENT",))
                _icc_omissions.append(imported_content_checks_landed())
            check("imported-content-gate-each-generation-omission-red-at-homes-2",
                  _icc_omissions == [False] * _opf_store.SUPPORTED_HOMES)
            _icc_g["required_checks"] = (
                lambda homes, imported=False, _rc=_icc_prev_rc:
                _rc(homes) + (("C-IMPORTED-CONTENT",) if imported else ()))
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
            _icc_g["IMPORTED_CHECKS"] = _icc_prev_roster
            _icc_g["required_checks"] = _icc_prev_rc
            _icc_g["REQUIRED_CHECKS"] = _icc_prev_req
            _icc_g["HOMES2_CHECKS"] = _icc_prev_h2
        # The homes-2 / imported-series coupling at the SHIPPED constants (red when SUPPORTED_HOMES
        # reaches 2 while SUPPORTED_SPEC_VERSION stays below IMPORTED_SPEC_VERSION): under it
        # imported_series_active reads exactly the homes-2 grade, so a homes-2 store never ships with
        # its imported leaves graded as unregistered strays. The SAME predicate is then exercised
        # under controlled constants (both restored in finally; no store fixture runs inside this
        # block). A probe that raises reads "raised", never False, so an exception never passes as a
        # fail-closed answer.
        check("homes2-carries-imported-series-at-shipped-constants", homes2_carries_imported_series())
        _h2c_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        _h2c_prev_homes = _opf_store.SUPPORTED_HOMES
        _h2c_missing = object()

        def _h2c_probe(homes, support):
            _opf_store.SUPPORTED_HOMES = homes
            if support is _h2c_missing:
                if hasattr(_opf_store, "SUPPORTED_SPEC_VERSION"):
                    del _opf_store.SUPPORTED_SPEC_VERSION
            else:
                _opf_store.SUPPORTED_SPEC_VERSION = support
            try:
                return homes2_carries_imported_series()
            except Exception:  # noqa: BLE001  a raise is its own result, never a False
                return "raised"

        try:
            # Homes 2 or later without imported-series support refuses (red when the predicate
            # stops consulting the support comparison).
            check("homes2-imported-coupling-refuses-unsupported",
                  all(_h2c_probe(_h2c_h, _h2c_v) is False
                      for _h2c_h, _h2c_v in ((2, "1.2.0"), (2, "1.2.99"), (3, "1.2.0"))))
            # Homes 2 with support at, above, and well above IMPORTED_SPEC_VERSION is admitted (red
            # when the support comparison is narrowed to a window below the homes-2 spec version).
            check("homes2-imported-coupling-admits-supported",
                  all(_h2c_probe(2, _h2c_v) is True
                      for _h2c_v in ("1.3.0", "1.3.1", _opf_store.HOMES2_SPEC_VERSION)))
            # While homes 2 is inactive the coupling defers, whatever the support (red when the
            # homes-1 deferral is dropped).
            check("homes2-imported-coupling-defers-below-homes2", _h2c_probe(1, "1.2.0") is True)
            # A malformed SUPPORTED_HOMES (a bool, a string, None, a float, or below 1) or a
            # malformed or missing SUPPORTED_SPEC_VERSION at homes 2 answers False and never raises
            # (red when either guard is dropped).
            _h2c_bad = ([_h2c_probe(_h2c_h, "1.3.0") for _h2c_h in (True, "2", None, 0, -1, 2.0)]
                        + [_h2c_probe(2, _h2c_v)
                           for _h2c_v in ("one.three", None, 130, "1.3.0\n", "", _h2c_missing)])
            check("homes2-imported-coupling-malformed-fails-closed",
                  len(_h2c_bad) == 12 and all(_h2c_r is False for _h2c_r in _h2c_bad))
            # Under each coupled pair of constants the flag equals the homes-2 grade on every manifest
            # shape; the control at homes 2 without support grades the pair 2, leaves it inactive,
            # and reads the coupling False (red when the flag reads the homes-2 grade alone, or when
            # the support threshold moves above the homes-2 spec version).
            _h2c_pair = {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION, "homes": 2}}
            _h2c_manifests = (
                _h2c_pair, {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION}},
                {"opf": {"homes": 2}}, {"opf": {"spec_version": "1.3.0", "homes": 2}},
                {"opf": {"spec_version": "1.3.0", "homes": 1}},
                {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION, "homes": True}},
                {"opf": {"spec_version": _opf_store.HOMES2_SPEC_VERSION, "homes": "2"}},
                {"opf": {}}, {"opf": []}, {}, None)
            _h2c_agree = []
            for _h2c_h, _h2c_v in ((1, "1.2.0"), (2, "1.3.0"), (2, _opf_store.HOMES2_SPEC_VERSION)):
                _h2c_agree.append(
                    _h2c_probe(_h2c_h, _h2c_v) is True
                    and all(imported_series_active(_h2c_m) is (_opf_store.homes_generation(_h2c_m) >= 2)
                            for _h2c_m in _h2c_manifests))
            _h2c_control = _h2c_probe(2, "1.2.0")
            check("imported-active-is-homes2-grade-when-coupled",
                  _h2c_agree == [True, True, True] and _h2c_control is False
                  and _opf_store.homes_generation(_h2c_pair) == 2
                  and imported_series_active(_h2c_pair) is False)
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _h2c_prev_ssv
            _opf_store.SUPPORTED_HOMES = _h2c_prev_homes
        # The per-series id grading (spec 8.2/8.3), keyed on imported_series_active alone: the graded 2.0.0 /
        # homes = 2 declaration and this tooling's imported-series support. BOTH tooling gates are patched
        # inside the block (the check_opf_homes per-vector idiom), SUPPORTED_HOMES to 2 and
        # SUPPORTED_SPEC_VERSION to IMPORTED_SPEC_VERSION, so the driver derives the flag True end to end;
        # both are restored in finally. Containment then registers the imported leaves, the four imported
        # checks grade them, and any imported check not landed would be CANNOT-EVALUATE by the roster
        # reconciliation on every active store: the vectors judge the per-check verdicts, and each clean
        # control asserts that any such pending reconciliation cants are its ONLY non-passing verdicts. The
        # fixtures build on act_machine (every imported leaf present and empty, every imported counter at
        # its zero high-water), the imp_row/imp_bi/imp_wl envelope rows and the imp_evidence bundle holding
        # the original those rows name, so the per-series id vectors and the imported-content grading read
        # the SAME stores.
        _i3_ns = sorted(set(BASELINE_TYPES.values()))

        def _i3_machine(imp=None, drop=(), leaves=None):
            m = act_machine()
            m["counters.toml"]["counters"].update(imp or {})
            for key in drop:
                m["counters.toml"]["counters"].pop(key, None)
            m.update(leaves or {})
            return m

        def _i3_row(rid, **over):
            return imp_row(rid, "backlog_item", "done", created_at=TS, updated_at=TS, **over)

        def _i3_obs(imported=None, high=None):
            o = clean_prior()
            o["prior"]["imported"] = {} if imported is None else imported
            o["prior"]["counters_high"].update(high or {})
            return o

        def _i3_run(machine, obs=None, working=None, evidence=True):
            # The planted evidence bundle (imp_evidence) holds the original every imp_row names; a vector
            # adds to it with `working`, or drops it with evidence=False.
            planted = dict(imp_evidence()) if evidence else {}
            planted.update(working or {})
            return run(machine, working=planted, obs=_i3_obs() if obs is None else obs)

        def _i3_bad(r):
            return {c for c, v in r.checks.items() if v != "PASS"} if r is not None else None

        def _i3_msgs(r, cid):
            return r.by_check.get(cid, []) if r is not None else []

        def _i3_pending(r):
            # Any imported check not landed is CANNOT-EVALUATE by the roster reconciliation on every
            # active store, beside whatever the vector's own check asserts (vacuous once all four land).
            return (r is not None
                    and all(r.checks.get(cid) == "CANNOT-EVALUATE" for cid in _imp_pending))

        def _i3_clean_active(r):
            # The clean active store: no findings, C-IMPORTED-SCHEMA passes, and the ONLY cants are
            # any pending imported checks' reconciliation cants (none once all four land, so every
            # check then passes and the store is VALID).
            return (r is not None and not r.findings
                    and r.checks.get("C-IMPORTED-SCHEMA") == "PASS"
                    and pending_only(r) and _i3_bad(r) == set(_imp_pending))

        def _i3_rows_refused_inactive(r):
            # C-COUNTERS on an INACTIVE store holding every imported row: each row, and nothing else,
            # draws the former finding that refuses an imported key as a clean two-letter namespace.
            return (r is not None and r.checks.get("C-COUNTERS") == "FINDING"
                    and sorted(_i3_msgs(r, "C-COUNTERS")) == sorted(
                        "counters.toml: [counters] key {!r} is not a two-letter uppercase namespace".format(
                            "imported:" + ns) for ns in _i3_ns))

        _i3_bi2 = {"backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1"), _i3_row("imported:BI-2")])}
        _i3_prev_homes = _opf_store.SUPPORTED_HOMES
        _i3_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The clean active store with a zero row for every imported counter (spec 8.2) passes every
            # check and is VALID (red when the flag stays False: the imported rows are then C-COUNTERS
            # findings).
            _i3_clean = _i3_run(_i3_machine())
            check("imported-active-clean-only-tripwire", _i3_clean_active(_i3_clean))
            # C-COUNTERS requires the imported rows on an active store (red when imported_namespaces is not
            # passed: the missing row is then no finding, and with every row dropped only the pending
            # cants are left). As for a missing clean row, the overall status is dominated by the
            # correlated C-HISTORY-COUNTERS cant (the current counters did not evaluate), so
            # discriminate on the per-check verdict. C-IMPORTED-IDS grades the same imported row
            # (spec 8.3: it checks the counters) with the same message under its own id.
            _i3_miss = _i3_run(_i3_machine(drop=("imported:BI",)))
            check("imported-counter-row-missing-finding",
                  _i3_miss is not None and _i3_miss.checks.get("C-COUNTERS") == "FINDING"
                  and [f for f in _i3_msgs(_i3_miss, "C-COUNTERS") if "imported" in f]
                  == ["counters.toml: [counters] is missing a high-water for imported namespace "
                      "'imported:BI' (spec 8.2)"]
                  and _i3_miss.checks.get("C-IMPORTED-IDS") == "FINDING"
                  and _i3_msgs(_i3_miss, "C-IMPORTED-IDS")
                  == ["C-IMPORTED-IDS: counters.toml: [counters] is missing a high-water for imported "
                      "namespace 'imported:BI' (spec 8.2)"])
            _i3_none = _i3_run(_i3_machine(drop=tuple("imported:" + ns for ns in _i3_ns)))
            check("imported-counter-rows-all-missing-finding",
                  _i3_none is not None and _i3_none.status == CANNOT_EVALUATE
                  and _i3_bad(_i3_none)
                  == {"C-COUNTERS", "C-HISTORY-COUNTERS", "C-IMPORTED-IDS"} | set(_imp_pending)
                  and _i3_pending(_i3_none)
                  and _i3_none.checks.get("C-COUNTERS") == "FINDING"
                  and sum("missing a high-water for imported namespace" in f
                          for f in _i3_msgs(_i3_none, "C-COUNTERS")) == len(_i3_ns) == 12
                  and sum("missing a high-water for imported namespace" in f
                          for f in _i3_msgs(_i3_none, "C-IMPORTED-IDS")) == len(_i3_ns))
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
            # check; only the pending reconciliation cants remain (the leaf is registered, never a
            # stray, here, and C-IMPORTED-SCHEMA reads the conforming rows and passes).
            _i3_ok = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves=_i3_bi2))
            check("imported-leaf-control-only-tripwire", _i3_clean_active(_i3_ok))
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
            _i3_gone_m = _i3_machine(imp={"imported:BI": 2})
            del _i3_gone_m["backlog_item.imported.index.toml"]
            _i3_gone = _i3_run(_i3_gone_m)
            check("imported-deleted-leaf-finding",
                  _i3_gone is not None
                  and _i3_bad(_i3_gone)
                  == {"C-NO-DELETION", "C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-LINKS"} | set(_imp_pending)
                  and _i3_pending(_i3_gone)
                  and _i3_gone.checks.get("C-IMPORTED-SCHEMA") == "FINDING"
                  and any("2 allocated imported:BI-<n> id(s) above imported:BI-0" in f
                          for f in _i3_msgs(_i3_gone, "C-NO-DELETION")))
            # imported:WL tiles apart from the clean worklog (spec 8.2). An imported gap is the imported line
            # finding only: clean contiguity, frozen coverage and the changelog gates still pass (red when the
            # imported numbers join the clean scan, or when the imported scan is removed).
            _i3_wlg = _i3_run(_i3_machine(imp={"imported:WL": 3}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [imp_wl(1), imp_wl(3)]}}))
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
                "worklog.imported.toml": {"schema": 1, "entry": [imp_wl(1), imp_wl(2), imp_wl(3)]}})
            _i3_rv["worklog.toml"] = {"schema": 1, "entry": [wl(4)]}
            _i3_rvr = _i3_run(_i3_rv)
            check("clean-wl-gap-not-filled-by-imported-line",
                  _i3_rvr is not None and [f for f in _i3_msgs(_i3_rvr, "C-CONTIGUITY")] == [
                      "C-CONTIGUITY: the worklog (active + archive) is not contiguous; WL-3 is missing (a gap "
                      "breaks span tiling, spec 6.1/11)"])
            _i3_wl1 = _i3_run(_i3_machine(imp={"imported:WL": 2}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [imp_wl(2)]}}))
            check("imported-wl-not-from-one-finding",
                  _i3_wl1 is not None and any("does not start at imported:WL-1" in f
                                              for f in _i3_msgs(_i3_wl1, "C-CONTIGUITY")))
            # Reservations on the imported number lines (spec 8.2: the next-ID derivation applies per
            # series key, so recovery treats a reserved number as issued even where no published record
            # carries it). An orphaned imported reservation's number, one the prior imported baseline
            # held no record with, is skipped by the imported deletion and contiguity scans exactly as a
            # clean one is on its own line (red when the imported scans ignore the reservations and owe
            # the number); an unreserved number stays owed (the unreserved controls are the deletion and
            # gap vectors above); a reserved number the baseline HELD is owed despite the reservation (a
            # reservation is irrevocable and outlives the publication it preceded); and with no usable
            # baseline the exemption is a named CANNOT-EVALUATE, never granted. The overhang count is
            # bounded at both ends: a reservation above the high-water or at/below the max present number
            # exempts nothing.
            import _opf_journal as _i3_jr

            def _i3_rsv(n, claims):
                rid = "{}-20260927T00{:04d}Z-{:016x}".format(_opf_store._HOME_RUN_PREFIXES["import"], n, n)
                return (_opf_store.allocation_record("import", rid)[len(WORKING_DIRNAME) + 1:],
                        _i3_jr.reservation_payload("import", rid, "op-{}".format(n), claims).decode("utf-8"))

            def _i3_rrun(machine, numbers, obs=None, key="imported:BI"):
                return _i3_run(machine, working=dict(_i3_rsv(i + 1, {key: n}) for i, n in enumerate(numbers)),
                               obs=obs)

            def _i3_ovm():
                # imported:BI-1 and imported:BI-2 present under imported:BI = 4: 3 and 4 are allocated
                # above the highest present number.
                return _i3_machine(imp={"imported:BI": 4}, leaves=copy.deepcopy(_i3_bi2))

            def _i3_owed(r, count):
                return (r is not None and r.checks.get("C-NO-DELETION") == "FINDING"
                        and any("{} allocated imported:BI-<n> id(s) above imported:BI-2".format(count) in f
                                for f in _i3_msgs(r, "C-NO-DELETION")))

            check("imported-reserved-overhang-exempt", _i3_clean_active(_i3_rrun(_i3_ovm(), (3, 4))))
            check("imported-overhang-unreserved-still-owed", _i3_owed(_i3_rrun(_i3_ovm(), (3,)), 1))
            check("imported-overhang-reserved-above-high-water-not-counted",
                  _i3_owed(_i3_rrun(_i3_ovm(), (3, 5)), 1))
            check("imported-overhang-reserved-at-max-present-not-counted",
                  _i3_owed(_i3_rrun(_i3_ovm(), (2, 4)), 1))
            check("imported-overhang-reserved-below-max-present-not-counted",
                  _i3_owed(_i3_rrun(_i3_ovm(), (1, 4)), 1))
            _i3_rgap = _i3_rrun(_i3_machine(imp={"imported:BI": 3}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1"), _i3_row("imported:BI-3")])}),
                (2,))
            check("imported-reserved-gap-exempt", _i3_clean_active(_i3_rgap))
            _i3_one_rsv = {"backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1")])}
            _i3_rheld = _i3_rrun(_i3_machine(imp={"imported:BI": 2}, leaves=copy.deepcopy(_i3_one_rsv)), (2,),
                                 obs=_i3_obs({"imported:BI-1": _record_digest(_i3_row("imported:BI-1")),
                                              "imported:BI-2": _record_digest(_i3_row("imported:BI-2"))},
                                             {"imported:BI": 2}))
            check("imported-reserved-number-prior-held-still-owed",
                  _i3_rheld is not None and _i3_rheld.checks.get("C-NO-DELETION") == "FINDING"
                  and any("1 allocated imported:BI-<n> id(s) above imported:BI-1" in f
                          for f in _i3_msgs(_i3_rheld, "C-NO-DELETION")))
            _i3_rnob = _i3_rrun(_i3_machine(imp={"imported:BI": 2}, leaves=copy.deepcopy(_i3_one_rsv)), (2,),
                                obs=clean_prior())
            check("imported-reserved-number-undecided-without-baseline",
                  _i3_rnob is not None and _i3_rnob.checks.get("C-NO-DELETION") == "CANNOT-EVALUATE"
                  and any("1 reserved imported:BI number(s)" in m and "the first imported:BI-2" in m
                          for m in _i3_msgs(_i3_rnob, "C-NO-DELETION"))
                  and not any("allocated imported:BI-<n>" in m for m in _i3_msgs(_i3_rnob, "C-NO-DELETION")))
            # A malformed imported baseline decides nothing either: a clean key (BI-9) in the prior's
            # imported member makes it malformed, so the exemption is the same named CANNOT-EVALUATE even
            # though the member also lists imported:BI-2 (red when the malformed-baseline arm of
            # _imported_prior_holdings is dropped: the partial baseline then reads as holding nothing and
            # the reserved number is exempted).
            _i3_rmf = _i3_rrun(_i3_machine(imp={"imported:BI": 2}, leaves=copy.deepcopy(_i3_one_rsv)), (2,),
                               obs=_i3_obs({"BI-9": _record_digest(_i3_row("imported:BI-1")),
                                            "imported:BI-2": _record_digest(_i3_row("imported:BI-2"))}))
            check("imported-reserved-number-undecided-with-malformed-baseline",
                  _i3_rmf is not None and _i3_rmf.checks.get("C-NO-DELETION") == "CANNOT-EVALUATE"
                  and any("1 reserved imported:BI number(s)" in m and "the first imported:BI-2" in m
                          for m in _i3_msgs(_i3_rmf, "C-NO-DELETION"))
                  and not any("allocated imported:BI-<n>" in m for m in _i3_msgs(_i3_rmf, "C-NO-DELETION")))
            _i3_rwm = _i3_machine(imp={"imported:WL": 3}, leaves={
                "worklog.imported.toml": {"schema": 1, "entry": [imp_wl(1), imp_wl(3)]}})
            check("imported-reserved-wl-gap-exempt",
                  _i3_clean_active(_i3_rrun(copy.deepcopy(_i3_rwm), (2,), key="imported:WL")))
            check("imported-reserved-wl-start-exempt",
                  _i3_clean_active(_i3_rrun(_i3_machine(imp={"imported:WL": 2}, leaves={
                      "worklog.imported.toml": {"schema": 1, "entry": [imp_wl(2)]}}), (1,), key="imported:WL")))
            _i3_rwn = _i3_rrun(_i3_rwm, (2,), obs=clean_prior(), key="imported:WL")
            check("imported-reserved-wl-undecided-without-baseline",
                  _i3_rwn is not None and _i3_rwn.checks.get("C-CONTIGUITY") == "CANNOT-EVALUATE"
                  and any("1 reserved imported:WL number(s)" in m and "the first imported:WL-2" in m
                          for m in _i3_msgs(_i3_rwn, "C-CONTIGUITY"))
                  and not any("imported:WL-2 is missing" in m for m in _i3_msgs(_i3_rwn, "C-CONTIGUITY")))
            # The imported:WL twin of the malformed-baseline vector, on C-CONTIGUITY.
            _i3_rwf = _i3_rrun(copy.deepcopy(_i3_rwm), (2,), key="imported:WL",
                               obs=_i3_obs({"BI-9": _record_digest(imp_wl(1)),
                                            "imported:WL-2": _record_digest(imp_wl(2))}))
            check("imported-reserved-wl-undecided-with-malformed-baseline",
                  _i3_rwf is not None and _i3_rwf.checks.get("C-CONTIGUITY") == "CANNOT-EVALUATE"
                  and any("1 reserved imported:WL number(s)" in m and "the first imported:WL-2" in m
                          for m in _i3_msgs(_i3_rwf, "C-CONTIGUITY"))
                  and not any("imported:WL-2 is missing" in m for m in _i3_msgs(_i3_rwf, "C-CONTIGUITY")))
            # Append-only history over the imported leaves (spec 8.3): an unchanged prior imported record
            # passes; an edited or vanished one is a finding; a prior with no imported baseline is a named
            # CANNOT-EVALUATE; an imported counter regression is a C-HISTORY-COUNTERS finding.
            _i3_one = {"backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1")])}
            _i3_dig = _record_digest(_i3_row("imported:BI-1"))
            _i3_hok = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_one),
                              obs=_i3_obs({"imported:BI-1": _i3_dig}, {"imported:BI": 1}))
            check("imported-history-unchanged-passes", _i3_clean_active(_i3_hok))
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
            # A prior imported key whose namespace the imported series refuses (imported:LF-1: LF is
            # outside IMPORTED_NAMESPACES, spec 8.2) is the SAME malformed-prior cant, never an
            # absent-record finding (red when the namespace arm is dropped from the prior
            # normalization: the key then normalizes and reads as a deleted record).
            _i3_hlf = _i3_run(_i3_machine(), obs=_i3_obs({"imported:LF-1": _i3_dig}))
            check("imported-prior-refused-namespace-member-when-active",
                  _i3_hlf is not None and _i3_hlf.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                  and any("malformed" in m for m in _i3_msgs(_i3_hlf, "C-HISTORY-APPEND-ONLY"))
                  and not any("absent from the imported leaves" in m
                              for m in _i3_msgs(_i3_hlf, "C-HISTORY-APPEND-ONLY")))
            # A prior imported digest that is not a string is the SAME malformed-prior cant, never a changed
            # record finding (red when the digest-type arm is dropped from the prior normalization: the
            # integer then normalizes and differs from the present record's digest).
            _i3_hdt = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_one), obs=_i3_obs({"imported:BI-1": 5}))
            check("imported-prior-non-string-digest-member-when-active",
                  _i3_hdt is not None and _i3_hdt.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                  and any("malformed" in m for m in _i3_msgs(_i3_hdt, "C-HISTORY-APPEND-ONLY"))
                  and not any("changed since the prior committed snapshot" in m
                              for m in _i3_msgs(_i3_hdt, "C-HISTORY-APPEND-ONLY")))
            # An unreadable imported leaf, or one with no [[record]] array, is a named CANNOT-EVALUATE and is
            # never read as an empty leaf: neither its allocated ids nor a prior imported record are reported
            # deleted (spec 11: an unreadable input fails closed; red when an unread leaf joins the
            # no-deletion or the append-only scan). Each per-series check that skips the unread series
            # reports it as NOT EVALUATED itself: C-NO-DELETION and C-HISTORY-APPEND-ONLY name the skipped
            # imported:BI series as cannot-evaluate rather than keeping a per-check PASS over a leaf
            # nothing read (red when either skip goes back to a silent per-series pass), while the clean
            # imported worklog line still grades (C-CONTIGUITY stays PASS here: its leaf is absent, not
            # unread).
            _i3_unread = []
            for _i3_text in ("record = [ not toml", "schema = 1\nrecord = 1\n"):
                _i3_ur = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves={
                    "backlog_item.imported.index.toml": _i3_text}), obs=_i3_obs({"imported:BI-1": _i3_dig}))
                _i3_unread.append(
                    _i3_ur is not None and _i3_ur.status == CANNOT_EVALUATE
                    and _i3_ur.checks.get("C-ID-SPACE") == "CANNOT-EVALUATE"
                    and any("backlog_item.imported.index.toml" in m for m in _i3_ur.cannot_evaluate)
                    and _i3_ur.checks.get("C-NO-DELETION") == "CANNOT-EVALUATE"
                    and any("imported:BI" in m and "not evaluated" in m
                            for m in _i3_msgs(_i3_ur, "C-NO-DELETION"))
                    and _i3_ur.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                    and any("imported:BI" in m and "not evaluated" in m
                            for m in _i3_msgs(_i3_ur, "C-HISTORY-APPEND-ONLY"))
                    and _i3_ur.checks.get("C-CONTIGUITY") == "PASS"
                    and not any("absent" in m or "changed" in m
                                for m in _i3_msgs(_i3_ur, "C-NO-DELETION")
                                + _i3_msgs(_i3_ur, "C-HISTORY-APPEND-ONLY")))
            check("imported-unread-leaf-cannot-evaluate-never-deleted", _i3_unread == [True, True])
            # The imported WORKLOG leaf unread: C-CONTIGUITY reports the imported:WL series as not
            # evaluated too (red when the unread leaf keeps the contiguity PASS), beside the same
            # C-NO-DELETION and C-HISTORY-APPEND-ONLY disclosures, and the clean worklog tiling still
            # grades (no clean C-CONTIGUITY finding on this store).
            _i3_wldig = _record_digest(imp_wl(1))
            _i3_wlur = _i3_run(_i3_machine(imp={"imported:WL": 1}, leaves={
                "worklog.imported.toml": "entry = [ not toml"}), obs=_i3_obs({"imported:WL-1": _i3_wldig}))
            check("imported-unread-worklog-leaf-not-evaluated-per-check",
                  _i3_wlur is not None and _i3_wlur.status == CANNOT_EVALUATE
                  and _i3_wlur.checks.get("C-CONTIGUITY") == "CANNOT-EVALUATE"
                  and any("imported:WL" in m and "not evaluated" in m
                          for m in _i3_msgs(_i3_wlur, "C-CONTIGUITY"))
                  and not any(m.startswith("C-CONTIGUITY: the worklog")
                              for m in _i3_msgs(_i3_wlur, "C-CONTIGUITY"))
                  and _i3_wlur.checks.get("C-NO-DELETION") == "CANNOT-EVALUATE"
                  and any("imported:WL" in m and "not evaluated" in m
                          for m in _i3_msgs(_i3_wlur, "C-NO-DELETION"))
                  and _i3_wlur.checks.get("C-HISTORY-APPEND-ONLY") == "CANNOT-EVALUATE"
                  and any("imported:WL" in m and "not evaluated" in m
                          for m in _i3_msgs(_i3_wlur, "C-HISTORY-APPEND-ONLY")))
            # The per-series grading over EVERY enabled namespace, never only the ones the vectors above
            # use: each enabled BASELINE namespace and a module-tier one (governance's MA, enabled inside
            # the fixture) takes the same five vectors end to end, per namespace and per check verdict.
            # The positive leaf (rows within the high-water) passes every per-series check, tolerates
            # only C-IMPORTED-SCHEMA row findings on its own leaf, and carries no cant; an id above
            # its own imported high-water is the exact
            # C-ID-SPACE finding; a duplicated id is the C-ID-SPACE duplicate finding; an allocated id
            # absent from the leaf is the C-NO-DELETION overhang finding with its exact count; and a
            # committed row that changed or vanished since the prior snapshot is each a
            # C-HISTORY-APPEND-ONLY finding. The generic rows carry the imported envelope through
            # imp_row/imp_wl but not every type's full field roster, so only the id series, the
            # counters, and the digests are asserted here per namespace; C-IMPORTED-SCHEMA's row
            # grading has its own vectors above (red when the per-series grading narrows to some
            # namespaces, e.g. a gather that reads only the BI and WL leaves).
            _i3_type_of = {ns: t for t, ns in BASELINE_TYPES.items()}
            _i3_type_of["MA"] = "maintainer_action"
            _i3_series_checks = ("C-COUNTERS", "C-ID-SPACE", "C-NO-DELETION", "C-CONTIGUITY",
                                 "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-CONTAINMENT",
                                 "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE")

            def _i3_series_pass(r, leaf):
                # Beside the per-series checks passing, the ONLY tolerated findings are
                # C-IMPORTED-SCHEMA row findings on the vector's OWN leaf (the generic rows do not
                # carry every type's full field roster, and a module-tier row is refused by that
                # check by design), and with every imported check landed no cant is tolerated: a
                # false finding from any OTHER check on a populated imported leaf, or against any
                # other path, reds the control.
                return (r is not None and pending_only(r)
                        and _i3_bad(r) <= {"C-IMPORTED-SCHEMA"} | set(_imp_pending)
                        and all(r.checks.get(c) == "PASS" for c in _i3_series_checks)
                        and all(f.startswith(".working/toml/" + leaf + "#") for f in r.findings))

            def _i3_srow(ns, n, mark="t"):
                if ns == "WL":
                    row = imp_wl(n)
                    row["summary"] = mark
                    return row
                return imp_row("imported:{}-{}".format(ns, n), _i3_type_of[ns], "done",
                               created_at=TS, updated_at=TS, title=mark)

            def _i3_ns_machine(ns, imp_high, rows):
                tname = _i3_type_of[ns]
                if tname in _LEDGER_TYPES:
                    leaf = {_opf_store.IMPORTED_WORKLOG_NAME: {"schema": 1, "entry": rows}}
                else:
                    leaf = {tname + _opf_store.IMPORTED_INDEX_SUFFIX: idx(rows)}
                m = _i3_machine(imp={"imported:" + ns: imp_high}, leaves=leaf)
                if ns == "MA":
                    m["manifest.toml"]["modules"] = {"governance": True}
                    m["manifest.toml"]["types"]["maintainer_action"] = {"namespace": "MA"}
                    m["counters.toml"]["counters"]["MA"] = 0
                    m["maintainer_action.index.toml"] = idx([])
                return m

            _i3_every_ns = _i3_ns + ["MA"]
            _i3_pos, _i3_ovr, _i3_dup2, _i3_del2, _i3_his = [], [], [], [], []
            for _i3_x in _i3_every_ns:
                _i3_tname = _i3_type_of[_i3_x]
                _i3_leaf = (_opf_store.IMPORTED_WORKLOG_NAME if _i3_tname in _LEDGER_TYPES
                            else _i3_tname + _opf_store.IMPORTED_INDEX_SUFFIX)
                _i3_xr = _i3_run(_i3_ns_machine(_i3_x, 2, [_i3_srow(_i3_x, 1), _i3_srow(_i3_x, 2)]))
                _i3_pos.append(_i3_series_pass(_i3_xr, _i3_leaf))
                _i3_xr = _i3_run(_i3_ns_machine(_i3_x, 1, [_i3_srow(_i3_x, 1), _i3_srow(_i3_x, 2)]))
                _i3_ovr.append(_i3_xr is not None and _i3_xr.checks.get("C-ID-SPACE") == "FINDING"
                               and _i3_msgs(_i3_xr, "C-ID-SPACE") == [
                                   "C-ID-SPACE: imported series: id 'imported:{0}-2' exceeds the "
                                   "imported:{0} high-water 1 (unreserved id; spec 8.2)".format(_i3_x)])
                _i3_xr = _i3_run(_i3_ns_machine(
                    _i3_x, 2, [_i3_srow(_i3_x, 1), _i3_srow(_i3_x, 2), _i3_srow(_i3_x, 2)]))
                _i3_dup2.append(_i3_xr is not None and _i3_xr.checks.get("C-ID-SPACE") == "FINDING"
                                and any("imported series: duplicate id 'imported:{}-2'".format(_i3_x) in f
                                        for f in _i3_msgs(_i3_xr, "C-ID-SPACE")))
                _i3_xr = _i3_run(_i3_ns_machine(_i3_x, 2, [_i3_srow(_i3_x, 1)]))
                _i3_del2.append(_i3_xr is not None and _i3_xr.checks.get("C-NO-DELETION") == "FINDING"
                                and any("1 allocated imported:{0}-<n> id(s) above imported:{0}-1".format(
                                    _i3_x) in f for f in _i3_msgs(_i3_xr, "C-NO-DELETION")))
                _i3_xr = _i3_run(
                    _i3_ns_machine(_i3_x, 2, [_i3_srow(_i3_x, 1, mark="edited")]),
                    obs=_i3_obs({"imported:{}-1".format(_i3_x): _record_digest(_i3_srow(_i3_x, 1)),
                                 "imported:{}-2".format(_i3_x): _record_digest(_i3_srow(_i3_x, 2))},
                                {"imported:" + _i3_x: 2}))
                _i3_his.append(_i3_xr is not None
                               and _i3_xr.checks.get("C-HISTORY-APPEND-ONLY") == "FINDING"
                               and any("imported:{}-1".format(_i3_x) in f
                                       and "changed since the prior committed snapshot" in f
                                       for f in _i3_msgs(_i3_xr, "C-HISTORY-APPEND-ONLY"))
                               and any("imported:{}-2".format(_i3_x) in f
                                       and "absent from the imported leaves" in f
                                       for f in _i3_msgs(_i3_xr, "C-HISTORY-APPEND-ONLY")))
            check("imported-populated-leaf-every-namespace",
                  len(_i3_pos) == len(_i3_every_ns) == 13 and all(_i3_pos))
            check("imported-above-high-water-every-namespace",
                  len(_i3_ovr) == len(_i3_every_ns) and all(_i3_ovr))
            check("imported-duplicate-id-every-namespace",
                  len(_i3_dup2) == len(_i3_every_ns) and all(_i3_dup2))
            check("imported-deleted-allocated-id-every-namespace",
                  len(_i3_del2) == len(_i3_every_ns) and all(_i3_del2))
            check("imported-history-changed-and-vanished-every-namespace",
                  len(_i3_his) == len(_i3_every_ns) and all(_i3_his))

            # --- C-IMPORTED-IDS, C-IMPORTED-PROVENANCE and C-IMPORTED-SEGREGATION (spec 8.3/8.6) ---------
            # Each check carries a positive finding vector and a flip: the SAME vector with that check's
            # grading function replaced by a no-op in this module (restored in finally), under which the
            # check reads PASS with no message of its own, so the finding is that check's and no other
            # path's (red when a vector would only find the defect elsewhere, or the check grades
            # nothing). Its fail-closed vectors grade an unread or malformed input CANNOT-EVALUATE, never
            # a pass. The clean controls are the leaf control above: every imported check passes there
            # and the activated store is VALID.
            def _i3_flip(fn_name, machine, **kw):
                g = globals()
                prev = g[fn_name]
                g[fn_name] = lambda *_a, **_k: None
                try:
                    return _i3_run(machine, **kw)
                finally:
                    g[fn_name] = prev

            def _i3_off(r, cid):
                return r is not None and r.checks.get(cid) == "PASS" and not _i3_msgs(r, cid)

            check("imported-checks-clean-leaf-control-valid",
                  _i3_ok is not None and _i3_ok.status == VALID
                  and all(_i3_ok.checks.get(cid) == "PASS" for cid in IMPORTED_CHECKS))
            # C-IMPORTED-IDS replays the one per-series grading message for message: an id above its own
            # imported high-water (C-ID-SPACE), a row outside its leaf series (the series grammar), and
            # an allocated id absent from its leaf (C-NO-DELETION).
            _i3_idv = _i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2)
            _i3_idr = _i3_run(copy.deepcopy(_i3_idv))
            check("imported-ids-above-high-water-finding",
                  _i3_idr is not None and _i3_idr.checks.get("C-IMPORTED-IDS") == "FINDING"
                  and _i3_msgs(_i3_idr, "C-IMPORTED-IDS")
                  == ["C-IMPORTED-IDS: " + m for m in _i3_msgs(_i3_idr, "C-ID-SPACE")]
                  and any("'imported:BI-2' exceeds the imported:BI high-water 1" in m
                          for m in _i3_msgs(_i3_idr, "C-IMPORTED-IDS")))
            _i3_idf = _i3_flip("_check_imported_ids", copy.deepcopy(_i3_idv))
            check("imported-ids-flip-disabled-passes",
                  _i3_off(_i3_idf, "C-IMPORTED-IDS") and _i3_idf.checks.get("C-ID-SPACE") == "FINDING")
            check("imported-ids-series-grammar-finding",
                  _i3_for is not None and _i3_for.checks.get("C-IMPORTED-IDS") == "FINDING"
                  and len([m for m in _i3_msgs(_i3_for, "C-IMPORTED-IDS")
                           if "is not an imported:BI-<n> id" in m]) == 2)
            check("imported-ids-no-deletion-finding",
                  _i3_del is not None and _i3_del.checks.get("C-IMPORTED-IDS") == "FINDING"
                  and any(m.startswith("C-IMPORTED-IDS: C-NO-DELETION: 1 allocated imported:BI-<n> id(s) "
                                       "above imported:BI-1") for m in _i3_msgs(_i3_del, "C-IMPORTED-IDS")))
            # The imported counter rows, fail-closed: an absent counters.toml owes every row (a finding);
            # an unparseable one, and one under an unsupported schema, are CANNOT-EVALUATE (red when
            # either is graded as an empty or a version-1 table).
            _i3_ca = _i3_machine()
            del _i3_ca["counters.toml"]
            _i3_car = _i3_run(_i3_ca)
            _i3_cu = _i3_machine()
            _i3_cu["counters.toml"] = "counters = [ not toml"
            _i3_cur = _i3_run(_i3_cu)
            _i3_cs = _i3_machine()
            _i3_cs["counters.toml"]["schema"] = 2
            _i3_csr = _i3_run(_i3_cs)
            check("imported-ids-counters-absent-finding",
                  _i3_car is not None and _i3_car.checks.get("C-IMPORTED-IDS") == "FINDING"
                  and any("counters.toml is absent; the imported series requires a counter row" in m
                          for m in _i3_msgs(_i3_car, "C-IMPORTED-IDS")))
            check("imported-ids-counters-unread-or-unsupported-cannot-evaluate",
                  _i3_cur is not None and _i3_cur.checks.get("C-IMPORTED-IDS") == "CANNOT-EVALUATE"
                  and any("could not be read" in m for m in _i3_msgs(_i3_cur, "C-IMPORTED-IDS"))
                  and _i3_csr is not None and _i3_csr.checks.get("C-IMPORTED-IDS") == "CANNOT-EVALUATE"
                  and any("under the supported schema 1" in m for m in _i3_msgs(_i3_csr, "C-IMPORTED-IDS")))
            # An unreadable imported leaf, or one with no [[record]] array: C-IMPORTED-IDS replays the
            # reader's CANNOT-EVALUATE and C-IMPORTED-PROVENANCE names the series it could not re-verify
            # (red when either reads the unread leaf as an empty one and passes).
            _i3_ul = []
            for _i3_text in ("record = [ not toml", "schema = 1\nrecord = 1\n"):
                _i3_ulr = _i3_run(_i3_machine(imp={"imported:BI": 2}, leaves={
                    "backlog_item.imported.index.toml": _i3_text}))
                _i3_ul.append(
                    _i3_ulr is not None and _i3_ulr.checks.get("C-IMPORTED-IDS") == "CANNOT-EVALUATE"
                    and any("backlog_item.imported.index.toml" in m for m in _i3_msgs(_i3_ulr, "C-IMPORTED-IDS"))
                    and _i3_ulr.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                    and any("the imported:BI imported leaf could not be read" in m
                            for m in _i3_msgs(_i3_ulr, "C-IMPORTED-PROVENANCE")))
            check("imported-ids-and-provenance-unread-leaf-cannot-evaluate", _i3_ul == [True, True])
            # C-IMPORTED-PROVENANCE: the planted original changed after import (its inventory still
            # consistent, so C-EVIDENCE-ENUM passes) no longer hashes to either row's source_sha256, the
            # provenance finding alone; with the check disabled the SAME store certifies VALID.
            _i3_pv = _i3_machine(imp={"imported:BI": 2}, leaves=_i3_bi2)
            _i3_tamper = imp_evidence("- [x] an original edited after import\n")
            _i3_pvr = _i3_run(copy.deepcopy(_i3_pv), working=_i3_tamper)
            check("imported-provenance-changed-original-finding",
                  _i3_pvr is not None and _i3_pvr.checks.get("C-IMPORTED-PROVENANCE") == "FINDING"
                  and len([m for m in _i3_msgs(_i3_pvr, "C-IMPORTED-PROVENANCE")
                           if "does not hash to its import.source_sha256" in m]) == 2
                  and _i3_bad(_i3_pvr) == {"C-IMPORTED-PROVENANCE"} | set(_imp_pending))
            _i3_pvf = _i3_flip("_check_imported_provenance", copy.deepcopy(_i3_pv), working=_i3_tamper)
            check("imported-provenance-flip-disabled-passes",
                  _i3_off(_i3_pvf, "C-IMPORTED-PROVENANCE") and _i3_clean_active(_i3_pvf))
            # Missing evidence fails closed (spec 8.3): an absent original is a finding for each row
            # naming it, and an original the contained reader refuses (a directory planted at its path)
            # is CANNOT-EVALUATE naming it.
            _i3_pab = _i3_run(copy.deepcopy(_i3_pv), evidence=False)
            check("imported-provenance-absent-original-finding",
                  _i3_pab is not None and _i3_pab.checks.get("C-IMPORTED-PROVENANCE") == "FINDING"
                  and len([m for m in _i3_msgs(_i3_pab, "C-IMPORTED-PROVENANCE") if "which is absent" in m]) == 2)
            _i3_pdr = _i3_run(copy.deepcopy(_i3_pv), evidence=False,
                              working={IMP_SOURCE[len(WORKING_DIRNAME) + 1:] + "/nested.md": "x"})
            check("imported-provenance-unreadable-original-cannot-evaluate",
                  _i3_pdr is not None and _i3_pdr.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and any(m.startswith("C-IMPORTED-PROVENANCE: cannot read " + IMP_SOURCE)
                          for m in _i3_msgs(_i3_pdr, "C-IMPORTED-PROVENANCE")))
            # A row whose provenance names no preserved original of its own run (a store control file),
            # or carries no 64-hex digest, is never read: CANNOT-EVALUATE beside the C-IMPORTED-SCHEMA
            # finding (red when such a source is read as an original and graded).
            _i3_pm = []
            for _i3_bad_imp in ({"source": ".working/toml/counters.toml"}, {"source_sha256": "NOT-HEX"}):
                _i3_mrow = _i3_row("imported:BI-1")
                _i3_mrow["import"] = dict(_i3_mrow["import"], **_i3_bad_imp)
                _i3_pmr = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                    "backlog_item.imported.index.toml": idx([_i3_mrow])}))
                _i3_pm.append(
                    _i3_pmr is not None and _i3_pmr.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                    and any("names no preserved original of its own import run" in m
                            for m in _i3_msgs(_i3_pmr, "C-IMPORTED-PROVENANCE"))
                    and _i3_pmr.checks.get("C-IMPORTED-SCHEMA") == "FINDING")
            check("imported-provenance-malformed-provenance-cannot-evaluate", _i3_pm == [True, True])
            # A row outside its leaf series is still re-verified: its wrong digest is a provenance
            # finding naming the row by its leaf position (red when only in-series rows are read).
            _i3_srw = _i3_row("BI-2")
            _i3_srw["import"] = dict(_i3_srw["import"], source_sha256="0" * 64)
            _i3_sr = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_row("imported:BI-1"), _i3_srw])}))
            check("imported-provenance-stray-row-still-verified",
                  _i3_sr is not None and _i3_sr.checks.get("C-IMPORTED-PROVENANCE") == "FINDING"
                  and [m for m in _i3_msgs(_i3_sr, "C-IMPORTED-PROVENANCE") if "does not hash" in m] == [
                      m for m in _i3_msgs(_i3_sr, "C-IMPORTED-PROVENANCE")
                      if "backlog_item.imported.index.toml record[1]" in m and "0" * 64 in m])
            # C-IMPORTED-SEGREGATION, the receipt firewall: a maintainer-authored backlog item ratified at
            # done whose only receipt is importer-authored is the firewall finding alone (C-RECEIPTS
            # leaves that item to this arm and passes); with the check disabled the SAME store certifies VALID. An
            # importer-authored item with that receipt is history the firewall does not grade.
            _i3_idn = dn(1, "BI-1")
            _i3_idn["actor"] = {"kind": "importer"}
            _i3_sg = _i3_machine()
            _i3_sg["done.index.toml"] = idx([_i3_idn])
            _i3_sg_obs = _i3_obs()
            _i3_sg_obs["prior"]["digests"]["DN-1"] = _record_digest(_i3_idn)
            _i3_sgr = _i3_run(copy.deepcopy(_i3_sg), obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-importer-receipt-finding",
                  _i3_sgr is not None and _i3_sgr.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                  and len(_i3_msgs(_i3_sgr, "C-IMPORTED-SEGREGATION")) == 1
                  and "'BI-1'" in _i3_msgs(_i3_sgr, "C-IMPORTED-SEGREGATION")[0]
                  and "importer-authored" in _i3_msgs(_i3_sgr, "C-IMPORTED-SEGREGATION")[0]
                  and _i3_sgr.checks.get("C-RECEIPTS") == "PASS"
                  and _i3_bad(_i3_sgr) == {"C-IMPORTED-SEGREGATION"} | set(_imp_pending))
            _i3_sgf = _i3_flip("_check_imported_segregation", copy.deepcopy(_i3_sg), obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-flip-disabled-passes",
                  _i3_off(_i3_sgf, "C-IMPORTED-SEGREGATION") and _i3_clean_active(_i3_sgf))
            _i3_sgl = copy.deepcopy(_i3_sg)
            _i3_sgl["backlog_item.index.toml"] = idx([dict(bi(1, "done"), actor={"kind": "importer"}),
                                                       bi(2, "open")])
            _i3_sglr = _i3_run(_i3_sgl, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-importer-item-not-graded",
                  _i3_sglr is not None and _i3_sglr.checks.get("C-IMPORTED-SEGREGATION") == "PASS")
            # Clean-to-imported links (spec 8.6): relates, derives_from, follows and same-type supersedes
            # pass this check; any other relation, and supersedes to another type, is its finding (red
            # when the relation set widens or the same-type arm is dropped). C-RECORDS and C-LINKS also
            # grade such a link (the clean validator refuses the same relations to an imported id, and
            # C-LINKS resolves an imported target over the union of both series, so a target absent from
            # the imported leaves is its dangling-link finding), so this check is judged on its own
            # verdict; the flip disables it on the refused relation.
            def _i3_link(rel, tid):
                m = _i3_machine()
                m["backlog_item.index.toml"] = idx([bi(1, "done"),
                                                    dict(bi(2, "open"), links=[{"rel": rel, "id": tid}])])
                return m

            _i3_refused = (("remediates", "imported:BI-1"), ("receipt_of", "imported:BI-1"),
                           ("supersedes", "imported:FN-1"))
            _i3_allowed = (("relates", "imported:BI-1"), ("derives_from", "imported:FN-1"),
                           ("follows", "imported:WL-1"), ("supersedes", "imported:BI-1"))
            _i3_lkr = [_i3_run(_i3_link(rel, tid)) for rel, tid in _i3_refused]
            _i3_lka = [_i3_run(_i3_link(rel, tid)) for rel, tid in _i3_allowed]
            check("imported-segregation-clean-to-imported-link-finding",
                  all(r is not None and r.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                      and len(_i3_msgs(r, "C-IMPORTED-SEGREGATION")) == 1
                      and tid in _i3_msgs(r, "C-IMPORTED-SEGREGATION")[0]
                      for r, (_lrel, tid) in zip(_i3_lkr, _i3_refused))
                  and all(r is not None and r.checks.get("C-IMPORTED-SEGREGATION") == "PASS" for r in _i3_lka))
            check("imported-segregation-link-flip-disabled-passes",
                  _i3_off(_i3_flip("_check_imported_segregation", _i3_link(*_i3_refused[0])),
                          "C-IMPORTED-SEGREGATION"))
            # Fail-closed: a clean index nothing could read leaves its records unseated, so the firewall
            # is CANNOT-EVALUATE naming the reader that did not evaluate, never a pass over them, in one
            # message for all its arms (red when the check also hands its arms the unseated list).
            _i3_sgu = _i3_machine()
            _i3_sgu["done.index.toml"] = "record = [ not toml"
            _i3_sgur = _i3_run(_i3_sgu)
            check("imported-segregation-unread-clean-records-cannot-evaluate",
                  _i3_sgur is not None and _i3_sgur.checks.get("C-IMPORTED-SEGREGATION") == "CANNOT-EVALUATE"
                  and any("C-RECORDS did not evaluate" in m for m in _i3_msgs(_i3_sgur, "C-IMPORTED-SEGREGATION"))
                  and len([m for m in _i3_msgs(_i3_sgur, "C-IMPORTED-SEGREGATION") if "did not evaluate" in m]) == 1)
            # The other two fail-closed arms, each on its own store: an archive bucket whose
            # archive.toml cannot be read makes C-ARCHIVE-ENUM CANNOT-EVALUATE, and a per-record
            # registry row that is not a table makes C-PERRECORD-RECONCILE CANNOT-EVALUATE; the
            # firewall then names the one reader whose records it could not seat, never a pass over
            # them (red when either arm leaves the unseated list).
            _i3_sga = _i3_machine()
            _i3_sga["archive/2026/archive.toml"] = "moved = [ not toml"
            _i3_sgar = _i3_run(_i3_sga)
            check("imported-segregation-unread-archive-cannot-evaluate",
                  _i3_sgar is not None and _i3_sgar.checks.get("C-ARCHIVE-ENUM") == "CANNOT-EVALUATE"
                  and _i3_sgar.checks.get("C-IMPORTED-SEGREGATION") == "CANNOT-EVALUATE"
                  and any("C-ARCHIVE-ENUM did not evaluate" in m
                          for m in _i3_msgs(_i3_sgar, "C-IMPORTED-SEGREGATION")))

            def _i3_pr_machine():
                # The activated store in the per-record layout: each non-empty clean index becomes the
                # registry and its rows the record files; the ledgers and the imported leaves are
                # unchanged (the first imported-series release keeps them inline in either layout).
                m = _i3_machine()
                m["manifest.toml"]["opf"]["layout"] = "per-record"
                for _t, _rows in (("backlog_item", [bi(1, "done"), bi(2, "open")]),
                                  ("done", [dn(1, "BI-1")]), ("handoff", [ho(1)])):
                    m[_t + ".index.toml"] = {"schema": 1, "record": [
                        {"id": r["id"], "state": r["status"], "path": "{}/{}.toml".format(_t, r["id"]),
                         "digest": _record_digest(r)} for r in _rows]}
                    for r in _rows:
                        m["{}/{}.toml".format(_t, r["id"])] = r
                return m

            _i3_prc = _i3_run(_i3_pr_machine())
            _i3_prm = _i3_pr_machine()
            _i3_prm["finding.index.toml"] = "schema = 1\nrecord = [ 7 ]\n"
            _i3_prr = _i3_run(_i3_prm)
            check("imported-segregation-unread-perrecord-cannot-evaluate",
                  _i3_prc is not None and _i3_prc.checks.get("C-PERRECORD-RECONCILE") == "PASS"
                  and _i3_prc.checks.get("C-IMPORTED-SEGREGATION") == "PASS"
                  and _i3_prr is not None
                  and _i3_prr.checks.get("C-PERRECORD-RECONCILE") == "CANNOT-EVALUATE"
                  and _i3_prr.checks.get("C-IMPORTED-SEGREGATION") == "CANNOT-EVALUATE"
                  and any("C-PERRECORD-RECONCILE did not evaluate" in m
                          for m in _i3_msgs(_i3_prr, "C-IMPORTED-SEGREGATION")))
            # The spec 8.6 receipt remedy: a maintainer-authored clean maintainer_decision linking
            # `corrects` to the ratified done item satisfies the receipt obligation in place of the
            # withdrawn importer-authored receipt, so the finding store above with the remedy added
            # draws no segregation finding and certifies VALID (red when the remedy is not accepted).
            # Whether its text records that the completion stands is prose no validator grades, so any
            # maintainer-authored corrects decision is accepted (a disclosed residual). A remedy
            # authored by an importer, or correcting another id, confers nothing: the finding stays.
            def _i3_remedy(actor_kind="maintainer", target="BI-1"):
                m = copy.deepcopy(_i3_sg)
                m["maintainer_decision.index.toml"] = idx([md(1, decision="BI-1 completion stands",
                                                              actor={"kind": actor_kind},
                                                              links=[{"rel": "corrects", "id": target}])])
                m["counters.toml"]["counters"]["MD"] = 1
                return m

            _i3_rmr = _i3_run(_i3_remedy(), obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-corrects-remedy-accepted",
                  _i3_rmr is not None and _i3_rmr.status == VALID
                  and _i3_rmr.checks.get("C-IMPORTED-SEGREGATION") == "PASS"
                  and not _i3_msgs(_i3_rmr, "C-IMPORTED-SEGREGATION"))
            _i3_rmi = _i3_run(_i3_remedy(actor_kind="importer"), obs=copy.deepcopy(_i3_sg_obs))
            _i3_rmo = _i3_run(_i3_remedy(target="BI-2"), obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-importer-remedy-confers-nothing",
                  _i3_rmi is not None and _i3_rmi.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                  and any("'BI-1'" in m for m in _i3_msgs(_i3_rmi, "C-IMPORTED-SEGREGATION"))
                  and _i3_rmo is not None and _i3_rmo.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                  and any("'BI-1'" in m for m in _i3_msgs(_i3_rmo, "C-IMPORTED-SEGREGATION")))
            # The receipt firewall's edges: an item holding one importer and one maintainer receipt
            # holds a clean receipt, so the firewall passes, and C-RECEIPTS passes too, since an
            # importer-authored done confers no receipt and so is no second one (see _check_receipts; red
            # when the arm's all() weakens to any(), or when the count reads importer-authored receipts
            # again); a proposed done item is not ratified, so it is not graded (red when the qualifier
            # guard is dropped).
            _i3_mx = _i3_machine()
            _i3_mx["done.index.toml"] = idx([copy.deepcopy(_i3_idn), dn(2, "BI-1")])
            _i3_mx["counters.toml"]["counters"]["DN"] = 2
            _i3_mxr = _i3_run(_i3_mx, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-mixed-receipts-pass",
                  _i3_mxr is not None and _i3_mxr.checks.get("C-IMPORTED-SEGREGATION") == "PASS"
                  and not _i3_msgs(_i3_mxr, "C-IMPORTED-SEGREGATION")
                  and _i3_mxr.checks.get("C-RECEIPTS") == "PASS"
                  and not _i3_msgs(_i3_mxr, "C-RECEIPTS"))
            _i3_ql = _i3_machine()
            _i3_ql["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open"),
                                                     dict(bi(3, "done/proposed"),
                                                          actor={"kind": "assistant"})])
            _i3_qdn = dn(2, "BI-3")
            _i3_qdn["actor"] = {"kind": "importer"}
            _i3_ql["done.index.toml"] = idx([dn(1, "BI-1"), _i3_qdn])
            _i3_ql["counters.toml"]["counters"].update({"BI": 3, "DN": 2})
            _i3_qlr = _i3_run(_i3_ql)
            check("imported-segregation-proposed-done-not-graded",
                  _i3_qlr is not None and _i3_qlr.checks.get("C-IMPORTED-SEGREGATION") == "PASS"
                  and not _i3_msgs(_i3_qlr, "C-IMPORTED-SEGREGATION"))
            # The decision-chain firewall (spec 8.6): "An importer-authored decision MUST NOT be
            # treated as the current effective resolution of a clean pending_decision chain". An
            # importer-authored PD-1 decided alone is a chain no non-importer decision resolves, which
            # C-DECISION-CHAINS does not list, so its importer resolution is this check's finding alone
            # and the store must not certify (red when the chain arm is dropped or skips every chain).
            # An importer-authored decision's `supersedes` links remove no current authority, over the
            # chain section 8.8 defines with those links included: a maintainer PD-1 decided plus an
            # importer-authored PD-2 that supersedes it keeps PD-1 the chain's one resolution and
            # certifies (red when an importer-authored link counts toward supersession again, which
            # leaves the importer decision the chain's resolution), while a maintainer PD-3 that then
            # supersedes PD-2 is a second current resolution of the same chain, a C-DECISION-CHAINS
            # finding with this check passing (red when the chain derivation drops importer-authored
            # decisions again, which splits PD-1 and PD-3 into two chains that each pass). The same
            # chain resolved by a maintainer PD-2, and an importer-authored PD-1 a maintainer PD-2
            # supersedes (history below a clean resolution), each certify.
            def _i3_chain(cur_actor=None, old_actor=None, head_actor=None):
                m = _i3_machine()
                p1, p2 = pd(1), pd(2, supersedes="PD-1")
                if old_actor is not None:
                    p1["actor"] = {"kind": old_actor}
                if cur_actor is not None:
                    p2["actor"] = {"kind": cur_actor}
                rows = [p1, p2]
                if head_actor is not None:
                    rows.append(dict(pd(3, supersedes="PD-2"), actor={"kind": head_actor}))
                m["pending_decision.index.toml"] = idx(rows)
                m["counters.toml"]["counters"]["PD"] = len(rows)
                return m

            def _i3_lone():
                m = _i3_machine()
                m["pending_decision.index.toml"] = idx([dict(pd(1), actor={"kind": "importer"})])
                m["counters.toml"]["counters"]["PD"] = 1
                return m

            _i3_dcr = _i3_run(_i3_lone())
            check("imported-segregation-importer-current-resolution-finding",
                  _i3_dcr is not None and _i3_dcr.status != VALID
                  and _i3_dcr.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                  and _i3_dcr.checks.get("C-DECISION-CHAINS") == "PASS"
                  and _i3_msgs(_i3_dcr, "C-IMPORTED-SEGREGATION")
                  == ["C-IMPORTED-SEGREGATION: importer-authored pending_decision 'PD-1' is the current "
                      "effective resolution of a clean pending_decision chain; resolving it now takes "
                      "a new clean decision linking back (spec 8.6)"]
                  and _i3_bad(_i3_dcr) == {"C-IMPORTED-SEGREGATION"})
            check("imported-segregation-importer-link-keeps-clean-resolution",
                  _i3_clean_active(_i3_run(_i3_chain(cur_actor="importer"))))
            _i3_jtr = _i3_run(_i3_chain(cur_actor="importer", head_actor="maintainer"))
            check("imported-segregation-importer-joined-two-heads-finding",
                  _i3_jtr is not None and _i3_jtr.status != VALID
                  and _i3_jtr.checks.get("C-IMPORTED-SEGREGATION") == "PASS"
                  and _i3_msgs(_i3_jtr, "C-DECISION-CHAINS")
                  == ["C-DECISION-CHAINS: a pending_decision supersession chain with a decided resolution "
                      "has 2 current effective resolutions; exactly one exists per chain (spec 8.5)"]
                  and _i3_bad(_i3_jtr) == {"C-DECISION-CHAINS"})
            check("imported-segregation-clean-resolution-passes",
                  _i3_clean_active(_i3_run(_i3_chain()))
                  and _i3_clean_active(_i3_run(_i3_chain(old_actor="importer"))))
            check("imported-segregation-chain-flip-disabled-passes",
                  _i3_off(_i3_flip("_check_imported_segregation", _i3_lone()), "C-IMPORTED-SEGREGATION"))
            # The chain arm grades importer authorship only: a chain whose current resolution an assistant
            # or automation actor authored (ratified, so unqualified) certifies (red when the arm widens
            # to every non-maintainer actor). A maintainer decision that links the item by any relation
            # but `corrects` is no remedy (red when the remedy accepts any relation).
            check("imported-segregation-non-importer-resolution-passes",
                  _i3_clean_active(_i3_run(_i3_chain(cur_actor="assistant")))
                  and _i3_clean_active(_i3_run(_i3_chain(cur_actor="automation"))))
            _i3_rmx = _i3_remedy()
            _i3_rmx["maintainer_decision.index.toml"]["record"][0]["links"] = [{"rel": "relates", "id": "BI-1"}]
            _i3_rmxr = _i3_run(_i3_rmx, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-non-corrects-remedy-confers-nothing",
                  _i3_rmxr is not None and _i3_rmxr.checks.get("C-IMPORTED-SEGREGATION") == "FINDING"
                  and any("'BI-1'" in m for m in _i3_msgs(_i3_rmxr, "C-IMPORTED-SEGREGATION")))
            # Fail-closed over a partial population while every clean reader still evaluates (spec 8.6:
            # missing or unreadable evidence fails closed). On the receipt store above, the importer-authored
            # receipt's links read as a string (a C-RECORDS finding), or that receipt enumerated as rotated
            # to a destination that does not hold it (a C-ARCHIVE-ENUM finding), leaves BI-1 with no receipt
            # the firewall can read; on the chain store above, the importer-authored PD-2's status does
            # not parse (a C-RECORDS finding), so the chain reads as resolved by the maintainer. Each
            # is CANNOT-EVALUATE naming the cause and the rule not run, with no finding of that rule, never
            # a pass over the records that were read (red when the firewall ignores the collectors' unread
            # set or _make_rec's unparsed fields).
            def _i3_seg_cant(r, cause, rule):
                return (r is not None and r.checks.get("C-IMPORTED-SEGREGATION") == "CANNOT-EVALUATE"
                        and any(cause in m and rule in m for m in _i3_msgs(r, "C-IMPORTED-SEGREGATION")))

            def _i3_seg_quiet(r):
                return r is not None and not any(m.startswith("C-IMPORTED-SEGREGATION") for m in r.findings)

            _i3_ulk = copy.deepcopy(_i3_sg)
            _i3_ulk_dn = dict(copy.deepcopy(_i3_idn), links="receipt_of BI-1")
            _i3_ulk["done.index.toml"] = idx([_i3_ulk_dn])
            _i3_ulk_obs = copy.deepcopy(_i3_sg_obs)
            _i3_ulk_obs["prior"]["digests"]["DN-1"] = _record_digest(_i3_ulk_dn)
            _i3_ulkr = _i3_run(_i3_ulk, obs=_i3_ulk_obs)
            check("imported-segregation-uninterpreted-receipt-cannot-evaluate",
                  _i3_ulkr is not None and _i3_ulkr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_ulkr.checks.get("C-ARCHIVE-ENUM") == "PASS" and _i3_seg_quiet(_i3_ulkr)
                  and _i3_seg_cant(_i3_ulkr, "the status or links of 'done' records could not be interpreted",
                                   "the receipt firewall"))
            _i3_umv = copy.deepcopy(_i3_sg)
            _i3_umv["done.index.toml"] = idx([])
            _i3_umv["archive/2026/archive.toml"]["moved"] = [
                {"id": "DN-1", "type": "done", "destination": "archive/2026/done.index.toml"}]
            _i3_umv["archive/2026/done.index.toml"] = idx([])
            _i3_umvr = _i3_run(_i3_umv, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-unconfirmed-moved-receipt-cannot-evaluate",
                  _i3_umvr is not None and _i3_umvr.checks.get("C-RECORDS") == "PASS"
                  and _i3_umvr.checks.get("C-ARCHIVE-ENUM") == "FINDING" and _i3_seg_quiet(_i3_umvr)
                  and _i3_seg_cant(_i3_umvr, "the records of 'done' could not all be read", "the receipt firewall"))
            _i3_ust = _i3_chain(cur_actor="importer")
            _i3_ust["pending_decision.index.toml"]["record"][1]["status"] = "settled"
            _i3_ustr = _i3_run(_i3_ust)
            check("imported-segregation-uninterpreted-chain-member-cannot-evaluate",
                  _i3_ustr is not None and _i3_ustr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_ustr.checks.get("C-ARCHIVE-ENUM") == "PASS" and _i3_seg_quiet(_i3_ustr)
                  and _i3_seg_cant(_i3_ustr,
                                   "the status or links of 'pending_decision' records could not be interpreted",
                                   "the decision-chain firewall"))
            # The receipt firewall's other two types: the ratified item's status does not parse on the
            # receipt store above, so the arm cannot tell it is ratified, and the remedy decision's status
            # does not parse on the remedy store above. Each is the arm's CANNOT-EVALUATE, never the pass
            # either store reads as over the records it could interpret (red when the arm's type set
            # drops backlog_item or maintainer_decision).
            _i3_ubi = copy.deepcopy(_i3_sg)
            _i3_ubi["backlog_item.index.toml"] = idx([bi(1, "finished"), bi(2, "open")])
            _i3_ubir = _i3_run(_i3_ubi, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-uninterpreted-item-cannot-evaluate",
                  _i3_ubir is not None and _i3_ubir.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_cant(_i3_ubir,
                                   "the status or links of 'backlog_item' records could not be interpreted",
                                   "the receipt firewall"))
            _i3_umd = _i3_remedy()
            _i3_umd["maintainer_decision.index.toml"]["record"][0]["status"] = "noted"
            _i3_umdr = _i3_run(_i3_umd, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-uninterpreted-remedy-cannot-evaluate",
                  _i3_umdr is not None and _i3_umdr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_cant(_i3_umdr,
                                   "the status or links of 'maintainer_decision' records could not be "
                                   "interpreted", "the receipt firewall"))
            # The link rule reads every clean record's links, so a record of any other type whose links do
            # not parse (here the store's handoff, beside a C-RECORDS finding), or a type whose records
            # were not all read (here a rotated reference its destination does not hold, beside a
            # C-ARCHIVE-ENUM finding), is CANNOT-EVALUATE naming the cause, while the receipt and chain
            # rules, whose types were read in full, still run and stay quiet (red when the link rule
            # ignores either).
            _i3_hlk = _i3_machine()
            _i3_hlk["handoff.index.toml"] = idx([dict(ho(1), links="remediates imported:BI-1")])
            _i3_hlkr = _i3_run(_i3_hlk)
            check("imported-segregation-uninterpreted-links-other-type-cannot-evaluate",
                  _i3_hlkr is not None and _i3_hlkr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_cant(_i3_hlkr, "the links of 'handoff' records could not be interpreted",
                                   "the clean-to-imported link rule")
                  and len(_i3_msgs(_i3_hlkr, "C-IMPORTED-SEGREGATION")) == 1)
            _i3_rmv = _i3_machine()
            _i3_rmv["archive/2026/archive.toml"]["moved"] = [
                {"id": "RF-9", "type": "reference", "destination": "archive/2026/reference.index.toml"}]
            _i3_rmv["archive/2026/reference.index.toml"] = idx([])
            _i3_rmv["counters.toml"]["counters"]["RF"] = 9
            _i3_rmvr = _i3_run(_i3_rmv)
            check("imported-segregation-unread-other-type-cannot-evaluate",
                  _i3_rmvr is not None and _i3_rmvr.checks.get("C-RECORDS") == "PASS"
                  and _i3_rmvr.checks.get("C-ARCHIVE-ENUM") == "FINDING"
                  and _i3_seg_cant(_i3_rmvr, "the records of 'reference' could not all be read",
                                   "the clean-to-imported link rule")
                  and len(_i3_msgs(_i3_rmvr, "C-IMPORTED-SEGREGATION")) == 1)
            # The decision-chain arm reads the collectors' unread set itself: on the importer-only chain
            # store above, a moved pending_decision row its destination does not hold (a C-ARCHIVE-ENUM
            # finding) leaves the pending_decision population unconfirmed, so the decision-chain firewall
            # is CANNOT-EVALUATE naming it, with no finding of that rule (red when the chain arm ignores the
            # unread set; the link rule's CANNOT-EVALUATE alone would leave the verdict unchanged).
            _i3_cmv = _i3_lone()
            _i3_cmv["archive/2026/archive.toml"]["moved"] = [
                {"id": "PD-2", "type": "pending_decision",
                 "destination": "archive/2026/pending_decision.index.toml"}]
            _i3_cmv["archive/2026/pending_decision.index.toml"] = idx([])
            _i3_cmv["counters.toml"]["counters"]["PD"] = 2
            _i3_cmvr = _i3_run(_i3_cmv)
            check("imported-segregation-unconfirmed-moved-chain-member-cannot-evaluate",
                  _i3_cmvr is not None and _i3_cmvr.checks.get("C-RECORDS") == "PASS"
                  and _i3_cmvr.checks.get("C-ARCHIVE-ENUM") == "FINDING" and _i3_seg_quiet(_i3_cmvr)
                  and _i3_seg_cant(_i3_cmvr, "the records of 'pending_decision' could not all be read",
                                   "the decision-chain firewall"))
            # An actor kind that is absent or unreadable (a C-RECORDS finding) is unknown provenance, never a
            # non-importer authority. Beside the importer-authored receipt above, a second receipt whose
            # kind is a number or that has no actor, or an actorless receipt in its place, leaves
            # C-RECEIPTS and the receipt firewall CANNOT-EVALUATE naming it, with no firewall finding;
            # each read as a clean receipt and passed (red when _make_rec, the receipt join or the firewall
            # gate drops the actor cause).
            def _i3_kind_receipts(rows):
                m = copy.deepcopy(_i3_sg)
                m["done.index.toml"] = idx(rows)
                m["counters.toml"]["counters"]["DN"] = len(rows)
                o = copy.deepcopy(_i3_sg_obs)
                o["prior"]["digests"]["DN-1"] = _record_digest(rows[0])
                return _i3_run(m, obs=o)

            _i3_dn2_na = dn(2, "BI-1")
            del _i3_dn2_na["actor"]
            _i3_dn1_na = dn(1, "BI-1")
            del _i3_dn1_na["actor"]
            _i3_kr = [_i3_kind_receipts([copy.deepcopy(_i3_idn), dict(dn(2, "BI-1"), actor={"kind": 7})]),
                      _i3_kind_receipts([copy.deepcopy(_i3_idn), _i3_dn2_na]),
                      _i3_kind_receipts([_i3_dn1_na])]
            _i3_dn_kind = "the actor kind of 'done' records is absent or unreadable"
            check("imported-segregation-unreadable-receipt-kind-cannot-evaluate",
                  all(r is not None and r.checks.get("C-RECORDS") == "FINDING"
                      and r.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
                      and any(_i3_dn_kind in m and "the receipt join" in m for m in _i3_msgs(r, "C-RECEIPTS"))
                      and _i3_seg_quiet(r) and _i3_seg_cant(r, _i3_dn_kind, "the receipt firewall")
                      for r in _i3_kr))
            # The same on the chain store: the resolution's actor kind a number, or its actor absent, leaves
            # C-DECISION-CHAINS and the decision-chain firewall CANNOT-EVALUATE naming it, never the pass
            # each read as over a non-importer resolution (red when _make_rec, the chain join or the
            # firewall gate drops the actor cause).
            _i3_cna = _i3_chain()
            del _i3_cna["pending_decision.index.toml"]["record"][1]["actor"]
            _i3_pd_kind = "the actor kind of 'pending_decision' records is absent or unreadable"
            check("imported-segregation-unreadable-chain-kind-cannot-evaluate",
                  all(r is not None and r.checks.get("C-RECORDS") == "FINDING"
                      and r.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
                      and any(_i3_pd_kind in m for m in _i3_msgs(r, "C-DECISION-CHAINS"))
                      and _i3_seg_quiet(r) and _i3_seg_cant(r, _i3_pd_kind, "the decision-chain firewall")
                      for r in (_i3_run(_i3_chain(cur_actor=7)), _i3_run(_i3_cna))))
            # The receipt firewall's other two types: the ratified item's actor kind a number beside its
            # importer-authored receipt, which the firewall read as a non-importer item and reported, and
            # the remedy decision's actor a string, not a table, which it read as no remedy and reported.
            # Each is the firewall's CANNOT-EVALUATE naming the type, never a finding over an author not
            # known (red when the firewall gate drops the actor cause).
            _i3_kbi = copy.deepcopy(_i3_sg)
            _i3_kbi["backlog_item.index.toml"] = idx([dict(bi(1, "done"), actor={"kind": 7}), bi(2, "open")])
            _i3_kbir = _i3_run(_i3_kbi, obs=copy.deepcopy(_i3_sg_obs))
            _i3_kmd = _i3_remedy()
            _i3_kmd["maintainer_decision.index.toml"]["record"][0]["actor"] = "maintainer"
            _i3_kmdr = _i3_run(_i3_kmd, obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-unreadable-item-and-remedy-kind-cannot-evaluate",
                  _i3_kbir is not None and _i3_kbir.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_quiet(_i3_kbir)
                  and _i3_seg_cant(_i3_kbir, "the actor kind of 'backlog_item' records is absent or unreadable",
                                   "the receipt firewall")
                  and _i3_kmdr is not None and _i3_kmdr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_quiet(_i3_kmdr)
                  and _i3_seg_cant(_i3_kmdr,
                                   "the actor kind of 'maintainer_decision' records is absent or unreadable",
                                   "the receipt firewall"))
            # A lookalike kind the schema does not admit (another case, an extension, padding; a C-RECORDS
            # finding) is unknown provenance as well, and never the importer kind. Beside the
            # importer-authored receipt above, a second receipt of such a kind leaves C-RECEIPTS and the
            # receipt firewall CANNOT-EVALUATE naming it, with no finding of either; each read it as a
            # clean non-importer receipt and passed (red when _make_rec takes a kind outside ACTOR_KINDS as
            # known provenance).
            _i3_lk_kinds = ("Importer", "importer-legacy", " importer")
            _i3_lk_dn = ("the actor kind of 'done' records is absent or unreadable, or not a kind the schema "
                         "admits")
            check("imported-segregation-lookalike-receipt-kind-cannot-evaluate",
                  all(r is not None and r.checks.get("C-RECORDS") == "FINDING"
                      and r.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
                      and not any(m.startswith("C-RECEIPTS:") for m in r.findings)
                      and any(_i3_lk_dn in m and "the receipt join" in m for m in _i3_msgs(r, "C-RECEIPTS"))
                      and _i3_seg_quiet(r) and _i3_seg_cant(r, _i3_lk_dn, "the receipt firewall")
                      for r in [_i3_kind_receipts([copy.deepcopy(_i3_idn),
                                                   dict(dn(2, "BI-1"), actor={"kind": k})])
                                for k in _i3_lk_kinds]))
            # The same on the chain store: a chain member of such a kind, the current resolution or the
            # decision that resolution supersedes, leaves C-DECISION-CHAINS and the decision-chain firewall
            # CANNOT-EVALUATE naming it, never the pass each read as over a non-importer member (red when
            # _make_rec takes a kind outside ACTOR_KINDS as known provenance).
            _i3_lk_pd = ("the actor kind of 'pending_decision' records is absent or unreadable, or not a "
                         "kind the schema admits")
            check("imported-segregation-lookalike-chain-kind-cannot-evaluate",
                  all(r is not None and r.checks.get("C-RECORDS") == "FINDING"
                      and r.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
                      and not any(m.startswith("C-DECISION-CHAINS:") for m in r.findings)
                      and any(_i3_lk_pd in m for m in _i3_msgs(r, "C-DECISION-CHAINS"))
                      and _i3_seg_quiet(r) and _i3_seg_cant(r, _i3_lk_pd, "the decision-chain firewall")
                      for r in [_i3_run(_i3_chain(cur_actor=k)) for k in _i3_lk_kinds]
                      + [_i3_run(_i3_chain(old_actor="Importer"))]))
            # The receipt firewall's other two types: a ratified item of such a kind beside its
            # importer-authored receipt, which the firewall read as a non-importer item and reported, and a
            # remedy decision of such a kind, which it read as no remedy and reported. Each is the
            # firewall's CANNOT-EVALUATE naming the type, never a finding over an author not known (red
            # when _make_rec takes a kind outside ACTOR_KINDS as known provenance).
            _i3_lkb = copy.deepcopy(_i3_sg)
            _i3_lkb["backlog_item.index.toml"] = idx([dict(bi(1, "done"), actor={"kind": "Importer"}),
                                                      bi(2, "open")])
            _i3_lkbr = _i3_run(_i3_lkb, obs=copy.deepcopy(_i3_sg_obs))
            _i3_lkmr = _i3_run(_i3_remedy(actor_kind="Maintainer"), obs=copy.deepcopy(_i3_sg_obs))
            check("imported-segregation-lookalike-item-and-remedy-kind-cannot-evaluate",
                  _i3_lkbr is not None and _i3_lkbr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_quiet(_i3_lkbr)
                  and _i3_seg_cant(_i3_lkbr, "the actor kind of 'backlog_item' records is absent or "
                                   "unreadable, or not a kind the schema admits", "the receipt firewall")
                  and _i3_lkmr is not None and _i3_lkmr.checks.get("C-RECORDS") == "FINDING"
                  and _i3_seg_quiet(_i3_lkmr)
                  and _i3_seg_cant(_i3_lkmr, "the actor kind of 'maintainer_decision' records is absent or "
                                   "unreadable, or not a kind the schema admits", "the receipt firewall"))
            # The section 8.6 residuals: an active store discloses the firewall entries and the
            # imported-series entry, and the inactive clean store, which holds no importer-authored record,
            # discloses neither (red when either disclosure is dropped or widened); the firewall-inactive
            # checks cover an inactive store that holds one. The pins are independent
            # of the live constants: sha256 over each tuple's entries joined by a newline, UTF-8, so a
            # changed, dropped, added or reordered entry fails here until the pin is renewed with it.
            check("imported-residuals-disclosed-active-not-clean",
                  _i3_ok is not None
                  and all(x in _i3_ok.residuals for x in _FIREWALL_RESIDUALS + _IMPORTED_RESIDUALS)
                  and clean is not None
                  and not any(x in clean.residuals for x in _FIREWALL_RESIDUALS + _IMPORTED_RESIDUALS))
            check("imported-residuals-pinned",
                  len(_FIREWALL_RESIDUALS) == 3
                  and hashlib.sha256("\n".join(_FIREWALL_RESIDUALS).encode("utf-8")).hexdigest()
                  == "002825b3eb55681aa1637241bd87f902d91179c59db90062ed4ed88366e1a594"
                  and len(_IMPORTED_RESIDUALS) == 1
                  and hashlib.sha256("\n".join(_IMPORTED_RESIDUALS).encode("utf-8")).hexdigest()
                  == "15be7339b7047adcc7f4831e29a52a00c87b93eca3cfbe58ea8a65045176dfbf")
            # A leaf row that is not a table: C-IMPORTED-PROVENANCE is CANNOT-EVALUATE naming it,
            # never a pass over evidence nothing read (the series grammar flags the row under
            # C-ID-SPACE; red when such a row is silently skipped).
            _i3_nt = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": "schema = 1\nrecord = [ 7 ]\n"}))
            check("imported-provenance-non-table-row-cannot-evaluate",
                  _i3_nt is not None and _i3_nt.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and any("is not a table, so the original it names is not re-verified" in m
                          for m in _i3_msgs(_i3_nt, "C-IMPORTED-PROVENANCE")))
            # A row whose import.source names ANOTHER run's preserved original: not an original of its
            # own run, so provenance is CANNOT-EVALUATE even though the named path exists and hashes
            # to the recorded digest (red when _is_preserved_original accepts another run's originals:
            # the row then re-verifies against the planted bytes and passes).
            _i3_other = "imp-20260601T000000Z-fedcba9876543210"
            _i3_osrc = ".working/imported/import/{}/originals/TODO.md".format(_i3_other)
            _i3_orow = _i3_row("imported:BI-1")
            _i3_orow["import"] = dict(_i3_orow["import"], source=_i3_osrc)
            _i3_or = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_orow])}),
                working={_i3_osrc[len(WORKING_DIRNAME) + 1:]: IMP_ORIGINAL})
            check("imported-provenance-other-run-original-cannot-evaluate",
                  _i3_or is not None and _i3_or.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and any("names no preserved original of its own import run" in m
                          for m in _i3_msgs(_i3_or, "C-IMPORTED-PROVENANCE")))
            # A source that dot-dot-escapes below its run's originals/ prefix: refused by the
            # canonical store-path rule, so the row is the same CANNOT-EVALUATE and the escaping path
            # is never read (red when the _home_file canonicality check is removed: the prefix test
            # alone then accepts the path and the row is read or graded).
            _i3_erow = _i3_row("imported:BI-1")
            _i3_erow["import"] = dict(_i3_erow["import"],
                                      source=IMP_SOURCE + "/../../../../toml/counters.toml")
            _i3_er = _i3_run(_i3_machine(imp={"imported:BI": 1}, leaves={
                "backlog_item.imported.index.toml": idx([_i3_erow])}))
            check("imported-provenance-dotdot-original-cannot-evaluate",
                  _i3_er is not None and _i3_er.checks.get("C-IMPORTED-PROVENANCE") == "CANNOT-EVALUATE"
                  and any("names no preserved original of its own import run" in m
                          for m in _i3_msgs(_i3_er, "C-IMPORTED-PROVENANCE")))
            # Homes 2 supported WITHOUT imported-series support (SUPPORTED_SPEC_VERSION back at its shipped
            # value). The 2.0.0 / homes = 2 store grades generation 2, but the flag is False, so an imported
            # counter row stays the former C-COUNTERS finding and the leaves are never graded (red when the
            # grading reads the homes-2 generation alone).
            _opf_store.SUPPORTED_SPEC_VERSION = _i3_prev_ssv
            _i3_h2 = run(_i3_machine(imp={"imported:BI": 1}, leaves=_i3_bi2))
            check("imported-grading-off-at-homes2-without-imported-support",
                  _i3_rows_refused_inactive(_i3_h2) and _i3_h2.checks.get("C-EVIDENCE-ENUM") == "PASS"
                  and _i3_h2.checks.get("C-ID-SPACE") == "PASS" and _i3_h2.checks.get("C-NO-DELETION") == "PASS")
        finally:
            _opf_store.SUPPORTED_HOMES = _i3_prev_homes
            _opf_store.SUPPORTED_SPEC_VERSION = _i3_prev_ssv
        # imported_series_active is bound once in this module, by its module-level def: a parallel change
        # that carries the same definition must leave ONE copy when both merge, and a textual merge can keep
        # two without a conflict. The census counts these binding forms, anywhere in the source: a def, a
        # class, an assignment or other name store (comprehension and walrus targets included), an import
        # binding (what the statement BINDS: an unaliased `import a.b` binds its first dotted component,
        # never the full dotted alias name), an except name, a function parameter of any kind, and a
        # structural-pattern capture (a case name, a star capture, or a mapping rest), so a second binding
        # by any of them is caught as well as a second def (red when the module binds it twice).
        import ast
        import inspect

        def _i3_census(tree, name):
            binds = []
            for n in ast.walk(tree):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    dotted = isinstance(n, ast.Import)
                    binds.extend(a for a in n.names
                                 if (a.asname or (a.name.partition(".")[0] if dotted else a.name)) == name)
                elif ((isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       and n.name == name)
                      or (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                          and n.id == name)
                      or (isinstance(n, ast.ExceptHandler) and n.name == name)
                      or (isinstance(n, ast.arg) and n.arg == name)
                      or (isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name == name)
                      or (isinstance(n, ast.MatchMapping) and n.rest == name)):
                    binds.append(n)
            return binds

        _i3_tree = ast.parse(inspect.getsource(sys.modules[__name__]))
        _i3_binds = _i3_census(_i3_tree, "imported_series_active")
        check("imported-series-active-defined-once",
              len(_i3_binds) == 1 and isinstance(_i3_binds[0], ast.FunctionDef)
              and _i3_binds[0] in _i3_tree.body)
        # The census discriminates each newly counted form on a fixture that binds the name ONLY that way:
        # a function parameter, a case capture, a star capture, and a mapping rest each count exactly one
        # (red when the parameter or pattern-capture arm is dropped and such a binding goes uncounted).
        check("imported-series-active-census-counts-parameters-and-captures",
              [len(_i3_census(ast.parse(_i3_src), "imported_series_active")) for _i3_src in (
                  "def f(imported_series_active):\n    pass\n",
                  "match v:\n    case imported_series_active:\n        pass\n",
                  "match v:\n    case [*imported_series_active]:\n        pass\n",
                  "match v:\n    case {**imported_series_active}:\n        pass\n")] == [1, 1, 1, 1])
        # The census counts what an import statement BINDS: an unaliased dotted `import a.b` binds its first
        # component `a` (its ast.alias carries the full dotted name), an alias binds the asname alone, and a
        # `from` import binds the imported name or its asname, never the module (red when an unaliased
        # dotted import of the name goes uncounted, or when a dotted name is matched whole).
        check("imported-series-active-census-counts-import-bindings",
              [len(_i3_census(ast.parse(_i3_src), "imported_series_active")) for _i3_src in (
                  "import imported_series_active\n",
                  "import imported_series_active.child\n",
                  "import imported_series_active.child as other\n",
                  "import other.imported_series_active\n",
                  "import pkg as imported_series_active\n",
                  "from pkg import imported_series_active\n",
                  "from pkg import other as imported_series_active\n",
                  "from imported_series_active import other\n")] == [1, 1, 0, 0, 1, 1, 1, 0])
        # Inactive prior handling is unchanged at the shipped constants: an imported counter key keeps
        # the malformed-prior verdict, and an imported member is ignored.
        _i3_pk = clean_prior()
        _i3_pk["prior"]["counters_high"]["imported:BI"] = 0
        _i3_pkr = run(clean_machine(), obs=_i3_pk)
        _i3_pm = clean_prior()
        _i3_pm["prior"]["imported"] = "not a table"
        _i3_pmr = run(clean_machine(), obs=_i3_pm)
        check("imported-prior-inert-when-inactive",
              _i3_pkr is not None and _i3_pkr.checks.get("C-HISTORY-COUNTERS") == "CANNOT-EVALUATE"
              and any("malformed" in m for m in _i3_msgs(_i3_pkr, "C-HISTORY-COUNTERS"))
              and _i3_pmr is not None and _i3_pmr.status == VALID)
        # F2 (per-record): worklog/<WL-n>.toml is never a per-record body -> INVALID; a garbage body control
        # is still caught.
        wlr = copy.deepcopy(pr_machine)
        wlr["worklog/WL-9.toml"] = "schema = 1"
        check("worklog-perrecord-body-invalid",
              run(wlr, product=pr_product, obs=pr_obs).status == INVALID)
        gbc = copy.deepcopy(pr_machine)
        gbc["finding/garbage.toml"] = "schema = 1"
        _gbc_run = run(gbc, product=pr_product, obs=pr_obs)
        check("perrecord-garbage-body-fails-closed",
              _gbc_run is not None and _gbc_run.status == CANNOT_EVALUATE
              and any("no registry row" in f for f in _gbc_run.findings))
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

        def handoff_msgs(res):
            return res.by_check.get("C-HANDOFF", []) if res is not None else []

        # C-HANDOFF with the imported series INACTIVE: the section 8.6 firewall is keyed on provenance,
        # never on activation (it names the clean series written by the pre-1.3.0 legacy importer, a
        # store whose imported series is inactive), so an importer-authored clean current handoff beside
        # the maintainer's is exempt history and the store stays VALID (red if the exemption is ever
        # gated on the activation flag).
        f = clean_machine()
        f["handoff.index.toml"] = idx([ho(1), envelope("HO-2", "handoff", "current",
                                                       actor={"kind": "importer", "id": "import-assistant"})])
        f["counters.toml"] = counters(HO=2)
        r2imp = run(f)
        check("r2-importer-current-handoff-exempt-while-inactive",
              r2imp.status == VALID and not handoff_msgs(r2imp))
        # Two clean non-importer current handoffs (a maintainer and an automation author) stay a C-HANDOFF
        # finding; this vector covers exactly the automation kind while the series is inactive (the
        # per-kind exclusion coverage, both kinds and both activation states, is the three-kind vectors
        # below).
        f = clean_machine()
        f["handoff.index.toml"] = idx([ho(1), envelope("HO-2", "handoff", "current",
                                                       actor={"kind": "automation"})])
        f["counters.toml"] = counters(HO=2)
        r2mix = run(f)
        check("r2-two-clean-non-importer-handoffs-invalid",
              r2mix.status == INVALID and len(handoff_msgs(r2mix)) == 1)
        # Three clean current handoffs, one per non-importer kind: a maintainer's, an assistant's and an
        # automation's. This vector covers exactly that all three non-importer kinds count while the
        # series is inactive: the one finding is the full C-HANDOFF text reporting all 3, so excluding the
        # assistant or the automation kind from the count, or changing any part of the text, reads red here.
        _r2_three_msg = ("C-HANDOFF: 3 clean handoffs not authored by a known importer are 'current'; at "
                         "most one current handoff exists among clean records not authored by a known "
                         "importer (spec 8.5/8.6)")

        def _three_kind_idx():
            return idx([ho(1),
                        envelope("HO-2", "handoff", "current", actor={"kind": "assistant"}),
                        envelope("HO-3", "handoff", "current", actor={"kind": "automation"})])
        f = clean_machine()
        f["handoff.index.toml"] = _three_kind_idx()
        f["counters.toml"] = counters(HO=3)
        r2kinds = run(f)
        check("r2-assistant-and-automation-handoffs-counted",
              r2kinds.status == INVALID and handoff_msgs(r2kinds) == [_r2_three_msg])
        # An actor that cannot be read is never taken for an importer: the record counts toward the limit
        # (fail-closed, as validate_record applies the importer exceptions on a KNOWN kind only), so C-HANDOFF
        # still fires beside the C-RECORDS finding naming the missing actor (red on a silent pass).
        f = clean_machine()
        _ho_noactor = ho(2)
        del _ho_noactor["actor"]
        f["handoff.index.toml"] = idx([ho(1), _ho_noactor])
        f["counters.toml"] = counters(HO=2)
        r2na = run(f)
        check("r2-unreadable-actor-handoff-counted",
              r2na.status == INVALID and len(handoff_msgs(r2na)) == 1
              and any("missing required field: actor" in m for m in r2na.findings))
        # An imported-series row never counts, whatever its actor (the series half of the filter, the
        # backstop for a direct caller).
        _hrep = _Report()
        _hrep.ran("C-HANDOFF")
        _check_handoff([_make_rec(ho(1), "handoff", "active"),
                        _make_rec(ho(2), "handoff", "imported")], _hrep)
        check("r2-imported-series-handoff-not-counted", not _hrep.findings)
        # Only the exact kind `importer` is exempt: beside a maintainer's current handoff, a lookalike kind
        # (another case, an extension, a prefix, padding) counts, while the exact kind stays exempt in the
        # same harness (red if the exemption widens to a case-insensitive, prefix or substring match).
        _r2_two_msg = ("C-HANDOFF: 2 clean handoffs not authored by a known importer are 'current'; at "
                       "most one current handoff exists among clean records not authored by a known "
                       "importer (spec 8.5/8.6)")

        def _r2_kind_msgs(kind):
            _row = ho(2)
            _row["actor"] = {"kind": kind, "id": "import-assistant"}
            _krep = _Report()
            _krep.ran("C-HANDOFF")
            _check_handoff([_make_rec(ho(1), "handoff", "active"), _make_rec(_row, "handoff", "active")],
                           _krep)
            return _krep.findings
        check("r2-importer-lookalike-kinds-counted",
              _r2_kind_msgs("importer") == []
              and all(_r2_kind_msgs(k) == [_r2_two_msg]
                      for k in ("Importer", "IMPORTER", "importers", "importer-legacy", "import", " importer")))
        # The same lookalike through the whole store: C-HANDOFF counts it and the record check names the
        # unknown kind, so the store is INVALID on both counts (red if either goes quiet).
        f = clean_machine()
        f["handoff.index.toml"] = idx([ho(1), envelope("HO-2", "handoff", "current",
                                                       actor={"kind": "Importer", "id": "import-assistant"})])
        f["counters.toml"] = counters(HO=2)
        r2look = run(f)
        check("r2-importer-lookalike-kind-store-invalid",
              r2look.status == INVALID and handoff_msgs(r2look) == [_r2_two_msg]
              and any("is not one of" in m and "Importer" in m for m in r2look.findings))
        # C-HANDOFF with the imported series ACTIVE: BOTH tooling gates are patched inside the vector
        # (the per-vector idiom of the activated C-IMPORTED-SCHEMA vectors above) and the store declares
        # the 2.0.0 / homes = 2 pair, so run()'s imported_series_active flag is True end to end.
        _r2_prev_ssv = _opf_store.SUPPORTED_SPEC_VERSION
        _r2_prev_homes = _opf_store.SUPPORTED_HOMES
        try:
            _opf_store.SUPPORTED_HOMES = 2
            _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
            # The same maintainer + importer pair as the inactive vector: the importer-authored record is
            # exempt history here too (spec 8.6), so no finding at all, C-HANDOFF evaluates to an explicit
            # PASS and the only cants are the pending imported checks (red if the exemption is removed or
            # if C-HANDOFF, or anything else, goes unevaluated). The clean HO high-water is raised in
            # place so the imported counter high-waters act_machine() sets survive, and the run takes
            # the empty imported baseline the across-time checks need on an active store.
            _r2_act = act_machine()
            _r2_act["handoff.index.toml"] = idx([ho(1), envelope("HO-2", "handoff", "current",
                                                                 actor={"kind": "importer",
                                                                        "id": "import-assistant"})])
            _r2_act["counters.toml"]["counters"]["HO"] = 2
            _r2_act_run = run(_r2_act, obs=_is2_prior())
            # The three-kind store while ACTIVE: this vector covers exactly that the one exemption stays
            # importer-only on an active store too, so widening it to the assistant or the automation
            # kind drops the reported count below 3 and reads red here. The clean HO high-water is raised
            # in place (replacing counters.toml would drop the imported high-waters act_machine() sets)
            # and the run takes the empty imported baseline, as the other active-store vectors do, so the
            # intended handoffs are the store's only defect.
            _r2k_act = act_machine()
            _r2k_act["handoff.index.toml"] = _three_kind_idx()
            _r2k_act["counters.toml"]["counters"]["HO"] = 3
            _r2k_act_run = run(_r2k_act, obs=_is2_prior())
            # The unreadable actor while ACTIVE: None is never taken for an importer on an active store
            # either, fail-closed (red if the exemption ever widens to None).
            _r2na_act = act_machine()
            _r2na_row = ho(2)
            del _r2na_row["actor"]
            _r2na_act["handoff.index.toml"] = idx([ho(1), _r2na_row])
            _r2na_act["counters.toml"]["counters"]["HO"] = 2
            _r2na_act_run = run(_r2na_act, obs=_is2_prior())
        finally:
            _opf_store.SUPPORTED_SPEC_VERSION = _r2_prev_ssv
            _opf_store.SUPPORTED_HOMES = _r2_prev_homes
        check("r2-importer-current-handoff-exempt-when-active",
              _r2_act_run is not None and not _r2_act_run.findings and not handoff_msgs(_r2_act_run)
              and _r2_act_run.checks.get("C-HANDOFF") == "PASS" and pending_only(_r2_act_run))
        check("r2-assistant-and-automation-handoffs-counted-while-active",
              _r2k_act_run is not None and handoff_msgs(_r2k_act_run) == [_r2_three_msg]
              and _r2k_act_run.findings == [_r2_three_msg] and pending_only(_r2k_act_run))
        check("r2-unreadable-actor-handoff-counted-while-active",
              _r2na_act_run is not None and len(handoff_msgs(_r2na_act_run)) == 1
              and any("missing required field: actor" in m for m in _r2na_act_run.findings)
              and pending_only(_r2na_act_run))
        # A handoff population the collectors could not read in full is never a C-HANDOFF pass: the
        # count over the records that were read cannot show that at most one is current. Each vector
        # below leaves C-HANDOFF CANNOT-EVALUATE with the partial-population message, which points to the
        # collector reports (C-RECORDS, C-PERRECORD-RECONCILE, C-ARCHIVE-ENUM) for the cause (red if the
        # check passes on a partial count): an index that cannot be read (a directory in its place), a
        # malformed index (unparseable, or `record` not an array), an absent index, a row that is not a
        # table, a malformed archived index, and, per-record, a registry row with no body and a body with
        # no registry row.
        def _handoff_unread(res):
            return (res is not None and res.checks.get("C-HANDOFF") == "CANNOT-EVALUATE"
                    and any("handoff records could not all be read" in m for m in handoff_msgs(res)))
        f = clean_machine()
        del f["handoff.index.toml"]
        f["handoff.index.toml/stray.toml"] = idx([ho(1)])
        check("r2-handoff-index-unreadable-cannot-evaluate", _handoff_unread(run(f)))
        f = clean_machine()
        f["handoff.index.toml"] = "this is not valid toml === ["
        _r2_unparsed = run(f)
        f = clean_machine()
        f["handoff.index.toml"] = {"schema": 1, "record": "HO-1"}
        check("r2-handoff-index-malformed-cannot-evaluate",
              _handoff_unread(_r2_unparsed) and _handoff_unread(run(f)))
        f = clean_machine()
        del f["handoff.index.toml"]
        check("r2-handoff-index-missing-cannot-evaluate", _handoff_unread(run(f)))
        f = clean_machine()
        f["handoff.index.toml"] = "schema = 1\nrecord = [\"HO-1\"]\n"
        check("r2-handoff-row-not-table-cannot-evaluate", _handoff_unread(run(f)))
        f = clean_machine()
        f["archive/2026/handoff.index.toml"] = {"schema": 1, "record": "HO-1"}
        check("r2-archived-handoff-index-malformed-cannot-evaluate", _handoff_unread(run(f)))
        f = copy.deepcopy(pr_machine)
        f["handoff.index.toml"] = {"schema": 1, "record": [
            {"id": "HO-1", "state": "current", "path": "handoff/HO-1.toml", "digest": "sha256:" + "0" * 64}]}
        check("r2-perrecord-handoff-row-without-body-cannot-evaluate",
              _handoff_unread(run(f, product=pr_product, obs=pr_obs)))
        f = copy.deepcopy(pr_machine)
        f["handoff/HO-1.toml"] = ho(1)
        check("r2-perrecord-handoff-body-without-row-cannot-evaluate",
              _handoff_unread(run(f, product=pr_product, obs=pr_obs)))
        # The same holds for the sibling current-state joins fed by the same collectors: C-RECEIPTS over
        # backlog_item and done, and C-DECISION-CHAINS over pending_decision, are CANNOT-EVALUATE when
        # their records could not all be read (red if either passes over the partial set), while an
        # unread type that none of the three reads leaves all three a PASS (red if the scoping widens).
        # C-LINKS reads EVERY type as a link source and target, so the same unread type leaves it
        # CANNOT-EVALUATE (red if it passes over any partial collection).
        f = clean_machine()
        f["backlog_item.index.toml"] = {"schema": 1, "record": "BI-1"}
        _r1_part = run(f)
        check("r1-receipts-partial-population-cannot-evaluate",
              _r1_part is not None and _r1_part.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and any("receipt join is not evaluated" in m for m in _r1_part.by_check.get("C-RECEIPTS", [])))
        f = clean_machine()
        f["pending_decision.index.toml"] = {"schema": 1, "record": "PD-1"}
        _r3_part = run(f)
        check("r3-chains-partial-population-cannot-evaluate",
              _r3_part is not None and _r3_part.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
              and any("chains are not evaluated" in m for m in _r3_part.by_check.get("C-DECISION-CHAINS", [])))
        # An archive bucket with no archive.toml is never walked, so none of its records is read: all
        # three joins are CANNOT-EVALUATE (red if an unwalked bucket leaves any of them a pass).
        f = clean_machine()
        del f["archive/2026/archive.toml"]
        f["archive/2026/handoff.index.toml"] = idx([])
        _r2_bucket = run(f)
        check("current-state-joins-cannot-evaluate-when-bucket-unwalked",
              _r2_bucket is not None
              and all(_r2_bucket.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS")))
        f = clean_machine()
        f["finding.index.toml"] = {"schema": 1, "record": "FN-1"}
        _r2_other = run(f)
        check("current-state-joins-pass-when-another-type-unread",
              _r2_other is not None and _r2_other.checks.get("C-RECORDS") == "CANNOT-EVALUATE"
              and all(_r2_other.checks.get(cid) == "PASS"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS")))

        def _links_unread(res):
            return (res is not None and res.checks.get("C-LINKS") == "CANNOT-EVALUATE"
                    and any("link and scope resolution is not evaluated" in m
                            for m in res.by_check.get("C-LINKS", [])))
        check("r4-links-cannot-evaluate-when-type-unread", _links_unread(_r2_other))
        f = clean_machine()
        del f["reference.index.toml"]
        f["reference.index.toml/stray.toml"] = idx([])
        check("r4-links-cannot-evaluate-when-index-unreadable", _links_unread(run(f)))
        f = clean_machine()
        del f["reference.index.toml"]
        check("r4-links-cannot-evaluate-when-index-missing", _links_unread(run(f)))
        # Every remaining collector branch that records a population as unread, one vector each, asserting
        # the check that reads the population (red when that branch alone is reverted). In order: a
        # per-record type directory that cannot be listed, an archive tree that cannot be listed, an
        # archive bucket that cannot be listed, an unreadable archive.toml, a malformed archive.toml, an
        # unreadable archived index, an archived row that is not a table, and an archived row the record
        # validator cannot identify (unreachable from store bytes today, so it is driven through a patched
        # validator, the vanishing-reader idiom).
        f = copy.deepcopy(pr_machine)
        f["handoff"] = "not a directory"
        check("r2-perrecord-type-dir-unlistable-cannot-evaluate",
              _handoff_unread(run(f, product=pr_product, obs=pr_obs)))
        f = clean_machine()
        del f["archive/2026/archive.toml"]
        del f["archive/2026/worklog.toml"]
        f["archive"] = "not a directory"
        _r2_tree = run(f)
        check("current-state-joins-cannot-evaluate-when-archive-tree-unlistable",
              _r2_tree is not None
              and all(_r2_tree.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS", "C-LINKS")))
        _real_list_contained = _list_contained

        def _bucket_failing_list(fd, reldir):
            if reldir.endswith("archive/2026"):
                raise StoreError("synthetic: this bucket cannot be listed")
            return _real_list_contained(fd, reldir)

        globals()["_list_contained"] = _bucket_failing_list
        try:
            _r2_bkl = run(clean_machine())
        finally:
            globals()["_list_contained"] = _real_list_contained
        check("current-state-joins-cannot-evaluate-when-bucket-unlistable",
              _r2_bkl is not None
              and any("could not be listed" in m for m in _r2_bkl.cannot_evaluate)
              and all(_r2_bkl.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS", "C-LINKS")))
        f = clean_machine()
        f["archive/2026/archive.toml"] = "this is not valid toml === ["
        _r2_amu = run(f)
        f = clean_machine()
        f["archive/2026/archive.toml"] = {"schema": 1, "moved": "HO-1",
                                          "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                             "destination": "archive/2026/worklog.toml"}]}
        _r2_amm = run(f)
        check("current-state-joins-cannot-evaluate-when-archive-manifest-unreadable",
              _r2_amu is not None
              and all(_r2_amu.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS")))
        check("current-state-joins-cannot-evaluate-when-archive-manifest-malformed",
              _r2_amm is not None
              and all(_r2_amm.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS")))
        f = clean_machine()
        f["archive/2026/handoff.index.toml"] = "this is not valid toml === ["
        check("r2-archived-handoff-index-unreadable-cannot-evaluate", _handoff_unread(run(f)))
        f = clean_machine()
        f["archive/2026/handoff.index.toml"] = idx(["HO-2"])
        check("r2-archived-handoff-row-not-table-cannot-evaluate", _handoff_unread(run(f)))
        _real_validate_record = validate_record

        def _marked_row_unidentifiable(record, expected_type=None, specs=None,
                                       registered_vendors=frozenset(), registered_kinds=None,
                                       standing_auth=None):
            if isinstance(record, dict) and record.get("title") == "unidentifiable for this vector":
                return _real_validate_record("not a table")
            return _real_validate_record(record, expected_type=expected_type, specs=specs,
                                         registered_vendors=registered_vendors,
                                         registered_kinds=registered_kinds, standing_auth=standing_auth)

        f = clean_machine()
        _r2_arow = envelope("HO-2", "handoff", "superseded", title="unidentifiable for this vector")
        f["archive/2026/archive.toml"]["moved"] = [
            {"id": "HO-2", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}]
        f["archive/2026/handoff.index.toml"] = idx([_r2_arow])
        f["counters.toml"] = counters(HO=2)
        globals()["validate_record"] = _marked_row_unidentifiable
        try:
            _r2_aun = run(f)
        finally:
            globals()["validate_record"] = _real_validate_record
        check("r2-archived-row-unidentifiable-cannot-evaluate", _handoff_unread(_r2_aun))
        # The done half of the C-RECEIPTS guard: a malformed done index alone is CANNOT-EVALUATE for the
        # receipt join (red when the guard narrows to backlog_item).
        f = clean_machine()
        f["done.index.toml"] = {"schema": 1, "record": "DN-1"}
        _r1_done = run(f)
        check("r1-receipts-done-population-unread-cannot-evaluate",
              _r1_done is not None and _r1_done.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and any("receipt join is not evaluated" in m for m in _r1_done.by_check.get("C-RECEIPTS", [])))
        # A count above one is still reported while the population is unread: two readable current
        # handoffs beside a malformed archived handoff index yield BOTH the count finding and the
        # unread cant on C-HANDOFF (red when the guard returns before counting, or when the unread set
        # suppresses the count).
        f = clean_machine()
        f["handoff.index.toml"] = idx([ho(1), ho(2)])
        f["counters.toml"] = counters(HO=2)
        f["archive/2026/handoff.index.toml"] = {"schema": 1, "record": "HO-9"}
        _r2_cnt = run(f)
        check("r2-count-above-one-reported-while-unread",
              _r2_cnt is not None and _r2_cnt.checks.get("C-HANDOFF") == "CANNOT-EVALUATE"
              and _r2_two_msg in handoff_msgs(_r2_cnt)
              and any("could not all be read" in m for m in handoff_msgs(_r2_cnt)))
        # A record archive.toml enumerates as moved but that its declared destination cannot confirm (an
        # absent destination here; the same branch covers a removed row, an unreadable destination and a
        # malformed one) is confirmed seated nowhere: the type's population is incomplete, so its join is
        # CANNOT-EVALUATE beside the destination finding, for each join the collectors feed (red when the
        # reconciliation stops recording the type unread). A destination outside the declaring bucket's
        # own index is the same incompleteness through the sibling branch.
        def _moved_fixture(mid, mtype, dest, **cnt):
            m = clean_machine()
            m["archive/2026/archive.toml"]["moved"] = [{"id": mid, "type": mtype, "destination": dest}]
            m["counters.toml"] = counters(**cnt)
            return run(m)
        _r2_mv = _moved_fixture("HO-2", "handoff", "archive/2026/handoff.index.toml", HO=2)
        check("r2-moved-handoff-missing-from-destination-cannot-evaluate",
              _handoff_unread(_r2_mv)
              and any("absent from its destination" in f_ for f_ in _r2_mv.findings))
        check("r2-moved-handoff-wrong-destination-cannot-evaluate",
              _handoff_unread(_moved_fixture("HO-2", "handoff", "archive/2026/done.index.toml", HO=2)))
        _r1_mv = _moved_fixture("DN-2", "done", "archive/2026/done.index.toml", DN=2)
        check("r1-moved-done-missing-from-destination-cannot-evaluate",
              _r1_mv is not None and _r1_mv.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and any("receipt join is not evaluated" in m for m in _r1_mv.by_check.get("C-RECEIPTS", [])))
        _r3_mv = _moved_fixture("PD-1", "pending_decision", "archive/2026/pending_decision.index.toml", PD=1)
        check("r3-moved-decision-missing-from-destination-cannot-evaluate",
              _r3_mv is not None and _r3_mv.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
              and any("chains are not evaluated" in m for m in _r3_mv.by_check.get("C-DECISION-CHAINS", [])))
        # A seated record whose status cannot be interpreted is never read as 'not current' (or 'not
        # ratified', or 'not decided'): the join of its type is CANNOT-EVALUATE beside the per-record
        # schema finding, for an unknown token, a non-string status, and an absent status alike (red when
        # the joins read only the records whose status parsed).
        _r2_hs = ho(2)
        _r2_hs["status"] = "bogus"
        _r2_hi = ho(2)
        _r2_hi["status"] = 7
        _r2_hm = ho(2)
        del _r2_hm["status"]
        _r2_bad = []
        for _row in (_r2_hs, _r2_hi, _r2_hm):
            f = clean_machine()
            f["handoff.index.toml"] = idx([ho(1), _row])
            f["counters.toml"] = counters(HO=2)
            _r2_bad.append(_handoff_unread(run(f)))
        check("r2-malformed-status-handoff-cannot-evaluate", all(_r2_bad))
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([envelope("BI-1", "backlog_item", "bogus")])
        _r1_bsr = run(f)
        check("r1-malformed-status-backlog-item-cannot-evaluate",
              _r1_bsr is not None and _r1_bsr.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and any("receipt join is not evaluated" in m for m in _r1_bsr.by_check.get("C-RECEIPTS", [])))
        _r3_ps = envelope("PD-1", "pending_decision", "bogus")
        _r3_m = clean_machine()
        _r3_m["pending_decision.index.toml"] = idx([_r3_ps])
        _r3_m["counters.toml"] = counters(PD=1)
        _r3_psr = run(_r3_m)
        check("r3-malformed-status-decision-cannot-evaluate",
              _r3_psr is not None and _r3_psr.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
              and any("chains are not evaluated" in m
                      for m in _r3_psr.by_check.get("C-DECISION-CHAINS", [])))
        # A done whose links value cannot be interpreted could carry the receipt the join no longer sees:
        # CANNOT-EVALUATE, never a false 'ratified backlog_item has no receipt' finding (red when the
        # malformed links value is read as 'no links').
        _r1_ml = envelope("DN-1", "done", "recorded", links="receipt_of BI-1")
        _r1_mlm = clean_machine()
        _r1_mlm["done.index.toml"] = idx([_r1_ml])
        _r1_mlr = run(_r1_mlm)
        check("r1-malformed-links-done-cannot-evaluate",
              _r1_mlr is not None and _r1_mlr.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and not any("has no done receipt" in f_ for f_ in _r1_mlr.findings))

        def _receipts_unread(res):
            return (res is not None and res.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
                    and any("receipt join is not evaluated" in m for m in res.by_check.get("C-RECEIPTS", [])))

        def _chains_unread(res):
            return (res is not None and res.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
                    and any("chains are not evaluated" in m for m in res.by_check.get("C-DECISION-CHAINS", [])))

        def _links_uninterpreted(res, rtype):
            return (_links_unread(res)
                    and any("the links or scopes of {!r} records could not be interpreted".format(rtype) in m
                            for m in res.by_check.get("C-LINKS", [])))
        # Each links entry _make_rec cannot interpret leaves the receipt join, and C-LINKS, which reads
        # every links value as a link source, CANNOT-EVALUATE the same way, one entry shape per branch: an
        # entry that is not a table, a rel that is not a string, an id that is not a string, and a rel
        # outside the closed vocabulary (red when that branch alone is reverted: the done then reads as
        # carrying no receipt, a false finding, and C-LINKS passes over the entry it skipped).
        for _name, _entry in (("entry-not-table", "receipt_of BI-1"),
                              ("rel-not-string", {"rel": 7, "id": "BI-1"}),
                              ("id-not-string", {"rel": "receipt_of", "id": 1}),
                              ("rel-not-in-vocabulary", {"rel": "receipt-of", "id": "BI-1"})):
            f = clean_machine()
            f["done.index.toml"] = idx([envelope("DN-1", "done", "recorded", links=[_entry])])
            _r1_le = run(f)
            check("r1-link-{}-done-cannot-evaluate".format(_name),
                  _receipts_unread(_r1_le) and _links_uninterpreted(_r1_le, "done")
                  and not any("has no done receipt" in f_ for f_ in _r1_le.findings))
        # C-LINKS over a links value that is not an array, and over a block's scopes value that is not an
        # array or holds an id that is not a string: CANNOT-EVALUATE, never a pass that skipped the value
        # (red when C-LINKS ignores _make_rec's unparsed links or scopes, or when either scopes branch
        # alone is reverted).
        f = clean_machine()
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open", links="relates FN-404")])
        f["counters.toml"] = counters(FN=1)
        check("r4-links-cannot-evaluate-when-links-not-array", _links_uninterpreted(run(f), "finding"))
        f = clean_machine()
        f["block.index.toml"] = idx([envelope("BL-1", "block", "active", scopes="FN-99")])
        f["counters.toml"] = counters(BL=1)
        check("r4-links-cannot-evaluate-when-scopes-not-array", _links_uninterpreted(run(f), "block"))
        f = clean_machine()
        f["block.index.toml"] = idx([envelope("BL-1", "block", "active", scopes=["BI-1", 99])])
        f["counters.toml"] = counters(BL=1)
        check("r4-links-cannot-evaluate-when-scope-not-string", _links_uninterpreted(run(f), "block"))
        # The C-LINKS message names every cause that holds: a type the collectors could not read in full
        # beside another whose links value could not be interpreted, compared in full (red when either
        # cause is dropped from the message).
        f = clean_machine()
        f["reference.index.toml"] = {"schema": 1, "record": "RF-1"}
        f["finding.index.toml"] = idx([envelope("FN-1", "finding", "open", links="relates FN-404")])
        f["counters.toml"] = counters(FN=1)
        _r4_two = run(f)
        check("r4-links-message-names-every-cause",
              _r4_two is not None and _r4_two.by_check.get("C-LINKS") == [
                  "C-LINKS: the records of 'reference' could not all be read or confirmed; the links or "
                  "scopes of 'finding' records could not be interpreted (see C-RECORDS, C-PERRECORD-RECONCILE, "
                  "C-ARCHIVE-ENUM and C-IMPORTED-SCHEMA); link and scope resolution is not evaluated over "
                  "a partial set (spec 8.5/8.6)"])
        # The worklog collectors feed the same completeness: every worklog entry is a link source and a
        # link target, so C-LINKS is CANNOT-EVALUATE, never a pass or a false dangling finding, when the
        # active ledger cannot be read, when a listed bucket worklog cannot be read (beside a link to one
        # of its entries, which must not read as dangling), when an entry is left out of the map of the
        # active ledger or of a bucket worklog (a reused id here, whose own dangling link is then never
        # read), when a worklog_moved span
        # enumerates an entry the bucket's worklog does not hold, when a span is not a well-formed range,
        # and when a worklog_moved row is dropped as malformed (its span, then its destination). Each is
        # red when its branch alone stops recording the worklog population as unread.
        def _links_worklog_unread(res):
            return (_links_unread(res)
                    and any("the records of 'worklog' could not all be read" in m
                            for m in res.by_check.get("C-LINKS", [])))
        f = clean_machine()
        f["worklog.toml"] = "garbage = ["
        check("r4-links-cannot-evaluate-when-active-worklog-unreadable", _links_worklog_unread(run(f)))
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(1, "done"), envelope(
            "BI-2", "backlog_item", "open", links=[{"rel": "relates", "id": "WL-1"}])])
        f["archive/2026/worklog.toml"] = "garbage = ["
        # No span names the bucket's entries, so the unreadable ledger alone leaves them unread (a span
        # would also report them absent through its own branch).
        f["archive/2026/archive.toml"]["worklog_moved"] = []
        _r4_awl = run(f)
        check("r4-links-cannot-evaluate-when-archived-worklog-unreadable",
              _links_worklog_unread(_r4_awl) and not any("dangling" in f_ for f_ in _r4_awl.findings))
        f = clean_machine()
        _r4_dup = wl(4)
        _r4_dup["links"] = [{"rel": "relates", "id": "FN-404"}]
        f["worklog.toml"] = {"schema": 1, "entry": [wl(3), wl(4), _r4_dup]}
        _r4_dupr = run(f)
        check("r4-links-cannot-evaluate-when-worklog-entry-left-out",
              _links_worklog_unread(_r4_dupr) and not any("dangling" in f_ for f_ in _r4_dupr.findings))
        f = clean_machine()
        _r4_adup = wl(2)
        _r4_adup["links"] = [{"rel": "relates", "id": "FN-404"}]
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(2), _r4_adup]}
        _r4_adupr = run(f)
        check("r4-links-cannot-evaluate-when-archived-worklog-entry-left-out",
              _links_worklog_unread(_r4_adupr) and not any("dangling" in f_ for f_ in _r4_adupr.findings))
        f = clean_machine()
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [wl(1)]}
        check("r4-links-cannot-evaluate-when-span-entry-absent", _links_worklog_unread(run(f)))
        for _name, _row in (("span-not-a-range", {"span": ["WL-2", "WL-1"],
                                                  "destination": "archive/2026/worklog.toml"}),
                            ("span-not-two-ids", {"span": ["WL-1"], "destination": "archive/2026/worklog.toml"}),
                            ("destination-not-string", {"span": ["WL-1", "WL-2"], "destination": 7})):
            f = clean_machine()
            f["archive/2026/archive.toml"]["worklog_moved"] = [_row]
            check("r4-links-cannot-evaluate-when-worklog-moved-{}".format(_name), _links_worklog_unread(run(f)))
        # Every other check that reads the worklog entries is CANNOT-EVALUATE naming the unread worklog, never
        # a pass or a false finding over the entries read, when a listed bucket worklog is absent at read or
        # cannot be parsed or validated and when the active ledger cannot be read; the store is never VALID.
        # C-ROTATION reads the archived entries alone, so an active ledger that cannot be read leaves it
        # graded. The second bucket holds an edited copy of WL-3, which the active ledger also holds: read in
        # full, it is a partition, frozen-coverage, id-space and changelog-gate finding, so a pass over the
        # unread bucket would hide a real defect. Removing any one check's unread branch turns red every case
        # that asserts that check, and the active-ledger case is red when C-ROTATION reads the active ledger's
        # state too. A cannot-evaluate outranks a finding in a check's state, so the checks that skip their
        # scan over a partial set are also asserted to report no finding, and a third case leaves unread a
        # bucket holding the only copy of WL-2, which a scan of the entries read would report missing: each
        # case is red when a check runs its scan beside its cannot-evaluate.
        _wl_readers = ("C-ID-SPACE", "C-FROZEN-COVERAGE", "C-NO-DELETION", "C-PARTITION", "C-CONTIGUITY",
                       "C-CHANGELOG-GATES")
        _wl_skipped = ("C-FROZEN-COVERAGE", "C-NO-DELETION", "C-CONTIGUITY", "C-CHANGELOG-GATES")

        def _wl_readers_unread(res, archived):
            cids = _wl_readers + (("C-ROTATION",) if archived else ())
            return (res is not None and res.status == CANNOT_EVALUATE
                    and all(res.checks.get(cid) == "CANNOT-EVALUATE"
                            and any(m.startswith(cid + ": the ") and "worklog entries" in m
                                    and "could not all be read or confirmed" in m
                                    and "not evaluated over a partial set" in m
                                    for m in res.by_check.get(cid, []))
                            for cid in cids)
                    and not any(m in res.findings for cid in _wl_skipped for m in res.by_check.get(cid, []))
                    and (archived or res.checks.get("C-ROTATION") == "PASS"))

        def _wl_second_bucket():
            m = clean_machine()
            _wl_copy = wl(3)
            _wl_copy["summary"] = "edited"
            m["archive/2025/archive.toml"] = {"schema": 1, "moved": [], "worklog_moved": []}
            m["archive/2025/worklog.toml"] = {"schema": 1, "entry": [_wl_copy]}
            return m
        _wl_full = run(_wl_second_bucket())
        check("worklog-second-bucket-read-in-full-findings",
              _wl_full is not None and _wl_full.status == INVALID
              and all(_wl_full.checks.get(cid) == "FINDING"
                      for cid in ("C-PARTITION", "C-FROZEN-COVERAGE", "C-ID-SPACE", "C-CHANGELOG-GATES")))
        # Absent at read: the archive reader is patched to report the listed bucket worklog absent, as a
        # removal between the bucket listing and the read would.
        _wl_gone = "archive/2025/worklog.toml"
        _wl_real_reader = _opf_worklog.load_archive_worklog_at

        def _wl_vanishing_reader(fd, relpath, **kwargs):
            if relpath.endswith(_wl_gone):
                return None
            return _wl_real_reader(fd, relpath, **kwargs)

        _opf_worklog.load_archive_worklog_at = _wl_vanishing_reader
        try:
            _wl_absent = run(_wl_second_bucket())
        finally:
            _opf_worklog.load_archive_worklog_at = _wl_real_reader
        check("worklog-readers-cannot-evaluate-when-archived-worklog-absent-at-read",
              _wl_readers_unread(_wl_absent, True))
        for _name, _doc in (("unparseable", "garbage = ["), ("invalid", {"schema": 1, "entry": "x"})):
            f = _wl_second_bucket()
            f[_wl_gone] = _doc
            check("worklog-readers-cannot-evaluate-when-archived-worklog-{}".format(_name),
                  _wl_readers_unread(run(f), True))
        f = clean_machine()
        f["worklog.toml"] = "garbage = ["
        check("worklog-readers-cannot-evaluate-when-active-ledger-unreadable", _wl_readers_unread(run(f), False))

        def _wl_unique_buckets():
            m = clean_machine()
            m["archive/2026/archive.toml"]["worklog_moved"] = [{"span": ["WL-1", "WL-1"],
                                                                "destination": "archive/2026/worklog.toml"}]
            m["archive/2026/worklog.toml"] = {"schema": 1, "entry": [wl(1)]}
            m["archive/2025/archive.toml"] = {"schema": 1, "moved": [], "worklog_moved": [
                {"span": ["WL-2", "WL-2"], "destination": "archive/2025/worklog.toml"}]}
            m["archive/2025/worklog.toml"] = {"schema": 1, "entry": [wl(2)]}
            return m
        _wl_uniq = run(_wl_unique_buckets())
        check("worklog-unique-entry-buckets-read-in-full-valid", _wl_uniq is not None and _wl_uniq.status == VALID)
        f = _wl_unique_buckets()
        f[_wl_gone] = "garbage = ["
        check("worklog-readers-cannot-evaluate-when-archived-unique-entry-unread", _wl_readers_unread(run(f), True))
        # C-HISTORY-RESURRECTION and C-NO-DELETION read a prior or allocated id absent from a type with a
        # record source that could not be read as CANNOT-EVALUATE naming that type, never as vanished (red
        # when either gate is removed): a prior worklog id beside an unread bucket worklog, and the handoff
        # line beside an unread handoff index. A type that was read is still graded beside it: BI-3,
        # allocated and named by the prior but held nowhere, is reported by both checks (red when either
        # gate widens to every type).
        def _unread_type_cant(res, cid, tname):
            return (res is not None and res.checks.get(cid) == "CANNOT-EVALUATE"
                    and any(m.startswith(cid + ": the records of {!r} could not all be read (see"
                                         .format(tname)) and "not evaluated over a partial set" in m
                            for m in res.by_check.get(cid, []))
                    and not any(m in res.findings for m in res.by_check.get(cid, [])))
        _prior_wl = clean_prior()
        _prior_wl["prior"]["records"]["WL-1"] = ("worklog", "added")
        f = clean_machine()
        f["archive/2026/worklog.toml"] = "garbage = ["
        check("history-resurrection-cannot-evaluate-when-worklog-unread",
              _unread_type_cant(run(f, obs=_prior_wl), "C-HISTORY-RESURRECTION", "worklog"))
        f = clean_machine()
        f["handoff.index.toml"] = "garbage = ["
        _ho_unread = run(f)
        check("no-deletion-and-resurrection-cannot-evaluate-when-handoff-unread",
              _unread_type_cant(_ho_unread, "C-NO-DELETION", "handoff")
              and _unread_type_cant(_ho_unread, "C-HISTORY-RESURRECTION", "handoff"))
        f = clean_machine()
        f["handoff.index.toml"] = "garbage = ["
        f["counters.toml"]["counters"]["BI"] = 3
        _prior_bi = clean_prior()
        _prior_bi["prior"]["records"]["BI-3"] = ("backlog_item", "open")
        _ho_read = run(f, obs=_prior_bi)
        check("no-deletion-and-resurrection-graded-for-read-type-beside-unread-handoff",
              _ho_read is not None
              and any(m.startswith("C-NO-DELETION: 1 allocated BI-<n> id(s)") for m in _ho_read.findings)
              and any(m.startswith("C-HISTORY-RESURRECTION: prior record 'BI-3' is absent")
                      for m in _ho_read.findings)
              and not any("HO-" in m for m in _ho_read.findings
                          if m.startswith(("C-NO-DELETION", "C-HISTORY-RESURRECTION"))))
        # A rotation whose destination was read without the row leaves its type unconfirmed for the
        # current-state joins, yet every source was read, so the presence gates still grade the type:
        # BI-1 enumerated as moved to an archived index that parses but lacks it, and BI-3 deleted
        # outright, beside counters BI=3 and a prior naming BI-3, report BI-3 under both checks while
        # C-RECEIPTS stays CANNOT-EVALUATE (red when the presence gates read the types left unconfirmed).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(2, "open")])
        f["archive/2026/archive.toml"]["moved"] = [
            {"id": "BI-1", "type": "backlog_item", "destination": "archive/2026/backlog_item.index.toml"}]
        f["archive/2026/backlog_item.index.toml"] = idx([])
        f["counters.toml"]["counters"]["BI"] = 3
        _mv_read = run(f, obs=_prior_bi)
        check("no-deletion-and-resurrection-graded-beside-destination-read-without-row",
              _mv_read is not None
              and any("enumerated moved id 'BI-1' is absent from its destination" in m
                      for m in _mv_read.findings)
              and _mv_read.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE"
              and any(m.startswith("C-NO-DELETION: 1 allocated BI-<n> id(s) above BI-2")
                      for m in _mv_read.findings)
              and any(m.startswith("C-HISTORY-RESURRECTION: prior record 'BI-3' is absent")
                      for m in _mv_read.findings)
              and not any(m.startswith(cid + ": the records of") for m in _mv_read.cannot_evaluate
                          for cid in ("C-NO-DELETION", "C-HISTORY-RESURRECTION")))
        # A moved row that cannot be reconciled leaves the population of every type it could stand for
        # incomplete, for each check that joins through moved rows (red when its branch alone stops
        # recording that population unconfirmed or unread). A row dropped as malformed still names a
        # record through its id's namespace or its declared type: a missing type (handoff, then
        # pending_decision), a type that is not a string (done), a malformed id and a destination that is
        # not a string. A row with neither a usable id nor a usable type could stand for any record, so
        # every join is CANNOT-EVALUATE. A row whose declared type is not a roster type, or names a type
        # its id's namespace does not bind, leaves the type its id binds incomplete even where the
        # destination holds the id; and a mislabelled row whose destination is absent adds that type
        # beside the declared one.
        def _moved_rows_run(rows, extra=None, **cnt):
            m = clean_machine()
            m["archive/2026/archive.toml"]["moved"] = rows
            m["counters.toml"] = counters(**cnt)
            m.update(extra or {})
            return run(m)
        _mv_ho = _moved_rows_run([{"id": "HO-2", "destination": "archive/2026/handoff.index.toml"}], HO=2)
        check("r2-moved-row-type-missing-cannot-evaluate", _handoff_unread(_mv_ho) and _links_unread(_mv_ho))
        check("r3-moved-row-type-missing-cannot-evaluate", _chains_unread(_moved_rows_run(
            [{"id": "PD-1", "destination": "archive/2026/pending_decision.index.toml"}], PD=1)))
        check("r1-moved-row-type-not-string-cannot-evaluate", _receipts_unread(_moved_rows_run(
            [{"id": "DN-2", "type": 7, "destination": "archive/2026/done.index.toml"}], DN=2)))
        check("r2-moved-row-id-malformed-cannot-evaluate", _handoff_unread(_moved_rows_run(
            [{"id": "HO-x", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}])))
        check("r2-moved-row-destination-not-string-cannot-evaluate", _handoff_unread(_moved_rows_run(
            [{"id": "HO-2", "type": "handoff", "destination": 5}], HO=2)))
        _mv_any = _moved_rows_run([{"id": "x", "type": 5, "destination": 5}])
        check("current-state-joins-cannot-evaluate-when-moved-row-unidentifiable",
              _mv_any is not None
              and all(_mv_any.checks.get(cid) == "CANNOT-EVALUATE"
                      for cid in ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS", "C-LINKS")))
        check("r2-moved-row-type-not-roster-cannot-evaluate", _handoff_unread(_moved_rows_run(
            [{"id": "HO-2", "type": "bogus", "destination": "archive/2026/bogus.index.toml"}],
            extra={"archive/2026/bogus.index.toml": idx([ho(2)])}, HO=2)))
        check("r1-moved-row-mislabelled-destination-holds-id-cannot-evaluate", _receipts_unread(_moved_rows_run(
            [{"id": "DN-2", "type": "reference", "destination": "archive/2026/reference.index.toml"}],
            extra={"archive/2026/reference.index.toml": idx([envelope(
                "DN-2", "reference", "recorded", refs=[{"kind": "doc", "locator": "x", "note": "n"}])])}, DN=2)))
        check("r1-moved-row-mislabelled-destination-absent-cannot-evaluate", _receipts_unread(_moved_rows_run(
            [{"id": "DN-2", "type": "reference", "destination": "archive/2026/reference.index.toml"}], DN=2)))
        # A moved row whose declared type is a roster type that is not enabled, with an id in that type's
        # own namespace, names no enabled type, so a wrong or an unconfirmed destination leaves every
        # enabled type's population incomplete and every join that reads it CANNOT-EVALUATE. Each branch
        # has its own vector (red when that branch alone records only the declared type unread).
        def _all_joins_unread(res):
            return (res is not None
                    and {cid: res.checks.get(cid) for cid in
                         ("C-RECEIPTS", "C-HANDOFF", "C-DECISION-CHAINS", "C-LINKS")}
                    == {"C-RECEIPTS": "CANNOT-EVALUATE", "C-HANDOFF": "CANNOT-EVALUATE",
                        "C-DECISION-CHAINS": "CANNOT-EVALUATE", "C-LINKS": "CANNOT-EVALUATE"})
        for _mtype, _mid in (("maintainer_action", "MA-1"), ("legacy_fragment", "LF-1")):
            _mname = _mtype.replace("_", "-")
            _mv_wrong = _moved_rows_run(
                [{"id": _mid, "type": _mtype, "destination": "archive/2026/handoff.index.toml"}])
            check("moved-row-{}-disabled-wrong-destination-cannot-evaluate".format(_mname),
                  _all_joins_unread(_mv_wrong)
                  and any("is not this bucket's type index" in f_ for f_ in _mv_wrong.findings)
                  and not any("absent from its destination" in f_ for f_ in _mv_wrong.findings))
            _mv_unconf = _moved_rows_run(
                [{"id": _mid, "type": _mtype, "destination": "archive/2026/{}.index.toml".format(_mtype)}])
            check("moved-row-{}-disabled-destination-unconfirmed-cannot-evaluate".format(_mname),
                  _all_joins_unread(_mv_unconf)
                  and any("absent from its destination" in f_ for f_ in _mv_unconf.findings)
                  and not any("is not this bucket's type index" in f_ for f_ in _mv_unconf.findings))
        # The presence gates over every moved-row branch, each vector built once with the record genuinely
        # held (at the declared location where one can hold it; in the active index where the declared
        # location cannot exist or cannot be read) and once genuinely held nowhere, beside counters that
        # allocate it and a prior that names it. The expected states follow _validate_archive's
        # classification principle: the absence FINDING exactly where the record is provably missing (its
        # declared destination in the counted set - this bucket's own index, a sibling's or another
        # bucket's enabled index, or an active one, each read in full by the scan that counts its ids, or
        # confirmed absent by the probe where the listing counted nothing - and a dropped row through its
        # kept declared destination where that is still a usable path, or one with no usable destination
        # wherever the type's counted locations were all read), CANNOT-EVALUATE where
        # its presence cannot be known (the declared location never opened, never counted, or unreadable),
        # and never an absence finding for a record that exists. The counted set is read through the
        # destination's canonical contained form, so a non-canonical spelling of a counted location is
        # still a graded lookup. Each gate is read through its own absence
        # finding and its own partial-set CANNOT-EVALUATE alone, so moving any one branch between the
        # graded and the source-unread sets turns at least one vector red; in particular, classifying a
        # wrong destination by path equality instead of by the counted set turns the four
        # wrong-destination-*-counted vectors red, classifying it during the walk instead of after turns
        # the later-bucket vectors red, discarding a dropped row's usable destination turns the held
        # dropped-row vectors red (the flips below read both rules directly), and reading the counted
        # set through the raw spelling turns the alias vectors' provable deletions into CANNOT-EVALUATE.
        def _gate_state(res, cid, absent_needle, cant_needle):
            msgs = res.by_check.get(cid, [])
            if any(absent_needle in m for m in msgs if m in res.findings):
                return "finding"
            if any(cant_needle in m for m in msgs if m in res.cannot_evaluate):
                return "cant"
            return "pass"

        def _presence_states(res, rid, tname, wl=False):
            if res is None:
                return None
            type_unread = "the records of {!r} could not all be read".format(tname)
            return (_gate_state(res, "C-NO-DELETION", "{} is absent".format(rid),
                                "worklog no-deletion is not evaluated" if wl else type_unread),
                    _gate_state(res, "C-HISTORY-RESURRECTION",
                                "prior record {!r} is absent".format(rid), type_unread))

        def _presence_moved(row, bi1_active=False, extra=None, working=None):
            f = clean_machine()
            f["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open")] if bi1_active
                                               else [bi(2, "open")])
            f["archive/2026/archive.toml"]["moved"] = [row]
            f.update(extra or {})
            return _presence_states(run(f, working=working), "BI-1", "backlog_item")

        _mv_own = "archive/2026/backlog_item.index.toml"
        _mv_held = idx([bi(1, "done")])
        _mv_b25 = {"schema": 1, "moved": [], "worklog_moved": []}
        for _name, _row, _held, _held_exp, _miss, _miss_exp in (
                ("destination-read",
                 {"id": "BI-1", "type": "backlog_item", "destination": _mv_own},
                 {"extra": {_mv_own: _mv_held}}, ("pass", "pass"),
                 {"extra": {_mv_own: idx([])}}, ("finding", "finding")),
                ("destination-absent",
                 {"id": "BI-1", "type": "backlog_item", "destination": _mv_own},
                 {"bi1_active": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("destination-unreadable",
                 {"id": "BI-1", "type": "backlog_item", "destination": _mv_own},
                 {"bi1_active": True, "extra": {_mv_own: "garbage = ["}}, ("cant", "pass"),
                 {"extra": {_mv_own: "garbage = ["}}, ("cant", "cant")),
                ("wrong-destination",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2026/old/backlog_item.index.toml"},
                 {"extra": {"archive/2026/old/backlog_item.index.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("wrong-destination-active-counted",
                 {"id": "BI-1", "type": "backlog_item", "destination": "backlog_item.index.toml"},
                 {"bi1_active": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("wrong-destination-sibling-counted",
                 {"id": "BI-1", "type": "backlog_item", "destination": "archive/2026/reference.index.toml"},
                 {"extra": {"archive/2026/reference.index.toml": idx([envelope(
                     "BI-1", "reference", "done")])}}, ("pass", "pass"),
                 {"extra": {"archive/2026/reference.index.toml": idx([])}}, ("finding", "finding")),
                ("wrong-destination-other-bucket-counted",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2025/backlog_item.index.toml"},
                 {"extra": {"archive/2025/archive.toml": _mv_b25,
                            "archive/2025/backlog_item.index.toml": _mv_held}}, ("pass", "pass"),
                 {"extra": {"archive/2025/archive.toml": _mv_b25,
                            "archive/2025/backlog_item.index.toml": idx([])}}, ("finding", "finding")),
                ("wrong-destination-later-bucket-counted",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2027/backlog_item.index.toml"},
                 {"extra": {"archive/2027/archive.toml": _mv_b25,
                            "archive/2027/backlog_item.index.toml": _mv_held}}, ("pass", "pass"),
                 {"extra": {"archive/2027/archive.toml": _mv_b25,
                            "archive/2027/backlog_item.index.toml": idx([])}}, ("finding", "finding")),
                ("wrong-destination-dot-alias-counted",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2026/./backlog_item.index.toml"},
                 {"extra": {_mv_own: _mv_held}}, ("pass", "pass"),
                 {"extra": {_mv_own: idx([])}}, ("finding", "finding")),
                ("wrong-destination-double-slash-alias-counted",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2026//backlog_item.index.toml"},
                 {"extra": {_mv_own: _mv_held}}, ("pass", "pass"),
                 {"extra": {_mv_own: idx([])}}, ("finding", "finding")),
                ("wrong-destination-contained-dotdot-alias-counted",
                 {"id": "BI-1", "type": "backlog_item",
                  "destination": "archive/2026/old/../backlog_item.index.toml"},
                 {"extra": {_mv_own: _mv_held}}, ("pass", "pass"),
                 {"extra": {_mv_own: idx([])}}, ("finding", "finding")),
                ("uncontained-destination",
                 {"id": "BI-1", "type": "backlog_item", "destination": "../escape.toml"},
                 {"working": {"escape.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("uncontained-traversal-type",
                 {"id": "BI-1", "type": "../../../escape",
                  "destination": "archive/2026/../../../escape.index.toml"},
                 {"bi1_active": True}, ("cant", "pass"),
                 {}, ("cant", "cant")),
                ("non-roster-type",
                 {"id": "BI-1", "type": "bogus", "destination": "archive/2026/bogus.index.toml"},
                 {"extra": {"archive/2026/bogus.index.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("disabled-declared-type",
                 {"id": "BI-1", "type": "maintainer_action",
                  "destination": "archive/2026/maintainer_action.index.toml"},
                 {"extra": {"archive/2026/maintainer_action.index.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("namespace-mismatch-enabled-type",
                 {"id": "BI-1", "type": "reference", "destination": "archive/2026/reference.index.toml"},
                 {"extra": {"archive/2026/reference.index.toml": idx([envelope(
                     "BI-1", "reference", "done")])}}, ("pass", "pass"),
                 {"extra": {"archive/2026/reference.index.toml": idx([])}}, ("finding", "finding")),
                ("malformed-row",
                 {"id": "BI-1", "type": 7, "destination": _mv_own},
                 {"extra": {_mv_own: _mv_held}}, ("pass", "pass"),
                 {"extra": {_mv_own: idx([])}}, ("finding", "finding")),
                ("dropped-row-uncounted-destination",
                 {"id": "BI-1x", "type": "backlog_item",
                  "destination": "archive/2026/old/backlog_item.index.toml"},
                 {"extra": {"archive/2026/old/backlog_item.index.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("dropped-row-uncontained-destination",
                 {"id": "BI-1x", "type": "backlog_item", "destination": "../escape.toml"},
                 {"working": {"escape.toml": _mv_held}}, ("cant", "cant"),
                 {}, ("cant", "cant")),
                ("dropped-row-no-destination",
                 {"id": "BI-1", "type": "backlog_item"},
                 {"bi1_active": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("dropped-row-unidentifiable",
                 {"id": 7, "type": 7, "destination": 7},
                 {"bi1_active": True}, ("pass", "pass"),
                 {}, ("finding", "finding"))):
            check("presence-gates-moved-row-{}-held".format(_name),
                  _presence_moved(_row, **_held) == _held_exp)
            check("presence-gates-moved-row-{}-missing".format(_name),
                  _presence_moved(_row, **_miss) == _miss_exp)
        # The restored containment refusal is read through its own finding: the expected path built from
        # the traversal-carrying declared type compares equal to the declared destination, yet the
        # destination is refused before any path is built from it, so C-ARCHIVE-ENUM is a FINDING (red
        # when the walk builds and probes the uncontained path instead, which turns the finding into the
        # contained reader's CANNOT-EVALUATE).
        f = clean_machine()
        f["archive/2026/archive.toml"]["moved"] = [
            {"id": "BI-1", "type": "../../../escape",
             "destination": "archive/2026/../../../escape.index.toml"}]
        _mv_esc = run(f)
        check("archive-enum-finding-for-uncontained-traversal-destination",
              _mv_esc is not None and _mv_esc.checks.get("C-ARCHIVE-ENUM") == "FINDING"
              and any("is not a contained store-relative path" in m for m in _mv_esc.findings))
        # A wrong destination the walk counted must not spread: a row for BI-1 naming the ACTIVE index
        # leaves an unrelated BI-2, deleted outright, a graded finding under both gates (red when the
        # wrong-destination branch records the row's types unread instead of reading the counted set).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(1, "done")])
        f["archive/2026/archive.toml"]["moved"] = [
            {"id": "BI-1", "type": "backlog_item", "destination": "backlog_item.index.toml"}]
        _mv_coll = run(f)
        check("no-deletion-and-resurrection-graded-beside-wrong-destination-counted",
              _mv_coll is not None
              and any("is not this bucket's type index" in m for m in _mv_coll.findings)
              and any(m.startswith("C-NO-DELETION: 1 allocated BI-<n> id(s) above BI-1")
                      for m in _mv_coll.findings)
              and any(m.startswith("C-HISTORY-RESURRECTION: prior record 'BI-2' is absent")
                      for m in _mv_coll.findings)
              and not any(m.startswith(cid + ": the records of") for m in _mv_coll.cannot_evaluate
                          for cid in ("C-NO-DELETION", "C-HISTORY-RESURRECTION")))
        # One junk row leaves no namespace's presence gate unread: it names no location, so every enabled
        # type and the worklog stay graded (red when a dropped row's types join the source-unread set),
        # while the rotation it names is unreconciled, so the current-state joins stay CANNOT-EVALUATE.
        f = clean_machine()
        f["archive/2026/archive.toml"]["moved"] = [{"id": 7, "type": 7, "destination": 7}]
        _mv_junk = run(f)
        check("presence-gates-graded-for-every-namespace-beside-junk-row",
              _mv_junk is not None
              and not any(m.startswith(("C-NO-DELETION", "C-HISTORY-RESURRECTION"))
                          for m in _mv_junk.cannot_evaluate)
              and _mv_junk.checks.get("C-RECEIPTS") == "CANNOT-EVALUATE")
        # The probe runs only where the bucket's listing counted nothing at the expected path. Unreadable
        # there, its None is a record source not read, never a graded absence, and never WORDED as one:
        # the row's state is a cannot-evaluate saying the destination is unread, not the absence finding
        # (red when the unreadable case takes the absence wording again). A probe HIT there is a
        # record the walk counted nowhere (the tree changed between the listing and the probe), so its
        # types join the source-unread set too: a record the probe saw never takes an absence finding.
        _real_dest_probe = _dest_index_has_id
        _mv_own_row = {"id": "BI-1", "type": "backlog_item", "destination": _mv_own}
        globals()["_dest_index_has_id"] = lambda *a, **k: None
        try:
            _probe_gone_held = _presence_moved(_mv_own_row, bi1_active=True)
            _probe_gone_miss = _presence_moved(_mv_own_row)
            f = clean_machine()
            f["archive/2026/archive.toml"]["moved"] = [dict(_mv_own_row)]
            _probe_gone_res = run(f)
        finally:
            globals()["_dest_index_has_id"] = _real_dest_probe
        check("presence-gates-moved-row-unreadable-at-probe-held", _probe_gone_held == ("cant", "pass"))
        check("presence-gates-moved-row-unreadable-at-probe-missing", _probe_gone_miss == ("cant", "cant"))
        check("moved-row-unreadable-at-probe-worded-unread-not-absent",
              _probe_gone_res is not None
              and any("could not be read at the probe" in m for m in _probe_gone_res.cannot_evaluate)
              and not any("is absent from its destination" in m for m in _probe_gone_res.findings))
        globals()["_dest_index_has_id"] = lambda *a, **k: True
        try:
            _probe_hit_uncounted = _presence_moved(_mv_own_row)
        finally:
            globals()["_dest_index_has_id"] = _real_dest_probe
        check("presence-gates-moved-row-probe-hit-uncounted-cannot-evaluate",
              _probe_hit_uncounted == ("cant", "cant"))

        def _presence_worklog(wl_rows, wl2_in_bucket=False, extra=None):
            f = clean_machine()
            f["archive/2026/archive.toml"]["worklog_moved"] = wl_rows
            f["archive/2026/worklog.toml"] = {"schema": 1,
                                              "entry": [wl(1), wl(2)] if wl2_in_bucket else [wl(1)]}
            f.update(extra or {})
            o = clean_prior()
            o["prior"]["records"]["WL-2"] = ("worklog", "added")
            return _presence_states(run(f, obs=o), "WL-2", "worklog", wl=True)

        # A span (well-formed, or malformed down to a reversed range) stays graded exactly when its
        # declared destination is a counted ledger: this bucket's own, another read bucket's, or the
        # active one, so an entry absent from every counted ledger is a graded absence while one held in
        # any of them never takes a finding (red when the span classification reads the declaring
        # bucket's own path instead of the counted set).
        _wl_own_dest = "archive/2026/worklog.toml"
        _wl_b25 = {"schema": 1, "moved": [], "worklog_moved": []}
        _wl_b25_held = {"archive/2025/archive.toml": _wl_b25,
                        "archive/2025/worklog.toml": {"schema": 1, "entry": [wl(2)]}}
        _wl_b25_empty = {"archive/2025/archive.toml": _wl_b25,
                         "archive/2025/worklog.toml": {"schema": 1, "entry": []}}
        for _name, _rows, _held, _held_exp, _miss, _miss_exp in (
                ("malformed-span",
                 [{"span": ["WL-1", "WL-x"], "destination": _wl_own_dest}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("span-entry-absent",
                 [{"span": ["WL-1", "WL-2"], "destination": _wl_own_dest}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("span-into-other-bucket-ledger",
                 [{"span": ["WL-1", "WL-2"], "destination": "archive/2025/worklog.toml"}],
                 {"extra": _wl_b25_held}, ("pass", "pass"),
                 {"extra": _wl_b25_empty}, ("finding", "finding")),
                ("span-into-later-bucket-ledger",
                 [{"span": ["WL-1", "WL-2"], "destination": "archive/2027/worklog.toml"}],
                 {"extra": {"archive/2027/archive.toml": _wl_b25,
                            "archive/2027/worklog.toml": {"schema": 1, "entry": [wl(2)]}}}, ("pass", "pass"),
                 {"extra": {"archive/2027/archive.toml": _wl_b25,
                            "archive/2027/worklog.toml": {"schema": 1, "entry": []}}},
                 ("finding", "finding")),
                ("reversed-span-other-bucket-ledger",
                 [{"span": ["WL-2", "WL-1"], "destination": "archive/2025/worklog.toml"}],
                 {"extra": _wl_b25_held}, ("pass", "pass"),
                 {"extra": _wl_b25_empty}, ("finding", "finding")),
                ("span-into-active-ledger",
                 [{"span": ["WL-1", "WL-2"], "destination": "worklog.toml"}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("reversed-span-active-ledger",
                 [{"span": ["WL-2", "WL-1"], "destination": "worklog.toml"}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("span-dot-alias-own-ledger",
                 [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/./worklog.toml"}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding")),
                ("dropped-span-counted-destination",
                 [{"span": "WL-1", "destination": _wl_own_dest}],
                 {"wl2_in_bucket": True}, ("pass", "pass"),
                 {}, ("finding", "finding"))):
            check("presence-gates-worklog-{}-held".format(_name),
                  _presence_worklog(_rows, **_held) == _held_exp)
            check("presence-gates-worklog-{}-missing".format(_name),
                  _presence_worklog(_rows, **_miss) == _miss_exp)
        # ... and a span whose declared destination no scan counted (a file under a bucket subdirectory
        # is never opened) names entries that may be held at a file this walk never reads, a row dropped
        # whole (its span not a two-element array) through its kept destination included: held there or
        # held nowhere, the presence gates are CANNOT-EVALUATE, never a false absence finding over the
        # entries that were read.
        _wl_stray_doc = {"schema": 1, "entry": [wl(2)]}
        for _name, _rows in (
                ("wrong-destination-span",
                 [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/old/worklog.toml"}]),
                ("malformed-span-wrong-destination",
                 [{"span": ["WL-1", "WL-x"], "destination": "archive/2026/old/worklog.toml"}]),
                ("dropped-span-uncounted-destination",
                 [{"span": "WL-1", "destination": "archive/2026/old/worklog.toml"}])):
            check("presence-gates-worklog-{}-held".format(_name),
                  _presence_worklog(_rows, False, extra={
                      "archive/2026/old/worklog.toml": _wl_stray_doc}) == ("cant", "cant"))
            check("presence-gates-worklog-{}-missing".format(_name),
                  _presence_worklog(_rows, False) == ("cant", "cant"))
        # The dropped-row destination rule and the canonical counted-set lookup are each read through a
        # flip (restored in finally). With _dropped_dest_key returning None for every row, a dropped
        # row's kept destination is discarded and both held fixtures above read a graded absence under
        # both gates - a false absence for a record genuinely held at its declared location. With
        # _canonical_contained the identity, a non-canonical spelling of a counted location misses the
        # set and a provable deletion reads CANNOT-EVALUATE instead of a graded finding.
        _mv_old = "archive/2026/old/backlog_item.index.toml"
        _real_dropped_key = _dropped_dest_key
        globals()["_dropped_dest_key"] = lambda dest: None
        try:
            _flip_drop_rec = _presence_moved(
                {"id": "BI-1x", "type": "backlog_item", "destination": _mv_old},
                extra={_mv_old: _mv_held})
            _flip_drop_wl = _presence_worklog(
                [{"span": "WL-1", "destination": "archive/2026/old/worklog.toml"}], False,
                extra={"archive/2026/old/worklog.toml": _wl_stray_doc})
        finally:
            globals()["_dropped_dest_key"] = _real_dropped_key
        check("presence-gates-dropped-row-flip-false-absence-when-destination-discarded",
              _flip_drop_rec == ("finding", "finding") and _flip_drop_wl == ("finding", "finding"))
        _real_canon = _canonical_contained
        globals()["_canonical_contained"] = lambda p: p
        try:
            _flip_alias = _presence_moved(
                {"id": "BI-1", "type": "backlog_item",
                 "destination": "archive/2026/./backlog_item.index.toml"},
                extra={_mv_own: idx([])})
        finally:
            globals()["_canonical_contained"] = _real_canon
        check("presence-gates-alias-flip-cannot-evaluate-when-raw-spelling-read",
              _flip_alias == ("cant", "cant"))
        # The section 8.6 firewall on the sibling joins (the maintainer ruling: keyed on provenance, on
        # every store): an importer-authored done confers no receipt on a non-importer ratified
        # backlog_item. The count leaves an item whose every receipt is importer-authored to the firewall
        # arm, so the defect is reported once, as that arm's finding under C-RECEIPTS (red when the count
        # reports it too, or when that arm stops reading importer-authored receipts).
        f = clean_machine()
        _r1_impdn = dn(1, "BI-1")
        _r1_impdn["actor"] = {"kind": "importer", "id": "import-assistant"}
        f["done.index.toml"] = idx([_r1_impdn])
        _r1_imp = run(f)
        check("r1-importer-authored-receipt-confers-no-authority",
              _r1_imp is not None and _r1_imp.status == INVALID
              and any("its only receipt(s) 'DN-1' are importer-authored" in f_
                      for f_ in _r1_imp.by_check.get("C-RECEIPTS", []))
              and not any("has no done receipt" in f_ for f_ in _r1_imp.by_check.get("C-RECEIPTS", [])))
        # ... and an importer-authored ratified backlog_item is owed no receipt: exempt history, the store
        # stays VALID (red when the join starts requiring receipts of importer-authored records again).
        f = clean_machine()
        _r1_impbi = envelope("BI-1", "backlog_item", "done",
                             actor={"kind": "importer", "id": "import-assistant"})
        f["backlog_item.index.toml"] = idx([_r1_impbi, bi(2, "open")])
        f["done.index.toml"] = idx([])
        f["counters.toml"] = counters(DN=0)
        _r1_impo = clean_prior()
        _r1_impo["prior"]["counters_high"] = counters(DN=0)["counters"]
        del _r1_impo["prior"]["records"]["DN-1"]
        del _r1_impo["prior"]["digests"]["DN-1"]
        del _r1_impo["prior"]["held"]["DN"]
        check("r1-importer-authored-backlog-item-owes-no-receipt",
              run(f, obs=_r1_impo).status == VALID)
        # ... yet the exemption is from owing and conferring only: a clean receipt naming an open
        # importer-authored backlog_item is the C-RECEIPTS finding a receipt naming an open clean item is
        # (red when the receipt targets are read among the non-importer items alone).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(1, "done"), dict(bi(2, "open"), actor={"kind": "importer"})])
        f["done.index.toml"] = idx([dn(1, "BI-1"), dn(2, "BI-2")])
        f["counters.toml"] = counters(DN=2)
        _r1_tgt = run(f)
        check("r1-clean-receipt-of-unratified-importer-item-finding",
              _r1_tgt is not None and _r1_tgt.status == INVALID
              and {c for c, v in _r1_tgt.checks.items() if v != "PASS"} == {"C-RECEIPTS"}
              and _r1_tgt.by_check.get("C-RECEIPTS")
              == ["C-RECEIPTS: done 'DN-2' receipt_of -> backlog_item 'BI-2' which is not ratified at 'done' "
                  "(state 'open'; spec 8.5)"])
        # ... and the one-to-one limit on clean receipts covers every clean target: two maintainer dones
        # naming one ratified importer-authored backlog_item are the C-RECEIPTS finding two naming a
        # ratified clean item are (red when the count runs over the non-importer items alone).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([dict(bi(1, "done"), actor={"kind": "importer"}), bi(2, "open")])
        f["done.index.toml"] = idx([dn(1, "BI-1"), dn(2, "BI-1")])
        f["counters.toml"] = counters(DN=2)
        _r1_two = run(f)
        check("r1-two-clean-receipts-of-importer-authored-item-finding",
              _r1_two is not None and _r1_two.status == INVALID
              and {c for c, v in _r1_two.checks.items() if v != "PASS"} == {"C-RECEIPTS"}
              and _r1_two.by_check.get("C-RECEIPTS")
              == ["C-RECEIPTS: backlog_item 'BI-1' has 2 done receipts; the receipt is one-to-one "
                  "(spec 8.5)"])
        # An importer-authored decided pending_decision confers no resolution: a chain whose only
        # non-superseded decided member is importer-authored has zero current effective resolutions
        # (red when the chain derivation reads the importer record as the resolution).
        f = clean_machine()
        _r3_imppd = pd(3, supersedes="PD-2")
        _r3_imppd["actor"] = {"kind": "importer", "id": "import-assistant"}
        f["pending_decision.index.toml"] = idx([pd(1), pd(2, status="withdrawn", supersedes="PD-1"),
                                                _r3_imppd])
        f["counters.toml"] = counters(PD=3)
        _r3_imp = run(f)
        check("r3-importer-authored-resolution-confers-no-authority",
              _r3_imp is not None and _r3_imp.status == INVALID
              and any("has 0 current effective resolutions" in f_
                      for f_ in _r3_imp.by_check.get("C-DECISION-CHAINS", [])))
        # ... and an importer-authored supersedes link removes no current authority: a clean decided PD-1
        # that an importer-authored withdrawn PD-2 supersedes stays its chain's one current resolution,
        # and with no importer decision current the store is VALID (red when the C-DECISION-CHAINS count
        # lets an importer-authored link supersede, as only the firewall arm's derivation does).
        f = clean_machine()
        _r3_implk = pd(2, status="withdrawn", supersedes="PD-1")
        _r3_implk["actor"] = {"kind": "importer", "id": "import-assistant"}
        f["pending_decision.index.toml"] = idx([pd(1), _r3_implk])
        f["counters.toml"] = counters(PD=2)
        _r3_lk = run(f)
        check("r3-importer-link-removes-no-authority",
              _r3_lk is not None and _r3_lk.status == VALID
              and _r3_lk.checks.get("C-DECISION-CHAINS") == "PASS")
        # Only the exact kind `importer` is barred by the receipt firewall and exempt from the chain
        # derivation: a done authored by a lookalike kind (another case, an extension, a prefix, padding)
        # or by an actor whose kind cannot be read still confers its receipt in the count, and such a
        # decided pending_decision still resolves its chain in the derivation, while the exact kind confers
        # neither in the same harness (red when either filter widens to a case-insensitive, prefix,
        # substring or stripped match, or exempts None). The receipt harness runs the count and its
        # firewall arm together, as the driver does on this store. A lookalike is a kind the schema does
        # not admit, so like an unreadable kind its provenance is not known: each makes the receipt join
        # and the receipt firewall CANNOT-EVALUATE naming it, with no finding, never the pass a lookalike
        # read as, while the exact kind raises no CANNOT-EVALUATE (red when _make_rec takes a kind outside
        # ACTOR_KINDS as known provenance).
        _r13_kinds = ("Importer", "IMPORTER", "importers", "importer-legacy", "import", " importer", None)

        def _with_kind(row, kind):
            if kind is None:
                del row["actor"]
            else:
                row["actor"] = {"kind": kind, "id": "import-assistant"}
            return row

        def _r1_kind_run(kind):
            _krecs = [_make_rec(bi(1, "done"), "backlog_item", "active"),
                      _make_rec(_with_kind(dn(1, "BI-1"), kind), "done", "active")]
            _krep = _Report()
            _krep.ran("C-RECEIPTS")
            _check_receipts(_krecs, {r.id: r for r in _krecs}, _krep)
            _check_importer_receipts(_krecs, _krep, "C-RECEIPTS")
            return _krep.findings, _krep.cannot

        def _r3_kind_current(kind):
            _krecs = [_make_rec(pd(1), "pending_decision", "active"),
                      _make_rec(pd(2, status="withdrawn", supersedes="PD-1"), "pending_decision", "active"),
                      _make_rec(_with_kind(pd(3, supersedes="PD-2"), kind), "pending_decision", "active")]
            return [cur for _members, cur in decision_chains(_krecs, {r.id: r for r in _krecs})]
        _r1_kinds = {k: _r1_kind_run(k) for k in ("importer",) + _r13_kinds}
        _r1_kind_cause = ("C-RECEIPTS: the actor kind of 'done' records is absent or unreadable, or not a "
                          "kind the schema admits (see C-RECORDS")
        check("r1-importer-lookalike-kinds-cannot-evaluate-receipt-join-and-firewall",
              len(_r1_kinds["importer"][0]) == 1 and _r1_kinds["importer"][1] == []
              and all(_r1_kinds[k][0] == [] and len(_r1_kinds[k][1]) == 2
                      and all(m.startswith(_r1_kind_cause) for m in _r1_kinds[k][1])
                      and any("the receipt join" in m for m in _r1_kinds[k][1])
                      and any("the receipt firewall" in m for m in _r1_kinds[k][1]) for k in _r13_kinds))
        # _make_rec reads each kind ACTOR_KINDS holds as itself, and a lookalike or absent kind as None with
        # "actor" in `unparsed`, so no reader can take a lookalike for any schema kind (red when the kind
        # test widens to any string, or keeps a lookalike's string).
        _r13_recs = {k: _make_rec(_with_kind(dn(1, "BI-1"), k), "done", "active")
                     for k in ACTOR_KINDS + _r13_kinds}
        check("r13-actor-kind-outside-schema-read-as-unknown",
              all(_r13_recs[k].actor_kind == k and "actor" not in _r13_recs[k].unparsed for k in ACTOR_KINDS)
              and all(_r13_recs[k].actor_kind is None and "actor" in _r13_recs[k].unparsed
                      for k in _r13_kinds))
        check("r3-importer-lookalike-kinds-resolve-chain",
              _r3_kind_current("importer") == [[]]
              and all(_r3_kind_current(k) == [["PD-3"]] for k in _r13_kinds))
        # An imported-series row neither confers nor owes a receipt, and resolves no chain, whatever its
        # actor (the series half of each filter, the backstop for a direct caller, since the doctor never
        # routes an imported row into these joins; red when either filter reads imported rows).
        _r1_srecs = [_make_rec(bi(1, "done"), "backlog_item", "active"),
                     _make_rec(dn(1, "BI-1"), "done", "imported"),
                     _make_rec(bi(2, "done"), "backlog_item", "imported")]
        _r1_srep = _Report()
        _r1_srep.ran("C-RECEIPTS")
        _check_receipts(_r1_srecs, {r.id: r for r in _r1_srecs}, _r1_srep)
        check("r1-imported-series-rows-neither-confer-nor-owe-receipts",
              _r1_srep.findings == ["C-RECEIPTS: ratified backlog_item 'BI-1' (at 'done') has no done receipt "
                                    "(the receipt is one-to-one; spec 8.5)"])
        # ... and an imported-series done authored by an importer, as an item's only apparent receipt,
        # neither confers it nor hands the item to the firewall arm: the item is the count's finding on the
        # join alone and beside the arm (red when the count's hand-off or the arm's receipts read the
        # imported row).
        _r1_no_receipt = ("C-RECEIPTS: ratified backlog_item 'BI-1' (at 'done') has no done receipt (the "
                          "receipt is one-to-one; spec 8.5)")
        _r1_irecs = [_make_rec(bi(1, "done"), "backlog_item", "active"),
                     _make_rec(_with_kind(dn(1, "BI-1"), "importer"), "done", "imported")]
        _r1_irep = _Report()
        _r1_irep.ran("C-RECEIPTS")
        _check_receipts(_r1_irecs, {r.id: r for r in _r1_irecs}, _r1_irep)
        _r1_ijoin = list(_r1_irep.findings)
        _check_importer_receipts(_r1_irecs, _r1_irep, "C-RECEIPTS")
        check("r1-imported-importer-authored-done-confers-no-receipt",
              _r1_ijoin == [_r1_no_receipt] and _r1_irep.findings == [_r1_no_receipt]
              and _r1_irep.cannot == [])
        # The count hands an item to the firewall arm only where that arm grades it, every clean receipt
        # naming it importer-authored, and both read an imported-series row as no receipt: an item whose
        # receipts are an importer-authored done and an imported-series row is the arm's finding alone, so
        # the count and the arm neither pass it between them nor both report it (red when either reads the
        # imported row as a receipt).
        _r1_hrecs = [_make_rec(bi(1, "done"), "backlog_item", "active"),
                     _make_rec(_with_kind(dn(1, "BI-1"), "importer"), "done", "active"),
                     _make_rec(dn(2, "BI-1"), "done", "imported")]
        _r1_hrep = _Report()
        _r1_hrep.ran("C-RECEIPTS")
        _check_receipts(_r1_hrecs, {r.id: r for r in _r1_hrecs}, _r1_hrep)
        _check_importer_receipts(_r1_hrecs, _r1_hrep, "C-RECEIPTS")
        _r1_arm_dn1 = ("C-RECEIPTS: ratified backlog_item 'BI-1' is authored by a non-importer actor but its "
                       "only receipt(s) 'DN-1' are importer-authored; it MUST hold a clean receipt authored by "
                       "a non-importer actor, or a maintainer-authored maintainer_decision correcting it (spec "
                       "8.6)")
        check("r1-receipt-handoff-only-where-the-arm-grades",
              _r1_hrep.findings == [_r1_arm_dn1])
        # The firewall arm reads an imported-series row as no remedy and as owing nothing: a
        # maintainer-authored imported maintainer_decision correcting an item whose only clean receipt is
        # importer-authored leaves the arm's finding standing, where the same decision in the clean series
        # remedies it, and an imported ratified backlog_item whose only receipt is importer-authored is not
        # graded, where the same item in the clean series is the finding (red when the arm's remedy or
        # item loop reads imported rows).
        def _r1_arm_run(*recs):
            _arep = _Report()
            _arep.ran("C-RECEIPTS")
            _check_receipts(list(recs), {r.id: r for r in recs}, _arep)
            _check_importer_receipts(list(recs), _arep, "C-RECEIPTS")
            return _arep.findings, _arep.cannot

        def _r1_remedy_run(location):
            return _r1_arm_run(_make_rec(bi(1, "done"), "backlog_item", "active"),
                               _make_rec(_with_kind(dn(1, "BI-1"), "importer"), "done", "active"),
                               _make_rec(md(1, links=[{"rel": "corrects", "id": "BI-1"}]),
                                         "maintainer_decision", location))

        def _r1_item_run(location):
            return _r1_arm_run(_make_rec(bi(1, "done"), "backlog_item", location),
                               _make_rec(_with_kind(dn(1, "BI-1"), "importer"), "done", "active"))
        check("r1-imported-maintainer-decision-remedies-nothing",
              _r1_remedy_run("imported") == ([_r1_arm_dn1], []) and _r1_remedy_run("active") == ([], []))
        check("r1-imported-backlog-item-owes-no-firewall-receipt",
              _r1_item_run("imported") == ([], []) and _r1_item_run("active") == ([_r1_arm_dn1], []))
        # C-IMPORTED-SEGREGATION's link rule grades the links of clean records alone: an imported-series row
        # a direct caller passes, linking receipt_of to an imported id, is not read as a clean record, while
        # the same link on a clean done is the finding (red when the link rule reads imported rows).
        def _seg_link_run(location):
            _lrecs = [_make_rec(dn(1, "imported:BI-1"), "done", location)]
            _lrep = _Report()
            _lrep.ran("C-IMPORTED-SEGREGATION")
            _check_imported_segregation(_lrecs, {r.id: r for r in _lrecs}, [], _lrep)
            return _lrep.findings, _lrep.cannot
        check("imported-segregation-imported-row-links-not-graded-as-clean",
              _seg_link_run("imported") == ([], [])
              and _seg_link_run("active")
              == (["C-IMPORTED-SEGREGATION: clean record 'DN-1' links 'receipt_of' -> 'imported:BI-1'; a "
                   "clean-to-imported link is limited to relates, derives_from, follows and same-type "
                   "supersedes (spec 8.6)"], []))
        # A clean receipt's target is read among every clean backlog_item: one naming a proposed
        # importer-authored item is the finding, one naming a ratified importer-authored item is not, and an
        # importer-authored record sharing a clean item's id never makes that item read as ratified (red
        # when the targets are read among the non-importer items alone, or when the duplicate's state wins).
        # The importer-authored receipts stay exempt beside it: two of them on an importer-authored item,
        # and one naming an open clean item, raise nothing.
        def _r1_target_findings(*rows):
            _trecs = [_make_rec(r, r["type"], "active") for r in rows]
            _trep = _Report()
            _trep.ran("C-RECEIPTS")
            _check_receipts(_trecs, {r.id: r for r in _trecs}, _trep)
            _check_importer_receipts(_trecs, _trep, "C-RECEIPTS")
            return _trep.findings
        check("r1-receipt-targets-include-importer-authored-items",
              _r1_target_findings(_with_kind(bi(1, "done/proposed"), "importer"), dn(1, "BI-1"))
              == ["C-RECEIPTS: done 'DN-1' receipt_of -> backlog_item 'BI-1' which is not ratified at 'done' "
                  "(state 'done'; spec 8.5)"]
              and _r1_target_findings(_with_kind(bi(1, "done"), "importer"), dn(1, "BI-1")) == []
              and _r1_target_findings(bi(1, "open"), _with_kind(bi(1, "done"), "importer"), dn(1, "BI-1"))
              == ["C-RECEIPTS: done 'DN-1' receipt_of -> backlog_item 'BI-1' which is not ratified at 'done' "
                  "(state 'open'; spec 8.5)"]
              and _r1_target_findings(_with_kind(bi(1, "done"), "importer"),
                                      _with_kind(dn(1, "BI-1"), "importer"),
                                      _with_kind(dn(2, "BI-1"), "importer")) == []
              and _r1_target_findings(bi(1, "open"), _with_kind(dn(1, "BI-1"), "importer")) == [])
        # The one-to-one count reads a done whose actor kind cannot be read as a clean receipt, fail-closed:
        # beside a maintainer done on a ratified importer-authored item it is a second receipt, while an
        # importer-authored done in its place is not counted (red when the count runs over the non-importer
        # items alone, drops a done of unreadable kind, or counts an importer-authored done).
        check("r1-unreadable-kind-receipt-counts-toward-one-to-one",
              _r1_target_findings(_with_kind(bi(1, "done"), "importer"), _with_kind(dn(1, "BI-1"), None),
                                  dn(2, "BI-1"))
              == ["C-RECEIPTS: backlog_item 'BI-1' has 2 done receipts; the receipt is one-to-one "
                  "(spec 8.5)"]
              and _r1_target_findings(_with_kind(bi(1, "done"), "importer"),
                                      _with_kind(dn(1, "BI-1"), "importer"), dn(2, "BI-1")) == [])
        _r3_srecs = [_make_rec(pd(1), "pending_decision", "active"),
                     _make_rec(pd(2, status="withdrawn", supersedes="PD-1"), "pending_decision", "active"),
                     _make_rec(pd(3, supersedes="PD-2"), "pending_decision", "imported")]
        check("r3-imported-series-row-resolves-no-chain",
              [cur for _members, cur in decision_chains(_r3_srecs, {r.id: r for r in _r3_srecs})] == [[]])
        # The section 8.6 firewall is keyed on provenance, so it applies on this inactive store too, where
        # C-IMPORTED-SEGREGATION is off the roster: the receipt arm reports under C-RECEIPTS and the
        # decision-chain arm under C-DECISION-CHAINS (each red when the driver runs them only with the
        # imported series active, which leaves these stores VALID). A store holding an importer-authored
        # clean record discloses the firewall residuals, never the imported-series one.
        def _fw_run(machine, extra_digests=None):
            o = clean_prior()
            o["prior"]["digests"].update(extra_digests or {})
            return run(machine, obs=o)

        def _fw_disclosed(r):
            return (r is not None and all(x in r.residuals for x in _FIREWALL_RESIDUALS)
                    and not any(x in r.residuals for x in _IMPORTED_RESIDUALS))

        _fw_idn = dict(dn(1, "BI-1"), actor={"kind": "importer"})
        _fw_dig = {"DN-1": _record_digest(_fw_idn)}

        def _fw_receipt(*mds):
            m = clean_machine()
            m["done.index.toml"] = idx([copy.deepcopy(_fw_idn)])
            if mds:
                m["maintainer_decision.index.toml"] = idx(list(mds))
                m["counters.toml"] = counters(MD=len(mds))
            return m

        _fw_rc = _fw_run(_fw_receipt(), _fw_dig)
        check("firewall-inactive-importer-receipt-finding",
              _fw_rc is not None and _fw_rc.status == INVALID
              and {c for c, v in _fw_rc.checks.items() if v != "PASS"} == {"C-RECEIPTS"}
              and _fw_rc.by_check.get("C-RECEIPTS")
              == ["C-RECEIPTS: ratified backlog_item 'BI-1' is authored by a non-importer actor but its only "
                  "receipt(s) 'DN-1' are importer-authored; it MUST hold a clean receipt authored by a "
                  "non-importer actor, or a maintainer-authored maintainer_decision correcting it (spec 8.6)"]
              and _fw_disclosed(_fw_rc))
        # The spec 8.6 remedy is accepted on this store too; a maintainer decision that links the item by
        # any relation but `corrects` is no remedy (red when the remedy accepts any relation).
        _fw_rm = _fw_run(_fw_receipt(md(1, links=[{"rel": "corrects", "id": "BI-1"}])), _fw_dig)
        _fw_rl = _fw_run(_fw_receipt(md(1, links=[{"rel": "relates", "id": "BI-1"}])), _fw_dig)
        check("firewall-inactive-corrects-remedy-accepted",
              _fw_rm is not None and _fw_rm.status == VALID and _fw_disclosed(_fw_rm))
        check("firewall-inactive-non-corrects-remedy-confers-nothing",
              _fw_rl is not None and _fw_rl.checks.get("C-RECEIPTS") == "FINDING"
              and any("'BI-1'" in m and "importer-authored" in m for m in _fw_rl.by_check.get("C-RECEIPTS", [])))
        # The decision-chain arm on this store: an importer-authored decision's `supersedes` links remove
        # no current authority, over the chain section 8.8 defines with those links included. A maintainer
        # PD-1 decided that an importer-authored PD-2 supersedes stays the chain's one resolution, and the
        # store certifies (red when an importer-authored link counts toward supersession again: the arm
        # then reads PD-2 as the resolution and reports it).
        def _fw_shape(head=False, archived=False):
            m = clean_machine()
            rows = [pd(1), dict(pd(2, supersedes="PD-1"), actor={"kind": "importer"})]
            if head:
                rows.append(pd(3, supersedes="PD-2"))
            if archived:
                m["archive/2026/archive.toml"]["moved"] = [
                    {"id": "PD-2", "type": "pending_decision",
                     "destination": "archive/2026/pending_decision.index.toml"}]
                m["archive/2026/pending_decision.index.toml"] = idx([rows.pop(1)])
            m["pending_decision.index.toml"] = idx(rows)
            m["counters.toml"] = counters(PD=3 if head else 2)
            return m

        _fw_ch = run(_fw_shape())
        check("firewall-inactive-importer-link-keeps-clean-resolution",
              _fw_ch is not None and _fw_ch.status == VALID
              and _fw_ch.checks.get("C-DECISION-CHAINS") == "PASS" and _fw_disclosed(_fw_ch))
        # ... so a maintainer PD-3 that supersedes PD-2 is a second current resolution of the one chain
        # PD-2 joins, a C-DECISION-CHAINS finding (spec 8.5, 8.8), never two one-member chains that each
        # pass (red when the derivation drops importer-authored decisions again). The same holds with PD-2
        # rotated to the archive, since the chain reads active and archived decisions together (spec 12).
        _fw_two = ["C-DECISION-CHAINS: a pending_decision supersession chain with a decided resolution has 2 "
                   "current effective resolutions; exactly one exists per chain (spec 8.5)"]
        _fw_jh = run(_fw_shape(head=True))
        check("firewall-inactive-importer-joined-two-heads-finding",
              _fw_jh is not None and _fw_jh.status == INVALID
              and {c for c, v in _fw_jh.checks.items() if v != "PASS"} == {"C-DECISION-CHAINS"}
              and _fw_jh.by_check.get("C-DECISION-CHAINS") == _fw_two and _fw_disclosed(_fw_jh))
        _fw_ajh = run(_fw_shape(head=True, archived=True))
        check("firewall-inactive-archived-importer-joined-two-heads-finding",
              _fw_ajh is not None and _fw_ajh.status == INVALID
              and {c for c, v in _fw_ajh.checks.items() if v != "PASS"} == {"C-DECISION-CHAINS"}
              and _fw_ajh.by_check.get("C-DECISION-CHAINS") == _fw_two and _fw_disclosed(_fw_ajh))
        # A chain no non-importer decision resolves (an importer-authored PD-1 decided alone) is the arm's
        # finding: resolving it now takes a new clean decision linking back (spec 8.6; red when the arm
        # skips every chain, or is dropped).
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([dict(pd(1), actor={"kind": "importer"})])
        f["counters.toml"] = counters(PD=1)
        _fw_lone = run(f)
        check("firewall-inactive-importer-only-chain-finding",
              _fw_lone is not None and _fw_lone.status == INVALID
              and {c for c, v in _fw_lone.checks.items() if v != "PASS"} == {"C-DECISION-CHAINS"}
              and _fw_lone.by_check.get("C-DECISION-CHAINS")
              == ["C-DECISION-CHAINS: importer-authored pending_decision 'PD-1' is the current effective "
                  "resolution of a clean pending_decision chain; resolving it now takes a new clean decision "
                  "linking back (spec 8.6)"]
              and _fw_disclosed(_fw_lone))
        # The arms are fail-closed on this store too, over the same unread set and _make_rec's unparsed
        # fields as on an active one. A receipt with no actor beside the importer-authored one leaves the
        # receipt join and the receipt firewall CANNOT-EVALUATE under C-RECEIPTS naming it, where both
        # passed; a remedy whose actor has no kind leaves the firewall CANNOT-EVALUATE, where it read as no
        # remedy and reported; a chain resolution whose kind is a number leaves the chain join and the
        # decision-chain firewall CANNOT-EVALUATE under C-DECISION-CHAINS, where both passed. A remedy, or
        # a chain member, enumerated as rotated to a destination that does not hold it leaves its arm
        # CANNOT-EVALUATE, never the finding it would report over the records it read. None raises a
        # finding of its check (red when a join or the firewall gate drops the actor cause, or the driver
        # passes the inactive arms no unread set).
        def _fw_cant(r, cid, cause, rule):
            return (r is not None and r.checks.get(cid) == "CANNOT-EVALUATE"
                    and not any(m.startswith(cid + ":") for m in r.findings)
                    and any(cause in m and rule in m for m in r.by_check.get(cid, [])))

        _fw_dn2 = dn(2, "BI-1")
        del _fw_dn2["actor"]
        _fw_nk = _fw_receipt()
        _fw_nk["done.index.toml"] = idx([copy.deepcopy(_fw_idn), _fw_dn2])
        _fw_nk["counters.toml"] = counters(DN=2)
        _fw_nkr = _fw_run(_fw_nk, _fw_dig)
        _fw_dn_kind = "the actor kind of 'done' records is absent or unreadable"
        check("firewall-inactive-unreadable-receipt-kind-cannot-evaluate",
              _fw_cant(_fw_nkr, "C-RECEIPTS", _fw_dn_kind, "the receipt join")
              and _fw_cant(_fw_nkr, "C-RECEIPTS", _fw_dn_kind, "the receipt firewall"))
        _fw_mdk = _fw_run(_fw_receipt(md(1, actor={"id": "x"}, links=[{"rel": "corrects", "id": "BI-1"}])),
                          _fw_dig)
        check("firewall-inactive-unreadable-remedy-kind-cannot-evaluate",
              _fw_cant(_fw_mdk, "C-RECEIPTS", "the actor kind of 'maintainer_decision' records is absent or "
                       "unreadable", "the receipt firewall"))
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([pd(1), dict(pd(2, supersedes="PD-1"), actor={"kind": 7})])
        f["counters.toml"] = counters(PD=2)
        _fw_ck = run(f)
        _fw_pd_kind = "the actor kind of 'pending_decision' records is absent or unreadable"
        check("firewall-inactive-unreadable-chain-kind-cannot-evaluate",
              _fw_cant(_fw_ck, "C-DECISION-CHAINS", _fw_pd_kind, "the chains are not evaluated as a pass")
              and _fw_cant(_fw_ck, "C-DECISION-CHAINS", _fw_pd_kind, "the decision-chain firewall"))
        _fw_mv = _fw_receipt()
        _fw_mv["archive/2026/archive.toml"]["moved"] = [
            {"id": "MD-1", "type": "maintainer_decision",
             "destination": "archive/2026/maintainer_decision.index.toml"}]
        _fw_mv["archive/2026/maintainer_decision.index.toml"] = idx([])
        _fw_mv["counters.toml"] = counters(MD=1)
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([dict(pd(1), actor={"kind": "importer"})])
        f["archive/2026/archive.toml"]["moved"] = [
            {"id": "PD-2", "type": "pending_decision",
             "destination": "archive/2026/pending_decision.index.toml"}]
        f["archive/2026/pending_decision.index.toml"] = idx([])
        f["counters.toml"] = counters(PD=2)
        check("firewall-inactive-unconfirmed-moved-arm-records-cannot-evaluate",
              _fw_cant(_fw_run(_fw_mv, _fw_dig), "C-RECEIPTS",
                       "the records of 'maintainer_decision' could not all be read", "the receipt firewall")
              and _fw_cant(run(f), "C-DECISION-CHAINS", "the records of 'pending_decision' could not all be read",
                           "the decision-chain firewall"))
        # The chain join still runs over a member of unreadable kind, reading it as not importer-authored:
        # PD-2 (its kind a number) and PD-3 both supersede PD-1, so the chain it reads has two current
        # resolutions, reported beside the CANNOT-EVALUATE that names the kind (red when the actor cause
        # stops the join, or the join drops it).
        f = clean_machine()
        f["pending_decision.index.toml"] = idx([pd(1), dict(pd(2, supersedes="PD-1"), actor={"kind": 7}),
                                                pd(3, supersedes="PD-1")])
        f["counters.toml"] = counters(PD=3)
        _fw_c2 = run(f)
        check("firewall-inactive-unreadable-kind-chain-still-counted",
              _fw_c2 is not None and _fw_c2.checks.get("C-DECISION-CHAINS") == "CANNOT-EVALUATE"
              and [m for m in _fw_c2.findings if m.startswith("C-DECISION-CHAINS:")]
              == ["C-DECISION-CHAINS: a pending_decision supersession chain with a decided resolution has 2 "
                  "current effective resolutions; exactly one exists per chain (spec 8.5)"]
              and any(_fw_pd_kind in m and "the chains are not evaluated as a pass" in m
                      for m in _fw_c2.by_check.get("C-DECISION-CHAINS", [])))
        # A lookalike kind the schema does not admit is unknown provenance on this store too, and never the
        # importer kind: a second receipt of such a kind beside the importer-authored one leaves the receipt
        # join and the receipt firewall CANNOT-EVALUATE under C-RECEIPTS naming it, and a chain member of
        # such a kind, the current resolution or the decision it supersedes, leaves the chain join and the
        # decision-chain firewall CANNOT-EVALUATE under C-DECISION-CHAINS naming it; each read the record
        # as non-importer and passed (red when _make_rec takes a kind outside ACTOR_KINDS as known
        # provenance).
        _fw_lk_kinds = ("Importer", "importer-legacy", " importer")

        def _fw_lk_receipt(kind):
            m = _fw_receipt()
            m["done.index.toml"] = idx([copy.deepcopy(_fw_idn), dict(dn(2, "BI-1"), actor={"kind": kind})])
            m["counters.toml"] = counters(DN=2)
            return _fw_run(m, _fw_dig)

        def _fw_lk_chain(cur=None, old=None):
            m = clean_machine()
            p1, p2 = pd(1), pd(2, supersedes="PD-1")
            if old is not None:
                p1["actor"] = {"kind": old}
            if cur is not None:
                p2["actor"] = {"kind": cur}
            m["pending_decision.index.toml"] = idx([p1, p2])
            m["counters.toml"] = counters(PD=2)
            return run(m)

        _fw_lk_dn = ("the actor kind of 'done' records is absent or unreadable, or not a kind the schema "
                     "admits")
        check("firewall-inactive-lookalike-receipt-kind-cannot-evaluate",
              all(_fw_cant(r, "C-RECEIPTS", _fw_lk_dn, "the receipt join")
                  and _fw_cant(r, "C-RECEIPTS", _fw_lk_dn, "the receipt firewall")
                  for r in [_fw_lk_receipt(k) for k in _fw_lk_kinds]))
        _fw_lk_pd = ("the actor kind of 'pending_decision' records is absent or unreadable, or not a kind "
                     "the schema admits")
        check("firewall-inactive-lookalike-chain-kind-cannot-evaluate",
              all(_fw_cant(r, "C-DECISION-CHAINS", _fw_lk_pd, "the chains are not evaluated as a pass")
                  and _fw_cant(r, "C-DECISION-CHAINS", _fw_lk_pd, "the decision-chain firewall")
                  for r in [_fw_lk_chain(cur=k) for k in _fw_lk_kinds] + [_fw_lk_chain(old="Importer")]))
        # Legacy importer-authored clean history the firewall leaves alone: an importer-authored item ratified
        # at done with its importer-authored receipt, and an importer-authored decision a maintainer decision
        # supersedes, certify VALID (red when either arm grades importer-authored history it must not).
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([dict(bi(1, "done"), actor={"kind": "importer"}), bi(2, "open")])
        f["done.index.toml"] = idx([copy.deepcopy(_fw_idn)])
        f["pending_decision.index.toml"] = idx([dict(pd(1), actor={"kind": "importer"}), pd(2, supersedes="PD-1")])
        f["counters.toml"] = counters(PD=2)
        _fw_hist = _fw_run(f, _fw_dig)
        check("firewall-inactive-legacy-history-valid",
              _fw_hist is not None and _fw_hist.status == VALID and _fw_disclosed(_fw_hist))
        # The firewall fails closed on this inactive store as on an active one: a clean-record reader that
        # did not evaluate leaves its records unseated, so each arm whose own record types are unread is
        # CANNOT-EVALUATE naming that reader in one message, never a pass over them (red when the driver
        # hands an arm no unseated list, or the reader leaves that list), and is not run, so it grades none
        # of the records it did seat (red when either arm runs past its cant). An unread pending_decision
        # index or registry leaves only the decision-chain arm's type unread, so only that arm is checked
        # there. The first store is the importer-only chain store above with its index unreadable; the last
        # joins the importer-only receipt and importer-only chain stores above beside an unreadable
        # archive, so each arm has a seated finding it does not raise.
        def _fw_unseated(r, reader, arms=("C-RECEIPTS", "C-DECISION-CHAINS")):
            def cants(cid):
                return [m for m in r.by_check.get(cid, []) if "did not evaluate" in m]
            return (r is not None and r.status == CANNOT_EVALUATE and r.checks.get(reader) == "CANNOT-EVALUATE"
                    and all(r.checks.get(cid) == "CANNOT-EVALUATE" and len(cants(cid)) == 1
                            and cants(cid)[0].startswith("{}: {} did not evaluate".format(cid, reader))
                            for cid in arms))

        _fw_chain = idx([dict(pd(1), actor={"kind": "importer"})])
        f = clean_machine()
        f["pending_decision.index.toml"] = _opf_emit.emit(_fw_chain) + "\n[[[ broken\n"
        f["counters.toml"] = counters(PD=1)
        check("firewall-inactive-unread-records-cannot-evaluate",
              _fw_unseated(run(f), "C-RECORDS", ("C-DECISION-CHAINS",)))
        f = clean_machine()
        f["archive/2026/archive.toml"] = "moved = [ not toml"
        check("firewall-inactive-unread-archive-cannot-evaluate", _fw_unseated(run(f), "C-ARCHIVE-ENUM"))
        f = copy.deepcopy(pr_machine)
        f["pending_decision.index.toml"] = "schema = 1\nrecord = [ 7 ]\n"
        check("firewall-inactive-unread-perrecord-cannot-evaluate",
              _fw_unseated(run(f, product=pr_product, obs=pr_obs), "C-PERRECORD-RECONCILE",
                           ("C-DECISION-CHAINS",)))
        f = _fw_receipt()
        f["pending_decision.index.toml"] = copy.deepcopy(_fw_chain)
        f["counters.toml"] = counters(PD=1)
        f["archive/2026/archive.toml"] = "moved = [ not toml"
        _fw_sd = _fw_run(f, _fw_dig)
        check("firewall-inactive-unseated-still-grades-seated",
              _fw_unseated(_fw_sd, "C-ARCHIVE-ENUM")
              and not any("'BI-1' is authored by a non-importer actor" in m
                          for m in _fw_sd.by_check.get("C-RECEIPTS", []))
              and not any("'PD-1' is the current effective resolution" in m
                          for m in _fw_sd.by_check.get("C-DECISION-CHAINS", [])))
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
        # A store that reads no imported row leaves a clean record's permitted link to an imported id
        # dangling: a C-LINKS finding, never a silent pass.
        f = clean_machine()
        f["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open"), envelope(
            "BI-3", "backlog_item", "open", links=[{"rel": "relates", "id": "imported:BI-1"}])])
        f["counters.toml"] = counters(BI=3)
        r = run(f)
        check("r4-dangling-cross-series-invalid",
              r is not None and r.status == INVALID and r.checks.get("C-LINKS") == "FINDING"
              and r.findings == ["C-LINKS: BI-3 relates link -> 'imported:BI-1' resolves to no record in "
                                 "either series (a dangling link over the union of both series; spec 8.6)"])
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
        _rmi_run = run(f)
        check("roster-missing-index-fails-closed",
              _rmi_run is not None and _rmi_run.status == CANNOT_EVALUATE
              and any("has no finding.index.toml" in f_ for f_ in _rmi_run.findings))

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
        _f5a_run = run(f)
        check("enumerated-id-absent-from-dest-fails-closed",
              _f5a_run is not None and _f5a_run.status == CANNOT_EVALUATE
              and any("absent from its destination" in f_ for f_ in _f5a_run.findings))
        f = clean_machine()
        # F12: a moved row whose declared type disagrees with its id namespace
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/handoff.index.toml"] = idx([])
        f["counters.toml"] = counters(RF=9)
        _f12_run = run(f)
        check("moved-type-mismatch-fails-closed",
              _f12_run is not None and _f12_run.status == CANNOT_EVALUATE
              and any("but the id namespace is" in f_ for f_ in _f12_run.findings))
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
        # behind a bounded child: a range-expansion REGRESSION would otherwise hang or OOM the
        # suite in-process; here it trips a deterministic TIMEOUT/OOM sentinel the status assertions
        # catch. Requires os.fork; a fork-less host fails the setup check closed. ------------------------
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
            # The span enumerates entries the bucket's worklog does not hold, so the worklog population
            # is not confirmed complete and C-LINKS fails closed: the bounded run reports CANNOT-EVALUATE
            # (the sentinel still fails this assertion on a range-expansion regression).
            check("huge-span-bounded-fails-closed", _sp_status == CANNOT_EVALUATE)

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
        _m2a_run = run(m2a)
        check("moved-dest-outside-bucket-fails-closed",
              _m2a_run is not None and _m2a_run.status == CANNOT_EVALUATE
              and any("is not this bucket's type index" in f_ for f_ in _m2a_run.findings))
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

        # --- the optional imported release-row flag (spec 6.1, 14.3): C-VERSION-LEDGER and the prior-row
        # check admit it as exactly true on an empty span, and the composed ledger readers (the changelog
        # gates and the view planner) apply the same rule; the whole-store vector below grades a store
        # with an imported row VALID end to end.
        imp_empty = compute_span_digest(full_wl, None)

        def imp_row(version, imported=True):
            r = {"version": version, "date": "2025-01-01T00:00:00Z", "worklog_span": [],
                 "coverage_digest": imp_empty}
            if imported is not None:
                r["imported"] = imported
            return r

        def imp_run(rows, prior_rows):
            m = clean_machine()
            m["version.toml"]["release"] = rows + m["version.toml"]["release"]
            o = copy.deepcopy(clean_prior())
            o["prior"]["releases"] = prior_rows + o["prior"]["releases"]
            return run(m, obs=o)

        def verdicts(r, *cids):
            return None if r is None else tuple(r.checks.get(c) for c in cids)

        hist = ("C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-HISTORY-RESURRECTION")
        _imp = imp_run([imp_row("0.9.0")], [imp_row("0.9.0")])
        check("rel-imported-doctor-accepted", verdicts(_imp, "C-VERSION-LEDGER") == ("PASS",))
        check("prior-imported-row-wellformed", verdicts(_imp, *hist) == ("PASS",) * 3)
        # A flag outside the value rule is a C-VERSION-LEDGER finding naming the rule, and the same row in
        # the prior is a malformed observation, so the history checks cannot evaluate.
        _impv = imp_run([imp_row("0.9.0", 1)], [imp_row("0.9.0", 1)])
        check("rel-imported-doctor-int1-refused",
              verdicts(_impv, "C-VERSION-LEDGER") == ("FINDING",)
              and any("exactly true" in f for f in _impv.findings)
              and verdicts(_impv, *hist) == (CANNOT_EVALUATE,) * 3
              and any("malformed" in m for m in _impv.cannot_evaluate))
        # The prior-row check applies the value and span rules of validate_version at their boundaries.
        check("prior-row-imported-admitted", _wellformed_release_row(imp_row("0.9.0")))
        check("prior-row-imported-false-refused", not _wellformed_release_row(imp_row("0.9.0", False)))
        check("prior-row-imported-int1-refused", not _wellformed_release_row(imp_row("0.9.0", 1)))
        check("prior-row-imported-nonempty-span-refused", not _wellformed_release_row(
            dict(imp_row("0.9.0"), worklog_span=["WL-1", "WL-1"],
                 coverage_digest=compute_span_digest(full_wl, (1, 1)))))
        check("prior-row-plain-still-wellformed", _wellformed_release_row(imp_row("0.9.0", None)))
        # The flag MUST NOT be added to or removed from an existing row (spec 6.1): with both ledgers
        # admitted, C-HISTORY-APPEND-ONLY reads either change as a rewrite.
        for _imp_label, _imp_cur, _imp_prior in (("added-to", True, None), ("removed-from", None, True)):
            _impr = imp_run([imp_row("0.9.0", _imp_cur)], [imp_row("0.9.0", _imp_prior)])
            check("history-flag-{}-existing-row-finding".format(_imp_label),
                  verdicts(_impr, "C-VERSION-LEDGER", "C-HISTORY-APPEND-ONLY") == ("PASS", "FINDING")
                  and any("C-HISTORY-APPEND-ONLY" in f and "rewritten" in f for f in _impr.findings))
        # Imported rows form a prefix of the ledger (spec 6.1): an imported row appended after the witnessed
        # rows is a C-VERSION-LEDGER finding naming the prefix rule. With the prefix rule removed from
        # validate_version the same store passes C-VERSION-LEDGER, so the vector is red without it.
        import _opf_release
        from unittest.mock import patch as _imp_patch

        def imp_late_run():
            m = clean_machine()
            m["version.toml"]["release"] = m["version.toml"]["release"] + [imp_row("9.0.0")]
            return run(m, obs=copy.deepcopy(clean_prior()))

        _impl = imp_late_run()
        check("rel-imported-doctor-after-witnessed-finding",
              verdicts(_impl, "C-VERSION-LEDGER") == ("FINDING",)
              and any("prefix of the ledger" in f for f in _impl.findings))
        with _imp_patch.object(_opf_release, "_imported_prefix_findings", lambda releases: []):
            _impl_off = imp_late_run()
        check("rel-imported-doctor-after-witnessed-red-without-rule",
              verdicts(_impl_off, "C-VERSION-LEDGER") == ("PASS",)
              and not any("prefix of the ledger" in f for f in _impl_off.findings))
        # WHOLE STORE, end to end: a store with a declared VERSION view whose ledger and prior carry an
        # imported row, with the changelog tiling all three releases, grades VALID with no finding and no
        # cant. Every composed ledger reader is on this path: C-VERSION-LEDGER, the changelog gates, the
        # view planner behind C-VIEW-DRIFT, and the history checks, so the vector is red when ANY of them
        # refuses the flag.
        _e2e_cl = make_changelog(["1.1.0", "1.0.0", "0.9.0"])
        _e2e_dig = freeze_digests(_e2e_cl, ["1.1.0", "1.0.0", "0.9.0"])
        _e2e = clean_machine()
        _e2e["manifest.toml"] = base_manifest(views={"VERSION": {
            "kind": "deterministic", "sources": ["version"], "target": "VERSION"}})
        _e2e["version.toml"]["release"] = [imp_row("0.9.0")] + _e2e["version.toml"]["release"]
        _e2e["version.toml"]["summary"] = [
            {"covers": "0.9.0", "status": "published", "digest": _e2e_dig["0.9.0"]},
            {"covers": "1.0.0", "status": "published", "digest": _e2e_dig["1.0.0"]},
            {"covers": "1.1.0", "status": "published", "digest": _e2e_dig["1.1.0"]},
            {"covers": "unreleased", "status": "working"}]
        _e2e_obs = copy.deepcopy(clean_prior())
        _e2e_obs["prior"]["releases"] = [imp_row("0.9.0")] + _e2e_obs["prior"]["releases"]
        _e2e_run = run(_e2e, product={"VERSION": "1.1.0\n", "CHANGELOG.md": _e2e_cl}, obs=_e2e_obs)
        check("rel-imported-doctor-whole-store-valid",
              _e2e_run is not None and _e2e_run.status == VALID
              and not _e2e_run.findings and not _e2e_run.cannot_evaluate
              and _e2e_run.checks.get("C-CHANGELOG-GATES") == "PASS"
              and _e2e_run.checks.get("C-VIEW-DRIFT") == "PASS"
              and _e2e_run.checks.get("C-VERSION-LEDGER") == "PASS")

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
        # unbounded BLOCK on the reader. The validate_store call runs behind a bounded child: a
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

        # --- Listing leg: an OSError from os.listdir inside _list_contained maps to a fail-closed StoreError
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

        # --- Close leg: the _list_contained `finally` closes are guarded, so a teardown close that
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

        # --- Teardown closes: validate_store's `finally` block closes its store / product-root descriptors OUTSIDE
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
        # The section 8.6 tripwire behind the module-tier residual: every module type the firewall names
        # (current-state joins and field-borne authority) is schema-deferred, so no store holding one of
        # its records certifies VALID before its schema ships (red when the deferral stops covering one).
        # The same clean importer-authored waiver at `active` on this inactive store never certifies either.
        check("imported-firewall-module-types-schema-deferred",
              all(_schema_deferred(t) for t in ("waiver", "session_lease", "mode", "gate_run",
                                                "tier_assessment", "artifact", "release", "maintainer_action")))
        mw = copy.deepcopy(mm)
        mw["artifact.index.toml"] = idx([])
        mw["counters.toml"] = counters(AR=0, GR=0, RL=0, WV=1)
        mw["waiver.index.toml"] = idx([envelope("WV-1", "waiver", "active", actor={"kind": "importer"})])
        mwr = run(mw)
        check("importer-waiver-inactive-not-valid",
              mwr is not None and mwr.status != VALID
              and any(m.startswith(".working/toml/waiver.index.toml: 1 schema-deferred record(s) of type "
                                   "'waiver'") and _SCHEMA_DEFERRAL in m for m in mwr.cannot_evaluate))

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
        # A moved row whose destination index FILE is entirely absent: the absence is a named finding, and
        # the record, confirmed seated nowhere, leaves its type's population unread (CANNOT-EVALUATE).
        f = clean_machine()
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "reference", "destination": "archive/2026/reference.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["counters.toml"] = counters(RF=9)
        # archive/2026/reference.index.toml is deliberately NOT created (destination file absent)
        r = run(f)
        check("archive-dest-file-absent-fails-closed", r is not None and r.status == CANNOT_EVALUATE)
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
        # Homes 2: a number an id reservation holds that no present record carries and no record of the
        # prior committed snapshot held (an orphaned reservation's) is not reported missing by C-NO-DELETION
        # or C-CONTIGUITY, which read each reservation for that purpose alone; an unreadable or malformed
        # reservation is CANNOT-EVALUATE. Homes 2 is activated only inside these vectors; the shipped
        # ceiling stays 1. The gap store holds BI-1, BI-2, BI-4 and WL-1 to WL-4 and WL-6 under BI = 4 and
        # WL = 6, so BI-3 and WL-5 are allocated with no published record; the clean prior held BI-1, BI-2
        # and WL-1 to WL-4.
        from unittest.mock import patch as _patch
        import _opf_journal as _ojr
        import _opf_oplock as _oplk

        def h2_manifest(machine):
            machine = copy.deepcopy(machine)
            machine["manifest.toml"]["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION
            machine["manifest.toml"]["opf"]["homes"] = 2
            return machine

        def h2_run_id(kind, n):
            return "{}-20260927T00{:04d}Z-{:016x}".format(_opf_store._HOME_RUN_PREFIXES[kind], n, n)

        def h2_reservation(kind, n, claims):
            # The reservation of one run: its path under .working/ and its canonical bytes.
            rid = h2_run_id(kind, n)
            return (_opf_store.allocation_record(kind, rid)[len(WORKING_DIRNAME) + 1:],
                    _ojr.reservation_payload(kind, rid, "op-{}".format(n), claims).decode("utf-8"))

        def h2(machine, working, product=None, obs="clean"):
            with _patch.object(_opf_store, "SUPPORTED_HOMES", 2):
                return run(machine, product=product, working=working, obs=obs)

        def h2_gap(*reservations):
            machine = h2_manifest(clean_machine())
            machine["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open"), bi(4, "open")])
            machine["counters.toml"] = counters(BI=4, WL=6)
            machine["worklog.toml"] = {"schema": 1, "entry": [wl(3), wl(4), wl(6)]}
            return machine, dict(reservations)

        def nd(r, needle, check_id="C-NO-DELETION"):
            return r is not None and any(needle in m for m in r.by_check.get(check_id, []))

        published = h2_reservation("record", 2, {"BI": 4, "WL": 6})
        orphan = h2_reservation("record", 1, {"BI": 3, "WL": 5})
        r = h2(h2_manifest(clean_machine()), {})
        check("homes2-reservations-clean-valid", r is not None and r.status == VALID
              and "C-EVIDENCE-ENUM" in r.checks)
        r = h2(*h2_gap(published, orphan))
        check("homes2-reserved-gap-valid", r is not None and r.status == VALID)
        check("homes2-reserved-gap-checks-pass", r is not None and r.checks.get("C-NO-DELETION") == "PASS"
              and r.checks.get("C-CONTIGUITY") == "PASS")
        r = h2(*h2_gap(published))
        check("homes2-unreserved-gap-invalid", r is not None and r.status == INVALID
              and nd(r, "BI-3 is absent") and nd(r, "WL-5 is absent") and nd(r, "WL-5 is missing", "C-CONTIGUITY"))
        for kind in _opf_store.JOURNAL_KINDS:
            r = h2(*h2_gap(published, h2_reservation(kind, 1, {"BI": 3, "WL": 5})))
            check("homes2-reserved-gap-kind-" + kind, r is not None and r.status == VALID)
        r = h2(*h2_gap(published, h2_reservation("import", 1, {"imported:BI": 3, "imported:WL": 5})))
        check("homes2-imported-key-reserves-no-clean-number", r is not None and r.status == INVALID
              and nd(r, "BI-3 is absent") and nd(r, "WL-5 is missing", "C-CONTIGUITY"))
        # Journal frames, projections and run directories are never opened by doctor: every contained open
        # passes _journal._open_parent, and while the store is validated (after its resolution) the spy
        # refuses any path under journals/ but an allocation home and a file directly inside one, recording
        # each, so a read that ignored the bytes still fails here.
        journal_root = WORKING_DIRNAME + "/journals"
        allocation_homes = {_opf_store.allocation_record(kind, h2_run_id(kind, 1)).rsplit("/", 1)[0]
                            for kind in _opf_store.JOURNAL_KINDS}

        def journal_refused(relpath):
            return ((relpath == journal_root or relpath.startswith(journal_root + "/"))
                    and relpath not in allocation_homes and relpath.rsplit("/", 1)[0] not in allocation_homes)

        def h2_spied(machine, working):
            opened = []
            real_open_parent = _journal._open_parent

            def spy(root_fd, relpath):
                opened.append(relpath)
                if journal_refused(relpath):
                    raise _journal.JournalError("a journal frame, projection or run path was opened: " + relpath)
                return real_open_parent(root_fd, relpath)
            with _patch.object(_opf_store, "SUPPORTED_HOMES", 2):
                res = resolve_store(build(machine, None, working))
                if res.status != RESOLVED:
                    return None, opened
                with _patch.object(_journal, "_open_parent", spy):
                    return validate_store(res, observations=clean_prior()), opened

        gm, gw = h2_gap(published, orphan)
        for kind in _opf_store.JOURNAL_KINDS:
            gw["journals/{}/journal/frames.log".format(kind)] = "torn"
            gw["journals/{}/runs/{}/transaction.toml".format(kind, h2_run_id(kind, 1))] = "not toml ["
        r, opened = h2_spied(gm, gw)
        check("homes2-journal-frames-unread", r is not None and r.status == VALID
              and not any(journal_refused(p) for p in opened)
              and WORKING_DIRNAME + "/" + orphan[0] in opened)

        # Above the highest present number, only the allocated numbers in (max, high] that no reservation
        # holds are owed: each end tested at, just inside and just outside it.
        def h2_overhang(*numbers):
            # BI-1 and BI-2 present under BI = 4: BI-3 and BI-4 are allocated above the highest present.
            machine = h2_manifest(clean_machine())
            machine["counters.toml"] = counters(BI=4)
            return h2(machine, dict(h2_reservation("record", i + 1, {"BI": n}) for i, n in enumerate(numbers)))

        def owed(r, count):
            return r is not None and r.status == INVALID and nd(r, "C-NO-DELETION: {} allocated BI-".format(count))

        r = h2_overhang(3, 4)
        check("homes2-overhang-reserved-valid", r is not None and r.status == VALID)
        check("homes2-overhang-unreserved-counted", owed(h2_overhang(3), 1))
        check("homes2-overhang-above-high-water-not-counted", owed(h2_overhang(3, 5), 1))
        check("homes2-overhang-at-max-present-not-counted", owed(h2_overhang(2, 4), 1))
        check("homes2-overhang-below-max-present-not-counted", owed(h2_overhang(1, 4), 1))

        # The worklog overhang is scanned at its own site and steps over reserved numbers the same way:
        # WL-1 to WL-4 present under WL = 6, so WL-5 and WL-6 are allocated above the highest present.
        def h2_wl_overhang(*numbers):
            machine = h2_manifest(clean_machine())
            machine["counters.toml"] = counters(WL=6)
            return h2(machine, dict(h2_reservation("record", i + 1, {"WL": n}) for i, n in enumerate(numbers)))

        r = h2_wl_overhang(5, 6)
        check("homes2-wl-overhang-reserved-valid", r is not None and r.status == VALID)
        r = h2_wl_overhang(5)
        check("homes2-wl-overhang-unreserved-counted", r is not None and r.status == INVALID
              and nd(r, "C-NO-DELETION: 1 allocated worklog id(s) above WL-4"))

        # Below the highest present number the scan steps over reserved numbers only and names the first
        # unreserved one.
        def h2_below(*numbers):
            # BI-1, BI-2 and BI-5 present under BI = 5: BI-3 and BI-4 lie below the highest present.
            machine = h2_manifest(clean_machine())
            machine["backlog_item.index.toml"] = idx([bi(1, "done"), bi(2, "open"), bi(5, "open")])
            machine["counters.toml"] = counters(BI=5)
            return h2(machine, dict(h2_reservation("record", i + 1, {"BI": n}) for i, n in enumerate(numbers)))

        r = h2_below(3, 4)
        check("homes2-gap-reserved-run-valid", r is not None and r.status == VALID)
        r = h2_below(4)
        check("homes2-gap-first-unreserved-named", r is not None and r.status == INVALID and nd(r, "BI-3 is absent"))
        r = h2_below(3)
        check("homes2-gap-last-unreserved-named", r is not None and r.status == INVALID and nd(r, "BI-4 is absent"))
        # The worklog may start at a reserved WL-1 (the F-6a store: WL-2 and WL-3 under WL = 3, whose prior
        # held FN-1, FN-2, WL-2 and WL-3).
        _f6a_h2_obs = copy.deepcopy(_f6a_obs)
        _f6a_h2_obs["prior"]["held"] = {"FN": [[1, 2]], "WL": [[2, 3]]}
        r = h2(h2_manifest(_f6a), dict([h2_reservation("record", 1, {"WL": 1})]), pr_product, _f6a_h2_obs)
        check("homes2-wl-start-reserved-valid", r is not None and r.status == VALID)
        r = h2(h2_manifest(_f6a), {}, pr_product, _f6a_h2_obs)
        check("homes2-wl-start-unreserved-invalid", r is not None and r.status == INVALID
              and nd(r, "does not start at WL-1", "C-CONTIGUITY"))
        # Doctor VALID does not make the unreleased tail cuttable: over the same version.toml and worklog
        # (active and archive together) the release cut, which reads no reservation, refuses a tail with a
        # reserved number below its highest entry, and the homes-2 report says so.
        r = h2(h2_manifest(_f6a), dict([h2_reservation("record", 1, {"WL": 1})]), pr_product, _f6a_h2_obs)
        start_cut = release_cut(_f6a["version.toml"], _f6a["worklog.toml"], "1.0.0", TS)
        check("homes2-wl-start-reserved-cut-refused", r is not None and r.status == VALID
              and start_cut.status == INVALID and start_cut.version_data is None
              and any("tail WL-1..WL-3 is not contiguous" in f for f in start_cut.findings))
        check("homes2-reserved-tail-residual-disclosed", r is not None and any(
            "a VALID doctor result does not mean the unreleased tail can be cut" in m for m in r.residuals))
        gap_machine, gap_working = h2_gap(published, orphan)
        r_gap = h2(gap_machine, gap_working)
        gap_merged = {"schema": 1, "entry": (gap_machine["archive/2026/worklog.toml"]["entry"]
                                             + gap_machine["worklog.toml"]["entry"])}
        gap_cut = release_cut(gap_machine["version.toml"], gap_merged, "1.2.0", TS)
        check("homes2-reserved-gap-cut-refused", r_gap is not None and r_gap.status == VALID
              and gap_cut.status == INVALID and gap_cut.version_data is None
              and any("tail WL-5..WL-6 is not contiguous" in f for f in gap_cut.findings))

        # An unreadable or malformed reservation is CANNOT-EVALUATE under C-NO-DELETION, C-CONTIGUITY points
        # to it, and both then grade as though nothing were reserved (never a wider pass).
        def h2_cant(r, needle):
            return (r is not None and r.status == CANNOT_EVALUATE
                    and r.checks.get("C-NO-DELETION") == "CANNOT-EVALUATE"
                    and r.checks.get("C-CONTIGUITY") == "CANNOT-EVALUATE"
                    and any(needle in m and m in r.cannot_evaluate for m in r.by_check.get("C-NO-DELETION", []))
                    and nd(r, "BI-3 is absent") and nd(r, "WL-5 is missing", "C-CONTIGUITY"))

        bad = h2_reservation("import", 9, {"BI": 3})
        import_home = bad[0].rsplit("/", 1)[0]
        staged = import_home + "/." + h2_run_id("import", 9) + ".toml" + _oplk._STAGING_MARKER
        for label, entry, needle in (
                ("not-toml", (bad[0], "not toml [\n"), "is malformed"),
                ("claims-out-of-range", (bad[0], bad[1].replace("BI = 3", "BI = 0")), "is malformed"),
                ("non-canonical", (bad[0], bad[1] + "\n"), "differs from the exact canonical"),
                ("other-kind-payload", (bad[0], _ojr.reservation_payload(
                    "ingest", h2_run_id("import", 9), "op-9", {"BI": 3}).decode("utf-8")),
                 "differs from the exact canonical"),
                ("misnamed", (import_home + "/notes.txt", bad[1]), "is not an id reservation"),
                ("other-kind-name", (import_home + "/" + h2_run_id("record", 9) + ".toml", bad[1]),
                 "is not an id reservation"),
                ("near-staging-name", (staged + "0" * 31, "torn"), "is not an id reservation"),
                ("directory", (bad[0] + "/inner.toml", bad[1]), "is a directory")):
            gm, gw = h2_gap(published, orphan)
            gw[entry[0]] = entry[1]
            check("homes2-reservation-cannot-evaluate-" + label, h2_cant(h2(gm, gw), needle))
        gm, gw = h2_gap(published, orphan)
        gw[staged + "0" * 32] = "torn"
        r = h2(gm, gw)
        check("homes2-reservation-staging-leftover-skipped", r is not None and r.status == VALID)

        # A reserved number has one owner across every kind's home: a second canonical reservation claiming
        # the same number under the same series key is CANNOT-EVALUATE naming both files (dup and first, the
        # orphan's reservation unless another is given; the store keeps the published and orphan ones).
        def h2_conflict(dup, needle, first=orphan):
            r = h2(*h2_gap(published, orphan, first, dup))
            names = [WORKING_DIRNAME + "/" + p for p in (first[0], dup[0])]
            return h2_cant(r, needle) and any(needle in m and all(n in m for n in names) for m in r.cannot_evaluate)

        check("homes2-reservation-conflict-same-kind",
              h2_conflict(h2_reservation("record", 3, {"BI": 3}), "both claim 3 under the series key BI"))
        check("homes2-reservation-conflict-cross-kind",
              h2_conflict(h2_reservation("ingest", 3, {"WL": 5}), "both claim 5 under the series key WL"))
        imported = h2_reservation("import", 3, {"imported:BI": 3})
        check("homes2-reservation-conflict-imported-key", h2_conflict(
            h2_reservation("import", 4, {"imported:BI": 3}), "both claim 3 under the series key imported:BI", imported))

        # A reservation read failure names C-NO-DELETION, so the C-CONTIGUITY pointer is accurate: an
        # unreadable regular file, and one listed but absent at read.
        orphan_rel = WORKING_DIRNAME + "/" + orphan[0]
        real_read_contained, real_list_contained = _journal._read_contained, _list_contained

        def unreadable(fd, relpath, *args, **kwargs):
            if relpath == orphan_rel:
                raise _journal.JournalError("injected read error")
            return real_read_contained(fd, relpath, *args, **kwargs)

        phantom = h2_run_id("record", 7) + ".toml"

        def listed_phantom(fd, reldir):
            subdirs, files = real_list_contained(fd, reldir)
            if reldir == orphan_rel.rsplit("/", 1)[0]:
                files = sorted((files or []) + [phantom])
            return subdirs, files

        with _patch.object(_journal, "_read_contained", unreadable):
            r = h2(*h2_gap(published, orphan))
        check("homes2-reservation-cannot-evaluate-unreadable",
              h2_cant(r, "C-NO-DELETION: cannot read " + orphan_rel))
        with _patch.dict(globals(), {"_list_contained": listed_phantom}):
            r = h2(*h2_gap(published, orphan))
        check("homes2-reservation-cannot-evaluate-absent-at-read", h2_cant(
            r, "C-NO-DELETION: the id reservation {}/{} was listed but is absent at read".format(
                orphan_rel.rsplit("/", 1)[0], phantom)))

        def h2_built(mutate):
            gm, gw = h2_gap(published, orphan)
            root = build(gm, None, gw)
            mutate(root / WORKING_DIRNAME)
            with _patch.object(_opf_store, "SUPPORTED_HOMES", 2):
                return validate_store(resolve_store(root), observations=clean_prior())

        def symlinked_entry(working):
            os.symlink("nowhere", str(working / orphan[0].rsplit("/", 1)[0] / (h2_run_id("record", 8) + ".toml")))

        def symlinked_home(working):
            (working / "journals" / "layout").mkdir(parents=True)
            os.symlink("nowhere", str(working / "journals" / "layout" / "allocations"))

        def home_not_directory(working):
            (working / "journals" / "adoption").mkdir(parents=True)
            (working / "journals" / "adoption" / "allocations").write_text("x", encoding="utf-8")

        for label, mutate in (("symlink-entry", symlinked_entry), ("symlink-home", symlinked_home),
                              ("home-not-directory", home_not_directory)):
            check("homes2-reservation-cannot-evaluate-" + label, h2_cant(h2_built(mutate), "cannot list"))
        with _patch.object(_opf_store, "JOURNAL_KINDS", _opf_store.JOURNAL_KINDS + ("bogus",)):
            r = h2(*h2_gap(published, orphan))
        check("homes2-reservation-cannot-evaluate-unknown-kind", h2_cant(r, "no reservation home"))
        # A legacy store reads no reservation, even under activated tooling: the malformed one is never
        # read and the gap is reported.
        gm, gw = h2_gap(published, orphan)
        gm["manifest.toml"] = clean_machine()["manifest.toml"]
        gw[bad[0]] = "not toml [\n"
        r = h2(gm, gw)
        check("homes1-reservations-unread", r is not None and r.checks.get("C-NO-DELETION") == "FINDING"
              and r.checks.get("C-CONTIGUITY") == "FINDING" and nd(r, "BI-3 is absent"))
        # Read-only: the store tree, bytes and modification times, is unchanged by the read.
        gm, gw = h2_gap(published, orphan)
        ro_root = build(gm, None, gw)

        def tree(root):
            return sorted((p.relative_to(root).as_posix(), p.read_bytes() if p.is_file() else b"",
                           p.stat().st_mtime_ns) for p in root.rglob("*"))
        ro_before = tree(ro_root)
        with _patch.object(_opf_store, "SUPPORTED_HOMES", 2):
            r = validate_store(resolve_store(ro_root), observations=clean_prior())
        check("homes2-reservations-read-only", r is not None and r.status == VALID and tree(ro_root) == ro_before)

        # A reservation is irrevocable and outlives the publication it preceded, so the exemption holds only
        # where the prior committed snapshot held no record with that id. BI-1 to BI-4 are allocated (BI = 4),
        # each reserved by its own record run as homes 2 requires before a publication; `present` are the BI
        # numbers the store carries, `prior` those the prior's type index held, `archived` those it held in
        # its archive alone.
        def h2_history(present, prior=(1, 2), archived=(), obs=None):
            machine = h2_manifest(clean_machine())
            machine["backlog_item.index.toml"] = idx([bi(1, "done")] + [bi(n, "open") for n in present if n > 1])
            machine["counters.toml"] = counters(BI=4)
            if obs is None:
                obs = clean_prior()
                obs["prior"]["counters_high"]["BI"] = max((2,) + tuple(prior) + tuple(archived))
                obs["prior"]["records"].update({"BI-{}".format(n): ("backlog_item", "open") for n in prior if n > 2})
                obs["prior"]["held"]["BI"] = [[n, n] for n in sorted(set(prior) | set(archived))]
            return h2(machine, dict(h2_reservation("record", n, {"BI": n}) for n in (1, 2, 3, 4)), obs=obs)

        r = h2_history((1, 2, 3), prior=(1, 2, 3, 4))
        check("homes2-published-tail-deleted-invalid", r is not None and r.status == INVALID
              and nd(r, "C-NO-DELETION: 1 allocated BI-<n> id(s) above BI-3"))
        r = h2_history((1, 2, 4), prior=(1, 2, 3, 4))
        check("homes2-published-mid-deleted-invalid", r is not None and r.status == INVALID
              and nd(r, "C-NO-DELETION: BI-3 is absent"))
        r = h2_history((1, 2, 3), prior=(1, 2, 3), archived=(4,))
        check("homes2-archived-at-prior-deleted-invalid", r is not None and r.status == INVALID
              and r.checks.get("C-HISTORY-RESURRECTION") == "PASS"
              and nd(r, "C-NO-DELETION: 1 allocated BI-<n> id(s) above BI-3"))
        r = h2_history((1, 2, 3), prior=(1, 2, 3))
        check("homes2-orphan-never-committed-valid", r is not None and r.status == VALID)
        # A record published and deleted again before any commit leaves only its reservation: with no prior
        # commit (an unborn HEAD's empty prior) it cannot be told from an orphan (the disclosed residual).
        r = h2_history((1, 2, 3), obs={"tracked": "tracked", "prior": {
            "releases": [], "counters_high": {}, "records": {}, "digests": {}, "held": {}}})
        check("homes2-no-prior-commit-residual-valid", r is not None and r.status == VALID)

        # No usable prior: the exemption is CANNOT-EVALUATE, never granted.
        def no_prior_cant(r, reason, check_id="C-NO-DELETION"):
            return (r is not None and r.status == CANNOT_EVALUATE and r.checks.get(check_id) == "CANNOT-EVALUATE"
                    and nd(r, "cannot be told from deleted records: " + reason, check_id))

        malformed = clean_prior()
        malformed["prior"]["records"] = "not a table"
        stripped = clean_prior()
        del stripped["prior"]["held"]
        for label, obs, reason in (
                ("malformed", malformed, "the prior committed snapshot observation was supplied but malformed"),
                ("absent", {"tracked": "tracked"}, "no prior committed snapshot was supplied"),
                ("no-held", stripped, "the prior committed snapshot does not enumerate every id it held")):
            check("homes2-prior-" + label + "-cannot-evaluate",
                  no_prior_cant(h2_history((1, 2, 3), obs=obs), reason))

        # The worklog likewise: WL-5 published, committed and deleted with its reservation standing is owed,
        # at the tail and mid-series, and with no prior a reserved worklog gap is CANNOT-EVALUATE under
        # C-CONTIGUITY too.
        def h2_wl_history(present, high, prior_high, obs=None):
            machine = h2_manifest(clean_machine())
            machine["counters.toml"] = counters(WL=high)
            machine["worklog.toml"] = {"schema": 1, "entry": [wl(n) for n in present if n > 2]}
            if obs is None:
                obs = clean_prior()
                obs["prior"]["counters_high"]["WL"] = prior_high
                obs["prior"]["held"]["WL"] = [[1, prior_high]]
            return h2(machine, dict(h2_reservation("record", n, {"WL": n}) for n in range(5, high + 1)), obs=obs)

        r = h2_wl_history((1, 2, 3, 4), 5, 5)
        check("homes2-wl-published-tail-deleted-invalid", r is not None and r.status == INVALID
              and nd(r, "C-NO-DELETION: 1 allocated worklog id(s) above WL-4"))
        r = h2_wl_history((1, 2, 3, 4, 6), 6, 6)
        check("homes2-wl-published-mid-deleted-invalid", r is not None and r.status == INVALID
              and nd(r, "C-NO-DELETION: WL-5 is absent") and nd(r, "WL-5 is missing", "C-CONTIGUITY"))
        r = h2_wl_history((1, 2, 3, 4, 6), 6, 6, obs={"tracked": "tracked"})
        check("homes2-wl-no-prior-contiguity-cannot-evaluate",
              no_prior_cant(r, "no prior committed snapshot was supplied", "C-CONTIGUITY")
              and no_prior_cant(r, "no prior committed snapshot was supplied"))

        # Each end of each new interval, tested just outside, at and just inside it. The prior-held lookup
        # over BI-3 to BI-5 and BI-8, and FN-1 from the type index alone:
        bounds = ({("FN", 1)}, {"BI": [(3, 5), (8, 8)]})
        check("homes2-prior-held-interval-ends",
              [n for n in range(1, 11) if _prior_held(bounds, "BI", n)] == [3, 4, 5, 8]
              and _prior_held(bounds, "FN", 1) and not _prior_held(bounds, "FN", 2)
              and not _prior_held(bounds, "WL", 4))

        # The observed `held` bounds: an id number in 1 to 2**63 - 1, a span low end not above its high end,
        # a roster namespace; anything else makes the whole prior malformed. Adjacent spans merge.
        def held_prior(held):
            prior = clean_prior()["prior"]
            prior["held"] = held
            return _normalize_prior(prior)

        top = (1 << 63) - 1
        check("homes2-prior-held-normalizer-bounds",
              held_prior({"BI": [[1, 1], [2, top]]})["held"] == {"BI": [(1, top)]}
              and held_prior({"BI": [[3, 3]]})["held"] == {"BI": [(3, 3)]}
              and all(held_prior(bad) is None for bad in (
                  {"BI": [[0, 1]]}, {"BI": [[1, top + 1]]}, {"BI": [[3, 2]]}, {"BI": [[True, 2]]},
                  {"ZZ": [[1, 1]]}, {"BI": [[1]]}, {"BI": "1-2"}, ["BI"])))
        # With no prior, the undecided numbers C-NO-DELETION reports lie in (floor, high-water]: WL-5 to WL-7
        # reserved over WL-1 to WL-4 under WL = 6 report WL-5 and WL-6, not WL-7, and none lies below the
        # highest present worklog number, so C-CONTIGUITY passes.
        machine = h2_manifest(clean_machine())
        machine["counters.toml"] = counters(WL=6)
        r = h2(machine, dict(h2_reservation("record", n, {"WL": n}) for n in (5, 6, 7)), obs={"tracked": "tracked"})
        check("homes2-undecided-high-water-end", r is not None and r.status == CANNOT_EVALUATE
              and nd(r, "C-NO-DELETION: 2 reserved WL number(s) that no present record carries, the first WL-5,")
              and r.checks.get("C-CONTIGUITY") == "PASS")
        # Under an ancestral floor of BI = 3, BI-2 (below it) and BI-3 (at it) are not reported, BI-4 is.
        machine = h2_manifest(clean_machine())
        machine["backlog_item.index.toml"] = idx([bi(1, "done"), bi(5, "open")])
        machine["counters.toml"] = counters(BI=5)
        floor_root = build(machine, None, dict(h2_reservation("record", n, {"BI": n}) for n in (2, 3, 4, 5)))
        with _patch.object(_opf_store, "SUPPORTED_HOMES", 2):
            r = validate_store(resolve_store(floor_root), observations={"tracked": "tracked"},
                               ancestral_floor={"BI": 3})
        check("homes2-undecided-floor-end", r is not None and r.status == CANNOT_EVALUATE
              and nd(r, "C-NO-DELETION: 1 reserved BI number(s) that no present record carries, the first BI-4,"))
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
