#!/usr/bin/env python3
"""OPF (DevProcess) store-level integrity validator: the reusable `opf doctor` engine (OPF core-tooling U6).

Offline, stdlib only, fail-closed. U2 (`_opf_schema`) validates ONE record at a time and U3
(`_opf_release`) validates ONE ledger at a time; neither can see the whole store, so the class of
spec-MANDATED cross-record, historical, and archive invariants the integrity layer owns (OPF-SPEC 11,
lines 931-939) has no owner. THIS unit builds that whole-store layer as a reusable ENGINE,
`validate_store`, that REUSES the U1/U2/U3 primitives and never re-implements per-record or per-ledger
logic. It is the engine the future `opf doctor` VERB will drive; the verb itself is deliberately NOT
wired here (see the sequencing note below).

Every declared input is read ONLY through U1's contained (dir-fd, no-follow) readers, so a swapped
symlink is refused rather than followed off-tree, matching U1's discipline; no path is opened directly.
FAIL CLOSED EVERYWHERE (OPF-SPEC 3, 11:939): any unreadable, unparseable, exotic, or malformed declared
input is a CANNOT-EVALUATE outcome that NAMES the input, never a silent empty pass, never "no rotation
happened", never "clean". A clean, fully-readable store validates VALID; a store with a definite defect
is INVALID; a store the engine cannot soundly read is CANNOT-EVALUATE (the worst outcome dominates, so a
cannot-evaluate is never masked by an otherwise-clean read).

CRITICAL SEQUENCING (honoured by this unit): the live `doctor` verb is NOT CLI-wired here. A prerequisite
hardening step (VC-4-HARDEN) has not landed, and wiring a live store verb before it is a known finding
(PD-OPF-PASSA-DEPTH option B, F-373). U6 therefore lands as (1) this module plus (2) its self-test wired
into the `opf-check` self-test leg ONLY, exactly as the earlier units did. The `doctor` verb stays in
`opf.py`'s fail-closed `KNOWN_VERBS` path (NOT-YET-IMPLEMENTED, exit 2); no verb routes to
`validate_store`. Assurance rides the `--self-test` leg over synthetic whole stores.

What `validate_store` OWNS (whole-store) and REUSES (per record/ledger), all fail-closed:

  Per record / ledger, DELEGATED (never re-implemented here):
  - `_opf_schema.validate_record` on every active AND archived record (archived worklog entries get full
    worklog-schema validation through `_opf_release.validate_worklog`, which calls `validate_record`).
  - `_opf_release.validate_version` / `validate_worklog` on the ledgers.

  Whole-store, OWNED here:
  - T1 store-topology (OPF-SPEC 11): the tracked-store requirement, pointer/sync-target/remote agreement,
    and whole-tree path/unmanaged-path containment need git and remote inspection beyond the TOML reads
    available to a pure-TOML engine. Each such sub-check is DISCLOSED as a named residual rather than
    silently passed; the manifest's own declared `[unmanaged].paths` containment is validated via
    `validate_manifest`. (See `_TOPOLOGY_RESIDUALS`.)
  - T2 cross-record: R1 done<->backlog_item one-to-one receipts; R2 at most one `current` handoff; R3 one
    current effective resolution per `pending_decision` supersession chain; R4 link-target resolution and
    type; R5 bidirectional index reconciliation (per-record digest match); R6 store-wide id uniqueness,
    ids-within-counters, and counter schema, reusing `_opf_schema.check_unique_ids`,
    `check_ids_within_counters`, `validate_counters`.
  - T3 historical: H4 within-snapshot ids-within-high-water and uniqueness (via R6); the across-time
    counter non-regression and the H1 release-row append-only-across-time both need the prior committed
    state (git history) a pure-TOML engine does not read, so both are DISCLOSED as residuals.
  - T4 frozen-span / archive / no-deletion, computed over active + archive TOGETHER (OPF-SPEC 12:982-983):
    F1 frozen coverage over the MERGED worklog (reuses `check_frozen_coverage`); F2 no deletion (reuses
    `check_no_deletion`); F3 exactly-one-location partition (reuses `check_ids_partition`); F4 rotation of
    released/terminal records only (reuses `check_rotation_only_released` for the worklog, and a terminal-
    state check for non-worklog archived records); F5 bidirectional archive-enumeration reconciliation
    against each bucket (built here, no prior owner); F6 span tiling across the merged worklog.

Boundary with U5 (compose, do not re-derive): changelog range-coverage and changelog freeze belong to U5.
`validate_store` COMPOSES U5's result when U5's entry point lands; U5 is not present in the tree, so this
is a named composition seam (a disclosed residual), never re-derived here. The active+archive worklog
merge that spec-12 coverage requires (F1) DOES happen here, because this is the layer that has the archive
(the tracked F-374 M2 obligation U5 deferred).

Reference-tooling / spec shapes DEFINED here where the spec leaves a gap (each fail-closed, named so the
choice is reviewable, per disclose-guard-residuals). The spec's own layout is illustrative; this unit is
the schema release that follows it for the store-level shapes it must parse:
  - `<type>.index.toml` file shape: an optional top-level `schema` int plus a `[[record]]` array. In the
    `inline` layout each `[[record]]` IS the full record table (validated by `validate_record`). In the
    `per-record` layout each `[[record]]` is a registry row `{id, digest}` and the record body lives at
    `<type>/<id>.toml` as a top-level record table; the row `digest` is `sha256:` + the SHA-256 of U3's
    canonical serialization of the body, so a silent edit to a record file breaks the recorded digest
    (OPF-SPEC 13:999-1001). Top keys `{schema, record}` and per-record-row keys `{id, digest}` are CLOSED.
  - `archive/<YYYY>/archive.toml` shape (this design defines it): `schema = 1`, a `[[moved]]` array of
    `{id, type, destination}` for moved non-worklog records, and a `[[worklog_moved]]` array of
    `{span, destination}` for moved released worklog spans. Top keys `{schema, moved, worklog_moved}` and
    the per-row keysets are CLOSED. An unreadable/unparseable `archive.toml`, or a present archive bucket
    with NO `archive.toml`, is CANNOT-EVALUATE, never "no rotation happened".
  - Staging uniqueness under `imports/<run-id>/` (OPF-SPEC 14.1) is OUT OF SCOPE for this unit (the
    importer lands later); R6 is scoped to active + archive and the staging case is disclosed as deferred.
"""
import hashlib
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained no-follow parent-open primitive (reused for listing)
# U1 supplies the outcome model, the contained readers, the manifest validator, and the message helpers.
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, RESOLVED, MANIFEST_NAME, StoreError,
    _read_toml_contained, _open_store_root_fd, validate_manifest,
    _sorted_key_names, _safe_display, _is_contained_relpath,
)
# U2 supplies the record validator, the counter guards, the status parser, and the type specs.
from _opf_schema import (  # noqa: E402
    validate_record, validate_counters, check_unique_ids, check_ids_within_counters,
    parse_status, BASELINE_SPECS, _valid_id_shape, SUPPORTED_SCHEMA,
)
# U3 supplies the ledger validators, the frozen/rotation/partition guards, and the canonical serializer
# (reused for the per-record digest). Reuse, never re-declare.
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id,
    check_frozen_coverage, check_no_deletion, check_ids_partition, check_rotation_only_released,
    _canonical, ReleaseError, _wl_num,
)

