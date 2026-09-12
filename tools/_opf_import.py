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

# An archive-year directory name is a 4-digit year (spec 12 rotates by year; ARCHIVE_PERIODS = ("year",)).
# A name outside this grammar is a phantom partition, refused rather than silently enumerated (CLASS 3(c)).
_ARCHIVE_YEAR_RE = re.compile(r"^[0-9]{4}$")

LF_TYPE = "legacy_fragment"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Closed plan keyset and closed fragment-row keyset.
_PLAN_KEYS = frozenset({"fragments", "worklog", "version"})
_FRAGMENT_ROW_KEYS = frozenset({"span", "state", "record", "target", "note"})


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
    """Enumerate staged ids across sibling runs under `<machine>/imports/`.

    Every candidate/fragments index is read through _record_ids. Baseline and importer records receive
    complete validation; ANY non-empty module-tier sibling index is CANNOT-EVALUATE until the module
    schemas release. Unknown index types and malformed sibling artefacts are refused. An incomplete
    sibling still enumerates artefacts already written. Symlinked/non-directory entries are refused.
    `skip_run_id` excludes this run during the post-claim re-check."""
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
    the AUTHORITATIVE manifest [devprocess].layout (guard-input-soundness); any layout other than `inline`,
    or an absent/malformed devprocess table, is CANNOT-EVALUATE, fail-closed, never a partial inline read of
    a non-inline store. (per-record support is a disclosed follow-on.)"""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    data = _read_toml(store_root_fd, manifest_rel)
    if data is None:
        raise _cannot("{}: the store manifest is absent; the storage layout cannot be determined "
                      "(spec 9)".format(manifest_rel))
    devprocess = data.get("devprocess")
    layout = devprocess.get("layout") if isinstance(devprocess, dict) else None
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
            # CLASS 4: type-validate EVERY plan/row field, not a subset. span (an int pair) and state (a
            # MAPPING_STATES member) are validated above; `record` and `target` are validated in their
            # state branches below; `note`, common to every state, must be a string when present. A
            # wrong-typed but EMITTABLE note (an int, a bool, a flat array) would otherwise stage verbatim
            # into plan.toml as promotion-ready, contradicting spec 14.1's fully-validated plan (test 13
            # covers only a non-emittable note refused at the emitter boundary).
            if "note" in row and not isinstance(row.get("note"), str):
                raise _finding("{}: fragment row `note` must be a string when present (spec 14.1)".format(
                    where))
            state_counts[state] += 1
            target = row.get("target")
            mapping_states[sp].append({"span": [start, end], "state": state, "target": target})

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
            {"span": [0, len(src)], "state": "mapped", "record": _big_cand}]}}
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

        # 3: dispatch deferral (F-373): `opf.py import` still exits 2 and stages nothing.
        opf_py = str(Path(__file__).resolve().parent / "opf.py")
        root4, machine4 = build_store(sources={"a.txt": src})
        cp = subprocess.run([sys.executable, "-I", "-B", opf_py, "import", "--root", str(root4)],
                            capture_output=True)
        check("3-verb-exits-2", cp.returncode == 2)
        check("3-verb-stages-nothing", not (machine4 / "imports").exists())

        # 4: R6 active collision: a fully-valid active index already carries the id next_id will mint (BI-1
        # above the BI=0 high-water) -> verdict 1, nothing written. The active record is now routed through
        # the complete contract (CLASS 3), so the collision is proven against a VALID store record (M11).
        root5, machine5 = build_store(sources={"a.txt": src},
                                      extra={"backlog_item.index.toml": bi_index_text()})
        res5 = stage_import(root5, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("4-active-collision-finding", res5.verdict == 1)
        check("4-active-collision-no-run", not (machine5 / "imports").exists())

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

        # F3: an id whose namespace does not match the index it sits in (a BI id in a done index) is a
        # malformed store under the full active contract (CLASS 3): CANNOT-EVALUATE, never an id-only read
        # (id-namespace binds type binds index file, spec 8.1/8.2, so a legitimately-placed id is only ever
        # in its own type's index; a legitimate same-index collision is covered by test 4). Still
        # fail-closed: nothing is staged.
        rootF3, machineF3 = build_store(sources={"a.txt": src},
                                        extra={"done.index.toml": 'schema = 1\n\n[[record]]\nid = "BI-1"\n'})
        resF3 = stage_import(rootF3, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE)
        check("F3-misplaced-id-cannot-eval", resF3.verdict == 2)
        check("F3-misplaced-id-no-run", not (machineF3 / "imports").exists())

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
        wl_nodate = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]},
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
                           {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
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
        dup_bad_active = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate",
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
        c4_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped", "note": 5}]}}
        rootC4, mC4 = build_store(sources={"a.txt": src})
        resC4 = stage_import(rootC4, ["a.txt"], c4_plan, now=NOW, run_nonce=NONCE)
        check("C4-wrong-typed-note-finding", resC4.verdict == 1)
        check("C4-wrong-typed-note-no-run", not (mC4 / "imports").exists())

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
        c5_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped", "record": c5_rec}]}}
        c5_before = copy.deepcopy(c5_plan)
        rootC5, mC5 = build_store(sources={"a.txt": src})
        resC5 = stage_import(rootC5, ["a.txt"], c5_plan, now=NOW, run_nonce=NONCE)
        check("C5-isolation-clean", resC5.verdict == 0)
        check("C5-input-plan-read-only", c5_plan == c5_before)
        if resC5.run_id:
            c5_run = mC5 / "imports" / resC5.run_id
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
        (mL / "imports").mkdir(); (mL / "archive").mkdir()
        resolL = _opf_store.resolve_store(rootL)
        fdL = _opf_store._open_store_root_fd(resolL.store_root, resolL.pointer_source != "default")
        _saved_listdir = os.listdir
        def _probe_listdir(relsuffix):
            try:
                _dir_entries_no_symlink(fdL, "{}/{}".format(resolL.machine_rel, relsuffix))
                return "no-raise"
            except _StageError as exc:
                return exc.verdict
            except OSError:
                return "escaped"
        try:
            os.listdir = (lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "simulated listdir error")))
            v_imp, v_arc = _probe_listdir("imports"), _probe_listdir("archive")
            try:
                _list_contained(fdL, "{}/imports".format(resolL.machine_rel))
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
        _journal._read_contained = (lambda root_fd, relpath:
                                    (_ for _ in ()).throw(OSError(errno.EIO, "simulated raw read-path error")))
        try:
            try:
                vbk = stage_import(rootBK, ["a.txt"], plan_mapped(len(src)), now=NOW, run_nonce=NONCE).verdict
            except OSError:
                vbk = "escaped"
        finally:
            _journal._read_contained = _saved_rc
        check("C1-boundary-backstop-cannot-eval", vbk == 2)
        check("C1-boundary-backstop-no-run", not (mBK / "imports").exists())

        # CLASS 3 (empty/unsupported sibling index): an index whose TYPE is unsupported is CANNOT-EVALUATE even
        # when its record list is EMPTY (an empty or unsupported index is never zero records). Pre-fix an empty
        # record list skipped the per-record type check, so the unsupported type slipped past as clean.
        sib_unsup = {"imports/{}/candidate/not_a_type.index.toml".format(sib_run): "schema = 1\nrecord = []\n"}
        rootC3u, mC3u = build_store(sources={"a.txt": src}, extra=sib_unsup)
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
        dup_phantom = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "duplicate",
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
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                                       "record": cand_full}]}}, now=NOW, run_nonce=NONCE)
        check("C6-fully-timestamped-clean", resC6.verdict == 0)
        if resC6.run_id:
            c6rec = tomllib.loads((mC6 / "imports" / resC6.run_id / "candidate"
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
        cand_vendor_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
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
        rootB1s, mB1s = build_store(sources={"a.txt": src}, extra=sibling_module)
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
                             {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                                       "record": cand_full_a}]}}, now=NOW, run_nonce=NONCE)
        check("M-a-clean", resMa.verdict == 0)
        if resMa.run_id:
            ma_rec = tomllib.loads((mMa / "imports" / resMa.run_id / "candidate"
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
        wl_vendor_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "unmapped"}]},
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
                "a.txt": [{"span": [0, len(src)], "state": "mapped", "record": mc_cand}]
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
            mc_run = mMc / "imports" / resMc.run_id / "candidate"
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
        g3_plan = {"fragments": {"a.txt": [{"span": [0, len(src)], "state": "mapped",
                                            "record": g3_cand}]}}
        rootG3, mG3 = build_store(sources={"a.txt": src})
        try:
            vG3 = stage_import(rootG3, ["a.txt"], g3_plan, now=NOW, run_nonce=NONCE).verdict
        except RecursionError:
            vG3 = "escaped"
        check("G3-deep-nested-plan-cannot-eval", vG3 == 2)
        check("G3-deep-nested-plan-no-run", not (mG3 / "imports").exists())

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
        check("G4-counters-regressed-no-run", not (mG4 / "imports").exists())
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
        lf_only["fragments"]["a.txt"] = [dict(span=[0, len(src)], state="unmapped")]
        rootG6, mG6 = build_store(counters="LF=0,WL=0", sources=dict([("a.txt", src)]),
                                  extra=dict([("backlog_item.index.toml", bi_index_text("BI-5"))]))
        resG6 = stage_import(rootG6, ["a.txt"], lf_only, now=NOW, run_nonce=NONCE)
        check("G6-counters-absent-namespace-regressed-cannot-eval", resG6.verdict == 2)
        check("G6-counters-absent-namespace-no-run", not (mG6 / "imports").exists())
        # the gate refuses ONLY a genuine untracked-namespace regression: an LF-only plan against a store
        # with NO seated BI id (BI legitimately absent from counters) still stages clean.
        rootG6b, mG6b = build_store(counters="LF=0,WL=0", sources=dict([("a.txt", src)]))
        check("G6-absent-namespace-no-durable-id-clean",
              stage_import(rootG6b, ["a.txt"], lf_only, now=NOW, run_nonce=NONCE).verdict == 0)

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
