#!/usr/bin/env python3
"""PR4a raw-reader tests. No production admission coverage is claimed here.

The fixtures are in-memory object databases. Reversions load separate module
instances and separate fixtures. No working tree, index, ref or substrate is
created. Filesystem/operation fixture families remain separate PR4 obligations.
The harness accepts source text without a reconciled repository review target;
reversal reports identify the measured candidate content digest only.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_init_observe.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import hashlib
import pathlib
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


# A case returns this (it otherwise returns None) when its reversal cannot be discriminated here:
# os.O_PATH is absent, so the walk keeps its documented O_RDONLY fallback and the reversal IS the
# production behaviour. The runner then reports the reversal UNAVAILABLE, never red, and refuses the
# declaration on a platform that has os.O_PATH.
NO_O_PATH = ("os.O_PATH absent: the walk keeps its documented O_RDONLY fallback, so the reversal is "
             "the production behaviour here")


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


def graft_close(module):
    """#378 P1: GitObjects.graft_snapshot moves ownership of the walk descriptor before closing it. The
    close of "/" fails after releasing its number to a reuser (the shared _journal close harness): the walk
    reports CANNOT-EVALUATE and its finally closes only the next directory, never the released number. Green
    is no problem at all, with and without the PROBE watch. Under the close-then-rebind body the finally
    closes the reuser's number (REUSE) and the next directory is never closed (LEAK): that pair, and only
    it, is this case's red; any other outcome is a harness error, never accepted as red."""
    import os
    import tempfile
    import _journal
    import _opf_store
    with tempfile.TemporaryDirectory(prefix="opf-graft-close-") as directory:
        target = os.path.join(os.path.realpath(directory), "info", "grafts")
        source = types.SimpleNamespace(run=lambda args, budget: (target + "\n").encode("utf-8"),
                                       product_root="/")

        def call(fault):
            real = _opf_store._open_dir_nofollow

            def arm(path, **kwargs):
                _opf_store._open_dir_nofollow = real
                return fault.arm(real(path, **kwargs))
            _opf_store._open_dir_nofollow = arm
            try:
                module.GitObjects.graft_snapshot(source, None)
            finally:
                _opf_store._open_dir_nofollow = real

        def expect(exc):
            return type(exc) is module.ObservationError and "injected close failure" in exc.detail
        for watch in (False, True):
            problems = _journal._st_close_run(call, True, expect, watch)
            tags = [problem.split(":")[0] for problem in problems]
            if tags == ["REUSE", "LEAK"] and not watch:
                print("RED-BY", "graft-close-reuse", "; ".join(problems))
                raise AssertionError("graft-close-reuse")
            if problems:
                raise RuntimeError("graft-close-reuse: {}".format(problems))


def graft_search_only(module):
    """The grafts walk crosses a SEARCH-ONLY ancestor (mode 0111, the owner-bound spelling of the
    root-owned 0711 scratch shape) and still classifies absence, because every component is opened in
    the shared walk's traverse mode (_opf_store._WALK_O_TRAVERSE: O_PATH, search permission only,
    where it exists), and a SYMLINKED intermediate still refuses (CANNOT-EVALUATE, never followed).
    The reversal (components opened O_RDONLY again) is red by the recorded open flags wherever
    os.O_PATH exists (uid 0 included) and also red behaviourally (EACCES) for a uid the mode bits
    bind. On a platform without O_PATH the walk keeps the O_RDONLY fallback by design: there the case
    asserts the documented fallback refusal instead (a bound uid's search-only ancestor refuses
    CANNOT-EVALUATE with EACCES), still pins the symlink refusal, and returns NO_O_PATH, so the
    runner reports the reversal UNAVAILABLE (simulated_fallback runs this branch where O_PATH exists)."""
    import os
    import tempfile
    import _opf_store
    identity = "graft-search-only-ancestor"
    fallback = not hasattr(os, "O_PATH")
    with tempfile.TemporaryDirectory(prefix="opf-graft-searchonly-") as directory:
        base = os.path.realpath(directory)
        shield = os.path.join(base, "shield")
        os.makedirs(os.path.join(shield, "info"))
        target = os.path.join(shield, "info", "grafts")
        source = types.SimpleNamespace(run=lambda args, budget: (target + "\n").encode("utf-8"),
                                       product_root="/")
        recorded = []
        real_open = os.open

        def recording_open(*args, **kwargs):
            if kwargs.get("dir_fd") is not None:
                recorded.append(args[1])
            return real_open(*args, **kwargs)

        bound = os.geteuid() != 0
        if bound:
            os.chmod(shield, 0o111)
        try:
            os.open = recording_open
            try:
                try:
                    outcome = module.GitObjects.graft_snapshot(source, None)
                except module.ObservationError as exc:
                    outcome = ("refused", exc.code, exc.detail)
            finally:
                os.open = real_open
        finally:
            if bound:
                os.chmod(shield, 0o755)
        if fallback and bound:
            # The documented fallback: a search-only ancestor still refuses, fail-closed (EACCES).
            check(outcome[0] == "refused" and outcome[1] == module.CANNOT_EVALUATE
                  and "[Errno 13]" in outcome[2], identity)
        else:
            check(len(outcome) == 3 and outcome[0] == target and outcome[2] == "absent", identity)
        want = _opf_store._WALK_O_TRAVERSE
        check(bool(recorded) and all(flags & want == want and flags & os.O_NOFOLLOW
                                     for flags in recorded), identity)
        # A symlinked intermediate still refuses through the traverse mode, never followed off-tree.
        os.symlink("shield", os.path.join(base, "lnk"))
        twisted = os.path.join(base, "lnk", "info", "grafts")
        src2 = types.SimpleNamespace(run=lambda args, budget: (twisted + "\n").encode("utf-8"),
                                     product_root="/")
        refuses(module, lambda: module.GitObjects.graft_snapshot(src2, None),
                module.CANNOT_EVALUATE, identity)
    return NO_O_PATH if fallback else None


