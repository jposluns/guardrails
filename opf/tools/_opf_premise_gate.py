#!/usr/bin/env python3
"""Read side of the premise-review evaluator over one flow unit's declared records.

A library module (no entry point; tools/check_premise_gate.py carries its vectors). It reads one
resolved OPF store and derives, for one backlog item, the declared history the flow skill names
(opf/prompt-pack/flow/SKILL.md sections 5 and 7): the manifest, the active worklog and the
record-rotation archive read together, the unit's flow entries parsed under the section 5 grammar,
the writer's lifecycle lines, and the unit's records, all behind the section 8.6 firewall (only
clean WL-series entries and records by non-importer actors count; an `imported:` id or an
importer-authored entry or record is history, never read as live state, and never an obligation).

This draft carries the read side only. Its outcomes:

  exit 0 (allow)            the store read in full AND no clean non-importer `finding` links
                            `relates` to the item, so no recurrence can be owed under any reading
                            of the recurrence predicate; nothing else ever reaches exit 0 here
  exit 1 (refuse)           never returned by this draft (EXIT_REFUSE is declared for callers)
  exit 2 (cannot evaluate)  every other outcome, each with a stable `cause:` token, including a
                            unit that has any finding at all (cause `not-evaluated`): the
                            recurrence predicate and the clearance reading are later work, and a
                            store this draft has not fully evaluated is never allowed

Cannot-evaluate causes, fail-closed and never read as an empty history:
  store-unresolved       the store cannot be resolved, or its root cannot be opened; resolution
                         reads the manifest first, so on disk a missing or unreadable manifest
                         fails here, not as manifest-invalid
  manifest-invalid       the manifest fails validation, or this evaluator's own contained re-read
                         of the resolved store's manifest finds it absent or unreadable
  worklog-unreadable     the active worklog is missing, unreadable, not the declared shape,
                         malformed TOML, fails id/filename agreement, or holds no WL-series entry
  archive-unreadable     a rotation-archive bucket, its archive.toml, a declared rotated worklog
                         span, or a moved record's destination cannot be read as declared: a
                         worklog_moved row missing a required key or carrying an invalid value, a
                         bucket worklog file the bucket does not declare, declared spans without
                         that file, spans that do not cover exactly the WL numbers read from it,
                         or a moved record not seated exactly once at its destination
  index-unreadable       a <type>.index.toml this evaluator reads is absent, unreadable, or not a
                         schema-1 {schema, record} index
  record-invalid         a worklog entry or record fails its _opf_schema validation, carries a
                         non-WL id, or an id is seated twice
  duplicate-wl           two worklog entries carry the same WL number
  unknown-flow-event     a `flow ` detail line carries an event outside the section 5 set (the
                         section 5 run entry, `flow run ...`, is recognised and never read as a
                         stream entry)
  flow-line-malformed    a flow line breaks the section 5 grammar: a status outside its event's
                         set, a malformed scope= encoding, a missing required field (a `verdict`
                         without phase=, result= or rev= included), an unknown or repeated field,
                         or an entry that does not link `relates` to the item its line names
  item-missing           the item is not a clean non-importer backlog_item in the store read
  no-start-entry         the item has no clean `flow start` entry
  finding-create-missing a clean finding that links `relates` to the item has no clean
                         `opf-record create` lifecycle entry
  head-moved             the caller's expected head WL differs from the highest WL read
  not-evaluated          the store read in full, but the unit has findings and this draft carries
                         no recurrence predicate, so allowance is refused
  usage                  a malformed argument (an expected head that is not a canonical WL-n)

Boundary notes. The head WL is the highest number over every WL-series entry read, active and
archived, importer-authored entries included (an importer append still moves the store), while the
`imported:` series is separate and never counted. Zero rounds is a positive reading (a `flow
start` and no non-failed DISCOVERY `verdict` in the unit's clean history; the window count takes
every such verdict wherever it sits relative to the start entry), never a stand-in for an
unreadable store. A
lifecycle line that does not match the writer's fixed grammar is not an error here: it records no
creation, so a finding relying on it still fails closed as finding-create-missing. The snapshot
digest covers the bytes of every store file this evaluator read, in read order; the manifest is
hashed from its own contained read, one read before the validated model's, so a manifest swapped
between those two reads is a disclosed race this digest does not close.
"""
import sys
import hashlib
import os
import re
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402
import _opf_schema       # noqa: E402
import _opf_store        # noqa: E402
import _opf_worklog      # noqa: E402

