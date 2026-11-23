#!/usr/bin/env python3
"""One-sided-merge gate: every merge result in a range must equal git's own re-merge of its recorded parents.

  check_merge_result.py [--repo DIR] [--base REV] [--head REV] [--declarations PATH] [--squash REV]...
                                       exit 0 clean, 1 finding, 2 cannot evaluate
  check_merge_result.py [--repo DIR] [--base REV] [--declarations PATH] --member REV
                                       member mode: the merge lane runs this on each member head against
                                       main before squashing it into a train (--head and --squash refused)
  check_merge_result.py --self-test    fixture repositories in a temporary directory, each rule shown red
                                       with that rule removed

WHICH MERGES IT SEES, BY MODE. A merge is judged only when it is a commit in the range (below) that records
exactly two parents, or a one-parent commit named with --squash or declared in a [[squash]] table. In the
default mode the range is base..HEAD: as wired in CI, on a train pull request that is the train's squash
commits (one parent each, so not judged unless named or declared) and GitHub's test merge of the train into
main; on a push to main the range is empty. Main receives squashed trains, so a member branch's own merges
(a member that merged main with -s ours, then squashed, carries the loss into main as ordinary additions) are
never in that range. Member mode (--member REV) sets the range to base..REV and so judges every two-parent
commit on the member branch that main does not already hold; the merge lane must run it on each member head
before squashing (the lane is outside this repository; nothing here runs member mode, so a lane that skips it
leaves those merges unjudged). Neither mode sees the squash the lane then makes unless it is named or
declared, a merge already reachable from the base, or a commit outside the range.

THE ARGUMENTS. Every option takes exactly one non-empty value: an option given with an empty value (as an
unset variable in the caller gives), with no value, twice, or an unknown argument is a usage error (exit 2),
never the default. An option left out takes its default. --repo must name the top directory of a work tree
(or, for a bare repository, its git directory) exactly: a directory git does not read as a repository, or
one inside a repository but not its top (git -C would silently judge the enclosing repository), is
cannot-evaluate (repository-unreadable).

THE RANGE. --base defaults to the full ref name refs/remotes/origin/main and --head to HEAD; both are read in
the repository (--repo, default this file's repository) with git rev-parse --verify --end-of-options
NAME^{commit}, replacement objects off, and a name that does not resolve to a commit is cannot-evaluate
(range-unreadable), never an empty range. Every revision name (--base, --head, --member, --squash) must name
exactly one object: a name with revision syntax (~, ^, :, @{, .., a glob character, or @ alone) is refused,
and so is a name more than one of git's own lookups would accept (HEAD itself, refs/NAME, refs/tags/NAME,
refs/heads/NAME, refs/remotes/NAME, refs/remotes/NAME/HEAD, and every object whose id starts with NAME when
NAME is 4 or more hex digits), so a tag that shares a branch's name, a ref shaped like an object id, or a
short id two objects share is range-unreadable, never silently read as one of them; a name none of them
accepts (another root ref such as FETCH_HEAD) is range-unreadable too; a name under refs/ must itself be that
one ref; and the commit git resolves must be the commit that one source resolves to, compared by object id
(so a root ref that shadows it is refused). The
commits judged are those reachable from the head commit and not from the base commit, found by a walk this
gate makes itself over raw commit objects (git cat-file commit), so every parent is read from the commit
object's own parent headers, as git reads them (the parent lines directly after the tree line; a parent line
after any other header is not a parent): replacement refs are off (GIT_NO_REPLACE_OBJECTS and a private
repository with no refs/replace), and a grafts file and a commit-graph are never read (the reads run in a
private bare repository under a temporary directory, created in the repository's own object format (SHA-1 or
SHA-256), whose only link to the repository is an objects/info/alternates line, with core.commitGraph false
written into its own configuration, so every git read there, merge-base included, skips the commit-graph of
the repository it borrows objects from). Nothing is written to the repository: merge-tree runs in a second
private bare repository made the same way (the re-merge store) and writes its trees and blobs into that one's
own object directory, removed with it; every source object (each object of the base history, the walk, the
inputs, the merge base and the recorded tree) is read in the first private repository (the source store),
which nothing writes to (the member's declaration path is read in the repository itself), so an object one
merge's re-merge writes never stands in for a missing source object of another merge in the same range. An
empty range (head reachable from base, as on main itself) passes with a NOTE line.

THE COMPARISON. For each commit with exactly two parents P1 and P2 (in recorded order), the gate runs
git merge-tree --write-tree -z --messages P1 P2 and compares the tree it writes with the commit's recorded
tree, entry by entry (git ls-tree -r -t -z --full-tree: mode, object type and object id per path, so a file
mode change, a file turned symlink, a symlink retarget, a binary change and a submodule gitlink change are
each a difference; no content is decoded or diffed). A listing that names the same path twice, or a path
below a directory it does not list as a tree (a tree entry whose name holds a slash), is cannot-evaluate
(malformed-tree), never collapsed to one of its entries. So is every tree read below (each root tree and
subtree of the recorded, input, merge-base and re-merged trees, and each tree on the member's declaration
path, which is declaration-unreadable instead) that is not in git's canonical form: an entry mode other than
exactly 100644, 100755, 120000, 160000 or 40000 (a zero-padded 040000 or a 100664 included), an entry name
that is empty, holds a slash or is . or .., a name stored twice, or entries not strictly increasing in git's
tree order (a subtree's name compared as if it ended in a slash). git reads such a tree inconsistently (a
lookup by name can miss an entry its listing shows, and a later merge into it can drop a side's change
cleanly), so it is never compared, even where a conflicted path would exempt the tree's id. Subtrees are
compared by object id too: a subtree whose id differs from the re-merge's (or that only one side holds)
with no differing file, symlink or gitlink below it (an added or dropped empty subtree, or a tree the
re-merge does not write) is a finding, and so is a recorded tree whose id is not the re-merged tree's when
no entry differs. The comparison below is over the entries that are not trees. Let D be the paths whose entries differ (an entry on
one side only included), C the paths git reports conflicted (every path of a conflict stage entry and every
path of an informational message whose type starts with CONFLICT) and H the hand resolutions declared for
that commit. A path in D and in C passes (a resolved conflict, listed on a NOTE line; its resolution is not
judged). Where a conflict (file/directory, or distinct types) makes git write a side's entry under a new name,
PATH~LABEL (with _N added when that name is taken), the label is the one git gave that side: merge-tree, given
commit ids, names it PATH~ID, the original merge by its own label (HEAD or a branch name). git never picks such
a name where a path already exists in either input tree or the merge base, so only a name git itself could have
generated for this merge's conflict is excused: a path in D, outside C and undeclared, that only the recorded tree
holds, that no input tree, merge-base tree or the re-merged tree holds (a path one of them holds is judged by the
ordinary comparison below), and that is PATH~LABEL (a label with no slash) for the PATH of such a name in C passes
(a NOTE line) when its entry is exactly the re-merge's entry under that name, the recorded tree does not also hold
the re-merge's name, and the pairing is one to one; otherwise it is cannot-evaluate (conflict-name), never a
pass. A path in D, outside C
and declared, passes only when the declaration names exactly the recorded
entry (its mode and object id, or absent = true for a path the result does not hold); any other declared
content is a finding. A path in D, outside C and undeclared, is a finding (exit 1) naming the commit, the
path, the re-merged entry and the recorded entry. A declaration for a commit in the range whose path is not
in D, or is in C, is a stale declaration and a finding, so a declaration cannot silently exempt a later
difference; so is a [[resolution]] or [[squash]] declaration naming a commit the walk reaches but does not
judge (an ordinary one-parent commit, a root commit, or a squash declared for a commit that does not have
exactly one parent). Declarations for commits outside the range are not read beyond their schema.

The recorded tree is checked present and read in full (git ls-tree -r -t) before merge-tree runs, so a missing
recorded tree or subtree is cannot-evaluate even when the re-merge would write the same object. Every object
the comparison relies on must be readable and must be the object its id names. Each object below is checked
present with the type its referrer claims (git cat-file --batch-check, or cat-file TYPE; the type a tree
entry's mode, a root tree or a commit header line names), once for each type it is named as, so an id read
once as one type (a tree, say) never satisfies a later entry that names it as another (a blob), and it is
read in full (git cat-file --batch),
and its content is hashed as git hashes it (type, size and content, SHA-1 or SHA-256 by the repository's
object format) and compared with its id: every commit of the base history (all of git rev-list BASE, read
before the walk, so an ancestor of the base rewritten to list a merge of the range as a parent cannot hide it
in the base set); every commit the walk reads; for each judged merge, both input commits and every commit of
their history (git rev-list P1 P2, which holds every commit merge-base and merge-tree can read to find the
merge base), then the merge-base commit, and every blob and tree of the merge base's tree, of the recorded
tree and of both input trees (each root tree included), all before merge-tree runs; after merge-tree every blob
and tree of the re-merged tree not already read; and in member mode the member commit, each tree on the path
to .aiqt/merge-resolutions.toml and that file's blob, before the declarations are parsed. An object missing,
of another type, unreadable, or whose stored content does not hash to its id is cannot-evaluate
(missing-object; declaration-unreadable for the member's declaration path), so a merge whose unchanged file
has lost its blob, or whose blob, merge base, merge-base tree, base-history commit or declaration blob was
replaced by other readable content, is never passed on matching object ids alone. What hashing cannot show:
an object store holding two different copies of one id (a loose object and a packed one) is read as git reads
it, and the gate assumes git reads the same copy for every call. Gitlinks (mode 160000) name a
commit in another repository and are compared by id only. merge-tree's output is parsed against its whole
framing: the tree id, the conflict stage entries, the empty field that closes them, then whole messages (a path count of at least one, that many paths, a non-empty type and the
message text), ending in NUL; and it must agree with merge-tree's exit status: exit 0 with no stage entry and
no CONFLICT message, exit 1 with at least one CONFLICT message and every stage path named by one. Anything
else is git-failure, so an output cut at any field boundary is never judged; what framing cannot show is an
output cut after a whole message that still leaves a CONFLICT message naming every stage path.

PINNED MERGE SETTINGS (the merge this gate reproduces): git's ort strategy (the only one merge-tree
--write-tree runs) with no -X strategy option and no --merge-base override; merge.renames=true,
diff.renames=true, merge.renameLimit=7000, diff.renameLimit=7000, merge.directoryRenames=conflict,
merge.conflictStyle=merge, merge.renormalize=false, core.autocrlf=false, core.commitGraph=false,
core.ignoreCase=false and core.attributesFile set to the null device; no system, global or XDG
configuration is read (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL, HOME and XDG_CONFIG_HOME pinned to the null
device, every other GIT_ variable dropped, GIT_ATTR_NOSYSTEM set), and the private bare repository has an
unborn HEAD and no info/attributes, so no .gitattributes is read: no custom merge driver, merge=binary or
text conversion applies. A merge made with other settings (a custom driver, -X ours, rename detection off,
directoryRenames true) can differ from the re-merge and is then a finding to declare, never a pass.

CANNOT EVALUATE (exit 2, each named by its kind, never a pass): git older than 2.38 (git-too-old, read from
git version; merge-tree --write-tree is not available before it), a merge whose parents have more than one
merge base (criss-cross), a commit with more than two parents (octopus), a commit or parent object that is
missing (missing-object; a shallow boundary is one, and so are a missing recorded tree, a missing or
unreadable blob or tree of the recorded, input, merge-base or re-merged tree or one of another type than its
entry claims (even when the same id was read earlier as the type it holds), a commit of the base history or
of the inputs' history that is missing or unreadable, and any object above whose content does not hash to its
id, each read in the source store, never from a re-merge's output), a tree listing that names a path twice or
a path under a directory it does not list, and a recorded, input, merge-base or re-merged tree or subtree off
git's canonical form (a mode other than 100644, 100755, 120000, 160000 or 40000, an empty name, a name with a
slash, . or .., a name stored twice, or entries out of git's tree order) (malformed-tree), a recorded path
named like an entry git moves aside for a conflict that the gate cannot pair with the re-merge's name for it
(conflict-name, under THE COMPARISON), an empty,
missing or repeated option value or an unknown argument (a usage error, before anything is read), a --repo
that is not the top of a repository (repository-unreadable), two parents with no merge base
(unrelated-histories), a declaration file that is absent when named, not a regular file, over 1 MiB, not UTF-8, not TOML or off its closed schema in any type, key or value (declaration-unreadable), a
commit named with --squash whose inputs are not declared (squash-undeclared), a commit named with --squash
that is not a one-parent commit in the range (squash-unjudged), a base, head, member or squash name that does
not resolve to exactly one commit, is ambiguous or uses revision syntax (range-unreadable), the default mode
on a bare repository without --declarations (declaration-unreadable: it has no work tree to read the file
from), and a git failure, timeout, merge-tree output off its framing or at odds with its exit
status, or a local error such as a temporary directory that cannot be made (git-failure). An unexpected
internal error is also exit 2, never exit 1. When a finding and a cannot-evaluate result both occur the exit
is 1; every per-commit result is printed either way. A cannot-evaluate merge has no declaration that accepts
it: the way out is to redo it (a criss-cross merged again from one merge base, an octopus as a chain of
two-parent merges, a shallow clone deepened, a missing object fetched).

SQUASH COMMITS. A one-parent commit is an ordinary commit and is not judged, unless it is named with
--squash REV or declared in a [[squash]] table; then it is judged as the merge of its declared inputs
[destination, source], where the destination must be its one recorded parent and the source must not be the
squash commit or a descendant of it (else a finding), and its recorded tree is compared as above. A named
squash with no declaration is squash-undeclared.

DECLARATIONS. --declarations PATH names the file (cannot-evaluate when absent); without it the default
mode reads .aiqt/merge-resolutions.toml in the work tree of the repository (a bare repository has none, so
the default mode on one without --declarations is declaration-unreadable; give --declarations PATH, or use
--member REV, which reads the member commit's file), and member mode reads it from the
member commit's own tree (the commit, the root tree, the .aiqt tree and the blob read with git cat-file and
each hashed to its id, each of the two trees in git's canonical form as above; .aiqt must be a tree listed
once and merge-resolutions.toml a regular file blob listed once, mode 100644 or 100755, of at most 1 MiB,
else declaration-unreadable), never from a work tree, so which declarations apply to a member does not depend on the lane's checkout or on an uncommitted
file. An absent default file is the empty declaration set (a NOTE line says so). The whole file is checked
against its closed schema before any part of it is used: format-version = 1 (an integer, not a boolean); resolution and squash, when present, are arrays of tables
(an empty table, a string, a number or a boolean in their place is off the schema); [[resolution]] tables
with merge (the full lowercase commit id), path (a relative /-separated path with no empty, . or ..
component), reason (a non-empty string) and either mode ("100644", "100755", "120000" or "160000") with
object (a full lowercase object id) or absent = true; [[squash]] tables with commit, inputs (two distinct
full commit ids, destination first, neither the commit itself) and reason. Every id has the length of the
repository's object format (40 hex digits for SHA-1, 64 for SHA-256). A repeated (merge, path) pair or
squash commit is cannot-evaluate.

RESIDUALS (not caught): the merges of a member branch that is squashed onto main without member mode being
run on its head (see WHICH MERGES IT SEES); a loss made by an ordinary commit rather than by a merge, in
either mode: a member that merges main honestly and then, in a later one-parent commit, restores a path main
deleted (or reverts a change main made) passes, since only merges are re-merged and that commit is an
ordinary change (the train squash then carries the loss onto main); in the default mode the declaration file
is read from the work tree of --repo, so an uncommitted edit to it applies; the content of a conflict
resolution (a path git reports conflicted passes whatever it holds); an entry git moved aside for a conflict
and kept with git's own content passes under a name PATH~LABEL that neither input tree, the merge base nor the
re-merged tree holds, when the recorded tree does not also hold the re-merge's own name for it (the original
merge's labels are not recorded, so LABEL is not judged); a squash that is neither named nor
declared (a one-parent commit is not recognized as a squash from its message); a commit outside the range; a
merge-tree output cut after a whole message that still leaves
every stage path named by a CONFLICT message; a path that is not valid UTF-8 cannot be declared; a
declaration's reason is not judged; a merge made with settings other than the pinned ones (above) is
reported, not reproduced; in CI on a pull request the checked-out HEAD is GitHub's own test merge, which is
judged like any other merge (whether GitHub's merge machinery always matches merge-tree is not verified);
the gate reads git's ownership check without the caller's global configuration, so a checkout owned by
another user is cannot-evaluate; a name is refused whenever more than one lookup accepts it, even where git's
first lookup rule settles it (HEAD itself, or a full refs/ name that exists), so a tag or branch named HEAD,
or a ref such as refs/tags/refs/remotes/origin/main, makes the default --head or --base ambiguous and every
run cannot-evaluate (exit 2, never a pass) until that ref is removed. The hook that would run this at
commit time is not part of this gate.

The entry line runs an inline copy of the canonical child-status guard (opf/tools/_child_status.py) before
main(), since the gate reads git's exit statuses.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_merge_result.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "refs/remotes/origin/main"
DEFAULT_HEAD = "HEAD"
DEFAULT_DECLARATIONS = ".aiqt/merge-resolutions.toml"
MAX_DECLARATION_BYTES = 1 << 20
GIT_FLOOR = (2, 38)
GIT_TIMEOUT = 600
OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
VERSION_RE = re.compile(r"git version ([0-9]+)\.([0-9]+)")
STAGE_RE = re.compile(rb"([0-7]{6}) ([0-9a-f]{40}|[0-9a-f]{64}) ([123])\t(.+)", re.DOTALL)
LS_TREE_RE = re.compile(rb"([0-7]{6}) ([a-z]+) ([0-9a-f]{40}|[0-9a-f]{64})\t(.+)", re.DOTALL)
MODES = ("100644", "100755", "120000", "160000")
# The only modes git writes into a tree entry (a subtree is 40000, with no leading zero).
TREE_MODES = (b"100644", b"100755", b"120000", b"160000", b"40000")
# git's own lookup order for a revision name under refs/ (refs.c ref_rev_parse_rules, after the root ref NAME).
REF_RULES = ("refs/{}", "refs/tags/{}", "refs/heads/{}", "refs/remotes/{}", "refs/remotes/{}/HEAD")
REVISION_SYNTAX_RE = re.compile(r"[~^:?*\[\\]|@\{|\.\.|\A@\Z")
HEX_NAME_RE = re.compile(r"[0-9a-fA-F]{4,64}")
# Bytes of object content read by one git cat-file --batch call (a larger single object is read alone).
BATCH_BYTES = 32 << 20
MERGE_SETTINGS = (
    ("merge.renames", "true"),
    ("diff.renames", "true"),
    ("merge.renameLimit", "7000"),
    ("diff.renameLimit", "7000"),
    ("merge.directoryRenames", "conflict"),
    ("merge.conflictStyle", "merge"),
    ("merge.renormalize", "false"),
    ("core.autocrlf", "false"),
    ("core.commitGraph", "false"),
    ("core.ignoreCase", "false"),
    ("core.attributesFile", os.devnull),
)

# The kinds of a cannot-evaluate result; each is distinct and none is a pass.
GIT_TOO_OLD = "git-too-old"
CRISS_CROSS = "criss-cross"
OCTOPUS = "octopus"
MISSING_OBJECT = "missing-object"
UNRELATED = "unrelated-histories"
DECLARATION_UNREADABLE = "declaration-unreadable"
SQUASH_UNDECLARED = "squash-undeclared"
SQUASH_UNJUDGED = "squash-unjudged"
RANGE_UNREADABLE = "range-unreadable"
REPOSITORY_UNREADABLE = "repository-unreadable"
MALFORMED_TREE = "malformed-tree"
CONFLICT_NAME = "conflict-name"
GIT_FAILURE = "git-failure"

# The rules the self-test removes one at a time (an in-process seam only; no command-line option reaches it).
RULES = ("compare", "conflict-exempt", "declaration-exact", "declaration-stale", "criss-cross", "octopus",
         "missing-parent", "git-floor", "declaration-read", "squash-undeclared", "raw-parents", "unrelated",
         "merge-tree-framing", "merge-tree-status", "squash-unjudged", "squash-source", "declaration-unjudged",
         "recorded-tree", "header-parents", "private-commit-graph", "blob-readable", "repository-top",
         "member-declarations", "name-unique", "tree-unique", "tree-ids", "object-hash", "bare-declarations",
         "merge-base-objects", "base-history-hash", "declaration-hash", "tree-canonical", "source-store",
         "object-kind", "conflict-names", "conflict-name-taken", "conflict-name-destination")


class CannotEvaluate(Exception):
    def __init__(self, kind, detail):
        super().__init__("{}: {}".format(kind, detail))
        self.kind = kind
        self.detail = detail


def show(path):
    """A path (bytes) for one output line: control bytes and non-UTF-8 bytes escaped."""
    text = path.decode("utf-8", "backslashreplace")
    return "".join(c if c.isprintable() else "\\x{:02x}".format(ord(c)) if ord(c) < 256
                   else "\\u{:04x}".format(ord(c)) for c in text)


def git_env(replace_objects=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": os.devnull,
                "XDG_CONFIG_HOME": os.devnull, "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C"})
    if not replace_objects:
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


class Git:
    """One git executable bound to one repository (a work tree or a git directory)."""

    def __init__(self, git, location, bare=False, replace_objects=False):
        self.git = git
        self.prefix = ["--git-dir", str(location)] if bare else ["-C", str(location)]
        self.env = git_env(replace_objects)

    def run(self, args, ok=(0,), what=None, data=None, discard=False):
        """git with args; data, when given, is its standard input, and discard drops its standard output."""
        cmd = [self.git] + self.prefix + list(args)
        stdout = subprocess.DEVNULL if discard else subprocess.PIPE
        try:
            if data is None:
                proc = subprocess.run(cmd, env=self.env, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=subprocess.PIPE)
            else:
                proc = subprocess.run(cmd, env=self.env, timeout=GIT_TIMEOUT, input=data, stdout=stdout,
                                      stderr=subprocess.PIPE)
        except subprocess.TimeoutExpired:
            raise CannotEvaluate(GIT_FAILURE, "git {} timed out after {} s".format(args[0], GIT_TIMEOUT))
        except OSError as exc:
            raise CannotEvaluate(GIT_FAILURE, "git could not run ({})".format(type(exc).__name__))
        if proc.returncode not in ok:
            raise CannotEvaluate(GIT_FAILURE, "{} exited {}: {}".format(
                what or "git " + args[0], proc.returncode,
                proc.stderr.decode("utf-8", "backslashreplace").strip()[:400]))
        return proc


def git_version(git):
    try:
        proc = subprocess.run([git, "version"], env=git_env(), capture_output=True, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotEvaluate(GIT_FAILURE, "git version could not run ({})".format(type(exc).__name__))
    match = VERSION_RE.match(proc.stdout.decode("ascii", "replace"))
    if proc.returncode != 0 or match is None:
        raise CannotEvaluate(GIT_FAILURE, "git version printed no version")
    return int(match.group(1)), int(match.group(2))


def peel_commit(repo_git, spec):
    """The commit id git resolves spec^{commit} to, or None."""
    proc = repo_git.run(["rev-parse", "--verify", "--quiet", "--end-of-options", spec + "^{commit}"], ok=(0, 1))
    oid = proc.stdout.decode("ascii", "replace").strip()
    return oid if proc.returncode == 0 and OID_RE.fullmatch(oid) else None


def name_sources(repo_git, name, hex_length):
    """[(description, spec)] for every lookup git would accept name by: HEAD (every repository holds it),
    each ref of REF_RULES that exists exactly, and every object whose id starts with name (4 or more hex
    digits). Another root ref (a file such as FETCH_HEAD in the git directory) is not listed; resolve then
    compares the commit git resolves with the one listed source's, so a root ref that shadows it is refused."""
    sources = []
    if name == "HEAD":
        sources.append(("the root ref HEAD", name))
    candidates = [rule.format(name) for rule in REF_RULES]
    if name.startswith("refs/"):
        candidates.insert(0, name)
    listed = repo_git.run(["for-each-ref", "--format=%(refname)"] + candidates).stdout
    existing = set(os.fsdecode(listed).split("\n"))
    sources += [("the ref " + ref, ref) for ref in candidates if ref in existing]
    if HEX_NAME_RE.fullmatch(name) and len(name) <= hex_length:
        objects = repo_git.run(["rev-parse", "--disambiguate=" + name.lower()], ok=(0, 1, 128)).stdout
        for oid in objects.decode("ascii", "replace").split():
            if OID_RE.fullmatch(oid):
                sources.append(("the object " + oid, oid))
    return sources