def graft_search_only_root(module):
    """The grafts walk opens the filesystem root "/" in the traverse mode too (traverse_only=True),
    because it only traverses it, so a search-only "/" is classifiable. The host root is never
    chmodded: the open of "/" is fault-injected to refuse EACCES whenever its flags lack os.O_PATH,
    which is the open a search-only root refuses. The reversal (the root opened readable again) is
    red wherever os.O_PATH exists, uid 0 included (the injection is flag-driven, not mode-driven).
    Without os.O_PATH every root open lacks it: the case asserts the documented fallback refusal
    instead and returns NO_O_PATH, so the runner reports the reversal UNAVAILABLE."""
    import errno
    import os
    import tempfile
    identity = "graft-search-only-root"
    o_path = getattr(os, "O_PATH", 0)
    with tempfile.TemporaryDirectory(prefix="opf-graft-searchonly-root-") as directory:
        base = os.path.realpath(directory)
        os.makedirs(os.path.join(base, "info"))
        target = os.path.join(base, "info", "grafts")
        source = types.SimpleNamespace(run=lambda args, budget: (target + "\n").encode("utf-8"),
                                       product_root="/")
        roots = []
        real_open = os.open

        def search_only_root_open(*args, **kwargs):
            if args[0] in ("/", "//") and kwargs.get("dir_fd") is None:
                roots.append(args[1])
                if not (o_path and args[1] & o_path):
                    raise PermissionError(errno.EACCES, "injected: search-only filesystem root", args[0])
            return real_open(*args, **kwargs)

        os.open = search_only_root_open
        try:
            try:
                outcome = module.GitObjects.graft_snapshot(source, None)
            except module.ObservationError as exc:
                outcome = ("refused", exc.code, exc.detail)
        finally:
            os.open = real_open
    check(len(roots) == 1, identity)                      # the injection was reached, exactly once
    if not o_path:
        check(outcome[0] == "refused" and outcome[1] == module.CANNOT_EVALUATE
              and "injected: search-only filesystem root" in outcome[2], identity)
        return NO_O_PATH
    check(len(outcome) == 3 and outcome[0] == target and outcome[2] == "absent", identity)
    return None


TRAVERSE_CASES = ("graft-search-only-ancestor", "graft-search-only-root")


def simulated_fallback(source):
    """The traverse-mode cases through the runner, on the documented no-O_PATH fallback SIMULATED
    where os.O_PATH exists: os.O_PATH is hidden and _opf_store._WALK_O_TRAVERSE set to os.O_RDONLY,
    the value _opf_store computes without O_PATH. Each case must pass by asserting the fallback
    refusal, and the runner must report each reversal UNAVAILABLE (never red, never survived). A
    native run on a platform without O_PATH is not claimed by this simulation."""
    import os
    import _opf_store
    if not hasattr(os, "O_PATH"):
        print("FALLBACK-SIMULATION not run: os.O_PATH is absent, so the native run above IS the fallback")
        return
    rows = [row for row in CASES if row[0] in TRAVERSE_CASES]
    check([row[0] for row in rows] == list(TRAVERSE_CASES), "simulated-fallback:cases")
    saved_path, saved_walk = os.O_PATH, _opf_store._WALK_O_TRAVERSE
    del os.O_PATH
    _opf_store._WALK_O_TRAVERSE = os.O_RDONLY
    try:
        unavailable = run(source, reversals=True, cases=rows, label="SIMULATED-FALLBACK")
    finally:
        os.O_PATH = saved_path
        _opf_store._WALK_O_TRAVERSE = saved_walk
    check(unavailable == list(TRAVERSE_CASES), "simulated-fallback:unavailable")
    print("FALLBACK-SIMULATED", ",".join(unavailable), "PASS reversal=UNAVAILABLE")


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
    ("graft-search-only-ancestor", graft_search_only,
     "next_fd = os.open(component, _opf_store._WALK_O_TRAVERSE | os.O_DIRECTORY\n"
     "                                      | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)",
     "next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY\n"
     "                                      | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)"),
    ("graft-search-only-root", graft_search_only_root,
     'fd = _opf_store._open_dir_nofollow("/", traverse_only=True)',
     'fd = _opf_store._open_dir_nofollow("/")'),
    ("graft-close-reuse", graft_close,
     "prev, fd = fd, next_fd                    # ownership moves first: a failed close is\n"
     "                os.close(prev)                            # never closed again by the finally (P1, #378)",
     "os.close(fd)\n                fd = next_fd"),
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


