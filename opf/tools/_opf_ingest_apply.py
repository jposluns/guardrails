"""Ingest promotion coordinator (MIG-PR5 slice 1): promote a reviewed, ACCEPTED staged ingest run.

`apply_ingest` owns the ingest state machine; it shares acceptance validation, the frozen review snapshot,
and index helpers with `_opf_import`, and never calls `apply_import`. Sequence:

  1. read-only admission: UTC clock, run-id grammar, a co-located product/store root, activated homes 2;
  2. the store operation capability, then the ingest _journal writer lock (fixed order; a possibly-live
     owner refuses, never seized);
  3. under both: prior attempts classified (an open attempt refuses), the staging run located (ambiguous
     homes refuse), the frozen snapshot re-validated through the MIG-PR4b semantic gate, and the DURABLE
     acceptance required and validated (a green staged gate is NOT acceptance; a reject refuses);
  4. every action, candidate, and reference path checked against the canonical grammar (reject, never
     normalize), then live source bytes re-verified against the accepted digests and every destination
     confirmed absent;
  5. permanent ids reserved irrevocably (_opf_allocation) BEFORE the reversible publication;
  6. ONE journaled publication attempt: evidence bundle (review snapshot, migrate originals, evidence
     inventory, promotion receipt), keep/move/migrate actions, record indexes and manifest, then the
     terminal deletion of exactly this staging run.

Moves are exclusive-create of the destination from verified bytes plus a guarded remove of the source
whose accepted digest and mode are pinned into the journal (`source-poststate`, verified at capture under
the held locks), never shutil.move or an overwrite-capable rename. Keep leaves the source untouched and
registers its exact accepted [unmanaged].paths spelling.

DEFERRED to slice 2 and refused or disclosed here: the crash-recovery matrix (an open attempt refuses;
a stale journal lock refuses), concurrency tests, the full link/parent-substitution matrix, relocated,
nested, or external stores (refused), the worklog adapter (a WL draft refuses), D4/D5 preview composition
and view publication, import_status transitions (left unchanged), and `apply_import`/CLI routing (an
ingest run is still refused by `apply_import`). Production stays at SUPPORTED_HOMES 1, so this entry point
refuses every shipped store; the self-tests exercise it over synthetic activated fixtures.

Residual boundary (inherited from _journal): descriptor containment prevents path redirection; it does
not make a name check and the following unlink indivisible against an arbitrary same-user writer.
Detected substitution fails closed; cooperating writers must hold the shared operation lock.
"""
import datetime
import hashlib
import os
import stat
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal  # noqa: E402
import _opf_allocation  # noqa: E402
import _opf_emit  # noqa: E402
import _opf_import  # noqa: E402
import _opf_journal  # noqa: E402
import _opf_oplock  # noqa: E402
import _opf_schema  # noqa: E402
import _opf_store  # noqa: E402

KIND = "ingest"
OPERATION = "ingest-apply"
PROMOTION_NAME = "promotion.toml"
PROMOTION_FORMAT = "opf.ingest.promotion/v1"
EVIDENCE_INVENTORY_FORMAT = "opf.ingest.evidence-inventory/v1"
REVIEW_DIRNAME = "review"
ORIGINALS_DIRNAME = "originals"
SCHEMA = 1
CLEAN = _opf_import.CLEAN
FINDING = _opf_import.FINDING
CANNOT_EVALUATE = _opf_import.CANNOT_EVALUATE
ApplyResult = _opf_import.ApplyResult
_cannot = _opf_import._cannot
_finding = _opf_import._finding
_StageError = _opf_import._StageError

# Deterministic self-test seams, empty in production: name -> callable(**context).
_HOOKS = {}


def _hook(name, **context):
    fn = _HOOKS.get(name)
    if fn is not None:
        fn(**context)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class _RetainLock(Exception):
    """A publication attempt is left open (rollback incomplete); the journal lock is kept for recovery."""


# --- the path boundary: reject, never normalize --------------------------------------------------------

# Product-root names no action may read or write; `.working` is refused except for the one accepted
# migrate-original destination beneath this run's evidence home.
_CONTROL_TOPS = (_opf_store.WORKING_DIRNAME, _opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL) \
    + _opf_store.STORE_ROOT_CONTROL_DIRS


def _canonical(path, what):
    """The canonical contained grammar: no absolute, drive-qualified, backslash, control, empty, '.', or
    '..' spelling. A refused spelling is never repaired into a different path."""
    try:
        return _opf_store._home_file(path)
    except ValueError:
        raise _cannot("{} {!r} is not a canonical contained path (absolute, drive-qualified, '..', '.', "
                      "empty component, backslash, or control character); refused".format(what, path))


