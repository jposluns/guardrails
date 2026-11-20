#!/usr/bin/env python3
"""Refuse a commit whose staged content keeps a conflict-marker line in a path the operation in progress left unmerged.

The committed-conflict-marker class: a merge, a cherry-pick or a revert stops on conflicts, git writes conflict markers
into the conflicted files, and the resolution is staged and committed with a marker line still in it. This gate is the
commit-time check for that class.

  check_merge_markers.py --staged-scan ROOT [--index FILE]
                                      judge the staged content of the git working tree whose top level is ROOT,
                                      read from the index FILE when given (a pre-commit hook passes
                                      --index "$GIT_INDEX_FILE")
  check_merge_markers.py --self-test  the fixture vectors below
  check_merge_markers.py [--self-test] --execution-report ABS_PATH
                                      the self-test, its executed case ids reported to
                                      tools/check_selftest_execution.py (suite merge-markers-selftest)

WHEN IT ACTS. It acts while git records an operation in progress in the working tree's git directory
(git rev-parse --absolute-git-dir): MERGE_HEAD (a merge, one line per merged commit), CHERRY_PICK_HEAD (a cherry-pick),
REVERT_HEAD (a revert) or REBASE_HEAD (a rebase stopped on a conflict or at an edit stop; the commit it names is
judged like a cherry-pick of it, so a stopped rebase finished with a plain git commit is judged). REBASE_HEAD is read
only while a rebase is in progress as git reads it (a rebase-merge directory, or a rebase-apply directory that is not
a git am session): git rebase --quit leaves REBASE_HEAD behind, and a stale one would otherwise judge, or once its
commit is pruned refuse (exit 2), every later commit. Each state file
present must be a regular file whose every line is one full commit id (40 or 64 lowercase hex) that names a commit;
otherwise the run cannot evaluate (exit 2). MERGE_MSG must then be a readable regular file as well, except that a
rebase alone may leave none (git 2.53 writes none for a rebase --apply conflict or an edit stop), and then it lists
no conflict. With no state file the check still acts when MERGE_MSG lists conflicts: git 2.53 leaves such a MERGE_MSG
and no state file for a cherry-pick --no-commit or a merge --squash stopped on conflicts, so the paths that list
names are judged (a MERGE_MSG present is then held to the same regular-file rule). With no state file and no listed
conflict it prints a NOTE and exits 0.

SCOPE. The paths judged are the union of three sources, so marker text quoted in any other path never fires:
  1. a recompute from the commits, never from the index (staging a resolution removes its unmerged entries, so the
     index cannot say what conflicted): git merge-tree --write-tree --name-only --no-messages -z, for a merge HEAD
     against each MERGE_HEAD commit with --allow-unrelated-histories (a merge state file means git already ran that
     merge, so a merge of unrelated histories is recomputed rather than refused); for a cherry-pick of C, or a
     rebase stopped on C, HEAD against C with --merge-base set to each parent of C (the empty tree for a root commit);
     for a revert of C, HEAD against each parent of C with --merge-base=C (for a revert of a root commit, which
     merge-tree cannot recompute, every path of C's tree); with no state file there is no recompute;
  2. the Conflicts list git writes into MERGE_MSG for the operation that actually ran (a "Conflicts:" line behind the
     comment prefix, then one prefixed, tab-indented path per line; the legacy form with no prefix is read too). It
     covers a conflict the recompute does not reproduce, such as one under a strategy option or a configuration the
     recompute does not share;
  3. moved content: when a path of sources 1 and 2 is absent from the staged tree (the resolution moved it with
     git mv, or deleted it and added its content elsewhere), every staged path the resolution added is judged too: a
     path absent from HEAD's tree and from the tree of every commit the operation merged (each MERGE_HEAD commit; C
     for a cherry-pick or a rebase; each parent of C for a revert; HEAD alone when only MERGE_MSG lists conflicts).
A merge-tree that cannot run, exits other than 0 or 1, or prints output that does not parse is cannot-evaluate.

MERGE DRIVERS. The recompute never runs a merge driver. Every merge.<name>.driver the repository's configuration
defines (git config -z --get-regexp, under the isolation below) is replaced, for the merge-tree calls only, by a
stand-in command path under /dev/null that cannot start (passed as GIT_CONFIG_COUNT entries), so no configured
command and no shell runs. A driver that cannot start is a failed driver, so git counts the path it applies to as a
conflict (git 2.53 prints that it cannot exec the stand-in and exits 1 naming the path), and that path is judged
whatever the driver would have done; a recompute that reached the stand-in and exited other than 1 is
cannot-evaluate (exit 2).

RULE. A conflict-marker line is a line (split on LF, one trailing CR removed) that starts with exactly N copies of one
of the four marker characters git writes, <, |, = or >, followed by the end of the line or by a whitespace character
and any text; N is the path's conflict-marker-size attribute, read from the index (git check-attr --cached), when it
is a positive number, and 7 otherwise, as git itself reads it. That covers every marker git writes: the ours and
theirs markers, the separator, and the base marker of the diff3 and zdiff3 styles. A scoped path whose staged entry
is a regular file holding one such line is a finding (exit 1), named with its line number. A scoped path the staged
tree does not hold (deleted) has nothing to judge; a staged symbolic link or submodule is named on a NOTE line and
not scanned. An unmerged entry still in the index is cannot-evaluate (git refuses such a commit anyway).

ISOLATION. Every git call is bound to ROOT (git -C ROOT, confirmed to be the working tree's top level) and runs with
every ambient GIT_-prefixed variable dropped, the system configuration off (GIT_CONFIG_NOSYSTEM=1), replace objects
off and lazy fetch, prompts and optional locks off, so a GIT_DIR, GIT_WORK_TREE or GIT_INDEX_FILE in the caller's
environment cannot redirect it. --index FILE (taken from the current directory when relative) must be a regular file,
not a symbolic link, whose real path lies inside the git directory, or the run exits 2; the index reads are then given
GIT_INDEX_FILE set to that real path. Under git commit -a or git commit PATH, git hands the hook a temporary index
named only by GIT_INDEX_FILE, so a hook that omits --index would judge the wrong index: --staged-scan without --index
while GIT_INDEX_FILE is set in the caller's environment (git sets it for the pre-commit hook) exits 2 before any
scan. The git binary is resolved through PATH (a trusted-toolchain concern). Installing it: a pre-commit hook running
  python3 -I -B tools/check_merge_markers.py --staged-scan "$(git rev-parse --show-toplevel)" --index "$GIT_INDEX_FILE"
This pack ships no installer for it.

OVER-REJECTION, DISCLOSED. A conflicted path whose resolution legitimately holds a marker-shaped line (a document
that quotes git's markers) is a finding, and so is such a line in a path the resolution added while a conflicted
path is absent from the staged tree (scope source 3). The remedy is git's own: set the conflict-marker-size
attribute for that path in .gitattributes (git then writes markers of that size, and lines of another length are
content), never a bypass of the commit-time check. A path a configured merge driver applies to and both sides
changed is judged even where the driver merged it cleanly, with the same remedy.

RESIDUALS (not caught): a rebase finished with rebase --continue (git runs no pre-commit hook there), and git am
outside a rebase (not probed here); a merge ended with merge --quit, which leaves no state file and no MERGE_MSG; a
rebase ended with rebase --quit, unless the MERGE_MSG it leaves lists the conflicts (git 2.53 leaves one for the
merge backend, none for rebase --apply); a
cherry-pick --no-commit or a merge --squash whose MERGE_MSG Conflicts list was edited away or removed, since with no
state file nothing else names its conflicts (a revert run with --no-commit leaves REVERT_HEAD, as git 2.53 does, and
is judged); marker text in a path that did not conflict, including a conflicted path's content moved during the
resolution onto a path HEAD or a merged commit already holds, or moved while the conflicted path itself stays staged,
since only added paths are judged and only while a conflicted path is absent; a conflict neither the recompute nor
the MERGE_MSG list names (the user can edit MERGE_MSG, and the recompute uses the current configuration and
attributes); a clean or smudge filter git may run in the recompute under merge.renormalize (not probed here); an
octopus merge, recomputed pairwise against HEAD; a conflict-marker-size attribute changed in the staged
.gitattributes to make the markers git wrote count as content; a marker line in a file the merge treated as binary
is judged like any other; a commit made in a clone without the hook, or with git commit --no-verify by a person (the
pack's gate-weakening hook refuses --no-verify for the assistant); and every commit already in history (no CI range
mode ships: the CI roster runs this gate's self-test only).

Exit: 0 nothing found (or no operation in progress); 1 a finding; 2 cannot evaluate or a bad argument.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_merge_markers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import io
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 30
DEFAULT_MARKER_SIZE = 7
MARKER_CHARS = b"<|=>"
# The state files of an operation stopped on conflicts, in the order they are read.
STATE_FILES = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
               ("REBASE_HEAD", "rebase"))
# The operations git may leave stopped with no MERGE_MSG (a rebase --apply conflict, an edit stop); with only these
# in progress an absent MERGE_MSG lists no conflict.
MERGE_MSG_OPTIONAL = ("rebase",)
# The subject named when no state file is present and only MERGE_MSG lists conflicts.
MERGE_MSG_ONLY = "the operation whose conflicts MERGE_MSG lists"
# The command every configured merge driver is replaced by in the recompute: a path under a regular file, which no
# exec can start and which holds no shell metacharacter, so git execs it directly and no shell runs.
DRIVER_STAND_IN = "/dev/null/merge-markers-runs-no-merge-driver"
_OID = re.compile(rb"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ATOI = re.compile(rb"\A[ \t\n\v\f\r]*\+?([0-9]+)")
_WHITESPACE = b" \t\r\n\v\f"
# The merge-tree options of a recompute that computes its own merge base (a merge): git already ran the merge whose
# state file is present, so a merge of unrelated histories (merge --allow-unrelated-histories) is recomputed too.
MERGE_TREE_OWN_BASE = ("--allow-unrelated-histories",)


class CannotEvaluate(RuntimeError):
    """A required repository input or git result could not answer the question."""


def _git_env(index=None, config=()):
    """The caller's environment with every GIT_-prefixed variable dropped and an offline, non-interactive,
    replace-free posture asserted; GIT_INDEX_FILE is set only to an index this run has validated, and config holds
    (key, value) settings this run passes as GIT_CONFIG_COUNT entries."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    if config:
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for number, (key, value) in enumerate(config):
            env["GIT_CONFIG_KEY_{}".format(number)] = key
            env["GIT_CONFIG_VALUE_{}".format(number)] = value
    return env