def run(source, *, reversals=False, cases=None, label=""):
    import os
    cases = CASES if cases is None else cases
    tag = "-" + label if label else ""
    digest = hashlib.sha256(source.encode()).hexdigest()
    ids = [row[0] for row in cases]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate declared test identity")
    unavailable = []
    for number, (identity, test, old, new) in enumerate(cases):
        module = load_candidate(source, "_pr4_candidate_" + str(number))
        reason = test(module)
        print("PASS" + tag, identity)
        if reason is not None and (reason != NO_O_PATH or hasattr(os, "O_PATH")):
            raise RuntimeError("reversal declared unavailable without cause: " + identity)
        if reversals:
            if source.count(old) != 1:
                raise RuntimeError("mutation target is not unique: " + identity)
            if reason is not None:
                # Neither red nor survived: the case declared its reversal indiscriminable here.
                unavailable.append(identity)
                print("REVERSAL-UNAVAILABLE" + tag, identity, "reason=" + reason)
                continue
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
    print("EXECUTED" + tag, ",".join(ids))
    if unavailable:
        print("RED-ON-REVERT" + tag, "UNAVAILABLE", ",".join(unavailable))
    return unavailable



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
        def capture(cmd, env, timeout, cap, input_bytes=None):
            check(input_bytes is None, "policy/cat-file-has-no-stdin")
            calls.append((cmd, env))
        m._capture_bounded = capture
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


# Threat model: the candidate this harness loads in-process (_opf_init_observe.py, and _opf_observe.py's
# functions) is reviewed in-repo code; the guard catches an ACCIDENTAL process ending from it (a stray
# sys.exit, SystemExit, KeyboardInterrupt, GeneratorExit, or any other BaseException that is not an
# Exception) at load and in every later call. An Exception keeps its existing meaning, a red finding (exit 1).
# A KeyboardInterrupt (an operator's Ctrl-C and one raised by loaded code are not told apart) is re-raised as
# a fresh KeyboardInterrupt, so it stops the gate runner instead of reading as this gate's exit 2.
_PROCESS_ENDING = (BaseException,)


def _backstop(run_all, args):
    """Return run_all(args)'s status. An Exception propagates unchanged (a test failure, exit 1). Any other
    BaseException (SystemExit 0, None or a non-int code included) is exit 2 (cannot evaluate) with a fixed
    message that never formats the exception or anything loaded. A KeyboardInterrupt is re-raised, after a
    fixed message, as a fresh KeyboardInterrupt (its context suppressed), which stops the runner rather than
    being absorbed as exit 2. Background fault channels are settled at this gate's entry
    (_fail_closed_main): a fault ending a worker thread or a destructor is recorded by the hooks installed
    there and forces exit 2 over a passing verdict, and the verdict ends the process with os._exit, so an
    atexit callback registered by loaded code never runs after it. Residuals, not covered: os._exit called
    by the loaded code itself, signal handlers it installs, mutation of sys or of
    this module's globals by the loaded code, deliberately hostile objects (the one residual stated in the
    _opf_views class disclosure), and a process exit raised while this module's own
    top-level imports run, before this guard is entered."""
    try:
        return run_all(args)
    except Exception:
        raise
    except KeyboardInterrupt:
        sys.stderr.write("check_opf_init_observe: self-test interrupted: KeyboardInterrupt re-raised to stop the "
                         "run; fail-closed\n")
        raise KeyboardInterrupt from None
    except _PROCESS_ENDING:
        sys.stderr.write("check_opf_init_observe: cannot evaluate: in-process code raised a process-ending "
                         "exception; fail-closed\n")
        return 2


# Each case is a candidate loaded through load_candidate: (label, source, call run()). A case whose label names
# KeyboardInterrupt must re-raise a fresh KeyboardInterrupt (the probe exits 130); every other case must give
# exit 2.
_LOADED_EXIT_CASES = (
    ("load SystemExit(0)", "raise SystemExit(0)\n", False),
    ("load SystemExit(None)", "raise SystemExit\n", False),
    ("load KeyboardInterrupt", "raise KeyboardInterrupt\n", False),
    ("call KeyboardInterrupt", "def run():\n    raise KeyboardInterrupt\n", True),
    ("load GeneratorExit", "raise GeneratorExit\n", False),
    ("load BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n", False),
    ("load SystemExit(code whose repr exits 0)",
     "class R:\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
     "raise SystemExit(R())\n", False),
    ("call SystemExit(0)", "def run():\n    raise SystemExit(0)\n", True),
)

# Run in a child through the real entry: load this file by path, replace only run() with a load of (and call
# into) one case through the real load_candidate, then exit with main(["--self-test"]).
_LOADED_EXIT_PROBE = """import importlib.util, sys
tool, case, call = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("_init_observe_entry_probe", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def body(source, *, reversals=False):
    module = gate.load_candidate(gate.pathlib.Path(case).read_text(encoding="utf-8"), "_pr4_exit_probe")
    if call == "call":
        module.run()
gate.run = body
try:
    code = gate.main(["--self-test"])
except KeyboardInterrupt as exc:
    sys.exit(130 if type(exc) is KeyboardInterrupt and exc.__suppress_context__ else 3)
sys.exit(code)
"""


