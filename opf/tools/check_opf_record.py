#!/usr/bin/env python3
"""OPF record-authoring gate (spec 8.8): `opf record` behaviour, and red-on-revert discriminators.

  check_opf_record.py --self-test                    the fixture suite (T1-T16)
  check_opf_record.py --self-test --red-on-revert    the same, plus each test's flip must turn it red

There is no live-adopter leg (this repository is not an OPFiles adopter), so the whole assurance rides the
self-test. Every fixture is a real store built beneath this gate's own mkdtemp root: `opf init`, a commit,
`opf render --write`, a commit, then per-case edits committed so the cleanliness gate sees a clean tree.
Each case runs on its own copy of that template; the root is removed in a finally.

  T1  a comment-bearing index, counters, or worklog refuses exit 2 with every byte untouched
      (flip: drop the byte-reproduction precondition)
  T2  create with links and refs round-trips and the published bytes are canonical; with a lossy emitter
      the verb's serializer (_emit_bytes) itself refuses, and the whole run refuses exit 2 with every byte
      untouched (flip: publish raw emit output instead of emit_checked; the independent postcondition
      still refuses the end-to-end run, so the flip turns red on the serializer's own refusal)
  T3  a plan that mutates one extra field of an existing record, or the initial status or requested title
      of the new record, or the requested summary of a new worklog entry, refuses exit 2, bytes untouched
      (flip: drop the allowed-delta postcondition)
  T4  two sequential creates claim BI-1 then BI-2 (and their own worklog entries WL-1, WL-2) with
      monotonic counters; a counters map missing an enabled namespace refuses (flip: drop the
      known-complete proof)
  T5  a kill at each journal step of create and of an assistant transition (three operands: counters,
      index, worklog) and of a maintainer done-with-receipt (four operands: counters, backlog index, done
      index, worklog) leaves the killed run's lease, which refuses the next run before any recovery write;
      once the operator releases it, reconciliation leaves the operands exactly the prestate or exactly the
      poststate, the poststate iff the transaction is COMPLETE, and no killed run reports an id (flip: write
      counters outside the journaled transaction)
  T6  an assistant `transition BI done` lands done/proposed with zero receipts; an assistant
      done-with-receipt refuses before the store is resolved, and a maintainer `transition BI done`
      refuses, both with every byte untouched; a maintainer done-with-receipt (ratifying done/proposed, or
      from active) lands unqualified done with exactly one receipt_of receipt and its own worklog entry,
      doctor VALID once committed (flips: derive the bare status for an assistant; drop the
      maintainer-only check; drop the done-only-through-its-receipt guard)
  T7  a maintainer rejection without --reason, of a proposal the worklog does not record, to another
      state, or by an assistant refuses with every byte untouched; with a reason it returns to the
      recorded and committed pre-proposal state and records the reason (flips: the check sees a
      placeholder reason; guess the pre-proposal state)
  T8  two branches that each create from the same committed counters conflict on the store paths; a
      canonical union resolution with the duplicate BI-1 is doctor INVALID with a C-ID-SPACE finding
      (flip: disable check_unique_ids)
  T9  a held lease refuses exit 2 and is never seized; success is reported only after the lease release;
      a failed release reports the final gate's recorded outcome, doctor-VALID after a create and the
      accepted pending cannot-evaluate after an actor-dependent transition (flips: emit the success report
      before releasing the lease; the unconditional doctor-VALID release-failure text)
  T10 a store the final doctor grades not VALID exits 2 with scoped recovery text, the change left for
      review and the lease released (flip: skip the final doctor)
  T11 worklog-append claims the next WL id with no status qualifier; an id inside a released span refuses
      exit 2, bytes untouched (flip: drop the released-span check)
  T12 an interrupted journal while another live run holds the shared lease (taken through the upgrade
      verb) refuses exit 2 with every byte untouched and the peer's lease intact (flip: recover without
      the lease)
  T13 an interrupted journal whose operand was edited after the interruption, on the prestate side or the
      poststate side, refuses exit 2 naming the path, every byte untouched; once the edit is undone the
      next run reconciles (flip: drop the intervening-edit check)
  T14 create with an unknown or path-like --type refuses with the enabled-baseline-types message, not an
      operand read failure (flip: skip the --type check before the operand read)
  T15 a run whose journal lock release fails after COMPLETE still renders, runs doctor, and reports, and
      says the lock was left; the next run reconciles the leftover lock and names the COMPLETE
      transaction without claiming render and doctor never ran (flip: the old never-ran outcome text)
  T16 a rejection whose worklog lifecycle line disagrees with the committed history (the line's FROM state
      rewritten canonically and committed, doctor VALID; or a history that reached the proposal again from
      another state), whose committed snapshot before the proposal commit does not parse, or whose proposal
      is not committed refuses with every byte untouched (flip: trust the worklog line alone)

Exit convention: 0 every assertion passes; 1 an assertion fails; 2 the harness cannot evaluate (git absent
or unusable, temporary storage unusable, or any unexpected harness fault), never a clean skip.
"""
import contextlib
import copy
import datetime
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
import _opf_check as opf_check      # noqa: E402
import _opf_emit as emit            # noqa: E402
import _opf_record as record        # noqa: E402
import _opf_schema as schema        # noqa: E402
import _opf_write_guard as guard    # noqa: E402
import opf                          # noqa: E402