def _git(root, *args, index=None, input_bytes=None, config=()):
    """One read-only git call bound to root; stdout and stderr are bytes."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], input=input_bytes, capture_output=True,
                              timeout=TIMEOUT, env=_git_env(index, config))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git invocation failed: {}".format(exc))


def _git_ok(root, *args, index=None, input_bytes=None):
    probe = _git(root, *args, index=index, input_bytes=input_bytes)
    if probe.returncode != 0:
        raise CannotEvaluate("git {} failed (exit {}): {}".format(
            args[0], probe.returncode, os.fsdecode(probe.stderr).strip() or "no message"))
    return probe.stdout


def _one_line(raw, what):
    lines = raw.split(b"\n")
    if len(lines) != 2 or lines[1] != b"" or not lines[0]:
        raise CannotEvaluate("{}: git did not print exactly one line".format(what))
    return lines[0]


def _repository(requested):
    """(top level, git directory) of the working tree whose top level is requested, both resolved."""
    try:
        wanted = Path(requested).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("{} cannot be resolved: {}".format(requested, exc))
    top = Path(os.fsdecode(_one_line(_git_ok(wanted, "rev-parse", "--show-toplevel"), "show-toplevel")))
    try:
        top = top.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("the working tree's top level cannot be resolved: {}".format(exc))
    if top != wanted:
        raise CannotEvaluate("{} is not the top level of its git working tree ({})".format(requested, top))
    gitdir = Path(os.fsdecode(_one_line(_git_ok(top, "rev-parse", "--absolute-git-dir"), "absolute-git-dir")))
    try:
        gitdir = gitdir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("the git directory cannot be resolved: {}".format(exc))
    return top, gitdir


def _validated_index(index_arg, gitdir):
    path = Path(index_arg)
    try:
        info = path.lstat()
    except OSError as exc:
        raise CannotEvaluate("--index {}: {}".format(index_arg, exc))
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CannotEvaluate("--index {} is not a regular file".format(index_arg))
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("--index {} cannot be resolved: {}".format(index_arg, exc))
    if not real.is_relative_to(gitdir) or real == gitdir:
        raise CannotEvaluate("--index {} lies outside the git directory {}".format(index_arg, gitdir))
    return real


def _read_regular(path, what):
    """The bytes of a state file that must be a regular file; None when it is absent."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotEvaluate("{} cannot be examined: {}".format(what, exc))
    if not stat.S_ISREG(info.st_mode):
        raise CannotEvaluate("{} is not a regular file".format(what))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CannotEvaluate("{} cannot be read: {}".format(what, exc))


def _is_commit(root, oid):
    probe = _git(root, "cat-file", "-t", os.fsdecode(oid))
    return probe.returncode == 0 and probe.stdout == b"commit\n"


def _exists(path, what):
    """True when path exists (not followed when a symbolic link); a path that cannot be examined is cannot-evaluate."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CannotEvaluate("{} cannot be examined: {}".format(what, exc))
    return True


def _rebase_in_progress(gitdir):
    """True when a rebase is in progress as git itself reads it (git status, git rebase --continue): a rebase-merge
    directory, or a rebase-apply directory that is not a git am session. git rebase --quit, and the removal of the
    rebase directory git's own advice names, leave REBASE_HEAD behind, so REBASE_HEAD alone is stale state."""
    if _exists(gitdir / "rebase-merge", "rebase-merge"):
        return True
    return (_exists(gitdir / "rebase-apply", "rebase-apply")
            and not _exists(gitdir / "rebase-apply" / "applying", "rebase-apply/applying"))


def _read_state(root, gitdir):
    """[(operation, [commit id, ...])] for each state file present, validated; a REBASE_HEAD with no rebase in
    progress is stale and not read."""
    state = []
    for name, operation in STATE_FILES:
        if operation == "rebase" and not _rebase_in_progress(gitdir):
            continue
        raw = _read_regular(gitdir / name, name)
        if raw is None:
            continue
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        if not lines:
            raise CannotEvaluate("{} is empty".format(name))
        oids = []
        for line in lines:
            if not _OID.match(line):
                raise CannotEvaluate("{} holds a line that is not one full commit id".format(name))
            if not _is_commit(root, line):
                raise CannotEvaluate("{} names {}, which is not a commit here".format(name, os.fsdecode(line)))
            oids.append(line)
        if operation != "merge" and len(oids) != 1:
            raise CannotEvaluate("{} names {} commits, not one".format(name, len(oids)))
        state.append((operation, oids))
    return state


def _empty_tree(root):
    return _one_line(_git_ok(root, "hash-object", "-t", "tree", "--stdin", input_bytes=b""), "empty tree")


def _parents(root, oid):
    fields = _one_line(_git_ok(root, "rev-list", "--parents", "-n", "1", os.fsdecode(oid)), "parents").split(b" ")
    if not fields or fields[0] != oid or not all(_OID.match(field) for field in fields):
        raise CannotEvaluate("git rev-list printed parents that do not parse for {}".format(os.fsdecode(oid)))
    return fields[1:]


