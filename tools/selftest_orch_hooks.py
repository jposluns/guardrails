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

import builtins
import errno
import json
import os
import subprocess
import tempfile
import shutil
import threading
import time
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
    _rdp_scope_cases(base, bare)


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
    # The binding record (D-433-FAILCLOSED-BINDING): once the hook has seen the registry bind, a registry
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


def _cite_prune_in(directory, keep, now_ns):
    """aiqt_hooks._cite_prune run on the directory at path directory: the number of files it removed."""
    dirfd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    try:
        return aiqt_hooks._cite_prune(dirfd, keep, now_ns)
    finally:
        os.close(dirfd)


def _citeledger_cases(tmp):
    """The citation read recorder (aiqt_hooks.citation_read_recorder): the rows each recognized tool call
    earns, the shapes that record nothing, the owner-only modes, the refused symlink, rotation,
    deduplication, pruning and the size bounds (each bound tried just inside, at and just outside it), and
    the surfaced write failure. Every tool_response here is built by hand in the shape the recorder
    recognizes; none was captured from a live session."""
    root = tmp / "citeledger"
    tools_dir = root / "tools"
    tools_dir.mkdir(parents=True)
    a_py, b_py, c_py = tools_dir / "a.py", tools_dir / "b.py", tools_dir / "c.py"
    a_bytes = b"alpha = 1\nbeta = 2\ngamma = 3\ndelta = 4\nepsilon = 5\n"
    a_py.write_bytes(a_bytes)
    b_py.write_bytes(b"other\n")
    for args in (["init", "-q", "-b", "main"], ["add", "tools"],
                 ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(root)] + args, check=True, capture_output=True, timeout=30)
    state = Path(aiqt_hooks._orch_state_dir_for_root(str(root)))
    ledger_dir = state / "citation-ledger"
    verdicts = []

    def ledger(session):
        return ledger_dir / (hashlib.sha256(session.encode("utf-8")).hexdigest()[:16] + ".jsonl")

    def rows(session):
        path = ledger(session)
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def spans(session):
        return [(r["via"], r["rel"], r["start"], r["end"]) for r in rows(session)]

    def call(tool, tool_input, response, session, event="PostToolUse"):
        data = {"hook_event_name": event, "cwd": str(root), "tool_name": tool, "tool_input": tool_input,
                "tool_response": response}
        if session is not None:
            data["session_id"] = session
        result = aiqt_hooks.citation_read_recorder(data)
        verdicts.append(_verdict(result))
        return result

    def read(content, start, count, path=a_py, kind="text"):
        return {"type": kind, "file": {"filePath": str(path), "content": content, "numLines": count,
                                       "startLine": start, "totalLines": 6}}

    def bash(command, stdout, session):
        return call("Bash", {"command": command}, {"stdout": stdout, "stderr": "", "interrupted": False,
                                                    "isImage": False}, session)

    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def mode(path):  # None for a missing path, so a recorder that writes nothing fails a check, not the run
        return oct(os.stat(str(path)).st_mode & 0o777) if os.path.lexists(str(path)) else None

    def size(path):
        return os.stat(str(path)).st_size if os.path.lexists(str(path)) else -1

    # The ledger directory and file are owner-only under a permissive umask, and wider modes found later
    # are narrowed again on the next append (red when the explicit modes are dropped for umask defaults).
    saved_umask = os.umask(0o022)
    try:
        call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "modes")
        fresh = (mode(ledger_dir), mode(ledger("modes")))
        for path, wide in ((ledger_dir, 0o755), (ledger("modes"), 0o644)):
            if os.path.lexists(str(path)):
                os.chmod(str(path), wide)
        call("Read", {"file_path": str(a_py)}, read("beta = 2", 2, 1), "modes")
        narrowed = (mode(ledger_dir), mode(ledger("modes")))
    finally:
        os.umask(saved_umask)
    check("citeledger/ledger-owner-only", (fresh, narrowed), (("0o700", "0o600"), ("0o700", "0o600")))

    # Read: the span comes from startLine and numLines, confirmed line by line against the file, and the
    # digest is of the raw bytes of those lines as read from disk.
    call("Read", {"file_path": str(a_py), "offset": 3, "limit": 2}, read("gamma = 3\ndelta = 4", 3, 2),
         "read-span")
    check("citeledger/read-span-recorded",
          [(r["kind"], r["via"], r["path"], r["rel"], r["start"], r["end"], r["sha256"], r["rev"])
           for r in rows("read-span")],
          [("read", "Read", os.path.realpath(str(a_py)), "tools/a.py", 3, 4,
            digest(b"gamma = 3\ndelta = 4\n"), None)])
    # A whole-file read shows an empty line after the final line feed; it is no line of the file, whether
    # numLines counts it or not.
    call("Read", {"file_path": str(a_py)}, read(a_bytes.decode("utf-8"), 1, 6), "read-whole-6")
    call("Read", {"file_path": str(a_py)}, read(a_bytes.decode("utf-8"), 1, 5), "read-whole-5")
    check("citeledger/read-final-line-feed-not-a-line", (spans("read-whole-6"), spans("read-whole-5")),
          ([("Read", "tools/a.py", 1, 5)], [("Read", "tools/a.py", 1, 5)]))
    # Shapes the recorder does not recognize record nothing, never a guessed span: the span a 0-based
    # offset would claim, cat -n prefixed text, a non-integer or boolean field, a count the content
    # disagrees with, an image read, a response naming another file, no file object, a bare string, a
    # zero startLine.
    unknown_read = (
        ("zero-based-offset", read("gamma = 3\ndelta = 4", 2, 2)),
        ("cat-n-prefixed", read("     3\tgamma = 3\n     4\tdelta = 4", 3, 2)),
        ("string-start", read("gamma = 3\ndelta = 4", "3", 2)),
        ("bool-count", read("gamma = 3", 3, True)),
        ("count-disagrees", read("gamma = 3\ndelta = 4", 3, 3)),
        ("image", read("gamma = 3", 3, 1, kind="image")),
        ("other-file", read("other", 1, 1, path=b_py)),
        ("no-file-object", {"type": "text"}),
        ("bare-string", "     3\tgamma = 3"),
        ("zero-start", read("alpha = 1", 0, 1)))
    for name, response in unknown_read:
        call("Read", {"file_path": str(a_py), "offset": 3, "limit": 2}, response, "unknown-read-" + name)
    check("citeledger/read-unknown-shape-records-nothing",
          [name for name, _resp in unknown_read if ledger("unknown-read-" + name).exists()], [])

    # No session id, an empty one, or an event other than PostToolUse: nothing is written.
    call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), None)
    call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "")
    call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "pre-event", event="PreToolUse")
    check("citeledger/no-session-no-row",
          (sorted(p.name for p in (ledger_dir.iterdir() if ledger_dir.is_dir() else ()) if p.name.startswith(
              hashlib.sha256(b"").hexdigest()[:16])), ledger("pre-event").exists()), ([], False))
    # A malformed payload (a tool_name that is not a string, a tool_input that is not an object, a path
    # holding a NUL) allows silently and records nothing, never a handler crash.
    malformed = []
    for name, tool, tool_input, response in (
            ("tool-list", ["Read"], {"file_path": str(a_py)}, read("alpha = 1", 1, 1)),
            ("input-list", "Read", [str(a_py)], read("alpha = 1", 1, 1)),
            ("nul-path", "Read", {"file_path": str(a_py) + "\x00"}, read("alpha = 1", 1, 1))):
        try:
            outcome = call(tool, tool_input, response, "malformed-" + name)
        except Exception as exc:  # a handler crash fails this check rather than ending the run
            outcome = "crash: {}".format(type(exc).__name__)
        malformed.append((outcome, ledger("malformed-" + name).exists()))
    check("citeledger/malformed-payload-records-nothing", malformed, [((0, None, None), False)] * 3)

    # Grep, content mode: hit and context lines of a directory search (paths relative to cwd, consecutive
    # lines merged into one span) and the N:TEXT lines of a one-file search.
    call("Grep", {"pattern": "gamma|other", "path": str(root), "output_mode": "content", "-n": True, "-A": 1},
         {"mode": "content", "numFiles": 2, "filenames": [], "numLines": 4,
          "content": "tools/a.py:3:gamma = 3\ntools/a.py-4-delta = 4\n--\ntools/b.py:1:other"}, "grep-dir")
    call("Grep", {"pattern": "beta", "path": str(a_py), "output_mode": "content"},
         {"mode": "content", "numFiles": 1, "filenames": [], "numLines": 1, "content": "2:beta = 2"},
         "grep-file")
    check("citeledger/grep-hit-recorded", (spans("grep-dir"), spans("grep-file")),
          ([("Grep", "tools/a.py", 3, 4), ("Grep", "tools/b.py", 1, 1)], [("Grep", "tools/a.py", 2, 2)]))
    # A line that splits into a line of two different files is ambiguous, and one whose text is not the
    # file's line, a files-only search and a search without line numbers name no line: nothing is recorded.
    (tools_dir / "c").write_bytes(b"d:1:e\n")
    (tools_dir / "c:1:d").write_bytes(b"e\n")
    grep_unknown = (
        ("ambiguous", {"pattern": "e", "path": str(root), "output_mode": "content"},
         {"mode": "content", "content": "tools/c:1:d:1:e"}),
        ("text-differs", {"pattern": "g", "path": str(root), "output_mode": "content"},
         {"mode": "content", "content": "tools/a.py:3:gamma = 33"}),
        ("files-only", {"pattern": "g", "path": str(root)},
         {"mode": "files_with_matches", "filenames": ["tools/a.py"], "content": "tools/a.py:3:gamma = 3"}),
        ("no-line-numbers", {"pattern": "g", "path": str(root), "output_mode": "content", "-n": False},
         {"mode": "content", "content": "tools/a.py:3:gamma = 3"}))
    for name, tool_input, response in grep_unknown:
        call("Grep", tool_input, response, "unknown-grep-" + name)
    check("citeledger/grep-unrecognized-records-nothing",
          [name for name, _i, _r in grep_unknown if ledger("unknown-grep-" + name).exists()], [])

    # git show REV:PATH: lines 1..k, digested from the blob, which here differs from the working tree.
    a_py.write_bytes(a_bytes.replace(b"beta = 2", b"beta = 22"))
    bash("git show HEAD:tools/a.py", a_bytes.decode("utf-8"), "git-show")
    bash("git --no-pager -C {} show HEAD:tools/a.py".format(root), a_bytes.decode("utf-8")[:-1],
         "git-show-c")
    a_py.write_bytes(a_bytes)
    check("citeledger/git-show-recorded",
          ([(r["via"], r["rel"], r["start"], r["end"], r["sha256"], r["rev"]) for r in rows("git-show")],
           spans("git-show-c")),
          ([("git-show", "tools/a.py", 1, 5, digest(a_bytes), "HEAD")], [("git-show", "tools/a.py", 1, 5)]))
    # grep -n, rg -n and sed -n 'A,Bp' as one plain command each.
    bash("grep -n beta tools/a.py", "2:beta = 2\n", "bash-grep")
    bash("rg -n -C1 gamma tools", "tools/a.py-2-beta = 2\ntools/a.py:3:gamma = 3\ntools/a.py-4-delta = 4\n",
         "bash-rg")
    bash("sed -n '2,3p' tools/a.py", "beta = 2\ngamma = 3\n", "bash-sed")
    check("citeledger/bash-read-recorded", (spans("bash-grep"), spans("bash-rg"), spans("bash-sed")),
          ([("grep", "tools/a.py", 2, 2)], [("grep", "tools/a.py", 2, 4)], [("sed", "tools/a.py", 2, 3)]))
    # Bash shapes that record nothing: a pipeline, a trailing separator, a redirect, an unrecorded command,
    # a commit operand, a relative -C, output that is not the blob, grep without -n, a reversed sed range,
    # an interrupted call, and a response with no stdout string.
    bash_unknown = (
        ("pipeline", "git show HEAD:tools/a.py | cat", a_bytes.decode("utf-8")),
        ("trailing-separator", "git show HEAD:tools/a.py;", a_bytes.decode("utf-8")),
        ("redirect", "git show HEAD:tools/a.py 2>/dev/null", a_bytes.decode("utf-8")),
        ("cat", "cat tools/a.py", a_bytes.decode("utf-8")),
        ("commit-operand", "git show HEAD", a_bytes.decode("utf-8")),
        ("relative-c", "git -C tools show HEAD:tools/a.py", a_bytes.decode("utf-8")),
        ("not-the-blob", "git show HEAD:tools/a.py", a_bytes.decode("utf-8").replace("beta", "BETA")),
        ("grep-no-n", "grep beta tools/a.py", "beta = 2\n"),
        ("sed-reversed", "sed -n '3,2p' tools/a.py", "gamma = 3\n"))
    for name, command, stdout in bash_unknown:
        bash(command, stdout, "unknown-bash-" + name)
    call("Bash", {"command": "sed -n '2p' tools/a.py"}, {"stdout": "beta = 2\n", "interrupted": True},
         "unknown-bash-interrupted")
    call("Bash", {"command": "sed -n '2p' tools/a.py"}, {"output": "beta = 2\n"}, "unknown-bash-no-stdout")
    check("citeledger/bash-unrecognized-records-nothing",
          [name for name in [n for n, _c, _s in bash_unknown] + ["interrupted", "no-stdout"]
           if ledger("unknown-bash-" + name).exists()], [])

    # Write is the whole written file and Edit the lines of its new text, both as "wrote" rows; a Write
    # whose file does not hold the written content records nothing.
    c_py.write_bytes(b"x = 1\ny = 2\n")
    call("Write", {"file_path": str(c_py), "content": "x = 1\ny = 2\n"}, {"filePath": str(c_py), "success": True},
         "write")
    call("Write", {"file_path": str(c_py), "content": "x = 9\n"}, {"filePath": str(c_py), "success": True},
         "write-differs")
    c_py.write_bytes(b"x = 1\ny = 2\nz = 3\n")
    call("Edit", {"file_path": str(c_py), "old_string": "y = 2\n", "new_string": "y = 2\nz = 3\n"},
         {"filePath": str(c_py)}, "edit")
    check("citeledger/write-edit-recorded",
          ([(r["kind"], r["via"], r["rel"], r["start"], r["end"]) for r in rows("write")],
           ledger("write-differs").exists(),
           [(r["kind"], r["via"], r["rel"], r["start"], r["end"]) for r in rows("edit")]),
          ([("wrote", "Write", "tools/c.py", 1, 2)], False, [("wrote", "Edit", "tools/c.py", 2, 3)]))

    # A file of exactly _CITE_MAX_FILE bytes is read; one byte more is not.
    saved_max = aiqt_hooks._CITE_MAX_FILE
    try:
        sizes = []
        for limit in (len(a_bytes) + 1, len(a_bytes), len(a_bytes) - 1):
            aiqt_hooks._CITE_MAX_FILE = limit
            call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "max-file-{}".format(limit))
            sizes.append(ledger("max-file-{}".format(limit)).exists())
    finally:
        aiqt_hooks._CITE_MAX_FILE = saved_max
    check("citeledger/max-file-boundary", sizes, [True, True, False])

    # The writer's bounds, driven through _cite_append with one fixed row of L bytes after F filler bytes.
    def row(start):
        return {"v": 1, "ts": "t", "kind": "read", "via": "Read", "path": "/x/p.py", "rel": None,
                "start": start, "end": start, "sha256": "0" * 64, "rev": None}

    width = len((json.dumps(row(1), sort_keys=True) + "\n").encode("utf-8"))
    filler = b"#" * 99 + b"\n"
    saved = (aiqt_hooks._CITE_CAP, aiqt_hooks._CITE_DEDUP_BELOW, aiqt_hooks._CITE_ROW_MAX)
    try:
        # rotation: a cap of F+L+1 and of F+L keeps one file (it ends at most at the cap); F+L-1 rotates.
        rotated = []
        for name, cap in (("inside", len(filler) + width + 1), ("at", len(filler) + width),
                          ("outside", len(filler) + width - 1)):
            ledger("rot-" + name).parent.mkdir(parents=True, exist_ok=True)
            ledger("rot-" + name).write_bytes(filler)
            aiqt_hooks._CITE_CAP = cap
            failure = aiqt_hooks._cite_append(str(state), "rot-" + name, [row(1)])
            older = Path(str(ledger("rot-" + name)) + ".1")
            rotated.append((failure, ledger("rot-" + name).stat().st_size,
                            older.stat().st_size if older.exists() else None))
        check("citeledger/rotation-cap-boundary", rotated,
              [(None, len(filler) + width, None), (None, len(filler) + width, None),
               (None, width, len(filler))])
        aiqt_hooks._CITE_CAP = saved[0]
        # deduplication: the held row is skipped below the bound (F+L < F+L+1) and appended again at it
        # (F+L == F+L) and above it (F+L > F+L-1).
        deduped = []
        for name, bound in (("inside", len(filler) + width + 1), ("at", len(filler) + width),
                            ("outside", len(filler) + width - 1)):
            ledger("dedup-" + name).write_bytes(filler)
            aiqt_hooks._cite_append(str(state), "dedup-" + name, [row(1)])
            aiqt_hooks._CITE_DEDUP_BELOW = bound
            aiqt_hooks._cite_append(str(state), "dedup-" + name, [row(1)])
            aiqt_hooks._CITE_DEDUP_BELOW = saved[1]
            deduped.append(ledger("dedup-" + name).stat().st_size)
        check("citeledger/dedup-boundary", deduped,
              [len(filler) + width, len(filler) + 2 * width, len(filler) + 2 * width])
        # row size: a JSON line of L bytes is kept under a bound of L+1 and dropped at L and at L-1.
        kept = []
        for name, bound in (("inside", width + 1), ("at", width), ("outside", width - 1)):
            aiqt_hooks._CITE_ROW_MAX = bound
            aiqt_hooks._cite_append(str(state), "row-" + name, [row(1)])
            kept.append(ledger("row-" + name).exists())
        check("citeledger/row-max-boundary", kept, [True, False, False])
    finally:
        aiqt_hooks._CITE_CAP, aiqt_hooks._CITE_DEDUP_BELOW, aiqt_hooks._CITE_ROW_MAX = saved

    # Rotation through the hook, with a cap of three rows: the fourth read rotates the first three onto .1
    # (one older generation), and the current file stays at or under the cap.
    saved_cap = aiqt_hooks._CITE_CAP
    try:
        for start in range(1, 6):
            line = a_bytes.split(b"\n")[start - 1].decode("utf-8")
            call("Read", {"file_path": str(a_py)}, read(line, start, 1), "rot-hook")
            if start == 1:
                aiqt_hooks._CITE_CAP = 3 * max(size(ledger("rot-hook")), 1)
        older = Path(str(ledger("rot-hook")) + ".1")
        older_starts = [json.loads(x)["start"] for x in older.read_text(encoding="utf-8").splitlines()] \
            if older.exists() else []
        check("citeledger/rotation-bounded",
              (0 <= size(ledger("rot-hook")) <= aiqt_hooks._CITE_CAP, older_starts,
               [r["start"] for r in rows("rot-hook")]),
              (True, [1, 2, 3], [4, 5]))
    finally:
        aiqt_hooks._CITE_CAP = saved_cap

    # Pruning keeps a file modified exactly 14 days before now or later, removes one a nanosecond older,
    # and passes over a symlink, another name shape and the session's own files; at most 50 per call.
    prune_dir = tmp / "cite-prune"
    prune_dir.mkdir()
    now_ns = 1_800_000_000 * 10 ** 9
    age = aiqt_hooks._CITE_PRUNE_AGE_NS
    keep = "f" * 16 + ".jsonl"
    stamps = {"a" * 16 + ".jsonl": now_ns - age + 1, "b" * 16 + ".jsonl": now_ns - age,
              "c" * 16 + ".jsonl": now_ns - age - 1, "d" * 16 + ".jsonl.1": now_ns - age - 1,
              keep: now_ns - age - 1, keep + ".1": now_ns - age - 1, "notes.txt": now_ns - age - 1}
    for name, when in stamps.items():
        (prune_dir / name).write_bytes(b"x\n")
        os.utime(str(prune_dir / name), ns=(when, when))
    os.symlink(str(prune_dir / "notes.txt"), str(prune_dir / ("e" * 16 + ".jsonl")))
    removed = _cite_prune_in(prune_dir, keep, now_ns)
    check("citeledger/prune-boundary", (removed, sorted(p.name for p in prune_dir.iterdir())),
          (2, sorted(["a" * 16 + ".jsonl", "b" * 16 + ".jsonl", "e" * 16 + ".jsonl", keep, keep + ".1",
                      "notes.txt"])))
    # The prune count: 49, 50 and 51 old files leave none, none and one.
    pruned = []
    for total in (49, 50, 51):
        many_dir = tmp / "cite-prune-many-{}".format(total)
        many_dir.mkdir()
        for index in range(total):
            old = many_dir / ("{:016x}.jsonl".format(index))
            old.write_bytes(b"x\n")
            os.utime(str(old), ns=(now_ns - age - 1, now_ns - age - 1))
        pruned.append((_cite_prune_in(many_dir, keep, now_ns), len(list(many_dir.iterdir()))))
    check("citeledger/prune-at-most-fifty", pruned, [(49, 0), (50, 0), (50, 1)])
    # Through the hook: a new session file prunes an old ledger file; an append to an existing one does not.
    stale = ledger_dir / ("9" * 16 + ".jsonl")
    stale.write_bytes(b"x\n")
    old_ns = time.time_ns() - age - 10 ** 9
    os.utime(str(stale), ns=(old_ns, old_ns))
    call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "read-span")
    kept_on_append = stale.exists()
    call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "prune-new-session")
    check("citeledger/new-session-prunes-old", (kept_on_append, stale.exists()), (True, False))

    # A write that fails is surfaced, never swallowed: a regular file where the ledger directory belongs,
    # and a symlink at the session file, whose target is left untouched.
    saved_xdg = os.environ.get("XDG_STATE_HOME")
    try:
        os.environ["XDG_STATE_HOME"] = str(tmp / "cite-alt-xdg")
        alt = Path(aiqt_hooks._orch_state_dir_for_root(str(root)))
        alt.mkdir(parents=True)
        (alt / "citation-ledger").write_bytes(b"not a directory\n")
        failed = call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "fail")
    finally:
        if saved_xdg is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved_xdg
    note = failed[1].get("systemMessage", "") if isinstance(failed[1], dict) else ""
    check("citeledger/write-failure-noted",
          (_verdict(failed), "citation-ledger write failed (FileExistsError" in note), ("warn", True))
    target = tmp / "cite-symlink-target.txt"
    target.write_bytes(b"keep\n")
    os.symlink(str(target), str(ledger("symlinked")))
    linked = call("Read", {"file_path": str(a_py)}, read("alpha = 1", 1, 1), "symlinked")
    note = linked[1].get("systemMessage", "") if isinstance(linked[1], dict) else ""
    check("citeledger/symlink-ledger-refused",
          (_verdict(linked), "citation-ledger write failed" in note, target.read_bytes()), ("warn", True, b"keep\n"))

    # The recorder never decides a permission: every call above allowed, silently or with a note.
    check("citeledger/never-denies", sorted(set(verdicts)), ["allow", "warn"])


