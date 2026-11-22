#!/usr/bin/env python3
"""Behavioural self-test for the git-side gate record (rule gatdis): the recorder, the pre-commit,
pre-merge-commit and pre-push verifiers and the installer, all in .aiqt/core/hooks/scripts/aiqt_hooks.py
(mode gate-record).

Every fixture is a throwaway repository under a temporary directory this run creates and removes, driven by
real git commands (git commit, git merge, git push) with the environment from tools/_git_fixture_env.py: every
inherited GIT_ variable dropped, HOME and XDG_CONFIG_HOME pointed at a scratch directory and the global and
system git configuration pinned to os.devnull, so no caller repository or configuration is read or changed.
Each fixture commits a copy of the hook source under test, so the installed stubs run the verifier from HEAD's
committed blob as they do in a real clone, and a two-set configuration (.aiqt/gate-record.toml: "full" and
"suite-x", each running a check.sh whose exit, signal, edit and sleep the row sets through FIXTURE_
variables).

Rows (the plan's section 7.2; exit 0 pass, 1 refused, 2 cannot evaluate):
  W1-W8  the recorder: pass, fail, a signal death, a check that moves the tree, an unstaged change and an
         untracked file (cannot evaluate, the check not run), caller GIT_DIR/GIT_INDEX_FILE ignored, and two
         recorders at once (two whole lines).
  V1-V16 the verifiers: missing, pass, failed, another tree (twice), git commit -a, another check set, a
         configuration lowered in the commit itself (read from HEAD), an unborn branch, an earlier failure on
         the same tree (refuses: orchestrator answer O6), a symbolic-link log, a group- or world-writable log,
         four malformed last lines, a forged log named in the environment (ignored), pre-push over several
         tips and a deletion, and pre-merge-commit on a clean merge.
  V17    a tree-moved record whose check exited 0 is no earlier failure (the restored tree's pass commits);
         its flip, a tree-moved record whose check also exited 1, still refuses.
  V18    a commit that changes the required set's argv commits, because the recorder runs HEAD's set; its
         flip, the same commit with the check failing, refuses.
  V19-V25 the binding class: an orphan branch that lowers the bar is
         refused at commit and at push; the check's process group is killed before the record is written
         (the flip pins residual R3, a writer that leaves the group); pre-push reads every worktree's log;
         the stub's verifier copy lives in the git directory; the lock wait and the check run are bounded;
         a symbolic-link aiqt directory is not followed; and the record binds the parent commit (another
         parent refuses, a check that moves HEAD records tree-moved). Each has its flip.
  V26    replace objects are off: a git replace of the configuration, the verifier or a pushed commit does
         not change what is recorded or judged. Each has its flip.
  V27    other substituted git state: a shallow file naming a pushed commit cannot evaluate, and a forged
         commit-graph parent does not prove a remote has a commit. Each has its flip.
  V28    a new branch or a tag at a commit the remote already has pushes with no record (read from the
         remote's own refs); a commit it lacks stays refused, also when a remote-tracking ref names it; an
         unreadable remote cannot evaluate; with no URL the refusal stands.
  V29    a URL ls-remote would read elsewhere (url.<base>.insteadOf, a remote named like the URL) cannot
         evaluate and pushes nothing. Its flip: an unrelated rewrite leaves a commit the remote has pushing.
  V30    the remote proof reads only the remote's branches and tags: a commit only a pull-request ref holds
         stays refused. Its flip: the same commit on a remote branch pushes.
  V31    a new branch at a shallow clone's boundary commit the remote has pushes. Its flip: once only a
         pull-request ref holds that commit, the push cannot evaluate.
  V32    the remote proof is not read through an ssh program the push chose (GIT_SSH_COMMAND): even a commit the
         remote has cannot evaluate. Its flip: over the plain URL, that commit pushes.
  V33    a program or setting that could deliver the push elsewhere than the proof reads cannot evaluate, and the
         unrecorded commit never reaches the remote: an ssh program or core.sshCommand that picks the repository by
         service, remote.<name>.receivepack, GIT_EXEC_PATH with another git-receive-pack, a transport helper URL,
         remote.<name>.vcs. Its flip: an unrelated setting leaves a commit the remote has pushing.
  V34    the stubs run the programs install recorded: a planted python3 on PATH or in GIT_EXEC_PATH, or a planted
         git there serving an accepting verifier, no longer skips the verifier; a recorded program that is missing
         cannot evaluate. Its flip: a recorded commit pushes past a planted python3 that would refuse.
  V35    a remote branch that is a symbolic ref to a pull-request ref is no proof. Its flip: a symbolic branch to a
         tag holding the commit leaves it pushing.
  X1-X4  the pinned residuals: --no-verify, core.hooksPath on the command line, a forged pass line, and git push
         --receive-pack on the command line delivering to another repository than its URL (the text guard denies
         the spellings it reads).
  I1-I6  the installer: install and re-run, core.hooksPath set, a symbolic-link hooks directory, a foreign
         pre-commit, the OPF pre-commit stub (refused, with the manual chaining step: orchestrator answer O1),
         and a missing configuration.
Each refusal row has a flip: the same fixture without the stubs (or without the refusing condition), where git
accepts the commit, merge or push (or the installer installs), so a refusal that never fires fails the row.

  selftest_gate_record.py [--execution-report ABS_PATH]
      exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_gate_record.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402

SUITE_ID = "gate-record-selftest"
ROOT = Path(__file__).resolve().parent.parent
HOOKS_REL = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
HOOKS_SRC = ROOT / HOOKS_REL
CHECKS_TOML = ROOT / "tools" / "selftest_checks.toml"
EXECUTED = []
_EXECUTED_SET = set()
FAILURES = []
HARNESS_ERRORS = []
ENV = None
TMP = None

CONFIG = """format-version = 1
pre-commit = "full"
pre-push = "full"

[[set]]
name = "full"
argv = ["sh", "check.sh"]

[[set]]
name = "suite-x"
argv = ["sh", "check.sh", "x"]
"""
LOWERED = CONFIG.replace('pre-commit = "full"', 'pre-commit = "suite-x"')
CHECK_SH = """#!/bin/sh
if [ -n "${FIXTURE_MARK:-}" ]; then : >"$FIXTURE_MARK"; fi
if [ -n "${FIXTURE_SLEEP:-}" ]; then sleep "$FIXTURE_SLEEP"; fi
if [ -n "${FIXTURE_EDIT:-}" ]; then printf 'edit\\n' >>a.txt; fi
if [ -n "${FIXTURE_SIGNAL:-}" ]; then kill -TERM $$; fi
if [ "${FIXTURE_WRITER:-}" = group ]; then ( sleep 2; cat "$FIXTURE_SAVED" >"$FIXTURE_LOG" ) & fi
if [ "${FIXTURE_WRITER:-}" = escape ]; then
    python3 -I -c 'import os, shutil, sys, time
if os.fork():
    os._exit(0)
os.setsid()
time.sleep(2)
shutil.copyfile(sys.argv[1], sys.argv[2])' "$FIXTURE_SAVED" "$FIXTURE_LOG"
fi
exit "${FIXTURE_EXIT:-0}"
"""
OPF_STUB = "#!/bin/sh\n# OPF pre-commit stub, written by opf/enforcement/precommit/install.sh (fixture copy).\nexit 0\n"


def check(name, got, want):
    if name in _EXECUTED_SET:
        HARNESS_ERRORS.append("duplicate check id " + name)
    else:
        _EXECUTED_SET.add(name)
        EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def run(argv, cwd, extra=None, data=None, timeout=300):
    env = dict(ENV)
    env.update(extra or {})
    return subprocess.run(argv, cwd=str(cwd), env=env, input=data, capture_output=True, text=True,
                          timeout=timeout, check=False)


def git(repo, *args, extra=None, data=None):
    return run(["git"] + list(args), repo, extra, data)


def git_out(repo, *args):
    done = git(repo, *args)
    if done.returncode != 0:
        raise RuntimeError("fixture git {} failed: {}".format(" ".join(args), done.stderr.strip()))
    return done.stdout.strip()


def gate(repo, *args, extra=None, data=None):
    return run([sys.executable, "-I", "-B", str(HOOKS_SRC), "gate-record"] + list(args), repo, extra, data)


def write(repo, rel, text):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def stage(repo, rel, text):
    write(repo, rel, text)
    git_out(repo, "add", "--", rel)


def log_path(repo):
    return Path(repo) / ".git" / "aiqt" / "gate-records.jsonl"


def records(repo):
    path = log_path(repo)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def head(repo):
    done = git(repo, "rev-parse", "-q", "--verify", "HEAD")
    return done.stdout.strip()


def commit(repo, message, *args):
    return git(repo, "commit", "-q", "-m", message, *args)


def stub_rc(repo, hook="pre-commit"):
    """The installed stub's own exit status, run from the top level as git runs it."""
    return run(["sh", str(Path(repo) / ".git" / "hooks" / hook)], repo).returncode


