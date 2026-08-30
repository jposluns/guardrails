#!/usr/bin/env python3
"""Orchestration-suite doctor (GD-112): validate the registry, the yield roster, the provider
contract, and the state directory; and re-run the resume audit to clear (or re-arm) the barrier.
  orch_doctor.py                 validate everything; exit 0 clean, 1 findings, 2 no registry
  orch_doctor.py --resume-audit  re-run the resume probes; a clean run clears the barrier
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

YIELD_MATCHER_TOOLS = {"ScheduleWakeup", "CronCreate"}  # keep equal to the manifest matcher


def main():
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status == "absent":
        print("no orchestration registry: the suite is inert here (by design)")
        return 2
    findings = []
    if status == "bad":
        findings.append("registry unreadable/invalid: {}".format(reg))
        reg = {}
    if "--resume-audit" in sys.argv[1:]:
        probe = aiqt_hooks._orch_resume_probes(reg, root)
        sd = aiqt_hooks._orch_state_dir_for_root(root)
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "resume-barrier.json"), "w", encoding="utf-8") as fh:
            json.dump({"active": bool(probe), "findings": probe,
                       "ts": aiqt_hooks._orch_now().isoformat(), "warned": False}, fh)
        if probe:
            print("resume audit: {} finding(s); the barrier stays armed:".format(len(probe)))
            for f in probe:
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


if __name__ == "__main__":
    sys.exit(main())