class _CiteRepo:
    """A committed git repository for the citation recorder's cases: files (bytes) and symlinks (target
    text) written under root and committed, calls made as PostToolUse payloads with cwd at root, and the
    rows each session's ledger holds."""

    def __init__(self, root, files, links=()):
        self.root = root
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        for rel, target in links:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, str(root / rel))
        for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                      "commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-C", str(root)] + args, check=True, capture_output=True, timeout=30)
        self.top = os.path.realpath(str(root))
        self.ledger_dir = Path(aiqt_hooks._orch_state_dir_for_root(str(root))) / "citation-ledger"
        self.verdicts = []

    def ledger(self, session):
        return self.ledger_dir / (hashlib.sha256(session.encode("utf-8")).hexdigest()[:16] + ".jsonl")

    def rows(self, session):
        path = self.ledger(session)
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def spans(self, session):
        return [(r["via"], r["rel"], r["start"], r["end"]) for r in self.rows(session)]

    def call(self, tool, tool_input, response, session):
        result = aiqt_hooks.citation_read_recorder({
            "hook_event_name": "PostToolUse", "cwd": str(self.root), "session_id": session, "tool_name": tool,
            "tool_input": tool_input, "tool_response": response})
        self.verdicts.append(_verdict(result))
        return result

    def bash(self, command, stdout, session):
        return self.call("Bash", {"command": command}, {"stdout": stdout, "stderr": "", "interrupted": False},
                         session)

    def grep(self, path, content, session):
        tool_input = {"pattern": "p", "output_mode": "content", "-n": True}
        if path is not None:
            tool_input["path"] = str(path)
        return self.call("Grep", tool_input, {"mode": "content", "content": content}, session)


class _CiteClock:
    """Stands in for the time module inside aiqt_hooks: monotonic() returns first until the clock is
    advanced and later from then on, so a deadline set from the first reading is met by later exactly as
    chosen. With step, the first reading advances it; otherwise advance() does (called here by a stand-in
    toplevel probe). Every other attribute is the real time module's."""

    def __init__(self, first, later, step):
        self.first, self.later, self.step, self.advanced = first, later, step, False

    def monotonic(self):
        value = self.later if self.advanced else self.first
        self.advanced = self.advanced or self.step
        return value

    def advance(self):
        self.advanced = True

    def __getattr__(self, name):
        return getattr(time, name)


def _citeledger_show_cases(tmp):
    """git show REV:PATH is recorded against the repository path and revision it names, never a
    working-tree file the path now resolves to: a committed symlink records nothing, and a path the working
    tree has replaced by a symlink, lost under a symlinked parent, replaced by a directory, or removed is
    recorded at that path. A repository other than the session's (here one nested inside it) leaves rel
    null."""
    blobs = {"tools/a.py": b"alpha = 1\n", "tools/b.py": b"bravo = 2\n", "tools/sub/s.py": b"sub = 3\n",
             "tools/d.py": b"delta = 4\n", "tools/gone.py": b"gone = 5\n", "tools/elsewhere/keep.txt": b"keep\n"}
    repo = _CiteRepo(tmp / "cite-show", blobs, links=[("tools/link", "b.py")])
    tools_dir = repo.root / "tools"
    os.unlink(str(tools_dir / "a.py"))
    os.symlink("b.py", str(tools_dir / "a.py"))
    shutil.rmtree(str(tools_dir / "sub"))
    os.symlink("elsewhere", str(tools_dir / "sub"))
    os.unlink(str(tools_dir / "d.py"))
    (tools_dir / "d.py").mkdir()
    os.unlink(str(tools_dir / "gone.py"))
    nested = repo.root / "vendor" / "inner"
    nested.mkdir(parents=True)
    (nested / "tools").mkdir()
    (nested / "tools" / "a.py").write_bytes(b"inner = 6\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(nested)] + args, check=True, capture_output=True, timeout=30)
    cases = (("symlink-entry", "git show HEAD:tools/link", "b.py"),
             ("replaced-by-symlink", "git show HEAD:tools/a.py", "alpha = 1\n"),
             ("parent-replaced-by-symlink", "git show HEAD:tools/sub/s.py", "sub = 3\n"),
             ("replaced-by-directory", "git show HEAD:tools/d.py", "delta = 4\n"),
             ("removed", "git show HEAD:tools/gone.py", "gone = 5\n"),
             ("nested-repository", "git -C {} show HEAD:tools/a.py".format(nested), "inner = 6\n"))
    got = {}
    for name, command, stdout in cases:
        repo.bash(command, stdout, "show-" + name)
        got[name] = [(r["path"], r["rel"], r["start"], r["end"], r["sha256"], r["rev"])
                     for r in repo.rows("show-" + name)]

    def want(rel, data, top=repo.top, named=True):
        return [(os.path.join(top, rel), rel if named else None, 1, 1, hashlib.sha256(data).hexdigest(), "HEAD")]
    check("citeledger/git-show-names-blob-path", got, {
        "symlink-entry": [],
        "replaced-by-symlink": want("tools/a.py", b"alpha = 1\n"),
        "parent-replaced-by-symlink": want("tools/sub/s.py", b"sub = 3\n"),
        "replaced-by-directory": want("tools/d.py", b"delta = 4\n"),
        "removed": want("tools/gone.py", b"gone = 5\n"),
        "nested-repository": want("tools/a.py", b"inner = 6\n", top=os.path.realpath(str(nested)), named=False)})


def _citeledger_edit_cases(tmp):
    """An Edit records only the lines its new text wholly occupies after the edit, at the edit's own
    position: from originalFile in the response where it is given (the replacement must reproduce the file
    on disk), else at the one occurrence of the new text; never another occurrence, never a line the new
    text only partly covers, and nothing where the position is ambiguous or the file disagrees."""
    repo = _CiteRepo(tmp / "cite-edit", {"seed.txt": b"seed\n"})
    cases = (
        ("partial-line", "x = 9\nsecret_token_two = 2\nw = 2\n", "9", "2", False, True),
        ("partial-line-no-original", "x = 9\nsecret_token_two = 2\nw = 2\n", "9", "2", False, False),
        ("other-occurrence", "keep\nOLD\nNEW\n", "OLD", "NEW", False, True),
        ("ambiguous-no-original", "keep\nOLD\nNEW\n", "OLD", "NEW", False, False),
        ("multi-line-partial-ends", "x = 1\ny = 2\n", "1\ny", "1\nz = 3\ny", False, True),
        ("replace-all", "a OLD\nOLD\nb\n", "OLD", "NEW", True, True),
        ("replace-all-no-original", "a OLD\nOLD\nb\n", "OLD", "NEW", True, False),
        ("whole-lines", "a\nb\nc\n", "b\n", "B1\nB2\n", False, True),
        ("unique-no-original", "m = 1\nold line\n", "old line\n", "new line\n", False, False),
        ("crlf-file", "a\r\nb\r\nc\r\n", "b", "B", False, True))
    got = {}
    for name, before, old, new, every, original in cases:
        path = repo.root / (name + ".txt")
        path.write_bytes((before.replace(old, new) if every else before.replace(old, new, 1)).encode("utf-8"))
        response = {"filePath": str(path)}
        if original:
            response["originalFile"] = before
        tool_input = {"file_path": str(path), "old_string": old, "new_string": new}
        if every:
            tool_input["replace_all"] = True
        repo.call("Edit", tool_input, response, "edit-" + name)
        got[name] = [(r["kind"], r["start"], r["end"]) for r in repo.rows("edit-" + name)]
    # The file on disk no longer equals originalFile with the edit made: nothing is recorded.
    drifted = repo.root / "drifted.txt"
    drifted.write_bytes(b"q = 3\nq = 2\n")
    repo.call("Edit", {"file_path": str(drifted), "old_string": "q = 1", "new_string": "q = 2"},
              {"filePath": str(drifted), "originalFile": "q = 1\n"}, "edit-drifted")
    got["drifted"] = [(r["kind"], r["start"], r["end"]) for r in repo.rows("edit-drifted")]
    check("citeledger/edit-own-lines-only", got, {
        "partial-line": [], "partial-line-no-original": [], "other-occurrence": [("wrote", 2, 2)],
        "ambiguous-no-original": [], "multi-line-partial-ends": [("wrote", 2, 2)],
        "replace-all": [("wrote", 2, 2)], "replace-all-no-original": [], "whole-lines": [("wrote", 2, 3)],
        "unique-no-original": [("wrote", 2, 2)], "crlf-file": [("wrote", 2, 2)], "drifted": []})


def _citeledger_scope_cases(tmp):
    """A Grep or grep/rg hit counts only where its resolved path is the searched file or lies under the
    searched directory (cwd when none is named), and the line-number flag is read as grep and rg parse it:
    the n of -en is -e's pattern, and a later -N or --no-line-number turns numbering off again."""
    repo = _CiteRepo(tmp / "cite-scope", {
        "tools/searched/s.txt": b"seen\n", "tools/unsearched/u.txt": b"secret\n", "tools/a.py": b"alpha = 1\n",
        "tools/b.py": b"other\n", "tools/e.txt": b"3:n\nx\nn\n"},
        links=[("tools/searched/ln.txt", "../unsearched/u.txt")])
    tools_dir = repo.root / "tools"
    repo.grep(tools_dir / "searched", "tools/unsearched/u.txt:1:secret", "scope-dir-outside")
    repo.grep(tools_dir / "a.py", "tools/b.py:1:other", "scope-file-outside")
    repo.grep(tools_dir / "searched", "tools/searched/ln.txt:1:secret", "scope-link-outside")
    repo.grep(tools_dir / "searched", "tools/searched/s.txt:1:seen", "scope-dir-inside")
    repo.grep(None, "tools/b.py:1:other", "scope-no-path")
    repo.bash("grep -rn secret tools/searched", "tools/unsearched/u.txt:1:secret\n", "scope-bash-outside")
    repo.bash("rg -n seen tools/searched", "tools/searched/s.txt:1:seen\n", "scope-bash-inside")
    names = ("dir-outside", "file-outside", "link-outside", "dir-inside", "no-path", "bash-outside",
             "bash-inside")
    check("citeledger/grep-scope", {name: repo.spans("scope-" + name) for name in names}, {
        "dir-outside": [], "file-outside": [], "link-outside": [],
        "dir-inside": [("Grep", "tools/searched/s.txt", 1, 1)], "no-path": [("Grep", "tools/b.py", 1, 1)],
        "bash-outside": [], "bash-inside": [("grep", "tools/searched/s.txt", 1, 1)]})
    # tools/e.txt is "3:n", "x", "n": unnumbered output of its lines 1 and 3 reads "3:n" and "n", which
    # a numbered reading would take as line 3.
    flags = (("rg-n-then-N", "rg -n -N n tools/e.txt", "3:n\nn\n"),
             ("rg-long-off", "rg --line-number --no-line-number n tools/e.txt", "3:n\nn\n"),
             ("grep-e-cluster", "grep -en tools/e.txt", "3:n\nn\n"),
             ("grep-n-cluster", "grep -in n tools/e.txt", "1:3:n\n3:n\n"),
             ("grep-e-then-n", "grep -e n -n tools/e.txt", "1:3:n\n3:n\n"))
    for name, command, stdout in flags:
        repo.bash(command, stdout, "flag-" + name)
    check("citeledger/grep-flag-parse", {name: repo.spans("flag-" + name) for name, _c, _s in flags}, {
        "rg-n-then-N": [], "rg-long-off": [], "grep-e-cluster": [],
        "grep-n-cluster": [("grep", "tools/e.txt", 1, 1), ("grep", "tools/e.txt", 3, 3)],
        "grep-e-then-n": [("grep", "tools/e.txt", 1, 1), ("grep", "tools/e.txt", 3, 3)]})
    # Option forms as GNU grep 3.12 and ripgrep 15.1.0 parse them. grep's --color and --colour take a value
    # only after '=', so the next word is the pattern; grep takes an unambiguous prefix of a long option
    # (--regex=, --line-n) and refuses an ambiguous one (--line); -X takes a value in grep and -d (--max-depth)
    # in rg; and a command the tool refuses (an unknown option, =VALUE on a flag, a value missing at the end)
    # records nothing. Numbered output reads "1:3:n" and "3:n", unnumbered "3:n" and "n"; the out-of-scope
    # line imitates a misread output line, as above. Each rg -N/-n order with each long line-number option
    # is pinned too, and the ones whose parse did not change are coverage.
    numbered, plain = "1:3:n\n3:n\n", "3:n\nn\n"
    both = [("grep", "tools/e.txt", 1, 1), ("grep", "tools/e.txt", 3, 3)]
    forms = (("grep-color-next-word", "grep -n --color n tools/e.txt", numbered, both),
             ("grep-colour-attached", "grep -n --colour=always n tools/e.txt", numbered, both),
             ("grep-colour-bare", "grep -n --colour n tools/e.txt", numbered, both),
             ("grep-regex-prefix", "grep -n --regex=n tools/e.txt", numbered, both),
             ("grep-line-number-prefix", "grep --line-n n tools/e.txt", numbered, both),
             ("grep-ambiguous-prefix", "grep -n --line n tools/e.txt", numbered, []),
             ("grep-X-value", "grep -n -X grep n tools/e.txt", numbered, both),
             ("grep-unknown-short", "grep -n -p n tools/e.txt", numbered, []),
             ("grep-flag-given-value", "grep --line-number=x n tools/e.txt", numbered, []),
             ("grep-value-missing", "grep -n n tools/e.txt -e", numbered, []),
             ("rg-no-prefix", "rg -n --regex=n tools/e.txt", numbered, []),
             ("rg-d-value", "rg -n -d 1 tools tools/searched", "tools/unsearched/u.txt:1:secret\n", []),
             ("rg-d-value-inside", "rg -n -d 1 seen tools/searched", "tools/searched/s.txt:1:seen\n",
              [("grep", "tools/searched/s.txt", 1, 1)]),
             ("rg-N-then-long-on", "rg -N --line-number n tools/e.txt", numbered, both),
             ("rg-long-on-then-N", "rg --line-number -N n tools/e.txt", plain, []),
             ("rg-n-then-long-off", "rg -n --no-line-number n tools/e.txt", plain, []),
             ("rg-long-off-then-n", "rg --no-line-number -n n tools/e.txt", numbered, both))
    for name, command, stdout, _want in forms:
        repo.bash(command, stdout, "form-" + name)
    check("citeledger/grep-option-forms", {name: repo.spans("form-" + name) for name, _c, _s, _w in forms},
          {name: want for name, _c, _s, want in forms})
    # A long option written without '=' takes the next word as its value: --regexp's pattern, --file's
    # pattern file and --context's count are never read as the pattern or a searched path.
    (repo.root / "tools" / "pats.txt").write_bytes(b"n\n")
    taking = (("regexp", "grep -n --regexp n tools/e.txt", numbered, both),
              ("file", "grep -n --file tools/pats.txt tools/e.txt", numbered, both),
              ("context", "grep -n --context 2 x tools/e.txt", "1-3:n\n2:x\n3-n\n",
               [("grep", "tools/e.txt", 1, 3)]))
    for name, command, stdout, _want in taking:
        repo.bash(command, stdout, "take-" + name)
    check("citeledger/grep-long-value-next-word",
          {name: repo.spans("take-" + name) for name, _c, _s, _w in taking},
          {name: want for name, _c, _s, want in taking})


def _citeledger_dedup_cases(tmp):
    """Deduplication keys on kind, via and rev as well as path, span and digest: rows that differ only in
    those are different facts and are all kept, within one call and against the file, while an exact
    repeat is still skipped."""
    def row(**fields):
        base = {"v": 1, "ts": "t", "kind": "read", "via": "Read", "path": "/x/p.py", "rel": None, "start": 1,
                "end": 1, "sha256": "0" * 64, "rev": None}
        base.update(fields)
        return base
    state = str(tmp / "cite-dedup-state")
    aiqt_hooks._cite_append(state, "dedup-key", [row()])
    aiqt_hooks._cite_append(state, "dedup-key", [row(via="git-show", rev="HEAD"), row(via="git-show", rev="main"),
                                                 row(kind="wrote", via="Edit"), row(),
                                                 row(via="git-show", rev="HEAD")])
    path = Path(state) / "citation-ledger" / (hashlib.sha256(b"dedup-key").hexdigest()[:16] + ".jsonl")
    held = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    check("citeledger/dedup-key-separates-facts", [(r["kind"], r["via"], r["rev"]) for r in held],
          [("read", "Read", None), ("read", "git-show", "HEAD"), ("read", "git-show", "main"),
           ("wrote", "Edit", None)])


def _citeledger_bound_cases(tmp):
    """The per-call bounds, each tried just inside, at and just outside it: 50 distinct files read, 500
    rows appended, 8 path splits tried per output line, 50 replacements of a replace_all Edit, the deadline
    (met by a stand-in clock: a git probe is started, and a read made, only while the deadline has not been
    reached), and the length of a path the recorder resolves, both a path a tool input or command names and
    a path split from an output line. The four count bounds are coverage: the recorder enforced each of
    them before the per-call deadline was added, so their checks pin the bounds and do not show a change to
    them."""
    files = {"many/f{:02d}.txt".format(i): "hit {:02d}\n".format(i).encode("utf-8") for i in range(51)}
    files["rows.txt"] = "".join("r{}\n".format(i) for i in range(1, 1002)).encode("utf-8")
    for k in (7, 8, 9):
        files["split{}/{}".format(k, "s" + ":1:s" * (k - 1))] = b"t\n"
    repo = _CiteRepo(tmp / "cite-bounds", files)

    def count(session):
        return len(repo.rows(session))
    # Coverage (see the docstring): files, rows, splits and Edit replacements.
    for n in (49, 50, 51):
        repo.grep(repo.root / "many", "\n".join("many/f{:02d}.txt:1:hit {:02d}".format(i, i) for i in range(n)),
                  "files-{}".format(n))
    check("citeledger/max-files-boundary", [count("files-{}".format(n)) for n in (49, 50, 51)], [49, 50, 50])
    for n in (499, 500, 501):
        repo.grep(repo.root / "rows.txt", "\n".join("{0}:r{0}".format(2 * k - 1) for k in range(1, n + 1)),
                  "rows-{}".format(n))
    check("citeledger/max-rows-boundary", [count("rows-{}".format(n)) for n in (499, 500, 501)], [499, 500, 500])
    for k in (7, 8, 9):
        repo.grep(repo.root / "split{}".format(k), "split{}/{}:1:t".format(k, "s" + ":1:s" * (k - 1)),
                  "splits-{}".format(k))
    check("citeledger/max-splits-boundary", [count("splits-{}".format(k)) for k in (7, 8, 9)], [1, 1, 0])
    for n in (49, 50, 51):
        path = repo.root / "edit-{}.txt".format(n)
        path.write_bytes(b"NEW\nx\n" * n)
        repo.call("Edit", {"file_path": str(path), "old_string": "OLD", "new_string": "NEW", "replace_all": True},
                  {"filePath": str(path), "originalFile": "OLD\nx\n" * n}, "edits-{}".format(n))
    check("citeledger/edit-max-boundary", [count("edits-{}".format(n)) for n in (49, 50, 51)], [49, 50, 50])
    # The deadline, set from the handler's first clock reading: with the clock just inside, at or just past
    # it before the toplevel probe, is the probe started and is the read made; and with the clock moved
    # there only once the probe has returned, is the read made.
    saved_time, saved_top = aiqt_hooks.time, aiqt_hooks._recovery_toplevel
    probed, read = [], []
    try:
        for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
            for step, session in ((True, "deadline-probe-" + name), (False, "deadline-read-" + name)):
                clock = aiqt_hooks.time = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITE_BUDGET + offset, step)
                calls = []
                aiqt_hooks._recovery_toplevel = lambda *args, **kwargs: calls.append(clock.advance()) or str(
                    repo.root)
                repo.call("Read", {"file_path": str(repo.root / "rows.txt")},
                          {"type": "text", "file": {"filePath": str(repo.root / "rows.txt"), "content": "r1",
                                                    "startLine": 1, "numLines": 1}}, session)
                if step:
                    probed.append((bool(calls), repo.ledger(session).exists()))
                else:
                    read.append(repo.ledger(session).exists())
    finally:
        aiqt_hooks.time, aiqt_hooks._recovery_toplevel = saved_time, saved_top
    check("citeledger/deadline-boundary", (probed, read),
          ([(True, True), (False, False), (False, False)], [True, False, False]))
    # The path bound: each path is written as the path to a file padded with slashes (which resolution
    # drops) to one under, at and one over _CITE_MAX_PATH characters. Over it the path is never resolved
    # (no longer path reaches _cite_real inside _cite_rows) and records nothing. Input paths: Read
    # and Write file_path and filePath, a Grep path, the file of a sed command and a file a grep command
    # names; Linux refuses a path of more than 4095 bytes itself, so that grep file over the bound already
    # fails its stat before any resolution, and its column is coverage (this suite runs on Linux). Split
    # paths: a path split from a Grep content line and from a grep command's output line.
    bound_repo = _CiteRepo(tmp / "cite-path-bound", {"a.txt": b"a1\n", "w.txt": b"w1\n"})
    root, limit = str(bound_repo.root), aiqt_hooks._CITE_MAX_PATH
    shapes = {
        "input": (
            ("Read", lambda p, s: bound_repo.call("Read", {"file_path": p("a.txt")}, {"type": "text", "file": {
                "filePath": p("a.txt"), "content": "a1", "startLine": 1, "numLines": 1}}, s)),
            ("Write", lambda p, s: bound_repo.call("Write", {"file_path": p("w.txt"), "content": "w1\n"},
                                                   {"filePath": p("w.txt")}, s)),
            ("Grep", lambda p, s: bound_repo.grep(p(""), "a.txt:1:a1", s)),
            ("sed", lambda p, s: bound_repo.bash("sed -n 1p " + p("a.txt"), "a1\n", s)),
            ("grep", lambda p, s: bound_repo.bash("grep -n a1 " + p("a.txt"), "1:a1\n", s))),
        "split": (
            ("Grep", lambda p, s: bound_repo.grep(bound_repo.root, p("a.txt") + ":1:a1", s)),
            ("grep", lambda p, s: bound_repo.bash("grep -rn a1 .", p("a.txt") + ":1:a1\n", s)))}
    counts, longest = {}, {}
    for shape, calls in shapes.items():
        for tool, call in calls:
            for size in (limit - 1, limit, limit + 1):
                session = "path-{}-{}-{}".format(shape, tool, size)

                def pad(name, size=size):
                    return root + "/" * (size - len(root) - len(name)) + name
                resolved = _cite_resolutions(lambda call=call, pad=pad, session=session: call(pad, session))
                longest[shape] = max([longest.get(shape, 0)] + [len(path) for path in resolved])
                counts.setdefault(shape, {}).setdefault(tool, []).append(len(bound_repo.rows(session)))
    check("citeledger/input-path-bound", (counts["input"], longest["input"]),
          ({tool: [1, 1, 0] for tool, _call in shapes["input"]}, limit))
    check("citeledger/split-path-bound", (counts["split"], longest["split"]),
          ({tool: [1, 1, 0] for tool, _call in shapes["split"]}, limit))


