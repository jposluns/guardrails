#!/usr/bin/env python3
"""Shared containment-capability probe for the platform-matrix gates (VER-CORE 3.6). Offline, stdlib
only.

The race-free containment primitive is dir-handle-relative open with no-follow semantics: O_NOFOLLOW
plus dir_fd support on the containment calls (present on Linux and macOS, the two supported
platforms). This module is the SINGLE probe the verifier-side gates key their mode off (3.6: a
capability probe at startup, refusing silent fallback); the step-6/7 mutators (cutover, re-pin swap,
recovery, un-adopt) consume the same probe and REFUSE before mutation where containment is absent.
The mode is decided from the probe of this process's actual os capabilities, never from an os-name
match (a guard is only as good as its input: the capability, not the label, answers the question).

Modes per operation class (3.6a to 3.6c):
  read-only-verification: "contained" with the primitive, else the labelled degraded verdict
  cutover / repin-swap / recovery / unadopt: "contained" with the primitive, else "fail-closed"

It also carries precheck_special_files, the OPF copy of the special-file precheck
(D-400-SPECIAL-FILE-PRECHECK; see its docstring).
"""
import os
import stat
import sys
from pathlib import Path

READ_ONLY = "read-only-verification"
MUTATING_CLASSES = ("cutover", "repin-swap", "recovery", "unadopt")
DEGRADED = "degraded-no-race-free-containment"


def probe():
    """True when the race-free containment primitive is available to this process: O_NOFOLLOW exists
    and the dir_fd forms of open/stat/unlink/rename/mkdir are supported. Never raises; a probe that
    cannot answer reports False (cannot-determine = not contained = the safe answer) and the caller
    applies the fail-safe mode for its class."""
    if not hasattr(os, "O_NOFOLLOW"):
        return False
    needed = (os.open, os.stat, os.unlink, os.rename, os.mkdir)
    try:
        return all(fn in os.supports_dir_fd for fn in needed)
    except Exception:
        return False


def mode_for(operation_class, contained):
    """The 3.6 mode for an operation class given the probe result. An unknown class fails closed
    ALWAYS, regardless of the probe: a class this table does not know cannot claim any proceed
    verdict, contained or not. Validate the class before consulting containment."""
    if operation_class not in {READ_ONLY} | set(MUTATING_CLASSES):
        return "fail-closed"
    if contained:
        return "contained"
    if operation_class == READ_ONLY:
        return DEGRADED
    return "fail-closed"


_PRECHECKED_ROOTS = set()
_SPECIAL_KINDS = ((stat.S_ISFIFO, "a FIFO"), (stat.S_ISSOCK, "a socket"), (stat.S_ISBLK, "a block device"),
                  (stat.S_ISCHR, "a character device"))


