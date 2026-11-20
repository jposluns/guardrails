#!/usr/bin/env python3
"""Refuse a merge resolution that drops lines a side added (the resolution-drops-a-side class).

  check_merge_resolution.py --left REV --right REV --tree REV [--omissions FILE] [--repo DIR]
                                  judge one resolution: REV of the first parent, the second parent and
                                  the resolved tree (a commit or tree name); the omission declaration is
                                  read from FILE (absent: no omission is declared)
  check_merge_resolution.py --commit REV [--repo DIR]
                                  judge the recorded two-parent merge commit REV: its parents, its tree
                                  and the omission declaration block in its message
  check_merge_resolution.py --audit-merges [--repo DIR]
                                  judge every two-parent merge commit reachable from HEAD and not from
                                  HEAD^1 (HEAD itself included), and every one reachable from HEAD and not
                                  from refs/remotes/origin/main when that ref resolves, each as --commit does
  check_merge_resolution.py --self-test
  check_merge_resolution.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

Exit: 0 every obligation held; 1 a finding; 2 cannot evaluate. DIR defaults to this file's repository.

WHAT IT HOLDS. The merge base of the two parents is computed independently and must be exactly one
readable commit. Every path either side changed since the merge base is judged, whether git reports it
as conflicted or merges it cleanly; a path only the resolution holds is not. For each judged path, outside
the generated exemption below, every line each side added since the merge base (an inserted or a
replacing line in a deterministic difflib line diff of the whole base blob against the whole side blob,
with autojunk off) must be present in the resolved blob. A path git did not report as conflicted whose
resolved blob is byte-identical to the only side that changed it (or to both sides) holds by
construction and is read no further; a conflicted path is always judged. Lines
split at LF only; a CR and the presence or absence of a final LF are part of the line, and bytes are
compared exactly. A side line counts as kept when the side-to-result diff aligns it, or else when an
unaligned result line holds the same bytes, consumed once per occurrence, so a duplicated line needs its
own occurrence. A result line that the base-to-result diff aligns with a base line this side deleted
never counts for that side, so a line a side moved is not kept by putting it back where it was. A line
both sides added counts once for both only where the two additions replace or follow the same base
lines (their base ranges meet); added in different places, the result must hold it once per side, and
each occurrence short of that is reported against the right side first, then the left.

The merge is also replayed with `git merge-tree --write-tree` in a temporary bare repository that borrows
the objects through an alternates file (no worktree, no index, no hooks, no info/attributes, the
attribute source pinned to the empty tree, the caller's attributes file ignored, rerere off, renormalize
off, rename detection pinned on), so no attribute file or merge driver anywhere can change the replay.
The replay names the conflicted paths (each must be a conflict type the gate supports) and gives git's
own merged tree, which shows where git carried one side's edits along a rename.

TAKING ONE SIDE WHOLE. When the resolved blob of a judged path is byte-identical to one side's blob (or
the path is absent in both), the other side's additions must all be in it already. Otherwise the run
fails (exit 1) whatever the omission declaration says.

KEEPING NONE OF A SIDE. When a side added lines to a judged path and the result keeps none of them, the
run fails (exit 1) whatever the omission declaration says, so changing one line of a result and then
declaring every line of the other side cannot stand in for taking that side whole.

DECLARED OMISSIONS. A JSON document, format-version 1, bound to the merge base and both parent commit ids:
  {"format-version": 1, "base": OID, "left": OID, "right": OID,
   "omissions": [{"path": P, "side": "left"|"right", "line": N, "text": T, "rationale": R}]}
Each entry names one added-line occurrence that the result drops: P the judged path, N its one-based line
number in that side's blob, T its exact text without the terminating LF (a CR kept), R a nonempty
reason. Unknown or missing fields, a duplicate key or entry, a wildcard path, a binding that names other
commits, and an entry that names no dropped addition (a stale entry) are cannot-evaluate (exit 2). A
recorded merge commit carries its declaration between a line Merge-Resolution-Omissions-Begin and a line
Merge-Resolution-Omissions-End in its message; tools/train_merge.py writes that block.

GENERATED EXEMPTION. Exactly the five outputs tools/gen_manifest.py recomputes (.gitattributes,
.aiqt/manifest.toml, .aiqt/release/root.txt, .aiqt/release/announce-snippet.txt and .aiqt/frozen.json)
skip the line, whole-side and keep-none rules, and only when the resolved tree passes that generator's
--check: the resolved tree is read into a fresh temporary repository (its objects borrowed through an
alternates file, every conversion attribute unset, so the blobs are written byte for byte), and the
tree's own tools/gen_manifest.py --check --root runs there. That generator must be the one git's
replayed merge holds, unconflicted; a resolution that edits it is a finding. The three release files,
and each other exempt output the exemption is claimed for, must also be canonical in the resolved tree:
regular, nonempty, strict UTF-8, LF line ends only, ending in LF. Generator drift or a noncanonical
output is a finding; a generator that cannot run is cannot-evaluate. tools/train_merge.py regenerates
the five outputs before it checks and records a member merge, so a train built through it passes here.

FAIL CLOSED (exit 2). A parent, tree or base that does not resolve or cannot be read, no merge base or more
than one, a shallow repository, an octopus or non-merge commit given to --commit, a merge-tree failure, a
conflict git reports for a rename, a directory, a submodule or any type this gate does not know, a
conflicted path that is absent from the base and from one side (a rename the gate cannot map), a path
git merged cleanly while one side deleted it and the other changed it, or that only one side added but
git's merged blob differs from that side's (a rename carrying the other side's edits), a symbolic link,
submodule or binary (NUL-bearing) blob on a judged path that does not hold by construction, a blob over
MAX_BLOB_BYTES, a .gitattributes in the base, either parent or the resolved tree that sets a merge
driver, a malformed or stale declaration, and an --audit-merges run on a merge-train-* branch when
refs/remotes/origin/main does not resolve or no merge commit is in range.

DISCLOSED LIMITS. This is a textual retention check: it does not show the kept lines are in a sensible
order, that the result builds or behaves, or that a replacement line was meant to survive (a deliberate
replacement needs a declared omission). Lines the resolution adds that neither side had are not judged,
so a conflict marker left in a result that also keeps both sides' lines passes.
The repository's config and the git binary resolved through PATH are trusted. The generated exemption
trusts the merged tools/gen_manifest.py but not the modules it imports: it shows that the outputs
describe the resolved inputs, not that the inputs kept both sides (the rules above cover those inputs).
On a pull request's test merge the --audit-merges range covers the merges the branch introduces; it never
sees a merge that a squash discarded before it ran.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_merge_resolution.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import collections
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = (".aiqt/manifest.toml", ".aiqt/release/root.txt", ".aiqt/release/announce-snippet.txt")
GENERATED_EXEMPT = RELEASE_FILES + (".gitattributes", ".aiqt/frozen.json")
GENERATOR = "tools/gen_manifest.py"
UPSTREAM_REF = "refs/remotes/origin/main"
GENERATED_TAG = "generated exemption: "
TRAIN_BRANCH_PREFIX = "merge-train-"
MAX_BLOB_BYTES = 8 * 1024 * 1024
MAX_DECLARATION_BYTES = 1024 * 1024
GIT_TIMEOUT = 300
GENERATOR_TIMEOUT = 900
OMISSIONS_FORMAT = 1
MESSAGE_BEGIN = "Merge-Resolution-Omissions-Begin"
MESSAGE_END = "Merge-Resolution-Omissions-End"
OID_RE = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
REV_RE = re.compile(r"[A-Za-z0-9_./^~@{}+-]{1,256}")
REGULAR_MODES = ("100644", "100755")
# The conflict types the line rules can judge. Any other CONFLICT type merge-tree reports (a rename,
# a directory, a submodule, a binary or one this gate does not know) is cannot-evaluate.
SUPPORTED_CONFLICTS = ("CONFLICT (contents)", "CONFLICT (add/add)", "CONFLICT (modify/delete)")
# Pinned for the replay so ambient configuration cannot change the inventory.
REPLAY_PINS = (
    "-c", "core.hooksPath=" + os.devnull,
    "-c", "core.attributesFile=" + os.devnull,
    "-c", "rerere.enabled=false",
    "-c", "merge.renormalize=false",
    "-c", "merge.renames=true",
    "-c", "merge.directoryRenames=conflict",
    "-c", "merge.conflictStyle=merge",
    "-c", "core.autocrlf=false",
)
DECLARATION_KEYS = ("format-version", "base", "left", "right", "omissions")
ENTRY_KEYS = ("path", "side", "line", "text", "rationale")


class CannotEvaluate(Exception):
    """An input the gate cannot judge: unreadable, unsupported or malformed. Exit 2, never a pass."""


def _git_env():
    """The caller's environment with every GIT_ variable dropped (no GIT_DIR, GIT_INDEX_FILE or
    GIT_CONFIG_* can redirect a read) and the system config off."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["LC_ALL"] = "C"
    return env


def git(repo, args, ok=(0,), stdin=None, pins=()):
    """Run git against repo (replacement objects off) and return the completed process."""
    argv = ["git", "--no-replace-objects", "-C", str(repo), "-c", "core.fsmonitor=false"] + list(pins) + list(args)
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("cannot run git {} ({})".format(args[0], exc)) from None
    if proc.returncode not in ok:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CannotEvaluate("git {} failed (exit {}): {}".format(
            args[0], proc.returncode, detail[-1] if detail else "no diagnostic"))
    return proc


def _check_rev(rev, what):
    if not isinstance(rev, str) or not REV_RE.fullmatch(rev) or rev.startswith("-"):
        raise CannotEvaluate("{} {!r} is not an acceptable revision name".format(what, rev))


