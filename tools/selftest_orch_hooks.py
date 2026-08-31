#!/usr/bin/env python3
"""Behavioural self-test for the GD-112 orchestrator-integrity handlers in
.aiqt/core/hooks/scripts/aiqt_hooks.py (the section-e acceptance vectors; authored BEFORE the core,
test-first). Hermetic: every case runs against throwaway fixtures under a temp dir (its own git repo,
its own registry, its own enumerator stub, its own state dir), removed in a finally; nothing on the
host is read or written. Verdicts are judged on the STRUCTURED result each handler returns (the
(code, stdout_obj, stderr) tuple), never by grepping diagnostic prose.

  selftest_orch_hooks.py    exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


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

    def set_items(self, items, version=1):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.enum_payload.write_text(json.dumps(
            {"version": version, "generated_at_utc": now,
             "source": {"locator": "fixture", "revision": "r1", "observed_at_utc": now},
             "items": items}), encoding="utf-8")

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


def main():
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

        # R2/R3: malformed AEI provenance is a cannot-evaluate (stop fails open), never a clean backlog
        nowv = now_iso(0)
        for label, env in [
                ("bool-version", '{"version": true, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % nowv),
                ("float-version", '{"version": 1.0, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % nowv),
                ("unparseable-ts", '{"version": 1, "generated_at_utc": "banana", "source": {"locator":"f"}, "items": []}'),
                ("future-ts", '{"version": 1, "generated_at_utc": "%s", "source": {"locator":"f"}, "items": []}' % now_iso(-48)),
                ("no-locator", '{"version": 1, "generated_at_utc": "%s", "source": {}, "items": []}' % nowv)]:
            f.enum_payload.write_text(env, encoding="utf-8")
            f.set_turn_state({})
            check("stop/malformed-provenance-%s-fails-open" % label, _verdict(stop()), "warn")
        # a blank-evidence external blocker does not prove a block (R3-CX-M7)
        f.set_items([item("A-be", blocker={"kind": "external", "ref": "ci", "evidence": "   ",
                                           "observed_at_utc": now_iso(1)})])
        f.set_turn_state({})
        check("stop/blank-evidence-denies", _verdict(stop()), "block2")
        # a malformed dispatch ledger HOLDS a tracked item: stop fails open, schedule denies (R3-CX-B1)
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "dispatch-ledger.jsonl").write_text("null\n{broken\n", encoding="utf-8")
        f.set_items([item("A-le", blocker={"kind": "tracked-task", "ref": "T-x"})])
        f.set_turn_state({})
        check("stop/malformed-ledger-fails-open", _verdict(stop()), "warn")
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

        # enumerator nonzero exit: stop -> warn (fail-open with findings)
        f.enum_exit.write_text("3", encoding="utf-8")
        f.set_turn_state({})
        check("stop/enum-error-fails-open", _verdict(stop()), "warn")

        # malformed enumeration is ENUMERATOR_ERROR, never an empty backlog
        f.enum_exit.write_text("0", encoding="utf-8")
        f.enum_payload.write_text("{not json", encoding="utf-8")
        f.set_turn_state({})
        check("stop/bad-json-fails-open", _verdict(stop()), "warn")
        f.set_items([item("D-1"), item("D-1")])  # duplicate id
        f.set_turn_state({})
        check("stop/dup-id-fails-open", _verdict(stop()), "warn")
        f.set_items([item("V-1")], version=9)
        f.set_turn_state({})
        check("stop/unknown-version-fails-open", _verdict(stop()), "warn")

        # escape artefact -> allow even with actionable work
        f.set_items([item("A-11")])
        f.set_turn_state({})
        (sd / "ESCAPE-ALLOW-YIELD").write_text("operator\n", encoding="utf-8")
        check("stop/escape-allows", _verdict(stop()), "allow")
        (sd / "ESCAPE-ALLOW-YIELD").unlink()

        # guard-events rows were appended by the denies above
        check("stop/guard-events-written", (sd / "guard-events.jsonl").exists(), True)

        # TeammateIdle: same core; enum error fails OPEN (allows idle, with findings)
        f.enum_exit.write_text("3", encoding="utf-8")
        f.set_turn_state({})
        check("idle/enum-error-fails-open",
              _verdict(aiqt_hooks.orch_teammate_idle(f.payload("TeammateIdle"))), "warn")
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

        # a ScheduleWakeup with stop=true is the STOP kind: enum error fails OPEN, not deny
        g.enum_exit.write_text("3", encoding="utf-8")
        g.set_turn_state({})
        check("yield/stop-kind-fails-open",
              _verdict(sched({"stop": True})), "warn")
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
        for mode_text, want in (("Operating-mode: overnight-unattended\n", "deny"),
                                ("Operating-mode: unattended (overnight; ipad-origin)\n", "deny"),
                                ("Operating-mode: daytime-unattended\n", "deny"),
                                ("Operating-mode: attended-autonomous\n", "allow"),
                                ("Operating-mode: fully-attended\n", "allow"),
                                ("", "allow")):  # absent mode line fails open
            h.mode.write_text(mode_text, encoding="utf-8")
            g_ask = h.payload("PreToolUse", "AskUserQuestion",
                              {"questions": [{"question": "pick one"}]},
                              extra={"tool_use_id": "tu-1"})
            check("ask/mode {!r}".format(mode_text.strip()), _verdict(ask()), want)
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

        # ---------- component 3b: the detached-dispatch guard (EN-9 / trkasy) ----------
        # A SEPARATE sibling of the truncation guard: it hard-DENIES a bare-& backgrounding of a DECLARED
        # worker (registry key worker_launch_commands), foreground / wrapped / pipeline / subshell, and inside
        # a literal shell -c; a bare-& inside $( ) command substitution is CAPTURED (parent waits) and ALLOWS.
        d = Fixture(tmp, "detach")
        _dreg = d.root / ".aiqt" / "orchestration.local.json"

        def _set_workers(val, present=True):
            reg = json.loads(_dreg.read_text(encoding="utf-8"))
            reg.pop("worker_launch_commands", None)
            if present:
                reg["worker_launch_commands"] = val
            _dreg.write_text(json.dumps(reg), encoding="utf-8")

        _set_workers(["orch-verify"])
        det = lambda cmd, rib=False: aiqt_hooks.orch_detached_dispatch_guard(
            d.payload("PreToolUse", "Bash", {"command": cmd, "run_in_background": rib}))
        # Lexer-contract pins (the EN-7 dependency): a bare backgrounding operator is sep_after == "&", and
        # &&, &>, >&, 2>&1, and a quoted & are NOT. A later EN-7 refactor that breaks this contract fails HERE
        # rather than silently breaking the guard.
        _seps = lambda cmd: [s for (_a, s) in aiqt_hooks._segments(cmd)]
        check("detach/lex-bare-amp-is-amp", "&" in _seps("orch-verify x &"), True)
        check("detach/lex-logical-and-not-amp", "&" in _seps("orch-verify x && echo ok"), False)
        check("detach/lex-amp-redirect-not-amp", "&" in _seps("orch-verify x &> run.log"), False)
        check("detach/lex-redirect-amp-not-amp", "&" in _seps("orch-verify x >& run.log"), False)
        check("detach/lex-dup-2gt1-not-amp", "&" in _seps("orch-verify x 2>&1 | tee run.log"), False)
        check("detach/lex-quoted-amp-not-amp", "&" in _seps("echo 'launch worker &'"), False)
        # FIRE (deny): the proven bare-& detach of a declared worker. Vectors 1-10 flip to ALLOW if the
        # sep_after == "&" detection is removed, so these tests fail-when-reverted.
        check("detach/fire-fg-trailing", _verdict(det("orch-verify --brief x &")), "deny")
        check("detach/fire-fg-newline", _verdict(det("orch-verify x &\necho next")), "deny")
        check("detach/fire-nohup", _verdict(det("nohup orch-verify x &")), "deny")
        check("detach/fire-setsid", _verdict(det("setsid orch-verify x &")), "deny")
        check("detach/fire-sudo", _verdict(det("sudo orch-verify x &")), "deny")
        check("detach/fire-subshell", _verdict(det("( orch-verify x & )")), "deny")
        check("detach/fire-then-cmd", _verdict(det("orch-verify x & echo done")), "deny")
        check("detach/fire-pipeline", _verdict(det("orch-verify x | tee run.log &")), "deny")
        check("detach/fire-env-timeout", _verdict(det("env FOO=bar timeout 30 orch-verify x &")), "deny")
        check("detach/fire-shell-c-literal", _verdict(det("bash -c 'orch-verify x &'")), "deny")
        # run_in_background is IGNORED for scoping (it tracks the wrapper shell, not a child the shell detaches).
        check("detach/fire-rib-true", _verdict(det("orch-verify x &", rib=True)), "deny")
        # NO-FIRE (allow): a non-detach & shape, a non-worker background, a foreground worker, and (Option A)
        # every $( ) command-substitution form. Vectors 11-15 and 18 flip to DENY if detection widens back to a
        # raw & regex, so these fail-when-reverted too.
        check("detach/allow-logical-and", _verdict(det("orch-verify x && echo ok")), "allow")
        check("detach/allow-amp-redirect", _verdict(det("orch-verify x &> run.log")), "allow")
        check("detach/allow-redirect-amp", _verdict(det("orch-verify x >& run.log")), "allow")
        check("detach/allow-dup-2gt1-pipe", _verdict(det("orch-verify x 2>&1 | tee run.log")), "allow")
        check("detach/allow-quoted-amp", _verdict(det("echo 'launch worker &'")), "allow")
        check("detach/allow-nonworker-bg", _verdict(det("sleep 5 &")), "allow")
        check("detach/allow-foreground-worker", _verdict(det("orch-verify x")), "allow")
        check("detach/allow-cmdsub", _verdict(det("result=$(orch-verify x &)")), "allow")
        check("detach/allow-cmdsub-nested", _verdict(det("a=$(b=$(orch-verify x &))")), "allow")
        # a real subshell is NOT a command substitution and DENIES, pinning the $( ) vs ( ) discrimination.
        check("detach/deny-subshell-not-cmdsub", _verdict(det("( orch-verify x & )")), "deny")
        # backtick command substitution: the launcher glues into a non-command-word token, so it does not fire
        # (a disclosed non-catch, not a scope); pinned so a future lexer change is caught.
        check("detach/allow-backtick", _verdict(det("x=`orch-verify y &`")), "allow")
        # arithmetic $(( a & b )): the & is bitwise-AND inside a substitution scope and the operands are not
        # command words, so it never fires.
        check("detach/allow-arith-amp", _verdict(det("echo $(( 1 & 2 ))")), "allow")
        # a declared worker basename that appears as an ARGUMENT, not the command word, is not a launch.
        check("detach/allow-worker-as-arg", _verdict(det("echo orch-verify &")), "allow")
        # Scope and cannot-evaluate posture. Undeclared / empty -> inert ALLOW; malformed control -> DENY.
        _set_workers([])
        check("detach/inert-empty-list", _verdict(det("orch-verify x &")), "allow")
        _set_workers(None, present=False)
        check("detach/inert-undeclared", _verdict(det("orch-verify x &")), "allow")
        _set_workers("orch-verify")  # a bare string, not a list: malformed control
        check("detach/malformed-nonlist-denies", _verdict(det("orch-verify x &")), "deny")
        _set_workers([""])  # a list with an empty entry: malformed control
        check("detach/malformed-empty-entry-denies", _verdict(det("orch-verify x &")), "deny")
        _set_workers(["orch-verify"])
        # In scope, no readable command string -> fail closed (DENY).
        check("detach/no-command-denies", _verdict(aiqt_hooks.orch_detached_dispatch_guard(
            d.payload("PreToolUse", "Bash", {"run_in_background": False}))), "deny")
        # UNPARSEABLE command: ASK on a worker word co-occurring with an apparent bare-&, else ALLOW.
        check("detach/unparseable-worker-amp-asks", _verdict(det('orch-verify x " &')), "ask")
        check("detach/unparseable-no-worker-allows", _verdict(det('foo x " &')), "allow")
        # A present-but-BAD registry (version != 1) is a cannot-evaluate: fail closed (DENY).
        _badver = json.loads(_dreg.read_text(encoding="utf-8"))
        _badver["version"] = 2
        _dreg.write_text(json.dumps(_badver), encoding="utf-8")
        check("detach/bad-registry-denies", _verdict(det("orch-verify x &")), "deny")
        _restore = json.loads(_dreg.read_text(encoding="utf-8"))
        _restore["version"] = 1
        _restore["worker_launch_commands"] = ["orch-verify"]
        _dreg.write_text(json.dumps(_restore), encoding="utf-8")  # restore a valid version-1 registry
        check("detach/restored-registry-fires", _verdict(det("orch-verify x &")), "deny")
        # a bare-& acquires NO new prompt when there is no orchestration registry at all.
        di = Fixture(tmp, "detach-inert")
        (di.root / ".aiqt" / "orchestration.local.json").unlink()
        check("detach/inert-no-registry", _verdict(aiqt_hooks.orch_detached_dispatch_guard(
            di.payload("PreToolUse", "Bash", {"command": "orch-verify x &", "run_in_background": False}))),
              "allow")
        # structural fail-closed: a mis-wired event hard-blocks; a missing tool_name denies; a non-Bash tool
        # is out of scope (allow).
        check("detach/wrong-event-hardblocks", _verdict(aiqt_hooks.orch_detached_dispatch_guard(
            {"hook_event_name": "Stop", "tool_name": "Bash", "cwd": str(d.root),
             "tool_input": {"command": "orch-verify x &"}})), "block2")
        check("detach/missing-tool-name-denies", _verdict(aiqt_hooks.orch_detached_dispatch_guard(
            {"hook_event_name": "PreToolUse", "cwd": str(d.root),
             "tool_input": {"command": "orch-verify x &"}})), "deny")
        check("detach/non-bash-tool-allows", _verdict(aiqt_hooks.orch_detached_dispatch_guard(
            {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": str(d.root),
             "tool_input": {"file_path": "/x"}})), "allow")

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
        for badver in (True, 1.0, "1", 2):
            base_reg["version"] = badver
            regpath.write_text(json.dumps(base_reg), encoding="utf-8")
            check("vB/registry-version-{!r}".format(badver),
                  aiqt_hooks._orch_registry(str(b.root))[0], "bad")
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
        # CONV2-CX2: wake-hygiene denials are cap-relieved like every other schedule DENY
        wake_ctx = dict(base, kind="schedule_idle", waiting=[("W-1", "tracked-task", "t9")],
                        wake_named=False, basis_unchanged=True)
        v, _r, _d = aiqt_hooks.decide_yield(dict(wake_ctx, schedule_denials=1))
        check("core/wake-hygiene-below-cap-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(dict(wake_ctx, schedule_denials=3))
        check("core/wake-hygiene-at-cap-findings", v, "ALLOW_WITH_FINDINGS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print("SELF-TEST FAIL:")
        for f_ in FAILURES:
            print("  - " + f_)
        return 1
    print("SELF-TEST PASS: the stop guard denies enumerated actionable work and unproven or stale "
          "blockers, allows proven blockers, live tracked tasks, proposed-only backlogs, absent "
          "registry/lease scope, the operator escape, and fails OPEN with findings on every "
          "cannot-evaluate; the schedule path denies on cannot-evaluate with a three-denial cap and "
          "wake hygiene, and the measured quiet figure beats a claimed one; the unattended-ask "
          "blocker reproduces the host hook's regression vectors with an idempotent redacted pending "
          "row; the truncation guard allows a plain metacharacter-free background command and asks on "
          "any shell syntax or reserved word, asks on a foreground bare-& detach while dropping a "
          "word-start `#` comment and failing an unbalanced/ANSI-C quote toward ASK rather than a silent "
          "allow; the ledger records launches and completions; the resume "
          "audit arms and clears the mutation barrier on real record state; and the prompt stamp "
          "resets guard counters from genuine human input")
    return 0


if __name__ == "__main__":
    sys.exit(main())