def _git_lines(root, args):
    """One bounded, read-only git query under root, as raw bytes; None when git cannot answer (no git
    binary, not a repository, a timeout, any nonzero exit). Callers treat None as git-absent and fall
    back on the fail-closed side: a fixed-location root, a walk with NO ignore filter (more refusals,
    never fewer). stdin is closed and the call is bounded, so this probe itself cannot be parked by a
    hostile tree; the ignore-control files git WOULD read with a plain blocking open are screened by
    the caller before the one query that reads them (see precheck_special_files)."""
    import subprocess
    try:
        proc = subprocess.run(["git", "-C", os.fspath(root), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_toplevel(root):
    """os.path.realpath of `git rev-parse --show-toplevel` run AT root, or None when git cannot
    answer. This never searches upward for a .git marker itself: whether a .git entry is a real
    repository is git's judgement (git skips an invalid planted marker such as an empty tools/.git
    directory), and the answer is used only to CONFIRM a root already derived from a fixed location,
    never to choose one (D-400-SPECIAL-FILE-PRECHECK root derivation). An answer that is not one
    NUL-free absolute path (empty, several lines, an embedded NUL or other undecodable bytes, a
    relative path) is cannot-evaluate, exit 2, by name: never a raw ValueError out of the path call,
    and never read as git-absent, which would skip the confirmation."""
    out = _git_lines(root, ["rev-parse", "--show-toplevel"])
    if out is None:
        return None
    top = out.rstrip(b"\n")
    try:
        if not top or b"\0" in top or b"\n" in top:
            raise ValueError("not exactly one NUL-free line")
        text = os.fsdecode(top)
        if not os.path.isabs(text):
            raise ValueError("not an absolute path")
        return os.path.realpath(text)
    except (OSError, ValueError) as exc:
        print("error: git rev-parse --show-toplevel at {} returned a malformed root {!r} ({}); cannot "
              "evaluate which tree to walk; fail-closed".format(root, out, exc), file=sys.stderr)
        raise SystemExit(2)


def _ignored_paths(root):
    """The root-relative paths git reports ignored under root (git ls-files --others --ignored
    --exclude-standard --directory, NUL-separated; a wholly ignored directory comes back as one
    entry), without trailing separators; None when git cannot answer, in which case the caller walks
    with NO ignore filter (strictly more refusals, never fewer)."""
    out = _git_lines(root, ["ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                            "--directory"])
    if out is None:
        return None
    paths = set()
    for raw in out.split(b"\0"):
        if raw:
            paths.add(os.fsdecode(raw).rstrip("/").replace("/", os.sep))
    return frozenset(paths)


def _derive_precheck_root(entry):
    """The tree root for a --precheck run, from the entry module's own FIXED location, never from an
    upward search for a .git marker (a planted empty tools/.git or opf/tools/.git must not narrow the
    walk and certify the wrong tree). tools/_gen_common.py roots at the parent of its tools/
    directory. opf/tools/_containment.py roots two levels up (the authoring repository) when that tree
    is recognizably the authoring one (the opf/ directory name beside a tools/_gen_common.py sentinel;
    the probe stats and never opens, so a special file planted at the sentinel path only narrows the
    root to the opf/ tree, never blocks), else at the standalone opf/ tree itself. Whenever git can
    answer, the __main__ entries and repo_root() require _git_toplevel to CONFIRM the derivation; a
    disagreement is cannot-evaluate (exit 2), never a silently substituted root."""
    here = entry.parent
    if (entry.name == "_containment.py" and here.parent.name == "opf"
            and (here.parent.parent / "tools" / "_gen_common.py").is_file()):
        return here.parent.parent
    return here.parent


def precheck_special_files(root):
    """Refuse a tree that holds a BLOCKING-READ hazard, before any tool reads from it
    (D-400-SPECIAL-FILE-PRECHECK): a gate that opens a FIFO, socket or device, directly or through a
    symlink, waits forever (a FIFO with no writer above all). Plain errors (an unlistable directory,
    an unresolvable symlink) stay loud, exit 2. Runs once per process per root (cached); returns root
    so a caller can wrap the expression that computes it.

    REFUSED, by name, exit 2 (hazard-scoped, so a layout a developer legitimately has does not trip
    it): a FIFO, socket, block or character device; a symlink whose resolved target is one of those
    (os.stat classifies the target without opening, so the classification itself cannot block); a
    symlink that cannot be resolved for any reason OTHER than a missing target (a loop above all); and
    a symlink to a directory OUTSIDE the root, whose contents this walk cannot certify. ACCEPTED: a
    regular file or directory; a DANGLING symlink (every open of it fails at once, so no read of it
    can block); a symlink to a REGULAR file, inside or outside the root (a plain read of a regular
    file does not block); and a symlink to a directory INSIDE the root, whose resolved target subtree
    is walked ONCE (a visited set keyed by device and inode bounds link cycles), so linked-in contents
    are checked rather than trusted.

    SCOPE: the whole tree under root, except every entry named .git (pruned; git's own metadata is
    outside the tools' read set, but git itself reads it, so a FIFO planted at for example .git/config
    still hangs the gates that invoke git; removing such a plant needs the operator) and except the
    paths git reports IGNORED (_ignored_paths), so a developer's .venv, editor droppings and build
    outputs are not refused. Ignored paths are therefore OUTSIDE this precheck. Checked against the
    roster, that is sound because no gate reads an ignored path through a BLOCKING call: the gates
    enumerate their read sets from git-tracked paths, fixed files and checked directories, except the
    repo-wide scanners (check_secrets, check_leaks, check_no_dashes, check_links, check_site), which
    do read whatever their walk meets and therefore read it through _walk.read_text_nonblocking,
    which refuses a non-regular file at open instead of blocking on it. Before git is asked for the
    ignore list, a first pass refuses any in-tree .gitignore that is, or resolves to, a special file,
    or is a symlink git cannot resolve other than a dangling one: git computes the ignore list by
    OPENING those files with a plain blocking read, so they are screened ahead of the one git query
    that reads them. When git cannot answer at all (an exported tree with no repository; any git
    failure), the walk runs with NO ignore filter: strictly more refusals, never fewer.

    ENTRY POINTS. This module is invoked directly (--precheck) as the FIRST post-checkout run step of
    every job in every CI workflow (tools/check_ci_parity.py holds every job to that order) and ahead
    of every gate in each runner, which stops on refusal (exit 2), so every gate and self-test after
    it acts on a checked tree. The root comes from _derive_precheck_root (a fixed location, never an
    upward .git search) and must be confirmed by _git_toplevel whenever git can answer. BOOTSTRAP: the
    runners and the CI steps first test, in shell ([ -f ] && [ ! -h ]), that the precheck module
    itself is a regular non-symlink file, because python3 would block LOADING a FIFO planted at the
    script path before any line of this code could run. Residuals that remain OUTSIDE the precheck:
    the interpreter binary and the runner script itself (both are read before the bootstrap line can
    run); everything under .git and the ignored paths, both as above; a special file CREATED, or a
    symlink RETARGETED, after this walk (one walk cannot close a race against a hostile co-writer of
    the tree); a vanished entry (skipped); and the per-tool residuals the tools' own docstrings
    carry. The shared reader (read_source_bytes) stays on the corpus and manifest paths as defence in
    depth.

    COPIES. tools/_gen_common.py and opf/tools/_containment.py each carry this section (the standalone
    OPF pack may not import tools/: check_opf_standalone_closure). tools/check_manifest.py's self-test
    compares the two copies' precheck sections (these helpers, the two module tables and the __main__
    block) as parsed trees, and holds each module to shape rules that keep the compared definitions
    the operative ones; its docstring names the mutation classes those rules do and do not catch."""
    root = Path(root)
    key = os.path.abspath(root)
    if key in _PRECHECKED_ROOTS:
        return root

    def _raise(exc):
        raise exc

    def _refuse(path, why):
        print("error: {}: refused, {}; remove it; fail-closed".format(path, why), file=sys.stderr)
        raise SystemExit(2)
    try:
        real_root = os.path.realpath(root)
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames + filenames:
                if name != ".gitignore":
                    continue
                path = os.path.join(dirpath, name)
                try:
                    checked = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(checked):
                    try:
                        checked = os.stat(path).st_mode
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    except OSError as exc:
                        _refuse(path, "an ignore-control file that is a symlink git cannot resolve "
                                      "({}); git opens it to compute the ignore list".format(exc))
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(checked):
                        _refuse(path, "an ignore-control file that is (or resolves to) {}; git opens "
                                      "it with a plain blocking read to compute the ignore "
                                      "list".format(kind))
        ignored = _ignored_paths(root)

        def _skipped(rel):
            while rel:
                if rel in ignored:
                    return True
                rel = os.path.dirname(rel)
            return False

        visited = set()
        stack = [(os.fspath(root), "")]
        while stack:
            dirpath, relbase = stack.pop()
            try:
                dir_stat = os.stat(dirpath)
            except FileNotFoundError:
                continue
            dir_key = (dir_stat.st_dev, dir_stat.st_ino)
            if dir_key in visited:
                continue
            visited.add(dir_key)
            try:
                with os.scandir(dirpath) as scan:
                    entries = sorted(scan, key=lambda item: item.name)
            except FileNotFoundError:
                continue
            for item in entries:
                name = item.name
                if name == ".git":
                    continue
                rel = os.path.join(relbase, name) if relbase else name
                if ignored is not None and rel in ignored:
                    continue
                path = item.path
                try:
                    mode = item.stat(follow_symlinks=False).st_mode
                except FileNotFoundError:
                    continue
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(mode):
                        _refuse(path, "{} in the repository tree (a special file, not a regular "
                                      "file)".format(kind))
                if stat.S_ISLNK(mode):
                    try:
                        target_mode = os.stat(path).st_mode  # follows the link; stat never opens, so
                    except FileNotFoundError:                # it cannot block on a FIFO target
                        continue  # dangling: every open of it fails at once; no read of it can block
                    except NotADirectoryError:
                        continue  # dangling through a non-directory component: open fails at once too
                    except OSError as exc:
                        _refuse(path, "a symlink whose target cannot be resolved ({}); no reader of "
                                      "it can be classified".format(exc))
                    for is_kind, kind in _SPECIAL_KINDS:
                        if is_kind(target_mode):
                            _refuse(path, "a symlink to {} (a special file, not a regular "
                                          "file)".format(kind))
                    if stat.S_ISDIR(target_mode):
                        real = os.path.realpath(path)
                        if real == real_root or real.startswith(real_root + os.sep):
                            link_rel = os.path.relpath(real, real_root)
                            if link_rel == ".":
                                link_rel = ""
                            if ignored is None or not _skipped(link_rel):
                                stack.append((real, link_rel))
                        else:
                            _refuse(path, "a symlink to a directory outside the repository root (to "
                                          "{}); its contents cannot be certified from this "
                                          "root".format(real))
                elif stat.S_ISDIR(mode):
                    stack.append((path, rel))
    except (OSError, ValueError) as exc:  # ValueError: a root path no path call accepts (an embedded NUL)
        print("error: cannot walk the repository tree {} ({}); fail-closed".format(root, exc), file=sys.stderr)
        raise SystemExit(2)
    _PRECHECKED_ROOTS.add(key)
    return root


if __name__ == "__main__":
    # Runner/CI entry (D-400-SPECIAL-FILE-PRECHECK): precheck the tree BEFORE any gate runs.
    # Usage: python3 -I -B <this module> --precheck
    # The root comes from this module's own FIXED location (_derive_precheck_root), never from an
    # upward .git search a planted marker could narrow; whenever git can answer, rev-parse must
    # CONFIRM the derivation, and a disagreement is cannot-evaluate. Without git (an exported tree)
    # the fixed location stands alone and the walk runs with no ignore filter.
    # Exit 0 when the walk finds nothing to refuse; exit 2 on a refusal, a root disagreement, or an
    # unknown argument.
    if sys.argv[1:] != ["--precheck"]:
        print("usage: python3 -I -B {} --precheck".format(sys.argv[0] or __file__), file=sys.stderr)
        raise SystemExit(2)
    _entry = Path(__file__).resolve()
    _root = _derive_precheck_root(_entry)
    _confirmed = _git_toplevel(_root)
    if _confirmed is not None and _confirmed != os.path.realpath(_root):
        print("error: the tree root derived from {} is {}, but git rev-parse --show-toplevel says "
              "{}; cannot evaluate which tree to walk; fail-closed".format(_entry, _root, _confirmed),
              file=sys.stderr)
        raise SystemExit(2)
    precheck_special_files(_root)
    print("PASS: special-file precheck found nothing to refuse in {}".format(_root))
    raise SystemExit(0)