def _overlaps(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _outside_control(path, what):
    top = path.split("/", 1)[0]
    if any(_overlaps(path, c) for c in _CONTROL_TOPS) or top in _CONTROL_TOPS:
        raise _cannot("{} {!r} lies in a store control area; refused".format(what, path))
    return path


class _Plan:
    """The pure execution plan derived from the validated frozen snapshot, before any filesystem use."""
    __slots__ = ("keeps", "removals", "lf_records", "drafts", "demand", "evidence_home")


def _execution_plan(snapshot, frozen, run_id):
    """Derive keeps, removals, LF quarantine records, publishable drafts, and allocation demand, checking
    EVERY path field at this boundary before any of them reaches the filesystem."""
    plan = _Plan()
    binding = snapshot["binding"]
    plan.evidence_home = _opf_import._ingest_acceptance_home(run_id)
    if binding.get("evidence_home") != plan.evidence_home:
        raise _cannot("accepted evidence home does not name this run's durable home")
    actions = frozen.load_toml(_opf_import.INGEST_ACTIONS_NAME).get("action", [])
    plan.keeps = []
    for a in actions:
        if a.get("kind") == "keep":
            src = _outside_control(_canonical(a.get("source_path"), "keep source"), "keep source")
            unmanaged = _outside_control(_canonical(a.get("unmanaged_path"), "keep unmanaged path"),
                                         "keep unmanaged path")
            if unmanaged != src:
                raise _cannot("keep {!r} registers a different path {!r}; only a co-located store is "
                              "supported in this slice".format(src, unmanaged))
            plan.keeps.append(dict(path=src, sha256=a["sha256"], size=a["size"]))
        elif a.get("kind") != "move":
            raise _cannot("unknown ingest action kind {!r}".format(a.get("kind")))
    plan.removals = []
    for r in binding["source_removals"]:
        src = _outside_control(_canonical(r.get("resolved_source_path"), "removal source"), "removal source")
        _canonical(r.get("source_path"), "removal declared source")
        dest = _canonical(r.get("destination"), "removal destination")
        if r.get("disposition") == "migrate":
            if dest != plan.evidence_home + "/" + ORIGINALS_DIRNAME + "/" + src:
                raise _cannot("migrate original destination {!r} is not this run's accepted originals "
                              "home".format(dest))
        elif r.get("disposition") == "move":
            _outside_control(dest, "move destination")
        else:
            raise _cannot("unknown source removal disposition {!r}".format(r.get("disposition")))
        plan.removals.append(dict(path=src, dest=dest, disposition=r["disposition"], sha256=r["sha256"],
                                  size=r["size"]))
    sources = [k["path"] for k in plan.keeps] + [r["path"] for r in plan.removals]
    dests = [r["dest"] for r in plan.removals]
    for i, a in enumerate(sources + dests):
        for b in (sources + dests)[i + 1:]:
            if _overlaps(a, b):
                raise _cannot("action paths {!r} and {!r} coincide or nest; refused".format(a, b))
    roster = _opf_import._roster()
    lf_ns = roster[_opf_import.LF_TYPE].namespace
    plan.lf_records, plan.demand = [], []
    lf_index = frozen.load_toml("fragments/legacy_fragment.index.toml").get("record", [])
    for rec in lf_index:
        sp = _canonical(rec.get("source_path"), "quarantine record source")
        span = rec.get("span")
        fid = _opf_import._fragment_id(sp, rec.get("source_digest"), span[0], span[1])
        plan.lf_records.append((fid, rec))
        plan.demand.append(("lf:" + fid, lf_ns))
    plan.drafts = []
    for u in snapshot["units"]:
        if u["kind"] != "conversion" or u["authority"]["outcome"] != "conversion-reviewed":
            continue
        for d in u["authority"]["candidates"]:
            _canonical(d.get("source_path"), "candidate source")
            for ref in d["record"].get("refs", []) if isinstance(d.get("record"), dict) else []:
                if isinstance(ref, dict) and ref.get("kind") == "path":
                    _canonical(ref.get("locator"), "candidate path reference")
            rtype = d.get("type")
            if rtype == "worklog":
                raise _cannot("worklog drafts need the dedicated worklog adapter (deferred); nothing promoted")
            if rtype not in _opf_schema.BASELINE_TYPES or rtype != d["record"].get("type"):
                raise _cannot("candidate type {!r} is not a publishable baseline type".format(rtype))
            key = "draft:{}:{}:{}:{}".format(u["scope"], d["source_path"], d["importer_kind"], d["draft_ref"])
            plan.drafts.append((key, d))
            plan.demand.append((key, roster[rtype].namespace))
    return plan


# --- authority: the frozen snapshot and the durable acceptance ------------------------------------------

def _locate_run(root_fd, run_id, homes):
    """Exactly one homes location may hold the run; duplicates are ambiguous, never first-readable."""
    found = []
    for rel in _opf_import._import_run_locations(run_id, homes):
        st = _journal._lstat_contained(root_fd, rel)
        if st is not None:
            if not stat.S_ISDIR(st.st_mode):
                raise _cannot("staging location {} is not a directory".format(rel))
            found.append(rel)
    if len(found) != 1:
        raise _cannot("run {} has {} staging locations ({}); exactly one is required".format(
            run_id, len(found), ", ".join(found) or "none"))
    return found[0]


def _validated_snapshot(store_root, run_rel, homes):
    """The full staged-run gate, then ONE frozen byte snapshot re-validated through the MIG-PR4b semantic
    gate; everything apply publishes is materialized from these same bytes."""
    import check_opf_import as gate
    run_dir = os.path.join(str(store_root), run_rel)
    _opf_import._require_review_gate(run_dir, homes)
    try:
        rd = gate._RunDir(run_dir)
    except Exception as exc:  # noqa: BLE001  the gate's own located error, fail-closed
        raise _cannot("cannot open the staged run ({})".format(exc))
    try:
        if any(kind not in ("file", "dir") for kind in rd.tree.values()):
            raise _cannot("the staged run carries an entry that is neither a file nor a directory")
        frozen = _opf_import._freeze_ingest_bytes(rd)
    finally:
        rd.close()
    try:
        snapshot = _opf_import._ingest_snapshot(frozen, homes)
    except _StageError:
        raise
    except Exception as exc:  # noqa: BLE001  a gate that cannot evaluate never reads as a pass
        raise _cannot("ingest semantic gate could not evaluate the frozen snapshot ({!r})".format(exc))
    return frozen, snapshot


def _require_acceptance(root_fd, run_id, snapshot):
    """The DURABLE acceptance is the only authority: absence refuses even when every staged check passes
    (the gate grades an absent acceptance as "not yet reviewed"), and a recorded reject refuses."""
    record = _opf_import._read_ingest_acceptance(root_fd, run_id)
    if record is None:
        raise _cannot("run {} has no durable acceptance; a green staged gate is not acceptance (review "
                      "the run first); nothing promoted".format(run_id))
    raw, _st = _journal._read_contained(root_fd, _opf_import._ingest_acceptance_home(run_id) + "/"
                                        + _opf_import.ACCEPTANCE_NAME, require_single_link=True)
    try:
        acceptance = _opf_import._strict_json(raw)
    except (ValueError, RecursionError) as exc:
        raise _cannot("durable acceptance is malformed ({})".format(exc))
    if acceptance != record:
        raise _cannot("durable acceptance changed while it was read; nothing promoted")
    binding, complete, rejected = _opf_import.validate_ingest_acceptance(snapshot, acceptance)
    if binding or complete:
        raise _cannot("durable acceptance does not bind this frozen review ({})".format(
            "; ".join(binding + complete)))
    if rejected:
        raise _finding("the durable acceptance records a reject decision; promotion is refused (a fresh "
                       "plan is the resolution)")
    return raw


# --- live checks ------------------------------------------------------------------------------------------

def _live_sources(root_fd, plan):
    """Re-read every kept and removed source: a singly-linked regular file whose bytes and size equal the
    accepted digest. Returns {path: (bytes, mode)}."""
    live = {}
    for item in plan.keeps + plan.removals:
        path = item["path"]
        st = _journal._lstat_contained(root_fd, path)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _cannot("accepted source {!r} is missing or not a regular file".format(path))
        data, fst = _journal._read_contained(root_fd, path, require_single_link=True)
        if "sha256:" + _sha(data) != item["sha256"] or len(data) != item["size"]:
            raise _cannot("accepted source {!r} changed since review; nothing promoted".format(path))
        live[path] = (data, stat.S_IMODE(fst.st_mode))
    return live


def _missing_parents(root_fd, rel, planned):
    """The parent directories of `rel` that do not exist yet (outermost first). An existing component
    must be a real directory; a symlink or any other type refuses."""
    parts = rel.split("/")[:-1]
    missing = []
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if prefix in planned:
            continue
        st = _journal._lstat_contained(root_fd, prefix)
        if st is None:
            missing.append(prefix)
            planned.add(prefix)
        elif not stat.S_ISDIR(st.st_mode):
            raise _cannot("destination parent {!r} is not a real directory".format(prefix))
    return missing


def _require_absent(root_fd, rel, what):
    if _journal._lstat_contained(root_fd, rel) is not None:
        raise _cannot("{} {!r} is already occupied; nothing promoted".format(what, rel))


# --- publication --------------------------------------------------------------------------------------------

def _file_post(data, mode):
    return dict(kind="file", mode=mode, **{"content-sha256": _sha(data)})


def _destination_op(root_fd, rel, data, mode):
    """The ONE move/migrate destination primitive: refuse an occupied name now, and emit a journal create
    (prior absence recorded at capture, O_EXCL at apply) from the verified bytes. Never an overwrite."""
    _require_absent(root_fd, rel, "destination")
    return dict(op="create", path=rel, poststate=_file_post(data, mode))


def _pinned_remove(rel, data, mode):
    """Remove a file only while it still holds the verified bytes and mode; the journal verifies the pin
    at capture, under the held locks, before it takes the preimage."""
    return dict(op="remove", path=rel, poststate=dict(kind="absent"),
                **{"source-poststate": dict(kind="file", mode=mode, sha256=_sha(data))})


class _Ops:
    """An ordered op list, its staged bytes, and the directories it creates."""

    def __init__(self, root_fd):
        self.root_fd = root_fd
        self.ops = []
        self.content = {}
        self.planned = set()

    def mkdirs(self, rel):
        for d in _missing_parents(self.root_fd, rel, self.planned):
            self.ops.append(dict(op="mkdir", path=d, poststate=dict(kind="dir", mode=_opf_import.DIR_MODE)))

    def mkdir(self, rel):
        _require_absent(self.root_fd, rel, "evidence directory")
        self.mkdirs(rel)
        self.planned.add(rel)
        self.ops.append(dict(op="mkdir", path=rel, poststate=dict(kind="dir", mode=_opf_import.DIR_MODE)))

    def create(self, rel, data, mode=_opf_import.FILE_MODE, destination=False):
        self.mkdirs(rel)
        if destination:
            self.ops.append(_destination_op(self.root_fd, rel, data, mode))
        else:
            _require_absent(self.root_fd, rel, "publication target")
            self.ops.append(dict(op="create", path=rel, poststate=_file_post(data, mode)))
        self.content[rel] = data

    def publish(self, rel, data):
        """Write an existing machine file (its own mode kept) or create a new one."""
        st = _journal._lstat_contained(self.root_fd, rel)
        if st is None:
            return self.create(rel, data)
        if not stat.S_ISREG(st.st_mode):
            raise _cannot("publication target {!r} is not a regular file".format(rel))
        self.ops.append(dict(op="write", path=rel, poststate=dict(kind="file", **{"content-sha256": _sha(data)})))
        self.content[rel] = data


def _materialized_records(plan, reservation, roster, vendors):
    """Records keyed by type, each carrying its reserved permanent id; staged LF labels stay review
    evidence and their mapping is recorded in the reservation and the promotion receipt."""
    by_type = dict()
    for fid, rec in plan.lf_records:
        by_type.setdefault(_opf_import.LF_TYPE, []).append(dict(rec, id=reservation.ids["lf:" + fid]))
    for key, d in plan.drafts:
        rec = dict(d["record"])
        if "id" in rec:
            raise _cannot("draft {} carries an id; drafts are id-less until apply".format(key))
        rec["id"] = reservation.ids[key]
        rec.setdefault("updated_at", reservation.stamp)
        by_type.setdefault(d["type"], []).append(rec)
    for rtype, recs in by_type.items():
        for rec in recs:
            v = _opf_schema.validate_record(rec, expected_type=rtype, specs=roster, registered_vendors=vendors)
            if v.status != _opf_store.VALID:
                raise _cannot("materialized {} record {} is not valid ({})".format(
                    rtype, rec.get("id"), "; ".join(v.findings)))
    return by_type


def _manifest_bytes(root_fd, machine_rel, keeps):
    """The live manifest with each accepted keep registered in [unmanaged].paths, exactly as accepted."""
    rel = machine_rel + "/" + _opf_store.MANIFEST_NAME
    raw, _st = _journal._read_contained(root_fd, rel, require_single_link=True)
    try:
        model = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _cannot("cannot parse the live manifest ({})".format(exc))
    table = model.setdefault("unmanaged", dict())
    paths = table.setdefault("paths", [])
    if not (isinstance(table, dict) and isinstance(paths, list) and all(isinstance(p, str) for p in paths)):
        raise _cannot("the live manifest's [unmanaged].paths is malformed")
    for k in keeps:
        if k["path"] not in paths:
            paths.append(k["path"])
    try:
        return rel, _opf_emit.emit_checked(model).encode("utf-8")
    except Exception as exc:  # noqa: BLE001  a first-party emit failure refuses, never a partial manifest
        raise _cannot("cannot re-emit the manifest with the keep registrations ({})".format(exc))


def _staging_removals(root_fd, run_rel, frozen, ops):
    """Delete exactly the frozen staging tree: pinned file removals, then directories deepest first,
    then the run directory. Any entry the snapshot did not freeze refuses."""
    files = sorted(n for n, k in frozen.tree.items() if k == "file")
    dirs = sorted((n for n, k in frozen.tree.items() if k == "dir"), key=lambda n: (-n.count("/"), n))
    for name in files:
        rel = run_rel + "/" + name
        st = _journal._lstat_contained(root_fd, rel)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _cannot("staged file {} disappeared or changed type".format(rel))
        ops.ops.append(_pinned_remove(rel, frozen.read_bytes(name), stat.S_IMODE(st.st_mode)))
    for name in dirs:
        ops.ops.append(dict(op="rmdir", path=run_rel + "/" + name, poststate=dict(kind="absent")))
    ops.ops.append(dict(op="rmdir", path=run_rel, poststate=dict(kind="absent")))


def _publication_ops(root_fd, machine_rel, run_rel, run_id, attempt, plan, frozen, live, reservation,
                     acc_raw, binding, roster, vendors):
    """ONE journaled publication, in dependency order: evidence, destinations, source removals, records
    and manifest, evidence inventory, promotion receipt, then the terminal staging deletion."""
    ops = _Ops(root_fd)
    home = plan.evidence_home
    evidence = [(_opf_import.ACCEPTANCE_NAME, acc_raw)]
    ops.mkdir(home + "/" + REVIEW_DIRNAME)
    for name in sorted((n for n, k in frozen.tree.items() if k == "dir"), key=lambda n: (n.count("/"), n)):
        ops.mkdir(home + "/" + REVIEW_DIRNAME + "/" + name)
    for name in sorted(n for n, k in frozen.tree.items() if k == "file"):
        data = frozen.read_bytes(name)
        ops.create(home + "/" + REVIEW_DIRNAME + "/" + name, data)
        evidence.append((REVIEW_DIRNAME + "/" + name, data))
    for r in plan.removals:
        data, mode = live[r["path"]]
        ops.create(r["dest"], data, mode, destination=True)
        if r["disposition"] == "migrate":
            evidence.append((r["dest"][len(home) + 1:], data))
    for r in plan.removals:
        data, mode = live[r["path"]]
        ops.ops.append(_pinned_remove(r["path"], data, mode))
    published = []
    for rtype, recs in sorted(_materialized_records(plan, reservation, roster, vendors).items()):
        rel = "{}/{}.index.toml".format(machine_rel, rtype)
        live_index = _opf_import._read_toml(root_fd, rel)
        live_recs = [] if live_index is None else live_index.get("record")
        if not isinstance(live_recs, list):
            raise _cannot("live index {} is malformed".format(rel))
        data = _opf_import._emit_bytes(dict(schema=SCHEMA, record=list(live_recs) + recs), rel)
        ops.publish(rel, data)
        published.append(dict(path=rel, sha256=_sha(data)))
    if plan.keeps:
        rel, data = _manifest_bytes(root_fd, machine_rel, plan.keeps)
        ops.publish(rel, data)
        published.append(dict(path=rel, sha256=_sha(data)))
    inventory = _opf_import._emit_bytes(dict(
        format=EVIDENCE_INVENTORY_FORMAT, schema=SCHEMA, run_id=run_id,
        entry=[dict(path=p, sha256=_sha(d), size=len(d)) for p, d in sorted(evidence)]), "evidence inventory")
    ops.create(_opf_store.evidence_inventory("import", run_id), inventory)
    receipt = _opf_import._emit_bytes(dict(
        format=PROMOTION_FORMAT, schema=SCHEMA, run_id=run_id, attempt=attempt,
        reservation_digest=reservation.digest, binding=dict(binding),
        evidence_inventory_sha256=_sha(inventory),
        allocation=[dict(key=k, id=v) for k, v in reservation.ids.items()],
        staged_labels=[dict(key="lf:" + fid, staged_id=rec["id"]) for fid, rec in plan.lf_records],
        published=published, keep=[k["path"] for k in plan.keeps],
        removed=[dict(source=r["path"], destination=r["dest"], disposition=r["disposition"])
                 for r in plan.removals]), "promotion receipt")
    ops.create(home + "/" + PROMOTION_NAME, receipt)
    _staging_removals(root_fd, run_rel, frozen, ops)
    return ops


def _verify_completed(cap, root_fd, run_id, attempt):
    """A completed run is a no-op only while its immutable evidence verifies: the attempt's INTENT binds
    the promotion receipt's exact bytes, the receipt binds the reservation and the evidence inventory,
    and every inventoried payload still hashes to its entry. Live index bytes are not compared (later
    legitimate additions must not trigger republishing)."""
    intent = _opf_journal.attempt_intent(cap, KIND, run_id, attempt)
    home = _opf_import._ingest_acceptance_home(run_id)
    rec_rel = home + "/" + PROMOTION_NAME
    raw, _st = _journal._read_contained(root_fd, rec_rel, require_single_link=True)
    problem = _opf_import._txn_record_intent_problem(intent.get("ops"), rec_rel, raw)
    if problem:
        raise _cannot("completed run {}: {}".format(run_id, problem))
    try:
        receipt = tomllib.loads(raw.decode("utf-8"))
        if receipt["format"] != PROMOTION_FORMAT or receipt["run_id"] != run_id or receipt["attempt"] != attempt:
            raise _cannot("completed run {}: the promotion receipt does not bind this run".format(run_id))
        alloc, _st = _journal._read_contained(root_fd, _opf_store.allocation_record(KIND, run_id),
                                              require_single_link=True)
        if "sha256:" + _sha(alloc) != receipt["reservation_digest"]:
            raise _cannot("completed run {}: the reservation does not match its receipt".format(run_id))
        inv_raw, _st = _journal._read_contained(root_fd, _opf_store.evidence_inventory("import", run_id),
                                                require_single_link=True)
        if _sha(inv_raw) != receipt["evidence_inventory_sha256"]:
            raise _cannot("completed run {}: the evidence inventory does not match its receipt".format(run_id))
        for entry in tomllib.loads(inv_raw.decode("utf-8"))["entry"]:
            data, _st = _journal._read_contained(root_fd, _canonical(home + "/" + entry["path"], "evidence"),
                                                 require_single_link=True)
            if _sha(data) != entry["sha256"] or len(data) != entry["size"]:
                raise _cannot("completed run {}: evidence {} is corrupt".format(run_id, entry["path"]))
    except (UnicodeDecodeError, ValueError, RecursionError, KeyError, TypeError) as exc:
        raise _cannot("completed run {}: its retained evidence is malformed ({!r})".format(run_id, exc))
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, run_id, attempt), journal_rel=_opf_store.journal_root(KIND))
    return ApplyResult(CLEAN, [], promoted=True, outcome="noop_already_complete", restore_ref=ref)


