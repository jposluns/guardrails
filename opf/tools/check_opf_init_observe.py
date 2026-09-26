#!/usr/bin/env python3
"""PR4a raw-reader tests. No production admission coverage is claimed here.

The fixtures are in-memory object databases. Reversions load separate module
instances and separate fixtures. No working tree, index, ref or substrate is
created. Filesystem/operation fixture families remain separate PR4 obligations.
The harness accepts source text without a reconciled repository review target;
reversal reports identify the measured candidate content digest only.
"""
import argparse
import hashlib
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
ROOTS = (".opf.toml", ".opf.local.toml", ".working")


def load_candidate(source, name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, name, "exec"), module.__dict__)
    except BaseException:
        del sys.modules[name]
        raise
    return module


class Objects:
    def __init__(self, module):
        self.module, self.data = module, {}

    def put(self, kind, raw):
        oid = hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self.data[(kind, oid)] = raw
        return oid

    def tree(self, files):
        groups = {}
        for path, body in files.items():
            name, sep, tail = path.partition("/")
            if sep:
                groups.setdefault(name, {})[tail] = body
            else:
                groups[name] = body
        raw = bytearray()
        for name, body in sorted(groups.items()):
            if isinstance(body, dict):
                mode, oid = b"40000", self.tree(body)
            else:
                mode, oid = b"100644", self.put("blob", body)
            raw.extend(mode + b" " + name.encode() + b"\0" + bytes.fromhex(oid))
        return self.put("tree", bytes(raw))

    def commit(self, files, parents=(), stamp=1):
        tree = self.tree(files)
        raw = b"tree " + tree.encode() + b"\n"
        raw += b"".join(b"parent " + p.encode() + b"\n" for p in parents)
        identity = b"fixture <fixture@example.invalid> " + str(stamp).encode() + b" +0000\n"
        raw += b"author " + identity + b"committer " + identity + b"\nfixture\n"
        return self.put("commit", raw)

    def read(self, kind, oid, budget):
        try:
            return self.data[(kind, oid)]
        except KeyError:
            raise self.module.ObservationError(self.module.CANNOT_EVALUATE, oid,
                                               "required fixture object missing")

    def history(self, **limits):
        return self.module.RawHistory(self.read, object_format="sha1",
                                       budget=self.module.Budget(self.module.Limits(**limits)))


def check(value, identity):
    if not value:
        raise AssertionError(identity)


def refuses(module, fn, code, identity):
    try:
        fn()
    except module.ObservationError as exc:
        check(exc.code == code, identity + ":wrong-verdict")
    else:
        raise AssertionError(identity)


def absence(module):
    db = Objects(module)
    root = db.commit({})
    side = db.commit({".working/toml/counters.toml": b"fragment"})
    removed = db.commit({}, (side,))
    head = db.commit({}, (root, removed))
    proof = db.history().prove_absence(head, prefix="", roots=ROOTS)
    check(proof["complete"] and not proof["absent"] and side in proof["present"],
          "all-parent-orphan-presence")


def no_history(module):
    db = Objects(module)
    root = db.commit({})
    head = db.commit({"unrelated": b"data"}, (root,))
    proof = db.history().prove_absence(head, prefix="", roots=ROOTS)
    check(proof["absent"] and proof["reachable"] == tuple(sorted((root, head))),
          "genuine-root-absence")


def latest(module):
    db = Objects(module)
    a = db.commit({".working/toml/counters.toml": b"WL = 3"}, stamp=9999)
    b = db.commit({".working/toml/counters.toml": b"WL = 5"}, (a,), stamp=1)
    e = db.commit({".working/toml/counters.toml": b"WL = 7"}, (b,), stamp=2)
    d = db.commit({}, (e,), stamp=0)
    h = db.commit({}, (d,), stamp=10000)
    selected = db.history().latest_deletion(h, prefix="", roots=ROOTS)
    check(selected == {"seed_commit": e, "deletion_commit": d, "absent_chain": (h, d)},
          "nearest-present-first-parent")


def merge_parent(module):
    db = Objects(module)
    e = db.commit({".working/left": b"left"})
    other = db.commit({".working/right": b"right"})
    d = db.commit({}, (e, other))
    check(db.history().first_parent(d)[1] == e, "merge-first-parent")
    selected = db.history().latest_deletion(d, prefix="", roots=ROOTS)
    check(selected["seed_commit"] == e, "merge-first-parent")


def side_activity(module):
    db = Objects(module)
    e = db.commit({".working/first": b"first"})
    d = db.commit({}, (e,))
    side = db.commit({".working/new": b"new"}, (d,))
    gone = db.commit({}, (side,))
    h = db.commit({}, (d, gone))
    activity = db.history().side_activity(h, e, prefix="", roots=ROOTS)
    check(side in activity, "side-line-allocation-visible")


