#!/usr/bin/env python3
"""OPF record-authoring gate (spec 8.8): `opf record` behaviour, and red-on-revert discriminators.

  check_opf_record.py --self-test                    the fixture suite (T1-T5, T9-T11)
  check_opf_record.py --self-test --red-on-revert    the same, plus each test's flip must turn it red

There is no live-adopter leg (this repository is not an OPFiles adopter), so the whole assurance rides the
self-test. Every fixture is a real store built beneath this gate's own mkdtemp root: `opf init`, a commit,
`opf render --write`, a commit, then per-case edits committed so the cleanliness gate sees a clean tree.
Each case runs on its own copy of that template; the root is removed in a finally.

  T1  a comment-bearing index, counters, or worklog refuses exit 2 with every byte untouched
      (flip: drop the byte-reproduction precondition)
  T2  create with links and refs round-trips, the published bytes are canonical, and a lossy emitter is
      refused before any write (flip: publish raw emit output instead of emit_checked)
  T3  a plan that mutates one extra field of an existing record refuses exit 2, bytes untouched
      (flip: drop the allowed-delta postcondition)
  T4  two sequential creates claim BI-1 then BI-2 with monotonic counters; a counters map missing an
      enabled namespace refuses (flip: drop the known-complete proof)
  T5  a kill at each journal step, then reconciliation on the next run, leaves the operands exactly the
      prestate or exactly the poststate, the poststate iff the transaction is COMPLETE, and no killed run
      reports an id (flip: write counters outside the journaled transaction)
  T9  a held lease refuses exit 2 and is never seized; success is reported only after the lease release
      (flip: emit the success report before releasing the lease)
  T10 a store the final doctor grades not VALID exits 2 with scoped recovery text, the change left for
      review and the lease released (flip: skip the final doctor)
  T11 worklog-append claims the next WL id with no status qualifier; an id inside a released span refuses
      exit 2, bytes untouched (flip: drop the released-span check)

Exit convention: 0 every assertion passes; 1 an assertion fails; 2 the harness cannot evaluate (git absent
or unusable, temporary storage unusable, or any unexpected harness fault), never a clean skip.
"""
import contextlib
import copy
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal as journal          # noqa: E402
import _opf_emit as emit            # noqa: E402
import _opf_record as record        # noqa: E402
import _opf_schema as schema        # noqa: E402
import _opf_write_guard as guard    # noqa: E402
import opf                          # noqa: E402

TOOLS = Path(__file__).resolve().parent
MACH = ".working/toml"
COUNTERS = MACH + "/counters.toml"
BI_INDEX = MACH + "/backlog_item.index.toml"
WORKLOG = MACH + "/worklog.toml"
VERSION = MACH + "/version.toml"
LEASE = MACH + "/lease.toml"
CREATE = ["create", "--type", "backlog_item", "--title", "an item", "--actor", "assistant:gate"]
APPEND = ["worklog-append", "--kind", "added", "--summary", "a fact", "--actor", "assistant:gate"]
KILL_POINTS = ("after-lock", "after-preimage-0", "after-preimage-1", "after-preimages", "torn:INTENT",
               "after-publish-INTENT", "torn-payload:0", "after-apply-0", "torn-payload:1", "after-apply-1",
               "torn:COMPLETE", "after-publish-COMPLETE")


class Harness(Exception):
    """A harness fault: the gate cannot evaluate (exit 2), never an assertion verdict."""


class Env:
    """The scrubbed environment every git and opf call in this gate runs under."""

    def __init__(self, base):
        home = base / "home"
        home.mkdir()
        self.vars = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "LC_ALL": "C",
                     "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "gate",
                     "GIT_AUTHOR_EMAIL": "gate@example.invalid", "GIT_COMMITTER_NAME": "gate",
                     "GIT_COMMITTER_EMAIL": "gate@example.invalid"}

    def git(self, root, *args):
        proc = subprocess.run(["git", "-C", str(root), "-c", "init.defaultBranch=main"] + list(args),
                              capture_output=True, text=True, timeout=120, env=self.vars)
        if proc.returncode != 0:
            raise Harness("fixture git {} failed: {}".format(args, proc.stderr.strip()))
        return proc.stdout


