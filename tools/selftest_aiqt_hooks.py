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
            # A second committed file that stays untouched, so a clean pathspec probe is deterministic.
            (repo / "clean.txt").write_text("clean line\n", encoding="utf-8")
            _git(repo, "add", "clean.txt")
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

        # --- FIX 1/2/3/4 regression cases ---------------------------------------------------------
        # file.txt is currently worktree-dirty (unstaged). Confirm the worktree-only discard still
        # BLOCKS, then stage the change and re-test the staged-only distinction.
        expect("(ii) worktree-dirty checkout -- blocks",
               "git -C {} checkout -- file.txt".format(rp), "deny")
        _git(repo, "add", "file.txt")  # now staged-only: index differs, worktree matches index
        # (i) FIX 4: a worktree-only discard (checkout -- with no ref) restores from the index and
        # PRESERVES the staged change, so a staged-only change ALLOWS.
        expect("(i) staged-only checkout -- allows",
               "git -C {} checkout -- file.txt".format(rp), "allow")
        # (iii) FIX 4: a ref-based checkout rewrites the index too, so a staged-only change BLOCKS.
        expect("(iii) staged-only checkout <ref> -- blocks",
               "git -C {} checkout HEAD -- file.txt".format(rp), "deny")

        # (iv) FIX B: a --pathspec-from-file source is NOT enumerated (the file reader was removed), so
        # even a path that would lose work falls OPEN (allow), a deliberate seed-sanctioned residual. Both
        # the inline '=' form and the bare space form fail open.
        tracked.write_text("committed line\nuncommitted fix\nmore\n", encoding="utf-8")  # worktree-dirty
        expect("(iv) inline pathspec-from-file allows (fail-open)",
               "git -C {} restore --pathspec-from-file=paths.txt".format(rp), "allow")
        expect("(iv) space-form pathspec-from-file allows (fail-open)",
               "git -C {} restore --pathspec-from-file paths.txt".format(rp), "allow")

        # (vii) FIX A: a backslash-newline line continuation is spliced before segmentation, so a
        # continued discard still lexes and BLOCKS on a worktree-dirty path (file.txt is worktree-dirty).
        expect("(vii) line-continuation checkout -- blocks",
               "git -C {} checkout \\\n  -- file.txt".format(rp), "deny")

        # (viii) FIX C: 'restore --source HEAD <path>' skips the -s/--source value, so it probes file.txt
        # (not HEAD); a worktree-dirty path BLOCKS. affects_index is False for a worktree-only restore, so
        # once the change is staged (worktree clean) the same command ALLOWS.
        expect("(viii) restore --source HEAD worktree-dirty blocks",
               "git -C {} restore --source HEAD file.txt".format(rp), "deny")
        _git(repo, "add", "file.txt")  # worktree now matches index (staged-only): worktree column clean
        expect("(viii) restore --source HEAD staged-only allows",
               "git -C {} restore --source HEAD file.txt".format(rp), "allow")

        # (x) FIX 53: a clustered short flag '-SW' is git's '-S -W' (rewrites index AND worktree), so it
        # affects the index; on the staged-only state that is a loss and it BLOCKS. The long-form
        # '--staged --worktree' is the same command and must BLOCK identically.
        _git(repo, "add", "file.txt")  # re-assert staged-only in case a prior expect re-dirtied the tree
        expect("(x) restore -SW clustered staged-only blocks",
               "git -C {} restore -SW file.txt".format(rp), "deny")
        expect("(x) restore --staged --worktree staged-only blocks",
               "git -C {} restore --staged --worktree file.txt".format(rp), "deny")

        # (xi) round-4: git's unambiguous long-option abbreviations. '--stag'/'--work' resolve to
        # '--staged'/'--worktree', which rewrites the index too, so on the staged-only state it BLOCKS.
        _git(repo, "add", "file.txt")  # re-assert staged-only
        expect("(xi) restore --stag --work abbreviated staged-only blocks",
               "git -C {} restore --stag --work file.txt".format(rp), "deny")
        # (xii) round-4: '--no-*' negation. '-SW --no-worktree' leaves staged-only (index-only unstage,
        # the worktree is preserved), which is not a discard, so it ALLOWS.
        _git(repo, "add", "file.txt")  # re-assert staged-only
        expect("(xii) restore -SW --no-worktree staged-only allows",
               "git -C {} restore -SW --no-worktree file.txt".format(rp), "allow")
        # (xiii) round-4: abbreviated reset. '--har' resolves to '--hard' (whole-tree index+worktree
        # discard), so on the staged-only lossy state it BLOCKS.
        _git(repo, "add", "file.txt")  # re-assert staged-only (a lossy state for reset --hard)
        expect("(xiii) reset --har abbreviated lossy blocks",
               "git -C {} reset --har HEAD".format(rp), "deny")
        # (xiv) round-4: a reset with a pathspec after '--' is a mixed reset (worktree preserved); the
        # '--hard' detection is scoped before '--', so a clean tracked pathspec ALLOWS (locks in the
        # pre-'--' scoping). clean.txt is a clean tracked path.
        expect("(xiv) reset -- clean pathspec allows",
               "git -C {} reset -- clean.txt".format(rp), "allow")

        # (ix) FIX C: option parsing is scoped before '--', so a literal pathspec named
        # '--pathspec-from-file=foo' AFTER '--' is a path, not an option: from_file is False and it is
        # enumerated. A unit assertion on the shape (a live-repo case for this name is awkward).
        shape = aiqt_hooks._discard_shape(["git", "checkout", "--", "--pathspec-from-file=foo"])
        if not (shape and shape.get("from_file") is False
                and shape.get("paths") == ["--pathspec-from-file=foo"]):
            failures.append("(ix) post-'--' literal pathspec: expected from_file=False, "
                            "paths=['--pathspec-from-file=foo'], got {!r}".format(shape))

        # (v) FIX 1: a malformed tool_input (a string, not a dict) must ALLOW (fail OPEN), not crash.
        malformed = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "malformed"}
        code, stdout_obj, _stderr = handler(malformed)
        if not (code == 0 and stdout_obj is None):
            failures.append("(v) malformed tool_input: expected allow, got code={!r}, stdout={!r}"
                            .format(code, stdout_obj))

        # (vi) FIX 2: chained '-C <outer> -C inner' resolves cumulatively to the inner repo; a dirty
        # inner worktree BLOCKS.
        outer = tmp / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(inner)],
                       check=True, capture_output=True, text=True, timeout=30)
        inner_file = inner / "f.txt"
        inner_file.write_text("base\n", encoding="utf-8")
        _git(inner, "add", "f.txt")
        _git(inner, "commit", "-q", "-m", "seed", env_identity=True)
        inner_file.write_text("base\ndirty\n", encoding="utf-8")  # worktree-dirty inner repo
        expect("(vi) chained -C inner dirty blocks",
               "git -C {} -C inner checkout -- f.txt".format(str(outer)), "deny")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: git_discard blocks a confirmed-lossy checkout/restore/reset --hard, honours "
          "the GUARDRAIL_ALLOW_DISCARD opt-out, distinguishes a staged-only change (worktree-only discard "
          "allows, ref-based checkout blocks), splices backslash-newline line continuations before "
          "segmentation, skips the --source/-s value so it probes the path (not the ref), scopes option "
          "parsing before '--' (a post-'--' literal '--pathspec-from-file=foo' is a path, not an option), "
          "resolves chained -C cumulatively, and fails open on a --pathspec-from-file source, a branch "
          "switch, a staged-only restore, an unparseable command, a malformed tool_input, and a non-git "
          "command")
    return 0


if __name__ == "__main__":
    sys.exit(main())