class _CiteSplitProbe(str):
    """A response text that counts the lines split out of it, by one str.split or by one str.find per line,
    so a vector can tell how far the recorder split it."""

    def split(self, *args, **kwargs):
        pieces = str.split(self, *args, **kwargs)
        self.lines = getattr(self, "lines", 0) + len(pieces)
        return pieces

    def find(self, *args, **kwargs):
        self.lines = getattr(self, "lines", 0) + 1
        return str.find(self, *args, **kwargs)


class _CiteCutProbe(_CiteSplitProbe):
    """A _CiteSplitProbe that moves the stand-in clock set as its clock (a _CiteClock) on once cut lines have
    been split out of it by str.find, so the deadline is reached at a known line."""

    def find(self, *args, **kwargs):
        found = _CiteSplitProbe.find(self, *args, **kwargs)
        if self.lines == self.cut:
            self.clock.advance()
        return found


def _cite_stub_git(stub_dir):
    """Write a git stand-in into stub_dir, for PATH: when one of its arguments is in CITE_STUB_SLOW (one per
    line; "*" slows every call) it appends "start" to the file CITE_STUB_MARK, sleeps 8 seconds, appends
    "done", and only then runs the real git. A "start" mark shows the slowed probe was reached, and "start"
    alone that its caller cut it off. 8 seconds is well past the 5 seconds a probe gets by default, so on a
    runner however loaded a probe given any timeout up to that default leaves "start" alone and only a probe
    given none leaves "done"; which timeout each probe was given, the time left or the default, is told
    apart by _cite_probe_timeouts, not by the marks. 8 seconds stays under the hook timeout of 10."""
    real_git = shutil.which("git")
    stub_dir.mkdir()
    (stub_dir / "git").write_text(
        "#!{} -I\nimport os, sys, time\nslow = set(os.environ.get('CITE_STUB_SLOW', '').split('\\n')) - {{''}}\n"
        "if '*' in slow or slow & set(sys.argv[1:]):\n"
        "    with open(os.environ['CITE_STUB_MARK'], 'a') as handle:\n"
        "        handle.write('start\\n')\n"
        "    time.sleep(8)\n"
        "    with open(os.environ['CITE_STUB_MARK'], 'a') as handle:\n"
        "        handle.write('done\\n')\n"
        "os.execv({!r}, [{!r}] + sys.argv[1:])\n".format(sys.executable, real_git, real_git), encoding="utf-8")
    os.chmod(str(stub_dir / "git"), 0o755)


def _cite_stubbed(stub_dir, slow, mark, run):
    """run() with the git stand-in of _cite_stub_git first on PATH, slowing the probes slow names and
    marking mark: the marks it left ([] where no slowed probe started)."""
    saved_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(stub_dir) + os.pathsep + saved_path
        os.environ["CITE_STUB_SLOW"], os.environ["CITE_STUB_MARK"] = slow, str(mark)
        run()
    finally:
        os.environ["PATH"] = saved_path
        os.environ.pop("CITE_STUB_SLOW", None)
        os.environ.pop("CITE_STUB_MARK", None)
    return mark.read_text(encoding="utf-8").split() if mark.exists() else []


def _cite_probe_timeouts(run, budget):
    """run() with every git probe of the citation hooks (aiqt_hooks._recovery_toplevel and _review_git)
    recorded: whether at least one probe ran and each was given a timeout greater than 0 and at most budget,
    so a probe given the time left is told apart from one given its 5 second default or none."""
    saved = (aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git)
    given = []

    def top(repo_dir, *args, **kwargs):
        given.append(kwargs.get("timeout", args[0] if args else None))
        return saved[0](repo_dir, *args, **kwargs)

    def git(repo_dir, *args, **kwargs):
        given.append(kwargs.get("timeout"))
        return saved[1](repo_dir, *args, **kwargs)
    try:
        aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = top, git
        run()
    finally:
        aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = saved
    return bool(given) and all(isinstance(t, (int, float)) and 0 < t <= budget for t in given)


class _CiteStepClock:
    """Stands in for the time module inside aiqt_hooks: monotonic() returns now, which moves only when
    move() is called (here by a stand-in git probe, as if the probe took that long), so the time left at
    each probe is known exactly. Every other attribute is the real time module's."""

    def __init__(self, now):
        self.now = now

    def monotonic(self):
        return self.now

    def move(self, seconds):
        self.now += seconds

    def __getattr__(self, name):
        return getattr(time, name)


def _cite_resolutions(run, within="_cite_rows"):
    """run() with every call of aiqt_hooks._cite_real (the walk that resolves a path through its symlinks)
    made inside aiqt_hooks.<within> (the recorder's _cite_rows, or the check's _citechk_run) recorded: the
    paths it was given, in order, so a vector can tell which paths the recorder or the check resolved."""
    saved_within, saved_real, active, paths = getattr(aiqt_hooks, within), aiqt_hooks._cite_real, [], []

    def scoped(*args, **kwargs):
        active.append(True)
        try:
            return saved_within(*args, **kwargs)
        finally:
            active.pop()

    def real(path, *args, **kwargs):
        if active:
            paths.append(path)
        return saved_real(path, *args, **kwargs)
    try:
        setattr(aiqt_hooks, within, scoped)
        aiqt_hooks._cite_real = real
        run()
    finally:
        setattr(aiqt_hooks, within, saved_within)
        aiqt_hooks._cite_real = saved_real
    return paths


def _citeledger_deadline_cases(tmp):
    """Where the recorder's deadline is checked. Every git probe is given the time left: a stand-in probe
    records the timeout it was given and then moves a stand-in clock on by half a second, as if it took
    that long, so each later probe must be given half a second less. Each tried with a stand-in clock just
    inside, at and just past the deadline: _cite_span, _cite_within and _cite_resolve stop at it, and a
    response's split into lines, the words of a grep or rg command, the merge of grep hits into spans and
    the digest of each row are not carried on once it is reached (the clock is moved there just before each
    step, by a stand-in around the step before it); and once the toplevel probe has moved it there, no path
    is resolved, whatever the tool."""
    repo = _CiteRepo(tmp / "cite-deadline", {"a.txt": b"a1\na2\na3\na4\na5\na6\n"})
    other = _CiteRepo(tmp / "cite-deadline-other", {"o.txt": b"o1\n"})
    shown = "a1\na2\na3\na4\na5\na6\n"
    # Each git probe is given what is left of the deadline when it starts, never the whole budget or its
    # own default: the handler's toplevel probe, the toplevel probe of a git -C repository, and the ls-tree
    # and cat-file probes of git show. Halves add and subtract exactly in binary, so the timeouts are exact.
    saved = (aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git, aiqt_hooks._CITE_BUDGET)
    tops, gits = [], []

    def top(repo_dir, *args, **kwargs):
        tops.append(kwargs.get("timeout", args[0] if args else "none given"))
        result = saved[1](repo_dir, *args, **kwargs)
        aiqt_hooks.time.move(0.5)
        return result

    def git(repo_dir, *args, **kwargs):
        gits.append((args[0], kwargs.get("timeout", "none given")))
        result = saved[2](repo_dir, *args, **kwargs)
        aiqt_hooks.time.move(0.5)
        return result
    given = []
    try:
        aiqt_hooks._CITE_BUDGET = 2.5
        aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = top, git
        for session, command, stdout in (("left-own", "git show HEAD:a.txt", shown),
                                         ("left-other", "git -C {} show HEAD:o.txt".format(other.root), "o1\n")):
            aiqt_hooks.time = _CiteStepClock(1000.0)
            del tops[:], gits[:]
            repo.bash(command, stdout, session)
            given.append((list(tops), list(gits), repo.spans(session)))
    finally:
        (aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git,
         aiqt_hooks._CITE_BUDGET) = saved
    check("citeledger/probe-given-time-left", given,
          [([2.5], [("ls-tree", 2.0), ("cat-file", 1.5)], [("git-show", "a.txt", 1, 6)]),
           ([2.5, 2.0], [("ls-tree", 1.5), ("cat-file", 1.0)], [("git-show", None, 1, 1)])])
    # _cite_span and _cite_within compare and walk lines only while the deadline has not been reached.
    entry, inner = aiqt_hooks._cite_entry(shown.encode("utf-8"), "/x"), []
    try:
        aiqt_hooks.time = _CiteClock(1000.0, 1000.0, False)
        for offset in (0.001, 0.0, -0.001):
            inner.append((aiqt_hooks._cite_span(entry, 2, ["a2", "a3"], 1000.0 + offset),
                          aiqt_hooks._cite_within(shown, 3, 9, 1000.0 + offset)))
    finally:
        aiqt_hooks.time = saved[0]
    check("citeledger/span-and-within-deadline", inner, [(3, (2, 3)), (None, None), (None, None)])
    # _cite_resolve resolves a path only while the deadline has not been reached, and only a path of at
    # most _CITE_MAX_PATH characters: tried with the deadline just ahead of, at and just behind the clock,
    # and with a path to a.txt padded with slashes (which resolution drops) to one under, at and one over
    # the bound.
    target, limit = os.path.realpath(str(repo.root / "a.txt")), aiqt_hooks._CITE_MAX_PATH
    padded = [str(repo.root) + "/" * (size - len(str(repo.root)) - len("a.txt")) + "a.txt"
              for size in (limit - 1, limit, limit + 1)]
    try:
        aiqt_hooks.time = _CiteClock(1000.0, 1000.0, False)
        timed = [aiqt_hooks._cite_resolve(target, 1000.0 + offset) for offset in (0.001, 0.0, -0.001)]
        sized = [aiqt_hooks._cite_resolve(path, 1000.001) for path in padded]
    finally:
        aiqt_hooks.time = saved[0]
    check("citeledger/resolve-bound-and-deadline", (timed, [len(path) for path in padded], sized),
          ([target, None, None], [limit - 1, limit, limit + 1], [target, target, None]))
    # Once a stand-in toplevel probe has moved the clock to just inside, at or just past the deadline, no
    # path is resolved, the session root included, whatever the tool: Read, Write and Grep stop at their
    # first resolution, and grep, sed and git show before it. Just inside, every tool resolves its paths
    # and records its row. git show's ls-tree and cat-file probes are each given the one millisecond left
    # (recorded, to the microsecond) by a stand-in that then runs the real probe with git's own default
    # timeout, so what is recorded never depends on how fast a real git process starts.
    (repo.root / "w.txt").write_bytes(b"w1\n")
    a_txt, w_txt = str(repo.root / "a.txt"), str(repo.root / "w.txt")
    tools = (("Read", [], lambda s: repo.call("Read", {"file_path": a_txt}, {"type": "text", "file": {
                 "filePath": a_txt, "content": "a1", "startLine": 1, "numLines": 1}}, s)),
             ("Write", [], lambda s: repo.call("Write", {"file_path": w_txt, "content": "w1\n"},
                                               {"filePath": w_txt}, s)),
             ("Grep", [], lambda s: repo.grep(repo.root, "a.txt:1:a1", s)),
             ("grep", [], lambda s: repo.bash("grep -n a1 a.txt", "1:a1\n", s)),
             ("sed", [], lambda s: repo.bash("sed -n 1p a.txt", "a1\n", s)),
             ("git-show", [("ls-tree", 0.001), ("cat-file", 0.001)],
              lambda s: repo.bash("git show HEAD:a.txt", shown, s)))
    resolved, probes = {}, []

    def probe(repo_dir, *args, **kwargs):
        probes.append((args[0], round(kwargs.get("timeout", -1.0), 6)))
        return saved[2](repo_dir, *args)
    try:
        aiqt_hooks._review_git = probe
        for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
            for tool, _probes, call in tools:
                clock = aiqt_hooks.time = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITE_BUDGET + offset, False)
                aiqt_hooks._recovery_toplevel = lambda *args, clock=clock, **kwargs: clock.advance() or str(
                    repo.root)
                session = "resolve-{}-{}".format(tool, name)
                del probes[:]
                paths = _cite_resolutions(lambda call=call, session=session: call(session))
                resolved.setdefault(tool, []).append((bool(paths), len(repo.rows(session)), list(probes)))
    finally:
        aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = saved[0], saved[1], saved[2]
    check("citeledger/no-resolution-past-deadline", resolved,
          {tool: [(True, 1, given), (False, 0, []), (False, 0, [])] for tool, given, _call in tools})

    def stepped(step_name, after, session_of, call, seen=None):
        """call(session) once per clock position, with the clock moved to it by a stand-in around
        aiqt_hooks.<step_name> (after the step's own work when after, else before it). With seen, each
        position's result is paired with seen(value) for each value the step returned there, so a vector
        can tell how far the step itself went, not only what the call recorded."""
        saved_step = getattr(aiqt_hooks, step_name, None)
        out = []
        try:
            for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
                clock = aiqt_hooks.time = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITE_BUDGET + offset, False)
                returned = []

                def stand_in(*args, clock=clock, returned=returned, **kwargs):
                    if not after:
                        clock.advance()
                    result = saved_step(*args, **kwargs)
                    clock.advance()
                    returned.append(result)
                    return result
                setattr(aiqt_hooks, step_name, stand_in)
                result = call(session_of(name))
                out.append(result if seen is None else (result, [seen(value) for value in returned]))
        finally:
            aiqt_hooks.time = saved[0]
            if saved_step is None:
                delattr(aiqt_hooks, step_name)
            else:
                setattr(aiqt_hooks, step_name, saved_step)
        return out

    def read_split(session):
        content = _CiteSplitProbe(shown)
        repo.call("Read", {"file_path": str(repo.root / "a.txt")},
                  {"type": "text", "file": {"filePath": str(repo.root / "a.txt"), "content": content,
                                            "startLine": 1, "numLines": 6}}, session)
        return getattr(content, "lines", 0), len(repo.rows(session))

    def grep_split(session):
        content = _CiteSplitProbe("1:a1\n2:a2\n3:a3")
        repo.grep(repo.root / "a.txt", content, session)
        return getattr(content, "lines", 0), len(repo.rows(session))

    # The split of a Read content (after the file read) and of a Grep content (after the searched path is
    # resolved): at the deadline not one line is split off, and nothing is recorded.
    check("citeledger/split-stops-at-deadline",
          (stepped("_cite_read", True, "split-read-{}".format, read_split),
           stepped("_cite_real", True, "split-grep-{}".format, grep_split)),
          ([(7, 1), (0, 0), (0, 0)], [(3, 1), (0, 0), (0, 0)]))

    def grep_rows(session):
        repo.grep(repo.root / "a.txt", "1:a1\n3:a3\n5:a5", session)
        return [(r["start"], r["end"]) for r in repo.rows(session)]
    # Three hits that merge into three spans: at the deadline _cite_merge itself returns no span (its own
    # return is checked, since the digest step would also leave the ledger empty), and once the first row
    # is digested at it no further row is.
    check("citeledger/merge-and-digest-stop-at-deadline",
          (stepped("_cite_merge", False, "merge-{}".format, grep_rows,
                   lambda spans: [(span[3], span[4]) for span in spans]),
           stepped("_cite_sha256", True, "digest-{}".format, grep_rows)),
          ([([(1, 1), (3, 3), (5, 5)], [[(1, 1), (3, 3), (5, 5)]]), ([], [[]]), ([], [[]])],
           [[(1, 1), (3, 3), (5, 5)], [(1, 1)], [(1, 1)]]))

    def grep_words(session):
        repo.bash("grep --line-number --color --regexp a1 a.txt", "1:a1\n", session)
        return [(r["start"], r["end"]) for r in repo.rows(session)]
    # A grep command's words: once the first long option has been read at the deadline, no further word is
    # (the options _cite_long_option returned are checked, since the output scan would also record nothing).
    check("citeledger/command-words-stop-at-deadline",
          stepped("_cite_long_option", True, "words-{}".format, grep_words, lambda option: option[0]),
          [([(1, 1)], ["line-number", "color", "regexp"]), ([], ["line-number"]), ([], ["line-number"])])


def _citeledger_timing_cases(tmp):
    """One deadline bounds the probes, reads, scans and digests of a call. With the budget cut, each worst
    shape is shown reached and cut short by evidence a loaded runner cannot change, never by a wall clock: a
    git probe slowed by the stand-in of _cite_stub_git leaves a "start" mark and no "done" mark (it was
    reached and cut off), and every probe was given a timeout of at most the budget, never its 5 second
    default (_cite_probe_timeouts); a large file read in slowed chunks and many files read slowly make at
    least one slowed read and fewer than reading them whole takes; and a huge grep output takes at most
    the budget plus one second of this process's CPU time (CPU time, not elapsed time, so a runner busy
    with other work cannot stretch it), and with the deadline reached at a known line by a stand-in clock
    is split up to that line, scanned into hits up to the line before it, and records nothing. Each of the
    toplevel, ls-tree and cat-file probes, slowed alone, is reached and cut off, and nothing is recorded.
    The probe and read vectors get a 2.5 second budget, so the unslowed probes before the slowed step finish
    well inside it on a busy runner, and a slowed read takes half a second, so a whole read of big.txt (9
    reads) takes 4.5 seconds and of the many files (100 reads) 50: the budget cuts both short."""
    budget, slack, cut_budget = 0.5, 1.0, 2.5
    big = b"x\n" * (4 * 1024 * 1024)
    files = {"tools/a.py": b"alpha = 1\n", "big.txt": big,
             "huge.txt": "".join("h{}\n".format(i) for i in range(1, 400001)).encode("utf-8")}
    files.update({"many/f{:02d}.txt".format(i): "hit {:02d}\n".format(i).encode("utf-8") for i in range(51)})
    repo = _CiteRepo(tmp / "cite-timing", files)
    stub_dir = tmp / "cite-stub-bin"
    _cite_stub_git(stub_dir)
    real_read, reads = os.read, {"count": 0, "targets": set()}

    def slow_read(fd, size):  # a read of a target file is slowed and counted; any other (a probe's pipe) is not
        try:
            st = os.fstat(fd)
            target = (st.st_dev, st.st_ino) in reads["targets"]
        except OSError:
            target = False
        if target:
            reads["count"] += 1
            time.sleep(0.5)
        return real_read(fd, size)

    def slowed(paths, run):
        reads["count"] = 0
        reads["targets"] = {(os.stat(str(p)).st_dev, os.stat(str(p)).st_ino) for p in paths}
        try:
            os.read = slow_read
            run()
        finally:
            os.read = real_read
        return reads["count"]
    many = sorted((repo.root / "many").iterdir())
    many_out = "\n".join("many/f{:02d}.txt:1:hit {:02d}".format(i, i) for i in range(51))
    huge_out = "\n".join("{0}:h{0}".format(i) for i in range(1, 400001))
    saved_budget = aiqt_hooks._CITE_BUDGET
    try:
        aiqt_hooks._CITE_BUDGET = cut_budget
        timed = []
        marks = _cite_stubbed(stub_dir, "*", tmp / "cite-mark-all", lambda: timed.append(_cite_probe_timeouts(
            lambda: repo.bash("git show HEAD:tools/a.py", "alpha = 1\n", "slow-git"), cut_budget)))
        # A whole read of big.txt takes 9 reads (8 chunks of 1 MiB and the empty read at its end); of the
        # first 50 many/ files (the per-call file bound), 2 each. At least one read shows the read was reached.
        big_reads = slowed([repo.root / "big.txt"], lambda: repo.call(
            "Read", {"file_path": str(repo.root / "big.txt")},
            {"type": "text", "file": {"filePath": str(repo.root / "big.txt"), "content": "x", "startLine": 1,
                                      "numLines": 1}}, "slow-read"))
        many_reads = slowed(many, lambda: repo.grep(repo.root / "many", many_out, "many-files"))
        aiqt_hooks._CITE_BUDGET = budget
        began = time.process_time()
        repo.grep(repo.root / "huge.txt", huge_out, "huge-grep")
        cpu = time.process_time() - began
        aiqt_hooks._CITE_BUDGET = cut_budget
        # The same output with the deadline reached at a known line: the 1,000th line split off moves a
        # stand-in clock to it (_CiteCutProbe; the toplevel probe is a stand-in too), so the scan is shown
        # reached and cut short whatever the runner: 1,000 lines split off, the 999 before the cut scanned
        # into hits (the hits _cite_merge is given), and no row, since no span is merged past the deadline.
        saved_scan = (aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._cite_merge)
        merged, cut_out = [], _CiteCutProbe(huge_out)
        try:
            cut_out.clock, cut_out.cut = _CiteClock(1000.0, 1000.0 + cut_budget + 0.001, False), 1000
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel = cut_out.clock, lambda *args, **kwargs: str(repo.root)
            aiqt_hooks._cite_merge = lambda hits, *args: merged.append(
                sum(len(numbers) for numbers in hits.values())) or saved_scan[2](hits, *args)
            repo.grep(repo.root / "huge.txt", cut_out, "huge-grep-cut")
        finally:
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._cite_merge = saved_scan
        scanned = (getattr(cut_out, "lines", 0), merged, len(repo.rows("huge-grep-cut")))
        cut = []
        for name, word in (("toplevel", "--show-toplevel"), ("ls-tree", "ls-tree"), ("cat-file", "cat-file")):
            within = []
            left = _cite_stubbed(stub_dir, word, tmp / "cite-mark-{}".format(name),
                                 lambda name=name, within=within: within.append(_cite_probe_timeouts(
                                     lambda: repo.bash("git show HEAD:tools/a.py", "alpha = 1\n", "cut-" + name),
                                     cut_budget)))
            cut.append((name, left, within, repo.spans("cut-" + name)))
    finally:
        aiqt_hooks._CITE_BUDGET = saved_budget
    check("citeledger/deadline-bounds-worst-shapes",
          [("slow-git-probe", marks, timed, repo.rows("slow-git")), ("slow-file-read", 0 < big_reads < 9),
           ("many-files", 0 < many_reads < 2 * 50), ("huge-grep-output", cpu <= budget + slack, scanned)],
          [("slow-git-probe", ["start"], [True], []), ("slow-file-read", True), ("many-files", True),
           ("huge-grep-output", True, (1000, [999], 0))])
    check("citeledger/slow-probe-cut-off", cut,
          [("toplevel", ["start"], [True], []), ("ls-tree", ["start"], [True], []),
           ("cat-file", ["start"], [True], [])])