EXIT_ALLOW = 0
EXIT_REFUSE = 1
EXIT_CANNOT = 2

# The record types this evaluator reads (flow skill section 7: the unit's item, its findings, and
# the decision records a later clearance reading needs; all are read and validated now so a store
# with an unreadable decision surface is never allowed).
RECORD_TYPES = ("backlog_item", "finding", "pending_decision", "maintainer_decision",
                "autonomous_decision")
INDEX_SUFFIX = ".index.toml"
ARCHIVE_DIRNAME = "archive"
ARCHIVE_MANIFEST_NAME = "archive.toml"
ARCHIVE_TOP_KEYS = frozenset({"schema", "moved", "worklog_moved"})
ARCHIVE_MOVED_KEYS = frozenset({"id", "type", "destination"})
ARCHIVE_WLMOVED_KEYS = frozenset({"span", "destination"})
INDEX_TOP_KEYS = frozenset({"schema", "record"})
WORKLOG_TOP_KEYS = frozenset({"schema", "entry"})

# The section 5 flow grammar: the closed event set, the status each event may carry, and the
# closed field vocabularies. `run` is outside the event set (the run entry is never a stream
# entry); unpark and rescope carry any status but `parked` and `done`.
EVENTS = ("start", "apply", "verdict", "converge", "park", "unpark", "rescope", "merge", "finish")
_RESUMABLE = frozenset({"drafting", "verifying", "fixing", "merging", "converged"})
EVENT_STATUS = {
    "start": frozenset({"drafting"}),
    "apply": frozenset({"verifying"}),
    "verdict": frozenset({"fixing", "verifying", "merging"}),
    "converge": frozenset({"converged"}),
    "park": frozenset({"parked"}),
    "unpark": _RESUMABLE,
    "rescope": _RESUMABLE,
    "merge": frozenset({"merging"}),
    "finish": frozenset({"done"}),
}
FIELD_KEYS = ("status", "branch", "lane", "scope", "tier", "phase", "result", "rev", "trigger")
TIERS = frozenset({"light", "substantive", "sensitive"})
PHASES = frozenset({"discovery", "verify", "merge-delta"})
RESULTS = frozenset({"clean", "findings", "failed"})
_LANE_RE = re.compile(r"^(?:advance-[1-9][0-9]*|harden|merge)\Z")
_CLEAN_ID_RE = re.compile(r"^[A-Z]{2}-[1-9][0-9]*\Z")
# The writer's fixed lifecycle grammar (OPF-SPEC.md section 8.8), matched on an entry's first
# detail line only.
_LIFECYCLE_RE = re.compile(r"^opf-record (create|transition) ([A-Z]{2}-[1-9][0-9]*) "
                           r"(?:(\S+) -> )?(\S+)\Z")


class PremiseGateCannot(Exception):
    """A cannot-evaluate outcome: the cause token and its detail; fail-closed, never empty."""

    def __init__(self, cause, detail):
        super().__init__("{}: {}".format(cause, detail))
        self.cause = cause
        self.detail = detail


class FlowLine:
    """One parsed section 5 stream entry: its WL number, event, item, and fields."""
    __slots__ = ("wl", "event", "item", "fields")

    def __init__(self, wl, event, item, fields):
        self.wl = wl
        self.event = event
        self.item = item
        self.fields = fields


class History:
    """The derived read: clean flow entries, lifecycle facts, and the head WL."""
    __slots__ = ("flows", "create_wl", "transitions", "head")

    def __init__(self, flows, create_wl, transitions, head):
        self.flows = flows
        self.create_wl = create_wl
        self.transitions = transitions
        self.head = head


class GateResult:
    """One evaluation outcome: the exit code, its cause (None on allow), and the report lines."""
    __slots__ = ("exit_code", "cause", "detail", "head", "snapshot", "lines")

    def __init__(self, exit_code, cause, detail, head, snapshot, lines):
        self.exit_code = exit_code
        self.cause = cause
        self.detail = detail
        self.head = head
        self.snapshot = snapshot
        self.lines = lines


