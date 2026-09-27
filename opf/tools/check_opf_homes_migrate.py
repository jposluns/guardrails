#!/usr/bin/env python3
"""Read-only homes-plan behaviour, inertness, and red-on-revert checks.

Fixture bytes are constructed independently of the new planner, using fixed
payloads and the existing legacy record serializers. The synthetic completed Move
journal is a proof-contract fixture, not a claim that main's legacy apply producer
emits Move operations.

The harness writes only beneath its own mkdtemp root and removes that root.
"""

import contextlib
import datetime
import hashlib
import io
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal as journal
import _opf_emit as emit
import _opf_import as imp
import _opf_store as store
import _opf_homes_migrate as migrate
import opf


NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
BODY = b"pinned legacy original\n"
MOVED = b"pinned moved original\n"
FOREIGN = b"adopter-owned archive bytes\n"
NONCE = "homes-plan-fixture-v1"

MANIFEST = b"""[opf]
standard = "opf"
spec_version = "1.2.0"
layout = "inline"
posture = "required"
import_status = "none"

[types.backlog_item]
namespace = "BI"
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(raw):
    return "sha256:" + sha(raw)


def toml(doc):
    # Existing serializer, not the new plan builder.
    return emit.emit_checked(doc).encode("utf-8")


def framed(kind, obj):
    # Independent fixture encoder for _journal.py:479-483's wire format.
    payload = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return (b"AIQTJ1 " + kind.encode("ascii") + b" "
            + str(len(payload)).encode("ascii") + b" "
            + sha(payload).encode("ascii") + b"\n" + payload + b"\n")


def fixture_bytes(*, invalid_acceptance=False):
    """Return the closed, independently assembled legacy file roster."""
    source = dict(path="legacy/original.md", sha256=sha(BODY), size=len(BODY), raw=BODY)
    plan = toml({
        "fragments": {
            source["path"]: [{"span": [0, len(BODY)], "state": "ignored"}],
        },
    })
    rid = imp._run_id([source], plan, NOW, NONCE)
    stage = imp.IMPORTS_REL + "/" + rid
    archive = imp.IMPORT_ARCHIVE_REL + "/" + rid
    projection = imp.IMPORT_OPS_REL + "/" + rid + "/transaction.toml"
    tid = "homes-plan-fixture"
    jr = imp.IMPORT_JOURNAL_REL + "/" + tid
    inventory, inv_digest, _, _ = imp._build_inventory([source])
    core = {
        "run.toml": toml(imp._run_toml_model(
            rid, "2026-01-02T03:04:05Z", NONCE, [source])),
        "plan.toml": plan,
        "mappings.toml": toml({
            "schema": 1,
            "mapping": [{
                "source_path": source["path"], "span": [0, len(BODY)],
                "state": "ignored", "origin": "baseline",
            }],
        }),
        "candidate/counters.toml": b"schema = 1\n\n[counters]\nBI = 0\n",
        "sources/" + sha(BODY): BODY,
    }
    report = toml(imp._run_report_model(
        rid, plan, inv_digest, {"ignored": 1}, False, core))
    acceptance = json.dumps({
        "format": "opf.import.acceptance/v1",
        "run_id": rid, "plan_digest": digest(plan),
        "inventory_digest": inv_digest,
        "actor": {
            "declared": "fixture reviewer",
            "context": {"os_user": "", "git_identity": "", "hostname": ""},
        },
        "reviewed_at": "2026-01-02T03:04:05Z",
        "decisions": [],
    }, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if invalid_acceptance:
        doc = json.loads(acceptance)
        del doc["actor"]
        acceptance = json.dumps(doc, sort_keys=True).encode("ascii") + b"\n"
    stage_files = dict(core)
    stage_files.update({
        "report.toml": report,
        "inventory.toml": toml(inventory),
        "acceptance.json": acceptance,
    })
    archived = {
        archive + "/sources/" + sha(BODY): BODY,
        archive + "/acceptance.json": acceptance,
    }
    transaction = toml({
        "format": "opf.import.transaction/v1", "schema": 1,
        "run_id": rid, "state": "complete", "txn_id": tid,
        "plan_digest": digest(plan), "inventory_digest": inv_digest,
        "allocation": {},
        "archived_sources": [sha(BODY)],
        "acceptance_sha256": sha(acceptance),
        "restore_ref": {
            "txn_id": tid, "journal_rel": imp.IMPORT_JOURNAL_REL,
            "observed_head": "1" * 40,
        },
    })

    def create(path, raw):
        return {
            "op": "create", "path": path, "prestate": {"kind": "absent"},
            "poststate": {"kind": "file", "mode": 0o644, "content-sha256": sha(raw)},
        }

    ops = [create(projection, transaction)]
    ops.extend(create(path, raw) for path, raw in sorted(archived.items()))
    move_dest = ".archive/legacy/moved.md"
    ops.append(create(move_dest, MOVED))
    preimages = {}

    def remove(path, raw):
        slot = str(len(ops))
        preimages[jr + "/preimages/" + slot] = raw
        ops.append({
            "op": "remove", "path": path,
            "prestate": {
                "kind": "file", "mode": 0o644, "size": len(raw),
                "payload": slot, "sha256": sha(raw),
            },
            "poststate": {"kind": "absent"},
        })

    remove("legacy/moved.md", MOVED)
    actions = toml({
        "format": "opf-ingest-plan-actions-v1", "schema": 1, "run_id": rid,
        "action": [{
            "kind": "move", "scope": "declared",
            "source_path": "legacy/moved.md", "dest_path": move_dest,
            "sha256": sha(MOVED), "size": len(MOVED),
        }],
    })
    remove(stage + "/ingest-actions.toml", actions)
    frames = framed("INTENT", {
        "txn": tid,
        "header": {
            "unit": rid, "kind": "import-apply", "plan_digest": digest(plan),
        },
        "ops": ops,
    }) + framed("COMPLETE", {"txn": tid})

    files = {
        ".working/toml/manifest.toml": MANIFEST,
        ".archive/legacy/moved.md": MOVED,
        ".archive/adopter.txt": FOREIGN,
        ".aiqt/unrelated/private.bin": b"not OPF import state\n",
        projection: transaction,
        jr + "/frames.log": frames,
    }
    files.update({stage + "/" + name: raw for name, raw in stage_files.items()})
    files.update(archived)
    files.update(preimages)
    return files, rid, jr


def refusal_order_fixture():
    files, rid, _ = fixture_bytes()
    stage = imp.IMPORTS_REL + "/" + rid + "/"
    # Remove the staged run so merge conflicts cannot mask either bad binding.
    files = {rel: raw for rel, raw in files.items() if not rel.startswith(stage)}
    archive = imp.IMPORT_ARCHIVE_REL + "/" + rid
    files[archive + "/acceptance.json"] = b"corrupt acceptance\n"
    files[archive + "/sources/" + sha(BODY)] = b"corrupt source\n"
    return files, archive


def evidence_fixtures():
    """Malformed evidence with otherwise consistent outer bindings."""
    files, rid, jr = fixture_bytes()
    archive = imp.IMPORT_ARCHIVE_REL + "/" + rid
    stage = imp.IMPORTS_REL + "/" + rid
    frames_rel = jr + "/frames.log"
    raw = files[frames_rel]
    first_nl = raw.index(b"\n")
    size = int(raw[:first_nl].split(b" ")[2])
    intent = json.loads(raw[first_nl + 1:first_nl + 1 + size])
    complete = {"txn": intent["txn"]}
    moved_op = next(op for op in intent["ops"]
                    if op["op"] == "remove" and op["path"] == "legacy/moved.md")
    preimage = jr + "/preimages/" + moved_op["prestate"]["payload"]

    invalid, _, _ = fixture_bytes(invalid_acceptance=True)
    yield ("acceptance-invalid", invalid, "actor must be an object",
           stage + "/acceptance.json", "acceptance")

    absent = dict(files)
    del absent[stage + "/acceptance.json"]
    del absent[archive + "/acceptance.json"]
    yield ("acceptance-absent", absent, "missing, or unbound durable archive member",
           archive, None)

    bad_digest = dict(files)
    bad_digest[preimage] = b"X" + MOVED[1:]  # Same size; only the digest is wrong.
    yield ("preimage-digest", bad_digest, "preimage digest or size mismatch",
           preimage, "preimage")

    bad_size = dict(files)
    moved_op["prestate"]["size"] += 1  # Bytes and their digest remain correct.
    bad_size[frames_rel] = framed("INTENT", intent) + framed("COMPLETE", complete)
    moved_op["prestate"]["size"] -= 1
    yield ("preimage-size", bad_size, "preimage digest or size mismatch",
           preimage, "preimage")

    bad_txn = dict(files)
    bad_txn[frames_rel] = (framed("INTENT", intent)
                          + framed("COMPLETE", {"txn": "another-transaction"}))
    yield ("complete-transaction", bad_txn, "txn id disagrees", frames_rel, "terminal")

    bad_checksum = dict(files)
    terminal = framed("COMPLETE", complete)
    header, payload = terminal.split(b"\n", 1)
    parts = header.split(b" ")
    parts[3] = b"0" * 64 if parts[3] != b"0" * 64 else b"1" * 64
    bad_checksum[frames_rel] = framed("INTENT", intent) + b" ".join(parts) + b"\n" + payload
    yield ("complete-checksum", bad_checksum, "journal frame checksum mismatch",
           frames_rel, "checksum")


def evidence_plan(root, files):
    # Build the captured tree from fixture bytes, independently of _snapshot.
    tree = {}
    for rel, raw in files.items():
        for home in (imp.IMPORTS_REL, imp.IMPORT_ARCHIVE_REL,
                     imp.IMPORT_OPS_REL, ".archive"):
            if rel.startswith(home + "/"):
                tree[rel] = raw
                parent = rel.rsplit("/", 1)[0]
                while True:
                    tree[parent] = None
                    if parent == home:
                        break
                    parent = parent.rsplit("/", 1)[0]
                break
    resolution = store.resolve_store(
        root, accept_tokens=(store.STANDARD_TOKEN, store.PRIOR_STANDARD_TOKEN))
    return migrate.build_homes_plan(
        resolution, files[".working/toml/manifest.toml"], tree, now=NOW)


def assert_evidence_refused(root, files, needle, offending):
    initial = snapshot(root)
    result = cli(["upgrade", "--homes-plan", "--root", str(root)])
    assert result[0] == 2 and result[1] == "", result
    assert needle in result[2] and offending in result[2], result
    try:
        evidence_plan(root, files)
    except migrate.MigrationPlanError as exc:
        assert exc.path == offending and needle in exc.condition, str(exc)
    else:
        raise AssertionError("builder accepted malformed evidence")
    assert snapshot(root) == initial


def reverted_evidence_rule(rule):
    # Delete the precise production rule in memory, retaining the surrounding
    # planner. Exact anchors fail closed if production changes. This is a closed
    # set of discriminators, not exhaustive mutation coverage of the planner.
    anchors = {
        "acceptance": ("_acceptance",
                      '    _need(not findings, where,\n'
                      '          "ownership unprovable: {}".format("; ".join(findings)))\n'),
        "preimage": ("_journal_proof",
                     '            _need(len(raw) == pre["size"] and _sha(raw) == pre["sha256"],\n'
                     '                  rel + "/" + name, "preimage digest or size mismatch")\n'),
        "terminal": ("_frames", '        _journal._validate_terminal_agreement(frames)\n'),
        "checksum": ("_frames",
                     '        _need(_sha(body) == digest, where, "journal frame checksum mismatch")\n'),
    }
    name, anchor = anchors[rule]
    source = inspect.getsource(getattr(migrate, name))
    assert source.count(anchor) == 1, (rule, "production mutation anchor changed")
    indent = anchor[:len(anchor) - len(anchor.lstrip(" "))]
    namespace = vars(migrate).copy()
    exec(compile(source.replace(anchor, indent + "pass\n"),
                 "<reverted-" + rule + ">", "exec"), namespace)
    return patch.object(migrate, name, namespace[name])


def reverted_archive_order():
    name = "build_homes_plan"
    anchor = "            for suffix in sorted(expected):\n"
    source = inspect.getsource(getattr(migrate, name))
    assert source.count(anchor) == 1, ("archive-order", "production mutation anchor changed")
    namespace = vars(migrate).copy()
    exec(compile(source.replace(anchor, "            for suffix in expected:\n"),
                 "<reverted-archive-order>", "exec"), namespace)
    return patch.object(migrate, name, namespace[name])


def refusal_paths(root, *, reverted=False):
    initial = snapshot(root)
    _, archive = refusal_order_fixture()
    paths = []
    script = """