def _citecheck_cases(tmp):
    """The citation read check (aiqt_hooks.citation_read_check) over ledgers the recorder writes: the warned
    and silent verdicts, the class of each citation in its guard-events row, the message and body sources,
    the five exclusions with their paired positives, the fail-closed notes (an unreadable ledger, a missing
    session id, an unreadable body), and each bound tried just inside, at and just outside it."""
    root = tmp / "citecheck"
    tools_dir = root / "tools"
    tools_dir.mkdir(parents=True)
    a_py, d_py = tools_dir / "a.py", tools_dir / "d.py"
    a_py.write_text("".join("line{} = {}\n".format(n, n) for n in range(1, 13)), encoding="utf-8")
    d_py.write_text("".join("d{}\n".format(n) for n in range(1, 7)), encoding="utf-8")
    (root / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(root)] + args, check=True, capture_output=True, timeout=30)
    state = Path(aiqt_hooks._orch_state_dir_for_root(str(root)))
    events = state / "guard-events.jsonl"
    ledger_dir = state / "citation-ledger"
    verdicts = []

    def ledger(session):
        return ledger_dir / (hashlib.sha256(session.encode("utf-8")).hexdigest()[:16] + ".jsonl")

    def read(session, start, end, path=a_py):
        shown = path.read_text(encoding="utf-8").split("\n")[start - 1:end]
        aiqt_hooks.citation_read_recorder({
            "hook_event_name": "PostToolUse", "cwd": str(root), "session_id": session, "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "text", "file": {"filePath": str(path), "content": "\n".join(shown),
                                                        "startLine": start, "numLines": end - start + 1}}})

    def event_rows():
        if not events.is_file():
            return []
        return [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines() if x.strip()]

    def run(command, session="cc", cwd=None, event="PreToolUse", tool="Bash"):
        data = {"hook_event_name": event, "cwd": str(cwd or root), "tool_name": tool,
                "tool_input": {"command": command}}
        if session is not None:
            data["session_id"] = session
        before = len(event_rows())
        try:
            result = aiqt_hooks.citation_read_check(data)
        except Exception as exc:  # a handler crash fails the check that made it rather than ending the run
            result = (2, None, "crash: {}".format(type(exc).__name__))
        verdicts.append(_verdict(result))
        note = result[1].get("systemMessage", "") if isinstance(result[1], dict) else ""
        return _verdict(result), note, [r for r in event_rows()[before:] if r.get("kind") == "citint"]

    def judged(command, session="cc", **kw):
        verdict, _note, new = run(command, session, **kw)
        return (verdict, [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]],
                [r["decision"] for r in new])

    def classes(command, session="cc", **kw):
        return judged(command, session, **kw)[1]

    # Unread and read: a commit message citing a range no row covers warns (a note, never a decision) and its
    # row says warn; after reading lines 1-10 the same citation is silent and its row says allow.
    verdict, note, new = run("git commit -m 'see tools/a.py:3'", "unread")
    check("citeledger/commit-m-unread-warns",
          (verdict, "tools/a.py:3 (no read or write recorded this session covers these lines)" in note,
           [(r["decision"], r["detail"]["cmd"], r["detail"]["citations"]) for r in new]),
          ("warn", True, [("warn", "git-commit", [{"cite": "tools/a.py:3", "class": "unread", "form": "A"}])]))
    read("read", 1, 10)
    check("citeledger/commit-m-read-silent", judged("git commit -m 'see tools/a.py:3-5'", "read"),
          ("allow", [("tools/a.py:3-5", "covered")], ["allow"]))
    # A row covers a range only to its own ends: read 4-6, then 4-6 and 5 are covered, 3-6 and 4-7 are not.
    read("edge", 4, 6)
    check("citeledger/coverage-boundary",
          classes("git commit -m 'tools/a.py:4-6 tools/a.py:5 tools/a.py:3-6 tools/a.py:4-7'", "edge"),
          [("tools/a.py:4-6", "covered"), ("tools/a.py:5", "covered"), ("tools/a.py:3-6", "unread"),
           ("tools/a.py:4-7", "unread")])
    # Two adjacent reads cover a range across them; a one-line gap between them does not.
    read("union", 1, 5)
    read("union", 6, 10)
    read("gap", 1, 5)
    read("gap", 7, 10)
    check("citeledger/union-covers",
          (classes("git commit -m 'tools/a.py:4-7'", "union"), classes("git commit -m 'tools/a.py:4-7'", "gap")),
          ([("tools/a.py:4-7", "covered")], [("tools/a.py:4-7", "unread")]))
    # A read whose lines changed afterwards is stale (an untracked file, so no diff credits the change).
    u_py = tools_dir / "u.py"
    u_py.write_text("u1\nu2\nu3\nu4\nu5\n", encoding="utf-8")
    read("stale", 1, 5, path=u_py)
    u_py.write_text("u1\nu2\nU3\nu4\nu5\n", encoding="utf-8")
    verdict, note, new = run("git commit -m 'tools/u.py:3'", "stale")
    check("citeledger/stale-warns",
          (verdict, [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]],
           "those lines have changed since" in note),
          ("warn", [("tools/u.py:3", "stale")], True))
    # A row ending at the file's last line can be current; one ending a line past it never is, even when
    # its digest equals that of the lines the file does hold.
    read("whole", 1, 12)
    whole = classes("git commit -m 'tools/a.py:12'", "whole")
    pieces = a_py.read_bytes()
    ledger("past").write_bytes((json.dumps({"v": 1, "ts": "t", "kind": "read", "via": "Read",
                                            "path": os.path.realpath(str(a_py)), "rel": "tools/a.py", "start": 1,
                                            "end": 13, "sha256": hashlib.sha256(pieces).hexdigest(),
                                            "rev": None}) + "\n").encode("utf-8"))
    check("citeledger/row-end-bound", (whole, classes("git commit -m 'tools/a.py:12'", "past")),
          ([("tools/a.py:12", "covered")], [("tools/a.py:12", "stale")]))

    # Message and body sources: -F and --file= files, a relative path under git -C, the option spellings,
    # gh pr create --body-file and gh pr edit --body, and one row per command of a compound command.
    msg = tmp / "cite-msg.txt"
    msg.write_text("see tools/a.py:7\n", encoding="utf-8")
    (root / "rel-msg.txt").write_text("see tools/a.py:8\n", encoding="utf-8")
    other = tmp / "citecheck-other"
    (other / "lib").mkdir(parents=True)
    (other / "lib" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True, timeout=30)
    check("citeledger/commit-F-file",
          (classes("git commit -F {}".format(msg), "files"), classes("git commit --file={}".format(msg), "files"),
           classes("git -C .. commit -qF rel-msg.txt", "files", cwd=tools_dir),
           classes("git -C {} commit -m 'lib/x.py:1'".format(other), "files")),
          ([("tools/a.py:7", "unread")], [("tools/a.py:7", "unread")], [("tools/a.py:8", "unread")],
           [("lib/x.py:1", "unread")]))
    check("citeledger/option-forms",
          [classes(command, "forms") for command in (
              "git commit -am 'tools/a.py:1'", "git commit -m'tools/a.py:2'", "git commit --message='tools/a.py:3'",
              "git commit --message 'tools/a.py:4'", "git commit --author '-mtools/a.py:5' -m x",
              "git commit -m x -- '-mtools/a.py:6'", "git commit -Smtools/a.py:7 -m x")],
          [[("tools/a.py:1", "unread")], [("tools/a.py:2", "unread")], [("tools/a.py:3", "unread")],
           [("tools/a.py:4", "unread")], [], [], []])
    body = tmp / "cite-body.md"
    body.write_text("Touches tools/a.py:9.\n", encoding="utf-8")
    check("citeledger/gh-body-file-and-inline",
          [judged(command, "gh") for command in (
              "gh pr create --title t --body-file {}".format(body), "gh pr create -F={}".format(body),
              "gh pr edit 12 --body 'see tools/a.py:10'", "gh pr create -db 'see tools/a.py:11'",
              "gh pr view 12 --body 'tools/a.py:1'")],
          [("warn", [("tools/a.py:9", "unread")], ["warn"]), ("warn", [("tools/a.py:9", "unread")], ["warn"]),
           ("warn", [("tools/a.py:10", "unread")], ["warn"]), ("warn", [("tools/a.py:11", "unread")], ["warn"]),
           ("allow", [], [])])
    verdict, note, new = run("git add tools && git commit -m 'tools/a.py:3' && gh pr create --body 'tools/a.py:4'",
                             "multi")
    check("citeledger/multi-command",
          (verdict, [r["detail"]["cmd"] for r in new], "this gh pr create body and git commit message" in note),
          ("warn", ["git-commit", "gh-pr-create"], True))
    check("citeledger/lines-of-form",
          [[(c["cite"], c["class"], c["form"]) for r in run(command, "form-b")[2] for c in r["detail"]["citations"]]
           for command in ("git commit -m 'per lines 2-4 of tools/a.py.'", "git commit -m 'line 5 in `tools/a.py`'",
                           "git commit -m 'Lines 6 - 7 of tools/a.py'", "git commit -m 'line 3 of tools/a.py:5'")],
          [[("tools/a.py:2-4", "unread", "B")], [("tools/a.py:5", "unread", "B")], [("tools/a.py:6-7", "unread", "B")],
           [("tools/a.py:5", "unread", "A")]])
    # A body that cannot be read is named, never skipped: a directory, a missing file, a FIFO (held open here
    # so a read could not block the suite), a path under /dev, and standard input with no here-document.
    fifo = tmp / "cite-fifo"
    os.mkfifo(str(fifo))
    holder = os.open(str(fifo), os.O_RDWR | os.O_NONBLOCK)
    try:
        unreadable = [run(command, "bodies")[:2] for command in (
            "git commit -F {}".format(tmp), "git commit -F {}".format(tmp / "absent.txt"),
            "gh pr create --body-file {}".format(fifo), "git commit -F /dev/null",
            "printf x | git commit -F -")]
    finally:
        os.close(holder)
    check("citeledger/unreadable-body-noted",
          [(verdict, phrase in note) for (verdict, note), phrase in zip(unreadable, (
              "was not read: it is not a regular file", "was not read: it cannot be opened (FileNotFoundError",
              "was not read: it is not a regular file", "was not read: it is under /dev or /proc",
              "was not read: it is read from standard input, which this check cannot see"))],
          [("warn", True)] * 5)
    # A command the lexer refuses (an unquoted here-document) is scanned whole; a quoted here-document on
    # -F - is read as the message.
    verdict, _note, new = run("git commit -F - <<EOF\nsee tools/a.py:3\nEOF", "heredoc")
    raw = (verdict, [(r["detail"]["parse"], r["detail"]["citations"][0]["class"]) for r in new])
    verdict, _note, new = run("git commit -F - <<'EOF'\nsee tools/a.py:4\nEOF\necho tools/a.py:9\n", "heredoc")
    quoted = (verdict, [(r["detail"]["parse"], r["detail"]["sources"], [c["cite"] for c in r["detail"]["citations"]])
                        for r in new])
    check("citeledger/raw-fallback", raw, ("warn", [("raw", "unread")]))
    check("citeledger/heredoc-body-read", quoted, ("warn", [("lexed", ["here-document"], ["tools/a.py:4"])]))

    # Exclusion 1, quoted logs and tracebacks: a fenced log, an unfenced log line and a traceback frame are
    # silent; the same citation in plain prose warns.
    fenced = "git commit -m 'Fails:\n```\ntools/a.py:3: AssertionError\ntools/a.py:4\n```\nfixed'"
    check("citeledger/excl-fence-log",
          [judged(command, "excl")[:2] for command in (
              fenced, "git commit -m 'tools/a.py:3: AssertionError raised'",
              "git commit -m 'tools/a.py:3:7: error: bad'", "git commit -m 'tools/a.py:3: see the loop'")],
          [("allow", []), ("allow", []), ("allow", []), ("warn", [("tools/a.py:3", "unread")])])
    # FORM_B needs only whitespace between the line number and "of" or "in", so a frame's ", in NAME" never
    # reads as a citation of NAME, even where NAME is a file.
    check("citeledger/excl-traceback",
          [judged("git commit -m 'File \"tools/a.py\", line 3, in {}'".format(name), "excl")[:2]
           for name in ("f", "Makefile")], [("allow", [])] * 2)
    # Fences open with three or more backticks or tildes indented at most three spaces; a closing run shorter
    # than the opening one does not close it, and an unclosed fence runs to the end.
    check("citeledger/fence-bounds",
          [classes("git commit -m 'x\n{}\ntools/a.py:3\n{}'".format(fence, fence.strip()), "excl")
           for fence in ("```", "  ```", "   ```", "    ```", "``", "````", "~~~")]
          + [classes("git commit -m '````\n```\ntools/a.py:3\n````'", "excl"),
             classes("git commit -m '```\n~~~\ntools/a.py:3\n```'", "excl"),
             classes("git commit -m '```\ntools/a.py:3'", "excl"),
             classes("git commit -m '```inline``` tools/a.py:3'", "excl")],
          [[], [], [], [("tools/a.py:3", "unread")], [("tools/a.py:3", "unread")], [], [], [], [], [],
           [("tools/a.py:3", "unread")]])
    # Block quotes are indented at most three spaces; an indented block needs four spaces or a tab after a
    # blank line (or at the start).
    check("citeledger/quote-indent-bounds",
          [classes("git commit -m {}".format(_single_quoted(text)), "excl") for text in (
              "x\n> tools/a.py:3", "x\n   > tools/a.py:3", "x\n    > tools/a.py:3", "x\n\n    tools/a.py:3",
              "x\n\n   tools/a.py:3", "x\n\n\ttools/a.py:3", "x\n    tools/a.py:3")],
          [[], [], [("tools/a.py:3", "unread")], [], [("tools/a.py:3", "unread")], [],
           [("tools/a.py:3", "unread")]])
    # Exclusion 2, URLs and #L anchors.
    verdict, _note, new = run("git commit -m 'https://github.com/o/r/blob/main/tools/a.py#L3-L5 "
                              "https://h.example/tools/a.py:3 <https://h.example/tools/a.py:4> tools/a.py#L3 "
                              "https://h.example/view?f=tools/a.py:5'", "excl")
    check("citeledger/excl-url-anchor", (verdict, [r["detail"]["citations"] for r in new],
                                         [r["detail"]["masked"]["url"] for r in new]), ("allow", [[]], [4]))
    # Exclusion 3, times and versions: none names a file or is path-shaped, so each is dropped and counted;
    # a cited file with no slash and no extension still counts where it exists.
    verdict, _note, new = run("git commit -m 'at 12:30 and 12:30:45, v1:2 python3.14:2 1.2.3:4 node:18 "
                              "localhost:8080 example.com:8080'", "excl")
    check("citeledger/excl-time-version",
          ((verdict, [(r["detail"]["citations"], r["detail"]["dropped_not_citation"]) for r in new]),
           classes("git commit -m 'tools/a.py:3 Makefile:1'", "excl")),
          (("allow", [([], 8)]), [("tools/a.py:3", "unread"), ("Makefile:1", "unread")]))
    # A path-shaped citation (a slash and a lettered extension of one to ten characters) that names no file
    # is nofile; an eleven-character extension or none is not path-shaped and is dropped.
    check("citeledger/ext-length-bound",
          classes("git commit -m 'x/a.abcdefghi:3 x/a.abcdefghij:3 x/a.abcdefghijk:3 x/abc:3'", "excl"),
          [("x/a.abcdefghi:3", "nofile"), ("x/a.abcdefghij:3", "nofile")])
    # A line number has one to nine digits and no leading zero; a reversed range is dropped and counted.
    verdict, _note, new = run("git commit -m 'tools/a.py:12345678 tools/a.py:123456789 tools/a.py:1234567890 "
                              "tools/a.py:0 tools/a.py:5-3'", "excl")
    check("citeledger/line-number-bound",
          ([(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]],
           [r["detail"]["masked"]["reversed"] for r in new]),
          ([("tools/a.py:12345678", "unread"), ("tools/a.py:123456789", "unread")], [1]))
    # Exclusion 4, verifier-supplied ranges: exempt and counted under a verifier or reviewer heading (to the
    # next heading of the same or a higher level) or on a Verifier-cited: line; the same citation unlabelled
    # warns.
    check("citeledger/excl-verifier-label",
          [judged("git commit -m {}".format(_single_quoted(text)), "excl")[:2] for text in (
              "x\n\n## Verifier report\ntools/a.py:3", "x\n\ntools/a.py:3", "Verifier-cited: tools/a.py:3")],
          [("allow", [("tools/a.py:3", "exempt-verifier")]), ("warn", [("tools/a.py:3", "unread")]),
           ("allow", [("tools/a.py:3", "exempt-verifier")])])
    check("citeledger/verifier-section-bounds",
          [classes("git commit -m {}".format(_single_quoted(text)), "excl") for text in (
              "###### Reviewer findings\ntools/a.py:3", "####### Verifier report\ntools/a.py:3",
              "## Verifier report\n### Detail\ntools/a.py:3\n## Next\ntools/a.py:4",
              "## Verifier report\n# Top\ntools/a.py:5")],
          [[("tools/a.py:3", "exempt-verifier")], [("tools/a.py:3", "unread")],
           [("tools/a.py:3", "exempt-verifier"), ("tools/a.py:4", "unread")], [("tools/a.py:5", "unread")]])
    # Exclusion 5, lines the author wrote: a Write row covers like a read, and lines the diff from HEAD to the
    # working tree adds count as authored at each end of the hunk, but not one line outside it.
    new_py = tools_dir / "new.py"
    new_py.write_text("n1\nn2\n", encoding="utf-8")
    aiqt_hooks.citation_read_recorder({
        "hook_event_name": "PostToolUse", "cwd": str(root), "session_id": "wrote", "tool_name": "Write",
        "tool_input": {"file_path": str(new_py), "content": "n1\nn2\n"},
        "tool_response": {"filePath": str(new_py), "success": True}})
    check("citeledger/excl-authored-write", judged("git commit -m 'adds tools/new.py:2'", "wrote")[:2],
          ("allow", [("tools/new.py:2", "covered")]))
    d_py.write_text("d1\nd2\nd3\nn1\nn2\nn3\nd4\nD5\nd6\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tools/d.py"], check=True, capture_output=True, timeout=30)
    verdict, _note, new = run("git commit -m 'tools/d.py:4 tools/d.py:5 tools/d.py:6 tools/d.py:3 tools/d.py:7 "
                              "tools/d.py:8'", "diff")
    check("citeledger/excl-authored-diff",
          (verdict, [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]],
           [r["detail"]["diff"] for r in new]),
          ("warn", [("tools/d.py:4", "authored-in-diff"), ("tools/d.py:5", "authored-in-diff"),
                    ("tools/d.py:6", "authored-in-diff"), ("tools/d.py:3", "unread"), ("tools/d.py:7", "unread"),
                    ("tools/d.py:8", "authored-in-diff")], ["used"]))
    # A pull request's diff starts at the merge base of its --base and HEAD, not at the base itself (here a
    # branch that already holds the change); with no resolvable base the exemption is skipped and the row
    # says so.
    pr_base = run("gh pr create --base main --body 'tools/d.py:5'", "diff")[2]
    pr_none = run("gh pr create --body 'tools/d.py:5'", "diff")[2]
    for args in (["-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "ahead"], ["branch", "ahead"], ["reset", "-q", "--soft", "HEAD~1"]):
        subprocess.run(["git", "-C", str(root)] + args, check=True, capture_output=True, timeout=30)
    pr_ahead = run("gh pr create --base ahead --body 'tools/d.py:5'", "diff")[2]
    check("citeledger/gh-diff-base",
          [[(c["class"], r["detail"]["diff"]) for r in rows for c in r["detail"]["citations"]]
           for rows in (pr_base, pr_none, pr_ahead)],
          [[("authored-in-diff", "used")], [("unread", "unavailable")], [("authored-in-diff", "used")]])

    # Ledger reading: rows held only in the older generation (.1) cover; a cited path that is a suffix of
    # exactly one recorded path resolves to it; two recorded paths with that suffix do not resolve.
    read("rotated", 1, 4)
    os.replace(str(ledger("rotated")), str(ledger("rotated")) + ".1")
    (tools_dir / "sub").mkdir()
    (root / "other" / "sub").mkdir(parents=True)
    for path in (tools_dir / "sub" / "x.py", root / "other" / "sub" / "x.py"):
        path.write_text("x1\nx2\n", encoding="utf-8")
    read("suffix", 1, 2, path=tools_dir / "sub" / "x.py")
    read("suffix-two", 1, 2, path=tools_dir / "sub" / "x.py")
    read("suffix-two", 1, 2, path=root / "other" / "sub" / "x.py")
    check("citeledger/rotated-generation-and-suffix",
          (classes("git commit -m 'tools/a.py:2'", "rotated"), classes("git commit -m 'sub/x.py:2'", "suffix"),
           classes("git commit -m 'sub/x.py:2'", "suffix-two")),
          ([("tools/a.py:2", "covered")], [("sub/x.py:2", "covered")], [("sub/x.py:2", "nofile")]))
    # Fail closed on a ledger that cannot be read: a directory or a symlink at the session file, a regular
    # file where the ledger directory belongs, and lines that are not rows each name the fault in a note,
    # even where the rows that remain cover every citation.
    read("faulty", 1, 12)
    with open(str(ledger("faulty")), "ab") as handle:
        handle.write(b"not json\n[1]\n")
    faults = [run("git commit -m 'tools/a.py:2'", "faulty")]
    os.mkdir(str(ledger("dir-gen")))
    faults.append(run("git commit -m 'tools/a.py:2'", "dir-gen"))
    os.symlink(str(ledger("faulty")), str(ledger("link-gen")))
    faults.append(run("git commit -m 'tools/a.py:2'", "link-gen"))
    saved_xdg = os.environ.get("XDG_STATE_HOME")
    try:
        os.environ["XDG_STATE_HOME"] = str(tmp / "citecheck-alt-xdg")
        alt = Path(aiqt_hooks._orch_state_dir_for_root(str(root)))
        alt.mkdir(parents=True)
        (alt / "citation-ledger").write_bytes(b"not a directory\n")
        faults.append(run("git commit -m 'tools/a.py:2'", "faulty")[:2] + ([],))
    finally:
        if saved_xdg is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved_xdg
    check("citeledger/unreadable-ledger-noted",
          [(verdict, "could not be read in full" in note and phrase in note,
            [(r["decision"], r["detail"]["ledger"]) for r in new])
           for (verdict, note, new), phrase in zip(faults, (
               "2 line(s) are not ledger rows", "it is not a regular file", "it cannot be opened (OSError",
               "the ledger directory"))],
          [("warn", True, [("warn", "unreadable")])] * 3 + [("warn", True, [])])
    # A row is a ledger row only with 1 <= start <= end: start 1 and end == start are rows, start 0 and an
    # end before the start are not.
    ledger("shape").write_bytes(b"".join((json.dumps({"v": 1, "kind": "read", "path": os.path.realpath(str(a_py)),
                                                      "start": start, "end": end, "sha256": "0" * 64}) + "\n")
                                         .encode("utf-8") for start, end in ((1, 1), (2, 2), (0, 3), (5, 4))))
    check("citeledger/row-shape-bounds",
          "2 line(s) are not ledger rows" in run("git commit -m 'tools/a.py:2'", "shape")[1], True)
    # No rows at all, and no session id: each is named, and its row says which.
    empty = run("git commit -m 'tools/a.py:2'", "never-recorded")
    nosession = run("git commit -m 'tools/a.py:2'", None)
    clean_nosession = run("git commit -m 'at 12:30'", None)
    check("citeledger/ledger-empty-and-no-session",
          ((empty[0], "no citation-ledger rows at all" in empty[1], [r["detail"]["ledger"] for r in empty[2]]),
           (nosession[0], "carries no session id" in nosession[1], [r["detail"]["ledger"] for r in nosession[2]]),
           clean_nosession[:2]),
          (("warn", True, ["empty"]), ("warn", True, ["no-session"]), ("allow", "")))
    # A file the recorder never records (a notebook, a file over its size bound) is named as such.
    (root / "docs").mkdir()
    (root / "docs" / "n.ipynb").write_text("{}\n{}\n{}\n", encoding="utf-8")
    saved_max = aiqt_hooks._CITE_MAX_FILE
    try:
        aiqt_hooks._CITE_MAX_FILE = 4
        big = run("git commit -m 'tools/a.py:1'", "blind")
    finally:
        aiqt_hooks._CITE_MAX_FILE = saved_max
    notebook = run("git commit -m 'docs/n.ipynb:2'", "blind")
    check("citeledger/unrecordable-noted",
          ([(c["class"]) for r in notebook[2] + big[2] for c in r["detail"]["citations"]],
           "records nothing for a notebook, PDF or image read" in notebook[1], "records nothing for a file over 4 bytes"
           in big[1]),
          (["unrecordable", "unrecordable"], True, True))

    # Bounds of the check itself, each just inside, at and just outside.
    read("bounds", 1, 12)
    saved = (aiqt_hooks._CITECHK_MAX_CITES, aiqt_hooks._CITECHK_MAX_FILES, aiqt_hooks._CITECHK_SHOWN,
             aiqt_hooks._CITECHK_BODY_MAX, aiqt_hooks._CITECHK_LEDGER_MAX, aiqt_hooks.time,
             aiqt_hooks._CITECHK_READ_MAX, aiqt_hooks._recovery_toplevel)
    try:
        aiqt_hooks._CITECHK_MAX_CITES = 2
        caps = []
        for k in (1, 2, 3):
            verdict, note, new = run("git commit -m '{}'".format(
                " ".join("tools/a.py:{}".format(n) for n in range(1, k + 1))), "bounds")
            caps.append((verdict, [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]],
                         [(r["decision"], r["detail"]["not_judged_past_cap"]) for r in new],
                         "1 citation(s) past the first 2 were not judged" in note))
        aiqt_hooks._CITECHK_MAX_CITES = saved[0]
        check("citeledger/cite-cap-boundary", caps,
              [("allow", [("tools/a.py:1", "covered")], [("allow", 0)], False),
               ("allow", [("tools/a.py:1", "covered"), ("tools/a.py:2", "covered")], [("allow", 0)], False),
               ("warn", [("tools/a.py:1", "covered"), ("tools/a.py:2", "covered")], [("warn", 1)], True)])
        read("bounds", 1, 2, path=tools_dir / "sub" / "x.py")
        read("bounds", 1, 2, path=root / "other" / "sub" / "x.py")
        aiqt_hooks._CITECHK_MAX_FILES = 2
        names = ("tools/a.py:1", "tools/sub/x.py:1", "other/sub/x.py:1")
        files_cap = [judged("git commit -m '{}'".format(" ".join(names[:k])), "bounds")[:2] for k in (1, 2, 3)]
        aiqt_hooks._CITECHK_MAX_FILES = saved[1]
        check("citeledger/file-cap-boundary", files_cap,
              [("allow", [(names[0], "covered")]), ("allow", [(names[0], "covered"), (names[1], "covered")]),
               ("warn", [(names[0], "covered"), (names[1], "covered"), (names[2], "unjudged")])])
        held_bytes = os.path.getsize(str(a_py)) + os.path.getsize(str(tools_dir / "sub" / "x.py"))
        read_bound = []
        for bound in (held_bytes + 1, held_bytes, held_bytes - 1):
            aiqt_hooks._CITECHK_READ_MAX = bound
            verdict, note, new = run("git commit -m '{}'".format(" ".join(names[:2])), "bounds")
            read_bound.append((verdict, [c["class"] for r in new for c in r["detail"]["citations"]],
                               "the cited files hold more than {} bytes".format(bound) in note))
        aiqt_hooks._CITECHK_READ_MAX = saved[6]
        check("citeledger/read-bytes-boundary", read_bound,
              [("allow", ["covered", "covered"], False), ("allow", ["covered", "covered"], False),
               ("warn", ["covered", "unjudged"], True)])
        aiqt_hooks._CITECHK_SHOWN = 2
        shown = [run("git commit -m '{}'".format(" ".join("tools/d.py:{}".format(n) for n in (1, 2, 3)[:k])),
                     "never-recorded")[1] for k in (1, 2, 3)]
        aiqt_hooks._CITECHK_SHOWN = saved[2]
        check("citeledger/shown-bound",
              [(note.count("\n  tools/d.py:"), "\n  and " in note, "\n  and 1 more" in note) for note in shown],
              [(1, False, False), (2, False, False), (2, True, True)])
        size = len(msg.read_bytes())
        sized = []
        for bound in (size + 1, size, size - 1):
            aiqt_hooks._CITECHK_BODY_MAX = bound
            sized.append(judged("git commit -F {}".format(msg), "bounds")[:2])
        aiqt_hooks._CITECHK_BODY_MAX = saved[3]
        check("citeledger/body-max-boundary", sized,
              [("allow", [("tools/a.py:7", "covered")]), ("allow", [("tools/a.py:7", "covered")]),
               ("warn", [])])
        held = os.path.getsize(str(ledger("bounds")))
        gens = []
        for bound in (held + 1, held, held - 1):
            aiqt_hooks._CITECHK_LEDGER_MAX = bound
            verdict, note, _new = run("git commit -m 'tools/a.py:2'", "bounds")
            gens.append((verdict, "it is larger than {} bytes".format(bound) in note))
        aiqt_hooks._CITECHK_LEDGER_MAX = saved[4]
        check("citeledger/ledger-max-boundary", gens, [("allow", False), ("allow", False), ("warn", True)])
        # The deadline, set from the handler's first clock reading (a stand-in clock): with the clock moved
        # just inside, at or just past it once the toplevel probe has returned, is a range judged (one in a
        # file, and a path-shaped one that names no file, which needs no read); and with the clock there
        # before the probe, is the probe started at all (one not started is named in a note, with no row,
        # since no state directory is known without it).
        budget, probed = {"tools/a.py:2": [], "x/none.py:1": []}, []
        for offset in (-0.001, 0.0, 0.001):
            for step, cited in ((False, "tools/a.py:2"), (False, "x/none.py:1"), (True, "tools/a.py:2")):
                clock = aiqt_hooks.time = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITECHK_BUDGET + offset, step)
                calls = []
                aiqt_hooks._recovery_toplevel = lambda *args, **kwargs: calls.append(clock.advance()) or str(root)
                verdict, note, new = run("git commit -m '{}'".format(cited), "bounds")
                aiqt_hooks.time, aiqt_hooks._recovery_toplevel = saved[5], saved[7]
                if step:
                    probed.append((bool(calls), verdict, "before it resolved the repository" in note, len(new)))
                else:
                    budget[cited].append((verdict, [(c["class"], r["detail"]["budget_exhausted"]) for r in new
                                                    for c in r["detail"]["citations"]], "incomplete (budget)" in note))
        check("citeledger/budget-noted", budget,
              {"tools/a.py:2": [("allow", [("covered", False)], False), ("warn", [("unjudged", True)], True),
                                ("warn", [("unjudged", True)], True)],
               "x/none.py:1": [("warn", [("nofile", False)], False), ("warn", [("unjudged", True)], True),
                               ("warn", [("unjudged", True)], True)]})
        check("citeledger/check-deadline-probe-boundary", probed,
              [(True, "allow", False, 1), (False, "warn", True, 0), (False, "warn", True, 0)])
    finally:
        (aiqt_hooks._CITECHK_MAX_CITES, aiqt_hooks._CITECHK_MAX_FILES, aiqt_hooks._CITECHK_SHOWN,
         aiqt_hooks._CITECHK_BODY_MAX, aiqt_hooks._CITECHK_LEDGER_MAX, aiqt_hooks.time,
         aiqt_hooks._CITECHK_READ_MAX, aiqt_hooks._recovery_toplevel) = saved
    # The deadline is checked before each ledger row is digested against the cited file: with the clock moved
    # just inside, at or just past it once the file is read, the range is covered, or not judged (budget)
    # with no row digested.
    read("digest", 1, 12)
    saved_time, saved_read = aiqt_hooks.time, aiqt_hooks._cite_read
    digested = []
    try:
        for offset in (-0.001, 0.0, 0.001):
            clock = aiqt_hooks.time = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITECHK_BUDGET + offset, False)

            def read_then_advance(*args, clock=clock):
                entry = saved_read(*args)
                clock.advance()
                return entry
            aiqt_hooks._cite_read = read_then_advance
            verdict, note, new = run("git commit -m 'tools/a.py:2'", "digest")
            aiqt_hooks.time, aiqt_hooks._cite_read = saved_time, saved_read
            digested.append((verdict, [(c["class"], r["detail"]["budget_exhausted"]) for r in new
                                       for c in r["detail"]["citations"]], "incomplete (budget)" in note))
    finally:
        aiqt_hooks.time, aiqt_hooks._cite_read = saved_time, saved_read
    check("citeledger/check-digest-deadline", digested,
          [("allow", [("covered", False)], False), ("warn", [("unjudged", True)], True),
           ("warn", [("unjudged", True)], True)])

    # Inert outside its scope (no row): another command, event or tool, a payload with no command, a cwd
    # outside any git repository; a commit with no message source still earns its allow row.
    outside = tmp / "citecheck-not-a-repo"
    outside.mkdir()
    inert = [run("git status", "inert"), run("git commit -m 'tools/a.py:3'", "inert", event="PostToolUse"),
             run("git commit -m 'tools/a.py:3'", "inert", tool="Read"),
             run("git commit -m 'tools/a.py:3'", "inert", cwd=outside)]
    before = len(event_rows())
    no_command = aiqt_hooks.citation_read_check({"hook_event_name": "PreToolUse", "cwd": str(root),
                                                 "tool_name": "Bash", "tool_input": {}, "session_id": "inert"})
    verdicts.append(_verdict(no_command))
    inert.append((_verdict(no_command), "", event_rows()[before:]))
    amend = run("git commit --amend --no-edit", "inert")
    check("citeledger/inert-outside-scope",
          ([(verdict, note, new) for verdict, note, new in inert],
           (amend[0], [(r["decision"], r["detail"]["citations"]) for r in amend[2]])),
          ([("allow", "", [])] * 5, ("allow", [("allow", [])])))
    # A fault inside the check is caught and named; a guard-events row that cannot be written is named too.
    saved_run = aiqt_hooks._citechk_run
    try:
        def broken(data):
            raise RuntimeError("injected")
        aiqt_hooks._citechk_run = broken
        crashed = run("git commit -m 'tools/a.py:3'", "fault")
    finally:
        aiqt_hooks._citechk_run = saved_run
    events.rename(events.with_name("guard-events.saved"))
    events.mkdir()
    try:
        unrecorded = run("git commit -m 'tools/a.py:3'", "fault")
    finally:
        events.rmdir()
        events.with_name("guard-events.saved").rename(events)
    check("citeledger/check-fault-noted",
          ((crashed[0], "could not run (RuntimeError)" in crashed[1]),
           (unrecorded[0], "the guard-events row for this warn (citint) could not be written" in unrecorded[1])),
          (("warn", True), ("warn", True)))

    # The check never decides a permission: every call above allowed, silently or with a note.
    check("citeledger/bake-never-denies", sorted(set(verdicts)), ["allow", "warn"])


