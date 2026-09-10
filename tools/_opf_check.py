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
never a silent empty pass. Time and memory are proportional to the bytes actually read through the contained
readers; NO loop is ever sized by a declared integer (a `WL-10**9` id, a `10**9` high-water, or a
`["WL-1","WL-10**9"]` span never expands), so a declared numeric field cannot amplify the run.

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
    SUPPORTED_SCHEMA,
)
# U3 supplies the ledger validators, the frozen/rotation/partition guards, and the across-time append-only.
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id,
    check_frozen_coverage, check_ids_partition, check_rotation_only_released, _verify_append_only, _wl_num,
)

# Fixed store-tree file / directory names (OPF-SPEC 4.2 layout; all lowercase machine source).
COUNTERS_NAME = "counters.toml"
VERSION_NAME = "version.toml"
WORKLOG_NAME = "worklog.toml"
LEASE_NAME = "lease.toml"                  # present only while the single-writer lease is held (spec 5.7)
ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_NAME = "archive.toml"
INDEX_SUFFIX = ".index.toml"

# Closed keysets for the shapes this unit defines (see the module docstring).
INDEX_TOP_KEYS = frozenset({"schema", "record"})
# The per-record registry row (OPF-SPEC 9:839, 13): id, state, path, digest, all REQUIRED and CLOSED. The
# earlier {id, digest} shape under-read the row; this schema-release unit fixes it (F10).
PERRECORD_ROW_KEYS = frozenset({"id", "state", "path", "digest"})
ARCHIVE_TOP_KEYS = frozenset({"schema", "moved", "worklog_moved"})
ARCHIVE_MOVED_KEYS = frozenset({"id", "type", "destination"})
ARCHIVE_WLMOVED_KEYS = frozenset({"span", "destination"})

# The single-source type -> normative-namespace roster (baseline + every module-tier + importer type), used
# to grade a moved-row `type` against its id namespace (F12) and to bound counters completeness.
_ROSTER_NAMESPACES = dict(BASELINE_TYPES)
for _t, (_ns, _mod) in MODULE_TYPES.items():
    _ROSTER_NAMESPACES[_t] = _ns
_ROSTER_NAMESPACES.update(IMPORTER_TYPES)

# The closed spec-11 integrity roster. Every required check calls rep.ran(<id>) once; result() reconciles
# the emitted set against this tuple, so a silently-skipped check becomes CANNOT-EVALUATE, never VALID.
REQUIRED_CHECKS = (
    "C-MANIFEST", "C-PROFILES", "C-ROSTER", "C-RECORDS", "C-PERRECORD-RECONCILE", "C-ARCHIVE-ENUM",
    "C-VERSION-LEDGER", "C-COUNTERS", "C-ROTATION", "C-STAGING", "C-ID-SPACE", "C-RECEIPTS", "C-HANDOFF",
    "C-DECISION-CHAINS", "C-LINKS", "C-FROZEN-COVERAGE", "C-NO-DELETION", "C-PARTITION", "C-CONTIGUITY",
    "C-TRACKED", "C-SYNC-AGREE", "C-CONTAINMENT", "C-VIEW-DRIFT", "C-VERSION-FILE", "C-CHANGELOG-GATES",
    "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS", "C-HISTORY-RESURRECTION",
)
_REQUIRED_SET = frozenset(REQUIRED_CHECKS)

