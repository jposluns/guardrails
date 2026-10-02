#!/usr/bin/env python3
"""Merge train (opt-in): refresh every open PR's head branch with the base branch, taking the base's
side for the declared GENERATED files only, regenerating and checking them, and refusing everything
else. It does nothing unless the project commits .aiqt/merge-train.toml on its base branch.

  merge_train.py [--repo DIR] [--pr N]... [--apply]   dry run by default; --apply commits and pushes
  merge_train.py --self-test [--red-on-revert]        hermetic fixture self-test (local bare remote, fake gh)
  merge_train.py --execution-report ABS_PATH          the self-test, also writing the executed check ids

Per open PR, in PR-number order: the worktree on the PR's branch must be idle (no other process with its
cwd or argv in the worktree, no git operation marker, the optional [busy].probe exits 0; an unreadable
process table counts as busy) and clean, and local HEAD must equal the remote PR head. With --apply it
then runs `git merge --no-commit --no-ff <M>` (M is the base tip fetched once per run), takes the base's
side for conflicted generated paths, refuses any other conflict, always runs the regenerate commands,
runs the check commands, proves nothing outside the generated set differs from the automatic merge,
re-checks busy and branch, commits as the configured maintainer and pushes plainly (fast-forward only)
to the PR's own head branch. One observer reads the remote after the push whatever its exit code.

The config is read from the fetched base tip (`git show <M>:.aiqt/merge-train.toml`), never from a PR
branch or the checkout. The branch pushed to is the worktree's own symbolic ref, re-confirmed against gh
before the commit and before the push; the tool takes no branch-name argument.

Stdout carries one JSON line per PR (schema merge-train/1); stderr carries one human line per PR.
Exit codes: 0 every PR current, pushed, already pushed or (dry run) mergeable; 1 any PR refused or
skipped; 2 the run failed closed and touched no worktree (no config, malformed config, lock held, gh or
fetch failure, a possibly truncated PR list, bad arguments); 3 restore-failed (a worktree could not be
proven back to its pre-run state; the run stops, the state marker is kept, nothing is deleted).

THREAT MODEL (cooperative): agents on a single-operator host. The repository's local config, the
operator's global and system config, hooks and the object store are trusted; the tool guards against
accidents and odd local state, not hostile repository state. Disclosed residuals: a worker started
between the last busy check and the commit is not detected (the orchestrator, as sole dispatcher, does
not dispatch during a train run); the process scan sees only processes of the tool's own uid and
excludes the tool and its ancestors; fork PRs are refused (fork-pr), not supported; v1 supports the
remote "origin" and the base "main" only; the regenerate and check commands run the merged tree's copy
of the generators, which may include the PR's own changes; review_carry is information only.

The tool never force-pushes (no --force, no lease flag, no + push refspec), rebases, cherry-picks,
stashes, cleans, runs reset --hard, prunes worktrees, resolves a non-generated conflict, pushes to or
checks out the base branch, edits PR metadata, or reads an ambient git identity.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("error: merge_train.py requires Python 3.11+ (tomllib).")

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "merge-train/1"
CONFIG_PATH = ".aiqt/merge-train.toml"
MARKER_NAME = "merge-train.json"
LOCK_NAME = "merge-train.lock"
PR_LIST_LIMIT = 200
GIT_TIMEOUT = 120
OK_RESULTS = ("current", "pushed", "already-pushed", "would-merge")
OPERATION_MARKERS = ("index.lock", "MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD",
                     "REVERT_HEAD", "BISECT_LOG")
GLOB_CHARS = set("*?[]!" + chr(123) + chr(125))
CONFIG_KEYS = frozenset(("version", "remote", "base", "commit", "generated", "busy", "limits"))
TABLE_KEYS = dict(commit=frozenset(("name", "email")),
                  generated=frozenset(("paths", "regenerate", "check")),
                  busy=frozenset(("probe",)),
                  limits=frozenset(("command_timeout_seconds", "network_timeout_seconds")))
REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")

# Every argv the tool hands to git, for the self-test's forbidden-operation audit.
GIT_AUDIT = []


class FailClosed(Exception):
    """The whole run stops before touching any worktree (exit 2)."""


class Refuse(Exception):
    """One PR is refused; its worktree is restored and the run continues."""

    def __init__(self, status, reason=""):
        Exception.__init__(self, status)
        self.status = status
        self.reason = reason


class RestoreFailed(Exception):
    """A worktree could not be proven back to its snapshot (exit 3)."""


def _now():
    return time.strftime("[%Y-%m-%dT%H:%MZ]", time.gmtime())


def _clean_env(extra=None):
    """The caller's environment with every GIT_-prefixed variable dropped (no repository-selecting or
    config-injecting variable reaches git), plus explicit additions."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or dict())
    return env


def _forbidden(args):
    """The argv audit enforced on every git call: no force push, no + push refspec, no rebase,
    cherry-pick, stash, clean, gc, worktree prune, and no reset other than reset --keep."""
    words = list(args)
    if words[:1] in (["rebase"], ["stash"], ["clean"], ["cherry-pick"], ["gc"], ["prune"]):
        return True
    if words[:2] == ["worktree", "prune"]:
        return True
    if words[:1] == ["reset"] and "--keep" not in words:
        return True
    if words[:1] == ["push"] and any(w.startswith("--force") or w == "-f" or w.startswith("+")
                                     for w in words[1:]):
        return True
    return False


def _git(cwd, *args, extra_env=None, timeout=GIT_TIMEOUT, ok=(0,)):
    """The single pinned git funnel: auto-maintenance pinned off in option position, scrubbed GIT_*,
    a timeout. Returns (returncode, stdout bytes, stderr text); raises Refuse("git-failed") on a
    timeout and on an exit code outside ok when ok is not None."""
    if _forbidden(args):
        raise AssertionError("forbidden git operation refused by the argv audit: %r" % (args,))
    argv = ["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
    argv += list(args)
    GIT_AUDIT.append(list(args))
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=_clean_env(extra_env), capture_output=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refuse("git-failed", "git %s: %s" % (" ".join(args[:2]), exc))
    if ok is not None and proc.returncode not in ok:
        raise Refuse("git-failed", "git %s exited %d: %s" % (
            " ".join(args[:2]), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip()[:300]))
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def _git_text(cwd, *args, **kw):
    return _git(cwd, *args, **kw)[1].decode("utf-8", "replace").strip()


def _run_external(argv, cwd, timeout, extra_env=None):
    """Launch a non-git program named by the trusted config, or the gh CLI: an argv list, no shell, a
    timeout. Returns (returncode, or None on a timeout or launch error, stdout text, stderr text)."""
    try:
        proc = subprocess.run(list(argv), cwd=str(cwd), env=_clean_env(extra_env),
                              capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


# ---------------------------------------------------------------- guards (the GUARDS table below)

def guard_config_fail_closed(data):
    """Raise FailClosed unless data is a well-formed version-1 merge-train config."""
    def bad(msg):
        raise FailClosed("malformed %s: %s" % (CONFIG_PATH, msg))

    def str_list(value, where, allow_empty=False):
        if not isinstance(value, list) or (not value and not allow_empty):
            bad("%s must be a%s list of strings" % (where, "" if allow_empty else " non-empty"))
        for item in value:
            if not isinstance(item, str) or not item or "\0" in item:
                bad("%s must hold non-empty strings" % where)

    def commands(value, where):
        if not isinstance(value, list) or not value:
            bad("%s must be a non-empty list of commands" % where)
        for command in value:
            str_list(command, where + " command")

    if not isinstance(data, dict):
        bad("not a table")
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        bad("unknown key(s) %s" % sorted(unknown))
    for key in ("version", "remote", "base", "commit", "generated"):
        if key not in data:
            bad("missing key %s" % key)
    if type(data["version"]) is not int or data["version"] != 1:
        bad("version must be the integer 1")
    for key in ("remote", "base"):
        value = data[key]
        if not isinstance(value, str) or not REF_NAME.match(value) or ".." in value:
            bad("%s must be a plain name" % key)
    for table, keys in TABLE_KEYS.items():
        if table not in data:
            continue
        if not isinstance(data[table], dict):
            bad("[%s] must be a table" % table)
        unknown = set(data[table]) - keys
        if unknown:
            bad("unknown key(s) in [%s]: %s" % (table, sorted(unknown)))
    for key in ("name", "email"):
        value = data["commit"].get(key)
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "<>\n\r\0"):
            bad("[commit].%s is required and must be a plain string" % key)
    generated = data["generated"]
    for key in ("paths", "regenerate", "check"):
        if key not in generated:
            bad("missing [generated].%s" % key)
    str_list(generated["paths"], "[generated].paths")
    seen = set()
    for path in generated["paths"]:
        parts = path.split("/")
        if (path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts)
                or GLOB_CHARS & set(path)):
            bad("[generated].paths entry %r must be an exact relative path" % path)
        if path in seen:
            bad("[generated].paths entry %r appears twice" % path)
        seen.add(path)
    commands(generated["regenerate"], "[generated].regenerate")
    commands(generated["check"], "[generated].check")
    if "busy" in data and "probe" in data["busy"]:
        str_list(data["busy"]["probe"], "[busy].probe", allow_empty=True)
    for key, value in data.get("limits", dict()).items():
        if type(value) is not int or value <= 0:
            bad("[limits].%s must be a positive integer" % key)


