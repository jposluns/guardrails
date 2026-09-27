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
    """A publication attempt's outcome is indeterminate (left open, a COMPLETE frame whose durability is
    unconfirmed, or its state unreadable): it may have committed, so neither promoted nor aborted is claimed,
    and the journal lock is kept for recovery."""

    def __init__(self, message, ref):
        super().__init__(message)
        self.ref = ref


class _Launch:
    """The launch boundary's evidence, held by apply_ingest OUTSIDE every fallible post-launch step: whether
    the publication attempt was launched, the indeterminate result built BEFORE the launch (so reporting a
    nested post-launch failure formats and constructs nothing), and the attempt's own result once formed.
    Only a formed result is an established outcome; a launched attempt without one retains the lock."""
    __slots__ = ("launched", "fallback", "result")

    def __init__(self):
        self.launched, self.fallback, self.result = False, None, None


def _safe_text(value):
    """`str(value)` that never raises: a value whose __str__ raises, recurses, or returns a non-str yields a
    placeholder naming its type, so no diagnostic built on the post-launch or cleanup path can itself fail
    and select an outcome. The result is always an exact str."""
    try:
        return str.__str__(str(value))
    except Exception:  # noqa: BLE001  a diagnostic never propagates
        pass
    try:
        return "<unprintable {}>".format(str.__str__(type(value).__name__))
    except Exception:  # noqa: BLE001
        return "<unprintable>"


def _surface(*parts):
    """Write one diagnostic line to stderr, never raising: a closed or broken stderr is swallowed, so
    surfacing a cleanup failure can never itself replace a formed result."""
    try:
        print("".join(_safe_text(p) for p in parts), file=sys.stderr)
    except Exception:  # noqa: BLE001  the diagnostic channel failing never overturns a result
        pass


def _cleanup(what, step, *args):
    """Run one final-cleanup step for its side effects only: it never raises and never selects an outcome.
    Any Exception from the step or its own diagnostics, not only OSError, is surfaced non-fatally, so
    cleanup cannot overwrite or downgrade a result already formed. The call to _surface is guarded here
    too: a failure AT the call (a RecursionError under stack pressure, raised before _surface's own guard
    is entered) is one no callee can catch. A lock or descriptor it fails to release is left for the next
    apply to refuse on."""
    try:
        step(*args)
    except Exception as exc:  # noqa: BLE001  the cleanup guard: a cleanup failure never replaces a result
        try:
            _surface("warning: ingest-apply cleanup (", what, ") failed: ", exc, "; the formed result stands")
        except Exception:  # noqa: BLE001  the cleanup diagnostic call itself failing never replaces a result
            pass


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


def _committed_result(ref, why):
    """A CONFIRMED commit whose later step failed: the promotion stands, reported CANNOT_EVALUATE."""
    return ApplyResult(CANNOT_EVALUATE, ["the promotion committed but " + why + "; inspect the retained journal"],
                       promoted=True, outcome="promoted", restore_ref=ref)


def _indeterminate(message, ref):
    """An attempt that may have committed: neither promoted nor aborted is claimed, and the lock is kept."""
    return ApplyResult(CANNOT_EVALUATE, [message + "; the journal lock is retained for recovery"],
                       promoted=None, outcome="indeterminate", restore_ref=ref)


def _post_launch_result(cap, run_id, attempt, ref, exc, returned):
    """The ONE outcome decision for any exception once the publication attempt is launched: durable journal
    evidence decides, never where the exception arose or its class. Commitment is CONFIRMED only when the
    transaction call returned, which it does only after the COMPLETE frame's log fsync succeeded. A COMPLETE
    frame merely READABLE after a raise is not that confirmation: the journal writes the frame before its
    fsync, so a failed fsync leaves a frame the page cache serves that the disk may not hold. That state, an
    open attempt (which recovery may roll FORWARD), and an unreadable state are indeterminate, with the lock
    kept so no later apply reads the unconfirmed frame as a completed no-op. Only an unopened attempt or a
    terminal rollback reads as aborted (a readable ROLLBACK-COMPLETE follows a ROLLBACK-IN-PROGRESS whose own
    publish returned, so its direction is durably backward). Every diagnostic goes through _safe_text, so no
    formatting step raises. Residual (the shared journal contract): the journal's raise does not say WHICH
    fsync failed, so a COMPLETE whose log fsync succeeded but whose closing directory fsync failed, durable
    in fact, is reported indeterminate too. A BaseException outside Exception (an interrupt or exit) is not
    caught here; it propagates, and apply_ingest's launch boundary keeps the journal lock held for it."""
    detail = _safe_text(exc)
    if returned:
        return _committed_result(ref, "live verification could not complete (" + detail + ")")
    try:
        state = _opf_journal.attempt_states(cap, KIND, run_id).get(attempt, "nothing-opened")
    except Exception as read_exc:  # noqa: BLE001  an unreadable state is indeterminate, never "aborted"
        state = "unreadable (" + _safe_text(read_exc) + ")"
    if state in ("nothing-opened", "rolled-back"):
        return ApplyResult(CANNOT_EVALUATE, ["publication aborted and rolled back (" + detail + "); the reserved "
                                             "ids stay consumed and a retry reuses them"],
                           promoted=False, outcome="aborted", restore_ref=ref)
    if state == "complete":
        state = "complete but its durability is unconfirmed"
    raise _RetainLock("publication attempt " + _safe_text(ref["txn_id"]) + " reached no confirmed terminal state ("
                      + detail + "; journal state " + _safe_text(state) + "); it may have committed", ref)


def _launched_attempt(cap, root_fd, run_id, attempt, ref, ops, plan, live, run_rel):
    """The launched publication attempt owns its outcome end to end, TOTAL over Exception: from the launch
    on, no exception, from the transaction, verification, the outcome helper itself, or any diagnostic, can
    reach the generic abort handler in apply_ingest. It returns a formed result or raises _RetainLock; a
    failure raising even that (constructing _RetainLock itself) is caught by apply_ingest's launch
    boundary, which reports the prebuilt indeterminate result and never aborted."""
    try:
        # ONE guard: no exception, from the transaction, verification, or any later step, can report a
        # committed (or possibly committed) promotion as aborted; _post_launch_result decides.
        returned = False
        try:
            _opf_journal.run_attempt_transaction(cap, KIND, run_id, attempt, ops.ops,
                                                 lambda op: ops.content[op["path"]])
            returned = True
            problems = _post_verify(root_fd, plan, live, run_rel)
            if problems:
                return _committed_result(ref, "live verification failed (" + "; ".join(problems) + ")")
            return ApplyResult(CLEAN, [], promoted=True, outcome="promoted", restore_ref=ref)
        except Exception as exc:  # noqa: BLE001  the post-launch guard: journal evidence decides the outcome
            return _post_launch_result(cap, run_id, attempt, ref, exc, returned)
    except _RetainLock:
        raise
    except Exception:  # noqa: BLE001  the outcome helper's own failure is indeterminate, never "aborted"
        raise _RetainLock("publication attempt reached no confirmed outcome (the outcome determination itself "
                          "failed); it may have committed", ref)


def _apply_locked(cap, resolution, run_id, homes, now, launch):
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
        # The inline readers below cannot enumerate a per-record store's ids: a non-inline layout refuses
        # BEFORE any id is read or reserved, never a partial read that could mint a colliding id.
        _opf_import._require_inline_layout(root_fd, machine_rel)
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
        # Built BEFORE the launch, so reporting a nested post-launch failure needs no fallible step.
        launch.fallback = _indeterminate("publication attempt " + ref["txn_id"] + " reached no confirmed outcome "
                                         "(its outcome could not be formed after launch); it may have committed",
                                         ref)
        launch.launched = True
        launch.result = _launched_attempt(cap, root_fd, run_id, attempt, ref, ops, plan, live, run_rel)
        return launch.result
    finally:
        # Guarded at the CALL too, as _cleanup guards its own _surface call: a failure raised before _cleanup's
        # guard is entered (a RecursionError under stack pressure) never replaces the selected outcome, a
        # pre-launch one included (no launch boundary backs it), and its diagnostic is guarded as well.
        try:
            _cleanup("store root descriptor close", _journal._close_fd_quietly, root_fd)
        except Exception:  # noqa: BLE001  the root-close cleanup call itself failing never replaces a result
            try:
                _surface("warning: ingest-apply could not enter the store root descriptor close; the selected "
                         "outcome stands")
            except Exception:  # noqa: BLE001  the root-close call's diagnostic failing never replaces a result
                pass


def apply_ingest(product_root, run_id, *, now=None):
    """Promote the reviewed, accepted staged ingest run `run_id`. Returns an ApplyResult; `promoted` and
    `outcome` are read from the result, never inferred from the verdict. An attempt whose commit can be
    neither confirmed nor ruled out (including a readable COMPLETE whose durability is unconfirmed) reports
    promoted None and outcome "indeterminate", never "aborted"; a cleanup failure never replaces a result.
    The launch boundary: the journal writer lock is released only on an ESTABLISHED outcome, a formed result
    or a failure before the attempt launched, never as the fall-through of an escape. Once launched, an
    Exception escaping every inner guard reports the attempt's formed result or the prebuilt indeterminate
    one, never aborted, and a BaseException (an interrupt or exit) propagates with the lock RETAINED unless
    the attempt's result was already formed, so a retry refuses rather than trusting an unconfirmed COMPLETE.
    Only a genuine pre-commit rollback, read from the journal inside the attempt, reports aborted."""
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
        return ApplyResult(CANNOT_EVALUATE, [_safe_text(exc) + "; fail-closed"], promoted=False, outcome="aborted")
    launch = _Launch()
    locked = release = False
    try:
        _opf_journal.acquire_writer_lock(cap, KIND)
        locked = True
        result = _apply_locked(cap, resolution, run_id, homes, now, launch)
        release = True
        return result
    except _RetainLock as exc:
        try:
            return _indeterminate(_safe_text(exc), exc.ref)
        except Exception:  # noqa: BLE001  forming the detailed result failed: the prebuilt one stands
            return launch.fallback
    except Exception as exc:  # noqa: BLE001  the launch boundary: a launched attempt is never reported aborted
        if launch.launched:
            release = launch.result is not None
            return launch.result if release else launch.fallback
        release = True
        if isinstance(exc, _StageError):
            return ApplyResult(exc.verdict, [exc.message], promoted=False,
                               outcome="rejected" if exc.verdict == FINDING else "aborted")
        if isinstance(exc, (_journal.JournalError, _opf_oplock.OpLockError, OSError, RecursionError)):
            return ApplyResult(CANNOT_EVALUATE, [_safe_text(exc) + "; fail-closed"], promoted=False,
                               outcome="aborted")
        raise
    except BaseException:
        # An interrupt or exit propagates, never swallowed; once launched the lock stays held unless the
        # attempt's own result was already formed.
        release = not launch.launched or launch.result is not None
        raise
    finally:
        # A release failure of ANY class never overturns a formed result (_cleanup); a lock left behind
        # refuses the next apply. Each call is guarded HERE too, as _cleanup guards its own _surface call: a
        # failure raised before _cleanup's guard is entered (a RecursionError under stack pressure) never
        # replaces the selected outcome, its diagnostic is guarded as well, and the next release is still
        # attempted. The writer lock is still released only on an established outcome (`release`).
        if locked and release:
            try:
                _cleanup("journal writer lock release", _opf_journal.release_writer_lock, cap, KIND)
            except Exception:  # noqa: BLE001  the writer-release cleanup call itself failing never replaces a result
                try:
                    _surface("warning: ingest-apply could not enter the journal writer lock release; the "
                             "selected outcome stands")
                except Exception:  # noqa: BLE001  the writer-release call's diagnostic failing never replaces one
                    pass
        try:
            _cleanup("operation capability release", _opf_oplock.release_operation, cap)
        except Exception:  # noqa: BLE001  the op-release cleanup call itself failing never replaces a result
            try:
                _surface("warning: ingest-apply could not enter the operation capability release; the selected "
                         "outcome stands")
            except Exception:  # noqa: BLE001  the op-release call's diagnostic failing never replaces a result
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


def _st_staged_apply(gate, base, kind, case):
    """Exercise apply_ingest at either depth-four home, observing the actual gate call.
    Withhold only the gate argument, so the coordinator still locates the run under homes 2."""
    from unittest.mock import patch
    root, rid, run = _st_build(base, "staged-{}-{}".format(kind, case))
    staged = root / _opf_store.stage_run(kind, rid)
    staged.parent.mkdir(parents=True)
    run.rename(staged)
    if case == "corrupt":
        record = root / _opf_import.IMPORT_OPS_REL / rid / _opf_import.TRANSACTION_NAME
        record.parent.mkdir(parents=True)
        record.write_bytes(b"state =\n")
    before = _st_counters(root)
    manifest = root / ".working/toml/manifest.toml"
    manifest_before = manifest.read_bytes()
    activation = (_opf_store.SUPPORTED_HOMES, _opf_store.HOMES2_SPEC_VERSION, _opf_store.validate_manifest)
    real_check = gate.check_staged_run
    seen = []

    def observed(path, homes=None):
        results = real_check(path, homes=None if case == "withheld" else homes)
        seen.append((Path(path), homes, results))
        return results

    with _opf_import._self_test_homes2_active(root), \
            patch.dict(sys.modules, {"check_opf_import": gate}), \
            patch.object(gate, "check_staged_run", side_effect=observed):
        result = apply_ingest(root, rid, now=_NOW)
    restored = (activation == (_opf_store.SUPPORTED_HOMES, _opf_store.HOMES2_SPEC_VERSION,
                              _opf_store.validate_manifest)
                and manifest.read_bytes() == manifest_before and gate.check_staged_run is real_check)
    if not (restored and len(seen) == 1 and seen[0][:2] == (staged, 2)):
        return False
    results = seen[0][2]
    if case == "clean":
        home = root / _opf_import._ingest_acceptance_home(rid)
        return (result.verdict == CLEAN and result.promoted is True and result.outcome == "promoted"
                and set(results) == set(gate.EXPECTED_CHECKS)
                and all(results[cid][0] for cid in gate.EXPECTED_CHECKS)
                and not staged.exists() and (home / PROMOTION_NAME).is_file()
                and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
                and _st_counters(root) == dict(BI=1, LF=3, WL=0))
    refused = (result.verdict == CANNOT_EVALUATE and result.promoted is False
               and result.outcome == "aborted" and staged.is_dir()
               and _st_counters(root) == before and _st_reservation(root, rid) is None
               and any("import gate failed:" in finding and "transaction-schema" in finding
                       for finding in result.findings))
    if case == "corrupt":
        return (refused and results["transaction-schema"][0] is False
                and "unreadable/unparseable" in results["transaction-schema"][1])
    if case == "withheld":
        error = ("cannot evaluate: {}: the store's homes generation was not supplied to this manifest-free "
                 "gate".format(staged))
        # Flip: removing the public generation boundary restores partial grading and unlocated errors.
        return (refused and tuple(results) == gate.EXPECTED_CHECKS
                and all(value == (False, error) for value in results.values()))
    raise ValueError("unknown staged apply case: " + case)


def _t_staged_apply(kind, case):
    def test(base, check):
        import check_opf_import as gate
        check("staged-{}-{}".format(kind, case), _st_staged_apply(gate, base, kind, case))
    return test


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


def _st_per_record(root):
    """Declare the fixture store's layout `per-record` and plant a BI-1 record file under `backlog_item/`,
    where the inline readers never look: the hidden-collision hazard a blind inline read would miss."""
    manifest = root / ".working/toml/manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    if text.count('layout = "inline"') != 1:
        raise RuntimeError("fixture manifest does not declare exactly one inline layout")
    manifest.write_text(text.replace('layout = "inline"', 'layout = "per-record"'), encoding="utf-8")
    (root / ".working/toml/backlog_item").mkdir()
    (root / ".working/toml/backlog_item/BI-1.toml").write_text(
        'schema = 1\nid = "BI-1"\ntype = "backlog_item"\n', encoding="utf-8")


def _t_per_record_refused(base, check):
    """A per-record store refuses as CANNOT_EVALUATE before any id is read or reserved: counters, the
    index, and the tree are byte-unchanged, nothing is reserved, and no evidence or receipt is written."""
    root, rid, run = _st_build(base, "per-record")
    _st_per_record(root)
    machine = root / ".working/toml"
    index = machine / "backlog_item.index.toml"
    counters = (machine / "counters.toml").read_bytes()
    index_before = index.read_bytes() if index.exists() else None
    with _opf_import._self_test_homes2_active(root):
        before = _st_tree(root)
        result = apply_ingest(root, rid, now=_NOW)
        unchanged = _unchanged(root, before, rid)
    home = root / _opf_import._ingest_acceptance_home(rid)
    # Flip: without the layout guard the inline id union misses the per-record BI-1, so apply mints a
    # colliding BI-1 and publishes it.
    check("per-record-refused", result.verdict == CANNOT_EVALUATE and result.promoted is False
          and result.outcome == "aborted" and "storage layout" in " ".join(result.findings))
    check("per-record-nothing-allocated-or-published", unchanged
          and (machine / "counters.toml").read_bytes() == counters
          and (index.read_bytes() if index.exists() else None) == index_before
          and not (home / PROMOTION_NAME).exists() and not (home / REVIEW_DIRNAME).exists()
          and not (root / _opf_store.evidence_inventory("import", rid)).exists() and run.is_dir())


def _st_postverify_fault(apply, root, rid, run, point, error):
    """Apply with a read fault injected into post-commit verification: `point` selects the removed-source
    lstat ("source") or the staging-run lstat ("staging"), and the fault fires only once the journal
    transaction has returned, so the promotion has genuinely committed. Returns (result, fired)."""
    from unittest.mock import patch
    targets = dict(source={"legacy/move.md", "legacy/mig.md"},
                   staging={run.relative_to(root).as_posix()})[point]
    committed, fired = [], []
    real_txn = _opf_journal.run_attempt_transaction
    real_lstat = _journal._lstat_contained

    def txn(*args, **kwargs):
        out = real_txn(*args, **kwargs)
        committed.append(True)
        return out

    def lstat(root_fd, relpath):
        if committed and relpath in targets:
            fired.append(relpath)
            raise error("injected post-commit verification fault at {}".format(relpath))
        return real_lstat(root_fd, relpath)

    with _opf_import._self_test_homes2_active(root):
        with patch.object(_opf_journal, "run_attempt_transaction", txn), \
                patch.object(_journal, "_lstat_contained", lstat):
            result = apply(root, rid, now=_NOW)
    return result, fired


