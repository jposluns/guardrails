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

It also covers the protected-line guard (protected_line, prtbrn/artbr1): a force-push OR a branch
DELETION of a protected branch (main/master) denies, with the banner naming the actual act, while a
force or delete to a feature branch allows; a refspec-less force-push, a forced or deleted HEAD/@, and
a direct commit (the literal commit subcommand only) are judged by a read-only HEAD probe (fail-to-ASK
when unresolvable); a --mirror/--all, wildcard, matching-':'/'+:', or --prune-with-wildcard sweep asks;
and the parse-error/wrapper fallback fails safe for the force-push, deletion, AND commit spellings.

It also covers the gate-weakening guard (gate_weakening, gatdis): a git verification-hook bypass
(--no-verify on commit/merge/push/pull/rebase/am, exact or abbreviated; the short -n only on
commit/am, where -n IS --no-verify) denies; a checker-shaped segment whose failure is swallowed
(|| true, || :) or piped into a truncating sink (| head, | tail) asks; option-value, post-'--',
and push/merge -n edges stay allowed; and the parse-error fallback fails safe.

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
        # The raw fallback (a tokenizer ValueError) now treats ANY raw 'git' + 'branch' as lossy: it does NOT parse
        # branch flags, so an unparseable 'git branch -d -f topic <heredoc>' / '-df' / '--del --for' can no
        # longer slip past the old '-D'/'--delete'-only raw check into a silent ALLOW; every form ASKS.
        _br_hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes the tokenizer raise -> raw fallback
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
        # quoted "false"), each on an unparseable heredoc discard (the lone quote makes the tokenizer raise), must ASK.
        _hd = " <<'EOF'\n'\nEOF"  # Bash-valid heredoc whose lone ' makes the tokenizer raise -> raw fallback
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

        # (rec-heredoc) C: a Bash-valid but tokenizer-UNPARSEABLE discard (an unbalanced quote inside a quoted
        # heredoc) reaches the raw-lossy fallback. It must ASK and, on a dirty tree, take a best-effort
        # recovery snapshot FIRST - before the fix the ValueError path returned ASK with NO recovery ref.
        rec_hd = _init_repo(tmp / "rec-heredoc")
        (rec_hd / "file.txt").write_text("committed line\nheredoc dirty\n", encoding="utf-8")
        hd_cmd = "git reset --hard <<'EOF'\n'\nEOF"  # Bash-valid heredoc; the lone ' makes the tokenizer raise
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

        def dexpect(label, command, want):
            got = _decision(aiqt_hooks.diff_source_pretool, command)
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
        pexpect("(pl-k1) unparseable apparent force-push naming main asks (fallback recoverable)",
                'git push --force origin main "unbalanced', "ask", cwd=plr)
        pexpect("(pl-k2) unparseable force-push with no readable protected target asks",
                'git push --force "unbalanced', "ask", cwd=plr)
        pexpect("(pl-k3) unparseable apparent commit asks", 'git commit -m "it broke', "ask", cwd=plr)
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
                'git push origin +main "unbalanced', "ask", cwd=plr)
        pexpect("(pl-p2) unparseable '--for' abbreviation force-push asks on the fallback",
                'git push --for origin main "unbalanced', "ask", cwd=plr)
        pexpect("(pl-q1) a wrapped force-push (env) asks via the raw scan, not a silent allow",
                "env git push --force origin main", "ask", cwd=plr)
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
                "git push origin ':refs/heads/*'", "ask", cwd=plr)
        pexpect("(pl-u7) '--delete origin HEAD' on main denies via the probe (a harmless over-deny: "
                "git itself rejects a HEAD delete as a nonexistent ref)",
                "git push --delete origin HEAD", "deny", cwd=plr)
        pexpect("(pl-u8) a refspec-less --delete allows (git itself rejects it, nothing to resolve)",
                "git push --delete origin", "allow", cwd=plr)
        # F-117: info-flag value-awareness and the widened fallback force/delete short clusters.
        pexpect("(f117-a) commit -m --help on protected HEAD asks (--help is the -m value, not help)",
                "git commit -m --help", "ask", cwd=plr)
        pexpect("(f117-b) push -o --help --force to main denies (--help is the -o value, --force is real)",
                "git push -o --help --force origin main", "deny", cwd=plr)
        pexpect("(f117-c) genuine push --help still allows", "git push --help", "allow", cwd=plr)
        pexpect("(f117-d) genuine commit --help still allows", "git commit --help", "allow", cwd=plr)
        pexpect("(f117-e) commit --amend --help over-asks (documented safe-direction residual)",
                "git commit --amend --help", "ask", cwd=plr)
        pexpect("(f117-f) unparseable push --prune asks (fallback prune spelling, F-117)",
                'git push --prune origin main "unbalanced', "ask", cwd=plr)
        pexpect("(f117-g) unparseable push -4d digit cluster asks (fallback, F-117)",
                'git push -4d origin main "unbalanced', "ask", cwd=plr)
        pexpect("(f117-h) unparseable push -4f digit cluster force asks (fallback, F-117)",
                'git push -4f origin main "unbalanced', "ask", cwd=plr)
        # F-117 round-6: --help/-h that git does not treat as help (after `--`, or as a redirect
        # target) must not mask a protected-branch action; quoted short clusters under a wrapper.
        pexpect("(f117r6-a) redirect-target --help does not mask a force-push",
                "git push --force origin main > --help", "deny", cwd=plr)
        pexpect("(f117r6-b) end-of-options operand --help does not mask a force-push",
                "git push --force origin -- main --help", "deny", cwd=plr)
        pexpect("(f117r6-c) end-of-options operand --help does not mask a direct commit",
                "git commit -- README.md --help", "ask", cwd=plr)
        pexpect("(f117r6-d) quoted short-cluster force under a wrapper asks (fallback)",
                "env git push '-4f' origin main", "ask", cwd=plr)
        pexpect("(f117r6-e) quoted short-cluster delete under a wrapper asks (fallback)",
                "env git push '-4d' origin main", "ask", cwd=plr)
        pexpect("(f117r6-f) attached-value option before --help over-asks (disclosed residual)",
                "git commit -mfoo --help", "ask", cwd=plr)
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
                "git --attr-source HEAD commit -m x", "ask", cwd=plr)
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
                "git log -wp", "ask")
        dexpect("(f117r7-e) pickaxe -Sfoo git log is now an ASK (L11: producer-capable, unproven listing)",
                "git log -Sfoo", "ask")
        dexpect("(f117r7-f) wrapped git diff is now an ASK (L11: a wrapper fits no proof)",
                "env git diff", "ask")
        dexpect("(f117r7-g) wrapper over a non-producer allows (no producer surface)", "env git status", "allow")
        dexpect("(f117r7-i) git diff -M --stat now ASKS (L11: an extra option fails the exact summary proof)",
                "git diff -M --stat", "ask")
        dexpect("(f117r7-x2) -S --stat pickaxe-value now ASKS (L11: an extra option fails proof B)",
                "git diff -S --stat", "ask")
        dexpect("(f117r7-j) genuine git log -p still denies (confirmed console patch)", "git log -p", "deny")

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
        # Unsupported constructs are cannot-evaluate (ValueError), never partial argv.
        for _bad, _why in [("git diff <<'EOF'\nx\nEOF", "heredoc"),
                           ("git diff <(echo x)", "process substitution"),
                           ("cat <<<word", "here-string"),
                           ('git diff "unbalanced', "unbalanced quote")]:
            try:
                aiqt_hooks._lex_command(_bad)
                failures.append("(tok-cannot) {} must raise ValueError, did not".format(_why))
            except ValueError:
                pass

        # === L11: additional diff-source vectors (the AIRTIGHT-NARROW contract) =======================
        dexpect("(l11-d1) bare git diff denies", "git diff", "deny")
        dexpect("(l11-d2) git show HEAD denies", "git show HEAD", "deny")
        dexpect("(l11-d3) git range-diff A B denies", "git range-diff A B", "deny")
        dexpect("(l11-d4) sudo git diff asks (wrapper)", "sudo git diff", "ask")
        dexpect("(l11-d5) command /usr/bin/git show asks (wrapper + path)",
                "command /usr/bin/git show", "ask")
        dexpect("(l11-d6) quote-fragmented g'it' d'iff' denies (cleaned argv resolves directly)",
                "g'it' d'iff'", "deny")
        dexpect("(l11-d7) 'git status && env git diff' asks (compound + wrapper)",
                "git status && env git diff", "ask")
        dexpect("(l11-d8) reverse-order 'env git diff && git status' asks",
                "env git diff && git status", "ask")
        dexpect("(l11-d9) echo 'git diff' asks (disclosed broad-scope over-match)",
                "echo 'git diff'", "ask")
        dexpect("(l11-d10) env git status allows (no producer surface)", "env git status", "allow")
        # Exact summary selectors allow; extra summary modifiers ASK.
        dexpect("(l11-s1) git diff --stat allows", "git diff --stat", "allow")
        dexpect("(l11-s2) git show --name-only HEAD allows", "git show --name-only HEAD", "allow")
        dexpect("(l11-s3) git diff --numstat allows", "git diff --numstat", "allow")
        dexpect("(l11-s4) git diff --stat --no-patch allows (--no-patch is the sole extra option)",
                "git diff --stat --no-patch", "allow")
        dexpect("(l11-s5) -M --stat asks", "git diff -M --stat", "ask")
        dexpect("(l11-s6) -U3 --stat asks", "git diff -U3 --stat", "ask")
        dexpect("(l11-s7) --cc --stat asks", "git show --cc --stat", "ask")
        dexpect("(l11-s8) --stat=80 asks (not an exact selector)", "git diff --stat=80", "ask")
        dexpect("(l11-s9) --stat -p denies (patch flag)", "git diff --stat -p", "deny")
        dexpect("(l11-s10) -- --stat denies (pathspec, not a summary)", "git diff -- --stat", "deny")
        dexpect("(l11-s11) git stash show --stat allows", "git stash show --stat", "allow")
        # Exact help allows; help as an option value/redirect target does not earn help ALLOW.
        dexpect("(l11-h1) git diff --help allows", "git diff --help", "allow")
        dexpect("(l11-h2) git range-diff -h allows", "git range-diff -h", "allow")
        dexpect("(l11-h3) git diff --stat --help asks (extra option, not the exact help form)",
                "git diff --stat --help", "ask")
        # Real-file / fd / last-wins diversions.
        dexpect("(l11-c1) leading '>out.patch git diff' allows", ">out.patch git diff", "allow")
        dexpect("(l11-c2) interspersed 'git >out.patch diff' allows", "git >out.patch diff", "allow")
        dexpect("(l11-c3) quoted-target 'git diff 1>>\"review out.patch\"' allows",
                'git diff 1>>"review out.patch"', "allow")
        dexpect("(l11-c4) 'git diff &>out.patch' allows", "git diff &>out.patch", "allow")
        dexpect("(l11-c5) 'git diff >out 2>&1' allows", "git diff >out 2>&1", "allow")
        dexpect("(l11-c6) 'git diff >out 1>&2' asks (stdout descriptor-bound, unprovable)",
                "git diff >out 1>&2", "ask")
        dexpect("(l11-c7) 'git diff >/dev/tty >out' allows (last-wins real file)",
                "git diff >/dev/tty >out", "allow")
        dexpect("(l11-c8) 'git diff >out >/dev/tty' denies (last-wins console)",
                "git diff >out >/dev/tty", "deny")
        dexpect("(l11-c9) 'git diff 2 >out' allows (2 is argv, stdout diverted)",
                "git diff 2 >out", "allow")
        dexpect("(l11-c10) 'git diff 2>out' denies (only stderr diverted)", "git diff 2>out", "deny")
        dexpect("(l11-c11) dynamic target 'git diff > $OUT' asks", "git diff > $OUT", "ask")
        dexpect("(l11-c12) tilde target 'git diff > ~/out.patch' asks", "git diff > ~/out.patch", "ask")
        # A RAW /dev,/proc-prefixed target is a device and DENIES even if a '..' would normalize elsewhere:
        # conservative raw-prefix classification over-blocks in the SAFE direction (round-3 codex note).
        dexpect("(l11-c13) raw '/dev/..' target 'git diff > /dev/../tmp/out.txt' denies (over-block, safe)",
                "git diff > /dev/../tmp/out.txt", "deny")
        # Exact terminal pager allows; wrapped/optioned/downstream pager variants ASK.
        dexpect("(l11-p1) git diff | less allows", "git diff | less", "allow")
        dexpect("(l11-p2) git diff | less -R asks", "git diff | less -R", "ask")
        dexpect("(l11-p3) git diff | env less asks", "git diff | env less", "ask")
        dexpect("(l11-p4) git diff | less | cat asks (later pipe)", "git diff | less | cat", "ask")
        dexpect("(l11-p5) git diff |& less asks", "git diff |& less", "ask")
        dexpect("(l11-p6) env git diff | less asks (wrapped stage 1)", "env git diff | less", "ask")
        # A pipe to a known console/truncating sink is a confirmed dump -> DENY.
        dexpect("(l11-p7) git diff | cat denies", "git diff | cat", "deny")
        dexpect("(l11-p8) git diff | tee out.log denies", "git diff | tee out.log", "deny")
        dexpect("(l11-p9) git diff | head denies", "git diff | head", "deny")
        dexpect("(l11-p10) git diff | tail -20 denies", "git diff | tail -20", "deny")
        # Unparseable apparent producer ASKS (never a regex-earned allow); a non-producer allows.
        dexpect("(l11-f1) unparseable apparent producer asks", 'git diff "unbalanced', "ask")
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
                "git log -5", "ask")
        dexpect("(l11-e11b) git log -n 5 asks (a value-taking count option)", "git log -n 5", "ask")
        dexpect("(l11-e11c) git log --author=x asks (a value-taking filter)", "git log --author=x", "ask")
        dexpect("(l11-e11d) git log --since=yesterday asks (value-taking)",
                "git log --since=yesterday", "ask")
        dexpect("(l11-e11e) git log --grep=fix asks (value-taking)", "git log --grep=fix", "ask")
        dexpect("(l11-e12) git log --format=%H asks (a value-taking option)",
                "git log --format=%H", "ask")
        dexpect("(l11-e12b) git log --graph --format=%H asks (a benign flag plus a value-taking one)",
                "git log --graph --format=%H", "ask")
        # The provably-hard cases: a pickaxe -G/-S WITHOUT -p shows no patch, but its diff behaviour depends
        # on a co-present -p this guard does not model, so it is NOT proven benign -> ASK (never ALLOW).
        dexpect("(l11-e13) git log -G foo asks (pickaxe, diff behaviour depends on -p, not proven benign)",
                "git log -G foo", "ask")
        dexpect("(l11-e14) git log -S foo asks (pickaxe, not proven benign)", "git log -S foo", "ask")
        dexpect("(l11-e15) git log -p still denies (confirmed console patch, unchanged)",
                "git log -p", "deny")
        dexpect("(l11-e16) git log -p --oneline denies (a patch flag co-present with a benign one)",
                "git log -p --oneline", "deny")
        dexpect("(l11-e16b) git log --graph -p denies (a patch flag overrides the benign traversal flag)",
                "git log --graph -p", "deny")
        dexpect("(l11-e17) a wrapped git log asks (proof E requires the literal command word git)",
                "env git log", "ask")
        dexpect("(l11-e18) git log in a compound denies when a later segment is a confirmed dump",
                "git log && git diff", "deny")
        dexpect("(l11-e19) git log --oneline HEAD~5 asks (the '~' is outside the conservative charset)",
                "git log --oneline HEAD~5", "ask")
        dexpect("(l11-e20) diff plumbing stays ASK, not benign (only git log gets proof E)",
                "git diff-tree", "ask")

        # === L11 QA fix round (tri-family blockers on PR #163). Each vector FAILS without its fix. =========
        # BLOCKER 2: an unquoted '#' at a word boundary is a comment; a redirect/pipe that is commented out
        # must NOT earn proof C/D (the diff goes to the CONSOLE). Mid-word '#' stays literal (gw-ba/bb).
        dexpect("(qa-b2a) '#'-commented redirect does not earn proof C -> console dump denies",
                "git diff HEAD^ HEAD # > /tmp/x", "deny")
        dexpect("(qa-b2b) '#'-commented pipe does not earn proof D -> console dump denies",
                "git diff # | less", "deny")
        dexpect("(qa-b2c) a mid-word '#' is still literal, not a comment (regression lock)",
                "git diff --output=out#1.txt", "ask")
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
        # BLOCKER 3: ANSI-C / $'...' quoting that resolves to a git command word is UNPROVEN -> never ALLOW.
        dexpect("(qa-b3a) $'g'it diff (bash runs git diff) never ALLOWs (opaque command word -> ASK)",
                "$'g'it diff HEAD^ HEAD", "ask")
        dexpect("(qa-b3b) a $VAR command word beside a producer surface never ALLOWs",
                "$GIT diff HEAD", "ask")
        # BLOCKER 4: a --output/-o diversion means the shell redirect/pipe is a decoy; proofs C/D disabled.
        dexpect("(qa-b4a) --output=/dev/tty with a decoy real-file redirect denies (console dump)",
                "git diff --output=/dev/tty > realfile.txt", "deny")
        dexpect("(qa-b4b) git log -p --output=/dev/tty with a decoy redirect denies",
                "git log -p --output=/dev/tty > f.txt", "deny")
        dexpect("(qa-b4c) --output=/dev/tty piped to less denies (pager decoy)",
                "git diff --output=/dev/tty | less", "deny")
        dexpect("(qa-b4d) bare --output=/dev/tty denies (console)", "git diff --output=/dev/tty", "deny")
        dexpect("(qa-b4e) --output=realfile.txt asks (diverted to a file, not a proof-C shell redirect)",
                "git diff --output=realfile.txt", "ask")
        dexpect("(qa-b4f) separated --output realfile.txt asks", "git diff --output realfile.txt", "ask")
        # BLOCKER 5: a redirect target that resolves to a device via '..' or a relative path must NOT earn
        # proof C; a genuine plain-file redirect still ALLOWs.
        dexpect("(qa-b5a) '> /tmp/../dev/stdout' normalizes to a device -> denies",
                "git diff > /tmp/../dev/stdout", "deny")
        dexpect("(qa-b5b) '> ../../../dev/stdout' has an unprovable '..' escape -> asks",
                "git diff > ../../../dev/stdout", "ask")
        dexpect("(qa-b5c) a '..'-bearing non-device target is unprovable -> asks",
                "git diff > ../out.txt", "ask")
        dexpect("(qa-b5d) a genuine plain-file redirect still allows (no over-DENY regression)",
                "git diff > out.txt", "allow")
        dexpect("(qa-b5e) a relative sub-path plain file still allows", "git diff > sub/out.txt", "allow")
        # BLOCKER 5 (round 4): a RELATIVE dev/proc-leading redirect target is cwd-dependent (from cwd '/' or
        # via a dev/proc symlink it IS the device the absolute form names), so it must NOT earn proof C's
        # file-real ALLOW; it ASKS. The absolute form still DENIES; a nested 'dev' stays a plain-file ALLOW.
        dexpect("(qa-b5f) '> dev/stdout' is cwd-dependent (could be the device) -> asks, not a silent allow",
                "git diff > dev/stdout", "ask")
        dexpect("(qa-b5g) '> ./dev/stdout' normalizes to a dev-leading target -> asks",
                "git diff > ./dev/stdout", "ask")
        dexpect("(qa-b5h) '> proc/self/fd/1' is a relative proc-leading target -> asks",
                "git diff > proc/self/fd/1", "ask")
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
                "sudo git push origin '+main:main'", "ask", cwd=plr)
        pexpect("(pl-x2) a wrapped --delete of main asks via the fallback",
                "env git push --delete origin main", "ask", cwd=plr)
        pexpect("(pl-x3) an unparseable ':main' delete asks via the fallback",
                'git push origin :main "unbalanced', "ask", cwd=plr)
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
                "git push origin :", "ask", cwd=plr)
        pexpect("(pl-y2) the forced matching refspec '+:' asks",
                "git push origin +:", "ask", cwd=plr)
        pexpect("(pl-y3) --prune with a wildcard refspec asks (deletes absent remote branches, "
                "no force flag)",
                "git push --prune origin 'refs/heads/*:refs/heads/*'", "ask", cwd=plr)
        pexpect("(pl-y4) a wrapped clustered '-dv' delete asks via the widened fallback",
                "env git push -dv origin main", "ask", cwd=plr)
        pexpect("(pl-y5) a wrapped git commit asks via the fallback",
                "sudo git commit -m 'fix'", "ask", cwd=plr)
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

        # === L11 cross-hook redirect vectors (protected_line): the useful round-10 tests RESTORED via the
        # shared raw-aware tokenizer, plus the two regressions that caused the naive-strip revert ==========
        # A redirect anywhere no longer hides the subcommand, operand, or force flag.
        pexpect("(pl-l11a) redirect before 'push' does not hide the protected force-push, denies",
                "git 2>/dev/null push --force origin main", "deny", cwd=plr)
        pexpect("(pl-l11b) leading redirect before 'push' does not hide the force-push, denies",
                ">/dev/null git push --force origin main", "deny", cwd=plr)
        pexpect("(pl-l11c) redirect before 'commit' does not hide the direct protected commit, asks",
                "git >/dev/null commit -m fix", "ask", cwd=plr)
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

        # The ASK heuristics: a checker-shaped segment swallowed or truncated.
        gexpect("(gw-o) pytest || true asks", "pytest || true", "ask")
        gexpect("(gw-p) pytest || : asks", "pytest -q || :", "ask")
        gexpect("(gw-q) make test || true asks (runner + operand)", "make test || true", "ask")
        gexpect("(gw-r) checker-named script || true asks (name parts)",
                "tools/run_all_checks.sh || true", "ask")
        gexpect("(gw-s) pytest | head asks (truncating sink)", "pytest | head", "ask")
        gexpect("(gw-t) pytest | tail -20 asks", "pytest -q | tail -20", "ask")
        gexpect("(gw-u) python -m pytest || true asks (runner -m module)",
                "python -m pytest || true", "ask")
        gexpect("(gw-v) npm test || true asks", "npm test || true", "ask")

        # The heuristic stays scoped: non-checkers and non-truncating pipes are untouched.
        gexpect("(gw-w) checker with no swallow allows", "pytest && echo done", "allow")
        gexpect("(gw-x) non-checker || true allows (disclosed scoping choice)",
                "grep -q foo file.txt || true", "allow")
        gexpect("(gw-y) checker | tee is not truncating, allows", "pytest | tee out.log", "allow")
        gexpect("(gw-z) echo is not checker-shaped", "echo test || true", "allow")

        # Precedence and disjointness (mirrors pl-l1/pl-m1).
        gexpect("(gw-aa) a later hook-bypass deny wins over an earlier swallow ask",
                "pytest || true; git commit -n -m 'x'", "deny")
        gexpect("(gw-ab) a git_discard verb is out of gate_weakening scope (disjoint controls)",
                "git reset --hard", "allow")

        # Parse-error posture (unbalanced quote): fail-safe, never a silent allow.
        gexpect("(gw-ac) unparseable apparent --no-verify denies",
                'git commit --no-verify -m "unbalanced', "deny")
        gexpect("(gw-ad) unparseable commit -n asks (the raw scan cannot bind the cluster)",
                'git commit -n -m "unbalanced', "ask")
        gexpect("(gw-ae) unparseable checker + swallow asks", 'pytest || true "unbalanced', "ask")
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
        gexpect("(gw-ap) ruff check . || true asks", "ruff check . || true", "ask")
        gexpect("(gw-aq) npm test | tail asks (truncating sink)", "npm test | tail", "ask")
        # DISCLOSED safe-direction over-deny (residue): a separated option VALUE that itself spells
        # --no-verify is scanned in the option region and DENIES; git would read it as the --message
        # value (no bypass), so this is a deliberate deny-direction over-match, never a silent allow.
        gexpect("(gw-ar) '--message --no-verify' over-denies (disclosed value-spelling residual)",
                "git commit --message --no-verify", "deny")

        # gatdis round-2 (F-123): the newline-continuation ASK-miss (F3) and the --resolvemsg
        # value-option over-block (F4) are fixed; the adjacency/regression locks hold; the disclosed
        # redirect-pollution slips (F1/F2, routed to the common enforcement-hook redesign) are pinned.
        gexpect("(gw-as) newline before the swallow now asks (F3)", "pytest ||\n true", "ask")
        gexpect("(gw-at) newline before the truncating sink now asks (F3)", "pytest |\n head", "ask")
        gexpect("(gw-au) am --resolvemsg -n consumes -n as the message value, allows (F4)",
                "git am --resolvemsg -n patch.mbox", "allow")
        gexpect("(gw-av) pytest || true still asks (adjacency lock)", "pytest || true", "ask")
        gexpect("(gw-aw) pytest ; true allows (';' is not a swallow)", "pytest ; true", "allow")
        gexpect("(gw-ax) am -n still denies (bare short -n is the bypass)", "git am -n", "deny")
        # L11 shared raw-aware tokenizer: a redirect anywhere in the command is removed from argv, so the
        # former F1/F2 redirect-pollution slips are CLOSED. The bypass/checker is no longer hidden.
        gexpect("(gw-ay) redirect before the git subcommand no longer slips, denies (L11 tokenizer)",
                "git >/dev/null commit --no-verify -m x", "deny")
        gexpect("(gw-az) redirect before the checker word no longer slips, asks (L11 tokenizer)",
                ">/dev/null pytest || true", "ask")

        # gatdis round-3 (F-123 disclosed residuals): the shared L11 tokenizer (_lex_command) treats a '#'
        # as an ordinary word character (it does no comment-stripping), so an embedded unquoted '#' is lexed
        # literally and the bypass/swallow after it is no longer hidden - the class-wide embedded-# residual
        # stays CLOSED; the two contrived safe-direction over-blocks still DENY.
        gexpect("(gw-ba) embedded-# no longer hides the trailing --no-verify, denies (round-32 lexer fix)",
                "git commit -m ticket#123 --no-verify", "deny")
        gexpect("(gw-bb) embedded-# no longer hides the '|| true' swallow, asks (round-32 lexer fix)",
                "pytest foo#bar || true", "ask")
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
        gexpect("(gw-l11c) leading redirect before the checker no longer hides the swallow, asks",
                ">/dev/null pytest || true", "ask")
        gexpect("(gw-l11d) interspersed redirect does not hide the truncating sink, asks",
                "pytest 2>/dev/null | head", "ask")
        # A QUOTED redirect-shaped option value stays argv and does not mask the bypass verb.
        gexpect("(gw-l11e) a quoted '>' -m value does not hide the trailing --no-verify, denies",
                "git commit -m '>' --no-verify", "deny")
        # QA BLOCKER 1: a backslash-newline continuation must not inject an empty argv element that hid the
        # bypass subcommand from the gate-weakening scan -> DENY.
        gexpect("(qa-b1-gw) backslash-newline no-verify does not slip via an empty argv element, denies",
                "git \\\n commit --no-verify -m x", "deny")

        # === L11: git_discard redirect regression locks (prsunc) - a redirect is still non-pristine ->
        # ASK; no redirect may become a new ALLOW, and the clean-parse lossy scan sees redirect-free argv ==
        gd_repo = _init_repo(tmp / "gd-redir")
        (gd_repo / "dirty.txt").write_text("x\n")  # untracked -> a probed-dirty tree
        gdr = str(gd_repo)
        expect("(gd-l11a) redirected 'reset --hard' is non-pristine -> ASK (no new allow)",
               "git reset --hard >/dev/null", "ask", cwd=gdr)
        expect("(gd-l11b) redirected 'checkout' is non-pristine -> ASK",
               "git checkout -- dirty.txt 2>/dev/null", "ask", cwd=gdr)
        expect("(gd-l11c) redirected 'clean -f' is non-pristine -> ASK",
               "git clean -f >/dev/null", "ask", cwd=gdr)

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
                return stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision", "unexpected")
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
        gexpect("(gs-a) Write a registered file target ASKS", "ask",
                tool="Write", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-b) Edit a member of a registered tree ASKS", "ask",
                tool="Edit", file_path=os.path.join(gr, "gen", "part.md"), cwd=gr)
        gexpect("(gs-c) MultiEdit a registered file target ASKS (MultiEdit in scope)", "ask",
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
        # ASK: malformed JSON, unknown version, malformed entry (kind=dir) all fail SAFE to ask.
        gs_reg3.write_text("{ not json", encoding="utf-8")
        gexpect("(gs-i1) malformed-JSON registry ASKS", "ask",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        gs_reg3.write_text(json.dumps({"version": 2, "generated": []}), encoding="utf-8")
        gexpect("(gs-i2) unknown-version registry ASKS", "ask",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "dir", "target": "x", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-i3) malformed-entry (unknown kind) registry ASKS", "ask",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # ASK: an unresolved repo root (a plain non-git cwd).
        gexpect("(gs-j) a non-git cwd (unresolved root) ASKS", "ask",
                tool="Write", file_path=os.path.join(gng, "GEN.md"), cwd=gng)
        # DENY: the only deny, the shared fail-closed contract (no tool_name).
        gexpect("(gs-k) a missing tool_name DENIES (fail-closed contract)", "deny",
                file_path=os.path.join(gr, "GEN.md"), cwd=gr, with_tool=False)
        # ASK: no session cwd, so the root cannot be resolved.
        gexpect("(gs-l) a missing cwd ASKS (root cannot be resolved)", "ask",
                tool="Write", file_path=os.path.join(gr, "GEN.md"), with_cwd=False)
        # ASK: a target outside the repo cannot be cleared against this repo registry.
        gexpect("(gs-m) a target outside the repo ASKS (non-contained)", "ask",
                tool="Write", file_path=str(tmp / "outside.md"), cwd=gr)
        # ALLOW: Bash is out of scope by design (defensive branch; the matcher excludes it too).
        gexpect("(gs-n) Bash is out of scope (allow)", "allow",
                tool="Bash", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        # ASK: payload fail-safes (non-dict tool_input, missing file_path).
        gexpect("(gs-o) a non-dict tool_input ASKS", "ask",
                tool="Write", cwd=gr, tool_input="not-a-dict")
        gexpect("(gs-p) a missing file_path ASKS", "ask",
                tool="Write", cwd=gr)
        # ASK: a non-regular-file registry is BAD, never absent (integ-check-fails-closed-on-unreadable).
        # DETERMINISTIC: the registry PATH is a DIRECTORY, so the lstat/S_ISREG probe rejects it as
        # non-regular BEFORE the open (a directory's st_mode is not S_ISREG -> bad) on every runner, root
        # included. No os.access/chmod skip (F-166).
        gexpect("(gs-q) a non-regular (directory-at-path) registry ASKS (not a regular file, never absent)",
                "ask",
                tool="Write", file_path=os.path.join(grdir, "GEN.md"), cwd=grdir)
        # ASK: a MultiEdit relative file_path is joined onto cwd, then matched.
        gexpect("(gs-r) a MultiEdit relative file_path is cwd-joined then matched (ASKS)", "ask",
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
        gexpect("(gs-t1) an empty-string tool_name ASKS (unreadable, not a miss)", "ask",
                tool="", file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-t2) a list tool_name ASKS (unreadable, not a miss)", "ask",
                tool=[], file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        gexpect("(gs-t3) a bool tool_name ASKS (unreadable, not a miss)", "ask",
                tool=True, file_path=os.path.join(gr, "GEN.md"), cwd=gr)
        # ASK: version:true is a JSON bool, not int 1 (type(True) is bool). Was ALLOW (True == 1). (F-159)
        gs_reg3.write_text(json.dumps({"version": True, "generated": [
            {"kind": "file", "target": "GEN.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-u) a JSON-bool version:true ASKS (type is bool, not int)", "ask",
                tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: version:"1" string is not int 1. (both old and new reject; asserts the type contract holds)
        gs_reg3.write_text(json.dumps({"version": "1", "generated": [
            {"kind": "file", "target": "GEN.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-v) a string version:\"1\" ASKS (not int)", "ask",
                tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a control character (NUL) in a FILE entry target is malformed -> bad. Was ALLOW (target
        # passed the old validation, no match on an unregistered query). (F-160)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "file", "target": "GEN\x00.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-w) a NUL in a file-entry target ASKS (control-char rejected)", "ask",
                tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a NON-NUL control char (0x1f) in a file target. Unlike NUL, realpath does NOT raise on it,
        # so ONLY the control-char rejection (not the realpath-fault wrap) catches it - guards F-160's
        # independent value. Was ALLOW (target passed old validation; no match on an unregistered query).
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "file", "target": "GEN\x1f.md", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-w2) a non-NUL control char in a file-entry target ASKS (realpath would not reject)",
                "ask", tool="Write", file_path=os.path.join(gr3, "README.md"), cwd=gr3)
        # ASK: a NUL in a BLOCK entry target is rejected BEFORE the block-skip. Was a zero-entry ALLOW
        # (the block was dropped, leaving no entries). (F-160)
        gs_reg3.write_text(json.dumps({"version": 1, "generated": [
            {"kind": "block", "target": "X\x00", "sources": ["s"], "regenerate": "r"}]}), encoding="utf-8")
        gexpect("(gs-x) a NUL in a block-entry target ASKS (rejected before the block-skip)", "ask",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # ASK: a NUL in the PAYLOAD file_path is rejected before realpath. Was an uncaught crash
        # (os.path.realpath raises ValueError on an embedded NUL). (F-160 + F-157)
        gexpect("(gs-y) a NUL in the payload file_path ASKS (was a crash-to-deny)", "ask",
                tool="Write", file_path=os.path.join(gr, "GEN\x00.md"), cwd=gr)
        # ASK: a NON-NUL control char (0x1f) in the payload file_path. realpath would NOT raise on it, so
        # only the control-char rejection catches it (guards F-160's independent value). Was a silent ALLOW.
        gexpect("(gs-y2) a non-NUL control char in the payload file_path ASKS (realpath would not reject)",
                "ask", tool="Write", file_path=os.path.join(gr, "GEN\x1f.md"), cwd=gr)
        # ASK: a repo dir name with a TRAILING SPACE: the toplevel is preserved because only git's single
        # trailing-newline terminator is stripped (result.stdout[:-1] when it endswith "\\n", stripping
        # exactly that one \\n), not strip(), so the registry IS found and the registered target ASKS. Was
        # ALLOW (strip() dropped the space -> wrong root -> registry not found -> absent). (F-162)
        gexpect("(gs-z) a trailing-space repo dir keeps its toplevel; the registered target ASKS", "ask",
                tool="Write", file_path=os.path.join(grsp, "GEN.md"), cwd=grsp)
        # ASK: a DANGLING symlink registry is BAD (a symlink is not a trusted regular file). lstat does NOT
        # follow the link, so S_ISREG is False on the link itself -> bad; this rejects a STATIONARY symlink
        # (best-effort against the accidental case, not a TOCTOU-closure claim). Was an inert ALLOW
        # (open -> FileNotFoundError -> absent). (F-164)
        gexpect("(gs-aa) a dangling-symlink registry ASKS (a symlink is never a regular file)", "ask",
                tool="Write", file_path=os.path.join(grlink, "GEN.md"), cwd=grlink)
        # ASK: a multibyte OVERSIZE registry (>1M BYTES but <1M chars) exceeds the BYTE bound. Was ALLOW
        # (a char-count read stayed under the cap and parsed to an empty registry). (F-165)
        gexpect("(gs-ab) a multibyte-oversize registry ASKS (the bound is on BYTES)", "ask",
                tool="Write", file_path=os.path.join(grbig, "GEN.md"), cwd=grbig)

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
                    "ask", tool="Write", file_path=_gs_inj_fp, cwd=gr)
        finally:
            os.path.realpath = _real_realpath
        _real_commonpath = os.path.commonpath
        try:
            os.path.commonpath = _raise_commonpath
            gexpect("(gs-ad) an injected commonpath fault ASKS (_gensrc_within containment 'err' branch)",
                    "ask", tool="Write", file_path=_gs_inj_fp, cwd=gr)
        finally:
            os.path.commonpath = _real_commonpath

        # ASK: a repo dir name ending in a NEWLINE keeps its toplevel. git prints the path + EXACTLY one \n
        # terminator, so stripping only that one \n preserves the dir's own trailing newline; the registry
        # IS found and the registered target (relative MultiEdit route, cwd = the newline-terminal repo)
        # ASKS. Falsifiable: rstrip("\n") eats the dir's own newline too -> wrong root -> registry not
        # found -> inert absent ALLOW. (F-167)
        gexpect("(gs-ae) a newline-terminal repo dir keeps its toplevel; the registered target ASKS", "ask",
                tool="MultiEdit", file_path="GEN.md",
                edits=[{"old_string": "a", "new_string": "b"}], cwd=grnl)

        # ASK: a NON-UTF-8 registry (invalid bytes) is BAD, never absent: the explicit
        # raw_bytes.decode("utf-8") raises UnicodeDecodeError, which is caught -> bad. Falsifiable:
        # removing the decode try/except turns the invalid bytes into an uncaught crash the dispatcher
        # hard-DENIES, not a clean ASK. (F-169 deterministic decode-path proof)
        gs_reg3.write_bytes(b"\xff\xfe\x00\x01not utf-8\xc3\x28")
        gexpect("(gs-af) a non-UTF-8 registry ASKS (invalid bytes -> decode fault -> bad)", "ask",
                tool="Write", file_path=os.path.join(gr3, "GEN.md"), cwd=gr3)
        # ASK: a FIFO registry is BAD (lstat/S_ISREG sees S_ISFIFO before the open), and the probe does NOT
        # block: os.lstat does not open the FIFO, so no writer is needed and there is no hang. Falsifiable:
        # dropping the lstat/S_ISREG probe would make open(path, "rb") block on the FIFO until the hook
        # timeout instead of returning ASK. The fifo is unlinked in the finally below. (F-169)
        _fifo_path = os.path.join(grfifo, ".aiqt", "gensrc.json")
        os.mkfifo(_fifo_path)
        try:
            gexpect("(gs-ag) a FIFO registry ASKS (lstat/S_ISREG sees S_ISFIFO, no open, no hang)", "ask",
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
            gexpect("(gs-ah) a delete-race (regular at lstat, gone at open) ASKS, not absent-ALLOW", "ask",
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
                return stdout_obj.get("hookSpecificOutput", {}).get("permissionDecision", "unexpected")
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
        bcmd("(ap-c2) cd relative asks", "cd sub", "ask")
        bcmd("(ap-c3) cd ../parent relative asks", "cd ../x", "ask")
        bcmd("(ap-c4) cd ./here relative asks", "cd ./x", "ask")
        bcmd("(ap-c5) pushd relative asks", "pushd rel", "ask")
        bcmd("(ap-c6) pushd absolute allows", "pushd {}".format(rp), "allow")
        bcmd("(ap-c7) cd opaque '$DIR' asks (unresolvable)", "cd $DIR", "ask")
        bcmd("(ap-c8) cd rooted-with-expansion '/$X/y' allows (always absolute)", "cd /$X/y", "allow")
        bcmd("(ap-c9) cd bare (HOME) allows", "cd", "allow")
        bcmd("(ap-c10) cd '-' (OLDPWD) allows", "cd -", "allow")
        bcmd("(ap-c11) pushd bare (stack swap) allows", "pushd", "allow")
        bcmd("(ap-c12) pushd '+1' rotation allows (no path)", "pushd +1", "allow")
        bcmd("(ap-c13) cd option then absolute allows (option skipped)", "cd -P {}".format(rp), "allow")
        bcmd("(ap-c14) cd option then relative asks (option skipped, dest judged)", "cd -P rel", "ask")
        bcmd("(ap-c15) pushd '-n' then relative asks", "pushd -n rel", "ask")
        bcmd("(ap-c16) relative redirect target asks", "echo hi > out.txt", "ask")
        bcmd("(ap-c17) absolute redirect target allows", "echo hi > {}/out.txt".format(rp), "allow")
        bcmd("(ap-c18) relative append redirect asks", "echo hi >> log", "ask")
        bcmd("(ap-c19) relative input redirect asks", "sort < data.txt", "ask")
        bcmd("(ap-c20) absolute input redirect allows", "sort < {}/data.txt".format(rp), "allow")
        bcmd("(ap-c21) fd duplication '2>&1' allows (descriptor, no path)", "cat x 2>&1", "allow")
        bcmd("(ap-c22) absolute /dev redirect allows", "cat x 2>/dev/null", "allow")
        bcmd("(ap-c23) opaque redirect target '$F' asks", "echo hi > $F", "ask")
        bcmd("(ap-c24) non-cd relative operand NOT judged (allows)", "cat rel.txt", "allow")
        bcmd("(ap-c25) compound with a relative redirect asks", "cd {} && echo x > out.txt".format(rp),
             "ask")
        bcmd("(ap-c26) compound all-absolute allows", "cd {} && cat {}/f".format(rp, rp), "allow")
        bcmd("(ap-c27) unparseable command with no earlier relative position allows (disclosed residual)",
             'git checkout -- "unbalanced', "allow")
        bcmd("(ap-c28) empty command asks (cannot read)", "", "ask")
        # GS-7 fix: per-SEGMENT partial lex so a resolvable relative cd/redirect in the PARSEABLE PREFIX
        # still ASKS even when a LATER segment is unparseable (pre-fix, one lexer ValueError over the whole
        # command discarded every parsed segment and ALLOWED). Fails-when-reverted: these were "allow".
        bcmd("(ap-c34) relative cd before a heredoc asks (prefix inspected, GS-7 FIX 1)",
             "cd .aiqt; cat <<EOF > /dev/null\nx\nEOF\npwd", "ask")
        bcmd("(ap-c35) relative redirect before a here-string asks (prefix inspected, GS-7 FIX 1)",
             "echo hi > out.txt; cat <<<x", "ask")
        bcmd("(ap-c36) relative cd before a process substitution asks (prefix inspected, GS-7 FIX 1)",
             "cd rel; cat <(printf x)", "ask")
        # boundary: an unparseable construct with NO earlier resolvable position stays a disclosed ALLOW
        # (a cd/redirect WITHIN or AFTER the construct is uninspected, not over-asked on every heredoc).
        bcmd("(ap-c37) heredoc with no earlier relative position allows (disclosed residual, GS-7 FIX 1)",
             "cat <<EOF\nx\nEOF", "allow")
        # GS-7 FIX 2: a real relative destination beginning '-'/'+' is the destination, not an option; '--'
        # ends option processing. Pre-fix ^[-+] skipped every such token -> None -> ALLOW. Fails-when-reverted.
        bcmd("(ap-c38) 'cd -- -relative' asks ('--' ends options, dest judged, GS-7 FIX 2)",
             "cd -- -relative", "ask")
        bcmd("(ap-c39) 'cd +relative' asks (a real relative dir name, GS-7 FIX 2)", "cd +relative", "ask")
        bcmd("(ap-c40) 'cd -relative' asks (a real relative dir name, GS-7 FIX 2)", "cd -relative", "ask")
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
             "cd ~user/x", "ask")
        bcmd("(ap-c45b) 'cd ~user' asks (~user existence unverifiable, GS-7 round-2 MAJOR 4)",
             "cd ~user", "ask")
        bcmd("(ap-c46) 'cd $HOME' asks (opaque expansion, not provably absolute, GS-7 FIX 3)",
             "cd $HOME", "ask")
        bcmd("(ap-c47) 'cd \"$HOME\"' asks (opaque expansion, GS-7 FIX 3)", 'cd "$HOME"', "ask")
        # a QUOTED '~' is a literal relative directory named '~', not tilde expansion: still ASKS (the
        # per-token LEADING-UNQUOTED-tilde flag tells the unquoted, expanded form from the quoted one).
        bcmd("(ap-c48) quoted 'cd \"~/foo\"' asks (quoted tilde is literal-relative, GS-7 FIX 3)",
             'cd "~/foo"', "ask")
        # GS-7 round-2 MAJOR 1: a QUOTED leading tilde with an UNQUOTED OPAQUE tail ('$'/glob/brace) is NOT
        # tilde-expanded by bash (the '~' is quoted, so the word stays RELATIVE), yet pre-round-2 it was a
        # false ALLOW because the token-wide opacity flag was mistaken for an unquoted-tilde signal. The
        # LEADING-UNQUOTED-tilde flag now gates the tilde-ALLOW, so every quoted-leading-tilde form ASKS
        # while the genuine unquoted '~/$VAR' stays ALLOW. Fails-when-reverted (all the ASK cases were ALLOW).
        bcmd("(ap-c49) 'cd \"~\"/x*' asks (quoted tilde + glob tail, not expanded, GS-7 round-2 MAJOR 1)",
             'cd "~"/x*', "ask")
        bcmd("(ap-c50) 'cd \"~\"/x?' asks (quoted tilde + glob tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/x?', "ask")
        bcmd("(ap-c51) 'cd \"~\"/{a}' asks (quoted tilde + brace tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/{a}', "ask")
        bcmd("(ap-c52) 'cd \"~\"/$V' asks (quoted tilde + expansion tail, GS-7 round-2 MAJOR 1)",
             'cd "~"/$V', "ask")
        bcmd("(ap-c53) \"cd '~'/x*\" asks (single-quoted tilde + glob tail, GS-7 round-2 MAJOR 1)",
             "cd '~'/x*", "ask")
        bcmd("(ap-c54) 'cd \"~/repo\"*' asks (quoted tilde path + glob, GS-7 round-2 MAJOR 1)",
             'cd "~/repo"*', "ask")
        bcmd("(ap-c55) 'cd \\\\~/$VAR' asks (escaped tilde + expansion tail, GS-7 round-2 MAJOR 1)",
             'cd \\~/$VAR', "ask")
        bcmd("(ap-c56) 'cd \"\"~/x' asks (empty-quote-preceded tilde is not leading, GS-7 round-2 MAJOR 1)",
             'cd ""~/x', "ask")
        # the genuine unquoted leading tilde with an opaque tail STAYS ALLOW (regression guard for MAJOR 1)
        bcmd("(ap-c57) 'cd ~/$VAR' allows (unquoted leading tilde expands to $HOME, GS-7 round-2 MAJOR 1)",
             "cd ~/$VAR", "allow")
        # GS-7 round-2 MAJOR 2: a relative redirect target fully parsed BEFORE an unparseable construct in
        # the SAME in-progress segment ('cat > out.txt <<EOF...', 'cat > out.txt <(...)') is now inspected
        # (the in-progress segment is recovered on the partial-lex exception path) and ASKS; pre-round-2 the
        # whole in-progress segment was discarded, a false ALLOW. Fails-when-reverted (both were ALLOW).
        bcmd("(ap-c58) redirect target before a heredoc asks (in-progress seg recovered, GS-7 round-2 MAJOR 2)",
             "cat > out.txt <<EOF\nx\nEOF", "ask")
        bcmd("(ap-c59) redirect target before a proc-subst asks (in-progress seg recovered, GS-7 round-2 MAJOR 2)",
             "cat > out.txt <(printf x)", "ask")
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
             "cd -- rel", "ask")
        bcmd("(ap-c64) 'cd -- --' asks (post-'--' relative dir named '--', GS-7 round-2 MAJOR 3 guard)",
             "cd -- --", "ask")
        # GS-7 round-3 MAJOR 1: bash expands a leading '~' ONLY when the WHOLE tilde-prefix (from '~' to the
        # first UNQUOTED '/' or end of word) is unquoted/unescaped. A QUOTE or ESCAPE anywhere in that prefix
        # disables expansion and the word stays RELATIVE - even when the DECODED token is fully literal ('~/x')
        # with no opacity flag. Pre-round-3 argv_leading_tilde was set merely because the FIRST char was an
        # unquoted '~', so all of these were false ALLOWs. The prefix is now tracked per-character in _read_word,
        # so each ASKS. Fails-when-reverted (every case here was ALLOW on the round-2 code).
        bcmd("(ap-c65) 'cd ~\"/x\"' asks (quoted '/' in tilde-prefix, not expanded, GS-7 round-3 MAJOR 1)",
             'cd ~"/x"', "ask")
        bcmd("(ap-c66) \"cd ~''\" asks (empty single-quote in tilde-prefix, GS-7 round-3 MAJOR 1)",
             "cd ~''", "ask")
        bcmd("(ap-c67) 'cd ~\"\"/x' asks (empty double-quote before the '/', GS-7 round-3 MAJOR 1)",
             'cd ~""/x', "ask")
        bcmd("(ap-c68) \"cd ~'/x'\" asks (single-quoted '/x' tail in prefix, GS-7 round-3 MAJOR 1)",
             "cd ~'/x'", "ask")
        bcmd("(ap-c69) 'cd ~\\\\/x' asks (escaped '/' in tilde-prefix, GS-7 round-3 MAJOR 1)",
             "cd ~\\/x", "ask")
        bcmd("(ap-c70) 'cd ~\"/\"$V' asks (quoted '/' then expansion, prefix quoted, GS-7 round-3 MAJOR 1)",
             'cd ~"/"$V', "ask")
        bcmd("(ap-c71) 'cd ~\"/x\" <(printf x)' asks (quoted-prefix tilde in a partial-lex compose, round-3)",
             'cd ~"/x" <(printf x)', "ask")
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
             "OLDPWD=.. cd -- -", "ask")
        bcmd("(ap-c74) 'OLDPWD=.. cd -' asks (inline OLDPWD= overrides $OLDPWD, GS-7 round-3 MAJOR 2)",
             "OLDPWD=.. cd -", "ask")
        bcmd("(ap-c75) 'OLDPWD=.. pushd -- -' asks (inline OLDPWD= overrides $OLDPWD, GS-7 round-3 MAJOR 2)",
             "OLDPWD=.. pushd -- -", "ask")
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
             "OLDPWD+=.. cd -", "ask")
        bcmd("(ap-c78b) 'unset OLDPWD; OLDPWD+=.. cd -' asks (append-assignment in a later segment, round-4)",
             "unset OLDPWD; OLDPWD+=.. cd -", "ask")
        # MAJOR 2 (under-block): a BARE 'cd' with an inline HOME= (or HOME+=) assignment cds to the redirected
        # $HOME, which can be relative, but the no-operand branch treated bare cd as unconditionally
        # cwd-independent. ANY leading assignment now makes the bare-cd (no-operand) $HOME default ASK.
        bcmd("(ap-c79) 'HOME=.. cd' asks (bare cd -> redirected $HOME, GS-7 round-4 MAJOR 2)",
             "HOME=.. cd", "ask")
        bcmd("(ap-c79b) 'HOME+=.. cd' asks (append-assignment bare cd -> $HOME, GS-7 round-4 MAJOR 2)",
             "HOME+=.. cd", "ask")
        bcmd("(ap-c79c) 'HOME=.. pushd' asks (bare pushd default, any leading assignment, round-4 MAJOR 2)",
             "HOME=.. pushd", "ask")
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
        bcmd("(ap-c81) 'printf x > \"~\"/x' asks (quoted tilde is literal-relative, GS-7 round-4 MAJOR 4 guard)",
             'printf x > "~"/x', "ask")
        bcmd("(ap-c81b) 'printf x > ~\"/x\"' asks (quote inside the tilde-prefix, round-4 MAJOR 4 guard)",
             'printf x > ~"/x"', "ask")
        bcmd("(ap-c81c) 'printf x > out.txt' asks (plain relative redirect target unaffected, round-4 guard)",
             "printf x > out.txt", "ask")
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
             '"OLDPWD"=.. cd -', "ask")
        bcmd("(ap-c82b) 'OLD\"PWD\"=.. cd -' asks (disclosed conservative over-fire, GS-7 round-4 MAJOR 3)",
             'OLD"PWD"=.. cd -', "ask")
        bcmd("(ap-c82c) 'OLDP\\WD=.. cd -' asks (escaped name, disclosed conservative over-fire, round-4 MAJOR 3)",
             "OLDP\\WD=.. cd -", "ask")
        # non-regression + the deliberate conservative CONSEQUENCE of the shrink: with NO leading assignment
        # the cwd-independent forms STAY ALLOW; a benign leading assignment ('FOO=x') now makes them ASK (the
        # disclosed cost of not enumerating variable names).
        bcmd("(ap-c83) plain bare 'cd' still allows (no leading assignment, GS-7 round-4 guard)", "cd", "allow")
        bcmd("(ap-c83b) plain 'cd -' still allows (no leading assignment, GS-7 round-4 guard)", "cd -", "allow")
        bcmd("(ap-c83c) plain 'cd -- -' still allows (no leading assignment, GS-7 round-4 guard)",
             "cd -- -", "allow")
        bcmd("(ap-c84) 'FOO=x cd -' asks (any leading assignment -> lone '-' ASKS, round-4 conservative cost)",
             "FOO=x cd -", "ask")
        bcmd("(ap-c84b) 'FOO=x cd' asks (any leading assignment -> bare-cd $HOME ASKS, round-4 conservative cost)",
             "FOO=x cd", "ask")
        # malformed / out-of-scope payloads for the Bash floor
        _bap = aiqt_hooks.bash_absolute_paths
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": 5}}) != "ask":
            failures.append("(ap-c29) non-string command must ASK (cannot read)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": None}) != "ask":
            failures.append("(ap-c30) non-mapping tool_input must ASK (cannot read command)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {}}) != "ask":
            failures.append("(ap-c31) missing command must ASK (cannot read)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": "Read",
                          "tool_input": {"command": "cd rel"}}) != "allow":
            failures.append("(ap-c32) out-of-scope tool must allow (matcher governs)")
        if _reduce(_bap, {"hook_event_name": "PreToolUse", "tool_name": None,
                          "tool_input": {"command": "cd rel"}}) != "deny":
            failures.append("(ap-c33) missing tool_name must DENY (fail-closed contract)")

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
          "PR-A round-4, prtbrn/artbr1) is proven: a force-push (every -f/--force/"
          "--force-with-lease/--force-if-includes spelling, a conservative long prefix, and a "
          "'+'-prefixed refspec) OR a protected DELETION (--delete, a clustered -d, an "
          "empty-source ':<dst>' refspec) whose refspec-position DESTINATION names a protected "
          "branch (main/master, bare or refs/heads/-qualified) DENIES, with a banner naming the "
          "actual act (force-push vs branch deletion), while a force or delete to a feature "
          "branch, a plain non-force push, and a push whose REMOTE merely carries a protected "
          "name ALLOW; flag detection is value-aware ('-o --force' and '--push-option --force' "
          "are option VALUES, not force; the separated --recurse-submodules value is consumed, "
          "so the refspec-less force behind it still probes and denies); a refspec-less "
          "force-push and a forced or deleted HEAD/@ resolve HEAD by a scrubbed read-only probe "
          "(deny on a protected HEAD, fail-to-ASK when unresolvable; the deleted-HEAD deny is a "
          "harmless safe-direction over-deny, git itself rejecting a HEAD delete as a "
          "nonexistent ref); a --mirror or forced --all/--branches sweep, a wildcard force or "
          "delete destination, the matching refspec ':' and its forced '+:' form, and --prune "
          "with a wildcard or matching refspec (which deletes absent remote branches with NO "
          "force flag) ASK; a direct git commit while HEAD is protected (or unprovable) ASKS - "
          "only the literal commit subcommand, merge/cherry-pick/revert being out of the "
          "accidental-case scope by design; the fallback (a shell parse error OR a command-word "
          "wrapper hiding git) ASKS an apparent git FORCE-PUSH (incl a +refspec even "
          "quote-anchored, --for, --mirror/--all), an apparent branch DELETION (--de..., a 'd' "
          "anywhere in a short cluster so a wrapped -dv is caught, ':<protected>'), AND an "
          "apparent git COMMIT - each witnessed under a wrapper as well as under a parse error - "
          "never a hard deny and never a silent allow; and the disclosed residuals are witnessed "
          "AS disclosed (ANY benign parsed git segment, earlier OR later, suppresses the "
          "wrapped-catch and the wrapped force-push ALLOWS, best-effort and not chased; a "
          "shell-EXPANDED destination ($BRANCH) is judged as the literal token and ALLOWS, the "
          "inherent lexical boundary; and the safe-direction over-DENIES hold: --force "
          "--no-force, --delete --no-delete, --force-if-includes alone, and --dry-run with a "
          "force or delete spelling all DENY)"
          ". The gate-weakening guard (EN-5 PR-B, gatdis) is proven: --no-verify denies on every verb "
          "that accepts it (commit/merge/push/pull/rebase/am; exact and conservative-prefix), the short "
          "-n denies only where it IS --no-verify (commit, am; clustered too, with -c values and "
          "leading env assignments skipped), a push -n dry-run and a merge -n no-stat stay allowed, "
          "option values (attached -m, separated --message, post-'--' pathspecs) are never read as "
          "flags, a checker-shaped segment swallowed by '|| true'/'|| :' or piped into head/tail ASKS "
          "while non-checkers and non-truncating pipes stay allowed, a deny wins over a pending ask, "
          "and the parse-error fallback denies/asks the raw spellings, never a silent allow"
          ". The secrets-shift-left guard (EN-5 PR-C, secsec) is proven: a Write content, an Edit "
          "new_string, or a Bash command carrying a synthetic-but-shaped provider token (GitHub, "
          "Anthropic, AWS access key id), a private key block header, or a credential-named assignment of "
          "a real-length literal DENIES, naming only the pattern label and never the value; a placeholder "
          "value (single-sourced PLACEHOLDER exclusion), ordinary code, an out-of-scope Read, and a Bash "
          "command with no secret ALLOW; and a missing tool_name or an absent target field fails closed"
          ". The generated-artefact edit guard (EN-5 PR-D, gensrc) is proven: a Write/Edit/MultiEdit "
          "whose file_path resolves onto a kind=file or kind=tree entry of the per-repo .aiqt/gensrc.json "
          "(read at decision time) ASKS the explicit steering ask (never leaning on the never-rendered "
          "manifest default), while a source edit, an unregistered path, and a kind=block target ALLOW, "
          "component-boundary matching keeps gen-extra/ and GEN.md.bak from matching gen/ and GEN.md, and "
          "Bash is out of scope; each fault branch is designed to fail SAFE to ASK (a malformed, unknown-version, "
          "malformed-entry, non-regular-file, byte-oversize, non-UTF-8, delete-raced, or unreadable "
          "registry - the non-regular-file rejection (an lstat/S_ISREG probe before the open that reads a "
          "STATIONARY non-regular registry as bad and does not block on a FIFO) is proven deterministically "
          "via a dangling symlink, a directory-at-path, and a FIFO (no permission-skip); the non-UTF-8 "
          "decode path via invalid bytes; and the delete race (regular at lstat, gone at open -> bad, not "
          "the absent ALLOW) via an injected os.lstat - a registry concurrently SWAPPED to another type in "
          "the lstat-to-open window stays a DISCLOSED best-effort residual, not a proven-closed case; a "
          "control character "
          "in an entry target or in the payload file_path; a JSON-bool or string version; an unresolvable "
          "non-git root, and - proven via an INJECTED os.path fault, both being POSIX-unreachable "
          "defence-in-depth - an unresolvable target (the guarded-realpath branch) and a containment fault "
          "(the _gensrc_within 'err' branch); a proven-outside (non-contained) target; a non-dict "
          "tool_input; a "
          "missing file_path or cwd; an empty/list/bool tool_name; and a cwd-joined relative MultiEdit "
          "path), a repo dir name with a trailing SPACE or a trailing NEWLINE keeps its toplevel (only "
          "git's single trailing-newline terminator is stripped, not every trailing newline) so the "
          "registered target still ASKS, an absent registry is the inert ALLOW, a mis-wired event "
          "HARD-BLOCKS (exit 2), and only a MISSING tool_name DENIES. The absolute-paths guard "
          "(abspth, GS-7) is proven across both linkages: the native-path predicate DENIES a "
          "drive-relative 'C:file', a bare 'C:', a leading-backslash path, a tilde, an empty, and a "
          "plain relative file_path while ALLOWING a POSIX-absolute, a drive-absolute, and a UNC path; "
          "the widened typed-path matcher judges MultiEdit (legacy-compat wire) and NotebookEdit "
          "(notebook_path) as required-absolute and Grep alongside Glob as an optional search root with "
          "the rootless carve-out re-affirmed, each field name fixed against the tool schema; a tool_input "
          "that is not a mapping fails closed for an in-scope tool (the old search-root fail-open is "
          "closed) while an out-of-scope tool allows; and the conservative Bash floor ASKS a relative or "
          "opaque cd/pushd destination and a relative or opaque redirection target, ALLOWS an absolute "
          "one, an UNQUOTED CURRENT-USER tilde ('~', '~/x') as cwd-independent (gated on a LEADING-unquoted-"
          "tilde flag, so a QUOTED leading tilde even with an unquoted opaque tail - \"~\"/x*, '~'/x?, "
          "\"~\"/$V - ASKS, and a '~user' whose account existence is unverifiable ASKS; a CLEAN-tilde "
          "REDIRECT target now ALLOWS consistently ('> ~/x', '> ~/\"x\"', '> ~/$V'), while a quoted or "
          "complex-prefix tilde redirect target ('> \"~\"/x', '> ~\"/x\"') keeps ASKing, GS-7 round-4 "
          "MAJOR 4), while an opaque '$VAR' still ASKS, a bare cd, the OLDPWD 'cd -' INCLUDING the post-'--' "
          "'cd -- -', the real cd/pushd option flags, a pushd rotation, and a descriptor duplication ALLOW "
          "ONLY absent a leading inline env-assignment; the GS-7 round-4 CONSERVATIVE CONSOLIDATION shrinks "
          "that ALLOW set so ANY leading env-assignment (any name, '=' or '+=', even an assignment-SHAPED "
          "token bash would reject) makes the cwd-independent 'cd -'/'cd -- -'/bare 'cd' ASK - 'OLDPWD+=.. "
          "cd -', 'HOME=.. cd', 'HOME+=.. cd', '\"OLDPWD\"=.. cd -' (the last a DISCLOSED over-fire), and the "
          "benign 'FOO=x cd -'/'FOO=x cd' as its deliberate cost - without enumerating variable names; "
          "treats a relative "
          "dir name beginning '-'/'+' as the destination ('cd -- -rel', 'cd +rel') and ASKS while a "
          "post-'--' dir literally named '--' ('cd -- --') stays ASK, does NOT judge an arbitrary command "
          "operand, inspects the parseable PREFIX before an unparseable construct INCLUDING the in-progress "
          "segment's redirect targets parsed before it ('cat > out.txt <<EOF' ASKS on out.txt) so an "
          "earlier relative cd/redirect still ASKS (only a position within or after the construct is a "
          "disclosed-residual allow), and never denies except on a missing tool_name. "
          "The write-scope guard (EN-8, wrtscp) is proven: ARMED, a Write/Edit/MultiEdit inside the "
          "per-slice declaration scope ALLOWS and one outside it DENIES (row 21); the frozen floor "
          "hard-denies a write into each frozen class (derived site/downloads/, manifest-self "
          ".aiqt/manifest.toml, archive .aiqt/archive/) and the floor's own .aiqt/frozen.json EVEN under a "
          "permissive declaration (row 18, deny over allow), while a non-frozen in-allow path still allows; "
          "an allow entry wholly on the floor makes the declaration malformed and DENIES (row 19); a "
          "sibling repo and a nested repo DENY in BOTH regimes (always-on structural, the conflict-2 "
          "resolution); UN-ARMED, a non-frozen in-repo write ALLOWS (slice confinement off) while the "
          "frozen and other/nested-repo denials still fire, and with the floor ABSENT the frozen layer goes "
          "inert (a would-be-frozen path allows) while the structural denial stays on; and every "
          "cannot-evaluate once armed DENIES (a malformed/boolean-version/worktree_root-mismatch "
          "declaration, an absent or malformed floor, a relative file_path) while a MISSING tool_name, a "
          "non-dict tool_input, a missing file_path, and a control-character file_path DENY as malformed "
          "calls; a relative file_path is inert un-armed (the sibling absolute_paths hook owns it); a "
          "covered write whose session root cannot be resolved (a non-git session or a payload with no cwd) "
          "DENIES fail-closed (FIX 2, MAJOR 3), as does every un-armed cannot-evaluate FAULT (an injected "
          "root or target canonicalization fault, a containment fault, and the codex-named nested-repo probe "
          "fault) that OLD code allowed at row 11; a BAD orchestration registry that locates the declaration "
          "DENIES an armed session's out-of-slice write rather than silently disarming via the XDG-default "
          "fallback (FIX 1, BLOCKER 2), proven against a declaration that MAY live in-tree at the "
          "registry-declared state_dir, and the deeper registry faults deny too - an unreadable-but-present "
          "registry (an lstat fault the old os.path.lexists swallowed to absent) and a registry declaring a "
          "present-but-empty state_dir (which old code fell to the XDG default over) both DENY (FIX A, "
          "round-3 BLOCKER), while a genuinely-absent state_dir key still selects the XDG default; faulting "
          "ONLY the second registry read (which the old double-read reached in the DECISION path, while the "
          "fixed _load_write_scope decision rests on exactly one read - a denial's best-effort guard-event "
          "logging may still read separately without affecting the decision) still DENIES, closing the "
          "TOCTOU fail-open (round-5 BLOCKER); Bash and "
          "Read are out of the matcher (the disclosed Bash residual); "
          "and a mis-wired event hard-blocks (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