TOOLS = Path(__file__).resolve().parent
MACH = ".working/toml"
COUNTERS = MACH + "/counters.toml"
BI_INDEX = MACH + "/backlog_item.index.toml"
DN_INDEX = MACH + "/done.index.toml"
WORKLOG = MACH + "/worklog.toml"
VERSION = MACH + "/version.toml"
LEASE = MACH + "/lease.toml"
RECORDED_EVENT = '"event": "recorded"'
CREATE = ["create", "--type", "backlog_item", "--title", "an item", "--actor", "assistant:gate"]
APPEND = ["worklog-append", "--kind", "added", "--summary", "a fact", "--actor", "assistant:gate"]


def kill_points(n):
    """The journal engine's injection hooks, in order, over one transaction of n operands."""
    points = ["after-lock"] + ["after-preimage-{}".format(i) for i in range(n)]
    points += ["after-preimages", "torn:INTENT", "after-publish-INTENT"]
    for i in range(n):
        points += ["torn-payload:{}".format(i), "after-apply-{}".format(i)]
    return tuple(points + ["torn:COMPLETE", "after-publish-COMPLETE"])


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

    def run_git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), "-c", "init.defaultBranch=main"] + list(args),
                              capture_output=True, text=True, timeout=120, env=self.vars)

    def git(self, root, *args):
        proc = self.run_git(root, *args)
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

    def case(self, name, source=None):
        """A fresh copy of the template, or of `source` (a case built from it)."""
        self.count += 1
        root = self.base / "{:03d}-{}".format(self.count, name)
        shutil.copytree(self.template if source is None else source, root, symlinks=True)
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
    # A lossy emitter (it drops refs): the verb's one serializer must itself refuse it. This isolated
    # check is the emit_checked discriminator, because end to end the independent postcondition also
    # refuses a lossy emission, so the run below cannot tell which guard caught it.
    sample = dict(schema=1, record=[dict(id="BI-1", refs=[dict(kind=k, locator=l, note=n) for k, l, n in REFS])])
    with patch.object(emit, "emit", _lossy_emit(emit.emit)):
        try:
            record._emit_bytes(sample)
            serializer_refused = False
        except record.RecordError:
            serializer_refused = True
    assert serializer_refused, "T2 the serializer refuses a lossy emission"
    # End to end, a lossy emitter publishes nothing: exit 2 and every byte untouched, asserted on the
    # outcome, not on which guard's message names it.
    root = fx.case("t2-lossy")
    before = snapshot(root)
    with patch.object(emit, "emit", _lossy_emit(emit.emit)):
        rc, out, err = record_cli(env, root, _t2_args())
    assert snapshot(root) == before, "T2 a lossy emission writes nothing"
    assert rc == 2 and RECORDED_EVENT not in out, ("T2 a lossy emission is refused", rc, err[-800:])


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
    # The newly appended row is checked against a delta derived from the request, never against the
    # planner's own row: tampering with it after planning must refuse too, in both planners.
    for label, sub, args, key, field, value in (
            ("initial-status", "create", CREATE, "record", "status", "active"),
            ("requested-title", "create", CREATE, "record", "title", "not the requested title"),
            ("worklog-summary", "worklog-append", APPEND, "entry", "summary", "not the requested summary")):
        root = fx.case("t3-appended-" + label)

        def appended_tampering(req, ctx, operand, now, planner=record._PLANNERS[sub], key=key, field=field,
                               value=value):
            plan = planner(req, ctx, operand, now)
            operand.new_model[key][-1][field] = value
            return plan

        before = snapshot(root)
        with patch.dict(record._PLANNERS, {sub: appended_tampering}):
            result = record_cli(env, root, args)
        refused(result, "postcondition failed")
        assert snapshot(root) == before, ("T3 bytes untouched", label)


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
    assert after["BI"] == prior["BI"] + 2 and after["WL"] == prior["WL"] + 2, (prior, after)
    assert schema.check_monotonic(prior, after) == [], (prior, after)
    assert {k: v for k, v in after.items() if k not in ("BI", "WL")} == {
        k: v for k, v in prior.items() if k not in ("BI", "WL")}
    entries = model(root, WORKLOG)["entry"]
    assert [(e["id"], e["detail"]) for e in entries] == [
        ("WL-1", "opf-record create BI-1 open"), ("WL-2", "opf-record create BI-2 open")], entries
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