def guard_config_source(root, base_sha):
    """The config TEXT, read from the fetched base tip only (never the checkout or a PR branch);
    None when the base tip carries no config."""
    code, out, _err = _git(root, "show", "%s:%s" % (base_sha, CONFIG_PATH), ok=None)
    if code != 0:
        return None
    return out.decode("utf-8", "replace")


def guard_busy(wt, git_dir, probe, timeout, own_merge):
    """Refuse busy, busy-unknown or in-progress-operation for a worktree in use. own_merge excuses
    the MERGE_HEAD of the tool's own recorded merge (the pre-commit re-check)."""
    for name in OPERATION_MARKERS:
        if name == "MERGE_HEAD" and own_merge:
            continue
        if os.path.lexists(os.path.join(git_dir, name)):
            status = "busy" if name == "index.lock" else "in-progress-operation"
            raise Refuse(status, "git operation marker %s present" % name)
    if probe:
        argv = [arg.replace("{worktree}", str(wt)) for arg in probe]
        code, _out, err = _run_external(argv, wt, timeout)
        if code != 0:
            raise Refuse("busy", "busy probe exited %s: %s" % (code, err.strip()[:200]))
    users = _process_users(str(wt))
    if users is None:
        raise Refuse("busy-unknown", "the process table could not be read")
    if users:
        raise Refuse("busy", "process(es) %s use the worktree" % ", ".join(users[:5]))


def guard_dirty(wt):
    """Refuse dirty unless the worktree has no staged, unstaged or untracked change."""
    out = _git(wt, "status", "--porcelain=v2", "-z", "--untracked-files=all")[1]
    entries = [e.decode("utf-8", "replace") for e in out.split(b"\0") if e]
    if entries:
        raise Refuse("dirty", "; ".join(entries[:5]))


def guard_branch_match(wt, branch, gh_view):
    """Refuse branch-mismatch unless the worktree's symbolic ref, the listed head branch and gh's
    fresh view all name the same branch of an open PR."""
    code, out, _err = _git(wt, "symbolic-ref", "--quiet", "--short", "HEAD", ok=None)
    if code != 0:
        raise Refuse("branch-mismatch", "worktree HEAD is detached")
    current = out.decode("utf-8", "replace").strip()
    if current != branch or gh_view.get("headRefName") != branch:
        raise Refuse("branch-mismatch", "worktree on %r, PR head %r, gh now %r" % (
            current, branch, gh_view.get("headRefName")))
    if gh_view.get("state") != "OPEN":
        raise Refuse("branch-mismatch", "PR is no longer open")


def guard_source_conflict(unmerged, generated):
    """Refuse source-conflict for any unmerged path outside the generated set."""
    outside = sorted(p for p in unmerged if p not in generated)
    if outside:
        raise Refuse("source-conflict", "conflicts outside the generated set: %s" % (
            ", ".join(outside[:10])))


def guard_regenerate_exit(code, command, err):
    if code != 0:
        raise Refuse("regenerate-failed", "%s exited %s: %s" % (" ".join(command), code,
                                                               err.strip()[:300]))


def guard_check_exit(code, command, err):
    if code != 0:
        raise Refuse("check-failed", "%s exited %s: %s" % (" ".join(command), code,
                                                          err.strip()[:300]))


def guard_fixpoint(wt):
    """After regenerate, add and check: nothing unstaged or unmerged (regenerate-not-fixpoint) and
    no untracked file (undeclared-write)."""
    out = _git(wt, "status", "--porcelain=v2", "-z", "--untracked-files=all")[1]
    for entry in [e.decode("utf-8", "replace") for e in out.split(b"\0") if e]:
        if entry.startswith("? "):
            raise Refuse("undeclared-write", "untracked file %s" % entry[2:])
        if entry[:1] in ("1", "2") and entry[3:4] != ".":
            raise Refuse("regenerate-not-fixpoint", "unstaged change: %s" % entry[-200:])
        if entry[:1] == "u":
            raise Refuse("regenerate-not-fixpoint", "unmerged entry left: %s" % entry[-200:])


def guard_push_restore(wt, old, observed, new):
    """After a rejected or overtaken push, restore the worktree to the old head (reset --keep keeps
    every local change and leaves the new commit in the reflog)."""
    if observed != new:
        _git(wt, "reset", "--keep", old)


def guard_abort_on_refusal(wt, git_dir, old):
    """Undo the tool's own merge or commit after a refusal: merge --abort mid-merge, reset --keep to
    the old head after a commit. Never reset --hard, clean or stash."""
    if os.path.lexists(os.path.join(git_dir, "MERGE_HEAD")):
        _git(wt, "merge", "--abort", ok=None)
    if _git_text(wt, "rev-parse", "HEAD") != old:
        _git(wt, "reset", "--keep", old, ok=None)


def guard_reconcile(ctx, marker):
    """Reconcile a marker left by an earlier run against git before anything else. Returns None to
    proceed normally, or a finished partial report (already-pushed, pushed). Raises
    Refuse(state-mismatch) for a marker that disagrees with git and Refuse(in-progress-operation)
    for a merge the tool did not start (never aborted)."""
    wt, git_dir = ctx["wt"], ctx["git_dir"]
    has_merge = os.path.lexists(os.path.join(git_dir, "MERGE_HEAD"))
    if marker is None:
        if has_merge:
            raise Refuse("in-progress-operation", "a merge not started by merge-train is in progress")
        return None
    head = _git_text(wt, "rev-parse", "HEAD")
    if marker.get("pr") != ctx["pr"] or marker.get("branch") != ctx["branch"]:
        raise Refuse("state-mismatch", "the marker names another PR or branch")
    if marker.get("phase") == "merging" and head == marker.get("old") and has_merge:
        if _read_text(os.path.join(git_dir, "MERGE_HEAD")).strip() != marker.get("base_sha"):
            raise Refuse("state-mismatch", "MERGE_HEAD is not the recorded base")
        _git(wt, "merge", "--abort")
        guard_dirty(wt)
        _remove_marker(git_dir)
        return None
    if marker.get("phase") == "committed" and head == marker.get("new") and not has_merge:
        remote = _remote_head(ctx)
        if remote == marker["new"]:
            _remove_marker(git_dir)
            return dict(result="already-pushed", old_head=marker["old"], new_head=marker["new"],
                        reason="an earlier run pushed %s" % marker["new"])
        if remote == marker["old"]:
            ctx["old"] = marker["old"]
            _run_checks(ctx)
            return _push_and_observe(ctx, marker["old"], marker["new"])
        raise Refuse("state-mismatch", "remote at %s, marker old %s new %s" % (
            remote, marker["old"], marker["new"]))
    raise Refuse("state-mismatch", "marker phase %r disagrees with git (HEAD %s)" % (
        marker.get("phase"), head))


