#!/usr/bin/env python3
"""OPF (DevProcess) store-level integrity validator: the reusable `opf doctor` engine (OPF core-tooling U6).

Offline, stdlib only, fail-closed, parse-only and read-only over the store. U2 (`_opf_schema`) validates
ONE record at a time and U3 (`_opf_release`) validates ONE ledger at a time; neither can see the whole
store, so the class of spec-MANDATED cross-record, historical, archive, view, changelog, and topology
invariants the integrity layer owns (OPF-SPEC 11) has no owner. THIS unit builds that whole-store layer as
a reusable ENGINE, `validate_store`, that REUSES the U1/U2/U3/U4/U5/U7/U8 primitives and never
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
import bisect
import hashlib
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained no-follow parent-open primitive (reused for listing / raw reads)
import _opf_emit       # noqa: E402  U8 canonical emitter (the per-record digest basis)
import _opf_import     # noqa: E402  U7 staging enumerator (imports/<run-id>/ sibling ids)
import _opf_views      # noqa: E402  U4 deterministic view planner (byte-drift comparison)
import _opf_changelog  # noqa: E402  U5 changelog range-coverage + freeze gates (composed here)
# U1 supplies the outcome model, the contained readers, the manifest validator, the taxonomy, and helpers.
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, RESOLVED, MANIFEST_NAME, WORKING_DIRNAME, StoreError,
    _read_toml_contained, _open_store_root_fd, _open_root_fd, validate_manifest, classify_target,
    _sorted_key_names, _safe_display, _is_contained_relpath,
    BASELINE_TYPES, MODULE_TYPES, IMPORTER_TYPES, KNOWN_MODULES,
)
# U2 supplies the record validator, the counter guards, the transition validator, and the type specs.
from _opf_schema import (  # noqa: E402
    validate_record, validate_counters, check_unique_ids, check_ids_within_counters, check_monotonic,
    validate_transition, parse_status, BASELINE_SPECS, ACTOR_KINDS, LINK_RELS, _valid_id_shape,
    SUPPORTED_SCHEMA, _valid_timestamp,
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
WORKLOG_NAME = "worklog.toml"
LEASE_NAME = "lease.toml"                  # present only while the single-writer lease is held (spec 5.7)
ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_NAME = "archive.toml"
INDEX_SUFFIX = ".index.toml"

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
# importer-only legacy_fragment type (its record schema ships in U7's import layer, not the baseline
# validator), mirroring U7's own staging validation of the identical shape.
_SCHEMA_DEFERRAL = ("record schema not available to the baseline validator; deferred (module-tier schemas "
                    "land in U2M, the importer legacy_fragment schema in the import layer)")


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
    "C-VERSION-LEDGER", "C-COUNTERS", "C-ROTATION", "C-STAGING", "C-LEASE", "C-ID-SPACE", "C-RECEIPTS",
    "C-HANDOFF",
    "C-DECISION-CHAINS", "C-LINKS", "C-FROZEN-COVERAGE", "C-NO-DELETION", "C-PARTITION", "C-CONTIGUITY",
    "C-TRACKED", "C-SYNC-AGREE", "C-CONTAINMENT", "C-VIEW-DRIFT", "C-VERSION-FILE", "C-CHANGELOG-GATES",
    "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-HISTORY-RESURRECTION",
)
_REQUIRED_SET = frozenset(REQUIRED_CHECKS)

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


class StoreValidation:
    """The whole-store outcome (the U1 ManifestValidation idiom, one level up). `status` reduces to a
    0/1/2 exit code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE); CANNOT-EVALUATE dominates INVALID, which
    dominates VALID, so an unreadable input is never masked by an otherwise-clean read. `checks` is the
    ordered per-check verdict map; `triage` is the spec-11/14.2 partial-import surface; `evaluated_profiles`
    and `unevaluated_profiles` name the profile scope (spec 16); `residuals` are the disclosed by-design
    uncovered sub-checks, reported but never changing the status."""
    __slots__ = ("status", "findings", "cannot_evaluate", "residuals", "checks", "triage",
                 "evaluated_profiles", "unevaluated_profiles")

    def __init__(self, status, findings=None, cannot_evaluate=None, residuals=None, checks=None,
                 triage=None, evaluated_profiles=None, unevaluated_profiles=None):
        self.status = status
        self.findings = findings or []
        self.cannot_evaluate = cannot_evaluate or []
        self.residuals = residuals or []
        self.checks = checks or {}
        self.triage = triage or []
        self.evaluated_profiles = evaluated_profiles or []
        self.unevaluated_profiles = unevaluated_profiles or []


def exit_code(result):
    """Reduce a StoreValidation to a 0/1/2 exit code for the future doctor verb."""
    if result.status == CANNOT_EVALUATE:
        return 2
    if result.status == INVALID:
        return 1
    return 0


class _Report:
    """A fail-closed accumulator plus the check-routing registry. `ran` registers a required check and makes
    it the current attribution target; `finding` / `cant` record a defect / a cannot-evaluate and attribute
    it to the current check. `result` reconciles the emitted check set against REQUIRED_CHECKS: a required
    check that never registered `ran` is routed to CANNOT-EVALUATE naming it, so a silently-skipped check is
    never a pass."""
    __slots__ = ("findings", "cannot", "residuals", "checks", "triage", "_current")

    def __init__(self):
        self.findings = []
        self.cannot = []
        self.residuals = list(_RESIDUALS)
        self.checks = {}                  # insertion-ordered check-id -> "PASS" / "FINDING" / "CANNOT-EVALUATE"
        self.triage = []
        self._current = None

    def ran(self, check_id):
        # Register a required check as executed and make it the attribution target. A second ran() for the
        # same id is an internal fault (a check double-counted), fail-closed.
        if check_id in self.checks:
            self.cannot.append("internal: check {!r} was run more than once".format(check_id))
        else:
            self.checks[check_id] = "PASS"
        self._current = check_id

    def finding(self, msg):
        self.findings.append(msg)
        self._attribute("FINDING")

    def cant(self, msg):
        self.cannot.append(msg)
        self._attribute("CANNOT-EVALUATE")

    def triage_path(self, msg):
        self.triage.append(msg)

    def _attribute(self, status):
        cid = self._current
        if cid is None:
            return
        cur = self.checks.get(cid)
        if cur is None or status == "CANNOT-EVALUATE":
            self.checks[cid] = status
        elif cur == "PASS":
            self.checks[cid] = "FINDING"

    def result(self, evaluated_profiles=None, unevaluated_profiles=None):
        # Reconcile the emitted check ids against the closed REQUIRED_CHECKS roster. A required check that
        # never registered ran() is routed to CANNOT-EVALUATE naming it; an unknown id that somehow ran is a
        # fail-closed internal fault. Neither can read as a pass.
        for cid in REQUIRED_CHECKS:
            if cid not in self.checks:
                self.cannot.append("internal: required check {!r} did not run; routed to CANNOT-EVALUATE "
                                   "(a skipped check is never a pass)".format(cid))
                self.checks[cid] = "CANNOT-EVALUATE"
        for cid in list(self.checks):
            if cid not in _REQUIRED_SET:
                self.cannot.append("internal: an unknown check id {!r} was run (not in REQUIRED_CHECKS; "
                                   "fail-closed)".format(cid))
        ordered = {cid: self.checks[cid] for cid in REQUIRED_CHECKS}
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
                               sorted(evaluated_profiles or []), sorted(unevaluated_profiles or []))


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
            for entry in sorted(os.listdir(dfd)):
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
            os.close(dfd)
    finally:
        os.close(pfd)


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
        host = authority     # KEEP the port: host:port is part of the endpoint identity (codex-4),
        # EXCEPT a port that spells the scheme's DEFAULT names the same endpoint as the port-less form
        # (spec 5.6 host+path equivalence): https://h:443/p, ssh://h:22/p and github:org/repo are one
        # endpoint. A NON-default port (2222 vs 2223) stays distinct (F3); match only a trailing numeric
        # default so a bracketed IPv6 host is never mis-split.
        _default_port = {"https": "443", "http": "80", "ssh": "22", "git": "9418"}.get(scheme.lower())
        hname, _sep, hport = host.rpartition(":")
        if _sep and hname and hport == _default_port:
            host = hname
    else:
        # scp-style [user@]host:path: the colon before any slash separates host from path. A bracketed
        # IPv6 host ([addr]) carries colons INSIDE the brackets that are part of the address, not the
        # separator, so the separator is the colon immediately after the closing bracket; a malformed
        # bracket or a missing host:path colon is unresolvable (returns None, disclosed; gemini round-7).
        at = s.rfind("@")
        hoststart = at + 1 if at != -1 else 0
        if s[hoststart:hoststart + 1] == "[":
            rb = s.find("]", hoststart)
            if rb == -1 or s[rb + 1:rb + 2] != ":":
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
    for tname in sorted(name for name in enabled_types if name != "worklog"):
        namespace = enabled_types[tname]
        idx_rel = _rel(machine_rel, "{}{}".format(tname, INDEX_SUFFIX))
        data, st = _read_toml(root_fd, idx_rel, rep)
        if st == "error":
            continue
        if st == "absent":
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


def _gather_worklog(root_fd, relpath, registered_vendors, rep, required):
    """Read and validate a worklog.toml (active or an archive bucket) through U3's validate_worklog, and
    return its WL-number -> entry map. A required (active) worklog that is absent is CANNOT-EVALUATE; an
    archive-bucket worklog that is absent returns None (the caller only reads it when the bucket has one)."""
    data, st = _read_toml(root_fd, relpath, rep)
    if st == "error":
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


def _has_active_import_run(root_fd, machine_rel, rep):
    """True when an imports/<run-id> run directory (a valid U7 run-id shape) is actually present. A declared
    import_status = "partial" is substantiated only by such a run (spec 11:940 requires an import or
    migration ACTUALLY running); with none present a partial declaration cannot license the triage posture
    that disables steady-state grading (codex-2). A listing failure is recorded CANNOT-EVALUATE by _list_dir
    and returns False (fail-closed: the cant dominates, so the paths then grade as findings)."""
    imports_rel = _rel(machine_rel, _opf_import.IMPORTS_DIRNAME)
    subdirs, _files = _list_dir(root_fd, imports_rel, rep)
    if not subdirs:
        return False
    for d in subdirs:
        if not _opf_import._RUN_ID_RE.match(d):
            continue
        # round-14 C5: a partial status is substantiated only by a WELL-FORMED run, not a bare
        # run-id-named (possibly empty) directory. Require the staged plan.toml (stage_import always writes
        # it) to be present, so a fake or empty run dir cannot license the store-wide triage posture that
        # suppresses stray grading.
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


def _validate_archive(root_fd, machine_rel, enabled_types, registered_vendors, import_status, rep):
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
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep)
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
                rep.finding("C-ARCHIVE-ENUM: unexpected file {!r} in archive bucket {} (spec 12)".format(
                    fn_, bucket))
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


def _check_decision_chains(recs, by_id, rep):
    """C-DECISION-CHAINS (R3): exactly one current effective resolution per pending_decision supersession
    chain THAT HAS A RESOLUTION (OPF-SPEC 8.5). A chain is a connected component over `supersedes` links; a
    current effective resolution is a decided (unqualified) pending_decision that no other supersedes. For a
    chain carrying at least one decided member, the count of current resolutions must be exactly one, in
    BOTH directions: zero (the sole resolution superseded by a withdrawn or still-open successor) is a
    fail-open the count-only-over-one form missed (F14). A wholly-undecided chain legitimately has zero and
    is not flagged."""
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
    for _root, members in components.items():
        decided = [m for m in members if by_id.get(m) is not None
                   and by_id[m].state == "decided" and by_id[m].qual is None]
        if not decided:
            continue     # a wholly-undecided chain has no resolution yet: zero current is legitimate
        current = [m for m in decided if m not in superseded]
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


# --- containment (C-CONTAINMENT, OPF-SPEC 14.2) ------------------------------------------------------

def _check_containment(root_fd, machine_rel, enabled_types, layout, manifest_data, import_status, rep):
    """C-CONTAINMENT: recursively walk `.working/`, matching every regular file against the managed set
    (the ledgers, the enabled non-ledger type indexes, per-record bodies, the archive tree, declared
    store-scope view targets, and files under a valid declared [unmanaged] path). A path in neither set is
    a finding at steady state; at a SUBSTANTIATED `import_status = "partial"` (an active imports/<run-id>
    run present) it goes to `triage` instead (spec 11/14.2). The imports subtree interior is walked, not
    skipped, so a leftover run or stray bytes is graded (F3). An unmanaged declaration that names or
    contains a managed path is a finding. Listing failure is CANNOT-EVALUATE."""
    mrel = machine_rel
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
    unmanaged = []
    um = manifest_data.get("unmanaged") if isinstance(manifest_data, dict) else None
    if isinstance(um, dict) and isinstance(um.get("paths"), list):
        for p in um["paths"]:
            if isinstance(p, str) and _is_contained_relpath(p):
                unmanaged.append(p)
            else:
                # guard-input-soundness (round-14 C4): a malformed [unmanaged] entry (non-string, or a
                # non-contained / escaping path) is a named CANNOT-EVALUATE, not a silent drop; a malformed
                # control input is surfaced, never quietly removed.
                rep.cant("C-CONTAINMENT: [unmanaged] path entry {} is not a contained store-relative string "
                         "(spec 14.2); the unmanaged declaration cannot be evaluated".format(_safe_display(p)))
    ledger_names = frozenset({MANIFEST_NAME, COUNTERS_NAME, VERSION_NAME, WORKLOG_NAME, LEASE_NAME})
    archive_root = _rel(mrel, ARCHIVE_DIRNAME)
    imports_root = _rel(mrel, _opf_import.IMPORTS_DIRNAME)

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
            if "/" not in r:
                if r in ledger_names:
                    return True
                if r.endswith(INDEX_SUFFIX):
                    t = r[:-len(INDEX_SUFFIX)]
                    # worklog is a ledger only (home: worklog.toml), so worklog.index.toml is NEVER a
                    # managed leaf (F2); a ledger type has no `<type>.index.toml`.
                    if t in enabled_types and t not in _LEDGER_TYPES:
                        return True
            elif layout == "per-record":
                head, tail = r.split("/", 1)
                # worklog has no per-record bodies either (F2): entries live in worklog.toml, never in
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
    ledger_index_files = tuple(_rel(mrel, t + INDEX_SUFFIX) for t in sorted(enabled_types)
                               if t in _LEDGER_TYPES)
    graded_containers = (archive_root, imports_root) + type_body_dirs
    managed_dir_prefixes = (mrel, archive_root, imports_root) + type_body_dirs + ledger_index_files
    valid_unmanaged = []
    for u in unmanaged:
        if (managed_leaf(u)
                or any(_under_any(pfx, (u,)) for pfx in managed_dir_prefixes)
                or _under_any(u, graded_containers)
                or any(_under_any(vt, (u,)) for vt in view_targets)):
            rep.finding("C-CONTAINMENT: unmanaged path {!r} collides with a managed store path (an "
                        "unmanaged declaration cannot name or contain a managed file; spec 14.2)".format(u))
        else:
            valid_unmanaged.append(u)

    unmanaged_files = []

    def managed_dir(full):
        # A subdir that is a legitimate managed namespace to RECURSE rather than flag: the machine-store
        # root, the imports root and its run interior (graded/triaged per F3), a per-record type-body dir,
        # or an ancestor of a store-scope view target. (archive_root and valid_unmanaged subtrees are
        # handled by the caller's skip.) Anything else under the store is an UNREGISTERED directory, graded
        # as a stray even when empty (round-14: the walk previously graded files only, so an empty rogue
        # directory, or a tree of only empty dirs, escaped grading entirely).
        if full == mrel or _under_any(full, (imports_root,)):
            return True
        if layout == "per-record" and full in type_body_dirs:
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
        for f in files:
            full = reldir + "/" + f
            if not managed_file(full):
                unmanaged_files.append(full)
        for d in subdirs:
            full = reldir + "/" + d
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
    # imports/<run-id> run present; spec 11:940). Without one the partial declaration cannot license the
    # triage posture that would disable steady-state grading, so it is itself a finding and the stray paths
    # grade as findings, never a silent triage pass.
    partial_active = import_status == "partial" and _has_active_import_run(root_fd, machine_rel, rep)
    if import_status == "partial" and not partial_active:
        rep.finding("C-CONTAINMENT: the manifest declares import_status partial but no active "
                    "imports/<run-id> run is present; a partial status is substantiated only by an import "
                    "or migration actually running (spec 11:940/14.2)")
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
    records: {id: (type, status)}, digests: {id: str} (optional)}. Every record id is shape-validated and
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
    for ns, val in counters_high.items():
        if not isinstance(ns, str) or ns not in _ROSTER_NS_SET:
            return None
        if not (isinstance(val, int) and not isinstance(val, bool) and val >= 0):
            return None
        norm_high[ns] = val
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
    return {"releases": list(releases), "counters_high": norm_high, "records": norm_records,
            "digests": norm_digests}


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

def validate_store(resolution, supported_profiles=None, *, observations=None):
    """Validate a RESOLVED store's whole-store integrity (OPF-SPEC 11). `resolution` is the object
    _opf_store.resolve_store returns; a resolution that is not RESOLVED is CANNOT-EVALUATE. `observations`
    is the inert, all-optional git-derived-facts object the git-aware caller (the future doctor verb; the
    self-test now) injects: `tracked` ("tracked"/"untracked"), `actual_remote` (the store repository's real
    push URL), and `prior` (the prior committed snapshot, already parsed). The module reads every declared
    input through U1's contained no-follow readers and reuses the U2/U3/U4/U5/U7/U8 primitives; it owns the
    cross-record, historical, archive, view, changelog, and topology invariants no single-record or
    single-ledger pass can see. Returns a StoreValidation (VALID / INVALID / CANNOT-EVALUATE) plus the
    per-check verdicts, the partial-import triage surface, the profile scope, and the disclosed residuals.
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
            in_repo, rep)
    except Exception as exc:
        # Top-level fail-closed barrier (B6): any unexpected error (a RecursionError from a hostile-shaped
        # store, or any other escape no specific guard anticipated) becomes a CANNOT-EVALUATE naming the
        # failure, never a raw crash, so the "fail closed everywhere" contract holds
        # (check-fails-closed-on-unreadable).
        rep.cant("internal: store validation raised an unexpected {} and fails closed to CANNOT-EVALUATE "
                 "({})".format(type(exc).__name__, exc))
    finally:
        os.close(root_fd)
        if product_root_fd is not None:
            os.close(product_root_fd)
    return rep.result(evaluated_profiles, unevaluated_profiles)