def _t5_scenarios(fx):
    """(label, base store or None for the template, args, operands in journal order): create over the
    template; an assistant transition (three operands) and a maintainer done-with-receipt (four operands)
    over a committed store whose BI-1 is active."""
    active = fx.case("t5-base-active")
    with ticking():
        step(fx, active, ["create", "--type", "backlog_item", "--title", "k"] + MAINTAINER, "BI-1")
        step(fx, active, ["transition", "BI-1", "active"] + MAINTAINER, "BI-1 active")
    return (("create", None, CREATE, (COUNTERS, BI_INDEX, WORKLOG)),
            ("transition", active, ["transition", "BI-1", "done"] + ASSISTANT, (COUNTERS, BI_INDEX, WORKLOG)),
            ("done-with-receipt", active, ["done-with-receipt", "BI-1"] + MAINTAINER,
             (COUNTERS, BI_INDEX, DN_INDEX, WORKLOG)))


def t5_crash(fx):
    for label, base, args, operands in _t5_scenarios(fx):
        _t5_matrix(fx, label, base, args, operands)


def _t5_matrix(fx, label, base, args, operands):
    env = fx.env
    flip = _t5_flip[0]
    reference = fx.case("t5-{}-reference".format(label), base)
    proc = child(env, reference, args)
    assert proc.returncode == 0 and '"event": "recorded"' in proc.stdout, (label, proc.returncode,
                                                                           proc.stderr[-800:])
    post = {rel: read(reference, rel) for rel in operands}
    for hook in kill_points(len(operands)):
        point = (label, hook)     # named in every assertion below
        root = fx.case("t5-{}-{}".format(label, hook.replace(":", "-")), base)
        pre = {rel: read(root, rel) for rel in operands}
        assert all(pre[rel] != post[rel] for rel in operands), ("T5 the operation rewrites every operand", point)
        retained = set(journal_states(root))     # the base's own completed transactions, kept as evidence
        proc = child(env, root, args, kill=hook, flip=flip)
        assert proc.returncode == 137, ("T5 the child is killed at", point, proc.returncode, proc.stderr[-800:])
        assert '"event": "recorded"' not in proc.stdout, ("T5 a killed run reports no id", point)
        # The killed run leaves its lease, and a held lease refuses the next run before any recovery write.
        assert (Path(root) / LEASE).exists(), ("T5 the killed run leaves its lease", point)
        held = snapshot(root)
        refused(record_cli(env, root, CREATE), "runs only under the single-writer lease")
        assert snapshot(root) == held, ("T5 a held lease refuses before any recovery write", point)
        # The operator's explicit reconciliation step (no opf run is live): release the leftover lease.
        (Path(root) / LEASE).unlink()
        refused(record_cli(env, root, CREATE), "was reconciled")
        assert not (Path(root) / LEASE).exists(), ("T5 recovery releases the lease it took", point)
        states = journal_states(root)
        assert not any(s == "open" for s in states.values()), ("T5 every transaction terminal", point, states)
        assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), ("T5 the journal lock released", point)
        now = {rel: read(root, rel) for rel in operands}
        complete = any(s == "complete" for name, s in states.items() if name not in retained)
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
    assert "reached doctor-VALID, but the lease release failed" in result[2], result[2][-800:]
    # After a transition whose final gate accepted only the pending cannot-evaluate, a failed release reports
    # that recorded outcome, never doctor-VALID.
    root = fx.case("t9-release-fails-pending")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        with patch.object(guard, "release_lease", failing):
            result = record_cli(env, root, ["transition", "BI-1", "done"] + ASSISTANT)
    refused(result, "synthetic release failure")
    err = result[2]
    assert "carrying only the accepted pending cannot-evaluate of BI-1 active -> done/proposed" in err, err[-800:]
    assert "lease release failed" in err and "doctor-VALID" not in err, err[-800:]