# Disclosed by-design residuals (OPF-SPEC 17): the sub-checks a parse-only, read-only whole-store engine
# cannot soundly reach from its inputs, or that another layer owns. They are reported alongside every result
# and never fold into the gradeable status. The membership test is that no residual may ever let a broken
# store certify VALID; every stored byte is graded, so none of these can hide a defect in the store's data.
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
    "store state hides behind it, because every stored byte is graded.",
    "Declared-but-unsupported profiles (spec 16): named in unevaluated_profiles, enforced only by a "
    "profile-aware tool.",
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

    def focus(self, check_id):
        # Re-point attribution at an already-run check (one gather pass can feed two checks).
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
    __slots__ = ("id", "rtype", "namespace", "state", "qual", "links", "actor_kind", "location", "scopes")

    def __init__(self, rid, rtype, namespace, state, qual, links, actor_kind, location, scopes):
        self.id = rid                 # the raw id value (a str for a well-formed record)
        self.rtype = rtype            # the record's type name
        self.namespace = namespace    # the two-letter namespace, or None when the id is malformed
        self.state = state            # the parsed status state (or "recorded" for a worklog entry)
        self.qual = qual              # the status qualifier ("proposed") or None
        self.links = links            # list of (rel, target-id) tuples with a string rel
        self.actor_kind = actor_kind  # the actor.kind string, or None
        self.location = location      # "active" or "archive/<YYYY>"
        self.scopes = scopes          # a block's scoped ids (list), or [] (spec 8.5)


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
    return _Rec(rid, rtype, namespace, state, qual, links, akind, location, scopes)


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
    """_list_contained with StoreError mapped to a CANNOT-EVALUATE naming the directory."""
    try:
        return _list_contained(root_fd, reldir)
    except StoreError as exc:
        rep.cant("cannot list {}: {}".format(reldir, exc))
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
    sch = data.get("schema")
    if sch is not None and (type(sch) is not int or sch != SUPPORTED_SCHEMA):
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


def _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, where, registered_vendors, rep, recon):
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
    """A non-worklog archived record may rotate only from an UNQUALIFIED TERMINAL state (OPF-SPEC 12:972).
    An open record, an active block, an unresolved decision, or a current handoff (each non-terminal or
    proposal-qualified) must never rotate (OPF-SPEC 12:978)."""
    spec = BASELINE_SPECS.get(rec.rtype)
    if spec is None:
        return False
    return rec.state in spec.terminal and rec.qual is None


def _validate_archive(root_fd, machine_rel, registered_vendors, rep):
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
    archive_rel = _rel(machine_rel, ARCHIVE_DIRNAME)
    years, _files = _list_dir(root_fd, archive_rel, rep)
    if years is None:
        return archive_recs, archive_worklogs, rotatable   # no archive/ tree: no rotation has happened (clean)
    for year in years:
        bucket = _rel(archive_rel, year)
        if not (year.isdigit() and len(year) == 4):
            rep.finding("C-ARCHIVE-ENUM: archive bucket {!r} is not a <YYYY> calendar-year directory "
                        "(spec 12)".format(year))
        _sub, bfiles = _list_dir(root_fd, bucket, rep)
        if bfiles is None:
            rep.cant("archive bucket {} could not be listed".format(bucket))
            continue
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
            idx_rel = _rel(bucket, fn_)
            data, st = _read_toml(root_fd, idx_rel, rep)
            if st != "ok":
                continue
            rows = _index_rows(data, idx_rel, rep)
            if rows is None:
                continue
            for i, row in enumerate(rows):
                rw = "{}#{}".format(idx_rel, i + 1)
                if not isinstance(row, dict):
                    rep.cant("{}: archived record row is not a table".format(rw))
                    continue
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
                    if not _archived_rotatable(rec):
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
            if dest.rsplit("/", 1)[-1] != want_base:
                rep.finding("C-ARCHIVE-ENUM: moved id {!r} destination {!r} basename is not {!r} (a moved "
                            "record lands in its type index; spec 12)".format(mid, dest, want_base))
            if not _dest_index_has_id(root_fd, machine_rel, dest, mid, rep):
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
        for span, dest in wl_moved:
            if not _is_contained_relpath(dest):
                rep.finding("C-ARCHIVE-ENUM: worklog_moved destination {!r} in {} is not a contained "
                            "store-relative path (spec 12); the destination is never opened".format(dest, bucket))
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