class _Snapshot:
    """A digest over every store file read, length-framed (relpath, bytes) in read order."""

    def __init__(self):
        self._hash = hashlib.sha256(b"opf-premise-gate-snapshot-v1\n")

    def feed(self, relpath, raw):
        name = relpath.encode("utf-8")
        self._hash.update(str(len(name)).encode("ascii") + b":" + name)
        self._hash.update(str(len(raw)).encode("ascii") + b":" + raw)

    def digest(self):
        return self._hash.hexdigest()


# --- guards, one per refusal, each a module attribute a vector flip can remove -----------------

def _resolve(store):
    """The resolved store, or store-unresolved; never a silent default elsewhere."""
    res = _opf_store.resolve_store(store)
    if res.status != _opf_store.RESOLVED:
        raise PremiseGateCannot("store-unresolved", "{} ({})".format(res.status, res.detail))
    return res


def _require_fresh_wl(seen, number, ident):
    """Refuse the second entry on one WL number; the caller records the number after."""
    if number in seen:
        raise PremiseGateCannot("duplicate-wl", "two worklog entries carry {}".format(ident))


def _require_wl_head(seen):
    """At least one WL-series entry must have been read; the head is the highest number read."""
    if not seen:
        raise PremiseGateCannot("worklog-unreadable", "no WL-series entry was read")
    return max(seen)


def _require_flow_relates(entry, item, number):
    """A flow entry must link relates to the item its line names."""
    if not _links_relate(entry, item):
        raise PremiseGateCannot("flow-line-malformed",
                                "WL-{} flow line names {} but the entry does not link "
                                "relates to it".format(number, item))


def _ignored_by_firewall(ident, actor_kind):
    """The section 8.6 firewall: imported-series ids and importer-authored writes are history."""
    return ident.startswith(_opf_schema.IMPORTED_PREFIX) or actor_kind == "importer"


def _require_known_event(event):
    """True for a section 5 event; unknown-flow-event otherwise (never a silent skip)."""
    if event not in EVENTS:
        raise PremiseGateCannot("unknown-flow-event",
                                "flow line carries unknown event {!r}".format(event))
    return True


def _require_status_allowed(event, status, where):
    if status not in EVENT_STATUS[event]:
        raise PremiseGateCannot("flow-line-malformed",
                                "{}: status={!r} is outside the {} set".format(where, status, event))


def _check_scope(value, where):
    """The section 5 scope= encoding, decoded exactly; any other byte or escape is malformed."""
    if value == "":
        raise PremiseGateCannot("flow-line-malformed", where + ": scope= carries an empty path")
    for path in value.split(","):
        if path == "":
            raise PremiseGateCannot("flow-line-malformed", where + ": scope= carries an empty path")
        i = 0
        while i < len(path):
            ch = path[i]
            if ch == "%":
                hexpair = path[i + 1:i + 3]
                if len(hexpair) != 2 or any(c not in "0123456789ABCDEF" for c in hexpair):
                    raise PremiseGateCannot(
                        "flow-line-malformed",
                        where + ": scope= escape %" + hexpair + " is not two uppercase hex digits")
                byte = int(hexpair, 16)
                if 0x21 <= byte <= 0x7E and byte not in (0x25, 0x2C):
                    raise PremiseGateCannot(
                        "flow-line-malformed",
                        where + ": scope= escapes a byte the encoding leaves literal (%" + hexpair + ")")
                i += 3
            elif 0x21 <= ord(ch) <= 0x7E and ch != ",":
                i += 1
            else:
                raise PremiseGateCannot(
                    "flow-line-malformed",
                    where + ": scope= carries a byte outside the encoding ({!r})".format(ch))


def _require_verdict_fields(fields, where):
    """Every verdict carries phase=, result= and rev= (section 5)."""
    for key in ("phase", "result", "rev"):
        if key not in fields:
            raise PremiseGateCannot("flow-line-malformed",
                                    "{}: verdict entry is missing {}=".format(where, key))


def _require_start_fields(fields, where):
    """Every start entry carries lane=, scope=, branch= and tier= (section 5)."""
    for key in ("lane", "scope", "branch", "tier"):
        if key not in fields:
            raise PremiseGateCannot("flow-line-malformed",
                                    "{}: start entry is missing {}=".format(where, key))