def _driver_overrides(root):
    """((key, DRIVER_STAND_IN), ...) for every merge.<name>.driver the repository's configuration defines."""
    probe = _git(root, "config", "-z", "--get-regexp", r"^merge\..+\.driver$")
    if probe.returncode == 1 and not probe.stdout:
        return ()
    if probe.returncode != 0:
        raise CannotEvaluate("git config --get-regexp failed (exit {}): {}".format(
            probe.returncode, os.fsdecode(probe.stderr).strip() or "no message"))
    keys = []
    for record in probe.stdout.split(b"\0"):
        if not record:
            continue
        key = os.fsdecode(record.partition(b"\n")[0])
        if key in keys:
            continue
        if not key.startswith("merge.") or not key.endswith(".driver") or len(key) <= len("merge..driver"):
            raise CannotEvaluate("git config printed a merge driver key that does not parse")
        keys.append(key)
    return tuple((key, DRIVER_STAND_IN) for key in keys)


def _merge_tree(root, ours, theirs, base=None, overrides=()):
    """The conflicted paths of one recomputed merge, every configured merge driver replaced by the stand-in."""
    args = ["merge-tree", "--write-tree", "--name-only", "--no-messages", "-z"]
    if base is not None:
        args.append("--merge-base=" + os.fsdecode(base))
    else:
        args += list(MERGE_TREE_OWN_BASE)
    args += [os.fsdecode(ours), os.fsdecode(theirs)]
    probe = _git(root, *args, config=overrides)
    if overrides and os.fsencode(DRIVER_STAND_IN) in probe.stderr and probe.returncode != 1:
        # A stand-in that cannot start is a failed driver, so git counts its path as a conflict (exit 1) and the path
        # is judged; a recompute that reached the stand-in and reported anything else cannot say that.
        raise CannotEvaluate("the recompute reached a configured merge driver ({}) and git merge-tree did not "
                             "report a conflict (exit {})".format(", ".join(key for key, _ in overrides),
                                                                  probe.returncode))
    if probe.returncode not in (0, 1):
        raise CannotEvaluate("git merge-tree failed (exit {}): {}".format(
            probe.returncode, os.fsdecode(probe.stderr).strip() or "no message"))
    fields = probe.stdout.split(b"\0")
    if len(fields) < 2 or fields[-1] != b"" or not _OID.match(fields[0]):
        raise CannotEvaluate("git merge-tree printed output that does not parse")
    paths = {os.fsdecode(field) for field in fields[1:-1] if field}
    if probe.returncode == 0 and paths:
        raise CannotEvaluate("git merge-tree reported a clean merge and conflicted paths at once")
    if probe.returncode == 1 and not paths:
        raise CannotEvaluate("git merge-tree reported a conflict and named no path")
    return paths


def _recompute(root, head, state):
    """The union of the conflicted paths of every recomputed merge, with the source named per path."""
    paths = set()
    overrides = _driver_overrides(root)
    for operation, oids in state:
        if operation == "merge":
            for oid in oids:
                paths |= _merge_tree(root, head, oid, overrides=overrides)
            continue
        oid = oids[0]
        parents = _parents(root, oid)
        if operation in ("cherry-pick", "rebase"):
            for base in parents or [_empty_tree(root)]:
                paths |= _merge_tree(root, head, oid, base, overrides=overrides)
        elif parents:
            for parent in parents:
                paths |= _merge_tree(root, head, parent, oid, overrides=overrides)
        else:
            paths |= _tree_paths(root, oid)
    return paths


def _tree_paths(root, treeish):
    listing = _git_ok(root, "ls-tree", "-r", "-z", "--name-only", "--end-of-options", os.fsdecode(treeish))
    return {os.fsdecode(name) for name in listing.split(b"\0") if name}


def _sides(root, head, state):
    """The commits whose trees the resolution starts from: HEAD and every commit the operation merged."""
    sides = [] if head is None else [head]
    for operation, oids in state:
        if operation == "revert":
            sides += _parents(root, oids[0])
        else:
            sides += oids
    return sides


def _added_paths(root, entries, sides):
    """The staged paths the resolution added: absent from the tree of every side."""
    known = set()
    for side in sides:
        known |= _tree_paths(root, side)
    return {path for path in entries if path not in known}


def _head_or_none(root):
    """HEAD's commit id, or None when HEAD names no commit (an unborn branch)."""
    probe = _git(root, "rev-parse", "--verify", "-q", "--end-of-options", "HEAD^{commit}")
    if probe.returncode != 0:
        return None
    return _one_line(probe.stdout, "HEAD")


def _merge_msg_conflicts(gitdir, required=True):
    """The paths of every Conflicts list in MERGE_MSG; an absent MERGE_MSG lists none when it is not required."""
    raw = _read_regular(gitdir / "MERGE_MSG", "MERGE_MSG")
    if raw is None:
        if not required:
            return set()
        raise CannotEvaluate("MERGE_MSG is absent while an operation is in progress")
    paths = set()
    prefix = None
    for line in raw.split(b"\n"):
        if line.endswith(b"\r"):
            line = line[:-1]
        if prefix is not None:
            if line.startswith(prefix + b"\t") and len(line) > len(prefix) + 1:
                paths.add(os.fsdecode(line[len(prefix) + 1:]))
                continue
            prefix = None
        if line == b"Conflicts:":
            prefix = b""
        elif line.endswith(b" Conflicts:"):
            head = line[:-len(b" Conflicts:")]
            if head and not any(byte in _WHITESPACE for byte in head):
                prefix = head
    return paths


def _staged_entries(root, index):
    """{path: (mode, object id)} of the stage-0 entries; an unmerged entry is cannot-evaluate."""
    if _git_ok(root, "ls-files", "-u", "-z", index=index):
        raise CannotEvaluate("the index still holds unmerged entries")
    entries = {}
    for record in _git_ok(root, "ls-files", "-s", "-z", index=index).split(b"\0"):
        if not record:
            continue
        meta, tab, name = record.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3 or fields[2] != b"0" or not _OID.match(fields[1]):
            raise CannotEvaluate("git ls-files printed an index entry that does not parse")
        entries[os.fsdecode(name)] = (fields[0], fields[1])
    return entries


def _marker_sizes(root, index, paths):
    """{path: conflict-marker size} read from the index's attributes, as git reads the attribute."""
    if not paths:
        return {}
    feed = b"".join(os.fsencode(path) + b"\0" for path in paths)
    out = _git_ok(root, "check-attr", "--cached", "-z", "--stdin", "conflict-marker-size", index=index,
                  input_bytes=feed)
    fields = out.split(b"\0")
    if not fields or fields[-1] != b"" or (len(fields) - 1) % 3:
        raise CannotEvaluate("git check-attr printed output that does not parse")
    sizes = {}
    for start in range(0, len(fields) - 1, 3):
        name, attribute, value = fields[start:start + 3]
        if attribute != b"conflict-marker-size":
            raise CannotEvaluate("git check-attr printed an unexpected attribute")
        match = _ATOI.match(value)
        size = int(match.group(1)) if match else 0
        sizes[os.fsdecode(name)] = size if size > 0 else DEFAULT_MARKER_SIZE
    missing = [path for path in paths if path not in sizes]
    if missing:
        raise CannotEvaluate("git check-attr named no value for {}".format(missing[0]))
    return sizes


def is_marker_line(line, size=DEFAULT_MARKER_SIZE):
    """True when line (bytes, no LF) is a conflict-marker line of the given size."""
    if line.endswith(b"\r"):
        line = line[:-1]
    if len(line) < size or line[:1] == b"" or line[0] not in MARKER_CHARS:
        return False
    if line[:size] != line[:1] * size:
        return False
    return len(line) == size or line[size] in _WHITESPACE


def _blob(root, oid):
    return _git_ok(root, "cat-file", "blob", os.fsdecode(oid))