def _citecheck_run(root, command, session):
    """One citation_read_check call on a Bash command run at root: (verdict, note, the citint guard-events
    rows it appended)."""
    events = Path(aiqt_hooks._orch_state_dir_for_root(str(root))) / "guard-events.jsonl"

    def rows():
        if not events.is_file():
            return []
        return [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines() if x.strip()]
    before = len(rows())
    result = aiqt_hooks.citation_read_check({"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Bash",
                                             "tool_input": {"command": command}, "session_id": session})
    note = result[1].get("systemMessage", "") if isinstance(result[1], dict) else ""
    return _verdict(result), note, [r for r in rows()[before:] if r.get("kind") == "citint"]


def _hook_functions(*names):
    """The source text of each named top-level function of the hook, by name."""
    import ast
    source = Path(aiqt_hooks.__file__).read_text(encoding="utf-8")
    rows = source.splitlines(keepends=True)
    return {node.name: "".join(rows[node.lineno - 1:node.end_lineno]) for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name in names}


def _hook_variant(tmp, name, text, old, new):
    """The hook's function name, whose source is text, with the one occurrence of old in that text
    replaced by new (None where old does not occur exactly once): written to a module under tmp and
    loaded, its code bound to the hook's own globals, so it calls the hook's other functions and reads its
    constants as the original does."""
    import importlib.util
    import types
    if text.count(old) != 1:
        return None
    path = tmp / "variant{}.py".format(len(list(tmp.glob("variant*.py"))))
    path.write_text(text.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return types.FunctionType(getattr(module, name).__code__, aiqt_hooks.__dict__, name)


def _citecheck_show_cases(tmp):
    """A git show row counts for a citation as any row does, where the working tree's lines still hold the
    bytes it digested, whatever revision it named: a row of HEAD whose lines the working tree has since
    changed is stale, and a row of an older revision whose bytes the working tree holds again covers. A row
    is matched at the repository path it names, so a citation of a path the working tree has replaced by a
    symlink resolves to the link's target and that row does not count for it."""
    repo = _CiteRepo(tmp / "citecheck-show", {"tools/g.py": b"g1\ng2\ng3\n", "tools/h.py": b"h1\nh2\n",
                                              "tools/t.py": b"t1\n", "tools/l.py": b"l1\n"})
    tools_dir = repo.root / "tools"
    (tools_dir / "h.py").write_bytes(b"h1\nH2\n")
    subprocess.run(["git", "-C", str(repo.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c",
                    "commit.gpgsign=false", "commit", "-q", "-am", "next"], check=True, capture_output=True, timeout=30)
    (tools_dir / "h.py").write_bytes(b"h1\nh2\n")
    (tools_dir / "g.py").write_bytes(b"g1\nG2\ng3\n")
    os.unlink(str(tools_dir / "l.py"))
    os.symlink("t.py", str(tools_dir / "l.py"))
    repo.bash("git show HEAD:tools/g.py", "g1\ng2\ng3\n", "show")
    repo.bash("git show HEAD~1:tools/h.py", "h1\nh2\n", "show")
    repo.bash("git show HEAD:tools/l.py", "l1\n", "show")
    verdict, _note, new = _citecheck_run(repo.root, "git commit -m 'tools/g.py:1 tools/h.py:2 tools/l.py:1'", "show")
    check("citeledger/git-show-row-judged-at-working-tree",
          ([(r["via"], r["rel"], r["start"], r["end"], r["rev"]) for r in repo.rows("show")], verdict,
           [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]]),
          ([("git-show", "tools/g.py", 1, 3, "HEAD"), ("git-show", "tools/h.py", 1, 2, "HEAD~1"),
            ("git-show", "tools/l.py", 1, 1, "HEAD")], "warn",
           [("tools/g.py:1", "stale"), ("tools/h.py:2", "covered"), ("tools/l.py:1", "unread")]))


def _citecheck_deadline_cases(tmp):
    """One deadline bounds every git probe and cited-file read of the check: with the budget cut to 2.5
    seconds and one kind of probe slowed (the stand-in of _cite_stub_git, which sleeps 8 seconds when one
    of its arguments is the slowed word), or a cited file of 8 MiB read in chunks slowed by half a second
    each, each of the session's toplevel probe, a git -C repository's toplevel probe, the base resolution,
    the merge base, the diff probe and the file read is reached and cut short, and what it could not finish
    is named: the repository not resolved, or the range not judged (budget). Reached and cut short are shown
    by evidence a loaded runner cannot change, never by a wall clock: the slowed probe leaves a "start" mark
    and no "done" mark, every probe was given a timeout of at most the budget, never its 5 second default
    (_cite_probe_timeouts), and the slowed file read makes at least one read and fewer than the 9 a whole
    read takes (8 chunks of 1 MiB and the empty read at its end). The budget leaves the probes before the
    slowed one ample time on a busy runner, and still cuts the slowed one off 5.5 seconds before its
    stand-in would finish."""
    repo = _CiteRepo(tmp / "citecheck-slow", {"tools/a.py": b"a1\na2\n", "big.txt": b"x\n" * (4 * 1024 * 1024)})
    other = _CiteRepo(tmp / "citecheck-slow-other", {"lib/x.py": b"x1\n"})
    real_read, big = os.read, os.stat(str(repo.root / "big.txt"))
    reads = []

    def slow_read(fd, size):  # a read of big.txt is slowed and counted; any other (a probe's pipe) is not
        try:
            st = os.fstat(fd)
            target = (st.st_dev, st.st_ino) == (big.st_dev, big.st_ino)
        except OSError:
            target = False
        if target:
            reads.append(size)
            time.sleep(0.5)
        return real_read(fd, size)
    stub_dir = tmp / "citecheck-stub-bin"
    _cite_stub_git(stub_dir)
    shapes = (("toplevel", "--show-toplevel", "git commit -m 'tools/a.py:1'"),
              ("other-toplevel", str(other.root), "git -C {} commit -m 'lib/x.py:1'".format(other.root)),
              ("base", "--verify", "git commit -m 'tools/a.py:1'"),
              ("merge-base", "merge-base", "gh pr create --base main --body 'tools/a.py:1'"),
              ("diff", "-U0", "git commit -m 'tools/a.py:1'"),
              ("file-read", None, "git commit -m 'big.txt:1'"))
    saved_budget = aiqt_hooks._CITECHK_BUDGET
    got = []
    try:
        aiqt_hooks._CITECHK_BUDGET = 2.5
        for name, word, command in shapes:
            del reads[:]
            outcome, within = [], []

            def run(name=name, command=command, word=word, outcome=outcome, within=within):
                try:
                    if word is None:
                        os.read = slow_read
                    within.append(_cite_probe_timeouts(
                        lambda: outcome.extend(_citecheck_run(repo.root, command, "slow-" + name)), 2.5))
                finally:
                    os.read = real_read
            marks = _cite_stubbed(stub_dir, word or "", tmp / "citecheck-mark-{}".format(name), run)
            verdict, note, new = outcome
            got.append((name, marks, 0 < len(reads) < 9, within, verdict,
                        "before it resolved the repository" in note, "incomplete (budget)" in note,
                        [(c["cite"], c["class"]) for r in new for c in r["detail"]["citations"]]))
    finally:
        aiqt_hooks._CITECHK_BUDGET = saved_budget
    # Each slowed probe is reached and cut off ("start" alone, with no slowed read); the file read is
    # reached and cut short (no slowed probe, and between 1 and 8 slowed reads).
    check("citeledger/check-deadline-bounds-slow-probes", got,
          [("toplevel", ["start"], False, [True], "warn", True, False, []),
           ("other-toplevel", ["start"], False, [True], "warn", False, True, [("lib/x.py:1", "unjudged")]),
           ("base", ["start"], False, [True], "warn", False, True, [("tools/a.py:1", "unjudged")]),
           ("merge-base", ["start"], False, [True], "warn", False, True, [("tools/a.py:1", "unjudged")]),
           ("diff", ["start"], False, [True], "warn", False, True, [("tools/a.py:1", "unjudged")]),
           ("file-read", [], True, [True], "warn", False, True, [("big.txt:1", "unjudged")])])


def _citecheck_path_cases(tmp):
    """Every path the citation check resolves is resolved only when it is at most _CITE_MAX_PATH characters
    long and the check's deadline has not been reached: a message or body file (-F), a cited path (joined
    to the repository top where relative), the repository top a diff is read against, and a git -C
    directory and the session cwd it is compared with. Each is tried one under, at and one over the bound
    (padded with slashes or ./ segments, which resolution drops), and with a stand-in clock moved past the
    deadline just before the step that resolves it. Whether a path was resolved is read from the paths given
    to _cite_real inside _citechk_run (_cite_resolutions), never from timing; the toplevel probe is a
    stand-in naming a fixed top, so a path git itself would refuse still reaches its site. Over the bound
    the body file is named as not read, in the note and in the sources_unread of the command's warn row,
    and the range as not judged, the bound named; past the deadline, the budget named, and a body file not
    read for it is named in the note that says the repository was not resolved. Where both the session cwd
    and the git -C directory are refused, the reason for each is named. One end-to-end call, with the real
    toplevel probe, cites a path of 800,000 characters: it returns the warn note naming the bound within the
    budget in this process's CPU time, the note and the row showing the path shortened; a cited path and a
    body file path one under, at and one over _CITECHK_PATH_SHOWN characters are shown whole, whole and
    shortened to that many."""
    repo = _CiteRepo(tmp / "citecheck-path", {"tools/a.py": b"a1\na2\n", "msg.txt": b"see tools/a.py:1\n"})
    root, limit = repo.top, aiqt_hooks._CITE_MAX_PATH
    shown_max = getattr(aiqt_hooks, "_CITECHK_PATH_SHOWN", 200)  # a hook without the bound shows paths whole
    out_of_time = aiqt_hooks._CITECHK_OUT_OF_TIME
    saved = (aiqt_hooks.time, aiqt_hooks._recovery_toplevel)

    def pad(head, tail, size):
        return head + "/" * (size - len(head) - len(tail)) + tail

    def cited(size):
        """A relative cited path naming tools/a.py that is size characters long once joined to root."""
        rest = size - len(root) - 1 - len("tools/a.py")
        detour = "tools/../" if rest % 2 else ""
        return "./" * ((rest - len(detour)) // 2) + detour + "tools/a.py"

    def run(command, session, cwd=root, top=root, step=None, after=False):
        """citation_read_check on command at cwd, its toplevel probe a stand-in naming top: the paths
        resolved inside _citechk_run, the command's guard-events rows, and the note. With step, a stand-in
        clock is moved past the deadline just before aiqt_hooks.<step> runs (for _recovery_toplevel, as the
        stand-in probe returns), or with after, once its first call has returned."""
        clock = _CiteClock(1000.0, 1000.0 + aiqt_hooks._CITECHK_BUDGET + 0.001, False)
        saved_step = None if step in (None, "_recovery_toplevel") else getattr(aiqt_hooks, step)

        def toplevel(*args, **kwargs):
            if step == "_recovery_toplevel":
                clock.advance()
            return top

        def stand_in(*args, **kwargs):
            if not after:
                clock.advance()
            try:
                return saved_step(*args, **kwargs)
            finally:
                clock.advance()
        events = Path(aiqt_hooks._orch_state_dir_for_root(top)) / "guard-events.jsonl"
        before = len(events.read_text(encoding="utf-8").splitlines()) if events.is_file() else 0
        result = []
        try:
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel = clock, toplevel
            if saved_step is not None:
                setattr(aiqt_hooks, step, stand_in)
            paths = _cite_resolutions(lambda: result.append(aiqt_hooks.citation_read_check({
                "hook_event_name": "PreToolUse", "cwd": cwd, "tool_name": "Bash", "session_id": session,
                "tool_input": {"command": command}})), "_citechk_run")
        finally:
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel = saved
            if saved_step is not None:
                setattr(aiqt_hooks, step, saved_step)
        note = result[0][1].get("systemMessage", "") if isinstance(result[0][1], dict) else ""
        rows = [r for r in (json.loads(x) for x in (events.read_text(encoding="utf-8").splitlines()[before:]
                                                    if events.is_file() else []) if x.strip())
                if r.get("kind") == "citint"]
        return paths, rows, note

    def summary(rows):
        """The classes and the decisions of rows, and each sources_unread entry as whether its label is a -F
        option and its reason."""
        return ([c["class"] for r in rows for c in r["detail"]["citations"]], [r["decision"] for r in rows],
                [(u.startswith("-F "), u.split(": ", 1)[-1]) for r in rows for u in r["detail"]["sources_unread"]])

    # Per site: the call at a size, the path whose resolution is looked for, and the step the stand-in clock
    # is moved past the deadline before.
    sites = (
        ("body", lambda size: (pad(root, "msg.txt", size), "git commit -F " + pad(root, "msg.txt", size), {}),
         "_citechk_read_body"),
        ("cited", lambda size: (os.path.join(root, cited(size)), "git commit -m 'see {}:1'".format(cited(size)),
                                {}), "_citechk_resolve"),
        ("top", lambda size: (pad(root, "", size), "git commit -m 'see {}/tools/a.py:1'".format(root),
                              {"top": pad(root, "", size)}), "_citechk_diff"),
        ("git-C-directory", lambda size: (pad(root, "", size), "git -C {} commit -m 'see tools/a.py:1'".format(
            pad(root, "", size)), {}), "_recovery_toplevel"),
        ("git-C-cwd", lambda size: (pad(root, "", size), "git -C {} commit -m 'see tools/a.py:1'".format(root),
                                    {"cwd": pad(root, "", size)}), "_recovery_toplevel"))
    bound = "is longer than {} characters".format(limit)
    sized, timed = {}, {}
    for site, call, step in sites:
        for size in (limit - 1, limit, limit + 1):
            path, command, where = call(size)
            paths, rows, note = run(command, "path-{}-{}".format(site, size), **where)
            sized.setdefault(site, []).append((path in paths,) + summary(rows) + (bound in note, "budget" in note))
        path, command, where = call(limit - 1)
        paths, rows, note = run(command, "path-{}-late".format(site), step=step, **where)
        timed[site] = (path in paths,) + summary(rows)[:2] + ("budget" in note,
                                                              "was not read: {}.".format(out_of_time) in note)
    inside, over = (True, ["unread"], ["warn"], [], False, False), (False, ["unjudged"], ["warn"], [], True, False)
    # Over the bound the body file's command still earns its warn row, which names the file as not read.
    check("citeledger/check-path-bound", sized,
          {"body": [inside, inside, (False, [], ["warn"], [(True, "its path {}, the most this check resolves".format(
              bound))], True, False)],
           "cited": [inside, inside, over], "top": [inside, inside, over], "git-C-directory": [inside, inside, over],
           "git-C-cwd": [inside, inside, over]})
    # Past the deadline: the body file's resolution comes before the toplevel probe, so that probe is not
    # started either: the note says the repository was not resolved within the budget and names the body
    # file as not read for it, and no row is written.
    check("citeledger/check-no-resolution-past-deadline", timed,
          {"body": (False, [], [], True, True), "cited": (False, ["unjudged"], ["warn"], True, False),
           "top": (False, ["unjudged"], ["warn"], True, False),
           "git-C-directory": (False, ["unjudged"], ["warn"], True, False),
           "git-C-cwd": (False, ["unjudged"], ["warn"], True, False)})
    # Both the session cwd and the git -C directory refused: the range's detail names the reason for each,
    # the cwd's first. Both over the length bound; the cwd over it and the directory refused for the budget
    # (the clock moved past the deadline once the cwd's refusal has returned, so the budget is named too);
    # and both refused for the budget (named once).
    long_cwd, long_dir, short_dir = pad(root, "", limit + 1), pad(root, "/.", limit + 1), root + "/."
    over_cwd = "the session cwd {}, the most this check resolves".format(bound)
    over_dir = "the git -C directory {}, the most this check resolves".format(bound)
    prefix = "  tools/a.py:1 (not judged: "
    both = []
    for session, command, cwd, step, after in (
            ("both-bound", "git -C {} commit -m 'see tools/a.py:1'".format(long_dir), long_cwd, None, False),
            ("bound-then-budget", "git -C {} commit -m 'see tools/a.py:1'".format(short_dir), long_cwd,
             "_citechk_real", True),
            ("both-budget", "git -C {} commit -m 'see tools/a.py:1'".format(short_dir), root + "/./.",
             "_citechk_real", False)):
        _paths, rows, note = run(command, session, cwd=cwd, step=step, after=after)
        details = [line[len(prefix):-1] for line in note.split("\n") if line.startswith(prefix)]
        both.append(summary(rows)[:2] + (details, "incomplete (budget)" in note))
    check("citeledger/check-both-refusals-named", both,
          [(["unjudged"], ["warn"], [over_cwd + "; " + over_dir], False),
           (["unjudged"], ["warn"], [over_cwd + "; " + out_of_time], True),
           (["unjudged"], ["warn"], [out_of_time], True)])
    # End to end, with the real toplevel probe and clock: a cited path of 800,000 characters is never
    # resolved (none longer than the bound is), the warn note naming the bound comes back within the budget
    # in this process's CPU time, and the note and the row show the path shortened, so the note stays under
    # 4,096 characters here.
    long_path = "x/" * 399998 + "a.py"
    events = Path(aiqt_hooks._orch_state_dir_for_root(str(repo.root))) / "guard-events.jsonl"
    before = len(events.read_text(encoding="utf-8").splitlines()) if events.is_file() else 0
    result = []
    began = time.process_time()
    paths = _cite_resolutions(lambda: result.append(aiqt_hooks.citation_read_check({
        "hook_event_name": "PreToolUse", "cwd": str(repo.root), "tool_name": "Bash", "session_id": "path-long",
        "tool_input": {"command": "git commit -m 'see {}:1'".format(long_path)}})), "_citechk_run")
    cpu = time.process_time() - began
    note = result[0][1].get("systemMessage", "") if isinstance(result[0][1], dict) else ""
    rows = [json.loads(x) for x in (events.read_text(encoding="utf-8").splitlines()[before:]
                                    if events.is_file() else []) if x.strip()]
    check("citeledger/check-long-cited-path",
          (len(long_path), _verdict(result[0]), [c["class"] for r in rows for c in r["detail"]["citations"]],
           "is longer than {} characters".format(limit) in note, cpu <= aiqt_hooks._CITECHK_BUDGET,
           max([0] + [len(path) for path in paths]) <= limit, len(note) < 4096,
           [len(c["cite"]) for r in rows for c in r["detail"]["citations"]]),
          (800000, "warn", ["unjudged"], True, True, True, True, [shown_max + len(":1")]))
    # A cited path that names no file (its class nofile) and a relative body file that is not there (it
    # cannot be opened), each one under, at and one over _CITECHK_PATH_SHOWN characters: the row's cite and
    # its sources_unread label show the path whole, whole, and shortened to that many characters.
    shown = {"cited": [], "body": []}
    for size in (shown_max - 1, shown_max, shown_max + 1):
        name = "d" * (size - len("/a.py")) + "/a.py"
        _paths, rows, _note = run("git commit -m 'see {}:1'".format(name), "shown-cited-{}".format(size))
        shown["cited"] += [(len(c["cite"]) - len(":1"), c["cite"] == name + ":1")
                           for r in rows for c in r["detail"]["citations"]]
        body = "m" * size
        _paths, rows, _note = run("git commit -F " + body, "shown-body-{}".format(size))
        shown["body"] += [(len(entry.split(": ", 1)[0]) - len("-F "), entry.startswith("-F {}: ".format(body)))
                          for r in rows for entry in r["detail"]["sources_unread"]]
    check("citeledger/check-shown-path-bound", shown,
          {"cited": [(shown_max - 1, True), (shown_max, True), (shown_max, False)],
           "body": [(shown_max - 1, True), (shown_max, True), (shown_max, False)]})


class _CiteCutPattern:
    """Stands in for a compiled pattern of aiqt_hooks: match() is the pattern's own, and its cut-th call moves
    the stand-in clock set as clock on, so a scan that matches the pattern once per line reaches the
    deadline at a known line."""

    def __init__(self, pattern, clock, cut):
        self.pattern, self.clock, self.cut, self.calls = pattern, clock, cut, 0

    def match(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.cut:
            self.clock.advance()
        return self.pattern.match(*args, **kwargs)


def _cite_touches(targets, clock, run, cut=None):
    """run() with each step a hook can take on a path in targets recorded as whether the stand-in clock had
    been advanced when it was made: an os.stat, os.lstat, os.open, os.readlink or builtin open of the path,
    and an os.fstat or os.read of a descriptor os.open gave for it (until os.close closes it). Those flags,
    in order. With cut, the name of one of those os functions, the clock is advanced as the first such step
    on targets returns, so the step after it is the first one the deadline can stop."""
    saved = {"stat": os.stat, "lstat": os.lstat, "open": os.open, "readlink": os.readlink, "fstat": os.fstat,
             "read": os.read, "close": os.close}
    saved_builtin, made, fds = builtins.open, [], set()

    def touched(name, step):
        made.append(clock.advanced)
        try:
            return step()
        finally:
            if name == cut:
                clock.advance()

    def by_path(name):
        def stand_in(path, *args, **kwargs):
            if not (isinstance(path, str) and path in targets):
                return saved[name](path, *args, **kwargs)
            result = touched(name, lambda: saved[name](path, *args, **kwargs))
            if name == "open":
                fds.add(result)
            return result
        return stand_in

    def by_fd(name):
        def stand_in(fd, *args, **kwargs):
            if fd not in fds:
                return saved[name](fd, *args, **kwargs)
            return touched(name, lambda: saved[name](fd, *args, **kwargs))
        return stand_in

    def close(fd, *args, **kwargs):
        fds.discard(fd)
        return saved["close"](fd, *args, **kwargs)

    def builtin_open(file, *args, **kwargs):
        if not (isinstance(file, str) and file in targets):
            return saved_builtin(file, *args, **kwargs)
        return touched("builtin open", lambda: saved_builtin(file, *args, **kwargs))
    try:
        os.stat, os.lstat, os.open, os.readlink = by_path("stat"), by_path("lstat"), by_path("open"), by_path("readlink")
        os.fstat, os.read, os.close, builtins.open = by_fd("fstat"), by_fd("read"), close, builtin_open
        run()
    finally:
        os.stat, os.lstat, os.open, os.readlink = saved["stat"], saved["lstat"], saved["open"], saved["readlink"]
        os.fstat, os.read, os.close, builtins.open = saved["fstat"], saved["read"], saved["close"], saved_builtin
    return made


def _citeledger_walk_cases(tmp):
    """How far a resolution walks. It follows at most _CITE_MAX_LINKS symlinks: a chain of one under, at and
    one over that many resolves, resolves, and is refused for the link bound, and wherever it completes its
    result is os.path.realpath's. A symlink passed through again counts again, as many symlinks as Linux
    follows for it each time, though the walk takes it from its cache: passes through one symlink, or
    through a symlink to a symlink, that follow one or two under, exactly, and one or two over that many
    resolve, resolve, and are refused for the link bound, as the kernel opens, opens, and refuses each
    (ELOOP). A symlink loop (one to itself, two to each other), met within a path or at its end, is refused
    for the link bound, as the kernel refuses it (ELOOP), where os.path.realpath keeps the link's own path.
    It stops at the deadline between two components: with a stand-in clock moved just inside, at
    or just past the deadline as the first symlink of a chain of three is read, the walk reads the rest, or
    stops with the deadline as its reason and makes no further lstat. It stops at the deadline before it
    reads a symlink: with the clock moved as the lstat of that first symlink returns, the walk reads all
    three, or reads none and stops with the deadline as its reason. The link
    fan-out (24 symlinked directories of 700 symlinks each, every one to 819 times e/../, cited as one path
    of 162 characters joined to the repository top) ends within each hook's budget in this process's CPU
    time: the check names the link bound for the range, and for a body file reached the same way, which it
    names as not read, and the recorder records nothing."""
    resolution = getattr(aiqt_hooks, "_cite_resolution", None)
    links = getattr(aiqt_hooks, "_CITE_MAX_LINKS", 40)  # a hook without the bound is held to Linux's own

    def resolved(path, deadline):
        """The hook's (resolution, reason) of path; a hook without _cite_resolution names no reason."""
        if resolution is None:
            return aiqt_hooks._cite_resolve(path, deadline), None
        return resolution(path, deadline)
    walk = tmp / "cite-walk"
    (walk / "d" / "e").mkdir(parents=True)
    (walk / "d" / "f").write_bytes(b"f\n")
    target = os.path.realpath(str(walk / "d" / "f"))

    def chain(count):
        """A chain l1 -> l2 -> ... -> l<count> -> the target, in its own directory: the path of l1."""
        where = walk / "chain-{}".format(count)
        where.mkdir()
        os.symlink(target, str(where / "l{}".format(count)))
        for k in range(count - 1, 0, -1):
            os.symlink("l{}".format(k + 1), str(where / "l{}".format(k)))
        return str(where / "l1")
    for name, value in (("rel", "d"), ("abs", str(walk / "d")), ("loop1", "loop2"), ("loop2", "loop1"),
                        ("dangling", "missing/x"), ("d/e/up", "../e/..")):
        os.symlink(value, str(walk / name))
    shapes = ["/", "//", "/.", "/..", str(walk), str(walk) + "/", str(walk / "rel" / "f"), str(walk) + "/abs/e/../f",
              str(walk) + "/rel/../rel/f", str(walk / "dangling"), str(walk) + "/dangling/y", str(walk) + "/d/f/g",
              str(walk) + "/d/e/up/f", str(walk) + "/no/../d/./f", str(walk) + "/rel//e/", "relative/x", ".", ""]
    unequal = [shape for shape in shapes if resolved(shape, time.monotonic() + 60) != (os.path.realpath(shape), None)]
    bounded = [resolved(chain(count), time.monotonic() + 60) for count in (links - 1, links, links + 1)]
    check("citeledger/resolve-link-bound", (unequal, bounded),
          ([], [(target, None), (target, None), (None, "links")]))
    # The deadline inside the walk: a stand-in readlink moves the stand-in clock on as the first symlink of
    # l1 -> l2 -> l3 -> target is read; each lstat made once the clock reads at or past the deadline counts.
    three, stopped = chain(3), []
    saved_time, saved_readlink, saved_lstat = aiqt_hooks.time, os.readlink, os.lstat
    try:
        for offset in (-0.001, 0.0, 0.001):
            clock = aiqt_hooks.time = _CiteClock(1000.0, 1005.0 + offset, False)
            counts = {"readlink": 0, "late": 0}

            def readlink(path, *args, clock=clock, counts=counts, **kwargs):
                counts["readlink"] += 1
                try:
                    return saved_readlink(path, *args, **kwargs)
                finally:
                    clock.advance()

            def lstat(path, *args, clock=clock, counts=counts, **kwargs):
                counts["late"] += clock.monotonic() >= 1005.0
                return saved_lstat(path, *args, **kwargs)
            os.readlink, os.lstat = readlink, lstat
            try:
                got = resolved(three, 1005.0)
            finally:
                os.readlink, os.lstat = saved_readlink, saved_lstat
            stopped.append((got, counts["readlink"], counts["late"]))
    finally:
        aiqt_hooks.time, os.readlink, os.lstat = saved_time, saved_readlink, saved_lstat
    check("citeledger/resolve-stops-mid-walk", stopped,
          [((target, None), 3, 0), ((None, "deadline"), 1, 0), ((None, "deadline"), 1, 0)])
    # The deadline before a symlink is read: a stand-in lstat moves the stand-in clock on as it finds l1 of
    # l1 -> l2 -> l3 -> target to be a symlink; each lstat or readlink made once the clock reads at or past
    # the deadline counts. Inside it the walk reads all three; at and past it, it reads none.
    first, paused = os.path.join(os.path.realpath(os.path.dirname(three)), "l1"), []
    try:
        for offset in (-0.001, 0.0, 0.001):
            clock = aiqt_hooks.time = _CiteClock(1000.0, 1005.0 + offset, False)
            counts = {"readlink": 0, "late": 0}

            def readlink(path, *args, clock=clock, counts=counts, **kwargs):
                counts["readlink"] += 1
                counts["late"] += clock.monotonic() >= 1005.0
                return saved_readlink(path, *args, **kwargs)

            def lstat(path, *args, clock=clock, counts=counts, **kwargs):
                counts["late"] += clock.monotonic() >= 1005.0
                try:
                    return saved_lstat(path, *args, **kwargs)
                finally:
                    if path == first:
                        clock.advance()
            os.readlink, os.lstat = readlink, lstat
            try:
                got = resolved(three, 1005.0)
            finally:
                os.readlink, os.lstat = saved_readlink, saved_lstat
            paused.append((got, counts["readlink"], counts["late"]))
    finally:
        aiqt_hooks.time, os.readlink, os.lstat = saved_time, saved_readlink, saved_lstat
    check("citeledger/resolve-no-readlink-past-deadline", paused,
          [((target, None), 3, 0), ((None, "deadline"), 0, 0), ((None, "deadline"), 0, 0)])
    # Passes through one symlink, each one Linux follows again, cached or not: pass1 leads to d, so a pass
    # through it follows one symlink, and pass2 to pass1, so a pass through it follows two. A path through
    # either that follows one or two under, exactly, and one or two over _CITE_MAX_LINKS symlinks before
    # naming d/f resolves to os.path.realpath's result, resolves, and is refused for the link bound, as the
    # kernel opens, opens, and refuses it (ELOOP). Built under the resolved directory, so no symlink above
    # it adds to the count.
    base = os.path.realpath(str(walk))
    for name, value in (("pass1", "d"), ("pass2", "pass1")):
        os.symlink(value, os.path.join(base, name))

    def kernel(path):
        try:
            os.stat(path)
            return "opens"
        except OSError as exc:
            return errno.errorcode.get(exc.errno, exc.errno)
    passes = {}
    for name, per in (("pass1", 1), ("pass2", 2)):
        for count in (links // per - 1, links // per, links // per + 1):
            path = base + "/{}/..".format(name) * count + "/d/f"
            got = resolved(path, time.monotonic() + 60)
            passes.setdefault(name, []).append((count * per, got, got[0] in (None, os.path.realpath(path)),
                                                kernel(path)))
    # A loop, a symlink met again inside its own resolution (self to itself, and loop1 to loop2 and back),
    # which the kernel follows until it passes its bound and then refuses (ELOOP): refused for the link
    # bound, whether the path goes on through it (self/../d/f) or ends at it.
    os.symlink("self", os.path.join(base, "self"))
    loops = ("self", "self/../d/f", "loop1", "loop1/../d/f", "loop1/x")
    passes["loops"] = [(rel, resolved(base + "/" + rel, time.monotonic() + 60), kernel(base + "/" + rel))
                       for rel in loops]
    want = {name: [(links - per, (target, None), True, "opens"), (links, (target, None), True, "opens"),
                   (links + per, (None, "links"), True, "ELOOP")] for name, per in (("pass1", 1), ("pass2", 2))}
    want["loops"] = [(rel, (None, "links"), "ELOOP") for rel in loops]
    check("citeledger/resolve-link-passes", passes, want)
    # The link fan-out, built in this run's own directory: each s<i> leads through d<i>/t0 ... t699, and each
    # t<j> back to d<i> by 819 times e/../ (a target of 4,095 characters).
    fan = _CiteRepo(tmp / "cite-fan-out", {"a.py": b"a1\n", "msg.txt": b"see a.py:1\n"})
    for i in range(24):
        (fan.root / "d{}".format(i) / "e").mkdir(parents=True)
        for j in range(700):
            os.symlink("e/../" * 819, str(fan.root / "d{}".format(i) / "t{}".format(j)))
        os.symlink("d{}/".format(i) + "/".join("t{}".format(j) for j in range(700)), str(fan.root / "s{}".format(i)))
    token = "".join("s{}/../".format(i) for i in range(24)) + "a.py"
    began = time.process_time()
    verdict, note, rows = _citecheck_run(fan.root, "git commit -m 'see {}:1'".format(token), "fan-out")
    check_cpu = time.process_time() - began
    # The same fan-out leading to a body file (-F): the file is named as not read, the link bound its reason.
    began = time.process_time()
    _verdict_, _note, body_rows = _citecheck_run(fan.root, "git commit -F " + os.path.join(
        str(fan.root), token[:-len("a.py")] + "msg.txt"), "fan-out-body")
    body = ([r["decision"] for r in body_rows], [u.split(": ", 1)[-1] for r in body_rows
                                                 for u in r["detail"]["sources_unread"]],
            time.process_time() - began <= aiqt_hooks._CITECHK_BUDGET)
    joined, walked = os.path.join(str(fan.root), token), []
    saved_real = aiqt_hooks._cite_real

    def real(path, *args, **kwargs):
        result = saved_real(path, *args, **kwargs)
        if path == joined:
            walked.append(result[1] if isinstance(result, tuple) else "resolved")
        return result
    began = time.process_time()
    try:
        aiqt_hooks._cite_real = real
        fan.call("Read", {"file_path": joined}, {"type": "text", "file": {
            "filePath": joined, "content": "a1", "startLine": 1, "numLines": 1}}, "fan-out-read")
    finally:
        aiqt_hooks._cite_real = saved_real
    record_cpu = time.process_time() - began
    check("citeledger/link-fan-out-bounded",
          (len(token), verdict, [c["class"] for r in rows for c in r["detail"]["citations"]],
           [r["detail"]["budget_exhausted"] for r in rows],
           "leads through more than {} symbolic links".format(links) in note, check_cpu <= aiqt_hooks._CITECHK_BUDGET,
           walked, len(fan.rows("fan-out-read")), record_cpu <= aiqt_hooks._CITE_BUDGET, body),
          (162, "warn", ["unjudged"], [False], True, True, ["links"], 0, True,
           (["warn"], ["its path leads through more than {} symbolic links, the most this check follows".format(
               links)], True)))


def _citecheck_step_cases(tmp):
    """No step of either hook is taken on a resolved path once the deadline is reached, and the check reads
    each message text once, in its command's option order, and stops its scan at the deadline. With a
    stand-in clock moved just inside, at or just past the deadline as one step returns, no further step is
    made on the path (_cite_touches records each os.stat, os.lstat, os.open, os.readlink and builtin open of
    it, and each os.fstat and os.read of a descriptor opened for it): in the check, the body file's open
    after its resolution, its fstat after its open, its first read chunk after its fstat and its next chunk
    after the first (_citechk_read_body), the cited file's stat after its resolution (_citechk_resolve), its
    size stat (_citechk_class), its open after its resolution and its fstat after its open (_cite_read), and
    a cited symlink's readlink after the lstat that finds it (_cite_real); in the recorder, a sed read's
    open after its resolution and its fstat after its open (_cite_read), a Grep path's stat
    (_cite_from_grep), a grep command's file stat after its resolution, and the second stat of the files a
    grep command names after the first (_cite_grep_command). Inside, each is made and the range judged or
    the row recorded; at and past it, the range is not judged (budget), the body file is named as not read
    (budget), or nothing is recorded. A grep command gives each path it names one stat, a single named file
    included. The here-document a command reads is scanned once, whatever the number
    of its -F - options: 1,000 options over a 101,000 character here-document take one scan and end within
    the budget in this process's CPU time, and a command's row lists each citation and source once. Each
    command reads only the here-document its own standard input receives (bash gives it the last of its
    standard-input redirections, and a later one of fd 0 onto itself leaves it), its sources keep their
    option order in the note, the row and under the citation cap, and where its standard input is not its
    own here-document it is named as not read. A command that does not lex, or in which a backslash-newline
    splits a here-document delimiter where bash reads one (at an operator the lexer reads, quoted or not, or
    unquoted inside a command substitution in double quotes), is scanned as written and names the expansion
    of each here-document with an unquoted delimiter as not read (a shift inside a one-line $((...)) is
    none), and each rule that decides where such a delimiter counts is shown to decide its vector, which
    gives another result under a variant of the hook without that rule; a command is read after the
    reserved words that begin its segment (a brace group's '{', '!', 'if', 'then', 'do', 'time -p',
    'time --', 'coproc'). The scan of a message stops at the deadline: a stand-in pattern moves the clock on
    at line 2 of the masking or of the search, line 3 is not scanned, and the note says no citation was
    checked, naming the budget."""
    steps = _CiteRepo(tmp / "citecheck-steps", {"tools/a.py": b"a1\na2\n", "msg.txt": b"see tools/a.py:1\n",
                                                "a.txt": b"a1\n", "b.txt": b"b1\n"}, links=(("tools/l.py", "a.py"),))
    root, budget = steps.top, aiqt_hooks._CITECHK_BUDGET

    def spelled(rel):
        """The spellings a hook may give a path under the repository: joined to it, and resolved."""
        return {os.path.join(root, rel), os.path.join(str(steps.root), rel)}
    saved = (aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git)

    def stepped(step, offset, budget_s, targets, call):
        """call() with a stand-in clock moved to the deadline plus offset once aiqt_hooks.<step> first
        returns, or, for a step written os.NAME, once the first such call on targets returns
        (_cite_touches), the toplevel probe a stand-in naming the repository and no diff probe: whether a
        step on targets was made after that, and call's result."""
        clock = _CiteClock(1000.0, 1000.0 + budget_s + offset, False)
        cut = step[len("os."):] if step.startswith("os.") else None
        saved_step, out = (None if cut else getattr(aiqt_hooks, step)), []

        def stand_in(*args, **kwargs):
            try:
                return saved_step(*args, **kwargs)
            finally:
                clock.advance()
        try:
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel = clock, lambda *args, **kwargs: str(steps.root)
            aiqt_hooks._review_git = lambda *args, **kwargs: None
            if cut is None:
                setattr(aiqt_hooks, step, stand_in)
            made = _cite_touches(targets, clock, lambda: out.append(call()), cut)
        finally:
            aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = saved
            if cut is None:
                setattr(aiqt_hooks, step, saved_step)
        return any(made), out[0]
    a_py, msg, l_py = spelled("tools/a.py"), spelled("msg.txt"), spelled("tools/l.py")
    body_file, cites_a = "git commit -F " + os.path.join(root, "msg.txt"), "git commit -m 'see tools/a.py:1'"
    # Per site: the step the clock is moved at, the command, and the paths whose steps are recorded. The
    # body file is cut at the return of its resolution, its open, its fstat and its first read chunk; the
    # cited file at the return of its resolution (before its stat), of _citechk_resolve (before its size
    # stat), of _cite_resolve (before its open) and of its open (before its fstat); and a cited symlink
    # (tools/l.py, to a.py) at the lstat that finds it to be one, before its readlink.
    sites = (("body-open", "_citechk_real", body_file, msg), ("body-fstat", "os.open", body_file, msg),
             ("body-read", "os.fstat", body_file, msg), ("body-chunk", "os.read", body_file, msg),
             ("cited-stat", "_citechk_real", cites_a, a_py), ("cited-size", "_citechk_resolve", cites_a, a_py),
             ("cited-open", "_cite_resolve", cites_a, a_py), ("cited-fstat", "os.open", cites_a, a_py),
             ("cited-readlink", "os.lstat", "git commit -m 'see tools/l.py:1'", l_py))
    checked = {}
    for site, step, command, targets in sites:
        for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
            made, (_verdict_, note, rows) = stepped(step, offset, budget, targets, lambda command=command, site=site,
                                                    name=name: _citecheck_run(steps.root, command,
                                                                              "step-{}-{}".format(site, name)))
            checked.setdefault(site, []).append((made, [c["class"] for r in rows for c in r["detail"]["citations"]],
                                                 "budget" in note))
    judged, late, unread = [(True, ["unread"], False)], [(False, ["unjudged"], True)] * 2, [(False, [], True)] * 2
    check("citeledger/check-no-step-past-deadline", checked,
          {site: judged + (unread if site.startswith("body-") else late) for site, _step, _command, _targets in sites})
    # The recorder: each tool's step cut as the first resolution returns, a sed read cut at its open (before
    # its fstat), and a grep command naming two files cut at the first stat of the files it names (before
    # the second stat).
    a_txt, recorded = spelled("a.txt"), {}
    tools = (("sed", "_cite_real", a_txt, lambda s: steps.bash("sed -n 1p a.txt", "a1\n", s)),
             ("Grep", "_cite_real", a_txt, lambda s: steps.grep(steps.root / "a.txt", "1:a1", s)),
             ("grep", "_cite_real", a_txt, lambda s: steps.bash("grep -n a1 a.txt", "1:a1\n", s)),
             ("sed-fstat", "os.open", a_txt, lambda s: steps.bash("sed -n 1p a.txt", "a1\n", s)),
             ("grep-stat", "os.stat", a_txt | spelled("b.txt"),
              lambda s: steps.bash("grep -n a1 a.txt b.txt", "a.txt:1:a1\n", s)))
    for tool, step, targets, call in tools:
        for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
            session = "step-{}-{}".format(tool, name)
            made, _result = stepped(step, offset, aiqt_hooks._CITE_BUDGET, targets,
                                    lambda call=call, session=session: call(session))
            recorded.setdefault(tool, []).append((made, len(steps.rows(session))))
    check("citeledger/recorder-no-step-past-deadline", recorded,
          {tool: [(True, 1), (False, 0), (False, 0)] for tool, _step, _targets, _call in tools})
    # One stat per path a grep command names: the stat that admits a single named file also says it is a
    # regular file, so it is not given to stat again.
    named_files, saved_stat, per_path = a_txt | spelled("b.txt"), os.stat, {}
    for name, command, shown in (("one", "grep -n a1 a.txt", "1:a1\n"), ("two", "grep -n a1 a.txt b.txt",
                                                                         "a.txt:1:a1\n")):
        counts = {}

        def counted(path, *args, counts=counts, **kwargs):
            if isinstance(path, str) and path in named_files:
                counts[os.path.basename(path)] = counts.get(os.path.basename(path), 0) + 1
            return saved_stat(path, *args, **kwargs)
        try:
            os.stat = counted
            steps.bash(command, shown, "stat-" + name)
        finally:
            os.stat = saved_stat
        per_path[name] = (counts, len(steps.rows("stat-" + name)))
    check("citeledger/grep-one-stat-per-path", per_path,
          {"one": ({"a.txt": 1}, 1), "two": ({"a.txt": 1, "b.txt": 1}, 1)})
    # Here-documents: each body scanned once (the scans of _citechk_extract counted), whatever the number of
    # -F - options, and each command reads only the here-document its own standard input receives.
    saved_extract, scans = aiqt_hooks._citechk_extract, []

    def extract(text, *args, **kwargs):
        scans.append(len(text))
        return saved_extract(text, *args, **kwargs)
    body = ("word " * 20 + "\n") * 1000
    shapes = (("options", "git commit" + " -F -" * 1000 + " <<'EOF'\n" + body + "EOF\n"),
              ("three", "git commit -F - -F - -F - <<'EOF'\nsee tools/a.py:1\n```\nx\n```\nEOF\n"),
              ("two-commands", "git commit -F - <<'A'\nsee tools/a.py:1\nA\ngit commit -F - <<'B'\nsee tools/a.py:2\n"
                               "B\n"))
    once = {}
    try:
        aiqt_hooks._citechk_extract = extract
        for name, command in shapes:
            del scans[:]
            began = time.process_time()
            verdict, _note, rows = _citecheck_run(steps.root, command, "heredoc-" + name)
            cpu = time.process_time() - began
            once[name] = (len(scans), cpu <= budget, verdict,
                          [([(c["cite"], c["class"]) for c in r["detail"]["citations"]], r["detail"]["sources"],
                            r["detail"]["masked"].get("fence", 0), r["detail"]["not_judged_past_cap"]) for r in rows])
    finally:
        aiqt_hooks._citechk_extract = saved_extract
    check("citeledger/check-heredoc-once", once,
          {"options": (1, True, "allow", [([], ["here-document"], 0, 0)]),
           "three": (1, True, "warn", [([("tools/a.py:1", "unread")], ["here-document"], 3, 0)]),
           "two-commands": (2, True, "warn", [([("tools/a.py:1", "unread")], ["here-document"], 0, 0),
                                              ([("tools/a.py:2", "unread")], ["here-document"], 0, 0)])})
    # A command's sources keep the order of its options: the here-document on -F - takes that option's
    # place, before or after -m, in the note, in the row's citations and sources, and under the citation
    # cap (lowered to one here), which judges the first source's range and counts the other's.
    saved_cap, ordered, here = aiqt_hooks._CITECHK_MAX_CITES, {}, " <<'EOF'\nsee tools/a.py:1\nEOF\n"
    try:
        for cap in (saved_cap, 1):
            aiqt_hooks._CITECHK_MAX_CITES = cap
            for name, command in (("F-first", "git commit -F - -m 'see tools/a.py:2'" + here),
                                  ("m-first", "git commit -m 'see tools/a.py:2' -F -" + here)):
                _verdict_, note, rows = _citecheck_run(steps.root, command, "order-{}-{}".format(name, cap))
                ordered["{}-{}".format(name, "capped" if cap == 1 else "all")] = (
                    [line.split()[0] for line in note.split("\n") if line.startswith("  tools/")],
                    [c["cite"] for r in rows for c in r["detail"]["citations"]],
                    [r["detail"]["sources"] for r in rows], [r["detail"]["not_judged_past_cap"] for r in rows])
    finally:
        aiqt_hooks._CITECHK_MAX_CITES = saved_cap
    first, second = ["tools/a.py:1", "tools/a.py:2"], ["tools/a.py:2", "tools/a.py:1"]
    check("citeledger/check-heredoc-order", ordered,
          {"F-first-all": (first, first, [["here-document", "-m value"]], [0]),
           "m-first-all": (second, second, [["-m value", "here-document"]], [0]),
           "F-first-capped": (first[:1], first[:1], [["here-document", "-m value"]], [1]),
           "m-first-capped": (second[:1], second[:1], [["-m value", "here-document"]], [1])})
    # Standard input is what bash gives the command, its last standard-input redirection: of two
    # here-documents the second; a file redirected after the here-document replaces it (standard input named
    # as not read), one before it does not; a later redirection that duplicates or moves fd 0 onto itself
    # (<&0, 0<&0, 0>&0, <&00, 0<&0-) leaves the here-document, as bash does, while one from fd 1 or a close
    # (<&-) replaces it; <<- strips the body's leading tabs, as bash does, so a tab does not hide a line as an
    # indented block; and a here-document another command of a pipeline reads is not this command's
    # (standard input named as not read).
    unseen = ["-F -: it is read from standard input, which this check cannot see"]
    stdin, own = {}, [(["tools/a.py:1"], ["here-document"], [])]
    dups = (("self", "<&0"), ("self-fd", "0<&0"), ("self-out", "0>&0"), ("self-00", "<&00"),
            ("self-move", "0<&0-"), ("from-1", "0<&1"), ("closed", "<&-"))
    for name, command in (("two", "git commit -F - <<'A' <<'B'\nsee tools/a.py:1\nA\nsee tools/a.py:2\nB\n"),
                          ("file-after", "git commit -F - <<'A' < a.txt\nsee tools/a.py:1\nA\n"),
                          ("file-before", "git commit -F - < a.txt <<'A'\nsee tools/a.py:1\nA\n"),
                          ("tabs", "git commit -F - <<-'A'\n\tsee tools/a.py:1\n\tA\n"),
                          ("piped", "cat <<'A' | git commit -F -\nsee tools/a.py:1\nA\n")) + tuple(
            (name, "git commit -F - <<'A' {}\nsee tools/a.py:1\nA\n".format(dup)) for name, dup in dups):
        _verdict_, _note, rows = _citecheck_run(steps.root, command, "stdin-" + name)
        stdin[name] = [([c["cite"] for c in r["detail"]["citations"]], r["detail"]["sources"],
                        r["detail"]["sources_unread"]) for r in rows]
    want = {"two": [(["tools/a.py:2"], ["here-document"], [])], "file-after": [([], [], unseen)],
            "file-before": own, "tabs": own, "piped": [([], [], unseen)]}
    want.update((name, own if name.startswith("self") else [([], [], unseen)]) for name, _dup in dups)
    check("citeledger/check-heredoc-stdin", stdin, want)
    # A command that does not lex is scanned as written, and the expansion of each here-document with an
    # unquoted delimiter in it is named as not read, with or without a $ in its body: plain text, whose
    # citation the scan still judges; a citation only the expansion makes, which the scan cannot see; the
    # <<- form; an operator split by a backslash-newline, which bash joins; and two such here-documents, each
    # named. A here-string, and a quoted here-document in a command that does not lex for a process
    # substitution, name none. A '<<' inside $((...)) is a shift and is not named, while one inside a
    # $((...) ) that bash reads as a command substitution of a subshell is a here-document and is named. A
    # '$((' that bash does not expand, in a comment or in a quoted here-document's body, does not hide a
    # here-document with an unquoted delimiter on a later line before a later '))': its expansion is named.
    # A $((...)) that spans lines is not passed over, so its '<<' is named (a note the command did not
    # earn). The operators are found without lexing: inside single quotes, as in 'x <<EOF', the closing
    # quote leaves no delimiter and none is named, while "x <<EOF y" in double quotes is named.
    reason = ("its delimiter is unquoted, so bash expands parameters, command substitutions and arithmetic in its "
              "body, and the command does not lex, so only its text as written was scanned")
    unquoted = {}
    for name, command in (("plain", "git commit -F - <<EOF\nsee tools/a.py:1\nEOF\n"),
                          ("dollar", "git commit -F - <<EOF\nsee $(printf tools/a.py):1\nEOF\n"),
                          ("dash", "git commit -F - <<-EOF\n\tsee tools/a.py:1\n\tEOF\n"),
                          ("split", "git commit -F - <\\\n<EOF\nsee tools/a.py:1\nEOF\n"),
                          ("two", "git commit -F - <<A <<B\nsee tools/a.py:1\nA\nsee tools/a.py:2\nB\n"),
                          ("here-string", "git commit -F - <<< 'see tools/a.py:1'"),
                          ("quoted", "cat <(true); git commit -F - <<'EOF'\nsee tools/a.py:1\nEOF\n"),
                          ("arith", "x=$((1<<2)); git commit -F - <<A\nsee tools/a.py:1\nA\n"),
                          ("arith-subshell", "echo $((cat <<B\nx\nB\n) ); git commit -F - <<A\nsee tools/a.py:1\nA\n"),
                          ("arith-comment", "# $((x\ngit commit -F - <<A\nsee tools/${F}.py:1\nA\n# ))"),
                          ("arith-quoted-body", "cat <<'Q'\nuse $((n\nQ\ngit commit -F - <<A\nsee tools/${F}.py:1\n"
                                                "A\n# ))"),
                          ("arith-lines", "x=$((1 <<A\n)); git commit -m 'see tools/a.py:1'"),
                          ("in-single-quotes", "git commit -m 'see tools/a.py:1 <<EOF' <(true)"),
                          ("in-double-quotes", "git commit -m \"see tools/a.py:1 <<EOF y\" <(true)")):
        verdict, note, rows = _citecheck_run(steps.root, command, "unquoted-" + name)
        unquoted[name] = (verdict, [(r["detail"]["parse"], [c["cite"] for c in r["detail"]["citations"]],
                                     r["detail"]["sources"], r["detail"]["sources_unread"]) for r in rows],
                          "The git commit message expanded <<" in note)

    def named(*delims):
        return ["expanded <<{} here-document: {}".format(delim, reason) for delim in delims]
    check("citeledger/check-unquoted-heredoc", unquoted,
          {"plain": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("EOF"))], True),
           "dollar": ("warn", [("raw", [], ["command text"], named("EOF"))], True),
           "dash": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("EOF"))], True),
           "split": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("EOF"))], True),
           "two": ("warn", [("raw", ["tools/a.py:1", "tools/a.py:2"], ["command text"], named("A", "B"))], True),
           "here-string": ("warn", [("raw", ["tools/a.py:1"], ["command text"], [])], False),
           "quoted": ("warn", [("raw", ["tools/a.py:1"], ["command text"], [])], False),
           "arith": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("A"))], True),
           "arith-subshell": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("B", "A"))], True),
           "arith-comment": ("warn", [("raw", [], ["command text"], named("A"))], True),
           "arith-quoted-body": ("warn", [("raw", [], ["command text"], named("A"))], True),
           "arith-lines": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("A"))], True),
           "in-single-quotes": ("warn", [("raw", ["tools/a.py:1"], ["command text"], [])], False),
           "in-double-quotes": ("warn", [("raw", ["tools/a.py:1"], ["command text"], named("EOF"))], True)})
    # A here-document delimiter that a backslash-newline splits is the word bash reads once it is removed:
    # <<E\, a newline and OF, <<\, a newline and EOF, and <<-\, a newline and EOF are the unquoted EOF, so
    # bash expands the body, and the command is scanned as one that does not lex, with that expansion named,
    # with or without a $ in the body. The lexer reads each split delimiter at an operator it reads as one
    # that holds a newline, which no line matches, so it would take the rest of the command as that body: a
    # later command is scanned too, and so is one after a split delimiter that bash reads as quoted (<<E\, a
    # newline and 'OF'; <<$\, a newline and 'EOF'; <<"E"\, a newline and OF; <<"E\, a newline and OF"), for
    # which no expansion is named, or after a split '<<' inside $((...)), which the lexer reads as an
    # operator and bash as a shift. Inside single quotes bash keeps the backslash-newline and also runs the
    # body to the end, so the later command scanned there is a note the command did not earn. A here-document
    # inside a command substitution in double quotes, $(...) or backticks, is real: bash expands its body,
    # which the lexer reads as text, so with a split delimiter that bash reads as unquoted the command is
    # scanned as written and that expansion named, also on the line after a comment in the substitution, after
    # a '#' inside a word (x#y, ${#v}, $(true)#z), which begins no comment, and in backticks after a comment
    # that the closing backtick ends. One that bash reads as quoted (also with backslash-newlines earlier in
    # the string, which the offset of the '<<' in the string without them allows for), one whose delimiter word
    # runs to the string's closing quote (at a closing backtick bash reads an unquoted delimiter with an empty
    # body, so nothing is expanded), and a '<<' inside single quotes, an ANSI-C quote with an escaped quote, a
    # comment (from a '#' at the substitution's opening or after a blank, to its newline) or $((...)) in such a
    # substitution, leaves the command lexed with its -F file read. A look-alike the lexer does not read as an
    # operator, inside quotes (after a substitution closes, too), a comment or a quoted here-document's body
    # (also one that, with '<(' for its '<<', would be the line that closes that body), or after an escaped
    # '<', leaves the command lexed, so its -F file (after the escaped '<', its -m value) is read, and so do
    # nine of them.
    split, commands, later = {}, {}, "git commit -m 'see tools/a.py:1'\n"
    for name, command in (("mid", "git commit -F - <<E\\\nOF\nsee tools/a.py:1\nEOF\n"),
                          ("mid-dollar", "git commit -F - <<E\\\nOF\nsee $(printf tools/a.py):1\nEOF\n"),
                          ("lead", "git commit -F - <<\\\nEOF\nsee tools/a.py:1\nEOF\n"),
                          ("dash", "git commit -F - <<-\\\nEOF\n\tsee tools/a.py:1\n\tEOF\n"),
                          ("later", "cat <<E\\\nOF\nx\nEOF\n" + later),
                          ("quoted", "git commit -F - <<E\\\n'OF'\nsee tools/a.py:1\nEOF\n"),
                          ("later-quoted", "cat <<E\\\n'OF'\nx\nEOF\n" + later),
                          ("later-ansi-c", "cat <<$\\\n'EOF'\nx\nEOF\n" + later),
                          ("later-quoted-part", "cat <<\"E\"\\\nOF\nx\nEOF\n" + later),
                          ("later-in-double-quotes", "cat <<\"E\\\nOF\"\nx\nEOF\n" + later),
                          ("later-arith", "echo $((x<<\\\n2))\n" + later),
                          ("later-in-single-quotes", "cat <<'E\\\nOF'\nx\nEOF\n" + later),
                          ("look-alike-single", "git commit -F msg.txt; echo 'x <<E\\\nOF y'"),
                          ("look-alike-double", "git commit -F msg.txt; echo \"x <<E\\\nOF y\""),
                          ("look-alike-comment", "git commit -F msg.txt # x <<E\\\nOF\n"),
                          ("look-alike-body", "git commit -F msg.txt; cat <<'Q'\nx <<E\\\nOF y\nQ\n"),
                          ("look-alike-escaped", "echo a\\<<E\\\nOF\n" + later),
                          ("look-alike-collision", "git commit -F msg.txt; cat <<'x <(E\\'\nx <<E\\\nOF\nx <(E\\\n"),
                          ("look-alike-after-subst", "git commit -F msg.txt; echo \"$(date) x <<E\\\nOF y\""),
                          ("look-alike-many", "git commit -F msg.txt; echo" + " \"x <<E\\\nOF y\"" * 9),
                          ("subst-double", "git commit -m \"$(cat <<E\\\nOF\nsee tools/${F}.py:1\nEOF\n)\""),
                          ("backticks-double", "git commit -m \"`cat <<E\\\nOF\nsee tools/${F}.py:1\nEOF\n`\""),
                          ("subst-quoted", "git commit -F msg.txt; echo \"$(cat <<E\\\n'OF'\nx\nEOF\n)\""),
                          ("subst-single", "git commit -F msg.txt; echo \"$(echo 'x <<E\\\nOF y')\""),
                          ("subst-arith", "git commit -F msg.txt; echo \"$((x<<\\\n2))\""),
                          ("subst-comment", "git commit -F msg.txt; echo \"$( # <<E\\\n# OF\n: )\""),
                          ("subst-comment-after", "git commit -F msg.txt; echo \"$(true # x <<E\\\nOF\n)\""),
                          ("subst-comment-line", "git commit -m \"$(echo a # x\ncat <<E\\\nOF\nsee tools/${F}.py:1\n"
                                                 "EOF\n)\""),
                          ("subst-hash-word", "git commit -m \"$(: x#y ${#v} $(true)#z; cat <<E\\\nOF\n"
                                              "see tools/${F}.py:1\nEOF\n)\""),
                          ("backticks-comment", "git commit -m \"`echo a # x` `cat <<E\\\nOF\nsee tools/${F}.py:1\n"
                                                "EOF\n`\""),
                          ("subst-ansi-c", "git commit -F msg.txt; echo \"$(echo $'x\\' <<E\\\nOF y')\""),
                          ("subst-joins-before", "git commit -F msg.txt; echo \"a\\\nb\\\nc\\\n $(cat <<E\\\n'OF'\n"
                                                 "abc ${HOME}\nEOF\n)\""),
                          ("backticks-to-quote", "git commit -F msg.txt; echo \"`cat <<E\\\nOF`\"")):
        commands[name] = command
        verdict, note, rows = _citecheck_run(steps.root, command, "split-delim-" + name)
        split[name] = (verdict, [(r["detail"]["parse"], [c["cite"] for c in r["detail"]["citations"]],
                                  r["detail"]["sources"], r["detail"]["sources_unread"]) for r in rows],
                       "The git commit message expanded <<" in note)
    by_raw = [("raw", ["tools/a.py:1"], ["command text"], named("EOF"))]
    by_quoted = [("raw", ["tools/a.py:1"], ["command text"], [])]
    by_file = ("warn", [("lexed", ["tools/a.py:1"], ["-F msg.txt"], [])], False)
    by_text = ("warn", [("raw", [], ["command text"], named("EOF"))], True)
    check("citeledger/check-split-heredoc-delim", split,
          {"mid": ("warn", by_raw, True), "mid-dollar": ("warn", [("raw", [], ["command text"], named("EOF"))], True),
           "lead": ("warn", by_raw, True), "dash": ("warn", by_raw, True), "later": ("warn", by_raw, True),
           "quoted": ("warn", by_quoted, False), "later-quoted": ("warn", by_quoted, False),
           "later-ansi-c": ("warn", by_quoted, False), "later-quoted-part": ("warn", by_quoted, False),
           "later-in-double-quotes": ("warn", by_quoted, False), "later-arith": ("warn", by_quoted, False),
           "later-in-single-quotes": ("warn", by_quoted, False), "look-alike-single": by_file,
           "look-alike-double": by_file, "look-alike-comment": by_file, "look-alike-body": by_file,
           "look-alike-escaped": ("warn", [("lexed", ["tools/a.py:1"], ["-m value"], [])], False),
           "look-alike-collision": by_file, "look-alike-after-subst": by_file, "look-alike-many": by_file,
           "subst-double": ("warn", [("raw", [], ["command text"], named("EOF"))], True),
           "backticks-double": ("warn", [("raw", [], ["command text"], named("EOF"))], True),
           "subst-quoted": by_file, "subst-single": by_file, "subst-arith": by_file, "subst-comment": by_file,
           "subst-comment-after": by_file, "subst-comment-line": by_text, "subst-hash-word": by_text,
           "backticks-comment": by_text, "subst-ansi-c": by_file, "subst-joins-before": by_file,
           "backticks-to-quote": by_file})
    # Each rule of the walk through a double-quoted substitution and of the split-delimiter decision decides
    # its vector above: under a variant of the hook with that rule removed (_hook_variant: one line of
    # _citechk_substituted_ops or _citechk_split_delim replaced) the vector gives the other result pinned here.
    # The rules: a substitution ends at its ')'; single quotes, an ANSI-C quote and $((...)) in it; a comment
    # in it, which begins at a '#' that begins a word and ends at its newline or, in backticks, at the closing
    # one; the joined delimiter read as quoted; the offset of the '<<' past earlier backslash-newlines; and a
    # delimiter word that runs to the string's closing quote. Four of these vectors (after the substitution's
    # close, in single quotes, in $((...)) and with a quoted joined delimiter) give their result above under a
    # hook that finds split delimiters by lexing the command again, so alone they are continuity coverage; the
    # variants are what show that each rule decides them.
    walk, decide = "_citechk_substituted_ops", "_citechk_split_delim"
    texts, base, variants = _hook_functions(walk, decide), tmp / "citecheck-variants", {}
    base.mkdir()
    for name, func, old, new in (("look-alike-after-subst", walk, 'elif opened[-1] != "`":', "elif False:"),
                                 ("subst-single", walk, 'if opened[-1] != "$((":  # single', "if False:  # single"),
                                 ("subst-arith", walk, 'if command.startswith("((", k + 1):', "if False:"),
                                 ("subst-quoted", decide, "if joined is not None and not joined[0]:",
                                  "if joined is not None:"),
                                 ("subst-comment", walk, 'elif c == "#":', "elif False:"),
                                 ("subst-comment-after", walk, 'elif c == "#":', "elif False:"),
                                 ("subst-comment-line", walk, 'close = command.find("\\n", k, last)', "close = -1"),
                                 ("subst-hash-word", walk, 'if opened[-1] != "$((" and at != -1 and (',
                                  'if opened[-1] != "$((" and (True or '),
                                 ("backticks-comment", walk, "if tick:  # bash ends", "if False:  # bash ends"),
                                 ("subst-ansi-c", walk, "elif opened and opened[-1] != \"$((\" and "
                                                        "command.startswith(\"'\", k + 1):", "elif False:"),
                                 ("subst-joins-before", decide, "at - start - 2 * j + 2", "at - start + 2"),
                                 ("backticks-to-quote", decide, "parsed[3] == end - 1 or ", "")):
        variant = _hook_variant(base, func, texts.get(func, ""), old, new)
        if variant is None:
            variants[name] = "no variant: {!r} does not occur exactly once in {}".format(old, func)
            continue
        original = getattr(aiqt_hooks, func)
        setattr(aiqt_hooks, func, variant)
        try:
            verdict, note, rows = _citecheck_run(steps.root, commands[name], "split-variant-" + name)
        finally:
            setattr(aiqt_hooks, func, original)
        variants[name] = (verdict, [(r["detail"]["parse"], [c["cite"] for c in r["detail"]["citations"]],
                                     r["detail"]["sources"], r["detail"]["sources_unread"]) for r in rows],
                          "The git commit message expanded <<" in note)
    by_scan = ("allow", [("raw", [], ["command text"], [])], False)
    by_value = ("allow", [("lexed", [], ["-m value"], [])], False)
    check("citeledger/check-split-heredoc-variants", variants,
          {"look-alike-after-subst": by_text, "subst-single": by_text, "subst-arith": by_scan,
           "subst-quoted": by_scan, "subst-comment": ("warn", [("raw", [], ["command text"], named("E#"))], True),
           "subst-comment-after": by_text, "subst-comment-line": by_value, "subst-hash-word": by_value,
           "backticks-comment": by_value, "subst-ansi-c": by_text, "subst-joins-before": by_scan,
           "backticks-to-quote": by_scan})
    # A command after the reserved words that begin its segment is read: in a brace group, after '!', 'if'
    # or 'then', after 'do', after 'time -p', after a '--' that follows time or its -p, and after coproc,
    # with the NAME bash reads before a compound command. Where bash runs another command, none is read: a
    # -p after time's '--' is that command, and so is the word after coproc where a simple command follows
    # it. Standard input that a brace group, a subshell or a loop is given as a whole is not the command's
    # own here-document, so it is named as not read; a here-document on the command itself inside a group is
    # read.
    grouped = {}
    for name, command in (("brace-stdin", "{ git commit -F -; } <<'A'\nsee tools/a.py:1\nA\n"),
                          ("brace-own", "{ git commit -F - <<'A'\nsee tools/a.py:1\nA\n}\n"),
                          ("brace-m", "{ git commit -m 'see tools/a.py:1'; }"),
                          ("subshell", "( git commit -F - ) <<'A'\nsee tools/a.py:1\nA\n"),
                          ("loop", "while read -r x; do git commit -F -; done <<'A'\nsee tools/a.py:1\nA\n"),
                          ("bang", "! git commit -m 'see tools/a.py:1'"),
                          ("if-then", "if git commit -m 'see tools/a.py:1'; then git commit -m 'see tools/a.py:2';"
                                      " fi"),
                          ("time", "time -p git commit -m 'see tools/a.py:1'"),
                          ("time-dashdash", "time -- git commit -m 'see tools/a.py:1'"),
                          ("time-p-dashdash", "time -p -- git commit -m 'see tools/a.py:1'"),
                          ("time-dashdash-p", "time -- -p git commit -m 'see tools/a.py:1'"),
                          ("coproc", "coproc git commit -m 'see tools/a.py:1'"),
                          ("coproc-name", "coproc NAME { git commit -m 'see tools/a.py:1'; }"),
                          ("coproc-name-simple", "coproc NAME git commit -m 'see tools/a.py:1'")):
        verdict, _note, rows = _citecheck_run(steps.root, command, "grouped-" + name)
        grouped[name] = (verdict, [([c["cite"] for c in r["detail"]["citations"]], r["detail"]["sources"],
                                    r["detail"]["sources_unread"]) for r in rows])
    by_m = [(["tools/a.py:1"], ["-m value"], [])]
    check("citeledger/check-command-in-group", grouped,
          {"brace-stdin": ("warn", [([], [], unseen)]), "brace-own": ("warn", own), "brace-m": ("warn", by_m),
           "subshell": ("warn", [([], [], unseen)]), "loop": ("warn", [([], [], unseen)]), "bang": ("warn", by_m),
           "if-then": ("warn", by_m + [(["tools/a.py:2"], ["-m value"], [])]), "time": ("warn", by_m),
           "time-dashdash": ("warn", by_m), "time-p-dashdash": ("warn", by_m), "time-dashdash-p": ("allow", []),
           "coproc": ("warn", by_m), "coproc-name": ("warn", by_m), "coproc-name-simple": ("allow", [])})
    # The scan's deadline: line 2's match of the search's heading pattern, or of the masking's fence
    # pattern, moves a stand-in clock to just inside, at or just past the deadline. Inside, line 3 (the
    # second citation) is scanned too (three matches) and both ranges judged; at and past it the scan stops
    # after line 2 (two matches), the scan comes before the toplevel probe, so that probe is not started,
    # and the note says no citation was checked, naming the budget (no row, with no state directory known).
    command = "git commit -m 'see tools/a.py:1\nx\nsee tools/a.py:2'"
    scanned = {}
    for loop, attr in (("search", "_CITECHK_HEADING"), ("masking", "_CITECHK_FENCE")):
        for name, offset in (("inside", -0.001), ("at", 0.0), ("outside", 0.001)):
            clock = _CiteClock(1000.0, 1000.0 + budget + offset, False)
            saved_pattern, pattern = getattr(aiqt_hooks, attr), None
            try:
                aiqt_hooks.time, aiqt_hooks._recovery_toplevel = clock, lambda *args, **kwargs: str(steps.root)
                aiqt_hooks._review_git = lambda *args, **kwargs: None
                pattern = _CiteCutPattern(saved_pattern, clock, 2)
                setattr(aiqt_hooks, attr, pattern)
                _verdict_, note, rows = _citecheck_run(steps.root, command, "scan-{}-{}".format(loop, name))
            finally:
                aiqt_hooks.time, aiqt_hooks._recovery_toplevel, aiqt_hooks._review_git = saved
                setattr(aiqt_hooks, attr, saved_pattern)
            scanned.setdefault(loop, []).append((
                pattern.calls, [c["class"] for r in rows for c in r["detail"]["citations"]],
                [r["decision"] for r in rows], "budget before it resolved the repository" in note))
    late = [(2, [], [], True)] * 2
    check("citeledger/check-scan-deadline", scanned,
          {"search": [(3, ["unread", "unread"], ["warn"], False)] + late,
           "masking": [(3, ["unread", "unread"], ["warn"], False)] + late})