# Every guard is reached through this table, so --red-on-revert can replace each one in turn.
GUARDS = dict([
    ("config-fail-closed", guard_config_fail_closed),
    ("config-source", guard_config_source),
    ("busy", guard_busy),
    ("dirty", guard_dirty),
    ("branch-match", guard_branch_match),
    ("source-conflict", guard_source_conflict),
    ("regenerate-exit", guard_regenerate_exit),
    ("check-exit", guard_check_exit),
    ("fixpoint", guard_fixpoint),
    ("push-restore", guard_push_restore),
    ("abort-on-refusal", guard_abort_on_refusal),
    ("reconcile", guard_reconcile),
])


def _guard(name):
    return GUARDS[name]


# ---------------------------------------------------------------- helpers

def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _process_users(wt):
    """pid:name of every same-uid process, other than this process and its ancestors, whose cwd is
    inside wt or whose argv names a path inside wt; None when the process table cannot be read."""
    if not os.path.isdir("/proc/self"):
        return None
    skip = set()
    pid = os.getpid()
    while pid > 1 and pid not in skip:
        skip.add(pid)
        stat = _read_text("/proc/%d/stat" % pid)
        try:
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            break
    real = os.path.realpath(wt)

    def inside(path):
        return path == real or path.startswith(real + os.sep)

    users = []
    uid = os.getuid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for name in entries:
        if not name.isdigit() or int(name) in skip:
            continue
        base = "/proc/" + name
        try:
            if os.stat(base).st_uid != uid:
                continue
            cwd = os.readlink(base + "/cwd")
            with open(base + "/cmdline", "rb") as handle:
                argv = [a.decode("utf-8", "replace") for a in handle.read().split(b"\0") if a]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            return None
        if inside(cwd) or any(inside(os.path.realpath(a)) for a in argv if a.startswith("/")):
            users.append("%s:%s" % (name, os.path.basename(argv[0]) if argv else "?"))
    return users


def _read_marker(git_dir):
    path = os.path.join(git_dir, MARKER_NAME)
    if not os.path.lexists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return dict(phase="unreadable")
    return data if isinstance(data, dict) else dict(phase="unreadable")


def _write_marker(git_dir, data):
    """Temp file, fsync, rename: a reader sees the old marker or the new one, never a torn one."""
    fd, tmp = tempfile.mkstemp(prefix=".merge-train-", dir=git_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, os.path.join(git_dir, MARKER_NAME))


def _remove_marker(git_dir):
    try:
        os.unlink(os.path.join(git_dir, MARKER_NAME))
    except FileNotFoundError:
        pass


def _remote_head(ctx):
    """The remote branch tip through ls-remote: a SHA, "" when absent, None when unreadable."""
    code, out, _err = _git(ctx["wt"], "ls-remote", "--", ctx["remote"],
                           "refs/heads/" + ctx["branch"], ok=None, timeout=ctx["net_timeout"])
    if code != 0:
        return None
    lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return ""
    sha = lines[0].split()[0]
    return sha if SHA.match(sha) else None


