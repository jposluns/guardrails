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
    sys.stderr.write(
        "error: check_opf_init_observe.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

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

            def arm(path):
                _opf_store._open_dir_nofollow = real
                return fault.arm(real(path))
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
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass; the
# verdict then ends the process with os._exit, so an atexit callback registered by loaded code can never
# run after it (that channel is unreachable, not merely disclosed). A SystemExit ending a worker thread
# is the interpreter's normal thread exit (default-hook parity) and is not recorded. Each recorder
# chains to the hook it wrapped, so the usual traceback still reaches stderr after the marker line.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    import threading

    previous_unraisable = sys.unraisablehook
    previous_thread = threading.excepthook

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

    def _record_thread(args):
        if args.exc_type is not None and issubclass(args.exc_type, SystemExit):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    threading.excepthook = _record_thread


def _gate_exit(code):
    """Settle and END the process: flush both streams, force a recorded background fault to exit 2 over a
    passing verdict, then os._exit, so no atexit callback registered by loaded code runs after the verdict
    (the gate's own cleanup runs inside its work; this gate registers no atexit work of its own)."""
    import os
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
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
)


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
    return failures


if __name__ == "__main__":
    _fail_closed_main(main)
