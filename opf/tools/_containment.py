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


def precheck_special_files(root):
    """Refuse an OPF tree that holds a special file or a hostile symlink, before an OPF tool reads from
    it (D-400-SPECIAL-FILE-PRECHECK). This is the standalone OPF pack's copy of
    tools/_gen_common.precheck_special_files: the pack may not import tools/ (check_opf_standalone_closure),
    and the two copies behave identically (tools/check_manifest.py's self-test requires the two functions,
    docstrings aside, and their two module tables to stay identical, binds each name exactly once per
    module, and refuses a module-level attribute rebinding in either copy). Walk root (os.walk with a
    raising onerror, never following a symlink for traversal, pruning every entry named .git), lstat every
    entry, and on the first FIFO, socket, block or character device print its path and exit 2
    (fail-closed), as on an unlistable directory. A FIFO with no writer blocks any plain read of it
    forever. Runs once per process per root (cached); returns root so a caller can wrap the expression
    that computes it.

    SYMLINK POLICY (same as the tools/ copy). Every symlink the walk meets is judged by its target:
    os.stat (follows the link, never opens, so it cannot block on a FIFO) classifies it, and the link is
    refused by name when the target is a special file, is missing (dangling) or unresolvable (a loop), is
    a DIRECTORY (a symlinked directory is REFUSED rather than walked), or resolves outside the root. A
    symlink to a regular file inside the root is accepted: the target is walked from the root itself.

    This module is invoked directly (python3 -I -B "$here/_containment.py" --precheck) as the FIRST line
    of opf/tools/run_all_checks.sh, which stops on refusal (|| exit 2); the root is the nearest .git
    ancestor (in the authoring repo, the whole repository) or, in a standalone opf/ checkout with no .git
    ancestor, the opf/ tree itself. Every gate and self-test the runner then runs acts on a tree this walk
    already checked; the per-tool calls in check_opf_homes (the opf/ subtree), check_opf_prompt_pack (its
    pack directory) and check_opf_init_contract (the repository root) stay for local single-tool runs.
    In the authoring repo, tools/run_all_checks.sh and .github/workflows/quality.yml also run the live
    legs of check_opf_drift, check_opf_doctor, check_opf_init, check_opf_init_contract and
    check_opf_upgrade; those runs sit behind the runner-level precheck, and apart from
    check_opf_init_contract their store reads act on an adopter's store (NOT APPLICABLE in this repo).
    Residuals: a special file created, or a symlink retargeted, after this walk (a concurrent writer); a
    vanished entry (skipped); and everything under .git, which this walk prunes (git's own reads of its
    metadata included)."""
    root = Path(root)
    key = os.path.abspath(root)
    if key in _PRECHECKED_ROOTS:
        return root

    def _raise(exc):
        raise exc

    def _refuse(path, why):
        print("error: {}: refused, {}; remove it; fail-closed".format(path, why), file=sys.stderr)
        raise SystemExit(2)
    real_root = os.path.realpath(root)
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames + filenames:
                if name == ".git":
                    continue
                path = os.path.join(dirpath, name)
                try:
                    mode = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(mode):
                        _refuse(path, "{} in the repository tree (a special file, not a regular "
                                      "file)".format(kind))
                if stat.S_ISLNK(mode):
                    try:
                        target_mode = os.stat(path).st_mode  # follows the link; stat never opens, so it
                    except FileNotFoundError:                # cannot block on a FIFO target
                        _refuse(path, "a dangling symlink (its target is missing)")
                    except OSError as exc:
                        _refuse(path, "a symlink whose target cannot be resolved ({})".format(exc))
                    for is_kind, kind in _SPECIAL_KINDS:
                        if is_kind(target_mode):
                            _refuse(path, "a symlink to {} (a special file, not a regular "
                                          "file)".format(kind))
                    if stat.S_ISDIR(target_mode):
                        _refuse(path, "a symlink to a directory (the walk never follows a symlink, so "
                                      "its contents would evade this check)")
                    real = os.path.realpath(path)
                    if real != real_root and not real.startswith(real_root + os.sep):
                        _refuse(path, "a symlink resolving outside the repository root (to "
                                      "{})".format(real))
    except OSError as exc:
        print("error: cannot walk the repository tree {} ({}); fail-closed".format(root, exc), file=sys.stderr)
        raise SystemExit(2)
    _PRECHECKED_ROOTS.add(key)
    return root


if __name__ == "__main__":
    # Runner entry (D-400-SPECIAL-FILE-PRECHECK): precheck the tree BEFORE any OPF gate runs.
    # Usage: python3 -I -B opf/tools/_containment.py --precheck
    # The root is the nearest .git ancestor, so in the authoring repo the whole repository is walked; a
    # standalone opf/ checkout with no .git ancestor walks the opf/ tree (this file's parents[1]).
    # Exit 0 when the walk finds nothing to refuse; exit 2 (fail-closed) on a refusal or unknown argument.
    if sys.argv[1:] != ["--precheck"]:
        print("usage: python3 -I -B opf/tools/_containment.py --precheck", file=sys.stderr)
        raise SystemExit(2)
    _entry = Path(__file__).resolve()
    _root = _entry.parents[1]
    for _anc in _entry.parents:
        if (_anc / ".git").exists():
            _root = _anc
            break
    precheck_special_files(_root)
    print("PASS: special-file precheck found nothing to refuse in {}".format(_root))
    raise SystemExit(0)
