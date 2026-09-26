"""L3b PR1: read-only, inert homes-migration planning.

This module captures legacy evidence and freezes a relocation map. It never writes,
claims a lock, acquires a lease, applies an operation, or invokes recovery.

Deferred slices:
* Apply: quiescence; capability-bound layout locking and legacy-lock reconciliation;
  rename or copy-fsync-verify-unlink publication; byte-exact journal transport, A13
  verification and receipt; the final homes/spec-version switch; legacy retirement;
  imported/layout receipts; and gitignore installation.
* Recover: --homes-recover, crash injection, and the complete migration round-trip.
* L4 activation: supported-version changes, writer migration remedies, and init.
* Doctor declaration-versus-reality reconciliation.
* L5 expiry and L6 registrations beyond this check.

Coverage boundaries:
* Ownership means consistency with OPF's records, not cryptographic authentication.
  A deliberately forged, internally consistent journal is outside this check.
* A captured Move proposal alone proves no execution. The supported Move proof
  additionally requires a completed import journal binding the proposal preimage,
  destination creation, and source removal. Other archive bytes remain in place.
* Only complete, projection-bound import journals are supported here. Open,
  rolled-back, lock-bearing, or otherwise unprovable histories fail closed and need
  reconciliation before planning.
* Repeated contained observations detect ordinary concurrent changes but do not
  provide an atomic snapshot. Apply must re-resolve, lock, and revalidate every
  binding, including destination occupancy. This plan is not execution authority.
* Reads can update filesystem access times. No content, directory entry, permission,
  lease, journal, or other application state is written.
* Individual reads use the contained reader's ceiling. TOML/JSON metadata has the
  store-read ceiling; snapshots also have explicit count, depth, and aggregate
  byte ceilings. Exceeding a ceiling refuses the plan.
* Canonical bytes are deterministic for the same observation AND supplied clock
  instant. Live invocations can differ in generated_at.

Legacy authorities:
  _opf_import.py:137,150-154: legacy import roots and transaction names.
  _opf_ingest.py:1016: legacy Move archive root.
  _opf_import.py:1711-1840,2195-2303,2347-2490: staged run producers.
  _opf_import.py:4474-4568: durable archive and transaction producer.
  _journal.py:1038-1151: preimage names and bindings.
"""

import hashlib
import os
import stat
from pathlib import Path

import _journal
import _opf_emit
import _opf_import as imp
import _opf_ingest as ingest
import _opf_store as store


PLAN_FORMAT = "opf.layout-migration.plan/v1"
EVIDENCE_INVENTORY_FORMAT = "opf.evidence.inventory/v1"

LEGACY_HOMES = (
    imp.IMPORTS_REL,
    imp.IMPORT_ARCHIVE_REL,
    imp.IMPORT_OPS_REL,
    ingest.ARCHIVE_DIRNAME,
)

MAX_SNAPSHOT_ENTRIES = 20000
MAX_SNAPSHOT_BYTES = 128 << 20
MAX_DEPTH = 64

_STAGE_FILES = frozenset((
    "run.toml", "plan.toml", "mappings.toml", "report.toml",
    imp.INVENTORY_NAME, imp.PROPOSALS_NAME, imp.REPORT_MD_NAME,
    imp.ACCEPTANCE_NAME, imp.INGEST_ACTIONS_NAME,
    imp.CANDIDATES_DRAFT_NAME, imp.INGEST_REVIEW_NAME,
))
_STAGE_DIRS = frozenset(("candidate", "fragments", "sources"))


class MigrationPlanError(Exception):
    """A located cannot-evaluate result; no partial plan accompanies it."""

    def __init__(self, path, condition):
        self.path = str(path)
        self.condition = str(condition)
        super().__init__("{}: {}".format(repr(self.path), self.condition))


def _need(condition, path, detail):
    if not condition:
        raise MigrationPlanError(path, detail)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(raw):
    return "sha256:" + _sha(raw)


def _hex(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _path(value, where):
    try:
        return store._home_file(value)
    except (TypeError, ValueError) as exc:
        raise MigrationPlanError(where, "invalid contained path: {}".format(exc))


def _run(value, where):
    _need(isinstance(value, str) and imp._RUN_ID_RE.fullmatch(value) is not None,
          where, "ownership unprovable: invalid import run-id")
    store.evidence_run("import", value)
    return value


def _toml(raw, where):
    _need(type(raw) is bytes and len(raw) <= store.MAX_STORE_READ_BYTES,
          where, "missing or oversized TOML metadata")
    try:
        value = imp.tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise MigrationPlanError(where, "malformed TOML: {}".format(exc))
    _need(isinstance(value, dict), where, "TOML document is not a table")
    return value


def _json(raw, where):
    try:
        return imp._strict_json(raw)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise MigrationPlanError(where, "malformed or oversized JSON: {}".format(exc))


def _schema(doc, where, run_id=None):
    _need(type(doc.get("schema")) is int and doc["schema"] == 1,
          where, "ownership unprovable: schema is not integer 1")
    if run_id is not None:
        _need(doc.get("run_id") == run_id, where,
              "ownership unprovable: record names another run")


def _signature(st):
    # Access time is deliberately excluded: observing bytes may update it.
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns)