def _snapshot(wt):
    """HEAD, the symbolic ref, the index entries, the full status with ignored files, and a SHA-256
    of every tracked and untracked file."""
    head = _git_text(wt, "rev-parse", "HEAD")
    code, out, _err = _git(wt, "symbolic-ref", "--quiet", "HEAD", ok=None)
    index = _git(wt, "ls-files", "-s", "-z")[1]
    status = _git(wt, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored")[1]
    files = _git(wt, "ls-files", "-z", "--cached", "--others", "--exclude-standard")[1]
    digests = dict()
    for raw in sorted(set(f for f in files.split(b"\0") if f)):
        path = os.path.join(str(wt), raw.decode("utf-8", "surrogateescape"))
        try:
            if os.path.islink(path):
                digests[raw] = "link:" + os.readlink(path)
            elif os.path.isfile(path):
                with open(path, "rb") as handle:
                    digests[raw] = hashlib.sha256(handle.read()).hexdigest()
            else:
                digests[raw] = "absent"
        except OSError as exc:
            digests[raw] = "error:" + str(exc)
    return dict(head=head, symref=out.strip() if code == 0 else None, index=index, status=status,
                files=digests)


def _snapshot_diff(before, after):
    diffs = [k for k in ("head", "symref", "index", "status") if before[k] != after[k]]
    paths = sorted(set(before["files"]) | set(after["files"]))
    diffs += [p.decode("utf-8", "replace") for p in paths
              if before["files"].get(p) != after["files"].get(p)]
    return diffs


def _unmerged(wt):
    """path -> set of stages, from ls-files -u -z."""
    result = dict()
    for entry in _git(wt, "ls-files", "-u", "-z")[1].split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            result.setdefault(path.decode("utf-8", "surrogateescape"), set()).add(
                int(meta.split()[2]))
    return result


def _index_entries(wt):
    """path -> list of (mode, sha, stage) for every index entry."""
    result = dict()
    for entry in _git(wt, "ls-files", "-s", "-z")[1].split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            result.setdefault(path.decode("utf-8", "surrogateescape"), []).append(
                tuple(meta.decode().split()))
    return result


def _changed_lines(wt, left, right, generated):
    """Per-file ordered +/- lines of git diff -U0 (line numbers dropped), generated paths excluded;
    right None compares left with the index."""
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames"]
    args += ["--cached", left] if right is None else [left, right]
    args += ["--", "."] + [":(exclude,literal)" + p for p in generated]
    out = _git(wt, *args)[1].decode("utf-8", "replace")
    files, current = dict(), None
    for line in out.splitlines():
        if line.startswith("diff --git "):
            current = files.setdefault(line, [])
        elif line.startswith(("+++", "---", "@@", "index ", "new file", "deleted file")):
            continue
        elif current is not None and line[:1] in ("+", "-"):
            current.append(line)
    return files


def _run_checks(ctx):
    for command in ctx["cfg"]["generated"]["check"]:
        code, _out, err = _run_external(command, ctx["wt"], ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("check-exit")(code, command, err)


def _push_and_observe(ctx, old, new):
    """Plain fast-forward push of new to the PR's own branch, then one observer over the remote."""
    wt, branch = ctx["wt"], ctx["branch"]
    _git(wt, "push", "--porcelain", "--no-follow-tags", "--", ctx["remote"],
         "%s:refs/heads/%s" % (new, branch), ok=None, timeout=ctx["net_timeout"])
    observed = _remote_head(ctx)
    if observed == new:
        _remove_marker(ctx["git_dir"])
        return dict(result="pushed", old_head=old, new_head=new, reason="")
    if observed is None:
        raise Refuse("push-unknown", "remote unreadable after the push; commit and marker kept")
    _guard("push-restore")(wt, old, observed, new)
    _remove_marker(ctx["git_dir"])
    status = "push-rejected" if observed == old else "remote-changed"
    raise Refuse(status, "remote at %s" % (observed or "(absent)"))


# ---------------------------------------------------------------- the run

def load_config(root, base_sha):
    text = _guard("config-source")(root, base_sha)
    if text is None:
        raise FailClosed("merge-train not enabled: %s is absent on the base tip %s" % (
            CONFIG_PATH, base_sha))
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise FailClosed("malformed %s: %s" % (CONFIG_PATH, exc))
    _guard("config-fail-closed")(data)
    return data


def _worktrees(root):
    """[dict(path, branch)] from git worktree list --porcelain; a detached worktree has branch None."""
    rows, row = [], None
    for line in _git_text(root, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if row:
                rows.append(row)
            row = None
        elif line.startswith("worktree "):
            row = dict(path=line[len("worktree "):], branch=None)
        elif line.startswith("branch refs/heads/") and row is not None:
            row["branch"] = line[len("branch refs/heads/"):]
    return rows


def _gh_json(argv, root, timeout):
    code, out, err = _run_external(argv, root, timeout)
    if code != 0:
        raise FailClosed("gh failed (%s): %s" % (code, err.strip()[:300]))
    try:
        return json.loads(out)
    except ValueError as exc:
        raise FailClosed("gh returned malformed JSON: %s" % exc)


def _gh_view(ctx):
    try:
        data = _gh_json(["gh", "pr", "view", str(ctx["pr"]), "--json",
                         "headRefName,headRefOid,state"], ctx["root"], ctx["net_timeout"])
    except FailClosed as exc:
        raise Refuse("branch-mismatch", "gh pr view unreadable: %s" % exc)
    if not isinstance(data, dict):
        raise Refuse("branch-mismatch", "gh pr view returned no object")
    return data


def _process_pr(ctx, apply):
    """One PR. Returns a partial report; raises Refuse for a refusal and RestoreFailed."""
    wt, git_dir = ctx["wt"], ctx["git_dir"]
    marker = _read_marker(git_dir)
    if marker is not None and not apply:
        raise Refuse("state-mismatch", "a merge-train marker is present; rerun with --apply")
    done = _guard("reconcile")(ctx, marker)
    if done is not None:
        return done
    view = _gh_view(ctx)
    _guard("branch-match")(wt, ctx["branch"], view)
    remote = _remote_head(ctx)
    if remote != ctx["head_oid"] or view.get("headRefOid") != ctx["head_oid"]:
        raise Refuse("pr-head-moved", "remote head %s, gh %s, listed %s" % (
            remote, view.get("headRefOid"), ctx["head_oid"]))
    _guard("busy")(wt, git_dir, ctx["probe"], ctx["cmd_timeout"], False)
    _guard("dirty")(wt)
    old = _git_text(wt, "rev-parse", "HEAD")
    ctx["old"] = old
    if old != remote:
        _git(wt, "fetch", "--no-tags", "--", ctx["remote"], "refs/heads/" + ctx["branch"], ok=None,
             timeout=ctx["net_timeout"])
        if _git(wt, "merge-base", "--is-ancestor", old, remote, ok=None)[0] == 0:
            raise Refuse("worktree-behind", "local %s behind remote %s" % (old, remote))
        if _git(wt, "merge-base", "--is-ancestor", remote, old, ok=None)[0] == 0:
            raise Refuse("unpushed-commits", "local %s ahead of remote %s" % (old, remote))
        raise Refuse("diverged", "local %s, remote %s" % (old, remote))
    base = ctx["base_sha"]
    if _git(wt, "merge-base", "--is-ancestor", base, old, ok=None)[0] == 0:
        return dict(result="current", old_head=old, new_head=None, reason="")
    if not apply:
        return dict(result="would-merge", old_head=old, new_head=None,
                    reason="dry run: would merge %s (rerun with --apply)" % base)
    before = _snapshot(wt)
    _write_marker(git_dir, dict(pr=ctx["pr"], branch=ctx["branch"], old=old, base_sha=base,
                                phase="merging", new=None))
    try:
        return _merge_commit_push(ctx, old, base)
    except Refuse as refusal:
        if refusal.status == "push-unknown":
            raise
        if refusal.status not in ("push-rejected", "remote-changed"):
            _guard("abort-on-refusal")(wt, git_dir, old)
        diffs = _snapshot_diff(before, _snapshot(wt))
        if diffs:
            raise RestoreFailed("%s after %s: %s differ" % (ctx["branch"], refusal.status,
                                                           ", ".join(diffs[:10])))
        _remove_marker(git_dir)
        raise


def _merge_commit_push(ctx, old, base):
    wt, git_dir, generated = ctx["wt"], ctx["git_dir"], ctx["generated"]
    ident = dict(GIT_AUTHOR_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_AUTHOR_EMAIL=ctx["cfg"]["commit"]["email"],
                 GIT_COMMITTER_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_COMMITTER_EMAIL=ctx["cfg"]["commit"]["email"])
    _git(wt, "merge", "--no-commit", "--no-ff", "--no-edit", base, extra_env=ident, ok=None,
         timeout=ctx["cmd_timeout"])
    if not os.path.lexists(os.path.join(git_dir, "MERGE_HEAD")):
        raise Refuse("git-failed", "the merge did not start")
    unmerged = _unmerged(wt)
    _guard("source-conflict")(unmerged, generated)
    for path in sorted(unmerged):
        if unmerged[path] != set((1, 2, 3)):
            raise Refuse("generated-delete-conflict", path)
    for path in sorted(unmerged):
        _git(wt, "checkout", "--theirs", "--", path)
        _git(wt, "add", "--", path)
    automatic = _index_entries(wt)
    for command in ctx["cfg"]["generated"]["regenerate"]:
        code, _out, err = _run_external(command, wt, ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("regenerate-exit")(code, command, err)
    _git(wt, "add", "-A", "--", *generated)
    _run_checks(ctx)
    _guard("fixpoint")(wt)
    resolved = _index_entries(wt)
    hand = sorted(p for p in set(automatic) | set(resolved)
                  if automatic.get(p) != resolved.get(p) and p not in generated)
    if hand:
        raise Refuse("undeclared-write", "paths outside the generated set changed: %s" % (
            ", ".join(hand[:10])))
    merge_base = _git_text(wt, "merge-base", old, base)
    ctx["review_carry"] = (_changed_lines(wt, merge_base, old, generated)
                           == _changed_lines(wt, base, None, generated))
    ctx["regenerated"] = [p for p in sorted(generated) if p in resolved]
    view = _gh_view(ctx)
    _guard("busy")(wt, git_dir, ctx["probe"], ctx["cmd_timeout"], True)
    _guard("branch-match")(wt, ctx["branch"], view)
    title = "Merge %s/%s into %s (merge-train)" % (ctx["remote"], ctx["base"], ctx["branch"])
    code, _out, err = _git(wt, "commit", "--no-edit", "-m", title, "-m",
                           "Merge-train: base=%s" % base, extra_env=ident, ok=None,
                           timeout=ctx["cmd_timeout"])
    if code != 0:
        raise Refuse("hook-failed", err.strip()[:300])
    new = _git_text(wt, "rev-parse", "HEAD")
    _write_marker(git_dir, dict(pr=ctx["pr"], branch=ctx["branch"], old=old, base_sha=base,
                                phase="committed", new=new))
    view = _gh_view(ctx)
    _guard("branch-match")(wt, ctx["branch"], view)
    return _push_and_observe(ctx, old, new)


def run_train(root, apply=False, only=None):
    """The whole run under the global lock. Returns (exit code, reports, fatal message or None)."""
    reports = []
    try:
        top = _git_text(root, "rev-parse", "--show-toplevel")
        common = _git_text(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except Refuse as exc:
        return 2, reports, "not a git repository: %s" % exc.reason
    with open(os.path.join(common, LOCK_NAME), "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 2, reports, "another merge train holds %s" % LOCK_NAME
        return _run_locked(Path(top), apply, only or set(), reports)


def _run_locked(root, apply, only, reports):
    # The config lives on the base tip, so v1 fetches the conventional origin/main once, pins M, and
    # requires the config to name that same remote and base.
    remote, base = "origin", "main"
    try:
        _git(root, "fetch", "--no-tags", "--", remote,
             "+refs/heads/%s:refs/remotes/%s/%s" % (base, remote, base), timeout=GIT_TIMEOUT)
        base_sha = _git_text(root, "rev-parse", "--verify",
                             "refs/remotes/%s/%s^0" % (remote, base))
    except Refuse as exc:
        return 2, reports, "fetch failed: %s" % exc.reason
    try:
        cfg = load_config(root, base_sha)
        if cfg["remote"] != remote or cfg["base"] != base:
            raise FailClosed("v1 supports remote 'origin' and base 'main' only")
        limits = cfg.get("limits", dict())
        net_timeout = limits.get("network_timeout_seconds", 120)
        cmd_timeout = limits.get("command_timeout_seconds", 900)
        prs = _gh_json(["gh", "pr", "list", "--state", "open", "--base", base, "--json",
                        "number,headRefName,headRefOid,isCrossRepository,baseRefName",
                        "--limit", str(PR_LIST_LIMIT)], root, net_timeout)
        if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
            raise FailClosed("gh pr list did not return a list of objects")
        if len(prs) >= PR_LIST_LIMIT:
            raise FailClosed("gh returned %d PRs; the list may be truncated" % len(prs))
        for pr in prs:
            if (type(pr.get("number")) is not int or not isinstance(pr.get("headRefName"), str)
                    or not isinstance(pr.get("headRefOid"), str)):
                raise FailClosed("gh pr list row malformed: %r" % (pr,))
        trees = _worktrees(root)
    except FailClosed as exc:
        return 2, reports, str(exc)
    except Refuse as exc:
        return 2, reports, "%s: %s" % (exc.status, exc.reason)
    names = dict()
    for pr in prs:
        names.setdefault(pr["headRefName"], []).append(pr["number"])
    generated = list(cfg["generated"]["paths"])
    rc = 0
    for pr in sorted(prs, key=lambda p: p["number"]):
        if only and pr["number"] not in only:
            continue
        branch = pr["headRefName"]
        report = dict(schema=SCHEMA, pr=pr["number"], branch=branch, worktree=None, base=base_sha,
                      old_head=None, new_head=None, result=None, reason="", regenerated=[],
                      hand_resolution=False, review_carry=None)
        ctx = dict(old=None, review_carry=None, regenerated=[])
        try:
            if len(names[branch]) > 1:
                raise Refuse("ambiguous-pr", "PRs %s share the branch" % names[branch])
            if pr.get("isCrossRepository") is not False:
                raise Refuse("fork-pr", "cross-repository PRs are not supported")
            if pr.get("baseRefName") != base:
                raise Refuse("base-mismatch", "PR base %r" % pr.get("baseRefName"))
            matches = [t for t in trees if t["branch"] == branch]
            if not matches:
                raise Refuse("no-worktree", "no worktree has %s checked out" % branch)
            if len(matches) > 1:
                raise Refuse("ambiguous-worktree", ", ".join(t["path"] for t in matches))
            wt = matches[0]["path"]
            report["worktree"] = wt
            ctx.update(root=root, wt=wt, pr=pr["number"], branch=branch, head_oid=pr["headRefOid"],
                       remote=remote, base=base, base_sha=base_sha, cfg=cfg, generated=generated,
                       probe=cfg.get("busy", dict()).get("probe", []), cmd_timeout=cmd_timeout,
                       net_timeout=net_timeout,
                       git_dir=_git_text(wt, "rev-parse", "--path-format=absolute", "--git-dir"))
            report.update(_process_pr(ctx, apply))
        except Refuse as refusal:
            report["result"], report["reason"] = refusal.status, refusal.reason
            report["old_head"] = ctx["old"]
        except RestoreFailed as exc:
            report["result"], report["reason"] = "restore-failed", str(exc)
            report["old_head"] = ctx["old"]
            reports.append(report)
            return 3, reports, "restore-failed: %s" % exc
        report["review_carry"] = ctx["review_carry"]
        report["regenerated"] = ctx["regenerated"]
        if report["result"] not in OK_RESULTS:
            rc = 1
        reports.append(report)
    return rc, reports, None


def _parse_args(argv):
    opts = dict(apply=False, repo=".", prs=[])
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--apply":
            opts["apply"] = True
        elif arg == "--repo" and i + 1 < len(argv):
            i += 1
            opts["repo"] = argv[i]
        elif arg == "--pr" and i + 1 < len(argv) and argv[i + 1].isdigit() and int(argv[i + 1]):
            i += 1
            opts["prs"].append(int(argv[i]))
        else:
            return None
        i += 1
    return opts


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--self-test"], ["--self-test", "--red-on-revert"]):
        return self_test(red_on_revert="--red-on-revert" in argv)
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(report_path=argv[1])
    opts = _parse_args(argv)
    if opts is None:
        print("usage: merge_train.py [--repo DIR] [--pr N]... [--apply] | --self-test "
              "[--red-on-revert] | --execution-report ABS_PATH", file=sys.stderr)
        return 2
    rc, reports, fatal = run_train(opts["repo"], opts["apply"], set(opts["prs"]))
    for report in reports:
        print(json.dumps(report, sort_keys=True))
        print("%s PR #%d %s: %s%s" % (_now(), report["pr"], report["branch"], report["result"],
                                      " (%s)" % report["reason"] if report["reason"] else ""),
              file=sys.stderr)
    if fatal:
        print("%s merge-train: %s" % (_now(), fatal), file=sys.stderr)
    return rc


# ---------------------------------------------------------------- self-test

SUITE_ID = "merge-train-selftest"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
_RECORDER = dict(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES)

TOY_GEN = r"""import hashlib, os, sys
root = os.getcwd()
lines = []
for dirpath, dirnames, filenames in os.walk(os.path.join(root, "src")):
    dirnames.sort()
    for name in sorted(filenames):
        path = os.path.join(dirpath, name)
        with open(path, "rb") as handle:
            lines.append("%s %s" % (hashlib.sha256(handle.read()).hexdigest(),
                                    os.path.relpath(path, root)))
text = "\n".join(sorted(lines)) + "\n"
target = os.path.join(root, "gen", "digest.txt")
if "--check" in sys.argv:
    with open(target) as handle:
        sys.exit(0 if handle.read() == text else 1)
if os.environ.get("TOY_FAIL"):
    sys.exit(3)
if os.environ.get("TOY_LITTER"):
    with open(os.path.join(root, "litter.txt"), "w") as handle:
        handle.write("x\n")
if os.environ.get("TOY_DRIFT"):
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("# drift\n")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write(text)
"""

FAKE_GH = r"""import json, os, sys
state_path = os.environ["FAKE_GH_STATE"]
with open(state_path) as handle:
    state = json.load(handle)
args = sys.argv[1:]
if state.get("fail"):
    sys.exit(4)
if args[:2] == ["pr", "list"]:
    print(json.dumps(state["prs"]))
elif args[:2] == ["pr", "view"]:
    number = args[2]
    calls = state.setdefault("view_calls", dict())
    calls[number] = calls.get(number, 0) + 1
    with open(state_path, "w") as handle:
        json.dump(state, handle)
    pr = [p for p in state["prs"] if str(p["number"]) == number][0]
    view = dict(headRefName=pr["headRefName"], headRefOid=pr["headRefOid"], state="OPEN")
    rename_after = state.get("rename_after", dict()).get(number)
    if rename_after is not None and calls[number] > rename_after:
        view["headRefName"] = "renamed/branch"
    print(json.dumps(view))
else:
    sys.exit(5)
"""

CONFIG_TOML = """version = 1
remote = "origin"
base = "main"
[commit]
name = "Train Maintainer"
email = "train@example.invalid"
[generated]
paths = ["gen/digest.txt"]
regenerate = [["%(py)s", "-I", "-B", "tools/gen.py"]]
check = [["%(py)s", "-I", "-B", "tools/gen.py", "--check"]]
[busy]
probe = %(probe)s
[limits]
command_timeout_seconds = 60
network_timeout_seconds = 60
"""

FIXTURE_IDENT = dict(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                     GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
NINE = "".join("l%d\n" % n for n in range(1, 10))


def check(name, got, want):
    if name in _RECORDER["executed"]:
        print("SELF-TEST HARNESS ERROR: duplicate check id %r" % name, file=sys.stderr)
        sys.exit(2)
    _RECORDER["executed"].add(name)
    _RECORDER["list"].append(name)
    if got != want:
        _RECORDER["failures"].append("%s: got %r, want %r" % (name, got, want))


class Fixture:
    """A bare remote whose pre-receive hook can refuse and whose post-receive hook logs every ref
    update, a main checkout carrying the toy generator and the config, PR branches as linked
    worktrees of that checkout, and a fake gh answering from a JSON state file."""

    def __init__(self, base, probe="[]"):
        self.base = Path(base)
        self.remote = self.base / "remote.git"
        self.main = self.base / "main"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.state = self.base / "gh-state.json"
        self.push_log = self.base / "push.log"
        self.path = os.environ.get("PATH", os.defpath)
        self.prs = []
        gh = self.bin / "gh"
        gh.write_text("#!%s\n%s" % (sys.executable, FAKE_GH), encoding="utf-8")
        gh.chmod(0o755)
        _git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        hooks = self.remote / "hooks"
        (hooks / "post-receive").write_text("#!/bin/sh\ncat >> '%s'\n" % self.push_log,
                                            encoding="utf-8")
        (hooks / "pre-receive").write_text(
            "#!/bin/sh\nif [ -e '%s' ]; then exit 1; fi\nexit 0\n" % (self.base / "reject"),
            encoding="utf-8")
        for hook in ("post-receive", "pre-receive"):
            (hooks / hook).chmod(0o755)
        _git(self.base, "init", "-q", "-b", "main", str(self.main))
        _git(self.main, "remote", "add", "origin", str(self.remote))
        self.write(self.main, "src/a.txt", NINE)
        self.write(self.main, "tools/gen.py", TOY_GEN)
        self.write(self.main, CONFIG_PATH, CONFIG_TOML % dict(py=sys.executable, probe=probe))
        self.write(self.main, ".gitignore", "ignored/\n")
        self.commit(self.main, "seed")
        _git(self.main, "push", "-q", "origin", "main")
        self.set_gh()

    def write(self, repo, rel, text):
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, repo, message):
        subprocess.run([sys.executable, "-I", "-B", "tools/gen.py"], cwd=str(repo), check=True,
                       env=_clean_env(), timeout=60)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message, extra_env=FIXTURE_IDENT)
        return _git_text(repo, "rev-parse", "HEAD")

    def add_pr(self, number, branch, files, detach=False):
        path = self.base / ("wt-%d" % number)
        _git(self.main, "worktree", "add", "-q", "-b", branch, str(path), "main")
        for rel, text in files.items():
            self.write(path, rel, text)
        head = self.commit(path, "pr %d" % number)
        _git(path, "push", "-q", "origin", "%s:refs/heads/%s" % (branch, branch))
        if detach:
            _git(path, "checkout", "-q", "--detach")
        self.prs.append(dict(number=number, headRefName=branch, headRefOid=head,
                             isCrossRepository=False, baseRefName="main"))
        self.set_gh()
        return path

    def advance_main(self, files):
        for rel, text in files.items():
            self.write(self.main, rel, text)
        sha = self.commit(self.main, "main advance")
        _git(self.main, "push", "-q", "origin", "main")
        return sha

    def set_gh(self, **extra):
        state = dict(prs=self.prs)
        state.update(extra)
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def run(self, apply=False, only=None):
        os.environ["FAKE_GH_STATE"] = str(self.state)
        os.environ["PATH"] = str(self.bin) + os.pathsep + self.path
        return run_train(self.main, apply, only)

    def remote_ref(self, branch):
        code, out, _err = _git(self.remote, "rev-parse", "--verify", "-q",
                               "refs/heads/" + branch, ok=None)
        return out.decode().strip() if code == 0 else None

    def pushes(self):
        """Tool-made ref updates: branch creations and base advances made by the fixture are
        filtered out."""
        if not self.push_log.exists():
            return []
        rows = [line.split() for line in self.push_log.read_text().splitlines() if line.strip()]
        return [r for r in rows if r[0] != "0" * 40 and r[2] != "refs/heads/main"]


def _result(reports, number):
    rows = [r for r in reports if r["pr"] == number]
    return rows[0]["result"] if rows else None


def _fixture(tmp, name, probe="[]"):
    base = Path(tmp) / name
    base.mkdir()
    return Fixture(base, probe)


def _stale_pr(fx, number=1, branch="feat/x"):
    """A PR whose generated digest conflicts with the base's after the base advances."""
    wt = fx.add_pr(number, branch, dict([("src/b.txt", "bravo\n")]))
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    return wt


def _git_dir(wt):
    return _git_text(wt, "rev-parse", "--path-format=absolute", "--git-dir")


def case_config(_tmp):
    good = dict(version=1, remote="origin", base="main",
                commit=dict(name="A", email="a@example.invalid"),
                generated=dict(paths=["gen/d.txt"], regenerate=[["x"]], check=[["y"]]))

    def verdict(mutate):
        data = json.loads(json.dumps(good))
        mutate(data)
        try:
            _guard("config-fail-closed")(data)
        except FailClosed:
            return "refused"
        return "accepted"

    check("config/valid-accepted", verdict(lambda d: None), "accepted")
    check("config/unknown-key", verdict(lambda d: d.update(extra=1)), "refused")
    check("config/bad-type", verdict(lambda d: d.update(version="1")), "refused")
    check("config/absolute-path", verdict(lambda d: d["generated"].update(paths=["/etc/x"])),
          "refused")
    check("config/dotdot-path", verdict(lambda d: d["generated"].update(paths=["a/../b"])),
          "refused")
    check("config/glob-path", verdict(lambda d: d["generated"].update(paths=["gen/*.txt"])),
          "refused")
    check("config/empty-list", verdict(lambda d: d["generated"].update(check=[])), "refused")
    check("config/duplicate-path",
          verdict(lambda d: d["generated"].update(paths=["gen/d.txt", "gen/d.txt"])), "refused")
    check("config/empty-command", verdict(lambda d: d["generated"].update(regenerate=[[]])),
          "refused")
    check("config/missing-identity", verdict(lambda d: d["commit"].pop("email")), "refused")


def case_absent(tmp):
    fx = _fixture(tmp, "absent")
    _git(fx.main, "rm", "-q", CONFIG_PATH)
    _git(fx.main, "commit", "-q", "-m", "drop config", extra_env=FIXTURE_IDENT)
    _git(fx.main, "push", "-q", "origin", "main")
    rc, reports, fatal = fx.run()
    check("config/absent-exit2", (rc, reports, "not enabled" in (fatal or "")), (2, [], True))


def case_frombase(tmp):
    fx = _fixture(tmp, "frombase")
    _stale_pr(fx)
    fx.write(fx.main, CONFIG_PATH, "version = 99\n")
    rc, reports, _fatal = fx.run()
    check("config/read-from-base-not-checkout", (rc, _result(reports, 1)), (0, "would-merge"))


def case_dry(tmp):
    fx = _fixture(tmp, "dry")
    wt = _stale_pr(fx)
    before = _snapshot(wt)
    remote_before = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run()
    check("dry/would-merge", (rc, _result(reports, 1)), (0, "would-merge"))
    check("dry/changes-nothing", (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
                                  fx.pushes()), ([], remote_before, []))


def case_happy(tmp):
    fx = _fixture(tmp, "happy")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    base = fx.remote_ref("main")
    del GIT_AUDIT[:]
    rc, reports, _fatal = fx.run(apply=True)
    report = reports[0] if reports else dict()
    new = fx.remote_ref("feat/x")
    check("apply/pushed", (rc, report.get("result"), report.get("new_head")), (0, "pushed", new))
    parents = _git_text(wt, "rev-list", "--parents", "-n", "1", new).split()[1:]
    check("apply/parents-old-then-base", parents, [old, base])
    fields = _git_text(wt, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B", new).split("|")
    check("apply/config-identity-no-ai-trailer",
          (fields[0], fields[1], "Co-Authored-By" in fields[2],
           ("Merge-train: base=" + base) in fields[2]),
          ("Train Maintainer <train@example.invalid>", "Train Maintainer <train@example.invalid>",
           False, True))
    check("apply/generated-fresh",
          subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"], cwd=str(wt),
                         env=_clean_env(), timeout=60).returncode, 0)
    check("apply/report-fields",
          (sorted(report), report.get("schema"), report.get("regenerated"),
           report.get("review_carry"), report.get("hand_resolution"), report.get("base"),
           report.get("old_head")),
          (sorted(["schema", "pr", "branch", "worktree", "base", "old_head", "new_head", "result",
                   "reason", "regenerated", "hand_resolution", "review_carry"]),
           SCHEMA, ["gen/digest.txt"], True, False, base, old))
    check("apply/one-fast-forward-push", fx.pushes(), [[old, new, "refs/heads/feat/x"]])
    check("apply/base-ref-untouched", fx.remote_ref("main"), base)
    check("apply/argv-audit-clean", [a for a in GIT_AUDIT if _forbidden(a)
                                     or any(w.startswith("--force") for w in a)
                                     or (a[:1] == ["push"] and any(w.startswith("+") for w in a))],
          [])
    check("apply/marker-removed", os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME)), False)
    fx.prs[0]["headRefOid"] = new
    fx.set_gh()
    rc2, reports2, _fatal = fx.run(apply=True)
    check("apply/second-run-current", (rc2, _result(reports2, 1), len(fx.pushes())),
          (0, "current", 1))


def case_carry(tmp):
    fx = _fixture(tmp, "carry")
    fx.add_pr(1, "feat/x", dict([("src/a.txt", NINE.replace("l8", "L8"))]))
    fx.advance_main(dict([("src/a.txt", NINE.replace("l2", "L2"))]))
    rc, reports, _fatal = fx.run(apply=True)
    check("carry/true-when-pr-lines-unchanged",
          (rc, _result(reports, 1), reports[0]["review_carry"] if reports else None),
          (0, "pushed", True))
    fx2 = _fixture(tmp, "carry2")
    fx2.add_pr(1, "feat/x", dict([("src/a.txt", NINE.replace("l8", "L8"))]))
    fx2.advance_main(dict([("src/a.txt", NINE.replace("l8", "L8")), ("src/d.txt", "delta\n")]))
    rc, reports, _fatal = fx2.run(apply=True)
    check("carry/false-when-pr-lines-change",
          (_result(reports, 1), reports[0]["review_carry"] if reports else None), ("pushed", False))


def case_discover(tmp):
    fx = _fixture(tmp, "discover")
    fx.add_pr(1, "feat/one", dict([("src/one.txt", "1\n")]))
    fx.add_pr(2, "feat/none", dict([("src/none.txt", "2\n")]), detach=True)
    fx.add_pr(3, "feat/fork", dict([("src/fork.txt", "3\n")]))
    fx.add_pr(4, "feat/base", dict([("src/base.txt", "4\n")]))
    fx.add_pr(5, "feat/moved", dict([("src/moved.txt", "5\n")]))
    wt7 = fx.add_pr(7, "feat/detached", dict([("src/seven.txt", "7\n")]))
    fx.advance_main(dict([("src/c.txt", "c\n")]))
    fx.prs[2]["isCrossRepository"] = True
    fx.prs[3]["baseRefName"] = "release"
    fx.prs[4]["headRefOid"] = "0" * 40
    fx.prs.append(dict(fx.prs[0], number=6))
    fx.set_gh()
    _git(wt7, "checkout", "-q", "-b", "feat/other")
    _git(fx.main, "worktree", "add", "-q", "--detach", str(fx.base / "wt-7b"), "main")
    rc, reports, _fatal = fx.run()
    got = dict((r["pr"], r["result"]) for r in reports)
    check("discover/ambiguous-pr", (got.get(1), got.get(6)), ("ambiguous-pr", "ambiguous-pr"))
    check("discover/no-worktree-skipped", got.get(2), "no-worktree")
    check("discover/fork-pr", got.get(3), "fork-pr")
    check("discover/base-mismatch", got.get(4), "base-mismatch")
    check("discover/pr-head-moved", got.get(5), "pr-head-moved")
    check("discover/worktree-on-other-branch", got.get(7), "no-worktree")
    check("discover/refusal-exit1", rc, 1)
    fx.prs[:] = [dict(fx.prs[0], number=n, headRefName="b%d" % n) for n in range(1, 201)]
    fx.set_gh()
    rc, reports, fatal = fx.run()
    check("discover/truncated-list-exit2", (rc, reports, "truncated" in (fatal or "")),
          (2, [], True))
    fx.set_gh(fail=True)
    rc, reports, _fatal = fx.run()
    check("discover/gh-failure-exit2", (rc, reports), (2, []))


def case_ambiguous_worktree(tmp):
    fx = _fixture(tmp, "ambiguous")
    _stale_pr(fx)
    _git(fx.main, "worktree", "add", "-q", "-f", str(fx.base / "wt-dup"), "feat/x")
    rc, reports, _fatal = fx.run()
    check("discover/ambiguous-worktree", (rc, _result(reports, 1)), (1, "ambiguous-worktree"))


def case_branch(tmp):
    fx = _fixture(tmp, "branch")
    wt = _stale_pr(fx)
    fx.set_gh(rename_after=dict([("1", 0)]))
    rc, reports, _fatal = fx.run()
    check("discover/branch-mismatch", (rc, _result(reports, 1)), (1, "branch-mismatch"))
    fx.set_gh(rename_after=dict([("1", 1)]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("discover/branch-renamed-before-commit",
          (_result(reports, 1), _snapshot_diff(before, _snapshot(wt)), fx.pushes()),
          ("branch-mismatch", [], []))


def case_busy(tmp):
    fx = _fixture(tmp, "busy")
    wt = _stale_pr(fx)
    holder = subprocess.Popen(["sleep", "30"], cwd=str(wt))
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        holder.kill()
        holder.wait()
    check("refuse/busy-process", (rc, _result(reports, 1), fx.pushes()), (1, "busy", []))
    lock = os.path.join(_git_dir(wt), "index.lock")
    Path(lock).write_text("", encoding="utf-8")
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        os.unlink(lock)
    check("refuse/busy-index-lock", _result(reports, 1), "busy")
    probe = json.dumps([sys.executable, "-c", "import sys; sys.exit(1)"])
    fx2 = _fixture(tmp, "probe", probe=probe)
    _stale_pr(fx2)
    rc, reports, _fatal = fx2.run(apply=True)
    check("refuse/busy-probe", _result(reports, 1), "busy")


def case_dirty(tmp):
    fx = _fixture(tmp, "dirty")
    wt = _stale_pr(fx)
    fx.write(wt, "src/new.txt", "n\n")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/dirty-untracked", (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt))),
          (1, "dirty", []))
    os.unlink(os.path.join(str(wt), "src", "new.txt"))
    fx.write(wt, "src/b.txt", "changed\n")
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/dirty-modified", _result(reports, 1), "dirty")
    _git(wt, "add", "src/b.txt")
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/dirty-staged", _result(reports, 1), "dirty")


def case_conflict(tmp):
    fx = _fixture(tmp, "conflict")
    wt = fx.add_pr(1, "feat/x", dict([("src/a.txt", "pr side\n")]))
    fx.advance_main(dict([("src/a.txt", "main side\n")]))
    before = _snapshot(wt)
    old = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/source-conflict",
          (rc, _result(reports, 1), "src/a.txt" in (reports[0]["reason"] if reports else "")),
          (1, "source-conflict", True))
    check("refuse/source-conflict-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes(),
           os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME))),
          ([], old, [], False))