def _cite_unpaired(roadmap):
    """The corpus ids of the rules of a parsed enforcement roadmap that name hook:citation-read-ledger without
    hook:citation-read-check. The recorder checks no citation on its own, so such a rule would read enforced
    in the register while nothing reads the ledger."""
    return sorted(rule.get("corpus-id") for rule in roadmap.get("rule", [])
                  if "hook:citation-read-ledger" in rule.get("mechanisms", [])
                  and "hook:citation-read-check" not in rule.get("mechanisms", []))


def _cite_roadmap_cases(tmp):
    """citint and refcap read enforced through the citation hooks only as a pair: the roadmap names the
    recorder for them, and never without the check that reads its ledger. The same test, run on the roadmap
    with the check taken out, names both rules, so it fails on an unpaired roadmap."""
    text = (repo_root() / ".aiqt" / "core" / "enforcement-roadmap.toml").read_text(encoding="utf-8")
    try:
        roadmap, alone = tomllib.loads(text), tomllib.loads(text.replace('"hook:citation-read-check", ', ""))
    except (ValueError, RecursionError, MemoryError) as exc:  # an unparseable roadmap fails the check below
        roadmap = alone = {"unparseable": type(exc).__name__}
    recorded = sorted(rule.get("corpus-id") for rule in roadmap.get("rule", [])
                      if "hook:citation-read-ledger" in rule.get("mechanisms", []))
    check("citeledger/roadmap-pairs-recorder-with-check",
          (recorded, _cite_unpaired(roadmap), _cite_unpaired(alone)),
          (["citint", "refcap"], [], ["citint", "refcap"]))


