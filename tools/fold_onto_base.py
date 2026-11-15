#!/usr/bin/env python3
"""Fold a branch's commits into one commit whose parent is the merge base of the branch and the
protected line, never the protected line's moved tip.

A fold that records the branch tip's tree on a NEWER protected tip (for example after a fetch in
another worktree advanced the remote-tracking ref) reverts every change the protected line made in
between. This helper derives the merge base B of the checked-out branch tip H and the protected ref
M, builds N = commit-tree H^{tree} -p B with git plumbing (never through the index), verifies that N
has exactly one parent, B, and the tree of H, records H under a create-only recovery ref
refs/aiqt-fold/<branch>/<utc>, and then moves the branch from H to N with a compare-and-swap
update-ref. It never pushes, fetches, rebases or resets, and never touches the index or worktree:
because N's tree equals H's tree, the checkout stays consistent after the update.

Refused (exit 1) unless: HEAD is symbolic to a branch other than main, master, the protected ref
itself or the local copy of the protected line (for --protected origin/trunk, the local trunk); the
--protected name is not ambiguous; the index and worktree carry no tracked change; no merge, rebase,
cherry-pick, revert, bisect or sequencer state is present; the branch and the protected ref have
exactly one merge base B, B is not H, and B equals --expect-base when that is given; and B..H holds no
merge commit. A compare-and-swap that loses (the branch moved after it was read) is refused too,
leaving the branch where the other writer put it. A shallow repository, a git failure, a failed commit
signature the repository's own signing configuration asks for, or a malformed argument is
cannot-evaluate (exit 2).

  fold_onto_base.py --protected REF --message-file ABS_PATH [--expect-base FULL_SHA] [--root DIR]
  fold_onto_base.py --self-test

Every git call runs the git executable resolved once, to an absolute path, when this module loads; it
binds the repository with -C and runs with every ambient GIT_ variable removed (the environment of
tools/check_branch_root.py: no system config, no lazy fetch, no prompt, no optional locks, no replace
objects), with GIT_GRAFT_FILE naming a path that cannot exist (so an info/grafts file cannot change a
parent or the merge base), and with core.fsmonitor=false, core.hooksPath set to that same nonexistent
path (no hook runs, the reference-transaction hook included), core.commitGraph=false,
protocol.allow=never and core.warnAmbiguousRefs=true in option position. The printed undo command
quotes every argument for a POSIX shell and binds the repository with git -C.

Not neutralized, and disclosed: a decoy git that is already first on PATH when this module loads is the
git it resolves; the repository's own signing configuration (commit.gpgSign, gpg.program,
user.signingKey) is honoured on purpose; a clean or process filter driver from the repository's or the
user's configuration can run during git status; index bits (assume-unchanged, skip-worktree) and
settings such as core.fileMode or core.ignoreStat can hide a worktree change from the dirty check (the
fold changes no index or worktree file and reads the tree from the commit, so such a change stays in the
worktree). A lost compare-and-swap leaves the recovery ref, its reflog and the unreferenced folded
commit, and a recovery-ref creation that fails leaves the unreferenced folded commit; both are
documented residue. Exit: 0 folded; 1 refused; 2 usage or cannot-evaluate.
Stdlib only.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: fold_onto_base.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import contextlib
import datetime
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 30
MESSAGE_MAX_BYTES = 65536
PROTECTED_BRANCHES = ("refs/heads/main", "refs/heads/master")
RECOVERY_PREFIX = "refs/aiqt-fold/"
# A path that cannot exist: a child of the null device, which is not a directory, so an open of it
# fails with ENOTDIR, which git reads as "absent" without a warning. GIT_GRAFT_FILE and core.hooksPath
# name it, so no graft file is read and no hook runs.
NO_SUCH_PATH = os.path.join(os.devnull, "aiqt-fold-no-such-path")
# The closed set of git subcommands this helper runs. push, fetch, pull, rebase, reset, checkout,
# switch, merge, commit, add and write-tree are deliberately absent: the helper never moves anything
# but the one branch ref (and creates its recovery ref), and never builds a tree from the index.
GIT_SUBCOMMANDS = frozenset((
    "rev-parse", "symbolic-ref", "status", "merge-base", "rev-list", "cat-file", "commit-tree",
    "update-ref", "check-ref-format", "config", "for-each-ref"))
# Configuration pinned in option position on every git call, after the maintenance pins: no fsmonitor
# program, no hook (core.hooksPath names a path that cannot exist), no commit-graph cache (it records
# parents), no transport, and an ambiguous ref name reported rather than silently resolved.
HARDENING = ("--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + str(NO_SUCH_PATH), "-c", "core.commitGraph=false",
             "-c", "protocol.allow=never", "-c", "core.warnAmbiguousRefs=true")


def _resolve_git():
    """The git executable as an absolute path, resolved once when this module loads, so a PATH change
    later in the process (or a relative PATH entry and a cwd change) cannot select another git. A decoy
    already first on PATH at load is what this resolves (disclosed); the bare name is the fallback only
    when no git is on PATH."""
    found = shutil.which("git")
    return os.path.abspath(found) if found else "git"


_GIT = _resolve_git()
# Repository states in which a fold would race an operation in progress (each a git-path name).
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge",
               "rebase-apply", "sequencer")
# Self-test seam (vector H7): called with (root, branch_ref) after the recovery ref is written and
# before the compare-and-swap, so a writer can move the branch in between. None in production.
_BEFORE_SWAP = None


class CannotEvaluate(RuntimeError):
    """A required input or git result could not answer the question (exit 2)."""


class Refused(RuntimeError):
    """A precondition of a safe fold does not hold (exit 1)."""


def _git_env():
    """The scrubbed environment of tools/check_branch_root.py: every ambient GIT_ variable removed
    (GIT_DIR, GIT_INDEX_FILE, GIT_WORK_TREE, GIT_CONFIG_* and the rest), then an offline,
    non-interactive posture reasserted. Replace refs are disabled (GIT_NO_REPLACE_OBJECTS) and
    GIT_GRAFT_FILE names a path that cannot exist, so neither a replace ref nor an info/grafts file can
    change a parent or the merge base: GIT_NO_REPLACE_OBJECTS alone leaves info/grafts in force."""
    env = dict(os.environ)
    for key in [key for key in env if key.startswith("GIT_")]:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = NO_SUCH_PATH
    return env


def _git(root, *args, data=None):
    """Run _GIT bound to root with -C, under _git_env, bytes in and out. Only GIT_SUBCOMMANDS run. The
    automatic-maintenance pins (gc.auto=0, gc.autoDetach=false, maintenance.auto=false) ride the argv in
    option position, the form tools/selftest_git_fixture_env.py's maintenance-pin scan reads, so no call
    can start a detached gc or maintenance child; HARDENING follows them, also in option position."""
    if not args or args[0] not in GIT_SUBCOMMANDS:
        raise CannotEvaluate("internal: git subcommand {!r} is outside the helper's closed set".format(
            args[0] if args else None))
    try:
        return subprocess.run(
            [_GIT, "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
             *HARDENING, "-C", os.fspath(root), *args],
            input=data,
            capture_output=True,
            timeout=TIMEOUT,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git invocation failed: {}".format(exc))


def _diag(proc):
    return proc.stderr.decode("utf-8", "replace").strip() or "no diagnostic"


def _oid(raw):
    """A full lowercase hex object id (SHA-1 or SHA-256) from one line of git output, or None."""
    token = raw.strip()
    if len(token) in (40, 64) and all(char in b"0123456789abcdef" for char in token):
        return token.decode("ascii")
    return None


def _repo_root(requested):
    probe = _git(requested, "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        raise CannotEvaluate("{} is not a readable git repository ({})".format(requested, _diag(probe)))
    lines = probe.stdout.split(b"\n")
    if len(lines) != 2 or not lines[0] or lines[1]:
        raise CannotEvaluate("git rev-parse returned no unique repository root")
    try:
        resolved = Path(os.fsdecode(lines[0])).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("repository root cannot be resolved: {}".format(exc))
    if not resolved.is_dir():
        raise CannotEvaluate("repository root is not a directory: {}".format(resolved))
    return resolved


def _resolve(root, revision, what):
    probe = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options",
                 "{}^{{commit}}".format(revision))
    sha = _oid(probe.stdout) if probe.returncode == 0 else None
    if sha is None:
        raise CannotEvaluate("{} {!r} does not resolve to one commit".format(what, revision))
    return sha


def _branch(root):
    """The full ref HEAD is symbolic to; refused when HEAD is detached."""
    probe = _git(root, "symbolic-ref", "-q", "HEAD")
    if probe.returncode == 1:
        raise Refused("HEAD is detached; check out the branch to fold")
    if probe.returncode != 0:
        raise CannotEvaluate("git symbolic-ref HEAD failed ({})".format(_diag(probe)))
    ref = os.fsdecode(probe.stdout.strip())
    if not ref.startswith("refs/heads/") or len(ref) == len("refs/heads/"):
        raise Refused("HEAD points at {!r}, which is not a local branch".format(ref))
    return ref


def _dwim_candidates(name):
    """The full ref names git's short-name rules try for name, in git's order."""
    return [name, "refs/" + name, "refs/tags/" + name, "refs/heads/" + name, "refs/remotes/" + name,
            "refs/remotes/" + name + "/HEAD"]


def _protected_ref(root, protected):
    """The full ref name --protected names, or "" when it names no ref (a commit id or an expression).
    Refused when more than one ref matches the name under git's short-name rules (a tag main beside the
    branch main, a local branch origin/main beside the remote-tracking one): git would silently pick
    one, which may not be the protected line."""
    # fold() refuses a protected name that starts with '-', so it cannot be read as an option here.
    listed = _git(root, "for-each-ref", "--format=%(refname)", *_dwim_candidates(protected))
    if listed.returncode != 0:
        raise CannotEvaluate("git for-each-ref failed ({})".format(_diag(listed)))
    present = set(os.fsdecode(line) for line in listed.stdout.split(b"\n") if line)
    matches = [name for name in _dwim_candidates(protected) if name in present]
    if len(matches) > 1:
        raise Refused("--protected {!r} is ambiguous: it matches {}; pass the full ref name of the "
                      "protected line (for example {})".format(protected, ", ".join(matches), matches[-1]))
    probe = _git(root, "rev-parse", "--symbolic-full-name", protected)
    full = os.fsdecode(probe.stdout.strip()) if probe.returncode == 0 else ""
    if matches and not full:
        # git names no ref for a name that matches one (a symbolic remote HEAD resolves to its target,
        # so only an empty answer is a disagreement).
        raise CannotEvaluate("--protected {!r} matches {} but git names no ref for it ({})".format(
            protected, matches[0], _diag(probe)))
    return full


def _local_copies(root, protected_ref):
    """The local branch names that are a copy of the protected line: the protected ref itself when it is
    a local branch; for a remote-tracking ref refs/remotes/<remote>/<name>, refs/heads/<name> for every
    way of splitting a remote name off the front (a remote name may hold a slash); a remote's symbolic
    HEAD is followed to the ref it names first."""
    if protected_ref.startswith("refs/remotes/") and protected_ref.endswith("/HEAD"):
        probe = _git(root, "symbolic-ref", "-q", protected_ref)
        if probe.returncode == 0 and probe.stdout.strip():
            protected_ref = os.fsdecode(probe.stdout.strip())
    if protected_ref.startswith("refs/heads/"):
        return {protected_ref}
    if not protected_ref.startswith("refs/remotes/"):
        return set()
    parts = protected_ref[len("refs/remotes/"):].split("/")
    return {"refs/heads/" + "/".join(parts[index:]) for index in range(1, len(parts))}


def _signing(root):
    """True when the repository's configuration asks for signed commits (commit.gpgSign). git
    commit-tree, being plumbing, does not read that setting itself, so the helper passes -S for it."""
    probe = _git(root, "config", "--type=bool", "--get", "commit.gpgsign")
    if probe.returncode == 1 and not probe.stdout.strip():
        return False
    value = probe.stdout.strip()
    if probe.returncode != 0 or value not in (b"true", b"false"):
        raise CannotEvaluate("commit.gpgsign cannot be read ({})".format(_diag(probe)))
    return value == b"true"


def _in_progress(root):
    """The names of the operation-in-progress markers present in the repository."""
    found = []
    for name in IN_PROGRESS:
        probe = _git(root, "rev-parse", "--git-path", name)
        if probe.returncode != 0 or not probe.stdout.strip():
            raise CannotEvaluate("git rev-parse --git-path {} failed ({})".format(name, _diag(probe)))
        path = Path(os.fsdecode(probe.stdout.strip()))
        if not path.is_absolute():
            path = Path(root) / path
        if os.path.lexists(path):
            found.append(name)
    return found


def _dirty(root):
    """True when the index or the worktree differs from HEAD at a tracked path."""
    probe = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no",
                 "--ignore-submodules=none")
    if probe.returncode != 0:
        raise CannotEvaluate("git status failed ({})".format(_diag(probe)))
    return bool(probe.stdout)


def _is_shallow(root):
    probe = _git(root, "rev-parse", "--is-shallow-repository")
    value = probe.stdout.strip()
    if probe.returncode != 0 or value not in (b"true", b"false"):
        raise CannotEvaluate("cannot determine whether the repository is shallow ({})".format(_diag(probe)))
    return value == b"true"


def _merge_bases(root, head_sha, protected_sha):
    probe = _git(root, "merge-base", "--all", head_sha, protected_sha)
    if probe.returncode == 1:
        raise Refused("the branch and the protected ref have no merge base (an orphaned branch); "
                      "a fold has no base to keep")
    if probe.returncode != 0:
        raise CannotEvaluate("git merge-base exited {} ({})".format(probe.returncode, _diag(probe)))
    bases = [_oid(line) for line in probe.stdout.split(b"\n") if line]
    if not bases or None in bases:
        raise CannotEvaluate("git merge-base returned a malformed base list")
    return bases


def _single_base(bases):
    """The one merge base; several (a criss-cross history) are refused, never picked from."""
    if len(bases) != 1:
        raise Refused("the branch and the protected ref have {} merge bases ({}); a fold onto any one "
                      "of them is ambiguous".format(len(bases), ", ".join(bases)))
    return bases[0]


def _check_expect(expect_base, base):
    """--expect-base only narrows: it must equal the derived base."""
    if expect_base is not None and expect_base != base:
        raise Refused("--expect-base {} differs from the derived merge base {}; nothing was changed"
                      .format(expect_base, base))


def _count(root, *args):
    probe = _git(root, "rev-list", "--count", *args)
    value = probe.stdout.strip()
    if probe.returncode != 0 or not value.isdigit():
        raise CannotEvaluate("git rev-list --count failed ({})".format(_diag(probe)))
    return int(value)


def _merge_count(root, base, head_sha):
    return _count(root, "--min-parents=2", "{}..{}".format(base, head_sha))


def _fold_parent(base, _protected_sha):
    """The folded commit's one parent: the merge base, never the protected tip."""
    return base