def _refusal_run(tmp, name, env_key=None, failing_check=False):
    """A stale-generated PR run with --apply under one injected generator fault; returns
    (exit code, result, reason, (snapshot differences, tool pushes))."""
    fx = _fixture(tmp, name)
    wt = _stale_pr(fx)
    if failing_check:
        text = _read_text(fx.main / CONFIG_PATH).replace(
            '"tools/gen.py", "--check"', '"-c", "import sys; sys.exit(1)"')
        fx.write(fx.main, CONFIG_PATH, text)
        _git(fx.main, "commit", "-q", "-am", "failing check", extra_env=FIXTURE_IDENT)
        _git(fx.main, "push", "-q", "origin", "main")
    before = _snapshot(wt)
    if env_key:
        os.environ[env_key] = "1"
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        os.environ.pop(env_key or "TOY_UNSET", None)
    reason = reports[0]["reason"] if reports else ""
    return rc, _result(reports, 1), reason, (_snapshot_diff(before, _snapshot(wt)), fx.pushes())


def case_regen(tmp):
    rc, result, _reason, restored = _refusal_run(tmp, "regen-fail", "TOY_FAIL")
    check("refuse/regenerate-failed", (rc, result, restored), (1, "regenerate-failed", ([], [])))
    rc, result, _reason, restored = _refusal_run(tmp, "check-fail", failing_check=True)
    check("refuse/check-failed", (rc, result, restored), (1, "check-failed", ([], [])))
    # A regenerator that edits a tracked file outside the generated set cannot be undone by merge
    # --abort, and the tool never resets or deletes, so the honest end state is restore-failed.
    rc, result, reason, restored = _refusal_run(tmp, "not-fixpoint", "TOY_DRIFT")
    check("refuse/regenerate-not-fixpoint", (rc, result, "regenerate-not-fixpoint" in reason,
                                             restored[1]),
          (3, "restore-failed", True, []))
    rc, result, reason, restored = _refusal_run(tmp, "litter", "TOY_LITTER")
    check("refuse/undeclared-write-restore-failed",
          (rc, result, "undeclared-write" in reason, restored[1]), (3, "restore-failed", True, []))


