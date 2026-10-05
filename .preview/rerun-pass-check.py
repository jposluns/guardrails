#!/usr/bin/env python3
"""PostToolUse, PostToolUseFailure and Stop hook (rerun-pass-check): a rerun pass is not conclusive verification.

WHAT IT DOES
    A check that fails and then passes on a rerun with no deliberate change in between is an unresolved
    intermittent result: the earlier failure is recorded and investigated, and the later pass is not presented
    as conclusive verification (rerun-pass-is-still-failure). This hook watches for the two common shapes and
    keeps the earlier failure in view:

    1. A CI RERUN. A shell command that reruns a CI run (`gh run rerun`, `glab ci retry`) is a rerun by
       definition. After it runs, the hook adds a note to the assistant's context: the rerun's result does not
       erase the earlier failure, which is to be recorded and investigated.
    2. A LOCAL RERUN. A recognized check command (CHECK_RE: a test runner, `make test` or `make check`, a
       `--self-test` or `--check` run) that failed and then, run again with the identical command text, passed,
       with no change recorded between the two runs. After the passing run, the hook adds the same note.
    3. AT TURN END (Stop), while such a rerun is outstanding, a final message that presents a pass as
       conclusive (CONCLUSIVE_RE: "all tests pass", "CI is green", "verified", and similar) without naming the
       earlier failure (DISCLOSED_RE: "flaky", "intermittent", "rerun", "earlier failure", and similar) is
       refused once, with the reason. A final message that names it clears the outstanding reruns. A loop cap
       bounds the refusals: inside one continuous stop_hook_active run at most BLOCK_CAP, then the stop is
       allowed with a one-line warning.

    A CHANGE between two runs is any Write, Edit, MultiEdit, or NotebookEdit call, and any other shell command
    that is not on the short read-only list (READ_ONLY: cat, ls, grep, git status, git diff, gh run view, and
    similar), so an install, a checkout, or a sed between the runs counts as a deliberate change and no rerun
    is flagged. A failed run is a PostToolUseFailure call, or a PostToolUse call whose tool_response reports
    an interruption or a nonzero exit code field; any other PostToolUse call is a pass.

    Events: PostToolUse and PostToolUseFailure (matcher Bash|Write|Edit|MultiEdit|NotebookEdit), and Stop.
    Output: nothing, or ONE line of JSON on stdout: a hookSpecificOutput additionalContext note (after a tool
    call), a top-level decision "block" object with a reason, or a top-level systemMessage warning (Stop).
    Exit status: always 0. No configuration is needed. State: one JSON file per session (named by a SHA-256 of
    session_id, else transcript_path) in AIQT_HOOK_STATE_DIR/rerun-pass-check when that is an absolute path,
    else $XDG_STATE_HOME/aiqt-guardrails/rerun-pass-check, else $HOME/.local/state/aiqt-guardrails/
    rerun-pass-check (created 0700, written by an atomic replace).

FAILURE DIRECTION
    After a tool call the safe direction is to inform: a rerun the hook recognizes is noted even when its state
    cannot be saved. At Stop the safe direction is not to hold the session on a guess: with no readable state,
    no final message in the payload (last_assistant_message), or an unknown or unsaveable refusal count under
    stop_hook_active, the stop is allowed. Any error, an unreadable payload, or an unrecognized event exits 0
    with no output. A worker process (AIQT_HOOKS_WORKER=1, or a legacy spelling) is skipped.

RESIDUAL COVERAGE
    It recognizes only the listed CI rerun commands and check commands, run through the shell tool; a rerun
    through a web page, a pushed empty commit, a retry option of the runner itself (pytest --reruns, a CI
    retry setting), a script that wraps the check, or a changed command line (an added flag, another order)
    is not seen. A change made outside the tool calls it sees (by the user, another process, a background job)
    is not seen, so a fix applied that way reads as no change and the pass is flagged anyway (a false note); a
    read-only-looking command with a side effect reads as no change. The pass and fail reading rests on the
    event name and a few tool_response fields, not on the command's output. The Stop check reads only the
    final message, by fixed phrase lists: a conclusive claim worded otherwise passes, and a message naming any
    disclosure word passes and clears the reruns, whether or not it records the failure. It does not record
    or investigate the failure itself. Concurrent hook runs in one session can lose a state update. State
    keeps at most MAX_CHECKS commands and MAX_FLAGS outstanding reruns.

Self-test: python3 -I -S -B rerun-pass-check.py --self-test
"""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile

HOOK = "rerun-pass-check"
STATE_MAX_BYTES = 1 << 18
BLOCK_CAP = 2  # refusals per continuous stop_hook_active run before a Stop is allowed with a warning
MAX_CHECKS = 200
MAX_FLAGS = 20
MAX_SHOWN = 160  # characters of a command shown in a note
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
CI_RERUN_RE = re.compile(r"(?:^|[\s;&|(])(?:gh\s+run\s+rerun|glab\s+ci\s+retry)\b")
CHECK_RE = re.compile(
    r"(?:^|[\s;&|(/])(?:pytest|py\.test|tox|nox|jest|vitest|mocha|ctest|phpunit|rspec)(?:\s|$)"
    r"|\bpython[0-9.]*\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check)\b"
    r"|\b(?:go|cargo)\s+test\b|\b(?:make|gmake|just)\s+(?:\S+\s+)*(?:test|tests|check)(?:\s|$)"
    r"|(?:^|[\s;&|(])(?:mvn|gradle|\./gradlew)\s+(?:\S+\s+)*test\b|\s--self-test\b|\s--check\b")
CONCLUSIVE_RE = re.compile(
    r"\ball (?:the )?(?:tests|checks|gates)(?: now)? (?:pass|passed|passing|are passing|are green)\b"
    r"|\b(?:tests|checks|gates|suite|build|pipeline|ci)(?: now)? (?:passes|passed|pass|is green|are green|is passing)\b"
    r"|\b(?:ci|build|pipeline) (?:is )?(?:green|passing)\b|\ball green\b|\bverified\b"
    r"|\bconfirmed (?:working|passing)\b",
    re.I)
DISCLOSED_RE = re.compile(
    r"\bflak(?:y|e|es|iness)\b|\bintermittent(?:ly)?\b|\bre-?run\b|\bre-?ran\b|\bretr(?:y|ied)\b"
    r"|\bearlier fail(?:ure|ed)?\b|\bfirst (?:run|attempt) fail(?:ed|s)?\b|\bfailed (?:once|first|initially)\b",
    re.I)
READ_ONLY = frozenset(("cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "pwd",
                       "echo", "printf", "wc", "date", "sleep", "true", "file", "stat", "which", "type", "diff",
                       "cmp", "sha256sum", "md5sum", "du", "df", "env", "id", "whoami", "uname", "jq", "tree"))
READ_ONLY_GIT = frozenset(("status", "log", "diff", "show", "rev-parse", "blame", "ls-files"))
READ_ONLY_GH = (("run", "view"), ("run", "list"), ("run", "watch"), ("pr", "view"), ("pr", "checks"))


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings


def state_path(payload, env):
    sid = payload.get("session_id")
    tp = payload.get("transcript_path")
    if isinstance(sid, str) and sid:
        key = "s:" + sid
    elif isinstance(tp, str) and tp:
        key = "t:" + tp
    else:
        return None
    v = _cfg("HOOK_STATE_DIR", env)
    x = env.get("XDG_STATE_HOME")
    h = env.get("HOME")
    if v and os.path.isabs(v):
        d = os.path.join(v, HOOK)
    elif x and os.path.isabs(x):
        d = os.path.join(x, "aiqt-guardrails", HOOK)
    elif h and os.path.isabs(h):
        d = os.path.join(h, ".local", "state", "aiqt-guardrails", HOOK)
    else:
        return None
    return os.path.join(d, hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32] + ".json")