def flip_t9():
    def report_then_release(ctx, lease, report):
        record._emit_success(report)
        record._release(ctx, lease)
    return patch.object(record, "_conclude", report_then_release)


def flip_t9_outcome():
    return patch.object(record, "_release_failure_text", lambda report: (
        "opf record: the store reached doctor-VALID, but the lease release failed; nothing is offered as "
        "recorded. Confirm no opf run is live (spec 5.7) and reconcile the lease before any further action."))


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
    return patch.object(record, "_final_gate", lambda root, transition=None: [])


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
    refused_untouched(env, root, APPEND, "already-released span")


def flip_t11():
    return patch.object(record, "_check_released_span", lambda version_model, wl_number: None)


# --- T12, T13: recovery runs only under the lease and never overwrites an intervening edit ---------------

def _interrupted(fx, name, point="after-apply-0"):
    """A store whose create was killed at `point` (at after-apply-0 counters.toml holds its poststate and
    the index its prestate), with the dead run's leftover lease released as the operator's own step."""
    root = fx.case(name)
    proc = child(fx.env, root, CREATE, kill=point)
    assert proc.returncode == 137, ("the child is killed at", point, proc.returncode, proc.stderr[-800:])
    (Path(root) / LEASE).unlink()
    return root


def t12_recovery_lease(fx):
    env = fx.env
    root = _interrupted(fx, "t12-live-peer")
    # A live peer takes the shared lease through the upgrade verb, as `opf upgrade` does.
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        peer = guard.acquire_lease(root_fd, MACH, "upgrade")
        try:
            refused_untouched(env, root, CREATE, "runs only under the single-writer lease")
            assert read(root, LEASE) == peer, "T12 the peer's lease is intact, never seized"
        finally:
            guard.release_lease(root_fd, MACH, peer, "upgrade")
    finally:
        os.close(root_fd)
    refused(record_cli(env, root, CREATE), "was reconciled")
    assert not any(s == "open" for s in journal_states(root).values()), "T12 reconciled once the peer is gone"


def flip_t12():
    return patch.object(record, "_with_recovery_lease", lambda ctx, pending, recover: recover())


def t13_intervening_edit(fx):
    env = fx.env
    for rel, side in ((BI_INDEX, "prestate"), (COUNTERS, "poststate")):
        root = _interrupted(fx, "t13-" + side)
        original = read(root, rel)
        (Path(root) / rel).write_bytes(original + b"# an owner's note written after the interruption\n")
        before = snapshot(root)
        result = record_cli(env, root, CREATE)
        assert snapshot(root) == before, ("T13 the intervening edit is never overwritten", rel)
        refused(result, "an intervening edit")
        assert rel in result[2], ("T13 the edited path is named", rel, result[2][-800:])
        # Once the owner undoes the edit, the next run reconciles.
        (Path(root) / rel).write_bytes(original)
        refused(record_cli(env, root, CREATE), "was reconciled")
        assert not any(s == "open" for s in journal_states(root).values()), ("T13 reconciled", rel)