_COUNTER = [0]


def make_base():
    base = TMP / "base"
    git_out(TMP, "init", "-q", "-b", "main", str(base))
    (base / HOOKS_REL).parent.mkdir(parents=True)
    shutil.copyfile(HOOKS_SRC, base / HOOKS_REL)
    write(base, ".aiqt/gate-record.toml", CONFIG)
    write(base, "check.sh", CHECK_SH)
    write(base, "a.txt", "a\n")
    write(base, ".gitignore", "ignored/\n")
    git_out(base, "add", "-A")
    git_out(base, "commit", "-q", "-m", "base")
    return base


def fixture(base, installed=True):
    _COUNTER[0] += 1
    repo = TMP / "r{:03d}".format(_COUNTER[0])
    git_out(TMP, "clone", "-q", str(base), str(repo))
    if installed:
        done = gate(repo, "install")
        if done.returncode != 0:
            raise RuntimeError("fixture install failed: " + done.stderr.strip())
    return repo


def refused_commit(repo, before, done, text):
    """(refused, named): the commit failed and left HEAD, and its stderr names text."""
    return done.returncode != 0 and head(repo) == before, text in done.stderr


# --- the recorder ------------------------------------------------------------------------------------------
def rows_recorder(base):
    repo = fixture(base, installed=False)
    stage(repo, "b.txt", "b\n")
    tree = git_out(repo, "write-tree")
    done = gate(repo, "run")
    last = records(repo)[-1]
    check("W1/exit", done.returncode, 0)
    check("W1/record", (last["result"], last["exit"], last["tree"], last["tree_after"], last["set"]),
          ("pass", 0, tree, tree, "full"))
    check("W1/keys", list(last), ["format", "tree", "head", "object_format", "tree_after", "set", "argv", "exit",
                                  "result", "started", "finished", "recorder"])
    check("W1/head-is-parent", last.get("head"), head(repo))
    done = gate(repo, "run", extra={"FIXTURE_EXIT": "3"})
    last = records(repo)[-1]
    check("W2/exit", done.returncode, 3)
    check("W2/record", (last["result"], last["exit"]), ("fail", 3))
    done = gate(repo, "run", extra={"FIXTURE_SIGNAL": "1"})
    last = records(repo)[-1]
    check("W3/exit-nonzero", done.returncode != 0, True)
    check("W3/record", (last["result"], last["exit"]), ("fail", 143))
    done = gate(repo, "run", extra={"FIXTURE_EDIT": "1"})
    last = records(repo)[-1]
    check("W4/exit-nonzero", done.returncode != 0, True)
    check("W4/record", (last["result"], last["exit"]), ("tree-moved", 0))
    git_out(repo, "checkout", "--", "a.txt")
    mark = TMP / "w5.mark"
    write(repo, "a.txt", "unstaged\n")
    done = gate(repo, "run", extra={"FIXTURE_MARK": str(mark)})
    last = records(repo)[-1]
    check("W5/exit", done.returncode, 2)
    check("W5/record", last["result"], "cannot-evaluate")
    check("W5/not-run", mark.exists(), False)
    git_out(repo, "checkout", "--", "a.txt")
    write(repo, "c.txt", "untracked\n")
    done = gate(repo, "run", extra={"FIXTURE_MARK": str(mark)})
    check("W6/exit", done.returncode, 2)
    check("W6/not-run", (records(repo)[-1]["result"], mark.exists()), ("cannot-evaluate", False))
    # the flip of W5 and W6: the same repository, clean, runs the check
    os.unlink(repo / "c.txt")
    done = gate(repo, "run", extra={"FIXTURE_MARK": str(mark)})
    check("W5-W6/flip-clean-runs", (done.returncode, mark.exists()), (0, True))
    other = fixture(base, installed=False)
    index_before = (other / ".git" / "index").read_bytes()
    count = len(records(repo))
    done = gate(repo, "run", extra={"GIT_DIR": str(other / ".git"), "GIT_INDEX_FILE": str(other / ".git" / "index")})
    check("W7/exit", done.returncode, 0)
    check("W7/own-log", len(records(repo)), count + 1)
    check("W7/other-untouched", (log_path(other).exists(), (other / ".git" / "index").read_bytes() == index_before),
          (False, True))
    count = len(records(repo))
    env = dict(ENV)
    env["FIXTURE_SLEEP"] = "1"
    argv = [sys.executable, "-I", "-B", str(HOOKS_SRC), "gate-record", "run"]
    procs = [subprocess.Popen(argv, cwd=str(repo), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for _ in range(2)]
    codes = [proc.wait(timeout=120) for proc in procs]
    lines = log_path(repo).read_text(encoding="ascii").splitlines()
    check("W8/both-exit-0", codes, [0, 0])
    check("W8/two-whole-lines", (len(lines), all(json.loads(line)["result"] == "pass" for line in lines[count:])),
          (count + 2, True))


# --- the verifiers -----------------------------------------------------------------------------------------
def rows_verify(base):
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        before = head(repo)
        done = commit(repo, "v1")
        if installed:
            check("V1/refused-missing", refused_commit(repo, before, done, "missing"), (True, True))
            check("V1/stub-exit", stub_rc(repo), 1)
        else:
            check("V1/flip-no-stub-commits", done.returncode, 0)
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    check("V2/stub-exit", stub_rc(repo), 0)
    check("V2/commit", commit(repo, "v2").returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run", extra={"FIXTURE_EXIT": "1"})
        before = head(repo)
        done = commit(repo, "v3")
        if installed:
            check("V3/refused-failed", refused_commit(repo, before, done, "failed"), (True, True))
            check("V3/stub-exit", stub_rc(repo), 1)
        else:
            check("V3/flip-no-stub-commits", done.returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        git_out(repo, "rm", "-q", "--cached", "b.txt")
        os.unlink(repo / "b.txt")
        stage(repo, "c.txt", "c\n")
        before = head(repo)
        done = commit(repo, "v4")
        if installed:
            check("V4/refused-other-tree", refused_commit(repo, before, done, "for another tree"), (True, True))
        else:
            check("V4/flip-no-stub-commits", done.returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        stage(repo, "d.txt", "more\n")
        before = head(repo)
        done = commit(repo, "v5")
        if installed:
            check("V5/refused-other-tree", refused_commit(repo, before, done, "for another tree"), (True, True))
        else:
            check("V5/flip-no-stub-commits", done.returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        write(repo, "a.txt", "unstaged edit\n")
        before = head(repo)
        done = commit(repo, "v6", "-a")
        if installed:
            check("V6/refused-commit-a", refused_commit(repo, before, done, "for another tree"), (True, True))
        else:
            check("V6/flip-no-stub-commits", done.returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run", "--set", "suite-x")
        before = head(repo)
        done = commit(repo, "v7")
        if installed:
            check("V7/refused-other-set", refused_commit(repo, before, done, "a different check set"), (True, True))
        else:
            check("V7/flip-no-stub-commits", done.returncode, 0)
    # V8: a commit that lowers the configuration is judged by HEAD's set. The recorder runs HEAD's set by default
    # so its record is for "full"; a record of the lowered set (run --set suite-x) is refused.
    repo = fixture(base)
    stage(repo, ".aiqt/gate-record.toml", LOWERED)
    gate(repo, "run")
    check("V8/recorder-runs-head-set", records(repo)[-1]["set"], "full")
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, ".aiqt/gate-record.toml", LOWERED)
        gate(repo, "run", "--set", "suite-x")
        before = head(repo)
        done = commit(repo, "v8")
        if installed:
            check("V8/refused-config-from-head", refused_commit(repo, before, done, "a different check set"),
                  (True, True))
        else:
            check("V8/flip-no-stub-commits", done.returncode, 0)
    # V18:a commit that changes the required set's argv records and commits by the documented
    # workflow, because the recorder runs HEAD's argv; its flip, the same commit with the check failing, refuses.
    for failed in (False, True):
        repo = fixture(base)
        stage(repo, ".aiqt/gate-record.toml", CONFIG.replace('argv = ["sh", "check.sh"]', 'argv = ["sh", "check.sh", "v2"]'))
        gate(repo, "run", extra={"FIXTURE_EXIT": "1"} if failed else None)
        before = head(repo)
        done = commit(repo, "v18")
        if failed:
            check("V18/flip-failing-head-set-refused", refused_commit(repo, before, done, "failed"), (True, True))
        else:
            check("V18/argv-change-commits", (records(repo)[-1]["argv"], done.returncode), (["sh", "check.sh"], 0))
    unborn = TMP / "unborn"
    git_out(TMP, "init", "-q", "-b", "main", str(unborn))
    (unborn / HOOKS_REL).parent.mkdir(parents=True)
    shutil.copyfile(HOOKS_SRC, unborn / HOOKS_REL)
    for rel, text in ((".aiqt/gate-record.toml", CONFIG), ("check.sh", CHECK_SH), ("a.txt", "a\n")):
        write(unborn, rel, text)
    git_out(unborn, "add", "-A")
    gate(unborn, "install")
    done = commit(unborn, "v9-before")
    check("V9/unborn-refused-without-record", (done.returncode != 0, "missing" in done.stderr), (True, True))
    gate(unborn, "run")
    check("V9/unborn-commit", commit(unborn, "v9").returncode, 0)
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run", extra={"FIXTURE_EXIT": "1"})
        gate(repo, "run")
        before = head(repo)
        done = commit(repo, "v10")
        if installed:
            check("V10/refused-earlier-failure", refused_commit(repo, before, done, "an earlier run on this tree failed"),
                  (True, True))
        else:
            check("V10/flip-no-stub-commits", done.returncode, 0)
    # V17: a tree-moved record whose check exited 0 (the tree changed under the run; the check did not fail) is no
    # earlier failure, so once the tree is restored a pass on it commits. Its flip: the same run with the check
    # also exiting 1 (tree-moved, exit 1) still refuses as an earlier failure.
    for failed in (False, True):
        repo = fixture(base)
        stage(repo, "b.txt", "b\n")
        extra = {"FIXTURE_EDIT": "1", "FIXTURE_EXIT": "1"} if failed else {"FIXTURE_EDIT": "1"}
        gate(repo, "run", extra=extra)
        git_out(repo, "checkout", "--", "a.txt")
        gate(repo, "run")
        before = head(repo)
        done = commit(repo, "v17")
        if failed:
            check("V17/flip-tree-moved-exit-1-refused",
                  refused_commit(repo, before, done, "an earlier run on this tree failed"), (True, True))
        else:
            check("V17/tree-moved-exit-0-commits", done.returncode, 0)
    rows_log_tamper(base)
    for installed in (True, False):
        repo = fixture(base, installed)
        forged = TMP / "forged-{}.jsonl".format(installed)
        stage(repo, "b.txt", "b\n")
        tree = git_out(repo, "write-tree")
        forged.write_text(forged_line(tree, head(repo)), encoding="ascii")
        extra = {"AIQT_GATE_RECORD": str(forged), "AIQT_GATE_RECORD_LOG": str(forged), "AIQT_GATE_TREE": tree}
        before = head(repo)
        done = git(repo, "commit", "-q", "-m", "v14", extra=extra)
        if installed:
            check("V14/forged-env-ignored", refused_commit(repo, before, done, "missing"), (True, True))
        else:
            check("V14/flip-no-stub-commits", done.returncode, 0)
    rows_push(base)
    rows_merge(base)


def forged_line(tree, parent):
    record = {"format": "aiqt-gate-record/2", "tree": tree, "head": parent, "object_format": "sha1",
              "tree_after": tree, "set": "full",
              "argv": ["sh", "check.sh"], "exit": 0, "result": "pass", "started": "2026-11-21T00:00:00Z",
              "finished": "2026-11-21T00:00:01Z", "recorder": "forged"}
    return json.dumps(record, separators=(",", ":")) + "\n"


def rows_log_tamper(base):
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    log = log_path(repo)
    good = log.read_text(encoding="ascii")
    check("V11-V13/baseline-pass", stub_rc(repo), 0)
    saved = log.with_name("saved.jsonl")
    shutil.copyfile(log, saved)
    os.unlink(log)
    os.symlink(saved, log)
    check("V11/symlink-log-exit", stub_rc(repo), 2)
    before = head(repo)
    done = commit(repo, "v11")
    check("V11/symlink-log-refused", refused_commit(repo, before, done, "cannot evaluate"), (True, True))
    os.unlink(log)
    log.write_text(good, encoding="ascii")
    os.chmod(log, 0o666)
    check("V12/writable-log-exit", stub_rc(repo), 2)
    os.chmod(log, 0o600)
    line = good.rstrip("\n")
    for rid, text in (("V13/duplicate-key-exit", line[:-1] + ',"recorder":"x"}\n'),
                      ("V13/extra-key-exit", line[:-1] + ',"extra":1}\n'),
                      ("V13/nan-exit", line.replace('"exit":0', '"exit":NaN') + "\n"),
                      ("V13/no-final-lf-exit", line)):
        log.write_text(text, encoding="ascii")
        check(rid, stub_rc(repo), 2)
    log.write_text(good, encoding="ascii")
    check("V11-V13/flip-restored-log-commits", commit(repo, "v13").returncode, 0)
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    tree = git_out(repo, "write-tree")
    log = log_path(repo)
    log.parent.mkdir(mode=0o700)
    log.write_text(forged_line(tree, head(repo)), encoding="ascii")
    os.chmod(log, 0o600)
    check("X3/forged-pass-line-commits", commit(repo, "x3").returncode, 0)
    stage(repo, "c.txt", "c\n")
    check("X1/no-verify-commits", commit(repo, "x1", "--no-verify").returncode, 0)
    stage(repo, "d.txt", "d\n")
    check("X2/hookspath-commits", git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "x2").returncode, 0)


def rows_push(base):
    for installed in (True, False):
        remote = TMP / "remote-{}.git".format(installed)
        git_out(TMP, "init", "-q", "--bare", str(remote))
        repo = fixture(base, installed=False)
        git_out(repo, "remote", "set-url", "origin", str(remote))
        git_out(repo, "branch", "old")
        git_out(repo, "push", "-q", "origin", "main", "old")
        if installed:
            gate(repo, "install")
        git_out(repo, "checkout", "-q", "-b", "tip-a")
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        commit(repo, "tip a")
        git_out(repo, "checkout", "-q", "-b", "tip-b", "main")
        stage(repo, "c.txt", "c\n")
        commit(repo, "tip b", "--no-verify")
        done = git(repo, "push", "-q", "origin", "tip-a", "tip-b", ":old")
        if installed:
            check("V15/push-refused", (done.returncode != 0, "missing" in done.stderr), (True, True))
            listed = git_out(remote, "for-each-ref", "--format=%(refname)")
            check("V15/remote-unchanged", sorted(listed.split()), ["refs/heads/main", "refs/heads/old"])
            done = git(repo, "push", "-q", "origin", "tip-a", ":old")
            check("V15/pass-and-deletion-pushed", done.returncode, 0)
        else:
            check("V15/flip-no-stub-pushes", done.returncode, 0)


def rows_merge(base):
    for installed in (True, False):
        repo = fixture(base, installed)
        git_out(repo, "checkout", "-q", "-b", "side")
        stage(repo, "s.txt", "side\n")
        commit(repo, "side", "--no-verify")
        git_out(repo, "checkout", "-q", "main")
        stage(repo, "m.txt", "main\n")
        commit(repo, "main", "--no-verify")
        before = head(repo)
        done = git(repo, "merge", "-q", "--no-edit", "side")
        if installed:
            check("V16/merge-refused", (done.returncode != 0, head(repo) == before, "missing" in done.stderr),
                  (True, True, True))
        else:
            check("V16/flip-no-stub-merges", (done.returncode, head(repo) != before), (0, True))


# --- the binding class: check, tree, parent and log location as the hook judges them ---------------------------
def bare_remote(repo, name):
    remote = TMP / "{}.git".format(name)
    git_out(TMP, "init", "-q", "--bare", "-b", "main", str(remote))
    git_out(repo, "remote", "set-url", "origin", str(remote))
    git_out(repo, "push", "-q", "--no-verify", "origin", "main")
    return remote


def rows_class(base):
    # V19:an orphan branch keeps the default branch's bar. Lowering the configuration in its index
    # refuses the commit, and a root commit made past the hook with it refuses the push; the flip, an orphan
    # branch that keeps the bar, commits and pushes.
    for lowered in (True, False):
        repo = fixture(base)
        remote = bare_remote(repo, "v19-{}".format(lowered))
        git_out(repo, "checkout", "-q", "--orphan", "v19")
        if lowered:
            stage(repo, ".aiqt/gate-record.toml", LOWERED)
        gate(repo, "run")
        done = commit(repo, "v19")
        if lowered:
            check("V19/orphan-lowered-commit-refused", (done.returncode != 0, head(repo), "own bar" in done.stderr),
                  (True, "", True))
            commit(repo, "v19", "--no-verify")
            done = git(repo, "push", "-q", "origin", "v19")
            check("V19/orphan-lowered-push-refused", (done.returncode != 0, "own bar" in done.stderr,
                                                       git(remote, "rev-parse", "-q", "--verify", "v19").stdout),
                  (True, True, ""))
        else:
            check("V19/flip-same-bar-orphan-commits", done.returncode, 0)
            check("V19/flip-same-bar-orphan-pushes", git(repo, "push", "-q", "origin", "v19").returncode, 0)
    # V20:the check runs in its own session and its process group is killed before the record is
    # written, so a writer it leaves behind in its group cannot rewrite the log; the flip pins residual R3, a writer
    # that leaves the group (setsid) and rewrites the log after the recorder's line.
    for escape in (False, True):
        repo = fixture(base)
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        saved = TMP / "v20-saved-{}.jsonl".format(escape)
        shutil.copyfile(log_path(repo), saved)
        done = gate(repo, "run", extra={"FIXTURE_EXIT": "1", "FIXTURE_WRITER": "escape" if escape else "group",
                                        "FIXTURE_SAVED": str(saved), "FIXTURE_LOG": str(log_path(repo))})
        time.sleep(3)
        before = head(repo)
        result = commit(repo, "v20")
        if escape:
            check("V20/flip-escaped-writer-forges-residual", (done.returncode, result.returncode), (1, 0))
        else:
            check("V20/group-writer-killed", (done.returncode, records(repo)[-1]["result"],
                                              refused_commit(repo, before, result, "failed")), (1, "fail", (True, True)))
    # V21:pre-push reads every worktree's log, so a branch recorded and committed in a linked
    # worktree pushes from the main worktree; the flip, a branch committed there with no record, refuses.
    repo = fixture(base)
    bare_remote(repo, "v21")
    linked = TMP / "v21-linked"
    git_out(repo, "worktree", "add", "-q", "-b", "v21", str(linked))
    stage(linked, "b.txt", "b\n")
    gate(linked, "run")
    commit(linked, "v21")
    check("V21/linked-record-pushes-from-main", git(repo, "push", "-q", "origin", "v21").returncode, 0)
    git_out(linked, "checkout", "-q", "-b", "v21-none")
    stage(linked, "c.txt", "c\n")
    commit(linked, "v21 none", "--no-verify")
    done = git(repo, "push", "-q", "origin", "v21-none")
    check("V21/flip-unrecorded-refused", (done.returncode != 0, "missing" in done.stderr), (True, True))
    # V22:the stub's verifier copy lives in the git directory, so an unusable TMPDIR does not
    # refuse and no copy is left behind; the flip, a symbolic-link aiqt directory, cannot evaluate.
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    done = git(repo, "commit", "-q", "-m", "v22", extra={"TMPDIR": str(TMP / "no-such-dir")})
    left = sorted(path.name for path in (repo / ".git" / "aiqt").iterdir() if path.name.startswith("verifier."))
    check("V22/no-tmpdir-commits", (done.returncode, left), (0, []))
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    real = repo / ".git" / "aiqt-real"
    (repo / ".git" / "aiqt").rename(real)
    os.symlink(real, repo / ".git" / "aiqt")
    check("V22/flip-symlink-dir-stub-exit", stub_rc(repo), 2)
    # V23:the recorder's lock wait and the check's run are bounded and cannot evaluate past the
    # bound; the flips, the same runs inside the bound, pass.
    import fcntl
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    fd = os.open(str(log_path(repo).parent / "recorder.lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        done = run([sys.executable, "-I", "-B", str(HOOKS_SRC), "gate-record", "run"], repo,
                   {"AIQT_GATE_RECORD_LOCK_TIMEOUT": "1"}, timeout=60)
    finally:
        os.close(fd)
    check("V23/held-lock-cannot-evaluate", (done.returncode, "stayed locked" in done.stderr), (2, True))
    check("V23/flip-free-lock-runs", gate(repo, "run", extra={"AIQT_GATE_RECORD_LOCK_TIMEOUT": "1"}).returncode, 0)
    done = gate(repo, "run", extra={"FIXTURE_SLEEP": "5", "AIQT_GATE_RECORD_CHECK_TIMEOUT": "1"})
    check("V23/slow-check-cannot-evaluate", (done.returncode, records(repo)[-1]["result"]), (2, "cannot-evaluate"))
    done = gate(repo, "run", extra={"FIXTURE_SLEEP": "1", "AIQT_GATE_RECORD_CHECK_TIMEOUT": "30"})
    check("V23/flip-check-inside-bound-passes", (done.returncode, records(repo)[-1]["result"]), (0, "pass"))
    # V24:the recorder opens the aiqt directory with O_NOFOLLOW and creates its files relative to
    # it, so a symbolic-link aiqt directory cannot evaluate and nothing is written through it; the flip, a real
    # directory, records.
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    outside = TMP / "v24-outside"
    outside.mkdir(mode=0o700)
    os.symlink(outside, repo / ".git" / "aiqt")
    done = gate(repo, "run")
    check("V24/symlink-dir-not-followed", (done.returncode, sorted(path.name for path in outside.iterdir())), (2, []))
    os.unlink(repo / ".git" / "aiqt")
    check("V24/flip-real-dir-records", gate(repo, "run").returncode, 0)
    # V25 (the class):the record binds the parent commit it ran on. The same tree committed on
    # another parent refuses; a check that moves HEAD records tree-moved; the flip, the recorded parent, commits.
    for moved in (False, True):
        repo = fixture(base)
        first = head(repo)
        stage(repo, "c.txt", "c\n")
        commit(repo, "other parent", "--no-verify")
        stage(repo, "b.txt", "b\n")
        gate(repo, "run")
        if moved:
            git_out(repo, "reset", "-q", "--soft", first)
            before = head(repo)
            done = commit(repo, "v25")
            check("V25/other-parent-refused", refused_commit(repo, before, done, "for another parent"), (True, True))
        else:
            check("V25/flip-recorded-parent-commits", commit(repo, "v25").returncode, 0)
    repo = fixture(base)
    moving = "git -c user.name=f -c user.email=f@example.invalid commit-tree -m moved HEAD^{tree}"
    write(repo, "check.sh", CHECK_SH.replace('exit "${FIXTURE_EXIT:-0}"',
                                             'if [ -n "${FIXTURE_MOVE:-}" ]; then git reset -q --soft "$(' + moving
                                             + ')"; fi\nexit "${FIXTURE_EXIT:-0}"'))
    git_out(repo, "add", "check.sh")
    git_out(repo, "commit", "-q", "--no-verify", "-m", "a check that can move HEAD")
    stage(repo, "d.txt", "d\n")
    check("V25/flip-check-not-moving-passes", gate(repo, "run").returncode, 0)
    done = gate(repo, "run", extra={"FIXTURE_MOVE": "1"})
    check("V25/check-moving-head-tree-moved", (done.returncode, records(repo)[-1]["result"]), (1, "tree-moved"))
    # V26:replace objects are off for every git read of the recorder, the verifiers
    # and the stubs. A git replace of HEAD's configuration blob with one whose argv is ["true"] does not lower the
    # set the recorder runs or the verifier judges; a replaced verifier blob is not what the stub runs; and a
    # commit replaced by a recorded one is judged as itself at push. The flips: with the replacement present, the
    # real set's pass commits, and the recorded commit itself pushes.
    repo = fixture(base)
    low = TMP / "v26-low.toml"
    low.write_text(CONFIG.replace('argv = ["sh", "check.sh"]\n', 'argv = ["true"]\n', 1), encoding="utf-8")
    git_out(repo, "replace", git_out(repo, "rev-parse", "HEAD:.aiqt/gate-record.toml"),
            git_out(repo, "hash-object", "-w", str(low)))
    stage(repo, "b.txt", "b\n")
    done = gate(repo, "run", extra={"FIXTURE_EXIT": "1"})
    before = head(repo)
    result = commit(repo, "v26")
    check("V26/replaced-config-not-read", (done.returncode, records(repo)[-1]["argv"],
                                           refused_commit(repo, before, result, "failed")),
          (1, ["sh", "check.sh"], (True, True)))
    stage(repo, "c.txt", "c\n")
    done = gate(repo, "run")
    flip = commit(repo, "v26 flip")
    check("V26/flip-replaced-config-real-pass-commits",
          (done.returncode, flip.returncode, "graft" in (done.stderr + flip.stderr).lower()), (0, 0, False))
    repo = fixture(base)
    fake = TMP / "v26-fake-verifier.py"
    fake.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    git_out(repo, "replace", git_out(repo, "rev-parse", "HEAD:" + HOOKS_REL),
            git_out(repo, "hash-object", "-w", str(fake)))
    stage(repo, "b.txt", "b\n")
    before = head(repo)
    result = commit(repo, "v26 verifier")
    check("V26/replaced-verifier-not-run", refused_commit(repo, before, result, "missing"), (True, True))
    repo = fixture(base)
    remote = bare_remote(repo, "v26")
    parent = head(repo)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    commit(repo, "v26 recorded")
    good = head(repo)
    git_out(repo, "reset", "-q", "--hard", parent)
    stage(repo, "c.txt", "c\n")
    commit(repo, "v26 unrecorded", "--no-verify")
    bad = head(repo)
    git_out(repo, "replace", bad, good)
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v26")
    check("V26/replaced-commit-push-refused", (done.returncode != 0, "missing" in done.stderr,
                                               git(remote, "rev-parse", "-q", "--verify", "v26").stdout),
          (True, True, ""))
    git_out(repo, "replace", "-d", bad)
    check("V26/flip-recorded-commit-pushes",
          git(repo, "push", "-q", "origin", good + ":refs/heads/v26-recorded").returncode, 0)
    rows_substituted_state(base)
    rows_remote_has(base)


def forge_graph_parent(repo, commit, parent):
    """Write the repository's commit-graph, then make it name parent as commit's first parent (a same-user write
    of the file; git does not check its trailing checksum when it reads it)."""
    git_out(repo, "commit-graph", "write", "--reachable")
    path = Path(repo) / ".git" / "objects" / "info" / "commit-graph"
    data = bytearray(path.read_bytes())
    hashlen = 20 if data[5] == 1 else 32
    chunks = {}
    for index in range(data[6] + 1):
        name, offset = struct.unpack(">4sQ", bytes(data[8 + 12 * index:20 + 12 * index]))
        chunks[name] = offset
    total = struct.unpack(">I", bytes(data[chunks[b"OIDF"] + 1020:chunks[b"OIDF"] + 1024]))[0]
    oids = [bytes(data[chunks[b"OIDL"] + i * hashlen:chunks[b"OIDL"] + (i + 1) * hashlen]).hex() for i in range(total)]
    at = chunks[b"CDAT"] + oids.index(commit) * (hashlen + 16) + hashlen
    data[at:at + 4] = struct.pack(">I", oids.index(parent))
    os.chmod(path, 0o644)
    path.write_bytes(bytes(data))


def push_line(sha, ref="refs/heads/x"):
    return "refs/heads/main {} {} {}\n".format(sha, ref, "0" * len(sha))


def rows_substituted_state(base):
    # V27 (gitrecord3, the class of git state the same user can substitute): a shallow file naming a pushed commit
    # hides its parent as a grafts file does, so pre-push judged it as a root commit whose tree a record on no
    # parent admitted; it now cannot evaluate. The flip: a shallow file naming another commit leaves a recorded
    # commit's push passing.
    repo = fixture(base)
    stage(repo, "b.txt", "b\n")
    commit(repo, "v27 unrecorded parent", "--no-verify")
    stage(repo, "c.txt", "c\n")
    commit(repo, "v27 c", "--no-verify")
    pushed = head(repo)
    git_out(repo, "switch", "-q", "--orphan", "v27-orphan")
    git_out(repo, "read-tree", pushed)
    git_out(repo, "checkout-index", "-a", "-f")
    recorded = gate(repo, "run").returncode
    git_out(repo, "switch", "-q", "-f", "main")
    (Path(repo) / ".git" / "shallow").write_text(pushed + "\n", encoding="ascii")
    done = gate(repo, "verify-push", data=push_line(pushed))
    check("V27/shallow-boundary-push-cannot-evaluate", (recorded, done.returncode, "shallow" in done.stderr),
          (0, 2, True))
    repo = fixture(base)
    root = head(repo)
    stage(repo, "b.txt", "b\n")
    gate(repo, "run")
    commit(repo, "v27 recorded")
    (Path(repo) / ".git" / "shallow").write_text(root + "\n", encoding="ascii")
    check("V27/flip-shallow-other-commit-pushes", gate(repo, "verify-push", data=push_line(head(repo))).returncode, 0)
    # A commit-graph file that names an unrecorded commit as the first parent of the remote's tip would make the
    # remote proof (rev-list over the parents) read the commit as one the remote has; git reads the commit objects
    # (core.commitGraph=false), so it stays refused. The flip: with that file present, a commit the remote really
    # has is accepted.
    repo = fixture(base)
    remote = bare_remote(repo, "v27")
    root = head(repo)
    git_out(repo, "switch", "-q", "-c", "v27-r")
    stage(repo, "r.txt", "r\n")
    commit(repo, "v27 on the remote", "--no-verify")
    tip = head(repo)
    git_out(repo, "push", "-q", "--no-verify", "origin", "v27-r")
    git_out(repo, "switch", "-q", "-c", "v27-u", root)
    stage(repo, "u.txt", "u\n")
    commit(repo, "v27 unrecorded", "--no-verify")
    unrecorded = head(repo)
    forge_graph_parent(repo, tip, unrecorded)
    done = gate(repo, "verify-push", "origin", str(remote), data=push_line(unrecorded))
    check("V27/graph-forged-parent-remote-proof-refused", (done.returncode, "missing" in done.stderr), (1, True))
    done = gate(repo, "verify-push", "origin", str(remote), data=push_line(tip))
    check("V27/flip-graph-present-remote-commit-accepted", done.returncode, 0)


def rows_remote_has(base):
    # V28:a new branch or a tag at a commit the remote already has (made elsewhere,
    # for example a hosted squash merge) pushes with no record in this clone; the proof is the remote's own refs
    # (git ls-remote of the push URL). The flips: a commit the remote lacks stays refused, also when this clone's
    # refs/remotes names it, an unreadable remote cannot evaluate, and with no URL the refusal stands.
    repo = fixture(base)
    remote = bare_remote(repo, "v28")
    other = TMP / "v28-other"
    git_out(TMP, "clone", "-q", str(remote), str(other))
    write(other, "s.txt", "squash\n")
    git_out(other, "add", "s.txt")
    git_out(other, "commit", "-q", "-m", "squash merge made elsewhere")
    git_out(other, "push", "-q", "origin", "main")
    git_out(repo, "fetch", "-q", "origin")
    git_out(repo, "switch", "-q", "-c", "v28-feature", "origin/main")
    done = git(repo, "push", "-q", "-u", "origin", "v28-feature")
    check("V28/new-branch-at-remote-commit-pushes",
          (done.returncode, git(remote, "rev-parse", "-q", "--verify", "v28-feature").stdout.strip() != ""), (0, True))
    git_out(repo, "tag", "v28-light", "origin/main")
    check("V28/tag-at-remote-commit-pushes", git(repo, "push", "-q", "origin", "v28-light").returncode, 0)
    git_out(repo, "tag", "-a", "-m", "annotated", "v28-annotated", "origin/main")
    check("V28/annotated-tag-at-remote-commit-pushes", git(repo, "push", "-q", "origin", "v28-annotated").returncode, 0)
    stage(repo, "u.txt", "u\n")
    commit(repo, "v28 unrecorded", "--no-verify")
    unrecorded = head(repo)
    git_out(repo, "update-ref", "refs/remotes/origin/forged", unrecorded)
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v28-u")
    check("V28/flip-tracking-ref-names-it-refused",
          (done.returncode != 0, "missing" in done.stderr,
           git(remote, "rev-parse", "-q", "--verify", "v28-u").stdout.strip()), (True, True, ""))
    done = gate(repo, "verify-push", "origin", str(TMP / "v28-no-such-remote.git"), data=push_line(unrecorded))
    check("V28/flip-remote-unreadable-cannot-evaluate", (done.returncode, "ls-remote" in done.stderr), (2, True))
    done = gate(repo, "verify-push", data=push_line(git_out(repo, "rev-parse", "origin/main")))
    check("V28/flip-no-url-refused", (done.returncode, "missing" in done.stderr), (1, True))
    rows_url_rewrite(base)


def rows_url_rewrite(base):
    # V29:git ls-remote reads the URL after the fetch rewrites, git push writes the URL after
    # the push rewrites. url.<this clone>.insteadOf=<remote> with url.<remote>.pushInsteadOf=<remote> (two
    # ordinary git config commands) made the remote proof read this clone, which has every commit, while the
    # push wrote the remote, so an unrecorded commit reached it; a remote named like the push URL did the same.
    # Both now cannot evaluate and nothing reaches the remote. The flip: a rewrite that does not match the push
    # URL leaves a commit the remote has pushing.
    repo = fixture(base)
    remote = bare_remote(repo, "v29")
    stage(repo, "u.txt", "u\n")
    commit(repo, "v29 unrecorded", "--no-verify")
    git_out(repo, "config", "url.{}.insteadOf".format(repo), str(remote))
    git_out(repo, "config", "url.{}.pushInsteadOf".format(remote), str(remote))
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v29-u")
    check("V29/insteadof-read-elsewhere-cannot-evaluate",
          (done.returncode != 0, "insteadOf" in done.stderr,
           git(remote, "rev-parse", "-q", "--verify", "v29-u").stdout.strip()), (True, True, ""))
    repo = fixture(base)
    git_out(TMP, "init", "-q", "--bare", "-b", "main", str(Path(repo) / "v29-lacks.git"))
    git_out(repo, "remote", "set-url", "origin", "v29-lacks.git")
    git_out(repo, "push", "-q", "--no-verify", "origin", "main")
    stage(repo, "u.txt", "u\n")
    commit(repo, "v29 unrecorded", "--no-verify")
    git_out(repo, "config", "remote.v29-lacks.git.url", str(repo))
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v29-n")
    check("V29/remote-named-like-url-cannot-evaluate",
          (done.returncode != 0, "remote named like" in done.stderr,
           git(Path(repo) / "v29-lacks.git", "rev-parse", "-q", "--verify", "v29-n").stdout.strip()),
          (True, True, ""))
    repo = fixture(base)
    remote = bare_remote(repo, "v29-flip")
    git_out(repo, "config", "url.{}.insteadOf".format(repo), "https://example.invalid/elsewhere.git")
    other = TMP / "v29-other"
    git_out(TMP, "clone", "-q", str(remote), str(other))
    write(other, "s.txt", "elsewhere\n")
    git_out(other, "add", "s.txt")
    git_out(other, "commit", "-q", "-m", "made elsewhere")
    git_out(other, "push", "-q", "origin", "main")
    git_out(repo, "fetch", "-q", "origin")
    git_out(repo, "switch", "-q", "-c", "v29-feature", "origin/main")
    done = git(repo, "push", "-q", "origin", "v29-feature")
    check("V29/flip-unrelated-rewrite-remote-commit-pushes",
          (done.returncode, git(remote, "rev-parse", "-q", "--verify", "v29-feature").stdout.strip() != ""),
          (0, True))
    rows_reviewed_refs(base)
    rows_shallow_remote(base)
    rows_transport(base)
    rows_delivery(base)


def rows_reviewed_refs(base):
    # V30:the remote proof read every ref the remote advertises, so an unrecorded
    # commit a pull-request ref holds (a hosted service makes refs/pull/<n>/head from any user's unreviewed
    # commits; simulated here with a --no-verify push) pushed to a branch with no record. Only the remote's branches
    # and tags count now: it stays refused and the remote never gets the branch. The flip: the same commit on a
    # branch of the remote pushes.
    repo = fixture(base)
    remote = bare_remote(repo, "v30")
    stage(repo, "u.txt", "u\n")
    commit(repo, "v30 unrecorded", "--no-verify")
    git_out(repo, "push", "-q", "--no-verify", "origin", "HEAD:refs/pull/1/head")
    git_out(repo, "push", "-q", "--no-verify", "origin", "HEAD:refs/merge-requests/1/head")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v30-u")
    check("V30/pull-request-ref-only-refused",
          (done.returncode != 0, "missing" in done.stderr,
           git(remote, "rev-parse", "-q", "--verify", "refs/heads/v30-u").stdout.strip()), (True, True, ""))
    git_out(repo, "push", "-q", "--no-verify", "origin", "HEAD:refs/heads/v30-reviewed")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v30-u")
    check("V30/flip-branch-ref-pushes",
          (done.returncode, git(remote, "rev-parse", "-q", "--verify", "refs/heads/v30-u").stdout.strip() != ""),
          (0, True))


def rows_shallow_remote(base):
    # V31:in a depth-1 clone, a new branch at the shallow boundary commit (which the
    # remote has on its main branch) could not be evaluated, because the shallow error came before the remote
    # proof; the proof reads the commit objects, which a shallow cut can only make accept less, so it now pushes.
    # The flip: once the remote's branches no longer hold that commit (only a pull-request ref does), the same push
    # cannot evaluate and the remote never gets the branch.
    seed = fixture(base, installed=False)
    remote = bare_remote(seed, "v31")
    stage(seed, "d.txt", "d\n")
    commit(seed, "v31 second", "--no-verify")
    git_out(seed, "push", "-q", "--no-verify", "origin", "main")
    boundary = head(seed)
    _COUNTER[0] += 1
    repo = TMP / "r{:03d}".format(_COUNTER[0])
    git_out(TMP, "clone", "-q", "--depth", "1", "file://" + str(remote), str(repo))
    done = gate(repo, "install")
    if done.returncode != 0:
        raise RuntimeError("fixture install failed: " + done.stderr.strip())
    shallow = git_out(repo, "rev-parse", "--is-shallow-repository")
    git_out(repo, "switch", "-q", "-c", "v31-new")
    done = git(repo, "push", "-q", "origin", "v31-new")
    check("V31/shallow-boundary-remote-has-pushes",
          (shallow, head(repo) == boundary, done.returncode,
           git(remote, "rev-parse", "-q", "--verify", "refs/heads/v31-new").stdout.strip() == boundary),
          ("true", True, 0, True))
    git_out(seed, "push", "-q", "--no-verify", "origin", "HEAD:refs/pull/1/head")
    git_out(seed, "push", "-q", "--no-verify", "-f", "origin", "HEAD~1:refs/heads/main", ":refs/heads/v31-new")
    git_out(repo, "switch", "-q", "-c", "v31-gone")
    done = git(repo, "push", "-q", "origin", "v31-gone")
    check("V31/flip-shallow-boundary-remote-lacks-cannot-evaluate",
          (done.returncode != 0, "shallow" in done.stderr, "does not have it" in done.stderr,
           git(remote, "rev-parse", "-q", "--verify", "refs/heads/v31-gone").stdout.strip()), (True, True, True, ""))


def rows_transport(base):
    # V32:an ssh program the push names sees which service git
    # asks for, so it can send the proof's read and the push to different repositories (V33). The proof is not
    # read through one: through a GIT_SSH_COMMAND transport (a local stand-in for ssh), even a new branch at a
    # commit the remote has cannot evaluate, and the remote never gets the branch. The flip: over the remote's
    # plain URL, the same branch pushes.
    repo = fixture(base)
    remote = bare_remote(repo, "v32")
    other = TMP / "v32-other"
    git_out(TMP, "clone", "-q", str(remote), str(other))
    write(other, "s.txt", "elsewhere\n")
    git_out(other, "add", "s.txt")
    git_out(other, "commit", "-q", "-m", "made elsewhere")
    git_out(other, "push", "-q", "origin", "main")
    git_out(repo, "fetch", "-q", "origin")
    fake = TMP / "v32-ssh"
    write(TMP, "v32-ssh", '#!/bin/sh\n# a stand-in for ssh: run the git command git asks for, here\nshift\n'
          'exec sh -c "git ${1#git-}"\n')
    os.chmod(fake, 0o755)
    git_out(repo, "remote", "set-url", "origin", "ssh://v32.invalid" + str(remote))
    transport = {"GIT_SSH_COMMAND": str(fake), "GIT_SSH_VARIANT": "simple"}
    git_out(repo, "switch", "-q", "-c", "v32-feature", "origin/main")
    done = git(repo, "push", "-q", "origin", "v32-feature", extra=transport)
    check("V32/ssh-command-not-read-cannot-evaluate",
          (done.returncode != 0, "GIT_SSH_COMMAND" in done.stderr,
           git(remote, "rev-parse", "-q", "--verify", "refs/heads/v32-feature").stdout.strip()), (True, True, ""))
    git_out(repo, "remote", "set-url", "origin", str(remote))
    done = git(repo, "push", "-q", "origin", "v32-feature")
    check("V32/flip-plain-url-remote-commit-pushes",
          (done.returncode, git(remote, "rev-parse", "-q", "--verify", "refs/heads/v32-feature").stdout.strip() != ""),
          (0, True))


SPLIT_SSH = """#!/bin/sh
# a stand-in for ssh that picks the repository by the service git asks for: the read goes to {read},
# the push to the URL's repository
shift
case "$1" in
    git-upload-pack*) exec git upload-pack "{read}" ;;
    *) exec sh -c "git ${{1#git-}}" ;;
esac
"""


def rows_delivery(base):
    # V33:git ls-remote of the push URL reads the repository upload-pack serves, while the push
    # goes where receive-pack runs, and a program or setting the push chose can make those differ: an ssh program
    # that picks the repository by service, remote.<name>.receivepack naming another repository's receive-pack,
    # GIT_EXEC_PATH holding another git-receive-pack, a transport helper. With each of the first four the proof read
    # this clone, which has every commit, while the unrecorded commit reached the remote (observed); a
    # transport helper (ext:: here) is a program of the same kind. Each now cannot evaluate and the remote never
    # gets the branch. The flip: an unrelated setting (core.sshCommand on a local URL) leaves a commit the remote
    # has pushing.
    def unrecorded(name):
        repo = fixture(base)
        remote = bare_remote(repo, name)
        stage(repo, "u.txt", name + "\n")
        commit(repo, name + " unrecorded", "--no-verify")
        return repo, remote

    def lacks(remote, ref):
        return git(remote, "rev-parse", "-q", "--verify", ref).stdout.strip()

    repo, remote = unrecorded("v33a")
    write(TMP, "v33a-ssh", SPLIT_SSH.format(read=repo))
    os.chmod(TMP / "v33a-ssh", 0o755)
    git_out(repo, "remote", "set-url", "origin", "ssh://v33.invalid" + str(remote))
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v33-u",
               extra={"GIT_SSH_COMMAND": str(TMP / "v33a-ssh"), "GIT_SSH_VARIANT": "simple"})
    check("V33/ssh-command-service-split-cannot-evaluate",
          (done.returncode != 0, "GIT_SSH_COMMAND" in done.stderr, lacks(remote, "refs/heads/v33-u")), (True, True, ""))

    repo, remote = unrecorded("v33b")
    write(TMP, "v33b-ssh", SPLIT_SSH.format(read=repo))
    os.chmod(TMP / "v33b-ssh", 0o755)
    git_out(repo, "remote", "set-url", "origin", "ssh://v33.invalid" + str(remote))
    git_out(repo, "config", "core.sshCommand", str(TMP / "v33b-ssh"))
    git_out(repo, "config", "ssh.variant", "simple")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v33-u")
    check("V33/core-sshcommand-service-split-cannot-evaluate",
          (done.returncode != 0, "core.sshCommand" in done.stderr, lacks(remote, "refs/heads/v33-u")),
          (True, True, ""))

    repo, remote = unrecorded("v33c")
    git_out(repo, "remote", "add", "up", str(repo))
    git_out(repo, "config", "remote.up.receivepack", "git receive-pack {} #".format(remote))
    done = git(repo, "push", "-q", "up", "HEAD:refs/heads/v33-u")
    check("V33/receivepack-config-cannot-evaluate",
          (done.returncode != 0, "receivepack" in done.stderr, lacks(remote, "refs/heads/v33-u")), (True, True, ""))

    repo, remote = unrecorded("v33d")
    planted = TMP / "v33d-exec"
    planted.mkdir()
    write(planted, "git-receive-pack", '#!/bin/sh\nexec git receive-pack "{}"\n'.format(remote))
    os.chmod(planted / "git-receive-pack", 0o755)
    done = git(repo, "push", "-q", str(repo), "HEAD:refs/heads/v33-u",
               extra={"GIT_EXEC_PATH": str(planted), "PATH": str(planted) + os.pathsep + ENV.get("PATH", "")})
    check("V33/exec-path-receive-pack-cannot-evaluate",
          (done.returncode != 0, "GIT_EXEC_PATH" in done.stderr, lacks(remote, "refs/heads/v33-u")), (True, True, ""))

    repo, remote = unrecorded("v33e")
    write(TMP, "v33e-ext", '#!/bin/sh\ncase "$1" in\n    git-upload-pack) exec git upload-pack "{}" ;;\n'
          '    *) exec git receive-pack "{}" ;;\nesac\n'.format(repo, remote))
    os.chmod(TMP / "v33e-ext", 0o755)
    git_out(repo, "config", "protocol.ext.allow", "always")
    done = git(repo, "push", "-q", "ext::{} %s".format(TMP / "v33e-ext"), "HEAD:refs/heads/v33-u")
    check("V33/transport-helper-url-cannot-evaluate",
          (done.returncode != 0, "git-remote-ext" in done.stderr, lacks(remote, "refs/heads/v33-u")),
          (True, True, ""))

    repo = fixture(base)
    remote = bare_remote(repo, "v33f")
    other = TMP / "v33f-other"
    git_out(TMP, "clone", "-q", str(remote), str(other))
    write(other, "s.txt", "elsewhere\n")
    git_out(other, "add", "s.txt")
    git_out(other, "commit", "-q", "-m", "made elsewhere")
    git_out(other, "push", "-q", "origin", "main")
    git_out(repo, "fetch", "-q", "origin")
    git_out(repo, "config", "core.sshCommand", str(TMP / "v33b-ssh"))
    git_out(repo, "switch", "-q", "-c", "v33-feature", "origin/main")
    done = git(repo, "push", "-q", "origin", "v33-feature")
    check("V33/flip-unrelated-config-remote-commit-pushes",
          (done.returncode, lacks(remote, "refs/heads/v33-feature") != ""), (0, True))

    # remote.<name>.vcs (gitrecord5): a remote whose URL is this clone and whose vcs names a transport helper the
    # push's PATH supplies (here one that hands the push to git remote-ext and the remote) made the proof read this
    # clone while the helper delivered elsewhere; it now cannot evaluate and the remote never gets the branch.
    repo, remote = unrecorded("v33g")
    helpers = TMP / "v33g-helpers"
    helpers.mkdir()
    write(helpers, "git-remote-v33g", '#!/bin/sh\nexec git remote-ext "$1" "git %s {}"\n'.format(remote))
    os.chmod(helpers / "git-remote-v33g", 0o755)
    git_out(repo, "remote", "add", "up", str(repo))
    git_out(repo, "config", "remote.up.vcs", "v33g")
    git_out(repo, "config", "protocol.allow", "always")
    done = git(repo, "push", "-q", "up", "HEAD:refs/heads/v33-u",
               extra={"PATH": str(helpers) + os.pathsep + ENV.get("PATH", "")})
    check("V33/remote-vcs-helper-cannot-evaluate",
          (done.returncode != 0, "remote.<name>.vcs" in done.stderr, lacks(remote, "refs/heads/v33-u")),
          (True, True, ""))

    # X4, a pinned residual: git push --receive-pack on the command line reaches no hook, so a push to this clone's
    # own URL (".") whose receive-pack runs in the remote delivers the unrecorded commit there, the proof having read
    # this clone. Open like core.hooksPath on the command line (X2); the text guard denies the spellings it reads
    # (_gate_push_redirect, the gpr-* rows of selftest_aiqt_hooks.py), so the residual is the forms it cannot read.
    repo, remote = unrecorded("x4")
    done = git(repo, "push", "-q", "--receive-pack=git receive-pack {} #".format(remote), ".", "HEAD:refs/heads/x4-u")
    check("X4/push-receive-pack-command-line-delivers",
          (done.returncode, lacks(remote, "refs/heads/x4-u") != ""), (0, True))
    rows_programs(base, unrecorded, lacks)
    rows_symbolic_refs(base, unrecorded, lacks)


FAKE_PY_EXIT = "#!/bin/sh\n# a planted python3: it never runs the verifier\nexit {}\n"
FAKE_GIT = """#!/bin/sh
# a planted git: git show (the stub reading the verifier from HEAD) prints a verifier that accepts; every other
# git command is the real git
case "$*" in
    *" show "*) printf 'raise SystemExit(0)\\n' ;;
    *) exec "{real}" "$@" ;;
esac
"""


def rows_programs(base, unrecorded, lacks):
    # V34:the stubs ran python3, git, timeout and mktemp from the PATH git passes,
    # where a PATH or GIT_EXEC_PATH the push named put a planted program first, so a planted python3 (or a planted
    # git that serves an accepting verifier) skipped the verifier and the unrecorded commit reached the remote. The
    # stubs now run the absolute paths install recorded, with PATH set to fixed directories, and the verifiers run
    # the recorded git: each planted program is passed over and the push is refused. The flip: a recorded commit
    # pushes with a planted python3 that would refuse first on the PATH, so the stub runs the recorded one.
    real_git = shutil.which("git", path=ENV.get("PATH", "")) or "git"
    repo, remote = unrecorded("v34a")
    planted = TMP / "v34a-bin"
    planted.mkdir()
    write(planted, "python3", FAKE_PY_EXIT.format(0))
    os.chmod(planted / "python3", 0o755)
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v34-u",
               extra={"PATH": str(planted) + os.pathsep + ENV.get("PATH", "")})
    check("V34/path-python3-planted-refused",
          (done.returncode != 0, "missing" in done.stderr, lacks(remote, "refs/heads/v34-u")), (True, True, ""))
    repo, remote = unrecorded("v34b")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v34-u", extra={"GIT_EXEC_PATH": str(planted)})
    check("V34/exec-path-python3-planted-refused",
          (done.returncode != 0, "GIT_EXEC_PATH" in done.stderr, lacks(remote, "refs/heads/v34-u")), (True, True, ""))
    repo, remote = unrecorded("v34c")
    fake = TMP / "v34c-exec"
    fake.mkdir()
    write(fake, "git", FAKE_GIT.format(real=real_git))
    os.chmod(fake / "git", 0o755)
    done = run([real_git, "push", "-q", "origin", "HEAD:refs/heads/v34-u"], repo, {"GIT_EXEC_PATH": str(fake)})
    check("V34/exec-path-git-planted-refused",
          (done.returncode != 0, "GIT_EXEC_PATH" in done.stderr, lacks(remote, "refs/heads/v34-u")), (True, True, ""))
    repo = fixture(base)
    remote = bare_remote(repo, "v34d")
    refusing = TMP / "v34d-bin"
    refusing.mkdir()
    write(refusing, "python3", FAKE_PY_EXIT.format(1))
    os.chmod(refusing / "python3", 0o755)
    stage(repo, "b.txt", "v34d\n")
    gate(repo, "run")
    commit(repo, "v34d recorded")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v34-r",
               extra={"PATH": str(refusing) + os.pathsep + ENV.get("PATH", "")})
    check("V34/flip-recorded-pushes-past-planted-python3",
          (done.returncode, lacks(remote, "refs/heads/v34-r") == head(repo)), (0, True))
    stub = (repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")
    check("V34/stub-runs-recorded-programs",
          ("\naiqt_git='/" in stub, "\naiqt_python='/" in stub, "\nPATH='/" in stub,
           '"$aiqt_python" -I -B "$tmp"' in stub, "\n$verify_bound python3 " in stub), (True, True, True, True, False))
    hook = repo / ".git" / "hooks" / "pre-commit"
    text = hook.read_text(encoding="utf-8")
    start = text.find("\naiqt_python='")
    got = None  # a stub that records no program (an earlier hook): the row fails, it does not stop the run
    if start >= 0:
        start += len("\naiqt_python='")
        hook.write_text(text[:start] + str(TMP / "v34-no-such-python3") + text[text.index("'", start):],
                        encoding="utf-8")
        done = run(["sh", str(hook)], repo)
        got = (done.returncode, "recorded at install" in done.stderr)
    check("V34/recorded-program-missing-cannot-evaluate", got, (2, True))


def rows_symbolic_refs(base, unrecorded, lacks):
    # V35:a branch on the remote that is a symbolic ref to a pull-request ref counted
    # as proof, so an unrecorded commit only that pull-request ref holds pushed. The proof reads symbolic refs
    # (protocol v2 ls-remote --symref) and drops a branch or tag naming a ref outside refs/heads and refs/tags:
    # the push is refused and the remote never gets the branch. The flip: a symbolic branch naming a tag that
    # holds the commit leaves it pushing.
    repo, remote = unrecorded("v35")
    git_out(repo, "push", "-q", "--no-verify", "origin", "HEAD:refs/pull/1/head")
    git_out(remote, "symbolic-ref", "refs/heads/alias", "refs/pull/1/head")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v35-u")
    check("V35/symbolic-branch-to-pull-ref-refused",
          (done.returncode != 0, "missing" in done.stderr, lacks(remote, "refs/heads/v35-u")), (True, True, ""))
    git_out(repo, "push", "-q", "--no-verify", "origin", "HEAD:refs/tags/v35-t")
    git_out(remote, "symbolic-ref", "refs/heads/alias2", "refs/tags/v35-t")
    git_out(remote, "update-ref", "-d", "refs/pull/1/head")
    done = git(repo, "push", "-q", "origin", "HEAD:refs/heads/v35-u")
    check("V35/flip-symbolic-branch-to-tag-pushes",
          (done.returncode, lacks(remote, "refs/heads/v35-u") == head(repo)), (0, True))


# --- the installer -----------------------------------------------------------------------------------------
def rows_install(base):
    repo = fixture(base, installed=False)
    done = gate(repo, "install")
    hooks = repo / ".git" / "hooks"
    stubs = [hooks / name for name in ("pre-commit", "pre-merge-commit", "pre-push")]
    want = [gate(repo, "stub", path.name).stdout for path in stubs]
    check("I1/install-exit", done.returncode, 0)
    check("I1/stubs", [(path.read_text(encoding="utf-8"), os.access(path, os.X_OK)) for path in stubs],
          [(text, True) for text in want])
    inodes = [os.stat(path).st_ino for path in stubs]
    done = gate(repo, "install")
    check("I1/rerun-no-op", (done.returncode, [os.stat(path).st_ino for path in stubs]), (0, inodes))
    repo = fixture(base, installed=False)
    git_out(repo, "config", "core.hooksPath", "elsewhere")
    done = gate(repo, "install")
    check("I2/hookspath-refused", (done.returncode, "core.hooksPath" in done.stderr,
                                    (repo / ".git" / "hooks" / "pre-commit").exists()), (2, True, False))
    git_out(repo, "config", "--unset", "core.hooksPath")
    check("I2/flip-unset-installs", gate(repo, "install").returncode, 0)
    repo = fixture(base, installed=False)
    hooks = repo / ".git" / "hooks"
    real = repo / ".git" / "hooks-real"
    hooks.rename(real)
    os.symlink(real, hooks)
    done = gate(repo, "install")
    check("I3/symlink-hooks-refused", (done.returncode, (real / "pre-commit").exists()), (2, False))
    os.unlink(hooks)
    real.rename(hooks)
    check("I3/flip-real-dir-installs", gate(repo, "install").returncode, 0)
    repo = fixture(base, installed=False)
    foreign = repo / ".git" / "hooks" / "pre-commit"
    foreign.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    done = gate(repo, "install")
    check("I4/foreign-refused", (done.returncode, foreign.read_text(encoding="utf-8"),
                                 (repo / ".git" / "hooks" / "pre-push").exists()), (2, "#!/bin/sh\nexit 0\n", False))
    os.unlink(foreign)
    check("I4/flip-removed-installs", gate(repo, "install").returncode, 0)
    repo = fixture(base, installed=False)
    opf = repo / ".git" / "hooks" / "pre-commit"
    opf.write_text(OPF_STUB, encoding="utf-8")
    done = gate(repo, "install")
    check("I5/opf-stub-refused", (done.returncode, opf.read_text(encoding="utf-8") == OPF_STUB), (2, True))
    check("I5/chaining-step-printed", ("OPF pre-commit stub" in done.stderr, "by hand" in done.stderr), (True, True))
    os.unlink(opf)
    check("I5/flip-removed-installs", gate(repo, "install").returncode, 0)
    repo = fixture(base, installed=False)
    os.unlink(repo / ".aiqt" / "gate-record.toml")
    done = gate(repo, "install")
    check("I6/no-config-refused", (done.returncode, (repo / ".git" / "hooks" / "pre-commit").exists()), (2, False))
    git_out(repo, "checkout", "--", ".aiqt/gate-record.toml")
    check("I6/flip-config-installs", gate(repo, "install").returncode, 0)


def _expected_check_ids():
    import tomllib
    data = None
    try:
        data = tomllib.loads(CHECKS_TOML.read_text(encoding="utf-8"))
    except (ValueError, RecursionError, MemoryError, OSError) as exc:
        HARNESS_ERRORS.append("cannot read {} ({})".format(CHECKS_TOML, type(exc).__name__))
    if data is None:
        return None
    for suite in data.get("suite", []):
        if suite.get("id") == SUITE_ID:
            return set(suite["expected-check-ids"])
    HARNESS_ERRORS.append("no [[suite]] {} in {}".format(SUITE_ID, CHECKS_TOML))
    return None


def run_checks():
    global ENV, TMP
    from _git_fixture_env import git_fixture_env
    TMP = Path(tempfile.mkdtemp(prefix="aiqt-gate-record-selftest-"))
    try:
        ENV = git_fixture_env(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                              GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                              TMPDIR=str(TMP))
        base = make_base()
        rows_recorder(base)
        rows_verify(base)
        rows_class(base)
        rows_install(base)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        # The fixture HOME _git_fixture_env made for this process: its atexit removal never runs here, since
        # the direct entry ends with os._exit (without this, one aiqt-git-fixture-home-* was left per run).
        import _git_fixture_env
        if _git_fixture_env._SCRATCH_HOME is not None:
            shutil.rmtree(_git_fixture_env._SCRATCH_HOME, ignore_errors=True)


def main(report_path=None):
    if report_path is not None:
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        run_checks()
        if not HARNESS_ERRORS:
            expected = _expected_check_ids()
            if expected is not None:
                for check_id in sorted(expected - _EXECUTED_SET):
                    FAILURES.append("execution-set/missing: {}".format(check_id))
                for check_id in sorted(_EXECUTED_SET - expected):
                    FAILURES.append("execution-set/extra: {}".format(check_id))
    except BaseException as exc:  # every failure is a harness error, never a pass
        HARNESS_ERRORS.append("uncaught {}: {}".format(type(exc).__name__, exc))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
    elif not HARNESS_ERRORS:
        print("SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
              "tools/selftest_checks.toml".format(len(EXECUTED)))
    for error in HARNESS_ERRORS:
        print("SELF-TEST HARNESS ERROR: " + error)
    sys.stdout.flush()
    return 1 if FAILURES else (2 if HARNESS_ERRORS else 0)


if __name__ == "__main__":
    argv = sys.argv[1:]
    report = None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        report = argv[1]
    elif argv:
        print("usage: selftest_gate_record.py [--execution-report ABS_PATH]")
        sys.stdout.flush()
        os._exit(2)
    code = main(report)
    if report is not None:
        _selftest_exit_report.exit_with(code)
    os._exit(code)
