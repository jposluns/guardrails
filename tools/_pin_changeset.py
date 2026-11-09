"""Hardened, read-only git reads for the changed-file set of a pinned commit.

A private helper for tools/check_brief_capability.py (preflight rule P7), modelled on
tools/check_release_cut.py's git_environment, git and commit_header. Every inherited GIT_
variable is dropped. Replace refs are the one defence set in both places (the environment's
GIT_NO_REPLACE_OBJECTS and the --no-replace-objects flag); the commit graph, fsmonitor and
automatic maintenance (core.commitGraph, core.fsmonitor, gc.auto, gc.autoDetach and
maintenance.auto) are set only on the command line. The environment alone pins the system and
global configuration to os.devnull, names an empty directory as HOME and XDG_CONFIG_HOME,
disables lazy fetch and grafts, and sets GIT_CEILING_DIRECTORIES to the parent of the directory
read, so a -C on a directory that is not itself a repository fails instead of walking up into an
enclosing one. Caller environment and host configuration therefore cannot steer a read; the
repository's own config is still read, and a malformed one fails the read. Parents are taken
from the raw commit bytes (cat-file commit), never from a revision walk, and the changed set
from diff-tree over resolved object ids, never from the working tree or the index. Every
failure raises ChangesetError, which the caller maps to its own cannot-evaluate outcome, never
to an empty set or a fallback guess; an empty diff is a valid empty set. GIT_GRAFT_FILE is
pinned to os.devnull as defence in depth only: the parents are read from the raw object bytes,
which a grafts entry does not alter, and no fixture demonstrates a grafts entry changing these
reads (untested hardening). Setting it makes git print a grafts deprecation hint, so an error
message gives git's own error lines first and its hint: lines after them (error_text).
"""

import atexit
import os
import shutil
import subprocess
import tempfile

GIT_TIMEOUT = 60
HEX_DIGITS = "0123456789abcdef"
# the hardening every invocation carries on the command line; only --no-replace-objects is also
# set in the environment (GIT_NO_REPLACE_OBJECTS), and every -c pin here is set on the command line
# alone, so dropping one of them does weaken a read; the gc.auto, gc.autoDetach and
# maintenance.auto pins keep a read from ever spawning a detached maintenance child
HARDEN_FLAGS = ("--no-replace-objects", "-c", "core.commitGraph=false", "-c", "core.fsmonitor=false",
                "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false")
# the one reading of the changed set: recursive, NUL-delimited names over immutable ids, no rename
# detection, so a rename contributes both its old and its new path
DIFF_FLAGS = ("diff-tree", "-r", "-z", "--name-only", "--no-renames")


class ChangesetError(Exception):
    """A read that could not be completed; the caller never turns one into an empty changed set."""


_HOME = None


def _empty_home():
    """A process-lifetime empty directory serving as HOME and XDG_CONFIG_HOME for these reads,
    created lazily and removed at interpreter exit."""
    global _HOME
    if _HOME is None or not os.path.isdir(_HOME):
        _HOME = tempfile.mkdtemp(prefix="aiqt-pin-changeset-home-")
        atexit.register(shutil.rmtree, _HOME, ignore_errors=True)
    return _HOME


def git_environment(root=None):
    """The caller's environment with every GIT_-prefixed variable dropped, then made hermetic and
    hardened: no replace objects, no lazy fetch, no grafts file, system and global config pinned to
    os.devnull, HOME and XDG_CONFIG_HOME an empty directory, no prompt, no optional locks, C locale.
    For a `root`, GIT_CEILING_DIRECTORIES names its resolved parent, so discovery examines `root`
    itself and never walks up into an enclosing repository."""
    env = dict((key, value) for key, value in os.environ.items() if not key.startswith("GIT_"))
    home = _empty_home()
    if root is not None:
        env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(str(root)))
    env.update({
        "HOME": home, "XDG_CONFIG_HOME": home,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_GRAFT_FILE": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C",
    })
    return env


def error_text(stderr):
    """git's stderr as one message, every `hint:` line moved after the other lines, so an advisory
    hint (the grafts deprecation one that GIT_GRAFT_FILE triggers) never leads the real error; no
    line is dropped."""
    lines = stderr.decode("utf-8", "replace").strip().splitlines()
    hints = [line for line in lines if line.startswith("hint:")]
    return "\n".join([line for line in lines if not line.startswith("hint:")] + hints)