def case_reject(tmp):
    fx = _fixture(tmp, "reject")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    (fx.base / "reject").write_text("", encoding="utf-8")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("push/rejected-restored",
          (rc, _result(reports, 1), _git_text(wt, "rev-parse", "HEAD"), fx.remote_ref("feat/x"),
           _snapshot_diff(before, _snapshot(wt))),
          (1, "push-rejected", old, old, []))


def _killed_run(fx, replacement):
    global _push_and_observe
    real = _push_and_observe
    _push_and_observe = replacement
    try:
        fx.run(apply=True)
    except KeyboardInterrupt:
        pass
    finally:
        _push_and_observe = real


def case_idem(tmp):
    def kill(*_args):
        raise KeyboardInterrupt("simulated kill after the commit")

    def push_then_kill(ctx, _old, new):
        _git(ctx["wt"], "push", "-q", "--", ctx["remote"], "%s:refs/heads/%s" % (new, ctx["branch"]))
        raise KeyboardInterrupt("simulated kill after the push")

    def kill_in_merge(*_args):
        raise KeyboardInterrupt("simulated kill mid-merge")

    fx = _fixture(tmp, "kill-commit")
    _stale_pr(fx)
    _killed_run(fx, kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-commit-one-push", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "pushed", 1))
    fx = _fixture(tmp, "kill-push")
    _stale_pr(fx)
    _killed_run(fx, push_then_kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-push-already-pushed", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "already-pushed", 1))
    fx = _fixture(tmp, "kill-merge")
    _stale_pr(fx)
    saved = GUARDS["fixpoint"]
    GUARDS["fixpoint"] = kill_in_merge
    try:
        fx.run(apply=True)
    except KeyboardInterrupt:
        pass
    finally:
        GUARDS["fixpoint"] = saved
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-mid-merge-restarts", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "pushed", 1))
    fx = _fixture(tmp, "foreign-merge")
    wt = _stale_pr(fx)
    _git(wt, "merge", "--no-commit", "--no-ff", fx.remote_ref("main"), ok=None,
         extra_env=FIXTURE_IDENT)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/foreign-merge-left-alone",
          (_result(reports, 1), _snapshot_diff(before, _snapshot(wt))),
          ("in-progress-operation", []))


def case_cli(tmp):
    proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--bogus"],
                          capture_output=True, timeout=60, env=_clean_env())
    check("cli/bad-argument-exit2", proc.returncode, 2)
    plain = Path(tmp) / "not-a-repo"
    plain.mkdir()
    rc, reports, fatal = run_train(plain)
    check("cli/not-a-repo-exit2", (rc, reports, bool(fatal)), (2, [], True))