def _t_postverify_committed(base, check):
    """A read fault in post-commit verification never reports a committed promotion as aborted: the result
    is CANNOT_EVALUATE with promoted=True, outcome "promoted", and the committed transaction's ref. Each
    fault point is driven with a JournalError, an OSError, and a RecursionError: the class-wide post-launch
    guard does not depend on the exception class."""
    for point in ("source", "staging"):
        for error in (_journal.JournalError, OSError, RecursionError):
            name = "postverify-{}-{}".format(point, error.__name__)
            root, rid, run = _st_build(base, name)
            result, fired = _st_postverify_fault(apply_ingest, root, rid, run, point, error)
            home = root / _opf_import._ingest_acceptance_home(rid)
            ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
            check(name + "-fired-after-commit", len(fired) == 1
                  and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
                  and not (root / "legacy/move.md").exists() and (home / PROMOTION_NAME).is_file())
            # Flip: without the post-commit guard the fault escapes to the aborted handler (promoted=False).
            check(name + "-committed-not-aborted", result.verdict == CANNOT_EVALUATE
                  and result.promoted is True and result.outcome == "promoted" and result.restore_ref == ref
                  and "could not complete" in " ".join(result.findings))


def _st_attempt_states(root, rid):
    """The run's recorded attempt states, read back under a fresh capability (homes 2 active)."""
    cap = _opf_oplock.acquire_operation(str(root), OPERATION)
    try:
        return _opf_journal.attempt_states(cap, KIND, rid)
    finally:
        _opf_oplock.release_operation(cap)


def _st_postcommit_fault(apply, root, rid, frame, target="dir"):
    """Apply with a fault in one of publish's fsyncs, armed only once the `frame` record (INTENT or COMPLETE)
    is written: `target` "dir" faults the closing txn-directory fsync (the log fsync succeeded), "log" faults
    the frames.log fsync itself, so the frame is readable but its durability unconfirmed. The OSError wraps to
    a JournalError at the store journal boundary. Returns (result, fired, states)."""
    from unittest.mock import patch
    marker = _journal.MAGIC + b" " + frame.encode() + b" "
    armed, fired = [], []
    real_write_all = _journal._write_all
    real_fsync = os.fsync

    def write_all(fd, data):
        real_write_all(fd, data)
        if data.startswith(marker):
            armed.append(True)

    def fsync(fd):
        mode = os.fstat(fd).st_mode
        if armed and not fired and (stat.S_ISDIR(mode) if target == "dir" else stat.S_ISREG(mode)):
            fired.append(frame)
            raise OSError("injected journal {} fsync fault after the {} frame".format(target, frame))
        return real_fsync(fd)

    with _opf_import._self_test_homes2_active(root):
        with patch.object(_journal, "_write_all", write_all), patch.object(_journal.os, "fsync", fsync):
            result = apply(root, rid, now=_NOW)
        states = _st_attempt_states(root, rid)
    return result, fired, states


def _t_postcommit_journal_fault(base, check):
    """A journal fault in the closing directory fsync after the COMPLETE frame is written never reports the
    promotion as aborted, nor as promoted: the transaction did not return, and although the attempt reads
    "complete" the raise cannot say whether the frame's log fsync succeeded, so the result is CANNOT_EVALUATE
    with promoted None, outcome "indeterminate", the attempt's ref, and the journal lock retained; a re-apply
    then refuses on the held lock rather than reading the unconfirmed frame as a completed no-op. A
    pre-commit failure still rolls back and reports aborted, and the same fault after INTENT alone (an open
    attempt) is indeterminate, never aborted, with the journal lock retained."""
    from unittest.mock import patch
    root, rid, run = _st_build(base, "postcommit-complete")
    result, fired, states = _st_postcommit_fault(apply_ingest, root, rid, _journal.F_COMPLETE)
    home = root / _opf_import._ingest_acceptance_home(rid)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    check("postcommit-complete-fired-after-commit", fired == [_journal.F_COMPLETE] and states == {1: "complete"}
          and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
          and not (root / "legacy/move.md").exists() and (home / PROMOTION_NAME).is_file())
    # Flip: without the post-launch guard the fault reaches the aborted handler; trusting the readable
    # COMPLETE claims promoted=True for a frame whose durability the raise leaves unconfirmed.
    check("postcommit-complete-indeterminate", result.verdict == CANNOT_EVALUATE and result.promoted is None
          and result.outcome == "indeterminate" and result.restore_ref == ref
          and "durability is unconfirmed" in " ".join(result.findings))
    with _opf_import._self_test_homes2_active(root):
        again = apply_ingest(root, rid, now=_NOW)
    check("postcommit-complete-reapply-refused", again.promoted is False and again.outcome == "aborted"
          and "already held" in " ".join(again.findings))
    root, rid, run = _st_build(base, "postcommit-rollback")
    with _opf_import._self_test_homes2_active(root):
        # A failed poststate check raises before COMPLETE is published: the attempt rolls back.
        with patch.object(_journal, "_poststate_verifies", lambda _root_fd, _op: False):
            rolled = apply_ingest(root, rid, now=_NOW)
        states = _st_attempt_states(root, rid)
    check("postcommit-precommit-rollback-aborted", rolled.verdict == CANNOT_EVALUATE
          and rolled.promoted is False and rolled.outcome == "aborted" and states == {1: "rolled-back"}
          and (root / "legacy/move.md").read_bytes() == b"moveme\n"
          and not (root / ".archive/legacy/move.md").exists() and run.is_dir())
    root, rid, run = _st_build(base, "postcommit-open")
    opened, fired, states = _st_postcommit_fault(apply_ingest, root, rid, _journal.F_INTENT)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    # Flip: reporting an open attempt as aborted claims a rollback that has not happened.
    check("postcommit-open-retains-lock", fired == [_journal.F_INTENT] and states == {1: "open"}
          and opened.verdict == CANNOT_EVALUATE and opened.promoted is None
          and opened.outcome == "indeterminate" and opened.restore_ref == ref
          and "retained for recovery" in " ".join(opened.findings))


def _st_reread_fault(apply, error):
    """Wrap `apply` so every attempt-state read made once the publication attempt is launched raises
    `error`; the classification ahead of the launch reads normally. Returns (wrapped, raised)."""
    from unittest.mock import patch
    raised = []

    def wrapped(*args, **kwargs):
        launched = []
        real_txn = _opf_journal.run_attempt_transaction
        real_states = _opf_journal.attempt_states

        def txn(*a, **k):
            launched.append(True)
            return real_txn(*a, **k)

        def states(*a, **k):
            if launched:
                raised.append(True)
                raise error("injected attempt-state re-read fault")
            return real_states(*a, **k)

        with patch.object(_opf_journal, "run_attempt_transaction", txn), \
                patch.object(_opf_journal, "attempt_states", states):
            return apply(*args, **kwargs)
    return wrapped, raised


def _st_complete_lost(apply, root, rid):
    """Apply with the COMPLETE frame's write refused before any byte of it reaches the log, after every op
    applied and verified: the attempt reads "open" and recovery can roll it FORWARD, so its commit can be
    neither confirmed nor ruled out. Recovery then runs inside the SAME homes-2 activation, whose exit
    restores the manifest bytes the apply wrote. Returns (result, fired, states, recovered)."""
    from unittest.mock import patch
    marker = _journal.MAGIC + b" " + _journal.F_COMPLETE.encode() + b" "
    fired = []
    real_write_all = _journal._write_all

    def write_all(fd, data):
        if data.startswith(marker):
            fired.append(_journal.F_COMPLETE)
            raise OSError("injected fault writing the COMPLETE frame")
        real_write_all(fd, data)

    with _opf_import._self_test_homes2_active(root):
        with patch.object(_journal, "_write_all", write_all):
            result = apply(root, rid, now=_NOW)
        states = _st_attempt_states(root, rid)
        recovered = _st_recover(root, rid, 1)
    return result, fired, states, recovered


def _st_foreign_txn_fault(apply, root, rid):
    """Apply with a ValueError, a class the journal's own rollback does not catch, raised by the post-apply
    poststate check: every op applied, no terminal frame is written, and the attempt stays open. An
    exception escaping `apply` is returned in place of the result. Returns (result, states)."""
    from unittest.mock import patch

    def poststate(_root_fd, _op):
        raise ValueError("injected foreign exception in the post-apply poststate check")

    with _opf_import._self_test_homes2_active(root):
        with patch.object(_journal, "_poststate_verifies", poststate):
            try:
                result = apply(root, rid, now=_NOW)
            except Exception as exc:  # noqa: BLE001  an escaped exception is the misreport under test
                result = exc
        states = _st_attempt_states(root, rid)
    return result, states


def _st_recover(root, rid, attempt):
    """Run journal recovery over one attempt directly (the recovery verb is deferred to slice 2) and return
    its verdict: "rolled-forward" shows every op of an open attempt applied and verified."""
    root_fd = _opf_store._open_root_fd(root)
    try:
        jr_fd = _journal.open_journal_root_fd(root_fd, _opf_store.journal_root(KIND))
        try:
            txn_dir = Path(root) / _opf_store.journal_root(KIND) / _opf_journal.attempt_txn(KIND, rid, attempt)
            return _journal.recover(jr_fd, txn_dir, root_fd)
        finally:
            _journal._close_fd_quietly(jr_fd)
    finally:
        _journal._close_fd_quietly(root_fd)


class _Unprintable(Exception):
    """An exception whose __str__ raises: any diagnostic that formats it without _safe_text fails."""

    def __str__(self):
        raise RecursionError("injected: this exception cannot be formatted")


def _st_helper_fault(apply, root, rid, run):
    """Apply with a committed publication's verification fault handed to an outcome helper that itself
    raises a RecursionError, a class the generic abort handler catches. Returns (result, fired)."""
    from unittest.mock import patch

    def helper(*_args, **_kwargs):
        raise RecursionError("injected failure inside the post-launch outcome helper")

    with patch.object(sys.modules[__name__], "_post_launch_result", helper):
        return _st_postverify_fault(apply, root, rid, run, "source", _journal.JournalError)


def _st_nested_fault(apply, root, rid, run):
    """Apply a publication that commits, then fail every layer that reports it: post-commit verification
    faults, the outcome helper raises a RecursionError, constructing the _RetainLock that would report that
    raises a RecursionError too, and the final operation-capability release fails AFTER its real release
    with the cleanup diagnostic call itself raising (the stack-pressure RecursionError no callee guard can
    catch). An exception escaping `apply` is returned in place of the result. Returns (result, fired,
    states)."""
    from unittest.mock import patch
    module = sys.modules[__name__]
    real_release = _opf_oplock.release_operation
    fired = []

    def helper(*_args, **_kwargs):
        fired.append("helper")
        raise RecursionError("injected failure inside the post-launch outcome helper")

    def retain_init(_self, *_args, **_kwargs):
        fired.append("retain")
        raise RecursionError("injected failure constructing _RetainLock")

    def surface(*_parts):
        fired.append("surface")
        raise RecursionError("injected failure calling the cleanup diagnostic")

    def release(*args, **kwargs):
        out = real_release(*args, **kwargs)
        if "op-release" not in fired:
            fired.append("op-release")
            raise RecursionError("injected fault in the op-release cleanup")
        return out

    with patch.object(module, "_post_launch_result", helper), \
            patch.object(module._RetainLock, "__init__", retain_init), \
            patch.object(module, "_surface", surface), patch.object(_opf_oplock, "release_operation", release):
        try:
            result, _verify = _st_postverify_fault(apply, root, rid, run, "source", _journal.JournalError)
        except Exception as exc:  # noqa: BLE001  an escaped exception is the misreport under test
            result = exc
    with _opf_import._self_test_homes2_active(root):
        states = _st_attempt_states(root, rid)
    return result, fired, states


class _InterruptingStr(Exception):
    """An exception whose __str__ raises KeyboardInterrupt: formatting it on the post-launch path interrupts."""

    def __str__(self):
        raise KeyboardInterrupt("injected: formatting this exception is interrupted")


def _st_interrupt_fault(apply, root, rid, interrupt):
    """Apply with `interrupt` raised by the frames.log fsync of the COMPLETE frame, so the frame is readable
    (the attempt reads complete) but its log fsync never succeeded. KeyboardInterrupt and SystemExit escape
    the transaction itself; _InterruptingStr reaches the post-launch guard and interrupts when its diagnostic
    is formatted. The interrupt escaping `apply` is captured, then the run is re-applied at once. Returns
    (escaped, fired, states, held, again): the escaped class (None when nothing escaped), whether the
    journal writer lock was still held after the interrupt, and the re-apply's result."""
    from unittest.mock import patch
    marker = _journal.MAGIC + b" " + _journal.F_COMPLETE.encode() + b" "
    armed, fired = [], []
    real_write_all = _journal._write_all
    real_fsync = os.fsync
    escaped = None

    def write_all(fd, data):
        real_write_all(fd, data)
        if data.startswith(marker):
            armed.append(True)

    def fsync(fd):
        if armed and not fired and stat.S_ISREG(os.fstat(fd).st_mode):
            fired.append(interrupt.__name__)
            raise interrupt("injected {} at the COMPLETE frame's log fsync".format(interrupt.__name__))
        return real_fsync(fd)

    with _opf_import._self_test_homes2_active(root):
        with patch.object(_journal, "_write_all", write_all), patch.object(_journal.os, "fsync", fsync):
            try:
                apply(root, rid, now=_NOW)
            except (KeyboardInterrupt, SystemExit) as exc:
                escaped = type(exc)
        states = _st_attempt_states(root, rid)
        held = (Path(root) / _opf_store.journal_root(KIND) / "lock").is_file()
        again = apply(root, rid, now=_NOW)
    return escaped, fired, states, held, again


class _BrokenStream:
    """A stderr whose every write fails, as a closed stream's does."""

    def write(self, _data):
        raise ValueError("injected: I/O operation on a closed stderr")

    def flush(self):
        raise ValueError("injected: I/O operation on a closed stderr")


def _st_cleanup_fault(apply, root, rid, step, error, broken_stderr=False):
    """Apply a promotion that commits and verifies cleanly, with `error` raised by one final-cleanup step
    AFTER its real side effect ran: "root-close" (the store root descriptor close in _apply_locked, armed
    only once post-commit verification returned), "lock-release" (the journal writer lock), or "op-release"
    (the operation capability). stderr is captured, or with `broken_stderr` fails on every write so the
    cleanup diagnostic itself fails. An exception escaping `apply` is returned in place of the result.
    Returns (result, fired, surfaced)."""
    import contextlib
    import io
    from unittest.mock import patch
    fired, verified = [], []
    module = sys.modules[__name__]
    real_verify = module._post_verify
    owner, attr, gate = dict(
        (("root-close", (_journal, "_close_fd_quietly", lambda: bool(verified))),
         ("lock-release", (_opf_journal, "release_writer_lock", lambda: True)),
         ("op-release", (_opf_oplock, "release_operation", lambda: True))))[step]
    real_step = getattr(owner, attr)

    def verify(*args, **kwargs):
        out = real_verify(*args, **kwargs)
        verified.append(True)
        return out

    def faulty(*args, **kwargs):
        out = real_step(*args, **kwargs)
        if gate() and not fired:
            fired.append(step)
            raise error("injected fault in the {} cleanup".format(step))
        return out

    stream = _BrokenStream() if broken_stderr else io.StringIO()
    with _opf_import._self_test_homes2_active(root):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module, "_post_verify", verify))
            stack.enter_context(patch.object(owner, attr, faulty))
            stack.enter_context(patch.object(sys, "stderr", stream))
            try:
                result = apply(root, rid, now=_NOW)
            except Exception as exc:  # noqa: BLE001  an escaped exception is the misreport under test
                result = exc
    return result, fired, "" if broken_stderr else stream.getvalue()


# The final cleanup calls, each guarded at its call: (label, what, noop, later). `noop` drives the call on a
# completed run's verified no-op, a pre-launch outcome no launch boundary backs; `later` is every cleanup
# still entered after the call fails.
_CLEANUP_CALLS = (("root-close", "store root descriptor close", True,
                   ["journal writer lock release", "operation capability release"]),
                  ("lock-release", "journal writer lock release", False, ["operation capability release"]),
                  ("op-release", "operation capability release", False, []))


def _st_cleanup_call_fault(apply, root, rid, what, noop=False, surface_fails=False):
    """Apply with a RecursionError raised AT the call to the `what` final cleanup, before _cleanup's own guard
    is entered (the stack-pressure failure no callee guard can catch); every other cleanup runs for real. With
    `noop` the run is first promoted cleanly, so the faulted apply is the completed run's verified no-op; with
    `surface_fails` the diagnostic call at that boundary raises a RecursionError too. stderr is captured. An
    exception escaping `apply` is returned in place of the result, and what the faulted call never released is
    released afterwards so no later vector inherits it. Returns (result, fired, entered, surfaced): `entered`
    lists the cleanups entered after the fault, in order."""
    import contextlib
    import io
    from unittest.mock import patch
    module = sys.modules[__name__]
    real_cleanup = module._cleanup
    fired, entered, leaked = [], [], []

    def cleanup(step_what, step, *args):
        if step_what == what and not fired:
            fired.append(what)
            leaked.append((step, args))
            raise RecursionError("injected failure entering the {} cleanup".format(what))
        if fired:
            entered.append(step_what)
        return real_cleanup(step_what, step, *args)

    def surface(*_parts):
        raise RecursionError("injected failure calling the cleanup-call diagnostic")

    stream = io.StringIO()
    with _opf_import._self_test_homes2_active(root):
        if noop:
            apply(root, rid, now=_NOW)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module, "_cleanup", cleanup))
            stack.enter_context(patch.object(sys, "stderr", stream))
            if surface_fails:
                stack.enter_context(patch.object(module, "_surface", surface))
            try:
                result = apply(root, rid, now=_NOW)
            except Exception as exc:  # noqa: BLE001  an escaped exception is the misreport under test
                result = exc
        for step, args in leaked:
            try:
                step(*args)
            except Exception:  # noqa: BLE001  fixture hygiene only; the observation above is already taken
                pass
    return result, fired, entered, stream.getvalue()