def git(root, *args, stdin=b""):
    """One hardened git invocation in `root`, output captured; any failure is ChangesetError."""
    try:
        result = subprocess.run(
            ["git", *HARDEN_FLAGS, "-C", str(root), *args],
            capture_output=True, env=git_environment(root), timeout=GIT_TIMEOUT, input=stdin,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChangesetError("git invocation: " + str(exc)) from exc
    if result.returncode != 0:
        raise ChangesetError("git {}: {}".format(args[0], error_text(result.stderr)))
    return result.stdout


def _one_line(raw, label):
    """The one output line of a read, decoded; anything else is ChangesetError."""
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ChangesetError(label + ": expected one output line")
    try:
        value = raw[:-1].decode("utf-8")
    except UnicodeError as exc:
        raise ChangesetError(label + ": non-UTF-8 output") from exc
    if not value:
        raise ChangesetError(label + ": empty output")
    return value


def _oid(value, label):
    """`value` as a full object id: 40 or 64 lower-case hex digits, read without a pattern."""
    if len(value) not in (40, 64) or not all(character in HEX_DIGITS for character in value):
        raise ChangesetError(label + ": invalid object identity")
    return value


def resolve(root, pin):
    """The full commit id `pin` names in `root`; an unknown or ambiguous pin is ChangesetError."""
    return _oid(_one_line(git(root, "rev-parse", "--verify", "--end-of-options",
                              pin + "^{commit}"), "resolve " + pin), "resolve " + pin)


def commit_header(root, revision):
    """(tree, parents) of a commit, parsed from its raw object bytes (cat-file commit), each named
    object's type verified; never a revision walk, so replace refs and grafts have nothing to alter."""
    raw = git(root, "cat-file", "commit", revision)
    if b"\n\n" not in raw:
        raise ChangesetError(revision + ": malformed commit")
    headers = raw.split(b"\n\n", 1)[0].splitlines()
    if not headers or not headers[0].startswith(b"tree "):
        raise ChangesetError(revision + ": missing commit tree")
    try:
        tree = _oid(headers[0][5:].decode("ascii"), "commit tree")
    except UnicodeError as exc:
        raise ChangesetError(revision + ": malformed commit tree line") from exc
    if git(root, "cat-file", "-t", tree) != b"tree\n":
        raise ChangesetError(revision + ": missing commit tree object")
    parents = []
    for line in headers:
        if line.startswith(b"parent "):
            try:
                parent = _oid(line[7:].decode("ascii"), "commit parent")
            except UnicodeError as exc:
                raise ChangesetError(revision + ": malformed parent line") from exc
            if git(root, "cat-file", "-t", parent) != b"commit\n":
                raise ChangesetError(revision + ": a named parent commit object is absent "
                                     "(a shallow or partial clone cannot be read as a root commit)")
            parents.append(parent)
    return tree, parents


def empty_tree(root):
    """The empty tree's id in `root`'s object format, computed read-only (no -w: nothing is written),
    so a root commit diffs correctly in SHA-256 repositories too."""
    return _oid(_one_line(git(root, "hash-object", "-t", "tree", "--stdin", stdin=b""),
                          "empty tree"), "empty tree")


def base_oid(root, parents):
    """The diff base of a commit with these parents: the first parent, or the empty tree for a root."""
    return parents[0] if parents else empty_tree(root)


def _nul_paths(raw, label):
    """The paths of NUL-terminated -z output; malformed output is ChangesetError, never an empty set."""
    if not raw:
        return frozenset()
    if not raw.endswith(b"\0"):
        raise ChangesetError(label + ": missing NUL terminator")
    names = []
    for record in raw[:-1].split(b"\0"):
        if not record:
            raise ChangesetError(label + ": empty path record")
        try:
            names.append(record.decode("utf-8"))
        except UnicodeError as exc:
            raise ChangesetError(label + ": non-UTF-8 path") from exc
    return frozenset(names)


def changed_set(root, pin):
    """(base, head, frozenset of paths) the pinned commit changed against its first parent (the empty
    tree for a root commit), read with diff-tree over resolved ids: additions, deletions, type changes
    and gitlink changes included, a rename as both its paths. Malformed output is ChangesetError,
    never an empty set; an empty diff is a valid empty set."""
    head = resolve(root, pin)
    _, parents = commit_header(root, head)
    base = base_oid(root, parents)
    return base, head, _nul_paths(git(root, *DIFF_FLAGS, base, head), "diff-tree")