def staged_scan(root_arg, index_arg=None, out=None):
    """The commit-time verdict: 0 nothing found, 1 a finding, 2 cannot evaluate."""
    out = sys.stdout if out is None else out
    try:
        root, gitdir = _repository(root_arg)
        index = None if index_arg is None else _validated_index(index_arg, gitdir)
        state = _read_state(root, gitdir)
        if state:
            subject = "the {} in progress".format(", ".join(sorted({operation for operation, _ in state})))
            head = _one_line(_git_ok(root, "rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"), "HEAD")
            required = any(operation not in MERGE_MSG_OPTIONAL for operation, _ in state)
            scope = _recompute(root, head, state) | _merge_msg_conflicts(gitdir, required)
            sides = _sides(root, head, state)
        else:
            scope = _listed_without_state(gitdir)
            if not scope:
                print("merge-markers: NOTE no merge, cherry-pick, revert or rebase is in progress and MERGE_MSG "
                      "lists no conflict; nothing to judge", file=out)
                return 0
            subject = MERGE_MSG_ONLY
            head = _head_or_none(root)
            sides = [] if head is None else [head]
        entries = _staged_entries(root, index)
        missing = sorted(path for path in scope if path not in entries)
        added = _added_paths(root, entries, sides) if missing else set()
        if added:
            print("merge-markers: NOTE {} conflicted path(s) absent from the staged tree ({}), so the {} staged "
                  "path(s) the resolution added are judged too".format(len(missing), missing[0], len(added)),
                  file=out)
        judged = sorted(path for path in scope | added if path in entries)
        sizes = _marker_sizes(root, index, judged)
        findings = []
        for path in judged:
            mode, oid = entries[path]
            if mode not in (b"100644", b"100755"):
                print("merge-markers: NOTE {} is staged as mode {}, not a regular file; not scanned".format(
                    path, os.fsdecode(mode)), file=out)
                continue
            for number, line in enumerate(_blob(root, oid).split(b"\n"), 1):
                if is_marker_line(line, sizes[path]):
                    findings.append((path, number))
    except CannotEvaluate as exc:
        print("merge-markers: cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    for path, number in findings:
        where = ("a path {} left unmerged".format(subject) if path in scope
                 else "a path the resolution of {} added".format(subject))
        print("merge-markers: FINDING {}:{}: a conflict-marker line in {}".format(path, number, where), file=out)
    if findings:
        print("merge-markers: resolve the conflict in each named path (or set its conflict-marker-size attribute "
              "if the file quotes markers), stage it, and commit again", file=out)
        return 1
    print("merge-markers: OK {} scoped path(s) of {} hold no conflict-marker line".format(len(judged), subject),
          file=out)
    return 0


def _listed_without_state(gitdir):
    """The paths MERGE_MSG lists as conflicts when no state file is present (none when MERGE_MSG is absent)."""
    return _merge_msg_conflicts(gitdir, required=False)


def _unpassed_index():
    """The refusal when the caller's environment names an index (GIT_INDEX_FILE) that --index did not pass on."""
    if "GIT_INDEX_FILE" in os.environ:
        return ("GIT_INDEX_FILE is set (git names the index being committed to its pre-commit hook), but --index "
                "was not passed; run --staged-scan ROOT --index \"$GIT_INDEX_FILE\"")
    return None


# ---------------------------------------------------------------- self-test
# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record the
# instrumented check() helper appends to; tools/check_selftest_execution.py reconciles the report against the registry,
# so a deleted or unreached case is a finding there.
SUITE_ID = "merge-markers-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {}".format(name, detail))


def _fixture_git(root, *args, check_exit=True, input_bytes=None, extra_env=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                          GIT_AUTHOR_NAME="Merge Markers Self Test", GIT_AUTHOR_EMAIL="merge-markers@example.invalid",
                          GIT_COMMITTER_NAME="Merge Markers Self Test",
                          GIT_COMMITTER_EMAIL="merge-markers@example.invalid", **(extra_env or {}))
    probe = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                            "-c", "rerere.enabled=false", *args],
                           input=input_bytes, capture_output=True, timeout=TIMEOUT, env=env)
    if check_exit and probe.returncode != 0:
        raise RuntimeError("fixture git {} failed (exit {}): {}".format(
            args[0], probe.returncode, os.fsdecode(probe.stderr).strip()))
    return probe


def _fixture_conflict(parent, name, operation="merge", ours=b"a\nOURS\nc\n", theirs=b"a\nTHEIRS\nc\n",
                      base=b"a\nb\nc\n", attributes=None, style="merge"):
    """A repository stopped on a conflict in f.txt by operation; returns its root."""
    root = parent / name
    root.mkdir()
    _fixture_git(root, "init", "-q", "-b", "main")
    if attributes is not None:
        (root / ".gitattributes").write_bytes(attributes)
    (root / "f.txt").write_bytes(base)
    (root / "quoted.md").write_bytes(b"Quoted markers:\n<<<<<<< ours\n=======\n>>>>>>> theirs\n")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "base")
    _fixture_git(root, "switch", "-q", "-c", "side")
    (root / "f.txt").write_bytes(theirs)
    _fixture_git(root, "commit", "-q", "-a", "-m", "side")
    _fixture_git(root, "switch", "-q", "main")
    (root / "f.txt").write_bytes(ours)
    _fixture_git(root, "commit", "-q", "-a", "-m", "main")
    style_args = ("-c", "merge.conflictStyle=" + style)
    if operation == "merge":
        probe = _fixture_git(root, *style_args, "merge", "side", check_exit=False)
    elif operation == "merge-squash":
        probe = _fixture_git(root, *style_args, "merge", "--squash", "side", check_exit=False)
    elif operation == "cherry-pick":
        probe = _fixture_git(root, *style_args, "cherry-pick", "side", check_exit=False)
    elif operation == "cherry-pick-no-commit":
        probe = _fixture_git(root, *style_args, "cherry-pick", "--no-commit", "side", check_exit=False)
    elif operation in ("rebase", "rebase-apply"):
        # Replay the side commit onto main, so the rebase stops on it.
        _fixture_git(root, "switch", "-q", "side")
        backend = ("--apply",) if operation == "rebase-apply" else ()
        probe = _fixture_git(root, *style_args, "rebase", *backend, "main", check_exit=False)
    else:
        # Revert the main commit after a later commit rewrote the same line, so the revert conflicts with it.
        (root / "f.txt").write_bytes(theirs)
        _fixture_git(root, "commit", "-q", "-a", "-m", "later")
        mode = ("--no-commit",) if operation == "revert-no-commit" else ("--no-edit",)
        probe = _fixture_git(root, *style_args, "revert", *mode, "HEAD~1", check_exit=False)
    if probe.returncode != 1:
        raise RuntimeError("fixture {}: {} did not stop on a conflict (exit {})".format(
            name, operation, probe.returncode))
    return root


def _fixture_unrelated(parent, name):
    """A repository stopped on an add/add conflict in f.txt by merge --allow-unrelated-histories; returns its root."""
    root = parent / name
    root.mkdir()
    _fixture_git(root, "init", "-q", "-b", "main")
    (root / "f.txt").write_bytes(b"a\nOURS\nc\n")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "main root")
    _fixture_git(root, "switch", "-q", "--orphan", "other")
    (root / "f.txt").write_bytes(b"a\nTHEIRS\nc\n")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "other root")
    _fixture_git(root, "switch", "-q", "main")
    probe = _fixture_git(root, "merge", "--allow-unrelated-histories", "other", check_exit=False)
    if probe.returncode != 1:
        raise RuntimeError("fixture {}: the unrelated-histories merge did not stop on a conflict (exit {})".format(
            name, probe.returncode))
    return root