def _parse_flow_line(first, wl):
    """One section 5 first line -> FlowLine, or None for the run entry; malformed fails closed."""
    where = "WL-{} flow line".format(wl)
    tokens = first.split(" ")
    event = tokens[1] if len(tokens) > 1 else ""
    if event == "run":
        return None                     # the section 5 run entry is never a stream entry
    if not _require_known_event(event):
        return None
    if len(tokens) < 3 or _CLEAN_ID_RE.fullmatch(tokens[2]) is None:
        raise PremiseGateCannot("flow-line-malformed",
                                where + ": the event is not followed by a clean item id")
    item = tokens[2]
    fields = {}
    for token in tokens[3:]:
        key, sep, value = token.partition("=")
        if not sep or key not in FIELD_KEYS or key in fields or value == "":
            raise PremiseGateCannot("flow-line-malformed",
                                    "{}: field {!r} is not one unrepeated key=value from the "
                                    "closed field set".format(where, token))
        fields[key] = value
    if "status" not in fields:
        raise PremiseGateCannot("flow-line-malformed", where + ": no status= field")
    _require_status_allowed(event, fields["status"], where)
    if "lane" in fields and _LANE_RE.fullmatch(fields["lane"]) is None:
        raise PremiseGateCannot("flow-line-malformed", where + ": lane= is outside the closed set")
    if "tier" in fields and fields["tier"] not in TIERS:
        raise PremiseGateCannot("flow-line-malformed", where + ": tier= is outside the closed set")
    if "phase" in fields and fields["phase"] not in PHASES:
        raise PremiseGateCannot("flow-line-malformed", where + ": phase= is outside the closed set")
    if "result" in fields and fields["result"] not in RESULTS:
        raise PremiseGateCannot("flow-line-malformed", where + ": result= is outside the closed set")
    if "scope" in fields:
        _check_scope(fields["scope"], where)
    if event == "start":
        _require_start_fields(fields, where)
    if event == "verdict":
        _require_verdict_fields(fields, where)
    if event == "converge":
        if fields.get("phase") != "discovery" or fields.get("lane") != "merge" \
                or fields.get("result") not in ("clean", "findings") or "rev" not in fields:
            raise PremiseGateCannot("flow-line-malformed",
                                    where + ": converge entry is missing its discovery round's "
                                    "phase=discovery, result=clean|findings, rev= or lane=merge")
    if event == "park" and "trigger" not in fields:
        raise PremiseGateCannot("flow-line-malformed", where + ": park entry carries no trigger=")
    return FlowLine(wl, event, item, fields)


def _validate_clean_record(row, rtype, where):
    """One clean record through _opf_schema; anything but VALID fails closed."""
    validation = _opf_schema.validate_record(row, expected_type=rtype)
    if validation.status != _opf_store.VALID:
        raise PremiseGateCannot("record-invalid", "{}: {}".format(
            where, "; ".join(validation.findings) or validation.status))


def _require_item(records, item):
    """The unit's item must be a clean non-importer backlog_item in the store read."""
    if item not in records["backlog_item"]:
        raise PremiseGateCannot("item-missing",
                                "{} is not a clean non-importer backlog_item here".format(item))


def _require_start(flows_for_item, item):
    """A unit's history begins at its `flow start` entry; none read means no evaluation."""
    if not any(flow.event == "start" for flow in flows_for_item):
        raise PremiseGateCannot("no-start-entry", "{} has no clean `flow start` entry".format(item))


def _require_finding_create(findings_for_item, create_wl):
    """Every clean finding of the unit must have its clean `opf-record create` entry."""
    for fid in sorted(findings_for_item):
        if fid not in create_wl:
            raise PremiseGateCannot("finding-create-missing",
                                    "{} has no clean `opf-record create` worklog entry".format(fid))


def _require_expected_head(expected, head):
    """The caller's freshness check: a moved head is never evaluated against stale intent."""
    if expected is not None and expected != head:
        raise PremiseGateCannot("head-moved",
                                "expected head WL-{} but the highest WL read is WL-{}".format(
                                    expected, head))