def _t_postlaunch_indeterminate(base, check):
    """The post-launch guard is class-wide. A failing attempt-state re-read after a committed publication is
    indeterminate, never aborted: CANNOT_EVALUATE, promoted None (commit neither confirmed nor ruled out),
    outcome "indeterminate", the attempt's ref, and the lock retained. An open attempt whose every op
    applied (its COMPLETE frame never written, or a foreign exception past the journal's rollback) is
    indeterminate too, never a false abort and never a false promotion. A transaction that RETURNED is
    committed however a later step fails, even while the attempt state is unreadable."""
    for error in (_journal.JournalError, OSError):
        name = "reread-{}".format(error.__name__)
        root, rid, run = _st_build(base, name)
        wrapped, raised = _st_reread_fault(apply_ingest, error)
        result, fired, states = _st_postcommit_fault(wrapped, root, rid, _journal.F_COMPLETE)
        home = root / _opf_import._ingest_acceptance_home(rid)
        ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
        check(name + "-fired-after-commit", fired == [_journal.F_COMPLETE] and len(raised) == 1
              and states == {1: "complete"} and (home / PROMOTION_NAME).is_file() and not run.exists())
        # Flip: without the guarded re-read the read fault escapes to the aborted handler (promoted=False).
        check(name + "-indeterminate-not-aborted", result.verdict == CANNOT_EVALUATE and result.promoted is None
              and result.outcome == "indeterminate" and result.restore_ref == ref
              and "retained for recovery" in " ".join(result.findings))
    root, rid, run = _st_build(base, "complete-lost")
    result, fired, states, recovered = _st_complete_lost(apply_ingest, root, rid)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    check("complete-lost-open-applied", fired == [_journal.F_COMPLETE] and states == {1: "open"}
          and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
          and not (root / "legacy/move.md").exists() and not run.exists())
    # Flip: reporting this open attempt aborted denies a promotion recovery rolls forward; reporting it
    # promoted claims a COMPLETE the journal does not hold.
    check("complete-lost-indeterminate", result.verdict == CANNOT_EVALUATE and result.promoted is None
          and result.outcome == "indeterminate" and result.restore_ref == ref
          and "retained for recovery" in " ".join(result.findings))
    check("complete-lost-recovery-rolls-forward", recovered == "rolled-forward")
    root, rid, run = _st_build(base, "foreign-txn")
    result, states = _st_foreign_txn_fault(apply_ingest, root, rid)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    # Flip: without the class-wide guard the foreign exception escapes apply_ingest with no result formed.
    check("foreign-txn-indeterminate", states == {1: "open"} and getattr(result, "promoted", False) is None
          and getattr(result, "outcome", None) == "indeterminate" and getattr(result, "restore_ref", None) == ref)
    root, rid, run = _st_build(base, "returned-unreadable")
    wrapped, raised = _st_reread_fault(apply_ingest, _journal.JournalError)
    result, fired = _st_postverify_fault(wrapped, root, rid, run, "source", _journal.JournalError)
    # Flip: without the returned-transaction evidence the unreadable state demotes a known commit to
    # indeterminate.
    check("returned-unreadable-promoted", len(fired) == 1 and not raised and result.verdict == CANNOT_EVALUATE
          and result.promoted is True and result.outcome == "promoted")