# Fixed store-tree file / directory names (OPF-SPEC 4.2 layout; all lowercase machine source).
COUNTERS_NAME = "counters.toml"
VERSION_NAME = "version.toml"
WORKLOG_NAME = "worklog.toml"
ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_NAME = "archive.toml"
INDEX_SUFFIX = ".index.toml"

# Closed keysets for the shapes this unit defines (see the module docstring).
INDEX_TOP_KEYS = frozenset({"schema", "record"})
PERRECORD_ROW_KEYS = frozenset({"id", "digest"})
ARCHIVE_TOP_KEYS = frozenset({"schema", "moved", "worklog_moved"})
ARCHIVE_MOVED_KEYS = frozenset({"id", "type", "destination"})
ARCHIVE_WLMOVED_KEYS = frozenset({"span", "destination"})

# The link relations whose target must resolve to an existing record of the correct type (OPF-SPEC 8.6;
# `follows`/`relates` are advisory and not resolved here). receipt_of targets a backlog_item; supersedes
# targets the same type; resolves/remediates/corrects need only an existing target.
RESOLVED_LINK_RELS = frozenset({"supersedes", "receipt_of", "resolves", "remediates", "corrects"})

# Disclosed by-design residuals (named, never silently passed). These are the sub-checks a pure-TOML
# whole-store engine cannot soundly evaluate from its inputs, or that another layer owns; they are
# reported alongside every result and never fold into the gradeable status (or a clean store could never
# validate VALID). Fail-closed disclosure per disclose-guard-residuals.
_TOPOLOGY_RESIDUALS = (
    "T1 tracked-store requirement (OPF-SPEC 5.1/11): needs version-control inspection beyond the TOML "
    "reads available to this engine; deferred to a git-aware doctor step and disclosed, not passed.",
    "T1 pointer / manifest sync-target / actual-remote agreement (OPF-SPEC 5.6/11): needs the store "
    "repository's real remote; deferred to a git-aware doctor step (disclosed residual).",
    "T1 phased unmanaged-path containment and whole-tree path containment (OPF-SPEC 14.2/11): need a "
    "store-location scan against the managed-file set; the manifest's declared [unmanaged].paths "
    "containment is validated via validate_manifest, the phased detection is deferred (disclosed).",
)
_HISTORY_RESIDUALS = (
    "H1 release-row append-only across time (OPF-SPEC 6.1): needs the prior committed ledger (git "
    "history); this pure-TOML engine reads no history. Left to U3's in-cut _verify_append_only and a "
    "future history-aware doctor step (disclosed residual).",
    "H4 counter non-regression across time (OPF-SPEC 8.2): likewise needs the prior committed "
    "counters.toml; the within-snapshot ids-within-high-water and uniqueness checks run here (R6), the "
    "across-time non-regression is deferred (disclosed residual).",
)
_SEAM_RESIDUALS = (
    "U5 changelog range-coverage and changelog freeze (OPF-SPEC 7.1/7.2) are U5's; validate_store "
    "composes U5's result when its entry point lands. U5 is not present in the tree, so this is a named "
    "composition seam, not re-derived here (disclosed residual).",
    "Staging uniqueness under imports/<run-id>/ (OPF-SPEC 14.1) is out of scope for this unit; R6 is "
    "scoped to active+archive. Deferred to the importer unit (disclosed residual).",
    "Manifest-registered additional worklog kinds (OPF-SPEC 6.2) have no slot in U1's manifest schema "
    "yet, so worklog validation here uses the built-in kinds; disclosed until the schema carries them.",
)


