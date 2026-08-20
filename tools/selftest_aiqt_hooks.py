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
wrapper form that hides a real `git reset --hard` while the raw scan still sees a contiguous git+verb keyword
(an `if`/`for`, a backtick or `$()` substitution, a `|&`, a leading or interspersed redirect, and the
wrappers sudo/nice/timeout/nohup/sh -c/bash -c/...) now ASKS (pristine-* cases); a wrapper that ALSO
fragments the command word `git` or the verb so no recognized lossy verb is seen is a DISCLOSED best-effort
residual, silently ALLOWED and not chased. It also proves the four accuracy fixes: (1) a config-forced probe defeats
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

It also covers the protected-line guard (protected_line, prtbrn/artbr1): a force-push to a
protected branch (main/master) denies and to a feature branch allows, a refspec-less force-push
and a direct commit are judged by a read-only HEAD probe (fail-to-ASK when unresolvable), a
--mirror/--all sweep asks, and the parse-error fallback fails safe.

  selftest_aiqt_hooks.py    exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
import datetime
import json
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


def _recovery_refs(repo):
    """The list of refs under refs/aiqt-recovery/ in repo (the private snapshot namespace the EN-6 recovery
    layer writes). Empty when no snapshot has been taken. RAISES on a non-zero for-each-ref return so an
    unreadable ref list can never be mistaken for an empty one (a broken listing must not falsely 'prove'
    that no snapshot was made)."""
    out = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)",
                          "refs/aiqt-recovery/"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError("for-each-ref refs/aiqt-recovery/ failed in {} (rc={}): {}"
                           .format(repo, out.returncode, out.stderr.strip()))
    return [r for r in out.stdout.splitlines() if r.strip()]


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
    # Redirect the recovery ledger into the throwaway tree so the EN-6 recovery layer, which fires on every
    # dirty-tree in-scope case below, never writes to the real user's $XDG_STATE_HOME/~/.local/state.
    os.environ["XDG_STATE_HOME"] = str(tmp / "xdgstate")
    # Make the fail-safe repository-view check (git_discard) deterministic: scrub any ambient GIT_* the
    # gate's own runner happens to carry (a shell may export GIT_EDITOR, CI may export others), so each case
    # below is judged ONLY against the GIT_* vars it explicitly sets. Production still reads the real
    # os.environ; this scrub is a test-harness isolation, not a change to the control.
    for _amb in [k for k in os.environ if k.startswith("GIT_")]:
        os.environ.pop(_amb, None)
    # F-106 regression guard: run the whole self-test with NO ambient git identity, so the EN-6 recovery
    # snapshot's `git commit-tree` must supply its OWN fixed identity to succeed. The recovery layer scrubs
    # GIT_CONFIG_*/GIT_AUTHOR_*/GIT_COMMITTER_* itself (the allowlist posture), but NOT HOME/XDG_CONFIG_HOME,
    # so pointing those at an EMPTY throwaway dir is what actually denies the snapshot a global-config
    # identity, reproducing CI's no-global-gitconfig condition LOCALLY (why F-106 passed here but failed in
    # CI), so a future regression that reintroduces an ambient-identity dependence in the snapshot fails THIS
    # gate, not only in CI. HOME/XDG_CONFIG_HOME are NOT GIT_*-prefixed, so this does not trip the guard's
    # own ambient-GIT_* view-override check (unlike GIT_CONFIG_GLOBAL/SYSTEM, which are non-cosmetic and would
    # force every case to ASK), and the recovery layer scrubs GIT_CONFIG_* regardless, so nulling those would
    # not reach its commit-tree anyway. The seed-repo commits set their identity inline via `-c user.name=...`
    # (env_identity), so they are unaffected; the rec-ambient case still injects and removes its own
    # GIT_AUTHOR_*/GIT_COMMITTER_* to prove an ambient identity does not break the snapshot.
    _emptyhome = tmp / "emptyhome"
    _emptyhome.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(_emptyhome)
    os.environ["XDG_CONFIG_HOME"] = str(_emptyhome / "xdgconfig")
    for _idk in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        os.environ.pop(_idk, None)
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
        # verb (checkout) it cannot prove safe -> ASK (F-60.1); a non-lossy unparseable stays ALLOW. Round-15
        # STRUCTURAL fix: the opt-out is NOT consulted on the unparseable path (the guard cannot parse the
        # command, so it cannot soundly trust an opt-out-looking prefix inside it), so an opt-out-prefixed
        # unparseable in-scope command ALSO ASKS (see the r15-raw-* battery below).
        expect("(bound-b) unparseable + lossy verb asks", 'git checkout -- "unbalanced', "ask", cwd=rp)
        expect("(bound-b2) unparseable non-lossy command allows", 'ls -la "unbalanced', "allow")
        expect("(bound-b3) unparseable + lossy + opt-out prefix still ASKS (round-15: opt-out not honoured on unparseable)",
               'GUARDRAIL_ALLOW_DISCARD=1 git reset --hard "unbalanced', "ask", cwd=rp)

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
        # EN-6 round-21 Fix B (text-only contract correction, NO logic change): LOCK the checkout -f
        # outcomes so the docstring rewording cannot silently drift them. A forced checkout carrying a BARE
        # OPERAND is lexically ambiguous (a branch OR a pathspec), so it is scoped -> ASK (recoverable and
        # human-gated), never a hard DENY that would false-block a legitimate forced path-restore; only an
        # operand-FREE forced whole-tree checkout DENIES on a confirmed-dirty tree.
        expect("(r21b-1) checkout -f <branch> (bare operand) asks, not denies", "git checkout -f main",
               "ask", cwd=rp)
        expect("(r21b-2) checkout -f (operand-free) denies on a dirty tree", "git checkout -f", "deny",
               cwd=rp)

        # === switch (a whole-tree clobber on force) ==========================================
        expect("(sw-a) switch -f dirty denies", "git switch -f other", "deny", cwd=rp)
        expect("(sw-b) switch --discard-changes dirty denies", "git switch --discard-changes other",
               "deny", cwd=rp)
        expect("(sw-c) plain switch allows (git aborts on dirty)", "git switch other", "allow", cwd=rp)

        # === EN-6 round-22 Fix 1 (F-81): a force branch-create/RESET on checkout/switch now ASKS =====
        # 'git checkout -B', 'git switch -C', and 'git switch --force-create' force-create or RESET a branch
        # ref exactly like 'git branch -f'/'-M'/'-C' (orphaning committed commits, reflog-recoverable), so
        # they ASK even on a dirty tree; a plain unforced create (-b/-c) keeps its allow. The checkout -f
        # whole-tree outcomes stay locked by (r21b-1) ASK and (r21b-2) DENY above.
        expect("(r22-1) checkout -B force branch-create/reset asks", "git checkout -B foo other", "ask",
               cwd=rp)
        expect("(r22-2) switch -C force branch-create/reset asks", "git switch -C foo other", "ask", cwd=rp)
        expect("(r22-3) switch --force-create force branch-create/reset asks",
               "git switch --force-create foo other", "ask", cwd=rp)
        expect("(r22-4) checkout -b plain create still allows (unchanged)", "git checkout -b foo", "allow",
               cwd=rp)
        expect("(r22-5) switch -c plain create still allows (unchanged)", "git switch -c foo", "allow",
               cwd=rp)

        # === EN-6 round-23 Fix F-85: argument-aware checkout/switch -b/-B/-c/-C parsing ==============
        # The new-branch NAME argument of -b/-B (checkout) and -c/-C (switch), whether ATTACHED ('-bfoo') or
        # SEPARATED ('-b foo'), is that option's value and is NOT char-scanned as clustered force flags. So
        # an attached name whose characters include 'f'/'B'/'C' ('-bfoo', '-bBranch', '-cfeature') is a plain
        # create -> ALLOW even on a dirty tree, never mis-read as carrying -f/-B/-C. The force-create forms
        # (-Bfoo/-B foo, -Cfoo/-C foo, --force-create foo) still ASK. This mirrors the branch -u<upstream>
        # parser (F-82) and closes the round-21 char-scan over-restriction.
        expect("(f85-co1) checkout -bfoo attached name allows", "git checkout -bfoo", "allow", cwd=rp)
        expect("(f85-co2) checkout -bBranch attached name (the B) allows", "git checkout -bBranch", "allow",
               cwd=rp)
        expect("(f85-co3) checkout -b foo separated name allows", "git checkout -b foo", "allow", cwd=rp)
        expect("(f85-co4) checkout -Bfoo attached force-create asks", "git checkout -Bfoo", "ask", cwd=rp)
        expect("(f85-co5) checkout -B foo separated force-create asks", "git checkout -B foo", "ask", cwd=rp)
        expect("(f85-sw1) switch -cfeature attached name allows", "git switch -cfeature", "allow", cwd=rp)
        expect("(f85-sw2) switch -Cfoo attached force-create asks", "git switch -Cfoo", "ask", cwd=rp)
        expect("(f85-sw3) switch -C foo separated force-create asks", "git switch -C foo", "ask", cwd=rp)
        expect("(f85-sw4) switch --force-create foo asks", "git switch --force-create foo", "ask", cwd=rp)

        # === EN-6 round-24 Fix F-88: checkout -m/--merge/--conflict is detected BEFORE the branch-create ==
        # allow, mirroring the switch classifier. A checkout --merge does a three-way merge that can overwrite
        # (and lose) local changes, so -m/--merge/--conflict[=<style>] is worktree-scoped EVEN when combined
        # with a -b create -> ASK on a dirty tree; a plain -b create with NO merge option stays ALLOW. '-m'
        # takes no argument, so '-mb new' == '-m -b new' (the parser treats -m as a flag, -b's arg as the name).
        expect("(f88-co1) checkout -m -b new merge-switch+create asks", "git checkout -m -b new other",
               "ask", cwd=rp)
        expect("(f88-co2) checkout --merge -b new merge-switch+create asks",
               "git checkout --merge -b new other", "ask", cwd=rp)
        expect("(f88-co3) checkout --conflict=merge -b new merge-switch+create asks",
               "git checkout --conflict=merge -b new other", "ask", cwd=rp)
        expect("(f88-co4) checkout -m other merge-switch (no create) asks", "git checkout -m other", "ask",
               cwd=rp)
        expect("(f88-co5) checkout -mb new (== -m -b new) merge-switch+create asks",
               "git checkout -mb new other", "ask", cwd=rp)
        expect("(f88-co6) checkout -b new plain create with no merge option still allows",
               "git checkout -b new", "allow", cwd=rp)
        expect("(f88-co7) checkout -bnew attached-name plain create with no merge option still allows",
               "git checkout -bnew", "allow", cwd=rp)

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

        # === EN-6 round-19 Fix A: an UNPARSEABLE 'git branch' ASKS regardless of any delete flag ====
        # The raw fallback (shlex ValueError) now treats ANY raw 'git' + 'branch' as lossy: it does NOT parse
        # branch flags, so an unparseable 'git branch -d -f topic <heredoc>' / '-df' / '--del --for' can no
        # longer slip past the old '-D'/'--delete'-only raw check into a silent ALLOW; every form ASKS.
        _br_hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes shlex raise -> raw fallback
        expect("(r19a-1) unparseable branch -d -f asks (was a silent allow)",
               "git branch -d -f topic" + _br_hd, "ask", cwd=rp)
        expect("(r19a-2) unparseable branch -df (clustered) asks",
               "git branch -df topic" + _br_hd, "ask", cwd=rp)
        expect("(r19a-3) unparseable branch --del --for (abbrev) asks",
               "git branch --del --for topic" + _br_hd, "ask", cwd=rp)
        # The PARSEABLE branch classifier is UNCHANGED by the raw-path widening: a parseable force-delete
        # still ASKS, and a parseable non-delete branch-create still ALLOWs.
        expect("(r19a-4) parseable branch -d -f still asks", "git branch -d -f other", "ask", cwd=rp)
        expect("(r19a-5) parseable non-delete branch-create still allows", "git branch newbranch",
               "allow", cwd=rp)

        # === EN-6 round-21 Fix A: a PARSEABLE force branch move/rename/copy/reset now ASKS ===========
        # A force MOVE/rename (-M, or -m/--move with --force), a force COPY (-C, or -c/--copy with --force),
        # and a bare force branch RESET (-f/--force with a branch and start-point) each reset or overwrite a
        # branch ref and can orphan committed commits (the same reflog-recoverable loss class as -D), so all
        # ASK. A non-force create and the parseable branch list stay ALLOW; the safe -d delete keeps its
        # allow and the -D force-delete keeps its ask (both unchanged). Closes F-77 (a silent-allow gap).
        expect("(r21a-1) branch -f <branch> <start> force reset asks", "git branch -f topic other", "ask",
               cwd=rp)
        expect("(r21a-2) branch -M force rename asks", "git branch -M a b", "ask", cwd=rp)
        expect("(r21a-3) branch -C force copy asks", "git branch -C a b", "ask", cwd=rp)
        expect("(r21a-4) branch -D force delete still asks (unchanged)", "git branch -D topic", "ask",
               cwd=rp)
        expect("(r21a-5b) branch newbr create still allows", "git branch newbr", "allow", cwd=rp)
        expect("(r21a-6) parseable bare branch (list) allows", "git branch", "allow", cwd=rp)

        # === EN-6 round-22 Fix 2 (F-82): '-u <upstream>' value is not char-scanned as a force flag =====
        # Round-21's blind char-scan read the ATTACHED upstream value of '-u<val>' as clustered force flags,
        # so 'git branch -ufoo topic' / '-uMain topic' / '-uCandidate topic' wrongly ASKED. The option parser
        # now stops the cluster scan at '-u' and treats the remainder as the upstream, so these ALLOW; a real
        # force delete/move/copy/reset still ASKS, and a plain create/list still allows (unchanged above).
        expect("(r22-6) branch -ufoo sets upstream, allows (not read as -f)", "git branch -ufoo topic",
               "allow", cwd=rp)
        expect("(r22-7) branch -uMain sets upstream, allows (not read as -M)", "git branch -uMain topic",
               "allow", cwd=rp)
        expect("(r22-8) branch -uCandidate sets upstream, allows (not read as -C/-d)",
               "git branch -uCandidate topic", "allow", cwd=rp)
        expect("(r22-9) branch -u <upstream> separated form allows", "git branch -u origin/main topic",
               "allow", cwd=rp)
        expect("(r22-10) branch -f a other force reset still asks (unchanged)", "git branch -f a other",
               "ask", cwd=rp)

        # === EN-6 round-25 Fix F-94: a branch delete combined with -r/--remotes ASKS ================
        # Deleting remote-tracking refs (-d/-D/--delete with -r/--remotes) is force-removed by git past the
        # merged-branch safeguard that protects a plain local -d, so every delete+remotes spelling ASKS; a
        # local non-force -d keeps its allow, and -D still ASKS.
        expect("(f94-1) branch -d -r origin/topic asks", "git branch -d -r origin/topic", "ask", cwd=rp)
        expect("(f94-2) branch -dr origin/topic (clustered) asks", "git branch -dr origin/topic", "ask",
               cwd=rp)
        expect("(f94-3) branch --delete --remotes asks", "git branch --delete --remotes origin/topic", "ask",
               cwd=rp)
        expect("(f94-4) branch -d local (local safe delete) still allows", "git branch -d other", "allow",
               cwd=rp)

        # === EN-6 round-25 Fix F-95: git stash export ASKS for every spelling =======================
        # 'stash export' writes stash state to a ref, and --to-ref overwrites an arbitrary ref
        # unconditionally, so every export form ASKS (drop/clear unchanged; push/list unaffected).
        expect("(f95-1) stash export --to-ref asks", "git stash export --to-ref refs/heads/topic", "ask",
               cwd=rp)
        expect("(f95-2) stash export --print asks", "git stash export --print", "ask", cwd=rp)
        expect("(f95-3) stash export (bare) asks", "git stash export", "ask", cwd=rp)

        # === EN-6 round-25 Fix F-97: a raw-lossy-flagged command with an UNRECOGNIZED sub ASKS =======
        # 'git checkout-index -a -f' and 'git read-tree -u --reset HEAD' are flagged in-scope by the raw scan
        # (a 'checkout'/'reset' substring) but resolve to a subcommand the form-classifier does not recognize,
        # so they used to win the catch-all allow and discard tracked worktree content with no snapshot. They
        # now ASK. A genuine safe FORM of a RECOGNIZED verb still ALLOWs (its sub IS recognized), and a verb
        # the raw scan does NOT flag at all (git worktree) stays allowed at the true boundary.
        expect("(f97-1) checkout-index -a -f on dirty tree asks", "git checkout-index -a -f", "ask", cwd=rp)
        expect("(f97-2) read-tree -u --reset HEAD on dirty tree asks", "git read-tree -u --reset HEAD", "ask",
               cwd=rp)
        expect("(f97-3) checkout -b new (recognized safe form) still allows", "git checkout -b new", "allow",
               cwd=rp)
        expect("(f97-4) reset --soft (recognized safe form) still allows", "git reset --soft", "allow",
               cwd=rp)
        expect("(f97-5) clean -n (recognized safe form) still allows", "git clean -n", "allow", cwd=rp)
        expect("(f97-6) worktree remove -f unflagged, still allows at the true boundary",
               "git worktree remove -f", "allow", cwd=rp)

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

        # === EN-6 round-13: opt-out is case-sensitive and last-wins; redirects ASK all forms ==
        # Fix 1: bash env-var names are case-sensitive, so a LOWERCASE guardrail_allow_discard=1 is NOT the
        # opt-out. On an unparseable heredoc discard the raw fallback must NOT honour it -> ASK. Round-15
        # STRUCTURAL fix: the opt-out is no longer consulted on the unparseable path at all, so even the
        # UPPERCASE form on the same unparseable command now ASKS too (see the r15-raw-* battery below).
        expect("(r13-1) lowercase optout on unparseable heredoc discard does not opt out -> ASK",
               "guardrail_allow_discard=1 git reset --hard <<'EOF'\n'\nEOF", "ask", cwd=rp)
        # Fix 2: bash last-wins on a duplicate leading assignment - =1 then =0 evaluates to 0 (NOT truthy),
        # so it does NOT opt out (the buggy first-wins saw =1 and ALLOWed). With no resolvable cwd the
        # un-opted-out reset --hard ASKS ("cannot resolve to the session directory"); had it opted out it
        # would have short-circuited to ALLOW before the resolvability check.
        expect("(r13-2) =1 =0 last-wins evaluates 0, does not opt out -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 GUARDRAIL_ALLOW_DISCARD=0 git reset --hard", "ask")
        # Fix 3: a command-local redirect (-C/--git-dir/--work-tree/inline GIT_DIR=) means the repository
        # view cannot be proven to be the session cwd, so the early view-uncertainty gate ASKS for ALL forms
        # BEFORE the role logic - including genuinely non-destructive ALLOW forms (reset --soft, plain switch)
        # that previously slipped through to a silent ALLOW.
        expect("(r13-3a) inline GIT_DIR= on reset --soft (allow form) now asks",
               "GIT_DIR=/tmp git reset --soft", "ask", cwd=rp)
        expect("(r13-3b) -C on a plain switch (allow form) now asks",
               "git -C /tmp switch other", "ask", cwd=rp)
        expect("(r13-3c) --git-dir= on reset --hard asks",
               "git --git-dir=/x reset --hard", "ask", cwd=rp)
        # No regression: a plain non-destructive form with NO redirect and NO opt-out still ALLOWs on a dirty
        # tree (recovery-snapshot-backed), exactly as before Fix 3.
        expect("(r13-4a) plain reset --soft with no redirect still allows", "git reset --soft", "allow",
               cwd=rp)
        expect("(r13-4b) plain switch with no redirect still allows", "git switch other", "allow", cwd=rp)

        # === EN-6 round-15: end the opt-out silent-allow class (Fix 1 structural + Fix 2 empty value) =====
        # Fix 2 (parseable path): the opt-out value capture now matches an EMPTY value, so an empty FINAL
        # leading assignment is evaluated by bash last-wins as falsy and does NOT opt out. With no resolvable
        # cwd the un-opted-out reset --hard ASKS ("cannot resolve to the session directory"); had the empty
        # value been ignored (the old (.+) capture) the earlier =1 would have wrongly opted out to ALLOW.
        expect("(r15-empty) empty final opt-out assignment is falsy (last-wins), does not opt out -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 GUARDRAIL_ALLOW_DISCARD= git reset --hard", "ask")
        # Fix 1 (STRUCTURAL): the opt-out is no longer consulted on the UNPARSEABLE (raw-fallback) path at
        # all - a regex cannot soundly parse an opt-out out of a command the shell lexer could not parse - so
        # every opt-out-looking prefix on an unparseable in-scope discard now ASKS. The five raw variants that
        # previously wrung a silent ALLOW out of the raw scan (quoted-falsy that reads truthy raw, an
        # interspersed other assignment, an opt-out leading a DIFFERENT command, a `0;` captured truthy, and a
        # quoted "false"), each on an unparseable heredoc discard (the lone quote makes shlex raise), must ASK.
        _hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc whose lone ' makes shlex raise -> raw fallback
        expect("(r15-raw-quotedfalsy) quoted-falsy opt-out on unparseable discard -> ASK",
               'GUARDRAIL_ALLOW_DISCARD="0" git reset --hard' + _hd, "ask", cwd=rp)
        expect("(r15-raw-interspersed) interspersed other assignment on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 OTHER=x git reset --hard" + _hd, "ask", cwd=rp)
        expect("(r15-raw-othercmd) opt-out leading a DIFFERENT command on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 true; git reset --hard" + _hd, "ask", cwd=rp)
        expect("(r15-raw-semicolon) `0;` captured-truthy opt-out on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=0; git reset --hard" + _hd, "ask", cwd=rp)
        expect("(r15-raw-quotedfalse) quoted \"false\" opt-out on unparseable discard -> ASK",
               'GUARDRAIL_ALLOW_DISCARD="false" git reset --hard' + _hd, "ask", cwd=rp)
        # Fix 3 (documented override semantics): a leading PARSEABLE opt-out on a pristine bare command is an
        # explicit operator override, evaluated FIRST, so it short-circuits the command-local-redirect (-C)
        # view-uncertainty gate too -> ALLOW (the manifest now qualifies that gate "unless the leading opt-out
        # is set"). Contrast (clean-d): the same -C form WITHOUT the opt-out ASKS.
        expect("(r15-optout-redirect) leading opt-out short-circuits the -C redirect gate -> ALLOW",
               "GUARDRAIL_ALLOW_DISCARD=1 git -C /tmp reset --hard", "allow", cwd=rp)
        # No regression (opt-b, co-a, rs-a above): a plain parseable opt-out still ALLOWs; a plain lossy form
        # with no opt-out still ASKS (co-a) and a dirty whole-tree clobber still DENIES (rs-a).

        # === EN-6 round-19 Fix B: the redirect/ambient ASK emits the _OPTOUT_PRISTINE guidance ======
        # A pristine repository-view-redirected form (a 'git -C <dir> <verb>' whose opt-out short-circuits
        # BEFORE the redirect gate) is opted out by prefixing the command AS ISSUED, keeping its -C. So its
        # ASK must carry the _OPTOUT_PRISTINE ("prefix this command") guidance, NOT the _OPTOUT_REISSUE
        # ("re-issue it as a parseable pristine bare 'git <verb>'") text, which drops the -C and is false here.
        data_bopt = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "tool_input": {"command": "git -C /tmp reset --hard"}, "cwd": rp}
        code_bopt, obj_bopt, _ = handler(data_bopt)
        dec_bopt = obj_bopt.get("hookSpecificOutput", {}).get("permissionDecision") \
            if isinstance(obj_bopt, dict) else None
        reason_bopt = obj_bopt.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") \
            if isinstance(obj_bopt, dict) else ""
        banner_bopt = obj_bopt.get("systemMessage", "") if isinstance(obj_bopt, dict) else ""
        if not (code_bopt == 0 and dec_bopt == "ask"):
            failures.append("(r19b-1) redirect/ambient view-uncertainty must ASK, got code={!r} dec={!r}"
                            .format(code_bopt, dec_bopt))
        if aiqt_hooks._OPTOUT_PRISTINE.strip() not in reason_bopt:
            failures.append("(r19b-2) redirect ASK reason must carry the _OPTOUT_PRISTINE guidance")
        if aiqt_hooks._OPTOUT_REISSUE.strip() in reason_bopt:
            failures.append("(r19b-3) redirect ASK reason must NOT carry the _OPTOUT_REISSUE guidance")
        if "prefix GUARDRAIL_ALLOW_DISCARD=1 to skip" not in banner_bopt:
            failures.append("(r19b-4) redirect ASK banner must be the pristine 'prefix ...' form")

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
        # A malformed tool_input (a string, not a dict) -> boundary ALLOW (fail open), not a crash. This is
        # the DELIBERATE true-boundary fail-open the module and git_discard docstrings state (F-86): a
        # malformed/missing command is input git_discard cannot read as a discard, so it fails OPEN here
        # rather than fail-CLOSED like the other PreToolUse controls; it never silently allows a RECOGNIZED
        # discard, only input it cannot recognize as one.
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
            (("checkout", ["-bfoo"]), "allow"),             # F-85: attached name, the 'f' is not force
            (("checkout", ["-bBranch"]), "allow"),          # F-85: attached name, the 'B' is not -B
            (("checkout", ["-Bfoo"]), "ask"),               # F-85: attached name on the force-create -B
            (("checkout", ["-B", "foo"]), "ask"),           # F-85: separated name on the force-create -B
            (("checkout", ["-B", "new", "start"]), "ask"),  # F-81: -B force-creates/RESETS a branch ref
            (("checkout", ["-f", "-B", "new"]), "ask"),     # F-81: -B ASKS even combined with -f
            (("checkout", ["-f", "-b", "new"]), "scoped"),  # blocker 7: forced branch-create no early-allow
            (("checkout", ["-f", "other"]), "scoped"),   # bare operand: cannot tell ref from path -> ask
            (("checkout", ["-f"]), "clobber"),            # force, no operand -> whole-tree
            (("checkout", ["--for"]), "clobber"),         # blocker 5: abbreviated --force, no operand
            (("checkout", ["--patc"]), "scoped"),         # blocker 5: abbreviated --patch
            (("checkout", ["-m", "-b", "new"]), "scoped"),  # F-88: merge-switch before the create allow
            (("checkout", ["--merge", "-b", "new"]), "scoped"),  # F-88: long spelling, with a -b create
            (("checkout", ["--conflict=diff3", "-b", "new"]), "scoped"),  # F-88: conflict-style merge + create
            (("checkout", ["--mer", "-b", "new"]), "scoped"),  # F-88: abbreviated --merge by prefix + create
            (("checkout", ["-m", "other"]), "scoped"),      # F-88: '-m' is --merge, no create
            (("checkout", ["-mb", "new"]), "scoped"),       # F-88: '-mb new' == '-m -b new' (m is a flag)
            (("checkout", ["-f", "-m"]), "clobber"),        # F-88: force keeps its outcome (not downgraded)
            (("checkout", ["-f", "-m", "-b", "new"]), "scoped"),  # F-88: forced merge+create stays scoped
            (("switch", ["--force", "other"]), "clobber"),
            (("switch", ["--dis", "other"]), "clobber"),  # blocker 5: abbreviated --discard-changes
            (("switch", ["--merge", "other"]), "scoped"),  # fix 3: a three-way merge can overwrite worktree
            (("switch", ["--conflict=diff3", "other"]), "scoped"),  # fix 3: conflict-style merge
            (("switch", ["-m", "other"]), "scoped"),      # fix 3: '-m' is --merge
            (("switch", ["-C", "new", "start"]), "ask"),  # F-81: -C force-creates/RESETS a branch ref
            (("switch", ["--force-create", "new", "start"]), "ask"),  # F-81: long spelling
            (("switch", ["--force-c", "new"]), "ask"),    # F-81: abbreviated --force-create by prefix
            (("switch", ["-c", "new"]), "allow"),         # F-81: plain -c create unchanged
            (("switch", ["-cfeature"]), "allow"),         # F-85: attached name, the 'f' is not force
            (("switch", ["-Cfoo"]), "ask"),               # F-85: attached name on the force-create -C
            (("switch", ["-C", "foo"]), "ask"),           # F-85: separated name on the force-create -C
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
            (("stash", ["export", "--to-ref", "refs/heads/topic"]), "ask"),  # F-95: export overwrites a ref
            (("stash", ["export", "--print"]), "ask"),       # F-95: every export spelling ASKS
            (("stash", ["export"]), "ask"),                  # F-95: bare export ASKS
            (("stash", ["push"]), "allow"),                  # F-95: push unaffected
            (("branch", ["-D", "x"]), "ask"),
            (("branch", ["--del", "--force", "x"]), "ask"),  # blocker 5: abbreviated --delete/--force
            (("branch", ["-d", "x"]), "allow"),
            (("branch", ["-d", "-r", "origin/topic"]), "ask"),  # F-94: delete + remotes force-removes refs
            (("branch", ["-dr", "origin/topic"]), "ask"),    # F-94: clustered -dr
            (("branch", ["--delete", "--remotes", "x"]), "ask"),  # F-94: long spellings
            (("branch", ["-D", "-r", "x"]), "ask"),          # F-94: -D + remotes still asks
            (("branch", ["-r"]), "allow"),                   # F-94: list remotes only (no delete) allows
            (("branch", ["-ufoo", "topic"]), "allow"),       # F-82: '-ufoo' is -u<upstream>, not -f/-o/-o
            (("branch", ["-uMain", "topic"]), "allow"),      # F-82: 'M' is in the upstream VALUE, not -M
            (("branch", ["-uCandidate", "topic"]), "allow"),  # F-82: 'C'/'d' are in the VALUE, not -C/-d
            (("branch", ["-u", "foo", "topic"]), "allow"),   # F-82: separated '-u <upstream>' consumes value
            (("branch", ["-f", "a", "other"]), "ask"),       # F-82: a real force reset still ASKS
        ]
        for (sub, args), want_role in role_cases:
            got_role = aiqt_hooks._discard_role(sub, args)[0]
            if got_role != want_role:
                failures.append("(role) _discard_role({!r}, {!r}) role: expected {}, got {}"
                                .format(sub, args, want_role, got_role))

        # === EN-6 recovery/snapshot layer ============================================================
        # The layer takes an INERT snapshot (a private refs/aiqt-recovery/* ref over a temp-index tree)
        # before returning its decision for a snapshottable in-scope verb on a not-provably-clean, resolvable
        # tree, on the ALLOW and ASK paths alike, and NEVER touches the real index/worktree/HEAD. Each case
        # builds a FRESH repo so a ref count is unambiguous.

        # (rec-ask) a dirty-tree ASK (a scoped checkout revert) takes a snapshot.
        rec_ask = _init_repo(tmp / "rec-ask")
        (rec_ask / "file.txt").write_text("committed line\nuncommitted fix\n", encoding="utf-8")
        expect("(rec-ask) checkout -- on dirty tree asks", "git checkout -- file.txt", "ask",
               cwd=str(rec_ask))
        if not _recovery_refs(rec_ask):
            failures.append("(rec-ask-snap) expected a recovery ref after a dirty-tree ASK")

        # (rec-misparse) a dirty-tree ALLOW that is a simulated classifier MIS-PARSE still snapshots (the
        # snapshot is decision-independent), and the decision stays ALLOW when the snapshot succeeds.
        rec_mis = _init_repo(tmp / "rec-misparse")
        (rec_mis / "file.txt").write_text("committed line\nmisparse work\n", encoding="utf-8")
        _orig_role = aiqt_hooks._discard_role
        aiqt_hooks._discard_role = lambda sub, args: ("allow", None)
        try:
            got_mis = _decision(handler, "git reset --hard", cwd=str(rec_mis))
        finally:
            aiqt_hooks._discard_role = _orig_role
        if got_mis != "allow":
            failures.append("(rec-misparse) simulated mis-parse: expected allow, got {}".format(got_mis))
        if not _recovery_refs(rec_mis):
            failures.append("(rec-misparse-snap) expected a recovery ref on a mis-parse ALLOW of a dirty tree")

        # (rec-clean) a provably-clean tree takes NO snapshot (nothing to lose).
        rec_clean = _init_repo(tmp / "rec-clean")
        expect("(rec-clean) reset --hard on clean tree allows", "git reset --hard", "allow",
               cwd=str(rec_clean))
        if _recovery_refs(rec_clean):
            failures.append("(rec-clean-snap) expected NO recovery ref on a provably-clean tree")

        # (rec-inv) the real git status, index tree, and HEAD are UNCHANGED after a snapshot (the snapshot
        # uses a temp index outside the repo and writes only objects + one ref).
        rec_inv = _init_repo(tmp / "rec-inv")
        (rec_inv / "file.txt").write_text("committed line\nworktree edit\n", encoding="utf-8")
        (rec_inv / "newstaged.txt").write_text("staged\n", encoding="utf-8")
        _git(rec_inv, "add", "newstaged.txt")
        (rec_inv / "untr.txt").write_text("junk\n", encoding="utf-8")

        def _snap(*a):
            # CHECK the return code: a git probe that errors must surface as a harness failure, not be
            # swallowed into an empty string that then matches "before" and falsely proves invariance.
            r = subprocess.run(["git", "-C", str(rec_inv), *a], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                failures.append("(rec-inv-probe) git {} failed in the invariant probe (rc={}): {}"
                                .format(" ".join(a), r.returncode, r.stderr.strip()))
            return r.stdout.strip()
        real_idx = rec_inv / ".git" / "index"
        # Compare the WRITE-TREE (index content), the raw index BYTES, git config, and the stash list, on top
        # of status/HEAD, before and after a snapshot: the snapshot must leave every one of them untouched.
        before = (_snap("status", "--porcelain"), _snap("rev-parse", "HEAD"), _snap("write-tree"),
                  _snap("config", "--list"), _snap("stash", "list"))
        before_idx = real_idx.read_bytes()
        # Also capture the WORKTREE bytes of the dirty file and the full branch-ref listing: the snapshot
        # must leave the actual working-tree content and every branch ref untouched (it writes only objects
        # and one refs/aiqt-recovery/* ref).
        before_wt = (rec_inv / "file.txt").read_bytes()
        before_heads = _snap("for-each-ref", "refs/heads")
        expect("(rec-inv) checkout -- on dirty invariant tree asks", "git checkout -- file.txt", "ask",
               cwd=str(rec_inv))
        after = (_snap("status", "--porcelain"), _snap("rev-parse", "HEAD"), _snap("write-tree"),
                 _snap("config", "--list"), _snap("stash", "list"))
        after_idx = real_idx.read_bytes()
        after_wt = (rec_inv / "file.txt").read_bytes()
        after_heads = _snap("for-each-ref", "refs/heads")
        if not _recovery_refs(rec_inv):
            failures.append("(rec-inv-snap) expected a recovery ref on the invariant repo")
        if before[0] != after[0]:
            failures.append("(rec-inv-status) the real git status changed after a snapshot")
        if before[1] != after[1]:
            failures.append("(rec-inv-head) HEAD changed after a snapshot")
        if before[2] != after[2]:
            failures.append("(rec-inv-index) the real index tree changed after a snapshot")
        if before_idx != after_idx:
            failures.append("(rec-inv-index-bytes) the real .git/index bytes changed after a snapshot")
        if before[3] != after[3]:
            failures.append("(rec-inv-config) the real git config changed after a snapshot")
        if before[4] != after[4]:
            failures.append("(rec-inv-stash) the real stash list changed after a snapshot")
        if before_wt != after_wt:
            failures.append("(rec-inv-worktree) the real worktree bytes changed after a snapshot")
        if before_heads != after_heads:
            failures.append("(rec-inv-branches) a branch ref changed after a snapshot")

        # (rec-restore) the snapshot ref actually RESTORES the work: dirty (tracked + untracked), snapshot
        # via a clean discard, wipe the tree, then `git checkout <ref> -- :/` recovers both.
        rec_res = _init_repo(tmp / "rec-restore")
        (rec_res / "file.txt").write_text("committed line\nrecovered fix\n", encoding="utf-8")
        (rec_res / "untr.txt").write_text("untracked work\n", encoding="utf-8")
        expect("(rec-restore-setup) clean -fd on dirty+untracked tree asks", "git clean -fd", "ask",
               cwd=str(rec_res))
        res_refs = _recovery_refs(rec_res)
        if not res_refs:
            failures.append("(rec-restore-ref) expected a recovery ref before a clean discard")
        else:
            _git(rec_res, "reset", "--hard")
            _git(rec_res, "clean", "-fd")
            _git(rec_res, "checkout", res_refs[0], "--", ":/")
            if "recovered fix" not in (rec_res / "file.txt").read_text(encoding="utf-8"):
                failures.append("(rec-restore-tracked) restore did not recover the tracked modification")
            if not (rec_res / "untr.txt").exists() or \
                    (rec_res / "untr.txt").read_text(encoding="utf-8") != "untracked work\n":
                failures.append("(rec-restore-untracked) restore did not recover the untracked file")

        # (rec-faildowngrade) a FORCED snapshot failure downgrades a would-be ALLOW to ASK (never a silent
        # allow of a not-provably-clean discard).
        rec_fail = _init_repo(tmp / "rec-fail")
        (rec_fail / "file.txt").write_text("committed line\nat risk\n", encoding="utf-8")
        _orig_role = aiqt_hooks._discard_role
        _orig_rec = aiqt_hooks._record_recovery
        aiqt_hooks._record_recovery = lambda repo, verb: ("fail", "forced failure (self-test)")
        try:
            aiqt_hooks._discard_role = lambda sub, args: ("allow", None)
            got_dg = _decision(handler, "git reset --hard", cwd=str(rec_fail))
            aiqt_hooks._discard_role = _orig_role  # real role: reset --hard is a clobber
            got_dn = _decision(handler, "git reset --hard", cwd=str(rec_fail))
        finally:
            aiqt_hooks._discard_role = _orig_role
            aiqt_hooks._record_recovery = _orig_rec
        if got_dg != "ask":
            failures.append("(rec-faildowngrade) a snapshot failure must downgrade a would-be ALLOW to ASK, "
                            "got {}".format(got_dg))
        if got_dn != "deny":
            failures.append("(rec-faildeny) a snapshot failure must leave a clobber DENY as DENY, got {}"
                            .format(got_dn))

        # (rec-ledger) the external ledger records the VERB, ref, sha, classes, and restore command, and
        # NEVER the raw command (privacy). The ledger lives outside every repo, under the redirected XDG dir.
        ledger = Path(os.environ["XDG_STATE_HOME"]) / "aiqt-guardrails" / "recovery.jsonl"
        if not ledger.exists():
            failures.append("(rec-ledger) expected the recovery ledger to exist after snapshots")
        else:
            lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
            try:
                rec = json.loads(lines[-1])
            except (ValueError, IndexError):
                rec = None
                failures.append("(rec-ledger-json) the ledger's last line is not a JSON record")
            if rec is not None:
                for key in ("ts", "repo", "verb", "ref", "sha", "classes", "restore"):
                    if key not in rec:
                        failures.append("(rec-ledger-field) ledger record missing key {!r}".format(key))
                if rec.get("verb", "x").startswith("git") or " " in rec.get("verb", " "):
                    failures.append("(rec-ledger-verb) ledger 'verb' should be a bare subcommand, got {!r}"
                                    .format(rec.get("verb")))
                if rec.get("ref", "") not in rec.get("restore", ""):
                    failures.append("(rec-ledger-restore) the restore command should reference the ref")

        # (rec-idxfile) A: an ambient GIT_INDEX_FILE is a TARGET-REDIRECT var. The probe would scrub it and
        # read the real cwd repo, but the guard cannot scrub it from the ACTUAL command, which would discard
        # the custom index the probe never saw. So the guard CANNOT prove the command's target IS the session
        # cwd: it ASKS (never a silent allow) and takes a BEST-EFFORT snapshot of the SESSION CWD (dirty here),
        # while the recovery git calls scrub the ambient GIT_INDEX_FILE so the real index and the bogus ambient
        # path stay untouched (the snapshot may not capture a redirected tree, but it recovers the common
        # non-redirecting case).
        rec_idx = _init_repo(tmp / "rec-idxfile")
        (rec_idx / "file.txt").write_text("committed line\nidx staged\n", encoding="utf-8")
        _git(rec_idx, "add", "file.txt")  # staged content in the REAL index
        (rec_idx / "untr.txt").write_text("junk\n", encoding="utf-8")
        real_index = rec_idx / ".git" / "index"
        idx_before = real_index.read_bytes()
        ambient = tmp / "ambient-index"  # a bogus preset index OUTSIDE the repo, must stay untouched
        os.environ["GIT_INDEX_FILE"] = str(ambient)
        try:
            got_idx = _decision(handler, "git checkout -- file.txt", cwd=str(rec_idx))
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
        if got_idx != "ask":
            failures.append("(rec-idxfile) an ambient GIT_INDEX_FILE target-redirect must ASK (unresolvable "
                            "target), got {}".format(got_idx))
        if real_index.read_bytes() != idx_before:
            failures.append("(rec-idxfile-index) the REAL .git/index changed under an ambient GIT_INDEX_FILE")
        if ambient.exists():
            failures.append("(rec-idxfile-ambient) the ambient GIT_INDEX_FILE path was written")
        if not _recovery_refs(rec_idx):
            failures.append("(rec-idxfile-snap) the ambient-override ASK on a dirty cwd must take a "
                            "best-effort recovery snapshot of the session cwd")

        # (rec-ambient) an assortment of NON-redirect ambient GIT_* env vars (identity, pager) does not break
        # the decision or the snapshot isolation: a dirty-tree ASK still ASKS, a snapshot ref is created, and
        # the real index/HEAD are unchanged. (Target-redirect vars like GIT_DIR/GIT_INDEX_FILE are a separate
        # case that forces ASK-with-no-snapshot; see rec-idxfile and rec-decoy.)
        rec_amb = _init_repo(tmp / "rec-ambient")
        (rec_amb / "file.txt").write_text("committed line\nambient env\n", encoding="utf-8")
        amb_index = rec_amb / ".git" / "index"
        amb_idx_before = amb_index.read_bytes()
        amb_head_before = subprocess.run(["git", "-C", str(rec_amb), "rev-parse", "HEAD"],
                                         capture_output=True, text=True, timeout=30).stdout.strip()
        amb_env = {"GIT_AUTHOR_NAME": "Ambient", "GIT_AUTHOR_EMAIL": "a@example.invalid",
                   "GIT_COMMITTER_NAME": "Ambient", "GIT_COMMITTER_EMAIL": "a@example.invalid",
                   "GIT_PAGER": "cat"}
        for _k, _v in amb_env.items():
            os.environ[_k] = _v
        try:
            got_amb = _decision(handler, "git checkout -- file.txt", cwd=str(rec_amb))
        finally:
            for _k in amb_env:
                os.environ.pop(_k, None)
        if got_amb != "ask":
            failures.append("(rec-ambient) dirty-tree ASK with ambient GIT_* env: expected ask, got {}"
                            .format(got_amb))
        if not _recovery_refs(rec_amb):
            failures.append("(rec-ambient-snap) expected a recovery ref with ambient GIT_* env present")
        if amb_index.read_bytes() != amb_idx_before:
            failures.append("(rec-ambient-index) the real index changed with ambient GIT_* env present")
        amb_head_after = subprocess.run(["git", "-C", str(rec_amb), "rev-parse", "HEAD"],
                                        capture_output=True, text=True, timeout=30).stdout.strip()
        if amb_head_after != amb_head_before:
            failures.append("(rec-ambient-head) HEAD changed with ambient GIT_* env present")

        # (rec-skip) stash and branch verbs are SKIPPED by the snapshot layer (a worktree snapshot cannot
        # capture stash entries or branch commits), so NO recovery ref is created even on a dirty tree.
        rec_skip = _init_repo(tmp / "rec-skip")
        (rec_skip / "file.txt").write_text("committed line\ndirty\n", encoding="utf-8")
        expect("(rec-skip-stash) stash drop on dirty tree asks", "git stash drop", "ask", cwd=str(rec_skip))
        expect("(rec-skip-branch) branch -D on dirty tree asks", "git branch -D other", "ask",
               cwd=str(rec_skip))
        if _recovery_refs(rec_skip):
            failures.append("(rec-skip-snap) expected NO recovery ref for stash/branch (not snapshottable)")

        # (rec-sizecap) a snapshot that exceeds the size cap FAILS, so a would-be ALLOW (reset --soft) on a
        # dirty tree is downgraded to ASK (never a silent allow with no recovery point), and no ref is made.
        rec_cap = _init_repo(tmp / "rec-cap")
        (rec_cap / "file.txt").write_text("committed line\nbig change\n", encoding="utf-8")
        _orig_cap = aiqt_hooks._RECOVERY_SIZE_CAP
        aiqt_hooks._RECOVERY_SIZE_CAP = 1  # any changed content now exceeds the cap -> snapshot fail
        try:
            got_cap = _decision(handler, "git reset --soft", cwd=str(rec_cap))
        finally:
            aiqt_hooks._RECOVERY_SIZE_CAP = _orig_cap
        if got_cap != "ask":
            failures.append("(rec-sizecap) an over-cap snapshot must downgrade a would-be ALLOW to ASK, got "
                            "{}".format(got_cap))
        if _recovery_refs(rec_cap):
            failures.append("(rec-sizecap-snap) expected NO recovery ref when the size cap fails the snapshot")

        # (rec-probeuncertain) when the clean probe is UNCERTAIN (returns None) a scoped discard ASKS, and
        # when the snapshot also fails the ASK reason SURFACES the snapshot failure (never a silent allow).
        rec_unc = _init_repo(tmp / "rec-unc")
        (rec_unc / "file.txt").write_text("committed line\nuncertain\n", encoding="utf-8")
        _orig_clean = aiqt_hooks._tree_is_clean
        _orig_rec = aiqt_hooks._record_recovery
        aiqt_hooks._tree_is_clean = lambda repo: None
        aiqt_hooks._record_recovery = lambda repo, verb: ("fail", "forced probe-uncertain failure (self-test)")
        try:
            data_unc = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                        "tool_input": {"command": "git restore file.txt"}, "cwd": str(rec_unc)}
            code_unc, obj_unc, _ = handler(data_unc)
        finally:
            aiqt_hooks._tree_is_clean = _orig_clean
            aiqt_hooks._record_recovery = _orig_rec
        dec_unc = obj_unc.get("hookSpecificOutput", {}).get("permissionDecision") \
            if isinstance(obj_unc, dict) else None
        reason_unc = obj_unc.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") \
            if isinstance(obj_unc, dict) else ""
        if not (code_unc == 0 and dec_unc == "ask"):
            failures.append("(rec-probeuncertain) probe-uncertain scoped discard: expected ask, got code={!r}"
                            " dec={!r}".format(code_unc, dec_unc))
        if "no pre-command recovery snapshot could be created" not in reason_unc:
            failures.append("(rec-probeuncertain-reason) the ASK reason must surface the snapshot failure")

        # (rec-b2-tmp) B2 guard: a temp snapshot dir resolving INSIDE the repo makes the snapshot FAIL
        # (refusing to write recovery data inside the tree it protects), so a would-be ALLOW (reset --soft)
        # on a dirty tree downgrades to ASK and no ref is created.
        rec_b2t = _init_repo(tmp / "rec-b2-tmp")
        (rec_b2t / "file.txt").write_text("committed line\ninside tmp\n", encoding="utf-8")
        inside_tmp = rec_b2t / "insidetmp"
        inside_tmp.mkdir()
        _orig_tempdir = tempfile.tempdir
        tempfile.tempdir = str(inside_tmp)  # force mkdtemp to create the temp dir inside the repo
        try:
            got_b2t = _decision(handler, "git reset --soft", cwd=str(rec_b2t))
        finally:
            tempfile.tempdir = _orig_tempdir
        if got_b2t != "ask":
            failures.append("(rec-b2-tmp) a temp dir inside the repo must fail-to-ASK a would-be ALLOW, got "
                            "{}".format(got_b2t))
        if _recovery_refs(rec_b2t):
            failures.append("(rec-b2-tmp-snap) expected NO recovery ref when the temp dir resolves inside the "
                            "repo")

        # (rec-b2-ledger) B2 guard: a ledger path resolving INSIDE the repo is SKIPPED (best-effort), but the
        # snapshot ref is still taken and the decision is unaffected.
        rec_b2l = _init_repo(tmp / "rec-b2-ledger")
        (rec_b2l / "file.txt").write_text("committed line\ninside ledger\n", encoding="utf-8")
        inside_ledger = rec_b2l / "insidexdg"
        _orig_xdg = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(inside_ledger)
        try:
            got_b2l = _decision(handler, "git checkout -- file.txt", cwd=str(rec_b2l))
        finally:
            if _orig_xdg is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = _orig_xdg
        if got_b2l != "ask":
            failures.append("(rec-b2-ledger) ledger-inside-repo: the decision must be unaffected (ask), got "
                            "{}".format(got_b2l))
        if not _recovery_refs(rec_b2l):
            failures.append("(rec-b2-ledger-snap) expected a recovery ref even when the ledger write is skipped")
        if (inside_ledger / "aiqt-guardrails" / "recovery.jsonl").exists():
            failures.append("(rec-b2-ledger-skip) the ledger must NOT be written inside the repo")

        # (rec-decoy) A: ambient GIT_DIR + GIT_WORK_TREE are TARGET-REDIRECT vars pointing at a CLEAN decoy.
        # The PROBE still scrubs them and reads the REAL dirty repo (so a false clean-decoy ALLOW is off the
        # table), but the guard cannot scrub them from the ACTUAL command, which would act on the redirected
        # decoy, not the probed cwd. So the guard cannot prove the command's target IS the session cwd: it
        # ASKS (never a DENY it cannot justify about the wrong target, never a false ALLOW), takes a
        # BEST-EFFORT snapshot of the real session cwd (dirty here; it may not capture the redirected target),
        # writes nothing to the decoy, and leaves the real repo untouched. (Old wrong
        # premise: that neutralizing the probe let the guard confidently DENY on the real repo, ignoring that
        # the real command still carries the redirect and would not even touch the probed repo.)
        rec_decoy = _init_repo(tmp / "rec-decoy")
        (rec_decoy / "file.txt").write_text("committed line\nreal dirty work\n", encoding="utf-8")
        decoy = _init_repo(tmp / "rec-decoy-clean")  # a CLEAN decoy the ambient env points at
        real_head = subprocess.run(["git", "-C", str(rec_decoy), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, timeout=30).stdout.strip()
        real_idx_bytes = (rec_decoy / ".git" / "index").read_bytes()
        decoy_env = {"GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy)}
        for _k, _v in decoy_env.items():
            os.environ[_k] = _v
        try:
            probe_decoy = aiqt_hooks._tree_is_clean(str(rec_decoy))
            got_decoy = _decision(handler, "git reset --hard", cwd=str(rec_decoy))
        finally:
            for _k in decoy_env:
                os.environ.pop(_k, None)
        if probe_decoy is not False:
            failures.append("(rec-decoy-probe) with ambient GIT_DIR/GIT_WORK_TREE at a clean decoy, the probe "
                            "must still scrub them and read the REAL dirty repo (False), got {}"
                            .format(probe_decoy))
        if got_decoy != "ask":
            failures.append("(rec-decoy) an ambient GIT_DIR/GIT_WORK_TREE target-redirect must ASK (the guard "
                            "cannot prove the command targets the session cwd), got {}".format(got_decoy))
        if not _recovery_refs(rec_decoy):
            failures.append("(rec-decoy-snap) the ambient-override ASK on a dirty cwd must take a best-effort "
                            "recovery snapshot on the real repo")
        if _recovery_refs(decoy):
            failures.append("(rec-decoy-wrongwrite) a recovery ref was written to the DECOY repo")
        if (rec_decoy / ".git" / "index").read_bytes() != real_idx_bytes:
            failures.append("(rec-decoy-index) the real index changed under an ambient GIT_DIR/GIT_WORK_TREE")
        real_head_after = subprocess.run(["git", "-C", str(rec_decoy), "rev-parse", "HEAD"],
                                         capture_output=True, text=True, timeout=30).stdout.strip()
        if real_head_after != real_head:
            failures.append("(rec-decoy-head) the real HEAD changed under an ambient GIT_DIR/GIT_WORK_TREE")

        # (rec-viewoverride) FAIL-SAFE repository-view check (round-9): the guard no longer enumerates a
        # FIXED list of redirecting GIT_* vars (a whack-a-mole - Codex round-8 found GIT_NO_REPLACE_OBJECTS,
        # GIT_REPLACE_REF_BASE, and GIT_REFERENCE_BACKEND all missed by it). It now ASKS whenever ANY ambient
        # GIT_*-prefixed var is set EXCEPT a small cosmetic allowlist, so an unknown or new var fails safe to
        # ASK. Each case runs a clean-tree PRISTINE discard that would otherwise ALLOW, so a flip to ASK is
        # attributable to the ambient var alone.
        rec_view = _init_repo(tmp / "rec-viewoverride")
        # control: with no ambient GIT_* override the clean-tree pristine discard ALLOWs.
        expect("(rec-viewoverride-base) clean pristine discard allows with no ambient GIT_* override",
               "git checkout -- file.txt", "allow", cwd=str(rec_view))
        # the three vars the old fixed list missed, plus an arbitrary UNKNOWN var: each MUST now force ASK
        # (fail-safe), even though the tree is clean and the form is a pristine discard.
        for _newvar in ("GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_REFERENCE_BACKEND",
                        "GIT_FUTURE_THING"):
            os.environ[_newvar] = "1"
            try:
                got_view = _decision(handler, "git checkout -- file.txt", cwd=str(rec_view))
            finally:
                os.environ.pop(_newvar, None)
            if got_view != "ask":
                failures.append("(rec-viewoverride-{}) an ambient non-cosmetic {} must fail-safe to ASK, got "
                                "{}".format(_newvar, _newvar, got_view))
        # the ORIGINAL six target-redirect vars still ASK under the fail-safe check.
        for _redir in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                       "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE"):
            os.environ[_redir] = "1"
            try:
                got_redir = _decision(handler, "git checkout -- file.txt", cwd=str(rec_view))
            finally:
                os.environ.pop(_redir, None)
            if got_redir != "ask":
                failures.append("(rec-viewoverride-{}) the redirect var {} must still ASK, got {}"
                                .format(_redir, _redir, got_redir))
        # a COSMETIC ambient var (GIT_PAGER, GIT_EDITOR) does NOT force ASK: the clean-tree pristine discard
        # still ALLOWs, so the allowlist genuinely lets the harmless UI/identity vars through.
        for _cos, _cosval in (("GIT_PAGER", "cat"), ("GIT_EDITOR", "true")):
            os.environ[_cos] = _cosval
            try:
                got_cos = _decision(handler, "git checkout -- file.txt", cwd=str(rec_view))
            finally:
                os.environ.pop(_cos, None)
            if got_cos != "allow":
                failures.append("(rec-viewoverride-cosmetic-{}) a cosmetic ambient {} must NOT force ASK on a "
                                "clean pristine discard, got {}".format(_cos, _cos, got_cos))
        # (rec-viewoverride-allowform) Fix 2 (structural completion): the fail-safe now covers the
        # NON-DESTRUCTIVE allow forms too. A pristine allow form (reset --soft, plain switch, clean -n,
        # checkout -b) ALLOWs on a clean tree with NO ambient override, but under a NON-COSMETIC ambient GIT_*
        # var it ASKS - the redirected repository view invalidates the form's safety premise, so an allow form
        # can no longer bypass the ambient-override ASK (before Fix 2 these ALLOWed, silently skipping the
        # fail-safe). The clean tree means no snapshot is warranted; the ASK is attributable to the ambient
        # var alone.
        allow_forms = ("git reset --soft", "git switch other", "git clean -n", "git checkout -b newbr")
        for _cmd in allow_forms:
            expect("(rec-viewoverride-allow-base) {} allows with no ambient override".format(_cmd),
                   _cmd, "allow", cwd=str(rec_view))
        for _amb2 in ("GIT_FUTURE_THING", "GIT_CONFIG_COUNT"):
            os.environ[_amb2] = "1"
            try:
                for _cmd in allow_forms:
                    got_af = _decision(handler, _cmd, cwd=str(rec_view))
                    if got_af != "ask":
                        failures.append("(rec-viewoverride-allow-{}-{}) an ambient non-cosmetic {} must force "
                                        "ASK on the allow form '{}' (was ALLOW), got {}"
                                        .format(_amb2, _cmd.replace(" ", "_"), _amb2, _cmd, got_af))
            finally:
                os.environ.pop(_amb2, None)
        # (rec-viewoverride-trace) Fix 1: GIT_TRACE is NO LONGER cosmetic - an absolute GIT_TRACE value makes
        # the ACTUAL command append trace output to that path (which could be a repo file), so an ambient
        # GIT_TRACE now forces ASK even on a clean pristine discard that used to ALLOW. The recovery/probe git
        # calls still scrub the GIT_TRACE family, so the guard itself writes no trace file.
        trace_view = tmp / "vo-trace.log"
        os.environ["GIT_TRACE"] = str(trace_view)
        try:
            got_trv = _decision(handler, "git checkout -- file.txt", cwd=str(rec_view))
        finally:
            os.environ.pop("GIT_TRACE", None)
        if got_trv != "ask":
            failures.append("(rec-viewoverride-trace) an ambient GIT_TRACE must force ASK on a clean pristine "
                            "discard (no longer cosmetic), got {}".format(got_trv))
        if trace_view.exists():
            failures.append("(rec-viewoverride-trace-file) the guard's git calls must scrub GIT_TRACE; no "
                            "trace file may be written")

        # (rec-heredoc) C: a Bash-valid but shlex-UNPARSEABLE discard (an unbalanced quote inside a quoted
        # heredoc) reaches the raw-lossy fallback. It must ASK and, on a dirty tree, take a best-effort
        # recovery snapshot FIRST - before the fix the ValueError path returned ASK with NO recovery ref.
        rec_hd = _init_repo(tmp / "rec-heredoc")
        (rec_hd / "file.txt").write_text("committed line\nheredoc dirty\n", encoding="utf-8")
        hd_cmd = "git reset --hard <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes shlex raise
        got_hd = _decision(handler, hd_cmd, cwd=str(rec_hd))
        if got_hd != "ask":
            failures.append("(rec-heredoc) an unparseable in-scope discard must ASK, got {}".format(got_hd))
        if not _recovery_refs(rec_hd):
            failures.append("(rec-heredoc-snap) expected a best-effort recovery ref for an unparseable "
                            "dirty-tree discard (Class C)")

        # (rec-subdir-tmp) C2: cwd is a SUBDIR of the repo and TMPDIR points at the worktree ROOT (above cwd).
        # The temp-dir containment check anchors on the resolved TOPLEVEL, not the cwd, so the temp dir is
        # judged INSIDE the tree -> snapshot FAIL -> a would-be ALLOW (reset --soft) downgrades to ASK and no
        # ref is written. (If it anchored on the deeper cwd, the root temp dir would read as outside and the
        # snapshot would wrongly succeed inside the tree.)
        rec_sub = _init_repo(tmp / "rec-subdir")
        (rec_sub / "file.txt").write_text("committed line\nsubdir dirty\n", encoding="utf-8")
        subdir = rec_sub / "sub" / "deep"
        subdir.mkdir(parents=True, exist_ok=True)
        _orig_tempdir = tempfile.tempdir
        tempfile.tempdir = str(rec_sub)  # TMPDIR at the worktree ROOT, ABOVE the cwd
        try:
            got_sub = _decision(handler, "git reset --soft", cwd=str(subdir))
        finally:
            tempfile.tempdir = _orig_tempdir
        if got_sub != "ask":
            failures.append("(rec-subdir-tmp) a temp dir at the toplevel (above a subdir cwd) must fail-to-ASK "
                            "a would-be ALLOW, got {}".format(got_sub))
        if _recovery_refs(rec_sub):
            failures.append("(rec-subdir-tmp-snap) expected NO ref when the temp dir resolves inside the "
                            "toplevel from a subdir cwd")

        # (rec-subdir-ledger) C2: cwd is a SUBDIR and XDG_STATE_HOME points at the worktree ROOT. The ledger
        # containment anchors on the TOPLEVEL, so the ledger write is SKIPPED (never written inside the tree),
        # but the snapshot ref is still taken and the decision is unaffected (ASK).
        rec_subl = _init_repo(tmp / "rec-subdir-ledger")
        (rec_subl / "file.txt").write_text("committed line\nsubdir ledger\n", encoding="utf-8")
        subdir2 = rec_subl / "nested"
        subdir2.mkdir(parents=True, exist_ok=True)
        _orig_xdg2 = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(rec_subl)  # ledger base at the worktree ROOT, ABOVE the cwd
        try:
            got_subl = _decision(handler, "git checkout -- file.txt", cwd=str(subdir2))
        finally:
            if _orig_xdg2 is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = _orig_xdg2
        if got_subl != "ask":
            failures.append("(rec-subdir-ledger) a ledger base at the toplevel above a subdir cwd must leave "
                            "the decision unaffected (ask), got {}".format(got_subl))
        if not _recovery_refs(rec_subl):
            failures.append("(rec-subdir-ledger-snap) expected a recovery ref even when the ledger is skipped")
        if (rec_subl / "aiqt-guardrails" / "recovery.jsonl").exists():
            failures.append("(rec-subdir-ledger-skip) the ledger must NOT be written inside the toplevel from "
                            "a subdir cwd")

        # (rec-badname) C3: a non-UTF-8 filename makes `status -z` (text=True) raise UnicodeDecodeError; it
        # must be caught and turned into a snapshot FAIL -> graceful ASK, never an uncaught crash (which the
        # dispatcher would turn into an exit-2 HARD BLOCK of a would-ALLOW command).
        rec_bad = _init_repo(tmp / "rec-badname")
        (rec_bad / "file.txt").write_text("committed line\ndirty\n", encoding="utf-8")  # tracked-dirty
        badname = os.fsencode(str(rec_bad)) + b"/\xff\xfe-bad.txt"  # invalid UTF-8 bytes in the name
        try:
            with open(badname, "wb") as fh:
                fh.write(b"junk\n")
            made_bad = True
        except OSError:
            made_bad = False  # a filesystem that refuses the byte name: skip this case gracefully
        if made_bad:
            try:
                got_bad = _decision(handler, "git checkout -- file.txt", cwd=str(rec_bad))
            except UnicodeDecodeError:
                got_bad = "CRASH (UnicodeDecodeError propagated to the dispatcher)"
            if got_bad != "ask":
                failures.append("(rec-badname) a non-UTF-8 path must fail-to-ASK gracefully, got {}"
                                .format(got_bad))

        # (rec-refcollision) C4: the recovery ref is CREATE-ONLY (empty expected-old value). Freezing the
        # timestamp forces two snapshots onto the SAME ref name; the second update-ref then FAILS rather than
        # clobbering the first, so the decision stays ASK, the prior ref stays intact, and no second ref is
        # created.
        rec_col = _init_repo(tmp / "rec-collision")
        (rec_col / "file.txt").write_text("committed line\ncollide\n", encoding="utf-8")

        _utc = datetime.timezone.utc

        class _FixedNow:
            @staticmethod
            def now(tz=None):
                return datetime.datetime(2020, 1, 1, 0, 0, 0, 123456, tzinfo=_utc)

        class _FakeDatetime:
            pass

        _FakeDatetime.datetime = _FixedNow  # set after the class body to avoid name shadowing
        _FakeDatetime.timezone = datetime.timezone

        _orig_dt = aiqt_hooks.datetime
        aiqt_hooks.datetime = _FakeDatetime
        try:
            got_col1 = _decision(handler, "git checkout -- file.txt", cwd=str(rec_col))
            refs_after1 = _recovery_refs(rec_col)
            first_sha = subprocess.run(["git", "-C", str(rec_col), "rev-parse", refs_after1[0]],
                                       capture_output=True, text=True, timeout=30).stdout.strip() \
                if refs_after1 else ""
            data_col2 = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": "git checkout -- file.txt"}, "cwd": str(rec_col)}
            code_col2, obj_col2, _ = handler(data_col2)
        finally:
            aiqt_hooks.datetime = _orig_dt
        dec_col2 = obj_col2.get("hookSpecificOutput", {}).get("permissionDecision") \
            if isinstance(obj_col2, dict) else None
        reason_col2 = obj_col2.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") \
            if isinstance(obj_col2, dict) else ""
        refs_after2 = _recovery_refs(rec_col)
        if got_col1 != "ask" or len(refs_after1) != 1:
            failures.append("(rec-refcollision-setup) expected one ref after the first snapshot and an ASK, "
                            "got dec={} refs={}".format(got_col1, refs_after1))
        if not (code_col2 == 0 and dec_col2 == "ask"):
            failures.append("(rec-refcollision) a create-only ref collision must keep the decision ASK, got "
                            "code={!r} dec={!r}".format(code_col2, dec_col2))
        if "no pre-command recovery snapshot could be created" not in reason_col2:
            failures.append("(rec-refcollision-reason) the ASK reason must surface the collision failure")
        if refs_after2 != refs_after1:
            failures.append("(rec-refcollision-intact) the prior recovery ref must be intact and unique after "
                            "a collision, was {} now {}".format(refs_after1, refs_after2))
        if refs_after2 and first_sha and subprocess.run(
                ["git", "-C", str(rec_col), "rev-parse", refs_after2[0]],
                capture_output=True, text=True, timeout=30).stdout.strip() != first_sha:
            failures.append("(rec-refcollision-sha) the prior recovery ref sha was clobbered by a collision")

        # (rec-fd-nonpristine) C6 (F-D EXPAND): a NON-PRISTINE in-scope ASK (a compound snapshottable
        # command) on a dirty tree now takes a BEST-EFFORT recovery snapshot too, so an asked-then-approved
        # wrapped/compound discard is recoverable. Decision stays ASK; a recovery ref is created.
        rec_fd = _init_repo(tmp / "rec-fd")
        (rec_fd / "file.txt").write_text("committed line\nfd work\n", encoding="utf-8")
        expect("(rec-fd-nonpristine) compound checkout on dirty tree asks",
               "git checkout -- file.txt && echo done", "ask", cwd=str(rec_fd))
        if not _recovery_refs(rec_fd):
            failures.append("(rec-fd-nonpristine-snap) expected a recovery ref on a non-pristine in-scope ASK "
                            "of a dirty tree (F-D EXPAND)")
        # A non-pristine WRAPPED form (a wrapper hides the verb from the segment scan; raw_lossy is the
        # signal) is likewise snapshot-backed against the session cwd.
        rec_fdw = _init_repo(tmp / "rec-fd-wrap")
        (rec_fdw / "file.txt").write_text("committed line\nwrapped fd\n", encoding="utf-8")
        expect("(rec-fd-wrap) wrapped reset --hard on dirty tree asks", "sudo git reset --hard", "ask",
               cwd=str(rec_fdw))
        if not _recovery_refs(rec_fdw):
            failures.append("(rec-fd-wrap-snap) expected a recovery ref on a wrapped in-scope ASK of a dirty "
                            "tree (F-D EXPAND, raw_lossy path)")

        # (rec-fd-nonsnap) C6 (structural): a NON-PRISTINE in-scope command whose ONLY visible lossy sub is a
        # non-snappable verb (stash drop / branch -D) now ALSO takes a best-effort snapshot on a dirty tree.
        # A non-pristine command can hide a snappable verb behind shell quoting/eval that no lexical scan can
        # reliably see, so the guard no longer gates the snapshot on a visible/raw snappable verb: it
        # snapshots whenever the tree is not provably clean. Over-snapshotting a pure stash/branch form is an
        # accepted inert cost (a worktree snapshot cannot capture their asset), never an under-protection; the
        # decision still ASKS.
        rec_ns = _init_repo(tmp / "rec-fd-nonsnap")
        (rec_ns / "file.txt").write_text("committed line\nnonsnap\n", encoding="utf-8")
        expect("(rec-fd-nonsnap-stash) compound stash drop on dirty tree asks",
               "git stash drop && echo done", "ask", cwd=str(rec_ns))
        expect("(rec-fd-nonsnap-branch) compound branch -D on dirty tree asks",
               "git branch -D other && echo done", "ask", cwd=str(rec_ns))
        if not _recovery_refs(rec_ns):
            failures.append("(rec-fd-nonsnap-snap) expected a recovery ref for a non-pristine in-scope "
                            "command on a dirty tree (accepted over-snapshot of a stash/branch form)")

        # (rec-cfgcount) C5 (round-9 fail-safe): ambient GIT_CONFIG_COUNT/KEY_0/VALUE_0 injecting core.worktree
        # at a CLEAN decoy, plus GIT_DISCOVERY_ACROSS_FILESYSTEM, are all NON-COSMETIC ambient GIT_* vars, so
        # the fail-safe repository-view check makes the target UNRESOLVABLE: the guard cannot prove the ACTUAL
        # command (which still carries the injected core.worktree) targets the session cwd, so reset --hard
        # ASKS (never a DENY it cannot justify about the wrong target, never a false ALLOW) and takes a
        # best-effort snapshot of the session cwd (dirty here), exactly like an ambient GIT_DIR/GIT_INDEX_FILE
        # (see rec-decoy/rec-idxfile). Separately,
        # the PROBE still scrubs every ambient GIT_* (the allowlist scrub disables the KEY/VALUE injection and
        # the discovery-boundary override), so it reads the REAL dirty repo (False); nothing is written to the
        # decoy and the real index/HEAD/worktree are untouched.
        rec_cfg = _init_repo(tmp / "rec-cfgcount")
        (rec_cfg / "file.txt").write_text("committed line\nreal cfg work\n", encoding="utf-8")
        cfg_decoy = _init_repo(tmp / "rec-cfgcount-clean")  # a CLEAN worktree the injected config points at
        cfg_head = subprocess.run(["git", "-C", str(rec_cfg), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
        cfg_idx_bytes = (rec_cfg / ".git" / "index").read_bytes()
        cfg_wt_bytes = (rec_cfg / "file.txt").read_bytes()
        cfg_env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
                   "GIT_CONFIG_VALUE_0": str(cfg_decoy), "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"}
        for _k, _v in cfg_env.items():
            os.environ[_k] = _v
        try:
            probe_cfg = aiqt_hooks._tree_is_clean(str(rec_cfg))
            got_cfg = _decision(handler, "git reset --hard", cwd=str(rec_cfg))
        finally:
            for _k in cfg_env:
                os.environ.pop(_k, None)
        if probe_cfg is not False:
            failures.append("(rec-cfgcount-probe) with GIT_CONFIG_COUNT injecting core.worktree at a clean "
                            "decoy, the probe must still read the REAL dirty repo (False), got {}"
                            .format(probe_cfg))
        if got_cfg != "ask":
            failures.append("(rec-cfgcount) reset --hard under an injected core.worktree decoy via a "
                            "non-cosmetic ambient GIT_CONFIG_COUNT must ASK (unresolvable target, fail-safe), "
                            "got {}".format(got_cfg))
        if not _recovery_refs(rec_cfg):
            failures.append("(rec-cfgcount-snap) the ambient-override ASK on a dirty cwd must take a "
                            "best-effort recovery snapshot on the REAL repo")
        if _recovery_refs(cfg_decoy):
            failures.append("(rec-cfgcount-wrongwrite) a recovery ref was written to the DECOY (an injected "
                            "core.worktree leaked into the snapshot)")
        if (rec_cfg / ".git" / "index").read_bytes() != cfg_idx_bytes:
            failures.append("(rec-cfgcount-index) the real index changed under an injected GIT_CONFIG_COUNT")
        if (rec_cfg / "file.txt").read_bytes() != cfg_wt_bytes:
            failures.append("(rec-cfgcount-worktree) the real worktree changed under an injected "
                            "GIT_CONFIG_COUNT")
        cfg_head_after = subprocess.run(["git", "-C", str(rec_cfg), "rev-parse", "HEAD"],
                                        capture_output=True, text=True, timeout=30).stdout.strip()
        if cfg_head_after != cfg_head:
            failures.append("(rec-cfgcount-head) the real HEAD changed under an injected GIT_CONFIG_COUNT")

        # (rec-sizecap-subdir) C2: the size-cap estimate anchors on the resolved TOPLEVEL, not the (deeper)
        # session cwd. cwd is a SUBDIR while the only dirty file lives at the toplevel; status paths are
        # toplevel-relative, so a correct estimate joins them onto the toplevel and SEES the dirty file. With
        # the cap forced below that file's size the would-be ALLOW (reset --soft) downgrades to ASK. Were the
        # estimate anchored on the subdir, the join would miss the toplevel file (size 0), stay under the cap,
        # and wrongly ALLOW.
        rec_capsub = _init_repo(tmp / "rec-sizecap-subdir")
        (rec_capsub / "file.txt").write_text("committed line\n" + "x" * 4096 + "\n", encoding="utf-8")
        capsub_dir = rec_capsub / "sub" / "deep"
        capsub_dir.mkdir(parents=True, exist_ok=True)
        _orig_cap = aiqt_hooks._RECOVERY_SIZE_CAP
        aiqt_hooks._RECOVERY_SIZE_CAP = 64  # below the toplevel file size, above an empty (subdir) estimate
        try:
            got_capsub = _decision(handler, "git reset --soft", cwd=str(capsub_dir))
        finally:
            aiqt_hooks._RECOVERY_SIZE_CAP = _orig_cap
        if got_capsub != "ask":
            failures.append("(rec-sizecap-subdir) the size estimate must anchor on the toplevel (not the "
                            "subdir cwd), downgrading a would-be ALLOW to ASK when the toplevel file exceeds "
                            "the cap, got {}".format(got_capsub))
        if _recovery_refs(rec_capsub):
            failures.append("(rec-sizecap-subdir-snap) expected NO recovery ref when the toplevel-anchored "
                            "estimate exceeds the size cap")

        # (rec-record-boundary) C3/item-2: _record_recovery wraps its whole body so ANY unexpected error is
        # downgraded to a graceful ('fail', reason), never propagated (an uncaught exception would exit-2
        # HARD-BLOCK even a would-ALLOW command). An embedded-NUL repo path makes the underlying subprocess
        # call raise, and a non-UTF-8 path is likewise unresolvable; both must come back as a 'fail' tuple.
        for _label, _badrepo in (("embedded-nul", str(repo) + "\x00bad"),
                                  ("non-utf8", str(repo) + "/\udcff\udcfe-bad")):
            try:
                _res = aiqt_hooks._record_recovery(_badrepo, "reset")
            except Exception as _exc:  # the whole point: nothing may propagate to the dispatcher
                _res = ("CRASH", "{}: {}".format(type(_exc).__name__, _exc))
            if not (isinstance(_res, tuple) and _res[0] == "fail"):
                failures.append("(rec-record-boundary-{}) _record_recovery on a bad repo path must return a "
                                "graceful ('fail', ...), got {!r}".format(_label, _res))
        # End-to-end: a snapshottable discard whose session cwd carries an embedded NUL must fail-to-ASK
        # gracefully (the recovery boundary turns the ValueError into a snapshot fail), never crash the guard.
        rec_nul = _init_repo(tmp / "rec-nulcwd")
        (rec_nul / "file.txt").write_text("committed line\nnul cwd\n", encoding="utf-8")
        try:
            got_nul = _decision(handler, "git reset --soft", cwd=str(rec_nul) + "\x00sub")
        except Exception as _exc:
            got_nul = "CRASH ({}: {})".format(type(_exc).__name__, _exc)
        if got_nul != "ask":
            failures.append("(rec-nulcwd) an embedded-NUL session cwd must fail-to-ASK gracefully, got {}"
                            .format(got_nul))

        # (rec-path-within-root) G1: _path_is_within treats a real subpath of the filesystem root '/' as
        # INSIDE '/' (the earlier base+os.sep test made '/' into '//', so every candidate read as OUTSIDE and
        # the containment guard was silently bypassed). Root itself is within root; a real subpath is too.
        if aiqt_hooks._path_is_within("/", "/") is not True:
            failures.append("(rec-path-within-root-self) '/' must be judged within '/'")
        if aiqt_hooks._path_is_within(str(repo), "/") is not True:
            failures.append("(rec-path-within-root-sub) a real subpath must be judged within the root '/'")

        # (rec-cfgparams) Fix A: an ambient GIT_CONFIG_PARAMETERS (the `git -c` propagation channel,
        # independent of GIT_CONFIG_COUNT) injecting core.excludesFile at a file that lists the untracked
        # name must NOT fool the probe into reading the dirty tree as clean. GIT_CONFIG_PARAMETERS is an
        # ambient GIT_* var, so the allowlist scrub drops it and the untracked file still reads dirty (False).
        rec_cp = _init_repo(tmp / "rec-cfgparams")
        (rec_cp / "untracked.txt").write_text("junk\n", encoding="utf-8")
        excludes_file = tmp / "cp-excludes"
        excludes_file.write_text("untracked.txt\n", encoding="utf-8")
        os.environ["GIT_CONFIG_PARAMETERS"] = "'core.excludesFile={}'".format(excludes_file)
        try:
            probe_cp = aiqt_hooks._tree_is_clean(str(rec_cp))
        finally:
            os.environ.pop("GIT_CONFIG_PARAMETERS", None)
        if probe_cp is not False:
            failures.append("(rec-cfgparams) with an ambient GIT_CONFIG_PARAMETERS injecting "
                            "core.excludesFile that hides the untracked file, the probe must still read the "
                            "REAL dirty repo (False), got {}".format(probe_cp))

        # (rec-gittrace) Fix D: git treats an absolute GIT_TRACE value as a FILE PATH and APPENDS trace
        # output to it, so an ambient GIT_TRACE would make even the read-only probe and the recovery git
        # calls WRITE a file (possibly inside the protected worktree). _isolate_git_env scrubs every
        # GIT_TRACE-prefixed var, so after a probe and a snapshot the trace target is NEVER created.
        rec_tr = _init_repo(tmp / "rec-gittrace")
        (rec_tr / "file.txt").write_text("committed line\ntrace dirty\n", encoding="utf-8")
        trace_target = tmp / "git-trace-out.log"
        trace2_target = Path(str(trace_target) + ".t2")
        os.environ["GIT_TRACE"] = str(trace_target)
        os.environ["GIT_TRACE2"] = str(trace2_target)
        try:
            _ = aiqt_hooks._tree_is_clean(str(rec_tr))
            got_tr = _decision(handler, "git checkout -- file.txt", cwd=str(rec_tr))
        finally:
            os.environ.pop("GIT_TRACE", None)
            os.environ.pop("GIT_TRACE2", None)
        if got_tr != "ask":
            failures.append("(rec-gittrace) dirty-tree ASK with an ambient GIT_TRACE: expected ask, got {}"
                            .format(got_tr))
        if trace_target.exists() or trace2_target.exists():
            failures.append("(rec-gittrace-file) an ambient GIT_TRACE/GIT_TRACE2 trace file was written; a "
                            "real-state call did not scrub the GIT_TRACE family")
        if not _recovery_refs(rec_tr):
            failures.append("(rec-gittrace-snap) expected a recovery ref on the dirty tree ASK")

        # (rec-scrub-allowlist) Fix 1: _isolate_git_env takes an ALLOWLIST posture - it scrubs EVERY ambient
        # GIT_*-prefixed var, not an enumerated family, so a random GIT_FOO and the round-5 GIT_ATTR_SOURCE
        # both go while non-GIT vars stay. This is the structural guarantee that closes the whole ambient-env
        # class at once (rounds 4-5 kept finding new members: GIT_CONFIG_*, GIT_TRACE*, GIT_ATTR_SOURCE).
        # The ambient GIT_NO_LAZY_FETCH/GIT_TERMINAL_PROMPT and the ambient git identity (GIT_AUTHOR_*/
        # GIT_COMMITTER_*) are set the WRONG way here to prove the re-assertion (Class B) OVERRIDES an ambient
        # value, not merely fills an absent one; the fixed recovery identity (F-106) must WIN over an ambient
        # user identity, so a recovery commit is deterministically the guard's and never depends on ambient
        # or on-disk identity that a fresh install / CI runner may lack.
        scrub_in = {"GIT_FOO": "bar", "GIT_ATTR_SOURCE": "HEAD", "GIT_DIR": "decoy",
                    "GIT_CONFIG_PARAMETERS": "'x=y'", "GIT_TRACE": "on",
                    "GIT_NO_LAZY_FETCH": "0", "GIT_TERMINAL_PROMPT": "1",
                    "GIT_AUTHOR_NAME": "Ambient User", "GIT_AUTHOR_EMAIL": "ambient@example.invalid",
                    "GIT_COMMITTER_NAME": "Ambient User", "GIT_COMMITTER_EMAIL": "ambient@example.invalid",
                    "LANG": "C", "TERM": "dumb"}
        scrub_out = aiqt_hooks._isolate_git_env(dict(scrub_in))
        # The PROTECTIVE vars and the fixed recovery identity are re-asserted AFTER the scrub (Class B), so
        # they are EXPECTED to be present at their fixed values; every OTHER ambient GIT_* must be gone. The
        # identity values are read from the source constants so this expectation cannot drift from the fix.
        _protective = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
                       "GIT_AUTHOR_NAME": aiqt_hooks._RECOVERY_IDENTITY_NAME,
                       "GIT_AUTHOR_EMAIL": aiqt_hooks._RECOVERY_IDENTITY_EMAIL,
                       "GIT_COMMITTER_NAME": aiqt_hooks._RECOVERY_IDENTITY_NAME,
                       "GIT_COMMITTER_EMAIL": aiqt_hooks._RECOVERY_IDENTITY_EMAIL}
        _leaked = sorted(k for k in scrub_out if k.startswith("GIT_") and k not in _protective)
        if _leaked:
            failures.append("(rec-scrub-allowlist) _isolate_git_env must scrub EVERY ambient GIT_* var; "
                            "these survived the allowlist scrub: {}".format(_leaked))
        for _pk, _pv in _protective.items():
            if scrub_out.get(_pk) != _pv:
                failures.append("(rec-scrub-protective) _isolate_git_env must re-assert {}={} after the scrub "
                                "(overriding any ambient value), got {!r}".format(_pk, _pv, scrub_out.get(_pk)))
        if scrub_out.get("LANG") != "C" or scrub_out.get("TERM") != "dumb":
            failures.append("(rec-scrub-allowlist-keep) _isolate_git_env must leave non-GIT vars intact")

        # (rec-nolazyfetch) Class B: the protective offline/non-interactive vars must reach the ACTUAL git
        # invocation, not just _isolate_git_env. Capture the env _recovery_git threads into subprocess.run and
        # assert GIT_NO_LAZY_FETCH=1 (no partial-clone lazy-fetch over the network) and GIT_TERMINAL_PROMPT=0
        # survive, even when the ambient env sets them the OTHER way. A full offline promisor lazy-fetch is not
        # feasible to stage deterministically here, so this asserts the mechanism at the call site.
        rec_nlf = _init_repo(tmp / "rec-nolazyfetch")
        captured_env = {}
        _orig_run = aiqt_hooks.subprocess.run

        def _capturing_run(cmd, *a, **kw):
            captured_env.clear()
            captured_env.update(kw.get("env") or {})
            return _orig_run(cmd, *a, **kw)

        os.environ["GIT_NO_LAZY_FETCH"] = "0"
        os.environ["GIT_TERMINAL_PROMPT"] = "1"
        aiqt_hooks.subprocess.run = _capturing_run
        try:
            aiqt_hooks._recovery_git(str(rec_nlf), ["rev-parse", "--show-toplevel"], timeout=5)
        finally:
            aiqt_hooks.subprocess.run = _orig_run
            os.environ.pop("GIT_NO_LAZY_FETCH", None)
            os.environ.pop("GIT_TERMINAL_PROMPT", None)
        if captured_env.get("GIT_NO_LAZY_FETCH") != "1":
            failures.append("(rec-nolazyfetch) _recovery_git must pass GIT_NO_LAZY_FETCH=1 to git, got {!r}"
                            .format(captured_env.get("GIT_NO_LAZY_FETCH")))
        if captured_env.get("GIT_TERMINAL_PROMPT") != "0":
            failures.append("(rec-nolazyfetch-prompt) _recovery_git must pass GIT_TERMINAL_PROMPT=0 to git, "
                            "got {!r}".format(captured_env.get("GIT_TERMINAL_PROMPT")))

        # (rec-attrsource) Fix 1: an ambient GIT_ATTR_SOURCE (round-5's newest ambient-GIT_* vector, which
        # points git's attribute lookup at a chosen treeish) does NOT survive the allowlist scrub. Set to an
        # unresolvable ref it would make an UN-scrubbed status probe FAIL (git: 'bad --attr-source or
        # GIT_ATTR_SOURCE', a non-zero return -> None); with the scrub it is gone, so the probe reads the REAL
        # dirty repo (False) rather than being steered or made to error by the ambient value.
        rec_as = _init_repo(tmp / "rec-attrsource")
        (rec_as / "untracked.txt").write_text("junk\n", encoding="utf-8")
        os.environ["GIT_ATTR_SOURCE"] = "refs/nonexistent-attr-source"
        try:
            probe_as = aiqt_hooks._tree_is_clean(str(rec_as))
        finally:
            os.environ.pop("GIT_ATTR_SOURCE", None)
        if probe_as is not False:
            failures.append("(rec-attrsource) with an ambient GIT_ATTR_SOURCE set to an unresolvable ref, "
                            "the allowlist scrub must drop it so the probe reads the REAL dirty repo (False), "
                            "got {}".format(probe_as))

        # (rec-ledger-nonfatal) Fix B: the best-effort ledger must NEVER flip a SUCCESSFUL snapshot to a
        # failure. Monkeypatch _write_recovery_ledger to raise a non-OSError (a ValueError) AFTER the
        # snapshot succeeds; _record_recovery must still return ('ok', info) with the ref present (the ledger
        # write is outside the snapshot-fail boundary and separately guarded).
        rec_lnf = _init_repo(tmp / "rec-ledger-nonfatal")
        (rec_lnf / "file.txt").write_text("committed line\nledger nonfatal\n", encoding="utf-8")
        _orig_wrl = aiqt_hooks._write_recovery_ledger

        def _raise_ledger(*_a, **_k):
            raise ValueError("forced non-OSError ledger fault (self-test)")

        aiqt_hooks._write_recovery_ledger = _raise_ledger
        try:
            res_lnf = aiqt_hooks._record_recovery(str(rec_lnf), "checkout")
        finally:
            aiqt_hooks._write_recovery_ledger = _orig_wrl
        if not (isinstance(res_lnf, tuple) and res_lnf[0] == "ok"):
            failures.append("(rec-ledger-nonfatal) a non-OSError from the ledger write after a SUCCESSFUL "
                            "snapshot must NOT flip the result; expected ('ok', info), got {!r}"
                            .format(res_lnf))
        if not _recovery_refs(rec_lnf):
            failures.append("(rec-ledger-nonfatal-snap) the successful snapshot ref must be present despite a "
                            "ledger fault")

        # (rec-c6-subst/rec-c6-wrap/rec-c6-eval) C6 (structural): a non-pristine in-scope command can hide a
        # snappable verb behind shell substitution ($(...)), a wrapper (sudo), or split-quoting/eval that no
        # lexical scan can reliably see. The guard no longer gates the snapshot on detecting a snappable verb:
        # any non-pristine in-scope command on a not-provably-clean tree is snapshot-backed, so an approved
        # hidden reset/checkout always has a recovery point. Each dirty-tree ASK below must create a ref.
        rec_c6a = _init_repo(tmp / "rec-c6-subst")
        (rec_c6a / "file.txt").write_text("committed line\nc6 subst\n", encoding="utf-8")
        expect("(rec-c6-subst) stash drop + hidden checkout -f asks",
               "git stash drop && $(echo git checkout -f)", "ask", cwd=str(rec_c6a))
        if not _recovery_refs(rec_c6a):
            failures.append("(rec-c6-subst-snap) expected a recovery ref: a hidden snappable verb (checkout "
                            "-f) behind a substitution must still snapshot on a dirty tree")
        rec_c6b = _init_repo(tmp / "rec-c6-wrap")
        (rec_c6b / "file.txt").write_text("committed line\nc6 wrap\n", encoding="utf-8")
        expect("(rec-c6-wrap) stash drop + hidden wrapped reset --hard asks",
               "git stash drop; sudo git reset --hard", "ask", cwd=str(rec_c6b))
        if not _recovery_refs(rec_c6b):
            failures.append("(rec-c6-wrap-snap) expected a recovery ref: a hidden snappable verb (reset "
                            "--hard) behind a wrapper must still snapshot on a dirty tree")
        # The split-quote/eval case is the one a raw lexical scan CANNOT catch: `re'set'` has no contiguous
        # `reset` in the raw string, yet bash assembles `reset` at runtime. The structural fix snapshots it
        # anyway, because the command is non-pristine on a not-provably-clean tree.
        rec_c6d = _init_repo(tmp / "rec-c6-eval")
        (rec_c6d / "file.txt").write_text("committed line\nc6 eval\n", encoding="utf-8")
        expect("(rec-c6-eval) stash drop + split-quote eval reset asks",
               "git stash drop; eval git re'set' --hard", "ask", cwd=str(rec_c6d))
        if not _recovery_refs(rec_c6d):
            failures.append("(rec-c6-eval-snap) expected a recovery ref: a snappable verb hidden by "
                            "split-quoting/eval must still snapshot on a dirty tree")
        # A pure stash/echo non-pristine command with NO snappable verb anywhere NOW ALSO snapshots (the
        # rec-c6-nosnap assertion FLIPS): the guard cannot prove no snappable verb is hidden, so it never
        # gates on absence. Over-snapshotting a pure stash form is an accepted inert cost, never an
        # under-protection.
        rec_c6c = _init_repo(tmp / "rec-c6-nosnap")
        (rec_c6c / "file.txt").write_text("committed line\nc6 nosnap\n", encoding="utf-8")
        expect("(rec-c6-nosnap) stash drop; echo hi asks", "git stash drop; echo hi", "ask",
               cwd=str(rec_c6c))
        if not _recovery_refs(rec_c6c):
            failures.append("(rec-c6-nosnap-snap) expected a recovery ref: any non-pristine in-scope command "
                            "on a dirty tree is snapshot-backed (accepted over-snapshot)")
        # === protected_line (prtbrn/artbr1): force-push to a protected ref + direct protected commit ===
        plg = aiqt_hooks.protected_line

        def pexpect(label, command, want, cwd=None):
            got = _decision(plg, command, cwd=cwd)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        pl_repo = _init_repo(tmp / "pl-repo")  # HEAD is main (a protected name) by construction
        plr = str(pl_repo)
        pl_feat = _init_repo(tmp / "pl-feat")
        _git(pl_feat, "switch", "other")       # HEAD is the non-protected 'other'
        plf = str(pl_feat)

        # The core contract: force to protected DENIES, force to a feature ref ALLOWS, '+' refspec to
        # protected DENIES, a plain non-force push ALLOWS (server-side protection is the real gate).
        pexpect("(pl-a) push --force to main denies", "git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-b) push --force to a feature branch allows",
                "git push --force origin my-feature", "allow", cwd=plr)
        pexpect("(pl-c) '+'-prefixed refspec to main denies", "git push origin +main", "deny", cwd=plr)
        pexpect("(pl-d) plain push to main allows", "git push origin main", "allow", cwd=plr)
        pexpect("(pl-e) '+' refspec to a feature branch allows",
                "git push origin +my-feature", "allow", cwd=plr)
        pexpect("(pl-e2) '+main:feature' forces only feature: allows",
                "git push origin +main:feature", "allow", cwd=plr)

        # Force spellings: -f, clustered, with-lease bare and =value, if-includes, a conservative long
        # prefix, the refs/heads/ qualifier, src:dst destination matching, and the master name.
        pexpect("(pl-f1) push -f to main denies", "git push -f origin main", "deny", cwd=plr)
        pexpect("(pl-f2) clustered -nf carries force", "git push -nf origin main", "deny", cwd=plr)
        pexpect("(pl-f3) --force-with-lease to main denies",
                "git push --force-with-lease origin main", "deny", cwd=plr)
        pexpect("(pl-f4) --force-with-lease=main:sha denies",
                "git push --force-with-lease=main:0000 origin main", "deny", cwd=plr)
        pexpect("(pl-f5) --force-if-includes denies",
                "git push --force-if-includes --force-with-lease origin main", "deny", cwd=plr)
        pexpect("(pl-f6) abbreviated --forc treated as force (conservative prefix)",
                "git push --forc origin main", "deny", cwd=plr)
        pexpect("(pl-f7) refs/heads/main qualifies as protected",
                "git push --force origin refs/heads/main", "deny", cwd=plr)
        pexpect("(pl-f8) src:dst matches the DESTINATION: feature:main denies",
                "git push --force origin feature:main", "deny", cwd=plr)
        pexpect("(pl-f9) main:feature forces only feature: allows",
                "git push --force origin main:feature", "allow", cwd=plr)
        pexpect("(pl-f10) push --force to master denies", "git push --force origin master", "deny",
                cwd=plr)

        # Argument-aware flags (the F-94/F-82 lesson): an '-o' VALUE is never scanned as flags or read
        # as a refspec, and a non-force long option sharing a prefix letter is not force.
        pexpect("(pl-g1) '-of' is -o with attached value 'f', not force",
                "git push -of origin main", "allow", cwd=plr)
        pexpect("(pl-g2) separated '-o force' value is not a flag",
                "git push -o force origin my-feature", "allow", cwd=plr)
        pexpect("(pl-g3) --follow-tags is not force", "git push --follow-tags origin main", "allow",
                cwd=plr)
        pexpect("(pl-g4) a separated --push-option value naming main is consumed, not a refspec",
                "git push --force --push-option main origin my-feature", "allow", cwd=plr)

        # A forced sweep the guard cannot prove misses the protected names ASKS.
        pexpect("(pl-h1) forced wildcard refspec asks",
                "git push --force origin refs/heads/*:refs/heads/*", "ask", cwd=plr)
        pexpect("(pl-h2) --mirror push asks", "git push --mirror backup", "ask", cwd=plr)
        pexpect("(pl-h3) forced --all asks", "git push --force --all origin", "ask", cwd=plr)

        # Refspec-less force-push: resolved through the HEAD probe (real repos, no mock).
        pexpect("(pl-i1) bare 'git push --force' with HEAD=main denies", "git push --force", "deny",
                cwd=plr)
        pexpect("(pl-i2) 'git push --force origin' with HEAD=main denies", "git push --force origin",
                "deny", cwd=plr)
        pexpect("(pl-i3) bare force-push with HEAD on a feature branch allows", "git push --force",
                "allow", cwd=plf)

        # Direct commit: ASK on the protected branch, ALLOW on a feature branch (HEAD probed for real).
        pexpect("(pl-j1) git commit with HEAD=main asks", "git commit -m 'fix'", "ask", cwd=plr)
        pexpect("(pl-j2) git commit on a feature branch allows", "git commit -m 'fix'", "allow",
                cwd=plf)
        pexpect("(pl-j3) add-and-commit compound on main asks",
                "git add -A && git commit -m 'fix'", "ask", cwd=plr)
        pexpect("(pl-j4) commit --amend on main asks", "git commit --amend --no-edit", "ask", cwd=plr)
        pexpect("(pl-j5) 'git -C <dir> commit' asks (redirected repository view)",
                "git -C {} commit -m 'fix'".format(plf), "ask", cwd=plr)
        os.environ["GIT_DIR"] = str(pl_feat / ".git")
        try:
            pexpect("(pl-j6) commit under an ambient GIT_DIR asks (unprovable view)",
                    "git commit -m 'fix'", "ask", cwd=plr)
        finally:
            os.environ.pop("GIT_DIR", None)

        # Probe failure is fail-to-ASK for both surfaces (mocked like _tree_is_clean above).
        _orig_head = aiqt_hooks._head_branch
        aiqt_hooks._head_branch = lambda repo: None
        try:
            pexpect("(pl-i4) bare force-push asks when HEAD cannot be resolved",
                    "git push --force", "ask", cwd=plr)
            pexpect("(pl-i5) direct commit asks when HEAD cannot be resolved",
                    "git commit -m 'x'", "ask", cwd=plr)
        finally:
            aiqt_hooks._head_branch = _orig_head

        # Parse-error posture (unbalanced quote): fail-safe, never a silent allow.
        pexpect("(pl-k1) unparseable apparent force-push naming main denies",
                'git push --force origin main "unbalanced', "deny", cwd=plr)
        pexpect("(pl-k2) unparseable force-push with no readable protected target asks",
                'git push --force "unbalanced', "ask", cwd=plr)
        pexpect("(pl-k3) unparseable apparent commit asks", 'git commit -m "it broke', "ask", cwd=plr)
        pexpect("(pl-k4) unparseable non-git command allows", 'ls -la "unbalanced', "allow", cwd=plr)

        # A deny in a later segment wins over an earlier ask; discard verbs are out of this scope.
        pexpect("(pl-l1) a later force-push deny wins over an earlier commit ask",
                "git commit -m 'x' && git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-m1) a git_discard verb is out of protected_line scope (disjoint controls)",
                "git reset --hard", "allow", cwd=plr)

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
          "is proven: every shell-grammar and wrapper form that hides a real reset --hard while the raw scan "
          "still sees a contiguous git+verb keyword (if/for, backtick and $() substitution, |&, leading and "
          "interspersed redirects, and sudo/nice/timeout/nohup/sh -c/bash -c wrappers) now ASKS; a wrapper "
          "that ALSO fragments the command word 'git' or the verb is a disclosed residual, silently ALLOWED "
          "and not chased. The four accuracy fixes are proven: the config-forced probe defeats "
          "status.showUntrackedFiles=no (untracked reads dirty -> DENY/ASK, not allow); clean -e/--exclude "
          "arg-consumption means '-n' is not mis-read as a dry run; switch --merge/--conflict route to "
          "scoped and ASK on a dirty tree; and the DENY wording covers untracked. The EN-6 round-22 fixes "
          "are proven: checkout -B and switch -C/--force-create force-create/RESET a branch ref (the "
          "reflog-recoverable class of branch -f/-M/-C) and ASK, even combined with other flags (F-81); the "
          "branch option parser treats an attached -u<upstream> value as the upstream and not a clustered "
          "force flag, so -ufoo/-uMain/-uCandidate ALLOW while a real force delete/move/copy/reset still "
          "ASKS (F-82). The EN-6 round-23 fix is proven: the checkout/switch parser consumes the new-branch "
          "NAME argument of -b/-B/-c/-C (attached -bfoo or separated -b foo), so -bfoo/-bBranch/-cfeature "
          "ALLOW while -Bfoo/-B foo/-Cfoo/-C foo/--force-create ASK (F-85). The prior GD-41 "
          "blocker cases and the F-60/F-62/F-64/F-65/F-66 under-block edges still ASK/DENY, and the role "
          "classifier is asserted too. The EN-6 recovery/snapshot layer is proven: a snapshot is taken on a "
          "dirty-tree ASK and on a simulated mis-parse ALLOW, NOT on a provably-clean tree; the real "
          "status/index/HEAD are unchanged after a snapshot; the ref restores tracked and untracked work; a "
          "forced snapshot failure downgrades a would-be ALLOW to ASK while leaving a clobber DENY; and the "
          "external ledger records the bare verb (not the raw command), ref, sha, classes, and restore. The "
          "EN-6 round-25 fixes are proven: a branch delete combined with -r/--remotes ASKS while a local -d "
          "still allows (F-94); git stash export ASKS for every spelling (F-95); and a raw-lossy-flagged "
          "command whose resolved subcommand is outside the recognized set (checkout-index, read-tree "
          "--reset) ASKS rather than winning the catch-all allow, while recognized safe forms and the "
          "unflagged git worktree remove are unchanged (F-97). The protected-line guard (EN-5 "
          "PR-A, prtbrn/artbr1) is proven: a force-push (every -f/--force/--force-with-lease/"
          "--force-if-includes spelling, a conservative long prefix, and a '+'-prefixed "
          "refspec) whose DESTINATION names a protected branch (main/master, bare or "
          "refs/heads/-qualified) DENIES, while a force-push to a feature branch and a plain "
          "non-force push ALLOW; a refspec-less force-push resolves HEAD by a scrubbed "
          "read-only probe (deny on a protected HEAD, fail-to-ASK when unresolvable); a "
          "--mirror or forced --all/--branches sweep ASKS; a direct git commit while HEAD is "
          "protected (or unprovable) ASKS; an '-o' value is not mis-scanned as force; and "
          "the parse-error fallback denies/asks an apparent protected force-push or commit, "
          "never a silent allow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
