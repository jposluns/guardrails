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
  X1-X3  the pinned residuals: --no-verify, core.hooksPath on the command line, a forged pass line.
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
import subprocess
import tempfile
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
    check("W1/keys", list(last), ["format", "tree", "object_format", "tree_after", "set", "argv", "exit", "result",
                                  "started", "finished", "recorder"])
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
    for installed in (True, False):
        repo = fixture(base, installed)
        stage(repo, ".aiqt/gate-record.toml", LOWERED)
        gate(repo, "run")
        before = head(repo)
        done = commit(repo, "v8")
        if installed:
            check("V8/recorded-lowered-set", records(repo)[-1]["set"], "suite-x")
            check("V8/refused-config-from-head", refused_commit(repo, before, done, "a different check set"),
                  (True, True))
        else:
            check("V8/flip-no-stub-commits", done.returncode, 0)
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
    rows_log_tamper(base)
    for installed in (True, False):
        repo = fixture(base, installed)
        forged = TMP / "forged-{}.jsonl".format(installed)
        stage(repo, "b.txt", "b\n")
        tree = git_out(repo, "write-tree")
        forged.write_text(forged_line(tree), encoding="ascii")
        extra = {"AIQT_GATE_RECORD": str(forged), "AIQT_GATE_RECORD_LOG": str(forged), "AIQT_GATE_TREE": tree}
        before = head(repo)
        done = git(repo, "commit", "-q", "-m", "v14", extra=extra)
        if installed:
            check("V14/forged-env-ignored", refused_commit(repo, before, done, "missing"), (True, True))
        else:
            check("V14/flip-no-stub-commits", done.returncode, 0)
    rows_push(base)
    rows_merge(base)


def forged_line(tree):
    record = {"format": "aiqt-gate-record/1", "tree": tree, "object_format": "sha1", "tree_after": tree, "set": "full",
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
    log.write_text(forged_line(tree), encoding="ascii")
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
        rows_install(base)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


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
