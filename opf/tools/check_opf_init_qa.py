#!/usr/bin/env python3
"""PR3a library QA regressions; also run by _opf_init_operation.py --self-test.

The shipped opf init CLI remains unchanged. Coupled CLI activation belongs to PR7.
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_emit
import _opf_init_operation as op


def _backlog_items(numbers):
    return _opf_emit.emit_checked({"schema": 1, "record": [{
        "id": "BI-{}".format(n),
        "type": "backlog_item",
        "status": "open",
        "title": "later",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
        "actor": {"kind": "maintainer"},
    } for n in numbers]}).encode("utf-8")


def _worklog(numbers):
    return _opf_emit.emit_checked({"schema": 1, "entry": [{
        "id": "WL-{}".format(n),
        "date": "2026-06-01T00:00:00Z",
        "actor": {"kind": "maintainer"},
        "kind": "added",
        "summary": "w{}".format(n),
    } for n in numbers]}).encode("utf-8")


class InitQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opf-init-qa-")
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.env = op._st_git_env(self.base)
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.root = op._plain_repo(os.path.join(self.base, "repo"), self.env)

    def ready(self):
        result = op.init_operation(self.root)
        self.assertEqual(result.status, op.SOURCES_READY, result.primary_failure)
        return result

    def readopt(self, **high):
        # B6: a committed store later retired in a commit; re-adoption seeds the counters from
        # the pinned snapshot and publishes EMPTY indexes (no historical record is restored).
        baseline, importer, _ = op._roster_namespaces()
        values = {ns: 0 for ns in baseline | importer}
        values.update(high)
        op._write(str(Path(self.root, op.COUNTERS_RELPATH)), op._counters_toml(values))
        op._git(["add", "--", op.COUNTERS_RELPATH], self.root, self.env)
        op._git(["commit", "-q", "-m", "seed"], self.root, self.env)
        seed = op._head(self.root, self.env)
        op._git(["rm", "--", op.COUNTERS_RELPATH], self.root, self.env)
        op._git(["commit", "-q", "-m", "retire store"], self.root, self.env)
        result = op.init_operation(self.root, ancestral=seed)
        self.assertEqual(result.status, op.SOURCES_READY, result.primary_failure)
        return result

    def assert_rerun(self, status, check=None):
        before = op._snapshot_all(self.root)
        result = op.init_operation(self.root)
        self.assertEqual(result.status, status, result.primary_failure)
        if check is not None:
            self.assertIn(check, result.primary_failure["detail"])
        self.assertEqual(op._snapshot_all(self.root), before)
        return result

    def test_library_journal_atomic_publication_and_rerun(self):
        # Every source must pass through link with the COMPLETE recorded payload.
        linked = []
        real_link = os.link

        def link(src, dst, **kw):
            if op._STAGE_MARKER in str(src):
                _, raw = op._read_plan(self.root)
                plan = op.validate_init_plan(raw)
                entry = next(s for s in plan["sets"]["S"] if s["staging"] == src)
                fd = os.open(src, os.O_RDONLY, dir_fd=kw["src_dir_fd"])
                try:
                    self.assertEqual(
                        os.read(fd, entry["size"] + 1),
                        op.plan_payloads(plan)[entry["path"]],
                    )
                finally:
                    os.close(fd)
                self.assertFalse(
                    os.path.lexists(os.path.join(self.root, entry["path"]))
                )
                linked.append(entry["path"])
            return real_link(src, dst, **kw)

        before_index = op._snapshot_all(self.root)[1]
        with mock.patch.object(os, "link", side_effect=link):
            first = self.ready()
        _, raw = op._read_plan(self.root)
        self.assertEqual(
            sorted(linked), sorted(op.plan_payloads(op.validate_init_plan(raw)))
        )
        self.assertEqual(op._snapshot_all(self.root)[1], before_index)
        before = op._snapshot_all(self.root)
        result = op.init_operation(self.root)
        self.assertEqual(
            result.status, op.ALREADY_INITIALIZED, result.primary_failure
        )
        self.assertEqual(result.operation_id, first.operation_id)
        self.assertEqual(result.plan_digest, first.plan_digest)
        self.assertEqual(op._snapshot_all(self.root), before)

    def test_library_resume_original_operation(self):
        real_publish = op._stage_and_publish
        calls = []

        def interrupt(*args):
            calls.append(args[2]["path"])
            if len(calls) == 2:
                raise op.InitOperationError("injected interruption", op.FAILED)
            return real_publish(*args)

        with mock.patch.object(op, "_stage_and_publish", side_effect=interrupt):
            interrupted = op.init_operation(self.root)
        self.assertEqual(
            interrupted.status, op.FAILED, interrupted.primary_failure
        )
        self.assertIn(
            "injected interruption", interrupted.primary_failure["detail"]
        )
        ops, raw = op._read_plan(self.root)
        plan = op.validate_init_plan(raw)
        self.assertEqual(interrupted.operation_id, plan["operation_id"])
        result = op.init_operation(self.root, recover=True)
        self.assertEqual(result.status, op.SOURCES_READY, result.primary_failure)
        self.assertEqual(result.operation_id, plan["operation_id"])
        self.assertEqual(result.plan_digest, plan["plan_digest"])
        self.assertEqual(op._read_plan(self.root), (ops, raw))

    def test_library_pinned_b6_seed(self):
        self.readopt(BI=7)
        path = Path(self.root, op.COUNTERS_RELPATH)
        self.assertEqual(
            tomllib.loads(path.read_text(encoding="utf-8"))["counters"]["BI"], 7
        )

    def test_completed_b6_seeded_rerun(self):
        # R2 HIGH: ids at or below the pinned ancestral high-water were allocated before the
        # store was retired and are not restored, so their absence is not a deletion.
        first = self.readopt(BI=7, WL=7)
        result = self.assert_rerun(op.ALREADY_INITIALIZED)
        self.assertEqual(result.operation_id, first.operation_id)
        # An id allocated ABOVE the seed and then removed is still a deletion.
        counters = Path(self.root, op.COUNTERS_RELPATH)
        index = Path(self.root, op._MACHINE_HOME, "backlog_item.index.toml")
        seeded, empty = counters.read_bytes(), index.read_bytes()
        vectors = [
            (8, [8], op.ALREADY_INITIALIZED),   # a valid later allocation
            (9, [9], op.REFUSED),               # BI-8 removed from below the max
            (9, [8], op.REFUSED),               # BI-9 removed from above the max
        ]
        for high, ids, status in vectors:
            with self.subTest(high=high, ids=ids):
                counters.write_bytes(seeded.replace(b"BI = 7", b"BI = %d" % high))
                index.write_bytes(_backlog_items(ids))
                try:
                    self.assert_rerun(status, None if status == op.ALREADY_INITIALIZED
                                      else "C-NO-DELETION")
                finally:
                    counters.write_bytes(seeded)
                    index.write_bytes(empty)
        self.assert_rerun(op.ALREADY_INITIALIZED)

    def test_completed_deletion_refused(self):
        # C-NO-DELETION is a current-source check (counters against present ids, no history):
        # a completed first adoption with BI-2 removed from below the max stays REFUSED.
        self.ready()
        counters = Path(self.root, op.COUNTERS_RELPATH)
        counters.write_bytes(counters.read_bytes().replace(b"BI = 0", b"BI = 3"))
        Path(self.root, op._MACHINE_HOME, "backlog_item.index.toml").write_bytes(
            _backlog_items([1, 3]))
        self.assert_rerun(op.REFUSED, "C-NO-DELETION")

    def test_malformed_ancestral_floor_cannot_evaluate(self):
        self.ready()
        res = op._opf_store.resolve_store(self.root)
        # R3: a floor past the signed 64-bit counter range is malformed too, never a wider pass.
        for floor in ({"BI": -1}, {"BI": True}, {"BI": "7"}, ["BI"], {"BI": 1 << 63},
                      {"BI": 10 ** 100}):
            with self.subTest(floor=floor):
                health = op._opf_check.validate_store(res, ancestral_floor=floor)
                self.assertEqual(health.checks["C-NO-DELETION"], "CANNOT-EVALUATE")

    def test_max_ancestral_floor_is_accepted(self):
        # Positive boundary discriminator: the exact signed 64-bit maximum is a VALID floor value,
        # not malformed, so C-NO-DELETION evaluates rather than routing to CANNOT-EVALUATE. Guards
        # against an off-by-one in the `0 <= v <= (1 << 63) - 1` range that would wrongly reject it.
        self.ready()
        res = op._opf_store.resolve_store(self.root)
        for floor in ({"BI": (1 << 63) - 1}, {"WL": (1 << 63) - 1}):
            with self.subTest(floor=floor):
                health = op._opf_check.validate_store(res, ancestral_floor=floor)
                self.assertNotEqual(health.checks["C-NO-DELETION"], "CANNOT-EVALUATE")

    def test_worklog_gap_above_floor_is_a_deletion(self):
        # R3 HIGH: C-NO-DELETION runs the floor-aware gap scan for WL as for every other
        # namespace, so a worklog id deleted from between the floor and the max is its finding
        # (C-CONTIGUITY knows no ancestral floor). ids at or below the floor stay unowed.
        self.ready()
        counters = Path(self.root, op.COUNTERS_RELPATH)
        worklog = Path(self.root, op._MACHINE_HOME, "worklog.toml")
        seeded, empty = counters.read_bytes(), worklog.read_bytes()
        self.assertIn(b"WL = 0", seeded)
        vectors = [
            ([9], "FINDING"),        # WL-8 removed from above the floor, below the max
            ([8, 9], "PASS"),        # a valid sequence above the floor
        ]
        for ids, status in vectors:
            with self.subTest(ids=ids):
                counters.write_bytes(seeded.replace(b"WL = 0", b"WL = 9"))
                worklog.write_bytes(_worklog(ids))
                try:
                    res = op._opf_store.resolve_store(self.root)
                    health = op._opf_check.validate_store(res, ancestral_floor={"WL": 7})
                    self.assertEqual(health.checks["C-NO-DELETION"], status,
                                     health.by_check.get("C-NO-DELETION"))
                finally:
                    counters.write_bytes(seeded)
                    worklog.write_bytes(empty)

    def test_completed_provenance_bound_to_plan(self):
        self.ready()
        path = Path(self.root, op.PROVENANCE_RELPATH)
        original = path.read_bytes()
        model = tomllib.loads(original.decode("utf-8"))
        mutations = {
            # R1 #3: grammar-valid, but not the digest of the plan's payloads.
            "zero-digest": lambda m: m.update(source_digest="sha256:" + "0" * 64),
            "foreign-root": lambda m: m["binding"]["product_root"].update(
                path="/foreign"
            ),
            "source-set": lambda m: m["source_set"].pop(),
            "head": lambda m: m["head"].update(oid="0" * 40),
            "inventory": lambda m: m.update(inventory_digest="sha256:" + "0" * 64),
            "acceptance": lambda m: m.update(acceptance={"present": True}),
            "adoption": lambda m: m.update(first_adoption=False),
            "spec": lambda m: m.update(spec_version="1.1.0"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                altered = copy.deepcopy(model)
                mutate(altered)
                path.write_text(_opf_emit.emit_checked(altered), encoding="utf-8")
                try:
                    before = op._snapshot_all(self.root)
                    result = op.init_operation(self.root)
                    self.assertEqual(
                        result.status, op.REFUSED, result.primary_failure
                    )
                    self.assertIn("provenance", result.primary_failure["detail"])
                    self.assertEqual(op._snapshot_all(self.root), before)
                finally:
                    path.write_bytes(original)
        before = op._snapshot_all(self.root)
        result = op.init_operation(self.root)
        self.assertEqual(
            result.status, op.ALREADY_INITIALIZED, result.primary_failure
        )
        self.assertEqual(op._snapshot_all(self.root), before)

    def test_completed_current_source_health(self):
        self.ready()
        record = {
            "id": "DN-1",
            "type": "done",
            "status": "recorded",
            "title": "test",
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "actor": {"kind": "maintainer"},
            "links": [{"rel": "receipt_of", "id": "BI-1"}],
        }
        vectors = [
            # R1 #4: the active worklog must be parsed on a completed rerun.
            ("worklog.toml", b"= invalid TOML", "C-RECORDS"),
            ("version.toml", b"= invalid TOML", "C-VERSION-LEDGER"),
            ("backlog_item.index.toml", b"= invalid TOML", "C-RECORDS"),
            ("stray.toml", b"schema = 1\n", "C-CONTAINMENT"),
            (
                "done.index.toml",
                _opf_emit.emit_checked({"schema": 1, "record": [record]}).encode(
                    "utf-8"
                ),
                "C-LINKS",
            ),
        ]
        for name, data, check in vectors:
            with self.subTest(name=name):
                path = Path(self.root, op._MACHINE_HOME, name)
                original = path.read_bytes() if path.exists() else None
                path.write_bytes(data)
                try:
                    before = op._snapshot_all(self.root)
                    result = op.init_operation(self.root)
                    self.assertEqual(
                        result.status, op.REFUSED, result.primary_failure
                    )
                    self.assertIn(check, result.primary_failure["detail"])
                    self.assertEqual(op._snapshot_all(self.root), before)
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)

        # A valid later edit and changed HEAD preserve bootstrap provenance.
        path = Path(self.root, op.COUNTERS_RELPATH)
        path.write_bytes(path.read_bytes().replace(b"BI = 0", b"BI = 1"))
        record = {
            "id": "BI-1",
            "type": "backlog_item",
            "status": "open",
            "title": "later",
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "actor": {"kind": "maintainer"},
        }
        Path(self.root, op._MACHINE_HOME, "backlog_item.index.toml").write_text(
            _opf_emit.emit_checked({"schema": 1, "record": [record]}),
            encoding="utf-8",
        )
        op._git(
            ["add", "--", ".working", ".opf.toml", "CHANGELOG.md"],
            self.root,
            self.env,
        )
        op._git(["commit", "-q", "-m", "valid later edit"], self.root, self.env)
        before = op._snapshot_all(self.root)
        result = op.init_operation(self.root)
        self.assertEqual(
            result.status, op.ALREADY_INITIALIZED, result.primary_failure
        )
        self.assertEqual(op._snapshot_all(self.root), before)


def self_test():
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(InitQA)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