def new_state():
    return dict(change=0, checks=dict(), flags=[], blocks=0)


def load_state(path):
    """(state, readable): an absent file is a fresh state; an unreadable or malformed one is (fresh, False)."""
    if path is None:
        return new_state(), False
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return new_state(), True
    except OSError:
        return new_state(), False
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return new_state(), False
            obj = json.loads(fh.read(STATE_MAX_BYTES + 1)[:STATE_MAX_BYTES])
        ok = (isinstance(obj, dict) and type(obj.get("change")) is int and isinstance(obj.get("checks"), dict)
              and isinstance(obj.get("flags"), list) and type(obj.get("blocks")) is int)
        return (obj, True) if ok else (new_state(), False)
    except Exception:
        return new_state(), False


def save_state(path, state):
    """Write the state by an atomic replace; True on success, False on any failure (never raises)."""
    if path is None:
        return False
    tmp = None
    try:
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def read_only(cmd):
    """True when every simple command in `cmd` starts with a word on the read-only list."""
    for seg in re.split(r"[;&|\n()]+", cmd):
        words = seg.split()
        if not words:
            continue
        w = os.path.basename(words[0])
        if w in READ_ONLY:
            continue
        if w == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
            continue
        if w == "gh" and tuple(words[1:3]) in READ_ONLY_GH:
            continue
        return False
    return True


def failed(event, response):
    if event == "PostToolUseFailure":
        return True
    if isinstance(response, dict):
        if response.get("interrupted") is True:
            return True
        for k in ("exit_code", "exitCode", "returncode", "returnCode"):
            v = response.get(k)
            if type(v) is int and v != 0:
                return True
    return False


def shown(cmd):
    one = " ".join(cmd.split())
    return one if len(one) <= MAX_SHOWN else one[:MAX_SHOWN] + "..."


def note(event, what):
    return dict(hookSpecificOutput=dict(hookEventName=event, additionalContext=(
        "RERUN NOTE (rerun-pass-check hook): " + what + " A pass on a rerun with no deliberate change in "
        "between does not erase the earlier failure: record the failure, investigate it as an intermittent "
        "result, and do not present the later pass as conclusive verification.")))


def after_tool(payload, state, event):
    """Update `state` for one finished tool call; return the note text, or None."""
    tool = payload.get("tool_name")
    if tool in EDIT_TOOLS:
        state["change"] += 1
        return None
    if tool != "Bash":
        return None
    ti = payload.get("tool_input")
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    bad = failed(event, payload.get("tool_response"))
    if CI_RERUN_RE.search(cmd):
        state["change"] += 1
        if bad:
            return None
        state["flags"] = (state["flags"] + ["CI rerun: " + shown(cmd)])[-MAX_FLAGS:]
        return "a CI rerun was started (" + shown(cmd) + ")."
    if not CHECK_RE.search(cmd):
        if not read_only(cmd):
            state["change"] += 1
        return None
    key = " ".join(cmd.split())
    checks = state["checks"]
    prior = checks.pop(key, None)
    if bad:
        checks[key] = ["fail", state["change"]]
    else:
        checks[key] = ["pass", state["change"]]
    while len(checks) > MAX_CHECKS:
        checks.pop(next(iter(checks)))
    if not bad and isinstance(prior, list) and len(prior) == 2 and prior[0] == "fail" \
            and prior[1] == state["change"]:
        state["flags"] = (state["flags"] + ["local rerun: " + shown(cmd)])[-MAX_FLAGS:]
        return "the check `" + shown(cmd) + "` failed earlier and passed on a rerun with no change recorded between."
    return None