def _post_verify(root_fd, plan, live, run_rel):
    """Live verification after COMPLETE: kept sources untouched, destinations hold the verified bytes,
    removed sources and the staging run absent. A failure is reported, never an automatic undo."""
    problems = []
    for k in plan.keeps:
        try:
            data, _st = _journal._read_contained(root_fd, k["path"])
        except _journal.JournalError as exc:
            data = exc
        if data != live[k["path"]][0]:
            problems.append("kept source {} changed".format(k["path"]))
    for r in plan.removals:
        if _journal._lstat_contained(root_fd, r["path"]) is not None:
            problems.append("removed source {} is present".format(r["path"]))
        try:
            data, _st = _journal._read_contained(root_fd, r["dest"])
        except _journal.JournalError as exc:
            data = exc
        if data != live[r["path"]][0]:
            problems.append("destination {} does not hold the verified bytes".format(r["dest"]))
    if _journal._lstat_contained(root_fd, run_rel) is not None:
        problems.append("staging run {} is still present".format(run_rel))
    return problems


def _require_colocated(product_root, resolution):
    """Slice 1 supports only a store whose root IS the product root, by opened-directory identity."""
    if resolution.pointer_source != "default":
        raise _cannot("only a co-located (default) store is supported; a relocated, nested, or external "
                      "store is refused before any reservation")
    a = _opf_store._open_root_fd(product_root)
    try:
        b = _opf_store._open_root_fd(resolution.store_root)
        try:
            sa, sb = os.fstat(a), os.fstat(b)
        finally:
            os.close(b)
    finally:
        os.close(a)
    if (sa.st_dev, sa.st_ino) != (sb.st_dev, sb.st_ino):
        raise _cannot("the store root is not the product root; refused")