def cli(env, argv):
    """Run `opf <argv>` in-process under the scrubbed environment: (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env.vars, clear=True), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = opf.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def record_cli(env, root, args):
    return cli(env, ["record"] + list(args) + ["--root", str(root)])


def snapshot(root):
    """Every file and directory beneath root except .git, by content and mode."""
    result = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not (Path(directory) == Path(root) and d == ".git")]
        for name in sorted(dirs + files):
            path = Path(directory) / name
            st = path.lstat()
            rel = path.relative_to(root).as_posix()
            kind = ("file", path.read_bytes()) if stat.S_ISREG(st.st_mode) else (
                "dir",) if stat.S_ISDIR(st.st_mode) else ("other", stat.S_IFMT(st.st_mode))
            result[rel] = (stat.S_IMODE(st.st_mode), kind)
    return result


def read(root, rel):
    return (Path(root) / rel).read_bytes()


def model(root, rel):
    return tomllib.loads(read(root, rel).decode("utf-8"))


def write_commit(env, root, rel, data, message):
    (Path(root) / rel).write_bytes(data)
    env.git(root, "add", "--", rel)
    env.git(root, "commit", "-q", "-m", message)


def refused(result, needle):
    rc, out, err = result
    assert rc == 2, ("expected exit 2", rc, out[-800:], err[-800:])
    assert needle in err, ("refusal text", needle, err[-1200:])
    assert '"event": "recorded"' not in out, ("no success report on a refusal", out[-800:])


def recorded(result):
    rc, out, err = result
    assert rc == 0, ("expected exit 0", rc, out[-800:], err[-1600:])
    assert "left UNCOMMITTED in the working tree" in out, out[-800:]
    return out


class Fixtures:
    """A doctor-VALID template store, copied fresh for every case."""

    def __init__(self, base, env):
        self.base = base
        self.env = env
        self.count = 0
        self.template = base / "template"
        self.template.mkdir()
        env.git(self.template, "init", "-q")
        rc, out, err = cli(env, ["init", "--root", str(self.template)])
        if rc != 0:
            raise Harness("opf init on the template failed: " + err[-800:])
        env.git(self.template, "add", "-A")
        env.git(self.template, "commit", "-q", "-m", "init")
        rc, out, err = cli(env, ["render", "--root", str(self.template), "--write"])
        if rc != 0:
            raise Harness("opf render --write on the template failed: " + err[-800:])
        env.git(self.template, "add", "-A")
        env.git(self.template, "commit", "-q", "-m", "views")
        rc, out, err = cli(env, ["doctor", "--root", str(self.template)])
        if rc != 0:
            raise Harness("the template store is not doctor-VALID: " + out[-800:] + err[-800:])

    def case(self, name):
        self.count += 1
        root = self.base / "{:03d}-{}".format(self.count, name)
        shutil.copytree(self.template, root, symlinks=True)
        return root

    def commit_all(self, root, message):
        self.env.git(root, "add", "-A", "--", ".working")
        self.env.git(root, "commit", "-q", "-m", message)


# --- T1: the byte-reproduction precondition ---------------------------------------------------------------

def t1_precondition(fx):
    env = fx.env
    for rel, args in ((BI_INDEX, CREATE), (COUNTERS, CREATE), (WORKLOG, APPEND)):
        root = fx.case("t1-" + Path(rel).stem)
        write_commit(env, root, rel, b"# a hand-written note\n" + read(root, rel), "hand edit")
        before = snapshot(root)
        refused(record_cli(env, root, args), "not in canonical new-document form")
        assert snapshot(root) == before, ("T1 bytes untouched", rel)


# --- T2: round-trip through emit_checked ------------------------------------------------------------------

# Link targets must resolve (a dangling link is a C-LINKS finding the final doctor grades), so the new
# record, BI-1 in a fresh store, links itself.
LINKS = (("relates", "BI-1"), ("follows", "BI-1"))
REFS = (("url", "https://example.invalid/a?b=c", "why"), ("path", "docs/x.md:12", "where"))


def _t2_args():
    args = list(CREATE)
    for rel, rid in LINKS:
        args += ["--link", "{}={}".format(rel, rid)]
    for kind, locator, note in REFS:
        args += ["--ref", kind, locator, note]
    return args


def _lossy_emit(original):
    def lossy(document):
        doc = copy.deepcopy(document)
        for row in doc.get("record", []) if isinstance(doc, dict) else []:
            if isinstance(row, dict):
                row.pop("refs", None)
        return original(doc)
    return lossy


def t2_round_trip(fx):
    env = fx.env
    root = fx.case("t2-round-trip")
    recorded(record_cli(env, root, _t2_args()))
    raw = read(root, BI_INDEX)
    parsed = tomllib.loads(raw.decode("utf-8"))
    rec = parsed["record"][-1]
    assert rec["links"] == [{"rel": r, "id": i} for r, i in LINKS], rec
    assert rec["refs"] == [{"kind": k, "locator": l, "note": n} for k, l, n in REFS], rec
    assert emit.emit_checked(parsed).encode("utf-8") == raw, "T2 published bytes are canonical"
    # A lossy emitter (it drops refs) must be refused before any write.
    root = fx.case("t2-lossy")
    before = snapshot(root)
    with patch.object(emit, "emit", _lossy_emit(emit.emit)):
        result = record_cli(env, root, _t2_args())
    refused(result, "does not round-trip")
    assert snapshot(root) == before, "T2 a lossy emission writes nothing"


def flip_t2():
    return patch.object(record, "_emit_bytes", lambda document: emit.emit(document).encode("utf-8"))


# --- T3: the allowed-delta postcondition -------------------------------------------------------------------

def t3_postcondition(fx):
    env = fx.env
    root = fx.case("t3-extra-field")
    recorded(record_cli(env, root, CREATE))
    fx.commit_all(root, "first record")
    original = record._PLANNERS["create"]

    def tampering(req, ctx, operand, now):
        plan = original(req, ctx, operand, now)
        operand.new_model["record"][0]["title"] = "silently rewritten"
        return plan

    before = snapshot(root)
    with patch.dict(record._PLANNERS, {"create": tampering}):
        result = record_cli(env, root, CREATE)
    refused(result, "postcondition failed")
    assert snapshot(root) == before, "T3 bytes untouched"


# --- T4: allocation (sequential claims, the known-complete proof) -------------------------------------------

def t4_allocation(fx):
    env = fx.env
    root = fx.case("t4-sequential")
    prior = model(root, COUNTERS)["counters"]
    recorded(record_cli(env, root, CREATE))
    fx.commit_all(root, "BI-1")
    recorded(record_cli(env, root, CREATE))
    ids = [r["id"] for r in model(root, BI_INDEX)["record"]]
    after = model(root, COUNTERS)["counters"]
    assert ids == ["BI-1", "BI-2"], ids
    assert after["BI"] == prior["BI"] + 2 and schema.check_monotonic(prior, after) == [], (prior, after)
    assert {k: v for k, v in after.items() if k != "BI"} == {k: v for k, v in prior.items() if k != "BI"}
    # A counters map missing an enabled namespace can never read as high-water 0.
    root = fx.case("t4-missing-namespace")
    counters = model(root, COUNTERS)
    del counters["counters"]["BI"]
    write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "drop BI counter")
    before = snapshot(root)
    refused(record_cli(env, root, CREATE), "cannot license an allocation")
    assert snapshot(root) == before, "T4 bytes untouched"


def flip_t4():
    def unproven(ctx, counters_model):
        high, _findings = schema.validate_counters(counters_model)
        return high, []
    return patch.object(record, "_counter_state", unproven)


# --- T5: crash durability (kill-injection at each journal step, then reconciliation) --------------------

_CHILD = """
import datetime, os, sys
from pathlib import Path
sys.path.insert(0, {tools!r})
import opf
assert opf._bootstrap() == 0
import _opf_record as record
record._clock_now = lambda: datetime.datetime(2026, 9, 27, 12, 0, 0, tzinfo=datetime.timezone.utc)
{flip}
sys.exit(opf.main(["record"] + sys.argv[2:] + ["--root", sys.argv[1]]))
"""

# The T5 flip, applied inside the killed child: counters.toml is written directly, OUTSIDE the journaled
# transaction, and only the record file is journaled. _t5_flip holds the prelude the children run with.
_t5_flip = [""]
FLIP_T5 = """
_journaled = record._publish
def _publish(ctx, plan, subcommand):
    counters = plan.operands[0]
    (Path(ctx.res.store_root) / counters.rel).write_bytes(counters.new_raw)
    plan.operands = plan.operands[1:]
    return _journaled(ctx, plan, subcommand)