def _t_postlaunch_total(base, check):
    """The launched attempt's outcome determination is TOTAL over Exception. An exception that cannot be
    formatted, raised in post-commit verification, is still a committed promotion (promoted=True, a
    placeholder in its finding); an outcome helper that itself raises is indeterminate, never aborted; a
    failure of ANY class in a final-cleanup step, the root descriptor close, the journal writer lock release,
    or the operation capability release, is surfaced and never replaces the formed promoted result, even
    with stderr itself broken; a failure AT each final-cleanup call, before _cleanup is entered, never
    replaces the selected outcome and every later cleanup is still entered; and a COMPLETE frame whose own log
    fsync failed, readable but not confirmed durable, is indeterminate with the lock and journal retained,
    never promoted, and a re-apply refuses rather than reporting a completed no-op."""
    root, rid, run = _st_build(base, "unprintable")
    result, fired = _st_postverify_fault(apply_ingest, root, rid, run, "source", _Unprintable)
    # Flip: formatting the exception without _safe_text raises out of the outcome helper, so the committed
    # promotion is reported aborted (or indeterminate through the helper-failure guard).
    check("unprintable-committed-not-aborted", len(fired) == 1 and result.verdict == CANNOT_EVALUATE
          and result.promoted is True and result.outcome == "promoted"
          and "<unprintable _Unprintable>" in " ".join(result.findings))
    root, rid, run = _st_build(base, "helper-fault")
    result, fired = _st_helper_fault(apply_ingest, root, rid, run)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    # Flip: without the helper-failure guard the RecursionError reaches the generic abort handler.
    check("helper-fault-indeterminate", len(fired) == 1 and result.verdict == CANNOT_EVALUATE
          and result.promoted is None and result.outcome == "indeterminate" and result.restore_ref == ref
          and "outcome determination itself failed" in " ".join(result.findings))
    for step in ("root-close", "lock-release", "op-release"):
        root, rid, run = _st_build(base, "cleanup-" + step)
        result, fired, surfaced = _st_cleanup_fault(apply_ingest, root, rid, step, RecursionError)
        # Flip: a cleanup guard narrowed to OSError lets the RecursionError out of _cleanup; the guarded call
        # (and, for the root close, the launch boundary) then keeps the result, so the step's own diagnostic
        # is what goes missing. The isolated red-on-revert rows strip those layers to show the outcome.
        check("cleanup-{}-result-preserved".format(step), fired == [step]
              and getattr(result, "verdict", None) == CLEAN and getattr(result, "promoted", None) is True
              and getattr(result, "outcome", None) == "promoted" and "cleanup (" in surfaced)
    root, rid, run = _st_build(base, "cleanup-diagnostic")
    result, fired, _surfaced = _st_cleanup_fault(apply_ingest, root, rid, "root-close", RecursionError,
                                                 broken_stderr=True)
    # Flip: an unguarded diagnostic write lets the broken stderr's ValueError out of _surface, which the
    # guarded diagnostic call in _cleanup then absorbs (so the red-on-revert row probes _surface directly).
    check("cleanup-diagnostic-result-preserved", fired == ["root-close"]
          and getattr(result, "promoted", None) is True and getattr(result, "outcome", None) == "promoted")
    for label, what, noop, later in _CLEANUP_CALLS:
        for surface_fails, name in ((False, "outcome-preserved"), (True, "diagnostic-failure-preserved")):
            root, rid, run = _st_build(base, "cleanup-call-{}-{}".format(label, name))
            result, fired, entered, surfaced = _st_cleanup_call_fault(apply_ingest, root, rid, what, noop,
                                                                      surface_fails)
            # Flip: an unguarded call (or, with its diagnostic call failing too, an unguarded diagnostic)
            # lets the failure replace the selected outcome (an abort of the completed run's no-op for the
            # root close, an exception escaping apply_ingest for either release) and skips every later release.
            check("cleanup-call-{}-{}".format(label, name), fired == [what] and entered == later
                  and getattr(result, "verdict", None) == CLEAN and getattr(result, "promoted", None) is True
                  and getattr(result, "outcome", None) == ("noop_already_complete" if noop else "promoted")
                  and (surface_fails or "could not enter the " + what in surfaced))
    root, rid, run = _st_build(base, "complete-unsynced")
    result, fired, states = _st_postcommit_fault(apply_ingest, root, rid, _journal.F_COMPLETE, target="log")
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    journal = root / _opf_store.journal_root(KIND)
    txn = journal / _opf_journal.attempt_txn(KIND, rid, 1)
    check("complete-unsynced-fired", fired == [_journal.F_COMPLETE] and states == {1: "complete"}
          and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n" and not run.exists())
    # Flip: trusting the readable COMPLETE frame reports promoted=True for a commit whose durability failed.
    check("complete-unsynced-not-promoted", result.verdict == CANNOT_EVALUATE and result.promoted is None
          and result.outcome == "indeterminate" and result.restore_ref == ref
          and "durability is unconfirmed" in " ".join(result.findings)
          and (journal / "lock").is_file() and (txn / "frames.log").is_file() and (txn / "preimages").is_dir())
    with _opf_import._self_test_homes2_active(root):
        again = apply_ingest(root, rid, now=_NOW)
    # Flip: releasing the lock lets the re-apply read the unconfirmed frame as a verified completed no-op.
    check("complete-unsynced-reapply-refused", again.promoted is False and again.outcome == "aborted"
          and "already held" in " ".join(again.findings))


def _t_postlaunch_retain(base, check):
    """A launched attempt is never reported aborted, and never releases the journal lock without an
    established outcome, whatever escapes. A committed publication whose every reporting layer fails (the
    outcome helper, the _RetainLock that would report that, and the final release's cleanup diagnostic call)
    is the prebuilt indeterminate result with the lock retained, never aborted and never an escape. A
    KeyboardInterrupt, a SystemExit, and an exception whose __str__ raises KeyboardInterrupt, each after a
    COMPLETE frame whose log fsync never succeeded, propagate with the lock retained, so an immediate
    re-apply refuses on it rather than reporting the unconfirmed frame as a completed no-op."""
    root, rid, run = _st_build(base, "nested-fault")
    result, fired, states = _st_nested_fault(apply_ingest, root, rid, run)
    home = root / _opf_import._ingest_acceptance_home(rid)
    ref = dict(txn_id=_opf_journal.attempt_txn(KIND, rid, 1), journal_rel=_opf_store.journal_root(KIND))
    check("nested-fault-fired-after-commit", fired == ["helper", "retain", "op-release", "surface"]
          and states == {1: "complete"} and (root / ".archive/legacy/move.md").read_bytes() == b"moveme\n"
          and not (root / "legacy/move.md").exists() and (home / PROMOTION_NAME).is_file())
    # Flip: without the launch boundary the _RetainLock construction failure reaches the generic abort
    # handler (promoted=False, outcome aborted, lock released); without the guarded diagnostic call AND the
    # guarded op-release call the op-release failure escapes apply_ingest with no result formed.
    check("nested-fault-not-aborted", getattr(result, "verdict", None) == CANNOT_EVALUATE
          and getattr(result, "promoted", False) is None and getattr(result, "outcome", None) == "indeterminate"
          and getattr(result, "restore_ref", None) == ref
          and "could not be formed after launch" in " ".join(getattr(result, "findings", []))
          and (root / _opf_store.journal_root(KIND) / "lock").is_file())
    for interrupt, escapes in ((KeyboardInterrupt, KeyboardInterrupt), (SystemExit, SystemExit),
                               (_InterruptingStr, KeyboardInterrupt)):
        name = "interrupt-" + interrupt.__name__
        root, rid, run = _st_build(base, name)
        escaped, fired, states, held, again = _st_interrupt_fault(apply_ingest, root, rid, interrupt)
        check(name + "-propagated", escaped is escapes and fired == [interrupt.__name__]
              and states == {1: "complete"} and not run.exists())
        # Flip: releasing the lock as the interrupt's fall-through lets the re-apply read the unconfirmed
        # COMPLETE as a verified completed no-op (promoted=True).
        check(name + "-lock-retained", held and again.promoted is False and again.outcome == "aborted"
              and "already held" in " ".join(again.findings))


TESTS = (("happy-path", _t_happy_path), ("retry-monotonic", _t_retry_monotonic),
         ("unreviewed-refused", _t_unreviewed_refused), ("traversal-refused", _t_traversal_refused),
         ("mint-move-toctou", _t_mint_move_toctou), ("per-record-refused", _t_per_record_refused),
         ("postverify-committed", _t_postverify_committed),
         ("postcommit-journal-fault", _t_postcommit_journal_fault),
         ("postlaunch-indeterminate", _t_postlaunch_indeterminate), ("postlaunch-total", _t_postlaunch_total),
         ("postlaunch-retain", _t_postlaunch_retain)) + tuple(
             ("staged-{}-{}".format(kind, case), _t_staged_apply(kind, case))
             for kind in ("import", "ingest") for case in ("clean", "corrupt", "withheld"))


def self_test(only=None):
    """Keep caller HOME/XDG out of fixture reads, including in-process production helpers."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return self_test_isolated(only)


def self_test_isolated(only=None):
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
# source (there _opf_observe.py; here _opf_allocation.py) for a guard that lives outside this file. A
# class-width guard is backed by several discriminators sharing its one mutation, one per sibling instance
# of the class, so reverting it must re-expose EVERY sibling, not only the one first cited.
#
# Every row carries a declared class. A "safety" row's mutant returns a wrong result at the outcome boundary
# (a false abort, a false promotion or completed no-op, a released lock, an escape or lost result, or a
# confirmed commit or genuine rollback misreported as indeterminate), or, for a unit-level guard, breaks that
# guard's own contract. Each row declares its asserted Boolean outcomes in _REVERT_ASSERTED; the harness
# requires a flip inside that set. Diagnostics and flips outside the declaration cannot qualify a safety row.
# A "guard-execution" row proves execution (its diagnostic, or the probe firing), not a change to its
# asserted safety outcome. Other checks may change incidentally; they do not upgrade this claim,
# and a sibling "/isolated" row proves the safety property instead. An isolated row tests the guard's
# unit contract directly, removes an overlapping finding from its fixture, or strips the named layers in the
# CANDIDATE only (never in this file): its baseline, the stripped source, must PASS
# with the guard alone, its mutant (the baseline plus the guard's reversal) must go RED, and the full pristine
# source must still PASS.

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


def _d_inline_required(module, base_dir):
    """The inline-layout requirement in `_apply_locked`: a per-record store is refused before any id is
    read or reserved. Reverting it lets the inline id union read the store blind, so apply mints a BI-1
    that collides with the per-record BI-1 and promotes."""
    root, rid, _run = module._st_build(base_dir, "per-record")
    module._st_per_record(root)
    with _opf_import._self_test_homes2_active(root):
        result = module.apply_ingest(root, rid, now=module._NOW)
    _revert_check(result.promoted is False and result.verdict == module.CANNOT_EVALUATE,
                  "per-record/inline-required")


def _d_staged_run_store_depth(kind):
    """Reverting the locator must fail the end-to-end corrupt case's own assertion, for each home."""
    def test(module, base_dir):
        _revert_check(_st_staged_apply(module, base_dir, kind, "corrupt"),
                      "gate/staged-run-store-depth/" + kind)
    return test


def _d_staging_generation_mismatch(kind):
    """A registered but inadmissible shape is never detached, with or without a .working decoy."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, run = _st_build(base_dir, "staging-generation-" + kind)
        staged = root / _opf_store.stage_run(kind, rid)
        staged.parent.mkdir(parents=True)
        run.rename(staged)
        decoy = root / ".working" / _opf_import.IMPORT_OPS_REL / rid / _opf_import.TRANSACTION_NAME
        error = ("cannot evaluate: cannot open the store root beneath the run dir no-follow "
                 "(run path is registered outside homes generation 1)")
        outcomes = []
        for with_decoy in (False, True):
            if with_decoy:
                decoy.parent.mkdir(parents=True)
                decoy.write_bytes(b"state =\n")
            for supported, supplied in ((1, 1), (1, None), (2, 1)):
                with patch.object(_opf_store, "SUPPORTED_HOMES", supported):
                    result = module.check_staged_run(staged, homes=supplied)
                outcomes.append(all(result[cid] == (False, error) for cid in
                                    ("transaction-schema", "transaction-consistency")))
        _revert_check(all(outcomes), "gate/staging-generation-mismatch/" + kind)
    return test


def _d_staging_alias(kind):
    """An alias of a registered run (a dotdot, relative, or ancestor-symlink spelling) is located by descriptor
    identity: it refuses the generation mismatch, reads the true store-root record, and ignores a .working decoy.
    Restoring a literal-suffix match on the physical walk loses every alias's located home."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, run = _st_build(base_dir, "staging-alias-" + kind)
        staged = root / _opf_store.stage_run(kind, rid)
        staged.parent.mkdir(parents=True)
        run.rename(staged)
        link = base_dir / ("staging-alias-link-" + kind)
        link.symlink_to(staged.parent)
        record = root / _opf_import.IMPORT_OPS_REL / rid / _opf_import.TRANSACTION_NAME
        decoy = root / ".working" / _opf_import.IMPORT_OPS_REL / rid / _opf_import.TRANSACTION_NAME
        record.parent.mkdir(parents=True)
        decoy.parent.mkdir(parents=True)
        error = ("cannot evaluate: cannot open the store root beneath the run dir no-follow "
                 "(run path is registered outside homes generation 1)")
        outcomes = []
        for corrupt in (record, decoy):
            corrupt.write_bytes(b"state =\n")
            for spelling, cwd in ((staged / ".." / rid, None), (Path(rid), staged.parent), (link / rid, None)):
                cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
                try:
                    if cwd is not None:
                        os.chdir(str(cwd))
                    with patch.object(_opf_store, "SUPPORTED_HOMES", 1):
                        legacy = module.check_staged_run(spelling, homes=1)
                    with _opf_import._self_test_homes2_active(root):
                        located = module.check_staged_run(spelling, homes=2)
                finally:
                    os.fchdir(cwd_fd)
                    os.close(cwd_fd)
                outcomes.append(all(legacy[cid] == (False, error) for cid in
                                    ("transaction-schema", "transaction-consistency")))
                outcomes.append(located["transaction-schema"][0] is (corrupt is decoy))
            corrupt.unlink()
        _revert_check(all(outcomes), "gate/staging-alias/" + kind)
    return test


_ST_TXN = ("transaction-schema", "transaction-consistency")

# R1's exact fail-closed diagnostic, shared by the symlink-route and unheld-relative fixtures.
_ST_HOME_REFUSED = "name a run not held by a store by an absolute, symlink-free path"
_ST_DETACHED = (True, "no transaction record (run not yet applied)")


def _st_staged(base_dir, name, kind, corrupt=True):
    """An _st_build run moved to its `kind` staging home, beside a corrupt store-root transaction record when
    `corrupt`. Returns (root, rid, staged)."""
    root, rid, run = _st_build(base_dir, name)
    staged = root / _opf_store.stage_run(kind, rid)
    staged.parent.mkdir(parents=True)
    run.rename(staged)
    if corrupt:
        record = root / _opf_import.IMPORT_OPS_REL / rid / _opf_import.TRANSACTION_NAME
        record.parent.mkdir(parents=True)
        record.write_bytes(b"state =\n")
    return root, rid, staged


def _st_graded(module, root, spelling, homes, cwd=None):
    """The candidate gate's verdict on `spelling` (from `cwd` when given) under homes generation `homes`."""
    from unittest.mock import patch
    cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        if cwd is not None:
            os.chdir(str(cwd))
        if homes == 1:
            with patch.object(_opf_store, "SUPPORTED_HOMES", 1):
                return module.check_staged_run(spelling, homes=1)
        with _opf_import._self_test_homes2_active(root):
            return module.check_staged_run(spelling, homes=2)
    finally:
        os.fchdir(cwd_fd)
        os.close(cwd_fd)


def _st_located(detail):
    return "cannot evaluate: cannot open the store root beneath the run dir no-follow ({})".format(detail)


def _d_run_name_bound(kind):
    """A registered run renamed to a non-run-id entry and spelled `link/.` or `link/` through a symlink named
    as the run is refused whole (the spelled name is not its own entry). Removing the name binding grades the
    staged checks under the link's name, so staged-run-structure passes a directory whose entry is no run id."""
    def test(module, base_dir):
        root, rid, staged = _st_staged(base_dir, "name-bound-" + kind, kind)
        moved = staged.parent / "renamed"
        staged.rename(moved)
        link = base_dir / ("name-bound-link-" + kind) / rid
        link.parent.mkdir()
        link.symlink_to(moved)
        unbound = ("cannot evaluate: the staged run dir's spelled name {!r} is not its own directory entry (a "
                   "symlink reached through a '/.' or trailing '/' spelling, or a renamed run)".format(rid))
        outcomes = [_st_graded(module, root, str(link) + suffix, homes) ==
                    dict((cid, (False, unbound)) for cid in module.EXPECTED_CHECKS)
                    for suffix in ("/.", "/") for homes in (1, 2)]
        _revert_check(all(outcomes), "gate/run-name-bound/" + kind)
    return test


def _d_home_symlink_route(kind):
    """A symlinked staging home refuses whatever the spelling (canonical, dotdot, "/.", trailing "/", an ancestor
    symlink). Removing the spelled-route provenance probe grades each as detached instead of refusing."""
    def test(module, base_dir):
        root, rid, staged = _st_staged(base_dir, "symlink-route-" + kind, kind, corrupt=False)
        link = base_dir / ("symlink-route-link-" + kind)
        link.symlink_to(staged.parent)
        staging = root / _opf_store.STAGING_REL
        elsewhere = base_dir / ("symlink-route-elsewhere-" + kind)
        staging.rename(elsewhere)
        staging.symlink_to(elsewhere)
        error = _st_located(_ST_HOME_REFUSED)
        outcomes = []
        for spelling in (staged, staged / ".." / rid, str(staged) + "/.", str(staged) + "/", link / rid):
            for homes in (1, 2):
                result = _st_graded(module, root, spelling, homes)
                outcomes.append(all(result[cid] == (False, error) for cid in _ST_TXN))
        _revert_check(all(outcomes), "gate/home-symlink-route/" + kind)
    return test



def _d_home_relative(kind, homes, case):
    """R1 refuses a relative symlink-bearing unheld route from every generated starting depth."""
    def test(module, base_dir):
        root, rid, staged = _st_staged(base_dir, "relative-" + kind, kind, corrupt=False)
        if case.startswith("legacy"):
            run = root / _opf_import._import_run_locations(rid, 1)[0]
            staged.rename(run)
        else:
            run = staged
        if case.endswith("entry"):
            link = run
            elsewhere = base_dir / "elsewhere" / rid
            elsewhere.parent.mkdir()
            cwd = run.parent / "w1" / "w2" / "w3"
        else:
            link = run.parent if case.startswith("legacy") else root / _opf_store.STAGING_REL
            elsewhere = base_dir / "elsewhere"
            cwd = root / ".working" / "w1" / "w2" / "w3"
        cwd.mkdir(parents=True)
        spelling = os.path.relpath(str(run), str(cwd)) + ("/" if case.endswith("entry") else "")
        link.rename(elsewhere)
        link.symlink_to(elsewhere)
        rel = str(run.relative_to(root))
        error = _st_located(_ST_HOME_REFUSED)
        absolute = _st_graded(module, root, str(run) + "/", homes)
        relative = _st_graded(module, root, spelling, homes, cwd)
        _revert_check(all(result[cid] == (False, error) for result in (absolute, relative)
                          for cid in _ST_TXN),
                      "gate/home-relative/{}/{}/homes-{}".format(case, kind, homes))
    return test


def _st_route_facts(cwd, spelling, depth):
    """Fixture oracle in string space, independent of the gate's descriptor walk and claim probes.
    Fixture links have absolute physical targets; procfs may use relative targets. Claimants come from
    fixture construction, never from the classifier. Include starting/per-edge physical ancestors."""
    parts = spelling.split("/")
    cur = "/" if spelling.startswith("/") else os.path.realpath(cwd)
    links, visited = 0, {cur}

    def ancestors(path):
        for _ in range(depth):
            path = os.path.dirname(path.rstrip("/")) or "/"
            visited.add(path)

    if not spelling.startswith("/"):
        ancestors(cur)
    while parts:
        comp = parts.pop(0)
        if comp in ("", "."):
            continue
        if comp == "..":
            cur = os.path.dirname(cur.rstrip("/")) or "/"
            visited.add(cur)
            continue
        step = cur.rstrip("/") + "/" + comp
        if os.path.islink(step):
            ancestors(cur)
            links += 1
            if links > 40:
                raise RuntimeError("fixture route loops")
            target = os.readlink(step)
            parts[:0] = target.split("/")
            if target.startswith("/"):
                cur = "/"
        else:
            cur = step
        visited.add(cur)
    return links, visited


def _st_home_verdict(result, reads, root, rid, corrupt=True):
    """Registered requires the expected transaction read at the physical store identity.
    Neither a schema failure nor an absent-record verdict alone proves a registered read."""
    schema = result["transaction-schema"]
    if schema[1].startswith("cannot evaluate:"):
        return "refused"
    st = root.stat()
    txn = str(Path(_opf_import.IMPORT_OPS_REL) / rid / _opf_import.TRANSACTION_NAME)
    body = b"state =\n" if corrupt else None
    if schema[0] is (not corrupt) and ((st.st_dev, st.st_ino), txn, body) in reads:
        return "registered"
    if schema == _ST_DETACHED:
        return "detached"
    return "unexpected:" + repr(schema)


# This registry defines the generated matrix. Holder/claimant facts come from construction below.
_ST_HOME_PLACEMENTS = ("none", "staging", "legacy", "entry", "ancestor-alias", "in-store-alias",
                       "external-link", "shared-ancestor", "detached", "chained", "nested-shared",
                       "external-root-out", "external-target", "proc-fd", "detached-alias",
                       "same-root-alias", "cwd-alias", "held-cwd-alias", "detached-relative", "detached-absolute")
_ST_HOME_HELD = ("none", "ancestor-alias", "external-link", "shared-ancestor", "nested-shared",
                "same-root-alias", "held-cwd-alias")


def _st_home_topology(base_dir, kind, placement, corrupt=True):
    """Return root, canonical, physical, aliases, inside cwds, physical holder, second-claim roots.
    The caller owns proc-fd lifetime; the proc spelling is added after construction."""
    import shutil
    name = "prop-{}-{}".format(placement, kind)
    root, rid, staged = _st_staged(base_dir, name, kind, corrupt=corrupt)
    legacy = root / _opf_import._import_run_locations(rid, 1)[0]
    aliases, inside, claimants = [], [], []
    held = placement in _ST_HOME_HELD
    if held:
        staged.rename(legacy)
        canonical = physical = legacy
        if placement == "ancestor-alias":
            link = base_dir / (name + "-link")
            link.symlink_to(root)
            aliases.append(str(link / legacy.relative_to(root)))
        if placement == "same-root-alias":
            staged.symlink_to(legacy)
            aliases.append(str(staged) + "/")
        if placement == "held-cwd-alias":
            claimant = base_dir / (name + "-claimant")
            link = (claimant / _opf_import._import_run_locations(rid, 1)[0]).parent
            link.parent.mkdir(parents=True)
            link.symlink_to(legacy.parent)
            claimants.append(claimant)
            inside.append(link)
        if placement == "external-link":
            claimant = base_dir / (name + "-x")
            link = claimant / _opf_import._import_run_locations(rid, 1)[0]
            link.parent.mkdir(parents=True)
            link.symlink_to(legacy)
            claimants.append(claimant)
            aliases.extend([str(link), str(link) + "/"])
        if placement in ("shared-ancestor", "nested-shared"):
            ancestor = base_dir / (name + "-A")
            (ancestor / "imports").mkdir(parents=True)
            destination = ancestor / ("imports/S" if placement == "nested-shared" else "S")
            root.rename(destination)
            root = destination
            canonical = physical = root / _opf_import._import_run_locations(rid, 1)[0]
            (ancestor / "imports" / rid).symlink_to(canonical)
            shared = base_dir / (name + "-C")
            shared.mkdir()
            (shared / ".working").symlink_to(ancestor)
            claimants.append(shared)
            aliases.append(str(shared / ".working" / root.relative_to(ancestor)
                               / canonical.relative_to(root)))
    elif placement in ("detached", "detached-alias", "detached-relative", "detached-absolute"):
        canonical = physical = base_dir / (name + "-copy") / "a" / "b" / rid
        canonical.parent.mkdir(parents=True)
        shutil.copytree(str(staged), str(canonical))
        if placement == "detached-alias":
            link = base_dir / (name + "-tmp")
            link.symlink_to(canonical.parent)
            aliases.append(str(link / rid))
    elif placement == "entry":
        elsewhere = base_dir / (name + "-elsewhere") / rid
        elsewhere.parent.mkdir()
        staged.rename(elsewhere)
        staged.symlink_to(elsewhere)
        canonical, physical = staged, elsewhere
        inside.append(elsewhere.parent)
    else:
        component = legacy.parent if placement == "legacy" else root / _opf_store.STAGING_REL
        if placement == "legacy":
            staged.rename(legacy)
            staged = legacy
        elsewhere = base_dir / (name + "-elsewhere")
        component.rename(elsewhere)
        component.symlink_to(elsewhere)
        canonical = staged
        physical = elsewhere / canonical.relative_to(component)
        inside.append(physical.parent)
        if placement == "cwd-alias":
            # The chdir traverses a link; the supplied run name records none of that history.
            link = base_dir / (name + "-cwd")
            link.symlink_to(physical.parent)
            inside[:] = [link]
        if placement == "in-store-alias":
            (root / ".working" / "alt").symlink_to(elsewhere)
            aliases.append(str(root / ".working" / "alt" / canonical.relative_to(component)))
        if placement == "chained":
            target = base_dir / (name + "-Z")
            physical.parent.rename(target)
            (elsewhere / kind).symlink_to(target)
            physical = target / rid
            deep = elsewhere / "d1" / "d2" / "d3" / "d4"
            deep.mkdir(parents=True)
            inside[:] = [deep, target]
            # From deep, this traverses only the second registered-component link (codex F1).
            aliases.append(str(elsewhere / kind / rid))
        if placement == "external-root-out":
            link = base_dir / (name + "-LR")
            link.symlink_to(root)
            aliases.append(str(link) + "/../" + str(physical.relative_to(root.parent)))
        if placement == "external-target":
            link = base_dir / (name + "-L")
            link.symlink_to(physical.parent)
            aliases.append(str(link / rid))
    return root, canonical, physical, aliases, inside, held, claimants


def _st_home_property(module, base_dir, kind, placements=_ST_HOME_PLACEMENTS, deep_only=False, corrupt=True,
                      asserted_identity=None):
    """Every matrix cell is independently classified from absolute spelling, links, holder, and second claim.
    No canonical-verdict equivalence substitutes for the structural expectation. Count each mismatching
    cell once. Proc magic links keep their descriptor live for the entire placement."""
    from unittest.mock import patch
    mismatches, count = [], 0
    protected_calls = []
    for placement in placements:
        root, canonical, physical, aliases, inside, held, claimants = _st_home_topology(
            base_dir, kind, placement, corrupt=corrupt)
        rid = physical.name
        depth = max(len(rel.split("/")) for gen in (1, 2)
                    for rel in _opf_import._import_run_locations(rid, gen)) - 1
        if placement in ("cwd-alias", "held-cwd-alias"):
            cwds = inside
        elif placement in ("detached-relative", "detached-absolute"):
            cwds = [canonical.parent]
        elif placement not in ("detached", "detached-alias"):
            deep = root / ".working" / "w2" / "w3" / "w4" / "w5" / "w6"
            deep.mkdir(parents=True)
            cwds = [base_dir, root] + [Path(*deep.parts[:len(root.parts) + n]) for n in range(1, 7)]
        else:
            cwds = [base_dir, canonical.parents[1], canonical.parent]
        if placement not in ("cwd-alias", "held-cwd-alias"):
            cwds += inside
        if deep_only:
            cwds = [cwd for cwd in cwds if len(cwd.parts) - len(root.parts) >= 4]
        proc_fd = None
        try:
            if placement == "proc-fd":
                proc_fd = os.open(str(physical.parent), os.O_RDONLY | os.O_DIRECTORY)
                aliases.append("/proc/self/fd/{}/{}".format(proc_fd, rid))
            for homes in (1, 2):
                for cwd in cwds:
                    spellings = [("canonical", str(canonical)), ("canonical-slash", str(canonical) + "/"),
                                 ("canonical-dot", str(canonical) + "/."),
                                 ("relative", os.path.relpath(str(canonical), str(cwd)))]
                    physical_rel = os.path.relpath(str(physical), str(cwd))
                    if physical_rel != spellings[-1][1]:
                        spellings.append(("relative-physical", physical_rel))
                    spellings += [("alias-{}".format(i), a) for i, a in enumerate(aliases)]
                    if placement == "chained":
                        spellings.append(("relative-chain", os.path.relpath(aliases[0], str(cwd))))
                    if placement in ("cwd-alias", "held-cwd-alias", "detached-relative", "detached-absolute"):
                        spelling = str(physical) if placement == "detached-absolute" else rid
                        spellings = [("bare", spelling), ("slash", spelling + "/"),
                                     ("dot", spelling + "/.")]
                    if physical != canonical:
                        # Pin the untraversed-home-link residual, including the earlier-chdir placement.
                        spellings.append(("physical", str(physical)))
                    for label, spelling in spellings:
                        if (placement, homes, cwd, label) == ("shared-ancestor", 1, base_dir, "alias-0"):
                            protected_calls.append(count)
                        links, visited = _st_route_facts(cwd, spelling, depth)
                        second = any(str(claimant) in visited for claimant in claimants)
                        if placement == "held-cwd-alias":
                            # chdir consumed the claimant's registered-component link before grading.
                            # Its history is unavailable to R2; the relative run stays physically held.
                            if not held or links or second or spelling.startswith("/"):
                                raise RuntimeError("held earlier-chdir residual fixture is not isolated")
                        expected = ("refused" if second else "registered") if held else (
                            "refused" if links or not spelling.startswith("/") else "detached")
                        reads = []
                        read_control = module._read_store_control

                        def read(fd, rel):
                            body = read_control(fd, rel)
                            st = os.fstat(fd)
                            reads.append(((st.st_dev, st.st_ino), rel, body))
                            return body

                        with patch.object(module, "_read_store_control", read):
                            result = _st_graded(module, root, spelling, homes, cwd)
                        got = _st_home_verdict(result, reads, root, rid, corrupt=corrupt)
                        count += 1
                        case = "{}/{}/homes-{}/{}/{}".format(placement, kind, homes, cwd, label)
                        if got != expected:
                            mismatches.append("{}: expected {}, got {}".format(case, expected, got))
        finally:
            if proc_fd is not None:
                os.close(proc_fd)
    # Bind the declared ordinal to the actual matrix cell, even when corrupt bits all stay False.
    if asserted_identity is not None and (asserted_identity.rsplit("/", 1)[-1] != kind
            or len(protected_calls) != 1 or any(
            ordinal != protected_calls[0]
            for ordinal, _cid, _expected in _REVERT_ASSERTED[asserted_identity])):
        raise RuntimeError("asserted home call identity drift: " + asserted_identity)
    return count, mismatches


def _t_home_property(base, check):
    """The enumerative spelling-equivalence property over the generated topology matrix, both kinds."""
    import check_opf_import as gate
    total = 0
    for kind in ("import", "ingest"):
        count, mismatches = _st_home_property(gate, base / ("home-property-" + kind), kind)
        total += count
        if mismatches:
            print("HOME-PROPERTY MISMATCHES ({}):\n  {}".format(kind, "\n  ".join(mismatches[:20])),
                  file=sys.stderr)
        check("home-property-" + kind, not mismatches)
    print("OPF-INGEST-APPLY HOME-PROPERTY: {} generated cases".format(total))


# The enumerative property runs in the plain self-test over the full declared matrix.
TESTS += (("home-property", _t_home_property),)


def _d_home_claim_ancestors(kind, starting=False):
    """R2 needs both ancestor probes: the foreign claim is reached only by the selected probe."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, staged = _st_staged(base_dir, "claim-" + kind, kind, corrupt=False)
        run = root / _opf_import._import_run_locations(rid, 1)[0]
        staged.rename(run)
        foreign = base_dir / "foreign"
        working = foreign / ".working"
        deep = working / "d1" / "d2" / "d3" / "d4"
        deep.mkdir(parents=True)
        (working / "imports").symlink_to(run.parent)
        link = deep / "L" if starting else working / "alt"
        link.symlink_to(run.parent)
        cwd = working if starting else deep
        spelling = ("d1/d2/d3/d4/L/" if starting else "../../../../alt/") + rid
        outcomes = []
        read_control = module._read_store_control
        st = root.stat()
        txn = str(Path(_opf_import.IMPORT_OPS_REL) / rid / _opf_import.TRANSACTION_NAME)
        for homes in (1, 2):
            reads = []

            def read(fd, rel):
                body = read_control(fd, rel)
                opened = os.fstat(fd)
                reads.append(((opened.st_dev, opened.st_ino), rel, body))
                return body

            with patch.object(module, "_read_store_control", read):
                clean = _st_graded(module, root, run, homes)
            result = _st_graded(module, root, spelling, homes, cwd)
            outcomes.append(((st.st_dev, st.st_ino), txn, None) in reads
                            and all(clean[cid][0] for cid in _ST_TXN)
                            and all(not result[cid][0] and "ambiguous second store claim" in result[cid][1]
                                    for cid in _ST_TXN))
        identity = "gate/home-start-ancestors/" if starting else "gate/home-property-ancestors/"
        _revert_check(all(outcomes), identity + kind)
    return test


def _d_home_property_ancestors(kind):
    return _d_home_claim_ancestors(kind)


def _d_home_start_ancestors(kind):
    return _d_home_claim_ancestors(kind, starting=True)


def _d_home_property_holding(kind):
    """The old seen-set restriction misses a second claim through the shared-ancestor alias."""
    def test(module, base_dir):
        count, mismatches = _st_home_property(
            module, base_dir, kind,
            placements=("none", "same-root-alias", "shared-ancestor", "nested-shared"),
            asserted_identity="gate/home-property-holding/" + kind)
        _revert_check(count and not mismatches, "gate/home-property-holding/" + kind)
    return test


def _d_home_rule(kind, rule):
    def test(module, base_dir):
        placements = (("chained", "external-root-out", "external-target", "proc-fd", "detached-alias")
                      if rule == "unheld-link" else ("shared-ancestor", "nested-shared"))
        count, mismatches = _st_home_property(
            module, base_dir, kind, placements=placements, corrupt=(rule != "unheld-link"),
            asserted_identity="gate/home-second-claim/" + kind if rule == "second-claim" else None)
        _revert_check(count and not mismatches, "gate/home-" + rule + "/" + kind)
    return test


# Discriminator factories are not lifecycle delegates; reserve the _isolated
# suffix for self-test bodies reached through an environment wrapper.
def _d_home_second_claim_clean_store(kind, rule):
    """A clean held store isolates second-claim refusal from corrupt-record findings."""
    def test(module, base_dir):
        root, canonical, _physical, aliases, _inside, held, claimants = _st_home_topology(
            base_dir, kind, "shared-ancestor", corrupt=False)
        if not held or len(claimants) != 1 or len(aliases) != 1:
            raise RuntimeError("second-claim isolation fixture is malformed")
        outcomes = []
        for homes in (1, 2):
            clean = _st_graded(module, root, canonical, homes)
            claimed = _st_graded(module, root, aliases[0], homes)
            outcomes.append(all(clean[cid][0] for cid in _ST_TXN)
                            and all(not claimed[cid][0] and "ambiguous second store claim" in claimed[cid][1]
                                    for cid in _ST_TXN))
        _revert_check(all(outcomes), "gate/home-" + rule + "/" + kind + "/isolated")
    return test


def _d_home_unheld_relative(kind):
    """Earlier chdir links and genuinely detached relative names both refuse; absolute twins still grade."""
    def test(module, base_dir):
        outcomes = []
        for placement in ("cwd-alias", "detached-relative", "detached-absolute"):
            root, _canonical, physical, _aliases, inside, _held, _claims = _st_home_topology(
                base_dir, kind, placement, corrupt=False)
            cwd = inside[0] if placement == "cwd-alias" else physical.parent
            for homes in (1, 2):
                relative = _st_graded(module, root, physical.name, homes, cwd)
                absolute = _st_graded(module, root, physical, homes, cwd)
                outcomes.append(all(relative[cid] == (False, _st_located(_ST_HOME_REFUSED))
                                    and absolute[cid] == _ST_DETACHED for cid in _ST_TXN))
        _revert_check(all(outcomes), "gate/home-unheld-relative/" + kind)
    return test


def _d_home_search_only(kind, homes, case):
    """Real 0311 permissions, restored even on failure; never accept an ineffective chmod fixture."""
    def test(module, base_dir):
        import shutil
        import stat
        from unittest.mock import patch
        root, rid, staged = _st_staged(base_dir, "search-" + kind, kind, corrupt=False)
        if homes == 1 and case != "detached":
            run = root / _opf_import._import_run_locations(rid, 1)[0]
            staged.rename(run)
        else:
            run = staged
        if case == "detached":
            run = base_dir / "copy" / "a" / "b" / rid
            run.parent.mkdir(parents=True)
            shutil.copytree(staged, run)
            restricted = run.parent
        elif case == "physical":
            restricted = run.parent
        else:
            restricted = root.parent
        clean = _st_graded(module, root, run, homes)
        mode = stat.S_IMODE(restricted.stat().st_mode)
        restricted.chmod(0o311)
        try:
            try:
                fd = os.open(str(restricted), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except PermissionError:
                pass
            else:
                os.close(fd)
                raise RuntimeError("search-only fixture still permits directory reads")
            restricted_result = _st_graded(module, root, run, homes)
            restricted.chmod(0)
            with patch.object(_opf_store, "SUPPORTED_HOMES", homes):
                unreachable = module.check_staged_run(run, homes=homes)
        finally:
            restricted.chmod(mode)
        _revert_check(restricted_result == clean and all(clean[cid][0] for cid in _ST_TXN)
                      and all(not unreachable[cid][0] for cid in _ST_TXN),
                      "gate/home-search-only/{}/{}/homes-{}".format(case, kind, homes))
    return test


def _d_home_external_symlink(kind, homes):
    """A visited second claimant refuses even without threading its registered location (literal R2)."""
    def test(module, base_dir):
        root, rid, run = _st_staged(base_dir, "external-" + kind, kind, corrupt=False)
        if homes == 1:
            legacy = root / _opf_import._import_run_locations(rid, 1)[0]
            run.rename(legacy)
            run = legacy
        clean = _st_graded(module, root, run, homes)
        link = base_dir / _opf_import._import_run_locations(rid, 1)[0]
        link.parent.mkdir(parents=True)
        link.symlink_to(run)
        absolute = _st_graded(module, root, run, homes)
        relative = _st_graded(module, root, rid, homes, run.parent)
        through_link = _st_graded(module, root, str(link) + "/", homes)
        depth = max(len(rel.split("/")) for gen in (1, 2)
                    for rel in _opf_import._import_run_locations(rid, gen)) - 1
        relative_claimed = len(run.parent.relative_to(base_dir).parts) <= depth
        _revert_check(all(clean[cid][0] for cid in _ST_TXN)
                      and all(not result[cid][0] for result in (absolute, through_link) for cid in _ST_TXN)
                      and (all(not relative[cid][0] for cid in _ST_TXN) if relative_claimed
                           else relative == clean),
                      "gate/home-external-symlink/{}/homes-{}".format(kind, homes))
    return test


def _d_home_cwd_bound(kind, homes):
    """Reclassification of a relative spelling uses the same starting descriptor after ambient chdir."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, run = _st_staged(base_dir, "cwd-" + kind, kind, corrupt=False)
        if homes == 1:
            legacy = root / _opf_import._import_run_locations(rid, 1)[0]
            run.rename(legacy)
            run = legacy
        clean = _st_graded(module, root, rid, homes, run.parent)
        classify, calls = module._classify_run_homes, []

        def shifted(rd):
            calls.append(True)
            if len(calls) == 2:
                os.chdir(str(base_dir))
            return classify(rd)

        with patch.object(module, "_classify_run_homes", shifted):
            shifted_result = _st_graded(module, root, rid, homes, run.parent)
        _revert_check(all(clean[cid][0] for cid in _ST_TXN)
                      and len(calls) > 1 and shifted_result == clean,
                      "gate/home-cwd-bound/{}/homes-{}".format(kind, homes))
    return test


def _st_route_while_renamed(module, component, moved):
    """Resolve the absolute spelling during a brief restoration, then leave the ancestry renamed.
    This non-atomic schedule isolates retained binding from R1's refusal of unheld relative names."""
    route = module._spelled_route

    def restored(rd, visit, depth):
        moved.rename(component)
        try:
            return route(rd, visit, depth)
        finally:
            component.rename(moved)
    return restored


def _d_home_restore(kind, homes):
    """A component restored after initial detached classification refuses at the next classification."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, run = _st_staged(base_dir, "restore-" + kind, kind, corrupt=False)
        component = root / _opf_store.STAGING_REL
        if homes == 1:
            legacy = root / _opf_import._import_run_locations(rid, 1)[0]
            run.rename(legacy)
            run = legacy
            component = run.parent
        clean = _st_graded(module, root, run, homes)
        moved = component.with_name("renamed-home")
        classify, calls = module._classify_run_homes, []

        def renamed(rd):
            calls.append(True)
            if len(calls) == 1:
                component.rename(moved)
                try:
                    return classify(rd)
                finally:
                    moved.rename(component)
            return classify(rd)

        error = _st_located("the run's registered home changed during grading (the run or a store component "
                            "was renamed or replaced); fail-closed, never re-read as detached")
        route = module._spelled_route

        # Construct the wrapper before patching, so it retains the real route resolver.
        restoring_route = _st_route_while_renamed(module, component, moved)

        def first_route(rd, visit, depth):
            return restoring_route(rd, visit, depth) if len(calls) == 1 else route(rd, visit, depth)

        with patch.object(module, "_classify_run_homes", renamed), \
                patch.object(module, "_spelled_route", first_route):
            result = _st_graded(module, root, run, homes)
        _revert_check(all(clean[cid][0] for cid in _ST_TXN)
                      and len(calls) > 1 and all(result[cid] == (False, error) for cid in _ST_TXN),
                      "gate/home-restore/{}/homes-{}".format(kind, homes))
    return test


def _d_home_speculative(kind):
    """An unrelated <store>/.working/.working file changes no result of a canonical staged run. Making a
    speculative candidate's unresolvable probe fatal refuses the run on that obstruction."""
    def test(module, base_dir):
        root, _rid, staged = _st_staged(base_dir, "speculative-" + kind, kind, corrupt=False)
        clean = [_st_graded(module, root, staged, homes) for homes in (1, 2)]
        (root / ".working" / ".working").write_bytes(b"unrelated\n")
        obstructed = [_st_graded(module, root, staged, homes) for homes in (1, 2)]
        _revert_check(obstructed == clean, "gate/home-speculative/" + kind)
    return test


def _d_home_rename_at_lookup(kind):
    """Guard execution: a renamed run refuses on its own entry, both at transaction lookup (an empty
    directory at its former name) and before first classification (a symlink at its bound name). The route
    identity check and R1 still refuse the mutant; the /isolated sibling tests this guard's contract."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, staged = _st_staged(base_dir, "rename-lookup-" + kind, kind)
        moved = staged.parent / "moved"
        error = _st_located("the bound run name {!r} no longer names the opened run directory (renamed or "
                            "replaced during grading); fail-closed, never classified as detached".format(rid))
        real_store_fd, real_tree = module._staged_run_store_fd, module._list_run_tree

        def renaming(rd, homes):
            staged.rename(moved)
            staged.mkdir()
            return real_store_fd(rd, homes)

        def swapping(fd):
            staged.rename(moved)
            staged.symlink_to("moved")
            return real_tree(fd)
        outcomes = []
        for homes in (1, 2):
            with patch.object(module, "_staged_run_store_fd", side_effect=renaming):
                result = _st_graded(module, root, staged / ".." / rid, homes)
            staged.rmdir()
            moved.rename(staged)
            outcomes.append(all(result[cid] == (False, error) for cid in _ST_TXN))
            with patch.object(module, "_list_run_tree", side_effect=swapping):
                result = _st_graded(module, root, rid + "/.", homes, cwd=staged.parent)
            staged.unlink()
            moved.rename(staged)
            outcomes.append(all(result[cid] == (False, error) for cid in _ST_TXN))
        _revert_check(all(outcomes), "gate/home-rename-at-lookup/" + kind)
    return test


def _d_home_rename_at_lookup_unit(kind):
    """Unit safety: a bound-name mismatch must raise, never return None ("not held"). Call the physical
    probe directly so route identity, R1 and retained-binding refusals cannot mask an unsafe fallback."""
    def test(module, base_dir):
        root, _rid, staged = _st_staged(base_dir, "rename-isolated-" + kind, kind, corrupt=False)
        rel = str(staged.relative_to(root))
        moved = staged.parent / "moved"
        rd = module._RunDir(staged)
        outcomes = []
        try:
            if rd.home_error is not None or rel not in (rd.home_binding or {}):
                raise RuntimeError("isolated bound-name fixture has no initial staging binding")
            for replacement in ("directory", "symlink"):
                staged.rename(moved)
                if replacement == "directory":
                    staged.mkdir()
                else:
                    staged.symlink_to("moved")
                fd, refused = None, False
                try:
                    try:
                        fd = module._physical_home(rd, rel)
                    except module._GateError:
                        refused = True
                    outcomes.append(refused)
                finally:
                    if fd is not None:
                        os.close(fd)
                    if replacement == "directory":
                        staged.rmdir()
                    else:
                        staged.unlink()
                    moved.rename(staged)
        finally:
            rd.close()
        _revert_check(all(outcomes), "gate/home-rename-at-lookup/" + kind + "/isolated")
    return test


def _d_home_retained(kind):
    """A store component renamed at lookup, briefly restored for absolute-route resolution, leaves the run
    physically unregistered at each ancestry probe. Retained binding refuses; serving only the fresh
    classification instead downgrades to detached instead of refusing the changed home."""
    def test(module, base_dir):
        from unittest.mock import patch
        root, rid, staged = _st_staged(base_dir, "retained-" + kind, kind, corrupt=False)
        staging = root / _opf_store.STAGING_REL
        error = _st_located("the run's registered home changed during grading (the run or a store component was "
                            "renamed or replaced); fail-closed, never re-read as detached")
        real_store_fd = module._staged_run_store_fd

        def moving(rd, homes):
            staging.rename(staging.parent / "moved")
            return real_store_fd(rd, homes)
        outcomes = []
        for homes in (1, 2):
            route = _st_route_while_renamed(module, staging, staging.parent / "moved")
            # The first classification precedes the move; subsequent route resolutions see a brief restore.
            def resolving(rd, visit, depth):
                return route(rd, visit, depth) if (staging.parent / "moved").exists() else real_route(rd, visit, depth)

            real_route = module._spelled_route
            with patch.object(module, "_staged_run_store_fd", side_effect=moving), \
                    patch.object(module, "_spelled_route", resolving):
                result = _st_graded(module, root, staged, homes)
            (staging.parent / "moved").rename(staging)
            outcomes.append(all(result[cid] == (False, error) for cid in _ST_TXN))
        _revert_check(all(outcomes), "gate/home-retained/" + kind)
    return test


def _d_transaction_generation_required(module, base_dir):
    """The internal reader must refuse invalid generations independently of the public boundary."""
    from unittest.mock import patch
    _root, _rid, run = _st_build(base_dir, "transaction-generation")
    rd = module._RunDir(run)
    try:
        with patch.object(_opf_store, "SUPPORTED_HOMES", 1):
            result = module._check_staged_run(rd, homes=3)
    finally:
        rd.close()
    error = "the supplied homes generation 3 is not 1 or 2, or is above the tooling's supported generation 1"
    _revert_check(all(result[cid] == (False, error) for cid in
                      ("transaction-schema", "transaction-consistency")),
                  "gate/transaction-generation-required")


# Post-launch probes: each drives one sibling of the committed-reported-as-aborted class (or the genuine
# rollback it must not absorb) through a candidate module and returns whether it is reported truthfully. An
# indeterminate probe also requires the journal-evidence finding, so the helper-failure guard's own
# indeterminate result cannot mask a reverted inner guard.

def _p_postverify_committed(module, base_dir):
    """A JournalError in post-commit verification is committed-but-unverified, never aborted."""
    root, rid, run = module._st_build(base_dir, "postverify")
    result, fired = module._st_postverify_fault(module.apply_ingest, root, rid, run, "source",
                                                _journal.JournalError)
    return len(fired) == 1 and result.promoted is True and result.outcome == "promoted"


def _p_postverify_foreign(module, base_dir):
    """A verification exception outside (JournalError, OSError) is committed-but-unverified too."""
    root, rid, run = module._st_build(base_dir, "postverify-foreign")
    result, fired = module._st_postverify_fault(module.apply_ingest, root, rid, run, "source", RecursionError)
    return len(fired) == 1 and result.promoted is True and result.outcome == "promoted"


def _p_postcommit_complete(module, base_dir):
    """A journal fault after the COMPLETE frame is written is indeterminate by journal evidence, never aborted
    and never promoted (the raise cannot confirm the frame's durability)."""
    root, rid, _run = module._st_build(base_dir, "postcommit")
    result, fired, states = module._st_postcommit_fault(module.apply_ingest, root, rid, _journal.F_COMPLETE)
    return (fired == [_journal.F_COMPLETE] and states == {1: "complete"} and result.promoted is None
            and result.outcome == "indeterminate" and "durability is unconfirmed" in " ".join(result.findings))


def _p_reread_indeterminate(module, base_dir):
    """A failing attempt-state re-read after a committed publication is indeterminate, never aborted."""
    root, rid, _run = module._st_build(base_dir, "reread")
    wrapped, raised = module._st_reread_fault(module.apply_ingest, _journal.JournalError)
    result, fired, states = module._st_postcommit_fault(wrapped, root, rid, _journal.F_COMPLETE)
    return (fired == [_journal.F_COMPLETE] and len(raised) == 1 and states == {1: "complete"}
            and result.promoted is None and result.outcome == "indeterminate"
            and "journal state unreadable" in " ".join(result.findings))


def _p_complete_lost_indeterminate(module, base_dir):
    """An open attempt whose COMPLETE frame never reached the log is indeterminate, never aborted."""
    root, rid, _run = module._st_build(base_dir, "complete-lost")
    result, fired, states, _recovered = module._st_complete_lost(module.apply_ingest, root, rid)
    return (fired == [_journal.F_COMPLETE] and states == {1: "open"} and result.promoted is None
            and result.outcome == "indeterminate" and "journal state open" in " ".join(result.findings))


def _p_foreign_txn_indeterminate(module, base_dir):
    """A foreign exception past the journal's rollback leaves an open attempt: indeterminate, never an
    exception escaping apply_ingest with no result formed."""
    root, rid, _run = module._st_build(base_dir, "foreign-txn")
    result, states = module._st_foreign_txn_fault(module.apply_ingest, root, rid)
    return (states == {1: "open"} and getattr(result, "promoted", False) is None
            and getattr(result, "outcome", None) == "indeterminate"
            and "journal state open" in " ".join(getattr(result, "findings", [])))


def _p_precommit_rollback_aborted(module, base_dir):
    """A genuine pre-commit rollback is still aborted: the guard neither widens a rollback to indeterminate
    nor launders it into a promotion."""
    from unittest.mock import patch
    root, rid, _run = module._st_build(base_dir, "rollback")
    with _opf_import._self_test_homes2_active(root):
        with patch.object(_journal, "_poststate_verifies", lambda _root_fd, _op: False):
            result = module.apply_ingest(root, rid, now=module._NOW)
        states = module._st_attempt_states(root, rid)
    return states == {1: "rolled-back"} and result.promoted is False and result.outcome == "aborted"


def _p_returned_is_committed(module, base_dir):
    """A transaction that returned is committed when a later step fails, even while the state is unreadable."""
    root, rid, run = module._st_build(base_dir, "returned")
    wrapped, raised = module._st_reread_fault(module.apply_ingest, _journal.JournalError)
    result, fired = module._st_postverify_fault(wrapped, root, rid, run, "source", _journal.JournalError)
    return len(fired) == 1 and not raised and result.promoted is True and result.outcome == "promoted"


def _p_unprintable_committed(module, base_dir):
    """An exception whose __str__ raises, in post-commit verification, is still a committed promotion."""
    root, rid, run = module._st_build(base_dir, "unprintable")
    result, fired = module._st_postverify_fault(module.apply_ingest, root, rid, run, "source",
                                                module._Unprintable)
    return len(fired) == 1 and result.promoted is True and result.outcome == "promoted"


def _p_helper_fault_indeterminate(module, base_dir):
    """An outcome helper that itself raises is indeterminate, never the generic abort."""
    root, rid, run = module._st_build(base_dir, "helper-fault")
    result, fired = module._st_helper_fault(module.apply_ingest, root, rid, run)
    return (len(fired) == 1 and result.promoted is None and result.outcome == "indeterminate"
            and "outcome determination itself failed" in " ".join(result.findings))


def _p_cleanup_preserved(step, broken_stderr=False):
    """A probe that a RecursionError in the `step` cleanup (with stderr broken too, when asked) leaves the
    formed promoted result standing, surfaced by the cleanup guard itself (with stderr intact). The guarded
    cleanup call, and for the root close the launch boundary, keep the result when the cleanup guard is
    reverted, so only that diagnostic tells them apart: a guard-execution probe (see _p_cleanup_outcome)."""
    def probe(module, base_dir):
        root, rid, _run = module._st_build(base_dir, "cleanup")
        result, fired, surfaced = module._st_cleanup_fault(module.apply_ingest, root, rid, step, RecursionError,
                                                           broken_stderr)
        return (fired == [step] and getattr(result, "promoted", None) is True
                and getattr(result, "outcome", None) == "promoted" and (broken_stderr or "cleanup (" in surfaced))
    return probe


def _p_surface_non_throwing(module, base_dir):
    """_surface itself swallows a broken stderr (probed directly, so the guarded diagnostic call in _cleanup
    cannot mask it), and a cleanup diagnostic over a broken stderr leaves the formed promoted result."""
    from unittest.mock import patch
    with patch.object(sys, "stderr", module._BrokenStream()):
        try:
            module._surface("probe: a diagnostic over a broken stderr")
        except Exception:  # noqa: BLE001  an escaped diagnostic failure is the defect under test
            return False
    return _p_cleanup_preserved("root-close", broken_stderr=True)(module, base_dir)


def _p_surface_outcome(module, base_dir):
    """A broken stderr during real promotion cleanup preserves the promoted result and releases the
    journal writer lock. Assert only the outcome and lock state, never diagnostic text or probe firing."""
    root, rid, _run = module._st_build(base_dir, "surface-outcome")
    result, _fired, _surfaced = module._st_cleanup_fault(
        module.apply_ingest, root, rid, "root-close", RecursionError, broken_stderr=True)
    try:
        (root / _opf_store.journal_root(KIND) / "lock").lstat()
    except FileNotFoundError:
        lock_released = True
    else:
        lock_released = False
    return (getattr(result, "promoted", None) is True
            and getattr(result, "outcome", None) == "promoted" and lock_released)


def _p_nested_not_aborted(module, base_dir):
    """A committed publication whose outcome helper, _RetainLock construction, and cleanup diagnostic call
    all fail is indeterminate with the lock retained, never aborted and never an escape."""
    root, rid, run = module._st_build(base_dir, "nested")
    result, fired, states = module._st_nested_fault(module.apply_ingest, root, rid, run)
    return (fired == ["helper", "retain", "op-release", "surface"] and states == {1: "complete"}
            and getattr(result, "promoted", False) is None and getattr(result, "outcome", None) == "indeterminate"
            and (root / _opf_store.journal_root(KIND) / "lock").is_file())


def _p_interrupt_retains(interrupt, escapes):
    """A probe that `interrupt` after an unsynced COMPLETE propagates as `escapes` with the lock retained, so
    an immediate re-apply refuses rather than reporting a completed no-op."""
    def probe(module, base_dir):
        root, rid, _run = module._st_build(base_dir, "interrupt")
        escaped, fired, states, held, again = module._st_interrupt_fault(module.apply_ingest, root, rid, interrupt)
        return (escaped is escapes and fired == [interrupt.__name__] and states == {1: "complete"} and held
                and again.promoted is False and again.outcome == "aborted")
    return probe


def _p_complete_unsynced(module, base_dir):
    """A COMPLETE frame whose own log fsync failed is readable but unconfirmed: indeterminate, never promoted."""
    root, rid, _run = module._st_build(base_dir, "unsynced")
    result, fired, states = module._st_postcommit_fault(module.apply_ingest, root, rid, _journal.F_COMPLETE,
                                                        target="log")
    return (fired == [_journal.F_COMPLETE] and states == {1: "complete"} and result.promoted is None
            and result.outcome == "indeterminate")


def _p_complete_unsynced_reapply(module, base_dir):
    """A re-apply after an unconfirmed COMPLETE refuses on the retained lock, never a completed no-op."""
    root, rid, _run = module._st_build(base_dir, "unsynced-reapply")
    _result, fired, _states = module._st_postcommit_fault(module.apply_ingest, root, rid, _journal.F_COMPLETE,
                                                          target="log")
    with _opf_import._self_test_homes2_active(root):
        again = module.apply_ingest(root, rid, now=module._NOW)
    return fired == [_journal.F_COMPLETE] and again.promoted is False and again.outcome == "aborted"


def _caught(apply):
    """`apply` with an escaping Exception returned in place of the result: an escape is itself a misreport."""
    def wrapped(*args, **kwargs):
        try:
            return apply(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001  an escaped exception is the misreport under test
            return exc
    return wrapped


# Scenarios for the outcome-only probes: each drives one launched attempt and returns (result, root).

def _s_complete_fault(module, base_dir):
    root, rid, _run = module._st_build(base_dir, "postcommit")
    return module._st_postcommit_fault(_caught(module.apply_ingest), root, rid, _journal.F_COMPLETE)[0], root


def _s_reread_fault(module, base_dir):
    root, rid, _run = module._st_build(base_dir, "reread")
    wrapped, _raised = module._st_reread_fault(_caught(module.apply_ingest), _journal.JournalError)
    return module._st_postcommit_fault(wrapped, root, rid, _journal.F_COMPLETE)[0], root


def _s_complete_lost(module, base_dir):
    root, rid, _run = module._st_build(base_dir, "complete-lost")
    return module._st_complete_lost(_caught(module.apply_ingest), root, rid)[0], root


def _s_foreign_txn(module, base_dir):
    root, rid, _run = module._st_build(base_dir, "foreign-txn")
    return module._st_foreign_txn_fault(module.apply_ingest, root, rid)[0], root


def _s_helper_fault(module, base_dir):
    root, rid, run = module._st_build(base_dir, "helper-fault")
    return module._st_helper_fault(_caught(module.apply_ingest), root, rid, run)[0], root


def _s_nested(module, base_dir):
    root, rid, run = module._st_build(base_dir, "nested")
    return module._st_nested_fault(module.apply_ingest, root, rid, run)[0], root


def _p_retained(scenario):
    """A safety probe at the outcome boundary ALONE: the launched attempt `scenario` drives is indeterminate
    (promoted None) with the journal lock retained. No diagnostic text or probe firing is asserted, so it goes
    RED only on a false abort, a false promotion, an escape with no result formed, or a released lock."""
    def probe(module, base_dir):
        result, root = scenario(module, base_dir)
        return (getattr(result, "promoted", False) is None and getattr(result, "outcome", None) == "indeterminate"
                and (root / _opf_store.journal_root(KIND) / "lock").is_file())
    return probe


def _p_cleanup_outcome(step):
    """A safety probe that a RecursionError in the `step` cleanup leaves the formed promoted result standing,
    asserting the outcome alone (no diagnostic text), so it goes RED only on an abort or an escape."""
    def probe(module, base_dir):
        root, rid, _run = module._st_build(base_dir, "cleanup")
        result, fired, _surfaced = module._st_cleanup_fault(module.apply_ingest, root, rid, step, RecursionError)
        return (fired == [step] and getattr(result, "promoted", None) is True
                and getattr(result, "outcome", None) == "promoted")
    return probe


def _p_cleanup_call_guarded(label, surface_fails=False):
    """A safety probe that a failure AT the `label` final-cleanup call, before _cleanup is entered (with the
    diagnostic call at that boundary failing too, when asked), leaves the selected outcome standing (for the
    root close, a completed run's verified no-op, which no launch boundary backs) and that every later cleanup
    is still entered."""
    def probe(module, base_dir):
        _label, what, noop, later = dict((c[0], c) for c in module._CLEANUP_CALLS)[label]
        root, rid, _run = module._st_build(base_dir, "cleanup-call")
        result, fired, entered, _surfaced = module._st_cleanup_call_fault(module.apply_ingest, root, rid, what,
                                                                          noop, surface_fails)
        return (fired == [what] and entered == later and getattr(result, "verdict", None) == CLEAN
                and getattr(result, "promoted", None) is True
                and getattr(result, "outcome", None) == ("noop_already_complete" if noop else "promoted"))
    return probe


def _probe_row(identity, probe, old, new, strips=()):
    """A discriminator row over this file whose focused test asserts `probe` under `identity`, over the
    isolation baseline `strips` (names in _STRIPS) when given."""
    def test(module, base_dir):
        _revert_check(probe(module, base_dir), identity)
    return (identity, "apply", test, old, new, tuple(strips))


# The ONE post-launch guard in `_launched_attempt`, and its reversal (the guard catches nothing).
_POST_LAUNCH_GUARD = "except Exception as exc:  # noqa: BLE001  the post-launch guard"
_POST_LAUNCH_REVERTED = "except () as exc:  # reverted: the post-launch guard"
# A readable COMPLETE is not a durable COMPLETE, and its reversal (the pre-fix trust in a readable frame).
_UNCONFIRMED_GUARD = 'state = "complete but its durability is unconfirmed"'
_UNCONFIRMED_REVERTED = ('return _committed_result(ref, "the journal post-commit step did not fully complete ("'
                         ' + detail + ")")')
# The ONE final-cleanup guard in `_cleanup`, and its reversal to the pre-fix OSError-only posture.
_CLEANUP_GUARD = "except Exception as exc:  # noqa: BLE001  the cleanup guard"
_CLEANUP_REVERTED = "except OSError as exc:  # reverted: the cleanup guard"
# The launch boundary in apply_ingest, and its reversal (a launched escape falls to the pre-launch abort).
_LAUNCH_BOUNDARY = "        if launch.launched:\n"
_LAUNCH_REVERTED = "        if False:  # reverted: the launch boundary\n"
# The interrupt's lock retention, and its reversal (the pre-fix release as the fall-through of any escape).
_INTERRUPT_RETAIN = "release = not launch.launched or launch.result is not None"
_INTERRUPT_REVERTED = "release = True  # reverted: the interrupt retention"
# The guarded attempt-state re-read, the outcome helper's own-failure guard, and the guarded cleanup
# diagnostic call, each with its reversal (the guard catches nothing).
_READ_GUARD, _READ_REVERTED = "except Exception as read_exc:", "except () as read_exc:"
_HELPER_GUARD = "except Exception:  # noqa: BLE001  the outcome helper's own failure"
_HELPER_REVERTED = "except ():  # reverted: the outcome helper's own failure"
_DIAG_CALL_GUARD = "except Exception:  # noqa: BLE001  the cleanup diagnostic call"
_DIAG_CALL_REVERTED = "except ():  # reverted: the cleanup diagnostic call"
# The three final-cleanup CALL guards (a failure raised before _cleanup is entered), label -> guard text.
_CALL_GUARDS = dict((label, "except Exception:  # noqa: BLE001  the {} cleanup call".format(site))
                    for label, site in (("root-close", "root-close"), ("lock-release", "writer-release"),
                                        ("op-release", "op-release")))


# The guard on each call's own diagnostic, label -> guard text.
_CALL_DIAG_GUARDS = dict((label, "except Exception:  # noqa: BLE001  the {} call's diagnostic".format(site))
                         for label, site in (("root-close", "root-close"), ("lock-release", "writer-release"),
                                             ("op-release", "op-release")))


def _call_reverted(label, verb="reverted", guards=_CALL_GUARDS):
    return guards[label].replace("except Exception:  # noqa: BLE001 ", "except ():  # " + verb + ":")


# Isolation baselines: an overlapping layer beneath a guard, stripped in the candidate only, name -> (old,
# new). A stripped layer is never a production change; the restored phase always runs the full source.
_STRIPS = dict((("helper-guard", (_HELPER_GUARD, _HELPER_REVERTED.replace("reverted", "stripped"))),
                ("launch-boundary", (_LAUNCH_BOUNDARY, _LAUNCH_REVERTED.replace("reverted", "stripped"))),
                ("cleanup-diagnostic-call",
                 (_DIAG_CALL_GUARD, _DIAG_CALL_REVERTED.replace("reverted", "stripped"))))
               + tuple((label + "-call", (_CALL_GUARDS[label], _call_reverted(label, "stripped")))
                       for label in _CALL_GUARDS))
_LAUNCHED_LAYERS = ("helper-guard", "launch-boundary")
# Rows whose mutant changes no declared asserted Boolean outcome claim only guard execution, even if
# other checks flip. Each has an "/isolated" safety row; safety requires a flip inside _REVERT_ASSERTED.
_GUARD_EXECUTION = frozenset((
    "postlaunch/complete-fault-not-aborted", "postlaunch/reread-fault-not-aborted",
    "postlaunch/complete-lost-not-aborted", "postlaunch/foreign-txn-not-escaped", "postlaunch/reread-guarded",
    "postlaunch/helper-failure-not-aborted", "cleanup/root-close-not-aborted", "cleanup/lock-release-not-escaped",
    "cleanup/op-release-not-escaped", "cleanup/diagnostic-call-not-escaped",
    "cleanup/diagnostic-not-escaped",
    "gate/home-rename-at-lookup/import", "gate/home-rename-at-lookup/ingest",
    "gate/home-property-holding/import", "gate/home-property-holding/ingest",
    "gate/home-second-claim/import", "gate/home-second-claim/ingest"))

# (identity, probe, unique old, new). The class width first: every sibling re-exposed by reverting the ONE
# post-launch guard. Then each decision branch of `_post_launch_result` by its own mutation, including the
# rollback branch, so the guard can neither absorb a genuine abort nor claim a commit it cannot confirm, and
# the unconfirmed-COMPLETE branch with one probe per sibling (the result, and a re-apply's no-op). Then the
# exception-total layers: _safe_text, the helper-failure guard, the cleanup guard with one probe per cleanup
# step, and the cleanup diagnostic channel. Then the launch boundary: a nested failure that escapes even the
# helper-failure guard (the launch-boundary reversal, and the guarded cleanup diagnostic call sharing its
# probe), and the interrupt retention with one probe per BaseException sibling. Then the three final-cleanup
# CALL guards and the guards on their own diagnostics, one probe per call. Last the "/isolated" safety rows,
# one per guard-execution row, each over the isolation baseline naming the overlapping layers stripped (a
# trailing tuple of _STRIPS names).
_POST_LAUNCH = (
    ("postverify/committed-not-aborted", _p_postverify_committed, _POST_LAUNCH_GUARD, _POST_LAUNCH_REVERTED),
    ("postverify/foreign-class-not-aborted", _p_postverify_foreign, _POST_LAUNCH_GUARD, _POST_LAUNCH_REVERTED),
    ("postlaunch/complete-fault-not-aborted", _p_postcommit_complete, _POST_LAUNCH_GUARD, _POST_LAUNCH_REVERTED),
    ("postlaunch/reread-fault-not-aborted", _p_reread_indeterminate, _POST_LAUNCH_GUARD, _POST_LAUNCH_REVERTED),
    ("postlaunch/complete-lost-not-aborted", _p_complete_lost_indeterminate, _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED),
    ("postlaunch/foreign-txn-not-escaped", _p_foreign_txn_indeterminate, _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED),
    ("postlaunch/reread-guarded", _p_reread_indeterminate, _READ_GUARD, _READ_REVERTED),
    ("postlaunch/rollback-still-aborted", _p_precommit_rollback_aborted,
     'if state in ("nothing-opened", "rolled-back"):', "if False:"),
    ("postlaunch/indeterminate-not-aborted", _p_complete_lost_indeterminate,
     'promoted=None, outcome="indeterminate"', 'promoted=False, outcome="aborted"'),
    ("postlaunch/returned-is-committed", _p_returned_is_committed, "returned = True", "returned = False"),
    ("postcommit/complete-unconfirmed-not-promoted", _p_complete_unsynced, _UNCONFIRMED_GUARD,
     _UNCONFIRMED_REVERTED),
    ("postcommit/complete-unconfirmed-reapply-refused", _p_complete_unsynced_reapply, _UNCONFIRMED_GUARD,
     _UNCONFIRMED_REVERTED),
    ("postlaunch/unprintable-not-aborted", _p_unprintable_committed, "def _safe_text(value):\n",
     "def _safe_text(value):\n    return str(value)\n"),
    ("postlaunch/helper-failure-not-aborted", _p_helper_fault_indeterminate, _HELPER_GUARD, _HELPER_REVERTED),
    ("cleanup/root-close-not-aborted", _p_cleanup_preserved("root-close"), _CLEANUP_GUARD, _CLEANUP_REVERTED),
    ("cleanup/lock-release-not-escaped", _p_cleanup_preserved("lock-release"), _CLEANUP_GUARD, _CLEANUP_REVERTED),
    ("cleanup/op-release-not-escaped", _p_cleanup_preserved("op-release"), _CLEANUP_GUARD, _CLEANUP_REVERTED),
    ("cleanup/diagnostic-not-escaped", _p_surface_non_throwing,
     "except Exception:  # noqa: BLE001  the diagnostic channel",
     "except ():  # reverted: the diagnostic channel"),
    ("postlaunch/nested-failure-not-aborted", _p_nested_not_aborted, _LAUNCH_BOUNDARY, _LAUNCH_REVERTED),
    ("cleanup/diagnostic-call-not-escaped", _p_nested_not_aborted, _DIAG_CALL_GUARD, _DIAG_CALL_REVERTED),
    ("postlaunch/interrupt-retains-lock", _p_interrupt_retains(KeyboardInterrupt, KeyboardInterrupt),
     _INTERRUPT_RETAIN, _INTERRUPT_REVERTED),
    ("postlaunch/exit-retains-lock", _p_interrupt_retains(SystemExit, SystemExit), _INTERRUPT_RETAIN,
     _INTERRUPT_REVERTED),
    ("postlaunch/interrupting-format-retains-lock", _p_interrupt_retains(_InterruptingStr, KeyboardInterrupt),
     _INTERRUPT_RETAIN, _INTERRUPT_REVERTED),
    ("cleanup/root-close-call-not-aborted", _p_cleanup_call_guarded("root-close"), _CALL_GUARDS["root-close"],
     _call_reverted("root-close")),
    ("cleanup/writer-release-call-not-escaped", _p_cleanup_call_guarded("lock-release"),
     _CALL_GUARDS["lock-release"], _call_reverted("lock-release")),
    ("cleanup/op-release-call-not-escaped", _p_cleanup_call_guarded("op-release"), _CALL_GUARDS["op-release"],
     _call_reverted("op-release")),
    ("cleanup/root-close-call-diagnostic-not-aborted", _p_cleanup_call_guarded("root-close", True),
     _CALL_DIAG_GUARDS["root-close"], _call_reverted("root-close", guards=_CALL_DIAG_GUARDS)),
    ("cleanup/writer-release-call-diagnostic-not-escaped", _p_cleanup_call_guarded("lock-release", True),
     _CALL_DIAG_GUARDS["lock-release"], _call_reverted("lock-release", guards=_CALL_DIAG_GUARDS)),
    ("cleanup/op-release-call-diagnostic-not-escaped", _p_cleanup_call_guarded("op-release", True),
     _CALL_DIAG_GUARDS["op-release"], _call_reverted("op-release", guards=_CALL_DIAG_GUARDS)),
    ("postlaunch/complete-fault-not-aborted/isolated", _p_retained(_s_complete_fault), _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED, _LAUNCHED_LAYERS),
    ("postlaunch/reread-fault-not-aborted/isolated", _p_retained(_s_reread_fault), _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED, _LAUNCHED_LAYERS),
    ("postlaunch/complete-lost-not-aborted/isolated", _p_retained(_s_complete_lost), _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED, _LAUNCHED_LAYERS),
    ("postlaunch/foreign-txn-not-escaped/isolated", _p_retained(_s_foreign_txn), _POST_LAUNCH_GUARD,
     _POST_LAUNCH_REVERTED, _LAUNCHED_LAYERS),
    ("postlaunch/reread-guarded/isolated", _p_retained(_s_reread_fault), _READ_GUARD, _READ_REVERTED,
     _LAUNCHED_LAYERS),
    ("postlaunch/helper-failure-not-aborted/isolated", _p_retained(_s_helper_fault), _HELPER_GUARD,
     _HELPER_REVERTED, ("launch-boundary",)),
    ("cleanup/root-close-not-aborted/isolated", _p_cleanup_outcome("root-close"), _CLEANUP_GUARD,
     _CLEANUP_REVERTED, ("launch-boundary", "root-close-call")),
    ("cleanup/lock-release-not-escaped/isolated", _p_cleanup_outcome("lock-release"), _CLEANUP_GUARD,
     _CLEANUP_REVERTED, ("lock-release-call",)),
    ("cleanup/op-release-not-escaped/isolated", _p_cleanup_outcome("op-release"), _CLEANUP_GUARD,
     _CLEANUP_REVERTED, ("op-release-call",)),
    ("cleanup/diagnostic-call-not-escaped/isolated", _p_retained(_s_nested), _DIAG_CALL_GUARD,
     _DIAG_CALL_REVERTED, ("op-release-call",)),
    ("cleanup/diagnostic-not-escaped/isolated", _p_surface_outcome,
     "except Exception:  # noqa: BLE001  the diagnostic channel",
     "except ():  # reverted: the diagnostic channel",
     ("cleanup-diagnostic-call", "root-close-call", "launch-boundary")),
)

# (identity, source-key, focused test, unique old, new). source-key selects which module's source is
# mutated: "apply" is this file, "alloc" is _opf_allocation.py (a dependency guard, mutated at source the
# same way the observer gate mutates its shared _opf_observe.py); "gate" is check_opf_import.py.
_DISCRIMINATORS = tuple(
    ("gate/staged-run-store-depth/" + kind, "gate", _d_staged_run_store_depth(kind),
     "store_fd = _staged_run_store_fd(rd, gen)",
     'store_fd = os.open("../../..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rd.fd)')
    for kind in ("import", "ingest")) + tuple(
    ("gate/staging-generation-mismatch/" + kind, "gate", _d_staging_generation_mismatch(kind),
     "if other is not None:", "if False:")
    for kind in ("import", "ingest")) + tuple(
    ("gate/staging-alias/" + kind, "gate", _d_staging_alias(kind),
     "            fd = _physical_home(rd, rel)\n",
     '            fd = _physical_home(rd, rel) if rd.path.parts[-len(rel.split("/")):] == tuple(rel.split("/")) '
     "else None\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/run-name-bound/" + kind, "gate", _d_run_name_bound(kind),
     "            _bind_run_name(self.fd, self.name)\n", "            pass  # reverted: _bind_run_name\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-symlink-route/" + kind, "gate", _d_home_symlink_route(kind),
     '        traversed, expanded = _spelled_route(rd, visit, max(len(rel.split("/")) for rel in rels) - 1)\n',
     "        traversed, expanded = set(), 0\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-speculative/" + kind, "gate", _d_home_speculative(kind),
     "                except (OSError, ValueError):\n                    continue\n",
     "                except FileNotFoundError:\n                    continue\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-rename-at-lookup/" + kind, "gate", _d_home_rename_at_lookup(kind),
     '                if depth == 0:\n                    raise _GateError("the bound run name',
     '                if False:\n                    raise _GateError("the bound run name')
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-rename-at-lookup/" + kind + "/isolated", "gate", _d_home_rename_at_lookup_unit(kind),
     '                if depth == 0:\n                    raise _GateError("the bound run name',
     '                if False:\n                    raise _GateError("the bound run name')
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-retained/" + kind, "gate", _d_home_retained(kind),
     "        bound = _retained_homes(rd, fresh)\n", "        bound = fresh\n")
    for kind in ("import", "ingest")) + (
    ("gate/transaction-generation-required", "gate", _d_transaction_generation_required,
     "if gen is None:\n        for cid in _TRANSACTION_CHECKS:\n            record(cid, False, gen_error)",
     "if False:\n        for cid in _TRANSACTION_CHECKS:\n            record(cid, False, gen_error)"),
    ("acceptance/reject-refused", "apply", _d_reject_refused, "if rejected:", "if False:"),
    ("path/canonical-contained", "apply", _d_canonical_contained,
     "return _opf_store._home_file(path)", "return path"),
    ("allocation/journal-lock-required", "alloc", _d_journal_lock_required,
     "if not _opf_journal.writer_lock_held(cap, KIND):", "if False:"),
    ("per-record/inline-required", "apply", _d_inline_required,
     "_opf_import._require_inline_layout(root_fd, machine_rel)",
     "pass  # reverted: _opf_import._require_inline_layout(root_fd, machine_rel)"),
) + tuple(_probe_row(*row) for row in _POST_LAUNCH)


# Round-4 cases remain; R1 now guards unheld relative paths independently of ancestor visits.
_DISCRIMINATORS += tuple(
    ("gate/home-relative/{}/{}/homes-{}".format(case, kind, homes), "gate",
     _d_home_relative(kind, homes, case),
     '        if not held and (expanded or not rd.spelling.startswith("/")):\n', "        if False:  # reverted R1\n")
    for kind in ("import", "ingest") for homes in (1, 2)
    for case in ("staging", "staging-entry", "legacy", "legacy-entry")) + tuple(
    ("gate/home-search-only/{}/{}/homes-{}".format(case, kind, homes), "gate",
     _d_home_search_only(kind, homes, case),
     'getattr(os, "O_PATH", os.O_RDONLY)', "os.O_RDONLY")
    for kind in ("import", "ingest") for homes in (1, 2)
    for case in ("detached", "canonical", "physical")) + tuple(
    ("gate/home-external-symlink/{}/homes-{}".format(kind, homes), "gate",
     _d_home_external_symlink(kind, homes),
     "        if held and reached:\n", "        if False:  # reverted R2\n")
    for kind in ("import", "ingest") for homes in (1, 2)) + tuple(
    ("gate/home-cwd-bound/{}/homes-{}".format(kind, homes), "gate",
     _d_home_cwd_bound(kind, homes), "else os.dup(rd.cwd_fd)", 'else os.open(".", flags)')
    for kind in ("import", "ingest") for homes in (1, 2)) + tuple(
    ("gate/home-restore/{}/homes-{}".format(kind, homes), "gate",
     _d_home_restore(kind, homes), "        bound = _retained_homes(rd, fresh)\n", "        bound = fresh\n")
    for kind in ("import", "ingest") for homes in (1, 2))


# Reintroduce the exact round-5 unordered threading condition. Under literal R2 its defect is that it
# can suppress a real second claim; F2 already has a second claim and refuses under either policy.
_ST_SEEN_SET_REVERTED = '''        seen, old_reached = set(), []

        def old_visit(dfd, edges):
            seen.add(_fd_identity(dfd))
            if _fd_identity(dfd) in roots:
                return
            for rel in rels:
                route = []
                try:
                    parts = rel.split("/")
                    for index in range(1, len(parts) + 1):
                        st = os.stat("/".join(parts[:index]), dir_fd=dfd)
                        route.append((st.st_dev, st.st_ino))
                except (OSError, ValueError):
                    continue
                if route[-1] == run:
                    old_reached.append((edges, tuple(route[:-1])))

        _old_edges, old_total = _spelled_route(rd, old_visit, max(len(r.split("/")) for r in rels) - 1)
        if held and any(e < old_total and all(step in seen for step in route)
                        for e, route in old_reached):
'''

# Ancestor visits now discriminate held runs with second claims, not R1's unheld paths.
_DISCRIMINATORS += tuple(
    ("gate/home-property-ancestors/" + kind, "gate", _d_home_property_ancestors(kind),
     "                    visit_ancestors(cur, expanded)\n",
     "                    pass  # reverted: per-edge ancestor visits\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-property-holding/" + kind, "gate", _d_home_property_holding(kind),
     "        if held and reached:\n", _ST_SEEN_SET_REVERTED)
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-start-ancestors/" + kind, "gate", _d_home_start_ancestors(kind),
     "\n            visit_ancestors(cur, expanded)\n",
     "\n            pass  # reverted: starting-ancestor visits\n")
    for kind in ("import", "ingest")) + tuple(
    ("gate/home-" + rule + "/" + kind, "gate", _d_home_rule(kind, rule),
     '        if not held and (expanded or not rd.spelling.startswith("/")):\n' if rule == "unheld-link" else "        if held and reached:\n",
     "        if False:  # reverted fail-closed rule\n")
    for kind in ("import", "ingest") for rule in ("unheld-link", "second-claim")) + tuple(
    ("gate/home-unheld-relative/" + kind, "gate", _d_home_unheld_relative(kind),
     '        if not held and (expanded or not rd.spelling.startswith("/")):\n',
     "        if not held and expanded:\n")
    for kind in ("import", "ingest"))

