#!/usr/bin/env python3
"""Behavioural self-test for the GD-112 orchestrator-integrity handlers in
.aiqt/core/hooks/scripts/aiqt_hooks.py (the section-e acceptance vectors; authored BEFORE the core,
test-first).

NO-ASK READING KEY (disclose-accuracy): the enforcement hooks NO LONGER emit permissionDecision "ask"
(the _ask constructor is removed and selftest_aiqt_hooks.py's global invariant asserts it). The
orch_truncation_guard cases that historically ASKED are now: a background dispatch carrying shell syntax
or a reserved word -> ALLOW-WITH-NOTE (the _verdict reducer labels this "warn": a systemMessage with no
permissionDecision), because denying legitimate background fan-out that redirects/pipes its own output
would stall an unattended run; a foreground bare-& detach -> DENY-and-educate (use the tracked background
dispatch, or run it foreground and wait), including the wait-loop degrade path where the classifier is
'indeterminate' and the truncation guard decides. The value "ask" survives only in the reducer's
vocabulary and never as a handler outcome; where prose or a case label still says "ASK"/"asks", read it
as that resolution (a "-allows-note" case reduces to "warn", a "-denies" case to "deny"). The separate
unattended-ask blocker (orch_ask_guard) is unrelated: it DENIES a manufactured ask in unattended mode,
and its "ask/*" case names refer to that blocked ask, not to a guard asking.

Filesystem-write hermetic in its fixtures: every case runs against throwaway fixtures
under a per-case temp dir (its own git repo, its own registry, its own enumerator stub, its own
state dir), removed in a finally, and the fixtures write nowhere else. A DIRECT invocation is NOT
read-hermetic: the fixtures' git calls still read the ambient per-user git configuration (the
HOME/XDG surfaces), and the run's own interpreter honours the ambient PYTHON* environment; running
the suite through tools/check_selftest_execution.py neutralizes both (HOME and XDG_CONFIG_HOME
pinned, GIT_* and PYTHON* dropped, the child launched -I -B), and the runner's own direct-invocation
git-config hermeticity is tracked separately (F-249). Verdicts are judged on the STRUCTURED result
each handler returns (the (code, stdout_obj, stderr) tuple), never by grepping diagnostic prose.

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
from _git_fixture_env import scrub_git_environment  # noqa: E402

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
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
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
    """Reduce a handler result tuple to one of: allow, warn, ask, deny, block2, explicit-allow, matching
    selftest_aiqt_hooks._reduce_result. "warn" is ONLY exit 0 with a stdout object whose keys are exactly
    systemMessage, holding non-whitespace text (the allow-with-note shape). An explicit permissionDecision
    "allow", with or without a note, is "explicit-allow", which no check expects (the hooks' _allow never
    emits one). Any other shape is an "unexpected" string."""
    code, obj, _err = result
    if code == 2:
        return "block2"
    if code == 0 and obj is None:
        return "allow"
    if code == 0 and isinstance(obj, dict):
        specific = obj.get("hookSpecificOutput")
        if isinstance(specific, dict):
            decision = specific.get("permissionDecision")
            if decision == "allow":
                return "explicit-allow"
            if decision in ("ask", "deny"):
                return decision
        elif set(obj) == {"systemMessage"}:
            note = obj["systemMessage"]
            if isinstance(note, str) and note.strip():
                return "warn"
    return "unexpected({!r})".format(result)


def _registry_ceiling(root, probe):
    """Wrap a registry probe of the truncation guard's ancestor walk (aiqt_hooks._orch_dirfd_has_registry)
    so that every directory STRICTLY ABOVE root reads as carrying no registry while every other directory
    is judged by probe. TEST-ONLY HERMETICITY: the production walk takes no ceiling and is not changed; the
    walk still climbs to the filesystem root (so a walk failure above root still surfaces), but a registry
    on the host above the fixture root (for example a live orchestration registry above TMPDIR) can no
    longer decide a truncation-guard row; only the fixtures under root can. The chain above root is read
    from root's resolved path and matched by device and inode, so a symlinked TMPDIR is followed to the
    physical chain the walk itself climbs. The returned probe exposes that set as .above."""
    above = set()
    path = os.path.realpath(str(root))
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            break
        st = os.stat(parent)
        above.add((st.st_dev, st.st_ino))
        path = parent

    def _probe(dirfd):
        st = os.fstat(dirfd)
        if (st.st_dev, st.st_ino) in above:
            return False
        return probe(dirfd)
    _probe.above = frozenset(above)
    return _probe


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
            # -I -B on every interpreter grandchild: a direct developer run must not honour an
            # ambient PYTHONPATH/sitecustomize (the gate launch additionally drops PYTHON* from
            # the child environment; this is the defence-in-depth layer for a bare invocation).
            "enumerator": {"argv": [sys.executable, "-I", "-B", str(stub),
                                    str(self.enum_payload), str(self.enum_exit)], "timeout": 30},
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
    from _git_fixture_env import fixture_git_lifecycle, scrub_git_environment
    scrub_git_environment()
    with fixture_git_lifecycle():
        return _main_isolated(report_path)


def _main_isolated(report_path=None):
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-orch-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temp dir: {}".format(exc), file=sys.stderr)
        return 2
    os.environ["XDG_STATE_HOME"] = str(tmp / "xdg")  # hermetic default state root
    # Hermetic git fixtures on a DIRECT run (test-hermeticity): the selftest-execution gate
    # launches this runner git-neutral, but a direct run inherits the caller's environment,
    # where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook children) would
    # redirect every fixture git init/add/commit below into the CALLER's repository.
    scrub_git_environment()
    # Every truncation-guard verdict is decided by fixtures under tmp alone: the registry walk runs behind
    # the test-only ceiling above (production takes none), restored in the finally below.
    saved_probe = aiqt_hooks._orch_dirfd_has_registry
    try:
        aiqt_hooks._orch_dirfd_has_registry = _registry_ceiling(tmp, saved_probe)
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
                # A value whose mode word is NOT a leading prefix (`fully-attended` begins `fully`) is
                # unrecognized under the word-anchored grammar and fails CLOSED to armed, never a substring allow:
                ("ask/line-mode-fully-attended-suffix-denies-armed",
                 "Operating-mode: fully-attended\n", "deny"),
                # JSON marker shape (the peer secureconfig writer): recognized in both directions.
                ("ask/json-mode-attended-allows",
                 '{"mode": "attended"}\n', "allow"),
                ("ask/json-mode-unattended-denies",
                 '{"mode": "unattended"}\n', "deny"),
                ("ask/json-mode-whitespace-padded-unattended-denies",
                 '  \n{"mode": "unattended"}\n  ', "deny"),
                # A leading byte-order mark before the JSON object is stripped, so the object is still read:
                ("ask/json-mode-bom-unattended-denies",
                 '\ufeff{"mode": "unattended"}\n', "deny"),   # BOM + unattended object -> armed/deny
                ("ask/json-mode-bom-attended-allows",
                 '\ufeff{"mode": "attended"}\n', "allow"),    # BOM + attended object -> attended/allow
                # FAIL CLOSED to guards-armed on a present-but-unusable marker (never a silent disarm):
                ("ask/json-mode-malformed-denies-armed",
                 '{"mode": "unattended"\n', "deny"),           # partial/malformed JSON
                ("ask/json-mode-no-string-mode-denies-armed",
                 '{"mode": 1}\n', "deny"),                     # JSON object, mode not a string
                ("ask/json-mode-empty-object-denies-armed",
                 '{}\n', "deny"),                              # JSON object without a mode key
                ("ask/json-mode-array-denies-armed",
                 '[{"mode": "unattended"}]\n', "deny"),        # JSON array (not an object) -> armed
                ("ask/json-mode-unrecognized-value-denies-armed",
                 '{"mode": "bananas"}\n', "deny"),            # JSON mode outside the attended family
                ("ask/line-mode-unrecognized-value-denies-armed",
                 "Operating-mode: bananas\n", "deny"),        # line value outside the attended family
                # A JSON SCALAR is a present marker attempt, not an object with a string mode -> armed/deny.
                ("ask/json-scalar-bool-denies-armed",
                 "true\n", "deny"),                           # JSON boolean scalar
                ("ask/json-scalar-number-denies-armed",
                 "42\n", "deny"),                             # JSON number scalar
                ("ask/json-scalar-null-denies-armed",
                 "null\n", "deny"),                           # JSON null scalar
                ("ask/json-scalar-string-denies-armed",
                 '"unattended"\n', "deny"),                   # terminated JSON string scalar (not an object)
                # An UNTERMINATED JSON string is a malformed string-shaped marker attempt -> armed/deny.
                ("ask/json-unterminated-string-denies-armed",
                 '"unattended\n', "deny"),
                # A present `Operating-mode:` declaration with an EMPTY value fails CLOSED (not the no-marker None):
                ("ask/empty-value-declaration-denies-armed",
                 "Operating-mode:\n", "deny"),
                # Ordinary prose with NO declaration and NO JSON stays the fail-open no-marker default (no over-fire):
                ("ask/prose-no-declaration-allows",
                 "This is a shared session-state note with no mode declaration.\n", "allow"),
                # A shared text file with real prose PLUS a valid Operating-mode line: the line is still found:
                ("ask/prose-with-valid-line-denies",
                 "Session state notes.\nSome context here.\nOperating-mode: unattended\nMore notes.\n", "deny"),
                # ---------- round-4 sound-parser matrix (single reader, no regex+substring leaks) ----------
                # Compound annotations begin with the mode word, so they classify (the real ipad compound):
                ("ask/line-mode-attended-compound-allows",
                 "Operating-mode: attended (iPad); continuous mode\n", "allow"),
                ("ask/line-mode-unattended-compound-denies",
                 "Operating-mode: unattended; continuous\n", "deny"),
                # SUBSTRING must NOT match: a value whose `attended` is not a leading prefix fails CLOSED to armed:
                ("ask/line-mode-disattended-denies-armed",
                 "Operating-mode: disattended\n", "deny"),
                ("ask/line-mode-not-attended-denies-armed",
                 "Operating-mode: not-attended\n", "deny"),
                ("ask/json-mode-disattended-denies-armed",
                 '{"mode": "disattended"}\n', "deny"),
                ("ask/json-mode-not-attended-denies-armed",
                 '{"mode": "not-attended"}\n', "deny"),
                # The declaration value is the rest of ITS physical line only: an empty value never crosses the
                # newline to capture the NEXT line (the LF and the CRLF fail-open legs the old `\s*` regex leaked):
                ("ask/line-mode-empty-value-next-line-not-captured-denies-armed",
                 "Operating-mode:\nattended\n", "deny"),
                ("ask/line-mode-crlf-empty-value-not-captured-denies-armed",
                 "Operating-mode:\r\nattended\n", "deny"),
                # A BOM-prefixed declaration line is stripped once and classified, never missed:
                ("ask/line-mode-bom-unattended-denies",
                 "\ufeffOperating-mode: unattended\n", "deny"),
                # An indented declaration is handled consistently (no over-arm): indented attended -> allow:
                ("ask/line-mode-indented-attended-allows",
                 "  Operating-mode: attended\n", "allow"),
                # JSON strict-exact: duplicate keys, extra keys, and trailing garbage after the object fail closed:
                ("ask/json-mode-duplicate-keys-denies-armed",
                 '{"mode": "unattended", "mode": "attended"}\n', "deny"),
                ("ask/json-mode-extra-keys-denies-armed",
                 '{"mode": "attended", "x": 1}\n', "deny"),
                ("ask/json-object-trailing-garbage-denies-armed",
                 '{"mode": "attended"} trailing\n', "deny"),
                # Prose merely MENTIONING a mode word, with no declaration line and no JSON, stays fail-open:
                ("ask/prose-mentions-mode-allows",
                 "Yesterday we worked unattended; today attended.\n", "allow"),
                # A whitespace-only file carries no marker (fail open):
                ("ask/whitespace-only-allows", "   \n\t\n", "allow"),
                ("ask/empty-mode-file-allows", "", "allow")):  # empty mode file (present, no marker) fails open
            h.mode.write_text(mode_text, encoding="utf-8")
            g_ask = h.payload("PreToolUse", "AskUserQuestion",
                              {"questions": [{"question": "pick one"}]},
                              extra={"tool_use_id": "tu-1"})
            check(check_id, _verdict(ask()), want)
        # absent mode record fails open (no marker): the fixture DELETES the file, so this tests ABSENCE
        h.mode.unlink()
        g_ask = h.payload("PreToolUse", "AskUserQuestion", {"questions": []},
                          extra={"tool_use_id": "tu-2"})
        check("ask/absent-mode-fails-open", _verdict(ask()), "allow")
        # a PRESENT but unreadable mode record fails CLOSED to guards-armed (distinct from the absent file
        # above). A directory at the mode path raises an OSError that is NOT FileNotFoundError when opened,
        # a hermetic proxy for an unreadable present file that holds on any uid, including root.
        h.mode.mkdir()
        g_ask = h.payload("PreToolUse", "AskUserQuestion", {"questions": []},
                          extra={"tool_use_id": "tu-2b"})
        check("ask/present-unreadable-mode-denies-armed", _verdict(ask()), "deny")
        h.mode.rmdir()
        # a PRESENT mode record whose bytes are not valid UTF-8 fails CLOSED to guards-armed (strict decode,
        # not errors="replace"): a raw 0xFF start byte cannot decode, so the reader arms rather than disarms.
        h.mode.write_bytes(b'\xff\xfe{"mode": "attended"}')
        g_ask = h.payload("PreToolUse", "AskUserQuestion", {"questions": []},
                          extra={"tool_use_id": "tu-2c"})
        check("ask/invalid-utf8-mode-denies-armed", _verdict(ask()), "deny")
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
        # AIRTIGHT-NARROW truncation guard (round 32): ALLOW (silent) only a plain metacharacter-free
        # background command; ANY OTHER shell metacharacter -> ALLOW-WITH-NOTE (the reducer labels it "warn":
        # denying a background dispatch that redirects or pipes its own output would block legitimate
        # fan-out, so the guard proceeds and notes the durable-capture guidance instead of prompting;
        # historically this ASKED); foreground out of scope; the no-command case DENIES.
        # ROUND-2 FINDING 9 EXCEPTION: a background dispatch that pipes a PRODUCER into a TRUNCATING SINK
        # (head/tail) discards the producer's full output AND its exit status (the completion signal binds to
        # the truncated view, a failing producer reads clean), so it DENIES-and-educates - a proven false-clean
        # hazard, not a merely-unprovable capture. cat/tee pass output through, so they stay "warn".
        check("trunc/plain-bg-allows", _verdict(bg("python3 build.py")), "allow")
        check("trunc/plain-args-allows", _verdict(bg("pytest -q tests/unit")), "allow")
        check("trunc/plain-flag-eq-allows", _verdict(bg("python3 build.py --out=dist/log")), "allow")
        check("trunc/plain-envprefix-allows", _verdict(bg("PYTHONPATH=src python3 build.py")), "allow")
        check("trunc/pipe-tail-denies", _verdict(bg("python3 build.py | tail -5")), "deny")
        check("trunc/head-denies", _verdict(bg("python3 build.py | head -20")), "deny")
        check("trunc/tee-allows-note", _verdict(bg("python3 build.py | tee full.out")), "warn")
        check("trunc/redirect-allows-note", _verdict(bg("python3 build.py > out.txt")), "warn")
        # a producer piped into head is a truncating sink even with a quoted-redirect earlier stage -> DENY.
        check("trunc/quoted-redirect-head-denies", _verdict(bg("grep '>' index.html | head -5")), "deny")
        check("trunc/quoted-pipe-allows-note", _verdict(bg("grep '|' file")), "warn")
        check("trunc/amp-tail-denies", _verdict(bg("python3 build.py | tail &")), "deny")
        check("trunc/semicolon-allows-note", _verdict(bg("python3 a.py ; python3 b.py")), "warn")
        # the truncating sink is caught through subshell/brace wrappers too (class width).
        check("trunc/subshell-tail-denies", _verdict(bg("( python3 build.py | tail )")), "deny")
        check("trunc/brace-tail-denies", _verdict(bg("{ python3 build.py | tail; }")), "deny")
        # ROUND-7 (codex finding 5): the sink is resolved THROUGH shell command-modifier wrappers
        # (command/env/nice/stdbuf/... with their own option/assignment args), so a truncating sink hidden
        # behind a wrapper is still caught; a genuine NON-truncating wrapped command still allows-with-note.
        # Reverting to the un-wrapped command-word read reds the four deny cases (they become "warn").
        check("trunc/wrap-command-head-denies", _verdict(bg("python3 build.py | command head -5")), "deny")
        check("trunc/wrap-env-head-denies", _verdict(bg("python3 build.py | env head -5")), "deny")
        check("trunc/wrap-env-assign-tail-denies", _verdict(bg("python3 build.py | env FOO=1 tail")), "deny")
        check("trunc/wrap-nice-sep-head-denies", _verdict(bg("python3 build.py | nice -n 0 head")), "deny")
        check("trunc/wrap-stdbuf-attached-head-denies",
              _verdict(bg("python3 build.py | stdbuf -oL head")), "deny")
        check("trunc/wrap-command-cat-allows-note", _verdict(bg("python3 build.py | command cat")), "warn")
        check("trunc/wrap-env-grep-allows-note", _verdict(bg("python3 build.py | env grep x")), "warn")
        check("trunc/dollar-var-allows-note", _verdict(bg("python3 build.py > $OUT")), "warn")
        check("trunc/cmdsub-allows-note", _verdict(bg("python3 build.py > $(date).log")), "warn")
        check("trunc/backtick-allows-note", _verdict(bg("python3 build.py > `date`.log")), "warn")
        check("trunc/coproc-allows-note", _verdict(bg("coproc producer")), "warn")
        check("trunc/reserved-time-allows-note", _verdict(bg("time python3 build.py")), "warn")
        check("trunc/reserved-for-allows-note", _verdict(bg("for x in a b")), "warn")
        check("trunc/glob-allows-note", _verdict(bg("cat *.log")), "warn")
        check("trunc/comment-allows-note", _verdict(bg("python3 build.py # note")), "warn")
        check("trunc/shell-c-allows-note", _verdict(bg("bash -c 'python3 build.py | tail'")), "warn")
        check("trunc/tilde-allows-note", _verdict(bg("cat ~/notes.txt")), "warn")
        check("trunc/foreground-plain-allows", _verdict(bg("python3 build.py", rib=False)), "allow")
        check("trunc/foreground-pipe-allows", _verdict(bg("python3 build.py | tail -5", rib=False)), "allow")
        check("trunc/foreground-tee-allows", _verdict(bg("python3 build.py | tee f", rib=False)), "allow")
        check("trunc/empty-bg-denies", _verdict(bg("")), "deny")
        # L-GS1 / trkasy: foreground bare-& detach coverage. A plain foreground call stays out of scope, but
        # a bare `&` detaches a child into untracked async work -> DENY-and-educate (use the tracked
        # background dispatch, or run it foreground and wait; historically this ASKED). The shell forms that
        # also carry an ampersand but do NOT detach (&&, &>, &>>, <&, >&, |&, and any quoted or escaped &)
        # stay ALLOW; the narrow scanner over-denies on some grammar it cannot model and silently allows
        # other forms (the KNOWN FALSE-ALLOW residual disclosed in the manifest).
        check("trunc/fg-detach-trailing-denies", _verdict(bg("long_job &", rib=False)), "deny")
        check("trunc/fg-detach-between-denies", _verdict(bg("worker & echo done", rib=False)), "deny")
        check("trunc/fg-detach-grouped-denies", _verdict(bg("( long_job & )", rib=False)), "deny")
        check("trunc/fg-detach-newline-denies", _verdict(bg("long_job &\necho next", rib=False)), "deny")
        # a later `wait` does not clear it: the lexical hook cannot prove the correct child is awaited.
        check("trunc/fg-detach-then-wait-denies", _verdict(bg("worker & wait", rib=False)), "deny")
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
        # finding E (a scan that ENDS inside a quote fails toward treating it as a detach - now a DENY, once
        # an ASK; a quote misread in mid-string can still shift it into a disclosed silent allow): a
        # scan that ends still inside a quote (an unbalanced quote, or an ANSI-C $'...' construct this scan
        # does not model) could hide a real trailing `&`, so it reports a detach. Without the fix each of
        # these ended `inside quotes` and returned False, silently allowing the real `&`.
        check("trunc/scan-ansi-c-hidden-detach", aiqt_hooks._orch_foreground_detach(r"echo $'a\'b' & echo x"), True)
        check("trunc/scan-unbalanced-single-asks", aiqt_hooks._orch_foreground_detach("echo 'oops & bg"), True)
        check("trunc/fg-ansi-c-hidden-detach-denies", _verdict(bg(r"echo $'a\'b' & echo x", rib=False)), "deny")
        # finding F (an unquoted word-start `#` comment is dropped, so a commented-out `&` does not prompt):
        # without the fix the `&` in comment text was scanned as an operator and over-fired (historically an
        # over-ASK, now an over-deny).
        check("trunc/scan-comment-amp-not-detach", aiqt_hooks._orch_foreground_detach("echo done # & comment"), False)
        check("trunc/scan-comment-leading-hash-not-detach", aiqt_hooks._orch_foreground_detach("# long_job &"), False)
        # L-GS1 fix-round: a `#` comment runs only to the end of ITS line, never to the end of a multi-line
        # command. A comment on an earlier line must NOT swallow a real bare-& detach on a later line; before
        # the fix the whole scan broke at the first `#`, so these two silently ALLOWED (returned False).
        check("trunc/scan-comment-then-detach-nextline", aiqt_hooks._orch_foreground_detach("echo hi  # note\nsleep 100 &"), True)
        check("trunc/scan-leading-comment-then-detach", aiqt_hooks._orch_foreground_detach("# lead comment\nsleep 100 &"), True)
        check("trunc/fg-comment-then-detach-denies", _verdict(bg("echo hi  # note\nsleep 100 &", rib=False)), "deny")
        check("trunc/fg-comment-amp-allows", _verdict(bg("echo done # & comment", rib=False)), "allow")
        # inert when the orchestration registry is absent: a foreground bare-& acquires no new prompt.
        ti = Fixture(tmp, "trunc-inert")
        (ti.root / ".aiqt" / "orchestration.local.json").unlink()
        check("trunc/fg-detach-inert-no-registry", _verdict(aiqt_hooks.orch_truncation_guard(
            ti.payload("PreToolUse", "Bash",
                       {"command": "long_job &", "run_in_background": False}))), "allow")
        # REGISTRY-REQUIRED MODE (opt-in, AIQT_ORCH_REQUIRE_REGISTRY): an ABSENT registry DENIES instead
        # of leaving the guard inert, with a reason naming the mode and its repair; an explicit off value
        # keeps the default inert allow, and a PRESENT registry behaves identically in both modes.
        _rr = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
        _rr_old = os.environ.get(_rr)
        try:
            os.environ[_rr] = "1"
            rr = aiqt_hooks.orch_truncation_guard(
                ti.payload("PreToolUse", "Bash", {"command": "ls", "run_in_background": False}))
            rr_reason = (rr[1] or {}).get("hookSpecificOutput", {}).get(
                "permissionDecisionReason", "")
            check("trunc/registry-required-absent-denies",
                  (_verdict(rr), _rr in rr_reason, "orchestration" in rr_reason), ("deny", True, True))
            check("trunc/registry-required-present-plain-allows", _verdict(bg("python3 build.py")),
                  "allow")
            check("trunc/registry-required-present-detach-denies",
                  _verdict(bg("long_job &", rib=False)), "deny")
            os.environ[_rr] = "off"
            check("trunc/registry-required-off-value-inert", _verdict(aiqt_hooks.orch_truncation_guard(
                ti.payload("PreToolUse", "Bash",
                           {"command": "long_job &", "run_in_background": False}))), "allow")
            # The off values are matched EXACTLY, nothing stripped (ASCII case-insensitive): at the pre-fix
            # pin the value was stripped first, so a tab, a newline, or an off word wrapped in spaces or
            # no-break spaces read as OFF and the guard allowed without a registry. Each now reads as ON.
            _padded = ("\t", "\n", " ", "\u00a0off\u00a0", " off", "off\n", "\u00a0")
            _padded_on = []
            for _v in _padded:
                os.environ[_rr] = _v
                _padded_on.append(aiqt_hooks._orch_registry_required())
            check("trunc/registry-required-padded-off-reads-on", _padded_on, [True] * len(_padded))
            os.environ[_rr] = "\u00a0off\u00a0"
            check("trunc/registry-required-padded-off-denies", _verdict(aiqt_hooks.orch_truncation_guard(
                ti.payload("PreToolUse", "Bash", {"command": ":", "run_in_background": False}))), "deny")
            _exact_off = []
            for _v in ("", "0", "false", "no", "off", "OFF", "False", "No"):
                os.environ[_rr] = _v
                _exact_off.append(aiqt_hooks._orch_registry_required())
            check("trunc/registry-required-exact-off-values-off", _exact_off, [False] * 8)
        finally:
            if _rr_old is None:
                os.environ.pop(_rr, None)
            else:
                os.environ[_rr] = _rr_old
        # HERE-DOCUMENT BODIES ARE DATA (adopter reproducers H1-H11; the former over-refusal residual is
        # withdrawn): the lines after a <<WORD / <<-WORD operator up to the terminator line, quoted or
        # unquoted delimiter, several here-documents per command, are read as data, so neither quote
        # balancing nor '&' detection sees them. Each H-case below DENIED at the pre-fix pin (an
        # "unbalanced" or "detach" misread of body prose) and must ALLOW now.
        check("trunc/fg-h1-heredoc-apostrophe-commit-allows",
              _verdict(bg("git commit -F - <<'EOF'\nDon't vendor the SDK; pin it.\nEOF", rib=False)),
              "allow")
        check("trunc/fg-h2-heredoc-amp-body-allows",
              _verdict(bg("gh pr create --title t --body-file - <<'EOF'\nRe-pin A & B\nEOF", rib=False)),
              "allow")
        check("trunc/fg-h3-heredoc-apostrophe-file-allows",
              _verdict(bg("cat > /tmp/brief.md <<'EOF'\nthe worker's deliverable\nEOF", rib=False)),
              "allow")
        check("trunc/fg-h4-heredoc-amp-python-allows",
              _verdict(bg("python3 - <<'PY'\nmask = GENERATED & {1,2}\nPY", rib=False)), "allow")
        check("trunc/fg-h5-commit-template-quoted-amp-allows",
              _verdict(bg("git commit -m \"$(cat <<'EOF'\nRe-pin \"A & B\" to main\nEOF\n)\"",
                          rib=False)), "allow")
        check("trunc/fg-h7-unquoted-delim-apostrophe-allows",
              _verdict(bg("cat <<EOF > /tmp/x.txt\nit's $HOME\nEOF", rib=False)), "allow")
        check("trunc/fg-h10-stdin-brief-amp-apostrophe-allows",
              _verdict(bg("orch-send <<BRIEF\nthe operator's plan & the fallback\nBRIEF", rib=False)),
              "allow")
        check("trunc/fg-h11-pr-comment-apostrophe-allows",
              _verdict(bg("gh pr comment 2701 --body-file - <<'EOF'\nWe don't merge unpinned refs.\nEOF",
                          rib=False)), "allow")
        check("trunc/fg-heredoc-amp-body-allows",
              _verdict(bg("cat > f <<'EOF'\nFix A & B\nEOF", rib=False)), "allow")
        check("trunc/fg-heredoc-dash-tabbed-body-allows",
              _verdict(bg("cat <<-EOF\n\tit's & indented\n\tEOF", rib=False)), "allow")
        check("trunc/fg-commit-template-amp-allows",
              _verdict(bg("git commit -m \"$(cat <<'EOF'\nFix A & B\nEOF\n)\"", rib=False)), "allow")
        # ARITHMETIC IS READ AS ON MAIN (QA round 4): an '&' in $((...)) or ((...)) is scanned as a detach,
        # the disclosed over-refusal, and a '<<' inside it is never a here-document (a left shift).
        check("trunc/fg-arith-bitwise-and-overrefusal-denies",
              _verdict(bg("echo $((3 & 1))", rib=False)), "deny")
        check("trunc/fg-arith-left-shift-allows", _verdict(bg("echo $((1<<2))", rib=False)), "allow")
        # QA round 3 reproductions: a $(...) or backtick substitution nested in arithmetic runs (bash 5.3
        # printed each marker on stderr). Each still denies, now as a plain detach.
        check("trunc/fg-heredoc-arith-cmdsub-detach-denies",
              _verdict(bg("cat <<EOF\n$(( $(printf DETACHED >&2 & wait; printf 0) ))\nEOF", rib=False)),
              "deny")
        check("trunc/fg-arith-command-cmdsub-detach-denies",
              _verdict(bg("(( $(printf DETACHED >&2 & wait; printf 1) ))", rib=False)), "deny")
        for name, cmd in (
                ("trunc/scan-arith-backtick-detach-kind", "echo $(( `printf X >&2 & wait; printf 0` ))"),
                ("trunc/scan-dq-arith-cmdsub-detach-kind",
                 "echo \"$(( $(printf X >&2 & wait; printf 0) ))\""),
                ("trunc/scan-nested-arith-cmdsub-detach-kind",
                 "echo $(( 1 + $(( $(printf X >&2 & wait; printf 0) )) ))"),
                ("trunc/scan-arith-subshell-cmdsub-detach-kind",
                 "echo $(( 1 + $((printf X >&2 & wait); printf 0) ))"),
                ("trunc/scan-heredoc-subshell-cmdsub-detach-kind",
                 "cat <<EOF\n$((printf X >&2 & wait); printf 0)\nEOF")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "detach")
        # QA round 4 BLOCKERS: '((' inside parameter-expansion text was read as an arithmetic opener and
        # hid a real detach (ALLOWED at 7a767a4a; bash 5.3 printed DETACHED), and a <<$(x) delimiter was
        # recorded as "$" so the lines through a later "$" line were hidden. Arithmetic skipping is removed
        # and only a SIMPLE delimiter word opens a here-document, so each denies.
        for name, cmd in (
                ("trunc/scan-r4-paramexp-arith-opener-detach-kind",
                 "unset x; echo ${x:-((}; printf DETACHED >&2 & wait # ))"),
                ("trunc/scan-r4-heredoc-paramexp-arith-opener-detach-kind",
                 "unset x; cat <<EOF\n$(echo ${x:-((}; printf DETACHED >&2 & wait # ))\n)\nEOF"),
                ("trunc/scan-r4-cmdsub-delim-detach-kind",
                 "cat <<$(x)\ndata\n$(x)\nprintf DETACHED >&2 & wait\ncat <<'END'\n$\nEND"),
                # A '<<' that bash reads as a left shift, a subscript, or expansion text never opens a
                # here-document once the scan meets a construct whose nesting it does not track, so the
                # lines after it stay code (each was ALLOWED at 7a767a4a or would be with arithmetic
                # skipping removed; bash 5.3 printed DETACHED for each).
                ("trunc/scan-subscript-shift-detach-kind",
                 "a[1<<2 ]=x\nprintf DETACHED >&2 & wait\n2"),
                ("trunc/scan-dollar-bracket-shift-detach-kind",
                 "echo $[1<<2 ]\nprintf DETACHED >&2 & wait\n2"),
                ("trunc/scan-arith-command-shift-detach-kind",
                 "(( x = 1<<2 ))\nprintf DETACHED >&2 & wait\n2"),
                ("trunc/scan-paramexp-heredoc-text-detach-kind",
                 "echo ${x:-<<EOF }\nprintf DETACHED >&2 & wait\nEOF"),
                ("trunc/scan-backtick-heredoc-detach-kind",
                 "echo `cat <<EOF` ; printf DETACHED >&2 & wait\nEOF"),
                ("trunc/scan-dq-nested-quote-heredoc-text-detach-kind",
                 "echo \"${x:-\"<<EOF \"}\"\nprintf DETACHED >&2 & wait\nEOF"),
                # An unquoted body's $(...) whose end the walk cannot read exactly (a ')' inside a
                # parameter expansion, a case pattern) is scanned as code to the end of the body; a detach
                # that scan finds is reported as one (else cannot-evaluate, QA round 7 below).
                ("trunc/scan-heredoc-paramexp-paren-detach-kind",
                 "cat <<EOF\n$(echo ${x:-)}; printf DETACHED >&2 & wait)\nEOF"),
                ("trunc/scan-heredoc-case-paren-detach-kind",
                 "cat <<EOF\n$(case x in x) printf DETACHED >&2 & wait;; esac)\nEOF")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "detach")
        # QA round 4 MEDIUM: a safe <<$(x) here-document is not recognised (its delimiter word is not
        # simple), so its body is scanned as code: a disclosed over-refusal in the deny direction.
        check("trunc/fg-cmdsub-delim-safe-overrefusal-denies",
              _verdict(bg("cat <<$(x)\nA & B\n$(x)", rib=False)), "deny")
        # Simple delimiter words: letters, digits, underscore, optionally wrapped whole in quotes.
        check("trunc/fg-simple-delim-underscore-digit-allows",
              _verdict(bg("cat <<END_1\nx & y\nEND_1", rib=False)), "allow")
        check("trunc/fg-double-quoted-delim-allows",
              _verdict(bg("cat <<\"EOF\"\nit's A & B\nEOF", rib=False)), "allow")
        check("trunc/fg-dq-dirname-nested-quotes-allows",
              _verdict(bg("cd \"$(dirname \"${BASH_SOURCE[0]}\")\"", rib=False)), "allow")
        # CLOSING THE HERE-DOCUMENT QUOTE-SHIFT FALSE-ALLOW (manifest case 5): body quotes no longer flip
        # the scan's quote state, so a real bare '&' AFTER a here-document is seen. The two-heredoc form
        # was a SILENT ALLOW at the pre-fix pin (the body apostrophes rebalanced the scan around the real
        # detach); the trailing form denied there only via the unbalanced misread and must stay denied
        # now, as a seen detach.
        check("trunc/fg-detach-between-heredocs-denies",
              _verdict(bg("cat <<'A'\nuser's\nA\nsleep 5 & cat <<'B'\nuser's\nB", rib=False)), "deny")
        check("trunc/fg-detach-after-heredoc-denies",
              _verdict(bg("cat <<'EOF' > /tmp/x\nthe user's file\nEOF\nsleep 100 &", rib=False)), "deny")
        check("trunc/scan-detach-after-heredoc-kind",
              aiqt_hooks._orch_foreground_detach_kind(
                  "cat <<'EOF' > /tmp/x\nthe user's file\nEOF\nsleep 100 &"), "detach")
        # An unquoted-delimiter body still runs $(...) and backtick substitutions: a real detach inside
        # one stays caught, never silently re-allowed by the body-as-data fix.
        check("trunc/fg-heredoc-cmdsub-detach-denies",
              _verdict(bg("cat <<EOF\n$(job &)\nEOF", rib=False)), "deny")
        check("trunc/fg-heredoc-backtick-detach-denies",
              _verdict(bg("cat <<EOF\n`job &`\nEOF", rib=False)), "deny")
        # An UNTERMINATED here-document fails toward the deny with a reason naming it (bash would still
        # be reading input). At the pre-fix pin this was a SILENT ALLOW when the body shifted no quotes.
        ut = bg("cat <<'EOF'\nno terminator", rib=False)
        ut_reason = (ut[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        check("trunc/fg-unterminated-heredoc-reason",
              (_verdict(ut), "terminator" in ut_reason, "detaches a child" in ut_reason),
              ("deny", True, False))
        # Here-string vs here-document (BLOCKER 1): a run of three or more `<` is a here-string operator
        # (`<<<`), never a here-document, so the word after it stays CODE. At the pre-fix pin the scanner
        # rejected `<<` at the FIRST `<` of `<<<`, then re-recognised `<<` at the second and parsed the
        # here-string word as a bogus here-document delimiter, swallowing the following lines as body: a real
        # detach after `cat <<<:` was HIDDEN (silent allow), and a safe `cat <<<'hello'` read as an
        # unterminated here-document (over-deny). Recognising the whole `<` run first corrects both.
        check("trunc/fg-herestring-detach-denies",
              _verdict(bg("cat <<<:\nprintf 'child\\n' &\nwait\n:", rib=False)), "deny")
        check("trunc/scan-herestring-colon-detach",
              aiqt_hooks._orch_foreground_detach("cat <<<:\nsleep 100 &"), True)
        check("trunc/scan-herestring-colon-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<<:\nsleep 100 &"), "detach")
        check("trunc/fg-herestring-safe-allows", _verdict(bg("cat <<<'hello'", rib=False)), "allow")
        check("trunc/scan-herestring-safe-not-detach",
              aiqt_hooks._orch_foreground_detach("cat <<<'hello'"), False)
        check("trunc/scan-herestring-word-then-detach",
              aiqt_hooks._orch_foreground_detach("cat <<<hello &"), True)
        # Backslash in an UNQUOTED here-document body (QA round 1 BLOCKER 2 and QA round 2 BLOCKERS 1-2):
        # bash removes backslash-newline continuations in such a body, with an outcome that depends on how
        # many backslashes precede the newline, so a continuation can move the terminator line (`EN\<nl>D`
        # ends at `END`, while `\\<nl>EOF` keeps EOF a separate line) or join `$\<nl>(` into a
        # substitution opener. The round-1 join emulation got both wrong at 9c310c2c (an escaped backslash
        # was joined, hiding the real terminator and a later detach; the joined text chose the terminator
        # while the substitution scan read physical text): SILENT ALLOWS. The emulation is withdrawn: an
        # unquoted body holding ANY backslash is not read as data, the rest of the command is scanned as
        # code, and the call DENIES (the disclosed over-refusal UNQUOTED HERE-DOCUMENT BODY WITH A
        # BACKSLASH), so a safe body with a backslash denies too, with a reason naming the backslash. A
        # QUOTED delimiter's body is literal and unchanged.
        def _why(result):
            return (result[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        check("trunc/fg-heredoc-bsnl-join-detach-denies",
              _verdict(bg("cat <<END\nEN\\\nD\nsleep 100 &\nEND", rib=False)), "deny")
        check("trunc/scan-heredoc-bsnl-body-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<END\nEN\\\nD\nsleep 100 &\nEND"), "detach")
        # Allowed at 9c310c2c (joined `EO\<nl>F` read as the terminator); a disclosed deny now.
        bsnl_safe = bg("cat <<EOF\nEO\\\nF", rib=False)
        check("trunc/fg-heredoc-bsnl-safe-backslash-denies",
              (_verdict(bsnl_safe), "holds a backslash" in _why(bsnl_safe)), ("deny", True))
        check("trunc/scan-quoted-delim-no-bsnl-join",
              aiqt_hooks._orch_foreground_detach_kind("cat <<'END'\nEN\\\nD\nsleep 100 &"), "unterminated")
        # QA round 2 BLOCKER 1: an escaped backslash before a newline (two or four backslashes) does not
        # continue the line, so the physical EOF line ends the body and the detach after it runs. ALLOWED
        # at 9c310c2c (scan None); denies now as a seen detach.
        esc_bs = "EOF() { :; }\ncat <<EOF\n\\\\\nEOF\nprintf DETACHED &\nwait\nEOF"
        check("trunc/fg-heredoc-escaped-bs-detach-denies", _verdict(bg(esc_bs, rib=False)), "deny")
        check("trunc/scan-heredoc-escaped-bs-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind(esc_bs), "detach")
        check("trunc/scan-heredoc-four-bs-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind(
                  "EOF() { :; }\ncat <<EOF\n\\\\\\\\\nEOF\nprintf DETACHED &\nwait\nEOF"), "detach")
        # Its safe counterpart: denied at 9c310c2c as "unterminated" (a misread); a disclosed deny now,
        # with the backslash reason.
        esc_safe = bg("cat <<EOF\n\\\\\nEOF", rib=False)
        check("trunc/fg-heredoc-escaped-bs-safe-denies",
              (_verdict(esc_safe), "holds a backslash" in _why(esc_safe)), ("deny", True))
        check("trunc/scan-heredoc-escaped-bs-safe-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\n\\\\\nEOF"), "body-backslash")
        # QA round 2 BLOCKER 2: `$\<nl>(` joins into a command substitution that bash runs. ALLOWED at
        # 9c310c2c (scan None); denies now. Its safe counterpart (no detach inside) was ALLOWED at
        # 9c310c2c and is a disclosed deny now.
        check("trunc/fg-heredoc-continued-cmdsub-detach-denies",
              _verdict(bg("cat <<EOF\n$\\\n(printf DETACHED & wait)\nEOF", rib=False)), "deny")
        check("trunc/scan-heredoc-continued-cmdsub-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\n$\\\n(printf DETACHED & wait)\nEOF"),
              "detach")
        cont_safe = bg("cat <<EOF\n$\\\n(printf SAFE)\nEOF", rib=False)
        check("trunc/fg-heredoc-continued-cmdsub-safe-denies",
              (_verdict(cont_safe), "holds a backslash" in _why(cont_safe)), ("deny", True))
        # Unchanged controls: a QUOTED delimiter's body with backslashes is literal data and allows; the
        # structural end finder for a `$(...)` inside double quotes gives up (-1) on an unquoted body
        # holding a backslash instead of predicting its end, so a detach after the real span stays seen.
        check("trunc/fg-quoted-heredoc-backslash-body-allows",
              _verdict(bg("cat <<'EOF'\nC:\\path & \\\\\nEOF", rib=False)), "allow")
        check("trunc/cmdsub-end-unquoted-backslash-body-unclosed",
              aiqt_hooks._orch_cmdsub_end("$(cat <<EOF\nEO\\\nF\n)", 2, 19)[0], -1)
        check("trunc/fg-dq-cmdsub-backslash-body-detach-denies",
              _verdict(bg("x=\"$(cat <<EOF\nEO\\\nF\n)\"; sleep 1 &\nEOF\n)\"", rib=False)), "deny")
        # QA round 3 MEDIUM: a double-quoted $(...) the structural reader cannot close used to fall back to
        # a character scan that stayed inside the balanced outer double quotes and ALLOWED (both forms at
        # ec85f8b0; bash ran the detach). It now DENIES with a reason naming the unclosed substitution.
        for name, cmd in (
                ("trunc/fg-dq-cmdsub-continued-detach-denies",
                 "x=\"$(cat <<EOF\n$\\\n(printf DETACHED >&2 & wait)\nEOF\n)\""),
                ("trunc/fg-dq-cmdsub-escaped-bs-detach-denies",
                 "x=\"$(cat <<EOF\n\\\\\nEOF\nprintf DETACHED >&2 &\nwait\n)\"")):
            res = bg(cmd, rib=False)
            check(name, (_verdict(res), "cannot close" in _why(res),
                         aiqt_hooks._orch_foreground_detach_kind(cmd)),
                  ("deny", True, "unclosed-substitution"))
        # The safe double-quoted form with a backslash under an UNQUOTED delimiter is a disclosed deny;
        # the same body under a quoted delimiter is literal and still allows.
        check("trunc/fg-dq-cmdsub-unquoted-bs-safe-denies",
              _verdict(bg("git commit -m \"$(cat <<EOF\nC:\\path\nEOF\n)\"", rib=False)), "deny")
        check("trunc/fg-dq-cmdsub-quoted-bs-safe-allows",
              _verdict(bg("git commit -m \"$(cat <<'EOF'\nC:\\path\nEOF\n)\"", rib=False)), "allow")
        # Delimiter words that are not simple (QA round 5 scope reduction): an ANSI-C $'...' word, an
        # escaped word, partial quoting, or a quoted word carrying a control byte is NOT read as a
        # here-document, so the lines after it are scanned as code. A safe body there is a disclosed
        # over-refusal; a detach after it, or one in a body bash ends elsewhere, denies.
        for name, cmd in (
                ("trunc/scan-ansic-delim-safe-overrefusal-kind", "cat <<$'E\\x4fF'\nA & B\nEOF"),
                ("trunc/scan-escaped-delim-safe-overrefusal-kind", "cat <<\\EOF\nA & B\nEOF"),
                ("trunc/scan-partial-quoted-delim-safe-overrefusal-kind", "cat <<E'OF'\nA & B\nEOF"),
                ("trunc/scan-ansic-delim-decoded-then-detach", "cat <<$'E\\x4fF'\nA\nEOF\nsleep 100 &"),
                ("trunc/scan-ansic-delim-nul-fails-closed", "cat <<$'E\\0F'\nA & B\nE"),
                ("trunc/scan-quoted-delim-ctlesc-fails-closed",
                 "cat <<'E\x01F'\nE\x01\x01F\nsleep 100 &\nE\x01F")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "detach")
        # QA round 6 MAJOR: a double-quoted $(...) holding a here-document was skipped whole to its closing
        # paren, so a bare '&' outside the body but inside the span was never judged (each ALLOWED at
        # 4e3e66ea; the body-quote form DENIED on main; bash 5.3 printed DETACHED). The span's inner text is
        # now scanned by the same scan (bodies data, everything else code), so each denies as a detach while
        # the commit-message forms (H5 and the amp form above) still allow.
        dq_hd_detach = "echo \"$(cat <<'EOF'\n\"\nEOF\nprintf DETACHED >&2 &\nwait\n)\""
        for name, cmd in (
                ("trunc/scan-r6-dq-cmdsub-heredoc-body-quote-detach-kind", dq_hd_detach),
                ("trunc/scan-r6-dq-cmdsub-heredoc-detach-kind",
                 "echo \"$(cat <<'EOF'\nx\nEOF\nprintf DETACHED >&2 &\nwait\n)\""),
                ("trunc/scan-r6-dq-cmdsub-unquoted-heredoc-detach-kind",
                 "echo \"$(cat <<EOF\nx\nEOF\nprintf DETACHED >&2 &\nwait\n)\""),
                ("trunc/scan-r6-dq-cmdsub-heredoc-same-line-detach-kind",
                 "echo \"$(cat <<'EOF' & wait\nx\nEOF\n)\"")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "detach")
        check("trunc/fg-r6-dq-cmdsub-heredoc-detach-denies", _verdict(bg(dq_hd_detach, rib=False)), "deny")
        check("trunc/fg-r6-dq-cmdsub-two-commit-messages-allows",
              _verdict(bg("git commit -m \"$(cat <<'EOF'\na\nEOF\n)\" -m \"$(cat <<'EOF'\nb's\nEOF\n)\"",
                          rib=False)), "allow")
        # QA round 6 MEDIUM: a here-document operator the scan does not read as one (after an untracked
        # construct such as ${x:-}, or under a delimiter word that is not simple), with a later line, had
        # its body scanned as code, so two body apostrophes rebalanced the quote state around a real '&'
        # (each ALLOWED on main and at 4e3e66ea, except the dropped-pending form, denied there only as an
        # open quote; bash 5.3 printed DETACHED). Each now denies as cannot-evaluate.
        for name, cmd in (
                ("trunc/scan-r6-dq-paramexp-heredoc-shift-kind",
                 "echo \"${x:-}\"; cat <<'EOF'\n'\nEOF\nprintf DETACHED >&2 &\ncat <<'EOF'\n'\nEOF\nwait"),
                ("trunc/scan-r6-paramexp-heredoc-shift-kind",
                 "echo ${x:-}; cat <<'EOF'\n'\nEOF\nprintf DETACHED >&2 &\ncat <<'EOF'\n'\nEOF\nwait"),
                ("trunc/scan-r6-paramexp-dash-heredoc-shift-kind",
                 "echo ${x:-}; cat <<-'EOF'\n'\n\tEOF\nprintf DETACHED >&2 &\ncat <<-'EOF'\n'\nEOF\nwait"),
                ("trunc/scan-r6-dropped-pending-heredoc-shift-kind",
                 "cat <<'EOF' ${x:-}\n'\nEOF\nprintf DETACHED >&2 &\necho \"'\" \"'\"\nwait"),
                ("trunc/scan-r6-partial-delim-heredoc-shift-kind",
                 "cat <<E'OF'\n'\nEOF\nprintf DETACHED >&2 &\ncat <<E'OF'\n'\nEOF\nwait")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "heredoc-unread")
        # The same inside a double-quoted $(...): the structural walk marks the span unreadable.
        for name, cmd in (
                ("trunc/scan-r6-dq-cmdsub-paramexp-heredoc-shift-kind",
                 "echo \"$(echo ${x}; cat <<'EOF'\n\"\"\nEOF\nprintf DETACHED >&2 &\nwait\n)\""),
                ("trunc/scan-r6-dq-cmdsub-partial-delim-shift-kind",
                 "echo \"$(cat <<E'OF'\n\"\"\nEOF\nprintf DETACHED >&2 &\nwait\n)\"")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "unclosed-substitution")
        shift_res = bg("echo \"${x:-}\"; cat <<'EOF'\n'\nEOF\nprintf DETACHED >&2 &\ncat <<'EOF'\n'\nEOF\nwait",
                       rib=False)
        check("trunc/fg-r6-paramexp-heredoc-shift-denies",
              (_verdict(shift_res), "cannot be evaluated" in _why(shift_res)), ("deny", True))
        # The disclosed over-refusals this adds, pinned: a safe here-document under a delimiter word that
        # is not simple, or after an untracked construct, denies when a later line exists; a '<<' with no
        # later line (a one-line left shift) and a here-string are scanned on as before.
        partial_safe = bg("cat <<E'OF'\nit's safe\nEOF", rib=False)
        check("trunc/fg-r6-partial-delim-safe-overrefusal-reason",
              (_verdict(partial_safe), "cannot be evaluated" in _why(partial_safe),
               "<<E'OF'" in _why(partial_safe)), ("deny", True, True))
        for name, cmd, want in (
                ("trunc/scan-r6-paramexp-then-safe-heredoc-overrefusal-kind",
                 "echo ${HOME}; cat <<'EOF'\nhello\nEOF", "heredoc-unread"),
                ("trunc/scan-r6-escaped-delim-safe-overrefusal-kind", "cat <<\\EOF\nhello\nEOF",
                 "heredoc-unread"),
                ("trunc/scan-r6-arith-shift-multiline-overrefusal-kind", "x=$((1<<2))\necho $x",
                 "heredoc-unread"),
                ("trunc/scan-r6-arith-shift-one-line-none", "echo $((1<<2))", None),
                ("trunc/scan-r6-herestring-after-paramexp-none", "echo ${x}; cat <<<'a'\necho b", None)):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), want)
        # QA round 6 MEDIUM (diagnostic): the open-quote reason names every here-document over-refusal
        # class, not only the backslash body (it named only that at 4e3e66ea).
        ub_why = _why(bg("echo 'open", rib=False))
        check("trunc/fg-r6-unbalanced-reason-lists-overrefusals",
              tuple(t in ub_why for t in ("<<E'OF'", "does not track", "holding a backslash",
                                          "cannot close or read exactly", "inside double quotes",
                                          "never arrives")), (True,) * 6)
        # QA round 7 MAJOR: in an unquoted-delimiter body, a $(...) span the walk reads inexactly (it holds
        # ${x}, $((1)), or a case pattern) had the rest of the body scanned as code and a None returned as
        # clean, so body apostrophes wrapped a later real substitution's '&' (each ALLOWED at 5c5ea3aa and
        # DENIED on main; bash 5.3 printed DETACHED). An unbounded body-substitution scan that finds nothing
        # is now cannot-evaluate, never clean; an unclosed backtick in a body is held to the same rule.
        r7_tail = " '\n$(printf DETACHED >&2 & wait)\n'\nEOF"
        for name, cmd in (
                ("trunc/scan-r7-body-paramexp-span-quote-shift-kind", "cat <<EOF\n'\n$(echo ${x})" + r7_tail),
                ("trunc/scan-r7-body-arith-span-quote-shift-kind", "cat <<EOF\n'\n$(echo $((1)))" + r7_tail),
                ("trunc/scan-r7-body-case-span-quote-shift-kind",
                 "cat <<EOF\n'\n$(case x in x) :;; esac)" + r7_tail),
                ("trunc/scan-r7-body-unclosed-backtick-quote-shift-kind",
                 "cat <<EOF\n'\n`echo '\n$(printf DETACHED >&2 & wait)\n'\nEOF"),
                # The disclosed over-refusal this adds: a safe body holding such a span denies too.
                ("trunc/scan-r7-body-paramexp-span-safe-overrefusal-kind", "cat <<EOF\nhi $(echo ${x})\nEOF"),
                ("trunc/scan-r7-body-heredoc-span-safe-overrefusal-kind",
                 "cat <<EOF\n$(cat <<X\nhi\nX\n)\nEOF")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "body-substitution-unread")
        r7_res = bg("cat <<EOF\n'\n$(echo ${x})" + r7_tail, rib=False)
        check("trunc/fg-r7-body-paramexp-span-quote-shift-denies",
              (_verdict(r7_res), "cannot be evaluated" in _why(r7_res), "class (d)" in _why(r7_res)),
              ("deny", True, True))
        # Spans read exactly stay clean, and a detach the fallback finds is still reported as one.
        check("trunc/scan-r7-body-exact-spans-none",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\nnow: $(date) and `pwd`\nEOF"), None)
        check("trunc/fg-r7-body-exact-spans-allows",
              _verdict(bg("cat <<EOF\nnow: $(date) and `pwd`\nEOF", rib=False)), "allow")
        # QA round 8 BLOCKER: a closed body span whose content holds a quote or a nested substitution
        # opener was scanned as code and inherited that scan's residuals: `echo "$(job & wait)"` in a body
        # backtick span read as double-quoted text (each ALLOWED at a72d694f; the quote forms DENIED on
        # main; bash 5.3 ran the '&' in each). Only a FLAT span is judged now; any other closed span is
        # cannot-evaluate, never the inner scan's result.
        r8_job = "true & echo BG=$!; wait"
        r8_repro = "cat <<EOF\n\"\n`echo \"$(printf DETACHED & wait)\"`\n\"\nEOF"
        for name, cmd in (
                ("trunc/scan-r8-body-backtick-dq-cmdsub-kind", r8_repro),
                ("trunc/scan-r8-body-backtick-squote-eval-kind", "cat <<EOF\n'\n`eval '" + r8_job + "'`\n'\nEOF"),
                ("trunc/scan-r8-body-cmdsub-squote-eval-kind", "cat <<EOF\n'\n$(eval '" + r8_job + "')\n'\nEOF"),
                ("trunc/scan-r8-body-cmdsub-dquote-eval-kind",
                 "cat <<EOF\n\"\n$(eval \"sleep 0 & jobs -p; wait\")\n\"\nEOF"),
                ("trunc/scan-r8-body-backtick-dquote-eval-kind",
                 "cat <<EOF\n\"\n`eval \"sleep 0 & jobs -p; wait\"`\n\"\nEOF"),
                ("trunc/scan-r8-body-backtick-paramexp-comment-kind",
                 "cat <<EOF\n`echo ${x:- #}; " + r8_job + "`\nEOF"),
                ("trunc/scan-r8-body-backtick-nested-cmdsub-kind",
                 "cat <<EOF\n`echo $(echo ${x:- #}); " + r8_job + "`\nEOF"),
                # Nested openers that the inner scan did catch now deny unread too (the class rule).
                ("trunc/scan-r8-body-backtick-dollar-bracket-kind", "cat <<EOF\n`echo $[1]; " + r8_job + "`\nEOF"),
                ("trunc/scan-r8-body-backtick-procsub-kind", "cat <<EOF\n`cat <(echo x); " + r8_job + "`\nEOF"),
                ("trunc/scan-r8-body-backtick-arith-kind", "cat <<EOF\n`echo $((1)); " + r8_job + "`\nEOF"),
                # The disclosed over-refusal this adds: a safe span holding a quote denies too.
                ("trunc/scan-r8-body-cmdsub-dq-safe-overrefusal-kind", "cat <<EOF\nhi $(echo \"x\")\nEOF"),
                ("trunc/scan-r8-body-backtick-dq-safe-overrefusal-kind", "cat <<EOF\nd `date +\"%F\"`\nEOF")):
            check(name, aiqt_hooks._orch_foreground_detach_kind(cmd), "body-substitution-unread")
        r8_res = bg(r8_repro, rib=False)
        check("trunc/fg-r8-body-backtick-dq-cmdsub-denies",
              (_verdict(r8_res), "cannot be evaluated" in _why(r8_res), "class (d)" in _why(r8_res),
               "nested substitution opener" in _why(r8_res)), ("deny", True, True, True))
        # Its nested backtick sibling (a backtick in a $(...) span) was already unreadable to the walk; a
        # flat span is still judged, so its real detach denies as one.
        check("trunc/scan-r8-body-cmdsub-nested-backtick-denies",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\n\"\n$(echo \"`" + r8_job + "`\")\n\"\nEOF")
              is not None, True)
        check("trunc/scan-r8-body-flat-cmdsub-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\n$(" + r8_job + ")\nEOF"), "detach")
        check("trunc/scan-r8-body-flat-backtick-detach-kind",
              aiqt_hooks._orch_foreground_detach_kind("cat <<EOF\n`" + r8_job + "`\nEOF"), "detach")
        # QA round 8 (found while checking siblings): `)` is a metacharacter, so a `#` right after a
        # subshell's closing paren opens a comment to bash. The walk read it as word text, closed the body
        # span on its first line, and left the next line's real '&' in the body as data (ALLOWED at
        # a72d694f, DENIED on main; bash 5.3 ran it). The walk now reads the comment and finds bash's end.
        r8_paren = "cat <<EOF\n$( (true)# )\n" + r8_job + "\n)\nEOF"
        check("trunc/scan-r8-body-paren-comment-detach-kind", aiqt_hooks._orch_foreground_detach_kind(r8_paren),
              "detach")
        check("trunc/fg-r8-body-paren-comment-detach-denies", _verdict(bg(r8_paren, rib=False)), "deny")
        check("trunc/cmdsub-end-r8-paren-comment",
              aiqt_hooks._orch_cmdsub_end("$( (true)# )\nx\n)", 2, 16), (16, False, True))
        check("trunc/fg-r8-body-paren-comment-safe-allows",
              _verdict(bg("cat <<EOF\n$( (true)# )\nsafe\n)\nEOF", rib=False)), "allow")
        # Malformed input fails CLOSED with a reason (before the fix each of these silently allowed): a
        # tool_input that is missing, null, or not an object; a run_in_background that is not a real
        # boolean (the string "true" is never read as foreground); a foreground command that is not a
        # string. An omitted run_in_background is a foreground call and stays in scope as before.
        raw = lambda extra: _verdict(aiqt_hooks.orch_truncation_guard(dict(
            {"hook_event_name": "PreToolUse", "cwd": str(t.root), "session_id": "s1",
             "tool_name": "Bash"}, **extra)))
        check("trunc/malformed-tool-input-null-denies", raw({"tool_input": None}), "deny")
        check("trunc/malformed-tool-input-missing-denies", raw({}), "deny")
        check("trunc/malformed-tool-input-array-denies", raw({"tool_input": ["sleep 5 &"]}), "deny")
        check("trunc/malformed-tool-input-string-denies", raw({"tool_input": "sleep 5 &"}), "deny")
        check("trunc/malformed-rib-string-true-denies",
              raw({"tool_input": {"command": "python3 build.py", "run_in_background": "true"}}), "deny")
        check("trunc/malformed-rib-string-false-denies",
              raw({"tool_input": {"command": "ls", "run_in_background": "false"}}), "deny")
        check("trunc/malformed-rib-int-denies",
              raw({"tool_input": {"command": "ls", "run_in_background": 1}}), "deny")
        check("trunc/malformed-rib-null-denies",
              raw({"tool_input": {"command": "ls", "run_in_background": None}}), "deny")
        check("trunc/malformed-fg-command-nonstr-denies", raw({"tool_input": {"command": 42}}), "deny")
        check("trunc/malformed-fg-command-missing-denies", raw({"tool_input": {}}), "deny")
        check("trunc/rib-omitted-foreground-allows", raw({"tool_input": {"command": "ls -la"}}), "allow")
        check("trunc/rib-omitted-foreground-detach-denies", raw({"tool_input": {"command": "sleep 5 &"}}),
              "deny")
        # Registry scope, the disclosed residual: with NO registry file the guard is inert (malformed input
        # included), while a PRESENT but unreadable or invalid registry keeps it active (fail-closed).
        check("trunc/malformed-inert-no-registry", _verdict(aiqt_hooks.orch_truncation_guard(
            {"hook_event_name": "PreToolUse", "cwd": str(ti.root), "tool_name": "Bash",
             "tool_input": None})), "allow")
        tb = Fixture(tmp, "trunc-badreg")
        (tb.root / ".aiqt" / "orchestration.local.json").write_text("{not json", encoding="utf-8")
        check("trunc/bad-registry-detach-denies", _verdict(aiqt_hooks.orch_truncation_guard(
            tb.payload("PreToolUse", "Bash", {"command": "sleep 5 &"}))), "deny")
        (tb.root / ".aiqt" / "orchestration.local.json").unlink()
        (tb.root / ".aiqt" / "orchestration.local.json").mkdir()
        check("trunc/dir-registry-malformed-denies", _verdict(aiqt_hooks.orch_truncation_guard(
            {"hook_event_name": "PreToolUse", "cwd": str(tb.root), "tool_name": "Bash",
             "tool_input": None})), "deny")
        # Where a `#` opens a comment follows bash as well as the historical str.isspace() rule, and either
        # rule's detach denies: a `#` after a character bash does not treat as a word break (carriage return,
        # the 0x1c separator, an ideographic space) is NOT a comment to bash, so the `&` after it detaches;
        # a `#` after a metacharacter (`;#`) IS a comment to bash, so the apostrophe in it no longer shifts
        # the scan past the real `&` on the next line. Each was a silent allow before this fix.
        check("trunc/fg-hash-after-cr-detach-denies", _verdict(bg("touch m y\r#z &", rib=False)), "deny")
        check("trunc/fg-hash-after-x1c-detach-denies", _verdict(bg("touch m y\x1c#z &", rib=False)), "deny")
        check("trunc/fg-hash-after-u3000-detach-denies", _verdict(bg("touch m y　#z &", rib=False)),
              "deny")
        check("trunc/fg-metachar-comment-quote-shift-denies",
              _verdict(bg("echo a;# it's\nsleep 5 & echo done # '", rib=False)), "deny")
        # A scan that ends inside an open quote denies with a reason about the quote, not a false claim
        # that a bare '&' was found (before this fix it reused the bare-& detach reason). A genuinely
        # unbalanced quote is used here: a here-document body apostrophe is data now and ALLOWS.
        uq = bg("echo 'oops & bg", rib=False)
        uq_reason = (uq[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        check("trunc/fg-unbalanced-quote-reason-names-quote",
              (_verdict(uq), "quote still open" in uq_reason, "detaches a child" in uq_reason),
              ("deny", True, False))
        # The shared fail-closed contract: a missing tool_name, and a cwd that is missing, null, not a
        # string, or empty, deny (before this fix each reached an allow). The non-Bash row is a CONTROL, not
        # a regression row: it guards against a new over-deny and passes before and after the fix.
        nocwd = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                 "tool_input": {"command": "sleep 5 &"}}
        check("trunc/missing-tool-name-denies", _verdict(aiqt_hooks.orch_truncation_guard(
            {"hook_event_name": "PreToolUse", "cwd": str(t.root), "session_id": "s1",
             "tool_input": {"command": "sleep 5 &"}})), "deny")
        check("trunc/malformed-cwd-missing-denies", _verdict(aiqt_hooks.orch_truncation_guard(nocwd)), "deny")
        check("trunc/malformed-cwd-null-denies",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(nocwd, cwd=None))), "deny")
        check("trunc/malformed-cwd-int-denies",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(nocwd, cwd=42))), "deny")
        check("trunc/malformed-cwd-empty-denies",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(nocwd, cwd=""))), "deny")
        check("trunc/non-bash-tool-allows", raw({"tool_name": "Write", "tool_input": None}), "allow")
        # A present but empty or non-string tool_name cannot be matched, so it denies (the wrtscp precedent)
        # instead of reading as a non-Bash tool; before this fix each reached the out-of-scope allow.
        check("trunc/malformed-tool-name-empty-denies", raw({"tool_name": ""}), "deny")
        check("trunc/malformed-tool-name-int-denies", raw({"tool_name": 5}), "deny")
        check("trunc/malformed-tool-name-list-denies", raw({"tool_name": ["Bash"]}), "deny")
        # A tool_name carrying a NUL or any other control character is never a real tool name, so it must
        # not take the non-Bash out-of-scope allow (the round-3 codex finding: a "Bash"-plus-NUL tool_name
        # was allowed silently); an ordinary non-Bash plain string still allows (the control row).
        check("trunc/tool-name-nul-denies",
              raw(dict(tool_name="Bash\x00", tool_input=None)), "deny")
        check("trunc/tool-name-nul-only-denies",
              raw(dict(tool_name="\x00", tool_input=None)), "deny")
        check("trunc/tool-name-control-char-denies",
              raw(dict(tool_name="Ba\x1bsh", tool_input=None)), "deny")
        check("trunc/tool-name-ordinary-non-bash-allows",
              raw(dict(tool_name="mcp__files__read", tool_input=None)), "allow")
        # A string cwd whose registry walk cannot be carried out (not a readable directory, a NUL, an
        # unreadable ancestor) denies with a named reason AND an actionable fix; a cwd whose COMPLETED walk
        # and git-toplevel union find no registry allows (a git FAILURE alone never denies: the round-3
        # git lockout is withdrawn). The confirmed-outside CONTROL row passes before and after the fix.
        def _cwd_case(cwd):
            res = aiqt_hooks.orch_truncation_guard(dict(nocwd, cwd=cwd))
            why = (res[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            return _verdict(res), why
        cv, cw = _cwd_case(str(t.root) + "\x00x")
        check("trunc/cwd-nul-denies", (cv, "NUL" in cw), ("deny", True))
        cv, cw = _cwd_case(str(tmp / "no-such-dir"))
        check("trunc/cwd-nonexistent-denies", (cv, "not an existing path" in cw), ("deny", True))
        (tmp / "cwd-file.txt").write_text("x\n", encoding="utf-8")
        cv, cw = _cwd_case(str(tmp / "cwd-file.txt"))
        check("trunc/cwd-regular-file-denies", (cv, "not a directory" in cw), ("deny", True))
        # An unreadable directory: os.access is patched for this one path only (a root-run self-test would
        # otherwise read every directory as readable).
        noread = tmp / "cwd-noread"
        noread.mkdir()
        saved_access = aiqt_hooks.os.access

        def _no_access(path, mode, *a, **k):
            return False if str(path) == str(noread) else saved_access(path, mode, *a, **k)
        try:
            aiqt_hooks.os.access = _no_access
            cv, cw = _cwd_case(str(noread))
        finally:
            aiqt_hooks.os.access = saved_access
        check("trunc/cwd-unreadable-dir-denies", (cv, "cannot read and enter" in cw), ("deny", True))
        # ROUND-3 LOCKOUT WITHDRAWN: scope is the registry walk UNIONED with a git-resolved toplevel's
        # registry (round 4), and a git FAILURE alone never denies, so a cwd git cannot resolve (a bare
        # repository, a dubious-ownership refusal, a missing git binary, a broken config, a timeout) is
        # OUT OF SCOPE when no registry sits on the cwd's ancestor chain (each such row was a blanket deny
        # at the round-3 revision and now allows: the restored-allow controls), while the SAME failing git
        # inside an orchestrated tree still applies the guard (a detach denies, a plain command allows).
        # _recovery_git is patched to raise or refuse, so these rows pin that the union leg turns every
        # git failure into a clean out-of-scope read, never a deny.
        bare = tmp / "cwd-bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True,
                       timeout=30)
        check("trunc/cwd-bare-repo-no-registry-allows", _cwd_case(str(bare))[0], "allow")
        barein = t.root / "inner-bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(barein)], check=True, capture_output=True,
                       timeout=30)
        cv, cw = _cwd_case(str(barein))
        check("trunc/cwd-bare-repo-in-orchestrated-tree-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        check("trunc/cwd-bare-repo-in-orchestrated-tree-plain-allows",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(
                  nocwd, cwd=str(barein), tool_input=dict(command="ls -la")))), "allow")
        saved_git = aiqt_hooks._recovery_git

        def _git_timeout(*_a, **_k):
            raise subprocess.TimeoutExpired(["git"], 5)

        def _git_oserror(*_a, **_k):
            raise OSError("simulated spawn failure")

        def _git_missing(*_a, **_k):
            raise FileNotFoundError(2, "No such file or directory", "git")

        class _GitDubious:
            returncode = 128
            stdout = ""
            stderr = ("fatal: detected dubious ownership in repository at '/fixture'\n"
                      "To add an exception for this directory, call:\n\n"
                      "\tgit config --global --add safe.directory /fixture\n")

        def _git_dubious(*_a, **_k):
            return _GitDubious()

        class _GitBadConfig:
            returncode = 128
            stdout = ""
            stderr = "fatal: bad config line 1 in file /fixture/.gitconfig\n"

        def _git_badconfig(*_a, **_k):
            return _GitBadConfig()
        norig = tmp / "cwd-no-registry-repo"
        norig.mkdir()
        subprocess.run(["git", "init", "-q", str(norig)], check=True, capture_output=True, timeout=30)
        try:
            for _sim, _row in ((_git_timeout, "trunc/git-timeout-no-registry-allows"),
                               (_git_oserror, "trunc/git-error-no-registry-allows"),
                               (_git_missing, "trunc/no-git-binary-no-registry-allows"),
                               (_git_dubious, "trunc/git-dubious-ownership-no-registry-allows"),
                               (_git_badconfig, "trunc/git-broken-config-no-registry-allows")):
                aiqt_hooks._recovery_git = _sim
                check(_row, _cwd_case(str(norig))[0], "allow")
            # the same failing git INSIDE an orchestrated tree: the registry is on the walk, so the guard
            # still applies its rules (the detach denies with the DETACH reason, a plain command allows).
            aiqt_hooks._recovery_git = _git_missing
            cv, cw = _cwd_case(str(t.root))
            check("trunc/git-refusal-with-registry-detach-denies",
                  (cv, "detaches a child" in cw), ("deny", True))
            check("trunc/git-refusal-with-registry-plain-allows",
                  _verdict(aiqt_hooks.orch_truncation_guard(dict(
                      nocwd, cwd=str(t.root), tool_input=dict(command="ls -la")))), "allow")
        finally:
            aiqt_hooks._recovery_git = saved_git
        # inside a .git directory git discovery refuses, but the walk decides: a registry above the work
        # tree keeps the guard active from inside .git, and a registryless repo's .git is out of scope.
        cv, cw = _cwd_case(str(t.root / ".git"))
        check("trunc/cwd-inside-git-dir-with-registry-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        check("trunc/cwd-inside-git-dir-no-registry-allows", _cwd_case(str(norig / ".git"))[0], "allow")
        # the walk's own failure DENIES with an actionable fix: an ancestor directory it cannot open (the
        # openat of ".." is patched to refuse, as a root-run self-test reads every directory as readable).
        anc = tmp / "anc-noread"
        (anc / "child").mkdir(parents=True)
        saved_open = aiqt_hooks.os.open

        def _no_parent_open(path, flags, *a, **k):
            if path == ".." and k.get("dir_fd") is not None:
                raise PermissionError(13, "Permission denied", "..")
            return saved_open(path, flags, *a, **k)
        try:
            aiqt_hooks.os.open = _no_parent_open
            cv, cw = _cwd_case(str(anc / "child"))
        finally:
            aiqt_hooks.os.open = saved_open
        check("trunc/cwd-unreadable-ancestor-denies",
              (cv, "ancestor directory" in cw, "search permission" in cw),
              ("deny", True, True))
        # a registry in a NON-GIT ancestor tree scopes the guard in (the walk needs no repository), and a
        # nested repository under an orchestrated tree is scoped in through the ancestor registry.
        orchtree = tmp / "orch-tree"
        (orchtree / ".aiqt").mkdir(parents=True)
        (orchtree / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                               encoding="utf-8")
        (orchtree / "sub").mkdir()
        cv, cw = _cwd_case(str(orchtree / "sub"))
        check("trunc/registry-above-non-git-dir-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        check("trunc/registry-above-non-git-dir-plain-allows",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(
                  nocwd, cwd=str(orchtree / "sub"), tool_input=dict(command="ls -la")))), "allow")
        nested = t.root / "nested-repo"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", str(nested)], check=True, capture_output=True, timeout=30)
        cv, cw = _cwd_case(str(nested))
        check("trunc/nested-repo-under-registry-tree-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        outside = tmp / "cwd-outside"
        outside.mkdir()
        # git's own discovery is held to tmp as well (GIT_CEILING_DIRECTORIES), so a TMPDIR inside some
        # repository cannot turn this precondition red.
        probe = subprocess.run(["git", "-C", str(outside), "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.realpath(str(tmp))))
        check("trunc/cwd-confirmed-outside-repo-allows",
              (probe.returncode, _cwd_case(str(outside))[0]), (128, "allow"))
        # HERMETICITY of every truncation-guard row: a registry ABOVE the fixture root never decides a
        # verdict (before this, a live registry above TMPDIR turned each out-of-scope row above into a
        # deny and let each registry-present row pass on the host's registry instead of its fixture's).
        # The run-wide ceiling covers the whole chain up to the filesystem root; on a tree under tmp, a
        # ceiling at ceil/inner reads the registry at ceil as absent, a registry AT the ceiling root is
        # still found, and with only the run-wide ceiling (which sits above ceil) the walk finds it, as
        # the production walk, which takes no ceiling, always does.
        # BEHAVIOURAL, not declarative (round 4): the old form asserted the root's dev/ino sat in the
        # ceiling's .above set, which a ceiling that declares the root but delegates its probe anyway
        # still satisfies. This row drives descriptors through the probes instead: the ACTIVE run-wide
        # ceiling must read a filesystem-root descriptor as registry-free, and a ceiling built by the
        # same constructor over an always-True probe must mask the root while still delegating a
        # descriptor at the ceiling root itself.
        root_fd = os.open(os.path.realpath(os.sep), aiqt_hooks._ORCH_O_WALK | os.O_DIRECTORY)
        tmpl_fd = os.open(os.path.realpath(str(tmp)), aiqt_hooks._ORCH_O_WALK | os.O_DIRECTORY)
        try:
            always = _registry_ceiling(tmp, lambda dirfd: True)
            check("trunc/hermetic-run-ceiling-covers-filesystem-root",
                  (aiqt_hooks._orch_dirfd_has_registry(root_fd), always(root_fd), always(tmpl_fd)),
                  (False, False, True))
        finally:
            os.close(root_fd)
            os.close(tmpl_fd)
        ceil = tmp / "ceil-probe"
        (ceil / ".aiqt").mkdir(parents=True)
        (ceil / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)), encoding="utf-8")
        (ceil / "inner" / "sub").mkdir(parents=True)
        run_probe = aiqt_hooks._orch_dirfd_has_registry
        try:
            aiqt_hooks._orch_dirfd_has_registry = _registry_ceiling(ceil / "inner", run_probe)
            hidden = aiqt_hooks._orch_registry_walk(str(ceil / "inner" / "sub"))
            aiqt_hooks._orch_dirfd_has_registry = _registry_ceiling(ceil, run_probe)
            at_root = aiqt_hooks._orch_registry_walk(str(ceil / "inner" / "sub"))
        finally:
            aiqt_hooks._orch_dirfd_has_registry = run_probe
        check("trunc/hermetic-ceiling-hides-registry-above-root", hidden, ("none", None))
        check("trunc/hermetic-ceiling-keeps-registry-at-root", at_root, ("found", None))
        check("trunc/hermetic-walk-without-inner-ceiling-finds-registry",
              aiqt_hooks._orch_registry_walk(str(ceil / "inner" / "sub")), ("found", None))
        # ROUND 4, UNION LEG: core.worktree (in a repository config, or in the config of a separate git
        # dir) can point the work tree OFF the cwd's physical ancestor chain. git then resolves a toplevel
        # the walk never visits, and the registry THERE must still scope the guard in (main's rev-parse
        # scoping denied these; the walk alone allowed them). Each detach row is red with the union leg
        # removed; the plain row guards the other direction (an in-scope plain foreground call stays an
        # allow). The union probes through the run-wide ceiling, so a host registry above tmp still never
        # decides a row.
        gw = tmp / "gw"
        (gw / "B" / ".aiqt").mkdir(parents=True)
        (gw / "B" / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                               encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(gw / "A")], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(gw / "A"), "config", "core.worktree", str(gw / "B")],
                       check=True, capture_output=True, timeout=30)
        cv, cw = _cwd_case(str(gw / "A"))
        check("trunc/git-core-worktree-registry-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        check("trunc/git-core-worktree-registry-plain-allows",
              _verdict(aiqt_hooks.orch_truncation_guard(dict(
                  nocwd, cwd=str(gw / "A"), tool_input=dict(command="ls -la")))), "allow")
        sepg = tmp / "gw-sep"
        sepg.mkdir()
        subprocess.run(["git", "init", "-q", "--separate-git-dir", str(sepg / "meta.git"),
                        str(sepg / "work")], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "--git-dir", str(sepg / "meta.git"), "config", "core.worktree",
                        str(sepg / "work")], check=True, capture_output=True, timeout=30)
        (sepg / "work" / ".aiqt").mkdir()
        (sepg / "work" / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                                    encoding="utf-8")
        cv, cw = _cwd_case(str(sepg / "meta.git"))
        check("trunc/git-separate-gitdir-worktree-registry-detach-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        # ROUND 4, CRAFTED-.aiqt BRANCHES PINNED: each deny-safe branch of _orch_dirfd_has_registry gets a
        # row that is red when the branch is removed. A symlinked .aiqt pointing at a REGISTRY-FREE
        # directory pins both the no-follow open flag (following it would read a clean absent) and the
        # OSError-reads-PRESENT branch (ELOOP); the dangling symlink and the regular file pin the same
        # branch through different faults.
        craft = tmp / "craft"
        (craft / "target-no-registry").mkdir(parents=True)
        (craft / "link-dir").mkdir()
        os.symlink(str(craft / "target-no-registry"), str(craft / "link-dir" / ".aiqt"))
        cv, cw = _cwd_case(str(craft / "link-dir"))
        check("trunc/aiqt-symlink-dir-denies", (cv, "detaches a child" in cw), ("deny", True))
        (craft / "dangling").mkdir()
        os.symlink(str(craft / "no-such-target"), str(craft / "dangling" / ".aiqt"))
        cv, cw = _cwd_case(str(craft / "dangling"))
        check("trunc/aiqt-dangling-symlink-denies", (cv, "detaches a child" in cw), ("deny", True))
        (craft / "regular-file").mkdir()
        (craft / "regular-file" / ".aiqt").write_text("not a directory", encoding="utf-8")
        cv, cw = _cwd_case(str(craft / "regular-file"))
        check("trunc/aiqt-regular-file-denies", (cv, "detaches a child" in cw), ("deny", True))
        # A registry NAME whose no-follow stat faults must read PRESENT (the deny-safe branch on the stat
        # inside a real .aiqt directory). The os.stat seam is a hermetic proxy for a mode-000 .aiqt on a
        # non-root run (the fault holds on any uid, including root, per the uid-independence precedent).
        (craft / "stat-fault" / ".aiqt").mkdir(parents=True)
        saved_stat = aiqt_hooks.os.stat
        _reg_names = tuple(r.rsplit("/", 1)[-1] for r in aiqt_hooks._ORCH_REGISTRY_FILES)

        def _stat_fault(path, *a, **k):
            if k.get("dir_fd") is not None and path in _reg_names:
                raise PermissionError(13, "Permission denied", path)
            return saved_stat(path, *a, **k)
        try:
            aiqt_hooks.os.stat = _stat_fault
            cv, cw = _cwd_case(str(craft / "stat-fault"))
        finally:
            aiqt_hooks.os.stat = saved_stat
        check("trunc/aiqt-registry-name-stat-fault-denies", (cv, "detaches a child" in cw),
              ("deny", True))
        # A SEARCH-ONLY ancestor must not fail the walk: O_PATH steps need only the search permission path
        # resolution itself needs, so the registry above is still found. The real chmod 0o311 exercises
        # the kernel on a non-root run; the os.open seam refuses a READ-open of that ancestor so the row
        # also discriminates on a root run (root is not bound by the chmod) and pins the O_PATH flag
        # itself (a walk rebuilt on O_RDONLY turns this found into a walk failure).
        sonly = tmp / "search-only"
        (sonly / ".aiqt").mkdir(parents=True)
        (sonly / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                            encoding="utf-8")
        (sonly / "mid" / "leaf").mkdir(parents=True)
        saved_open_so = aiqt_hooks.os.open
        _opath = getattr(os, "O_PATH", 0)
        _leaf_ino = os.stat(str(sonly / "mid" / "leaf")).st_ino

        def _read_open_refused(path, flags, *a, **k):
            if (_opath and path == ".." and k.get("dir_fd") is not None
                    and os.fstat(k["dir_fd"]).st_ino == _leaf_ino and not flags & _opath):
                raise PermissionError(13, "Permission denied", "..")
            return saved_open_so(path, flags, *a, **k)
        try:
            aiqt_hooks.os.open = _read_open_refused
            if _opath:
                os.chmod(str(sonly / "mid"), 0o311)
            so_res = aiqt_hooks._orch_registry_walk(str(sonly / "mid" / "leaf"))
        finally:
            os.chmod(str(sonly / "mid"), 0o755)
            aiqt_hooks.os.open = saved_open_so
        check("trunc/walk-search-only-ancestor-finds-registry", so_res, ("found", None))
        # ROUND 4, CONCURRENT-MOVE DETECTION: descriptor anchoring preserves each opened directory's
        # identity, not its parent relationship, so a mid-walk rename of an ancestor redirects the ".."
        # chain. The seam performs REAL renames around the REAL openat (no filesystem result is
        # fabricated) and restores the tree before the walk returns, the adversarial interleaving from
        # the round-4 review. With the registry present throughout, the path-anchored recheck finds it and
        # the detach denies (red with the recheck removed: the walk read only the bypassed chain and
        # allowed); with no registry anywhere, the recheck's chain comparison catches the redirect and
        # denies naming the changed chain.
        race = tmp / "race"
        saved_open_rc = aiqt_hooks.os.open

        def _race_open_factory(branch, moved):
            fired = []

            def _race_open(path, flags, *a, **k):
                if (not fired and path == ".." and k.get("dir_fd") is not None
                        and os.fstat(k["dir_fd"]).st_ino == os.stat(str(branch)).st_ino):
                    fired.append(True)
                    os.rename(str(branch), str(moved))
                    try:
                        return saved_open_rc(path, flags, *a, **k)
                    finally:
                        os.rename(str(moved), str(branch))
                return saved_open_rc(path, flags, *a, **k)
            return _race_open
        (race / "tree" / ".aiqt").mkdir(parents=True)
        (race / "tree" / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                                    encoding="utf-8")
        (race / "tree" / "branch" / "cwd").mkdir(parents=True)
        (race / "outside").mkdir()
        try:
            aiqt_hooks.os.open = _race_open_factory(race / "tree" / "branch",
                                                    race / "outside" / "branch")
            cv, cw = _cwd_case(str(race / "tree" / "branch" / "cwd"))
        finally:
            aiqt_hooks.os.open = saved_open_rc
        check("trunc/walk-raced-ancestor-rename-registry-still-denies",
              (cv, "detaches a child" in cw), ("deny", True))
        (race / "bare-tree" / "branch" / "cwd").mkdir(parents=True)
        (race / "bare-outside").mkdir()
        try:
            aiqt_hooks.os.open = _race_open_factory(race / "bare-tree" / "branch",
                                                    race / "bare-outside" / "branch")
            cv, cw = _cwd_case(str(race / "bare-tree" / "branch" / "cwd"))
        finally:
            aiqt_hooks.os.open = saved_open_rc
        check("trunc/walk-raced-ancestor-rename-chain-mismatch-denies",
              (cv, "changed its ancestor chain" in cw), ("deny", True))
        # ROUND 4, SELF-BIND-MOUNT EDGE: a directory bind-mounted onto its own child repeats one dev/ino
        # between the mount root and its ".." parent without being the filesystem root, so the old
        # dev/ino-repeat stop read it as the top and missed a registry above. The walk now steps THROUGH a
        # non-root repeat. The os.fstat seam is a hermetic proxy for `mount --bind bindm/a bindm/a/b`
        # (mount privilege is unavailable here): the directory opened at a/b reports a's identity, exactly
        # as the kernel reports the bind source's identity at the mount root, while its ".." still names a.
        bindm = tmp / "bindm"
        (bindm / ".aiqt").mkdir(parents=True)
        (bindm / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                            encoding="utf-8")
        (bindm / "a" / "b").mkdir(parents=True)
        saved_fstat = aiqt_hooks.os.fstat
        _b_st = os.stat(str(bindm / "a" / "b"))
        _a_st = os.stat(str(bindm / "a"))

        def _bind_sim_fstat(fd):
            st = saved_fstat(fd)
            if st.st_ino == _b_st.st_ino and st.st_dev == _b_st.st_dev:
                return _a_st
            return st
        # The path-anchored recheck would ALSO reach the registry here (path resolution crosses mounts),
        # masking a reverted repeat-stop, so it is stubbed to a clean miss for this row alone: the found
        # below can then come only from the walk stepping through the non-root repeat. The recheck's own
        # behaviour is pinned by the raced-ancestor rows above.
        saved_recheck = aiqt_hooks._orch_walk_recheck
        try:
            aiqt_hooks.os.fstat = _bind_sim_fstat
            aiqt_hooks._orch_walk_recheck = lambda _cwd, _chain: ("none", None)
            bind_res = aiqt_hooks._orch_registry_walk(str(bindm / "a" / "b"))
        finally:
            aiqt_hooks.os.fstat = saved_fstat
            aiqt_hooks._orch_walk_recheck = saved_recheck
        check("trunc/walk-self-bind-mount-sim-finds-registry-above", bind_res, ("found", None))
        # ROUND 5, FAIL-CLOSED BRANCHES PINNED: each branch below keeps main's deny where the scope read
        # cannot be completed, and each row is red when its branch is turned into a clean miss (an allow).
        # The cwd carries no registry, so without the fault the call allows; the deny can come only from
        # the faulted branch. The union leg: git names a toplevel this probe cannot open as a directory (a
        # regular file, ENOTDIR on any uid) must read IN SCOPE, as main's registry lstat fault did. The
        # recheck seams are installed only while _orch_walk_recheck runs, so the walk itself is unfaulted:
        # a realpath fault, an open fault on a textual chain path, and an fstat fault each deny naming the
        # recheck step that failed.
        fcb = tmp / "fail-closed"
        (fcb / "cwd" / "sub").mkdir(parents=True)
        (fcb / "top-file").write_text("not a directory", encoding="utf-8")
        fcb_cwd = str(fcb / "cwd" / "sub")
        saved_top = aiqt_hooks._recovery_toplevel
        try:
            aiqt_hooks._recovery_toplevel = lambda _cwd: str(fcb / "top-file")
            cv, cw = _cwd_case(fcb_cwd)
        finally:
            aiqt_hooks._recovery_toplevel = saved_top
        check("trunc/union-unopenable-toplevel-detach-denies", (cv, "detaches a child" in cw),
              ("deny", True))
        saved_recheck_fc = aiqt_hooks._orch_walk_recheck
        saved_realpath = aiqt_hooks.os.path.realpath
        saved_open_fc = aiqt_hooks.os.open
        saved_fstat_fc = aiqt_hooks.os.fstat
        _fcb_parent = os.path.realpath(str(fcb / "cwd"))

        def _realpath_fault(path, *a, **k):
            raise OSError(40, "Too many levels of symbolic links", path)

        def _open_fault(path, flags, *a, **k):
            if path == _fcb_parent and k.get("dir_fd") is None:
                raise PermissionError(13, "Permission denied", path)
            return saved_open_fc(path, flags, *a, **k)

        def _fstat_fault(fd):
            raise PermissionError(13, "Permission denied")

        def _recheck_under(attr, fake):
            target = aiqt_hooks.os.path if attr == "realpath" else aiqt_hooks.os
            saved = getattr(target, attr)

            def _faulted(cwd, chain):
                setattr(target, attr, fake)
                try:
                    return saved_recheck_fc(cwd, chain)
                finally:
                    setattr(target, attr, saved)
            return _faulted
        # The fault rows sit literally in the for header so the execution-set gate resolves each id.
        for attr, fake, why, row in (("realpath", _realpath_fault,
                                      "could not be re-resolved after the registry walk",
                                      "trunc/walk-recheck-realpath-fault-denies"),
                                     ("open", _open_fault, "recheck cannot re-resolve (PermissionError)",
                                      "trunc/walk-recheck-open-fault-denies"),
                                     ("fstat", _fstat_fault, "recheck cannot examine (PermissionError)",
                                      "trunc/walk-recheck-fstat-fault-denies")):
            try:
                aiqt_hooks._orch_walk_recheck = _recheck_under(attr, fake)
                cv, cw = _cwd_case(fcb_cwd)
            finally:
                aiqt_hooks._orch_walk_recheck = saved_recheck_fc
                aiqt_hooks.os.path.realpath = saved_realpath
                aiqt_hooks.os.open = saved_open_fc
                aiqt_hooks.os.fstat = saved_fstat_fc
            check(row, (cv, why in cw), ("deny", True))
        # The walk's own fault branches, pinned the same way: an open fault on the cwd itself (after its
        # stat and access checks pass) and an fstat fault on an opened ancestor each deny naming the walk
        # step that failed, never reading the chain as registry-free.
        _fcb_parent_ino = os.stat(_fcb_parent).st_ino

        def _walk_open_fault(path, flags, *a, **k):
            if path == fcb_cwd and k.get("dir_fd") is None:
                raise PermissionError(13, "Permission denied", path)
            return saved_open_fc(path, flags, *a, **k)

        def _walk_fstat_fault(fd):
            st = saved_fstat_fc(fd)
            if st.st_ino == _fcb_parent_ino:
                raise PermissionError(13, "Permission denied")
            return st
        for attr, fake, why, row in (("open", _walk_open_fault,
                                      "could not be opened for the registry walk (PermissionError)",
                                      "trunc/walk-cwd-open-fault-denies"),
                                     ("fstat", _walk_fstat_fault,
                                      "ancestor directory this walk cannot examine (PermissionError)",
                                      "trunc/walk-ancestor-fstat-fault-denies")):
            try:
                setattr(aiqt_hooks.os, attr, fake)
                cv, cw = _cwd_case(fcb_cwd)
            finally:
                aiqt_hooks.os.open = saved_open_fc
                aiqt_hooks.os.fstat = saved_fstat_fc
            check(row, (cv, why in cw), ("deny", True))
        # The shared dispatcher fails closed on ANY exception while reading stdin: deeply nested JSON raises
        # RecursionError and a read can raise MemoryError, which the old narrow except let escape as a
        # traceback with exit 1 (non-blocking). A PreToolUse hook now exits 2 naming the exception; a Stop
        # hook keeps its warn-and-exit-0 posture.
        deep = "[" * 200000 + "]" * 200000
        hook_py = str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts" / "aiqt_hooks.py")
        dp = subprocess.run([sys.executable, "-I", "-B", hook_py, "orch_truncation_guard"], input=deep,
                            capture_output=True, text=True, timeout=120)
        check("trunc/dispatch-deep-json-fails-closed", (dp.returncode, "RecursionError" in dp.stderr),
              (2, True))
        ds = subprocess.run([sys.executable, "-I", "-B", hook_py, "orch_stop_guard"], input=deep,
                            capture_output=True, text=True, timeout=120)
        check("trunc/dispatch-deep-json-stop-warns", (ds.returncode, "RecursionError" in ds.stdout),
              (0, True))

        class _MemErrStdin:
            def read(self, *_a):
                raise MemoryError("simulated")
        saved_in, saved_err = sys.stdin, sys.stderr
        cap = __import__("io").StringIO()
        try:
            sys.stdin, sys.stderr = _MemErrStdin(), cap
            try:
                mrc = aiqt_hooks.main(["orch_truncation_guard"])
            except MemoryError:
                mrc = "escaped"
        finally:
            sys.stdin, sys.stderr = saved_in, saved_err
        check("trunc/dispatch-memoryerror-fails-closed", (mrc, "MemoryError" in cap.getvalue()), (2, True))

        # ---------- component 3b: the untracked wait-loop guard (trkasy, deny) ----------
        w = Fixture(tmp, "waitloop")
        # predicate direct checks (three-valued): the four-conjunct truth table.
        clf = aiqt_hooks._orch_bg_poll_loop
        BASE = "while true; do gh pr checks 42; sleep 30; done &"
        check("wl/base-match", clf(BASE), "match")
        # removing each of the four conjuncts individually -> 'none' (no deny).
        check("wl/no-detach-none", clf("while true; do gh pr checks 42; sleep 30; done"), "none")
        check("wl/no-loop-none",   clf("gh pr checks 42; sleep 30 &"), "none")
        check("wl/no-sleep-none",  clf("while true; do gh pr checks 42; done &"), "none")
        check("wl/no-probe-none",  clf("while true; do echo working; sleep 30; done &"), "none")
        # the three DENY fixtures from the brief classify 'match'.
        check("wl/deny-while-gh", clf("while true; do gh pr checks 42; sleep 30; done &"), "match")
        check("wl/deny-until-curl-condition",
              clf("until curl -fsS https://example.invalid/status; do sleep 10; done &"), "match")
        check("wl/deny-for-actions-runs",
              clf("for attempt in 1 2 3; do curl -fsS https://example.invalid/actions/runs; "
                  "sleep 20; done &"), "match")
        # probe via a --watch token (command word neither gh nor curl in the body position).
        check("wl/deny-watch-token",
              clf("while true; do run_check --watch; sleep 5; done &"), "match")
        # GD-137 PR1 round 2: a nested loop is no longer force-attributed to one canonical shape (that was
        # the B2-class false positive). Two raw-unquoted headers -> 'indeterminate', deferring to the
        # truncation guard on the same event (now deny for a detach / allow-note for shell syntax; once an ASK).
        check("wl/nested-indeterminate",
              clf("while outer; do while inner; do gh api x; sleep 1; done; done &"), "indeterminate")
        # GD-137 PR1 round 2: the codex false-positive BLOCKERs the redesign eliminates. A 'match' on any of
        # these would strand a session; reverting the matching fix flips each back to a wrong 'match'.
        # B1a: the bare-& closes a command named 'done' written QUOTED (argv is quote-decoded, so the old
        # code misread it as the terminator); the final unquoted `done` closes the FOREGROUND loop.
        check("wl/b1a-quoted-done-none",
              clf('while false; do gh x; sleep 1; "done" & done'), "none")
        # B1b: `_command_word` basenamed /tmp/done -> done; the raw-unquoted terminator check rejects it.
        check("wl/b1b-path-done-none",
              clf("while false; do gh x; sleep 1; /tmp/done & done"), "none")
        # B2: the detached inner `for` carries only sleep; the gh probe belongs to the FOREGROUND outer loop.
        check("wl/b2-inner-detach-indeterminate",
              clf("while gh pr checks 42; do for n in 1 2; do sleep 1; done & wait; break; done"),
              "indeterminate")
        # conditional reserved words / negation in the loop span (were undisclosed false negatives).
        check("wl/cond-if-then-indeterminate",
              clf("while true; do if gh api x; then :; fi; sleep 1; done &"), "indeterminate")
        check("wl/cond-negation-indeterminate",
              clf("while ! gh pr checks 42; do sleep 5; done &"), "indeterminate")
        # brace grouping masks the body command word.
        check("wl/brace-group-indeterminate",
              clf("while true; do { gh x; sleep 1; }; done &"), "indeterminate")
        # C-style for (( )) is not subshell grouping, but its '(' separator is caught and now disclosed.
        check("wl/c-style-for-indeterminate",
              clf("for ((i=0;i<3;i++)); do gh x; sleep 1; done &"), "indeterminate")
        # more than one bare-& is not the single canonical shape.
        check("wl/multi-detach-indeterminate",
              clf("sleep 1 & while true; do gh x; sleep 1; done &"), "indeterminate")
        # a redirect on the `done` terminator makes its raw not a bare `done`: 'none', not a wrong match.
        check("wl/done-redirect-none",
              clf("while true; do gh x; sleep 1; done >log &"), "none")
        # run_in_background true with an inner trailing `done &` is still 'match' (predicate ignores rib).
        check("wl/deny-rib-true-inner-detach", clf(BASE), "match")
        # NEGATIVE fixtures (this control): every one is 'none'.
        # each id is an explicit literal so the execution-set reconciler can enumerate it statically.
        for cid, cmd in [
            ("wl/allow-none-foreground", "while true; do gh pr checks 42; sleep 30; done"),  # foreground, no detach
            ("wl/allow-none-bounded-watch", "timeout 180 gh pr checks 42 --watch"),          # bounded foreground watch
            ("wl/allow-none-bare-sleep", "sleep 30 &"),                                       # bare sleep detach, no loop
            ("wl/allow-none-parallel-build", "make -j8 &"),                                   # parallel build detach, no loop/probe
            ("wl/allow-none-quoted-loop-data", "printf '%s\\n' 'while true; do curl x; sleep 1; done &'"),  # loop text is quoted data
            ("wl/allow-none-no-sleep-probe", "while read -r line; do echo \"$line\"; done < input.txt &"),  # loop, but no sleep/probe
            ("wl/allow-none-logical-and", "git add . && git commit -m x"),                    # && is not a detach
            ("wl/allow-none-redirects", "cmd > log 2>&1"),                                    # redirects, no bare &
            ("wl/allow-none-pipe-amp", "a |& b"),                                             # |& is not a detach
        ]:
            check(cid, clf(cmd), "none")
        # indeterminate cases -> 'indeterminate' here (never this deny), and still reach the truncation
        # guard's decision on the same event (the degrade path); for a foreground bare-& detach that
        # decision is now DENY-and-educate (historically an ASK).
        HEREDOC = "cat <<EOF > poll.sh\nwhile true; do gh pr checks 42; sleep 30; done &\nEOF\n"
        UNBAL = "while true; do gh pr checks 42; sleep 30; done ' &"     # unbalanced quote before the &
        GROUPED = "( while true; do gh pr checks 42; sleep 30; done ) &"  # subshell-grouped detach
        for cid, cmd in [("wl/indeterminate-heredoc", HEREDOC),
                         ("wl/indeterminate-unbalanced", UNBAL),
                         ("wl/indeterminate-grouped", GROUPED)]:
            check(cid, clf(cmd), "indeterminate")
        # degrade path: this guard emits nothing (allow) while the truncation guard DENIES-and-educates the
        # foreground bare-& detach on the same event (historically an ASK).
        wl = lambda cmd, rib=False: aiqt_hooks.orch_untracked_wait_loop(
            w.payload("PreToolUse", "Bash", {"command": cmd, "run_in_background": rib}))
        tg = lambda cmd, rib=False: aiqt_hooks.orch_truncation_guard(
            w.payload("PreToolUse", "Bash", {"command": cmd, "run_in_background": rib}))
        for guard_cid, trunc_cid, cmd in [
                ("wl/degrade-unbalanced-guard-allows", "wl/degrade-unbalanced-trunc-denies", UNBAL),
                ("wl/degrade-grouped-guard-allows", "wl/degrade-grouped-trunc-denies", GROUPED)]:
            check(guard_cid, _verdict(wl(cmd)), "allow")
            check(trunc_cid, _verdict(tg(cmd)), "deny")
        # handler DENY: the three brief DENY fixtures, plus the first with run_in_background:true.
        check("wl/handler-deny-while", _verdict(wl("while true; do gh pr checks 42; sleep 30; done &")),
              "deny")
        check("wl/handler-deny-until",
              _verdict(wl("until curl -fsS https://example.invalid/status; do sleep 10; done &")), "deny")
        check("wl/handler-deny-for",
              _verdict(wl("for attempt in 1 2 3; do curl -fsS https://example.invalid/actions/runs; "
                         "sleep 20; done &")), "deny")
        check("wl/handler-deny-rib-true",
              _verdict(wl("while true; do gh pr checks 42; sleep 30; done &", rib=True)), "deny")
        # handler ALLOW: a foreground poll loop (no detach) and a bounded watch acquire no deny here.
        check("wl/handler-allow-foreground",
              _verdict(wl("while true; do gh pr checks 42; sleep 30; done")), "allow")
        check("wl/handler-allow-watch", _verdict(wl("timeout 180 gh pr checks 42 --watch")), "allow")
        # handler ALLOW on the eliminated false positives: predicate 'none'/'indeterminate' -> emit nothing.
        check("wl/handler-allow-b1a",
              _verdict(wl('while false; do gh x; sleep 1; "done" & done')), "allow")
        check("wl/handler-allow-b1b",
              _verdict(wl("while false; do gh x; sleep 1; /tmp/done & done")), "allow")
        check("wl/handler-allow-b2",
              _verdict(wl("while gh pr checks 42; do for n in 1 2; do sleep 1; done & wait; break; done")),
              "allow")
        check("wl/handler-allow-nested",
              _verdict(wl("while outer; do while inner; do gh api x; sleep 1; done; done &")), "allow")
        # fail-open: non-Bash tool, non-str command, absent registry -> silent allow even on a match command.
        check("wl/failopen-nonbash", _verdict(aiqt_hooks.orch_untracked_wait_loop(
            w.payload("PreToolUse", "Write", {"command": BASE}))), "allow")
        check("wl/failopen-nonstr", _verdict(aiqt_hooks.orch_untracked_wait_loop(
            w.payload("PreToolUse", "Bash", {"command": 42}))), "allow")
        wi = Fixture(tmp, "waitloop-inert")
        (wi.root / ".aiqt" / "orchestration.local.json").unlink()
        check("wl/failopen-no-registry", _verdict(aiqt_hooks.orch_untracked_wait_loop(
            wi.payload("PreToolUse", "Bash", {"command": BASE}))), "allow")

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
        # GD-158 QA round-2 (expbnd async-by-id form): a TaskOutput with NO task_id is SURFACED as unbound
        # via a non-blocking systemMessage, never silently accepted or correlated to a dispatch by recency.
        # Without the fix the handler returns a bare allow (no systemMessage) and this check fails.
        _unb = aiqt_hooks.orch_dispatch_ledger(t.payload("PostToolUse", "TaskOutput", {}))
        _unb_msg = _unb[1].get("systemMessage", "") if isinstance(_unb, tuple) \
            and len(_unb) > 1 and isinstance(_unb[1], dict) else ""
        check("ledger/unbound-taskoutput-surfaced", "unbound" in _unb_msg.lower(), True)

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
            subprocess.run([sys.executable, "-I", "-B", regtool, "append",
                            "--register", str(mr_reg),
                            "--id", rid, "--mistake", "premature wind-down claim",
                            "--evidence", "resume-audit finding", "--rule", "cntdef",
                            "--guardrail", "stop-guard hardening", "--klass", "systemic-lapse",
                            "--check-ref", check_ref],
                           check=True, capture_output=True, timeout=30)

        mr_append("MR-1", "seed.txt")
        check("lapse/klass-recorded",
              '"class": "systemic-lapse"' in mr_reg.read_text(encoding="utf-8"), True)
        proj = subprocess.run([sys.executable, "-I", "-B", regtool, "project",
                               "--register", str(mr_reg)],
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
        aiqt_hooks._orch_dirfd_has_registry = saved_probe
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
          "row; the truncation guard allows (silently) a plain metacharacter-free background command and "
          "ALLOWS-WITH-NOTE (reducer 'warn') any other shell syntax or reserved word, DENIES-and-educates a "
          "background dispatch that pipes a producer into a truncating sink (head/tail, which discards the "
          "producer's full output and exit status) and a "
          "foreground bare-& detach (historically an ASK for both) while dropping a word-start `#` comment, "
          "reading a here-document body under a simple delimiter word as DATA (a safe body '&' or apostrophe "
          "allows; an unquoted delimiter's command/backtick substitution spans are still scanned, one "
          "whose end the scan cannot read exactly denying as cannot-evaluate, and a "
          "double-quoted command substitution holding a here-document has its inner text scanned as code; "
          "an unquoted body holding a backslash and arithmetic '&' are scanned as code and denied, and a "
          "here-document under any other delimiter word or after an untracked construct denies as "
          "cannot-evaluate, disclosed over-refusals; an unterminated here-document denies with its own "
          "reason), denying a double-quoted command substitution it cannot close, and failing a "
          "scan that ends inside an "
          "open quote toward a deny with its own reason (a quote the scan misreads in mid-string, such as "
          "an ANSI-C escaped quote, can still shift it into a disclosed silent allow), reads a '#' comment "
          "by bash's word-start rule as well, denies every in-scope call when the opt-in registry-required "
          "mode is set and no registry is found, and fails "
          "closed on a missing or unreadable tool_name, an unreadable cwd or one whose registry walk cannot "
          "be carried out (scope is the ancestor walk with its concurrent-move recheck, unioned with "
          "a git-resolved toplevel; a git failure alone never denies), a malformed tool_input, "
          "run_in_background, or command, and on any stdin the dispatcher cannot parse; the ledger "
          "records launches "
          "and completions; the resume "
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
