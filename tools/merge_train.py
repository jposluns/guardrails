#!/usr/bin/env python3
"""Merge train (opt-in): refresh every open PR's head branch with the base branch, taking the base's
side for the declared GENERATED files only, regenerating and checking them, and refusing everything
else. It does nothing unless the project commits .aiqt/merge-train.toml on its base branch.

  merge_train.py [--repo DIR] [--pr N]... [--apply]   dry run by default; --apply commits and pushes
  merge_train.py --self-test [--red-on-revert]        hermetic fixture self-test (local bare remote, fake gh)
  merge_train.py --execution-report ABS_PATH          the self-test, also writing the executed check ids

The tool NEVER mutates the author's working tree, index, HEAD or any local branch: that capability
was removed, not guarded (decision D-390-PRIVATE-WORKTREE). Per open PR, in PR-number order, every
merge, conflict resolution, regeneration, check and commit happens in a PRIVATE scratch checkout
the run creates for that PR (a --shared clone of the repository, detached at the PR's remote head)
and removes in a finally; checkout filters, text/eol/autocrlf conversion, ident expansion, the
working-tree encoding, hooks (core.hooksPath pinned to the null device) and fsmonitor are disabled
there for every git call. The author's worktree is only READ: the run locates the worktree that has
the PR branch checked out and re-confirms, against a fresh gh view, that it still names the same
open PR. That read-only branch-match check is kept because it anchors each PR to one local checkout
for reporting and refuses a renamed or closed PR; the old busy check and its process scan protected
in-place mutation and were removed with it, as were the dirty check and the local-position checks.
The author's local branch ref is NOT fast-forwarded by the tool: after a push the author pulls,
like after any other remote change.

With --apply the run validates the PR head branch name (git check-ref-format --branch plus the
lease's own character set; an unsupported name is refused as bad-branch-name before any work),
requires the gh view, the gh list and the remote tip to agree on the PR head, merges the fetched
base tip M in the private checkout with `git merge --no-commit --no-ff`, takes the base's side for
conflicted generated paths, refuses any other conflict, refuses a declared generated path that is
(or sits under) a symlink or resolves outside the checkout, always runs the regenerate commands,
runs the check commands, proves nothing outside the generated set differs from the automatic merge,
then proves that EVERY blob of the validated tree is byte-identical to the private-checkout file
the checks read (what was validated is what is pushed; a clean filter or autocrlf transform cannot
smuggle unvalidated bytes), commits with `git commit-tree` against that validated tree (no commit
hook runs), verifies the new commit's first parent is the exact PR head this run validated and that
the head is an ancestor of the new commit, and pushes from the private checkout with an exact-value
compare-and-set (--force-with-lease=refs/heads/<branch>:<the head this run validated>, a 40- or
64-hex object id, which can only narrow what a plain push would accept) to the PR's own head
branch, so a branch rewound, advanced or deleted since the validation is refused by the remote,
never overwritten. One observer reads the remote after every push attempt, a timed-out or failed
one included; a push whose outcome cannot be observed (a push timeout, an observer timeout, an
unreadable remote) is INDETERMINATE: a small marker is kept so the retry can classify it, and
nothing else changes anywhere. There is no rollback machinery, because nothing local is mutated and
so there is nothing to roll back.

The config is read from the fetched base tip (`git show <M>:.aiqt/merge-train.toml`), never from a
PR branch or the checkout; the [busy] table is still accepted, and ignored, for config
compatibility. The branch pushed to is the PR's own head branch as gh names it, re-confirmed before
the push; the tool takes no branch-name argument. The tool ships with the pack; this repository's
own .aiqt/merge-train.toml does not (.aiqt/core/ownership.toml excludes it), because its presence
is what enables the tool. An adopting project commits its own.

Stdout carries one JSON line per PR (schema merge-train/1); stderr carries one human line per PR.
Exit codes: 0 every PR current, pushed, already pushed or (dry run) mergeable; 1 any PR refused or
skipped; 2 the run failed closed and touched nothing (no config, malformed config, lock held, gh or
fetch failure, a possibly truncated PR list, bad arguments).

THREAT MODEL (cooperative): agents on a single-operator host. The repository's local config, the
operator's global and system config, hooks and the object store are trusted; the tool guards
against accidents and odd local state, not hostile repository state. Disclosed residuals: fork PRs
are refused (fork-pr), not supported; v1 supports the remote "origin" and the base "main" only; the
regenerate and check commands run the merged tree's copy of the generators, which may include the
PR's own changes; review_carry is information only; the private checkout reuses the repository's
object store through --shared, the same trust tier as the repository itself.

The tool never force-pushes (no --force, no -f, no bundled or abbreviated force, delete, mirror,
all or prune push option, no + or : push refspec, and no src:dst refspec other than the exact
<40-or-64-hex>:refs/heads/<branch> form away from the base; the one lease form its own argv audit
allows is the exact-value --force-with-lease=refs/heads/<branch>:<validated old head>, a pure
compare-and-set that only narrows what a plain push would accept, because the pushed commit's
first parent is verified to be that validated head), rebases, cherry-picks, stashes, cleans, runs
ANY reset, prunes worktrees, resolves a non-generated conflict, pushes to or checks out the base
branch, edits PR metadata, or reads an ambient git identity.
"""


import fcntl
import hashlib
import json
import os
import re
import shutil
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
GLOB_CHARS = set("*?[]!" + chr(123) + chr(125))
CONFIG_KEYS = frozenset(("version", "remote", "base", "commit", "generated", "busy", "limits"))
TABLE_KEYS = dict(commit=frozenset(("name", "email")),
                  generated=frozenset(("paths", "regenerate", "check")),
                  busy=frozenset(("probe",)),
                  limits=frozenset(("command_timeout_seconds", "network_timeout_seconds")))
REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# The single push --force* form the argv audit allows: an exact-value compare-and-set lease, with a
# 40-hex (SHA-1) or 64-hex (SHA-256) validated old head.
PUSH_LEASE = re.compile(r"\A--force-with-lease=refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*:"
                        r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
# The single src:dst push refspec shape allowed: an exact object id onto a plain head, never main.
PUSH_SPEC = re.compile(r"\A[0-9a-f]{40}([0-9a-f]{24})?:refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
FORBIDDEN_PUSH_LONG = ("--force", "--force-with-lease", "--force-if-includes", "--delete",
                       "--mirror", "--all", "--prune")

# Every argv the tool hands to git, for the self-test's forbidden-operation audit.
GIT_AUDIT = []


class FailClosed(Exception):
    """The whole run stops before touching anything (exit 2)."""


class Refuse(Exception):
    """One PR is refused; its private checkout is discarded and the run continues."""

    def __init__(self, status, reason=""):
        Exception.__init__(self, status)
        self.status = status
        self.reason = reason


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
    """The argv audit enforced on every git call, kept in exact agreement with the self-test's
    independently written _audit_violation: no rebase, cherry-pick, stash, clean, gc, worktree
    prune, and no reset of ANY kind (the tool has nothing local to reset); for push, no force,
    delete, mirror, all or prune option in long, abbreviated (git accepts unique prefixes) or
    bundled short form (-uf, -fu), no lease other than the one exact-value compare-and-set form
    (PUSH_LEASE, 40- or 64-hex), and no refspec other than a plain name or the exact
    <hex>:refs/heads/<branch> form away from the base (no + force spec, no :delete spec, no
    src:dst alias). The first non-option word is the repository (a URL may carry a colon)."""
    words = list(args)
    if words[:1] in (["rebase"], ["stash"], ["clean"], ["cherry-pick"], ["gc"], ["prune"],
                     ["reset"]):
        return True
    if words[:2] == ["worktree", "prune"]:
        return True
    if words[:1] != ["push"]:
        return False
    specs_only, repo_seen = False, False
    for w in words[1:]:
        if w == "--" and not specs_only:
            specs_only = True
            continue
        if not specs_only and w.startswith("-"):
            if not w.startswith("--"):
                if any(c in "fd" for c in w[1:]):
                    return True
                continue
            if PUSH_LEASE.match(w):
                continue
            stem = w.split("=", 1)[0]
            for banned in FORBIDDEN_PUSH_LONG:
                if len(stem) > 2 and (banned.startswith(stem) or stem.startswith(banned)):
                    return True
            continue
        if not repo_seen:
            repo_seen = True
            continue
        if w.startswith("+") or ":" in w:
            if not PUSH_SPEC.match(w) or w.endswith(":refs/heads/main"):
                return True
    return False


def _git(cwd, *args, extra_env=None, timeout=GIT_TIMEOUT, ok=(0,)):
    """The single pinned git funnel: auto-maintenance pinned off in option position, scrubbed GIT_*,
    a timeout. Returns (returncode, stdout bytes, stderr text); raises Refuse("git-failed") on a
    timeout and on an exit code outside ok when ok is not None. The audit records every attempted
    argv, a refused one included."""
    GIT_AUDIT.append(list(args))
    if _forbidden(args):
        raise AssertionError("forbidden git operation refused by the argv audit: %r" % (args,))
    argv = ["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
    argv += list(args)
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


def guard_branch_name(root, branch):
    """Refuse bad-branch-name BEFORE any work for a PR head branch name the tool does not support.
    The name comes from gh: it must satisfy git check-ref-format --branch and the exact-value
    lease's own character set [A-Za-z0-9._/-] (a name outside it, such as one carrying + or @,
    cannot be expressed in the one lease form the argv audit allows, so it is refused up front
    with a named status instead of crashing later)."""
    if not isinstance(branch, str) or not REF_NAME.match(branch) or ".." in branch:
        raise Refuse("bad-branch-name", "unsupported PR head branch name %r" % (branch,))
    if _git(root, "check-ref-format", "--branch", branch, ok=None)[0] != 0:
        raise Refuse("bad-branch-name", "git check-ref-format refuses %r" % (branch,))


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


def guard_reconcile(ctx, marker):
    """A marker records only a push whose outcome this tool could not observe (a push or observer
    timeout, an unreadable remote): it exists to make the retry safe to CLASSIFY, never to gate
    anything local, because the tool mutates nothing local. Returns a finished partial report
    (already-pushed) when the recorded push turns out to have been delivered, or None to proceed
    with a fresh private checkout (the stale marker is then dropped); raises
    Refuse(state-mismatch) for a marker naming another PR or branch and Refuse(push-unknown) while
    the remote is still unreadable (the marker is kept and nothing changes)."""
    if marker is None:
        return None
    if marker.get("pr") != ctx["pr"] or marker.get("branch") != ctx["branch"]:
        raise Refuse("state-mismatch", "the marker names another PR or branch")
    remote = _remote_head(ctx)
    if remote is None:
        raise Refuse("push-unknown",
                     "the recorded push is still unconfirmed: the remote is unreadable")
    if remote == marker.get("new"):
        _remove_marker(ctx["git_dir"])
        return dict(result="already-pushed", old_head=marker.get("old"),
                    new_head=marker.get("new"),
                    reason="an earlier run pushed %s" % marker.get("new"))
    _remove_marker(ctx["git_dir"])
    return None


def guard_scratch_setup(scratch):
    """Disable every content conversion and every hook in the private checkout, so the bytes the
    regenerate and check commands read are the bytes git stores and pushes: checkout and checkin
    filters, text/eol/autocrlf conversion, ident expansion and the working-tree encoding are unset
    for every path (the checkout's own info/attributes outranks any in-tree .gitattributes), hooks
    are disabled (core.hooksPath pinned to the null device; the commit is made with commit-tree,
    which runs no hook anyway) and fsmonitor is off."""
    for key, value in (("core.autocrlf", "false"), ("core.eol", "lf"),
                       ("core.hooksPath", os.devnull), ("core.fsmonitor", "false")):
        _git(scratch, "config", key, value)
    git_dir = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    info = os.path.join(git_dir, "info")
    os.makedirs(info, exist_ok=True)
    with open(os.path.join(info, "attributes"), "w", encoding="utf-8") as handle:
        handle.write("* -filter -text -eol -crlf -ident -working-tree-encoding\n")


def guard_pushed_bytes(scratch, tree):
    """Refuse commit-mismatch unless EVERY blob of the validated tree is byte-identical to the
    private-checkout file the regenerate and check commands read (every path, not only generated
    ones): a content transform between the checkout and the object store (a clean filter,
    autocrlf, eol or ident conversion) would otherwise push bytes the checks never saw. The
    checkout bytes are hashed directly against the repository's own object format, with no filter
    in the path; a symlink must reproduce the committed target; gitlinks carry no bytes and are
    skipped."""
    algo = _git_text(scratch, "rev-parse", "--show-object-format")
    hasher = dict(sha1=hashlib.sha1, sha256=hashlib.sha256).get(algo)
    if hasher is None:
        raise Refuse("commit-mismatch", "unknown object format %r" % algo)
    root = str(scratch)
    for row in _git(scratch, "ls-tree", "-r", "-z", tree)[1].split(b"\0"):
        if not row:
            continue
        meta, raw = row.split(b"\t", 1)
        mode, kind, sha = meta.decode("utf-8", "replace").split()
        if kind != "blob":
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        path = os.path.join(root, rel)
        if mode == "120000":
            target = _git(scratch, "cat-file", "blob", sha)[1]
            try:
                link = os.readlink(path)
            except OSError as exc:
                raise Refuse("commit-mismatch", "%s: %s" % (rel, exc))
            if os.fsencode(link) != target:
                raise Refuse("commit-mismatch",
                             "symlink %s differs from the committed target" % rel)
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise Refuse("commit-mismatch", "%s unreadable: %s" % (rel, exc))
        if hasher(b"blob %d\0" % len(data) + data).hexdigest() != sha:
            raise Refuse("commit-mismatch",
                         "%s: the checkout bytes differ from the committed blob" % rel)


def guard_commit_parents(scratch, old, new):
    """Refuse commit-mismatch unless the new commit's FIRST parent is exactly the PR head this run
    validated and that head is an ancestor of the new commit: the exact-value lease is then a pure
    narrowing of a plain push (never a rewind), and a rewritten or parentless commit is never
    pushed."""
    parents = _git_text(scratch, "rev-list", "--parents", "-n", "1", new).split()[1:]
    if not parents or parents[0] != old:
        raise Refuse("commit-mismatch", "first parent %s is not the validated head %s" % (
            parents[0] if parents else "(none)", old))
    if _git(scratch, "merge-base", "--is-ancestor", old, new, ok=None)[0] != 0:
        raise Refuse("commit-mismatch", "the validated head is not an ancestor of the new commit")


def guard_push_lease(branch, old):
    """The push's exact-value compare-and-set argv: the remote refuses unless the branch is still
    exactly at old, the head this run validated, so a rewound, advanced or deleted branch is
    refused, never overwritten."""
    return ["--force-with-lease=refs/heads/%s:%s" % (branch, old)]


def guard_push_indeterminate(_exc):
    """A push that timed out or failed to launch may still have reached the remote: swallow the
    error so the one observer reads the remote and decides; a rollback never precedes
    observation."""
    return None


def guard_generated_confined(wt, generated):
    """Refuse generated-symlink when a declared generated path, or any directory on the way to it,
    is a symlink or resolves outside the worktree: regenerating through it would write outside the
    declared set."""
    root = os.path.realpath(str(wt))
    for rel in generated:
        parts = rel.split("/")
        for depth in range(1, len(parts) + 1):
            if os.path.islink(os.path.join(root, *parts[:depth])):
                raise Refuse("generated-symlink", "/".join(parts[:depth]) + " is a symlink")
        real = os.path.realpath(os.path.join(root, *parts))
        if real != root and not real.startswith(root + os.sep):
            raise Refuse("generated-symlink", "%s resolves outside the worktree" % rel)


def guard_commit_tree(wt, new, validated_tree):
    """Refuse commit-mismatch when the committed tree is not the validated one (a pre-commit hook
    that edits and restages files would otherwise push content the checks never saw)."""
    if _git_text(wt, "rev-parse", new + "^{tree}") != validated_tree:
        raise Refuse("commit-mismatch",
                     "the committed tree is not the validated tree %s" % validated_tree)


# Every guard is reached through this table, so --red-on-revert can replace each one in turn.
GUARDS = dict([
    ("config-fail-closed", guard_config_fail_closed),
    ("config-source", guard_config_source),
    ("branch-name", guard_branch_name),
    ("branch-match", guard_branch_match),
    ("source-conflict", guard_source_conflict),
    ("regenerate-exit", guard_regenerate_exit),
    ("check-exit", guard_check_exit),
    ("fixpoint", guard_fixpoint),
    ("reconcile", guard_reconcile),
    ("scratch-setup", guard_scratch_setup),
    ("generated-confined", guard_generated_confined),
    ("pushed-bytes", guard_pushed_bytes),
    ("commit-parents", guard_commit_parents),
    ("commit-tree", guard_commit_tree),
    ("push-lease", guard_push_lease),
    ("push-indeterminate", guard_push_indeterminate),
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
    """The remote branch tip through ls-remote (run in the repository root, by remote name, so a
    config change is honoured): a SHA, "" when absent, None when unreadable for ANY reason, a
    timeout included; the caller treats None as indeterminate, never as a cue to change
    anything."""
    try:
        code, out, _err = _git(ctx["root"], "ls-remote", "--", ctx["remote"],
                               "refs/heads/" + ctx["branch"], ok=None,
                               timeout=ctx["net_timeout"])
    except Refuse:
        return None
    if code != 0:
        return None
    lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return ""
    sha = lines[0].split()[0]
    return sha if SHA.match(sha) else None


def _snapshot(wt):
    """HEAD, the symbolic ref, the index entries, the full status with ignored files, and a SHA-256
    of every tracked and untracked file, ignored files included: the self-test's proof that the
    author's worktree, index and refs are byte-identical across a run."""
    head = _git_text(wt, "rev-parse", "HEAD")
    code, out, _err = _git(wt, "symbolic-ref", "--quiet", "HEAD", ok=None)
    index = _git(wt, "ls-files", "-s", "-z")[1]
    status = _git(wt, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored")[1]
    files = _git(wt, "ls-files", "-z", "--cached", "--others")[1]
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


# Every private scratch checkout ever created, for the self-test's removal proof.
SCRATCHES = []


def _scratch_checkout(ctx):
    """The PRIVATE scratch checkout for one PR: a --shared clone of the repository (the object
    store is reused; nothing of the author's checkout is written), with every conversion and hook
    disabled by the scratch-setup guard. Returns (checkout path, directory to remove in the
    caller's finally). The author's worktree is never written: no merge, checkout, add, commit or
    reset ever runs there."""
    scratch_root = tempfile.mkdtemp(prefix="merge-train-scratch-")
    SCRATCHES.append(scratch_root)
    scratch = os.path.join(scratch_root, "co")
    _git(ctx["root"], "clone", "--quiet", "--shared", "--no-checkout", str(ctx["root"]), scratch)
    _guard("scratch-setup")(scratch)
    return scratch, scratch_root


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


def _run_checks(ctx, scratch):
    for command in ctx["cfg"]["generated"]["check"]:
        code, _out, err = _run_external(command, scratch, ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("check-exit")(code, command, err)


def _push_and_observe(ctx, scratch, old, new):
    """Compare-and-set push of new from the private checkout to the PR's own branch (the
    exact-value lease makes the remote refuse unless the branch is still at old, the head this run
    validated), then one observer over the remote. A push whose outcome is unknown (a timeout, a
    launch failure) and an observation failure of ANY kind, an observer timeout included, are
    INDETERMINATE: the marker is kept and nothing changes anywhere; nothing local exists to roll
    back."""
    branch = ctx["branch"]
    try:
        _git(scratch, "push", "--porcelain", "--no-follow-tags",
             *_guard("push-lease")(branch, old), "--", ctx["remote_url"],
             "%s:refs/heads/%s" % (new, branch), ok=None, timeout=ctx["net_timeout"])
    except Refuse as exc:
        _guard("push-indeterminate")(exc)
    observed = _remote_head(ctx)
    if observed == new:
        _remove_marker(ctx["git_dir"])
        return dict(result="pushed", old_head=old, new_head=new, reason="")
    if observed is None:
        raise Refuse("push-unknown",
                     "remote unreadable after the push; the marker is kept, nothing changed")
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
    """One PR, read-only outside the private scratch checkout. Returns a partial report; raises
    Refuse for a refusal. The author's worktree, index, HEAD and local branch are never written;
    the only author-side state is the retry marker in the worktree's git dir."""
    marker = _read_marker(ctx["git_dir"])
    if marker is not None and not apply:
        raise Refuse("state-mismatch", "a merge-train marker is present; rerun with --apply")
    done = _guard("reconcile")(ctx, marker)
    if done is not None:
        return done
    view = _gh_view(ctx)
    _guard("branch-match")(ctx["wt"], ctx["branch"], view)
    remote = _remote_head(ctx)
    if remote is None:
        raise Refuse("pr-head-moved", "the remote branch tip could not be read")
    if remote != ctx["head_oid"] or view.get("headRefOid") != ctx["head_oid"]:
        raise Refuse("pr-head-moved", "remote head %s, gh %s, listed %s" % (
            remote, view.get("headRefOid"), ctx["head_oid"]))
    old = ctx["head_oid"]
    ctx["old"] = old
    scratch, scratch_root = _scratch_checkout(ctx)
    try:
        _git(scratch, "fetch", "--no-tags", "--", ctx["remote_url"],
             "refs/heads/" + ctx["branch"], timeout=ctx["net_timeout"])
        if _git_text(scratch, "rev-parse", "FETCH_HEAD^0") != old:
            raise Refuse("pr-head-moved", "the remote head moved during the fetch")
        if _git(scratch, "merge-base", "--is-ancestor", ctx["base_sha"], old, ok=None)[0] == 0:
            return dict(result="current", old_head=old, new_head=None, reason="")
        if not apply:
            return dict(result="would-merge", old_head=old, new_head=None,
                        reason="dry run: would merge %s (rerun with --apply)" % ctx["base_sha"])
        _git(scratch, "checkout", "--quiet", "--detach", old, timeout=ctx["cmd_timeout"])
        return _merge_commit_push(ctx, scratch, old, ctx["base_sha"])
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def _merge_commit_push(ctx, scratch, old, base):
    generated = ctx["generated"]
    ident = dict(GIT_AUTHOR_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_AUTHOR_EMAIL=ctx["cfg"]["commit"]["email"],
                 GIT_COMMITTER_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_COMMITTER_EMAIL=ctx["cfg"]["commit"]["email"])
    scratch_git = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    _git(scratch, "merge", "--no-commit", "--no-ff", "--no-edit", base, extra_env=ident, ok=None,
         timeout=ctx["cmd_timeout"])
    if not os.path.lexists(os.path.join(scratch_git, "MERGE_HEAD")):
        raise Refuse("git-failed", "the merge did not start")
    unmerged = _unmerged(scratch)
    _guard("source-conflict")(unmerged, generated)
    for path in sorted(unmerged):
        if unmerged[path] != set((1, 2, 3)):
            raise Refuse("generated-delete-conflict", path)
    for path in sorted(unmerged):
        _git(scratch, "checkout", "--theirs", "--", path)
        _git(scratch, "add", "--", path)
    _guard("generated-confined")(scratch, generated)
    automatic = _index_entries(scratch)
    for command in ctx["cfg"]["generated"]["regenerate"]:
        code, _out, err = _run_external(command, scratch, ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("regenerate-exit")(code, command, err)
    _git(scratch, "add", "-A", "--", *generated)
    _run_checks(ctx, scratch)
    _guard("fixpoint")(scratch)
    resolved = _index_entries(scratch)
    hand = sorted(p for p in set(automatic) | set(resolved)
                  if automatic.get(p) != resolved.get(p) and p not in generated)
    if hand:
        raise Refuse("undeclared-write", "paths outside the generated set changed: %s" % (
            ", ".join(hand[:10])))
    merge_base = _git_text(scratch, "merge-base", old, base)
    ctx["review_carry"] = (_changed_lines(scratch, merge_base, old, generated)
                           == _changed_lines(scratch, base, None, generated))
    ctx["regenerated"] = [p for p in sorted(generated) if p in resolved]
    validated_tree = _git_text(scratch, "write-tree")
    _guard("pushed-bytes")(scratch, validated_tree)
    view = _gh_view(ctx)
    _guard("branch-match")(ctx["wt"], ctx["branch"], view)
    title = "Merge %s/%s into %s (merge-train)" % (ctx["remote"], ctx["base"], ctx["branch"])
    new = _git_text(scratch, "commit-tree", validated_tree, "-p", old, "-p", base,
                    "-m", title, "-m", "Merge-train: base=%s" % base, extra_env=ident,
                    timeout=ctx["cmd_timeout"])
    _guard("commit-tree")(scratch, new, validated_tree)
    _guard("commit-parents")(scratch, old, new)
    _write_marker(ctx["git_dir"], dict(pr=ctx["pr"], branch=ctx["branch"], old=old, new=new))
    return _push_and_observe(ctx, scratch, old, new)


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
        remote_url = _git_text(root, "remote", "get-url", remote)
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
            _guard("branch-name")(root, branch)
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
                       remote=remote, remote_url=remote_url, base=base, base_sha=base_sha, cfg=cfg,
                       generated=generated, cmd_timeout=cmd_timeout, net_timeout=net_timeout,
                       git_dir=_git_text(wt, "rev-parse", "--path-format=absolute", "--git-dir"))
            report.update(_process_pr(ctx, apply))
        except Refuse as refusal:
            report["result"], report["reason"] = refusal.status, refusal.reason
            report["old_head"] = ctx["old"]
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
if os.environ.get("TOY_IGNORED"):
    with open(os.path.join(root, "ignored", "valuable"), "w") as handle:
        handle.write("LOST\n")
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

FAKE_GH = r"""import json, os, subprocess, sys
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
    move = state.get("move_on_view", dict()).get(number)
    if move is not None and calls[number] == move["call"]:
        subprocess.run(["git", "--git-dir=" + move["remote"], "update-ref", move["ref"], move["new"],
                        move["old"]], check=True, timeout=60)
    race = state.get("commit_on_view", dict()).get(number)
    if race is not None and calls[number] == race["call"]:
        env = dict(os.environ)
        env.update(GIT_AUTHOR_NAME="Author", GIT_AUTHOR_EMAIL="author@example.invalid",
                   GIT_COMMITTER_NAME="Author", GIT_COMMITTER_EMAIL="author@example.invalid")
        with open(os.path.join(race["worktree"], race["file"]), "w") as handle:
            handle.write("author\n")
        subprocess.run(["git", "-C", race["worktree"], "add", "--", race["file"]],
                       check=True, timeout=60, env=env)
        subprocess.run(["git", "-C", race["worktree"], "commit", "-q", "-m", "author race"],
                       check=True, timeout=60, env=env)
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


ZERO = "0" * 40


def _audit_violation(args):
    """The self-test's own forbidden-operation predicate, written apart from _forbidden so that a
    weakened funnel cannot also blind the audit; the two must AGREE on every argv: no rebase,
    cherry-pick, stash, clean, gc, worktree prune or reset of ANY kind; for push, no force, delete,
    mirror, all or prune option in long, abbreviated or bundled short form (-uf, -fu), no lease
    other than the one exact-value refs/heads compare-and-set form (40- or 64-hex), and no refspec
    other than a plain name or the exact <hex>:refs/heads/<branch> form away from the base (no +
    force spec, no :delete spec, no src:dst alias). The first non-option word is the repository."""
    words = list(args)
    if words[:1] in (["rebase"], ["cherry-pick"], ["stash"], ["clean"], ["gc"], ["prune"],
                     ["reset"]):
        return True
    if words[:2] == ["worktree", "prune"]:
        return True
    if words[:1] != ["push"]:
        return False
    lease = re.compile(r"\A--force-with-lease=refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*:"
                       r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
    spec = re.compile(r"\A[0-9a-f]{40}([0-9a-f]{24})?:refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
    specs_only, repo_seen = False, False
    for w in words[1:]:
        if w == "--" and not specs_only:
            specs_only = True
            continue
        if not specs_only and w.startswith("-"):
            if not w.startswith("--"):
                if any(c in "fd" for c in w[1:]):
                    return True
                continue
            if lease.match(w):
                continue
            stem = w.split("=", 1)[0]
            for banned in ("--force", "--force-with-lease", "--force-if-includes", "--delete",
                           "--mirror", "--all", "--prune"):
                if len(stem) > 2 and (banned.startswith(stem) or stem.startswith(banned)):
                    return True
            continue
        if not repo_seen:
            repo_seen = True
            continue
        if w.startswith("+") or ":" in w:
            if not spec.match(w) or w.endswith(":refs/heads/main"):
                return True
    return False


class Fixture:
    """A bare remote whose pre-receive hook can refuse and whose post-receive hook logs every ref
    update (and, when asked, then makes the remote unreadable to the main checkout), a main checkout
    carrying the toy generator and the config, PR branches as linked worktrees of that checkout, and
    a fake gh answering from a JSON state file. Every tool run records the plan's per-case
    invariants into self.violations."""

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
        self.runs = 0
        self.violations = []
        self.gone = self.base / "gone.git"
        gh = self.bin / "gh"
        gh.write_text("#!%s\n%s" % (sys.executable, FAKE_GH), encoding="utf-8")
        gh.chmod(0o755)
        _git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        hooks = self.remote / "hooks"
        (hooks / "post-receive").write_text(
            "#!/bin/sh\ncat >> '%s'\nif [ -e '%s' ]; then\n  rm -f '%s'\n"
            "  git config --file '%s' remote.origin.url '%s'\nfi\n" % (
                self.push_log, self.base / "unreadable-after-push",
                self.base / "unreadable-after-push", self.main / ".git" / "config", self.gone),
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
        _git(path, "push", "-q", "origin", "%s:refs/heads/%s" % (head, branch))
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
        """One tool run; the per-case invariants are recorded even when the run is killed."""
        os.environ["FAKE_GH_STATE"] = str(self.state)
        os.environ["PATH"] = str(self.bin) + os.pathsep + self.path
        refs = self.remote_refs()
        logged = len(self.log_rows())
        audit = len(GIT_AUDIT)
        try:
            return run_train(self.main, apply, only)
        finally:
            attempted = GIT_AUDIT[audit:]
            self.runs += 1
            self.violations += self._violations(refs, logged, attempted, apply)

    def _violations(self, refs, logged, attempted, apply):
        """Plan section 9, for one run: the remote base ref byte-identical; only refs/heads/<PR
        branch> changed, only by fast-forward and only under --apply; every logged ref update a
        fast-forward of a PR branch; no forbidden argv attempted."""
        bad = ["argv %r" % (a,) for a in attempted if _audit_violation(a)]
        after = self.remote_refs()
        if after.get("refs/heads/main") != refs.get("refs/heads/main"):
            bad.append("base ref refs/heads/main %s -> %s" % (
                refs.get("refs/heads/main"), after.get("refs/heads/main")))
        allowed = set("refs/heads/" + p["headRefName"] for p in self.prs)
        rows = self.log_rows()[logged:]
        moved_by_push = set(r[2] for r in rows)
        for ref in sorted(set(refs) | set(after)):
            old, new = refs.get(ref), after.get(ref)
            if old == new or ref not in moved_by_push:
                # An unlogged change is the fixture's own update-ref: the tool can change a ref
                # on the bare remote only through a logged receive.
                continue
            if not (apply and ref in allowed and old and new and self.fast_forward(old, new)):
                bad.append("ref %s %s -> %s" % (ref, old, new))
        for old, new, ref in rows:
            if ref not in allowed or ZERO in (old, new) or not self.fast_forward(old, new):
                bad.append("logged update %s %s -> %s" % (ref, old, new))
            elif old != refs.get(ref):
                bad.append("logged update %s moved from %s but the run started at %s (a mid-run "
                           "move was overwritten)" % (ref, old, refs.get(ref)))
        return bad

    def remote_refs(self):
        out = _git_text(self.remote, "for-each-ref", "--format=%(refname) %(objectname)")
        return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())

    def fast_forward(self, old, new):
        return _git(self.remote, "merge-base", "--is-ancestor", old, new, ok=None)[0] == 0

    def foreign_commit(self, parent, ref=None):
        """A commit made by someone else straight in the remote (parent's tree), optionally moving
        ref onto it from parent; the post-receive log does not see it."""
        sha = _git_text(self.remote, "commit-tree", parent + "^{tree}", "-p", parent, "-m",
                        "foreign", extra_env=FIXTURE_IDENT)
        if ref:
            _git(self.remote, "update-ref", ref, sha, parent)
        return sha

    def remote_ref(self, branch):
        code, out, _err = _git(self.remote, "rev-parse", "--verify", "-q",
                               "refs/heads/" + branch, ok=None)
        return out.decode().strip() if code == 0 else None

    def log_rows(self):
        if not self.push_log.exists():
            return []
        return [line.split() for line in self.push_log.read_text().splitlines() if line.strip()]

    def pushes(self):
        """Tool-made ref updates: branch creations and base advances made by the fixture are
        filtered out."""
        return [r for r in self.log_rows() if r[0] != ZERO and r[2] != "refs/heads/main"]


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


def _invariants(*fixtures):
    """The per-case invariants recorded over every tool run of the given fixtures; a fixture the
    case never ran through the tool is itself a violation."""
    bad = []
    for fx in fixtures:
        bad += ["%s: %s" % (fx.base.name, v) for v in fx.violations]
        if not fx.runs:
            bad.append("%s: no tool run observed" % fx.base.name)
    return bad


def _has_marker(wt):
    return os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME))


def case_config(_tmp):
    # Pure schema checks: no tool run, no git call and no remote, so the per-case invariants are
    # vacuous here and not asserted.
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
    fx2 = _fixture(tmp, "malformed")
    wt = _stale_pr(fx2)
    fx2.write(fx2.main, CONFIG_PATH, "version = 1\nextra = true\n")
    _git(fx2.main, "commit", "-q", "-am", "malformed config", extra_env=FIXTURE_IDENT)
    _git(fx2.main, "push", "-q", "origin", "main")
    before = _snapshot(wt)
    rc, reports, fatal = fx2.run(apply=True)
    check("config/malformed-exit2",
          (rc, reports, "malformed" in (fatal or ""), _snapshot_diff(before, _snapshot(wt))),
          (2, [], True, []))
    check("config/absent-invariants", _invariants(fx, fx2), [])


def case_frombase(tmp):
    fx = _fixture(tmp, "frombase")
    _stale_pr(fx)
    fx.write(fx.main, CONFIG_PATH, "version = 99\n")
    rc, reports, _fatal = fx.run()
    check("config/read-from-base-not-checkout", (rc, _result(reports, 1)), (0, "would-merge"))
    check("config/read-from-base-invariants", _invariants(fx), [])


def case_dry(tmp):
    fx = _fixture(tmp, "dry")
    wt = _stale_pr(fx)
    before = _snapshot(wt)
    remote_before = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run()
    check("dry/would-merge", (rc, _result(reports, 1)), (0, "would-merge"))
    check("dry/changes-nothing", (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
                                  fx.pushes()), ([], remote_before, []))
    check("dry/invariants", _invariants(fx), [])


def case_happy(tmp):
    fx = _fixture(tmp, "happy")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    base = fx.remote_ref("main")
    before = _snapshot(wt)
    del GIT_AUDIT[:]
    rc, reports, _fatal = fx.run(apply=True)
    report = reports[0] if reports else dict()
    new = fx.remote_ref("feat/x")
    check("apply/pushed", (rc, report.get("result"), report.get("new_head")), (0, "pushed", new))
    parents = _git_text(fx.remote, "rev-list", "--parents", "-n", "1", new).split()[1:]
    check("apply/parents-old-then-base", parents, [old, base])
    fields = _git_text(fx.remote, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B", new).split("|")
    check("apply/config-identity-no-ai-trailer",
          (fields[0], fields[1], "Co-Authored-By" in fields[2],
           ("Merge-train: base=" + base) in fields[2]),
          ("Train Maintainer <train@example.invalid>", "Train Maintainer <train@example.invalid>",
           False, True))
    fresh = Path(tmp) / "happy-fresh"
    _git(fx.base, "clone", "--quiet", "--no-local", str(fx.remote), str(fresh))
    _git(fresh, "checkout", "--quiet", "feat/x")
    check("apply/generated-fresh",
          subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"], cwd=str(fresh),
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
    check("apply/argv-audit-clean", [a for a in GIT_AUDIT if _forbidden(a) or _audit_violation(a)],
          [])
    tool_pushes = [a for a in GIT_AUDIT if a[:1] == ["push"] and "--porcelain" in a]
    check("apply/push-leased-to-validated-head",
          (len(tool_pushes), all(("--force-with-lease=refs/heads/feat/x:" + old) in a
                                 for a in tool_pushes)), (1, True))
    check("apply/marker-removed", _has_marker(wt), False)
    check("apply/author-worktree-untouched",
          (_snapshot_diff(before, _snapshot(wt)), _git_text(wt, "rev-parse", "HEAD")), ([], old))
    check("apply/local-branch-not-fast-forwarded",
          _git_text(fx.main, "rev-parse", "refs/heads/feat/x"), old)
    check("apply/scratch-removed", [p for p in SCRATCHES if os.path.exists(p)], [])
    fx.prs[0]["headRefOid"] = new
    fx.set_gh()
    rc2, reports2, _fatal = fx.run(apply=True)
    check("apply/second-run-current", (rc2, _result(reports2, 1), len(fx.pushes())),
          (0, "current", 1))
    check("apply/invariants", _invariants(fx), [])


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
    check("carry/invariants", _invariants(fx, fx2), [])


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
    check("discover/invariants", _invariants(fx), [])


def case_ambiguous_worktree(tmp):
    fx = _fixture(tmp, "ambiguous")
    _stale_pr(fx)
    _git(fx.main, "worktree", "add", "-q", "-f", str(fx.base / "wt-dup"), "feat/x")
    rc, reports, _fatal = fx.run()
    check("discover/ambiguous-worktree", (rc, _result(reports, 1)), (1, "ambiguous-worktree"))
    check("discover/ambiguous-worktree-invariants", _invariants(fx), [])


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
    check("discover/branch-invariants", _invariants(fx), [])


def case_badname(tmp):
    # Git accepts + and @ in branch names; the lease cannot express them, so the run must refuse
    # with a named status BEFORE any work: no clone, merge, commit or push is attempted.
    fx = _fixture(tmp, "badname")
    trees = []
    for branch in ("feat/x+plus", "feat/x@at"):
        number = len(fx.prs) + 1
        path = fx.base / ("wt-%d" % number)
        _git(fx.main, "worktree", "add", "-q", "-b", branch, str(path), "main")
        fx.write(path, "src/extra-%d.txt" % number, "payload\n")
        head = fx.commit(path, "pr %d" % number)
        _git(fx.remote, "fetch", "--no-tags", str(fx.main),
             "refs/heads/%s:refs/heads/%s" % (branch, branch))
        fx.prs.append(dict(number=number, headRefName=branch, headRefOid=head,
                           isCrossRepository=False, baseRefName="main"))
        trees.append(path)
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    befores = [_snapshot(p) for p in trees]
    audit = len(GIT_AUDIT)
    rc, reports, _fatal = fx.run(apply=True)
    worked = [a for a in GIT_AUDIT[audit:] if a[:1] in (
        ["clone"], ["merge"], ["commit-tree"], ["push"], ["checkout"], ["add"], ["write-tree"])]
    check("refuse/bad-branch-name-plus", (rc, _result(reports, 1)), (1, "bad-branch-name"))
    check("refuse/bad-branch-name-at", _result(reports, 2), "bad-branch-name")
    check("refuse/bad-branch-name-before-any-work",
          (worked, fx.pushes(), [_snapshot_diff(b, _snapshot(p)) for b, p in zip(befores, trees)],
           [_has_marker(p) for p in trees]),
          ([], [], [[], []], [False, False]))
    check("refuse/bad-branch-name-invariants", _invariants(fx), [])


def case_audit(_tmp):
    # Pure predicate checks over forbidden and allowed push argv forms: no tool run, no git call.
    # Both predicates must refuse every forbidden form and accept the one lease shape, and they
    # must AGREE on every probe.
    def verdict(args):
        return (_forbidden(args), _audit_violation(args))

    both = (True, True)
    neither = (False, False)
    l40 = "--force-with-lease=refs/heads/feat/x:" + "6" * 40
    l64 = "--force-with-lease=refs/heads/feat/x:" + "6" * 64
    probes = []

    def probed(args):
        probes.append(list(args))
        return verdict(args)

    check("audit/push-f", probed(["push", "-f", "origin", "x"]), both)
    check("audit/push-bundled-uf", probed(["push", "-uf", "origin", "x"]), both)
    check("audit/push-bundled-fu", probed(["push", "-fu", "origin", "x"]), both)
    check("audit/push-d", probed(["push", "-d", "origin", "x"]), both)
    check("audit/push-delete", probed(["push", "--delete", "origin", "x"]), both)
    check("audit/push-delete-abbrev", probed(["push", "--dele", "origin", "x"]), both)
    check("audit/push-mirror", probed(["push", "--mirror", "origin"]), both)
    check("audit/push-mirror-abbrev", probed(["push", "--mirr", "origin"]), both)
    check("audit/push-all", probed(["push", "--all", "origin"]), both)
    check("audit/push-prune", probed(["push", "--prune", "origin", "x"]), both)
    check("audit/push-force-bare", probed(["push", "--force", "origin", "x"]), both)
    check("audit/push-force-abbrev", probed(["push", "--forc", "origin", "x"]), both)
    check("audit/push-force-if-includes",
          probed(["push", "--force-if-includes", l40, "origin", "x"]), both)
    check("audit/push-lease-bare", probed(["push", "--force-with-lease", "origin", "x"]), both)
    check("audit/push-lease-short-sha",
          probed(["push", "--force-with-lease=refs/heads/feat/x:6666", "origin", "x"]), both)
    check("audit/push-lease-plus-branch",
          probed(["push", "--force-with-lease=refs/heads/feat/x+y:" + "6" * 40, "origin", "x"]),
          both)
    check("audit/push-plus-refspec",
          probed(["push", "--", "origin", "+feat/x:refs/heads/feat/x"]), both)
    check("audit/push-colon-refspec", probed(["push", "--", "origin", ":refs/heads/feat/x"]), both)
    check("audit/push-src-other-refspec",
          probed(["push", "--", "origin", "feat/x:refs/heads/other"]), both)
    check("audit/push-to-base-refspec",
          probed(["push", "--", "origin", ("6" * 40) + ":refs/heads/main"]), both)
    check("audit/reset-refused", probed(["reset", "--keep", "6" * 40]), both)
    check("audit/lease-40-allowed",
          probed(["push", "--porcelain", "--no-follow-tags", l40, "--", "origin",
                  ("6" * 40) + ":refs/heads/feat/x"]), neither)
    check("audit/lease-64-allowed",
          probed(["push", "--porcelain", "--no-follow-tags", l64, "--", "origin",
                  ("6" * 64) + ":refs/heads/feat/x"]), neither)
    check("audit/predicates-agree",
          [a for a in probes if _forbidden(a) != _audit_violation(a)], [])


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
    check("refuse/source-conflict-invariants", _invariants(fx), [])


def _refusal_run(tmp, name, env_key=None, failing_check=False, seen=None):
    """A stale-generated PR run with --apply under one injected generator fault; returns
    (exit code, result, reason, (snapshot differences, tool pushes)). The fixture is appended to
    seen for the invariant check."""
    fx = _fixture(tmp, name)
    if seen is not None:
        seen.append(fx)
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
    # Every generator fault happens in the PRIVATE checkout: the refusal is exit 1 and the author's
    # worktree is untouched by construction; there is no restore-failed state left to reach.
    seen = []
    rc, result, _reason, untouched = _refusal_run(tmp, "regen-fail", "TOY_FAIL", seen=seen)
    check("refuse/regenerate-failed", (rc, result, untouched), (1, "regenerate-failed", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "check-fail", failing_check=True, seen=seen)
    check("refuse/check-failed", (rc, result, untouched), (1, "check-failed", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "not-fixpoint", "TOY_DRIFT", seen=seen)
    check("refuse/regenerate-not-fixpoint", (rc, result, untouched),
          (1, "regenerate-not-fixpoint", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "litter", "TOY_LITTER", seen=seen)
    check("refuse/undeclared-write", (rc, result, untouched), (1, "undeclared-write", ([], [])))
    check("refuse/scratch-removed", [p for p in SCRATCHES if os.path.exists(p)], [])
    check("refuse/regenerate-invariants", _invariants(*seen), [])


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
    check("push/rejected-invariants", _invariants(fx), [])


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

    def push_then_kill(ctx, scratch, _old, new):
        _git(scratch, "push", "-q", "--", ctx["remote_url"],
             "%s:refs/heads/%s" % (new, ctx["branch"]))
        raise KeyboardInterrupt("simulated kill after the push")

    def kill_in_merge(*_args):
        raise KeyboardInterrupt("simulated kill mid-merge")

    seen = []
    fx = _fixture(tmp, "kill-commit")
    seen.append(fx)
    _stale_pr(fx)
    _killed_run(fx, kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-commit-one-push", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "pushed", 1))
    fx = _fixture(tmp, "kill-push")
    seen.append(fx)
    _stale_pr(fx)
    _killed_run(fx, push_then_kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-push-already-pushed", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "already-pushed", 1))
    fx = _fixture(tmp, "kill-merge")
    seen.append(fx)
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
    # An author's own in-progress merge lives in the author's worktree, which the tool never
    # touches or even inspects for state: the run proceeds in its private checkout and the
    # author's half-done merge survives byte-identically.
    fx = _fixture(tmp, "author-merge")
    seen.append(fx)
    wt = _stale_pr(fx)
    _git(wt, "merge", "--no-commit", "--no-ff", fx.remote_ref("main"), ok=None,
         extra_env=FIXTURE_IDENT)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/author-merge-in-progress-ignored",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt))), (0, "pushed", []))
    check("idem/invariants", _invariants(*seen), [])


def _reason(reports, number):
    rows = [r for r in reports if r["pr"] == number]
    return rows[0]["reason"] if rows else ""


def case_remote_changed(tmp):
    fx = _fixture(tmp, "remote-changed")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    other = fx.foreign_commit(old)
    # The third gh view (the re-check after the commit, just before the push) moves the PR branch
    # forward as a concurrent pusher would, so the exact-value lease no longer holds and the
    # remote refuses the push.
    fx.set_gh(move_on_view=dict([("1", dict(call=2, remote=str(fx.remote), ref="refs/heads/feat/x",
                                            old=old, new=other))]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("push/remote-changed", (rc, _result(reports, 1), other in _reason(reports, 1)),
          (1, "remote-changed", True))
    check("push/remote-changed-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes() == pushed,
           _has_marker(wt)), ([], other, True, False))
    check("push/remote-changed-invariants", _invariants(fx), [])


def case_push_unknown(tmp):
    fx = _fixture(tmp, "push-unknown")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    # The post-receive hook points the checkout's origin at a missing path once the push has
    # landed, so the observer's ls-remote cannot read the remote: indeterminate, marker kept,
    # nothing changed anywhere.
    (fx.base / "unreadable-after-push").write_text("", encoding="utf-8")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/unknown-keeps-marker-changes-nothing",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)),
           marker.get("new") == fx.remote_ref("feat/x"), marker.get("old"), len(fx.pushes())),
          (1, "push-unknown", [], True, old, 1))
    _git(fx.main, "config", "remote.origin.url", str(fx.remote))
    rc, reports, _fatal = fx.run(apply=True)
    check("push/unknown-rerun-already-pushed",
          (rc, _result(reports, 1), _has_marker(wt), len(fx.pushes())),
          (0, "already-pushed", False, 1))
    check("push/unknown-invariants", _invariants(fx), [])


def case_generated_delete(tmp):
    fx = _fixture(tmp, "generated-delete")
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    _git(wt, "rm", "-q", "--", "gen/digest.txt")
    _git(wt, "commit", "-q", "-m", "pr drops the generated file", extra_env=FIXTURE_IDENT)
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % _git_text(wt, "rev-parse", "HEAD"))
    fx.prs[0]["headRefOid"] = old = _git_text(wt, "rev-parse", "HEAD")
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/generated-delete-conflict",
          (rc, _result(reports, 1), _reason(reports, 1)), (1, "generated-delete-conflict",
                                                           "gen/digest.txt"))
    check("refuse/generated-delete-conflict-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes() == pushed,
           _has_marker(wt)), ([], old, True, False))
    check("refuse/generated-delete-conflict-invariants", _invariants(fx), [])


def case_author(tmp):
    # The core promise of D-390-PRIVATE-WORKTREE: an author commit, a staged edit, an unstaged
    # edit, an untracked file and an ignored file in the author's worktree survive a pushing run
    # byte-identically (snapshot: worktree, index, refs, ignored contents), present through EVERY
    # phase because they exist before the run starts; the local branch is never fast-forwarded.
    fx = _fixture(tmp, "author")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    fx.write(wt, "ignored/valuable", "KEEP\n")
    fx.write(wt, "untracked.txt", "author untracked\n")
    fx.write(wt, "staged.txt", "author staged\n")
    _git(wt, "add", "--", "staged.txt")
    fx.write(wt, "src/b.txt", "author unstaged edit\n")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    check("author/dirty-worktree-untouched-and-pushed",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)), new != old),
          (0, "pushed", [], True))
    check("author/local-branch-not-advanced",
          (_git_text(wt, "rev-parse", "HEAD"),
           _git_text(fx.main, "rev-parse", "refs/heads/feat/x")), (old, old))
    # An author commit injected DURING the run, at the start-of-run gh view and at the pre-push gh
    # view: the run still pushes, and the commit stays the local branch tip, never rewound.
    fx2 = _fixture(tmp, "author-start")
    wt2 = _stale_pr(fx2)
    fx2.set_gh(commit_on_view=dict([("1", dict(call=1, worktree=str(wt2),
                                               file="author-start.txt"))]))
    rc, reports, _fatal = fx2.run(apply=True)
    check("author/mid-run-commit-at-start-kept",
          (rc, _result(reports, 1), _git_text(wt2, "log", "-1", "--format=%s"),
           os.path.exists(os.path.join(str(wt2), "author-start.txt"))),
          (0, "pushed", "author race", True))
    fx3 = _fixture(tmp, "author-prepush")
    wt3 = _stale_pr(fx3)
    fx3.set_gh(commit_on_view=dict([("1", dict(call=2, worktree=str(wt3),
                                               file="author-prepush.txt"))]))
    rc, reports, _fatal = fx3.run(apply=True)
    check("author/mid-run-commit-pre-push-kept",
          (rc, _result(reports, 1), _git_text(wt3, "log", "-1", "--format=%s"),
           os.path.exists(os.path.join(str(wt3), "author-prepush.txt"))),
          (0, "pushed", "author race", True))
    check("author/invariants", _invariants(fx, fx2, fx3), [])


def case_observer(tmp):
    # The observer's ls-remote times out AFTER the push was delivered: indeterminate, the marker is
    # kept, nothing local changes, and the rerun reconciles to already-pushed.
    fx = _fixture(tmp, "observer")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    before = _snapshot(wt)
    real_run = subprocess.run
    state = dict(pushed=False)

    def hostile_run(argv, **kwargs):
        if "push" in argv and "--porcelain" in argv:
            state["pushed"] = True
            return real_run(argv, **kwargs)
        if state["pushed"] and "ls-remote" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return real_run(argv, **kwargs)

    subprocess.run = hostile_run
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        subprocess.run = real_run
    new = fx.remote_ref("feat/x")
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/observer-timeout-indeterminate",
          (rc, _result(reports, 1), new != old, marker.get("new") == new,
           _snapshot_diff(before, _snapshot(wt)), _git_text(wt, "rev-parse", "HEAD")),
          (1, "push-unknown", True, True, [], old))
    rc, reports, _fatal = fx.run(apply=True)
    check("push/observer-timeout-rerun-already-pushed",
          (rc, _result(reports, 1), _has_marker(wt), len(fx.pushes())),
          (0, "already-pushed", False, 1))
    check("push/observer-timeout-invariants", _invariants(fx), [])


def case_rewind(tmp):
    # The author force-rewinds the PR branch between the last re-check and the push: the
    # exact-value lease makes the remote refuse, and the rewind is preserved, never overwritten.
    fx = _fixture(tmp, "rewind")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    parent = _git_text(wt, "rev-parse", old + "^")
    fx.set_gh(move_on_view=dict([("1", dict(call=2, remote=str(fx.remote),
                                            ref="refs/heads/feat/x", old=old, new=parent))]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("push/rewound-branch-refused",
          (rc, _result(reports, 1), fx.remote_ref("feat/x"),
           _snapshot_diff(before, _snapshot(wt)), _has_marker(wt)),
          (1, "remote-changed", parent, [], False))
    check("push/rewound-invariants", _invariants(fx), [])


def case_timeout(tmp):
    # A push that times out AFTER delivery is indeterminate: the observer reads the remote and
    # decides, so the delivered push is reported pushed and nothing is rolled back blind.
    fx = _fixture(tmp, "timeout")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    real_run = subprocess.run

    def timeout_run(argv, **kwargs):
        proc = real_run(argv, **kwargs)
        if "push" in argv and "--porcelain" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return proc

    subprocess.run = timeout_run
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        subprocess.run = real_run
    new = fx.remote_ref("feat/x")
    check("push/timeout-observed-delivered",
          (rc, _result(reports, 1), new != old, _git_text(wt, "rev-parse", "HEAD") == old,
           _has_marker(wt)), (0, "pushed", True, True, False))
    check("push/timeout-invariants", _invariants(fx), [])


def case_symlink(tmp):
    # A declared generated path that is a symlink would let the regenerator write outside the
    # declared set: refused before any regenerate command runs.
    fx = _fixture(tmp, "symlink")
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    outside = Path(tmp) / "outside-payload.txt"
    outside.write_text("KEEP\n", encoding="utf-8")
    os.unlink(os.path.join(str(wt), "gen", "digest.txt"))
    os.symlink(str(outside), os.path.join(str(wt), "gen", "digest.txt"))
    _git(wt, "add", "-A", "--", "gen/digest.txt")
    _git(wt, "commit", "-q", "-m", "digest becomes a symlink", extra_env=FIXTURE_IDENT)
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % _git_text(wt, "rev-parse", "HEAD"))
    fx.prs[0]["headRefOid"] = old = _git_text(wt, "rev-parse", "HEAD")
    fx.set_gh()
    fx.advance_main(dict([("docs/note.txt", "note\n")]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/generated-symlink",
          (rc, _result(reports, 1), outside.read_text(encoding="utf-8"),
           _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)),
          (1, "generated-symlink", "KEEP\n", [], old, True, False))
    probe = Path(tmp) / "symlink-dir"
    (probe / "real").mkdir(parents=True)
    os.symlink(str(probe / "real"), str(probe / "gen"))
    (probe / "real" / "digest.txt").write_text("x\n", encoding="utf-8")
    try:
        _guard("generated-confined")(probe, ["gen/digest.txt"])
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/generated-symlink-parent", got, "generated-symlink")
    check("refuse/generated-symlink-invariants", _invariants(fx), [])


def case_tamper(tmp):
    # Author-side hooks never run: the merge, checks and commit happen in the private checkout
    # (hooks disabled, and the commit is commit-tree, which runs no hook anywhere), so a hook that
    # edits and restages sources can neither block the run nor smuggle content into the push.
    fx = _fixture(tmp, "tamper")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    for name in ("pre-commit", "post-commit", "post-merge"):
        hook = fx.main / ".git" / "hooks" / name
        hook.write_text("#!/bin/sh\nprintf 'HOOK CHANGED SOURCE\\n' >> src/a.txt\n"
                        "git add -- src/a.txt\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    blob = _git(fx.remote, "cat-file", "blob", new + ":src/a.txt")[1]
    check("refuse/hooks-never-run",
          (rc, _result(reports, 1), b"HOOK CHANGED SOURCE" in blob,
           _snapshot_diff(before, _snapshot(wt))), (0, "pushed", False, []))
    # Unit probes of the two commit guards over a real private-style checkout: a parentless
    # (hook-rewritten) commit and a commit carrying a foreign tree are both refused, so neither
    # can ever be pushed.
    probe = Path(tmp) / "tamper-probe"
    _git(fx.base, "clone", "--quiet", "--shared", "--no-checkout", str(fx.main), str(probe))
    _guard("scratch-setup")(probe)
    _git(probe, "checkout", "--quiet", "--detach", old)
    tree = _git_text(probe, "rev-parse", old + "^{tree}")
    orphan = _git_text(probe, "commit-tree", tree, "-m", "orphan", extra_env=FIXTURE_IDENT)
    try:
        _guard("commit-parents")(probe, old, orphan)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/commit-parents-probe", got, "commit-mismatch")
    other_tree = _git_text(probe, "rev-parse", fx.remote_ref("main") + "^{tree}")
    swapped = _git_text(probe, "commit-tree", other_tree, "-p", old, "-m", "wrong tree",
                        extra_env=FIXTURE_IDENT)
    try:
        _guard("commit-tree")(probe, swapped, tree)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/commit-tree-probe", got, "commit-mismatch")
    check("refuse/tamper-invariants", _invariants(fx), [])


def case_filter(tmp):
    # A clean filter in the author's repository (codex round-2 reproduction): the private checkout
    # is a fresh clone that defines no filter and unsets every conversion attribute, so the checks
    # read exactly the stored blobs and the pushed tree is self-consistent: a fresh materialization
    # of the pushed commit passes the declared check.
    fx = _fixture(tmp, "filter")
    _git(fx.main, "config", "filter.constant.clean", "printf FILTERED")
    _git(fx.main, "config", "filter.constant.smudge", "cat")
    wt = fx.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt filter=constant\n")]))
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    fresh = Path(tmp) / "filter-fresh"
    _git(fx.base, "clone", "--quiet", "--no-local", str(fx.remote), str(fresh))
    _git(fresh, "checkout", "--quiet", "feat/x")
    fresh_check = subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"],
                                 cwd=str(fresh), env=_clean_env(), timeout=60).returncode
    check("filter/clean-filter-pushed-correctly",
          (rc, _result(reports, 1), new is not None, fresh_check,
           _snapshot_diff(before, _snapshot(wt))), (0, "pushed", True, 0, []))
    # Unit probe of the every-path byte guard: a private checkout whose file bytes diverge from the
    # committed blob (what any conversion would produce) is refused before the push.
    probe = Path(tmp) / "filter-probe"
    _git(fx.base, "clone", "--quiet", "--shared", "--no-checkout", str(fx.main), str(probe))
    _guard("scratch-setup")(probe)
    _git(probe, "checkout", "--quiet", "--detach", fx.remote_ref("main"))
    tree = _git_text(probe, "rev-parse", "HEAD^{tree}")
    (probe / "src" / "a.txt").write_text("DIVERGED\n", encoding="utf-8")
    try:
        _guard("pushed-bytes")(probe, tree)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("filter/pushed-bytes-probe", got, "commit-mismatch")
    check("filter/invariants", _invariants(fx), [])


def case_eol(tmp):
    # An eol/text attribute in the PR (the autocrlf reproduction): with conversions disabled in the
    # private checkout the materialized bytes equal the stored blob, the regenerated digest is
    # computed over those same bytes, and the pushed source blob is exactly the PR's LF content.
    # With the scratch-setup guard reverted, the CRLF materialization diverges from the blob and
    # the every-path byte guard refuses the commit instead of pushing it.
    fx = _fixture(tmp, "eol")
    wt = fx.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt text eol=crlf\n")]))
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    blob = _git(fx.remote, "cat-file", "blob", new + ":src/b.txt")[1] if new else b""
    check("filter/eol-attribute-pushed-correctly",
          (rc, _result(reports, 1), blob, _snapshot_diff(before, _snapshot(wt))),
          (0, "pushed", b"bravo\n", []))
    check("filter/eol-invariants", _invariants(fx), [])


def case_lock_held(tmp):
    fx = _fixture(tmp, "lock-held")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    common = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-common-dir")
    before, pushed = _snapshot(wt), fx.pushes()
    with open(os.path.join(common, LOCK_NAME), "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        audit = len(GIT_AUDIT)
        rc, reports, fatal = fx.run(apply=True)
        fetched = [a for a in GIT_AUDIT[audit:] if a[:1] == ["fetch"]]
    check("run/lock-held-exit2", (rc, reports, LOCK_NAME in (fatal or "")), (2, [], True))
    check("run/lock-held-touches-nothing",
          (fetched, _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)), ([], [], old, True, False))
    check("run/lock-held-invariants", _invariants(fx), [])


def case_fetch_failure(tmp):
    fx = _fixture(tmp, "fetch-failure")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    before, pushed = _snapshot(wt), fx.pushes()
    _git(fx.main, "config", "remote.origin.url", str(fx.gone))
    audit = len(GIT_AUDIT)
    try:
        rc, reports, fatal = fx.run(apply=True)
    finally:
        _git(fx.main, "config", "remote.origin.url", str(fx.remote))
    touching = [a for a in GIT_AUDIT[audit:] if a[:1] in (
        ["merge"], ["commit-tree"], ["push"], ["ls-remote"], ["checkout"], ["add"], ["clone"])]
    check("run/fetch-failure-exit2", (rc, reports, "fetch failed" in (fatal or "")), (2, [], True))
    check("run/fetch-failure-touches-nothing",
          (touching, _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)), ([], [], old, True, False))
    check("run/fetch-failure-invariants", _invariants(fx), [])


def case_state_mismatch(tmp):
    # A marker naming another PR or branch is refused untouched; a marker also blocks a dry run.
    fx = _fixture(tmp, "state-mismatch")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    git_dir = _git_dir(wt)
    _write_marker(git_dir, dict(pr=7, branch="feat/other", old=old, new=ZERO))
    marker = Path(git_dir) / MARKER_NAME
    marker_bytes = marker.read_bytes()
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/state-mismatch-refused", (rc, _result(reports, 1)), (1, "state-mismatch"))
    rc, reports, _fatal = fx.run()
    check("idem/state-mismatch-dry-run", (rc, _result(reports, 1)), (1, "state-mismatch"))
    check("idem/state-mismatch-touches-nothing",
          (_snapshot_diff(before, _snapshot(wt)), marker.read_bytes() == marker_bytes,
           fx.remote_ref("feat/x"), fx.pushes() == pushed), ([], True, old, True))
    check("idem/state-mismatch-invariants", _invariants(fx), [])


def case_cli(tmp):
    proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--bogus"],
                          capture_output=True, timeout=60, env=_clean_env())
    check("cli/bad-argument-exit2", proc.returncode, 2)
    plain = Path(tmp) / "not-a-repo"
    plain.mkdir()
    audit = len(GIT_AUDIT)
    rc, reports, fatal = run_train(plain)
    check("cli/not-a-repo-exit2", (rc, reports, bool(fatal)), (2, [], True))
    # No remote exists here, so of the per-case invariants only the argv audit applies.
    check("cli/invariants", [a for a in GIT_AUDIT[audit:] if _audit_violation(a)], [])


CASES = dict([
    ("config", case_config), ("absent", case_absent), ("frombase", case_frombase),
    ("dry", case_dry), ("happy", case_happy), ("carry", case_carry), ("discover", case_discover),
    ("ambiguous", case_ambiguous_worktree), ("branch", case_branch), ("badname", case_badname),
    ("audit", case_audit), ("conflict", case_conflict), ("regen", case_regen),
    ("reject", case_reject), ("idem", case_idem), ("remote-changed", case_remote_changed),
    ("push-unknown", case_push_unknown), ("generated-delete", case_generated_delete),
    ("author", case_author), ("rewind", case_rewind), ("timeout", case_timeout),
    ("observer", case_observer), ("symlink", case_symlink), ("tamper", case_tamper),
    ("filter", case_filter), ("eol", case_eol), ("lock-held", case_lock_held),
    ("fetch-failure", case_fetch_failure), ("state-mismatch", case_state_mismatch),
    ("cli", case_cli),
])


def _noop(*_args, **_kwargs):
    return None


def _reraise(exc):
    """The reverted push-indeterminate guard: decide from the error alone, without observing."""
    raise exc


# Red-on-revert: each guard, replaced in turn by its reverted form, must turn its named case red.
REVERTS = dict([
    ("config-fail-closed", (_noop, "config")),
    ("config-source", (lambda root, _sha: _read_text(os.path.join(str(root), CONFIG_PATH)),
                       "frombase")),
    ("branch-name", (_noop, "badname")),
    ("branch-match", (_noop, "branch")),
    ("source-conflict", (_noop, "conflict")),
    ("regenerate-exit", (_noop, "regen")),
    ("check-exit", (_noop, "regen")),
    ("fixpoint", (_noop, "regen")),
    ("reconcile", (_noop, "push-unknown")),
    ("scratch-setup", (_noop, "eol")),
    ("generated-confined", (_noop, "symlink")),
    ("pushed-bytes", (_noop, "filter")),
    ("commit-parents", (_noop, "tamper")),
    ("commit-tree", (_noop, "tamper")),
    ("push-lease", (lambda branch, old: [], "rewind")),
    ("push-indeterminate", (_reraise, "timeout")),
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
