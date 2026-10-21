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
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_orch_hooks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import base64
import json
import os
import stat
import subprocess
import tempfile
import shutil
import threading
import datetime
import hashlib
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: selftest_orch_hooks.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402
from _git_fixture_env import scrub_git_environment  # noqa: E402
import _selftest_exit_report  # noqa: E402

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


def _fg(command):
    return {"command": command, "run_in_background": False}


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


AUTH_STUB = """#!/usr/bin/env python3
import json
import sys
import time
ctl = json.load(open(sys.argv[1]))
time.sleep(ctl.get("sleep", 0))
sys.stdout.write(ctl.get("out", ""))
sys.exit(ctl.get("exit", 0))
"""


def _rdp_git(root, *args):
    """Run a fixture git command in root with a fixed identity; returns stdout text."""
    p = subprocess.run(["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"] + list(args),
                       check=True, capture_output=True, text=True, timeout=30)
    return p.stdout.strip()


class RdpFixture:
    """One review dispatch pin fixture: a repo whose seed commit holds seed.txt, src/a.py and old.txt and
    whose change commit (self.pin) modifies src/a.py, adds src/b.py and deletes old.txt; a registry with a
    review_dispatch binding (or none); and an authority stub that prints the pin unless told otherwise."""
    CHANGED = ("old.txt", "src/a.py", "src/b.py")

    def __init__(self, base, name, binding=True):
        self.root = base / name
        self.briefs = base / (name + "-briefs")
        self.briefs.mkdir(parents=True)
        (self.root / "src").mkdir(parents=True)
        _rdp_git(base, "init", "-q", "-b", "main", str(self.root))
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        (self.root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "old.txt").write_text("old\n", encoding="utf-8")
        _rdp_git(self.root, "add", "-A")
        _rdp_git(self.root, "commit", "-q", "-m", "seed")
        self.seed = _rdp_git(self.root, "rev-parse", "HEAD")
        (self.root / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
        (self.root / "old.txt").unlink()
        _rdp_git(self.root, "add", "-A")
        _rdp_git(self.root, "commit", "-q", "-m", "change")
        self.pin = _rdp_git(self.root, "rev-parse", "HEAD")
        self.ctl = self.briefs / "authority.json"
        self.stub = self.briefs / "authority-stub.py"
        self.stub.write_text(AUTH_STUB, encoding="utf-8")
        self.authority(self.pin + "\n")
        self.binding = dict(
            commands=["orch-dispatch"], brief_option="--brief",
            labels=dict(target="Review-target:", revision="Reviewed-revision:", path="Review-path:",
                        repo="Review-repo:", branch="Review-branch:"),
            authority=dict(argv=[sys.executable, "-I", "-B", str(self.stub), str(self.ctl)], timeout=5),
            max_brief_bytes=4096)
        (self.root / ".aiqt").mkdir()
        self.write_registry(self.binding if binding else None)

    def write_registry(self, binding):
        reg = dict(version=1, state_dir=str(self.briefs / "state"))
        if binding is not None:
            reg["review_dispatch"] = binding
        (self.root / ".aiqt" / "orchestration.local.json").write_text(json.dumps(reg), encoding="utf-8")

    def rebind(self, binding):
        """An operator's change of the binding: write the registry and clear the hook's binding record, so
        the next check records the new binding instead of withholding the dispatch as a changed one."""
        self.write_registry(binding)
        shutil.rmtree(self.root / ".git" / "aiqt" / "review-dispatch-binding", ignore_errors=True)

    def ignore_registry(self, patterns=(".aiqt/orchestration.local.json", ".aiqt/orchestration.json")):
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("".join(p + "\n" for p in patterns), encoding="utf-8")

    def authority(self, out, code=0, sleep=0):
        self.ctl.write_text(json.dumps(dict(out=out, exit=code, sleep=sleep)), encoding="utf-8")

    def brief(self, lines, name="brief.txt"):
        path = self.briefs / name
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return str(path)

    def good(self, pin=None, paths=None, extra=()):
        lines = ["Review the change.", "Review-target: revision", "Reviewed-revision: " + (pin or self.pin)]
        lines += ["Review-path: " + p for p in (self.CHANGED if paths is None else paths)]
        return self.brief(lines + list(extra))

    def run(self, command, background=False):
        return aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(self.root), session_id="s1", tool_name="Bash",
            tool_input=dict(command=command, run_in_background=background)))

    def dispatch(self, brief, background=False):
        return self.run("orch-dispatch --brief " + brief, background)


def _rdp_kind(result):
    """_verdict, with a deny whose reason carries the UNVERIFIABLE: prefix reported as unverifiable."""
    verdict = _verdict(result)
    if verdict == "deny":
        reason = result[1]["hookSpecificOutput"].get("permissionDecisionReason", "")
        if reason.startswith("UNVERIFIABLE: "):
            return "unverifiable"
    return verdict


def _rdp_cases(tmp):
    """The review dispatch pin vectors (rdp/*). Each fixture is a throwaway repo under tmp."""
    base = tmp / "rdp"
    base.mkdir()
    bare = base / "bare"
    _rdp_git(base, "init", "-q", "-b", "main", str(bare))
    check("rdp/registry-absent-inert", _verdict(aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(bare), tool_name="Bash",
        tool_input=dict(command="orch-dispatch --brief /nonexistent")))), "allow")
    nb = RdpFixture(base, "nobinding", binding=False)
    check("rdp/no-binding-inert", _verdict(nb.dispatch(nb.brief(["Review-branch: main"]))), "allow")

    f = RdpFixture(base, "main")
    branch_only = f.brief(["Review-target: revision", "Review-branch: main"], "branch-only.txt")
    check("rdp/reconciled-allows", _rdp_kind(f.dispatch(f.good())), "allow")
    # An undeclared command off rule 4's allowlist is not plain, so a bound session withholds it.
    check("rdp/undeclared-command-not-plain-unverifiable",
          _rdp_kind(f.run("other-dispatch --brief " + branch_only)), "unverifiable")
    check("rdp/missing-target-denies", _rdp_kind(f.dispatch(f.brief(["Reviewed-revision: " + f.pin]))), "deny")
    check("rdp/unknown-target-denies", _rdp_kind(f.dispatch(f.brief(["Review-target: tip"]))), "deny")
    check("rdp/duplicate-target-unverifiable", _rdp_kind(f.dispatch(f.brief(
        ["Review-target: revision", "Review-target: working-tree"]))), "unverifiable")
    events = f.briefs / "state" / "guard-events.jsonl"

    def _event_rows():
        try:
            return [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line]
        except OSError:
            return []
    rows_before = len(_event_rows())
    wt_kind = _rdp_kind(f.dispatch(f.brief(["Review-target: working-tree"])))
    new_rows = _event_rows()[rows_before:]
    check("rdp/working-tree-target-notes", (wt_kind, [(r.get("kind"), r.get("decision")) for r in new_rows]),
          ("warn", [("review-dispatch-pin", "allow-declared-target")]))
    check("rdp/branch-only-brief-denies", _rdp_kind(f.dispatch(branch_only)), "deny")
    check("rdp/short-sha-denies", _rdp_kind(f.dispatch(f.good(pin=f.pin[:12]))), "deny")
    check("rdp/ref-name-as-pin-denies", [_rdp_kind(f.dispatch(f.good(pin=p))) for p in ("HEAD", "main")],
          ["deny", "deny"])
    check("rdp/wrong-length-for-format-denies", _rdp_kind(f.dispatch(f.good(pin=f.pin + "0" * 24))), "deny")
    check("rdp/uppercase-pin-denies", _rdp_kind(f.dispatch(f.good(pin=f.pin.upper()))), "deny")
    check("rdp/duplicate-pin-unverifiable", _rdp_kind(f.dispatch(f.good(
        extra=["Reviewed-revision: " + f.pin]))), "unverifiable")
    check("rdp/absent-object-pin-unverifiable", _rdp_kind(f.dispatch(f.good(pin="1" * 40))), "unverifiable")
    _rdp_git(f.root, "tag", "-a", "-m", "t", "rv1", f.pin)
    tag_oid = _rdp_git(f.root, "rev-parse", "rv1")
    check("rdp/tag-object-pin-unverifiable", (tag_oid != f.pin, _rdp_kind(f.dispatch(f.good(pin=tag_oid)))),
          (True, "unverifiable"))
    f.authority(f.seed + "\n")
    check("rdp/authority-mismatch-denies", _rdp_kind(f.dispatch(f.good())), "deny")
    f.authority(f.pin + "\n", code=3)
    check("rdp/authority-error-unverifiable/exit", _rdp_kind(f.dispatch(f.good())), "unverifiable")
    f.authority(f.pin + "\n" + f.pin + "\n")
    check("rdp/authority-error-unverifiable/two-lines", _rdp_kind(f.dispatch(f.good())), "unverifiable")
    f.authority(f.pin[:12] + "\n")
    check("rdp/authority-error-unverifiable/short", _rdp_kind(f.dispatch(f.good())), "unverifiable")
    f.authority(f.pin + "\n", sleep=3)
    f.rebind(dict(f.binding, authority=dict(f.binding["authority"], timeout=1)))
    check("rdp/authority-error-unverifiable/timeout", _rdp_kind(f.dispatch(f.good())), "unverifiable")
    f.rebind(f.binding)
    f.authority(f.pin + "\n")
    check("rdp/declared-path-not-in-commit-denies", _rdp_kind(f.dispatch(f.good(
        paths=f.CHANGED + ("src/c.py",)))), "deny")
    check("rdp/committed-path-undeclared-denies", _rdp_kind(f.dispatch(f.good(paths=("src/a.py", "src/b.py")))),
          "deny")
    em = RdpFixture(base, "emptycommit")
    _rdp_git(em.root, "commit", "-q", "--allow-empty", "-m", "empty")
    empty_pin = _rdp_git(em.root, "rev-parse", "HEAD")
    em.authority(empty_pin + "\n")
    # A clean clone holds no untracked registry, so only the declared-paths check refuses the empty set there.
    em_clone = base / "emptycommit-clone"
    _rdp_git(base, "clone", "-q", str(em.root), str(em_clone))
    check("rdp/no-declared-paths-denies", [_rdp_kind(f.dispatch(f.good(paths=()))), _rdp_kind(em.dispatch(
        em.good(pin=empty_pin, paths=(), extra=["Review-repo: " + str(em_clone)])))], ["deny", "deny"])
    (f.root / "seed.txt").write_text("unrelated edit\n", encoding="utf-8")
    check("rdp/unrelated-dirty-path-allows", _rdp_kind(f.dispatch(f.good())), "allow")
    _rdp_git(f.root, "checkout", "-q", "--", "seed.txt")
    good = f.good()
    check("rdp/stdin-heredoc-brief-denies", [_rdp_kind(f.run(c)) for c in (
        "orch-dispatch --brief " + good + " <<'EOF'\nReview-target: working-tree\nEOF",
        "orch-dispatch --brief - <<EOF\nReview-target: working-tree\nEOF",
        "cat " + good + " | orch-dispatch --brief " + good,
        "orch-dispatch --brief " + good + " < " + good,
        "orch-dispatch",
        "orch-dispatch --brief " + good + " --brief=" + good)],
        ["unverifiable", "unverifiable", "unverifiable", "unverifiable", "deny", "deny"])
    # A file literally named $BRIEF exists, so only the expansion check stops the literal read of it.
    (f.root / "$BRIEF").write_text("Review-target: working-tree\n", encoding="utf-8")
    check("rdp/opaque-brief-arg-unverifiable", _rdp_kind(f.run("orch-dispatch --brief $BRIEF")), "unverifiable")
    (f.root / "$BRIEF").unlink()
    rel = os.path.relpath(f.good(), str(f.root))
    check("rdp/relative-brief-resolves-against-cwd", _rdp_kind(f.run("orch-dispatch --brief " + rel)), "allow")
    check("rdp/cd-then-relative-brief-unverifiable",
          _rdp_kind(f.run("cd " + str(f.root) + " && orch-dispatch --brief " + rel)), "unverifiable")
    check("rdp/wrapper-prefixed-dispatch-recognized", [_rdp_kind(f.run(c + branch_only)) for c in (
        "env FOO=1 nice -n 5 orch-dispatch --brief ", "command /usr/local/bin/orch-dispatch --brief=",
        "true && nohup orch-dispatch --brief ")], ["unverifiable", "unverifiable", "unverifiable"])
    import signal as _signal

    class _Blocked(BaseException):
        """Raised by the alarm; not an OSError, so no handler under test can swallow it."""

    def _alarm(_sig, _frame):
        raise _Blocked()

    def _bounded(fn):
        old = _signal.signal(_signal.SIGALRM, _alarm)
        _signal.alarm(5)
        try:
            return fn()
        except _Blocked:
            return "blocked"
        finally:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, old)
    oversize = f.briefs / "oversize.txt"
    oversize.write_text("Review-target: working-tree\n" + "x" * 5000 + "\n", encoding="utf-8")
    bad_utf8 = f.briefs / "bad-utf8.txt"
    bad_utf8.write_bytes(b"Review-target: working-tree\n\xff\n")
    nul = f.briefs / "nul.txt"
    nul.write_bytes(b"Review-target: working-tree\n\x00\n")
    sep = f.briefs / "sep.txt"
    sep.write_text("Note: see below\u2028Review-target: working-tree\n", encoding="utf-8")
    unreadable = f.briefs / "unreadable.txt"
    unreadable.write_text("Review-target: working-tree\n", encoding="utf-8")
    unreadable.chmod(0)
    fifo = f.briefs / "fifo.txt"
    os.mkfifo(str(fifo))
    got = [_bounded(lambda p=p: _rdp_kind(f.dispatch(str(p)))) for p in (
        f.briefs / "missing.txt", f.briefs, fifo, oversize, bad_utf8, nul, sep)]
    # A privileged run reads a mode-0 file anyway, so that one leg is judged only where it can bite.
    got.append(_rdp_kind(f.dispatch(str(unreadable))) if os.geteuid() != 0 else "unverifiable")
    unreadable.chmod(0o644)
    check("rdp/unreadable-brief-unverifiable", got, ["unverifiable"] * 8)

    w = RdpFixture(base, "wtedit")
    (w.root / "src" / "a.py").write_text("a = 3  # the real fix, never committed\n", encoding="utf-8")
    check("rdp/pre-change-tip-worktree-edit-denies", _rdp_kind(w.dispatch(w.good())), "deny")
    st = RdpFixture(base, "staged")
    (st.root / "src" / "b.py").write_text("b = 2\n", encoding="utf-8")
    _rdp_git(st.root, "add", "src/b.py")
    # The working tree goes back to the pinned content, so only the staged-state check sees the change.
    (st.root / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
    check("rdp/staged-only-change-denies", _rdp_kind(st.dispatch(st.good())), "deny")
    un = RdpFixture(base, "untracked")
    (un.root / ".git" / "info" / "exclude").write_text("old.txt\n", encoding="utf-8")
    (un.root / "old.txt").write_text("recreated\n", encoding="utf-8")
    check("rdp/untracked-recreates-deleted-denies", _rdp_kind(un.dispatch(un.good())), "deny")

    ro = RdpFixture(base, "rootcommit")
    _rdp_git(ro.root, "checkout", "-q", ro.seed)
    ro.authority(ro.seed + "\n")
    check("rdp/root-commit-empty-tree-allows", _rdp_kind(ro.dispatch(ro.good(
        pin=ro.seed, paths=("old.txt", "seed.txt", "src/a.py")))), "allow")

    mg = RdpFixture(base, "merge")
    _rdp_git(mg.root, "checkout", "-q", "-b", "side", mg.seed)
    (mg.root / "side.txt").write_text("side\n", encoding="utf-8")
    _rdp_git(mg.root, "add", "side.txt")
    _rdp_git(mg.root, "commit", "-q", "-m", "side")
    _rdp_git(mg.root, "checkout", "-q", "main")
    _rdp_git(mg.root, "merge", "-q", "--no-ff", "-m", "merge", "side")
    merge = _rdp_git(mg.root, "rev-parse", "HEAD")
    mg.authority(merge + "\n")
    check("rdp/merge-first-parent-allows", [_rdp_kind(mg.dispatch(mg.good(pin=merge, paths=p))) for p in (
        ("side.txt",), mg.CHANGED)], ["allow", "deny"])

    shallow = base / "shallow"
    _rdp_git(base, "clone", "-q", "--depth", "1", "file://" + str(f.root), str(shallow))
    shallow_result = f.dispatch(f.good(extra=["Review-repo: " + str(shallow)]))
    shallow_reason = shallow_result[1]["hookSpecificOutput"]["permissionDecisionReason"]
    check("rdp/shallow-missing-parent-unverifiable",
          (_rdp_kind(shallow_result), "is not in the repository" in shallow_reason), ("unverifiable", True))
    check("rdp/repo-label-not-toplevel-unverifiable", [_rdp_kind(f.dispatch(f.good(extra=["Review-repo: " + r])))
                                                       for r in ("src", str(f.root / "src"))],
          ["unverifiable", "unverifiable"])

    rp = RdpFixture(base, "replace")
    _rdp_git(rp.root, "replace", "--graft", rp.pin)
    rp_parent = aiqt_hooks._review_git(str(rp.root), "rev-parse", "--verify", rp.pin + "^")
    check("rdp/replace-ref-ignored", ("parent " in _rdp_git(rp.root, "cat-file", "commit", rp.pin),
                                      _rdp_kind(rp.dispatch(rp.good())),
                                      rp_parent.stdout.decode().strip() if rp_parent else None),
          (False, "allow", rp.seed))
    gf = RdpFixture(base, "grafts")
    (gf.root / ".git" / "info" / "grafts").write_text(gf.pin + "\n", encoding="utf-8")
    gf_parent = aiqt_hooks._review_git(str(gf.root), "rev-parse", "--verify", gf.pin + "^")
    check("rdp/grafts-file-ignored", (_rdp_git(gf.root, "log", "-1", "--format=%P", gf.pin),
                                      _rdp_kind(gf.dispatch(gf.good())),
                                      gf_parent.stdout.decode().strip() if gf_parent else None),
          ("", "allow", gf.seed))

    seen = []
    real_run = subprocess.run

    def _capture(argv, **kw):
        seen.append((list(argv), dict(kw.get("env") or {})))
        return real_run(argv, **kw)
    aiqt_hooks.subprocess.run = _capture
    try:
        aiqt_hooks._review_git(str(f.root), "rev-parse", "HEAD")
    finally:
        aiqt_hooks.subprocess.run = real_run
    argv, env = seen[0] if seen else ([], {})
    check("rdp/git-env-neutralized", (
        env.get("GIT_OPTIONAL_LOCKS"), env.get("GIT_NO_REPLACE_OBJECTS"), env.get("GIT_GRAFT_FILE"),
        env.get("GIT_LITERAL_PATHSPECS"), argv[:9]),
        ("0", "1", os.devnull, "1", ["git", "-C", str(f.root), "-c", "core.commitGraph=false",
                                     "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]))

    ps = RdpFixture(base, "pathspec")
    for name in ("*", ":(glob)*.py"):
        (ps.root / name).write_text("literal\n", encoding="utf-8")
        _rdp_git(ps.root, "add", "--", ":(literal)" + name)
    _rdp_git(ps.root, "commit", "-q", "-m", "odd names")
    odd = _rdp_git(ps.root, "rev-parse", "HEAD")
    ps.authority(odd + "\n")
    (ps.root / "src" / "a.py").write_text("a = 9\n", encoding="utf-8")
    check("rdp/pathspec-magic-literal", _rdp_kind(ps.dispatch(ps.good(pin=odd, paths=("*", ":(glob)*.py")))),
          "allow")

    bm = RdpFixture(base, "branchmoved")
    (bm.root / "seed.txt").write_text("later\n", encoding="utf-8")
    _rdp_git(bm.root, "commit", "-q", "-am", "later")
    check("rdp/branch-label-moved-notes", [_rdp_kind(bm.dispatch(bm.good(extra=["Review-branch: " + b])))
                                           for b in ("main", "no-such-branch")], ["warn", "warn"])

    mb = RdpFixture(base, "malformed")
    mb.write_registry(dict(mb.binding, surplus=1))
    check("rdp/malformed-binding-withholds-every-call", [
        _rdp_kind(mb.run("ls", background=True)), _rdp_kind(mb.run("ls"))], ["unverifiable", "unverifiable"])
    mb.write_registry(dict(mb.binding, labels=dict(mb.binding["labels"], path="Review-target:")))
    check("rdp/duplicate-labels-malformed", _rdp_kind(mb.run("ls", background=True)), "unverifiable")
    (mb.root / ".aiqt" / "orchestration.local.json").write_text("{not json", encoding="utf-8")
    # A registry that cannot be parsed cannot say which commands dispatch: a foreground dispatch is withheld
    # as well as a background one.
    check("rdp/bad-registry-withholds-every-call", [
        _rdp_kind(mb.run("ls", background=True)), _rdp_kind(mb.run("ls")),
        _rdp_kind(mb.run("orch-dispatch --brief /nonexistent"))], ["unverifiable"] * 3)

    # ---------- fail-closed discovery, workdir, descriptors, content, isolation ----------
    dc = RdpFixture(base, "discovery")
    good_dc = dc.good()
    with open(str(dc.root / ".git" / "config"), "a", encoding="utf-8") as fh:
        fh.write("[broken\n")
    gone = dc.root / "gone"

    def _why(result):
        return (_rdp_kind(result), "cannot resolve the session repository" in result[1]["hookSpecificOutput"].get(
            "permissionDecisionReason", "") if result[1] else False)
    check("rdp/discovery-failure-unverifiable", [
        _why(dc.dispatch(good_dc)), _why(aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(gone), tool_name="Bash",
            tool_input=dict(command="orch-dispatch --brief " + good_dc)))),
        _rdp_kind(dc.run("ls"))], [("unverifiable", True), ("unverifiable", True), "allow"])
    ec = RdpFixture(base, "envchdir")
    (ec.root / "brief.txt").write_text(open(ec.good(), encoding="utf-8").read(), encoding="utf-8")
    (ec.root / "src" / "brief.txt").write_text("Review-target: revision\nReview-branch: main\n",
                                                encoding="utf-8")
    # Only a provably plain dispatch is checked: every wrapper (env and its -C and -S forms, as GNU env and
    # other env builds read them, timeout, nohup, sudo, xargs, setsid, ionice) is withheld, so no wrapper
    # grammar decides which brief is read or where.
    check("rdp/wrapped-dispatch-unverifiable", [_rdp_kind(ec.run(c)) for c in (
        "orch-dispatch --brief brief.txt", "env -C src orch-dispatch --brief brief.txt",
        "env --chdir=src orch-dispatch --brief brief.txt", "env -C src -C .. orch-dispatch --brief brief.txt",
        "env -S '-C src' orch-dispatch --brief brief.txt", "env -Sorch-dispatch --brief src/brief.txt",
        "env A=1 -S'orch-dispatch --brief src/brief.txt'", "env -S'A=1' -S'orch-dispatch --brief src/brief.txt'",
        "env -S'orch-dispatch --brief brief.txt' -S'--brief src/brief.txt'",
        "env -S$'orch-dispatch\\t--brief\\tsrc/brief.txt'", "sudo env -Sorch-dispatch --brief src/brief.txt",
        "timeout 600 orch-dispatch --brief brief.txt", "setsid orch-dispatch --brief brief.txt",
        "sudo -u me orch-dispatch --brief brief.txt", "xargs orch-dispatch --brief brief.txt",
        "ionice -c3 orch-dispatch --brief brief.txt", "bash -c 'orch-dispatch --brief src/brief.txt'")],
        ["allow"] + ["unverifiable"] * 16)
    # Every command that is not plain is withheld, whatever it names (an undeclared command off the
    # allowlist too); a plain one that names no declared command is allowed.
    check("rdp/non-plain-command-unverifiable", [_rdp_kind(ec.run(c)) for c in (
        "timeout --frobnicate 5 ls", "env -S'ls -l' /tmp", "ls -la && git log --oneline | head -3",
        "echo $HOME > /dev/null", "other-dispatch --brief src/brief.txt", "ls -la src")],
        ["unverifiable"] * 5 + ["allow"])
    check("rdp/cd-then-absolute-brief-unverifiable",
          _rdp_kind(f.run("cd " + str(f.root / "src") + " && orch-dispatch --brief " + f.good())), "unverifiable")
    good = f.good()
    check("rdp/abbreviated-second-brief-denies", [_rdp_kind(f.run("orch-dispatch --brief " + good + c)) for c in (
        " --brie " + branch_only, " --bri=" + branch_only)], ["deny", "deny"])
    # An abbreviation BEFORE the brief, where the last-argument rule cannot catch it.
    check("rdp/abbreviated-brief-before-last-denies", _rdp_kind(f.run(
        "orch-dispatch --brie " + branch_only + " --brief " + good)), "deny")
    check("rdp/unrelated-descriptor-redirect", [_rdp_kind(f.run("orch-dispatch --brief " + good + c)) for c in (
        " 3</dev/null", " 0<" + good, " <&3")], ["unverifiable", "unverifiable", "unverifiable"])

    ff = RdpFixture(base, "fifos")
    fifo_brief = ff.briefs / "fifo-brief.txt"
    os.mkfifo(str(fifo_brief))
    link_brief = ff.briefs / "link-brief.txt"
    link_brief.symlink_to(ff.good())
    check("rdp/fifo-or-symlink-brief-unverifiable-without-blocking", [
        _bounded(lambda: _rdp_kind(ff.dispatch(str(fifo_brief)))),
        _bounded(lambda: _rdp_kind(ff.dispatch(str(link_brief))))], ["unverifiable", "unverifiable"])
    reg = ff.root / ".aiqt" / "orchestration.local.json"
    reg_text = reg.read_text(encoding="utf-8")
    reg.unlink()
    os.mkfifo(str(reg))
    fifo_reg = _bounded(lambda: _rdp_kind(ff.dispatch(ff.good(), background=True)))
    reg.unlink()
    (ff.briefs / "registry.json").write_text(reg_text, encoding="utf-8")
    reg.symlink_to(ff.briefs / "registry.json")
    check("rdp/fifo-or-symlink-registry-unverifiable-without-blocking", [
        fifo_reg, _bounded(lambda: _rdp_kind(ff.dispatch(ff.good(), background=True)))],
        ["unverifiable", "unverifiable"])
    ix = RdpFixture(base, "indexflags")
    (ix.root / "src" / "a.py").write_text("a = 3  # uncommitted\n", encoding="utf-8")
    _rdp_git(ix.root, "update-index", "--assume-unchanged", "src/a.py")
    got = [_rdp_kind(ix.dispatch(ix.good()))]
    _rdp_git(ix.root, "update-index", "--no-assume-unchanged", "src/a.py")
    _rdp_git(ix.root, "checkout", "-q", "--", "src/a.py")
    (ix.root / "src" / "b.py").write_text("b = 9\n", encoding="utf-8")
    _rdp_git(ix.root, "update-index", "--skip-worktree", "src/b.py")
    got.append(_rdp_kind(ix.dispatch(ix.good())))
    _rdp_git(ix.root, "update-index", "--no-skip-worktree", "src/b.py")
    _rdp_git(ix.root, "checkout", "-q", "--", "src/b.py")
    _rdp_git(ix.root, "config", "core.ignoreStat", "true")
    _rdp_git(ix.root, "update-index", "--really-refresh")
    (ix.root / "src" / "a.py").write_text("a = 777\n", encoding="utf-8")
    got.append(_rdp_kind(ix.dispatch(ix.good())))
    _rdp_git(ix.root, "checkout", "-q", "--", "src/a.py")
    _rdp_git(ix.root, "config", "--unset", "core.ignoreStat")
    (ix.root / "src" / "a.py").chmod(0o755)
    got.append(_rdp_kind(ix.dispatch(ix.good())))
    (ix.root / "src" / "a.py").chmod(0o644)
    got.append(_rdp_kind(ix.dispatch(ix.good())))
    check("rdp/index-flags-cannot-hide-worktree-edit", got, ["deny", "deny", "deny", "deny", "allow"])

    hostile = {"GIT_DIR": str(bare / ".git"), "GIT_WORK_TREE": str(bare), "GIT_INDEX_FILE": str(bare / "nope")}
    saved_env = {k: os.environ.get(k) for k in hostile}
    os.environ.update(hostile)
    try:
        # The probes are scrubbed and still read f.root; the dispatch, which inherits the variables, is
        # withheld, since its verifier would read the repository they name.
        hostile_kind = (_rdp_kind(f.dispatch(f.good())), aiqt_hooks._review_git(
            str(f.root), "rev-parse", "HEAD").stdout.decode().strip() == f.pin)
        graft_parent = aiqt_hooks._review_git(str(gf.root), "rev-parse", "--verify", gf.pin + "^")
        replace_parent = aiqt_hooks._review_git(str(rp.root), "rev-parse", "--verify", rp.pin + "^")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("rdp/hostile-git-env-scrubbed-and-dispatch-withheld", hostile_kind, ("unverifiable", True))
    # Each ambient variable on its own withholds the dispatch, named in the reason.
    ambient_names = (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH", "GIT_NAMESPACE", "GIT_REPLACE_REF_BASE",
        "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE")
    ambient_brief = f.good()
    ambient_got = []
    for name in ambient_names:
        saved = os.environ.get(name)
        os.environ[name] = str(bare / "elsewhere")
        try:
            result = f.dispatch(ambient_brief)
        finally:
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved
        reason = result[1]["hookSpecificOutput"].get("permissionDecisionReason", "") if result[1] else ""
        ambient_got.append((name, _rdp_kind(result), "sets " + name + "," in reason))
    check("rdp/each-ambient-git-variable-withholds", ambient_got,
          [(name, "unverifiable", True) for name in ambient_names])
    check("rdp/grafts-and-replace-probe-real-parents", [
        (p.returncode, p.stdout.decode().strip()) if p is not None else None
        for p in (graft_parent, replace_parent)], [(0, gf.seed), (0, rp.seed)])
    fm = RdpFixture(base, "fsmonitor")
    marker = fm.briefs / "fsmonitor-ran"
    hook = fm.briefs / "fsmonitor.sh"
    hook.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _rdp_git(fm.root, "config", "core.fsmonitor", str(hook))
    fm_kind = _rdp_kind(fm.dispatch(fm.good()))
    aiqt_hooks._review_git(str(fm.root), "status", "--porcelain")
    check("rdp/fsmonitor-never-runs", (fm_kind, marker.exists()), ("allow", False))
    saved_budget = aiqt_hooks._RDP_BUDGET
    f.authority(f.pin + "\n", sleep=3)
    aiqt_hooks._RDP_BUDGET = 1.0
    started = aiqt_hooks.time.monotonic()
    try:
        budget_kind = _rdp_kind(f.dispatch(f.good()))
    finally:
        aiqt_hooks._RDP_BUDGET = saved_budget
        f.authority(f.pin + "\n")
    check("rdp/deadline-bounds-the-decision", (budget_kind, aiqt_hooks.time.monotonic() - started < 2.5),
          ("unverifiable", True))
    # Each content read and git probe refuses to start once the deadline has passed: a past deadline is set
    # directly, then each reader is called (a symlink entry exercises the walk without the blob hash).
    (f.root / "rdp-link").symlink_to("seed.txt")
    probes = []
    fd = os.open(str(f.root / "seed.txt"), os.O_RDONLY)
    try:
        for name, call in (
                ("blob", lambda: aiqt_hooks._rdp_blob_id("sha1", 5, fd)),
                ("read", lambda: aiqt_hooks._rdp_read_fd(fd, 10)),
                ("entry", lambda: aiqt_hooks._rdp_worktree_entry(str(f.root), b"rdp-link", "sha1")),
                ("git", lambda: aiqt_hooks._review_git(str(f.root), "rev-parse", "HEAD"))):
            aiqt_hooks._RDP_DEADLINE[0] = aiqt_hooks.time.monotonic() - 1
            try:
                probes.append((name, "returned" if call() is not None else "none"))
            except TimeoutError:
                probes.append((name, "timeout"))
            finally:
                aiqt_hooks._RDP_DEADLINE[0] = None
    finally:
        os.close(fd)
    (f.root / "rdp-link").unlink()
    check("rdp/content-read-past-deadline-refused", probes,
          [("blob", "timeout"), ("read", "timeout"), ("entry", "timeout"), ("git", "none")])
    # The reported reproduction: a declared file's hash starts after the budget is spent (here the deadline
    # is moved into the past instead of sleeping), and the decision must not allow.
    real_blob = aiqt_hooks._rdp_blob_id

    def _late_blob(fmt, header_size, fd):
        aiqt_hooks._RDP_DEADLINE[0] = aiqt_hooks.time.monotonic() - 1
        return real_blob(fmt, header_size, fd)
    aiqt_hooks._rdp_blob_id = _late_blob
    try:
        late = [_rdp_kind(f.dispatch(f.good()))]
    finally:
        aiqt_hooks._rdp_blob_id = real_blob
    # An overrun anywhere else (here after the whole reconciliation returned allow) is a cannot-evaluate too.
    real_reconcile = aiqt_hooks._rdp_reconcile

    def _late_reconcile(*args):
        out = real_reconcile(*args)
        aiqt_hooks._RDP_DEADLINE[0] = aiqt_hooks.time.monotonic() - 1
        return out
    aiqt_hooks._rdp_reconcile = _late_reconcile
    try:
        late.append(_rdp_kind(f.dispatch(f.good())))
    finally:
        aiqt_hooks._rdp_reconcile = real_reconcile
    late.append(_rdp_kind(f.dispatch(f.good())))
    check("rdp/overdue-read-never-allows", late, ["unverifiable", "unverifiable", "allow"])
    f.rebind(dict(f.binding, brief_option="-b"))
    check("rdp/attached-short-second-brief-denies", [_rdp_kind(f.run("orch-dispatch -b " + good + c)) for c in (
        "", " -b" + branch_only)], ["allow", "deny"])
    f.rebind(f.binding)
    # ---------- only a provably plain dispatch is checked; every command that is not plain withholds ----------
    check("rdp/second-brief-alias-or-group-denies", [_rdp_kind(f.run(c)) for c in (
        "orch-dispatch --brief " + good + " -b " + branch_only, "orch-dispatch --brief " + good + " extra",
        "orch-dispatch -- --brief " + good, "orch-dispatch -b " + branch_only + " --brief " + good)],
        ["deny", "deny", "deny", "allow"])
    f.rebind(dict(f.binding, brief_option="-b"))
    check("rdp/grouped-short-second-brief-denies", [_rdp_kind(f.run("orch-dispatch -b " + good + c)) for c in (
        " -xb " + branch_only, " -xb" + branch_only, "")], ["deny", "deny", "allow"])
    f.rebind(f.binding)
    pl = RdpFixture(base, "plain")
    (pl.root / "brief.txt").write_text(open(pl.good(), encoding="utf-8").read(), encoding="utf-8")
    (pl.root / "src" / "brief.txt").write_text("Review-target: revision\nReview-branch: main\n", encoding="utf-8")
    check("rdp/quote-split-or-expanded-name-unverifiable", [_rdp_kind(pl.run(c)) for c in (
        "cat <<EOF >/dev/null\nx\nEOF\norch-dis''patch --brief src/brief.txt",
        "diff <(true) /dev/null; orch-dis\\patch --brief src/brief.txt",
        "{orch-dispatch,} --brief src/brief.txt", "D=orch-dispatch; $D --brief src/brief.txt",
        "$'\\x6frch-dispatch' --brief src/brief.txt", "$'orch\\x2ddispatch' --brief src/brief.txt",
        "$\"orch-dispatch\" --brief src/brief.txt", "\\orch-dispatch --brief src/brief.txt",
        "orch-dispatch --brief brief.txt > out.log", "o{r,}ch-dispatch --brief src/brief.txt")],
        ["unverifiable"] * 10)
    check("rdp/shell-control-before-dispatch-unverifiable", [_rdp_kind(pl.run(c)) for c in (
        "eval cd src; orch-dispatch --brief brief.txt", "pushd src; orch-dispatch --brief brief.txt",
        "if true; then cd src; fi; orch-dispatch --brief brief.txt", ". ./env.sh; orch-dispatch --brief brief.txt",
        "PWD=src orch-dispatch --brief brief.txt", "orch-dispatch --brief brief.txt; orch-dispatch --brief brief.txt")],
        ["unverifiable"] * 6)
    check("rdp/plain-dispatch-forms-allow", [_rdp_kind(pl.run(c)) for c in (
        "orch-dispatch --family x --brief=brief.txt", "orch-dispatch \"--brief\" 'brief.txt'",
        "/usr/local/bin/orch-dispatch --brief brief.txt", "  orch-dispatch  --brief  b'rief'.txt  ")],
        ["allow"] * 4)
    # The shared specification admits ONE simple command: an operator, a redirection (even </dev/null) or a
    # quoted command word makes the dispatch not plain.
    check("rdp/compound-or-redirected-dispatch-unverifiable", [_rdp_kind(pl.run(c)) for c in (
        "orch-dispatch --brief brief.txt </dev/null", "orch-dispatch --brief brief.txt 0</dev/null",
        "'orch-dispatch' --brief brief.txt", "git status && orch-dispatch --brief brief.txt",
        "orch-dispatch --brief brief.txt | tee /dev/null", "orch-dispatch --brief brief.txt &")],
        ["unverifiable"] * 6)
    # A linked worktree, inside the orchestrated tree or beside it, is scoped by the main worktree's registry.
    lw = RdpFixture(base, "linked")
    lw_branch = lw.brief(["Review-target: revision", "Review-branch: main"], "branch-only.txt")
    _rdp_git(lw.root, "worktree", "add", "-q", "--detach", str(lw.root / ".worktrees" / "rev"), lw.pin)
    _rdp_git(lw.root, "worktree", "add", "-q", "--detach", str(base / "linked-beside"), lw.pin)
    check("rdp/linked-worktree-scoped", [_rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(wt), session_id="s1", tool_name="Bash",
        tool_input=dict(command="orch-dispatch --brief " + lw_branch)))) for wt in (
            lw.root / ".worktrees" / "rev", base / "linked-beside")], ["deny", "deny"])
    real_walk = aiqt_hooks._rdp_registry_dir
    aiqt_hooks._rdp_registry_dir = lambda cwd: ("fail", "the ancestor walk failed")
    try:
        walk_fail = [_verdict(aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(bare), session_id="s1", tool_name="Bash",
            tool_input=dict(command="ls", run_in_background=bg)))) for bg in (True, False)]
    finally:
        aiqt_hooks._rdp_registry_dir = real_walk
    check("rdp/ancestor-walk-failure-withholds", walk_fail, ["deny", "deny"])
    # A submodule's .gitmodules `ignore = all` cannot hide its bump from the changed set, and its checkout is
    # compared with the pinned gitlink.
    sb = RdpFixture(base, "submodule")
    (sb.root / ".gitmodules").write_text('[submodule "lib"]\n\tpath = lib\n\turl = ./lib\n\tignore = all\n',
                                          encoding="utf-8")
    _rdp_git(sb.root, "add", ".gitmodules")
    _rdp_git(sb.root, "update-index", "--add", "--cacheinfo", "160000," + sb.seed + ",lib")
    _rdp_git(sb.root, "commit", "-q", "-m", "add lib")
    _rdp_git(sb.root, "update-index", "--cacheinfo", "160000," + sb.pin + ",lib")
    (sb.root / "src" / "a.py").write_text("a = 4\n", encoding="utf-8")
    _rdp_git(sb.root, "add", "src/a.py")
    _rdp_git(sb.root, "commit", "-q", "-m", "bump lib")
    sb_pin = _rdp_git(sb.root, "rev-parse", "HEAD")
    sb.authority(sb_pin + "\n")
    (sb.root / "lib").mkdir()
    sb_got = [_rdp_kind(sb.dispatch(sb.good(pin=sb_pin, paths=p))) for p in (("src/a.py",), ("lib", "src/a.py"))]
    _rdp_git(base, "init", "-q", "-b", "main", str(sb.root / "lib"))
    _rdp_git(sb.root / "lib", "commit", "-q", "--allow-empty", "-m", "other")
    sb_got.append(_rdp_kind(sb.dispatch(sb.good(pin=sb_pin, paths=("lib", "src/a.py")))))
    check("rdp/submodule-ignore-config-neutralized", sb_got, ["deny", "allow", "deny"])
    # A file the pin replaces with a directory is clean when the directory holds the pin's own entries; a
    # directory where the pin has nothing is not.
    fd_ = RdpFixture(base, "filetodir")
    (fd_.root / "seed.txt").unlink()
    (fd_.root / "seed.txt").mkdir()
    (fd_.root / "seed.txt" / "inner.txt").write_text("inner\n", encoding="utf-8")
    _rdp_git(fd_.root, "add", "-A", "--", "seed.txt")
    _rdp_git(fd_.root, "commit", "-q", "-m", "file to dir")
    fd_pin = _rdp_git(fd_.root, "rev-parse", "HEAD")
    fd_.authority(fd_pin + "\n")
    fd_got = [_rdp_kind(fd_.dispatch(fd_.good(pin=fd_pin, paths=("seed.txt", "seed.txt/inner.txt"))))]
    dd = RdpFixture(base, "deleteddir")
    (dd.root / "old.txt").mkdir()
    (dd.root / "old.txt" / "x").write_text("untracked\n", encoding="utf-8")
    fd_got.append(_rdp_kind(dd.dispatch(dd.good())))
    check("rdp/file-replaced-by-directory", fd_got, ["allow", "deny"])
    # A declared symlink is compared by its target.
    sl = RdpFixture(base, "symlink")
    (sl.root / "src" / "l").symlink_to("../seed.txt")
    _rdp_git(sl.root, "add", "src/l")
    _rdp_git(sl.root, "commit", "-q", "-m", "link")
    sl_pin = _rdp_git(sl.root, "rev-parse", "HEAD")
    sl.authority(sl_pin + "\n")
    (sl.root / "src" / "l").unlink()
    (sl.root / "src" / "l").symlink_to("../old.txt")
    sl_got = [_rdp_kind(sl.dispatch(sl.good(pin=sl_pin, paths=("src/l",))))]
    (sl.root / "src" / "l").unlink()
    (sl.root / "src" / "l").symlink_to("../seed.txt")
    sl_got.append(_rdp_kind(sl.dispatch(sl.good(pin=sl_pin, paths=("src/l",)))))
    check("rdp/symlink-target-compared", sl_got, ["deny", "allow"])
    # A read that blocks in the kernel (a hung filesystem; here a sleep that never checks the deadline) cannot
    # hold the hook past its budget.
    real_blob = aiqt_hooks._rdp_blob_id

    def _hung_blob(fmt, header_size, fd):
        aiqt_hooks.time.sleep(3)
        return real_blob(fmt, header_size, fd)
    saved_budget = aiqt_hooks._RDP_BUDGET
    aiqt_hooks._RDP_BUDGET = 1.0
    aiqt_hooks._rdp_blob_id = _hung_blob
    started = aiqt_hooks.time.monotonic()
    try:
        hung_kind = _rdp_kind(f.dispatch(f.good()))
    finally:
        aiqt_hooks._rdp_blob_id = real_blob
        aiqt_hooks._RDP_BUDGET = saved_budget
    check("rdp/hung-read-bounded-by-deadline", (hung_kind, aiqt_hooks.time.monotonic() - started < 2.0),
          ("unverifiable", True))
    aiqt_hooks.time.sleep(2.5)   # the abandoned worker finishes before the next vector uses f
    mf = RdpFixture(base, "mutants")
    check("rdp/duplicate-repo-or-branch-unverifiable", [_rdp_kind(mf.dispatch(mf.good(extra=[
        lab + " " + v, lab + " " + v]))) for lab, v in (("Review-repo:", str(mf.root)), ("Review-branch:", "main"))],
        ["unverifiable", "unverifiable"])
    check("rdp/unparseable-dispatch-unverifiable", _rdp_kind(mf.run("orch-dispatch --brief 'open")),
          "unverifiable")
    check("rdp/empty-path-value-unverifiable", _rdp_kind(mf.dispatch(mf.good(extra=["Review-path: "]))),
          "unverifiable")
    mf.write_registry(dict(mf.binding, surplus=1))
    check("rdp/malformed-binding-without-tool-input-unverifiable", _rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(mf.root), tool_name="Bash", tool_input="ls"))), "unverifiable")
    mf.write_registry(mf.binding)
    real_review_git = aiqt_hooks._review_git

    failed_probe = []
    for verb in ("diff-index", "ls-tree"):
        def _fail_one(repo, *args, stdin=None, _verb=verb):
            return None if args[:1] == (_verb,) else real_review_git(repo, *args, stdin=stdin)
        aiqt_hooks._review_git = _fail_one
        try:
            failed_probe.append(_rdp_kind(mf.dispatch(mf.good())))
        finally:
            aiqt_hooks._review_git = real_review_git
    check("rdp/failed-state-probe-unverifiable", failed_probe, ["unverifiable", "unverifiable"])

    # ---------- one strict plain-command classifier (the shared specification) ----------
    check("rdp/plain-spec-vector-table", [(c, aiqt_hooks._rdp_plain_words(c)[0] is not None)
                                          for c, _p in aiqt_hooks._RDP_PLAIN_VECTORS],
          list(aiqt_hooks._RDP_PLAIN_VECTORS))
    bad = mf.brief(["Review-target: revision", "Reviewed-revision: HEAD"], "bad.txt")
    good = mf.good()
    # Bash truncates an ANSI-C quoted word at a NUL, so these run orch-dispatch with the unpinned brief.
    check("rdp/ansi-c-nul-name-unverifiable", [_rdp_kind(mf.run(c + "patch --brief " + bad)) for c in (
        "$'orch-dis\\0junk'", "$'orch-dis\\x00junk'", "$'orch-dis\\u0000junk'")], ["unverifiable"] * 3)
    # A non-ASCII digit is no descriptor to bash: it stays the brief argument, so the command is not plain.
    check("rdp/unicode-digit-not-plain-unverifiable", [_rdp_kind(mf.run(
        "orch-dispatch --brief " + d + "</dev/null " + good)) for d in ("\u00b2", "\u0661", "\uff10")],
        ["unverifiable"] * 3)
    # An earlier command in the same call can rewrite the brief or the tree after the check.
    check("rdp/same-call-rewrite-unverifiable", [_rdp_kind(mf.run(c)) for c in (
        "cp " + bad + " " + good + "; orch-dispatch --brief " + good,
        "cp " + bad + " " + good + " && orch-dispatch --brief " + good,
        "git -C " + str(mf.root) + " checkout -q " + mf.seed + " && orch-dispatch --brief " + good,
        "orch-dispatch --brief " + good + " & cp " + bad + " " + good,
        "printf -v HOME %s /tmp; orch-dispatch --brief " + good)], ["unverifiable"] * 5)
    check("rdp/case-folded-command-name", [_rdp_kind(mf.run(n + " --brief " + bad)) for n in (
        "ORCH-DISPATCH", "Orch-Dispatch", "/usr/bin/ORCH-dispatch")], ["deny"] * 3)
    mf.rebind(dict(mf.binding, brief_option="-b"))
    check("rdp/short-option-equals-form-denies", [_rdp_kind(mf.run("orch-dispatch -b" + c + good)) for c in (
        "=", " ")], ["deny", "allow"])
    mf.rebind(mf.binding)
    bom = mf.briefs / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbf" + "".join(line + "\n" for line in [
        "Review-target: revision", "Reviewed-revision: " + mf.pin] + [
        "Review-path: " + p for p in mf.CHANGED]).encode("utf-8"))
    check("rdp/bom-brief-allows", _rdp_kind(mf.dispatch(str(bom))), "allow")
    # /proc/self names the hook's own process, not the dispatcher's: here the hook's cwd holds a good brief.
    saved_cwd = os.getcwd()
    os.chdir(str(mf.briefs))
    try:
        proc_kind = _rdp_kind(mf.dispatch("/proc/self/cwd/brief.txt"))
    finally:
        os.chdir(saved_cwd)
    check("rdp/proc-self-brief-unverifiable", proc_kind, "unverifiable")
    # The worker finishes after the deadline and after the caller's reset of the shared deadline (simulated
    # by the worker clearing it), so the worker's own overdue check cannot see it: the verdict is not used.
    real_judge = aiqt_hooks._rdp_judge

    def _late_judge(*args, **kwargs):
        aiqt_hooks.time.sleep(0.35)
        aiqt_hooks._RDP_DEADLINE[0] = None
        return ("allow", "")
    saved_budget = aiqt_hooks._RDP_BUDGET
    aiqt_hooks._RDP_BUDGET = 0.3
    aiqt_hooks._rdp_judge = _late_judge
    try:
        late_kind = _rdp_kind(mf.dispatch(good))
    finally:
        aiqt_hooks._rdp_judge = real_judge
        aiqt_hooks._RDP_BUDGET = saved_budget
    check("rdp/late-verdict-never-used", late_kind, "unverifiable")
    fx = RdpFixture(base, "filemode")
    (fx.root / "src" / "b.py").chmod(0o755)
    _rdp_git(fx.root, "add", "src/b.py")
    _rdp_git(fx.root, "commit", "-q", "-m", "exec")
    fx_pin = _rdp_git(fx.root, "rev-parse", "HEAD")
    fx.authority(fx_pin + "\n")
    (fx.root / "src" / "b.py").chmod(0o644)
    fx_got = []
    for mode in ("false", "true"):
        _rdp_git(fx.root, "config", "core.fileMode", mode)
        fx_got.append(_rdp_kind(fx.dispatch(fx.good(pin=fx_pin, paths=("src/b.py",)))))
    check("rdp/filemode-false-ignores-exec-bit", fx_got, ["allow", "deny"])
    # A relative authority runs from the registry's worktree: a linked worktree without its own copy still
    # gets the registry's authority, and a nested repository's own authority and commit are never used.
    ra = RdpFixture(base, "relauth")
    auth = "import subprocess, sys\nsys.stdout.write(subprocess.run(['git', 'rev-parse', 'HEAD'], " \
           "capture_output=True, text=True).stdout)\n"
    (ra.root / "auth.py").write_text(auth, encoding="utf-8")
    ra.write_registry(dict(ra.binding, authority=dict(argv=[sys.executable, "-I", "-B", "auth.py"], timeout=5)))
    _rdp_git(ra.root, "worktree", "add", "-q", "--detach", str(base / "relauth-linked"), ra.pin)
    nested = ra.root / "nested"
    _rdp_git(base, "init", "-q", "-b", "main", str(nested))
    (nested / "x.txt").write_text("x\n", encoding="utf-8")
    _rdp_git(nested, "add", "x.txt")
    _rdp_git(nested, "commit", "-q", "-m", "nested")
    nested_pin = _rdp_git(nested, "rev-parse", "HEAD")
    (nested / "auth.py").write_text(auth, encoding="utf-8")

    def _from(cwd, brief, background=False):
        return _rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(cwd), session_id="s1", tool_name="Bash",
            tool_input=dict(command="orch-dispatch --brief " + brief, run_in_background=background))))
    check("rdp/authority-runs-from-registry-worktree", [
        _from(base / "relauth-linked", ra.good()),
        _from(nested, ra.good(pin=nested_pin, paths=("x.txt",)))], ["allow", "unverifiable"])
    # core.worktree moves git's top level to a directory whose registry has no binding: the cwd's own
    # registry still scopes the session, and the redirected top level is foreign to it.
    cw = RdpFixture(base, "coreworktree")
    elsewhere = base / "coreworktree-elsewhere"
    (elsewhere / ".aiqt").mkdir(parents=True)
    (elsewhere / ".aiqt" / "orchestration.local.json").write_text(json.dumps(dict(
        version=1, state_dir=str(cw.briefs / "state2"))), encoding="utf-8")
    cw_branch = cw.brief(["Review-target: revision", "Review-branch: main"], "branch-only.txt")
    cw_got = [_rdp_kind(cw.dispatch(cw_branch))]
    _rdp_git(cw.root, "config", "core.worktree", str(elsewhere))
    cw_got.append(_rdp_kind(cw.dispatch(cw_branch)))
    check("rdp/core-worktree-cannot-unscope", cw_got, ["deny", "unverifiable"])
    # A linked worktree beside its main worktree stays scoped when the shared configuration breaks git
    # (read from the raw .git and commondir files), and when the git directory is separated from the main
    # worktree (no main worktree can be located: withheld as a registry state that cannot be read).
    lb = RdpFixture(base, "linkedbroken")
    lb_branch = lb.brief(["Review-target: revision", "Review-branch: main"], "branch-only.txt")
    _rdp_git(lb.root, "worktree", "add", "-q", "--detach", str(base / "linkedbroken-beside"), lb.pin)
    lb_got = [_from(base / "linkedbroken-beside", lb_branch)]
    with open(str(lb.root / ".git" / "config"), "a", encoding="utf-8") as fh:
        fh.write("\n[broken\n")
    lb_got.append(_from(base / "linkedbroken-beside", lb_branch))
    check("rdp/linked-worktree-broken-config-scoped", lb_got, ["deny", "unverifiable"])
    ls_ = RdpFixture(base, "linkedsep")
    ls_branch = ls_.brief(["Review-target: revision", "Review-branch: main"], "branch-only.txt")
    _rdp_git(ls_.root, "worktree", "add", "-q", "--detach", str(base / "linkedsep-beside"), ls_.pin)
    _rdp_git(ls_.root, "init", "-q", "--separate-git-dir=" + str(base / "linkedsep-meta"))
    subprocess.run(["git", "-C", str(ls_.root), "worktree", "repair"], capture_output=True, timeout=30)
    check("rdp/linked-worktree-separate-gitdir-withheld", [
        _from(base / "linkedsep-beside", ls_branch, background=True), _from(base / "linkedsep-beside", ls_branch),
        _rdp_kind(ls_.dispatch(ls_branch))], ["unverifiable", "unverifiable", "deny"])
    # The same separated git directory with a broken shared configuration: git cannot resolve the linked
    # worktree, and the raw .git and commondir files name a common directory that is no main worktree's
    # .git, so the scope still cannot be known and every call stays withheld.
    with open(str(base / "linkedsep-meta" / "config"), "a", encoding="utf-8") as fh:
        fh.write("\n[invalid\n")
    check("rdp/linked-worktree-separate-gitdir-broken-config-withheld", [
        _from(base / "linkedsep-beside", "/nonexistent", background=bg) for bg in (False, True)],
        ["unverifiable", "unverifiable"])
    # A registry WITHOUT a binding never ends the search for one: not one a linked worktree checks out from
    # a commit (the binding kept in the main worktree's untracked local registry), not one written beside
    # or inside a linked worktree, not one in a repository nested inside the orchestrated tree.
    sw = RdpFixture(base, "shadow")
    sw_bad = sw.brief(["Review-target: revision", "Reviewed-revision: HEAD"], "bad.txt")
    unbound = json.dumps(dict(version=1))
    (sw.root / ".aiqt" / "orchestration.json").write_text(unbound, encoding="utf-8")
    _rdp_git(sw.root, "add", ".aiqt/orchestration.json")
    _rdp_git(sw.root, "commit", "-q", "-m", "registry without a binding")
    _rdp_git(sw.root, "worktree", "add", "-q", "--detach", str(base / "shadow-committed"), "HEAD")
    sw_dirs = [base / "shadow-committed"]
    for wt in (base / "shadow-beside", sw.root / ".worktrees" / "inside"):
        _rdp_git(sw.root, "worktree", "add", "-q", "--detach", str(wt), sw.pin)
        (wt / ".aiqt").mkdir()
        (wt / ".aiqt" / "orchestration.local.json").write_text(unbound, encoding="utf-8")
        sw_dirs.append(wt)
    sw_nested = sw.root / "nested"
    _rdp_git(base, "init", "-q", "-b", "main", str(sw_nested))
    (sw_nested / "x.txt").write_text("x\n", encoding="utf-8")
    _rdp_git(sw_nested, "add", "x.txt")
    _rdp_git(sw_nested, "commit", "-q", "-m", "nested")
    (sw_nested / ".aiqt").mkdir()
    (sw_nested / ".aiqt" / "orchestration.local.json").write_text(unbound, encoding="utf-8")
    check("rdp/bindingless-registry-cannot-hide-binding", [_from(d, sw_bad) for d in sw_dirs + [sw_nested]],
          ["deny", "deny", "deny", "unverifiable"])
    # A failed or timed-out common-directory probe in a linked worktree: the raw .git and commondir files
    # still name the main worktree, whose binding checks the dispatch (never a foreground note).
    real_main = aiqt_hooks._rdp_main_worktree

    def _probe_fails(root):
        raise OSError("git cannot read the common git directory of {}".format(root))
    aiqt_hooks._rdp_main_worktree = _probe_fails
    try:
        probe_got = [_from(base / "linked-beside", lw_branch, background=bg) for bg in (False, True)]
    finally:
        aiqt_hooks._rdp_main_worktree = real_main
    check("rdp/failed-main-worktree-probe-reads-gitfile", probe_got, ["deny", "deny"])
    # The authority runs with every ambient GIT_* variable scrubbed, as the git probes do.
    auth_envs = []
    auth_argv = f.binding["authority"]["argv"]
    real_run = aiqt_hooks.subprocess.run

    def _capture_auth(argv, **kw):
        if list(argv[:len(auth_argv)]) == auth_argv:
            auth_envs.append(kw.get("env"))
        return real_run(argv, **kw)
    saved_param = os.environ.get("GIT_CONFIG_PARAMETERS")
    os.environ["GIT_CONFIG_PARAMETERS"] = "'aiqt.selftest=1'"
    aiqt_hooks.subprocess.run = _capture_auth
    try:
        f.authority(f.pin + "\n")
        f.dispatch(f.good())
    finally:
        aiqt_hooks.subprocess.run = real_run
        if saved_param is None:
            os.environ.pop("GIT_CONFIG_PARAMETERS", None)
        else:
            os.environ["GIT_CONFIG_PARAMETERS"] = saved_param
    check("rdp/authority-git-env-scrubbed", [isinstance(e, dict) and "GIT_CONFIG_PARAMETERS" not in e
                                             and e.get("GIT_TERMINAL_PROMPT") == "0" for e in auth_envs], [True])
    # A dispatcher name assembled at run time is not in the raw text: the command is not plain, so it is
    # withheld although no declared name can be found in it.
    sw_run = RdpFixture(base, "assembled")
    as_bad = sw_run.brief(["Review-target: revision", "Reviewed-revision: HEAD"], "bad.txt")
    check("rdp/assembled-name-unverifiable", [_rdp_kind(sw_run.run(c)) for c in (
        "o=orch-; ${o}dispatch --brief " + as_bad, "o=orch-dis; ${o}patch --brief " + as_bad,
        "/usr/local/bin/orch-dispatc? --brief " + as_bad, "orch-dis$(true)patch --brief " + as_bad)],
        ["unverifiable"] * 4)
    # A plain command that names a declared command inside an argument (a git alias that runs it), compared
    # without regard to case: refused, since its -c global sets configuration, which no bound session may.
    check("rdp/plain-command-naming-dispatch-unverifiable", [_rdp_kind(sw_run.run(c)) for c in (
        "git -c 'alias.r=!orch-dispatch --brief " + as_bad + "' r",
        "git -c 'alias.r=!ORCH-Dispatch --brief " + as_bad + "' r")], ["deny"] * 2)
    # Each rule of the plain-command specification on its own: every forbidden character, bare or in a
    # double-quoted segment (but literal in a single-quoted one), a tab or a line break, an unterminated
    # quote, every wrapper command word, also in another case, and rule 4's allowlist: each allowed program,
    # bare or under one of its four directories, is plain, and any other command word, an allowed one in
    # another case, or one under another directory or a relative path is not.
    allowed = (
        "git opf ls cat echo printf pwd true false test head tail wc grep egrep fgrep diff cmp stat du df date "
        "basename dirname realpath readlink uniq cut tr mkdir rmdir touch cp mv rm ln chmod").split()
    forbidden = "$`\\;&|<>(){}[]*?!#~"
    wrappers = (
        ". alias ash awk bash builtin bun busybox bwrap caffeinate chroot chrt command coproc csh dash "
        "deno doas entr env eval exec expect fakeroot find firejail fish flock function gawk gdb ghci "
        "groovy guile ionice irb jshell julia ksh ltrace lua luajit make mawk mksh nawk nice node nodejs "
        "nohup nsenter oksh osascript parallel pdksh perl php powershell proot pwsh pypy python racket "
        "rbash rlwrap rscript ruby runghc runuser script sed setpriv setsid sh source ssh stdbuf strace "
        "su sudo systemd-run taskset tclsh tcsh time timeout toybox trap unbuffer unshare valgrind watch "
        "wish xargs yash zsh").split()
    rule_rows = [("git log a" + ch, False) for ch in forbidden] + \
        [('git log "a' + ch + '"', False) for ch in forbidden] + \
        [("git log 'a" + ch + "'", True) for ch in forbidden] + [
        ("git\tstatus", False), ("git status\n", False), ("git status\r", False), ("git log 'x", False),
        ('git log "x', False), ("git log x'", False)] + \
        [(w + " x", False) for w in wrappers] + [(w.upper() + " x", False) for w in wrappers] + \
        [("/usr/bin/" + w + " x", False) for w in wrappers] + \
        [(w + " x", True) for w in allowed] + [(d + "/" + w + " x", True) for w in allowed for d in (
            "/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin")] + [(w.upper() + " x", False) for w in allowed] + \
        [(c + " x", False) for c in (
            "sort", "cd", "jq", "tar", "python3.14", "/usr/bin/python3.14", "./ls", "/tmp/x/ls", "bin/ls",
            "../bin/ls", "/usr//bin/ls", "/usr/bin/../bin/ls", "/ls", "/usr/bin/", "nodejs", "tee", "dd",
            "install", "rsync", "vim", "less", "orch-dispatch")]
    check("rdp/plain-spec-each-rule", [(c, aiqt_hooks._rdp_plain_words(c)[0] is not None) for c, _p in rule_rows],
          rule_rows)
    check("rdp/plain-words-literal", aiqt_hooks._rdp_plain_words("git commit -m 'fix: a; b' x\"y\"z"),
          (["git", "commit", "-m", "fix: a; b", "xyz"], None))
    import contextlib
    import io
    saved_decide, saved_stdin = aiqt_hooks._rdp_decide, sys.stdin

    def _boom(data):
        raise RuntimeError("injected review dispatch fault")
    try:
        aiqt_hooks._rdp_decide = _boom
        sys.stdin = io.StringIO(json.dumps(dict(hook_event_name="PreToolUse", cwd=str(f.root),
                                                tool_name="Bash", tool_input=dict(command="ls"))))
        with contextlib.redirect_stderr(io.StringIO()):
            rc = aiqt_hooks.main(["review_dispatch_pin"])
    finally:
        aiqt_hooks._rdp_decide, sys.stdin = saved_decide, saved_stdin
    check("rdp/handler-crash-fails-closed", rc, 2)
    _rdp_draft_cases(base)
    _rdp_scope_cases(base, bare)


def _rdp_draft_diff(root, base, head):
    """A worker draft as git writes it: the diff from base to head with git's default a/ and b/ prefixes
    and rename detection, whatever the ambient configuration says."""
    return _rdp_git(root, "diff", "--no-color", "--no-ext-diff", "-M", "--src-prefix=a/", "--dst-prefix=b/",
                    base, head) + "\n"


def _rdp_draft_cases(base):
    """The worker draft census vectors (rdp/draft-*): where the binding declares the draft and skip labels,
    a revision brief naming the draft a commit applies declares every path the draft's diff headers name,
    less the paths it records as skipped, so a drafted path neither committed nor skipped is refused; a
    rename names both of its paths; a draft whose headers cannot be read, a section it does not complete,
    a section whose headers disagree, a diff header or no-newline marker outside a section, a marker that
    is not git's exact one in its place, a CRLF line, a transfer-encoded mailbox part and a submodule shown
    inline are each a cannot-evaluate whose reason names its cause."""
    labels = dict(target="Review-target:", revision="Reviewed-revision:", path="Review-path:",
                  repo="Review-repo:", branch="Review-branch:", draft="Review-draft:", skip="Review-skip:")
    dr = RdpFixture(base, "draft")
    dr.binding = dict(dr.binding, labels=labels)
    dr.write_registry(dr.binding)

    def _draft(fixture, name, text):
        path = fixture.briefs / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _named(result, *needles):
        reason = result[1]["hookSpecificOutput"].get("permissionDecisionReason", "") if result[1] else ""
        return (_rdp_kind(result), all(needle in reason for needle in needles))

    def _read_as(rows, stem):
        """The verdict on a brief naming each draft text and no Review-path line, with whether the reason
        says that draft cannot be read as a git diff for the row's cause; an allow needs every changed path
        read from the draft, so a control row that allows shows the binding valid and the draft read."""
        got = []
        for k, (text, cause) in enumerate(rows):
            path = _draft(dr, "{}{}.diff".format(stem, k), text)
            got.append(_named(dr.dispatch(dr.good(paths=(), extra=["Review-draft: " + path])),
                              "the draft {} cannot be read as a git diff: ".format(path), cause))
        return got
    real = _rdp_draft_diff(dr.root, dr.seed, dr.pin)
    real_d = _draft(dr, "real.diff", real)
    c_section = ("diff --git a/src/c.py b/src/c.py\nnew file mode 100644\nindex 0000000..2ad4f87\n"
                 "--- /dev/null\n+++ b/src/c.py\n@@ -0,0 +1 @@\n+c = 1\n")
    unapplied = _draft(dr, "unapplied.diff", real + c_section)
    # The draft supplies the declared set: no Review-path line, all of them, or some of them. A draft is
    # split on LF only, so a form feed in it (as some source files hold) is content, not a line boundary.
    feed = _draft(dr, "feed.diff", real + "notes\x0c\n")
    check("rdp/draft-census-declares-drafted-paths", [_rdp_kind(dr.dispatch(dr.good(
        paths=p, extra=["Review-draft: " + d]))) for p, d in (
        ((), real_d), (dr.CHANGED, real_d), (("src/a.py",), real_d), ((), feed))], ["allow"] * 4)
    # A drafted file the apply left out is in neither the commit nor the hand list, yet it is refused.
    check("rdp/draft-unapplied-path-denies", [_named(dr.dispatch(dr.good(
        paths=p, extra=["Review-draft: " + unapplied])), "drafted in {} but neither changed".format(unapplied))
        for p in (dr.CHANGED, ())], [("deny", True)] * 2)
    check("rdp/draft-recorded-skip-allows", [_rdp_kind(dr.dispatch(dr.good(
        paths=p, extra=["Review-draft: " + unapplied, "Review-skip: src/c.py"]))) for p in (dr.CHANGED, ())],
        ["allow"] * 2)
    # Every skip line counts: with two drafted paths left out, both skips allow, and either one alone
    # leaves the other path refused as drafted. Coverage only: skip handling does not depend on the diff
    # grammar, and a reader without that grammar passes this check too.
    two = _draft(dr, "two.diff", real + c_section + c_section.replace("c.py", "d.py"))
    check("rdp/draft-several-skips-allow", [_named(dr.dispatch(dr.good(paths=(), extra=[
        "Review-draft: " + two] + ["Review-skip: " + s for s in skips])), "neither changed", w)
        for skips, w in ((("src/c.py", "src/d.py"), ""), (("src/c.py",), "line: 'src/d.py')"),
                         (("src/d.py",), "line: 'src/c.py')"))], [("allow", False), ("deny", True), ("deny", True)])
    # Each skip refusal beside its control, a skip of a drafted path the commit leaves out, which allows.
    skip_ok = ["Review-draft: " + unapplied, "Review-skip: src/c.py"]
    check("rdp/draft-skip-of-undrafted-path-denies", [_named(dr.dispatch(dr.good(extra=e)), w) for e, w in (
        (skip_ok, "names no such path"), (["Review-draft: " + real_d, "Review-skip: src/zzz.py"],
                                          "names no such path"))], [("allow", False), ("deny", True)])
    check("rdp/draft-skip-of-committed-path-denies", [_named(dr.dispatch(dr.good(extra=e)), w) for e, w in (
        (skip_ok, "changes it"), (["Review-draft: " + real_d, "Review-skip: src/a.py"], "changes it"))],
        [("allow", False), ("deny", True)])
    check("rdp/draft-skip-without-draft-denies", [_named(dr.dispatch(dr.good(extra=e)), w) for e, w in (
        (skip_ok, "no 'Review-draft:' line"), (["Review-skip: src/c.py"], "no 'Review-draft:' line"))],
        [("allow", False), ("deny", True)])
    # A second draft line, a relative draft path (here one that resolves from the hook's own cwd) and an
    # empty skip value are cannot-evaluate, each with its reason; the controls, one absolute draft line
    # alone and with a skip, allow.
    check("rdp/draft-label-values-checked", [_named(dr.dispatch(dr.good(extra=e)), w) for e, w in (
        (["Review-draft: " + real_d], "(control)"), (skip_ok, "(control)"),
        (["Review-draft: " + real_d, "Review-draft: " + real_d], "has 2 'Review-draft:' lines; at most one"),
        (["Review-draft: " + os.path.relpath(real_d)], "{!r} is not an absolute path".format(
            os.path.relpath(real_d))),
        (["Review-draft: " + unapplied, "Review-skip: "], "has an empty 'Review-skip:' value"))],
        [("allow", False)] * 2 + [("unverifiable", True)] * 3)
    # Each draft the hook cannot read names the draft and its cause; the control, the valid draft with no
    # Review-path line, allows only where the binding is valid and the draft is read.
    link = dr.briefs / "link.diff"
    link.symlink_to(real_d)
    big = _draft(dr, "big.diff", real + "\n" * 5000)
    latin = dr.briefs / "latin.diff"
    latin.write_bytes(real.encode("utf-8") + b"caf\xe9\n")
    nul = _draft(dr, "nul.diff", real + "\x00\n")
    unreadable = ((real_d, "(control)"), (str(dr.briefs / "absent.diff"), "cannot be opened"),
                  (str(link), "cannot be opened"), (big, "is larger than max_brief_bytes"),
                  (str(dr.briefs), "is not a regular file"), (str(latin), "is not valid UTF-8"),
                  (nul, "contains a NUL"))
    check("rdp/draft-unreadable-unverifiable", [_named(dr.dispatch(dr.good(paths=(), extra=[
        "Review-draft: " + d])), "the draft {} {}".format(d, cause)) for d, cause in unreadable],
        [("allow", False)] + [("unverifiable", True)] * (len(unreadable) - 1))
    # The draft is read under the brief's max_brief_bytes cap: one byte inside it, exactly at it, and one
    # byte past it.
    cap = dr.binding["max_brief_bytes"]
    sized = [_draft(dr, "size{}.diff".format(n), real + "\n" * (n - len(real.encode("utf-8"))))
             for n in (cap - 1, cap, cap + 1)]
    check("rdp/draft-size-cap-boundary", [(os.path.getsize(d), _rdp_kind(dr.dispatch(dr.good(
        extra=["Review-draft: " + d])))) for d in sized],
        [(cap - 1, "allow"), (cap, "allow"), (cap + 1, "unverifiable")])
    # Each section the census cannot read, appended to a draft whose other sections are all applied, with
    # the cause its reason names: a combined diff, git's --no-prefix and mnemonic prefixes, a --- line with
    # no +++ line, a truncated hunk, a hunk line that is no context, removed or added line, an unreadable
    # hunk header, a hunk with more removed lines than it counts, a diff --git line naming two paths with no
    # rename or copy header, a quoted name with an unknown escape, and diff --git lines naming their paths
    # under b/ and then a/ as git diff -R writes them, above a mode change (bare and C-quoted), a binary
    # change, a pure rename, an empty file in both forms and a pure copy (the last three as git 2.55 writes
    # them for the creation of an empty file, the deletion of an empty file and, with -C
    # --find-copies-harder, the deletion of a duplicate), none of which has a --- line to name the prefix:
    # the reason names the swap. The first row is the control.
    swapped = "names its paths under b/ and then a/, git's default prefixes swapped, as git diff -R writes them"
    bad_sections = (
        ("", "(control)"),
        ("diff --cc src/a.py\nindex 1,2..3\n--- a/src/a.py\n+++ b/src/a.py\n@@@ -1,1 -1,1 +1,1 @@@\n- a = 1\n"
         " -a = 3\n++a = 2\n", "it holds a combined diff"),
        ("diff --git src/a.py src/a.py\n--- src/a.py\n+++ src/a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
         "does not name a path under git's a/ prefix"),
        ("diff --git c/src/a.py i/src/a.py\n--- c/src/a.py\n+++ i/src/a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
         "does not name a path under git's a/ prefix"),
        ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
         "has no +++ line after it"),
        ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n",
         "a hunk ends before its header's line counts"),
        ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n*a = 1\n+a = 2\n",
         "is no context, removed or added line"),
        ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -x +1 @@\n-a = 1\n+a = 2\n",
         "the hunk header '@@ -x +1 @@' cannot be read"),
        ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-a = 1\n-b = 1\n"
         "+a = 2\n+b = 2\n", "holds more lines than its header counts"),
        ("diff --git a/src/a.py b/src/d.py\nold mode 100644\nnew mode 100755\n", "cannot be read as one path"),
        ('diff --git "a/src/a\\q.py" "b/src/a\\q.py"\nold mode 100644\nnew mode 100755\n',
         "cannot be read as one path"),
        ("diff --git b/src/a.py a/src/a.py\nold mode 100755\nnew mode 100644\n", swapped),
        ('diff --git "b/caf\\303\\251.sh" "a/caf\\303\\251.sh"\nold mode 100755\nnew mode 100644\n', swapped),
        ("diff --git b/src/c.png a/src/c.png\nindex 1592e5c..8352675 100644\n"
         "Binary files b/src/c.png and a/src/c.png differ\n", swapped),
        ("diff --git b/src/b.py a/old.txt\nsimilarity index 100%\nrename from src/b.py\nrename to old.txt\n",
         swapped),
        ("diff --git b/src/e.txt a/src/e.txt\ndeleted file mode 100644\nindex e69de29..0000000\n", swapped),
        ("diff --git b/src/e.txt a/src/e.txt\nnew file mode 100644\nindex 0000000..e69de29\n", swapped),
        ("diff --git b/src/b.py a/src/dup.py\nsimilarity index 100%\ncopy from src/b.py\ncopy to src/dup.py\n",
         swapped))
    check("rdp/draft-unparseable-unverifiable", _read_as([(real + s, w) for s, w in bad_sections], "bad"),
          [("allow", False)] + [("unverifiable", True)] * (len(bad_sections) - 1))
    # A draft that names no file in a diff header: prose with a separator and a diffstat, or nothing at all.
    # The control is the valid draft, through the same brief.
    without = (real, "The worker changed src/a.py and src/b.py and removed old.txt.\n---\n src/a.py | 2 +-\n", "")
    got = []
    for k, text in enumerate(without):
        path = _draft(dr, "prose{}.diff".format(k), text)
        got.append(_named(dr.dispatch(dr.good(paths=(), extra=["Review-draft: " + path])),
                          "the draft {} names no file in a diff header".format(path)))
    check("rdp/draft-without-headers-unverifiable", got, [("allow", False)] + [("unverifiable", True)] * 2)
    # A file section the draft opens and does not complete is never prose, wherever it stands: cut short
    # after its diff --git line, or after an index line, at the end, before prose or before another
    # section, or with a rename header and not its pair.
    git_c = "diff --git a/src/c.py b/src/c.py\n"
    cut = "ends with no hunk, binary marker"
    cut_rows = (
        (real, "(control)"),
        (real + git_c, cut),
        (real + git_c + "index 0000000..2ad4f87\n", cut),
        (real + git_c + "index 0000000..2ad4f87\nThe rest of the draft was lost.\n", cut),
        (git_c + "index 0000000..2ad4f87\n" + real, cut),
        (real + "diff --git a/src/b.py b/src/c.py\nsimilarity index 90%\nrename from src/b.py\n",
         "without the other"))
    check("rdp/draft-truncated-section-unverifiable", _read_as(cut_rows, "cut"),
          [("allow", False)] + [("unverifiable", True)] * (len(cut_rows) - 1))
    # A --- line naming a path opens a section, so one with no +++ line after it is refused, at the end of
    # the draft, before prose, before a section, as a prose line that reads as a header, or as the --- line
    # of a context-format (diff -c) section.
    minus_rows = (
        (real, "(control)"),
        (real + "--- a/src/c.py\n", "has no +++ line after it"),
        (real + "--- a/src/c.py\nThe rest of the draft was lost.\n", "has no +++ line after it"),
        ("--- a/src/c.py\n" + real, "has no +++ line after it"),
        (real + "--- a/src/a.py is the main file\n", "has no +++ line after it"),
        (real + "*** a/src/c.py\n--- b/src/c.py\n***************\n*** 0 ****\n--- 1 ----\n+ c = 1\n",
         "has no +++ line after it"))
    check("rdp/draft-minus-header-without-plus-unverifiable", _read_as(minus_rows, "minus"),
          [("allow", False)] + [("unverifiable", True)] * (len(minus_rows) - 1))
    # A +++ line with no hunk after it: a plain section at the end or before prose, a git section cut after
    # its +++ line or inside its hunk header, and a plain section cut inside its hunk header before another.
    c_head = c_section.split("@@")[0]
    plus_rows = (
        (real, "(control)"),
        (real + "--- a/src/c.py\n+++ b/src/c.py\n", "has no hunk after it"),
        (real + "--- a/src/c.py\n+++ b/src/c.py\nThe rest of the draft was lost.\n", "has no hunk after it"),
        (real + c_head, "has no hunk after it"),
        (real + c_head + "@@\n", "has no hunk after it"),
        ("--- a/src/c.py\n+++ b/src/c.py\n@\n" + real, "has no hunk after it"))
    check("rdp/draft-plus-header-without-hunk-unverifiable", _read_as(plus_rows, "plus"),
          [("allow", False)] + [("unverifiable", True)] * (len(plus_rows) - 1))
    # Outside a section, the mailbox headers and message, the --- separator, the diffstat and the -- signature
    # are prose (the controls: the valid draft, and git format-patch output of the same commit); any other
    # line that begins as a diff header does is refused: a +++ line with no --- line, a hunk after prose or
    # after a section with no hunks, extended headers whose diff --git line is gone, a binary marker, and a
    # diff command line git does not write.
    mbox = _rdp_git(dr.root, "format-patch", "--stdout", "--no-color", "--no-ext-diff", "-M", "--src-prefix=a/",
                    "--dst-prefix=b/", "--signature=fixture", "--no-attach", "-1", dr.pin) + "\n"
    stray_rows = (
        (real, "(control)"),
        (mbox, "(control)"),
        (real + "+++ b/src/c.py\n", "stands outside a file section"),
        (real + "Notes.\n@@ -0,0 +1 @@\n+c = 1\n", "stands outside a file section"),
        (real + "diff --git a/src/a.py b/src/a.py\nold mode 100644\nnew mode 100755\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
         "stands outside a file section"),
        (real + "similarity index 100%\nrename from src/b.py\nrename to src/c.py\n", "stands outside a file section"),
        (real + "new file mode 100644\nindex 0000000..2ad4f87\n", "stands outside a file section"),
        (real + "Binary files a/src/c.png and b/src/c.png differ\n", "stands outside a file section"),
        (real + "diff -ruN a/src/c.py b/src/c.py\n", "stands outside a file section"))
    check("rdp/draft-stray-header-unverifiable", ("\n---\n" in mbox and "\n-- \nfixture\n" in mbox,
                                                  _read_as(stray_rows, "stray")),
          (True, [("allow", False)] * 2 + [("unverifiable", True)] * (len(stray_rows) - 2)))
    # A header or hunk line ending in a carriage return before its LF is refused with a reason naming CRLF:
    # the whole draft saved with CRLF line ends, one CRLF hunk line, a CRLF mode change section, a CRLF
    # --- or +++ line that would otherwise be refused for its own cause, and a CRLF no-newline marker
    # inside a hunk and after its last counted line, which would otherwise be refused as no exact marker
    # (coverage rows: the CRLF check on a marker predates the exact marker reading).
    a_old = "\n-a = 1\n+a = 2\n"
    crlf_rows = (
        (real, "(control)"),
        (real.replace("\n", "\r\n"), "carriage return before its LF (CRLF)"),
        (real.replace("+a = 2\n", "+a = 2\r\n"), "carriage return before its LF (CRLF)"),
        (real + "diff --git a/src/a.py b/src/a.py\r\nold mode 100644\r\nnew mode 100755\r\n",
         "carriage return before its LF (CRLF)"),
        (real + "--- a/src/c.py\r\n", "carriage return before its LF (CRLF)"),
        (real + "+++ b/src/c.py\r\n", "carriage return before its LF (CRLF)"),
        (real.replace(a_old, "\n-a = 1\n\\ No newline at end of file\r\n+a = 2\n"),
         "carriage return before its LF (CRLF)"),
        (real.replace(a_old, a_old + "\\ No newline at end of file\r\n"), "carriage return before its LF (CRLF)"))
    check("rdp/draft-crlf-unverifiable", ("+a = 2\n" in real and a_old in real, _read_as(crlf_rows, "crlf")),
          (True, [("allow", False)] + [("unverifiable", True)] * (len(crlf_rows) - 1)))

    # A mailbox part transfer-encoded other than 7bit or 8bit hides the headers git am decodes and applies:
    # an 8bit part and a 7bit one (its header name in any case) are read, a base64 part holding a second
    # patch and a quoted-printable one are refused.
    def _part(encoding, subject, body):
        return ("From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\nFrom: T "
                "<t@example.invalid>\nSubject: [PATCH] {}\nMIME-Version: 1.0\nContent-Type: text/plain; "
                "charset=UTF-8\nContent-Transfer-Encoding: {}\n\n{}\n").format(subject, encoding, body)
    first = _part("8bit", "change", "change\n---\n" + real + "-- \nfixture")
    b64 = base64.encodebytes(("add c\n---\n" + c_section).encode("utf-8")).decode("ascii")
    encoded_rows = (
        (first, "(control)"),
        (first.replace("Content-Transfer-Encoding: 8bit", "content-transfer-encoding: 7BIT"), "(control)"),
        (first + _part("base64", "add c", b64), "is transfer-encoded"),
        (first + _part("quoted-printable", "add c", "add c\n---\n" + c_section.replace("=", "=3D")),
         "is transfer-encoded"))
    check("rdp/draft-encoded-part-unverifiable", _read_as(encoded_rows, "mail"),
          [("allow", False)] * 2 + [("unverifiable", True)] * 2)
    # git diff --submodule=log or --submodule=diff shows a submodule's own changes inline under a Submodule
    # header, refused with a reason naming --submodule=short; a prose line that begins with the word is read.
    sub_rows = (
        (real, "(control)"),
        (real + "Submodule sub was bumped in this change.\n", "(control)"),
        (real + "Submodule sub 1234567..89abcde:\ndiff --git a/sub/f b/sub/f\nindex 1234567..89abcde 100644\n"
         "--- a/sub/f\n+++ b/sub/f\n@@ -1 +1 @@\n-x\n+y\n", "--submodule=short"),
        (real + "Submodule sub 1234567..89abcde:\n  > bump sub\n", "--submodule=short"),
        (real + "Submodule sub 0000000...89abcde (new submodule)\n", "--submodule=short"),
        (real + "Submodule sub contains modified content\n", "--submodule=short"))
    check("rdp/draft-submodule-inline-unverifiable", _read_as(sub_rows, "sub"),
          [("allow", False)] * 2 + [("unverifiable", True)] * (len(sub_rows) - 2))
    # A line of a mailbox draft's commit message is read as prose only where it could not be what the census
    # refuses elsewhere: a message line that reads as an inline submodule header, a transfer encoding
    # other than 7bit or 8bit, a diff header or a no-newline marker is refused like one, since telling a
    # message line from a part header or a section's line needs the mailbox structure a draft can mangle.
    # The control is git format-patch output of the commit with a plain message line; the Submodule,
    # Content-Transfer-Encoding and index rows are coverage of refusals the census already made.
    msg_rows = [(mbox.replace("\n---\n", "\n" + line + "\n---\n", 1), w) for line, w in (
        ("Bump the submodule and note the encoding.", "(control)"),
        ("Submodule sub 1234567..89abcde: bumped", "--submodule=short"),
        ("Content-Transfer-Encoding: base64 is unsupported", "is transfer-encoded"),
        ("index 1234567..89abcde is the new blob", "stands outside a file section"),
        ("\\ No newline at end of file, as the old file had", "follows no hunk line"))]
    check("rdp/draft-message-lines-unverifiable", ("\n\n---\n" in mbox, _read_as(msg_rows, "msg")),
          (True, [("allow", False)] + [("unverifiable", True)] * (len(msg_rows) - 1)))
    # A hunk that ends in a \ No newline marker is followed by the next section, inside a full dispatch: the
    # deletion's marker before the src/a.py section, and a marker closing the draft. Coverage: a reader
    # that left the marker as prose passed this too, and only with a marker outside a hunk refused (the
    # next check) does it fail where the hunk reader does not consume the marker after its counted lines.
    marked = ((real.replace("\n-old\n", "\n-old\n\\ No newline at end of file\n"), "(marker)"),
              (real.replace("\n+b = 1\n", "\n+b = 1\n\\ No newline at end of file\n"), "(marker)"))
    check("rdp/draft-marker-then-section", ([t.count("\\ No newline") for t, _w in marked],
                                            _read_as(marked, "marker")),
          ([1, 1], [("allow", False)] * 2))
    # A line that begins as git's no-newline marker does is never prose: outside a hunk it is refused (after
    # a section that ends with no hunk, after prose, at the start, after a hunk's own marker), so the
    # control with the deletion's marker before the next section allows only because the hunk reader
    # consumes the marker after the deletion's counted line.
    no_nl = "\\ No newline at end of file\n"
    mode_a = "diff --git a/src/a.py b/src/a.py\nold mode 100644\nnew mode 100755\n"
    follows = "follows no hunk line"
    stray_marks = (
        (real, "(control)"),
        (real.replace("\n-old\n", "\n-old\n" + no_nl), "(control)"),
        (real + mode_a + no_nl, follows),
        (real + "Notes.\n" + no_nl, follows),
        ("\\ No newline here, as a message line\n" + real, follows),
        (real.replace("\n-old\n", "\n-old\n" + no_nl + no_nl), "follows no context, removed or added line"))
    check("rdp/draft-stray-marker-unverifiable", ("\n-old\ndiff --git a/src/a.py" in real,
                                                  _read_as(stray_marks, "nomark")),
          (True, [("allow", False)] * 2 + [("unverifiable", True)] * (len(stray_marks) - 2)))
    # Inside a hunk and right after its counted lines, only git's exact marker is read, and only where git
    # writes one: after the last line its hunk header counts for each side of the file it marks (a marker
    # after a removed line marks the old side, after an added line the new side, after a context line
    # both), in the section's last hunk. A section for src/a.py appended to the valid draft is read with
    # each side marked after its last line (the controls; the third, an added-line marker that a removed
    # line follows, shows that a marker bounds only the sides it marks) and refused with any other
    # backslash line in the hunk or right after its counted lines or their marker, a localized marker, a
    # marker after the hunk header or after another marker, a marker before the last line of a side it
    # marks (each kind, and with a second marker for that side after it), and a hunk after a marker (one
    # among the earlier hunk's counted lines, with and without a marker in the later hunk, or one right
    # after them).
    # Each row's section alone was run through real git apply --check against the file its old side
    # describes. The controls apply. Of the rows that check the marker's place, git applies those with a
    # backslash line right after the counted lines or their marker, a context marker that only added
    # lines follow, and an added-line marker that more added lines follow (only the old side must match
    # the file). It refuses a removed-line marker that another removed line follows and a context marker
    # that a removed line follows as not applying, and applies the same hunks without their markers. It
    # refuses each hunk after a marker too, but also with the markers removed, so that refusal does not
    # turn on the marker.
    a_head = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
    not_exact = "is no context, removed or added line, and not git's no-newline marker"
    after_hunk = "right after a hunk's counted lines begins with a backslash and is not git's no-newline marker"
    no_line = "follows no context, removed or added line"
    early = "stands before the last line its hunk header counts for the {} of the file"
    later = "follows a no-newline marker in its section"
    bad_marks = (
        ("@@ -1 +1 @@\n-a = 1\n" + no_nl + "+a = 2\n" + no_nl, "(control)"),
        ("@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n b\n" + no_nl, "(control)"),
        ("@@ -1,2 +1 @@\n-a = 1\n+a = 2\n" + no_nl + "-b\n", "(control)"),
        ("@@ -1 +1 @@\n-a = 1\n\\ garbage\n+a = 2\n", not_exact),
        ("@@ -1 +1 @@\n-a = 1\n+a = 2\n\\ garbage\n", after_hunk),
        ("@@ -1 +1 @@\n-a = 1\n+a = 2\n\\No newline at end of file\n", after_hunk),
        ("@@ -1 +1 @@\n-a = 1\n+a = 2\n" + no_nl + "\\x not a marker\n", after_hunk),
        ("@@ -1 +1 @@\n-a = 1\n\\ Kein Zeilenumbruch am Dateiende.\n+a = 2\n", not_exact),
        ("@@ -1 +1 @@\n" + no_nl + "-a = 1\n+a = 2\n", no_line),
        ("@@ -1 +1 @@\n-a = 1\n" + no_nl + no_nl + "+a = 2\n", no_line),
        ("@@ -1 +1 @@\n-a = 1\n+a = 2\n" + no_nl + no_nl, no_line),
        ("@@ -1,2 +1,2 @@\n-a = 1\n" + no_nl + "-b\n+x\n+y\n", early.format("old side")),
        ("@@ -1,2 +1,2 @@\n a = 1\n" + no_nl + "-b\n+c\n", early.format("old and new sides")),
        ("@@ -1,2 +1,2 @@\n-z\n a = 1\n" + no_nl + "+b\n", early.format("new side")),
        ("@@ -1 +1,2 @@\n-a = 1\n+a = 2\n" + no_nl + "+b\n", early.format("new side")),
        ("@@ -1,2 +1 @@\n-a = 1\n" + no_nl + "-b\n" + no_nl + "+a = 2\n", early.format("old side")),
        ("@@ -1,2 +1 @@\n a = 1\n" + no_nl + "-b\n" + no_nl, early.format("old side")),
        ("@@ -1 +1,2 @@\n-a = 1\n+a = 2\n" + no_nl + "+b\n" + no_nl, early.format("new side")),
        ("@@ -1 +1 @@\n-a = 1\n" + no_nl + "+a = 2\n@@ -3 +3 @@\n-c\n" + no_nl + "+d\n", later),
        ("@@ -1 +1 @@\n-a = 1\n" + no_nl + "+a = 2\n@@ -3 +3 @@\n-c\n+d\n", later),
        ("@@ -1 +1 @@\n-a = 1\n+a = 2\n" + no_nl + "@@ -3 +3 @@\n-c\n+d\n", later))
    check("rdp/draft-marker-malformed-unverifiable", _read_as([(real + a_head + h, w) for h, w in bad_marks],
                                                               "badmark"),
          [("allow", False)] * 3 + [("unverifiable", True)] * (len(bad_marks) - 3))
    # A section names at most one old path and one new path, and its diff --git line names exactly those, as
    # git writes them from one pair; a section whose headers disagree is refused, not counted by one of
    # them. The controls are the valid draft and a pure rename section whose headers agree; refused are
    # path headers naming another path than the diff --git line (a change, a creation, a deletion), rename
    # or copy headers naming other paths than it, and rename headers naming other paths than the +++ line.
    git_c = "diff --git a/src/c.py b/src/c.py\n"
    git_line = "disagree with its diff --git line"
    disagree_rows = (
        (real, "(control)"),
        (real + "diff --git a/old.txt b/src/b.py\nsimilarity index 100%\nrename from old.txt\nrename to src/b.py\n",
         "(control)"),
        (real + git_c + "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n", git_line),
        (real + git_c + "new file mode 100644\n--- /dev/null\n+++ b/src/b.py\n@@ -0,0 +1 @@\n+b = 1\n", git_line),
        (real + git_c + "deleted file mode 100644\n--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n", git_line),
        (real + git_c + "similarity index 100%\nrename from old.txt\nrename to src/b.py\n", git_line),
        (real + "diff --git a/src/c.py b/src/b.py\nsimilarity index 90%\ncopy from src/a.py\ncopy to src/b.py\n",
         git_line),
        (real + "diff --git a/old.txt b/src/b.py\nsimilarity index 90%\nrename from old.txt\nrename to src/b.py\n"
         "--- a/old.txt\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+a = 2\n", "disagree with each other"))
    check("rdp/draft-section-headers-disagree-unverifiable", _read_as(disagree_rows, "disagree"),
          [("allow", False)] * 2 + [("unverifiable", True)] * (len(disagree_rows) - 2))
    # The paths are read from the headers alone: hunks are consumed by their counts (so content lines that
    # read as --- and +++ headers are content), C-quoted and tab-ended names are decoded, a copy names its
    # destination only, a rename both paths, a section with no path header the name its diff --git line
    # repeats, a plain unified section its a/ and b/ names, a binary patch's encoded lines are prose, and
    # prose around the sections is skipped: the mailbox headers and message, the --- separator (also with
    # trailing blanks), the diffstat, the -- signature, and a section indented from column 0, which git
    # apply does not read either. The last three rows are coverage of readings the census already made: a
    # rename whose diff --git line quotes one name and not the other, names holding ` b/` (read against
    # the names the other headers give), and lines after a hunk's counted lines, read as prose after the
    # section, as git apply reads them.
    parse_rows = (
        ("From 1 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] x\n\n---\n x.py | 2 +-\n\ndiff --git a/x.py b/x.py\n"
         "index 1..2 100644\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n x\n--- a/phantom.py\n+++ b/ghost.py\n"
         "@@ -9 +9 @@\n-y\n+z\n-- \n2.43.0\n", ["x.py"]),
        ("Subject: [PATCH] w\n\nbody\n---  \n w.py | 1 +\n\ndiff --git a/w.py b/w.py\n--- a/w.py\n+++ b/w.py\n"
         "@@ -1 +1,2 @@\n w\n+v\nNotes:\n    --- a/hidden.py\n    +++ b/hidden.py\n    @@ -1 +1 @@\n", ["w.py"]),
        ("diff --git a/g.bin b/g.bin\nindex 1..2 100644\nGIT binary patch\nliteral 4\nLcmZ?d00001\n\nliteral 0\n"
         "HcmV?d00001\n\ndiff --git a/h.txt b/h.txt\n--- a/h.txt\n+++ b/h.txt\n@@ -1 +1 @@\n-h\n+i\n",
         ["g.bin", "h.txt"]),
        ('diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"\n--- "a/caf\\303\\251.txt"\n'
         '+++ "b/caf\\303\\251.txt"\n@@ -1 +1 @@\n-c\n+d\ndiff --git a/with space.txt b/with space.txt\n'
         "--- a/with space.txt\t\n+++ b/with space.txt\t\n@@ -1 +1 @@\n-s\n+t\n",
         ["caf\u00e9.txt", "with space.txt"]),
        ("diff --git a/a.txt b/c.txt\nsimilarity index 90%\ncopy from a.txt\ncopy to c.txt\n--- a/a.txt\n"
         "+++ b/c.txt\n@@ -1 +1 @@\n-a\n+c\n", ["c.txt"]),
        ("diff --git a/o.txt b/n.txt\nsimilarity index 100%\nrename from o.txt\nrename to n.txt\n",
         ["n.txt", "o.txt"]),
        ("diff --git a/m.sh b/m.sh\nold mode 100644\nnew mode 100755\ndiff --git a/bin.dat b/bin.dat\n"
         "index 1..2 100644\nBinary files a/bin.dat and b/bin.dat differ\ndiff --git a/e.txt b/e.txt\n"
         "new file mode 100644\nindex 0000000..e69de29\n", ["bin.dat", "e.txt", "m.sh"]),
        ('diff --git "a/caf\\303\\251.sh" "b/caf\\303\\251.sh"\nold mode 100644\nnew mode 100755\n',
         ["caf\u00e9.sh"]),
        ("--- a/p.txt\t2026-10-15 10:00:00\n+++ b/p.txt\t2026-10-15 10:01:00\n@@ -1 +1 @@\n-p\n+q\n", ["p.txt"]),
        ("diff --git a/k.txt b/k.txt\n--- a/k.txt\n+++ b/k.txt\n@@ -1,3 +1,3 @@\n k\n\n-x\n"
         "\\ No newline at end of file\n+y\n\\ No newline at end of file\ndiff --git a/l.txt b/l.txt\n"
         "--- a/l.txt\n+++ b/l.txt\n@@ -1 +1 @@\n-l\n+m\n", ["k.txt", "l.txt"]),
        ('diff --git a/plain.txt "b/caf\\303\\251.txt"\nsimilarity index 100%\nrename from plain.txt\n'
         'rename to "caf\\303\\251.txt"\n', ["caf\u00e9.txt", "plain.txt"]),
        ("diff --git a/x b/y b/x b/y\n--- a/x b/y\t\n+++ b/x b/y\t\n@@ -1 +1 @@\n-a\n+b\n"
         "diff --git a/u b/v.txt b/w b/v.txt\nsimilarity index 100%\nrename from u b/v.txt\nrename to w b/v.txt\n",
         ["u b/v.txt", "w b/v.txt", "x b/y"]),
        ("diff --git a/x.txt b/x.txt\n--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\n+c\n-d\n e\n", ["x.txt"]))
    check("rdp/draft-paths-read-from-headers", [sorted(aiqt_hooks._rdp_draft_paths(t)[0] or ["(error)"])
                                                 for t, _w in parse_rows], [w for _t, w in parse_rows])
    # A pure rename has no --- or +++ line: its rename headers name both paths, so a commit that copies the
    # file instead of moving it leaves the source drafted but unapplied, unless that skip is recorded.
    dn = RdpFixture(base, "draftrename")
    dn.binding = dict(dn.binding, labels=labels)
    dn.write_registry(dn.binding)
    _rdp_git(dn.root, "checkout", "-q", "-b", "mv")
    _rdp_git(dn.root, "mv", "seed.txt", "kept.txt")
    (dn.root / "src" / "a.py").write_text("a = 3\n", encoding="utf-8")
    _rdp_git(dn.root, "commit", "-q", "-am", "rename")
    moved = _rdp_git(dn.root, "rev-parse", "HEAD")
    renamed = _draft(dn, "rename.diff", _rdp_draft_diff(dn.root, dn.pin, moved))
    dn.authority(moved + "\n")
    rn_text = Path(renamed).read_text(encoding="utf-8")
    rn = [_named(dn.dispatch(dn.good(pin=moved, paths=(), extra=["Review-draft: " + renamed])), "'seed.txt'")]
    _rdp_git(dn.root, "checkout", "-q", "-b", "cp", dn.pin)
    shutil.copyfile(str(dn.root / "seed.txt"), str(dn.root / "kept.txt"))
    (dn.root / "src" / "a.py").write_text("a = 3\n", encoding="utf-8")
    _rdp_git(dn.root, "add", "kept.txt", "src/a.py")
    _rdp_git(dn.root, "commit", "-q", "-m", "copy instead of rename")
    copied = _rdp_git(dn.root, "rev-parse", "HEAD")
    dn.authority(copied + "\n")
    for extra in ([], ["Review-skip: seed.txt"]):
        rn.append(_named(dn.dispatch(dn.good(pin=copied, paths=("kept.txt", "src/a.py"), extra=[
            "Review-draft: " + renamed] + extra)), "'seed.txt'"))
    check("rdp/draft-rename-names-both-paths", (
        "\nrename from seed.txt\nrename to kept.txt\n" in rn_text and "a/seed.txt\n" not in rn_text, rn),
        (True, [("allow", False), ("deny", True), ("allow", False)]))
    # The draft and skip labels are declared together or not at all, and their values join the labels
    # that must be distinct: a malformed binding withholds every Bash call.
    lb = RdpFixture(base, "draftlabels")
    lb_got = []
    for extra in (dict(draft="Review-draft:"), dict(skip="Review-skip:"), dict(draft="Review-draft:",
                                                                                skip="Review-path:")):
        lb.write_registry(dict(lb.binding, labels=dict(lb.binding["labels"], **extra)))
        lb_got.append(_rdp_kind(lb.run("ls")))
    lb.write_registry(dict(lb.binding, labels=labels))
    lb_got.append(_rdp_kind(lb.run("ls")))
    check("rdp/draft-labels-declared-together", lb_got, ["unverifiable"] * 3 + ["allow"])


def _rdp_ls(cwd, background):
    """The review dispatch verdict on a plain `ls` from cwd."""
    return _rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(cwd), session_id="s1", tool_name="Bash",
        tool_input=dict(command="ls", run_in_background=background))))


def _rdp_scope_cases(base, plain):
    """Scope vectors: a session with no registry anywhere is never withheld (a linked worktree of a bare
    repository, a failed common-directory probe, a git that echoes --git-common-dir back), and a bound
    session cannot switch the hook off through a plain command that writes or removes its registry."""
    src = base / "baresrc"
    _rdp_git(base, "init", "-q", "-b", "main", str(src))
    (src / "x.txt").write_text("x\n", encoding="utf-8")
    _rdp_git(src, "add", "x.txt")
    _rdp_git(src, "commit", "-q", "-m", "x")
    _rdp_git(base, "clone", "-q", "--bare", str(src), str(base / "bareproj.git"))
    _rdp_git(base / "bareproj.git", "worktree", "add", "-q", str(base / "bareproj-wt"), "main")
    check("rdp/bare-repository-linked-worktree-unbound-allows",
          [_rdp_ls(base / "bareproj-wt", bg) for bg in (False, True)], ["allow", "allow"])
    real_main, real_git = aiqt_hooks._rdp_main_worktree, aiqt_hooks._review_git

    def _probe_fails(root):
        raise OSError("git cannot read the common git directory of {}".format(root))

    def _old_git(repo, *args, stdin=None):
        # git before 2.5 echoes an option rev-parse does not know (--git-common-dir) as if it were a path.
        p = real_git(repo, *args, stdin=stdin)
        if p is not None and args[:1] == ("rev-parse",) and "--git-common-dir" in args and p.returncode == 0:
            p.stdout = b"--git-common-dir\n" + p.stdout.split(b"\n", 1)[-1]
        return p
    got = []
    try:
        aiqt_hooks._rdp_main_worktree = _probe_fails
        got += [_rdp_ls(plain, bg) for bg in (False, True)]
        aiqt_hooks._rdp_main_worktree = real_main
        aiqt_hooks._review_git = _old_git
        got += [_rdp_ls(plain, bg) for bg in (False, True)]
    finally:
        aiqt_hooks._rdp_main_worktree, aiqt_hooks._review_git = real_main, real_git
    check("rdp/unbound-repository-failed-probe-or-old-git-allows", got, ["allow"] * 4)
    # A plain command that writes, moves or removes the registry binding this session is refused; a read
    # of it, and a command naming other .aiqt paths, is not.
    rg = RdpFixture(base, "regdisarm")
    rg_bad = rg.brief(["Review-target: revision", "Reviewed-revision: HEAD"], "bad.txt")
    unbound = rg.briefs / "unbound.json"
    unbound.write_text(json.dumps(dict(version=1)), encoding="utf-8")
    local = rg.root / ".aiqt" / "orchestration.local.json"
    check("rdp/plain-registry-write-denies", [_rdp_kind(rg.run(c)) for c in (
        "cp " + str(unbound) + " .aiqt/orchestration.local.json", "git rm -q .aiqt/orchestration.json",
        "rm -rf .aiqt", "mv .aiqt/ elsewhere", "rm " + str(rg.root / ".AIQT" / "Orchestration.Local.JSON"),
        "ln -sf " + str(unbound) + " .aiqt/orchestration.local.json", "git checkout HEAD -- .aiqt/.",
        "cp " + str(unbound) + " --target-directory=.aiqt")], ["deny"] * 8)
    check("rdp/plain-registry-read-allows", [_rdp_kind(rg.run(c)) for c in (
        "cat .aiqt/orchestration.local.json", "ls .aiqt", "git diff .aiqt/core/x.toml")], ["allow"] * 3)
    check("rdp/registry-disarm-dispatch-still-denies", _rdp_kind(rg.dispatch(rg_bad)), "deny")
    # The binding in the committed registry, a bindingless local one beside it: the binding still scopes.
    reg = dict(version=1, state_dir=str(rg.briefs / "state"), review_dispatch=rg.binding)
    (rg.root / ".aiqt" / "orchestration.json").write_text(json.dumps(reg), encoding="utf-8")
    local.write_text(json.dumps(dict(version=1, state_dir=str(rg.briefs / "state"))), encoding="utf-8")
    check("rdp/bindingless-local-registry-cannot-hide-committed-binding", _rdp_kind(rg.dispatch(rg_bad)), "deny")
    # The .aiqt directory named without its plain name: a value glued to a short option (alone or in a
    # group), a quoted glob git expands itself, a pathspec after its magic, a subdirectory entered with -C
    # while another word climbs with .., and a .. resolved against a cwd inside .aiqt. Other .aiqt paths,
    # and prose holding a glob character, are not refused.
    (rg.root / ".aiqt" / "core").mkdir()
    check("rdp/plain-registry-indirect-name-denies", [_rdp_kind(rg.run(c)) for c in (
        "git rm -rfq '.aiq*'", "git clean -fdxq '.aiq*'", "git -C .aiqt/core rm -rfq ..",
        "cp -rt.aiqt " + str(rg.briefs) + "/.", "mv -ft.aiqt x", "git rm -rq ':/.aiqt'",
        "git rm -rq ':(icase).AIQT'", "git rm -rq '.aiqt/orch*'", "git rm -rq '.a?qt/'")] + [
        _rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(rg.root / ".aiqt" / "core"), session_id="s1", tool_name="Bash",
            tool_input=dict(command=c, run_in_background=False)))) for c in ("rm -rf ..", "git rm -rq -C..")],
        ["deny"] * 11)
    check("rdp/plain-aiqt-subpath-and-glob-prose-allow", [_rdp_kind(rg.run(c)) for c in (
        "git add .aiqt/core/x.toml", "git commit -m 'why?'", "touch .aiqt/core/x.toml", "git rm -q '*.py'")],
        ["allow"] * 4)
    # A plain git command that moves the repository or its work tree off the bound registry (core.worktree,
    # core.bare, a configuration include, a separated git directory) is refused; a read of the key is not.
    check("rdp/plain-git-repository-move-denies", [_rdp_kind(rg.run(c)) for c in (
        "git config core.worktree " + str(base), "git config --worktree Core.WorkTree " + str(base),
        "git config core.bare true", "git config --unset core.worktree", "git config set core.bare true",
        "git config include.path " + str(unbound), "git config includeIf.gitdir:/x/.path " + str(unbound),
        "git init -q --separate-git-dir=" + str(base / "regdisarm-meta"), "git -c core.worktree=/x config x y",
        "git -c core.bare=true branch --list")],
        ["deny"] * 10)
    # git config is judged by position: only --get, --get-all, --get-regexp, --list or -l (after modifiers,
    # --show-origin among them), with the operands each takes, or one key alone, reads.
    reads = ("git config --get core.worktree", "git config --list", "git config core.bare",
             "git config --show-origin --list", "git config --global --get-all core.worktree",
             "git config --get core.worktree get", "git config --show-origin core.worktree")
    check("rdp/plain-git-repository-read-allows", [_rdp_kind(rg.run(c)) for c in reads], ["allow"] * len(reads))
    # Any other git config form naming repository configuration is a write: a word after the key is its
    # value (git config core.worktree get sets core.worktree to get), a rename into the core or include
    # section moves a key there, an edit action opens an editor that can set any key, and a subcommand
    # behind an unknown global option is judged the same way.
    writes = ("git config core.worktree get", "git config core.bare true list", "git config get core.bare",
              "git config --worktree core.worktree /x get", "git config --show-origin core.worktree /x",
              "git config core.worktree --get", "git config --worktree --rename-section x core",
              "git config --rename-section x Include", "git config rename-section x includeIf.gitdir:/x/",
              "git config --edit", "git config -e", "git config edit", "git config --ed",
              "git --exec-path=/x config core.worktree get", "git --exec-path=/x config --rename-section x core",
              "git -c core.worktree=/x config --list", "git --config-env=core.bare=V config --list")
    check("rdp/plain-git-config-judged-by-position", [_rdp_kind(rg.run(c)) for c in writes],
          ["deny"] * len(writes))
    # A git subcommand that only reads (status, log, diff, show, rev-parse, ls-files, blame, grep, cat-file)
    # treats every word as data: a repository key, a registry name or a glob in it is no move and no write.
    data = ("git log -S core.worktree --oneline", "git log -1 --format='tformat:*'", "git ls-files '*'",
            "git grep core.worktree", "git log --grep=core.bare", "git log -S include.path", "git diff -- '*'",
            "git show HEAD:.aiqt/orchestration.local.json", "git -C .aiqt/core log -- ..",
            "git blame -- .aiqt/orchestration.local.json", "git cat-file -p HEAD:seed.txt",
            "git rev-parse --git-path core.worktree", "git status -- .aiqt", "git --no-pager log -- '.aiq*'")
    check("rdp/plain-git-read-words-are-data", [_rdp_kind(rg.run(c)) for c in data], ["allow"] * len(data))
    # A read subcommand that writes a file (--output, abbreviated or not), runs a program on its matches
    # (grep -O, --open-files-in-pager) or takes configuration from a -c or --config-env global is judged as
    # any other git command; so is a subcommand off the read list, or one not written exactly.
    judged = ("git log -1 --format=%B --output=.aiqt/orchestration.local.json",
              "git diff --outp=.aiqt/orchestration.local.json", "git show --output .aiqt/orchestration.json",
              "git grep -O/bin/rm -l review .aiqt", "git grep -lO/bin/rm review .aiqt",
              "git grep --open-files-in-pager=/bin/rm -l review .aiqt", "git -c core.pager=x log -- '.aiq*'",
              "git -c x.y=z log -S core.worktree", "git LOG -S core.worktree", "git checkout -- '*'",
              "git commit -m 'document core.worktree handling'", "git init -q --separate-git-dir=/tmp/x")
    check("rdp/plain-git-read-that-writes-or-runs-judged", [_rdp_kind(rg.run(c)) for c in judged],
          ["deny"] * len(judged))
    # Configuration can make any git command, a read among them, run a program, so no key list closes it:
    # the external diff sequence (set diff.external to rm -f, then diff the registry with --ext-diff), each
    # call run only where the hook allows it, would delete the registry and let a missing-brief dispatch
    # through; every git config write and -c or --config-env global is refused, whatever the key.
    ed = RdpFixture(base, "extdiff")
    (ed.root / "other").write_text("different\n", encoding="utf-8")
    ed_got = []
    for command, args in (("git config diff.external 'rm -f'", ["config", "diff.external", "rm -f"]),
                          ("git diff --no-index --ext-diff .aiqt/orchestration.local.json other",
                           ["diff", "--no-index", "--ext-diff", ".aiqt/orchestration.local.json", "other"])):
        ed_got.append(_rdp_kind(ed.run(command)))
        if ed_got[-1] == "allow":
            subprocess.run(["git", "-C", str(ed.root)] + args, capture_output=True, timeout=30)
    ed_got += [_rdp_kind(ed.dispatch("/missing")), (ed.root / ".aiqt" / "orchestration.local.json").is_file()]
    check("rdp/git-external-diff-sequence-cannot-remove-registry", ed_got,
          ["deny", "deny", "unverifiable", True])
    keys = ("git config core.pager 'rm -f'", "git config alias.l '!rm -f x'", "git config core.fsmonitor x",
            "git config --global diff.x.textconv x", "git config filter.x.smudge x", "git config set diff.external x",
            "git config --file x diff.external x", "git config --add core.hooksPath x",
            "git config --unset diff.external", "git -c diff.external=x status", "git -c x.y=z commit -m x",
            "git --config-env=core.pager=V status", "git --exec-path=/x -c diff.external=x status",
            "git --exec-path=/x config diff.external x")
    check("rdp/plain-git-config-write-any-key-denies", [_rdp_kind(ed.run(c)) for c in keys], ["deny"] * len(keys))
    # A read carrying an option that runs a configured or named program (an external diff, a textconv or
    # smudge filter, a patch a textconv applies to, a signature check, the pager) gets the full checks.
    runs = ("git diff --textconv .aiqt/orchestration.local.json other", "git diff --textc -- .aiqt",
            "git show --textconv HEAD:.aiqt/orchestration.local.json", "git cat-file --textconv HEAD:.aiqt/orchestration.json",
            "git cat-file --filters HEAD:.aiqt/orchestration.json", "git grep --textconv x .aiqt", "git blame --textconv -- .aiqt",
            "git log -p -- .aiqt", "git log --patch -- .aiqt", "git show -U3 -- .aiqt", "git log -pS x -- .aiqt",
            "git diff --ext -- .aiqt", "git log --cc -- .aiqt", "git log --word-diff -- .aiqt",
            "git log --show-signature -- .aiqt", "git log --format='%G?' -- .aiqt", "git -p log -- .aiqt",
            "git --paginate status .aiqt")
    check("rdp/plain-git-read-running-program-judged", [_rdp_kind(ed.run(c)) for c in runs], ["deny"] * len(runs))
    # A plain command that may write into a git directory or a git configuration file (a configuration
    # file or hook copied, linked or moved into place) is refused; reads of them, and a read naming the
    # registry with no program-running option, are not.
    gitdir = ("cp other .git/config", "ln -sf other .git/hooks/post-index-change", "mv other .GIT/config",
              "git show HEAD:seed.txt --output=.git/config", "cp other x/.gitconfig",
              "cp other x/.config/git/config", "git init -q --template=/x", "cp -rT other '.gi?'",
              "chmod +x .git/hooks/pre-commit", "git -C .git commit -m x")
    check("rdp/plain-git-dir-write-denies", [_rdp_kind(ed.run(c)) for c in gitdir], ["deny"] * len(gitdir))
    quiet = ("git config --get diff.external", "git config --list", "ls .git", "test -d .git",
             "cat .git/config", "git diff --stat -- .aiqt/orchestration.local.json",
             "git log --oneline -- .aiqt/orchestration.local.json", "git diff --text -- .aiqt",
             "git log --no-ext-diff --no-textconv -S x -- .aiqt", "git status")
    check("rdp/plain-git-read-without-program-allows", [_rdp_kind(ed.run(c)) for c in quiet],
          ["allow"] * len(quiet))
    # A git option or subcommand that runs a program can run shell text no word check sees (.aiqt inside
    # a pager command), so it is refused outright, whatever the command names: the pager sequence (grep's
    # --open-files-in-pager set to rm -rf .aiqt), each call run only where the hook allows it, would delete
    # the registry and let a missing-brief dispatch through.
    pg = RdpFixture(base, "pager")
    pg_got = [_rdp_kind(pg.dispatch("/missing"))]
    pg_got.append(_rdp_kind(pg.run("git grep --open-files-in-pager='rm -rf .aiqt' a -- src/a.py")))
    if pg_got[-1] == "allow":
        subprocess.run(["git", "-C", str(pg.root), "grep", "--open-files-in-pager=rm -rf .aiqt", "a", "--",
                        "src/a.py"], capture_output=True, timeout=30)
    pg_got += [_rdp_kind(pg.dispatch("/missing")), (pg.root / ".aiqt" / "orchestration.local.json").is_file()]
    check("rdp/git-pager-option-sequence-cannot-remove-registry", pg_got,
          ["unverifiable", "deny", "unverifiable", True])
    # The checks below use a fixture of their own, so a registry removed above cannot make them pass.
    go = RdpFixture(base, "gitops")
    programs = ("git grep -O'rm -rf x' a", "git grep -iO a", "git grep --open a", "git grep -e a -O",
                "git -p status", "git --paginate log", "git diff --ext-diff", "git log --textconv",
                "git cat-file --filters HEAD:seed.txt", "git log --show-signature", "git log --format='%G?'",
                "git for-each-ref --format '%(signature)'", "git rebase -x 'rm -rf x' HEAD",
                "git rebase --exec=x HEAD", "git difftool -x x", "git difftool --extcmd=x",
                "git clone -u x a b", "git clone --config core.fsmonitor=x a b", "git clone -c x=y a b",
                "git fetch --upload-pack=x origin", "git push --receive-pack=x origin",
                "git archive --remote=x --exec=y HEAD", "git send-email --sendmail-cmd=x a",
                "git --exec-path=/x status", "git bisect run x", "git submodule foreach x",
                "git filter-branch --tree-filter x", "git --no-advice rebase -x x HEAD",
                "git commit -S -m x", "git commit --gpg-sign -m x", "git commit -p", "git commit --trailer a=b -m x",
                "git tag -s v1 -m x", "git tag -v v1", "git tag -u k v1", "git tag --sign v1",
                "git merge -s x y", "git merge --strategy=x y", "git merge --verify-signatures y", "git merge -S y",
                "git rebase -s x y", "git rebase -S y", "git cherry-pick --strategy=x y", "git cherry-pick -S y",
                "git revert --gpg-sign y", "git push --signed origin", "git add -p", "git add -i",
                "git add --interactive", "git checkout -p", "git restore --patch x", "git reset -p",
                "git stash -p", "git fetch evil::x", "git clone evil::x d", "git remote add o evil::x",
                "git push --repo=evil::x", "git log --help", "git diff --ext-diff --",
                "git log --show-signature --", "git log --grep -- --ext-diff", "git log -n -- --show-signature",
                "git commit -m -- -S", "git commit --mess -- --gpg-sign", "git diff --cached --ext-diff",
                "git grep -e -e -e -- -O", "git commit --mess -m -m -- -S", "git blame -n -L -- --ext-diff",
                "git stash show -- --ext-diff", "git clone -- evil::x d", "git commit --mess --mess --mess -- -S",
                "git blame -L -- --ext-diff", "git shortlog --committer --grep -- --ext-diff",
                "git commit --allow-empty --mess --mess --mess -- -S", "git blame -CS --ext-diff f",
                "git blame -MS --ext-diff f", "git shortlog -wG --ext-diff",
                "git show --word-diff-regex --grep --ext-diff", "git log -p --word-diff-regex -S --ext-diff",
                "git show HEAD --default --grep --ext-diff", "git log -p --output --grep --ext-diff",
                "git show -s --word-diff-regex -S --show-signature", "git log --since-as-filter --author --ext-diff",
                "git diff --word-diff-regex --grep --ext-diff", "git rev-list --default --grep --ext-diff HEAD",
                "git shortlog --since-as-filter --grep --ext-diff", "git blame --word-diff-regex -S --ext-diff f",
                "git log --word-diff-regex -- --ext-diff", "git show --word-diff-reg --grep --ext-diff",
                "git log --oneline --default --grep --ext-diff", "git log -1 --format=%-GG",
                "git show -s --pretty=tformat:%+GK", "git log -1 --format='% GT' -- .aiqt",
                "git log --pretty tformat:%-GS", "git log --format='%<(9)% GF'",
                "git for-each-ref --format='%(*signature)'", "git tag -l --format '%(*signature:grade)'",
                "git for-each-ref --sort=signature", "git for-each-ref --sort=signature:grade",
                "git for-each-ref --sort=-signature", "git for-each-ref --sort='*signature'",
                "git for-each-ref --sort='-*signature:signer'", "git for-each-ref --sort=v:signature",
                "git for-each-ref --sort=version:signature:key", "git for-each-ref --so=signature",
                "git for-each-ref --sor signature", "git for-each-ref --sort=refname --sort=signature",
                "git for-each-ref --sort refname --sort -signature", "git for-each-ref --no-sort --sort=signature",
                "git branch --sort=signature", "git branch --so=signature:grade", "git tag --sort='*signature'",
                "git tag -l --sor '*signature'", "git for-each-ref --sort=contents:signature",
                "git shortlog --group=%GG HEAD", "git shortlog --gr=%GG HEAD", "git shortlog --g='%G?' HEAD",
                "git shortlog --group %GG HEAD", "git shortlog --group --format=%GG HEAD",
                "git shortlog --group=format:%GS HEAD", "git shortlog --group=committer --group=%GK HEAD",
                "git shortlog -c --group %-GG HEAD")
    pr_got = []
    for c in programs:
        result = go.run(c)
        specific = result[1].get("hookSpecificOutput") if isinstance(result[1], dict) else None
        reason = specific.get("permissionDecisionReason", "") if isinstance(specific, dict) else ""
        pr_got.append((_rdp_kind(result), "runs a program" in reason))
    check("rdp/plain-git-program-option-denies", pr_got, [("deny", True)] * len(programs))
    # Options are read with their operands: a search operand glued to -S or -G, or the next word of
    # --grep, --author or -S, is no patch request (git log -Sconfig reads, its c no -c), and words after
    # -- are paths, also after an option taking no value or with its value glued (--stat, --cached, -1,
    # --format=oneline, -n5), or after a word that is itself an option's value, separated as attached
    # (git grep -e -e -- -O as git grep -e-e -- -O); an option that shares a letter or prefix with a
    # program-running one runs none.
    operands = ("git log -Sconfig -- .aiqt", "git log -S config -- .aiqt", "git log -Gcmp -- .aiqt",
                "git log --grep -c -- .aiqt", "git log --author -p -- .aiqt", "git log -n 3 -- .aiqt",
                "git log -- .aiqt -p", "git grep -e -O -- .aiqt", "git grep -eOops -- .aiqt",
                "git cat-file --filter=blob:none --batch-check", "git commit -m 'fix -x and %G'",
                "git rebase -i HEAD", "git --exec-path", "git log --grep --ext-diff",
                "git log --grep=--ext-diff", "git show --author --show-signature", "git commit -m --gpg-sign",
                "git grep -A 1 -e -O", "git tag -m -s v1", "git log --grep --help", "git clone -o -u a b",
                "git log --grep --format=%G --format=%s", "git diff -- --ext-diff",
                "git log -- --show-signature", "git log HEAD -- --show-signature", "git grep a -- -O",
                "git diff --stat -- --ext-diff", "git diff --cached -- --ext-diff", "git log -1 -- --show-signature",
                "git log --format=oneline -- --show-signature", "git log -n5 -- --show-signature",
                "git commit -a -- -S", "git grep -e -e -- -O", "git grep -e-e -- -O",
                "git log --grep --author -- --show-signature", "git log --grep=--author -- --show-signature",
                "git log -S -n -- --ext-diff", "git log -S-n -- --ext-diff", "git commit --mess --mess -- -S",
                "git blame -n -- --ext-diff", "git blame -L 1,2 -- --ext-diff", "git shortlog -n -- --ext-diff",
                "git blame -G -G -- --ext-diff", "git commit --mess --gpg-sign", "git shortlog -G -G -- --ext-diff",
                "git blame -I -O -- --ext-diff", "git for-each-ref --sort=refname", "git for-each-ref --sort refname",
                "git branch --sort=-committerdate", "git tag -l --sort=v:refname", "git shortlog --group=author HEAD",
                "git shortlog --group author --group=trailer:x HEAD", "git shortlog --group=%an HEAD",
                "git shortlog --grep %GG HEAD", "git log --grep signature", "git tag -m signature v1",
                "git for-each-ref --sort=refname signature", "git shortlog --group=author %GG",
                "git for-each-ref --sort refname signature", "git shortlog --group author %GG")
    check("rdp/plain-git-option-operands-parsed", [_rdp_kind(go.run(c)) for c in operands],
          ["allow"] * len(operands))
    patches = ("git log -Sx -p -- .aiqt", "git log -pSconfig -- .aiqt", "git log -cS x -- .aiqt",
               "git show -U3 -- .aiqt", "git log --grep=x --patch -- .aiqt", "git log --default --grep -p -- .aiqt",
               "git show --since-as-filter -S -p -- .aiqt", "git log --default -- -p -- .aiqt")
    check("rdp/plain-git-patch-read-judged", [_rdp_kind(go.run(c)) for c in patches], ["deny"] * len(patches))
    # A git config read keeps its scope options (--local, --global, --system, --worktree, --file PATH),
    # --show-origin and --type before any read form; a trailing word after the key is still a value.
    config_reads = ("git config --show-origin --get core.bare", "git config --local core.bare",
                    "git config --local --show-origin core.bare", "git config --system --get core.bare",
                    "git config --worktree core.bare", "git config --global --list",
                    "git config --file other --get core.bare", "git config -f other core.bare",
                    "git config --file=.git/config core.bare", "git config --type bool --get core.bare",
                    "git config --show-scope --show-origin --get-all core.bare")
    check("rdp/plain-git-config-read-modifiers-allow", [_rdp_kind(go.run(c)) for c in config_reads],
          ["allow"] * len(config_reads))
    config_writes = ("git config core.bare --local", "git config --local core.bare true",
                     "git config --file core.bare", "git config --show-origin core.bare false",
                     "git config --type bool core.bare true", "git config --file x --unset core.bare")
    check("rdp/plain-git-config-trailing-word-still-writes", [_rdp_kind(go.run(c)) for c in config_writes],
          ["deny"] * len(config_writes))
    # A git subcommand off the allowlist is refused whatever it names: the instaweb sequence (its --httpd
    # set to rm -rf .aiqt, run only where the hook allows it) would delete the registry and let a
    # missing-brief dispatch through.
    iw = RdpFixture(base, "instaweb")
    iw_got = [_rdp_kind(iw.dispatch("/missing")),
              _rdp_kind(iw.run("git instaweb --httpd='rm -rf .aiqt lighttpd' --start"))]
    if iw_got[-1] == "allow":
        subprocess.run(["git", "-C", str(iw.root), "instaweb", "--httpd=rm -rf .aiqt lighttpd", "--start"],
                       capture_output=True, timeout=30)
    iw_got += [_rdp_kind(iw.dispatch("/missing")), (iw.root / ".aiqt" / "orchestration.local.json").is_file()]
    check("rdp/git-instaweb-sequence-cannot-remove-registry", iw_got, ["unverifiable", "deny", "unverifiable", True])
    off_list = (("git instaweb --start", "instaweb"), ("git difftool", "difftool"), ("git mergetool", "mergetool"),
                ("git send-email --to=a x", "send-email"), ("git filter-branch", "filter-branch"),
                ("git bisect start", "bisect"), ("git submodule status", "submodule"), ("git daemon", "daemon"),
                ("git web--browse x", "web--browse"), ("git credential fill", "credential"),
                ("git credential-store get", "credential-store"), ("git credential-cache exit", "credential-cache"),
                ("git help log", "help"), ("git co main", "co"), ("git my-external", "my-external"),
                ("git LOG", "LOG"), ("git --no-advice status", "--no-advice"), ("git init", "init"),
                ("git clean -n", "clean"), ("git gc", "gc"), ("git --help", "--help"))
    off_got = []
    for c, word in off_list:
        result = go.run(c)
        specific = result[1].get("hookSpecificOutput") if isinstance(result[1], dict) else None
        reason = specific.get("permissionDecisionReason", "") if isinstance(specific, dict) else ""
        off_got.append((_rdp_kind(result), "({})".format(word) in reason))
    check("rdp/plain-git-off-allowlist-subcommand-denies", off_got, [("deny", True)] * len(off_list))
    allowed = ("git status", "git log", "git diff", "git show", "git rev-parse HEAD", "git ls-files", "git ls-tree HEAD",
               "git cat-file -t HEAD", "git blame seed.txt", "git grep x", "git describe", "git shortlog",
               "git merge-base HEAD HEAD", "git rev-list HEAD", "git for-each-ref", "git show-ref", "git branch",
               "git tag", "git remote", "git config --list", "git add x", "git rm x", "git mv x y", "git commit -m x",
               "git push", "git fetch", "git stash", "git switch main", "git checkout main", "git restore x",
               "git reset", "git merge x", "git rebase x", "git cherry-pick x", "git revert x", "git worktree list",
               "git clone a b", "git", "git --version")
    check("rdp/plain-git-allowlisted-subcommand-allows", [_rdp_kind(go.run(c)) for c in allowed],
          ["allow"] * len(allowed))
    # The binding record fails closed: once the hook has seen the registry bind, a registry
    # removed or rewritten by any command leaves the next dispatch withheld as UNVERIFIABLE, its reason naming
    # the binding record, never allowed. Each change is made directly, as a command the hook did not judge
    # would make it, after one dispatch has been checked under the binding.
    def _reason(result):
        specific = result[1].get("hookSpecificOutput") if isinstance(result[1], dict) else None
        return specific.get("permissionDecisionReason", "") if isinstance(specific, dict) else ""

    def _drifted(name, prepare, change):
        fx = RdpFixture(base, name)
        prepare(fx)
        first = _rdp_kind(fx.dispatch("/missing"))
        change(fx)
        after = fx.dispatch("/missing")
        return [first, aiqt_hooks._rdp_scope(str(fx.root))[0], _rdp_kind(after), "binding record" in _reason(after)]

    def _payload(fx):
        fx.ignore_registry()
        (fx.root / "payload" / ".aiqt").mkdir(parents=True)
        (fx.root / "payload" / ".aiqt" / "orchestration.local.json").write_text(json.dumps(dict(version=1)),
                                                                                encoding="utf-8")
        _rdp_git(fx.root, "add", "-f", "payload")
        _rdp_git(fx.root, "commit", "-q", "-m", "payload")

    def _tracked(fx):
        _rdp_git(fx.root, "branch", "noreg")
        _rdp_git(fx.root, "add", "-f", ".aiqt/orchestration.local.json")
        _rdp_git(fx.root, "commit", "-q", "-m", "track the registry")
    changes = (
        ("record-rm-tree", lambda fx: None,
         lambda fx: (_rdp_git(fx.root, "add", "-A"), _rdp_git(fx.root, "rm", "-rfq", "."))),
        ("record-restore-subtree", _payload, lambda fx: _rdp_git(fx.root, "restore", "--source=HEAD:payload", ".")),
        ("record-checkout-subtree", _payload, lambda fx: _rdp_git(fx.root, "checkout", "HEAD:payload", "--", ".")),
        ("record-reset-branch", _tracked, lambda fx: _rdp_git(fx.root, "reset", "-q", "--hard", "noreg")),
        ("record-rm-aiqt", lambda fx: None, lambda fx: shutil.rmtree(fx.root / ".aiqt")))
    check("rdp/record-registry-removed-withholds-dispatch", [_drifted(n, p, c) for n, p, c in changes],
          [["unverifiable", None, "unverifiable", True]] * len(changes))
    rebound = _drifted("record-rebind", lambda fx: None,
                       lambda fx: fx.write_registry(dict(fx.binding, commands=["other-dispatch"])))
    check("rdp/record-registry-rewritten-withholds-dispatch", rebound, ["unverifiable", "ok", "unverifiable", True])
    # The record is hook-owned state in the common git directory, mode 0600, holding the binding; an operator
    # clears it outside the session and the next check records the binding then in force. In the session a
    # plain command naming it is refused, and a plain command naming neither is still allowed while the
    # registry is changed.
    rb = RdpFixture(base, "record-operator")
    rb.dispatch("/missing")
    home = rb.root / ".git" / "aiqt" / "review-dispatch-binding"
    records = sorted(home.iterdir()) if home.is_dir() else []
    record = records[0] if len(records) == 1 else home / "absent.json"
    modes = [oct(path.stat().st_mode & 0o777) for path in [home] + records if path.exists()]
    doc = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
    shutil.rmtree(rb.root / ".aiqt")
    in_session = [_rdp_kind(rb.run(c)) for c in ("rm " + str(record), "rm -rf .git/aiqt", "ls", "git status")]
    (rb.root / ".aiqt").mkdir()
    rb.write_registry(dict(rb.binding, commands=["other-dispatch"]))
    still = _rdp_kind(rb.dispatch("/missing"))
    record.unlink(missing_ok=True)
    cleared = rb.run("other-dispatch --brief /missing")
    check("rdp/record-location-and-operator-clear",
          [len(records), modes,
           [doc.get("worktree"), doc.get("registry_path")] == [".", "."], doc.get("binding", {}).get("commands"),
           in_session, still, _rdp_kind(cleared), "binding record" in _reason(cleared),
           json.loads(record.read_text(encoding="utf-8"))["binding"]["commands"] if record.is_file() else None],
          [1, ["0o700", "0o600"], True, ["orch-dispatch"], ["deny", "deny", "allow", "allow"], "unverifiable", "unverifiable",
           False, ["other-dispatch"]])
    # Ordinary git commands are allowed whatever the registry's git state: tracked with an older registry
    # commit in its history, or untracked and not ignored; the registry stays bound and nothing is withheld.
    def _ordinary(name, track):
        fx = RdpFixture(base, name)
        reg = fx.root / ".aiqt" / "orchestration.local.json"
        if track:
            _rdp_git(fx.root, "add", "-f", ".aiqt/orchestration.local.json")
            _rdp_git(fx.root, "commit", "-q", "-m", "track the registry")
            reg.write_bytes(reg.read_bytes() + b"\n")
            _rdp_git(fx.root, "commit", "-q", "-a", "-m", "touch the registry")
        (fx.root / "src" / "a.py").write_text("a = 3\n", encoding="utf-8")
        got = []
        for c in ("git add src/a.py", "git commit -q -m edit", "git checkout HEAD -- src/a.py", "git restore src/a.py",
                  "git reset --hard HEAD", "git checkout -- .", "git restore .", "git reset -q", "git checkout -q -b side",
                  "git switch -q main"):
            got.append(_rdp_kind(fx.run(c)))
            if got[-1] == "allow":
                subprocess.run(["git", "-C", str(fx.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                                "-c", "commit.gpgsign=false"] + c.split(" ")[1:], capture_output=True, timeout=30)
        after = fx.dispatch("/missing")
        return got + [aiqt_hooks._rdp_scope(str(fx.root))[0], _rdp_kind(after), "binding record" in _reason(after)]
    check("rdp/record-ordinary-git-commands-allow", [_ordinary("record-ord-tracked", True),
                                                     _ordinary("record-ord-untracked", False)],
          [["allow"] * 10 + ["ok", "unverifiable", False]] * 2)
    # A record that cannot be read leaves the recorded dispatch commands unknown, so every Bash call is
    # withheld; a binding whose record cannot be written withholds every dispatch, naming the record.
    bad = RdpFixture(base, "record-malformed")
    bad.dispatch("/missing")
    for path in (bad.root / ".git" / "aiqt" / "review-dispatch-binding").glob("*.json"):
        path.write_text("{", encoding="utf-8")
    nw = RdpFixture(base, "record-unwritable")
    (nw.root / ".git" / "aiqt").write_text("x\n", encoding="utf-8")
    nw_result = nw.dispatch("/missing")
    check("rdp/record-unreadable-or-unwritable-withholds",
          [_rdp_kind(bad.run("ls")), _rdp_kind(nw.run("ls")), _rdp_kind(nw_result),
           "cannot be recorded" in _reason(nw_result)], ["unverifiable", "allow", "unverifiable", True])
    # A linked worktree is guarded by its main worktree's record, kept in the shared common git directory.
    lw = RdpFixture(base, "record-linked")
    _rdp_git(lw.root, "worktree", "add", "-q", str(base / "record-linked-wt"), "-b", "wt")
    linked = dict(hook_event_name="PreToolUse", cwd=str(base / "record-linked-wt"), session_id="s1",
                  tool_name="Bash", tool_input=dict(command="orch-dispatch --brief /missing"))
    lw_first = _rdp_kind(aiqt_hooks.review_dispatch_pin(dict(linked)))
    shutil.rmtree(lw.root / ".aiqt")
    lw_after = aiqt_hooks.review_dispatch_pin(dict(linked))
    check("rdp/record-linked-worktree-main-registry-removed-withholds",
          [lw_first, _rdp_kind(lw_after), "binding record" in _reason(lw_after)], ["unverifiable", "unverifiable", True])
    # The record is keyed by the worktree's identity in its common git directory and the registry's path
    # relative to the top level, never an absolute path, so a renamed repository keeps its record: the
    # bound registry is committed, reset away to the registry-free revision, the repository is renamed
    # outside the session (in it, mv of a directory holding the git directory is refused), and the
    # dispatch from the renamed repository is still withheld, naming the record.
    rn = RdpFixture(base, "record-rename")
    _tracked(rn)
    rn_first = _rdp_kind(rn.dispatch("/missing"))
    _rdp_git(rn.root, "reset", "-q", "--hard", "noreg")
    renamed = base / "record-renamed"
    rn_mv = _rdp_kind(rn.run("mv " + str(rn.root) + " " + str(renamed)))
    os.rename(rn.root, renamed)
    rn_after = aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(renamed), session_id="s1", tool_name="Bash",
        tool_input=dict(command="orch-dispatch --brief /missing", run_in_background=False)))
    check("rdp/record-renamed-repository-withholds",
          [rn_first, rn_mv, _rdp_kind(rn_after), "binding record" in _reason(rn_after),
           str(renamed / ".git" / "aiqt") in _reason(rn_after)], ["unverifiable", "deny", "unverifiable", True, True])
    # The protected directory is the RESOLVED common git directory, whatever its spelling: with a separated
    # git directory not named .git, every plain command but a read whose operand is it, lies inside it or
    # holds it is refused (absolute, relative, through a symbolic link, a parent), a read and ordinary work
    # are not, and a registry removed outside the session still withholds the dispatch through the record.
    sg = RdpFixture(base, "record-sepgit")
    sg_meta = base / "record-sepgit-meta"
    _rdp_git(sg.root, "init", "-q", "--separate-git-dir=" + str(sg_meta))
    sg_first = _rdp_kind(sg.dispatch("/missing"))
    sg_home = sg_meta / "aiqt" / "review-dispatch-binding"
    sg_records = sorted(sg_home.iterdir()) if sg_home.is_dir() else []
    (base / "record-sepgit-alias").symlink_to(sg_meta)
    refused = ["rm -rf " + str(sg_meta / "aiqt"), "rm -rf " + str(sg_meta), "rm -rf ../record-sepgit-meta/aiqt",
               "rm -rf " + str(base / "record-sepgit-alias") + "/aiqt", "mv " + str(sg_meta) + " x",
               "chmod -R 000 " + str(sg_meta), "rm -rf " + str(base), "touch " + str(sg_records[0] if sg_records
                                                                                      else sg_home),
               "cp seed.txt " + str(sg_home), "ln -sf /dev/null " + str(sg_home / "x.json"),
               "rm -rf ../RECORD-SEPGIT-META/AIQT", "git -C " + str(sg_meta) + " rm -rq aiqt",
               "git diff --output=" + str(sg_home / "x.json")]
    allowed = ["ls " + str(sg_home), "cat " + str(sg_records[0] if sg_records else sg_home), "git add .",
               "git status", "mkdir build", "rm -rf build", "touch seed.txt"]
    sg_refused = [_rdp_kind(sg.run(c)) for c in refused]
    sg_allowed = [_rdp_kind(sg.run(c)) for c in allowed]
    shutil.rmtree(sg.root / ".aiqt")
    sg_after = sg.dispatch("/missing")
    check("rdp/record-separated-git-dir-protected-by-resolution",
          [sg_first, len(sg_records), sg_refused, sg_allowed, _rdp_kind(sg_after), "binding record" in _reason(sg_after)],
          ["unverifiable", 1, ["deny"] * len(refused), ["allow"] * len(allowed), "unverifiable", True])
    # Where the git directory cannot be located, every plain command but a read is refused in a bound session.
    check("rdp/record-unresolved-git-dir-refuses",
          [aiqt_hooks._rdp_names_common_dir(["rm", "x"], str(sg.root), (None, "no git directory")) is not None,
           aiqt_hooks._rdp_names_common_dir(["cat", "x"], str(sg.root), (None, "no git directory")),
           aiqt_hooks._rdp_names_common_dir(["rm", "x"], str(sg.root), (None, None))], [True, None, None])
    # A directory HOLDING the git directory (the repository root, for .git inside it) is refused only to a
    # command that can delete, move or recursively rewrite it: rm -r or -d, rmdir, mv of it, mv --exchange,
    # chmod, cp -r or -a of it, a write whose path merges into it (x/., -T) or resolves into the git
    # directory through a link, and a word the option tables do not model. Writing a new file or directory
    # into it, and ln naming it (ln never recurses), are allowed.
    an = RdpFixture(base, "record-ancestor")
    an_first = _rdp_kind(an.dispatch("/missing"))
    (base / "record-ancestor-src" / "g").mkdir(parents=True)
    (an.root / "sub").mkdir()
    (an.root / "sub" / "g").symlink_to(an.root / ".git")
    ext = str(base / "record-ancestor-src")
    refused = ["rm -rf .", "rm -r " + str(an.root), "rm -d .", "rmdir .", "rm --rec .", "rm -rf -- ../record-ancestor",
               "mv " + str(an.root) + " " + str(base / "record-ancestor-moved"), "mv -t " + str(base) + " .",
               "mv --exchange seed.txt .", "chmod -R 755 .", "chmod 755 .", "cp -a . " + ext + "/copy",
               "cp -rl . " + ext + "/links", "cp -r " + ext + "/. .", "cp -rT " + ext + " .",
               "cp -r " + ext + "/g sub", "ln -sfT seed.txt .", "cp -b -S /../x seed.txt .",
               "cp --frob seed.txt ."]
    allowed = ["cp seed.txt .", "touch .", "touch ./f", "touch " + str(an.root), "mkdir newdir", "mkdir -p .",
               "ln -s seed.txt ./l", "ln -s . self", "ln -sr . self2", "cp -r src .", "mv seed.txt .",
               "cp -t . seed.txt", "rm -f .", "rm -rf build", "mv -- old.txt .", "cp -a src " + ext + "/s"]
    an_refused = [_rdp_kind(an.run(c)) for c in refused]
    an_allowed = [_rdp_kind(an.run(c)) for c in allowed]
    check("rdp/record-ancestor-only-destructive-forms-refused",
          [an_first, an_refused, an_allowed],
          ["unverifiable", ["deny"] * len(refused), ["allow"] * len(allowed)])
    # A declared dispatch command writes no git directory, so a word of it may name a directory holding
    # the git directory (--workdir ., the repository root) and the dispatch reaches the pin check; a
    # word naming the git directory or a path inside it is still refused, and so is a holding directory
    # to a program the option tables do not model (opf).
    dw = RdpFixture(base, "dispatch-workdir")
    dw_good = dw.good()
    dw_allowed = [_rdp_kind(dw.run(c)) for c in ("orch-dispatch --brief " + dw_good,
                                                "orch-dispatch --workdir . --brief " + dw_good,
                                                "orch-dispatch --workdir " + str(dw.root) + " --brief " + dw_good)]
    dw_inside = [dw.run(c) for c in ("orch-dispatch --workdir .git/aiqt --brief " + dw_good,
                                     "orch-dispatch --workdir " + str(dw.root / ".git") + " --brief " + dw_good)]
    dw_guard = (os.path.realpath(dw.root / ".git"), None)
    check("rdp/dispatch-workdir-holding-dir-allowed-inside-refused",
          [dw_allowed, [_rdp_kind(r) for r in dw_inside],
           aiqt_hooks._rdp_names_common_dir(["orch-dispatch", "--workdir", "."], str(dw.root), dw_guard, True),
           aiqt_hooks._rdp_names_common_dir(["orch-dispatch", "--workdir", ".git/aiqt"], str(dw.root), dw_guard,
                                            True),
           aiqt_hooks._rdp_names_common_dir(["orch-dispatch", "--workdir", str(dw.root / ".git")], str(dw.root),
                                            dw_guard, True),
           aiqt_hooks._rdp_names_common_dir(["opf", "."], str(dw.root), dw_guard)],
          [["allow"] * 3, ["deny", "deny"], None, ".git/aiqt", str(dw.root / ".git"), "."])
    # One check reads each registry ONCE and enforces the recorded binding: a registry removed between the
    # record comparison and the enforcement (the record check wrapped to remove .aiqt after it compares)
    # still leaves the dispatch withheld, and no registry directory is read twice within one check.
    il = RdpFixture(base, "record-interleave")
    il_before = _rdp_kind(il.dispatch("/missing"))
    real_check, real_scope = aiqt_hooks._rdp_record_check, aiqt_hooks._rdp_scope
    reads = []

    def _check_then_remove(*args, **kw):
        out = real_check(*args, **kw)
        shutil.rmtree(il.root / ".aiqt", ignore_errors=True)
        return out

    def _counted(reg_dir):
        reads.append(os.path.realpath(reg_dir))
        return real_scope(reg_dir)
    try:
        aiqt_hooks._rdp_record_check, aiqt_hooks._rdp_scope = _check_then_remove, _counted
        il_mid = il.dispatch("/missing")
    finally:
        aiqt_hooks._rdp_record_check, aiqt_hooks._rdp_scope = real_check, real_scope
    il_next = il.dispatch("/missing")
    check("rdp/record-interleaved-removal-withholds",
          [il_before, _rdp_kind(il_mid), len(reads) == len(set(reads)), _rdp_kind(il_next),
           "binding record" in _reason(il_next)], ["unverifiable", "unverifiable", True, "unverifiable", True])
    # A linked worktree beside a main worktree whose git directory is separated (core.worktree naming the
    # main worktree): a dispatch is withheld, and the two calls that would point core.worktree at an empty
    # directory META/get, and so unscope the session, end at the config write, whose trailing word is a
    # value and no read action.
    cv = RdpFixture(base, "cfgvalue")
    cv_meta = base / "cfgvalue-meta"
    _rdp_git(cv.root, "init", "-q", "--separate-git-dir=" + str(cv_meta))
    _rdp_git(cv.root, "config", "core.worktree", str(cv.root))
    cv_wt = base / "cfgvalue-beside"
    _rdp_git(cv.root, "worktree", "add", "-q", "--detach", str(cv_wt), cv.pin)
    check("rdp/config-value-word-cannot-unscope-linked-worktree", [_rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
        hook_event_name="PreToolUse", cwd=str(cv_wt), session_id="s1", tool_name="Bash",
        tool_input=dict(command=c, run_in_background=False)))) for c in (
            "orch-dispatch --brief /missing", "mkdir " + str(cv_meta / "get"), "git config core.worktree get")],
        ["unverifiable", "deny", "deny"])
    # core.worktree moved onto ANOTHER bound tree: the binding above the session cwd still governs, and a
    # dispatch either binding declares is withheld, never checked against the other repository (before,
    # the redirected tree's binding decided and the cwd's dispatcher went undeclared, so it was allowed).
    ca = RdpFixture(base, "conflict-a")
    cb = RdpFixture(base, "conflict-b")
    cb.write_registry(dict(cb.binding, commands=["another-dispatch"]))
    cs = RdpFixture(base, "conflict-same")
    ca_bad = ca.brief(["Review-target: revision", "Reviewed-revision: HEAD"], "bad.txt")
    cf_got = [_rdp_kind(ca.dispatch(ca_bad))]
    _rdp_git(ca.root, "config", "core.worktree", str(cb.root))
    cf_got += [_rdp_kind(ca.run(c)) for c in (
        "orch-dispatch --brief " + ca_bad, "another-dispatch --brief " + ca_bad, "ls", "rm -rf .aiqt")]
    _rdp_git(ca.root, "config", "core.worktree", str(cs.root))
    cf_got.append(_rdp_kind(ca.dispatch(cs.good())))
    check("rdp/redirected-bound-tree-conflict-withholds", cf_got,
          ["deny", "unverifiable", "unverifiable", "allow", "deny", "unverifiable"])
    # The cannot-say withholds, each on its own: a failed common-directory probe where the raw .git file,
    # its examination, or the commondir file cannot be read withholds every call (no registry anywhere is
    # needed for that), and a gone .git/worktrees/NAME directory still names its main worktree.
    fake = base / "rawgit-fake"
    fake.mkdir()
    meta = base / "rawgit-meta" / "worktrees" / "n"
    (meta / "commondir").mkdir(parents=True)
    real_root, real_main, real_lstat = aiqt_hooks._orch_root, aiqt_hooks._rdp_main_worktree, aiqt_hooks.os.lstat
    dotgit = os.path.join(os.path.realpath(str(fake)), ".git")

    def _denied_lstat(path, *a, **k):
        if str(path) == dotgit:
            raise PermissionError(13, "Permission denied", dotgit)
        return real_lstat(path, *a, **k)
    raw_got = []
    try:
        aiqt_hooks._orch_root = lambda data: str(fake)
        aiqt_hooks._rdp_main_worktree = _probe_fails
        for text in ("not a pointer\n", "gitdir: " + str(meta) + "\n"):
            (fake / ".git").write_text(text, encoding="utf-8")
            raw_got.append(_rdp_ls(fake, False))
        aiqt_hooks.os.lstat = _denied_lstat
        try:
            raw_got.append(_rdp_ls(fake, False))
        finally:
            aiqt_hooks.os.lstat = real_lstat
        (fake / ".git").write_text("gitdir: " + str(rg.root / ".git" / "worktrees" / "gone") + "\n",
                                   encoding="utf-8")
        raw_got.append(_rdp_kind(aiqt_hooks.review_dispatch_pin(dict(
            hook_event_name="PreToolUse", cwd=str(fake), session_id="s1", tool_name="Bash",
            tool_input=dict(command="rm -rf .aiqt", run_in_background=False)))))
    finally:
        aiqt_hooks._orch_root, aiqt_hooks._rdp_main_worktree = real_root, real_main
        aiqt_hooks.os.lstat = real_lstat
    # The last: git cannot name the common git directory of the gitfile's root, so its binding record cannot
    # be read and the call is withheld (before the record, the main worktree's binding refused it).
    check("rdp/raw-gitfile-cannot-say-withholds", raw_got, ["unverifiable"] * 4)


def _fixture_tmpdir(prefix):
    """A fresh temporary directory for the fixtures, never under /dev or /proc: the review dispatch pin
    refuses a brief there by design (a /dev/shm private to each process, as in a sandbox, names a different
    file in the hook than in the dispatcher), so a TMPDIR under /dev or /proc is passed over for the first
    writable system temporary directory outside both. Raises OSError when there is none."""
    failures = []
    for where in (None, "/var/tmp", "/tmp"):
        try:
            path = tempfile.mkdtemp(prefix=prefix, dir=where)
        except OSError as exc:
            failures.append(str(exc))
            continue
        real = os.path.realpath(path)
        if real in ("/dev", "/proc") or real.startswith(("/dev/", "/proc/")):
            shutil.rmtree(path, ignore_errors=True)
            failures.append("{} is under /dev or /proc".format(path))
            continue
        return Path(path)
    raise OSError("no writable temporary directory outside /dev and /proc ({})".format("; ".join(failures)))


# QA round 7 of the silent-write fixes: the state-directory permission sweep. Each cell of _PERM_SWEEP_TABLE
# is one hook call after the state directory is set to an owner mode. The row key is (call, writer,
# turn-state.json, recorded schedule basis, counter earned so far), and the eight cells are owner modes 0000,
# 0100, ... 0700 in order. call: "sched" a ScheduleWakeup the decision core denies on wake hygiene, "ystop"
# a ScheduleWakeup stop=true over an actionable item, "stop" a Stop over it, "quiet" a ScheduleWakeup whose
# quiet-duration claim contradicts the measured gap, "badreg" a ScheduleWakeup under an unreadable registry,
# "ybadreg" a ScheduleWakeup stop=true under it. Under an unreadable registry the hook resolves the default
# state directory, never the registry's state_dir, so the badreg and ybadreg cells set that directory and its
# turn-state.json: the state a relief added before the unreadable-registry deny would read.
# writer: "opath" the O_PATH directory open, "rdonly" the O_RDONLY fallback of a platform without O_PATH.
# earned: "0", "cap" (3 schedule denials and 2 stop denials: the cap and the loop bound), "over" (4 and 3: one
# above each), "big" (9999 each: the largest valid count), "bad" (both counts the malformed value "m", which
# validation rejects), "nonobj" (turn-state.json holds the JSON array [], not an object), or "sig" (both
# counts 0, and the payload carries stop_hook_active set to true). A cell is the outcome (a allow, w warn,
# d deny, b block), the counter on disk after the call (the stop count for ystop, stop and ybadreg, else the
# schedule count; j while turn-state.json holds no JSON object), the wake digests on disk after it, then c
# when the output names a failed denial-counter save and w when it
# names a failed wake-digest save. Each output channel of the outcome is judged on its own (the deny reason
# and the banner of a deny, the banner of a warn, stderr of a block): the letter needs every channel to name
# the failure, '-' means none does, and '!' (never expected) means only some do.
_PERM_SWEEP_TABLE = {
    ("sched", "opath", "read", "same", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("sched", "opath", "read", "same", "cap"): "d30c- w30-w d30c- w31-- d30c- w30-w d30c- w31--",
    ("sched", "opath", "read", "changed", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("sched", "opath", "read", "changed", "cap"): "d30c- d30c- d30c- d10-- d30c- d30c- d30c- d10--",
    ("sched", "opath", "unread", "same", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("sched", "opath", "unread", "same", "cap"): "d30c- d30c- d30c- d10-- d30c- d30c- d30c- d10--",
    ("sched", "opath", "unread", "changed", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("sched", "opath", "unread", "changed", "cap"): "d30c- d30c- d30c- d10-- d30c- d30c- d30c- d10--",
    ("sched", "rdonly", "read", "same", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("sched", "rdonly", "read", "same", "cap"): "d30c- w30-w d30c- w30-w d30c- w30-w d30c- w31--",
    ("sched", "rdonly", "read", "changed", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("sched", "rdonly", "read", "changed", "cap"): "d30c- d30c- d30c- d30c- d30c- d30c- d30c- d10--",
    ("sched", "rdonly", "unread", "same", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("sched", "rdonly", "unread", "same", "cap"): "d30c- d30c- d30c- d30c- d30c- d30c- d30c- d10--",
    ("sched", "rdonly", "unread", "changed", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("sched", "rdonly", "unread", "changed", "cap"): "d30c- d30c- d30c- d30c- d30c- d30c- d30c- d10--",
    ("ystop", "opath", "read", "same", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("ystop", "opath", "read", "same", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "opath", "read", "changed", "0"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("ystop", "opath", "read", "changed", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "opath", "unread", "same", "0"): "d00c- w00-- d00c- w00-- d00c- w00-- d00c- w00--",
    ("ystop", "opath", "unread", "same", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "opath", "unread", "changed", "0"): "d00c- w00-- d00c- w00-- d00c- w00-- d00c- w00--",
    ("ystop", "opath", "unread", "changed", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "rdonly", "read", "same", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("ystop", "rdonly", "read", "same", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "rdonly", "read", "changed", "0"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("ystop", "rdonly", "read", "changed", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "rdonly", "unread", "same", "0"): "d00c- w00-- d00c- w00-- d00c- w00-- d00c- w00--",
    ("ystop", "rdonly", "unread", "same", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("ystop", "rdonly", "unread", "changed", "0"): "d00c- w00-- d00c- w00-- d00c- w00-- d00c- w00--",
    ("ystop", "rdonly", "unread", "changed", "cap"): "d20c- w20-- d20c- w20-- d20c- w20-- d20c- w20--",
    ("stop", "opath", "read", "same", "0"): "w00c- w00c- w00c- b10-- w00c- w00c- w00c- b10--",
    ("stop", "opath", "read", "same", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "opath", "read", "changed", "0"): "w00c- w00c- w00c- b10-- w00c- w00c- w00c- b10--",
    ("stop", "opath", "read", "changed", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "opath", "unread", "same", "0"): "w00c- w00-- w00c- w00-- w00c- w00-- w00c- w00--",
    ("stop", "opath", "unread", "same", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "opath", "unread", "changed", "0"): "w00c- w00-- w00c- w00-- w00c- w00-- w00c- w00--",
    ("stop", "opath", "unread", "changed", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "rdonly", "read", "same", "0"): "w00c- w00c- w00c- w00c- w00c- w00c- w00c- b10--",
    ("stop", "rdonly", "read", "same", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "rdonly", "read", "changed", "0"): "w00c- w00c- w00c- w00c- w00c- w00c- w00c- b10--",
    ("stop", "rdonly", "read", "changed", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "rdonly", "unread", "same", "0"): "w00c- w00-- w00c- w00-- w00c- w00-- w00c- w00--",
    ("stop", "rdonly", "unread", "same", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("stop", "rdonly", "unread", "changed", "0"): "w00c- w00-- w00c- w00-- w00c- w00-- w00c- w00--",
    ("stop", "rdonly", "unread", "changed", "cap"): "w20c- w20-- w20c- w20-- w20c- w20-- w20c- w20--",
    ("sched", "opath", "read", "same", "bad"): "dm0c- dm0c- dm0c- d10-- dm0c- dm0c- dm0c- d10--",
    ("sched", "rdonly", "read", "same", "bad"): "dm0c- dm0c- dm0c- dm0c- dm0c- dm0c- dm0c- d10--",
    ("sched", "opath", "read", "same", "sig"): "d00c- d00c- d00c- d10-- d00c- d00c- d00c- d10--",
    ("sched", "rdonly", "read", "same", "sig"): "d00c- d00c- d00c- d00c- d00c- d00c- d00c- d10--",
    ("ystop", "opath", "read", "same", "bad"): "dm0c- wm0-- dm0c- wm0-- dm0c- wm0-- dm0c- wm0--",
    ("ystop", "rdonly", "read", "same", "bad"): "dm0c- wm0-- dm0c- wm0-- dm0c- wm0-- dm0c- wm0--",
    ("ystop", "opath", "read", "same", "sig"): "w00-- w00-- w00-- w00-- w00-- w00-- w00-- w00--",
    ("ystop", "rdonly", "read", "same", "sig"): "w00-- w00-- w00-- w00-- w00-- w00-- w00-- w00--",
    ("stop", "opath", "read", "same", "bad"): "wm0c- wm0-- wm0c- wm0-- wm0c- wm0-- wm0c- wm0--",
    ("stop", "rdonly", "read", "same", "bad"): "wm0c- wm0-- wm0c- wm0-- wm0c- wm0-- wm0c- wm0--",
    ("stop", "opath", "read", "same", "sig"): "w00-- w00-- w00-- w00-- w00-- w00-- w00-- w00--",
    ("stop", "rdonly", "read", "same", "sig"): "w00-- w00-- w00-- w00-- w00-- w00-- w00-- w00--",
    ("quiet", "opath", "read", "same", "cap"): "w30-w d30-- w30-w d30-- w30-w d30-- w30-w d30--",
    ("quiet", "rdonly", "read", "same", "cap"): "w30-w d30-- w30-w d30-- w30-w d30-- w30-w d30--",
    ("badreg", "opath", "read", "same", "cap"): "d30-- d30-- d30-- d30-- d30-- d30-- d30-- d30--",
    ("badreg", "rdonly", "read", "same", "cap"): "d30-- d30-- d30-- d30-- d30-- d30-- d30-- d30--",
    ("ybadreg", "opath", "read", "same", "cap"): "d20-- d20-- d20-- d20-- d20-- d20-- d20-- d20--",
    ("ybadreg", "rdonly", "read", "same", "cap"): "d20-- d20-- d20-- d20-- d20-- d20-- d20-- d20--",
    ("ybadreg", "opath", "read", "same", "bad"): "dm0-- dm0-- dm0-- dm0-- dm0-- dm0-- dm0-- dm0--",
    ("ybadreg", "rdonly", "read", "same", "bad"): "dm0-- dm0-- dm0-- dm0-- dm0-- dm0-- dm0-- dm0--",
    ("ybadreg", "opath", "read", "same", "sig"): "d00-- d00-- d00-- d00-- d00-- d00-- d00-- d00--",
    ("ybadreg", "rdonly", "read", "same", "sig"): "d00-- d00-- d00-- d00-- d00-- d00-- d00-- d00--",
    ("ybadreg", "opath", "unread", "same", "0"): "d00-- d00-- d00-- d00-- d00-- d00-- d00-- d00--",
    ("ybadreg", "rdonly", "unread", "same", "0"): "d00-- d00-- d00-- d00-- d00-- d00-- d00-- d00--",
    ("ybadreg", "opath", "read", "same", "nonobj"): "dj0-- dj0-- dj0-- dj0-- dj0-- dj0-- dj0-- dj0--",
    ("ybadreg", "rdonly", "read", "same", "nonobj"): "dj0-- dj0-- dj0-- dj0-- dj0-- dj0-- dj0-- dj0--",
    ("sched", "opath", "read", "same", "nonobj"): "dj0c- dj0c- dj0c- d10-- dj0c- dj0c- dj0c- d10--",
    ("sched", "rdonly", "read", "same", "nonobj"): "dj0c- dj0c- dj0c- dj0c- dj0c- dj0c- dj0c- d10--",
    ("ystop", "opath", "read", "same", "nonobj"): "dj0c- wj0-- dj0c- wj0-- dj0c- wj0-- dj0c- wj0--",
    ("ystop", "rdonly", "read", "same", "nonobj"): "dj0c- wj0-- dj0c- wj0-- dj0c- wj0-- dj0c- wj0--",
    ("stop", "opath", "read", "same", "nonobj"): "wj0c- wj0-- wj0c- wj0-- wj0c- wj0-- wj0c- wj0--",
    ("stop", "rdonly", "read", "same", "nonobj"): "wj0c- wj0-- wj0c- wj0-- wj0c- wj0-- wj0c- wj0--",
    ("sched", "opath", "read", "same", "over"): "d40c- w40-w d40c- w41-- d40c- w40-w d40c- w41--",
    ("sched", "rdonly", "read", "same", "over"): "d40c- w40-w d40c- w40-w d40c- w40-w d40c- w41--",
    ("sched", "opath", "read", "same", "big"):
        "d99990c- w99990-w d99990c- w99991-- d99990c- w99990-w d99990c- w99991--",
    ("sched", "rdonly", "read", "same", "big"):
        "d99990c- w99990-w d99990c- w99990-w d99990c- w99990-w d99990c- w99991--",
    ("ystop", "opath", "read", "same", "over"): "d30c- w30-- d30c- w30-- d30c- w30-- d30c- w30--",
    ("ystop", "rdonly", "read", "same", "over"): "d30c- w30-- d30c- w30-- d30c- w30-- d30c- w30--",
    ("ystop", "opath", "read", "same", "big"):
        "d99990c- w99990-- d99990c- w99990-- d99990c- w99990-- d99990c- w99990--",
    ("ystop", "rdonly", "read", "same", "big"):
        "d99990c- w99990-- d99990c- w99990-- d99990c- w99990-- d99990c- w99990--",
    ("stop", "opath", "read", "same", "over"): "w30c- w30-- w30c- w30-- w30c- w30-- w30c- w30--",
    ("stop", "rdonly", "read", "same", "over"): "w30c- w30-- w30c- w30-- w30c- w30-- w30c- w30--",
    ("stop", "opath", "read", "same", "big"):
        "w99990c- w99990-- w99990c- w99990-- w99990c- w99990-- w99990c- w99990--",
    ("stop", "rdonly", "read", "same", "big"):
        "w99990c- w99990-- w99990c- w99990-- w99990c- w99990-- w99990c- w99990--",
}


def _perm_spare_flag():
    """A flag bit above every flag the hook passes to os.open: the model leg's stand-in for O_PATH where the
    platform has none (the seam clears it before the real open)."""
    used = (os.O_RDONLY | os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_DIRECTORY
            | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    return 1 << used.bit_length()


class _PermModel:
    """The model leg's seams: for the operations the hook makes in the state directory sd, refuse what owner
    mode `mode` on sd, and `fmode` on its turn-state.json, refuse a non-root owner, whatever the real modes
    (left at 0700 and 0600) and euid; the kernel leg holds this model to the kernel where the modes bind.
    A name looked up in sd needs search; a create, replace, rename or unlink there needs write too; an open
    of sd itself without O_PATH needs read; a read of turn-state.json needs its read bit. walk is the bit
    that marks an O_PATH open (the native O_PATH, or the spare bit, cleared before the real open, which
    then opens for reading)."""

    def __init__(self, sd, mode, fmode, walk, spare):
        self.sd, self.mode, self.fmode, self.walk, self.spare = str(sd), mode, fmode, walk, spare
        st = os.stat(self.sd)
        self.key = (st.st_dev, st.st_ino)
        self.real = dict(open=os.open, stat=os.stat, lstat=os.lstat, replace=os.replace, rename=os.rename,
                         unlink=os.unlink, remove=os.remove, mkdir=os.mkdir)

    def _where(self, path, dir_fd=None):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return None
        name = os.fsdecode(path)
        if dir_fd is not None and not os.path.isabs(name):
            st = os.fstat(dir_fd)
            return "name" if (st.st_dev, st.st_ino) == self.key else None
        full = os.path.abspath(name)
        if full == self.sd:
            return "dir"
        return "name" if os.path.dirname(full) == self.sd else None

    def _need(self, bits, path, mode=None):
        if (self.mode if mode is None else mode) & bits != bits:
            raise PermissionError(13, "Permission denied (modelled)", os.fsdecode(path))

    def _exists(self, path, dir_fd=None):
        try:
            self.real["lstat"](path, dir_fd=dir_fd)
        except FileNotFoundError:
            return False
        return True

    def _is_state(self, path):
        return os.path.basename(os.fsdecode(path)) == "turn-state.json"

    def os_open(self, path, flags, *a, dir_fd=None, **k):
        lookup = self.walk and flags & self.walk
        if self.spare and flags & self.spare:
            flags &= ~self.spare
        where = self._where(path, dir_fd)
        if where == "dir" and not lookup:
            self._need(0o400, path)
        elif where == "name":
            self._need(0o100, path)
            if flags & os.O_CREAT and not self._exists(path, dir_fd):
                self._need(0o200, path)
            if (self._is_state(path) and not lookup
                    and flags & getattr(os, "O_ACCMODE", 3) != os.O_WRONLY):
                self._need(0o400, path, self.fmode)
        return self.real["open"](path, flags, *a, dir_fd=dir_fd, **k)

    def open(self, file, mode="r", *a, **k):
        if self._where(file) == "name":
            self._need(0o100, file)
            if any(c in mode for c in "wax+") and not self._exists(file):
                self._need(0o200, file)
            if self._is_state(file) and any(c in mode for c in "r+"):
                self._need(0o400, file, self.fmode)
        return open(file, mode, *a, **k)

    def lookup(self, name):
        real = self.real[name]

        def seam(path, *a, dir_fd=None, **k):
            if self._where(path, dir_fd) == "name":
                self._need(0o100, path)
            return real(path, *a, dir_fd=dir_fd, **k)
        return seam

    def change(self, name):
        real = self.real[name]

        def seam(path, *a, dir_fd=None, **k):
            if self._where(path, dir_fd) == "name":
                self._need(0o300, path)
            return real(path, *a, dir_fd=dir_fd, **k)
        return seam

    def move(self, name):
        real = self.real[name]

        def seam(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            if "name" in (self._where(src, src_dir_fd), self._where(dst, dst_dir_fd)):
                self._need(0o300, src)
            return real(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        return seam

    def __enter__(self):
        os.open, os.stat, os.lstat = self.os_open, self.lookup("stat"), self.lookup("lstat")
        os.replace, os.rename = self.move("replace"), self.move("rename")
        os.unlink, os.remove, os.mkdir = self.change("unlink"), self.change("remove"), self.change("mkdir")
        aiqt_hooks.open = self.open
        return self

    def __exit__(self, *exc):
        real = self.real
        os.open, os.stat, os.lstat, os.replace = real["open"], real["stat"], real["lstat"], real["replace"]
        os.rename, os.unlink, os.remove, os.mkdir = real["rename"], real["unlink"], real["remove"], real["mkdir"]
        del aiqt_hooks.open


def _perm_sweep(base):
    """Run every _PERM_SWEEP_TABLE row in two legs and return (kernel cells, model cells, mismatches). The
    MODEL leg runs everywhere: the real modes stay 0700 and 0600 and _PermModel refuses what the owner mode
    refuses, so a root run and a platform without O_PATH get the same answers (the O_RDONLY fallback open
    is refused for a mode without read whatever the native O_PATH, and a spare bit stands in for a missing
    O_PATH). The KERNEL leg uses real chmod and the platform's own writers (O_PATH where it exists, and
    the O_RDONLY fallback) and runs only without root, since the modes do not bind root; it holds the model
    to the kernel."""
    native = getattr(os, "O_PATH", 0)
    spare = 0 if native else _perm_spare_flag()
    root_run = os.geteuid() == 0
    fixtures = dict()
    for call in ("sched", "ystop", "stop"):
        fx = Fixture(base, "permsweep-" + call)
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(fx.root)))
        sd.mkdir(parents=True, exist_ok=True)
        fx.set_items([item("QA-R6", blocker=dict(kind="not-before", ref=now_iso(-48)))] if call == "sched"
                     else [item("QA-S")])
        fx.set_turn_state(dict())
        aiqt_hooks.orch_yield_tool(fx.payload("PreToolUse", "ScheduleWakeup", dict(prompt="waiting")))
        fixtures[call] = (fx, sd, fx.turn_state().get("schedule_basis"))
    fixtures["quiet"] = fixtures["sched"]
    for call, like in (("badreg", "sched"), ("ybadreg", "ystop")):
        fx, _sd, recorded = fixtures[like]
        # the directory _orch_state_dir_for_root falls back to when the registry cannot be read
        sd = Path(aiqt_hooks._state_dir_from_registry(str(fx.root), None))
        sd.mkdir(parents=True, exist_ok=True)
        fixtures[call] = (fx, sd, recorded)
    inputs = dict(sched=dict(prompt="waiting"), quiet=dict(prompt="quiet for 90 minutes, recheck QA-R6"),
                  badreg=dict(prompt="waiting"), ystop=dict(prompt="done", stop=True),
                  ybadreg=dict(prompt="done", stop=True))
    letter = dict(allow="a", warn="w", deny="d", block2="b")

    def _cell(key, mode, model):
        call, writer, readable, basis, earned = key
        fx, sd, recorded = fixtures[call]
        cap, bound, top = aiqt_hooks._ORCH_SCHEDULE_CAP, aiqt_hooks._ORCH_LOOP_BOUND, aiqt_hooks._ORCH_COUNTER_MAX
        scount, bcount = dict(cap=(cap, bound), over=(cap + 1, bound + 1), big=(top, top)).get(earned, (0, 0))
        state = dict(schedule_denials=scount, stop_denials=bcount,
                     schedule_basis=recorded if basis == "same" else "a changed basis")
        if earned == "bad":
            state.update(schedule_denials="m", stop_denials="m")
        extra = dict(stop_hook_active=True) if earned == "sig" else None
        if call == "quiet":
            state["last_human_input_utc"] = now_iso()
        fmode = 0o600 if readable == "read" else 0
        for name in os.listdir(str(sd)):
            os.unlink(str(sd / name))
        (sd / "turn-state.json").write_text("[]" if earned == "nonobj" else json.dumps(state), encoding="utf-8")
        registry = fx.root / ".aiqt" / "orchestration.local.json"
        saved_registry = registry.read_bytes()
        if call in ("badreg", "ybadreg"):
            registry.write_text("not json", encoding="utf-8")
        if call == "stop":
            run = lambda: aiqt_hooks.orch_stop_guard(fx.payload("Stop", extra=extra))
        else:
            run = lambda: aiqt_hooks.orch_yield_tool(fx.payload("PreToolUse", "ScheduleWakeup", inputs[call],
                                                                extra))
        saved_walk = aiqt_hooks._ORCH_O_WALK
        try:
            aiqt_hooks._ORCH_O_WALK = os.O_RDONLY if writer == "rdonly" else (native or spare)
            if model:
                with _PermModel(sd, mode, fmode, native or spare, spare):
                    result = run()
            else:
                os.chmod(str(sd / "turn-state.json"), fmode)
                os.chmod(str(sd), mode)
                result = run()
        finally:
            aiqt_hooks._ORCH_O_WALK = saved_walk
            os.chmod(str(sd), 0o700)
            os.chmod(str(sd / "turn-state.json"), 0o600)
            registry.write_bytes(saved_registry)
        after = json.loads((sd / "turn-state.json").read_text(encoding="utf-8"))
        verdict = _verdict(result)
        obj = result[1] if isinstance(result[1], dict) else dict()
        hso = obj.get("hookSpecificOutput")
        reason = hso.get("permissionDecisionReason") if isinstance(hso, dict) else None
        channels = dict(deny=(reason, obj.get("systemMessage")), warn=(obj.get("systemMessage"),),
                        block2=(result[2],)).get(verdict, ())

        def named(phrase, mark):
            found = [isinstance(text, str) and phrase in text for text in channels]
            return mark if found and all(found) else "!" if any(found) else "-"
        if not isinstance(after, dict):
            count, after = "j", dict()
        else:
            count = after.get("stop_denials" if call in ("ystop", "stop", "ybadreg") else "schedule_denials", "x")
        return "{}{}{}{}{}".format(letter.get(verdict, "?"), count, len(after.get("wake_digests") or []),
                                   named("denial counter could not be", "c"),
                                   named("prompt digest could not be written", "w"))
    counts, mismatches = [0, 0], []
    for model in (False, True):
        for key, want in _PERM_SWEEP_TABLE.items():
            if not model and (root_run or (key[1] == "opath" and not native)):
                continue
            for index, expected in enumerate(want.split()):
                got = _cell(key, index << 6, model)
                counts[model] += 1
                if got != expected:
                    mismatches.append(("model" if model else "kernel",) + key + (oct(index << 6), got, expected))
    return counts[0], counts[1], mismatches


def _leasehb_timed(fn, seconds=30):
    """Run one lease-heartbeat call on a TIMED daemon worker thread: (done, result). A call
    that blocks past the bound (an O_NONBLOCK regression on a synchronous FIFO open) reads as
    done=False, a NAMED red in the caller's check, never a suite hang to the CI job timeout
    (the daemon thread is reaped at interpreter exit, as in the append-fifo vectors)."""
    box = []
    worker = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    worker.start()
    worker.join(seconds)
    return (not worker.is_alive(), box[0] if box else None)


def _leasehb_cases(tmp):
    """Component: the lease-heartbeat keeper (cnclse/recncl; two warn-only bindings over one core).
    Hermetic fixtures (each its own repo, registry and state dir); verdicts are judged on the
    STRUCTURED result, and every no-write claim is proven on bytes plus mtime_ns, never on prose."""
    base = tmp / "leasehb"
    base.mkdir()

    def hb(**overrides):
        block = {"mode": "warn", "format": "lines", "holder_field": "holder",
                 "refresh_after_minutes": 10, "warn_after_minutes": 30}
        block.update(overrides)
        return block

    def build(name, block, lease_text="holder: s1\n", age_secs=7200, holder_flag=True):
        f = Fixture(base, name)
        reg_path = f.root / ".aiqt" / "orchestration.local.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        lease = {"path": str(f.lease), "max_age_hours": 24}
        if holder_flag:
            lease["holder_is_session_id"] = True
        if block is not None:
            lease["heartbeat"] = block
        reg["lease"] = lease
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        if lease_text is None:
            f.lease.unlink(missing_ok=True)
        else:
            f.lease.write_text(lease_text, encoding="utf-8")
            aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - age_secs
            os.utime(f.lease, times=(aged, aged))
        return f

    def stop(f, sid="s1"):
        payload = f.payload("Stop")
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_stop(payload)

    def post(f, sid="s1"):
        payload = f.payload("PostToolUse", "Bash", {"command": "true"})
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_post(payload)

    def text_of(result):
        obj = result[1]
        return obj.get("systemMessage", "") if isinstance(obj, dict) else ""

    def snap(path):
        st = os.lstat(path)
        return (path.read_bytes(), st.st_mtime_ns)

    def rows(f):
        path = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root))) / "guard-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def swapped_ino(dirfd, name, _real=aiqt_hooks._orch_lease_hb_restat):
        st = _real(dirfd, name)
        values = list(st)
        return os.stat_result(values[:1] + [st.st_ino + 1] + values[2:])

    # --- declaration validation (fail-first: these existed in no form before this control) ---
    f = build("cfg", None)
    check("leasehb/config-absent-inert", _verdict(stop(f)), "allow")
    reg = json.loads((f.root / ".aiqt" / "orchestration.local.json").read_text(encoding="utf-8"))
    reg["lease"]["heartbeat"] = hb()
    check("leasehb/config-valid-ok", aiqt_hooks._orch_lease_hb_config(reg)[0], "ok")
    for cid, block, flag in (
            ("leasehb/config-bad-holder-flag", hb(), False),
            ("leasehb/config-bad-thresholds-inverted",
             hb(refresh_after_minutes=30, warn_after_minutes=30), True),
            ("leasehb/config-bad-warn-over-ceiling", hb(warn_after_minutes=24 * 60), True),
            ("leasehb/config-bad-duplicate-fields", hb(field="holder"), True),
            ("leasehb/config-bad-unknown-key", hb(extra_key=1), True),
            ("leasehb/config-bad-state-without-active", hb(state_field="state"), True),
            ("leasehb/config-bad-mode", hb(mode="block"), True),
            ("leasehb/config-bad-mode-list", hb(mode=[]), True),
            ("leasehb/config-bad-mode-dict", hb(mode={}), True),
            ("leasehb/config-bad-format-dict", hb(format={}), True),
            ("leasehb/config-bad-format-list", hb(format=[]), True),
            ("leasehb/config-bad-operating-mode-field", hb(field="Operating-mode"), True)):
        bad = {"version": 1,
               "lease": {"path": str(f.lease), "max_age_hours": 24, "heartbeat": block}}
        if flag:
            bad["lease"]["holder_is_session_id"] = True
        check(cid, aiqt_hooks._orch_lease_hb_config(bad)[0], "bad")
    g = build("cfgbad", hb(mode="nonesuch"))
    result = stop(g)
    check("leasehb/config-bad-stop-warns", _verdict(result), "warn")
    check("leasehb/config-bad-stop-names-invalid", "invalid" in text_of(result), True)
    # an invalid declaration holds no descriptor proof: bookkeeping is withheld and the Stop
    # warning is the only surfacing (the descriptor-bound replacement of the old positive row)
    check("leasehb/config-bad-withholds-row", rows(g), [])
    check("leasehb/config-bad-post-silent", _verdict(post(g)), "allow")
    gu = build("cfgunhash", hb(mode=[]))
    before = snap(gu.lease)
    result = stop(gu)
    check("leasehb/config-unhashable-mode-stop-named",
          (_verdict(result), "invalid" in text_of(result)), ("warn", True))
    check("leasehb/config-unhashable-mode-no-write", snap(gu.lease), before)
    post(gu)
    # the named invalid-declaration refusal surfaces on Stop and withholds every row (no
    # descriptor proof exists before the first open), and never a TypeError crash row either
    check("leasehb/config-unhashable-mode-withholds-row",
          [r.get("detail") for r in rows(gu) if r.get("kind") == "lease-heartbeat"], [])
    gv = build("cfgunhashfmt", hb(format={}))
    result = stop(gv)
    check("leasehb/config-unhashable-format-stop-named",
          (_verdict(result), "format must be" in text_of(result)), ("warn", True))
    check("leasehb/config-unhashable-format-post-silent", _verdict(post(gv)), "allow")
    # no row for the named format refusal, through Stop AND Post: an invalid declaration
    # holds no descriptor proof and every bookkeeping writer withholds
    check("leasehb/config-unhashable-format-withholds-row",
          len([r for r in rows(gv) if "format must be" in r.get("detail", "")]), 0)
    gv_reg = json.loads((gv.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    check("leasehb/resume-unhashable-format-finding",
          any("invalid" in fi for fi in
              aiqt_hooks._orch_lease_hb_resume_findings(gv_reg, str(gv.root), gv_reg["lease"])),
          True)
    # decided item 3 of round 4: unusable path VALUES are the named invalid-declaration refusal
    # at config time (an embedded NUL raises ValueError at open; an empty component collapses
    # to a different path than the declared spelling)
    for cid, bad_path in (
            ("leasehb/config-bad-lease-path-nul", str(f.lease) + "\0x"),
            ("leasehb/config-bad-lease-path-empty-component",
             str(f.root) + os.sep + "sub" + os.sep + os.sep + "lease.txt")):
        bad = {"version": 1,
               "lease": {"path": bad_path, "max_age_hours": 24, "holder_is_session_id": True,
                         "heartbeat": hb()}}
        check(cid, aiqt_hooks._orch_lease_hb_config(bad)[0], "bad")
    for cid, bad_lock in (
            ("leasehb/config-bad-lock-path-nul", "bad\0.lock"),
            ("leasehb/config-bad-lock-path-trailing-sep", "lock.dir" + os.sep)):
        bad = {"version": 1,
               "lease": {"path": str(f.lease), "max_age_hours": 24, "holder_is_session_id": True,
                         "heartbeat": hb(mode="refresh", lock_path=bad_lock)}}
        check(cid, aiqt_hooks._orch_lease_hb_config(bad)[0], "bad")
    nl = build("nullock", hb(mode="refresh", lock_path="bad\0.lock"))
    before = snap(nl.lease)
    result = stop(nl)
    check("leasehb/config-nul-lock-stop-named",
          (_verdict(result), "invalid" in text_of(result), "lock_path" in text_of(result),
           snap(nl.lease) == before), ("warn", True, True, True))
    gu_reg = json.loads((gu.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    check("leasehb/resume-unhashable-mode-finding",
          any("invalid" in fi for fi in
              aiqt_hooks._orch_lease_hb_resume_findings(gu_reg, str(gu.root), gu_reg["lease"])),
          True)

    # --- warn mode, holder proof, lost/taken memory ---
    f = build("warm", hb())
    before = snap(f.lease)
    result = stop(f)
    check("leasehb/held-gap-stop-warns", _verdict(result), "warn")
    check("leasehb/held-gap-names-minutes", "120 min" in text_of(result), True)
    check("leasehb/warn-mode-never-writes", snap(f.lease), before)
    check("leasehb/held-gap-post-silent", _verdict(post(f)), "allow")
    check("leasehb/held-gap-post-row",
          any(r.get("kind") == "lease-heartbeat" and "gap" in r.get("detail", "")
              for r in rows(f)), True)
    f.lease.write_text("holder: s2\nprevious_holder: s1\n", encoding="utf-8")
    aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - 7200
    os.utime(f.lease, times=(aged, aged))
    check("leasehb/taken-warns-past-holder", "lease taken" in text_of(stop(f)), True)
    f.lease.unlink()
    check("leasehb/lost-warns-past-holder", "lease lost" in text_of(stop(f)), True)

    d = build("decoy", hb(), lease_text="holder: s2\nprevious_holder: s1\n")
    before = snap(d.lease)
    check("leasehb/decoy-foreign-silent", _verdict(stop(d)), "allow")
    check("leasehb/foreign-never-writes", snap(d.lease), before)

    a = build("absent", hb(), lease_text=None)
    check("leasehb/absent-silent-no-past-holder", _verdict(stop(a)), "allow")
    e = build("emptysid", hb())
    check("leasehb/empty-session-silent", _verdict(stop(e, sid="")), "allow")

    # --- cannot-evaluate battery (each a named no-write warning, bytes and mtime untouched) ---
    c = build("dupholder", hb(), lease_text="holder: s1\nholder: s1\n")
    before = snap(c.lease)
    result = stop(c)
    check("leasehb/duplicate-holder-cannot", "cannot be evaluated" in text_of(result), True)
    check("leasehb/duplicate-holder-no-write", snap(c.lease), before)
    sl = build("symfinal", hb(), lease_text=None)
    real = sl.root / "real-lease.txt"
    real.write_text("holder: s1\n", encoding="utf-8")
    os.symlink(str(real), str(sl.lease))
    check("leasehb/symlink-final-cannot", "cannot be evaluated" in text_of(stop(sl)), True)
    sp = build("symparent", hb(), lease_text=None)
    realdir = sp.root / "realdir"
    realdir.mkdir()
    (realdir / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    os.symlink(str(realdir), str(sp.root / "linkdir"))
    sp_reg_path = sp.root / ".aiqt" / "orchestration.local.json"
    sp_reg = json.loads(sp_reg_path.read_text(encoding="utf-8"))
    sp_reg["lease"]["path"] = str(sp.root / "linkdir" / "lease.txt")
    sp_reg_path.write_text(json.dumps(sp_reg), encoding="utf-8")
    check("leasehb/symlink-parent-cannot", "cannot be evaluated" in text_of(stop(sp)), True)
    hl = build("hardlink", hb())
    os.link(str(hl.lease), str(hl.root / "alias.txt"))
    check("leasehb/hardlink-cannot", "cannot be evaluated" in text_of(stop(hl)), True)
    ff = build("fifo", hb(), lease_text=None)
    os.mkfifo(str(ff.lease))
    # TIMED: the non-blocking lease open must refuse a FIFO promptly; an O_NONBLOCK regression
    # is a named red (done=False), never a hung suite
    ff_done, ff_result = _leasehb_timed(lambda: stop(ff))
    check("leasehb/fifo-cannot",
          (ff_done, "cannot be evaluated" in text_of(ff_result or (None, None, None))),
          (True, True))
    ov = build("oversize", hb(), lease_text=None)
    ov.lease.write_bytes(b"holder: s1\n" + b"x" * (64 * 1024))
    check("leasehb/oversize-cannot", "cannot be evaluated" in text_of(stop(ov)), True)
    bu = build("badutf8", hb(), lease_text=None)
    bu.lease.write_bytes(b"holder: s1\n\xff\xfe\n")
    check("leasehb/bad-utf8-cannot", "cannot be evaluated" in text_of(stop(bu)), True)

    wo = build("owner", hb())
    saved_fstat = aiqt_hooks._orch_lease_hb_fstat
    try:
        def foreign_uid(fd, _real=saved_fstat):
            st = _real(fd)
            values = list(st)
            return os.stat_result(values[:4] + [st.st_uid + 1] + values[5:])
        aiqt_hooks._orch_lease_hb_fstat = foreign_uid
        check("leasehb/wrong-owner-cannot", "cannot be evaluated" in text_of(stop(wo)), True)
    finally:
        aiqt_hooks._orch_lease_hb_fstat = saved_fstat
    rs = build("released", hb(state_field="state", active_values=["active"]),
               lease_text="holder: s1\nstate: released\n")
    result = stop(rs)
    check("leasehb/released-state-warns", _verdict(result), "warn")
    check("leasehb/released-state-never-revives", "never revived" in text_of(result), True)

    js = build("jsonfmt", hb(format="json"),
               lease_text=json.dumps({"holder": "s1", "note": "x"}) + "\n")
    check("leasehb/json-held-gap-warns", "heartbeat gap" in text_of(stop(js)), True)
    js.lease.write_text(json.dumps({"holder": "s1"})[:-1] + ', "holder": "s1"}', encoding="utf-8")
    check("leasehb/json-duplicate-key-cannot", "cannot be evaluated" in text_of(stop(js)), True)

    dd = build("dotdot", hb(), lease_text=None)
    dd_reg_path = dd.root / ".aiqt" / "orchestration.local.json"
    dd_reg = json.loads(dd_reg_path.read_text(encoding="utf-8"))
    dd_reg["lease"]["path"] = os.path.join("sub", os.pardir, "lease.txt")
    dd_reg_path.write_text(json.dumps(dd_reg), encoding="utf-8")
    check("leasehb/dotdot-refused", "cannot be evaluated" in text_of(stop(dd)), True)

    # --- fault containment and wiring ---
    cr = build("crash", hb())
    saved_cfg_fn = aiqt_hooks._orch_lease_hb_config
    try:
        def boom(reg_arg):
            raise RuntimeError("leasehb-crash")
        aiqt_hooks._orch_lease_hb_config = boom
        check("leasehb/crash-contained-post", _verdict(post(cr)), "allow")
        check("leasehb/crash-contained-stop", _verdict(stop(cr)), "allow")
    finally:
        aiqt_hooks._orch_lease_hb_config = saved_cfg_fn
    # a crash BEFORE the first open holds no descriptor proof: the crash row is withheld
    # (the descriptor-bound replacement of the old pre-open crash-row expectation)
    check("leasehb/crash-pre-open-withholds-row",
          any(r.get("kind") == "lease-heartbeat" and "core fault" in r.get("detail", "")
              for r in rows(cr)), False)
    cd = build("crashheld", hb())
    cd_before = snap(cd.lease)
    saved_parse_fn = aiqt_hooks._orch_lease_hb_parse
    try:
        def parse_boom(text, cfg, keys=None):
            raise RuntimeError("leasehb-held-crash")
        aiqt_hooks._orch_lease_hb_parse = parse_boom
        check("leasehb/crash-held-contained-stop", _verdict(stop(cd)), "allow")
    finally:
        aiqt_hooks._orch_lease_hb_parse = saved_parse_fn
    # the ONE crash row this event records: written from inside the judge while the proven
    # lease descriptor is still held, through the write-time descriptor check, and exactly
    # once (the binding's recorder runs after cleanup, finds no held descriptor and withholds)
    check("leasehb/crash-row-descriptor-held",
          len([r for r in rows(cd) if "core fault" in r.get("detail", "")]), 1)
    check("leasehb/crash-held-lease-untouched", snap(cd.lease), cd_before)
    check("leasehb/wiring-handlers",
          (aiqt_hooks.HANDLERS.get("orch_lease_heartbeat_post")
           is aiqt_hooks.orch_lease_heartbeat_post
           and aiqt_hooks.HANDLERS.get("orch_lease_heartbeat_stop")
           is aiqt_hooks.orch_lease_heartbeat_stop
           and aiqt_hooks.HANDLER_EVENT.get("orch_lease_heartbeat_post") == "PostToolUse"
           and aiqt_hooks.HANDLER_EVENT.get("orch_lease_heartbeat_stop") == "Stop"
           and "orch_lease_heartbeat_post" in aiqt_hooks.FLOOR_FAIL_OPEN_MODES
           and "orch_lease_heartbeat_stop" in aiqt_hooks.FLOOR_FAIL_OPEN_MODES), True)

    # --- refresh mode, mtime form ---
    r1 = build("refresh", hb(mode="refresh"))
    before = snap(r1.lease)
    result = stop(r1)
    check("leasehb/refresh-mtime-refreshes",
          os.lstat(r1.lease).st_mtime_ns > before[1] and r1.lease.read_bytes() == before[0], True)
    check("leasehb/refresh-gap-warns-refreshed", "refreshed NOW" in text_of(result), True)
    reg_now = json.loads((r1.root / ".aiqt" / "orchestration.local.json").read_text(encoding="utf-8"))
    check("leasehb/scope-live-after-refresh",
          aiqt_hooks._orch_scope_live(reg_now, str(r1.root), "s1"), True)
    settled = snap(r1.lease)
    second = stop(r1)
    check("leasehb/refresh-idempotent-second-call",
          second[0] == 0 and second[1] is None and snap(r1.lease) == settled, True)
    check("leasehb/refresh-post-throttled", _verdict(post(r1)), "allow")
    r2 = build("ceiling", hb(mode="refresh"), age_secs=24 * 3600 - 400)
    before = snap(r2.lease)
    result = stop(r2)
    check("leasehb/refresh-ceiling-no-write",
          "too close" in text_of(result) and snap(r2.lease) == before, True)
    r3 = build("stale", hb(mode="refresh"), age_secs=25 * 3600)
    before = snap(r3.lease)
    result = stop(r3)
    check("leasehb/refresh-stale-no-write",
          "lapsed" in text_of(result) and snap(r3.lease) == before, True)
    r4 = build("future", hb(mode="refresh"), age_secs=-3600)
    before = snap(r4.lease)
    result = stop(r4)
    check("leasehb/refresh-future-no-write",
          "future" in text_of(result) and snap(r4.lease) == before, True)
    r5 = build("nocreate", hb(mode="refresh"), lease_text=None)
    check("leasehb/refresh-absent-no-create",
          stop(r5)[0] == 0 and not r5.lease.exists(), True)
    r6 = build("refforeign", hb(mode="refresh"), lease_text="holder: s2\n")
    before = snap(r6.lease)
    check("leasehb/refresh-foreign-no-write",
          _verdict(stop(r6)) == "allow" and snap(r6.lease) == before, True)
    r7 = build("swap", hb(mode="refresh"))
    before = snap(r7.lease)
    saved_restat = aiqt_hooks._orch_lease_hb_restat
    try:
        aiqt_hooks._orch_lease_hb_restat = swapped_ino
        result = stop(r7)
    finally:
        aiqt_hooks._orch_lease_hb_restat = saved_restat
    check("leasehb/refresh-swap-no-write",
          "refused" in text_of(result) and snap(r7.lease) == before, True)

    # --- refresh mode, heartbeat field form ---
    old_ts = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=2)).replace(microsecond=0)
    old_val = old_ts.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    fl = build("fieldlines", hb(mode="refresh", field="heartbeat_utc"), lease_text=None)
    content = ("Prose before.\nholder: s1\n\theartbeat_utc  :  " + old_val
               + "  \nOperating-mode: unattended\nProse after.\n")
    fl.lease.write_text(content, encoding="utf-8")
    os.chmod(fl.lease, 0o640)
    fl_reg_path = fl.root / ".aiqt" / "orchestration.local.json"
    fl_reg = json.loads(fl_reg_path.read_text(encoding="utf-8"))
    fl_reg["mode"] = {"path": str(fl.lease)}
    fl_reg_path.write_text(json.dumps(fl_reg), encoding="utf-8")
    mode_before = aiqt_hooks._orch_mode(fl_reg, str(fl.root))
    saved_now = aiqt_hooks._orch_now
    fixed_now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    try:
        aiqt_hooks._orch_now = lambda: fixed_now
        stop(fl)
    finally:
        aiqt_hooks._orch_now = saved_now
    new_val = fixed_now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    after_text = fl.lease.read_text(encoding="utf-8")
    check("leasehb/field-lines-only-value-changes",
          after_text, content.replace(old_val, new_val))
    check("leasehb/field-value-is-clock-read",
          "\theartbeat_utc  :  " + new_val + "  \n" in after_text, True)
    check("leasehb/field-mode-bits-kept", os.lstat(fl.lease).st_mode & 0o777, 0o640)
    check("leasehb/field-no-tmp-left",
          [n for n in os.listdir(fl.root) if n.endswith(".hb.tmp")], [])
    check("leasehb/field-mode-line-preserved",
          aiqt_hooks._orch_mode(fl_reg, str(fl.root)), mode_before)
    fj = build("fieldjson", hb(mode="refresh", format="json", field="hb"), lease_text=None)
    fj.lease.write_text(json.dumps({"zeta": "1", "holder": "s1", "hb": old_val, "alpha": "2"}),
                        encoding="utf-8")
    stop(fj)
    fj_after = json.loads(fj.lease.read_text(encoding="utf-8"))
    check("leasehb/field-json-value-refreshed",
          fj_after["hb"].endswith("Z") and fj_after["hb"] != old_val
          and fj_after["holder"] == "s1", True)
    check("leasehb/field-json-order-kept", list(fj_after.keys()), ["zeta", "holder", "hb", "alpha"])
    fs = build("fieldstale", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: 2020-01-01T00:00:00Z\n", age_secs=0)
    before = snap(fs.lease)
    check("leasehb/field-old-stale-no-write",
          "lapsed" in text_of(stop(fs)) and snap(fs.lease) == before, True)
    fe = build("fieldepoch", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: 1760000000\n", age_secs=0)
    before = snap(fe.lease)
    check("leasehb/field-epoch-cannot",
          "cannot be read" in text_of(stop(fe)) and snap(fe.lease) == before, True)
    fn = build("fieldnaive", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: 2026-10-12T10:00:00\n", age_secs=0)
    check("leasehb/field-naive-ts-cannot", "cannot be read" in text_of(stop(fn)), True)

    fw = build("fieldswap", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    before = snap(fw.lease)
    saved_restat = aiqt_hooks._orch_lease_hb_restat
    try:
        aiqt_hooks._orch_lease_hb_restat = swapped_ino
        result = stop(fw)
    finally:
        aiqt_hooks._orch_lease_hb_restat = saved_restat
    check("leasehb/field-swap-no-write",
          "refused" in text_of(result) and snap(fw.lease) == before, True)
    check("leasehb/field-swap-no-tmp-left",
          [n for n in os.listdir(fw.root) if n.endswith(".hb.tmp")], [])
    fp = build("fieldplant", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    victim = fp.root / "victim.txt"
    victim.write_text("victim\n", encoding="utf-8")
    planted_name = ".lease.txt." + "0" * 16 + ".hb.tmp"
    planted = fp.root / planted_name
    os.symlink(str(victim), str(planted))
    fp_before = snap(fp.lease)
    # FORCE the temporary name onto the planted entry (the _orch_lease_hb_tmp_name seam): an
    # unforced random suffix would miss the plant with near certainty, proving nothing about the
    # O_EXCL|O_NOFOLLOW discipline; a symlink-FOLLOWING writer goes red here, and ONLY here
    # (O_NOFOLLOW alone refuses this plant whether or not O_EXCL is kept, so a dropped O_EXCL
    # stays green against it: the hard-link plant below is the vector that catches that drop)
    saved_tmp_name = aiqt_hooks._orch_lease_hb_tmp_name
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name: planted_name
        result = stop(fp)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmp_name
    check("leasehb/field-planted-tmp-untouched",
          victim.read_text(encoding="utf-8") == "victim\n" and planted.is_symlink(), True)
    check("leasehb/field-planted-tmp-forced-refuses",
          ("the temporary file cannot be created" in text_of(result),
           snap(fp.lease) == fp_before), (True, True))
    fp2 = build("fieldplanthard", hb(mode="refresh", field="heartbeat_utc"),
                lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    victim2 = fp2.root / "victim2.txt"
    victim2.write_text("victim2\n", encoding="utf-8")
    planted2 = fp2.root / planted_name
    # a REGULAR-FILE plant, hard-linked to a victim: O_NOFOLLOW alone passes it, so ONLY the
    # O_EXCL half of the open discipline refuses it; a writer that drops O_EXCL goes red HERE
    # by overwriting the victim through the link (the symlink plant above cannot see that drop)
    os.link(str(victim2), str(planted2))
    fp2_before = snap(fp2.lease)
    victim2_before = snap(victim2)
    saved_tmp_name = aiqt_hooks._orch_lease_hb_tmp_name
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name: planted_name
        result = stop(fp2)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmp_name
    check("leasehb/field-planted-tmp-hardlink-refuses-exact",
          ("the temporary file cannot be created" in text_of(result),
           snap(fp2.lease) == fp2_before), (True, True))
    try:
        hard_intact = (snap(victim2) == victim2_before and os.lstat(planted2).st_nlink == 2
                       and os.lstat(planted2).st_ino == os.lstat(victim2).st_ino)
    except OSError:
        # a vanished plant or victim IS the mutant shape (the replace consumed the planted
        # name): a red check, never a suite crash
        hard_intact = False
    check("leasehb/field-planted-tmp-hardlink-victim-intact", hard_intact, True)
    fg = build("fieldgit", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    fg._git("add", "lease.txt")
    fg._git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track the lease")
    before = snap(fg.lease)
    check("leasehb/field-git-tracked-refused",
          "refused" in text_of(stop(fg)) and snap(fg.lease) == before, True)
    fa = Fixture(base, "fieldaiqt")
    fa_lease = fa.root / ".aiqt" / "under-aiqt-lease.txt"
    fa_lease.write_text("holder: s1\nheartbeat_utc: " + old_val + "\n", encoding="utf-8")
    fa_reg_path = fa.root / ".aiqt" / "orchestration.local.json"
    fa_reg = json.loads(fa_reg_path.read_text(encoding="utf-8"))
    fa_reg["lease"] = {"path": str(fa_lease), "max_age_hours": 24, "holder_is_session_id": True,
                       "heartbeat": hb(mode="refresh", field="heartbeat_utc")}
    fa_reg_path.write_text(json.dumps(fa_reg), encoding="utf-8")
    fa_before = fa_lease.read_bytes()
    check("leasehb/field-protected-aiqt-refused",
          "refused" in text_of(stop(fa)) and fa_lease.read_bytes() == fa_before, True)
    fr = build("fieldrename", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    before = snap(fr.lease)
    saved_replace = aiqt_hooks._orch_lease_hb_replace
    try:
        def no_replace(tmp_name, name, dirfd):
            raise OSError(13, "injected rename failure")
        aiqt_hooks._orch_lease_hb_replace = no_replace
        result = stop(fr)
    finally:
        aiqt_hooks._orch_lease_hb_replace = saved_replace
    check("leasehb/field-rename-fail-clean",
          "refused" in text_of(result) and snap(fr.lease) == before, True)
    check("leasehb/field-rename-fail-no-tmp",
          [n for n in os.listdir(fr.root) if n.endswith(".hb.tmp")], [])
    fv = build("fieldverify", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    saved_verify = aiqt_hooks._orch_lease_hb_verify
    try:
        aiqt_hooks._orch_lease_hb_verify = \
            lambda dirfd, name, cfg, sid, val: "injected verify failure"
        result = stop(fv)
    finally:
        aiqt_hooks._orch_lease_hb_verify = saved_verify
    check("leasehb/field-postverify-reported", "WAS replaced" in text_of(result), True)

    # --- optional lock ---
    lk = build("lock", hb(mode="refresh", lock_path="lock.file"))
    lock_file = lk.root / "lock.file"
    lock_file.write_text("", encoding="utf-8")
    fcntl = __import__("fcntl")
    holder_fd = os.open(str(lock_file), os.O_RDONLY)
    before = snap(lk.lease)
    try:
        fcntl.flock(holder_fd, fcntl.LOCK_EX)
        check("leasehb/lock-busy-skips",
              _verdict(stop(lk)) == "allow" and snap(lk.lease) == before, True)
    finally:
        os.close(holder_fd)
    stop(lk)
    check("leasehb/lock-released-writes", os.lstat(lk.lease).st_mtime_ns > before[1], True)
    lm = build("lockmissing", hb(mode="refresh", lock_path="absent.lock"))
    before = snap(lm.lease)
    check("leasehb/lock-missing-no-write",
          "refused" in text_of(stop(lm)) and snap(lm.lease) == before
          and not (lm.root / "absent.lock").exists(), True)
    lsym = build("locksym", hb(mode="refresh", lock_path="sym.lock"))
    lock_target = lsym.root / "lock-target"
    lock_target.write_text("", encoding="utf-8")
    os.symlink(str(lock_target), str(lsym.root / "sym.lock"))
    before = snap(lsym.lease)
    check("leasehb/lock-symlink-refused",
          "refused" in text_of(stop(lsym)) and snap(lsym.lease) == before, True)

    # --- the shared age classifier of the scope and resume boundaries: NOT a pure refactor, a
    # declared max_age_hours that main read as no horizon (0, a negative number, "24", false,
    # NaN), as one hour (true) or as never reached (infinity) now classifies 'cannot' ---
    check("leasehb/age-fresh-at-horizon", aiqt_hooks._orch_lease_age_class(24 * 3600, 24), "fresh")
    check("leasehb/age-stale-past-horizon",
          aiqt_hooks._orch_lease_age_class(24 * 3600 + 1, 24), "stale")
    check("leasehb/age-fresh-at-skew", aiqt_hooks._orch_lease_age_class(-300, 24), "fresh")
    check("leasehb/age-future-past-skew", aiqt_hooks._orch_lease_age_class(-301, 24), "future")
    check("leasehb/age-no-horizon-never-stale",
          aiqt_hooks._orch_lease_age_class(10 ** 9, None), "fresh")
    for cid, horizon in (("leasehb/age-future-zero-cannot", 0),
                         ("leasehb/age-future-negative-cannot", -1),
                         ("leasehb/age-future-string-cannot", "24"),
                         ("leasehb/age-future-nan-cannot", float("nan")),
                         ("leasehb/age-future-inf-cannot", float("inf")),
                         ("leasehb/age-future-bool-false-cannot", False),
                         ("leasehb/age-future-bool-true-cannot", True)):
        # the horizon is judged FIRST: a future-dated age (past the skew) can never hide an
        # unusable horizon behind 'future'
        check(cid, aiqt_hooks._orch_lease_age_class(-301, horizon), "cannot")

    # --- resume-audit probes ---
    ra = build("resumestale", hb(field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: 2020-01-01T00:00:00Z\n", age_secs=0)
    ra_reg = json.loads((ra.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    check("leasehb/resume-field-stale-finding",
          any("stale" in finding for finding in
              aiqt_hooks._orch_lease_hb_resume_findings(ra_reg, str(ra.root), ra_reg["lease"])),
          True)
    ra.lease.write_text("holder: s1\nheartbeat_utc: " + old_val + "\n", encoding="utf-8")
    check("leasehb/resume-clean-no-finding",
          aiqt_hooks._orch_lease_hb_resume_findings(ra_reg, str(ra.root), ra_reg["lease"]), [])
    ra_bad = json.loads(json.dumps(ra_reg))
    ra_bad["lease"]["heartbeat"]["mode"] = "nonesuch"
    check("leasehb/resume-bad-declaration-finding",
          any("invalid" in finding for finding in
              aiqt_hooks._orch_lease_hb_resume_findings(ra_bad, str(ra.root), ra_bad["lease"])),
          True)

    # --- fix vectors: the lock critical section (the lock is taken BEFORE the lease is read) ---
    def lock_seam_writer(mutate, _real=aiqt_hooks._orch_lease_hb_lock):
        """A cooperating writer at the lock-acquisition seam: it changes the lease IN PLACE (same
        inode, same size) and restores the original timestamps before the real lock is taken, so
        only a read inside the lock's critical section can see the change."""
        def wrapper(root_arg, lock_path):
            mutate()
            return _real(root_arg, lock_path)
        return wrapper

    li = build("lockinplace", hb(mode="refresh", lock_path="lock.file"))
    (li.root / "lock.file").write_text("", encoding="utf-8")
    li_st = os.lstat(li.lease)

    def swap_holder():
        with open(li.lease, "r+", encoding="utf-8") as fh:
            fh.write("holder: s2\n")
        os.utime(li.lease, ns=(li_st.st_atime_ns, li_st.st_mtime_ns))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam_writer(swap_holder)
        result = stop(li)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-inplace-holder-swap-no-write",
          _verdict(result) == "allow" and os.lstat(li.lease).st_mtime_ns == li_st.st_mtime_ns
          and li.lease.read_text(encoding="utf-8") == "holder: s2\n", True)

    lr = build("lockrelease", hb(mode="refresh", lock_path="lock.file",
                                 state_field="state", active_values=["active"]),
               lease_text="holder: s1\nstate: active\n")
    (lr.root / "lock.file").write_text("", encoding="utf-8")
    lr_st = os.lstat(lr.lease)

    def release_state():
        with open(lr.lease, "r+", encoding="utf-8") as fh:
            fh.write("holder: s1\nstate: closed\n")
        os.utime(lr.lease, ns=(lr_st.st_atime_ns, lr_st.st_mtime_ns))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam_writer(release_state)
        result = stop(lr)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-inplace-release-no-write",
          "never revived" in text_of(result)
          and os.lstat(lr.lease).st_mtime_ns == lr_st.st_mtime_ns
          and lr.lease.read_text(encoding="utf-8") == "holder: s1\nstate: closed\n", True)

    # --- fix vectors: an invalid declared horizon is a cannot-evaluate, never fresh ---
    check("leasehb/age-bool-horizon-cannot", aiqt_hooks._orch_lease_age_class(7200, True), "cannot")
    check("leasehb/age-zero-horizon-cannot", aiqt_hooks._orch_lease_age_class(60, 0), "cannot")
    check("leasehb/age-negative-horizon-cannot",
          aiqt_hooks._orch_lease_age_class(60, -1), "cannot")
    check("leasehb/age-nan-horizon-cannot",
          aiqt_hooks._orch_lease_age_class(60, float("nan")), "cannot")
    check("leasehb/age-inf-horizon-cannot",
          aiqt_hooks._orch_lease_age_class(60, float("inf")), "cannot")
    check("leasehb/age-string-horizon-cannot",
          aiqt_hooks._orch_lease_age_class(60, "24"), "cannot")
    for cid, horizon in (("leasehb/config-bad-nan-horizon", float("nan")),
                         ("leasehb/config-bad-inf-horizon", float("inf"))):
        bad = {"version": 1, "lease": {"path": "lease.txt", "max_age_hours": horizon,
                                       "holder_is_session_id": True, "heartbeat": hb()}}
        check(cid, aiqt_hooks._orch_lease_hb_config(bad)[0], "bad")

    sv = build("scopehorizon", None)
    sv_reg = json.loads((sv.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    check("leasehb/scope-valid-horizon-live",
          aiqt_hooks._orch_scope_live(sv_reg, str(sv.root), "s1"), True)
    for cid, horizon in (("leasehb/scope-bool-true-horizon-cannot", True),
                         ("leasehb/scope-bool-false-horizon-cannot", False),
                         ("leasehb/scope-zero-horizon-cannot", 0),
                         ("leasehb/scope-negative-horizon-cannot", -1),
                         ("leasehb/scope-nan-horizon-cannot", float("nan")),
                         ("leasehb/scope-inf-horizon-cannot", float("inf")),
                         ("leasehb/scope-string-horizon-cannot", "24")):
        # an unusable DECLARED horizon is a cannot-evaluate, never not-live: the callers stay
        # armed on it (the consumer vectors live in _leasehb_fix2_cases)
        sv_reg["lease"]["max_age_hours"] = horizon
        check(cid, aiqt_hooks._orch_scope_live(sv_reg, str(sv.root), "s1"), "cannot")
    sv_reg["lease"]["max_age_hours"] = None
    check("leasehb/scope-none-horizon-live",
          aiqt_hooks._orch_scope_live(sv_reg, str(sv.root), "s1"), True)
    sb = build("scopeboundary", None, age_secs=3600 - 120)
    sb_reg = json.loads((sb.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    sb_reg["lease"]["max_age_hours"] = 1
    check("leasehb/scope-just-inside-horizon-live",
          aiqt_hooks._orch_scope_live(sb_reg, str(sb.root), "s1"), True)
    sb_aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - 3600 - 120
    os.utime(sb.lease, times=(sb_aged, sb_aged))
    check("leasehb/scope-just-past-horizon-not-live",
          aiqt_hooks._orch_scope_live(sb_reg, str(sb.root), "s1"), False)
    rp = build("resumehorizon", None, age_secs=60)
    rp_reg = json.loads((rp.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    rp_reg["lease"]["max_age_hours"] = True
    check("leasehb/resume-invalid-horizon-finding",
          any("max_age_hours" in fi and "cannot-evaluate" in fi
              for fi in aiqt_hooks._orch_resume_probes(rp_reg, str(rp.root))), True)

    # --- fix vectors: nested state subtree protection, PostToolUse refresh coverage ---
    ns = Fixture(base, "nestedstate")
    ns_nested = ns.state / "nested"
    ns_nested.mkdir(parents=True)
    ns_lease = ns_nested / "lease.txt"
    ns_lease.write_text("holder: s1\nheartbeat_utc: " + old_val + "\n", encoding="utf-8")
    ns_reg_path = ns.root / ".aiqt" / "orchestration.local.json"
    ns_reg = json.loads(ns_reg_path.read_text(encoding="utf-8"))
    ns_reg["lease"] = {"path": str(ns_lease), "max_age_hours": 24, "holder_is_session_id": True,
                       "heartbeat": hb(mode="refresh", field="heartbeat_utc")}
    ns_reg_path.write_text(json.dumps(ns_reg), encoding="utf-8")
    ns_before = ns_lease.read_bytes()
    check("leasehb/field-protected-nested-state-refused",
          "refused" in text_of(stop(ns)) and ns_lease.read_bytes() == ns_before, True)

    pf = build("postfield", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    pf_result = post(pf)
    pf_after = pf.lease.read_text(encoding="utf-8")
    check("leasehb/post-fresh-mtime-due-field-refreshes",
          _verdict(pf_result) == "allow" and old_val not in pf_after
          and "heartbeat_utc: " in pf_after, True)
    pm = build("postmtime", hb(mode="refresh"))
    before = snap(pm.lease)
    check("leasehb/post-mtime-refreshes",
          _verdict(post(pm)) == "allow" and os.lstat(pm.lease).st_mtime_ns > before[1]
          and pm.lease.read_bytes() == before[0], True)

    # --- fix vectors: one row per refusal state, the git probe backed off while it persists ---
    tg = build("trackedrows", hb(mode="refresh", field="heartbeat_utc"),
               lease_text="holder: s1\nheartbeat_utc: " + old_val + "\n", age_secs=0)
    tg._git("add", "lease.txt")
    tg._git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track the lease")
    tracked_calls = []
    saved_tracked = aiqt_hooks._orch_lease_hb_git_tracked

    def counting_tracked(root_arg, path_arg, _real=saved_tracked):
        tracked_calls.append(path_arg)
        return _real(root_arg, path_arg)

    def tracked_rows():
        return [r for r in rows(tg) if "tracked" in r.get("detail", "")]
    try:
        aiqt_hooks._orch_lease_hb_git_tracked = counting_tracked
        stop(tg)
        for _ in range(2):
            post(tg)
            stop(tg)
        check("leasehb/refusal-row-once-per-state", len(tracked_rows()), 1)
        check("leasehb/refusal-no-git-call-on-repeat", len(tracked_calls), 1)
        saved_retry = getattr(aiqt_hooks, "_ORCH_LEASE_HB_RETRY_SECONDS", 600)
        try:
            aiqt_hooks._ORCH_LEASE_HB_RETRY_SECONDS = 0
            post(tg)
        finally:
            aiqt_hooks._ORCH_LEASE_HB_RETRY_SECONDS = saved_retry
        check("leasehb/refusal-backoff-reprobes-no-row",
              (len(tracked_calls), len(tracked_rows())), (2, 1))
        tg.lease.write_text("holder: s1\nheartbeat_utc: " + old_val + " \n", encoding="utf-8")
        stop(tg)
        check("leasehb/refusal-row-rearms-on-state-change", len(tracked_rows()), 2)
    finally:
        aiqt_hooks._orch_lease_hb_git_tracked = saved_tracked
    wg = build("warngaprows", hb())
    stop(wg)
    post(wg)
    post(wg)
    check("leasehb/warn-gap-row-once",
          len([r for r in rows(wg) if "gap" in r.get("detail", "")]), 1)
    cb = build("cfgbadrows", hb(mode="nonesuch"))
    stop(cb)
    post(cb)
    stop(cb)
    check("leasehb/config-bad-withholds-rows",
          len([r for r in rows(cb) if r.get("kind") == "lease-heartbeat"]), 0)

    # --- fix vectors: an out-of-tree lease passes the tracking gate ---
    ot = Fixture(base, "outoftree")
    ot_outside = base / "outside-tree"
    ot_outside.mkdir()
    ot_lease = ot_outside / "lease.txt"
    ot_lease.write_text("holder: s1\nheartbeat_utc: " + old_val + "\n", encoding="utf-8")
    ot_reg_path = ot.root / ".aiqt" / "orchestration.local.json"
    ot_reg = json.loads(ot_reg_path.read_text(encoding="utf-8"))
    ot_reg["lease"] = {"path": str(ot_lease), "max_age_hours": 24, "holder_is_session_id": True,
                       "heartbeat": hb(mode="refresh", field="heartbeat_utc")}
    ot_reg_path.write_text(json.dumps(ot_reg), encoding="utf-8")
    stop(ot)
    check("leasehb/field-out-of-tree-refreshes",
          old_val not in ot_lease.read_text(encoding="utf-8"), True)

    # --- fix vectors: the past-holder memory reader is bounded, non-blocking and no-follow ---
    mm = build("memfifo", hb())
    mm_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(mm.root)))
    mm_sd.mkdir(parents=True, exist_ok=True)
    os.mkfifo(str(mm_sd / "lease-heartbeat.json"))
    mm_dests = (str(mm_sd / "lease-heartbeat.json"), str(mm_sd / "guard-events.jsonl"))
    # TIMED, both calls: the memory reader and the whole Stop must return promptly on a FIFO
    # at the record's name; an O_NONBLOCK regression is a named red, never a hung suite
    mm_time = __import__("time")

    def mm_read():
        mm_t0 = mm_time.monotonic()
        mm_got = aiqt_hooks._orch_lease_hb_memory(mm_dests)
        # the latency bound is measured INSIDE the timed worker: "prompt" means the read
        # returned in under 5 s, not merely under the worker's own 30 s hang bound
        return (mm_got, mm_time.monotonic() - mm_t0 < 5)
    mm_done, mm_result = _leasehb_timed(mm_read)
    mm_mem, mm_fast = mm_result if mm_result is not None else (None, None)
    check("leasehb/memory-fifo-prompt-empty", (mm_done, mm_mem == {}, mm_fast),
          (True, True, True))
    mm_done2, mm_stop = _leasehb_timed(lambda: stop(mm))
    check("leasehb/memory-fifo-stop-still-warns",
          (mm_done2, "heartbeat gap" in text_of(mm_stop or (None, None, None))), (True, True))
    mo = build("memoversize", hb())
    mo_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(mo.root)))
    mo_sd.mkdir(parents=True, exist_ok=True)
    (mo_sd / "lease-heartbeat.json").write_text(
        json.dumps({"session_id": "s1", "pad": "x".ljust(70000, "x")}), encoding="utf-8")
    check("leasehb/memory-oversize-empty",
          aiqt_hooks._orch_lease_hb_memory(
              (str(mo_sd / "lease-heartbeat.json"), str(mo_sd / "guard-events.jsonl"))), {})
    ms = build("memsymlink", hb())
    ms_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(ms.root)))
    ms_sd.mkdir(parents=True, exist_ok=True)
    ms_real = ms.root / "real-mem.json"
    ms_real.write_text(json.dumps({"session_id": "s1"}), encoding="utf-8")
    os.symlink(str(ms_real), str(ms_sd / "lease-heartbeat.json"))
    check("leasehb/memory-symlink-empty",
          aiqt_hooks._orch_lease_hb_memory(
              (str(ms_sd / "lease-heartbeat.json"), str(ms_sd / "guard-events.jsonl"))), {})


def _leasehb_fix2_cases(tmp):
    """Component: the round-2 and round-3 lease-heartbeat fixes. (1) A 'cannot' horizon class
    keeps every lease-scoped guard armed and surfaced, never _allow(), per unusable form and
    under a PAST and a FUTURE lease mtime alike (the classifier judges the horizon first), with
    the horizon item NAMED in the stop, idle and yield deny text. (2) The keeper's bookkeeping
    destinations are resolved once per event, proven distinct from the declared lease (resolved
    path, inode, parent identity; an absent state directory is created for a real comparison)
    before any save or append, carried WITH the proof as one per-event holder that every writer
    and the crash recorder consume (a registry re-read fault cannot redirect one), and re-proven
    on the opened descriptor at the first open and at the lock seam, where a FAILED re-open
    records nothing. (3) Both refresh forms pass the protected-path and
    git-tracking gates, and each protected directory's identity-containment leg is exercised.
    (4) The backoff bounds at the production constant, the protection stat-fault refusals and
    the widened identity re-check each carry a vector. (5) A cooperating takeover under the lock
    reads as taken or lost, a non-busy flock fault is a named refusal (any other flock fault
    still closes the descriptor exactly once), and the tracking gate decides outside-ness on
    resolved paths, with a comparison fault a probe fault, never proven-untracked. (6) Round-5
    and round-6 vectors: a row or save happens only through a holder carrying the HELD,
    separation-proven lease descriptor, every append is re-checked on the descriptor it writes
    through at WRITE time (a destination aliased onto the lease after the proof is refused), a
    lease path at or above the state directory is refused before anything is created, the one
    crash row of a core fault is recorded inside the critical section, and descriptor
    ownership starts at the moment of acquisition. (7) Round-8 vectors: the saves check the
    temporary DESCRIPTOR before its first byte and the temporary NAME immediately before the
    replace, the holder build owns its state-directory descriptor across any fault, the
    busy-lock bookkeeping deferral surfaces on Stop only, and the same-user concurrency class
    keeps one disclosed-residual WITNESS vector (the lease's parent directory re-pointed at
    the state directory), asserted as landing, never as refused. (8) Round-9 vectors: the
    declared lock is a stable SEPARATE coordination file (refused by name at declaration
    validation, by resolved path and inode at the entry check, and by descriptor identity
    under the held lock, with an interleaving witness that a second cooperating keeper stays
    excluded through the whole critical section), a faulted lock defers like a busy one and
    names the fault, a busy-lock crash is reported as the crash, the unlinked-temporary vector
    observes the orphaned inode directly, descriptor ownership at the holder build is counted
    exactly once per descriptor (the previous-holder boundary included), and an
    unknown-identity temporary leftover is disclosed. (9) Round-10 vectors: the lock is
    refused wherever ANY plugin writer replaces files (a lock inside the state directory, a
    whole-file-writer target such as turn-state.json, the memory and events spellings, a
    relative spelling resolving to the lease and a hard-link alias, each at declaration
    validation and at the resume audit through the one shared read-only validation, and the
    state directory swept by identity under the held lock), the interleaving witness covers
    the WHOLE read-prove-write span with only EAGAIN/EWOULDBLOCK read as busy, the
    previous-holder replacement boundary is fault-injected on both sides of the close and
    resolved through the REAL event cleanup, a due refresh under a faulted lock renders the
    flock fault exactly once, both lock-separation fault legs and the lock-identity seam are
    exercised, the unknown-identity temporary is disclosed for any exception class and a
    cleanup stat fault discloses the name and the fault, and the FIFO prompt vector keeps its
    sub-5s latency bound inside the timed worker. (10) Round-11 vectors: the event finalizer
    and the judge's re-opened-then-lock disposition attempt every remaining close exactly once
    under a RuntimeError at the state-descriptor close (before and after its syscall) and
    inside the re-opened descriptor's close, with the lock released; the interleaving witness
    probes every write syscall of the final row (a forced short write included) and the ends
    of the row and of the memory save; the lock/state-directory containment is
    component-aware ('/', an ordinary and a nested descendant, a textual-prefix sibling, a
    comparison fault); a non-inventory state-directory lock separates the containment and
    parent-identity legs at declaration and resume; both sweep fault legs are named; the
    declaration and resume create no state directory; and the field writer's temporary
    cleanup is finally-based and identity-aware with its residue disclosed. (11) Further
    vectors: the field writer's residue disclosure rides the hook result whenever an
    exception is propagating (a non-OSError interruption whose cleanup cannot remove the
    temporary, and a named OSError failure followed by a non-OSError close fault); a lock
    declared as the state directory itself is refused (the equality end of the containment
    bound), at declaration validation and through the event entry, where a lock path that
    reaches the state directory only after declaration validation is refused by the entry
    recheck before any lease open, lock or write; and the walk and lock cleanups keep the
    FIRST cleanup fault. (12) A non-OSError fault raised inside the temporary cleanup itself
    (at its stat or its unlink) still names the temporary through the hook result, for the
    field writer and the memory save alike, and the fault still propagates, the FIRST fault
    winning when another is already propagating and the named failure leading the
    disclosure. (13) The disclosure names the temporary even when the cleanup fault's text
    cannot be rendered, the FIRST fault still the one reported; a STOP raised in the cleanup
    (an interrupt or an exit, not an Exception) propagates ahead of an Exception already
    propagating, and a cleanup fault of either class never displaces a stop already
    propagating."""
    base = tmp / "leasehb2"
    base.mkdir()

    def hb(**overrides):
        block = {"mode": "warn", "format": "lines", "holder_field": "holder",
                 "refresh_after_minutes": 10, "warn_after_minutes": 30}
        block.update(overrides)
        return block

    def stop(f, sid="s1"):
        payload = f.payload("Stop")
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_stop(payload)

    def post(f, sid="s1"):
        payload = f.payload("PostToolUse", "Bash", {"command": "true"})
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_post(payload)

    def text_of(result):
        obj = result[1]
        return obj.get("systemMessage", "") if isinstance(obj, dict) else ""

    def snap(path):
        st = os.lstat(path)
        return (path.read_bytes(), st.st_mtime_ns)

    def age_file(path, age_secs):
        aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - age_secs
        os.utime(path, times=(aged, aged))

    def declare(f, lease_path, block):
        reg_path = f.root / ".aiqt" / "orchestration.local.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["lease"] = {"path": str(lease_path), "max_age_hours": 24,
                        "holder_is_session_id": True, "heartbeat": block}
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        return reg


    # --- (1) the 'cannot' horizon class: every lease-scoped guard armed and surfaced ---
    sc = Fixture(base, "scopecannot")
    sc.set_items([])
    sc_reg_path = sc.root / ".aiqt" / "orchestration.local.json"
    sc_future = datetime.datetime.now(datetime.timezone.utc).timestamp() + 3600
    sc_ledger = Path(aiqt_hooks._orch_state_dir_for_root(str(sc.root))) / "dispatch-ledger.jsonl"

    def set_horizon(horizon):
        sc_reg = json.loads(sc_reg_path.read_text(encoding="utf-8"))
        sc_reg["lease"]["max_age_hours"] = horizon
        sc_reg_path.write_text(json.dumps(sc_reg), encoding="utf-8")

    # a PAST mtime and a FUTURE mtime (past the skew) both keep every guard armed and
    # surfaced, for EVERY unusable horizon form: the classifier judges the horizon FIRST, so a
    # future-dated lease can never hide an unusable horizon behind the 'future' class (a silent
    # guards-off). Literal id rows, so the execution gate can resolve each statically.
    for cid_stop, cid_idle, cid_yield, cid_ledger, cid_stamp, tag, horizon, future in (
            ("leasehb/scope-cannot-stop-armed-zero",
             "leasehb/scope-cannot-idle-armed-zero",
             "leasehb/scope-cannot-yield-denies-zero",
             "leasehb/scope-cannot-ledger-armed-zero",
             "leasehb/scope-cannot-stamp-armed-zero",
             "zero", 0, False),
            ("leasehb/scope-cannot-future-stop-armed-zero",
             "leasehb/scope-cannot-future-idle-armed-zero",
             "leasehb/scope-cannot-future-yield-denies-zero",
             "leasehb/scope-cannot-future-ledger-armed-zero",
             "leasehb/scope-cannot-future-stamp-armed-zero",
             "future-zero", 0, True),
            ("leasehb/scope-cannot-stop-armed-negative",
             "leasehb/scope-cannot-idle-armed-negative",
             "leasehb/scope-cannot-yield-denies-negative",
             "leasehb/scope-cannot-ledger-armed-negative",
             "leasehb/scope-cannot-stamp-armed-negative",
             "negative", -1, False),
            ("leasehb/scope-cannot-future-stop-armed-negative",
             "leasehb/scope-cannot-future-idle-armed-negative",
             "leasehb/scope-cannot-future-yield-denies-negative",
             "leasehb/scope-cannot-future-ledger-armed-negative",
             "leasehb/scope-cannot-future-stamp-armed-negative",
             "future-negative", -1, True),
            ("leasehb/scope-cannot-stop-armed-string",
             "leasehb/scope-cannot-idle-armed-string",
             "leasehb/scope-cannot-yield-denies-string",
             "leasehb/scope-cannot-ledger-armed-string",
             "leasehb/scope-cannot-stamp-armed-string",
             "string", "24", False),
            ("leasehb/scope-cannot-future-stop-armed-string",
             "leasehb/scope-cannot-future-idle-armed-string",
             "leasehb/scope-cannot-future-yield-denies-string",
             "leasehb/scope-cannot-future-ledger-armed-string",
             "leasehb/scope-cannot-future-stamp-armed-string",
             "future-string", "24", True),
            ("leasehb/scope-cannot-stop-armed-nan",
             "leasehb/scope-cannot-idle-armed-nan",
             "leasehb/scope-cannot-yield-denies-nan",
             "leasehb/scope-cannot-ledger-armed-nan",
             "leasehb/scope-cannot-stamp-armed-nan",
             "nan", float("nan"), False),
            ("leasehb/scope-cannot-future-stop-armed-nan",
             "leasehb/scope-cannot-future-idle-armed-nan",
             "leasehb/scope-cannot-future-yield-denies-nan",
             "leasehb/scope-cannot-future-ledger-armed-nan",
             "leasehb/scope-cannot-future-stamp-armed-nan",
             "future-nan", float("nan"), True),
            ("leasehb/scope-cannot-stop-armed-inf",
             "leasehb/scope-cannot-idle-armed-inf",
             "leasehb/scope-cannot-yield-denies-inf",
             "leasehb/scope-cannot-ledger-armed-inf",
             "leasehb/scope-cannot-stamp-armed-inf",
             "inf", float("inf"), False),
            ("leasehb/scope-cannot-future-stop-armed-inf",
             "leasehb/scope-cannot-future-idle-armed-inf",
             "leasehb/scope-cannot-future-yield-denies-inf",
             "leasehb/scope-cannot-future-ledger-armed-inf",
             "leasehb/scope-cannot-future-stamp-armed-inf",
             "future-inf", float("inf"), True),
            ("leasehb/scope-cannot-stop-armed-bool-false",
             "leasehb/scope-cannot-idle-armed-bool-false",
             "leasehb/scope-cannot-yield-denies-bool-false",
             "leasehb/scope-cannot-ledger-armed-bool-false",
             "leasehb/scope-cannot-stamp-armed-bool-false",
             "bool-false", False, False),
            ("leasehb/scope-cannot-future-stop-armed-bool-false",
             "leasehb/scope-cannot-future-idle-armed-bool-false",
             "leasehb/scope-cannot-future-yield-denies-bool-false",
             "leasehb/scope-cannot-future-ledger-armed-bool-false",
             "leasehb/scope-cannot-future-stamp-armed-bool-false",
             "future-bool-false", False, True),
            ("leasehb/scope-cannot-stop-armed-bool-true",
             "leasehb/scope-cannot-idle-armed-bool-true",
             "leasehb/scope-cannot-yield-denies-bool-true",
             "leasehb/scope-cannot-ledger-armed-bool-true",
             "leasehb/scope-cannot-stamp-armed-bool-true",
             "bool-true", True, False),
            ("leasehb/scope-cannot-future-stop-armed-bool-true",
             "leasehb/scope-cannot-future-idle-armed-bool-true",
             "leasehb/scope-cannot-future-yield-denies-bool-true",
             "leasehb/scope-cannot-future-ledger-armed-bool-true",
             "leasehb/scope-cannot-future-stamp-armed-bool-true",
             "future-bool-true", True, True)):
        set_horizon(horizon)
        mtime = sc_future if future else \
            datetime.datetime.now(datetime.timezone.utc).timestamp() - 60
        os.utime(sc.lease, times=(mtime, mtime))
        sc.set_turn_state({})
        code, _obj, err = aiqt_hooks.orch_stop_guard(sc.payload("Stop"))
        check(cid_stop, (code, "cannot-evaluate" in (err or "")), (2, True))
        sc.set_turn_state({})
        code, _obj, err = aiqt_hooks.orch_teammate_idle(sc.payload("TeammateIdle"))
        check(cid_idle, (code, "cannot-evaluate" in (err or "")), (2, True))
        sc.set_turn_state({})
        yres = aiqt_hooks.orch_yield_tool(sc.payload("PreToolUse", "ScheduleWakeup",
                                                     {"note": "recheck"}))
        check(cid_yield, _verdict(yres), "deny")
        tid = "task-cannot-" + tag
        lg = sc.payload("PostToolUse", "Bash", {"command": "true",
                                                "run_in_background": True})
        lg["tool_response"] = {"task_id": tid}
        lres = aiqt_hooks.orch_dispatch_ledger(lg)
        lrows = [json.loads(line) for line in
                 sc_ledger.read_text(encoding="utf-8").splitlines()
                 if line.strip()] if sc_ledger.exists() else []
        check(cid_ledger, (_verdict(lres), any(r.get("task_id") == tid for r in lrows)),
              ("warn", True))
        sc.set_turn_state({})
        pp = sc.payload("UserPromptSubmit")
        pp["prompt"] = "hello operator " + tag
        pres = aiqt_hooks.orch_prompt_stamp(pp)
        check(cid_stamp, (_verdict(pres),
                          isinstance(sc.turn_state().get("last_human_input_utc"), str)),
              ("warn", True))
    set_horizon(0)
    sc_aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - 60
    os.utime(sc.lease, times=(sc_aged, sc_aged))
    sc.set_turn_state({})
    _code, _obj, err = aiqt_hooks.orch_stop_guard(sc.payload("Stop"))
    check("leasehb/scope-cannot-stop-names-horizon",
          "lease:max_age_hours" in (err or ""), True)
    sc.set_turn_state({})
    _code, _obj, err = aiqt_hooks.orch_teammate_idle(sc.payload("TeammateIdle"))
    check("leasehb/scope-cannot-idle-names-horizon",
          "lease:max_age_hours" in (err or ""), True)
    sc.set_turn_state({})
    yres = aiqt_hooks.orch_yield_tool(sc.payload("PreToolUse", "ScheduleWakeup",
                                                 {"note": "recheck"}))
    yobj = yres[1] if isinstance(yres[1], dict) else {}
    yspec = yobj.get("hookSpecificOutput")
    if not isinstance(yspec, dict):
        yspec = {}
    check("leasehb/scope-cannot-yield-names-horizon",
          (_verdict(yres), "lease:max_age_hours" in str(yspec.get("permissionDecisionReason"))),
          ("deny", True))
    sc.set_turn_state({"stop_denials": aiqt_hooks._ORCH_LOOP_BOUND})
    check("leasehb/scope-cannot-loop-bound-releases",
          _verdict(aiqt_hooks.orch_stop_guard(sc.payload("Stop"))), "warn")
    sc.mode.write_text("Operating-mode: unattended\n", encoding="utf-8")
    sc_reg = json.loads(sc_reg_path.read_text(encoding="utf-8"))
    check("leasehb/scope-cannot-mode-live-outranks",
          aiqt_hooks._orch_scope_live(sc_reg, str(sc.root), "s1"), True)
    sc.mode.write_text("", encoding="utf-8")
    hm = Fixture(base, "holdercannot")
    hm.lease.write_text("holder: s1\n", encoding="utf-8")
    hm_reg = json.loads((hm.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    hm_reg["lease"]["max_age_hours"] = 0
    hm_reg["lease"]["holder_is_session_id"] = True
    check("leasehb/scope-cannot-holder-cannot",
          aiqt_hooks._orch_scope_live(hm_reg, str(hm.root), "s1"), "cannot")
    nh = Fixture(base, "nonholder")
    nh.lease.write_text("holder: s1\n", encoding="utf-8")
    nh_reg = json.loads((nh.root / ".aiqt" / "orchestration.local.json")
                        .read_text(encoding="utf-8"))
    nh_reg["lease"]["max_age_hours"] = 0
    nh_reg["lease"]["holder_is_session_id"] = True
    check("leasehb/scope-cannot-non-holder-false",
          aiqt_hooks._orch_scope_live(nh_reg, str(nh.root), "s2"), False)


    # --- (2) lease/bookkeeping separation before any save or append, crash paths included ---
    def coll(name, block, leaf, lease_text, age_secs=7200):
        f = Fixture(base, name)
        f.state.mkdir(parents=True, exist_ok=True)
        lease_path = f.state / leaf
        if lease_text is not None:
            lease_path.write_text(lease_text, encoding="utf-8")
            age_file(lease_path, age_secs)
        declare(f, lease_path, block)
        return f, lease_path

    cw, cw_lease = coll("collwarn", hb(), "lease-heartbeat.json", "holder: s1\n")
    before = snap(cw_lease)
    result = stop(cw)
    check("leasehb/collision-memory-warn-no-write",
          (snap(cw_lease) == before, _verdict(result), "coincides" in text_of(result)),
          (True, "warn", True))
    check("leasehb/collision-memory-warn-no-row",
          (cw.state / "guard-events.jsonl").exists(), False)
    cs, cs_lease = coll("collstale", hb(mode="refresh"), "lease-heartbeat.json",
                        "holder: s1\n", age_secs=25 * 3600)
    before = snap(cs_lease)
    stop(cs)
    check("leasehb/collision-memory-stale-no-write", snap(cs_lease), before)
    cr2, cr2_lease = coll("collreleased",
                          hb(mode="refresh", state_field="state", active_values=["active"]),
                          "lease-heartbeat.json", "holder: s1\nstate: released\n")
    before = snap(cr2_lease)
    stop(cr2)
    check("leasehb/collision-memory-released-no-write", snap(cr2_lease), before)
    cm, cm_lease = coll("collmalformed", hb(), "guard-events.jsonl",
                        "holder: s2\nholder: s2\n")
    before = snap(cm_lease)
    stop(cm)
    post(cm)
    check("leasehb/collision-events-malformed-no-append", snap(cm_lease), before)
    ca, ca_lease = coll("collabsent", hb(), "guard-events.jsonl", None)
    result = stop(ca)
    # a VALID declaration whose lease coincides with the absent events destination: the named
    # collision creates nothing (an INVALID declaration now refuses before the separation
    # check even runs, covered by leasehb/invalid-declaration-creates-no-statedir)
    check("leasehb/collision-absent-target-no-create",
          (result[0], ca_lease.exists(), "coincides" in text_of(result)), (0, False, True))
    cc, cc_lease = coll("collcrash", hb(), "guard-events.jsonl", None)
    saved_cfg_fn = aiqt_hooks._orch_lease_hb_config
    try:
        def boom(reg_arg):
            raise RuntimeError("leasehb2-crash")
        aiqt_hooks._orch_lease_hb_config = boom
        result = post(cc)
    finally:
        aiqt_hooks._orch_lease_hb_config = saved_cfg_fn
    check("leasehb/collision-crash-no-row",
          (_verdict(result), cc_lease.exists()), ("allow", False))
    cp, cp_lease = coll("collparent", hb(), "other-lease.txt", "holder: s1\n")
    before = snap(cp_lease)
    result = stop(cp)
    check("leasehb/collision-statedir-parent-refused",
          (snap(cp_lease) == before, "state directory" in text_of(result),
           (cp.state / "guard-events.jsonl").exists()), (True, True, False))
    rf2 = Fixture(base, "regfault")
    rf2_default_sd = Path(aiqt_hooks._state_dir_from_registry(str(rf2.root), None))
    rf2_default_sd.mkdir(parents=True, exist_ok=True)
    rf2_lease = rf2_default_sd / "guard-events.jsonl"
    rf2_lease.write_text("holder: s1\n", encoding="utf-8")
    declare(rf2, rf2_lease, hb(mode="nonesuch"))
    before = snap(rf2_lease)
    rf2_calls = []
    saved_registry = aiqt_hooks._orch_registry
    # the destinations are derived ONCE from the event entry's single read: EVERY registry
    # re-read after it faults here (resolving the DEFAULT state dir, where this lease lives), so
    # a separation check or writer that re-reads mid-event lands in the lease and goes red; on
    # the fixed path the invalid declaration holds no descriptor proof, so NOTHING is recorded
    # anywhere and the lease stays untouched
    def flaky_registry(root_arg, *a, **k):
        rf2_calls.append(root_arg)
        if len(rf2_calls) > 1:
            return ("bad", "injected registry re-read fault")
        return saved_registry(root_arg, *a, **k)
    try:
        aiqt_hooks._orch_registry = flaky_registry
        stop(rf2)
    finally:
        aiqt_hooks._orch_registry = saved_registry
    rf2_log = rf2.state / "guard-events.jsonl"
    rf2_rows = [json.loads(line) for line in rf2_log.read_text(encoding="utf-8").splitlines()
                if line.strip()] if rf2_log.exists() else []
    check("leasehb/registry-fault-withholds-row",
          any("invalid heartbeat declaration" in r.get("detail", "") for r in rf2_rows), False)
    check("leasehb/registry-fault-lease-untouched", snap(rf2_lease), before)
    ci = Fixture(base, "collalias")
    ci.state.mkdir(parents=True, exist_ok=True)
    ci_events = ci.state / "guard-events.jsonl"
    ci_events.write_text("", encoding="utf-8")
    ci_alias = ci.root / "alias-lease.txt"
    os.link(str(ci_events), str(ci_alias))
    declare(ci, ci_alias, hb())
    before = snap(ci_events)
    result = stop(ci)
    check("leasehb/collision-inode-alias-no-append",
          (snap(ci_events) == before, "shares an inode" in text_of(result)), (True, True))

    def declared_rows(f):
        log = f.state / "guard-events.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    cf = Fixture(base, "crashregfault")
    cf_default_sd = Path(aiqt_hooks._state_dir_from_registry(str(cf.root), None))
    cf_default_sd.mkdir(parents=True, exist_ok=True)
    cf_lease = cf_default_sd / "guard-events.jsonl"
    cf_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(cf_lease, 7200)
    declare(cf, cf_lease, hb())
    cf_before = snap(cf_lease)
    cf_reads = []
    saved_registry2 = aiqt_hooks._orch_registry
    saved_memory_fn = aiqt_hooks._orch_lease_hb_memory

    def cf_flaky_registry(root_arg, *a, **k):
        cf_reads.append(root_arg)
        if len(cf_reads) > 1:
            return ("bad", "injected registry re-read fault")
        return saved_registry2(root_arg, *a, **k)

    def cf_boom(dests_arg):
        raise RuntimeError("injected core fault")
    # the round-3 reproduction, now a vector: a core fault WITH every registry re-read faulted
    # and the declared lease AT the default guard-events path; the fault fires BEFORE the first
    # open, so the crash recorder holds no descriptor proof and records NOTHING anywhere (one
    # registry read, no redirect), and the lease stays byte-identical
    try:
        aiqt_hooks._orch_registry = cf_flaky_registry
        aiqt_hooks._orch_lease_hb_memory = cf_boom
        result = stop(cf)
    finally:
        aiqt_hooks._orch_registry = saved_registry2
        aiqt_hooks._orch_lease_hb_memory = saved_memory_fn
    check("leasehb/crash-regfault-lease-untouched",
          (snap(cf_lease) == cf_before, _verdict(result), len(cf_reads)), (True, "allow", 1))
    check("leasehb/crash-pre-open-writes-nothing-anywhere",
          (any("core fault" in r.get("detail", "") for r in declared_rows(cf)),
           (cf.state / "lease-heartbeat.json").exists()), (False, False))
    cb = Fixture(base, "crashnoproof")
    cb_default_sd = Path(aiqt_hooks._state_dir_from_registry(str(cb.root), None))
    cb_default_sd.mkdir(parents=True, exist_ok=True)
    cb_lease = cb_default_sd / "guard-events.jsonl"
    declare(cb, cb_lease, hb())
    cb_calls = []
    saved_sep_fn = aiqt_hooks._orch_lease_hb_bookkeeping_sep

    def cb_sep_boom(path_arg, dests_arg, _real=saved_sep_fn):
        cb_calls.append(path_arg)
        if len(cb_calls) == 1:
            raise RuntimeError("injected transient separation fault")
        return _real(path_arg, dests_arg)
    # a TRANSIENT fault in the separation proof itself: the event crashes BEFORE the holder is
    # proven, so the recorder is handed no proof and writes NOTHING anywhere (an unknown lease
    # is unknown, never absent); a recorder that re-derived and re-proved for itself would see
    # the fault clear, land its row in the declared log, and go red here
    try:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = cb_sep_boom
        result = stop(cb)
    finally:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = saved_sep_fn
    check("leasehb/crash-before-proof-writes-nothing",
          (_verdict(result), cb_lease.exists(), (cb.state / "guard-events.jsonl").exists()),
          ("allow", False, False))
    cpa = Fixture(base, "collparentabsent")
    # the state directory does NOT exist yet: the parent-identity leg must still refuse a lease
    # declared directly inside it (the separation proof creates the directory for a REAL
    # identity comparison instead of skipping the leg)
    cpa_lease = cpa.state / "pending-lease.txt"
    declare(cpa, cpa_lease, hb())
    result = stop(cpa)
    check("leasehb/collision-absent-statedir-parent-refused",
          ("state directory" in text_of(result),
           (cpa.state / "guard-events.jsonl").exists(), cpa.state.is_dir()),
          (True, False, True))
    fo = Fixture(base, "firstopenalias")
    fo.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(fo.lease, 7200)
    declare(fo, fo.lease, hb())
    fo_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(fo.root)))
    fo_sd.mkdir(parents=True, exist_ok=True)
    fo_events = fo_sd / "guard-events.jsonl"
    fo_events.write_text(json.dumps({"ts": "t0", "kind": "seed", "decision": "seed",
                                     "detail": "seed"}) + "\n", encoding="utf-8")
    fo_before = fo_events.read_bytes()
    saved_memory_fn2 = aiqt_hooks._orch_lease_hb_memory

    def fo_plant(dests_arg):
        # between the path-based entry proof and the FIRST open, a cooperating writer swaps
        # the lease for a hard link to the events log; only the descriptor-bound re-proof on
        # the first opened descriptor can refuse before a row would append into the alias
        tmp_link = fo.root / "alias.tmp"
        os.link(str(fo_events), str(tmp_link))
        os.replace(str(tmp_link), str(fo.lease))
        return {}
    try:
        aiqt_hooks._orch_lease_hb_memory = fo_plant
        result = stop(fo)
    finally:
        aiqt_hooks._orch_lease_hb_memory = saved_memory_fn2
    check("leasehb/first-open-alias-no-append",
          (fo_events.read_bytes() == fo_before, "shares an inode" in text_of(result)),
          (True, True))


    # --- (3) both refresh forms pass the protected and tracking gates ---
    old_ts = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=2)).replace(microsecond=0)
    old_val = old_ts.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    def gated(name, rel_parts, field=None):
        f = Fixture(base, name)
        lease_path = f.root.joinpath(*rel_parts)
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        if field is None:
            lease_path.write_text("holder: s1\n", encoding="utf-8")
            block = hb(mode="refresh")
        else:
            lease_path.write_text("holder: s1\n" + field + ": " + old_val + "\n",
                                  encoding="utf-8")
            block = hb(mode="refresh", field=field)
        age_file(lease_path, 7200)
        declare(f, lease_path, block)
        return f, lease_path

    for cid, parts in (
            ("leasehb/mtime-protected-nested-state-refused", ("state", "nested", "lease.txt")),
            ("leasehb/mtime-protected-aiqt-refused", (".aiqt", "lease.txt")),
            ("leasehb/mtime-protected-git-refused", (".git", "lease.txt")),
            ("leasehb/mtime-protected-claude-refused", (".claude", "lease.txt")),
            ("leasehb/mtime-protected-plugin-refused", ("plugin", "lease.txt"))):
        f, lease_path = gated("m" + parts[0].strip(".") + "x", parts)
        before = snap(lease_path)
        result = stop(f)
        check(cid, ("refused" in text_of(result), snap(lease_path) == before), (True, True))
    for cid, parts in (
            ("leasehb/field-protected-git-refused", (".git", "lease.txt")),
            ("leasehb/field-protected-claude-refused", (".claude", "lease.txt")),
            ("leasehb/field-protected-plugin-refused", ("plugin", "lease.txt"))):
        f, lease_path = gated("f" + parts[0].strip(".") + "x", parts, field="heartbeat_utc")
        before = snap(lease_path)
        result = stop(f)
        check(cid, ("refused" in text_of(result), snap(lease_path) == before), (True, True))
    mt, mt_lease = gated("mtimetracked", ("lease.txt",))
    mt._git("add", "lease.txt")
    mt._git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track the lease")
    before = snap(mt_lease)
    result = stop(mt)
    check("leasehb/mtime-git-tracked-refused",
          ("refused" in text_of(result), snap(mt_lease) == before), (True, True))


    # --- (4a) a protection check that cannot be evaluated refuses, in both forms ---
    gf, gf_lease = gated("gatefaultm", ("lease.txt",))
    fault_target = str(gf.root / "plugin")
    real_stat = os.stat

    def faulty_stat(p, *a, **k):
        if not a and not k and isinstance(p, (str, bytes, os.PathLike)) \
                and os.fspath(p) == fault_target:
            raise PermissionError(13, "injected stat fault")
        return real_stat(p, *a, **k)

    before = snap(gf_lease)
    try:
        os.stat = faulty_stat
        result = stop(gf)
    finally:
        os.stat = real_stat
    check("leasehb/mtime-gate-cannot-evaluate-refused",
          ("cannot be evaluated" in text_of(result), snap(gf_lease) == before), (True, True))
    gff, gff_lease = gated("gatefaultf", ("lease.txt",), field="heartbeat_utc")
    fault_target = str(gff.root / "plugin")
    before = snap(gff_lease)
    try:
        os.stat = faulty_stat
        result = stop(gff)
    finally:
        os.stat = real_stat
    check("leasehb/field-gate-cannot-evaluate-refused",
          ("cannot be evaluated" in text_of(result), snap(gff_lease) == before), (True, True))

    # --- (4b) the stat-fault refusal legs of _orch_lease_hb_protected, called direct ---
    pfx = Fixture(base, "pfault")
    pf_lease = pfx.root / "sub" / "lease.txt"
    pf_lease.parent.mkdir()
    pf_lease.write_text("holder: s1\n", encoding="utf-8")
    pf_st = os.stat(pf_lease)
    fault_target = str(pfx.root / "plugin")
    try:
        os.stat = faulty_stat
        got = aiqt_hooks._orch_lease_hb_protected(str(pfx.root), str(pf_lease), pf_st,
                                                  str(pfx.root / "state"))
    finally:
        os.stat = real_stat
    check("leasehb/protected-dir-stat-fault-refuses", "cannot be evaluated" in (got or ""), True)
    real_lstat = os.lstat
    lstat_target = str(pfx.root / "sub")

    def faulty_lstat(p, *a, **k):
        if not a and not k and isinstance(p, (str, bytes, os.PathLike)) \
                and os.fspath(p) == lstat_target:
            raise PermissionError(13, "injected lstat fault")
        return real_lstat(p, *a, **k)

    try:
        os.lstat = faulty_lstat
        got = aiqt_hooks._orch_lease_hb_protected(str(pfx.root), str(pf_lease), pf_st,
                                                  str(pfx.root / "state"))
    finally:
        os.lstat = real_lstat
    check("leasehb/protected-ancestor-stat-fault-refuses",
          "cannot be evaluated" in (got or ""), True)
    lstat_target = str(pfx.root / ".aiqt" / "orchestration.local.json")
    try:
        os.lstat = faulty_lstat
        got = aiqt_hooks._orch_lease_hb_protected(str(pfx.root), str(pf_lease), pf_st,
                                                  str(pfx.root / "state"))
    finally:
        os.lstat = real_lstat
    check("leasehb/protected-registry-stat-fault-refuses",
          "cannot be evaluated" in (got or ""), True)
    # the identity (st_dev, st_ino) containment leg for each protected directory, reached
    # WITHOUT its name on the path (as a bind-mounted alias would be): an injected lstat maps a
    # neutral ancestor onto the protected directory's identity, so a mutant that drops any pdir
    # tuple entry goes red (the lexical component check above it cannot catch this spelling)
    (pfx.root / ".claude").mkdir()
    (pfx.root / "plugin").mkdir()
    pv_vault = pfx.root / "vault"
    pv_vault.mkdir()
    pv_lease = pv_vault / "lease.txt"
    pv_lease.write_text("holder: s1\n", encoding="utf-8")
    pv_st = os.stat(pv_lease)
    for cid, pdir_name in (("leasehb/protected-identity-git", ".git"),
                           ("leasehb/protected-identity-aiqt", ".aiqt"),
                           ("leasehb/protected-identity-claude", ".claude"),
                           ("leasehb/protected-identity-plugin", "plugin")):
        pdir_st = os.stat(pfx.root / pdir_name)

        def alias_lstat(p, *a, _pdir_st=pdir_st, **k):
            if not a and not k and isinstance(p, (str, bytes, os.PathLike)) \
                    and os.fspath(p) == str(pv_vault):
                return _pdir_st
            return real_lstat(p, *a, **k)

        try:
            os.lstat = alias_lstat
            got = aiqt_hooks._orch_lease_hb_protected(str(pfx.root), str(pv_lease), pv_st,
                                                      str(pfx.root / "state"))
        finally:
            os.lstat = real_lstat
        check(cid, "inside a protected directory subtree" in (got or "")
              and (os.sep + pdir_name) in (got or ""), True)


    # --- (4c) the widened identity re-check refuses on a same-inode change, both forms ---
    def bump_restat(dirfd, name_arg, _real=aiqt_hooks._orch_lease_hb_restat):
        st0 = _real(dirfd, name_arg)
        os.utime(name_arg, ns=(st0.st_atime_ns, st0.st_mtime_ns + 1), dir_fd=dirfd,
                 follow_symlinks=False)
        return _real(dirfd, name_arg)

    rw, rw_lease = gated("recheckm", ("lease.txt",))
    before = snap(rw_lease)
    saved_restat = aiqt_hooks._orch_lease_hb_restat
    try:
        aiqt_hooks._orch_lease_hb_restat = bump_restat
        result = stop(rw)
    finally:
        aiqt_hooks._orch_lease_hb_restat = saved_restat
    check("leasehb/mtime-recheck-widened-refuses",
          ("refused" in text_of(result), rw_lease.read_bytes() == before[0],
           os.lstat(rw_lease).st_mtime_ns == before[1] + 1), (True, True, True))
    rf, rf_lease = gated("recheckf", ("lease.txt",), field="heartbeat_utc")
    saved_restat = aiqt_hooks._orch_lease_hb_restat
    try:
        aiqt_hooks._orch_lease_hb_restat = bump_restat
        result = stop(rf)
    finally:
        aiqt_hooks._orch_lease_hb_restat = saved_restat
    check("leasehb/field-recheck-widened-refuses",
          ("refused" in text_of(result), old_val in rf_lease.read_text(encoding="utf-8")),
          (True, True))

    # --- (4d) the backoff bounds at the PRODUCTION constant, each end just inside/at/past ---
    bo = Fixture(base, "backoff2")
    bo_dests = (str(bo.root / "state" / "lease-heartbeat.json"),
                str(bo.root / "state" / "guard-events.jsonl"))
    bo_st = os.stat(bo.lease)
    bo_key = [bo_st.st_dev, bo_st.st_ino, bo_st.st_mtime_ns, bo_st.st_size]
    probes = []
    saved_tracked = aiqt_hooks._orch_lease_hb_git_tracked

    def counting_tracked(root_arg, path_arg):
        probes.append(path_arg)
        return True

    bo_now = datetime.datetime.now(datetime.timezone.utc)
    check("leasehb/backoff-production-constant", aiqt_hooks._ORCH_LEASE_HB_RETRY_SECONDS, 600)
    bo_fd = os.open(str(bo.lease), os.O_RDONLY)
    try:
        aiqt_hooks._orch_lease_hb_git_tracked = counting_tracked
        for cid, elapsed, want_probe in (
                ("leasehb/backoff-just-before-zero-reprobes", -0.001, 1),
                ("leasehb/backoff-at-zero-skips", 0.0, 0),
                ("leasehb/backoff-just-past-zero-skips", 0.001, 0),
                ("leasehb/backoff-just-inside-bound-skips", 599.999, 0),
                ("leasehb/backoff-at-bound-reprobes", 600.0, 1),
                ("leasehb/backoff-just-past-bound-reprobes", 600.001, 1)):
            memory = {"last_row": {"kind": "no-write",
                                   "code": aiqt_hooks._ORCH_LEASE_HB_TRACKED_DETAIL,
                                   "lease": list(bo_key),
                                   "ts": (bo_now
                                          - datetime.timedelta(seconds=elapsed)).isoformat()}}
            seen = len(probes)
            try:
                # the backoff save consumes the event's separation holder, as every bookkeeping
                # writer does (here one carrying a REAL held lease descriptor); the except arm
                # keeps a pre-holder module shape a red check
                refused = aiqt_hooks._orch_lease_hb_tracked_refused(
                    str(bo.root), str(bo.lease), bo_st, bo_now, memory,
                    {"dests": bo_dests, "lease_fd": bo_fd, "lease_path": str(bo.lease)})
            except BaseException as exc:
                refused = "crash: {}".format(exc)
            check(cid, (len(probes) - seen, refused), (want_probe, True))
    finally:
        aiqt_hooks._orch_lease_hb_git_tracked = saved_tracked
        os.close(bo_fd)


    # --- (5) takeover under the lock, non-busy flock fault, resolved-path tracking gate ---
    def lock_seam(mutate, _real=aiqt_hooks._orch_lease_hb_lock):
        def wrapper(root_arg, lock_path):
            mutate()
            return _real(root_arg, lock_path)
        return wrapper

    def prime_memory(f):
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "lease-heartbeat.json").write_text(
            json.dumps({"version": 1, "session_id": "s1", "ts": now_iso(1)}), encoding="utf-8")

    def locked_fixture(name):
        f = Fixture(base, name)
        f.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f.lease, 7200)
        (f.root / "lock.file").write_text("", encoding="utf-8")
        declare(f, f.lease, hb(mode="refresh", lock_path="lock.file"))
        prime_memory(f)
        return f

    tk = locked_fixture("takeover")

    def takeover():
        acquired = tk.root / "acquire.txt"
        acquired.write_text("holder: s2\n", encoding="utf-8")
        os.replace(str(acquired), str(tk.lease))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam(takeover)
        result = stop(tk)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-takeover-reads-taken", "lease taken" in text_of(result), True)
    tu = locked_fixture("takeunlink")
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam(lambda: os.unlink(tu.lease))
        result = stop(tu)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-takeover-unlink-reads-lost", "lease lost" in text_of(result), True)
    ha = locked_fixture("hardalias")
    ha_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(ha.root)))
    ha_events = ha_sd / "guard-events.jsonl"
    ha_events.write_text(json.dumps({"ts": "t0", "kind": "seed", "decision": "seed",
                                     "detail": "seed"}) + "\n", encoding="utf-8")
    ha_before = ha_events.read_bytes()

    def alias_lease():
        # a cooperating writer at the lock seam replaces the lease with a HARD LINK to the
        # keeper's own events log: the under-lock re-open then holds that aliased inode, and
        # only the descriptor-bound separation re-proof can refuse before the refusal row would
        # append through the events name into the lease
        tmp_link = ha.root / "alias.tmp"
        os.link(str(ha_events), str(tmp_link))
        os.replace(str(tmp_link), str(ha.lease))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam(alias_lease)
        result = stop(ha)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-seam-alias-no-append",
          (ha_events.read_bytes() == ha_before, "shares an inode" in text_of(result)),
          (True, True))
    sy = locked_fixture("symseam")
    sy_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(sy.root)))
    sy_events = sy_sd / "guard-events.jsonl"
    sy_events.write_text(json.dumps({"ts": "t0", "kind": "seed", "decision": "seed",
                                     "detail": "seed"}) + "\n", encoding="utf-8")
    sy_before = sy_events.read_bytes()

    def symlink_lease():
        # at the lock seam the lease becomes a SYMLINK to the events log: the no-follow
        # re-open refuses it, and the refusal branch must then record NOTHING, because a row
        # appended through the events name would land in the file the lease now resolves to
        tmp_link = sy.root / "sym.tmp"
        os.symlink(str(sy_events), str(tmp_link))
        os.replace(str(tmp_link), str(sy.lease))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam(symlink_lease)
        result = stop(sy)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-seam-symlink-no-append",
          (sy_events.read_bytes() == sy_before, "cannot be evaluated" in text_of(result)),
          (True, True))
    an = Fixture(base, "ancseam")
    an_dir = an.root / "leasedir"
    an_dir.mkdir()
    an_lease = an_dir / "lease.txt"
    an_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(an_lease, 7200)
    (an.root / "lock.file").write_text("", encoding="utf-8")
    declare(an, an_lease, hb(mode="refresh", lock_path="lock.file"))
    prime_memory(an)
    an_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(an.root)))
    an_sd.mkdir(parents=True, exist_ok=True)
    an_events = an_sd / "guard-events.jsonl"
    an_events.write_text("", encoding="utf-8")
    an_before = snap(an_events)

    def alias_ancestor():
        # an ANCESTOR directory of the lease becomes a symlink at the lock seam: the no-follow
        # component walk of the re-open refuses, and the refusal branch records nothing
        os.replace(str(an_dir), str(an.root / "leasedir.gone"))
        os.symlink(str(an.root / "leasedir.gone"), str(an_dir))
    saved_lock = aiqt_hooks._orch_lease_hb_lock
    try:
        aiqt_hooks._orch_lease_hb_lock = lock_seam(alias_ancestor)
        result = stop(an)
    finally:
        aiqt_hooks._orch_lease_hb_lock = saved_lock
    check("leasehb/lock-seam-ancestor-alias-no-append",
          (snap(an_events) == an_before, "cannot be evaluated" in text_of(result)),
          (True, True))
    ur = locked_fixture("unreadseam")
    ur_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(ur.root)))
    ur_events = ur_sd / "guard-events.jsonl"
    ur_events.write_text("", encoding="utf-8")
    ur_before = snap(ur_events)
    ur_lease_before = snap(ur.lease)
    ur_lease_path = str(ur.lease)
    ur_calls = []
    saved_open_fn2 = aiqt_hooks._orch_lease_hb_open

    def unreadable_reopen(root_arg, path_arg, _real=saved_open_fn2):
        # the first open of the LEASE succeeds; the under-lock RE-open of the same path fails
        # (an unreadable plant, injected so the vector is uid-independent); the lock-file open
        # passes through untouched
        if path_arg == ur_lease_path:
            ur_calls.append(path_arg)
            if len(ur_calls) > 1:
                return (None, None, None, ("cannot", "injected unreadable re-open"))
        return _real(root_arg, path_arg)
    try:
        aiqt_hooks._orch_lease_hb_open = unreadable_reopen
        result = stop(ur)
    finally:
        aiqt_hooks._orch_lease_hb_open = saved_open_fn2
    check("leasehb/lock-seam-unreadable-reopen-no-row",
          (snap(ur_events) == ur_before, snap(ur.lease) == ur_lease_before, len(ur_calls),
           "injected unreadable re-open" in text_of(result)), (True, True, 2, True))
    lf = locked_fixture("lockfault")
    before = snap(lf.lease)
    errno_mod = __import__("errno")
    # getattr so a module without the seam (the pre-fix shape) reports a red check, not a crash
    saved_flock = getattr(aiqt_hooks, "_orch_lease_hb_flock", None)

    def no_locks(fd):
        raise OSError(errno_mod.ENOLCK, "no locks available")
    try:
        aiqt_hooks._orch_lease_hb_flock = no_locks
        result = stop(lf)
    finally:
        if saved_flock is None:
            del aiqt_hooks._orch_lease_hb_flock
        else:
            aiqt_hooks._orch_lease_hb_flock = saved_flock
    check("leasehb/lock-fault-named-refusal",
          ("refused" in text_of(result), snap(lf.lease) == before), (True, True))
    lfd = locked_fixture("lockfdclose")
    lfd_fds = []
    saved_open_fn = aiqt_hooks._orch_lease_hb_open

    def recording_open(root_arg, path_arg, _real=saved_open_fn):
        got = _real(root_arg, path_arg)
        lfd_fds.append(got[1])
        return got

    def exploding_flock(fd):
        raise RuntimeError("injected non-OSError flock fault")
    saved_flock2 = getattr(aiqt_hooks, "_orch_lease_hb_flock", None)
    raised = None
    try:
        aiqt_hooks._orch_lease_hb_open = recording_open
        aiqt_hooks._orch_lease_hb_flock = exploding_flock
        try:
            aiqt_hooks._orch_lease_hb_lock(str(lfd.root), "lock.file")
        except RuntimeError as exc:
            raised = str(exc)
    finally:
        aiqt_hooks._orch_lease_hb_open = saved_open_fn
        if saved_flock2 is None:
            del aiqt_hooks._orch_lease_hb_flock
        else:
            aiqt_hooks._orch_lease_hb_flock = saved_flock2
    lfd_closed = True
    try:
        os.fstat(lfd_fds[-1])
        lfd_closed = False  # nothing else opened between: the number still names the lock file
    except OSError:
        pass
    check("leasehb/lock-generic-fault-closes-fd",
          (raised, lfd_closed), ("injected non-OSError flock fault", True))
    al = Fixture(base, "aliastrack")
    al_sub = al.root / "subdir"
    al_sub.mkdir()
    al_lease = al_sub / "tracked-lease.txt"
    al_lease.write_text("holder: s1\n", encoding="utf-8")
    al._git("add", os.path.join("subdir", "tracked-lease.txt"))
    al._git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "track the lease")
    al_alias = base / "alias-into-tree"
    os.symlink(str(al_sub), str(al_alias))
    check("leasehb/tracked-alias-spelling-probed",
          aiqt_hooks._orch_lease_hb_git_tracked(str(al.root),
                                                str(al_alias / "tracked-lease.txt")), True)
    real_relpath = os.path.relpath

    def fault_relpath(*a, **k):
        raise ValueError("injected resolution compare fault")
    try:
        os.path.relpath = fault_relpath
        got = aiqt_hooks._orch_lease_hb_git_tracked(str(al.root),
                                                    str(al_sub / "tracked-lease.txt"))
    finally:
        os.path.relpath = real_relpath
    check("leasehb/tracked-valueerror-probe-fault", got, None)
    oc = Fixture(base, "openclose")
    (oc.root / "okdir").mkdir()
    opened_fds = []
    closed_fds = []
    real_os_open = os.open
    real_os_close = os.close

    def counting_open(*a, **k):
        fd = real_os_open(*a, **k)
        opened_fds.append(fd)
        return fd

    def counting_close(fd):
        closed_fds.append(fd)
        real_os_close(fd)

    def nul_open(path_arg):
        # equal open and close multisets prove EVERY descriptor the walk acquired was closed
        # exactly once; the except arm keeps a pre-fix shape (a propagating ValueError) a red
        # check instead of a suite crash
        opened_fds.clear()
        closed_fds.clear()
        try:
            got_open = aiqt_hooks._orch_lease_hb_open(str(oc.root), path_arg)
        except BaseException as exc:
            got_open = (None, None, None, ("crash", str(exc)))
        return (got_open, list(opened_fds), list(closed_fds))
    try:
        os.open = counting_open
        os.close = counting_close
        leaf_got, leaf_opened, leaf_closed = nul_open(os.path.join("okdir", "bad\0leaf"))
        mid_got, mid_opened, mid_closed = nul_open(os.path.join("bad\0dir", "leaf"))
    finally:
        os.open = real_os_open
        os.close = real_os_close
    check("leasehb/open-nul-leaf-closes-once",
          (leaf_got[:3], leaf_got[3][0], "unusable" in str(leaf_got[3][1]),
           sorted(leaf_closed) == sorted(leaf_opened), len(leaf_opened) > 0),
          ((None, None, None), "cannot", True, True, True))
    check("leasehb/open-nul-intermediate-closes-once",
          (mid_got[:3], mid_got[3][0], "unusable" in str(mid_got[3][1]),
           sorted(mid_closed) == sorted(mid_opened), len(mid_opened) > 0),
          ((None, None, None), "cannot", True, True, True))

    # --- (6) round-5 vectors: a row only through a proof bound to the descriptor in hand ---
    def events_at(f):
        sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
        sd.mkdir(parents=True, exist_ok=True)
        log = sd / "guard-events.jsonl"
        log.write_text(json.dumps({"ts": "t0", "kind": "seed", "decision": "seed",
                                   "detail": "seed"}) + "\n", encoding="utf-8")
        return log

    def meta(path):
        st = os.lstat(path)
        return (path.read_bytes(), st.st_ino, st.st_mtime_ns, st.st_size)

    fs1 = Fixture(base, "fosym")
    fs1.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(fs1.lease, 7200)
    declare(fs1, fs1.lease, hb())
    fs1_events = events_at(fs1)
    fs1_before = meta(fs1_events)
    saved_mem3 = aiqt_hooks._orch_lease_hb_memory

    def fs1_plant(dests_arg):
        # between the path-based entry proof and the FIRST open the lease becomes a SYMLINK to
        # the events log: the no-follow first open then FAILS, and with no descriptor in hand
        # there is no current proof, so the refusal branch must record NOTHING (a row appended
        # through the events name would land in the file the declared lease now resolves to)
        tmp_link = fs1.root / "sym.tmp"
        os.symlink(str(fs1_events), str(tmp_link))
        os.replace(str(tmp_link), str(fs1.lease))
        return {}
    try:
        aiqt_hooks._orch_lease_hb_memory = fs1_plant
        result = stop(fs1)
    finally:
        aiqt_hooks._orch_lease_hb_memory = saved_mem3
    check("leasehb/first-open-symlink-alias-no-append",
          (meta(fs1_events) == fs1_before, "cannot be evaluated" in text_of(result),
           (Path(aiqt_hooks._orch_state_dir_for_root(str(fs1.root)))
            / "lease-heartbeat.json").exists()),
          (True, True, False))

    fa2 = Fixture(base, "foanc")
    fa2_dir = fa2.root / "leasedir"
    fa2_dir.mkdir()
    # the lease leaf NAME matches the events log's, so the planted ancestor below redirects
    # the declared lease path exactly onto the keeper's own bookkeeping file
    fa2_lease = fa2_dir / "guard-events.jsonl"
    fa2_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(fa2_lease, 7200)
    declare(fa2, fa2_lease, hb())
    fa2_events = events_at(fa2)
    fa2_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(fa2.root)))
    fa2_before = meta(fa2_events)
    fa2_lease_before = fa2_lease.read_bytes()

    def fa2_plant(dests_arg):
        # an ANCESTOR of the lease becomes a symlink TO THE STATE DIRECTORY before the FIRST
        # open, so the declared lease path now resolves onto the keeper's own events log: the
        # no-follow component walk refuses, no descriptor exists, and nothing may be recorded
        os.replace(str(fa2_dir), str(fa2.root / "leasedir.gone"))
        os.symlink(str(fa2_sd), str(fa2_dir))
        return {}
    try:
        aiqt_hooks._orch_lease_hb_memory = fa2_plant
        result = stop(fa2)
    finally:
        aiqt_hooks._orch_lease_hb_memory = saved_mem3
    # both the events log and the displaced lease are snapshotted AFTER the plant: bytes,
    # inode and size unchanged on the log, bytes unchanged on the lease
    check("leasehb/first-open-ancestor-alias-no-append",
          (meta(fa2_events) == fa2_before,
           (fa2.root / "leasedir.gone" / "guard-events.jsonl").read_bytes()
           == fa2_lease_before, "cannot be evaluated" in text_of(result)),
          (True, True, True))

    fu = Fixture(base, "founread")
    fu.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(fu.lease, 7200)
    declare(fu, fu.lease, hb())
    fu_events = events_at(fu)
    fu_before = meta(fu_events)
    fu_lease_path = str(fu.lease)
    saved_open3 = aiqt_hooks._orch_lease_hb_open

    def unreadable_first_open(root_arg, path_arg, _real=saved_open3):
        # the FIRST open of the lease fails (an unreadable plant, injected so the vector is
        # uid-independent): with no descriptor in hand the refusal must record nothing
        if path_arg == fu_lease_path:
            return (None, None, None, ("cannot", "injected unreadable first open"))
        return _real(root_arg, path_arg)
    try:
        aiqt_hooks._orch_lease_hb_open = unreadable_first_open
        result = stop(fu)
    finally:
        aiqt_hooks._orch_lease_hb_open = saved_open3
    check("leasehb/first-open-unreadable-no-row",
          (meta(fu_events) == fu_before,
           "injected unreadable first open" in text_of(result)), (True, True))

    fm = Fixture(base, "fomissing")
    declare(fm, fm.root / "absent-lease.txt", hb())
    prime_memory(fm)
    fm_events = events_at(fm)
    fm_before = meta(fm_events)
    result = stop(fm)
    check("leasehb/first-open-missing-no-row",
          (meta(fm_events) == fm_before, "lease lost" in text_of(result)), (True, True))

    def flock_alias_fixture(name):
        f = Fixture(base, name)
        f.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f.lease, 7200)
        (f.root / "lock.file").write_text("", encoding="utf-8")
        declare(f, f.lease, hb(mode="refresh", lock_path="lock.file"))
        prime_memory(f)
        return f, events_at(f)

    saved_flock3 = getattr(aiqt_hooks, "_orch_lease_hb_flock", None)

    def flock_seam(f, log, link_fn, err):
        def seam(fd):
            # a cooperating LOCK HOLDER re-aliases the declared lease onto the events log at
            # name level and reports busy (or a fault): no re-open and no re-proof happen on
            # these branches, the pre-lock descriptor is orphaned by the swap, and a proof made
            # on it cannot see the alias, so every bookkeeping writer must withhold
            tmp = f.root / "alias.tmp"
            link_fn(str(log), str(tmp))
            os.replace(str(tmp), str(f.lease))
            raise OSError(err, "injected lock outcome")
        return seam

    errno_mod2 = __import__("errno")
    for cid, fx_name, link_fn, err in (
            ("leasehb/lock-busy-hardlink-alias-no-append", "busyhard", os.link,
             errno_mod2.EWOULDBLOCK),
            ("leasehb/lock-busy-symlink-alias-no-append", "busysym", os.symlink,
             errno_mod2.EWOULDBLOCK),
            ("leasehb/lock-fault-hardlink-alias-no-append", "faulthard", os.link,
             errno_mod2.ENOLCK),
            ("leasehb/lock-fault-symlink-alias-no-append", "faultsym", os.symlink,
             errno_mod2.ENOLCK)):
        f, log = flock_alias_fixture(fx_name)
        before = meta(log)
        mem_path = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root))) / "lease-heartbeat.json"
        mem_before = mem_path.read_bytes()
        try:
            aiqt_hooks._orch_lease_hb_flock = flock_seam(f, log, link_fn, err)
            result = stop(f)
        finally:
            if saved_flock3 is None:
                del aiqt_hooks._orch_lease_hb_flock
            else:
                aiqt_hooks._orch_lease_hb_flock = saved_flock3
        check(cid, (meta(log) == before, mem_path.read_bytes() == mem_before,
                    "cannot be evaluated" in text_of(result), result[0]),
              (True, True, True, 0))

    for cid, exc_type in (
            ("leasehb/first-proof-runtimeerror-closes-no-row", RuntimeError),
            ("leasehb/first-proof-valueerror-closes-no-row", ValueError)):
        fp3 = Fixture(base, "fproof" + exc_type.__name__.lower())
        fp3.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(fp3.lease, 7200)
        declare(fp3, fp3.lease, hb())
        fp3_events = events_at(fp3)
        fp3_before = meta(fp3_events)
        saved_sepfd = aiqt_hooks._orch_lease_hb_sep_fd
        saved_root_fn = aiqt_hooks._orch_root

        def sep_boom(fd, dests_arg, _exc=exc_type):
            raise _exc("injected first-proof fault")
        opened3 = []
        closed3 = []
        real_open3 = os.open
        real_close3 = os.close

        def c_open(*a, **k):
            fd = real_open3(*a, **k)
            opened3.append(fd)
            return fd

        def c_close(fd):
            closed3.append(fd)
            real_close3(fd)
        # equal open/close multisets prove the descriptors acquired for the lease were closed
        # exactly once although the FIRST proof (not only the open) crashed; _orch_root is
        # pinned so no subprocess pipe descriptors cross the counting window, and the events
        # log stays byte-identical because an unproven holder withholds the crash row too
        try:
            aiqt_hooks._orch_lease_hb_sep_fd = sep_boom
            aiqt_hooks._orch_root = lambda data_arg: str(fp3.root)
            os.open = c_open
            os.close = c_close
            result = stop(fp3)
        finally:
            os.open = real_open3
            os.close = real_close3
            aiqt_hooks._orch_lease_hb_sep_fd = saved_sepfd
            aiqt_hooks._orch_root = saved_root_fn
        check(cid,
              (sorted(closed3) == sorted(opened3), len(opened3) > 0, result[0],
               meta(fp3_events) == fp3_before),
              (True, True, 0, True))

    md = Fixture(base, "mkdirfault")
    declare(md, md.state / "pending-lease.txt", hb())
    real_makedirs = os.makedirs

    def fault_makedirs(path_arg, *a, **k):
        if os.fspath(path_arg) == str(md.state):
            raise PermissionError(13, "injected mkdir fault")
        return real_makedirs(path_arg, *a, **k)
    # a CREATION fault on the absent state directory is the named cannot-evaluate refusal whose
    # remediation names the state-record fault (a lease-path redeclaration would not repair it),
    # with nothing created and nothing recorded
    try:
        os.makedirs = fault_makedirs
        result = stop(md)
    finally:
        os.makedirs = real_makedirs
    check("leasehb/collision-statedir-mkdir-fault-named",
          ("cannot be evaluated" in text_of(result), "injected mkdir fault" in text_of(result),
           "state directory" in text_of(result), md.state.is_dir(),
           (md.state / "guard-events.jsonl").exists()),
          (True, True, True, False, False))

    rd = Fixture(base, "statedirrepeat")
    rd.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(rd.lease, 7200)
    rd_reg = declare(rd, rd.lease, hb())
    rd_dests = aiqt_hooks._orch_lease_hb_dests(str(rd.root), rd_reg)
    rd_first = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(rd.lease), rd_dests)
    rd_second = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(rd.lease), rd_dests)
    check("leasehb/collision-absent-statedir-create-idempotent",
          (rd_first, rd_second, rd.state.is_dir()), (None, None, True))

    rc = Fixture(base, "statedirconcurrent")
    rc.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(rc.lease, 7200)
    rc_reg = declare(rc, rc.lease, hb())
    rc_dests = aiqt_hooks._orch_lease_hb_dests(str(rc.root), rc_reg)
    # the CONCURRENT-creation repeat: the existence stat reports the state directory absent
    # while another keeper creates it before the makedirs that follows, so a creation that
    # drops its idempotence (exist_ok) raises FileExistsError and goes red as a
    # cannot-evaluate (the sequential repeat above finds the directory by the stat and never
    # reaches the creation, so only this injection discriminates that mutant); getattr keeps
    # a module without the seam a red check, not a crash
    saved_sdstat = getattr(aiqt_hooks, "_orch_lease_hb_statedir_stat", None)

    def racing_stat(sd):
        os.makedirs(sd)  # the concurrent keeper wins the race first
        raise FileNotFoundError(2, "injected absent report", sd)
    try:
        if saved_sdstat is None:
            rc_got = "no statedir stat seam"
        else:
            aiqt_hooks._orch_lease_hb_statedir_stat = racing_stat
            rc_got = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(rc.lease), rc_dests)
    finally:
        if saved_sdstat is not None:
            aiqt_hooks._orch_lease_hb_statedir_stat = saved_sdstat
    check("leasehb/collision-statedir-concurrent-create-idempotent",
          (rc_got, rc.state.is_dir()), (None, True))

    wwf = Fixture(base, "withholdall")
    wwf_sd = wwf.root / "wwstate"
    wwf_dests = (str(wwf_sd / "lease-heartbeat.json"), str(wwf_sd / "guard-events.jsonl"))
    wwf_holder = {"dests": wwf_dests, "lease_fd": None, "lease_path": str(wwf.lease)}
    wwf_now = datetime.datetime.now(datetime.timezone.utc)
    wwf_st = os.stat(wwf.lease)
    wwf_mem = {"last_row": {"kind": "no-write",
                            "code": aiqt_hooks._ORCH_LEASE_HB_TRACKED_DETAIL,
                            "lease": [wwf_st.st_dev, wwf_st.st_ino, wwf_st.st_mtime_ns,
                                      wwf_st.st_size],
                            "ts": (wwf_now - datetime.timedelta(seconds=700)).isoformat()}}
    saved_tracked_ww = aiqt_hooks._orch_lease_hb_git_tracked
    try:
        # ALL FIVE writers, each handed a holder with NO held descriptor and ABSOLUTE
        # destinations under this fixture (a regressed writer that wrote anyway would create
        # them HERE, asserted absent below, never outside the fixture); the backoff leg forces
        # the at-bound re-probe (tracked True), whose timestamp save must withhold too; the
        # except arm keeps a writer that crashes on the holder shape a red check
        aiqt_hooks._orch_lease_hb_git_tracked = lambda root_arg, path_arg: True
        ww = (aiqt_hooks._orch_lease_hb_event(wwf_holder, "warn", "withheld"),
              aiqt_hooks._orch_lease_hb_append_checked(wwf_holder, wwf_dests[1],
                                                       {"kind": "withheld"}),
              aiqt_hooks._orch_lease_hb_row_once(wwf_holder, {}, wwf_now, "warn", "c",
                                                 "withheld"),
              aiqt_hooks._orch_lease_hb_memory_save(wwf_holder, {"session_id": "s1"}),
              aiqt_hooks._orch_lease_hb_crash_row(wwf_holder, RuntimeError("withheld")),
              aiqt_hooks._orch_lease_hb_tracked_refused(str(wwf.root), str(wwf.lease), wwf_st,
                                                        wwf_now, wwf_mem, wwf_holder))
    except BaseException as exc:
        ww = "crash: {}".format(exc)
    finally:
        aiqt_hooks._orch_lease_hb_git_tracked = saved_tracked_ww
    check("leasehb/writer-withholds-without-proof",
          (ww, wwf_sd.exists()), ((False, False, False, None, None, True), False))

    gm = Fixture(base, "unhashmodedict")
    gm.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(gm.lease, 7200)
    gm_reg = declare(gm, gm.lease, hb(mode={}))
    result = stop(gm)
    check("leasehb/config-unhashable-mode-dict-stop-named",
          (_verdict(result), "mode must be" in text_of(result)), ("warn", True))
    check("leasehb/config-unhashable-mode-dict-post-silent", _verdict(post(gm)), "allow")
    check("leasehb/config-unhashable-mode-dict-withholds-row",
          len([r for r in declared_rows(gm) if "mode must be" in r.get("detail", "")]), 0)
    check("leasehb/resume-unhashable-mode-dict-finding",
          any("invalid" in fi for fi in aiqt_hooks._orch_lease_hb_resume_findings(
              gm_reg, str(gm.root), gm_reg["lease"])), True)
    gl = Fixture(base, "unhashfmtlist")
    gl.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(gl.lease, 7200)
    gl_reg = declare(gl, gl.lease, hb(format=[]))
    result = stop(gl)
    check("leasehb/config-unhashable-format-list-stop-named",
          (_verdict(result), "format must be" in text_of(result)), ("warn", True))
    check("leasehb/config-unhashable-format-list-post-silent", _verdict(post(gl)), "allow")
    check("leasehb/config-unhashable-format-list-withholds-row",
          len([r for r in declared_rows(gl) if "format must be" in r.get("detail", "")]), 0)
    check("leasehb/resume-unhashable-format-list-finding",
          any("invalid" in fi for fi in aiqt_hooks._orch_lease_hb_resume_findings(
              gl_reg, str(gl.root), gl_reg["lease"])), True)

    empty_detail = "contains an empty path component (a doubled, leading or trailing separator)"
    check("leasehb/path-bad-doubled-root-refused",
          (aiqt_hooks._orch_lease_hb_path_bad(os.sep * 2 + "tmp" + os.sep + "lease"),
           aiqt_hooks._orch_lease_hb_path_bad(os.sep * 3 + "tmp" + os.sep + "lease"),
           aiqt_hooks._orch_lease_hb_path_bad(os.sep + "tmp" + os.sep + "lease")),
          (empty_detail, empty_detail, None))

    def declare_raw(f, lease_path_value, block):
        reg_path = f.root / ".aiqt" / "orchestration.local.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["lease"] = {"path": lease_path_value, "max_age_hours": 24,
                        "holder_is_session_id": True, "heartbeat": block}
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

    # end-to-end Stop coverage of the declared path VALUES, judged BEFORE any resolution: each
    # is the NAMED invalid-declaration refusal on Stop (never a silent allow and never a core
    # fault), with the lease untouched and nothing recorded (no proof exists about such a path)
    for cid, fx_name, mangle in (
            ("leasehb/stop-lease-path-nul-named", "nullease", lambda p: p + "\0x"),
            ("leasehb/stop-lease-path-leading-sep-named", "leadlease", lambda p: os.sep + p),
            ("leasehb/stop-lease-path-interior-empty-named", "midlease",
             lambda p: p.replace(os.sep + "lease.txt", os.sep + os.sep + "lease.txt")),
            ("leasehb/stop-lease-path-trailing-sep-named", "taillease", lambda p: p + os.sep)):
        f = Fixture(base, fx_name)
        f.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f.lease, 7200)
        declare_raw(f, mangle(str(f.lease)), hb())
        before = snap(f.lease)
        result = stop(f)
        check(cid, (_verdict(result), "invalid" in text_of(result),
                    "the declared lease path contains" in text_of(result),
                    snap(f.lease) == before, (f.state / "guard-events.jsonl").exists()),
              ("warn", True, True, True, False))
    for cid, fx_name, lock_value in (
            ("leasehb/stop-lock-path-nul-named", "nullock2", "bad\0.lock"),
            ("leasehb/stop-lock-path-leading-sep-named", "leadlock",
             os.sep + os.sep + "tmp" + os.sep + "l.lock"),
            ("leasehb/stop-lock-path-interior-empty-named", "midlock",
             "sub" + os.sep + os.sep + "l.lock"),
            ("leasehb/stop-lock-path-trailing-sep-named", "taillock", "l.lock" + os.sep)):
        f = Fixture(base, fx_name)
        f.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f.lease, 7200)
        declare(f, f.lease, hb(mode="refresh", lock_path=lock_value))
        before = snap(f.lease)
        result = stop(f)
        check(cid, (_verdict(result), "invalid" in text_of(result),
                    "lock_path" in text_of(result), snap(f.lease) == before,
                    (f.state / "guard-events.jsonl").exists()),
              ("warn", True, True, True, False))

    # --- (7) round-6 vectors: the WRITE itself is descriptor-bound, not only the proof ---
    saved_load = aiqt_hooks._orch_lease_hb_load

    a1 = Fixture(base, "alias1dest")
    a1.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(a1.lease, 7200)
    declare(a1, a1.lease, hb())
    a1_events = events_at(a1)
    a1_lease_before = snap(a1.lease)

    def a1_load(fd, _real=saved_load):
        # AFTER the descriptor proof passed, a cooperating writer replaces the events LOG with
        # a symlink to the lease: the proof saw a clean destination, so only a write that
        # re-opens its destination no-follow and fstats the descriptor it writes through can
        # refuse this plant (a name-resolved append follows it into the lease and goes red)
        tmp = a1.root / "destsym.tmp"
        os.symlink(str(a1.lease), str(tmp))
        os.replace(str(tmp), str(a1_events))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = a1_load
        result = stop(a1)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load
    check("leasehb/write-seam-dest-symlink-no-append",
          (snap(a1.lease) == a1_lease_before, "heartbeat gap" in text_of(result),
           os.path.islink(str(a1_events))),
          (True, True, True))

    a2 = Fixture(base, "alias2hard")
    a2.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(a2.lease, 7200)
    declare(a2, a2.lease, hb())
    a2_events = events_at(a2)
    a2_seed = a2_events.read_bytes()

    def a2_load(fd, _real=saved_load):
        # AFTER the descriptor proof passed, a cooperating writer replaces the lease NAME with
        # a hard link to the events log (no lock declared): the held descriptor is orphaned
        # (nlink 0, the NAMED refusal) and the refusal row would append through the events
        # name INTO the file the lease name now carries; the write-time fstat (two links) and
        # the fresh lstat of the lease name both refuse it
        tmp = a2.root / "desthard.tmp"
        os.link(str(a2_events), str(tmp))
        os.replace(str(tmp), str(a2.lease))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = a2_load
        result = stop(a2)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load
    check("leasehb/write-seam-lease-hardlink-no-append",
          (a2.lease.read_bytes() == a2_seed, a2_events.read_bytes() == a2_seed,
           "replaced or unlinked" in text_of(result)),
          (True, True, True))

    ck = Fixture(base, "crashlockalias")
    ck.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(ck.lease, 7200)
    (ck.root / "lock.file").write_text("", encoding="utf-8")
    declare(ck, ck.lease, hb(mode="refresh", lock_path="lock.file"))
    prime_memory(ck)
    ck_events = events_at(ck)
    ck_before = snap(ck.lease)

    def ck_load(fd, _real=saved_load):
        # UNDER the held lock, after the descriptor re-proof: the events log becomes a symlink
        # to the lease and the core then faults; the one crash row is recorded INSIDE the
        # critical section through the write-time descriptor check, which refuses the planted
        # alias, and the binding's recorder (after cleanup) holds no descriptor, so NOTHING
        # lands anywhere (a post-cleanup name-resolved crash row follows the plant into the
        # lease and goes red)
        tmp = ck.root / "crashsym.tmp"
        os.symlink(str(ck.lease), str(tmp))
        os.replace(str(tmp), str(ck_events))
        raise RuntimeError("injected under-lock core fault")
    try:
        aiqt_hooks._orch_lease_hb_load = ck_load
        result = stop(ck)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load
    check("leasehb/crash-under-lock-dest-alias-no-append",
          (snap(ck.lease) == ck_before, _verdict(result), os.path.islink(str(ck_events))),
          (True, "allow", True))

    b1 = Fixture(base, "leaseisstatedir")
    declare(b1, b1.state, hb())
    result = stop(b1)
    # the lease declared AT the (absent) state directory: refused BEFORE any creation, so
    # nothing exists at the declared lease path afterwards (a check that creates first plants
    # a directory at the lease name and goes red)
    check("leasehb/collision-lease-is-statedir-creates-nothing",
          (b1.state.exists(), "state directory or an ancestor" in text_of(result),
           _verdict(result)),
          (False, True, "warn"))

    b2 = Fixture(base, "leaseancestor")
    b2_wrap = b2.root / "statewrap"
    b2_reg_path = b2.root / ".aiqt" / "orchestration.local.json"
    b2_reg = json.loads(b2_reg_path.read_text(encoding="utf-8"))
    b2_reg["state_dir"] = str(b2_wrap / "state")
    b2_reg["lease"] = {"path": str(b2_wrap), "max_age_hours": 24,
                       "holder_is_session_id": True, "heartbeat": hb()}
    b2_reg_path.write_text(json.dumps(b2_reg), encoding="utf-8")
    result = stop(b2)
    # the lease declared as an ANCESTOR of the (absent) state directory: refused before any
    # creation, so neither the lease-named directory nor anything under it exists afterwards
    check("leasehb/collision-lease-ancestor-of-statedir-creates-nothing",
          (b2_wrap.exists(), "state directory or an ancestor" in text_of(result),
           _verdict(result)),
          (False, True, "warn"))

    c1 = Fixture(base, "leasesidefault")
    declare(c1, c1.root / "seed.txt" / "lease.txt", hb())
    result = stop(c1)
    # a LEASE-side separation fault (a path through a regular file) is remediated at the
    # declared lease path, chosen from the faulted side, never from a detail substring (a
    # state-directory remedy here would send the operator to repair the wrong surface)
    check("leasehb/sep-lease-side-fault-names-lease-remedy",
          ("cannot be evaluated" in text_of(result),
           "inspect and repair the declared lease path" in text_of(result)),
          (True, True))

    # --- (8) round-7: two-sided close-fault ownership at every transfer, by GENERATION ---
    ocf = Fixture(base, "closefault")
    (ocf.root / "walk1").mkdir()
    (ocf.root / "walk1" / "walk2").mkdir()
    (ocf.root / "walk1" / "walk2" / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    saved_mark = getattr(aiqt_hooks, "_orch_lease_hb_close_mark", None)
    real_open7 = os.open
    real_close7 = os.close

    def gen_tracked(body):
        # acquisitions tracked by GENERATION, never only by descriptor number: each open is a
        # new generation even when the kernel reuses a number, so a double close or a leak
        # cannot hide behind number reuse
        gens = []        # (generation, fd) in acquisition order
        live = {}        # fd -> generation of the acquisition that currently owns the number
        closes = []      # generations closed, in close order
        counter = [0]

        def t_open(*a, **k):
            fd = real_open7(*a, **k)
            counter[0] += 1
            gens.append((counter[0], fd))
            live[fd] = counter[0]
            return fd

        def t_close(fd):
            closes.append(live.pop(fd, "unowned:{}".format(fd)))
            real_close7(fd)
        try:
            os.open = t_open
            os.close = t_close
            got = body()
        finally:
            os.open = real_open7
            os.close = real_close7
            for leaked_fd in list(live):
                try:
                    real_close7(leaked_fd)  # reclaim a pre-fix leak; the check reads live first
                except OSError:
                    pass
        return gens, closes, got, sorted(live.values())

    def faulting_mark(at_call, side):
        calls = []

        def seam(fd, done, _real=saved_mark):
            calls.append(fd)
            if len(calls) == at_call and side == "before":
                raise RuntimeError("injected close fault " + side)
            _real(fd, done)
            if len(calls) == at_call and side == "after":
                raise RuntimeError("injected close fault " + side)
        return seam

    def walk_with_fault(at_call, side):
        def body():
            try:
                return aiqt_hooks._orch_lease_hb_open(
                    str(ocf.root), os.path.join("walk1", "walk2", "lease.txt"))
            except RuntimeError as exc:
                return str(exc)
        try:
            aiqt_hooks._orch_lease_hb_close_mark = faulting_mark(at_call, side)
            return gen_tracked(body)
        finally:
            if saved_mark is None:
                del aiqt_hooks._orch_lease_hb_close_mark
            else:
                aiqt_hooks._orch_lease_hb_close_mark = saved_mark

    # EVERY transfer close of the walk (anchor->walk1, walk1->walk2, walk2->parent), with the
    # injected fault on EITHER side of the close syscall: the walk must propagate the fault with
    # every acquired generation closed exactly once (a shape that owns a descriptor only from
    # the next acquisition leaks it on the before side; one that blindly retries double closes
    # on the after side and the unowned marker appears in closes)
    oc_results = []
    for at_call7 in (1, 2, 3):
        for side7 in ("before", "after"):
            gens7, closes7, got7, leaked7 = walk_with_fault(at_call7, side7)
            # sorted by repr: a double close records the string 'unowned:<fd>' beside the
            # integer generations, and the comparison must then read RED as this named check,
            # never a TypeError that aborts the whole suite
            oc_results.append((got7,
                               sorted(closes7, key=repr)
                               == sorted((g for g, _fd in gens7), key=repr),
                               len(set(closes7)) == len(closes7),
                               leaked7, len(gens7) == at_call7 + 1))
    check("leasehb/open-close-fault-owns-parent",
          oc_results,
          [("injected close fault before", True, True, [], True),
           ("injected close fault after", True, True, [], True)] * 3)

    def closed_now(fd):
        try:
            os.fstat(fd)
            return False
        except OSError:
            return True

    # the lock helper's one transfer (the walked parent), both fault sides: ownership of the
    # already-acquired lock descriptor starts at ITS acquisition, so the fault must still close
    # both descriptors exactly once (the open seam is pinned so the two numbers are known)
    lcf = Fixture(base, "lockclosefault")
    (lcf.root / "lock.file").write_text("", encoding="utf-8")
    saved_open6 = aiqt_hooks._orch_lease_hb_open
    real_close6 = os.close
    lcf_results = []
    for side6 in ("before", "after"):
        lcf_parent = os.open(str(lcf.root), os.O_RDONLY)
        lcf_fd = os.open(str(lcf.root / "lock.file"), os.O_RDONLY)
        raised6 = None
        lcf_closes = []

        def lcf_counting_close(fd, _real=real_close6):
            # EXACTLY-ONCE closure: every close syscall is counted per descriptor number, so a
            # blind retry (a double close the swallowed OSError would otherwise hide) reads as
            # a count of two, and eventual invalidity alone can no longer pass this check
            lcf_closes.append(fd)
            _real(fd)
        try:
            aiqt_hooks._orch_lease_hb_open = (
                lambda root_arg, path_arg, _p=lcf_parent, _f=lcf_fd: (_p, _f, "lock.file", None))
            aiqt_hooks._orch_lease_hb_close_mark = faulting_mark(1, side6)
            os.close = lcf_counting_close
            try:
                aiqt_hooks._orch_lease_hb_lock(str(lcf.root), "lock.file")
            except RuntimeError as exc:
                raised6 = str(exc)
        finally:
            os.close = real_close6
            aiqt_hooks._orch_lease_hb_open = saved_open6
            if saved_mark is None:
                del aiqt_hooks._orch_lease_hb_close_mark
            else:
                aiqt_hooks._orch_lease_hb_close_mark = saved_mark
        lcf_results.append((raised6, lcf_closes.count(lcf_parent), lcf_closes.count(lcf_fd),
                            closed_now(lcf_parent), closed_now(lcf_fd)))
        for leftover6 in (lcf_parent, lcf_fd):
            try:
                os.close(leftover6)  # reclaim a pre-fix leak; both numbers are otherwise untouched
            except OSError:
                pass
    check("leasehb/lock-close-fault-owns-lock-fd",
          lcf_results, [("injected close fault before", 1, 1, True, True),
                        ("injected close fault after", 1, 1, True, True)])

    # --- (9) round-7 vectors: every bookkeeping write is RELATIVE to held descriptors ---
    r7a = Fixture(base, "r7destontolease")
    r7a.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7a.lease, 7200)
    declare(r7a, r7a.lease, hb())
    r7a_events = events_at(r7a)
    r7a_seed = r7a_events.read_bytes()
    saved_afstat = getattr(aiqt_hooks, "_orch_lease_hb_append_fstat", None)
    r7a_planted = []

    def r7a_fstat(fd, _real=saved_afstat):
        # between the destination OPEN and its fstat, a cooperating writer renames the events
        # log onto the lease name: the identity read taken AFTER this fstat sees the moved file
        # AT the lease name and refuses the write (an identity read taken before the open
        # misses it and the row lands at the lease name: red against the round-6 delivery)
        if not r7a_planted:
            r7a_planted.append(True)
            os.replace(str(r7a_events), str(r7a.lease))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_append_fstat = r7a_fstat
        result = stop(r7a)
    finally:
        if saved_afstat is None:
            del aiqt_hooks._orch_lease_hb_append_fstat
        else:
            aiqt_hooks._orch_lease_hb_append_fstat = saved_afstat
    check("leasehb/write-seam-dest-renamed-onto-lease-no-append",
          (r7a.lease.read_bytes() == r7a_seed,
           "aliases the declared lease" in text_of(result), r7a_planted), (True, True, [True]))

    r7b = Fixture(base, "r7interleave")
    r7b_dir = r7b.root / "leasedir"
    r7b_dir.mkdir()
    r7b_lease = r7b_dir / "guard-events.jsonl"  # the lease leaf name MATCHES the events log's
    r7b_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7b_lease, 7200)
    declare(r7b, r7b_lease, hb())
    r7b_events = events_at(r7b)
    r7b_events_seed = r7b_events.read_bytes()
    r7b_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7b.root)))
    r7b_moved = r7b.root / "state.moved"
    saved_ids7 = aiqt_hooks._orch_lease_hb_lease_ids
    r7b_planted = []

    def r7b_ids(holder_arg, _real=saved_ids7):
        got = _real(holder_arg)
        if not r7b_planted:
            # AFTER an identity read: the events file moves ONTO the lease name and the state
            # directory NAME becomes a symlink into the lease's directory. A name-resolved
            # append then lands its row at the lease name (red against the round-6 delivery);
            # a write through the HELD state-directory descriptor is redirected by neither
            # rename and lands in the real (moved) directory.
            r7b_planted.append(True)
            os.replace(str(r7b_events), str(r7b_lease))
            os.replace(str(r7b_sd), str(r7b_moved))
            os.symlink(str(r7b_dir), str(r7b_sd))
        return got
    try:
        aiqt_hooks._orch_lease_hb_lease_ids = r7b_ids
        result = stop(r7b)
    finally:
        aiqt_hooks._orch_lease_hb_lease_ids = saved_ids7
    r7b_moved_log = r7b_moved / "guard-events.jsonl"
    r7b_moved_rows = [json.loads(line) for line in
                      r7b_moved_log.read_text(encoding="utf-8").splitlines()
                      if line.strip()] if r7b_moved_log.exists() else []
    check("leasehb/write-seam-interleave-held-dir-no-redirect",
          (r7b_lease.read_bytes() == r7b_events_seed,
           any(r.get("kind") == "lease-heartbeat" for r in r7b_moved_rows),
           (r7b_moved / "lease-heartbeat.json").exists(),
           "heartbeat gap" in text_of(result)),
          (True, True, True, True))

    r7d = Fixture(base, "r7provesym")
    r7d_dir = r7d.root / "leasedir"
    r7d_dir.mkdir()
    r7d_lease = r7d_dir / "lease.txt"  # distinct leaf: only the O_NOFOLLOW open can refuse
    r7d_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7d_lease, 7200)
    declare(r7d, r7d_lease, hb())
    r7d_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7d.root)))
    r7d_seed = r7d_lease.read_bytes()
    os.symlink(str(r7d_dir), str(r7d_sd))
    saved_sep7 = aiqt_hooks._orch_lease_hb_bookkeeping_sep
    try:
        # the path-based entry check is pinned to pass, so this vector exercises the holder
        # build alone: its O_NOFOLLOW state-directory open must reject the planted symlink,
        # with nothing written anywhere and the remediation naming the state side
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = lambda p_arg, d_arg: None
        result = stop(r7d)
    finally:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = saved_sep7
    check("leasehb/prove-statedir-symlink-refused",
          (r7d_lease.read_bytes() == r7d_seed,
           "cannot be opened for the write-time binding" in text_of(result),
           sorted(os.listdir(r7d_dir))), (True, True, ["lease.txt"]))

    # a FIFO planted at the events destination, WITHOUT a reader: the non-blocking open fails
    # outright (never a blocked hook), the refusal is reported, and no byte lands anywhere
    r7f = Fixture(base, "r7fifonoreader")
    r7f.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7f.lease, 7200)
    declare(r7f, r7f.lease, hb())
    r7f_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7f.root)))
    r7f_sd.mkdir(parents=True, exist_ok=True)
    r7f_fifo = r7f_sd / "guard-events.jsonl"
    os.mkfifo(str(r7f_fifo))
    # the stop runs as a TIMED worker thread: a regression that blocks on the FIFO open turns
    # THIS named check red (done=False) and the suite continues, instead of hanging the whole
    # run to the CI job timeout (the daemon thread is reaped at interpreter exit)
    r7f_box = []
    r7f_thread = threading.Thread(target=lambda: r7f_box.append(stop(r7f)), daemon=True)
    r7f_thread.start()
    r7f_thread.join(30)
    r7f_done = not r7f_thread.is_alive()
    result = r7f_box[0] if r7f_box else (None, None, None)
    check("leasehb/append-fifo-no-reader-prompt-no-write",
          (r7f_done, "bookkeeping withheld" in text_of(result),
           "append failed" in text_of(result),
           stat.S_ISFIFO(os.lstat(r7f_fifo).st_mode)), (True, True, True, True))

    # the same FIFO WITH a reader: the open succeeds at once, the descriptor fstat refuses a
    # non-regular destination before any write, and the reader drains nothing
    r7g = Fixture(base, "r7fiforeader")
    r7g.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7g.lease, 7200)
    declare(r7g, r7g.lease, hb())
    r7g_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7g.root)))
    r7g_sd.mkdir(parents=True, exist_ok=True)
    r7g_fifo = r7g_sd / "guard-events.jsonl"
    os.mkfifo(str(r7g_fifo))
    r7g_reader = os.open(str(r7g_fifo), os.O_RDONLY | os.O_NONBLOCK)
    try:
        # the same timed-thread shape as the no-reader case: a blocking regression is a named
        # red here, never a hung suite
        r7g_box = []
        r7g_thread = threading.Thread(target=lambda: r7g_box.append(stop(r7g)), daemon=True)
        r7g_thread.start()
        r7g_thread.join(30)
        r7g_done = not r7g_thread.is_alive()
        result = r7g_box[0] if r7g_box else (None, None, None)
        try:
            drained = os.read(r7g_reader, 1 << 16)
        except BlockingIOError:
            drained = b""
    finally:
        os.close(r7g_reader)
    check("leasehb/append-fifo-with-reader-no-write",
          (r7g_done, "not a regular file" in text_of(result), drained,
           stat.S_ISFIFO(os.lstat(r7g_fifo).st_mode)), (True, True, b"", True))

    # a FIFO at the memory record's name: the save's target lstat refuses it with nothing
    # opened at the target and nothing written through it
    r7h = Fixture(base, "r7fifosave")
    r7h.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7h.lease, 7200)
    declare(r7h, r7h.lease, hb())
    r7h_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7h.root)))
    r7h_sd.mkdir(parents=True, exist_ok=True)
    r7h_fifo = r7h_sd / "lease-heartbeat.json"
    os.mkfifo(str(r7h_fifo))
    # TIMED, as the sibling append-fifo vectors are: an O_NONBLOCK regression on the memory
    # open is a named red (done=False), never a hung suite
    r7h_done, result = _leasehb_timed(lambda: stop(r7h))
    result = result or (None, None, None)
    check("leasehb/save-fifo-target-no-write",
          (r7h_done, "not a single-link regular file" in text_of(result),
           stat.S_ISFIFO(os.lstat(r7h_fifo).st_mode)), (True, True, True))

    # the lease RENAMED onto the events destination after the proof: only the held-descriptor
    # identity leg can refuse (the lease name is then absent and contributes no identity)
    r7i = Fixture(base, "r7leaseondest")
    r7i.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7i.lease, 7200)
    declare(r7i, r7i.lease, hb())
    r7i_events = events_at(r7i)
    saved_load7 = aiqt_hooks._orch_lease_hb_load
    r7i_planted = []

    def r7i_load(fd, _real=saved_load7):
        if not r7i_planted:
            r7i_planted.append(True)
            os.replace(str(r7i.lease), str(r7i_events))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = r7i_load
        result = stop(r7i)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load7
    check("leasehb/write-seam-lease-renamed-onto-dest-no-append",
          (r7i_events.read_bytes(), "aliases the declared lease" in text_of(result)),
          (b"holder: s1\n", True))

    # a destination with TWO hard links, neither the lease: only the nlink leg can refuse
    r7j = Fixture(base, "r7destmultilink")
    r7j.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7j.lease, 7200)
    declare(r7j, r7j.lease, hb())
    r7j_events = events_at(r7j)
    r7j_seed = r7j_events.read_bytes()
    r7j_planted = []

    def r7j_load(fd, _real=saved_load7):
        if not r7j_planted:
            r7j_planted.append(True)
            os.link(str(r7j_events), str(r7j.root / "events-alias.jsonl"))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = r7j_load
        result = stop(r7j)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load7
    check("leasehb/write-seam-dest-multilink-no-append",
          (r7j_events.read_bytes() == r7j_seed,
           "exactly one hard link" in text_of(result)), (True, True))

    # a symlink at the destination name aiming at a DECOY outside lease and state records:
    # only O_NOFOLLOW can refuse (every identity leg passes a followed decoy)
    r7k = Fixture(base, "r7destdecoy")
    r7k.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7k.lease, 7200)
    declare(r7k, r7k.lease, hb())
    r7k_events = events_at(r7k)
    r7k_decoy = r7k.root / "decoy.txt"
    r7k_decoy.write_text("decoy\n", encoding="utf-8")
    r7k_planted = []

    def r7k_load(fd, _real=saved_load7):
        if not r7k_planted:
            r7k_planted.append(True)
            tmp7k = r7k.root / "decoysym.tmp"
            os.symlink(str(r7k_decoy), str(tmp7k))
            os.replace(str(tmp7k), str(r7k_events))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = r7k_load
        result = stop(r7k)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load7
    check("leasehb/write-seam-dest-symlink-decoy-no-append",
          (r7k_decoy.read_bytes(), "append failed" in text_of(result),
           os.path.islink(str(r7k_events))), (b"decoy\n", True, True))

    # the lease renamed onto the MEMORY record's name after the proof: the save's target check
    # refuses by held-descriptor identity, and the file at that name keeps the lease's bytes
    r7l = Fixture(base, "r7leaseonsave")
    r7l.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7l.lease, 7200)
    declare(r7l, r7l.lease, hb())
    r7l_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7l.root)))
    r7l_sd.mkdir(parents=True, exist_ok=True)
    r7l_target = r7l_sd / "lease-heartbeat.json"
    saved_load8 = aiqt_hooks._orch_lease_hb_load
    r7l_planted = []

    def r7l_load(fd, _real=saved_load8):
        if not r7l_planted:
            r7l_planted.append(True)
            os.replace(str(r7l.lease), str(r7l_target))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_load = r7l_load
        result = stop(r7l)
    finally:
        aiqt_hooks._orch_lease_hb_load = saved_load8
    check("leasehb/save-target-is-lease-no-replace",
          (r7l_target.read_bytes(),
           "not a single-link regular file distinct" in text_of(result)),
          (b"holder: s1\n", True))

    # a MULTI-LINK regular file at the memory record's name, neither link the lease: only the
    # save target's nlink term can refuse, and the file keeps its bytes
    r7m = Fixture(base, "r7savemultilink")
    r7m.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7m.lease, 7200)
    declare(r7m, r7m.lease, hb())
    r7m_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r7m.root)))
    r7m_sd.mkdir(parents=True, exist_ok=True)
    r7m_target = r7m_sd / "lease-heartbeat.json"
    r7m_target.write_text("{}", encoding="utf-8")
    os.link(str(r7m_target), str(r7m.root / "memory-alias.json"))
    result = stop(r7m)
    check("leasehb/save-target-multilink-no-replace",
          (r7m_target.read_bytes(),
           "not a single-link regular file distinct" in text_of(result)),
          (b"{}", True))

    # a FAILED memory save is reported through the withheld list and the Stop banner
    r7n = Fixture(base, "r7savefaultstop")
    r7n.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7n.lease, 7200)
    declare(r7n, r7n.lease, hb())
    saved_srepl2 = getattr(aiqt_hooks, "_orch_lease_hb_save_replace", None)

    def r7n_fault(tmp_arg, name_arg, dirfd):
        raise PermissionError(13, "injected replace fault")
    try:
        aiqt_hooks._orch_lease_hb_save_replace = r7n_fault
        result = stop(r7n)
    finally:
        if saved_srepl2 is None:
            del aiqt_hooks._orch_lease_hb_save_replace
        else:
            aiqt_hooks._orch_lease_hb_save_replace = saved_srepl2
    check("leasehb/save-failure-reported-stop",
          (_verdict(result), "bookkeeping withheld" in text_of(result),
           "the memory save replace failed" in text_of(result),
           "injected replace fault" in text_of(result)), ("warn", True, True, True))

    # the same failed save on PostToolUse rides the result's error text (exit 0, no stdout)
    r7o = Fixture(base, "r7savefaultpost")
    r7o.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7o.lease, 7200)
    declare(r7o, r7o.lease, hb())
    try:
        aiqt_hooks._orch_lease_hb_save_replace = r7n_fault
        result = post(r7o)
    finally:
        if saved_srepl2 is None:
            del aiqt_hooks._orch_lease_hb_save_replace
        else:
            aiqt_hooks._orch_lease_hb_save_replace = saved_srepl2
    check("leasehb/save-failure-reported-post",
          (result[0], result[1],
           "bookkeeping withheld" in (result[2] or ""),
           "the memory save replace failed" in (result[2] or "")), (0, None, True, True))

    # the crash row, observed INSIDE the append seam: the held lease descriptor is still open
    # and a COMPETING lock acquisition fails busy while the row is written; after cleanup the
    # captured holder is empty and a writer attempt performs no open and no write
    r7q = locked_fixture("r7crashobs")
    r7q_events = events_at(r7q)
    r7q_obs = []
    saved_ids8 = aiqt_hooks._orch_lease_hb_lease_ids
    fcntl7 = __import__("fcntl")

    def r7q_ids(holder_arg, _real=saved_ids8):
        fd_ok = False
        try:
            os.fstat(holder_arg["lease_fd"])
            fd_ok = True
        except (OSError, TypeError, KeyError):
            pass
        busy = None
        competing = os.open(str(r7q.root / "lock.file"), os.O_RDONLY)
        try:
            try:
                fcntl7.flock(competing, fcntl7.LOCK_EX | fcntl7.LOCK_NB)
                busy = False
            except OSError as lock_exc:
                busy = lock_exc.errno in (errno_mod2.EWOULDBLOCK, errno_mod2.EAGAIN)
        finally:
            os.close(competing)
        r7q_obs.append((fd_ok, busy))
        return _real(holder_arg)

    r7q_holders = []
    saved_holder_fn7 = aiqt_hooks._orch_lease_hb_holder

    def r7q_holder(_real=saved_holder_fn7):
        made = _real()
        r7q_holders.append(made)
        return made
    saved_parse7 = aiqt_hooks._orch_lease_hb_parse

    def r7q_parse(text, cfg, keys=None, _real=saved_parse7):
        if keys is None:
            raise RuntimeError("r7 injected core fault")  # the FULL parse, under the held lock
        return _real(text, cfg, keys=keys)
    try:
        aiqt_hooks._orch_lease_hb_lease_ids = r7q_ids
        aiqt_hooks._orch_lease_hb_holder = r7q_holder
        aiqt_hooks._orch_lease_hb_parse = r7q_parse
        result = stop(r7q)
    finally:
        aiqt_hooks._orch_lease_hb_lease_ids = saved_ids8
        aiqt_hooks._orch_lease_hb_holder = saved_holder_fn7
        aiqt_hooks._orch_lease_hb_parse = saved_parse7
    r7q_rows = [json.loads(line) for line in
                r7q_events.read_text(encoding="utf-8").splitlines() if line.strip()]
    r7q_crash_rows = [r for r in r7q_rows if "r7 injected core fault" in r.get("detail", "")]
    check("leasehb/crash-row-fd-valid-in-append-seam",
          (len(r7q_obs) >= 1, all(ob[0] for ob in r7q_obs)), (True, True))
    check("leasehb/crash-row-lock-held-while-written",
          all(ob[1] for ob in r7q_obs), True)
    check("leasehb/crash-row-once-under-lock",
          (len(r7q_crash_rows), result[0]), (1, 0))
    r7q_opens = []
    real_open9 = os.open

    def r7q_counting_open(*a, **k):
        r7q_opens.append(a)
        return real_open9(*a, **k)
    rows_before_pc = len(r7q_rows)
    try:
        os.open = r7q_counting_open
        got_pc = aiqt_hooks._orch_lease_hb_event(r7q_holders[-1], "warn", "post-cleanup attempt")
    finally:
        os.open = real_open9
    r7q_rows_after = [json.loads(line) for line in
                      r7q_events.read_text(encoding="utf-8").splitlines() if line.strip()]
    check("leasehb/post-cleanup-writer-no-open-no-write",
          (got_pc, r7q_opens, len(r7q_rows_after) == rows_before_pc,
           len(r7q_holders) >= 1), (False, [], True, True))

    # the ancestor-bound three-point battery: a sibling-prefix lease JUST OUTSIDE the state
    # directory's path is allowed, the state directory itself and an ancestor each refuse
    r7r = Fixture(base, "r7bound")
    r7r_pfx = r7r.root / "pfx"
    r7r_pfx.mkdir()
    (r7r_pfx / "sta").write_text("holder: s1\n", encoding="utf-8")
    r7r_reg_path = r7r.root / ".aiqt" / "orchestration.local.json"
    r7r_reg = json.loads(r7r_reg_path.read_text(encoding="utf-8"))
    r7r_reg["state_dir"] = str(r7r_pfx / "state")
    r7r_dests = aiqt_hooks._orch_lease_hb_dests(str(r7r.root), r7r_reg)
    r7r_outside = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(r7r_pfx / "sta"), r7r_dests)
    r7r_at = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(r7r_pfx / "state"), r7r_dests)
    r7r_above = aiqt_hooks._orch_lease_hb_bookkeeping_sep(str(r7r_pfx), r7r_dests)
    check("leasehb/collision-ancestor-bound-three-point",
          (r7r_outside,
           r7r_at[0] if isinstance(r7r_at, tuple) else r7r_at,
           r7r_above[0] if isinstance(r7r_above, tuple) else r7r_above),
          (None, "collision", "collision"))

    # an INVALID declaration is now validated before the separation check runs, so it creates
    # no state directory (and still names the refusal on Stop with nothing recorded)
    r7s = Fixture(base, "r7invalidnostate")
    r7s.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r7s.lease, 7200)
    declare(r7s, r7s.lease, hb(mode="bogus"))
    result = stop(r7s)
    check("leasehb/invalid-declaration-creates-no-statedir",
          (_verdict(result), "invalid" in text_of(result), r7s.state.exists()),
          ("warn", True, False))

    # --- (10) round-8 vectors: the save's temporary descriptor is checked before its first
    # write, descriptor ownership is exception-safe at the holder build, and the same-user
    # race class is disclosed whole (with a witness), never one window at a time ---

    # (10a) the save's temporary file RENAMED ONTO THE LEASE NAME before its first write: the
    # pre-write identity check refuses with nothing written through the descriptor, so the
    # file now at the lease name stays EMPTY (red against the round-7 delivery, where the
    # memory JSON lands in the file the lease name then carries)
    r8a = Fixture(base, "r8tmponlease")
    r8a.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8a.lease, 7200)
    declare(r8a, r8a.lease, hb())
    r8a_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8a.root)))
    r8a_sd.mkdir(parents=True, exist_ok=True)
    r8a_tmp = r8a_sd / ".forced.r8a.hb.tmp"
    saved_tmpname8 = aiqt_hooks._orch_lease_hb_tmp_name
    saved_ids9 = aiqt_hooks._orch_lease_hb_lease_ids
    r8a_planted = []

    def r8a_ids(holder_arg, _real=saved_ids9):
        # keyed on the forced temporary name EXISTING, so the plant runs inside a save, after
        # the exclusive create: the temporary is renamed onto the lease name, and the pre-write
        # identity legs must see the temporary AT the lease name and refuse before any byte
        if not r8a_planted and r8a_tmp.exists():
            r8a_planted.append(True)
            os.replace(str(r8a_tmp), str(r8a.lease))
        return _real(holder_arg)
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r8a.hb.tmp"
        aiqt_hooks._orch_lease_hb_lease_ids = r8a_ids
        result = stop(r8a)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname8
        aiqt_hooks._orch_lease_hb_lease_ids = saved_ids9
    check("leasehb/save-tmpfd-renamed-onto-lease-no-write",
          (r8a.lease.read_bytes(), "aliases the declared lease" in text_of(result),
           r8a_planted), (b"", True, [True]))

    # (10b) the temporary HARD-LINKED between the exclusive create and the pre-write fstat:
    # the exactly-one-link term refuses before the first byte, and the planted alias stays
    # empty (red against the round-7 delivery, which checked nothing on the temporary)
    r8b = Fixture(base, "r8tmplinked")
    r8b.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8b.lease, 7200)
    declare(r8b, r8b.lease, hb())
    r8b_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8b.root)))
    r8b_sd.mkdir(parents=True, exist_ok=True)
    r8b_tmp = r8b_sd / ".forced.r8b.hb.tmp"
    r8b_alias = r8b.root / "tmp-alias.json"
    saved_tfstat = getattr(aiqt_hooks, "_orch_lease_hb_save_tmp_fstat", None)
    r8b_planted = []

    def r8b_tfstat(fd, _real=saved_tfstat):
        if not r8b_planted:
            r8b_planted.append(True)
            os.link(str(r8b_tmp), str(r8b_alias))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r8b.hb.tmp"
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = r8b_tfstat
        result = stop(r8b)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname8
        if saved_tfstat is None:
            del aiqt_hooks._orch_lease_hb_save_tmp_fstat
        else:
            aiqt_hooks._orch_lease_hb_save_tmp_fstat = saved_tfstat
    check("leasehb/save-tmpfd-hardlinked-no-write",
          (r8b_alias.exists(),
           r8b_alias.read_bytes() if r8b_alias.exists() else None,
           "is not a single-link regular file (linked or unlinked before its first write)"
           in text_of(result)), (True, b"", True))

    # (10c) the temporary UNLINKED between the exclusive create and the pre-write fstat: the
    # zero-link descriptor is refused before the first byte, and nothing of the first save
    # remains anywhere (red against the round-7 delivery, which wrote into the orphaned inode
    # and then failed only at the replace)
    r8c = Fixture(base, "r8tmpunlinked")
    r8c.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8c.lease, 7200)
    declare(r8c, r8c.lease, hb())
    r8c_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8c.root)))
    r8c_sd.mkdir(parents=True, exist_ok=True)
    r8c_tmp = r8c_sd / ".forced.r8c.hb.tmp"
    r8c_planted = []
    r8c_orphan = []

    def r8c_tfstat(fd, _real=saved_tfstat):
        if not r8c_planted:
            r8c_planted.append(True)
            # a READ descriptor is retained on the inode BEFORE the name is unlinked, so the
            # no-write property is observed DIRECTLY on the orphaned inode afterwards (red
            # under a write-before-rejection mutant, whose bytes would be read back below)
            r8c_orphan.append(os.open(str(r8c_tmp), os.O_RDONLY))
            os.unlink(str(r8c_tmp))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r8c.hb.tmp"
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = r8c_tfstat
        result = stop(r8c)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname8
        if saved_tfstat is None:
            del aiqt_hooks._orch_lease_hb_save_tmp_fstat
        else:
            aiqt_hooks._orch_lease_hb_save_tmp_fstat = saved_tfstat
    r8c_read = None
    if r8c_orphan:
        r8c_read = os.read(r8c_orphan[0], 1 << 16)
        os.close(r8c_orphan[0])
    check("leasehb/save-tmpfd-unlinked-no-write",
          ("is not a single-link regular file (linked or unlinked before its first write)"
           in text_of(result), r8c_tmp.exists(), r8c_planted, r8c_read),
          (True, False, [True], b""))

    # (10d) the temporary NAME re-pointed at a decoy inode AFTER the write, before the
    # replace: only the source re-check immediately before the replace can refuse publishing
    # the decoy (red against the round-7 delivery, where the decoy lands at the memory name)
    r8d = Fixture(base, "r8tmprepoint")
    r8d.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8d.lease, 7200)
    declare(r8d, r8d.lease, hb())
    r8d_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8d.root)))
    r8d_sd.mkdir(parents=True, exist_ok=True)
    r8d_tmp = r8d_sd / ".forced.r8d.hb.tmp"
    r8d_decoy = r8d.root / "decoy.json"
    r8d_decoy.write_text('{"decoy": true}', encoding="utf-8")
    r8d_mem = r8d_sd / "lease-heartbeat.json"
    r8d_planted = []

    def r8d_ids(holder_arg, _real=saved_ids9):
        got = _real(holder_arg)
        # keyed on the temporary already WRITTEN (size > 0): the plant runs after the save's
        # post-write identity read and re-points the temporary name at the decoy
        if not r8d_planted and r8d_tmp.exists() and r8d_tmp.stat().st_size > 0:
            r8d_planted.append(True)
            os.replace(str(r8d_decoy), str(r8d_tmp))
        return got
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r8d.hb.tmp"
        aiqt_hooks._orch_lease_hb_lease_ids = r8d_ids
        result = stop(r8d)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname8
        aiqt_hooks._orch_lease_hb_lease_ids = saved_ids9
    check("leasehb/save-tmp-repointed-before-replace-no-replace",
          (r8d_mem.exists(),
           "no longer carries the file this save created" in text_of(result),
           r8d_tmp.read_bytes() if r8d_tmp.exists() else None, r8d_planted),
          (False, True, b'{"decoy": true}', [True]))


    # (10i) a FAILED backoff save is reported: while a tracked-lease refusal persists and the
    # backoff elapses, the re-probe path rewrites only the remembered timestamp through the
    # memory save, and a replace fault inside THAT save must reach the Stop output
    r8i = Fixture(base, "r8backoffsave")
    r8i.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8i.lease, 7200)
    declare(r8i, r8i.lease, hb(mode="refresh"))
    r8i_st = os.stat(r8i.lease)
    r8i_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8i.root)))
    r8i_sd.mkdir(parents=True, exist_ok=True)
    (r8i_sd / "lease-heartbeat.json").write_text(json.dumps(
        {"version": 1, "session_id": "s1", "ts": now_iso(1),
         "last_row": {"kind": "no-write",
                      "code": aiqt_hooks._ORCH_LEASE_HB_TRACKED_DETAIL,
                      "lease": [r8i_st.st_dev, r8i_st.st_ino, r8i_st.st_mtime_ns,
                                r8i_st.st_size],
                      "ts": now_iso(1)}}), encoding="utf-8")
    saved_tracked8 = aiqt_hooks._orch_lease_hb_git_tracked
    saved_srepl4 = aiqt_hooks._orch_lease_hb_save_replace

    def r8i_fault(tmp_arg, name_arg, dirfd):
        raise PermissionError(13, "injected backoff save fault")
    try:
        aiqt_hooks._orch_lease_hb_git_tracked = lambda root_arg, path_arg: True
        aiqt_hooks._orch_lease_hb_save_replace = r8i_fault
        result = stop(r8i)
    finally:
        aiqt_hooks._orch_lease_hb_git_tracked = saved_tracked8
        aiqt_hooks._orch_lease_hb_save_replace = saved_srepl4
    check("leasehb/backoff-save-failure-reported",
          (_verdict(result), "bookkeeping withheld" in text_of(result),
           "injected backoff save fault" in text_of(result),
           "refresh was refused" in text_of(result)), ("warn", True, True, True))


    # (10e) the state directory's NAME re-pointed at another directory between the save's
    # identity checks and its replace (the real directory moved away, a decoy directory renamed
    # onto the name): the dir_fd-bound replace lands in the real (moved) directory, and the
    # directory now carrying the state-dir name receives NOTHING through name resolution
    r8e = Fixture(base, "r8statedirswap")
    r8e.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8e.lease, 7200)
    declare(r8e, r8e.lease, hb())
    r8e_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8e.root)))
    r8e_sd.mkdir(parents=True, exist_ok=True)
    r8e_moved = r8e.root / "state.moved"
    r8e_decoy_dir = r8e.root / "decoy.dir"
    r8e_decoy_dir.mkdir()
    saved_srepl3 = aiqt_hooks._orch_lease_hb_save_replace
    r8e_planted = []

    def r8e_replace(tmp_arg, name_arg, dirfd, _real=saved_srepl3):
        if not r8e_planted:
            r8e_planted.append(True)
            os.replace(str(r8e_sd), str(r8e_moved))
            os.replace(str(r8e_decoy_dir), str(r8e_sd))
        return _real(tmp_arg, name_arg, dirfd)
    try:
        aiqt_hooks._orch_lease_hb_save_replace = r8e_replace
        result = stop(r8e)
    finally:
        aiqt_hooks._orch_lease_hb_save_replace = saved_srepl3
    check("leasehb/save-seam-statedir-swap-no-redirect",
          ((r8e_moved / "lease-heartbeat.json").exists(),
           sorted(os.listdir(r8e_sd)),
           r8e.lease.read_bytes(), "heartbeat gap" in text_of(result), r8e_planted),
          (True, [], b"holder: s1\n", True, [True]))


    # (10f) the lease replaced by a HARD LINK to the keeper's events log BEFORE the first
    # open, with the path-based entry check pinned to pass: only the descriptor-bound re-proof
    # at the holder build (_orch_lease_hb_sep_fd on the descriptor in hand) can refuse, with
    # nothing appended through the aliased inode
    r8f = Fixture(base, "r8provealias")
    r8f.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8f.lease, 7200)
    declare(r8f, r8f.lease, hb())
    r8f_events = events_at(r8f)
    r8f_before = meta(r8f_events)
    r8f_link = r8f.root / "alias.tmp"
    os.link(str(r8f_events), str(r8f_link))
    os.replace(str(r8f_link), str(r8f.lease))
    saved_sep8 = aiqt_hooks._orch_lease_hb_bookkeeping_sep
    try:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = lambda p_arg, d_arg: None
        result = stop(r8f)
    finally:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = saved_sep8
    check("leasehb/prove-held-identity-collision-refused",
          (meta(r8f_events) == r8f_before, "shares an inode" in text_of(result)),
          (True, True))


    # (10g) the state directory declared AS the lease's own parent directory, the path check
    # pinned to pass: the holder build itself must refuse by DESCRIPTOR identity (the race leg
    # between the path-based check and the build), and nothing may be created beside the lease
    r8g = Fixture(base, "r8sameparent")
    r8g_dir = r8g.root / "leasedir"
    r8g_dir.mkdir()
    r8g_lease = r8g_dir / "lease.txt"
    r8g_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8g_lease, 7200)
    declare(r8g, r8g_lease, hb())
    r8g_reg_path = r8g.root / ".aiqt" / "orchestration.local.json"
    r8g_reg = json.loads(r8g_reg_path.read_text(encoding="utf-8"))
    r8g_reg["state_dir"] = str(r8g_dir)
    r8g_reg_path.write_text(json.dumps(r8g_reg), encoding="utf-8")
    saved_sep9 = aiqt_hooks._orch_lease_hb_bookkeeping_sep
    try:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = lambda p_arg, d_arg: None
        result = stop(r8g)
    finally:
        aiqt_hooks._orch_lease_hb_bookkeeping_sep = saved_sep9
    check("leasehb/prove-statedir-is-lease-parent-refused",
          ("lies directly in this keeper's own state directory" in text_of(result),
           sorted(os.listdir(r8g_dir))), (True, ["lease.txt"]))


    # (10h) descriptor ownership at the holder build, counted EXACTLY: acquisitions and close
    # ATTEMPTS are counted per descriptor number, and every descriptor the build acquired must
    # see exactly one terminal close attempt, under a NON-OSError fault at the FIRST identity
    # fstat (the freshly opened state directory's) and at the SECOND (the held parent's) alike
    # (red against the round-7 delivery, which leaked the descriptor, and red under a
    # DUPLICATED close, which the attempt count reads as 2, never 1)
    r8h = Fixture(base, "r8provefault")
    r8h_dir = r8h.root / "leasedir"
    r8h_dir.mkdir()
    (r8h_dir / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    r8h_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8h.root)))
    r8h_sd.mkdir(parents=True, exist_ok=True)
    r8h_dests = (str(r8h_sd / "lease-heartbeat.json"), str(r8h_sd / "guard-events.jsonl"))
    r8h_parent = os.open(str(r8h_dir), os.O_RDONLY | os.O_DIRECTORY)
    r8h_leaf = os.open(str(r8h_dir / "lease.txt"), os.O_RDONLY)
    real_open10 = os.open
    real_fstat10 = os.fstat
    real_close10 = os.close

    def r8h_prove_fault(which):
        # one injected fault per scenario: "first" faults the fstat of the freshly opened
        # state-directory descriptor, "second" the held parent's; the counters record every
        # acquisition and every close ATTEMPT (an attempt on an already-closed number counts
        # too, so a duplicated close reads as 2 for that descriptor, a red)
        opened, closes = [], {}

        def counting_open(*a, **k):
            fd = real_open10(*a, **k)
            opened.append(fd)
            return fd

        def counting_close(fd):
            closes[fd] = closes.get(fd, 0) + 1
            real_close10(fd)

        def faulting_fstat(fd):
            if which == "first" and opened and fd == opened[-1]:
                raise RuntimeError("injected prove fstat fault")
            if which == "second" and fd == r8h_parent:
                raise RuntimeError("injected prove fstat fault")
            return real_fstat10(fd)
        holder10 = aiqt_hooks._orch_lease_hb_holder()
        holder10["dests"] = r8h_dests
        raised10 = None
        try:
            os.open = counting_open
            os.fstat = faulting_fstat
            os.close = counting_close
            try:
                aiqt_hooks._orch_lease_hb_prove(holder10, r8h_parent, r8h_leaf, "lease.txt",
                                                str(r8h_dir / "lease.txt"), r8h_dests)
            except RuntimeError as exc:
                raised10 = str(exc)
        finally:
            os.open = real_open10
            os.fstat = real_fstat10
            os.close = real_close10
        leaked10 = []
        for fd10 in opened:
            try:
                real_fstat10(fd10)
                leaked10.append(fd10)
            except OSError:
                pass
        for fd10 in leaked10:
            try:
                real_close10(fd10)  # reclaim a pre-fix leak; the leak list is read first
            except OSError:
                pass
        return (raised10, len(opened), [closes.get(fd10, 0) for fd10 in opened], leaked10,
                holder10.get("state_fd"))
    check("leasehb/prove-statefd-owned-on-fault",
          (r8h_prove_fault("first"), r8h_prove_fault("second")),
          (("injected prove fstat fault", 1, [1], [], None),
           ("injected prove fstat fault", 1, [1], [], None)))

    # the PREVIOUS-HOLDER replacement boundary: a second successful proof closes the previous
    # holder descriptor exactly ONCE (never twice), transfers the new one UNCLOSED, and the
    # REAL event cleanup (_orch_lease_hb_event_cleanup, driven by this vector itself, never a
    # test-side close) then closes the new one exactly once and empties the holder (red under
    # a duplicated close of either descriptor, a duplicated close in the real cleanup
    # included, and red if the transferred descriptor is closed by the build itself)
    r8h2_closes = {}

    def r8h2_close(fd):
        r8h2_closes[fd] = r8h2_closes.get(fd, 0) + 1
        real_close10(fd)
    r8h2_holder = aiqt_hooks._orch_lease_hb_holder()
    r8h2_holder["dests"] = r8h_dests
    r8h2_previous = os.open(str(r8h_sd), os.O_RDONLY | os.O_DIRECTORY)
    r8h2_holder["state_fd"] = r8h2_previous
    try:
        os.close = r8h2_close
        r8h2_sep = aiqt_hooks._orch_lease_hb_prove(r8h2_holder, r8h_parent, r8h_leaf,
                                                   "lease.txt",
                                                   str(r8h_dir / "lease.txt"), r8h_dests)
        r8h2_new = r8h2_holder.get("state_fd")
        r8h2_mid = (r8h2_closes.get(r8h2_previous, 0), r8h2_closes.get(r8h2_new, 0))
        aiqt_hooks._orch_lease_hb_event_cleanup(r8h2_holder)  # the REAL event cleanup
    finally:
        os.close = real_close10
    r8h2_new_live = True
    try:
        real_fstat10(r8h2_new)
    except OSError:
        r8h2_new_live = False
    check("leasehb/prove-previous-holder-close-once",
          (r8h2_sep, r8h2_mid, r8h2_closes.get(r8h2_previous, 0),
           r8h2_closes.get(r8h2_new, 0), r8h2_new_live, r8h2_new == r8h2_previous,
           r8h2_holder.get("state_fd")),
          (None, (1, 0), 1, 1, False, False, None))

    # the replacement boundary under FAULT INJECTION, immediately BEFORE and immediately
    # AFTER the previous-holder close, resolved through the REAL event cleanup: a fault
    # BEFORE the close leaves the previous descriptor OWNED (the holder still carries it, so
    # the real cleanup closes it exactly once), a fault AFTER it leaves it recorded as closed
    # and NEVER retried (the holder carries None and the cleanup closes nothing); the freshly
    # opened descriptor sees exactly one terminal close from the build's own ownership either
    # way, and no descriptor leaks (red on a tree whose replacement bypasses the ledger seam,
    # or that retries an ambiguously closed descriptor)
    def r10a_boundary(side):
        closes10 = {}
        opened10 = []

        def r10a_open(*a, **k):
            fd = real_open10(*a, **k)
            if a and a[0] == str(r8h_sd):
                opened10.append(fd)
            return fd

        def r10a_close(fd):
            closes10[fd] = closes10.get(fd, 0) + 1
            real_close10(fd)
        saved_mark10 = aiqt_hooks._orch_lease_hb_close_mark

        def r10a_mark(fd, done, _real=saved_mark10):
            if side == "before":
                raise RuntimeError("injected before-close fault")
            _real(fd, done)
            raise RuntimeError("injected after-close fault")
        holder10a = aiqt_hooks._orch_lease_hb_holder()
        holder10a["dests"] = r8h_dests
        previous10 = real_open10(str(r8h_sd), os.O_RDONLY | os.O_DIRECTORY)
        holder10a["state_fd"] = previous10
        raised10a = None
        try:
            os.open = r10a_open
            os.close = r10a_close
            aiqt_hooks._orch_lease_hb_close_mark = r10a_mark
            try:
                aiqt_hooks._orch_lease_hb_prove(holder10a, r8h_parent, r8h_leaf, "lease.txt",
                                                str(r8h_dir / "lease.txt"), r8h_dests)
            except RuntimeError as exc:
                raised10a = str(exc)
            owned_mid10 = holder10a.get("state_fd") is previous10
            aiqt_hooks._orch_lease_hb_event_cleanup(holder10a)  # the REAL event cleanup
        finally:
            os.open = real_open10
            os.close = real_close10
            aiqt_hooks._orch_lease_hb_close_mark = saved_mark10
        previous_live10 = True
        try:
            real_fstat10(previous10)
        except OSError:
            previous_live10 = False
        if previous_live10:
            real_close10(previous10)  # reclaim a leak so the suite stays clean; read first
        return (raised10a, owned_mid10, closes10.get(previous10, 0),
                [closes10.get(fd10, 0) for fd10 in opened10], previous_live10,
                holder10a.get("state_fd"))
    check("leasehb/prove-previous-close-fault-before-owned", r10a_boundary("before"),
          ("injected before-close fault", True, 1, [1], False, None))
    check("leasehb/prove-previous-close-fault-after-owned", r10a_boundary("after"),
          ("injected after-close fault", False, 1, [1], False, None))
    for r8h_own in (r8h_parent, r8h_leaf):
        try:
            os.close(r8h_own)
        except OSError:
            pass


    # (10j) the cleanup-time invalidation on the NORMAL path (no injected crash): after a
    # plain Stop the captured holder carries no descriptor, so a later writer attempt performs
    # no open and no write even when fresh directories have REUSED the released descriptor
    # numbers (red when the cleanup-time clears are removed: the append then lands a row
    # through a reused number in an unrelated directory)
    r8j = Fixture(base, "r8postcleanup")
    r8j.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8j.lease, 7200)
    declare(r8j, r8j.lease, hb())
    r8j_events = events_at(r8j)
    r8j_holders = []
    saved_holder8 = aiqt_hooks._orch_lease_hb_holder

    def r8j_holder(_real=saved_holder8):
        made = _real()
        r8j_holders.append(made)
        return made
    try:
        aiqt_hooks._orch_lease_hb_holder = r8j_holder
        result = stop(r8j)
    finally:
        aiqt_hooks._orch_lease_hb_holder = saved_holder8
    r8j_before_rows = r8j_events.read_bytes()
    r8j_victims = [r8j.root / "v1", r8j.root / "v2", r8j.root / "v3"]
    for v8 in r8j_victims:
        v8.mkdir()
    r8j_vfds = [os.open(str(v8), os.O_RDONLY) for v8 in r8j_victims]
    r8j_opens = []
    real_open11 = os.open

    def r8j_open(*a, **k):
        r8j_opens.append(a)
        return real_open11(*a, **k)
    try:
        os.open = r8j_open
        got8j = aiqt_hooks._orch_lease_hb_event(r8j_holders[-1], "warn",
                                                "post-cleanup normal path")
    finally:
        os.open = real_open11
    r8j_victim_files = sorted(p8.name for v8 in r8j_victims for p8 in v8.iterdir())
    for fd11 in r8j_vfds:
        try:
            os.close(fd11)
        except OSError:
            pass
    check("leasehb/post-cleanup-normal-path-writer-withholds",
          (len(r8j_holders) >= 1,
           [r8j_holders[-1].get(k8) for k8 in ("lease_fd", "lease_parent_fd", "state_fd")],
           got8j, r8j_opens, r8j_victim_files,
           r8j_events.read_bytes() == r8j_before_rows),
          (True, [None, None, None], False, [], [], True))


    # (10k) a BUSY declared lock never produces a per-tool-call report, with AT LEAST TWO
    # deferring writers reached in ONE event (the holder-change memory save on the empty
    # memory, then the unreadable-heartbeat row), so the deferral DEDUP itself is exercised:
    # red when the dedup is removed (the captured stop holder's deferred list then carries
    # the deferral twice; the Stop-text count separately guards the report layer's own dedup),
    # and red against the round-7 delivery, which reported "no held, separation-proven lease
    # descriptor" on every PostToolUse call
    r8k = Fixture(base, "r8lockbusy")
    r8k.lease.write_text("holder: s1\nheartbeat_utc: garbage\n", encoding="utf-8")
    age_file(r8k.lease, 7200)
    (r8k.root / "lock.file").write_text("", encoding="utf-8")
    declare(r8k, r8k.lease, hb(mode="refresh", lock_path="lock.file", field="heartbeat_utc"))
    r8k_gate = []
    saved_gate8 = aiqt_hooks._orch_lease_hb_no_holder

    def r8k_count_gate(holder_arg, _real=saved_gate8):
        r8k_gate.append(True)
        return _real(holder_arg)
    r8k_holders = []
    saved_holder9 = aiqt_hooks._orch_lease_hb_holder

    def r8k_holder(_real=saved_holder9):
        made = _real()
        r8k_holders.append(made)
        return made
    fcntl8 = __import__("fcntl")
    r8k_lockfd = os.open(str(r8k.root / "lock.file"), os.O_RDONLY)
    try:
        fcntl8.flock(r8k_lockfd, fcntl8.LOCK_EX | fcntl8.LOCK_NB)
        aiqt_hooks._orch_lease_hb_no_holder = r8k_count_gate
        aiqt_hooks._orch_lease_hb_holder = r8k_holder
        try:
            r8k_posts = [post(r8k) for _ in range(3)]
            r8k_stop = stop(r8k)
        finally:
            aiqt_hooks._orch_lease_hb_no_holder = saved_gate8
            aiqt_hooks._orch_lease_hb_holder = saved_holder9
    finally:
        os.close(r8k_lockfd)
    check("leasehb/lock-busy-no-per-call-report",
          (r8k_posts, _verdict(r8k_stop), len(r8k_gate) >= 8,
           "heartbeat value cannot be read" in text_of(r8k_stop),
           text_of(r8k_stop).count("the declared lock is busy"),
           r8k_holders[-1].get("deferred")),
          ([(0, None, None)] * 3, "warn", True, True, 1,
           ["the declared lock is busy, so this event's bookkeeping is deferred to "
            "a later event"]))


    # (10l) the WITNESS of the disclosed same-user residual class, NOT a pass claim: between
    # the destination open and the identity read the lease's PARENT directory is re-pointed at
    # the keeper's own state directory (the parent moved away, the physical state directory
    # renamed onto the parent's name: a rename in the lease's grandparent, which no check bound
    # to the held descriptors can see), so the append's row LANDS in the file the declared
    # lease path now resolves to. This vector asserts the landing, so the class disclosure in
    # the docs stays true; if a later round closes this window, this vector goes RED and the
    # disclosure must be rewritten with it.
    r8m = Fixture(base, "r8witness")
    r8m_dir = r8m.root / "leasedir"
    r8m_dir.mkdir()
    r8m_lease = r8m_dir / "guard-events.jsonl"  # the lease leaf name MATCHES the events log's
    r8m_lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r8m_lease, 7200)
    declare(r8m, r8m_lease, hb())
    events_at(r8m)
    r8m_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r8m.root)))
    r8m_old = r8m.root / "leasedir.old"
    saved_afstat2 = aiqt_hooks._orch_lease_hb_append_fstat
    r8m_planted = []

    def r8m_fstat(fd, _real=saved_afstat2):
        if not r8m_planted:
            r8m_planted.append(True)
            os.replace(str(r8m_dir), str(r8m_old))
            os.replace(str(r8m_sd), str(r8m_dir))
        return _real(fd)
    try:
        aiqt_hooks._orch_lease_hb_append_fstat = r8m_fstat
        result = stop(r8m)
    finally:
        aiqt_hooks._orch_lease_hb_append_fstat = saved_afstat2
    r8m_landed = []
    if r8m_lease.exists():
        for line8 in r8m_lease.read_text(encoding="utf-8").splitlines():
            if line8.strip():
                try:
                    r8m_landed.append(json.loads(line8))
                except ValueError:
                    pass
    check("leasehb/residual-witness-lease-parent-repoint-row-lands",
          (any(r8.get("kind") == "lease-heartbeat" for r8 in r8m_landed),
           "heartbeat gap" in text_of(result), r8m_planted), (True, True, [True]))


    # --- (11) round-9 vectors: the declared lock is a stable SEPARATE coordination file ---

    # (11a) lock_path NAMING the declared lease: refused at declaration validation (named),
    # and a lock_path spelled differently but RESOLVING to the lease (a symlink alias) is
    # refused at the entry check (named), the lease untouched in both (red without the
    # config-time name check and the entry lock-separation check respectively)
    r9a = Fixture(base, "r9locklease")
    r9a.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r9a.lease, 7200)
    r9a_reg = declare(r9a, r9a.lease, hb(mode="refresh", lock_path=str(r9a.lease)))
    r9a_before = snap(r9a.lease)
    r9a_cfg = aiqt_hooks._orch_lease_hb_config(r9a_reg)
    result = stop(r9a)
    r9a2 = Fixture(base, "r9lockleasealias")
    r9a2.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r9a2.lease, 7200)
    os.symlink(str(r9a2.lease), str(r9a2.root / "lock.alias"))
    declare(r9a2, r9a2.lease, hb(mode="refresh", lock_path="lock.alias"))
    r9a2_before = snap(r9a2.lease)
    result2 = stop(r9a2)
    check("leasehb/lock-path-names-lease-refused",
          (r9a_cfg[0], "names the declared lease path" in str(r9a_cfg[1]),
           "names the declared lease path" in text_of(result),
           "coincides with the declared lease path" in text_of(result2),
           snap(r9a.lease) == r9a_before, snap(r9a2.lease) == r9a2_before),
          ("bad", True, True, True, True, True))

    # (11b) lock_path RESOLVING to the keeper's own past-holder memory: refused at the entry
    # check by resolved path/inode (named), the memory file untouched and nothing written (red
    # without the entry lock-separation check: the keeper's own memory save would otherwise
    # REPLACE the very file its flock is held on)
    r9b = Fixture(base, "r9lockmemory")
    r9b.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r9b.lease, 7200)
    r9b_state = r9b.root / "state"
    r9b_state.mkdir()
    r9b_mem = r9b_state / "lease-heartbeat.json"
    r9b_mem.write_text(json.dumps({"version": 1, "session_id": "s1",
                                   "ts": now_iso(1)}), encoding="utf-8")
    declare(r9b, r9b.lease, hb(mode="refresh",
                               lock_path=str(r9b_state / "lease-heartbeat.json")))
    r9b_reg_path = r9b.root / ".aiqt" / "orchestration.local.json"
    r9b_reg = json.loads(r9b_reg_path.read_text(encoding="utf-8"))
    r9b_reg["state_dir"] = str(r9b_state)
    r9b_reg_path.write_text(json.dumps(r9b_reg), encoding="utf-8")
    r9b_before = snap(r9b_mem)
    result = stop(r9b)
    check("leasehb/lock-path-names-memory-refused",
          ("past-holder memory" in text_of(result),
           "Nothing was written" in text_of(result),
           snap(r9b_mem) == r9b_before, snap(r9b.lease)[0]),
          (True, True, True, b"holder: s1\n"))

    # (11c) the lock renamed ONTO the lease name AFTER flock (the entry check already passed):
    # only the under-the-held-lock identity re-proof can refuse, with the lock inode never
    # replaced, nothing written and nothing recorded (red without the under-lock check: the
    # refresh would then replace the keeper's own held lock through the lease name)
    r9c = locked_fixture("r9lockalias")
    r9c_lock = r9c.root / "lock.file"
    r9c_lock.write_text("holder: s1\n", encoding="utf-8")
    age_file(r9c_lock, 7200)
    r9c_lock_ino = os.stat(r9c_lock).st_ino
    r9c_planted = []
    saved_flock9c = aiqt_hooks._orch_lease_hb_flock

    def r9c_flock(fd, _real=saved_flock9c):
        _real(fd)
        if not r9c_planted:
            r9c_planted.append(True)
            # the cooperating actor renames the lock (carrying plausible lease content) onto
            # the lease name: the keeper's held flock descriptor and the re-opened lease are
            # then the SAME inode, and a refresh would replace the keeper's own held lock
            os.replace(str(r9c_lock), str(r9c.lease))
    try:
        aiqt_hooks._orch_lease_hb_flock = r9c_flock
        result = stop(r9c)
    finally:
        aiqt_hooks._orch_lease_hb_flock = saved_flock9c
    check("leasehb/lock-identity-under-lock-refused",
          ("shares an inode with the declared lease" in text_of(result),
           os.lstat(r9c.lease).st_ino == r9c_lock_ino,
           r9c.lease.read_bytes(), r9c_planted),
          (True, True, b"holder: s1\n", [True]))

    # (11d) INTERLEAVING: with a stable separate lock file, a second cooperating keeper stays
    # excluded through the WHOLE read-prove-write span, probed non-blocking at the under-lock
    # re-open and re-proof, at the load, at BOTH parses, at the holder-memory replacement, at
    # the pre-replace identity re-check, at the replace, at the verification (and its own
    # re-load and re-parse) and at the LAST bookkeeping write of the span (the post-verify
    # gap-row append: at its destination stat, at EVERY write syscall of the row, the repeat
    # after a forced SHORT write included, and once the row has completed), and once the
    # holder-memory save has completed, each reading busy, and the row lands whole (red under
    # a lock released after the destination stat and before the row's write: the write
    # probes then read acquired). The probe classifies ONLY EAGAIN/EWOULDBLOCK as
    # busy; any other flock error is a NAMED vector failure, never read as exclusion. The
    # first open, the first proof and the lock file's own open read acquired (they run before
    # the take), the probe acquires again only after the event releases the lock, and the
    # refresh lands (red when the critical section is narrowed at the read stage or the lock
    # is released before the last bookkeeping row: that probe then reads acquired)
    r9d = Fixture(base, "r9interleave")
    r9d_old = now_iso(2)
    r9d.lease.write_text("holder: s1\nheartbeat_utc: " + r9d_old + "\n", encoding="utf-8")
    age_file(r9d.lease, 7200)
    (r9d.root / "lock.file").write_text("", encoding="utf-8")
    declare(r9d, r9d.lease,
            hb(mode="refresh", lock_path="lock.file", field="heartbeat_utc"))
    # the memory is NOT primed: the holder-change memory save then runs INSIDE the critical
    # section, so its replace seam is probed as part of the span
    fcntl9 = __import__("fcntl")
    errno9d = __import__("errno")

    def r9d_probe():
        fd9 = os.open(str(r9d.root / "lock.file"), os.O_RDONLY)
        try:
            fcntl9.flock(fd9, fcntl9.LOCK_EX | fcntl9.LOCK_NB)
            return "acquired"
        except OSError as exc:
            if exc.errno in (errno9d.EAGAIN, errno9d.EWOULDBLOCK):
                return "busy"
            return "fault({})".format(exc.errno)
        finally:
            os.close(fd9)
    r9d_probes = []

    def r9d_wrap(label9, saved9):
        def wrapped9(*a, **k):
            r9d_probes.append((label9, r9d_probe()))
            return saved9(*a, **k)
        return wrapped9
    r9d_seams = (("open", "_orch_lease_hb_open"), ("prove", "_orch_lease_hb_prove"),
                 ("load", "_orch_lease_hb_load"), ("parse", "_orch_lease_hb_parse"),
                 ("mem-replace", "_orch_lease_hb_save_replace"),
                 ("re-check", "_orch_lease_hb_restat"), ("replace", "_orch_lease_hb_replace"),
                 ("verify", "_orch_lease_hb_verify"), ("row", "_orch_lease_hb_append_fstat"))

    def r9d_after(label9, saved9):
        # the END-of-operation observation: probed once the wrapped operation has returned
        def wrapped9(*a, **k):
            try:
                return saved9(*a, **k)
            finally:
                r9d_probes.append((label9, r9d_probe()))
        return wrapped9
    r9d_short = []

    def r9d_write(saved9):
        # EVERY write syscall of the final row is probed; the first is forced SHORT (one
        # byte), so the row's write loop must issue the repeat write inside the lock too
        def wrapped9(fd, view):
            r9d_probes.append(("row-write", r9d_probe()))
            if not r9d_short:
                r9d_short.append(True)
                return saved9(fd, view[:1])
            return saved9(fd, view)
        return wrapped9
    r9d_extra = (("_orch_lease_hb_memory_save", lambda saved9: r9d_after("mem-done", saved9)),
                 ("_orch_lease_hb_append_checked",
                  lambda saved9: r9d_after("row-done", saved9)),
                 ("_orch_lease_hb_row_write", r9d_write))
    r9d_saved = ([(attr9, getattr(aiqt_hooks, attr9)) for _lbl9, attr9 in r9d_seams]
                 + [(attr9, getattr(aiqt_hooks, attr9)) for attr9, _w9 in r9d_extra])
    try:
        for (label9, attr9), (_a9, saved9) in zip(r9d_seams, r9d_saved):
            setattr(aiqt_hooks, attr9, r9d_wrap(label9, saved9))
        for attr9, wrap9 in r9d_extra:
            setattr(aiqt_hooks, attr9, wrap9(getattr(aiqt_hooks, attr9)))
        result = stop(r9d)
    finally:
        for attr9, saved9 in r9d_saved:
            setattr(aiqt_hooks, attr9, saved9)
    try:
        r9d_last = json.loads((Path(aiqt_hooks._orch_state_dir_for_root(str(r9d.root)))
                               / "guard-events.jsonl").read_text(
                                   encoding="utf-8").splitlines()[-1]).get("decision")
    except (OSError, ValueError, IndexError) as exc:
        r9d_last = "unreadable ({})".format(exc)
    check("leasehb/lock-interleaving-excluded-critical-section",
          (r9d_probes, r9d_probe(), "refreshed NOW" in text_of(result),
           r9d_old in r9d.lease.read_text(encoding="utf-8"), r9d_short, r9d_last),
          ([("open", "acquired"), ("prove", "acquired"), ("open", "acquired"),
            ("open", "busy"), ("prove", "busy"), ("load", "busy"), ("parse", "busy"),
            ("parse", "busy"), ("mem-replace", "busy"), ("mem-done", "busy"),
            ("re-check", "busy"), ("replace", "busy"), ("verify", "busy"), ("load", "busy"),
            ("parse", "busy"), ("row", "busy"), ("row-write", "busy"), ("row-write", "busy"),
            ("row-done", "busy")], "acquired", True, False, [True], "refresh"))

    # (11e) a FAULTED declared lock defers exactly as a busy one does: three PostToolUse calls
    # stay silent (no per-call report) and the Stop surfacing NAMES the flock fault and
    # carries the bookkeeping consequence, never the generic no-descriptor refusal (red
    # without the faulted-lock deferral: every post then carried "no held, separation-proven
    # lease descriptor" as its error text)
    r9e = locked_fixture("r9lockfault")
    errno9 = __import__("errno")
    saved_flock9e = aiqt_hooks._orch_lease_hb_flock

    def r9e_flock(fd):
        raise OSError(errno9.ENOLCK, "no locks available")
    try:
        aiqt_hooks._orch_lease_hb_flock = r9e_flock
        r9e_posts = [post(r9e) for _ in range(3)]
        r9e_stop = stop(r9e)
    finally:
        aiqt_hooks._orch_lease_hb_flock = saved_flock9e
    check("leasehb/lock-fault-no-per-call-report",
          (r9e_posts, "no locks available" in text_of(r9e_stop),
           "bookkeeping is withheld under the same fault" in text_of(r9e_stop),
           "no held, separation-proven lease descriptor" in text_of(r9e_stop)),
          ([(0, None, None)] * 3, True, True, False))

    # (12a) a DUE refresh under a faulted lock renders the flock fault exactly ONCE on Stop:
    # the banner names the fault and carries the bookkeeping consequence, and the row
    # attempt's own faulted-lock deferral is DROPPED rather than rendered a second time (the
    # captured Stop holder's deferred list is empty after the render) (red without the drop:
    # the fault text appeared twice, in the banner and again in the appended withheld report)
    r10e = locked_fixture("r10faultonce")
    errno10 = __import__("errno")
    saved_flock10 = aiqt_hooks._orch_lease_hb_flock

    def r10e_flock(fd):
        raise OSError(errno10.ENOLCK, "no locks available")
    r10e_holders = []
    saved_holder10 = aiqt_hooks._orch_lease_hb_holder

    def r10e_holder(_real=saved_holder10):
        made10 = _real()
        r10e_holders.append(made10)
        return made10
    try:
        aiqt_hooks._orch_lease_hb_flock = r10e_flock
        aiqt_hooks._orch_lease_hb_holder = r10e_holder
        r10e_stop = stop(r10e)
    finally:
        aiqt_hooks._orch_lease_hb_flock = saved_flock10
        aiqt_hooks._orch_lease_hb_holder = saved_holder10
    check("leasehb/lock-fault-rendered-once",
          (text_of(r10e_stop).count("no locks available"),
           "bookkeeping is withheld under the same fault" in text_of(r10e_stop),
           r10e_holders[-1].get("deferred")),
          (1, True, []))

    # (11f) a core fault raised WHILE the declared lock is busy is reported AS the crash on
    # Stop, never folded into the busy deferral, and still never a per-call report (red
    # without the crash-as-crash surfacing: the Stop text then carried only the generic busy
    # deferral and the fault text vanished)
    r9f = locked_fixture("r9busycrash")
    saved_parse9 = aiqt_hooks._orch_lease_hb_parse

    def r9f_parse(text_arg, cfg_arg, keys=None):
        raise RuntimeError("injected busy crash")
    fcntl9f = __import__("fcntl")
    r9f_lockfd = os.open(str(r9f.root / "lock.file"), os.O_RDONLY)
    try:
        fcntl9f.flock(r9f_lockfd, fcntl9f.LOCK_EX | fcntl9f.LOCK_NB)
        aiqt_hooks._orch_lease_hb_parse = r9f_parse
        try:
            r9f_posts = [post(r9f) for _ in range(3)]
            r9f_stop = stop(r9f)
        finally:
            aiqt_hooks._orch_lease_hb_parse = saved_parse9
    finally:
        os.close(r9f_lockfd)
    check("leasehb/lock-busy-crash-reported-as-crash",
          (r9f_posts,
           "lease-heartbeat core fault: RuntimeError: injected busy crash"
           in (r9f_stop[2] or ""),
           (r9f_stop[2] or "").count("lease-heartbeat core fault")),
          ([(0, None, None)] * 3, True, 1))

    # (11g) the save's temporary whose identity was never learned (the pre-write stat itself
    # faulted) is never unlinked, and the leftover is DISCLOSED in the report (red without the
    # disclosure: the file stayed behind silently)
    r9g = Fixture(base, "r9tmpunknown")
    r9g.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r9g.lease, 7200)
    declare(r9g, r9g.lease, hb())
    r9g_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r9g.root)))
    r9g_sd.mkdir(parents=True, exist_ok=True)
    saved_tmpname9 = aiqt_hooks._orch_lease_hb_tmp_name
    saved_tfstat9 = aiqt_hooks._orch_lease_hb_save_tmp_fstat

    def r9g_tfstat(fd):
        raise OSError(5, "injected tmp fstat fault")
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r9g.hb.tmp"
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = r9g_tfstat
        result = stop(r9g)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname9
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = saved_tfstat9
    check("leasehb/save-tmp-unknown-identity-disclosed",
          ("injected tmp fstat fault" in text_of(result),
           "may remain (.forced.r9g.hb.tmp)" in text_of(result),
           (r9g_sd / ".forced.r9g.hb.tmp").exists()), (True, True, True))


    # --- (12) round-10 vectors: the lock is refused wherever ANY plugin writer replaces ---

    def r10_resume(f10, reg10):
        lease10 = reg10.get("lease") if isinstance(reg10.get("lease"), dict) else {}
        return aiqt_hooks._orch_lease_hb_resume_findings(reg10, str(f10.root), lease10)

    # (12b) a lock_path INSIDE the plugin state directory, naming a file the plugin's own
    # whole-file writer replaces (turn-state.json, the _orch_write_json_atomic vector of
    # decided item 1): refused at declaration validation, by the resume audit and on Stop,
    # with the lease untouched and nothing written (red on the round-9 tree: the declaration
    # read ok there, the entry check passed, and a second keeper could acquire the lock after
    # _orch_write_json_atomic replaced it under the first keeper's held flock)
    r10b = Fixture(base, "r10lockstate")
    r10b.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10b.lease, 7200)
    r10b_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10b.root)))
    r10b_sd.mkdir(parents=True, exist_ok=True)
    (r10b_sd / "turn-state.json").write_text("{}", encoding="utf-8")
    r10b_reg = declare(r10b, r10b.lease,
                       hb(mode="refresh", lock_path=str(r10b_sd / "turn-state.json")))
    r10b_before = snap(r10b.lease)
    r10b_cfg = aiqt_hooks._orch_lease_hb_config(r10b_reg, str(r10b.root))
    r10b_find = r10_resume(r10b, r10b_reg)
    result = stop(r10b)
    check("leasehb/lock-path-in-state-dir-refused",
          (r10b_cfg[0], "whole-file writers replace" in str(r10b_cfg[1]),
           len(r10b_find) == 1 and "declaration is invalid" in r10b_find[0]
           and "whole-file writers replace" in r10b_find[0],
           "whole-file writers replace" in text_of(result),
           "Nothing was written" in text_of(result), snap(r10b.lease) == r10b_before),
          ("bad", True, True, True, True, True))

    # (12c)-(12f) EVERY refused spelling at declaration validation AND at the resume audit,
    # through the ONE shared read-only validation (each red on the round-9 tree, whose
    # declaration check refused only the lease's own normpath spelling and whose resume audit
    # returned [] for all of these): the past-holder memory, the guard-events log, a RELATIVE
    # lock_path that resolves to the lease, and a hard-link inode alias of the lease (a
    # spelling realpath cannot fold)
    r10c = Fixture(base, "r10declmem")
    r10c.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10c.lease, 7200)
    r10c_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10c.root)))
    r10c_sd.mkdir(parents=True, exist_ok=True)
    r10c_reg = declare(r10c, r10c.lease,
                       hb(mode="refresh", lock_path=str(r10c_sd / "lease-heartbeat.json")))
    r10c_cfg = aiqt_hooks._orch_lease_hb_config(r10c_reg, str(r10c.root))
    r10c_find = r10_resume(r10c, r10c_reg)
    check("leasehb/lock-decl-names-memory-refused",
          (r10c_cfg[0], "past-holder memory" in str(r10c_cfg[1]),
           len(r10c_find) == 1 and "past-holder memory" in r10c_find[0]),
          ("bad", True, True))

    r10d = Fixture(base, "r10declevents")
    r10d.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10d.lease, 7200)
    r10d_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10d.root)))
    r10d_sd.mkdir(parents=True, exist_ok=True)
    r10d_reg = declare(r10d, r10d.lease,
                       hb(mode="refresh", lock_path=str(r10d_sd / "guard-events.jsonl")))
    r10d_cfg = aiqt_hooks._orch_lease_hb_config(r10d_reg, str(r10d.root))
    r10d_find = r10_resume(r10d, r10d_reg)
    check("leasehb/lock-decl-names-events-refused",
          (r10d_cfg[0], "guard-events log" in str(r10d_cfg[1]),
           len(r10d_find) == 1 and "guard-events log" in r10d_find[0]),
          ("bad", True, True))

    r10f = Fixture(base, "r10declrel")
    r10f.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10f.lease, 7200)
    r10f_reg = declare(r10f, r10f.lease, hb(mode="refresh", lock_path="lease.txt"))
    r10f_cfg = aiqt_hooks._orch_lease_hb_config(r10f_reg, str(r10f.root))
    r10f_find = r10_resume(r10f, r10f_reg)
    check("leasehb/lock-decl-relative-resolves-lease-refused",
          (r10f_cfg[0], "coincides with the declared lease path" in str(r10f_cfg[1]),
           len(r10f_find) == 1 and "coincides with the declared lease path" in r10f_find[0]),
          ("bad", True, True))

    r10g = Fixture(base, "r10declalias")
    r10g.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10g.lease, 7200)
    os.link(str(r10g.lease), str(r10g.root / "lock.hard"))
    r10g_reg = declare(r10g, r10g.lease, hb(mode="refresh", lock_path="lock.hard"))
    r10g_cfg = aiqt_hooks._orch_lease_hb_config(r10g_reg, str(r10g.root))
    r10g_find = r10_resume(r10g, r10g_reg)
    check("leasehb/lock-decl-inode-alias-refused",
          (r10g_cfg[0], "shares an inode with the declared lease path" in str(r10g_cfg[1]),
           len(r10g_find) == 1
           and "shares an inode with the declared lease path" in r10g_find[0]),
          ("bad", True, True))

    # (12g) a stat fault inside the shared validation is the NAMED refusal at declaration, at
    # resume and on Stop alike (chkfcl; this exercises _orch_lease_hb_lock_sep's own fault
    # leg), with a lock_path whose walk crosses a regular file (ENOTDIR: an OSError other
    # than absence)
    r10h = Fixture(base, "r10declfault")
    r10h.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10h.lease, 7200)
    (r10h.root / "plainfile").write_text("", encoding="utf-8")
    r10h_reg = declare(r10h, r10h.lease,
                       hb(mode="refresh", lock_path="plainfile/lock.file"))
    r10h_cfg = aiqt_hooks._orch_lease_hb_config(r10h_reg, str(r10h.root))
    r10h_find = r10_resume(r10h, r10h_reg)
    result = stop(r10h)
    check("leasehb/lock-decl-stat-fault-named",
          (r10h_cfg[0], "cannot be evaluated" in str(r10h_cfg[1]),
           len(r10h_find) == 1 and "cannot be evaluated" in r10h_find[0],
           "cannot be evaluated" in text_of(result)),
          ("bad", True, True, True))

    # (12h) the lock renamed INTO the state directory AFTER flock (declaration and entry both
    # passed): the under-lock state-directory identity SWEEP refuses, by fstat identity on
    # the very descriptor flock was taken on, with the lease untouched (red on the round-9
    # tree, whose under-lock check compared only the lease and the two destinations, so this
    # keeper's whole-file writers could replace its own held lock: decided item 1's width)
    r10k = locked_fixture("r10lockswept")
    r10k_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10k.root)))
    r10k_planted = []
    saved_flock10k = aiqt_hooks._orch_lease_hb_flock

    def r10k_flock(fd, _real=saved_flock10k):
        _real(fd)
        if not r10k_planted:
            r10k_planted.append(True)
            # the cooperating actor renames the HELD lock into the state directory under a
            # whole-file writer's own target name: only the under-lock sweep can see it
            os.replace(str(r10k.root / "lock.file"), str(r10k_sd / "turn-state.json"))
    r10k_before = snap(r10k.lease)
    try:
        aiqt_hooks._orch_lease_hb_flock = r10k_flock
        result = stop(r10k)
    finally:
        aiqt_hooks._orch_lease_hb_flock = saved_flock10k
    check("leasehb/lock-statedir-identity-under-lock-refused",
          ("lives in this keeper's own state directory" in text_of(result),
           "turn-state.json" in text_of(result),
           snap(r10k.lease) == r10k_before, r10k_planted),
          (True, True, True, [True]))

    # (12i) the under-lock fault leg at the held lock's own identity stat is a NAMED refusal,
    # and the _orch_lease_hb_lock_ids seam is exercised: nothing is written (red when that
    # leg is made fail-open: the refresh then proceeded)
    r10i = locked_fixture("r10lockidsfault")
    saved_lockids10 = aiqt_hooks._orch_lease_hb_lock_ids

    def r10i_lock_ids(fd):
        raise OSError(5, "injected lock fstat fault")
    r10i_before = snap(r10i.lease)
    try:
        aiqt_hooks._orch_lease_hb_lock_ids = r10i_lock_ids
        result = stop(r10i)
    finally:
        aiqt_hooks._orch_lease_hb_lock_ids = saved_lockids10
    check("leasehb/lock-held-sep-fault-named",
          ("the held lock cannot be stat'ed" in text_of(result),
           "injected lock fstat fault" in text_of(result),
           snap(r10i.lease) == r10i_before),
          (True, True, True))

    # (12j) the OTHER under-lock fault leg: a bookkeeping-target stat fault inside
    # _orch_lease_hb_lock_held_sep is the named refusal, never a pass (exercised directly on
    # real held descriptors, with a destination path whose walk crosses a regular file)
    r10j_dir = base / "r10helddest"
    r10j_dir.mkdir()
    (r10j_dir / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    (r10j_dir / "lock.file").write_text("", encoding="utf-8")
    (r10j_dir / "plainfile").write_text("", encoding="utf-8")
    r10j_sd = r10j_dir / "state"
    r10j_sd.mkdir()
    r10j_parent = os.open(str(r10j_dir), os.O_RDONLY | os.O_DIRECTORY)
    r10j_leaf = os.open(str(r10j_dir / "lease.txt"), os.O_RDONLY)
    r10j_state = os.open(str(r10j_sd), os.O_RDONLY | os.O_DIRECTORY)
    r10j_lock = os.open(str(r10j_dir / "lock.file"), os.O_RDONLY)
    r10j_holder = aiqt_hooks._orch_lease_hb_holder()
    r10j_holder.update({"dests": (str(r10j_sd / "lease-heartbeat.json"),
                                  str(r10j_dir / "plainfile" / "guard-events.jsonl")),
                        "lease_fd": r10j_leaf, "lease_parent_fd": r10j_parent,
                        "lease_name": "lease.txt",
                        "lease_path": str(r10j_dir / "lease.txt"),
                        "state_fd": r10j_state})
    try:
        r10j_sep = aiqt_hooks._orch_lease_hb_lock_held_sep(r10j_lock, r10j_holder)
    finally:
        for fd10 in (r10j_parent, r10j_leaf, r10j_state, r10j_lock):
            os.close(fd10)
    check("leasehb/lock-held-sep-dest-fault-named",
          "lock/bookkeeping separation cannot be evaluated" in str(r10j_sep), True)

    # (12k) the unknown-identity temporary is disclosed from a FINALLY, for ANY exception
    # class: a non-OSError core fault at the pre-write stat still leaves the leftover
    # disclosed by name (and never unlinked) while the fault itself propagates to the crash
    # path (red on the round-9 tree: the cleanup ran only for OSError/ValueError, so the
    # file stayed behind silently)
    r10m = Fixture(base, "r10tmpnonos")
    r10m.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10m.lease, 7200)
    declare(r10m, r10m.lease, hb())
    r10m_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10m.root)))
    r10m_sd.mkdir(parents=True, exist_ok=True)
    saved_tmpname10 = aiqt_hooks._orch_lease_hb_tmp_name
    saved_tfstat10 = aiqt_hooks._orch_lease_hb_save_tmp_fstat

    def r10m_tfstat(fd):
        raise RuntimeError("injected non-OSError tmp fault")
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r10m.hb.tmp"
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = r10m_tfstat
        r10m_stop = stop(r10m)
    finally:
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname10
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = saved_tfstat10
    check("leasehb/save-tmp-nonoserror-fault-disclosed",
          ("may remain (.forced.r10m.hb.tmp)" in (r10m_stop[2] or ""),
           "RuntimeError: injected non-OSError tmp fault" in (r10m_stop[2] or ""),
           (r10m_sd / ".forced.r10m.hb.tmp").exists()), (True, True, True))

    # (12l) a cleanup stat fault other than confirmed absence still DISCLOSES the temporary
    # name and the fault, and never unlinks: an unreadable result cannot establish absence
    # (red on the round-9 tree, whose cleanup swallowed the stat fault silently)
    r10n = Fixture(base, "r10tmpcleanfault")
    r10n.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r10n.lease, 7200)
    declare(r10n, r10n.lease, hb())
    r10n_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r10n.root)))
    r10n_sd.mkdir(parents=True, exist_ok=True)
    saved_tmpname11 = aiqt_hooks._orch_lease_hb_tmp_name
    saved_tfstat11 = aiqt_hooks._orch_lease_hb_save_tmp_fstat
    real_stat10 = os.stat

    def r10n_tfstat(fd):
        raise OSError(5, "injected tmp fstat fault")

    def r10n_stat(*a, **k):
        if a and a[0] == ".forced.r10n.hb.tmp" and k.get("dir_fd") is not None:
            raise OSError(5, "injected cleanup stat fault")
        return real_stat10(*a, **k)
    try:
        aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: ".forced.r10n.hb.tmp"
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = r10n_tfstat
        os.stat = r10n_stat
        result = stop(r10n)
    finally:
        os.stat = real_stat10
        aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname11
        aiqt_hooks._orch_lease_hb_save_tmp_fstat = saved_tfstat11
    check("leasehb/save-tmp-cleanup-stat-fault-disclosed",
          ("cannot be re-checked for cleanup" in text_of(result),
           "injected cleanup stat fault" in text_of(result),
           "may remain (.forced.r10n.hb.tmp)" in text_of(result),
           (r10n_sd / ".forced.r10n.hb.tmp").exists()), (True, True, True, True))

    # --- (13) round-11 vectors: every terminal cleanup attempts each remaining close ---

    def r11_live(fd, ident):
        # LEAKED: the number is still open AND still carries the identity captured when it was
        # tracked (a reused number carrying another file reads as closed)
        try:
            st11 = os.fstat(fd)
        except OSError:
            return False
        return (st11.st_dev, st11.st_ino) == ident

    # (13a) the EVENT FINALIZER of _orch_lease_heartbeat under a close fault of ANY exception
    # class: a RuntimeError injected immediately BEFORE or AFTER the state-descriptor close
    # syscall, through the REAL finalizer (a Stop through the binding), still attempts every
    # remaining close exactly once (the lease descriptor and its parent each closed once,
    # never leaked), never retries the faulted close (ONE attempt: on the before side that
    # ambiguous close is left as the disclosed leak, never closed a second time), leaves the
    # holder empty and surfaces the fault as the named core fault (red on the round-10 tree,
    # whose fault skipped the lease descriptors' close and left them open)
    def r11a_finalizer(side):
        f11 = Fixture(base, "r11finalizer" + side)
        f11.lease.write_text("holder: s1\n", encoding="utf-8")
        declare(f11, f11.lease, hb())
        prime_memory(f11)
        saved_cleanup11 = aiqt_hooks._orch_lease_hb_event_cleanup
        real_close11 = os.close
        tracked11 = {}
        attempts11 = {}
        fired11 = []
        holders11 = []

        def r11a_spy(holder_arg, *fds_arg):
            # captures the descriptors the REAL finalizer is about to release, then delegates
            holders11.append(holder_arg)
            for role11, key11 in (("state", "state_fd"), ("lease", "lease_fd"),
                                  ("parent", "lease_parent_fd")):
                fd11 = holder_arg.get(key11) if isinstance(holder_arg, dict) else None
                if isinstance(fd11, int) and not isinstance(fd11, bool):
                    st11 = os.fstat(fd11)
                    tracked11[role11] = (fd11, (st11.st_dev, st11.st_ino))
            return saved_cleanup11(holder_arg, *fds_arg)

        def r11a_close(fd):
            for role11, (tfd11, _id11) in tracked11.items():
                if fd == tfd11:
                    attempts11[role11] = attempts11.get(role11, 0) + 1
            if "state" in tracked11 and fd == tracked11["state"][0] and not fired11:
                fired11.append(side)
                if side == "before":
                    raise RuntimeError("injected terminal close fault before")
                real_close11(fd)
                raise RuntimeError("injected terminal close fault after")
            real_close11(fd)
        try:
            aiqt_hooks._orch_lease_hb_event_cleanup = r11a_spy
            os.close = r11a_close
            stop11 = stop(f11)
        finally:
            os.close = real_close11
            aiqt_hooks._orch_lease_hb_event_cleanup = saved_cleanup11
        leaked11 = {role11: r11_live(*pair11) for role11, pair11 in tracked11.items()}
        for role11, pair11 in tracked11.items():
            if leaked11[role11]:
                real_close11(pair11[0])  # reclaim a leak so the suite stays clean; read first
        return (fired11, sorted(tracked11),
                attempts11.get("state", 0), leaked11.get("state"),
                attempts11.get("lease", 0), leaked11.get("lease"),
                attempts11.get("parent", 0), leaked11.get("parent"),
                bool(holders11) and holders11[0].get("state_fd") is None,
                "lease-heartbeat core fault: RuntimeError: injected terminal close fault "
                + side in (stop11[2] or ""))
    check("leasehb/finalizer-state-close-fault-before-attempts-rest", r11a_finalizer("before"),
          (["before"], ["lease", "parent", "state"], 1, True, 1, False, 1, False, True, True))
    check("leasehb/finalizer-state-close-fault-after-attempts-rest", r11a_finalizer("after"),
          (["after"], ["lease", "parent", "state"], 1, False, 1, False, 1, False, True, True))

    # (13b) the judge's re-opened-then-lock disposition under a fault INSIDE the re-opened
    # parent descriptor's close (its syscall ran, then a RuntimeError): the re-opened lease
    # descriptor and the declared lock are each still closed exactly once, the lock is
    # RELEASED (a second keeper's probe acquires it) and the fault surfaces as the named core
    # fault (red on the round-10 tree: the fault skipped both remaining closes and the lock
    # stayed held by the leaked descriptor)
    r11b = locked_fixture("r11reopenfault")
    saved_open11 = aiqt_hooks._orch_lease_hb_open
    saved_lock11 = aiqt_hooks._orch_lease_hb_lock
    real_close11b = os.close
    r11b_opens = []
    r11b_locks = []
    r11b_tracked = {}
    r11b_attempts = {}
    r11b_fired = []

    def r11b_open(*a, **k):
        got11 = saved_open11(*a, **k)
        r11b_opens.append(got11)
        if len(r11b_opens) == 3 and got11[3] is None and r11b_locks \
                and r11b_locks[0][0] == "ok":
            # the third walk is the re-open UNDER the held lock (the first open, the lock
            # file's own walk, then this one): track it and the lock descriptor
            for role11, fd11 in (("reparent", got11[0]), ("releaf", got11[1]),
                                 ("lock", r11b_locks[0][1])):
                st11 = os.fstat(fd11)
                r11b_tracked[role11] = (fd11, (st11.st_dev, st11.st_ino))
        return got11

    def r11b_lock(*a, **k):
        got11 = saved_lock11(*a, **k)
        r11b_locks.append(got11)
        return got11

    def r11b_close(fd):
        for role11, (tfd11, _id11) in r11b_tracked.items():
            if fd == tfd11:
                r11b_attempts[role11] = r11b_attempts.get(role11, 0) + 1
        if "reparent" in r11b_tracked and fd == r11b_tracked["reparent"][0] \
                and not r11b_fired:
            r11b_fired.append(True)
            real_close11b(fd)
            raise RuntimeError("injected re-opened close fault")
        real_close11b(fd)
    try:
        aiqt_hooks._orch_lease_hb_open = r11b_open
        aiqt_hooks._orch_lease_hb_lock = r11b_lock
        os.close = r11b_close
        r11b_stop = stop(r11b)
    finally:
        os.close = real_close11b
        aiqt_hooks._orch_lease_hb_lock = saved_lock11
        aiqt_hooks._orch_lease_hb_open = saved_open11
    r11b_leaked = {role11: r11_live(*pair11) for role11, pair11 in r11b_tracked.items()}
    fcntl11 = __import__("fcntl")
    errno11 = __import__("errno")
    r11b_probe_fd = os.open(str(r11b.root / "lock.file"), os.O_RDONLY)
    try:
        fcntl11.flock(r11b_probe_fd, fcntl11.LOCK_EX | fcntl11.LOCK_NB)
        r11b_probe = "acquired"
    except OSError as exc:
        r11b_probe = "busy" if exc.errno in (errno11.EAGAIN, errno11.EWOULDBLOCK) \
            else "fault({})".format(exc.errno)
    finally:
        os.close(r11b_probe_fd)
    for role11, pair11 in r11b_tracked.items():
        if r11b_leaked[role11]:
            real_close11b(pair11[0])  # reclaim a leak so the suite stays clean; read first
    check("leasehb/judge-reopened-close-fault-releases-lock",
          (r11b_fired, sorted(r11b_tracked),
           r11b_attempts.get("reparent", 0), r11b_leaked.get("reparent"),
           r11b_attempts.get("releaf", 0), r11b_leaked.get("releaf"),
           r11b_attempts.get("lock", 0), r11b_leaked.get("lock"), r11b_probe,
           "lease-heartbeat core fault: RuntimeError: injected re-opened close fault"
           in (r11b_stop[2] or "")),
          ([True], ["lock", "releaf", "reparent"], 1, False, 1, False, 1, False, "acquired",
           True))

    # (13c)-(13g) the lock/state-directory containment is COMPONENT-aware, judged directly by
    # the shared validation: a state directory of '/' contains every lock (red on the
    # round-10 tree, whose textual prefix '//' matched no descendant), an ordinary and a
    # nested descendant are contained, a sibling sharing the directory's textual prefix is
    # not, and a containment comparison fault is the named refusal (red on the round-10 tree,
    # which had no comparison to fault)
    r11c = base / "r11contain"
    (r11c / "state" / "sub" / "deeper").mkdir(parents=True)
    (r11c / "state-sib").mkdir()
    r11c_lease = r11c / "lease.txt"
    r11c_locks = {"ordinary": r11c / "state" / "x.lock",
                  "nested": r11c / "state" / "sub" / "deeper" / "x.lock",
                  "sibling": r11c / "state-sib" / "x.lock"}
    for p11 in [r11c_lease] + list(r11c_locks.values()):
        p11.write_text("", encoding="utf-8")
    r11c_dests = (str(r11c / "state" / "lease-heartbeat.json"),
                  str(r11c / "state" / "guard-events.jsonl"))

    def r11c_sep(lock11, dests11=r11c_dests):
        return aiqt_hooks._orch_lease_hb_lock_sep(str(r11c_lease), str(lock11), dests11)
    r11c_root = r11c_sep(r11c_locks["sibling"],
                         (os.sep + "lease-heartbeat.json", os.sep + "guard-events.jsonl"))
    check("leasehb/lock-contain-statedir-root-refused",
          ("lies inside this keeper's own state directory ({})".format(os.sep)
           in str(r11c_root), "alias spelling" in str(r11c_root)), (True, False))
    r11c_inside = "lies inside this keeper's own state directory ({})".format(r11c / "state")
    r11c_ordinary = r11c_sep(r11c_locks["ordinary"])
    check("leasehb/lock-contain-ordinary-refused",
          (r11c_inside in str(r11c_ordinary), "alias spelling" in str(r11c_ordinary)),
          (True, False))
    r11c_nested = r11c_sep(r11c_locks["nested"])
    check("leasehb/lock-contain-nested-refused",
          (r11c_inside in str(r11c_nested), "alias spelling" in str(r11c_nested)),
          (True, False))
    check("leasehb/lock-contain-prefix-sibling-allowed", r11c_sep(r11c_locks["sibling"]), None)
    saved_within11 = getattr(aiqt_hooks, "_orch_lease_hb_within", None)

    def r11c_fault(child, ancestor):
        raise ValueError("injected containment comparison fault")
    try:
        aiqt_hooks._orch_lease_hb_within = r11c_fault
        r11c_cmp = r11c_sep(r11c_locks["sibling"])
    finally:
        if saved_within11 is None:
            del aiqt_hooks._orch_lease_hb_within
        else:
            aiqt_hooks._orch_lease_hb_within = saved_within11
    check("leasehb/lock-contain-compare-fault-refused",
          ("containment cannot be evaluated" in str(r11c_cmp),
           "injected containment comparison fault" in str(r11c_cmp)), (True, True))

    # (13h) a lock at a NON-inventory state-directory file (resume-barrier.json, replaced by
    # the resume audit's own writer) separates the containment and parent-identity legs at
    # declaration validation AND at the resume audit: the containment leg names it by itself
    # (red when that leg is removed: the parent leg's alias-spelling refusal answers
    # instead), and with the containment leg blinded, as for an alias spelling realpath
    # cannot fold, the parent-identity leg still refuses (red when that leg is removed: the
    # declaration then read ok and the audit stayed silent)
    r11h = Fixture(base, "r11barrierlock")
    r11h.lease.write_text("holder: s1\n", encoding="utf-8")
    r11h_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r11h.root)))
    r11h_sd.mkdir(parents=True, exist_ok=True)
    (r11h_sd / "resume-barrier.json").write_text("{}", encoding="utf-8")
    r11h_reg = declare(r11h, r11h.lease,
                       hb(mode="refresh", lock_path=str(r11h_sd / "resume-barrier.json")))
    r11h_cfg = aiqt_hooks._orch_lease_hb_config(r11h_reg, str(r11h.root))
    r11h_find = r10_resume(r11h, r11h_reg)
    check("leasehb/lock-decl-statedir-file-containment-leg",
          (r11h_cfg[0], "lies inside this keeper's own state directory" in str(r11h_cfg[1]),
           "alias spelling" in str(r11h_cfg[1]),
           len(r11h_find) == 1 and "declaration is invalid" in r11h_find[0]
           and "lies inside this keeper's own state directory" in r11h_find[0]
           and "alias spelling" not in r11h_find[0]),
          ("bad", True, False, True))
    try:
        aiqt_hooks._orch_lease_hb_within = lambda child, ancestor: False
        r11h_cfg2 = aiqt_hooks._orch_lease_hb_config(r11h_reg, str(r11h.root))
        r11h_find2 = r10_resume(r11h, r11h_reg)
    finally:
        if saved_within11 is None:
            del aiqt_hooks._orch_lease_hb_within
        else:
            aiqt_hooks._orch_lease_hb_within = saved_within11
    check("leasehb/lock-decl-statedir-file-parent-leg",
          (r11h_cfg2[0], "under an alias spelling" in str(r11h_cfg2[1]),
           len(r11h_find2) == 1 and "declaration is invalid" in r11h_find2[0]
           and "under an alias spelling" in r11h_find2[0]),
          ("bad", True, True))

    # (13i) BOTH fault legs of the under-lock state-directory sweep in
    # _orch_lease_hb_lock_held_sep are the named refusal, exercised directly on real held
    # descriptors: the directory listing faults (the held state descriptor is not a
    # directory: ENOTDIR), and one entry's identity stat faults (EIO injected for that entry
    # alone); red when either leg is made fail-open
    r11i_dir = base / "r11heldsweep"
    (r11i_dir / "state").mkdir(parents=True)
    (r11i_dir / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    (r11i_dir / "lock.file").write_text("", encoding="utf-8")
    (r11i_dir / "plainfile").write_text("", encoding="utf-8")
    (r11i_dir / "state" / "victim.json").write_text("{}", encoding="utf-8")
    r11i_parent = os.open(str(r11i_dir), os.O_RDONLY | os.O_DIRECTORY)
    r11i_leaf = os.open(str(r11i_dir / "lease.txt"), os.O_RDONLY)
    r11i_state = os.open(str(r11i_dir / "state"), os.O_RDONLY | os.O_DIRECTORY)
    r11i_plain = os.open(str(r11i_dir / "plainfile"), os.O_RDONLY)
    r11i_lock = os.open(str(r11i_dir / "lock.file"), os.O_RDONLY)
    real_stat11 = os.stat

    def r11i_holder(state_fd):
        holder11 = aiqt_hooks._orch_lease_hb_holder()
        holder11.update({"dests": (str(r11i_dir / "state" / "lease-heartbeat.json"),
                                   str(r11i_dir / "state" / "guard-events.jsonl")),
                         "lease_fd": r11i_leaf, "lease_parent_fd": r11i_parent,
                         "lease_name": "lease.txt", "lease_path": str(r11i_dir / "lease.txt"),
                         "state_fd": state_fd})
        return holder11

    def r11i_stat(*a, **k):
        if a and a[0] == "victim.json" and k.get("dir_fd") == r11i_state:
            raise OSError(5, "injected entry stat fault")
        return real_stat11(*a, **k)
    try:
        r11i_list = aiqt_hooks._orch_lease_hb_lock_held_sep(r11i_lock, r11i_holder(r11i_plain))
        try:
            os.stat = r11i_stat
            r11i_entry = aiqt_hooks._orch_lease_hb_lock_held_sep(r11i_lock,
                                                                 r11i_holder(r11i_state))
        finally:
            os.stat = real_stat11
    finally:
        for r11i_fd in (r11i_parent, r11i_leaf, r11i_state, r11i_plain, r11i_lock):
            os.close(r11i_fd)
    check("leasehb/lock-held-sep-statedir-list-fault-named",
          "lock/state-directory separation cannot be evaluated" in str(r11i_list), True)
    check("leasehb/lock-held-sep-statedir-entry-fault-named",
          ("lock/state-directory separation cannot be evaluated" in str(r11i_entry),
           "injected entry stat fault" in str(r11i_entry)), (True, True))

    # (13j) declaration validation and the resume audit with the state directory ABSENT
    # create nothing: a valid lock declaration (with a heartbeat field, so the audit also
    # reads the lease) leaves the state directory absent (red when either path calls the
    # separation check, whose absent-state-directory leg creates it)
    r11j = Fixture(base, "r11nostatedir")
    r11j.lease.write_text("holder: s1\nheartbeat_utc: " + now_iso(0) + "\n", encoding="utf-8")
    (r11j.root / "lock.file").write_text("", encoding="utf-8")
    r11j_reg = declare(r11j, r11j.lease, hb(mode="refresh", lock_path="lock.file",
                                            field="heartbeat_utc"))
    r11j_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r11j.root)))
    r11j_before = r11j_sd.exists()
    r11j_cfg = aiqt_hooks._orch_lease_hb_config(r11j_reg, str(r11j.root))
    r11j_find = r10_resume(r11j, r11j_reg)
    check("leasehb/lock-decl-statedir-absent-stays-absent",
          (r11j_before, r11j_cfg[0], r11j_find, r11j_sd.exists()), (False, "ok", [], False))

    # (13k) the heartbeat FIELD write's temporary cleanup is finally-based and identity-aware,
    # with its residue DISCLOSED: a non-OSError interruption after the exclusive create still
    # removes the temporary this write proved it owns (red on the round-10 tree: it stayed
    # behind silently); a failed unlink discloses the leftover by name (red there: swallowed
    # silently); an identity never learned leaves the temporary in place and discloses it (red
    # there: no identity was learned at all, the write proceeded); and a temporary name
    # re-pointed at ANOTHER entry before the cleanup is never removed (red there: the planted
    # entry was unlinked)
    def field_fixture(name):
        f11 = Fixture(base, name)
        f11.lease.write_text("holder: s1\nheartbeat_utc: " + now_iso(2) + "\n",
                             encoding="utf-8")
        age_file(f11.lease, 7200)
        declare(f11, f11.lease, hb(mode="refresh", field="heartbeat_utc"))
        prime_memory(f11)
        return f11

    def field_run(f11, tmp_name, patches):
        # patches: (owner, attribute, replacement) triples, each restored afterwards
        saved11 = [(owner11, attr11, getattr(owner11, attr11, None))
                   for owner11, attr11, _r11 in patches]
        saved_tmpname11b = aiqt_hooks._orch_lease_hb_tmp_name
        try:
            aiqt_hooks._orch_lease_hb_tmp_name = lambda name_arg: tmp_name
            for owner11, attr11, repl11 in patches:
                setattr(owner11, attr11, repl11)
            return stop(f11)
        finally:
            for owner11, attr11, orig11 in reversed(saved11):
                if orig11 is None:
                    delattr(owner11, attr11)
                else:
                    setattr(owner11, attr11, orig11)
            aiqt_hooks._orch_lease_hb_tmp_name = saved_tmpname11b

    def same_file(fd, path11):
        try:
            a11, b11 = os.fstat(fd), os.lstat(str(path11))
        except OSError:
            return False
        return (a11.st_dev, a11.st_ino) == (b11.st_dev, b11.st_ino)
    real_fchmod11 = os.fchmod
    real_fsync11 = os.fsync
    real_unlink11 = os.unlink

    r11k = field_fixture("r11fieldnonos")
    r11k_tmp = r11k.root / ".forced.r11k.hb.tmp"
    r11k_before = snap(r11k.lease)

    def r11k_fchmod(fd, mode):
        if same_file(fd, r11k_tmp):
            raise RuntimeError("injected field fchmod fault")
        return real_fchmod11(fd, mode)
    r11k_stop = field_run(r11k, ".forced.r11k.hb.tmp", [(os, "fchmod", r11k_fchmod)])
    check("leasehb/field-tmp-nonoserror-interruption-cleaned",
          ("lease-heartbeat core fault: RuntimeError: injected field fchmod fault"
           in (r11k_stop[2] or ""), "may remain" in (r11k_stop[2] or ""),
           os.path.lexists(str(r11k_tmp)), snap(r11k.lease) == r11k_before),
          (True, False, False, True))

    r11l = field_fixture("r11fieldunlink")
    r11l_tmp = r11l.root / ".forced.r11l.hb.tmp"
    r11l_before = snap(r11l.lease)

    def r11l_fsync(fd):
        if same_file(fd, r11l_tmp):
            raise OSError(errno11.ENOSPC, "injected field write fault")
        return real_fsync11(fd)

    def r11l_unlink(*a, **k):
        if a and a[0] == ".forced.r11l.hb.tmp":
            raise PermissionError(errno11.EACCES, "injected field unlink fault")
        return real_unlink11(*a, **k)
    result = field_run(r11l, ".forced.r11l.hb.tmp",
                       [(os, "fsync", r11l_fsync), (os, "unlink", r11l_unlink)])
    check("leasehb/field-tmp-unlink-fault-disclosed",
          ("injected field write fault" in text_of(result),
           "cannot be removed" in text_of(result),
           "injected field unlink fault" in text_of(result),
           "may remain (.forced.r11l.hb.tmp)" in text_of(result),
           os.path.lexists(str(r11l_tmp)), snap(r11l.lease) == r11l_before),
          (True, True, True, True, True, True))

    r11m = field_fixture("r11fieldnoid")
    r11m_tmp = r11m.root / ".forced.r11m.hb.tmp"
    r11m_before = snap(r11m.lease)

    def r11m_tfstat(fd):
        raise OSError(5, "injected field tmp fstat fault")
    result = field_run(r11m, ".forced.r11m.hb.tmp",
                       [(aiqt_hooks, "_orch_lease_hb_field_tmp_fstat", r11m_tfstat)])
    check("leasehb/field-tmp-unknown-identity-kept-disclosed",
          ("the temporary file cannot be checked" in text_of(result),
           "injected field tmp fstat fault" in text_of(result),
           "may remain (.forced.r11m.hb.tmp)" in text_of(result),
           os.path.lexists(str(r11m_tmp)), snap(r11m.lease) == r11m_before),
          (True, True, True, True, True))

    r11n = field_fixture("r11fieldrepoint")
    r11n_tmp = r11n.root / ".forced.r11n.hb.tmp"
    r11n_plant = r11n.root / "planted.txt"
    r11n_plant.write_text("planted\n", encoding="utf-8")
    r11n_before = snap(r11n.lease)

    def r11n_fsync(fd):
        if same_file(fd, r11n_tmp):
            # another actor re-points the temporary NAME at its own entry before the cleanup
            os.replace(str(r11n_plant), str(r11n_tmp))
            raise OSError(errno11.ENOSPC, "injected field write fault")
        return real_fsync11(fd)
    result = field_run(r11n, ".forced.r11n.hb.tmp", [(os, "fsync", r11n_fsync)])
    r11n_kept = r11n_tmp.read_bytes() if os.path.lexists(str(r11n_tmp)) else None
    check("leasehb/field-tmp-repointed-entry-kept",
          ("injected field write fault" in text_of(result), r11n_kept,
           snap(r11n.lease) == r11n_before),
          (True, b"planted\n", True))

    # (13l) a NON-OSError interruption of the field write whose cleanup CANNOT remove the
    # temporary discloses the residue by name through the hook result, and the temporary
    # stays (red when the _orch_lease_hb_withheld call is removed, or when
    # _orch_lease_hb_refresh stops passing the event holder: the disclosure then has no
    # channel at all)
    r12a = field_fixture("r12fieldnonosresidue")
    r12a_tmp = r12a.root / ".forced.r12a.hb.tmp"
    r12a_before = snap(r12a.lease)

    def r12a_fchmod(fd, mode):
        if same_file(fd, r12a_tmp):
            raise RuntimeError("injected field fchmod fault")
        return real_fchmod11(fd, mode)

    def r12a_unlink(*a, **k):
        if a and a[0] == ".forced.r12a.hb.tmp":
            raise PermissionError(errno11.EACCES, "injected field unlink fault")
        return real_unlink11(*a, **k)
    r12a_stop = field_run(r12a, ".forced.r12a.hb.tmp",
                          [(os, "fchmod", r12a_fchmod), (os, "unlink", r12a_unlink)])
    r12a_text = (r12a_stop[2] or "") + " " + text_of(r12a_stop)
    check("leasehb/field-tmp-nonoserror-residue-disclosed",
          ("lease-heartbeat core fault: RuntimeError: injected field fchmod fault" in r12a_text,
           "the heartbeat field write was interrupted" in r12a_text,
           "injected field unlink fault" in r12a_text,
           "may remain (.forced.r12a.hb.tmp)" in r12a_text,
           os.path.lexists(str(r12a_tmp)), snap(r12a.lease) == r12a_before),
          (True, True, True, True, True, True))

    # (13m) a NAMED OSError failure followed by a NON-OSError fault at the temporary close
    # still discloses the residue through the hook result: the channel follows the
    # propagating exception, never the named failure (red when the channel is chosen by the
    # failure being None: the disclosure joined the no-write detail that the propagating
    # fault discards)
    r12b = field_fixture("r12fielddouble")
    r12b_tmp = r12b.root / ".forced.r12b.hb.tmp"
    r12b_before = snap(r12b.lease)
    r12b_fired = []
    real_close12 = os.close

    def r12b_fsync(fd):
        if same_file(fd, r12b_tmp):
            raise OSError(errno11.ENOSPC, "injected field write fault")
        return real_fsync11(fd)

    def r12b_close(fd):
        if not r12b_fired and same_file(fd, r12b_tmp):
            r12b_fired.append(True)
            real_close12(fd)  # closed once; the fault arrives after the syscall
            raise RuntimeError("injected field close fault")
        real_close12(fd)

    def r12b_unlink(*a, **k):
        if a and a[0] == ".forced.r12b.hb.tmp":
            raise PermissionError(errno11.EACCES, "injected field unlink fault")
        return real_unlink11(*a, **k)
    r12b_stop = field_run(r12b, ".forced.r12b.hb.tmp",
                          [(os, "fsync", r12b_fsync), (os, "close", r12b_close),
                           (os, "unlink", r12b_unlink)])
    r12b_text = (r12b_stop[2] or "") + " " + text_of(r12b_stop)
    check("leasehb/field-tmp-double-fault-residue-disclosed",
          (r12b_fired,
           "lease-heartbeat core fault: RuntimeError: injected field close fault" in r12b_text,
           "the heartbeat field write was interrupted" in r12b_text,
           "injected field unlink fault" in r12b_text,
           "may remain (.forced.r12b.hb.tmp)" in r12b_text,
           os.path.lexists(str(r12b_tmp)), snap(r12b.lease) == r12b_before),
          ([True], True, True, True, True, True, True))

    # (13n) the EQUALITY end of the containment bound: a lock declared AS the state
    # directory itself is contained (commonpath equals the directory) and refused, by the
    # shared validation and at declaration validation (red under a strict-descendant
    # containment: no other leg refuses the directory itself); just inside is the ordinary
    # descendant and just outside the textual-prefix sibling, both above
    r12c_direct = r11c_sep(r11c / "state")
    r12c = Fixture(base, "r12statedirlock")
    r12c.lease.write_text("holder: s1\n", encoding="utf-8")
    r12c_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r12c.root)))
    r12c_sd.mkdir(parents=True, exist_ok=True)
    r12c_reg = declare(r12c, r12c.lease, hb(mode="refresh", lock_path=str(r12c_sd)))
    r12c_cfg = aiqt_hooks._orch_lease_hb_config(r12c_reg, str(r12c.root))
    # and through the hook's EVENT ENTRY, with a refresh due: the literal declaration is
    # refused at declaration validation, and a lock path that reaches the state directory
    # only AFTER declaration validation (a symlink re-pointed there between the two runs of
    # the shared validation) is refused by the explicit entry recheck; each before any lease
    # open, lock or write (red with that recheck removed: the event then opens the lease and
    # takes the lock)
    r12c_calls = []
    real_lock13 = aiqt_hooks._orch_lease_hb_lock
    real_open13 = aiqt_hooks._orch_lease_hb_open
    real_config13 = aiqt_hooks._orch_lease_hb_config

    def r12c_lock(*a, **k):
        r12c_calls.append("lock")
        return real_lock13(*a, **k)

    def r12c_open(*a, **k):
        r12c_calls.append("open")
        return real_open13(*a, **k)

    def r12c_state(f13):
        sd13 = Path(aiqt_hooks._orch_state_dir_for_root(str(f13.root)))
        return (snap(f13.lease), sorted(os.listdir(str(f13.root))),
                sorted((p13.name, p13.read_bytes()) for p13 in sd13.iterdir()))

    def r12c_event(f13, after_config=None):
        def config13(*a, **k):
            validated13 = real_config13(*a, **k)
            if after_config is not None:
                after_config()
            return validated13
        del r12c_calls[:]
        before13 = r12c_state(f13)
        try:
            aiqt_hooks._orch_lease_hb_lock = r12c_lock
            aiqt_hooks._orch_lease_hb_open = r12c_open
            aiqt_hooks._orch_lease_hb_config = config13
            result13 = stop(f13)
        finally:
            aiqt_hooks._orch_lease_hb_config = real_config13
            aiqt_hooks._orch_lease_hb_open = real_open13
            aiqt_hooks._orch_lease_hb_lock = real_lock13
        text13 = (result13[2] or "") + " " + text_of(result13)
        return ("declaration is invalid" in text13,
                "lies inside this keeper's own state directory" in text13,
                "declare a distinct lock_path" in text13, list(r12c_calls),
                r12c_state(f13) == before13)
    age_file(r12c.lease, 7200)
    prime_memory(r12c)
    r12c_decl = r12c_event(r12c)
    r12e = Fixture(base, "r13statedirlockentry")
    r12e.lease.write_text("holder: s1\n", encoding="utf-8")
    age_file(r12e.lease, 7200)
    prime_memory(r12e)
    r12e_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(r12e.root)))
    (r12e.root / "lock.file").write_text("", encoding="utf-8")
    r12e_link = r12e.root / "lock.link"
    os.symlink(str(r12e.root / "lock.file"), str(r12e_link))
    declare(r12e, r12e.lease, hb(mode="refresh", lock_path=str(r12e_link)))

    def r12e_repoint():
        os.unlink(str(r12e_link))
        os.symlink(str(r12e_sd), str(r12e_link))
    r12c_entry = r12c_event(r12e, r12e_repoint)
    check("leasehb/lock-contain-statedir-itself-refused",
          (r11c_inside in str(r12c_direct), "alias spelling" in str(r12c_direct),
           r12c_cfg[0], "lies inside this keeper's own state directory" in str(r12c_cfg[1]),
           "alias spelling" in str(r12c_cfg[1]), r12c_decl, r12c_entry),
          (True, False, "bad", True, False, (True, True, False, [], True),
           (False, True, True, [], True)))

    # (13o) the walk and lock cleanups keep the FIRST cleanup fault, as _orch_lease_hb_close
    # does: a transfer close faults BEFORE its syscall (two descriptors then stay owned by
    # the terminal cleanup), and both terminal closes fault AFTER theirs; the first of those
    # faults propagates (red under the last-fault overwrite) and every close is attempted
    r12d = base / "r12firstfault"
    (r12d / "sub").mkdir(parents=True)
    (r12d / "sub" / "lease.txt").write_text("holder: s1\n", encoding="utf-8")
    (r12d / "lock.file").write_text("", encoding="utf-8")
    real_close_held12 = aiqt_hooks._orch_lease_hb_close_held
    real_open12 = aiqt_hooks._orch_lease_hb_open
    r12d_armed = []
    r12d_calls = []

    def r12d_close_held(held, fd):
        if not r12d_armed:
            return real_close_held12(held, fd)
        r12d_calls.append(fd)
        if len(r12d_calls) == 1:
            raise RuntimeError("injected transfer close fault")
        real_close_held12(held, fd)
        raise RuntimeError("injected cleanup close fault {}".format(len(r12d_calls) - 1))

    def r12d_open(root_arg, path_arg):
        opened12 = real_open12(root_arg, path_arg)
        r12d_armed.append(True)
        return opened12

    def r12d_run(call):
        del r12d_calls[:]
        try:
            call()
        except RuntimeError as exc:
            return (str(exc), len(r12d_calls))
        return ("no fault", len(r12d_calls))
    try:
        aiqt_hooks._orch_lease_hb_close_held = r12d_close_held
        r12d_armed.append(True)
        r12d_walk = r12d_run(lambda: aiqt_hooks._orch_lease_hb_open(
            str(r12d), str(r12d / "sub" / "lease.txt")))
        del r12d_armed[:]
        aiqt_hooks._orch_lease_hb_open = r12d_open
        r12d_lock = r12d_run(lambda: aiqt_hooks._orch_lease_hb_lock(
            str(r12d), str(r12d / "lock.file")))
    finally:
        aiqt_hooks._orch_lease_hb_open = real_open12
        aiqt_hooks._orch_lease_hb_close_held = real_close_held12
    check("leasehb/walk-lock-cleanup-first-fault-kept", (r12d_walk, r12d_lock),
          (("injected cleanup close fault 1", 3), ("injected cleanup close fault 1", 3)))

    # (13p)-(13s) a fault of a class OTHER than OSError raised INSIDE the temporary cleanup
    # itself (an interrupt at its stat or at its unlink) still names the temporary through
    # the hook result, for the field writer and for the memory save alike, and the fault
    # still propagates to the crash path. Two legs each: after a NAMED failure the cleanup's
    # own fault is the one reported and the named failure leads the disclosure (red with that
    # prefix removed: the field writer dropped it, and the memory save would drop it the
    # same way); while a body fault is already propagating, that FIRST
    # fault is the one reported and the cleanup's fault never displaces it (red with the
    # call-site catch removed: the cleanup's fault escaped before any disclosure and displaced
    # the first fault; the first-fault legs are red too when the cleanup's fault is always
    # re-raised)
    real_stat13 = os.stat

    def r13_in(dir_fd, path13):
        return dir_fd is not None and same_file(dir_fd, path13)

    def r13_run(f13, tmp13, where13, first13, kind13, home13):
        # where13: the cleanup step interrupted ('stat' or 'unlink'); first13: a body fault
        # is already propagating when the cleanup runs; home13: the directory the writer
        # creates its temporary in (the lease parent, or the state directory)
        def fsync13(fd):
            if same_file(fd, home13 / tmp13):
                if first13:
                    raise RuntimeError("injected {} body interrupt".format(kind13))
                raise OSError(errno11.ENOSPC, "injected {} write fault".format(kind13))
            return real_fsync11(fd)

        def stat13(*a, **k):
            if where13 == "stat" and a and a[0] == tmp13 and r13_in(k.get("dir_fd"), home13):
                raise RuntimeError("injected {} cleanup stat interrupt".format(kind13))
            return real_stat13(*a, **k)

        def unlink13(*a, **k):
            if where13 == "unlink" and a and a[0] == tmp13 \
                    and r13_in(k.get("dir_fd"), home13):
                raise RuntimeError("injected {} cleanup unlink interrupt".format(kind13))
            return real_unlink11(*a, **k)
        before13 = snap(f13.lease)
        result13 = field_run(f13, tmp13, [(os, "fsync", fsync13), (os, "stat", stat13),
                                          (os, "unlink", unlink13)])
        text13 = (result13[2] or "") + " " + text_of(result13)
        fault13 = "injected {} cleanup {} interrupt".format(kind13, where13)
        return ("lease-heartbeat core fault: RuntimeError: injected {} body interrupt".format(
                    kind13) in text13,
                "lease-heartbeat core fault: RuntimeError: " + fault13 in text13,
                "cleanup was interrupted (RuntimeError: {}) and the file may remain ({})".format(
                    fault13, tmp13) in text13,
                "cannot be written ([Errno {}] injected {} write fault); its temporary file "
                "cleanup was interrupted (RuntimeError: {})".format(
                    errno11.ENOSPC, kind13, fault13) in text13,
                os.path.lexists(str(home13 / tmp13)), snap(f13.lease) == before13)

    def r13_field(name13, where13, first13):
        f13 = field_fixture(name13)
        return r13_run(f13, ".forced.{}.hb.tmp".format(name13), where13, first13, "field",
                       f13.root)

    def r13_memory(name13, where13, first13):
        f13 = Fixture(base, name13)
        f13.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f13.lease, 7200)
        declare(f13, f13.lease, hb())
        sd13 = Path(aiqt_hooks._orch_state_dir_for_root(str(f13.root)))
        sd13.mkdir(parents=True, exist_ok=True)
        return r13_run(f13, ".forced.{}.hb.tmp".format(name13), where13, first13, "memory",
                       sd13)
    check("leasehb/field-tmp-cleanup-stat-interrupt-disclosed",
          (r13_field("r13fieldstat", "stat", False), r13_field("r13fieldstat1", "stat", True)),
          ((False, True, True, True, True, True), (True, False, True, False, True, True)))
    check("leasehb/field-tmp-cleanup-unlink-interrupt-disclosed",
          (r13_field("r13fieldunlink", "unlink", False),
           r13_field("r13fieldunlink1", "unlink", True)),
          ((False, True, True, True, True, True), (True, False, True, False, True, True)))
    check("leasehb/save-tmp-cleanup-stat-interrupt-disclosed",
          (r13_memory("r13memstat", "stat", False), r13_memory("r13memstat1", "stat", True)),
          ((False, True, True, True, True, True), (True, False, True, False, True, True)))
    check("leasehb/save-tmp-cleanup-unlink-interrupt-disclosed",
          (r13_memory("r13memunlink", "unlink", False),
           r13_memory("r13memunlink1", "unlink", True)),
          ((False, True, True, True, True, True), (True, False, True, False, True, True)))

    # (13t)-(13y) the cleanup disclosure survives a cleanup fault whose text cannot be rendered,
    # and a STOP (a fault that is not an Exception: an interrupt or an exit) raised in the
    # cleanup keeps its stop semantics, for the field writer and for the memory save alike. A
    # cleanup fault whose str() raises (a ValueError for the field writer, a MemoryError for
    # the memory save) still names the temporary on the holder, through the fixed disclosure
    # and after any named failure, and the fault handed to the crash recorder is the FIRST one
    # (red with the rendering fallback removed: the rendering fault escaped, displaced the
    # first fault and lost the disclosure). A stop raised in the cleanup while an Exception is
    # already propagating escapes the event, with the temporary named on the holder first
    # (red with the stop rule removed: the stop was swallowed and the crash recorder reported
    # the Exception); a cleanup fault of either class raised while a stop is already
    # propagating never displaces that stop (red when the cleanup fault always propagates, or
    # propagates whenever either fault is a stop)
    class R14Unprintable(RuntimeError):
        # its str() raises the exception class it carries: its text cannot be rendered
        def __str__(self):
            raise self.args[0]("injected unprintable fault text")

    def r14_text(exc14):
        # an escaped fault's text, through a guard: an injected fault whose str() raises the
        # stop it carries (R15StopText) yields a named marker, so the check reporting it goes
        # red by name instead of the stop aborting the suite; any other fault raised while
        # formatting is never swallowed here
        try:
            return "{}: {}".format(type(exc14).__name__, exc14)
        except BaseException as fmt14:
            if fmt14 is not getattr(exc14, "stop15", None):
                raise
            return "{}: <its text raised {}>".format(type(exc14).__name__,
                                                     type(fmt14).__name__)

    def r14_run(f14, tmp14, home14, kind14, body14, cleanup14, shown14):
        # body14: the fault raised at the fsync of the temporary (None: a NAMED ENOSPC write
        # failure instead); cleanup14: the fault raised at the cleanup stat of the temporary;
        # shown14: the rendered fault the disclosure carries (empty when it cannot be rendered)
        holders14 = []
        crashed14 = []
        real_holder14 = aiqt_hooks._orch_lease_hb_holder
        real_crash14 = aiqt_hooks._orch_lease_hb_crash_row

        def holder14():
            made14 = real_holder14()
            holders14.append(made14)
            return made14

        def crash14(holder_arg, exc_arg):
            crashed14.append(type(exc_arg).__name__)
            return real_crash14(holder_arg, exc_arg)

        def fsync14(fd):
            if same_file(fd, home14 / tmp14):
                if body14 is not None:
                    raise body14
                raise OSError(errno11.ENOSPC, "injected {} write fault".format(kind14))
            return real_fsync11(fd)

        def stat14(*a, **k):
            if a and a[0] == tmp14 and r13_in(k.get("dir_fd"), home14):
                raise cleanup14
            return real_stat13(*a, **k)
        before14 = snap(f14.lease)
        escaped14 = None
        try:
            field_run(f14, tmp14, [(os, "fsync", fsync14), (os, "stat", stat14),
                                   (aiqt_hooks, "_orch_lease_hb_holder", holder14),
                                   (aiqt_hooks, "_orch_lease_hb_crash_row", crash14)])
        except BaseException as exc14:
            # the stop a cleanup fault's str() raises (R15StopText) is injected too
            if exc14 is not body14 and exc14 is not cleanup14 \
                    and exc14 is not getattr(cleanup14, "stop15", None):
                raise  # not an injected fault (a real interrupt): never swallowed here
            escaped14 = r14_text(exc14)
        # the record on the holder, whether or not a stop escaped before the hook result
        text14 = " ".join(d14 for h14 in holders14 for d14 in (h14.get("withheld") or []))
        disclosed14 = ("its temporary file cleanup was interrupted{} and the file may remain "
                       "({})".format(shown14, tmp14))
        return (escaped14, sorted(set(crashed14)),
                "lease-heartbeat core fault: RuntimeError: injected {} body interrupt".format(
                    kind14) in text14,
                disclosed14 in text14,
                "cannot be written ([Errno {}] injected {} write fault); {}".format(
                    errno11.ENOSPC, kind14, disclosed14) in text14,
                os.path.lexists(str(home14 / tmp14)), snap(f14.lease) == before14)

    def r14_field(name14, body14, cleanup14, shown14):
        f14 = field_fixture(name14)
        return r14_run(f14, ".forced.{}.hb.tmp".format(name14), f14.root, "field", body14,
                       cleanup14, shown14)

    def r14_memory(name14, body14, cleanup14, shown14):
        f14 = Fixture(base, name14)
        f14.lease.write_text("holder: s1\n", encoding="utf-8")
        age_file(f14.lease, 7200)
        declare(f14, f14.lease, hb())
        sd14 = Path(aiqt_hooks._orch_state_dir_for_root(str(f14.root)))
        sd14.mkdir(parents=True, exist_ok=True)
        return r14_run(f14, ".forced.{}.hb.tmp".format(name14), sd14, "memory", body14,
                       cleanup14, shown14)

    def r14_body(kind14):
        return RuntimeError("injected {} body interrupt".format(kind14))

    def r14_stopped(escaped14):
        return (escaped14, [], False, True, False, True, True)
    r14_fmt_want = ((None, ["R14Unprintable"], False, True, True, True, True),
                    (None, ["RuntimeError"], True, True, False, True, True))
    check("leasehb/field-tmp-cleanup-unprintable-fault-disclosed",
          (r14_field("r14fieldfmt", None, R14Unprintable(ValueError), ""),
           r14_field("r14fieldfmt1", r14_body("field"), R14Unprintable(ValueError), "")),
          r14_fmt_want)
    check("leasehb/save-tmp-cleanup-unprintable-fault-disclosed",
          (r14_memory("r14memfmt", None, R14Unprintable(MemoryError), ""),
           r14_memory("r14memfmt1", r14_body("memory"), R14Unprintable(MemoryError), "")),
          r14_fmt_want)
    check("leasehb/field-tmp-cleanup-stop-ahead-of-first-fault",
          (r14_field("r14fieldstop", r14_body("field"),
                     KeyboardInterrupt("injected field cleanup stop"),
                     " (KeyboardInterrupt: injected field cleanup stop)"),
           r14_field("r14fieldexit", r14_body("field"), SystemExit("injected field cleanup exit"),
                     " (SystemExit: injected field cleanup exit)")),
          (r14_stopped("KeyboardInterrupt: injected field cleanup stop"),
           r14_stopped("SystemExit: injected field cleanup exit")))
    check("leasehb/save-tmp-cleanup-stop-ahead-of-first-fault",
          (r14_memory("r14memstop", r14_body("memory"),
                      KeyboardInterrupt("injected memory cleanup stop"),
                      " (KeyboardInterrupt: injected memory cleanup stop)"),
           r14_memory("r14memexit", r14_body("memory"),
                      SystemExit("injected memory cleanup exit"),
                      " (SystemExit: injected memory cleanup exit)")),
          (r14_stopped("KeyboardInterrupt: injected memory cleanup stop"),
           r14_stopped("SystemExit: injected memory cleanup exit")))
    check("leasehb/field-tmp-cleanup-fault-behind-first-stop",
          (r14_field("r14fieldbehind", KeyboardInterrupt("injected field body stop"),
                     RuntimeError("injected field cleanup stat interrupt"),
                     " (RuntimeError: injected field cleanup stat interrupt)"),
           r14_field("r14fieldbehind1", KeyboardInterrupt("injected field body stop"),
                     SystemExit("injected field cleanup exit"),
                     " (SystemExit: injected field cleanup exit)")),
          (r14_stopped("KeyboardInterrupt: injected field body stop"),
           r14_stopped("KeyboardInterrupt: injected field body stop")))
    check("leasehb/save-tmp-cleanup-fault-behind-first-stop",
          (r14_memory("r14membehind", KeyboardInterrupt("injected memory body stop"),
                      RuntimeError("injected memory cleanup stat interrupt"),
                      " (RuntimeError: injected memory cleanup stat interrupt)"),
           r14_memory("r14membehind1", KeyboardInterrupt("injected memory body stop"),
                      SystemExit("injected memory cleanup exit"),
                      " (SystemExit: injected memory cleanup exit)")),
          (r14_stopped("KeyboardInterrupt: injected memory body stop"),
           r14_stopped("KeyboardInterrupt: injected memory body stop")))

    # (13z) a STOP raised while RENDERING a cleanup fault (its str() raises an interrupt or an
    # exit) is never absorbed: the fixed disclosure naming the temporary is recorded on the
    # holder BEFORE the rendering, and the stop then propagates ahead of an ordinary first
    # fault, as a stop raised in the cleanup itself does, for the field writer and for the
    # memory save alike (red with the change removed: the rendering absorbed the stop and the
    # crash recorder reported the first fault; red with only the record moved back after the
    # rendering: the stop escaped with nothing disclosed). Behind a first stop the first stop
    # is kept (red when a rendering stop always propagates). A cleanup stop that is neither an
    # interrupt nor an exit (a direct BaseException subclass) also propagates ahead of an
    # ordinary first fault (red with ONLY the stop-ahead rule removed, which the
    # behind-first-stop vectors above survive, and red when a stop is told by a list of
    # classes rather than by not being an Exception)
    class R15StopText(RuntimeError):
        # an ordinary cleanup fault whose str() raises the STOP it carries
        def __init__(self, stop15):
            RuntimeError.__init__(self, "injected stop-text cleanup fault")
            self.stop15 = stop15

        def __str__(self):
            raise self.stop15

    class R15Stop(BaseException):
        # a STOP that is neither an interrupt nor an exit: not an Exception
        pass
    check("leasehb/field-tmp-cleanup-render-stop-ahead-of-first-fault",
          (r14_field("r15fieldrender", r14_body("field"),
                     R15StopText(KeyboardInterrupt("injected field render stop")), ""),
           r14_field("r15fieldrenderexit", r14_body("field"),
                     R15StopText(SystemExit("injected field render exit")), "")),
          (r14_stopped("KeyboardInterrupt: injected field render stop"),
           r14_stopped("SystemExit: injected field render exit")))
    check("leasehb/save-tmp-cleanup-render-stop-ahead-of-first-fault",
          (r14_memory("r15memrender", r14_body("memory"),
                      R15StopText(KeyboardInterrupt("injected memory render stop")), ""),
           r14_memory("r15memrenderexit", r14_body("memory"),
                      R15StopText(SystemExit("injected memory render exit")), "")),
          (r14_stopped("KeyboardInterrupt: injected memory render stop"),
           r14_stopped("SystemExit: injected memory render exit")))
    check("leasehb/tmp-cleanup-render-stop-behind-first-stop",
          (r14_field("r15fieldrenderbehind", KeyboardInterrupt("injected field body stop"),
                     R15StopText(SystemExit("injected field render exit")), ""),
           r14_memory("r15memrenderbehind", KeyboardInterrupt("injected memory body stop"),
                      R15StopText(SystemExit("injected memory render exit")), "")),
          (r14_stopped("KeyboardInterrupt: injected field body stop"),
           r14_stopped("KeyboardInterrupt: injected memory body stop")))
    check("leasehb/tmp-cleanup-other-stop-ahead-of-first-fault",
          (r14_field("r15fieldother", r14_body("field"), R15Stop("injected field cleanup stop"),
                     " (R15Stop: injected field cleanup stop)"),
           r14_memory("r15memother", r14_body("memory"), R15Stop("injected memory cleanup stop"),
                      " (R15Stop: injected memory cleanup stop)")),
          (r14_stopped("R15Stop: injected field cleanup stop"),
           r14_stopped("R15Stop: injected memory cleanup stop")))
    # an injected fault whose str() raises its stop is reported by name, never aborting the
    # suite (red with the guard removed from r14_text: the stop escaped and aborted the run)
    check("leasehb/escaped-stop-text-fault-named",
          (r14_text(R15StopText(KeyboardInterrupt("injected escaped render stop"))),
           r14_text(RuntimeError("injected escaped fault"))),
          ("R15StopText: <its text raised KeyboardInterrupt>",
           "RuntimeError: injected escaped fault"))
    # and any OTHER fault raised while formatting an escaped fault is re-raised, never named,
    # told from the injected stop by identity: an ordinary fault, and a distinct stop of the
    # very class the escaped fault carries (red with the re-raise removed from r14_text: both
    # were named; red when the injected stop is told by its class: the distinct stop was named)
    class R17OtherText(RuntimeError):
        # an injected fault carrying the stop stop15, whose str() raises a DIFFERENT fault
        def __init__(self, stop15, other17):
            RuntimeError.__init__(self, "injected other-text fault")
            self.stop15 = stop15
            self.other17 = other17

        def __str__(self):
            raise self.other17

    def r17_text(stop17, other17):
        # ("named", the text) when r14_text names the fault, ("re-raised", the class) when
        # exactly the fault its str() raised escapes; anything else propagates
        try:
            return ("named", r14_text(R17OtherText(stop17, other17)))
        except BaseException as exc17:
            if exc17 is not other17:
                raise
            return ("re-raised", type(exc17).__name__)
    check("leasehb/escaped-other-text-fault-re-raised",
          (r17_text(KeyboardInterrupt("injected carried stop"),
                    ValueError("injected other text fault")),
           r17_text(KeyboardInterrupt("injected carried stop"),
                    KeyboardInterrupt("injected other stop"))),
          (("re-raised", "ValueError"), ("re-raised", "KeyboardInterrupt")))

    # (13aa) the rendered cleanup disclosure replaces EXACTLY the record made before the
    # rendering, bound by that record's index and identity, never by its value, each leg
    # asserting the holder's WHOLE withheld list, for the field writer and the memory save
    # alike: a cleanup fault whose str() appends a record EQUAL in value to the fixed one (a
    # distinct object) leaves the fixed record and the appended one as they are (red with the
    # value comparison back: the appended record was overwritten by the rendered one; red when
    # only the slot's identity is compared: the fixed record was overwritten), one whose str()
    # swaps an equal copy into the fixed record's slot leaves that copy (red when only the
    # length is compared), and a fault with no side effect leaves ONE record, the rendered one
    # (red when the rendered detail is appended rather than stored in place)
    class R16SideText(RuntimeError):
        # an ordinary cleanup fault whose FIRST str() changes the event holder's withheld list,
        # then renders: "append" adds a copy EQUAL in value to its last record (a distinct
        # object), "swap" puts that copy in the last record's slot, "none" changes nothing
        def __init__(self, made16, how16):
            RuntimeError.__init__(self, "injected side-effect cleanup fault")
            self.made16 = made16
            self.how16 = how16
            self.copied16 = None

        def __str__(self):
            if self.copied16 is None and self.how16 != "none":
                w16 = self.made16[-1]["withheld"]
                copy16 = "".join(list(w16[-1]))
                # equal in value and a distinct object, or the leg proves nothing
                self.copied16 = copy16 == w16[-1] and copy16 is not w16[-1]
                if self.how16 == "append":
                    w16.append(copy16)
                else:
                    w16[-1] = copy16
            return "injected side-effect cleanup fault"

    def r16_run(kind16, name16, body16, cleanup16, made16, seed16=None, patches16=(),
                stops16=()):
        # one r14 run whose event holders are captured in made16 (seed16, when given, builds
        # each holder's withheld list first), with further (owner, attribute, replacement)
        # patches16; stops16 are injected stops a run may let escape (reported by name).
        # Returns (the escaped fault, the crash classes, EVERY holder's whole withheld list)
        real16 = aiqt_hooks._orch_lease_hb_holder

        def holder16():
            made_one16 = real16()
            if seed16 is not None:
                made_one16["withheld"] = seed16()
            made16.append(made_one16)
            return made_one16
        saved16 = [(owner16, attr16, getattr(owner16, attr16))
                   for owner16, attr16, _repl16 in patches16]
        try:
            aiqt_hooks._orch_lease_hb_holder = holder16
            for owner16, attr16, repl16 in patches16:
                setattr(owner16, attr16, repl16)
            try:
                got16 = (r14_field if kind16 == "field" else r14_memory)(
                    name16, body16, cleanup16, "")[:2]
            except BaseException as exc16:
                if not any(exc16 is stop16 for stop16 in stops16):
                    raise  # not an injected stop (a real interrupt): never swallowed here
                got16 = (r14_text(exc16), None)
        finally:
            for owner16, attr16, orig16 in reversed(saved16):
                setattr(owner16, attr16, orig16)
            aiqt_hooks._orch_lease_hb_holder = real16
        return got16 + ([list(made_one16.get("withheld") or []) for made_one16 in made16],)

    def r16_tmp(name16):
        return ".forced.{}.hb.tmp".format(name16)

    def r16_named(kind16):
        # the NAMED write failure r14_run injects when no body fault is given
        return "the {}temporary file cannot be written ([Errno {}] injected {} write fault)".format(
            "memory " if kind16 == "memory" else "", errno11.ENOSPC, kind16)

    def r16_lead(kind16, failure16):
        # the lead of the cleanup record: the named failure, else the interruption
        if kind16 == "field":
            return "the heartbeat field write was interrupted; " + (
                "{}; ".format(failure16) if failure16 is not None else "")
        return "{}; ".format(failure16) if failure16 is not None \
            else "the memory save was interrupted; "

    def r16_fixed(name16):
        return "its temporary file cleanup was interrupted and the file may remain ({})".format(
            r16_tmp(name16))

    def r16_rendered(name16, fault16):
        return ("its temporary file cleanup was interrupted ({}) and the file may remain "
                "({})".format(fault16, r16_tmp(name16)))

    def r16_side(kind16, how16):
        name16 = "r16{}{}".format(kind16, how16)
        made16 = []
        fault16 = R16SideText(made16, how16)
        return r16_run(kind16, name16, None, fault16, made16) + (fault16.copied16,)

    def r16_side_want(kind16, how16):
        name16 = "r16{}{}".format(kind16, how16)
        lead16 = r16_lead(kind16, r16_named(kind16))
        crash16 = "lease-heartbeat core fault: R16SideText: injected side-effect cleanup fault"
        if how16 == "append":
            held16 = [lead16 + r16_fixed(name16), lead16 + r16_fixed(name16), crash16]
        elif how16 == "swap":
            held16 = [lead16 + r16_fixed(name16), crash16]
        else:
            held16 = [lead16 + r16_rendered(
                name16, "R16SideText: injected side-effect cleanup fault"), crash16]
        return (None, ["R16SideText"], [held16], None if how16 == "none" else True)
    check("leasehb/field-tmp-cleanup-render-replaces-only-its-record",
          tuple(r16_side("field", how16) for how16 in ("append", "swap", "none")),
          tuple(r16_side_want("field", how16) for how16 in ("append", "swap", "none")))
    check("leasehb/save-tmp-cleanup-render-replaces-only-its-record",
          tuple(r16_side("memory", how16) for how16 in ("append", "swap", "none")),
          tuple(r16_side_want("memory", how16) for how16 in ("append", "swap", "none")))

    # (13ab) the failure cleanup formats nothing outside a guard before it records a temporary
    # that may remain, and a fault raised while that record is built or recorded never
    # displaces a stop already propagating, for the field writer and the memory save alike: the
    # fixed disclosure is formatted before the temporary is created (red with it formatted in
    # the cleanup again: an exhaustion there escaped, displacing the first stop with nothing
    # recorded); a record that cannot be built leaves the preformatted fallback naming the
    # temporary (red with the build unguarded: the exhaustion escaped the same way); a stop
    # raised while recording, or at the rendered detail's store, is ordered as a stop raised in
    # the cleanup, so the first stop still propagates (red with either guard removed: that stop
    # escaped in its place), and each such leg asserts that its injected stop FIRED (red with
    # the recording or the store skipped: the first stop propagated just the same, so only the
    # spent stop tells them apart); a stop raised while the record itself is appended leaves
    # the holder's list EMPTY, the DOCUMENTED residual (_orch_lease_hb_cleanup_record),
    # asserted as such; behind an ORDINARY first fault (a RuntimeError at the body) a stop
    # raised while recording or at the store propagates AHEAD of it, for both callers, each leg
    # asserting the escaping stop and the whole withheld list (red with that site's stop rule
    # removed, or with a caller's stop-ahead rule removed: the stop was absorbed and the crash
    # recorder reported the first fault, which the behind-first-stop legs cannot tell); and the
    # field writer's no-write detail that cannot be built is the preformatted fallback (red with
    # that build unguarded: the exhaustion became a core fault and the temporary went unnamed)
    class R16Unjoinable(str):
        # a fixed disclosure whose concatenation exhausts memory: building the record faults
        def __radd__(self, other16):
            raise MemoryError("injected record exhaustion")

    class R16Unformattable(str):
        # a cleanup disclosure whose formatting exhausts memory
        def __format__(self, spec16):
            raise MemoryError("injected no-write detail exhaustion")

    class R16StopList(list):
        # a withheld list whose FIRST append of an entry naming the temporary raises the stop
        # it carries, as a signal delivered while recording would
        def __init__(self, tmp16, stop16):
            list.__init__(self)
            self.tmp16 = tmp16
            self.stop16 = stop16

        def append(self, item16):
            if self.stop16 is not None and self.tmp16 in item16:
                stop16, self.stop16 = self.stop16, None
                raise stop16
            list.append(self, item16)

    class R16StoreStopList(list):
        # a withheld list whose FIRST item store raises the stop it carries, as a signal
        # delivered at the rendered detail's store would
        def __init__(self, stop16):
            list.__init__(self)
            self.stop16 = stop16

        def __setitem__(self, index16, item16):
            if self.stop16 is not None:
                stop16, self.stop16 = self.stop16, None
                raise stop16
            list.__setitem__(self, index16, item16)

    def r16_behind_name(kind16, how16, first16):
        return "r16{}{}{}".format(kind16, how16, "" if first16 == "stop" else "1")

    def r16_behind(kind16, how16, first16="stop"):
        # a first fault propagates when the cleanup's stat raises an ordinary fault: a STOP, or
        # with first16 "fault" an ORDINARY one (a RuntimeError); how16 names what else faults
        # on the cleanup path. Returns the r16_run result and, for each withheld list seeded
        # with an injected stop, whether that stop FIRED (it is spent once raised)
        name16 = r16_behind_name(kind16, how16, first16)
        made16 = []
        seeded16 = []
        cleanup16 = RuntimeError("injected {} cleanup stat interrupt".format(kind16))
        stop16 = SystemExit("injected {} {} stop".format(kind16, how16))
        real_fixed16 = aiqt_hooks._orch_lease_hb_cleanup_fault_fixed
        seed16 = None
        patches16 = []
        if how16 == "late":
            def fixed16(tmp_arg):
                # exhausted once the cleanup fault has been raised
                if cleanup16.__traceback__ is not None:
                    raise MemoryError("injected fixed-disclosure exhaustion")
                return real_fixed16(tmp_arg)
            patches16 = [(aiqt_hooks, "_orch_lease_hb_cleanup_fault_fixed", fixed16)]
        elif how16 == "unbuilt":
            patches16 = [(aiqt_hooks, "_orch_lease_hb_cleanup_fault_fixed",
                          lambda tmp_arg: R16Unjoinable(real_fixed16(tmp_arg)))]
        elif how16 == "record":
            def seed16():
                seeded16.append(R16StopList(r16_tmp(name16), stop16))
                return seeded16[-1]
        else:
            def seed16():
                seeded16.append(R16StoreStopList(stop16))
                return seeded16[-1]
        body16 = KeyboardInterrupt("injected {} body stop".format(kind16)) \
            if first16 == "stop" else r14_body(kind16)
        return r16_run(kind16, name16, body16, cleanup16, made16, seed16=seed16,
                       patches16=patches16, stops16=(stop16,)) \
            + ([seeded_one16.stop16 is None for seeded_one16 in seeded16],)

    # the DOCUMENTED residual (_orch_lease_hb_cleanup_record, ORCHESTRATION.md and the manifest
    # residue): a stop raised while the record itself is appended propagates, ordered as a stop
    # raised in the cleanup, WITHOUT the disclosure, since no fallback can be recorded once that
    # append has stopped; the holder's withheld list after it is EMPTY, asserted deliberately
    r16_append_stop_residual = []

    def r16_behind_want(kind16, how16, first16="stop"):
        name16 = r16_behind_name(kind16, how16, first16)
        lead16 = r16_lead(kind16, None)
        fired16 = []
        if how16 == "late":
            held16 = [lead16 + r16_rendered(
                name16, "RuntimeError: injected {} cleanup stat interrupt".format(kind16))]
        elif how16 == "unbuilt":
            held16 = ["{} failed and its temporary file may remain ({})".format(
                "the heartbeat field write" if kind16 == "field" else "the memory save",
                r16_tmp(name16))]
        elif how16 == "record":
            held16 = list(r16_append_stop_residual)
            fired16 = [True]
        else:
            held16 = [lead16 + r16_fixed(name16)]
            fired16 = [True]
        if first16 == "stop":
            return ("KeyboardInterrupt: injected {} body stop".format(kind16), [], [held16],
                    fired16)
        # behind an ORDINARY first fault the injected stop escapes in its place, past the crash
        # recorder (which catches Exception), so no crash class is captured
        return ("SystemExit: injected {} {} stop".format(kind16, how16), None, [held16], fired16)
    for how16, id16 in (("late", "leasehb/tmp-cleanup-fixed-disclosure-preformatted"),
                        ("unbuilt", "leasehb/tmp-cleanup-unbuilt-record-falls-back"),
                        ("record", "leasehb/tmp-cleanup-record-stop-behind-first-stop"),
                        ("store", "leasehb/tmp-cleanup-store-stop-behind-first-stop")):
        check(id16, (r16_behind("field", how16), r16_behind("memory", how16)),
              (r16_behind_want("field", how16), r16_behind_want("memory", how16)))
    for how16, id16 in (("record", "leasehb/tmp-cleanup-record-stop-ahead-of-first-fault"),
                        ("store", "leasehb/tmp-cleanup-store-stop-ahead-of-first-fault")):
        check(id16, (r16_behind("field", how16, "fault"), r16_behind("memory", how16, "fault")),
              (r16_behind_want("field", how16, "fault"),
               r16_behind_want("memory", how16, "fault")))

    def r16_unformatted():
        made16 = []
        written16 = []
        real_cleanup16 = aiqt_hooks._orch_lease_hb_save_cleanup
        real_write16 = aiqt_hooks._orch_lease_hb_write_field

        def cleanup16(*a16):
            detail16 = real_cleanup16(*a16)
            return None if detail16 is None else R16Unformattable(detail16)

        def write16(*a16, **k16):
            try:
                written16.append(real_write16(*a16, **k16))
            except Exception as exc16:
                written16.append(type(exc16).__name__)
                raise
            return written16[-1]
        return r16_run("field", "r16fieldunformatted", None,
                       OSError(errno11.EACCES, "injected field cleanup stat fault"), made16,
                       patches16=[(aiqt_hooks, "_orch_lease_hb_save_cleanup", cleanup16),
                                  (aiqt_hooks, "_orch_lease_hb_write_field", write16)]) \
            + (written16,)
    check("leasehb/field-tmp-cleanup-unbuilt-detail-falls-back", r16_unformatted(),
          (None, [], [[]], [("no-write", "the heartbeat field write failed and its temporary "
                             "file may remain ({})".format(r16_tmp("r16fieldunformatted")))]))

    # a STOP raised while that no-write detail is built is never absorbed: it propagates, the
    # detail is never returned and nothing is recorded, the DOCUMENTED residual
    # (_orch_lease_hb_write_field), asserted as such (red when that build absorbs the stop: the
    # stop was swallowed and the fallback refusal returned)
    class R17StopFormat(str):
        # a cleanup disclosure whose FIRST formatting raises the stop it carries
        def __new__(cls, text17, stop17):
            made17 = str.__new__(cls, text17)
            made17.stop17 = stop17
            return made17

        def __format__(self, spec17):
            if self.stop17 is not None:
                stop17, self.stop17 = self.stop17, None
                raise stop17
            return str.__format__(self, spec17)

    def r17_detail_stop():
        made17 = []
        written17 = []
        stop17 = SystemExit("injected field detail stop")
        real_cleanup17 = aiqt_hooks._orch_lease_hb_save_cleanup
        real_write17 = aiqt_hooks._orch_lease_hb_write_field

        def cleanup17(*a17):
            detail17 = real_cleanup17(*a17)
            return None if detail17 is None else R17StopFormat(detail17, stop17)

        def write17(*a17, **k17):
            written17.append(real_write17(*a17, **k17))
            return written17[-1]
        return r16_run("field", "r17fielddetailstop", None,
                       OSError(errno11.EACCES, "injected field cleanup stat fault"), made17,
                       patches16=[(aiqt_hooks, "_orch_lease_hb_save_cleanup", cleanup17),
                                  (aiqt_hooks, "_orch_lease_hb_write_field", write17)],
                       stops16=(stop17,)) + (written17,)
    check("leasehb/field-tmp-cleanup-detail-stop-residual", r17_detail_stop(),
          ("SystemExit: injected field detail stop", None, [[]], []))


def _leasehb_write_integrity_cases(tmp):
    """Component: the lease-heartbeat write integrity vectors. (1) The failure cleanup unlinks by
    NAME after one identity stat (POSIX offers no unlink-if-inode): an entry swapped onto the
    temporary name between that stat and the unlink is removed in its place and the moved
    temporary stays, undisclosed, the documented same-user concurrency window, witnessed for the
    field write and the memory save through the cleanup's unlink seam. (2) A field write refuses
    a lease carrying a setuid, setgid or sticky bit and carries every other permission bit over
    exactly. (3) A failed directory fsync after a verified replace is the distinct
    written-not-durable warning on Stop, never plain success, and the directory fsync is made on
    the lease's parent after the replace and before the verification. (4) A json field write
    changes only the declared field's value text: a huge number, a long decimal and non-ASCII
    text in other members stay byte-identical, and a verification divergence refuses. (5) The
    holder proof is exact equality in refresh mode: a holder longer than the session and a
    session longer than the holder both write nothing. (6) The lines reader splits on line feeds
    only, so a Unicode or control separator inside a value never yields a phantom holder line."""
    base = tmp / "leasehbwi"
    base.mkdir()
    errno_wi = __import__("errno")

    def hb(**overrides):
        block = {"mode": "refresh", "format": "lines", "holder_field": "holder",
                 "refresh_after_minutes": 10, "warn_after_minutes": 30}
        block.update(overrides)
        return block

    def stop(f, sid="s1"):
        payload = f.payload("Stop")
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_stop(payload)

    def text_of(result):
        obj = result[1]
        return obj.get("systemMessage", "") if isinstance(obj, dict) else ""

    def snap(path):
        st = os.lstat(path)
        return (path.read_bytes(), st.st_mtime_ns)

    def fixture(name, lease_text, block, age_secs=7200, primed=True):
        f = Fixture(base, name)
        f.lease.write_bytes(lease_text.encode("utf-8"))
        aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - age_secs
        os.utime(f.lease, times=(aged, aged))
        reg_path = f.root / ".aiqt" / "orchestration.local.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["lease"] = {"path": str(f.lease), "max_age_hours": 24,
                        "holder_is_session_id": True, "heartbeat": block}
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        if primed:
            # the past-holder memory already names s1, so no holder-change save runs
            sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "lease-heartbeat.json").write_text(
                json.dumps({"version": 1, "session_id": "s1", "ts": now_iso(1)}),
                encoding="utf-8")
        return f

    def rows(f):
        path = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root))) / "guard-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def patched(patches, fn):
        # patches: (owner, attribute, replacement) triples, each restored afterwards (an
        # attribute the owner lacked is removed again)
        saved = [(owner, attr, getattr(owner, attr, None)) for owner, attr, _r in patches]
        try:
            for owner, attr, repl in patches:
                setattr(owner, attr, repl)
            return fn()
        finally:
            for owner, attr, orig in reversed(saved):
                if orig is None:
                    delattr(owner, attr)
                else:
                    setattr(owner, attr, orig)

    def is_entry(fd, path):
        try:
            a, b = os.fstat(fd), os.lstat(str(path))
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)

    def is_dir_of(fd, ident):
        st_fd = os.fstat(fd)
        return stat.S_ISDIR(st_fd.st_mode) and (st_fd.st_dev, st_fd.st_ino) == ident

    def tmp_names(directory):
        return sorted(n for n in os.listdir(str(directory)) if n.endswith(".hb.tmp"))

    real_fsync = os.fsync
    real_cleanup_unlink = getattr(aiqt_hooks, "_orch_lease_hb_cleanup_unlink", None)

    def swap_seam(plant, aside, fired):
        # another process of the same user, between the cleanup's identity stat and its
        # unlink: it moves this write's temporary aside and renames its own entry onto the name
        def seam(tmp_arg, dirfd):
            if not fired:
                fired.append(tmp_arg)
                os.rename(tmp_arg, aside, src_dir_fd=dirfd, dst_dir_fd=dirfd)
                os.rename(plant, tmp_arg, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            return real_cleanup_unlink(tmp_arg, dirfd)
        return seam

    # --- (1) the cleanup's by-name unlink after its identity stat: an entry swapped onto the
    # temporary name in between is removed in its place and the moved temporary stays,
    # undisclosed (the documented same-user concurrency window; red where the cleanup does not
    # unlink through the seam, so no swap can be planted there) ---
    sw = fixture("swapfield", "holder: s1\nheartbeat_utc: " + now_iso(2) + "\n",
                 hb(field="heartbeat_utc"))
    sw_tmp = sw.root / ".forced.swapfield.hb.tmp"
    (sw.root / "planted.txt").write_bytes(b"planted\n")
    sw_before = snap(sw.lease)
    sw_fired = []

    def sw_fsync(fd):
        if is_entry(fd, sw_tmp):
            raise OSError(errno_wi.ENOSPC, "injected field write fault")
        return real_fsync(fd)
    result = patched([(aiqt_hooks, "_orch_lease_hb_tmp_name", lambda name_arg: sw_tmp.name),
                      (os, "fsync", sw_fsync),
                      (aiqt_hooks, "_orch_lease_hb_cleanup_unlink",
                       swap_seam("planted.txt", "moved-aside.tmp", sw_fired))],
                     lambda: stop(sw))
    check("leasehb/field-tmp-cleanup-swap-after-stat-residual",
          (sw_fired, os.path.lexists(str(sw_tmp)), (sw.root / "planted.txt").exists(),
           (sw.root / "moved-aside.tmp").exists(),
           "injected field write fault" in text_of(result), "may remain" in text_of(result),
           snap(sw.lease) == sw_before),
          ([sw_tmp.name], False, False, True, True, False, True))

    ms = fixture("swapsave", "holder: s1\n", hb(mode="warn"), primed=False)
    ms_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(ms.root)))
    ms_sd.mkdir(parents=True, exist_ok=True)
    ms_tmp = ms_sd / ".forced.swapsave.hb.tmp"
    (ms_sd / "planted.txt").write_bytes(b"planted\n")
    ms_fired = []

    def ms_replace(tmp_arg, name_arg, dirfd):
        raise PermissionError(errno_wi.EACCES, "injected memory replace fault")
    result = patched([(aiqt_hooks, "_orch_lease_hb_tmp_name", lambda name_arg: ms_tmp.name),
                      (aiqt_hooks, "_orch_lease_hb_save_replace", ms_replace),
                      (aiqt_hooks, "_orch_lease_hb_cleanup_unlink",
                       swap_seam("planted.txt", "moved-aside.tmp", ms_fired))],
                     lambda: stop(ms))
    check("leasehb/save-tmp-cleanup-swap-after-stat-residual",
          (ms_fired, os.path.lexists(str(ms_tmp)), (ms_sd / "planted.txt").exists(),
           (ms_sd / "moved-aside.tmp").exists(),
           "injected memory replace fault" in text_of(result), "may remain" in text_of(result),
           (ms_sd / "lease-heartbeat.json").exists()),
          ([ms_tmp.name], False, False, True, True, False, False))

    # --- (2) a lease carrying a setuid, setgid or sticky bit is refused before any temporary
    # exists, and every other permission bit is carried over exactly (red on a writer that
    # masks the mode with 0o777: the special bit was silently dropped) ---
    sg = fixture("setgid", "holder: s1\nheartbeat_utc: " + now_iso(2) + "\n",
                 hb(field="heartbeat_utc"))
    os.chmod(str(sg.lease), 0o2640)
    sg_mode = stat.S_IMODE(os.lstat(str(sg.lease)).st_mode)
    sg_before = snap(sg.lease)
    result = stop(sg)
    check("leasehb/field-setgid-lease-refused",
          (sg_mode, _verdict(result),
           "setuid, setgid or sticky bit (mode 2640)" in text_of(result),
           snap(sg.lease) == sg_before, stat.S_IMODE(os.lstat(str(sg.lease)).st_mode),
           tmp_names(sg.root)),
          (0o2640, "warn", True, True, 0o2640, []))
    modes = {}
    for tag, mode in (("setuid", 0o4640), ("sticky", 0o1640), ("plain", 0o604)):
        fm = fixture("mode" + tag, "holder: s1\nheartbeat_utc: " + now_iso(2) + "\n",
                     hb(field="heartbeat_utc"))
        os.chmod(str(fm.lease), mode)
        set_mode = stat.S_IMODE(os.lstat(str(fm.lease)).st_mode)
        fm_before = fm.lease.read_bytes()
        result = stop(fm)
        modes[tag] = (set_mode, fm.lease.read_bytes() != fm_before,
                      stat.S_IMODE(os.lstat(str(fm.lease)).st_mode),
                      "sticky bit (mode" in text_of(result))
    check("leasehb/field-setuid-sticky-refused-plain-mode-kept", modes,
          {"setuid": (0o4640, False, 0o4640, True), "sticky": (0o1640, False, 0o1640, True),
           "plain": (0o604, True, 0o604, False)})

    # --- (3) a failed directory fsync after a verified replace is the written-not-durable
    # warning, never plain success (red where the fault is swallowed), and the directory fsync
    # is made on the lease's parent after the replace and before the verification (red on a
    # writer with the call removed) ---
    df = fixture("dirfsynceio", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    df_st = os.stat(str(df.root))
    df_before = df.lease.read_bytes()

    def df_fsync(fd):
        if is_dir_of(fd, (df_st.st_dev, df_st.st_ino)):
            raise OSError(errno_wi.EIO, "injected directory fsync fault")
        return real_fsync(fd)
    result = patched([(os, "fsync", df_fsync)], lambda: stop(df))
    check("leasehb/field-dir-fsync-eio-not-durable",
          (_verdict(result), "not durable" in text_of(result),
           "injected directory fsync fault" in text_of(result),
           df.lease.read_bytes() != df_before,
           [r.get("decision") for r in rows(df) if r.get("kind") == "lease-heartbeat"]),
          ("warn", True, True, True, ["written-not-durable"]))
    dc = fixture("dirfsynccall", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    dc_st = os.stat(str(dc.root))
    dc_before = dc.lease.read_bytes()
    dc_order = []
    real_replace = aiqt_hooks._orch_lease_hb_replace
    real_verify = aiqt_hooks._orch_lease_hb_verify

    def dc_fsync(fd):
        if is_dir_of(fd, (dc_st.st_dev, dc_st.st_ino)):
            dc_order.append("dir-fsync")
        return real_fsync(fd)

    def dc_replace(*a):
        dc_order.append("replace")
        return real_replace(*a)

    def dc_verify(*a):
        dc_order.append("verify")
        return real_verify(*a)
    result = patched([(os, "fsync", dc_fsync),
                      (aiqt_hooks, "_orch_lease_hb_replace", dc_replace),
                      (aiqt_hooks, "_orch_lease_hb_verify", dc_verify)], lambda: stop(dc))
    check("leasehb/field-dir-fsync-called-after-replace",
          (_verdict(result), dc_order, dc.lease.read_bytes() != dc_before),
          ("allow", ["replace", "dir-fsync", "verify"], True))

    # --- (4) a json field write rewrites ONLY the declared field's value text: every other
    # member stays byte-identical (red on a re-serializing writer: 1e400 became Infinity,
    # 0.10000000000000000555 became 0.1, non-ASCII text became escapes) ---
    def json_case(name, member_text):
        old_hb = now_iso(2)
        text = '{"holder": "s1", "heartbeat_utc": "' + old_hb + '", ' + member_text + '}\n'
        fj = fixture(name, text, hb(format="json", field="heartbeat_utc"))
        stop(fj)
        new_text = fj.lease.read_bytes().decode("utf-8")
        try:
            new_hb = json.loads(new_text)["heartbeat_utc"]
        except (ValueError, KeyError, TypeError):
            return ("unparsed", new_text)
        return (new_hb != old_hb, new_text == text.replace(old_hb, new_hb),
                member_text in new_text)
    check("leasehb/json-huge-number-kept", json_case("jsonhuge", '"big": 1e400'),
          (True, True, True))
    check("leasehb/json-long-decimal-kept",
          json_case("jsondecimal", '"dec": 0.10000000000000000555'), (True, True, True))
    check("leasehb/json-non-ascii-kept",
          json_case("jsonnonascii", '"name": "café 日本"'), (True, True, True))
    dv_text = '{"holder": "s1", "heartbeat_utc": "2020-01-01T00:00:00Z", "n": 1}'
    dv_cfg = {"format": "json", "field": "heartbeat_utc", "holder_field": "holder"}
    dv_status, dv_info = aiqt_hooks._orch_lease_hb_parse(dv_text, dv_cfg)
    # a verification divergence (the parsed member read as True where the text says 1) is
    # refused under exact comparison, never written
    dv_info["json"] = dict(dv_info["json"], n=True)
    check("leasehb/json-render-verify-refuses-divergence",
          (dv_status, aiqt_hooks._orch_lease_hb_render(dv_text, dv_cfg, dv_info,
                                                       "2026-01-01T00:00:00Z")),
          ("ok", ("cannot",
                  "the json lease cannot be rewritten with every other member unchanged")))

    # --- (5) the holder proof is EXACT equality in refresh mode (red on a prefix or
    # substring comparison, which every s1/s2 vector passes) ---
    for cid, name, holder, sid in (
            ("leasehb/holder-longer-than-session-no-write", "holderlonger", "s1x", "s1"),
            ("leasehb/session-longer-than-holder-no-write", "sessionlonger", "s1", "s1x")):
        fx = fixture(name, "holder: " + holder + "\n", hb(), primed=False)
        fx_before = snap(fx.lease)
        result = stop(fx, sid=sid)
        check(cid, (_verdict(result), snap(fx.lease) == fx_before), ("allow", True))

    # --- (6) a separator str.splitlines breaks on (U+2028, U+0085, a form feed) stays inside
    # its line, so it never yields a phantom holder line (red on a splitlines reader: the
    # phantom proved the holder and the lease was touched) ---
    for cid, name, sep in (("leasehb/lines-u2028-no-phantom-holder", "sepu2028", "\u2028"),
                           ("leasehb/lines-nel-no-phantom-holder", "sepnel", "\x85"),
                           ("leasehb/lines-formfeed-no-phantom-holder", "sepff", "\x0c")):
        fs = fixture(name, "note: x" + sep + "holder: s1\n", hb(), primed=False)
        fs_before = snap(fs.lease)
        result = stop(fs)
        check(cid, (_verdict(result), "appears 0 times" in text_of(result),
                    snap(fs.lease) == fs_before), ("warn", True, True))


# The child of the binding exit-status vectors: it loads the hook script from the directory in
# argv[1], makes the core's lease read raise SystemExit(int(argv[3])) after a marker proving the
# injection fired, and runs the REAL dispatcher entry for the binding named in argv[2], so the
# returncode is the status the hook PROCESS really ends with.
_LEASEHB_EXIT_CHILD = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import aiqt_hooks\n"
    "def stop(*_a, **_k):\n"
    "    sys.stderr.write('injected core stop fired\\n')\n"
    "    sys.stderr.flush()\n"
    "    raise SystemExit(int(sys.argv[3]))\n"
    "aiqt_hooks._orch_lease_hb_load = stop\n"
    "os._exit(aiqt_hooks.main([sys.argv[2]]))\n")


def _leasehb_status_and_parse_cases(tmp):
    """Component: the lease-heartbeat binding status, surfacing, group and parse vectors. (1)
    Neither binding ever exits 2: a SystemExit raised inside the core whose status would be 2
    (code 2, or 258, whose low byte is 2) ends the hook PROCESS with status 1, while another
    stop keeps its own status, run through the real dispatcher in a child process. (2) A
    written-not-durable refresh on PostToolUse is surfaced as the result's error text, never as
    plain success, with an attempted guard-events row; when that row's append fails too, the
    withheld-bookkeeping report rides the same text and no row lands; a written-unverified
    refresh (the replace happened, its verification failed) on PostToolUse is surfaced the same
    way, and when its row append fails too, both texts ride the error text and no row lands. (3)
    A field write passes the lease's proven group to the group change (a proof gid injected
    distinct from the new-file, real, effective, supplementary, directory and lease gids, so a
    wrong gid is red on a single-group host too), gives the temporary that group, then the
    lease's mode, then its first byte (the lease in a real other group where this process can
    set one), and a failure to set the group, or the mode, refuses the write under that step's
    own name with nothing published. (4) The special-bit refusal is
    the field write's alone: the mtime touch
    refreshes a setgid lease and keeps its mode. (5) A json field write keeps nested members
    (one of them named like the declared field), escaped key spellings and whitespace variants
    byte-identical, and an escaped spelling of a declared key is a duplicate, a cannot-evaluate.
    (6) The lines reader splits on line feeds only for the remaining separators str.splitlines
    breaks on (U+2029, a vertical tab, U+001C to U+001E, a lone carriage return), a lease whose
    lines end with a lone carriage return is a cannot-evaluate, and a CRLF lease is still
    refreshed with its line endings kept."""
    base = tmp / "leasehbsp"
    base.mkdir()
    errno_sp = __import__("errno")

    def hb(**overrides):
        block = {"mode": "refresh", "format": "lines", "holder_field": "holder",
                 "refresh_after_minutes": 10, "warn_after_minutes": 30}
        block.update(overrides)
        return block

    def stop(f, sid="s1"):
        payload = f.payload("Stop")
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_stop(payload)

    def post(f, sid="s1"):
        payload = f.payload("PostToolUse", "Bash", {"command": "true"})
        payload["session_id"] = sid
        return aiqt_hooks.orch_lease_heartbeat_post(payload)

    def text_of(result):
        obj = result[1]
        return obj.get("systemMessage", "") if isinstance(obj, dict) else ""

    def snap(path):
        st = os.lstat(path)
        return (path.read_bytes(), st.st_mtime_ns)

    def fixture(name, lease_text, block, age_secs=7200, primed=True):
        f = Fixture(base, name)
        f.lease.write_bytes(lease_text.encode("utf-8"))
        aged = datetime.datetime.now(datetime.timezone.utc).timestamp() - age_secs
        os.utime(f.lease, times=(aged, aged))
        reg_path = f.root / ".aiqt" / "orchestration.local.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["lease"] = {"path": str(f.lease), "max_age_hours": 24,
                        "holder_is_session_id": True, "heartbeat": block}
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        if primed:
            # the past-holder memory already names s1, so no holder-change save runs
            sd = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root)))
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "lease-heartbeat.json").write_text(
                json.dumps({"version": 1, "session_id": "s1", "ts": now_iso(1)}),
                encoding="utf-8")
        return f

    def rows(f):
        path = Path(aiqt_hooks._orch_state_dir_for_root(str(f.root))) / "guard-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def decisions(f):
        return [r.get("decision") for r in rows(f) if r.get("kind") == "lease-heartbeat"]

    def patched(patches, fn):
        # patches: (owner, attribute, replacement) triples, each restored afterwards
        saved = [(owner, attr, getattr(owner, attr)) for owner, attr, _r in patches]
        try:
            for owner, attr, repl in patches:
                setattr(owner, attr, repl)
            return fn()
        finally:
            for owner, attr, orig in reversed(saved):
                setattr(owner, attr, orig)

    def is_entry(fd, path):
        try:
            a, b = os.fstat(fd), os.lstat(str(path))
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)

    def is_dir_of(fd, ident):
        st_fd = os.fstat(fd)
        return stat.S_ISDIR(st_fd.st_mode) and (st_fd.st_dev, st_fd.st_ino) == ident

    def tmp_names(directory):
        return sorted(n for n in os.listdir(str(directory)) if n.endswith(".hb.tmp"))

    real_fsync = os.fsync
    real_fchown = os.fchown
    real_write = os.write

    # --- (1) neither binding ever exits 2: a core stop whose status would be 2 ends the hook
    # process with status 1, and any other stop keeps its own status (red where a binding
    # catches only Exception: SystemExit(2) and SystemExit(258) both end the process with 2) ---
    hooks_dir = os.path.dirname(os.path.abspath(aiqt_hooks.__file__))
    ex = fixture("exitstatus", "holder: s1\nheartbeat_utc: " + now_iso(2) + "\n",
                 hb(field="heartbeat_utc"))

    def exit_status(handler, event, code):
        payload = ex.payload(event, "Bash", {"command": "true"}) if event == "PostToolUse" \
            else ex.payload(event)
        try:
            p = subprocess.run([sys.executable, "-I", "-B", "-c", _LEASEHB_EXIT_CHILD, hooks_dir,
                                handler, str(code)], input=json.dumps(payload), cwd=str(ex.root),
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return ("child timed out", False)
        return (p.returncode, "injected core stop fired" in p.stderr)
    for cid, handler, event in (
            ("leasehb/post-binding-never-exits-2", "orch_lease_heartbeat_post", "PostToolUse"),
            ("leasehb/stop-binding-never-exits-2", "orch_lease_heartbeat_stop", "Stop")):
        check(cid, [exit_status(handler, event, code) for code in (2, 258, 3)],
              [(1, True), (1, True), (3, True)])

    # --- (2) a written-not-durable refresh on PostToolUse is the result's error text, never
    # plain success (red where that event returns a clean allow), with an attempted row; a
    # failed row append is reported through the withheld-bookkeeping text and lands no row ---
    def dir_fault(f):
        dst = os.stat(str(f.root))

        def fsync(fd):
            if is_dir_of(fd, (dst.st_dev, dst.st_ino)):
                raise OSError(errno_sp.EIO, "injected directory fsync fault")
            return real_fsync(fd)
        return fsync
    pd = fixture("postnotdurable", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    pd_before = pd.lease.read_bytes()
    result = patched([(os, "fsync", dir_fault(pd))], lambda: post(pd))
    err = result[2] if isinstance(result[2], str) else ""
    check("leasehb/post-dir-fsync-not-durable-error-text",
          (result[0], result[1], "WAS replaced and verified, but it is not durable" in err,
           "injected directory fsync fault" in err, pd.lease.read_bytes() != pd_before,
           decisions(pd)),
          (0, None, True, True, True, ["written-not-durable"]))
    pr = fixture("postnotdurablerow", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    pr_before = pr.lease.read_bytes()

    def row_fault(fd, view):
        raise OSError(errno_sp.EIO, "injected row write fault")
    result = patched([(os, "fsync", dir_fault(pr)),
                      (aiqt_hooks, "_orch_lease_hb_row_write", row_fault)], lambda: post(pr))
    err = result[2] if isinstance(result[2], str) else ""
    check("leasehb/post-dir-fsync-not-durable-row-fault-withheld",
          (result[0], result[1], "WAS replaced and verified, but it is not durable" in err,
           "bookkeeping withheld (the bookkeeping append failed ([Errno {}] injected row write "
           "fault))".format(errno_sp.EIO) in err, pr.lease.read_bytes() != pr_before, rows(pr)),
          (0, None, True, True, True, []))
    # a written-unverified refresh on PostToolUse (the replace happened, the post-replace
    # verification failed) is the result's error text too, never plain success (red where that
    # event returns a clean allow), with an attempted row
    pv = fixture("postunverified", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    pv_before = pv.lease.read_bytes()
    pv_fired = []

    def verify_fault(*_args):
        pv_fired.append(True)
        return "injected verify fault"
    result = patched([(aiqt_hooks, "_orch_lease_hb_verify", verify_fault)], lambda: post(pv))
    err = result[2] if isinstance(result[2], str) else ""
    check("leasehb/post-verify-fault-unverified-error-text",
          (len(pv_fired), result[0], result[1],
           "WAS replaced but could not be verified afterwards (injected verify fault)" in err,
           pv.lease.read_bytes() != pv_before, decisions(pv)),
          (1, 0, None, True, True, ["written-unverified"]))
    # the same unverified write with its row append failing too: the written-unverified warning
    # and the withheld-bookkeeping report both ride the error text, and no row lands (red where
    # either text is lost, or the row is written by another route)
    pw = fixture("postunverifiedrow", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    pw_before = pw.lease.read_bytes()
    pv_fired.clear()
    result = patched([(aiqt_hooks, "_orch_lease_hb_verify", verify_fault),
                      (aiqt_hooks, "_orch_lease_hb_row_write", row_fault)], lambda: post(pw))
    err = result[2] if isinstance(result[2], str) else ""
    check("leasehb/post-verify-fault-row-fault-withheld",
          (len(pv_fired), result[0], result[1],
           "WAS replaced but could not be verified afterwards (injected verify fault)" in err,
           "bookkeeping withheld (the bookkeeping append failed ([Errno {}] injected row write "
           "fault))".format(errno_sp.EIO) in err, pw.lease.read_bytes() != pw_before, rows(pw)),
          (1, 0, None, True, True, True, []))

    # --- (3) the temporary takes the lease's proven group, then the lease's mode, then its
    # first byte (red where no group change is made, where any gid other than the proven one is
    # passed, and where the mode change runs ahead of the group change), and a failure to set
    # the group is the named no-write refusal with nothing published ---
    gp = fixture("tmpgroup", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    gp_probe = gp.root / "group-probe.txt"
    gp_probe.write_bytes(b"")
    default_gid = os.stat(str(gp_probe)).st_gid
    gp_probe.unlink()
    for other in sorted(set(os.getgroups()) | {os.getegid()}):
        if other != default_gid:
            # a group this process can set: the lease then really differs from the default
            try:
                os.chown(str(gp.lease), -1, other)
                break
            except OSError:
                continue
    lease_gid = os.stat(str(gp.lease)).st_gid
    # group bits a mode change made ahead of the group change would grant the default group
    os.chmod(str(gp.lease), 0o640)
    gp_mode = stat.S_IMODE(os.lstat(str(gp.lease)).st_mode)
    # the proof stat the keeper reads carries a gid that is none of the new-file group, the real
    # or effective gid, a supplementary gid, the directory's gid or the lease's real gid, so
    # only the lease's PROVEN group can be the argument: on a host with one group those real
    # gids all coincide, and a writer passing the effective or the new-file gid would pass a
    # real-gid comparison there. The group change then applies the lease's real group in the
    # proof gid's place.
    known_gids = set(os.getgroups()) | {os.getgid(), os.getegid(), default_gid, lease_gid,
                                        os.stat(str(gp.root)).st_gid}
    proof_gid = max(known_gids) + 1
    real_load = aiqt_hooks._orch_lease_hb_load
    real_fchmod = os.fchmod

    class ProofGidStat:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, attr):
            return getattr(self._st, attr)

        @property
        def st_gid(self):
            return proof_gid

    def gp_load(fd):
        loaded = real_load(fd)
        if loaded[0] != "ok":
            return loaded
        return ("ok", (ProofGidStat(loaded[1][0]), loaded[1][1]))
    gp_tmp = gp.root / ".forced.tmpgroup.hb.tmp"
    gp_events = []

    def gp_fchown(fd, uid, gid):
        if is_entry(fd, gp_tmp):
            gp_events.append(("fchown", uid, gid))
            return real_fchown(fd, uid, lease_gid if gid == proof_gid else gid)
        return real_fchown(fd, uid, gid)

    def gp_fchmod(fd, mode):
        if is_entry(fd, gp_tmp):
            gp_events.append(("fchmod", mode))
        return real_fchmod(fd, mode)

    def gp_write(fd, data):
        # the FIRST write through the temporary only: the order check reads the first three
        if is_entry(fd, gp_tmp) and ("write",) not in gp_events:
            gp_events.append(("write",))
        return real_write(fd, data)
    gp_before = gp.lease.read_bytes()
    result = patched([(aiqt_hooks, "_orch_lease_hb_tmp_name", lambda name_arg: gp_tmp.name),
                      (aiqt_hooks, "_orch_lease_hb_load", gp_load), (os, "fchown", gp_fchown),
                      (os, "fchmod", gp_fchmod), (os, "write", gp_write)], lambda: stop(gp))
    check("leasehb/field-tmp-takes-lease-group",
          ([e[2] for e in gp_events if e[0] == "fchown"], _verdict(result),
           gp.lease.read_bytes() != gp_before, os.stat(str(gp.lease)).st_gid == lease_gid,
           tmp_names(gp.root)),
          ([proof_gid], "allow", True, True, []))
    check("leasehb/field-tmp-group-before-mode-and-write",
          (gp_mode, gp_events[:3], stat.S_IMODE(os.lstat(str(gp.lease)).st_mode)),
          (0o640, [("fchown", -1, proof_gid), ("fchmod", 0o640), ("write",)], 0o640))
    gf = fixture("tmpgroupfault", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    gf_tmp = gf.root / ".forced.tmpgroupfault.hb.tmp"
    gf_fired = []

    def gf_fchown(fd, uid, gid):
        if is_entry(fd, gf_tmp):
            gf_fired.append(gid)
            raise PermissionError(errno_sp.EPERM, "injected group change fault")
        return real_fchown(fd, uid, gid)
    gf_before = snap(gf.lease)
    result = patched([(aiqt_hooks, "_orch_lease_hb_tmp_name", lambda name_arg: gf_tmp.name),
                      (os, "fchown", gf_fchown)], lambda: stop(gf))
    check("leasehb/field-tmp-group-fault-refused",
          (len(gf_fired), _verdict(result),
           "the temporary file cannot be given the lease's group ([Errno {}] injected group "
           "change fault)".format(errno_sp.EPERM) in text_of(result),
           "the lease is unchanged" in text_of(result), snap(gf.lease) == gf_before,
           tmp_names(gf.root), decisions(gf)),
          (1, "warn", True, True, True, [], ["no-write"]))
    # a failure of the temporary's mode change is refused under its own name, never labelled a
    # write failure (red where the mode change runs under the write step's label)
    gm = fixture("tmpmodefault", "holder: s1\nheartbeat_utc: " + now_iso(0.25) + "\n",
                 hb(field="heartbeat_utc"), age_secs=900)
    gm_tmp = gm.root / ".forced.tmpmodefault.hb.tmp"
    gm_fired = []

    def gm_fchmod(fd, mode):
        if is_entry(fd, gm_tmp):
            gm_fired.append(mode)
            raise PermissionError(errno_sp.EPERM, "injected mode change fault")
        return real_fchmod(fd, mode)
    gm_before = snap(gm.lease)
    result = patched([(aiqt_hooks, "_orch_lease_hb_tmp_name", lambda name_arg: gm_tmp.name),
                      (os, "fchmod", gm_fchmod)], lambda: stop(gm))
    check("leasehb/field-tmp-mode-fault-refused",
          (len(gm_fired), _verdict(result),
           "the temporary file cannot be given the lease's mode ([Errno {}] injected mode "
           "change fault)".format(errno_sp.EPERM) in text_of(result),
           "cannot be written" in text_of(result), "the lease is unchanged" in text_of(result),
           snap(gm.lease) == gm_before, tmp_names(gm.root), decisions(gm)),
          (1, "warn", True, False, True, True, [], ["no-write"]))

    # --- (4) the special-bit refusal is the field write's alone: the mtime touch refreshes a
    # setgid lease and leaves its mode as it was ---
    sm = fixture("setgidmtime", "holder: s1\n", hb(), age_secs=900)
    os.chmod(str(sm.lease), 0o2640)
    sm_mode = stat.S_IMODE(os.lstat(str(sm.lease)).st_mode)
    sm_before = snap(sm.lease)
    result = stop(sm)
    check("leasehb/mtime-setgid-lease-touched-mode-kept",
          (sm_mode, _verdict(result), sm.lease.read_bytes() == sm_before[0],
           os.lstat(str(sm.lease)).st_mtime_ns > sm_before[1],
           stat.S_IMODE(os.lstat(str(sm.lease)).st_mode)),
          (0o2640, "allow", True, True, 0o2640))

    # --- (5) a json field write keeps nested members, escaped key spellings and whitespace
    # variants byte-identical (red on a re-serializing writer, and on a textual writer that
    # rewrites the first member named like the field, a nested one included) ---
    def json_case(name, template):
        old_hb = now_iso(2)
        text = template.replace("OLD", old_hb)
        fj = fixture(name, text, hb(format="json", field="heartbeat_utc"))
        result_j = stop(fj)
        new_text = fj.lease.read_bytes().decode("utf-8")
        try:
            new_hb = json.loads(new_text)["heartbeat_utc"]
        except (ValueError, KeyError, TypeError):
            return ("unparsed", text_of(result_j))
        return (new_hb != old_hb, new_text == text.replace(old_hb, new_hb))
    check("leasehb/json-nested-same-name-member-kept",
          json_case("jsonnested",
                    '{"meta": {"heartbeat_utc": "2020-01-01T00:00:00Z", '
                    '"list": [1,{"heartbeat_utc":"x"}]},  "holder": "s1", '
                    '"heartbeat_utc": "OLD"}\n'),
          (True, True))
    check("leasehb/json-escaped-keys-kept",
          json_case("jsonescaped",
                    '{"hold\\u0065r": "s1", "heartbeat\\u005futc": "OLD", '
                    '"n\\u00e4me": "caf\\u00e9", "a\\"b": 1}\n'),
          (True, True))
    dup_text = ('{"holder": "s1", "heartbeat_utc": "' + now_iso(2) + '", '
                '"heartbeat\\u005futc": "' + now_iso(2) + '"}\n')
    fd_dup = fixture("jsonescapeddup", dup_text, hb(format="json", field="heartbeat_utc"))
    dup_before = snap(fd_dup.lease)
    result = stop(fd_dup)
    check("leasehb/json-escaped-duplicate-key-cannot-evaluate",
          (_verdict(result), "cannot be evaluated (the lease does not parse as json" in
           text_of(result), snap(fd_dup.lease) == dup_before),
          ("warn", True, True))
    check("leasehb/json-whitespace-variants-kept",
          (json_case("jsoncompact", '{"holder":"s1","heartbeat_utc":"OLD","n":[1,2,{"k":null}]}'),
           json_case("jsontabscrlf",
                     '{\r\n\t"holder"\t:\t"s1" ,\r\n\t"heartbeat_utc" :  "OLD"\r\n}\r\n'),
           json_case("jsonouterws", '\n \t{ "holder" : "s1" , "heartbeat_utc" : "OLD" }\n\n')),
          ((True, True), (True, True), (True, True)))

    # --- (6) the remaining separators str.splitlines breaks on stay inside their line (red on a
    # splitlines reader: the phantom holder line proved the holder and the lease was touched) ---
    for cid, name, sep in (("leasehb/lines-u2029-no-phantom-holder", "sepu2029", "\u2029"),
                           ("leasehb/lines-vt-no-phantom-holder", "sepvt", "\x0b"),
                           ("leasehb/lines-fs-no-phantom-holder", "sepfs", "\x1c"),
                           ("leasehb/lines-gs-no-phantom-holder", "sepgs", "\x1d"),
                           ("leasehb/lines-rs-no-phantom-holder", "seprs", "\x1e"),
                           ("leasehb/lines-lone-cr-no-phantom-holder", "sepcr", "\r")):
        fs = fixture(name, "note: x" + sep + "holder: s1\n", hb(), primed=False)
        fs_before = snap(fs.lease)
        result = stop(fs)
        check(cid, (_verdict(result), "appears 0 times" in text_of(result),
                    snap(fs.lease) == fs_before), ("warn", True, True))

    # a lease whose lines end with a lone carriage return is ONE physical line: a cannot-evaluate
    # on Stop and nothing written on either event, wherever the holder line sits (red where a
    # holder line that comes first reads as a foreign holder, in silence)
    lone = {}
    for tag, lone_text, lone_block in (
            ("first", "holder: s1\rheartbeat_utc: " + now_iso(2) + "\r",
             hb(field="heartbeat_utc")),
            ("single", "holder: s1\r", hb()),
            ("later", "heartbeat_utc: " + now_iso(2) + "\rholder: s1\r",
             hb(field="heartbeat_utc"))):
        fc = fixture("lonecr" + tag, lone_text, lone_block)
        fc_before = snap(fc.lease)
        stop_text = text_of(stop(fc))
        post_result = post(fc)
        lone[tag] = ("the declared lease cannot be evaluated" in stop_text,
                     "the declared holder field's value carries a carriage return" in stop_text,
                     "the declared holder field appears 0 times" in stop_text,
                     _verdict(post_result), snap(fc.lease) == fc_before)
    check("leasehb/lines-lone-cr-lease-cannot-evaluate", lone,
          {"first": (True, True, False, "allow", True),
           "single": (True, True, False, "allow", True),
           "later": (True, False, True, "allow", True)})

    # a CRLF lease is still refreshed, its line endings and other lines byte-identical (red on
    # a carriage-return rule that refuses a carriage return anywhere in a lines lease)
    crlf_old = now_iso(0.25)
    crlf_text = "holder: s1\r\nheartbeat_utc: " + crlf_old + "\r\nnote: kept\r\n"
    fl = fixture("crlflease", crlf_text, hb(field="heartbeat_utc"), age_secs=900)
    result = stop(fl)
    crlf_new = fl.lease.read_bytes().decode("utf-8")
    crlf_lines = crlf_new.split("\r\n")
    crlf_hb = crlf_lines[1][len("heartbeat_utc: "):] if len(crlf_lines) > 1 else ""
    check("leasehb/lines-crlf-lease-refreshed-endings-kept",
          (_verdict(result), crlf_hb != crlf_old, crlf_new == crlf_text.replace(crlf_old, crlf_hb)),
          ("allow", True, True))


def main(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    from _git_fixture_env import fixture_git_lifecycle, scrub_git_environment
    scrub_git_environment()
    with fixture_git_lifecycle():
        return _main_isolated(report_path)


def _main_isolated(report_path=None):
    try:
        tmp = _fixture_tmpdir("aiqt-orch-selftest-")
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temp dir: {}".format(exc), file=sys.stderr)
        return 2
    os.environ["XDG_STATE_HOME"] = str(tmp / "xdg")  # hermetic default state root
    # Hermetic registry scope: an inherited opt-in AIQT_ORCH_REQUIRE_REGISTRY would turn every no-registry
    # allow below into a deny; the registry-required checks set and restore it themselves.
    os.environ.pop(aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV, None)
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
        # Inside registry scope the deny reads a here-document body and arithmetic as code and does not
        # read setsid, nohup or disown, exactly as before the out-of-scope note was added: the note's own
        # scan must not leak into the deny.
        check("trunc/fg-heredoc-amp-still-denies",
              _verdict(bg("cat > /tmp/f <<'EOF'\nFix A & B\nEOF", rib=False)), "deny")
        check("trunc/fg-arith-amp-still-denies", _verdict(bg("echo $(( 6 & 3 ))", rib=False)), "deny")
        check("trunc/fg-setsid-still-allows", _verdict(bg("setsid long_job", rib=False)), "allow")
        # With no orchestration registry the guard denies nothing, but a readable foreground command that
        # detaches work is allowed WITH A NOTE (the reducer's "warn") naming the tracked background
        # mechanism; every other call stays the silent allow.
        ti = Fixture(tmp, "trunc-inert")
        (ti.root / ".aiqt" / "orchestration.local.json").unlink()
        check("trunc/fg-detach-inert-no-registry", _verdict(aiqt_hooks.orch_truncation_guard(
            ti.payload("PreToolUse", "Bash",
                       {"command": "long_job &", "run_in_background": False}))), "warn")
        # The same no-registry fixture: (check id, tool_input, reduced verdict). A foreground command that
        # detaches work notes ("warn"); the controls stay the silent allow ("allow"). Each control carries an
        # ampersand or a detach word that a scan ignoring quotes, comments, here-document bodies, arithmetic,
        # command position (redirect targets included), setsid -w or the background flag would note.
        for check_id, tool_input, want in (
                ("trunc/oos-detach-no-space-notes", _fg("long_job&"), "warn"),
                ("trunc/oos-detach-loop-end-notes", _fg('for d in a b; do run_one "$d"; done &'), "warn"),
                ("trunc/oos-detach-loop-body-notes", _fg('for d in a b; do run_one "$d" & done'), "warn"),
                ("trunc/oos-detach-subshell-notes", _fg("( long_job ) &"), "warn"),
                ("trunc/oos-detach-brace-notes", _fg("{ long_job; } &"), "warn"),
                ("trunc/oos-detach-redirect-notes", _fg("long_job > /tmp/log 2>&1 &"), "warn"),
                ("trunc/oos-detach-here-string-notes", _fg('grep foo <<< "$x" &'), "warn"),
                ("trunc/oos-detach-second-line-notes", _fg("cd /tmp\nlong_job &"), "warn"),
                ("trunc/oos-detach-after-comment-notes", _fg("echo hi  # note\nsleep 100 &"), "warn"),
                ("trunc/oos-detach-after-heredoc-notes",
                 _fg("cat <<'EOF'\nthe user's file & more\nEOF\nlong_job &"), "warn"),
                ("trunc/oos-detach-omitted-flag-notes", {"command": "long_job &"}, "warn"),
                ("trunc/oos-setsid-notes", _fg("setsid long_job"), "warn"),
                ("trunc/oos-setsid-fork-notes", _fg("setsid -f long_job"), "warn"),
                ("trunc/oos-nohup-notes", _fg("nohup long_job > /tmp/log 2>&1"), "warn"),
                ("trunc/oos-disown-notes", _fg("long_job; disown"), "warn"),
                ("trunc/oos-wrapped-nohup-notes", _fg("timeout 5 nohup long_job"), "warn"),
                ("trunc/oos-assign-setsid-notes", _fg("X=1 setsid long_job"), "warn"),
                ("trunc/oos-after-and-setsid-notes", _fg("cd /tmp && setsid long_job"), "warn"),
                ("trunc/oos-logical-and-allows", _fg("build && test"), "allow"),
                ("trunc/oos-amp-redirect-allows", _fg("build &> /tmp/o"), "allow"),
                ("trunc/oos-dup-allows", _fg("build 2>&1"), "allow"),
                ("trunc/oos-pipe-stderr-allows", _fg("build |& tee /tmp/l"), "allow"),
                ("trunc/oos-single-quoted-amp-allows", _fg("echo 'a & b'"), "allow"),
                ("trunc/oos-double-quoted-amp-allows", _fg('echo "a & b"'), "allow"),
                ("trunc/oos-escaped-amp-allows", _fg("echo a \\& b"), "allow"),
                ("trunc/oos-comment-amp-allows", _fg("echo done # & comment"), "allow"),
                ("trunc/oos-heredoc-quoted-amp-allows",
                 _fg("cat > /tmp/f <<'EOF'\nFix A & B\nEOF"), "allow"),
                ("trunc/oos-heredoc-unquoted-amp-allows",
                 _fg("cat > /tmp/f <<EOF\nFix A & B\nEOF"), "allow"),
                ("trunc/oos-heredoc-detach-word-allows", _fg("cat <<'EOF'\nsetsid x\nEOF"), "allow"),
                ("trunc/oos-arith-expansion-allows", _fg("echo $(( 6 & 3 ))"), "allow"),
                ("trunc/oos-arith-command-allows", _fg("(( x = 6 & 3 ))"), "allow"),
                ("trunc/oos-setsid-wait-allows", _fg("setsid -w long_job"), "allow"),
                ("trunc/oos-setsid-wait-long-allows", _fg("setsid --wait long_job"), "allow"),
                ("trunc/oos-detach-word-argument-allows", _fg("git log --grep setsid"), "allow"),
                ("trunc/oos-detach-word-redirect-target-allows", _fg("echo hi > nohup"), "allow"),
                # A redirect operator and its target operand leave the pending command position alone, with
                # a blank between operator and target or not: a leading redirect must not hide the real
                # command word behind its target, and a spaced redirect target named like a detacher is a
                # filename, not a command (bash runs long_job in `> nohup long_job`).
                ("trunc/oos-redirect-target-detacher-name-allows", _fg("> nohup long_job"), "allow"),
                ("trunc/oos-redirect-first-setsid-notes", _fg("2> /dev/null setsid -f long_job"), "warn"),
                ("trunc/oos-redirect-first-nohup-notes", _fg("> /tmp/log nohup long_job"), "warn"),
                ("trunc/oos-redirect-stdin-first-setsid-notes", _fg("< /dev/null setsid long_job"), "warn"),
                ("trunc/oos-redirect-first-no-space-setsid-notes", _fg("2>/dev/null setsid long_job"), "warn"),
                ("trunc/oos-redirect-target-no-space-detacher-notes", _fg(">nohup setsid long_job"), "warn"),
                ("trunc/oos-redirect-pair-first-setsid-notes",
                 _fg("> /dev/null 2> /tmp/e setsid long_job"), "warn"),
                ("trunc/oos-redirect-mid-wrapper-nohup-notes",
                 _fg("timeout 5 > /dev/null nohup long_job"), "warn"),
                ("trunc/oos-redirect-quoted-target-setsid-notes", _fg("> 'nohup' setsid long_job"), "warn"),
                # a dup target that is not a number: the blank after `>&` must not end the redirect hold
                ("trunc/oos-dup-spaced-target-setsid-notes", _fg(">& /dev/null setsid long_job"), "warn"),
                # A here-string `<<<` is a redirect whose word is its target, never a here-document opener:
                # the command after the word is still read, and the lines after it are code, not a body.
                # The escaped `\<` row is the control: bash reads `\<` as a word and `<<EOF` as a real
                # here-document, so only an unquoted `<` before `<<` stops the opener.
                ("trunc/oos-here-string-first-setsid-notes", _fg("<<< word setsid long_job"), "warn"),
                ("trunc/oos-here-string-first-no-space-setsid-notes", _fg("<<<word setsid long_job"), "warn"),
                ("trunc/oos-here-string-mid-wrapper-setsid-notes", _fg("nice <<< word setsid long_job"),
                 "warn"),
                ("trunc/oos-here-string-next-line-detach-notes", _fg("cat <<< word\nlong_job &"), "warn"),
                ("trunc/oos-here-string-target-detacher-name-allows", _fg("<<< nohup long_job"), "allow"),
                ("trunc/oos-escaped-lt-heredoc-body-allows", _fg("\\<<<EOF\nsetsid x\nEOF"), "allow"),
                # The noclobber redirect `>|` is one operator, not a redirect then a pipe: its target named
                # like a detacher is a filename, and the command after its target is still read. A pipe after
                # the target still starts a command.
                ("trunc/oos-noclobber-target-detacher-name-allows", _fg(">| nohup long_job"), "allow"),
                ("trunc/oos-noclobber-trailing-detacher-name-allows", _fg("echo hi >| nohup"), "allow"),
                ("trunc/oos-noclobber-first-setsid-notes", _fg(">| /tmp/f setsid long_job"), "warn"),
                ("trunc/oos-noclobber-fd-first-setsid-notes", _fg("2>| /tmp/e setsid long_job"), "warn"),
                ("trunc/oos-noclobber-then-pipe-nohup-notes", _fg("echo hi >| /tmp/f | nohup long_job"),
                 "warn"),
                # A named descriptor written directly before its operator is part of the redirect; a brace
                # word with a blank after it is the command itself.
                ("trunc/oos-named-fd-first-setsid-notes", _fg("{fd}> /tmp/x setsid long_job"), "warn"),
                ("trunc/oos-brace-word-command-allows", _fg("{fd} setsid long_job"), "allow"),
                # setsid waits under -w, --wait, or any unique prefix of --wait; --w is the shortest such
                # prefix (just inside the bound), while `--` ends the options and --waitx is no prefix
                # (both just outside, so each still notes).
                ("trunc/oos-setsid-wait-prefix-allows", _fg("setsid --w long_job"), "allow"),
                ("trunc/oos-setsid-double-dash-notes", _fg("setsid -- long_job"), "warn"),
                ("trunc/oos-setsid-wait-nonprefix-notes", _fg("setsid --waitx long_job"), "warn"),
                # Documented limit: a here-document inside a double-quoted command substitution (the
                # commit-message form) is read as double-quoted text, so a body whose double quotes toggle
                # that reading exposes a body `&` to an extra note; a body that leaves the reading
                # balanced stays silent. The same misreading can also hide a detach: a body that leaves a
                # quote open ends the scan inside it, and that case stays silent (the residue discloses both).
                ("trunc/oos-commit-heredoc-amp-exposed-notes",
                 _fg("git commit -m \"$(cat <<'EOF'\nsay \"a & b\"\nEOF\n)\""), "warn"),
                ("trunc/oos-commit-heredoc-amp-allows",
                 _fg("git commit -m \"$(cat <<'EOF'\nFix A & B\nEOF\n)\""), "allow"),
                ("trunc/oos-plain-allows", _fg("python3 build.py"), "allow"),
                ("trunc/oos-unbalanced-allows", _fg("echo 'oops & bg"), "allow"),
                ("trunc/oos-background-allows",
                 {"command": "setsid long_job &", "run_in_background": True}, "allow"),
                ("trunc/oos-flag-not-boolean-allows",
                 {"command": "long_job &", "run_in_background": "true"}, "allow")):
            check(check_id, _verdict(aiqt_hooks.orch_truncation_guard(
                ti.payload("PreToolUse", "Bash", tool_input))), want)
        _oos = aiqt_hooks.orch_truncation_guard(ti.payload(
            "PreToolUse", "Bash", {"command": "setsid long_job", "run_in_background": False}))
        _oos_note = (_oos[1] or {}).get("systemMessage", "")
        check("trunc/oos-note-names-mechanism",
              (_verdict(_oos), "run_in_background" in _oos_note, "setsid" in _oos_note), ("warn", True, True))
        _oos_bad = ti.payload("PreToolUse", "Bash")
        _oos_bad["tool_input"] = "long_job &"
        check("trunc/oos-malformed-input-allows", _verdict(aiqt_hooks.orch_truncation_guard(_oos_bad)),
              "allow")
        # REGISTRY-REQUIRED MODE (opt-in, AIQT_ORCH_REQUIRE_REGISTRY): an ABSENT registry DENIES instead
        # of the default no-registry outcome (no deny, a readable foreground detach draws only the note),
        # with a reason naming the mode and its repair; an explicit off value keeps that default outcome,
        # and a PRESENT registry behaves identically in both modes.
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
            # an explicit off value keeps the default no-registry outcome: no deny, the detach note only
            check("trunc/registry-required-off-value-inert", _verdict(aiqt_hooks.orch_truncation_guard(
                ti.payload("PreToolUse", "Bash",
                           {"command": "long_job &", "run_in_background": False}))), "warn")
            # The off values are matched EXACTLY, nothing stripped (ASCII case-insensitive): a comparison that
            # stripped the value first would read a tab, a newline, or an off word wrapped in spaces or
            # no-break spaces as OFF and allow without a registry. Each reads as ON.
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
        # The cautious scanner's disclosed over-refusal residual: a here-document body is scanned as code,
        # so a safe body carrying an unquoted `&` is denied; the double-quoted commit-message form is read
        # as double-quoted text and allowed.
        check("trunc/fg-heredoc-amp-overrefusal-residual-denies",
              _verdict(bg("cat > f <<'EOF'\nFix A & B\nEOF", rib=False)), "deny")
        check("trunc/fg-commit-template-amp-allows",
              _verdict(bg("git commit -m \"$(cat <<'EOF'\nFix A & B\nEOF\n)\"", rib=False)), "allow")
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
        # Registry scope, the disclosed residual: BY DEFAULT (this run pops AIQT_ORCH_REQUIRE_REGISTRY) with NO
        # registry file the guard denies nothing (malformed input included; a readable foreground detach
        # draws only the note), while a PRESENT but unreadable or invalid registry keeps it active
        # (fail-closed).
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
        # that a bare '&' was found (before this fix it reused the bare-& detach reason).
        uq = bg("cat > f <<'EOF'\nthe user's file\nEOF", rib=False)
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
        # and git-toplevel union find no registry allows BY DEFAULT (a git FAILURE alone never denies by
        # default: the round-3 git lockout is withdrawn; the registry-required exception is pinned by
        # trunc/registry-required-git-unavailable-core-worktree-denies). The confirmed-outside CONTROL row passes before and after the fix.
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
        # registry (round 4), and BY DEFAULT a git FAILURE alone never denies (registry-required mode
        # denies one that hides a registry reachable only through the git toplevel: see the gw rows
        # below), so a cwd git cannot resolve (a bare
        # repository, a dubious-ownership refusal, a missing git binary, a broken config, a timeout) is
        # OUT OF SCOPE when no registry sits on the cwd's ancestor chain (each such row was a blanket deny
        # at the round-3 revision and now allows: the restored-allow controls), while the SAME failing git
        # inside an orchestrated tree still applies the guard (a detach denies, a plain command allows).
        # _recovery_git is patched to raise or refuse, so these rows pin that the union leg turns every
        # git failure into a clean out-of-scope read, never a deny (these rows run in the default mode).
        bare = tmp / "cwd-bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True,
                       timeout=30)
        # nocwd carries a detach (sleep 5 &), so each out-of-scope row below reads the out-of-scope note
        # ("warn"): an allow, never a deny, and a note that only a no-registry read produces.
        check("trunc/cwd-bare-repo-no-registry-allows", _cwd_case(str(bare))[0], "warn")
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
                check(_row, _cwd_case(str(norig))[0], "warn")
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
        check("trunc/cwd-inside-git-dir-no-registry-allows", _cwd_case(str(norig / ".git"))[0], "warn")
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
              (probe.returncode, _cwd_case(str(outside))[0]), (128, "warn"))
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
        # Registry-required mode on the union leg: the registry is reachable ONLY through the git toplevel
        # (core.worktree), so it is PRESENT and a plain call stays an allow. Red when the registry-required
        # deny is moved above the git-union test (an absent CHAIN registry alone must not deny).
        _rrg = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
        _rrg_old = os.environ.get(_rrg)
        try:
            os.environ[_rrg] = "1"
            check("trunc/registry-required-git-core-worktree-plain-allows",
                  _verdict(aiqt_hooks.orch_truncation_guard(dict(
                      nocwd, cwd=str(gw / "A"), tool_input=dict(command="ls -la")))), "allow")
            # The strict-mode exception to "a git failure alone never denies": with git unavailable (a
            # controlled PATH holding no git) the union leg reads False, so the registry reachable ONLY
            # through core.worktree reads as ABSENT and strict mode DENIES ls -la; the same call with the
            # variable unset ALLOWS (the default mode's out-of-scope read). Red when a git failure is read
            # as PRESENT, and red when the strict deny is skipped on a git failure.
            nogit = tmp / "gw-nogit-bin"
            nogit.mkdir()
            _path_old = os.environ.get("PATH")
            try:
                os.environ["PATH"] = str(nogit)
                ng = aiqt_hooks.orch_truncation_guard(dict(
                    nocwd, cwd=str(gw / "A"), tool_input=dict(command="ls -la")))
                ng_reason = (ng[1] or {}).get("hookSpecificOutput", {}).get(
                    "permissionDecisionReason", "")
                os.environ.pop(_rrg, None)
                ng_unset = _verdict(aiqt_hooks.orch_truncation_guard(dict(
                    nocwd, cwd=str(gw / "A"), tool_input=dict(command="ls -la"))))
            finally:
                if _path_old is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = _path_old
            check("trunc/registry-required-git-unavailable-core-worktree-denies",
                  (_verdict(ng), _rrg in ng_reason, ng_unset), ("deny", True, "allow"))
        finally:
            if _rrg_old is None:
                os.environ.pop(_rrg, None)
            else:
                os.environ[_rrg] = _rrg_old
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
        # ROUND 5, THREE-VALUED DISCOVERY: a registry entry the probe can neither rule out nor confirm is
        # _ORCH_REG_CANNOT_EVALUATE. BY DEFAULT it reads PRESENT (the guard stays active, so a plain
        # command allows and the detach rows above deny, unchanged); under registry-required mode it is
        # NOT a registry and a plain command DENIES with the cannot-evaluate reason. Each vector pins the
        # strict deny AND the unset verdict (plain allow, detach deny), so the strict deny is red when a
        # cannot-evaluate is read as a registry again and the unset pair is red on any default drift.
        (craft / "unreadable-dir" / ".aiqt").mkdir(parents=True)
        (craft / "nonreg-dir" / ".aiqt" / "orchestration.json").mkdir(parents=True)
        (craft / "nonreg-link" / ".aiqt").mkdir(parents=True)
        (craft / "nonreg-link" / "real.json").write_text(json.dumps(dict(version=1)), encoding="utf-8")
        os.symlink(str(craft / "nonreg-link" / "real.json"),
                   str(craft / "nonreg-link" / ".aiqt" / "orchestration.json"))
        # ROUND 6: the FIRST present registry name decides (whole-file precedence). A symlinked or directory
        # orchestration.local.json beside a REGULAR orchestration.json is not confirmed: red under the
        # mutant "any regular registry name confirms", which reads the regular orchestration.json as True.
        for _pname in ("local-link-beside-regular", "local-dir-beside-regular"):
            (craft / _pname / ".aiqt").mkdir(parents=True)
            (craft / _pname / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                                        encoding="utf-8")
        (craft / "local-link-beside-regular" / "real.json").write_text(json.dumps(dict(version=1)),
                                                                     encoding="utf-8")
        os.symlink(str(craft / "local-link-beside-regular" / "real.json"),
                   str(craft / "local-link-beside-regular" / ".aiqt" / "orchestration.local.json"))
        (craft / "local-dir-beside-regular" / ".aiqt" / "orchestration.local.json").mkdir()
        _r5 = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
        _r5_old = os.environ.get(_r5)
        _r5_stat = aiqt_hooks.os.stat
        _r5_unread = os.path.realpath(str(craft / "unreadable-dir" / ".aiqt"))

        def _r5_stat_unreadable(path, *a, **k):
            # A root run is not bound by mode 000, so the seam supplies the kernel's EACCES there for
            # the unreadable directory's registry lookups only (the uid-independence precedent).
            dfd = k.get("dir_fd")
            if (dfd is not None and path in _reg_names
                    and os.path.realpath("/proc/self/fd/{}".format(dfd)) == _r5_unread):
                raise PermissionError(13, "Permission denied", path)
            return _r5_stat(path, *a, **k)

        def _r5_case(cwd, command):
            res = aiqt_hooks.orch_truncation_guard(dict(nocwd, cwd=cwd, tool_input=dict(command=command)))
            why = (res[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            return _verdict(res), ("could not be confirmed" in why and _r5 in why)
        _r5_vectors = (("aiqt-regular-file", craft / "regular-file"),
                       ("aiqt-dangling-symlink", craft / "dangling"),
                       ("aiqt-unreadable-dir", craft / "unreadable-dir"),
                       ("aiqt-registry-name-directory", craft / "nonreg-dir"),
                       ("aiqt-registry-name-symlink", craft / "nonreg-link"),
                       ("aiqt-local-symlink-beside-regular", craft / "local-link-beside-regular"),
                       ("aiqt-local-directory-beside-regular", craft / "local-dir-beside-regular"))
        _r5_rows = {}
        try:
            os.chmod(_r5_unread, 0)
            if os.geteuid() == 0:
                aiqt_hooks.os.stat = _r5_stat_unreadable
            for _name, _dir in _r5_vectors:
                _r5_fd = os.open(str(_dir), aiqt_hooks._ORCH_O_WALK | os.O_DIRECTORY)
                try:
                    _probe = aiqt_hooks._orch_dirfd_has_registry(_r5_fd)
                finally:
                    os.close(_r5_fd)
                os.environ.pop(_r5, None)
                _unset = (_r5_case(str(_dir), "printf ok")[0], _r5_case(str(_dir), "long_job &")[0])
                os.environ[_r5] = "1"
                _strict = _r5_case(str(_dir), "printf ok")
                _r5_rows[_name] = (_probe, _unset, _strict)
        finally:
            aiqt_hooks.os.stat = _r5_stat
            os.chmod(_r5_unread, 0o755)
            if _r5_old is None:
                os.environ.pop(_r5, None)
            else:
                os.environ[_r5] = _r5_old
        # The rows sit literally in the for header so the execution-set gate resolves each id.
        for _cid, _name in (("trunc/registry-required-aiqt-regular-file-plain-denies", "aiqt-regular-file"),
                            ("trunc/registry-required-aiqt-dangling-symlink-plain-denies",
                             "aiqt-dangling-symlink"),
                            ("trunc/registry-required-aiqt-unreadable-dir-plain-denies",
                             "aiqt-unreadable-dir"),
                            ("trunc/registry-required-aiqt-registry-name-directory-plain-denies",
                             "aiqt-registry-name-directory"),
                            ("trunc/registry-required-aiqt-registry-name-symlink-plain-denies",
                             "aiqt-registry-name-symlink"),
                            ("trunc/registry-required-aiqt-local-symlink-beside-regular-plain-denies",
                             "aiqt-local-symlink-beside-regular"),
                            ("trunc/registry-required-aiqt-local-directory-beside-regular-plain-denies",
                             "aiqt-local-directory-beside-regular")):
            check(_cid, _r5_rows[_name],
                  (aiqt_hooks._ORCH_REG_CANNOT_EVALUATE, ("allow", "deny"), ("deny", True)))
        # The CONFIRMED side of the same probe: a regular registry file reads True (not the third value),
        # and the fixture tree's own regular registry satisfies registry-required mode (plain allow).
        conf_fd = os.open(str(t.root), aiqt_hooks._ORCH_O_WALK | os.O_DIRECTORY)
        try:
            _confirmed = aiqt_hooks._orch_dirfd_has_registry(conf_fd)
        finally:
            os.close(conf_fd)
        try:
            os.environ[_r5] = "1"
            _confirmed_strict = _r5_case(str(t.root), "printf ok")
        finally:
            if _r5_old is None:
                os.environ.pop(_r5, None)
            else:
                os.environ[_r5] = _r5_old
        check("trunc/registry-required-confirmed-registry-plain-allows",
              (_confirmed is True, _confirmed_strict), (True, ("allow", False)))
        # The UNION leg is three-valued too: a git toplevel (core.worktree, off the ancestor chain) whose
        # .aiqt is a regular file reads cannot-evaluate, so the default keeps the guard active (detach
        # denies, plain allows) and registry-required mode denies the plain call.
        gwc = tmp / "gw-cannot"
        (gwc / "B").mkdir(parents=True)
        (gwc / "B" / ".aiqt").write_text("not a directory", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(gwc / "A")], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(gwc / "A"), "config", "core.worktree", str(gwc / "B")],
                       check=True, capture_output=True, timeout=30)
        try:
            os.environ.pop(_r5, None)
            _gw_unset = (_r5_case(str(gwc / "A"), "printf ok")[0], _r5_case(str(gwc / "A"), "long_job &")[0])
            os.environ[_r5] = "1"
            _gw_strict = _r5_case(str(gwc / "A"), "printf ok")
        finally:
            if _r5_old is None:
                os.environ.pop(_r5, None)
            else:
                os.environ[_r5] = _r5_old
        check("trunc/registry-required-git-toplevel-cannot-evaluate-plain-denies",
              (aiqt_hooks._orch_git_toplevel_has_registry(str(gwc / "A")), _gw_unset, _gw_strict),
              (aiqt_hooks._ORCH_REG_CANNOT_EVALUATE, ("allow", "deny"), ("deny", True)))
        # ROUND 6, THE RECHECK'S THIRD VALUE: when the path-anchored recheck's probe returns
        # _ORCH_REG_CANNOT_EVALUATE the recheck returns ('cannot-evaluate', None), never ('found', None). The
        # direct call pins the return; the guard legs make the descriptor walk's own probes miss (the seam
        # is live only until the recheck starts), so the scope can come only from the recheck: unset, the
        # guard stays active (plain allow, detach deny); strict, it denies with the cannot-evaluate
        # reason. Red under the mutant recheck that returns ('found', None) for that probe.
        _rc_dir = str(craft / "regular-file")
        _rc_direct = aiqt_hooks._orch_walk_recheck(_rc_dir, [])
        _rc_probe = aiqt_hooks._orch_dirfd_has_registry
        _rc_recheck = aiqt_hooks._orch_walk_recheck
        _rc_live = []

        def _rc_walk_probe(dirfd):
            return _rc_probe(dirfd) if _rc_live else False

        def _rc_recheck_wrap(cwd, chain):
            _rc_live.append(True)
            return _rc_recheck(cwd, chain)
        _rc_rows = []
        try:
            aiqt_hooks._orch_dirfd_has_registry = _rc_walk_probe
            aiqt_hooks._orch_walk_recheck = _rc_recheck_wrap
            for _rc_env, _rc_cmd in ((None, "printf ok"), (None, "long_job &"), ("1", "printf ok")):
                del _rc_live[:]
                if _rc_env is None:
                    os.environ.pop(_r5, None)
                else:
                    os.environ[_r5] = _rc_env
                _rc_rows.append(_r5_case(_rc_dir, _rc_cmd))
        finally:
            aiqt_hooks._orch_dirfd_has_registry = _rc_probe
            aiqt_hooks._orch_walk_recheck = _rc_recheck
            if _r5_old is None:
                os.environ.pop(_r5, None)
            else:
                os.environ[_r5] = _r5_old
        check("trunc/walk-recheck-cannot-evaluate-strict-denies",
              (_rc_direct, (_rc_rows[0][0], _rc_rows[1][0]), _rc_rows[2]),
              (("cannot-evaluate", None), ("allow", "deny"), ("deny", True)))
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
        _ut_env = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
        _ut_old = os.environ.get(_ut_env)
        try:
            aiqt_hooks._recovery_toplevel = lambda _cwd: str(fcb / "top-file")
            cv, cw = _cwd_case(fcb_cwd)
            # ROUND 6, THREE-PART (probe value, unset pair, strict deny with reason): the union leg returns
            # its OWN value for a toplevel it cannot open, and strict mode denies it with a reason naming
            # the toplevel, not a .aiqt entry. Red when that except branch returns True (a confirmed
            # registry: strict allows) and red against round 5 (the .aiqt-fault reason).
            _ut_probe = aiqt_hooks._orch_git_toplevel_has_registry(fcb_cwd)
            os.environ.pop(_ut_env, None)
            _ut_plain = _verdict(aiqt_hooks.orch_truncation_guard(dict(
                nocwd, cwd=fcb_cwd, tool_input=dict(command="printf ok"))))
            os.environ[_ut_env] = "1"
            _ut_res = aiqt_hooks.orch_truncation_guard(dict(
                nocwd, cwd=fcb_cwd, tool_input=dict(command="printf ok")))
            _ut_why = (_ut_res[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        finally:
            aiqt_hooks._recovery_toplevel = saved_top
            if _ut_old is None:
                os.environ.pop(_ut_env, None)
            else:
                os.environ[_ut_env] = _ut_old
        check("trunc/union-unopenable-toplevel-detach-denies", (cv, "detaches a child" in cw),
              ("deny", True))
        check("trunc/registry-required-union-unopenable-toplevel-plain-denies",
              (_ut_probe, (_ut_plain, cv), _verdict(_ut_res),
               "toplevel git resolves for this cwd could not be opened as a directory" in _ut_why,
               _ut_env in _ut_why, "could not be confirmed" in _ut_why),
              (aiqt_hooks._ORCH_REG_TOPLEVEL_UNOPENABLE, ("allow", "deny"), "deny", True, True, False))
        # ROUND 6, THE DOCTOR REPORTS WHAT THE GUARD DECIDES (tools/orch_doctor.py over
        # aiqt_hooks._orch_truncation_scope, rooted at a fixture by patching its repo_root). Default mode, no
        # registry at the root but one ABOVE it: the doctor must not call the suite inert, and names the
        # truncation guard ACTIVE. Strict mode, a symlinked registry file the loader accepts: the doctor
        # reports the guard's deny, never "all usable". Red against the round-5 doctor on both rows.
        import importlib
        import contextlib
        import io
        _doc = importlib.import_module("orch_doctor")
        _doc_root = _doc.repo_root
        _doc_env = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
        _doc_old = os.environ.get(_doc_env)
        (tmp / "doc-above" / ".aiqt").mkdir(parents=True)
        (tmp / "doc-above" / ".aiqt" / "orchestration.json").write_text(json.dumps(dict(version=1)),
                                                                        encoding="utf-8")
        (tmp / "doc-above" / "nested").mkdir()
        # ROUND 7: a registry the loader accepts through a symlinked orchestration.json, with a WORKING
        # enumerator, a readable mode line, and a writable state directory, so the guard's strict deny is
        # the ONLY possible finding: unset mode reports "all usable" (exit 0) and a clean resume audit, and
        # strict mode must count the deny (exit 1, one finding) and carry it into the resume barrier. A
        # doctor that only prints the deny line, or drops it on --resume-audit, is red here.
        _doc_fx = Fixture(tmp, "doc-strict-link")
        _doc_fx.mode.write_text("Operating-mode: attended\n", encoding="utf-8")
        _doc_local = _doc_fx.root / ".aiqt" / "orchestration.local.json"
        _doc_real = _doc_fx.root / "registry-real.json"
        _doc_real.write_text(_doc_local.read_text(encoding="utf-8"), encoding="utf-8")
        _doc_local.unlink()
        os.symlink(str(_doc_real), str(_doc_fx.root / ".aiqt" / "orchestration.json"))
        _doc_barrier = _doc_fx.state / "resume-barrier.json"

        def _doc_run(root, env, *flags):
            if env is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = env
            _doc.repo_root = lambda: Path(root)
            out = io.StringIO()
            saved_argv = sys.argv
            sys.argv = [saved_argv[0]] + list(flags)
            try:
                with contextlib.redirect_stdout(out):
                    code = _doc.main()
            finally:
                sys.argv = saved_argv
            return code, out.getvalue()

        def _doc_barrier_read():
            with open(_doc_barrier, "r", encoding="utf-8") as fh:
                bar = json.load(fh)
            return (bar.get("active"), [f for f in bar.get("findings") or [] if "is DENIED" in f] != [])
        _doc_top = _doc.aiqt_hooks._recovery_toplevel
        try:
            _da_code, _da_out = _doc_run(tmp / "doc-above" / "nested", None)
            _du_code, _du_out = _doc_run(_doc_fx.root, None)
            _ds_code, _ds_out = _doc_run(_doc_fx.root, "1")
            _dru_code, _dru_out = _doc_run(_doc_fx.root, None, "--resume-audit")
            _dru_bar = _doc_barrier_read()
            _drs_code, _drs_out = _doc_run(_doc_fx.root, "1", "--resume-audit")
            _drs_bar = _doc_barrier_read()
            # Default mode, a git toplevel the union leg cannot open (fcb above): the guard is ACTIVE on a
            # discovery fault, which the doctor must report as that fault, not as a registry found.
            _doc.aiqt_hooks._recovery_toplevel = lambda _cwd: str(fcb / "top-file")
            _dt_code, _dt_out = _doc_run(fcb_cwd, None)
        finally:
            _doc.aiqt_hooks._recovery_toplevel = _doc_top
            _doc.repo_root = _doc_root
            if _doc_old is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = _doc_old
        check("doctor/default-registry-above-root-not-inert",
              (_da_code, "the suite is inert here" in _da_out, "truncation guard: ACTIVE" in _da_out),
              (2, False, True))
        check("doctor/registry-required-symlinked-registry-reports-deny",
              (_du_code, "all usable" in _du_out,
               _ds_code, "DOCTOR: 1 finding(s):\n  truncation guard (" in _ds_out,
               "is DENIED" in _ds_out and "symlinked registry file" in _ds_out, "all usable" in _ds_out),
              (0, True, 1, True, True, False))
        check("doctor/resume-audit-carries-strict-scope-deny",
              (_dru_code, "resume audit clean" in _dru_out, _dru_bar,
               _drs_code, "resume audit clean" in _drs_out,
               "1 finding(s); the barrier stays armed:\n  truncation guard (" in _drs_out, _drs_bar),
              (0, True, (False, False), 1, False, True, (True, True)))
        check("doctor/default-toplevel-unopenable-reports-fault-not-registry",
              (_dt_code, "truncation guard: ACTIVE" in _dt_out, "a registry entry was found" in _dt_out,
               "git toplevel cannot be opened as a directory" in _dt_out),
              (2, True, False, True))
        # ROUND 8, ONE BARRIER TRUTH: both resume-barrier writers (the SessionStart hook and the doctor's
        # --resume-audit) arm from aiqt_hooks._orch_resume_audit_findings, so a SessionStart after the
        # doctor armed the barrier for the strict scope deny (the symlinked registry above) keeps it
        # armed with the same findings; unset mode is clean in both. Red when the hook audits the resume
        # probes alone (it then clears the barrier while the guard still denies). A registry the loader
        # reports bad arms the barrier with its own finding in both writers (red when that finding is
        # dropped from the shared list). In default mode a cannot-evaluate scope found ABOVE a root with no
        # registry is reported as the fault, not as a registry found (red when the doctor's fault branch
        # tests only "toplevel-unopenable").
        _r8_bad = Fixture(tmp, "doc-bad-registry")
        (_r8_bad.root / ".aiqt" / "orchestration.local.json").write_text("{", encoding="utf-8")
        _r8_bad_bar = Path(aiqt_hooks._orch_state_dir_for_root(str(_r8_bad.root))) / "resume-barrier.json"
        (tmp / "doc-cev" / ".aiqt" / "orchestration.json").mkdir(parents=True)
        (tmp / "doc-cev" / "nested").mkdir()

        def _r8_bar(path):
            with open(path, "r", encoding="utf-8") as fh:
                bar = json.load(fh)
            return bar.get("active"), bar.get("findings")
        try:
            _r8_drs = _doc_run(_doc_fx.root, "1", "--resume-audit")[0]
            _r8_drs_bar = _r8_bar(_doc_barrier)
            _r8_hrs = _verdict(aiqt_hooks.orch_resume_audit(_doc_fx.payload("SessionStart")))
            _r8_hrs_bar = _r8_bar(_doc_barrier)
            os.environ.pop(_doc_env, None)
            _r8_hru = _verdict(aiqt_hooks.orch_resume_audit(_doc_fx.payload("SessionStart")))
            _r8_hru_bar = _r8_bar(_doc_barrier)
            _r8_dbad = _doc_run(_r8_bad.root, None, "--resume-audit")[0]
            _r8_dbad_bar = _r8_bar(_r8_bad_bar)
            _r8_hbad = _verdict(aiqt_hooks.orch_resume_audit(_r8_bad.payload("SessionStart")))
            _r8_hbad_bar = _r8_bar(_r8_bad_bar)
            _r8_cev_code, _r8_cev_out = _doc_run(tmp / "doc-cev" / "nested", None)
        finally:
            _doc.repo_root = _doc_root
            if _doc_old is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = _doc_old
        check("doctor/hook-resume-audit-keeps-strict-scope-barrier",
              (_r8_drs, _r8_drs_bar[0], _r8_hrs, _r8_hrs_bar == _r8_drs_bar,
               [f for f in _r8_hrs_bar[1] or [] if "is DENIED" in f] != [], _r8_hru, _r8_hru_bar),
              (1, True, "warn", True, True, "allow", (False, [])))
        _r8_read = "the orchestration registry could not be read ("
        check("doctor/resume-audit-registry-unreadable-arms-barrier",
              (_r8_dbad, _r8_dbad_bar[0], [f for f in _r8_dbad_bar[1] or [] if _r8_read in f] != [],
               _r8_hbad, _r8_hbad_bar == _r8_dbad_bar),
              (1, True, True, "warn", True))
        check("doctor/default-cannot-evaluate-above-root-reports-fault-not-registry",
              (_r8_cev_code, "truncation guard: ACTIVE" in _r8_cev_out,
               "hit a fault it reads as present" in _r8_cev_out, "a registry entry was found" in _r8_cev_out,
               "cannot be confirmed as a registry" in _r8_cev_out),
              (2, True, True, False, True))
        # ROUND 9, A WALK FAILURE ARMS THE BARRIER IN EVERY MODE WHERE GIT RESOLVES THE ROOT: a repository
        # root the truncation guard's walk cannot carry out is denied in every mode, so
        # _orch_guard_scope_report flags it as a deny and, where git resolves that root (here the fixture
        # is a real repository and only _orch_registry_walk is mocked), the SessionStart resume audit arms
        # the barrier and warns with that finding, unset and set alike (red when the 'fail' branch reports
        # denies=False). Where git resolves no root the audit returns silently before this check. The warning names the condition as a repair,
        # not only the record.
        _r9_fx = Fixture(tmp, "doc-walk-fail")
        _r9_bar = Path(aiqt_hooks._orch_state_dir_for_root(str(_r9_fx.root))) / "resume-barrier.json"
        _r9_walk = aiqt_hooks._orch_registry_walk
        _r9_old = os.environ.get(_doc_env)
        _r9_rows = []
        try:
            aiqt_hooks._orch_registry_walk = lambda _cwd: (
                "fail", ("is a directory this process cannot read and enter", "fix the root's mode"))
            for _r9_val in (None, "1"):
                if _r9_val is None:
                    os.environ.pop(_doc_env, None)
                else:
                    os.environ[_doc_env] = _r9_val
                _r9_rep = aiqt_hooks._orch_guard_scope_report(str(_r9_fx.root))
                _r9_res = aiqt_hooks.orch_resume_audit(_r9_fx.payload("SessionStart"))
                _r9_msg = json.dumps(_r9_res[1]) if _r9_res[1] is not None else ""
                _r9_b = _r8_bar(_r9_bar)
                _r9_rows.append((_r9_rep[1], _verdict(_r9_res), _r9_b[0],
                                 [f for f in _r9_b[1] or [] if "denied in every mode" in f] != [],
                                 "the condition it names" in _r9_msg))
        finally:
            aiqt_hooks._orch_registry_walk = _r9_walk
            if _r9_old is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = _r9_old
        check("doctor/resume-audit-walk-fail-arms-barrier-every-mode", tuple(_r9_rows),
              ((True, "warn", True, True, True), (True, "warn", True, True, True)))
        # ROUND 8, A MISSING GIT TOPLEVEL READS AS ABSENT (decided, not a fault): git names a toplevel that
        # does not exist (core.worktree naming a removed directory). It holds no registry, so the union leg
        # returns False and the scope is ('none', None): inert by default, and DENIED in registry-required
        # mode with the absent-registry reason (fail-closed there), never the unopenable-toplevel reason.
        _mt_top = aiqt_hooks._recovery_toplevel
        _mt_old = os.environ.get(_doc_env)
        try:
            aiqt_hooks._recovery_toplevel = lambda _cwd: str(fcb / "no-such-toplevel")
            _mt_probe = aiqt_hooks._orch_git_toplevel_has_registry(fcb_cwd)
            _mt_scope = aiqt_hooks._orch_truncation_scope(fcb_cwd)
            os.environ.pop(_doc_env, None)
            _mt_plain = _verdict(aiqt_hooks.orch_truncation_guard(dict(
                nocwd, cwd=fcb_cwd, tool_input=dict(command="printf ok"))))
            os.environ[_doc_env] = "1"
            _mt_res = aiqt_hooks.orch_truncation_guard(dict(
                nocwd, cwd=fcb_cwd, tool_input=dict(command="printf ok")))
            _mt_why = (_mt_res[1] or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        finally:
            aiqt_hooks._recovery_toplevel = _mt_top
            if _mt_old is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = _mt_old
        check("trunc/union-missing-toplevel-reads-absent-strict-denies",
              (_mt_probe, _mt_scope, _mt_plain, _verdict(_mt_res),
               "no .aiqt/orchestration.local.json or" in _mt_why, "could not be opened" in _mt_why),
              (False, ("none", None), "allow", "deny", True, False))
        # ROUND 12, WHERE THE RESUME AUDIT STAYS SILENT AND WHERE IT ARMS FOR A ROOT IT CANNOT ENTER: (a) where
        # _orch_root returns None the audit returns before it reads the registry, silent in both modes,
        # though the cwd's walk holds a registry (an unparsable one, so a read would warn), while the
        # guard's scope check from that cwd finds it and allows a plain command. A session cwd that is
        # missing, None, empty or not a string is just as silent and calls no git: for those calls the git
        # seam counts its calls and hands back that registry's directory, so a branch that calls git for
        # such a cwd (a fallback to the process cwd, for example) reads the registry and warns (red).
        # (b) git resolves a core.worktree toplevel from the repository's git directory: one that exists
        # without search permission (mode 0o000; a root run is not bound by it, so there the seam supplies
        # the kernel's EACCES for an lstat under it, matched on the path as created and on its real path,
        # since git prints the real path) and a regular file each fault the loader's lstat, so the audit
        # warns in both modes (red when that fault reads as absent) and writes a barrier file this row reads
        # back: active, its findings non-empty and each named in the warning (red when the barrier is
        # written inactive or its write fails); one that does not exist reads as absent, so the audit stays
        # silent and writes no barrier in both modes (red when the audit's absent return is dropped). The
        # barrier is removed before each call, so one an earlier call wrote cannot satisfy the row.
        # (c) Arming is best-effort: with XDG_STATE_HOME naming a regular file the barrier cannot be written,
        # yet the audit for the regular-file toplevel still warns, naming every finding the writable run
        # named, plus the forced-exit cannot-evaluate finding (opening the log under a state directory that
        # is not a directory fails other than as not found), and no barrier file exists at that path (red
        # when the write error propagates or the warning drops a finding). It also lists every path named
        # resume-barrier* under this self-test's temporary tree (the fixtures and the hermetic default
        # state root) before and after that run and requires none new (red when a failed write falls back
        # to another location under that tree; a write outside it is not watched).
        _r12_nr = tmp / "r12-noroot"
        (_r12_nr / ".aiqt").mkdir(parents=True)
        (_r12_nr / ".aiqt" / "orchestration.json").write_text("not json", encoding="utf-8")
        _r12_wt = dict()
        for _r12_kind in ("noenter", "file", "missing"):
            _r12_repo = tmp / "r12-wt" / _r12_kind / "repo"
            _r12_top = tmp / "r12-wt" / _r12_kind / "top"
            subprocess.run(["git", "init", "-q", str(_r12_repo)], check=True, capture_output=True, timeout=30)
            if _r12_kind == "noenter":
                _r12_top.mkdir()
            elif _r12_kind == "file":
                _r12_top.write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(_r12_repo), "config", "core.worktree", str(_r12_top)],
                           check=True, capture_output=True, timeout=30)
            _r12_wt[_r12_kind] = (str(_r12_repo / ".git"), str(_r12_top))
        _r12_noenter = _r12_wt["noenter"][1]
        _r12_noenter_under = tuple({_r12_noenter + os.sep, os.path.realpath(_r12_noenter) + os.sep})
        _r12_xdg_file = tmp / "r12-xdg-file"
        _r12_xdg_file.write_text("x", encoding="utf-8")
        _r12_lstat = aiqt_hooks.os.lstat

        def _r12_lstat_seam(path, *a, **k):
            if isinstance(path, str) and path.startswith(_r12_noenter_under):
                raise PermissionError(13, "Permission denied", path)
            return _r12_lstat(path, *a, **k)

        def _r12_barrier_path(root):
            return os.path.join(aiqt_hooks._orch_state_dir_for_root(root), "resume-barrier.json")

        def _r12_audit(cwd):
            # (root resolved to the configured toplevel, verdict, warning text, barrier read back) for one
            # SessionStart run with no barrier file beforehand.
            root = aiqt_hooks._orch_root(dict(cwd=cwd))
            if root is not None:
                try:
                    os.unlink(_r12_barrier_path(root))
                except (FileNotFoundError, NotADirectoryError):
                    pass
            try:
                res = aiqt_hooks.orch_resume_audit(dict(hook_event_name="SessionStart", cwd=cwd))
            except OSError as exc:
                return root, "raised " + type(exc).__name__, "", None
            msg = (res[1] or {}).get("systemMessage", "")
            if root is None:
                return root, _verdict(res), msg, None
            try:
                with open(_r12_barrier_path(root), "r", encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (FileNotFoundError, NotADirectoryError):
                return root, _verdict(res), msg, None
            except (OSError, ValueError) as exc:
                return root, _verdict(res), msg, "unreadable " + type(exc).__name__
            found = rec.get("findings") if isinstance(rec, dict) else None
            return root, _verdict(res), msg, (
                rec.get("active") if isinstance(rec, dict) else "not an object",
                isinstance(found, list) and bool(found) and all(isinstance(f, str) and f in msg for f in found))

        def _r12_barrier_names():
            # every path named resume-barrier* under this self-test's temporary tree
            return set(os.path.join(d, n) for d, dirs, files in os.walk(str(tmp))
                       for n in dirs + files if n.startswith("resume-barrier"))

        def _r12_findings(msg):
            # the findings text of a resume-audit warning, or a marker no warning carries
            head, sep, rest = msg.partition("observed reality: ")
            return rest.partition(". Correct the record")[0] if sep and rest else "\x00no findings"
        _r12_toplevel = aiqt_hooks._recovery_toplevel
        _r12_old = os.environ.get(_doc_env)
        _r12_xdg_old = os.environ.get("XDG_STATE_HOME")
        _r12_none, _r12_wt_rows, _r12_unwritable = [], [], []
        try:
            os.chmod(_r12_noenter, 0o000)
            if os.geteuid() == 0:
                aiqt_hooks.os.lstat = _r12_lstat_seam
            for _r12_val in (None, "1"):
                if _r12_val is None:
                    os.environ.pop(_doc_env, None)
                else:
                    os.environ[_doc_env] = _r12_val
                aiqt_hooks._recovery_toplevel = lambda _cwd: None
                _r12_entry = (
                    aiqt_hooks._orch_root(dict(cwd=str(_r12_nr))),
                    _verdict(aiqt_hooks.orch_resume_audit(dict(hook_event_name="SessionStart",
                                                               cwd=str(_r12_nr)))),
                    aiqt_hooks._orch_truncation_scope(str(_r12_nr)),
                    _verdict(aiqt_hooks.orch_truncation_guard(dict(
                        nocwd, cwd=str(_r12_nr), tool_input=dict(command="printf ok")))))
                _r12_calls = []
                aiqt_hooks._recovery_toplevel = lambda _cwd: _r12_calls.append(_cwd) or str(_r12_nr)
                _r12_entry += (tuple(
                    (aiqt_hooks._orch_root(_r12_ev), _verdict(aiqt_hooks.orch_resume_audit(_r12_ev)))
                    for _r12_ev in (dict(hook_event_name="SessionStart"),
                                    dict(hook_event_name="SessionStart", cwd=None),
                                    dict(hook_event_name="SessionStart", cwd=""),
                                    dict(hook_event_name="SessionStart", cwd=7),
                                    dict(hook_event_name="SessionStart", cwd=[str(_r12_nr)]))),
                    len(_r12_calls))
                _r12_none.append(_r12_entry)
                aiqt_hooks._recovery_toplevel = _r12_toplevel
                _r12_row, _r12_msgs = [], dict()
                for _r12_kind in ("noenter", "file", "missing"):
                    _r12_cwd, _r12_top = _r12_wt[_r12_kind]
                    _r12_root, _r12_v, _r12_msgs[_r12_kind], _r12_bar = _r12_audit(_r12_cwd)
                    _r12_row.append((
                        _r12_root is not None and os.path.realpath(_r12_root) == os.path.realpath(_r12_top),
                        _r12_v, _r12_bar))
                _r12_wt_rows.append(tuple(_r12_row))
                os.environ["XDG_STATE_HOME"] = str(_r12_xdg_file)
                try:
                    _r12_cwd, _r12_top = _r12_wt["file"]
                    _r12_seen = _r12_barrier_names()
                    _r12_root, _r12_v, _r12_msg, _r12_bar = _r12_audit(_r12_cwd)
                    _r12_unwritable.append((
                        _r12_root is not None and os.path.realpath(_r12_root) == os.path.realpath(_r12_top),
                        _r12_v, _r12_findings(_r12_msgs["file"]) in _r12_msg,
                        "pending forced-exit.jsonl present but unreadable" in _r12_msg, _r12_bar,
                        sorted(_r12_barrier_names() - _r12_seen)))
                finally:
                    if _r12_xdg_old is None:
                        os.environ.pop("XDG_STATE_HOME", None)
                    else:
                        os.environ["XDG_STATE_HOME"] = _r12_xdg_old
        finally:
            aiqt_hooks._recovery_toplevel = _r12_toplevel
            aiqt_hooks.os.lstat = _r12_lstat
            os.chmod(_r12_noenter, 0o700)
            if _r12_old is None:
                os.environ.pop(_doc_env, None)
            else:
                os.environ[_doc_env] = _r12_old
        check("resume-audit/no-root-silent-every-mode-with-registry-on-walk", tuple(_r12_none),
              ((None, "allow", ("found", None), "allow", ((None, "allow"),) * 5, 0),) * 2)
        check("resume-audit/core-worktree-toplevel-unenterable-arms-missing-silent", tuple(_r12_wt_rows),
              (((True, "warn", (True, True)), (True, "warn", (True, True)), (True, "allow", None)),) * 2)
        check("resume-audit/barrier-unwritable-still-warns-no-barrier-under-tmp", tuple(_r12_unwritable),
              ((True, "warn", True, True, None, []),) * 2)
        # ROUND 8, SEARCH PERMISSION DECIDES A .aiqt DIRECTORY'S PROBE (the documented attribute): mode
        # 0o100 (search only, no read) confirms the registry inside it; 0o600 (read and write, no search)
        # and 0o000 cannot be evaluated. A root run is not bound by these modes, so there the seam supplies
        # the kernel's EACCES for a registry-name stat under a .aiqt lacking the owner search bit.
        _sp_stat = aiqt_hooks.os.stat
        _sp_dirs = {}
        for _sp_mode in (0o100, 0o600, 0o000):
            _sp_dir = tmp / "search-perm" / ("m%03o" % _sp_mode)
            (_sp_dir / ".aiqt").mkdir(parents=True)
            (_sp_dir / ".aiqt" / "orchestration.json").write_text("{}", encoding="utf-8")
            _sp_dirs[_sp_mode] = _sp_dir

        def _sp_stat_seam(path, *a, **k):
            dfd = k.get("dir_fd")
            if dfd is not None and path in _reg_names and not (_sp_stat(dfd).st_mode & 0o100):
                raise PermissionError(13, "Permission denied", path)
            return _sp_stat(path, *a, **k)
        _sp_res = []
        try:
            for _sp_mode, _sp_dir in sorted(_sp_dirs.items(), reverse=True):
                os.chmod(str(_sp_dir / ".aiqt"), _sp_mode)
            if os.geteuid() == 0:
                aiqt_hooks.os.stat = _sp_stat_seam
            for _sp_mode in (0o100, 0o600, 0o000):
                _sp_fd = os.open(str(_sp_dirs[_sp_mode]), aiqt_hooks._ORCH_O_WALK | os.O_DIRECTORY)
                try:
                    _sp_res.append(aiqt_hooks._orch_dirfd_has_registry(_sp_fd))
                finally:
                    os.close(_sp_fd)
            # The O_RDONLY fallback (platforms without O_PATH) also needs READ permission to open .aiqt, so
            # there the mode 0o100 .aiqt cannot be evaluated: forced here on any platform.
            _sp_walk = aiqt_hooks._ORCH_O_WALK
            aiqt_hooks._ORCH_O_WALK = os.O_RDONLY
            try:
                _sp_rd_fd = os.open(str(_sp_dirs[0o100]), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    _sp_res.append(aiqt_hooks._orch_dirfd_has_registry(_sp_rd_fd))
                finally:
                    os.close(_sp_rd_fd)
            finally:
                aiqt_hooks._ORCH_O_WALK = _sp_walk
        finally:
            aiqt_hooks.os.stat = _sp_stat
            for _sp_dir in _sp_dirs.values():
                os.chmod(str(_sp_dir / ".aiqt"), 0o755)
        # Mode 0o100 evaluates normally only under an O_PATH walk (or for root, whom no mode bit binds).
        _sp_rdonly_100 = True if os.geteuid() == 0 else aiqt_hooks._ORCH_REG_CANNOT_EVALUATE
        _sp_walk_100 = True if getattr(os, "O_PATH", None) == aiqt_hooks._ORCH_O_WALK else _sp_rdonly_100
        check("trunc/aiqt-search-permission-decides-probe", tuple(_sp_res),
              (_sp_walk_100, aiqt_hooks._ORCH_REG_CANNOT_EVALUATE, aiqt_hooks._ORCH_REG_CANNOT_EVALUATE,
               _sp_rdonly_100))
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

        # ROUND 14, ATOMIC BARRIER WRITE: an armed barrier is seeded and a json.dump seam writes the first
        # ten characters of the barrier object, flushes, then raises ENOSPC. For an arming audit (a branch
        # finding), a clearing audit (clean), the PreToolUse warned-flag write and a direct call of the
        # helper the doctor uses, the seeded bytes must stay byte-identical and no temporary file may stay
        # beside it, while the arming audit still warns and the PreToolUse call still surfaces (red when
        # the barrier is opened for writing in place, as the truncated object then replaces the seed, or
        # when the temporary file is left behind). A last run without the seam must replace the seeded
        # barrier with a cleared one (red when the helper stops writing).
        _r14_bar = rsd / "resume-barrier.json"
        _r14_seed = json.dumps(dict(active=True, findings=["an earlier finding"], ts="seed", warned=False))
        _r14_trunc = json.dumps(dict(active=True))[:10]
        _r14_dump = json.dump

        def _r14_partial_dump(obj, fh, *a, **k):
            if isinstance(obj, dict) and "active" in obj and "warned" in obj:
                fh.write(_r14_trunc)
                fh.flush()
                raise OSError(28, "No space left on device")
            return _r14_dump(obj, fh, *a, **k)

        def _r14_left():
            # (seed unchanged, temporary files left beside the barrier)
            return (_r14_bar.read_text(encoding="utf-8") == _r14_seed,
                    sorted(n for n in os.listdir(str(rsd))
                           if n.startswith("resume-barrier.json.") and n.endswith(".tmp")))

        def _r14_msg(res):
            if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], dict):
                return res[1].get("systemMessage", "")
            return ""
        _r14_part = []
        try:
            for _r14_branch in ("feature/other", "main"):
                r.handoff.write_text("Branch: " + _r14_branch + chr(10), encoding="utf-8")
                _r14_bar.write_text(_r14_seed, encoding="utf-8")
                json.dump = _r14_partial_dump
                try:
                    _r14_res = aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))
                finally:
                    json.dump = _r14_dump
                _r14_part.append((_verdict(_r14_res), "feature/other" in _r14_msg(_r14_res)) + _r14_left())
            _r14_bar.write_text(_r14_seed, encoding="utf-8")
            json.dump = _r14_partial_dump
            try:
                _r14_res = aiqt_hooks.orch_resume_barrier(r.payload(
                    "PreToolUse", "Write", dict(file_path=str(r.root / "src.py"), content="x")))
                try:
                    aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=False, findings=[], ts="x",
                                                                       warned=False))
                    _r14_raised = "returned"
                except OSError as exc:
                    _r14_raised = "raised errno " + str(exc.errno)
            finally:
                json.dump = _r14_dump
            _r14_part.append((_verdict(_r14_res), "an earlier finding" in _r14_msg(_r14_res)) + _r14_left())
            _r14_part.append((_r14_raised,) + _r14_left())
            _r14_res = aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))
            _r14_part.append((_verdict(_r14_res), json.loads(_r14_bar.read_text(encoding="utf-8")).get("active"),
                              _r14_left()[1]))
        finally:
            json.dump = _r14_dump
            r.handoff.write_text("Branch: main" + chr(10), encoding="utf-8")
        check("barrier/partial-write-leaves-previous-barrier-byte-identical", tuple(_r14_part),
              (("warn", True, True, []), ("allow", False, True, []), ("warn", True, True, []),
               ("raised errno 28", True, []), ("allow", False, [])))
        # ROUND 14, A MALFORMED BARRIER READS ARMED: a barrier file that exists but is truncated, empty,
        # not an object, carries a non-boolean active or a non-string finding, or is a directory (its open
        # raises IsADirectoryError) surfaces a mutation outside the allowlist on every call, naming the file
        # as unreadable, and leaves the file as it is, while a record write stays silent (red when it reads
        # as absent or clear, or surfaces only once). ROUND 15 adds a missing findings key for both active
        # values with and without warned (red when a missing findings reads as an empty list), a non-boolean
        # warned (red when any truthy warned reads as already warned), a non-list findings string (red when
        # the list check is dropped, as "abc" iterates as strings), an extra key and a non-string ts (red
        # when the key set is not exact), and JSON nested past the recursion limit (red when its
        # RecursionError escapes the handler, which fails the call closed); the directory note must say to
        # remove the directory (red when it advises the audit, which cannot replace a directory). An absent
        # barrier and a well-formed clear one, with or without its optional keys, stay silent (red when
        # the shape check is stricter than documented), and a clean audit replaces a malformed barrier,
        # after which the mutation is silent again (the recovery path).
        _r14_src = dict(file_path=str(r.root / "src.py"), content="x")
        _r14_mal = []
        _r14_bodies = (_r14_trunc, "", "[]", json.dumps(dict(active="yes", findings=[])),
                       json.dumps(dict(active=True, findings=[1])), json.dumps(dict(active=False)),
                       json.dumps(dict(active=True)), json.dumps(dict(active=False, warned=True)),
                       json.dumps(dict(active=True, warned=True)),
                       json.dumps(dict(active=True, findings=[], warned="no")),
                       json.dumps(dict(active=False, findings="abc")),
                       json.dumps(dict(active=False, findings=[], extra=1)),
                       json.dumps(dict(active=False, findings=[], ts=5)), "[" * 200000, None)
        # ROUND 16: an exception escaping the handler (a RecursionError when the except is narrowed, for
        # example) is recorded as that body's row by its type name, so one row fails instead of the
        # exception aborting the suite and hiding the later rows.
        for _r14_body in _r14_bodies:
            if _r14_body is None:
                _r14_bar.unlink()
                _r14_bar.mkdir()
            else:
                _r14_bar.write_text(_r14_body, encoding="utf-8")
            try:
                _r14_first = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
                _r14_again = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
                _r14_rec = aiqt_hooks.orch_resume_barrier(r.payload(
                    "PreToolUse", "Write", dict(file_path=str(r.findings), content="x")))
            except Exception as exc:
                _r14_mal.append(("raised " + type(exc).__name__,))
            else:
                _r14_m = _r14_msg(_r14_first)
                _r14_mal.append((_verdict(_r14_first), _verdict(_r14_again), _verdict(_r14_rec),
                                 str(_r14_bar) in _r14_m and "unreadable or malformed" in _r14_m
                                 and (_r14_body is not None or "remove the directory" in _r14_m),
                                 _r14_bar.is_dir() if _r14_body is None
                                 else _r14_bar.read_text(encoding="utf-8") == _r14_body))
            if _r14_body is None:
                _r14_bar.rmdir()
        _r14_absent = [_verdict(aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src)))]
        for _r14_body in (dict(active=False, findings=[]), dict(active=False, findings=[], ts="t", warned=False)):
            _r14_bar.write_text(json.dumps(_r14_body), encoding="utf-8")
            _r14_res = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
            _r14_absent.append((_verdict(_r14_res), _r14_msg(_r14_res)))
        _r14_bar.write_text(_r14_trunc, encoding="utf-8")
        _r14_recover = (_verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))),
                        _verdict(aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))))
        check("barrier/malformed-reads-armed-every-call-clean-audit-clears",
              (tuple(_r14_mal), tuple(_r14_absent), _r14_recover),
              ((("warn", "warn", "allow", True, True),) * len(_r14_bodies),
               ("allow", ("allow", ""), ("allow", "")), ("allow", "allow")))
        # ROUND 15, A DIRECTORY AT THE BARRIER PATH: a clean audit cannot replace it (the rename raises
        # IsADirectoryError and the audit swallows it), so it returns allow, the directory stays, and no
        # temporary file is left beside it; the helper the doctor uses raises IsADirectoryError and leaves
        # no temporary file (red when the unlink after a failed replace is dropped). Once the directory is
        # removed, a clean audit writes a clear barrier and the mutation is silent.
        _r15_dir = []

        def _r15_tmps():
            return sorted(n for n in os.listdir(str(rsd))
                          if n.startswith("resume-barrier.json.") and n.endswith(".tmp"))
        _r14_bar.unlink()
        _r14_bar.mkdir()
        try:
            _r15_dir.append((_verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))),
                             _r14_bar.is_dir(), _r15_tmps()))
            try:
                aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=False, findings=[], ts="x",
                                                                   warned=False))
                _r15_raised = "returned"
            except OSError as exc:
                _r15_raised = type(exc).__name__
            _r15_dir.append((_r15_raised, _r14_bar.is_dir(), _r15_tmps()))
        finally:
            if _r14_bar.is_dir():
                _r14_bar.rmdir()
        _r15_dir.append((_verdict(aiqt_hooks.orch_resume_audit(r.payload("SessionStart"))),
                         json.loads(_r14_bar.read_text(encoding="utf-8")).get("active"),
                         _verdict(aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src)))))
        check("barrier/directory-not-replaced-no-temp-left-removal-clears", tuple(_r15_dir),
              (("allow", True, []), ("IsADirectoryError", True, []), ("allow", False, "allow")))
        # ROUND 15, THE WRITER REMOVES ONLY ITS OWN TEMPORARY FILE: (a) an open seam whose context exit
        # closes the real file and then raises EIO (a close error) must leave the seeded barrier
        # byte-identical and no temporary file (red when the cleanup does not cover the with block's exit);
        # (b) with os.urandom pinned so the temporary name is known, a file already at that name (not
        # created by this call) makes the "x" open raise FileExistsError, and that file must keep its
        # bytes (red when the helper unlinks a temporary name it did not create).
        _r15_tmp = []
        _r15_real_open = open

        class _R15CloseFails:
            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                return self.fh.__enter__()

            def __exit__(self, *exc):
                self.fh.__exit__(*exc)
                raise OSError(5, "injected close EIO")

        def _r15_open(path, *a, **k):
            return _R15CloseFails(_r15_real_open(path, *a, **k))
        _r14_bar.write_text(_r14_seed, encoding="utf-8")
        aiqt_hooks.open = _r15_open
        try:
            try:
                aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=False, findings=[], ts="x",
                                                                   warned=False))
                _r15_raised = "returned"
            except OSError as exc:
                _r15_raised = "raised errno " + str(exc.errno)
        finally:
            del aiqt_hooks.open
        _r15_tmp.append((_r15_raised,) + _r14_left())
        _r15_urandom = os.urandom
        _r15_foreign = Path("{}.{}.{}.tmp".format(_r14_bar, os.getpid(), "00" * 8))
        _r15_foreign.write_text("foreign", encoding="utf-8")
        os.urandom = lambda n: bytes(n)
        try:
            try:
                aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=False, findings=[], ts="x",
                                                                   warned=False))
                _r15_raised = "returned"
            except OSError as exc:
                _r15_raised = type(exc).__name__
        finally:
            os.urandom = _r15_urandom
        _r15_tmp.append((_r15_raised, _r14_bar.read_text(encoding="utf-8") == _r14_seed,
                         _r15_foreign.is_file() and _r15_foreign.read_text(encoding="utf-8") == "foreign"))
        if _r15_foreign.is_file():
            _r15_foreign.unlink()
        _r15_tmp.append(_r14_left()[1])
        check("barrier/close-error-removes-own-temp-never-foreign-temp", tuple(_r15_tmp),
              (("raised errno 5", True, []), ("FileExistsError", True, True), []))
        # ROUND 15, A STATE DIRECTORY PATH THROUGH A REGULAR FILE: with the state directory resolved under
        # a regular file, the barrier's open raises NotADirectoryError, which reads as an absent barrier:
        # the mutation is silent with no note (red when NotADirectoryError reads as armed).
        _r15_file = rsd / "r15-not-a-directory"
        _r15_file.write_text("x", encoding="utf-8")
        _r15_sd = aiqt_hooks._orch_state_dir_for_root
        aiqt_hooks._orch_state_dir_for_root = lambda root: os.path.join(str(_r15_file), "state")
        try:
            _r15_res = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
        finally:
            aiqt_hooks._orch_state_dir_for_root = _r15_sd
            _r15_file.unlink()
        check("barrier/state-dir-not-a-directory-reads-absent", (_verdict(_r15_res), _r14_msg(_r15_res)),
              ("allow", ""))
        # ROUND 16, THE BARRIER READ NEVER WAITS ON A FIFO OR READS PAST ITS BOUND: a FIFO with no writer
        # at the barrier path, a symlink to such a FIFO, a symlink to /dev/zero, a regular file one byte
        # over the bound (a clear barrier padded with spaces) and a symlink to a directory each read as
        # armed: a mutation outside the allowlist surfaces the note naming the file and the reason, never
        # the remove-the-directory note, and a repair write in the state directory is allowed with no
        # note. ROUND 17: every row runs the COMPLETE handler, both the outside and the repair call, and
        # computes its assertions inside a child capped at its own size plus 64 MiB (RLIMIT_AS) with a
        # 30-second timeout; this process only creates the file and parses the child's one JSON line, so a
        # handler that bypasses the reader (the round-15 shape, which opens and reads the barrier itself)
        # fails its row in the child (a timeout on the FIFO rows, a MemoryError on /dev/zero) and never
        # reads the file in this process. On 36d79720 (round 14, where _orch_barrier_read does not exist)
        # the check is red: both FIFO rows time out in the child, the /dev/zero and directory rows warn
        # without the not-a-regular-file reason, and the padded file reads as clear.
        _r16_hooks = os.path.dirname(os.path.abspath(aiqt_hooks.__file__))
        _r16_tools = os.path.dirname(os.path.abspath(__file__))
        _r16_child_src = (
            "import json, os, resource, sys\n"
            "sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])\n"
            "import aiqt_hooks, selftest_orch_hooks as t\n"
            "vm = int(open('/proc/self/statm').read().split()[0]) * os.sysconf('SC_PAGE_SIZE')\n"
            "hard = resource.getrlimit(resource.RLIMIT_AS)[1]\n"
            "soft = vm + (64 << 20) if hard == resource.RLIM_INFINITY else min(vm + (64 << 20), hard)\n"
            "resource.setrlimit(resource.RLIMIT_AS, (soft, hard))\n"
            "bar, reason = sys.argv[3], sys.argv[4]\n"
            "def note(res):\n"
            "    return res[1].get('systemMessage', '') if isinstance(res[1], dict) else ''\n"
            "res = aiqt_hooks.orch_resume_barrier(json.loads(sys.argv[5]))\n"
            "row = [t._verdict(res), bar in note(res) and reason in note(res)\n"
            "       and 'remove the directory' not in note(res)]\n"
            "res = aiqt_hooks.orch_resume_barrier(json.loads(sys.argv[6]))\n"
            "print(json.dumps(row + [[t._verdict(res), note(res)]]))\n")

        def _r16_child(reason):
            calls = [json.dumps(r.payload("PreToolUse", "Write", dict(file_path=fp, content="x")))
                     for fp in (str(r.root / "src.py"), str(rsd / "r16-repair.json"))]
            try:
                p = subprocess.run([sys.executable, "-I", "-B", "-c", _r16_child_src, _r16_hooks, _r16_tools,
                                    str(_r14_bar), reason] + calls, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return ("child timed out", False, ("child timed out", ""))
            try:
                verdict, ok, fix = json.loads(p.stdout)
                return (verdict, ok, tuple(fix))
            except (ValueError, TypeError):
                return ("child exit {}: {}".format(p.returncode, p.stderr.strip()[-200:]), False, ("", ""))
        _r16_target = rsd / "r16-target"
        _r16_bound = getattr(aiqt_hooks, "_ORCH_BARRIER_MAX_BYTES", 64 * 1024)  # a base without it still runs
        _r16_pad = json.dumps(dict(active=False, findings=[])) + " " * _r16_bound
        _r16_cases = (
            ("fifo", lambda: os.mkfifo(str(_r14_bar)), "(not a regular file)"),
            ("symlink-to-fifo", lambda: (os.mkfifo(str(_r16_target)), _r14_bar.symlink_to(_r16_target)),
             "(not a regular file)"),
            ("symlink-to-dev-zero", lambda: _r14_bar.symlink_to("/dev/zero"), "(not a regular file)"),
            ("oversized", lambda: _r14_bar.write_text(_r16_pad, encoding="utf-8"),
             "(larger than the {}-byte bound)".format(_r16_bound)),
            ("symlink-to-directory", lambda: (_r16_target.mkdir(), _r14_bar.symlink_to(_r16_target)),
             "(not a regular file)"))
        _r16_rows = []
        for _r16_name, _r16_make, _r16_reason in _r16_cases:
            if os.path.lexists(str(_r14_bar)):
                os.unlink(str(_r14_bar))
            _r16_make()
            try:
                _r16_rows.append((_r16_name,) + _r16_child(_r16_reason))
            finally:
                os.unlink(str(_r14_bar))
                if _r16_target.is_dir() and not _r16_target.is_symlink():
                    _r16_target.rmdir()
                elif os.path.lexists(str(_r16_target)):
                    os.unlink(str(_r16_target))
        check("barrier/non-regular-or-oversized-reads-armed-promptly-repair-allowed", tuple(_r16_rows),
              tuple((n, "warn", True, ("allow", "")) for n, _m, _s in _r16_cases))
        # A symlink to a regular armed barrier reads as that barrier: the first mutation surfaces its
        # findings, and the warned-flag write replaces the link with a regular file and leaves the target's
        # bytes unchanged. This row passes on the round-15 base as well: it pins the kept symlink policy and
        # does not discriminate any round-16 or round-17 change.
        _r16_armed = json.dumps(dict(active=True, findings=["r16 finding"], warned=False))
        _r16_target.write_text(_r16_armed, encoding="utf-8")
        _r14_bar.symlink_to(_r16_target)
        try:
            _r16_out = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
            _r16_link = (_verdict(_r16_out), "r16 finding" in _r14_msg(_r16_out), _r14_bar.is_symlink(),
                         _r14_bar.is_file() and json.loads(_r14_bar.read_text(encoding="utf-8")).get("warned"),
                         _r16_target.read_text(encoding="utf-8") == _r16_armed)
        finally:
            os.unlink(str(_r14_bar))
            os.unlink(str(_r16_target))
        check("barrier/symlink-to-regular-reads-as-target-warned-write-replaces-link", _r16_link,
              ("warn", True, False, True, True))
        # ROUND 17, A CLOSE ERROR STAYS A READER RESULT: with os.close wrapped so that closing the
        # barrier's own descriptor really closes it and then raises EIO, an outside write and a repair write
        # run through the dispatcher (aiqt_hooks.main) exit 0: the outside write warns, naming the file and
        # OSError, and the repair write is allowed with no output (red when the close error escapes the
        # reader, where the dispatcher fails the PreToolUse call closed with exit 2).
        import contextlib as _r17_ctx
        import errno as _r17_errno
        import io as _r17_io
        _r14_bar.write_text(_r14_seed, encoding="utf-8")
        _r17_st = os.stat(str(_r14_bar))
        _r17_real_close = os.close

        def _r17_close(fd):
            try:
                st = os.fstat(fd)
            except OSError:
                st = None
            _r17_real_close(fd)
            if st is not None and (st.st_dev, st.st_ino) == (_r17_st.st_dev, _r17_st.st_ino):
                raise OSError(_r17_errno.EIO, "injected close EIO")

        def _r17_main(file_path):
            out, err, stdin = _r17_io.StringIO(), _r17_io.StringIO(), sys.stdin
            sys.stdin = _r17_io.StringIO(json.dumps(r.payload(
                "PreToolUse", "Write", dict(file_path=file_path, content="x"))))
            os.close = _r17_close
            try:
                with _r17_ctx.redirect_stdout(out), _r17_ctx.redirect_stderr(err):
                    code = aiqt_hooks.main(["orch_resume_barrier"])
            finally:
                os.close = _r17_real_close
                sys.stdin = stdin
            note = json.loads(out.getvalue()).get("systemMessage", "") if out.getvalue().strip() else ""
            return (code, bool(note), str(_r14_bar) in note and "(OSError)" in note, err.getvalue())
        check("barrier/close-error-is-a-reader-result-never-exit-2",
              (_r17_main(str(r.root / "src.py")), _r17_main(str(rsd / "r17-repair.json")),
               _r14_bar.read_text(encoding="utf-8") == _r14_seed),
              ((0, True, True, ""), (0, False, False, ""), True))
        # ROUND 17, A DIRECTORY IS NAMED ONLY WHERE THE LSTAT MATCHES WHAT WAS OPENED: given the fstat of
        # what the reader opened, _orch_barrier_nonregular names a directory only where the lstat of the
        # path has the same st_dev and st_ino (red when it trusts the lstat alone, or takes no fstat).
        _r17_da, _r17_db = rsd / "r17-dir-a", rsd / "r17-dir-b"
        _r17_da.mkdir()
        _r17_db.mkdir()
        try:
            _r17_dir = tuple(aiqt_hooks._orch_barrier_nonregular(str(_r17_da), *st) for st in (
                (os.stat(str(_r17_da)),), (os.stat(str(_r17_db)),), ()))
        except (TypeError, AttributeError) as exc:
            _r17_dir = "raised " + type(exc).__name__
        finally:
            _r17_da.rmdir()
            _r17_db.rmdir()
        check("barrier/directory-named-only-where-lstat-matches-opened", _r17_dir,
              ("not a regular file: a directory", "not a regular file", "not a regular file: a directory"))
        # ROUND 17, A WRITTEN BARRIER ALWAYS FITS THE READER'S BOUND: 400 findings of about 230 bytes (the
        # 92458-byte shape a forced-exit log produced) are stored as their first findings plus one line
        # counting the rest, within the bound; the file reads ok, well-formed and armed; the first mutation
        # surfaces the first finding and records warned, so the next mutation is silent; a single
        # 100000-character finding is stored cut to 4000 characters; and an object whose other keys alone
        # exceed the bound raises ValueError and leaves the previous barrier byte-identical (red when the
        # writer stores the full list, which reads back as larger than the bound).
        _r17_many = ["forced exit {:04d}: {}".format(i, "x" * 210) for i in range(400)]
        _r17_rows = []
        aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=True, findings=_r17_many, ts="r17",
                                                           warned=False))
        _r17_raw = _r14_bar.read_bytes()  # a regular file this check just wrote
        _r17_bar = json.loads(_r17_raw.decode("utf-8"))
        _r17_kept = _r17_bar["findings"][:-1]
        _r17_rows.append((len(_r17_raw) <= _r16_bound, aiqt_hooks._orch_barrier_well_formed(_r17_bar)
                          and _r17_bar["active"], 0 < len(_r17_kept) < 400,
                          _r17_kept == _r17_many[:len(_r17_kept)],
                          _r17_bar["findings"][-1].startswith(
                              "{} more finding(s) not stored".format(400 - len(_r17_kept)))))
        _r17_first = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
        _r17_again = aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src))
        _r17_rows.append((_verdict(_r17_first), "forced exit 0000" in _r14_msg(_r17_first),
                          json.loads(_r14_bar.read_text(encoding="utf-8")).get("warned"), _verdict(_r17_again)))
        _r17_big = "y" * 100000
        aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=True, findings=[_r17_big], warned=False))
        _r17_rows.append(json.loads(_r14_bar.read_text(encoding="utf-8")).get("findings")
                         == [_r17_big[:4000] + " (cut)"])
        _r14_bar.write_text(_r14_seed, encoding="utf-8")
        try:
            aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=True, findings=[], ts="z" * 70000))
            _r17_raised = "returned"
        except ValueError as exc:
            _r17_raised = type(exc).__name__
        _r17_rows.append((_r17_raised,) + _r14_left())
        _r14_bar.unlink()
        check("barrier/written-barrier-fits-bound-first-findings-plus-count", tuple(_r17_rows),
              ((True, True, True, True, True), ("warn", True, True, "allow"), True,
               ("ValueError", True, [])))
        # ROUND 18, A RE-FIT CARRIES THE STORED COUNT FORWARD: the 400-finding barrier above, stored as its
        # first findings plus a count line, is written again with a "ts" large enough that it no longer
        # fits; the new count line counts every finding not stored (400 less the findings kept), never the
        # old count line as one finding (red when the re-fit keeps the old line as a finding to count).
        aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(active=True, findings=_r17_many, warned=False))
        _r18_once = json.loads(_r14_bar.read_text(encoding="utf-8"))
        aiqt_hooks._orch_barrier_write(str(_r14_bar), dict(_r18_once, ts="t" * 20000))
        _r18_raw = _r14_bar.read_bytes()  # a regular file this check just wrote
        _r18_twice = json.loads(_r18_raw.decode("utf-8"))
        _r18_kept = _r18_twice["findings"][:-1]
        check("barrier/refit-carries-stored-count-forward",
              (len(_r18_raw) <= _r16_bound, 0 < len(_r18_kept) < len(_r18_once["findings"]) - 1,
               _r18_kept == _r17_many[:len(_r18_kept)],
               _r18_twice["findings"][-1].startswith(
                   "{} more finding(s) not stored here (".format(400 - len(_r18_kept)))),
              (True, True, True, True))
        _r14_bar.unlink()
        # ROUND 18, THE READER COMPARES THE LSTAT WITH WHAT IT OPENED: a directory at the barrier path is
        # renamed away right after the reader's os.open of it and a new directory made at the path (the
        # same device, another inode), so the reader's fstat and its lstat disagree; _orch_barrier_read
        # names it "not a regular file" and the handler gives the generic note, never the
        # remove-the-directory note. Without the swap the same directory is named a directory and gets
        # that note. Red when the reader calls _orch_barrier_nonregular(path) without its fstat. The swap
        # stays on one filesystem, so this vector exercises only the inode half of the reader's
        # (st_dev, st_ino) comparison; the next vector tests the device half with a faked fstat result.
        _r18_real_open = os.open
        _r18_moved = rsd / "r18-dir-moved"

        def _r18_swap_open(path, *a, **k):
            fd = _r18_real_open(path, *a, **k)
            if path == str(_r14_bar) and not os.path.lexists(str(_r18_moved)):
                os.rename(str(_r14_bar), str(_r18_moved))
                os.mkdir(str(_r14_bar))
            return fd

        def _r18_dir_row(swap):
            _r14_bar.mkdir()
            os.open = _r18_swap_open if swap else _r18_real_open
            try:
                read = aiqt_hooks._orch_barrier_read(str(_r14_bar))
                pair = None
                if swap:
                    a_st, b_st = os.lstat(str(_r18_moved)), os.lstat(str(_r14_bar))
                    pair = (a_st.st_dev == b_st.st_dev, a_st.st_ino != b_st.st_ino)
            finally:
                os.open = _r18_real_open
                for d in (_r18_moved, _r14_bar):
                    if d.is_dir():
                        d.rmdir()
            _r14_bar.mkdir()
            os.open = _r18_swap_open if swap else _r18_real_open
            try:
                note = _r14_msg(aiqt_hooks.orch_resume_barrier(r.payload("PreToolUse", "Write", _r14_src)))
            finally:
                os.open = _r18_real_open
                for d in (_r18_moved, _r14_bar):
                    if d.is_dir():
                        d.rmdir()
            return (pair, read, "(not a regular file)" in note, "remove the directory" in note)
        check("barrier/reader-names-directory-only-where-lstat-matches-its-fstat",
              (_r18_dir_row(True), _r18_dir_row(False)),
              (((True, True), ("bad", "not a regular file"), True, False),
               (None, ("bad", "not a regular file: a directory"), False, True)))
        # ROUND 20, THE DEVICE HALF OF THAT COMPARISON: _orch_barrier_nonregular is handed a directory's
        # own lstat result as the opened file (named a directory), then the same inode on another device
        # and the same device with another inode (both "not a regular file"), and no opened result
        # (named a directory). A fake stands in for the fstat of a second filesystem, which a self-test
        # cannot mount. Red when the comparison drops st_dev or st_ino.
        _r14_bar.mkdir()
        try:
            _r20_st = os.lstat(str(_r14_bar))
            import types as _r20_types
            _r20_fake = _r20_types.SimpleNamespace
            _r20_names = tuple(aiqt_hooks._orch_barrier_nonregular(str(_r14_bar), o) for o in (
                _r20_st, _r20_fake(st_dev=_r20_st.st_dev + 1, st_ino=_r20_st.st_ino),
                _r20_fake(st_dev=_r20_st.st_dev, st_ino=_r20_st.st_ino + 1), None))
        finally:
            _r14_bar.rmdir()
        check("barrier/nonregular-names-directory-only-where-device-and-inode-match", _r20_names,
              ("not a regular file: a directory", "not a regular file", "not a regular file",
               "not a regular file: a directory"))
        # ROUND 18, THE DESCRIPTOR IS CLOSED EXACTLY ONCE AND AN EARLIER BAD RESULT WINS: with os.close
        # wrapped so that closing the barrier's descriptor really closes it, is counted, and then raises
        # EIO, a regular barrier reads ('bad', 'OSError'), and a directory and a FIFO at the barrier path
        # read as their not-a-regular-file result, never the close error; each row closes the barrier's
        # descriptor exactly once (red when the reader retries the close after an error, or lets the close
        # error override an earlier bad result).
        _r18_fds, _r18_calls = [], []
        _r18_real_close = os.close

        def _r18_open(path, *a, **k):
            fd = _r18_real_open(path, *a, **k)
            if path == str(_r14_bar):
                _r18_fds.append(fd)
            return fd

        def _r18_close(fd):
            if fd not in _r18_fds:
                return _r18_real_close(fd)
            _r18_calls.append(fd)
            _r18_real_close(fd)
            raise OSError(_r17_errno.EIO, "injected close EIO")

        def _r18_close_row(make):
            make()
            del _r18_fds[:], _r18_calls[:]
            os.open, os.close = _r18_open, _r18_close
            try:
                try:
                    res = aiqt_hooks._orch_barrier_read(str(_r14_bar))
                except Exception as exc:
                    res = "raised " + type(exc).__name__
            finally:
                os.open, os.close = _r18_real_open, _r18_real_close
                if _r14_bar.is_dir() and not _r14_bar.is_symlink():
                    _r14_bar.rmdir()
                else:
                    os.unlink(str(_r14_bar))
            return (res, len(_r18_fds), len(_r18_calls))
        check("barrier/close-once-never-retried-earlier-bad-result-wins",
              (_r18_close_row(lambda: _r14_bar.write_text(_r14_seed, encoding="utf-8")),
               _r18_close_row(_r14_bar.mkdir), _r18_close_row(lambda: os.mkfifo(str(_r14_bar)))),
              ((("bad", "OSError"), 1, 1), (("bad", "not a regular file: a directory"), 1, 1),
               (("bad", "not a regular file"), 1, 1)))
        # ROUND 18, THE OMITTED-FINDINGS LINE POINTS AT EVIDENCE THAT SURVIVES: a fixture with 400
        # forced-exit records (each a finding of about 200 bytes) and a handoff naming another branch. The
        # SessionStart audit stores the first findings plus a count line; the doctor's --resume-audit then
        # no longer lists any omitted forced-exit finding (each is raised once), so the line must not send
        # the operator there for them. The line names the forced-exit log by its path, and that file holds
        # every one of the 400 records in full, the omitted ones included; the doctor prints the recurring
        # handoff finding again, as the line says. Red when the line only says to run the doctor.
        import re as _r18_re
        _r18_fx = Fixture(tmp, "r18-omitted")
        _r18_fx.handoff.write_text("Branch: r18-other-branch\n", encoding="utf-8")
        _r18_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r18_fx.root)))
        _r18_rows = [dict(key="r18-key-{:04d}".format(i), ts="r18-ts-{:04d}".format(i),
                          open_ids=["R18-{:04d}-{}".format(i, j) + "x" * 24 for j in range(3)])
                     for i in range(400)]
        for _r18_row in _r18_rows:
            aiqt_hooks._orch_append_jsonl(str(_r18_sd / "forced-exit.jsonl"), _r18_row)
        _r18_old_env = os.environ.pop(_doc_env, None)
        try:
            _r18_ss = aiqt_hooks.orch_resume_audit(_r18_fx.payload("SessionStart"))
            _r18_bar = json.loads((_r18_sd / "resume-barrier.json").read_text(encoding="utf-8"))
            _doc.repo_root = lambda: Path(_r18_fx.root)
            _r18_out, _r18_argv = io.StringIO(), sys.argv
            sys.argv = [_r18_argv[0], "--resume-audit"]
            try:
                with contextlib.redirect_stdout(_r18_out):
                    _r18_dcode = _doc.main()
            finally:
                sys.argv = _r18_argv
                _doc.repo_root = _doc_root
        finally:
            if _r18_old_env is not None:
                os.environ[_doc_env] = _r18_old_env
        _r18_line = _r18_bar["findings"][-1]
        _r18_stored = [f for f in _r18_bar["findings"][:-1] if "forced_unresolved at r18-ts-" in f]
        _r18_omitted = _r18_rows[len(_r18_stored):]
        _r18_m = _r18_re.search(r"read every forced-exit record in full in (.+?) \(an ignored", _r18_line)
        _r18_named = _r18_m.group(1) if _r18_m else None
        _r18_log, _r18_logbad = (aiqt_hooks._orch_read_jsonl(_r18_named) if _r18_named else (None, 0))
        _r18_doc_out = _r18_out.getvalue()
        check("barrier/omitted-findings-line-names-surviving-forced-exit-log",
              (_verdict(_r18_ss), 0 < len(_r18_stored) < 400,
               _r18_line.startswith("{} more finding(s) not stored here (".format(len(_r18_omitted))),
               _r18_dcode, any(row["ts"] in _r18_doc_out for row in _r18_omitted),
               "handoff names branch r18-other-branch" in _r18_doc_out,
               _r18_named == str(_r18_sd / "forced-exit.jsonl"),
               _r18_log == _r18_rows and all(row in _r18_log for row in _r18_omitted),
               # the named log must parse whole: a malformed row is a failure, never a silent skip
               _r18_logbad,
               "tools/orch_doctor.py --resume-audit" in _r18_line and "to list them all" not in _r18_line),
              ("warn", True, True, 1, False, True, True, True, 0, True))
        # ROUND 19, THE OMITTED-FINDINGS LINE NAMES THE LASTING ESCAPE RECORD: the count line a barrier
        # too large to store gets (_orch_barrier_fit) is read for the files it names, and each one is read
        # back. Two ignored escape sentinels in a row (T1, then T2) are each recorded, raised by the resume
        # probe and renamed to escape-spoof.json.surfaced: the guard-events.jsonl the line names holds an
        # escape-spoof row for T1 and for T2, and the .surfaced file it names holds T2, never T1 (the line
        # says it holds only the latest sentinel whose rename succeeded). The line says a forced-exit
        # finding is normally raised once, at least once if recording it fails, never "only once", and
        # that the guard-events row is the record only where it was written, a row that could not be
        # written having been warned about. Red when the line names .surfaced as keeping the escape
        # records, names a file the hook does not write, or drops the warned-about qualification.
        _r19_fx = Fixture(tmp, "r19-escape")
        _r19_root = str(_r19_fx.root)
        _r19_sd = Path(aiqt_hooks._orch_state_dir_for_root(_r19_root))
        _r19_line = aiqt_hooks._orch_barrier_fit(
            dict(active=True, findings=["r19 " + "z" * 3000] * 40, warned=False), str(_r19_sd))["findings"][-1]

        def _r19_named(pattern):
            m = _r18_re.search(pattern, _r19_line)
            return m.group(1) if m else None
        _r19_events = _r19_named(
            r"its row of kind escape-spoof in (.+?) where that row was written, and a row")
        _r19_warned = ("where that row was written, and a row that could not be written was warned about "
                       "when the sentinel was ignored, with a request to record it manually;") in _r19_line
        _r19_kept = _r19_named(
            r"record it manually; (.+?) holds only the latest sentinel whose rename succeeded")
        _r19_left = _r19_named(r"a failed rename leaves it at (.+?), where the next audit raises it again")

        def _r19_detail(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh).get("detail")
            except (TypeError, OSError, ValueError):
                return None

        def _r19_spoofs():
            rows = aiqt_hooks._orch_read_jsonl(_r19_events)[0] if _r19_events else None
            return [row.get("detail") for row in rows or [] if row.get("kind") == "escape-spoof"]

        def _r19_raise(detail):
            warn = aiqt_hooks._orch_record_escape_spoof(_r19_root, detail)
            return (warn, any(detail in f for f in aiqt_hooks._orch_pending_artefact_findings(_r19_root)))
        check("barrier/omitted-findings-line-escape-record-survives-two-sentinels",
              (aiqt_hooks._ORCH_BARRIER_REST_RE.match(_r19_line) is not None, _r19_raise("r19-T1"),
               _r19_raise("r19-T2"), _r19_events == str(_r19_sd / "guard-events.jsonl"), _r19_spoofs(),
               _r19_kept == str(_r19_sd / "escape-spoof.json.surfaced"), _r19_detail(_r19_kept),
               "normally raised once (at least once if recording that it was raised fails)" in _r19_line,
               "raised only once" in _r19_line or "is kept in" in _r19_line, _r19_warned),
              (True, ("", True), ("", True), True, ["r19-T1", "r19-T2"], True, "r19-T2", True, False, True))
        # ROUND 19, A FAILED RENAME LEAVES THE SENTINEL WHERE THE LINE SAYS: with escape-spoof.json.surfaced
        # a directory, a third sentinel (T3) is recorded and raised but its rename fails; the .surfaced
        # path the line names is that directory (it holds no sentinel), the escape-spoof.json the line names
        # still holds T3, the next probe raises T3 again, and the guard-events.jsonl the line names holds
        # T1, T2 and T3 (red when the line names another file for the sentinel a failed rename leaves).
        # The probe's rename target is checked by the vector above; here it is replaced by a directory
        # whatever that vector found, so a mutant that never renames still reaches this check by name.
        _r19_surf = _r19_sd / "escape-spoof.json.surfaced"
        if os.path.lexists(str(_r19_surf)) and not _r19_surf.is_dir():
            _r19_surf.unlink()
        if not _r19_surf.is_dir():
            _r19_surf.mkdir()
        check("barrier/omitted-findings-line-escape-record-failed-rename",
              (_r19_raise("r19-T3"), bool(_r19_kept) and os.path.isdir(_r19_kept),
               _r19_left == str(_r19_sd / "escape-spoof.json"), _r19_detail(_r19_left),
               any("r19-T3" in f for f in aiqt_hooks._orch_pending_artefact_findings(_r19_root)),
               _r19_spoofs()),
              (("", True), True, True, "r19-T3", True, ["r19-T1", "r19-T2", "r19-T3"]))
        if _r19_surf.is_dir():
            _r19_surf.rmdir()
        # ROUND 20, A FAILED GUARD-EVENTS APPEND IS WARNED ABOUT AND LEAVES NO ROW: in a fresh state
        # directory, guard-events.jsonl is a directory while sentinel T1 is recorded and raised, then the
        # directory is removed and T2 is recorded and raised. T1's recorder returns the warning naming
        # guard-events FAILED and escape-spoof.json ok and asking for a manual record; T2's returns "".
        # The guard-events.jsonl the line names then holds T2 only, and the .surfaced file holds T2: no
        # lasting record of T1 exists, which is what the line and ORCHESTRATION.md say ("where that row
        # was written"). Red when the recorder warns only on a failed escape-spoof.json write.
        _r20_fx = Fixture(tmp, "r20-escape")
        _r20_root = str(_r20_fx.root)
        _r20_sd = Path(aiqt_hooks._orch_state_dir_for_root(_r20_root))
        _r20_ge = _r20_sd / "guard-events.jsonl"
        if os.path.lexists(str(_r20_ge)) and not _r20_ge.is_dir():
            _r20_ge.unlink()
        _r20_ge.mkdir(parents=True, exist_ok=True)

        def _r20_raise(detail):
            warn = aiqt_hooks._orch_record_escape_spoof(_r20_root, detail)
            return (warn, [f for f in aiqt_hooks._orch_pending_artefact_findings(_r20_root) if "r20-" in f])

        def _r20_spoofs():
            rows = aiqt_hooks._orch_read_jsonl(str(_r20_ge))[0] if _r20_ge.is_file() else None
            return [row.get("detail") for row in rows or [] if row.get("kind") == "escape-spoof"]
        _r20_t1 = _r20_raise("r20-T1")
        if _r20_ge.is_dir():
            _r20_ge.rmdir()
        _r20_t2 = _r20_raise("r20-T2")
        check("barrier/omitted-findings-line-escape-record-guard-events-failure",
              (_r20_t1[0], any("r20-T1" in f for f in _r20_t1[1]), _r20_t2[0],
               any("r20-T2" in f for f in _r20_t2[1]), _r20_spoofs(),
               _r19_detail(str(_r20_sd / "escape-spoof.json.surfaced"))),
              ("Additionally, an ignored escape sentinel could not be fully recorded (guard-events FAILED, "
               "escape-spoof.json ok); record the spoof manually before resuming (nocncl).", True, "", True,
               ["r20-T2"], "r20-T2"))
        # ROUND 20, A LATER SENTINEL BEFORE THE AUDIT OVERWRITES escape-spoof.json: T3 and then T4 are
        # recorded with no resume probe between them; the next probe raises T4 and never T3, a second
        # probe raises neither, and guard-events.jsonl holds a row for each (the docs say the earlier one
        # then keeps only its guard-events row, so it is not "surfaced once").
        _r20_w3 = aiqt_hooks._orch_record_escape_spoof(_r20_root, "r20-T3")
        _r20_w4 = aiqt_hooks._orch_record_escape_spoof(_r20_root, "r20-T4")
        _r20_p1 = [f for f in aiqt_hooks._orch_pending_artefact_findings(_r20_root) if "r20-" in f]
        _r20_p2 = [f for f in aiqt_hooks._orch_pending_artefact_findings(_r20_root) if "r20-" in f]
        check("barrier/omitted-findings-line-escape-record-overwrite-before-audit",
              (_r20_w3, _r20_w4, any("r20-T4" in f for f in _r20_p1), any("r20-T3" in f for f in _r20_p1),
               _r20_p2, _r20_spoofs()),
              ("", "", True, False, [], ["r20-T2", "r20-T3", "r20-T4"]))

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
        # QA round 10: every deny branch is relieved at the loop bound or the cap and above it, never below:
        # one below, at, one above, and the largest valid count, for each stop and each schedule deny branch.
        _core_held = dict(actionable=[("A", "t", "no blocker")])
        _core_branches = [
            ("stop", "counter", aiqt_hooks._ORCH_LOOP_BOUND, dict(base, **_core_held)),
            ("stop", "counter", aiqt_hooks._ORCH_LOOP_BOUND, dict(base, enum_status="ENUMERATOR_ERROR")),
            ("stop", "counter", aiqt_hooks._ORCH_LOOP_BOUND,
             dict(base, cannot_evaluate=[("CE", "cannot-evaluate", "held")])),
            ("schedule", "schedule_denials", aiqt_hooks._ORCH_SCHEDULE_CAP,
             dict(base, kind="schedule_idle", basis_unchanged=True, enum_status="ENUMERATOR_ERROR")),
            ("schedule", "schedule_denials", aiqt_hooks._ORCH_SCHEDULE_CAP,
             dict(base, kind="schedule_idle", basis_unchanged=True,
                  cannot_evaluate=[("CE", "cannot-evaluate", "held")])),
            ("schedule", "schedule_denials", aiqt_hooks._ORCH_SCHEDULE_CAP, wake_ctx),
            ("schedule", "schedule_denials", aiqt_hooks._ORCH_SCHEDULE_CAP,
             dict(base, kind="schedule_idle", basis_unchanged=True, **_core_held)),
        ]
        check("core/bound-and-cap-relieve-above-the-limit-not-only-at-it",
              [[aiqt_hooks.decide_yield(dict(ctx, **{key: n}))[0]
                for n in (limit - 1, limit, limit + 1, aiqt_hooks._ORCH_COUNTER_MAX)]
               for _kind, key, limit, ctx in _core_branches],
              [["DENY"] + ["ALLOW_WITH_FINDINGS"] * 3] * len(_core_branches))

        # Load-shed characterization pins (the load-shed mechanism itself was declined): decide_yield
        # sets no live-worker count floor, and a shed recorded as a fresh external blocker on queued
        # items exhausts the set and licenses a stop. They pin CURRENT behaviour, not an endorsement of
        # it; a reword of cntdef.8 or secres.7 should prompt a recheck that the load-shed advice holds.
        _ls_live = dict(base, waiting=[("A", "tracked-task", "t1")])
        _ls_queued = dict(_ls_live, actionable=[("B", "t", "no blocker recorded")])
        _ls_idle = dict(_ls_live, kind="schedule_idle", wake_named=True)
        v, _r, _d = aiqt_hooks.decide_yield(dict(_ls_live))
        check("core/one-live-worker-stop-allows", v, "ALLOW")
        v, _r, _d = aiqt_hooks.decide_yield(dict(_ls_queued))
        check("core/one-live-plus-queued-denies", (v, "Two legal exits" in _r), ("DENY", True))
        v, _r, _d = aiqt_hooks.decide_yield(dict(_ls_idle))
        check("core/one-live-named-idle-allows", v, "ALLOW")
        v, _r, _d = aiqt_hooks.decide_yield(dict(_ls_idle, actionable=_ls_queued["actionable"]))
        check("core/one-live-queued-idle-denies", v, "DENY")
        v, _r, _d = aiqt_hooks.decide_yield(dict(_ls_queued, counter=2))
        check("core/queued-loop-bound-findings", v, "ALLOW_WITH_FINDINGS")
        # the hazard: classify_backlog marks the shed item blocked, so decide_yield sees no actionable
        # item and ALLOWs the stop (a concurrency cap read as whole-set exhaustion)
        _ls_cls = aiqt_hooks.classify_backlog(
            [item("A", blocker={"kind": "tracked-task", "ref": "t1"}),
             item("B", blocker={"kind": "external", "ref": "load-shed",
                                "evidence": "host load shed to one workstream",
                                "observed_at_utc": now_iso(0)})],
            {"t1"}, True, "", {})
        v, _r, _d = aiqt_hooks.decide_yield(dict(base, **_ls_cls))
        check("core/external-shed-blocker-exhausts",
              (_ls_cls["actionable"], _ls_cls["blocked"], v,
               _r.startswith("no actionable item remains")),
              ([], [("B", "external", "load-shed")], "ALLOW", True))

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
            aiqt_hooks._orch_write_json_atomic = lambda p, o: "PermissionError"
            aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        finally:
            aiqt_hooks._orch_write_json_atomic = _o_wja
        check("attest/failed-write-invalidates-snapshot",
              (asd / "attestations-validated.json").exists(), False)
        a.set_items([ext("AT-I1", "ci")])
        a.set_turn_state({})
        check("attest/failed-write-holds", _verdict(astop()), "block2")
        # the failed snapshot write is itself a finding, so the resume audit's warning names it (the findings
        # list is that warning's text), whatever happens to the audit's own guard-events row
        try:
            aiqt_hooks._orch_write_json_atomic = lambda p, o: "PermissionError"
            _at_fail = aiqt_hooks._orch_validate_attestations(areg, str(a.root))
        finally:
            aiqt_hooks._orch_write_json_atomic = _o_wja
        # REGRESSION PIN: it pins behaviour the 88e0b86 hooks already had, not a change of this PR; it fails on
        # those hooks now only because its patched writer returns this round's failure detail, not False
        check("attest/failed-write-finding-names-it",
              any(x.startswith("the attestation snapshot could not be written") for x in _at_fail), True)
        # a broken chain is held, never read as a smaller clean register
        at_reg.write_text('{"seq": 1, "id": "AT-1", "prev": "beef"}\n', encoding="utf-8")
        check("attest/broken-chain-finding",
              bool(aiqt_hooks._orch_validate_attestations(areg, str(a.root))), True)
        a.set_turn_state({})
        check("attest/broken-chain-stop-holds", _verdict(astop()), "block2")
        # the hooks mirror carries the gate's strict anchor shape: a seq of 0, -1 or false
        # (each formerly int()-coerced past the digest comparison), a short digest and a
        # non-object anchor are each a HOLD, never ok
        mirror_tip = chained(
            {"seq": 1, "id": "AT-1", "ts": now_iso(0), "status": "accepted",
             "mistake": "attests ci", "evidence": "run pending", "rule": "corexc",
             "guardrail": "n/a", "ref": "ci", "check_ref": "seed.txt"})
        at_reg.write_text(mirror_tip, encoding="utf-8")
        at_anchor_file = Path(str(at_reg) + ".anchor")
        authority = lambda: aiqt_hooks._orch_register_authority(str(a.root), str(at_reg))[0]
        at_anchor_file.write_text(json.dumps({"seq": 0, "digest": "0" * 64}),
                                  encoding="utf-8")
        check("attest/anchor-seq-zero-holds", authority(), "hold")
        at_anchor_file.write_text(json.dumps({"seq": -1, "digest": "0" * 64}),
                                  encoding="utf-8")
        check("attest/anchor-seq-negative-holds", authority(), "hold")
        at_anchor_file.write_text(json.dumps({"seq": False, "digest": "0" * 64}),
                                  encoding="utf-8")
        check("attest/anchor-seq-false-holds", authority(), "hold")
        at_anchor_file.write_text(json.dumps({"seq": 1, "digest": "a" * 63}),
                                  encoding="utf-8")
        # the SHAPE check must own these two holds (the specific malformed-anchor
        # classification): a short digest already held on the base code through the digest
        # COMPARISON, and a non-object anchor through the unreadable-authority catch, so the
        # verdict alone does not discriminate the strict shape check.
        # attest/anchor-digest-short-holds and attest/anchor-non-object-holds are CONTROLS
        # (they pass without the strict shape check); the paired -names-malformed checks are
        # the regression checks
        short_auth = aiqt_hooks._orch_register_authority(str(a.root), str(at_reg))
        check("attest/anchor-digest-short-holds", short_auth[0], "hold")
        check("attest/anchor-digest-short-names-malformed",
              "anchor is malformed" in str(short_auth[1]), True)
        at_anchor_file.write_text(json.dumps([1, 2]), encoding="utf-8")
        nonobj_auth = aiqt_hooks._orch_register_authority(str(a.root), str(at_reg))
        check("attest/anchor-non-object-holds", nonobj_auth[0], "hold")
        check("attest/anchor-non-object-names-malformed",
              "anchor is malformed" in str(nonobj_auth[1]), True)
        # attest/anchor-valid-after-shape-vectors-ok is a CONTROL: a valid anchor restored after
        # the shape vectors answers ok, so the holds above come from the shapes, not from a
        # fixture that never answers ok
        at_anchor(mirror_tip)
        check("attest/anchor-valid-after-shape-vectors-ok", authority(), "ok")
        # the anchor is opened ONCE without following a final symlink and without blocking, and
        # its type is checked on the opened descriptor: a live symlink to a VALID anchor (which
        # the base code followed to ok), a live symlink to a directory (held on the base code
        # too, as an unreadable target, so the hold must name the link) and a FIFO (on which
        # the base code's open blocked; TIMED, so that is a named red, never a hung suite) are
        # each a HOLD
        mirror_anchor = at_anchor_file.read_text(encoding="utf-8")
        anchor_store = a.root / "anchor-store.json"
        anchor_store.write_text(mirror_anchor, encoding="utf-8")
        anchor_store_dir = a.root / "anchor-store-dir"
        anchor_store_dir.mkdir()
        at_anchor_file.unlink()
        at_anchor_file.symlink_to(anchor_store)
        check("attest/anchor-live-symlink-holds", authority(), "hold")
        at_anchor_file.unlink()
        at_anchor_file.symlink_to(anchor_store_dir)
        dir_link_auth = aiqt_hooks._orch_register_authority(str(a.root), str(at_reg))
        check("attest/anchor-directory-symlink-holds-naming-link",
              (dir_link_auth[0], "symlink" in str(dir_link_auth[1])), ("hold", True))
        at_anchor_file.unlink()
        os.mkfifo(str(at_anchor_file))
        check("attest/anchor-fifo-holds-without-blocking", _leasehb_timed(authority, seconds=10),
              (True, "hold"))
        at_anchor_file.unlink()
        # a CONTROL: the same anchor as a regular file answers ok, so the three holds above
        # come from the entry types, not from the anchor's content
        at_anchor_file.write_text(mirror_anchor, encoding="utf-8")
        check("attest/anchor-regular-after-type-vectors-ok", authority(), "ok")
        # the tracked DECISION fails closed, as in the gate: a root where git ls-files answers
        # neither 0 (tracked) nor 1 (not tracked), here a non-repository root (exit 128), is a
        # HOLD, never a fall-through into the anchor leg
        nonrepo = tmp / "authority-nonrepo"
        nonrepo.mkdir()
        nr_reg = nonrepo / "attestations.jsonl"
        nr_reg.write_text(mirror_tip, encoding="utf-8")
        nr_lines = mirror_tip.splitlines()
        Path(str(nr_reg) + ".anchor").write_text(json.dumps(
            {"seq": len(nr_lines), "digest": _hashlib.sha256(
                nr_lines[-1].encode("utf-8")).hexdigest()}), encoding="utf-8")
        check("attest/git-failure-tracked-decision-holds",
              aiqt_hooks._orch_register_authority(str(nonrepo), str(nr_reg))[0], "hold")

        # ---------- GD-127: the systemic-lapse lifecycle (verdict-preservation legs) ----------
        # The appender validates in the register gate's own root, resolved from the gate's
        # location; install the appender (with the gate modules it imports) inside the fixture
        # repository so that root IS the fixture, exactly like an adopter's installed tool.
        fixture_tools = a.root / "tools"
        fixture_tools.mkdir()
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(repo_root() / "tools" / tool_name), str(fixture_tools / tool_name))
        regtool = str(fixture_tools / "orch_register.py")

        def mr_append(rid, check_ref, *extra):
            subprocess.run([sys.executable, "-I", "-B", regtool, "append",
                            "--register", str(mr_reg),
                            "--id", rid, "--mistake", "premature wind-down claim",
                            "--evidence", "resume-audit finding", "--rule", "cntdef",
                            "--guardrail", "stop-guard hardening", "--klass", "systemic-lapse",
                            "--check-ref", check_ref, *extra],
                           check=True, capture_output=True, timeout=30)

        # the register's first row: --bootstrap establishes the anchor the later append needs
        mr_append("MR-1", "seed.txt", "--bootstrap")
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
                lambda p, o: "PermissionError" if str(p).endswith("checkpoint-init.marker") else _o_wja3(p, o))
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
        # the artefact must parse whole: a malformed row is a failure, never a silent skip
        check("forced/artefact-names-open-ids", (frows[-1].get("open_ids"), _fbad), (["FX-1"], 0))
        check("forced/guard-event-kind",
              '"forced_unresolved"' in (dsd / "guard-events.jsonl").read_text(encoding="utf-8"),
              True)
        # the next resume audit surfaces the pending record (normally once) and arms the warn-first barrier
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

        # THE DOCUMENTED STOP FIELD: the hooks reference (Stop input) sends stop_hook_active, true when Claude
        # Code is already continuing as a result of a stop hook, and advises a blocking Stop hook to check it.
        # The stop guard honours it as a loop signal below its own counter: an actionable backlog that denies
        # with no prior denial yields with findings (the recorded forced exit) once the payload says the stop
        # continues a stop-hook block. Only the JSON true is that signal; a string "true" and an explicit
        # false still deny.
        _sha = Fixture(tmp, "stop-hook-active")
        _sha_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_sha.root)))
        _sha.set_items([item("SHA-1")])
        for _sha_value, _sha_id in ((False, "stop-hook-active/false-denies"),
                                    ("true", "stop-hook-active/string-denies")):
            _sha.set_turn_state({})
            check(_sha_id, _verdict(aiqt_hooks.orch_stop_guard(
                _sha.payload("Stop", extra={"stop_hook_active": _sha_value}))), "block2")
        _sha.set_turn_state({})
        check("stop-hook-active/true-yields-with-findings", _verdict(aiqt_hooks.orch_stop_guard(
            _sha.payload("Stop", extra={"stop_hook_active": True}))), "warn")
        _sha_rows, _sha_bad = aiqt_hooks._orch_read_jsonl(str(_sha_sd / "forced-exit.jsonl"))
        # the artefact must hold exactly the expected row and parse whole: a malformed row is a failure
        check("stop-hook-active/forced-exit-recorded",
              ([r.get("open_ids") for r in _sha_rows], _sha_bad), ([["SHA-1"]], 0))

        # ROUND 21, A FAILED GUARD-EVENTS APPEND ON A DENY IS WARNED ABOUT AND THE DENY HOLDS: with
        # guard-events.jsonl a directory (a real failed append, no patched seam), the shared Stop and
        # TeammateIdle path still blocks (exit 2) with the recording-failure warning on its block reason (it
        # has no banner); both scheduling denies (the backlog deny and the quiet-claim deny) and the
        # unattended-ask deny still deny, with the warning in the deny reason and the banner; a bound-forced
        # checkpoint drop under a clean ALLOW is warned about in the banner. Red on b02061bd, where each of
        # these dropped the failed row silently. The control leg (the directory removed) shows the warning
        # is absent when the row is written.
        _r21 = Fixture(tmp, "r21-events")
        _r21_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r21.root)))
        _r21_ge = _r21_sd / "guard-events.jsonl"
        _r21_ge.mkdir(parents=True, exist_ok=True)
        _r21.set_items([item("R21-1")])

        def _r21_block(result, event):
            code, obj, err = result
            return (code, obj, "R21-1" in (err or ""),
                    ("Additionally, the guard-events row for this deny ({}) could not be written; "
                     "record it manually (nocncl).".format(event)) in (err or ""))

        def _r21_deny(result, kind):
            obj = result[1] if isinstance(result[1], dict) else {}
            hso = obj.get("hookSpecificOutput")
            reason = hso.get("permissionDecisionReason", "") if isinstance(hso, dict) else ""
            warn = "the guard-events row for this deny ({}) could not be written".format(kind)
            return (_verdict(result), warn in reason, warn in str(obj.get("systemMessage", "")))
        _r21.set_turn_state({})
        check("r21/stop-deny-holds-and-warns",
              _r21_block(aiqt_hooks.orch_stop_guard(_r21.payload("Stop")), "Stop"),
              (2, None, True, True))
        check("r21/stop-deny-counted", _r21.turn_state().get("stop_denials"), 1)
        _r21.set_turn_state({})
        check("r21/teammate-idle-deny-holds-and-warns",
              _r21_block(aiqt_hooks.orch_teammate_idle(_r21.payload("TeammateIdle")), "TeammateIdle"),
              (2, None, True, True))
        _r21.set_turn_state({})
        check("r21/schedule-deny-holds-and-warns",
              _r21_deny(aiqt_hooks.orch_yield_tool(_r21.payload(
                  "PreToolUse", "ScheduleWakeup", {"prompt": "recheck R21-1 later"})), "yield-tool"),
              ("deny", True, True))
        _r21.set_turn_state({"last_human_input_utc": now_iso(0)})
        check("r21/quiet-claim-deny-holds-and-warns",
              _r21_deny(aiqt_hooks.orch_yield_tool(_r21.payload(
                  "PreToolUse", "ScheduleWakeup",
                  {"prompt": "user quiet for 20 minutes; recheck R21-1"})), "yield-tool"),
              ("deny", True, True))
        _r21.mode.write_text("Operating-mode: unattended\n", encoding="utf-8")
        check("r21/ask-deny-holds-and-warns",
              _r21_deny(aiqt_hooks.orch_ask_guard(_r21.payload(
                  "PreToolUse", "AskUserQuestion", {"questions": [{"question": "which?"}]})), "ask-guard"),
              ("deny", True, True))
        _r21.mode.write_text("", encoding="utf-8")
        _r21.set_items([item("R21-P{:05d}".format(n), state="proposed")
                        for n in range(aiqt_hooks._ORCH_CHECKPOINT_MAX + 5)])
        _r21.set_turn_state({})
        _r21_res = aiqt_hooks.orch_stop_guard(_r21.payload("Stop"))
        check("r21/checkpoint-bound-drop-warns",
              (_verdict(_r21_res),
               "the guard-events row for this dropped (checkpoint-bound) could not be written"
               in str((_r21_res[1] or {}).get("systemMessage", ""))),
              ("warn", True))
        _r21_ge.rmdir()
        _r21.set_items([item("R21-1")])
        _r21.set_turn_state({})
        _r21_ctl = aiqt_hooks.orch_stop_guard(_r21.payload("Stop"))
        _r21_rows = aiqt_hooks._orch_read_jsonl(str(_r21_ge))[0] or []
        check("r21/stop-deny-recorded-no-warning",
              (_r21_ctl[0], "Additionally" in (_r21_ctl[2] or ""),
               [(r.get("kind"), r.get("decision")) for r in _r21_rows]),
              (2, False, [("Stop", "deny")]))

        # ROUND 22, THE REMAINING WARNING SITES EACH HAVE A VECTOR: with guard-events.jsonl a directory (a
        # real failed append), (a) a Stop deny whose counter cannot be persisted fails open with the
        # allow_unpersistable warning in its banner; (b) a Stop at the loop bound and (c) a stop-classified
        # scheduling call at the loop bound allow with findings, each banner carrying the allow_with_findings
        # warning; (d) a resume audit with a finding carries the findings warning; (e) the operator-escape
        # ALLOW of a Stop and of a scheduling call (the escape seam stands in for a differently-owned
        # sentinel) is still allowed, its banner saying the override's row could not be written. Each row
        # fails when its warning is removed. (f) A resume barrier that cannot be written (a directory at its
        # path, guard-events writable) leaves the finding's warning saying the barrier was not persisted and
        # never saying a re-run clears it (red on f8fe9112). The control rows show neither warning when the
        # row and the barrier are written, and the escape row recorded.
        _r22 = Fixture(tmp, "r22-events")
        _r22_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r22.root)))
        _r22_ge = _r22_sd / "guard-events.jsonl"
        _r22_ge.mkdir(parents=True, exist_ok=True)
        _r22.set_items([item("R22-1")])

        def _r22_note(result):
            obj = result[1] if isinstance(result[1], dict) else dict()
            return _verdict(result), str(obj.get("systemMessage", ""))

        def _r22_has(result, text):
            verdict, msg = _r22_note(result)
            return verdict, text in msg
        _r22_record = aiqt_hooks._orch_record_denial
        try:
            aiqt_hooks._orch_record_denial = lambda *a: "PermissionError"
            _r22.set_turn_state(dict())
            check("r22/allow-unpersistable-warns",
                  _r22_has(aiqt_hooks.orch_stop_guard(_r22.payload("Stop")),
                           "the guard-events row for this allow_unpersistable (Stop) could not be written"),
                  ("warn", True))
        finally:
            aiqt_hooks._orch_record_denial = _r22_record
        _r22.set_turn_state(dict(stop_denials=aiqt_hooks._ORCH_LOOP_BOUND))
        check("r22/stop-findings-warns",
              _r22_has(aiqt_hooks.orch_stop_guard(_r22.payload("Stop")),
                       "the guard-events row for this allow_with_findings (Stop) could not be written"),
              ("warn", True))
        _r22.set_turn_state(dict(stop_denials=aiqt_hooks._ORCH_LOOP_BOUND))
        check("r22/yield-findings-warns",
              _r22_has(aiqt_hooks.orch_yield_tool(_r22.payload(
                  "PreToolUse", "ScheduleWakeup", dict(stop=True, prompt="end after R22-1"))),
                  "the guard-events row for this allow_with_findings (yield-tool) could not be written"),
              ("warn", True))
        _r22.handoff.write_text("Branch: feature/other" + chr(10), encoding="utf-8")
        _r22_audit = aiqt_hooks.orch_resume_audit(_r22.payload("SessionStart"))
        check("r22/resume-audit-findings-warns",
              (_r22_has(_r22_audit, "the guard-events row for this findings (resume-audit) could not be "
                                    "written"), "feature/other" in _r22_note(_r22_audit)[1]),
              (("warn", True), True))
        _r22.handoff.write_text("Branch: main" + chr(10), encoding="utf-8")
        _r22_escape = aiqt_hooks._orch_escape_active
        try:
            aiqt_hooks._orch_escape_active = lambda reg, root: (True, None)
            _r22.set_turn_state(dict())
            _r22_esc_stop = aiqt_hooks.orch_stop_guard(_r22.payload("Stop"))
            _r22.set_turn_state(dict())
            _r22_esc_sched = aiqt_hooks.orch_yield_tool(_r22.payload(
                "PreToolUse", "ScheduleWakeup", dict(prompt="recheck R22-1 later")))
            check("r22/escape-allow-unrecorded-warns",
                  (_r22_has(_r22_esc_stop, "the guard-events row recording this operator-escape release "
                                           "(Stop) could not be written"),
                   _r22_has(_r22_esc_sched, "the guard-events row recording this operator-escape release "
                                            "(yield-tool) could not be written")),
                  (("warn", True), ("warn", True)))
            _r22_ge.rmdir()
            _r22.set_turn_state(dict())
            _r22_esc_ctl = aiqt_hooks.orch_stop_guard(_r22.payload("Stop"))
            _r22_rows = aiqt_hooks._orch_read_jsonl(str(_r22_ge))[0] or []
            check("r22/escape-allow-recorded-no-warning",
                  (_r22_esc_ctl, [(r.get("kind"), r.get("decision"), r.get("detail")) for r in _r22_rows]),
                  ((0, None, None), [("Stop", "allow", "operator escape artefact present (logged)")]))
        finally:
            aiqt_hooks._orch_escape_active = _r22_escape
        _r22_bar = _r22_sd / "resume-barrier.json"
        try:
            _r22_bar.unlink()
        except FileNotFoundError:
            pass
        _r22_bar.mkdir()
        _r22.handoff.write_text("Branch: feature/other" + chr(10), encoding="utf-8")
        _r22_unarmed = _r22_note(aiqt_hooks.orch_resume_audit(_r22.payload("SessionStart")))
        _r22_bar.rmdir()
        _r22_armed = _r22_note(aiqt_hooks.orch_resume_audit(_r22.payload("SessionStart")))
        _r22.handoff.write_text("Branch: main" + chr(10), encoding="utf-8")
        check("r22/resume-barrier-unwritten-warns-never-says-cleared",
              tuple((v, "feature/other" in m,
                     "the resume barrier could not be written (IsADirectoryError), so it was not persisted"
                     in m, "to clear the barrier" in m) for v, m in (_r22_unarmed, _r22_armed))
              + (json.loads(_r22_bar.read_text(encoding="utf-8")).get("active"),),
              (("warn", True, True, False), ("warn", True, False, True), True))

        # EVERY FAILED RECORD WRITE ON A DENY, FINDINGS OR ESCAPE PATH IS REPORTED, THE DECISION UNCHANGED:
        # (1) a scheduling deny whose denial counter cannot be written (turn-state.json a directory, a real
        # failed write) still denies, past the cap too, with the warning in its deny reason and banner, and a
        # stop=true call whose counter save fails (the save seam) says the loop bound is not advanced; (2) an
        # allowed schedule whose wake digest cannot be written allows with a note saying so, on the clean
        # ALLOW (a real failed write), the cap-relieved ALLOW_WITH_FINDINGS and the operator-escape ALLOW (the
        # save seam); (3) a genuine prompt whose stamp cannot be written proceeds with a note, and a
        # timer-originated prompt whose digest cannot be consumed says so in its context line; (4) a brief
        # declaring a working-tree or a not-a-review target is allowed with a note naming an unwritten
        # guard-events row; (5) the TeammateIdle operator-escape ALLOW names its unwritten row; (6) a resume
        # barrier that cannot be written for a reason other than a directory at its path names that error
        # and the state directory remedy, and a directory names its removal; (7) a clean audit whose clear
        # fails leaves an armed barrier that is noted once per arming, so one already warned about stays
        # silent. Each row fails when its warning (or, for (7), the once-per-arming flag) is removed, and each
        # control leg shows no warning where the write succeeds.
        _rw = Fixture(tmp, "recwrite")
        _rw_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_rw.root)))
        _rw_ts = _rw_sd / "turn-state.json"
        _rw_ge = _rw_sd / "guard-events.jsonl"
        _rw_sched = lambda ti: aiqt_hooks.orch_yield_tool(_rw.payload("PreToolUse", "ScheduleWakeup", ti))

        def _rw_msg(result):
            obj = result[1] if isinstance(result[1], dict) else dict()
            return _verdict(result), str(obj.get("systemMessage", ""))

        def _rw_note(result, needle):
            verdict, msg = _rw_msg(result)
            return verdict, needle in msg

        def _rw_deny(result, needle):
            obj = result[1] if isinstance(result[1], dict) else dict()
            hso = obj.get("hookSpecificOutput")
            reason = hso.get("permissionDecisionReason", "") if isinstance(hso, dict) else ""
            return _verdict(result), needle in reason, needle in str(obj.get("systemMessage", ""))

        def _rw_context(result):
            obj = result[1] if isinstance(result[1], dict) else dict()
            hso = obj.get("hookSpecificOutput")
            return str(hso.get("additionalContext", "")) if isinstance(hso, dict) else ""
        _rw_counter = "the denial counter could not be written to turn-state.json"
        _rw_wake = "this wake's prompt digest could not be written to turn-state.json"
        _rw.set_items([item("RW-1")])
        _rw_sd.mkdir(parents=True, exist_ok=True)
        _rw_ts.unlink(missing_ok=True)
        _rw_ts.mkdir()
        check("recwrite/schedule-deny-unpersisted-counter-warns",
              [_rw_deny(_rw_sched(dict(prompt="recheck RW-1 later")),
                        _rw_counter + ", so this deny does not count toward the scheduling cap")
               for _ in range(aiqt_hooks._ORCH_SCHEDULE_CAP + 1)],
              [("deny", True, True)] * (aiqt_hooks._ORCH_SCHEDULE_CAP + 1))
        _rw.set_items([])
        check("recwrite/wake-digest-unwritten-clean-allow-warns",
              _rw_note(_rw_sched(dict(prompt="check back later")), _rw_wake), ("warn", True))
        _rw_ts.rmdir()
        _rw.set_turn_state(dict())
        check("recwrite/wake-digest-written-no-warning",
              (_rw_sched(dict(prompt="check back later")), len(_rw.turn_state().get("wake_digests") or [])),
              ((0, None, None), 1))
        _rw_save = aiqt_hooks._orch_save_turn_state
        _rw_escape = aiqt_hooks._orch_escape_active
        try:
            _rw.set_items([item("RW-1")])
            _rw.set_turn_state(dict())
            aiqt_hooks._orch_save_turn_state = lambda root, state: "PermissionError"
            check("recwrite/stop-call-deny-unpersisted-counter-warns",
                  _rw_deny(_rw_sched(dict(stop=True, prompt="end after RW-1")),
                           _rw_counter + ", so this deny does not count toward the loop bound"),
                  ("deny", True, True))
            # QA rounds 9 and 10: the whole unsaved-counter sentence of each deny, and the comment above it, are
            # pinned so that restoring an earlier wording (a stop relieved only "at" the bound, no
            # stop_hook_active field, that field claimed as a documented signal of a PreToolUse call, no Stop
            # fail-open on its own unsaved deny, or a schedule call relieved only "at" the cap) fails this row
            _rw.set_turn_state(dict())
            _rw_full = (_rw_counter + ", so this deny does not count toward the {} (PermissionError); {}; record "
                        "it manually (nocncl).")
            check("recwrite/unsaved-counter-deny-text-names-each-relief-trigger",
                  (_rw_deny(_rw_sched(dict(stop=True, prompt="end after RW-1")), _rw_full.format(
                      "loop bound", "a later stop=true call or Stop takes the loop-bound exit only when the count "
                      "it reads from turn-state.json is at or above the bound, that file cannot be read as a JSON "
                      "object or its stop_denials value is malformed (each of which needs a state directory it "
                      "can search), or that call's payload carries stop_hook_active set to true (a field the "
                      "Claude Code hooks reference documents in the Stop input and does not list in the "
                      "PreToolUse input), and a later Stop the decision core denies also allows with findings "
                      "when its own denial counter cannot be saved")),
                   _rw_deny(_rw_sched(dict(prompt="recheck RW-1 later")), _rw_full.format(
                       "scheduling cap", "a later call is relieved only when the count it reads from "
                       "turn-state.json is at or above the cap on an unchanged basis, which needs a state "
                       "directory it can search and a turn-state.json it can read")),
                   "\n".join((
                       "        # a counter that cannot be saved leaves this deny uncounted; relief on a later call "
                       "comes only from",
                       "        # what that call reads (its turn-state count, see _orch_build_ctx, or a "
                       "stop_hook_active field set to",
                       "        # true in its payload, documented in the Stop input and not in the PreToolUse "
                       "input) or, for a Stop,",
                       "        # from its own deny failing to save, never from this deny. The deny stands and "
                       "the failure is",
                       "        # reported on it",
                       "        unsaved = _orch_record_denial(root, ts, kind, basis)"))
                   in Path(aiqt_hooks.__file__).read_text(encoding="utf-8")),
                  (("deny", True, True), ("deny", True, True), True))
            aiqt_hooks._orch_save_turn_state = _rw_save
            _rw.set_turn_state(dict())
            check("recwrite/stop-call-deny-counted-no-warning",
                  (_rw_deny(_rw_sched(dict(stop=True, prompt="end after RW-1")), _rw_counter),
                   _rw.turn_state().get("stop_denials")),
                  (("deny", False, False), 1))
            _rw.set_turn_state(dict())
            for _ in range(aiqt_hooks._ORCH_SCHEDULE_CAP):
                _rw_sched(dict(prompt="recheck RW-1 later"))
            aiqt_hooks._orch_save_turn_state = lambda root, state: "PermissionError"
            check("recwrite/wake-digest-unwritten-findings-warns",
                  _rw_note(_rw_sched(dict(prompt="recheck RW-1 later")), _rw_wake), ("warn", True))
            aiqt_hooks._orch_escape_active = lambda reg, root: (True, None)
            _rw.set_turn_state(dict())
            check("recwrite/wake-digest-unwritten-escape-warns",
                  _rw_note(_rw_sched(dict(prompt="recheck RW-1 later")), _rw_wake), ("warn", True))
        finally:
            aiqt_hooks._orch_save_turn_state = _rw_save
            aiqt_hooks._orch_escape_active = _rw_escape
        _rw_ts.unlink()
        _rw_ts.mkdir()
        check("recwrite/stamp-unwritten-notes",
              _rw_note(aiqt_hooks.orch_prompt_stamp(_rw.payload("UserPromptSubmit", extra=dict(prompt="hello"))),
                       "its time was not stamped and the denial counters were not reset"),
              ("warn", True))
        _rw_ts.rmdir()
        check("recwrite/stamp-written-silent",
              (aiqt_hooks.orch_prompt_stamp(_rw.payload("UserPromptSubmit", extra=dict(prompt="hello"))),
               bool(_rw.turn_state().get("last_human_input_utc"))),
              ((0, None, None), True))
        _rw.set_turn_state(dict(wake_digests=[hashlib.sha256(b"wake RW-1").hexdigest()]))
        _rw_wake_prompt = lambda: aiqt_hooks.orch_prompt_stamp(
            _rw.payload("UserPromptSubmit", extra=dict(prompt="wake RW-1")))
        try:
            aiqt_hooks._orch_save_turn_state = lambda root, state: "PermissionError"
            _rw_timer = _rw_context(_rw_wake_prompt())
        finally:
            aiqt_hooks._orch_save_turn_state = _rw_save
        _rw_timer_ctl = _rw_context(_rw_wake_prompt())
        check("recwrite/stamp-timer-digest-unconsumed-says-so",
              ("TIMER-ORIGINATED" in _rw_timer, "consuming this wake's digest failed" in _rw_timer,
               "TIMER-ORIGINATED" in _rw_timer_ctl, "Additionally" in _rw_timer_ctl,
               _rw.turn_state().get("wake_digests")),
              (True, True, True, False, []))
        _rw_ge.unlink(missing_ok=True)
        _rw_ge.mkdir()
        try:
            aiqt_hooks._orch_escape_active = lambda reg, root: (True, None)
            _rw.set_turn_state(dict())
            check("recwrite/teammate-idle-escape-allow-unrecorded-warns",
                  _rw_note(aiqt_hooks.orch_teammate_idle(_rw.payload("TeammateIdle")),
                           "the guard-events row recording this operator-escape release (TeammateIdle) could "
                           "not be written"),
                  ("warn", True))
            _rw_ge.rmdir()
            _rw.set_turn_state(dict())
            check("recwrite/teammate-idle-escape-allow-recorded-no-warning",
                  (aiqt_hooks.orch_teammate_idle(_rw.payload("TeammateIdle")),
                   [(r.get("kind"), r.get("decision")) for r in aiqt_hooks._orch_read_jsonl(str(_rw_ge))[0] or []]),
                  ((0, None, None), [("TeammateIdle", "allow")]))
        finally:
            aiqt_hooks._orch_escape_active = _rw_escape
        _rw_rd = RdpFixture(tmp, "recwrite-rdp")
        _rw_rd_ge = _rw_rd.briefs / "state" / "guard-events.jsonl"
        _rw_rd_out = []
        for _rw_target in ("working-tree", "not-a-review"):
            _rw_brief = _rw_rd.brief(["Review-target: " + _rw_target])
            _rw_rd_ge.unlink(missing_ok=True)
            _rw_rd_ge.mkdir(parents=True)
            _rw_failed = _rw_note(_rw_rd.dispatch(_rw_brief),
                                  "the guard-events row for this allow-declared-target (review-dispatch-pin) "
                                  "could not be written")
            _rw_rd_ge.rmdir()
            _rw_ok = _rw_note(_rw_rd.dispatch(_rw_brief), "Additionally")
            _rw_rd_out.append((_rw_target, _rw_failed, _rw_ok, [
                (r.get("kind"), r.get("decision"), r.get("detail"))
                for r in aiqt_hooks._orch_read_jsonl(str(_rw_rd_ge))[0] or []]))
        check("recwrite/declared-target-unrecorded-notes", _rw_rd_out, [
            (t, ("warn", True), ("warn", False),
             [("review-dispatch-pin", "allow-declared-target", "{}: {}".format(_rw_rd.briefs / "brief.txt", t))])
            for t in ("working-tree", "not-a-review")])
        _rw_b = Fixture(tmp, "recwrite-barrier")
        _rw_b_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_rw_b.root)))
        _rw_bar = _rw_b_sd / "resume-barrier.json"
        _rw_audit = lambda: _rw_msg(aiqt_hooks.orch_resume_audit(_rw_b.payload("SessionStart")))
        shutil.rmtree(_rw_b_sd, ignore_errors=True)
        _rw_b_sd.write_text("not a directory" + chr(10), encoding="utf-8")
        _rw_b.handoff.write_text("Branch: feature/other" + chr(10), encoding="utf-8")
        _rw_bv, _rw_bm = _rw_audit()
        _rw_b_sd.unlink()
        check("recwrite/resume-barrier-unwritten-other-error-names-it",
              (_rw_bv, "feature/other" in _rw_bm,
               "the resume barrier could not be written (FileExistsError), so it was not persisted" in _rw_bm,
               "once the state directory can be created and written" in _rw_bm,
               "remove the directory at the barrier path" in _rw_bm, "to clear the barrier" in _rw_bm),
              ("warn", True, True, True, False, False))
        _rw_bar.mkdir(parents=True)
        _rw_dv, _rw_dm = _rw_audit()
        _rw_bar.rmdir()
        check("recwrite/resume-barrier-directory-names-removal",
              (_rw_dv, "remove the directory at the barrier path {}".format(_rw_bar) in _rw_dm,
               "once the state directory can be created" in _rw_dm),
              ("warn", True, False))
        _rw_b.handoff.write_text("", encoding="utf-8")
        _rw_write = aiqt_hooks._orch_barrier_write

        def _rw_refuse(path, obj):
            raise PermissionError("the clear is refused")
        _rw_outside = lambda: _verdict(aiqt_hooks.orch_resume_barrier(_rw_b.payload(
            "PreToolUse", "Write", dict(file_path=str(_rw_b.root / "src.py"), content="x"))))
        _rw_pin = []
        for _rw_warned in (True, False):
            _rw_bar.write_text(json.dumps(dict(active=True, findings=["recwrite-armed"], warned=_rw_warned)),
                               encoding="utf-8")
            try:
                aiqt_hooks._orch_barrier_write = _rw_refuse
                _rw_clean = aiqt_hooks.orch_resume_audit(_rw_b.payload("SessionStart"))
            finally:
                aiqt_hooks._orch_barrier_write = _rw_write
            _rw_pin.append((_rw_warned, _rw_clean, _rw_outside(), _rw_outside(),
                            json.loads(_rw_bar.read_text(encoding="utf-8")).get("active")))
        check("recwrite/failed-clear-armed-barrier-noted-once-per-arming", _rw_pin,
              [(True, (0, None, None), "allow", "allow", True), (False, (0, None, None), "warn", "allow", True)])

        # ROUND 2 OF THE RECORD-WRITE AUDIT, EACH FAILED WRITE REACHES THE OUTPUT EVEN WHEN ITS GUARD-EVENTS ROW
        # IS WRITTEN: (1) a checkpoint or init marker that cannot be written (only that path refused, so the
        # guard-events append succeeds) is named in the deny reason and banner of a scheduling deny and in the
        # block reason of a Stop, and under a persistent fault the clean schedule ALLOW left after the item
        # vanishes carries the warning instead of returning silently; under a fault that clears after one
        # window the vanished item is not held, which that window's warning says; (2) the wake-digest warning
        # promises no classification: with a matching digest already registered the returning prompt reads as
        # timer-originated, without one as genuine, and the warning fits both; (3) a turn-state save that
        # fails after writing part of its bytes leaves turn-state.json byte-identical with no temporary file
        # (the save is atomic), so the unconsumed digest still classifies the next identical prompt, and the
        # timer path's warning promises no classification and reaches the operator in a systemMessage; (4) a
        # clean audit's failed clear under a fault that also refuses the warned flag notes the armed barrier on
        # each mutation; (5) the writers already reported in the output stay reported with their guard-events
        # append succeeding: escape-spoof.json, forced-exit.jsonl and the ask guard's pending row.
        _r2 = Fixture(tmp, "recwrite2")
        _r2_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r2.root)))
        _r2_ge = _r2_sd / "guard-events.jsonl"
        _r2_sched = lambda ti: aiqt_hooks.orch_yield_tool(_r2.payload("PreToolUse", "ScheduleWakeup", ti))
        _r2_stop = lambda: aiqt_hooks.orch_stop_guard(_r2.payload("Stop"))
        _r2_prompt = lambda text: aiqt_hooks.orch_prompt_stamp(
            _r2.payload("UserPromptSubmit", extra=dict(prompt=text)))
        _r2_wja = aiqt_hooks._orch_write_json_atomic
        _r2_aj = aiqt_hooks._orch_append_jsonl
        _r2_wj = aiqt_hooks._orch_write_json
        _r2_save = aiqt_hooks._orch_save_turn_state
        _r2_ea = aiqt_hooks._orch_escape_active

        def _r2_refuse(suffix):
            return lambda p, o: "PermissionError" if str(p).endswith(suffix) else _r2_wja(p, o)

        def _r2_out(result):
            # (verdict, deny reason or Stop block reason, systemMessage)
            code, obj, err = result
            obj = obj if isinstance(obj, dict) else dict()
            hso = obj.get("hookSpecificOutput")
            reason = hso.get("permissionDecisionReason", "") if isinstance(hso, dict) else ""
            return _verdict(result), str(reason or err or ""), str(obj.get("systemMessage", ""))

        def _r2_context(result):
            obj = result[1] if isinstance(result[1], dict) else dict()
            hso = obj.get("hookSpecificOutput")
            return str(hso.get("additionalContext", "")) if isinstance(hso, dict) else ""

        def _r2_events(kind):
            return sum(1 for r in aiqt_hooks._orch_read_jsonl(str(_r2_ge))[0] or [] if r.get("kind") == kind)
        _r2_ckpt = "the anti-shrinkage checkpoint backlog-checkpoint.json could not be written"
        _r2_marker = "the checkpoint-init marker checkpoint-init.marker could not be written"
        _r2.set_items([item("QA-1")])
        _r2.set_turn_state(dict())
        try:
            aiqt_hooks._orch_write_json_atomic = _r2_refuse("backlog-checkpoint.json")
            _r2_d = _r2_out(_r2_sched(dict(prompt="recheck QA-1 later")))
            _r2_s = _r2_out(_r2_stop())
            _r2_n1 = _r2_events("checkpoint-unwritable")
            _r2.set_items([], keep_checkpoint=True)
            _r2.set_turn_state(dict())
            _r2_v = _r2_out(_r2_sched(dict(prompt="check back later")))
        finally:
            aiqt_hooks._orch_write_json_atomic = _r2_wja
        check("recwrite2/checkpoint-unwritten-named-while-its-row-is-written",
              (_r2_d[0], _r2_ckpt in _r2_d[1], _r2_ckpt in _r2_d[2],
               "(checkpoint-unwritable)" in _r2_d[1] + _r2_d[2] + _r2_s[1], _r2_s[0], _r2_ckpt in _r2_s[1], _r2_n1),
              ("deny", True, True, False, "block2", True, 2))
        check("recwrite2/checkpoint-unwritten-vanished-item-not-silent",
              (_r2_v[0], _r2_ckpt in _r2_v[2], _r2_events("checkpoint-unwritable")), ("warn", True, 3))
        _r2.set_items([item("QA-2")])
        _r2.set_turn_state(dict())
        try:
            aiqt_hooks._orch_write_json_atomic = _r2_refuse("backlog-checkpoint.json")
            _r2_t = _r2_out(_r2_sched(dict(prompt="recheck QA-2 later")))
        finally:
            aiqt_hooks._orch_write_json_atomic = _r2_wja
        _r2.set_items([], keep_checkpoint=True)
        _r2.set_turn_state(dict())
        check("recwrite2/checkpoint-unwritten-once-warns-later-vanish-unheld",
              (_r2_t[0], "is not held as vanished or demoted unless an earlier checkpoint already records it"
               in _r2_t[2], _r2_sched(dict(prompt="check back later"))),
              ("deny", True, (0, None, None)))
        _r2.set_items([item("QA-3")])
        _r2.set_turn_state(dict())
        try:
            aiqt_hooks._orch_write_json_atomic = _r2_refuse("checkpoint-init.marker")
            _r2_m1 = _r2_out(_r2_sched(dict(prompt="recheck QA-3 later")))
            _r2_m2 = _r2_out(_r2_stop())
        finally:
            aiqt_hooks._orch_write_json_atomic = _r2_wja
        check("recwrite2/marker-unwritten-named-while-its-row-is-written",
              (_r2_m1[0], _r2_marker in _r2_m1[1], _r2_marker in _r2_m1[2], _r2_m2[0], _r2_marker in _r2_m2[1],
               "(checkpoint-marker-unwritable)" in _r2_m1[1] + _r2_m1[2] + _r2_m2[1],
               _r2_events("checkpoint-marker-unwritable")),
              ("deny", True, True, "block2", True, False, 2))
        _r2_same = "same wake QA"
        _r2_dig = hashlib.sha256(_r2_same.encode("utf-8")).hexdigest()
        _r2.set_items([])
        _r2_wk = []
        for _r2_pre in ([_r2_dig], []):
            _r2.set_turn_state(dict(wake_digests=list(_r2_pre)))
            try:
                aiqt_hooks._orch_save_turn_state = lambda root, state: "PermissionError"
                _r2_w = _r2_out(_r2_sched(dict(prompt=_r2_same)))
            finally:
                aiqt_hooks._orch_save_turn_state = _r2_save
            _r2_back = _r2_prompt(_r2_same)
            _r2_wk.append((_r2_w[0], "may read as genuine human input or as timer-originated" in _r2_w[2],
                           "will read" in _r2_w[2], "TIMER-ORIGINATED" in _r2_context(_r2_back),
                           _r2_back == (0, None, None)))
        check("recwrite2/wake-digest-unwritten-promises-no-classification", _r2_wk,
              [("warn", True, False, True, False), ("warn", True, False, False, True)])
        _r2.set_turn_state(dict(wake_digests=[_r2_dig]))
        _r2_tsp = _r2_sd / "turn-state.json"
        _r2_before = _r2_tsp.read_bytes()
        _r2_write = os.write

        def _r2_partial(fd, data):
            _r2_write(fd, bytes(data[:1]))
            raise OSError("injected after a partial write")
        try:
            os.write = _r2_partial
            _r2_p = _r2_prompt(_r2_same)
        finally:
            os.write = _r2_write
        _r2_after = _r2_tsp.read_bytes()
        _r2_tmp_left = bool(list(_r2_sd.glob("turn-state.json*.tmp")))
        _r2_next = _r2_prompt(_r2_same)
        check("recwrite2/turn-state-partial-write-leaves-previous-state",
              (_r2_after == _r2_before, _r2_tmp_left, "TIMER-ORIGINATED" in _r2_context(_r2_next),
               _r2.turn_state().get("wake_digests")),
              (True, False, True, []))
        _r2_pctx = _r2_context(_r2_p)
        _r2_psys = _r2_p[1].get("systemMessage", "") if isinstance(_r2_p[1], dict) else ""
        check("recwrite2/timer-unconsumed-uncertain-and-operator-sees-it",
              ("TIMER-ORIGINATED" in _r2_pctx, "consuming this wake's digest failed" in _r2_pctx,
               "may read as timer-originated or as genuine human input" in _r2_pctx, "will also read" in _r2_pctx,
               _r2_psys.startswith("AIQT guardrail: this prompt was read as timer-originated, but turn-state.json "
                                   "could not be written")),
              (True, True, True, False, True))
        _r2_b = Fixture(tmp, "recwrite2-barrier")
        _r2_bsd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r2_b.root)))
        _r2_bar = _r2_bsd / "resume-barrier.json"
        _r2_bsd.mkdir(parents=True, exist_ok=True)
        _r2_bar.write_text(json.dumps(dict(active=True, findings=["recwrite2-armed"], warned=False)),
                           encoding="utf-8")
        _r2_bw = aiqt_hooks._orch_barrier_write

        def _r2_bfail(path, obj):
            raise PermissionError("the state directory refuses every barrier write")
        _r2_mut = lambda: _verdict(aiqt_hooks.orch_resume_barrier(_r2_b.payload(
            "PreToolUse", "Write", dict(file_path=str(_r2_b.root / "src.py"), content="x"))))
        try:
            aiqt_hooks._orch_barrier_write = _r2_bfail
            _r2_bc = aiqt_hooks.orch_resume_audit(_r2_b.payload("SessionStart"))
            _r2_bm = [_r2_mut() for _ in range(3)]
        finally:
            aiqt_hooks._orch_barrier_write = _r2_bw
        # REGRESSION PIN: this vector passes on the 88e0b86 hooks too; it pins behaviour that was already
        # correct and does not test a change of this PR
        check("recwrite2/failed-clear-persistent-fault-noted-each-mutation",
              (_r2_bc, _r2_bm, json.loads(_r2_bar.read_text(encoding="utf-8")).get("warned")),
              ((0, None, None), ["warn"] * 3, False))
        _r2.set_items([])
        _r2.set_turn_state(dict())
        try:
            aiqt_hooks._orch_escape_active = lambda reg, root: (False, "recwrite2 ignored sentinel")
            aiqt_hooks._orch_write_json = lambda p, o: False if str(p).endswith("escape-spoof.json") else _r2_wj(p, o)
            _r2_sp = _r2_out(_r2_stop())
        finally:
            aiqt_hooks._orch_escape_active = _r2_ea
            aiqt_hooks._orch_write_json = _r2_wj
        # REGRESSION PIN: passes on the 88e0b86 hooks too (pins already-correct behaviour, not a change)
        check("recwrite2/spoof-file-unwritten-named-while-its-row-is-written",
              (_r2_sp[0], "(guard-events ok, escape-spoof.json FAILED)" in _r2_sp[2], _r2_events("escape-spoof")),
              ("warn", True, 1))
        _r2.set_items([item("QA-4")])
        _r2.set_turn_state(dict(stop_denials=aiqt_hooks._ORCH_LOOP_BOUND))
        try:
            aiqt_hooks._orch_append_jsonl = lambda p, o: False if str(p).endswith("forced-exit.jsonl") else _r2_aj(p, o)
            _r2_fx = _r2_out(_r2_stop())
        finally:
            aiqt_hooks._orch_append_jsonl = _r2_aj
        # REGRESSION PIN: passes on the 88e0b86 hooks too (pins already-correct behaviour, not a change)
        check("recwrite2/forced-exit-log-unwritten-named-while-its-row-is-written",
              (_r2_fx[0], "(guard-events ok, forced-exit.jsonl FAILED)" in _r2_fx[2],
               _r2_events("forced_unresolved")),
              ("warn", True, 1))
        _r2.mode.write_text("Operating-mode: unattended" + chr(10), encoding="utf-8")
        try:
            aiqt_hooks._orch_append_jsonl = (
                lambda p, o: False if str(p).endswith("pending-asks.jsonl") else _r2_aj(p, o))
            _r2_ask = _r2_out(aiqt_hooks.orch_ask_guard(_r2.payload("PreToolUse", "AskUserQuestion",
                                                                     dict(questions=[]))))
        finally:
            aiqt_hooks._orch_append_jsonl = _r2_aj
            _r2.mode.write_text("", encoding="utf-8")
        # REGRESSION PIN: passes on the 88e0b86 hooks too (pins already-correct behaviour, not a change)
        check("recwrite2/pending-ask-unwritten-named-while-its-row-is-written",
              (_r2_ask[0], "the pending row could NOT be persisted" in _r2_ask[2], _r2_events("ask-guard")),
              ("deny", True, 1))

        # QA round 2 of the silent-write fixes: _orch_write_json_atomic creates its own temporary file
        # (O_CREAT|O_EXCL|O_NOFOLLOW under a random name) and replaces the target with only that file.
        # (1) A symlink planted at the old fixed temporary name, and one at the exact name the writer draws
        # (os.urandom pinned), change neither the target nor the symlinks' referent: the pinned save fails and
        # leaves both links, an ordinary save publishes a regular file. (2) Save A writes one byte, save B runs
        # whole and succeeds, then A's rename fails: A reports the failure and B's file is published whole.
        # (3) Save B creates its temporary file before A renames and writes after: a reader between the two
        # reads A's whole file, both report success and B's file is the final one. (4) Under umask 022 an
        # existing 0600 or 0640 target keeps its bits and a new target is 0600. (5) A failed save whose cleanup
        # also fails names the temporary file it left in the hook's note. (6) A writable turn-state.json in a
        # state directory the hook cannot write is not saved, so the Stop deny fails open with findings (the
        # disclosed decision change; as root the directory mode is not enforced and the deny stands). (7) The
        # mode reader: a FIFO with no writer, /dev/zero, a file one byte over the bound, a path with a lone
        # surrogate, a surrogate-escaped non-UTF-8 name of an existing attended file the OS could open, a
        # socket (refused at the open; that row passes on the 7f97273f hooks too, pinning the corrected
        # disclosure) and a path with a NUL character each deny the ask, naming the reason,
        # and a JSON-shaped file nested too deep to parse denies it too, with the unattended deny and no read
        # failure named, each inside a child with its own RLIMIT_AS and a 30-second timeout.
        _r3 = Fixture(tmp, "recwrite3")
        _r3_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r3.root)))
        _r3_sd.mkdir(parents=True, exist_ok=True)
        _r3_ts = _r3_sd / "turn-state.json"
        _r3_save = lambda state: aiqt_hooks._orch_write_json_atomic(str(_r3_ts), state)
        _r3_tmps = lambda: sorted(p.name for p in _r3_sd.iterdir() if p.name.endswith(".tmp"))
        _r3_victim = _r3_sd / "victim.txt"
        _r3_victim.write_text("victim", encoding="utf-8")
        _r3_prior = json.dumps(dict(tag="prior")).encode("utf-8")
        _r3_ts.write_bytes(_r3_prior)
        _r3_fixed = os.urandom(8)
        _r3_drawn = _r3_sd / "turn-state.json.{}.tmp".format(_r3_fixed.hex())
        (_r3_sd / "turn-state.json.tmp").symlink_to(_r3_victim)
        _r3_drawn.symlink_to(_r3_victim)
        _r3_urandom = os.urandom
        try:
            os.urandom = lambda n: _r3_fixed
            _r3_pinned = _r3_save(dict(tag="pinned"))
        finally:
            os.urandom = _r3_urandom
        _r3_after_pinned = _r3_ts.read_bytes()
        _r3_plain = _r3_save(dict(tag="plain"))
        check("recwrite3/planted-temp-symlink-no-effect",
              (_r3_pinned, _r3_after_pinned == _r3_prior, _r3_plain, _r3_ts.is_symlink(), _r3.turn_state(),
               _r3_victim.read_text(encoding="utf-8"), (_r3_sd / "turn-state.json.tmp").is_symlink(),
               _r3_drawn.is_symlink(), _r3_tmps()),
              ("FileExistsError", True, None, False, dict(tag="plain"), "victim", True, True,
               sorted(["turn-state.json.tmp", _r3_drawn.name])))
        for _r3_link in (_r3_sd / "turn-state.json.tmp", _r3_drawn):
            if os.path.lexists(str(_r3_link)):  # a writer that removes or replaces them fails the check above
                os.unlink(str(_r3_link))
        _r3_real_write, _r3_real_replace, _r3_real_open = os.write, os.replace, os.open
        _r3_b, _r3_calls = [], []

        def _r3_write_a(fd, data):
            os.write = _r3_real_write  # the rest of A, and all of B, write unhindered
            n = _r3_real_write(fd, bytes(data[:1]))
            _r3_b.append(_r3_save(dict(long_name=123456)))
            return n

        def _r3_replace_a(src, dst, **kw):
            _r3_calls.append(src)
            if len(_r3_calls) == 2:
                raise OSError("injected: the rename of save A fails")
            return _r3_real_replace(src, dst, **kw)
        try:
            os.write, os.replace = _r3_write_a, _r3_replace_a
            _r3_a = _r3_save(dict(a=1))
        finally:
            os.write, os.replace = _r3_real_write, _r3_real_replace
        check("recwrite3/overlap-failed-save-leaves-the-successful-one-whole",
              (_r3_a, _r3_b, _r3.turn_state(), _r3_tmps(), len(set(_r3_calls))),
              ("OSError", [None], dict(long_name=123456), [], 2))
        _r3_ev_b, _r3_ev_a = threading.Event(), threading.Event()
        _r3_seen, _r3_res = [], dict()

        def _r3_open(path, flags, *args, **kw):
            fd = _r3_real_open(path, flags, *args, **kw)
            if threading.current_thread().name == "r3-B" and flags & os.O_EXCL:
                _r3_ev_b.set()
                _r3_ev_a.wait(10)  # B holds its created temporary file until A has renamed
            return fd

        def _r3_replace(src, dst, **kw):
            if threading.current_thread().name != "r3-A":
                return _r3_real_replace(src, dst, **kw)
            _r3_ev_b.wait(10)
            _r3_real_replace(src, dst, **kw)
            _r3_seen.append(aiqt_hooks._orch_turn_state(str(_r3.root)))
            _r3_ev_a.set()
            return None
        _r3_threads = [threading.Thread(target=lambda n=n, st=st: _r3_res.__setitem__(n, _r3_save(st)), name=n)
                       for n, st in (("r3-B", dict(stop_denials=7, tag="B")), ("r3-A", dict(stop_denials=1, tag="A")))]
        try:
            os.open, os.replace = _r3_open, _r3_replace
            for _r3_t in _r3_threads:
                _r3_t.start()
            for _r3_t in _r3_threads:
                _r3_t.join(30)
        finally:
            os.open, os.replace = _r3_real_open, _r3_real_replace
        check("recwrite3/overlap-reader-reads-a-whole-file-and-both-report-success",
              (_r3_res.get("r3-A"), _r3_res.get("r3-B"), _r3_seen, _r3.turn_state(), _r3_tmps()),
              (None, None, [dict(stop_denials=1, tag="A")], dict(stop_denials=7, tag="B"), []))
        _r3_modes = []
        _r3_umask = os.umask(0o022)
        try:
            for _r3_mode in (0o600, 0o640):
                os.chmod(str(_r3_ts), _r3_mode)
                _r3_save(dict(mode=_r3_mode))
                _r3_modes.append(oct(os.stat(str(_r3_ts)).st_mode & 0o777))
            _r3_new = _r3_sd / "r3-fresh.json"
            aiqt_hooks._orch_write_json_atomic(str(_r3_new), dict(fresh=True))
            _r3_modes.append(oct(os.stat(str(_r3_new)).st_mode & 0o777))
        finally:
            os.umask(_r3_umask)
        check("recwrite3/permission-bits-kept-new-file-0600-under-umask-022", _r3_modes,
              ["0o600", "0o640", "0o600"])
        _r3_unlink = os.unlink

        def _r3_partial(fd, data):
            _r3_real_write(fd, bytes(data[:1]))
            raise OSError("injected after a partial write")

        def _r3_no_unlink(path, *args, **kw):
            raise PermissionError("injected: the temporary file cannot be removed")
        _r3.set_turn_state(dict(tag="kept"))
        try:
            os.write, os.unlink = _r3_partial, _r3_no_unlink
            _r3_note = _r2_out(aiqt_hooks.orch_prompt_stamp(
                _r3.payload("UserPromptSubmit", extra=dict(prompt="genuine r3"))))
        finally:
            os.write, os.unlink = _r3_real_write, _r3_unlink
        _r3_left = _r3_tmps()
        check("recwrite3/failed-cleanup-names-the-left-temporary-file",
              (_r3_note[0], "removing its temporary file" in _r3_note[2], len(_r3_left),
               bool(_r3_left) and str(_r3_sd / _r3_left[0]) in _r3_note[2], _r3.turn_state()),
              ("warn", True, 1, True, dict(tag="kept")))
        for _r3_name in _r3_left:
            os.unlink(str(_r3_sd / _r3_name))
        _r3.set_items([item("QA-R3")])
        _r3.set_turn_state(dict())
        os.chmod(str(_r3_sd), 0o555)
        try:
            _r3_ro = _r2_out(aiqt_hooks.orch_stop_guard(_r3.payload("Stop")))
        finally:
            os.chmod(str(_r3_sd), 0o755)
        check("recwrite3/unwritable-state-dir-writable-turn-state-fails-open-with-findings",
              (_r3_ro[0], "the denial counter could not be persisted (PermissionError)" in _r3_ro[2]),
              ("warn", True) if os.geteuid() != 0 else ("block2", False))
        _r3_regp = _r3.root / ".aiqt" / "orchestration.local.json"
        _r3_reg_text = _r3_regp.read_text(encoding="utf-8")
        _r3_child_src = (
            "import json, os, resource, sys\n"
            "sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])\n"
            "import aiqt_hooks, selftest_orch_hooks as t\n"
            "vm = int(open('/proc/self/statm').read().split()[0]) * os.sysconf('SC_PAGE_SIZE')\n"
            "hard = resource.getrlimit(resource.RLIMIT_AS)[1]\n"
            "soft = vm + (64 << 20) if hard == resource.RLIM_INFINITY else min(vm + (64 << 20), hard)\n"
            "resource.setrlimit(resource.RLIMIT_AS, (soft, hard))\n"
            "res = aiqt_hooks.orch_ask_guard(json.loads(sys.argv[4]))\n"
            "hso = res[1].get('hookSpecificOutput') if isinstance(res[1], dict) else None\n"
            "reason = hso.get('permissionDecisionReason', '') if isinstance(hso, dict) else ''\n"
            "has, lacks = json.loads(sys.argv[3])\n"
            "print(json.dumps([t._verdict(res), has in str(reason) and not (lacks and lacks in str(reason))]))\n")
        _r3_fifo = _r3.root / "r3-mode-fifo"
        _r3_big = _r3.root / "r3-mode-big.md"
        _r3_deep = _r3.root / "r3-mode-deep.json"
        _r3_esc = _r3.root / "r3-mode-\udcff.md"  # the name's byte 0xff, surrogate-escaped
        _r3_sock = _r3.root / "r3-mode-sock"
        _r3_bind = ("import os, socket, sys\nos.chdir(sys.argv[1])\ns = socket.socket(socket.AF_UNIX)\n"
                    "s.bind(sys.argv[2])\ns.close()\n")  # a relative bind: the root may be too long for a path
        _r3_bound = getattr(aiqt_hooks, "_ORCH_MODE_MAX_BYTES", 1 << 20)  # a base without it still runs
        _r3_cases = (
            ("fifo", str(_r3_fifo), lambda: os.mkfifo(str(_r3_fifo)), "is not a regular file"),
            ("dev-zero", "/dev/zero", lambda: None, "is not a regular file"),
            ("over-bound", str(_r3_big), lambda: _r3_big.write_bytes(b"a" * (_r3_bound + 1)),
             "larger than the {}-byte bound".format(_r3_bound)),
            ("lone-surrogate", str(_r3.root / "r3-mode") + "\ud800", lambda: None, "is not strict UTF-8"),
            ("surrogate-escaped-openable", str(_r3_esc),
             lambda: _r3_esc.write_text("Operating-mode: attended\n", encoding="utf-8"),
             "is not strict UTF-8 (a lone surrogate or a surrogate-escaped non-UTF-8 byte), so it is refused"),
            ("socket", str(_r3_sock),
             lambda: subprocess.run([sys.executable, "-I", "-B", "-c", _r3_bind, str(_r3.root), _r3_sock.name],
                                    check=True, timeout=30),
             "the mode file cannot be opened (OSError)"),
            ("nul", str(_r3.root / "r3-mode") + "\x00x", lambda: None, "contains a NUL character"),
            ("deep-nesting", str(_r3_deep), lambda: _r3_deep.write_text("[" * 100000, encoding="utf-8"),
             ("the session operating-mode is unattended. RECORD", " because ")))
        _r3_rows = []
        _r3_ask = json.dumps(_r3.payload("PreToolUse", "AskUserQuestion", dict(questions=[])))
        for _r3_name, _r3_path, _r3_make, _r3_reason in _r3_cases:
            _r3_reg = json.loads(_r3_reg_text)
            _r3_reg["mode"] = dict(path=_r3_path)
            _r3_regp.write_text(json.dumps(_r3_reg), encoding="utf-8")  # ensure_ascii escapes the odd paths
            _r3_make()
            try:
                _r3_needle = json.dumps(list(_r3_reason) if isinstance(_r3_reason, tuple) else [_r3_reason, ""])
                p = subprocess.run([sys.executable, "-I", "-B", "-c", _r3_child_src, _r16_hooks, _r16_tools,
                                    _r3_needle, _r3_ask], capture_output=True, text=True, timeout=30)
                try:
                    _r3_rows.append((_r3_name,) + tuple(json.loads(p.stdout)))
                except (ValueError, TypeError):
                    _r3_rows.append((_r3_name, "child exit {}: {}".format(p.returncode, p.stderr.strip()[-200:]),
                                     False))
            except subprocess.TimeoutExpired:
                _r3_rows.append((_r3_name, "child timed out", False))
            finally:
                _r3_regp.write_text(_r3_reg_text, encoding="utf-8")
                for _r3_made in (_r3_fifo, _r3_big, _r3_deep, _r3_esc, _r3_sock):
                    if os.path.lexists(str(_r3_made)):
                        os.unlink(str(_r3_made))
        check("recwrite3/mode-reader-nonregular-oversize-undecodable-path-fails-closed-promptly", tuple(_r3_rows),
              tuple((n, "deny", True) for n, _p, _m, _r in _r3_cases))

        # QA round 3 of the silent-write fixes: _orch_write_json_atomic opens the parent directory once and
        # binds the target's lstat, the create, the replace and the cleanup unlink to that descriptor. (1) The
        # state directory is moved aside and a symlink to a foreign directory put in its place while the
        # writer fsyncs, the foreign directory holding a file at the exact temporary name the writer draws
        # (os.urandom pinned): the save lands in the moved original, the foreign file is neither published
        # nor removed, and no temporary file is left. (2) The same swap, then an injected fsync failure: the
        # writer removes its own temporary file in the moved original, never the foreign file of that name.
        # (3) An injected fsync failure with no swap returns its reason, leaves the prior target unchanged and
        # no temporary file, and the save opens no descriptor it does not close.
        _r4 = Fixture(tmp, "recwrite4")
        _r4_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r4.root)))
        _r4_sd.mkdir(parents=True, exist_ok=True)
        _r4_moved = _r4_sd.parent / (_r4_sd.name + ".moved")
        _r4_foreign = Path(tmp) / "recwrite4-foreign"
        _r4_foreign.mkdir()
        _r4_fixed = os.urandom(8)
        _r4_tmpname = "turn-state.json.{}.tmp".format(_r4_fixed.hex())
        _r4_alien = json.dumps(dict(foreign=True)).encode("utf-8")
        _r4_prior = json.dumps(dict(tag="prior")).encode("utf-8")
        _r4_save = lambda state: aiqt_hooks._orch_write_json_atomic(str(_r4_sd / "turn-state.json"), state)
        _r4_tmps = lambda d: sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))
        _r4_real_fsync, _r4_urandom = os.fsync, os.urandom

        def _r4_fds():
            # None where the descriptors cannot be enumerated, never a measured zero
            try:
                return len(os.listdir("/proc/self/fd"))
            except OSError:
                return None

        def _r4_fd_delta(before):
            after = _r4_fds()
            if before is None or after is None:
                return "cannot-evaluate: the open descriptors cannot be enumerated (/proc/self/fd)"
            return after - before

        def _r4_swap():
            os.rename(str(_r4_sd), str(_r4_moved))
            os.symlink(str(_r4_foreign), str(_r4_sd))

        def _r4_unswap():
            if os.path.islink(str(_r4_sd)):
                os.unlink(str(_r4_sd))
            if os.path.isdir(str(_r4_moved)):
                os.rename(str(_r4_moved), str(_r4_sd))

        def _r4_run(fsync):
            (_r4_sd / "turn-state.json").write_bytes(_r4_prior)
            (_r4_foreign / _r4_tmpname).write_bytes(_r4_alien)
            try:
                os.fsync, os.urandom = fsync, (lambda n: _r4_fixed)
                res = _r4_save(dict(tag="saved"))
            finally:
                os.fsync, os.urandom = _r4_real_fsync, _r4_urandom
            seen = _r4_moved if os.path.isdir(str(_r4_moved)) else _r4_sd
            out = (res, (seen / "turn-state.json").read_bytes(), _r4_tmps(seen),
                   os.path.lexists(str(_r4_foreign / "turn-state.json")),
                   (_r4_foreign / _r4_tmpname).read_bytes() if (_r4_foreign / _r4_tmpname).exists() else None)
            _r4_unswap()
            for _r4_left in [_r4_foreign / "turn-state.json"] + [_r4_sd / n for n in _r4_tmps(_r4_sd)]:
                if os.path.lexists(str(_r4_left)):
                    os.unlink(str(_r4_left))
            return out

        def _r4_swap_then_sync(fd):
            _r4_swap()
            return _r4_real_fsync(fd)

        def _r4_swap_then_fail(fd):
            _r4_swap()
            raise OSError("injected: fsync fails after the parent swap")

        def _r4_fail(fd):
            raise OSError("injected: fsync fails")
        _r4_saved = json.dumps(dict(tag="saved"), sort_keys=True).encode("utf-8")
        check("recwrite4/parent-swapped-during-fsync-never-publishes-foreign-content",
              _r4_run(_r4_swap_then_sync), (None, _r4_saved, [], False, _r4_alien))
        check("recwrite4/fsync-failure-after-parent-swap-removes-its-own-temporary-file",
              _r4_run(_r4_swap_then_fail), ("OSError", _r4_prior, [], False, _r4_alien))
        # REGRESSION PIN: passes on the 7f97273f hooks too; a writer that skips the fsync fails it
        _r4_fd0 = _r4_fds()
        _r4_plain = _r4_run(_r4_fail)
        check("recwrite4/fsync-failure-returns-its-reason-prior-target-unchanged-no-temporary-file",
              (_r4_plain, _r4_fd_delta(_r4_fd0)), (("OSError", _r4_prior, [], False, _r4_alien), 0))

        # QA round 4 of the silent-write fixes. (1) A write-and-search-only (0300) state directory: the writer
        # opens it with _ORCH_O_WALK (O_PATH where available), which needs no read permission, so a direct
        # save succeeds and the Stop deny stands (block2, its counter persisted) end to end; the O_RDONLY open
        # of 181ff037 failed every such save and flipped that deny to fail open. As root the directory mode is
        # not enforced; where O_PATH is absent the row expects the disclosed fallback (the save fails and the
        # deny fails open with findings). (2) The state directory is swapped for a symlink to a foreign
        # directory at the writer's stat of the target, before the create: the descriptor-bound no-follow
        # stat reads the original target, a symlink to a 0640 file, so the new file is 0600, and the create,
        # the replace and the cleanup act in the moved original. A stat by path reads the foreign target's
        # 0604, a following stat the linked file's 0640, and a create by path puts the temporary file in the
        # foreign directory, where the descriptor-bound replace cannot find it: each fails the row. (3) Each
        # descriptor the save opens is closed exactly once, on success and on an fsync failure, and a
        # successful save leaves the count of open descriptors unchanged (cannot-evaluate, which fails the
        # row, where the descriptors cannot be enumerated).
        _r5 = Fixture(tmp, "recwrite5")
        _r5_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r5.root)))
        _r5_sd.mkdir(parents=True, exist_ok=True)
        _r5_ts = _r5_sd / "turn-state.json"
        _r5_direct = _r5_sd / "r5-direct.json"
        _r5.set_items([item("QA-R5")])
        _r5.set_turn_state(dict())
        os.chmod(str(_r5_sd), 0o300)
        try:
            _r5_res = aiqt_hooks._orch_write_json_atomic(str(_r5_direct), dict(direct=True))
            _r5_stop = _r2_out(aiqt_hooks.orch_stop_guard(_r5.payload("Stop")))
        finally:
            os.chmod(str(_r5_sd), 0o755)
        _r5_ok = hasattr(os, "O_PATH") or os.geteuid() == 0
        check("recwrite5/search-only-state-dir-saves-and-stop-deny-stands",
              (_r5_res, json.loads(_r5_direct.read_text(encoding="utf-8")) if _r5_direct.exists() else None,
               _r5_stop[0], "could not be persisted" in _r5_stop[1] + _r5_stop[2],
               _r5.turn_state().get("stop_denials"), _r4_tmps(_r5_sd)),
              (None, dict(direct=True), "block2", False, 1, []) if _r5_ok
              else ("PermissionError", None, "warn", True, None, []))
        _r5_moved = _r5_sd.parent / (_r5_sd.name + ".moved")
        _r5_foreign = Path(tmp) / "recwrite5-foreign"
        _r5_foreign.mkdir()
        _r5_linked = Path(tmp) / "recwrite5-linked.json"
        _r5_linked.write_bytes(b'{"linked": true}')
        os.chmod(str(_r5_linked), 0o640)
        (_r5_foreign / "turn-state.json").write_bytes(b'{"foreign": true}')
        os.chmod(str(_r5_foreign / "turn-state.json"), 0o604)
        _r5_ts.unlink()
        _r5_ts.symlink_to(_r5_linked)
        _r5_real_stat, _r5_real_lstat = os.stat, os.lstat
        _r5_swapped = []

        def _r5_swap_at_stat(real):
            def _stat(path, *args, **kw):
                if (not _r5_swapped and isinstance(path, (str, bytes, os.PathLike))
                        and os.path.basename(os.fsdecode(path)) == "turn-state.json"):
                    _r5_swapped.append(os.fsdecode(path))
                    os.rename(str(_r5_sd), str(_r5_moved))
                    os.symlink(str(_r5_foreign), str(_r5_sd))
                return real(path, *args, **kw)
            return _stat
        try:
            os.stat, os.lstat = _r5_swap_at_stat(_r5_real_stat), _r5_swap_at_stat(_r5_real_lstat)
            _r5_res = aiqt_hooks._orch_write_json_atomic(str(_r5_ts), dict(tag="r5"))
        finally:
            os.stat, os.lstat = _r5_real_stat, _r5_real_lstat
        _r5_seen = _r5_moved if os.path.isdir(str(_r5_moved)) else _r5_sd
        _r5_new = _r5_seen / "turn-state.json"
        _r5_got = (_r5_res, len(_r5_swapped), _r5_new.read_bytes() if not _r5_new.is_symlink() else "a symlink",
                   oct(os.lstat(str(_r5_new)).st_mode & 0o777), _r5_linked.read_bytes(),
                   (_r5_foreign / "turn-state.json").read_bytes(), sorted(os.listdir(str(_r5_foreign))),
                   _r4_tmps(_r5_seen))
        if os.path.islink(str(_r5_sd)):
            os.unlink(str(_r5_sd))
        if os.path.isdir(str(_r5_moved)):
            os.rename(str(_r5_moved), str(_r5_sd))
        check("recwrite5/parent-swapped-at-the-stat-stat-and-create-stay-on-the-descriptor", _r5_got,
              (None, 1, json.dumps(dict(tag="r5"), sort_keys=True).encode("utf-8"), "0o600", b'{"linked": true}',
               b'{"foreign": true}', ["turn-state.json"], []))
        _r5_real_open, _r5_real_close, _r5_real_fsync = os.open, os.close, os.fsync

        def _r5_closes(fsync):
            opened, closed = [], []

            def _open(path, flags, *args, **kw):
                fd = _r5_real_open(path, flags, *args, **kw)
                opened.append(fd)
                return fd

            def _close(fd):
                closed.append(fd)
                return _r5_real_close(fd)
            before = _r4_fds()
            try:
                os.open, os.close, os.fsync = _open, _close, fsync
                res = aiqt_hooks._orch_write_json_atomic(str(_r5_ts), dict(tag="closes"))
            finally:
                os.open, os.close, os.fsync = _r5_real_open, _r5_real_close, _r5_real_fsync
            return (res, len(opened), [closed.count(fd) for fd in opened], len(closed), _r4_fd_delta(before),
                    _r4_tmps(_r5_sd))
        check("recwrite5/each-save-descriptor-closed-exactly-once-on-success-and-failure",
              (_r5_closes(_r5_real_fsync), _r5_closes(_r4_fail)),
              ((None, 2, [1, 1], 2, 0, []), ("OSError", 2, [1, 1], 2, 0, [])))

        # QA round 6 of the silent-write fixes: the yield residue counter claims. (1) A write-and-search-only
        # (0300) state directory: the schedule denials accumulate there and the fourth call on an unchanged
        # basis is cap-relieved (three denies, then a warn, the counter at 3). The real chmod binds a non-root
        # run; the os.open seam refuses an open of the state directory itself that lacks O_PATH whenever the
        # directory's current mode lacks read, whatever the native O_PATH and euid, so the row also
        # discriminates on a root run and a writer reverted to an O_RDONLY directory open fails it (its saves
        # fail, the count stays unset and the fourth call denies). Where O_PATH is absent the row expects
        # the disclosed fallback, as root too (no count, four denies). (2)
        # Relief already earned survives a later save failure: decide_yield reads the counter before any
        # save, so three denials in a 0700 directory, then the directory set to search only (0100), still
        # relieve, and so does a counter at the cap in a 0300 directory under the O_RDONLY fallback writer;
        # each failed wake-digest save is named in the relief note and leaves the counter at 3. A seam
        # refusing every create in the state directory makes the 0100 save fail on a root run too.
        _r6_opath = getattr(os, "O_PATH", 0)
        _r6_real_open = aiqt_hooks.os.open

        def _r6_run(name, setup_mode, mode, walk=None):
            fx = Fixture(tmp, name)
            sd = Path(aiqt_hooks._orch_state_dir_for_root(str(fx.root)))
            sd.mkdir(parents=True, exist_ok=True)
            ino = os.stat(str(sd)).st_ino
            fx.set_items([item("QA-R6", blocker=dict(kind="not-before", ref=now_iso(-48)))])
            fx.set_turn_state(dict())
            run = lambda: _r2_out(aiqt_hooks.orch_yield_tool(
                fx.payload("PreToolUse", "ScheduleWakeup", dict(prompt="waiting"))))

            def _open(path, flags, *a, **k):
                current = switched[0] if switched else setup_mode
                if (not current & 0o400 and not (_r6_opath and flags & _r6_opath)
                        and isinstance(path, (str, bytes, os.PathLike))
                        and k.get("dir_fd") is None and os.path.abspath(os.fsdecode(path)) == str(sd)):
                    raise PermissionError(13, "Permission denied", os.fsdecode(path))
                if (switched and mode == 0o100 and flags & os.O_CREAT and k.get("dir_fd") is not None
                        and os.fstat(k["dir_fd"]).st_ino == ino):
                    raise PermissionError(13, "Permission denied", os.fsdecode(path))
                return _r6_real_open(path, flags, *a, **k)
            saved_walk = aiqt_hooks._ORCH_O_WALK
            out, switched = [], []
            try:
                aiqt_hooks.os.open = _open
                os.chmod(str(sd), setup_mode)
                out += [run() for _ in range(3)]
                os.chmod(str(sd), mode)
                switched.append(mode)
                if walk is not None:
                    aiqt_hooks._ORCH_O_WALK = walk
                out.append(run())
            finally:
                aiqt_hooks._ORCH_O_WALK = saved_walk
                aiqt_hooks.os.open = _r6_real_open
                os.chmod(str(sd), 0o755)
            return ([o[0] for o in out], "prompt digest could not be written" in out[-1][2],
                    fx.turn_state().get("schedule_denials"))
        check("recwrite6/search-only-state-dir-yield-denials-accumulate-and-cap-relieves",
              _r6_run("recwrite6a", 0o300, 0o300),
              (["deny", "deny", "deny", "warn"], False, 3) if _r6_opath else (["deny"] * 4, False, None))
        check("recwrite6/earned-cap-relief-survives-a-failed-save",
              (_r6_run("recwrite6b", 0o700, 0o100), _r6_run("recwrite6c", 0o700, 0o300, walk=os.O_RDONLY)),
              ((["deny", "deny", "deny", "warn"], True, 3), (["deny", "deny", "deny", "warn"], True, 3)))

        # QA rounds 7 to 10: one sweep pins the counter-save and relief sentences of the orch-yield-tool-guard
        # and orch-stop-guard residues (see _PERM_SWEEP_TABLE), the malformed-count, non-object turn-state,
        # above-the-limit count and stop_hook_active exits included, and that no relief reaches the
        # unreadable-registry deny of a schedule or stop=true call, judged on the default state directory that
        # deny's call resolves; it checks each output channel of a cell on its own: the kernel leg runs the
        # platform's writers without root, the model leg runs both writers everywhere, and every cell matches
        # the table.
        check("recwrite7/state-dir-permission-sweep-matches-the-table",
              _perm_sweep(tmp),
              (0 if os.geteuid() == 0 else 736 if getattr(os, "O_PATH", 0) else 368, 736, []))

        # QA round 10: a count above the loop bound or the cap is a forced exit like one at it, so a Stop, a
        # stop=true call and a schedule call each record forced_unresolved at the limit, one above it, and at
        # the largest valid count (the sweep pins the verdicts; this pins the record).
        _r10 = Fixture(tmp, "recwrite10")
        _r10_sd = Path(aiqt_hooks._orch_state_dir_for_root(str(_r10.root)))
        _r10.set_items([item("R10-B", blocker=dict(kind="not-before", ref=now_iso(-48)))])
        _r10.set_turn_state(dict())
        aiqt_hooks.orch_yield_tool(_r10.payload("PreToolUse", "ScheduleWakeup", dict(prompt="waiting")))
        _r10_basis = _r10.turn_state().get("schedule_basis")

        def _r10_forced(kind, count):
            (_r10_sd / "forced-exit.jsonl").unlink(missing_ok=True)
            if kind == "schedule":
                _r10.set_turn_state(dict(schedule_denials=count, schedule_basis=_r10_basis))
                result = aiqt_hooks.orch_yield_tool(_r10.payload("PreToolUse", "ScheduleWakeup",
                                                                 dict(prompt="waiting")))
            else:
                _r10.set_turn_state(dict(stop_denials=count))
                result = (aiqt_hooks.orch_stop_guard(_r10.payload("Stop")) if kind == "stop" else
                          aiqt_hooks.orch_yield_tool(_r10.payload("PreToolUse", "ScheduleWakeup",
                                                                  dict(prompt="done", stop=True))))
            rows, _bad = aiqt_hooks._orch_read_jsonl(str(_r10_sd / "forced-exit.jsonl"))
            # the record must parse whole: a malformed row is a failure, never a silent skip
            return _verdict(result), len(rows or []), _bad
        _r10_limits = dict(schedule=aiqt_hooks._ORCH_SCHEDULE_CAP, stop=aiqt_hooks._ORCH_LOOP_BOUND,
                           ystop=aiqt_hooks._ORCH_LOOP_BOUND)
        check("recwrite10/forced-exit-recorded-at-and-above-the-bound-and-cap",
              [(kind, count, _r10_forced(kind, count)) for kind, limit in sorted(_r10_limits.items())
               for count in (limit, limit + 1, aiqt_hooks._ORCH_COUNTER_MAX)],
              [(kind, count, ("warn", 1, 0)) for kind, limit in sorted(_r10_limits.items())
               for count in (limit, limit + 1, aiqt_hooks._ORCH_COUNTER_MAX)])

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
        # unrecorded; the gate now records it.
        check("forced5/cap-exit-warns", _verdict(esched({"prompt": "waiting"})), "warn")
        check("forced5/recorded-over-blocked", (esd / "forced-exit.jsonl").exists(), True)
        e1, _b1 = aiqt_hooks._orch_read_jsonl(str(esd / "forced-exit.jsonl"))
        # both reads must parse whole: a malformed row is a failure, never a silent skip
        check("forced5/blocked-id-recorded", (e1[-1].get("open_ids"), _b1), (["BW-1"], 0))
        # a SECOND forced exit appends (append-only), never clobbering the first
        check("forced5/cap-exit-warns-2", _verdict(esched({"prompt": "waiting"})), "warn")
        e2, _b2 = aiqt_hooks._orch_read_jsonl(str(esd / "forced-exit.jsonl"))
        check("forced5/two-rows-appended", (len(e2), _b2), (2, 0))
        # the resume audit surfaces BOTH, then a second resume is clean (the surfaced-set write succeeds
        # here; a failed one would raise them again, at least once)
        check("forced5/resume-surfaces-both",
              _verdict(aiqt_hooks.orch_resume_audit(e.payload("SessionStart"))), "warn")
        check("forced5/second-resume-clean",
              _verdict(aiqt_hooks.orch_resume_audit(e.payload("SessionStart"))), "allow")

        # The fail-closed stop on an enumerator error (self-discriminating): under a whole-enumerator
        # failure (cannot-evaluate) a BELOW-BOUND stop DENIES with NO escape, and an operator-owned escape
        # sentinel STILL releases it. Asserting BOTH here makes the leg fail AT THIS LEG when the stop
        # path stops failing closed on an enumerator error (the no-escape verdict flips block2 -> warn),
        # not only at the pure-core legs (core/stop-enum-error-denies, core/stop-cannot-evaluate-denies)
        # or the f/ce integration legs above. Proves the escape (not a fail-open) is the release.
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
        # The fail-closed stop and the forced-exit record together: the below-bound cannot-evaluate
        # DENIES (ce/enum-error-denies -> block2 below discriminates the fail-closed stop at this leg: a
        # stop path that stopped failing closed flips it to warn) and records NO forced exit (the
        # forced-exit record is written only on the bounded ALLOW_WITH_FINDINGS, never on a deny); the
        # SAME enum-error, once the loop bound is reached, DOES record one. So ce/no-forced-exit-on-deny
        # is read against ce/enum-error-denies, discriminating the deny (no record) from the unchanged
        # bound-exit.
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
        _leasehb_cases(tmp)
        _leasehb_fix2_cases(tmp)
        _leasehb_write_integrity_cases(tmp)
        _leasehb_status_and_parse_cases(tmp)
        _rdp_cases(tmp)
    finally:
        aiqt_hooks._orch_dirfd_has_registry = saved_probe
        shutil.rmtree(tmp, ignore_errors=True)

    # In-run execution-set self-guard (defence in depth beside tools/check_selftest_execution.py): the
    # executed set reconciles against the hand-authored expectation manifest even on a direct developer
    # run. The report is finalized at interpreter exit, so it always reflects what actually executed.
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
          "foreground bare-& detach (historically an ASK for both), allows-with-note a readable foreground "
          "detach where no registry is found (naming the tracked background mechanism; redirect operands "
          "keep their command position pending), while dropping a word-start `#` comment "
          "and failing a scan that ends inside an open quote toward a deny with its own reason (a quote the "
          "scan misreads in mid-string, such as an ANSI-C escaped quote or a quote in a here-document body, "
          "can still shift it into a disclosed silent allow, and a safe here-document body '&' is a "
          "disclosed over-refusal), reads a '#' comment by bash's word-start rule as well, denies every "
          "Bash call that passes the pre-scope checks when the opt-in registry-required mode is set and no registry is found or the nearest registry entry cannot be evaluated (a discovery fault never satisfies that mode; by default it keeps the guard active), and fails "
          "closed on a missing or unreadable tool_name, an unreadable cwd or one whose registry walk cannot "
          "be carried out (scope is the ancestor walk with its concurrent-move recheck, unioned with "
          "a git-resolved toplevel; by default a git failure alone never denies, while the "
          "registry-required mode denies one that hides a registry reachable only through the git "
          "toplevel), a malformed tool_input, "
          "run_in_background, or command, and on any stdin the dispatcher cannot parse; the ledger "
          "records launches "
          "and completions; the resume "
          "audit arms and clears the mutation barrier on real record state (best-effort and atomic: a "
          "barrier write that fails, before or after the temporary file is opened, leaves the previous "
          "barrier byte-identical while the warning still surfaces, and a barrier file that is unreadable "
          "or malformed reads as armed); the prompt stamp "
          "resets guard counters from genuine human input; an actor-owned, symlinked, or writable "
          "escape sentinel is ignored, recorded (or its failed record warned about), and normally "
          "raised once at resume; a declared attestation "
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
    _selftest_exit_report.exit_with(main(report_path=_parse_argv(sys.argv[1:])))