def missing_parent(module):
    db = Objects(module)
    h = db.commit({}, ("f" * 40,))
    refuses(module, lambda: db.history().reachable(h), module.CANNOT_EVALUATE,
            "missing-parent-not-root")


def object_identity(module):
    db = Objects(module)
    h = db.commit({})
    original = db.data[("commit", h)]
    db.data[("commit", h)] = original + b"substitution"
    refuses(module, lambda: db.history().commit(h), module.CANNOT_EVALUATE,
            "exact-object-identity")


def framing(module):
    refuses(module, lambda: module.parse_tree(b"100644 name\0short", "sha1"),
            module.CANNOT_EVALUATE, "tree-framing")


def duplicates(module):
    row = b"100644 name\0" + b"x" * 20
    refuses(module, lambda: module.parse_tree(row + row, "sha1"),
            module.CANNOT_EVALUATE, "tree-duplicate")


def commit_cap(module):
    db = Objects(module)
    a = db.commit({})
    b = db.commit({}, (a,))
    refuses(module, lambda: db.history(commits=1).reachable(b), module.CANNOT_EVALUATE,
            "commit-cap")


def byte_cap(module):
    db = Objects(module)
    a = db.commit({})
    refuses(module, lambda: db.history(aggregate_bytes=1).commit(a), module.CANNOT_EVALUATE,
            "aggregate-byte-cap")


def time_cap(module):
    clock = [0.0]
    budget = module.Budget(module.Limits(), clock=lambda: clock[0])
    clock[0] = 61.0
    refuses(module, budget.tick, module.CANNOT_EVALUATE, "elapsed-time-cap")


def invalid_limit(module, field, value, identity):
    refuses(module, lambda: module.Budget(module.Limits(**{field: value})),
            module.CANNOT_EVALUATE, identity)


def seed_fixture(module):
    db = Objects(module)
    path = ".working/toml/counters.toml"
    a = db.commit({path: b"schema = 1\n[counters]\nWL = 1\n"})
    side = db.commit({path: b"schema = 1\n[counters]\nWL = 9\n"}, (a,))
    main = db.commit({}, (a,))
    h = db.commit({}, (main, side))

    class Adapter:
        def __init__(self, git, root):
            pass

        def run(self, args, budget):
            # GitObjects.run returns stdout on success and raises on a nonzero
            # cat-file -e outcome. Unexpected commands are fixture errors.
            check(len(args) == 3 and args[:2] == ["cat-file", "-e"],
                  "fixture-evidence-lookup")
            budget.tick()
            if any(oid == args[2] for _kind, oid in db.data):
                return b""
            raise module.ObservationError(
                module.CANNOT_EVALUATE, "git cat-file",
                "required evidence does not resolve: fixture object missing")

        def graft_snapshot(self, budget):
            return ("fixture", "absent")

        def __call__(self, kind, oid, budget):
            return db.read(kind, oid, budget)

    module.GitObjects = Adapter
    return side, h


def seed_membership(module):
    side, h = seed_fixture(module)
    refuses(module, lambda: module.read_seed_basis(
        "/fixture", git="/fixture/git", pinned_head=h, evidence_commit=side,
        prefix="", object_format="sha1"), module.REFUSED, "seed-first-parent-membership")



def absent_evidence(module):
    _side, h = seed_fixture(module)
    identity = "seed-absent-evidence"
    try:
        module.read_seed_basis(
            "/fixture", git="/fixture/git", pinned_head=h, evidence_commit="0" * 40,
            prefix="", object_format="sha1")
    except module.ObservationError as exc:
        check(exc.code == module.CANNOT_EVALUATE
              and "does not resolve" in exc.detail, identity)
    else:
        raise AssertionError(identity)


def scope_omission(module):
    db = Objects(module)
    h = db.commit({".working/entry": b"present"})
    refuses(module, lambda: db.history().prove_absence(h, prefix="", roots=()),
            module.CANNOT_EVALUATE, "scope-omission")


def tree_entry_cap(module):
    db = Objects(module)
    h = db.commit({"one": b"1", "two": b"2"})
    refuses(module, lambda: db.history(entries=1).at(h, "one"),
            module.CANNOT_EVALUATE, "tree-entry-cap")


