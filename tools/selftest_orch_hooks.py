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
        check("trunc/tail-denies", _verdict(bg("python3 build.py | tail -5")), "deny")
        check("trunc/head-denies", _verdict(bg("python3 build.py | head -20")), "deny")
        check("trunc/sed-n-denies", _verdict(bg("python3 build.py | sed -n 1,5p")), "deny")
        check("trunc/grep-m-denies", _verdict(bg("python3 build.py | grep -m 3 fail")), "deny")
        # A tee is a FULL capture only as the terminal stage or with its stdout diverted off the
        # pipe; a tee feeding a further stage that may close early cannot be proven full -> ASK
        # (round 31). tee|head SIGPIPEs tee (partial file); an unrecognized tee flag writes nothing.
        check("trunc/tee-then-tail-asks",
              _verdict(bg("python3 build.py | tee full.out | tail -5")), "ask")
        check("trunc/tee-then-head-asks",
              _verdict(bg("python3 build.py | tee full.out | head -1")), "ask")
        check("trunc/tee-terminal-allows",
              _verdict(bg("python3 build.py | tee full.out")), "allow")
        # round 32: TERMINAL-ONLY. A mid-chain tee with a stdout redirect still ASKs - a devproc target
        # like /dev/stdout or >&1 re-opens fd1 to the SAME pipe, not off it; only a TERMINAL tee credits
        # a capture. An unvouched tee flag (invalid -u, or --append=x that errors) and a mid-word "#"
        # (lexed literally now, so the truncator is visible) are all handled.
        check("trunc/tee-stdout-devnull-mid-asks",
              _verdict(bg("python3 build.py | tee full.out >/dev/null | tail -5")), "ask")
        check("trunc/tee-stdout-devstdout-mid-asks",
              _verdict(bg("python3 build.py | tee full.out >/dev/stdout | head -1")), "ask")
        check("trunc/tee-flag-u-asks",
              _verdict(bg("python3 build.py | tee -u full.out")), "ask")
        check("trunc/tee-append-eqvalue-asks",
              _verdict(bg("python3 build.py | tee --append=x full.out")), "ask")
        check("trunc/comment-midword-hash-denies",
              _verdict(bg("./build.sh --rev=abc#1 | head -5")), "deny")
        check("trunc/tee-append-terminal-allows",
              _verdict(bg("python3 build.py | tee -a full.out")), "allow")
        check("trunc/tee-unknown-flag-asks",
              _verdict(bg("python3 build.py | tee --definitely-invalid invalid.out | tail -1")), "ask")
        check("trunc/tail-then-tee-denies",
              _verdict(bg("python3 build.py | tail -5 | tee full.out")), "deny")
        check("trunc/tee-devnull-denies",
              _verdict(bg("python3 build.py | tee /dev/null | tail -5")), "deny")
        check("trunc/foreground-out-of-scope",
              _verdict(bg("python3 build.py | tail -5", rib=False)), "allow")
        check("trunc/plain-bg-allows", _verdict(bg("python3 build.py")), "allow")
        check("trunc/unparseable-with-filter-asks",
              _verdict(bg("python3 build.py | tail -5 'unbalanced")), "ask")
        check("trunc/unparseable-bg-asks",
              _verdict(bg("python3 build.py 'unbalanced")), "ask")
        # an unquoted trailing & is in scope even without run_in_background
        check("trunc/amp-bg-denies",
              _verdict(bg("python3 build.py | tail -5 &", rib=False)), "deny")

        # capture-proof redesign: a redirect AFTER a transform captures only the reduced output;
        # unknown transforms need no vocabulary; identity stages and producer real redirects pass; an
        # opaque or orphaned stream is unprovable (ask); a devproc redirect drops the stream.
        check("trunc/redirect-after-transform-denies",
              _verdict(bg("python3 build.py | tail -5 > out.txt")), "deny")
        check("trunc/awk-denies", _verdict(bg("python3 build.py | awk 'NR<=10'")), "deny")
        check("trunc/cut-denies", _verdict(bg("python3 build.py | cut -c1-80")), "deny")
        check("trunc/producer-devnull-denies", _verdict(bg("python3 build.py > /dev/null")), "deny")
        # round 5 (CL1): a GROUPED backgrounded command cannot be parsed into reliable chains, so it is
        # an ASK (not the old deny/allow); the fused ')&' form, where the '&' hid from the operator split,
        # is the blocker case that previously slipped to ALLOW.
        check("trunc/subshell-amp-asks",
              _verdict(bg("( python3 build.py | tail -5 ) &", rib=False)), "ask")
        check("trunc/subshell-fused-amp-asks",
              _verdict(bg("( python3 build.py | tail -5 )&", rib=False)), "ask")
        check("trunc/grouped-foreground-out-of-scope",
              _verdict(bg("( python3 build.py | tail -5 )", rib=False)), "allow")
        check("trunc/list-amp-denies",
              _verdict(bg("python3 build.py && true | tail -3 &", rib=False)), "deny")
        # round 5 (CL2): a FOREGROUND list after a backgrounded '&' is out of scope; only the backgrounded
        # in-scope chain (here a producer redirected to a real file) is proven, so this ALLOWS.
        check("trunc/foreground-list-after-amp-out-of-scope",
              _verdict(bg("python3 build.py > out.txt & python3 report.py | tail -5", rib=False)), "allow")
        check("trunc/opaque-redirect-asks", _verdict(bg('python3 build.py > "$OUT"')), "ask")
        check("trunc/orphan-amp-asks", _verdict(bg("python3 build.py &", rib=False)), "ask")
        check("trunc/producer-redirect-allows", _verdict(bg("python3 build.py > out.txt")), "allow")
        check("trunc/cat-identity-allows", _verdict(bg("python3 build.py | cat")), "allow")
        check("trunc/cat-redirect-allows", _verdict(bg("python3 build.py | cat > out.txt")), "allow")
        check("trunc/producer-redirect-stderr-allows",
              _verdict(bg("python3 build.py > out.log 2>&1 &", rib=False)), "allow")

        # converged-QA round 1: nested shell -c, stdin-redirect pipe-break, process-sub tee targets
        check("trunc/shell-c-pipeline-denies",
              _verdict(bg("bash -c 'python3 build.py | tail -5'")), "deny")
        check("trunc/shell-c-plain-allows", _verdict(bg("sh -c 'python3 build.py'")), "allow")
        check("trunc/shell-c-tee-asks",
              _verdict(bg("bash -c 'python3 build.py | tee real.out | tail -5'")), "ask")
        check("trunc/shell-c-nested-denies",
              _verdict(bg("bash -c 'bash -c \"python3 build.py | tail\"'")), "deny")
        check("trunc/stdin-redirect-tee-denies",
              _verdict(bg("python3 build.py | tee capture.txt < replacement.txt | tail -5")), "deny")
        check("trunc/procsub-tee-asks",
              _verdict(bg("python3 build.py | tee >/dev/null >(head)")), "ask")
        # round 20: a bare ')' close of a proc-sub severs the chain in the flat parse, so the guard can no
        # longer confidently DENY here; it admits the parse limit -> grouping-uncertainty ASK (safe).
        check("trunc/procsub-tee-close-asks",
              _verdict(bg("python3 build.py | tee >(head) >/dev/null")), "ask")
        # converged-QA round 2: a bare fd digit before a redirect is not a phantom tee capture target
        check("trunc/tee-stderr-redirect-denies",
              _verdict(bg("python3 build.py | tee /dev/null 2>/dev/null | head -5")), "deny")
        check("trunc/cat-stderr-redirect-identity-allows",
              _verdict(bg("python3 build.py | cat 2>/dev/null | tee real.out")), "allow")
        # converged-QA round 3: a shell -c wrapper's OWN outer redirect governs the terminal sink,
        # and a cat with a transforming flag (cat -s) is not an identity passthrough.
        check("trunc/shell-c-outer-devnull-denies",
              _verdict(bg("bash -c 'python3 build.py' > /dev/null")), "deny")
        check("trunc/shell-c-outer-discardall-denies",
              _verdict(bg("bash -c 'python3 build.py' >/dev/null 2>&1")), "deny")
        check("trunc/shell-c-outer-real-allows",
              _verdict(bg("bash -c 'python3 build.py' > real.out")), "allow")
        check("trunc/shell-c-inner-tee-outer-devnull-allows",
              _verdict(bg("bash -c 'python3 build.py | tee real.out' > /dev/null")), "allow")
        check("trunc/cat-squeeze-flag-denies",
              _verdict(bg("python3 build.py | cat -s > out.txt")), "deny")
        check("trunc/cat-dash-identity-allows",
              _verdict(bg("python3 build.py | cat - > out.txt")), "allow")
        # round 5 (CX1): a tee/cat carrying an info flag (--help/-h/--version) copies nothing, so it is
        # neither identity nor capture; the producer output is dropped -> deny.
        check("trunc/tee-help-not-capture-denies",
              _verdict(bg("python3 build.py | tee --help full.out")), "deny")
        check("trunc/tee-version-not-capture-denies",
              _verdict(bg("python3 build.py | tee --version real.out | tail -5")), "deny")
        check("trunc/cat-help-not-identity-denies",
              _verdict(bg("python3 build.py | cat --help | tail -5")), "deny")
        # round 5 (G1 defensive): a redirect target token that itself carries a redirect/pipe metachar is
        # opaque, not a proven real file -> unprovable (ask).
        check("trunc/redirect-target-metachar-asks",
              _verdict(bg('python3 build.py > "o>ut"')), "ask")
        # round 7 (CONV6): brace groups + coproc -> ASK; spaced proc-sub '&' lands on an empty chain that
        # now fail-closes to ASK (was a false ALLOW); tee flags are '--'/abbreviation-aware.
        check("trunc/brace-group-redir-amp-asks",
              _verdict(bg("{ python3 build.py | head; } > o.txt &", rib=False)), "ask")
        check("trunc/brace-group-tee-amp-asks",
              _verdict(bg("{ python3 build.py | tail -5; } | tee r.out &", rib=False)), "ask")
        check("trunc/coproc-asks",
              _verdict(bg("coproc { python3 build.py > /dev/null; }", rib=False)), "ask")
        check("trunc/procsub-spaced-amp-asks",
              _verdict(bg("python3 build.py > >(head) &", rib=False)), "ask")
        # round 20: a backgrounded command-substitution's ')' close severs the flat parse (the same
        # mis-parse behind the round-19 false-ALLOW), so even this genuine capture now ASKs - the
        # disclosed safe-direction over-ASK on a backgrounded '$()'-with-redirect shape.
        check("trunc/cmdsub-bg-redirect-asks",
              _verdict(bg("echo $(printf x) > full.out &", rib=False)), "ask")
        check("trunc/tee-abbrev-version-denies", _verdict(bg("printf X | tee --ver")), "deny")
        check("trunc/tee-ddash-filename-allows",
              _verdict(bg("python3 build.py | tee -- --help")), "allow")
        # round 9 (Option 1, fail-closed allowlist): a known-shell wrapper not cleanly recursed via an
        # exact '-c' (clustered -lc/-ec/-euc) -> ASK; exact '-c' still ALLOWs/DENYs its inner correctly.
        check("trunc/shell-c-clustered-lc-asks",
              _verdict(bg("bash -lc 'python3 build.py | tail -5'")), "ask")
        check("trunc/shell-c-clustered-euc-asks",
              _verdict(bg("sh -euc 'python3 build.py | tail -5'")), "ask")
        check("trunc/shell-c-exact-tee-asks",
              _verdict(bg("bash -c 'python3 build.py | tee real.out | tail -5'")), "ask")
        check("trunc/shell-c-exact-tail-denies",
              _verdict(bg("bash -c 'python3 build.py | tail -5'")), "deny")
        # inner inline-& orphans the stream (wrapper exits first) -> ASK.
        check("trunc/shell-c-inner-amp-asks", _verdict(bg("bash -c 'make build &'")), "ask")
        # a compound command (while/for/if) backgrounded by inline & -> ASK (was mis-scoped to allow); a
        # compound keyword as an ARGUMENT must NOT over-fire (grep's operand 'for' stays a plain capture).
        check("trunc/compound-while-amp-asks",
              _verdict(bg("while read l; do python3 build.py | tail -5; done > out.txt &", rib=False)), "ask")
        check("trunc/compound-for-amp-asks",
              _verdict(bg("for f in a b; do python3 build.py | tail; done > o.txt &", rib=False)), "ask")
        check("trunc/compound-word-as-arg-no-overfire",
              _verdict(bg("grep -r for . | tee out.txt &", rib=False)), "allow")
        # round 11 (Option 1 completion): a shell hidden behind a PREFIX wrapper (env/nohup/timeout) is
        # still recognized -> ASK; a prefix wrapping a NON-shell clean capture resolves (flag-only ALLOW,
        # value-prefix ASK).
        check("trunc/prefix-env-shell-asks",
              _verdict(bg("env bash -c 'python3 build.py | head -5'")), "ask")
        check("trunc/prefix-nohup-shell-asks",
              _verdict(bg("nohup bash -c 'python3 build.py | head'")), "ask")
        check("trunc/prefix-env-producer-allows",
              _verdict(bg("env python3 build.py > out.txt")), "allow")
        # round 13: a prefix wrapping a NON-shell producer with a real redirect is a clean capture (no shell
        # token) -> ALLOW; the round-11 value-prefix over-ASK is gone.
        check("trunc/prefix-timeout-producer-allows",
              _verdict(bg("timeout 60 python3 build.py > out.txt")), "allow")
        check("trunc/prefix-sudo-producer-allows",
              _verdict(bg("sudo python3 build.py > out.txt")), "allow")
        # inner coproc/compound inside a cleanly-recursed shell -c -> ASK (F2).
        check("trunc/shell-c-inner-coproc-asks",
              _verdict(bg("bash -c 'coproc python3 build.py'")), "ask")
        check("trunc/shell-c-inner-compound-asks",
              _verdict(bg("bash -c 'while true; do python3 build.py | head; done'")), "ask")
        # a POSITIONAL -c (argv to a script, not executed) no longer over-DENYs or corner-false-ALLOWs (F3).
        check("trunc/shell-positional-c-asks",
              _verdict(bg("bash myscript.sh -c 'x | head'")), "ask")
        check("trunc/shell-positional-c-devnull-asks",
              _verdict(bg("bash myscript.sh -c 'x | tee /tmp/f' > /dev/null")), "ask")
        # round 13 (any-shell-token): ANY wrapper hiding a shell not in the exact `-c <body>` form -> ASK.
        check("trunc/wrap-sudo-shell-asks",
              _verdict(bg("sudo bash -c 'python3 build.py | head'")), "ask")
        check("trunc/wrap-env-sepflag-shell-asks",
              _verdict(bg("env -u FOO bash -c 'python3 build.py | head'")), "ask")
        check("trunc/wrap-stdbuf-sepflag-shell-asks",
              _verdict(bg("stdbuf -o L bash -c 'python3 build.py | head'")), "ask")
        check("trunc/shell-c-option-after-c-asks",
              _verdict(bg("bash -c -- 'python3 build.py | head'")), "ask")
        check("trunc/shell-c-eopt-after-c-asks",
              _verdict(bg("bash -c -e 'python3 build.py | head'")), "ask")
        check("trunc/leading-redirect-shell-asks",
              _verdict(bg("> out.txt bash -c 'python3 build.py | head' &", rib=False)), "ask")
        check("trunc/cat-u-identity-allows",
              _verdict(bg("python3 build.py | cat -u > out.txt")), "allow")
        # round 14: `eval <string>` executes an opaque command string like a shell -> ASK (closes the
        # disclosed eval-command residual); a command substitution computing an ARGUMENT inside a recursed
        # -c body is command-internal, not a deliverable truncation, and a real drop there still DENIES.
        check("trunc/eval-string-asks",
              _verdict(bg("eval 'echo foo | head' > out.txt &", rib=False)), "ask")
        check("trunc/shell-c-eval-inner-asks",
              _verdict(bg("bash -c 'eval \"echo foo | head\"'")), "ask")
        check("trunc/shell-c-cmdsub-arg-denies",
              _verdict(bg("bash -c 'python3 build.py | head > out.txt'")), "deny")
        # round 16: model '>|' (noclobber-override) and '&>>' (append-both); fail-close other '>'-ops.
        check("trunc/redir-noclobber-devnull-denies",
              _verdict(bg("python3 build.py >| /dev/null")), "deny")
        check("trunc/redir-appendboth-devnull-denies",
              _verdict(bg("python3 build.py &>> /dev/null")), "deny")
        check("trunc/redir-noclobber-realfile-allows",
              _verdict(bg("python3 build.py >| out.txt")), "allow")
        check("trunc/redir-stdout-to-stderr-denies", _verdict(bg("python3 build.py >&2")), "deny")
        check("trunc/redir-stderr-dup-realfile-allows",
              _verdict(bg("python3 build.py > out.log 2>&1")), "allow")  # 2>&1 must not over-fire
        # round 16: more known shells recurse like bash; an INPUT proc-sub '<(producer)' -> ASK.
        check("trunc/ksh-c-tail-denies",
              _verdict(bg("ksh -c 'python3 build.py | tail' > out.txt")), "deny")
        check("trunc/fish-c-tail-denies", _verdict(bg("fish -c 'python3 build.py | tail'")), "deny")
        check("trunc/input-procsub-asks",
              _verdict(bg("tail -5 <(python3 build.py) > out.txt")), "ask")
        # round 18: an explicit-fd1 input-side redirect ('1<>', '1<&') diverts stdout -> ASK; a bare
        # '<'-op only touches stdin, so the producer's stdout is still captured -> ALLOW.
        check("trunc/fd1-input-redirect-asks", _verdict(bg("python3 build.py 1<> /dev/null")), "ask")
        check("trunc/fd1-dup-close-asks", _verdict(bg("python3 build.py 1<&-")), "ask")
        check("trunc/bare-stdin-redirect-allows", _verdict(bg("python3 build.py < in.txt > out.txt")), "allow")
        # round 20: a bare ')' SEPARATOR (a substitution/subshell close the flat parse cannot model)
        # severs the truncating chain, leaving an orphan redirect fragment that must not read as a
        # clean capture -> grouping-uncertainty ASK. Both an input proc-sub '<(producer)' feeding a
        # head, and a no-op '$(true)' before the redirect, previously flipped DENY->ALLOW.
        check("trunc/procsub-close-sever-asks",
              _verdict(bg("head -1 <(seq 1 100) > out.log &", rib=False)), "ask")
        check("trunc/cmdsub-close-sever-asks",
              _verdict(bg("seq 1 100 | head -1 $(true) > out.log &", rib=False)), "ask")
        check("trunc/procsub-close-foreground-out-of-scope",
              _verdict(bg("head -1 <(seq 1 100) > out.log", rib=False)), "allow")
        # round 20: '<>' (read-write) and '<&' (input dup) on stdin break a pipeline stage's input, so
        # a stage carrying one is not an intact identity passthrough -> the reduced stream DENIES.
        check("trunc/stdin-rw-breaks-pipe-denies",
              _verdict(bg("python3 build.py | tee <> /dev/null > real.out &", rib=False)), "deny")
        check("trunc/stdin-dup-breaks-pipe-denies",
              _verdict(bg("python3 build.py | tee <& 3 > real.out &", rib=False)), "deny")
        # regression: a foreground wrapper WITHOUT an inner '&', and a literal '&' in a NON-shell
        # argument, both stay correctly OUT OF SCOPE (foreground filtering is out of scope by design).
        check("trunc/fg-shellc-no-amp-out-of-scope",
              _verdict(bg("bash -c 'python3 build.py | tail'", rib=False)), "allow")
        check("trunc/fg-literal-amp-nonshell-out-of-scope",
              _verdict(bg("grep 'foo&' file", rib=False)), "allow")
        # round 29/30 (claude Finding 1): coproc after a pipeline prefix (time/!) is still a coproc bash
        # recognizes, in BOTH rib modes - a plain command-word test missed it.
        check("trunc/time-coproc-asks", _verdict(bg("time coproc producer", rib=True)), "ask")
        check("trunc/bang-coproc-fg-asks", _verdict(bg("! coproc producer", rib=False)), "ask")
        # round 29/30 (claude Finding 2): a shlex-unparseable command carrying coproc and no '&' now ASKs.
        check("trunc/unparseable-coproc-asks",
              _verdict(bg("coproc grep 'unbalanced", rib=False)), "ask")

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
          "row; the truncation guard denies an uncaptured truncated background dispatch and honours "
          "the tee-before-truncator escape; the ledger records launches and completions; the resume "
          "audit arms and clears the mutation barrier on real record state; and the prompt stamp "
          "resets guard counters from genuine human input")
    return 0


if __name__ == "__main__":
    sys.exit(main())
