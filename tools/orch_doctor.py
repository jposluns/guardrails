#!/usr/bin/env python3
"""Orchestration-suite doctor (GD-112): validate the registry, the yield roster, the provider
contract, and the state directory; and re-run the resume audit to clear (or re-arm) the barrier.
  orch_doctor.py                 validate everything; exit 0 clean, 1 findings, 2 no registry
  orch_doctor.py --resume-audit  re-run the resume probes; a clean run clears the barrier
  An interpreter older than Python 3.14 is refused at exit 2 before anything runs.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: orch_doctor.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

YIELD_MATCHER_TOOLS = {"ScheduleWakeup", "CronCreate"}  # keep equal to the manifest matcher


# The truncation guard's registry-required deny reasons for each scope it denies, as the doctor reports
# them (aiqt_hooks._orch_truncation_scope decides the scope exactly as the guard does).
_STRICT_SCOPE_FINDINGS = dict((
    ("none", "no orchestration registry on this repository root's ancestor chain or at its git "
             "toplevel"),
    ("cannot-evaluate", "the nearest .aiqt entry on this repository root's ancestor chain (or, with "
                        "none there, at its git toplevel) cannot be confirmed as a registry: .aiqt is not "
                        "a directory openable without following a symlink, or its first present registry "
                        "name (orchestration.local.json, then orchestration.json) is not a regular file "
                        "(a symlinked registry file included) or cannot be examined"),
    ("toplevel-unopenable", "no registry on this repository root's ancestor chain, and its git toplevel "
                            "cannot be opened as a directory"),
))


def _guard_scope_lines(root):
    """What the truncation guard decides for a Bash call whose cwd is the repository root: a list of
    report lines, and whether the guard denies that call at the scope check in the CURRENT mode (a cwd
    the walk cannot carry out denies in every mode; an absent or unconfirmable registry denies only when
    registry-required mode is on)."""
    scope, found = aiqt_hooks._orch_truncation_scope(root)
    env = aiqt_hooks._ORCH_REQUIRE_REGISTRY_ENV
    if scope == "fail":
        return (["truncation guard: a Bash call from the repository root is denied in every mode: its "
                 "cwd %s" % found[0]], True)
    if aiqt_hooks._orch_registry_required() and scope in _STRICT_SCOPE_FINDINGS:
        return (["truncation guard (%s is set, registry-required mode): a Bash call from the repository "
                 "root is DENIED: %s" % (env, _STRICT_SCOPE_FINDINGS[scope])], True)
    if scope == "none":
        return (["truncation guard: inert for a Bash call from the repository root (no registry on its "
                 "ancestor chain or at its git toplevel; set %s to make it deny instead)" % env], False)
    if scope in _STRICT_SCOPE_FINDINGS:
        # Default mode reads a discovery fault as present (the deny-safe direction): the guard is active
        # without a confirmed registry, which is reported as the fault it is, not as a registry found.
        return (["truncation guard: ACTIVE for a Bash call from the repository root because its registry "
                 "discovery hit a fault it reads as present, not because a registry was confirmed: %s "
                 "(with %s set it denies instead)" % (_STRICT_SCOPE_FINDINGS[scope], env)], False)
    return (["truncation guard: ACTIVE for a Bash call from the repository root (a registry entry was "
             "found on its ancestor chain, which can lie above this repository, or at its git "
             "toplevel)"], False)


def main():
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    guard_lines, guard_denies = _guard_scope_lines(root)
    if status == "absent":
        # Scoped like the components themselves: every component except the truncation guard is inert
        # with no registry at the repository root, but the guard's ancestor walk can find a registry ABOVE
        # it (and the guard is then active), and in registry-required mode it denies instead of going inert.
        print("no orchestration registry at the repository root: every suite component except the "
              "truncation guard is inert here (by design); the truncation guard scopes by the cwd's "
              "ancestor chain and the git toplevel, judged per call from its cwd")
        for line in guard_lines:
            print(line)
        return 2
    findings = []
    if status == "bad":
        findings.append("registry unreadable/invalid: {}".format(reg))
        reg = {}
    if guard_denies:
        # The loader accepted (or reported) the registry, but the truncation guard's own discovery denies a
        # Bash call from here: report it, never "usable" (a symlinked registry file is the common case in
        # registry-required mode: the loader follows it, the guard's no-follow probe does not confirm it).
        findings.extend(guard_lines)
    if "--resume-audit" in sys.argv[1:]:
        # The findings gathered above (an unreadable registry, the truncation guard's deny) hold the
        # barrier as the resume probes do: the audit is clean only when neither found anything.
        audit = findings + aiqt_hooks._orch_resume_probes(reg, root)
        sd = aiqt_hooks._orch_state_dir_for_root(root)
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "resume-barrier.json"), "w", encoding="utf-8") as fh:
            json.dump({"active": bool(audit), "findings": audit,
                       "ts": aiqt_hooks._orch_now().isoformat(), "warned": False}, fh)
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


if __name__ == "__main__":
    sys.exit(main())
