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

WHEN IT ACTS. It acts only while git records an operation in progress in the working tree's git directory
(git rev-parse --absolute-git-dir): MERGE_HEAD (a merge, one line per merged commit), CHERRY_PICK_HEAD (a cherry-pick)
or REVERT_HEAD (a revert). With none of them it prints a NOTE and exits 0. Each state file present must be a regular
file whose every line is one full commit id (40 or 64 lowercase hex) that names a commit; otherwise the run cannot
evaluate (exit 2). MERGE_MSG must then be a readable regular file as well.

SCOPE. The paths judged are the union of two sources, so marker text quoted in any other path never fires:
  1. a recompute from the commits, never from the index (staging a resolution removes its unmerged entries, so the
     index cannot say what conflicted): git merge-tree --write-tree --name-only --no-messages -z, for a merge HEAD
     against each MERGE_HEAD commit with --allow-unrelated-histories (a merge state file means git already ran that
     merge, so a merge of unrelated histories is recomputed rather than refused); for a cherry-pick of C, HEAD
     against C with --merge-base set to each parent of C (the empty tree for a root commit); for a revert of C, HEAD
     against each parent of C with --merge-base=C (for a revert of a root commit, which merge-tree cannot recompute,
     every path of C's tree);
  2. the Conflicts list git writes into MERGE_MSG for the operation that actually ran (a "Conflicts:" line behind the
     comment prefix, then one prefixed, tab-indented path per line; the legacy form with no prefix is read too). It
     covers a conflict the recompute does not reproduce, such as one under a strategy option or a configuration the
     recompute does not share.
A merge-tree that cannot run, exits other than 0 or 1, or prints output that does not parse is cannot-evaluate. The
recompute runs under the repository's own configuration and attributes, so a custom merge driver configured for a
scoped path (merge.<name>.driver) runs again on each check, as it ran during the operation.

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
named only by GIT_INDEX_FILE, so a hook that omits --index judges the wrong index. The git binary is resolved through
PATH (a trusted-toolchain concern). Installing it: a pre-commit hook running
  python3 -I -B tools/check_merge_markers.py --staged-scan "$(git rev-parse --show-toplevel)" --index "$GIT_INDEX_FILE"
This pack ships no installer for it.

OVER-REJECTION, DISCLOSED. A conflicted path whose resolution legitimately holds a marker-shaped line (a document
that quotes git's markers) is a finding. The remedy is git's own: set the conflict-marker-size attribute for that
path in .gitattributes (git then writes markers of that size, and lines of another length are content), never a
bypass of the commit-time check.

RESIDUALS (not caught): a rebase and git am, whether finished with rebase --continue (git runs no pre-commit hook
there) or with a plain git commit while the rebase is stopped (the hook runs, but git records REBASE_HEAD, not one of
the three state files read here); a cherry-pick run with --no-commit, a conflicted merge --squash (no MERGE_HEAD), and
a merge ended with merge --quit, since each leaves no state file (a revert run with --no-commit leaves REVERT_HEAD,
as git 2.53 does, and is judged); marker text in a path that did not conflict, including a conflicted path's content
moved during the resolution to a new path (git mv, or a delete and an add), since the conflicted path is then absent
and the new path is outside the scope; a conflict neither the recompute nor the MERGE_MSG list names (the user can
edit MERGE_MSG, and the recompute uses the current configuration and attributes); an octopus merge, recomputed
pairwise against HEAD; a conflict-marker-size attribute changed in the staged .gitattributes to make the markers
git wrote count as content; a marker line in a file the merge treated as binary is judged like any other; a commit
made in a clone without the hook, or with git commit --no-verify by a person (the pack's gate-weakening hook refuses
--no-verify for the assistant); and every commit already in history (no CI range mode ships: the CI roster runs this
gate's self-test only).

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
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 30
DEFAULT_MARKER_SIZE = 7
MARKER_CHARS = b"<|=>"
# The state files of an operation stopped on conflicts, in the order they are read.
STATE_FILES = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"))
_OID = re.compile(rb"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ATOI = re.compile(rb"\A[ \t\n\v\f\r]*\+?([0-9]+)")
_WHITESPACE = b" \t\r\n\v\f"
# The merge-tree options of a recompute that computes its own merge base (a merge): git already ran the merge whose
# state file is present, so a merge of unrelated histories (merge --allow-unrelated-histories) is recomputed too.
MERGE_TREE_OWN_BASE = ("--allow-unrelated-histories",)


class CannotEvaluate(RuntimeError):
    """A required repository input or git result could not answer the question."""


def _git_env(index=None):
    """The caller's environment with every GIT_-prefixed variable dropped and an offline, non-interactive,
    replace-free posture asserted; GIT_INDEX_FILE is set only to an index this run has validated."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


def _git(root, *args, index=None, input_bytes=None):
    """One read-only git call bound to root; stdout and stderr are bytes."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], input=input_bytes, capture_output=True,
                              timeout=TIMEOUT, env=_git_env(index))
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


def _read_state(root, gitdir):
    """[(operation, [commit id, ...])] for each state file present, validated."""
    state = []
    for name, operation in STATE_FILES:
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


def _merge_tree(root, ours, theirs, base=None):
    """The conflicted paths of one recomputed merge."""
    args = ["merge-tree", "--write-tree", "--name-only", "--no-messages", "-z"]
    if base is not None:
        args.append("--merge-base=" + os.fsdecode(base))
    else:
        args += list(MERGE_TREE_OWN_BASE)
    args += [os.fsdecode(ours), os.fsdecode(theirs)]
    probe = _git(root, *args)
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
    for operation, oids in state:
        if operation == "merge":
            for oid in oids:
                paths |= _merge_tree(root, head, oid)
            continue
        oid = oids[0]
        parents = _parents(root, oid)
        if operation == "cherry-pick":
            for base in parents or [_empty_tree(root)]:
                paths |= _merge_tree(root, head, oid, base)
        elif parents:
            for parent in parents:
                paths |= _merge_tree(root, head, parent, oid)
        else:
            listing = _git_ok(root, "ls-tree", "-r", "-z", "--name-only", os.fsdecode(oid))
            paths |= {os.fsdecode(name) for name in listing.split(b"\0") if name}
    return paths


def _merge_msg_conflicts(gitdir):
    """The paths of every Conflicts list in MERGE_MSG."""
    raw = _read_regular(gitdir / "MERGE_MSG", "MERGE_MSG")
    if raw is None:
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
        if not state:
            print("merge-markers: NOTE no merge, cherry-pick or revert is in progress; nothing to judge", file=out)
            return 0
        head = _one_line(_git_ok(root, "rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"), "HEAD")
        scope = _recompute(root, head, state) | _merge_msg_conflicts(gitdir)
        entries = _staged_entries(root, index)
        judged = sorted(path for path in scope if path in entries)
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
    operations = ", ".join(sorted({operation for operation, _ in state}))
    for path, number in findings:
        print("merge-markers: FINDING {}:{}: a conflict-marker line in a path the {} in progress left unmerged".format(
            path, number, operations), file=out)
    if findings:
        print("merge-markers: resolve the conflict in each named path (or set its conflict-marker-size attribute "
              "if the file quotes markers), stage it, and commit again", file=out)
        return 1
    print("merge-markers: OK {} scoped path(s) of the {} in progress hold no conflict-marker line".format(
        len(judged), operations), file=out)
    return 0


# ---------------------------------------------------------------- self-test


def _fixture_git(root, *args, check=True, input_bytes=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                          GIT_AUTHOR_NAME="Merge Markers Self Test", GIT_AUTHOR_EMAIL="merge-markers@example.invalid",
                          GIT_COMMITTER_NAME="Merge Markers Self Test",
                          GIT_COMMITTER_EMAIL="merge-markers@example.invalid")
    probe = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                            "-c", "rerere.enabled=false", *args],
                           input=input_bytes, capture_output=True, timeout=TIMEOUT, env=env)
    if check and probe.returncode != 0:
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
        probe = _fixture_git(root, *style_args, "merge", "side", check=False)
    elif operation == "cherry-pick":
        probe = _fixture_git(root, *style_args, "cherry-pick", "side", check=False)
    else:
        # Revert the main commit after a later commit rewrote the same line, so the revert conflicts with it.
        (root / "f.txt").write_bytes(theirs)
        _fixture_git(root, "commit", "-q", "-a", "-m", "later")
        mode = ("--no-commit",) if operation == "revert-no-commit" else ("--no-edit",)
        probe = _fixture_git(root, *style_args, "revert", *mode, "HEAD~1", check=False)
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
    probe = _fixture_git(root, "merge", "--allow-unrelated-histories", "other", check=False)
    if probe.returncode != 1:
        raise RuntimeError("fixture {}: the unrelated-histories merge did not stop on a conflict (exit {})".format(
            name, probe.returncode))
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


def _verdict(root, index=None):
    sink = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        return staged_scan(str(root), index, out=sink)


def _self_test_isolated(temp):
    failures = []
    ran = []

    def case(label, expected, root, index=None):
        ran.append(label)
        try:
            got = _verdict(root, index)
        except Exception as exc:  # noqa: BLE001  a crash is a named failure, never a pass
            failures.append("{}: raised {}: {}".format(label, type(exc).__name__, exc))
            return
        if got != expected:
            failures.append("{}: expected exit {}, got {}".format(label, expected, got))

    def build(label, builder):
        try:
            return builder()
        except Exception as exc:  # noqa: BLE001  a fixture that cannot be built is a named NOT RUN failure
            failures.append("NOT RUN: {}: {}".format(label, exc))
            return None

    clean = b"a\nRESOLVED\nc\n"
    # V1 a merge stopped on a conflict, the markers staged as they are: a finding. Its fix reverted (the marker
    # predicate answering never) passes it.
    v1 = build("V1", lambda: _fixture_conflict(temp, "v1"))
    if v1 is not None:
        _fixture_git(v1, "add", "f.txt")
        case("V1 merge markers staged", 1, v1)
        with _replaced("is_marker_line", lambda line, size=DEFAULT_MARKER_SIZE: False):
            case("V1-revert marker predicate off passes", 0, v1)
    # V2 the same marker text in a path the merge did not conflict (git add -A stages it): no finding. Its fix
    # reverted (every staged path in scope) refuses it.
    v2 = build("V2", lambda: _fixture_conflict(temp, "v2"))
    if v2 is not None:
        (v2 / "f.txt").write_bytes(clean)
        (v2 / "notes.md").write_bytes(b"<<<<<<< HEAD\n=======\n>>>>>>> side\n")
        _fixture_git(v2, "add", "-A")
        case("V2 marker text in an unconflicted path", 0, v2)
        with _replaced("_recompute", lambda root, head, state: set(_staged_entries(root, None))):
            case("V2-revert unscoped refuses", 1, v2)
    # V3 a clean resolution: no finding.
    v3 = build("V3", lambda: _fixture_conflict(temp, "v3"))
    if v3 is not None:
        _stage(v3, "f.txt", clean)
        case("V3 clean resolution", 0, v3)
    # V4 merge state that cannot be read: cannot evaluate. Each of its fixes reverted (the state reader returning
    # nothing) passes it.
    for label, spoil in (
            ("V4a MERGE_HEAD is a directory", lambda gd: ((gd / "MERGE_HEAD").unlink(), (gd / "MERGE_HEAD").mkdir())),
            ("V4b MERGE_HEAD is empty", lambda gd: (gd / "MERGE_HEAD").write_bytes(b"")),
            ("V4c MERGE_HEAD is not a commit id", lambda gd: (gd / "MERGE_HEAD").write_bytes(b"side\n")),
            ("V4d MERGE_HEAD names a blob", None),
            ("V4e MERGE_MSG is absent", lambda gd: (gd / "MERGE_MSG").unlink()),
            ("V4f MERGE_MSG is a directory", lambda gd: ((gd / "MERGE_MSG").unlink(), (gd / "MERGE_MSG").mkdir())),
            ("V4g MERGE_HEAD is a symbolic link", lambda gd: (
                (gd / "MERGE_HEAD").rename(gd / "MERGE_HEAD.real"), (gd / "MERGE_HEAD").symlink_to("MERGE_HEAD.real"))),
    ):
        slug = "v4" + label[2]
        root = build(label, lambda slug=slug: _fixture_conflict(temp, slug))
        if root is None:
            continue
        _stage(root, "f.txt", clean)
        gd = _gitdir(root)
        if spoil is None:
            blob = _fixture_git(root, "hash-object", "-w", "--stdin", input_bytes=b"x\n").stdout.strip()
            (gd / "MERGE_HEAD").write_bytes(blob + b"\n")
        else:
            spoil(gd)
        case(label, 2, root)
    v4h = build("V4h", lambda: _fixture_conflict(temp, "v4h"))
    if v4h is not None:
        _stage(v4h, "f.txt", clean)

        def failing(*args, **kwargs):
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: injected")

        def garbage(*args, **kwargs):
            return subprocess.CompletedProcess(args, 1, b"not a tree id\0f.txt\0", b"")
        for label, fake in (("V4h merge-tree fails", failing), ("V4i merge-tree prints garbage", garbage)):
            real_git = _git

            def routed(root, *args, fake=fake, real_git=real_git, **kwargs):
                if args and args[0] == "merge-tree":
                    return fake(root, *args, **kwargs)
                return real_git(root, *args, **kwargs)
            with _replaced("_git", routed):
                case(label, 2, v4h)
        _gd = _gitdir(v4h)
        (_gd / "MERGE_HEAD").write_bytes(b"")
        with _replaced("_read_state", lambda root, gitdir: []):
            case("V4-revert state reader swallowing passes", 0, v4h)
    # V5 a conflicted path that quotes markers on both sides. With no attribute, keeping the quotation is a finding
    # (the disclosed over-rejection); with conflict-marker-size=9 git writes 9-character markers, so keeping the
    # 7-character quotation passes and leaving git's markers is a finding.
    quote = b"<<<<<<< ours\n=======\n>>>>>>> theirs\n"
    v5a = build("V5a", lambda: _fixture_conflict(temp, "v5a", ours=quote + b"OURS\n", theirs=quote + b"THEIRS\n",
                                                 base=quote + b"b\n"))
    if v5a is not None:
        _stage(v5a, "f.txt", quote + b"RESOLVED\n")
        case("V5a quoted markers kept, no attribute", 1, v5a)
    attrs = b"f.txt conflict-marker-size=9\n"
    v5b = build("V5b", lambda: _fixture_conflict(temp, "v5b", ours=quote + b"OURS\n", theirs=quote + b"THEIRS\n",
                                                 base=quote + b"b\n", attributes=attrs))
    if v5b is not None:
        written = (v5b / "f.txt").read_bytes()
        if b"<<<<<<<<< " not in written:
            failures.append("V5b: git did not write 9-character markers")
        _fixture_git(v5b, "add", "f.txt")
        case("V5b git's 9-character markers staged", 1, v5b)
        _stage(v5b, "f.txt", quote + b"RESOLVED\n")
        case("V5c quoted 7-character markers kept under size 9", 0, v5b)
        with _replaced("_marker_sizes", lambda root, index, paths: {path: DEFAULT_MARKER_SIZE for path in paths}):
            case("V5c-revert attribute ignored refuses", 1, v5b)
    # V6 a path only one source names. 6a: only MERGE_MSG lists other.txt (its Conflicts list names it); 6b: the
    # Conflicts list was edited away and only the recompute names f.txt. Each source reverted passes its case.
    v6 = build("V6a", lambda: _fixture_conflict(temp, "v6a"))
    if v6 is not None:
        _stage(v6, "f.txt", clean)
        _stage(v6, "other.txt", b"<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> side\n")
        gd = _gitdir(v6)
        (gd / "MERGE_MSG").write_bytes((gd / "MERGE_MSG").read_bytes() + b"#\tother.txt\n")
        case("V6a path only MERGE_MSG lists", 1, v6)
        with _replaced("_merge_msg_conflicts", lambda gitdir: set()):
            case("V6a-revert MERGE_MSG source off passes", 0, v6)
    v6b = build("V6b", lambda: _fixture_conflict(temp, "v6b"))
    if v6b is not None:
        _fixture_git(v6b, "add", "f.txt")
        gd = _gitdir(v6b)
        (gd / "MERGE_MSG").write_bytes(b"Merge branch 'side'\n")
        case("V6b path only the recompute names", 1, v6b)
        with _replaced("_recompute", lambda root, head, state: set()):
            case("V6b-revert recompute off passes", 0, v6b)
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
        (v7 / "f.txt").write_bytes(b"a\n<<<<<<< HEAD\nOURS\n=======\nTHEIRS\n>>>>>>> side\nc\n")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _git_fixture_env import git_fixture_env
        subprocess.run(["git", "-C", str(v7), "add", "f.txt"], capture_output=True, timeout=TIMEOUT, check=True,
                       env=git_fixture_env(GIT_INDEX_FILE=str(temporary)))
        (v7 / "f.txt").write_bytes(conflicted)
        case("V7 commit -a index holds markers", 1, v7, str(temporary))
        case("V7-revert repository index read instead passes", 0, v7)
        outside = temp / "outside-index"
        shutil.copyfile(temporary, outside)
        case("V7b --index outside the git directory", 2, v7, str(outside))
        link = gd / "index-link"
        link.symlink_to(temporary)
        case("V7c --index is a symbolic link", 2, v7, str(link))
    # V8 CRLF line endings: a marker line ending in CR is a finding.
    v8 = build("V8", lambda: _fixture_conflict(temp, "v8"))
    if v8 is not None:
        _stage(v8, "f.txt", b"a\r\n<<<<<<< HEAD\r\nOURS\r\n=======\r\nTHEIRS\r\n>>>>>>> side\r\nc\r\n")
        case("V8 CRLF markers", 1, v8)
    # V9 each marker form alone, the diff3 and zdiff3 base marker included: each is a finding. A line of eight
    # marker characters, or one glued to text, is not a marker of size 7.
    for style in ("diff3", "zdiff3"):
        root = build("V9 " + style, lambda style=style: _fixture_conflict(temp, "v9" + style, style=style))
        if root is None:
            continue
        if b"\n||||||| " not in (root / "f.txt").read_bytes():
            failures.append("V9 {}: git wrote no base marker".format(style))
        _fixture_git(root, "add", "f.txt")
        case("V9 {} markers staged".format(style), 1, root)
        for form in (b"<<<<<<< HEAD", b"||||||| base", b"|||||||", b"=======", b">>>>>>> side", b"======= \t"):
            _stage(root, "f.txt", b"a\nRESOLVED\n" + form + b"\nc\n")
            case("V9 {} lone {!r}".format(style, form), 1, root)
        for text in (b"========", b"<<<<<<<<", b"=======x", b"<<<<<<", b" ======="):
            _stage(root, "f.txt", b"a\nRESOLVED\n" + text + b"\nc\n")
            case("V9 {} not a marker {!r}".format(style, text), 0, root)
    # V10 no operation in progress: staged marker text is not judged.
    v10 = build("V10", lambda: _fixture_conflict(temp, "v10"))
    if v10 is not None:
        _fixture_git(v10, "merge", "--abort")
        _stage(v10, "f.txt", b"<<<<<<< HEAD\n")
        case("V10 no operation in progress", 0, v10)
    # V11 and V12 a cherry-pick and a revert stopped on conflicts: markers staged are a finding, a clean
    # resolution passes. Their fix reverted (reading MERGE_HEAD only) passes the finding.
    for label, operation in (("V11", "cherry-pick"), ("V12", "revert")):
        root = build(label, lambda label=label, operation=operation: _fixture_conflict(temp, label.lower(), operation))
        if root is None:
            continue
        _fixture_git(root, "add", "f.txt")
        case("{} {} markers staged".format(label, operation), 1, root)
        with _replaced("STATE_FILES", (("MERGE_HEAD", "merge"),)):
            case("{}-revert merge state only passes".format(label), 0, root)
        _stage(root, "f.txt", clean)
        case("{} {} clean resolution".format(label, operation), 0, root)
    # V13 a conflicted path resolved to a symbolic link: named on a NOTE line, not scanned.
    v13 = build("V13", lambda: _fixture_conflict(temp, "v13"))
    if v13 is not None:
        (v13 / "f.txt").unlink()
        (v13 / "f.txt").symlink_to("quoted.md")
        _fixture_git(v13, "add", "f.txt")
        case("V13 symbolic-link resolution", 0, v13)
    # V14 a ROOT that is not the top level of a working tree, and an unmerged entry left in the index.
    v14 = build("V14", lambda: _fixture_conflict(temp, "v14"))
    if v14 is not None:
        (v14 / "sub").mkdir()
        case("V14a ROOT below the top level", 2, v14 / "sub")
        case("V14b unmerged entry in the index", 2, v14)
    # V16 a merge of unrelated histories (merge --allow-unrelated-histories) stopped on an add/add conflict: a clean
    # resolution passes and the markers staged are a finding. Its fix reverted (the recompute refusing unrelated
    # histories) makes even the clean resolution cannot-evaluate, a commit no resolution can make.
    v16 = build("V16", lambda: _fixture_unrelated(temp, "v16"))
    if v16 is not None:
        _fixture_git(v16, "add", "f.txt")
        case("V16a unrelated-histories merge markers staged", 1, v16)
        _stage(v16, "f.txt", clean)
        case("V16b unrelated-histories merge clean resolution", 0, v16)
        with _replaced("MERGE_TREE_OWN_BASE", ()):
            case("V16-revert recompute refusing unrelated histories", 2, v16)
    # V17 a revert run with --no-commit leaves REVERT_HEAD, so it is judged: the markers staged are a finding.
    v17 = build("V17", lambda: _fixture_conflict(temp, "v17", "revert-no-commit"))
    if v17 is not None:
        if not (_gitdir(v17) / "REVERT_HEAD").is_file():
            failures.append("V17: revert --no-commit left no REVERT_HEAD")
        _fixture_git(v17, "add", "f.txt")
        case("V17 revert --no-commit markers staged", 1, v17)
    # V15 the command line: the staged-scan mode reaches the verdict, and a malformed argument list is a usage
    # error (exit 2), never a scan.
    for label, argv, expected in (
            ("V15a --staged-scan reaches the verdict", ["--staged-scan", str(v1)] if v1 else None, 1),
            ("V15b --index reaches the verdict", ["--staged-scan", str(v3), "--index", str(_gitdir(v3) / "index")]
             if v3 else None, 0),
            ("V15c no argument", [], 2),
            ("V15d --staged-scan without ROOT", ["--staged-scan"], 2),
            ("V15e --index without FILE", ["--staged-scan", str(temp), "--index"], 2),
            ("V15f unknown option", ["--staged-scan", str(temp), "--cached", "x"], 2),
            ("V15g --self-test with an operand", ["--self-test", "x"], 2),
    ):
        if argv is None:
            failures.append("NOT RUN: {}: its fixture was not built".format(label))
            continue
        ran.append(label)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = main(argv)
        if got != expected:
            failures.append("{}: expected exit {}, got {}".format(label, expected, got))
    return failures, ran


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
            failures, ran = _self_test_isolated(temp)
    except Exception as exc:  # noqa: BLE001  a setup failure is a failed self-test, never a pass
        failures, ran = ["NOT RUN: self-test setup: {}: {}".format(type(exc).__name__, exc)], []
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    for failure in failures:
        print("FAIL: " + failure)
    if failures:
        print("merge-markers self-test: FAILED ({} of {} cases)".format(len(failures), len(ran)))
        return 1
    print("merge-markers self-test: PASS ({} cases)".format(len(ran)))
    return 0


USAGE = ("usage: check_merge_markers.py --staged-scan ROOT [--index FILE]\n"
         "       check_merge_markers.py --self-test\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) in (2, 4) and argv[0] == "--staged-scan" and argv[1]:
        if len(argv) == 2:
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