def _require_predicate_ready(findings_for_item, item):
    """This draft carries no recurrence predicate: a unit with findings is never allowed."""
    if findings_for_item:
        raise PremiseGateCannot("not-evaluated",
                                "{} has {} clean finding(s) and this draft carries no recurrence "
                                "predicate; allowance refused".format(item, len(findings_for_item)))


def _worklog_read_failed(exc):
    """A failed active-ledger read is a refusal, never an empty or assumed ledger."""
    raise PremiseGateCannot("worklog-unreadable", str(exc))


def _archive_stat_failed(exc):
    """A failed stat of the archive directory is a refusal, never an absent archive."""
    raise PremiseGateCannot("archive-unreadable", str(exc))


def _require_span_row(row, bucket, destination):
    """One worklog_moved row carries every required key with a valid value: span, a two-element
    [low, high] of canonical WL-n references whose low end does not exceed its high end, and
    destination, the bucket's own worklog file. Returns the WL numbers the span covers, both
    ends inclusive."""
    if not (isinstance(row, dict) and set(row) == ARCHIVE_WLMOVED_KEYS):
        raise PremiseGateCannot("archive-unreadable", bucket + ": malformed worklog_moved row")
    bounds = row["span"]
    refs = ([_opf_worklog._valid_wl_ref(bound) for bound in bounds]
            if isinstance(bounds, list) and len(bounds) == 2 else [None])
    if any(ref is None or ref[1] is not None for ref in refs):
        raise PremiseGateCannot("archive-unreadable",
                                bucket + ": worklog_moved span is not [low, high] canonical "
                                "WL-n references")
    if refs[0][0] > refs[1][0]:
        raise PremiseGateCannot("archive-unreadable",
                                bucket + ": worklog_moved span low end is above its high end")
    if row["destination"] != destination:
        raise PremiseGateCannot("archive-unreadable",
                                "{}: worklog_moved destination must be the bucket's own "
                                "worklog file {}".format(bucket, destination))
    return range(refs[0][0], refs[1][0] + 1)


def _require_declared_worklog(declared, document, bucket):
    """A bucket's worklog file and its declared spans must agree in both directions."""
    if document is None and declared:
        raise PremiseGateCannot("archive-unreadable",
                                bucket + " declares rotated spans but carries no worklog.toml")
    if document is not None and not declared:
        raise PremiseGateCannot("archive-unreadable",
                                bucket + " carries a worklog.toml it does not declare")


def _require_span_match(declared, entries, bucket):
    """The declared spans and the bucket's worklog entries must cover the same WL numbers."""
    read = set()
    for entry in entries:
        ref = _opf_worklog._valid_wl_ref(entry.get("id")) if isinstance(entry, dict) else None
        if ref is not None:
            read.add(ref[0])
    if read != declared:
        raise PremiseGateCannot("archive-unreadable",
                                bucket + ": the declared rotated spans do not match the WL "
                                "numbers read from its worklog.toml")


def _require_seated_once(seated, ident, dest):
    """A moved record must be seated exactly once at its declared destination."""
    if len(seated) != 1:
        raise PremiseGateCannot("archive-unreadable",
                                "moved id {} is not seated once in {}".format(ident, dest))
    return seated[0]


def _require_index_shape(data, rel, cause):
    """Each record index this evaluator reads must be a schema-1 {schema, record} index."""
    if not (isinstance(data, dict) and set(data) <= INDEX_TOP_KEYS
            and type(data.get("schema")) is int and data.get("schema") == 1
            and isinstance(data.get("record", []), list)):
        raise PremiseGateCannot(cause, rel + " is not a schema-1 record index")


# --- reads -------------------------------------------------------------------------------------

def _open_root(res):
    try:
        return _opf_store._open_store_root_fd(res.store_root, res.pointer_source != "default")
    except OSError as exc:
        raise PremiseGateCannot("store-unresolved",
                                "cannot open store root {} ({})".format(res.store_root, exc))