# Each case owns a deliberate reversal and a reached assertion identity. No syntax
# error, unexpected exception, timeout or failed fixture setup is accepted as RED.
CASES = [
    ("scope-omission", scope_omission,
     "if type(roots) is not tuple or roots != required:", "if False:"),
    ("tree-entry-cap", tree_entry_cap,
     'self.budget.tick("entries", len(entries))', "pass"),
    ("all-parent-orphan-presence", absence,
     "todo.extend(reversed(parents))", "todo.extend(reversed(parents[:1]))"),
    ("genuine-root-absence", no_history, '"absent": not present', '"absent": False'),
    ("nearest-present-first-parent", latest,
     '"seed_commit": oid, "deletion_commit": deletion',
     '"seed_commit": chain[-1], "deletion_commit": deletion'),
    ("merge-first-parent", merge_parent,
     "root = parents[0] if parents else None", "root = parents[-1] if parents else None"),
    ("side-line-allocation-visible", side_activity,
     "difference = self.reachable(root) - self.reachable(selected)", "difference = frozenset()"),
    ("missing-parent-not-root", missing_parent,
     "_tree, parents = self.commit(oid)\n            seen.add(oid)",
     "try:\n                _tree, parents = self.commit(oid)\n"
     "            except ObservationError:\n                parents = ()\n            seen.add(oid)"),
    ("exact-object-identity", object_identity,
     "if hashlib.new(self.fmt, header + raw).hexdigest() != oid:",
     "if False:"),
    ("tree-framing", framing,
     'raise ObservationError(CANNOT_EVALUATE, "tree", "truncated tree entry")',
     "return out"),
    ("tree-duplicate", duplicates, "or b\"/\" in name or name in out", "or b\"/\" in name"),
    ("commit-cap", commit_cap,
     "if self.counts[what] > getattr(self.limits, what):", "if False:"),
    ("aggregate-byte-cap", byte_cap,
     "if self.counts[what] > getattr(self.limits, what):", "if False:"),
    ("elapsed-time-cap", time_cap,
     "if self.clock() >= self.deadline:", "if False:"),
    ("seed-first-parent-membership", seed_membership,
     "if evidence_commit not in chain:", "if False:"),
    ("seed-absent-evidence", absent_evidence,
     'source.run(["cat-file", "-e", evidence_commit], budget)', "pass"),
]
for field, value, identity in (
        ("commits", 0, "limits/commits-zero"),
        ("commits", True, "limits/commits-bool"),
        ("commits", 10001, "limits/commits-over"),
        ("payload_bytes", -1, "limits/payload-negative"),
        ("call_seconds", float("nan"), "limits/time-nan"),
        ("call_seconds", float("inf"), "limits/time-inf"),
        ("call_seconds", True, "limits/time-bool")):
    CASES.append((identity,
                  lambda m, f=field, v=value, i=identity: invalid_limit(m, f, v, i),
                  "limits.validate()", "pass"))


def run(source, *, reversals=False):
    digest = hashlib.sha256(source.encode()).hexdigest()
    ids = [row[0] for row in CASES]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate declared test identity")
    for number, (identity, test, old, new) in enumerate(CASES):
        module = load_candidate(source, "_pr4_candidate_" + str(number))
        test(module)
        print("PASS", identity)
        if reversals:
            if source.count(old) != 1:
                raise RuntimeError("mutation target is not unique: " + identity)
            changed = source.replace(old, new, 1)
            mutant = load_candidate(changed, "_pr4_mutant_" + str(number))
            try:
                test(mutant)
            except AssertionError as exc:
                if str(exc) != identity:
                    raise RuntimeError("wrong assertion for " + identity) from exc
            else:
                raise RuntimeError("reversal survived: " + identity)
            restored = load_candidate(source, "_pr4_restored_" + str(number))
            test(restored)
            print("RED-ON-REVERT", identity, "assertion=" + identity,
                  "restored=PASS",
                  "candidate_sha256=" + digest)
    print("EXECUTED", ",".join(ids))



