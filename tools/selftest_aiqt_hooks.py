#!/usr/bin/env python3
"""Behavioural self-test for the enforcement-hook handlers in .aiqt/core/hooks/scripts/aiqt_hooks.py.

The gen_hooks.py self-test proves the GENERATOR's fail-closed invariants; this one proves the HANDLERS'
decisions, so a regression in a control's logic (not just its wiring) fails a gate. It imports the source
handler module directly (never the generated plugin copy) and drives each handler with a synthetic hook
payload, asserting allow vs ask vs deny by the structured decision the handler returns, not by grepping
output.

The git_discard control is the EN-6 scoped-posture guard with THREE outcomes (allow/ask/deny), so this
suite distinguishes all three. Its cases need real git probes, so the suite builds hermetic throwaway
repos under a temp dir (git init, a local identity, committed files), dirties them, and points each
command at them with '-C <repo>' (or a payload cwd) so the probe is deterministic regardless of the
runner's cwd; the temp tree is removed in a finally.

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


def _decision(handler, command, tool="Bash", cwd=None):
    """Run a PreToolUse handler over a synthetic Bash payload and reduce its result to 'allow', 'ask', or
    'deny'. An allow is the silent no-decision (exit 0, no stdout object); ask/deny carry the matching
    permissionDecision in the stdout object. Any other shape is surfaced as a harness error string."""
    data = {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}}
    if cwd is not None:
        data["cwd"] = cwd
    code, stdout_obj, _stderr = handler(data)
    if code == 0 and stdout_obj is None:
        return "allow"
    if code == 0 and isinstance(stdout_obj, dict):
        decision = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision in ("allow", "ask", "deny"):
            return decision
    return "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)


def _git(repo, *args, env_identity=False):
    """Run a git command against repo, raising on failure so a setup fault surfaces as a harness error."""
    cmd = ["git", "-C", str(repo)]
    if env_identity:
        cmd += ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false"]
    cmd += list(args)
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)


def _init_repo(path):
    """git init a repo at path with two committed tracked files (file.txt, clean.txt) on branch main,
    plus a second branch 'other' at the same commit, and return the Path. Raises on any git failure."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   check=True, capture_output=True, text=True, timeout=30)
    (path / "file.txt").write_text("committed line\n", encoding="utf-8")
    (path / "clean.txt").write_text("clean line\n", encoding="utf-8")
    _git(path, "add", "file.txt", "clean.txt")
    _git(path, "commit", "-q", "-m", "seed", env_identity=True)
    _git(path, "branch", "other")
    return path