def _read(parent_fd, name, where, control=False):
    """Read through the existing contained reader and reconcile object identity.

    _journal.py:304-353 supplies no-follow, fd-bound, incrementally bounded reads.
    The before/opened/after comparison adds a conservative change detector.
    """
    try:
        before = _journal._lstat_contained(parent_fd, name)
        _need(before is not None and stat.S_ISREG(before.st_mode), where,
              "symlink or wrong type: expected a regular file")
        raw, opened = _journal._read_contained(
            parent_fd, name, require_single_link=control)
        after = _journal._lstat_contained(parent_fd, name)
        _need(after is not None
              and _signature(before) == _signature(opened) == _signature(after),
              where, "file identity or metadata changed during the read")
        return raw
    except MigrationPlanError:
        raise
    except (OSError, _journal.JournalError, ValueError) as exc:
        raise MigrationPlanError(where, "cannot read contained file: {}".format(exc))


def _snapshot(root_fd):
    """Return an observed tree: directories are None, regular files are bytes.

    Only LEGACY_HOMES are listed. In particular the enclosing .aiqt directory is
    never enumerated. Directory traversal follows _journal.py:927-963's fd-bound
    enumeration pattern; path resolution stays in its contained helpers.
    """
    tree = {}
    total = 0

    def add(rel, value):
        nonlocal total
        _need(rel not in tree, rel, "duplicate directory entry")
        _need(len(tree) < MAX_SNAPSHOT_ENTRIES, rel,
              "snapshot entry ceiling exceeded")
        if value is not None:
            total += len(value)
            _need(total <= MAX_SNAPSHOT_BYTES, rel,
                  "snapshot aggregate byte ceiling exceeded")
        tree[rel] = value

    def descend(parent_fd, name, rel, expected, depth):
        _need(depth <= MAX_DEPTH, rel, "snapshot depth ceiling exceeded")
        dfd = None
        try:
            dfd = _journal._open_dir_contained(parent_fd, name)
            opened = os.fstat(dfd)
            _need(_signature(opened) == _signature(expected), rel,
                  "directory identity changed before enumeration")
            add(rel, None)
            with os.scandir(dfd) as entries:
                names = sorted(entry.name for entry in entries)
            for child in names:
                child_rel = rel + "/" + child
                _path(child_rel, child_rel)
                st = os.lstat(child, dir_fd=dfd)
                if stat.S_ISDIR(st.st_mode):
                    descend(dfd, child, child_rel, st, depth + 1)
                elif stat.S_ISREG(st.st_mode):
                    control = (rel.startswith(imp.IMPORT_OPS_REL + "/")
                               or child.endswith(".toml")
                               or child == imp.ACCEPTANCE_NAME)
                    add(child_rel, _read(dfd, child, child_rel, control))
                else:
                    raise MigrationPlanError(
                        child_rel, "symlink or wrong type: expected directory or regular file")
            _need(_signature(os.fstat(dfd)) == _signature(opened), rel,
                  "directory changed during enumeration")
            current = _journal._lstat_contained(parent_fd, name)
            _need(current is not None
                  and _signature(current) == _signature(opened), rel,
                  "directory binding changed during enumeration")
        except MigrationPlanError:
            raise
        except (OSError, _journal.JournalError, ValueError) as exc:
            raise MigrationPlanError(
                rel, "cannot open or list contained directory: {}".format(exc))
        finally:
            if dfd is not None:
                os.close(dfd)

    for home in LEGACY_HOMES:
        try:
            st = _journal._lstat_contained(root_fd, home)
        except (OSError, _journal.JournalError, ValueError) as exc:
            raise MigrationPlanError(home, "cannot classify legacy home: {}".format(exc))
        if st is None:
            continue
        _need(stat.S_ISDIR(st.st_mode), home,
              "symlink or wrong type: legacy home must be a directory")
        descend(root_fd, home, home, st, 0)
    return tree


def _children(tree, parent):
    prefix = parent + "/"
    return sorted(p for p in tree
                  if p.startswith(prefix) and "/" not in p[len(prefix):])


def _files(tree, parent):
    prefix = parent + "/"
    return {p[len(prefix):]: raw for p, raw in tree.items()
            if p.startswith(prefix) and raw is not None}


def _directory(tree, rel):
    _need(rel in tree and tree[rel] is None, rel,
          "wrong type or missing directory")


def _bytes(tree, rel):
    raw = tree.get(rel)
    _need(type(raw) is bytes, rel, "missing or wrong-type required file")
    return raw


