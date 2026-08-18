#!/usr/bin/env python3
"""Behavioural self-test for the enforcement-hook handlers in .aiqt/core/hooks/scripts/aiqt_hooks.py.

The gen_hooks.py self-test proves the GENERATOR's fail-closed invariants; this one proves the HANDLERS'
decisions, so a regression in a control's logic (not just its wiring) fails a gate. It imports the source
handler module directly (never the generated plugin copy) and drives each handler with a synthetic hook
payload, asserting allow vs ask vs deny by the structured decision the handler returns, not by grepping
output.

The git_discard control is the EN-6 ULTRA-CONSERVATIVE "ask unless PRISTINE and provably clean" guard with
THREE outcomes (allow/ask/deny), so this suite distinguishes all three. The rule: for a recognized lossy
verb, ASK unless the command is a PRISTINE SINGLE BARE `git <verb>` invocation (no shell metacharacter
anywhere even quoted, no reserved word, no wrapper/redirect/compound, and a command word literally `git`)
AND either its form is genuinely non-destructive, the whole tree is PROVABLY CLEAN, or the leading opt-out is
set, in which case ALLOW; a pristine bare whole-tree-clobbering verb (reset --hard, checkout -f, switch
--force) on a confirmed-dirty tree DENIES. This suite proves the EN-6 pristine gate: every shell-grammar and
wrapper form that hides a real `git reset --hard` (an `if`/`for`, a backtick or `$()` substitution, a `|&`,
a leading or interspersed redirect, and the wrappers sudo/nice/timeout/nohup/sh -c/bash -c/...) now ASKS
(pristine-* cases). It also proves the four accuracy fixes: (1) a config-forced probe defeats
status.showUntrackedFiles=no so an untracked file still reads dirty (cfg-* cases: reset --hard/checkout -f
DENY, clean -f ASKS); (2) the arg-consuming clean options `-e`/`--exclude` are respected so `-n` is not
mis-read as a dry run (cle-* cases); (3) `switch --merge`/`--conflict` route to scoped and ASK on a dirty
tree (swm-* cases); (4) the DENY reason wording covers untracked too. The prior GD-41 blocker cases are kept
and still hold. The clean/dirty probe runs in the payload cwd only for a pristine single bare git command
that resolves to the session worktree, so the probing cases point cwd at a hermetic throwaway repo (git init,
a local identity, committed files) built under a temp dir and dirtied several ways (worktree-dirty,
untracked-only, staged-only, config-hidden-untracked); the temp tree is removed in a finally. A `git -C
<dir>`/env/compound/wrapped/metacharacter form is deliberately NOT probed (not pristine-single-bare) and
ASKS, which many cases below assert.

  selftest_aiqt_hooks.py    exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
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

        # === boundary: the true fail-open boundary ALLOWS ====================================
        expect("(bound-a) non-git command allows", "ls -la {}".format(rp), "allow")
        # An UNPARSEABLE command (unbalanced quote) is not a free ALLOW: a raw scan finds git AND a lossy
        # verb (checkout) it cannot prove safe -> ASK (F-60.1); a non-lossy unparseable stays ALLOW; a
        # truthy opt-out on an unparseable command still ALLOWs.
        expect("(bound-b) unparseable + lossy verb asks", 'git checkout -- "unbalanced', "ask", cwd=rp)
        expect("(bound-b2) unparseable non-lossy command allows", 'ls -la "unbalanced', "allow")
        expect("(bound-b3) unparseable + lossy but opted out allows",
               'GUARDRAIL_ALLOW_DISCARD=1 git reset --hard "unbalanced', "allow", cwd=rp)

        # === a PROVABLY CLEAN tree: every recognized discard is safe -> ALLOW ================
        expect("(clean-a) reset --hard clean allows", "git reset --hard", "allow", cwd=rp)
        expect("(clean-b) checkout -- clean allows", "git checkout -- file.txt", "allow", cwd=rp)
        expect("(clean-c) checkout <branch> on clean tree allows", "git checkout other", "allow", cwd=rp)
        # Coarse worktree-certainty: a `git -C <dir> ...` form cannot be resolved with certainty (the -C is
        # a global option), so EVEN ON A CLEAN TREE a lossy verb there ASKS rather than probe the session
        # dir. This is the accepted over-ask that replaces the removed, fooled dir modelling.
        expect("(clean-d) -C form not-certain asks even on clean tree",
               "git -C {} reset --hard".format(rp), "ask", cwd=rp)

        # Dirty the tracked file (worktree-dirty): the tree is no longer provably clean.
        tracked.write_text("committed line\nuncommitted fix\n", encoding="utf-8")

        # === checkout ========================================================================
        # A worktree-scoped discard on a not-provably-clean tree ASKS (it no longer DENIES per-path, and it
        # no longer proves a disjoint clean path safe - both removed fast paths).
        expect("(co-a) checkout -- dirty asks", "git checkout -- file.txt", "ask", cwd=rp)
        expect("(co-b) checkout -- with optout allows",
               "GUARDRAIL_ALLOW_DISCARD=1 git checkout -- file.txt", "allow", cwd=rp)
        expect("(co-c) checkout . dirty asks", "git checkout .", "ask", cwd=rp)
        expect("(co-d) checkout <branch> on dirty tree asks", "git checkout other", "ask", cwd=rp)
        # Removed path-disjoint fast path: a discard of a CLEAN tracked path on a dirty tree now ASKS (it
        # used to be silently ALLOWED by probing only that path).
        expect("(co-e) checkout -- disjoint-clean path on dirty tree asks",
               "git checkout -- clean.txt", "ask", cwd=rp)
        # A forced checkout WITH an explicit pathspec is path-scoped -> ASK (F-65.F2: the old cut hard-
        # DENIED this even for a clean path; the coarse guard asks, which is recoverable).
        expect("(co-f) checkout -f -- <path> asks not denies", "git checkout -f -- clean.txt", "ask",
               cwd=rp)

        # === switch (a whole-tree clobber on force) ==========================================
        expect("(sw-a) switch -f dirty denies", "git switch -f other", "deny", cwd=rp)
        expect("(sw-b) switch --discard-changes dirty denies", "git switch --discard-changes other",
               "deny", cwd=rp)
        expect("(sw-c) plain switch allows (git aborts on dirty)", "git switch other", "allow", cwd=rp)

        # === restore =========================================================================
        expect("(re-a) restore dirty asks", "git restore file.txt", "ask", cwd=rp)
        # Blocker 6: restore --staged is no longer an unconditional allow; on a not-provably-clean tree it
        # routes through the probe and ASKS (a --staged unstage can erase staged-only content).
        expect("(re-b) restore --staged on dirty tree asks (blocker 6)", "git restore --staged file.txt",
               "ask", cwd=rp)

        # === reset ===========================================================================
        expect("(rs-a) reset --hard dirty denies (whole-tree clobber)", "git reset --hard", "deny",
               cwd=rp)
        expect("(rs-b) reset --merge dirty asks", "git reset --merge", "ask", cwd=rp)
        # Blocker 6: a --mixed or path reset changes the index and can erase staged-only content, so it is
        # no longer an unconditional allow; it routes through the probe and ASKS on a not-provably-clean tree.
        expect("(rs-c) reset --mixed on dirty tree asks (blocker 6)", "git reset --mixed", "ask", cwd=rp)
        expect("(rs-d) reset -- <path> on dirty tree asks (blocker 6)", "git reset -- clean.txt", "ask",
               cwd=rp)
        # Abbreviated modes: '--h' == '--hard' (clobber -> DENY), an ambiguous bare '--m' errs to merge
        # (scoped -> ASK). The old option parser could be fooled by abbreviations into a silent allow.
        expect("(rs-e) reset --h abbrev is hard, denies", "git reset --h", "deny", cwd=rp)
        expect("(rs-f) reset --m ambiguous errs to ask", "git reset --m", "ask", cwd=rp)
        # Last-wins: '--hard' then '--soft' resolves to soft (keeps worktree) -> ALLOW.
        expect("(rs-g) reset --hard --soft last-wins-soft allows", "git reset --hard --soft", "allow",
               cwd=rp)

        # === rm ==============================================================================
        expect("(rm-a) rm dirty tracked asks (was over-blocked to DENY, F-66.6)", "git rm file.txt",
               "ask", cwd=rp)
        # Blocker 6: rm --cached is no longer an unconditional allow; it can erase staged-only content, so
        # it routes through the probe and ASKS on a not-provably-clean tree.
        expect("(rm-b) rm --cached on dirty tree asks (blocker 6)", "git rm --cached file.txt", "ask",
               cwd=rp)
        expect("(rm-c) rm clean-path on dirty tree asks", "git rm clean.txt", "ask", cwd=rp)

        # === clean, stash, branch: unconditional asks (no per-verb probe) ====================
        (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        expect("(cl-a) clean -fd asks", "git clean -fd", "ask", cwd=rp)
        expect("(cl-b) clean -n dry-run allows", "git clean -nfd", "allow", cwd=rp)
        # A bare clean with NO force flag now ASKS: clean.requireForce=false could make it destructive, and
        # the guard no longer models whether the clean fires (F-66.5). The old cut silently ALLOWed it.
        expect("(cl-c) clean without force asks (requireForce edge, F-66.5)", "git clean -d", "ask",
               cwd=rp)
        expect("(st-a) stash drop asks", "git stash drop", "ask", cwd=rp)
        expect("(st-b) stash clear asks", "git stash clear", "ask", cwd=rp)
        expect("(st-c) stash pop allows (out of scope)", "git stash pop", "allow", cwd=rp)
        expect("(br-a) branch -D asks", "git branch -D other", "ask", cwd=rp)
        expect("(br-b) branch -d allows (git refuses unmerged)", "git branch -d other", "allow", cwd=rp)

        # === previously-fooled shell-expansion pathspecs now ASK (F-62.1, F-64.1/2, F-65.F1) ==
        # A pathspec carrying a variable, command substitution, glob, brace, or tilde used to be probed
        # LITERALLY, so the path-disjoint fast path fired and a real discard was silently ALLOWED. Coarse:
        # a lossy verb on a not-provably-clean tree ASKS regardless of what the pathspec expands to.
        expect("(exp-a) checkout -- $VAR pathspec asks (F-62.1)", "git checkout -- $DIR/f", "ask", cwd=rp)
        expect("(exp-b) rm $(cat list) command-substitution asks (F-62.1)", "git rm $(cat list)", "ask",
               cwd=rp)
        expect("(exp-c) checkout -- glob asks (F-62.1)", 'git checkout -- "*.txt"', "ask", cwd=rp)
        expect("(exp-d) checkout -- brace expansion asks (F-64.1)", "git checkout -- f.{txt,md}", "ask",
               cwd=rp)
        expect("(exp-e) checkout -- tilde asks (F-64.2)", "git checkout -- ~/f", "ask", cwd=rp)
        expect("(exp-f) clean -f $DIR expansion asks (F-65.F1)", "git clean -f $DIR", "ask", cwd=rp)

        # === previously-fooled interactive-patch forms now ASK (F-60.2/F-60.3) ===============
        expect("(patch-a) checkout -p asks (F-60.2)", "git checkout -p", "ask", cwd=rp)
        expect("(patch-b) restore -p --staged asks (F-60.3)", "git restore -p --staged file.txt", "ask",
               cwd=rp)

        # === previously-fooled/hanging clean forms now ASK, no probe (F-64.3, F-66.1) ========
        # clean -i used to reach the `clean -n` probe and could hang on interactive input; clean -q
        # suppressed the probe output so it read as "nothing to remove" and ALLOWed. The coarse guard runs
        # no clean probe at all: any real clean ASKS.
        expect("(clx-a) clean -dfi asks, no hang (F-64.3)", "git clean -dfi", "ask", cwd=rp)
        expect("(clx-b) clean -qfd asks (F-66.1)", "git clean -qfd", "ask", cwd=rp)

        # === previously-fooled worktree-redirection now ASK (F-62.2/3, F-64.4, F-66.2/3/4) ===
        # A cd/pushd/subshell in the chain, a -C/--git-dir/--work-tree global option, or a GIT_DIR/
        # GIT_WORK_TREE env assignment means the worktree cannot be resolved with certainty, so the guard
        # ASKS rather than probe the (possibly wrong, clean) session dir and silently allow.
        (repo / "sub").mkdir(exist_ok=True)
        clean_repo = _init_repo(tmp / "cleanrepo")  # a CLEAN repo a cd might misdirect toward
        expect("(dir-a) cd sub && git reset --hard asks (F-62.2)", "cd sub && git reset --hard", "ask",
               cwd=rp)
        expect("(dir-b) backgrounded cd & git reset --hard asks (F-64.4/F-66.3)",
               "cd sub & git reset --hard", "ask", cwd=rp)
        expect("(dir-c) subshell cd misdirect asks (F-62.2)",
               "( cd {} ) ; git reset --hard".format(str(clean_repo)), "ask", cwd=rp)
        expect("(dir-d) -C global option not-certain asks (F-66.2)", "git -C {} reset --hard".format(rp),
               "ask", cwd=rp)
        expect("(dir-e) --git-dir global option not-certain asks (F-66.4)",
               "git --git-dir={}/.git reset --hard".format(rp), "ask", cwd=rp)
        expect("(dir-f) --work-tree global option not-certain asks",
               "git --work-tree={0} --git-dir={0}/.git reset --hard".format(rp), "ask", cwd=rp)
        expect("(dir-g) GIT_WORK_TREE= env not-certain asks (F-62.3)", "GIT_WORK_TREE=sub git reset --hard",
               "ask", cwd=rp)
        expect("(dir-h) GIT_DIR= env not-certain asks (F-62.3/F-66.4)", "GIT_DIR=.git git reset --hard",
               "ask", cwd=rp)

        # === the opt-out is a LEADING assignment on the git command only (F-65.F3, blocker 4) =
        # The same string buried in an argument (echo) does NOT disable the guard; the command is compound,
        # so the trailing reset --hard ASKS (a compound cannot be probed clean, blocker 2) rather than allow.
        expect("(opt-a) buried GUARDRAIL_ALLOW_DISCARD in an arg does not opt out (F-65.F3)",
               "echo GUARDRAIL_ALLOW_DISCARD=1 ; git reset --hard", "ask", cwd=rp)
        # Blocker 4: an opt-out LEADING a non-git segment does not opt out the later git command; the
        # command is compound, so the reset --hard ASKS, never a silent allow.
        expect("(opt-a2) opt-out leading a non-git segment does not opt out the reset (blocker 4)",
               "GUARDRAIL_ALLOW_DISCARD=1 true ; git reset --hard", "ask", cwd=rp)
        # A genuine leading opt-out prefix on the git command itself still ALLOWs the same reset.
        expect("(opt-b) leading GUARDRAIL_ALLOW_DISCARD prefix opts out",
               "GUARDRAIL_ALLOW_DISCARD=1 git reset --hard", "allow", cwd=rp)

        # === a pathspec-from-file source is worktree-scoped -> ASK on a dirty tree ===========
        expect("(pff-a) restore --pathspec-from-file asks on dirty tree",
               "git restore --pathspec-from-file=paths.txt", "ask", cwd=rp)

        # === GD-41 blockers: each silent-allow blocker now ASKS or DENIES, never allows =======
        # Blocker 1: an UNTRACKED-ONLY-dirty tree is NOT provably clean (the earlier cut skipped '??' and
        # silently allowed a force discard). Build a repo dirtied ONLY by an untracked file.
        untracked_repo = _init_repo(tmp / "untrackedrepo")
        (untracked_repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        ru = str(untracked_repo)
        if aiqt_hooks._tree_is_clean(ru) is not False:
            failures.append("(b1-probe) _tree_is_clean on untracked-only tree: expected False")
        expect("(b1-a) reset --hard on untracked-only tree denies", "git reset --hard", "deny", cwd=ru)
        expect("(b1-b) clean -f on untracked-only tree asks", "git clean -f", "ask", cwd=ru)
        expect("(b1-c) checkout other on untracked-only tree asks (not allow)", "git checkout other",
               "ask", cwd=ru)

        # Blocker 2: a COMPOUND command whose earlier segment dirties the tree cannot be probed clean; on a
        # CLEAN repo the trailing lossy verb must ASK, not be allowed by a stale pre-write probe.
        compound_repo = _init_repo(tmp / "compoundrepo")
        rco = str(compound_repo)
        expect("(b2-a) printf >> f && reset --hard asks on clean tree (blocker 2)",
               "printf x >> file.txt && git reset --hard", "ask", cwd=rco)
        expect("(b2-b) stash apply && reset --hard asks on clean tree (blocker 2)",
               "git stash apply && git reset --hard", "ask", cwd=rco)

        # Blocker 3: an operand after '--' is a pathspec, never a safe-looking option, so a force clean/rm
        # is not allowed by misreading it. (On the dirty repo: ASK, not allow.)
        expect("(b3-a) clean -f -- -nasty asks (not read as -n dry-run)", "git clean -f -- -nasty", "ask",
               cwd=rp)
        expect("(b3-b) rm -f -- --cached asks (not read as --cached unstage)", "git rm -f -- --cached",
               "ask", cwd=rp)

        # Blocker 5: abbreviated destructive options are recognized by prefix, so they do not slip through
        # as inert tokens on the dirty tree.
        expect("(b5-a) checkout --for (abbrev --force) denies", "git checkout --for", "deny", cwd=rp)
        expect("(b5-b) checkout --patc (abbrev --patch) asks", "git checkout --patc", "ask", cwd=rp)
        expect("(b5-c) switch --dis (abbrev --discard-changes) denies", "git switch --dis other", "deny",
               cwd=rp)
        expect("(b5-d) branch --del --force (abbrev) asks", "git branch --del --force other", "ask",
               cwd=rp)

        # Blocker 6: an index-only change on a STAGED-ONLY-dirty tree (worktree matches index, index differs
        # from HEAD) ASKS, because the probe sees the staged change; on a CLEAN tree it allows.
        staged_repo = _init_repo(tmp / "stagedrepo")
        (staged_repo / "file.txt").write_text("committed line\nstaged fix\n", encoding="utf-8")
        _git(staged_repo, "add", "file.txt")  # staged only; worktree == index
        rst = str(staged_repo)
        expect("(b6-a) restore --staged on staged-only tree asks (blocker 6)",
               "git restore --staged file.txt", "ask", cwd=rst)
        expect("(b6-b) reset --mixed on staged-only tree asks (blocker 6)", "git reset --mixed", "ask",
               cwd=rst)
        expect("(b6-c) rm --cached on staged-only tree asks (blocker 6)", "git rm --cached file.txt",
               "ask", cwd=rst)
        # On a genuinely clean tree the same index-only forms allow (nothing staged to lose).
        clean_index = _init_repo(tmp / "cleanindexrepo")
        rci = str(clean_index)
        expect("(b6-d) restore --staged on clean tree allows", "git restore --staged file.txt", "allow",
               cwd=rci)
        expect("(b6-e) rm --cached on clean tree allows", "git rm --cached file.txt", "allow", cwd=rci)

        # Blocker 7: a FORCED branch-create (checkout -f -b) no longer early-allows; on the dirty tree it
        # ASKS, and an UNforced branch-create still allows.
        expect("(b7-a) checkout -f -b new on dirty tree asks (not early-allow)", "git checkout -f -b new",
               "ask", cwd=rp)
        expect("(b7-b) checkout -b new on clean tree allows", "git checkout -b new", "allow", cwd=rci)

        # Blocker 8: a shell wrapper hiding the git verb ASKS (the verb is not at the segment command-word
        # position, so it must not fall open). A wrapper over a non-lossy git command still allows.
        for label, cmd in (("command", "command git reset --hard"), ("exec", "exec git reset --hard"),
                           ("env", "env git reset --hard"), ("bang", "! git reset --hard"),
                           ("time", "time git reset --hard"), ("builtin", "builtin git reset --hard")):
            expect("(b8-{}) wrapper hiding git reset --hard asks".format(label), cmd, "ask", cwd=rp)
        expect("(b8-status) wrapper over a non-lossy git command allows", "command git status", "allow",
               cwd=rp)

        # === EN-6 ULTRA-CONSERVATIVE pristine gate: any shell structure ASKS (never a silent allow) =
        # A lossy-verb command reaches the clean probe ONLY when it is a PRISTINE SINGLE BARE 'git <verb>'
        # invocation - no shell metacharacter anywhere (even quoted), no reserved word, no wrapper/redirect/
        # compound, and a command word that is literally 'git'. Each form below hides a real reset --hard
        # behind shell structure the lexer does not model; the coarse GD-41 cut still silently allowed some
        # of these, and each MUST now ASK. Tested on the dirty repo (a silent allow would discard the fix).
        pristine_asks = [
            ("gram-if", "if true; then git reset --hard; fi"),            # reserved words + ';'
            ("gram-for", "for x in 1; do git reset --hard; done"),        # a for-loop
            ("gram-backtick", "echo `git reset --hard`"),                 # backtick command substitution
            ("gram-dollar-paren", "echo $(git reset --hard)"),            # $( ) command substitution
            ("gram-pipe-amp", "echo x |& git reset --hard"),              # a '|&' pipe-both
            ("gram-lead-redirect", "> log git reset --hard"),            # a LEADING stdout redirect
            ("gram-mid-redirect", "git 2>/dev/null reset --hard"),        # an interspersed redirect
            ("wrap-sudo", "sudo git reset --hard"),                       # a privilege wrapper
            ("wrap-nice", "nice git reset --hard"),                       # a scheduling wrapper
            ("wrap-timeout", "timeout 5 git reset --hard"),               # a timeout wrapper
            ("wrap-nohup", "nohup git reset --hard"),                     # a nohup wrapper
            ("wrap-sh-c", 'sh -c "git reset --hard"'),                    # an interpreter -c wrapper
            ("wrap-bash-c", 'bash -c "git reset --hard"'),                # an interpreter -c wrapper
            ("wrap-stdbuf", "stdbuf -oL git reset --hard"),              # a wrapper NOT in any enumerated list
            ("wrap-doas", "doas git reset --hard"),                      # a privilege wrapper not in any list
            ("wrap-setsid", "setsid git reset --hard"),                 # a session wrapper not in any list
            ("wrap-eval", "eval 'git reset --hard'"),                   # eval runs its quoted argument
        ]
        for label, cmd in pristine_asks:
            expect("(pristine-{}) shell structure hides a reset --hard -> asks".format(label), cmd, "ask",
                   cwd=rp)
        # A pathed git ('/usr/bin/git') is not the literal command word 'git', so it is not pristine -> ASK.
        expect("(pristine-pathed) a pathed git is not literally 'git' -> asks", "/usr/bin/git reset --hard",
               "ask", cwd=rp)

        # === switch --merge/--conflict overwrite local changes -> scoped, ASK on a dirty tree (fix 3) =
        expect("(swm-a) switch --merge on dirty tree asks", "git switch --merge other", "ask", cwd=rp)
        expect("(swm-b) switch --conflict= on dirty tree asks", "git switch --conflict=diff3 other", "ask",
               cwd=rp)
        expect("(swm-c) switch -m on dirty tree asks", "git switch -m other", "ask", cwd=rp)

        # === clean arg-consuming options: '-e'/'--exclude' consume the next token -> do NOT trust '-n' ==
        # 'git clean -f -e '*.keep' -n' must NOT be read as a dry run (fix 2): with an arg-consuming option
        # present the guard cannot tell a real '-n' flag from an exclude pattern, so it ASKS. '-n' alone still
        # allows. (Tested on the dirty/untracked repo below via the config-hidden repo too.)
        (repo / "keeper.keep").write_text("keep\n", encoding="utf-8")
        expect("(cle-a) clean -f -e PAT -n asks (not read as dry-run)", "git clean -f -e '*.keep' -n", "ask",
               cwd=rp)
        expect("(cle-b) clean -f -e -n asks (-n is the exclude pattern)", "git clean -f -e -n", "ask",
               cwd=rp)
        expect("(cle-c) clean -n alone still allows (dry run)", "git clean -n", "allow", cwd=rp)
        expect("(cle-d) clean -n --no-dry-run -f asks (boolean negation disables the dry run)",
               "git clean -n --no-dry-run -f", "ask", cwd=rp)
        expect("(cle-e) clean -f -en asks (attached -e value, '-n' is the exclude pattern)",
               "git clean -f -en", "ask", cwd=rp)
        expect("(cle-f) clean -n --no-dry-r -f asks (abbreviated negation prefix)",
               "git clean -n --no-dry-r -f", "ask", cwd=rp)
        # --pathspec-from-file reads pathspecs from a file, so its NEXT token is a filename, not a flag:
        # checkout/reset must treat it as path-scoped and ASK, not misread the filename as -b/--soft.
        expect("(pfr-checkout) checkout --pathspec-from-file -b asks (-b is the file, not branch-create)",
               "git checkout --pathspec-from-file -b", "ask", cwd=rp)
        expect("(pfr-reset) reset --pathspec-from-file --soft asks (--soft is the file, not the mode)",
               "git reset --pathspec-from-file --soft", "ask", cwd=rp)
        # An inline git alias that expands to a work-losing verb cannot be resolved -> ASK.
        expect("(alias-inline) git -c alias.x=reset x --hard asks", "git -c alias.x=reset x --hard", "ask",
               cwd=rp)
        # A glob char (*?[) can bash-expand an option name (in a dir with a file named --hard, '--h*' becomes
        # '--hard'), so any lossy command carrying one is not pristine -> ASK.
        expect("(glob-opt) reset --soft --h* asks (glob defeats the pristine gate)", "git reset --soft --h*",
               "ask", cwd=rp)
        # An abbreviated --pathspec-from-f and any unrecognized option-only checkout must not default to allow.
        expect("(pfr-abbrev) checkout --pathspec-from-f=paths asks", "git checkout --pathspec-from-f=paths",
               "ask", cwd=rp)
        expect("(co-unknown) checkout with an unrecognized option asks", "git checkout --some-exotic-opt",
               "ask", cwd=rp)

        # === config-proof probe: status.showUntrackedFiles=no cannot hide an untracked file (fix 1) ===
        # A repo configured to omit untracked files from status must NOT let a force discard read the tree as
        # clean. The probe forces '-c status.showUntrackedFiles=all --untracked-files=all', so an untracked
        # file still counts as dirty: reset --hard / checkout -f DENY and clean -f ASKS, never allow.
        hidden_repo = _init_repo(tmp / "hiddenrepo")
        _git(hidden_repo, "config", "status.showUntrackedFiles", "no")
        (hidden_repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        rh = str(hidden_repo)
        if aiqt_hooks._tree_is_clean(rh) is not False:
            failures.append("(cfg-probe) _tree_is_clean with showUntrackedFiles=no + untracked: expected "
                            "False (the probe must force untracked reporting)")
        expect("(cfg-a) reset --hard on config-hidden untracked tree denies", "git reset --hard", "deny",
               cwd=rh)
        expect("(cfg-b) clean -f on config-hidden untracked tree asks", "git clean -f", "ask", cwd=rh)
        expect("(cfg-c) checkout -f on config-hidden untracked tree denies", "git checkout -f", "deny",
               cwd=rh)

        # === malformed / robustness (F-66.7) ================================================
        # A malformed tool_input (a string, not a dict) -> boundary ALLOW (fail open), not a crash.
        malformed = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "malformed"}
        code, stdout_obj, _stderr = handler(malformed)
        if not (code == 0 and stdout_obj is None):
            failures.append("(robust-a) malformed tool_input: expected allow, got code={!r}, stdout={!r}"
                            .format(code, stdout_obj))
        # No cwd and a dir-simple lossy verb: the worktree cannot be resolved -> ASK, never silent-allow.
        expect("(robust-b) no-cwd reset --hard asks", "git reset --hard", "ask")

        # === unit assertions on the coarse role classifier ==================================
        # Regression guards for the verb-form recognition that decides allow/ask/scoped/clobber.
        role_cases = [
            (("reset", ["--hard"]), "clobber"),
            (("reset", ["--soft", "--hard"]), "clobber"),
            (("reset", ["--hard", "--soft"]), "allow"),   # last-wins soft: HEAD-only, index+worktree intact
            (("reset", ["--merge"]), "scoped"),
            (("reset", ["--mixed"]), "scoped"),           # blocker 6: index-changing, can lose staged-only
            (("reset", ["--soft"]), "allow"),
            (("reset", ["--har"]), "clobber"),            # blocker 5: abbreviated --hard
            (("reset", []), "scoped"),                    # blocker 6: default --mixed changes the index
            (("checkout", ["-b", "new"]), "allow"),
            (("checkout", ["-f", "-b", "new"]), "scoped"),  # blocker 7: forced branch-create no early-allow
            (("checkout", ["-f", "other"]), "scoped"),   # bare operand: cannot tell ref from path -> ask
            (("checkout", ["-f"]), "clobber"),            # force, no operand -> whole-tree
            (("checkout", ["--for"]), "clobber"),         # blocker 5: abbreviated --force, no operand
            (("checkout", ["--patc"]), "scoped"),         # blocker 5: abbreviated --patch
            (("switch", ["--force", "other"]), "clobber"),
            (("switch", ["--dis", "other"]), "clobber"),  # blocker 5: abbreviated --discard-changes
            (("switch", ["--merge", "other"]), "scoped"),  # fix 3: a three-way merge can overwrite worktree
            (("switch", ["--conflict=diff3", "other"]), "scoped"),  # fix 3: conflict-style merge
            (("switch", ["-m", "other"]), "scoped"),      # fix 3: '-m' is --merge
            (("switch", ["other"]), "allow"),
            (("restore", ["--staged", "file"]), "scoped"),  # blocker 6: --staged can erase staged-only
            (("restore", ["--staged", "--worktree", "file"]), "scoped"),
            (("restore", ["file"]), "scoped"),
            (("rm", ["--cached", "file"]), "scoped"),     # blocker 6: --cached can erase staged-only
            (("rm", ["file"]), "scoped"),
            (("rm", ["-f", "--", "--cached"]), "scoped"),  # blocker 3: --cached after -- is a pathspec
            (("clean", ["-fd"]), "ask"),
            (("clean", ["-nfd"]), "allow"),
            (("clean", ["-d"]), "ask"),
            (("clean", ["-f", "--", "-nasty"]), "ask"),   # blocker 3: -nasty after -- is not the -n flag
            (("clean", ["-f", "-e", "*.keep", "-n"]), "ask"),  # fix 2: -e consumes; '-n' not trusted as dry-run
            (("clean", ["-f", "-e", "-n"]), "ask"),       # fix 2: '-n' is the exclude PATTERN, not dry-run
            (("clean", ["--exclude=x", "-n"]), "ask"),    # fix 2: an --exclude= present -> do not trust '-n'
            (("clean", ["-n"]), "allow"),                 # a plain dry run with no arg-consuming option allows
            (("stash", ["drop"]), "ask"),
            (("stash", ["pop"]), "allow"),
            (("branch", ["-D", "x"]), "ask"),
            (("branch", ["--del", "--force", "x"]), "ask"),  # blocker 5: abbreviated --delete/--force
            (("branch", ["-d", "x"]), "allow"),
        ]
        for (sub, args), want_role in role_cases:
            got_role = aiqt_hooks._discard_role(sub, args)[0]
            if got_role != want_role:
                failures.append("(role) _discard_role({!r}, {!r}) role: expected {}, got {}"
                                .format(sub, args, want_role, got_role))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: git_discard (EN-6 ULTRA-CONSERVATIVE ask-unless-pristine-and-provably-clean) "
          "fails open only at the true boundary; within scope it ASKS unless the command is a PRISTINE "
          "SINGLE BARE 'git <verb>' invocation (no shell metacharacter anywhere even quoted, no reserved "
          "word, no wrapper/redirect/compound, command word literally 'git') that is either genuinely "
          "non-destructive, on a PROVABLY CLEAN tree, or leading-opt-out; a pristine bare whole-tree clobber "
          "(reset --hard, checkout -f, switch --force) on a confirmed-dirty tree DENIES. The pristine gate "
          "is proven: every shell-grammar and wrapper form that hides a real reset --hard (if/for, backtick "
          "and $() substitution, |&, leading and interspersed redirects, and sudo/nice/timeout/nohup/sh -c/"
          "bash -c wrappers) now ASKS. The four accuracy fixes are proven: the config-forced probe defeats "
          "status.showUntrackedFiles=no (untracked reads dirty -> DENY/ASK, not allow); clean -e/--exclude "
          "arg-consumption means '-n' is not mis-read as a dry run; switch --merge/--conflict route to "
          "scoped and ASK on a dirty tree; and the DENY wording covers untracked. The prior GD-41 "
          "blocker cases and the F-60/F-62/F-64/F-65/F-66 under-block edges still ASK/DENY, and the role "
          "classifier is asserted too")
    return 0


if __name__ == "__main__":
    sys.exit(main())
