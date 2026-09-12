#!/usr/bin/env python3
"""Behavioural self-test for the enforcement-hook handlers in .aiqt/core/hooks/scripts/aiqt_hooks.py.

The gen_hooks.py self-test proves the GENERATOR's fail-closed invariants; this one proves the HANDLERS'
decisions, so a regression in a control's logic (not just its wiring) fails a gate. It imports the source
handler module directly (never the generated plugin copy) and drives each handler with a synthetic hook
payload, asserting the structured decision the handler returns, not by grepping output.

NO-ASK READING KEY (disclose-accuracy): these hooks NO LONGER emit permissionDecision "ask" - the _ask
constructor is removed and the global invariant at the end of main() asserts no guard returns "ask" over a
broad vector battery. The words "ASK"/"ASKS" still appear throughout this file's docstring, inline
case-comments, and case LABELS as HISTORICAL shorthand for the retired ask outcome; read every such
occurrence as its live no-ask resolution - a benign or convention-level case ALLOWS (with a note), a
recoverable git discard is SNAPSHOT-THEN-ALLOWED (allowed once its refs/aiqt-recovery/ snapshot exists,
denied only if that snapshot cannot be made), and a confirmed or genuinely-ambiguous hazard
DENIES-and-educates. The reducers map an allow-with-note (a systemMessage with no permissionDecision) to
"allow", so a case LABELLED "... asks" now asserts want="allow" or want="deny" accordingly, and the
PASS banner enumerates the current per-guard outcomes. The value "ask" survives only in the reducers'
three-value vocabulary and in the invariant that proves it never occurs.

The git_discard control was originally the EN-6 ULTRA-CONSERVATIVE "ask unless PRISTINE and provably clean"
guard with THREE outcomes (allow/ask/deny); under the key above its former ASK is now snapshot-then-allow
(recoverable) or deny (unrecoverable/unclassifiable). This suite distinguishes those outcomes. The rule: for a recognized lossy
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

It also covers the protected-line guard (protected_line, prtbrn/artbr1): a force-push OR a branch
DELETION of a protected branch (main/master) denies, with the banner naming the actual act, while a
force or delete to a feature branch allows; a refspec-less force-push, a forced or deleted HEAD/@, and
a direct commit (the literal commit subcommand only) are judged by a read-only HEAD probe (fail-to-ASK
when unresolvable); a --mirror/--all, wildcard, matching-':'/'+:', or --prune-with-wildcard sweep asks,
as does a command-local remote.<name>.mirror=true config (or the key via --config-env) before the push
subcommand (GD-146); and the parse-error/wrapper fallback fails safe for the force-push, deletion,
mirror-config, AND commit spellings.

It covers branch_root (brnrot) through H1-H32: rooted creation allows, an orphaned explicit start
denies, an unresolvable origin/HEAD asks, non-creation git commands allow, and non-git commands allow;
the switch long-form create extracts its start and denies an orphan while git-branch --track routes to ASK; git-branch
upstream-setting forms are non-creation; --track/-t and --orphan (checkout/switch/worktree) and any
unknown-arity option in a creation-ish command ASK rather than silently allow; and a rooted-but-stale
start allows (only the CI gate's --max-lag catches staleness). Every outcome is asserted from the
handler's structured decision.

It also covers the gate-weakening guard (gate_weakening, gatdis): a git verification-hook bypass
(--no-verify on commit/merge/push/pull/rebase/am, exact or abbreviated; the short -n only on
commit/am, where -n IS --no-verify) denies; a checker-shaped segment whose failure is swallowed
(|| true, || :) or piped into a truncating sink (| head, | tail) asks; option-value, post-'--',
and push/merge -n edges stay allowed; and the parse-error fallback fails safe.

It also covers the BEST-EFFORT, ASK-only git-commit argument substitution guard
(commit_msg_subst, sectvl). In a parsed simple segment whose effective command resolves past the named
command modifiers to literal git, every token after the literal commit subcommand is scanned, including
tokens after `--`; single-quoted literals ALLOW, while escaped literals ALLOW except for the disclosed
bare-`$`-before-escaped-paren over-ASK. The raw fallback ASKS on a coarse apparent hit. The tests pin
detected cases, deliberate over-ASKs, and selected named residual ALLOWs; they make no soundness or
universal-coverage claim.

It also covers the secrets-shift-left guard (secrets_shift_left, secsec): a Write content, an Edit
new_string, or a Bash command that carries an obvious hardcoded secret (a provider-token prefix or a
credential-named assignment of a real-length literal, single-sourced from tools/check_secrets.py) denies;
a placeholder value, ordinary code, an out-of-scope Read, and a Bash command with no secret allow; and a
missing tool_name or an absent target field fails closed. Every secret fixture is synthetic-but-shaped.

It also covers the generated-artefact edit guard (gensrc_guard, gensrc): a Write/Edit/MultiEdit whose
file_path resolves onto a kind=file or kind=tree entry of the per-repo .aiqt/gensrc.json (read at
decision time) ASKS the steering ask, while a source edit, an unregistered path, and a kind=block entry
ALLOW, Bash is out of scope, and component-boundary matching means gen-extra/ and GEN.md.bak do not
match gen/ and GEN.md. Each fault branch is designed to fail SAFE to ASK (an unreadable, malformed, or
unknown-version registry, a malformed entry, an unresolvable repo root, a non-contained target, and an
unreadable payload field), an absent registry is the inert ALLOW, and only a missing tool_name denies.
Fixtures are throwaway git repos under the temp tree (a registry-carrying repo, a registry-less repo, a
mutable-bad-registry repo, and a plain non-git dir), removed in the finally.

It also covers the write-scope guard (write_scope_guard, wrtscp, EN-8): a Write/Edit/MultiEdit is confined
to a harness-set per-slice declaration (write-scope.json at the registry-declared state_dir, out of the
slice tree by default but possibly in-tree) while writes to the frozen floor (.aiqt/frozen.json, one entry
per frozen class {derived, manifest-self, archive}) and to other or nested git repositories are hard-denied
as an un-lowerable floor. It is inert only on a genuine absence (a missing declaration for slice
confinement, or a genuinely-absent floor un-armed); a covered write whose repository root is unresolvable,
a bad/unreadable orchestration registry, and every un-armed cannot-evaluate FAULT all deny fail-closed. The
structural denial applies whenever the root resolves and the frozen denial whenever a floor is present or
the session is armed, and every cannot-evaluate denies once armed. Fixtures are throwaway git repos under the temp tree (a session repo, a
sibling repo, and a nested repo), with the declaration written into the redirected XDG_STATE_HOME and the
floor synthesized in-tree, removed in the finally.

  selftest_aiqt_hooks.py    exit 0 on SELF-TEST PASS, 1 on SELF-TEST FAIL, 2 on a harness/setup error
"""
import contextlib
import datetime
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import gen_secret_patterns  # noqa: E402  (same tools dir, for the drift-gate F-129 self-test)

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402


def _decision(handler, command, tool="Bash", cwd=None):
    """Run a PreToolUse handler over a synthetic Bash payload and reduce its result to 'allow', 'ask', or
    'deny'. An allow is the silent no-decision (exit 0, no stdout object) OR an allow-with-note (exit 0 with
    a systemMessage and NO permissionDecision, the _allow_note shape the no-ask posture uses to surface an
    informational note while the user's own permission flow still governs); a deny carries permissionDecision
    "deny"; an "ask" (which these hooks must NEVER emit) carries permissionDecision "ask". Any other shape is
    surfaced as a harness error string so a malformed decision cannot read as a pass."""
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
        # An _allow_note: a systemMessage with NO permissionDecision is an informational allow.
        if "hookSpecificOutput" not in stdout_obj and "systemMessage" in stdout_obj:
            return "allow"
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
        expect("(bound-b) unparseable + lossy verb asks", 'git checkout -- "unbalanced', "allow", cwd=rp)
        expect("(bound-b2) unparseable non-lossy command allows", 'ls -la "unbalanced', "allow")
        expect("(bound-b3) unparseable + lossy + opt-out prefix still ASKS (round-15: opt-out not honoured on unparseable)",
               'GUARDRAIL_ALLOW_DISCARD=1 git reset --hard "unbalanced', "allow", cwd=rp)

        # === a PROVABLY CLEAN tree: every recognized discard is safe -> ALLOW ================
        expect("(clean-a) reset --hard clean allows", "git reset --hard", "allow", cwd=rp)
        expect("(clean-b) checkout -- clean allows", "git checkout -- file.txt", "allow", cwd=rp)
        expect("(clean-c) checkout <branch> on clean tree allows", "git checkout other", "allow", cwd=rp)
        # Coarse worktree-certainty: a `git -C <dir> ...` form cannot be resolved with certainty (the -C is
        # a global option), so EVEN ON A CLEAN TREE a lossy verb there ASKS rather than probe the session
        # dir. This is the accepted over-ask that replaces the removed, fooled dir modelling.
        expect("(clean-d) -C form not-certain asks even on clean tree",
               "git -C {} reset --hard".format(rp), "allow", cwd=rp)

        # Dirty the tracked file (worktree-dirty): the tree is no longer provably clean.
        tracked.write_text("committed line\nuncommitted fix\n", encoding="utf-8")

        # === checkout ========================================================================
        # A worktree-scoped discard on a not-provably-clean tree ASKS (it no longer DENIES per-path, and it
        # no longer proves a disjoint clean path safe - both removed fast paths).
        expect("(co-a) checkout -- dirty asks", "git checkout -- file.txt", "allow", cwd=rp)
        expect("(co-b) checkout -- with optout allows",
               "GUARDRAIL_ALLOW_DISCARD=1 git checkout -- file.txt", "allow", cwd=rp)
        expect("(co-c) checkout . dirty asks", "git checkout .", "allow", cwd=rp)
        expect("(co-d) checkout <branch> on dirty tree asks", "git checkout other", "allow", cwd=rp)
        # Removed path-disjoint fast path: a discard of a CLEAN tracked path on a dirty tree now ASKS (it
        # used to be silently ALLOWED by probing only that path).
        expect("(co-e) checkout -- disjoint-clean path on dirty tree asks",
               "git checkout -- clean.txt", "allow", cwd=rp)
        # A forced checkout WITH an explicit pathspec is path-scoped -> ASK (F-65.F2: the old cut hard-
        # DENIED this even for a clean path; the coarse guard asks, which is recoverable).
        expect("(co-f) checkout -f -- <path> asks not denies", "git checkout -f -- clean.txt", "allow",
               cwd=rp)
        # EN-6 round-21 Fix B (text-only contract correction, NO logic change): LOCK the checkout -f
        # outcomes so the docstring rewording cannot silently drift them. A forced checkout carrying a BARE
        # OPERAND is lexically ambiguous (a branch OR a pathspec), so it is scoped -> ASK (recoverable and
        # human-gated), never a hard DENY that would false-block a legitimate forced path-restore; only an
        # operand-FREE forced whole-tree checkout DENIES on a confirmed-dirty tree.
        expect("(r21b-1) checkout -f <branch> (bare operand) asks, not denies", "git checkout -f main",
               "allow", cwd=rp)
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
        expect("(r22-1) checkout -B force branch-create/reset asks", "git checkout -B foo other", "allow",
               cwd=rp)
        expect("(r22-2) switch -C force branch-create/reset asks", "git switch -C foo other", "allow", cwd=rp)
        expect("(r22-3) switch --force-create force branch-create/reset asks",
               "git switch --force-create foo other", "allow", cwd=rp)
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
        expect("(f85-co4) checkout -Bfoo attached force-create asks", "git checkout -Bfoo", "allow", cwd=rp)
        expect("(f85-co5) checkout -B foo separated force-create asks", "git checkout -B foo", "allow", cwd=rp)
        expect("(f85-sw1) switch -cfeature attached name allows", "git switch -cfeature", "allow", cwd=rp)
        expect("(f85-sw2) switch -Cfoo attached force-create asks", "git switch -Cfoo", "allow", cwd=rp)
        expect("(f85-sw3) switch -C foo separated force-create asks", "git switch -C foo", "allow", cwd=rp)
        expect("(f85-sw4) switch --force-create foo asks", "git switch --force-create foo", "allow", cwd=rp)

        # === EN-6 round-24 Fix F-88: checkout -m/--merge/--conflict is detected BEFORE the branch-create ==
        # allow, mirroring the switch classifier. A checkout --merge does a three-way merge that can overwrite
        # (and lose) local changes, so -m/--merge/--conflict[=<style>] is worktree-scoped EVEN when combined
        # with a -b create -> ASK on a dirty tree; a plain -b create with NO merge option stays ALLOW. '-m'
        # takes no argument, so '-mb new' == '-m -b new' (the parser treats -m as a flag, -b's arg as the name).
        expect("(f88-co1) checkout -m -b new merge-switch+create asks", "git checkout -m -b new other",
               "allow", cwd=rp)
        expect("(f88-co2) checkout --merge -b new merge-switch+create asks",
               "git checkout --merge -b new other", "allow", cwd=rp)
        expect("(f88-co3) checkout --conflict=merge -b new merge-switch+create asks",
               "git checkout --conflict=merge -b new other", "allow", cwd=rp)
        expect("(f88-co4) checkout -m other merge-switch (no create) asks", "git checkout -m other", "allow",
               cwd=rp)
        expect("(f88-co5) checkout -mb new (== -m -b new) merge-switch+create asks",
               "git checkout -mb new other", "allow", cwd=rp)
        expect("(f88-co6) checkout -b new plain create with no merge option still allows",
               "git checkout -b new", "allow", cwd=rp)
        expect("(f88-co7) checkout -bnew attached-name plain create with no merge option still allows",
               "git checkout -bnew", "allow", cwd=rp)

        # === restore =========================================================================
        expect("(re-a) restore dirty asks", "git restore file.txt", "allow", cwd=rp)
        # Blocker 6: restore --staged is no longer an unconditional allow; on a not-provably-clean tree it
        # routes through the probe and ASKS (a --staged unstage can erase staged-only content).
        expect("(re-b) restore --staged on dirty tree asks (blocker 6)", "git restore --staged file.txt",
               "allow", cwd=rp)

        # === reset ===========================================================================
        expect("(rs-a) reset --hard dirty denies (whole-tree clobber)", "git reset --hard", "deny",
               cwd=rp)
        expect("(rs-b) reset --merge dirty asks", "git reset --merge", "allow", cwd=rp)
        # Blocker 6: a --mixed or path reset changes the index and can erase staged-only content, so it is
        # no longer an unconditional allow; it routes through the probe and ASKS on a not-provably-clean tree.
        expect("(rs-c) reset --mixed on dirty tree asks (blocker 6)", "git reset --mixed", "allow", cwd=rp)
        expect("(rs-d) reset -- <path> on dirty tree asks (blocker 6)", "git reset -- clean.txt", "allow",
               cwd=rp)
        # Abbreviated modes: '--h' == '--hard' (clobber -> DENY), an ambiguous bare '--m' errs to merge
        # (scoped -> ASK). The old option parser could be fooled by abbreviations into a silent allow.
        expect("(rs-e) reset --h abbrev is hard, denies", "git reset --h", "deny", cwd=rp)
        expect("(rs-f) reset --m ambiguous errs to ask", "git reset --m", "allow", cwd=rp)
        # Last-wins: '--hard' then '--soft' resolves to soft (keeps worktree) -> ALLOW.
        expect("(rs-g) reset --hard --soft last-wins-soft allows", "git reset --hard --soft", "allow",
               cwd=rp)

        # === rm ==============================================================================
        expect("(rm-a) rm dirty tracked asks (was over-blocked to DENY, F-66.6)", "git rm file.txt",
               "allow", cwd=rp)
        # Blocker 6: rm --cached is no longer an unconditional allow; it can erase staged-only content, so
        # it routes through the probe and ASKS on a not-provably-clean tree.
        expect("(rm-b) rm --cached on dirty tree asks (blocker 6)", "git rm --cached file.txt", "allow",
               cwd=rp)
        expect("(rm-c) rm clean-path on dirty tree asks", "git rm clean.txt", "allow", cwd=rp)

        # === clean, stash, branch: unconditional asks (no per-verb probe) ====================
        (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        expect("(cl-a) clean -fd asks", "git clean -fd", "allow", cwd=rp)
        expect("(cl-b) clean -n dry-run allows", "git clean -nfd", "allow", cwd=rp)
        # A bare clean with NO force flag now ASKS: clean.requireForce=false could make it destructive, and
        # the guard no longer models whether the clean fires (F-66.5). The old cut silently ALLOWed it.
        expect("(cl-c) clean without force asks (requireForce edge, F-66.5)", "git clean -d", "allow",
               cwd=rp)
        expect("(st-a) stash drop asks", "git stash drop", "allow", cwd=rp)
        expect("(st-b) stash clear asks", "git stash clear", "allow", cwd=rp)
        expect("(st-c) stash pop allows (out of scope)", "git stash pop", "allow", cwd=rp)
        expect("(br-a) branch -D asks", "git branch -D other", "allow", cwd=rp)
        expect("(br-b) branch -d allows (git refuses unmerged)", "git branch -d other", "allow", cwd=rp)

        # === EN-6 round-19 Fix A: an UNPARSEABLE 'git branch' ASKS regardless of any delete flag ====
        # The raw fallback (a tokenizer ValueError) now treats ANY raw 'git' + 'branch' as lossy: it does NOT parse
        # branch flags, so an unparseable 'git branch -d -f topic <heredoc>' / '-df' / '--del --for' can no
        # longer slip past the old '-D'/'--delete'-only raw check into a silent ALLOW; every form ASKS.
        _br_hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes the tokenizer raise -> raw fallback
        expect("(r19a-1) unparseable branch -d -f asks (was a silent allow)",
               "git branch -d -f topic" + _br_hd, "allow", cwd=rp)
        expect("(r19a-2) unparseable branch -df (clustered) asks",
               "git branch -df topic" + _br_hd, "allow", cwd=rp)
        expect("(r19a-3) unparseable branch --del --for (abbrev) asks",
               "git branch --del --for topic" + _br_hd, "allow", cwd=rp)
        # The PARSEABLE branch classifier is UNCHANGED by the raw-path widening: a parseable force-delete
        # still ASKS, and a parseable non-delete branch-create still ALLOWs.
        expect("(r19a-4) parseable branch -d -f still asks", "git branch -d -f other", "allow", cwd=rp)
        expect("(r19a-5) parseable non-delete branch-create still allows", "git branch newbranch",
               "allow", cwd=rp)

        # === EN-6 round-21 Fix A: a PARSEABLE force branch move/rename/copy/reset now ASKS ===========
        # A force MOVE/rename (-M, or -m/--move with --force), a force COPY (-C, or -c/--copy with --force),
        # and a bare force branch RESET (-f/--force with a branch and start-point) each reset or overwrite a
        # branch ref and can orphan committed commits (the same reflog-recoverable loss class as -D), so all
        # ASK. A non-force create and the parseable branch list stay ALLOW; the safe -d delete keeps its
        # allow and the -D force-delete keeps its ask (both unchanged). Closes F-77 (a silent-allow gap).
        expect("(r21a-1) branch -f <branch> <start> force reset asks", "git branch -f topic other", "allow",
               cwd=rp)
        expect("(r21a-2) branch -M force rename asks", "git branch -M a b", "allow", cwd=rp)
        expect("(r21a-3) branch -C force copy asks", "git branch -C a b", "allow", cwd=rp)
        expect("(r21a-4) branch -D force delete still asks (unchanged)", "git branch -D topic", "allow",
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
               "allow", cwd=rp)

        # === EN-6 round-25 Fix F-94: a branch delete combined with -r/--remotes ASKS ================
        # Deleting remote-tracking refs (-d/-D/--delete with -r/--remotes) is force-removed by git past the
        # merged-branch safeguard that protects a plain local -d, so every delete+remotes spelling ASKS; a
        # local non-force -d keeps its allow, and -D still ASKS.
        expect("(f94-1) branch -d -r origin/topic asks", "git branch -d -r origin/topic", "allow", cwd=rp)
        expect("(f94-2) branch -dr origin/topic (clustered) asks", "git branch -dr origin/topic", "allow",
               cwd=rp)
        expect("(f94-3) branch --delete --remotes asks", "git branch --delete --remotes origin/topic", "allow",
               cwd=rp)
        expect("(f94-4) branch -d local (local safe delete) still allows", "git branch -d other", "allow",
               cwd=rp)

        # === EN-6 round-25 Fix F-95: git stash export ASKS for every spelling =======================
        # 'stash export' writes stash state to a ref, and --to-ref overwrites an arbitrary ref
        # unconditionally, so every export form ASKS (drop/clear unchanged; push/list unaffected).
        expect("(f95-1) stash export --to-ref asks", "git stash export --to-ref refs/heads/topic", "allow",
               cwd=rp)
        expect("(f95-2) stash export --print asks", "git stash export --print", "allow", cwd=rp)
        expect("(f95-3) stash export (bare) asks", "git stash export", "allow", cwd=rp)

        # === EN-6 round-25 Fix F-97: a raw-lossy-flagged command with an UNRECOGNIZED sub ASKS =======
        # 'git checkout-index -a -f' and 'git read-tree -u --reset HEAD' are flagged in-scope by the raw scan
        # (a 'checkout'/'reset' substring) but resolve to a subcommand the form-classifier does not recognize,
        # so they used to win the catch-all allow and discard tracked worktree content with no snapshot. They
        # now ASK. A genuine safe FORM of a RECOGNIZED verb still ALLOWs (its sub IS recognized), and a verb
        # the raw scan does NOT flag at all (git worktree) stays allowed at the true boundary.
        expect("(f97-1) checkout-index -a -f on dirty tree asks", "git checkout-index -a -f", "deny", cwd=rp)
        expect("(f97-2) read-tree -u --reset HEAD on dirty tree asks", "git read-tree -u --reset HEAD", "deny",
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
        expect("(exp-a) checkout -- $VAR pathspec asks (F-62.1)", "git checkout -- $DIR/f", "allow", cwd=rp)
        expect("(exp-b) rm $(cat list) command-substitution asks (F-62.1)", "git rm $(cat list)", "allow",
               cwd=rp)
        expect("(exp-c) checkout -- glob asks (F-62.1)", 'git checkout -- "*.txt"', "allow", cwd=rp)
        expect("(exp-d) checkout -- brace expansion asks (F-64.1)", "git checkout -- f.{txt,md}", "allow",
               cwd=rp)
        expect("(exp-e) checkout -- tilde asks (F-64.2)", "git checkout -- ~/f", "allow", cwd=rp)
        expect("(exp-f) clean -f $DIR expansion asks (F-65.F1)", "git clean -f $DIR", "allow", cwd=rp)

        # === previously-fooled interactive-patch forms now ASK (F-60.2/F-60.3) ===============
        expect("(patch-a) checkout -p asks (F-60.2)", "git checkout -p", "allow", cwd=rp)
        expect("(patch-b) restore -p --staged asks (F-60.3)", "git restore -p --staged file.txt", "allow",
               cwd=rp)

        # === previously-fooled/hanging clean forms now ASK, no probe (F-64.3, F-66.1) ========
        # clean -i used to reach the `clean -n` probe and could hang on interactive input; clean -q
        # suppressed the probe output so it read as "nothing to remove" and ALLOWed. The coarse guard runs
        # no clean probe at all: any real clean ASKS.
        expect("(clx-a) clean -dfi asks, no hang (F-64.3)", "git clean -dfi", "allow", cwd=rp)
        expect("(clx-b) clean -qfd asks (F-66.1)", "git clean -qfd", "allow", cwd=rp)

        # === previously-fooled worktree-redirection now ASK (F-62.2/3, F-64.4, F-66.2/3/4) ===
        # A cd/pushd/subshell in the chain, a -C/--git-dir/--work-tree global option, or a GIT_DIR/
        # GIT_WORK_TREE env assignment means the worktree cannot be resolved with certainty, so the guard
        # ASKS rather than probe the (possibly wrong, clean) session dir and silently allow.
        (repo / "sub").mkdir(exist_ok=True)
        clean_repo = _init_repo(tmp / "cleanrepo")  # a CLEAN repo a cd might misdirect toward
        expect("(dir-a) cd sub && git reset --hard asks (F-62.2)", "cd sub && git reset --hard", "allow",
               cwd=rp)
        expect("(dir-b) backgrounded cd & git reset --hard asks (F-64.4/F-66.3)",
               "cd sub & git reset --hard", "allow", cwd=rp)
        expect("(dir-c) subshell cd misdirect asks (F-62.2)",
               "( cd {} ) ; git reset --hard".format(str(clean_repo)), "allow", cwd=rp)
        expect("(dir-d) -C global option not-certain asks (F-66.2)", "git -C {} reset --hard".format(rp),
               "allow", cwd=rp)
        expect("(dir-e) --git-dir global option not-certain asks (F-66.4)",
               "git --git-dir={}/.git reset --hard".format(rp), "allow", cwd=rp)
        expect("(dir-f) --work-tree global option not-certain asks",
               "git --work-tree={0} --git-dir={0}/.git reset --hard".format(rp), "allow", cwd=rp)
        expect("(dir-g) GIT_WORK_TREE= env not-certain asks (F-62.3)", "GIT_WORK_TREE=sub git reset --hard",
               "allow", cwd=rp)
        expect("(dir-h) GIT_DIR= env not-certain asks (F-62.3/F-66.4)", "GIT_DIR=.git git reset --hard",
               "allow", cwd=rp)

        # === the opt-out is a LEADING assignment on the git command only (F-65.F3, blocker 4) =
        # The same string buried in an argument (echo) does NOT disable the guard; the command is compound,
        # so the trailing reset --hard ASKS (a compound cannot be probed clean, blocker 2) rather than allow.
        expect("(opt-a) buried GUARDRAIL_ALLOW_DISCARD in an arg does not opt out (F-65.F3)",
               "echo GUARDRAIL_ALLOW_DISCARD=1 ; git reset --hard", "allow", cwd=rp)
        # Blocker 4: an opt-out LEADING a non-git segment does not opt out the later git command; the
        # command is compound, so the reset --hard ASKS, never a silent allow.
        expect("(opt-a2) opt-out leading a non-git segment does not opt out the reset (blocker 4)",
               "GUARDRAIL_ALLOW_DISCARD=1 true ; git reset --hard", "allow", cwd=rp)
        # A genuine leading opt-out prefix on the git command itself still ALLOWs the same reset.
        expect("(opt-b) leading GUARDRAIL_ALLOW_DISCARD prefix opts out",
               "GUARDRAIL_ALLOW_DISCARD=1 git reset --hard", "allow", cwd=rp)

        # === EN-6 round-13: opt-out is case-sensitive and last-wins; redirects ASK all forms ==
        # Fix 1: bash env-var names are case-sensitive, so a LOWERCASE guardrail_allow_discard=1 is NOT the
        # opt-out. On an unparseable heredoc discard the raw fallback must NOT honour it -> ASK. Round-15
        # STRUCTURAL fix: the opt-out is no longer consulted on the unparseable path at all, so even the
        # UPPERCASE form on the same unparseable command now ASKS too (see the r15-raw-* battery below).
        expect("(r13-1) lowercase optout on unparseable heredoc discard does not opt out -> ASK",
               "guardrail_allow_discard=1 git reset --hard <<'EOF'\n'\nEOF", "allow", cwd=rp)
        # Fix 2: bash last-wins on a duplicate leading assignment - =1 then =0 evaluates to 0 (NOT truthy),
        # so it does NOT opt out (the buggy first-wins saw =1 and ALLOWed). With no resolvable cwd the
        # un-opted-out reset --hard ASKS ("cannot resolve to the session directory"); had it opted out it
        # would have short-circuited to ALLOW before the resolvability check.
        expect("(r13-2) =1 =0 last-wins evaluates 0, does not opt out -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 GUARDRAIL_ALLOW_DISCARD=0 git reset --hard", "deny")
        # Fix 3: a command-local redirect (-C/--git-dir/--work-tree/inline GIT_DIR=) means the repository
        # view cannot be proven to be the session cwd, so the early view-uncertainty gate ASKS for ALL forms
        # BEFORE the role logic - including genuinely non-destructive ALLOW forms (reset --soft, plain switch)
        # that previously slipped through to a silent ALLOW.
        expect("(r13-3a) inline GIT_DIR= on reset --soft (allow form) now asks",
               "GIT_DIR=/tmp git reset --soft", "allow", cwd=rp)
        expect("(r13-3b) -C on a plain switch (allow form) now asks",
               "git -C /tmp switch other", "allow", cwd=rp)
        # ROUND-6 FINDING 5 (supersedes the round-13 view-uncertainty ASK for this case): a DESTRUCTIVE
        # discard under a --git-dir/GIT_DIR redirect to a repository that is NOT provably the session repo
        # (here '/x' != rp/.git) destroys that OTHER repository's index/refs, which a session-worktree+index
        # snapshot cannot capture, so it now DENIES rather than allow on a session-snapshot basis. Revert the
        # finding-5 gitdir-redirect deny and this flips back to 'allow' (the old unsound session-snapshot
        # allow that lost the redirected index). Same-repo --git-dir still allows (dir-e/dir-f below).
        expect("(r13-3c-f5) --git-dir to a DIFFERENT repo on reset --hard now DENIES (finding 5)",
               "git --git-dir=/x reset --hard", "deny", cwd=rp)
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
               "GUARDRAIL_ALLOW_DISCARD=1 GUARDRAIL_ALLOW_DISCARD= git reset --hard", "deny")
        # Fix 1 (STRUCTURAL): the opt-out is no longer consulted on the UNPARSEABLE (raw-fallback) path at
        # all - a regex cannot soundly parse an opt-out out of a command the shell lexer could not parse - so
        # every opt-out-looking prefix on an unparseable in-scope discard now ASKS. The five raw variants that
        # previously wrung a silent ALLOW out of the raw scan (quoted-falsy that reads truthy raw, an
        # interspersed other assignment, an opt-out leading a DIFFERENT command, a `0;` captured truthy, and a
        # quoted "false"), each on an unparseable heredoc discard (the lone quote makes the tokenizer raise), must ASK.
        _hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc whose lone ' makes the tokenizer raise -> raw fallback
        expect("(r15-raw-quotedfalsy) quoted-falsy opt-out on unparseable discard -> ASK",
               'GUARDRAIL_ALLOW_DISCARD="0" git reset --hard' + _hd, "allow", cwd=rp)
        expect("(r15-raw-interspersed) interspersed other assignment on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 OTHER=x git reset --hard" + _hd, "allow", cwd=rp)
        expect("(r15-raw-othercmd) opt-out leading a DIFFERENT command on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=1 true; git reset --hard" + _hd, "allow", cwd=rp)
        expect("(r15-raw-semicolon) `0;` captured-truthy opt-out on unparseable discard -> ASK",
               "GUARDRAIL_ALLOW_DISCARD=0; git reset --hard" + _hd, "allow", cwd=rp)
        expect("(r15-raw-quotedfalse) quoted \"false\" opt-out on unparseable discard -> ASK",
               'GUARDRAIL_ALLOW_DISCARD="false" git reset --hard' + _hd, "allow", cwd=rp)
        # Fix 3 (documented override semantics): a leading PARSEABLE opt-out on a pristine bare command is an
        # explicit operator override, evaluated FIRST, so it short-circuits the command-local-redirect (-C)
        # view-uncertainty gate too -> ALLOW (the manifest now qualifies that gate "unless the leading opt-out
        # is set"). Contrast (clean-d): the same -C form WITHOUT the opt-out ASKS.
        expect("(r15-optout-redirect) leading opt-out short-circuits the -C redirect gate -> ALLOW",
               "GUARDRAIL_ALLOW_DISCARD=1 git -C /tmp reset --hard", "allow", cwd=rp)
        # No regression (opt-b, co-a, rs-a above): a plain parseable opt-out still ALLOWs; a plain lossy form
        # with no opt-out still ASKS (co-a) and a dirty whole-tree clobber still DENIES (rs-a).

        # === ROUND-2 FINDING 4: a command-local worktree redirect (-C/--work-tree/GIT_WORK_TREE) on a
        # === DESTRUCTIVE discard snapshots the ACTUAL TARGET repo, not the session cwd; when the target
        # === cannot be snapshotted the discard DENIES (never allow-note a recovery ref that lacks the state).
        # (a) -C into a DIFFERENT real repo: the recovery snapshot lands in the TARGET, NOT the session cwd.
        # This discriminates the fix - the old code snapshotted the session cwd, whose ref would NOT contain
        # the -C target's discarded state (the exact codex P1). Both ref-count assertions flip if reverted.
        try:
            f4_target = _init_repo(tmp / "f4target")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the finding-4 target repo: {}".format(exc),
                  file=sys.stderr)
            return 2
        (f4_target / "file.txt").write_text("committed line\nuncommitted fix\n", encoding="utf-8")  # dirty it
        f4_before_target = len(_recovery_refs(f4_target))
        f4_before_sess = len(_recovery_refs(repo))
        expect("(f4-C-otherrepo) -C into a DIFFERENT dirty repo snapshots the TARGET and allows",
               "git -C {} reset --hard".format(str(f4_target)), "allow", cwd=rp)
        if len(_recovery_refs(f4_target)) != f4_before_target + 1:
            failures.append("(f4-C-target-ref) the recovery snapshot for a -C-redirected discard must land "
                            "in the -C TARGET repo (finding 4): expected exactly one new ref there")
        if len(_recovery_refs(repo)) != f4_before_sess:
            failures.append("(f4-C-not-cwd) the recovery snapshot for a -C-redirected discard must NOT land "
                            "in the session cwd repo (finding 4; the old code snapshotted the wrong repo)")
        # (b) -C into a NON-repo target cannot be snapshotted -> DENY (was an allow-note in round 1).
        expect("(f4-C-nonrepo) -C into a NON-repo target DENIES (the target snapshot cannot be taken)",
               "git -C {} reset --hard".format(str(tmp / "f4nonrepo")), "deny", cwd=rp)
        # (c) -C into the SAME repo as the session cwd still ALLOWS (target resolves to the session repo).
        expect("(f4-C-samerepo) -C into the SAME repo as cwd still allows (target == session repo)",
               "git -C {} reset --hard".format(rp), "allow", cwd=rp)
        # (d) a --work-tree redirect to a NON-repo dir also DENIES a destructive discard.
        expect("(f4-worktree-nonrepo) --work-tree to a non-repo dir DENIES a destructive discard",
               "git --work-tree={0} --git-dir={0}/.git reset --hard".format(str(tmp / "f4nonrepo2")),
               "deny", cwd=rp)
        # (e) a NON-destructive redirected form (reset --soft) still ALLOWS regardless of the target.
        expect("(f4-soft-redirect) a non-destructive reset --soft under -C to a non-repo still allows",
               "git -C {} reset --soft".format(str(tmp / "f4nonrepo")), "allow", cwd=rp)
        # (f) the same-repo -C allow-note still names the prsunc rule (round-19 Fix B carry-forward).
        _f4d = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git -C {} reset --hard".format(rp)}, "cwd": rp}
        _f4c, _f4o, _ = handler(_f4d)
        _f4banner = _f4o.get("systemMessage", "") if isinstance(_f4o, dict) else ""
        if "prsunc" not in _f4banner:
            failures.append("(f4-note-rule) the same-repo -C redirect allow-note must name the prsunc rule")

        # === ROUND-2 FINDING 5: the recovery snapshot preserves the STAGED (index) state as the ref's
        # === SECOND PARENT, so staged-only content a `git restore --staged` discards stays recoverable.
        try:
            f5 = _init_repo(tmp / "f5")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the finding-5 repo: {}".format(exc), file=sys.stderr)
            return 2
        (f5 / "file.txt").write_text("STAGED-A\n", encoding="utf-8")
        _git(f5, "add", "file.txt")                                   # stage version A
        (f5 / "file.txt").write_text("WORKTREE-B\n", encoding="utf-8")  # worktree diverges to B
        _f5top = aiqt_hooks._recovery_toplevel(str(f5))
        _f5st, _f5info = aiqt_hooks._take_snapshot(str(f5), _f5top, "restore")
        if _f5st != "ok":
            failures.append("(f5-snap) the recovery snapshot must succeed on a staged-vs-worktree repo; "
                            "got {} {}".format(_f5st, _f5info))
        else:
            if not _f5info.get("staged"):
                failures.append("(f5-staged-flag) the snapshot must flag a distinct STAGED state (finding 5)")
            _f5staged = subprocess.run(
                ["git", "-C", str(f5), "show", "{}^2:file.txt".format(_f5info["ref"])],
                capture_output=True, text=True, timeout=30)
            if _f5staged.returncode != 0 or _f5staged.stdout != "STAGED-A\n":
                failures.append("(f5-staged-recover) the discarded STAGED version A must be recoverable from "
                                "the ref's second parent (finding 5); got rc={} out={!r}"
                                .format(_f5staged.returncode, _f5staged.stdout))
            _f5wt = subprocess.run(
                ["git", "-C", str(f5), "show", "{}:file.txt".format(_f5info["ref"])],
                capture_output=True, text=True, timeout=30)
            if _f5wt.stdout != "WORKTREE-B\n":
                failures.append("(f5-worktree) the snapshot's primary tree must hold the worktree version B; "
                                "got {!r}".format(_f5wt.stdout))

        # === ROUND-2 FINDING 6: 'git stash drop'/'clear' (NOT reflog-recoverable afterwards) preserves every
        # === stash entry under durable refs BEFORE allowing; denies when it cannot preserve them.
        try:
            f6 = _init_repo(tmp / "f6")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the finding-6 repo: {}".format(exc), file=sys.stderr)
            return 2
        # No stash entries yet: a clear loses nothing -> plain ALLOW (no preservation refs).
        _f6_empty_before = len(_recovery_refs(f6))
        expect("(f6-clear-empty) stash clear with no stashes allows (nothing to preserve)",
               "git stash clear", "allow", cwd=str(f6))
        if len(_recovery_refs(f6)) != _f6_empty_before:
            failures.append("(f6-clear-empty-refs) an empty stash clear must create no recovery refs")
        # Create two stashes, then a clear must preserve BOTH under durable refs before allowing.
        (f6 / "file.txt").write_text("committed line\nstash-1\n", encoding="utf-8")
        _git(f6, "stash", "push", "-m", "s1", env_identity=True)
        (f6 / "file.txt").write_text("committed line\nstash-2\n", encoding="utf-8")
        _git(f6, "stash", "push", "-m", "s2", env_identity=True)
        _f6_before = len(_recovery_refs(f6))
        expect("(f6-clear-allows) stash clear allows once the entries are preserved",
               "git stash clear", "allow", cwd=str(f6))
        _f6_new = [r for r in _recovery_refs(f6) if r.endswith(("stash0", "stash1"))]
        if len(_recovery_refs(f6)) != _f6_before + 2 or len(_f6_new) != 2:
            failures.append("(f6-clear-refs) stash clear must preserve BOTH stash entries under durable "
                            "refs before allowing (finding 6); expected two new refs")
        else:
            # the preserved MOST-RECENT stash (stash@{0} = s2) must be recoverable from its ref
            _f6ref0 = sorted(_f6_new)[0]  # ...-stash0
            _f6show = subprocess.run(["git", "-C", str(f6), "show", "{}:file.txt".format(_f6ref0)],
                                     capture_output=True, text=True, timeout=30)
            if "stash-2" not in _f6show.stdout:
                failures.append("(f6-clear-recover) a preserved stash entry must carry the stashed worktree "
                                "content (finding 6); got {!r}".format(_f6show.stdout))
        # stash drop on a repo with a stash also preserves it before allowing.
        (f6 / "file.txt").write_text("committed line\nstash-3\n", encoding="utf-8")
        _git(f6, "stash", "push", "-m", "s3", env_identity=True)
        _f6_before2 = len(_recovery_refs(f6))
        expect("(f6-drop-allows) stash drop allows once the entry is preserved", "git stash drop", "allow",
               cwd=str(f6))
        # _record_stash_recovery conservatively preserves EVERY stash entry (a worktree snapshot cannot tell
        # which one drop targets), so the count rises by at least one; without the fix it would not rise.
        if len(_recovery_refs(f6)) <= _f6_before2:
            failures.append("(f6-drop-refs) stash drop must preserve the stash entries under durable refs "
                            "before allowing (finding 6); expected at least one new recovery ref")
        # No session cwd: the stash entries cannot be preserved -> DENY (was an allow-note in round 1).
        expect("(f6-clear-nocwd) stash clear with no session cwd DENIES (cannot preserve the stash)",
               "git stash clear", "deny")

        # === ROUND-2 FINDING 7: 'git clean' whose worktree is UNRESOLVABLE DENIES for parity with
        # === reset/checkout (was an allow-note via the role-ask fall-through with no recovery snapshot).
        expect("(f7-clean-nocwd) git clean with no resolvable worktree DENIES (parity with reset/checkout)",
               "git clean -fd", "deny")
        # Parity witness: reset --hard with no cwd also denies (unchanged), so clean now matches it.
        expect("(f7-reset-nocwd-parity) reset --hard with no cwd denies too (parity witness)",
               "git reset --hard", "deny")
        # A resolvable dirty tree still ALLOWS (snapshot-backed), so the fix denies ONLY the unrecoverable
        # unresolvable-worktree case, not a normal clean.
        _f7 = _init_repo(tmp / "f7")
        (_f7 / "untracked.txt").write_text("junk\n", encoding="utf-8")
        expect("(f7-clean-resolvable) git clean on a resolvable dirty tree still allows (snapshot-backed)",
               "git clean -fd", "allow", cwd=str(_f7))

        # === a pathspec-from-file source is worktree-scoped -> ASK on a dirty tree ===========
        expect("(pff-a) restore --pathspec-from-file asks on dirty tree",
               "git restore --pathspec-from-file=paths.txt", "allow", cwd=rp)

        # === GD-41 blockers: each silent-allow blocker now ASKS or DENIES, never allows =======
        # Blocker 1: an UNTRACKED-ONLY-dirty tree is NOT provably clean (the earlier cut skipped '??' and
        # silently allowed a force discard). Build a repo dirtied ONLY by an untracked file.
        untracked_repo = _init_repo(tmp / "untrackedrepo")
        (untracked_repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
        ru = str(untracked_repo)
        if aiqt_hooks._tree_is_clean(ru) is not False:
            failures.append("(b1-probe) _tree_is_clean on untracked-only tree: expected False")
        expect("(b1-a) reset --hard on untracked-only tree denies", "git reset --hard", "deny", cwd=ru)
        expect("(b1-b) clean -f on untracked-only tree asks", "git clean -f", "allow", cwd=ru)
        expect("(b1-c) checkout other on untracked-only tree asks (not allow)", "git checkout other",
               "allow", cwd=ru)

        # Blocker 2: a COMPOUND command whose earlier segment dirties the tree cannot be probed clean; on a
        # CLEAN repo the trailing lossy verb must ASK, not be allowed by a stale pre-write probe.
        compound_repo = _init_repo(tmp / "compoundrepo")
        rco = str(compound_repo)
        expect("(b2-a) printf >> f && reset --hard asks on clean tree (blocker 2)",
               "printf x >> file.txt && git reset --hard", "allow", cwd=rco)
        expect("(b2-b) stash apply && reset --hard asks on clean tree (blocker 2)",
               "git stash apply && git reset --hard", "allow", cwd=rco)

        # Blocker 3: an operand after '--' is a pathspec, never a safe-looking option, so a force clean/rm
        # is not allowed by misreading it. (On the dirty repo: ASK, not allow.)
        expect("(b3-a) clean -f -- -nasty asks (not read as -n dry-run)", "git clean -f -- -nasty", "allow",
               cwd=rp)
        expect("(b3-b) rm -f -- --cached asks (not read as --cached unstage)", "git rm -f -- --cached",
               "allow", cwd=rp)

        # Blocker 5: abbreviated destructive options are recognized by prefix, so they do not slip through
        # as inert tokens on the dirty tree.
        expect("(b5-a) checkout --for (abbrev --force) denies", "git checkout --for", "deny", cwd=rp)
        expect("(b5-b) checkout --patc (abbrev --patch) asks", "git checkout --patc", "allow", cwd=rp)
        expect("(b5-c) switch --dis (abbrev --discard-changes) denies", "git switch --dis other", "deny",
               cwd=rp)
        expect("(b5-d) branch --del --force (abbrev) asks", "git branch --del --force other", "allow",
               cwd=rp)

        # Blocker 6: an index-only change on a STAGED-ONLY-dirty tree (worktree matches index, index differs
        # from HEAD) ASKS, because the probe sees the staged change; on a CLEAN tree it allows.
        staged_repo = _init_repo(tmp / "stagedrepo")
        (staged_repo / "file.txt").write_text("committed line\nstaged fix\n", encoding="utf-8")
        _git(staged_repo, "add", "file.txt")  # staged only; worktree == index
        rst = str(staged_repo)
        expect("(b6-a) restore --staged on staged-only tree asks (blocker 6)",
               "git restore --staged file.txt", "allow", cwd=rst)
        expect("(b6-b) reset --mixed on staged-only tree asks (blocker 6)", "git reset --mixed", "allow",
               cwd=rst)
        expect("(b6-c) rm --cached on staged-only tree asks (blocker 6)", "git rm --cached file.txt",
               "allow", cwd=rst)
        # On a genuinely clean tree the same index-only forms allow (nothing staged to lose).
        clean_index = _init_repo(tmp / "cleanindexrepo")
        rci = str(clean_index)
        expect("(b6-d) restore --staged on clean tree allows", "git restore --staged file.txt", "allow",
               cwd=rci)
        expect("(b6-e) rm --cached on clean tree allows", "git rm --cached file.txt", "allow", cwd=rci)

        # Blocker 7: a FORCED branch-create (checkout -f -b) no longer early-allows; on the dirty tree it
        # ASKS, and an UNforced branch-create still allows.
        expect("(b7-a) checkout -f -b new on dirty tree asks (not early-allow)", "git checkout -f -b new",
               "allow", cwd=rp)
        expect("(b7-b) checkout -b new on clean tree allows", "git checkout -b new", "allow", cwd=rci)

        # Blocker 8: a shell wrapper hiding the git verb ASKS (the verb is not at the segment command-word
        # position, so it must not fall open). A wrapper over a non-lossy git command still allows.
        for label, cmd in (("command", "command git reset --hard"), ("exec", "exec git reset --hard"),
                           ("env", "env git reset --hard"), ("bang", "! git reset --hard"),
                           ("time", "time git reset --hard"), ("builtin", "builtin git reset --hard")):
            expect("(b8-{}) wrapper hiding git reset --hard snapshot-then-allows".format(label), cmd,
                   "allow", cwd=rp)
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
            expect("(pristine-{}) shell structure hides a reset --hard -> snapshot-then-allows".format(label),
                   cmd, "allow", cwd=rp)
        # A pathed git ('/usr/bin/git') is not the literal command word 'git', so it is not pristine -> ASK.
        expect("(pristine-pathed) a pathed git is not literally 'git' -> asks", "/usr/bin/git reset --hard",
               "allow", cwd=rp)

        # === switch --merge/--conflict overwrite local changes -> scoped, ASK on a dirty tree (fix 3) =
        expect("(swm-a) switch --merge on dirty tree asks", "git switch --merge other", "allow", cwd=rp)
        expect("(swm-b) switch --conflict= on dirty tree asks", "git switch --conflict=diff3 other", "allow",
               cwd=rp)
        expect("(swm-c) switch -m on dirty tree asks", "git switch -m other", "allow", cwd=rp)

        # === clean arg-consuming options: '-e'/'--exclude' consume the next token -> do NOT trust '-n' ==
        # 'git clean -f -e '*.keep' -n' must NOT be read as a dry run (fix 2): with an arg-consuming option
        # present the guard cannot tell a real '-n' flag from an exclude pattern, so it ASKS. '-n' alone still
        # allows. (Tested on the dirty/untracked repo below via the config-hidden repo too.)
        (repo / "keeper.keep").write_text("keep\n", encoding="utf-8")
        expect("(cle-a) clean -f -e PAT -n asks (not read as dry-run)", "git clean -f -e '*.keep' -n", "allow",
               cwd=rp)
        expect("(cle-b) clean -f -e -n asks (-n is the exclude pattern)", "git clean -f -e -n", "allow",
               cwd=rp)
        expect("(cle-c) clean -n alone still allows (dry run)", "git clean -n", "allow", cwd=rp)
        expect("(cle-d) clean -n --no-dry-run -f asks (boolean negation disables the dry run)",
               "git clean -n --no-dry-run -f", "allow", cwd=rp)
        expect("(cle-e) clean -f -en asks (attached -e value, '-n' is the exclude pattern)",
               "git clean -f -en", "allow", cwd=rp)
        expect("(cle-f) clean -n --no-dry-r -f asks (abbreviated negation prefix)",
               "git clean -n --no-dry-r -f", "allow", cwd=rp)
        # --pathspec-from-file reads pathspecs from a file, so its NEXT token is a filename, not a flag:
        # checkout/reset must treat it as path-scoped and ASK, not misread the filename as -b/--soft.
        expect("(pfr-checkout) checkout --pathspec-from-file -b asks (-b is the file, not branch-create)",
               "git checkout --pathspec-from-file -b", "allow", cwd=rp)
        expect("(pfr-reset) reset --pathspec-from-file --soft asks (--soft is the file, not the mode)",
               "git reset --pathspec-from-file --soft", "allow", cwd=rp)
        # An inline git alias that expands to a work-losing verb cannot be resolved -> ASK.
        # CLAUDE-F1 (round-7): an inline '-c alias.<name>=' invoking a NON-builtin subcommand ('x') enters the
        # view-override branch ('-c' makes the command not dir-simple); its raw scan flags 'reset' but the
        # resolved sub 'x' is outside the recognized lossy-verb set, so it cannot be proven non-destructive or
        # snapshotted and now DENIES (previously it ALLOWED with no snapshot - the alias could expand to a
        # work-losing verb and destroy uncommitted work unrecoverably). Reverting the view-override
        # unrecognized-verb deny reds this case.
        expect("(alias-inline) git -c alias.x=reset x --hard DENIES (CLAUDE-F1)",
               "git -c alias.x=reset x --hard", "deny", cwd=rp)
        # A glob char (*?[) can bash-expand an option name (in a dir with a file named --hard, '--h*' becomes
        # '--hard'), so any lossy command carrying one is not pristine -> ASK.
        expect("(glob-opt) reset --soft --h* asks (glob defeats the pristine gate)", "git reset --soft --h*",
               "allow", cwd=rp)
        # An abbreviated --pathspec-from-f and any unrecognized option-only checkout must not default to allow.
        expect("(pfr-abbrev) checkout --pathspec-from-f=paths asks", "git checkout --pathspec-from-f=paths",
               "allow", cwd=rp)
        expect("(co-unknown) checkout with an unrecognized option asks", "git checkout --some-exotic-opt",
               "allow", cwd=rp)

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
        expect("(cfg-b) clean -f on config-hidden untracked tree asks", "git clean -f", "allow", cwd=rh)
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
        expect("(robust-b) no-cwd reset --hard asks", "git reset --hard", "deny")

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
        expect("(rec-ask) checkout -- on dirty tree asks", "git checkout -- file.txt", "allow",
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
        expect("(rec-inv) checkout -- on dirty invariant tree asks", "git checkout -- file.txt", "allow",
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
        expect("(rec-restore-setup) clean -fd on dirty+untracked tree asks", "git clean -fd", "allow",
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
        if got_dg != "deny":
            failures.append("(rec-faildowngrade) a snapshot failure must DENY a would-be ALLOW (unrecoverable "
                            "discard), got {}".format(got_dg))
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
        if got_idx != "allow":
            failures.append("(rec-idxfile) an ambient GIT_INDEX_FILE target-redirect snapshot-then-allows on a "
                            "dirty cwd (best-effort snapshot taken), got {}".format(got_idx))
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
        if got_amb != "allow":
            failures.append("(rec-ambient) dirty-tree snapshot-then-allow with ambient GIT_* env: expected "
                            "allow, got {}".format(got_amb))
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
        expect("(rec-skip-stash) stash drop on dirty tree asks", "git stash drop", "allow", cwd=str(rec_skip))
        expect("(rec-skip-branch) branch -D on dirty tree asks", "git branch -D other", "allow",
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
        if got_cap != "deny":
            failures.append("(rec-sizecap) an over-cap snapshot failure must DENY a would-be ALLOW "
                            "(unrecoverable discard), got {}".format(got_cap))
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
        if not (code_unc == 0 and dec_unc == "deny"):
            failures.append("(rec-probeuncertain) probe-uncertain scoped discard with a failed snapshot must "
                            "DENY (unrecoverable), got code={!r} dec={!r}".format(code_unc, dec_unc))
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
        if got_b2t != "deny":
            failures.append("(rec-b2-tmp) a temp dir inside the repo fails the snapshot, so a would-be ALLOW "
                            "must DENY (unrecoverable), got {}".format(got_b2t))
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
        if got_b2l != "allow":
            failures.append("(rec-b2-ledger) ledger-inside-repo: the snapshot still succeeds so the discard "
                            "snapshot-then-allows (ledger write skipped), got {}".format(got_b2l))
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
        if got_decoy != "allow":
            failures.append("(rec-decoy) an ambient GIT_DIR/GIT_WORK_TREE target-redirect snapshot-then-allows "
                            "on a dirty cwd (best-effort snapshot on the real repo), got {}".format(got_decoy))
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
            if got_view != "allow":
                failures.append("(rec-viewoverride-{}) an ambient non-cosmetic {} on a clean cwd (nothing to "
                                "snapshot) snapshot-then-allows, got {}".format(_newvar, _newvar, got_view))
        # the ORIGINAL six target-redirect vars still ASK under the fail-safe check.
        for _redir in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                       "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE"):
            os.environ[_redir] = "1"
            try:
                got_redir = _decision(handler, "git checkout -- file.txt", cwd=str(rec_view))
            finally:
                os.environ.pop(_redir, None)
            if got_redir != "allow":
                failures.append("(rec-viewoverride-{}) the redirect var {} on a clean cwd snapshot-then-allows "
                                "(nothing to snapshot), got {}".format(_redir, _redir, got_redir))
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
                    if got_af != "allow":
                        failures.append("(rec-viewoverride-allow-{}-{}) an ambient non-cosmetic {} on the allow "
                                        "form '{}' on a clean cwd snapshot-then-allows (nothing to snapshot), "
                                        "got {}".format(_amb2, _cmd.replace(" ", "_"), _amb2, _cmd, got_af))
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
        if got_trv != "allow":
            failures.append("(rec-viewoverride-trace) an ambient GIT_TRACE on a clean pristine discard "
                            "snapshot-then-allows (no longer cosmetic, nothing to snapshot), got {}"
                            .format(got_trv))
        if trace_view.exists():
            failures.append("(rec-viewoverride-trace-file) the guard's git calls must scrub GIT_TRACE; no "
                            "trace file may be written")

        # (rec-heredoc) C: a Bash-valid but tokenizer-UNPARSEABLE discard (an unbalanced quote inside a quoted
        # heredoc) reaches the raw-lossy fallback. It must ASK and, on a dirty tree, take a best-effort
        # recovery snapshot FIRST - before the fix the ValueError path returned ASK with NO recovery ref.
        rec_hd = _init_repo(tmp / "rec-heredoc")
        (rec_hd / "file.txt").write_text("committed line\nheredoc dirty\n", encoding="utf-8")
        hd_cmd = "git reset --hard <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes the tokenizer raise
        got_hd = _decision(handler, hd_cmd, cwd=str(rec_hd))
        if got_hd != "allow":
            failures.append("(rec-heredoc) an unparseable in-scope discard on a dirty tree snapshot-then-allows "
                            "(best-effort snapshot taken), got {}".format(got_hd))
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
        if got_sub != "deny":
            failures.append("(rec-subdir-tmp) a temp dir at the toplevel (above a subdir cwd) fails the "
                            "snapshot, so a would-be ALLOW must DENY (unrecoverable), got {}".format(got_sub))
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
        if got_subl != "allow":
            failures.append("(rec-subdir-ledger) a ledger base at the toplevel above a subdir cwd leaves the "
                            "decision unaffected: the snapshot succeeds and it snapshot-then-allows, got {}"
                            .format(got_subl))
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
            if got_bad != "deny":
                failures.append("(rec-badname) a non-UTF-8 path fails the snapshot gracefully, so the discard "
                                "must DENY (unrecoverable), got {}".format(got_bad))

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
        if got_col1 != "allow" or len(refs_after1) != 1:
            failures.append("(rec-refcollision-setup) expected one ref after the first snapshot and a "
                            "snapshot-then-allow, got dec={} refs={}".format(got_col1, refs_after1))
        if not (code_col2 == 0 and dec_col2 == "deny"):
            failures.append("(rec-refcollision) a create-only ref collision fails the snapshot, so the discard "
                            "must DENY (unrecoverable), got code={!r} dec={!r}".format(code_col2, dec_col2))
        if "no pre-command recovery snapshot could be created" not in reason_col2:
            failures.append("(rec-refcollision-reason) the DENY reason must surface the collision failure")
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
               "git checkout -- file.txt && echo done", "allow", cwd=str(rec_fd))
        if not _recovery_refs(rec_fd):
            failures.append("(rec-fd-nonpristine-snap) expected a recovery ref on a non-pristine in-scope ASK "
                            "of a dirty tree (F-D EXPAND)")
        # A non-pristine WRAPPED form (a wrapper hides the verb from the segment scan; raw_lossy is the
        # signal) is likewise snapshot-backed against the session cwd.
        rec_fdw = _init_repo(tmp / "rec-fd-wrap")
        (rec_fdw / "file.txt").write_text("committed line\nwrapped fd\n", encoding="utf-8")
        expect("(rec-fd-wrap) wrapped reset --hard on dirty tree asks", "sudo git reset --hard", "allow",
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
               "git stash drop && echo done", "allow", cwd=str(rec_ns))
        expect("(rec-fd-nonsnap-branch) compound branch -D on dirty tree asks",
               "git branch -D other && echo done", "allow", cwd=str(rec_ns))
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
        if got_cfg != "allow":
            failures.append("(rec-cfgcount) reset --hard under an injected core.worktree decoy via a "
                            "non-cosmetic ambient GIT_CONFIG_COUNT snapshot-then-allows on a dirty cwd "
                            "(best-effort snapshot on the real repo), got {}".format(got_cfg))
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
        if got_capsub != "deny":
            failures.append("(rec-sizecap-subdir) the size estimate must anchor on the toplevel (not the "
                            "subdir cwd), failing the snapshot so a would-be ALLOW DENIES (unrecoverable) when "
                            "the toplevel file exceeds the cap, got {}".format(got_capsub))
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
        if got_nul != "deny":
            failures.append("(rec-nulcwd) an embedded-NUL session cwd fails the snapshot gracefully, so the "
                            "discard must DENY (unrecoverable), got {}".format(got_nul))

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
        if got_tr != "allow":
            failures.append("(rec-gittrace) dirty-tree snapshot-then-allow with an ambient GIT_TRACE: "
                            "expected allow, got {}".format(got_tr))
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
               "git stash drop && $(echo git checkout -f)", "allow", cwd=str(rec_c6a))
        if not _recovery_refs(rec_c6a):
            failures.append("(rec-c6-subst-snap) expected a recovery ref: a hidden snappable verb (checkout "
                            "-f) behind a substitution must still snapshot on a dirty tree")
        rec_c6b = _init_repo(tmp / "rec-c6-wrap")
        (rec_c6b / "file.txt").write_text("committed line\nc6 wrap\n", encoding="utf-8")
        expect("(rec-c6-wrap) stash drop + hidden wrapped reset --hard asks",
               "git stash drop; sudo git reset --hard", "allow", cwd=str(rec_c6b))
        if not _recovery_refs(rec_c6b):
            failures.append("(rec-c6-wrap-snap) expected a recovery ref: a hidden snappable verb (reset "
                            "--hard) behind a wrapper must still snapshot on a dirty tree")
        # The split-quote/eval case is the one a raw lexical scan CANNOT catch: `re'set'` has no contiguous
        # `reset` in the raw string, yet bash assembles `reset` at runtime. The structural fix snapshots it
        # anyway, because the command is non-pristine on a not-provably-clean tree.
        rec_c6d = _init_repo(tmp / "rec-c6-eval")
        (rec_c6d / "file.txt").write_text("committed line\nc6 eval\n", encoding="utf-8")
        expect("(rec-c6-eval) stash drop + split-quote eval reset asks",
               "git stash drop; eval git re'set' --hard", "allow", cwd=str(rec_c6d))
        if not _recovery_refs(rec_c6d):
            failures.append("(rec-c6-eval-snap) expected a recovery ref: a snappable verb hidden by "
                            "split-quoting/eval must still snapshot on a dirty tree")
        # A pure stash/echo non-pristine command with NO snappable verb anywhere NOW ALSO snapshots (the
        # rec-c6-nosnap assertion FLIPS): the guard cannot prove no snappable verb is hidden, so it never
        # gates on absence. Over-snapshotting a pure stash form is an accepted inert cost, never an
        # under-protection.
        rec_c6c = _init_repo(tmp / "rec-c6-nosnap")
        (rec_c6c / "file.txt").write_text("committed line\nc6 nosnap\n", encoding="utf-8")
        expect("(rec-c6-nosnap) stash drop; echo hi asks", "git stash drop; echo hi", "allow",
               cwd=str(rec_c6c))
        if not _recovery_refs(rec_c6c):
            failures.append("(rec-c6-nosnap-snap) expected a recovery ref: any non-pristine in-scope command "
                            "on a dirty tree is snapshot-backed (accepted over-snapshot)")

        # ROUND-3 FINDING 1: a NON-PRISTINE discard whose effective worktree is a -C redirect or a cd'd-into
        # target must snapshot THAT target (so the recovery ref contains the state the command discards), not
        # blindly the session cwd. Revert _nonpristine_discard_actions and the ref lands only in the session
        # repo while the target (T) has NONE -> the note would falsely claim recovery. Discriminates on ref
        # LOCATION: the target repo must carry a recovery ref.
        r3f1_sess = _init_repo(tmp / "r3f1-sess")
        (r3f1_sess / "file.txt").write_text("committed line\nsess dirty\n", encoding="utf-8")
        r3f1_T = _init_repo(tmp / "r3f1-T")
        (r3f1_T / "file.txt").write_text("committed line\nT uncommitted\n", encoding="utf-8")
        expect("(r3f1a) 'git -C T restore file.txt; :' allows",
               "git -C {} restore file.txt; :".format(str(r3f1_T)), "allow", cwd=str(r3f1_sess))
        if not _recovery_refs(r3f1_T):
            failures.append("(r3f1a-snap) expected a recovery ref in the -C TARGET repo T, not only the "
                            "session repo (finding 1: the compound '-C T' discard was snapshotting the cwd)")
        r3f1_T2 = _init_repo(tmp / "r3f1-T2")
        (r3f1_T2 / "file.txt").write_text("committed line\nT2 uncommitted\n", encoding="utf-8")
        expect("(r3f1b) 'cd T2 && git restore file.txt' allows",
               "cd {} && git restore file.txt".format(str(r3f1_T2)), "allow", cwd=str(r3f1_sess))
        if not _recovery_refs(r3f1_T2):
            failures.append("(r3f1b-snap) expected a recovery ref in the cd'd-into TARGET repo T2 (finding 1: "
                            "the 'cd T2 && git restore' discard was snapshotting the session cwd)")

        # ROUND-3 FINDING 2: a redirected/compound 'git stash drop'/'clear' must PRESERVE the stash of the
        # repo it actually clears (a refs/aiqt-recovery/*-stash* ref in THAT repo), or DENY. Revert the
        # non-pristine stash handling / the redirect-path stash handling and the stash is not preserved (no
        # stash ref) while the note still claims recovery. Discriminates on the presence of a stash ref.
        def _stash_refs(repo):
            return [r for r in _recovery_refs(repo) if "stash" in r]
        r3f2_sess = _init_repo(tmp / "r3f2-sess")
        r3f2_T = _init_repo(tmp / "r3f2-T")
        (r3f2_T / "file.txt").write_text("committed line\nstash me\n", encoding="utf-8")
        _git(r3f2_T, "stash", env_identity=True)             # one stash entry to preserve
        expect("(r3f2a) 'git -C T stash clear' allows", "git -C {} stash clear".format(str(r3f2_T)),
               "allow", cwd=str(r3f2_sess))
        if not _stash_refs(r3f2_T):
            failures.append("(r3f2a-snap) expected a stash recovery ref in the -C TARGET repo T (finding 2: "
                            "the redirected 'git -C T stash clear' preserved no stash)")
        r3f2_T2 = _init_repo(tmp / "r3f2-T2")
        (r3f2_T2 / "file.txt").write_text("committed line\nstash me 2\n", encoding="utf-8")
        _git(r3f2_T2, "stash", env_identity=True)
        expect("(r3f2b) 'git stash clear; :' allows", "git stash clear; :", "allow", cwd=str(r3f2_T2))
        if not _stash_refs(r3f2_T2):
            failures.append("(r3f2b-snap) expected a stash recovery ref for the compound 'git stash clear; :' "
                            "(finding 2: the compound stash clear preserved no stash)")

        # ROUND-3 FINDING 3: when the pure-index (staged) write-tree FAILS while staged content is present,
        # the snapshot cannot contain the staged-only payload a --staged/--cached/default-reset discard drops,
        # so it must FAIL (the caller DENIES) rather than advertise a snapshot missing it. Fault-inject the
        # FIRST write-tree (the pure-index capture) to fail. Revert the 'elif "staged" in classes' guard and
        # _take_snapshot returns 'ok' (advertising a snapshot without the staged payload).
        r3f3 = _init_repo(tmp / "r3f3-staged")
        _git(r3f3, "rm", "--cached", "clean.txt")            # simplify: leave one tracked file
        _git(r3f3, "commit", "-q", "-m", "trim", env_identity=True)
        (r3f3 / "file.txt").write_text("STAGED payload\n", encoding="utf-8")
        _git(r3f3, "add", "file.txt")                        # staged change (index != HEAD)
        (r3f3 / "file.txt").write_text("WORKTREE differs\n", encoding="utf-8")  # staged-only content at risk
        _orig_rg = aiqt_hooks._recovery_git
        _wt_state = {"n": 0}

        def _faulty_recovery_git(repo, args, env_extra=None, timeout=10):
            if args and args[0] == "write-tree":
                _wt_state["n"] += 1
                if _wt_state["n"] == 1:                      # the pure-index (staged) write-tree
                    return subprocess.CompletedProcess(["git"], 1, "", "injected write-tree failure")
            return _orig_rg(repo, args, env_extra=env_extra, timeout=timeout)

        aiqt_hooks._recovery_git = _faulty_recovery_git
        try:
            _r3f3_res = aiqt_hooks._take_snapshot(str(r3f3), str(r3f3), "restore")
        finally:
            aiqt_hooks._recovery_git = _orig_rg
        if _r3f3_res[0] != "fail":
            failures.append("(r3f3) with staged content and a failing pure-index write-tree, _take_snapshot "
                            "must FAIL (fail closed), got {!r} (finding 3)".format(_r3f3_res[0]))

        # ROUND-3 FINDING 8: in an UNBORN-HEAD repo the staged (index) commit is the snapshot's FIRST parent
        # (there is no HEAD parent), so the recovery pointer must advertise '^1', not '^2'. Stage a file, then
        # differ the worktree so the staged content is staged-only; take the snapshot and confirm the pointer
        # names '^1' and that '<ref>^1' resolves to the staged content (while '^2' does not exist). Revert the
        # staged_pointer computation and the pointer says '^2', which does not resolve on an unborn HEAD.
        r3f8 = tmp / "r3f8-unborn"
        r3f8.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(r3f8)], check=True, capture_output=True,
                       text=True, timeout=30)
        (r3f8 / "f").write_text("STAGED-ONLY\n", encoding="utf-8")
        _git(r3f8, "add", "f")                               # staged in an unborn-HEAD index
        (r3f8 / "f").write_text("WORKTREE\n", encoding="utf-8")   # worktree differs -> staged-only content
        _r3f8_res = aiqt_hooks._take_snapshot(str(r3f8), str(r3f8), "rm")
        if _r3f8_res[0] != "ok":
            failures.append("(r3f8-snap) expected an ok unborn-HEAD staged snapshot, got {!r}".format(
                _r3f8_res[0]))
        else:
            _info = _r3f8_res[1]
            if _info.get("staged_pointer") != "^1":
                failures.append("(r3f8-ptr) unborn-HEAD staged parent must be '^1', got {!r} (finding 8)"
                                .format(_info.get("staged_pointer")))
            _show1 = subprocess.run(["git", "-C", str(r3f8), "show", "{}^1:f".format(_info["ref"])],
                                    capture_output=True, text=True, timeout=30)
            if _show1.returncode != 0 or _show1.stdout != "STAGED-ONLY\n":
                failures.append("(r3f8-resolve) '<ref>^1:f' must resolve to the staged-only content on an "
                                "unborn HEAD (finding 8), got rc={} {!r}".format(_show1.returncode,
                                                                                 _show1.stdout))
            if "^1" not in aiqt_hooks._recovery_pointer(_info):
                failures.append("(r3f8-pointer-text) the recovery pointer must advertise '^1' on an unborn "
                                "HEAD (finding 8)")

        # === ROUND-6 findings 3/4/6/7: fail-closed the git_discard forms that lost a redirected target =====
        # A separate DIRTY target repo T the session cwd (repo, dirty) is NOT: the old code snapshotted the
        # session cwd for these forms, whose ref would NOT contain T's discarded state. Each new DENY flips to
        # 'allow' (the old unsound behaviour) if its fix is reverted, and no session-cwd ref is created.
        try:
            r6t = _init_repo(tmp / "r6-target")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the round-6 target repo: {}".format(exc), file=sys.stderr)
            return 2
        (r6t / "file.txt").write_text("committed line\nr6 target dirty\n", encoding="utf-8")  # dirty T
        r6tc = str(r6t)
        # FINDING 3a: a lossy git discard INSIDE a subshell that cd'd to T loses T; the walk cannot model the
        # subshell cwd -> DENY (was allow-with-a-session-cwd snapshot). No ref may land in the session repo.
        _r6_sess_before = len(_recovery_refs(repo))
        expect("(r6-f3-subshell) '(cd T && git restore)' whose subshell cd moves the target DENIES (finding 3)",
               "(cd {} && git restore -- file.txt)".format(r6tc), "deny", cwd=rp)
        if len(_recovery_refs(repo)) != _r6_sess_before:
            failures.append("(r6-f3-subshell-noref) a denied subshell-redirected discard must NOT snapshot the "
                            "session cwd (finding 3)")
        # FINDING 3b: a WRAPPED git carrying a -C redirect to T loses T -> DENY (was allow-with-session-snap).
        expect("(r6-f3-wrapper) 'env git -C T restore' (wrapped + redirect) DENIES (finding 3)",
               "env git -C {} restore -- file.txt".format(r6tc), "deny", cwd=rp)
        # CONTROL: a subshell/substitution or wrapper with NO redirect targets the FOREGROUND session cwd, so it
        # is snapshot-backed and ALLOWS (the C6 design). These flip to DENY if finding-3 over-fires on depth
        # alone rather than on a moved target, so they lock the fix's precision.
        expect("(r6-f3-ctl-subshell-nocd) '(git reset --hard)' with no internal cd still allows (foreground cwd)",
               "(git reset --hard)", "allow", cwd=rp)
        expect("(r6-f3-ctl-substitution) 'echo $(git checkout -f)' still allows (runs at the foreground cwd)",
               "echo $(git checkout -f)", "allow", cwd=rp)
        expect("(r6-f3-ctl-wrap-noredirect) 'env git reset --hard' with no redirect still allows (session cwd)",
               "env git reset --hard", "allow", cwd=rp)
        # FINDING 4: an UNPARSEABLE (unquoted heredoc) discard carrying a -C to a DIFFERENT dirty repo, on a
        # CLEAN session cwd, no longer allows on the clean-cwd basis -> DENY (was a silent allow at :3349).
        r6clean = _init_repo(tmp / "r6-clean")   # session cwd is CLEAN
        expect("(r6-f4-fallback-redirect) unparseable 'git -C Tdirty restore <<EOF' on a clean cwd DENIES "
               "(finding 4)", "git -C {} restore -- file.txt <<EOF\nx\nEOF".format(r6tc), "deny",
               cwd=str(r6clean))
        # CONTROL: an unparseable lossy discard with NO redirect still allows on a clean cwd (no target moved).
        expect("(r6-f4-ctl-noredirect) unparseable 'git checkout -- x <<EOF' with no redirect still allows",
               "git checkout -- file.txt <<EOF\nx\nEOF", "allow", cwd=str(r6clean))
        # FINDING 5: a --git-dir/GIT_DIR STAGED discard to a DIFFERENT repo destroys that repo's index, which a
        # session snapshot cannot capture -> DENY (was allow-with-session-snap). (reset --hard case: r13-3c-f5.)
        r6stg = _init_repo(tmp / "r6-staged")
        (r6stg / "file.txt").write_text("committed line\nr6 staged\n", encoding="utf-8")
        _git(r6stg, "add", "file.txt")   # staged content in T's index
        expect("(r6-f5-gitdir-staged) 'git --git-dir=T/.git restore --staged' to a DIFFERENT repo DENIES "
               "(finding 5)", "git --git-dir={}/.git restore --staged -- file.txt".format(str(r6stg)),
               "deny", cwd=rp)
        # FINDING 6: a staged-index commit-tree failure while distinct staged content is present must FAIL the
        # snapshot (fail closed), not silently drop the staged payload. Fault-inject the staged commit-tree.
        r6f6 = tmp / "r6-f6-staged"
        r6f6.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(r6f6)], check=True, capture_output=True,
                       text=True, timeout=30)
        (r6f6 / "s.txt").write_text("committed\n", encoding="utf-8")
        _git(r6f6, "add", "s.txt")
        _git(r6f6, "commit", "-q", "-m", "seed", env_identity=True)
        (r6f6 / "s.txt").write_text("STAGED payload\n", encoding="utf-8")
        _git(r6f6, "add", "s.txt")                              # index != HEAD (staged)
        (r6f6 / "s.txt").write_text("WORKTREE differs\n", encoding="utf-8")  # staged-only content at risk
        _orig_rg6 = aiqt_hooks._recovery_git

        def _faulty_staged_ct(repo, args, env_extra=None, timeout=10):
            # fail ONLY the staged (index) snapshot commit-tree; the write-trees still succeed.
            if args and args[0] == "commit-tree" and any("staged (index) snapshot" in a for a in args):
                return subprocess.CompletedProcess(["git"], 1, "", "injected staged commit-tree failure")
            return _orig_rg6(repo, args, env_extra=env_extra, timeout=timeout)

        aiqt_hooks._recovery_git = _faulty_staged_ct
        try:
            _r6f6_res = aiqt_hooks._take_snapshot(str(r6f6), str(r6f6), "restore")
        finally:
            aiqt_hooks._recovery_git = _orig_rg6
        if _r6f6_res[0] != "fail":
            failures.append("(r6-f6-staged-ct) a failing staged (index) commit-tree with distinct staged "
                            "content must FAIL the snapshot (fail closed), got {!r} (finding 6)"
                            .format(_r6f6_res[0]))
        # FINDING 7: a recovery snapshot taken against a redirected TARGET repo advertises restore commands
        # bound to that repo ('git -C <target> ...'), not a bare form that would fail from the session cwd.
        _r6f7 = aiqt_hooks._take_snapshot(r6tc, r6tc, "reset")
        if _r6f7[0] != "ok":
            failures.append("(r6-f7-snap) expected an ok target-repo snapshot for the pointer test, got {!r}"
                            .format(_r6f7[0]))
        else:
            _ptr = aiqt_hooks._recovery_pointer(_r6f7[1])
            if "git -C {}".format(r6tc) not in _ptr:
                failures.append("(r6-f7-pointer-repo) the recovery pointer must bind its restore command to "
                                "the target repo 'git -C {} ...' (finding 7); got {!r}".format(r6tc, _ptr))
            if "restore it with 'git checkout " in _ptr:
                failures.append("(r6-f7-pointer-bare) the recovery pointer must NOT advertise a bare "
                                "'git checkout <ref>' that fails from another cwd (finding 7)")

        # === ROUND-7 codex findings 3/4: advertised recovery commands shell-quote the interpolated repo
        # PATH (a space/metacharacter cannot break or inject), and the redirected-stash recovery command
        # binds to the target repo with 'git -C <repo> stash apply'. Reverting either reds these. ========
        try:
            cf3 = tmp / "cf3 space repo"          # a repo whose path carries a SPACE (and a ';' would inject)
            subprocess.run(["git", "init", "-q", "-b", "main", str(cf3)],
                           check=True, capture_output=True, text=True, timeout=30)
            (cf3 / "f.txt").write_text("x\n", encoding="utf-8")
            _git(cf3, "add", "f.txt")
            _git(cf3, "commit", "-q", "-m", "seed", env_identity=True)
            (cf3 / "f.txt").write_text("y\n", encoding="utf-8")   # dirty so the snapshot has content
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the spaced-path recovery fixture: {}".format(exc),
                  file=sys.stderr)
            return 2
        _cf3top = os.path.realpath(str(cf3))
        _cf3snap = aiqt_hooks._take_snapshot(str(cf3), _cf3top, "reset")
        if _cf3snap[0] != "ok":
            failures.append("(cf3-snap) expected an ok snapshot for the quoting test, got {!r}"
                            .format(_cf3snap[0]))
        else:
            _cf3q = shlex.quote(_cf3top)
            if _cf3q not in _cf3snap[1]["restore"]:
                failures.append("(cf3-restore-quoted) the advertised restore command must shell-quote the "
                                "repo PATH (codex finding 3); got {!r}".format(_cf3snap[1]["restore"]))
            if "git -C {} checkout".format(_cf3top) in _cf3snap[1]["restore"]:
                failures.append("(cf3-restore-unquoted) the restore command interpolated the repo path "
                                "UNQUOTED (a space would break it, a ';' inject); got {!r}"
                                .format(_cf3snap[1]["restore"]))
            if _cf3q not in aiqt_hooks._recovery_pointer(_cf3snap[1]):
                failures.append("(cf3-pointer-quoted) the recovery pointer must shell-quote the repo PATH "
                                "(codex finding 3)")
        # F4: the redirected-stash recovery command binds to 'git -C <quoted repo> stash apply <ref>'.
        try:
            _git(cf3, "stash", "push", "-m", "wip", env_identity=True)   # one stash entry to preserve
            _cf3stashok = True
        except (OSError, subprocess.SubprocessError):
            _cf3stashok = False
        if _cf3stashok:
            _cf3code, _cf3obj, _ = aiqt_hooks._stash_drop_clear_outcome(str(cf3), "clear")
            _cf3msg = _cf3obj.get("systemMessage", "") if isinstance(_cf3obj, dict) else ""
            _cf3bind = "git -C {} stash apply".format(shlex.quote(str(cf3)))
            if _cf3bind not in _cf3msg:
                failures.append("(cf4-stash-bound) the stash recovery command must bind to the target repo "
                                "'git -C <quoted repo> stash apply' (codex finding 4); got {!r}"
                                .format(_cf3msg))

        # === ROUND-7 codex finding 1: git_discard resolves the REPO an index/ref/stash discard acts on from
        # -C/ambient, treating --work-tree as worktree-only, so the snapshot/stash preservation lands on the
        # ACTUAL repo git acts on (the ambient session repo), NOT the --work-tree value; a WORKTREE-CONTENT
        # discard whose --work-tree is OUTSIDE the ambient repo fails closed. Reverting reds these. =========
        try:
            cf1_decoy = _init_repo(tmp / "cf1-decoy")     # a clean SEPARATE repo used as the --work-tree value
            cf1_idx = _init_repo(tmp / "cf1-idx")         # ambient repo A with STAGED content (index discard)
            (cf1_idx / "file.txt").write_text("staged change\n", encoding="utf-8")
            _git(cf1_idx, "add", "file.txt")
            cf1_stash = _init_repo(tmp / "cf1-stash")     # ambient repo A with a STASH entry
            (cf1_stash / "file.txt").write_text("to stash\n", encoding="utf-8")
            _git(cf1_stash, "stash", "push", "-m", "wip", env_identity=True)
            cf1_wt = _init_repo(tmp / "cf1-wt")           # ambient repo A, dirty worktree (worktree discard)
            (cf1_wt / "file.txt").write_text("dirty\n", encoding="utf-8")
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the --work-tree recovery fixtures: {}".format(exc),
                  file=sys.stderr)
            return 2
        # F1a: 'git --work-tree=<decoy> restore --staged file.txt' (INDEX-only) from cf1_idx must snapshot
        # the AMBIENT repo cf1_idx (which holds the discarded staged content), NOT the decoy --work-tree repo.
        expect("(cf1a-index-allows) --work-tree index discard allows (snapshot the ambient repo)",
               "git --work-tree={} restore --staged file.txt".format(str(cf1_decoy)), "allow", cwd=str(cf1_idx))
        if not _recovery_refs(cf1_idx):
            failures.append("(cf1a-snap-ambient) a --work-tree index discard must snapshot the AMBIENT repo "
                            "(codex finding 1): expected a recovery ref in cf1-idx, found none")
        if _recovery_refs(cf1_decoy):
            failures.append("(cf1a-not-decoy) a --work-tree index discard must NOT snapshot the --work-tree "
                            "value repo (codex finding 1): found a stray recovery ref in cf1-decoy")
        # F1b: 'git --work-tree=<decoy> stash clear' from cf1_stash must preserve the AMBIENT repo's stash,
        # NOT the decoy's. The ambient repo gains a durable '-stash' recovery ref; the decoy stays empty.
        expect("(cf1b-stash-allows) --work-tree stash clear allows (preserve the ambient repo's stash)",
               "git --work-tree={} stash clear".format(str(cf1_decoy)), "allow", cwd=str(cf1_stash))
        if not any(r.endswith("stash0") or "stash" in r for r in _recovery_refs(cf1_stash)):
            failures.append("(cf1b-stash-ambient) a --work-tree stash clear must preserve the AMBIENT repo's "
                            "stash (codex finding 1): expected a stash recovery ref in cf1-stash, found none")
        if _recovery_refs(cf1_decoy):
            failures.append("(cf1b-not-decoy) a --work-tree stash clear must NOT preserve the --work-tree "
                            "value repo's stash (codex finding 1): stray ref in cf1-decoy")
        # F1c: 'git --work-tree=<decoy repo, OUTSIDE the ambient repo> reset --hard' destroys WORKTREE content
        # in the decoy while the index stays in the ambient repo: a single snapshot cannot capture the split,
        # so it DENIES. (Before the fix it snapshotted the clean decoy and allow-noted a false recovery.)
        expect("(cf1c-worktree-split-denies) --work-tree (outside) worktree discard DENIES (split state)",
               "git --work-tree={} reset --hard".format(str(cf1_decoy)), "deny", cwd=str(cf1_wt))

        # === CLAUDE-F1: a redirected/ambient 'checkout-index' (sub outside the recognized lossy-verb set,
        # flagged by the raw scan) entering the view-override branch DENIES (was allowed with no snapshot). ==
        expect("(clf1-checkout-index) a -C-redirected 'checkout-index -a -f' DENIES (CLAUDE-F1)",
               "git -C {} checkout-index -a -f".format(rp), "deny", cwd=rp)

        # === protected_line (prtbrn/artbr1): force-push to a protected ref + direct protected commit ===
        plg = aiqt_hooks.protected_line

        def pexpect(label, command, want, cwd=None):
            got = _decision(plg, command, cwd=cwd)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        def dexpect(label, command, want):
            got = _decision(aiqt_hooks.diff_source_pretool, command)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        pl_repo = _init_repo(tmp / "pl-repo")  # HEAD is main (a protected name) by construction
        # Give both protected-line fixtures a configured remote, so a direct commit on their protected
        # HEAD is denied as a REMOTE-BACKED repo (the whole existing commit-on-protected suite is the
        # regression guard that the no-remote exemption below does not weaken remote-backed protection).
        # A local path url suffices: `git remote add` only writes config, it fetches no refs, so
        # origin/HEAD stays unresolved and the branch-root probes are unaffected.
        _git(pl_repo, "remote", "add", "origin", str(tmp / "pl-remote.git"))
        plr = str(pl_repo)
        pl_feat = _init_repo(tmp / "pl-feat")
        _git(pl_feat, "remote", "add", "origin", str(tmp / "pl-feat-remote.git"))
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
        pexpect("(pl-h1) forced wildcard refspec denies (fail-safe: unprovable protected-line rewrite)",
                "git push --force origin refs/heads/*:refs/heads/*", "deny", cwd=plr)
        pexpect("(pl-h2) --mirror push asks", "git push --mirror backup", "deny", cwd=plr)
        pexpect("(pl-h3) forced --all asks", "git push --force --all origin", "deny", cwd=plr)
        pexpect("(pl-h3a) forced namespace-wide wildcard '+refs/*:refs/*' asks (GD-145: a sweep over "
                "every ref, including refs/heads/main, that the old refs/heads/-only test missed)",
                "git push origin '+refs/*:refs/*'", "deny", cwd=plr)

        # Refspec-less force-push: resolved through the HEAD probe (real repos, no mock).
        pexpect("(pl-i1) bare 'git push --force' with HEAD=main denies", "git push --force", "deny",
                cwd=plr)
        pexpect("(pl-i2) 'git push --force origin' with HEAD=main denies", "git push --force origin",
                "deny", cwd=plr)
        pexpect("(pl-i3) bare force-push with HEAD on a feature branch allows", "git push --force",
                "allow", cwd=plf)

        # Direct commit: ASK on the protected branch, ALLOW on a feature branch (HEAD probed for real).
        pexpect("(pl-j1) git commit with HEAD=main asks", "git commit -m 'fix'", "deny", cwd=plr)
        pexpect("(pl-j2) git commit on a feature branch allows", "git commit -m 'fix'", "allow",
                cwd=plf)
        pexpect("(pl-j3) add-and-commit compound on main asks",
                "git add -A && git commit -m 'fix'", "deny", cwd=plr)
        pexpect("(pl-j4) commit --amend on main asks", "git commit --amend --no-edit", "deny", cwd=plr)
        pexpect("(pl-j5) 'git -C <dir> commit' asks (redirected repository view)",
                "git -C {} commit -m 'fix'".format(plf), "allow", cwd=plr)
        # ROUND-7 (codex finding 2): --work-tree relocates ONLY the worktree, never which repository a commit
        # lands on, so 'git --work-tree=<feat> commit' from the main-HEAD session repo is classified against
        # the SESSION repo's protected HEAD (main) and DENIES - not against the --work-tree repo's non-protected
        # HEAD. Before the fix it probed the --work-tree value (pl_feat, HEAD 'other') and allow-noted. Reverting
        # reds this (it becomes an allow).
        pexpect("(pl-j5wt) 'git --work-tree=<feat> commit' from main HEAD DENIES (codex finding 2)",
                "git --work-tree={} commit -m 'fix'".format(plf), "deny", cwd=plr)
        os.environ["GIT_DIR"] = str(pl_feat / ".git")
        try:
            pexpect("(pl-j6) commit under an ambient GIT_DIR asks (unprovable view)",
                    "git commit -m 'fix'", "allow", cwd=plr)
        finally:
            os.environ.pop("GIT_DIR", None)

        # === No-remote exemption (artbr1): a repo with NO configured remote has no server-side branch
        # protection and no PR/CI path, so direct-to-main is its only model (a local record store's handoff
        # commit) and the COMMIT guard exempts it; a remote-backed repo stays protected exactly as before,
        # and an unresolvable remote probe fails CLOSED (still denied). ==================================
        nr_remote = _init_repo(tmp / "pl-nr-remote")   # main HEAD, WITH a configured remote
        _git(nr_remote, "remote", "add", "origin", str(tmp / "pl-nr-remote.git"))
        nr_none = _init_repo(tmp / "pl-nr-none")       # main HEAD, NO remote configured
        # (i) Regression guard: a direct commit on main in a REMOTE-BACKED repo STILL DENIES (the exemption
        # must not weaken a repo that has a remote). Reverting the exemption keeps this deny; over-firing it
        # (exempting a remote-backed repo) reds this.
        pexpect("(pl-nr1) commit on main in a repo WITH a remote still DENIES (remote-backed, unchanged)",
                "git commit -m 'fix'", "deny", cwd=str(nr_remote))
        # (ii) The exemption: a direct commit on main in a repo with NO remote configured ALLOWS. Removing
        # the exemption block reds this (it reverts to deny).
        pexpect("(pl-nr2) commit on main in a repo with NO remote ALLOWS (no-remote exemption)",
                "git commit -m 'fix'", "allow", cwd=str(nr_none))
        # (iii) Fail-closed leg: when the remote probe cannot be evaluated the guard DENIES, never allows on
        # an unverified basis. Inject _branch_root_git -> None (the primitive _repo_has_remote calls) so the
        # probe is undecidable over the SAME no-remote repo that (ii) allows; the deny here proves the None
        # path fails closed rather than exempting. Over-firing the exemption on None reds this.
        _orig_brg = aiqt_hooks._branch_root_git
        aiqt_hooks._branch_root_git = lambda repo, *args: None
        try:
            pexpect("(pl-nr3) commit on main DENIES when the remote probe cannot be evaluated (fail-closed)",
                    "git commit -m 'fix'", "deny", cwd=str(nr_none))
        finally:
            aiqt_hooks._branch_root_git = _orig_brg

        # === F-R2-1: the no-remote exemption is restricted to a LONE, directly-bound `git commit`. A COMPOUND
        # command's pre-command remote-absence probe is STALE (a `cd remote-repo && commit` lands in a repo the
        # probe never saw; a `git remote add && commit` adds the remote AFTER the probe ran), so a compound /
        # multi-segment / cd-bearing / redirected commit is handled EXACTLY as before the exemption: DENY when
        # it lands on a protected branch. Each DENY below runs from a no-remote main-HEAD session cwd (nr_none),
        # so it is fail-to-pass under a revert - reverting the lone_direct restriction makes the exemption
        # (nr_none has no remote) ALLOW these compound/redirected commits (the over-fire the finding reported).
        # nr_all1/nr_all2 exercise the remote-spelling coverage: a remote-backed repo in ANY spelling still
        # DENIES a lone protected commit (the exemption must not weaken it). Judged by the structured verdict. ==
        nr_upstream = _init_repo(tmp / "pl-nr-upstream")   # main HEAD, a remote named 'upstream'
        _git(nr_upstream, "remote", "add", "upstream", str(tmp / "pl-nr-upstream.git"))
        nr_fileurl = _init_repo(tmp / "pl-nr-fileurl")     # main HEAD, a file:// URL remote
        _git(nr_fileurl, "remote", "add", "origin", "file://" + str(tmp / "pl-nr-fileurl.git"))
        nr_backup = _init_repo(tmp / "pl-nr-backup")       # main HEAD, a 'backup' path remote
        _git(nr_backup, "remote", "add", "backup", str(tmp / "pl-nr-backup.git"))
        # Positive control (the exemption still works for the lone form): a lone bare commit on main in a
        # no-remote repo ALLOWS. This contrasts the compound denials below (the denial is the compound-ness,
        # not the repo), and reds if the lone-form exemption is broken.
        pexpect("(pl-fr1-0) lone 'git commit' on main in a no-remote repo ALLOWS (the exemption, lone form)",
                "git commit -m x", "allow", cwd=str(nr_none))
        # cd into a (remote-backed) repo then commit: compound -> exemption withheld -> DENY. Reverting reds it.
        pexpect("(pl-fr1-1) 'cd <remote-repo> && git commit' DENIES (compound; pre-command state is stale)",
                "cd {} && git commit --allow-empty -m qa".format(nr_remote), "deny", cwd=str(nr_none))
        # add a remote then commit: the pre-add no-remote state is stale -> DENY. Reverting reds it.
        pexpect("(pl-fr1-2) 'git remote add X <url> && git commit' DENIES (preceding remote mutation)",
                "git remote add upstream {} && git commit -m x".format(tmp / "x.git"), "deny",
                cwd=str(nr_none))
        # any other compound spelling (';'-sequenced) -> DENY (cover as before).
        pexpect("(pl-fr1-3) 'git commit ; echo done' DENIES (';'-compound, multi-segment)",
                "git commit -m x ; echo done", "deny", cwd=str(nr_none))
        # a redirected commit -> DENY (cover as before; the exemption is only for a lone bare commit).
        pexpect("(pl-fr1-4) 'git commit > out' DENIES (redirected commit)",
                "git commit -m x > out.txt", "deny", cwd=str(nr_none))
        # remote-backed repo in ANY spelling still DENIES a lone direct protected commit (regression guards).
        pexpect("(pl-fr1-5) lone commit on main with an 'upstream' remote DENIES (remote-backed)",
                "git commit -m x", "deny", cwd=str(nr_upstream))
        pexpect("(pl-fr1-6) lone commit on main with a file:// URL remote DENIES (remote-backed)",
                "git commit -m x", "deny", cwd=str(nr_fileurl))
        pexpect("(pl-fr1-7) lone commit on main with a 'backup' path remote DENIES (remote-backed)",
                "git commit -m x", "deny", cwd=str(nr_backup))

        # === F-R2-4: a LONE single segment with no redirects is NOT sufficient to prove the commit's
        # repo/remote context is stable - an executable command/process substitution INSIDE the commit
        # (a double-quoted `$(...)`, an unquoted backtick) can mutate that context (add a remote, run a
        # nested git) before git runs, so the pre-command no-remote probe is stale. The exemption must
        # require the commit segment be FREE of such substitution (opaque_shell False). Each DENY below
        # runs from a no-remote main-HEAD cwd (nr_none) and is fail-to-pass under a revert: dropping the
        # opaque_shell qualifier from lone_direct_commit re-ALLOWS these substitution-bearing commits
        # (the bypass the finding reported). The positive control (pl-fr1-0 above) proves the exemption
        # still ALLOWS the genuine lone bare commit, so these denials are the substitution, not the repo.
        pexpect("(pl-fr4-1) 'git commit -m \"$(git remote add ...)\"' DENIES (command substitution in "
                "the commit segment; no-remote probe is stale)",
                'git commit --allow-empty -m "$(git remote add origin /some/path; echo qa)"',
                "deny", cwd=str(nr_none))
        pexpect("(pl-fr4-2) 'git commit -m `git remote add ...`' DENIES (backtick command substitution)",
                "git commit --allow-empty -m `git remote add origin /some/path && echo qa`",
                "deny", cwd=str(nr_none))
        # A LONE single-segment backtick (no internal metachar to split on) is the strict fail-to-pass
        # for the backtick vector on the opaque_shell qualifier: pl-fr4-2's payload denies via multi-
        # segment (its internal '&&' splits it) even without the qualifier, whereas this one is a single
        # segment whose ONLY disqualifier is opaque_shell, so reverting the qualifier re-ALLOWS it.
        pexpect("(pl-fr4-2b) lone single-segment backtick commit DENIES (opaque_shell command "
                "substitution; strict fail-to-pass for the qualifier)",
                "git commit --allow-empty -m `whoami`", "deny", cwd=str(nr_none))
        pexpect("(pl-fr4-3) 'git commit -m \"$(git -C <repo> commit ...)\"' DENIES (nested-git command "
                "substitution)",
                'git commit --allow-empty -m "$(git -C {} commit --allow-empty -m injected; echo qa)"'
                .format(nr_upstream), "deny", cwd=str(nr_none))

        # === ROUND-2 FINDING 13: classify a commit against the branch it will ACTUALLY land on ===========
        # Direction 1 (false-DENY fix): a 'git switch -c <feature> && git commit' on a main HEAD lands on the
        # NEW branch, so it ALLOWS (was denied on the stale pre-command main HEAD). Discriminates: without the
        # switched_to tracking the commit reads plr's HEAD=main and denies.
        pexpect("(pl-f13a) switch -c feature && commit on main HEAD ALLOWS (lands on the new branch)",
                "git switch -c feature && git commit -m x", "allow", cwd=plr)
        pexpect("(pl-f13b) switch <existing feature> && commit ALLOWS (lands on the switched branch)",
                "git switch other && git commit -m x", "allow", cwd=plr)
        # Direction 1 (false-ALLOW fix): a switch TO a protected branch before a commit must DENY, even from a
        # feature HEAD - the pre-command probe (HEAD=other) would have wrongly allowed it.
        pexpect("(pl-f13c) switch main && commit from a feature HEAD DENIES (switched to the protected line)",
                "git switch main && git commit -m x", "deny", cwd=plf)
        # An '||' does NOT propagate the switch (the commit runs on switch FAILURE, HEAD unchanged), so a
        # commit after '|| ' is classified against the pre-command HEAD (main) and DENIES.
        pexpect("(pl-f13d) switch -c feature || commit on main HEAD DENIES ('||' commit runs on switch fail)",
                "git switch -c feature || git commit -m x", "deny", cwd=plr)
        # Direction 2 (false-ALLOW fix): a 'git -C <repo-on-main> commit' probes the -C TARGET's HEAD and
        # DENIES when that target is on the protected line (was a blanket allow-note for any -C redirect).
        pexpect("(pl-f13e) 'git -C <repo-on-main> commit' from a feature cwd DENIES (probes the -C target "
                "HEAD, finding 13)", "git -C {} commit -m x".format(plr), "deny", cwd=plf)
        # ... and still ALLOWS when the -C target is on a feature branch (the honoured explicit-target form).
        pexpect("(pl-f13f) 'git -C <repo-on-feature> commit' ALLOWS (the -C target HEAD is non-protected)",
                "git -C {} commit -m x".format(plf), "allow", cwd=plr)

        # ROUND-3 FINDING 4: an earlier same-command switch exempts a following commit ONLY when that commit
        # runs in the SAME (session) repository the switch acted on. A 'git switch -c feature && git -C <T>
        # commit' switches the SESSION repo but commits in repo T (on main); the switch must NOT exempt the
        # -C commit, which is classified against T's actual HEAD and DENIES. Revert the '_segment_dir_simple'
        # qualifier on the switched_to branch and this flips to 'allow' (the session switch wrongly exempts
        # the cross-repo commit on the protected line).
        pexpect("(pl-r3f4) switch -c feature (session) && commit -C <repo-on-main> DENIES (cross-repo, "
                "finding 4)", "git switch -c newfeat && git -C {} commit --allow-empty -m qa".format(plr),
                "deny", cwd=plf)

        # ROUND-3 FINDING 5: only '&&' gates a switch's success. A ';'-sequenced switch runs the commit
        # REGARDLESS of whether the switch succeeded ('git switch missing; git commit' lands on the still-
        # protected branch when the switch fails), so a ';' switch never exempts the following commit: it is
        # classified against the pre-command (protected) branch and DENIES. Revert the "_sep == '&&'" gate
        # (restoring ';' to the propagation set) and this flips to 'allow' (the failed switch wrongly exempts).
        pexpect("(pl-r3f5) switch missing; commit on main HEAD DENIES (';' does not gate switch success, "
                "finding 5)", "git switch missing; git commit --allow-empty -m qa", "deny", cwd=plr)

        # ROUND-6 FINDING 1 (B-class): the switch->commit exemption must hold ONLY on an UNBROKEN '&&' chain
        # from the switch through the commit. A '&& ... ||' retains the recorded switch target across the '||'
        # even though the commit runs when the switch FAILED (HEAD still on protected main), so it must DENY.
        # Revert the sw_pure_and / non-'&&'-clears-switched_to gating and this flips to 'allow' (the retained
        # exemption wrongly classifies the commit against the non-protected 'missing' the switch named).
        pexpect("(pl-r6f1) 'switch missing && true || commit' on main HEAD DENIES (commit runs on switch "
                "failure; exemption must not survive the '||', finding 1)",
                "git switch missing && true || git commit --allow-empty -m x", "deny", cwd=plr)
        # A '||' BEFORE the switch also lets the commit run without the switch ('x || switch && commit' runs
        # the commit when x succeeds and the switch is skipped), so the switch never gates it -> DENY.
        pexpect("(pl-r6f1b) 'true || switch -c feat && commit' DENIES (switch skipped when 'true' succeeds)",
                "true || git switch -c feat && git commit --allow-empty -m x", "deny", cwd=plr)
        # No regression: a pure '&&' chain through the commit still exempts it (lands on the switched branch).
        pexpect("(pl-r6f1c) 'switch -c feat && true && commit' still ALLOWS (unbroken '&&' chain)",
                "git switch -c feat && true && git commit --allow-empty -m x", "allow", cwd=plr)
        # A new and-or list boundary (';') resets the pure-'&&' prefix, so 'x ; switch -c feat && commit' still
        # exempts (the commit IS '&&'-gated on the switch within its own list).
        pexpect("(pl-r6f1d) 'true ; switch -c feat && commit' still ALLOWS ('&&'-gated within its own list)",
                "true ; git switch -c feat && git commit --allow-empty -m x", "allow", cwd=plr)

        # ROUND-6 FINDING 2 (Lens E): an ANSI-C ($'...') heredoc delimiter must resolve to its LITERAL value,
        # so the lexer ends the heredoc body at the real EOF line and a FOLLOWING command stays executable
        # (previously '$' was ordinary, building the delimiter '$EOF' that never matched, so every following
        # line was swallowed as body and a hidden --no-verify commit / force-push escaped every guard).
        _f2cmd = "cat <<$'EOF'\nnote body\nEOF\ngit push --force origin main"
        try:
            _f2segs = [s.argv for s in aiqt_hooks._lex_command(_f2cmd) if s.argv]
        except ValueError:
            _f2segs = None
        if _f2segs != [["cat"], ["git", "push", "--force", "origin", "main"]]:
            failures.append("(pl-r6f2-lex) an ANSI-C $'EOF' heredoc delimiter must resolve to EOF, ending the "
                            "body at the real EOF line so the following 'git push' is a visible segment; got "
                            "{!r} (finding 2)".format(_f2segs))
        # And the guard now SEES and DENIES the previously-hidden protected force-push.
        pexpect("(pl-r6f2-guard) a force-push hidden after an ANSI-C $'EOF' heredoc is now seen and DENIES "
                "(finding 2)", _f2cmd, "deny", cwd=plr)

        # CLAUDE-F1: the protected_line raw FALLBACK strips QUOTED-heredoc bodies before scanning, so a
        # 'git push --force ... main' that appears only inside a quoted heredoc body (literal data, here forced
        # to the fallback by a process substitution the lexer cannot parse) no longer FALSE-DENIES. Revert the
        # _strip_quoted_heredoc_bodies pass in the fallback and this flips to 'deny' (the body over-matches).
        pexpect("(pl-cf1) push-force inside a QUOTED heredoc body (fallback) does not false-deny (CLAUDE-F1)",
                "cat <(echo x) <<'EOF'\ngit push -f origin main\nEOF", "allow", cwd=plr)
        # DISCRIMINATION: a REAL force-push OUTSIDE the quoted heredoc body, on the same unparseable command,
        # is preserved by the body-strip and still DENIES (no under-deny introduced).
        pexpect("(pl-cf1-outside) a real force-push OUTSIDE the quoted heredoc body still DENIES (CLAUDE-F1)",
                "git push -f origin main <(echo x)", "deny", cwd=plr)

        # Probe failure is fail-to-ASK for both surfaces (mocked like _tree_is_clean above).
        _orig_head = aiqt_hooks._head_branch
        aiqt_hooks._head_branch = lambda repo: None
        try:
            pexpect("(pl-i4) bare force-push asks when HEAD cannot be resolved",
                    "git push --force", "deny", cwd=plr)
            pexpect("(pl-i5) direct commit asks when HEAD cannot be resolved",
                    "git commit -m 'x'", "allow", cwd=plr)
        finally:
            aiqt_hooks._head_branch = _orig_head

        # Parse-error posture (unbalanced quote): fail-safe, never a silent allow.
        pexpect("(pl-k1) unparseable apparent force-push naming main asks (fallback recoverable)",
                'git push --force origin main "unbalanced', "deny", cwd=plr)
        pexpect("(pl-k2) unparseable force-push with no readable protected target asks",
                'git push --force "unbalanced', "deny", cwd=plr)
        pexpect("(pl-k3) unparseable apparent commit asks", 'git commit -m "it broke', "allow", cwd=plr)
        pexpect("(pl-k4) unparseable non-git command allows", 'ls -la "unbalanced', "allow", cwd=plr)

        # === F-112 round-2: HEAD/@ proxy (B1), fallback +refspec/--for (B2/3B), the --recurse-submodules
        # value-skip (1A, removed in round-2 and RESTORED in round-3: the value is mandatory-separable),
        # and a wrapped git push (1C) ===
        pexpect("(pl-n1) force-push to HEAD on main denies (HEAD resolves to the current branch)",
                "git push --force origin HEAD", "deny", cwd=plr)
        pexpect("(pl-n2) '-f origin @' on main denies (@ is HEAD)", "git push -f origin @", "deny", cwd=plr)
        pexpect("(pl-n3) force-push to HEAD on a feature branch allows",
                "git push --force origin HEAD", "allow", cwd=plf)
        pexpect("(pl-n4) '+HEAD' on main denies", "git push origin +HEAD", "deny", cwd=plr)
        pexpect("(pl-o1) separated --recurse-submodules value is consumed (mandatory value); the "
                "refspec-less force then probes HEAD and denies on main",
                "git push -f --recurse-submodules main origin", "deny", cwd=plr)
        pexpect("(pl-p1) unparseable '+main' force-push (no -f) asks on the fallback",
                'git push origin +main "unbalanced', "deny", cwd=plr)
        pexpect("(pl-p2) unparseable '--for' abbreviation force-push asks on the fallback",
                'git push --for origin main "unbalanced', "deny", cwd=plr)
        pexpect("(pl-q1) a wrapped force-push (env) asks via the raw scan, not a silent allow",
                "env git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-q2) a wrapped NON-force push is out of scope, allows",
                "env git push origin main", "allow", cwd=plr)
        pexpect("(pl-q3) a non-git wrapped command allows", "env FOO=1 echo hi", "allow", cwd=plr)
        # === F-112 round-3: protected DELETION, value-aware flags, operand roles, fallback widening ===
        # A delete of a protected branch rewrites the protected line like a force: every spelling denies.
        pexpect("(pl-u1) push --delete of main denies", "git push --delete origin main", "deny", cwd=plr)
        pexpect("(pl-u2) push -d of main denies", "git push -d origin main", "deny", cwd=plr)
        pexpect("(pl-u3) empty-source ':main' delete refspec denies", "git push origin :main", "deny",
                cwd=plr)
        pexpect("(pl-u4) ':refs/heads/main' delete refspec denies",
                "git push origin :refs/heads/main", "deny", cwd=plr)
        pexpect("(pl-u5) a delete of a feature branch allows",
                "git push --delete origin old-feature", "allow", cwd=plr)
        pexpect("(pl-u6) a wildcard empty-source delete asks (a sweep the guard cannot prove safe)",
                "git push origin ':refs/heads/*'", "deny", cwd=plr)
        pexpect("(pl-u7) '--delete origin HEAD' on main denies via the probe (a harmless over-deny: "
                "git itself rejects a HEAD delete as a nonexistent ref)",
                "git push --delete origin HEAD", "deny", cwd=plr)
        pexpect("(pl-u8) a refspec-less --delete allows (git itself rejects it, nothing to resolve)",
                "git push --delete origin", "allow", cwd=plr)
        # F-117: info-flag value-awareness and the widened fallback force/delete short clusters.
        pexpect("(f117-a) commit -m --help on protected HEAD asks (--help is the -m value, not help)",
                "git commit -m --help", "deny", cwd=plr)
        pexpect("(f117-b) push -o --help --force to main denies (--help is the -o value, --force is real)",
                "git push -o --help --force origin main", "deny", cwd=plr)
        pexpect("(f117-c) genuine push --help still allows", "git push --help", "allow", cwd=plr)
        pexpect("(f117-d) genuine commit --help still allows", "git commit --help", "allow", cwd=plr)
        pexpect("(f117-e) commit --amend --help over-asks (documented safe-direction residual)",
                "git commit --amend --help", "deny", cwd=plr)
        pexpect("(f117-f) unparseable push --prune asks (fallback prune spelling, F-117)",
                'git push --prune origin main "unbalanced', "deny", cwd=plr)
        pexpect("(f117-g) unparseable push -4d digit cluster asks (fallback, F-117)",
                'git push -4d origin main "unbalanced', "deny", cwd=plr)
        pexpect("(f117-h) unparseable push -4f digit cluster force asks (fallback, F-117)",
                'git push -4f origin main "unbalanced', "deny", cwd=plr)
        # F-117 round-6: --help/-h that git does not treat as help (after `--`, or as a redirect
        # target) must not mask a protected-branch action; quoted short clusters under a wrapper.
        pexpect("(f117r6-a) redirect-target --help does not mask a force-push",
                "git push --force origin main > --help", "deny", cwd=plr)
        pexpect("(f117r6-b) end-of-options operand --help does not mask a force-push",
                "git push --force origin -- main --help", "deny", cwd=plr)
        pexpect("(f117r6-c) end-of-options operand --help does not mask a direct commit",
                "git commit -- README.md --help", "deny", cwd=plr)
        pexpect("(f117r6-d) quoted short-cluster force under a wrapper asks (fallback)",
                "env git push '-4f' origin main", "deny", cwd=plr)
        pexpect("(f117r6-e) quoted short-cluster delete under a wrapper asks (fallback)",
                "env git push '-4d' origin main", "deny", cwd=plr)
        pexpect("(f117r6-f) attached-value option before --help over-asks (disclosed residual)",
                "git commit -mfoo --help", "deny", cwd=plr)
        # F-117 round-6 / F-118: the shared diff-source guard (cnsdif). Genuine help allows; a --help
        # that git treats as a pathspec (after `--`) is a real console dump and denies; a non-stdout fd
        # redirect ('2>') is not a stdout real-file escape (F-118); a real stdout redirect still allows.
        dexpect("(f117r6-g) genuine diff --help allows", "git diff --help", "allow")
        dexpect("(f117r6-i) end-of-options operand --help does not mask a diff dump",
                "git diff -- file --help", "deny")
        dexpect("(f117r6-j) stderr redirect is not a stdout real-file escape (F-118)",
                "git diff 2> errors.log", "deny")
        dexpect("(f117r6-k) genuine stdout real-file redirect still allows",
                "git diff > real.txt", "allow")
        dexpect("(f117r6-l) explicit fd-1 stdout redirect allows",
                "git diff 1> real.txt", "allow")
        # F-117 round-7: remaining reachable silent-allows now fixed (prtbrn --end-of-options + --repo;
        # cnsdif clustered patch + command-word-wrapper fallback), plus disclosed-residual lock cases.
        pexpect("(f117r7-a) --end-of-options boundary does not mask a force-push",
                "git push --force origin --end-of-options main --help", "deny", cwd=plr)
        pexpect("(f117r8-a) --repo does not create a false refspec escape (refspec-less force denies)",
                "git push -f --repo=origin backup", "deny", cwd=plr)
        # F-121 round-9: value-taking-option parse gaps (a global --attr-source, and abbreviated push
        # value-options) must not mask a protected-branch action.
        pexpect("(f117r9-a) separated --attr-source global option does not mask a force-push",
                "git --attr-source HEAD push --force origin main", "deny", cwd=plr)
        pexpect("(f117r9-b) separated --attr-source does not mask a direct commit",
                "git --attr-source HEAD commit -m x", "allow", cwd=plr)
        pexpect("(f117r9-c) abbreviated --rep (=--repo) does not mask a refspec-less force",
                "git push -f --rep backup origin", "deny", cwd=plr)
        pexpect("(f117r9-d) abbreviated --push-opt does not mask a refspec-less force",
                "git push -f --push-opt marker origin", "deny", cwd=plr)
        pexpect("(f117r9-e) a genuine separated --push-option value is still skipped (plain push allows)",
                "git push --push-option marker origin main", "allow", cwd=plr)
        pexpect("(f117r7-x1) embedded-quote flag in the raw fallback is a DISCLOSED inherent residual",
                "env git push -'f' origin main", "allow", cwd=plr)
        # L11 AIRTIGHT-NARROW redesign: the former F-119 disclosed silent-allow residuals now ASK. A
        # producer-capable form outside the four closed proofs (extra summary modifiers, a wrapper, a
        # benign 'git log', a pickaxe listing) is producer-capable-but-unproven -> ASK, never ALLOW.
        dexpect("(f117r7-d) clustered patch flag -wp is now an ASK (L11: producer-capable, unproven)",
                "git log -wp", "allow")
        dexpect("(f117r7-e) pickaxe -Sfoo git log is now an ASK (L11: producer-capable, unproven listing)",
                "git log -Sfoo", "allow")
        dexpect("(f117r7-f) wrapped git diff is now an ASK (L11: a wrapper fits no proof)",
                "env git diff", "allow")
        dexpect("(f117r7-g) wrapper over a non-producer allows (no producer surface)", "env git status", "allow")
        dexpect("(f117r7-i) git diff -M --stat allow-notes (L11: an extra option fails the exact summary "
                "proof, so it is producer-capable-but-unproven -> allow-with-note, not a confirmed dump)",
                "git diff -M --stat", "allow")
        dexpect("(f117r7-x2) -S --stat pickaxe-value allow-notes (L11: an extra option fails proof B, "
                "producer-capable-but-unproven -> allow-with-note)",
                "git diff -S --stat", "allow")
        dexpect("(f117r7-j) genuine git log -p still denies (confirmed console patch)", "git log -p", "deny")

        # ROUND-3 FINDING 7 (heredoc delimiter concatenation): a PARTIALLY-QUOTED heredoc delimiter is the
        # concatenation of its adjacent quoted+unquoted fragments (bash word rules), so <<'EO'F closes on the
        # line 'EOF'. The lexer previously read only the first fragment ('EO'), so the true 'EOF' closing line
        # was consumed as heredoc body and a trailing 'git diff' console dump was HIDDEN and silently ALLOWED.
        # It must now resolve the delimiter to 'EOF', see the following 'git diff', and DENY the dump. Revert
        # the _parse_heredoc_delim fragment loop and this flips to 'allow' (the diff is swallowed again).
        dexpect("(r3f7-a) partially-quoted heredoc delimiter does not hide a trailing git diff dump",
                "cat <<'EO'F\nbody\nEOF\ngit diff", "deny")
        dexpect("(r3f7-b) trailing-quoted-fragment heredoc delimiter does not hide a git diff dump",
                "cat <<EO'F'\nbody\nEOF\ngit diff", "deny")
        dexpect("(r3f7-c) fully-quoted heredoc delimiter still resolves correctly (preserved)",
                "cat <<'EOF'\nbody\nEOF\ngit diff", "deny")

        # === branch_root (brnrot): H1-H5 structured branch-creation decisions =================
        brg = aiqt_hooks.branch_root
        br_repo = _init_repo(tmp / "branch-root-repo")
        brr = str(br_repo)
        # Give the protected line depth >= 2 so origin/HEAD~1 names a rooted ancestor (the H12 stale
        # case). _init_repo leaves HEAD on `main`, so this second commit advances the protected tip.
        (br_repo / "second.txt").write_text("second\n", encoding="utf-8")
        _git(br_repo, "add", "second.txt")
        _git(br_repo, "commit", "-q", "-m", "second", env_identity=True)
        br_tip = subprocess.run(
            ["git", "-C", brr, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        _git(br_repo, "update-ref", "refs/remotes/origin/main", br_tip)
        _git(br_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        def brexpect(label, command, want):
            got = _decision(brg, command, cwd=brr)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # H1: implicit HEAD is rooted on origin/HEAD.
        brexpect("(H1) checkout -b from rooted HEAD allows", "git checkout -b x", "allow")

        # H2: a parentless commit in the same object database is an explicit orphan start.
        br_tree = subprocess.run(
            ["git", "-C", brr, "rev-parse", "HEAD^{tree}"],
            check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        br_orphan = subprocess.run(
            ["git", "-C", brr, "-c", "user.name=Test", "-c",
             "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
             "commit-tree", br_tree],
            input="orphan root\n", check=True, capture_output=True, text=True,
            timeout=30).stdout.strip()
        _git(br_repo, "update-ref", "refs/heads/orphan-start", br_orphan)
        brexpect("(H2) checkout -b from orphan denies",
                 "git checkout -b x orphan-start", "deny")

        # ROUND-7 (codex finding 2): --work-tree relocates only the worktree, never the repository a branch is
        # created in. A second repo (br_wt) has a ROOTED branch 'orphan-start'; the session repo br_repo has an
        # ORPHANED 'orphan-start'. 'git --work-tree=<br_wt> checkout -b x orphan-start' from br_repo creates the
        # branch in br_repo from br_repo's ORPHANED start, so it must DENY. Before the fix the guard probed the
        # --work-tree value (br_wt, where 'orphan-start' is rooted) and ALLOWED. Reverting reds this (-> allow).
        br_wt = _init_repo(tmp / "br-wt")
        _brwt_tip = subprocess.run(["git", "-C", str(br_wt), "rev-parse", "HEAD"],
                                   check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        _git(br_wt, "update-ref", "refs/remotes/origin/main", _brwt_tip)
        _git(br_wt, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        _git(br_wt, "branch", "orphan-start")   # a ROOTED branch (shares origin/HEAD) of the SAME name
        brexpect("(H2wt) --work-tree to a repo with a rooted 'orphan-start' still DENIES (codex finding 2: "
                 "the branch is created in the ambient repo's ORPHANED start)",
                 "git --work-tree={} checkout -b x orphan-start".format(str(br_wt)), "deny")

        # H6: the switch long-form create (--create/--force-create) is now recognized, so an orphan
        # start denies just like the short -c/-C form (previously the long forms were unhandled and the
        # command silently passed).
        brexpect("(H6a) switch --create from orphan denies",
                 "git switch --create x orphan-start", "deny")
        brexpect("(H6b) switch --force-create from orphan denies",
                 "git switch --force-create x orphan-start", "deny")

        # H7: under fail-safe-ASK, a tracking form is not a confidently-clean creation - `git branch
        # --track` may create-with-tracking OR (with no start) DWIM the current upstream - so branch
        # --track/-t routes to ASK, consistent with checkout/switch --track (redesign-fix-2, codex MAJOR).
        brexpect("(H7a) branch --track from orphan asks",
                 "git branch --track x orphan-start", "deny")
        brexpect("(H7b) branch -t (short --track) asks",
                 "git branch -t x orphan-start", "deny")

        # H8: git branch upstream-setting forms are non-creation and are not judged.
        brexpect("(H8a) branch -u <upstream> <name> is non-creation allows",
                 "git branch -u origin/main x", "allow")
        brexpect("(H8b) branch --set-upstream-to=<upstream> <name> is non-creation allows",
                 "git branch --set-upstream-to=origin/main x", "allow")

        # H9: ROUND-2 FINDING 12: '--track'/'-t' only ENABLES upstream tracking and names a real rooted
        # upstream (origin/main), so it no longer routes the whole form to a fail-safe deny; the checkout/
        # switch --track forms ALLOW (the positional operand remains the start point / a DWIM tracking
        # creation). '--orphan' and worktree '--track' still ASK->deny (their start is genuinely unverifiable).
        brexpect("(H9a) checkout --track origin/main allows (finding 12: rooted upstream)",
                 "git checkout --track origin/main", "allow")
        brexpect("(H9b) checkout -t origin/main allows (finding 12)", "git checkout -t origin/main", "allow")
        brexpect("(H9c) switch --track origin/main allows (finding 12)",
                 "git switch --track origin/main", "allow")
        brexpect("(H9d) worktree add --orphan asks", "git worktree add --orphan ../wt-orphan", "deny")
        brexpect("(H9e) worktree add --track asks",
                 "git worktree add --track -b x ../wt-track origin/main", "deny")

        # H10: only an EXPLICIT -b/-B worktree creation is deny-capable (its start is extractable). A bare
        # `worktree add <path> <commit-ish>` (no -b) is a detached/existing checkout, not a clean creation,
        # so under fail-safe-ASK it ASKS rather than probe-denying a non-creation (redesign-fix-1, codex +
        # claude corroborated); `worktree add <path>` alone is ambiguous (DWIM-create vs checkout-existing) and ASKS.
        brexpect("(H10a) worktree add -b <name> <path> <orphan-start> denies",
                 "git worktree add -b wtden ../wt-den orphan-start", "deny")
        brexpect("(H10b) worktree add <path> <commit-ish> (no -b) asks",
                 "git worktree add ../wt-ci orphan-start", "deny")
        brexpect("(H10c) worktree add <path> alone (ambiguous DWIM-create vs checkout-existing) asks",
                 "git worktree add ../wt-dwim", "deny")

        # H11: an unmodelled option of unknown arity in a creation-ish command ASKS rather than risk a
        # mis-extracted start (fail safe over a fragile full-grammar model).
        brexpect("(H11) checkout -b with an unknown option asks",
                 "git checkout -b x --unknown-opt", "deny")

        # H12: a rooted-but-STALE start (an old ancestor of origin/HEAD) PASSES the hook; only the CI
        # gate's configured --max-lag catches staleness (documented residue, disclose-guard-residuals).
        brexpect("(H12) checkout -b from a stale-but-rooted start allows",
                 "git checkout -b x origin/HEAD~1", "allow")

        # ROUND-2 FINDING 12 (keep-working): the -c creation carrying '--track <ref>' is checked against its
        # positional start; a rooted upstream ALLOWS, but --track never rescues a genuine ORPHAN operand.
        brexpect("(f12-track-rooted) switch -c topic --track origin/main allows (rooted upstream operand)",
                 "git switch -c topic --track origin/main", "allow")
        brexpect("(f12-track-orphan) switch -c topic --track <orphan> still DENIES (--track does not rescue "
                 "an orphan start)", "git switch -c topic --track orphan-start", "deny")

        # ROUND-3 FINDING 6: a '--track'/'-t' checkout/switch WITHOUT an explicit -c/-B still CREATES a local
        # branch (git's DWIM tracking creation) rooted at the operand, so its ancestry MUST be probed. An
        # orphan operand DENIES; a rooted operand (origin/main, H9a-c above) still ALLOWS. Revert the
        # track-creation branch in _checkout_creation_start and these flip to 'allow' (the missing -c wrongly
        # read as a non-creation that skips the probe).
        brexpect("(r3f6a) switch --track <orphan> (no -c) DENIES (DWIM tracking creation off an orphan)",
                 "git switch --track orphan-start", "deny")
        brexpect("(r3f6b) checkout --track <orphan> (no -c) DENIES (DWIM tracking creation off an orphan)",
                 "git checkout --track orphan-start", "deny")
        brexpect("(r3f6c) checkout -t <orphan> (short --track, no -b) DENIES",
                 "git checkout -t orphan-start", "deny")
        # (banner support) a -C/--work-tree target that RESOLVES to a concrete NON-repo directory is probed
        # there and DENIES via the ancestry-unknown fail-safe (not allow-with-note); only a --git-dir/GIT_dir
        # or a truly opaque -C notes. /etc is a real dir that is not a git repo.
        if _decision(brg, "git -C /etc checkout -b x", cwd=brr) != "deny":
            failures.append("(r3-C-nonrepo) a -C target that resolves to a non-repo concrete dir (/etc) must "
                            "DENY via the ancestry-unknown fail-safe, not allow-with-note")
        # -C-AWARENESS: with the session cwd a NON-git dir, a '-C <repo>' creation resolves the TARGET and
        # checks ancestry THERE. A rooted HEAD ALLOWS (was a fail-safe deny before finding 12 - the flip
        # discriminates the fix), while an orphan start under the same -C target is still caught and DENIES.
        _f12_plain = tmp / "f12-plain-cwd"
        _f12_plain.mkdir(parents=True, exist_ok=True)
        if _decision(brg, "git -C {} checkout -b x".format(brr), cwd=str(_f12_plain)) != "allow":
            failures.append("(f12-C-rooted) a -C <repo> creation from a rooted HEAD must ALLOW by probing "
                            "the -C target, even from a non-git cwd (finding 12 -C-awareness)")
        if _decision(brg, "git -C {} checkout -b x orphan-start".format(brr),
                     cwd=str(_f12_plain)) != "deny":
            failures.append("(f12-C-orphan) a -C <repo> creation from an ORPHAN start must still DENY "
                            "(the -C target's ancestry is checked, not blindly allowed)")

        # H3: ROUND-2 FINDING 12: origin/HEAD cannot be derived, so the guard falls back to a local
        # main/master; a MISSING protected line is not evidence a plain local branch is orphaned, so a
        # resolvable-start creation ALLOWS (was an over-block deny). A genuine orphan is still caught by the
        # ancestry probe when a protected line DOES resolve (see the orphaned-start denies above).
        _git(br_repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        brexpect("(H3) missing origin/HEAD allows a plain local branch (finding 12)",
                 "git checkout -b x", "allow")

        # H4: non-creation git commands are untouched even while origin/HEAD is absent.
        for br_cmd in ("git status", "git log --oneline", "git branch"):
            brexpect("(H4) {} allows".format(br_cmd), br_cmd, "allow")

        # H5: a non-git command is outside the matcher logic.
        brexpect("(H5) non-git command allows", "printf hello", "allow")

        # H3 above deleted origin/HEAD; restore it so the H13+ ancestry probes have a protected ref.
        _git(br_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

        # H13: F2 round-2 forms the round-1 parser silently ALLOWED now extract the start and DENY an
        # orphan: an attached =value switch create, and a clustered short create.
        brexpect("(H13a) switch --create=NAME from orphan denies",
                 "git switch --create=x orphan-start", "deny")
        brexpect("(H13b) switch --force-create=NAME from orphan denies",
                 "git switch --force-create=x orphan-start", "deny")
        brexpect("(H13c) checkout clustered -qbNAME from orphan denies",
                 "git checkout -qbfoo orphan-start", "deny")

        # H14: --recurse-submodules is BOOLEAN for git branch (so the start is extracted and an orphan
        # denies); --color is an ambiguous optional-value the redesign will not classify, so it ASKS
        # rather than silently allow (its orphan is caught by the ASK or the CI gate).
        # (maximally-conservative 2026-09-02) any option outside the tiny valueless allowlist -q/-f/-v ->
        # ASK, even a boolean like --recurse-submodules; the creation is caught by ASK, not silently allowed.
        brexpect("(H14a) branch --recurse-submodules creation asks",
                 "git branch --recurse-submodules x orphan-start", "deny")
        brexpect("(H14b) branch with trailing --color asks (unclassifiable optional-value)",
                 "git branch x orphan-start --color", "deny")

        # H15: a cd/pushd BEFORE the git-creation segment leaves the target repository unreconcilable
        # with the session cwd, so the creation ASKS (F3) rather than probe the wrong repo.
        brexpect("(H15a) cd-prefixed checkout -b asks",
                 "cd /tmp && git checkout -b x orphan-start", "deny")
        brexpect("(H15b) pushd-prefixed switch -c asks",
                 "pushd /tmp && git switch -c x orphan-start", "deny")

        # H16: non-creation git branch forms in their =value and attached-upstream spellings are
        # correctly non-creation ALLOWs (F5), not over-blocked or over-asked.
        brexpect("(H16a) branch --contains=main (value-taking filter) asks",
                 "git branch --contains=main", "deny")
        brexpect("(H16b) branch --merged=main (value-taking filter) asks",
                 "git branch --merged=main", "deny")
        brexpect("(H16c) branch --points-at=main (value-taking filter) asks",
                 "git branch --points-at=main 'ma*'", "deny")
        brexpect("(H16d) branch -u<upstream> <name> (attached value) asks",
                 "git branch -umain feature", "deny")

        # H17: a clustered short create where the create letter is NOT first (-mb = -m -b) still extracts
        # no clean start, so it ASKS rather than silently allowing (round-3 claude MAJOR; makes the
        # "clustered -> ASK" residue disclosure true).
        brexpect("(H17a) checkout -mb (create letter not first) asks",
                 "git checkout -mb feat orphan-start", "deny")
        brexpect("(H17b) switch -mc (create letter not first) asks",
                 "git switch -mc feat orphan-start", "deny")
        # H18: --guess enables DWIM auto-creation from a matching remote (implicit start) -> ASK.
        brexpect("(H18) checkout --guess asks (DWIM implicit start)",
                 "git checkout --guess feat", "deny")
        # H19: for checkout, everything after -- is a pathspec, so -b feat -- path is cut from HEAD (rooted)
        # -> allow (round-3 claude MINOR: the post-'--' token is no longer mis-read as the start).
        brexpect("(H19) checkout -b feat -- path is cut from HEAD allows",
                 "git checkout -b feat -- path", "allow")
        # H20: git worktree add --guess-remote may pick a matching remote-tracking branch as the start
        # (implicit) -> ASK rather than default to HEAD (round-3 codex MAJOR-2).
        brexpect("(H20) worktree add --guess-remote asks (implicit start)",
                 "git worktree add --guess-remote /tmp/wt-x", "deny")

        # H21: an explicit orphan start given AFTER `--` is a real creation form for switch/branch/worktree
        # (they have no pathspecs), so it is collected and DENIES an orphan (round-4 codex BLOCKER: fix-3 had
        # over-applied checkout's post-`--`-is-a-pathspec treatment to all three).
        brexpect("(H21a) branch -- <name> <orphan> denies",
                 "git branch -- boundary orphan-start", "deny")
        brexpect("(H21b) switch -c <name> -- <orphan> denies",
                 "git switch -c switchboundary -- orphan-start", "deny")
        brexpect("(H21c) worktree add -b <name> -- <path> <orphan> denies",
                 "git worktree add -b wtboundary -- /tmp/wt-b orphan-start", "deny")
        # H21d: for checkout, post-`--` remains a pathspec, so the branch is cut from HEAD (rooted) -> allow.
        brexpect("(H21d) checkout -b <name> -- <pathspec> is cut from HEAD allows",
                 "git checkout -b coboundary -- orphan-start", "allow")
        # H22: git resolves an unambiguous long-option PREFIX to the full option, so an abbreviated
        # creation/tracking flag must ASK (round-5 codex MAJOR: --cre/--orp/--tr returned allow).
        # H27: a popd (like cd/pushd) BEFORE a git creation moves the target out of the session cwd, so
        # the creation routes to ASK, not a probe of the wrong directory (redesign-fix-4, codex MAJOR).
        brexpect("(H27) popd then a creation asks (dir change)",
                 "popd && git checkout -b h27 orphan-start", "deny")
        # H28: an unsupported LATER construct (a heredoc) must NOT discard a proven-complete orphan creation
        # already in the prefix; DENY outranks the whole-command parse error (redesign-fix-5, codex MAJOR).
        brexpect("(H28) creation then heredoc still denies (parse-poison)",
                 "git checkout -b h28 orphan-start; cat <<'EOF'\npayload\nEOF\n", "deny")
        brexpect("(H22a) switch --cre (abbrev --create) asks",
                 "git switch --cre abbrbr orphan-start", "deny")
        brexpect("(H22b) checkout --orp (abbrev --orphan) asks",
                 "git checkout --orp abbrbr", "deny")
        brexpect("(H22c) checkout --tr (abbrev --track) asks",
                 "git checkout --tr origin/abbr", "deny")
        brexpect("(H22d) switch --force-cre (abbrev --force-create) asks",
                 "git switch --force-cre abbrbr orphan-start", "deny")
        # H22e: under the fail-safe-ASK redesign an unrecognized long option ASKS (it could be an
        # abbreviated creation flag), never a silent allow. (Superseded the pre-redesign allow expectation.)
        brexpect("(H22e) checkout --patch (unrecognized long option) asks",
                 "git checkout --patch abbrbr", "deny")

        # H23: a FAILURE of the shallow-repository probe must fail SAFE to unknown/ASK, never fall through
        # to ORPHANED/deny (round-5 codex MINOR; aligns the hook with the gate's cannot-evaluate posture).
        class _R:
            def __init__(self, rc, out=""):
                self.returncode = rc
                self.stdout = out
        def _probe_with_shallow(shallow_result):
            seq = [_R(0, "AAA"), _R(0, "BBB"), _R(1, ""), shallow_result]
            saved = aiqt_hooks._branch_root_git
            aiqt_hooks._branch_root_git = lambda repo, *a: seq.pop(0)
            try:
                return aiqt_hooks._branch_root_probe("/nonexistent", "start")[0]
            finally:
                aiqt_hooks._branch_root_git = saved
        for label, res, want in (
                ("(H23a) shallow-probe exit 128 -> unknown", _R(128, ""), "unknown"),
                ("(H23b) shallow-probe None -> unknown", None, "unknown"),
                ("(H23c) shallow-probe 'true' -> unknown", _R(0, "true"), "unknown"),
                ("(H23d) shallow-probe garbage -> unknown", _R(0, "maybe"), "unknown"),
                ("(H23e) shallow-probe 'false' -> orphaned", _R(0, "false"), "orphaned")):
            got = _probe_with_shallow(res)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # H24: `git branch -c/-C` COPY makes a new ref at the SOURCE commit, so an orphaned source DENIES
        # (round-6 codex MAJOR: copy was classified non-creation and allowed); a RENAME is out of scope.
        brexpect("(H24a) branch -c <orphan-src> <dst> denies",
                 "git branch -c orphan-start copied", "deny")
        brexpect("(H24b) branch -C <orphan-src> <dst> (force copy) denies",
                 "git branch -C orphan-start copied2", "deny")
        brexpect("(H24c) branch -c <dst> copies rooted HEAD, allows",
                 "git branch -c copiedhead", "allow")
        brexpect("(H24d) branch -m rename is out of scope, allows",
                 "git branch -m orphan-start renamed", "allow")
        # H24e: an explicit COPY action dominates a display/list classifier - `git branch -c <orphan> <dst>
        # --format=...` still copies, so the orphan source is probed and DENIES, never silently allowed
        # (redesign-fix-6, codex BLOCKER: --format had set noncreate and short-circuited the copy).
        brexpect("(H24e) branch -c <orphan> <dst> --format (copy + display) asks",
                 "git branch -c orphan-start copiedfmt --format=%(refname)", "deny")
        # H29: a SEPARATED option value pollutes operand identification, so a copy combined with any
        # non-clean option ASKS rather than probing the wrong source (maximally-conservative, codex BLOCKER).
        brexpect("(H29) branch --format main -c <orphan> <dst> asks (separated value)",
                 "git branch --format main -c orphan-start copsep", "deny")
        # H30: --ours is checkout-only; on switch it is unrecognized, so a switch creation carrying it ASKS
        # rather than probe-denying a git-error command (maximally-conservative, codex MINOR).
        brexpect("(H30) switch -c <name> --ours <orphan> asks (checkout-only option on switch)",
                 "git switch -c h30 --ours orphan-start", "deny")
        # H31: -v/--verbose is a git-branch-only option; on checkout/switch/worktree git rejects it (creates
        # nothing), so it is NOT in the clean allowlist there and the creation ASKS (maximally-conservative,
        # codex MINOR: it was a shared clean boolean and false-denied a git-error command).
        brexpect("(H31a) checkout -v -b <name> <orphan> asks (branch-only -v)",
                 "git checkout -v -b h31 orphan-start", "deny")
        brexpect("(H31b) branch -v <name> <orphan> still probes (branch DOES expose -v)",
                 "git branch -v h31b orphan-start", "deny")
        # H32: --show-current / --edit-description are recognized non-creation actions -> allow (they match
        # the residue's non-creation ALLOW claim; codex MINOR: they had ASKed).
        brexpect("(H32a) branch --show-current allows",
                 "git branch --show-current", "allow")
        brexpect("(H32b) branch --edit-description allows",
                 "git branch --edit-description somebranch", "allow")
        # H24f: git checkout -b with --detach is a git error (creates nothing); the hook must not probe-deny
        # a command that creates no branch (redesign-fix-6, codex MINOR).
        brexpect("(H24f) checkout -b <name> --detach asks (order-dependent detach, no silent allow)",
                 "git checkout -b h24f --detach orphan-start", "deny")
        brexpect("(H24g) checkout --detach --no-detach -b creation asks (no silent allow)",
                 "git checkout --detach --no-detach -b h24g orphan-start", "deny")
        # H25: a `git worktree add --detach` creates NO branch, so it is allowed even from an orphan
        # (round-6 codex MAJOR: --detach was over-denied).
        # H25: --detach is option-order-dependent (a later --no-detach flips it, git applies last), so a
        # detach form is not confidently classifiable and fail-safe ASKS (redesign-fix-7, codex --no-detach
        # BLOCKER); never a silent allow of a --detach --no-detach -b creation.
        brexpect("(H25a) worktree add --detach <path> <orphan> asks",
                 "git worktree add --detach /tmp/wt-detach orphan-start", "deny")
        brexpect("(H25b) worktree add --detach --no-detach -b <name> creation asks (no silent allow)",
                 "git worktree add --detach --no-detach -b h25b /tmp/wt-nd orphan-start", "deny")

        # H26: fail-safe-ASK redesign (Architect 2026-09-02, round-7 cap). Any unclassifiable option shape
        # ASKs rather than silently allowing OR falsely denying; the confidently-clean forms still probe.
        brexpect("(H26a) branch --list --no-list (negation flips classifier) asks",
                 "git branch --list --no-list qa orphan-start", "deny")
        brexpect("(H26b) branch --delete --no-delete (negation) asks",
                 "git branch --delete --no-delete qa orphan-start", "deny")
        brexpect("(H26c) branch --unknown-flag creation asks",
                 "git branch --unknown-flag qa orphan-start", "deny")
        brexpect("(H26d) checkout --patch (benign unrecognized) asks (fail-safe)",
                 "git checkout --patch somefile", "deny")
        brexpect("(H26e) worktree add -d (short --detach) asks",
                 "git worktree add -d /tmp/wt-shortd orphan-start", "deny")
        brexpect("(H26f) branch -lv (version-ambiguous -l) asks",
                 "git branch -lv somepattern", "deny")

        # === L11: shared raw-aware tokenizer regression matrix (direct _lex_command assertions) =======
        # Assert the exact cleaned argv and the redirect metadata, BEFORE the handler-level vectors, so a
        # future tokenizer regression is caught at the tokenizer, not only through a handler outcome.
        def _argv(command):
            return [seg.argv for seg in aiqt_hooks._lex_command(command)]

        def _redir(command, index=0):
            return [(r.op, r.src_fd, r.target, r.target_class, r.stdout_effect)
                    for r in aiqt_hooks._lex_command(command)[index].redirects]

        def texpect(label, got, want):
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # A redirect anywhere (leading, interspersed, trailing) is removed from argv; a stderr redirect
        # keeps stdout on the console.
        texpect("(tok-1) 2>/dev/null before the subcommand is removed",
                _argv("git 2>/dev/null push --force origin main"),
                [["git", "push", "--force", "origin", "main"]])
        # A SPACED bare '2' is an operand, not an IO_NUMBER, and the '>' is the default stdout redirect.
        texpect("(tok-2) '2 > out' keeps '2' as argv, '>' is stdout",
                _argv("git 2 > out push --force origin main"),
                [["git", "2", "push", "--force", "origin", "main"]])
        # A quoted operator value is preserved as argv, never read as a redirect.
        texpect("(tok-3) quoted '>' option value stays argv",
                _argv("git push -o '>' --force origin main"),
                [["git", "push", "-o", ">", "--force", "origin", "main"]])
        # A quoted numeric option value stays argv; the later '>' is the stdout redirect (removed).
        texpect("(tok-4) quoted numeric option value '2' stays argv",
                _argv("git push --repo '2' > /dev/null --force origin main"),
                [["git", "push", "--repo", "2", "--force", "origin", "main"]])
        # A redirect TARGET '--' is removed and never becomes an argv boundary; a trailing '--help' after
        # it is an ordinary git argument.
        texpect("(tok-5) redirect target '--' is removed, '--help' stays argv",
                _argv("git push --force origin main > -- --help"),
                [["git", "push", "--force", "origin", "main", "--help"]])
        texpect("(tok-6) leading redirect before a non-git checker is removed",
                _argv("> /dev/null pytest || true"), [["pytest"], ["true"]])
        texpect("(tok-7) leading redirect before the git subcommand is removed",
                _argv("git >out commit --no-verify"), [["git", "commit", "--no-verify"]])
        # stdout effects and last-redirect-wins.
        texpect("(tok-8) '>out 2>&1': stdout=file-real, stderr dup does not change stdout",
                _redir("git diff >out 2>&1"),
                [(">", 1, "out", "file-real", "file-real"), (">&", 2, "1", "descriptor", "")])
        texpect("(tok-9) '2>&1 >out': last stdout redirect (>out) wins",
                _redir("git diff 2>&1 >out"),
                [(">&", 2, "1", "descriptor", ""), (">", 1, "out", "file-real", "file-real")])
        texpect("(tok-10) '>out 1>&2': stdout becomes descriptor-bound (unprovable)",
                _redir("git diff >out 1>&2"),
                [(">", 1, "out", "file-real", "file-real"), (">&", 1, "2", "descriptor", "descriptor")])
        texpect("(tok-11) '&>out': both streams to a real file",
                _redir("git diff &>out"), [("&>", None, "out", "file-real", "file-real")])
        texpect("(tok-12) '2>out': only stderr diverted, no stdout effect",
                _redir("git diff 2>out"), [(">", 2, "out", "file-real", "")])
        # An escaped or quoted operator-shaped word stays argv; a dynamic and a /dev target classify.
        texpect("(tok-13) escaped '\\>' stays argv",
                _argv(r"git diff \> file"), [["git", "diff", ">", "file"]])
        texpect("(tok-14) dynamic target classifies opaque",
                _redir("git diff > $OUT"), [(">", 1, "$OUT", "opaque", "opaque")])
        texpect("(tok-15) /dev target classifies file-dev",
                _redir("git diff > /dev/tty"), [(">", 1, "/dev/tty", "file-dev", "file-dev")])
        # A backslash-newline line-continuation JOINS and NEVER injects a synthetic empty argv element.
        texpect("(tok-16a) boundary backslash-newline is dropped, no empty argv element",
                _argv("git \\\n commit -m x"), [["git", "commit", "-m", "x"]])
        texpect("(tok-16b) mid-word backslash-newline joins the word",
                _argv("git di\\\nff"), [["git", "diff"]])
        texpect("(tok-16c) a genuine QUOTED empty operand '' is preserved (not a synthetic empty)",
                _argv("git commit -m ''"), [["git", "commit", "-m", ""]])
        # A '#' at a word boundary starts a comment; a mid-word '#' is literal.
        texpect("(tok-17a) word-boundary '#' comments out the rest of the line (redirect ignored)",
                (_argv("git diff # > /tmp/x"), _redir("git diff # > /tmp/x")),
                ([["git", "diff"]], []))
        texpect("(tok-17b) mid-word '#' stays literal",
                _argv("git commit -m ticket#123"), [["git", "commit", "-m", "ticket#123"]])
        # A '..'-normalized device target and a decoy-real target under '..' do not classify file-real.
        texpect("(tok-18a) '/tmp/../dev/stdout' normalizes to a /dev device target",
                _redir("git diff > /tmp/../dev/stdout"),
                [(">", 1, "/tmp/../dev/stdout", "file-dev", "file-dev")])
        texpect("(tok-18b) a '..' escape target is unprovable (opaque), never file-real",
                _redir("git diff > ../out.txt"), [(">", 1, "../out.txt", "opaque", "opaque")])
        # A RELATIVE dev/proc-leading target is cwd-dependent (from cwd '/' it IS the device): opaque, never
        # file-real. The ABSOLUTE form stays file-dev (tok-15); a nested 'dev' component stays a plain file.
        texpect("(tok-18c) relative 'dev/stdout' is cwd-dependent -> opaque, never file-real",
                _redir("git diff > dev/stdout"), [(">", 1, "dev/stdout", "opaque", "opaque")])
        texpect("(tok-18d) relative './proc/self/fd/1' normalizes to a proc-leading target -> opaque",
                _redir("git diff > ./proc/self/fd/1"), [(">", 1, "./proc/self/fd/1", "opaque", "opaque")])
        texpect("(tok-18e) a nested 'dev' component ('foo/dev/x') is a plain file, still file-real",
                _redir("git diff > foo/dev/x"), [(">", 1, "foo/dev/x", "file-real", "file-real")])
        # Unsupported constructs are cannot-evaluate (ValueError), never partial argv. ROUND-2 FINDING 17: an
        # UNQUOTED heredoc still raises (its body interpolates and stays in scope); a QUOTED heredoc does NOT
        # (its body is literal data, excluded from analysis - asserted separately below).
        for _bad, _why in [("git diff <<EOF\nx\nEOF", "unquoted heredoc"),
                           ("git diff <(echo x)", "process substitution"),
                           ("cat <<<word", "here-string"),
                           ('git diff "unbalanced', "unbalanced quote")]:
            try:
                aiqt_hooks._lex_command(_bad)
                failures.append("(tok-cannot) {} must raise ValueError, did not".format(_why))
            except ValueError:
                pass
        # ROUND-2 FINDING 17: a QUOTED heredoc (<<'EOF'/<<"EOF"/<<\\EOF) lexes cleanly with its body EXCLUDED,
        # so the lexical guards never fire on shell syntax quoted inside heredoc prose. The lexer sees only
        # the command word ('cat'), not the body's 'git diff | tail' / '|| true' / bare '&'.
        _f17_quoted = "cat <<'EOF'\ngit diff | tail\npytest || true\nrm -rf /  &\nEOF"
        try:
            _f17_segs = aiqt_hooks._lex_command(_f17_quoted)
            _f17_argvs = [s.argv for s in _f17_segs if s.argv]
            if _f17_argvs != [["cat"]]:
                failures.append("(f17-lex) a quoted heredoc must lex to just its command word, body "
                                "excluded; got {!r}".format(_f17_argvs))
        except ValueError:
            failures.append("(f17-lex-raise) a QUOTED heredoc must NOT raise (its body is literal data; "
                            "finding 17)")
        # No lexical Bash guard fires on the quoted-heredoc prose (each returns allow / allow-note, never a
        # deny caused by the body). git_discard's UNCONDITIONAL raw scan is heredoc-body-stripped too.
        for _f17g in (aiqt_hooks.diff_source_pretool, aiqt_hooks.gate_weakening,
                      aiqt_hooks.bash_absolute_paths, aiqt_hooks.git_explicit_binding,
                      aiqt_hooks.commit_msg_subst, aiqt_hooks.git_discard):
            _f17c, _f17o, _ = _f17g({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                     "tool_input": {"command": _f17_quoted}, "cwd": "/tmp"})
            _f17d = (_f17o.get("hookSpecificOutput", {}).get("permissionDecision")
                     if isinstance(_f17o, dict) else None)
            if _f17d == "deny":
                failures.append("(f17-guard-{}) a lexical guard must not fire on quoted-heredoc prose "
                                "(finding 17); got deny".format(getattr(_f17g, "__name__", _f17g)))
        # DISCRIMINATION: the SAME lossy verb OUTSIDE a quoted heredoc is still caught (body-strip preserves
        # everything outside the body), so the allow above is the body exclusion, not a blanket pass.
        _f17r, _f17ro, _ = aiqt_hooks.git_discard(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "git reset --hard <<'EOF'\nnote\nEOF"}, "cwd": brr})
        if not (isinstance(_f17ro, dict)):
            failures.append("(f17-outside) a real 'git reset --hard' OUTSIDE a quoted heredoc must still be "
                            "in scope (not a boundary allow); finding 17 preserves it")

        # === L11: additional diff-source vectors (the AIRTIGHT-NARROW contract) =======================
        dexpect("(l11-d1) bare git diff denies", "git diff", "deny")
        dexpect("(l11-d2) git show HEAD denies", "git show HEAD", "deny")
        dexpect("(l11-d3) git range-diff A B denies", "git range-diff A B", "deny")
        # cnsdif blob-form precision: 'git show <ref>:<path>' / ':<path>' is a file READ (cat against a
        # revision), NOT a diff dump -> ALLOW; a bare 'git show <commit>' still renders a diff -> covered.
        # Each blob ALLOW is fail-to-pass under a revert: without the _is_diff_producer show refinement AND
        # the _diff_proof_show_blob proof, the OLD code classified every 'git show ...' as a diff producer
        # and DENIED these. Judged by the structured verdict, never by grepping prose.
        dexpect("(l11-blob1) git show <ref>:<path> allows (blob read, the adopter repro)",
                "git show origin/plugin-manifests:.claude-plugin/plugin.json", "allow")
        dexpect("(l11-blob2) git show :<path> allows (index blob read)",
                "git show :path/to/file.json", "allow")
        dexpect("(l11-blob3) git show HEAD:src/x.py allows (blob read)", "git show HEAD:src/x.py", "allow")
        dexpect("(l11-blob4) git show --textconv HEAD:x.c allows (option skipped, sole operand is a blob)",
                "git show --textconv HEAD:x.c", "allow")
        dexpect("(l11-blob5) bare git show HEAD still DENIES (no colon operand -> a real diff)",
                "git show HEAD", "deny")
        dexpect("(l11-blob6) git show (no operand) still DENIES", "git show", "deny")
        dexpect("(l11-blob7) git diff still DENIES (unaffected by the blob refinement)", "git diff", "deny")
        dexpect("(l11-blob8) blob show in a compound is not a whole-command proof -> ASK (safe)",
                "git show ref:path && echo done", "allow")
        dexpect("(l11-blob9) 'git diff && git show ref:path' still DENIES (git diff is a dump)",
                "git diff && git show ref:path", "deny")
        # ALL-operands precision: 'git show' consumes N objects, so the blob proof admits it ONLY when
        # EVERY non-option operand is a blob selector. A trailing/leading bare ref, or a ':/<text>'
        # commit-message SEARCH, means git renders a FULL diff -> DENY. Each DENY below is fail-to-pass
        # under a revert: the OLD code inspected only the FIRST operand, so it classified these as blob
        # reads and ALLOWED them (verified live: 'git show HEAD:f HEAD' and 'git show :/' each emit
        # 'diff --git' + '@@' hunks). Judged by the structured verdict, never by grepping prose.
        dexpect("(l11-blob10) git show HEAD:a HEAD DENIES (bare-ref operand -> full diff; was ALLOW pre-fix)",
                "git show HEAD:a HEAD", "deny")
        dexpect("(l11-blob11) git show HEAD:a HEAD:b allows (every operand a blob selector -> file reads)",
                "git show HEAD:a HEAD:b", "allow")
        dexpect("(l11-blob12) git show :/text DENIES (commit-message SEARCH -> diff; was ALLOW pre-fix)",
                "git show :/text", "deny")
        dexpect("(l11-blob13) git show -O opt:val HEAD DENIES (bare-ref operand -> full diff; was ALLOW pre-fix)",
                "git show -O opt:val HEAD", "deny")
        # ROUND-2 FINDING 16: a value-taking option's SEPARATED value (e.g. '-L 1,3:file') contains a ':' but
        # is NOT a blob operand; skipping it leaves no blob operand, so the line-range DIFF is DENIED (was
        # ALLOWED - the value was misread as a blob selector). The attached '-L1,3:file' form denies too.
        dexpect("(f16-showL) git show -L 1,3:file DENIES (line-range diff; the -L value is not a blob "
                "operand; was ALLOW pre-fix)", "git show -L 1,3:file", "deny")
        dexpect("(f16-showL-attached) git show -L1,3:file DENIES (attached -L value; line-range diff)",
                "git show -L1,3:file", "deny")
        # DISCRIMINATION: a value-taking option before a GENUINE blob operand still ALLOWS (the value is
        # skipped, the blob read stands), so the deny above is the missing blob operand, not the ':' in -L.
        dexpect("(f16-showU-blob) git show -U 3 HEAD:file ALLOWS (the -U value is skipped; blob read stands)",
                "git show -U 3 HEAD:file", "allow")
        # classifier witness: '-L 1,3:file' is a diff producer (no blob operand once the -L value is skipped).
        if not aiqt_hooks._is_diff_producer(["git", "show", "-L", "1,3:file"]):
            failures.append("(f16-cls) _is_diff_producer must be True for 'git show -L 1,3:file' (finding 16)")
        # Discrimination at the classifier: the blob form is NOT a diff producer, the bare form IS, and a
        # blob-selector operand followed by a bare ref/commit or a ':/' search IS a producer.
        if aiqt_hooks._is_diff_producer(["git", "show", "HEAD:x.py"]):
            failures.append("(l11-blob-cls1) _is_diff_producer must be False for a blob-form git show")
        if not aiqt_hooks._is_diff_producer(["git", "show", "HEAD"]):
            failures.append("(l11-blob-cls2) _is_diff_producer must stay True for a bare 'git show <commit>'")
        if not aiqt_hooks._is_diff_producer(["git", "show", "HEAD:a", "HEAD"]):
            failures.append("(l11-blob-cls3) _is_diff_producer must be True when a bare-ref operand follows a blob")
        if aiqt_hooks._is_diff_producer(["git", "show", "HEAD:a", "HEAD:b"]):
            failures.append("(l11-blob-cls4) _is_diff_producer must be False when every operand is a blob selector")
        if not aiqt_hooks._is_diff_producer(["git", "show", ":/text"]):
            failures.append("(l11-blob-cls5) _is_diff_producer must be True for a ':/<text>' commit-message search")
        # ROUND-2 no-patch precision (cnsdif): 'git show -s' / '--no-patch' SUPPRESSES the patch (git prints
        # only the commit metadata + message, no diff), the same non-diff class as the --stat/--name-only
        # summary forms cnsdif already allows -> ALLOW. A co-present patch flag (-p/-u/--patch*) re-enables the
        # diff, so '-s -p' stays covered. Each -s ALLOW is fail-to-pass under a revert of the _show_no_patch_exempt
        # show branch (the OLD code classified any bare-ref 'git show ...' as a producer and DENIED it); the
        # '-s -p' DENY goes RED if the exemption ignores the patch flag. Judged by the structured verdict.
        # (The F-R2-2/F-R2-3 position/argument/cluster-aware discrimination rows are added further below.)
        dexpect("(cnsdif-nopatch1) git show -s --format metadata read allows (--no-patch class, no diff)",
                "git show -s --format='%H %an' HEAD", "allow")
        dexpect("(cnsdif-nopatch2) git show -s -p DENIES (co-present patch flag re-enables the diff)",
                "git show -s -p HEAD", "deny")
        dexpect("(cnsdif-nopatch3) bare git show HEAD still DENIES (regression guard; no -s)",
                "git show HEAD", "deny")
        # classifier witnesses: -s/--no-patch (without a patch flag) is NOT a producer; -s -p IS.
        if aiqt_hooks._is_diff_producer(["git", "show", "-s", "HEAD"]):
            failures.append("(cnsdif-nopatch-cls1) _is_diff_producer must be False for 'git show -s <ref>'")
        if aiqt_hooks._is_diff_producer(["git", "show", "--no-patch", "HEAD"]):
            failures.append("(cnsdif-nopatch-cls2) _is_diff_producer must be False for 'git show --no-patch <ref>'")
        if not aiqt_hooks._is_diff_producer(["git", "show", "-s", "-p", "HEAD"]):
            failures.append("(cnsdif-nopatch-cls3) _is_diff_producer must be True for 'git show -s -p <ref>' (patch flag re-enables)")
        # === F-R2-2/F-R2-3 (cnsdif git show): the no-patch exemption is POSITION/ARGUMENT/CLUSTER-aware. Each
        # ALLOW row emits NO patch (git prints only metadata) and is fail-to-pass under a revert of
        # _show_no_patch_exempt (the exemption removed -> the producer is DENIED); each DENY row DOES emit a
        # patch (or carries a patch-enabler / ambiguous / unknown / value-argument spelling that the OLD
        # exact-token scan mistook for non-diff) and is fail-to-pass under a revert (the OLD code exempted and
        # ALLOWED it - a console-diff ESCAPE). The GATE step-4 executed check runs the real `git show ...` and
        # confirms patch-vs-no-patch against these decisions. Judged by the structured verdict, never prose. ===
        # ALLOW: a genuine suppression flag, no patch enabler, only recognized non-patch options.
        dexpect("(fr2-show-a1) git show -s HEAD allows", "git show -s HEAD", "allow")
        dexpect("(fr2-show-a2) git show -s --format quoted allows (token path; quotes fail metachar proofs)",
                "git show -s --format='%H' HEAD", "allow")
        dexpect("(fr2-show-a3) git show --no-patch HEAD allows", "git show --no-patch HEAD", "allow")
        dexpect("(fr2-show-a4) git show -s --stat HEAD allows (summary + suppress)",
                "git show -s --stat HEAD", "allow")
        dexpect("(fr2-show-a5) git show --stat -s HEAD allows (order-independent)",
                "git show --stat -s HEAD", "allow")
        dexpect("(fr2-show-a6) git show -s --raw HEAD allows (raw listing + suppress)",
                "git show -s --raw HEAD", "allow")
        # DENY: a patch enabler present (a full or combined patch is emitted), or an ambiguous/unknown/value-
        # argument spelling that must NOT earn the exemption (conservative cover).
        dexpect("(fr2-show-d1) git show -s -p HEAD DENIES (patch enabler)", "git show -s -p HEAD", "deny")
        dexpect("(fr2-show-d2) git show -p -s HEAD DENIES (patch enabler, order-independent)",
                "git show -p -s HEAD", "deny")
        dexpect("(fr2-show-d3) git show -sp HEAD DENIES (short cluster containing p)",
                "git show -sp HEAD", "deny")
        dexpect("(fr2-show-d4) git show HEAD -p DENIES (-p in an option position after an operand)",
                "git show HEAD -p", "deny")
        dexpect("(fr2-show-d5) git show -s --patch-with-stat HEAD DENIES (--patch* enabler)",
                "git show -s --patch-with-stat HEAD", "deny")
        dexpect("(fr2-show-d6) git show -s --patch-with-raw HEAD DENIES (--patch* enabler)",
                "git show -s --patch-with-raw HEAD", "deny")
        dexpect("(fr2-show-d7) git show -s --patch HEAD DENIES (--patch enabler)",
                "git show -s --patch HEAD", "deny")
        dexpect("(fr2-show-d8) git show -s --patch=true HEAD DENIES (--patch* family)",
                "git show -s --patch=true HEAD", "deny")
        dexpect("(fr2-show-d9) git show -s -U3 HEAD DENIES (-U emits a patch; ESCAPE pre-fix)",
                "git show -s -U3 HEAD", "deny")
        dexpect("(fr2-show-d10) git show --no-patch --unified=3 HEAD DENIES (--unified emits a patch; ESCAPE)",
                "git show --no-patch --unified=3 HEAD", "deny")
        dexpect("(fr2-show-d11) git show -s -sp HEAD DENIES (a later cluster with p re-enables; ESCAPE)",
                "git show -s -sp HEAD", "deny")
        dexpect("(fr2-show-d12) git show HEAD -- file.txt -s DENIES (the -s is a post-'--' pathspec; ESCAPE)",
                "git show HEAD -- file.txt -s", "deny")
        dexpect("(fr2-show-d13) git show -S -s HEAD DENIES (the -s is -S's pickaxe ARGUMENT; ESCAPE)",
                "git show -S -s HEAD", "deny")
        dexpect("(fr2-show-d14) git show -G --no-patch HEAD DENIES (the --no-patch is -G's ARGUMENT; ESCAPE)",
                "git show -G --no-patch HEAD", "deny")
        dexpect("(fr2-show-d15) git show HEAD -- file.txt --no-patch DENIES (post-'--' pathspec; ESCAPE)",
                "git show HEAD -- file.txt --no-patch", "deny")
        dexpect("(fr2-show-d16) git show HEAD -- -s DENIES (a tracked file named -s, an operand; ESCAPE)",
                "git show HEAD -- -s", "deny")
        # classifier witnesses at _is_diff_producer for the key escapes (True == producer == covered):
        for _wargs, _wlbl in (
                (["-s", "-U3", "HEAD"], "-U emits a patch"),
                (["--no-patch", "--unified=3", "HEAD"], "--unified emits a patch"),
                (["-s", "-sp", "HEAD"], "a later p-cluster re-enables"),
                (["HEAD", "--", "file.txt", "-s"], "-s is a post-'--' pathspec"),
                (["-S", "-s", "HEAD"], "-s is -S's argument"),
                (["-G", "--no-patch", "HEAD"], "--no-patch is -G's argument")):
            if not aiqt_hooks._is_diff_producer(["git", "show"] + _wargs):
                failures.append("(fr2-show-cls) _is_diff_producer must be True for 'git show {}' ({})"
                                .format(" ".join(_wargs), _wlbl))
        for _wargs, _wlbl in (
                (["-s", "HEAD"], "suppress only"),
                (["--no-patch", "HEAD"], "suppress only"),
                (["-s", "--raw", "HEAD"], "suppress + raw listing"),
                (["-s", "--stat", "HEAD"], "suppress + summary")):
            if aiqt_hooks._is_diff_producer(["git", "show"] + _wargs):
                failures.append("(fr2-show-cls) _is_diff_producer must be False for 'git show {}' ({})"
                                .format(" ".join(_wargs), _wlbl))
        # === F-R2-5 (cnsdif SUMMARY classifier): _diff_emits_only_summary is now ARGUMENT-AWARE about patch
        # enablers, so a summary flag co-present with -U/--unified or a p/u short cluster is NOT summary-only
        # (git EMITS a patch, verified against real git) and DENIES rather than allow-noting. Judged by the
        # structured verdict, never prose. The three escapes below were ALLOW-with-note pre-fix. ===
        dexpect("(fr2-sum-d1) git show -s --stat -U3 HEAD DENIES (-U enables a patch; ESCAPE pre-fix)",
                "git show -s --stat -U3 HEAD", "deny")
        dexpect("(fr2-sum-d2) git show --no-patch --stat --unified=3 HEAD DENIES (--unified enables; ESCAPE)",
                "git show --no-patch --stat --unified=3 HEAD", "deny")
        dexpect("(fr2-sum-d3) git show -s --stat -sp HEAD DENIES (a p short cluster re-enables; ESCAPE)",
                "git show -s --stat -sp HEAD", "deny")
        dexpect("(fr2-sum-d4) git show -s --stat -p HEAD DENIES (-p patch flag; regression guard, was deny)",
                "git show -s --stat -p HEAD", "deny")
        dexpect("(fr2-sum-a1) git show -s --stat HEAD allows (genuine summary-only, no patch enabler)",
                "git show -s --stat HEAD", "allow")
        # classifier witnesses: _diff_emits_only_summary must be FALSE (not summary-only) for the escapes and
        # TRUE for a genuine summary-only command; _argv_has_patch_enabler discriminates the enabler directly.
        for _sargs, _slbl in (
                (["-s", "--stat", "-U3", "HEAD"], "-U cluster enables a patch"),
                (["--no-patch", "--stat", "--unified=3", "HEAD"], "--unified enables a patch"),
                (["-s", "--stat", "-sp", "HEAD"], "a p short cluster re-enables"),
                (["-s", "--stat", "-p", "HEAD"], "-p patch flag")):
            if aiqt_hooks._diff_emits_only_summary(["git", "show"] + _sargs):
                failures.append("(fr2-sum-cls) _diff_emits_only_summary must be False for 'git show {}' ({})"
                                .format(" ".join(_sargs), _slbl))
            if not aiqt_hooks._argv_has_patch_enabler(["git", "show"] + _sargs):
                failures.append("(fr2-sum-enb) _argv_has_patch_enabler must be True for 'git show {}' ({})"
                                .format(" ".join(_sargs), _slbl))
        for _sargs, _slbl in (
                (["-s", "--stat", "HEAD"], "summary + suppress, no enabler"),
                (["--cc", "--stat", "HEAD"], "--cc is merge-only, NOPATCH on a non-merge, stays summary"),
                (["-M", "--stat", "HEAD"], "-M rename detection is not a patch enabler")):
            if not aiqt_hooks._diff_emits_only_summary(["git", "show"] + _sargs):
                failures.append("(fr2-sum-cls) _diff_emits_only_summary must be True for 'git show {}' ({})"
                                .format(" ".join(_sargs), _slbl))
            if aiqt_hooks._argv_has_patch_enabler(["git", "show"] + _sargs):
                failures.append("(fr2-sum-enb) _argv_has_patch_enabler must be False for 'git show {}' ({})"
                                .format(" ".join(_sargs), _slbl))
        dexpect("(l11-d4) sudo git diff asks (wrapper)", "sudo git diff", "allow")
        dexpect("(l11-d5) command /usr/bin/git show asks (wrapper + path)",
                "command /usr/bin/git show", "allow")
        dexpect("(l11-d6) quote-fragmented g'it' d'iff' denies (cleaned argv resolves directly)",
                "g'it' d'iff'", "deny")
        dexpect("(l11-d7) 'git status && env git diff' asks (compound + wrapper)",
                "git status && env git diff", "allow")
        dexpect("(l11-d8) reverse-order 'env git diff && git status' asks",
                "env git diff && git status", "allow")
        dexpect("(l11-d9) echo 'git diff' asks (disclosed broad-scope over-match)",
                "echo 'git diff'", "allow")
        dexpect("(l11-d10) env git status allows (no producer surface)", "env git status", "allow")
        # Exact summary selectors allow; extra summary modifiers ASK.
        dexpect("(l11-s1) git diff --stat allows", "git diff --stat", "allow")
        dexpect("(l11-s2) git show --name-only HEAD allows", "git show --name-only HEAD", "allow")
        dexpect("(l11-s3) git diff --numstat allows", "git diff --numstat", "allow")
        dexpect("(l11-s4) git diff --stat --no-patch allows (--no-patch is the sole extra option)",
                "git diff --stat --no-patch", "allow")
        dexpect("(l11-s5) -M --stat asks", "git diff -M --stat", "allow")
        dexpect("(l11-s6) -U3 --stat DENIES (-U enables a patch even with --stat; verified PATCH vs real "
                "git, F-R2-5)", "git diff -U3 --stat", "deny")
        dexpect("(l11-s7) --cc --stat asks", "git show --cc --stat", "allow")
        dexpect("(l11-s8) --stat=80 asks (not an exact selector)", "git diff --stat=80", "allow")
        dexpect("(l11-s9) --stat -p denies (patch flag)", "git diff --stat -p", "deny")
        dexpect("(l11-s10) -- --stat denies (pathspec, not a summary)", "git diff -- --stat", "deny")
        dexpect("(l11-s11) git stash show --stat allows", "git stash show --stat", "allow")
        # Exact help allows; help as an option value/redirect target does not earn help ALLOW.
        dexpect("(l11-h1) git diff --help allows", "git diff --help", "allow")
        dexpect("(l11-h2) git range-diff -h allows", "git range-diff -h", "allow")
        dexpect("(l11-h3) git diff --stat --help asks (extra option, not the exact help form)",
                "git diff --stat --help", "allow")
        # Real-file / fd / last-wins diversions.
        dexpect("(l11-c1) leading '>out.patch git diff' allows", ">out.patch git diff", "allow")
        dexpect("(l11-c2) interspersed 'git >out.patch diff' allows", "git >out.patch diff", "allow")
        dexpect("(l11-c3) quoted-target 'git diff 1>>\"review out.patch\"' allows",
                'git diff 1>>"review out.patch"', "allow")
        dexpect("(l11-c4) 'git diff &>out.patch' allows", "git diff &>out.patch", "allow")
        dexpect("(l11-c5) 'git diff >out 2>&1' allows", "git diff >out 2>&1", "allow")
        dexpect("(l11-c6) 'git diff >out 1>&2' asks (stdout descriptor-bound, unprovable)",
                "git diff >out 1>&2", "allow")
        dexpect("(l11-c7) 'git diff >/dev/tty >out' allows (last-wins real file)",
                "git diff >/dev/tty >out", "allow")
        dexpect("(l11-c8) 'git diff >out >/dev/tty' denies (last-wins console)",
                "git diff >out >/dev/tty", "deny")
        dexpect("(l11-c9) 'git diff 2 >out' allows (2 is argv, stdout diverted)",
                "git diff 2 >out", "allow")
        dexpect("(l11-c10) 'git diff 2>out' denies (only stderr diverted)", "git diff 2>out", "deny")
        dexpect("(l11-c11) dynamic target 'git diff > $OUT' asks", "git diff > $OUT", "allow")
        dexpect("(l11-c12) tilde target 'git diff > ~/out.patch' asks", "git diff > ~/out.patch", "allow")
        # A RAW /dev,/proc-prefixed target is a device and DENIES even if a '..' would normalize elsewhere:
        # conservative raw-prefix classification over-blocks in the SAFE direction (round-3 codex note).
        dexpect("(l11-c13) raw '/dev/..' target 'git diff > /dev/../tmp/out.txt' denies (over-block, safe)",
                "git diff > /dev/../tmp/out.txt", "deny")
        # Exact terminal pager allows; wrapped/optioned/downstream pager variants ASK.
        dexpect("(l11-p1) git diff | less allows", "git diff | less", "allow")
        dexpect("(l11-p2) git diff | less -R asks", "git diff | less -R", "allow")
        dexpect("(l11-p3) git diff | env less asks", "git diff | env less", "allow")
        dexpect("(l11-p4) git diff | less | cat asks (later pipe)", "git diff | less | cat", "allow")
        dexpect("(l11-p5) git diff |& less asks", "git diff |& less", "allow")
        dexpect("(l11-p6) env git diff | less asks (wrapped stage 1)", "env git diff | less", "allow")
        # A pipe to a known console/truncating sink is a confirmed dump -> DENY.
        dexpect("(l11-p7) git diff | cat denies", "git diff | cat", "deny")
        dexpect("(l11-p8) git diff | tee out.log denies", "git diff | tee out.log", "deny")
        dexpect("(l11-p9) git diff | head denies", "git diff | head", "deny")
        dexpect("(l11-p10) git diff | tail -20 denies", "git diff | tail -20", "deny")
        # Unparseable apparent producer ASKS (never a regex-earned allow); a non-producer allows.
        dexpect("(l11-f1) unparseable apparent producer asks", 'git diff "unbalanced', "allow")
        dexpect("(l11-f2) unparseable non-producer allows", 'ls -la "unbalanced', "allow")
        # Disclosed boundary lock: a non-git alias/name that omits a detectable git word is ALLOWED (the
        # guard targets git producers, not an arbitrary renamed tool).
        dexpect("(l11-r1) DISCLOSED boundary: a non-git 'mydiff' name allows (no git word to detect)",
                "mydiff --color", "allow")

        # === L11 proof E (Architect refinement): a benign 'git log' commit listing ALLOWS; a git log with
        # any extra/unknown flag, a patch flag, a redirect, or a pipe stays airtight-narrow ================
        dexpect("(l11-e1) bare git log allows (proof E: a listing, no diff)", "git log", "allow")
        dexpect("(l11-e2) git log --oneline allows (proof E)", "git log --oneline", "allow")
        dexpect("(l11-e3) git log --stat allows (proof B summary, unchanged)", "git log --stat", "allow")
        dexpect("(l11-e4) git log with a bare revision operand allows (proof E)", "git log main", "allow")
        dexpect("(l11-e5) git log --oneline with an operand allows (proof E)",
                "git log --oneline origin/main", "allow")
        dexpect("(l11-e6) git log with a post-'--' pathspec allows (proof E)", "git log -- src", "allow")
        dexpect("(l11-e7) git log --oneline | less allows (proof D pager, stage 1 is a producer)",
                "git log --oneline | less", "allow")
        dexpect("(l11-e8) git log --oneline > out.txt allows (proof C real-file diversion)",
                "git log --oneline > out.txt", "allow")
        # The value-free, provably-diff-free display/traversal flags are on the exact allowlist and ALLOW,
        # alone and combined (the classic inspection command).
        dexpect("(l11-e9a) git log --graph allows (benign traversal flag)", "git log --graph", "allow")
        dexpect("(l11-e9b) git log --decorate allows", "git log --decorate", "allow")
        dexpect("(l11-e9c) git log --no-decorate allows", "git log --no-decorate", "allow")
        dexpect("(l11-e9d) git log --abbrev-commit allows", "git log --abbrev-commit", "allow")
        dexpect("(l11-e9e) git log --reverse allows", "git log --reverse", "allow")
        dexpect("(l11-e9f) git log --all allows", "git log --all", "allow")
        dexpect("(l11-e10a) git log --graph --oneline --decorate --all allows (the classic listing)",
                "git log --graph --oneline --decorate --all", "allow")
        dexpect("(l11-e10b) benign flags with a bare operand allow",
                "git log --graph --abbrev-commit --reverse main", "allow")
        dexpect("(l11-e10c) git log --name-only allows via proof B (a file-list summary, like git diff "
                "--name-only), not proof E", "git log --name-only", "allow")
        # Value-taking / unknown / count flags are NOT proven benign -> ASK (Architect's airtight line):
        # a value-swallowing option is exactly the grammar this design refuses to parse.
        dexpect("(l11-e11) git log -5 asks (a numeric count flag is not on the allowlist)",
                "git log -5", "allow")
        dexpect("(l11-e11b) git log -n 5 asks (a value-taking count option)", "git log -n 5", "allow")
        dexpect("(l11-e11c) git log --author=x asks (a value-taking filter)", "git log --author=x", "allow")
        dexpect("(l11-e11d) git log --since=yesterday asks (value-taking)",
                "git log --since=yesterday", "allow")
        dexpect("(l11-e11e) git log --grep=fix asks (value-taking)", "git log --grep=fix", "allow")
        dexpect("(l11-e12) git log --format=%H asks (a value-taking option)",
                "git log --format=%H", "allow")
        dexpect("(l11-e12b) git log --graph --format=%H asks (a benign flag plus a value-taking one)",
                "git log --graph --format=%H", "allow")
        # The provably-hard cases: a pickaxe -G/-S WITHOUT -p shows no patch, but its diff behaviour depends
        # on a co-present -p this guard does not model, so it is NOT proven benign -> ASK (never ALLOW).
        dexpect("(l11-e13) git log -G foo asks (pickaxe, diff behaviour depends on -p, not proven benign)",
                "git log -G foo", "allow")
        dexpect("(l11-e14) git log -S foo asks (pickaxe, not proven benign)", "git log -S foo", "allow")
        dexpect("(l11-e15) git log -p still denies (confirmed console patch, unchanged)",
                "git log -p", "deny")
        dexpect("(l11-e16) git log -p --oneline denies (a patch flag co-present with a benign one)",
                "git log -p --oneline", "deny")
        dexpect("(l11-e16b) git log --graph -p denies (a patch flag overrides the benign traversal flag)",
                "git log --graph -p", "deny")
        dexpect("(l11-e17) a wrapped git log asks (proof E requires the literal command word git)",
                "env git log", "allow")
        dexpect("(l11-e18) git log in a compound denies when a later segment is a confirmed dump",
                "git log && git diff", "deny")
        dexpect("(l11-e19) git log --oneline HEAD~5 asks (the '~' is outside the conservative charset)",
                "git log --oneline HEAD~5", "allow")
        dexpect("(l11-e20) diff plumbing stays ASK, not benign (only git log gets proof E)",
                "git diff-tree", "allow")

        # === L11 QA fix round (tri-family blockers on PR #163). Each vector FAILS without its fix. =========
        # BLOCKER 2: an unquoted '#' at a word boundary is a comment; a redirect/pipe that is commented out
        # must NOT earn proof C/D (the diff goes to the CONSOLE). Mid-word '#' stays literal (gw-ba/bb).
        dexpect("(qa-b2a) '#'-commented redirect does not earn proof C -> console dump denies",
                "git diff HEAD^ HEAD # > /tmp/x", "deny")
        dexpect("(qa-b2b) '#'-commented pipe does not earn proof D -> console dump denies",
                "git diff # | less", "deny")
        dexpect("(qa-b2c) a mid-word '#' is still literal, not a comment (regression lock)",
                "git diff --output=out#1.txt", "allow")
        # COMPOSITION (backslash-newline + '#'): after a continuation join, a '#' now at a word boundary
        # must be re-recognized as a comment, so the commented-out redirect/pipe earns no proof C/D.
        dexpect("(qa-b2d) continuation then word-boundary '#' comments out the redirect -> console dump denies",
                "git diff \\\n# > out.txt", "deny")
        dexpect("(qa-b2e) continuation then '#' comments out the pipe -> console dump denies",
                "git diff \\\n# | less", "deny")
        texpect("(tok-16d) a continuation-exposed '#' starts a comment (no literal '#' argv, no redirect)",
                (_argv("git diff \\\n# > out.txt"), _redir("git diff \\\n# > out.txt")),
                ([["git", "diff"]], []))
        texpect("(tok-16e) a continuation-joined MID-word '#' stays literal (di\\<nl>ff#x -> diff#x)",
                _argv("git di\\\nff#x"), [["git", "diff#x"]])
        # ROUND-2 FINDING 15: ANSI-C $'...' quoting is LITERAL and deterministic, so $'g'it resolves to the
        # command word 'git' (bash runs 'git diff HEAD^ HEAD' to the console) and is now provably a console
        # diff dump -> DENY (the lexer no longer marks $'...' opaque; a genuine $VAR command word stays
        # opaque and allows, see qa-b3b). Was an allow-note on the over-conservative opaque command word.
        dexpect("(qa-b3a) $'g'it diff resolves to a real 'git diff' console dump -> DENIES (finding 15)",
                "$'g'it diff HEAD^ HEAD", "deny")
        dexpect("(qa-b3b) a $VAR command word beside a producer surface never ALLOWs",
                "$GIT diff HEAD", "allow")
        # BLOCKER 4: a --output/-o diversion means the shell redirect/pipe is a decoy; proofs C/D disabled.
        dexpect("(qa-b4a) --output=/dev/tty with a decoy real-file redirect denies (console dump)",
                "git diff --output=/dev/tty > realfile.txt", "deny")
        dexpect("(qa-b4b) git log -p --output=/dev/tty with a decoy redirect denies",
                "git log -p --output=/dev/tty > f.txt", "deny")
        dexpect("(qa-b4c) --output=/dev/tty piped to less denies (pager decoy)",
                "git diff --output=/dev/tty | less", "deny")
        dexpect("(qa-b4d) bare --output=/dev/tty denies (console)", "git diff --output=/dev/tty", "deny")
        dexpect("(qa-b4e) --output=realfile.txt asks (diverted to a file, not a proof-C shell redirect)",
                "git diff --output=realfile.txt", "allow")
        dexpect("(qa-b4f) separated --output realfile.txt asks", "git diff --output realfile.txt", "allow")
        # BLOCKER 5: a redirect target that resolves to a device via '..' or a relative path must NOT earn
        # proof C; a genuine plain-file redirect still ALLOWs.
        dexpect("(qa-b5a) '> /tmp/../dev/stdout' normalizes to a device -> denies",
                "git diff > /tmp/../dev/stdout", "deny")
        dexpect("(qa-b5b) '> ../../../dev/stdout' has an unprovable '..' escape -> asks",
                "git diff > ../../../dev/stdout", "allow")
        dexpect("(qa-b5c) a '..'-bearing non-device target is unprovable -> asks",
                "git diff > ../out.txt", "allow")
        dexpect("(qa-b5d) a genuine plain-file redirect still allows (no over-DENY regression)",
                "git diff > out.txt", "allow")
        dexpect("(qa-b5e) a relative sub-path plain file still allows", "git diff > sub/out.txt", "allow")
        # BLOCKER 5 (round 4): a RELATIVE dev/proc-leading redirect target is cwd-dependent (from cwd '/' or
        # via a dev/proc symlink it IS the device the absolute form names), so it must NOT earn proof C's
        # file-real ALLOW; it ASKS. The absolute form still DENIES; a nested 'dev' stays a plain-file ALLOW.
        dexpect("(qa-b5f) '> dev/stdout' is cwd-dependent (could be the device) -> asks, not a silent allow",
                "git diff > dev/stdout", "allow")
        dexpect("(qa-b5g) '> ./dev/stdout' normalizes to a dev-leading target -> asks",
                "git diff > ./dev/stdout", "allow")
        dexpect("(qa-b5h) '> proc/self/fd/1' is a relative proc-leading target -> asks",
                "git diff > proc/self/fd/1", "allow")
        dexpect("(qa-b5i) the absolute device form still denies (no under-block change)",
                "git diff > /dev/stdout", "deny")
        dexpect("(qa-b5j) a nested 'dev' component ('git diff > foo/dev/x') is a plain file -> allows",
                "git diff > foo/dev/x", "allow")

        # Force detection is VALUE-AWARE: a force spelling in an option-value position is not a flag.
        pexpect("(pl-v1) '-o --force' is the push-option value, not force",
                "git push -o --force origin main", "allow", cwd=plr)
        pexpect("(pl-v2) '--push-option --force' is its value, not force",
                "git push --push-option --force origin main", "allow", cwd=plr)
        # Operand ROLES: the first bare operand is the repository, never judged as a destination; and
        # the restored --recurse-submodules value-skip means a truly refspec-less force still probes.
        pexpect("(pl-w1) a remote literally named 'main' is not a protected refspec",
                "git push --force main my-feature", "allow", cwd=plr)
        pexpect("(pl-w2) '-f --recurse-submodules on-demand origin' is refspec-less: probes and denies on main",
                "git push -f --recurse-submodules on-demand origin", "deny", cwd=plr)
        # Fallback widening: a quote-anchored '+refspec' under a wrapper, and the delete spellings.
        pexpect("(pl-x1) a QUOTED +refspec under a wrapper asks via the fallback",
                "sudo git push origin '+main:main'", "deny", cwd=plr)
        pexpect("(pl-x2) a wrapped --delete of main asks via the fallback",
                "env git push --delete origin main", "deny", cwd=plr)
        pexpect("(pl-x3) an unparseable ':main' delete asks via the fallback",
                'git push origin :main "unbalanced', "deny", cwd=plr)
        # Disclosed residuals, witnessed so the residue cannot drift from reality: ANY benign parsed
        # git segment - earlier OR later - suppresses the wrapped-catch (best-effort, not chased),
        # and --dry-run with a force spelling over-denies (the safe direction).
        pexpect("(pl-r1) DISCLOSED residual: a benign git segment BEFORE a wrapped force-push allows",
                "git status && env git push --force origin main", "allow", cwd=plr)
        pexpect("(pl-r1b) DISCLOSED residual: a benign git segment AFTER a wrapped force-push also "
                "suppresses the wrapped-catch and allows",
                "env git push --force origin main && git status", "allow", cwd=plr)
        pexpect("(pl-r2) DISCLOSED over-deny: --dry-run --force to main still denies",
                "git push --dry-run --force origin main", "deny", cwd=plr)

        # === EN-5 PR-A round-4: matching/prune sweeps, the widened -d fallback, wrapped delete and
        # commit coverage, and the disclosed over-denies and lexical boundary, witnessed ===
        pexpect("(pl-y1) the matching refspec ':' asks (a sweep of every branch on both ends)",
                "git push origin :", "deny", cwd=plr)
        pexpect("(pl-y2) the forced matching refspec '+:' asks",
                "git push origin +:", "deny", cwd=plr)
        pexpect("(pl-y3) --prune with a wildcard refspec asks (deletes absent remote branches, "
                "no force flag)",
                "git push --prune origin 'refs/heads/*:refs/heads/*'", "deny", cwd=plr)
        pexpect("(pl-y3a) --prune --all asks (GD-145: deletes remote branches absent locally with "
                "no force flag and no command-line refspec, previously a silent allow)",
                "git push --prune --all origin", "deny", cwd=plr)
        pexpect("(pl-y3b) --branches --prune asks (GD-145: the --branches spelling of the same sweep)",
                "git push --branches --prune origin", "deny", cwd=plr)
        pexpect("(pl-y3c) --prune with the matching ':' refspec asks as a prune sweep (GD-145: judged "
                "before the plain matching case so its deletion effect is named)",
                "git push --prune origin :", "deny", cwd=plr)
        # pl-y3c-reason (GD-145): the pruning matching-refspec's ASK reason must name the prune
        # deletion, not the non-deleting matching explanation; this gates the prune-before-matching
        # reorder (the verdict is ASK under both orders, so only a reason check fails without the fix).
        _pm_data = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "tool_input": {"command": "git push --prune origin :"}, "cwd": plr}
        _pm_code, _pm_obj, _pm_err = plg(_pm_data)
        _pm_reason = _pm_obj.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") \
            if isinstance(_pm_obj, dict) else ""
        if "prune" not in _pm_reason:
            failures.append("(pl-y3c-reason) --prune origin : ASK reason must name the prune deletion, "
                            "got: {!r}".format(_pm_reason))
        pexpect("(pl-y4) a wrapped clustered '-dv' delete asks via the widened fallback",
                "env git push -dv origin main", "deny", cwd=plr)
        pexpect("(pl-y5) a wrapped git commit asks via the fallback",
                "sudo git commit -m 'fix'", "allow", cwd=plr)
        pexpect("(pl-z1) DISCLOSED over-deny: '--force --no-force' still denies (negation not "
                "modelled)", "git push --force --no-force origin main", "deny", cwd=plr)
        pexpect("(pl-z2) DISCLOSED over-deny: '--delete --no-delete' still denies",
                "git push --delete --no-delete origin main", "deny", cwd=plr)
        pexpect("(pl-z3) DISCLOSED over-deny: --force-if-includes alone (a documented no-op) denies",
                "git push --force-if-includes origin main", "deny", cwd=plr)
        pexpect("(pl-z4) DISCLOSED over-deny: --dry-run with a -d delete still denies",
                "git push --dry-run -d origin main", "deny", cwd=plr)
        pexpect("(pl-z5) DISCLOSED boundary: a shell-expanded destination is judged as the literal "
                "token, allows", 'git push --force origin "$BRANCH"', "allow", cwd=plr)

        # A deny in a later segment wins over an earlier ask; discard verbs are out of this scope.
        pexpect("(pl-l1) a later force-push deny wins over an earlier commit ask",
                "git commit -m 'x' && git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-m1) a git_discard verb is out of protected_line scope (disjoint controls)",
                "git reset --hard", "allow", cwd=plr)

        # === GD-146: command-local mirror-config guard (m1-m36) ==============================
        # A command-local `-c remote.<name>.mirror=true push` (or the key via --config-env) reproduces
        # --mirror's force-and-delete sweep with no bulk flag, so it ASKS with the same disposition. The
        # named-protected force/delete DENY still outranks it, and a falsy config never stands down an
        # explicit --mirror. Labels are gd146-mN (the plan's m1-m36; the bare pl-mN space collides with
        # the pl-m1 git_discard-scope case above). ASK/ALLOW cases run on the feature-branch fixture plf
        # (the clause fires before the HEAD probe), DENY interactions on plr.
        _mkey = "remote.origin.mirror"
        # Parsed path, fires (ASK).
        pexpect("(gd146-m1) '-c mirror=true' with an explicit refspec asks (beats the explicit-refspec "
                "early return)", "git -c remote.origin.mirror=true push origin main", "deny", cwd=plf)
        pexpect("(gd146-m2) '-c mirror=true' refspec-less asks (beats the no-force early return)",
                "git -c remote.origin.mirror=true push origin", "deny", cwd=plf)
        for _v in ("1", "yes", "on", "TRUE"):
            pexpect("(gd146-m3/{}) truthy spelling denies (fail-safe: unprovable protected-line rewrite)"
                    .format(_v),
                    "git -c remote.origin.mirror={} push origin".format(_v), "deny", cwd=plf)
        pexpect("(gd146-m4) a bare key is boolean true, asks",
                "git -c remote.origin.mirror push origin", "deny", cwd=plf)
        pexpect("(gd146-m5) case-insensitive section/key asks",
                "git -c ReMoTe.origin.MiRrOr=YeS push origin", "deny", cwd=plf)
        pexpect("(gd146-m6) a dotted remote name (startswith/endswith, not a 3-way split) asks",
                "git -c remote.a.b.mirror=true push origin", "deny", cwd=plf)
        pexpect("(gd146-m7) separated --config-env naming the key asks (value unreadable)",
                "git --config-env remote.origin.mirror=MFLAG push origin", "deny", cwd=plf)
        pexpect("(gd146-m8) attached --config-env=... naming the key asks",
                "git --config-env=remote.origin.mirror=MFLAG push origin", "deny", cwd=plf)
        pexpect("(gd146-m9) --config-env forces unknown regardless of a later direct falsy, asks",
                "git --config-env remote.origin.mirror=MFLAG -c remote.origin.mirror=false push origin",
                "deny", cwd=plf)
        pexpect("(gd146-m10) a not-provably-falsy value fires (git dies on the bad boolean; harmless "
                "over-ask)", "git -c remote.origin.mirror=maybe push origin", "deny", cwd=plf)
        pexpect("(gd146-m11) any truthy mirror key fires; the remote name is not resolved (locked "
                "over-ask)", "git -c remote.backup.mirror=true push origin", "deny", cwd=plf)
        pexpect("(gd146-m12) direct last-value-wins ends truthy, asks",
                "git -c remote.origin.mirror=false -c remote.origin.mirror=true push origin",
                "deny", cwd=plf)
        pexpect("(gd146-m13) per-key independence: one key's falsy never cancels another's truthy",
                "git -c remote.other.mirror=false -c remote.origin.mirror=true push origin",
                "deny", cwd=plf)
        pexpect("(gd146-m14) the '-c' region is pre-subcommand; the '--' operand boundary does not "
                "shield it", "git -c remote.origin.mirror=true push -- origin main", "deny", cwd=plf)
        # Parsed path, stands down (ALLOW).
        for _v in ("false", "0", "no", "off", ""):
            pexpect("(gd146-m15/{!r}) provably-falsy stands down, allows".format(_v),
                    "git -c remote.origin.mirror={} push origin".format(_v), "allow", cwd=plf)
        pexpect("(gd146-m16) direct last-value-wins ends falsy, allows (discriminates last-wins from "
                "any-truthy)",
                "git -c remote.origin.mirror=true -c remote.origin.mirror=false push origin",
                "allow", cwd=plf)
        pexpect("(gd146-m17a) a non-mirror key does not over-fire",
                "git -c color.ui=always push origin main", "allow", cwd=plf)
        pexpect("(gd146-m17b) another non-mirror key does not over-fire",
                "git -c user.name=x push origin", "allow", cwd=plf)
        for _k in ("remote.origin.mirrors=true", "remote.origin.fetch=true", "remotes.origin.mirror=true"):
            pexpect("(gd146-m18/{}) near-miss key does not fire".format(_k),
                    "git -c {} push origin".format(_k), "allow", cwd=plf)
        pexpect("(gd146-m19) an empty remote name (remote..mirror) never fires",
                "git -c remote..mirror=true push origin", "allow", cwd=plf)
        pexpect("(gd146-m20a) mirror text in a push-option value is not pre-subcommand config",
                "git push -o remote.origin.mirror=true origin main", "allow", cwd=plf)
        pexpect("(gd146-m20b) mirror text in an operand position is not config",
                "git push origin remote.origin.mirror=true", "allow", cwd=plf)
        pexpect("(gd146-m21) the clause lives in the push handler only (status is untouched)",
                "git -c remote.origin.mirror=true status", "allow", cwd=plf)
        pexpect("(gd146-m22) the existing info-flag skip still wins over the config",
                "git -c remote.origin.mirror=true push --help", "allow", cwd=plf)
        # Precedence and interaction.
        pexpect("(gd146-m23) named-protected forced refspec DENIES before the mirror ASK",
                "git -c remote.origin.mirror=true push origin +main:main", "deny", cwd=plr)
        pexpect("(gd146-m24) named-protected delete refspec DENIES before the mirror ASK",
                "git -c remote.origin.mirror=true push origin :main", "deny", cwd=plr)
        pexpect("(gd146-m25) a falsy config never stands down an explicit --mirror, asks",
                "git -c remote.origin.mirror=false push --mirror origin", "deny", cwd=plf)
        # Wording: the ASK detail names the concrete key and the effect (direct), or the unreadable env
        # value (--config-env). Extracted from the reason like the pl-y3c-reason check above.
        def _reason(command, cwd):
            _d = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": command}, "cwd": cwd}
            _c, _o, _e = plg(_d)
            return _o.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") \
                if isinstance(_o, dict) else ""
        _m26 = _reason("git -c remote.origin.mirror=true push origin", plf)
        if _mkey not in _m26 or "DELETES" not in _m26:
            failures.append("(gd146-m26) direct-config ASK detail must name the {} key and the "
                            "delete effect, got: {!r}".format(_mkey, _m26))
        _m27 = _reason("git --config-env remote.origin.mirror=MFLAG push origin", plf)
        if _mkey not in _m27 or "cannot read" not in _m27:
            failures.append("(gd146-m27) config-env ASK detail must name the key and say the value "
                            "cannot be read, got: {!r}".format(_m27))
        # Raw fallback (wrapped or unparseable): asks on the same spellings, anchored to the option token.
        pexpect("(gd146-m28) wrapped direct mirror config asks via the raw fallback",
                "env git -c remote.origin.mirror=true push origin", "deny", cwd=plf)
        pexpect("(gd146-m29) unparseable mirror config asks via the raw fallback",
                'git -c remote.origin.mirror=true push origin "unbalanced', "deny", cwd=plf)
        pexpect("(gd146-m30) wrapped attached --config-env asks via the raw fallback",
                "env git --config-env=remote.origin.mirror=MFLAG push origin", "deny", cwd=plf)
        pexpect("(gd146-m31) the raw path parses no values: a wrapped falsy config over-asks (accepted)",
                "env git -c remote.origin.mirror=false push origin", "deny", cwd=plf)
        pexpect("(gd146-m32) the raw pattern is anchored to the -c/--config-env option spelling: a "
                "push-option value allows", "env git push -o remote.origin.mirror=true origin main",
                "allow", cwd=plf)
        # Residual locks (ALLOW today; each cross-referenced to the section-5 disclosure so non-coverage
        # is asserted, not assumed). The guard reads no git config offline, so these stand down.
        pl_mcfg = _init_repo(tmp / "pl-mcfg")
        _git(pl_mcfg, "config", "remote.origin.mirror", "true")  # persisted file config: NOT read
        pexpect("(gd146-m33) a PERSISTED file-config mirror mode is a disclosed residual, allows",
                "git push origin", "allow", cwd=str(pl_mcfg))
        pexpect("(gd146-m34) the GIT_CONFIG_* env protocol (and the explicit-refspec ambient gap) is a "
                "disclosed residual, allows",
                "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.mirror GIT_CONFIG_VALUE_0=true "
                "git push origin main", "allow", cwd=plf)
        pexpect("(gd146-m35) an alias hiding the push (subcommand is 'p', not 'push') is a disclosed "
                "residual, allows", "git -c alias.p='push --mirror' p origin", "allow", cwd=plf)
        pexpect("(gd146-m36) a command-local remote.<name>.push refspec is a disclosed follow-on "
                "residual, allows", "git -c remote.origin.push=:main push origin", "allow", cwd=plf)
        # (gd146-m37, round-2 QA BLOCKER) the raw fallback requires a CONTIGUOUS '.mirror', so a shell
        # quote (or backslash) that FRAGMENTS THE KEY on a wrapped or unparseable command evades it and
        # ALLOWS, while git 2.53 reconstructs the key to mirror=true. Arbitrary shell fragmentation of the
        # option token, the key, or the value cannot be robustly matched by a regex (only a shell parser
        # could, which the pack deliberately avoids), so this is an inherent lexical-scan boundary, the same
        # class as the disclosed wrapper/alias/fragmented-command-word residuals. Disposition per
        # disclose-guard-residuals: DISCLOSE, not a regex arms-race; the non-coverage is stated in the
        # section-5 residue (manifest ~line 112), SYSTEM-HARDENING.md, and the _RAW_PUSH_MIRRORCFG_RE code
        # comment. This lock asserts it is DELIBERATE, not accidental; the PARSED path stays complete (m1-m25)
        # and network-side branch protection remains the backstop. Flip: contiguous 'remote.origin.mirror'
        # (m28) ASKS, so this ALLOW isolates the key-fragmentation boundary.
        pexpect("(gd146-m37) a KEY-FRAGMENTED wrapped raw mirror config is a DISCLOSED inherent "
                "lexical-scan residual, allows",
                "env git -c remote.origin.'mirror'=true push origin", "allow", cwd=plf)

        # === GD-146 round-1 tri-family QA fixes (a discriminating test per confirmed finding) =========
        # QA-BLOCKER (raw fallback): a shell quote BETWEEN the -c/--config-env option and its
        # remote.<name>.mirror key defeated the old raw pattern (it required the key IMMEDIATELY after the
        # flag + whitespace), so a wrapped/unparseable QUOTED mirror config silently ALLOWED (confirmed by
        # codex runtime probes on git 2.53). The widened quote-tolerant pattern ASKS; reverting the quote
        # tolerance ('-c[\s\x27"]+' -> '-c\s+', config-env '[=\s][\x27"]*' -> '[=\s]') makes all three fail.
        pexpect("(gd146-qa1r1) a WRAPPED quoted '-c' mirror config asks via the raw fallback",
                "env git -c 'remote.origin.mirror=true' push origin", "deny", cwd=plf)
        pexpect("(gd146-qa1r2) a WRAPPED quoted separated '--config-env' mirror key asks via the raw "
                "fallback", "env git --config-env 'remote.origin.mirror=MFLAG' push origin", "deny", cwd=plf)
        pexpect("(gd146-qa1r3) an UNPARSEABLE (unbalanced-quote) quoted '-c' mirror config asks via the "
                "raw fallback", 'git -c \'remote.origin.mirror=true\' push origin "unbalanced', "deny",
                cwd=plf)
        # QA-MAJOR (parsed path, per-remote key identity): m13 above (falsy 'other' FIRST, truthy 'origin'
        # LAST) does NOT discriminate - a mutant that collapses all remote identities to one, or case-folds
        # the case-sensitive subsection, still ends truthy ('origin' last) and ASKS. These two put the
        # TRUTHY key FIRST and a would-collapse FALSY key LAST, so under a collapsing or case-folding mutant
        # last-value-wins ends FALSY and ALLOWS, making the test (expecting ASK) fail - the proving flip.
        pexpect("(gd146-qa1k1) per-key: a DIFFERENT remote's falsy-last never cancels origin's truthy-first "
                "(fails under an identity-collapse mutant)",
                "git -c remote.origin.mirror=true -c remote.other.mirror=false push origin", "deny", cwd=plf)
        pexpect("(gd146-qa1k2) case-sensitive subsection: 'origin' truthy-first and 'ORIGIN' falsy-last are "
                "DISTINCT remotes, asks (fails under a subsection-case-fold OR identity-collapse mutant)",
                "git -c remote.origin.mirror=true -c remote.ORIGIN.mirror=false push origin", "deny", cwd=plf)

        # === L11 cross-hook redirect vectors (protected_line): the useful round-10 tests RESTORED via the
        # shared raw-aware tokenizer, plus the two regressions that caused the naive-strip revert ==========
        # A redirect anywhere no longer hides the subcommand, operand, or force flag.
        pexpect("(pl-l11a) redirect before 'push' does not hide the protected force-push, denies",
                "git 2>/dev/null push --force origin main", "deny", cwd=plr)
        pexpect("(pl-l11b) leading redirect before 'push' does not hide the force-push, denies",
                ">/dev/null git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-l11c) redirect before 'commit' does not hide the direct protected commit, asks",
                "git >/dev/null commit -m fix", "deny", cwd=plr)
        pexpect("(pl-l11d) trailing stderr redirect does not defeat the refspec-less HEAD probe, denies",
                "git push --force 2>/dev/null", "deny", cwd=plr)
        # The two round-10 regressions the naive post-tokenize strip mishandled: a QUOTED '>' option value
        # must stay the -o value (still catching the real --force), and a QUOTED numeric option value must
        # stay argv (not swallowed as a redirect fd), while an interspersed real redirect is still removed.
        pexpect("(pl-l11e) '-o \">\"' keeps the quoted '>' value and still catches force, denies",
                "git push -o '>' --force origin main", "deny", cwd=plr)
        pexpect("(pl-l11f) '--repo \"2\" > /dev/null --force' keeps numeric 2, real redirect removed, denies",
                "git push --repo '2' > /dev/null --force origin main", "deny", cwd=plr)
        # A redirect target '--'/'--help' is a filename, never an argv boundary or a help flag.
        pexpect("(pl-l11g) redirect-target '--help' does not mask a force-push (target removed)",
                "git push --force origin main > --help", "deny", cwd=plr)
        # QA BLOCKER 1: a backslash-newline line-continuation must NOT inject a synthetic empty argv
        # element (which mis-set the subcommand and defeated the guard); it joins as bash does -> DENY.
        pexpect("(qa-b1-pl) backslash-newline force-push does not slip via an empty argv element, denies",
                "git \\\n push --force origin main", "deny", cwd=plr)

        # gatdis (EN-5 PR-B): decision-signal battery for the gate-weakening guard.
        # === gate_weakening (gatdis): a git hook bypass + a swallowed or truncated checker ============
        # Purely lexical: no repo fixture and no probe, so no cwd is passed (the handler never reads it).
        gwg = aiqt_hooks.gate_weakening

        def gexpect(label, command, want):
            got = _decision(gwg, command)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # The DENY core: --no-verify on every verb that accepts it (verified against the git 2.53.0 man
        # pages), exact, abbreviated (conservative prefix), and the short -n ONLY where -n IS --no-verify.
        gexpect("(gw-a) commit --no-verify denies", "git commit --no-verify -m 'x'", "deny")
        gexpect("(gw-b) commit -n denies", "git commit -n -m 'x'", "deny")
        gexpect("(gw-b2) a -c global-option value is skipped, -n still found",
                "git -c user.email=x@example.invalid commit -n -m 'x'", "deny")
        gexpect("(gw-b3) a leading env assignment is skipped, -n still found",
                "GIT_EDITOR=true git commit -n -m 'x'", "deny")
        gexpect("(gw-c) clustered -an carries -n", "git commit -an -m 'x'", "deny")
        gexpect("(gw-d) push --no-verify denies even to a feature branch",
                "git push --no-verify origin my-feature", "deny")
        gexpect("(gw-e) merge --no-verify denies", "git merge --no-verify topic", "deny")
        gexpect("(gw-f) abbreviated --no-verif treated as the bypass (conservative prefix)",
                "git commit --no-verif -m 'x'", "deny")
        gexpect("(gw-g1) rebase --no-verify denies", "git rebase --no-verify origin/main", "deny")
        gexpect("(gw-g2) pull --no-verify denies", "git pull --no-verify", "deny")
        gexpect("(gw-g3) am -n denies (-n IS --no-verify on am)", "git am -n patch.mbox", "deny")

        # The -n discipline: -n is NOT the bypass on push (--dry-run) or merge (--no-stat), and an
        # option VALUE or post-'--' operand carrying the letter n is never read as the flag.
        gexpect("(gw-h) push -n is a dry run, allows", "git push -n origin main", "allow")
        gexpect("(gw-i) merge -n is --no-stat, allows", "git merge -n topic", "allow")
        gexpect("(gw-j) plain commit allows", "git commit -m 'fix: adjust'", "allow")
        gexpect("(gw-k) attached -m value containing -n allows", "git commit -m'-n note'", "allow")
        gexpect("(gw-l) separated --message value -n is consumed, allows",
                "git commit --message -n", "allow")
        gexpect("(gw-m) post-'--' pathspec -n is an operand, allows",
                "git commit -m 'x' -- -n", "allow")
        gexpect("(gw-n1) --no-verbose is not --no-verify", "git commit --no-verbose -m 'x'", "allow")
        gexpect("(gw-n2) git log -n is outside the verb roster", "git log -n 5", "allow")
        gexpect("(gw-n3) --no-verify-signatures is not the bypass",
                "git merge --no-verify-signatures topic", "allow")
        gexpect("(gw-n4) positive --verify allows", "git commit --verify -m 'x'", "allow")

        # ROUND-2 FINDING 14: the checker-shape swallow/truncate HEURISTIC is too broad (a benign optional
        # probe is common and is not a gate bypass), so it ALLOWS-WITH-NOTE, not deny. Only the CONFIRMED
        # --no-verify bypass above still DENIES. These previously denied; each now allows with a note.
        gexpect("(gw-o) pytest || true allows-with-note (heuristic relaxed, finding 14)", "pytest || true",
                "allow")
        gexpect("(gw-p) pytest || : allows-with-note (finding 14)", "pytest -q || :", "allow")
        gexpect("(gw-q) make test || true allows-with-note (runner + operand; finding 14)",
                "make test || true", "allow")
        gexpect("(gw-r) checker-named script || true allows-with-note (name parts; finding 14)",
                "tools/run_all_checks.sh || true", "allow")
        gexpect("(gw-s) pytest | head allows-with-note (truncating sink; finding 14)", "pytest | head",
                "allow")
        gexpect("(gw-t) pytest | tail -20 allows-with-note (finding 14)", "pytest -q | tail -20", "allow")
        gexpect("(gw-u) python -m pytest || true allows-with-note (runner -m module; finding 14)",
                "python -m pytest || true", "allow")
        gexpect("(gw-v) npm test || true allows-with-note (finding 14)", "npm test || true", "allow")

        # The heuristic stays scoped: non-checkers and non-truncating pipes are untouched.
        gexpect("(gw-w) checker with no swallow allows", "pytest && echo done", "allow")
        gexpect("(gw-x) non-checker || true allows (disclosed scoping choice)",
                "grep -q foo file.txt || true", "allow")
        gexpect("(gw-y) checker | tee is not truncating, allows", "pytest | tee out.log", "allow")
        gexpect("(gw-z) echo is not checker-shaped", "echo test || true", "allow")

        # Precedence and disjointness (mirrors pl-l1/pl-m1).
        gexpect("(gw-aa) a hook-bypass deny (--no-verify) still wins over an earlier swallow note (the "
                "confirmed bypass DENY is preserved by finding 14)",
                "pytest || true; git commit -n -m 'x'", "deny")
        gexpect("(gw-ab) a git_discard verb is out of gate_weakening scope (disjoint controls)",
                "git reset --hard", "allow")

        # Parse-error posture (unbalanced quote): fail-safe, never a silent allow.
        gexpect("(gw-ac) unparseable apparent --no-verify denies",
                'git commit --no-verify -m "unbalanced', "deny")
        gexpect("(gw-ad) unparseable commit -n asks (the raw scan cannot bind the cluster)",
                'git commit -n -m "unbalanced', "deny")
        gexpect("(gw-ae) unparseable checker + swallow allows-with-note (finding 14; fallback relaxed)",
                'pytest || true "unbalanced', "allow")
        gexpect("(gw-af) unparseable non-gate command allows", 'ls -la "unbalanced', "allow")

        # gatdis (EN-5 PR-B): additional brief coverage - the full verb roster in long form, the
        # short/abbrev spellings, the out-of-roster verbs, and the disclosed value-spelling over-deny.
        gexpect("(gw-ag) am --no-verify denies (exact long form)", "git am --no-verify", "deny")
        # (gw-ah) --no-ver is INTENDED conservative-long-prefix over-deny, NOT the catch of a real
        # bypass: --no-ver is ambiguous between --no-verify and --no-verbose, so git itself rejects it
        # and no bypass occurs; this is a safe-direction over-deny of an invalid command.
        gexpect("(gw-ah) --no-ver is a conservative-long-prefix over-deny of an ambiguous (git-rejected) "
                "option, not a real bypass", "git commit --no-ver -m 'x'", "deny")
        gexpect("(gw-ai) clustered -sn carries -n", "git commit -sn -m 'x'", "deny")
        gexpect("(gw-aj) pull -n is --no-stat, allows", "git pull -n", "allow")
        gexpect("(gw-ak) an -m value naming the no-verify word allows",
                'git commit -m "fix the no-verify flag"', "allow")
        gexpect("(gw-al) --no-verify-signatures on commit is not the bypass",
                "git commit --no-verify-signatures -m 'x'", "allow")
        gexpect("(gw-am) cherry-pick with a stray -n is out of the verb roster, allows",
                "git cherry-pick -n 0000", "allow")
        gexpect("(gw-an) revert with a stray -n is out of the verb roster, allows",
                "git revert -n 0000", "allow")
        gexpect("(gw-ao) a plain pytest with no swallow allows", "pytest", "allow")
        gexpect("(gw-ap) ruff check . || true allows-with-note (finding 14)", "ruff check . || true",
                "allow")
        gexpect("(gw-aq) npm test | tail allows-with-note (truncating sink; finding 14)", "npm test | tail",
                "allow")
        # DISCLOSED safe-direction over-deny (residue): a separated option VALUE that itself spells
        # --no-verify is scanned in the option region and DENIES; git would read it as the --message
        # value (no bypass), so this is a deliberate deny-direction over-match, never a silent allow.
        gexpect("(gw-ar) '--message --no-verify' over-denies (disclosed value-spelling residual)",
                "git commit --message --no-verify", "deny")

        # gatdis round-2 (F-123): the newline-continuation ASK-miss (F3) and the --resolvemsg
        # value-option over-block (F4) are fixed; the adjacency/regression locks hold; the disclosed
        # redirect-pollution slips (F1/F2, routed to the common enforcement-hook redesign) are pinned.
        gexpect("(gw-as) newline before the swallow allows-with-note (F3 adjacency held; finding 14)",
                "pytest ||\n true", "allow")
        gexpect("(gw-at) newline before the truncating sink allows-with-note (F3 held; finding 14)",
                "pytest |\n head", "allow")
        gexpect("(gw-au) am --resolvemsg -n consumes -n as the message value, allows (F4)",
                "git am --resolvemsg -n patch.mbox", "allow")
        gexpect("(gw-av) pytest || true allows-with-note (adjacency lock held; finding 14)",
                "pytest || true", "allow")
        gexpect("(gw-aw) pytest ; true allows (';' is not a swallow)", "pytest ; true", "allow")
        gexpect("(gw-ax) am -n still denies (bare short -n is the bypass)", "git am -n", "deny")
        # L11 shared raw-aware tokenizer: a redirect anywhere in the command is removed from argv, so the
        # former F1/F2 redirect-pollution slips are CLOSED. The bypass/checker is no longer hidden.
        gexpect("(gw-ay) redirect before the git subcommand no longer slips, denies (L11 tokenizer)",
                "git >/dev/null commit --no-verify -m x", "deny")
        gexpect("(gw-az) redirect before the checker word no longer slips; allows-with-note (L11 tokenizer "
                "still de-pollutes argv; finding 14)", ">/dev/null pytest || true", "allow")

        # gatdis round-3 (F-123 disclosed residuals): the shared L11 tokenizer (_lex_command) treats a '#'
        # as an ordinary word character (it does no comment-stripping), so an embedded unquoted '#' is lexed
        # literally and the bypass/swallow after it is no longer hidden - the class-wide embedded-# residual
        # stays CLOSED; the two contrived safe-direction over-blocks still DENY.
        gexpect("(gw-ba) embedded-# no longer hides the trailing --no-verify, denies (round-32 lexer fix)",
                "git commit -m ticket#123 --no-verify", "deny")
        gexpect("(gw-bb) embedded-# no longer hides the '|| true' swallow; allows-with-note (lexer still "
                "lexes it; finding 14)", "pytest foo#bar || true", "allow")
        gexpect("(gw-bc) a trailing --verify does not cancel --no-verify here, still denies "
                "(disclosed --verify-cancel over-block)", "git commit --no-verify --verify -m x", "deny")
        gexpect("(gw-bd) a clustered -hn reads 'n' as the bypass, still denies "
                "(disclosed clustered-help over-block)", "git commit -hn -m x", "deny")

        # === L11: the F1/F2 redirect-pollution slips are CLOSED by the shared raw-aware tokenizer =========
        # A redirect anywhere no longer hides the --no-verify bypass or the checker command word.
        gexpect("(gw-l11a) redirect before the subcommand no longer hides --no-verify, denies",
                "git >/dev/null commit --no-verify -m x", "deny")
        gexpect("(gw-l11b) trailing redirect does not hide --no-verify, denies",
                "git commit --no-verify -m x >/dev/null", "deny")
        gexpect("(gw-l11c) leading redirect before the checker no longer hides the swallow; allows-with-note "
                "(finding 14)", ">/dev/null pytest || true", "allow")
        gexpect("(gw-l11d) interspersed redirect does not hide the truncating sink; allows-with-note "
                "(finding 14)", "pytest 2>/dev/null | head", "allow")
        # A QUOTED redirect-shaped option value stays argv and does not mask the bypass verb.
        gexpect("(gw-l11e) a quoted '>' -m value does not hide the trailing --no-verify, denies",
                "git commit -m '>' --no-verify", "deny")
        # QA BLOCKER 1: a backslash-newline continuation must not inject an empty argv element that hid the
        # bypass subcommand from the gate-weakening scan -> DENY.
        gexpect("(qa-b1-gw) backslash-newline no-verify does not slip via an empty argv element, denies",
                "git \\\n commit --no-verify -m x", "deny")

        # GD-124: this BEST-EFFORT, ASK-only guard scans every token after a recognized git commit
        # subcommand. The handler reads _lex_command's per-token opacity without Git option binding; it
        # does not regex-scan the primary path. No repository fixture or probe is involved.
        cmg = aiqt_hooks.commit_msg_subst

        def cmexpect(label, command, want, tool="Bash"):
            got = _decision(cmg, command, tool=tool)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        def cmdataexpect(label, data, want):
            code, stdout_obj, _stderr = cmg(data)
            if code == 0 and stdout_obj is None:
                got = "allow"
            elif code == 0 and isinstance(stdout_obj, dict):
                got = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision", "unexpected")
            else:
                got = "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # ASK: separated, attached, clustered, repeated, compound, abbreviated, and split-unquoted forms.
        cmexpect("(cm-a) double-quoted backtick asks", 'git commit -m "fix `whoami` now"', "deny")
        cmexpect("(cm-b) double-quoted dollar-paren asks",
                 'git commit -m "fix $(rm -rf x) now"', "deny")
        cmexpect("(cm-c) attached -m value asks", 'git commit -m"fix `x` y"', "deny")
        cmexpect("(cm-d) attached --message value asks",
                 'git commit --message="fix `x` y"', "deny")
        cmexpect("(cm-e) separated --message value asks",
                 'git commit --message "fix $(x)"', "deny")
        cmexpect("(cm-f) clustered -am value asks", 'git commit -am "fix `x`"', "deny")
        cmexpect("(cm-g) repeated -m inspects every bound value",
                 'git commit -m ok -m "x $(y)"', "deny")
        cmexpect("(cm-h) compound isolates the commit segment",
                 'make build && git commit -m "ship `date`"', "deny")
        cmexpect("(cm-i) narrow unquoted split dollar-paren asks",
                 "git commit -m Built-$(date +%F)", "deny")
        cmexpect("(cm-j) unquoted backtick asks", "git commit -m `git clean -fdx`", "deny")
        cmexpect("(cm-k) arithmetic is a disclosed safe-direction over-ask",
                 'git commit -m "n=$((1+2))"', "deny")
        # ROUND-2 FINDING 15: ANSI-C $'...' quoting does NOT command-substitute, so a literal $( or backtick
        # inside it is a LITERAL message and now ALLOWS (was a disclosed safe-direction over-deny).
        cmexpect("(cm-l) ANSI-C $'...' literal $( is a literal message and ALLOWS (finding 15)",
                 "git commit -m $'a $(x) b'", "allow")
        cmexpect("(cm-l2) ANSI-C $'...' literal backtick is a literal message and ALLOWS (finding 15)",
                 "git commit -m $'Document `code` examples'", "allow")
        # DISCRIMINATION: the SAME markers in DOUBLE quotes DO substitute and still DENY, so the allow above
        # is attributable to ANSI-C quoting being literal, not to dropping backtick/$( detection.
        cmexpect("(cm-l3) the same $( in DOUBLE quotes still DENIES (substitutes; discriminates cm-l)",
                 'git commit -m "a $(x) b"', "deny")
        cmexpect("(cm-m) marker in an abbreviated long-option argument asks",
                 'git commit --mess "x `y`"', "deny")
        cmexpect("(cm-n) git global value option is skipped before commit",
                 'git -C /tmp commit -m "a `b`"', "deny")
        cmexpect("(cm-o) sibling and message substitutions are both in scope",
                 'git commit -F "$(x)" -m "y `z`"', "deny")
        cmexpect("(cm-p) unparseable apparent in-scope hit asks",
                 'git commit -m "unbalanced $(x)', "deny")
        cmexpect("(cm-help-tail) substitution before --help still asks",
                 'git commit -m "$(x)" --help', "deny")
        cmexpect("(cm-help-head) substitution after --help still asks",
                 'git commit --help -m "$(x)"', "deny")
        cmexpect("(cm-abbrev-shift) abbreviated sibling option cannot misbind a later marker",
                 'git commit --pathspec-from-f -F -m "$(printf x)"', "deny")
        cmexpect("(cm-fallback-attached) unparseable attached -m marker asks via raw fallback",
                 'git commit -mfoo$(printf x) <<<x', "deny")
        cmexpect("(cm-v) message-file argument substitution is in scope and asks",
                 'git commit -F "$(x)"', "deny")
        cmexpect("(cm-w) reuse/template argument substitutions are in scope and ask",
                 'git commit -C "$(x)" -c "`y`" -t "$(z)" -m ok', "deny")
        cmexpect("(cm-x) author argument substitution is in scope and asks",
                 'git commit --author="a `x` b" -m ok', "deny")
        # Round 3: scan every post-subcommand token. A `--` consumed as -m's value is not a boundary,
        # and even a genuine pathspec boundary does not stop Bash from executing a substitution.
        cmexpect("(cm-boundary-value) bare -- consumed as the first -m value cannot hide a later -m",
                 'git commit -m -- -m "$(x)"', "deny")
        cmexpect("(cm-boundary-quoted-value) quoted -- value cannot hide a later -m",
                 'git commit -m "--" -m "$(x)"', "deny")
        cmexpect("(cm-boundary-eoo-value) --end-of-options consumed as -m value cannot hide a later -m",
                 'git commit -m --end-of-options -m "$(x)"', "deny")
        # Resolve the effective command word through the hook-local command-modifier roster. These
        # modifiers still cause Bash to expand their arguments before the effective command runs.
        cmexpect("(cm-wrap-command) command-wrapped git commit asks",
                 'command git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-env) env-wrapped git commit asks", 'env git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-env-options) env options and assignments are skipped",
                 'env -i FOO=bar git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-exec) exec-wrapped git commit asks", 'exec git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-builtin) builtin-wrapped git commit asks",
                 'builtin git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-nohup) nohup-wrapped git commit asks", 'nohup git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-time) time-wrapped git commit asks", 'time git commit -m "$(x)"', "deny")
        cmexpect("(cm-wrap-negation) negated git commit asks", '! git commit -m "$(x)"', "deny")
        cmexpect("(cm-pathspec-subst) post-boundary pathspec substitution over-asks",
                 'git commit -- $(ls)', "deny")

        # ALLOW: safe literal forms and documented best-effort residuals.
        cmexpect("(cm-q) single-quoted substitution text allows",
                 "git commit -m 'fix $(rm -rf x) now'", "allow")
        cmexpect("(cm-r) escaped dollar-paren allows",
                 'git commit -m "fix \\$(rm -rf x)"', "allow")
        cmexpect("(cm-s) escaped backticks allow",
                 'git commit -m "use \\`code\\` please"', "allow")
        cmexpect("(cm-t) bare variable without a marker allows",
                 'git commit -m "release $VERSION"', "allow")
        cmexpect("(cm-u) marker-free ANSI-C value allows", "git commit -m $'plain'", "allow")
        cmexpect("(cm-marker-free) marker-free git commit allows", "git commit -m plain", "allow")
        cmexpect("(cm-help-clean) help without a substitution allows", "git commit --help", "allow")
        cmexpect("(cm-y) single-quoted post-boundary message-shaped operand remains opaque=False",
                 "git commit -m ok -- '-m `x`'", "allow")
        cmexpect("(cm-z) quoted prose naming git commit is parseably out of scope",
                 "echo 'git commit -m \"`x`\"'", "allow")
        cmexpect("(cm-aa) non-commit git allows", 'git status -m "x `y`"', "allow")
        cmexpect("(cm-ab) non-git command allows", "ls -la", "allow")
        cmexpect("(cm-ac) unparseable command without a marker allows",
                 'git commit -m "unbalanced', "allow")
        cmexpect("(cm-ad) present non-Bash tool allows",
                 'git commit -m "x `y`"', "allow", tool="Read")
        # DISCLOSED residuals: the lexer does not parse through a reserved-word compound to its inner
        # command, and an unquoted pre-subcommand $( fragments the git invocation into separate segments.
        cmexpect("(cm-residual-compound) reserved-word compound allows (documented residual)",
                 'if git commit -m "$(x)"; then :; fi', "allow")
        cmexpect("(cm-residual-global-subst) pre-subcommand unquoted $( fragments the segment and allows",
                 'git -C $(x) commit -m "$(y)"', "allow")

        # Payload postures: unreadable command ASKS; only the shared missing-tool contract DENIES.
        cmdataexpect("(cm-ae) missing command asks",
                     {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}, "deny")
        cmdataexpect("(cm-af) missing tool_name denies",
                     {"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}}, "deny")

        # === L11: git_discard redirect regression locks (prsunc) - a redirect is still non-pristine ->
        # ASK; no redirect may become a new ALLOW, and the clean-parse lossy scan sees redirect-free argv ==
        gd_repo = _init_repo(tmp / "gd-redir")
        (gd_repo / "dirty.txt").write_text("x\n")  # untracked -> a probed-dirty tree
        gdr = str(gd_repo)
        expect("(gd-l11a) redirected 'reset --hard' is non-pristine -> ASK (no new allow)",
               "git reset --hard >/dev/null", "allow", cwd=gdr)
        expect("(gd-l11b) redirected 'checkout' is non-pristine -> ASK",
               "git checkout -- dirty.txt 2>/dev/null", "allow", cwd=gdr)
        expect("(gd-l11c) redirected 'clean -f' is non-pristine -> ASK",
               "git clean -f >/dev/null", "allow", cwd=gdr)

        # === L11 cross-hook redirect vectors (commit_identity): a redirect no longer hides an AI --author,
        # a co-author trailer, or an identity assignment ==================================================
        cig = aiqt_hooks.commit_identity

        def ciexpect(label, command, want):
            got = _decision(cig, command)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        ciexpect("(ci-l11a) redirect before 'commit' does not hide an AI --author, denies",
                 'git >/dev/null commit --author="Claude <c@x>" -m x', "deny")
        ciexpect("(ci-l11b) a leading AI identity assignment survives, a trailing redirect is removed, denies",
                 "GIT_AUTHOR_NAME=Claude git commit -m x >/dev/null", "deny")
        ciexpect("(ci-l11c) an AI co-author trailer in a redirected commit denies",
                 'git commit -m "Co-Authored-By: Claude <c@x>" >/dev/null', "deny")
        ciexpect("(ci-l11d) a redirected commit with no AI identity allows",
                 "git commit -m fix >/dev/null", "allow")
        # QA BLOCKER 1: a backslash-newline continuation must not inject an empty argv element that hid the
        # commit subcommand (which had made the AI --author invisible) -> DENY.
        ciexpect("(ci-b1) backslash-newline AI-author commit does not slip via an empty argv element, denies",
                 "git \\\n commit --author='Claude <c@x>' -m y", "deny")

        # secsec (EN-5 PR-C): decision-signal battery for the secrets-shift-left guard.
        # === secrets_shift_left (secsec): an obvious hardcoded secret in a Write/Edit/Bash write-form ==
        # Purely lexical over the target text (the Write content, the Edit new_string, or the Bash
        # command), single-sourced from tools/check_secrets.py. Every secret value below is
        # CLEARLY-SYNTHETIC-BUT-SHAPE-MATCHING (a run of A/synthetic chars that fits the pattern shape),
        # never a real token (SECP synthetic-fixture-data). No repo fixture and no probe: the handler
        # reads only tool_input, so no cwd is passed.
        ssl = aiqt_hooks.secrets_shift_left

        def sexpect(label, tool, tool_input, want, secret=None):
            data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
            # Capture any DIRECT stdout/stderr the handler might emit, not only its returned tuple, so the
            # redaction guard covers the FULL emitted surface (a future handler that printed a value instead
            # of returning it would still be caught). The real secsec path emits only via its return
            # (no print/log/write), so these buffers are empty today.
            _cap_out, _cap_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(_cap_out), contextlib.redirect_stderr(_cap_err):
                code, stdout_obj, _stderr = ssl(data)
            if code == 0 and stdout_obj is None:
                got = "allow"
            elif code == 0 and isinstance(stdout_obj, dict):
                got = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision", "unexpected")
            else:
                got = "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))
            # redaction guard (F-131/F-134/F-136): a secsec decision names the pattern label but NEVER
            # echoes the secret value on ANY emitted surface - permissionDecisionReason, the systemMessage
            # banner (both in stdout_obj), stderr, or a direct stdout/stderr print. Runs for ANY case that
            # plants a secret (not only DENYs), so an allow path that echoed a value is caught too.
            if secret is not None:
                emitted = ((json.dumps(stdout_obj) if stdout_obj is not None else "") + (_stderr or "")
                           + _cap_out.getvalue() + _cap_err.getvalue())
                if secret in emitted:
                    failures.append("{}: output must not echo the secret value (redaction, F-131/F-134/F-136)".format(label))

        # Synthetic secret shapes (NOT real), assembled from PARTS at runtime so the shape never appears
        # as a contiguous literal in THIS source file: it uses the single-sourced check_secrets.py
        # patterns, so a literal here would itself trip the repo secret-scan gate. The provider values
        # are a split prefix plus a run of synthetic chars; the credential value is a plain 16-char
        # mixed literal, real-length but with no credential keyword beside it here (SECP
        # synthetic-fixture-data). _asgn builds a 'keyword = value' assignment at runtime, so the source
        # carries only the bare keyword string, never the scannable 'keyword = "<value>"' shape.
        _fake_ghp = "ghp_" + "A" * 30
        _fake_ant = "sk-ant-" + "A" * 30
        _fake_akia = "AKIA" + "A" * 16
        _fake_pkey = "-----BEGIN RSA PRIVATE" + " KEY-----"  # split so the header is not a literal here
        _cred = "aB3xY9kL2mN8qR5t"                           # 16 mixed alpha+digit chars, not a placeholder

        def _asgn(keyword, value):
            return "{} = {}".format(keyword, value)

        # DENY: a Write whose content carries each shape.
        sexpect("(ss-a) Write content with a GitHub token denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("token", _fake_ghp)}, "deny",
                secret=_fake_ghp)
        sexpect("(ss-b) Write content with an Anthropic key denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("key", _fake_ant)}, "deny",
                secret=_fake_ant)
        sexpect("(ss-c) Write content with an AWS access key id denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("aws", _fake_akia)}, "deny",
                secret=_fake_akia)
        sexpect("(ss-d) Write content with a private key block header denies",
                "Write", {"file_path": "/tmp/x", "content": _fake_pkey + "\nMIIB...\n"}, "deny",
                secret=_fake_pkey)
        sexpect("(ss-e) Write content with a credential assignment denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("api_key", '"' + _cred + '"')}, "deny",
                secret=_cred)
        # DENY: an Edit whose new_string introduces a secret.
        sexpect("(ss-f) Edit new_string with a GitHub token denies",
                "Edit", {"file_path": "/tmp/x", "old_string": "a", "new_string": _asgn("auth", _fake_ghp)},
                "deny", secret=_fake_ghp)
        # DENY: a Bash write-form (printf redirect, heredoc) emitting a secret in the command string.
        sexpect("(ss-g) Bash printf > f with a GitHub token denies",
                "Bash", {"command": "printf '%s' '" + _fake_ghp + "' > /tmp/f"}, "deny",
                secret=_fake_ghp)
        sexpect("(ss-h) Bash heredoc writing an AWS key denies",
                "Bash", {"command": "cat > /tmp/f <<EOF\n" + _fake_akia + "\nEOF"}, "deny",
                secret=_fake_akia)

        # ALLOW: a Write whose only credential-shaped values are PLACEHOLDERS (excluded exactly as
        # check_secrets.py excludes them; the first two are 12+ chars so they exercise the ASSIGN
        # placeholder-exclusion path, not merely the too-short no-match path).
        _placeholders = ('api_key = "<your-key-here>"\n'
                         'token = "${SOME_VARIABLE}"\n'
                         'password = "changeme"\n'
                         'secret = "example"\n')
        sexpect("(ss-i) Write content with placeholder values allows",
                "Write", {"file_path": "/tmp/x", "content": _placeholders}, "allow")
        # ALLOW: ordinary code/prose with no secret.
        sexpect("(ss-j) Write content with ordinary code allows",
                "Write", {"file_path": "/tmp/x", "content": "def add(a, b):\n    return a + b\n"}, "allow")
        # ALLOW: an Edit new_string with no secret.
        sexpect("(ss-k) Edit new_string with no secret allows",
                "Edit", {"file_path": "/tmp/x", "old_string": "a", "new_string": 'print("hello world")'},
                "allow")
        # ALLOW: a Read is not in the matcher, so it is out of scope (defensive allow).
        sexpect("(ss-l) Read (out of matcher scope) allows",
                "Read", {"file_path": "/tmp/x"}, "allow")
        # ALLOW: a Bash command with no secret.
        sexpect("(ss-m) Bash command with no secret allows",
                "Bash", {"command": "ls -la /tmp"}, "allow")
        # Fail-closed: a missing tool_name denies; an in-scope tool whose target field is absent denies.
        sexpect("(ss-n) missing tool_name denies (fail-closed)",
                None, {"content": _fake_ghp}, "deny", secret=_fake_ghp)
        sexpect("(ss-o) Write with no readable content denies (fail-closed)",
                "Write", {"file_path": "/tmp/x"}, "deny")

        # secsec round-2 (F-124/F-125)
        # F-124 (scan ALL assignments per line, not just the first): a placeholder assignment BEFORE a
        # real one on the SAME line must still DENY. Old .search stopped at the first (placeholder) match
        # and ALLOWED; finditer reaches the real second assignment and DENIES. The one-liner is assembled
        # from parts so this source file carries no scannable 'keyword = "<value>"' shape (SECP).
        _ph_then_real = (_asgn("token", '"<your-key-here>"') + "; "
                         + _asgn("password", '"' + _cred + '"'))
        sexpect("(ss-p) F-124 Write placeholder-then-real on one line denies",
                "Write", {"file_path": "/tmp/x", "content": _ph_then_real}, "deny", secret=_cred)
        sexpect("(ss-q) F-124 Edit placeholder-then-real on one line denies",
                "Edit", {"file_path": "/tmp/x", "old_string": "a", "new_string": _ph_then_real}, "deny", secret=_cred)
        sexpect("(ss-r) F-124 Bash placeholder-then-real on one line denies",
                "Bash", {"command": _ph_then_real}, "deny", secret=_cred)
        # F-125 (broadened private-key block regex): a DSA header and the PGP 'PRIVATE KEY BLOCK' form
        # now match; both were missed by the old (?:RSA |EC |OPENSSH |PGP )? alternation. Split so the
        # header is not a contiguous literal here (mirrors _fake_pkey above).
        _dsa_pkey = "-----BEGIN DSA PRIVATE" + " KEY-----"
        _pgp_pkey = "-----BEGIN PGP PRIVATE" + " KEY BLOCK-----"
        sexpect("(ss-s) F-125 Write DSA private key block header denies",
                "Write", {"file_path": "/tmp/x", "content": _dsa_pkey + "\nMIIB...\n"}, "deny", secret=_dsa_pkey)
        sexpect("(ss-t) F-125 Write PGP private key block header denies",
                "Write", {"file_path": "/tmp/x", "content": _pgp_pkey + "\nmQENB...\n"}, "deny", secret=_pgp_pkey)
        # Control: a placeholder-only one-liner still ALLOWS (both values are non-secrets: a placeholder
        # and a too-short 'example', so no assignment on the line is real).
        _ph_only = (_asgn("token", '"<your-key-here>"') + "; " + _asgn("api_key", '"example"'))
        sexpect("(ss-u) F-124 placeholder-only one-liner allows",
                "Write", {"file_path": "/tmp/x", "content": _ph_only}, "allow")

        # secsec round-3 (F-126/F-128): the two new provider-token variants, MultiEdit coverage, the
        # in-handler non-dict guard, and NotebookEdit out-of-scope. Shapes assembled from PARTS at
        # runtime so no contiguous token literal appears in this source (SECP; a literal would trip the
        # repo secret-scan). The sk-proj/xapp tokens stand ALONE with NO credential keyword beside them,
        # so the DENY is attributable to the NEW prefix pattern (F-126), not the credential-named ASSIGN.
        _fake_skproj = "sk-" + "proj-" + "A" * 30           # F-126 OpenAI project key; generic sk- cannot match
        _fake_xapp = "xapp-" + "1-A0123456789-" + "A" * 40  # F-126 Slack app-level token
        sexpect("(ss-v) F-126 Write with a bare sk-proj token denies",
                "Write", {"file_path": "/tmp/x", "content": _fake_skproj + "\n"}, "deny", secret=_fake_skproj)
        sexpect("(ss-w) F-126 Write with a bare xapp token denies",
                "Write", {"file_path": "/tmp/x", "content": _fake_xapp + "\n"}, "deny", secret=_fake_xapp)
        # MultiEdit (F-128a): the newline-joined new_string values of the edits are scanned. A ghp_ token
        # in any edit's new_string DENIES; an edits list with no secret ALLOWS.
        sexpect("(ss-x) F-128a MultiEdit whose edit introduces a secret denies",
                "MultiEdit", {"file_path": "/tmp/x",
                              "edits": [{"old_string": "a", "new_string": "hello world"},
                                        {"old_string": "b", "new_string": _asgn("auth", _fake_ghp)}]}, "deny", secret=_fake_ghp)
        sexpect("(ss-y) F-128a MultiEdit whose edits carry no secret allows",
                "MultiEdit", {"file_path": "/tmp/x",
                              "edits": [{"old_string": "a", "new_string": "def add(a, b):"},
                                        {"old_string": "b", "new_string": "    return a + b"}]}, "allow")
        # In-handler non-dict guard (F-128a hygiene): a non-dict tool_input for an in-scope tool fails
        # CLOSED cleanly (a _deny), not an AttributeError only the dispatcher would catch.
        sexpect("(ss-z) F-128a non-dict tool_input denies cleanly (fail-closed)",
                "Write", "not-a-dict", "deny")
        # Out of scope (F-128b disclosure): NotebookEdit is not in the matcher set, so it ALLOWS.
        sexpect("(ss-aa) NotebookEdit (out of scope) allows",
                "NotebookEdit", {"notebook_path": "/tmp/x.ipynb", "new_source": _fake_ghp}, "allow", secret=_fake_ghp)
        # secsec F-127 (environment lookup is not a credential literal): a credential-named variable
        # assigned a JavaScript-style env lookup (process.env.X) is a CODE REFERENCE, so it ALLOWS; a
        # dotted token that only LOOKS identifier-shaped but carries no env accessor - a HashiCorp Vault
        # hvs.<random> token, a dotted provider secret - is a real credential and still DENIES. Mirrors
        # check_secrets.py's DOTTED_PATH / _ENV_REF. Secret values assembled from parts (SECP).
        sexpect("(ss-ab) F-127 Write env lookup process.env.X allows",
                "Write", {"file_path": "/tmp/x", "content": _asgn("token", "process.env.OPENAI_KEY_V2")},
                "allow")
        _fake_vault = "hvs." + "CvmS4c0DPTvHv5eJgXWMJg9r"
        sexpect("(ss-ac) F-127 Write dotted Vault token still denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("token", _fake_vault)}, "deny",
                secret=_fake_vault)
        _fake_dotted = "prod.secret.auth." + "a1b2c3d4e5f6"
        sexpect("(ss-ad) F-127 Write dotted provider secret still denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("api_key", _fake_dotted)}, "deny",
                secret=_fake_dotted)
        _plus_tail = "process.env.OPENAI_KEY1" + "+" + "Abcdef1234567890"
        sexpect("(ss-ae) F-127 Write +-joined tail after an env-ref still denies (+ breaks the exclusion)",
                "Write", {"file_path": "/tmp/x", "content": _asgn("token", _plus_tail)}, "deny",
                secret=_plus_tail)
        sexpect("(ss-af) F-127 Write QUOTED env-ref still denies (exclusion is unquoted-only)",
                "Write", {"file_path": "/tmp/x",
                          "content": _asgn("token", '"' + "process.env.OPENAI_KEY_V2" + '"')}, "deny")
        # secsec F-127 PARITY: the shipped hook (_scan_secret) and the CI gate (check_secrets) must decide
        # every credential line identically. The exclusion is hand-mirrored (the generated region single-
        # sources only the regex strings, not this loop logic), so this guards against future drift.
        import check_secrets as _cs  # same tools/ dir
        def _cs_line_hit(_line):
            return any(_cs._assign_is_secret(_m) for _m in _cs.ASSIGN.finditer(_line))
        _parity_lines = [
            _asgn("token", "process.env.OPENAI_KEY_V2"),
            _asgn("api_key", "import.meta.env.VITE_API_KEY2"),
            _asgn("secret", "env.SECRET_VALUE2"),
            _asgn("token", "hvs." + "CvmS4c0DPTvHv5eJgXWMJg9r"),
            _asgn("api_key", "prod.secret.auth." + "a1b2c3d4e5f6"),
            _asgn("password", "process_env_KEY2"),
            _asgn("secret", '"' + "AbcDef123456ghiJ" + '"'),
            _asgn("token", "myorg.env.production." + "secretkey12345"),   # env not at root: caught by both
            _asgn("token", "process.env.OPENAI_KEY" + "+" + "Abcdef1234567890"),  # + breaks fullmatch: both
            _asgn("token", '"' + "process.env.OPENAI_KEY_V2" + '"'),  # quoted ROOT env-ref: caught by both
        ]
        for _pl in _parity_lines:
            _gate = _cs_line_hit(_pl)
            _hook = aiqt_hooks._scan_secret(_pl) is not None
            if _gate != _hook:
                failures.append("(ss-parity) gate/hook disagree: gate={} hook={} on a credential line".format(_gate, _hook))

        # secsec round-4 (F-129): the pattern drift gate must REJECT a target carrying more than one
        # generated BEGIN..END region. text.find inspects only the FIRST region, so a SECOND region (e.g.
        # a second _SECSEC_PREFIX_SOURCES = []) could override the patterns undetected while --check still
        # passed. gen_secret_patterns._splice now asserts EXACTLY ONE BEGIN and EXACTLY ONE END, raising
        # ValueError (which run() maps to a nonzero exit) otherwise; both the regen and --check paths reach
        # the region through _splice, so this guards both. A one-region text splices cleanly; a two-region
        # text must raise. Without the F-129 count guard, the two-region splice returns silently and this
        # case fails, so it is the durable check that fails without the change.
        _begin, _end = gen_secret_patterns.BEGIN, gen_secret_patterns.END
        _region = "{}\n_SECSEC_PREFIX_SOURCES = []\n{}".format(_begin, _end)
        _one_region = "prefix\n{}\nbody\n{}\nsuffix\n".format(_begin, _end)
        _two_region = _one_region + "{}\nsecond body\n{}\n".format(_begin, _end)
        try:
            gen_secret_patterns._splice(_one_region, _region)
        except ValueError as exc:
            failures.append("(ss-drift-a) single-region _splice unexpectedly raised: {}".format(exc))
        try:
            gen_secret_patterns._splice(_two_region, _region)
            failures.append("(ss-drift-b) two-region _splice did not raise; the drift gate would miss a "
                            "second generated region")
        except ValueError:
            pass

        # secsec round-5 (GD-121): the entropy-gated generic detector and the new provider families.
        # Shapes assembled from PARTS at runtime (SECP; a contiguous literal would trip the repo
        # secret-scan). `_hi` is a 48-char, 16-distinct high-entropy token (letters + digits); it clears
        # the 3.5 floor. Handler-level decisions plus a gate/hook agreement loop (the parity battery ask).
        def _n5(seed, length):
            return (seed * (length // len(seed) + 1))[:length]
        _hi = _n5("aB3dE7gH9kLmN2pQ", 48)
        _upper5 = "ABCDEFGHIJKLMNOP"
        # DENY: a bare `key` assigned a high-entropy literal (entropy detector, not the keyword ASSIGN).
        sexpect("(ss-ag) GD-121 Write high-entropy under a bare key denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("key", '"' + _hi + '"')}, "deny",
                secret=_hi)
        # DENY: unquoted broadened keyword (passphrase) with a high-entropy value.
        sexpect("(ss-ah) GD-121 Write unquoted passphrase high-entropy denies",
                "Write", {"file_path": "/tmp/x", "content": _asgn("passphrase", _n5("aB3dE7gH9kLmN2pQ", 40))},
                "deny")
        # DENY: new provider families (each stands alone; attributable to the NEW prefix, not the ASSIGN).
        _aiza = "AI" + "za" + _n5("aB3dE7gH9k", 35)
        _webhook = "https://hooks.slack.com" + "/services/" + "T" + _upper5 + "/B" + _upper5 + "/" + _n5("aB3dE7gH9k", 24)
        sexpect("(ss-ai) GD-121 Write Google AIza token denies",
                "Write", {"file_path": "/tmp/x", "content": _aiza + "\n"}, "deny", secret=_aiza)
        sexpect("(ss-aj) GD-121 Write Slack webhook URL denies",
                "Write", {"file_path": "/tmp/x", "content": _webhook + "\n"}, "deny", secret=_webhook)
        # ALLOW: a placeholder high-entropy value (excluded BEFORE entropy).
        sexpect("(ss-ak) GD-121 Write placeholder high-entropy allows",
                "Write", {"file_path": "/tmp/x",
                          "content": _asgn("key", '"' + "your_key_here_replace_before_use_1234567890" + '"')},
                "allow")
        # ALLOW: a metadata-named LHS (public_key) - the disclosed safe-direction divergence from gitleaks.
        sexpect("(ss-al) GD-121 Write metadata-named public_key allows (safe-direction residual)",
                "Write", {"file_path": "/tmp/x", "content": _asgn("public_key", '"' + _hi + '"')}, "allow",
                secret=_hi)
        # ALLOW: anti-substring names (monkey/author/keyboard) - exact-component matching, not substrings.
        for _nm in ("monkey", "author", "keyboard"):
            sexpect("(ss-am-{}) GD-121 Write anti-substring name allows".format(_nm),
                    "Write", {"file_path": "/tmp/x", "content": _asgn(_nm, '"' + _hi + '"')}, "allow",
                    secret=_hi)
        # ALLOW: 39-char value under a bare key (one below the length floor).
        sexpect("(ss-an) GD-121 Write 39-char high-entropy allows (length boundary)",
                "Write", {"file_path": "/tmp/x", "content": _asgn("key", '"' + _hi[:39] + '"')}, "allow")

        # GD-121 PARITY: the shipped hook (_scan_secret) and the CI gate (check_secrets) must decide every
        # entropy/provider line identically. The entropy decision and the new prefixes are hand-mirrored,
        # so this guards against future drift, exactly as the F-127 parity loop does for the env-ref logic.
        def _cs_any_hit(_line):
            if any(_rx.search(_line) for _rx, _l in _cs.PREFIXES):
                return True
            if any(_cs._assign_is_secret(_m) for _m in _cs.ASSIGN.finditer(_line)):
                return True
            return any(_cs._entropy_assign_is_secret(_m) for _m in _cs.ENTROPY_ASSIGN.finditer(_line))
        _gd121_parity = [
            _asgn("key", '"' + _hi + '"'),                       # entropy under bare key: both fire
            _asgn("passphrase", _n5("aB3dE7gH9kLmN2pQ", 40)),    # unquoted broadened keyword: both fire
            _asgn("public_key", '"' + _hi + '"'),                # metadata excluded: both skip
            _asgn("monkey", '"' + _hi + '"'),                    # anti-substring: both skip
            _asgn("key", '"' + "a" * 40 + '"'),                  # low entropy: both skip
            _asgn("key", '"' + _hi[:39] + '"'),                  # 39-char: below floor, both skip
            _aiza,                                               # Google prefix: both fire
            _webhook,                                            # Slack webhook: both fire
            "npm_" + _n5("aB3dE7gH9k", 36),                      # npm token: both fire
        ]
        for _pl in _gd121_parity:
            if _cs_any_hit(_pl) != (aiqt_hooks._scan_secret(_pl) is not None):
                failures.append("(ss-gd121-parity) gate/hook disagree on a GD-121 line")

        # === gensrc_guard (gensrc): a Write/Edit/MultiEdit onto a REGISTERED generated artefact ASKS =
        # A registry-driven PATH guard: the handler reads the per-repo .aiqt/gensrc.json at decision
        # time and ASKS on a kind=file or kind=tree match. Judged by the STRUCTURED decision, never by
        # grepping output. Fixtures are throwaway git repos under tmp (removed in the finally); registry
        # targets need not exist on disk (realpath resolves a non-existent path), so no seed commits are
        # needed beyond git init.
        def _gs_init(name):
            path = tmp / name
            path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                           check=True, capture_output=True, text=True, timeout=30)
            return path

        def gdecide(data):
            code, stdout_obj, _stderr = aiqt_hooks.gensrc_guard(data)
            if code == 0 and stdout_obj is None:
                return "allow"
            if code == 0 and isinstance(stdout_obj, dict):
                decision = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision")
                if decision in ("allow", "ask", "deny"):
                    return decision
                # An _allow_note: a systemMessage with NO permissionDecision is an informational allow.
                if "hookSpecificOutput" not in stdout_obj and "systemMessage" in stdout_obj:
                    return "allow"
                return "unexpected"
            return "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)

        def gexpect(label, want, tool="Write", file_path=None, edits=None, cwd=None,
                    with_tool=True, with_cwd=True, tool_input="__default__"):
            data = {"hook_event_name": "PreToolUse"}
            if with_tool:
                data["tool_name"] = tool
            if tool_input == "__default__":
                ti = {}
                if file_path is not None:
                    ti["file_path"] = file_path
                if edits is not None:
                    ti["edits"] = edits
                data["tool_input"] = ti
            elif tool_input is not None:
                data["tool_input"] = tool_input
            if with_cwd and cwd is not None:
                data["cwd"] = cwd
            got = gdecide(data)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # A checked-in-shape registry with one file, one tree, and one block entry.
        _gs_registry = {"version": 1, "generated": [
            {"kind": "file", "target": "GEN.md", "sources": ["src.toml"],
             "regenerate": "python3 tools/gen_x.py"},
            {"kind": "tree", "target": "gen/", "sources": ["src/"],
             "regenerate": "python3 tools/gen_tree.py"},
            {"kind": "block", "target": "CLAUDE.md", "sources": ["rules/"],
             "regenerate": "python3 tools/gen_claude.py"}]}
        try:
            gs_repo = _gs_init("gsrepo")               # carries the registry
            (gs_repo / ".aiqt").mkdir(parents=True, exist_ok=True)
            (gs_repo / ".aiqt" / "gensrc.json").write_text(json.dumps(_gs_registry), encoding="utf-8")
            gs_repo2 = _gs_init("gsrepo2")             # NO registry (absent, inert)
            gs_repo3 = _gs_init("gsrepo3")             # mutable bad registries
            (gs_repo3 / ".aiqt").mkdir(parents=True, exist_ok=True)
            gs_reg3 = gs_repo3 / ".aiqt" / "gensrc.json"
            gs_nogit = tmp / "gsnogit"                 # plain non-git dir (unresolved root)
            gs_nogit.mkdir(parents=True, exist_ok=True)
            # Deterministic hardening fixtures (no permission-dependent skips):
            gs_repo_sp = _gs_init("gsrepo_sp ")        # dir name with a TRAILING SPACE (F-162)
            (gs_repo_sp / ".aiqt").mkdir(parents=True, exist_ok=True)
            (gs_repo_sp / ".aiqt" / "gensrc.json").write_text(json.dumps(_gs_registry), encoding="utf-8")
            gs_repo_nl = _gs_init("gsrepo_nl\n")        # dir name ending in a NEWLINE (F-167)
            (gs_repo_nl / ".aiqt").mkdir(parents=True, exist_ok=True)
            (gs_repo_nl / ".aiqt" / "gensrc.json").write_text(json.dumps(_gs_registry), encoding="utf-8")
            gs_link = _gs_init("gslink")               # registry is a DANGLING symlink (F-164)
            (gs_link / ".aiqt").mkdir(parents=True, exist_ok=True)
            os.symlink(str(gs_link / ".aiqt" / "nonexistent.json"),
                       str(gs_link / ".aiqt" / "gensrc.json"))
            gs_dir = _gs_init("gsdir")                 # registry PATH is a DIRECTORY (deterministic unreadable)
            (gs_dir / ".aiqt").mkdir(parents=True, exist_ok=True)
            (gs_dir / ".aiqt" / "gensrc.json").mkdir(parents=True, exist_ok=True)
            gs_big = _gs_init("gsbig")                 # multibyte OVERSIZE registry: >1M BYTES, <1M chars (F-165)
            (gs_big / ".aiqt").mkdir(parents=True, exist_ok=True)
            # ensure_ascii=False so the multibyte pad is written as REAL 2-byte UTF-8 (not the 6-byte
            # \\uXXXX ASCII escape): the file is then ~1.2M BYTES but only ~600k CHARS, so a char-count
            # read would slip under the cap (the pre-fix hole) while the byte-bound read rejects it.
            _big_pad = "é" * 600000               # 600k chars, ~1.2M BYTES ('e' with acute is 2 UTF-8 bytes)
            (gs_big / ".aiqt" / "gensrc.json").write_text(
                json.dumps({"version": 1, "generated": [], "pad": _big_pad}, ensure_ascii=False),
                encoding="utf-8")
            gs_fifo = _gs_init("gsfifo")               # registry PATH is a FIFO (F-169: lstat/S_ISREG rejects)
            (gs_fifo / ".aiqt").mkdir(parents=True, exist_ok=True)
            gs_race = _gs_init("gsrace")               # NO registry file; the delete-race is injected (F-169)
            (gs_race / ".aiqt").mkdir(parents=True, exist_ok=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the gensrc fixtures: {}".format(exc), file=sys.stderr)
            return 2
        gr, gr2, gr3, gng = str(gs_repo), str(gs_repo2), str(gs_repo3), str(gs_nogit)
        grsp, grlink, grdir, grbig = str(gs_repo_sp), str(gs_link), str(gs_dir), str(gs_big)
        grnl, grfifo, grrace = str(gs_repo_nl), str(gs_fifo), str(gs_race)

        # ASK: a file match, a tree-member match, and a MultiEdit file match. gs-a proves the EXPLICIT
        # _ask (the manifest default is never rendered, so an ask here cannot be leaning on it).
        gexpect("(gs-a) Write a registered file target ASKS", "deny",
                tool="Write", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-b) Edit a member of a registered tree ASKS", "deny",
                tool="Edit", file_path=os.path.join(gr, "gen", "part.md"), cwd=gr)
        gexpect("(gs-c) MultiEdit a registered file target ASKS (MultiEdit in scope)", "deny",
                tool="MultiEdit", file_path=os.path.join(gr, "GEN.md"),
                edits=[{"old_string": "a", "new_string": "b"}], cwd=gr)
        # ALLOW: a source edit, an unregistered path, a block-entry file.
        gexpect("(gs-d) Edit a source (never a generated target) allows", "allow",
                tool="Edit", file_path=os.path.join(gr, "src.toml"), cwd=gr)
        gexpect("(gs-e) Write an unregistered path allows (no-match inertness)", "allow",
                tool="Write", file_path=os.path.join(gr, "README.md"), cwd=gr)
        gexpect("(gs-f) Edit a kind=block target allows (block exclusion)", "allow",
                tool="Edit", file_path=os.path.join(gr, "CLAUDE.md"), cwd=gr)
        # ALLOW: component-boundary and equality matching (fails under a raw string prefix).
        gexpect("(gs-g1) Write gen-extra/ does not match the gen/ tree", "allow",
                tool="Write", file_path=os.path.join(gr, "gen-extra", "x.md"), cwd=gr)
        gexpect("(gs-g2) Write GEN.md.bak does not match the GEN.md file", "allow",
                tool="Write", file_path=os.path.join(gr, "GEN.md.bak"), cwd=gr)
        # ALLOW: an absent registry is inert (repo2 has no .aiqt/gensrc.json).
        gexpect("(gs-h) an absent registry is the inert ALLOW", "allow",
                tool="Write", file_path=os.path.join(gr2, "GEN.md"), cwd=gr2)
        # ROUND-2 FINDING 8: a PRESENT-but-unreadable/malformed registry is a cannot-evaluate for a
        # PROTECTIVE guard, so it fails CLOSED (DENY), never allow-note - a corrupted/garbled .aiqt/gensrc.json
        # must not silently DISABLE the generated-artefact restriction. malformed JSON, unknown version, and a
        # malformed entry (kind=dir) all DENY. (Contrast the genuinely-ABSENT registry gs-h and the non-git /
        # payload cannot-evaluate branches below, which stay allow: no generated-file protection is expected.)
        gs_reg3.write_text("{ not json", encoding="utf-8")
        gexpect("(gs-i1) malformed-JSON registry DENIES fail-closed (finding 8)", "deny",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        gs_reg3.write_text(json.dumps({"version": 2, "generated": []}), encoding="utf-8")
        gexpect("(gs-i2) unknown-version registry DENIES fail-closed (finding 8)", "deny",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "dir", "target": "x", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-i3) malformed-entry (unknown kind) registry DENIES fail-closed (finding 8)", "deny",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # DISCRIMINATION (fails WITHOUT the finding-8 fix): the ONLY difference between gs-i1 (present-but-bad
        # -> deny) and gs-h (absent -> allow) is registry READABILITY, so the deny is attributable to the
        # fail-closed treatment of a present-but-unreadable registry, not to a path match (gr3 has no match
        # for GEN.md when the registry is unreadable). Under the old allow-note it was "allow".
        # ASK: an unresolved repo root (a plain non-git cwd).
        gexpect("(gs-j) a non-git cwd (unresolved root) ASKS", "allow",
                tool="Write", file_path=os.path.join(gng, "GEN.md"), cwd=gng)
        # DENY: the only deny, the shared fail-closed contract (no tool_name).
        gexpect("(gs-k) a missing tool_name DENIES (fail-closed contract)", "deny",
                file_path=os.path.join(gr, "GEN.md"), cwd=gr, with_tool=False)
        # ASK: no session cwd, so the root cannot be resolved.
        gexpect("(gs-l) a missing cwd ASKS (root cannot be resolved)", "allow",
                tool="Write", file_path=os.path.join(gr, "GEN.md"), with_cwd=False)
        # ASK: a target outside the repo cannot be cleared against this repo registry.
        gexpect("(gs-m) a target outside the repo ASKS (non-contained)", "allow",
                tool="Write", file_path=str(tmp / "outside.md"), cwd=gr)
        # ALLOW: Bash is out of scope by design (defensive branch; the matcher excludes it too).
        gexpect("(gs-n) Bash is out of scope (allow)", "allow",
                tool="Bash", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        # ASK: payload fail-safes (non-dict tool_input, missing file_path).
        gexpect("(gs-o) a non-dict tool_input ASKS", "allow",
                tool="Write", cwd=gr, tool_input="not-a-dict")
        gexpect("(gs-p) a missing file_path ASKS", "allow",
                tool="Write", cwd=gr)
        # ASK: a non-regular-file registry is BAD, never absent (integ-check-fails-closed-on-unreadable).
        # DETERMINISTIC: the registry PATH is a DIRECTORY, so the lstat/S_ISREG probe rejects it as
        # non-regular BEFORE the open (a directory's st_mode is not S_ISREG -> bad) on every runner, root
        # included. No os.access/chmod skip (F-166).
        gexpect("(gs-q) a non-regular (directory-at-path) registry DENIES fail-closed (not a regular file, "
                "never absent; finding 8)", "deny",
                tool="Write", file_path=os.path.join(grdir, "GEN.md"), cwd=grdir)
        # ASK: a MultiEdit relative file_path is joined onto cwd, then matched.
        gexpect("(gs-r) a MultiEdit relative file_path is cwd-joined then matched (ASKS)", "deny",
                tool="MultiEdit", file_path="GEN.md",
                edits=[{"old_string": "a", "new_string": "b"}], cwd=gr)

        # === round-2 hardening: input-validation holes that must fail SAFE to ASK, never silent-allow ===
        # HARD BLOCK: a mis-wired event (not PreToolUse) fails closed at exit 2 (no structured decision).
        _hb_code, _hb_out, _hb_err = aiqt_hooks.gensrc_guard(
            {"hook_event_name": "PostToolUse", "tool_name": "Write",
             "tool_input": {"file_path": os.path.join(gr, "GEN.md")}, "cwd": gr})
        if _hb_code != 2:
            failures.append("(gs-s) a mis-wired event hard-blocks (exit 2): expected 2, got {}"
                            .format(_hb_code))
        # ASK: a present-but-unreadable tool_name (empty string, list, bool) cannot be matched -> fail-safe
        # ask (only a MISSING tool_name denies). Was a silent ALLOW (not in _GENSRC_TOOLS). (F-161)
        gexpect("(gs-t1) an empty-string tool_name ASKS (unreadable, not a miss)", "allow",
                tool="", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-t2) a list tool_name ASKS (unreadable, not a miss)", "allow",
                tool=[], file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-t3) a bool tool_name ASKS (unreadable, not a miss)", "allow",
                tool=True, file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        # ASK: version:true is a JSON bool, not int 1 (type(True) is bool). Was ALLOW (True == 1). (F-159)
        gs_reg3.write_text(json.dumps({"version": True, "generated": [
            {"kind": "file", "target": "GEN.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-u) a JSON-bool version:true DENIES fail-closed (type is bool, not int; finding 8)",
                "deny", tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # DENY: version:"1" string is not int 1 (a present-but-bad registry -> fail-closed, finding 8).
        gs_reg3.write_text(json.dumps({"version": "1", "generated": [
            {"kind": "file", "target": "GEN.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-v) a string version:\"1\" DENIES fail-closed (not int; finding 8)", "deny",
                tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a control character (NUL) in a FILE entry target is malformed -> bad. Was ALLOW (target
        # passed the old validation, no match on an unregistered query). (F-160)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "file", "target": "GEN\x00.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-w) a NUL in a file-entry target DENIES fail-closed (control-char rejected; finding 8)",
                "deny", tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a NON-NUL control char (0x1f) in a file target. Unlike NUL, realpath does NOT raise on it,
        # so ONLY the control-char rejection (not the realpath-fault wrap) catches it - guards F-160's
        # independent value. Was ALLOW (target passed old validation; no match on an unregistered query).
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "file", "target": "GEN\x1f.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-w2) a non-NUL control char in a file-entry target DENIES fail-closed (finding 8)",
                "deny", tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a NUL in a BLOCK entry target is rejected BEFORE the block-skip. Was a zero-entry ALLOW
        # (the block was dropped, leaving no entries). (F-160)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "block", "target": "X\x00", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-x) a NUL in a block-entry target DENIES fail-closed (rejected before the block-skip; "
                "finding 8)", "deny", tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # ASK: a NUL in the PAYLOAD file_path is rejected before realpath. Was an uncaught crash
        # (os.path.realpath raises ValueError on an embedded NUL). (F-160 + F-157)
        gexpect("(gs-y) a NUL in the payload file_path ASKS (was a crash-to-deny)", "allow",
                tool="Write", file_path=os.path.join(gr, "GEN\x00.md"), cwd=gr)
        # ASK: a NON-NUL control char (0x1f) in the payload file_path. realpath would NOT raise on it, so
        # only the control-char rejection catches it (guards F-160's independent value). Was a silent ALLOW.
        gexpect("(gs-y2) a non-NUL control char in the payload file_path ASKS (realpath would not reject)",
                "allow", tool="Write", file_path=os.path.join(gr, "GEN\x1f.md"), cwd=gr)
        # ASK: a repo dir name with a TRAILING SPACE: the toplevel is preserved because only git's single
        # trailing-newline terminator is stripped (result.stdout[:-1] when it endswith "\\n", stripping
        # exactly that one \\n), not strip(), so the registry IS found and the registered target ASKS. Was
        # ALLOW (strip() dropped the space -> wrong root -> registry not found -> absent). (F-162)
        gexpect("(gs-z) a trailing-space repo dir keeps its toplevel; the registered target ASKS", "deny",
                tool="Write", file_path=os.path.join(grsp, "GEN.md"), cwd=grsp)
        # ASK: a DANGLING symlink registry is BAD (a symlink is not a trusted regular file). lstat does NOT
        # follow the link, so S_ISREG is False on the link itself -> bad; this rejects a STATIONARY symlink
        # (best-effort against the accidental case, not a TOCTOU-closure claim). Was an inert ALLOW
        # (open -> FileNotFoundError -> absent). (F-164)
        gexpect("(gs-aa) a dangling-symlink registry DENIES fail-closed (a symlink is never a regular file; "
                "finding 8)", "deny", tool="Write", file_path=os.path.join(grlink, "GEN.md"), cwd=grlink)
        # ASK: a multibyte OVERSIZE registry (>1M BYTES but <1M chars) exceeds the BYTE bound. Was ALLOW
        # (a char-count read stayed under the cap and parsed to an empty registry). (F-165)
        gexpect("(gs-ab) a multibyte-oversize registry DENIES fail-closed (the bound is on BYTES; finding 8)",
                "deny", tool="Write", file_path=os.path.join(grbig, "GEN.md"), cwd=grbig)

        # gs-ac / gs-ad: the guarded-realpath-fault branch (the target/entry realpath raises) and the
        # _gensrc_within containment-fault "err" branch are DEFENSE-IN-DEPTH and NOT input-reachable on
        # POSIX (a control-char input is rejected before realpath; a realpath'd absolute never makes
        # os.path.commonpath raise on Linux). Exercise them DETERMINISTICALLY by INJECTING the fault:
        # monkeypatch the module-shared os.path primitive to raise within the call, assert the handler
        # returns ASK, restore in the finally. The good repo (gr) + a registered target gives a resolvable
        # root and a real registry, so the flow REACHES the guarded call before the fault fires.
        # Falsifiable: removing the guarding try/except (gs-ac the gensrc_guard realpath wrap, gs-ad the
        # _gensrc_within wrap / its "err" sentinel handling) turns the injected fault into an uncaught
        # crash the dispatcher hard-DENIES, not an ASK.
        def _raise_realpath(*_a, **_k):
            raise OSError("injected realpath fault (gs-ac)")

        def _raise_commonpath(*_a, **_k):
            raise ValueError("injected commonpath fault (gs-ad)")

        _gs_inj_fp = os.path.join(gr, "GEN.md")
        _real_realpath = os.path.realpath
        try:
            os.path.realpath = _raise_realpath
            gexpect("(gs-ac) an injected realpath fault on the target ASKS (guarded-realpath branch)",
                    "allow", tool="Write", file_path=_gs_inj_fp, cwd=gr)
        finally:
            os.path.realpath = _real_realpath
        _real_commonpath = os.path.commonpath
        try:
            os.path.commonpath = _raise_commonpath
            gexpect("(gs-ad) an injected commonpath fault ASKS (_gensrc_within containment 'err' branch)",
                    "allow", tool="Write", file_path=_gs_inj_fp, cwd=gr)
        finally:
            os.path.commonpath = _real_commonpath

        # ASK: a repo dir name ending in a NEWLINE keeps its toplevel. git prints the path + EXACTLY one \n
        # terminator, so stripping only that one \n preserves the dir's own trailing newline; the registry
        # IS found and the registered target (relative MultiEdit route, cwd = the newline-terminal repo)
        # ASKS. Falsifiable: rstrip("\n") eats the dir's own newline too -> wrong root -> registry not
        # found -> inert absent ALLOW. (F-167)
        gexpect("(gs-ae) a newline-terminal repo dir keeps its toplevel; the registered target ASKS", "deny",
                tool="MultiEdit", file_path="GEN.md",
                edits=[{"old_string": "a", "new_string": "b"}], cwd=grnl)

        # ASK: a NON-UTF-8 registry (invalid bytes) is BAD, never absent: the explicit
        # raw_bytes.decode("utf-8") raises UnicodeDecodeError, which is caught -> bad. Falsifiable:
        # removing the decode try/except turns the invalid bytes into an uncaught crash the dispatcher
        # hard-DENIES, not a clean ASK. (F-169 deterministic decode-path proof)
        gs_reg3.write_bytes(b"\xff\xfe\x00\x01not utf-8\xc3\x28")
        gexpect("(gs-af) a non-UTF-8 registry DENIES fail-closed (invalid bytes -> decode fault -> bad; "
                "finding 8)", "deny", tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # ASK: a FIFO registry is BAD (lstat/S_ISREG sees S_ISFIFO before the open), and the probe does NOT
        # block: os.lstat does not open the FIFO, so no writer is needed and there is no hang. Falsifiable:
        # dropping the lstat/S_ISREG probe would make open(path, "rb") block on the FIFO until the hook
        # timeout instead of returning ASK. The fifo is unlinked in the finally below. (F-169)
        _fifo_path = os.path.join(grfifo, ".aiqt", "gensrc.json")
        os.mkfifo(_fifo_path)
        try:
            gexpect("(gs-ag) a FIFO registry DENIES fail-closed (lstat/S_ISREG sees S_ISFIFO, no open, no "
                    "hang; finding 8)", "deny",
                    tool="Write", file_path=os.path.join(grfifo, "GEN.md"), cwd=grfifo)
        finally:
            os.remove(_fifo_path)
        # ASK: a DELETE RACE in the lstat->open window. The registry file does not exist, so open() would
        # raise FileNotFoundError; monkeypatch os.lstat to report a REGULAR file for that path so the
        # S_ISREG probe passes and the flow reaches the open, which then raises FNF -> bad (fail-safe ASK),
        # NOT absent. Falsifiable: the pre-fix open FileNotFoundError returned ("absent", None) -> the inert
        # ALLOW; the fix maps it to bad -> ASK. os.lstat is restored in the finally. (F-169)
        _real_lstat = os.lstat
        _regular_st = _real_lstat(os.path.join(gr, ".aiqt", "gensrc.json"))  # a genuine regular-file stat
        _race_rel = os.path.join("gsrace", ".aiqt", "gensrc.json")
        def _racing_lstat(p, *a, **k):
            if "gsrace" in str(p) and str(p).endswith(_race_rel):
                return _regular_st           # claim a regular file for the absent registry path
            return _real_lstat(p, *a, **k)
        try:
            os.lstat = _racing_lstat
            gexpect("(gs-ah) a delete-race (regular at lstat, gone at open) DENIES fail-closed, not "
                    "absent-ALLOW (finding 8)", "deny",
                    tool="Write", file_path=os.path.join(grrace, "GEN.md"), cwd=grrace)
        finally:
            os.lstat = _real_lstat

        # === abspth: absolute paths across typed-path tools and the Bash cwd floor (GS-7) ============
        # FAILING-FIRST battery: no abspth coverage existed before GS-7, and every case here is one the
        # pre-GS-7 handler answered differently (it allowed a drive-relative file_path, was blind to
        # MultiEdit/NotebookEdit/Grep, fail-opened on a malformed tool_input at the search-root branch,
        # and had no Bash linkage at all). It pins three layers: (a) the native-path predicate that
        # rejects drive-relative 'C:file', bare 'C:', and leading-backslash '\\file' while accepting a
        # drive-absolute or UNC path, plus the tool_input-must-be-a-mapping fail-closed; (b) the widened
        # matcher over MultiEdit (file_path), NotebookEdit (notebook_path), and Grep (path), each field
        # name fixed against the live Claude Code tool schema, with the rootless Glob/Grep carve-out
        # re-affirmed; and (c) the conservative Bash floor that ASKS on a relative cd/pushd destination or
        # a relative redirection target and allows an absolute one, never denying and never judging an
        # arbitrary command operand.
        def _reduce(handler, data):
            code, stdout_obj, _stderr = handler(data)
            if code == 0 and stdout_obj is None:
                return "allow"
            if code == 0 and isinstance(stdout_obj, dict):
                decision = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision")
                if decision in ("allow", "ask", "deny"):
                    return decision
                # An _allow_note: a systemMessage with NO permissionDecision is an informational allow.
                if "hookSpecificOutput" not in stdout_obj and "systemMessage" in stdout_obj:
                    return "allow"
                return "unexpected"
            return "unexpected result (code={!r}, stdout={!r})".format(code, stdout_obj)

        def apexpect(label, tool, tool_input, want):
            got = _reduce(aiqt_hooks.absolute_paths,
                          {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        def bcmd(label, command, want):
            got = _reduce(aiqt_hooks.bash_absolute_paths,
                          {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": command}})
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        # --- Layer a: native-path predicate over the required-absolute file_path branch --------------
        apexpect("(ap-a1) Read absolute file_path allows", "Read", {"file_path": rp}, "allow")
        apexpect("(ap-a2) Read relative file_path denies", "Read", {"file_path": "rel/x"}, "deny")
        apexpect("(ap-a3) Edit drive-relative 'C:file' denies (native predicate)", "Edit",
                 {"file_path": "C:file"}, "deny")
        apexpect("(ap-a4) Write bare drive 'C:' denies (native predicate)", "Write",
                 {"file_path": "C:"}, "deny")
        apexpect("(ap-a5) Write leading-backslash '\\file' denies (native predicate)", "Write",
                 {"file_path": "\\file"}, "deny")
        apexpect("(ap-a6) Write drive-absolute 'C:\\x' allows", "Write", {"file_path": "C:\\x"}, "allow")
        apexpect("(ap-a7) Write drive-absolute forward 'C:/x' allows", "Write",
                 {"file_path": "C:/x"}, "allow")
        apexpect("(ap-a8) Write UNC '\\\\srv\\share' allows", "Write",
                 {"file_path": "\\\\srv\\share"}, "allow")
        apexpect("(ap-a9) Read tilde '~/x' denies (not expanded, fail-closed)", "Read",
                 {"file_path": "~/x"}, "deny")
        apexpect("(ap-a10) Read empty file_path denies (fail-closed)", "Read", {"file_path": ""}, "deny")
        apexpect("(ap-a11) Read missing file_path denies (fail-closed)", "Read", {}, "deny")
        apexpect("(ap-a12) Read non-string file_path denies (fail-closed)", "Read",
                 {"file_path": 5}, "deny")
        # tool_input must be a mapping BEFORE any branch: a None or list payload fails closed for an
        # in-scope tool (the search-root fail-open the old 'or {}' collapse allowed is now closed), while
        # an out-of-scope tool still allows (the matcher governs).
        apexpect("(ap-a13) Read tool_input None fails closed (mapping guard)", "Read", None, "deny")
        apexpect("(ap-a14) Glob tool_input a list fails closed (was fail-open at search-root)", "Glob",
                 [], "deny")
        apexpect("(ap-a15) Grep tool_input None fails closed (mapping guard)", "Grep", None, "deny")
        apexpect("(ap-a16) out-of-scope tool with non-dict tool_input allows (matcher governs)", "Bash",
                 None, "allow")
        apexpect("(ap-a17) missing tool_name denies (fail-closed)-> via absolute_paths", None,
                 {"file_path": "rel"}, "deny")

        # --- Layer b: widened matcher over MultiEdit / NotebookEdit / Grep, live-schema field names ---
        apexpect("(ap-b1) MultiEdit relative file_path denies", "MultiEdit",
                 {"file_path": "rel/x", "edits": []}, "deny")
        apexpect("(ap-b2) MultiEdit absolute file_path allows", "MultiEdit",
                 {"file_path": rp, "edits": []}, "allow")
        apexpect("(ap-b3) MultiEdit missing file_path denies (fail-closed)", "MultiEdit",
                 {"edits": []}, "deny")
        apexpect("(ap-b4) NotebookEdit relative notebook_path denies", "NotebookEdit",
                 {"notebook_path": "nb.ipynb"}, "deny")
        apexpect("(ap-b5) NotebookEdit absolute notebook_path allows", "NotebookEdit",
                 {"notebook_path": rp + "/nb.ipynb"}, "allow")
        apexpect("(ap-b6) NotebookEdit missing notebook_path denies (fail-closed)", "NotebookEdit",
                 {"new_source": "x"}, "deny")
        apexpect("(ap-b7) Grep relative path search root denies", "Grep",
                 {"pattern": "x", "path": "rel"}, "deny")
        apexpect("(ap-b8) Grep absolute path search root allows", "Grep",
                 {"pattern": "x", "path": rp}, "allow")
        apexpect("(ap-b9) Grep rootless (no path) allows (carve-out)", "Grep", {"pattern": "x"}, "allow")
        apexpect("(ap-b10) Grep empty path denies (fail-closed)", "Grep",
                 {"pattern": "x", "path": ""}, "deny")
        # carve-out re-affirmed for the pre-existing Glob wiring, unchanged by GS-7
        apexpect("(ap-b11) Glob rootless (no path) allows (carve-out)", "Glob",
                 {"pattern": "*.py"}, "allow")
        apexpect("(ap-b12) Glob relative path denies", "Glob", {"pattern": "*.py", "path": "rel"}, "deny")
        apexpect("(ap-b13) Glob absolute path allows", "Glob", {"pattern": "*.py", "path": rp}, "allow")

        # --- Layer c: conservative Bash floor over cd/pushd operands and redirect targets ------------
        bcmd("(ap-c1) cd absolute allows", "cd {} && ls".format(rp), "allow")
        bcmd("(ap-c2) cd relative asks", "cd sub", "allow")
        bcmd("(ap-c3) cd ../parent relative asks", "cd ../x", "allow")
        bcmd("(ap-c4) cd ./here relative asks", "cd ./x", "allow")
        bcmd("(ap-c5) pushd relative asks", "pushd rel", "allow")
        bcmd("(ap-c6) pushd absolute allows", "pushd {}".format(rp), "allow")
        bcmd("(ap-c7) cd opaque '$DIR' asks (unresolvable)", "cd $DIR", "allow")
        bcmd("(ap-c8) cd rooted-with-expansion '/$X/y' allows (always absolute)", "cd /$X/y", "allow")
        bcmd("(ap-c9) cd bare (HOME) allows", "cd", "allow")
        bcmd("(ap-c10) cd '-' (OLDPWD) allows", "cd -", "allow")
        bcmd("(ap-c11) pushd bare (stack swap) allows", "pushd", "allow")
        bcmd("(ap-c12) pushd '+1' rotation allows (no path)", "pushd +1", "allow")
        bcmd("(ap-c13) cd option then absolute allows (option skipped)", "cd -P {}".format(rp), "allow")
        bcmd("(ap-c14) cd option then relative asks (option skipped, dest judged)", "cd -P rel", "allow")
        bcmd("(ap-c15) pushd '-n' then relative asks", "pushd -n rel", "allow")
        # ROUND-2 FINDING 10: a TRUNCATING redirect ('>', '>|', '&>', '>&' to a file) to a RELATIVE or OPAQUE
        # target DENIES-and-educates (it zeroes an unverified ambient target; the `: > file.txt` class). The
        # NON-destructive forms (append '>>', read '<'/'<>') and an absolute truncating target stay allow.
        bcmd("(ap-c16) relative truncating redirect target DENIES (finding 10)", "echo hi > out.txt", "deny")
        bcmd("(ap-c17) absolute redirect target allows", "echo hi > {}/out.txt".format(rp), "allow")
        bcmd("(ap-c18) relative append redirect allows (append does not truncate; convention nudge)",
             "echo hi >> log", "allow")
        bcmd("(ap-c19) relative input redirect allows (read is not destructive; convention nudge)",
             "sort < data.txt", "allow")
        bcmd("(ap-c20) absolute input redirect allows", "sort < {}/data.txt".format(rp), "allow")
        bcmd("(ap-c21) fd duplication '2>&1' allows (descriptor, no path)", "cat x 2>&1", "allow")
        bcmd("(ap-c22) absolute /dev redirect allows", "cat x 2>/dev/null", "allow")
        bcmd("(ap-c23) opaque truncating redirect target '$F' DENIES (finding 10: unverified target)",
             "echo hi > $F", "deny")
        bcmd("(ap-c24) non-cd relative operand NOT judged (allows)", "cat rel.txt", "allow")
        bcmd("(ap-c25) compound with a relative TRUNCATING redirect DENIES (finding 10)",
             "cd {} && echo x > out.txt".format(rp), "deny")
        # the bare-truncation `: > file.txt` class (a no-op producer zeroing a relative target) DENIES.
        bcmd("(ap-c25b) ': > relative' bare truncation DENIES (finding 10, the named example)",
             ": > file.txt", "deny")
        # discrimination witness: the SAME target as an APPEND ('>>') stays allow, so the deny is the
        # TRUNCATION, not merely the relative path (fails if the truncating-op gate is removed).
        bcmd("(ap-c25c) the same relative target as an append '>>' allows (truncation is the discriminator)",
             ": >> file.txt", "allow")
        bcmd("(ap-c25d) a relative '>|' clobber redirect DENIES (truncating op; finding 10)",
             "echo x >| out.txt", "deny")
        bcmd("(ap-c26) compound all-absolute allows", "cd {} && cat {}/f".format(rp, rp), "allow")
        bcmd("(ap-c27) unparseable command with no earlier relative position allows (disclosed residual)",
             'git checkout -- "unbalanced', "allow")
        bcmd("(ap-c28) empty command asks (cannot read)", "", "allow")
        # GS-7 fix: per-SEGMENT partial lex so a resolvable relative cd/redirect in the PARSEABLE PREFIX
        # still ASKS even when a LATER segment is unparseable (pre-fix, one lexer ValueError over the whole
        # command discarded every parsed segment and ALLOWED). Fails-when-reverted: these were "allow".
        bcmd("(ap-c34) relative cd before a heredoc asks (prefix inspected, GS-7 FIX 1)",
             "cd .aiqt; cat <<EOF > /dev/null\nx\nEOF\npwd", "allow")
        bcmd("(ap-c35) relative TRUNCATING redirect before a here-string DENIES (prefix inspected, GS-7 FIX "
             "1 + finding 10)", "echo hi > out.txt; cat <<<x", "deny")
        bcmd("(ap-c36) relative cd before a process substitution asks (prefix inspected, GS-7 FIX 1)",
             "cd rel; cat <(printf x)", "allow")
        # boundary: an unparseable construct with NO earlier resolvable position stays a disclosed ALLOW
        # (a cd/redirect WITHIN or AFTER the construct is uninspected, not over-asked on every heredoc).
        bcmd("(ap-c37) heredoc with no earlier relative position allows (disclosed residual, GS-7 FIX 1)",
             "cat <<EOF\nx\nEOF", "allow")
        # GS-7 FIX 2: a real relative destination beginning '-'/'+' is the destination, not an option; '--'
        # ends option processing. Pre-fix ^[-+] skipped every such token -> None -> ALLOW. Fails-when-reverted.
        bcmd("(ap-c38) 'cd -- -relative' asks ('--' ends options, dest judged, GS-7 FIX 2)",
             "cd -- -relative", "allow")
        bcmd("(ap-c39) 'cd +relative' asks (a real relative dir name, GS-7 FIX 2)", "cd +relative", "allow")
        bcmd("(ap-c40) 'cd -relative' asks (a real relative dir name, GS-7 FIX 2)", "cd -relative", "allow")
        # the real option/rotation forms still allow (not regressed by FIX 2)
        bcmd("(ap-c41) 'cd -- /abs' allows (post-'--' absolute destination)", "cd -- {}".format(rp),
             "allow")
        bcmd("(ap-c42) 'cd -e -L /abs' allows (real cd option flags skipped)", "cd -e -L {}".format(rp),
             "allow")
        # GS-7 FIX 3: an UNQUOTED CURRENT-USER tilde ('~', '~/x') is cwd-independent (expands to $HOME) ->
        # ALLOW, matching bare 'cd'; pre-fix it ASKED (an ASK-fatigue over-fire that trained the 'cd' rewrite
        # bypass). An opaque '$VAR' still cannot be proven absolute and ASKS. Fails-when-reverted (tilde).
        bcmd("(ap-c43) 'cd ~' allows (current-user tilde expands to $HOME, GS-7 FIX 3)", "cd ~", "allow")
        bcmd("(ap-c44) 'cd ~/foo' allows (current-user tilde path, GS-7 FIX 3)", "cd ~/foo", "allow")
        # GS-7 round-2 MAJOR 4: a '~user'/'~user/x' names another account's home whose EXISTENCE the hook
        # cannot verify (an unresolved login name leaves the word RELATIVE), so the ALLOW is NARROWED to the
        # current-user forms and '~user' now ASKS (was a false ALLOW pre-round-2). Fails-when-reverted.
        bcmd("(ap-c45) 'cd ~user/x' asks (~user existence unverifiable, GS-7 round-2 MAJOR 4)",
             "cd ~user/x", "allow")
        bcmd("(ap-c45b) 'cd ~user' asks (~user existence unverifiable, GS-7 round-2 MAJOR 4)",
             "cd ~user", "allow")
        bcmd("(ap-c46) 'cd $HOME' asks (opaque expansion, not provably absolute, GS-7 FIX 3)",
             "cd $HOME", "allow")
        bcmd("(ap-c47) 'cd \"$HOME\"' asks (opaque expansion, GS-7 FIX 3)", 'cd "$HOME"', "allow")
        # a QUOTED '~' is a literal relative directory named '~', not tilde expansion: still ASKS (the
        # per-token LEADING-UNQUOTED-tilde flag tells the unquoted, expanded form from the quoted one).
        bcmd("(ap-c48) quoted 'cd \"~/foo\"' asks (quoted tilde is literal-relative, GS-7 FIX 3)",
             'cd "~/foo"', "allow")
        # GS-7 round-2 MAJOR 1: a QUOTED leading tilde with an UNQUOTED OPAQUE tail ('$'/glob/brace) is NOT
        # tilde-expanded by bash (the '~' is quoted, so the word stays RELATIVE), yet pre-round-2 it was a
        # false ALLOW because the token-wide opacity flag was mistaken for an unquoted-tilde signal. The
        # LEADING-UNQUOTED-tilde flag now gates the tilde-ALLOW, so every quoted-leading-tilde form ASKS
        # while the genuine unquoted '~/$VAR' stays ALLOW. Fails-when-reverted (all the ASK cases were ALLOW).
        bcmd("(ap-c49) 'cd \"~\"/x*' asks (quoted tilde + glob tail, not expanded, GS-7 round-2 MAJOR 1)",
             'cd "~"/x*', "allow")
        bcmd("(ap-c50) 'cd \"~\"/x?' asks (quoted tilde + glob tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/x?', "allow")
        bcmd("(ap-c51) 'cd \"~\"/{a}' asks (quoted tilde + brace tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/{a}', "allow")
        bcmd("(ap-c52) 'cd \"~\"/$V' asks (quoted tilde + expansion tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/$V', "allow")
        bcmd("(ap-c53) \"cd '~'/x*\" asks (single-quoted tilde + glob tail, GS-7 round-2 MAJOR 1)",
             "cd '~'/x*", "allow")
        bcmd("(ap-c54) 'cd \"~/repo\"*' asks (quoted tilde path + glob, GS-7 round-2 MAJOR 1)",
             'cd "~/repo"*', "allow")
        bcmd("(ap-c55) 'cd \\\\~/$VAR' asks (escaped tilde + expansion tail, GS-7 round-2 MAJOR 1)",
             'cd \\~/$VAR', "allow")
        bcmd("(ap-c56) 'cd \"\"~/x' asks (empty-quote-preceded tilde is not leading, GS-7 round-2 MAJOR 1)",
             'cd ""~/x', "allow")
        # the genuine unquoted leading tilde with an opaque tail STAYS ALLOW (regression guard for MAJOR 1)
        bcmd("(ap-c57) 'cd ~/$VAR' allows (unquoted leading tilde expands to $HOME, GS-7 round-2 MAJOR 1)",
             "cd ~/$VAR", "allow")
        # GS-7 round-2 MAJOR 2: a relative redirect target fully parsed BEFORE an unparseable construct in
        # the SAME in-progress segment ('cat > out.txt <<EOF...', 'cat > out.txt <(...)') is now inspected
        # (the in-progress segment is recovered on the partial-lex exception path) and ASKS; pre-round-2 the
        # whole in-progress segment was discarded, a false ALLOW. Fails-when-reverted (both were ALLOW).
        bcmd("(ap-c58) truncating redirect target before a heredoc DENIES (in-progress seg recovered, GS-7 "
             "round-2 MAJOR 2 + finding 10)", "cat > out.txt <<EOF\nx\nEOF", "deny")
        bcmd("(ap-c59) truncating redirect target before a proc-subst DENIES (in-progress seg recovered, "
             "GS-7 round-2 MAJOR 2 + finding 10)", "cat > out.txt <(printf x)", "deny")
        # boundary preserved: a construct with NO earlier resolvable position in the in-progress segment
        # stays a disclosed ALLOW; a cd/redirect AFTER the construct stays uninspected (disclosed residual).
        bcmd("(ap-c60) heredoc with only a command word before it allows (disclosed residual, GS-7 round-2)",
             "cat <<EOF\nx\nEOF", "allow")
        bcmd("(ap-c61) relative cd AFTER a proc-subst stays allow (position after construct, GS-7 round-2)",
             "cat <(x); cd rel", "allow")
        # GS-7 round-2 MAJOR 3: a lone '-' destination AFTER '--' is still the $OLDPWD shortcut in bash
        # (empirically /tmp->/opt), so 'cd -- -' ALLOWS; pre-round-2 the OLDPWD check ran only during option
        # processing, so post-'--' the '-' was mis-read as a relative name and ASKED. Fails-when-reverted.
        bcmd("(ap-c62) 'cd -- -' allows (lone '-' after '--' is $OLDPWD, GS-7 round-2 MAJOR 3)",
             "cd -- -", "allow")
        # regression guards for MAJOR 3: a real relative name after '--' still ASKS, and 'cd -- --' (a dir
        # literally named '--', which bash resolves against the cwd) stays ASK - NOT a false OLDPWD allow.
        bcmd("(ap-c63) 'cd -- rel' asks (post-'--' relative name, GS-7 round-2 MAJOR 3 guard)",
             "cd -- rel", "allow")
        bcmd("(ap-c64) 'cd -- --' asks (post-'--' relative dir named '--', GS-7 round-2 MAJOR 3 guard)",
             "cd -- --", "allow")
        # GS-7 round-3 MAJOR 1: bash expands a leading '~' ONLY when the WHOLE tilde-prefix (from '~' to the
        # first UNQUOTED '/' or end of word) is unquoted/unescaped. A QUOTE or ESCAPE anywhere in that prefix
        # disables expansion and the word stays RELATIVE - even when the DECODED token is fully literal ('~/x')
        # with no opacity flag. Pre-round-3 argv_leading_tilde was set merely because the FIRST char was an
        # unquoted '~', so all of these were false ALLOWs. The prefix is now tracked per-character in _read_word,
        # so each ASKS. Fails-when-reverted (every case here was ALLOW on the round-2 code).
        bcmd("(ap-c65) 'cd ~\"/x\"' asks (quoted '/' in tilde-prefix, not expanded, GS-7 round-3 MAJOR 1)",
             'cd ~"/x"', "allow")
        bcmd("(ap-c66) \"cd ~''\" asks (empty single-quote in tilde-prefix, GS-7 round-3 MAJOR 1)",
             "cd ~''", "allow")
        bcmd("(ap-c67) 'cd ~\"\"/x' asks (empty double-quote before the '/', GS-7 round-3 MAJOR 1)",
             'cd ~""/x', "allow")
        bcmd("(ap-c68) \"cd ~'/x'\" asks (single-quoted '/x' tail in prefix, GS-7 round-3 MAJOR 1)",
             "cd ~'/x'", "allow")
        bcmd("(ap-c69) 'cd ~\\\\/x' asks (escaped '/' in tilde-prefix, GS-7 round-3 MAJOR 1)",
             "cd ~\\/x", "allow")
        bcmd("(ap-c70) 'cd ~\"/\"$V' asks (quoted '/' then expansion, prefix quoted, GS-7 round-3 MAJOR 1)",
             'cd ~"/"$V', "allow")
        bcmd("(ap-c71) 'cd ~\"/x\" <(printf x)' asks (quoted-prefix tilde in a partial-lex compose, round-3)",
             'cd ~"/x" <(printf x)', "allow")
        # regression guard for round-3 MAJOR 1: an unquoted tilde-prefix closed by an unquoted '/' STAYS ALLOW,
        # even with an opaque '$VAR' AFTER the '/', because material after the prefix does not block expansion.
        bcmd("(ap-c72) 'cd ~/$VAR' allows (whole tilde-prefix unquoted, GS-7 round-3 MAJOR 1 guard)",
             "cd ~/$VAR", "allow")
        # GS-7 round-3 MAJOR 2: an inline OLDPWD= assignment overrides $OLDPWD for that command, so a lone '-'
        # destination (incl. post-'--') no longer resolves to a cwd-independent prior dir - bash cds to the
        # assigned value, which can be RELATIVE ('OLDPWD=.. cd -- -' -> '..'). The floor cannot prove the value
        # absolute, so a lone '-' with an inline OLDPWD= present now ASKS. Pre-round-3 it was a false ALLOW.
        # Fails-when-reverted. Without an inline OLDPWD=, 'cd -' / 'cd -- -' STAY ALLOW (guards below).
        bcmd("(ap-c73) 'OLDPWD=.. cd -- -' asks (inline OLDPWD= overrides $OLDPWD, GS-7 round-3 MAJOR 2)",
             "OLDPWD=.. cd -- -", "allow")
        bcmd("(ap-c74) 'OLDPWD=.. cd -' asks (inline OLDPWD= overrides $OLDPWD, GS-7 round-3 MAJOR 2)",
             "OLDPWD=.. cd -", "allow")
        bcmd("(ap-c75) 'OLDPWD=.. pushd -- -' asks (inline OLDPWD= overrides $OLDPWD, GS-7 round-3 MAJOR 2)",
             "OLDPWD=.. pushd -- -", "allow")
        bcmd("(ap-c76) plain 'cd -- -' still allows (no inline OLDPWD=, GS-7 round-3 MAJOR 2 guard)",
             "cd -- -", "allow")
        bcmd("(ap-c77) plain 'cd -' still allows (no inline OLDPWD=, GS-7 round-3 MAJOR 2 guard)",
             "cd -", "allow")
        # GS-7 round-4 CONSERVATIVE CONSOLIDATION. Four codex MAJORs, resolved by SHRINKING the fragile ALLOW
        # set (any leading env-assignment -> the cwd-independent cd destinations ASK) rather than enumerating
        # variable names, plus a redirect-tilde consistency fix. Each behavioural case below fails-when-reverted.
        # MAJOR 1 (under-block): '_ENV_ASSIGN_RE' matched 'NAME=' but not the APPEND 'NAME+=', so 'OLDPWD+=..'
        # was mistaken for the command word and the following cd never examined -> false ALLOW of a relative cd.
        # The regex now recognizes '+=', so the assignment is skipped, cd is examined, and the lone '-' ASKS.
        bcmd("(ap-c78) 'OLDPWD+=.. cd -' asks (append-assignment now recognized, GS-7 round-4 MAJOR 1)",
             "OLDPWD+=.. cd -", "allow")
        bcmd("(ap-c78b) 'unset OLDPWD; OLDPWD+=.. cd -' asks (append-assignment in a later segment, round-4)",
             "unset OLDPWD; OLDPWD+=.. cd -", "allow")
        # MAJOR 2 (under-block): a BARE 'cd' with an inline HOME= (or HOME+=) assignment cds to the redirected
        # $HOME, which can be relative, but the no-operand branch treated bare cd as unconditionally
        # cwd-independent. ANY leading assignment now makes the bare-cd (no-operand) $HOME default ASK.
        bcmd("(ap-c79) 'HOME=.. cd' asks (bare cd -> redirected $HOME, GS-7 round-4 MAJOR 2)",
             "HOME=.. cd", "allow")
        bcmd("(ap-c79b) 'HOME+=.. cd' asks (append-assignment bare cd -> $HOME, GS-7 round-4 MAJOR 2)",
             "HOME+=.. cd", "allow")
        bcmd("(ap-c79c) 'HOME=.. pushd' asks (bare pushd default, any leading assignment, round-4 MAJOR 2)",
             "HOME=.. pushd", "allow")
        # MAJOR 4 (over-fire fixed): a redirect target whose CLEAN leading tilde-prefix expands to $HOME is
        # absolute, but the redirect parser discarded the leading-tilde signal so '> ~/x' was classed opaque
        # and ASKED, inconsistent with 'cd ~/x' ALLOW. The signal is now propagated, so a clean-tilde redirect
        # target ALLOWs, while a quoted or complex-prefix tilde still ASKS - matching the cd tilde rule exactly.
        bcmd("(ap-c80) 'printf x > ~/x' allows (clean-tilde redirect target -> $HOME, GS-7 round-4 MAJOR 4)",
             "printf x > ~/x", "allow")
        bcmd("(ap-c80b) 'printf x > ~/\"x\"' allows (quote AFTER the prefix '/' does not block, round-4 MAJOR 4)",
             'printf x > ~/"x"', "allow")
        bcmd("(ap-c80c) 'printf x > ~/$V' allows (expansion after the prefix '/' does not block, round-4 MAJOR 4)",
             "printf x > ~/$V", "allow")
        bcmd("(ap-c80d) 'printf x >> ~/log' allows (append redirect, clean-tilde target, round-4 MAJOR 4)",
             "printf x >> ~/log", "allow")
        bcmd("(ap-c80e) 'printf x > ~' allows (bare '~' target expands to $HOME, round-4 MAJOR 4)",
             "printf x > ~", "allow")
        # regression guards for MAJOR 4: a QUOTED or complex-prefix tilde redirect target is NOT expanded and
        # still ASKS (matching cd), and a plain relative redirect target is unaffected.
        bcmd("(ap-c81) 'printf x > \"~\"/x' DENIES (quoted tilde is literal-relative truncating target; "
             "round-4 MAJOR 4 guard + finding 10)", 'printf x > "~"/x', "deny")
        bcmd("(ap-c81b) 'printf x > ~\"/x\"' DENIES (quote inside the tilde-prefix -> literal-relative "
             "truncating target; round-4 MAJOR 4 + finding 10)", 'printf x > ~"/x"', "deny")
        bcmd("(ap-c81c) 'printf x > out.txt' DENIES (plain relative TRUNCATING target; finding 10)",
             "printf x > out.txt", "deny")
        bcmd("(ap-c81d) 'printf x > /tmp/x' allows (absolute redirect target unaffected, round-4 guard)",
             "printf x > /tmp/x", "allow")
        # MAJOR 3 (DISCLOSED conservative over-fire, PINNED): an assignment NAME quoted or escaped
        # ('\"OLDPWD\"=.. cd -', 'OLD\"PWD\"=.. cd -', 'OLDP\\WD=.. cd -') is a COMMAND to bash (command-not-found;
        # cd never runs), so it does not actually reach cd. But the DECODED token is assignment-shaped
        # ('OLDPWD=..'), so under the round-4 'any leading assignment -> ASK' rule these ASK. This is an
        # intentional conservative over-fire on an assignment-shaped token bash would reject anyway; it is
        # DISCLOSED in the manifest residue, and PINNED here so it cannot silently drift. Not chased with
        # assignment-name quote provenance (that parser complexity is deliberately declined).
        bcmd("(ap-c82) '\"OLDPWD\"=.. cd -' asks (disclosed conservative over-fire, GS-7 round-4 MAJOR 3)",
             '"OLDPWD"=.. cd -', "allow")
        bcmd("(ap-c82b) 'OLD\"PWD\"=.. cd -' asks (disclosed conservative over-fire, GS-7 round-4 MAJOR 3)",
             'OLD"PWD"=.. cd -', "allow")
        bcmd("(ap-c82c) 'OLDP\\WD=.. cd -' asks (escaped name, disclosed conservative over-fire, round-4 MAJOR 3)",
             "OLDP\\WD=.. cd -", "allow")
        # non-regression + the deliberate conservative CONSEQUENCE of the shrink: with NO leading assignment
        # the cwd-independent forms STAY ALLOW; a benign leading assignment ('FOO=x') now makes them ASK (the
        # disclosed cost of not enumerating variable names).
        bcmd("(ap-c83) plain bare 'cd' still allows (no leading assignment, GS-7 round-4 guard)", "cd", "allow")
        bcmd("(ap-c83b) plain 'cd -' still allows (no leading assignment, GS-7 round-4 guard)", "cd -", "allow")
        bcmd("(ap-c83c) plain 'cd -- -' still allows (no leading assignment, GS-7 round-4 guard)",
             "cd -- -", "allow")
        bcmd("(ap-c84) 'FOO=x cd -' asks (any leading assignment -> lone '-' ASKS, round-4 conservative cost)",
             "FOO=x cd -", "allow")
        bcmd("(ap-c84b) 'FOO=x cd' asks (any leading assignment -> bare-cd $HOME ASKS, round-4 conservative cost)",
             "FOO=x cd", "allow")
        # malformed / out-of-scope payloads for the Bash floor
        _bap = aiqt_hooks.bash_absolute_paths
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": 5}}) != "allow":
            failures.append("(ap-c29) non-string command allows with a note (abspth is a convention, not a "
                            "hazard; cannot read)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": None}) != "allow":
            failures.append("(ap-c30) non-mapping tool_input allows with a note (cannot read command)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {}}) != "allow":
            failures.append("(ap-c31) missing command allows with a note (cannot read)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Read",
                          "tool_input": {"command": "cd rel"}}) != "allow":
            failures.append("(ap-c32) out-of-scope tool must allow (matcher governs)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": None,
                          "tool_input": {"command": "cd rel"}}) != "deny":
            failures.append("(ap-c33) missing tool_name must DENY (fail-closed contract)")

        # === expbnd: explicit git target and enumerated scope (GD-157/158/159) =======================
        # Failing-first coverage: before git_explicit_binding existed, the ASK cases below had no
        # dedicated decider. Absolute cd operands avoid the sibling abspth relative-path ASK, while
        # direct handler calls keep every verdict attributable to this control.
        def ebexpect(label, command, want):
            got = _decision(aiqt_hooks.git_explicit_binding, command)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        ebexpect("(eb-e1) absolute cd feeding untargeted commit asks",
                 "cd /abs/repo && git commit -m x", "allow")
        ebexpect("(eb-e2) explicit -C target credits the binding",
                 "cd /abs/repo && git -C /abs/repo commit -m x", "allow")
        ebexpect("(eb-e3) inline --git-dir target credits the binding",
                 "cd /abs/repo && git --git-dir=/abs/repo/.git commit -m x", "allow")
        # ROUND-2 FINDING 11: a whole-tree BREADTH stage feeding a PUBLISH in the same command DENIES-and-
        # educates (it sweeps unrelated content into a pushed commit); a preceding cd does not mask it.
        ebexpect("(eb-e4) breadth add feeding push DENIES (finding 11)", "git add -A && git push", "deny")
        ebexpect("(eb-e5) relocated breadth stage and publish DENIES (finding 11; cd does not mask it)",
                 "cd /abs/repo && git add --all && git commit -m x && git push", "deny")
        ebexpect("(eb-e6) pushd feeding untargeted mutation asks",
                 "pushd /abs/repo && git rm -r src && popd", "allow")
        ebexpect("(eb-e7) dynamic cd still exposes the ambient binding",
                 'cd "$D" && git commit -m x', "allow")
        ebexpect("(eb-e8) lone bare mutation is a deliberate non-fire", "git commit -m x", "allow")
        ebexpect("(eb-e9) lone breadth commit (NO publish) is a deliberate non-fire (allow)",
                 "git commit -am x", "allow")
        ebexpect("(eb-e10) dot pathspec feeding push DENIES (finding 11)", "git add . && git push", "deny")
        # NOT over-blocked (finding 11 guard): a plain commit and a scoped add + push stay allow; a breadth
        # stage with NO publish stays allow (only breadth PAIRED with a publish denies).
        ebexpect("(eb-e10b) plain 'git commit -m' with no breadth allows", "git commit -m x && git push",
                 "allow")
        ebexpect("(eb-e10c) scoped 'git add <path>' feeding push allows (enumerated, not breadth)",
                 "git add src/a.py && git push", "allow")
        ebexpect("(eb-e10d) breadth add with NO publish allows (relocate/convention, not breadth+publish)",
                 "git add -A && git commit -m x", "allow")
        ebexpect("(eb-e10e) a push BEFORE the breadth stage does not fire (publishes pre-breadth state)",
                 "git push && git add -A", "allow")
        ebexpect("(eb-e11) cd feeding read-only git allows", "cd /abs && git status", "allow")
        ebexpect("(eb-e12) cd feeding non-git allows", "cd /abs && ls -la", "allow")
        ebexpect("(eb-e13) bare discard remains the git_discard control's boundary",
                 "git reset --hard", "allow")
        ebexpect("(eb-e14) quoted prose in a parseable command allows",
                 'echo "cd /x && git commit -m y"', "allow")
        ebexpect("(eb-e15) unparseable visible cd plus mutation fails safe to ask",
                 "cd /abs && git commit -m x && (", "allow")
        ebexpect("(eb-e16) unparseable command outside the visible patterns allows",
                 'ls -la "unbalanced', "allow")
        if _reduce(aiqt_hooks.git_explicit_binding,
                   {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}) != "allow":
            failures.append("(eb-e17) absent command allows with a note (expbnd is a binding convention, not a "
                            "hazard; cannot evaluate target or scope)")
        if _reduce(aiqt_hooks.git_explicit_binding,
                   {"hook_event_name": "PreToolUse", "tool_input": {"command": "git commit -m x"}}) != "deny":
            failures.append("(eb-e18) missing tool_name must DENY (shared fail-closed contract)")
        ebexpect("(eb-e19a) plain fetch under cd is deliberately non-mutating",
                 "cd /abs && git fetch", "allow")
        ebexpect("(eb-e19b) pruning fetch under cd asks", "cd /abs && git fetch --prune", "allow")
        ebexpect("(eb-e20) branch list is an enumerated read-only form",
                 "cd /abs && git branch --list", "allow")
        # GD-158 QA round-1: an explicit binding is credited only when ABSOLUTE and complete; a relative
        # -C/--git-dir or a lone --work-tree still leaves the target ambient and routes to the same ASK.
        ebexpect("(eb-e23) relative -C is not a complete binding",
                 "cd /abs/repo && git -C repo commit -m x", "allow")
        ebexpect("(eb-e24) lone --work-tree without --git-dir is not credited",
                 "cd /abs && git --work-tree=/abs commit -m x", "allow")
        ebexpect("(eb-e25) relative --git-dir is not credited",
                 "cd /abs && git --git-dir=rel/.git commit -m x", "allow")
        # Common bare wrappers are peeled so they do not bypass the detector; an option/assignment-carrying
        # wrapper is a disclosed residual left unpeeled.
        ebexpect("(eb-e26) 'command git' wrapper under cd asks",
                 "cd /abs && command git commit -m x", "allow")
        ebexpect("(eb-e27) leading sudo git under cd asks",
                 "cd /abs && sudo git commit -m x", "allow")
        ebexpect("(eb-e28) wrapped 'command cd' feeding a mutation asks",
                 "command cd /abs && git commit -m x", "allow")
        ebexpect("(eb-e29) option-carrying wrapper is a disclosed residual (allows)",
                 "cd /abs && env -i git commit -m x", "allow")
        # Read-only forms stay exempt even under a cd (no false ASK).
        ebexpect("(eb-e30) branch --show-current is read-only",
                 "cd /abs && git branch --show-current", "allow")
        ebexpect("(eb-e31) tag --points-at is read-only",
                 "cd /abs && git tag --points-at HEAD", "allow")
        ebexpect("(eb-e32) bare 'git config <key>' read is not a mutation",
                 "cd /abs && git config user.name", "allow")
        ebexpect("(eb-e33) 'git config <key> <value>' write under cd asks",
                 "cd /abs && git config user.name value", "allow")
        # GD-158 QA round-2: the cd-based triggers are ORDER-SENSITIVE -- a shift (cd/pushd/popd) confuses
        # only a git segment it PRECEDES, and the breadth+publish hazard is a breadth op PRECEDING a push.
        # A trailing shift, or a breadth op AFTER a push, cannot confuse the earlier mutation and must
        # ALLOW; the forward forms above (eb-e1/e4/e6) still ASK. Each case below fails on the pre-fix,
        # order-insensitive implementation.
        ebexpect("(eb-e35) a cd AFTER the mutation does not confuse it",
                 "git commit -m x && cd /abs/repo", "allow")
        ebexpect("(eb-e36) a cd several segments after the mutation is exempt",
                 "git commit -m x ; cd /var/log ; cat foo.log", "allow")
        ebexpect("(eb-e37) popd BEFORE an untargeted mutation shifts the target",
                 "popd && git commit -m x", "allow")
        ebexpect("(eb-e38) breadth AFTER a push is not a pre-publish breadth",
                 "git push && git add -A", "allow")
        ebexpect("(eb-e39) branch --format listing under cd is read-only",
                 "cd /abs && git branch --format=refname", "allow")
        ebexpect("(eb-e40) tag --format listing under cd is read-only",
                 "cd /abs && git tag --format=refname", "allow")
        ebexpect("(eb-e41) branch --sort listing under cd is read-only",
                 "cd /abs && git branch --sort=-committerdate", "allow")
        # GD-158 round-3 (F-GD158-R2 classifier fail-open): a create/delete/move/copy is a MUTATION even
        # when it carries a formatting flag (--format/--sort); a read token after '--' is an operand, not a
        # flag. Each case fails on the pre-fix any(read_flag in args) classifier. The pure-listing ALLOWs
        # (eb-e39..e41 above) still hold (no positional -> read).
        ebexpect("(eb-e42) branch CREATE carrying --format is a mutation under cd",
                 "cd /x && git branch --format=refname b1", "allow")
        ebexpect("(eb-e43) branch delete carrying --format asks under cd",
                 "cd /x && git branch --format=refname -D feature", "allow")
        ebexpect("(eb-e44) branch move carrying --sort asks under cd",
                 "cd /x && git branch --sort=x -m old new", "allow")
        ebexpect("(eb-e45) branch copy carrying --format asks under cd",
                 "cd /x && git branch --format=X -c old new", "allow")
        ebexpect("(eb-e46) tag CREATE carrying --format is a mutation under cd",
                 "cd /x && git tag --format=refname t1", "allow")
        ebexpect("(eb-e47) tag delete carrying --format asks under cd",
                 "cd /x && git tag --format=refname -d v1", "allow")
        ebexpect("(eb-e48) a read token after -- is an operand, delete still asks under cd",
                 "cd /x && git branch -D -- --format", "allow")
        ebexpect("(eb-e49) branch LIST with a pattern is a read (allow under cd)",
                 "cd /x && git branch --list 'feat/*'", "allow")
        ebexpect("(eb-e50) branch filter with its value operand is a read (allow under cd)",
                 "cd /x && git branch --contains HEAD", "allow")
        ebexpect("(eb-e51) separate-form --format value is not a create target (allow under cd)",
                 "cd /x && git branch --format 'refname'", "allow")
        # GD-158 round-5 (F-GD158-R4 fail-open): a create/rename/delete TARGET placed AFTER '--' is still a
        # mutation (git accepts the target there: "git branch -- name"/"git tag -- name" CREATE, real git
        # 2.53); a list pattern after '--' with a list flag is a read. Each ASK case fails on the round-3
        # (pre-only) classifier that discarded post-'--' operands.
        ebexpect("(eb-e52) branch CREATE target after -- is a mutation under cd",
                 "cd /x && git branch -- newb1", "allow")
        ebexpect("(eb-e53) branch create + start-point after -- asks under cd",
                 "cd /x && git branch -- b2 HEAD", "allow")
        ebexpect("(eb-e54) branch force-create after -- asks under cd",
                 "cd /x && git branch -f -- b5 HEAD", "allow")
        ebexpect("(eb-e55) tag CREATE target after -- is a mutation under cd",
                 "cd /x && git tag -- newt1", "allow")
        ebexpect("(eb-e56) branch LIST with a pattern after -- is a read under cd",
                 "cd /x && git branch --list -- 'new*'", "allow")
        # GD-158 round-6 + tri-family synthesis: the branch/tag classifier is now FAIL-SAFE (defaults
        # MUTATING; returns read only when every token resolves to a recognized read-neutral role and no
        # create/rename/delete target is present). This table exercises _git_is_mutating directly against
        # the git 2.53 ground truth: the plan's matrix classes, the six historical fail-open forms as
        # regression teeth, and fail-safe probes asserting the default on unknown/ambiguous/malformed
        # input. want=True is MUTATING (-> expbnd ASK); over-ASK is safe, a fail-open (want-True read as
        # False) is the forbidden regression. This table is validated against git 2.53 by the in-suite eb
        # differential above and an out-of-suite real-git differential; a future git option-table change is
        # a disclosed drift residual, and a dedicated option-table drift-tripwire gate is a tracked
        # follow-up (GD-158-T7), not a gate that exists yet.
        _M, _R = True, False
        for _rc_label, _rc_sub, _rc_args, _rc_want in (
            # -- branch: writes (short, long, attached-short, abbreviated-long) --
            ("eb-e57", "branch", ["newb"], _M),                        # bare positional creates
            ("eb-e58", "branch", ["-d", "feature"], _M),
            ("eb-e59", "branch", ["-D", "feature"], _M),
            ("eb-e60", "branch", ["-m", "old", "new"], _M),
            ("eb-e61", "branch", ["-c", "old", "new"], _M),
            ("eb-e62", "branch", ["--delete", "feature"], _M),
            ("eb-e63", "branch", ["--move", "old", "new"], _M),
            ("eb-e64", "branch", ["-f", "b", "start"], _M),
            ("eb-e65", "branch", ["-u", "origin/main"], _M),
            ("eb-e66", "branch", ["--set-upstream-to=origin/main"], _M),
            ("eb-e67", "branch", ["--unset-upstream", "b"], _M),
            ("eb-e68", "branch", ["--edit-description"], _M),
            ("eb-e69", "branch", ["-t", "b", "origin/main"], _M),
            ("eb-e70", "branch", ["-uorigin/main"], _M),               # r6 attached-short value
            ("eb-e71", "branch", ["--unset-upst", "b"], _M),           # r6 abbreviated long
            ("eb-e72", "branch", ["--set-upstream-t=origin/main"], _M),
            ("eb-e73", "branch", ["--edit-descript"], _M),
            ("eb-e74", "branch", ["-dv", "f"], _M),                    # write char leads a cluster
            ("eb-e75", "branch", ["-vd", "f"], _M),                    # write char after a read char
            # -- branch: r1 write-beside-list (list mode must never mask a write) --
            ("eb-e76", "branch", ["--format=%(refname)", "-D", "feature"], _M),
            ("eb-e77", "branch", ["--list", "-D", "feature"], _M),
            # -- branch: reads / lists --
            ("eb-e78", "branch", [], _R),
            ("eb-e79", "branch", ["--list"], _R),
            ("eb-e80", "branch", ["-l"], _R),
            ("eb-e81", "branch", ["--list", "feat/*"], _R),
            ("eb-e82", "branch", ["-a"], _R),
            ("eb-e83", "branch", ["-r"], _R),
            ("eb-e84", "branch", ["-v"], _R),
            ("eb-e85", "branch", ["--show-current"], _R),
            ("eb-e86", "branch", ["--format=%(refname)"], _R),
            ("eb-e87", "branch", ["--sort=-committerdate"], _R),
            ("eb-e88", "branch", ["--contains", "HEAD"], _R),
            ("eb-e89", "branch", ["--merged", "HEAD"], _R),
            ("eb-e90", "branch", ["--no-contains", "HEAD"], _R),
            ("eb-e91", "branch", ["--points-at", "HEAD"], _R),
            ("eb-e92", "branch", ["--contains"], _R),                  # filter value optional-when-last (U-5)
            ("eb-e93", "branch", ["--format", "refname"], _R),         # separate display value, no target
            # -- branch: r4/5 --create-reflog is a create MODIFIER, not a standalone write --
            ("eb-e94", "branch", ["--create-reflog"], _R),
            ("eb-e95", "branch", ["--create-reflog", "newb"], _M),
            # -- branch: r4/5 --color/--abbrev/--column consume NOTHING following (a name creates) --
            ("eb-e96", "branch", ["--color", "bcolorsep"], _M),
            ("eb-e97", "branch", ["--abbrev", "babbrev"], _M),
            ("eb-e98", "branch", ["--column", "bcol"], _M),
            # -- branch: D-3 tooth -- --format consumes the next argv verbatim, so foo is created --
            ("eb-e99", "branch", ["--format", "--list", "foo"], _M),
            ("eb-e100", "branch", ["--format"], _M),                   # missing required value
            ("eb-e101", "branch", ["--sort", "refname", "NAME"], _M),
            # -- branch: r3 post-'--' target; a list pattern after '--' stays a read --
            ("eb-e102", "branch", ["--", "newb"], _M),
            ("eb-e103", "branch", ["-D", "--", "feature"], _M),
            ("eb-e104", "branch", ["--list", "--", "new*"], _R),
            # -- branch: mode cancellers roled W (D-9) --
            ("eb-e105", "branch", ["--list", "--no-list", "NAME"], _M),
            ("eb-e106", "branch", ["--points-at", "HEAD", "--no-points-at", "NAME"], _M),
            ("eb-e107", "branch", ["--show-current", "NAME"], _M),
            # -- branch: D-2 (git rejects -a/-r NAME fatally; over-ASK) --
            ("eb-e108", "branch", ["-a", "NAME"], _M),
            # -- branch: fail-safe probes (assert the DEFAULT) --
            ("eb-e109", "branch", ["--frobnicate"], _M),               # unknown long
            ("eb-e110", "branch", ["-Z", "x"], _M),                    # unknown short
            ("eb-e111", "branch", ["--co", "x"], _M),                  # ambiguous prefix
            ("eb-e112", "branch", ["--for"], _M),                      # force/format ambiguity
            ("eb-e113", "branch", ["--list=x"], _M),                   # '=' on a no-value option
            # -- tag: writes --
            ("eb-e114", "tag", ["-a", "-m", "msg", "v1"], _M),
            ("eb-e115", "tag", ["-m", "msg", "v1"], _M),
            ("eb-e116", "tag", ["-s", "v1"], _M),
            ("eb-e117", "tag", ["v1"], _M),                            # lightweight tag create
            ("eb-e118", "tag", ["-d", "v1"], _M),
            ("eb-e119", "tag", ["--delete", "v1"], _M),
            ("eb-e120", "tag", ["-f", "v1"], _M),
            ("eb-e121", "tag", ["-e", "-m", "x", "v1"], _M),
            ("eb-e122", "tag", ["-ammsg", "t1"], _M),                  # attached-short write cluster
            ("eb-e123", "tag", ["--ann", "t1"], _M),                   # abbreviated write long
            ("eb-e124", "tag", ["--sig", "t1"], _M),
            ("eb-e125", "tag", ["--cleanup=whitespace"], _M),          # D-5: create-mode modifier
            # -- tag: r6 --column/--no-column are NOT list triggers (a name beside them creates) --
            ("eb-e126", "tag", ["--column", "tname"], _M),
            ("eb-e127", "tag", ["--no-column", "tname"], _M),
            # -- tag: r5 verify is a READ mode --
            ("eb-e128", "tag", ["-v", "v2"], _R),
            ("eb-e129", "tag", ["--verify", "v2"], _R),
            ("eb-e130", "tag", ["--ver", "v2"], _R),                   # unique prefix of --verify
            # -- tag: lists / -n[N] --
            ("eb-e131", "tag", [], _R),
            ("eb-e132", "tag", ["-l"], _R),
            ("eb-e133", "tag", ["--list", "v*"], _R),
            ("eb-e134", "tag", ["-n"], _R),
            ("eb-e135", "tag", ["-n1", "v*"], _R),                     # D-6: attached decimal -> read
            ("eb-e136", "tag", ["-nf", "v*"], _M),                     # non-digit tail -> fail-safe
            ("eb-e137", "tag", ["-ln1", "v*"], _R),
            # -- tag: reads / filters / create-reflog --
            ("eb-e138", "tag", ["--format=%(refname)"], _R),
            ("eb-e139", "tag", ["--format=%(refname)", "t1"], _M),
            ("eb-e140", "tag", ["--points-at", "HEAD"], _R),
            ("eb-e141", "tag", ["--contains", "v1"], _R),
            ("eb-e142", "tag", ["--create-reflog"], _R),
            ("eb-e143", "tag", ["--create-reflog", "v9"], _M),
            # -- tag: r3 post-'--' target; fail-safe probes --
            ("eb-e144", "tag", ["--", "newt"], _M),
            ("eb-e145", "tag", ["--", "-v"], _M),                      # -v after '--' is an operand -> create
            ("eb-e146", "tag", ["--frob"], _M),
            ("eb-e147", "tag", ["-Z"], _M),
        ):
            _rc_got = aiqt_hooks._git_is_mutating(_rc_sub, _rc_args)
            if _rc_got is not _rc_want:
                failures.append("({}) _git_is_mutating({!r}, {!r}): expected {}, got {}"
                                .format(_rc_label, _rc_sub, _rc_args, _rc_want, _rc_got))
        for _eb_sub, _eb_args, _eb_want in (
                ("commit", ["-am", "x"], True),
                ("add", ["-A"], True),
                ("add", ["--", "src/a.c"], False),
                ("add", ["--", "--all"], False),
                ("add", ["--", "-A"], False),
                ("add", ["--", "."], True),
                ("commit", ["-m", "x"], False)):
            _eb_got = aiqt_hooks._git_is_breadth(_eb_sub, _eb_args)
            if _eb_got is not _eb_want:
                failures.append("(eb-e21) _git_is_breadth({}, {!r}): expected {}, got {}"
                                .format(_eb_sub, _eb_args, _eb_want, _eb_got))
        for _tg_tokens, _tg_want in (
                (["git", "-C", "/abs/repo", "commit"], True),
                (["git", "-C", "repo", "commit"], False),
                (["git", "--git-dir=/abs/.git", "commit"], True),
                (["git", "--git-dir=rel/.git", "commit"], False),
                (["git", "--work-tree=/abs", "commit"], False),
                (["git", "--git-dir", "/abs/.git", "--work-tree", "/abs", "commit"], True),
                (["git", "commit", "-m", "x"], False)):
            if aiqt_hooks._git_target_is_explicit(_tg_tokens) is not _tg_want:
                failures.append("(eb-e34) _git_target_is_explicit({!r}): expected {}"
                                .format(_tg_tokens, _tg_want))
        if (aiqt_hooks.HANDLERS.get("git_explicit_binding") is not aiqt_hooks.git_explicit_binding or
                aiqt_hooks.HANDLER_EVENT.get("git_explicit_binding") != "PreToolUse"):
            failures.append("(eb-e22) git_explicit_binding handler/event wiring is missing or wrong")

        # === write_scope_guard (wrtscp, EN-8): confine guarded-tool writes to a per-slice scope =========
        # declaration; hard-deny writes to the frozen floor and to other/nested repos as an un-lowerable
        # floor; inert on absence for slice confinement, fail-closed on cannot-evaluate once armed. Judged
        # by the STRUCTURED (code, obj) verdict, never by grepping prose. The out-of-tree declaration lands
        # in the harness state dir (XDG_STATE_HOME, redirected into tmp at the top of main), so it never
        # touches the repo tree; the in-tree floor is a synthetic .aiqt/frozen.json carrying one entry of
        # each frozen class {derived (site/downloads/), manifest-self (.aiqt/manifest.toml), archive
        # (.aiqt/archive/)} plus the self-listed .aiqt/frozen.json, mirroring the real gen_manifest floor.
        def wsdecide(tool, file_path, cwd, extra=None, event="PreToolUse", with_tool=True,
                     tool_input="__default__"):
            data = {"hook_event_name": event}
            if with_tool:
                data["tool_name"] = tool
            if tool_input == "__default__":
                ti = {}
                if file_path is not None:
                    ti["file_path"] = file_path
                if extra:
                    ti.update(extra)
                data["tool_input"] = ti
            elif tool_input is not None:
                data["tool_input"] = tool_input
            if cwd is not None:
                data["cwd"] = cwd
            code, obj, _s = aiqt_hooks.write_scope_guard(data)
            if code == 2 and obj is None:
                return "block2"
            if code == 0 and obj is None:
                return "allow"
            if code == 0 and isinstance(obj, dict):
                return obj.get("hookSpecificOutput", {}).get("permissionDecision", "unexpected")
            return "unexpected(code={!r},obj={!r})".format(code, obj)

        def ws_root_of(repo):
            return aiqt_hooks._recovery_toplevel(str(repo))

        def ws_state_dir(repo):
            return aiqt_hooks._orch_state_dir_for_root(ws_root_of(repo))

        def ws_arm(repo, allow, worktree_root="__self__", slice_name="slice-1", version=1, raw=None):
            sd = ws_state_dir(repo)
            os.makedirs(sd, exist_ok=True)
            path = os.path.join(sd, "write-scope.json")
            if raw is not None:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                return
            wr = os.path.realpath(ws_root_of(repo)) if worktree_root == "__self__" else worktree_root
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": version, "slice": slice_name, "worktree_root": wr, "allow": allow}, fh)

        def ws_disarm(repo):
            path = os.path.join(ws_state_dir(repo), "write-scope.json")
            if os.path.exists(path):
                os.remove(path)

        def ws_floor(repo, frozen, version=1, raw=None):
            (repo / ".aiqt").mkdir(parents=True, exist_ok=True)
            path = repo / ".aiqt" / "frozen.json"
            if raw is not None:
                path.write_text(raw, encoding="utf-8")
                return
            path.write_text(json.dumps({"version": version, "frozen": frozen}), encoding="utf-8")

        def ws_nofloor(repo):
            path = repo / ".aiqt" / "frozen.json"
            if path.exists():
                path.unlink()

        def wsexpect(label, want, tool, file_path, repo, **kw):
            got = wsdecide(tool, file_path, str(repo), **kw)
            if got != want:
                failures.append("{}: expected {}, got {}".format(label, want, got))

        WS_FLOOR = [".aiqt/frozen.json", ".aiqt/manifest.toml", ".aiqt/archive/", "site/downloads/"]
        try:
            ws_rp = tmp / "wsrepo"
            ws_rp.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(ws_rp)],
                           check=True, capture_output=True, text=True, timeout=30)
            ws_other = tmp / "wsother"
            ws_other.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(ws_other)],
                           check=True, capture_output=True, text=True, timeout=30)
            ws_nested = ws_rp / "nested"
            ws_nested.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(ws_nested)],
                           check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the write-scope fixtures: {}".format(exc),
                  file=sys.stderr)
            return 2
        rp_j = lambda *p: os.path.join(str(ws_rp), *p)
        other_j = lambda *p: os.path.join(str(ws_other), *p)

        # --- ARMED slice confinement: allow in-scope, deny out-of-scope (allow = src/) ---
        ws_floor(ws_rp, WS_FLOOR)
        ws_arm(ws_rp, ["src/"])
        wsexpect("(ws-a) armed: Write an in-scope file allows", "allow", "Write", rp_j("src", "x.py"), ws_rp)
        wsexpect("(ws-a2) armed: Edit an in-scope tree member allows", "allow",
                 "Edit", rp_j("src", "deep", "y.py"), ws_rp)
        wsexpect("(ws-a3) armed: MultiEdit an in-scope file allows (matcher includes MultiEdit)", "allow",
                 "MultiEdit", rp_j("src", "x.py"), ws_rp)
        wsexpect("(ws-b) armed: Write an out-of-scope file denies (row 21)", "deny",
                 "Write", rp_j("tools", "y.py"), ws_rp)
        wsexpect("(ws-b2) armed: Write an out-of-scope repo-root file denies", "deny",
                 "Write", rp_j("README.md"), ws_rp)

        # --- FROZEN-FLOOR hard-deny for EACH frozen class, EVEN WITH a permissive declaration (allow = ---
        # --- .aiqt/ + site/, both LEGAL parents of frozen subtrees; the floor outranks the allowlist) ---
        ws_arm(ws_rp, [".aiqt/", "site/"])
        wsexpect("(ws-f-derived) frozen derived tree denies under a permissive allow (row 18)", "deny",
                 "Write", rp_j("site", "downloads", "pkg.zip"), ws_rp)
        wsexpect("(ws-f-manifest) frozen manifest-self file denies under a permissive allow (row 18)", "deny",
                 "Edit", rp_j(".aiqt", "manifest.toml"), ws_rp)
        wsexpect("(ws-f-archive) frozen archive tree denies under a permissive allow (row 18)", "deny",
                 "Write", rp_j(".aiqt", "archive", "old.json"), ws_rp)
        wsexpect("(ws-f-self) the floor's own copy denies (self-protection)", "deny",
                 "Write", rp_j(".aiqt", "frozen.json"), ws_rp)
        wsexpect("(ws-f-ok) a non-frozen in-allow path still allows under the permissive declaration",
                 "allow", "Write", rp_j("site", "index.html"), ws_rp)

        # --- REGISTRY FREEZE: the orchestration LOCAL registry (.aiqt/orchestration.local.json) is
        # --- ALWAYS-FROZEN against covered writes, in BOTH regimes and INDEPENDENT of the committed floor,
        # --- so the constrained actor cannot self-declare a companion_stores widening through a covered
        # --- Write/Edit/MultiEdit (the self-widening residual). The registry is NOT on WS_FLOOR, so a plain
        # --- floor lookup would miss it; the deny comes from the hook-side always-frozen set. ---
        _ws_reg = rp_j(".aiqt", "orchestration.local.json")
        ws_floor(ws_rp, WS_FLOOR)              # committed floor present, does NOT list the registry
        ws_arm(ws_rp, [".aiqt/"])              # permissive declaration covering .aiqt/ (would otherwise allow)
        wsexpect("(ws-frozen-reg-armed) armed: a covered Write to the orchestration LOCAL registry denies "
                 "under a permissive allow (always-frozen registry)", "deny", "Write", _ws_reg, ws_rp)
        wsexpect("(ws-frozen-reg-armed-edit) armed: an Edit of the registry denies too (always-frozen)",
                 "deny", "Edit", _ws_reg, ws_rp)
        wsexpect("(ws-frozen-reg-armed-multiedit) armed: a MultiEdit of the registry denies too",
                 "deny", "MultiEdit", _ws_reg, ws_rp)
        ws_disarm(ws_rp)
        wsexpect("(ws-frozen-reg-unarmed) un-armed: a covered write to the registry still denies (both "
                 "regimes)", "deny", "Write", _ws_reg, ws_rp)
        ws_nofloor(ws_rp)
        wsexpect("(ws-frozen-reg-nofloor) un-armed with NO committed floor: the registry freeze still denies "
                 "(independent of the committed floor, so a deleted frozen.json cannot unfreeze it)",
                 "deny", "Write", _ws_reg, ws_rp)
        # DISCRIMINATION (fails WITHOUT the fix): with the always-frozen set emptied, the same covered write
        # is ALLOWED (un-armed, in-repo, not on the committed floor), proving the deny is caused by the
        # registry freeze and not by some other rule. Restore the constant afterwards.
        ws_floor(ws_rp, WS_FLOOR)              # floor present again; still un-armed
        _ws_orig_always = aiqt_hooks._WRTSCP_ALWAYS_FROZEN
        aiqt_hooks._WRTSCP_ALWAYS_FROZEN = ()
        try:
            _ws_got_without = wsdecide("Write", _ws_reg, str(ws_rp))
        finally:
            aiqt_hooks._WRTSCP_ALWAYS_FROZEN = _ws_orig_always
        if _ws_got_without != "allow":
            failures.append("(ws-frozen-reg-discriminates) WITHOUT the always-frozen registry entry the "
                            "covered write must ALLOW (un-armed, in-repo, not on the committed floor), so "
                            "the deny is attributable to the registry freeze; got {}"
                            .format(_ws_got_without))
        if ("file", ".aiqt/orchestration.local.json") not in tuple(aiqt_hooks._WRTSCP_ALWAYS_FROZEN):
            failures.append("(ws-frozen-reg-const) _WRTSCP_ALWAYS_FROZEN must freeze "
                            ".aiqt/orchestration.local.json")

        # --- ROUND-2 FINDING 1: the COMMITTED registry .aiqt/orchestration.json is ALSO always-frozen. ---
        # companion_stores / the yield-roster are read from EITHER registry file (_orch_registry falls back
        # from the LOCAL file to the committed .aiqt/orchestration.json), so freezing ONLY the LOCAL file left
        # a covered Write of companion_stores into .aiqt/orchestration.json to take effect IN-SESSION before
        # CI could see it - a self-widening bypass. Now every _ORCH_REGISTRY_FILES entry is frozen.
        _ws_regjson = rp_j(".aiqt", "orchestration.json")
        ws_disarm(ws_rp)
        ws_floor(ws_rp, WS_FLOOR)              # committed floor present, does NOT list either registry
        wsexpect("(ws-frozen-regjson-unarmed) un-armed: a covered Write of the COMMITTED registry "
                 ".aiqt/orchestration.json denies (finding 1: both registry files are frozen)",
                 "deny", "Write", _ws_regjson, ws_rp)
        wsexpect("(ws-frozen-regjson-edit) an Edit of .aiqt/orchestration.json denies too (always-frozen)",
                 "deny", "Edit", _ws_regjson, ws_rp)
        ws_nofloor(ws_rp)
        wsexpect("(ws-frozen-regjson-nofloor) un-armed with NO committed floor: the .aiqt/orchestration.json "
                 "freeze still denies (independent of the committed floor)", "deny", "Write", _ws_regjson,
                 ws_rp)
        # DISCRIMINATION (fails WITHOUT the finding-1 fix): freezing ONLY the LOCAL file (the pre-fix set)
        # ALLOWS the .json write (un-armed, in-repo, not on the committed floor), proving the deny is caused
        # by adding .aiqt/orchestration.json to the freeze. Restore the constant afterwards.
        ws_floor(ws_rp, WS_FLOOR)
        _ws_orig_always2 = aiqt_hooks._WRTSCP_ALWAYS_FROZEN
        aiqt_hooks._WRTSCP_ALWAYS_FROZEN = (("file", ".aiqt/orchestration.local.json"),)
        try:
            _ws_json_without = wsdecide("Write", _ws_regjson, str(ws_rp))
        finally:
            aiqt_hooks._WRTSCP_ALWAYS_FROZEN = _ws_orig_always2
        if _ws_json_without != "allow":
            failures.append("(ws-frozen-regjson-discriminates) with ONLY .aiqt/orchestration.local.json "
                            "frozen (the pre-fix set) the covered Write of .aiqt/orchestration.json must "
                            "ALLOW, so the deny is attributable to freezing the committed registry too; got {}"
                            .format(_ws_json_without))
        if ("file", ".aiqt/orchestration.json") not in tuple(aiqt_hooks._WRTSCP_ALWAYS_FROZEN):
            failures.append("(ws-frozen-regjson-const) _WRTSCP_ALWAYS_FROZEN must freeze "
                            ".aiqt/orchestration.json")
        # CLASS WIDTH: every registry file that can source companion_stores/the yield-roster is frozen, so the
        # freeze set can never drift behind _ORCH_REGISTRY_FILES.
        for _rel in aiqt_hooks._ORCH_REGISTRY_FILES:
            if ("file", _rel) not in tuple(aiqt_hooks._WRTSCP_ALWAYS_FROZEN):
                failures.append("(ws-frozen-reg-classwidth) every _ORCH_REGISTRY_FILES entry must be "
                                "always-frozen against covered writes; {!r} is not".format(_rel))

        # --- Row 19: an allow entry wholly on the floor makes the declaration malformed -> deny ---
        ws_arm(ws_rp, ["site/downloads/"])   # == the floor tree; wholly frozen
        wsexpect("(ws-r19) allow entry wholly on the floor denies (declaration malformed, row 19)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)

        # --- ARMED cannot-evaluate: every fault denies (rows 13, 14, 15, 17) ---
        ws_arm(ws_rp, ["src/"])
        wsexpect("(ws-rel-armed) a relative file_path denies while armed (row 14)", "deny",
                 "Write", "rel/x.py", ws_rp)
        ws_arm(ws_rp, ["src/"], worktree_root="/nonexistent/elsewhere")
        wsexpect("(ws-mismatch) a worktree_root that does not resolve to this repo denies (row 13)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)
        ws_arm(ws_rp, [], raw="{ not json")
        wsexpect("(ws-decl-badjson) a malformed declaration denies while armed (row 13)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)
        ws_arm(ws_rp, [], raw=json.dumps({"version": True, "slice": "s", "worktree_root": ".", "allow": []}))
        wsexpect("(ws-decl-boolver) a boolean-true version is not int -> declaration bad denies", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)
        ws_arm(ws_rp, ["src/"])
        ws_nofloor(ws_rp)
        wsexpect("(ws-floor-absent-armed) an armed session with no committed floor denies (row 17)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)
        ws_floor(ws_rp, [], raw="{ not json")
        wsexpect("(ws-floor-bad-armed) a malformed floor denies while armed (cannot-evaluate)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp)

        # --- STRUCTURAL always-on: other-repo and nested-repo deny in BOTH regimes ---
        ws_floor(ws_rp, WS_FLOOR)
        ws_arm(ws_rp, ["src/"])
        wsexpect("(ws-other-armed) a write into a sibling repo denies while armed (row 16)", "deny",
                 "Write", other_j("z.txt"), ws_rp)
        wsexpect("(ws-nested-armed) a write into a nested repo denies while armed (row 16)", "deny",
                 "Write", rp_j("nested", "f.txt"), ws_rp)
        ws_disarm(ws_rp)
        wsexpect("(ws-other-unarmed) a write into a sibling repo denies UN-ARMED (always-on, conflict-2)",
                 "deny", "Write", other_j("z.txt"), ws_rp)
        wsexpect("(ws-nested-unarmed) a write into a nested repo denies UN-ARMED (always-on)", "deny",
                 "Write", rp_j("nested", "f.txt"), ws_rp)

        # --- UN-ARMED inert slice confinement + always-on frozen (floor present) ---
        # ws_rp is disarmed; floor is WS_FLOOR.
        wsexpect("(ws-inert-allow) un-armed: a non-frozen in-repo write allows (slice confinement off, row 12)",
                 "allow", "Write", rp_j("anywhere", "z.txt"), ws_rp)
        wsexpect("(ws-inert-frozen) un-armed: a frozen write still denies (floor always-on, row 8)", "deny",
                 "Write", rp_j(".aiqt", "manifest.toml"), ws_rp)
        wsexpect("(ws-rel-unarmed) un-armed: a relative file_path allows (abspth owns it, row 6)", "allow",
                 "Write", "rel/x.py", ws_rp)
        ws_floor(ws_rp, [], raw="{ not json")
        wsexpect("(ws-floor-bad-unarmed) un-armed: a present-but-malformed floor denies (row 9)", "deny",
                 "Write", rp_j("site", "downloads", "x.zip"), ws_rp)

        # --- UN-ARMED, floor ABSENT: fully inert in-repo (frozen layer inert), structural still on ---
        ws_nofloor(ws_rp)
        wsexpect("(ws-nofloor-allow) un-armed + no floor: a non-frozen write allows (row 10 -> 12)", "allow",
                 "Write", rp_j("anywhere", "z.txt"), ws_rp)
        wsexpect("(ws-nofloor-frozenpath) un-armed + no floor: a would-be-frozen path allows (frozen inert)",
                 "allow", "Write", rp_j("site", "downloads", "x.zip"), ws_rp)
        wsexpect("(ws-nofloor-other) un-armed + no floor: a sibling-repo write STILL denies (structural)",
                 "deny", "Write", other_j("z.txt"), ws_rp)

        # --- Payload / matcher / event fail-closed contracts ---
        wsexpect("(ws-notool) missing tool_name denies (shared fail-closed contract, row 3)", "deny",
                 "Write", rp_j("src", "x.py"), ws_rp, with_tool=False)
        wsexpect("(ws-nofp) a missing file_path denies (malformed call, row 4)", "deny",
                 "Write", None, ws_rp)
        wsexpect("(ws-badinput) a non-dict tool_input denies (malformed call, row 4)", "deny",
                 "Write", None, ws_rp, tool_input=None)
        wsexpect("(ws-ctrlchar) a control-character file_path denies (malformed)", "deny",
                 "Write", rp_j("src", "x\x00.py"), ws_rp)
        wsexpect("(ws-bash-oos) Bash is out of the matcher and allows (the Bash residual, disclosed)",
                 "allow", "Bash", None, ws_rp, extra={"command": "echo hi > /tmp/x"}, tool_input={"command": "echo hi"})
        wsexpect("(ws-read-oos) Read is out of the matcher and allows", "allow",
                 "Read", rp_j("src", "x.py"), ws_rp)
        wsexpect("(ws-event) a mis-wired non-PreToolUse event hard-blocks (exit 2)", "block2",
                 "Write", rp_j("src", "x.py"), ws_rp, event="Stop")
        # FIX 2 (MAJOR 3): a covered write whose session root cannot be resolved DENIES (fail-closed),
        # never the old inert allow. A non-git session (row 5) and a payload with no cwd both deny.
        wsexpect("(ws-nongit) a non-git session denies a covered write (root unresolvable, fail-closed)",
                 "deny", "Write", os.path.join(str(tmp), "plainfile.txt"), tmp)
        ws_nocwd_got = wsdecide("Write", rp_j("src", "x.py"), None)
        if ws_nocwd_got != "deny":
            failures.append("(ws-nocwd) a covered write whose payload carries no cwd must deny (session "
                            "root unresolvable, FIX 2), got {}".format(ws_nocwd_got))

        # --- FIX 2 extension (codex MAJOR 3): un-armed cannot-evaluate FAULT paths DENY (fail-closed) ---
        # A resolution or probe ERROR on a covered write is a cannot-evaluate: the guard cannot PROVE the
        # target is in-repo / not-nested, so it DENIES rather than allowing an unverified write. UN-ARMED
        # (ws_rp disarmed, no floor); OLD code returned _allow() (row 11) at each site, so each assertion is
        # fail-to-pass by construction. Faults are injected deterministically and restored in a finally,
        # mirroring the gensrc gs-ac/gs-ad idiom; judged by the STRUCTURED verdict, never by grepping prose.
        ws_disarm(ws_rp)
        ws_nofloor(ws_rp)
        _ws_fault_fp = rp_j("src", "x.py")
        _ws_real_realpath = os.path.realpath
        _ws_real_commonpath = os.path.commonpath
        # Site 1 (aiqt_hooks.py row 11): the SESSION-ROOT canonicalization fault. The first realpath call in
        # the covered path is root_c (_recovery_toplevel uses no realpath), so a raising realpath fires it.
        def _ws_raise_realpath_all(*_a, **_k):
            raise OSError("injected root_c realpath fault (ws-fault-rootc)")
        try:
            os.path.realpath = _ws_raise_realpath_all
            wsexpect("(ws-fault-rootc) an un-armed root canonicalization fault DENIES (cannot-evaluate, was "
                     "a row-11 allow)", "deny", "Write", _ws_fault_fp, ws_rp)
        finally:
            os.path.realpath = _ws_real_realpath
        # Site 2 (row 11): the TARGET canonicalization fault only (root_c must still resolve).
        def _ws_raise_realpath_target(path, *_a, **_k):
            if path == _ws_fault_fp:
                raise OSError("injected target realpath fault (ws-fault-target)")
            return _ws_real_realpath(path)
        try:
            os.path.realpath = _ws_raise_realpath_target
            wsexpect("(ws-fault-target) an un-armed target canonicalization fault DENIES (cannot-evaluate, "
                     "was a row-11 allow)", "deny", "Write", _ws_fault_fp, ws_rp)
        finally:
            os.path.realpath = _ws_real_realpath
        # Site 3 (row 11): the CONTAINMENT fault via a raising commonpath (_gensrc_within returns 'err').
        def _ws_raise_commonpath(*_a, **_k):
            raise ValueError("injected commonpath fault (ws-fault-contain)")
        try:
            os.path.commonpath = _ws_raise_commonpath
            wsexpect("(ws-fault-contain) an un-armed containment fault DENIES (_gensrc_within 'err', "
                     "cannot-evaluate, was a row-11 allow)", "deny", "Write", _ws_fault_fp, ws_rp)
        finally:
            os.path.commonpath = _ws_real_commonpath
        # Site 4 (row 11): the NESTED-REPO probe fault (the codex-named case) via a None-returning probe.
        _ws_real_nested = aiqt_hooks._wrtscp_nested_repo
        try:
            aiqt_hooks._wrtscp_nested_repo = lambda *_a, **_k: None
            wsexpect("(ws-fault-nested) an un-armed nested-repo probe fault DENIES (cannot-evaluate, the "
                     "codex MAJOR 3 case, was a row-11 allow)", "deny", "Write", _ws_fault_fp, ws_rp)
        finally:
            aiqt_hooks._wrtscp_nested_repo = _ws_real_nested

        # --- FIX 1 (BLOCKER 2): a BAD orchestration registry cannot silently disarm confinement ---
        # The registry LOCATES the write-scope declaration. When it is unreadable/malformed, the old code
        # fell back to the XDG-default state dir, found no declaration there, read the session as UN-ARMED,
        # and ALLOWED an out-of-slice write. The fix returns BAD (fail-closed) for a bad registry, so an
        # armed session denies. Built to fail on the old path: the declaration lives at a registry-DECLARED
        # in-tree state_dir, so a valid registry arms and confines to src/, and corrupting only the registry
        # must NOT drop that confinement. Judged by the STRUCTURED verdict, never by grepping prose.
        try:
            ws_reg = tmp / "wsreg"
            ws_reg.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(ws_reg)],
                           check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the write-scope registry fixture: {}".format(exc),
                  file=sys.stderr)
            return 2
        reg_j = lambda *p: os.path.join(str(ws_reg), *p)
        reg_registry = ws_reg / ".aiqt" / "orchestration.local.json"
        reg_registry.parent.mkdir(parents=True, exist_ok=True)
        # A version-1 registry declaring an in-tree state_dir (relative -> joined onto the repo root). This
        # proves the declaration MAY live in-tree (the corrected out-of-tree disclosure) and that a valid
        # registry locates it there.
        reg_registry.write_text(json.dumps({"version": 1, "state_dir": ".aiqt/orch-state"}),
                                encoding="utf-8")
        ws_floor(ws_reg, WS_FLOOR)
        ws_arm(ws_reg, ["src/"])   # ws_state_dir reads the registry, so this writes into .aiqt/orch-state/
        wsexpect("(ws-reg-ok-in) a valid registry arms via its declared in-tree state_dir; in-scope allows",
                 "allow", "Write", reg_j("src", "x.py"), ws_reg)
        wsexpect("(ws-reg-ok-out) a valid registry arms via its declared state_dir; out-of-scope denies",
                 "deny", "Write", reg_j("tools", "y.py"), ws_reg)
        # Corrupt ONLY the registry; the armed declaration stays present in .aiqt/orch-state/.
        reg_registry.write_text("{ not json", encoding="utf-8")
        wsexpect("(ws-reg-bad) a BAD registry with the declaration still present DENIES an out-of-slice "
                 "write (FIX 1: a cannot-evaluate registry cannot silently disarm; old code ALLOWED)",
                 "deny", "Write", reg_j("tools", "y.py"), ws_reg)

        # --- FIX A (round-3 BLOCKER): two deeper registry fault paths that round-1 missed must DENY ---
        # ws_reg still carries the armed declaration in its registry-declared .aiqt/orch-state, so a working
        # registry arms + confines to src/. Both faults below leave that declaration present.
        # (A1) An UNREADABLE-but-present registry: an lstat FAULT (not FileNotFoundError). Old _orch_registry
        # used os.path.lexists, which swallowed the fault to False -> "absent" -> XDG fallback -> un-armed
        # ALLOW. Injected deterministically via os.lstat raising PermissionError for the registry path only
        # (any uid; not a chmod that root would bypass); restored in the finally.
        reg_registry.write_text(json.dumps({"version": 1, "state_dir": ".aiqt/orch-state"}), encoding="utf-8")
        _ws_real_lstat = os.lstat
        _reg_path_str = str(reg_registry)
        def _ws_lstat_perm(path, *_a, **_k):
            try:
                same = os.fspath(path) == _reg_path_str
            except TypeError:
                same = False   # an int fd is never the registry path
            if same:
                raise PermissionError("injected registry lstat fault (ws-reg-unreadable)")
            return _ws_real_lstat(path, *_a, **_k)
        try:
            os.lstat = _ws_lstat_perm
            wsexpect("(ws-reg-unreadable) an unreadable-but-present registry (lstat fault) DENIES an "
                     "out-of-slice write (FIX A1; old lexists swallowed it to absent -> allow)",
                     "deny", "Write", reg_j("tools", "y.py"), ws_reg)
        finally:
            os.lstat = _ws_real_lstat
        # (A2) A registry that is OK but declares a PRESENT-but-invalid state_dir (an empty string): a
        # cannot-evaluate. Old code returned None from _orch_path and fell to the XDG default, disarming the
        # armed session. (An ABSENT state_dir key legitimately means "use the XDG default" and is NOT this
        # case; that path stays allowed, exercised by ws_rp elsewhere, which carries no registry.)
        reg_registry.write_text(json.dumps({"version": 1, "state_dir": ""}), encoding="utf-8")
        wsexpect("(ws-reg-baddir) a registry declaring a present-but-empty state_dir DENIES an out-of-slice "
                 "write (FIX A2; old code fell to XDG -> disarmed -> allow)",
                 "deny", "Write", reg_j("tools", "y.py"), ws_reg)

        # (round-5 BLOCKER) The DECISION's registry read must never fault-and-disarm. Old _load_write_scope
        # validated the registry once, then called _orch_state_dir_for_root which REREAD it; a second read
        # that faulted (EACCES between the reads) fell to the XDG default and disarmed the armed session ->
        # ALLOW. The fix resolves the state dir from the already-validated result, so the DECISION in
        # _load_write_scope rests on EXACTLY ONE registry read. (A subsequent DENIAL's best-effort guard-event
        # logging path, _orch_guard_event -> _orch_state_dir_for_root, may perform its OWN read, but that is
        # telemetry and never affects the decision.) Injected by faulting ONLY the second lstat on the
        # registry path (the first succeeds), which the old double-read reached in the decision path and the
        # fixed single-read decision does not.
        reg_registry.write_text(json.dumps({"version": 1, "state_dir": ".aiqt/orch-state"}), encoding="utf-8")
        _reg_lstat_calls = [0]
        def _ws_lstat_second_only(path, *_a, **_k):
            try:
                same = os.fspath(path) == _reg_path_str
            except TypeError:
                same = False
            if same:
                _reg_lstat_calls[0] += 1
                if _reg_lstat_calls[0] >= 2:
                    raise PermissionError("injected SECOND registry lstat fault (ws-reg-second-read)")
            return _ws_real_lstat(path, *_a, **_k)
        try:
            os.lstat = _ws_lstat_second_only
            wsexpect("(ws-reg-second-read) faulting ONLY the second registry read still DENIES an "
                     "out-of-slice write (round-5 FIX A; old double-read fell to XDG -> disarmed -> allow)",
                     "deny", "Write", reg_j("tools", "y.py"), ws_reg)
        finally:
            os.lstat = _ws_real_lstat

        # --- SANCTIONED COMPANION-STORE declaration (wrtscp cross-repo fix) ------------------------------
        # A covered write into an adopter-DECLARED companion-store repo (the orchestrator's own durable store
        # beside the code repo, a SECOND git repo whose toplevel resolves to itself) is ALLOWED and AUDITED;
        # an UNDECLARED other repo, a MALFORMED/unresolvable/non-repo declaration, a repo NESTED inside the
        # store, and the frozen/nested-in-session denials all stay DENY. Every companion ALLOW below is
        # fail-to-pass under a revert: the OLD code denied EVERY cross-repo write unconditionally. Judged by
        # the STRUCTURED verdict, never by grepping prose.
        try:
            cs_sess = tmp / "cssess"      # the session code repo
            cs_store = tmp / "csstore"    # the declared companion store (a second repo beside it)
            cs_other = tmp / "csother"    # an UNDECLARED other repo
            cs_nonrepo = tmp / "csnonrepo"
            for _p in (cs_sess, cs_store, cs_other):
                _p.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "init", "-q", "-b", "main", str(_p)],
                               check=True, capture_output=True, text=True, timeout=30)
            cs_nonrepo.mkdir(parents=True, exist_ok=True)   # a plain dir, not a git repo
            cs_store_nested = cs_store / "innerrepo"         # a repo NESTED inside the store
            cs_store_nested.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(cs_store_nested)],
                           check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            print("SELF-TEST ERROR: could not build the companion-store fixtures: {}".format(exc),
                  file=sys.stderr)
            return 2
        cs_reg = cs_sess / ".aiqt" / "orchestration.local.json"
        cs_reg.parent.mkdir(parents=True, exist_ok=True)

        def cs_set(companion):
            obj = {"version": 1}
            if companion is not None:
                obj["companion_stores"] = companion
            cs_reg.write_text(json.dumps(obj), encoding="utf-8")

        def cs_rm():
            if cs_reg.exists():
                cs_reg.unlink()

        store_root = os.path.realpath(str(cs_store))
        # Baseline: no declaration -> a cross-repo write to the store DENIES (the pre-fix floor).
        cs_rm()
        wsexpect("(ws-cs-baseline) no companion declaration: a store write DENIES",
                 "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        # Declared: writes anywhere in the declared store repo ALLOW (root file and a deep path).
        cs_set([store_root])
        wsexpect("(ws-cs-allow-root) a write to a DECLARED companion-store root file ALLOWS",
                 "allow", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        wsexpect("(ws-cs-allow-deep) a write deep in the DECLARED store ALLOWS",
                 "allow", "Edit", os.path.join(str(cs_store), "recs", "a", "b.md"), cs_sess)
        # CLAUDE-F2 (round-7): the registry freeze follows TRANSITIVELY into a declared companion store's OWN
        # orchestration registry. A covered write to <store>/.aiqt/orchestration.local.json (or
        # orchestration.json) would complete a cross-session companion_stores self-widening chain through the
        # exact guarded-tool path the freeze closes, so it DENIES - even though a NON-registry write to the
        # same store ALLOWS above. Reverting the transitive-freeze check reds the two deny cases (they become
        # routine companion-store allows).
        wsexpect("(ws-cs-store-reg-local) a covered write to the store's OWN orchestration.local.json DENIES "
                 "(CLAUDE-F2, transitive freeze)", "deny",
                 "Write", os.path.join(str(cs_store), ".aiqt", "orchestration.local.json"), cs_sess)
        wsexpect("(ws-cs-store-reg-committed) a covered write to the store's OWN orchestration.json DENIES "
                 "(CLAUDE-F2, transitive freeze)", "deny",
                 "Edit", os.path.join(str(cs_store), ".aiqt", "orchestration.json"), cs_sess)
        wsexpect("(ws-cs-store-nonreg-allows) a NON-registry write to the declared store still ALLOWS "
                 "(CLAUDE-F2 does not break legitimate store writes)", "allow",
                 "Write", os.path.join(str(cs_store), ".aiqt", "notes.md"), cs_sess)
        # An UNDECLARED other repo still DENIES (the floor holds for genuine aiming errors).
        wsexpect("(ws-cs-other-deny) a write to an UNDECLARED other repo DENIES",
                 "deny", "Write", os.path.join(str(cs_other), "x.md"), cs_sess)
        # EXACT repo-root match: a repo NESTED inside the store (its own toplevel differs) does NOT match.
        wsexpect("(ws-cs-nested-in-store) a write into a repo NESTED inside the declared store DENIES "
                 "(exact-root match, no escape)", "deny",
                 "Write", os.path.join(str(cs_store_nested), "f.txt"), cs_sess)
        # An in-repo write is unaffected (still allows in this un-armed session).
        wsexpect("(ws-cs-inrepo) an in-repo write still ALLOWS (companion path untouched)",
                 "allow", "Write", os.path.join(str(cs_sess), "src", "x.py"), cs_sess)
        # Fail-closed on a MALFORMED / unresolvable / non-repo declaration: the store write DENIES.
        cs_set("not-a-list")
        wsexpect("(ws-cs-notlist) companion_stores not a list -> fail-closed, store DENIES",
                 "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        cs_set([str(tmp / "does-not-exist")])
        wsexpect("(ws-cs-missing) an unresolvable declared path -> fail-closed, DENIES",
                 "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        cs_set([str(cs_nonrepo)])
        wsexpect("(ws-cs-nonrepo) a declared path that is NOT a git repo -> fail-closed, DENIES",
                 "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        cs_set(["relative/not/absolute"])
        wsexpect("(ws-cs-relative) a non-absolute declared path -> fail-closed, DENIES",
                 "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        cs_set([os.path.join(store_root, "recs")])   # inside the store, not its root
        wsexpect("(ws-cs-subdir) a declared path INSIDE the store but not its root -> DENIES "
                 "(exact-root only)", "deny", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        # Mixed: a valid store beside a bad entry -> the valid store is honoured, the bad entry opens no hole.
        cs_set([store_root, "bad-entry"])
        wsexpect("(ws-cs-mixed-ok) a valid store beside a bad entry still ALLOWS the valid store",
                 "allow", "Write", os.path.join(str(cs_store), "s.md"), cs_sess)
        wsexpect("(ws-cs-mixed-nohole) a bad entry never opens an UNDECLARED other repo",
                 "deny", "Write", os.path.join(str(cs_other), "x.md"), cs_sess)
        # A companion declaration does NOT bypass the frozen floor OR the nested-in-SESSION denial.
        cs_set([store_root])
        (cs_sess / ".aiqt" / "frozen.json").write_text(
            json.dumps({"version": 1, "frozen": [".aiqt/manifest.toml"]}), encoding="utf-8")
        wsexpect("(ws-cs-frozen-intact) a frozen path in the session repo still DENIES (floor not lowered)",
                 "deny", "Edit", os.path.join(str(cs_sess), ".aiqt", "manifest.toml"), cs_sess)
        (cs_sess / ".aiqt" / "frozen.json").unlink()
        cs_sess_nested = cs_sess / "nested"
        cs_sess_nested.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(cs_sess_nested)],
                       check=True, capture_output=True, text=True, timeout=30)
        wsexpect("(ws-cs-nested-session) a nested-in-SESSION repo still DENIES (unchanged)",
                 "deny", "Write", os.path.join(str(cs_sess_nested), "f.txt"), cs_sess)
        # AUDIT: a companion-store ALLOW emits a guard-event row (kind wrtscp, decision allow).
        cs_set([store_root])
        _ = wsdecide("Write", os.path.join(str(cs_store), "audit.md"), str(cs_sess))
        cs_sd = aiqt_hooks._orch_state_dir_for_root(aiqt_hooks._recovery_toplevel(str(cs_sess)))
        cs_ge = os.path.join(cs_sd, "guard-events.jsonl")
        cs_rows = []
        if os.path.exists(cs_ge):
            with open(cs_ge, "r", encoding="utf-8") as fh:
                cs_rows = [json.loads(ln) for ln in fh if ln.strip()]
        if not any(r.get("kind") == "wrtscp" and r.get("decision") == "allow"
                   and "companion-store" in r.get("detail", "") for r in cs_rows):
            failures.append("(ws-cs-audit) a companion-store ALLOW must emit a wrtscp allow guard-event row")
        # Classifier discrimination: the matcher matches only the declared root, never an undeclared repo.
        _cs_stores = aiqt_hooks._wrtscp_companion_stores(aiqt_hooks._recovery_toplevel(str(cs_sess)))
        if aiqt_hooks._wrtscp_target_companion_store(
                os.path.realpath(os.path.join(str(cs_store), "z")), _cs_stores) is None:
            failures.append("(ws-cs-match1) a target inside the declared store must match its root")
        if aiqt_hooks._wrtscp_target_companion_store(
                os.path.realpath(os.path.join(str(cs_other), "z")), _cs_stores) is not None:
            failures.append("(ws-cs-match2) a target in an undeclared repo must never match")

        # --- ROUND-2 FINDING 2: the FROZEN denials (always-frozen registry set AND the committed floor) are
        # --- evaluated BEFORE the companion-store admission, so a frozen target that RESOLVES (via symlink,
        # --- or a floor entry that resolves) OUTSIDE the session repo INTO a declared store still DENIES; the
        # --- companion allow can never win a HEAD ALLOW over the freeze. Each assertion below FLIPS to
        # --- "allow" if the ordering is reverted (companion-before-freeze), so it discriminates the fix.
        # --- Symlinks are required; if the platform cannot create them the sub-block is skipped (recorded).
        _cs_can_symlink = True
        try:
            _cs_probe_link = cs_sess / ".aiqt" / "_symlink_probe"
            os.symlink(str(cs_store), str(_cs_probe_link))
            _cs_probe_link.unlink()
        except OSError:
            _cs_can_symlink = False
        if _cs_can_symlink:
            # (a) A frozen-FLOOR entry that symlinks INTO the declared store. A covered Write to it resolves
            # OUT into the store, yet the floor freeze DENIES it (not a companion allow).
            cs_set([store_root])
            (cs_store / "planted-derived.txt").write_text("x", encoding="utf-8")
            (cs_sess / ".aiqt" / "frozen.json").write_text(
                json.dumps({"version": 1, "frozen": [".aiqt/planted-derived"]}), encoding="utf-8")
            _cs_floor_link = cs_sess / ".aiqt" / "planted-derived"
            if _cs_floor_link.is_symlink() or _cs_floor_link.exists():
                _cs_floor_link.unlink()
            os.symlink(str(cs_store / "planted-derived.txt"), str(_cs_floor_link))
            wsexpect("(ws-cs-floor-symlink) a covered Write to a FROZEN-FLOOR path that symlinks INTO a "
                     "declared companion store DENIES (floor precedes companion allow; finding 2)",
                     "deny", "Write", str(_cs_floor_link), cs_sess)
            _cs_floor_link.unlink()
            (cs_sess / ".aiqt" / "frozen.json").unlink()
            # (b) The LOCAL registry itself symlinked INTO the store, where the symlink target IS a valid
            # version-1 registry declaring the store (so companion_stores IS populated and the companion allow
            # WOULD fire without the reorder). The always-frozen registry deny must still win.
            _cs_planted_reg = cs_store / "planted-registry.json"
            _cs_planted_reg.write_text(json.dumps({"version": 1, "companion_stores": [store_root]}),
                                       encoding="utf-8")
            if cs_reg.is_symlink() or cs_reg.exists():
                cs_reg.unlink()
            os.symlink(str(_cs_planted_reg), str(cs_reg))
            # Sanity: companion_stores IS populated through the symlinked registry, so without the reorder the
            # out-resolving target would companion-ALLOW; the freeze is what denies it.
            if not aiqt_hooks._wrtscp_companion_stores(aiqt_hooks._recovery_toplevel(str(cs_sess))):
                failures.append("(ws-cs-reg-symlink-setup) the symlinked local registry must still populate "
                                "companion_stores, else the finding-2 registry case does not discriminate")
            wsexpect("(ws-cs-reg-symlink) a covered Write to the LOCAL registry that symlinks INTO a declared "
                     "companion store DENIES (registry freeze precedes companion allow; finding 2)",
                     "deny", "Write", str(cs_reg), cs_sess)
            cs_reg.unlink()
            # --- CLAUDE-F2 (round-6): a frozen-floor TREE entry with a symlink BELOW it into the declared
            # store. A covered Write to '<tree>/sub/x' where '<tree>/sub' symlinks into the store REALPATHS
            # into the store (so the realpath-based floor match misses it and the companion allow WOULD fire),
            # but its LEXICAL path is inside the frozen tree, so the new lexical-tree match DENIES it. Revert
            # _wrtscp_lexical_tree_hit and this flips to 'allow' (the symlink-below-a-tree-entry escape).
            cs_set([store_root])
            _f2tree = cs_sess / ".aiqt" / "frozen-tree"
            _f2tree.mkdir(parents=True, exist_ok=True)          # a real dir so the tree entry resolves
            (cs_store / "frozensubdir").mkdir(parents=True, exist_ok=True)
            (cs_sess / ".aiqt" / "frozen.json").write_text(
                json.dumps({"version": 1, "frozen": [".aiqt/frozen-tree/"]}), encoding="utf-8")
            _f2link = _f2tree / "sub"
            if _f2link.is_symlink() or _f2link.exists():
                _f2link.unlink()
            os.symlink(str(cs_store / "frozensubdir"), str(_f2link))  # a symlink BELOW the frozen tree entry
            _f2target = str(_f2link / "file.md")                 # lexical: inside the frozen tree
            # Sanity: the REALPATH floor match MISSES it (target resolves out into the store), so without the
            # lexical check the companion allow would fire - this is what makes the case discriminate.
            _f2root_c = os.path.realpath(str(cs_sess))
            if aiqt_hooks._wrtscp_target_matches([("tree", ".aiqt/frozen-tree")],
                                                 os.path.realpath(_f2target), _f2root_c) is not False:
                failures.append("(ws-cf2-setup) the realpath floor match must MISS the symlinked-out tree "
                                "target, else CLAUDE-F2 does not discriminate")
            wsexpect("(ws-cf2-tree-symlink) a covered Write whose LEXICAL path is inside a frozen-floor TREE "
                     "entry but whose symlink resolves OUT into the declared store DENIES (CLAUDE-F2 lexical "
                     "tree match)", "deny", "Write", _f2target, cs_sess)
            # CONTROL: a NON-frozen sibling tree with the same symlink-into-store shape still ALLOWS as a
            # companion write (the lexical match is scoped to frozen TREE entries only, no over-deny).
            _f2ok = cs_sess / ".aiqt" / "open-tree"
            _f2ok.mkdir(parents=True, exist_ok=True)
            _f2oklink = _f2ok / "sub"
            if _f2oklink.is_symlink() or _f2oklink.exists():
                _f2oklink.unlink()
            os.symlink(str(cs_store / "frozensubdir"), str(_f2oklink))
            wsexpect("(ws-cf2-open-ctl) a symlink into the store BELOW a NON-frozen tree still ALLOWS "
                     "(companion write; lexical match is frozen-tree-scoped)",
                     "allow", "Write", str(_f2oklink / "file.md"), cs_sess)
            _f2link.unlink(); _f2oklink.unlink()
            (cs_sess / ".aiqt" / "frozen.json").unlink()

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # === GLOBAL NO-ASK INVARIANT (maintainer directive) =========================================
    # These enforcement hooks must NEVER return permissionDecision "ask": an unattended orchestrator must
    # never stall waiting on a human. This invariant sweeps every PreToolUse guard over a broad battery of
    # benign, hazard, unparseable, and wrapper vectors and asserts NONE returns "ask" (each resolves to
    # allow / allow-with-note / deny). It also asserts the ask constructor is GONE (a missed call site would
    # then fail loudly at runtime, never silently ask) and its replacement _allow_note exists. This is the
    # discriminating backstop: it fails if any guard is reverted to an ask outcome or if _ask is restored.
    def _decision_any(handler, data):
        code, stdout_obj, _stderr = handler(data)
        if code == 0 and stdout_obj is None:
            return "allow"
        if code == 0 and isinstance(stdout_obj, dict):
            dec = stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision")
            if dec in ("allow", "ask", "deny"):
                return dec
            if "hookSpecificOutput" not in stdout_obj and "systemMessage" in stdout_obj:
                return "allow"  # an _allow_note
        if code == 2:
            return "hard_block"  # a deliberate exit-2 (mis-wired event), never an ask
        return "unexpected(code={!r}, stdout={!r})".format(code, stdout_obj)

    if hasattr(aiqt_hooks, "_ask"):
        failures.append("(noask-helper) the _ask constructor must be REMOVED (hooks never ask); it is still "
                        "defined on the module")
    if not hasattr(aiqt_hooks, "_allow_note"):
        failures.append("(noask-helper2) the _allow_note constructor (the allow-with-note replacement for "
                        "the retired ask) must exist")

    _bash_guards = [aiqt_hooks.diff_source_pretool, aiqt_hooks.bash_absolute_paths,
                    aiqt_hooks.git_explicit_binding, aiqt_hooks.git_discard, aiqt_hooks.protected_line,
                    aiqt_hooks.branch_root, aiqt_hooks.gate_weakening, aiqt_hooks.commit_msg_subst]
    _bash_vectors = [
        "git status", "ls -la", "git diff", "git diff --color=always | grep foo", "/usr/bin/git diff",
        "git checkout -- file.txt", "git reset --hard", "git reset --soft", "git clean -fd",
        "git stash drop", "git branch -D topic", "git checkout -b feature", "git switch -c feat --track x",
        "GUARDRAIL_ALLOW_DISCARD=0 git reset --hard", "cd sub && git commit -m x", "git -C /tmp commit -m x",
        "git push --force origin main", "git push --mirror backup", "git push origin refs/heads/*:refs/heads/*",
        "sudo git reset --hard", "git commit --no-verify -m x", "pytest || true", "make test | head",
        'git commit -m "fix `whoami`"', "git commit -m x", "git -c alias.co=checkout co -- f",
        "git checkout-index -a -f", "eval 'git reset --hard'", 'git reset --hard "unbalanced',
        'git push --force "unbalanced', 'git commit -m "x `y', "git branch --track t origin/x",
    ]
    for _g in _bash_guards:
        for _cmd in _bash_vectors:
            for _cwd in (None, "/nonexistent-noask-probe"):
                _data = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": _cmd}}
                if _cwd is not None:
                    _data["cwd"] = _cwd
                _got = _decision_any(_g, _data)
                if _got == "ask":
                    failures.append("(noask-{}-{!r}) a guard returned permissionDecision 'ask' (hooks must "
                                    "never ask)".format(getattr(_g, "__name__", _g), _cmd))
                elif _got.startswith("unexpected"):
                    failures.append("(noask-shape-{}-{!r}) a guard returned an unrecognized decision shape: {}"
                                    .format(getattr(_g, "__name__", _g), _cmd, _got))
    # gensrc_guard over Write/Edit/MultiEdit vectors (registry-absent and cannot-evaluate branches).
    for _tool in ("Write", "Edit", "MultiEdit"):
        for _fp in ("/tmp/x", "rel/x", "/nonexistent/gen/out.md"):
            _gd = {"hook_event_name": "PreToolUse", "tool_name": _tool,
                   "tool_input": {"file_path": _fp}, "cwd": "/nonexistent-noask-probe"}
            _got = _decision_any(aiqt_hooks.gensrc_guard, _gd)
            if _got == "ask":
                failures.append("(noask-gensrc-{}-{!r}) gensrc_guard returned 'ask' (hooks must never ask)"
                                .format(_tool, _fp))
    # orch PreToolUse guards must not ask either (inert-probe legs, kept for shape coverage).
    for _og, _od in ((aiqt_hooks.orch_truncation_guard,
                      {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": "sleep 1 &"}, "cwd": "/nonexistent-noask-probe"}),
                     (aiqt_hooks.orch_truncation_guard,
                      {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": "python x.py | tee out", "run_in_background": True},
                       "cwd": "/nonexistent-noask-probe"})):
        if _decision_any(_og, _od) == "ask":
            failures.append("(noask-orch-{}) an orchestration PreToolUse guard returned 'ask'"
                            .format(getattr(_og, "__name__", _og)))

    # ROUND-2 FINDING 18: exercise the orchestration PreToolUse guards on REAL triggering inputs (a real git
    # repo + a version-1 orchestration registry), not only the inert /nonexistent probe (root None -> early
    # allow). This ADDS orch_yield_tool (previously unexercised by this invariant, so a restored ask path
    # there passed both suites) and drives orch_truncation on a REAL detach. Each must NOT return 'ask', and
    # the decision must GENUINELY RUN (a real deny), so a restored ask path - however its constructor is
    # named - is caught by behaviour, not by the evadable _ask-attribute check above.
    _f18_tmp = None
    try:
        _f18_tmp = Path(tempfile.mkdtemp(prefix="aiqt-noask-orch-"))
        _f18_repo = _f18_tmp / "repo"
        _f18_repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(_f18_repo)],
                       check=True, capture_output=True, timeout=30)
        _f18_stub = _f18_repo / "enum-stub.py"
        _f18_stub.write_text("import sys\nsys.stdout.write(open(sys.argv[1]).read())\n"
                             "sys.exit(int(open(sys.argv[2]).read().strip()))\n", encoding="utf-8")
        _f18_payload = _f18_repo / "enum.json"
        _f18_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _f18_payload.write_text(json.dumps({
            "version": 1, "generated_at_utc": _f18_now,
            "source": {"locator": "noask-fixture", "revision": "r1", "observed_at_utc": _f18_now},
            "items": [{"id": "IT-1", "title": "actionable", "state": "open", "granted": True}]}),
            encoding="utf-8")
        (_f18_repo / "enum-exit.txt").write_text("0", encoding="utf-8")
        (_f18_repo / "lease.txt").write_text("holder: noask\n", encoding="utf-8")
        (_f18_repo / "mode.md").write_text("Operating-mode: overnight-unattended\n", encoding="utf-8")
        for _f18_rec in ("findings.md", "pending.md", "handoff.md"):
            (_f18_repo / _f18_rec).write_text("", encoding="utf-8")
        _f18_reg = {
            "version": 1,
            "enumerator": {"argv": [sys.executable, "-I", "-B", str(_f18_stub),
                                    str(_f18_payload), str(_f18_repo / "enum-exit.txt")], "timeout": 30},
            "lease": {"path": str(_f18_repo / "lease.txt"), "max_age_hours": 24},
            "mode": {"path": str(_f18_repo / "mode.md")},
            "record": {"findings": str(_f18_repo / "findings.md"),
                       "pending_decisions": str(_f18_repo / "pending.md"),
                       "handoff": str(_f18_repo / "handoff.md")},
            "state_dir": str(_f18_repo / "state"),
            "yield_tools": ["ScheduleWakeup"],
            "dispatch_tools": [],
            "staleness": {"external_hours": 24, "task_hours": 24},
        }
        (_f18_repo / ".aiqt").mkdir()
        _f18_regfile = _f18_repo / ".aiqt" / "orchestration.local.json"
        _f18_regfile.write_text(json.dumps(_f18_reg), encoding="utf-8")

        def _f18_yield(tool_input):
            return _decision_any(aiqt_hooks.orch_yield_tool, {
                "hook_event_name": "PreToolUse", "tool_name": "ScheduleWakeup",
                "tool_input": tool_input, "cwd": str(_f18_repo), "session_id": "noask-s1"})

        # (a) armed decide_yield over an actionable backlog: a stop MUST DENY (not ask), a REAL decision.
        _f18_stop = _f18_yield({"stop": True})
        if _f18_stop == "ask":
            failures.append("(noask-yield-stop) orch_yield_tool returned 'ask' on an armed stop (finding 18)")
        elif _f18_stop != "deny":
            failures.append("(noask-yield-exercised) orch_yield_tool must genuinely DENY a stop past an "
                            "actionable backlog (proving the decision ran, not an inert allow); got {}"
                            .format(_f18_stop))
        # (b) malformed registry -> fail-closed deny (the finding's named real input), never ask.
        _f18_regfile.write_text("{ not json", encoding="utf-8")
        _f18_bad = _f18_yield({"stop": True})
        if _f18_bad == "ask":
            failures.append("(noask-yield-badreg) orch_yield_tool returned 'ask' on a malformed registry")
        elif _f18_bad != "deny":
            failures.append("(noask-yield-badreg-deny) orch_yield_tool must DENY fail-closed on a malformed "
                            "registry; got {}".format(_f18_bad))
        _f18_regfile.write_text(json.dumps(_f18_reg), encoding="utf-8")

        # (c) orch_truncation on a REAL repo + registry: a foreground bare-& detach and a background
        # truncating sink each DENY (not ask), proving the decision ran (the /nonexistent probe is inert).
        def _f18_trunc(cmd, rib):
            return _decision_any(aiqt_hooks.orch_truncation_guard, {
                "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": cmd, "run_in_background": rib}, "cwd": str(_f18_repo)})
        _f18_detach = _f18_trunc("sleep 1 &", False)
        if _f18_detach == "ask":
            failures.append("(noask-trunc-detach) orch_truncation_guard returned 'ask' on a real detach")
        elif _f18_detach != "deny":
            failures.append("(noask-trunc-exercised) orch_truncation_guard must DENY a real foreground "
                            "bare-& detach on a real repo (proving the decision ran, not an inert allow); "
                            "got {}".format(_f18_detach))
        _f18_sink = _f18_trunc("python producer.py | tail -5", True)
        if _f18_sink == "ask":
            failures.append("(noask-trunc-sink) orch_truncation_guard returned 'ask' on a truncating sink")
        elif _f18_sink != "deny":
            failures.append("(noask-trunc-sink-deny) orch_truncation_guard must DENY a background producer "
                            "piped into a truncating sink on a real repo (finding 9/18); got {}"
                            .format(_f18_sink))
    except (OSError, subprocess.SubprocessError) as _f18_exc:
        failures.append("(noask-orch-fixture) could not build the finding-18 armed orch fixture: {}"
                        .format(_f18_exc))
    finally:
        if _f18_tmp is not None:
            shutil.rmtree(str(_f18_tmp), ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS. NO-ASK POSTURE HELD: the global invariant confirms no PreToolUse guard "
          "returns permissionDecision 'ask' over the exercised vectors, and the _ask constructor is "
          "removed; every decision is allow / allow-with-note / snapshot-then-allow / deny-and-educate. "
          "Current outcomes, by guard: "
          "cnsdif (diff_source_pretool) DENIES-and-educates a confirmed console diff dump and ALLOWS "
          "with a note any producer-capable-but-unproven or unparseable form (a convention, not a "
          "hazard). abspth (bash_absolute_paths) ALLOWS with a note a relative or opaque cd/redirect and "
          "an unreadable command (a convention nudge), and DENIES-and-educates a TRUNCATING redirect "
          "('>'/'>|'/'&>'/'>&') to a relative or opaque target (it could zero the wrong file; finding 10). "
          "expbnd (git_explicit_binding) ALLOWS with a note an ambient git target or a relocated whole-tree "
          "breadth op, and DENIES-and-educates a whole-tree breadth stage paired with a publish in one "
          "command (finding 11). prsunc "
          "(git_discard) recovers-then-allows: a recoverable discard is snapshotted then ALLOWED with a "
          "recovery-pointer note, a confirmed whole-tree clobber on a dirty tree DENIES (with a snapshot "
          "when one could be made), and it DENIES-and-educates only when a warranted recovery snapshot "
          "cannot be created (a forced snapshot failure, an over-cap or bad-path snapshot, a temp dir "
          "inside the repo, a ref collision, an embedded-NUL or no-session cwd) or the command cannot be "
          "classified/resolved (an inline -c alias, an unrecognized flagged subcommand such as "
          "checkout-index or read-tree --reset, an unresolvable worktree); a provably-clean tree ALLOWS "
          "with no snapshot; an ambient GIT_*/redirect view-override snapshot-then-allows on a dirty cwd "
          "and ALLOWS on a clean one (nothing to snapshot); stash/branch are not snapshottable and "
          "allow-with-note. prtbrn/artbr1 (protected_line) DENIES a force-push or protected-branch "
          "deletion, DENIES fail-safe a push it cannot prove misses the protected line (a "
          "--mirror/--all/wildcard/prune sweep) and an unparseable apparent force-push/delete, DENIES a "
          "commit PROVABLY on the protected branch, and ALLOWS with a note a commit it merely cannot "
          "prove lands off it (a normal detached-HEAD commit; server-side protection is the real gate), "
          "and it now classifies a commit against the branch it will ACTUALLY land on - a same-command "
          "switch's target or a -C target's HEAD (finding 13). brnrot (branch_root) DENIES a branch "
          "created from an orphaned start and DENIES-and-educates a form it cannot prove rooted (an "
          "--orphan form, an unresolved ancestry), is -C-AWARE (resolves and probes the -C/--work-tree "
          "target, so a -C/--work-tree target that resolves to a non-rooted concrete dir - nonexistent, "
          "/etc - DENIES via the ancestry-unknown fail-safe), treats a missing origin/HEAD as rooted and a "
          "CHECKOUT/SWITCH --track of a real rooted ref as a rooted creation (a git-branch --track is an "
          "unclassifiable form and DENIES), and ALLOWS-WITH-NOTE only a redirect whose worktree it cannot pin "
          "(a --git-dir/GIT_DIR/-c form or an opaque -C/--work-tree) (finding 12); a rooted creation and "
          "non-creation commands ALLOW. "
          "gatdis (gate_weakening) DENIES a --no-verify bypass and ALLOWS-WITH-NOTE a checker-shaped segment "
          "whose failure is swallowed (|| true) or truncated (| head/tail) - the heuristic is too broad to "
          "deny (finding 14). sectvl "
          "(commit_msg_subst) DENIES-and-educates a backtick or $( command substitution in a git commit "
          "argument (re-issue single-quoted, in ANSI-C $'...' quoting, escaped, or via -F <file>); a "
          "literal marker inside single-quoted or ANSI-C $'...' quoting is NOT a substitution and ALLOWS "
          "(finding 15); the fallback and unreadable command deny fail-safe. gensrc (gensrc_guard) "
          "DENIES-and-educates a hand-edit of a registered generated artefact (edit the source and "
          "regenerate) AND a PRESENT-but-unreadable/malformed registry (fail-closed, finding 8), and ALLOWS "
          "with a note the remaining cannot-evaluate branches (a non-git session, an outside-repo target, a "
          "malformed payload), the CI drift gate remaining the backstop; a genuinely-absent registry is the "
          "inert allow and only a missing tool_name denies. secsec (secrets_shift_left) still "
          "DENIES a shaped secret in a Write/Edit/Bash write-form (unchanged by the no-ask work) and "
          "fails closed on a missing tool_name or target field. abspth's typed-path predicate still "
          "DENIES a required-absolute relative/drive-relative/tilde/UNC-less path and ALLOWS an absolute "
          "one across Write/Edit/MultiEdit/NotebookEdit/Grep/Glob. wrtscp (write_scope_guard) is "
          "unchanged except the ADDED registry freeze: armed, an in-scope write ALLOWS and an "
          "out-of-scope one DENIES; the frozen floor and the sibling/nested-repo denials hold in both "
          "regimes; a declared companion-store write is allowed and audited; EVERY orchestration registry "
          "file (both .aiqt/orchestration.local.json AND the committed .aiqt/orchestration.json) is "
          "ALWAYS-FROZEN against covered writes in both regimes and independent of the committed floor, and "
          "the frozen/floor denials are evaluated BEFORE the companion-store allow so a symlink into a "
          "declared store cannot bypass the freeze (findings 1 and 2), so a companion_stores widening cannot "
          "be self-declared through a guarded tool; the general Bash-write path stays the disclosed wrtscp "
          "residual. A "
          "mis-wired PreToolUse event hard-blocks (exit 2). Historical note: several guards were "
          "originally ASK-based; this suite now asserts the no-ask outcomes above and the global "
          "invariant guarantees none reverts to an ask.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