_DISCRIMINATORS += tuple(
    ("gate/home-" + rule + "/" + kind + "/isolated", "gate", _d_home_second_claim_clean_store(kind, rule),
     "        if held and reached:\n",
     _ST_SEEN_SET_REVERTED if rule == "property-holding" else "        if False:  # reverted fail-closed rule\n")
    for kind in ("import", "ingest") for rule in ("property-holding", "second-claim"))


# Unit rows observe the named contract directly; transaction-generation-required observes the internal
# reader's registry; every other row observes the public outcome.
# The property-holding/second-claim /isolated rows deliberately use the public boundary: corrupt=False
# removes the overlapping record finding, so their declared transaction bits measure R2 refusal itself.
_REVERT_UNIT_BOUNDARIES = {
    "path/canonical-contained": ("_canonical", "_StageError"),
    "allocation/journal-lock-required": ("reserve_ingest_ids", "AllocationError"),
    **{"gate/home-rename-at-lookup/" + kind + "/isolated": ("_physical_home", "_GateError")
       for kind in ("import", "ingest")},
}


# Declared outcome sets, independent of observed flips: (call ordinal, check id, expected baseline bit).
# Each gate row names a protected call, never a clean setup/control call. None/wildcard ordinals are
# forbidden. One representative protected call suffices; this does not prove every fixture assertion
# discriminates. The fixture's oracle, representative selection and call alignment still need review.
_REVERT_ASSERTED = {
    "gate/transaction-generation-required": tuple((0, cid, False) for cid in _ST_TXN),
    "path/canonical-contained": ((0, "refused", True),),
    "allocation/journal-lock-required": ((0, "refused", True),),
}
for _kind in ("import", "ingest"):
    for _case, _call, _expected, _checks in (
        ("staged-run-store-depth", 0, False, ("transaction-schema",)),
        ("staging-generation-mismatch", 0, False, _ST_TXN),
        ("staging-alias", 0, False, _ST_TXN),  # legacy alias refusal
        ("run-name-bound", 0, False, ("staged-run-structure",)),
        ("home-symlink-route", 0, False, _ST_TXN),
        ("home-speculative", 3, True, _ST_TXN),  # obstructed homes-2, after clean calls
        ("home-rename-at-lookup", 0, False, _ST_TXN),
        ("home-retained", 0, False, _ST_TXN),
        ("home-property-ancestors", 1, False, _ST_TXN),  # claimed spelling after clean
        # _st_home_property checks these ordinals against shared-ancestor/homes-1/base_dir/alias-0.
        ("home-property-holding", 148, False, _ST_TXN),
        ("home-start-ancestors", 1, False, _ST_TXN),
        ("home-unheld-link", 0, False, _ST_TXN),  # chained canonical route
        ("home-second-claim", 4, False, _ST_TXN),  # shared-ancestor's first alias
        ("home-unheld-relative", 0, False, _ST_TXN),
    ):
        _REVERT_ASSERTED["gate/" + _case + "/" + _kind] = tuple(
            (_call, cid, _expected) for cid in _checks)
    # _RunDir setup makes physical-home calls 0..2; call 3 is the direct mismatch probe.
    _REVERT_ASSERTED["gate/home-rename-at-lookup/" + _kind + "/isolated"] = ((3, "refused", True),)
    for _rule in ("property-holding", "second-claim"):
        _REVERT_ASSERTED["gate/home-" + _rule + "/" + _kind + "/isolated"] = tuple(
            (1, cid, False) for cid in _ST_TXN)
    for _homes in (1, 2):
        for _case in ("staging", "staging-entry", "legacy", "legacy-entry"):
            _REVERT_ASSERTED["gate/home-relative/{}/{}/homes-{}".format(_case, _kind, _homes)] = tuple(
                (1, cid, False) for cid in _ST_TXN)  # relative refused spelling
        for _case in ("detached", "canonical", "physical"):
            _REVERT_ASSERTED["gate/home-search-only/{}/{}/homes-{}".format(_case, _kind, _homes)] = tuple(
                (1, cid, True) for cid in _ST_TXN)  # restricted call, not clean control
        for _case, _expected in (("external-symlink", False), ("cwd-bound", True), ("restore", False)):
            _REVERT_ASSERTED["gate/home-{}/{}/homes-{}".format(_case, _kind, _homes)] = tuple(
                (1, cid, _expected) for cid in _ST_TXN)
