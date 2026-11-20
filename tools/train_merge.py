#!/usr/bin/env python3
"""The merge-train entry point that records a member merge only after tools/check_merge_resolution.py passes.

  train_merge.py start MEMBER_REV
      Pin HEAD and the member commit, then start `git merge --no-ff --no-commit` of the member. Refused
      (exit 2) when a merge is already in progress, a pin exists, or the index or tracked files differ
      from HEAD.
  train_merge.py record --message-file FILE [--omissions FILE]
      Regenerate the five outputs of tools/gen_manifest.py (.gitattributes, .aiqt/manifest.toml,
      .aiqt/release/root.txt, .aiqt/release/announce-snippet.txt and .aiqt/frozen.json) with the resolved
      tree's own generator and stage them (a conflicted output is staged first, then overwritten), so
      every member merge records outputs that match their generator and the CI audit of the train's
      member merges passes on a correct train. Then freeze the staged result as a tree (refused while any
      other path is unmerged), check it with
      check_merge_resolution.py against the pinned parents, and only on exit 0 record the merge with
      `git commit --cleanup=verbatim` (hooks run as usual). The omission declaration, when given, is
      appended to the message between the Merge-Resolution-Omissions-Begin and -End lines, where CI's
      audit reads it. After the commit, its parents and tree must equal the checked ones, and the recorded
      commit is judged again with --commit; any difference or failure exits nonzero and names the commit.
  train_merge.py abort
      Remove the pin and run `git merge --abort`.

Exit: 0 recorded (or started, or aborted); 1 the check found a dropped line, nothing recorded; 2 refused or
cannot evaluate. Run it from inside the train's working tree; git is resolved through PATH.

Procedure (RELEASING.md): start the member, resolve and stage every conflicted source file by hand, then
record (it regenerates the release manifest outputs itself). A nonzero record leaves the merge in progress
so the resolution can be fixed and recorded again.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: train_merge.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import subprocess
from pathlib import Path

CHECKER = Path(__file__).resolve().parent / "check_merge_resolution.py"
PIN_NAME = "train-merge-pin.json"
TIMEOUT = 900
MESSAGE_BEGIN = "Merge-Resolution-Omissions-Begin"
MESSAGE_END = "Merge-Resolution-Omissions-End"
GENERATOR = "tools/gen_manifest.py"
# The five outputs tools/gen_manifest.py writes; record regenerates and stages them for every member.
GENERATED_OUTPUTS = (".gitattributes", ".aiqt/manifest.toml", ".aiqt/release/root.txt",
                     ".aiqt/release/announce-snippet.txt", ".aiqt/frozen.json")


class Refused(Exception):
    """A refusal: exit 2, nothing recorded."""


def _env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return env


def git(args, ok=(0,), stdin=None):
    try:
        proc = subprocess.run(["git"] + list(args), input=stdin, capture_output=True, env=_env(), timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("cannot run git {} ({})".format(args[0], exc)) from None
    if proc.returncode not in ok:
        raise Refused("git {} failed (exit {}): {}".format(
            args[0], proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    return proc


def _out(args, ok=(0,)):
    return git(args, ok).stdout.decode("utf-8", "surrogateescape").strip()


def _git_dir():
    return Path(_out(["rev-parse", "--absolute-git-dir"]))


def _merge_head(git_dir):
    path = git_dir / "MERGE_HEAD"
    if not path.exists():
        return None
    heads = path.read_text(encoding="ascii").split()
    if len(heads) != 1:
        raise Refused("MERGE_HEAD names {} commits; only a two-parent merge is supported".format(len(heads)))
    return heads[0]


def _commit(rev):
    oid = _out(["rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}"], ok=(0, 1))
    if not oid:
        raise Refused("{} does not resolve to a commit".format(rev))
    return oid


def _checker(args):
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(CHECKER)] + args, env=_env(), timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("cannot run check_merge_resolution.py ({})".format(exc)) from None
    return proc.returncode


def start(member_rev):
    git_dir = _git_dir()
    pin = git_dir / PIN_NAME
    if pin.exists() or _merge_head(git_dir) is not None:
        raise Refused("a merge or a pin is already in progress; record it or run train_merge.py abort")
    if _out(["status", "--porcelain", "--untracked-files=no"]):
        raise Refused("the index or tracked files differ from HEAD; start from a clean train head")
    head = _commit("HEAD")
    member = _commit(member_rev)
    with open(pin, "x", encoding="utf-8") as handle:
        json.dump({"head": head, "member": member}, handle)
        handle.write("\n")
    try:
        proc = git(["merge", "--no-ff", "--no-commit", member], ok=(0, 1))
    except Refused:
        # git merge itself failed (for example, no committer identity): no merge started, so the pin goes.
        if _merge_head(git_dir) is None:
            pin.unlink()
        raise
    if _merge_head(git_dir) != member:
        pin.unlink()
        raise Refused("git merge did not leave {} as MERGE_HEAD: {}".format(
            member[:12], (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()))
    sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
    print("started: train head {} + member {}; resolve and stage, then record (it regenerates the release "
          "manifest outputs)".format(head[:12], member[:12]))
    return 0


def _unmerged_paths():
    raw = git(["ls-files", "--unmerged", "-z"]).stdout
    paths = set()
    for record in raw.split(b"\0"):
        if record:
            paths.add(record.partition(b"\t")[2].decode("utf-8", "surrogateescape"))
    return paths


def regenerate(top):
    """Regenerate the generated outputs with the resolved tree's own tools/gen_manifest.py and stage
    them, so every member merge records outputs that match their generator. A conflicted generated output
    is staged first (its conflicted text is then overwritten); any other conflicted path is left for the
    unmerged-path refusal."""
    for path in sorted(_unmerged_paths() & set(GENERATED_OUTPUTS)):
        git(["add", "--", path])
    generator = Path(top) / GENERATOR
    if not generator.is_file():
        if any((Path(top) / path).exists() for path in GENERATED_OUTPUTS):
            raise Refused("{} is missing from the resolved tree, but generated outputs are present; nothing "
                          "recorded".format(GENERATOR))
        print("regenerated: nothing ({} and its outputs are absent)".format(GENERATOR))
        return
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(generator), "--root", top], cwd=top,
                              capture_output=True, env=_env(), timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("cannot run {} ({})".format(GENERATOR, exc)) from None
    if proc.returncode != 0:
        raise Refused("{} exited {}: {}".format(GENERATOR, proc.returncode, (proc.stdout + proc.stderr).decode(
            "utf-8", "replace").strip()[-2000:]))
    present = [path for path in GENERATED_OUTPUTS if (Path(top) / path).is_file()]
    if present:
        git(["add", "--"] + present)
    print("regenerated: {}".format(", ".join(present) or "no output present"), flush=True)


def _read_pin(git_dir):
    try:
        pin = json.loads((git_dir / PIN_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("no readable pin ({}); run train_merge.py start".format(exc)) from None
    if not isinstance(pin, dict) or sorted(pin) != ["head", "member"]:
        raise Refused("the pin is malformed")
    return pin["head"], pin["member"]


def record(message_file, omissions_file):
    git_dir = _git_dir()
    head, member = _read_pin(git_dir)
    if _commit("HEAD") != head:
        raise Refused("HEAD moved since start (pinned {}); nothing recorded".format(head[:12]))
    if _merge_head(git_dir) != member:
        raise Refused("MERGE_HEAD is not the pinned member {}; nothing recorded".format(member[:12]))
    top = _out(["rev-parse", "--show-toplevel"])
    regenerate(top)
    if _out(["ls-files", "--unmerged"]):
        raise Refused("unmerged paths remain in the index; resolve and stage them first")
    tree = _out(["write-tree"])
    args = ["--left", head, "--right", member, "--tree", tree, "--repo", top]
    declaration = None
    if omissions_file is not None:
        args += ["--omissions", omissions_file]
        with open(omissions_file, encoding="utf-8") as handle:
            declaration = handle.read().strip()
    code = _checker(args)
    if code != 0:
        print("NOT RECORDED: check_merge_resolution.py exited {}; the merge stays in progress".format(code))
        return 1 if code == 1 else 2
    with open(message_file, encoding="utf-8") as handle:
        message = handle.read().rstrip("\n") + "\n"
    if declaration:
        message += "\n{}\n{}\n{}\n".format(MESSAGE_BEGIN, declaration, MESSAGE_END)
    if _out(["write-tree"]) != tree:
        raise Refused("the index changed after the check; nothing recorded, record again")
    message_path = git_dir / "TRAIN_MERGE_MSG"
    message_path.write_text(message, encoding="utf-8")
    git(["commit", "--cleanup=verbatim", "-F", str(message_path)])
    message_path.unlink()
    recorded = _commit("HEAD")
    parents = _out(["rev-parse", recorded + "^1", recorded + "^2"]).split()
    if parents != [head, member] or _out(["rev-parse", recorded + "^{tree}"]) != tree:
        print("RECORDED BUT REFUSED: commit {} does not have the checked parents and tree (a hook changed the "
              "commit); reset to {} and record again".format(recorded[:12], head[:12]))
        return 2
    code = _checker(["--commit", recorded, "--repo", top])
    if code != 0:
        print("RECORDED BUT REFUSED: the recorded commit {} fails the check (exit {}); reset to {} and record "
              "again".format(recorded[:12], code, head[:12]))
        return code
    (git_dir / PIN_NAME).unlink()
    print("recorded: merge {} of member {} into {}".format(recorded[:12], member[:12], head[:12]))
    return 0


def abort():
    git_dir = _git_dir()
    pin = git_dir / PIN_NAME
    if pin.exists():
        pin.unlink()
    if _merge_head(git_dir) is not None:
        git(["merge", "--abort"])
    print("aborted")
    return 0


def main(argv):
    try:
        if len(argv) == 2 and argv[0] == "start" and not argv[1].startswith("-"):
            return start(argv[1])
        if argv[:1] == ["record"]:
            rest = argv[1:]
            if len(rest) == 2 and rest[0] == "--message-file":
                return record(rest[1], None)
            if len(rest) == 4 and rest[0] == "--message-file" and rest[2] == "--omissions":
                return record(rest[1], rest[3])
        if argv == ["abort"]:
            return abort()
    except Refused as exc:
        print("REFUSED: {}".format(exc), file=sys.stderr)
        return 2
    print("usage: train_merge.py start MEMBER_REV | record --message-file FILE [--omissions FILE] | abort",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