def flip_t13():
    return patch.object(record, "_unexplained_operands", lambda root_fd, jr_fd, txns: [])


# --- T14: --type is checked before any operand read ---------------------------------------------------------

def t14_type_before_read(fx):
    env = fx.env
    root = fx.case("t14-type")
    before = snapshot(root)
    for rtype in ("bogus", "../counters"):
        args = ["create", "--type", rtype, "--title", "t", "--actor", "maintainer"]
        refused(record_cli(env, root, args), "enabled baseline record types only")
        assert snapshot(root) == before, ("T14 bytes untouched", rtype)


def flip_t14():
    return patch.object(record, "_check_request", lambda req, ctx: None)


# --- T6-T8: transitions, the done receipt, and the parallel-branch collision --------------------------------

ASSISTANT = ["--actor", "assistant:gate"]
MAINTAINER = ["--actor", "maintainer:owner"]


class Ticker:
    """A strictly advancing clock for one case's in-process runs: a transition's updated_at must follow the
    record's recorded timestamps, and two real runs can fall in the same second."""

    def __init__(self):
        self.now = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)

    def __call__(self):
        self.now += datetime.timedelta(minutes=1)
        return self.now


def ticking():
    return patch.object(record, "_clock_now", Ticker())


def step(fx, root, args, message):
    """One recorded operation, then a commit, so the next operation's cleanliness gate passes."""
    out = recorded(record_cli(fx.env, root, args))
    fx.commit_all(root, message)
    return out


def row(root, rid):
    rows = [r for r in model(root, BI_INDEX)["record"] if r["id"] == rid]
    assert len(rows) == 1, (rid, rows)
    return rows[0]


def receipts_of(root, rid):
    link = {"rel": "receipt_of", "id": rid}
    return [r for r in model(root, DN_INDEX).get("record", []) if link in r.get("links", [])]


def lifecycle(root):
    return [e["detail"] for e in model(root, WORKLOG)["entry"]]


def doctor_valid(env, root):
    rc, out, err = cli(env, ["doctor", "--root", str(root)])
    assert rc == 0, ("doctor VALID at rest", rc, out[-1600:], err[-800:])


def refused_untouched(env, root, args, needle):
    """A refusal that writes nothing. The byte comparison is asserted BEFORE the refusal text, so a guard
    removed under a flip turns this red on what was written, never on a changed message."""
    before = snapshot(root)
    result = record_cli(env, root, args)
    assert snapshot(root) == before, ("bytes untouched on refusal", args, result[0], result[2][-800:])
    refused(result, needle)


def refused_before_store(env, root, args):
    """A usage refusal that happens before the store is resolved at all: the resolver is never called and
    no byte changes (asserted before any message)."""
    before = snapshot(root)
    calls = []
    original = record._opf_store.resolve_store

    def observing(*a, **k):
        calls.append(a)
        return original(*a, **k)

    with patch.object(record._opf_store, "resolve_store", observing):
        rc, out, err = record_cli(env, root, args)
    assert calls == [], ("refused before the store is resolved", args, rc, err[-800:])
    assert snapshot(root) == before and rc == 2, ("refused with nothing written", args, rc)
    assert '"event": "recorded"' not in out, out[-800:]