CASES = dict([
    ("config", case_config), ("absent", case_absent), ("frombase", case_frombase),
    ("dry", case_dry), ("happy", case_happy), ("carry", case_carry), ("discover", case_discover),
    ("ambiguous", case_ambiguous_worktree), ("branch", case_branch), ("busy", case_busy),
    ("dirty", case_dirty), ("conflict", case_conflict), ("regen", case_regen),
    ("reject", case_reject), ("idem", case_idem), ("cli", case_cli),
])


def _noop(*_args, **_kwargs):
    return None


# Red-on-revert: each guard, replaced in turn by its reverted form, must turn its named case red.
REVERTS = dict([
    ("config-fail-closed", (_noop, "config")),
    ("config-source", (lambda root, _sha: _read_text(os.path.join(str(root), CONFIG_PATH)),
                       "frombase")),
    ("busy", (_noop, "busy")),
    ("dirty", (_noop, "dirty")),
    ("branch-match", (_noop, "branch")),
    ("source-conflict", (_noop, "conflict")),
    ("regenerate-exit", (_noop, "regen")),
    ("check-exit", (_noop, "regen")),
    ("fixpoint", (_noop, "regen")),
    ("push-restore", (_noop, "reject")),
    ("abort-on-refusal", (_noop, "conflict")),
    ("reconcile", (_noop, "idem")),
])