def _fold_tree(root, head_sha):
    """The tree of the branch tip, read from the commit, never from the index."""
    return _resolve_tree(root, head_sha)


def _resolve_tree(root, commit):
    probe = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", "{}^{{tree}}".format(commit))
    sha = _oid(probe.stdout) if probe.returncode == 0 else None
    if sha is None:
        raise CannotEvaluate("the tree of {} cannot be read".format(commit))
    return sha


def _read_message(path):
    """The commit message bytes from an absolute regular file: nonempty, UTF-8, no NUL, bounded."""
    if not os.path.isabs(path):
        raise CannotEvaluate("--message-file must be an absolute path, got {!r}".format(path))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise CannotEvaluate("--message-file {!r} cannot be opened ({})".format(path, exc))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CannotEvaluate("--message-file {!r} is not a regular file".format(path))
        with os.fdopen(os.dup(fd), "rb") as handle:
            data = handle.read(MESSAGE_MAX_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("--message-file {!r} cannot be read ({})".format(path, exc))
    finally:
        os.close(fd)
    if len(data) > MESSAGE_MAX_BYTES:
        raise CannotEvaluate("--message-file is over {} bytes".format(MESSAGE_MAX_BYTES))
    if b"\0" in data:
        raise CannotEvaluate("--message-file carries a NUL byte")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("--message-file is not UTF-8")
    if not data.strip():
        raise CannotEvaluate("--message-file is empty")
    return data if data.endswith(b"\n") else data + b"\n"


def _commit_tree(root, tree, parent, message):
    """N = commit-tree tree -p parent. The repository's signing configuration applies (_signing adds
    -S, which signs with user.signingKey or the committer identity), and a configured signature that
    fails makes commit-tree fail, which refuses the fold."""
    sign = ("-S",) if _signing(root) else ()
    probe = _git(root, "commit-tree", *sign, tree, "-p", parent, data=message)
    sha = _oid(probe.stdout) if probe.returncode == 0 else None
    if sha is None:
        raise CannotEvaluate("git commit-tree failed ({}); a commit signature the repository's "
                             "configuration requires and that fails refuses the fold".format(_diag(probe)))
    return sha


def _verify(root, new_sha, tree, base):
    """Read N's raw header back: exactly one parent, equal to base, and the tree of the tip."""
    probe = _git(root, "cat-file", "commit", new_sha)
    if probe.returncode != 0:
        raise CannotEvaluate("git cat-file commit {} failed ({})".format(new_sha, _diag(probe)))
    header = probe.stdout.split(b"\n\n", 1)[0].split(b"\n")
    trees = [line[5:] for line in header if line.startswith(b"tree ")]
    parents = [line[7:] for line in header if line.startswith(b"parent ")]
    if [_oid(item) for item in trees] != [tree]:
        raise CannotEvaluate("the folded commit {} does not record the tip's tree {}".format(new_sha, tree))
    if [_oid(item) for item in parents] != [base]:
        raise CannotEvaluate("the folded commit {} does not have exactly the one parent {}".format(
            new_sha, base))


def _stamp():
    """The UTC time stamp that names a recovery ref (a function so a vector can fix it)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _recovery(root, branch_ref, head_sha):
    """Create refs/aiqt-fold/<branch>/<utc> -> head_sha, create-only (the zero old value)."""
    name = "{}{}/{}".format(RECOVERY_PREFIX, branch_ref[len("refs/heads/"):], _stamp())
    if _git(root, "check-ref-format", name).returncode != 0:
        raise CannotEvaluate("the recovery ref name {!r} is not a valid ref name".format(name))
    zero = "0" * len(head_sha)
    probe = _git(root, "update-ref", "--create-reflog", "-m", "fold-onto-base: recovery of {}".format(
        branch_ref), name, head_sha, zero)
    if probe.returncode != 0:
        raise CannotEvaluate("the recovery ref {} could not be created ({}); nothing was moved".format(
            name, _diag(probe)))
    return name


def _swap(root, branch_ref, new_sha, old_sha, reason):
    """Compare-and-swap: move branch_ref to new_sha only while it still holds old_sha."""
    return _git(root, "update-ref", "-m", reason, branch_ref, new_sha, old_sha).returncode == 0


def _check_swapped(root, branch_ref, new_sha):
    """The post-swap re-read: the branch must hold the folded commit after the update."""
    if _resolve(root, branch_ref, "branch") != new_sha:
        raise CannotEvaluate("{} does not hold the folded commit after the update".format(branch_ref))


def _undo_command(root, branch_ref, head_sha, new_sha):
    """The undo command line for a POSIX shell: every argument quoted, the repository bound with -C."""
    return " ".join(shlex.quote(item) for item in (
        "git", "-C", os.fspath(root), "update-ref", branch_ref, head_sha, new_sha))


def fold(root, protected, message_file, expect_base=None):
    """Fold the checked-out branch onto its merge base with protected. Returns a dict of the result;
    raises Refused or CannotEvaluate."""
    if expect_base is not None and _oid(expect_base.encode("utf-8", "replace")) != expect_base:
        raise CannotEvaluate("--expect-base must be a full lowercase hex commit id, got {!r}".format(expect_base))
    if not protected or protected.startswith("-"):
        raise CannotEvaluate("--protected must name a ref, got {!r}".format(protected))
    message = _read_message(message_file)
    root = _repo_root(root)
    branch_ref = _branch(root)
    if branch_ref in PROTECTED_BRANCHES:
        raise Refused("HEAD is on {}, a protected branch; fold only a work branch".format(branch_ref))
    protected_ref = _protected_ref(root, protected)
    if protected_ref == branch_ref:
        raise Refused("the checked-out branch {} is the protected ref itself".format(branch_ref))
    if branch_ref in _local_copies(root, protected_ref):
        raise Refused("the checked-out branch {} is the local copy of the protected line {}; fold only a "
                      "work branch".format(branch_ref, protected_ref))
    busy = _in_progress(root)
    if busy:
        raise Refused("an operation is in progress ({}); finish or abort it first".format(", ".join(busy)))
    if _dirty(root):
        raise Refused("the index or worktree has tracked changes; commit or stash them first")
    if _is_shallow(root):
        raise CannotEvaluate("the repository is shallow, so the merge base may be missing history; "
                             "fetch the full history first")
    head_sha = _resolve(root, branch_ref, "branch")
    protected_sha = _resolve(root, protected_ref or protected, "protected ref")
    base = _single_base(_merge_bases(root, head_sha, protected_sha))
    if base == head_sha:
        raise Refused("the branch has no commits of its own over the merge base {}; nothing to fold".format(base))
    _check_expect(expect_base, base)
    if _merge_count(root, base, head_sha):
        raise Refused("{}..{} holds a merge commit; folding across a merge is refused".format(base, head_sha))
    folded = _count(root, "{}..{}".format(base, head_sha))
    tree = _fold_tree(root, head_sha)
    if tree != _resolve_tree(root, head_sha):
        raise CannotEvaluate("internal: the fold tree is not the tip's tree")
    parent = _fold_parent(base, protected_sha)
    new_sha = _commit_tree(root, tree, parent, message)
    _verify(root, new_sha, _resolve_tree(root, head_sha), base)
    recovery = _recovery(root, branch_ref, head_sha)
    if _BEFORE_SWAP is not None:
        _BEFORE_SWAP(root, branch_ref)
    reason = "fold-onto-base: fold {} commit(s) of {}..{} onto merge base {}".format(
        folded, base, head_sha, base)
    if not _swap(root, branch_ref, new_sha, head_sha, reason):
        raise Refused("{} moved after it was read (the compare-and-swap from {} lost); the branch was "
                      "not changed by this helper; the recovery ref {} keeps {}".format(
                          branch_ref, head_sha, recovery, head_sha))
    _check_swapped(root, branch_ref, new_sha)
    return {"root": os.fspath(root), "branch": branch_ref, "head": head_sha, "protected": protected_sha,
            "base": base, "new": new_sha, "recovery": recovery, "folded": folded}


def run(root, protected, message_file, expect_base=None):
    try:
        result = fold(root, protected, message_file, expect_base)
    except Refused as exc:
        print("REFUSED: fold-onto-base: {}".format(exc), file=sys.stderr)
        return 1
    except CannotEvaluate as exc:
        print("ERROR: fold-onto-base cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    print("fold-onto-base: folded {} commit(s) of {}".format(result["folded"], result["branch"]))
    print("  H (old tip)       {}".format(result["head"]))
    print("  M (protected tip) {}".format(result["protected"]))
    print("  B (merge base)    {}".format(result["base"]))
    print("  N (folded)        {}".format(result["new"]))
    print("  recovery ref      {} -> {}".format(result["recovery"], result["head"]))
    print("Nothing was pushed. To undo: {}".format(
        _undo_command(result["root"], result["branch"], result["head"], result["new"])))
    return 0


# ---------- self-test ----------
# The registered execution-suite identity (tools/selftest_checks.toml). check() records every id it
# reaches into the current sink; the mutant runs use a private sink, so only the top-level run is
# reported to the execution gate.
SUITE_ID = "fold-onto-base-selftest"
EXECUTED = []
FAILURES = []
_SINK = [EXECUTED, FAILURES]


def check(name, ok, detail=""):
    executed, failures = _SINK
    if name in executed:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    executed.append(name)
    if not ok:
        failures.append("{}: {!r}".format(name, detail))


def _fixture_git(root, *args, data=None, text=True):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                          GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    cmd = ["git", "-C", os.fspath(root), "-c", "user.name=Fold Self Test",
           "-c", "user.email=fold@example.invalid", "-c", "commit.gpgsign=false", *args]
    out = subprocess.run(cmd, input=data, capture_output=True, check=True, timeout=TIMEOUT, env=env).stdout
    return out.decode("utf-8", "surrogateescape").strip() if text else out


def _repo(temp, name):
    """A repository on branch main with one seed commit and a local identity, signing off."""
    root = Path(temp) / name
    root.mkdir()
    _fixture_git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Fold Self Test"), ("user.email", "fold@example.invalid"),
                       ("commit.gpgsign", "false")):
        _fixture_git(root, "config", key, value)
    _write(root, "seed.txt", "seed\n")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "seed")
    return root


def _write(root, name, content):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def _commit(root, name, content, message=None):
    _write(root, name, content)
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", message or "change {}".format(name))
    return _fixture_git(root, "rev-parse", "HEAD")


def _rev(root, rev):
    return _fixture_git(root, "rev-parse", "--verify", "-q", rev)


def _feature(temp, name):
    """main at B (seed plus one), feature with two commits over B, checked out. Returns (root, B, H)."""
    root = _repo(temp, name)
    base = _commit(root, "main1.txt", "main one\n")
    _fixture_git(root, "switch", "-q", "-c", "feature")
    _commit(root, "f1.txt", "feature one\n")
    head = _commit(root, "f2.txt", "feature two\n")
    return root, base, head


def _message(temp, name, text="Fold the feature\n\nOne commit for the feature's changes.\n"):
    path = Path(temp) / "{}.msg".format(name)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _fold_cli(root, temp, name, protected="main", expect=None):
    argv = ["--root", str(root), "--protected", protected, "--message-file", _message(temp, name)]
    if expect is not None:
        argv += ["--expect-base", expect]
    return _cli(argv)


def _parents(root, rev):
    raw = _fixture_git(root, "cat-file", "commit", rev)
    return [line[7:] for line in raw.split("\n\n", 1)[0].splitlines() if line.startswith("parent ")]


def _h1(temp):
    root, base, head = _feature(temp, "h1")
    msg = _message(temp, "h1-own", "Feature fold\n")
    code, out, err = _cli(["--root", str(root), "--protected", "main", "--message-file", msg])
    new = _rev(root, "refs/heads/feature")
    recovery = _fixture_git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/aiqt-fold/")
    check("fold/h1-valid-fold", code == 0 and new != head and _parents(root, new) == [base]
          and _rev(root, new + "^{tree}") == _rev(root, head + "^{tree}")
          and _fixture_git(root, "symbolic-ref", "HEAD") == "refs/heads/feature"
          and _fixture_git(root, "status", "--porcelain") == ""
          and _fixture_git(root, "log", "-1", "--format=%B", new) == "Feature fold",
          (code, out, err))
    names = recovery.split()
    check("fold/h1-recovery-ref-create-only", len(names) == 2 and names[1] == head
          and names[0].startswith("refs/aiqt-fold/feature/") and names[0].endswith("Z"), recovery)


def _h2(temp):
    """Main advances in a sibling worktree (as a fetch from another worktree moves the ref) after the
    branch was cut: the fold's parent must be the merge base B, never the moved tip M."""
    root, base, head = _feature(temp, "h2")
    sibling = Path(temp) / "h2-sibling"
    _fixture_git(root, "worktree", "add", "-q", str(sibling), "main")
    _commit(sibling, "main1.txt", "main one, revised\n")
    moved = _commit(sibling, "main2.txt", "main two\n")
    code, out, err = _fold_cli(root, temp, "h2")
    new = _rev(root, "refs/heads/feature")
    check("fold/h2-protected-moved-parent-is-base", code == 0 and moved == _rev(root, "main")
          and _parents(root, new) == [base], (code, out, err, _parents(root, new), base, moved))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_branch_root
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        good = check_branch_root.evaluate(str(root), "refs/heads/main", new)
    check("fold/h14-fold-passes-branch-root", good.code == 0 and good.verdict == "PASS", good)
    tree = _rev(root, head + "^{tree}")
    wrong = _fixture_git(root, "commit-tree", tree, "-p", moved, data=b"wrong fold\n")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bad = check_branch_root.evaluate(str(root), "refs/heads/main", wrong)
    check("fold/h14-tip-fold-is-reverts", bad.code == 1 and bad.verdict == "REVERTS", bad)


def _h3(temp):
    root, base, head = _feature(temp, "h3")
    older = _rev(root, base + "^")
    code, _out, err = _fold_cli(root, temp, "h3-old", expect=older)
    check("fold/h3-expect-base-older-refused", code == 1 and "--expect-base" in err
          and _rev(root, "refs/heads/feature") == head, (code, err))
    code, _out, err = _fold_cli(root, temp, "h3-short", expect=base[:12])
    check("fold/h3-expect-base-abbreviated-usage", code == 2 and _rev(root, "refs/heads/feature") == head,
          (code, err))
    code, _out, err = _fold_cli(root, temp, "h3-match", expect=base)
    check("fold/h3-expect-base-equal-folds", code == 0
          and _parents(root, _rev(root, "refs/heads/feature")) == [base], (code, err))


def _h4(temp):
    root, _base, head = _feature(temp, "h4")
    _write(root, "f1.txt", "edited but not staged\n")
    code, _out, err = _fold_cli(root, temp, "h4-dirty")
    check("fold/h4-dirty-worktree-refused", code == 1 and "tracked changes" in err
          and _rev(root, "refs/heads/feature") == head, (code, err))
    _fixture_git(root, "checkout", "-q", "--", "f1.txt")
    _write(root, "unrelated.txt", "staged, unrelated\n")
    _fixture_git(root, "add", "unrelated.txt")
    code, _out, err = _fold_cli(root, temp, "h4-staged")
    check("fold/h4-staged-unrelated-refused", code == 1 and _rev(root, "refs/heads/feature") == head,
          (code, err))
    # The tree is read from the tip commit, never from the index: with the staged file still present,
    # the fold tree is the tip's tree.
    check("fold/h4-tree-from-commit-not-index",
          _fold_tree(root, head) == _rev(root, head + "^{tree}"), _fold_tree(root, head))


def _h5(temp):
    root = _repo(temp, "h5")
    seed = _rev(root, "HEAD")
    tree = _rev(root, "HEAD^{tree}")
    a1 = _fixture_git(root, "commit-tree", tree, "-p", seed, data=b"a1\n")
    b1 = _fixture_git(root, "commit-tree", tree, "-p", seed, data=b"b1\n")
    m1 = _fixture_git(root, "commit-tree", tree, "-p", a1, "-p", b1, data=b"merge one\n")
    m2 = _fixture_git(root, "commit-tree", tree, "-p", b1, "-p", a1, data=b"merge two\n")
    _fixture_git(root, "update-ref", "refs/heads/main", m1)
    _fixture_git(root, "update-ref", "refs/heads/feature", m2)
    _fixture_git(root, "switch", "-q", "feature")
    code, _out, err = _fold_cli(root, temp, "h5")
    check("fold/h5-criss-cross-refused", code == 1 and "2 merge bases" in err
          and _rev(root, "refs/heads/feature") == m2, (code, err))


def _h6(temp):
    src, _base, _head = _feature(temp, "h6-src")
    clone = Path(temp) / "h6-shallow"
    _fixture_git(temp, "clone", "-q", "--depth", "1", "-b", "feature", "file://{}".format(src), str(clone))
    for key, value in (("user.name", "Fold Self Test"), ("user.email", "fold@example.invalid")):
        _fixture_git(clone, "config", key, value)
    code, _out, err = _fold_cli(clone, temp, "h6", protected="origin/main")
    check("fold/h6-shallow-cannot-evaluate", code == 2 and "shallow" in err, (code, err))


def _h7(temp):
    global _BEFORE_SWAP
    root, _base, head = _feature(temp, "h7")
    moved = []

    def race(fold_root, branch_ref):
        tree = _rev(fold_root, head + "^{tree}")
        other = _fixture_git(fold_root, "commit-tree", tree, "-p", head, data=b"concurrent writer\n")
        _fixture_git(fold_root, "update-ref", branch_ref, other)
        moved.append(other)

    _BEFORE_SWAP = race
    try:
        code, _out, err = _fold_cli(root, temp, "h7")
    finally:
        _BEFORE_SWAP = None
    held = _rev(root, "refs/heads/feature")
    recovery = _fixture_git(root, "for-each-ref", "--format=%(objectname)", "refs/aiqt-fold/")
    check("fold/h7-branch-moved-swap-lost", code == 1 and moved and held == moved[0]
          and recovery == head and "compare-and-swap" in err, (code, err, held, moved))


def _h8(temp):
    root = _repo(temp, "h8")
    _commit(root, "x.txt", "x\n")
    for name, branch in (("fold/h8-on-main-refused", "main"), ("fold/h8-on-master-refused", "master")):
        _fixture_git(root, "switch", "-q", "-C", branch)
        code, _out, err = _fold_cli(root, temp, "h8-" + branch, protected="main")
        check(name, code == 1 and "protected branch" in err, (code, err))
    _fixture_git(root, "switch", "-q", "-c", "line")
    code, _out, err = _fold_cli(root, temp, "h8-self", protected="refs/heads/line")
    check("fold/h8-protected-is-branch-refused", code == 1 and "protected ref itself" in err, (code, err))


def _h9(temp):
    root, _base, head = _feature(temp, "h9")
    _fixture_git(root, "switch", "-q", "--detach", head)
    code, _out, err = _fold_cli(root, temp, "h9")
    check("fold/h9-detached-refused", code == 1 and "detached" in err, (code, err))


def _h10(temp):
    root, base, _head = _feature(temp, "h10")
    _fixture_git(root, "switch", "-q", "-c", "side", base)
    _commit(root, "side.txt", "side\n")
    _fixture_git(root, "switch", "-q", "feature")
    _fixture_git(root, "merge", "-q", "--no-ff", "--no-edit", "side")
    merged = _rev(root, "HEAD")
    code, _out, err = _fold_cli(root, temp, "h10")
    check("fold/h10-merge-in-range-refused", code == 1 and "merge commit" in err
          and _rev(root, "refs/heads/feature") == merged, (code, err))


def _h11(temp):
    root, _base, head = _feature(temp, "h11")
    git_dir = Path(_fixture_git(root, "rev-parse", "--absolute-git-dir"))
    for name, marker, directory in (("fold/h11-rebase-merge-refused", "rebase-merge", True),
                                    ("fold/h11-rebase-apply-refused", "rebase-apply", True),
                                    ("fold/h11-merge-head-refused", "MERGE_HEAD", False),
                                    ("fold/h11-cherry-pick-refused", "CHERRY_PICK_HEAD", False),
                                    ("fold/h11-revert-refused", "REVERT_HEAD", False),
                                    ("fold/h11-bisect-refused", "BISECT_LOG", False),
                                    ("fold/h11-sequencer-refused", "sequencer", True)):
        path = git_dir / marker
        if directory:
            path.mkdir()
        else:
            path.write_text(head + "\n", encoding="ascii")
        try:
            code, _out, err = _fold_cli(root, temp, "h11-" + marker)
        finally:
            if directory:
                shutil.rmtree(path)
            else:
                path.unlink()
        check(name, code == 1 and marker in err and _rev(root, "refs/heads/feature") == head, (code, err))


def _h12(temp):
    root = _repo(temp, "h12")
    _commit(root, "x.txt", "x\n")
    _fixture_git(root, "switch", "-q", "-c", "feature")
    code, _out, err = _fold_cli(root, temp, "h12")
    check("fold/h12-nothing-to-fold-refused", code == 1 and "nothing to fold" in err, (code, err))


def _h13(temp):
    root, base, _head = _feature(temp, "h13")
    _write(root, "with space.txt", "space\n")
    _write(root, "new\nline.txt", "newline\n")
    _write(root, "bin.dat", b"\x00\xff\x00binary\n")
    _fixture_git(root, "rm", "-q", "seed.txt")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "awkward paths")
    _fixture_git(root, "mv", "f1.txt", "renamed f1.txt")
    _fixture_git(root, "commit", "-q", "-m", "rename")
    head = _rev(root, "HEAD")
    code, _out, err = _fold_cli(root, temp, "h13")
    new = _rev(root, "refs/heads/feature")
    check("fold/h13-awkward-paths-tree-kept", code == 0 and _parents(root, new) == [base]
          and _rev(root, new + "^{tree}") == _rev(root, head + "^{tree}")
          and _fixture_git(root, "status", "--porcelain") == "", (code, err))