def _check_resurrection(prior_records, by_id, all_ids, rep):
    """C-HISTORY-RESURRECTION: for every id in the prior committed snapshot, confirm it did not resurrect
    across time (OPF-SPEC 8.4/13). A prior unqualified-terminal state now in any other state is a definite
    finding (actor-independent). A non-terminal change is swept across the four actor kinds: VALID for some
    actor passes; INVALID for every actor is a finding; a rejection-shaped transition needing a
    pre-proposal state the snapshot cannot supply is CANNOT-EVALUATE for that transition (never a permissive
    pass, never a fabricated actor). Every durable prior id must remain present in active or archive (the
    across-time complement of the no-deletion check)."""
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
        cur_status = _status_string(cur.state, cur.qual)
        if cur_status is None or cur_status == pstatus:
            continue     # an unparseable current status is the schema check's; an unchanged status is clean
        if p_state in spec.terminal and p_qual is None:
            rep.finding("C-HISTORY-RESURRECTION: prior record {!r} was at unqualified terminal {!r} but is "
                        "now {!r}; a terminal record does not change (no resurrection; spec 8.4)".format(
                            rid, pstatus, cur_status))
            continue
        results = [validate_transition(ptype, pstatus, cur_status, ak) for ak in ACTOR_KINDS]
        if any(rv.status == VALID for rv in results):
            continue
        if any(rv.status == CANNOT_EVALUATE for rv in results):
            rep.cant("C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} cannot be verified "
                     "without the pre-proposal state (spec 8.4)".format(rid, pstatus, cur_status))
            continue
        rep.finding("C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} is illegal for every "
                    "actor kind (spec 8.4/8.5)".format(rid, pstatus, cur_status))


# --- containment (C-CONTAINMENT, OPF-SPEC 14.2) ------------------------------------------------------

def _check_containment(root_fd, machine_rel, enabled_types, layout, manifest_data, import_status, rep):
    """C-CONTAINMENT: recursively walk `.working/`, matching every regular file against the managed set
    (the ledgers, the enabled type indexes, per-record bodies, the archive tree, valid imports run
    subtrees, declared store-scope view targets, and declared [unmanaged] paths). A path in neither set is
    a finding at steady state; at `import_status = "partial"` it goes to `triage` instead (spec 11/14.2). An
    unmanaged declaration that collides with a managed path is a finding. Listing failure is
    CANNOT-EVALUATE."""
    mrel = machine_rel
    view_targets = set()
    views = manifest_data.get("views") if isinstance(manifest_data, dict) else None
    if isinstance(views, dict):
        for name in views:
            if isinstance(name, str):
                scope, dest = _opf_views._spec_destination(name)
                if scope == "store":
                    view_targets.add(dest)
    unmanaged = []
    um = manifest_data.get("unmanaged") if isinstance(manifest_data, dict) else None
    if isinstance(um, dict) and isinstance(um.get("paths"), list):
        unmanaged = [p for p in um["paths"] if isinstance(p, str) and _is_contained_relpath(p)]
    ledger_names = frozenset({MANIFEST_NAME, COUNTERS_NAME, VERSION_NAME, WORKLOG_NAME, LEASE_NAME})
    archive_root = _rel(mrel, ARCHIVE_DIRNAME)
    imports_root = _rel(mrel, _opf_import.IMPORTS_DIRNAME)

    def managed_file(p):
        if p in view_targets or _under_any(p, unmanaged):
            return True
        prefix = mrel + "/"
        if p.startswith(prefix):
            r = p[len(prefix):]
            if "/" not in r:
                if r in ledger_names:
                    return True
                if r.endswith(INDEX_SUFFIX) and r[:-len(INDEX_SUFFIX)] in enabled_types:
                    return True
            elif layout == "per-record":
                head, tail = r.split("/", 1)
                if "/" not in tail and tail.endswith(".toml") and head in enabled_types \
                        and _valid_id_shape(tail[:-len(".toml")]) is not None:
                    return True
        return False

    # An unmanaged declaration that equals or nests with a managed path is a finding (spec 14.2).
    managed_dir_prefixes = (mrel, archive_root, imports_root)
    for u in unmanaged:
        if managed_file(u) or u in managed_dir_prefixes or _under_any(u, managed_dir_prefixes):
            rep.finding("C-CONTAINMENT: unmanaged path {!r} collides with a managed store path (an "
                        "unmanaged declaration cannot cover a managed file; spec 14.2)".format(u))

    unmanaged_files = []

    def walk(reldir):
        subdirs, files = _list_dir(root_fd, reldir, rep)
        if subdirs is None and files is None:
            return
        for f in files:
            full = reldir + "/" + f
            if not managed_file(full):
                unmanaged_files.append(full)
        for d in subdirs:
            full = reldir + "/" + d
            if _under_any(full, unmanaged) or full == archive_root:
                continue     # a declared-unmanaged subtree is never read; the archive is C-ARCHIVE-ENUM's
            if reldir == imports_root:
                # a valid imports run subtree is U7's (managed); a non-matching entry is unmanaged
                if not _opf_import._RUN_ID_RE.match(d):
                    unmanaged_files.append(full)
                continue
            walk(full)

    walk(WORKING_DIRNAME)
    for p in sorted(unmanaged_files):
        if import_status == "partial":
            rep.triage_path("unregistered path {!r} at the store location (an import or migration is in "
                            "progress; triage per spec 14.2)".format(p))
        else:
            rep.finding("C-CONTAINMENT: unregistered path {!r} at the store location is neither OPF-managed "
                        "nor enumerated as unmanaged (spec 14.2/11)".format(p))