import contextlib
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import check_opf_homes_migrate as gate
assert gate.opf._bootstrap() == 0
files, _ = gate.refusal_order_fixture()
context = gate.reverted_archive_order() if sys.argv[3] == "reverted" else contextlib.nullcontext()
with context:
    try:
        gate.evidence_plan(Path(sys.argv[2]), files)
    except gate.migrate.MigrationPlanError as exc:
        print(json.dumps([exc.path, exc.condition]))
    else:
        raise AssertionError("builder accepted competing bad archive bindings")
"""
    # -I ignores PYTHONHASHSEED. Pair isolated launches with -s -P launches
    # under a closed environment so the explicit seeds really take effect.
    # These sampled seeds discriminate this mutation, not every refusal order.
    for seed in ("0", "1", "2", "3", "4", "5", "6", "7", "random"):
        for flags in (("-I", "-B"), ("-s", "-P", "-B")):
            proc = subprocess.run(
                [sys.executable, *flags, "-c", script,
                 str(Path(__file__).resolve().parent), str(root),
                 "reverted" if reverted else "original"],
                cwd=str(root.parent), capture_output=True, text=True, timeout=30,
                env={"PATH": os.defpath, "LC_ALL": "C", "TZ": "UTC",
                     "PYTHONHASHSEED": seed},
            )
            assert proc.returncode == 0 and proc.stderr == "", (
                seed, flags, proc.returncode, proc.stdout, proc.stderr)
            path, condition = json.loads(proc.stdout)
            assert condition == "archive bytes are not bound by the transaction and its INTENT", (
                seed, flags, path, condition)
            assert path in (archive + "/acceptance.json",
                            archive + "/sources/" + sha(BODY)), (seed, flags, path)
            paths.append(path)
    assert snapshot(root) == initial
    return paths


def assert_refusal_order(paths, expected):
    assert paths and all(path == expected for path in paths), (
        "refusal-deterministic-first-path", paths, expected)


def materialize(root, files):
    root.mkdir()
    for rel, raw in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def snapshot(root):
    """Independent inertness observation including directories and permissions."""
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            st = path.lstat()
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(st.st_mode):
                value = ("link", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                value = ("directory",)
            elif stat.S_ISREG(st.st_mode):
                value = ("file", path.read_bytes())
            else:
                value = ("special", stat.S_IFMT(st.st_mode))
            result[rel] = (stat.S_IMODE(st.st_mode), value)
    return result


def cli(args, entry=opf.main):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = entry(args)
    return code, stdout.getvalue(), stderr.getvalue()


def assert_happy(root, files, rid, output):
    code, stdout, stderr = output
    assert code == 0, (code, stdout, stderr)
    assert stderr == "", stderr
    doc = imp.tomllib.loads(stdout)
    assert doc["format"] == "opf.layout-migration.plan/v1"
    assert doc["schema"] == 1
    assert doc["source_homes"] == 1 and doc["target_homes"] == 2
    assert doc["target_spec_version"] == "2.0.0"
    assert doc["product_root"] == str(root)
    assert doc["store_root"] == str(root)
    assert doc["machine_rel"] == ".working/toml"
    assert doc["source_manifest_digest"] == digest(MANIFEST)
    assert stdout == emit.emit_checked(doc)

    expected = {}
    prefixes = (
        (imp.IMPORTS_REL + "/" + rid + "/",
         store.evidence_run("import", rid) + "/", "relocate"),
        (imp.IMPORT_ARCHIVE_REL + "/" + rid + "/",
         store.evidence_run("import", rid) + "/", "merge"),
        (imp.IMPORT_JOURNAL_REL + "/",
         store.journal_root("import") + "/", "transport"),
    )
    for source, raw in files.items():
        for old, new, operation in prefixes:
            if source.startswith(old):
                expected[source] = (new + source[len(old):], operation, digest(raw))
                break
    projection = imp.IMPORT_OPS_REL + "/" + rid + "/transaction.toml"
    expected[projection] = (
        store.txn_record("import", rid), "transport", digest(files[projection]))
    expected[".archive/legacy/moved.md"] = (
        store.moved_dest("legacy/moved.md"), "relocate", digest(MOVED))
    rows = doc["entry"]
    assert len(rows) == len({row["source_rel"] for row in rows})
    actual = {row["source_rel"]: (row["dest_rel"], row["op"], row["digest"])
              for row in rows}
    assert actual == expected, (actual, expected)
    for row in rows:
        assert row["run_id"] == rid
        if row["op"] == "transport":
            assert row["old_path"] == row["source_rel"]
            assert row["new_path"] == row["dest_rel"]
            assert row["new_digest"] == row["old_digest"] == row["digest"]
    assert {row["source_rel"] for row in doc["left_in_place"]} == {
        ".archive/adopter.txt"}
    assert doc["left_in_place"][0]["digest"] == digest(FOREIGN)
    assert any(row.get("identical_destination") is True for row in rows)
    assert not any(".aiqt/unrelated" in row["source_rel"] for row in rows)


def original_upgrade_parser(rest):
    """Frozen pre-change parser behaviour from opf.py:1606-1636.

    It calls the existing schema engine, providing a behavioural comparison at
    the changed dispatch seam without running a second migration implementation.
    """
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf upgrade: --root requires a directory argument", file=sys.stderr)
                return opf.EXIT_MALFORMED
            if root is not None:
                print("opf upgrade: --root given more than once", file=sys.stderr)
                return opf.EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf upgrade: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return opf.EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf upgrade: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return opf.EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        return opf._upgrade_run(root)
    except opf._UpgradeError as exc:
        print("opf upgrade: refused: {}; exit 2".format(exc), file=sys.stderr)
        return opf.EXIT_MALFORMED
    except Exception as exc:
        print("opf upgrade: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return opf.EXIT_MALFORMED


def self_test(red_on_revert=False):
    base = Path(tempfile.mkdtemp(prefix="opf-homes-migrate-")).resolve()
    ran, failures = [], []

    def check(name, test):
        ran.append(name)
        try:
            test()
        except AssertionError as exc:
            failures.append("{}: {}".format(name, exc))

    try:
        assert opf._bootstrap() == 0
        files, rid, jr = fixture_bytes()
        root = base / "happy"
        materialize(root, files)
        args = ["upgrade", "--homes-plan", "--root", str(root)]
        before = snapshot(root)

        # Every forbidden mutation seam is armed during the happy-path call.
        def forbidden(*_args, **_kwargs):
            raise AssertionError("homes-plan entered a mutation or schema-upgrade seam")

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(migrate, "_clock_now", return_value=NOW))
            for owner, name in (
                (opf, "_upgrade_run"), (opf, "_upgrade_acquire_lease"),
                (opf, "_upgrade_replace"), (journal, "apply_ops"),
                (journal, "run_transaction"), (journal, "recover"),
            ):
                stack.enter_context(patch.object(owner, name, side_effect=forbidden))
            first = cli(args)
            second = cli(args)

        check("happy-exact-roster-digests-destinations-a13",
              lambda: assert_happy(root, files, rid, first))

        def deterministic():
            assert first == second
            assert imp.tomllib.loads(first[1])["generated_at"] == "2026-01-02T03:04:05Z"
        check("deterministic-with-fixed-clock", deterministic)

        order_root = base / "refusal-order"
        order_files, order_archive = refusal_order_fixture()
        materialize(order_root, order_files)
        # Lexical order puts acceptance.json before sources/<digest>; the
        # oracle is fixed here, never obtained from the planner's iteration.
        first_refusal = order_archive + "/acceptance.json"
        check("refusal-deterministic-first-path",
              lambda: assert_refusal_order(refusal_paths(order_root), first_refusal))

        def inert():
            assert snapshot(root) == before
        check("inert-tree-and-mutation-seams", inert)

        # Exercise the isolated executable as well as the in-process dispatcher.
        def executable():
            proc = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(opf.__file__).resolve()),
                 "upgrade", "--homes-plan", "--root", str(root)],
                cwd=str(base), capture_output=True, text=True, timeout=30,
                env={"PATH": os.defpath, "LC_ALL": "C", "TZ": "UTC"},
            )
            assert_happy(root, files, rid, (proc.returncode, proc.stdout, proc.stderr))
            assert snapshot(root) == before
        check("isolated-cli", executable)

        def refusal(name, mutate, needle, offending):
            case = base / name
            materialize(case, files)
            mutate(case)
            initial = snapshot(case)
            result = cli(["upgrade", "--homes-plan", "--root", str(case)])
            assert result[0] == 2 and result[1] == "", result
            assert needle in result[2] and offending in result[2], result
            assert snapshot(case) == initial

        stage = imp.IMPORTS_REL + "/" + rid
        body_rel = stage + "/sources/" + sha(BODY)

        def symlink(case):
            path = case / body_rel
            path.unlink()
            path.symlink_to(case / ".archive/adopter.txt")
        check("symlink-member", lambda: refusal(
            "symlink", symlink, "symlink or wrong type", body_rel))

        check("unknown-staged-member", lambda: refusal(
            "unknown", lambda case: (case / stage / "foreign.bin").write_bytes(b"x"),
            "unknown member", stage + "/foreign.bin"))

        archive_acceptance = imp.IMPORT_ARCHIVE_REL + "/" + rid + "/acceptance.json"
        check("merge-conflict", lambda: refusal(
            "conflict", lambda case: (case / archive_acceptance).write_bytes(b"changed"),
            "merge digest conflict", archive_acceptance))

        unknown_ops = imp.IMPORT_OPS_REL + "/foreign.bin"
        check("unprovable-import-member", lambda: refusal(
            "foreign-ops", lambda case: (case / unknown_ops).write_bytes(b"x"),
            "wrong type", unknown_ops))

        def oversized(case):
            path = case / body_rel
            with path.open("wb") as stream:
                stream.truncate(journal._MAX_PRODUCT_READ_BYTES + 1)
        check("oversized-member", lambda: refusal(
            "oversized", oversized, "cap", body_rel))

        def unlistable():
            target = os.stat(root / imp.IMPORT_ARCHIVE_REL)
            real_scandir = os.scandir

            def denied(fd):
                if isinstance(fd, int):
                    st = os.fstat(fd)
                    if (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino):
                        raise PermissionError("injected EACCES")
                return real_scandir(fd)

            with patch.object(os, "scandir", side_effect=denied):
                result = cli(args)
            assert result[0] == 2 and result[1] == "", result
            assert "cannot open or list" in result[2], result
            assert imp.IMPORT_ARCHIVE_REL in result[2], result
            assert snapshot(root) == before
        check("unlistable-home-even-under-root", unlistable)

        def relocated():
            product = base / "product"
            product.mkdir()
            (product / store.POINTER_REL).write_bytes(toml({
                "store": {"target": "dir:" + str(root)},
            }))
            result = cli(["upgrade", "--homes-plan", "--root", str(product)])
            assert result[0] == 2 and result[1] == "", result
            assert "non-co-located" in result[2] and str(root) in result[2], result
        check("resolved-external-store", relocated)

        empty = base / "not-adopted"
        empty.mkdir()

        def not_adopted():
            result = cli(["upgrade", "--homes-plan", "--root", str(empty)])
            assert result[0] == 0 and result[2] == "", result
            assert "NOT APPLICABLE" in result[1], result
            assert snapshot(empty) == {}
        check("not-applicable", not_adopted)

        def already():
            case = base / "already"
            manifest = MANIFEST.replace(
                b'spec_version = "1.2.0"',
                b'spec_version = "2.0.0"\nhomes = 2')
            materialize(case, {".working/toml/manifest.toml": manifest})
            initial = snapshot(case)
            result = cli(["upgrade", "--homes-plan", "--root", str(case)])
            assert evidence_plan(case, {".working/toml/manifest.toml": manifest}) is None
            assert result == (0, "opf upgrade: already at homes 2; nothing to plan\n", ""), result
            assert snapshot(case) == initial
        check("already-homes-two", already)

        def unexecuted_move():
            case = base / "unexecuted"
            altered = dict(files)
            # A real archive file plus an unrelated matching source body is not
            # ownership proof. Remove the recorded Move from the completed INTENT,
            # while retaining a valid ordinary import transaction and archive.
            raw = altered[jr + "/frames.log"]
            first_nl = raw.index(b"\n")
            size = int(raw[:first_nl].split(b" ")[2])
            intent = json.loads(raw[first_nl + 1:first_nl + 1 + size])
            intent["ops"] = intent["ops"][:3]
            altered[jr + "/frames.log"] = (
                framed("INTENT", intent)
                + framed("COMPLETE", {"txn": intent["txn"]}))
            for name in list(altered):
                if name.startswith(jr + "/preimages/"):
                    del altered[name]
            materialize(case, altered)
            result = cli(["upgrade", "--homes-plan", "--root", str(case)])
            assert result[0] == 0 and result[2] == "", result
            doc = imp.tomllib.loads(result[1])
            assert ".archive/legacy/moved.md" in {
                row["source_rel"] for row in doc["left_in_place"]}
            assert ".archive/legacy/moved.md" not in {
                row["source_rel"] for row in doc["entry"]}
        check("archive-bytes-without-execution-proof-stay", unexecuted_move)

        def parser():
            for rest in (
                ["--homes-plan", "--homes-plan"],
                ["--homes-plan", "--root"],
                ["--homes-plan", "--root", ""],
                ["--homes-plan", "--root", str(root), "--root", str(root)],
                ["--homes-plan", "--unknown"],
                ["--homes-plan", "--homes-apply"],
            ):
                result = cli(["upgrade"] + rest)
                assert result[0] == 2 and result[1] == "", result
        check("argument-refusals", parser)

        def schema_compatibility():
            for rest in (
                ["--root", str(empty)],
                ["--root"],
                ["--root", ""],
                ["--root", str(empty), "--root", str(empty)],
                ["--unknown"],
            ):
                expected = cli(rest, entry=original_upgrade_parser)
                with patch.object(migrate, "plan_homes_migration", side_effect=forbidden):
                    actual = cli(["upgrade"] + rest)
                assert actual == expected, (rest, actual, expected)
            assert snapshot(empty) == {}
        check("schema-dispatch-compatibility", schema_compatibility)

        def other_read_only_verbs():
            with patch.object(migrate, "plan_homes_migration", side_effect=forbidden):
                for argv in (
                    ["doctor", "--root", str(empty)],
                    ["render", "--check", "--root", str(empty)],
                ):
                    result = cli(argv)
                    assert result[0] == 0 and "NOT APPLICABLE" in result[1], result
            assert snapshot(empty) == {}
        check("other-read-only-verbs-do-not-enter-planner", other_read_only_verbs)

        evidence_cases = list(evidence_fixtures())
        for name, altered, needle, offending, rule in evidence_cases:
            case = base / name
            materialize(case, altered)
            check(name, lambda case=case, altered=altered, needle=needle,
                  offending=offending: assert_evidence_refused(
                      case, altered, needle, offending))

        if red_on_revert:
            def discriminate_archive_order():
                assert_refusal_order(refusal_paths(order_root), first_refusal)
                paths = refusal_paths(order_root, reverted=True)
                try:
                    assert_refusal_order(paths, first_refusal)
                except AssertionError as exc:
                    assert exc.args[0][0] == "refusal-deterministic-first-path", exc
                else:
                    raise AssertionError("archive-order removal survived its discriminator")
                assert_refusal_order(refusal_paths(order_root), first_refusal)
            check("red-on-revert-refusal-deterministic-first-path",
                  discriminate_archive_order)

            def discriminate_evidence(name, altered, needle, offending, rule):
                case = base / name
                initial = snapshot(case)
                assert_evidence_refused(case, altered, needle, offending)
                with reverted_evidence_rule(rule):
                    # Require actual acceptance, so an unrelated exception or
                    # refusal cannot masquerade as a killed mutation.
                    mutant = cli(["upgrade", "--homes-plan", "--root", str(case)])
                    assert mutant[0] == 0 and mutant[2] == "", mutant
                    assert imp.tomllib.loads(mutant[1])["format"] == migrate.PLAN_FORMAT
                    assert evidence_plan(case, altered)["format"] == migrate.PLAN_FORMAT
                    try:
                        assert_evidence_refused(case, altered, needle, offending)
                    except AssertionError:
                        pass
                    else:
                        raise AssertionError(rule + " removal survived its discriminator")
                assert_evidence_refused(case, altered, needle, offending)
                assert snapshot(case) == initial

            for evidence_case in evidence_cases:
                if evidence_case[-1] is not None:
                    check("red-on-revert-" + evidence_case[0],
                          lambda evidence_case=evidence_case:
                          discriminate_evidence(*evidence_case))

            def discriminate():
                with patch.object(migrate, "_clock_now", return_value=NOW):
                    baseline = cli(args)
                    assert_happy(root, files, rid, baseline)
                    with patch.object(migrate, "build_homes_plan", return_value=None):
                        mutant = cli(args)
                    red = False
                    try:
                        assert_happy(root, files, rid, mutant)
                    except (AssertionError, ValueError):
                        red = True
                    assert red, "stubbed builder survived the happy-path assertion"
                    restored = cli(args)
                    assert_happy(root, files, rid, restored)
                    assert restored == baseline
                assert snapshot(root) == before
            check("red-on-revert-builder-stub-and-restoration", discriminate)

    except Exception as exc:
        print("OPF-HOMES-MIGRATE SELF-TEST ERROR: {!r}".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(base)

    print("OPF-HOMES-MIGRATE SELF-TEST: exercised " + ", ".join(ran))
    if failures:
        for failure in failures:
            print("FAIL: " + failure, file=sys.stderr)
        return 1
    print("OPF-HOMES-MIGRATE SELF-TEST PASSED")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["--self-test"], ["--self-test", "--red-on-revert"]):
        print("check_opf_homes_migrate: expected --self-test [--red-on-revert]",
              file=sys.stderr)
        return 2
    return self_test(red_on_revert="--red-on-revert" in args)


if __name__ == "__main__":
    sys.exit(main())