def _tree_shape(tree):
    _need(isinstance(tree, dict), "<snapshot>", "snapshot is not a mapping")
    _need(len(tree) <= MAX_SNAPSHOT_ENTRIES, "<snapshot>",
          "snapshot entry ceiling exceeded")
    total = 0
    for rel, value in tree.items():
        _path(rel, rel)
        _need(any(rel == home or rel.startswith(home + "/")
                  for home in LEGACY_HOMES),
              rel, "snapshot names a path outside the legacy-home roster")
        _need(value is None or type(value) is bytes, rel,
              "snapshot member is neither directory nor file bytes")
        if rel not in LEGACY_HOMES:
            _directory(tree, rel.rsplit("/", 1)[0])
        else:
            _directory(tree, rel)
        if value is not None:
            _need(len(value) <= _journal._MAX_PRODUCT_READ_BYTES, rel,
                  "contained read ceiling exceeded")
            total += len(value)
    _need(total <= MAX_SNAPSHOT_BYTES, "<snapshot>",
          "snapshot aggregate byte ceiling exceeded")


def _runs(tree, home):
    result = {}
    for rel in _children(tree, home):
        _directory(tree, rel)
        rid = _run(rel.rsplit("/", 1)[-1], rel)
        result[rid] = rel
    return result


def _acceptance(raw, where, rid, plan_digest, inventory_digest):
    doc = _json(raw, where)
    findings = imp._validate_acceptance(doc, where)
    _need(not findings, where,
          "ownership unprovable: {}".format("; ".join(findings)))
    _need(doc["run_id"] == rid and doc["plan_digest"] == plan_digest
          and doc["inventory_digest"] == inventory_digest,
          where, "acceptance does not bind this run's plan and inventory")
    return doc