# --- observations (the inert git-derived facts the caller injects) -----------------------------------

def _normalize_prior(prior):
    """Validate the injected prior committed snapshot: {releases: list, counters_high: {ns: int},
    records: {id: (type, status)}}. Returns the normalized dict or None when any field is malformed (which
    the caller routes to CANNOT-EVALUATE, never a permissive pass)."""
    if not isinstance(prior, dict):
        return None
    releases = prior.get("releases")
    counters_high = prior.get("counters_high")
    records = prior.get("records")
    if not isinstance(releases, list) or not isinstance(counters_high, dict) or not isinstance(records, dict):
        return None
    norm_records = {}
    for rid, val in records.items():
        if not isinstance(rid, str):
            return None
        if not (isinstance(val, (list, tuple)) and len(val) == 2
                and isinstance(val[0], str) and isinstance(val[1], str)):
            return None
        norm_records[rid] = (val[0], val[1])
    return {"releases": releases, "counters_high": counters_high, "records": norm_records}


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
    obs = {}
    tracked = observations.get("tracked")
    if isinstance(tracked, str) and tracked in ("tracked", "untracked"):
        obs["tracked"] = tracked
    actual_remote = observations.get("actual_remote")
    if isinstance(actual_remote, str):
        obs["actual_remote"] = actual_remote
    prior = observations.get("prior")
    if prior is not None:
        norm = _normalize_prior(prior)
        if norm is not None:
            obs["prior"] = norm
        # a malformed prior is dropped; the history checks route to cannot-evaluate naming it
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
    evaluated_profiles, unevaluated_profiles = [], []
    try:
        evaluated_profiles, unevaluated_profiles = _validate_opened_store(
            root_fd, product_root_fd, machine_rel, supported_profiles, obs, rep)
    finally:
        os.close(root_fd)
        if product_root_fd is not None:
            os.close(product_root_fd)
    return rep.result(evaluated_profiles, unevaluated_profiles)