def shared_tests(module, capture_source, run_source, *, reversals):
    import os
    import subprocess
    from unittest.mock import patch

    def capture_test(candidate, identity, command, timeout, cap):
        result = candidate._capture_bounded(
            [sys.executable, "-I", "-B", "-c", command], candidate._scrubbed_env(), timeout, cap)
        check(not result.completed and result.out == b"", identity)

    cases = (
        ("capture/stdout-cap", 'print("x" * 512)', 2, 64),
        ("capture/stderr-cap", 'import sys;sys.stderr.write("x" * 512)', 2, 64),
        ("capture/timeout", 'import time;time.sleep(0.2)', 0.03, 64),
        ("capture/invalid-control", 'print("ok")', 2, 0),
    )
    unsafe = (
        "def _capture_bounded(cmd, env, timeout, max_output_bytes):\n"
        "    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)\n"
        "    return _GitOutcome(True, p.returncode, p.stdout, p.stderr.decode())\n"
    )
    def candidate(text):
        m = types.ModuleType("_pr4_shared")
        m.__dict__.update(module.__dict__)
        exec(compile(text + "\n" + run_source, "<shared-candidate>", "exec"), m.__dict__)
        return m

    digest = hashlib.sha256((capture_source + run_source).encode()).hexdigest()
    for identity, command, duration, cap in cases:
        capture_test(candidate(capture_source), identity, command, duration, cap)
        print("PASS", identity)
        if reversals:
            try:
                capture_test(candidate(unsafe), identity, command, duration, cap)
            except AssertionError as exc:
                check(str(exc) == identity, "wrong shared assertion")
            else:
                raise RuntimeError("shared reversal survived: " + identity)
            capture_test(candidate(capture_source), identity, command, duration, cap)
            print("RED-ON-REVERT", identity, "assertion=" + identity,
                  "restored=PASS",
                  "candidate_sha256=" + digest)

    def policy(text, identity, predicate):
        m = candidate(capture_source)
        exec(compile(text, "<policy-candidate>", "exec"), m.__dict__)
        calls = []
        m._capture_bounded = lambda cmd, env, timeout, cap: calls.append((cmd, env))
        with patch.dict(os.environ, {"GIT_TRACE": "/forbidden/trace",
                                    "GIT_DIR": "/forbidden/repository",
                                    "GIT_CONFIG_COUNT": "1",
                                    "GIT_CONFIG_KEY_0": "core.fsmonitor",
                                    "GIT_CONFIG_VALUE_0": "/forbidden/execute"}):
            m._run_git("/usr/bin/git", "/fixture", ["cat-file", "-t", "a" * 40])
        check(len(calls) == 1 and predicate(*calls[0]), identity)

    settings = (
        ("policy/commit-graph", '"-c", "core.commitGraph=false", ',
         lambda cmd, env: "core.commitGraph=false" in cmd),
        ("policy/replacement", '"--no-replace-objects", ',
         lambda cmd, env: "--no-replace-objects" in cmd),
        ("policy/fsmonitor", '"-c", "core.fsmonitor=false",',
         lambda cmd, env: "core.fsmonitor=false" in cmd),
    )
    for identity, token, predicate in settings:
        policy(run_source, identity, predicate)
        print("PASS", identity)
        if reversals:
            check(run_source.count(token) == 1, "unique policy mutation")
            try:
                policy(run_source.replace(token, "", 1), identity, predicate)
            except AssertionError as exc:
                check(str(exc) == identity, "wrong policy assertion")
            else:
                raise RuntimeError("policy reversal survived: " + identity)
            policy(run_source, identity, predicate)
            print("RED-ON-REVERT", identity, "assertion=" + identity,
                  "restored=PASS",
                  "candidate_sha256=" + digest)

    policy(run_source, "policy/scrub-and-bind",
           lambda cmd, env: cmd[-5:] == ["-C", "/fixture", "cat-file", "-t", "a" * 40]
           and cmd[0] == "/usr/bin/git" and "--no-pager" in cmd
           and "GIT_DIR" not in env and "GIT_TRACE" not in env
           and "GIT_CONFIG_COUNT" not in env and env["GIT_OPTIONAL_LOCKS"] == "0"
           and env["GIT_NO_LAZY_FETCH"] == "1")
    print("PASS policy/scrub-and-bind")
    if reversals:
        altered = run_source.replace("env = _scrubbed_env()", "env = dict(os.environ)", 1)
        predicate = lambda cmd, env: "GIT_DIR" not in env and "GIT_TRACE" not in env
        try:
            policy(altered, "policy/scrub-and-bind", predicate)
        except AssertionError as exc:
            check(str(exc) == "policy/scrub-and-bind", "wrong scrub assertion")
        else:
            raise RuntimeError("scrub reversal survived")
        policy(run_source, "policy/scrub-and-bind", predicate)
        print("RED-ON-REVERT policy/scrub-and-bind assertion=policy/scrub-and-bind",
              "restored=PASS",
              "candidate_sha256=" + digest)


if __name__ == "__main__":
    import ast
    import _opf_observe
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    args = parser.parse_args()
    here = pathlib.Path(__file__).parent
    source = here.joinpath("_opf_init_observe.py").read_text(encoding="utf-8")
    run(source, reversals=args.red_on_revert)
    shared_source = here.joinpath("_opf_observe.py").read_text(encoding="utf-8")
    functions = {node.name: ast.get_source_segment(shared_source, node) + "\n"
                 for node in ast.parse(shared_source).body if isinstance(node, ast.FunctionDef)}
    shared_tests(_opf_observe, functions["_capture_bounded"], functions["_run_git"],
                 reversals=args.red_on_revert)