def _read_manifest(root_fd, machine_rel, snapshot):
    """The validated manifest model; its bytes are hashed from a contained read of its own."""
    rel = machine_rel + "/" + _opf_store.MANIFEST_NAME
    try:
        got = _opf_store._read_toml_contained(root_fd, rel, with_raw=True)
    except _opf_store.StoreError as exc:
        raise PremiseGateCannot("manifest-invalid", str(exc))
    if got is None:
        raise PremiseGateCannot("manifest-invalid", rel + " is absent")
    snapshot.feed(rel, got[0])
    try:
        return _opf_worklog.read_manifest_model_at(root_fd, machine_rel)
    except _opf_worklog.WorklogError as exc:
        raise PremiseGateCannot("manifest-invalid", str(exc))


def _load_worklog_history(root_fd, machine_rel, model, snapshot):
    """The manifest-selected active worklog, shape-checked; failures are never an empty ledger."""
    try:
        document = _opf_worklog.load_worklog_at(root_fd, machine_rel, manifest_model=model,
                                                on_read=snapshot.feed)
    except (_opf_store.StoreError, _journal.JournalError) as exc:
        # WorklogError subclasses StoreError; the generation-1 ledger read can also surface the
        # contained reader's own StoreError untranslated, and both fail closed here.
        document = _worklog_read_failed(exc)
    _require_worklog_shape(document, machine_rel, "worklog-unreadable")
    return document


def _require_worklog_shape(document, where, cause):
    if not (isinstance(document, dict) and set(document) <= WORKLOG_TOP_KEYS
            and type(document.get("schema")) is int and document.get("schema") == 1
            and isinstance(document.get("entry", []), list)):
        raise PremiseGateCannot(cause, "{}: not a schema-1 worklog ledger".format(where))


def _read_archive(root_fd, machine_rel, snapshot):
    """Every rotation-archive bucket read with the active store (flow skill section 5): the
    rotated worklog spans' entries, each span compared with what the bucket's worklog file
    actually holds, and the moved records of the types this evaluator reads."""
    arel = machine_rel + "/" + ARCHIVE_DIRNAME
    try:
        st = _journal._lstat_contained(root_fd, arel)
    except (_journal.JournalError, OSError) as exc:
        # _lstat_contained walks the contained parents no-follow and can raise JournalError or,
        # from its own descriptor duplication, a bare OSError; evaluate catches neither, so both
        # are mapped to the archive cause here, never an escaping crash.
        st = _archive_stat_failed(exc)
    if st is None:
        return [], {}
    if not stat.S_ISDIR(st.st_mode):
        raise PremiseGateCannot("archive-unreadable", arel + " is not a directory")
    try:
        dfd = _journal._open_dir_contained(root_fd, arel)
    except (_journal.JournalError, OSError) as exc:
        raise PremiseGateCannot("archive-unreadable", "{} cannot be opened ({})".format(arel, exc))
    try:
        names = sorted(os.listdir(dfd))
    except OSError as exc:
        raise PremiseGateCannot("archive-unreadable", "{} cannot be listed ({})".format(arel, exc))
    finally:
        os.close(dfd)
    entries, moved, dest_cache = [], {}, {}
    for name in names:
        bucket = arel + "/" + name
        data = _read_archive_toml(root_fd, bucket + "/" + ARCHIVE_MANIFEST_NAME, snapshot,
                                  required=True)
        if not (isinstance(data, dict) and set(data) <= ARCHIVE_TOP_KEYS
                and type(data.get("schema")) is int and data.get("schema") == 1):
            raise PremiseGateCannot("archive-unreadable",
                                    bucket + "/archive.toml is not a schema-1 archive manifest")
        spans = data.get("worklog_moved", [])
        rows = data.get("moved", [])
        if not isinstance(spans, list) or not isinstance(rows, list):
            raise PremiseGateCannot("archive-unreadable",
                                    bucket + "/archive.toml moved/worklog_moved must be arrays")
        wl_rel = ARCHIVE_DIRNAME + "/" + name + "/" + _opf_worklog.LEGACY_NAME
        declared = set()
        for span in spans:
            declared.update(_require_span_row(span, bucket, wl_rel))
        try:
            document = _opf_worklog.load_archive_worklog_at(
                root_fd, bucket + "/" + _opf_worklog.LEGACY_NAME, on_read=snapshot.feed)
        except (_opf_store.StoreError, _journal.JournalError) as exc:
            raise PremiseGateCannot("archive-unreadable", str(exc))
        _require_declared_worklog(declared, document, bucket)
        if document is not None:
            _require_worklog_shape(document, bucket, "archive-unreadable")
            _require_span_match(declared, document.get("entry", []), bucket)
            entries.extend(document.get("entry", []))
        for row in rows:
            if not (isinstance(row, dict) and set(row) <= ARCHIVE_MOVED_KEYS
                    and isinstance(row.get("id"), str) and isinstance(row.get("type"), str)
                    and isinstance(row.get("destination"), str)):
                raise PremiseGateCannot("archive-unreadable", bucket + ": malformed moved row")
            rtype = row["type"]
            if rtype not in RECORD_TYPES:
                continue
            dest = machine_rel + "/" + row["destination"]
            if dest not in dest_cache:
                data2 = _read_archive_toml(root_fd, dest, snapshot, required=True)
                _require_index_shape(data2, dest, "archive-unreadable")
                dest_cache[dest] = data2.get("record", [])
            seated = [r for r in dest_cache[dest]
                      if isinstance(r, dict) and r.get("id") == row["id"]]
            moved.setdefault(rtype, []).append(_require_seated_once(seated, row["id"], dest))
    return entries, moved


