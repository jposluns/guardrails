#!/usr/bin/env python3
"""Behavioural self-test for the GD-112 orchestrator-integrity handlers in
.aiqt/core/hooks/scripts/aiqt_hooks.py (the section-e acceptance vectors; authored BEFORE the core,
test-first). Hermetic: every case runs against throwaway fixtures under a temp dir (its own git repo,
its own registry, its own enumerator stub, its own state dir), removed in a finally; nothing on the
host is read or written. Verdicts are judged on the STRUCTURED result each handler returns (the
(code, stdout_obj, stderr) tuple), never by grepping diagnostic prose.

  selftest_orch_hooks.py                              exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL
  selftest_orch_hooks.py --execution-report ABS_PATH  additionally write the executed check ids as a
                                                      JSON report to ABS_PATH, on pass AND fail

Exit 1 covers assertion failures and an execution-set mismatch against tools/selftest_checks.toml (the
in-run self-guard; tools/check_selftest_execution.py reconciles the report independently). Exit 2 is a
harness/setup error: bad argv, a relative report path, a duplicate check id, a failed report write, or
an unreadable, malformed, or suite-missing expectation manifest.
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import datetime
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: selftest_orch_hooks.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

FAILURES = []
EXECUTED = []        # ordered check ids actually reached this run
_EXECUTED_SET = set()
SUITE_ID = "orch-behaviour-selftest"
CHECKS_MANIFEST = repo_root() / "tools" / "selftest_checks.toml"


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _expected_check_ids():
    """The registered execution set from the hand-authored expectation manifest, or None on an
    unreadable, malformed, or suite-missing manifest (the caller fails closed, exit 2). Light
    validation only; the strict schema gate lives in tools/check_selftest_execution.py."""
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    suites = data.get("suite")
    for row in (suites if isinstance(suites, list) else []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids):
                return set(ids)
            print("SELF-TEST HARNESS ERROR: malformed expected-check-ids for suite {!r} in {}".format(
                SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
            return None
    print("SELF-TEST HARNESS ERROR: no suite {!r} registered in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def _verdict(result):
    """Reduce a handler result tuple to one of: allow, warn, ask, deny, block2."""
    code, obj, _err = result
    if code == 2:
        return "block2"
    if code == 0 and obj is None:
        return "allow"
    if code == 0 and isinstance(obj, dict):
        decision = obj.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision in ("ask", "deny"):
            return decision
        if "systemMessage" in obj or "hookSpecificOutput" in obj:
            return "warn"
    return "unexpected({!r})".format(result)


ENUM_STUB = """#!/usr/bin/env python3
import sys
sys.stdout.write(open(sys.argv[1]).read())
sys.exit(int(open(sys.argv[2]).read().strip()))
"""


class Fixture:
    """One hermetic orchestration fixture: a git repo, a registry, a controllable enumerator, and a
    private state dir. Each case mutates enum payload/exit, mode, lease, and records as needed."""

    def __init__(self, base, name):
        self.root = base / name
        self.root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)],
                       check=True, capture_output=True, timeout=30)
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        self._git("add", "seed.txt")
        self._git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
        self.state = self.root / "state"
        self.enum_payload = self.root / "enum-payload.json"
        self.enum_exit = self.root / "enum-exit.txt"
        stub = self.root / "enum-stub.py"
        stub.write_text(ENUM_STUB, encoding="utf-8")
        self.enum_exit.write_text("0", encoding="utf-8")
        self.mode = self.root / "session-state.md"
        self.lease = self.root / "lease.txt"
        self.pending = self.root / "pending-decisions.md"
        self.findings = self.root / "findings.md"
        self.handoff = self.root / "handoff.md"
        for f in (self.mode, self.pending, self.findings, self.handoff):
            f.write_text("", encoding="utf-8")
        self.lease.write_text("holder: selftest\n", encoding="utf-8")
        registry = {
            "version": 1,
            "enumerator": {"argv": [sys.executable, str(stub), str(self.enum_payload),
                                    str(self.enum_exit)], "timeout": 30},
            "record": {"findings": str(self.findings),
                       "pending_decisions": str(self.pending),
                       "handoff": str(self.handoff)},
            "mode": {"path": str(self.mode)},
            "lease": {"path": str(self.lease), "max_age_hours": 24},
            "state_dir": str(self.state),
            "yield_tools": ["ScheduleWakeup", "CronCreate"],
            "dispatch_tools": [],
            "staleness": {"external_hours": 24, "task_hours": 24},
        }
        (self.root / ".aiqt").mkdir()
        (self.root / ".aiqt" / "orchestration.local.json").write_text(
            json.dumps(registry), encoding="utf-8")
        self.set_items([])

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root)] + list(args),
                       check=True, capture_output=True, timeout=30)

    def set_items(self, items, version=1, keep_checkpoint=False):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.enum_payload.write_text(json.dumps(
            {"version": version, "generated_at_utc": now,
             "source": {"locator": "fixture", "revision": "r1", "observed_at_utc": now},
             "items": items}), encoding="utf-8")
        if not keep_checkpoint:
            # Each set_items call REPLACES the fixture backlog wholesale (a new scenario, never a
            # shrink of the previous one), so the C.3 anti-shrinkage window starts fresh; a C.3 leg
            # opts into the union with keep_checkpoint=True. FIX 4: the init marker is part of that
            # window state, so a fresh scenario clears it too (else a stale marker would read as a
            # possible reset on the next stop).
            (self.state / "backlog-checkpoint.json").unlink(missing_ok=True)
            (self.state / "checkpoint-init.marker").unlink(missing_ok=True)

    def payload(self, event, tool=None, tool_input=None, extra=None):
        data = {"hook_event_name": event, "cwd": str(self.root),
                "session_id": "s1"}
        if tool is not None:
            data["tool_name"] = tool
            data["tool_input"] = tool_input or {}
        if extra:
            data.update(extra)
        return data

    def turn_state(self):
        sd = aiqt_hooks._orch_state_dir_for_root(str(self.root))
        p = Path(sd) / "turn-state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def set_turn_state(self, st):
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(self.root)))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "turn-state.json").write_text(json.dumps(st), encoding="utf-8")


def item(iid, state="open", granted=True, blocker=None, title=None):
    it = {"id": iid, "title": title or iid, "state": state, "granted": granted}
    if blocker:
        it["blocker"] = blocker
    return it


def now_iso(hours_ago=0):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return t.isoformat()


def main(report_path=None):
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-orch-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temp dir: {}".format(exc), file=sys.stderr)
        return 2
    os.environ["XDG_STATE_HOME"] = str(tmp / "xdg")  # hermetic default state root
    try:
        # ---------- component 1: the stop guard ----------
        f = Fixture(tmp, "stop")
        stop = lambda: aiqt_hooks.orch_stop_guard(f.payload("Stop"))

        # registry absent -> inert allow
        bare = tmp / "bare"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(bare)],
                       check=True, capture_output=True, timeout=30)
        check("stop/registry-absent", _verdict(aiqt_hooks.orch_stop_guard(
            {"hook_event_name": "Stop", "cwd": str(bare)})), "allow")

        # no live lease -> allow (scope)
        f.lease.unlink()
        f.set_items([item("A-1")])
        check("stop/no-lease", _verdict(stop()), "allow")
        f.lease.write_text("holder: selftest\n", encoding="utf-8")

        # one granted open unblocked item -> deny naming it
        f.set_items([item("A-1", title="do the thing")])
        code, obj, err = stop()
        check("stop/actionable-denies", code, 2)
        check("stop/deny-names-item", "A-1" in (err or ""), True)
        check("stop/deny-names-exits", "blocker" in (err or "").lower(), True)

        # raw BLOCKED with no proof -> deny
        f.set_items([item("A-2", blocker={"kind": "external", "ref": "x", "evidence": ""})])
        f.set_turn_state({})
        check("stop/unproven-blocker-denies", _verdict(stop()), "block2")

        # all compelling items carrying current proof -> allow (disposition logged)
        f.set_items([item("A-3", blocker={"kind": "external", "ref": "ci",
                                          "evidence": "run 42 pending",
                                          "observed_at_utc": now_iso(1)})])
        f.set_turn_state({})
        check("stop/proven-blockers-allow", _verdict(stop()), "allow")

        # stale external evidence -> deny
        f.set_items([item("A-4", blocker={"kind": "external", "ref": "ci",
                                          "evidence": "old", "observed_at_utc": now_iso(48)})])
        f.set_turn_state({})
        check("stop/stale-evidence-denies", _verdict(stop()), "block2")

        # proposed-only backlog -> allow
        f.set_items([item("P-1", state="proposed")])
        f.set_turn_state({})
        check("stop/proposed-never-compels", _verdict(stop()), "allow")

        # live ledger task -> allow; past staleness -> deny
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
        sd.mkdir(parents=True, exist_ok=True)
        ledger = sd / "dispatch-ledger.jsonl"
        ledger.write_text(json.dumps({"ts": now_iso(1), "event": "launch", "task_id": "T-9",
                                      "tool": "Bash", "wake": True}) + "\n", encoding="utf-8")
        f.set_items([item("A-5", blocker={"kind": "tracked-task", "ref": "T-9"})])
        f.set_turn_state({})
        check("stop/live-task-allows", _verdict(stop()), "allow")
        ledger.write_text(json.dumps({"ts": now_iso(48), "event": "launch", "task_id": "T-9",
                                      "tool": "Bash", "wake": True}) + "\n", encoding="utf-8")
        f.set_turn_state({})
        check("stop/stale-task-denies", _verdict(stop()), "block2")

        # live task plus an independent actionable item -> deny
        ledger.write_text(json.dumps({"ts": now_iso(1), "event": "launch", "task_id": "T-9",
                                      "tool": "Bash", "wake": True}) + "\n", encoding="utf-8")
        f.set_items([item("A-5", blocker={"kind": "tracked-task", "ref": "T-9"}), item("A-6")])
        f.set_turn_state({})
        check("stop/waiting-plus-actionable-denies", _verdict(stop()), "block2")

        # human-decision with a matching pending row -> allow; without -> deny
        f.pending.write_text("| PD-7 | 2026-01-01 | which licence | RAISED |\n", encoding="utf-8")
        f.set_items([item("A-7", blocker={"kind": "human-decision", "ref": "PD-7"})])
        f.set_turn_state({})
        check("stop/pending-decision-allows", _verdict(stop()), "allow")
        f.set_items([item("A-8", blocker={"kind": "human-decision", "ref": "PD-404"})])
        f.set_turn_state({})
        check("stop/missing-pending-denies", _verdict(stop()), "block2")
        # R2-CX-B3: a decision-id-shaped token appearing only in PROSE (not at a row-id position) must
        # NOT forge a human-decision block, so the item stays actionable and the stop denies.
        f.pending.write_text("The encoding chosen for PD-7 was UTF-8.\n", encoding="utf-8")
        f.set_items([item("A-8b", blocker={"kind": "human-decision", "ref": "UTF-8"})])
        f.set_turn_state({})
        check("stop/prose-token-does-not-forge-block", _verdict(stop()), "block2")

        # R2/R3: malformed AEI provenance is a cannot-evaluate (FIX 1 DENIES the stop; ignorance
        # refuses the wind-down), never a clean backlog
        nowv = now_iso(0)
        for check_id, env in [
                ("stop/malformed-provenance-bool-version-denies",
                 '{"version": true, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % nowv),
                ("stop/malformed-provenance-float-version-denies",
                 '{"version": 1.0, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % nowv),
                ("stop/malformed-provenance-unparseable-ts-denies",
                 '{"version": 1, "generated_at_utc": "banana", "source": {"locator":"f"}, "items": []}'),
                ("stop/malformed-provenance-future-ts-denies",
                 '{"version": 1, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % now_iso(-48)),
                ("stop/malformed-provenance-no-locator-denies",
                 '{"version": 1, "generated_at_utc": "%s", "source": {}, "items": []}' % nowv)]:
            f.enum_payload.write_text(env, encoding="utf-8")
            f.set_turn_state({})
            check(check_id, _verdict(stop()), "block2")
        # a blank-evidence external blocker does not prove a block (R3-CX-M7)
        f.set_items([item("A-be", blocker={"kind": "external", "ref": "ci", "evidence": "   ",
                                           "observed_at_utc": now_iso(1)})])
        f.set_turn_state({})
        check("stop/blank-evidence-denies", _verdict(stop()), "block2")
        # a malformed dispatch ledger HOLDS a tracked item: FIX 1 DENIES the stop, schedule denies (R3-CX-B1)
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "dispatch-ledger.jsonl").write_text("null\n{broken\n", encoding="utf-8")
        f.set_items([item("A-le", blocker={"kind": "tracked-task", "ref": "T-x"})])
        f.set_turn_state({})
        check("stop/malformed-ledger-denies", _verdict(stop()), "block2")
        check("sched/malformed-ledger-denies", _verdict(aiqt_hooks.orch_yield_tool(
            f.payload("PreToolUse", "ScheduleWakeup", {"prompt": "recheck T-x"}))), "deny")
        (sd / "dispatch-ledger.jsonl").unlink()

        # not-before: unmet -> allow, met -> deny
        f.set_items([item("A-9", blocker={"kind": "not-before", "ref": now_iso(-24)})])
        f.set_turn_state({})
        check("stop/not-before-unmet-allows", _verdict(stop()), "allow")
        f.set_items([item("A-9", blocker={"kind": "not-before", "ref": now_iso(24)})])
        f.set_turn_state({})
        check("stop/not-before-met-denies", _verdict(stop()), "block2")

        # counter at 2 -> allow with findings (warn), never a third deny
        f.set_items([item("A-10")])
        f.set_turn_state({"stop_denials": 2})
        check("stop/loop-bound", _verdict(stop()), "warn")

        # enumerator nonzero exit: FIX 1 DENIES the stop (ignorance refuses the wind-down)
        f.enum_exit.write_text("3", encoding="utf-8")
        f.set_turn_state({})
        check("stop/enum-error-denies", _verdict(stop()), "block2")

        # malformed enumeration is ENUMERATOR_ERROR, never an empty backlog
        f.enum_exit.write_text("0", encoding="utf-8")
        f.enum_payload.write_text("{not json", encoding="utf-8")
        f.set_turn_state({})
        check("stop/bad-json-denies", _verdict(stop()), "block2")
        f.set_items([item("D-1"), item("D-1")])  # duplicate id
        f.set_turn_state({})
        check("stop/dup-id-denies", _verdict(stop()), "block2")
        f.set_items([item("V-1")], version=9)
        f.set_turn_state({})
        check("stop/unknown-version-denies", _verdict(stop()), "block2")

        # C.1 witnessed fail-to-pass (reproduce-before-fix): a sentinel written by the RUNNING euid
        # is actor-owned, so it must NOT open the clean-ALLOW channel. Run against the PRE-change
        # code this same leg fails (the old lexists honour returns "allow"); post-change the verdict
        # is the normal enumeration verdict (block2 on A-11) plus an escape-spoof record.
        f.set_items([item("A-11")])
        f.set_turn_state({})
        (sd / "ESCAPE-ALLOW-YIELD").write_text("operator\n", encoding="utf-8")
        check("stop/actor-owned-escape-ignored", _verdict(stop()), "block2")
        check("stop/escape-spoof-artefact", (sd / "escape-spoof.json").exists(), True)
        check("stop/escape-spoof-logged",
              '"escape-spoof"' in (sd / "guard-events.jsonl").read_text(encoding="utf-8"), True)
        (sd / "ESCAPE-ALLOW-YIELD").unlink()
        (sd / "escape-spoof.json").unlink()
        # a symlink sentinel is ignored the same way (lstat, never followed)
        (sd / "ESCAPE-ALLOW-YIELD").symlink_to(f.root / "seed.txt")
        f.set_turn_state({})
        check("stop/symlink-escape-ignored", _verdict(stop()), "block2")
        (sd / "ESCAPE-ALLOW-YIELD").unlink()
        (sd / "escape-spoof.json").unlink()
        # ---------- C.1 FIX 2: escape-path anchoring, symlink-component, hardlink hardening ----------
        _fregp = f.root / ".aiqt" / "orchestration.local.json"
        _freg = json.loads(_fregp.read_text(encoding="utf-8"))

        def _set_escape(path_value):
            _freg["escape"] = {"path": path_value}
            _fregp.write_text(json.dumps(_freg), encoding="utf-8")

        def _spoof_detail():
            return json.loads(
                (sd / "escape-spoof.json").read_text(encoding="utf-8")).get("detail", "")

        f.set_items([item("A-13")])
        # an absolute FOREIGN escape path outside the operator-trusted anchor is ignored + spoofed
        _set_escape("/etc/passwd")
        f.set_turn_state({})
        check("stop/foreign-abs-escape-ignored", _verdict(stop()), "block2")
        check("stop/foreign-abs-escape-spoof",
              "outside the operator-trusted anchor" in _spoof_detail(), True)
        (sd / "escape-spoof.json").unlink()
        # a '..' traversal in the escape path is rejected + spoofed
        _set_escape(str(sd / os.pardir / "ESCAPE-ALLOW-YIELD"))
        f.set_turn_state({})
        check("stop/dotdot-escape-ignored", _verdict(stop()), "block2")
        check("stop/dotdot-escape-spoof", "'..'" in _spoof_detail(), True)
        (sd / "escape-spoof.json").unlink()
        # a symlinked PARENT component under the anchor is refused (the walk never follows a link)
        (sd / "realdir").mkdir()
        (sd / "linkdir").symlink_to(sd / "realdir")
        _set_escape(str(sd / "linkdir" / "ESCAPE-ALLOW-YIELD"))
        f.set_turn_state({})
        check("stop/symlink-component-escape-ignored", _verdict(stop()), "block2")
        check("stop/symlink-component-escape-spoof",
              "symlinked path component" in _spoof_detail(), True)
        (sd / "escape-spoof.json").unlink()
        # restore the fixture to no declared escape key (default state-dir sentinel) for the seam legs
        del _freg["escape"]
        _fregp.write_text(json.dumps(_freg), encoding="utf-8")
        f.set_items([item("A-11")])
        # FIX B: a FIFO sentinel with no writer is IGNORED (not a hang). The real _orch_escape_stat
        # opens O_RDONLY|O_NONBLOCK, so a writerless FIFO opens non-blocking and the S_ISREG check
        # rejects it; WITHOUT O_NONBLOCK the open blocks forever and this leg would hang (a regression
        # surfaces as a hung self-test). A real mkfifo in the state-dir anchor, not the injected seam.
        os.mkfifo(str(sd / "ESCAPE-ALLOW-YIELD"))
        f.set_turn_state({})
        check("stop/fifo-escape-ignored", _verdict(stop()), "block2")
        check("stop/fifo-escape-spoof", "not a regular file" in _spoof_detail(), True)
        (sd / "ESCAPE-ALLOW-YIELD").unlink()
        (sd / "escape-spoof.json").unlink()
        # The remaining acceptance legs run through the injected _orch_escape_stat seam, because a
        # real file owned by a DIFFERENT uid cannot be created hermetically on a single-uid host
        # (test-hermeticity: the seam supplies the lstat result the filesystem cannot).
        import stat as _stat

        class _St:
            def __init__(self, mode, uid, nlink=1):
                self.st_mode = mode
                self.st_uid = uid
                self.st_nlink = nlink
        _orig_escape_stat = aiqt_hooks._orch_escape_stat
        try:
            # honour leg: different uid, regular file, owner-only mode -> the clean ALLOW stands
            aiqt_hooks._orch_escape_stat = lambda path: _St(_stat.S_IFREG | 0o600, os.geteuid() + 1)
            f.set_turn_state({})
            check("stop/operator-owned-escape-allows", _verdict(stop()), "allow")
            # a group-writable different-uid sentinel is ignored (any writer could have widened it)
            aiqt_hooks._orch_escape_stat = lambda path: _St(_stat.S_IFREG | 0o664, os.geteuid() + 1)
            f.set_turn_state({})
            check("stop/group-writable-escape-ignored", _verdict(stop()), "block2")
            # a hard-linked sentinel (st_nlink > 1) is ignored: a second link could be the actor's own
            aiqt_hooks._orch_escape_stat = lambda path: _St(_stat.S_IFREG | 0o600,
                                                            os.geteuid() + 1, nlink=2)
            f.set_items([item("A-14")])
            f.set_turn_state({})
            check("stop/hardlinked-escape-ignored", _verdict(stop()), "block2")
            # an OSError from the stat (the seam returns None) reads inactive and adds NO new deny
            aiqt_hooks._orch_escape_stat = lambda path: None
            f.set_items([])
            f.set_turn_state({})
            check("stop/escape-oserror-no-new-deny", _verdict(stop()), "allow")
        finally:
            aiqt_hooks._orch_escape_stat = _orig_escape_stat
        if (sd / "escape-spoof.json").exists():
            (sd / "escape-spoof.json").unlink()

        # FIX 6: a spoof whose record FAILS surfaces fail-loud in the banner (never a silent None).
        # Both writes are forced to fail; with an empty backlog the verdict would otherwise be a silent
        # ALLOW, so the warning is the only signal.
        _o_wj = aiqt_hooks._orch_write_json
        _o_aj = aiqt_hooks._orch_append_jsonl
        try:
            aiqt_hooks._orch_write_json = lambda p, o: False
            aiqt_hooks._orch_append_jsonl = lambda p, o: False
            f.set_items([])
            f.set_turn_state({})
            (sd / "ESCAPE-ALLOW-YIELD").write_text("operator\n", encoding="utf-8")
            scode, sobj, _serr = stop()
            check("stop/spoof-record-failure-surfaces",
                  scode == 0 and isinstance(sobj, dict)
                  and "could not be fully recorded" in sobj.get("systemMessage", ""), True)
        finally:
            aiqt_hooks._orch_write_json = _o_wj
            aiqt_hooks._orch_append_jsonl = _o_aj
            (sd / "ESCAPE-ALLOW-YIELD").unlink(missing_ok=True)

        # guard-events rows were appended by the denies above
        check("stop/guard-events-written", (sd / "guard-events.jsonl").exists(), True)

        # TeammateIdle: same core; FIX 1 DENIES the idle on enum error (ignorance refuses the wind-down)
        f.enum_exit.write_text("3", encoding="utf-8")
        f.set_turn_state({})
        check("idle/enum-error-denies",
              _verdict(aiqt_hooks.orch_teammate_idle(f.payload("TeammateIdle"))), "block2")
        f.enum_exit.write_text("0", encoding="utf-8")
        f.set_items([item("A-12")])
        f.set_turn_state({})
        check("idle/actionable-denies",
              _verdict(aiqt_hooks.orch_teammate_idle(f.payload("TeammateIdle"))), "block2")

        # ---------- component 1: the scheduled-yield tool binding ----------
        g = Fixture(tmp, "yield")
        sched = lambda ti: aiqt_hooks.orch_yield_tool(
            g.payload("PreToolUse", "ScheduleWakeup", ti))

        # enumerator error on the schedule path -> deny (missing evidence licenses no autonomy)
        g.enum_exit.write_text("3", encoding="utf-8")
        check("yield/enum-error-denies", _verdict(sched({"prompt": "recheck"})), "deny")

        # 4th denial on an unchanged basis -> allow with findings
        g.set_turn_state({"schedule_denials": 3, "schedule_basis": "ENUMERATOR_ERROR"})
        check("yield/denial-cap", _verdict(sched({"prompt": "recheck"})), "warn")
        g.enum_exit.write_text("0", encoding="utf-8")

        # waiting item: a wake naming it allows; one naming nothing denies (hygiene)
        gsd = Path(aiqt_hooks._orch_state_dir_for_root(str(g.root)))
        gsd.mkdir(parents=True, exist_ok=True)
        (gsd / "dispatch-ledger.jsonl").write_text(
            json.dumps({"ts": now_iso(1), "event": "launch", "task_id": "T-1",
                        "tool": "Bash", "wake": True}) + "\n", encoding="utf-8")
        g.set_items([item("W-1", blocker={"kind": "tracked-task", "ref": "T-1"})])
        g.set_turn_state({})
        check("yield/wake-names-item-allows",
              _verdict(sched({"prompt": "recheck T-1 completion"})), "allow")
        g.set_turn_state({})
        check("yield/wake-names-nothing-denies", _verdict(sched({"prompt": "just waiting"})), "deny")

        # an existing cron is never a live task: actionable item still denies the schedule
        g.set_items([item("A-1")])
        g.set_turn_state({})
        check("yield/actionable-denies-schedule",
              _verdict(sched({"prompt": "recheck A-1 later"})), "deny")

        # a ScheduleWakeup with stop=true is the STOP kind: FIX 1 DENIES on enum error (ignorance
        # refuses the wind-down; the yield tool maps DENY to a PreToolUse deny)
        g.enum_exit.write_text("3", encoding="utf-8")
        g.set_turn_state({})
        check("yield/stop-kind-denies",
              _verdict(sched({"stop": True})), "deny")
        g.enum_exit.write_text("0", encoding="utf-8")

        # measured quiet wins over a claimed quiet duration
        g.set_items([item("W-1", blocker={"kind": "tracked-task", "ref": "T-1"})])
        g.set_turn_state({"last_human_input_utc": now_iso(0)})  # measured: ~0 minutes
        check("yield/measured-beats-claimed",
              _verdict(sched({"prompt": "user quiet for 20 minutes; recheck T-1"})), "deny")

        # a tool outside the registry's yield_tools is out of scope
        check("yield/undeclared-tool-out-of-scope",
              _verdict(aiqt_hooks.orch_yield_tool(
                  g.payload("PreToolUse", "CronDelete", {"prompt": "x"}))), "allow")

        # ---------- component 4: the unattended-ask blocker ----------
        h = Fixture(tmp, "ask")
        ask = lambda: aiqt_hooks.orch_ask_guard(g_ask)
        # regression vectors imported from the live host hook's self-test
        for check_id, mode_text, want in (
                ("ask/mode-overnight-unattended-denies",
                 "Operating-mode: overnight-unattended\n", "deny"),
                ("ask/mode-unattended-parenthetical-denies",
                 "Operating-mode: unattended (overnight; ipad-origin)\n", "deny"),
                ("ask/mode-daytime-unattended-denies",
                 "Operating-mode: daytime-unattended\n", "deny"),
                ("ask/mode-attended-autonomous-allows",
                 "Operating-mode: attended-autonomous\n", "allow"),
                ("ask/mode-fully-attended-allows",
                 "Operating-mode: fully-attended\n", "allow"),
                ("ask/mode-absent-allows", "", "allow")):  # absent mode line fails open
            h.mode.write_text(mode_text, encoding="utf-8")
            g_ask = h.payload("PreToolUse", "AskUserQuestion",
                              {"questions": [{"question": "pick one"}]},
                              extra={"tool_use_id": "tu-1"})
            check(check_id, _verdict(ask()), want)
        # unreadable mode record fails open
        h.mode.unlink()
        g_ask = h.payload("PreToolUse", "AskUserQuestion", {"questions": []},
                          extra={"tool_use_id": "tu-2"})
        check("ask/unreadable-mode-fails-open", _verdict(ask()), "allow")
        # idempotent pending append, redacted (digest + counts, never the question text)
        h.mode.write_text("Operating-mode: daytime-unattended\n", encoding="utf-8")
        g_ask = h.payload("PreToolUse", "AskUserQuestion",
                          {"questions": [{"question": "SECRETPHRASE which db?"}]},
                          extra={"tool_use_id": "tu-3"})
        ask(); ask()
        hsd = Path(aiqt_hooks._orch_state_dir_for_root(str(h.root)))
        rows = (hsd / "pending-asks.jsonl").read_text(encoding="utf-8").splitlines()
        keyed = [r for r in rows if "tu-3" in r]
        check("ask/idempotent-append", len(keyed), 1)
        check("ask/redacted", any("SECRETPHRASE" in r for r in rows), False)

        # ---------- component 3: the truncation guard ----------
        t = Fixture(tmp, "trunc")
        bg = lambda cmd, rib=True: aiqt_hooks.orch_truncation_guard(
            t.payload("PreToolUse", "Bash",
                      {"command": cmd, "run_in_background": rib}))
        # AIRTIGHT-NARROW truncation guard (round 32): ALLOW only a plain metacharacter-free background
        # command; ANY shell metacharacter -> ASK; foreground out of scope; only the no-command case DENIES.
        check("trunc/plain-bg-allows", _verdict(bg("python3 build.py")), "allow")
        check("trunc/plain-args-allows", _verdict(bg("pytest -q tests/unit")), "allow")
        check("trunc/plain-flag-eq-allows", _verdict(bg("python3 build.py --out=dist/log")), "allow")
        check("trunc/plain-envprefix-allows", _verdict(bg("PYTHONPATH=src python3 build.py")), "allow")
        check("trunc/pipe-asks", _verdict(bg("python3 build.py | tail -5")), "ask")
        check("trunc/head-asks", _verdict(bg("python3 build.py | head -20")), "ask")
        check("trunc/tee-asks", _verdict(bg("python3 build.py | tee full.out")), "ask")
        check("trunc/redirect-asks", _verdict(bg("python3 build.py > out.txt")), "ask")
        check("trunc/quoted-redirect-asks", _verdict(bg("grep '>' index.html | head -5")), "ask")
        check("trunc/quoted-pipe-asks", _verdict(bg("grep '|' file")), "ask")
        check("trunc/amp-asks", _verdict(bg("python3 build.py | tail &")), "ask")
        check("trunc/semicolon-asks", _verdict(bg("python3 a.py ; python3 b.py")), "ask")
        check("trunc/subshell-asks", _verdict(bg("( python3 build.py | tail )")), "ask")
        check("trunc/brace-asks", _verdict(bg("{ python3 build.py | tail; }")), "ask")
        check("trunc/dollar-var-asks", _verdict(bg("python3 build.py > $OUT")), "ask")
        check("trunc/cmdsub-asks", _verdict(bg("python3 build.py > $(date).log")), "ask")
        check("trunc/backtick-asks", _verdict(bg("python3 build.py > `date`.log")), "ask")
        check("trunc/coproc-asks", _verdict(bg("coproc producer")), "ask")
        check("trunc/reserved-time-asks", _verdict(bg("time python3 build.py")), "ask")
        check("trunc/reserved-for-asks", _verdict(bg("for x in a b")), "ask")
        check("trunc/glob-asks", _verdict(bg("cat *.log")), "ask")
        check("trunc/comment-asks", _verdict(bg("python3 build.py # note")), "ask")
        check("trunc/shell-c-asks", _verdict(bg("bash -c 'python3 build.py | tail'")), "ask")
        check("trunc/tilde-asks", _verdict(bg("cat ~/notes.txt")), "ask")
        check("trunc/foreground-plain-allows", _verdict(bg("python3 build.py", rib=False)), "allow")
        check("trunc/foreground-pipe-allows", _verdict(bg("python3 build.py | tail -5", rib=False)), "allow")
        check("trunc/foreground-tee-allows", _verdict(bg("python3 build.py | tee f", rib=False)), "allow")
        check("trunc/empty-bg-denies", _verdict(bg("")), "deny")
        # L-GS1 / trkasy: foreground bare-& detach coverage. A plain foreground call stays out of scope, but
        # a bare `&` detaches a child into untracked async work -> ASK. The shell forms that also carry an
        # ampersand but do NOT detach (&&, &>, &>>, <&, >&, |&, and any quoted or escaped &) stay ALLOW; the
        # narrow scanner over-asks (never silently allows) on grammar it cannot model.
        check("trunc/fg-detach-trailing-asks", _verdict(bg("long_job &", rib=False)), "ask")
        check("trunc/fg-detach-between-asks", _verdict(bg("worker & echo done", rib=False)), "ask")
        check("trunc/fg-detach-grouped-asks", _verdict(bg("( long_job & )", rib=False)), "ask")
        check("trunc/fg-detach-newline-asks", _verdict(bg("long_job &\necho next", rib=False)), "ask")
        # a later `wait` does not clear it: the lexical hook cannot prove the correct child is awaited.
        check("trunc/fg-detach-then-wait-asks", _verdict(bg("worker & wait", rib=False)), "ask")
        check("trunc/fg-logical-and-allows", _verdict(bg("build && test", rib=False)), "allow")
        check("trunc/fg-amp-redirect-allows", _verdict(bg("build &> out.log", rib=False)), "allow")
        check("trunc/fg-amp-redirect-append-allows", _verdict(bg("build &>> out.log", rib=False)), "allow")
        check("trunc/fg-dup-stdout-allows", _verdict(bg("build 2>&1", rib=False)), "allow")
        check("trunc/fg-dup-lt-allows", _verdict(bg("read x <&3", rib=False)), "allow")
        check("trunc/fg-pipe-stderr-allows", _verdict(bg("build |& tee log", rib=False)), "allow")
        check("trunc/fg-single-quoted-amp-allows", _verdict(bg("echo 'a & b'", rib=False)), "allow")
        check("trunc/fg-double-quoted-amp-allows", _verdict(bg('echo "a & b"', rib=False)), "allow")
        check("trunc/fg-escaped-amp-allows", _verdict(bg("echo a \\& b", rib=False)), "allow")
        # direct scanner unit checks (the quote/escape provenance _segments cannot carry): a quoted or an
        # escaped redirect char before `&` is still a real detach, while a genuine dup redirect is not.
        check("trunc/scan-quoted-redirect-detach", aiqt_hooks._orch_foreground_detach('echo ">" &'), True)
        check("trunc/scan-escaped-gt-then-detach", aiqt_hooks._orch_foreground_detach("echo \\>&"), True)
        check("trunc/scan-real-dup-not-detach", aiqt_hooks._orch_foreground_detach("cmd 2>&1"), False)
        # finding E (unbalanced/ambiguous quoting fails toward ASK, never a silent allow of a real `&`): a
        # scan that ends still inside a quote (an unbalanced quote, or an ANSI-C $'...' construct this scan
        # does not model) could hide a real trailing `&`, so it reports a detach. Without the fix each of
        # these ended `inside quotes` and returned False, silently allowing the real `&`.
        check("trunc/scan-ansi-c-hidden-detach", aiqt_hooks._orch_foreground_detach(r"echo $'a\'b' & echo x"), True)
        check("trunc/scan-unbalanced-single-asks", aiqt_hooks._orch_foreground_detach("echo 'oops & bg"), True)
        check("trunc/fg-ansi-c-hidden-detach-asks", _verdict(bg(r"echo $'a\'b' & echo x", rib=False)), "ask")
        # finding F (an unquoted word-start `#` comment is dropped, so a commented-out `&` does not prompt):
        # without the fix the `&` in comment text was scanned as an operator and over-ASKED.
        check("trunc/scan-comment-amp-not-detach", aiqt_hooks._orch_foreground_detach("echo done # & comment"), False)
        check("trunc/scan-comment-leading-hash-not-detach", aiqt_hooks._orch_foreground_detach("# long_job &"), False)
        # L-GS1 fix-round: a `#` comment runs only to the end of ITS line, never to the end of a multi-line
        # command. A comment on an earlier line must NOT swallow a real bare-& detach on a later line; before
        # the fix the whole scan broke at the first `#`, so these two silently ALLOWED (returned False).
        check("trunc/scan-comment-then-detach-nextline", aiqt_hooks._orch_foreground_detach("echo hi  # note\nsleep 100 &"), True)
        check("trunc/scan-leading-comment-then-detach", aiqt_hooks._orch_foreground_detach("# lead comment\nsleep 100 &"), True)
        check("trunc/fg-comment-then-detach-asks", _verdict(bg("echo hi  # note\nsleep 100 &", rib=False)), "ask")
        check("trunc/fg-comment-amp-allows", _verdict(bg("echo done # & comment", rib=False)), "allow")
        # inert when the orchestration registry is absent: a foreground bare-& acquires no new prompt.
        ti = Fixture(tmp, "trunc-inert")
        (ti.root / ".aiqt" / "orchestration.local.json").unlink()
        check("trunc/fg-detach-inert-no-registry", _verdict(aiqt_hooks.orch_truncation_guard(
            ti.payload("PreToolUse", "Bash",
                       {"command": "long_job &", "run_in_background": False}))), "allow")

        # ---------- Surface B: the validation membrane ----------
        import time as _time
        check("vB/exact-int-valid", aiqt_hooks._v_exact_int(3, 0, 9999), 3)
        check("vB/exact-int-bool", aiqt_hooks._v_exact_int(True, 0, 9999), None)
        check("vB/exact-int-neg", aiqt_hooks._v_exact_int(-1, 0, 9999), None)
        check("vB/exact-int-over", aiqt_hooks._v_exact_int(10000, 0, 9999), None)
        check("vB/exact-int-float", aiqt_hooks._v_exact_int(3.0, 0, 9999), None)
        check("vB/finite-pos-valid", aiqt_hooks._v_finite_pos(24, 8760), 24)
        check("vB/finite-pos-zero", aiqt_hooks._v_finite_pos(0, 8760), None)
        check("vB/finite-pos-nan", aiqt_hooks._v_finite_pos(float("nan"), 8760), None)
        check("vB/finite-pos-inf", aiqt_hooks._v_finite_pos(float("inf"), 8760), None)
        check("vB/finite-pos-over", aiqt_hooks._v_finite_pos(99999, 8760), None)
        check("vB/finite-pos-bool", aiqt_hooks._v_finite_pos(True, 8760), None)
        check("vB/staleness-inf-defaults",
              aiqt_hooks._orch_validate("staleness", {"task_hours": float("inf")})[1]["task_hours"], 24)
        check("vB/staleness-neg-defaults",
              aiqt_hooks._orch_validate("staleness", {"task_hours": -5})[1]["task_hours"], 24)
        check("vB/staleness-valid",
              aiqt_hooks._orch_validate("staleness", {"task_hours": 12})[1]["task_hours"], 12)
        check("vB/turn-absent-fresh",
              aiqt_hooks._orch_validate("turn_state", {})[1]["stop_denials"], 0)
        check("vB/turn-valid",
              aiqt_hooks._orch_validate("turn_state", {"stop_denials": 4})[1]["stop_denials"], 4)
        check("vB/turn-malformed-none",
              aiqt_hooks._orch_validate("turn_state", {"stop_denials": "x"})[1]["stop_denials"], None)
        check("vB/turn-unreadable-none",
              aiqt_hooks._orch_validate("turn_state", None)[1]["stop_denials"], None)
        check("vB/unknown-boundary-cannot-evaluate",
              aiqt_hooks._orch_validate("nope", {})[0], "cannot-evaluate")
        # D13: the registry version must be an exact int 1 (True and 1.0 do not pass)
        b = Fixture(tmp, "surfb")
        regpath = b.root / ".aiqt" / "orchestration.local.json"
        base_reg = json.loads(regpath.read_text(encoding="utf-8"))
        for check_id, badver in (("vB/registry-version-bool-true-bad", True),
                                 ("vB/registry-version-float-one-bad", 1.0),
                                 ("vB/registry-version-string-one-bad", "1"),
                                 ("vB/registry-version-int-two-bad", 2)):
            base_reg["version"] = badver
            regpath.write_text(json.dumps(base_reg), encoding="utf-8")
            check(check_id, aiqt_hooks._orch_registry(str(b.root))[0], "bad")
        base_reg["version"] = 1
        regpath.write_text(json.dumps(base_reg), encoding="utf-8")
        check("vB/registry-version-ok", aiqt_hooks._orch_registry(str(b.root))[0], "ok")
        # D12: schedule denials on basis X do not carry to basis Y (fresh count of 1); same basis increments
        aiqt_hooks._orch_record_denial(str(b.root), {"schedule_denials": 2, "schedule_basis": "X"},
                                       "schedule_idle", "Y")
        check("vB/d12-basis-change-resets", b.turn_state().get("schedule_denials"), 1)
        aiqt_hooks._orch_record_denial(str(b.root), {"schedule_denials": 2, "schedule_basis": "X"},
                                       "schedule_idle", "X")
        check("vB/d12-same-basis-increments", b.turn_state().get("schedule_denials"), 3)
        # D12(ii): the basis is class-tagged so an actionable/cannot-evaluate flip changes it
        b.set_items([item("Z-1")])
        _c, _t2, basis_a = aiqt_hooks._orch_build_ctx(
            aiqt_hooks._orch_registry(str(b.root))[1], str(b.root), "schedule_idle", b.payload("Stop"))
        check("vB/d12-basis-class-tagged", "a:Z-1" in basis_a, True)
        # CONV4-CX2: waiting and blocked ids are in the decide-basis (w:/b: tagged), so swapping one
        # blocked/waiting item for another is a CHANGED basis and cannot buy premature cap relief. Without
        # the fix the basis omits them and this "b:BL-1" membership check fails.
        b.pending.write_text("| PD-9 | 2026-01-01 | q | RAISED |\n", encoding="utf-8")
        b.set_items([item("BL-1", blocker={"kind": "human-decision", "ref": "PD-9"})])
        _c2, _t3, basis_b = aiqt_hooks._orch_build_ctx(
            aiqt_hooks._orch_registry(str(b.root))[1], str(b.root), "schedule_idle", b.payload("Stop"))
        check("vB/cx2-blocked-in-basis", "b:BL-1:human-decision:PD-9" in basis_b, True)
        # future-mtime lease is not "fresh forever": a far-future mtime reads stale, so scope is not live
        future = _time.time() + 3600 * 24 * 365
        os.utime(str(b.lease), (future, future))
        check("vB/future-mtime-not-live",
              aiqt_hooks._orch_scope_live(aiqt_hooks._orch_registry(str(b.root))[1],
                                          str(b.root), "s1"), False)

        # ---------- substrate: the dispatch ledger writer ----------
        led = aiqt_hooks.orch_dispatch_ledger(t.payload(
            "PostToolUse", "Bash", {"command": "python3 build.py", "run_in_background": True}))
        check("ledger/launch-recorded", _verdict(led) in ("allow", "warn"), True)
        tsd = Path(aiqt_hooks._orch_state_dir_for_root(str(t.root)))
        text = (tsd / "dispatch-ledger.jsonl").read_text(encoding="utf-8")
        check("ledger/launch-row", '"launch"' in text, True)
        aiqt_hooks.orch_dispatch_ledger(t.payload(
            "PostToolUse", "TaskOutput", {"task_id": text and json.loads(
                text.splitlines()[0])["task_id"]}))
        text = (tsd / "dispatch-ledger.jsonl").read_text(encoding="utf-8")
        check("ledger/complete-row", '"complete"' in text, True)

        # ---------- component 5: the resume audit and barrier ----------
        r = Fixture(tmp, "resume")
        r.handoff.write_text("Branch: feature/other\n", encoding="utf-8")
        res = aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))
        check("resume/branch-divergence-warns", _verdict(res), "warn")
        rsd = Path(aiqt_hooks._orch_state_dir_for_root(str(r.root)))
        barrier = json.loads((rsd / "resume-barrier.json").read_text(encoding="utf-8"))
        check("resume/barrier-armed", barrier.get("active"), True)
        # declared-but-unreadable record surface -> cannot-evaluate finding, barrier holds
        r.handoff.write_text("Branch: main\n", encoding="utf-8")
        r.findings.unlink()
        check("resume/unreadable-record-warns",
              _verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))), "warn")
        # correcting the record clears the barrier
        r.findings.write_text("", encoding="utf-8")
        check("resume/clean-is-silent",
              _verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))), "allow")
        barrier = json.loads((rsd / "resume-barrier.json").read_text(encoding="utf-8"))
        check("resume/barrier-cleared", barrier.get("active"), False)
        # CONV4-G2: a lease mtime in the future (beyond clock skew) is a resume-audit finding (clock skew
        # or tamper), so the audit warns and re-arms; without the fix a future mtime reads as fresh and
        # the audit stays silent.
        os.utime(str(r.lease), (_time.time() + 3600 * 24, _time.time() + 3600 * 24))
        check("resume/future-lease-mtime-warns",
              _verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))), "warn")
        # CONV6-F: the future-mtime finding fires even with NO max_age_hours (round-5 gated it behind
        # max_age>0). Drop the horizon from the registry lease, keep the future mtime, expect a warn.
        _regp = r.root / ".aiqt" / "orchestration.local.json"
        _reg = json.loads(_regp.read_text(encoding="utf-8"))
        _reg_lease_saved = _reg["lease"]
        _reg["lease"] = {"path": str(r.lease)}
        _regp.write_text(json.dumps(_reg), encoding="utf-8")
        check("resume/future-lease-no-maxage-warns",
              _verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))), "warn")
        _reg["lease"] = _reg_lease_saved
        _regp.write_text(json.dumps(_reg), encoding="utf-8")
        os.utime(str(r.lease), None)  # restore a fresh mtime so later tests reusing this fixture see a live lease
        # barrier bake behaviour: armed -> out-of-scope mutation surfaces, record write is silent
        (rsd / "resume-barrier.json").write_text(
            json.dumps({"active": True, "findings": ["f"], "warned": False}), encoding="utf-8")
        check("barrier/mutation-surfaces",
              _verdict(aiqt_hooks.orch_resume_barrier(r.payload(
                  "PreToolUse", "Write", {"file_path": str(r.root / "src.py"),
                                          "content": "x"}))), "warn")
        check("barrier/record-write-stays-silent",
              _verdict(aiqt_hooks.orch_resume_barrier(r.payload(
                  "PreToolUse", "Write", {"file_path": str(r.findings),
                                          "content": "x"}))), "allow")

        # ---------- substrate: the prompt stamp ----------
        p = aiqt_hooks.orch_prompt_stamp(r.payload("UserPromptSubmit",
                                                   extra={"prompt": "hello"}))
        code, obj, _ = p
        check("stamp/exit0", code, 0)
        st = r.turn_state()
        check("stamp/human-input-stamped", bool(st.get("last_human_input_utc")), True)
        check("stamp/counters-reset", st.get("stop_denials", 0), 0)

        # ---------- pure-core spot checks (decide_yield directly) ----------
        base = {"kind": "stop", "escape": False, "loop_signal": False, "counter": 0,
                "enum_status": "ok", "enum_detail": "", "actionable": [], "waiting": [],
                "blocked": [], "proposed": [], "wake_named": None,
                "schedule_denials": 0, "basis_unchanged": False}
        v, _r, _d = aiqt_hooks.decide_yield(dict(base))
        check("core/empty-backlog-stop-allows", v, "ALLOW")
        v, _r, _d = aiqt_hooks.decide_yield(dict(base, kind="schedule_idle",
                                                 enum_status="ENUMERATOR_ERROR"))
        check("core/schedule-enum-error-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(dict(base, actionable=[("A", "t", "no blocker")],
                                                 counter=2))
        check("core/loop-bound-findings", v, "ALLOW_WITH_FINDINGS")
        # FIX 1: the STOP path now fails CLOSED-continue on a cannot-evaluate. A whole-enumerator
        # failure and an item-level cannot-evaluate both DENY the stop below the loop bound (they used
        # to ALLOW_WITH_FINDINGS); the operator escape still releases, and the loop bound is still a
        # bounded ALLOW_WITH_FINDINGS exit.
        v, _r, _d = aiqt_hooks.decide_yield(dict(base, enum_status="ENUMERATOR_ERROR"))
        check("core/stop-enum-error-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(
            dict(base, cannot_evaluate=[("CE", "cannot-evaluate", "held")]))
        check("core/stop-cannot-evaluate-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(
            dict(base, escape=True, enum_status="ENUMERATOR_ERROR"))
        check("core/stop-escape-releases-under-enum-error", v, "ALLOW")
        v, _r, _d = aiqt_hooks.decide_yield(
            dict(base, enum_status="ENUMERATOR_ERROR", counter=2))
        check("core/stop-enum-error-loop-bound-findings", v, "ALLOW_WITH_FINDINGS")
        # CONV2-CX2: wake-hygiene denials are cap-relieved like every other schedule DENY
        wake_ctx = dict(base, kind="schedule_idle", waiting=[("W-1", "tracked-task", "t9")],
                        wake_named=False, basis_unchanged=True)
        v, _r, _d = aiqt_hooks.decide_yield(dict(wake_ctx, schedule_denials=1))
        check("core/wake-hygiene-below-cap-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(dict(wake_ctx, schedule_denials=3))
        check("core/wake-hygiene-at-cap-findings", v, "ALLOW_WITH_FINDINGS")

        # ---------- C.2: the attestation register for blocker evidence ----------
        # The no-register behaviour stays byte-identical and is already covered above by the fixture-f
        # external-evidence legs (stop/proven-blockers-allow, stop/blank-evidence-denies).
        import hashlib as _hashlib

        def chained(*rows):
            out, prev = [], "0" * 64
            for r in rows:
                line = json.dumps(dict(r, prev=prev), sort_keys=True)
                out.append(line)
                prev = _hashlib.sha256(line.encode("utf-8")).hexdigest()
            return "\n".join(out) + "\n"

        a = Fixture(tmp, "attest")
        at_reg = a.root / "attestations.jsonl"
        mr_reg = a.root / "mistakes.jsonl"
        mr_reg.write_text("", encoding="utf-8")
        _aregp = a.root / ".aiqt" / "orchestration.local.json"
        _areg_raw = json.loads(_aregp.read_text(encoding="utf-8"))
        _areg_raw["attestations"] = str(at_reg)
        _areg_raw["mistakes_register"] = str(mr_reg)
        _aregp.write_text(json.dumps(_areg_raw), encoding="utf-8")
        astop = lambda: aiqt_hooks.orch_stop_guard(a.payload("Stop"))
        asched = lambda ti: aiqt_hooks.orch_yield_tool(a.payload("PreToolUse", "ScheduleWakeup", ti))
        ext = lambda iid, ref: item(iid, blocker={"kind": "external", "ref": ref,
                                                  "evidence": "run pending",
                                                  "observed_at_utc": now_iso(1)})
        def at_anchor(text):
            lines = text.splitlines()
            payload = ({"seq": len(lines), "digest": _hashlib.sha256(
                lines[-1].encode("utf-8")).hexdigest()} if lines else {"seq": 0, "digest": "0" * 64})
            Path(str(at_reg) + ".anchor").write_text(json.dumps(payload), encoding="utf-8")

        def write_at(text):
            at_reg.write_text(text, encoding="utf-8")
            at_anchor(text)

        # declared register with NO validated snapshot yet: held cannot-evaluate (stop DENIES -> block2, schedule denies)
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/no-snapshot-stop-holds", _verdict(astop()), "block2")
        a.set_turn_state({})
        check("attest/no-snapshot-schedule-denies",
              _verdict(asched({"prompt": "recheck AT-I1"})), "deny")
        # an APPROVED (accepted) validated row that does NOT cover the blocker's ref: no block
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests other", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "other", "check_ref": "seed.txt"}))
        areg = aiqt_hooks._orch_registry(str(a.root))[1]
        check("attest/validate-clean",
              aiqt_hooks._orch_validate_attestations(areg, str(a.root)), [])
        a.set_turn_state({})
        check("attest/unattested-ref-denies", _verdict(astop()), "block2")
        # the same blocker WITH a fresh approved attestation covering its ref: blocked -> allow
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"}))
        aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        a.set_turn_state({})
        check("attest/attested-ref-allows", _verdict(astop()), "allow")
        # a row whose pointer resolves to nothing is unsubstantiated: a finding, attesting nothing
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"},
            {"seq": 2, "id": "AT-2", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests dep", "evidence": "", "rule": "corexc",
             "guardrail": "n/a", "ref": "dep", "check_ref": "nosuch-integrity-signal.txt"}))
        u_findings = aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        check("attest/unsubstantiated-finding", any("AT-2" in x for x in u_findings), True)
        asd = Path(aiqt_hooks._orch_state_dir_for_root(str(a.root)))
        snap = json.loads((asd / "attestations-validated.json").read_text(encoding="utf-8"))
        check("attest/unsubstantiated-in-snapshot", snap.get("unsubstantiated"), ["AT-2"])
        check("attest/substantiated-ref-kept", "ci" in snap.get("refs", []), True)
        # FIX 3: a row whose latest status is merely 'proposed' is NOT approved -> unsubstantiated
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "proposed",
             "mistake": "m", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"}))
        p_findings = aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        check("attest/proposed-status-unsubstantiated",
              any("AT-1" in x and "not approved" in x for x in p_findings), True)
        psnap = json.loads((asd / "attestations-validated.json").read_text(encoding="utf-8"))
        check("attest/proposed-not-in-refs", "ci" not in psnap.get("refs", []), True)
        # FIX 3: a hand-forged snapshot (fabricated refs, bogus register binding) is NOT trusted at
        # yield: the reader re-anchors and the binding fails -> HOLD
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"}))
        (asd / "attestations-validated.json").write_text(json.dumps(
            {"version": 2, "ts": now_iso(0), "status": "ok", "refs": ["ci"], "unsubstantiated": [],
             "register": "/forged/path", "seq": 99, "digest": "deadbeef"}), encoding="utf-8")
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/forged-snapshot-holds", _verdict(astop()), "block2")
        # FIX 3: a register whose anchor no longer verifies (a row rewritten without advancing the
        # anchor) fails the append-only authority -> HOLD, never read as a smaller clean register
        good_ci = chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"})
        write_at(good_ci)
        aiqt_hooks._orch_validate_attestations(areg, str(a.root))  # ok snapshot bound to this tip
        tampered_ci = chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests DEP not ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "dep", "check_ref": "seed.txt"})
        at_reg.write_text(tampered_ci, encoding="utf-8")  # rewrite WITHOUT advancing the anchor
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/anchor-tamper-holds", _verdict(astop()), "block2")
        # FIX A1 (discriminating): a snapshot bound to the REAL register tip but carrying a FABRICATED
        # ref is NOT trusted. The yield reader re-derives refs from the register content, so the fake
        # ref substantiates nothing (the round-1 impl trusted snap['refs'] and would have ALLOWED).
        write_at(good_ci)  # a clean chain whose only approved, resolving ref is 'ci'
        aiqt_hooks._orch_validate_attestations(areg, str(a.root))  # ok snapshot bound to this tip
        _bound = json.loads((asd / "attestations-validated.json").read_text(encoding="utf-8"))
        (asd / "attestations-validated.json").write_text(
            json.dumps(dict(_bound, refs=["phantom"])), encoding="utf-8")  # real binding, fake ref
        a.set_items([ext("AT-I1", "phantom")])
        a.set_turn_state({})
        check("attest/real-bound-fabricated-ref-not-substantiated", _verdict(astop()), "block2")
        # control: the genuinely substantiated 'ci' ref from that same re-derivation still blocks
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/rederived-real-ref-still-allows", _verdict(astop()), "allow")
        # A2 residual (DISCLOSED, not a HOLD): an fs-write actor who APPENDS a valid approved row (and,
        # for an anchor-authority register, advances the companion anchor, both filesystem writes, no
        # git) DOES substantiate the appended ref. This records the TRUE behaviour so the suite is not
        # a false HOLD; the categorical closure is an OS-owned, non-writable register.
        write_at(chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"},
            {"seq": 2, "id": "AT-2", "ts": now_iso(0), "status": "accepted",
             "mistake": "appended approved row", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "dep", "check_ref": "seed.txt"}))
        aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        a.set_items([ext("AT-I1", "dep")])
        a.set_turn_state({})
        check("attest/appended-approved-row-substantiates", _verdict(astop()), "allow")
        # FIX 3: a failed snapshot write invalidates any prior 'ok' snapshot (delete-then-hold)
        write_at(good_ci)
        aiqt_hooks._orch_validate_attestations(areg, str(a.root))  # fresh ok snapshot present
        check("attest/ok-snapshot-present", (asd / "attestations-validated.json").exists(), True)
        _o_wja = aiqt_hooks._orch_write_json_atomic
        try:
            aiqt_hooks._orch_write_json_atomic = lambda p, o: False
            aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        finally:
            aiqt_hooks._orch_write_json_atomic = _o_wja
        check("attest/failed-write-invalidates-snapshot",
              (asd / "attestations-validated.json").exists(), False)
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/failed-write-holds", _verdict(astop()), "block2")
        # a broken chain is held, never read as a smaller clean register
        at_reg.write_text('{"seq": 1, "id": "AT-1", "prev": "beef"}\n', encoding="utf-8")
        check("attest/broken-chain-finding",
              bool(aiqt_hooks._orch_validate_attestations(areg, str(a.root))), True)
        a.set_turn_state({})
        check("attest/broken-chain-stop-holds", _verdict(astop()), "block2")

        # ---------- GD-127: the systemic-lapse lifecycle (verdict-preservation legs) ----------
        regtool = str(repo_root() / "tools" / "orch_register.py")

        def mr_append(rid, check_ref):
            subprocess.run([sys.executable, regtool, "append", "--register", str(mr_reg),
                            "--id", rid, "--mistake", "premature wind-down claim",
                            "--evidence", "resume-audit finding", "--rule", "cntdef",
                            "--guardrail", "stop-guard hardening", "--klass", "systemic-lapse",
                            "--check-ref", check_ref],
                           check=True, capture_output=True, timeout=30)

        mr_append("MR-1", "seed.txt")
        check("lapse/klass-recorded",
              '"class": "systemic-lapse"' in mr_reg.read_text(encoding="utf-8"), True)
        proj = subprocess.run([sys.executable, regtool, "project", "--register", str(mr_reg)],
                              check=True, capture_output=True, text=True, timeout=30)
        lapse_items = json.loads(proj.stdout)["items"]
        # (a) a lapse row plus one actionable item: still DENY, no bypass of any kind
        a.set_items(lapse_items + [item("A-20")])
        a.set_turn_state({})
        check("lapse/no-bypass-still-denies", _verdict(astop()), "block2")
        # (b) the park: the lapse row stays proposed while the other item is blocked on a recorded
        # pending decision, reaching the existing no-actionable ALLOW (quiescence, not an escape)
        a.pending.write_text("| DEC-1 | 2026-09-03 | lapse triage | RAISED |\n", encoding="utf-8")
        a.set_items(lapse_items + [item("A-21",
                                        blocker={"kind": "human-decision", "ref": "DEC-1"})])
        a.set_turn_state({})
        check("lapse/park-allows-with-disposition", _verdict(astop()), "allow")
        # (c) a lapse row whose pointer resolves to nothing is unsubstantiated (a resume-audit
        # finding) and blocks nothing
        mr_append("MR-2", "nosuch-integrity-signal.txt")
        l_findings = aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        check("lapse/unsubstantiated-lapse-finding", any("MR-2" in x for x in l_findings), True)
        a.set_turn_state({})
        check("lapse/unsubstantiated-blocks-nothing", _verdict(astop()), "allow")

        # ---------- C.3: the anti-shrinkage checkpoint union ----------
        c = Fixture(tmp, "ckpt")
        cstop = lambda: aiqt_hooks.orch_stop_guard(c.payload("Stop"))
        csched = lambda ti: aiqt_hooks.orch_yield_tool(c.payload("PreToolUse", "ScheduleWakeup", ti))
        cblocked = lambda iid: item(iid, blocker={"kind": "external", "ref": "up-" + iid,
                                                  "evidence": "vendor outage",
                                                  "observed_at_utc": now_iso(1)})
        # first window: no checkpoint yet, no comparison (the disclosed residual)
        c.set_items([cblocked("CK-A"), cblocked("CK-B")])
        c.set_turn_state({})
        check("ckpt/first-window-allows", _verdict(cstop()), "allow")
        # CK-B vanishes with no close receipt: held as cannot-evaluate (FIX 1 DENIES -> block2, never a clean allow);
        # reverting C.3 makes this leg fail on "allow"
        c.set_items([cblocked("CK-A")], keep_checkpoint=True)
        c.set_turn_state({})
        check("ckpt/shrink-stop-holds", _verdict(cstop()), "block2")
        c.set_turn_state({})
        check("ckpt/shrink-schedule-denies", _verdict(csched({"prompt": "recheck CK-A"})), "deny")
        # a closed row is the receipt: CK-B leaves the checkpoint and clean behaviour returns
        c.set_items([cblocked("CK-A"), item("CK-B", state="closed")], keep_checkpoint=True)
        c.set_turn_state({})
        check("ckpt/receipt-allows", _verdict(cstop()), "allow")
        c.set_items([cblocked("CK-A")], keep_checkpoint=True)
        c.set_turn_state({})
        check("ckpt/receipted-absence-allows", _verdict(cstop()), "allow")
        # an unreadable checkpoint injects one marker row and is rewritten fresh
        (c.state / "backlog-checkpoint.json").write_text("{broken", encoding="utf-8")
        c.set_turn_state({})
        check("ckpt/malformed-holds-once", _verdict(cstop()), "block2")
        c.set_turn_state({})
        check("ckpt/rewritten-fresh-allows", _verdict(cstop()), "allow")
        # FIX 4: a checkpoint deleted after a prior window (init marker present) is a possible reset ->
        # cannot-evaluate, never a silent fresh first window. Reverting FIX 4 makes this allow.
        check("ckpt/marker-written", (c.state / "checkpoint-init.marker").exists(), True)
        (c.state / "backlog-checkpoint.json").unlink()
        c.set_items([cblocked("CK-A")], keep_checkpoint=True)
        c.set_turn_state({})
        check("ckpt/deleted-after-init-holds", _verdict(cstop()), "block2")
        # a genuinely never-initialised dir (no marker, no checkpoint) is still a clean first window
        c2 = Fixture(tmp, "ckpt2")
        c2stop = lambda: aiqt_hooks.orch_stop_guard(c2.payload("Stop"))
        c2.set_items([cblocked("CK-A")])
        c2.set_turn_state({})
        check("ckpt/never-init-first-window-allows", _verdict(c2stop()), "allow")
        # FIX 2: the OTHER incident variant - a previously-actionable id DEMOTED to proposed/ungranted
        # (not vanished) routes to the harmless proposed bucket and evades both the deny and the vanish
        # check. The checkpoint now stores an eligibility label, detects the demotion, and injects a
        # cannot-evaluate that (under FIX 1) DENIES the stop.
        cd = Fixture(tmp, "ckpt-demote")
        cdstop = lambda: aiqt_hooks.orch_stop_guard(cd.payload("Stop"))
        cd.set_items([cblocked("CK-D")])                                     # window 1: eligible, clean
        cd.set_turn_state({})
        check("ckpt/demote-window1-allows", _verdict(cdstop()), "allow")
        cd.set_items([item("CK-D", state="proposed")], keep_checkpoint=True) # demote: state proposed
        cd.set_turn_state({})
        check("ckpt/demote-to-proposed-denies", _verdict(cdstop()), "block2")
        cd.set_items([cblocked("CK-D")], keep_checkpoint=False)              # fresh eligible window
        cd.set_turn_state({})
        check("ckpt/demote-reset-window1-allows", _verdict(cdstop()), "allow")
        cd.set_items([item("CK-D", granted=False)], keep_checkpoint=True)    # demote: ungranted (state
        cd.set_turn_state({})                                                # still open; state alone
        check("ckpt/demote-ungranted-denies", _verdict(cdstop()), "block2")  # would miss it)
        cd.set_items([cblocked("CK-D")], keep_checkpoint=False)              # discriminating: NO demote
        cd.set_turn_state({})
        check("ckpt/demote-window1b-allows", _verdict(cdstop()), "allow")
        cd.set_items([cblocked("CK-D")], keep_checkpoint=True)               # stays eligible -> allow
        cd.set_turn_state({})                                                # (mere presence never denies)
        check("ckpt/no-demote-still-allows", _verdict(cdstop()), "allow")
        # FIX C: a failed init-marker write is fail-loud, not a silent gap. Patch the atomic writer to
        # fail ONLY the marker (the checkpoint write still succeeds) on a never-initialised dir so the
        # marker path is reached; assert a cannot-evaluate injection (FIX 1 DENIES -> block2, not a clean
        # allow) plus a guard event. Reverting FIX C makes this leg allow with no event.
        cm = Fixture(tmp, "ckpt-marker")
        cmstop = lambda: aiqt_hooks.orch_stop_guard(cm.payload("Stop"))
        cmsd = Path(aiqt_hooks._orch_state_dir_for_root(str(cm.root)))
        cm.set_items([cblocked("CM-A")])  # a blocked-only backlog -> otherwise a clean allow
        cm.set_turn_state({})
        _o_wja3 = aiqt_hooks._orch_write_json_atomic
        try:
            aiqt_hooks._orch_write_json_atomic = (
                lambda p, o: False if str(p).endswith("checkpoint-init.marker") else _o_wja3(p, o))
            check("ckpt/marker-unwritable-holds", _verdict(cmstop()), "block2")
        finally:
            aiqt_hooks._orch_write_json_atomic = _o_wja3
        check("ckpt/marker-unwritable-guard-event",
              "checkpoint-marker-unwritable" in (cmsd / "guard-events.jsonl").read_text(
                  encoding="utf-8"), True)
        # FIX 1 preview immutability (record_checkpoint=False, the tools/orch_preflight.py posture):
        # the preview COMPUTES injections but writes NOTHING. The backlog is driven OVER
        # _ORCH_CHECKPOINT_MAX so a bound-forced drop WOULD emit a "checkpoint-bound" guard-event AND
        # rewrite the checkpoint + marker on the real path; under preview the state dir stays untouched,
        # so the no-guard-events leg is DISCRIMINATING (making the bound-drop event unconditional under
        # preview fails it), not vacuous on a below-bound backlog that emits no event either way.
        pv = Fixture(tmp, "preview")
        pvreg = aiqt_hooks._orch_registry(str(pv.root))[1]
        pvsd = Path(aiqt_hooks._orch_state_dir_for_root(str(pv.root)))
        pv.set_items([item("PV-{:05d}".format(n))
                      for n in range(aiqt_hooks._ORCH_CHECKPOINT_MAX + 50)])
        pv.set_turn_state({})  # creates the state dir; the three probed files must stay absent
        aiqt_hooks._orch_build_ctx(pvreg, str(pv.root), "stop", {}, record_checkpoint=False)
        check("preview/no-checkpoint", (pvsd / "backlog-checkpoint.json").exists(), False)
        check("preview/no-marker", (pvsd / "checkpoint-init.marker").exists(), False)
        check("preview/no-guard-events", (pvsd / "guard-events.jsonl").exists(), False)
        # contrast: the real hook path (record_checkpoint default True) DOES write checkpoint + marker
        # AND, the backlog being over the bound, emits the "checkpoint-bound" guard-event the preview
        # suppressed (proving the guard-event path was genuinely exercised, so the leg above discriminates)
        aiqt_hooks._orch_build_ctx(pvreg, str(pv.root), "stop", {})
        check("preview/real-path-writes-checkpoint",
              (pvsd / "backlog-checkpoint.json").exists(), True)
        check("preview/real-path-writes-marker", (pvsd / "checkpoint-init.marker").exists(), True)
        check("preview/real-path-emits-bound-drop",
              (pvsd / "guard-events.jsonl").exists()
              and "checkpoint-bound" in (pvsd / "guard-events.jsonl").read_text(encoding="utf-8"),
              True)

        # ---------- C.4: forced-exit recording ----------
        d = Fixture(tmp, "forced")
        dstop = lambda: aiqt_hooks.orch_stop_guard(d.payload("Stop"))
        dsd = Path(aiqt_hooks._orch_state_dir_for_root(str(d.root)))
        d.set_items([item("FX-1")])
        d.set_turn_state({})
        check("forced/deny-1", _verdict(dstop()), "block2")
        check("forced/deny-2", _verdict(dstop()), "block2")
        check("forced/bound-exit-warns", _verdict(dstop()), "warn")
        check("forced/artefact-written", (dsd / "forced-exit.jsonl").exists(), True)
        frows, _fbad = aiqt_hooks._orch_read_jsonl(str(dsd / "forced-exit.jsonl"))
        check("forced/artefact-names-open-ids", frows[-1].get("open_ids"), ["FX-1"])
        check("forced/guard-event-kind",
              '"forced_unresolved"' in (dsd / "guard-events.jsonl").read_text(encoding="utf-8"),
              True)
        # the next resume audit surfaces the pending record ONCE and arms the warn-first barrier
        check("forced/resume-audit-surfaces",
              _verdict(aiqt_hooks.orch_resume_audit(d.payload("SessionStart"))), "warn")
        dbarrier = json.loads((dsd / "resume-barrier.json").read_text(encoding="utf-8"))
        check("forced/barrier-armed-warn-first", dbarrier.get("active"), True)
        check("forced/surfaced-set-written", (dsd / "forced-exit-surfaced.json").exists(), True)
        check("forced/clean-after-triage",
              _verdict(aiqt_hooks.orch_resume_audit(d.payload("SessionStart"))), "allow")
        # a failed forced-exit record surfaces in the warn banner itself (the write seam injects the
        # failure; the fixture state dir stays untouched, per test-hermeticity)
        _orig_append = aiqt_hooks._orch_append_jsonl
        try:
            aiqt_hooks._orch_append_jsonl = lambda path, obj: False
            d.set_items([item("FX-2")])
            d.set_turn_state({"stop_denials": 2})
            fcode, fobj, _ferr = dstop()
            check("forced/failed-record-in-banner",
                  fcode == 0 and isinstance(fobj, dict)
                  and "forced-exit record could not be fully persisted"
                  in fobj.get("systemMessage", ""), True)
        finally:
            aiqt_hooks._orch_append_jsonl = _orig_append

        # ---------- C.4 FIX 5: cap-relief over a BLOCKED row + append-only no-clobber ----------
        e = Fixture(tmp, "forced5")
        esched = lambda ti: aiqt_hooks.orch_yield_tool(
            e.payload("PreToolUse", "ScheduleWakeup", ti))
        esd = Path(aiqt_hooks._orch_state_dir_for_root(str(e.root)))
        # a not-before blocker 48h in the future -> blocked (no actionable, no cannot-evaluate)
        e.set_items([item("BW-1", blocker={"kind": "not-before", "ref": now_iso(-48)})])
        e.set_turn_state({})
        check("forced5/deny-1", _verdict(esched({"prompt": "waiting"})), "deny")
        check("forced5/deny-2", _verdict(esched({"prompt": "waiting"})), "deny")
        check("forced5/deny-3", _verdict(esched({"prompt": "waiting"})), "deny")
        # the 4th call is cap-relieved (ALLOW_WITH_FINDINGS) over a BLOCKED row: the OLD gate left this
        # unrecorded; FIX 5 records it.
        check("forced5/cap-exit-warns", _verdict(esched({"prompt": "waiting"})), "warn")
        check("forced5/recorded-over-blocked", (esd / "forced-exit.jsonl").exists(), True)
        e1, _b1 = aiqt_hooks._orch_read_jsonl(str(esd / "forced-exit.jsonl"))
        check("forced5/blocked-id-recorded", e1[-1].get("open_ids"), ["BW-1"])
        # a SECOND forced exit appends (append-only), never clobbering the first
        check("forced5/cap-exit-warns-2", _verdict(esched({"prompt": "waiting"})), "warn")
        e2, _b2 = aiqt_hooks._orch_read_jsonl(str(esd / "forced-exit.jsonl"))
        check("forced5/two-rows-appended", len(e2), 2)
        # the resume audit surfaces BOTH exactly once, then a second resume is clean
        check("forced5/resume-surfaces-both",
              _verdict(aiqt_hooks.orch_resume_audit(e.payload("SessionStart"))), "warn")
        check("forced5/second-resume-clean",
              _verdict(aiqt_hooks.orch_resume_audit(e.payload("SessionStart"))), "allow")

        # FIX 1 (self-discriminating): under a whole-enumerator failure (cannot-evaluate) a BELOW-BOUND
        # stop DENIES with NO escape, and an operator-owned escape sentinel STILL releases it. Asserting
        # BOTH here makes the leg fail on a FIX-1 revert AT THIS LEG (the no-escape verdict flips block2 ->
        # warn), not only at the pure-core legs (core/stop-enum-error-denies, core/stop-cannot-evaluate-
        # denies) or the f/ce integration legs above. Proves the escape (not a fail-open) is the release.
        _orig_es2 = aiqt_hooks._orch_escape_stat
        try:
            f.enum_exit.write_text("3", encoding="utf-8")
            aiqt_hooks._orch_escape_stat = lambda path: None                 # escape ABSENT
            f.set_turn_state({})
            check("stop/enum-error-denies-without-escape", _verdict(stop()), "block2")
            aiqt_hooks._orch_escape_stat = lambda path: _St(_stat.S_IFREG | 0o600, os.geteuid() + 1)
            f.set_turn_state({})
            check("stop/escape-releases-under-enum-error", _verdict(stop()), "allow")
        finally:
            aiqt_hooks._orch_escape_stat = _orig_es2
            f.enum_exit.write_text("0", encoding="utf-8")
        if (sd / "escape-spoof.json").exists():
            (sd / "escape-spoof.json").unlink()
        # FIX 1 + C.4: the below-bound cannot-evaluate DENIES (ce/enum-error-denies -> block2 below is the
        # FIX-1 discriminator for this leg: a revert flips it to warn) and records NO forced exit (C.4
        # fires only on the bounded ALLOW_WITH_FINDINGS, never on a deny); the SAME enum-error, once the
        # loop bound is reached, DOES record one. So ce/no-forced-exit-on-deny is read against
        # ce/enum-error-denies, discriminating the deny (no record) from the unchanged bound-exit.
        ce = Fixture(tmp, "ce_forced")
        cestop = lambda: aiqt_hooks.orch_stop_guard(ce.payload("Stop"))
        cesd = Path(aiqt_hooks._orch_state_dir_for_root(str(ce.root)))
        ce.set_items([item("CE-1")])
        ce.enum_exit.write_text("3", encoding="utf-8")
        ce.set_turn_state({})
        check("ce/enum-error-denies", _verdict(cestop()), "block2")
        check("ce/no-forced-exit-on-deny", (cesd / "forced-exit.jsonl").exists(), False)
        ce.set_turn_state({"stop_denials": aiqt_hooks._ORCH_LOOP_BOUND})
        check("ce/bound-exit-warns", _verdict(cestop()), "warn")
        check("ce/forced-exit-on-bound", (cesd / "forced-exit.jsonl").exists(), True)

        # FIX 6: the escape-spoof record is FAIL-LOUD on EVERY verdict path, not only the clean
        # stop-ALLOW. Force a spoof whose record fails, then assert the warning surfaces on a stop
        # DENY, a stop ALLOW_WITH_FINDINGS, a yield DENY, and a yield ALLOW.
        g = Fixture(tmp, "spoof6")
        gstop = lambda: aiqt_hooks.orch_stop_guard(g.payload("Stop"))
        gsched = lambda ti: aiqt_hooks.orch_yield_tool(
            g.payload("PreToolUse", "ScheduleWakeup", ti))
        gblk = lambda iid: item(iid, blocker={"kind": "external", "ref": "up-" + iid,
                                              "evidence": "vendor outage",
                                              "observed_at_utc": now_iso(1)})
        _o_active = aiqt_hooks._orch_escape_active
        _o_spoof = aiqt_hooks._orch_record_escape_spoof
        try:
            aiqt_hooks._orch_escape_active = lambda reg, root: (False, "forced-spoof-detail")
            aiqt_hooks._orch_record_escape_spoof = lambda root, detail: "SPOOF-UNRECORDED"
            # (1) stop DENY over an actionable item: warning rides the block reason (err)
            g.set_items([item("SP-1")])
            g.set_turn_state({})
            _c, _o, gerr = gstop()
            check("spoof6/stop-deny-surfaces", "SPOOF-UNRECORDED" in (gerr or ""), True)
            # (2) stop ALLOW_WITH_FINDINGS (loop bound reached): warning rides the warn banner
            g.set_items([item("SP-1")])
            g.set_turn_state({"stop_denials": aiqt_hooks._ORCH_LOOP_BOUND})
            _c, gobj, _e = gstop()
            check("spoof6/stop-awf-surfaces",
                  "SPOOF-UNRECORDED" in (gobj or {}).get("systemMessage", ""), True)
            # (3) yield DENY (schedule past an actionable backlog): warning rides the deny reason
            g.set_items([item("SP-1")])
            g.set_turn_state({})
            _c, gyd, _e = gsched({"prompt": "recheck SP-1"})
            check("spoof6/yield-deny-surfaces",
                  "SPOOF-UNRECORDED" in (gyd or {}).get(
                      "hookSpecificOutput", {}).get("permissionDecisionReason", ""), True)
            # (4) yield ALLOW (blocked-only backlog, clean allow): warning rides systemMessage
            g.set_items([gblk("SP-2")])
            g.set_turn_state({})
            _c, gya, _e = gsched({"prompt": "recheck SP-2"})
            check("spoof6/yield-allow-surfaces",
                  "SPOOF-UNRECORDED" in (gya or {}).get("systemMessage", ""), True)
            # (5) yield ALLOW_WITH_FINDINGS (schedule cap-relieved past an actionable backlog): the
            # spoof warning rides the systemMessage on the cap-relief branch too, not only clean ALLOW.
            # Three denials on an unchanged basis reach the cap; the fourth call is cap-relieved.
            g.set_items([item("SP-3")])
            g.set_turn_state({})
            for _ in range(aiqt_hooks._ORCH_SCHEDULE_CAP):
                gsched({"prompt": "recheck SP-3"})
            _c, gyawf, _e = gsched({"prompt": "recheck SP-3"})
            check("spoof6/yield-awf-verdict", _verdict((_c, gyawf, _e)), "warn")
            check("spoof6/yield-awf-surfaces",
                  "SPOOF-UNRECORDED" in (gyawf or {}).get("systemMessage", ""), True)
        finally:
            aiqt_hooks._orch_escape_active = _o_active
            aiqt_hooks._orch_record_escape_spoof = _o_spoof
        # FIX 5 (discriminating): the resume surfacing names EVERY forced-exit row's ids, not just the
        # first. Two appended rows with DISTINCT open ids must BOTH appear in the findings text (an
        # impl that emits only the first row but advances the surfaced set over both would fail here).
        h = Fixture(tmp, "forced5b")
        hsd = Path(aiqt_hooks._orch_state_dir_for_root(str(h.root)))
        hsd.mkdir(parents=True, exist_ok=True)
        aiqt_hooks._orch_append_jsonl(str(hsd / "forced-exit.jsonl"),
                                      {"ts": now_iso(0), "event": "Stop", "key": "k1",
                                       "open_ids": ["OX-1"], "reason": "r", "enum_status": "ok"})
        aiqt_hooks._orch_append_jsonl(str(hsd / "forced-exit.jsonl"),
                                      {"ts": now_iso(0), "event": "Stop", "key": "k2",
                                       "open_ids": ["OX-2"], "reason": "r", "enum_status": "ok"})
        _htext = " ".join(aiqt_hooks._orch_forced_exit_findings(str(hsd)))
        check("forced5/surfaces-first-id", "OX-1" in _htext, True)
        check("forced5/surfaces-second-id", "OX-2" in _htext, True)
        check("forced5/both-keys-surfaced",
              set(json.loads((hsd / "forced-exit-surfaced.json").read_text(
                  encoding="utf-8")).get("keys", [])), {"k1", "k2"})
        check("forced5/second-pass-clean", aiqt_hooks._orch_forced_exit_findings(str(hsd)), [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if report_path is not None:
        try:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump({"format_version": 1, "suite": SUITE_ID, "check_ids": EXECUTED}, handle)
                handle.write("\n")
        except OSError as exc:
            # A failed report write must not swallow the assertion diagnostics already collected:
            # surface what the suite found first, then the harness error.
            if FAILURES:
                print("SELF-TEST FAIL:")
                for f_ in FAILURES:
                    print("  - " + f_)
            print("SELF-TEST HARNESS ERROR: cannot write execution report {}: {}".format(
                report_path, exc), file=sys.stderr)
            return 2

    # In-run execution-set self-guard (defence in depth beside tools/check_selftest_execution.py): the
    # executed set reconciles against the hand-authored expectation manifest even on a direct developer
    # run. The report above is written FIRST, so it always reflects what actually executed.
    expected_ids = _expected_check_ids()
    if expected_ids is None:
        return 2
    for cid in sorted(expected_ids - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(cid))
    for cid in sorted(_EXECUTED_SET - expected_ids):
        FAILURES.append("execution-set/extra: {}".format(cid))

    if FAILURES:
        print("SELF-TEST FAIL:")
        for f_ in FAILURES:
            print("  - " + f_)
        return 1
    print("SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
          "tools/selftest_checks.toml".format(len(EXECUTED)))
    print("Coverage narrative (human orientation, not evidence): the stop guard denies enumerated "
          "actionable work and unproven or stale "
          "blockers, allows proven blockers, live tracked tasks, proposed-only backlogs, absent "
          "registry/lease scope, a genuinely operator-owned escape sentinel, and DENIES a BELOW-BOUND "
          "cannot-evaluate (ignorance refuses the wind-down; the operator escape OR the bounded loop-exit "
          "releases); the "
          "schedule path denies on cannot-evaluate with a three-denial cap and "
          "wake hygiene, and the measured quiet figure beats a claimed one; the unattended-ask "
          "blocker reproduces the host hook's regression vectors with an idempotent redacted pending "
          "row; the truncation guard allows a plain metacharacter-free background command and asks on "
          "any shell syntax or reserved word, asks on a foreground bare-& detach while dropping a "
          "word-start `#` comment and failing an unbalanced/ANSI-C quote toward ASK rather than a silent "
          "allow; the ledger records launches and completions; the resume "
          "audit arms and clears the mutation barrier on real record state; the prompt stamp "
          "resets guard counters from genuine human input; an actor-owned, symlinked, or writable "
          "escape sentinel is ignored, recorded, and surfaced once at resume; a declared attestation "
          "register gates external/foreign-lease evidence at audit cadence, holding on an unreadable "
          "surface and surfacing unsubstantiated rows; an id that vanishes from the enumeration "
          "without a close receipt is held by the anti-shrinkage checkpoint; a bound- or cap-released "
          "exit past open work is marked forced_unresolved with a fail-loud record and triaged at "
          "resume; and a systemic-lapse register row never bypasses a verdict, parks behind a "
          "recorded decision, and blocks nothing when unsubstantiated")
    return 0


def _parse_argv(argv):
    """No arguments (unchanged behaviour), or exactly --execution-report ABS_PATH. Anything else,
    including a relative report path, is usage: exit 2."""
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return argv[1]
    print("usage: selftest_orch_hooks.py [--execution-report ABS_PATH] "
          "(the report path must be absolute)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    sys.exit(main(report_path=_parse_argv(sys.argv[1:])))