def _apply_locked(cap, resolution, run_id, homes, now):
    root_fd = _opf_store._open_root_fd(resolution.store_root)
    try:
        states = _opf_journal.attempt_states(cap, KIND, run_id)
        if any(s == "open" for s in states.values()):
            raise _cannot("run {} has an open publication attempt; recovery is required first".format(run_id))
        done = sorted(n for n, s in states.items() if s == "complete")
        if done:
            return _verify_completed(cap, root_fd, run_id, done[-1])
        run_rel = _locate_run(root_fd, run_id, homes)
        frozen, snapshot = _validated_snapshot(resolution.store_root, run_rel, homes)
        acc_raw = _require_acceptance(root_fd, run_id, snapshot)
        plan = _execution_plan(snapshot, frozen, run_id)
        live = _live_sources(root_fd, plan)
        _hook("after-preflight", root=resolution.store_root, plan=plan)
        machine_rel = resolution.machine_rel
        roster = _opf_import._roster()
        vendors = _opf_import._registered_vendors(root_fd, machine_rel)
        live_ids = _opf_import._existing_id_set(root_fd, machine_rel, _opf_import._active_types(
            root_fd, machine_rel), roster, vendors)
        binding = dict(run_id=run_id, review_model_digest=snapshot["binding"]["review_model_digest"],
                       bundle_digest=snapshot["binding"]["bundle_digest"],
                       acceptance_digest="sha256:" + _sha(acc_raw))
        try:
            reservation = _opf_allocation.reserve_ingest_ids(
                cap, run_id, binding, plan.demand, _opf_import._rfc3339(now), live_ids)
        except _opf_allocation.AllocationError as exc:
            raise _cannot("id reservation refused: {}".format(exc))
        attempt = max(states, default=0) + 1
        ops = _publication_ops(root_fd, machine_rel, run_rel, run_id, attempt, plan, frozen, live,
                               reservation, acc_raw, binding, roster, vendors)
        _hook("before-publication", root=resolution.store_root, plan=plan)
        ref = dict(txn_id=_opf_journal.attempt_txn(KIND, run_id, attempt), journal_rel=_opf_store.journal_root(KIND))
        try:
            _opf_journal.run_attempt_transaction(cap, KIND, run_id, attempt, ops.ops,
                                                 lambda op: ops.content[op["path"]])
        except _journal.JournalError as exc:
            state = _opf_journal.attempt_states(cap, KIND, run_id).get(attempt, "nothing-opened")
            if state in ("nothing-opened", "rolled-back"):
                return ApplyResult(CANNOT_EVALUATE, ["publication aborted and rolled back ({}); the reserved "
                                                     "ids stay consumed and a retry reuses them".format(exc)],
                                   promoted=False, outcome="aborted", restore_ref=ref)
            raise _RetainLock("publication failed and its rollback did not complete ({})".format(exc))
        problems = _post_verify(root_fd, plan, live, run_rel)
        if problems:
            return ApplyResult(CANNOT_EVALUATE, ["the promotion committed but live verification failed ({}); "
                                                 "inspect the retained journal".format("; ".join(problems))],
                               promoted=True, outcome="promoted", restore_ref=ref)
        return ApplyResult(CLEAN, [], promoted=True, outcome="promoted", restore_ref=ref)
    finally:
        _journal._close_fd_quietly(root_fd)