def _fixture_edit_stop(parent, name):
    """A repository whose rebase -i stopped at an edit line (REBASE_HEAD, no conflict); returns its root."""
    root = parent / name
    root.mkdir()
    _fixture_git(root, "init", "-q", "-b", "main")
    (root / "f.txt").write_bytes(b"a\nb\nc\n")
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "base")
    (root / "f.txt").write_bytes(b"a\nEDITED\nc\n")
    _fixture_git(root, "commit", "-q", "-a", "-m", "edited")
    tip = os.fsdecode(_fixture_git(root, "rev-parse", "HEAD").stdout.strip())
    editor = parent / (name + "-editor.sh")
    editor.write_text("#!/bin/sh\nprintf 'edit {}\\n' > \"$1\"\n".format(tip))
    os.chmod(editor, 0o755)
    probe = _fixture_git(root, "rebase", "-i", "HEAD~1", check_exit=False,
                         extra_env={"GIT_SEQUENCE_EDITOR": str(editor)})
    if probe.returncode != 0:
        raise RuntimeError("fixture {}: rebase -i did not stop at its edit line (exit {}): {}".format(
            name, probe.returncode, os.fsdecode(probe.stderr).strip()))
    return root


def _gitdir(root):
    return Path(os.fsdecode(_fixture_git(root, "rev-parse", "--absolute-git-dir").stdout.strip()))


def _stage(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    _fixture_git(root, "add", "--", path)


@contextlib.contextmanager
def _replaced(name, value):
    """Replace one module-level name of this gate for the body: the self-test's revert of one fix."""
    module = sys.modules[__name__]
    saved = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, saved)


@contextlib.contextmanager
def _environ(name, value):
    """Set (or, for None, remove) one variable of this process's environment for the body."""
    saved = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved


def _scan(root, index=None):
    """staged_scan's exit for root (its output captured), or the text of an exception it raised."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return staged_scan(str(root), index, out=io.StringIO())
    except Exception as exc:  # noqa: BLE001  a crash is a named failure, never a pass
        return "raised {}: {}".format(type(exc).__name__, exc)


def _main_exit(argv):
    """main()'s exit for argv, its output captured."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def _self_test_isolated(temp):
    def build(label, builder):
        try:
            return builder()
        except Exception as exc:  # noqa: BLE001  a fixture that cannot be built is a named NOT RUN failure
            FAILURES.append("NOT RUN: {}: {}".format(label, exc))
            return None

    clean = b"a\nRESOLVED\nc\n"
    marked = b"a\n<<<<<<< HEAD\nOURS\n=======\nTHEIRS\n>>>>>>> side\nc\n"
    # V1 a merge stopped on a conflict, the markers staged as they are: a finding. Its fix reverted (the marker
    # predicate answering never) passes it.
    v1 = build("V1", lambda: _fixture_conflict(temp, "v1"))
    if v1 is not None:
        _fixture_git(v1, "add", "f.txt")
        got = _scan(v1)
        check("merge/markers-staged-finding", got == 1, got)
        with _replaced("is_marker_line", lambda line, size=DEFAULT_MARKER_SIZE: False):
            got = _scan(v1)
        check("flip/marker-predicate-off-passes", got == 0, got)
    # V2 the same marker text in a path the merge did not conflict (git add -A stages it): no finding, even though
    # the path is new, since the conflicted path stays staged. Its fix reverted (every staged path in scope) refuses.
    v2 = build("V2", lambda: _fixture_conflict(temp, "v2"))
    if v2 is not None:
        (v2 / "f.txt").write_bytes(clean)
        (v2 / "notes.md").write_bytes(b"<<<<<<< HEAD\n=======\n>>>>>>> side\n")
        _fixture_git(v2, "add", "-A")
        got = _scan(v2)
        check("scope/unconflicted-path-marker-text-passes", got == 0, got)
        with _replaced("_recompute", lambda root, head, state: set(_staged_entries(root, None))):
            got = _scan(v2)
        check("flip/unscoped-refuses", got == 1, got)
    # V3 a clean resolution: no finding.
    v3 = build("V3", lambda: _fixture_conflict(temp, "v3"))
    if v3 is not None:
        _stage(v3, "f.txt", clean)
        got = _scan(v3)
        check("merge/clean-resolution-passes", got == 0, got)
    # V4 merge state that cannot be read: cannot evaluate. Its fix reverted (the state reader returning nothing)
    # passes it.
    for name, slug, spoil in (
            ("state/merge-head-directory-cannot-evaluate", "v4a",
             lambda gd: ((gd / "MERGE_HEAD").unlink(), (gd / "MERGE_HEAD").mkdir())),
            ("state/merge-head-empty-cannot-evaluate", "v4b", lambda gd: (gd / "MERGE_HEAD").write_bytes(b"")),
            ("state/merge-head-not-a-commit-id-cannot-evaluate", "v4c",
             lambda gd: (gd / "MERGE_HEAD").write_bytes(b"side\n")),
            ("state/merge-head-names-a-blob-cannot-evaluate", "v4d", None),
            ("state/merge-msg-absent-cannot-evaluate", "v4e", lambda gd: (gd / "MERGE_MSG").unlink()),
            ("state/merge-msg-directory-cannot-evaluate", "v4f",
             lambda gd: ((gd / "MERGE_MSG").unlink(), (gd / "MERGE_MSG").mkdir())),
            ("state/merge-head-symlink-cannot-evaluate", "v4g", lambda gd: (
                (gd / "MERGE_HEAD").rename(gd / "MERGE_HEAD.real"), (gd / "MERGE_HEAD").symlink_to("MERGE_HEAD.real"))),
    ):
        root = build(name, lambda slug=slug: _fixture_conflict(temp, slug))
        if root is None:
            continue
        _stage(root, "f.txt", clean)
        gd = _gitdir(root)
        if spoil is None:
            blob = _fixture_git(root, "hash-object", "-w", "--stdin", input_bytes=b"x\n").stdout.strip()
            (gd / "MERGE_HEAD").write_bytes(blob + b"\n")
        else:
            spoil(gd)
        got = _scan(root)
        check(name, got == 2, got)
    v4h = build("V4h", lambda: _fixture_conflict(temp, "v4h"))
    if v4h is not None:
        _stage(v4h, "f.txt", clean)

        def failing(*args, **kwargs):
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: injected")

        def garbage(*args, **kwargs):
            return subprocess.CompletedProcess(args, 1, b"not a tree id\0f.txt\0", b"")
        for name, fake in (("recompute/merge-tree-fails-cannot-evaluate", failing),
                           ("recompute/merge-tree-garbage-cannot-evaluate", garbage)):
            real_git = _git

            def routed(root, *args, fake=fake, real_git=real_git, **kwargs):
                if args and args[0] == "merge-tree":
                    return fake(root, *args, **kwargs)
                return real_git(root, *args, **kwargs)
            with _replaced("_git", routed):
                got = _scan(v4h)
            check(name, got == 2, got)
        _gd = _gitdir(v4h)
        (_gd / "MERGE_HEAD").write_bytes(b"")
        with _replaced("_read_state", lambda root, gitdir: []):
            got = _scan(v4h)
        check("flip/state-reader-swallowing-passes", got == 0, got)
    # V5 a conflicted path that quotes markers on both sides. With no attribute, keeping the quotation is a finding
    # (the disclosed over-rejection); with conflict-marker-size=9 git writes 9-character markers, so keeping the
    # 7-character quotation passes and leaving git's markers is a finding.
    quote = b"<<<<<<< ours\n=======\n>>>>>>> theirs\n"
    v5a = build("V5a", lambda: _fixture_conflict(temp, "v5a", ours=quote + b"OURS\n", theirs=quote + b"THEIRS\n",
                                                 base=quote + b"b\n"))
    if v5a is not None:
        _stage(v5a, "f.txt", quote + b"RESOLVED\n")
        got = _scan(v5a)
        check("attr/quoted-markers-kept-no-attribute-finding", got == 1, got)
    attrs = b"f.txt conflict-marker-size=9\n"
    v5b = build("V5b", lambda: _fixture_conflict(temp, "v5b", ours=quote + b"OURS\n", theirs=quote + b"THEIRS\n",
                                                 base=quote + b"b\n", attributes=attrs))
    if v5b is not None:
        written = (v5b / "f.txt").read_bytes()
        _fixture_git(v5b, "add", "f.txt")
        got = _scan(v5b)
        check("attr/size-9-markers-staged-finding", got == 1 and b"<<<<<<<<< " in written, (got, written[:40]))
        _stage(v5b, "f.txt", quote + b"RESOLVED\n")
        got = _scan(v5b)
        check("attr/quoted-7-under-size-9-passes", got == 0, got)
        with _replaced("_marker_sizes", lambda root, index, paths: {path: DEFAULT_MARKER_SIZE for path in paths}):
            got = _scan(v5b)
        check("flip/attribute-ignored-refuses", got == 1, got)
    # V6 a path only one source names. 6a: only MERGE_MSG lists other.txt (its Conflicts list names it); 6b: the
    # Conflicts list was edited away and only the recompute names f.txt. Each source reverted passes its case.
    v6 = build("V6a", lambda: _fixture_conflict(temp, "v6a"))
    if v6 is not None:
        _stage(v6, "f.txt", clean)
        _stage(v6, "other.txt", b"<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> side\n")
        gd = _gitdir(v6)
        (gd / "MERGE_MSG").write_bytes((gd / "MERGE_MSG").read_bytes() + b"#\tother.txt\n")
        got = _scan(v6)
        check("scope/merge-msg-only-path-finding", got == 1, got)
        with _replaced("_merge_msg_conflicts", lambda gitdir, required=True: set()):
            got = _scan(v6)
        check("flip/merge-msg-source-off-passes", got == 0, got)
    v6b = build("V6b", lambda: _fixture_conflict(temp, "v6b"))
    if v6b is not None:
        _fixture_git(v6b, "add", "f.txt")
        gd = _gitdir(v6b)
        (gd / "MERGE_MSG").write_bytes(b"Merge branch 'side'\n")
        got = _scan(v6b)
        check("scope/recompute-only-path-finding", got == 1, got)
        with _replaced("_recompute", lambda root, head, state: set()):
            got = _scan(v6b)
        check("flip/recompute-off-passes", got == 0, got)
    # V7 the temporary index of git commit -a: the repository's index is clean, the hook's index keeps the
    # markers. With --index it is a finding; reading the repository's own index instead passes it. An --index
    # outside the git directory, or a symbolic link, is cannot-evaluate.
    v7 = build("V7", lambda: _fixture_conflict(temp, "v7"))
    if v7 is not None:
        gd = _gitdir(v7)
        _stage(v7, "f.txt", clean)
        conflicted = (v7 / "f.txt").read_bytes()
        temporary = gd / "index.lock-selftest"
        shutil.copyfile(gd / "index", temporary)
        (v7 / "f.txt").write_bytes(marked)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _git_fixture_env import git_fixture_env
        subprocess.run(["git", "-C", str(v7), "add", "f.txt"], capture_output=True, timeout=TIMEOUT, check=True,
                       env=git_fixture_env(GIT_INDEX_FILE=str(temporary)))
        (v7 / "f.txt").write_bytes(conflicted)
        got = _scan(v7, str(temporary))
        check("index/commit-a-temporary-index-finding", got == 1, got)
        got = _scan(v7)
        check("flip/repository-index-read-passes", got == 0, got)
        outside = temp / "outside-index"
        shutil.copyfile(temporary, outside)
        got = _scan(v7, str(outside))
        check("index/outside-git-dir-cannot-evaluate", got == 2, got)
        link = gd / "index-link"
        link.symlink_to(temporary)
        got = _scan(v7, str(link))
        check("index/symlink-cannot-evaluate", got == 2, got)
    # V8 CRLF line endings: a marker line ending in CR is a finding.
    v8 = build("V8", lambda: _fixture_conflict(temp, "v8"))
    if v8 is not None:
        _stage(v8, "f.txt", b"a\r\n<<<<<<< HEAD\r\nOURS\r\n=======\r\nTHEIRS\r\n>>>>>>> side\r\nc\r\n")
        got = _scan(v8)
        check("rule/crlf-markers-finding", got == 1, got)
    # V9 each marker form alone, the diff3 and zdiff3 base marker included: each is a finding. A line of eight
    # marker characters, or one glued to text, is not a marker of size 7.
    v9 = {}
    for style in ("diff3", "zdiff3"):
        v9[style] = build("V9 " + style, lambda style=style: _fixture_conflict(temp, "v9" + style, style=style))
    for name, style in (("rule/diff3-markers-staged-finding", "diff3"),
                        ("rule/zdiff3-markers-staged-finding", "zdiff3")):
        root = v9[style]
        if root is None:
            continue
        written = (root / "f.txt").read_bytes()
        _fixture_git(root, "add", "f.txt")
        got = _scan(root)
        check(name, got == 1 and b"\n||||||| " in written, (got, written[:60]))
    for name, style, line, expected in (
            ("rule/diff3-lone-ours-marker", "diff3", b"<<<<<<< HEAD", 1),
            ("rule/diff3-lone-base-marker", "diff3", b"||||||| base", 1),
            ("rule/diff3-lone-bare-base-marker", "diff3", b"|||||||", 1),
            ("rule/diff3-lone-separator", "diff3", b"=======", 1),
            ("rule/diff3-lone-theirs-marker", "diff3", b">>>>>>> side", 1),
            ("rule/diff3-separator-trailing-whitespace", "diff3", b"======= \t", 1),
            ("rule/diff3-eight-equals-not-a-marker", "diff3", b"========", 0),
            ("rule/diff3-eight-less-than-not-a-marker", "diff3", b"<<<<<<<<", 0),
            ("rule/diff3-glued-text-not-a-marker", "diff3", b"=======x", 0),
            ("rule/diff3-six-less-than-not-a-marker", "diff3", b"<<<<<<", 0),
            ("rule/diff3-indented-not-a-marker", "diff3", b" =======", 0),
            ("rule/zdiff3-lone-ours-marker", "zdiff3", b"<<<<<<< HEAD", 1),
            ("rule/zdiff3-lone-base-marker", "zdiff3", b"||||||| base", 1),
            ("rule/zdiff3-lone-bare-base-marker", "zdiff3", b"|||||||", 1),
            ("rule/zdiff3-lone-separator", "zdiff3", b"=======", 1),
            ("rule/zdiff3-lone-theirs-marker", "zdiff3", b">>>>>>> side", 1),
            ("rule/zdiff3-separator-trailing-whitespace", "zdiff3", b"======= \t", 1),
            ("rule/zdiff3-eight-equals-not-a-marker", "zdiff3", b"========", 0),
            ("rule/zdiff3-eight-less-than-not-a-marker", "zdiff3", b"<<<<<<<<", 0),
            ("rule/zdiff3-glued-text-not-a-marker", "zdiff3", b"=======x", 0),
            ("rule/zdiff3-six-less-than-not-a-marker", "zdiff3", b"<<<<<<", 0),
            ("rule/zdiff3-indented-not-a-marker", "zdiff3", b" =======", 0),
    ):
        root = v9[style]
        if root is None:
            continue
        _stage(root, "f.txt", b"a\nRESOLVED\n" + line + b"\nc\n")
        got = _scan(root)
        check(name, got == expected, "expected exit {}, got {}".format(expected, got))
    # V10 no operation in progress: staged marker text is not judged.
    v10 = build("V10", lambda: _fixture_conflict(temp, "v10"))
    if v10 is not None:
        _fixture_git(v10, "merge", "--abort")
        _stage(v10, "f.txt", b"<<<<<<< HEAD\n")
        got = _scan(v10)
        check("state/no-operation-passes", got == 0, got)
    # V11 and V12 a cherry-pick and a revert stopped on conflicts: markers staged are a finding, a clean
    # resolution passes. Their fix reverted (reading MERGE_HEAD only, and MERGE_MSG unread without a state file,
    # which would otherwise still name f.txt) passes the finding.
    for markers_id, flip_id, clean_id, slug, operation in (
            ("cherry-pick/markers-staged-finding", "flip/cherry-pick-merge-state-only-passes",
             "cherry-pick/clean-resolution-passes", "v11", "cherry-pick"),
            ("revert/markers-staged-finding", "flip/revert-merge-state-only-passes",
             "revert/clean-resolution-passes", "v12", "revert"),
    ):
        root = build(slug, lambda slug=slug, operation=operation: _fixture_conflict(temp, slug, operation))
        if root is None:
            continue
        _fixture_git(root, "add", "f.txt")
        got = _scan(root)
        check(markers_id, got == 1, got)
        with _replaced("STATE_FILES", (("MERGE_HEAD", "merge"),)), \
                _replaced("_listed_without_state", lambda gitdir: set()):
            got = _scan(root)
        check(flip_id, got == 0, got)
        _stage(root, "f.txt", clean)
        got = _scan(root)
        check(clean_id, got == 0, got)
    # V13 a conflicted path resolved to a symbolic link: named on a NOTE line, not scanned.
    v13 = build("V13", lambda: _fixture_conflict(temp, "v13"))
    if v13 is not None:
        (v13 / "f.txt").unlink()
        (v13 / "f.txt").symlink_to("quoted.md")
        _fixture_git(v13, "add", "f.txt")
        got = _scan(v13)
        check("rule/symlink-resolution-not-scanned", got == 0, got)
    # V14 a ROOT that is not the top level of a working tree, and an unmerged entry left in the index.
    v14 = build("V14", lambda: _fixture_conflict(temp, "v14"))
    if v14 is not None:
        (v14 / "sub").mkdir()
        got = _scan(v14 / "sub")
        check("repo/root-below-top-level-cannot-evaluate", got == 2, got)
        got = _scan(v14)
        check("repo/unmerged-entry-cannot-evaluate", got == 2, got)
    # V16 a merge of unrelated histories (merge --allow-unrelated-histories) stopped on an add/add conflict: a clean
    # resolution passes and the markers staged are a finding. Its fix reverted (the recompute refusing unrelated
    # histories) makes even the clean resolution cannot-evaluate, a commit no resolution can make.
    v16 = build("V16", lambda: _fixture_unrelated(temp, "v16"))
    if v16 is not None:
        _fixture_git(v16, "add", "f.txt")
        got = _scan(v16)
        check("unrelated/markers-staged-finding", got == 1, got)
        _stage(v16, "f.txt", clean)
        got = _scan(v16)
        check("unrelated/clean-resolution-passes", got == 0, got)
        with _replaced("MERGE_TREE_OWN_BASE", ()):
            got = _scan(v16)
        check("flip/unrelated-histories-refused-cannot-evaluate", got == 2, got)
    # V17 a revert run with --no-commit leaves REVERT_HEAD, so it is judged: the markers staged are a finding.
    v17 = build("V17", lambda: _fixture_conflict(temp, "v17", "revert-no-commit"))
    if v17 is not None:
        present = (_gitdir(v17) / "REVERT_HEAD").is_file()
        _fixture_git(v17, "add", "f.txt")
        got = _scan(v17)
        check("revert/no-commit-markers-staged-finding", got == 1 and present, (got, present))
    # V18 a conflicted path's staged content moved during the resolution: git add then git mv f.txt g.txt, or the
    # conflicted path removed and its markers added at a new path. The conflicted path is absent from the staged
    # tree, so the added path is judged: a finding. Its fix reverted (no added path judged) passes both; a clean
    # move passes.
    v18a = build("V18a", lambda: _fixture_conflict(temp, "v18a"))
    if v18a is not None:
        _fixture_git(v18a, "add", "f.txt")
        _fixture_git(v18a, "mv", "f.txt", "g.txt")
        got = _scan(v18a)
        check("moved/git-mv-markers-finding", got == 1, got)
        with _replaced("_added_paths", lambda root, entries, sides: set()):
            got = _scan(v18a)
        check("flip/added-paths-unjudged-passes", got == 0, got)
    v18b = build("V18b", lambda: _fixture_conflict(temp, "v18b"))
    if v18b is not None:
        _fixture_git(v18b, "rm", "-q", "f.txt")
        _stage(v18b, "moved/g.txt", marked)
        got = _scan(v18b)
        check("moved/delete-and-add-markers-finding", got == 1, got)
    v18c = build("V18c", lambda: _fixture_conflict(temp, "v18c"))
    if v18c is not None:
        _stage(v18c, "f.txt", clean)
        _fixture_git(v18c, "mv", "f.txt", "g.txt")
        got = _scan(v18c)
        check("moved/clean-move-passes", got == 0, got)
    # V19 a rebase stopped on a conflict and finished with a plain git commit: git records REBASE_HEAD (and, for
    # the apply backend, no MERGE_MSG). The markers staged are a finding and a clean resolution passes. Their fixes
    # reverted (REBASE_HEAD unread; MERGE_MSG required for a rebase) pass the finding (the merge backend's MERGE_MSG
    # list unread as well) or refuse the clean resolution; the edit stop, where REBASE_HEAD is present with no
    # conflict and no MERGE_MSG, passes.
    v19 = build("V19", lambda: _fixture_conflict(temp, "v19", "rebase"))
    if v19 is not None:
        present = (_gitdir(v19) / "REBASE_HEAD").is_file()
        _fixture_git(v19, "add", "f.txt")
        got = _scan(v19)
        check("rebase/markers-staged-finding", got == 1 and present, (got, present))
        with _replaced("STATE_FILES", STATE_FILES[:3]), _replaced("_listed_without_state", lambda gitdir: set()):
            got = _scan(v19)
        check("flip/rebase-head-unread-passes", got == 0, got)
        _stage(v19, "f.txt", clean)
        got = _scan(v19)
        check("rebase/clean-resolution-passes", got == 0, got)
    v19b = build("V19b", lambda: _fixture_conflict(temp, "v19b", "rebase-apply"))
    if v19b is not None:
        absent = not (_gitdir(v19b) / "MERGE_MSG").exists()
        _fixture_git(v19b, "add", "f.txt")
        got = _scan(v19b)
        check("rebase/apply-backend-markers-staged-finding", got == 1 and absent, (got, absent))
        with _replaced("STATE_FILES", STATE_FILES[:3]):
            got = _scan(v19b)
        check("flip/rebase-head-unread-apply-backend-passes", got == 0, got)
        _stage(v19b, "f.txt", clean)
        got = _scan(v19b)
        check("rebase/apply-backend-clean-resolution-passes", got == 0, got)
        with _replaced("MERGE_MSG_OPTIONAL", ()):
            got = _scan(v19b)
        check("flip/rebase-merge-msg-required-cannot-evaluate", got == 2, got)
    v19c = build("V19c", lambda: _fixture_edit_stop(temp, "v19c"))
    if v19c is not None:
        present = (_gitdir(v19c) / "REBASE_HEAD").is_file()
        _stage(v19c, "f.txt", b"a\nEDITED AGAIN\nc\n")
        got = _scan(v19c)
        check("rebase/edit-stop-passes", got == 0 and present, (got, present))
        with _replaced("MERGE_MSG_OPTIONAL", ()):
            got = _scan(v19c)
        check("flip/edit-stop-merge-msg-required-cannot-evaluate", got == 2, got)
    # V20 a cherry-pick --no-commit and a merge --squash stopped on conflicts leave no state file, but MERGE_MSG
    # lists the conflicts, so those paths are judged: the markers staged are a finding, a clean resolution passes.
    # Its fix reverted (MERGE_MSG unread without a state file) passes the finding.
    v20 = build("V20", lambda: _fixture_conflict(temp, "v20", "cherry-pick-no-commit"))
    if v20 is not None:
        stateless = not any((_gitdir(v20) / name).exists() for name, _ in STATE_FILES)
        _fixture_git(v20, "add", "f.txt")
        got = _scan(v20)
        check("merge-msg-only/cherry-pick-no-commit-markers-finding", got == 1 and stateless, (got, stateless))
        with _replaced("_listed_without_state", lambda gitdir: set()):
            got = _scan(v20)
        check("flip/merge-msg-unread-without-state-passes", got == 0, got)
        _stage(v20, "f.txt", clean)
        got = _scan(v20)
        check("merge-msg-only/cherry-pick-no-commit-clean-passes", got == 0, got)
    v20b = build("V20b", lambda: _fixture_conflict(temp, "v20b", "merge-squash"))
    if v20b is not None:
        _fixture_git(v20b, "add", "f.txt")
        got = _scan(v20b)
        check("merge-msg-only/merge-squash-markers-finding", got == 1, got)
    # V21 a merge driver configured for the conflicted path after the merge stopped: the recompute must not run
    # it. Reaching it counts the path as a conflict, so a clean resolution passes and the markers are a finding,
    # with its sentinel unwritten either way; a driver configured only for a path the recompute does not merge
    # leaves a clean resolution passing. Its fix reverted (the driver left configured) runs the driver, which writes
    # its sentinel and, exiting 0, merges the path cleanly.
    v21 = build("V21", lambda: _fixture_conflict(temp, "v21"))
    if v21 is not None:
        _stage(v21, "f.txt", clean)
        sentinel = temp / "v21-driver-ran"
        driver = "printf ran > {}; exit 0".format(shlex.quote(str(sentinel)))
        _fixture_git(v21, "config", "merge.selftest.driver", driver)
        attributes = _gitdir(v21) / "info" / "attributes"
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_bytes(b"quoted.md merge=selftest\n")
        got = _scan(v21)
        check("driver/unreached-driver-clean-passes", got == 0 and not sentinel.exists(), (got, sentinel.exists()))
        attributes.write_bytes(b"f.txt merge=selftest\n")
        got = _scan(v21)
        check("driver/reached-driver-not-run-clean-passes", got == 0 and not sentinel.exists(),
              (got, sentinel.exists()))
        _stage(v21, "f.txt", marked)
        got = _scan(v21)
        check("driver/reached-driver-not-run-markers-finding", got == 1 and not sentinel.exists(),
              (got, sentinel.exists()))
        _stage(v21, "f.txt", clean)
        with _replaced("_driver_overrides", lambda root: ()):
            got = _scan(v21)
        check("flip/driver-left-configured-runs", got == 0 and sentinel.exists(), (got, sentinel.exists()))
    # V22 a rebase stopped on a conflict and ended with git rebase --quit leaves REBASE_HEAD with no rebase in
    # progress. While the MERGE_MSG it leaves lists the conflict, markers kept are still a finding; once that is
    # gone (reset --hard), a later commit is not judged against the stale commit, and a stale REBASE_HEAD naming no
    # commit is not cannot-evaluate. Its fix reverted (REBASE_HEAD read alone) refuses both.
    v22 = build("V22", lambda: _fixture_conflict(temp, "v22", "rebase"))
    if v22 is not None:
        _fixture_git(v22, "rebase", "--quit")
        stale = (_gitdir(v22) / "REBASE_HEAD").is_file() and not (_gitdir(v22) / "rebase-merge").exists()
        _fixture_git(v22, "add", "f.txt")
        got = _scan(v22)
        check("rebase/quit-merge-msg-listed-markers-finding", got == 1 and stale, (got, stale))
        _fixture_git(v22, "reset", "-q", "--hard", "main")
        _stage(v22, "f.txt", b"Title\n=======\nbody\n")
        got = _scan(v22)
        check("rebase/quit-stale-rebase-head-not-judged", got == 0, got)
        with _replaced("_rebase_in_progress", lambda gitdir: True):
            got = _scan(v22)
        check("flip/stale-rebase-head-judged-refuses", got == 1, got)
        (_gitdir(v22) / "REBASE_HEAD").write_bytes(b"e" * 40 + b"\n")
        got = _scan(v22)
        check("rebase/quit-stale-rebase-head-pruned-passes", got == 0, got)
        with _replaced("_rebase_in_progress", lambda gitdir: True):
            got = _scan(v22)
        check("flip/stale-rebase-head-pruned-cannot-evaluate", got == 2, got)
    # V15 the command line: the staged-scan mode reaches the verdict, a malformed argument list is a usage error
    # (exit 2), never a scan, and --staged-scan without --index while GIT_INDEX_FILE is set is exit 2 (the hook's
    # index would go unread). Its fix reverted reaches the scan of the repository's own index.
    v15 = v1 if v1 is not None else build("V15", lambda: _fixture_conflict(temp, "v15"))
    with _environ("GIT_INDEX_FILE", None):
        for name, argv, expected in (
                ("cli/staged-scan-reaches-verdict", ["--staged-scan", str(v1)], 1),
                ("cli/index-reaches-verdict",
                 ["--staged-scan", str(v3), "--index", str(temp / "v3" / ".git" / "index")], 0),
                ("cli/no-argument", [], 2),
                ("cli/staged-scan-without-root", ["--staged-scan"], 2),
                ("cli/index-without-file", ["--staged-scan", str(temp), "--index"], 2),
                ("cli/unknown-option", ["--staged-scan", str(temp), "--cached", "x"], 2),
                ("cli/self-test-with-operand", ["--self-test", "x"], 2),
                ("cli/relative-execution-report", ["--execution-report", "relative/report"], 2),
        ):
            got = _main_exit(argv)
            check(name, got == expected, "expected exit {}, got {}".format(expected, got))
    if v15 is not None:
        if v15 is not v1:
            _fixture_git(v15, "add", "f.txt")
        with _environ("GIT_INDEX_FILE", str(_gitdir(v15) / "index")):
            got = _main_exit(["--staged-scan", str(v15)])
            check("cli/git-index-file-without-index-cannot-evaluate", got == 2, got)
            with _replaced("_unpassed_index", lambda: None):
                got = _main_exit(["--staged-scan", str(v15)])
            check("flip/git-index-file-unchecked-reaches-scan", got == 1, got)