def t6_done_with_receipt(fx):
    env = fx.env
    root = fx.case("t6-ratify")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        out = step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        assert "CANNOT-EVALUATE" not in out, "an actor-independent transition leaves doctor VALID"
        out = step(fx, root, ["transition", "BI-1", "done"] + ASSISTANT, "BI-1 done/proposed")
        assert row(root, "BI-1")["status"] == "done/proposed" and receipts_of(root, "BI-1") == [], "T6 proposed"
        assert model(root, DN_INDEX).get("record", []) == [] and model(root, COUNTERS)["counters"]["DN"] == 0
        assert "until this change is committed" in out and "'active' -> 'done/proposed'" in out, out[-1200:]
        doctor_valid(env, root)
        refused_before_store(env, root, ["done-with-receipt", "BI-1"] + ASSISTANT)
        refused_untouched(env, root, ["transition", "BI-1", "done"] + MAINTAINER, "done-with-receipt")
        step(fx, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "BI-1 done")
        receipts = receipts_of(root, "BI-1")
        assert row(root, "BI-1")["status"] == "done" and [r["id"] for r in receipts] == ["DN-1"], receipts
        assert receipts[0]["status"] == "recorded" and receipts[0]["actor"] == {"kind": "maintainer", "id": "owner"}
        assert len(model(root, DN_INDEX)["record"]) == 1 and model(root, COUNTERS)["counters"]["DN"] == 1
        assert lifecycle(root) == ["opf-record create BI-1 open", "opf-record transition BI-1 open -> active",
                                   "opf-record transition BI-1 active -> done/proposed",
                                   "opf-record transition BI-1 done/proposed -> done\nreceipt: DN-1"], lifecycle(root)
        doctor_valid(env, root)
        refused_untouched(env, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "active or done/proposed")
    root = fx.case("t6-from-active")
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "m"] + MAINTAINER, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + MAINTAINER, "BI-1 active")
        step(fx, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "BI-1 done")
        assert row(root, "BI-1")["status"] == "done" and [r["id"] for r in receipts_of(root, "BI-1")] == ["DN-1"]
        doctor_valid(env, root)


def flip_t6_bare():
    return patch.object(record, "_derived_status", lambda kind, spec, cur_state, target: target)


def flip_t6_maintainer():
    return patch.object(record, "_require_maintainer", lambda actor: None)


def flip_t6_receipt():
    return patch.object(record, "_require_receipt_path", lambda rtype, to_status: None)


def t7_rejection(fx):
    env = fx.env
    root = fx.case("t7-reject")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        step(fx, root, ["transition", "BI-1", "done"] + ASSISTANT, "BI-1 done/proposed")
        refused_untouched(env, root, ["transition", "BI-1", "active"] + MAINTAINER, "recorded reason")
        refused_untouched(env, root, ["transition", "BI-1", "open", "--reason", "x"] + MAINTAINER,
                          "actual pre-proposal state")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + ASSISTANT,
                          "only a maintainer")
        step(fx, root, ["transition", "BI-1", "active", "--reason", "the fix did not hold"] + MAINTAINER, "rejected")
        entry = model(root, WORKLOG)["entry"][-1]
        assert row(root, "BI-1")["status"] == "active" and receipts_of(root, "BI-1") == []
        assert entry["detail"] == "opf-record transition BI-1 done/proposed -> active\nreason: the fix did not hold"
        assert entry["actor"] == {"kind": "maintainer", "id": "owner"}, entry
        doctor_valid(env, root)
    # A proposal made outside this verb: the worklog records no pre-proposal state, so it cannot be rejected.
    root = fx.case("t7-unrecorded")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        index = model(root, BI_INDEX)
        index["record"][0].update(status="done/proposed", updated_at="2026-09-01T00:02:30Z")
        write_commit(env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "proposed by hand")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "no pre-proposal state")


def flip_t7_reason():
    return patch.object(record, "_checked_reason", lambda req: req.values.get("--reason") or "placeholder")


def flip_t7_pre():
    return patch.object(record, "_pre_proposal_state", lambda entries, rid, status: "active")


def _proposed(fx, root, via):
    """BI-1 created and moved to active by an assistant, then proposed at `via` (done or dropped), each
    step committed: the proposing lifecycle line is the verb's own."""
    step(fx, root, CREATE, "BI-1")
    step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
    step(fx, root, ["transition", "BI-1", via] + ASSISTANT, "BI-1 {}/proposed".format(via))


def _set_status(fx, root, status, message):
    """A canonical hand edit of BI-1's status, committed (history the verb did not write)."""
    index = model(root, BI_INDEX)
    index["record"][0]["status"] = status
    write_commit(fx.env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), message)


