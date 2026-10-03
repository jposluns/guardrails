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
    """Refuse an OPF tree that holds a special file, before an OPF tool reads from it
    (D-400-SPECIAL-FILE-PRECHECK). This is the standalone OPF pack's copy of
    tools/_gen_common.precheck_special_files: the pack may not import tools/ (check_opf_standalone_closure),
    and the two copies behave identically (tools/check_manifest.py's self-test requires the two functions,
    docstrings aside, and their two module tables to stay identical). Walk root (os.walk with a raising
    onerror, never following a symlink, pruning every entry named .git), lstat every entry, and on the first
    FIFO, socket, block or character device print its path and exit 2 (fail-closed), as on an unlistable
    directory. A FIFO with no writer blocks any plain read of it forever. Runs once per process per root
    (cached); returns root so a caller can wrap the expression that computes it.

    Covered: the two entry points that opf/tools/run_all_checks.sh runs outside --self-test,
    check_opf_homes (on the opf/ subtree it reads the spec from) and check_opf_prompt_pack (on its pack
    directory). Not covered, with reasons: opf.py and the other check_opf_* gates (they act on an adopter's
    store, and run_all_checks.sh runs them only as --self-test on scratch fixtures), a special file created
    after this walk (a concurrent writer), and a vanished entry (skipped)."""
    root = Path(root)
    key = os.path.abspath(root)
    if key in _PRECHECKED_ROOTS:
        return root

    def _raise(exc):
        raise exc
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
                        print("error: {}: refused, {} in the repository tree (a special file, not a regular "
                              "file); remove it; fail-closed".format(path, kind), file=sys.stderr)
                        raise SystemExit(2)
    except OSError as exc:
        print("error: cannot walk the repository tree {} ({}); fail-closed".format(root, exc), file=sys.stderr)
        raise SystemExit(2)
    _PRECHECKED_ROOTS.add(key)
    return root
