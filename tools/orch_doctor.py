#!/usr/bin/env python3
"""Orchestration-suite doctor (GD-112): validate the registry, the yield roster, the provider
contract, and the state directory; and re-run the resume audit to clear (or re-arm) the barrier.
  orch_doctor.py                 validate everything; exit 0 clean, 1 findings, 2 no registry
  orch_doctor.py --resume-audit  re-run the resume probes; a clean run clears the barrier
  An interpreter older than Python 3.14 that can start this file is refused at exit 2 before anything
  runs. One that cannot start it fails with Python's own error first, and that exit is Python's: 1 for a
  compile failure, which reads as findings, or 2 for an interpreter predating -I when run with it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: orch_doctor.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

YIELD_MATCHER_TOOLS = {"ScheduleWakeup", "CronCreate"}  # keep equal to the manifest matcher


# The truncation guard's registry-required deny reasons, and its scope decision for a Bash call from the
# repository root, live in aiqt_hooks (round 8) so this doctor and the SessionStart resume audit, the two
# writers of resume-barrier.json, report and arm on one scope.
_STRICT_SCOPE_FINDINGS = aiqt_hooks._ORCH_STRICT_SCOPE_FINDINGS


def _guard_scope_lines(root):
    """What the truncation guard decides for a Bash call whose cwd is the repository root: a list of
    report lines, and whether the guard denies that call at the scope check in the CURRENT mode
    (aiqt_hooks._orch_guard_scope_report)."""
    return aiqt_hooks._orch_guard_scope_report(root)


def main():
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    guard_lines, guard_denies = _guard_scope_lines(root)
    if status == "absent":
        # Scoped like the components themselves: every component except the truncation guard is inert
        # with no registry at the repository root, but the guard's ancestor walk can find a registry ABOVE
        # it (and the guard is then active), and in registry-required mode it denies instead of denying nothing.
        print("no orchestration registry at the repository root: every suite component except the "
              "truncation guard is inert here (by design); the truncation guard scopes by the cwd's "
              "ancestor chain and the git toplevel, judged per call from its cwd")
        for line in guard_lines:
            print(line)
        return 2
    findings = []
    raw_reg = reg
    if status == "bad":
        findings.append("registry unreadable/invalid: {}".format(reg))
        reg = {}
    if guard_denies:
        # The loader accepted (or reported) the registry, but the truncation guard's own discovery denies a
        # Bash call from here: report it, never "usable" (a symlinked registry file is the common case in
        # registry-required mode: the loader follows it, the guard's no-follow probe does not confirm it).
        findings.extend(guard_lines)
    if "--resume-audit" in sys.argv[1:]:
        # The same finding list the SessionStart resume audit writes (an unreadable registry, the truncation
        # guard's deny, the resume probes): the audit is clean only when none of them found anything.
        audit = aiqt_hooks._orch_resume_audit_findings(status, raw_reg, root)
        # Atomic, as the SessionStart audit writes it: a failed write raises and ends this run, leaving the
        # previous barrier byte-identical (aiqt_hooks._orch_barrier_write).
        sd = aiqt_hooks._orch_state_dir_for_root(root)
        aiqt_hooks._orch_barrier_write(os.path.join(sd, "resume-barrier.json"),
                                       {"active": bool(audit), "findings": audit,
                                        "ts": aiqt_hooks._orch_now().isoformat(), "warned": False})
        if audit:
            print("resume audit: {} finding(s); the barrier stays armed:".format(len(audit)))
            for f in audit:
                print("  " + f)
            return 1
        print("resume audit clean: the barrier is cleared")
        return 0
    for tool in reg.get("yield_tools") or []:
        if tool not in YIELD_MATCHER_TOOLS:
            findings.append("yield tool {!r} is OUTSIDE the shipped PreToolUse matcher and is not "
                            "covered by the hook (a manifest matcher is fixed at generation)"
                            .format(tool))
    sd = aiqt_hooks._orch_state_dir_for_root(root)
    try:
        os.makedirs(sd, exist_ok=True)
        probe = os.path.join(sd, ".doctor-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.unlink(probe)
    except OSError as exc:
        findings.append("state directory not writable: {}".format(exc))
    if reg.get("enumerator"):
        est, payload = aiqt_hooks._orch_enumerate(reg, root)
        if est != "ok":
            findings.append("enumerator contract: {}: {}".format(est, payload))
        else:
            print("enumerator OK: {} item(s)".format(len(payload)))
    else:
        findings.append("no enumerator declared: the stop guard will fail open with findings on "
                        "every yield (stop) and deny scheduling (schedule_idle)")
    if reg.get("mode") and aiqt_hooks._orch_mode(reg, root) is None:
        findings.append("declared mode record carries no readable Operating-mode line")
    if findings:
        print("DOCTOR: {} finding(s):".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("DOCTOR: registry, roster, provider, and state directory are all usable")
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
    sys.exit(_child_status_refusal_exit("orch_doctor.py") or main())