def t16_corroborated_rejection(fx):
    env = fx.env
    # The reproduction: only the proposing line's FROM state is rewritten, canonically, and committed with
    # its re-rendered views, so doctor is VALID and byte reproduction passes.
    root = fx.case("t16-forged-line")
    with ticking():
        _proposed(fx, root, "dropped")
        worklog = model(root, WORKLOG)
        genuine = "opf-record transition BI-1 active -> dropped/proposed"
        assert worklog["entry"][-1]["detail"] == genuine, worklog["entry"][-1]
        worklog["entry"][-1]["detail"] = "opf-record transition BI-1 open -> dropped/proposed"
        write_commit(env, root, WORKLOG, emit.emit_checked(worklog).encode("utf-8"), "a forged proposing line")
        rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
        assert rc == 0, ("T16 the views re-render", rc, err[-800:])
        env.git(root, "add", "-A")
        env.git(root, "commit", "-q", "-m", "views")
        doctor_valid(env, root)
        for state in ("open", "active"):
            refused_untouched(env, root, ["transition", "BI-1", state, "--reason", "reject"] + MAINTAINER,
                              "altered or stale")
        assert row(root, "BI-1")["status"] == "dropped/proposed"
    # Stale: the committed history reaches the proposal again from another state after the verb's line.
    root = fx.case("t16-stale-line")
    with ticking():
        _proposed(fx, root, "done")
        _set_status(fx, root, "open", "BI-1 back to open by hand")
        _set_status(fx, root, "done/proposed", "BI-1 proposed again by hand")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "altered or stale")
    # Malformed: the snapshot immediately before the (latest) proposal commit does not parse.
    root = fx.case("t16-malformed-history")
    with ticking():
        _proposed(fx, root, "done")
        proposed = read(root, BI_INDEX)
        write_commit(env, root, BI_INDEX, b"record = [\n", "a torn index")
        write_commit(env, root, BI_INDEX, proposed, "the proposed index restored")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "does not parse")
    # Unverifiable: a proposal left uncommitted has no committed history yet.
    root = fx.case("t16-uncommitted")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        recorded(record_cli(env, root, ["transition", "BI-1", "done"] + ASSISTANT))
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "the proposal is not committed")


def flip_t16():
    """Trust the worklog's lifecycle line alone (the reviewed head's behaviour)."""
    return patch.object(record, "_corroborated_pre_proposal", lambda ctx, rel, rid, status, claimed: (claimed, None))


def t8_collision(fx):
    env = fx.env
    root = fx.case("t8-collision")
    env.git(root, "branch", "peer")
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "alpha"] + ASSISTANT, "BI-1 alpha")
        doctor_valid(env, root)
        env.git(root, "checkout", "-q", "peer")
        step(fx, root, ["create", "--type", "backlog_item", "--title", "beta"] + ASSISTANT, "BI-1 beta")
        doctor_valid(env, root)
    env.git(root, "checkout", "-q", "main")
    assert env.run_git(root, "merge", "--no-edit", "peer").returncode != 0, "T8 the store paths conflict"
    conflicted = env.git(root, "diff", "--name-only", "--diff-filter=U").split()
    assert BI_INDEX in conflicted, conflicted
    # The hand merge spec 5.7 forbids: the index keeps both sides' rows, re-emitted canonically so byte
    # reproduction cannot catch it; every other conflicted path takes this side.
    for rel in conflicted:
        if rel != BI_INDEX:
            env.git(root, "checkout", "--ours", "--", rel)
            continue
        ours, theirs = (tomllib.loads(env.git(root, "show", ":{}:{}".format(n, rel))) for n in (2, 3))
        ours["record"] = ours["record"] + [r for r in theirs["record"] if r not in ours["record"]]
        (Path(root) / rel).write_bytes(emit.emit_checked(ours).encode("utf-8"))
    env.git(root, "add", "-A")
    env.git(root, "commit", "-q", "--no-edit")
    ids = [r["id"] for r in model(root, BI_INDEX)["record"]]
    assert ids == ["BI-1", "BI-1"], ids
    rc, out, err = cli(env, ["doctor", "--root", str(root)])
    assert rc == 1 and "store integrity: INVALID" in out, ("T8 doctor INVALID", rc, out[-2400:], err[-800:])
    assert "  C-ID-SPACE: FINDING" in out, out[-2400:]
    assert "FINDING: C-ID-SPACE: duplicate id 'BI-1'" in out, out[-2400:]