def resolve(repo_git, name, what, hex_length=40, disabled=frozenset()):
    """The one commit id name resolves to; anything else (no commit, revision syntax, a name more than one of
    git's lookups accept) is range-unreadable."""
    if not name or name.startswith("-") or any(ord(c) < 0x21 or ord(c) == 0x7f for c in name):
        raise CannotEvaluate(RANGE_UNREADABLE, "{} {!r} is not a usable revision name".format(what, name))
    hint = (" (fetch it first: git fetch origin main:refs/remotes/origin/main)" if name == DEFAULT_BASE else "")
    try:
        if "name-unique" not in disabled:
            if REVISION_SYNTAX_RE.search(name):
                raise CannotEvaluate(RANGE_UNREADABLE, "{} {} is not a plain name (give a full object id or a "
                                     "ref name, with no ~, ^, :, @{{, .., glob character or @ alone)".format(
                                         what, name))
            sources = name_sources(repo_git, name, hex_length)
            if len(sources) > 1:
                raise CannotEvaluate(RANGE_UNREADABLE, "{} {} is ambiguous: git would accept it as {}; give "
                                     "the full ref name or object id".format(
                                         what, name, ", ".join(text for text, _spec in sources)))
            if not sources:
                raise CannotEvaluate(RANGE_UNREADABLE, "{} {} does not resolve to a commit{}".format(
                    what, name, hint))
            text, spec = sources[0]
            if name.startswith("refs/") and spec != name:
                raise CannotEvaluate(RANGE_UNREADABLE, "{} {} is not itself a ref (git would read it as {})".format(
                    what, name, text))
        oid = peel_commit(repo_git, name)
        if oid is None:
            raise CannotEvaluate(RANGE_UNREADABLE, "{} {} does not resolve to a commit{}".format(what, name, hint))
        if "name-unique" not in disabled:
            own = peel_commit(repo_git, spec)
            if own != oid:
                raise CannotEvaluate(RANGE_UNREADABLE, "{} {} resolves to {} but {} resolves to {}".format(
                    what, name, oid, text, own))
    except CannotEvaluate as exc:
        if exc.kind == RANGE_UNREADABLE:
            raise
        raise CannotEvaluate(RANGE_UNREADABLE, "{} {}: {}".format(what, name, exc.detail))
    return oid


def valid_path(value):
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def is_oid(value, hex_length):
    return isinstance(value, str) and len(value) == hex_length and OID_RE.fullmatch(value) is not None


def is_reason(value):
    return isinstance(value, str) and bool(value.strip())


def check_schema(data, hex_length):
    """The whole parsed declaration file against its closed schema, before any of it is used: (resolutions,
    squashes) on success, else the first violation as a string (types, keys and values all checked)."""
    if not isinstance(data, dict):
        return "the document is not a table"
    unknown = set(data) - {"format-version", "resolution", "squash"}
    if unknown:
        return "unknown key(s) {}".format(", ".join(sorted(unknown)))
    version = data.get("format-version")
    if type(version) is not int or version != 1:
        return "format-version must be the integer 1"
    tables = {}
    for name in ("resolution", "squash"):
        rows = data.get(name, [])
        if type(rows) is not list:
            return "{} must be an array of tables ([[{}]]), not {}".format(name, name, type(rows).__name__)
        for index, row in enumerate(rows):
            if type(row) is not dict:
                return "{} {} is not a table".format(name, index + 1)
        tables[name] = rows
    ids = "a full lowercase id of {} hex digits".format(hex_length)
    resolutions = {}
    for index, row in enumerate(tables["resolution"]):
        where = "resolution {}".format(index + 1)
        keys = set(row)
        if "absent" in keys:
            if keys != {"merge", "path", "reason", "absent"} or row["absent"] is not True:
                return where + " with absent must hold exactly merge, path, reason and absent = true"
            entry = None
        else:
            if keys != {"merge", "path", "reason", "mode", "object"}:
                return where + " must hold exactly merge, path, reason, mode and object"
            if type(row["mode"]) is not str or row["mode"] not in MODES:
                return where + " mode must be one of the strings " + ", ".join(MODES)
            if not is_oid(row["object"], hex_length):
                return where + " object must be " + ids
            entry = (row["mode"], row["object"])
        if not is_oid(row["merge"], hex_length):
            return where + " merge must be " + ids
        if not valid_path(row["path"]):
            return where + " path must be a relative path with no empty, . or .. component"
        if not is_reason(row["reason"]):
            return where + " reason must be a non-empty string"
        key = (row["merge"], row["path"].encode("utf-8"))
        if key in resolutions:
            return where + " repeats merge {} path {}".format(row["merge"], row["path"])
        resolutions[key] = entry
    squashes = {}
    for index, row in enumerate(tables["squash"]):
        where = "squash {}".format(index + 1)
        if set(row) != {"commit", "inputs", "reason"}:
            return where + " must be a table of exactly commit, inputs and reason"
        inputs = row["inputs"]
        if (not is_oid(row["commit"], hex_length) or type(inputs) is not list or len(inputs) != 2
                or not all(is_oid(i, hex_length) for i in inputs)):
            return where + " commit and both inputs must be " + ids
        if inputs[0] == inputs[1] or row["commit"] in inputs:
            return where + " inputs must be two distinct commits, neither the squash commit itself"
        if not is_reason(row["reason"]):
            return where + " reason must be a non-empty string"
        if row["commit"] in squashes:
            return where + " repeats commit {}".format(row["commit"])
        squashes[row["commit"]] = (inputs[0], inputs[1])
    return resolutions, squashes


def load_declarations(path, required, hex_length=40):
    """(resolutions {(merge, path bytes): (mode, object) or None}, squashes {commit: (dest, source)}, note)."""
    def bad(why):
        return CannotEvaluate(DECLARATION_UNREADABLE, "{}: {}".format(path, why))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if required:
            raise bad("absent")
        return {}, {}, "NOTE: no declaration file at {}; no hand resolution or squash is declared".format(path)
    except OSError as exc:
        raise bad("cannot be read ({})".format(type(exc).__name__))
    if not stat.S_ISREG(info.st_mode):
        raise bad("not a regular file")
    if info.st_size > MAX_DECLARATION_BYTES:
        raise bad("over {} bytes".format(MAX_DECLARATION_BYTES))
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(MAX_DECLARATION_BYTES + 1)
    except OSError as exc:
        raise bad("cannot be read ({})".format(type(exc).__name__))
    resolutions, squashes = parse_declarations(raw, path, hex_length)
    return resolutions, squashes, None


def read_hashed(git, oid, kind, object_format, check=True):
    """The raw content of the object oid of the kind, which must hash to oid (unless check is off); None when
    it is missing or of another type."""
    proc = git.run(["cat-file", kind, oid], ok=(0, 1, 128))
    if proc.returncode != 0:
        return None
    if check and object_hash(object_format, kind, proc.stdout) != oid:
        raise CannotEvaluate(MISSING_OBJECT, "{} {} does not hash to its id (its stored content differs)".format(
            kind, oid))
    return proc.stdout