_REVERT_ASSERTED.update({
    "acceptance/reject-refused": ((0, "promoted=False", True), (0, "outcome=rejected", True)),
    "per-record/inline-required": ((0, "promoted=False", True), (0, "verdict=" + str(CANNOT_EVALUATE), True)),
})
# Each listed apply row asserts this outcome on this call; incidental flips on setup or earlier
# interrupted calls cannot stand in for the re-apply/no-op contract.
for _call, _outcome, _identities in (
    (0, "promoted", (
        "postverify/committed-not-aborted", "postverify/foreign-class-not-aborted",
        "postlaunch/returned-is-committed", "postlaunch/unprintable-not-aborted",
        "cleanup/root-close-not-aborted", "cleanup/lock-release-not-escaped",
        "cleanup/op-release-not-escaped", "cleanup/diagnostic-not-escaped",
        "cleanup/writer-release-call-not-escaped", "cleanup/op-release-call-not-escaped",
        "cleanup/writer-release-call-diagnostic-not-escaped", "cleanup/op-release-call-diagnostic-not-escaped")),
    (0, "indeterminate", (
        "postlaunch/complete-fault-not-aborted", "postlaunch/reread-fault-not-aborted",
        "postlaunch/complete-lost-not-aborted", "postlaunch/foreign-txn-not-escaped",
        "postlaunch/reread-guarded", "postlaunch/indeterminate-not-aborted",
        "postcommit/complete-unconfirmed-not-promoted", "postlaunch/helper-failure-not-aborted",
        "postlaunch/nested-failure-not-aborted", "cleanup/diagnostic-call-not-escaped")),
    (0, "aborted", ("postlaunch/rollback-still-aborted",)),
    (1, "aborted", (
        "postcommit/complete-unconfirmed-reapply-refused", "postlaunch/interrupt-retains-lock",
        "postlaunch/exit-retains-lock", "postlaunch/interrupting-format-retains-lock")),
    (1, "noop_already_complete", (
        "cleanup/root-close-call-not-aborted", "cleanup/root-close-call-diagnostic-not-aborted")),
):
    for _identity in _identities:
        _REVERT_ASSERTED[_identity] = ((_call, "returned", True), (_call, "outcome=" + _outcome, True))
        if _identity in _GUARD_EXECUTION:
            _REVERT_ASSERTED[_identity + "/isolated"] = _REVERT_ASSERTED[_identity]