def at_stop(payload, state, readable, path):
    if not readable or not state["flags"]:
        return None
    msg = payload.get("last_assistant_message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    if DISCLOSED_RE.search(msg):
        state["flags"] = []
        state["blocks"] = 0
        save_state(path, state)
        return None
    if not CONCLUSIVE_RE.search(msg):
        return None
    active = payload.get("stop_hook_active") is True
    blocks = state["blocks"] if active else 0
    allowed = "rerun-pass-check: turn end allowed; the final message presents a pass as conclusive after a rerun"
    if blocks >= BLOCK_CAP:
        return dict(systemMessage=allowed + " (loop cap)")
    state["blocks"] = blocks + 1
    if not save_state(path, state) and active:
        return dict(systemMessage=allowed + " (refusal count cannot be saved)")
    return dict(decision="block", reason="rerun-pass-check: your final message presents a pass as conclusive, "
                "but this session saw a rerun pass after a failure (" + "; ".join(state["flags"][-3:]) + "). "
                "State the earlier failure and that it is unresolved (intermittent), and do not call the "
                "later pass conclusive (refusal " + str(blocks + 1) + " of at most " + str(BLOCK_CAP) + ").")


def decide(payload, env):
    event = payload.get("hook_event_name")
    path = state_path(payload, env)
    if event in ("PostToolUse", "PostToolUseFailure"):
        state, readable = load_state(path)
        what = after_tool(payload, state, event)
        save_state(path, state)  # a failed save still notes below
        return note(event, what) if what else None
    if event == "Stop":
        state, readable = load_state(path)
        return at_stop(payload, state, readable and path is not None, path)
    return None


def _emit_line(text):
    """Write one line to stdout; an output error is swallowed so the hook still exits 0."""
    try:
        print(text, flush=True)
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    if len(argv) > 1 and argv[1] == "--self-test":
        return _self_test()
    try:
        if _is_worker():
            return 0
        buf = getattr(sys.stdin, "buffer", None)
        payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        out = decide(payload, os.environ)
        if out is not None:
            _emit_line(json.dumps(out))
    except Exception:
        pass  # fail open: any error exits 0 with no output
    return 0


def _self_test():
    import shutil
    import subprocess
    import unittest

    here = os.path.abspath(__file__)

    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.mkdtemp(prefix="rerun-pass-check-test-")
            self.env = dict(AIQT_HOOK_STATE_DIR=os.path.join(self.tmp, "st"))

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)

        def bash(self, cmd, ok=True, **response):
            event = "PostToolUse" if ok else "PostToolUseFailure"
            return decide(dict(hook_event_name=event, session_id="s", tool_name="Bash",
                               tool_input=dict(command=cmd), tool_response=response), self.env)

        def edit(self):
            return decide(dict(hook_event_name="PostToolUse", session_id="s", tool_name="Edit",
                               tool_input=dict(file_path="/x")), self.env)

        def stop(self, msg, active=False):
            return decide(dict(hook_event_name="Stop", session_id="s", last_assistant_message=msg,
                               stop_hook_active=active), self.env)

        def test_01_local_rerun_is_noted(self):
            self.assertIsNone(self.bash("pytest -q tests", ok=False))
            self.assertIsNone(self.bash("git status"))
            out = self.bash("pytest  -q tests")
            self.assertIn("failed earlier and passed on a rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")

        def test_02_a_change_between_is_not_a_rerun(self):
            for change in (self.edit, lambda: self.bash("sed -i s/a/b/ f.py"), lambda: self.bash("pip install x")):
                self.assertIsNone(self.bash("make test", ok=False))
                change()
                self.assertIsNone(self.bash("make test"))
            self.assertIsNone(self.stop("All tests pass."))

        def test_03_ci_rerun_is_noted(self):
            out = self.bash("gh run rerun 12345 --failed")
            self.assertIn("CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("gh run rerun 1", ok=False))

        def test_04_stop_refuses_a_conclusive_claim_with_a_cap(self):
            self.bash("gh run rerun 7")
            out = self.stop("CI is green now; the change is verified.")
            self.assertEqual(out["decision"], "block")
            n = 1
            for _ in range(10):
                out = self.stop("CI is green.", active=True)
                if "decision" not in out:
                    break
                n += 1
            self.assertEqual(n, BLOCK_CAP)
            self.assertIn("loop cap", out["systemMessage"])

        def test_05_disclosure_clears_and_other_messages_pass(self):
            self.bash("npm test", ok=False)
            self.bash("npm test")
            self.assertIsNone(self.stop("I looked at the logs."))
            self.assertIsNone(self.stop("Tests pass on the rerun, but the first run failed; this is flaky."))
            self.assertIsNone(self.stop("All tests pass."))

        def test_06_failure_fields_in_a_posttooluse_response(self):
            self.bash("cargo test", exit_code=1)
            self.assertIsNotNone(self.bash("cargo test"))
            self.bash("go test ./...", interrupted=True)
            self.assertIsNotNone(self.bash("go test ./..."))

        def test_07_no_flag_no_state_no_message(self):
            self.assertIsNone(self.stop("All tests pass."))
            self.assertIsNone(self.bash("pytest"))
            self.assertIsNone(self.bash("pytest"))
            self.bash("tox", ok=False)
            self.bash("tox")
            self.assertIsNone(decide(dict(hook_event_name="Stop", session_id="s"), self.env))
            self.assertIsNone(decide(dict(hook_event_name="Stop", session_id="other",
                                          last_assistant_message="All tests pass."), self.env))
            self.assertIsNone(decide(dict(hook_event_name="SessionStart", session_id="s"), self.env))

        def test_08_without_state_it_still_notes_but_never_blocks(self):
            self.env = dict()
            self.assertIsNotNone(self.bash("gh run rerun 9"))
            self.assertIsNone(self.stop("All checks pass."))

        def test_09_bad_state_allows_at_stop(self):
            self.bash("gh run rerun 9")
            path = state_path(dict(session_id="s"), self.env)
            with open(path, "w", encoding="ascii") as fh:
                fh.write("\x7bbroken")
            self.assertIsNone(self.stop("All checks pass."))

        def test_10_classifiers(self):
            for c in ("pytest", "python3 -m pytest -x", "python3 -B -m unittest", "make -C d check",
                      "python3 tools/x.py --self-test", "yarn run test", "./gradlew build test"):
                self.assertTrue(CHECK_RE.search(c), c)
            for c in ("ls tests", "echo test", "git commit -m test", "cat pytest.ini"):
                self.assertFalse(CHECK_RE.search(c), c)
            self.assertTrue(read_only("git log | head -5; ls && cat f"))
            self.assertFalse(read_only("ls; rm f"))
            self.assertTrue(CONCLUSIVE_RE.search("All the tests now pass."))
            self.assertFalse(CONCLUSIVE_RE.search("The tests still fail."))

        def run_hook(self, payload, env):
            base = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"))
            base.update(env)
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=payload, capture_output=True,
                               env=base, timeout=30)
            return p.returncode, p.stdout, p.stderr

        def test_11_process_contract(self):
            call = json.dumps(dict(hook_event_name="PostToolUse", session_id="p", tool_name="Bash",
                                   tool_input=dict(command="gh run rerun 5"))).encode()
            rc, out, err = self.run_hook(call, self.env)
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))
            self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])
            stop = json.dumps(dict(hook_event_name="Stop", session_id="p",
                                   last_assistant_message="All checks pass.")).encode()
            rc, out, err = self.run_hook(stop, self.env)
            self.assertEqual((rc, err, json.loads(out)["decision"]), (0, b"", "block"))
            for junk in (b"", b"\x7b", b"[]", b"\xff\xfe", b"null"):
                self.assertEqual(self.run_hook(junk, self.env), (0, b"", b""))
            self.assertEqual(self.run_hook(stop, dict(self.env, AIQT_HOOKS_WORKER="1")), (0, b"", b""))

        def test_12_source_house_rules(self):
            with open(here, "rb") as fh:
                src = fh.read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