def self_test():
    try:
        temp = Path(tempfile.mkdtemp(prefix="aiqt-merge-markers-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: cannot create fixture root: {}".format(exc), file=sys.stderr)
        return 2
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _git_fixture_env import fixture_git_lifecycle
        with fixture_git_lifecycle():
            _self_test_isolated(temp)
    except Exception as exc:  # noqa: BLE001  a setup failure is a failed self-test, never a pass
        FAILURES.append("NOT RUN: self-test setup: {}: {}".format(type(exc).__name__, exc))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    for failure in FAILURES:
        print("FAIL: " + failure)
    if FAILURES:
        print("merge-markers self-test: FAILED ({} failure(s), {} case(s) run)".format(len(FAILURES), len(EXECUTED)))
        return 1
    print("merge-markers self-test: PASS ({} cases)".format(len(EXECUTED)))
    return 0


USAGE = ("usage: check_merge_markers.py --staged-scan ROOT [--index FILE]\n"
         "       check_merge_markers.py --self-test\n"
         "       check_merge_markers.py [--self-test] --execution-report ABS_PATH\n")


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the self-test
        # armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in that
    # trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return self_test()
    if len(argv) in (2, 4) and argv[0] == "--staged-scan" and argv[1]:
        if len(argv) == 2:
            refusal = _unpassed_index()
            if refusal is not None:
                print("merge-markers: cannot evaluate: {}".format(refusal), file=sys.stderr)
                return 2
            return staged_scan(argv[1])
        if argv[2] == "--index" and argv[3]:
            return staged_scan(argv[1], argv[3])
    sys.stderr.write(USAGE)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate starts git and reads its exit
# status (subprocess), so under an ignored SIGCHLD a failing git would read as a pass; the guard runs at
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
    sys.exit(_child_status_refusal_exit("check_merge_markers.py") or main())