def loaded_exit_vectors():
    """Each case, at load or in a later call, must give exit 2 with the fixed backstop message through the
    real entry in a child process, so the vector is red if the backstop is reverted (_PROCESS_ENDING
    emptied) or removed from main(). An interrupt case must instead re-raise a fresh KeyboardInterrupt with
    the fixed interrupt message (probe exit 130), so the vector is red if the interrupt is absorbed as exit 2
    (which would let a gate runner run on past an operator's Ctrl-C)."""
    import subprocess
    import tempfile
    tool = str(pathlib.Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-observe-loaded-exit-") as tmp:
        probe = pathlib.Path(tmp) / "probe.py"
        probe.write_text(_LOADED_EXIT_PROBE, encoding="utf-8")
        for index, (label, body, call) in enumerate(_LOADED_EXIT_CASES):
            case = pathlib.Path(tmp) / "loaded_exit_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case),
                                    "call" if call else "load"], capture_output=True, text=True, timeout=300)
            if "KeyboardInterrupt" in label:
                if child.returncode != 130 or "check_opf_init_observe: self-test interrupted: " \
                        "KeyboardInterrupt re-raised" not in child.stderr:
                    raise RuntimeError("loaded-exit vector {}: expected a fresh KeyboardInterrupt (130) with "
                                       "the interrupt message, got {}".format(label, child.returncode))
            elif child.returncode != 2 or "check_opf_init_observe: cannot evaluate" not in child.stderr:
                raise RuntimeError("loaded-exit vector {}: expected exit 2 with the backstop message, got {}"
                                   .format(label, child.returncode))
            print("PASS loaded-exit/" + label)