def _cite_pin_state(tmp):
    """Pin the citation cases' state inside tmp. None of their fixture repositories holds an orchestration
    registry, so a state directory resolves to $XDG_STATE_HOME/aiqt-guardrails/orch/<repository key>/:
    XDG_STATE_HOME is set to tmp/cite-xdg (a case that changes it restores it), and every state directory
    resolved until _cite_unpin_state is recorded. Returns what _cite_unpin_state restores and checks."""
    saved = (os.environ.get("XDG_STATE_HOME"), aiqt_hooks._orch_state_dir_for_root, [])

    def resolve(root):
        path = saved[1](root)
        saved[2].append(path)
        return path
    os.environ["XDG_STATE_HOME"] = str(tmp / "cite-xdg")
    aiqt_hooks._orch_state_dir_for_root = resolve
    return saved


def _cite_unpin_state(tmp, saved):
    """Undo _cite_pin_state, and check that the citation cases resolved a state directory and that every
    one lies inside tmp, so a ledger or guard-events row that would land outside it fails a check instead
    of being written there unseen."""
    saved_xdg, aiqt_hooks._orch_state_dir_for_root, resolved = saved
    if saved_xdg is None:
        os.environ.pop("XDG_STATE_HOME", None)
    else:
        os.environ["XDG_STATE_HOME"] = saved_xdg
    inside = os.path.realpath(str(tmp)) + os.sep
    check("citeledger/state-dir-inside-tmp",
          (bool(resolved), sorted({path for path in resolved if not os.path.realpath(path).startswith(inside)})),
          (True, []))


def _single_quoted(text):
    """text as one single-quoted shell word."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


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
        # inert BY DEFAULT when the orchestration registry is absent: a foreground bare-& acquires no new prompt.
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
        # registry file the guard is inert (malformed input
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
        # unrecorded; FIX 5 records it.
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
        _rdp_cases(tmp)
        pinned = _cite_pin_state(tmp)
        try:
            _citeledger_cases(tmp)
            _citeledger_show_cases(tmp)
            _citeledger_edit_cases(tmp)
            _citeledger_scope_cases(tmp)
            _citeledger_dedup_cases(tmp)
            _citeledger_bound_cases(tmp)
            _citeledger_deadline_cases(tmp)
            _citeledger_timing_cases(tmp)
            _citecheck_cases(tmp)
            _citecheck_show_cases(tmp)
            _citecheck_deadline_cases(tmp)
            _citecheck_path_cases(tmp)
            _citeledger_walk_cases(tmp)
            _citecheck_step_cases(tmp)
            _cite_roadmap_cases(tmp)
        finally:
            _cite_unpin_state(tmp, pinned)
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
          "foreground bare-& detach (historically an ASK for both) while dropping a word-start `#` comment "
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
