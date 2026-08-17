#!/usr/bin/env python3
"""Behavioural self-test for the enforcement-hook handlers in .aiqt/core/hooks/scripts/aiqt_hooks.py.

The gen_hooks.py self-test proves the GENERATOR's fail-closed invariants; this one proves the HANDLERS'
decisions, so a regression in a control's logic (not just its wiring) fails a gate. It imports the source
handler module directly (never the generated plugin copy) and drives each handler with a synthetic hook
payload, asserting allow vs deny by the structured decision the handler returns, not by grepping output.

The git_discard cases need a real git-status probe, so the suite builds ONE hermetic throwaway repo under
a temp dir (git init, a local identity, one committed file), dirties it, and points each command at it with
'-C <repo>' so the probe is deterministic regardless of cwd; the temp tree is removed in a finally.

  selftest_aiqt_hooks.py    exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402


def _decision(handler, command, tool="Bash"):
    """Run a PreToolUse handler over a synthetic Bash payload and reduce its result to 'allow' or 'deny'.
    An allow is the silent no-decision (exit 0, no stdout object); a deny carries a permissionDecision
    'deny' in its stdout object. Any other shape is surfaced as a harness error string."""
    data = {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}}
    code, stdout_obj, _stderr = handler(data)
    if code == 0 and stdout_obj is None:
        return "allow"
    if code == 0 and isinstance(stdout_obj, dict):
        decision = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision == "deny":
            return "deny"
    return "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)


def _git(repo, *args, env_identity=False):
    """Run a git command against repo, raising on failure so a setup fault surfaces as a harness error."""
    cmd = ["git", "-C", str(repo)]
    if env_identity:
        cmd += ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false"]
    cmd += list(args)
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)


def main():
    handler = aiqt_hooks.git_discard
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-hooks-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []

    def expect(label, command, want, tool="Bash"):
        got = _decision(handler, command, tool=tool)
        if got != want:
            failures.append("{}: expected {}, got {}".format(label, want, got))

    try:
        repo = tmp / "repo"
        repo.mkdir()
        try:
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                           check=True, capture_output=True, text=True, timeout=30)
            tracked = repo / "file.txt"
            tracked.write_text("committed line\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _git(repo, "commit", "-q", "-m", "seed", env_identity=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the throwaway repo: {}".format(exc), file=sys.stderr)
            return 2

        rp = str(repo)
        # (d, clean) reset --hard on a clean tree ALLOWS (the probe reports nothing to lose).
        expect("(d) reset --hard clean allows", "git -C {} reset --hard".format(rp), "allow")

        # Dirty the tracked file: now every worktree discard of it loses work.
        tracked.write_text("committed line\nuncommitted fix\n", encoding="utf-8")

        # (a) checkout -- <path> with a dirtied tracked path BLOCKS.
        expect("(a) checkout -- dirty blocks", "git -C {} checkout -- file.txt".format(rp), "deny")
        # (b) same with the GUARDRAIL_ALLOW_DISCARD=1 opt-out ALLOWS.
        expect("(b) checkout -- with optout allows",
               "GUARDRAIL_ALLOW_DISCARD=1 git -C {} checkout -- file.txt".format(rp), "allow")
        # (c) checkout <branch> with no -- (a branch switch) ALLOWS (never a discard, never probed).
        expect("(c) checkout branch allows", "git -C {} checkout main".format(rp), "allow")
        # (d, dirty) reset --hard on a dirty tree BLOCKS.
        expect("(d) reset --hard dirty blocks", "git -C {} reset --hard".format(rp), "deny")
        # (e) restore --staged <path> (unstage only, touches no worktree) ALLOWS.
        expect("(e) restore --staged allows", "git -C {} restore --staged file.txt".format(rp), "allow")
        # (f) restore <path> on a dirty tracked path BLOCKS.
        expect("(f) restore dirty blocks", "git -C {} restore file.txt".format(rp), "deny")
        # (g) an unparseable command (unbalanced quote) ALLOWS (fail open, no raw-string scan).
        expect("(g) unparseable allows", 'git -C {} checkout -- "unbalanced'.format(rp), "allow")
        # (h) a non-git command ALLOWS.
        expect("(h) non-git allows", "ls -la {}".format(rp), "allow")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: git_discard blocks a confirmed-lossy checkout/restore/reset --hard, honours "
          "the GUARDRAIL_ALLOW_DISCARD opt-out, and fails open on a branch switch, a staged-only restore, "
          "an unparseable command, and a non-git command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