def flip_t8():
    return patch.object(opf_check, "check_unique_ids", lambda ids: [])


# --- T15: a journal lock left after COMPLETE, then its reconciliation ----------------------------------------

# Inside the child: the journal lock release fails after the transaction reached COMPLETE, so the run goes
# on to render, run doctor, and report, leaving its journal lock behind.
FAILING_LOCK_RELEASE = """
import _journal
def _failing_release(journal_root):
    raise _journal.JournalError("synthetic journal lock release failure")
_journal.release_lock = _failing_release
"""


def t15_leftover_lock(fx):
    env = fx.env
    root = fx.case("t15-leftover-lock")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and '"event": "recorded"' in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert "could not be released" in proc.stderr, ("T15 the retained lock is surfaced", proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T15 the journal lock is left"
    result = record_cli(env, root, CREATE)
    refused(result, "was reconciled")
    err = result[2]
    assert "is COMPLETE" in err and "not recorded by the journal" in err, err[-1200:]
    assert "never run" not in err and "never ran" not in err, ("T15 no claim that render and doctor did not "
                                                             "run", err[-1200:])
    assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), "T15 the leftover lock is reconciled"


def flip_t15():
    original = record._leftover_lock_outcome

    def claims_never_ran(owner, states):
        line = original(owner, states)
        head, sep, _rest = line.partition("present in the working tree")
        return head + sep + ", with its render and final doctor never run" if sep else line
    return patch.object(record, "_leftover_lock_outcome", claims_never_ran)


def flip_t1():
    return patch.object(record, "_require_canonical", lambda operand: None)


def flip_t3():
    return patch.object(record, "_postcondition", lambda plan, req, ctx, now: None)


# --- the runner ------------------------------------------------------------------------------------------------

TESTS = (
    ("T1-precondition-byte-reproduction", t1_precondition, flip_t1),
    ("T2-round-trip-emit-checked", t2_round_trip, flip_t2),
    ("T3-allowed-delta-postcondition", t3_postcondition, flip_t3),
    ("T4-allocation-known-complete", t4_allocation, flip_t4),
    ("T5-crash-prestate-or-poststate", t5_crash, None),     # its flip runs inside the killed child
    ("T6-proposed-then-ratified-receipt", t6_done_with_receipt, (flip_t6_bare, flip_t6_maintainer,
                                                                flip_t6_receipt)),
    ("T7-rejection-reason-and-pre-proposal", t7_rejection, (flip_t7_reason, flip_t7_pre)),
    ("T8-parallel-branch-collision", t8_collision, flip_t8),
    ("T9-lease-release-before-success", t9_lease, (flip_t9, flip_t9_outcome)),
    ("T10-final-doctor", t10_final_doctor, flip_t10),
    ("T11-worklog-released-span", t11_worklog, flip_t11),
    ("T12-recovery-under-the-lease", t12_recovery_lease, flip_t12),
    ("T13-recovery-intervening-edit", t13_intervening_edit, flip_t13),
    ("T14-create-type-before-read", t14_type_before_read, flip_t14),
    ("T15-leftover-journal-lock", t15_leftover_lock, flip_t15),
    ("T16-rejection-corroborated-by-history", t16_corroborated_rejection, flip_t16),
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
            for name, test, flips in TESTS:
                flips = () if flips is None else flips if isinstance(flips, tuple) else (flips,)
                for flip in flips:
                    label = name if len(flips) == 1 else "{}:{}".format(name, flip.__name__)
                    check("red-on-revert-" + label,
                          lambda name=label, test=test, flip=flip: discriminate(name, lambda: test(fx), flip))
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