def _revert_trace(module, key, identity, trace):
    """Record actual Boolean outcomes, never the test's diagnostic predicate.
    Gate checks retain only their pass/fail bit. Apply outcomes project returned/promoted/verdict/outcome
    into Boolean checks; unit contracts record refusal. Messages, findings and probe firing are excluded.
    Calls are paired by invocation ordinal within the same deterministic fixture, then by check id.
    This proves a flip at these boundaries, not the completeness or correctness of the fixture's oracle.
    An unobserved boundary cannot supply safety evidence.
    """
    from unittest.mock import patch
    unit = _REVERT_UNIT_BOUNDARIES.get(identity)
    method = unit[0] if unit else {"gate": "check_staged_run", "apply": "apply_ingest"}[key]
    # Observe the inner guard directly so the public refusal cannot mask its reversal.
    if identity == "gate/transaction-generation-required":
        method = "_check_staged_run"
    actual = getattr(module, method)

    def observed(*args, **kwargs):
        bits = {}
        trace.append(bits)
        if unit:
            try:
                result = actual(*args, **kwargs)
            except getattr(module, unit[1]):
                bits["refused"] = True
                raise
            bits["refused"] = False
            return result
        if key == "gate":
            result = actual(*args, **kwargs)
            if (not isinstance(result, dict) or set(result) != set(module.EXPECTED_CHECKS)
                    or any(not isinstance(v, tuple) or len(v) != 2 or type(v[0]) is not bool
                           for v in result.values())):
                raise RuntimeError("unreadable Boolean check outcomes: " + identity)
            bits.update((cid, value[0]) for cid, value in result.items())
            return result
        try:
            result = actual(*args, **kwargs)
        except BaseException:
            bits["returned"] = False
            raise
        bits["returned"] = True
        bits.update(("promoted=" + str(value), result.promoted is value) for value in (True, False, None))
        bits.update(("verdict=" + str(value), result.verdict == value)
                    for value in (CLEAN, FINDING, CANNOT_EVALUATE))
        bits.update(("outcome=" + value, result.outcome == value)
                    for value in ("promoted", "aborted", "rejected", "indeterminate", "noop_already_complete"))
        return result

    return patch.object(module, method, observed)