def apply_ingest(product_root, run_id, *, now=None):
    """Promote the reviewed, accepted staged ingest run `run_id`. Returns an ApplyResult; `promoted` and
    `outcome` are read from the result, never inferred from the verdict."""
    try:
        _journal.require_containment()
        _opf_import._require_utc(now)
        if not (isinstance(run_id, str) and _opf_import._RUN_ID_RE.match(run_id)):
            raise _cannot("run-id {!r} does not match the run-id grammar".format(run_id))
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path")
        resolution = _opf_import._resolve_store_for_review(product_root)
        _require_colocated(product_root, resolution)
        homes = _opf_import._require_ingest_homes(_opf_import._store_homes(resolution))
        cap = _opf_oplock.acquire_operation(str(resolution.store_root), OPERATION)
    except _StageError as exc:
        return ApplyResult(exc.verdict, [exc.message], promoted=False, outcome="aborted")
    except (_journal.JournalError, _opf_oplock.OpLockError, OSError, ValueError) as exc:
        return ApplyResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)], promoted=False, outcome="aborted")
    locked = retain = False
    try:
        _opf_journal.acquire_writer_lock(cap, KIND)
        locked = True
        return _apply_locked(cap, resolution, run_id, homes, now)
    except _RetainLock as exc:
        retain = True
        return ApplyResult(CANNOT_EVALUATE, ["{}; the journal lock is retained for recovery".format(exc)],
                           promoted=False, outcome="aborted")
    except _StageError as exc:
        return ApplyResult(exc.verdict, [exc.message], promoted=False,
                           outcome="rejected" if exc.verdict == FINDING else "aborted")
    except (_journal.JournalError, _opf_oplock.OpLockError, OSError, RecursionError) as exc:
        return ApplyResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)], promoted=False, outcome="aborted")
    finally:
        # A release failure never overturns a formed result; a lock left behind refuses the next apply.
        if locked and not retain:
            try:
                _opf_journal.release_writer_lock(cap, KIND)
            except (_journal.JournalError, OSError):
                pass
        try:
            _opf_oplock.release_operation(cap)
        except (_opf_oplock.OpLockError, OSError):
            pass


# --- self-test (slice 1: happy path, retry monotonicity, core-guard discriminators) ------------------

_NOW = datetime.datetime(2026, 9, 10, 12, tzinfo=datetime.timezone.utc)
_MIXED = (("legacy/keep.md", "keepme\n", "keep"), ("legacy/move.md", "moveme\n", "move"),
          ("legacy/mig.md", "- [ ] t\n", "migrate"))


def _st_build(base, name, rows=_MIXED, capture=True, reject=False):
    """A synthetic activated store with one staged ingest run, planned and (optionally) reviewed through
    the real planner and review capture. Never `opf init`. Returns (root, run_id, run_path)."""
    import _opf_ingest as ingest
    import check_opf_import as gate
    root = base / name
    machine = root / ".working" / "toml"
    machine.mkdir(parents=True)
    (machine / "manifest.toml").write_text("\n".join([
        "[opf]", 'standard = "opf"', 'spec_version = "' + _opf_store.SUPPORTED_SPEC_VERSION + '"',
        'layout = "inline"', 'posture = "required"', 'import_status = "none"', "", "[store]",
        'sync_target = ""', "", "[modules]", "governance = true", "", "[types.backlog_item]",
        'namespace = "BI"', "", "[vendors]", "registered = []"]) + "\n", encoding="utf-8")
    (machine / "counters.toml").write_text("schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n",
                                           encoding="utf-8")
    by = dict()
    for path, text, dispo in rows:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
        by[path] = dispo
    found = ingest.detect(root, include=sorted(by))
    payload = dict(format=ingest.WORKSHEET_FORMAT, schema=SCHEMA,
                   row=[dict(r, disposition=by[r["source_path"]], note="") for r in found.rows])
    ws = dict(payload, worksheet_digest="sha256:" + _sha(ingest._worksheet_bytes(payload)))
    options = []
    for r in ws["row"]:
        if by[r["source_path"]] == "migrate":
            options.append(dict(scope=r["scope"], source_path=r["source_path"], importer_kind="github-tasklist"))
        elif by[r["source_path"]] == "move":
            options.append(dict(scope=r["scope"], source_path=r["source_path"]))
    planned = ingest.plan_ingest(root, ws, dict(format=ingest.OPTIONS_FORMAT, schema=SCHEMA, option=options),
                                 include=sorted(by), now=_NOW, run_nonce="mig-pr5-" + name)
    if planned.verdict != CLEAN:
        raise RuntimeError("fixture plan failed: {}".format(planned.findings))
    run = root / _opf_import.IMPORTS_REL / planned.run_id
    with _opf_import._self_test_homes2_active(root):
        (root / _opf_import._ingest_acceptance_home(run.name)).mkdir(parents=True)
        if capture:
            rd = gate._RunDir(run)
            try:
                snap = _opf_import._ingest_snapshot(rd, 2)
            finally:
                rd.close()
            env = _opf_import._ingest_review_envelope(snap)
            units = [dict(d, decision="accept", note="") for d in snap["units"]]
            if reject:
                units[0]["decision"] = "reject"
            reviewed = _opf_import.review_import(
                root, run.name, actor="reviewer", now=_NOW,
                decisions=[dict(d, decision="accept", note="") for d in env["decisions"]],
                ingest=dict(env["ingest"], units=units), clock=lambda: _NOW)
            if reviewed.verdict != CLEAN:
                raise RuntimeError("fixture review failed: {}".format(reviewed.findings))
    return root, run.name, run


def _st_tree(root):
    """relpath -> bytes for every regular file (the byte snapshot unchanged-state assertions use)."""
    out = dict()
    for dirpath, _dirs, names in os.walk(str(root)):
        for n in names:
            p = os.path.join(dirpath, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, str(root))] = fh.read()
    return out