def _stage(tree, rel, rid):
    """Validate the producer-accounted staged shape and its content bindings.

    This is ownership reconnaissance, not a substitute for the import promotion
    gate. Known candidate indexes still use the existing schema validator.
    """
    files = _files(tree, rel)
    roster = imp._roster()
    index_names = set(roster) | set(store.MODULE_TYPES)
    for path, value in tree.items():
        if not path.startswith(rel + "/"):
            continue
        suffix = path[len(rel) + 1:]
        if value is None:
            _need(suffix in _STAGE_DIRS, path,
                  "unknown directory inside an OPF run")
            continue
        parts = suffix.split("/")
        allowed = suffix in _STAGE_FILES
        if len(parts) == 2 and parts[0] == "sources":
            allowed = _hex(parts[1])
        if len(parts) == 2 and parts[0] == "candidate":
            allowed = (parts[1] in ("counters.toml", "worklog.toml")
                       or any(parts[1] == name + ".index.toml"
                              for name in index_names))
        if suffix == "fragments/legacy_fragment.index.toml":
            allowed = True
        _need(allowed, path, "unknown member inside an OPF run")

    required = ("run.toml", "plan.toml", "mappings.toml",
                "report.toml", "candidate/counters.toml")
    for name in required:
        _bytes(tree, rel + "/" + name)
    _directory(tree, rel + "/candidate")
    _directory(tree, rel + "/sources")

    docs = {name: _toml(raw, rel + "/" + name)
            for name, raw in files.items() if name.endswith(".toml")}
    run = docs["run.toml"]
    report = docs["report.toml"]
    _schema(run, rel + "/run.toml", rid)
    _schema(report, rel + "/report.toml", rid)
    _need(type(report.get("verdict")) is int and report["verdict"] == 0
          and report.get("promotion_ready") is True,
          rel + "/report.toml", "ownership unprovable: incomplete staged run")

    source_rows = run.get("source")
    _need(isinstance(source_rows, list), rel + "/run.toml",
          "source inventory is not an array")
    sources, paths, bodies = [], set(), set()
    for row in source_rows:
        _need(isinstance(row, dict) and set(row) == {"path", "sha256", "size"},
              rel + "/run.toml", "malformed source record")
        sp = _path(row["path"], rel + "/run.toml")
        _need(sp not in paths and _hex(row["sha256"])
              and type(row["size"]) is int and row["size"] >= 0,
              rel + "/run.toml", "duplicate or malformed source identity")
        body_rel = rel + "/sources/" + row["sha256"]
        raw = _bytes(tree, body_rel)
        _need(_sha(raw) == row["sha256"] and len(raw) == row["size"],
              body_rel, "source bytes disagree with their recorded digest or size")
        paths.add(sp)
        bodies.add("sources/" + row["sha256"])
        sources.append(dict(row, raw=raw))
    _need({p for p in files if p.startswith("sources/")} == bodies,
          rel + "/sources", "source-body roster disagrees with run.toml")

    plan = docs["plan.toml"]
    try:
        fragments = imp._validate_plan_shape(plan)
        _need(set(fragments) == paths, rel + "/plan.toml",
              "plan source roster disagrees with run.toml")
        for source in sources:
            imp._tile_spans(fragments[source["path"]], source["size"],
                            rel + "/plan.toml")
        stamp = imp.datetime.datetime.strptime(
            run["staged_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=imp.datetime.timezone.utc)
        imp._require_nonce(run.get("nonce"))
        expected_id = imp._run_id(sources, files["plan.toml"], stamp, run["nonce"])
        _need(expected_id == rid, rel + "/run.toml",
              "run-id does not recompute from its source, plan, time, and nonce")
        inventory, inv_digest, _, _ = imp._build_inventory(sources)
    except MigrationPlanError:
        raise
    except (imp._StageError, KeyError, TypeError, ValueError) as exc:
        raise MigrationPlanError(rel, "ownership unprovable: {}".format(exc))

    pd = _digest(files["plan.toml"])
    _need(report.get("plan_digest") == pd
          and report.get("inventory_digest") == inv_digest,
          rel + "/report.toml", "report plan or inventory digest does not recompute")

    # The report accounts for the first-pass candidate payload, not the later
    # review additions: _opf_import.py:1762-1774,2195-2206.
    core = {name for name in files
            if name in ("run.toml", "plan.toml", "mappings.toml")
            or name.startswith(("candidate/", "fragments/", "sources/"))}
    rows = report.get("artifact")
    _need(isinstance(rows, list), rel + "/report.toml",
          "report artifact roster is not an array")
    recorded = {}
    for row in rows:
        _need(isinstance(row, dict) and set(row) == {"path", "sha256"}
              and isinstance(row["path"], str) and _hex(row["sha256"]),
              rel + "/report.toml", "malformed artifact binding")
        name = row["path"]
        _path(name, rel + "/report.toml")
        _need(name not in recorded and name in core,
              rel + "/" + name, "duplicate or unrecognized report artifact")
        _need(_sha(files[name]) == row["sha256"], rel + "/" + name,
              "artifact digest does not recompute")
        recorded[name] = row["sha256"]
    _need(set(recorded) == core, rel + "/report.toml",
          "report does not account for the exact candidate payload")

    for name, doc in docs.items():
        if name.endswith(".index.toml") or name == "candidate/worklog.toml":
            rtype = ("worklog" if name.endswith("/worklog.toml")
                     else name.rsplit("/", 1)[-1][:-len(".index.toml")])
            try:
                imp._record_ids(doc, rel + "/" + name, roster=roster,
                                expected_type=rtype)
            except imp._StageError as exc:
                raise MigrationPlanError(rel + "/" + name, str(exc))
    counters = docs["candidate/counters.toml"]
    _schema(counters, rel + "/candidate/counters.toml")
    _need(set(counters) == {"schema", "counters"}
          and isinstance(counters["counters"], dict)
          and all(isinstance(k, str) and type(v) is int and v >= 0
                  for k, v in counters["counters"].items()),
          rel + "/candidate/counters.toml", "malformed candidate counters")
    mappings = docs["mappings.toml"]
    _schema(mappings, rel + "/mappings.toml")
    _need(isinstance(mappings.get("mapping"), list),
          rel + "/mappings.toml", "malformed mapping roster")
    for row in mappings["mapping"]:
        _need(isinstance(row, dict) and isinstance(row.get("source_path"), str)
              and row["source_path"] in paths
              and isinstance(row.get("state"), str)
              and row["state"] in imp.MAPPING_STATES
              and isinstance(row.get("origin"), str)
              and row["origin"] in imp._ORIGIN_SET
              and isinstance(row.get("span"), list) and len(row["span"]) == 2
              and all(type(n) is int for n in row["span"]),
              rel + "/mappings.toml", "malformed mapping row")

    if imp.INVENTORY_NAME in docs:
        _need(docs[imp.INVENTORY_NAME] == inventory,
              rel + "/" + imp.INVENTORY_NAME,
              "inventory does not reproduce from the preserved sources")
    if imp.PROPOSALS_NAME in docs:
        proposals = docs[imp.PROPOSALS_NAME]
        _schema(proposals, rel + "/" + imp.PROPOSALS_NAME, rid)
        _need(isinstance(proposals.get("proposal"), list),
              rel + "/" + imp.PROPOSALS_NAME, "malformed proposal roster")
    if imp.ACCEPTANCE_NAME in files:
        _acceptance(files[imp.ACCEPTANCE_NAME], rel + "/" + imp.ACCEPTANCE_NAME,
                    rid, pd, inv_digest)

    if any(name in files for name in imp._INGEST_RUN_MARKERS):
        for name in imp._INGEST_RUN_MARKERS:
            _need(name in docs, rel + "/" + name,
                  "ownership unprovable: incomplete ingest evidence")
        actions = docs[imp.INGEST_ACTIONS_NAME]
        _schema(actions, rel + "/" + imp.INGEST_ACTIONS_NAME, rid)
        _need(actions.get("format") == imp.INGEST_ACTIONS_FORMAT
              and isinstance(actions.get("action"), list),
              rel + "/" + imp.INGEST_ACTIONS_NAME, "malformed ingest actions")
        _schema(docs[imp.CANDIDATES_DRAFT_NAME],
                rel + "/" + imp.CANDIDATES_DRAFT_NAME, rid)
        _need(isinstance(docs[imp.CANDIDATES_DRAFT_NAME].get("candidate"), list),
              rel + "/" + imp.CANDIDATES_DRAFT_NAME, "malformed ingest candidates")
        try:
            bundle = imp._validate_staged_ingest_bundle(
                docs[imp.INGEST_REVIEW_NAME], rid)
        except imp._StageError as exc:
            raise MigrationPlanError(rel + "/" + imp.INGEST_REVIEW_NAME, str(exc))
        bindings = {
            "plan_digest": pd, "inventory_digest": inv_digest,
            "ingest_actions_digest": _digest(files[imp.INGEST_ACTIONS_NAME]),
            "candidates_draft_digest": _digest(files[imp.CANDIDATES_DRAFT_NAME]),
        }
        for key, name in (("proposals_digest", imp.PROPOSALS_NAME),
                          ("inventory_toml_digest", imp.INVENTORY_NAME)):
            bindings[key] = _digest(_bytes(tree, rel + "/" + name))
        _need(bundle["binding"] == bindings, rel + "/" + imp.INGEST_REVIEW_NAME,
              "ingest bundle bindings do not recompute")

    return pd, inv_digest


def _frames(raw, where):
    """Strict, read-only decoding of the captured journal bytes.

    The wire authority is _journal.py:479-483,631-664. Unlike recovery, planning
    refuses a torn tail; it neither truncates nor treats a prefix as the whole.
    """
    frames, offset = [], 0
    while offset < len(raw):
        nl = raw.find(b"\n", offset)
        _need(nl >= 0, where, "torn journal frame header")
        parts = raw[offset:nl].split(b" ")
        _need(len(parts) == 4 and parts[0] == _journal.MAGIC, where,
              "malformed journal frame header")
        try:
            kind = parts[1].decode("ascii")
            length = int(parts[2])
            digest = parts[3].decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise MigrationPlanError(where, "malformed journal frame: {}".format(exc))
        _need(kind in _journal.FRAME_TYPES and length >= 0 and _hex(digest),
              where, "invalid journal frame type, length, or digest")
        start, end = nl + 1, nl + 1 + length
        _need(end < len(raw) and raw[end:end + 1] == b"\n",
              where, "torn journal frame payload")
        body = raw[start:end]
        _need(_sha(body) == digest, where, "journal frame checksum mismatch")
        frames.append((kind, _json(body, where)))
        offset = end + 1
    try:
        _journal._validate_terminal_agreement(frames)
    except _journal.JournalError as exc:
        raise MigrationPlanError(where, str(exc))
    _need(tuple(k for k, _ in frames) == (_journal.F_INTENT, _journal.F_COMPLETE),
          where, "ownership unprovable: journal is not terminal COMPLETE")
    return frames


def _touching(ops, path):
    return [(i, op) for i, op in enumerate(ops) if op["path"] == path]


def _created(ops, path, raw):
    found = _touching(ops, path)
    return (len(found) == 1 and found[0][1]["op"] == "create"
            and found[0][1]["poststate"].get("content-sha256") == _sha(raw))


def _journal_proof(tree, rel, rid, txn, txn_raw):
    where = rel + "/frames.log"
    frames = _frames(_bytes(tree, where), where)
    intent = frames[0][1]
    header = intent.get("header")
    tid = rel.rsplit("/", 1)[-1]
    _need(intent.get("txn") == tid and isinstance(header, dict)
          and header.get("unit") == rid and header.get("kind") == "import-apply"
          and header.get("plan_digest") == txn["plan_digest"],
          where, "ownership unprovable: INTENT does not bind this import run")
    ops = intent.get("ops")
    _need(isinstance(ops, list), where, "INTENT has no operation roster")
    expected_preimages = set()
    for i, op in enumerate(ops):
        _need(isinstance(op, dict) and isinstance(op.get("op"), str)
              and op["op"] in _journal.OP_KINDS, where, "malformed INTENT operation")
        _path(op.get("path"), where)
        pre, post = op.get("prestate"), op.get("poststate")
        _need(isinstance(pre, dict) and isinstance(post, dict), where,
              "INTENT operation lacks state bindings")
        kind = op["op"]
        if kind in ("create", "mkdir"):
            _need(pre == {"kind": "absent"}, where, "invalid creation prestate")
        elif kind == "rmdir":
            _need(pre.get("kind") == "dir", where, "invalid directory prestate")
        else:
            payload = str(i)
            _need(pre.get("kind") == "file" and pre.get("payload") == payload
                  and _hex(pre.get("sha256"))
                  and type(pre.get("size")) is int and pre["size"] >= 0,
                  where, "malformed file preimage binding")
            name = "preimages/" + payload
            raw = _bytes(tree, rel + "/" + name)
            _need(len(raw) == pre["size"] and _sha(raw) == pre["sha256"],
                  rel + "/" + name, "preimage digest or size mismatch")
            expected_preimages.add(name)
        expected_kind = ("file" if kind in ("create", "write")
                         else "dir" if kind == "mkdir" else "absent")
        _need(post.get("kind") == expected_kind, where,
              "operation and poststate kind disagree")
        if expected_kind == "file":
            _need(_hex(post.get("content-sha256")), where,
                  "file poststate has no content digest")
    members = _files(tree, rel)
    _need(set(members) == {"frames.log"} | expected_preimages, rel,
          "unknown or missing journal member")
    for child in _children(tree, rel):
        _need(child in (where, rel + "/preimages"), child,
              "unknown journal member")
    if rel + "/preimages" in tree:
        _directory(tree, rel + "/preimages")
        _need(all(tree[p] is not None for p in _children(tree, rel + "/preimages")),
              rel + "/preimages", "wrong-type preimage member")
    problem = imp._txn_record_intent_problem(
        ops, imp._txn_record_rel(rid), txn_raw)
    _need(not problem, where, "ownership unprovable: " + problem)
    return ops


def _move_proofs(tree, journal_rel, rid, ops):
    """Recognize an executed Move, never an unexecuted staged proposal.

    Proposal shape: _opf_ingest.py:1277-1281.
    Execution/preimage bindings: _journal.py:1038-1151,1585-1653.
    No claim is made that main's legacy producer emits this combination.
    """
    proofs = {}
    action_path = imp.IMPORTS_REL + "/" + rid + "/" + imp.INGEST_ACTIONS_NAME
    found = _touching(ops, action_path)
    if len(found) != 1 or found[0][1]["op"] != "remove":
        return proofs
    action_op = found[0][1]
    raw = _bytes(tree, journal_rel + "/preimages/" + action_op["prestate"]["payload"])
    doc = _toml(raw, action_path)
    _schema(doc, action_path, rid)
    _need(doc.get("format") == imp.INGEST_ACTIONS_FORMAT
          and isinstance(doc.get("action"), list),
          action_path, "malformed captured ingest-action record")
    for row in doc["action"]:
        _need(isinstance(row, dict), action_path, "malformed captured action")
        if row.get("kind") != "move":
            continue
        _need(set(row) == {"kind", "scope", "source_path", "dest_path", "sha256", "size"}
              and isinstance(row["scope"], str) and _hex(row["sha256"])
              and type(row["size"]) is int and row["size"] >= 0,
              action_path, "malformed captured Move disposition")
        source = _path(row["source_path"], action_path)
        dest = _path(row["dest_path"], action_path)
        if dest != ingest._archive_dest(source):
            continue
        created, removed = _touching(ops, dest), _touching(ops, source)
        if len(created) != 1 or len(removed) != 1:
            continue
        ci, create = created[0]
        ri, remove = removed[0]
        if not (ci < ri and create["op"] == "create" and remove["op"] == "remove"
                and create["poststate"].get("content-sha256") == row["sha256"]
                and remove["prestate"].get("sha256") == row["sha256"]
                and remove["prestate"].get("size") == row["size"]):
            continue
        _need(dest not in proofs, action_path, "duplicate executed Move disposition")
        proofs[dest] = (row["sha256"], row["size"], rid,
                        journal_rel + "/frames.log")
    return proofs


def build_homes_plan(resolution, manifest_bytes, tree, *, now):
    """Pure builder over a captured observation; performs no I/O.

    `tree` is the complete result of _snapshot, not an ownership assertion or an
    adopter-supplied work-list. The CLI wrapper obtains and repeats that observation.
    Callers exercising this pure seam must supply the whole observation themselves.
    """
    context = str(resolution.store_root)
    try:
        _need(resolution.status == store.RESOLVED, context, "store is not resolved")
        _need(Path(resolution.store_root) == Path(resolution.product_root), context,
              "non-co-located store: multi-root migration is unsupported")
        imp._require_utc(now)
        manifest = _toml(manifest_bytes, resolution.machine_rel + "/" + store.MANIFEST_NAME)
        validation = store.validate_manifest(manifest)
        _need(validation.status == store.VALID,
              resolution.machine_rel + "/" + store.MANIFEST_NAME,
              "manifest invalid: {}".format("; ".join(validation.findings)))
        if store.homes_generation(manifest) == 2:
            return None
        _tree_shape(tree)
        staged = _runs(tree, imp.IMPORTS_REL)
        archived = _runs(tree, imp.IMPORT_ARCHIVE_REL)

        projections = {}
        for rel in _children(tree, imp.IMPORT_OPS_REL):
            if rel == imp.IMPORT_JOURNAL_REL:
                _directory(tree, rel)
                continue
            _directory(tree, rel)
            rid = _run(rel.rsplit("/", 1)[-1], rel)
            record_rel = rel + "/" + imp.TRANSACTION_NAME
            _need(_children(tree, rel) == [record_rel], rel,
                  "unknown or missing import transaction member")
            raw = _bytes(tree, record_rel)
            txn = _toml(raw, record_rel)
            ok, detail = imp._validate_transaction_record(txn, rid)
            _need(ok and txn.get("state") == "complete", record_rel,
                  "ownership unprovable: " + (detail or "transaction is not complete"))
            rr = txn["restore_ref"]
            _need(rr.get("journal_rel") == imp.IMPORT_JOURNAL_REL
                  and rr.get("txn_id") == txn["txn_id"],
                  record_rel, "transaction restore_ref names another journal or transaction")
            tid = _path(txn["txn_id"], record_rel)
            _need("/" not in tid, record_rel, "transaction id is not one component")
            projections[rid] = (txn, raw)

        entries, destinations = [], {}

        def add(source, dest, op, rid=None, kind="import", **extra):
            raw = _bytes(tree, source)
            digest = _digest(raw)
            previous = destinations.get(dest)
            _need(previous is None or previous == digest, source,
                  "merge digest conflict at " + dest)
            row = dict(source_rel=source, dest_rel=dest, op=op,
                       kind=kind, digest=digest)
            if rid is not None:
                row["run_id"] = rid
            if previous is not None:
                row["identical_destination"] = True
            destinations[dest] = digest
            if op == "transport":
                row.update(old_path=source, old_digest=digest,
                           new_path=dest, new_digest=digest)
            row.update(extra)
            entries.append(row)

        # Detect overlapping-byte conflicts before choosing either contribution.
        for runs, operation in ((staged, "relocate"), (archived, "merge")):
            for rid, rel in sorted(runs.items()):
                home = store.evidence_run("import", rid)
                for suffix in sorted(_files(tree, rel)):
                    add(rel + "/" + suffix, home + "/" + suffix, operation, rid)

        stage_bindings = {rid: _stage(tree, rel, rid)
                          for rid, rel in sorted(staged.items())}

        journals = {}
        for rel in _children(tree, imp.IMPORT_JOURNAL_REL):
            _directory(tree, rel)
            tid = rel.rsplit("/", 1)[-1]
            _need(tid not in ("lock", "lock.break"), rel,
                  "ownership unprovable: legacy lock needs reconciliation")
            owners = [(rid, txn, raw) for rid, (txn, raw) in projections.items()
                      if txn["txn_id"] == tid]
            _need(len(owners) == 1, rel,
                  "ownership unprovable: journal lacks one matching transaction projection")
            rid, txn, raw = owners[0]
            ops = _journal_proof(tree, rel, rid, txn, raw)
            journals[rid] = (rel, ops)
            for suffix in sorted(_files(tree, rel)):
                add(rel + "/" + suffix,
                    store.journal_root("import") + "/" + tid + "/" + suffix,
                    "transport", rid)

        moves = {}
        for rid, (txn, raw) in sorted(projections.items()):
            record_rel = imp._txn_record_rel(rid)
            _need(rid in journals, record_rel, "transaction has no proven journal")
            journal_rel, ops = journals[rid]
            _need(rid in archived, record_rel, "complete transaction lacks durable archive")
            rel = archived[rid]
            source_hashes = txn.get("archived_sources")
            _need(isinstance(source_hashes, list)
                  and all(_hex(h) for h in source_hashes)
                  and len(source_hashes) == len(set(source_hashes))
                  and _hex(txn.get("acceptance_sha256")),
                  record_rel, "transaction lacks valid durable archive bindings")
            expected = {"sources/" + h for h in source_hashes} | {imp.ACCEPTANCE_NAME}
            _need(set(_files(tree, rel)) == expected, rel,
                  "unknown, missing, or unbound durable archive member")
            _directory(tree, rel + "/sources")
            _need(_children(tree, rel) == sorted(
                (rel + "/sources", rel + "/" + imp.ACCEPTANCE_NAME)),
                rel, "unknown durable archive directory")
            for suffix in expected:
                member = rel + "/" + suffix
                data = _bytes(tree, member)
                wanted = (txn["acceptance_sha256"] if suffix == imp.ACCEPTANCE_NAME
                          else suffix.split("/", 1)[1])
                _need(_sha(data) == wanted and _created(ops, member, data), member,
                      "archive bytes are not bound by the transaction and its INTENT")
            _acceptance(_bytes(tree, rel + "/" + imp.ACCEPTANCE_NAME),
                        rel + "/" + imp.ACCEPTANCE_NAME, rid,
                        txn["plan_digest"], txn["inventory_digest"])
            if rid in stage_bindings:
                _need(stage_bindings[rid] == (txn["plan_digest"], txn["inventory_digest"]),
                      record_rel, "staged and completed run bindings disagree")
            add(record_rel, store.txn_record("import", rid), "transport", rid)
            for dest, proof in _move_proofs(tree, journal_rel, rid, ops).items():
                _need(dest not in moves, dest, "ambiguous executed Move ownership")
                moves[dest] = proof
        for rid, rel in archived.items():
            _need(rid in projections, rel,
                  "ownership unprovable: archive lacks a transaction projection")

        left = []
        archive = ingest.ARCHIVE_DIRNAME
        for rel in sorted(tree):
            if not rel.startswith(archive + "/"):
                continue
            raw = tree[rel]
            if raw is None:
                if not _children(tree, rel):
                    left.append(dict(source_rel=rel, reason="unproven empty archive directory"))
                continue
            proof = moves.get(rel)
            if proof is None:
                reason = "no completed, digest-bound OPF Move evidence"
            elif (_sha(raw), len(raw)) != proof[:2]:
                reason = "archive bytes disagree with the recorded OPF Move"
            else:
                _, _, rid, evidence = proof
                suffix = rel[len(archive) + 1:]
                add(rel, store.moved_dest(suffix), "relocate", rid,
                    ownership_path=evidence,
                    ownership_digest=_digest(_bytes(tree, evidence)))
                continue
            left.append(dict(source_rel=rel, digest=_digest(raw),
                             size=len(raw), reason=reason))

        return dict(
            format=PLAN_FORMAT, schema=1,
            generated_at=imp._rfc3339(now),
            product_root=str(resolution.product_root),
            store_root=str(resolution.store_root),
            machine_rel=resolution.machine_rel,
            source_manifest_digest=_digest(manifest_bytes),
            source_homes=1, target_homes=2,
            target_spec_version=store.HOMES2_SPEC_VERSION,
            entry=sorted(entries, key=lambda row: row["source_rel"]),
            left_in_place=left,
        )
    except MigrationPlanError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError,
            _journal.JournalError, store.StoreError,
            imp._StageError, _opf_emit.EmitError) as exc:
        raise MigrationPlanError(context, "cannot validate migration evidence: {}".format(exc))


def _clock_now():
    # Reuse the sibling's UTC datetime dependency and timestamp convention:
    # _opf_import.py:101,452-457,480-481.
    return imp.datetime.datetime.now(imp.datetime.timezone.utc)


def plan_homes_migration(root):
    """Resolve, observe, validate, and return complete stdout text. No printing."""
    where = os.path.abspath(os.fspath(root))
    fd = None
    try:
        _journal.require_containment()
        resolution = store.resolve_store(
            Path(where),
            accept_tokens=(store.STANDARD_TOKEN, store.PRIOR_STANDARD_TOKEN))
        if resolution.status == store.NOT_ADOPTED:
            return "opf upgrade: NOT APPLICABLE ({})\n".format(resolution.detail)
        _need(resolution.status == store.RESOLVED, where,
              "cannot resolve store: " + resolution.detail)
        validation = store.load_manifest(resolution)
        manifest_rel = resolution.machine_rel + "/" + store.MANIFEST_NAME
        _need(validation.status == store.VALID, manifest_rel,
              "manifest invalid: {}; run the schema upgrade first where required".format(
                  "; ".join(validation.findings)))
        _need(Path(resolution.store_root) == Path(resolution.product_root),
              resolution.store_root,
              "non-co-located store: multi-root migration is unsupported")

        fd = store._open_dir_nofollow(resolution.store_root)
        manifest_raw = _read(fd, manifest_rel, manifest_rel, control=True)
        manifest = _toml(manifest_raw, manifest_rel)
        checked = store.validate_manifest(manifest)
        _need(checked.status == store.VALID, manifest_rel,
              "manifest changed or is invalid: " + "; ".join(checked.findings))
        if store.homes_generation(manifest) == 2:
            return "opf upgrade: already at homes 2; nothing to plan\n"

        first = _snapshot(fd)
        second = _snapshot(fd)
        _need(first == second, resolution.store_root,
              "legacy evidence changed between observations")
        _need(_read(fd, manifest_rel, manifest_rel, control=True) == manifest_raw,
              manifest_rel, "manifest changed during observation")
        model = build_homes_plan(
            resolution, manifest_raw, second, now=_clock_now())
        if model is None:
            return "opf upgrade: already at homes 2; nothing to plan\n"
        return _opf_emit.emit_checked(model)
    except MigrationPlanError:
        raise
    except (OSError, ValueError, TypeError, RecursionError,
            _journal.JournalError, store.StoreError, _opf_emit.EmitError) as exc:
        raise MigrationPlanError(where, "cannot evaluate migration plan: {}".format(exc))
    finally:
        if fd is not None:
            os.close(fd)