def _revert_boolean_witness(identity, baseline, mutant, safety, asserted):
    """Safety requires the declared call/direction; guard-execution forbids declared ids at any call.
    Missing calls/checks are not flips; fixture call alignment still needs review.
    """
    if (not isinstance(asserted, tuple) or not asserted
            or any(not isinstance(item, tuple) or len(item) != 3
                   or type(item[0]) is not int or item[0] < 0
                   or not isinstance(item[1], str) or not item[1]
                   or type(item[2]) is not bool for item in asserted)
            or len({item[:2] for item in asserted}) != len(asserted)):
        raise RuntimeError("malformed asserted outcome set: " + identity)
    for trace in (baseline, mutant):
        if any(type(bit) is not bool for call in trace for bit in call.values()):
            raise RuntimeError("non-Boolean outcome evidence: " + identity)
    if any(not any(ordinal == index and call.get(cid) is expected
                   for index, call in enumerate(baseline)) for ordinal, cid, expected in asserted):
        raise RuntimeError("unobserved asserted baseline outcome: " + identity)
    changed = [(index, cid, before[cid], after[cid])
               for index, (before, after) in enumerate(zip(baseline, mutant))
               for cid in sorted(before.keys() & after.keys()) if before[cid] is not after[cid]]
    flips = ["{}:{}:{}->{}".format(*item) for item in changed]
    witnesses = [flip for flip, (index, cid, before, _after) in zip(flips, changed)
                 if (index, cid, before) in asserted]
    if safety and not witnesses:
        raise RuntimeError("safety row has no asserted Boolean outcome flip: {}; all_flips={}".format(
            identity, ",".join(flips) or "none"))
    declared_ids = {cid for _ordinal, cid, _expected in asserted}
    if not safety and any(cid in declared_ids for _index, cid, _before, _after in changed):
        raise RuntimeError("guard-execution row has an asserted Boolean outcome flip; reclassify as safety: "
                           + identity)
    return flips, witnesses


def _t_revert_boolean_guard(base, check):
    """Negative controls use the same observer and refusing gate as the real mutation harness."""
    from types import SimpleNamespace
    from unittest.mock import patch
    import check_opf_import as gate
    traces = []
    asserted = ((0, "check", False),)
    for ok, unrelated, detail in ((False, False, "guard diagnostic"),
                                  (False, False, "different diagnostic"),
                                  (True, False, "different diagnostic"),
                                  (False, True, "different diagnostic")):
        module = SimpleNamespace(EXPECTED_CHECKS=("check", "unrelated"),
                                 check_staged_run=lambda: {"check": (ok, detail),
                                                          "unrelated": (unrelated, "other check")})
        trace = []
        with _revert_trace(module, "gate", "control", trace):
            module.check_staged_run()
        traces.append(trace)
    check("Boolean-flip-control", bool(_revert_boolean_witness(
        "control", traces[0], traces[2], True, asserted)[1]))
    for label, baseline, mutant, safety, declaration in (
        ("diagnostic-only", traces[0], traces[1], True, asserted),
        ("incidental-only", traces[0], traces[3], True, asserted),
        ("extra-call", traces[0], traces[0] + traces[2], True, asserted),
        ("wrong-call", traces[0] + traces[0], traces[0] + traces[2], True, asserted),
        ("clean-control-only", traces[2] + traces[0], traces[0] + traces[0],
         True, ((1, "check", False),)),
        ("wrong-direction", traces[2], traces[0], True, asserted),
        ("guard-execution-flip", traces[0], traces[2], False, asserted),
        ("guard-execution-other-call", traces[0] + traces[0], traces[0] + traces[2], False, asserted),
        ("guard-execution-other-direction", traces[0] + traces[2], traces[0] + traces[0], False, asserted),
        ("guard-execution-other-check-call", traces[0] + traces[0], traces[0] + traces[3],
         False, asserted + ((0, "unrelated", False),)),
        ("missing", traces[0], [], True, asserted),
        ("missing-check", traces[0], [{}], True, asserted),
        ("non-Boolean", traces[0], [{"check": 0}], True, asserted),
    ):
        refused = False
        try:
            _revert_boolean_witness("control", baseline, mutant, safety, declaration)
        except RuntimeError as exc:
            refused = True
            if label == "incidental-only":
                check("Boolean-incidental-flip-reported", "0:unrelated:False->True" in str(exc))
                print("BOOLEAN NEGATIVE CONTROL:", exc)
        check("Boolean-refuses-" + label, refused)
    check("Boolean-guard-execution", not _revert_boolean_witness(
        "control", traces[0], traces[1], False, asserted)[1])
    for declaration, reason in (
        ((), "malformed"),
        (((0, "absent", False),), "unobserved"),
        (((1, "check", False),), "unobserved"),
        (((None, "check", False),), "malformed"),
        (((True, "check", False),), "malformed"),
        (((-1, "check", False),), "malformed"),
        (((0, "check"),), "malformed"),
        (((0, "check", 0),), "malformed"),
        (((0, "check", False), (0, "check", True)), "malformed"),
    ):
        refused = False
        try:
            _revert_boolean_witness("control", traces[0], traces[2], True, declaration)
        except RuntimeError as exc:
            refused = str(exc).startswith(reason + " asserted")
        check("Boolean-refuses-declaration-" + repr(declaration), refused)
    for bad in ({}, {"check": (0, "diagnostic")}, {"check": False}):
        module = SimpleNamespace(EXPECTED_CHECKS=("check",), check_staged_run=lambda: bad)
        refused = False
        try:
            with _revert_trace(module, "gate", "control", []):
                module.check_staged_run()
        except RuntimeError:
            refused = True
        check("Boolean-refuses-malformed-" + repr(bad), refused)

    # Drift to another corrupt call with the same baseline bits; only matrix identity can refuse it.
    for kind in ("import", "ingest"):
        for rule in ("property-holding", "second-claim"):
            identity = "gate/home-" + rule + "/" + kind
            drifted = tuple((ordinal + 1, cid, expected)
                            for ordinal, cid, expected in _REVERT_ASSERTED[identity])
            test = _d_home_property_holding(kind) if rule == "property-holding" else _d_home_rule(kind, rule)
            refused = False
            with patch.dict(_REVERT_ASSERTED, {identity: drifted}):
                try:
                    test(gate, base / ("ordinal-drift-" + rule + "-" + kind))
                except RuntimeError as exc:
                    refused = str(exc) == "asserted home call identity drift: " + identity
            check("Boolean-refuses-ordinal-drift-" + rule + "-" + kind, refused)

    # Exercise apply's real projection, including an escape that supplies only the returned bit.
    apply_traces = []
    for outcome in ("rejected", "promoted", "escape"):
        def apply():
            if outcome == "escape":
                raise RuntimeError("control escape")
            return SimpleNamespace(promoted=outcome == "promoted",
                                   verdict=FINDING if outcome == "rejected" else CLEAN, outcome=outcome)

        module = SimpleNamespace(apply_ingest=apply)
        trace = []
        with _revert_trace(module, "apply", "control", trace):
            try:
                module.apply_ingest()
            except RuntimeError as exc:
                if outcome != "escape" or str(exc) != "control escape":
                    raise
        apply_traces.append(trace)
    check("Boolean-apply-projection", apply_traces[0] == [{
        "returned": True, "promoted=True": False, "promoted=False": True, "promoted=None": False,
        "verdict=" + str(CLEAN): False, "verdict=" + str(FINDING): True,
        "verdict=" + str(CANNOT_EVALUATE): False,
        "outcome=promoted": False, "outcome=aborted": False, "outcome=rejected": True,
        "outcome=indeterminate": False, "outcome=noop_already_complete": False,
    }])
    check("Boolean-apply-witness", _revert_boolean_witness(
        "control", apply_traces[0], apply_traces[1], True, ((0, "outcome=rejected", True),))[1]
        == ["0:outcome=rejected:True->False"])
    check("Boolean-apply-escape-projection", apply_traces[2] == [{"returned": False}])
    check("Boolean-apply-escape-witness", _revert_boolean_witness(
        "control", apply_traces[0], apply_traces[2], True, ((0, "returned", True),))[1]
        == ["0:returned:True->False"])


TESTS += (("revert-boolean-guard", _t_revert_boolean_guard),)


def _red_on_revert():
    """Run the discriminators in a private temporary tree. Return 0 when every guard reverts to RED and
    restores to PASS; refuse a safety row without an asserted Boolean flip, a guard-execution row with one,
    a survived reversal, a wrong assertion, or a non-unique mutation target."""
    import shutil
    import tempfile
    here = Path(__file__).resolve().parent
    sources = {"apply": here.joinpath("_opf_ingest_apply.py"), "alloc": here.joinpath("_opf_allocation.py"),
               "gate": here.joinpath("check_opf_import.py")}
    read = dict((key, path.read_text(encoding="utf-8")) for key, path in sources.items())
    ids = [d[0] for d in _DISCRIMINATORS]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate declared discriminator identity")
    if (not _GUARD_EXECUTION <= set(ids)
            or any(i + "/isolated" not in ids or i + "/isolated" in _GUARD_EXECUTION for i in _GUARD_EXECUTION)):
        raise RuntimeError("a guard-execution row is undeclared or has no /isolated safety row")
    rows = {row[0]: row for row in _DISCRIMINATORS}
    if set(_REVERT_ASSERTED) != set(ids):
        raise RuntimeError("asserted outcome declarations differ from discriminator registry")
    for identity in _GUARD_EXECUTION:
        parent, isolated = rows[identity], rows[identity + "/isolated"]
        if (parent[1], parent[3], parent[4]) != (isolated[1], isolated[3], isolated[4]):
            raise RuntimeError("isolated row has a different mutation target: " + identity)
    base = Path(tempfile.mkdtemp(prefix="opf-ingest-apply-revert-")).resolve()
    ran, classes = [], dict(safety=0, guard_execution=0)
    try:
        for number, row in enumerate(_DISCRIMINATORS):
            identity, key, test, old, new = row[:5]
            strips = row[5] if len(row) > 5 else ()
            source = read[key]
            file_path = str(sources[key])
            digest = _sha(source.encode("utf-8"))
            # The harness lives IN this file, so a mutation anchor also appears in its own docstrings and
            # the _DISCRIMINATORS table below. Mutate only the production region ahead of this section, so
            # uniqueness is judged against the guard, never the harness's description of it.
            prefix = source.split(_REVERT_MARKER, 1)[0]
            suffix = source[len(prefix):]
            # An isolated row's baseline strips its named overlapping layers in the candidate only; any other
            # row's baseline is the pristine source itself.
            for strip in strips:
                strip_old, strip_new = _STRIPS[strip]
                if prefix.count(strip_old) != 1:
                    raise RuntimeError("baseline strip target is not unique in the production region: " + identity)
                prefix = prefix.replace(strip_old, strip_new, 1)
            pristine = _load_revert_candidate(prefix + suffix, "_revert_pristine_{}".format(number), file_path)
            try:
                baseline_bits = []
                with _revert_trace(pristine, key, identity, baseline_bits):
                    test(pristine, base / "p{}".format(number))
            finally:
                sys.modules.pop(pristine.__name__, None)
            if prefix.count(old) != 1:
                raise RuntimeError("mutation target is not unique in the production region: " + identity)
            mutant = _load_revert_candidate(prefix.replace(old, new, 1) + suffix,
                                            "_revert_mutant_{}".format(number), file_path)
            try:
                mutant_bits = []
                with _revert_trace(mutant, key, identity, mutant_bits):
                    test(mutant, base / "m{}".format(number))
            except AssertionError as exc:
                if str(exc) != identity:
                    raise RuntimeError("wrong assertion for " + identity) from exc
            else:
                raise RuntimeError("reversal survived: " + identity)
            finally:
                sys.modules.pop(mutant.__name__, None)
            kind = "guard-execution" if identity in _GUARD_EXECUTION else "safety"
            asserted = _REVERT_ASSERTED[identity]
            flips, witnesses = _revert_boolean_witness(
                identity, baseline_bits, mutant_bits, kind == "safety", asserted)
            restored = _load_revert_candidate(source, "_revert_restored_{}".format(number), file_path)
            try:
                restored_bits = []
                with _revert_trace(restored, key, identity, restored_bits):
                    test(restored, base / "r{}".format(number))
                if not strips and restored_bits != baseline_bits:
                    raise RuntimeError("Boolean outcomes did not restore: " + identity)
            finally:
                sys.modules.pop(restored.__name__, None)
            classes[kind.replace("-", "_")] += 1
            print("RED-ON-REVERT", identity, "assertion=" + identity, "class=" + kind,
                  "baseline=" + ("stripped:" + "+".join(strips) if strips else "pristine"), "restored=PASS",
                  "boolean_flip=" + ("yes" if flips else "no"),
                  "asserted=" + ",".join("{}:{}:{}".format(i, cid, expected) for i, cid, expected in asserted),
                  "boolean_flips=" + (",".join(flips) or "none"),
                  "boolean_witness=" + (",".join(witnesses) or "none"), "candidate_sha256=" + digest)
            ran.append(identity)
    finally:
        shutil.rmtree(str(base), ignore_errors=True)
    print("OPF-INGEST-APPLY RED-ON-REVERT: {} discriminators, {} safety and {} guard-execution ({})".format(
        len(ran), classes["safety"], classes["guard_execution"], ", ".join(ran)))
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


def _self_test_main(args):
    """Keep both registered self-test legs inside the same isolation boundary."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _self_test_main_isolated(args)


def _self_test_main_isolated(args):
    rc = self_test()
    return rc if rc != 0 or "--red-on-revert" not in args else _red_on_revert_main()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--self-test"], ["--self-test", "--red-on-revert"]):
        return _self_test_main(args)
    print("_opf_ingest_apply: the ingest promotion coordinator; run with --self-test (add --red-on-revert "
          "for the guard-discrimination harness; no verb is wired in this slice).",
          file=sys.stderr if args else sys.stdout)
    return 2 if args else 0


if __name__ == "__main__":
    sys.exit(main())