def _validate_opened_store(root_fd, product_root_fd, machine_rel, supported_profiles, obs, rep):
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
    archive_recs, archive_worklogs, rotatable = _validate_archive(root_fd, machine_rel, registered_vendors,
                                                                  rep)

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
    # present WL ids must equal 1..high-water. Contiguity checks the 1..max side; this O(1) arithmetic
    # overhang catches the max..high-water side, reporting the COUNT of allocated ids absent from both
    # locations, never an enumerated list.
    present_wl = sorted(merged)
    wl_hw = high.get("WL")
    if isinstance(wl_hw, int) and not isinstance(wl_hw, bool) and wl_hw >= 0:
        max_present = present_wl[-1] if present_wl else 0
        if wl_hw > max_present:
            rep.finding("C-NO-DELETION: {} allocated worklog id(s) above WL-{} are absent from both the "
                        "active worklog and the archive (counters WL high-water is {}; nothing is ever "
                        "deleted, spec 12/13)".format(wl_hw - max_present, max_present, wl_hw))

    rep.ran("C-PARTITION")
    for f in check_ids_partition(active_wl_ids, archive_wl_ids):
        rep.finding("C-PARTITION: {}".format(f))

    rep.ran("C-CONTIGUITY")
    _check_worklog_contiguous(merged.keys(), rep)

    # --- C-TRACKED / C-SYNC-AGREE: topology, from the injected observations (spec 5.1/5.6) ------------
    rep.ran("C-TRACKED")
    tracked = obs.get("tracked")
    if tracked is None:
        rep.cant("C-TRACKED: no `tracked` observation supplied; the tracked-store requirement (spec 5.1) "
                 "is not evaluable by a parse-only engine")
    elif tracked == "untracked":
        rep.finding("C-TRACKED: the resolved store is not under version control (spec 5.1)")

    rep.ran("C-SYNC-AGREE")
    store_tbl = manifest_data.get("store") if isinstance(manifest_data, dict) else None
    sync_target = store_tbl.get("sync_target") if isinstance(store_tbl, dict) else ""
    if sync_target is None:
        sync_target = ""
    if not isinstance(sync_target, str):
        rep.cant("C-SYNC-AGREE: [store].sync_target is not a string")
    elif sync_target == "":
        pass     # no dedicated target (in-repo / local-only durability residual disclosed; spec 5.7/17)
    else:
        try:
            classify_target(sync_target)
        except StoreError as exc:
            rep.finding("C-SYNC-AGREE: [store].sync_target {!r} does not classify as a target (spec "
                        "5.5): {}".format(sync_target, exc))
        else:
            actual = obs.get("actual_remote")
            if actual is None:
                rep.cant("C-SYNC-AGREE: no `actual_remote` observation supplied; the sync-target / remote "
                         "agreement (spec 5.6) is not evaluable by a parse-only engine")
            elif actual.strip() != sync_target.strip():
                rep.finding("C-SYNC-AGREE: the manifest sync_target {!r} does not match the store "
                            "repository's actual remote {!r} (spec 5.6)".format(sync_target, actual))

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
        if releases:     # with no release there is no VERSION to render, and none is required
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
    rep.ran("C-HISTORY-APPEND-ONLY")
    if prior is None:
        rep.cant("C-HISTORY-APPEND-ONLY: no prior committed snapshot supplied; across-time release "
                 "immutability (spec 6.1/13) is not evaluated")
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
        rep.cant("C-HISTORY-COUNTERS: no prior committed snapshot supplied; counter non-regression (spec "
                 "8.2) is not evaluated")
    elif not counters_ok:
        rep.cant("C-HISTORY-COUNTERS: the current counters did not evaluate; counter non-regression is not "
                 "checkable")
    else:
        for f in check_monotonic(prior["counters_high"], high):
            rep.finding("C-HISTORY-COUNTERS: {}".format(f))

    rep.ran("C-HISTORY-RESURRECTION")
    if prior is None:
        rep.cant("C-HISTORY-RESURRECTION: no prior committed snapshot supplied; no-resurrection across time "
                 "(spec 8.4) is not evaluated")
    else:
        _check_resurrection(prior["records"], by_id, all_ids, rep)

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
    validates VALID; removing the enforcing branch flips its vector back to VALID or drops its check id,
    which the registry reconciliation then fails. No real git is used: validate_store reads no git, and the
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
                                      "DN-1": ("done", "recorded"), "HO-1": ("handoff", "current")}}}

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
        pm = clean_machine()
        pm["manifest.toml"] = base_manifest()
        pm["manifest.toml"]["devprocess"]["import_status"] = "partial"
        partial = run(pm, working={"junk.md": "x\n"})
        check("partial-import-triage-not-finding",
              partial.status == VALID and any("junk.md" in t for t in partial.triage))
        um = clean_machine()
        um["manifest.toml"] = base_manifest()
        um["manifest.toml"]["unmanaged"] = {"paths": [".working/toml/manifest.toml"]}
        check("unmanaged-collision-invalid", run(um).status == INVALID)
        sm = clean_machine()
        sm["manifest.toml"] = base_manifest()
        sm["manifest.toml"]["store"] = {"sync_target": "git:git@example.com:store.git"}
        check("sync-mismatch-invalid",
              run(sm, obs={"tracked": "tracked", "actual_remote": "git:git@example.com:OTHER.git",
                           "prior": clean_prior()["prior"]}).status == INVALID)
        check("sync-no-remote-cannot-eval",
              run(sm, obs={"tracked": "tracked", "prior": clean_prior()["prior"]}).status == CANNOT_EVALUATE)

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