class StoreValidation:
    """The whole-store outcome (the U1 ManifestValidation idiom, one level up). `status` reduces to a
    0/1/2 exit code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE); CANNOT-EVALUATE dominates INVALID, which
    dominates VALID, so an unreadable input is never masked by an otherwise-clean read. `residuals` are
    the disclosed by-design uncovered sub-checks; they are reported but never change the status."""
    __slots__ = ("status", "findings", "cannot_evaluate", "residuals")

    def __init__(self, status, findings=None, cannot_evaluate=None, residuals=None):
        self.status = status
        self.findings = findings or []
        self.cannot_evaluate = cannot_evaluate or []
        self.residuals = residuals or []


def exit_code(result):
    """Reduce a StoreValidation to a 0/1/2 exit code for the future doctor verb."""
    if result.status == CANNOT_EVALUATE:
        return 2
    if result.status == INVALID:
        return 1
    return 0


class _Report:
    """A fail-closed accumulator: definite defects go to `findings` (INVALID), unreadable/unparseable
    declared inputs go to `cannot` (CANNOT-EVALUATE, which dominates), disclosed residuals are reported
    but never graded."""
    __slots__ = ("findings", "cannot", "residuals")

    def __init__(self):
        self.findings = []
        self.cannot = []
        self.residuals = list(_TOPOLOGY_RESIDUALS) + list(_HISTORY_RESIDUALS) + list(_SEAM_RESIDUALS)

    def finding(self, msg):
        self.findings.append(msg)

    def cant(self, msg):
        self.cannot.append(msg)

    def result(self):
        if self.cannot:
            status = CANNOT_EVALUATE
        elif self.findings:
            status = INVALID
        else:
            status = VALID
        return StoreValidation(status, self.findings, self.cannot, self.residuals)


# --- a lightweight record descriptor for the cross-record checks -------------------------------------