def _read_archive_toml(root_fd, rel, snapshot, required):
    try:
        got = _opf_store._read_toml_contained(root_fd, rel, with_raw=True)
    except _opf_store.StoreError as exc:
        raise PremiseGateCannot("archive-unreadable", str(exc))
    if got is None:
        if required:
            raise PremiseGateCannot("archive-unreadable", rel + " is absent")
        return None
    snapshot.feed(rel, got[0])
    return got[1]


def _collect_worklog(entries):
    """The clean WL-series history behind the firewall: flow entries, lifecycle facts, head."""
    seen = set()
    clean = []
    for position, entry in enumerate(entries):
        where = "worklog entry {}".format(position)
        if not isinstance(entry, dict):
            raise PremiseGateCannot("record-invalid", where + " is not a table")
        ident = entry.get("id")
        if not isinstance(ident, str):
            raise PremiseGateCannot("record-invalid", where + " carries no string id")
        if ident.startswith(_opf_schema.IMPORTED_PREFIX):
            continue                    # the imported series is a separate number line
        ref = _opf_worklog._valid_wl_ref(ident)
        if ref is None:
            raise PremiseGateCannot("record-invalid",
                                    where + ": {!r} is not a WL reference".format(ident))
        number = ref[0]
        _require_fresh_wl(seen, number, ident)
        seen.add(number)
        _validate_clean_record(entry, "worklog", ident)
        actor = entry.get("actor")
        kind = actor.get("kind") if isinstance(actor, dict) else None
        if _ignored_by_firewall(ident, kind):
            continue                    # importer-authored: validated, then kept out of live history
        clean.append((number, entry))
    head = _require_wl_head(seen)
    clean.sort(key=lambda pair: pair[0])
    flows, create_wl, transitions = [], {}, {}
    for number, entry in clean:
        detail = entry.get("detail")
        if not isinstance(detail, str):
            continue
        first = detail.split("\n", 1)[0]
        if first == "flow" or first.startswith("flow "):
            parsed = _parse_flow_line(first, number)
            if parsed is None:
                continue
            _require_flow_relates(entry, parsed.item, number)
            flows.append(parsed)
            continue
        match = _LIFECYCLE_RE.fullmatch(first)
        if match is not None:
            verb, rid = match.group(1), match.group(2)
            if verb == "create":
                create_wl.setdefault(rid, number)
            else:
                transitions.setdefault(rid, []).append((number, match.group(3), match.group(4)))
    return History(flows, create_wl, transitions, head)


def _links_relate(record, target):
    links = record.get("links")
    if not isinstance(links, list):
        return False
    return any(isinstance(link, dict) and link.get("rel") == "relates" and link.get("id") == target
               for link in links)