def _run_case(name, tmp):
    """One case; an unexpected exception fails that case and never crashes the suite."""
    try:
        CASES[name](tmp)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        _RECORDER["failures"].append("case %s raised %s: %s" % (name, type(exc).__name__, exc))


def _red_on_revert(tmp):
    """The guards whose reverted form left their named case green (an empty list passes)."""
    stale = []
    for name, (revert, case) in REVERTS.items():
        saved = GUARDS[name]
        GUARDS[name] = revert
        _RECORDER.update(executed=set(), list=[], failures=[])
        try:
            sub = tempfile.mkdtemp(prefix="red-%s-" % name, dir=tmp)
            _run_case(case, sub)
        finally:
            GUARDS[name] = saved
        if not _RECORDER["failures"]:
            stale.append("%s (case %s)" % (name, case))
    _RECORDER.update(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES)
    return stale


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read %s: %s" % (CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite %r in %s" % (
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(dict(format_version=1, suite=SUITE_ID, check_ids=EXECUTED), handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report %s: %s" % (
            report_path, exc), file=sys.stderr)
        return False
    return True


def self_test(red_on_revert=False, report_path=None):
    """Every case inside fixture_git_lifecycle (GIT_* scrubbed, scratch HOME, global and system
    config pinned to os.devnull, a PATH-front git wrapper), under one private temp directory."""
    sys.path.insert(0, str(ROOT / "tools"))
    from _git_fixture_env import fixture_git_lifecycle
    stale = []
    with fixture_git_lifecycle():
        with tempfile.TemporaryDirectory(prefix="merge-train-selftest-") as raw:
            tmp = os.path.realpath(raw)
            for name in CASES:
                _run_case(name, tmp)
            if red_on_revert and not FAILURES:
                stale = _red_on_revert(tmp)
    if not _write_report(report_path):
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: %s" % check_id)
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: %s" % check_id)
    for guard in stale:
        FAILURES.append("red-on-revert: reverting guard %s left its case green" % guard)
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: %d unique checks executed; execution set reconciled against "
          "tools/selftest_checks.toml%s" % (len(EXECUTED), (
              "; all %d guards in GUARDS went red when reverted" % len(REVERTS))
              if red_on_revert else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
