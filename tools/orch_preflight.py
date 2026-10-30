#!/usr/bin/env python3
"""Pre-yield preflight (GD-112, D2: non-blocking in v1): print the disposition table the stop guard
would act on, so the orchestrator sees per-item (id, class, proof) BEFORE yielding. The hooks
re-enumerate authoritatively at yield time; this CLI adds visibility, never enforcement, and
escalating it to a required permit is a recorded phase-2 option.
  orch_preflight.py [stop|idle|drain]   exit 0 with the table; exit 2 on no registry or a bad operation
  An interpreter older than Python 3.14 that can start this file is refused at exit 2 before anything
  runs. One that cannot start it fails with Python's own error first, and that exit is Python's: 1 for a
  compile failure, or 2 for an interpreter predating -I when run with it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: orch_preflight.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402


def main():
    argv = sys.argv[1:]
    op = argv[0] if argv else "stop"
    # a drained declaration is verified by the stop path; an idle wake must use the schedule path so it
    # gets deny-on-cannot-evaluate, never the stop path's fail-open (CX-preflight-mode finding).
    kinds = {"stop": "stop", "drain": "stop", "idle": "schedule_idle"}
    if op not in kinds:
        print("usage: orch_preflight.py [stop|idle|drain]", file=sys.stderr)
        return 2
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status != "ok":
        print("no usable orchestration registry ({}); nothing to preflight".format(status))
        return 2
    # A preview makes NO state change (secpv): the checkpoint union is computed for display but not
    # persisted, and no guard-events are emitted. The real hook path keeps record_checkpoint=True.
    ctx, _ts, _basis = aiqt_hooks._orch_build_ctx(
        reg, root, kinds[op], {}, record_checkpoint=False)
    verdict, reason, disposition = aiqt_hooks.decide_yield(ctx)
    print("operation: {} (decision kind: {})".format(op, kinds[op]))
    print("verdict if you {} now: {}".format(op, verdict))
    print("reason: {}".format(reason))
    print("disposition ({} row(s)):".format(len(disposition)))
    for klass, iid, a, b in disposition:
        print("  {:<11} {:<16} {} {}".format(klass, iid, a, b))
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
    sys.exit(_child_status_refusal_exit("orch_preflight.py") or main())