def _validate_opened_store(root_fd, product_root_fd, machine_rel, supported_profiles, obs, pointer_target,
                           pointer_source, in_repo, rep):
    # --- C-MANIFEST: identify the store, derive enabled types / vendors / layout ----------------------
    rep.ran("C-MANIFEST")
    manifest_rel = _rel(machine_rel, MANIFEST_NAME)
    manifest_data, st = _read_toml(root_fd, manifest_rel, rep)
    if st != "ok":
        if st == "absent":
            rep.cant("{} is absent (the store manifest is required; spec 4.5)".format(manifest_rel))
        return [], []
    mv = validate_manifest(manifest_data, supported_profiles)
    if mv.status == CANNOT_EVALUATE:
        rep.cant("{}: {}".format(manifest_rel, "; ".join(mv.findings)))
        return [], []
    if mv.status != VALID:
        for f in mv.findings:
            rep.finding("manifest: {}".format(f))

    dp = manifest_data.get("devprocess") if isinstance(manifest_data, dict) else None
    layout = dp.get("layout") if isinstance(dp, dict) else None
    import_status = dp.get("import_status") if isinstance(dp, dict) else None
    types_tbl = manifest_data.get("types") if isinstance(manifest_data, dict) else None
    vendors = manifest_data.get("vendors") if isinstance(manifest_data, dict) else None
    reg = vendors.get("registered") if isinstance(vendors, dict) else None
    registered_vendors = frozenset(reg) if isinstance(reg, list) and all(isinstance(x, str) for x in reg) \
        else frozenset()
    enabled_modules = _enabled_modules(manifest_data)

    # --- C-PROFILES: name the evaluated / unevaluated profile scope (spec 16) -------------------------
    rep.ran("C-PROFILES")
    declared_profiles = set()
    prof_tbl = manifest_data.get("profiles") if isinstance(manifest_data, dict) else None
    if isinstance(prof_tbl, dict):
        declared_profiles = {k for k in prof_tbl if isinstance(k, str)}
    unevaluated_profiles = list(mv.unevaluated_profiles)
    evaluated_profiles = sorted(declared_profiles - set(unevaluated_profiles))

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
    active_worklog = _gather_worklog(root_fd, _rel(machine_rel, WORKLOG_NAME), registered_vendors, rep,
                                     required=True) or {}

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
                                                                  registered_vendors, import_status, rep)

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
        high, cfindings = validate_counters(counters_data, known_namespaces=known_ns)
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

    # --- C-STAGING: staged ids under imports/<run-id>/ (spec 14.1), reusing U7's enumerator ------------
    rep.ran("C-STAGING")
    staged_ids = []
    try:
        staged_ids = _opf_import._sibling_ids(root_fd, machine_rel, _opf_import._roster(),
                                              registered_vendors)
    except _opf_import._StageError as exc:
        if exc.verdict == _opf_import.FINDING:
            rep.finding("C-STAGING: {}".format(exc.message))
        else:
            rep.cant("C-STAGING: {}".format(exc.message))

    # --- C-LEASE: the single-writer lease payload, present only while held (spec 5.7) -----------------
    rep.ran("C-LEASE")
    _check_lease(root_fd, machine_rel, rep)

    # --- C-ID-SPACE (R6/H4): store-wide uniqueness (incl. staging) + ids-within-counters --------------
    rep.ran("C-ID-SPACE")
    committed_ids = [r.id for r in active_recs if isinstance(r.id, str)]
    committed_ids += [r.id for r in archive_recs if isinstance(r.id, str)]
    committed_ids += ["WL-{}".format(n) for n in active_worklog]
    for _year, wl_map in archive_worklogs.items():
        committed_ids += ["WL-{}".format(n) for n in wl_map]
    # Uniqueness spans active + archive + staging (spec 11); ids-within-counters is scoped to the committed
    # store, because staging legitimately mints ids above the committed high-water pending promotion
    # (StageResult.new_high_water), so a staged id is never a counter violation.
    for f in check_unique_ids(committed_ids + list(staged_ids)):
        rep.finding("C-ID-SPACE: {}".format(f))
    for f in check_ids_within_counters(committed_ids, high):
        rep.finding("C-ID-SPACE: {}".format(f))

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
    # present ids of a namespace must equal 1..high-water. For WL, C-CONTIGUITY checks the 1..max side and
    # this O(1) arithmetic overhang catches the max..high-water side, reporting the COUNT of allocated ids
    # absent from both locations, never an enumerated list.
    present_wl = sorted(merged)
    wl_hw = high.get("WL")
    if isinstance(wl_hw, int) and not isinstance(wl_hw, bool) and wl_hw >= 0:
        max_present = present_wl[-1] if present_wl else 0
        if wl_hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated worklog id(s) above WL-{} are absent from both the "
                        "active worklog and the archive (counters WL high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(wl_hw - max_present, max_present, wl_hw))
    # Every OTHER roster namespace has no contiguity check, so C-NO-DELETION owns BOTH sides here: an
    # overhang above the max present id AND any gap below it (an id deleted from between). The present set
    # is the committed active + archive records for the namespace (B1); staging is excluded because it
    # legitimately mints ids above the committed high-water pending promotion (not durable). Both scans are
    # O(present) arithmetic, never a range() expansion of the high-water (F15).
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
        nums = sorted(present_by_ns.get(ns, ()))
        max_present = nums[-1] if nums else 0
        if hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated {}-<n> id(s) above {}-{} are absent from both the "
                        "active store and the archive (counters {} high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(hw - max_present, ns, ns, max_present, ns, hw))
        expected = 1
        for n in nums:
            if n != expected:
                rep.finding("C-NO-DELETION: {}-{} is absent from both the active store and the archive (an "
                            "allocated id between 1 and {}-{} cannot be deleted; spec 12/13)".format(
                                ns, expected, ns, max_present))
                break
            expected += 1

    rep.ran("C-PARTITION")
    for f in check_ids_partition(active_wl_ids, archive_wl_ids):
        rep.finding("C-PARTITION: {}".format(f))

    rep.ran("C-CONTIGUITY")
    _check_worklog_contiguous(merged.keys(), rep)

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
                pcanon, _pun = _target_remote_canon(pointer_target)
                if tcanon and pcanon and pcanon.isdisjoint(tcanon):
                    rep.finding("C-SYNC-AGREE: the committed pointer remote {!r} does not agree with the "
                                "manifest sync_target {!r} (spec 5.6)".format(pointer_target.value, sync_target))
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
                    matched = acanon in tcanon
                    if not matched and tgt.kind in ("github", "gitlab"):
                        # GitHub / GitLab resolve the org/repo path case-insensitively, so a case-only
                        # difference is not a disagreement (F5). The host is already lowercased; the
                        # disclosed residual is that a bare git: remote path stays case-sensitive, since an
                        # arbitrary git host may be case-sensitive.
                        matched = (acanon[0], acanon[1].lower()) in {(h, p.lower()) for h, p in tcanon}
                    if not matched:
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
    _check_containment(root_fd, machine_rel, enabled_types, layout, manifest_data, import_status, rep)

    # --- C-VIEW-DRIFT: byte-level drift of every declared deterministic view (spec 5.8/10) ------------
    rep.ran("C-VIEW-DRIFT")
    if isinstance(views_tbl := (manifest_data.get("views") if isinstance(manifest_data, dict) else None),
                  dict) and len(views_tbl) > 0:
        try:
            planned = _opf_views.plan_views(root_fd, machine_rel)
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
    prior_reason = ("the prior committed snapshot observation was supplied but malformed"
                    if obs.get("prior_malformed") else "no prior committed snapshot was supplied")
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

    rep.ran("C-HISTORY-COUNTERS")
    if prior is None:
        rep.cant("C-HISTORY-COUNTERS: {}; counter non-regression (spec 8.2) is not evaluated".format(
            prior_reason))
    elif not counters_ok:
        rep.cant("C-HISTORY-COUNTERS: the current counters did not evaluate; counter non-regression is not "
                 "checkable")
    else:
        for f in check_monotonic(prior["counters_high"], high):
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
    """The type -> NORMATIVE-namespace roster the store must carry: the nine baseline types, each
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

    def base_manifest(layout="inline", types=None, views=None):
        if types is None:
            types = {t: {"namespace": ns} for t, ns in (
                ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
                ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
                ("autonomous_decision", "AD"), ("block", "BL"))}
        m = {
            "devprocess": {"standard": "devprocess", "spec_version": "1.0.0", "layout": layout,
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
        c = {"BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0, "BL": 0, "HO": 1, "RF": 0}
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
                               (".working/toml/imports/leftover", "toml/imports/leftover/junk.bin"),
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
        # F-2: a declared importer legacy_fragment type defers to a named CANNOT-EVALUATE (its record schema
        # ships in U7's import layer, not the baseline validator), never a false INVALID.
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
              _malr is not None and any("not a contained store-relative string" in m
                                        for m in _malr.cannot_evaluate))
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
        # round-14 C5: a partial import substantiated only by a WELL-FORMED run (carrying plan.toml). A
        # run-id-named dir WITHOUT plan.toml does not substantiate, so a stray is graded, not triaged.
        _c5 = clean_machine()
        _c5["manifest.toml"] = base_manifest()
        _c5["manifest.toml"]["devprocess"]["import_status"] = "partial"
        _c5["imports/imp-20260601T000000Z-0123456789abcdef/junk.txt"] = "x"
        _c5r = run(_c5, working=dict([("stray.md", "x")]))
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
        pm["manifest.toml"]["devprocess"]["import_status"] = "partial"
        pm["imports/{}/plan.toml".format(RUNID)] = "schema = 1"
        partial = run(pm, working=dict([("junk.md", "x")]))
        check("partial-import-triage-not-finding",
              partial.status == VALID and any("junk.md" in t for t in partial.triage))
        # codex-2: a STALE partial with no active run does not launder; the stray path grades as a finding
        # and the unsubstantiated declaration is itself flagged -> INVALID.
        stale = clean_machine()
        stale["manifest.toml"] = base_manifest()
        stale["manifest.toml"]["devprocess"]["import_status"] = "partial"
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
        imp1["imports/{}/candidate/evil.bin".format(RUNID0)] = "x"
        check("imports-stray-bytes-none-invalid", run(imp1).status == INVALID)
        imp2 = clean_machine()
        imp2["imports/{}/plan.toml".format(RUNID0)] = "schema = 1"
        check("imports-leftover-run-none-invalid", run(imp2).status == INVALID)

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

        # --- range-bounding: a huge declared high-water and span must NOT expand (F15) ----------------
        f = clean_machine()
        f["counters.toml"] = counters(WL=10 ** 9)
        big_obs = copy.deepcopy(clean_prior())
        big_obs["prior"]["counters_high"]["WL"] = 10 ** 9
        r = run(f, obs=big_obs)   # terminates promptly; the overhang finding carries a COUNT, not a list
        check("f15-huge-high-water-bounded-invalid", r.status == INVALID)
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
        r = run(f, product={"VERSION": "1.0.0\n", "CHANGELOG.md": one_cl2})   # span WL-1..WL-1e9 over 2 present
        check("f15-huge-span-bounded-invalid", r.status == INVALID)

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

        # C-STAGING (enumeration): a staged sibling id that collides with a committed id is only caught
        # because the staging enumerator contributes it to the uniqueness union; removing that enumeration
        # makes the store validate clean.
        stf = clean_machine()
        stf["imports/imp-20260601T000000Z-0123456789abcdef/candidate/backlog_item.index.toml"] = \
            idx([bi(1, "open")])
        stfr = run(stf)
        check("c-staging-collision-not-valid", stfr is not None and stfr.status != VALID)

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

        # --- B4: a FIFO planted as a declared read target fails closed, never an unbounded block ------
        if hasattr(os, "mkfifo"):
            fifo_root = build(clean_machine(), clean_product())
            (fifo_root / "CHANGELOG.md").unlink()
            os.mkfifo(str(fifo_root / "CHANGELOG.md"))
            fr = validate_store(resolve_store(fifo_root), observations=clean_prior())
            check("b4-fifo-target-cannot-eval-not-hang",
                  fr is not None and fr.status == CANNOT_EVALUATE
                  and any("regular file" in m for m in fr.cannot_evaluate))

        # --- B6: a pathologically deep directory chain UNDER A RECURSED namespace (the imports interior)
        # fails closed to CANNOT-EVALUATE, never a RecursionError. A deep chain OUTSIDE a managed namespace
        # is now flagged as an unregistered directory at its first level (round-14), so the ceiling is
        # exercised here via the imports interior, which the walk does descend. -----------
        deep_root = build(clean_machine(), clean_product())
        deep = deep_root / ".working" / "toml" / "imports" / "imp-20260601T000000Z-0123456789abcdef"
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
        check("manifest-unknown-table-invalid", run(f).status == INVALID)
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
    sys.exit(self_test())