def _run_all(args):
    import ast
    import _opf_observe
    here = pathlib.Path(__file__).parent
    source = here.joinpath("_opf_init_observe.py").read_text(encoding="utf-8")
    run(source, reversals=args.red_on_revert)
    simulated_fallback(source)
    shared_source = here.joinpath("_opf_observe.py").read_text(encoding="utf-8")
    functions = {node.name: ast.get_source_segment(shared_source, node) + "\n"
                 for node in ast.parse(shared_source).body if isinstance(node, ast.FunctionDef)}
    shared_tests(_opf_observe, functions["_capture_bounded"], functions["_run_git"],
                 reversals=args.red_on_revert)
    if args.self_test:
        loaded_exit_vectors()
        fault_failures = _fault_channel_vectors()
        if fault_failures:
            raise RuntimeError("fault-channel vectors: " + "; ".join(fault_failures))
        print("PASS harness/fault-channels")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    args = parser.parse_args(argv)
    return _backstop(_run_all, args)



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass. Before
# a passing verdict the settle waits (_SETTLE_SECONDS in all) for every other thread still running,
# threading's census and the runtime's live count of low-level _thread workers together, so a worker's
# later fault is recorded instead of killed silently by the exit, and a thread or low-level worker still
# running at the bound fails the verdict closed (exit 2). On Linux the settle also waits on the OS's own
# thread list for this process (_os_threads_pending), which still shows a worker while the runtime clears
# its thread state (its thread-local and context values' destructors run there, after the runtime's live
# count and threading's census already dropped it), a faulthandler watchdog and a thread a C extension
# starts. The verdict then ends the process with os._exit, so an
# atexit callback registered by loaded code can never run after it (that channel is unreachable, not
# merely disclosed). Only a SystemExit with code None or 0 ending a worker thread is the interpreter's
# normal SUCCESSFUL thread exit (default-hook parity) and is not recorded; any other code is an
# unsuccessful exit a worker reported, so it is recorded.
# A low-level _thread worker's SystemExit never reaches any hook (the interpreter drops it), so the
# start entries and threading's saved module aliases of them are wrapped from before the gate's work and
# a non-zero code ending such a worker is recorded as a fault there. The subinterpreter modules are made
# unimportable before the gate's work, because a thread inside a subinterpreter reports its faults to
# that interpreter's own hooks, never these recorders. Stated residuals, not closed: where the platform
# has no per-process thread list (outside Linux) a thread outside the Python runtime stays invisible to
# the census and the live count; and a process the loaded code starts is not followed, so one still
# running after this gate ends is outside its verdict.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. After the exit flushes the settle stops the collector, blocks every signal, runs any
# handler already tripped, and re-reads the record and every liveness term, so a fault recorded or a
# worker a signal handler started while a flush was blocked on a full pipe still fails the verdict, and
# no loaded code can run between that last read and the exit.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (deliberately hostile loaded code is accepted there and not closed).
_LOADED_FAULTS = []
# A started low-level worker's liveness flag, set synchronously at the start call: the runtime
# live count misses the brief window before a just-started worker's OS thread registers, so the
# settle also waits while any flag is set, never breaking on that transient zero.
_LOADED_WORKERS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    # A local alias: opf.py's close-lifecycle census reads an attribute store through the bare module name
    # as shadowing that module.
    import threading as _threads

    # The modules that create a subinterpreter are made unimportable (an import is an ImportError the
    # gate's work fails on): a thread inside one reports its faults to that interpreter's own hooks.
    for _name in ("_interpreters", "concurrent.interpreters", "_testinternalcapi", "_testcapi"):
        sys.modules[_name] = None

    previous_unraisable = sys.unraisablehook
    previous_thread = _threads.excepthook

    def _record(channel, chained, args):
        _LOADED_FAULTS.append(channel)
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a {} fault was recorded; a passing verdict will fail closed\n".format(
                _FAULT_MARKER, channel))
        finally:
            chained(args)

    def _record_unraisable(args):
        _record("destructor-or-callback", previous_unraisable, args)

    def _benign_thread_exit(args):
        # Only a worker thread ending by SystemExit(None) or SystemExit(0) is the interpreter's normal
        # SUCCESSFUL thread exit (default-hook parity). Any other SystemExit code is an unsuccessful exit
        # a worker reported, recorded like any other fault (never a silent pass); the code is read
        # through exact built-in types alone and never formatted.
        if args.exc_type is None or not issubclass(args.exc_type, SystemExit):
            return False
        code = getattr(args.exc_value, "code", None) if args.exc_value is not None else None
        return code is None or (type(code) is int and code == 0)

    def _record_thread(args):
        if _benign_thread_exit(args):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    _threads.excepthook = _record_thread

    import _thread as _lowlevel

    def _record_lowlevel_fault():
        _LOADED_FAULTS.append("lowlevel-worker")
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a lowlevel-worker fault was recorded; a passing verdict will fail "
                             "closed\n".format(_FAULT_MARKER))
        except Exception:
            pass

    import types as _types
    _bootstrap = _threads.Thread._bootstrap

    def _genuine_bootstrap(target):
        # A thread start is threading's own only when its target is exactly a bound method whose function
        # is threading.Thread._bootstrap and whose instance is a threading.Thread (read through the exact
        # method type, never an attribute the target supplies). Such a worker's fault already reaches
        # threading.excepthook, so it is passed through untouched; wrapping it would also replace the
        # target an audit-based observer reads, making threading's own start look like a raw one.
        return (type(target) is _types.MethodType and target.__func__ is _bootstrap
                and isinstance(target.__self__, _threads.Thread))

    def _wrap_lowlevel(original):
        # A raw low-level worker's SystemExit reaches no hook (the interpreter drops it), so the start
        # entry wraps the worker's function and records a non-zero code as a fault (None or 0 is the
        # successful low-level exit, default-interpreter parity; the code is read through exact built-in
        # types alone and never formatted), and tracks the worker's liveness flag. A genuine threading
        # start is passed through untouched.
        def _start(function, *args, **kwargs):
            if _genuine_bootstrap(function):
                return original(function, *args, **kwargs)
            state = [True]
            def _run(*wargs, **wkwargs):
                try:
                    return function(*wargs, **wkwargs)
                except SystemExit as exc:
                    code = getattr(exc, "code", None)
                    if not (code is None or (type(code) is int and code == 0)):
                        _record_lowlevel_fault()
                    raise
                finally:
                    state[0] = False
            result = original(_run, *args, **kwargs)
            _LOADED_WORKERS.append(state)
            return result
        return _start

    _wrapped = []
    for _entry in ("start_new_thread", "start_new", "start_joinable_thread"):
        if hasattr(_lowlevel, _entry):
            _original = getattr(_lowlevel, _entry)
            _replacement = _wrap_lowlevel(_original)
            setattr(_lowlevel, _entry, _replacement)
            _wrapped.append((_original, _replacement))
    for _alias in dir(_threads):
        _value = getattr(_threads, _alias)
        for _original, _replacement in _wrapped:
            if _value is _original:
                setattr(_threads, _alias, _replacement)


# The OS's own list of this process's threads (Linux): it still shows a worker while the runtime clears
# its thread state, a faulthandler watchdog and a thread a C extension starts. None where the platform has
# no such list; the runtime's own counts then carry the settle alone (a stated residual).
_TASK_DIR = "/proc/self/task" if sys.platform.startswith("linux") else None


def _os_threads_pending():
    """True while the OS lists a thread of this process besides the calling one, and when that list
    exists but cannot be read (fail-closed); False where the platform has no such list."""
    import os
    if _TASK_DIR is None:
        return False
    try:
        return len(os.listdir(_TASK_DIR)) != 1
    except OSError:
        return True


def _drain_pending_signals():
    """A Python-level call: the interpreter runs any signal handler already tripped on entry here."""
    return None


_SETTLE_SECONDS = 60.0