class _Rec:
    __slots__ = ("id", "rtype", "namespace", "state", "qual", "links", "actor_kind", "location")

    def __init__(self, rid, rtype, namespace, state, qual, links, actor_kind, location):
        self.id = rid                 # the raw id value (a str for a well-formed record)
        self.rtype = rtype            # the record's type name
        self.namespace = namespace    # the two-letter namespace, or None when the id is malformed
        self.state = state            # the parsed status state (or "recorded" for a worklog entry)
        self.qual = qual              # the status qualifier ("proposed") or None
        self.links = links            # list of (rel, target-id) tuples with a string rel
        self.actor_kind = actor_kind  # the actor.kind string, or None
        self.location = location      # "active" or "archive/<YYYY>"


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
    return _Rec(rid, rtype, namespace, state, qual, links, akind, location)


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
    """The per-record digest `sha256:<hex>` over U3's canonical serialization of a record body (the digest
    the per-record registry row carries; OPF-SPEC 13). Raises ReleaseError on an uncanonicalizable body,
    which the caller maps to CANNOT-EVALUATE, never a silent mismatch."""
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


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
    unidentifiable row, recorded CANNOT-EVALUATE."""
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


def _gather_perrecord(root_fd, machine_rel, tname, namespace, rows, where, registered_vendors, rep):
    """Gather the per-record-layout records of one type: each registry row is `{id, digest}`, its body
    lives at `<type>/<id>.toml`, and the row digest must match the body (OPF-SPEC 13). Reconciliation is
    BIDIRECTIONAL: a registry row whose body file is absent is an INVALID finding (a row with no record),
    and a record file under `<type>/` with no registry row is an INVALID finding (a record with no row)."""
    recs = []
    seen_ids = set()
    for i, row in enumerate(rows):
        rw = "{}#{}".format(where, i + 1)
        if not isinstance(row, dict):
            rep.cant("{}: registry row is not a table".format(rw))
            continue
        extra = set(row) - PERRECORD_ROW_KEYS
        if extra:
            rep.finding("{}: unknown registry-row key(s): {}".format(rw, ", ".join(_sorted_key_names(extra))))
        rid = row.get("id")
        shape = _valid_id_shape(rid)
        if shape is None or shape[0] != namespace:
            rep.finding("{}: registry row id {} is not a well-formed {}-<n> id (spec 8.2)".format(
                rw, _safe_display(rid), namespace))
            continue
        seen_ids.add(rid)
        body_rel = _rel(machine_rel, tname, "{}.toml".format(rid))
        body, bst = _read_toml(root_fd, body_rel, rep)
        if bst == "error":
            continue
        if bst == "absent":
            rep.finding("{}: registry row {!r} resolves to no record file {} (an index row with no "
                        "record; spec 13:999-1001)".format(rw, rid, body_rel))
            continue
        if not isinstance(body, dict):
            rep.cant("{}: record file {} is not a table".format(rw, body_rel))
            continue
        rv = validate_record(body, expected_type=tname, registered_vendors=registered_vendors)
        if rv.status == CANNOT_EVALUATE:
            rep.cant("{}: {}".format(body_rel, "; ".join(rv.findings)))
        elif rv.status != VALID:
            for f in rv.findings:
                rep.finding("{}: {}".format(body_rel, f))
        try:
            want = _record_digest(body)
        except ReleaseError as exc:
            rep.cant("{}: cannot compute the per-record digest ({})".format(body_rel, exc))
            want = None
        if want is not None and row.get("digest") != want:
            rep.finding("{}: per-record digest mismatch for {!r} (the index row and the record file "
                        "disagree; spec 13:999-1001)".format(rw, rid))
        recs.append(_make_rec(body, tname, "active"))
    # Reverse direction: every record file under <type>/ must have a registry row.
    _subdirs, files = _list_dir(root_fd, _rel(machine_rel, tname), rep)
    if files is not None:
        for fn in files:
            if not fn.endswith(".toml"):
                continue
            fid = fn[:-len(".toml")]
            if fid not in seen_ids:
                rep.finding("{}/{}: record file {!r} has no registry row in {} (a record with no index "
                            "row; spec 13:999-1001)".format(tname, fn, fid, where))
    return recs


def _gather_active_records(root_fd, machine_rel, enabled_types, layout, registered_vendors, rep):
    """Gather every active non-worklog record across the enabled type indexes. A declared enabled type
    with no `<type>.index.toml` is an INVALID finding (a declared input is absent; OPF-SPEC 11:939)."""
    recs = []
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
                                          registered_vendors, rep))
        else:
            recs.extend(_gather_inline(tname, rows, idx_rel, registered_vendors, rep))
    return recs


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


# --- the archive (F4/F5 and the merged worklog) ------------------------------------------------------

def _parse_archive_manifest(data, where, rep):
    """Parse and validate an `archive.toml` (shape defined here). Returns (moved, worklog_moved) lists of
    validated rows, or (None, None) when the file is not a table, carries an unsupported/absent schema, or
    an array is malformed (each CANNOT-EVALUATE). A malformed individual row is an INVALID finding."""
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
        mid, dest = m.get("id"), m.get("destination")
        if _valid_id_shape(mid) is None:
            rep.finding("{}: id {} is not a well-formed <NS>-<n> id (spec 8.2)".format(rw, _safe_display(mid)))
            continue
        if not isinstance(dest, str) or not dest:
            rep.finding("{}: destination must be a non-empty string".format(rw))
            continue
        moved.append((mid, dest))
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
        rep.finding("archive destination {!r} is not a contained store-relative path (spec 14.2)".format(dest))
        return False
    full = _rel(machine_rel, dest)
    data, st = _read_toml(root_fd, full, rep)
    if st == "error":
        return False
    if st == "absent":
        rep.finding("archive destination {!r} does not exist".format(dest))
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
    (F4/F5, OPF-SPEC 12/13). Returns (archive_recs, archive_worklogs) where archive_worklogs maps a year
    to its WL-number -> entry map. Archive absent means no rotation (clean); a present bucket with no
    archive.toml is CANNOT-EVALUATE, never 'no rotation happened'."""
    archive_recs = []
    archive_worklogs = {}
    archive_rel = _rel(machine_rel, ARCHIVE_DIRNAME)
    years, _files = _list_dir(root_fd, archive_rel, rep)
    if years is None:
        return archive_recs, archive_worklogs      # no archive/ tree: no rotation has happened (clean)
    for year in years:
        bucket = _rel(archive_rel, year)
        if not (year.isdigit() and len(year) == 4):
            rep.finding("F5: archive bucket {!r} is not a <YYYY> calendar-year directory (spec 12)".format(year))
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
        enumerated_ids = {mid for mid, _dest in moved}

        # Archived non-worklog records actually present in this bucket's <type>.index.toml files.
        present_ids = set()
        for fn in bfiles:
            if fn in (ARCHIVE_MANIFEST_NAME, WORKLOG_NAME):
                continue
            if not fn.endswith(INDEX_SUFFIX):
                rep.finding("F5: unexpected file {!r} in archive bucket {} (spec 12)".format(fn, bucket))
                continue
            tname = fn[:-len(INDEX_SUFFIX)]
            idx_rel = _rel(bucket, fn)
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
                    present_ids.add(rec.id)
                    if not _archived_rotatable(rec):
                        rep.finding("F4: archived record {!r} ({}) is not in an unqualified terminal state "
                                    "and must never rotate (spec 12:972-979)".format(rec.id, tname))
        # Archived worklog (release-based rotation).
        wl_map = {}
        if WORKLOG_NAME in bfiles:
            wl_map = _gather_worklog(root_fd, _rel(bucket, WORKLOG_NAME), registered_vendors, rep,
                                     required=False) or {}
        archive_worklogs[year] = wl_map

        # F5 non-worklog reconciliation, BOTH directions.
        for mid, dest in moved:
            if not _dest_index_has_id(root_fd, machine_rel, dest, mid, rep):
                rep.finding("F5: enumerated moved id {!r} is absent from its destination {!r} (a rotation "
                            "must land where it says; spec 12:981-982)".format(mid, dest))
        for rid in sorted(present_ids):
            if rid not in enumerated_ids:
                rep.finding("F5: archived record {!r} in {} is not enumerated in archive.toml (a record "
                            "cannot quietly vanish under the name of rotation; spec 12:981-982)".format(
                                rid, bucket))

        # F5 worklog-span reconciliation, BOTH directions.
        span_ids = set()
        for span, dest in wl_moved:
            a, b = _wl_num(span[0]), _wl_num(span[1])
            if a is None or b is None or a > b:
                rep.finding("F5: worklog_moved span {} in {} is not a well-formed [WL-a, WL-b] range "
                            "(spec 8.2/12)".format(_safe_display(span), bucket))
                continue
            for n in range(a, b + 1):
                span_ids.add(n)
                if n not in wl_map:
                    rep.finding("F5: worklog_moved span enumerates WL-{} but it is absent from {!r} "
                                "(spec 12:981-982)".format(n, dest))
        for n in sorted(wl_map):
            if n not in span_ids:
                rep.finding("F5: archived worklog WL-{} in {} is not enumerated in a worklog_moved span "
                            "(a record cannot vanish under rotation; spec 12:981-982)".format(n, bucket))
    return archive_recs, archive_worklogs


