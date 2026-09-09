#!/usr/bin/env python3
"""OPF unit U7: import staging (candidate + self-test only; the live `import` verb stays UNWIRED).

Offline, stdlib only, fail-closed. This module takes an operator-enumerated set of legacy SOURCE files
and an untrusted MAPPING PLAN, validates both, mints record ids from the store's counters, and STAGES a
byte-canonical candidate under `<machine>/imports/<run-id>/`. Its writes are confined to the `imports/`
staging ROOT (created if absent; spec 14 stages under `imports/<run-id>/`, so the staging root is part of
the staging area, not the active store) and the new run directory beneath it: the active store, its
`counters.toml`, its indexes, its archive, and the sources are read-only inputs, never written. It
composes U1 (store resolution + manifest), U2 (record envelope + counters + id helpers), U3 (the worklog
release-boundary gate), and U8 (the constrained-subset canonical emitter) rather than re-deriving them.

Sequencing (build plan): U7 lands as MODULE + SELF-TEST ONLY, like U4/U5. The live `import` verb is left
on opf.py's fail-closed KNOWN_VERBS path (F-373 / VC-4-HARDEN): opf.py gains an `import _opf_import` line
and one `("opf-import", _opf_import.self_test)` SELF_TESTS row, and NOTHING in main()/KNOWN_VERBS/dispatch.
A self-test vector (dispatch deferral) proves `opf.py import` still exits 2 and stages nothing, so wiring
the verb later is a conscious edit to this test rather than a silent drift.

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
  - an archive year is `<machine>/archive/<year>/archive.toml` carrying `moved = [<id>, ...]`;
  - a sibling staging run mirrors this run's layout; every `*.index.toml` and `worklog.toml` under its
    `candidate/` and `fragments/` enumerates staged ids (an incomplete sibling, no report.toml, still
    enumerates what it wrote; an unreadable/unparseable sibling artefact is CANNOT-EVALUATE);
  - the run-id grammar `imp-<UTCSTAMP>Z-<hash16>` is U7-defined (a spec follow-on is proposed).

The untrusted PLAN (spec 14.1, inert data staged verbatim as plan.toml):
  {
    "fragments": { <source-path>: [ {"span": [start, end], "state": <one of MAPPING_STATES>,
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
import datetime
import hashlib
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

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: tools/_opf_import.py requires Python 3.11+ (tomllib).")


# --- fixed names, vocabularies, and the outcome model ------------------------------------------------

CLEAN = 0            # staged: a full pass
FINDING = 1          # a validation finding (R6 collision, bad plan/candidate, plan-supplied id): stage nothing
CANNOT_EVALUATE = 2  # unreadable/malformed/exotic/out-of-subset: stage nothing (fail-closed)

IMPORTS_DIRNAME = "imports"
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
_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")
_RUN_DESCRIPTOR_FORMAT = "opf-import-run-v1"

LF_TYPE = "legacy_fragment"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Closed plan keyset and closed fragment-row keyset.
_PLAN_KEYS = frozenset({"fragments", "worklog", "version"})
_FRAGMENT_ROW_KEYS = frozenset({"span", "state", "record", "target", "note"})


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
    """A contained TOML read (U1): the parsed dict, or None when absent. A StoreError is CANNOT-EVALUATE."""
    try:
        return _opf_store._read_toml_contained(store_root_fd, rel)
    except _opf_store.StoreError as exc:
        raise _cannot(str(exc))


def _record_ids(data, where, roster=None, expected_type=None):
    """Extract the ids of a `{schema = 1, record: [[record]]}` index file, fail-closed. An ABSENT file is
    zero records (absence outside the declared set is clean). A PRESENT file is held to the declared index
    contract (module docstring): ONLY the `schema` and `record` top-level keys, a `schema = 1`, a `record`
    array of tables, each row a table with a well-formed id; any violation is CANNOT-EVALUATE naming the
    file (a present-but-contract-malformed index is a refusing failure, never silent absence). An explicit
    `schema = 1` with `record = []` is a genuine empty index and reads as zero records.

    When `roster` and `expected_type` are supplied AND the roster carries a spec for that type (a SIBLING
    staging index, whose records an incomplete concurrent run may have written malformed), each record is
    additionally held to its COMPLETE envelope contract via _opf_schema.validate_record with file/type
    agreement (expected_type from the file the record sits in): a record missing envelope fields, whose
    `type` disagrees with its file, or otherwise schema-invalid is CANNOT-EVALUATE, never a silent id-only
    read (B4, check-fails-closed-on-unreadable). An ACTIVE-store index (no roster passed) is validated
    structurally only: a module-tier type carries no local TypeSpec and an already-promoted record may
    carry a manifest-registered x-<vendor> extension this reader cannot resolve, so its ids still enter the
    union while its structural contract is enforced (disclosed residual)."""
    if data is None:
        return []
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
    spec = roster.get(expected_type) if (roster is not None and expected_type is not None) else None
    ids = []
    for i, r in enumerate(recs):
        if not isinstance(r, dict):
            raise _cannot("{}: record[{}] is not a table".format(where, i))
        rid = r.get("id")
        if _opf_schema._valid_id_shape(rid) is None:
            raise _cannot("{}: record[{}] carries a malformed id {!r}".format(where, i, rid))
        if spec is not None:
            # A sibling record whose complete contract the roster can judge: full envelope validation with
            # file/type agreement. A non-VALID sibling record is a refusing failure, not a silent id read.
            rv = _opf_schema.validate_record(r, expected_type=expected_type, specs=roster)
            if rv.status != _opf_store.VALID:
                raise _cannot("{}: record[{}] does not satisfy its complete {} contract ({})".format(
                    where, i, expected_type, "; ".join(rv.findings)))
        ids.append(rid)
    return ids


def _index_ids(store_root_fd, machine_rel, type_name):
    # An ACTIVE-store index is scanned for its ids STRUCTURALLY only (no roster passed): a module-tier type
    # has no local spec and an already-promoted record may carry a registered vendor extension. Sibling
    # indexes, read via _sibling_ids, get the full-contract validation (B4).
    rel = "{}/{}.index.toml".format(machine_rel, type_name)
    return _record_ids(_read_toml(store_root_fd, rel), rel)


def _archive_ids(store_root_fd, machine_rel):
    """Every id enumerated across `<machine>/archive/<year>/archive.toml` (spec 12: rotation enumerates
    every moved id). Archive-year dirs are enumerated with the no-follow-REFUSING helper (B3), so a
    SYMLINKED or non-directory archive-year entry is CANNOT-EVALUATE, never silently omitted from the
    uniqueness union (U1's _immediate_subdirs would drop it: correct for store DISCOVERY, wrong for the
    union). An archive year without a parseable archive.toml, or a `moved` list carrying a malformed id, is
    CANNOT-EVALUATE, fail-closed."""
    archive_rel = "{}/{}".format(machine_rel, ARCHIVE_DIRNAME)
    years = _dir_entries_no_symlink(store_root_fd, archive_rel)
    if years is None:
        return []
    ids = []
    for year in years:
        rel = "{}/{}/archive.toml".format(archive_rel, year)
        data = _read_toml(store_root_fd, rel)
        if data is None:
            raise _cannot("{}: an archive year must carry a parseable archive.toml (spec 12)".format(rel))
        moved = data.get("moved")
        if not isinstance(moved, list):
            raise _cannot("{}: `moved` must be an array of record ids (spec 12)".format(rel))
        for mid in moved:
            if _opf_schema._valid_id_shape(mid) is None:
                raise _cannot("{}: moved id {!r} is malformed".format(rel, mid))
            ids.append(mid)
    return ids


def _list_contained(store_root_fd, rel):
    """The immediate entry names of a directory beneath the store root, listed no-follow, or None when the
    directory is absent. CANNOT-EVALUATE on any read error or a refused symlink (fail-closed listing)."""
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
            return sorted(os.listdir(dfd))
        finally:
            os.close(dfd)
    finally:
        os.close(pfd)


def _dir_entries_no_symlink(store_root_fd, rel):
    """The immediate entry names of a directory beneath the store root, listed no-follow, or None when the
    directory is absent. A SYMLINK, or any non-directory entry, is CANNOT-EVALUATE (fail-closed): an entry
    that cannot be classified as a real subdirectory must refuse rather than vanish from the uniqueness
    union, where U1's _immediate_subdirs would silently drop it (correct for store DISCOVERY, wrong for the
    union; F4/B3). Used for BOTH the `imports/` sibling-run sweep and the `archive/` year enumeration."""
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
            out = []
            for entry in sorted(os.listdir(dfd)):
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
            os.close(dfd)
    finally:
        os.close(pfd)


def _sibling_ids(store_root_fd, machine_rel, roster, skip_run_id=None):
    """Every staged id across every sibling run under `<machine>/imports/` (spec 11: staging is a location
    the R6 union covers). A sibling's own on-disk `*.index.toml` and `worklog.toml` under `candidate/` and
    `fragments/` ARE the authoritative index of its staged ids, so an incomplete sibling (no report.toml)
    still enumerates what it wrote; only an unreadable/unparseable/contract-malformed sibling artefact is
    CANNOT-EVALUATE. Each present sibling index is held to its COMPLETE record contract (B4), the file's
    type taken from its name (`<type>.index.toml` -> `<type>`, `worklog.toml` -> `worklog`). A symlinked
    (or otherwise non-directory) `imports/` entry is CANNOT-EVALUATE, never silently omitted (F4).
    `skip_run_id`, when set, excludes this run's own just-claimed dir from the sweep so it does not
    self-collide on its own indexes during the post-claim re-check (F2)."""
    imports_rel = "{}/{}".format(machine_rel, IMPORTS_DIRNAME)
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
                    etype = entry[:-len(".index.toml")]
                elif entry == "worklog.toml":
                    etype = "worklog"
                else:
                    continue
                rel = "{}/{}".format(sub_rel, entry)
                ids.extend(_record_ids(_read_toml(store_root_fd, rel), rel, roster, etype))
    return ids


def _worklog_ids(store_root_fd, machine_rel):
    """The WL ids of the ACTIVE worklog.toml (`[[entry]]` rows, spec 6.2), fail-closed. Absent -> zero; a
    present file whose schema, `entry` array, or an entry id is malformed is CANNOT-EVALUATE. The active
    worklog is NOT an `{schema, record}` index (its rows are `[[entry]]` and its `schema` is optional per
    U3), so _record_ids does not read it; this reader honours the U3 worklog.toml shape."""
    rel = "{}/worklog.toml".format(machine_rel)
    data = _read_toml(store_root_fd, rel)
    if data is None:
        return []
    schema = data.get("schema")
    if schema is not None and (type(schema) is not int or schema != SCHEMA):
        raise _cannot("{}: worklog schema {!r} is not the supported schema {}".format(rel, schema, SCHEMA))
    entries = data.get("entry")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise _cannot("{}: `entry` must be an array of tables".format(rel))
    ids = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise _cannot("{}: entry[{}] is not a table".format(rel, i))
        eid = e.get("id")
        if _opf_schema._valid_id_shape(eid) is None:
            raise _cannot("{}: entry[{}] carries a malformed id {!r}".format(rel, i, eid))
        ids.append(eid)
    return ids


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


def _active_store_ids(store_root_fd, machine_rel, active_types):
    """Every id in the WHOLE active store: each ENABLED active type's own `<type>.index.toml` PLUS the
    active worklog.toml PLUS the archive (spec 11: uniqueness is whole-store, so an id sitting in a
    DIFFERENT active index than its minting type, INCLUDING an enabled module-tier index, is SEEN, not read
    as absent; B2/F3). `active_types` already excludes worklog (its ids come from worklog.toml, read via
    _worklog_ids). Fail-closed on any unreadable or contract-malformed input."""
    ids = []
    for type_name in sorted(active_types):
        ids.extend(_index_ids(store_root_fd, machine_rel, type_name))
    ids.extend(_worklog_ids(store_root_fd, machine_rel))
    ids.extend(_archive_ids(store_root_fd, machine_rel))
    return ids


def _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids, skip_run_id=None):
    """The R6 uniqueness union (spec 11): this run's minted ids plus every id already present anywhere a
    minted id could collide, the WHOLE active store (every ENABLED active type index, the active worklog,
    the archive) and every sibling staging run. One assembly path, shared by the pre-write check and the
    post-claim re-check (B2/F3/F4/F2). `active_types` is the enabled active-type name set; `roster` carries
    the TypeSpecs a present sibling index is fully validated against (B4). `skip_run_id`, when set, excludes
    that run's own dir from the sibling sweep (the re-check, so a run does not self-collide on its
    just-written indexes)."""
    union = list(minted_ids)
    union.extend(_active_store_ids(store_root_fd, machine_rel, active_types))
    union.extend(_sibling_ids(store_root_fd, machine_rel, roster, skip_run_id=skip_run_id))
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
    byte-canonical candidate under `<machine>/imports/<run-id>/`. Writes only the `imports/` staging root
    (created if absent) and the new run directory beneath it; the active store, its counters, indexes,
    archive, and the sources are read-only. Returns a StageResult; fail-closed on anything unreadable,
    malformed, exotic, or outside the supported subset, never a silent clean pass."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return StageResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        _require_utc(now)
        _require_nonce(run_nonce)
        if not (isinstance(import_set, (list, tuple)) and all(isinstance(p, str) for p in import_set)):
            raise _cannot("import_set must be a list of source-path strings")

        resolution = _opf_store.resolve_store(product_root)
        if resolution.status != _opf_store.RESOLVED:
            raise _cannot("store did not resolve ({}: {})".format(resolution.status, resolution.detail))
        mv = _opf_store.load_manifest(resolution)
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
    run_rel = "{}/{}/{}".format(machine_rel, IMPORTS_DIRNAME, run_id)

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

    # B2: the ENABLED active-type set the whole-store id union and the duplicate/candidate-link existence
    # authority scan, derived from the store's [modules] config (fail-closed on a malformed config, never a
    # partial union). Computed once, shared by the union, the existence set, and the post-claim re-check.
    active_types = _active_types(store_root_fd, machine_rel)

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
        # proposal). Built once, on first use.
        nonlocal existing_lookup
        if existing_lookup is None:
            existing_lookup = _existing_id_set(store_root_fd, machine_rel, active_types)
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
            allowed_keys = {"span", "state", "note"}
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
            state_counts[state] += 1
            target = row.get("target")
            mapping_states[sp].append({"span": [start, end], "state": state, "target": target})

            if state in _CANDIDATE_STATES:
                # A mapped/split row mints its OWN id (B6 already refused a contradictory `target`).
                model = row.get("record")
                ns = _validate_candidate_model(model, roster, where)
                rtype = model["type"]
                rid = mint(ns, where)
                rec = dict(model)
                rec["id"] = rid
                # B5: do NOT fabricate created_at from the staging clock. When the source model carries no
                # creation time, the record's ORIGINAL creation instant is unknown (timestamp-from-clock: a
                # date for an earlier event is never guessed from the current clock). Omit created_at and
                # attach the import-provenance reference (source path + digest + span + run id) the staged
                # record already carries; the importer created_at exception (spec 8.3) then requires exactly
                # such a provenance ref in place of the omitted timestamp, so a NON-importer candidate that
                # omits its creation time stays a finding (validate_record) rather than a fabricated stamp.
                if "created_at" not in rec:
                    prov = {"kind": "path", "locator": source["path"],
                            "note": "imported fragment [{}:{}] of {} (sha256:{}) in run {}; original "
                                    "created_at unknown".format(start, end, source["path"],
                                                                source["sha256"], run_id)}
                    refs = rec.get("refs")
                    if isinstance(refs, list):
                        rec["refs"] = list(refs) + [prov]
                    elif refs is None:
                        rec["refs"] = [prov]
                    # a non-list `refs` is left untouched for validate_record to reject as malformed
                rec.setdefault("updated_at", stamp)
                rv = _opf_schema.validate_record(rec, expected_type=rtype, specs=roster)
                if rv.status != _opf_store.VALID:
                    raise _finding("{}: candidate is not a valid {} ({})".format(
                        where, rtype, "; ".join(rv.findings)))
                # B7: a candidate may LINK to an EXISTING record, but candidate-to-candidate links are
                # unsupported: every links[].id must resolve in the active/archive existence authority (the
                # same set a duplicate target resolves against). An unresolved link (a dangling id, or a
                # link to an id minted only in this import set) is a finding: a candidate with a dangling
                # link is not a fully-validated candidate (spec 14.1). validate_record already proved each
                # link well-formed, so link.get("id") is a valid id string here.
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
            rec = dict(model)
            rec["id"] = rid
            rec.setdefault("date", stamp)
            rv = _opf_schema.validate_record(rec, expected_type="worklog", specs=roster)
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
    # B2/F3/F4: one assembly path scans every ENABLED active type index (baseline + enabled module-tier),
    # the active worklog, and the archive, plus every sibling run (a symlinked sibling or archive year is
    # refused, not dropped), so an id in a non-minting active index, a module-tier index, or a symlinked
    # sibling is SEEN rather than read as absent.
    dup_findings = _opf_schema.check_unique_ids(
        _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids))
    if dup_findings:
        raise _finding("R6 id uniqueness: {}".format("; ".join(dup_findings)))

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
                                working_high, state_counts, active_types, roster, minted_ids)
    return StageResult(CLEAN, [], run_id=run_id, run_rel=run_rel_result, mapping_states=mapping_states,
                       staged_ids=minted_ids, new_high_water=working_high, promotion_ready=True,
                       migration_incomplete=bool(lf_records))


def _existing_id_set(store_root_fd, machine_rel, active_types):
    """The set of ids that exist across the WHOLE active store (every ENABLED active type's index PLUS the
    active worklog.toml) and the archive: the authority a `duplicate` target and a candidate link must
    resolve against (B2/B7). Reuses the same enabled active-type set the R6 union scans, so a module-tier
    target (e.g. MA-1 under [modules] governance) resolves; the active worklog is read via _worklog_ids,
    since `worklog.index.toml` does not exist, so a valid active WL-n target resolves through the correct
    reader rather than a nonexistent index (MINOR). Fail-closed on any unreadable/unparseable input."""
    ids = set()
    for type_name in active_types:
        ids.update(_index_ids(store_root_fd, machine_rel, type_name))
    ids.update(_worklog_ids(store_root_fd, machine_rel))
    ids.update(_archive_ids(store_root_fd, machine_rel))
    return ids


def _write_run(store_root_fd, machine_rel, run_id, run_rel, plan_bytes, sources, now, run_nonce,
               mapping_states, candidate_records, lf_records, worklog_records, working_high, state_counts,
               active_types, roster, minted_ids):
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
            row = {"source_path": sp, "span": list(m["span"]), "state": m["state"]}
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

    # F7: preserve each source's FULL original bytes in the run, content-addressed as `sources/<sha256>`
    # (spec 14.2: the original's full content is preserved in the import run). Stored VERBATIM, not through
    # U8 (a raw body, not TOML), so a fully-mapped source's content still survives here; the digest is the
    # source sha256, re-verified by apply_ops. Identical-content sources dedupe to one file.
    source_bodies = {"sources/{}".format(s["sha256"]): s["raw"] for s in sources}

    all_body = dict(files)
    all_body.update(source_bodies)

    report = {
        "schema": SCHEMA, "run_id": run_id, "verdict": CLEAN, "promotion_ready": True,
        "migration_incomplete": bool(lf_records),
        "counts": {k: v for k, v in state_counts.items() if v},
        "artifact": [{"path": suffix, "sha256": _sha256_hex(data)}
                     for suffix, data in sorted(all_body.items())],
    }
    files_report = _emit_bytes(report, "report.toml")

    content = {run_rel + "/" + suffix: data for suffix, data in all_body.items()}

    # --- pass 1: claim the exclusive run dir and stage every file EXCEPT report.toml ------------------
    ops = []
    imports_rel = "{}/{}".format(machine_rel, IMPORTS_DIRNAME)
    imports_st = _journal._lstat_contained(store_root_fd, imports_rel)
    if imports_st is None:
        # The imports/ staging ROOT (spec 14) is created if absent: part of the staging area, not the
        # active store (F1). A race surfaces as a JournalError -> CANNOT-EVALUATE.
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
        _uniqueness_union(store_root_fd, machine_rel, active_types, roster, minted_ids, skip_run_id=run_id))
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


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Import-staging invariants over synthetic stores. Judged on the returned verdict values, never by
    grepping output. Fixtures live under a private tempdir removed in a finally; the injected instant and
    nonce are fixed, so ids and bytes are deterministic. Follows the U1 self-test idiom."""
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
    INDEP_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")

    def manifest_text():
        return "\n".join([
            "[devprocess]", 'standard = "devprocess"', 'spec_version = "1.0.0"',
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

    def build_store(counters="BI=0,LF=0,WL=0", sources=None, extra=None):
        """A default-resolution store with a valid manifest + counters, plus optional source files and
        extra store files ({rel-under-machine: text})."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        machine = root / ".working" / "toml"
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
        return root, machine

    def bi_candidate():
        return {"type": "backlog_item", "status": "open", "title": "Imported item",
                "actor": {"kind": "importer"}}

    def bi_index_text(rid="BI-1"):
        """A FULLY VALID backlog_item index (all envelope fields present), so a sibling or active fixture is
        a genuine VALID store under the complete-contract sibling validation (B4) and the whole-store union
        (M11: clean fixtures are VALID stores)."""
        return ('schema = 1\n\n[[record]]\nid = "{}"\ntype = "backlog_item"\n'
                'status = "open"\ntitle = "x"\ncreated_at = "2026-01-01T00:00:00Z"\n'
                'updated_at = "2026-01-01T00:00:00Z"\nactor = {{ kind = "maintainer" }}\n').format(rid)

    def plan_mapped(source_len):
        return {"fragments": {"a.txt": [
            {"span": [0, source_len], "state": "mapped", "record": bi_candidate()}]}}

    def snapshot(machine, include_imports=True):
        """A byte snapshot of the machine tree for a before/after comparison. With include_imports=False it
        covers only the ACTIVE store (manifest, counters, indexes, archive, version/worklog), excluding the
        imports/ staging area, so an active-store-unchanged assertion can allow the new imports/<run-id>/
        subtree while still pinning that nothing outside imports/ changed (F1/F11.3). Default INCLUDES
        imports/ so an out-of-run-dir write becomes visible."""
        snap = {}
        for p in sorted(machine.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(machine)
            if not include_imports and "imports" in rel.parts:
                continue
            snap[str(rel)] = p.read_bytes()
        return snap

    try:
        # 1: positive stage (mapped + unmapped): verdict 0, run dir + files present, LF quarantine for the
        # unmapped fragment carrying all four provenance fields, active store + counters unchanged.
        src = "hello world body"
        root, machine = build_store(sources={"a.txt": src})
        before = snapshot(machine, include_imports=False)
        rows = [{"span": [0, 5], "state": "mapped", "record": bi_candidate()},
                {"span": [5, len(src)], "state": "unmapped"}]
        res = stage_import(root, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce=NONCE)
        check("1-positive-clean", res.verdict == 0)
        check("1-run-id-grammar", bool(res.run_id and INDEP_RUN_ID_RE.match(res.run_id)))
        check("1-migration-incomplete", res.migration_incomplete is True)
        run_dir = machine / "imports" / (res.run_id or "MISSING")
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
        check("1-store-unchanged", snapshot(machine, include_imports=False) == before)
        # F1/F11.3: the ONLY new entry under imports/ is this run's dir (the staging root plus one run
        # dir); nothing outside imports/<run-id>/ was written, and no active index/counter/archive byte
        # changed (asserted above with include_imports=False).
        imports_children = sorted(d.name for d in (machine / "imports").iterdir())
        check("1-only-run-dir-under-imports", imports_children == [res.run_id])
        check("1-two-ids", len(res.staged_ids) == 2)

        # 2: determinism: identical inputs give a byte-identical id; a different nonce gives a different id.
        root2, machine2 = build_store(sources={"a.txt": src})
        res2 = stage_import(root2, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce=NONCE)
        check("2-deterministic-id", res2.run_id == res.run_id and res2.verdict == 0)
        root3, machine3 = build_store(sources={"a.txt": src})
        res3 = stage_import(root3, ["a.txt"], {"fragments": {"a.txt": rows}}, now=NOW, run_nonce="other")
        check("2-nonce-changes-id", res3.run_id != res.run_id and res3.verdict == 0)

        # 3: dispatch deferral (F-373): `opf.py import` still exits 2 and stages nothing.
        opf_py = str(Path(__file__).resolve().parent / "opf.py")
        root4, machine4 = build_store(sources={"a.txt": src})
        cp = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--root", str(root4)],
                            capture_output=True)
        check("3-verb-exits-2", cp.returncode == 2)
        check("3-verb-stages-nothing", not (machine4 / "imports").exists())

        # 4: R6 active collision: an active index already carries the id next_id will mint (BI-1 above the
        # BI=0 high-water) -> verdict 1, nothing written.
        idx_text = ('schema = 1\n\n[[record]]\nid = "BI-1"\ntype = "backlog_item"\n'
                    'status = "open"\ntitle = "x"\n')
        root5, machine5 = build_store(sources={"a.txt": src}, extra={"backlog_item.index.toml": idx_text})
        res5 = stage_import(root5, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("4-active-collision-finding", res5.verdict == 1)
        check("4-active-collision-no-run", not (machine5 / "imports").exists())

        # 5: R6 archive collision: the minted id is enumerated in a synthetic archive.toml -> verdict 1.
        root6, machine6 = build_store(sources={"a.txt": src},
                                      extra={"archive/2026/archive.toml": 'moved = ["BI-1"]\n'})
        res6 = stage_import(root6, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("5-archive-collision-finding", res6.verdict == 1)

        # 6: R6 sibling collision: a pre-built sibling run carries the id -> verdict 1; an unparseable
        # sibling artefact -> verdict 2. The sibling record is a FULLY VALID backlog_item so it survives the
        # complete-contract sibling validation (B4) and its id is scanned into the union.
        sib = {"imports/imp-20260101T000000Z-0000000000000000/candidate/backlog_item.index.toml":
               bi_index_text()}
        root7, machine7 = build_store(sources={"a.txt": src}, extra=sib)
        res7 = stage_import(root7, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("6-sibling-collision-finding", res7.verdict == 1)
        bad_sib = {"imports/imp-20260101T000000Z-0000000000000000/candidate/backlog_item.index.toml":
                   "this is not = valid toml ["}
        root8, machine8 = build_store(sources={"a.txt": src}, extra=bad_sib)
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
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                                       "record": bad}]}}, now=NOW, run_nonce=NONCE)
        check("8-plan-supplied-id-finding", res11.verdict == 1)

        # 9: an invalid candidate (bad status for the type) -> verdict 1.
        badstat = bi_candidate(); badstat["status"] = "not-a-state"
        root12, machine12 = build_store(sources={"a.txt": src})
        res12 = stage_import(root12, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                                       "record": badstat}]}}, now=NOW, run_nonce=NONCE)
        check("9-invalid-candidate-finding", res12.verdict == 1)

        # 10: tiling violations each -> verdict 1 (gap, beyond EOF, reversed); an empty source needs one
        # explicit [0, 0] row.
        root13, machine13 = build_store(sources={"a.txt": src})
        gap = [{"span": [0, 3], "state": "unmapped"}, {"span": [5, len(src)], "state": "unmapped"}]
        check("10-gap-finding",
              stage_import(root13, ["a.txt"], {"fragments": {"a.txt": gap}}, now=NOW, run_nonce=NONCE).verdict == 1)
        beyond = [{"span": [0, len(src) + 5], "state": "unmapped"}]
        root14, machine14 = build_store(sources={"a.txt": src})
        check("10-beyond-eof-finding",
              stage_import(root14, ["a.txt"], {"fragments": {"a.txt": beyond}}, now=NOW, run_nonce=NONCE).verdict == 1)
        rev = [{"span": [5, 2], "state": "unmapped"}]
        root15, machine15 = build_store(sources={"a.txt": src})
        check("10-reversed-finding",
              stage_import(root15, ["a.txt"], {"fragments": {"a.txt": rev}}, now=NOW, run_nonce=NONCE).verdict == 1)
        root16, machine16 = build_store(sources={"empty.txt": ""})
        ok_empty = {"fragments": {"empty.txt": [{"span": [0, 0], "state": "unmapped"}]}}
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
        badplan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped",
                                            "note": [[1, 2]]}]}}
        res21 = stage_import(root21, ["a.txt"], badplan, now=NOW, run_nonce=NONCE)
        check("13-emitter-boundary-cannot-eval", res21.verdict == 2)
        check("13-emitter-no-run", not (machine21 / "imports").exists())

        # 14: run-dir refusal: a pre-created run directory -> verdict 2, pre-existing content byte-intact.
        root22, machine22 = build_store(sources={"a.txt": src})
        pre = stage_import(root22, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        rid = pre.run_id
        shutil.rmtree(machine22 / "imports")
        target = machine22 / "imports" / rid
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
        vplan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]},
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
        dup_bad = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate",
                                            "target": "BI-999"}]}}
        check("17-duplicate-dangling-finding",
              stage_import(root25, ["a.txt"], dup_bad, now=NOW, run_nonce=NONCE).verdict == 1)
        dup_ok = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate",
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
        wl_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]},
                   "worklog": [{"kind": "added", "summary": "imported note",
                                "actor": {"kind": "importer"}}]}
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
        rootA, machineA = build_store(sources={"a.txt": src}, extra=sibx)
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

        # F3: an id in a DIFFERENT active index than its minting type is seen by the whole-store union.
        rootF3, machineF3 = build_store(sources={"a.txt": src},
                                        extra={"done.index.toml": 'schema = 1\n\n[[record]]\nid = "BI-1"\n'})
        resF3 = stage_import(rootF3, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("F3-cross-index-collision-finding", resF3.verdict == 1)
        check("F3-cross-index-no-run", not (machineF3 / "imports").exists())

        # F4: a SYMLINKED sibling run is CANNOT-EVALUATE (fail-closed), never silently dropped.
        realsib = base / "real-sibling"
        (realsib / "candidate").mkdir(parents=True)
        (realsib / "candidate" / "backlog_item.index.toml").write_text(
            'schema = 1\n\n[[record]]\nid = "BI-1"\ntype = "backlog_item"\nstatus = "open"\ntitle = "x"\n',
            encoding="utf-8")
        rootF4, machineF4 = build_store(sources={"a.txt": src})
        (machineF4 / "imports").mkdir()
        os.symlink(str(realsib), str(machineF4 / "imports" / sib_run))
        check("F4-symlink-sibling-cannot-eval",
              stage_import(rootF4, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # F5: a present-but-contract-malformed sibling index is CANNOT-EVALUATE, not zero records; an
        # explicit empty index (schema = 1, record = []) still reads as zero and stages clean.
        def _sib_index(text):
            return {"imports/{}/candidate/backlog_item.index.toml".format(sib_run): text}
        rootF5a, mF5a = build_store(sources={"a.txt": src}, extra=_sib_index("schema = 2\n"))
        check("F5-bad-schema-cannot-eval",
              stage_import(rootF5a, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootF5b, mF5b = build_store(sources={"a.txt": src}, extra=_sib_index("schema = 1\n"))
        check("F5-missing-record-cannot-eval",
              stage_import(rootF5b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootF5c, mF5c = build_store(sources={"a.txt": src}, extra=_sib_index("schema = 1\nrecord = []\n"))
        check("F5-empty-index-clean",
              stage_import(rootF5c, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 0)

        # F6: fragment bodies are extracted from RAW BYTES then decoded. Source "éX" (bytes c3 a9 58):
        # mapped [0,2] (the é), quarantine [2,3] (X) -> the staged legacy_fragment body is "X".
        eX = "éX"   # 3 UTF-8 bytes
        rootF6, mF6 = build_store(sources={"a.txt": eX})
        f6_plan = {"fragments": {"a.txt": [
            {"span": [0, 2], "state": "mapped", "record": bi_candidate()},
            {"span": [2, 3], "state": "unmapped"}]}}
        resF6 = stage_import(rootF6, ["a.txt"], f6_plan, now=NOW, run_nonce=NONCE)
        check("F6-byte-span-clean", resF6.verdict == 0)
        if resF6.run_id:
            lfp = mF6 / "imports" / resF6.run_id / "fragments" / "legacy_fragment.index.toml"
            check("F6-byte-span-body-X",
                  lfp.is_file() and tomllib.loads(lfp.read_text())["record"][0]["body"] == "X")
        # a span splitting the multi-byte é ([0,1]) is refused fail-closed (not silently emptied).
        rootF6b, mF6b = build_store(sources={"a.txt": eX})
        f6b_plan = {"fragments": {"a.txt": [
            {"span": [0, 1], "state": "unmapped"}, {"span": [1, 3], "state": "unmapped"}]}}
        check("F6-split-multibyte-finding",
              stage_import(rootF6b, ["a.txt"], f6b_plan, now=NOW, run_nonce=NONCE).verdict == 1)

        # F7: a FULLY mapped source's full original content is preserved in the run (spec 14.2), under
        # sources/. The marker appears in the preserved-source artefact, which pre-fix exists nowhere.
        marker = "UNIQUE-LEGACY-BODY-7f1c9a"
        rootF7, mF7 = build_store(sources={"a.txt": marker})
        resF7 = stage_import(rootF7, ["a.txt"], plan_mapped(len(marker)), now=NOW, run_nonce=NONCE)
        check("F7-fully-mapped-clean", resF7.verdict == 0)
        if resF7.run_id:
            run_dir7 = mF7 / "imports" / resF7.run_id
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
        f9_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                            "record": bi_candidate(), "target": "BI-999"}]}}
        rootF9, mF9 = build_store(sources={"a.txt": src})
        resF9 = stage_import(rootF9, ["a.txt"], f9_plan, now=NOW, run_nonce=NONCE)
        check("F9-mapped-target-finding", resF9.verdict == 1)
        check("F9-mapped-target-no-run", not (mF9 / "imports").exists())

        # F10: an accepted lexical internal '..' source (sub/../a.txt) -> verdict 2, NO uncaught raise.
        rootF10, mF10 = build_store(sources={"a.txt": src})
        check("F10-internal-dotdot-cannot-eval",
              stage_import(rootF10, ["sub/../a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)

        # F11.1: apply_ops must not write THROUGH a symlink (fd-relative, no-follow). A run-id symlink to an
        # external dir is refused and the external dir is never written into. Positive-property vector: an
        # unsafe path-based writer would resolve the link and populate the target, failing this assertion.
        rootS, machineS = build_store(sources={"a.txt": src})
        rid_s = stage_import(rootS, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).run_id
        shutil.rmtree(machineS / "imports")
        external = base / "external-link-target"
        external.mkdir()
        (machineS / "imports").mkdir()
        os.symlink(str(external), str(machineS / "imports" / rid_s))
        resS = stage_import(rootS, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("F11.1-symlink-run-dir-refused", resS.verdict == 2)
        check("F11.1-symlink-target-untouched", list(external.iterdir()) == [])

        # --- B2/B3/B4/B6/B7/M2 discrimination + the worklog-ledger relaxation (M11) ----------------------

        # B2: the whole-store uniqueness union and the duplicate/candidate-link existence authority cover
        # every ENABLED module-tier type, not just the baseline roster. Under [modules] governance (the
        # self-test manifest) the MA (maintainer_action) index is active; a duplicate targeting a valid
        # MA-1 resolves (pre-fix it was falsely rejected, the module index never scanned).
        ma_ok = ('schema = 1\n\n[[record]]\nid = "MA-1"\ntype = "maintainer_action"\n'
                 'status = "open"\ntitle = "x"\n')
        rootB2, mB2 = build_store(sources={"a.txt": src},
                                  extra={"maintainer_action.index.toml": ma_ok})
        dup_ma = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "target": "MA-1"}]}}
        check("B2-module-tier-duplicate-resolves-clean",
              stage_import(rootB2, ["a.txt"], dup_ma, now=NOW, run_nonce=NONCE).verdict == 0)
        # an id sitting in a module-tier index is SEEN by the union: a minted BI-1 colliding there is a
        # finding (pre-fix the module index was never scanned, so the collision escaped).
        ma_collide = ('schema = 1\n\n[[record]]\nid = "BI-1"\ntype = "maintainer_action"\n'
                      'status = "open"\ntitle = "x"\n')
        rootB2b, mB2b = build_store(sources={"a.txt": src},
                                    extra={"maintainer_action.index.toml": ma_collide})
        check("B2-minted-collides-in-module-index-finding",
              stage_import(rootB2b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 1)

        # MINOR: a `duplicate` targeting a valid ACTIVE worklog WL-n resolves (the existence set reads
        # worklog.toml via _worklog_ids, not a nonexistent worklog.index.toml).
        wl_active = ('schema = 1\n\n[[entry]]\nid = "WL-1"\ndate = "2026-01-01T00:00:00Z"\n'
                     'kind = "added"\nsummary = "seed"\nactor = { kind = "maintainer" }\n')
        rootWL, mWL = build_store(counters="BI=0,LF=0,WL=1", sources={"a.txt": src},
                                  extra={"worklog.toml": wl_active})
        dup_wl = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate", "target": "WL-1"}]}}
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
                                    extra=_sib_cand('schema = 1\n\n[[record]]\nid = "BI-2"\n'
                                                    'type = "backlog_item"\nstatus = "open"\ntitle = "x"\n'))
        check("B4-sibling-missing-envelope-cannot-eval",
              stage_import(rootB4a, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootB4b, mB4b = build_store(
            sources={"a.txt": src},
            extra=_sib_cand(bi_index_text().replace('type = "backlog_item"', 'type = "finding"')))
        check("B4-sibling-type-disagreement-cannot-eval",
              stage_import(rootB4b, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        rootB4c, mB4c = build_store(sources={"a.txt": src},
                                    extra=_sib_cand('schema = 1\nunknown_top = 1\nrecord = []\n'))
        check("B4-sibling-unknown-top-key-cannot-eval",
              stage_import(rootB4c, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 2)
        # a fully-valid sibling record is scanned (its id enters the union) and stages the non-colliding run.
        rootB4d, mB4d = build_store(sources={"a.txt": src}, extra=_sib_cand(bi_index_text("BI-9")))
        check("B4-valid-sibling-clean",
              stage_import(rootB4d, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict == 0)

        # B6: a plan row carrying a field its state does not use is a finding (untrusted plan fully
        # validated, spec 14.1): a `target` on an unmapped row, a `record` on a duplicate.
        b6a = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "target": "BI-1"}]}}
        rootB6, mB6 = build_store(sources={"a.txt": src})
        resB6 = stage_import(rootB6, ["a.txt"], b6a, now=NOW, run_nonce=NONCE)
        check("B6-unmapped-with-target-finding", resB6.verdict == 1)
        check("B6-unmapped-with-target-no-run", not (mB6 / "imports").exists())
        b6b = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate",
                                        "record": bi_candidate(), "target": "BI-1"}]}}
        rootB6b, mB6b = build_store(sources={"a.txt": src})
        check("B6-duplicate-with-record-finding",
              stage_import(rootB6b, ["a.txt"], b6b, now=NOW, run_nonce=NONCE).verdict == 1)

        # B7: a candidate linking to a NONEXISTENT record is a finding; candidate-to-candidate links are
        # unsupported (a link to an id minted only in this import set resolves nowhere in active/archive).
        link_bad = bi_candidate(); link_bad["links"] = [{"rel": "relates", "id": "BI-999"}]
        rootB7, mB7 = build_store(sources={"a.txt": src})
        resB7 = stage_import(rootB7, ["a.txt"],
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                                       "record": link_bad}]}}, now=NOW, run_nonce=NONCE)
        check("B7-dangling-candidate-link-finding", resB7.verdict == 1)
        check("B7-dangling-candidate-link-no-run", not (mB7 / "imports").exists())
        # a candidate linking to an EXISTING active record resolves clean (it mints BI-2 above BI=1).
        link_ok = bi_candidate(); link_ok["links"] = [{"rel": "relates", "id": "BI-1"}]
        rootB7b, mB7b = build_store(sources={"a.txt": src}, counters="BI=1,LF=0,WL=0",
                                    extra={"backlog_item.index.toml": bi_index_text()})
        check("B7-existing-link-clean",
              stage_import(rootB7b, ["a.txt"],
                           {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
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
        check("M2-post-resolution-oserror-no-run", not (mM2 / "imports").exists())

        # MINOR: an explicitly-empty worklog candidate list mints no worklog entry, so the version ledger
        # is NOT required (the release-boundary gate has no new WL number to check); the run stages clean.
        rootWLE, mWLE = build_store(sources={"a.txt": src})   # no version.toml
        wle_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]}, "worklog": []}
        check("empty-worklog-no-ledger-clean",
              stage_import(rootWLE, ["a.txt"], wle_plan, now=NOW, run_nonce=NONCE).verdict == 0)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-IMPORT SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-IMPORT SELF-TEST: PASS ({} import-staging checks)".format(checked[0]))
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