def resolve(repo, rev, kind, what):
    """The full object id rev names, peeled to kind (commit or tree); cannot-evaluate when unreadable."""
    _check_rev(rev, what)
    proc = git(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{" + kind + "}"], ok=(0, 1))
    oid = proc.stdout.decode("ascii", "replace").strip()
    if proc.returncode != 0 or not OID_RE.fullmatch(oid):
        raise CannotEvaluate("{} {} does not resolve to a readable {}".format(what, rev, kind))
    return oid


def refuse_shallow(repo):
    proc = git(repo, ["rev-parse", "--is-shallow-repository"])
    if proc.stdout.strip() != b"false":
        raise CannotEvaluate("the repository is shallow (or git could not say it is not); a merge base "
                             "read from truncated history cannot be trusted; fetch the full history")


def merge_base(repo, left, right):
    proc = git(repo, ["merge-base", "--all", left, right], ok=(0, 1))
    bases = proc.stdout.decode("ascii", "replace").split()
    if proc.returncode != 0 or not bases:
        raise CannotEvaluate("no merge base of {} and {} could be read".format(left[:12], right[:12]))
    if len(bases) != 1:
        raise CannotEvaluate("{} merge bases of {} and {} (a criss-cross merge); this gate supports "
                             "exactly one".format(len(bases), left[:12], right[:12]))
    if not OID_RE.fullmatch(bases[0]):
        raise CannotEvaluate("merge base {!r} is not an object id".format(bases[0]))
    return bases[0]


def list_tree(repo, treeish):
    """{path: (mode, type, oid)} for every entry of treeish, recursively; paths decoded with
    surrogateescape so a non-UTF-8 name is kept, never dropped."""
    proc = git(repo, ["ls-tree", "-r", "-z", "--full-tree", treeish])
    entries = {}
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        head, sep, name = record.partition(b"\t")
        fields = head.split(b" ")
        if not sep or len(fields) != 3:
            raise CannotEvaluate("unparseable ls-tree record {!r}".format(record[:80]))
        mode, kind, oid = (field.decode("ascii", "replace") for field in fields)
        entries[name.decode("utf-8", "surrogateescape")] = (mode, kind, oid)
    return entries


def read_blob(repo, oid, where):
    proc = git(repo, ["cat-file", "-s", oid])
    try:
        size = int(proc.stdout.strip())
    except ValueError:
        raise CannotEvaluate("{}: unreadable blob size".format(where)) from None
    if size > MAX_BLOB_BYTES:
        raise CannotEvaluate("{}: blob of {} bytes is over the {} byte bound".format(where, size, MAX_BLOB_BYTES))
    data = git(repo, ["cat-file", "blob", oid]).stdout
    if len(data) != size:
        raise CannotEvaluate("{}: blob read returned {} of {} bytes".format(where, len(data), size))
    return data


def _borrowing_repo(repo, prefix, bare):
    """A fresh temporary repository (its path) that borrows repo's objects through an alternates file,
    in repo's object format, with no hooks, no info/attributes and no worktree attributes of its own."""
    objects = git(repo, ["rev-parse", "--path-format=absolute", "--git-path", "objects"]).stdout.decode(
        "utf-8", "surrogateescape").strip()
    object_format = git(repo, ["rev-parse", "--show-object-format"]).stdout.decode("ascii", "replace").strip()
    if object_format not in ("sha1", "sha256"):
        raise CannotEvaluate("unknown object format {!r}".format(object_format))
    scratch = tempfile.mkdtemp(prefix=prefix)
    try:
        git(scratch, ["init", "-q", "--template=", "--object-format=" + object_format]
            + (["--bare"] if bare else []) + ["."])
        git_dir = scratch if bare else os.path.join(scratch, ".git")
        os.makedirs(os.path.join(git_dir, "objects", "info"), exist_ok=True)
        with open(os.path.join(git_dir, "objects", "info", "alternates"), "w", encoding="utf-8") as handle:
            handle.write(objects + "\n")
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return scratch


def replay(repo, base, left, right):
    """(sorted conflicted paths, git's merged tree map) for merging left and right over base, replayed in
    a temporary bare repository that reads no attribute file (module docstring)."""
    scratch = _borrowing_repo(repo, "merge-resolution-replay-", bare=True)
    try:
        empty = git(scratch, ["hash-object", "-w", "-t", "tree", "--stdin"], stdin=b"").stdout.decode(
            "ascii", "replace").strip()
        if not OID_RE.fullmatch(empty):
            raise CannotEvaluate("could not write the empty tree for the attribute source")
        pins = REPLAY_PINS + ("-c", "attr.tree=" + empty, "--attr-source=" + empty)
        proc = git(scratch, ["merge-tree", "--write-tree", "-z", "--name-only", "--merge-base=" + base, left,
                             right], ok=(0, 1), pins=pins)
        parts = proc.stdout.split(b"\0")
        merged_oid = parts[0].decode("ascii", "replace") if parts else ""
        if not OID_RE.fullmatch(merged_oid):
            raise CannotEvaluate("merge-tree printed no result tree")
        merged = list_tree(scratch, merged_oid)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _conflicts(proc, parts), merged


def _conflicts(proc, parts):
    """The sorted conflicted paths of a merge-tree -z --name-only run."""
    if proc.returncode == 0:
        return []
    conflicted, index = set(), 1
    while index < len(parts) and parts[index] != b"":
        conflicted.add(parts[index].decode("utf-8", "surrogateescape"))
        index += 1
    index += 1
    while index < len(parts) and parts[index] != b"":
        try:
            count = int(parts[index])
        except ValueError:
            raise CannotEvaluate("unparseable merge-tree message record") from None
        if count < 0 or index + count + 2 >= len(parts):
            raise CannotEvaluate("truncated merge-tree message record")
        paths = [p.decode("utf-8", "surrogateescape") for p in parts[index + 1:index + 1 + count]]
        kind = parts[index + 1 + count].decode("utf-8", "replace")
        message = parts[index + 2 + count].decode("utf-8", "replace").strip()
        index += count + 3
        if kind.startswith("CONFLICT"):
            if kind not in SUPPORTED_CONFLICTS:
                raise CannotEvaluate("unsupported conflict {} on {}: {}".format(kind, ", ".join(paths), message))
            conflicted.update(paths)
    if not conflicted:
        raise CannotEvaluate("merge-tree reported a conflict but named no conflicted path")
    return sorted(conflicted)


def refuse_merge_drivers(repo, maps):
    """Cannot-evaluate when a .gitattributes in the base, either parent or the resolved tree selects a
    merge driver: the replay reads none, so the train's own merge could differ from it, and a resolution
    that brings one in would change later merges unseen."""
    for role in ("base", "left", "right", "result"):
        for path, (mode, kind, oid) in sorted(maps[role].items()):
            if path != ".gitattributes" and not path.endswith("/.gitattributes"):
                continue
            if kind != "blob":
                raise CannotEvaluate("{} {}: not a regular file".format(role, path))
            for line in read_blob(repo, oid, "{} {}".format(role, path)).decode("utf-8", "replace").splitlines():
                tokens = line.split()
                if tokens and not tokens[0].startswith("#") and any(
                        token.lstrip("-!") == "merge" or token.startswith("merge=") for token in tokens[1:]):
                    raise CannotEvaluate("{} {} sets a merge driver ({!r}); unsupported".format(role, path, line))


def split_lines(data):
    """The lines of data split at LF only, each keeping its LF; a final line without LF is kept as is."""
    if not data:
        return []
    parts = data.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def side_changes(base_lines, side_lines):
    """(additions, deleted, kept) of base -> side: additions [(one-based side line number, bytes,
    (i1, i2) the base range it replaces or follows)] for every inserted or replacing line; deleted and
    kept the sets of base indexes the side deleted (or replaced) and kept."""
    matcher = difflib.SequenceMatcher(None, base_lines, side_lines, autojunk=False)
    added, deleted, kept = [], set(), set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            kept.update(range(i1, i2))
            continue
        if tag in ("insert", "replace"):
            added.extend((j + 1, side_lines[j], (i1, i2)) for j in range(j1, j2))
        if tag in ("delete", "replace"):
            deleted.update(range(i1, i2))
    return added, deleted, kept


def added_lines(base_lines, side_lines):
    """[(one-based side line number, bytes)] for every inserted or replacing line of base -> side."""
    return [(number, line) for number, line, _span in side_changes(base_lines, side_lines)[0]]


def aligned_pairs(a_lines, b_lines):
    """{a index: b index} for every line the a -> b diff aligns."""
    matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    pairs = {}
    for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.update((i1 + k, j1 + k) for k in range(i2 - i1))
    return pairs


def missing_additions(additions, side_lines, result_lines, excluded=frozenset()):
    """The additions the result does not keep (see the module docstring for the occurrence rule).
    excluded: result indexes that never count as an unaligned occurrence (the result lines standing for
    base lines this side deleted)."""
    if not additions:
        return []
    matcher = difflib.SequenceMatcher(None, side_lines, result_lines, autojunk=False)
    aligned_side, aligned_result = set(), set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            aligned_side.update(range(i1, i2))
            aligned_result.update(range(j1, j2))
    pool = collections.Counter(line for j, line in enumerate(result_lines)
                               if j not in aligned_result and j not in excluded)
    missing = []
    for number, line in additions:
        if number - 1 in aligned_side:
            continue
        if pool[line] > 0:
            pool[line] -= 1
            continue
        missing.append((number, line))
    return missing


def _show(line):
    return repr(line)[1:]


def changed_paths(maps):
    """The sorted paths whose left or right entry differs from the base entry."""
    paths = set(maps["base"]) | set(maps["left"]) | set(maps["right"])
    return sorted(path for path in paths
                  if maps["left"].get(path) != maps["base"].get(path)
                  or maps["right"].get(path) != maps["base"].get(path))


def holds_by_construction(maps, path):
    """True when the resolved entry equals the only side that changed path, or both sides' equal entry:
    every line either side added is then aligned in the result and nothing is left to judge."""
    base, left, right, result = (maps[role].get(path) for role in ("base", "left", "right", "result"))
    if left == right:
        return result == left
    if left == base:
        return result == right
    if right == base:
        return result == left
    return False


def _shared_shortfall(lines, changes, per_side, blobs):
    """[(side, blob, number, bytes, need, have)] for lines both sides added in different places that the
    result holds fewer times than once per side (module docstring)."""
    left_add, right_add = changes["left"][0], changes["right"][0]
    texts = {line for _n, line, _s in left_add} & {line for _n, line, _s in right_add}
    kept_both = changes["left"][2] & changes["right"][2]
    short_entries = []
    for text in sorted(texts):
        lefts = [(number, span) for number, line, span in left_add if line == text]
        rights = [(number, span) for number, line, span in right_add if line == text]
        paired_left, paired_right = set(), set()
        for li, (_number, (a1, a2)) in enumerate(lefts):
            for ri, (_other, (b1, b2)) in enumerate(rights):
                if ri not in paired_right and a1 <= b2 and b1 <= a2:
                    paired_left.add(li)
                    paired_right.add(ri)
                    break
        need = (sum(1 for index in kept_both if lines["base"][index] == text)
                + len(lefts) + len(rights) - len(paired_right))
        have = sum(1 for line in lines["result"] if line == text)
        missing = {(side, entry[2]) for side in ("left", "right") for entry in per_side[side] if entry[3] == text}
        short = need - have - len(missing)
        if short <= 0:
            continue
        order = ([("right", n) for ri, (n, _s) in reversed(list(enumerate(rights))) if ri not in paired_right]
                 + [("left", n) for li, (n, _s) in reversed(list(enumerate(lefts))) if li not in paired_left]
                 + [("right", n) for ri, (n, _s) in reversed(list(enumerate(rights))) if ri in paired_right]
                 + [("left", n) for li, (n, _s) in reversed(list(enumerate(lefts))) if li in paired_left])
        order = [item for item in order if item not in missing]
        for side, number in order[:short]:
            short_entries.append((side, blobs[side], number, text, need, have))
    return short_entries


def judge_path(repo, path, maps, conflicted, merged):
    """The judgement of one judged, non-exempt path, a dict:
      dropped       [(side, side_blob, number, bytes)] additions the result does not keep
      shared_short  [(side, side_blob, number, bytes, need, have)] lines both sides added in different
                    places that the result holds too few times (each entry one occurrence short)
      whole_side    None or (kept_side, the other side's dropped list)
      none_kept     [(side, every dropped or short entry of that side)] for each side that added lines and
                    has none of them kept"""
    entries = {role: maps[role].get(path) for role in ("base", "left", "right", "result")}
    for role, entry in entries.items():
        if entry is not None and (entry[1] != "blob" or entry[0] not in REGULAR_MODES):
            raise CannotEvaluate("{}: the {} entry is {} {}; only regular files are supported".format(
                path, role, entry[1], entry[0]))
    present = [side for side in ("left", "right") if entries[side] is not None]
    if conflicted:
        if entries["base"] is None and len(present) < 2:
            raise CannotEvaluate("{}: absent from the merge base and from one side (a rename this gate "
                                 "cannot map); unsupported".format(path))
    elif len(present) == 1 and entries["base"] is None and merged.get(path) != entries[present[0]]:
        raise CannotEvaluate("{}: only the {} side added it, but git's merge holds other content there (a "
                             "rename carrying the other side's edits, which this gate cannot map); "
                             "unsupported".format(path, present[0]))
    elif len(present) == 1 and entries["base"] is not None and entries[present[0]] != entries["base"]:
        raise CannotEvaluate("{}: one side deleted it, the {} side changed it, and git merged it without a "
                             "conflict (a rename this gate cannot map); unsupported".format(path, present[0]))
    lines = {}
    for role, entry in entries.items():
        data = b"" if entry is None else read_blob(repo, entry[2], "{} {}".format(role, path))
        if b"\0" in data:
            raise CannotEvaluate("{}: the {} blob is binary (holds a NUL byte); unsupported".format(path, role))
        lines[role] = split_lines(data)
    base_to_result = aligned_pairs(lines["base"], lines["result"])
    changes = {side: side_changes(lines["base"], lines[side]) for side in ("left", "right")}
    blobs = {side: entries[side][2] if entries[side] else "(absent)" for side in ("left", "right")}
    dropped = []
    per_side = {}
    for side in ("left", "right"):
        additions, deleted, _kept = changes[side]
        excluded = frozenset(base_to_result[index] for index in deleted if index in base_to_result)
        missing = missing_additions([(number, line) for number, line, _span in additions], lines[side],
                                    lines["result"], excluded)
        per_side[side] = [(side, blobs[side], number, line) for number, line in missing]
        dropped.extend(per_side[side])
    shared_short = _shared_shortfall(lines, changes, per_side, blobs)
    none_kept = []
    for side in ("left", "right"):
        lost = per_side[side] + [entry[:4] for entry in shared_short if entry[0] == side]
        if changes[side][0] and len(lost) >= len(changes[side][0]):
            none_kept.append((side, lost))
    result_id = entries["result"][2] if entries["result"] else None
    left_id = entries["left"][2] if entries["left"] else None
    right_id = entries["right"][2] if entries["right"] else None
    whole_side = None
    if left_id != right_id:
        if result_id == left_id and per_side["right"]:
            whole_side = ("left", per_side["right"])
        elif result_id == right_id and per_side["left"]:
            whole_side = ("right", per_side["left"])
    return {"dropped": dropped, "shared_short": shared_short, "whole_side": whole_side, "none_kept": none_kept}


def _strict_object(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise CannotEvaluate("declaration: duplicate key {!r}".format(key))
        seen[key] = value
    return seen


def parse_declaration(text, base, left, right):
    """{(path, side, line): text} from a declaration document (None or empty text: no omission)."""
    if text is None or not text.strip():
        return {}
    if len(text.encode("utf-8", "surrogatepass")) > MAX_DECLARATION_BYTES:
        raise CannotEvaluate("declaration: over {} bytes".format(MAX_DECLARATION_BYTES))
    try:
        document = json.loads(text, object_pairs_hook=_strict_object)
    except ValueError as exc:
        raise CannotEvaluate("declaration: not JSON ({})".format(exc)) from None
    if not isinstance(document, dict) or sorted(document) != sorted(DECLARATION_KEYS):
        raise CannotEvaluate("declaration: the top level must hold exactly the keys {}".format(", ".join(DECLARATION_KEYS)))
    if type(document["format-version"]) is not int or document["format-version"] != OMISSIONS_FORMAT:
        raise CannotEvaluate("declaration: format-version must be {}".format(OMISSIONS_FORMAT))
    for key, expected in (("base", base), ("left", left), ("right", right)):
        if document[key] != expected:
            raise CannotEvaluate("declaration: {} is {!r}, but this merge's {} is {}".format(
                key, document[key], key, expected))
    if not isinstance(document["omissions"], list):
        raise CannotEvaluate("declaration: omissions must be a list")
    declared = {}
    for number, entry in enumerate(document["omissions"], 1):
        where = "declaration: omission {}".format(number)
        if not isinstance(entry, dict) or sorted(entry) != sorted(ENTRY_KEYS):
            raise CannotEvaluate("{}: must hold exactly the keys {}".format(where, ", ".join(ENTRY_KEYS)))
        path, side, line, line_text, rationale = (entry[key] for key in ENTRY_KEYS)
        if not isinstance(path, str) or not path or any(c in path for c in "*?[") or path.endswith("/"):
            raise CannotEvaluate("{}: path must name one file exactly (no wildcard, no directory)".format(where))
        if side not in ("left", "right"):
            raise CannotEvaluate("{}: side must be left or right".format(where))
        if type(line) is not int or line < 1:
            raise CannotEvaluate("{}: line must be a positive integer".format(where))
        if not isinstance(line_text, str) or "\n" in line_text:
            raise CannotEvaluate("{}: text must be one line without its LF".format(where))
        if not isinstance(rationale, str) or not rationale.strip():
            raise CannotEvaluate("{}: rationale must be nonempty".format(where))
        key = (path, side, line)
        if key in declared:
            raise CannotEvaluate("{}: duplicates an earlier entry for {} {} line {}".format(where, path, side, line))
        declared[key] = line_text
    return declared


def declaration_from_message(message):
    """The declaration text a commit message carries between the marker lines, or None."""
    lines = message.split("\n")
    begins = [i for i, line in enumerate(lines) if line.strip() == MESSAGE_BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == MESSAGE_END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise CannotEvaluate("the commit message must carry at most one well-formed omission block")
    return "\n".join(lines[begins[0] + 1:ends[0]])


def _line_text(line):
    return line[:-1] if line.endswith(b"\n") else line


def canonical_problem(data):
    if not data:
        return "is empty"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "is not UTF-8"
    if b"\r" in data:
        return "holds a CR (not canonical LF)"
    if not data.endswith(b"\n"):
        return "does not end with LF (not canonical LF)"
    return None


def validate_generated(repo, tree, result_map, claimed, merged, conflicted):
    """Findings for the generated exemption: the release files and every other claimed output are
    canonical, the generator is git's own unconflicted merge of it, and that generator's --check passes on
    a byte-exact copy of the resolved tree."""
    findings = []
    if GENERATOR in conflicted:
        raise CannotEvaluate("{} conflicted in the replay, so no generator both sides agree on can vouch for "
                             "the generated outputs".format(GENERATOR))
    if result_map.get(GENERATOR) != merged.get(GENERATOR):
        findings.append(GENERATED_TAG + "{}: the resolved generator differs from git's merge of the two sides; the generated "
                        "exemption trusts only the merged generator".format(GENERATOR))
    for path in RELEASE_FILES + tuple(path for path in claimed if path not in RELEASE_FILES):
        entry = result_map.get(path)
        if entry is None or entry[1] != "blob" or entry[0] != "100644":
            findings.append(GENERATED_TAG + "{}: generated output missing from the result or not a regular file".format(path))
            continue
        problem = canonical_problem(read_blob(repo, entry[2], "result " + path))
        if problem:
            findings.append(GENERATED_TAG + "{}: generated output {}".format(path, problem))
    entry = result_map.get(GENERATOR)
    if entry is None or entry[1] != "blob":
        raise CannotEvaluate("{} is absent from the resolved tree, so the generated outputs cannot be "
                             "recomputed".format(GENERATOR))
    scratch = _borrowing_repo(repo, "merge-resolution-candidate-", bare=False)
    try:
        os.makedirs(os.path.join(scratch, ".git", "info"), exist_ok=True)
        with open(os.path.join(scratch, ".git", "info", "attributes"), "w", encoding="utf-8") as handle:
            handle.write("* -text !eol -filter -ident !working-tree-encoding\n")
        git(scratch, ["read-tree", tree])
        git(scratch, ["checkout-index", "-a", "-q"], pins=("-c", "core.autocrlf=false", "-c", "core.symlinks=true"))
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-B", os.path.join(scratch, GENERATOR), "--check", "--root", scratch],
                cwd=scratch, capture_output=True, env=_git_env(), timeout=GENERATOR_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CannotEvaluate("cannot run {} --check ({})".format(GENERATOR, exc)) from None
        output = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip().splitlines()
        if proc.returncode == 1:
            findings.append(GENERATED_TAG + "{} --check reports drift on the resolved tree: {}".format(
                GENERATOR, " | ".join(output[-5:]) or "no output"))
        elif proc.returncode != 0:
            raise CannotEvaluate("{} --check could not evaluate the resolved tree (exit {}): {}".format(
                GENERATOR, proc.returncode, " | ".join(output[-5:]) or "no output"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return findings


def judge(repo, left_rev, right_rev, tree_rev, declaration_text):
    """(findings, notes) for one resolution; raises CannotEvaluate when it cannot be judged."""
    refuse_shallow(repo)
    left = resolve(repo, left_rev, "commit", "left parent")
    right = resolve(repo, right_rev, "commit", "right parent")
    tree = resolve(repo, tree_rev, "tree", "resolved tree")
    base = merge_base(repo, left, right)
    resolve(repo, base, "tree", "merge base")
    maps = {"base": list_tree(repo, base), "left": list_tree(repo, left),
            "right": list_tree(repo, right), "result": list_tree(repo, tree)}
    refuse_merge_drivers(repo, maps)
    declared = parse_declaration(declaration_text, base, left, right)
    conflicted, merged = replay(repo, base, left, right)
    changed = changed_paths(maps)
    # A conflicted path is always judged (as before this rule widened the scope); another changed path
    # is judged unless it holds by construction.
    judged = sorted(set(conflicted) | {path for path in changed if not holds_by_construction(maps, path)})
    findings, notes, used = [], [], set()
    notes.append("merge base {}, left {}, right {}, resolved tree {}: {} conflicted path(s); {} path(s) changed "
                 "by a side, {} of them judged line by line".format(
                     base[:12], left[:12], right[:12], tree[:12], len(conflicted), len(changed),
                     len([path for path in judged if path not in GENERATED_EXEMPT])))
    exempt = [path for path in judged if path in GENERATED_EXEMPT]
    for path in judged:
        if path in GENERATED_EXEMPT:
            continue
        verdict = judge_path(repo, path, maps, path in conflicted, merged)
        if verdict["whole_side"] is not None:
            kept, lost = verdict["whole_side"]
            other = "right" if kept == "left" else "left"
            findings.append("{}: the result is the {} version whole and drops {} line(s) the {} side added; "
                            "taking one side whole is refused unless that side already holds the other "
                            "side's additions, and a declared omission does not waive it".format(
                                path, kept, len(lost), other))
            for side, blob, number, line in lost:
                used.add((path, side, number))
                findings.append("  {} line {} (blob {}): {}".format(side, number, blob[:12], _show(line)))
            continue
        refused_sides = set()
        for side, lost in verdict["none_kept"]:
            refused_sides.add(side)
            findings.append("{}: the result keeps none of the {} line(s) the {} side added; dropping every "
                            "addition of one side is refused, and a declared omission does not waive it".format(
                                path, len(lost), side))
            for _side, blob, number, line in lost:
                used.add((path, side, number))
                findings.append("  {} line {} (blob {}): {}".format(side, number, blob[:12], _show(line)))
        short = {(entry[0], entry[2]): entry for entry in verdict["shared_short"]}
        for side, blob, number, line in verdict["dropped"] + [entry[:4] for entry in verdict["shared_short"]]:
            if side in refused_sides:
                continue
            key = (path, side, number)
            text = declared.get(key)
            if text is not None and text.encode("utf-8", "surrogateescape") == _line_text(line):
                used.add(key)
                notes.append("declared omission: {} {} line {}: {}".format(path, side, number, _show(line)))
                continue
            if (side, number) in short:
                need, have = short[(side, number)][4:]
                findings.append("{}: {} line {} (blob {}) was added by both sides in different places, but the "
                                "result holds it {} time(s) where the base lines both sides kept and each "
                                "side's additions need {}: {}".format(
                                    path, side, number, blob[:12], have, need, _show(line)))
                continue
            findings.append("{}: {} line {} (blob {}) added since the merge base is missing from the result: "
                            "{}".format(path, side, number, blob[:12], _show(line)))
    stale = sorted(key for key in declared if key not in used)
    if stale:
        raise CannotEvaluate("declaration: {} entr(ies) name no dropped addition (stale or mistyped): {}".format(
            len(stale), "; ".join("{} {} line {}".format(*key) for key in stale)))
    if exempt:
        notes.append("generated exemption claimed for: {}".format(", ".join(exempt)))
        findings.extend(validate_generated(repo, tree, maps["result"], exempt, merged, conflicted))
    return findings, notes


def read_commit(repo, rev):
    """(left, right, tree, message) of a two-parent merge commit."""
    oid = resolve(repo, rev, "commit", "merge commit")
    raw = git(repo, ["cat-file", "commit", oid]).stdout
    head, sep, body = raw.partition(b"\n\n")
    parents, tree = [], None
    for line in head.split(b"\n"):
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii", "replace"))
        elif line.startswith(b"tree "):
            tree = line[5:].decode("ascii", "replace")
    if len(parents) != 2 or tree is None:
        raise CannotEvaluate("commit {} has {} parent(s); only a two-parent merge is supported".format(
            oid[:12], len(parents)))
    try:
        message = body.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("commit {}: message is not UTF-8".format(oid[:12])) from None
    return parents[0], parents[1], tree, message


def judge_commit(repo, rev):
    left, right, tree, message = read_commit(repo, rev)
    return judge(repo, left, right, tree, declaration_from_message(message))


def _upstream(repo):
    """The commit refs/remotes/origin/main names, or None when that ref does not resolve."""
    proc = git(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", UPSTREAM_REF + "^{commit}"],
               ok=(0, 1))
    oid = proc.stdout.decode("ascii", "replace").strip()
    return oid if proc.returncode == 0 and OID_RE.fullmatch(oid) else None


def _merges_in(repo, head, exclude):
    """The two-parent merge commits reachable from head and not from exclude, oldest first."""
    octopus = git(repo, ["rev-list", "--min-parents=3", head, "^" + exclude]).stdout.split()
    if octopus:
        raise CannotEvaluate("octopus merge {} in the audited range; unsupported".format(octopus[0].decode()[:12]))
    return [m.decode("ascii") for m in git(repo, ["rev-list", "--min-parents=2", "--reverse", "--topo-order",
                                                  head, "^" + exclude]).stdout.split()]


def audit_merges(repo):
    """(findings, notes) over every merge commit in HEAD^1..HEAD (HEAD included) and, when
    refs/remotes/origin/main resolves, in origin/main..HEAD; a merge-train-* branch needs that ref and at
    least one merge in range."""
    refuse_shallow(repo)
    head = resolve(repo, "HEAD", "commit", "HEAD")
    branch = git(repo, ["symbolic-ref", "-q", "--short", "HEAD"], ok=(0, 1)).stdout.decode(
        "utf-8", "surrogateescape").strip()
    on_train = branch.startswith(TRAIN_BRANCH_PREFIX)
    upstream = _upstream(repo)
    if on_train and upstream is None:
        raise CannotEvaluate("HEAD is on train branch {} but {} does not resolve, so the train's member "
                             "merges cannot be found; fetch origin/main".format(branch, UPSTREAM_REF))
    raw = git(repo, ["cat-file", "commit", head]).stdout.partition(b"\n\n")[0]
    parents = [line[7:].decode("ascii") for line in raw.split(b"\n") if line.startswith(b"parent ")]
    ranges, merges = [], []
    for exclude, label in ((upstream, UPSTREAM_REF), (parents[0] if parents else None, "HEAD^1")):
        if exclude is None:
            continue
        ranges.append("{} ({})..{}".format(label, exclude[:12], head[:12]))
        merges.extend(merge for merge in _merges_in(repo, head, exclude) if merge not in merges)
    if not ranges:
        return [], ["HEAD {} is a root commit and {} does not resolve: no merge to audit".format(
            head[:12], UPSTREAM_REF)]
    if on_train and not merges:
        raise CannotEvaluate("HEAD is on train branch {} but no merge commit is in {}; a train's member "
                             "merges must be in range".format(branch, " or ".join(ranges)))
    findings, notes = [], []
    for merge in merges:
        found, said = judge_commit(repo, merge)
        notes.extend("{}: {}".format(merge[:12], line) for line in said)
        findings.extend("merge {}: {}".format(merge[:12], line) for line in found)
    notes.append("audited {} merge commit(s) in {}".format(len(merges), " and ".join(ranges)))
    return findings, notes


def report(findings, notes):
    for line in notes:
        print("NOTE: " + line)
    for line in findings:
        print("FAIL: " + line)
    if findings:
        generated = [line for line in findings if GENERATED_TAG in line]
        lines = [line for line in findings if GENERATED_TAG not in line]
        kinds = []
        if lines:
            kinds.append("the resolution drops lines a side added")
        if generated:
            kinds.append("a generated output the exemption covers does not match its generator or is not "
                         "canonical")
        print("RESULT: FAIL, {} (exit 1)".format("; and ".join(kinds) or "see the FAIL lines above"))
        return 1
    print("PASS: every line each side added to a path either side changed is kept or declared, and every "
          "generated output the exemption covers matches its generator")
    return 0


def run(argv):
    """Exit code for a non-self-test argument list."""
    options, index = {}, 0
    valued = ("--left", "--right", "--tree", "--omissions", "--repo", "--commit")
    while index < len(argv):
        token = argv[index]
        if token == "--audit-merges" and token not in options:
            options[token] = True
            index += 1
            continue
        if token in valued and token not in options and index + 1 < len(argv) \
                and not argv[index + 1].startswith("-"):
            options[token] = argv[index + 1]
            index += 2
            continue
        print("error: unrecognized or repeated argument {!r}; fail closed".format(token), file=sys.stderr)
        return 2
    repo = Path(options.get("--repo", str(ROOT)))
    modes = [key for key in ("--left", "--commit", "--audit-merges") if key in options]
    try:
        if modes == ["--left"] and "--right" in options and "--tree" in options:
            declaration = None
            if "--omissions" in options:
                try:
                    with open(options["--omissions"], "rb") as handle:
                        raw = handle.read(MAX_DECLARATION_BYTES + 1)
                    declaration = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise CannotEvaluate("cannot read the omission declaration ({})".format(exc)) from None
            findings, notes = judge(repo, options["--left"], options["--right"], options["--tree"], declaration)
        elif modes == ["--commit"] and not {"--right", "--tree", "--omissions"} & set(options):
            findings, notes = judge_commit(repo, options["--commit"])
        elif modes == ["--audit-merges"] and not {"--right", "--tree", "--omissions"} & set(options):
            findings, notes = audit_merges(repo)
        else:
            print("error: give --left/--right/--tree, --commit or --audit-merges; fail closed", file=sys.stderr)
            return 2
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(findings, notes)


# ---------------------------------------------------------------------------------------------------
# Self-test. The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id
# record the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "merge-resolution-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


class _Fixture:
    """A throwaway bare-object repository whose commits are built with plumbing, no worktree."""

    def __init__(self, directory):
        self.repo = directory
        self.count = 0
        os.makedirs(directory)
        self.git(["init", "-q", "--template=", "."])

    def git(self, args, stdin=None, extra=None):
        env = _git_env()
        env.update({"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
        env.update(extra or {})
        proc = subprocess.run(["git", "-C", self.repo] + args, input=stdin, capture_output=True, env=env,
                              timeout=GIT_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError("fixture git {} failed: {}".format(args, proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("utf-8").strip()

    def tree(self, files):
        self.count += 1
        index = os.path.join(self.repo, ".git", "fixture-index-{}".format(self.count))
        for path, data in sorted(files.items()):
            mode = "100644"
            if isinstance(data, tuple):
                mode, data = data
            oid = self.git(["hash-object", "-w", "--stdin"], stdin=data)
            self.git(["update-index", "--add", "--cacheinfo", "{},{},{}".format(mode, oid, path)],
                     extra={"GIT_INDEX_FILE": index})
        if not files:
            return self.git(["mktree"], stdin=b"")
        return self.git(["write-tree"], extra={"GIT_INDEX_FILE": index})

    def commit(self, files, parents=(), message="fixture"):
        args = ["commit-tree", self.tree(files)]
        for parent in parents:
            args += ["-p", parent]
        return self.git(args + ["-m", message])

    def commit_tree(self, tree, parents, message):
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        return self.git(args, stdin=message.encode("utf-8"))

    def tree_of(self, rev):
        return self.git(["rev-parse", rev + "^{tree}"])


def _lines(*items):
    return "".join(item + "\n" for item in items).encode("utf-8")


BASE_LINES = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _variant(changes, extra_after=None):
    lines = list(BASE_LINES)
    for index, replacement in changes.items():
        lines[index:index + 1] = replacement
    if extra_after:
        position = lines.index(extra_after[0]) + 1
        lines[position:position] = extra_after[1]
    return _lines(*lines)


def _judge_quiet(repo, left, right, tree, declaration=None):
    """(exit code, findings and notes text) of judge() for the fixture inputs."""
    try:
        findings, notes = judge(repo, left, right, tree, declaration)
    except CannotEvaluate as exc:
        return 2, "CANNOT EVALUATE: " + str(exc)
    return (1 if findings else 0), "\n".join(notes + findings)


FIXTURE_GENERATOR = '''import hashlib, pathlib, subprocess, sys
args = sys.argv[1:]
root = pathlib.Path(args[args.index("--root") + 1])
names = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
names = sorted(n.decode() for n in names.split(b"\\0") if n and n.decode().startswith("src/"))
manifest = "".join("{} {}\\n".format(hashlib.sha256((root / n).read_bytes()).hexdigest(), n) for n in names)
digest = hashlib.sha256(manifest.encode()).hexdigest()
want = {".aiqt/manifest.toml": manifest, ".aiqt/release/root.txt": digest + "\\n",
        ".aiqt/release/announce-snippet.txt": "root " + digest + "\\n"}
if (root / ".gitattributes").exists():
    want[".gitattributes"] = "".join("{} text eol=lf\\n".format(n) for n in names) + "# digest " + digest + "\\n"
if (root / ".aiqt/frozen.json").exists():
    want[".aiqt/frozen.json"] = '{"digest": "' + digest + '"}\\n'
if "--check" not in args:
    for p, body in want.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_bytes(body.encode())
    print("wrote " + ", ".join(sorted(want)))
    sys.exit(0)
drift = [p for p, body in want.items() if (root / p).read_bytes() != body.encode()]
print("drift: " + ", ".join(drift) if drift else "ok")
sys.exit(1 if drift else 0)
'''


def _generated(sources, extras=False):
    """The fixture tree for sources with its generated outputs (with extras, also .gitattributes and
    .aiqt/frozen.json, as the fixture generator writes them)."""
    import hashlib
    manifest = "".join("{} {}\n".format(hashlib.sha256(data).hexdigest(), name) for name, data in sorted(sources.items()))
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    files = dict(sources)
    files[".aiqt/manifest.toml"] = manifest.encode()
    files[".aiqt/release/root.txt"] = (digest + "\n").encode()
    files[".aiqt/release/announce-snippet.txt"] = ("root " + digest + "\n").encode()
    if extras:
        files[".gitattributes"] = ("".join("{} text eol=lf\n".format(name) for name in sorted(sources))
                                   + "# digest " + digest + "\n").encode()
        files[".aiqt/frozen.json"] = ('{"digest": "' + digest + '"}\n').encode()
    files[GENERATOR] = FIXTURE_GENERATOR.encode()
    return files


def _self_test_lines(tmp):
    fx = _Fixture(os.path.join(tmp, "lines"))
    repo = fx.repo
    base = fx.commit({"f.txt": _lines(*BASE_LINES)})
    left_body = _variant({1: ["two LEFT"]})
    right_body = _variant({1: ["two RIGHT"]}, ("eight", ["right distant A", "right distant B"]))
    left = fx.commit({"f.txt": left_body}, [base])
    right = fx.commit({"f.txt": right_body}, [base])

    code, text = _judge_quiet(repo, left, right, fx.tree_of(left))
    check("whole-side/left-whole-drops-right-additions-fails",
          code == 1 and "right distant A" in text and "right distant B" in text and "whole" in text, (code, text))

    code, text = _judge_quiet(repo, left, right, fx.tree_of(right))
    check("whole-side/right-whole-drops-left-additions-fails", code == 1 and "two LEFT" in text, (code, text))

    both = _variant({1: ["two LEFT", "two RIGHT"]}, ("eight", ["right distant A", "right distant B"]))
    code, text = _judge_quiet(repo, left, right, fx.tree({"f.txt": both}))
    check("hand/keeps-both-sides-passes", code == 0 and "1 conflicted path" in text, (code, text))

    reordered = _variant({1: ["two RIGHT", "two LEFT"]}, ("eight", ["right distant A", "right distant B"]))
    code, text = _judge_quiet(repo, left, right, fx.tree({"f.txt": reordered}))
    check("hand/keeps-both-sides-other-order-passes", code == 0, (code, text))

    holder = fx.commit({"f.txt": _variant({1: ["two RIGHT", "two LEFT"]})}, [base])
    plain_right = fx.commit({"f.txt": _variant({1: ["two RIGHT"]})}, [base])
    code, text = _judge_quiet(repo, holder, plain_right, fx.tree_of(holder))
    check("whole-side/chosen-side-holds-other-additions-passes", code == 0, (code, text))

    clean_drop = _variant({1: ["two LEFT", "two RIGHT"]}, ("eight", ["right distant A"]))
    code, text = _judge_quiet(repo, left, right, fx.tree({"f.txt": clean_drop}))
    check("hand/clean-region-addition-dropped-fails",
          code == 1 and "right distant B" in text and "right distant A" not in text, (code, text))

    keep_left = _variant({1: ["two LEFT"]}, ("eight", ["right distant A", "right distant B"]))
    keep_left_tree = fx.tree({"f.txt": keep_left})
    code, text = _judge_quiet(repo, left, right, keep_left_tree)
    check("omission/undeclared-fails", code == 1 and "right line 2" in text and "two RIGHT" in text, (code, text))

    def declaration(entries, binding=None):
        document = {"format-version": 1, "base": base, "left": left, "right": right, "omissions": entries}
        document.update(binding or {})
        return json.dumps(document)

    entry = {"path": "f.txt", "side": "right", "line": 2, "text": "two RIGHT", "rationale": "superseded by left"}
    code, text = _judge_quiet(repo, left, right, keep_left_tree, declaration([entry]))
    check("omission/declared-passes", code == 0 and "declared omission" in text, (code, text))

    entries = [{"path": "f.txt", "side": "right", "line": n, "text": t, "rationale": "fixture"}
               for n, t in ((2, "two RIGHT"), (9, "right distant A"), (10, "right distant B"))]
    code, text = _judge_quiet(repo, left, right, fx.tree_of(left), declaration(entries))
    check("omission/whole-side-not-waived", code == 1 and "does not waive" in text, (code, text))

    stale = dict(entry, line=3, text="three")
    code, text = _judge_quiet(repo, left, right, keep_left_tree, declaration([entry, stale]))
    check("omission/stale-cannot-evaluate", code == 2 and "stale" in text, (code, text))

    wrong_text = dict(entry, text="two right")
    code, text = _judge_quiet(repo, left, right, keep_left_tree, declaration([wrong_text]))
    check("omission/wrong-text-cannot-evaluate", code == 2 and "stale" in text, (code, text))

    malformed = []
    for document in (declaration([dict(entry, extra="x")]), declaration([entry], {"base": left}),
                     declaration([entry, entry]), declaration([dict(entry, path="f.*")]),
                     declaration([dict(entry, rationale=" ")]), declaration([dict(entry, line=True)]),
                     '{"format-version": 1, "format-version": 1}', "not json"):
        malformed.append(_judge_quiet(repo, left, right, keep_left_tree, document)[0])
    check("omission/malformed-cannot-evaluate", malformed == [2] * 8, malformed)

    dup_base = _lines("x", "y", "x", "z")
    dup_right = _lines("x", "y", "x", "x", "z RIGHT")
    dup_left = _lines("x", "y", "x", "z LEFT")
    b2 = fx.commit({"d.txt": dup_base})
    l2 = fx.commit({"d.txt": dup_left}, [b2])
    r2 = fx.commit({"d.txt": dup_right}, [b2])
    code, text = _judge_quiet(repo, l2, r2, fx.tree({"d.txt": _lines("x", "y", "x", "z LEFT", "z RIGHT")}))
    check("lines/duplicate-occurrence-counted", code == 1 and "right line 4" in text, (code, text))
    code, text = _judge_quiet(repo, l2, r2, fx.tree({"d.txt": _lines("x", "y", "x", "x", "z LEFT", "z RIGHT")}))
    check("lines/duplicate-occurrence-kept-passes", code == 0, (code, text))

    s_base = _lines("a", "b", "c", "d", "e", "f")
    s_left = _lines("a", "b LEFT", "c", "d", "e", "shared", "f")
    s_right = _lines("a", "b RIGHT", "c", "d", "e", "shared", "f")
    b3 = fx.commit({"s.txt": s_base})
    l3 = fx.commit({"s.txt": s_left}, [b3])
    r3 = fx.commit({"s.txt": s_right}, [b3])
    code, text = _judge_quiet(repo, l3, r3, fx.tree({"s.txt": _lines("a", "b LEFT", "b RIGHT", "c", "d", "e",
                                                                      "shared", "f")}))
    check("lines/identical-additions-share-one-occurrence", code == 0, (code, text))

    c_base = b"a\nb\nc\n"
    b4 = fx.commit({"c.txt": c_base})
    l4 = fx.commit({"c.txt": b"a\nb LEFT\nc\n"}, [b4])
    r4 = fx.commit({"c.txt": b"a\nb RIGHT\r\nc\n"}, [b4])
    code, text = _judge_quiet(repo, l4, r4, fx.tree({"c.txt": b"a\nb LEFT\nb RIGHT\nc\n"}))
    check("lines/crlf-compared-exactly", code == 1 and "\\r\\n" in text, (code, text))
    r5 = fx.commit({"c.txt": b"a\nb RIGHT\nc"}, [b4])
    code, text = _judge_quiet(repo, l4, r5, fx.tree({"c.txt": b"a\nb LEFT\nb RIGHT\nc\n"}))
    check("lines/final-newline-compared-exactly", code == 1 and "'c'" in text, (code, text))

    deleted = fx.commit({}, [base])
    code, text = _judge_quiet(repo, left, deleted, fx.tree({}))
    check("shape/modify-delete-taking-delete-fails", code == 1 and "two LEFT" in text, (code, text))

    bin_base = fx.commit({"b.bin": b"\0base\n"})
    bin_left = fx.commit({"b.bin": b"\0left\n"}, [bin_base])
    bin_right = fx.commit({"b.bin": b"\0right\n"}, [bin_base])
    code, text = _judge_quiet(repo, bin_left, bin_right, fx.tree_of(bin_left))
    check("shape/binary-cannot-evaluate", code == 2, (code, text))

    link_base = fx.commit({"l": ("120000", b"target-base")})
    link_left = fx.commit({"l": ("120000", b"target-left")}, [link_base])
    link_right = fx.commit({"l": ("120000", b"target-right")}, [link_base])
    code, text = _judge_quiet(repo, link_left, link_right, fx.tree_of(link_left))
    check("shape/symlink-cannot-evaluate", code == 2, (code, text))

    long_base = _lines(*["line {}".format(n) for n in range(1, 30)])
    rb = fx.commit({"old.txt": long_base})
    rename_left = fx.commit({"new.txt": long_base.replace(b"line 5\n", b"line 5 LEFT\n")}, [rb])
    rename_right = fx.commit({"old.txt": long_base.replace(b"line 5\n", b"line 5 RIGHT\n")}, [rb])
    code, text = _judge_quiet(repo, rename_left, rename_right, fx.tree_of(rename_left))
    check("shape/rename-cannot-evaluate", code == 2, (code, text))

    driver = fx.commit({"f.txt": _lines(*BASE_LINES), ".gitattributes": b"*.txt merge=union\n"})
    driver_left = fx.commit({"f.txt": left_body, ".gitattributes": b"*.txt merge=union\n"}, [driver])
    driver_right = fx.commit({"f.txt": right_body, ".gitattributes": b"*.txt merge=union\n"}, [driver])
    code, text = _judge_quiet(repo, driver_left, driver_right, fx.tree_of(driver_left))
    check("shape/merge-driver-cannot-evaluate", code == 2 and "merge driver" in text, (code, text))

    # Fail closed on history that cannot be read.
    code, text = _judge_quiet(repo, left, "0" * 40, fx.tree_of(left))
    check("fail-closed/unreadable-side", code == 2, (code, text))
    lone = fx.commit({"f.txt": _lines("unrelated")})
    code, text = _judge_quiet(repo, left, lone, fx.tree_of(left))
    check("fail-closed/no-merge-base", code == 2 and "no merge base" in text, (code, text))
    x1 = fx.commit({"f.txt": _variant({0: ["one X1"]})}, [base])
    x2 = fx.commit({"f.txt": _variant({9: ["ten X2"]})}, [base])
    m1 = fx.commit({"f.txt": _variant({0: ["one X1"], 9: ["ten X2"]})}, [x1, x2])
    m2 = fx.commit({"f.txt": _variant({0: ["one X1"], 9: ["ten X2"]})}, [x2, x1])
    code, text = _judge_quiet(repo, m1, m2, fx.tree_of(m1))
    check("fail-closed/multiple-merge-bases", code == 2 and "criss-cross" in text, (code, text))

    # The recorded-merge and audit modes over the same fixture.
    bad_merge = fx.commit_tree(fx.tree_of(left), [left, right], "Merge member\n")
    code_bad = _main_exit(["--commit", bad_merge, "--repo", repo])
    block = "Merge member\n\n{}\n{}\n{}\n".format(MESSAGE_BEGIN, declaration([entry]), MESSAGE_END)
    declared_merge = fx.commit_tree(keep_left_tree, [left, right], block)
    code_declared = _main_exit(["--commit", declared_merge, "--repo", repo])
    check("commit/recorded-merge-judged-with-message-declaration", (code_bad, code_declared) == (1, 0),
          (code_bad, code_declared))

    restored = fx.commit_tree(fx.tree({"f.txt": both}), [bad_merge], "restore the dropped lines\n")
    synthetic = fx.commit_tree(fx.tree({"f.txt": both}), [base, restored], "test merge\n")
    fx.git(["update-ref", "--no-deref", "HEAD", synthetic])
    code_audit = _main_exit(["--audit-merges", "--repo", repo])
    good_merge = fx.commit_tree(fx.tree({"f.txt": both}), [left, right], "Merge member\n")
    synthetic_ok = fx.commit_tree(fx.tree({"f.txt": both}), [base, good_merge], "test merge\n")
    fx.git(["update-ref", "--no-deref", "HEAD", synthetic_ok])
    code_audit_ok = _main_exit(["--audit-merges", "--repo", repo])
    check("audit/earlier-lossy-merge-restored-later-still-fails", (code_audit, code_audit_ok) == (1, 0),
          (code_audit, code_audit_ok))

    # The unreadable merge base: remove its commit object, so no base can be read.
    loose = os.path.join(repo, ".git", "objects", base[:2], base[2:])
    os.chmod(loose, 0o600)
    os.remove(loose)
    code, text = _judge_quiet(repo, left, right, fx.tree({"f.txt": both}))
    check("fail-closed/unreadable-base", code == 2, (code, text))


def _self_test_clean_paths(tmp):
    """A path git merges without a conflict is judged like a conflicted one (the unconflicted-path rule)."""
    fx = _Fixture(os.path.join(tmp, "clean"))
    repo = fx.repo
    base = fx.commit({"f.txt": _lines(*BASE_LINES), "g.txt": _lines("g one", "g two")})
    left = fx.commit({"f.txt": _variant({0: ["one LEFT"]}), "g.txt": _lines("g one", "g two")}, [base])
    right = fx.commit({"f.txt": _variant({9: ["ten RIGHT"]}), "g.txt": _lines("g one", "g two", "g RIGHT")},
                      [base])
    merged = {"f.txt": _variant({0: ["one LEFT"], 9: ["ten RIGHT"]}), "g.txt": _lines("g one", "g two", "g RIGHT")}
    code, text = _judge_quiet(repo, left, right, fx.tree(merged))
    check("clean/git-merge-result-passes", code == 0 and "0 conflicted path" in text, (code, text))

    reverted = dict(merged, **{"g.txt": _lines("g one", "g two")})
    code, text = _judge_quiet(repo, left, right, fx.tree(reverted))
    check("clean/unconflicted-file-reverted-fails", code == 1 and "g RIGHT" in text, (code, text))

    both_changed = dict(merged, **{"f.txt": _variant({0: ["one LEFT"]})})
    code, text = _judge_quiet(repo, left, right, fx.tree(both_changed))
    check("clean/both-sides-changed-unconflicted-drop-fails", code == 1 and "ten RIGHT" in text, (code, text))

    code, text = _judge_quiet(repo, left, right, fx.tree_of(left))
    check("clean/ours-strategy-without-conflict-fails",
          code == 1 and "whole" in text and "ten RIGHT" in text and "g RIGHT" in text, (code, text))

    added = fx.commit({"f.txt": _lines(*BASE_LINES), "g.txt": _lines("g one", "g two"),
                       "new.txt": _lines("new RIGHT")}, [base])
    code, text = _judge_quiet(repo, left, added, fx.tree_of(left))
    check("clean/file-one-side-added-left-out-fails", code == 1 and "new RIGHT" in text, (code, text))

    long_base = _lines(*["line {}".format(n) for n in range(1, 30)])
    rb = fx.commit({"old.txt": long_base})
    renamed = fx.commit({"new.txt": long_base}, [rb])
    edited = fx.commit({"old.txt": long_base.replace(b"line 5\n", b"line 5 RIGHT\n")}, [rb])
    rename_merge = fx.tree({"new.txt": long_base.replace(b"line 5\n", b"line 5 RIGHT\n")})
    code, text = _judge_quiet(repo, renamed, edited, rename_merge)
    check("clean/rename-carrying-edits-cannot-evaluate", code == 2 and "rename" in text, (code, text))


def _self_test_attributes(tmp):
    """No attribute file outside the judged trees changes the replay (the attribute rule)."""
    fx = _Fixture(os.path.join(tmp, "attributes"))
    repo = fx.repo
    base = fx.commit({"f.txt": _lines(*BASE_LINES)})
    left_body = _variant({1: ["two LEFT"]})
    right_body = _variant({1: ["two RIGHT"]}, ("eight", ["right distant A"]))
    left = fx.commit({"f.txt": left_body}, [base])
    right = fx.commit({"f.txt": right_body}, [base])
    with open(os.path.join(repo, ".gitattributes"), "w", encoding="utf-8") as handle:
        handle.write("*.txt merge=union\n")
    os.makedirs(os.path.join(repo, ".git", "info"), exist_ok=True)
    with open(os.path.join(repo, ".git", "info", "attributes"), "w", encoding="utf-8") as handle:
        handle.write("*.txt merge=union\n")
    try:
        conflicted = replay(repo, base, left, right)[0]
    except CannotEvaluate as exc:
        conflicted = str(exc)
    keep_left = _variant({1: ["two LEFT"]}, ("eight", ["right distant A"]))
    code, text = _judge_quiet(repo, left, right, fx.tree({"f.txt": keep_left}))
    check("attributes/worktree-and-info-attributes-do-not-change-replay",
          conflicted == ["f.txt"] and code == 1 and "two RIGHT" in text, (conflicted, code, text))

    nested = fx.tree({"f.txt": _variant({1: ["two LEFT", "two RIGHT"]}, ("eight", ["right distant A"])),
                      "d/.gitattributes": b"*.txt merge=union\n"})
    code, text = _judge_quiet(repo, left, right, nested)
    check("attributes/resolution-bringing-a-merge-driver-cannot-evaluate",
          code == 2 and "result d/.gitattributes" in text and "merge driver" in text, (code, text))


def _self_test_occurrences(tmp):
    """The shared-occurrence, moved-line and keep-none rules."""
    fx = _Fixture(os.path.join(tmp, "occurrences"))
    repo = fx.repo
    s_base = _lines("a", "b", "c", "d", "e", "f")
    b1 = fx.commit({"s.txt": s_base})
    l1 = fx.commit({"s.txt": _lines("a", "b LEFT", "shared", "c", "d", "e", "f")}, [b1])
    r1 = fx.commit({"s.txt": _lines("a", "b", "c", "d", "e RIGHT", "shared", "f")}, [b1])
    once = fx.tree({"s.txt": _lines("a", "b LEFT", "shared", "c", "d", "e RIGHT", "f")})
    code, text = _judge_quiet(repo, l1, r1, once)
    check("lines/identical-additions-in-different-places-kept-once-fails",
          code == 1 and "both sides in different places" in text and "right line 6" in text, (code, text))
    twice = fx.tree({"s.txt": _lines("a", "b LEFT", "shared", "c", "d", "e RIGHT", "shared", "f")})
    code, text = _judge_quiet(repo, l1, r1, twice)
    check("lines/identical-additions-in-different-places-kept-twice-passes", code == 0, (code, text))

    m_base = _lines("a", "b", "c", "d", "e", "f", "g")
    b2 = fx.commit({"m.txt": m_base})
    l2 = fx.commit({"m.txt": _lines("a", "c", "d", "e", "f", "b", "g")}, [b2])
    r2 = fx.commit({"m.txt": _lines("a", "b", "c", "d", "e", "f", "g RIGHT")}, [b2])
    put_back = fx.tree({"m.txt": _lines("a", "b", "c", "d", "e", "f", "g RIGHT")})
    code, text = _judge_quiet(repo, l2, r2, put_back)
    check("lines/moved-line-put-back-fails", code == 1 and "left line 6" in text and "'b\\n'" in text, (code, text))
    moved = fx.tree({"m.txt": _lines("a", "c", "d", "e", "f", "b", "g RIGHT")})
    code, text = _judge_quiet(repo, l2, r2, moved)
    check("lines/moved-line-kept-moved-passes", code == 0, (code, text))

    base = fx.commit({"f.txt": _lines(*BASE_LINES)})
    left = fx.commit({"f.txt": _variant({1: ["two LEFT"]})}, [base])
    right = fx.commit({"f.txt": _variant({1: ["two RIGHT"]}, ("eight", ["right distant A"]))}, [base])
    nudged = fx.tree({"f.txt": _variant({1: ["two LEFT"]}) + b"\n"})
    document = json.dumps({"format-version": 1, "base": base, "left": left, "right": right, "omissions": [
        {"path": "f.txt", "side": "right", "line": 2, "text": "two RIGHT", "rationale": "fixture"},
        {"path": "f.txt", "side": "right", "line": 9, "text": "right distant A", "rationale": "fixture"}]})
    code, text = _judge_quiet(repo, left, right, nudged, document)
    check("omission/declaring-every-addition-of-a-side-fails",
          code == 1 and "keeps none of the 2 line(s) the right side added" in text, (code, text))


def _self_test_audit_range(tmp):
    """The local runner's audit finds a train branch's member merges (the audit-range rule)."""
    fx = _Fixture(os.path.join(tmp, "audit"))
    repo = fx.repo
    base = fx.commit({"f.txt": _lines(*BASE_LINES)})
    member_a = fx.commit({"f.txt": _variant({1: ["two A"]})}, [base])
    member_b = fx.commit({"f.txt": _variant({8: ["nine B"]})}, [base])
    lossy = fx.commit_tree(fx.tree_of(base), [base, member_a], "Merge member A\n")
    good = fx.commit_tree(fx.tree({"f.txt": _variant({8: ["nine B"]})}), [lossy, member_b], "Merge member B\n")
    tip = fx.commit_tree(fx.tree_of(good), [good], "Regenerate\n")
    fx.git(["update-ref", "refs/heads/merge-train-fixture", tip])
    fx.git(["symbolic-ref", "HEAD", "refs/heads/merge-train-fixture"])
    code_missing = _main_exit(["--audit-merges", "--repo", repo])
    fx.git(["update-ref", "refs/remotes/origin/main", base])
    code_found = _main_exit(["--audit-merges", "--repo", repo])
    check("audit/train-branch-member-merges-audited", (code_missing, code_found) == (2, 1),
          (code_missing, code_found))


def _self_test_report():
    """The result line names what failed (the report-wording rule)."""
    import contextlib
    import io
    drift = ["generated exemption: tools/gen_manifest.py --check reports drift on the resolved tree: drift"]
    dropped = ["f.txt: right line 2 (blob 0123) added since the merge base is missing from the result: 'x'"]
    outputs = []
    for findings in (drift, dropped, []):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = report(findings, [])
        outputs.append((code, buffer.getvalue()))
    ok = (outputs[0][0] == 1 and "drops lines" not in outputs[0][1] and "generator" in outputs[0][1]
          and outputs[1][0] == 1 and "drops lines" in outputs[1][1] and "generator" not in outputs[1][1].split(
              "RESULT")[1] and outputs[2][0] == 0 and "conflicted path" not in outputs[2][1])
    check("report/result-line-names-what-failed", ok, outputs)


def _self_test_generated(tmp):
    fx = _Fixture(os.path.join(tmp, "generated"))
    repo = fx.repo
    sources = {"src/a.txt": _lines("alpha"), "src/b.txt": _lines("beta")}
    base = fx.commit(_generated(sources))
    left_sources = dict(sources, **{"src/a.txt": _lines("alpha", "alpha LEFT")})
    right_sources = dict(sources, **{"src/b.txt": _lines("beta", "beta RIGHT")})
    left = fx.commit(_generated(left_sources), [base])
    right = fx.commit(_generated(right_sources), [base])
    merged = dict(sources, **{"src/a.txt": left_sources["src/a.txt"], "src/b.txt": right_sources["src/b.txt"]})

    code, text = _judge_quiet(repo, left, right, fx.tree(_generated(merged)))
    check("generated/regenerated-release-files-pass",
          code == 0 and "generated exemption claimed for" in text and "3 conflicted path" in text, (code, text))

    stale = _generated(merged)
    for path in RELEASE_FILES:
        stale[path] = _generated(left_sources)[path]
    code, text = _judge_quiet(repo, left, right, fx.tree(stale))
    check("generated/stale-release-files-fail", code == 1 and "drift" in text, (code, text))

    crlf = _generated(merged)
    crlf[".aiqt/release/root.txt"] = crlf[".aiqt/release/root.txt"].replace(b"\n", b"\r\n")
    code, text = _judge_quiet(repo, left, right, fx.tree(crlf))
    check("generated/noncanonical-output-fails", code == 1 and "not canonical LF" in text, (code, text))

    missing = _generated(merged)
    del missing[GENERATOR]
    code, text = _judge_quiet(repo, left, right, fx.tree(missing))
    check("generated/generator-absent-cannot-evaluate", code == 2 and GENERATOR in text, (code, text))

    both_conflict = dict(merged)
    left_src = _generated(dict(left_sources, **{"src/b.txt": _lines("beta", "beta LEFT")}))
    right_src = _generated(right_sources)
    l2 = fx.commit(left_src, [base])
    r2 = fx.commit(right_src, [base])
    keep_left_source = dict(both_conflict, **{"src/b.txt": _lines("beta", "beta LEFT")})
    code, text = _judge_quiet(repo, l2, r2, fx.tree(_generated(keep_left_source)))
    check("generated/exemption-does-not-cover-sources", code == 1 and "src/b.txt" in text
          and "beta RIGHT" in text, (code, text))

    edited = _generated(merged)
    edited[GENERATOR] = FIXTURE_GENERATOR.encode() + b"# a resolution's own line\n"
    code, text = _judge_quiet(repo, left, right, fx.tree(edited))
    check("generated/resolution-edited-generator-fails",
          code == 1 and "differs from git's merge" in text, (code, text))

    # .gitattributes and .aiqt/frozen.json, the generator's other two outputs, are exempt only through it.
    xbase = fx.commit(_generated(sources, extras=True))
    xleft = fx.commit(_generated(left_sources, extras=True), [xbase])
    xright = fx.commit(_generated(right_sources, extras=True), [xbase])
    code, text = _judge_quiet(repo, xleft, xright, fx.tree(_generated(merged, extras=True)))
    check("generated/regenerated-attributes-and-frozen-pass",
          code == 0 and ".gitattributes" in text and ".aiqt/frozen.json" in text
          and "generated exemption claimed for" in text, (code, text))
    stale_frozen = _generated(merged, extras=True)
    stale_frozen[".aiqt/frozen.json"] = _generated(left_sources, extras=True)[".aiqt/frozen.json"]
    code, text = _judge_quiet(repo, xleft, xright, fx.tree(stale_frozen))
    check("generated/stale-frozen-fails", code == 1 and "drift" in text and ".aiqt/frozen.json" in text,
          (code, text))


def _self_test_train(tmp):
    """tools/train_merge.py records a member merge only after this gate passes."""
    fx = _Fixture(os.path.join(tmp, "train"))
    repo = fx.repo
    base = fx.commit({"f.txt": _lines(*BASE_LINES)})
    left_body = _variant({1: ["two LEFT"]})
    right_body = _variant({1: ["two RIGHT"]}, ("eight", ["right distant A"]))
    left = fx.commit({"f.txt": left_body}, [base])
    right = fx.commit({"f.txt": right_body}, [base])
    fx.git(["config", "user.name", "Fixture"])
    fx.git(["config", "user.email", "fixture@example.invalid"])
    fx.git(["update-ref", "refs/heads/train", left])
    fx.git(["symbolic-ref", "HEAD", "refs/heads/train"])
    fx.git(["reset", "-q", "--hard"])
    tool = str(Path(__file__).resolve().parent / "train_merge.py")
    message = os.path.join(tmp, "train-message.txt")
    with open(message, "w", encoding="utf-8") as handle:
        handle.write("Merge member\n")

    def train(args):
        proc = subprocess.run([sys.executable, "-I", "-B", tool] + args, cwd=repo, capture_output=True,
                              timeout=GENERATOR_TIMEOUT)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")

    started = train(["start", right])
    with open(os.path.join(repo, "f.txt"), "wb") as handle:
        handle.write(left_body)
    fx.git(["add", "f.txt"])
    refused = train(["record", "--message-file", message])
    head_after_refusal = fx.git(["rev-parse", "HEAD"])
    merging = os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD"))
    check("train/dropped-line-records-no-commit",
          started[0] == 0 and refused[0] == 1 and head_after_refusal == left and merging
          and "right distant A" in refused[1], (started, refused, head_after_refusal == left, merging))

    both = _variant({1: ["two LEFT", "two RIGHT"]}, ("eight", ["right distant A"]))
    with open(os.path.join(repo, "f.txt"), "wb") as handle:
        handle.write(both)
    fx.git(["add", "f.txt"])
    recorded = train(["record", "--message-file", message])
    parents = fx.git(["rev-parse", "HEAD^1", "HEAD^2"]).split()
    pin_gone = not os.path.exists(os.path.join(repo, ".git", "train-merge-pin.json"))
    check("train/kept-resolution-records-checked-merge",
          recorded[0] == 0 and parents == [left, right] and pin_gone, (recorded, parents, pin_gone))


def _self_test_train_regenerates(tmp):
    """A train built through tools/train_merge.py records member merges whose generated outputs match their
    generator, without a hand regeneration (the train-procedure rule)."""
    fx = _Fixture(os.path.join(tmp, "train-generated"))
    repo = fx.repo
    sources = {"src/a.txt": _lines("alpha"), "src/b.txt": _lines("beta")}
    base = fx.commit(_generated(sources))
    a_sources = dict(sources, **{"src/a.txt": _lines("alpha", "alpha A")})
    b_sources = dict(sources, **{"src/b.txt": _lines("beta", "beta B")})
    member_a = fx.commit(_generated(a_sources), [base])
    member_b = fx.commit(_generated(b_sources), [base])
    fx.git(["config", "user.name", "Fixture"])
    fx.git(["config", "user.email", "fixture@example.invalid"])
    fx.git(["update-ref", "refs/heads/merge-train-fixture", base])
    fx.git(["symbolic-ref", "HEAD", "refs/heads/merge-train-fixture"])
    fx.git(["reset", "-q", "--hard"])
    fx.git(["update-ref", "refs/remotes/origin/main", base])
    tool = str(Path(__file__).resolve().parent / "train_merge.py")
    message = os.path.join(tmp, "train-generated-message.txt")
    with open(message, "w", encoding="utf-8") as handle:
        handle.write("Merge member\n")
    steps = []
    for member in (member_a, member_b):
        for args in (["start", member], ["record", "--message-file", message]):
            proc = subprocess.run([sys.executable, "-I", "-B", tool] + args, cwd=repo, capture_output=True,
                                  timeout=GENERATOR_TIMEOUT)
            steps.append((args[0], proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")[-400:]))
    audited = _main_exit(["--audit-merges", "--repo", repo])
    check("train/member-merges-regenerated-pass-the-audit",
          [step[1] for step in steps] == [0, 0, 0, 0] and audited == 0, (steps, audited))


def _self_test():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _git_fixture_env
    with _git_fixture_env.fixture_git_lifecycle():
        with tempfile.TemporaryDirectory(prefix="merge-resolution-selftest-") as tmp:
            for runner in (_self_test_lines, _self_test_clean_paths, _self_test_attributes, _self_test_occurrences,
                           _self_test_audit_range, _self_test_generated, _self_test_train,
                           _self_test_train_regenerates):
                try:
                    runner(tmp)
                except (CannotEvaluate, RuntimeError, OSError) as exc:
                    FAILURES.append("{}: harness error {}".format(runner.__name__, exc))

    _self_test_report()

    split = [split_lines(b""), split_lines(b"a\r\nb"), split_lines(b"a\n\nb\n")]
    check("lines/split-at-lf-only", split == [[], [b"a\r\n", b"b"], [b"a\n", b"\n", b"b\n"]], split)

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"], ["--execution-report", "rel/x"],
                                         ["--left", "a"], ["--commit", "HEAD", "--audit-merges"],
                                         ["--left", "a", "--left", "b"])]
    check("refuse/unrecognized-argument", got == [2] * 6, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_merge_resolution {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_merge_resolution {} checks (taking either side whole while it drops the other "
          "side's additions fails, also with omissions declared; a hand resolution keeping both sides passes; a "
          "whole side already holding the other's additions passes; a dropped line from a region git merged "
          "cleanly fails; a file git merged without a conflict is judged too, so a reverted file, an ours "
          "strategy merge and a left-out added file fail, while a clean rename carrying edits is "
          "cannot-evaluate; no worktree or info attribute changes the replay, and a resolution bringing a "
          "merge driver is cannot-evaluate; a line both sides added in different places kept once fails, a "
          "moved line put back fails, and declaring every addition of a side fails; declared omissions pass "
          "only when exact, and stale or malformed declarations are cannot-evaluate; duplicates, CR and the "
          "final LF are compared exactly; regenerated release files, .gitattributes and .aiqt/frozen.json pass "
          "through the generator check while stale or noncanonical ones, or an edited generator, fail; binary, "
          "symlink, rename and merge-driver conflicts, an unreadable side or merge base, no base and "
          "criss-cross bases are cannot-evaluate; recorded merges and the audit range, a train branch's "
          "member merges included, are judged; a train built through train_merge.py passes the audit; the "
          "result line names what failed)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if "--self-test" in argv or "--execution-report" in argv:
        if argv != ["--self-test"]:
            print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
            return 2
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if not argv:
        print("error: no mode given; fail closed (see --help in the module docstring)", file=sys.stderr)
        return 2
    return run(argv)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