def _st_counters(root):
    return tomllib.loads((root / ".working/toml/counters.toml").read_text(encoding="utf-8"))["counters"]


def _st_reservation(root, run_id):
    p = root / _opf_store.allocation_record(KIND, run_id)
    return p.read_bytes() if p.exists() else None


def _t_happy_path(base, check):
    """A reviewed, accepted run promotes: ids minted under both locks, keep/move/migrate executed, the
    evidence bundle written, staging reclaimed; a re-apply is a verified no-op that mints nothing."""
    root, rid, run = _st_build(base, "happy")
    # Another run consumed LF numbers after this one was planned: permanent ids come from the live
    # counters under the lock, never from the staged LF labels (LF-1..3) the review saw.
    (root / ".working/toml/counters.toml").write_text("schema = 1\n\n[counters]\nBI = 0\nLF = 10\nWL = 0\n",
                                                      encoding="utf-8")
    seen, caps = [], []
    real_mint = _opf_allocation._mint_ids
    real_acquire = _opf_oplock.acquire_operation

    def observed_mint(*args, **kwargs):
        seen.append((_opf_journal.writer_lock_held(caps[-1], KIND), not caps[-1]._released))
        return real_mint(*args, **kwargs)

    def remember(*args, **kwargs):
        caps.append(real_acquire(*args, **kwargs))
        return caps[-1]

    from unittest.mock import patch
    with _opf_import._self_test_homes2_active(root):
        with patch.object(_opf_allocation, "_mint_ids", observed_mint), \
                patch.object(_opf_oplock, "acquire_operation", remember):
            result = apply_ingest(root, rid, now=_NOW)
        manifest = tomllib.loads((root / ".working/toml/manifest.toml").read_text(encoding="utf-8"))
        before_noop = _st_tree(root)
        again = apply_ingest(root, rid, now=_NOW)
        after_noop = _st_tree(root)
    home = root / _opf_import._ingest_acceptance_home(rid)
    check("happy-promoted", result.verdict == CLEAN and result.promoted and result.outcome == "promoted")
    if not result.promoted:
        return
    # Flip: minting outside the held capability or journal lock records a False here (or refuses).
    check("happy-mint-under-both-locks", seen == [(True, True)])
    check("happy-counters-advanced", _st_counters(root) == dict(BI=1, LF=13, WL=0))
    bi = tomllib.loads((root / ".working/toml/backlog_item.index.toml").read_text(encoding="utf-8"))
    lf = tomllib.loads((root / ".working/toml/legacy_fragment.index.toml").read_text(encoding="utf-8"))
    check("happy-records-published", [r["id"] for r in bi["record"]] == ["BI-1"]
          and sorted(r["id"] for r in lf["record"]) == ["LF-11", "LF-12", "LF-13"]
          and all(r["status"] == "quarantined" for r in lf["record"]))
    check("happy-keep-untouched", (root / "legacy/keep.md").read_bytes() == b"keepme\n"
          and manifest.get("unmanaged", dict()).get("paths") == ["legacy/keep.md"])
    check("happy-move", (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
          and not (root / "legacy/move.md").exists())
    check("happy-migrate-original", (home / "originals/legacy/mig.md").read_bytes() == b"- [ ] t\n"
          and not (root / "legacy/mig.md").exists())
    check("happy-evidence", (home / PROMOTION_NAME).is_file() and (home / "inventory.toml").is_file()
          and (home / REVIEW_DIRNAME / "ingest-review.toml").is_file()
          and (home / _opf_import.ACCEPTANCE_NAME).is_file())
    check("happy-staging-reclaimed", not run.exists())
    check("happy-noop", again.verdict == CLEAN and again.outcome == "noop_already_complete"
          and before_noop == after_noop)


def _t_retry_monotonic(base, check):
    """A failed publication leaves its reservation consumed; a retry with identical bindings reuses the
    SAME allocation, mints nothing, and counters never regress, even when counters were rolled back."""
    root, rid, run = _st_build(base, "retry")
    collide = root / ".archive/legacy/move.md"

    def plant(**_ctx):
        collide.parent.mkdir(parents=True, exist_ok=True)
        collide.write_bytes(b"foreign\n")

    with _opf_import._self_test_homes2_active(root):
        _HOOKS["before-publication"] = plant
        try:
            first = apply_ingest(root, rid, now=_NOW)
        finally:
            _HOOKS.pop("before-publication", None)
        reserved = _st_reservation(root, rid)
        counters_after_first = _st_counters(root)
        check("retry-first-aborted", first.promoted is False and first.outcome == "aborted"
              and reserved is not None and counters_after_first == dict(BI=1, LF=3, WL=0)
              and collide.read_bytes() == b"foreign\n" and (root / "legacy/move.md").is_file())
        collide.unlink()
        # A counters file rolled back beneath the durable reservation must not free its numbers.
        (root / ".working/toml/counters.toml").write_text(
            "schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n", encoding="utf-8")
        later = _NOW + datetime.timedelta(hours=1)
        second = apply_ingest(root, rid, now=later)
    check("retry-second-promoted", second.promoted is True)
    if not second.promoted:
        return
    bi = tomllib.loads((root / ".working/toml/backlog_item.index.toml").read_text(encoding="utf-8"))
    check("retry-promoted", second.verdict == CLEAN and second.promoted
          and second.restore_ref["txn_id"].endswith(".a0002"))
    # Flip: allocating anew on retry mints BI-2/LF-4.. (or refuses on the existing record).
    check("retry-same-allocation", [r["id"] for r in bi["record"]] == ["BI-1"]
          and bi["record"][0]["updated_at"] == _opf_import._rfc3339(_NOW)
          and _st_reservation(root, rid) == reserved)
    check("retry-no-regression", _st_counters(root) == counters_after_first)


def _unchanged(root, before, rid):
    """Nothing but the operation lock's own persistent anchor appeared, and nothing was reserved."""
    def content(tree):
        return dict((k, v) for k, v in tree.items()
                    if not k.startswith(_opf_oplock.CONTROL_DIRNAME + os.sep))
    return content(_st_tree(root)) == content(before) and _st_reservation(root, rid) is None


def _t_unreviewed_refused(base, check):
    """A genuinely valid staged run passes its semantic gate with acceptance absent, and apply still
    refuses without reserving; a present reject decision refuses too."""
    import check_opf_import as gate
    root, rid, run = _st_build(base, "unreviewed", capture=False)
    with _opf_import._self_test_homes2_active(root):
        graded = gate.check_staged_run(run, homes=2)
        before = _st_tree(root)
        result = apply_ingest(root, rid, now=_NOW)
        check("unreviewed-gate-green", set(graded) == set(gate.EXPECTED_CHECKS)
              and all(ok for ok, _d in graded.values()))
        # Flip: replacing the explicit acceptance requirement with gate success promotes this run.
        check("unreviewed-refused", result.promoted is False and result.verdict == CANNOT_EVALUATE
              and _unchanged(root, before, rid))
    root, rid, run = _st_build(base, "rejected", reject=True)
    with _opf_import._self_test_homes2_active(root):
        before = _st_tree(root)
        result = apply_ingest(root, rid, now=_NOW)
        # Flip: dropping the rejection check promotes a run a reviewer rejected.
        check("rejected-refused", result.promoted is False and result.outcome == "rejected"
              and _unchanged(root, before, rid))


def _t_traversal_refused(base, check):
    """Every traversal, absolute, alias, drive, backslash, control, or control-area spelling in an action,
    candidate, or quarantine path refuses BEFORE any reservation. The vectors enter at the execution-plan
    boundary, past the acceptance binding that would otherwise mask a missing boundary check."""
    import copy
    from unittest.mock import patch
    root, rid, run = _st_build(base, "traversal")
    sentinel = base / "outside.md"
    sentinel.write_bytes(b"sentinel\n")
    real = _execution_plan
    spellings = ("../escape.md", "/abs/escape.md", "legacy/./x.md", "legacy\\x.md", "C:/x.md",
                 "legacy//x.md", "legacy/\x01x.md", "", ".git/hooks/post-commit", ".working/toml/x.md")

    def tampered(field, value):
        def plan(snapshot, frozen, run_id):
            snap = copy.deepcopy(snapshot)
            data = dict((n, frozen.read_bytes(n)) for n, k in frozen.tree.items() if k == "file")
            if field == "destination":
                [r for r in snap["binding"]["source_removals"] if r["disposition"] == "move"][0][field] = value
            elif field == "candidate":
                [u for u in snap["units"] if u["kind"] == "conversion"][0]["authority"]["candidates"][0][
                    "source_path"] = value
            elif field == "keep":
                actions = frozen.load_toml(_opf_import.INGEST_ACTIONS_NAME)
                keep = [a for a in actions["action"] if a["kind"] == "keep"][0]
                keep["source_path"] = keep["unmanaged_path"] = value
                data[_opf_import.INGEST_ACTIONS_NAME] = _opf_import._emit_bytes(actions, "actions")
            elif field == "lf":
                idx = frozen.load_toml("fragments/legacy_fragment.index.toml")
                idx["record"][0]["source_path"] = value
                data["fragments/legacy_fragment.index.toml"] = _opf_import._emit_bytes(idx, "lf")

            class Frozen:
                tree = frozen.tree
                path = frozen.path

                def read_bytes(self, name):
                    return data[name]

                def load_toml(self, name):
                    return tomllib.loads(data[name].decode("utf-8"))
            return real(snap, Frozen(), run_id)
        return plan

    vectors = [("destination", s) for s in spellings]
    vectors += [(f, "../escape.md") for f in ("candidate", "keep", "lf")]
    for i, (field, value) in enumerate(vectors):
        with _opf_import._self_test_homes2_active(root):
            before = _st_tree(root)
            with patch.object(sys.modules[__name__], "_execution_plan", tampered(field, value)):
                result = apply_ingest(root, rid, now=_NOW)
            unchanged = _unchanged(root, before, rid)
        # Flip: without the boundary check the reservation advances counters, or the path is used,
        # before anything else refuses. (A keep source is also refused by the journal's own contained
        # reader before reservation: defence in depth, so that vector is not a sole discriminator.)
        check("traversal-{}-{!r}".format(field, value), result.promoted is False
              and result.verdict == CANNOT_EVALUATE and unchanged and sentinel.read_bytes() == b"sentinel\n")
        if not unchanged:   # a changed store would contaminate the next vector: rebuild it
            root, rid, run = _st_build(base, "traversal-{}".format(i))


def _t_mint_move_toctou(base, check):
    """The mint+move race is closed: a destination occupied after preflight is preserved with the
    source (exclusive create, never an overwrite), and allocation refuses outside the held journal lock."""
    root, rid, run = _st_build(base, "race")
    dest = root / ".archive/legacy/move.md"

    def plant(**_ctx):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"arrived after preflight\n")

    with _opf_import._self_test_homes2_active(root):
        _HOOKS["after-preflight"] = plant
        try:
            result = apply_ingest(root, rid, now=_NOW)
        finally:
            _HOOKS.pop("after-preflight", None)
        # Flip: an overwrite-capable destination primitive replaces the arrived file and removes the source.
        check("race-destination-preserved", result.promoted is False
              and dest.read_bytes() == b"arrived after preflight\n"
              and (root / "legacy/move.md").read_bytes() == b"moveme\n" and run.is_dir())
        counters = (root / ".working/toml/counters.toml").read_bytes()
        cap = _opf_oplock.acquire_operation(str(root), OPERATION)
        try:
            # The capability alone is not enough: the journal writer lock is required too.
            try:
                _opf_allocation.reserve_ingest_ids(
                    cap, "imp-20260910T120000Z-0000000000000001",
                    dict(run_id="imp-20260910T120000Z-0000000000000001", review_model_digest="x",
                         bundle_digest="x", acceptance_digest="x"), [("k", "BI")], "stamp", set())
                refused = False
            except _opf_allocation.AllocationError:
                refused = True
        finally:
            _opf_oplock.release_operation(cap)
        # Flip: dropping the journal-lock requirement mints BI here and advances counters.
        check("race-mint-requires-journal-lock", refused
              and (root / ".working/toml/counters.toml").read_bytes() == counters)