def raw_tree_entries(raw, hex_length):
    """[(mode bytes, name bytes, id)] of a raw tree object, in stored order; None when it is off the format."""
    entries = []
    pos = 0
    width = hex_length // 2
    while pos < len(raw):
        space = raw.find(b" ", pos)
        nul = raw.find(b"\x00", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + width > len(raw):
            return None
        entries.append((raw[pos:space], raw[space + 1:nul], raw[nul + 1:nul + 1 + width].hex()))
        pos = nul + 1 + width
    return entries


def tree_form_problem(raw, hex_length):
    """None when the raw tree object is in git's canonical form, else why it is not: every mode exactly one of
    TREE_MODES, every name non-empty, without a slash and not . or .., no name listed twice, and the entries
    strictly increasing in git's tree order (a subtree's name compared as if it ended in a slash). git reads a
    tree off this form inconsistently (a lookup by name can miss an entry a listing shows, and a merge can
    drop a side's change to it), so such a tree is never compared."""
    entries = raw_tree_entries(raw, hex_length)
    if entries is None:
        return "it is off the tree format"
    names = set()
    previous = None
    for mode, name, _oid in entries:
        if mode not in TREE_MODES:
            return "it lists {} with the mode {}".format(show(name), show(mode))
        if not name or b"/" in name or name in (b".", b".."):
            return "it lists the entry name {!r}".format(show(name))
        if name in names:
            return "it lists the name {} more than once".format(show(name))
        names.add(name)
        key = name + b"/" if mode == b"40000" else name
        if previous is not None and key <= previous:
            return "it lists {} out of git's tree order".format(show(name))
        previous = key
    return None


def committed_declarations(real, commit, hex_length=40, object_format="sha1", disabled=frozenset()):
    """The default declaration file as the commit's own tree holds it (member mode), never a work tree's:
    (resolutions, squashes, note), the empty set with a note when the commit does not hold the file. The
    commit, each tree on the path to the file and the file's blob are read in full and hashed to their ids
    (declaration-unreadable when one does not hash to its id), so a substituted object is never read as the
    member's declarations."""
    where = "{}:{}".format(commit, DEFAULT_DECLARATIONS)
    check = "declaration-hash" not in disabled

    def bad(why):
        return CannotEvaluate(DECLARATION_UNREADABLE, "{}: {}".format(where, why))
    try:
        raw = read_hashed(real, commit, "commit", object_format, check)
        if raw is None:
            raise bad("the commit cannot be read")
        tree = parse_commit(raw)[0]
        names = DEFAULT_DECLARATIONS.encode("ascii").split(b"/")
        entry = None
        for depth, name in enumerate(names):
            content = read_hashed(real, tree, "tree", object_format, check)
            if content is None:
                raise bad("the tree {} cannot be read".format(tree))
            entries = raw_tree_entries(content, hex_length)
            if entries is None:
                raise bad("the tree {} is off the tree format".format(tree))
            problem = tree_form_problem(content, hex_length) if "tree-canonical" not in disabled else None
            if problem is not None:
                raise bad("the tree {} is not in git's canonical form ({})".format(tree, problem))
            found = [e for e in entries if e[1] == name]
            if not found:
                return {}, {}, ("NOTE: member {} holds no {}; no hand resolution or squash is declared".format(
                    commit, DEFAULT_DECLARATIONS))
            if len(found) != 1:
                raise bad("the tree {} lists {} more than once".format(tree, show(name)))
            entry = found[0]
            if depth < len(names) - 1:
                if entry[0] != b"40000":
                    raise bad("not a regular file in the commit's tree")
                tree = entry[2]
        if entry[0] not in (b"100644", b"100755"):
            raise bad("not a regular file in the commit's tree")
        blob = entry[2]
        size = real.run(["cat-file", "-s", blob]).stdout.strip()
        if not size.isdigit() or int(size) > MAX_DECLARATION_BYTES:
            raise bad("over {} bytes".format(MAX_DECLARATION_BYTES))
        raw = read_hashed(real, blob, "blob", object_format, check)
        if raw is None:
            raise bad("not a regular file in the commit's tree")
    except CannotEvaluate as exc:
        if exc.kind == DECLARATION_UNREADABLE:
            raise
        raise bad("cannot be read ({})".format(exc.detail))
    resolutions, squashes = parse_declarations(raw, where, hex_length)
    return resolutions, squashes, None


def parse_declarations(raw, path, hex_length):
    """(resolutions, squashes) from the declaration file's bytes, checked whole against its closed schema."""
    def bad(why):
        return CannotEvaluate(DECLARATION_UNREADABLE, "{}: {}".format(path, why))
    if len(raw) > MAX_DECLARATION_BYTES:
        raise bad("over {} bytes".format(MAX_DECLARATION_BYTES))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad("not UTF-8")
    try:
        data = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        raise bad("not TOML ({})".format(type(exc).__name__))
    checked = check_schema(data, hex_length)
    if isinstance(checked, str):
        raise bad(checked)
    return checked


def parse_commit(raw, disabled=frozenset()):
    """(tree, [parents]) from a raw commit object's header, in recorded order, read as git reads it: the
    first line is the tree and the parents are the parent lines directly after it (a parent line after any
    other header is not a parent)."""
    header = raw.split(b"\n\n", 1)[0].split(b"\n")
    if not header[0].startswith(b"tree "):
        raise CannotEvaluate(GIT_FAILURE, "a commit header that does not start with its tree")
    tree = header[0][5:].decode("ascii", "replace")
    parents = []
    for line in header[1:]:
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii", "replace"))
        elif "header-parents" not in disabled:
            break
    if not OID_RE.fullmatch(tree) or not all(OID_RE.fullmatch(p) for p in parents):
        raise CannotEvaluate(GIT_FAILURE, "a commit header the gate cannot parse")
    return tree, parents


def exists(priv, oid, kind):
    proc = priv.run(["cat-file", "-t", oid], ok=(0, 1, 128))
    return proc.returncode == 0 and proc.stdout.strip() == kind.encode("ascii")


def tree_entries(priv, tree, disabled=frozenset()):
    """{path: (mode, type, id)} for every entry of tree, its subtrees included (git ls-tree -r -t). A path
    listed twice, or a path whose directory is not listed as a tree, is malformed-tree."""
    out = priv.run(["ls-tree", "-r", "-t", "-z", "--full-tree", tree]).stdout
    entries = {}
    for record in out.split(b"\x00"):
        if not record:
            continue
        match = LS_TREE_RE.fullmatch(record)
        if match is None:
            raise CannotEvaluate(GIT_FAILURE, "an ls-tree line the gate cannot parse")
        path = match.group(4)
        if path in entries and "tree-unique" not in disabled:
            raise CannotEvaluate(MALFORMED_TREE, "tree {} lists the path {} more than once".format(tree, show(path)))
        entries[path] = (match.group(1).decode(), match.group(2).decode(), match.group(3).decode())
    if "tree-unique" not in disabled:
        for path in entries:
            parent = path.rpartition(b"/")[0]
            if parent and (entries.get(parent) or ("", "", ""))[1] != "tree":
                raise CannotEvaluate(MALFORMED_TREE, "tree {} lists the path {} but not its directory {} as a "
                                     "tree (a tree entry whose name holds a slash)".format(
                                         tree, show(path), show(parent)))
    return entries


def object_hash(object_format, kind, content):
    """The object id git gives content of the kind (blob, tree or commit) in the object format."""
    digest = hashlib.new("sha1" if object_format == "sha1" else "sha256")
    digest.update("{} {}\x00".format(kind, len(content)).encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def read_commit(priv, oid, object_format, disabled=frozenset()):
    """The raw commit object oid, which must hash to oid; None when it is missing."""
    proc = priv.run(["cat-file", "commit", oid], ok=(0, 1, 128))
    if proc.returncode != 0:
        return None
    if "object-hash" not in disabled and object_hash(object_format, "commit", proc.stdout) != oid:
        raise CannotEvaluate(MISSING_OBJECT, "commit {} does not hash to its id (its stored content differs)".format(
            oid))
    return proc.stdout


def require_objects(priv, entries, done, commit, which, object_format, roots=(), disabled=frozenset(),
                    commits=()):
    """Every blob and tree of entries (gitlinks excepted), every root tree id in roots and every commit id in
    commits whose (id, kind) pair is not in done is present with the kind its entry claims, read in full and
    hashed to its id, else missing-object, and every such tree is in git's canonical form (tree_form_problem),
    else malformed-tree; the (id, kind) pairs read are added to done. done is keyed by kind as well as id, so an
    id read once as one kind (a tree, say) never satisfies a later entry that names it as another (a blob).
    which names where they come from (the recorded tree, the base history) and commit the commit or range they
    are read for."""
    wanted = {(entry[2], entry[1]) for entry in entries.values() if entry[1] in ("blob", "tree")}
    wanted |= {(root, "tree") for root in roots}
    wanted |= {(oid, "commit") for oid in commits}
    if "object-kind" in disabled:
        done_ids = {oid for oid, _kind in done}
        objects = sorted(item for item in wanted if item[0] not in done_ids)
    else:
        objects = sorted(item for item in wanted if item not in done)
    if not objects:
        return
    data = "".join(oid + "\n" for oid, _kind in objects).encode("ascii")
    out = priv.run(["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                   data=data).stdout.split(b"\n")
    if out[-1:] != [b""] or len(out) != len(objects) + 1:
        raise CannotEvaluate(GIT_FAILURE, "git cat-file --batch-check answered {} line(s) for {} object(s)".format(
            len(out) - 1, len(objects)))
    sized = []
    for (oid, kind), line in zip(objects, out):
        parts = line.split(b" ")
        if len(parts) != 3 or parts[:2] != [oid.encode("ascii"), kind.encode("ascii")] or not parts[2].isdigit():
            raise CannotEvaluate(MISSING_OBJECT, "{} {} of the {} of {} is missing or not a {} ({})".format(
                kind, oid, which, commit, kind, show(line)))
        sized.append((oid, kind, int(parts[2])))
    batches, batch, total = [], [], 0
    for item in sized:
        if batch and total + item[2] > BATCH_BYTES:
            batches.append(batch)
            batch, total = [], 0
        batch.append(item)
        total += item[2]
    batches.append(batch)
    for batch in batches:
        request = "".join(oid + "\n" for oid, _kind, _size in batch).encode("ascii")
        try:
            raw = priv.run(["cat-file", "--batch"], data=request).stdout
        except CannotEvaluate as exc:
            raise CannotEvaluate(MISSING_OBJECT, "an object of the {} of {} cannot be read ({})".format(
                which, commit, exc.detail))
        view = memoryview(raw)
        pos = 0
        for oid, kind, size in batch:
            def unreadable(why):
                return CannotEvaluate(MISSING_OBJECT, "{} {} of the {} of {} {}".format(
                    kind, oid, which, commit, why))
            end_of_header = raw.find(b"\n", pos)
            if end_of_header < 0 or raw[pos:end_of_header] != "{} {} {}".format(oid, kind, size).encode("ascii"):
                raise unreadable("cannot be read (git cat-file --batch is off its framing)")
            start = end_of_header + 1
            end = start + size
            if raw[end:end + 1] != b"\n":
                raise unreadable("cannot be read (its content is not {} bytes)".format(size))
            if "object-hash" not in disabled and object_hash(object_format, kind, view[start:end]) != oid:
                raise unreadable("does not hash to its id (its stored content differs)")
            if kind == "tree" and "tree-canonical" not in disabled:
                problem = tree_form_problem(raw[start:end], 40 if object_format == "sha1" else 64)
                if problem is not None:
                    raise CannotEvaluate(MALFORMED_TREE, "tree {} of the {} of {} is not in git's canonical "
                                         "form ({})".format(oid, which, commit, problem))
            pos = end + 1
        if pos != len(raw):
            raise CannotEvaluate(MISSING_OBJECT, "git cat-file --batch printed more than the {} of {} "
                                 "asked for".format(which, commit))
    done.update((oid, kind) for oid, kind, _size in sized)


def parse_merge_tree(out, returncode, disabled=frozenset()):
    """(tree, conflicted paths) from merge-tree --write-tree -z --messages output and its exit status.

    The full framing is required: <tree> NUL, zero or more <mode> SP <object> SP <stage> TAB <path> NUL, an
    empty field (NUL) closing them, then zero or more messages <count> NUL <path> NUL x count <type> NUL
    <message> NUL with count at least 1 and a non-empty type. The output must agree with the exit status:
    0 (clean) lists no stage entry and no CONFLICT message; 1 (conflicted) lists at least one CONFLICT
    message, and every stage path is named by one. Anything else is git-failure."""
    framing = "merge-tree-framing" not in disabled

    def broken(why):
        return CannotEvaluate(GIT_FAILURE, "merge-tree output (exit {}) {}".format(returncode, why))
    if returncode not in (0, 1):
        raise broken("has an exit status other than 0 (clean) or 1 (conflicted)")
    fields = out.split(b"\x00")
    if fields[-1] != b"":
        raise broken("does not end in NUL")
    fields = fields[:-1]
    tree = fields[0].decode("ascii", "replace") if fields else ""
    if not OID_RE.fullmatch(tree):
        raise broken("holds no tree id")
    stages = set()
    index = 1
    while index < len(fields) and fields[index] != b"":
        match = STAGE_RE.fullmatch(fields[index])
        if match is None:
            raise broken("holds a conflict stage entry the gate cannot parse")
        stages.add(match.group(4))
        index += 1
    if index >= len(fields) and framing:
        raise broken("ends before the empty field that closes its conflict stage entries (truncated)")
    index += 1
    named = set()
    conflict_messages = 0
    while index < len(fields):
        count = fields[index]
        if not count.isdigit() or (framing and int(count) < 1):
            raise broken("holds a message whose path count the gate cannot parse")
        count = int(count)
        if index + count + 2 >= len(fields):
            raise broken("ends inside a message (truncated)")
        paths = fields[index + 1:index + 1 + count]
        kind = fields[index + 1 + count]
        if framing and (not kind or not all(paths)):
            raise broken("holds a message with an empty type or path")
        if kind.startswith(b"CONFLICT"):
            conflict_messages += 1
            named.update(paths)
        index += count + 3
    if "merge-tree-status" not in disabled:
        if returncode == 0 and (stages or conflict_messages):
            raise broken("reports a conflict although the merge exited clean")
        if returncode == 1 and not conflict_messages:
            raise broken("reports no CONFLICT message although the merge exited conflicted (incomplete)")
        if not stages <= named:
            raise broken("lists a conflict stage path {} that no CONFLICT message names (incomplete)".format(
                show(sorted(stages - named)[0])))
    return tree, stages | named


def describe(entry):
    return "absent" if entry is None else "{} {} {}".format(*entry)


GENERATED_SUFFIX_RE = re.compile(rb"(?:_[0-9]+)?")


def conflict_names(conflicted, differing, expected, recorded, declared, labels, taken=frozenset(),
                   recorded_paths=frozenset()):
    """Where a conflict made git write a side's entry under a new name (PATH~LABEL, for a file/directory or
    distinct-types conflict, with _N added when that name is taken), the name holds the label git gave that side:
    merge-tree labels each side by the id it is given, the original merge by its own label (HEAD, a branch name).
    Returns (mapped {recorded path: re-merge path}, unmapped [recorded paths]): a recorded-only, undeclared path
    outside the conflicted set that is PATH~LABEL for the PATH of such a re-merge name is mapped when its entry is
    exactly the re-merge's entry under that name and the pairing is one to one; else it is unmapped (the gate
    cannot tell which side's entry it is, so cannot-evaluate, never a finding). Only a name git itself could have
    generated for this merge is a candidate: git never picks a name that already exists in either input tree or
    the merge base, so a path in taken (every path of both input trees, of each merge-base tree and of the
    re-merged tree, subtrees included) is no candidate and gets the ordinary comparison. A pairing whose re-merge
    name the recorded tree already holds (recorded_paths, every path of the recorded tree) is not one to one (the
    recorded tree would hold git's entry twice), so the candidate is unmapped."""
    generated = {}
    for path in conflicted:
        for label in labels:
            marker = b"~" + label.encode("ascii")
            at = path.rfind(marker)
            if at <= 0 or path[at - 1:at] == b"/":
                continue
            if GENERATED_SUFFIX_RE.fullmatch(path[at + len(marker):]):
                generated[path] = path[:at]
    pairs = {}
    lookalikes = set()
    for path in sorted(differing):
        if (path in conflicted or path in declared or path in expected or path not in recorded
                or path in taken):
            continue
        for name, stem in generated.items():
            rest = path[len(stem) + 1:]
            if path.startswith(stem + b"~") and rest and b"/" not in rest:
                lookalikes.add(path)
                if recorded[path] == expected.get(name) and name not in recorded_paths:
                    pairs.setdefault(path, set()).add(name)
    claimed = {}
    for path, names in pairs.items():
        for name in names:
            claimed.setdefault(name, set()).add(path)
    mapped = {path: next(iter(names)) for path, names in pairs.items()
              if len(names) == 1 and len(claimed[next(iter(names))]) == 1}
    return mapped, sorted(lookalikes - set(mapped))


class Run:
    def __init__(self):
        self.lines = []
        self.findings = 0
        self.cannot = 0

    def finding(self, text):
        self.findings += 1
        self.lines.append("FAIL " + text)

    def unevaluable(self, commit, exc):
        self.cannot += 1
        self.lines.append("CANNOT EVALUATE {}: {}: {}".format(commit, exc.kind, exc.detail))

    def note(self, text):
        self.lines.append("NOTE " + text)


def judge(run, priv, commit, p1, p2, recorded_tree, resolutions, disabled, object_format="sha1",
          verified=None, remerge=None):
    """Judge one merge of p1 and p2; verified is the set of (object id, kind) pairs already read and hashed in
    this run (commits of the walk and of the base history), extended here. Every source object is read in
    priv, the source store, which nothing writes to; merge-tree runs in remerge, the re-merge store (priv when None),
    so an object one merge's re-merge writes can never stand in for a source object of another merge."""
    if verified is None:
        verified = set()
    if remerge is None:
        remerge = priv
    input_trees = []
    for parent in (p1, p2):
        if not exists(priv, parent, "commit"):
            raise CannotEvaluate(MISSING_OBJECT, "input {} of {} is missing".format(parent, commit))
        raw = read_commit(priv, parent, object_format, disabled)
        if raw is None:
            raise CannotEvaluate(MISSING_OBJECT, "input {} of {} is missing".format(parent, commit))
        input_trees.append(parse_commit(raw)[0])
    merge_base_objects = "merge-base-objects" not in disabled
    if merge_base_objects:
        # Every commit merge-base and merge-tree can read to find the merge base (the whole history of both
        # inputs) is read and hashed to its id first, so a substituted ancestor cannot move the merge base.
        listed = priv.run(["rev-list", p1, p2], ok=(0, 128))
        if listed.returncode != 0:
            raise CannotEvaluate(MISSING_OBJECT, "the history of {} and {} cannot be read".format(p1, p2))
        history = listed.stdout.decode("ascii", "replace").split()
        require_objects(priv, {}, verified, commit, "input history", object_format, (), disabled, history)
    bases = priv.run(["merge-base", "--all", p1, p2], ok=(0, 1)).stdout.split()
    bases = [b.decode("ascii", "replace") for b in bases]
    if not bases and "unrelated" not in disabled:
        raise CannotEvaluate(UNRELATED, "{} and {} have no merge base".format(p1, p2))
    if len(bases) > 1 and "criss-cross" not in disabled:
        raise CannotEvaluate(CRISS_CROSS, "{} and {} have {} merge bases".format(p1, p2, len(bases)))
    base_trees = []
    if merge_base_objects:
        # The merge base merge-tree reads: its commit hashed to its id, and every blob and tree of its tree read
        # in full and hashed, before merge-tree runs.
        for merge_base in bases:
            raw = read_commit(priv, merge_base, object_format, disabled)
            if raw is None:
                raise CannotEvaluate(MISSING_OBJECT, "the merge base {} of {} is missing".format(merge_base, commit))
            base_trees.append(parse_commit(raw)[0])
    recorded = None
    if "recorded-tree" not in disabled:
        # Read the recorded tree in full before merge-tree can write any object into the private store, so a
        # missing recorded tree or subtree is never supplied by the re-merge itself.
        if not exists(priv, recorded_tree, "tree"):
            raise CannotEvaluate(MISSING_OBJECT, "the recorded tree {} of {} is missing".format(
                recorded_tree, commit))
        recorded = tree_entries(priv, recorded_tree, disabled)
    objects_read = set()
    readable = "blob-readable" not in disabled
    if readable:
        # Every blob and tree the comparison relies on is read and hashed before merge-tree can write any object
        # into the private store: the recorded tree's and both input trees' (gitlinks excepted).
        for which, tree in (("recorded", recorded_tree), ("input", input_trees[0]), ("input", input_trees[1])):
            entries = (recorded if which == "recorded" and recorded is not None
                       else tree_entries(priv, tree, disabled))
            require_objects(priv, entries, objects_read, commit, which + " tree", object_format, (tree,), disabled)
    for tree in (base_trees if readable else ()):
        # The merge base's tree, each subtree and blob of it (gitlinks excepted), the root tree included.
        require_objects(priv, {}, objects_read, commit, "merge-base tree", object_format, (tree,), disabled)
        require_objects(priv, tree_entries(priv, tree, disabled), objects_read, commit, "merge-base tree",
                        object_format, (), disabled)
    settings = []
    for key, value in MERGE_SETTINGS:
        settings += ["-c", "{}={}".format(key, value)]
    proc = remerge.run(settings + ["merge-tree", "--write-tree", "-z", "--messages", p1, p2], ok=(0, 1),
                       what="git merge-tree")
    expected_tree, conflicted = parse_merge_tree(proc.stdout, proc.returncode, disabled)
    expected_all = tree_entries(remerge, expected_tree, disabled)
    if recorded is None:
        recorded = tree_entries(remerge, recorded_tree, disabled)
    if readable:
        require_objects(remerge, expected_all, objects_read, commit, "re-merged tree", object_format,
                        (expected_tree,), disabled)
    recorded_all = recorded
    # Files, symlinks and gitlinks are compared path by path below; subtrees by object id after them.
    expected = {p: e for p, e in expected_all.items() if e[1] != "tree"}
    recorded = {p: e for p, e in recorded_all.items() if e[1] != "tree"}
    differing = set()
    if "compare" not in disabled:
        differing = {p for p in set(expected) | set(recorded) if expected.get(p) != recorded.get(p)}
    exempt = conflicted if "conflict-exempt" not in disabled else set()
    declared = {path: entry for (merge, path), entry in resolutions.items() if merge == commit}
    renamed, unmapped = {}, []
    if "conflict-names" not in disabled:
        taken = set()
        if "conflict-name-taken" not in disabled and any(b"~" in path for path in conflicted):
            # Every path of both input trees, of each merge-base tree and of the re-merged tree: none of them can
            # be a name git generated for this merge's conflict.
            taken.update(expected_all)
            trees = list(input_trees) + list(base_trees)
            if not merge_base_objects:
                for merge_base in bases:
                    raw = priv.run(["cat-file", "commit", merge_base]).stdout
                    trees.append(parse_commit(raw, disabled)[0])
            for tree in trees:
                taken.update(tree_entries(priv, tree, disabled))
        recorded_paths = set(recorded_all) if "conflict-name-destination" not in disabled else set()
        renamed, unmapped = conflict_names(conflicted, differing, expected, recorded, declared, (p1, p2), taken,
                                           recorded_paths)
    clean = True
    for path in sorted(differing):
        have = recorded.get(path)
        have_short = None if have is None else (have[0], have[2])
        if path in exempt:
            run.note("{} {}: a conflict git reports, resolved by hand (not judged)".format(commit, show(path)))
        elif path in renamed:
            run.note("{} {}: git's own name for the conflict entry the re-merge names {}, kept as git wrote it "
                     "(not judged)".format(commit, show(path), show(renamed[path])))
        elif path in unmapped:
            continue
        elif path in declared:
            if declared[path] == have_short or "declaration-exact" in disabled:
                run.note("{} {}: declared hand resolution, recorded {}".format(commit, show(path),
                                                                               describe(have)))
            else:
                clean = False
                run.finding("{} {}: declared {} but recorded {}".format(
                    commit, show(path), "absent" if declared[path] is None else " ".join(declared[path]),
                    describe(have)))
        else:
            clean = False
            run.finding("{} {}: re-merge of {} and {} gives {}, recorded {}".format(
                commit, show(path), p1, p2, describe(expected.get(path)), describe(have)))
    if "declaration-stale" not in disabled:
        for path in sorted(declared):
            if path not in differing or path in exempt:
                clean = False
                run.finding("{} {}: stale declaration (the path {})".format(
                    commit, show(path), "is a conflict git reports" if path in differing
                    else "matches the re-merge"))
    if "tree-ids" not in disabled:
        # A subtree whose id differs from the re-merge's (or that one side lacks) must hold a differing file,
        # symlink or gitlink below it; one that does not (an empty subtree added or dropped, a tree the re-merge
        # does not write) is a finding, and so is a root tree id that differs with no entry differing.
        def tree_of(entries, path):
            entry = entries.get(path)
            return entry if entry is not None and entry[1] == "tree" else None
        tree_paths = {p for p, e in expected_all.items() if e[1] == "tree"}
        tree_paths |= {p for p, e in recorded_all.items() if e[1] == "tree"}
        unexplained = []
        for path in sorted(tree_paths):
            want, have = tree_of(expected_all, path), tree_of(recorded_all, path)
            if want != have and not any(p.startswith(path + b"/") for p in differing):
                unexplained.append(path)
                clean = False
                run.finding("{} {}: the re-merge of {} and {} gives the subtree {}, recorded {}, and no file below "
                            "it differs (an empty or malformed subtree)".format(
                                commit, show(path), p1, p2, describe(want), describe(have)))
        if expected_tree != recorded_tree and not differing and not unexplained:
            clean = False
            run.finding("{}: the re-merge of {} and {} gives the tree {}, recorded {}, and no entry differs".format(
                commit, p1, p2, expected_tree, recorded_tree))
    if unmapped:
        raise CannotEvaluate(CONFLICT_NAME, "{} is named like an entry git moves aside for a conflict "
                             "(PATH~LABEL), but the gate cannot pair it with the re-merge's name for that entry "
                             "(its content differs, or the pairing is not one to one)".format(show(unmapped[0])))
    if clean:
        run.note("{}: matches the re-merge of {} and {}".format(commit, p1, p2))
    return clean


def walk(priv, real, head, base_set, disabled, object_format="sha1", verified=None):
    """[(commit, tree, parents, missing parents)] reachable from head and not from the base; every commit read
    is hashed to its id and added to verified as (id, "commit")."""
    if verified is None:
        verified = set()
    order = []
    seen = set()
    stack = [head]
    while stack:
        commit = stack.pop()
        if commit in seen or commit in base_set:
            continue
        seen.add(commit)
        raw = read_commit(priv, commit, object_format, disabled)
        if raw is None:
            raise CannotEvaluate(MISSING_OBJECT, "commit {} is missing".format(commit))
        if "object-hash" not in disabled:
            verified.add((commit, "commit"))
        tree, parents = parse_commit(raw, disabled)
        if "raw-parents" in disabled:
            listed = real.run(["rev-list", "--parents", "-n", "1", commit]).stdout.split()
            parents = [p.decode("ascii") for p in listed[1:]]
        missing = [p for p in parents if p not in base_set and not exists(priv, p, "commit")]
        if "missing-parent" in disabled:
            parents = [p for p in parents if p not in missing]
            missing = []
        order.append((commit, tree, parents, missing))
        for parent in reversed(parents):
            if parent not in missing:
                stack.append(parent)
    return order


def make_private(git, scratch, object_format, common, disabled=frozenset(), name="private"):
    """The private bare repository NAME.git under scratch, borrowing the repository's objects through one
    alternates line, with core.commitGraph false in its own configuration (so no read there uses a
    commit-graph)."""
    priv_dir = os.path.join(scratch, name + ".git")
    template = os.path.join(scratch, name + "-template")
    try:
        os.mkdir(template)
        Git(git, scratch).run(["init", "-q", "--bare", "--object-format=" + object_format,
                               "--template=" + template, priv_dir])
        os.makedirs(os.path.join(priv_dir, "objects", "info"), exist_ok=True)
        with open(os.path.join(priv_dir, "objects", "info", "alternates"), "w", encoding="utf-8") as handle:
            handle.write(os.path.join(common, "objects") + "\n")
    except OSError as exc:
        raise CannotEvaluate(GIT_FAILURE, "the private repository could not be made ({})".format(
            type(exc).__name__))
    priv = Git(git, priv_dir, bare=True)
    if "private-commit-graph" not in disabled:
        priv.run(["config", "core.commitGraph", "false"])
    return priv


def evaluate(repo, base=DEFAULT_BASE, head=DEFAULT_HEAD, declarations=None, squash=(), git="git",
             disabled=frozenset(), member=None):
    """(exit code, output lines) for the merges between base and head (or base and member, in member mode)
    in the repository at repo."""
    run = Run()
    if member is not None:
        head, head_what = member, "member"
    else:
        head_what = "head"
    try:
        # Every input is a non-empty value before anything is read; an empty one never becomes a default.
        if not isinstance(repo, str) or not repo:
            raise CannotEvaluate(REPOSITORY_UNREADABLE, "the repository {!r} is not a usable path".format(repo))
        if declarations is not None and (not isinstance(declarations, str) or not declarations):
            raise CannotEvaluate(DECLARATION_UNREADABLE, "the declaration path {!r} is not a usable path".format(
                declarations))
        version = git_version(git)
        if version < GIT_FLOOR and "git-floor" not in disabled:
            raise CannotEvaluate(GIT_TOO_OLD, "git {}.{} is older than {}.{} (merge-tree --write-tree)".format(
                *(version + GIT_FLOOR)))
        real = Git(git, repo, replace_objects="raw-parents" in disabled)
        if "repository-top" not in disabled:
            if not os.path.isdir(repo):
                raise CannotEvaluate(REPOSITORY_UNREADABLE, "{} is not a directory".format(repo))
            try:
                bare = real.run(["rev-parse", "--is-bare-repository"]).stdout.strip() == b"true"
                top = real.run(["rev-parse", "--absolute-git-dir" if bare else "--show-toplevel"]).stdout
            except CannotEvaluate as exc:
                raise CannotEvaluate(REPOSITORY_UNREADABLE, "{} is not read as a repository ({})".format(
                    repo, exc.detail))
            top = os.fsdecode(top.rstrip(b"\n"))
            if os.path.realpath(top) != os.path.realpath(repo):
                raise CannotEvaluate(REPOSITORY_UNREADABLE, "{} is not the top of a repository (git reads the "
                                     "repository at {})".format(repo, top))
        common = real.run(["rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout
        common = os.fsdecode(common.rstrip(b"\n"))
        object_format = real.run(["rev-parse", "--show-object-format"]).stdout.decode("ascii", "replace").strip()
        if object_format not in ("sha1", "sha256"):
            raise CannotEvaluate(GIT_FAILURE, "unknown object format {!r}".format(object_format))
        hex_length = 40 if object_format == "sha1" else 64
        base_oid = resolve(real, base, "base", hex_length, disabled)
        head_oid = resolve(real, head, head_what, hex_length, disabled)
        named = {resolve(real, rev, "squash", hex_length, disabled) for rev in squash}
        try:
            if declarations is None and member is not None and "member-declarations" not in disabled:
                resolutions, squashes, note = committed_declarations(real, head_oid, hex_length, object_format,
                                                                     disabled)
            elif declarations is None:
                if ("bare-declarations" not in disabled
                        and real.run(["rev-parse", "--is-bare-repository"]).stdout.strip() == b"true"):
                    raise CannotEvaluate(DECLARATION_UNREADABLE, "{} is a bare repository: the default mode reads {} "
                                         "from a work tree and a bare repository has none; name the file with "
                                         "--declarations PATH, or judge a member head with --member REV (which "
                                         "reads the member commit's own file)".format(repo, DEFAULT_DECLARATIONS))
                top = os.fsdecode(real.run(["rev-parse", "--show-toplevel"]).stdout.rstrip(b"\n"))
                resolutions, squashes, note = load_declarations(os.path.join(top, DEFAULT_DECLARATIONS), False,
                                                                hex_length)
            else:
                resolutions, squashes, note = load_declarations(declarations, True, hex_length)
        except CannotEvaluate:
            if "declaration-read" not in disabled:
                raise
            resolutions, squashes, note = {}, {}, None
        if note:
            run.note(note[len("NOTE: "):])
        run.note("range {}..{} (base {}, {} {})".format(base_oid, head_oid, base, head_what, head))
        if member is not None:
            run.note("member mode: every two-parent commit on member {} that base {} does not hold is "
                     "judged".format(member, base))
    except CannotEvaluate as exc:
        run.unevaluable("range", exc)
        return 2, run.lines
    try:
        scratch = tempfile.mkdtemp(prefix="merge-result-")
    except OSError as exc:
        run.unevaluable("range", CannotEvaluate(GIT_FAILURE, "no temporary directory could be made ({})".format(
            type(exc).__name__)))
        return 2, run.lines
    try:
        try:
            priv = make_private(git, scratch, object_format, common, disabled)
            # merge-tree writes into a store of its own, which the source store priv cannot see: no source
            # object of one merge is ever read from the output of another merge's re-merge.
            remerge = (make_private(git, scratch, object_format, common, disabled, name="remerge")
                       if "source-store" not in disabled else priv)
            listed = priv.run(["-c", "core.commitGraph=false", "rev-list", base_oid], ok=(0, 128))
            if listed.returncode != 0:
                raise CannotEvaluate(MISSING_OBJECT, "the history of base {} cannot be read".format(base_oid))
            base_history = listed.stdout.decode("ascii", "replace").split()
            base_set = set(base_history)
            verified = set()
            if "base-history-hash" not in disabled:
                # Every commit of the base history is read and hashed to its id, so a substituted ancestor of the
                # base (one that lists a merge of the range as its parent, say) cannot hide that merge from the walk.
                require_objects(priv, {}, verified, "base " + base_oid, "base history", object_format, (), disabled,
                                base_history)
            commits = walk(priv, real, head_oid, base_set, disabled, object_format, verified)
        except CannotEvaluate as exc:
            run.unevaluable("range", exc)
            return (1 if run.findings else 2), run.lines
        if not commits:
            run.note("no commit between base and head; nothing to judge")
        judged = ordinary = 0
        walked = {commit: parents for commit, _tree, parents, _missing in commits}
        considered = set()
        for commit, tree, parents, missing in commits:
            try:
                if missing:
                    considered.add(commit)
                    raise CannotEvaluate(MISSING_OBJECT, "parent {} is missing".format(", ".join(missing)))
                if len(parents) > 2 and "octopus" not in disabled:
                    considered.add(commit)
                    raise CannotEvaluate(OCTOPUS, "{} parents".format(len(parents)))
                if len(parents) >= 2:
                    considered.add(commit)
                    judge(run, priv, commit, parents[0], parents[1], tree, resolutions, disabled, object_format,
                          verified, remerge)
                    judged += 1
                elif len(parents) == 1 and (commit in squashes or commit in named):
                    if commit not in squashes:
                        if "squash-undeclared" in disabled:
                            ordinary += 1
                            continue
                        considered.add(commit)
                        raise CannotEvaluate(SQUASH_UNDECLARED, "named a squash; its inputs are not declared")
                    considered.add(commit)
                    destination, source = squashes[commit]
                    if destination != parents[0]:
                        run.finding("{}: declared squash destination {} is not its recorded parent {}".format(
                            commit, destination, parents[0]))
                        continue
                    if "squash-source" not in disabled:
                        if not exists(priv, source, "commit"):
                            raise CannotEvaluate(MISSING_OBJECT, "squash source {} is missing".format(source))
                        contains = priv.run(["merge-base", "--is-ancestor", commit, source], ok=(0, 1))
                        if contains.returncode == 0:
                            run.finding("{}: declared squash source {} is the squash commit or a descendant "
                                        "of it, so the re-merge would reproduce the squash itself".format(
                                            commit, source))
                            continue
                    judge(run, priv, commit, destination, source, tree, resolutions, disabled, object_format,
                          verified, remerge)
                    judged += 1
                else:
                    ordinary += 1
            except CannotEvaluate as exc:
                run.unevaluable(commit, exc)
        if "squash-unjudged" not in disabled:
            for rev in sorted(named):
                if rev not in walked:
                    run.unevaluable(rev, CannotEvaluate(SQUASH_UNJUDGED, "named with --squash but not in the "
                                                        "range {}..{}".format(base_oid, head_oid)))
                elif len(walked[rev]) != 1:
                    run.unevaluable(rev, CannotEvaluate(SQUASH_UNJUDGED, "named with --squash but records {} "
                                                        "parent(s), not one".format(len(walked[rev]))))
        if "declaration-unjudged" not in disabled:
            for merge, path in sorted(resolutions):
                if merge in walked and merge not in considered:
                    run.finding("{} {}: stale declaration (the commit is in the range but records {} parent(s) "
                                "and is not judged as a merge)".format(merge, show(path), len(walked[merge])))
            for commit in sorted(squashes):
                if commit in walked and len(walked[commit]) != 1:
                    run.finding("{}: stale squash declaration (the commit is in the range but records {} "
                                "parent(s), not one)".format(commit, len(walked[commit])))
        run.note("{} merge(s) judged, {} other commit(s) not merges".format(judged, ordinary))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if run.findings:
        return 1, run.lines
    if run.cannot:
        return 2, run.lines
    return 0, run.lines


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    try:
        return run_cli(args)
    except Exception as exc:
        # Every case the gate cannot judge is exit 2: an unexpected error is never read as a finding (exit 1)
        # or a pass.
        print("CANNOT EVALUATE: internal error ({}: {}); nothing was judged".format(
            type(exc).__name__, str(exc)[:400]), file=sys.stderr)
        return 2


def run_cli(args):
    options = {"--repo": None, "--base": None, "--head": None, "--declarations": None, "--member": None}
    squash = []
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in options or flag == "--squash":
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                print("CANNOT EVALUATE: {} needs a value".format(flag), file=sys.stderr)
                return 2
            if not args[index + 1]:
                # An empty value (an unset variable in the caller) is a usage error, never the default.
                print("CANNOT EVALUATE: {} is empty; give a value or leave the option out".format(flag),
                      file=sys.stderr)
                return 2
            if flag == "--squash":
                squash.append(args[index + 1])
            elif options[flag] is not None:
                print("CANNOT EVALUATE: {} given twice".format(flag), file=sys.stderr)
                return 2
            else:
                options[flag] = args[index + 1]
            index += 2
            continue
        print("CANNOT EVALUATE: unrecognized argument {!r} (see the module docstring)".format(flag),
              file=sys.stderr)
        return 2
    if options["--member"] is not None and (options["--head"] is not None or squash):
        print("CANNOT EVALUATE: --member judges base..member and takes neither --head nor --squash",
              file=sys.stderr)
        return 2
    def given(flag, default):
        return default if options[flag] is None else options[flag]
    code, lines = evaluate(given("--repo", str(ROOT)), given("--base", DEFAULT_BASE),
                           given("--head", DEFAULT_HEAD), options["--declarations"], squash,
                           member=options["--member"])
    for line in lines:
        print(line)
    print({0: "PASS: every merge in the range matches its re-merge",
           1: "FAIL: a merge result differs from the re-merge of its parents",
           2: "CANNOT EVALUATE: a merge in the range could not be judged (never a pass)"}[code])
    return code


# ---------------------------------------------------------------------------------------------------------
# Self-test: fixture repositories under a temporary directory, removed at the end.

class Fixture:
    def __init__(self, root, name, object_format="sha1"):
        self.path = os.path.join(root, name)
        os.mkdir(self.path)
        self.env = git_env()
        self.env.update({"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                         "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                         "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"})
        template = os.path.join(root, name + "-template")
        os.mkdir(template)
        self.git("init", "-q", "-b", "main", "--object-format=" + object_format, "--template=" + template)

    def git(self, *args, ok=(0,), data=None):
        cmd = ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "merge.conflictStyle=merge",
               "-C", self.path] + list(args)
        if data is None:
            proc = subprocess.run(cmd, env=self.env, capture_output=True, timeout=GIT_TIMEOUT,
                                  stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.run(cmd, env=self.env, capture_output=True, timeout=GIT_TIMEOUT, input=data)
        if proc.returncode not in ok:
            raise RuntimeError("fixture git {} exited {}: {}".format(
                " ".join(args), proc.returncode, proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("utf-8", "replace").strip()

    def write(self, name, data):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if os.path.islink(full):
            os.remove(full)
        with open(full, "wb") as handle:
            handle.write(data)

    def link(self, name, target):
        full = os.path.join(self.path, name)
        if os.path.lexists(full):
            os.remove(full)
        os.symlink(target, full)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def blob(self, rev, path):
        return self.git("rev-parse", "{}:{}".format(rev, path))


RENAMED_TEXT = b"".join(b"line %d of the renamed file\n" % n for n in range(1, 21))


def build_base(fx):
    fx.write("keep.txt", b"kept\n")
    fx.write("gone1.txt", b"deleted on main\n")
    fx.write("dir/gone2.txt", b"also deleted on main\n")
    fx.write("img.bin", b"\x00\x01\x02binary\x00")
    fx.write("tool.sh", b"#!/bin/sh\n")
    fx.write("target1", b"t1\n")
    fx.write("target2", b"t2\n")
    fx.link("lnk", "target1")
    fx.write("conflict.txt", b"a\nb\nc\n")
    fx.write("r1.txt", RENAMED_TEXT)
    os.mkdir(os.path.join(fx.path, "sub"))
    fx.git("add", "-A")
    fx.git("update-index", "--add", "--cacheinfo", "160000,{},sub".format("1" * 40))
    return fx.commit("base")


def fixtures(root):
    """{case: (repo, kwargs for evaluate)} over fixture repositories built under root."""
    cases = {}
    # The incident: a branch merges main with -s ours, so every change main made (two deletions, a binary
    # edit, a symlink retarget, a mode change and a submodule move) is lost from the result.
    fx = Fixture(root, "incident")
    build_base(fx)
    fx.git("checkout", "-q", "-b", "side")
    fx.write("feature.txt", b"feature\n")
    fx.commit("side feature")
    fx.git("checkout", "-q", "main")
    os.remove(os.path.join(fx.path, "gone1.txt"))
    os.remove(os.path.join(fx.path, "dir", "gone2.txt"))
    fx.write("img.bin", b"\x00\x09\x08binary changed\x00")
    os.chmod(os.path.join(fx.path, "tool.sh"), 0o755)
    fx.link("lnk", "target2")
    fx.git("add", "-A")
    fx.git("update-index", "--cacheinfo", "160000,{},sub".format("2" * 40))
    main_tip = fx.commit("main deletes and edits")
    fx.git("checkout", "-q", "side")
    fx.git("merge", "-q", "--no-edit", "-s", "ours", "main")
    merge = fx.git("rev-parse", "HEAD")
    cases["incident"] = (fx.path, {"base": main_tip, "head": merge})
    cases["incident-replace"] = (fx.path, {"base": main_tip, "head": merge, "graft": "replace"})
    side_tip = fx.git("rev-parse", "HEAD^1")
    fixtures.incident = (fx, merge, side_tip, main_tip)
    # The same incident on a member branch that the merge lane then squashes onto main: the default mode on
    # the train sees one ordinary commit and passes (the disclosed residual); member mode on the member head
    # sees the -s ours merge and fails.
    fx.git("checkout", "-q", "-b", "train", main_tip)
    fx.git("merge", "-q", "--squash", "side")
    fx.git("commit", "-q", "-m", "train: squash of side")
    cases["member-train-squash"] = (fx.path, {"base": main_tip, "head": fx.git("rev-parse", "HEAD")})
    cases["member-mode"] = (fx.path, {"base": main_tip, "member": merge})
    fx.git("checkout", "-q", "side")
    # --repo naming a directory inside the repository but not its top: git -C would judge the enclosing
    # repository, so it is refused.
    cases["repository-subdirectory"] = (os.path.join(fx.path, "dir"), {"base": main_tip, "head": merge})

    # A clean merge, a rename on one side edited on the other, and a hand-resolved conflict.
    fx = Fixture(root, "clean")
    base = build_base(fx)
    fx.git("checkout", "-q", "-b", "side")
    fx.write("feature.txt", b"feature\n")
    fx.write("r1.txt", RENAMED_TEXT.replace(b"line 10 of", b"line ten, edited on side, of"))
    fx.write("conflict.txt", b"a\nSIDE\nc\n")
    fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.git("mv", "r1.txt", "r2.txt")
    fx.write("keep.txt", b"kept, edited on main\n")
    fx.write("conflict.txt", b"a\nMAIN\nc\n")
    main_tip = fx.commit("main renames r1 to r2")
    fx.git("checkout", "-q", "-b", "plain", base)
    fx.write("other.txt", b"other\n")
    plain_tip = fx.commit("plain")
    fx.git("checkout", "-q", "-b", "clean-only", base)
    fx.write("keep.txt", b"kept, edited\n")
    clean_base = fx.commit("edit keep")
    fx.git("merge", "-q", "--no-edit", plain_tip)
    clean_merge = fx.git("rev-parse", "HEAD")
    cases["clean"] = (fx.path, {"base": clean_base, "head": clean_merge})
    # Rename: side edits r1.txt, main renames it; the merge carries the edit into r2.txt.
    fx.git("checkout", "-q", "-b", "rename-only", main_tip)
    fx.git("checkout", "-q", "-b", "side-rename", base)
    fx.write("r1.txt", RENAMED_TEXT.replace(b"line 10 of", b"line ten, edited on side, of"))
    side_rename = fx.commit("side edits r1")
    fx.git("checkout", "-q", "rename-only")
    fx.git("merge", "-q", "--no-edit", side_rename)
    rename_merge = fx.git("rev-parse", "HEAD")
    cases["rename"] = (fx.path, {"base": main_tip, "head": rename_merge})
    fixtures.rename_check = (fx, rename_merge)
    # Conflict resolved by hand: passes, a conflict git reports.
    fx.git("checkout", "-q", "-b", "conflicted", main_tip)
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    fx.write("conflict.txt", b"a\nRESOLVED\nc\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    conflict_merge = fx.git("rev-parse", "HEAD")
    cases["conflict"] = (fx.path, {"base": main_tip, "head": conflict_merge})

    # A deliberate hand resolution in an otherwise clean merge (an evil merge), declared.
    fx.git("checkout", "-q", "-b", "evil", clean_base)
    fx.git("merge", "-q", "--no-commit", plain_tip)
    fx.write("other.txt", b"other, fixed in the merge\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    evil = fx.git("rev-parse", "HEAD")
    blob = fx.blob(evil, "other.txt")
    good = os.path.join(root, "declared.toml")
    with open(good, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "other.txt"\n'
                     'mode = "100644"\nobject = "{}"\nreason = "fixture"\n'.format(evil, blob))
    wrong = os.path.join(root, "wrong.toml")
    with open(wrong, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "other.txt"\n'
                     'mode = "100644"\nobject = "{}"\nreason = "fixture"\n'.format(evil, "0" * 40))
    stale = os.path.join(root, "stale.toml")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "keep.txt"\n'
                     'absent = true\nreason = "fixture"\n'.format(clean_merge))
    malformed = os.path.join(root, "malformed.toml")
    with open(malformed, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n[[resolution]\nmerge = \n')
    cases["declared"] = (fx.path, {"base": clean_base, "head": evil, "declarations": good})
    # Member mode reads the declarations the member commit holds, whatever the checkout (here a branch that
    # does not hold the file).
    fx.git("checkout", "-q", "-b", "evil-member", evil)
    with open(good, encoding="utf-8") as handle:
        fx.write(DEFAULT_DECLARATIONS, handle.read().encode("utf-8"))
    evil_member = fx.commit("the member declares its hand resolution")
    cases["member-declarations"] = (fx.path, {"base": clean_base, "member": evil_member})
    cases["undeclared"] = (fx.path, {"base": clean_base, "head": evil})
    cases["declared-wrong"] = (fx.path, {"base": clean_base, "head": evil, "declarations": wrong})
    cases["declared-stale"] = (fx.path, {"base": clean_base, "head": clean_merge, "declarations": stale})
    cases["declaration-malformed"] = (fx.path, {"base": clean_base, "head": clean_merge,
                                                "declarations": malformed})
    cases["declaration-directory"] = (fx.path, {"base": clean_base, "head": clean_merge, "declarations": root})
    cases["declaration-absent"] = (fx.path, {"base": clean_base, "head": clean_merge,
                                             "declarations": os.path.join(root, "absent.toml")})

    # A squash (one parent), named: undeclared, declared, and declared with the wrong destination.
    fx.git("checkout", "-q", "-b", "squashed", clean_base)
    fx.git("merge", "-q", "--squash", plain_tip)
    fx.git("commit", "-q", "-m", "squash of plain")
    squash = fx.git("rev-parse", "HEAD")
    squash_decl = os.path.join(root, "squash.toml")
    with open(squash_decl, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[squash]]\ncommit = "{}"\ninputs = ["{}", "{}"]\n'
                     'reason = "fixture"\n'.format(squash, clean_base, plain_tip))
    squash_wrong = os.path.join(root, "squash-wrong.toml")
    with open(squash_wrong, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[squash]]\ncommit = "{}"\ninputs = ["{}", "{}"]\n'
                     'reason = "fixture"\n'.format(squash, plain_tip, clean_base))
    cases["squash-undeclared"] = (fx.path, {"base": clean_base, "head": squash, "squash": [squash]})
    cases["squash-declared"] = (fx.path, {"base": clean_base, "head": squash, "squash": [squash],
                                          "declarations": squash_decl})
    cases["squash-wrong-destination"] = (fx.path, {"base": clean_base, "head": squash,
                                                   "declarations": squash_wrong})
    # A named commit the walk does not judge as a squash: outside the range, or a two-parent merge.
    cases["squash-named-outside"] = (fx.path, {"base": clean_base, "head": squash, "squash": [base]})
    cases["squash-named-merge"] = (fx.path, {"base": clean_base, "head": clean_merge, "squash": [clean_merge]})
    # A squash declared with a source that descends from the squash itself (an empty commit on top of it):
    # the re-merge would reproduce the squash, so it would always pass.
    fx.git("checkout", "-q", "-b", "after-squash", squash)
    after_squash = fx.commit("empty commit on top of the squash")
    descendant = os.path.join(root, "squash-descendant.toml")
    with open(descendant, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[squash]]\ncommit = "{}"\ninputs = ["{}", "{}"]\n'
                     'reason = "fixture"\n'.format(squash, clean_base, after_squash))
    cases["squash-source-descendant"] = (fx.path, {"base": clean_base, "head": squash,
                                                   "declarations": descendant})
    # Declarations naming a commit the walk reaches but does not judge as a merge.
    one_parent = os.path.join(root, "one-parent.toml")
    with open(one_parent, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "keep.txt"\nabsent = true\n'
                     'reason = "fixture"\n'.format(squash))
    cases["declaration-one-parent"] = (fx.path, {"base": clean_base, "head": squash, "declarations": one_parent})
    squash_on_merge = os.path.join(root, "squash-on-merge.toml")
    with open(squash_on_merge, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[squash]]\ncommit = "{}"\ninputs = ["{}", "{}"]\n'
                     'reason = "fixture"\n'.format(clean_merge, clean_base, plain_tip))
    cases["squash-declaration-on-merge"] = (fx.path, {"base": clean_base, "head": clean_merge,
                                                      "declarations": squash_on_merge})
    # Valid TOML off the declaration schema: an empty table, a string or a number where an array of tables
    # belongs, and an id of the other object format's length.
    for name, body in (("decl-resolution-table", "resolution = {}\n"), ("decl-squash-string", 'squash = ""\n'),
                       ("decl-resolution-integer", "resolution = 1\n"),
                       ("decl-id-length", '[[resolution]]\nmerge = "{}"\npath = "keep.txt"\nabsent = true\n'
                                          'reason = "fixture"\n'.format("a" * 64))):
        path = os.path.join(root, name + ".toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("format-version = 1\n" + body)
        cases[name] = (fx.path, {"base": clean_base, "head": clean_merge, "declarations": path})
    # merge-tree output off its framing or at odds with its exit status, from a stand-in git that prints a
    # fixed merge-tree output for the clean merge and passes every other call to the real git.
    clean_tree = fx.git("rev-parse", clean_merge + "^{tree}")
    stage = "100644 {} 1\tkeep.txt".format("1" * 40)
    for name, payload, code in (
            ("merge-tree-truncated", clean_tree + "\0", 0),
            ("merge-tree-truncated-stage", clean_tree + "\0" + stage + "\0", 1),
            ("merge-tree-status-clean", clean_tree + "\0" + stage + "\0\0", 0),
            ("merge-tree-status-conflicted", clean_tree + "\0\0", 1),
            ("merge-tree-unnamed-stage", clean_tree + "\0" + stage + "\0\0" + "1\0other.txt\0CONFLICT (contents)\0"
             "CONFLICT (content): Merge conflict in other.txt\n\0", 1)):
        stub = os.path.join(root, name + "-git")
        with open(stub, "w", encoding="utf-8") as handle:
            handle.write("#!{} -I\nimport os, sys\nif 'merge-tree' in sys.argv:\n"
                         "    sys.stdout.buffer.write({!r})\n    sys.stdout.flush()\n    sys.exit({})\n"
                         "os.execv({!r}, [{!r}] + sys.argv[1:])\n".format(
                             sys.executable, payload.encode("ascii"), code, shutil.which("git"),
                             shutil.which("git")))
        os.chmod(stub, 0o755)
        cases[name] = (fx.path, {"base": clean_base, "head": clean_merge, "git": stub})
    # A parent header after the author line: git reads one parent, so the gate reads one too (an ordinary
    # commit), never a merge of a second parent git does not see.
    crafted = os.path.join(root, "crafted-commit")
    with open(crafted, "w", encoding="ascii") as handle:
        handle.write("tree {}\nparent {}\nauthor F <f@example.invalid> 946684800 +0000\ncommitter F "
                     "<f@example.invalid> 946684800 +0000\nparent {}\n\ncrafted\n".format(
                         fx.git("rev-parse", clean_base + "^{tree}"), clean_base, plain_tip))
    crafted_commit = fx.git("hash-object", "-t", "commit", "-w", "--literally", crafted)
    cases["header-parents"] = (fx.path, {"base": clean_base, "head": crafted_commit})
    fixtures.conflict_inputs = (fx, main_tip, "side")

    # An octopus merge.
    fx.git("checkout", "-q", "-b", "octo-a", base)
    fx.write("octo-a.txt", b"a\n")
    octo_a = fx.commit("octo a")
    fx.git("checkout", "-q", "-b", "octo-b", base)
    fx.write("octo-b.txt", b"b\n")
    octo_b = fx.commit("octo b")
    fx.git("checkout", "-q", "-b", "octo", base)
    fx.write("octo-c.txt", b"c\n")
    octo_base = fx.commit("octo c")
    fx.git("merge", "-q", "--no-edit", octo_a, octo_b)
    cases["octopus"] = (fx.path, {"base": octo_base, "head": fx.git("rev-parse", "HEAD")})

    # A criss-cross: two merges cross, then a merge of the two has two merge bases.
    fx.git("checkout", "-q", "-b", "cross-a", base)
    fx.write("ca.txt", b"a\n")
    ca = fx.commit("cross a")
    fx.git("checkout", "-q", "-b", "cross-b", base)
    fx.write("cb.txt", b"b\n")
    cb = fx.commit("cross b")
    fx.git("checkout", "-q", "cross-a")
    fx.git("merge", "-q", "--no-edit", cb)
    fx.git("checkout", "-q", "cross-b")
    fx.git("merge", "-q", "--no-edit", ca)
    cross_b = fx.git("rev-parse", "HEAD")
    fx.git("checkout", "-q", "cross-a")
    cross_a = fx.git("rev-parse", "HEAD")
    fx.git("merge", "-q", "--no-edit", cross_b)
    cases["criss-cross"] = (fx.path, {"base": cross_a, "head": fx.git("rev-parse", "HEAD")})

    # Unrelated histories joined by a merge.
    fx.git("checkout", "-q", "--orphan", "island")
    fx.git("rm", "-r", "-q", "-f", "--cached", ".")
    for name in os.listdir(fx.path):
        if name != ".git":
            full = os.path.join(fx.path, name)
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
    fx.write("island.txt", b"island\n")
    island = fx.commit("island")
    fx.git("checkout", "-q", "-f", "-b", "joined", clean_base)
    fx.git("merge", "-q", "--no-edit", "--allow-unrelated-histories", island)
    cases["unrelated"] = (fx.path, {"base": clean_base, "head": fx.git("rev-parse", "HEAD")})

    # A missing parent object: the merge's second parent is removed from the object store.
    fx = Fixture(root, "missing")
    build_base(fx)
    fx.git("checkout", "-q", "-b", "side")
    fx.write("feature.txt", b"feature\n")
    side_tip = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("keep.txt", b"main\n")
    main_tip = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", side_tip)
    merge = fx.git("rev-parse", "HEAD")
    fx.git("checkout", "-q", "--detach", merge)
    fx.git("branch", "-q", "-D", "side")
    os.remove(os.path.join(fx.path, ".git", "objects", side_tip[:2], side_tip[2:]))
    cases["missing-parent"] = (fx.path, {"base": main_tip, "head": merge})

    # A missing recorded tree: a clean merge whose own tree object is removed. The re-merge would write the
    # same tree into the private store, so it must be read before merge-tree runs.
    fx = Fixture(root, "notree")
    fx.write("a.txt", b"a\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("b.txt", b"b\n")
    side_tip = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("a.txt", b"a, edited on main\n")
    main_tip = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", side_tip)
    merge = fx.git("rev-parse", "HEAD")
    merge_tree = fx.git("rev-parse", merge + "^{tree}")
    os.remove(os.path.join(fx.path, ".git", "objects", merge_tree[:2], merge_tree[2:]))
    cases["recorded-tree-missing"] = (fx.path, {"base": main_tip, "head": merge})

    # A missing blob: a clean merge whose unchanged file dir/a has lost its blob. merge-tree never reads it and
    # both trees name the same id, so only reading every blob refuses it.
    fx = Fixture(root, "noblob")
    fx.write("dir/a", b"unchanged on both sides\n")
    fx.write("k.txt", b"k\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("s.txt", b"s\n")
    side_tip = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("k.txt", b"k, edited on main\n")
    main_tip = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", side_tip)
    merge = fx.git("rev-parse", "HEAD")
    lost = fx.blob(merge, "dir/a")
    os.remove(os.path.join(fx.path, ".git", "objects", lost[:2], lost[2:]))
    cases["blob-missing"] = (fx.path, {"base": main_tip, "head": merge})

    # A SHA-256 repository: the private repository takes the checkout's object format. A clean merge passes
    # and a branch that merges main with -s ours (losing main's edit) fails.
    fx = Fixture(root, "sha256", object_format="sha256")
    fx.write("a.txt", b"a\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("b.txt", b"b\n")
    fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("a.txt", b"a, edited on main\n")
    main256 = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", "side")
    cases["sha256-clean"] = (fx.path, {"base": main256, "head": fx.git("rev-parse", "HEAD")})
    fx.git("checkout", "-q", "side")
    fx.git("merge", "-q", "--no-edit", "-s", "ours", main256)
    cases["sha256-incident"] = (fx.path, {"base": main256, "head": fx.git("rev-parse", "HEAD")})

    # Inputs that must name exactly one object: a revision name two refs share, a recorded tree that lists a
    # path twice or under a directory it does not list, an empty subtree the re-merge does not hold, a bare
    # repository in the default mode, and (in a repository of its own) a blob whose stored content was replaced.
    fx = Fixture(root, "objects")
    fx.write("a.txt", b"a\n")
    fx.write("dir/u.txt", b"unchanged\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("b.txt", b"b1\nb2\nb3\n")
    obj_side = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("a.txt", b"a, edited on main\n")
    obj_main = fx.commit("main")
    fx.git("checkout", "-q", "-b", "honest", obj_main)
    fx.git("merge", "-q", "--no-edit", obj_side)
    honest = fx.git("rev-parse", "HEAD")
    cases["head-default-name"] = (fx.path, {"base": obj_main, "head": "HEAD"})
    fx.git("checkout", "-q", "-b", "amb", obj_side)
    fx.git("merge", "-q", "--no-edit", "-s", "ours", obj_main)
    fx.git("checkout", "-q", "honest")
    fx.git("tag", "amb", obj_main)
    cases["ambiguous-name"] = (fx.path, {"base": obj_main, "member": "amb"})
    cases["full-ref-name"] = (fx.path, {"base": obj_main, "member": "refs/heads/amb"})

    def crafted_merge(entries, message):
        raw = b"".join(mode + b" " + name + b"\x00" + bytes.fromhex(oid) for mode, name, oid in entries)
        tree = fx.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", data=raw)
        return fx.git("commit-tree", tree, "-p", obj_main, "-p", obj_side, "-m", message)
    a_id, b_id = fx.blob(honest, "a.txt"), fx.blob(honest, "b.txt")
    u_id, dir_id = fx.blob(honest, "dir/u.txt"), fx.blob(honest, "dir")
    lost_b = fx.git("hash-object", "-w", "--stdin", data=b"b1\nlost\n")
    duplicate = crafted_merge(((b"100644", b"a.txt", a_id), (b"100644", b"b.txt", lost_b),
                               (b"100644", b"b.txt", b_id), (b"40000", b"dir", dir_id)), "b.txt listed twice")
    cases["duplicate-path"] = (fx.path, {"base": obj_main, "head": duplicate})
    slash = crafted_merge(((b"100644", b"a.txt", a_id), (b"100644", b"b.txt", b_id),
                           (b"100644", b"dir/u.txt", u_id)), "dir/u.txt as one entry name")
    cases["slash-name"] = (fx.path, {"base": obj_main, "head": slash})
    empty = fx.git("hash-object", "-t", "tree", "-w", "--stdin", data=b"")
    with_empty = crafted_merge(((b"100644", b"a.txt", a_id), (b"100644", b"b.txt", b_id),
                                (b"40000", b"dir", dir_id), (b"40000", b"empty", empty)), "adds an empty subtree")
    cases["empty-subtree"] = (fx.path, {"base": obj_main, "head": with_empty})
    bare = os.path.join(root, "objects-bare.git")
    fx.git("clone", "-q", "--bare", fx.path, bare)
    cases["bare-default-mode"] = (bare, {"base": obj_main, "head": honest})
    cases["bare-member-mode"] = (bare, {"base": obj_main, "member": honest})
    fx = Fixture(root, "rehash")
    fx.write("dir/u.txt", b"unchanged\n")
    fx.write("k.txt", b"k\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("s.txt", b"s\n")
    side_tip = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("k.txt", b"k, edited on main\n")
    main_tip = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", side_tip)
    merge = fx.git("rev-parse", "HEAD")
    victim = fx.blob(merge, "dir/u.txt")
    loose = os.path.join(fx.path, ".git", "objects", victim[:2], victim[2:])
    os.chmod(loose, 0o644)
    with open(loose, "wb") as handle:
        handle.write(zlib.compress(b"blob 9\x00different"))
    cases["blob-wrong-content"] = (fx.path, {"base": main_tip, "head": merge})

    # Objects the comparison relies on outside the recorded and input trees, each replaced by other readable
    # content under its own id (a loose object rewritten): every case passes the lossy merge when that object is
    # not hashed, so each must be cannot-evaluate.
    def substitute(fx, oid, kind, content):
        loose = os.path.join(fx.path, ".git", "objects", oid[:2], oid[2:])
        os.chmod(loose, 0o644)
        with open(loose, "wb") as handle:
            handle.write(zlib.compress(b"%s %d\x00" % (kind.encode("ascii"), len(content)) + content))

    def lossy_history(name):
        # base B; main edits k.txt (m1) then adds j.txt (tip); side adds s.txt and merges main with -s ours.
        fx = Fixture(root, name)
        fx.write("f.txt", b"f\n")
        fx.write("k.txt", b"k\n")
        b = fx.commit("base")
        fx.git("checkout", "-q", "-b", "side")
        fx.write("s.txt", b"s\n")
        fx.commit("side")
        fx.git("checkout", "-q", "main")
        fx.write("k.txt", b"k, edited on main\n")
        m1 = fx.commit("main edits k")
        fx.write("j.txt", b"j\n")
        tip = fx.commit("main adds j")
        fx.git("checkout", "-q", "side")
        fx.git("merge", "-q", "--no-edit", "-s", "ours", tip)
        return fx, b, m1, tip, fx.git("rev-parse", "HEAD")

    def raw_tree(fx, tree):
        entries = [line.split("\t", 1) for line in fx.git("ls-tree", tree).split("\n")]
        return b"".join(head.split(" ")[0].lstrip("0").encode("ascii") + b" " + path.encode("utf-8") + b"\x00"
                        + bytes.fromhex(head.split(" ")[2]) for head, path in entries)
    # An ancestor of the base rewritten to list the lossy merge as a parent: the merge joins the base history.
    fx, b, m1, tip, merge = lossy_history("subst-base-history")
    raw = fx.git("cat-file", "commit", m1) + "\n"
    raw = raw.replace("parent {}\n".format(b), "parent {}\nparent {}\n".format(b, merge), 1)
    substitute(fx, m1, "commit", raw.encode("utf-8"))
    cases["subst-base-history"] = (fx.path, {"base": tip, "head": merge})
    # The merge-base commit rewritten to name main's tree: the re-merge then reproduces the -s ours result.
    fx, b, m1, tip, merge = lossy_history("subst-merge-base-commit")
    raw = fx.git("cat-file", "commit", b) + "\n"
    raw = raw.replace(fx.git("rev-parse", b + "^{tree}"), fx.git("rev-parse", tip + "^{tree}"), 1)
    substitute(fx, b, "commit", raw.encode("utf-8"))
    cases["subst-merge-base-commit"] = (fx.path, {"base": tip, "head": merge})
    # The merge-base root tree rewritten to main's tree content: the same false re-merge.
    fx, b, m1, tip, merge = lossy_history("subst-merge-base-tree")
    substitute(fx, fx.git("rev-parse", b + "^{tree}"), "tree", raw_tree(fx, tip + "^{tree}"))
    cases["subst-merge-base-tree"] = (fx.path, {"base": tip, "head": merge})

    def lossy_conflict(name):
        # base f.txt a/b/c; side changes the first line, main the last; side merges main with -s ours.
        fx = Fixture(root, name)
        fx.write("f.txt", b"a\nb\nc\n")
        b = fx.commit("base")
        fx.git("checkout", "-q", "-b", "side")
        fx.write("f.txt", b"A\nb\nc\n")
        fx.commit("side")
        fx.git("checkout", "-q", "main")
        fx.write("f.txt", b"a\nb\nC\n")
        tip = fx.commit("main")
        fx.git("checkout", "-q", "side")
        fx.git("merge", "-q", "--no-edit", "-s", "ours", tip)
        return fx, b, tip, fx.git("rev-parse", "HEAD")
    # A merge-base blob (one only the merge base holds) rewritten so both sides seem to change the same lines:
    # git then reports a conflict, which would exempt the lost edit.
    fx, b, tip, merge = lossy_conflict("subst-merge-base-blob")
    substitute(fx, fx.blob(b, "f.txt"), "blob", b"X\nb\nY\n")
    cases["subst-merge-base-blob"] = (fx.path, {"base": tip, "head": merge})
    # Member mode: the member head's declaration blob rewritten to declare the lost path.
    fx, b, tip, merge = lossy_conflict("subst-declaration")
    fx.write(DEFAULT_DECLARATIONS, b"format-version = 1\n")
    member = fx.commit("the member adds an empty declaration file")
    declared = ('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "f.txt"\nmode = "100644"\n'
                'object = "{}"\nreason = "fixture"\n'.format(merge, fx.blob(merge, "f.txt"))).encode("utf-8")
    substitute(fx, fx.blob(member, DEFAULT_DECLARATIONS), "blob", declared)
    cases["subst-declaration"] = (fx.path, {"base": tip, "member": member})

    # Trees off git's canonical form next to a resolved conflict: a conflicted path exempts the root tree id,
    # so only reading each tree's form refuses them. git reads an unsorted tree inconsistently (a lookup by name
    # misses keep.txt), and a later merge of a branch that edits keep.txt into it drops that edit cleanly.
    fx = Fixture(root, "canonical")
    fx.write("conflict.txt", b"a\nb\nc\n")
    fx.write("keep.txt", b"k1\nk2\nk3\n")
    fx.write("z.txt", b"z\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    fx.write("conflict.txt", b"a\nSIDE\nc\n")
    side_tip = fx.commit("side")
    fx.git("checkout", "-q", "main")
    fx.write("conflict.txt", b"a\nMAIN\nc\n")
    main_tip = fx.commit("main")
    fx.git("merge", "-q", "--no-edit", side_tip, ok=(1,))
    fx.write("conflict.txt", b"a\nRESOLVED\nc\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    honest = fx.git("rev-parse", "HEAD")
    ids = {name: fx.blob(honest, name) for name in ("conflict.txt", "keep.txt", "z.txt")}

    def literal_tree(entries):
        raw = b"".join(mode + b" " + name + b"\x00" + bytes.fromhex(oid) for mode, name, oid in entries)
        return fx.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", data=raw)

    def off_form(entries, message):
        tree = literal_tree([(mode, name, ids[name.decode("ascii")]) for mode, name in entries])
        return fx.git("commit-tree", tree, "-p", main_tip, "-p", side_tip, "-m", message)
    unsorted = off_form(((b"100644", b"conflict.txt"), (b"100644", b"z.txt"), (b"100644", b"keep.txt")),
                        "keep.txt stored last")
    cases["tree-unsorted"] = (fx.path, {"base": main_tip, "head": unsorted})
    odd_mode = off_form(((b"100644", b"conflict.txt"), (b"100664", b"keep.txt"), (b"100644", b"z.txt")),
                        "keep.txt stored with mode 100664")
    cases["tree-mode"] = (fx.path, {"base": main_tip, "head": odd_mode})
    fx.git("checkout", "-q", "-b", "edit-keep", main_tip)
    fx.write("keep.txt", b"k1\nk2 edited\nk3\n")
    edit_keep = fx.commit("edit keep")
    lossy_tree = fx.git("merge-tree", "--write-tree", unsorted, edit_keep).split("\n")[0]
    second = fx.git("commit-tree", lossy_tree, "-p", unsorted, "-p", edit_keep, "-m", "merge the edit")
    cases["tree-unsorted-input"] = (fx.path, {"base": unsorted, "head": second})
    # Member mode: the member commit's root tree lists .aiqt after keep.txt.
    fx.write(DEFAULT_DECLARATIONS, b"format-version = 1\n")
    declared_tip = fx.commit("an empty declaration file")
    aiqt = fx.git("rev-parse", declared_tip + ":.aiqt")
    member_tree = literal_tree(((b"100644", b"keep.txt", fx.blob(declared_tip, "keep.txt")),
                                (b"40000", b".aiqt", aiqt)))
    cases["member-tree-unsorted"] = (fx.path, {"base": edit_keep, "member": fx.git(
        "commit-tree", member_tree, "-p", edit_keep, "-m", ".aiqt stored last")})

    # A blob entry naming an object the gate has already read as a tree: a crafted side lists f.txt as a 100644
    # entry whose id is the tree of dir, which the recorded tree (read first) holds. The merge resolves the
    # modify/delete conflict on f.txt by deleting it, so merge-tree never reads the confused entry, and only
    # checking each object for the kind its entry claims, not only for its id, refuses it.
    fx = Fixture(root, "kind-confused")
    fx.write("f.txt", b"f\n")
    fx.write("dir/a", b"a\n")
    kind_base = fx.commit("base")
    fx.git("rm", "-q", "f.txt")
    kind_main = fx.commit("main deletes f.txt")
    dir_tree = fx.blob(kind_main, "dir")
    confused_raw = b"40000 dir\x00" + bytes.fromhex(dir_tree) + b"100644 f.txt\x00" + bytes.fromhex(dir_tree)
    confused = fx.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", data=confused_raw)
    kind_side = fx.git("commit-tree", confused, "-p", kind_base, "-m", "f.txt names the tree of dir")
    kind_merge = fx.git("commit-tree", fx.git("rev-parse", kind_main + "^{tree}"), "-p", kind_main, "-p",
                        kind_side, "-m", "resolve the modify/delete conflict by deleting f.txt")
    cases["kind-confused"] = (fx.path, {"base": kind_main, "head": kind_merge})
    # The same confused side as the first input: the other input-tree leg.
    first_merge = fx.git("commit-tree", fx.git("rev-parse", kind_main + "^{tree}"), "-p", kind_side, "-p",
                         kind_main, "-m", "the confused side first, f.txt deleted")
    cases["kind-confused-first-input"] = (fx.path, {"base": kind_main, "head": first_merge})
    # The recorded-tree leg: the recorded tree itself lists f.txt as a blob naming the tree of dir. It is the
    # first tree read for a merge, so its two kinds of that id are asked for together and the read refuses it
    # with or without the object-kind rule; with no blob read at all the extra f.txt is only a finding.
    recorded_confused = fx.git("commit-tree", confused, "-p", kind_main, "-p", kind_base, "-m",
                               "recorded tree lists f.txt as the tree of dir")
    cases["kind-confused-recorded"] = (fx.path, {"base": kind_main, "head": recorded_confused})
    # The merge-base leg: the merge base lists f.txt as a blob naming the tree of dir, and both sides replace
    # f.txt with the same blob, so the merge is clean and merge-tree never reads the merge base's entry.
    fx = Fixture(root, "kind-confused-base")
    fx.write("f.txt", b"real\n")
    fx.write("dir/a", b"a\n")
    fx.commit("a real tree")
    real_tree = fx.git("rev-parse", "HEAD^{tree}")
    base_dir = fx.git("rev-parse", "HEAD:dir")
    base_raw = b"40000 dir\x00" + bytes.fromhex(base_dir) + b"100644 f.txt\x00" + bytes.fromhex(base_dir)
    base_tree = fx.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", data=base_raw)
    confused_base = fx.git("commit-tree", base_tree, "-m", "merge base: f.txt names the tree of dir")
    base_left = fx.git("commit-tree", real_tree, "-p", confused_base, "-m", "left replaces f.txt")
    base_right = fx.git("commit-tree", real_tree, "-p", confused_base, "-m", "right replaces f.txt the same way")
    base_merge = fx.git("commit-tree", real_tree, "-p", base_left, "-p", base_right, "-m", "clean merge")
    cases["kind-confused-base"] = (fx.path, {"base": base_left, "head": base_merge})

    # A file/directory conflict resolved by keeping both entries git wrote: main edits a, the side replaces it
    # with the directory a/, and git moves main's a aside as a~HEAD (the label of the side it came from), where
    # merge-tree, given commit ids, names it a~<id>. The kept entry is mapped to the re-merge's name, never a
    # finding; one whose content differs from git's cannot be paired and is cannot-evaluate; a loss elsewhere in
    # the same merge is still a finding.
    fx = Fixture(root, "file-directory")
    fx.write("a", b"base\n")
    fx.write("keep.txt", b"kept\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    os.remove(os.path.join(fx.path, "a"))
    fx.write("a/sub", b"a is a directory on the side\n")
    fx.commit("side turns a into a directory")
    fx.git("checkout", "-q", "main")
    fx.write("a", b"base, edited on main\n")
    fd_main = fx.commit("main edits a")
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    fd_kept = fx.git("rev-parse", "HEAD")
    cases["conflict-name-kept"] = (fx.path, {"base": fd_main, "head": fd_kept})
    fx.git("checkout", "-q", "-b", "fd-edited", fd_main)
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    fx.write("a~HEAD", b"base, edited on main, then by hand\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    cases["conflict-name-edited"] = (fx.path, {"base": fd_main, "head": fx.git("rev-parse", "HEAD")})
    fx.git("checkout", "-q", "-b", "fd-lossy", fd_main)
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    fx.write("keep.txt", b"kept, changed in the merge\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    cases["conflict-name-lossy"] = (fx.path, {"base": fd_main, "head": fx.git("rev-parse", "HEAD")})
    # Both git's name a~HEAD and the re-merge's own name a~<id> kept with git's content: the re-merge's name is
    # already in the recorded tree, so a~HEAD cannot be paired one to one (a duplicate), cannot-evaluate.
    fx.git("checkout", "-q", "-b", "fd-duplicate", fd_main)
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    fx.write("a~" + fd_main, b"base, edited on main\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    cases["conflict-name-duplicate"] = (fx.path, {"base": fd_main, "head": fx.git("rev-parse", "HEAD")})

    # A name that already exists is never one git generated for the conflict: the merge base holds a and a~old,
    # the side turns a into a directory and deletes a~old, main edits a to a~old's content, and the merge drops
    # git's a~HEAD and keeps the deleted a~old. a~old is in the merge base, so it is judged as any other path: the
    # side's deletion was lost, a finding.
    fx = Fixture(root, "file-directory-deleted")
    fx.write("a", b"base\n")
    fx.write("a~old", b"an older copy of a\n")
    fx.write("keep.txt", b"kept\n")
    fx.commit("base")
    fx.git("checkout", "-q", "-b", "side")
    os.remove(os.path.join(fx.path, "a"))
    os.remove(os.path.join(fx.path, "a~old"))
    fx.write("a/sub", b"a is a directory on the side\n")
    fx.commit("side turns a into a directory and deletes a~old")
    fx.git("checkout", "-q", "main")
    fx.write("a", b"an older copy of a\n")
    deleted_main = fx.commit("main edits a to a~old's content")
    fx.git("merge", "-q", "--no-edit", "side", ok=(1,))
    os.remove(os.path.join(fx.path, "a~HEAD"))
    fx.write("a~old", b"an older copy of a\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    cases["conflict-name-deleted"] = (fx.path, {"base": deleted_main, "head": fx.git("rev-parse", "HEAD")})

    # An older merge whose auto-merged blob is missing, below a newer merge whose re-merge writes that same blob
    # (and whose recorded tree holds a declared hand edit instead): the newer merge is judged first, so its
    # re-merge must not supply the older merge's missing source object.
    fx = Fixture(root, "regenerated")
    fx.write("f.txt", b"1\n2\n3\n4\n5\n")
    start = fx.commit("base")
    fx.git("checkout", "-q", "-b", "left")
    fx.write("f.txt", b"1L\n2\n3\n4\n5\n")
    fx.commit("left")
    fx.git("checkout", "-q", "-b", "right", start)
    fx.write("f.txt", b"1\n2\n3\n4\n5R\n")
    right = fx.commit("right")
    fx.git("checkout", "-q", "left")
    fx.git("merge", "-q", "--no-edit", right)
    older = fx.git("rev-parse", "HEAD")
    lost = fx.blob(older, "f.txt")
    fx.write("f.txt", b"1\n2\n3\n4\n5\n")
    undo = fx.commit("undo both edits")
    fx.git("checkout", "-q", "-b", "left2")
    fx.write("f.txt", b"1L\n2\n3\n4\n5\n")
    fx.commit("left again")
    fx.git("checkout", "-q", "-b", "right2", undo)
    fx.write("f.txt", b"1\n2\n3\n4\n5R\n")
    right2 = fx.commit("right again")
    fx.git("checkout", "-q", "left2")
    fx.git("merge", "-q", "--no-commit", right2)
    fx.write("f.txt", b"1L\n2\n3\n4\n5R\nhand edit\n")
    fx.git("add", "-A")
    fx.git("commit", "-q", "--no-edit")
    newer = fx.git("rev-parse", "HEAD")
    regenerated = os.path.join(root, "regenerated.toml")
    with open(regenerated, "w", encoding="utf-8") as handle:
        handle.write('format-version = 1\n\n[[resolution]]\nmerge = "{}"\npath = "f.txt"\nmode = "100644"\n'
                     'object = "{}"\nreason = "fixture"\n'.format(newer, fx.blob(newer, "f.txt")))
    os.remove(os.path.join(fx.path, ".git", "objects", lost[:2], lost[2:]))
    cases["regenerated-older"] = (fx.path, {"base": start, "head": older, "declarations": regenerated})
    cases["regenerated-range"] = (fx.path, {"base": start, "head": newer, "declarations": regenerated})

    # Git older than the floor: a stand-in that reports 2.30.0 and passes every other call to the real git.
    real_git = shutil.which("git")
    stub = os.path.join(root, "old-git")
    with open(stub, "w", encoding="utf-8") as handle:
        handle.write('#!/bin/sh\nif [ "$1" = "version" ]; then echo "git version 2.30.0"; exit 0; fi\n'
                     'exec "{}" "$@"\n'.format(real_git))
    os.chmod(stub, 0o755)
    cases["git-too-old"] = (fixtures.incident[0].path, {"base": fixtures.incident[3],
                                                        "head": fixtures.incident[3], "git": stub})
    return cases


def has(lines, text):
    return any(text in line for line in lines)


EXPECT = {
    # case: (exit code, text every expected line holds, the rule whose removal must turn the case red)
    "incident": (1, ("gone1.txt", "dir/gone2.txt", "img.bin", "lnk", "tool.sh", "sub"), "compare"),
    "incident-replace": (1, ("gone1.txt",), "raw-parents"),
    "clean": (0, ("matches the re-merge",), None),
    "rename": (0, ("matches the re-merge",), None),
    "conflict": (0, ("a conflict git reports",), "conflict-exempt"),
    "declared": (0, ("declared hand resolution",), None),
    "undeclared": (1, ("other.txt: re-merge",), None),
    "declared-wrong": (1, ("other.txt: declared",), "declaration-exact"),
    "declared-stale": (1, ("stale declaration",), "declaration-stale"),
    "declaration-malformed": (2, (DECLARATION_UNREADABLE, "not TOML"), "declaration-read"),
    "declaration-directory": (2, (DECLARATION_UNREADABLE, "not a regular file"), "declaration-read"),
    "declaration-absent": (2, (DECLARATION_UNREADABLE, "absent"), "declaration-read"),
    "squash-undeclared": (2, (SQUASH_UNDECLARED,), "squash-undeclared"),
    "squash-declared": (0, ("matches the re-merge",), None),
    "squash-wrong-destination": (1, ("declared squash destination",), None),
    "octopus": (2, (OCTOPUS,), "octopus"),
    "criss-cross": (2, (CRISS_CROSS,), "criss-cross"),
    "unrelated": (2, (UNRELATED,), "unrelated"),
    "missing-parent": (2, (MISSING_OBJECT,), "missing-parent"),
    "git-too-old": (2, (GIT_TOO_OLD,), "git-floor"),
    "sha256-clean": (0, ("matches the re-merge",), None),
    "sha256-incident": (1, ("a.txt: re-merge",), None),
    "member-train-squash": (0, ("0 merge(s) judged, 1 other commit(s)",), None),
    "member-mode": (1, ("gone1.txt", "member mode"), None),
    "squash-named-outside": (2, (SQUASH_UNJUDGED, "not in the range"), "squash-unjudged"),
    "squash-named-merge": (2, (SQUASH_UNJUDGED, "records 2 parent(s)"), "squash-unjudged"),
    "squash-source-descendant": (1, ("descendant of it",), "squash-source"),
    "declaration-one-parent": (1, ("not judged as a merge",), "declaration-unjudged"),
    "squash-declaration-on-merge": (1, ("stale squash declaration",), "declaration-unjudged"),
    "decl-resolution-table": (2, (DECLARATION_UNREADABLE, "array of tables"), "declaration-read"),
    "decl-squash-string": (2, (DECLARATION_UNREADABLE, "array of tables"), "declaration-read"),
    "decl-resolution-integer": (2, (DECLARATION_UNREADABLE, "array of tables"), "declaration-read"),
    "decl-id-length": (2, (DECLARATION_UNREADABLE, "40 hex digits"), "declaration-read"),
    "merge-tree-truncated": (2, (GIT_FAILURE, "truncated"), "merge-tree-framing"),
    "merge-tree-truncated-stage": (2, (GIT_FAILURE, "truncated"), None),
    "merge-tree-status-clean": (2, (GIT_FAILURE, "exited clean"), "merge-tree-status"),
    "merge-tree-status-conflicted": (2, (GIT_FAILURE, "no CONFLICT message"), "merge-tree-status"),
    "merge-tree-unnamed-stage": (2, (GIT_FAILURE, "no CONFLICT message names"), "merge-tree-status"),
    "header-parents": (0, ("0 merge(s) judged, 1 other commit(s)",), "header-parents"),
    "recorded-tree-missing": (2, (MISSING_OBJECT, "recorded tree"), "recorded-tree"),
    "blob-missing": (2, (MISSING_OBJECT, "missing or not a blob"), "blob-readable"),
    "repository-subdirectory": (2, (REPOSITORY_UNREADABLE, "not the top of a repository"), "repository-top"),
    "member-declarations": (0, ("declared hand resolution", "member mode"), "member-declarations"),
    "head-default-name": (0, ("matches the re-merge", "head HEAD"), None),
    "ambiguous-name": (2, (RANGE_UNREADABLE, "is ambiguous", "refs/tags/amb", "refs/heads/amb"), "name-unique"),
    "full-ref-name": (1, ("a.txt: re-merge", "member refs/heads/amb"), None),
    # A name stored twice is refused both by the listing and by the tree's form, so both rules are removed together.
    "duplicate-path": (2, (MALFORMED_TREE, "b.txt more than once"), ("tree-unique", "tree-canonical")),
    "slash-name": (2, (MALFORMED_TREE, "not its directory dir"), "tree-unique"),
    "empty-subtree": (1, ("empty: the re-merge", "no file below it differs"), "tree-ids"),
    "bare-default-mode": (2, (DECLARATION_UNREADABLE, "is a bare repository"), "bare-declarations"),
    "bare-member-mode": (0, ("matches the re-merge", "member mode"), None),
    "blob-wrong-content": (2, (MISSING_OBJECT, "does not hash to its id"), "object-hash"),
    "subst-base-history": (2, (MISSING_OBJECT, "of the base history", "does not hash to its id"),
                           "base-history-hash"),
    # The merge-base commit is both in the base history and the merge base, so both rules are removed together.
    "subst-merge-base-commit": (2, (MISSING_OBJECT, "does not hash to its id"),
                                ("base-history-hash", "merge-base-objects")),
    "subst-merge-base-tree": (2, (MISSING_OBJECT, "of the merge-base tree", "does not hash to its id"),
                              "merge-base-objects"),
    "subst-merge-base-blob": (2, (MISSING_OBJECT, "of the merge-base tree", "does not hash to its id"),
                              "merge-base-objects"),
    "subst-declaration": (2, (DECLARATION_UNREADABLE, "does not hash to its id"), "declaration-hash"),
    "tree-unsorted": (2, (MALFORMED_TREE, "of the recorded tree", "keep.txt out of git's tree order"),
                      "tree-canonical"),
    "tree-mode": (2, (MALFORMED_TREE, "of the recorded tree", "keep.txt with the mode 100664"), "tree-canonical"),
    "tree-unsorted-input": (2, (MALFORMED_TREE, "of the input tree", "keep.txt out of git's tree order"),
                            "tree-canonical"),
    "member-tree-unsorted": (2, (DECLARATION_UNREADABLE, "not in git's canonical form"), "tree-canonical"),
    "kind-confused": (2, (MISSING_OBJECT, "of the input tree", "missing or not a blob"), "object-kind"),
    "kind-confused-first-input": (2, (MISSING_OBJECT, "of the input tree", "missing or not a blob"), "object-kind"),
    "kind-confused-recorded": (2, (MISSING_OBJECT, "of the recorded tree", "missing or not a blob"), "blob-readable"),
    "kind-confused-base": (2, (MISSING_OBJECT, "of the merge-base tree", "missing or not a blob"), "object-kind"),
    "conflict-name-kept": (0, ("a~HEAD: git's own name for the conflict entry", "matches the re-merge"),
                           "conflict-names"),
    "conflict-name-edited": (2, (CONFLICT_NAME, "a~HEAD is named like an entry git moves aside"), "conflict-names"),
    "conflict-name-lossy": (1, ("keep.txt: re-merge", "a~HEAD: git's own name for the conflict entry"), None),
    "conflict-name-duplicate": (2, (CONFLICT_NAME, "a~HEAD is named like an entry git moves aside"),
                                "conflict-name-destination"),
    "conflict-name-deleted": (1, ("a~old: re-merge",), "conflict-name-taken"),
    "regenerated-older": (2, (MISSING_OBJECT, "of the recorded tree", "missing or not a blob"), None),
    "regenerated-range": (2, (MISSING_OBJECT, "of the recorded tree", "missing or not a blob"), "source-store"),
}

# Rules shown red by a check of their own in self_test rather than by a case above.
STANDALONE_RULES = ("private-commit-graph",)


def self_test():
    failures = []
    executed = []

    def check(name, ok, detail=""):
        executed.append(name)
        if not ok:
            failures.append("{}: {}".format(name, detail))

    if shutil.which("git") is None:
        print("SELF-TEST CANNOT EVALUATE: no git on PATH", file=sys.stderr)
        return 2
    root = tempfile.mkdtemp(prefix="merge-result-selftest-")
    try:
        cases = fixtures(root)
        incident_fx, incident_merge, incident_side, incident_main = fixtures.incident
        check("fixture-set", sorted(cases) == sorted(EXPECT), "cases {} vs {}".format(sorted(cases), sorted(EXPECT)))
        removed = {one for _code, _texts, rule in EXPECT.values() if rule is not None
                   for one in ((rule,) if isinstance(rule, str) else rule)} | set(STANDALONE_RULES)
        check("every-rule-removed", removed == set(RULES), "rules never removed: {}".format(
            sorted(set(RULES) - removed)))
        for name in sorted(EXPECT):
            if name not in cases:
                continue
            repo, kwargs = cases[name]
            kwargs = dict(kwargs)
            graft = kwargs.pop("graft", None)
            if graft == "replace":
                incident_fx.git("replace", "--graft", incident_merge, incident_side)
            want_code, texts, rule = EXPECT[name]

            def holds(result):
                code, lines = result
                return code == want_code and all(has(lines, t) for t in texts)
            try:
                result = evaluate(repo, **kwargs)
                check(name, holds(result), "exit {} lines {}".format(result[0], result[1]))
                if rule is not None:
                    rules = (rule,) if isinstance(rule, str) else rule
                    mutant = evaluate(repo, disabled=frozenset(rules), **kwargs)
                    check(name + " red without " + " and ".join(rules), not holds(mutant),
                          "still exit {} with the {} rule(s) removed".format(mutant[0], ", ".join(rules)))
            finally:
                if graft == "replace":
                    incident_fx.git("replace", "-d", incident_merge)
        # A grafts file is never read either.
        grafts = os.path.join(incident_fx.path, ".git", "info", "grafts")
        os.makedirs(os.path.dirname(grafts), exist_ok=True)
        with open(grafts, "w", encoding="ascii") as handle:
            handle.write("{} {}\n".format(incident_merge, incident_side))
        result = evaluate(incident_fx.path, base=incident_main, head=incident_merge)
        check("incident-grafts", result[0] == 1 and has(result[1], "gone1.txt"),
              "exit {} lines {}".format(*result))
        mutant = evaluate(incident_fx.path, base=incident_main, head=incident_merge,
                          disabled=frozenset({"raw-parents"}))
        check("incident-grafts red without raw-parents", mutant[0] != 1, "still exit {}".format(mutant[0]))
        os.remove(grafts)
        # The naive two-diff predicate (every path the result changed against one parent must be a path the
        # other side changed since the merge base) falsely fails the rename case; the re-merge does not.
        fx, rename_merge = fixtures.rename_check
        p1, p2 = fx.git("rev-parse", rename_merge + "^1"), fx.git("rev-parse", rename_merge + "^2")
        mb = fx.git("merge-base", p1, p2)

        def changed(a, b):
            return set(fx.git("diff", "--name-only", "--no-renames", a, b).split())
        naive = (changed(p1, rename_merge) - changed(mb, p2)) | (changed(p2, rename_merge) - changed(mb, p1))
        check("naive-rename-false-failure", "r2.txt" in naive, "naive predicate flagged {}".format(sorted(naive)))
        # An empty range passes with a note; the CLI propagates exit 1 and refuses a stray argument with exit 2.
        result = evaluate(incident_fx.path, base=incident_merge, head=incident_merge)
        check("empty-range", result[0] == 0 and has(result[1], "nothing to judge"), "exit {}".format(result[0]))
        result = evaluate(incident_fx.path, base="refs/remotes/origin/main", head=incident_merge)
        check("base-unresolved", result[0] == 2 and has(result[1], RANGE_UNREADABLE), "exit {}".format(result[0]))
        cli = [sys.executable, "-I", "-B", os.path.abspath(__file__)]
        proc = subprocess.run(cli + ["--repo", incident_fx.path, "--base", incident_main, "--head", incident_merge],
                              capture_output=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
        check("cli-finding", proc.returncode == 1 and b"FAIL " in proc.stdout, "exit {}".format(proc.returncode))
        proc = subprocess.run(cli + ["--self-testx"], capture_output=True, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
        check("cli-unknown-argument", proc.returncode == 2, "exit {}".format(proc.returncode))
        # Member mode from the command line: the member's -s ours merge of main fails; --head beside it is
        # refused.
        proc = subprocess.run(cli + ["--repo", incident_fx.path, "--base", incident_main, "--member",
                                     incident_merge], capture_output=True, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
        check("cli-member", proc.returncode == 1 and b"gone1.txt" in proc.stdout, "exit {}".format(proc.returncode))
        proc = subprocess.run(cli + ["--repo", incident_fx.path, "--member", incident_merge, "--head",
                                     incident_merge], capture_output=True, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
        check("cli-member-with-head", proc.returncode == 2, "exit {}".format(proc.returncode))
        proc = subprocess.run(cli + ["--repo", incident_fx.path, "--member", incident_merge, "--squash",
                                     incident_merge], capture_output=True, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
        check("cli-member-with-squash", proc.returncode == 2 and b"takes neither" in proc.stderr,
              "exit {}".format(proc.returncode))
        # An empty value for any option (an unset variable in the caller) is a usage error, never the default.
        for flag in ("--repo", "--base", "--head", "--declarations", "--member", "--squash"):
            args = {"--repo": incident_fx.path, "--base": incident_main, "--head": incident_merge}
            if flag in ("--member", "--squash"):
                args.pop("--head")
            args[flag] = ""
            proc = subprocess.run(cli + [part for pair in args.items() for part in pair], capture_output=True,
                                  timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, cwd=incident_fx.path)
            check("cli-empty" + flag, proc.returncode == 2 and b"is empty" in proc.stderr,
                  "exit {} stderr {!r}".format(proc.returncode, proc.stderr[:200]))
        # merge-tree output cut at every field boundary before its last CONFLICT message ends is git-failure
        # (real output of a conflicted merge); the whole output parses.
        fx, ours, theirs = fixtures.conflict_inputs
        settings = [arg for key, value in MERGE_SETTINGS for arg in ("-c", "{}={}".format(key, value))]
        proc = subprocess.run(["git", "-C", fx.path] + settings + ["merge-tree", "--write-tree", "-z", "--messages",
                                                                   ours, theirs],
                              env=git_env(), capture_output=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
        out = proc.stdout
        fields = out.split(b"\x00")
        last_conflict_end = 0
        index = fields.index(b"", 1) + 1
        while index < len(fields) - 1:
            count = int(fields[index])
            if fields[index + 1 + count].startswith(b"CONFLICT"):
                last_conflict_end = len(b"\x00".join(fields[:index + count + 3])) + 1
            index += count + 3
        cuts = [n + 1 for n in range(len(out)) if out[n:n + 1] == b"\x00" and n + 1 < last_conflict_end]

        def all_cuts_refused(disabled):
            refused = 0
            for cut in cuts:
                try:
                    parse_merge_tree(out[:cut], proc.returncode, disabled)
                except CannotEvaluate:
                    refused += 1
            return refused == len(cuts)
        try:
            whole = parse_merge_tree(out, proc.returncode)
            whole_ok = proc.returncode == 1 and b"conflict.txt" in whole[1]
        except CannotEvaluate as exc:
            whole_ok = "refused: {}".format(exc)
        check("merge-tree-whole-output", whole_ok is True, "exit {}: {}".format(proc.returncode, whole_ok))
        check("merge-tree-every-cut", len(cuts) >= 4 and all_cuts_refused(frozenset()),
              "{} cut(s) at field boundaries".format(len(cuts)))
        check("merge-tree-every-cut red without merge-tree-framing and merge-tree-status",
              not all_cuts_refused(frozenset({"merge-tree-framing", "merge-tree-status"})), "still all refused")
        # The private repository holds core.commitGraph false in its own configuration.
        for disabled in (frozenset(), frozenset({"private-commit-graph"})):
            scratch = tempfile.mkdtemp(dir=root)
            priv = make_private("git", scratch, "sha1", os.path.join(incident_fx.path, ".git"), disabled)
            value = priv.run(["config", "--get", "core.commitGraph"], ok=(0, 1)).stdout.strip()
            if disabled:
                check("private-commit-graph red without private-commit-graph", value != b"false", value)
            else:
                check("private-commit-graph", value == b"false", value)
        # A local error (no temporary directory can be made) is cannot-evaluate, never an exception.
        saved = tempfile.tempdir
        tempfile.tempdir = os.path.join(root, "no-such-directory")
        try:
            result = evaluate(incident_fx.path, base=incident_main, head=incident_merge)
            check("local-error", result[0] == 2 and has(result[1], GIT_FAILURE), "exit {}".format(result[0]))
        except Exception as exc:
            check("local-error", False, "raised {}".format(type(exc).__name__))
        finally:
            tempfile.tempdir = saved
    finally:
        shutil.rmtree(root, ignore_errors=True)
    check("fixtures-removed", not os.path.exists(root), root)
    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: " + failure)
        print("SELF-TEST FAIL: check_merge_result {} of {} check(s) failed".format(len(failures), len(executed)))
        return 1
    print("SELF-TEST PASS: check_merge_result {} checks (the incident, a clean merge, a rename edited on the "
          "other side, a hand-resolved conflict, declared, undeclared, wrong and stale resolutions, each "
          "cannot-evaluate kind, a missing blob, a --repo below the top, replacement refs and grafts ignored, "
          "declaration schema violations, merge-tree output cut or at odds with its exit status, member mode and "
          "its committed declarations, every empty option value refused, squash naming and source checks, an "
          "ambiguous revision name, a tree listing a path twice or under an unlisted directory, an empty subtree, "
          "a bare repository in the default mode, a blob whose content does not hash to its id, a substituted "
          "base-history commit, merge-base commit, merge-base tree, merge-base blob and member declaration blob, "
          "a recorded, input or member tree off git's canonical form, a missing blob a later re-merge writes, "
          "a blob entry of the recorded, either input or the merge-base tree naming an object read as a tree, "
          "a file/directory conflict kept as git named it, edited, beside a loss, kept twice, or kept under a name "
          "the merge base already holds, and each rule red when "
          "removed)".format(
              len(executed)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The gate starts git and reads its exit
# status (subprocess), so under an ignored SIGCHLD a failing git read would read as a pass; the guard runs
# at the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("check_merge_result.py") or main())