def _gate_exit(code):
    """Settle and END the process: before a passing verdict, wait (up to _SETTLE_SECONDS in all) for every
    other thread still running, so a fault it raises later is recorded, never killed silently by the exit,
    and fail closed (exit 2) if one still runs at the bound (the census covers threading's threads and,
    through the runtime's live count, workers started through the low-level _thread entry points, so a
    low-level worker still running at the bound also fails closed, and on Linux the OS's own thread list
    covers a worker still clearing its thread state, a watchdog and a C extension's thread; elsewhere a
    thread outside the Python runtime is invisible: a stated residual); force a recorded background fault
    to exit 2 over a passing verdict, flush both streams, then stop the collector, block every signal, run
    any handler already tripped and re-read the record and every liveness term AFTER the flushes (a fault
    recorded or a worker started while a flush was blocked, on a full pipe say, must still fail the
    verdict; its late marker is written straight to fd 2, past the buffers), then os._exit, so no atexit
    callback registered by loaded code
    runs after the verdict (the gate's own cleanup runs inside its work; this gate registers no atexit work
    of its own)."""
    import os
    import threading as _threads
    import time
    import _thread as _lowlevel
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    low = code & 0xFF
    if code != 0 and low == 0:
        # os._exit truncates its status to the low 8 bits, so this non-zero code would end the
        # process with exit status 0, a silent pass; fail closed instead.
        sys.stderr.write("{}: a SystemExit code outside the exit-status range at the gate entry; exit 2\n".format(
            _FAULT_MARKER))
        code = 2
    else:
        # os._exit refuses a code outside the C int range (OverflowError) instead of exiting, and
        # that escape would let atexit callbacks registered by loaded code run after the verdict:
        # take the low byte, the status any in-range code would report, so the exit below always runs.
        code = low
    if code == 0:
        current = _threads.current_thread()
        deadline = time.monotonic() + _SETTLE_SECONDS
        while True:
            running = [worker for worker in _threads.enumerate()
                       if worker is not current and worker.is_alive()]
            pending = (_lowlevel._count() or any(_state[0] for _state in _LOADED_WORKERS)
                       or _os_threads_pending())
            remaining = deadline - time.monotonic()
            if (not running and not pending) or remaining <= 0:
                break
            if running:
                running[0].join(min(remaining, 0.5))
            else:
                time.sleep(min(remaining, 0.05))
        if running or pending:
            sys.stderr.write("{}: a worker thread or low-level worker was still running at the verdict; "
                             "fail-closed\n".format(_FAULT_MARKER))
            code = 2
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        sys.stderr.write("{}: {} background fault(s) from loaded code over a passing verdict; "
                         "fail-closed\n".format(_FAULT_MARKER, len(_LOADED_FAULTS) or 1))
        code = 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        if code == 0:
            code = 2
    if code == 0:
        # The verdict stands only once no loaded code can run before the exit: the collector is stopped,
        # every signal is blocked (no other thread is left to take one), a handler already tripped runs
        # now, and the record and every liveness term are read afresh, so a fault or a worker a handler
        # started while a flush was blocked fails the verdict.
        import gc as _gc
        import signal as _signals
        try:
            _gc.disable()
            _signals.pthread_sigmask(_signals.SIG_BLOCK, _signals.valid_signals())
            _drain_pending_signals()
        except BaseException:  # noqa: BLE001  a handler's own escape fails the verdict, never the exit
            code = 2
        if (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False) or _lowlevel._count()
                or any(_state[0] for _state in _LOADED_WORKERS) or _os_threads_pending()):
            code = 2
        if code != 0:
            try:
                os.write(2, ("{}: a background fault or a worker was recorded during the exit flush; "
                             "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
            except OSError:
                pass
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through the gate's REAL _fail_closed_main in a child that
# loads one fixture the way this gate loads code in process: a clean load passes; an atexit callback
# registered by loaded code never runs (unreachable behind os._exit); a worker-thread fault and a
# destructor fault are recorded and force exit 2.
_FAULT_CHANNEL_PROBE = """import importlib.util, sys
tool, fixture = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_fault_channel_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
gate._SETTLE_SECONDS = 2.0
def load():
    case = importlib.util.spec_from_file_location("_fault_channel_fixture", fixture)
    module = importlib.util.module_from_spec(case)
    case.loader.exec_module(module)
    return 0
gate._fail_closed_main(load)
"""

# (label, fixture source, expected exit, marker required, text that must NOT appear on stderr)
_FAULT_CHANNEL_CASES = (
    ("clean", "x = 1\n", 0, False, None),
    ("atexit", "import atexit, sys\n"
     "def callback():\n"
     "    sys.stderr.write('FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "    raise RuntimeError('fault-fixture-atexit')\n"
     "atexit.register(callback)\n", 0, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("joined-thread", "import threading\n"
     "def fault():\n"
     "    raise RuntimeError('fault-fixture-thread')\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("destructor", "import gc\n"
     "class Fault:\n"
     "    def __del__(self):\n"
     "        raise RuntimeError('fault-fixture-destructor')\n"
     "Fault()\n"
     "gc.collect()\n", 2, True, None),
    ("thread-systemexit", "import threading\n"
     "def fault():\n"
     "    raise SystemExit(7)\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("thread-systemexit-zero", "import sys\nimport threading\n"
     "def done():\n"
     "    sys.exit(0)\n"
     "worker = threading.Thread(target=done)\n"
     "worker.start()\n"
     "worker.join()\n", 0, False, None),
    ("finishing-thread", "import threading, time\n"
     "threading.Thread(target=time.sleep, args=(0.3,)).start()\n", 0, False, None),
    ("unjoined-thread", "import threading, time\n"
     "def fault():\n"
     "    time.sleep(0.5)\n"
     "    raise RuntimeError('fault-fixture-unjoined-thread')\n"
     "threading.Thread(target=fault).start()\n", 2, True, None),
    ("unfinished-thread", "import threading\n"
     "threading.Thread(target=threading.Event().wait, daemon=True).start()\n", 2, True, None),
    # Low-level _thread workers: the interpreter drops a SystemExit ending one without calling any hook,
    # so the wrapped start entry records a non-zero code as a fault (None or 0 stays the successful
    # low-level exit), and the settle waits on the runtime's live count, so one still running at the
    # bound fails the verdict closed while one that finishes is waited out.
    ("lowlevel-systemexit", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(7)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 2, True, None),
    ("lowlevel-systemexit-zero", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(0)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 0, False, None),
    ("lowlevel-finishing", "import _thread, time\n"
     "_thread.start_new_thread(time.sleep, (0.3,))\n", 0, False, None),
    ("lowlevel-unfinished", "import _thread, threading\n"
     "_thread.start_new_thread(threading.Event().wait, ())\n", 2, True, None),
    # A handled failed low-level start leaves no pending worker: red when the start flag is recorded
    # before the start call (the flag then stays set and the settle fails closed at the bound).
    ("lowlevel-failed-start", "import _thread\n"
     "try:\n"
     "    _thread.start_new_thread(print)\n"
     "except TypeError:\n"
     "    pass\n", 0, False, None),
    # Thread-state teardown: a worker's thread-local and context-variable values are destroyed while the
    # runtime clears its thread state, after its live count and threading's census already dropped it;
    # the OS thread list still shows the worker, so the settle waits and records the fault (red without
    # that term: each ends 0).
    ("lowlevel-local-destructor", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    ("thread-local-destructor", "import threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-thread-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "threading.Thread(target=worker).start()\n"
     "entered.wait()\n", 2, True, None),
    ("contextvar-destructor", "import _thread, contextvars, threading, time\n"
     "entered = threading.Event()\n"
     "held = contextvars.ContextVar('held')\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-contextvar-destructor')\n"
     "def worker():\n"
     "    held.set(Held())\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The runtime's live count, the term that carries the settle where the platform has no thread list:
    # the fixture switches the OS list off in the probe's gate, and a low-level worker whose return value's
    # destructor still runs after its callback returned must still be waited out and recorded (red
    # without the live count: it ends 0).
    ("runtime-count-return-destructor", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-return-destructor')\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The settle's own reads of those two terms: a teardown destructor or a return value's destructor
    # that finishes without a fault is waited out and passes (red without the settle-position read: the
    # re-read after the flushes then still sees the worker and fails the clean run closed).
    ("lowlevel-local-finishing", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    ("runtime-count-return-finishing", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    # Capability removal: every subinterpreter module is unimportable (red when any one is left
    # importable: the fixture then ends 3).
    ("subinterpreter-modules", "import importlib\n"
     "for name in ('_interpreters', 'concurrent.interpreters', '_testinternalcapi', '_testcapi'):\n"
     "    try:\n"
     "        importlib.import_module(name)\n"
     "    except ImportError:\n"
     "        continue\n"
     "    raise SystemExit(3)\n", 0, False, None),
    # An armed faulthandler watchdog is a C-level thread only the OS thread list shows; still armed at
    # the bound it fails the verdict closed (red without that term: it ends 0).
    ("watchdog-armed", "import faulthandler\n"
     "faulthandler.dump_traceback_later(600)\n", 2, True, None),
    # Exit-status range: os._exit truncates its status to the low 8 bits, so a non-zero SystemExit
    # code that is a multiple of 256 would end the child with status 0, a silent pass; the gate maps
    # it to 2. The neighbouring codes pass through (257 reaches the parent as its low byte, 1).
    ("exit-status-255", "raise SystemExit(255)\n", 255, False, None),
    ("exit-status-256", "raise SystemExit(256)\n", 2, True, None),
    ("exit-status-257", "raise SystemExit(257)\n", 1, False, None),
    ("exit-status-negative-256", "raise SystemExit(-256)\n", 2, True, None),
    # Codes around the ends of the C int range: os._exit raises OverflowError on a code outside that
    # range instead of exiting, which would let the loaded code's atexit callbacks run after the
    # verdict; the gate takes the low byte first, so the exit always runs. The wide codes (beyond
    # each end, with non-zero low bytes) reach the parent as their low bytes only through that
    # ordering. The boundary codes (2**31, just past the positive end, and -(2**31), exactly the
    # negative end) have a zero low byte and fail closed as exit 2; the multiple-of-256 rule alone
    # already gives both that outcome, so those two cases are preservation controls over the range
    # ends, not evidence that the ordering is required. The code at the positive end itself,
    # 2**31 - 1, has low byte 255 and is inside os._exit's accepted range.
    ("exit-status-wide-positive", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(2**31 + 1)\n", 1, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-wide-negative", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(-(2**31) - 1)\n", 255, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-boundary-positive", "raise SystemExit(2**31)\n", 2, True, None),
    ("exit-status-boundary-negative", "raise SystemExit(-(2**31))\n", 2, True, None),
)


# The flush-window channel: _gate_exit's fault decision must hold across the exit flushes. The fixture
# fills the child's stdout pipe to its exact capacity (F_GETPIPE_SZ) and leaves one byte in the stream's
# buffer, then arms a timer (SIGALRM unblocked: a caller's blocked mask is inherited) whose signal handler
# drops an object with a faulting destructor, so the fault is recorded while the exit flush is blocked on
# the full pipe (after the settle's thread wait, which a worker-thread fault would not outlast); the
# parent drains stdout only after stderr shows the fault marker. Exit 2 with the marker is required. Red
# when the fault decision runs only before the flushes: the child then ends 0 with the marker on stderr.
_FLUSH_WINDOW_FIXTURE = """import fcntl, os, signal, sys
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
class Fault:
    def __del__(self):
        raise RuntimeError("fault-fixture-flush-window")
def fault(signum, frame):
    Fault()
signal.signal(signal.SIGALRM, fault)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""

# The same window with a start instead of a fault: the handler starts a low-level worker that never ends
# while the exit flush is blocked, then reports on fd 2, and the parent drains stdout only after that
# line. Exit 2 with the marker is required. Red when only the record is read after the flushes: the
# child then ends 0 with the worker still running.
_FLUSH_WINDOW_START_FIXTURE = """import _thread, fcntl, os, signal, sys, threading
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
def start(signum, frame):
    _thread.start_new_thread(threading.Event().wait, ())
    os.write(2, b"FAULT-FIXTURE-FLUSH-START\\n")
signal.signal(signal.SIGALRM, start)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""


def _flush_window_failures():
    """One failure string per flush-window requirement a child missed (see _FLUSH_WINDOW_FIXTURE and
    _FLUSH_WINDOW_START_FIXTURE)."""
    import subprocess
    from pathlib import Path
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-observe-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for label, fixture, ready in (
                ("flush-window", _FLUSH_WINDOW_FIXTURE, _FAULT_MARKER + ":"),
                ("flush-window-start", _FLUSH_WINDOW_START_FIXTURE, "FAULT-FIXTURE-FLUSH-START")):
            case = Path(tmp) / (label + ".py")
            case.write_text(fixture, encoding="utf-8")
            try:
                child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            with child:
                killer = threading.Timer(120, child.kill)
                killer.start()
                try:
                    header = b""
                    while ready.encode("ascii") not in header:
                        line = child.stderr.readline()
                        if not line:
                            break
                        header += line
                    child.stdout.read()
                    trailer = child.stderr.read()
                    code = child.wait()
                finally:
                    killer.cancel()
            stderr_text = (header + trailer).decode("utf-8", "replace")
            if code != 2:
                failures.append("fault-channel/{}: expected exit 2, got {}".format(label, code))
            elif (_FAULT_MARKER + ":") not in stderr_text:
                failures.append("fault-channel/{}: fault marker absent".format(label))
    return failures


# The entry wiring, checked statically on this file's own source: the final __main__ block must be
# exactly _ENTRY_WIRING by AST (spacing and comments aside), so a rewiring that keeps _fail_closed_main
# defined but routes an entry branch around it (a plain sys.exit around the work, say) is red even though
# the channel probes above drive _fail_closed_main directly.
_ENTRY_WIRING = '''if __name__ == "__main__":
    _fail_closed_main(main)
'''


def _entry_wiring_failures():
    """One failure string per entry-wiring requirement this file's own source misses (see _ENTRY_WIRING)."""
    import ast
    from pathlib import Path
    failures = []
    try:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return ["fault-channel/entry-wiring: this file could not be parsed ({})".format(type(exc).__name__)]
    last = tree.body[-1] if tree.body else None
    expected = ast.parse(_ENTRY_WIRING).body[0]
    if last is None or ast.dump(last) != ast.dump(expected):
        failures.append("fault-channel/entry-wiring: the final __main__ block is not the expected "
                        "fail-closed form")
    return failures

def _fault_channel_vectors():
    """One failure string per fault-channel case whose child did not end as required: the expected exit
    code, the fault marker exactly when a fault must be recorded, and for the atexit case no trace of the
    callback (it must never run). Red without the entry wrapper (the probe then ends 1, AttributeError on
    _fail_closed_main, where clean and atexit expect 0) and red with the wrapper reverted to a plain exit
    (the thread and destructor children then end 0 with the default hooks' output alone, where exit 2 with
    the marker is required, and the atexit child then runs the callback)."""
    import subprocess
    import tempfile
    failures = []
    tool = str(pathlib.Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-observe-fault-channel-") as tmp:
        probe = pathlib.Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for index, (label, body, expected, marked, absent) in enumerate(_FAULT_CHANNEL_CASES):
            case = pathlib.Path(tmp) / "fault_channel_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            if child.returncode != expected:
                failures.append("fault-channel/{}: expected exit {}, got {}".format(
                    label, expected, child.returncode))
            elif marked != ((_FAULT_MARKER + ":") in child.stderr):
                failures.append("fault-channel/{}: fault marker {}".format(
                    label, "absent" if marked else "present"))
            elif absent is not None and absent in child.stderr:
                failures.append("fault-channel/{}: the atexit callback ran".format(label))
    failures.extend(_flush_window_failures())
    failures.extend(_entry_wiring_failures())
    return failures


if __name__ == "__main__":
    _fail_closed_main(main)