TESTS = (("happy-path", _t_happy_path), ("retry-monotonic", _t_retry_monotonic),
         ("unreviewed-refused", _t_unreviewed_refused), ("traversal-refused", _t_traversal_refused),
         ("mint-move-toctou", _t_mint_move_toctou))


def self_test(only=None):
    """Run the slice-1 vectors in a private temporary tree, report the executed test identities and the
    check count, and return 0 (pass), 1 (a failed check), or 2 (a harness error)."""
    import shutil
    import tempfile
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-INGEST-APPLY SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    failures, count, ran = [], [0], []

    def check(label, cond):
        count[0] += 1
        if not cond:
            failures.append(label)

    base = Path(tempfile.mkdtemp(prefix="opf-ingest-apply-selftest-")).resolve()
    try:
        for name, fn in TESTS:
            if only is None or name in only:
                fn(base, check)
                ran.append(name)
    except Exception as exc:  # noqa: BLE001  a harness failure is reported as exit 2, never a pass
        print("OPF-INGEST-APPLY SELF-TEST ERROR: {!r}".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(str(base), ignore_errors=True)
    print("OPF-INGEST-APPLY SELF-TEST: ran {} ({} checks)".format(", ".join(ran), count[0]))
    if failures or not ran:
        print("OPF-INGEST-APPLY SELF-TEST FAILED: {}".format(", ".join(failures) or "no test ran"),
              file=sys.stderr)
        return 1
    print("OPF-INGEST-APPLY SELF-TEST PASSED")
    return 0


# --- in-tree red-on-revert discrimination (mirrors the PR4 observer gate) ------------------------------
#
# The self-test above proves the guards accept a correct run; it cannot prove each guard is what refuses a
# wrong one. Each discriminator reverts EXACTLY ONE guard by a unique source-text mutation, loads the
# mutated module as a fresh candidate, and asserts the focused check the guard backs goes RED; it then
# reloads the pristine source and asserts the same check is restored to PASS. A reversal that survives, a
# wrong assertion, or a non-unique mutation target is a harness failure, never a silent pass. This copies
# the shape of check_opf_init_observe.py (the PR4a observer gate), including its mutation of a dependency's
# source (there _opf_observe.py; here _opf_allocation.py) for a guard that lives outside this file.

# The banner above is the first occurrence of this text in the file and marks where the production region
# ends; mutations are confined ahead of it (see _red_on_revert). The literal here is a second occurrence,
# which the one-shot split never reaches.
_REVERT_MARKER = "# --- in-tree red-on-revert discrimination (mirrors"


def _load_revert_candidate(source, name, file_path):
    """Compile `source` into a fresh module registered under `name`, injecting __file__ so the module's
    own sys.path bootstrap runs. The caller pops it from sys.modules when the phase is done."""
    import types
    module = types.ModuleType(name)
    module.__dict__["__file__"] = file_path
    sys.modules[name] = module
    try:
        exec(compile(source, name, "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _revert_check(cond, identity):
    """A focused assertion whose failure names the discriminator identity, exactly as the mutant run
    expects it (str(exc) == identity)."""
    if not cond:
        raise AssertionError(identity)


def _d_reject_refused(module, base_dir):
    """The durable-acceptance reject branch in `_require_acceptance`: a run a reviewer rejected is refused,
    never promoted. Reverting `if rejected:` promotes it, flipping the outcome away from 'rejected'."""
    root, rid, _run = module._st_build(base_dir, "reject", reject=True)
    with _opf_import._self_test_homes2_active(root):
        result = module.apply_ingest(root, rid, now=module._NOW)
    _revert_check(result.promoted is False and result.outcome == "rejected", "acceptance/reject-refused")


def _d_canonical_contained(module, base_dir):
    """The canonical-path boundary in `_canonical`: a traversal spelling is refused as CANNOT_EVALUATE.
    Reverting the `_home_file` validation to return the raw path lets the spelling through un-refused."""
    try:
        module._canonical("../escape.md", "probe source")
    except module._StageError as exc:
        _revert_check(exc.verdict == module.CANNOT_EVALUATE, "path/canonical-contained")
        return
    raise AssertionError("path/canonical-contained")


def _d_journal_lock_required(module, base_dir):
    """The journal-writer-lock requirement in `_opf_allocation.reserve_ingest_ids`: allocation refuses
    when this process does not hold the ingest journal writer lock. Reverting the check lets a lock-less
    caller through, so the lock-specific refusal no longer fires. The fixture is built with the real
    module; only the allocation guard is the candidate. The assertion keys on the phrase only this guard
    emits, so a different downstream AllocationError does not mask the reversal."""
    fake = "imp-20260910T120000Z-0000000000000001"
    root, _rid, _run = _st_build(base_dir, "alloc")
    with _opf_import._self_test_homes2_active(root):
        cap = _opf_oplock.acquire_operation(str(root), OPERATION)
        try:
            try:
                module.reserve_ingest_ids(
                    cap, fake,
                    dict(run_id=fake, review_model_digest="x", bundle_digest="x", acceptance_digest="x"),
                    [("k", "BI")], "stamp", set())
                message = None
            except module.AllocationError as exc:
                message = str(exc)
        finally:
            _opf_oplock.release_operation(cap)
    _revert_check(message is not None and "journal writer lock" in message,
                  "allocation/journal-lock-required")


# (identity, source-key, focused test, unique old, new). source-key selects which module's source is
# mutated: "apply" is this file, "alloc" is _opf_allocation.py (a dependency guard, mutated at source the
# same way the observer gate mutates its shared _opf_observe.py).
_DISCRIMINATORS = (
    ("acceptance/reject-refused", "apply", _d_reject_refused, "if rejected:", "if False:"),
    ("path/canonical-contained", "apply", _d_canonical_contained,
     "return _opf_store._home_file(path)", "return path"),
    ("allocation/journal-lock-required", "alloc", _d_journal_lock_required,
     "if not _opf_journal.writer_lock_held(cap, KIND):", "if False:"),
)


def _red_on_revert():
    """Run the discriminators in a private temporary tree. Return 0 when every guard reverts to RED and
    restores to PASS; raise on a survived reversal, a wrong assertion, or a non-unique mutation target."""
    import shutil
    import tempfile
    here = Path(__file__).resolve().parent
    sources = {"apply": here.joinpath("_opf_ingest_apply.py"), "alloc": here.joinpath("_opf_allocation.py")}
    read = dict((key, path.read_text(encoding="utf-8")) for key, path in sources.items())
    ids = [d[0] for d in _DISCRIMINATORS]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate declared discriminator identity")
    base = Path(tempfile.mkdtemp(prefix="opf-ingest-apply-revert-")).resolve()
    ran = []
    try:
        for number, (identity, key, test, old, new) in enumerate(_DISCRIMINATORS):
            source = read[key]
            file_path = str(sources[key])
            digest = _sha(source.encode("utf-8"))
            # The harness lives IN this file, so a mutation anchor also appears in its own docstrings and
            # the _DISCRIMINATORS table below. Mutate only the production region ahead of this section, so
            # uniqueness is judged against the guard, never the harness's description of it.
            prefix = source.split(_REVERT_MARKER, 1)[0]
            suffix = source[len(prefix):]
            pristine = _load_revert_candidate(source, "_revert_pristine_{}".format(number), file_path)
            try:
                test(pristine, base / "p{}".format(number))
            finally:
                sys.modules.pop(pristine.__name__, None)
            if prefix.count(old) != 1:
                raise RuntimeError("mutation target is not unique in the production region: " + identity)
            mutant = _load_revert_candidate(prefix.replace(old, new, 1) + suffix,
                                            "_revert_mutant_{}".format(number), file_path)
            try:
                test(mutant, base / "m{}".format(number))
            except AssertionError as exc:
                if str(exc) != identity:
                    raise RuntimeError("wrong assertion for " + identity) from exc
            else:
                raise RuntimeError("reversal survived: " + identity)
            finally:
                sys.modules.pop(mutant.__name__, None)
            restored = _load_revert_candidate(source, "_revert_restored_{}".format(number), file_path)
            try:
                test(restored, base / "r{}".format(number))
            finally:
                sys.modules.pop(restored.__name__, None)
            print("RED-ON-REVERT", identity, "assertion=" + identity, "restored=PASS",
                  "candidate_sha256=" + digest)
            ran.append(identity)
    finally:
        shutil.rmtree(str(base), ignore_errors=True)
    print("OPF-INGEST-APPLY RED-ON-REVERT: {} discriminators ({})".format(len(ran), ", ".join(ran)))
    return 0


def _red_on_revert_main():
    """Wrap `_red_on_revert` with the same fail-closed containment guard and exit contract as self_test:
    0 pass, 1 a discrimination or harness failure (never a silent pass), 2 no containment."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-INGEST-APPLY RED-ON-REVERT ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    try:
        _red_on_revert()
    except Exception as exc:  # noqa: BLE001  a discrimination or harness failure is never a silent pass
        print("OPF-INGEST-APPLY RED-ON-REVERT FAILED: {!r}".format(exc), file=sys.stderr)
        return 1
    print("OPF-INGEST-APPLY RED-ON-REVERT PASSED")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    if args == ["--self-test", "--red-on-revert"]:
        rc = self_test()
        return rc if rc != 0 else _red_on_revert_main()
    print("_opf_ingest_apply: the ingest promotion coordinator; run with --self-test (add --red-on-revert "
          "for the guard-discrimination harness; no verb is wired in this slice).",
          file=sys.stderr if args else sys.stdout)
    return 2 if args else 0


if __name__ == "__main__":
    sys.exit(main())