# --- the cross-record checks (T2) --------------------------------------------------------------------

def _check_r1_receipts(recs, by_id, rep):
    """R1: the done<->backlog_item receipt is one-to-one in BOTH directions (OPF-SPEC 8.5:698). Every
    ratified backlog_item (unqualified `done`) has exactly one done receipt; every done's receipt_of
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
                rep.finding("R1: ratified backlog_item {!r} (at 'done') has no done receipt (the receipt "
                            "is one-to-one; spec 8.5)".format(bid))
            elif n > 1:
                rep.finding("R1: backlog_item {!r} has {} done receipts; the receipt is one-to-one "
                            "(spec 8.5)".format(bid, n))
    for did, targets in done_targets.items():
        for tid in targets:
            st = bi_state.get(tid)
            if st is None:
                # A receipt whose target is not a backlog_item in the store. A dangling target is reported
                # by R4; a target that IS present but of the wrong type is reported here.
                tgt = by_id.get(tid)
                if tgt is not None and tgt.rtype != "backlog_item":
                    rep.finding("R1: done {!r} receipt_of -> {!r} is not a backlog_item (spec 8.1/8.5)".format(
                        did, tid))
            else:
                state, qual = st
                if not (state == "done" and qual is None):
                    rep.finding("R1: done {!r} receipt_of -> backlog_item {!r} which is not ratified at "
                                "'done' (state {!r}; spec 8.5)".format(did, tid, state))


def _check_r2_handoff(recs, rep):
    """R2: at most one `current` handoff across the whole store (OPF-SPEC 8.5:705)."""
    current = [r.id for r in recs if r.rtype == "handoff" and r.state == "current" and r.qual is None]
    if len(current) > 1:
        rep.finding("R2: {} handoffs are 'current'; at most one current handoff exists across the store "
                    "(spec 8.5)".format(len(current)))


def _check_r3_decision_chains(recs, by_id, rep):
    """R3: exactly one current effective resolution per pending_decision supersession chain (OPF-SPEC
    8.5:702). A chain is a connected component over `supersedes` links; its current effective resolution
    is a decided pending_decision that no other pending_decision supersedes. Two such in one chain is a
    finding."""
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
        current = [m for m in members if m not in superseded
                   and by_id.get(m) is not None and by_id[m].state == "decided" and by_id[m].qual is None]
        if len(current) > 1:
            rep.finding("R3: a pending_decision supersession chain has {} current effective resolutions; "
                        "exactly one exists per chain (spec 8.5)".format(len(current)))


def _check_r4_links(recs, by_id, all_ids, rep):
    """R4: every supersedes / receipt_of / resolves / remediates / corrects link target resolves to an
    existing record of the correct type (OPF-SPEC 8.6:721-723). receipt_of targets a backlog_item;
    supersedes targets the same type as its source; the rest need only an existing target."""
    for r in recs:
        for rel, tid in r.links:
            if rel not in RESOLVED_LINK_RELS or not isinstance(tid, str):
                continue
            if tid not in all_ids:
                rep.finding("R4: {} {} link -> {!r} resolves to no record in the store (a dangling link; "
                            "spec 8.6)".format(r.id, rel, tid))
                continue
            target = by_id.get(tid)
            if target is None:
                continue
            if rel == "receipt_of" and target.rtype != "backlog_item":
                rep.finding("R4: {} receipt_of -> {!r} is a {}, not a backlog_item (spec 8.1/8.5)".format(
                    r.id, tid, target.rtype))
            elif rel == "supersedes" and target.rtype != r.rtype:
                rep.finding("R4: {} (type {!r}) supersedes -> {!r} of type {!r}; supersession links the "
                            "same type (spec 8.6)".format(r.id, r.rtype, tid, target.rtype))


# --- the frozen-span / archive worklog checks (T4) ---------------------------------------------------

def _check_worklog_contiguous(numbers, rep):
    """F6: read across active + archive the worklog tiles from WL-1 with no gap, so the released spans (a
    structural check in validate_version) map to real coverage and the tail is everything after the last
    span (OPF-SPEC 6.1:444-445). A gap breaks span tiling."""
    nums = sorted(n for n in numbers if isinstance(n, int) and not isinstance(n, bool))
    if not nums:
        return
    if nums[0] != 1:
        rep.finding("F6: the worklog does not start at WL-1 (first present is WL-{}; span tiling starts at "
                    "WL-1, spec 6.1)".format(nums[0]))
    present = set(nums)
    missing = [n for n in range(1, nums[-1] + 1) if n not in present]
    if missing:
        rep.finding("F6: the worklog (active + archive) is not contiguous; WL-{} is missing (a gap breaks "
                    "span tiling, spec 6.1/11)".format(missing[0]))


# --- the whole-store validator -----------------------------------------------------------------------

def validate_store(resolution, supported_profiles=None):
    """Validate a RESOLVED store's whole-store integrity (OPF-SPEC 11:931-939). `resolution` is the object
    _opf_store.resolve_store returns; a resolution that is not RESOLVED is CANNOT-EVALUATE. Reads every
    declared input through U1's contained no-follow readers and reuses the U2/U3 primitives; owns only the
    cross-record, historical, and archive invariants no single-record or single-ledger pass can see.
    Returns a StoreValidation (VALID / INVALID / CANNOT-EVALUATE) plus the disclosed residuals."""
    rep = _Report()
    if resolution is None or getattr(resolution, "status", None) != RESOLVED:
        got = getattr(resolution, "status", None)
        rep.cant("store is not resolved (status {!r}); nothing to validate".format(got))
        return rep.result()
    machine_rel = resolution.machine_rel
    if not isinstance(machine_rel, str) or not machine_rel:
        rep.cant("resolved store carries no machine-store path")
        return rep.result()
    try:
        root_fd = _open_store_root_fd(resolution.store_root, resolution.pointer_source != "default")
    except OSError as exc:
        rep.cant("cannot open the resolved store root {} ({})".format(resolution.store_root, exc))
        return rep.result()
    try:
        _validate_opened_store(root_fd, machine_rel, supported_profiles, rep)
    finally:
        os.close(root_fd)
    return rep.result()


def _validate_opened_store(root_fd, machine_rel, supported_profiles, rep):
    # --- manifest: identify the store, derive the enabled types / vendors / layout --------------------
    manifest_rel = _rel(machine_rel, MANIFEST_NAME)
    manifest_data, st = _read_toml(root_fd, manifest_rel, rep)
    if st != "ok":
        if st == "absent":
            rep.cant("{} is absent (the store manifest is required; spec 4.5)".format(manifest_rel))
        return
    mv = validate_manifest(manifest_data, supported_profiles)
    if mv.status == CANNOT_EVALUATE:
        rep.cant("{}: {}".format(manifest_rel, "; ".join(mv.findings)))
        return
    if mv.status != VALID:
        for f in mv.findings:
            rep.finding("manifest: {}".format(f))

    dp = manifest_data.get("devprocess") if isinstance(manifest_data, dict) else None
    layout = dp.get("layout") if isinstance(dp, dict) else None
    enabled_types = {}
    types_tbl = manifest_data.get("types") if isinstance(manifest_data, dict) else None
    if isinstance(types_tbl, dict):
        for name, tbl in types_tbl.items():
            if isinstance(name, str) and isinstance(tbl, dict) and isinstance(tbl.get("namespace"), str):
                enabled_types[name] = tbl["namespace"]
    vendors = manifest_data.get("vendors") if isinstance(manifest_data, dict) else None
    reg = vendors.get("registered") if isinstance(vendors, dict) else None
    registered_vendors = frozenset(reg) if isinstance(reg, list) and all(isinstance(x, str) for x in reg) \
        else frozenset()

    # --- gather records: active non-worklog, active worklog, archive ----------------------------------
    active_recs = _gather_active_records(root_fd, machine_rel, enabled_types, layout, registered_vendors, rep)
    active_worklog = _gather_worklog(root_fd, _rel(machine_rel, WORKLOG_NAME), registered_vendors, rep,
                                     required=True) or {}
    archive_recs, archive_worklogs = _validate_archive(root_fd, machine_rel, registered_vendors, rep)

    # --- the merged worklog (active + archive together; spec 12:982-983) ------------------------------
    merged = {}
    active_wl_ids = set(active_worklog)
    archive_wl_ids = set()
    for n, entry in active_worklog.items():
        merged[n] = entry
    for _year, wl_map in archive_worklogs.items():
        for n, entry in wl_map.items():
            archive_wl_ids.add(n)
            merged[n] = entry

    # --- worklog _Rec descriptors (so R4 supersedes/corrects can resolve WL targets) ------------------
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

    # --- ledgers: version.toml (structural) + counters.toml -------------------------------------------
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

    high = {}
    counters_data, cst = _read_toml(root_fd, _rel(machine_rel, COUNTERS_NAME), rep)
    if cst == "absent":
        rep.finding("{} is absent (one monotonic high-water per namespace is required; spec 8.2)".format(
            _rel(machine_rel, COUNTERS_NAME)))
    elif cst == "ok":
        high, cfindings = validate_counters(counters_data)
        for f in cfindings:
            rep.finding("counters.toml: {}".format(f))

    # --- R6 / H4 (whole-store id space): uniqueness + ids-within-counters -----------------------------
    id_multiset = [r.id for r in active_recs if isinstance(r.id, str)]
    id_multiset += [r.id for r in archive_recs if isinstance(r.id, str)]
    id_multiset += ["WL-{}".format(n) for n in active_worklog]
    for _year, wl_map in archive_worklogs.items():
        id_multiset += ["WL-{}".format(n) for n in wl_map]
    for f in check_unique_ids(id_multiset):
        rep.finding("R6: {}".format(f))
    for f in check_ids_within_counters(id_multiset, high):
        rep.finding("R6/H4: {}".format(f))

    # --- T2 cross-record ------------------------------------------------------------------------------
    _check_r1_receipts(recs, by_id, rep)
    _check_r2_handoff(recs, rep)
    _check_r3_decision_chains(recs, by_id, rep)
    _check_r4_links(recs, by_id, all_ids, rep)

    # --- T4 frozen-span / archive / no-deletion (active + archive together) ---------------------------
    if version_data is not None:
        for f in check_frozen_coverage(version_data, merged):
            rep.finding("F1: {}".format(f))
        for f in check_rotation_only_released(archive_wl_ids, version_data):
            rep.finding("F4: {}".format(f))
    wl_hw = high.get("WL")
    expected = range(1, wl_hw + 1) if isinstance(wl_hw, int) and not isinstance(wl_hw, bool) and wl_hw >= 0 \
        else None
    if expected is not None:
        for f in check_no_deletion(expected, merged.keys()):
            rep.finding("F2: {}".format(f))
    for f in check_ids_partition(active_wl_ids, archive_wl_ids, expected_ids=expected):
        rep.finding("F3: {}".format(f))
    _check_worklog_contiguous(merged.keys(), rep)


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Whole-store integrity invariants over synthetic stores (one clean leg plus at least one leg per
    invariant class). Judged on the returned status / finding VALUES, never by grepping printed output
    (the isolate-verifiers rule). Each violation vector is discriminating: removing the enforcing branch
    would let the vector pass. Returns 0 clean, 1 on a failed check, 2 on a fail-closed error."""
    import tempfile
    import shutil
    import copy

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _opf_emit
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

    def pd(n, supersedes=None):
        r = envelope("PD-{}".format(n), "pending_decision", "decided",
                     decision="x", decided_at=TS, decided_by="maintainer")
        if supersedes is not None:
            r["links"] = [{"rel": "supersedes", "id": supersedes}]
        return r

    def fn(n):
        return envelope("FN-{}".format(n), "finding", "open")

    def base_manifest(layout="inline", types=None):
        if types is None:
            types = {t: {"namespace": ns} for t, ns in (
                ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
                ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
                ("autonomous_decision", "AD"), ("block", "BL"))}
        return {
            "devprocess": {"standard": "devprocess", "spec_version": "1.0.0", "layout": layout,
                           "posture": "required", "import_status": "none"},
            "store": {"sync_target": ""},
            "types": types,
            "vendors": {"registered": []},
            "archive": {"period": "year"},
        }

    def idx(records):
        return {"schema": 1, "record": records}

    full_wl = {n: wl(n) for n in (1, 2, 3, 4)}
    dig12 = compute_span_digest(full_wl, (1, 2))
    dig34 = compute_span_digest(full_wl, (3, 4))

    def rel_row(v, a, b, dig):
        return {"version": v, "date": TS, "worklog_span": ["WL-{}".format(a), "WL-{}".format(b)],
                "coverage_digest": dig}

    def clean_files():
        return {
            "manifest.toml": base_manifest(),
            "counters.toml": {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "HO": 1}},
            "backlog_item.index.toml": idx([bi(1, "done"), bi(2, "open")]),
            "done.index.toml": idx([dn(1, "BI-1")]),
            "finding.index.toml": idx([]),
            "pending_decision.index.toml": idx([]),
            "handoff.index.toml": idx([ho(1)]),
            "reference.index.toml": idx([]),
            "autonomous_decision.index.toml": idx([]),
            "block.index.toml": idx([]),
            "worklog.toml": {"schema": 1, "entry": [wl(3), wl(4)]},
            "version.toml": {"schema": 1, "release": [rel_row("1.0.0", 1, 2, dig12),
                                                      rel_row("1.1.0", 3, 4, dig34)],
                             "summary": [{"covers": "unreleased", "status": "working"}]},
            "archive/2026/archive.toml": {"schema": 1, "moved": [],
                                          "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                             "destination": "archive/2026/worklog.toml"}]},
            "archive/2026/worklog.toml": {"schema": 1, "entry": [wl(1), wl(2)]},
        }

    base = Path(tempfile.mkdtemp(prefix="opf-check-selftest-")).resolve()
    counter = [0]

    def build(files):
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / ".working" / "toml").mkdir(parents=True)
        for rel, doc in files.items():
            p = root / ".working" / "toml" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            text = doc if isinstance(doc, str) else _opf_emit.emit(doc)
            p.write_text(text, encoding="utf-8")
        return root

    def run(files):
        res = resolve_store(build(files))
        if res.status != RESOLVED:
            return None
        return validate_store(res)

    try:
        clean = run(clean_files())
        check("clean-resolved", clean is not None)
        check("clean-valid", clean is not None and clean.status == VALID)
        check("clean-no-findings", clean is not None and not clean.findings and not clean.cannot_evaluate)
        check("clean-discloses-residuals", clean is not None and bool(clean.residuals))

        f = clean_files()
        f["done.index.toml"] = idx([dn(1, "BI-1"), dn(2, "BI-1")])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 2, "WL": 4, "HO": 1}}
        check("r1-two-dones-same-bi-invalid", run(f).status == INVALID)
        f = clean_files()
        f["done.index.toml"] = idx([])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "WL": 4, "HO": 1}}
        check("r1-ratified-bi-no-done-invalid", run(f).status == INVALID)
        f = clean_files()
        f["done.index.toml"] = idx([dn(1, "BI-99")])
        check("r1-receipt-target-unresolved-invalid", run(f).status == INVALID)

        f = clean_files()
        f["handoff.index.toml"] = idx([ho(1), ho(2)])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "HO": 2}}
        check("r2-two-current-handoffs-invalid", run(f).status == INVALID)

        f = clean_files()
        f["pending_decision.index.toml"] = idx([pd(1), pd(2, "PD-1"), pd(3, "PD-1")])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "HO": 1, "PD": 3}}
        check("r3-two-current-resolutions-invalid", run(f).status == INVALID)

        f = clean_files()
        f["pending_decision.index.toml"] = idx([pd(1, "PD-99")])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "HO": 1, "PD": 1}}
        check("r4-dangling-supersedes-invalid", run(f).status == INVALID)

        d1, d2 = _record_digest(fn(1)), _record_digest(fn(2))
        pr = {
            "manifest.toml": base_manifest(layout="per-record", types={"finding": {"namespace": "FN"}}),
            "counters.toml": {"schema": 1, "counters": {"FN": 4}},
            "finding.index.toml": {"schema": 1, "record": [
                {"id": "FN-1", "digest": d1}, {"id": "FN-2", "digest": d2},
                {"id": "FN-4", "digest": "sha256:" + "a" * 64}]},
            "finding/FN-1.toml": fn(1),
            "finding/FN-2.toml": fn(2),
            "finding/FN-3.toml": fn(3),
            "worklog.toml": {"schema": 1, "entry": []},
            "version.toml": {"schema": 1, "release": [],
                             "summary": [{"covers": "unreleased", "status": "working"}]},
        }
        r5 = run(pr)
        check("r5-index-record-mismatch-invalid", r5.status == INVALID)
        check("r5-row-with-no-record-named",
              any("row" in fnd and "no record" in fnd for fnd in r5.findings))
        check("r5-record-with-no-row-named",
              any("no index row" in fnd for fnd in r5.findings))
        prc = {
            "manifest.toml": base_manifest(layout="per-record", types={"finding": {"namespace": "FN"}}),
            "counters.toml": {"schema": 1, "counters": {"FN": 2}},
            "finding.index.toml": {"schema": 1, "record": [
                {"id": "FN-1", "digest": d1}, {"id": "FN-2", "digest": d2}]},
            "finding/FN-1.toml": fn(1),
            "finding/FN-2.toml": fn(2),
            "worklog.toml": {"schema": 1, "entry": []},
            "version.toml": {"schema": 1, "release": [],
                             "summary": [{"covers": "unreleased", "status": "working"}]},
        }
        check("r5-clean-per-record-valid", run(prc).status == VALID)
        prm = copy.deepcopy(prc)
        prm["finding.index.toml"]["record"][0]["digest"] = "sha256:" + "b" * 64
        check("r5-digest-mismatch-invalid", run(prm).status == INVALID)

        f = clean_files()
        f["worklog.toml"] = {"schema": 1, "entry": [wl(2), wl(3), wl(4)]}
        check("r6-duplicate-id-invalid", run(f).status == INVALID)
        f = clean_files()
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 3, "HO": 1}}
        check("r6-id-above-high-water-invalid", run(f).status == INVALID)
        f = clean_files()
        f["counters.toml"] = "this is not valid toml === ["
        check("r6-corrupt-counters-cannot-eval", run(f).status == CANNOT_EVALUATE)

        f = clean_files()
        edited = wl(1)
        edited["summary"] = "EDITED after freeze"
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [edited, wl(2)]}
        check("f1-edited-frozen-archive-entry-invalid", run(f).status == INVALID)

        f = clean_files()
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 5, "HO": 1}}
        check("f2-id-in-neither-location-invalid", run(f).status == INVALID)

        f = clean_files()
        f["worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(3), wl(4)]}
        check("f3-id-in-both-invalid", run(f).status == INVALID)

        f = clean_files()
        f["version.toml"] = {"schema": 1, "release": [rel_row("1.0.0", 1, 2, dig12)],
                             "summary": [{"covers": "unreleased", "status": "working"}]}
        f["worklog.toml"] = {"schema": 1, "entry": [wl(4)]}
        f["archive/2026/archive.toml"] = {"schema": 1, "moved": [],
                                          "worklog_moved": [{"span": ["WL-1", "WL-3"],
                                                             "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/worklog.toml"] = {"schema": 1, "entry": [wl(1), wl(2), wl(3)]}
        check("f4-tail-id-rotated-invalid", run(f).status == INVALID)
        f = clean_files()
        f["handoff.index.toml"] = idx([])
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "HO-1", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/handoff.index.toml"] = idx([ho(1)])
        check("f4-non-terminal-rotated-invalid", run(f).status == INVALID)

        f = clean_files()
        f["archive/2026/reference.index.toml"] = idx([ref(9)])
        f["counters.toml"] = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "HO": 1, "RF": 9}}
        check("f5-archived-record-not-enumerated-invalid", run(f).status == INVALID)
        f = clean_files()
        f["archive/2026/archive.toml"] = {
            "schema": 1,
            "moved": [{"id": "RF-9", "type": "reference", "destination": "archive/2026/reference.index.toml"}],
            "worklog_moved": [{"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]}
        f["archive/2026/reference.index.toml"] = idx([])
        check("f5-enumerated-id-absent-from-dest-invalid", run(f).status == INVALID)
        f = clean_files()
        f["archive/2027/worklog.toml"] = {"schema": 1, "entry": []}
        check("f5-bucket-without-archive-toml-cannot-eval", run(f).status == CANNOT_EVALUATE)

        f = clean_files()
        f["version.toml"] = "not valid toml === ["
        check("io-unreadable-version-cannot-eval", run(f).status == CANNOT_EVALUATE)
        f = clean_files()
        f["archive/2026/worklog.toml"] = "not valid toml === ["
        check("io-unreadable-archive-bucket-cannot-eval", run(f).status == CANNOT_EVALUATE)
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