def main():
    handler = aiqt_hooks.git_discard
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-hooks-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []

    def expect(label, command, want, cwd=None):
        got = _decision(handler, command, cwd=cwd)
        if got != want:
            failures.append("{}: expected {}, got {}".format(label, want, got))

    try:
        try:
            repo = _init_repo(tmp / "repo")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the throwaway repo: {}".format(exc), file=sys.stderr)
            return 2
        tracked = repo / "file.txt"
        rp = str(repo)

        # === boundary + posture: the true fail-open boundary ALLOWS ==========================
        expect("(bound-a) non-git command allows", "ls -la {}".format(rp), "allow")
        expect("(bound-b) unparseable command allows", 'git -C {} checkout -- "unbalanced'.format(rp),
               "allow")
        # A clean tree: every recognized discard is prove-safe -> ALLOW.
        expect("(clean-a) reset --hard clean allows", "git -C {} reset --hard".format(rp), "allow")
        expect("(clean-b) checkout -- clean allows", "git -C {} checkout -- file.txt".format(rp), "allow")

        # Dirty the tracked file (worktree-dirty): now worktree discards of it lose work.
        tracked.write_text("committed line\nuncommitted fix\n", encoding="utf-8")

        # === checkout ========================================================================
        expect("(co-a) checkout -- dirty denies", "git -C {} checkout -- file.txt".format(rp), "deny")
        expect("(co-b) checkout -- with optout allows",
               "GUARDRAIL_ALLOW_DISCARD=1 git -C {} checkout -- file.txt".format(rp), "allow")
        expect("(co-c) checkout . dirty denies", "git -C {} checkout .".format(rp), "deny")
        expect("(co-d) checkout <branch> clean-switch allows", "git -C {} checkout other".format(rp),
               "allow")
        expect("(co-e) checkout -f <branch> dirty denies", "git -C {} checkout -f other".format(rp),
               "deny")
        # Path-disjoint fast path: the discard targets a CLEAN tracked path, so it ALLOWS.
        expect("(co-f) checkout -- disjoint clean path allows",
               "git -C {} checkout -- clean.txt".format(rp), "allow")

        # === switch ==========================================================================
        expect("(sw-a) switch -f dirty denies", "git -C {} switch -f other".format(rp), "deny")
        expect("(sw-b) switch --discard-changes dirty denies",
               "git -C {} switch --discard-changes other".format(rp), "deny")
        expect("(sw-c) plain switch allows (git aborts on dirty)", "git -C {} switch other".format(rp),
               "allow")

        # === restore =========================================================================
        expect("(re-a) restore dirty denies", "git -C {} restore file.txt".format(rp), "deny")
        expect("(re-b) restore --staged pure-unstage allows",
               "git -C {} restore --staged file.txt".format(rp), "allow")

        # === reset ===========================================================================
        expect("(rs-a) reset --hard dirty denies", "git -C {} reset --hard".format(rp), "deny")
        expect("(rs-b) reset --merge dirty asks", "git -C {} reset --merge".format(rp), "ask")
        expect("(rs-c) reset --mixed allows", "git -C {} reset --mixed".format(rp), "allow")
        expect("(rs-d) reset -- <clean path> allows", "git -C {} reset -- clean.txt".format(rp), "allow")

        # === rm ==============================================================================
        expect("(rm-a) rm dirty tracked denies", "git -C {} rm file.txt".format(rp), "deny")
        expect("(rm-b) rm --cached pure-unstage allows", "git -C {} rm --cached file.txt".format(rp),
               "allow")
        expect("(rm-c) rm clean tracked allows (recoverable from HEAD)",
               "git -C {} rm clean.txt".format(rp), "allow")

        # === clean, stash, branch: their own probes / always-ask =============================
        (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        expect("(cl-a) clean -fd would-remove asks", "git -C {} clean -fd".format(rp), "ask")
        expect("(cl-b) clean -n dry-run allows", "git -C {} clean -nfd".format(rp), "allow")
        expect("(cl-c) clean without force allows", "git -C {} clean -d".format(rp), "allow")
        expect("(st-a) stash drop asks", "git -C {} stash drop".format(rp), "ask")
        expect("(st-b) stash clear asks", "git -C {} stash clear".format(rp), "ask")
        expect("(st-c) stash pop allows (out of scope)", "git -C {} stash pop".format(rp), "allow")
        expect("(br-a) branch -D asks", "git -C {} branch -D other".format(rp), "ask")
        expect("(br-b) branch -d allows (git refuses unmerged)", "git -C {} branch -d other".format(rp),
               "allow")

        # === F-57: --end-of-options and value-taking --pathspec-from-file ====================
        # 'reset --pathspec-from-file <file-named --hard>' is a PATH-mode reset (worktree preserved), so
        # the operand '--hard' must NOT be read as the hard mode. A path-mode reset preserves the
        # worktree -> ALLOW even on a dirty tree. (git 2.53: verified this reverts only the index.)
        (repo / "--hard").write_text("file.txt\n", encoding="utf-8")
        expect("(f57-a) reset --pathspec-from-file --hard is path-mode, allows",
               "git -C {} reset --pathspec-from-file --hard".format(rp), "allow")
        # '--end-of-options' terminates option parsing, so a following '--hard' is an operand, not a mode.
        expect("(f57-b) reset --end-of-options --hard operand allows",
               "git -C {} reset --end-of-options --hard".format(rp), "allow")
        # Unit assertion: the effective-mode parser skips the pathspec-from-file value and stops at the
        # option boundary, so neither yields 'hard'.
        if aiqt_hooks._reset_effective_mode(["--pathspec-from-file", "--hard"]) is not None:
            failures.append("(f57-c) _reset_effective_mode read a pathspec-from-file value as a mode")
        if aiqt_hooks._reset_effective_mode(["--end-of-options", "--hard"]) is not None:
            failures.append("(f57-d) _reset_effective_mode read a post-end-of-options operand as a mode")
        # A genuine last-wins '--hard' is still detected (regression guard for the parser).
        if aiqt_hooks._reset_effective_mode(["--soft", "--hard"]) != "hard":
            failures.append("(f57-e) _reset_effective_mode lost the last-wins --hard")

        # === staged-only distinction (F-53/54/55/56 regression guards) =======================
        _git(repo, "add", "file.txt")  # now staged-only: index differs, worktree matches index
        # A worktree-only discard restores from the index and PRESERVES the staged change -> ALLOW.
        expect("(idx-a) staged-only checkout -- allows",
               "git -C {} checkout -- file.txt".format(rp), "allow")
        # A ref-based checkout rewrites the index too, so a staged-only change is lost -> DENY.
        expect("(idx-b) staged-only checkout <ref> -- denies",
               "git -C {} checkout HEAD -- file.txt".format(rp), "deny")
        # reset --hard rewrites the index too -> DENY on a staged-only change.
        expect("(idx-c) staged-only reset --hard denies", "git -C {} reset --hard".format(rp), "deny")
        # Clustered '-SW' == '-S -W' (rewrites index AND worktree) -> DENY on staged-only; the long form
        # is identical.
        _git(repo, "add", "file.txt")
        expect("(idx-d) restore -SW clustered staged-only denies",
               "git -C {} restore -SW file.txt".format(rp), "deny")
        _git(repo, "add", "file.txt")
        expect("(idx-e) restore --staged --worktree staged-only denies",
               "git -C {} restore --staged --worktree file.txt".format(rp), "deny")
        # Unambiguous long-option abbreviations resolve; '-SW --no-worktree' leaves a pure unstage -> ALLOW.
        _git(repo, "add", "file.txt")
        expect("(idx-f) restore --stag --work abbreviated staged-only denies",
               "git -C {} restore --stag --work file.txt".format(rp), "deny")
        _git(repo, "add", "file.txt")
        expect("(idx-g) restore -SW --no-worktree pure-unstage allows",
               "git -C {} restore -SW --no-worktree file.txt".format(rp), "allow")
        # reset mode is last-wins: '--har --soft' -> soft (allow), '--soft --hard' -> hard (deny).
        _git(repo, "add", "file.txt")
        expect("(idx-h) reset --har --soft last-wins-soft allows",
               "git -C {} reset --har --soft".format(rp), "allow")
        expect("(idx-i) reset --soft --hard last-wins-hard denies",
               "git -C {} reset --soft --hard".format(rp), "deny")
        # '--source HEAD' skips the -s/--source value so it probes file.txt (not HEAD); staged-only worktree
        # is clean for a worktree-only restore -> ALLOW.
        _git(repo, "add", "file.txt")
        expect("(idx-j) restore --source HEAD staged-only allows",
               "git -C {} restore --source HEAD file.txt".format(rp), "allow")

        # === restore forms git ERRORS on are not discards -> ALLOW ===========================
        # A fresh worktree edit that DIFFERS from the staged index, so the worktree column is dirty.
        tracked.write_text("committed line\nworktree only edit\n", encoding="utf-8")
        expect("(err-a) restore -S --no-staged errors, allows",
               "git -C {} restore -S --no-staged file.txt".format(rp), "allow")
        expect("(err-b) restore --s ambiguous errors, allows",
               "git -C {} restore --s file.txt".format(rp), "allow")
        expect("(err-c) restore default worktree restore denies",
               "git -C {} restore file.txt".format(rp), "deny")

        # === pathspec-from-file source -> cannot enumerate -> ASK (posture flip from EN-4) ====
        expect("(pff-a) inline pathspec-from-file asks",
               "git -C {} restore --pathspec-from-file=paths.txt".format(rp), "ask")
        expect("(pff-b) space-form pathspec-from-file asks",
               "git -C {} restore --pathspec-from-file paths.txt".format(rp), "ask")
        # A post-'--' literal named '--pathspec-from-file=foo' is a PATHSPEC, not an option: a unit
        # assertion (a live-repo case for this name is awkward).
        pre, post, had_sep = aiqt_hooks._split_pre_post(["--", "--pathspec-from-file=foo"])
        if not (had_sep and post == ["--pathspec-from-file=foo"]
                and aiqt_hooks._pathspec_from_file(pre) is False):
            failures.append("(pff-c) post-'--' literal misparsed: pre={!r} post={!r}".format(pre, post))

        # === directory resolution (spec section 5) ===========================================
        # A malformed tool_input (a string, not a dict) -> boundary ALLOW (fail open), not a crash.
        malformed = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "malformed"}
        code, stdout_obj, _stderr = handler(malformed)
        if not (code == 0 and stdout_obj is None):
            failures.append("(dir-a) malformed tool_input: expected allow, got code={!r}, stdout={!r}"
                            .format(code, stdout_obj))
        # cwd threading: a plain 'git reset --hard' with the payload cwd pointing at the dirty repo -> DENY.
        expect("(dir-b) cwd-only reset --hard dirty denies", "git reset --hard", "deny", cwd=rp)
        # A leading 'cd <sub>' in the segment chain is threaded into the effective dir.
        (repo / "sub").mkdir(exist_ok=True)
        expect("(dir-c) cd sub then reset --hard (repo dirty) denies",
               "cd sub && git reset --hard", "deny", cwd=rp)
        # --work-tree targeting the dirty repo -> DENY (work-tree wins for the probe).
        expect("(dir-d) --work-tree targeting dirty repo denies",
               "git --git-dir={0}/.git --work-tree={0} reset --hard".format(rp), "deny")
        # An unresolvable dir ('cd $VAR') on a recognized lossy verb -> ASK, never silent-allow.
        expect("(dir-e) unresolvable cd $VAR reset --hard asks",
               "cd $VAR && git reset --hard", "ask")
        # No cwd and only relative selectors -> unresolvable -> ASK.
        expect("(dir-f) no-cwd plain reset --hard asks", "git reset --hard", "ask")
        # Chained '-C outer -C inner' resolves cumulatively; a dirty inner repo -> DENY.
        inner = _init_repo(tmp / "outer" / "inner")
        (inner / "file.txt").write_text("committed line\ndirty\n", encoding="utf-8")
        expect("(dir-g) chained -C inner dirty denies",
               "git -C {} -C inner checkout -- file.txt".format(str(tmp / "outer")), "deny")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: git_discard (EN-6 scoped posture) fails open only at the true boundary; proves "
          "safe within scope (clean tree, disjoint path, pure unstage, clean branch switch -> allow); "
          "DENIES a confirmed tracked-work loss across checkout/switch/restore/reset --hard/rm; ASKS the "
          "unprovable middle (reset --merge, clean of untracked files, stash drop/clear, branch -D, a "
          "pathspec-from-file source, an unresolvable repo dir); distinguishes a staged-only change "
          "(F-53/54/55/56 guards); closes F-57 (--end-of-options and the value-taking --pathspec-from-file "
          "so an operand named like a mode is not read as the mode); resolves the repo dir from the payload "
          "cwd, a leading cd/pushd, cumulative -C, and --work-tree; and honours the GUARDRAIL_ALLOW_DISCARD "
          "opt-out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