def _h15(temp):
    """Every PreToolUse hook that matches Bash allows the helper's documented command line."""
    script = Path(__file__).resolve().parent.parent / ".aiqt" / "core" / "hooks" / "scripts" / "aiqt_hooks.py"
    manifest = script.parent.parent / "manifest.toml"
    handlers, block, verdicts = [], {}, {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines() + ["[[hook]]"]
    except OSError as exc:
        # NOT RUN is a failure: no handler is listed, so the check below fails and names the cause.
        lines = []
        verdicts["manifest"] = "NOT RUN: hook manifest unreadable: {}".format(exc)
    for line in lines:
        if line.startswith("[["):
            if (block.get("event") == "PreToolUse" and "Bash" in block.get("matcher", "").split("|")
                    and block.get("handler")):
                handlers.append(block["handler"])
            block = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep and key in ("event", "matcher", "handler") and value.startswith('"'):
            block[key] = value.split('"')[1]
    root, base, _head = _feature(temp, "h15")
    command = ("python3 -I -B tools/fold_onto_base.py --protected origin/main --message-file {} "
               "--expect-base {}".format(_message(temp, "h15"), base))
    keywords = [word for word in ("git", "reset", "branch") if word in command.split("/")[-1].split()[0]]
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["CLAUDE_PROJECT_DIR"] = str(root)
    for handler in handlers:
        payload = json.dumps({"session_id": "fold-self-test", "transcript_path": "",
                              "cwd": str(root), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                              "tool_input": {"command": command}})
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", str(script), handler], input=payload,
                                  capture_output=True, text=True, timeout=60, cwd=str(root), env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            verdicts[handler] = "not run: {}".format(exc)
            continue
        decided = proc.stdout
        if proc.returncode != 0 or '"deny"' in decided or '"ask"' in decided:
            verdicts[handler] = (proc.returncode, decided[:300], proc.stderr[:300])
    check("fold/h15-hooks-allow-command", len(handlers) >= 5 and "git_discard" in handlers
          and not verdicts and not keywords, (len(handlers), verdicts, keywords))


def _h16(temp):
    """An ambient GIT_DIR / GIT_INDEX_FILE naming another repository is scrubbed: the fold acts on the
    repository bound with -C, and the other repository is untouched."""
    root, base, _head = _feature(temp, "h16")
    other = _repo(temp, "h16-other")
    other_head = _rev(other, "HEAD")
    saved = {key: os.environ.get(key) for key in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")}
    os.environ["GIT_DIR"] = str(other / ".git")
    os.environ["GIT_INDEX_FILE"] = str(other / ".git" / "index")
    os.environ["GIT_WORK_TREE"] = str(other)
    try:
        code, _out, err = _fold_cli(root, temp, "h16")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    check("fold/h16-ambient-git-env-scrubbed", code == 0
          and _parents(root, _rev(root, "refs/heads/feature")) == [base]
          and _rev(other, "HEAD") == other_head
          and _fixture_git(other, "for-each-ref", "refs/aiqt-fold/") == "", (code, err))


def _q10(temp):
    root, _base, head = _feature(temp, "q10")
    _fixture_git(root, "config", "commit.gpgsign", "true")
    _fixture_git(root, "config", "gpg.program", str(Path(temp) / "no-such-signer"))
    code, _out, err = _fold_cli(root, temp, "q10")
    check("fold/q10-failed-signature-refused", code == 2 and _rev(root, "refs/heads/feature") == head
          and _fixture_git(root, "for-each-ref", "refs/aiqt-fold/") == "", (code, err))


@contextlib.contextmanager
def _patched(**replacements):
    """Module globals replaced for the body of a with block (a vector's seam), then restored."""
    saved = {name: globals()[name] for name in replacements}
    globals().update(replacements)
    try:
        yield
    finally:
        globals().update(saved)


def _fixture_env():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    return git_fixture_env(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                           GIT_NO_REPLACE_OBJECTS="1")


def _moved_main(temp, name):
    """main at B, feature (two commits over B) checked out, then main moves on: a commit that changes
    shared.txt and one more, M2. Returns (root, B, the feature's first commit, H, M2)."""
    root, base, head = _feature(temp, name)
    first = _rev(root, head + "^")
    _fixture_git(root, "switch", "-q", "main")
    _commit(root, "shared.txt", "main's change\n")
    moved = _commit(root, "main2.txt", "main two\n")
    _fixture_git(root, "switch", "-q", "feature")
    return root, base, first, head, moved


def _h17(temp):
    """F1: an info/grafts file that makes the feature's first commit a child of the moved tip M2 must not
    change the merge base; the fold's parent stays B. The twin, the same graph without the graft file,
    folds onto B too."""
    root, base, _first, _head, _moved = _moved_main(temp, "h17-twin")
    code, _out, err = _fold_cli(root, temp, "h17-twin")
    check("fold/h17-twin-no-graft-folds-onto-base", code == 0
          and _parents(root, _rev(root, "refs/heads/feature")) == [base], (code, err))
    root, base, first, head, moved = _moved_main(temp, "h17")
    git_dir = Path(_fixture_git(root, "rev-parse", "--absolute-git-dir"))
    (git_dir / "info").mkdir(exist_ok=True)
    (git_dir / "info" / "grafts").write_text("{} {}\n".format(first, moved), encoding="ascii")
    code, _out, err = _fold_cli(root, temp, "h17")
    new = _rev(root, "refs/heads/feature")
    check("fold/h17-graft-file-ignored", code == 0 and _parents(root, new) == [base]
          and _rev(root, new + "^{tree}") == _rev(root, head + "^{tree}"),
          (code, err, _parents(root, new), base, moved))


def _undo_run(root, temp, name, cwd):
    """Fold, then run the printed undo command through /bin/sh from cwd. Returns (fold code, undo code,
    the branch's value after the undo, the old tip, the undo command)."""
    head = _rev(root, "HEAD")
    code, out, err = _fold_cli(root, temp, name)
    lines = [line for line in out.splitlines() if "To undo: " in line]
    if code != 0 or len(lines) != 1:
        return code, None, None, head, (out, err)
    command = lines[0].split("To undo: ", 1)[1]
    proc = subprocess.run(["/bin/sh", "-c", command], capture_output=True, timeout=TIMEOUT, cwd=str(cwd),
                          env=_fixture_env())
    branch = _fixture_git(root, "symbolic-ref", "HEAD")
    return code, proc.returncode, _rev(root, branch), head, command


def _h18(temp):
    """The printed undo command restores the old tip when run through /bin/sh from outside
    the repository (cwd another repository), with a branch name holding shell punctuation; the other
    repository is untouched. The twin: a plain branch name, run from inside the repository."""
    root, _base, _head = _feature(temp, "h18-twin")
    code, undo, after, head, command = _undo_run(root, temp, "h18-twin", root)
    check("fold/h18-undo-plain-branch-inside-repo", code == 0 and undo == 0 and after == head,
          (code, undo, command))
    root = _repo(temp, "h18")
    _commit(root, "main1.txt", "main one\n")
    _fixture_git(root, "switch", "-q", "-c", "fold;false'$(q)'&x")
    _commit(root, "f1.txt", "feature one\n")
    other = _repo(temp, "h18-other")
    other_refs = _fixture_git(other, "for-each-ref", "--format=%(refname) %(objectname)")
    code, undo, after, head, command = _undo_run(root, temp, "h18", other)
    check("fold/h18-undo-shell-punctuation-outside-repo", code == 0 and undo == 0 and after == head
          and _fixture_git(other, "for-each-ref", "--format=%(refname) %(objectname)") == other_refs,
          (code, undo, command))


def _script(path, body):
    """An executable /bin/sh script."""
    Path(path).write_text("#!/bin/sh\n" + body, encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


def _h19(temp):
    """F2: a repository-local core.fsmonitor program and a core.hooksPath reference-transaction hook never
    run, and a decoy git put first on PATH after the helper loaded never runs; the fold itself succeeds.
    The twins show each hostile setting is live for git outside the helper's pins."""
    root, base, _head = _feature(temp, "h19")
    marks = Path(temp) / "h19-marks"
    marks.mkdir()
    hooks = Path(temp) / "h19-hooks"
    hooks.mkdir()
    decoy = Path(temp) / "h19-decoy"
    decoy.mkdir()
    real = shutil.which("git") or "git"
    _fixture_git(root, "config", "core.fsmonitor", _script(Path(temp) / "h19-fsmonitor",
                                                           "echo ran >> '{}'\n".format(marks / "fsmonitor")))
    _script(hooks / "reference-transaction", "echo ran >> '{}'\n".format(marks / "hook"))
    _fixture_git(root, "config", "core.hooksPath", str(hooks))
    _script(decoy / "git", "echo ran >> '{}'\nexec '{}' \"$@\"\n".format(marks / "decoy", real))
    saved = os.environ.get("PATH", "")
    os.environ["PATH"] = str(decoy) + os.pathsep + saved
    try:
        code, _out, err = _fold_cli(root, temp, "h19")
    finally:
        os.environ["PATH"] = saved
    folded = code == 0 and _parents(root, _rev(root, "refs/heads/feature")) == [base]
    check("fold/h19-fsmonitor-not-run", folded and not (marks / "fsmonitor").exists(), (code, err))
    check("fold/h19-hooks-not-run", folded and not (marks / "hook").exists(), (code, err))
    check("fold/h19-path-decoy-not-run", folded and not (marks / "decoy").exists(), (code, err))
    env = _git_env()
    subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, timeout=TIMEOUT,
                   env=env)
    subprocess.run(["git", "-C", str(root), "update-ref", "refs/h19/twin", base], capture_output=True,
                   timeout=TIMEOUT, env=env)
    check("fold/h19-twin-hostile-settings-live", (marks / "fsmonitor").exists() and (marks / "hook").exists()
          and shutil.which("git", path=str(decoy) + os.pathsep + saved) == str(decoy / "git"),
          sorted(os.listdir(marks)))


def _h20(temp):
    """F3: the last checks fire. A folded commit with the protected tip as parent, or with another tree,
    is cannot-evaluate in _verify before any ref moves; an update that reports success without moving the
    branch is caught by the post-swap re-read; and a recovery ref name that already exists is never
    overwritten (create-only). The twin: _verify accepts a correct folded commit."""
    root, base, _first, head, moved = _moved_main(temp, "h20")
    tree = _rev(root, head + "^{tree}")
    good = _fixture_git(root, "commit-tree", tree, "-p", base, data=b"good\n")
    try:
        _verify(root, good, tree, base)
        accepted = True
    except CannotEvaluate as exc:
        accepted = exc
    check("fold/h20-verify-accepts-good-commit", accepted is True, accepted)

    def tip_commit(fold_root, fold_tree, _parent, message):
        return _fixture_git(fold_root, "commit-tree", fold_tree, "-p", moved, data=message)

    def base_tree_commit(fold_root, _tree, parent, message):
        return _fixture_git(fold_root, "commit-tree", _rev(fold_root, base + "^{tree}"), "-p", parent,
                            data=message)

    for name, seam in (("fold/h20-verify-wrong-parent-cannot-evaluate", tip_commit),
                       ("fold/h20-verify-wrong-tree-cannot-evaluate", base_tree_commit)):
        with _patched(_commit_tree=seam):
            code, _out, err = _fold_cli(root, temp, name.rsplit("/", 1)[1])
        check(name, code == 2 and "folded commit" in err and _rev(root, "refs/heads/feature") == head
              and _fixture_git(root, "for-each-ref", "refs/aiqt-fold/") == "", (code, err))

    def silent_swap(_root, _branch_ref, _new_sha, _old_sha, _reason):
        return True

    with _patched(_swap=silent_swap):
        code, _out, err = _fold_cli(root, temp, "h20-reread")
    check("fold/h20-post-swap-reread-fires", code == 2 and "does not hold the folded commit" in err
          and _rev(root, "refs/heads/feature") == head, (code, err))
    stamp = "20000101T000000000000Z"
    taken = "{}feature/{}".format(RECOVERY_PREFIX, stamp)
    for line in _fixture_git(root, "for-each-ref", "--format=%(refname)", "refs/aiqt-fold/").splitlines():
        _fixture_git(root, "update-ref", "-d", line)
    _fixture_git(root, "update-ref", taken, base)
    with _patched(_stamp=lambda: stamp):
        code, _out, err = _fold_cli(root, temp, "h20-collision")
    check("fold/h20-recovery-collision-refused", code == 2 and "could not be created" in err
          and _rev(root, taken) == base and _rev(root, "refs/heads/feature") == head, (code, err))


def _h21(temp):
    """F4: in a clone whose protected line is trunk, the checked-out local trunk (two commits ahead of
    origin/trunk) is refused as the local copy of the protected line, by --protected origin/trunk and
    by --protected origin (the remote's HEAD, followed); the twin, a work branch in the same clone,
    folds."""
    src = _repo(temp, "h21-src")
    _fixture_git(src, "branch", "-q", "-m", "main", "trunk")
    _commit(src, "t.txt", "trunk\n")
    clone = Path(temp) / "h21"
    _fixture_git(temp, "clone", "-q", "file://{}".format(src), str(clone))
    for key, value in (("user.name", "Fold Self Test"), ("user.email", "fold@example.invalid"),
                       ("commit.gpgsign", "false")):
        _fixture_git(clone, "config", key, value)
    _commit(clone, "a.txt", "a\n")
    head = _commit(clone, "b.txt", "b\n")
    for name, protected in (("fold/h21-local-trunk-refused", "origin/trunk"),
                            ("fold/h21-local-trunk-via-remote-head-refused", "origin")):
        code, _out, err = _fold_cli(clone, temp, "h21-" + protected.replace("/", "-"), protected=protected)
        check(name, code == 1 and "local copy of the protected line" in err
              and _rev(clone, "refs/heads/trunk") == head, (code, err))
    base = _rev(clone, "origin/trunk")
    _fixture_git(clone, "switch", "-q", "-c", "work", base)
    _commit(clone, "w1.txt", "w1\n")
    _commit(clone, "w2.txt", "w2\n")
    code, _out, err = _fold_cli(clone, temp, "h21-work", protected="origin/trunk")
    check("fold/h21-twin-work-branch-folds", code == 0
          and _parents(clone, _rev(clone, "refs/heads/work")) == [base]
          and _rev(clone, "refs/heads/trunk") == head, (code, err))


def _h22(temp):
    """F5: an ambiguous --protected name is refused by name: a tag main beside the branch main, and a
    local branch origin/main beside refs/remotes/origin/main. The twin: the full ref name, with the tag
    still present, folds onto the merge base with the branch."""
    root, base, head = _feature(temp, "h22")
    _fixture_git(root, "tag", "main", base + "^")
    code, _out, err = _fold_cli(root, temp, "h22-tag")
    check("fold/h22-tag-and-branch-ambiguous-refused", code == 1 and "ambiguous" in err
          and "refs/tags/main" in err and _rev(root, "refs/heads/feature") == head, (code, err))
    _fixture_git(root, "update-ref", "refs/remotes/origin/main", base)
    _fixture_git(root, "update-ref", "refs/heads/origin/main", base + "^")
    code, _out, err = _fold_cli(root, temp, "h22-origin", protected="origin/main")
    check("fold/h22-local-origin-main-ambiguous-refused", code == 1 and "ambiguous" in err
          and "refs/heads/origin/main" in err and _rev(root, "refs/heads/feature") == head, (code, err))
    code, _out, err = _fold_cli(root, temp, "h22-full", protected="refs/heads/main")
    check("fold/h22-twin-full-ref-folds", code == 0
          and _parents(root, _rev(root, "refs/heads/feature")) == [base], (code, err))


def _usage(temp):
    root, _base, head = _feature(temp, "usage")
    results = [
        _cli(["--root", str(root), "--protected", "main", "--message-file", "relative.msg"])[0],
        _cli(["--root", str(root), "--protected", "main", "--message-file", str(Path(temp) / "absent")])[0],
        _cli(["--root", str(root), "--protected", "main", "--message-file", _message(temp, "blank", " \n")])[0],
        _cli(["--root", str(root), "--message-file", _message(temp, "noprot")])[0],
        _cli(["--root", str(root), "--protected", "no-such-ref", "--message-file", _message(temp, "nr")])[0],
        _cli(["--self-test", "--protected", "main"])[0],
    ]
    check("cli/usage-and-unresolvable-exit-2", results == [2, 2, 2, 2, 2, 2]
          and _rev(root, "refs/heads/feature") == head, results)
    check("cli/closed-git-subcommands", not GIT_SUBCOMMANDS & {
        "push", "fetch", "pull", "rebase", "reset", "checkout", "switch", "merge", "commit", "add",
        "write-tree", "read-tree"}, sorted(GIT_SUBCOMMANDS))
    # F7: --execution-report is accepted only as the trailing option after --self-test (the suite's
    # args); alone it is a usage error and never starts the self-test.
    alone = _cli(["--execution-report", str(Path(temp) / "report.json")])
    check("cli/execution-report-alone-usage", alone[0] == 2 and "--execution-report" in alone[2]
          and "SELF-TEST" not in alone[1] and not (Path(temp) / "report.json").exists(), alone)
    try:
        split = _split_report(["--self-test", "--execution-report", str(Path(temp) / "report.json")])
    except ValueError as exc:
        split = exc
    check("cli/execution-report-after-self-test-accepted",
          split == (str(Path(temp) / "report.json"), ["--self-test"]), split)
    code, out, _err = _cli(["--help"])
    check("cli/help-explains-options-exits-and-rewrite", code == 0 and all(word in out for word in (
        "--root DIR", "--protected REF", "--message-file ABS_PATH", "--expect-base FULL_SHA", "--self-test",
        "exit codes:", "REWRITES", "force push", "residue", "local copy", "ambiguous", "Not neutralized")),
          out)


VECTORS = (_h1, _h2, _h3, _h4, _h5, _h6, _h7, _h8, _h9, _h10, _h11, _h12, _h13, _h15, _h16, _q10, _h17,
           _h18, _h19, _h20, _h21, _h22, _usage)


def _with_home(body):
    """Run body(temp) with HOME and XDG_CONFIG_HOME pinned to an empty fixture home, then clean up."""
    temp = Path(tempfile.mkdtemp(prefix="aiqt-fold-selftest-"))
    saved = {key: os.environ.get(key) for key in ("HOME", "XDG_CONFIG_HOME")}
    home = temp / "home"
    home.mkdir()
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / "xdg")
    try:
        return body(temp)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(temp, ignore_errors=True)


def _mutated(replacements, vector):
    """Failures of one vector run with module globals replaced, in a private sink."""
    global _SINK
    saved = {name: globals()[name] for name in replacements}
    saved_sink = _SINK
    _SINK = [[], []]
    globals().update(replacements)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _with_home(vector)
    except (OSError, subprocess.SubprocessError, CannotEvaluate) as exc:
        _SINK[1].append("mutant run error: {}".format(exc))
    finally:
        globals().update(saved)
        failures = _SINK[1]
        _SINK = saved_sink
    return failures


def _mutants():
    def tip_parent(_base, protected_sha):
        return protected_sha

    def index_tree(root, _head_sha):
        probe = subprocess.run(["git", "-C", os.fspath(root), "write-tree"], capture_output=True,
                               timeout=TIMEOUT, env=_git_env())
        return probe.stdout.strip().decode("ascii")

    def blind_swap(root, branch_ref, new_sha, _old_sha, reason):
        return _git(root, "update-ref", "-m", reason, branch_ref, new_sha).returncode == 0

    def first_base(bases):
        return bases[0]

    def ignore_expect(_expect_base, _base):
        return None

    def no_recovery(_root, branch_ref, _head_sha):
        return RECOVERY_PREFIX + branch_ref[len("refs/heads/"):] + "/not-created"

    def no_merges(_root, _base, _head_sha):
        return 0

    def never_dirty(_root):
        return False

    def ambient_env():
        return dict(os.environ)

    def unsigned(_root):
        return False

    scrubbed_env = _git_env

    def graft_env():
        env = scrubbed_env()
        env.pop("GIT_GRAFT_FILE", None)
        return env

    def unquoted_undo(_root, branch_ref, head_sha, new_sha):
        return "git update-ref {} {} {}".format(branch_ref, head_sha, new_sha)

    def without(*dropped):
        items, kept = list(HARDENING), []
        while items:
            pair = items[:2] if items[0] == "-c" else items[:1]
            del items[:len(pair)]
            if pair[-1] not in dropped:
                kept.extend(pair)
        return tuple(kept)

    def no_verify(_root, _new_sha, _tree, _base):
        return None

    def verify_tree_only(root, new_sha, tree, _base):
        probe = _git(root, "cat-file", "commit", new_sha)
        header = probe.stdout.split(b"\n\n", 1)[0].split(b"\n")
        if [_oid(line[5:]) for line in header if line.startswith(b"tree ")] != [tree]:
            raise CannotEvaluate("the folded commit {} does not record the tip's tree".format(new_sha))

    def no_reread(_root, _branch_ref, _new_sha):
        return None

    def overwriting_recovery(root, branch_ref, head_sha):
        name = "{}{}/{}".format(RECOVERY_PREFIX, branch_ref[len("refs/heads/"):], _stamp())
        if _git(root, "update-ref", name, head_sha).returncode != 0:
            raise CannotEvaluate("the recovery ref {} could not be created".format(name))
        return name

    def no_local_copies(_root, _protected_ref):
        return set()

    def ambiguity_ignored(root, protected):
        # The pinned behaviour: no ambiguity check, so git's own short-name order picks silently.
        probe = _git(root, "rev-parse", "--symbolic-full-name", protected)
        return os.fsdecode(probe.stdout.strip()) if probe.returncode == 0 else ""

    for name, replacements, vector, red in (
            ("mutant/parent-is-protected-tip", {"_fold_parent": tip_parent}, _h2,
             "fold/h2-protected-moved-parent-is-base"),
            ("mutant/tree-from-index", {"_fold_tree": index_tree}, _h4, "fold/h4-tree-from-commit-not-index"),
            ("mutant/swap-without-old-value", {"_swap": blind_swap}, _h7, "fold/h7-branch-moved-swap-lost"),
            ("mutant/first-of-several-bases", {"_single_base": first_base}, _h5, "fold/h5-criss-cross-refused"),
            ("mutant/expect-base-ignored", {"_check_expect": ignore_expect}, _h3,
             "fold/h3-expect-base-older-refused"),
            ("mutant/no-recovery-ref", {"_recovery": no_recovery}, _h1, "fold/h1-recovery-ref-create-only"),
            ("mutant/merges-allowed", {"_merge_count": no_merges}, _h10, "fold/h10-merge-in-range-refused"),
            ("mutant/dirty-allowed", {"_dirty": never_dirty}, _h4, "fold/h4-dirty-worktree-refused"),
            ("mutant/ambient-git-env-kept", {"_git_env": ambient_env}, _h16, "fold/h16-ambient-git-env-scrubbed"),
            ("mutant/signing-config-ignored", {"_signing": unsigned}, _q10, "fold/q10-failed-signature-refused"),
            ("mutant/graft-file-honoured", {"_git_env": graft_env}, _h17, "fold/h17-graft-file-ignored"),
            ("mutant/undo-unquoted-unbound", {"_undo_command": unquoted_undo}, _h18,
             "fold/h18-undo-shell-punctuation-outside-repo"),
            ("mutant/fsmonitor-pin-dropped", {"HARDENING": without("core.fsmonitor=false")}, _h19,
             "fold/h19-fsmonitor-not-run"),
            ("mutant/hooks-pin-dropped", {"HARDENING": without("core.hooksPath=" + str(NO_SUCH_PATH))}, _h19,
             "fold/h19-hooks-not-run"),
            ("mutant/git-looked-up-per-call", {"_GIT": "git"}, _h19, "fold/h19-path-decoy-not-run"),
            ("mutant/verify-call-dropped", {"_verify": no_verify}, _h20,
             "fold/h20-verify-wrong-parent-cannot-evaluate"),
            ("mutant/verify-ignores-parent", {"_verify": verify_tree_only}, _h20,
             "fold/h20-verify-wrong-parent-cannot-evaluate"),
            ("mutant/post-swap-reread-dropped", {"_check_swapped": no_reread}, _h20,
             "fold/h20-post-swap-reread-fires"),
            ("mutant/recovery-ref-overwrites", {"_recovery": overwriting_recovery}, _h20,
             "fold/h20-recovery-collision-refused"),
            ("mutant/local-copy-allowed", {"_local_copies": no_local_copies}, _h21,
             "fold/h21-local-trunk-refused"),
            ("mutant/ambiguous-protected-allowed", {"_protected_ref": ambiguity_ignored}, _h22,
             "fold/h22-tag-and-branch-ambiguous-refused")):
        failures = _mutated(replacements, vector)
        check(name, any(entry.startswith(red + ":") for entry in failures),
              "the mutant left {} green ({})".format(red, failures))


def self_test():
    try:
        _with_home(lambda temp: [vector(temp) for vector in VECTORS])
    except (OSError, subprocess.SubprocessError, CannotEvaluate, ImportError) as exc:
        print("SELF-TEST ERROR: fixture setup failed: {}".format(exc), file=sys.stderr)
        return 2
    _mutants()
    if FAILURES:
        print("SELF-TEST FAIL: fold_onto_base {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: fold_onto_base {} checks (H1 to H22 and Q10 in temporary repositories: a valid "
          "fold with a create-only recovery ref; a protected tip moved from a sibling worktree folds onto the "
          "merge base and passes the branch-root gate, while a fold onto the tip is REVERTS there; "
          "--expect-base narrows only; dirty, staged, criss-cross, detached, protected-branch, merge-in-range, "
          "in-progress and nothing-to-fold states are refused; a shallow clone and a failed signature are "
          "cannot-evaluate; a lost compare-and-swap leaves the other writer's commit; awkward paths keep the "
          "tree; ambient GIT_ variables are scrubbed; every Bash PreToolUse hook allows the command; an "
          "info/grafts file cannot move the base; the printed undo command restores the tip through /bin/sh "
          "from another repository with shell punctuation in the branch name; a repository fsmonitor "
          "program, a reference-transaction hook and a later PATH decoy git never run; _verify, the post-swap "
          "re-read and the create-only recovery ref each fire; the local copy of the protected line and an "
          "ambiguous --protected name are refused; --execution-report needs --self-test; --help explains "
          "the options, exits and rewrite; each listed mutant turns its vector red)".format(len(EXECUTED)))
    return 0


HELP_DESCRIPTION = """\
Fold the checked-out branch into one commit on its merge base with the protected ref.

This REWRITES the branch's history: the branch's commits since the merge base B are replaced by one new
commit N whose tree is the old tip H's tree and whose one parent is B (never the protected line's tip,
which may have moved). The old tip stays reachable under a create-only recovery ref
refs/aiqt-fold/<branch>/<utc>. A branch already pushed now differs from its remote copy, so publishing
it needs a force push, which this helper never does: it never pushes, fetches, rebases or resets, and
never touches the index or the worktree."""

HELP_EPILOG = """\
exit codes:
  0  folded; the branch now holds N, and the output names H, the protected tip, B, N, the recovery ref
     and an undo command quoted for a POSIX shell (git -C <repository> update-ref <branch> H N)
  1  refused, nothing was changed: HEAD detached or on main, master, the protected ref or its
     local copy (for --protected origin/trunk, the local trunk); an ambiguous --protected name; tracked
     changes in the index or worktree; a merge, rebase, cherry-pick, revert, bisect or sequencer in
     progress; no merge base, several merge bases, nothing to fold, a merge commit in B..H, or a base
     other than --expect-base. A lost compare-and-swap (the branch moved after it was read) is also
     exit 1; it leaves the branch where the other writer put it, and leaves the recovery ref, its
     reflog and the unreferenced folded commit behind (documented, intended residue).
  2  usage error or cannot evaluate: a bad option or message file, a shallow repository, a git
     failure, or a failed commit signature that the repository's signing configuration asks for.

hardening: every git call runs the git resolved to an absolute path when the helper starts, with every
ambient GIT_ variable removed, no system configuration, replace refs and info/grafts disabled, no
fsmonitor program, no hook, no commit-graph cache and no transport. Not neutralized: a decoy git
already first on PATH at start; the repository's own signing configuration (honoured on purpose); a
clean or process filter driver run by git status; index bits (assume-unchanged, skip-worktree) or
settings such as core.fileMode that hide a worktree change from the dirty check (such a change stays
in the worktree, since the fold changes no worktree file)."""


def _split_report(argv):
    """(report path or None, argv). The execution gate launches the self-test as `--self-test
    --execution-report ABS_PATH` (the suite's args); that trailing form, with an absolute path, is the
    only one accepted. --execution-report anywhere else, or without --self-test, is a usage error."""
    if "--execution-report" not in argv and not any(item.startswith("--execution-report=") for item in argv):
        return None, argv
    if len(argv) == 3 and argv[0] == "--self-test" and argv[1] == "--execution-report" \
            and os.path.isabs(argv[2]):
        return argv[2], ["--self-test"]
    raise ValueError("--execution-report ABS_PATH is accepted only as the trailing option after "
                     "--self-test")


def main(argv=None):
    if argv is None:
        # The process entry, reached only after the child-status guard on the entry line: the command
        # line, ended through the execution-report finalizer when the self-test armed it. In-process
        # callers (the self-test's _cli) pass their own argv and get the returned code.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    try:
        report_path, argv = _split_report(list(argv))
    except ValueError as exc:
        print("usage: fold_onto_base.py --self-test [--execution-report ABS_PATH]", file=sys.stderr)
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        prog="fold_onto_base.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=HELP_DESCRIPTION,
        epilog=HELP_EPILOG)
    parser.add_argument("--root", metavar="DIR", help=(
        "the repository to fold in (any directory inside it); default the current directory. Every git "
        "call and the printed undo command bind its top level with git -C."))
    parser.add_argument("--protected", metavar="REF", help=(
        "the protected line the branch was cut from, for example origin/main or refs/remotes/origin/main "
        "(required). A name that matches more than one ref (a tag main beside the branch main, a local "
        "branch origin/main beside the remote-tracking one) is refused; pass the full ref name then."))
    parser.add_argument("--message-file", metavar="ABS_PATH", help=(
        "an absolute path to a regular file holding the folded commit's message: nonempty UTF-8, no NUL "
        "byte, at most {} bytes, not a symbolic link (required).".format(MESSAGE_MAX_BYTES)))
    parser.add_argument("--expect-base", metavar="FULL_SHA", help=(
        "the full lowercase hex commit id the merge base must equal (40 or 64 characters, never "
        "abbreviated); it only narrows: a different derived base is refused and nothing changes."))
    parser.add_argument("--self-test", action="store_true", help=(
        "run the vectors and mutants in temporary repositories and exit; takes no other option except a "
        "trailing --execution-report ABS_PATH, which the execution gate appends."))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.self_test:
        if any(value is not None for value in (args.root, args.protected, args.message_file, args.expect_base)):
            parser.print_usage(sys.stderr)
            return 2
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return self_test()
    if args.protected is None or args.message_file is None:
        parser.print_usage(sys.stderr)
        print("error: --protected and --message-file are required", file=sys.stderr)
        return 2
    if args.root is not None:
        root = args.root
    else:
        try:
            root = str(Path.cwd())
        except OSError as exc:
            print("ERROR: fold-onto-base cannot evaluate: the working directory is unavailable ({})".format(exc),
                  file=sys.stderr)
            return 2
    return run(root, args.protected, args.message_file, args.expect_base)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool starts git and reads its exit
# status (subprocess), so under an ignored SIGCHLD a failed git would read as a success; the guard runs at
# the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("fold_onto_base.py") or main())