record._publish = _publish
"""


def child(env, root, args, kill=None, flip=""):
    script = _CHILD.format(tools=str(TOOLS), flip=flip)
    child_env = dict(env.vars)
    if kill is not None:
        child_env[journal.KILL_ENV] = kill
    return subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)] + list(args),
                          capture_output=True, text=True, timeout=180, env=child_env)


def journal_states(root):
    jroot = Path(root) / record.JOURNAL_REL
    if not jroot.is_dir():
        return {}
    jr_fd = journal.open_journal_root_from_path(str(root), record.JOURNAL_REL)
    try:
        return {t.name: journal.classify_state(jr_fd, t) for t in journal._journal_txn_dirs(jr_fd, jroot)}
    finally:
        os.close(jr_fd)


def t5_crash(fx):
    env = fx.env
    flip = _t5_flip[0]
    operands = (COUNTERS, BI_INDEX)
    reference = fx.case("t5-reference")
    proc = child(env, reference, CREATE)
    assert proc.returncode == 0 and '"event": "recorded"' in proc.stdout, (proc.returncode, proc.stderr[-800:])
    post = {rel: read(reference, rel) for rel in operands}
    for point in KILL_POINTS:
        root = fx.case("t5-" + point.replace(":", "-"))
        pre = {rel: read(root, rel) for rel in operands}
        proc = child(env, root, CREATE, kill=point, flip=flip)
        assert proc.returncode == 137, ("T5 the child is killed at", point, proc.returncode, proc.stderr[-800:])
        assert '"event": "recorded"' not in proc.stdout, ("T5 a killed run reports no id", point)
        refused(record_cli(env, root, CREATE), "was reconciled")
        states = journal_states(root)
        assert not any(s == "open" for s in states.values()), ("T5 every transaction terminal", point, states)
        assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), ("T5 the journal lock released", point)
        now = {rel: read(root, rel) for rel in operands}
        complete = any(s == "complete" for s in states.values())
        assert now == (post if complete else pre), (
            "T5 exactly the prestate or the poststate, the poststate iff COMPLETE", point, states)


# --- T9: the single-writer lease and release-before-success -------------------------------------------------

PEER_LEASE = (b'acquired_at = "2026-09-27T00:00:00Z"\nholder = "peer-runner"\noperation = "record"\n'
              b'schema = 1\n')


def t9_lease(fx):
    env = fx.env
    # A held lease (untracked, as a live peer leaves it) refuses and is never seized.
    root = fx.case("t9-held")
    (Path(root) / LEASE).write_bytes(PEER_LEASE)
    before = snapshot(root)
    refused(record_cli(env, root, CREATE), "never seized")
    assert snapshot(root) == before and read(root, LEASE) == PEER_LEASE, "T9 the peer lease survives"
    # On success the lease is released BEFORE the report: observe stdout at the moment of release.
    root = fx.case("t9-order")
    seen = []
    original = guard.release_lease

    def observing(root_fd, machine_rel, payload, verb):
        seen.append(sys.stdout.getvalue())
        return original(root_fd, machine_rel, payload, verb)

    with patch.object(guard, "release_lease", observing):
        out = recorded(record_cli(env, root, CREATE))
    assert len(seen) == 1 and "recorded" not in seen[0], ("T9 nothing reported before release", seen)
    assert "BI-1" in out and not (Path(root) / LEASE).exists(), "T9 reported after the lease is gone"
    # A failed release reports no success.
    root = fx.case("t9-release-fails")

    def failing(*_args):
        raise guard.WriteGuardError("synthetic release failure")

    with patch.object(guard, "release_lease", failing):
        result = record_cli(env, root, CREATE)
    refused(result, "synthetic release failure")
    assert "lease release failed" in result[2], result[2][-800:]


def flip_t9():
    def report_then_release(ctx, lease, report):
        record._emit_success(report)
        record._release(ctx, lease)
    return patch.object(record, "_conclude", report_then_release)


# --- T10: the final doctor -----------------------------------------------------------------------------------

def t10_final_doctor(fx):
    env = fx.env
    root = fx.case("t10-doctor-invalid")
    env.git(root, "rm", "-q", "--", "CHANGELOG.md")
    env.git(root, "commit", "-q", "-m", "drop the changelog")
    counters_before = read(root, COUNTERS)
    result = record_cli(env, root, CREATE)
    refused(result, "NOT doctor-VALID")
    assert "C-CHANGELOG-GATES" in result[2] and "restore --staged --worktree" in result[2], result[2][-1600:]
    assert "whole-tree restore" in result[2], "T10 the recovery text is scoped"
    assert read(root, COUNTERS) != counters_before, "T10 the published change is left for review"
    assert not (Path(root) / LEASE).exists(), "T10 the lease is released on the failure path"


def flip_t10():
    return patch.object(record, "_final_gate", lambda root: None)


# --- T11: worklog-append and the released span -----------------------------------------------------------------

def t11_worklog(fx):
    env = fx.env
    root = fx.case("t11-tail")
    recorded(record_cli(env, root, APPEND))
    entries = model(root, WORKLOG)["entry"]
    assert [e["id"] for e in entries] == ["WL-1"] and "status" not in entries[0], entries
    assert model(root, COUNTERS)["counters"]["WL"] == 1
    fx.commit_all(root, "WL-1")
    # A release whose frozen span reaches past the counter: the next claim, WL-2, lies inside it.
    version = model(root, VERSION)
    version["release"] = [{"version": "0.1.0", "date": "2026-09-01T00:00:00Z",
                           "worklog_span": ["WL-1", "WL-2"], "coverage_digest": "sha256:" + "0" * 64}]
    write_commit(env, root, VERSION, emit.emit_checked(version).encode("utf-8"), "a frozen span")
    before = snapshot(root)
    refused(record_cli(env, root, APPEND), "already-released span")
    assert snapshot(root) == before, "T11 bytes untouched"


def flip_t11():
    return patch.object(record, "_check_released_span", lambda version_model, wl_number: None)


def flip_t1():
    return patch.object(record, "_require_canonical", lambda operand: None)


def flip_t3():
    return patch.object(record, "_postcondition", lambda plan, counters_rel: None)


# --- the runner ------------------------------------------------------------------------------------------------

TESTS = (
    ("T1-precondition-byte-reproduction", t1_precondition, flip_t1),
    ("T2-round-trip-emit-checked", t2_round_trip, flip_t2),
    ("T3-allowed-delta-postcondition", t3_postcondition, flip_t3),
    ("T4-allocation-known-complete", t4_allocation, flip_t4),
    ("T5-crash-prestate-or-poststate", t5_crash, None),     # its flip runs inside the killed child
    ("T9-lease-release-before-success", t9_lease, flip_t9),
    ("T10-final-doctor", t10_final_doctor, flip_t10),
    ("T11-worklog-released-span", t11_worklog, flip_t11),
)


def self_test(red_on_revert=False):
    if shutil.which("git") is None:
        print("OPF-RECORD SELF-TEST ERROR: git is not on PATH (the fixtures need real commits); exit 2",
              file=sys.stderr)
        return 2
    ran, failures = [], []

    def check(name, test):
        ran.append(name)
        try:
            test()
        except AssertionError as exc:
            failures.append("{}: {}".format(name, exc))

    def discriminate(name, test, flip):
        """The flip must turn the test red with an assertion (never an unrelated harness fault), and the
        unflipped test must stay green around it."""
        test()
        try:
            with flip():
                test()
        except AssertionError:
            pass
        else:
            raise AssertionError(name + " survived its flip")
        test()

    base = Path(tempfile.mkdtemp(prefix="opf-record-gate-")).resolve()
    try:
        if opf._bootstrap() != 0:
            raise Harness("opf bootstrap failed")
        env = Env(base)
        fx = Fixtures(base, env)
        for name, test, _flip in TESTS:
            check(name, lambda test=test: test(fx))
        if red_on_revert:
            for name, test, flip in TESTS:
                if flip is not None:
                    check("red-on-revert-" + name,
                          lambda name=name, test=test, flip=flip: discriminate(name, lambda: test(fx), flip))
            check("red-on-revert-T5-crash-prestate-or-poststate",
                  lambda: discriminate("T5", lambda: t5_crash(fx),
                                       lambda: _child_flip(fx)))
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a verdict
        print("OPF-RECORD SELF-TEST ERROR: {!r}".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("OPF-RECORD SELF-TEST: exercised " + ", ".join(ran))
    if failures:
        for failure in failures:
            print("FAIL: " + failure, file=sys.stderr)
        return 1
    print("OPF-RECORD SELF-TEST PASSED")
    return 0


@contextlib.contextmanager
def _child_flip(fx):
    """The T5 flip as a context: while active, t5_crash's killed children run with FLIP_T5."""
    _t5_flip[0] = FLIP_T5
    try:
        yield
    finally:
        _t5_flip[0] = ""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["--self-test"], ["--self-test", "--red-on-revert"]):
        print("check_opf_record: expected --self-test [--red-on-revert]", file=sys.stderr)
        return 2
    return self_test(red_on_revert="--red-on-revert" in args)


if __name__ == "__main__":
    sys.exit(main())