def _collect_records(root_fd, machine_rel, snapshot, archived):
    """Each read type's clean records by id, active index plus archive, behind the firewall."""
    records = {}
    for rtype in RECORD_TYPES:
        rel = machine_rel + "/" + rtype + INDEX_SUFFIX
        try:
            got = _opf_store._read_toml_contained(root_fd, rel, with_raw=True)
        except _opf_store.StoreError as exc:
            raise PremiseGateCannot("index-unreadable", str(exc))
        if got is None:
            raise PremiseGateCannot("index-unreadable", rel + " is absent")
        snapshot.feed(rel, got[0])
        data = got[1]
        _require_index_shape(data, rel, "index-unreadable")
        byid = {}
        for row in list(data.get("record", [])) + list(archived.get(rtype, [])):
            if not isinstance(row, dict):
                raise PremiseGateCannot("record-invalid", rel + " carries a non-table record")
            ident = row.get("id")
            if not isinstance(ident, str):
                raise PremiseGateCannot("record-invalid", rel + " carries a record with no string id")
            if ident.startswith(_opf_schema.IMPORTED_PREFIX):
                continue
            _validate_clean_record(row, rtype, ident)
            actor = row.get("actor")
            kind = actor.get("kind") if isinstance(actor, dict) else None
            if _ignored_by_firewall(ident, kind):
                continue
            if ident in byid:
                raise PremiseGateCannot("record-invalid", ident + " is seated twice")
            byid[ident] = row
        records[rtype] = byid
    return records


def _parse_expected_head(value):
    """None, or the canonical WL-n the caller expects; anything else is a usage refusal."""
    if value is None:
        return None
    ref = _opf_worklog._valid_wl_ref(value) if isinstance(value, str) else None
    if ref is None or ref[1] is not None:
        raise PremiseGateCannot("usage",
                                "expected head {!r} is not a canonical WL-n reference".format(value))
    return ref[0]


# --- the evaluation ----------------------------------------------------------------------------

def evaluate(store, item, expect_head_wl=None):
    """One read-only evaluation of `item` in the store at `store`; returns a GateResult."""
    try:
        if not isinstance(item, str) or _CLEAN_ID_RE.fullmatch(item) is None:
            raise PremiseGateCannot("usage", "item {!r} is not a clean record id".format(item))
        expected = _parse_expected_head(expect_head_wl)
        res = _resolve(store)
        snapshot = _Snapshot()
        root_fd = _open_root(res)
        try:
            model = _read_manifest(root_fd, res.machine_rel, snapshot)
            document = _load_worklog_history(root_fd, res.machine_rel, model, snapshot)
            archive_entries, archived = _read_archive(root_fd, res.machine_rel, snapshot)
            history = _collect_worklog(archive_entries + list(document.get("entry", [])))
            records = _collect_records(root_fd, res.machine_rel, snapshot, archived)
        finally:
            os.close(root_fd)
        _require_item(records, item)
        flows_for_item = [flow for flow in history.flows if flow.item == item]
        _require_start(flows_for_item, item)
        findings_for_item = sorted(
            fid for fid, row in records["finding"].items() if _links_relate(row, item))
        _require_finding_create(findings_for_item, history.create_wl)
        _require_expected_head(expected, history.head)
        windows = sum(1 for flow in flows_for_item
                      if flow.event == "verdict" and flow.fields.get("phase") == "discovery"
                      and flow.fields.get("result") != "failed")
        _require_predicate_ready(findings_for_item, item)
        lines = [
            "basis: windows={} findings=0 episodes=0".format(windows),
            "basis: no clean finding links relates to {}; nothing can be owed".format(item),
            "basis: class identity is declaration-only",
        ]
        return GateResult(EXIT_ALLOW, None, "", history.head, snapshot.digest(), lines)
    except PremiseGateCannot as exc:
        return GateResult(EXIT_CANNOT, exc.cause, exc.detail, None, None,
                          ["cause: {}: {}".format(exc.cause, exc.detail)])


def render(result, item):
    """The fixed report: the first line per exit, then the body lines."""
    if result.exit_code == EXIT_ALLOW:
        first = "premise-gate: allow {} head=WL-{} snapshot={}".format(
            item, result.head, result.snapshot)
    elif result.exit_code == EXIT_REFUSE:
        first = "premise-gate: refuse {} head=WL-{} snapshot={}".format(
            item, result.head, result.snapshot)
    else:
        first = "premise-gate: cannot-evaluate {}".format(item)
    return "\n".join([first] + list(result.lines)) + "\n"
