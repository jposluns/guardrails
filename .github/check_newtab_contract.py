#!/usr/bin/env python3
"""Pin this repository's parsed NEWTAB declaration, outside the portable gate.

This guards declaration drift, not environment overrides or edits to this assertion.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Import the shared helpers WITHOUT placing this script's own directory ahead of the stdlib (the
# check_internal_names idiom): append keeps stdlib precedence under `python3 -I`.
sys.path.append(str(REPO / "tools"))
from _gen_common import load_toml, precheck_special_files  # noqa: E402  SPECIAL-FILE-PRECHECK


def main():
    # SPECIAL-FILE-PRECHECK: refuse a tree holding a special file or hostile symlink before any
    # read; load_toml's non-blocking reader then refuses a FIFO at the declaration itself as defence in
    # depth, so this gate can never block on a plain read.
    declaration = precheck_special_files(REPO) / ".aiqt/newtab.toml"
    try:
        actual = load_toml(declaration)
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        # tomllib refuses deep nesting and (CPython 3.14.8 on) a 1001-part key with RecursionError, and a
        # many-part key under a memory limit with MemoryError; load_toml maps each into ValueError at the
        # parse locus, and this handler names both classes as well: each is exit 2, never a traceback.
        print("error: cannot read NEWTAB repository declaration: {}".format(exc), file=sys.stderr)
        return 2
    expected = dict(roots=["site", "opf/site"])
    if actual != expected:
        print("FAIL: NEWTAB repository declaration: expected {!r}, got {!r}".format(
            expected, actual), file=sys.stderr)
        return 1
    print("PASS: NEWTAB repository declaration equals {!r}".format(expected))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("check_newtab_contract.py") or main())
