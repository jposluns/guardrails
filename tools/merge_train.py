#!/usr/bin/env python3
"""Merge train (opt-in): refresh every open PR's head branch with the base branch, taking the base's
side for the declared GENERATED files only, regenerating and checking them, and refusing everything
else. It does nothing unless the project commits .aiqt/merge-train.toml on its base branch.

  merge_train.py --dry-run [--repo DIR] [--pr N]...   live dry run: report what --apply would do
  merge_train.py [--repo DIR] [--pr N]... --apply     live run: commit and push
  merge_train.py --self-test [--red-on-revert]        hermetic fixture self-test (local bare remote, fake gh)
  merge_train.py --execution-report ABS_PATH          the self-test, also writing the executed check ids
  merge_train.py                                      no arguments: the same hermetic self-test

A live run is selected only by an explicit option (--dry-run, --apply, --repo or --pr; without
--apply it is a dry run). With no arguments at all the tool runs its hermetic self-test and never
a live run: tools/merge_train.py is the registered runner of the merge-train-selftest suite, and
the git-fixture-env config-injection lane launches every suite runner with no arguments, under
the caller's injected (and deliberately poisoned) git configuration, and requires a hermetic exit
0, as the other suite runners give. A bare live run could not meet that: it fetches the real
remote, and its outcome depends on the remote's state (exit 2 while the base tip carries no
config, then gh and the open PRs once it does). Live runs still read the operator's global and
system git configuration, unchanged (the threat model below trusts them); a credential helper
configured there keeps working for every fetch, ls-remote and push. Transport, plainly: the base
fetch, the PR-branch fetch, the ls-remote observer and the push all run FROM PRIVATE storage the
run owns (the per-run private base repository, or the per-PR scratch checkout) to the URL that
`git remote get-url <remote>` reports, so the repository's LOCAL transport settings never apply
to them: a local remote.<remote>.pushurl, a local credential helper, a local
remote.<remote>.uploadpack, a local core.sshCommand and local http.* settings are all ignored
(global and system settings still apply). The base fetch runs in a private bare repository
created fresh for the run (hooks disabled, no inherited local configuration, --no-tags,
--no-recurse-submodules and --refmap= passed explicitly), never in the author's repository, so it
cannot follow a symbolic remote-tracking ref, recurse on demand into a populated submodule with
the submodule's own refspecs, run a repository hook, or write any ref, object, reflog or
FETCH_HEAD the author can see. Every git call the tool ITSELF runs against the author's repository
goes through one author-side funnel (_git_author), with exactly two exceptions: the disclosed
--shared clone that creates each private scratch checkout (see _scratch_checkout), and the
alternate-ref `for-each-ref` child git itself spawns in the author's repository during the private
fetches (see _ALTERNATE_FETCH_CONFIG; it inherits GIT_NO_LAZY_FETCH, reads the author's local
configuration, and runs neither an fsmonitor command nor any hook). The funnel layers two
defences: protocol.allow=never fails any transport closed, a lazy fetch included, and
GIT_NO_LAZY_FETCH=1 (set after the GIT_ scrub) independently stops a read that names a missing
PROMISED object in a partial clone from launching a lazy fetch (which would execute a configured
remote.<remote>.uploadpack and write pack files into the author's object store);
core.fsmonitor=false and core.hooksPath are pinned off in option position. The tool's whole author-side write footprint, stated exactly: the
lock file and the retry marker, nothing else; no ref, no object content, no reflog, no FETCH_HEAD
and no hook or configured-command execution in the author's repository, on a dry run or an apply
run. One disclosed residual: an author-side object that already exists may have its MTIME
refreshed (git freshens an existing object instead of rewriting it when the private scratch
writes a byte-identical one through the shared object store), which resets that object's prune
clock and changes no content.

The tool NEVER mutates the author's working tree, index, HEAD or any local branch: that capability
was removed, not guarded (decision D-390-PRIVATE-WORKTREE). Per open PR, in PR-number order, every
merge, conflict resolution, regeneration, check and commit happens in a PRIVATE scratch checkout
the run creates for that PR (a --shared clone of the repository, detached at the PR's remote head)
and removes in a finally entered as soon as the directory exists, so a failure in the clone or in
the scratch setup cannot leak it; a SIGTERM, SIGHUP or SIGQUIT during a live run is converted into that
same cleanup, and a SIGINT unwinds through it as KeyboardInterrupt (the in-flight git or generator
child is terminated with its WHOLE process group, SIGTERM first so git removes its own lock files,
then SIGKILL after a short grace, and reaped, and the group is then probed: a grandchild that
ignored the SIGTERM is SIGKILLed even when the direct child exited first, and a group that still
has members when the bounded wait expires (unreaped zombies held for a subreaper, a member this
user cannot signal) is REPORTED on stderr, never silently claimed gone; every
cleanup finally runs with every signal in UNWIND_SIGNALS blocked, so a second signal cannot abort a removal
half-way and is re-delivered when that cleanup ends; the scratch and the private base repository
are removed, the lock is released) and the process then exits with the
conventional killed-by-signal status (an inherited SIG_IGN disposition, as under nohup, is left
alone; a group-wide SIGKILL from a supervisor, such as timeout -k or kill -9 on the group, cannot
be handled and leaves children running in their own sessions); checkout filters, text/eol/autocrlf conversion, ident expansion, the
working-tree encoding, hooks (core.hooksPath pinned to the null device) and fsmonitor are disabled
there for every git call. The author's worktree is only READ: the run locates the worktree that has
the PR branch checked out and re-confirms, against a fresh gh view, that it still names the same
open PR. That read-only branch-match check is kept because it anchors each PR to one local checkout
for reporting and refuses a renamed or closed PR; the old busy check and its process scan protected
in-place mutation and were removed with it, as were the dirty check and the local-position checks.
The author's local branch ref is NOT fast-forwarded by the tool: after a push the author pulls,
like after any other remote change.

With --apply the run validates the PR head branch name (git check-ref-format --branch plus the
lease's own character set; an unsupported name is refused as bad-branch-name before any work),
requires the gh view, the gh list and the remote tip to agree on the PR head, merges the fetched
base tip M in the private checkout with `git merge --no-commit --no-ff`, takes the base's side for
conflicted generated paths, refuses any other conflict, refuses a declared generated path that is
(or sits under) a symlink or resolves outside the checkout, always runs the regenerate commands,
runs the check commands, proves nothing outside the generated set differs from the automatic merge,
then proves that EVERY blob of the validated tree is byte-identical to the private-checkout file
the checks read (what was validated is what is pushed; a clean filter or autocrlf transform cannot
smuggle unvalidated bytes), commits with `git commit-tree` against that validated tree (no commit
hook runs), verifies the new commit's first parent is the exact PR head this run validated (which
by itself makes that head an ancestor of the new commit), checks the candidate in a SECOND, normal
private checkout (the committed attributes and the operator's global and system configuration
honoured, no local configuration carried over, hooks disabled) and refuses normal-checkout-failed
when any declared check fails there (regeneration with conversions disabled must not publish a
commit whose own fresh checkout fails its generators), and pushes from the private checkout with an
exact-value
compare-and-set (--force-with-lease=refs/heads/<branch>:<the head this run validated>, a 40- or
64-hex object id, which can only narrow what a plain push would accept) to the PR's own head
branch, so a branch rewound, advanced or deleted since the validation is refused by the remote,
never overwritten. One observer reads the remote after every push attempt, a timed-out or failed
one included; a push whose outcome cannot be observed (a push timeout, an observer timeout, an
unreadable remote, and a timed-out push with the remote still at the old head, which may still be
in flight in the remote's receive-pack) is INDETERMINATE: a small marker is kept so the retry can
classify it, and
nothing else changes anywhere. There is no rollback machinery, because nothing local is mutated and
so there is nothing to roll back.

The config is read from the fetched base tip (`git show <M>:.aiqt/merge-train.toml`), never from a
PR branch or the checkout; the [busy] table is still accepted, and ignored, for config
compatibility. The branch pushed to is the PR's own head branch as gh names it, re-confirmed before
the push; the tool takes no branch-name argument. The tool ships with the pack; this repository's
own .aiqt/merge-train.toml does not (.aiqt/core/ownership.toml excludes it), because its presence
is what enables the tool. An adopting project commits its own.

Stdout carries one JSON line per PR (schema merge-train/1); stderr carries one human line per PR.
Exit codes: 0 every PR current, pushed, already pushed or (dry run) mergeable; 1 any PR refused or
skipped; 2 the run failed closed and touched nothing (no config, malformed config, lock held, gh or
fetch failure, a possibly truncated PR list, bad arguments).

THREAT MODEL (cooperative): agents on a single-operator host. The repository's local config, the
operator's global and system config, hooks and the object store are trusted; the tool guards
against accidents and odd local state, not hostile repository state. Disclosed residuals: fork PRs
are refused (fork-pr), not supported; v1 supports the remote "origin" and the base "main" only; the
regenerate and check commands run the merged tree's copy of the generators, which may include the
PR's own changes; review_carry is information only; the private checkout reuses the repository's
object store through --shared, the same trust tier as the repository itself.

The tool never force-pushes (no --force, no -f, no bundled or abbreviated force, delete, mirror,
all or prune push option, no + or : push refspec, and no src:dst refspec other than the exact
<40-or-64-hex>:refs/heads/<branch> form away from the base; the one lease form its own argv audit
allows is the exact-value --force-with-lease=refs/heads/<branch>:<validated old head>, a pure
compare-and-set that only narrows what a plain push would accept, because the pushed commit's
first parent is verified to be that validated head), rebases, cherry-picks, stashes, cleans, runs
ANY reset, prunes worktrees, resolves a non-generated conflict, pushes to or checks out the base
branch, edits PR metadata, or reads an ambient git identity.
"""


import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("error: merge_train.py requires Python 3.11+ (tomllib).")

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "merge-train/1"
CONFIG_PATH = ".aiqt/merge-train.toml"
MARKER_NAME = "merge-train.json"
LOCK_NAME = "merge-train.lock"
PR_LIST_LIMIT = 200
GIT_TIMEOUT = 120
OK_RESULTS = ("current", "pushed", "already-pushed", "would-merge")
GLOB_CHARS = set("*?[]!" + chr(123) + chr(125))
CONFIG_KEYS = frozenset(("version", "remote", "base", "commit", "generated", "busy", "limits"))
TABLE_KEYS = dict(commit=frozenset(("name", "email")),
                  generated=frozenset(("paths", "regenerate", "check")),
                  busy=frozenset(("probe",)),
                  limits=frozenset(("command_timeout_seconds", "network_timeout_seconds")))
REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# The single push --force* form the argv audit allows: an exact-value compare-and-set lease, with a
# 40-hex (SHA-1) or 64-hex (SHA-256) validated old head.
PUSH_LEASE = re.compile(r"\A--force-with-lease=refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*:"
                        r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
# The single src:dst push refspec shape allowed: an exact object id onto a plain head, never main.
PUSH_SPEC = re.compile(r"\A[0-9a-f]{40}([0-9a-f]{24})?:refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
FORBIDDEN_PUSH_LONG = ("--force", "--force-with-lease", "--force-if-includes", "--delete",
                       "--mirror", "--all", "--prune")

# Every argv the tool hands to git, for the self-test's forbidden-operation audit.
GIT_AUDIT = []


class FailClosed(Exception):
    """The whole run stops before touching anything (exit 2)."""


class _Signalled(BaseException):
    """A SIGTERM, SIGHUP or SIGQUIT during a live run, converted into an exception so the run unwinds
    through its own finallys: the in-flight git or generator child has its whole process group
    terminated (SIGTERM, a bounded grace, then SIGKILL) and is reaped, the private scratch
    checkout and the private base repository are removed (each removal running with SIGTERM, SIGHUP
    and SIGQUIT blocked, so a second signal cannot interrupt it), the lock is released. main() then
    re-raises the signal under the default disposition, so the process exits with the
    conventional killed-by-signal status."""

    def __init__(self, signum):
        BaseException.__init__(self, signum)
        self.signum = signum


class Refuse(Exception):
    """One PR is refused; its private checkout is discarded and the run continues."""

    def __init__(self, status, reason=""):
        Exception.__init__(self, status)
        self.status = status
        self.reason = reason


def _now():
    return time.strftime("[%Y-%m-%dT%H:%MZ]", time.gmtime())


def _clean_env(extra=None):
    """The caller's environment with every GIT_-prefixed variable dropped (no repository-selecting or
    config-injecting variable reaches git), plus explicit additions."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or dict())
    return env


# How long a terminated child's process group is given to exit on SIGTERM before SIGKILL.
TERM_GRACE = 5.0

# Every signal the run unwinds on: SIGINT (Python's own KeyboardInterrupt) plus SIGTERM, SIGHUP
# and SIGQUIT (converted into _Signalled by run_train). ONE constant, used by the launch mask in
# _run_child, by _cleanup's mask, by the child-side mask restore and by the self-test's
# launch-window and second-signal cases, so the set cannot drift apart per call site (QA round 6,
# codex major 1: SIGINT was missing from the launch mask, so a KeyboardInterrupt at the launch
# boundary could reach the launch-failure handler with no handle under cleanup and leak the
# just-created session).
UNWIND_SIGNALS = frozenset((signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT))


def _cleanup(func):
    """Run ONE cleanup step with every signal in UNWIND_SIGNALS blocked. _Signalled and
    KeyboardInterrupt are BaseExceptions, so a second signal arriving while a finally runs
    shutil.rmtree would otherwise abort the removal (ignore_errors swallows OSError only, never
    an exception raised by a signal handler) and leak the directory being removed. The previous
    mask is restored afterwards, so a signal taken while blocked is delivered then: the run still
    dies by signal, only the cleanup itself is atomic against it."""
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, UNWIND_SIGNALS)
    try:
        func()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def _group_alive(pgid):
    """Whether the process group still has any member (a not-yet-reaped member counts briefly)."""
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def _end_child(proc, group):
    """Terminate a child and reap it: SIGTERM first (git removes its own *.lock files on SIGTERM,
    never on SIGKILL), a bounded grace, then SIGKILL for whatever ignored the SIGTERM. With group
    True the child's WHOLE process group is signalled and then probed (the child was
    started with start_new_session=True, so its pid names the group and every grandchild that
    stayed in it is included; a grandchild that itself calls setsid starts a new session, ESCAPES
    the group and cannot be ended here): the direct child exiting says nothing about a grandchild
    that ignored the SIGTERM, so after the direct child is reaped the group is probed, given the
    bounded grace to finish its own SIGTERM handling, SIGKILLed if anything remains, and probed
    again within the same bound. What that final probe CONFIRMS is only this: either the group
    was gone within the bound, or a stderr line reports the process group that remains (its
    members can be unreaped zombies, e.g. SIGKILLed descendants held for a subreaper that has not
    reaped them yet, or setsid escapes and unsignallable members); the group being GONE on return
    is NOT guaranteed. That is the dying-run path and the regenerate-or-check TIMEOUT
    path, where nothing the command spawned may keep running in a scratch directory the run is
    about to remove. With group False only the direct child is signalled: that is the push-TIMEOUT
    path ONLY (see _run_child), where the run itself continues and the in-flight receive-pack of a
    timed-out push to a local-path remote may legitimately still land the push; the push-unknown
    marker exists exactly to classify that on the retry."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            if group:
                os.killpg(proc.pid, signum)
            else:
                os.kill(proc.pid, signum)
        except OSError:
            pass
        try:
            proc.wait(timeout=TERM_GRACE)
            break
        except subprocess.TimeoutExpired:
            continue
    if group:
        deadline = time.monotonic() + TERM_GRACE
        while _group_alive(proc.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _group_alive(proc.pid):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            deadline = time.monotonic() + TERM_GRACE
            while _group_alive(proc.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
        if _group_alive(proc.pid):
            print("%s merge-train: process group %d still has members after SIGKILL; they may be "
                  "unreaped zombies (held for a subreaper) or setsid escapes; leaving them behind"
                  % (_now(), proc.pid), file=sys.stderr)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def _run_child(argv, cwd=None, env=None, timeout=None, push=False):
    """The single child-process funnel under _git and _run_external: every child starts in its OWN
    session (start_new_session=True), so a timeout and ANY unwind while the child runs (the
    _Signalled conversion of SIGTERM, SIGHUP or SIGQUIT included) terminate the child through
    _end_child, with signals blocked around that cleanup. Every signal in UNWIND_SIGNALS (SIGINT
    included: a KeyboardInterrupt here unwinds exactly like _Signalled) is BLOCKED
    across the launch itself, from just before the fork until the handle is covered by the cleanup
    try below, so a signal arriving in that window is held pending and then unwinds through the
    normal cleanup instead of leaking a just-created session; the mask is restored as the FIRST
    statement inside the protected region (never held across communicate, which would delay the
    run's response to a signal by the child's whole runtime), and the child starts with every
    UNWIND_SIGNALS member unblocked (a minimal preexec_fn that only unblocks that set between
    fork and exec), so git and the generators see the signals unblocked. An unwind ends the whole
    process group, and so does
    a timeout, EXCEPT a timed-out push (push=True), which ends the direct child only: the
    in-flight receive-pack is exactly the indeterminate outcome the push-unknown marker reconciles
    on the retry (see _end_child). Residual: a group-wide SIGKILL from a supervisor cannot be
    handled and leaves children running in their own sessions. Returns a CompletedProcess; raises
    subprocess.TimeoutExpired on a timeout and OSError on a launch failure."""
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, UNWIND_SIGNALS)

    def _child_mask():
        signal.pthread_sigmask(signal.SIG_UNBLOCK, UNWIND_SIGNALS)

    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                start_new_session=True, preexec_fn=_child_mask)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        raise
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        out, err = proc.communicate(timeout=timeout)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        _cleanup(lambda: _end_child(proc, group=not (timed_out and push)))
        if timed_out:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        raise
    return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)


def _forbidden(args):
    """The argv audit enforced on every git call, kept in exact agreement with the self-test's
    independently written _audit_violation: no rebase, cherry-pick, stash, clean, gc, worktree
    prune, and no reset of ANY kind (the tool has nothing local to reset); for push, no force,
    delete, mirror, all or prune option in long, abbreviated (git accepts unique prefixes) or
    bundled short form (-uf, -fu), no lease other than the one exact-value compare-and-set form
    (PUSH_LEASE, 40- or 64-hex), and no refspec other than a plain name or the exact
    <hex>:refs/heads/<branch> form away from the base (no + force spec, no :delete spec, no
    src:dst alias). The first non-option word is the repository (a URL may carry a colon)."""
    words = list(args)
    if words[:1] in (["rebase"], ["stash"], ["clean"], ["cherry-pick"], ["gc"], ["prune"],
                     ["reset"]):
        return True
    if words[:2] == ["worktree", "prune"]:
        return True
    if words[:1] != ["push"]:
        return False
    specs_only, repo_seen = False, False
    for w in words[1:]:
        if w == "--" and not specs_only:
            specs_only = True
            continue
        if not specs_only and w.startswith("-"):
            if not w.startswith("--"):
                if any(c in "fd" for c in w[1:]):
                    return True
                continue
            if PUSH_LEASE.match(w):
                continue
            stem = w.split("=", 1)[0]
            for banned in FORBIDDEN_PUSH_LONG:
                if len(stem) > 2 and (banned.startswith(stem) or stem.startswith(banned)):
                    return True
            continue
        if not repo_seen:
            repo_seen = True
            continue
        if w.startswith("+") or ":" in w:
            if not PUSH_SPEC.match(w) or w.endswith(":refs/heads/main"):
                return True
    return False


def _git(cwd, *args, extra_env=None, timeout=GIT_TIMEOUT, ok=(0,), push=False):
    """The single pinned git funnel: auto-maintenance pinned off in option position, scrubbed GIT_*,
    a timeout. Returns (returncode, stdout bytes, stderr text); raises Refuse("git-failed") on a
    timeout and on an exit code outside ok when ok is not None. The audit records every attempted
    argv, a refused one included."""
    GIT_AUDIT.append(list(args))
    if _forbidden(args):
        raise AssertionError("forbidden git operation refused by the argv audit: %r" % (args,))
    argv = ["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
    argv += list(args)
    try:
        proc = _run_child(argv, cwd=str(cwd), env=_clean_env(extra_env), timeout=timeout,
                          push=push)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refuse("git-failed", "git %s: %s" % (" ".join(args[:2]), exc))
    if ok is not None and proc.returncode not in ok:
        raise Refuse("git-failed", "git %s exited %d: %s" % (
            " ".join(args[:2]), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip()[:300]))
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def _git_text(cwd, *args, **kw):
    return _git(cwd, *args, **kw)[1].decode("utf-8", "replace").strip()


# The author-side read hardening (see _git_author). protocol.allow=never is safe here because no
# author-side call is a transport operation (git refuses even a local-path clone under it, so the
# two --shared clones FROM the author's store stay outside this funnel, at the --shared trust tier
# the threat model already discloses).
_AUTHOR_GIT_CONFIG = ("-c", "protocol.allow=never", "-c", "core.fsmonitor=false",
                      "-c", "core.hooksPath=" + os.devnull)
# What a FETCH from private storage whose object store includes the author's as an alternate can
# carry (protocol.allow=never cannot: the fetch itself is a transport operation). Only the
# ENVIRONMENT piece reaches the `git --git-dir=<author .git> for-each-ref` alternate-ref read git
# runs IN the author's repository to seed negotiation: git scrubs GIT_CONFIG_PARAMETERS from the
# environment of a child it points at another repository (local_repo_env), so the -c pairs below
# do NOT reach that child, and it reads the author's LOCAL configuration; GIT_NO_LAZY_FETCH is
# not scrubbed and does reach it, so that read cannot lazy-fetch a missing promised object.
# Accepted residual: for-each-ref runs neither an fsmonitor command nor any hook, so the
# author-local settings the -c pairs would pin off name nothing that child executes; the pairs
# still harden the fetch process itself, which receives them normally in option position.
_ALTERNATE_FETCH_ENV = dict(GIT_NO_LAZY_FETCH="1")
_ALTERNATE_FETCH_CONFIG = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull)


def _git_author(cwd, *args, extra_env=None, timeout=GIT_TIMEOUT, ok=(0,)):
    """The single author-side git funnel: every git call the tool itself runs against the
    author's repository (rev-parse, worktree list, remote get-url, check-ref-format,
    symbolic-ref) goes through here; the two calls that touch the author's repository WITHOUT it
    are the disclosed --shared clone (_scratch_checkout) and the alternate-ref child git spawns
    there during the private fetches (see _ALTERNATE_FETCH_CONFIG). On top of _git's pins it
    layers two independent lazy-fetch defences: protocol.allow=never (option position) fails ANY
    transport closed, including the lazy fetch that a read naming a missing PROMISED object in a
    partial clone would launch (which would execute a configured remote.<remote>.uploadpack and
    write pack files into the author's object store), and GIT_NO_LAZY_FETCH=1, set AFTER
    _clean_env's GIT_ scrub, stops the same lazy fetch before any transport is attempted. Either
    layer alone fails such a read closed, so no behavioural self-test can distinguish them; each
    is pinned instead by the argv and environment the child process receives
    (fetch/author-env-no-lazy-fetch). core.fsmonitor and core.hooksPath are pinned off in option
    position. Author-side footprint of a call made here, stated
    exactly: no ref, no object content, no reflog, no FETCH_HEAD, no hook, no configured command
    and no object mtime change (the one disclosed mtime refresh comes from the scratch's object
    writes through the shared store, never from these reads; see _scratch_checkout)."""
    env = dict(GIT_NO_LAZY_FETCH="1")
    env.update(extra_env or dict())
    return _git(cwd, *(_AUTHOR_GIT_CONFIG + args), extra_env=env, timeout=timeout, ok=ok)


def _git_author_text(cwd, *args, **kw):
    return _git_author(cwd, *args, **kw)[1].decode("utf-8", "replace").strip()


def _run_external(argv, cwd, timeout, extra_env=None):
    """Launch a non-git program named by the trusted config, or the gh CLI: an argv list, no shell, a
    timeout. Returns (returncode, or None on a timeout or launch error, stdout text, stderr text)."""
    try:
        proc = _run_child(list(argv), cwd=str(cwd), env=_clean_env(extra_env), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


# ---------------------------------------------------------------- guards (the GUARDS table below)


def guard_config_fail_closed(data):
    """Raise FailClosed unless data is a well-formed version-1 merge-train config."""
    def bad(msg):
        raise FailClosed("malformed %s: %s" % (CONFIG_PATH, msg))

    def str_list(value, where, allow_empty=False):
        if not isinstance(value, list) or (not value and not allow_empty):
            bad("%s must be a%s list of strings" % (where, "" if allow_empty else " non-empty"))
        for item in value:
            if not isinstance(item, str) or not item or "\0" in item:
                bad("%s must hold non-empty strings" % where)

    def commands(value, where):
        if not isinstance(value, list) or not value:
            bad("%s must be a non-empty list of commands" % where)
        for command in value:
            str_list(command, where + " command")

    if not isinstance(data, dict):
        bad("not a table")
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        bad("unknown key(s) %s" % sorted(unknown))
    for key in ("version", "remote", "base", "commit", "generated"):
        if key not in data:
            bad("missing key %s" % key)
    if type(data["version"]) is not int or data["version"] != 1:
        bad("version must be the integer 1")
    for key in ("remote", "base"):
        value = data[key]
        if not isinstance(value, str) or not REF_NAME.match(value) or ".." in value:
            bad("%s must be a plain name" % key)
    for table, keys in TABLE_KEYS.items():
        if table not in data:
            continue
        if not isinstance(data[table], dict):
            bad("[%s] must be a table" % table)
        unknown = set(data[table]) - keys
        if unknown:
            bad("unknown key(s) in [%s]: %s" % (table, sorted(unknown)))
    for key in ("name", "email"):
        value = data["commit"].get(key)
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "<>\n\r\0"):
            bad("[commit].%s is required and must be a plain string" % key)
    generated = data["generated"]
    for key in ("paths", "regenerate", "check"):
        if key not in generated:
            bad("missing [generated].%s" % key)
    str_list(generated["paths"], "[generated].paths")
    seen = set()
    for path in generated["paths"]:
        parts = path.split("/")
        if (path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts)
                or GLOB_CHARS & set(path)):
            bad("[generated].paths entry %r must be an exact relative path" % path)
        if path in seen:
            bad("[generated].paths entry %r appears twice" % path)
        seen.add(path)
    commands(generated["regenerate"], "[generated].regenerate")
    commands(generated["check"], "[generated].check")
    if "busy" in data and "probe" in data["busy"]:
        str_list(data["busy"]["probe"], "[busy].probe", allow_empty=True)
    for key, value in data.get("limits", dict()).items():
        if type(value) is not int or value <= 0:
            bad("[limits].%s must be a positive integer" % key)


def guard_config_source(repo, base_sha):
    """The config TEXT, read from the fetched base tip only (never the checkout or a PR branch),
    out of the run's private base repository; None when the base tip carries no config."""
    code, out, _err = _git(repo, "show", "%s:%s" % (base_sha, CONFIG_PATH), ok=None)
    if code != 0:
        return None
    return out.decode("utf-8", "replace")


def guard_branch_name(root, branch):
    """Refuse bad-branch-name BEFORE any work for a PR head branch name the tool does not support.
    The name comes from gh: it must satisfy git check-ref-format --branch and the exact-value
    lease's own character set [A-Za-z0-9._/-] (a name outside it, such as one carrying + or @,
    cannot be expressed in the one lease form the argv audit allows, so it is refused up front
    with a named status instead of crashing later)."""
    if not isinstance(branch, str) or not REF_NAME.match(branch) or ".." in branch:
        raise Refuse("bad-branch-name", "unsupported PR head branch name %r" % (branch,))
    if _git_author(root, "check-ref-format", "--branch", branch, ok=None)[0] != 0:
        raise Refuse("bad-branch-name", "git check-ref-format refuses %r" % (branch,))


def guard_branch_match(wt, branch, gh_view):
    """Refuse branch-mismatch unless the worktree's symbolic ref, the listed head branch and gh's
    fresh view all name the same branch of an open PR."""
    code, out, _err = _git_author(wt, "symbolic-ref", "--quiet", "--short", "HEAD", ok=None)
    if code != 0:
        raise Refuse("branch-mismatch", "worktree HEAD is detached")
    current = out.decode("utf-8", "replace").strip()
    if current != branch or gh_view.get("headRefName") != branch:
        raise Refuse("branch-mismatch", "worktree on %r, PR head %r, gh now %r" % (
            current, branch, gh_view.get("headRefName")))
    if gh_view.get("state") != "OPEN":
        raise Refuse("branch-mismatch", "PR is no longer open")


def guard_source_conflict(unmerged, generated):
    """Refuse source-conflict for any unmerged path outside the generated set."""
    outside = sorted(p for p in unmerged if p not in generated)
    if outside:
        raise Refuse("source-conflict", "conflicts outside the generated set: %s" % (
            ", ".join(outside[:10])))


def guard_regenerate_exit(code, command, err):
    if code != 0:
        raise Refuse("regenerate-failed", "%s exited %s: %s" % (" ".join(command), code,
                                                               err.strip()[:300]))


def guard_check_exit(code, command, err):
    if code != 0:
        raise Refuse("check-failed", "%s exited %s: %s" % (" ".join(command), code,
                                                          err.strip()[:300]))


def guard_fixpoint(wt):
    """After regenerate, add and check: nothing unstaged or unmerged (regenerate-not-fixpoint) and
    no untracked file (undeclared-write). --no-renames keeps a base-side rename as separate add
    and delete records: a rename record's ORIGINAL path arrives as its own NUL-separated field,
    which this parser would misread as a record of its own and falsely refuse."""
    out = _git(wt, "status", "--porcelain=v2", "-z", "--no-renames", "--untracked-files=all")[1]
    for entry in [e.decode("utf-8", "replace") for e in out.split(b"\0") if e]:
        if entry.startswith("? "):
            raise Refuse("undeclared-write", "untracked file %s" % entry[2:])
        if entry[:1] in ("1", "2") and entry[3:4] != ".":
            raise Refuse("regenerate-not-fixpoint", "unstaged change: %s" % entry[-200:])
        if entry[:1] == "u":
            raise Refuse("regenerate-not-fixpoint", "unmerged entry left: %s" % entry[-200:])


def guard_reconcile(ctx, marker):
    """A marker records only a push whose outcome this tool could not observe (a push or observer
    timeout, an unreadable remote): it exists to make the retry safe to CLASSIFY, never to gate
    anything local, because the tool mutates nothing local. Returns a finished partial report
    (already-pushed) when the recorded push turns out to have been delivered, or None to proceed
    with a fresh private checkout (the stale marker is then dropped); raises
    Refuse(state-mismatch) for a marker naming another PR or branch and Refuse(push-unknown) while
    the remote is still unreadable (the marker is kept and nothing changes)."""
    if marker is None:
        return None
    if marker.get("pr") != ctx["pr"] or marker.get("branch") != ctx["branch"]:
        raise Refuse("state-mismatch", "the marker names another PR or branch")
    remote = _remote_head(ctx)
    if remote is None:
        raise Refuse("push-unknown",
                     "the recorded push is still unconfirmed: the remote is unreadable")
    if remote == marker.get("new"):
        _remove_marker(ctx["git_dir"])
        return dict(result="already-pushed", old_head=marker.get("old"),
                    new_head=marker.get("new"),
                    reason="an earlier run pushed %s" % marker.get("new"))
    _remove_marker(ctx["git_dir"])
    return None


def guard_scratch_setup(scratch):
    """Disable every content conversion and every hook in the private checkout, so the bytes the
    regenerate and check commands read are the bytes git stores and pushes: checkout and checkin
    filters, text/eol/autocrlf conversion, ident expansion and the working-tree encoding are unset
    for every path (the checkout's own info/attributes outranks any in-tree .gitattributes), hooks
    are disabled (core.hooksPath pinned to the null device; the commit is made with commit-tree,
    which runs no hook anyway) and fsmonitor is off."""
    for key, value in (("core.autocrlf", "false"), ("core.eol", "lf"),
                       ("core.hooksPath", os.devnull), ("core.fsmonitor", "false")):
        _git(scratch, "config", key, value)
    git_dir = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    info = os.path.join(git_dir, "info")
    os.makedirs(info, exist_ok=True)
    with open(os.path.join(info, "attributes"), "w", encoding="utf-8") as handle:
        handle.write("* -filter -text -eol -crlf -ident -working-tree-encoding\n")


def guard_pushed_bytes(scratch, tree):
    """Refuse commit-mismatch unless EVERY blob of the validated tree is byte-identical to the
    private-checkout file the regenerate and check commands read (every path, not only generated
    ones): a content transform between the checkout and the object store (a clean filter,
    autocrlf, eol or ident conversion) would otherwise push bytes the checks never saw. The
    checkout bytes are hashed directly against the repository's own object format, with no filter
    in the path; a symlink must reproduce the committed target; gitlinks carry no bytes and are
    skipped."""
    algo = _git_text(scratch, "rev-parse", "--show-object-format")
    hasher = dict(sha1=hashlib.sha1, sha256=hashlib.sha256).get(algo)
    if hasher is None:
        raise Refuse("commit-mismatch", "unknown object format %r" % algo)
    root = str(scratch)
    for row in _git(scratch, "ls-tree", "-r", "-z", tree)[1].split(b"\0"):
        if not row:
            continue
        meta, raw = row.split(b"\t", 1)
        mode, kind, sha = meta.decode("utf-8", "replace").split()
        if kind != "blob":
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        path = os.path.join(root, rel)
        if mode == "120000":
            target = _git(scratch, "cat-file", "blob", sha)[1]
            try:
                link = os.readlink(path)
            except OSError as exc:
                raise Refuse("commit-mismatch", "%s: %s" % (rel, exc))
            if os.fsencode(link) != target:
                raise Refuse("commit-mismatch",
                             "symlink %s differs from the committed target" % rel)
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise Refuse("commit-mismatch", "%s unreadable: %s" % (rel, exc))
        if hasher(b"blob %d\0" % len(data) + data).hexdigest() != sha:
            raise Refuse("commit-mismatch",
                         "%s: the checkout bytes differ from the committed blob" % rel)


def guard_commit_parents(scratch, old, new):
    """Refuse commit-mismatch unless the new commit's FIRST parent is exactly the PR head this run
    validated: a parent is by definition an ancestor, so this alone makes the exact-value lease a
    pure narrowing of a plain push (never a rewind), and a rewritten or parentless commit is never
    pushed (the former separate is-ancestor re-check was implied by this one and was removed as
    dead code)."""
    parents = _git_text(scratch, "rev-list", "--parents", "-n", "1", new).split()[1:]
    if not parents or parents[0] != old:
        raise Refuse("commit-mismatch", "first parent %s is not the validated head %s" % (
            parents[0] if parents else "(none)", old))


def guard_fresh_checkout(ctx, scratch, new):
    """Refuse normal-checkout-failed unless the candidate commit also passes every declared check
    in a SECOND, normal private checkout: a fresh --shared clone of the private checkout with the
    committed attributes and the operator's global and system configuration honoured, no local
    configuration carried over, and hooks disabled. The conversion-free private checkout proves
    the stored bytes are the validated bytes; this proves the commit is also correct the way a
    consumer using ONLY the operator's global and system configuration materializes it (a
    committed eol=crlf attribute or an operator core.autocrlf=true would otherwise turn a passing
    PR into a pushed commit whose own fresh checkout fails its generators). A filter or conversion
    defined only in a repository's LOCAL configuration is NOT modelled here: this fresh clone
    carries no local configuration, so a consumer whose checkout depends on a repository-local
    filter is out of this check's scope."""
    fresh_root = tempfile.mkdtemp(prefix="merge-train-fresh-")
    SCRATCHES.append(fresh_root)
    try:
        fresh = os.path.join(fresh_root, "co")
        _git(scratch, "clone", "--quiet", "--shared", "--no-checkout", str(scratch), fresh)
        for key, value in (("core.hooksPath", os.devnull), ("core.fsmonitor", "false")):
            _git(fresh, "config", key, value)
        _git(fresh, "checkout", "--quiet", "--detach", new, timeout=ctx["cmd_timeout"])
        for command in ctx["cfg"]["generated"]["check"]:
            code, _out, err = _run_external(command, fresh, ctx["cmd_timeout"],
                                            dict(PYTHONDONTWRITEBYTECODE="1"))
            if code != 0:
                raise Refuse("normal-checkout-failed",
                             "%s exited %s in a fresh normal checkout of the candidate: %s" % (
                                 " ".join(command), code, err.strip()[:300]))
    finally:
        _cleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))


def guard_push_lease(branch, old):
    """The push's exact-value compare-and-set argv: the remote refuses unless the branch is still
    exactly at old, the head this run validated, so a rewound, advanced or deleted branch is
    refused, never overwritten."""
    return ["--force-with-lease=refs/heads/%s:%s" % (branch, old)]


def guard_push_indeterminate(_exc):
    """A push that timed out or failed to launch may still have reached the remote: swallow the
    error so the one observer reads the remote and decides; a rollback never precedes
    observation."""
    return None


def guard_generated_confined(wt, generated):
    """Refuse generated-symlink when a declared generated path, or any directory on the way to it,
    is a symlink or resolves outside the worktree: regenerating through it would write outside the
    declared set."""
    root = os.path.realpath(str(wt))
    for rel in generated:
        parts = rel.split("/")
        for depth in range(1, len(parts) + 1):
            if os.path.islink(os.path.join(root, *parts[:depth])):
                raise Refuse("generated-symlink", "/".join(parts[:depth]) + " is a symlink")
        real = os.path.realpath(os.path.join(root, *parts))
        if real != root and not real.startswith(root + os.sep):
            raise Refuse("generated-symlink", "%s resolves outside the worktree" % rel)


def guard_commit_tree(wt, new, validated_tree):
    """Refuse commit-mismatch when the committed tree is not the validated one (a pre-commit hook
    that edits and restages files would otherwise push content the checks never saw)."""
    if _git_text(wt, "rev-parse", new + "^{tree}") != validated_tree:
        raise Refuse("commit-mismatch",
                     "the committed tree is not the validated tree %s" % validated_tree)


def guard_scratch_complete(scratch):
    """Refuse scratch-incomplete when the detached checkout did not materialize every tracked
    file: `git checkout` can EXIT 0 and still fail to read a blob the shared object store cannot
    supply (QA round 6, observed on git 2.53.0: in a --shared clone of a blob:none partial clone
    whose promised blob is absent, checkout prints "error: unable to read sha1 file", exits 0,
    and leaves the path missing from the worktree), and regenerating and validating over that
    incomplete tree would push content the PR never contained. The probe is plain `git status`
    against the just-detached HEAD: any worktree difference immediately after a fresh checkout
    means the checkout was incomplete."""
    out = _git(scratch, "status", "--porcelain=v2", "-z", "--untracked-files=no")[1]
    rows = [r.decode("utf-8", "replace") for r in out.split(b"\0") if r]
    if rows:
        raise Refuse("scratch-incomplete",
                     "the scratch checkout did not materialize cleanly (a missing or unreadable "
                     "object, e.g. a promised blob of a partial clone): %s" % "; ".join(rows[:5]))


# Every guard is reached through this table, so --red-on-revert can replace each one in turn.
GUARDS = dict([
    ("config-fail-closed", guard_config_fail_closed),
    ("config-source", guard_config_source),
    ("branch-name", guard_branch_name),
    ("branch-match", guard_branch_match),
    ("source-conflict", guard_source_conflict),
    ("regenerate-exit", guard_regenerate_exit),
    ("check-exit", guard_check_exit),
    ("fixpoint", guard_fixpoint),
    ("reconcile", guard_reconcile),
    ("scratch-setup", guard_scratch_setup),
    ("scratch-complete", guard_scratch_complete),
    ("generated-confined", guard_generated_confined),
    ("pushed-bytes", guard_pushed_bytes),
    ("commit-parents", guard_commit_parents),
    ("commit-tree", guard_commit_tree),
    ("fresh-checkout", guard_fresh_checkout),
    ("push-lease", guard_push_lease),
    ("push-indeterminate", guard_push_indeterminate),
])


def _guard(name):
    return GUARDS[name]


# ---------------------------------------------------------------- helpers


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _read_marker(git_dir):
    path = os.path.join(git_dir, MARKER_NAME)
    if not os.path.lexists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return dict(phase="unreadable")
    return data if isinstance(data, dict) else dict(phase="unreadable")


def _write_marker(git_dir, data):
    """Temp file, fsync, rename: a reader sees the old marker or the new one, never a torn one."""
    fd, tmp = tempfile.mkstemp(prefix=".merge-train-", dir=git_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, os.path.join(git_dir, MARKER_NAME))


def _remove_marker(git_dir):
    try:
        os.unlink(os.path.join(git_dir, MARKER_NAME))
    except FileNotFoundError:
        pass


def _remote_head(ctx):
    """The remote branch tip through ls-remote, run in the run's PRIVATE base repository and BY
    URL: the URL is re-read from the author's repository per call (a read of plain config, so a
    mid-run remote.<remote>.url change is honoured as before), but the ls-remote itself never runs
    in the author's repository and never by remote name, so no author-local transport setting
    (remote.<remote>.uploadpack, core.sshCommand, http.*) can name a command to execute or apply.
    Returns a SHA, "" when absent, None when unreadable for ANY reason, a timeout included; the
    caller treats None as indeterminate, never as a cue to change anything."""
    try:
        url = _git_author_text(ctx["root"], "remote", "get-url", ctx["remote"])
        code, out, _err = _git(ctx["base_repo"], "ls-remote", "--", url,
                               "refs/heads/" + ctx["branch"], ok=None,
                               timeout=ctx["net_timeout"])
    except Refuse:
        return None
    if code != 0:
        return None
    lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return ""
    sha = lines[0].split()[0]
    return sha if SHA.match(sha) else None


def _snapshot(wt):
    """HEAD, the symbolic ref, the index entries, the full status with ignored files, and a SHA-256
    of every tracked and untracked file, ignored files included: the self-test's proof that the
    author's worktree, index and refs are byte-identical across a run."""
    head = _git_text(wt, "rev-parse", "HEAD")
    code, out, _err = _git(wt, "symbolic-ref", "--quiet", "HEAD", ok=None)
    index = _git(wt, "ls-files", "-s", "-z")[1]
    status = _git(wt, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored")[1]
    files = _git(wt, "ls-files", "-z", "--cached", "--others")[1]
    digests = dict()
    for raw in sorted(set(f for f in files.split(b"\0") if f)):
        path = os.path.join(str(wt), raw.decode("utf-8", "surrogateescape"))
        try:
            if os.path.islink(path):
                digests[raw] = "link:" + os.readlink(path)
            elif os.path.isfile(path):
                with open(path, "rb") as handle:
                    digests[raw] = hashlib.sha256(handle.read()).hexdigest()
            else:
                digests[raw] = "absent"
        except OSError as exc:
            digests[raw] = "error:" + str(exc)
    return dict(head=head, symref=out.strip() if code == 0 else None, index=index, status=status,
                files=digests)


# Every private scratch checkout ever created, for the self-test's removal proof.
SCRATCHES = []


def _scratch_checkout(ctx, scratch_root):
    """The PRIVATE scratch checkout for one PR, created INSIDE scratch_root, a directory the
    caller made and already covers with its finally (so a failure in the clone or in the
    scratch-setup guard cannot leak the directory): a --shared clone of the repository (the object
    store is reused; nothing of the author's checkout is written), with every conversion and hook
    disabled by the scratch-setup guard. Returns the checkout path. The author's worktree is never
    written: no merge, checkout, add, commit or reset ever runs there."""
    scratch = os.path.join(scratch_root, "co")
    _git(ctx["root"], "clone", "--quiet", "--shared", "--no-checkout", str(ctx["root"]), scratch)
    # The base tip's objects were fetched into the run's PRIVATE base repository, never into the
    # author's object store, so the scratch attaches that store as a second alternate. Disclosed
    # footprint of writing objects in a checkout whose alternates include the author's store: an
    # object the author already stores byte-identically is FRESHENED instead of rewritten (its
    # mtime is refreshed in the author's objects/, its content never changes).
    scratch_git = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    with open(os.path.join(scratch_git, "objects", "info", "alternates"), "a",
              encoding="utf-8") as handle:
        handle.write(os.path.join(ctx["base_repo"], "objects") + "\n")
    _guard("scratch-setup")(scratch)
    return scratch


def _snapshot_diff(before, after):
    diffs = [k for k in ("head", "symref", "index", "status") if before[k] != after[k]]
    paths = sorted(set(before["files"]) | set(after["files"]))
    diffs += [p.decode("utf-8", "replace") for p in paths
              if before["files"].get(p) != after["files"].get(p)]
    return diffs


def _unmerged(wt):
    """path -> set of stages, from ls-files -u -z."""
    result = dict()
    for entry in _git(wt, "ls-files", "-u", "-z")[1].split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            result.setdefault(path.decode("utf-8", "surrogateescape"), set()).add(
                int(meta.split()[2]))
    return result


def _index_entries(wt):
    """path -> list of (mode, sha, stage) for every index entry."""
    result = dict()
    for entry in _git(wt, "ls-files", "-s", "-z")[1].split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            result.setdefault(path.decode("utf-8", "surrogateescape"), []).append(
                tuple(meta.decode().split()))
    return result


def _changed_lines(wt, left, right, generated):
    """Per-file ordered +/- lines of git diff -U0 (line numbers dropped), generated paths excluded;
    right None compares left with the index."""
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames"]
    args += ["--cached", left] if right is None else [left, right]
    args += ["--", "."] + [":(exclude,literal)" + p for p in generated]
    out = _git(wt, *args)[1].decode("utf-8", "replace")
    files, current = dict(), None
    for line in out.splitlines():
        if line.startswith("diff --git "):
            current = files.setdefault(line, [])
        elif line.startswith(("+++", "---", "@@", "index ", "new file", "deleted file")):
            continue
        elif current is not None and line[:1] in ("+", "-"):
            current.append(line)
    return files


def _run_checks(ctx, scratch):
    for command in ctx["cfg"]["generated"]["check"]:
        code, _out, err = _run_external(command, scratch, ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("check-exit")(code, command, err)


def _push_and_observe(ctx, scratch, old, new):
    """Compare-and-set push of new from the private checkout to the PR's own branch (the
    exact-value lease makes the remote refuse unless the branch is still at old, the head this run
    validated), then one observer over the remote. A push whose outcome is unknown (a timeout, a
    launch failure) and an observation failure of ANY kind, an observer timeout included, are
    INDETERMINATE: the marker is kept and nothing changes anywhere; nothing local exists to roll
    back. A push that raised AND left the remote still at the old head is ALSO indeterminate, not
    push-rejected: the remote's receive-pack may still be running (a slow pre-receive hook) and
    the push can land after this run reports, so the marker is kept for the retry to classify."""
    branch = ctx["branch"]
    indeterminate = False
    try:
        # push=True: a TIMED-OUT push ends the direct child only, because its in-flight
        # receive-pack may still land the push; that outcome is exactly what the marker
        # reconciles on the retry (see _run_child and _end_child).
        _git(scratch, "push", "--porcelain", "--no-follow-tags",
             *_guard("push-lease")(branch, old), "--", ctx["remote_url"],
             "%s:refs/heads/%s" % (new, branch), ok=None, timeout=ctx["net_timeout"], push=True)
    except Refuse as exc:
        _guard("push-indeterminate")(exc)
        indeterminate = True
    observed = _remote_head(ctx)
    if observed == new:
        _remove_marker(ctx["git_dir"])
        return dict(result="pushed", old_head=old, new_head=new, reason="")
    if observed is None:
        raise Refuse("push-unknown",
                     "remote unreadable after the push; the marker is kept, nothing changed")
    if indeterminate and observed == old:
        raise Refuse("push-unknown",
                     "the push timed out with the remote still at the old head; it may still be "
                     "in flight, so the marker is kept")
    _remove_marker(ctx["git_dir"])
    status = "push-rejected" if observed == old else "remote-changed"
    raise Refuse(status, "remote at %s" % (observed or "(absent)"))


# ---------------------------------------------------------------- the run


def load_config(root, base_sha):
    text = _guard("config-source")(root, base_sha)
    if text is None:
        raise FailClosed("merge-train not enabled: %s is absent on the base tip %s" % (
            CONFIG_PATH, base_sha))
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise FailClosed("malformed %s: %s" % (CONFIG_PATH, exc))
    _guard("config-fail-closed")(data)
    return data


def _worktrees(root):
    """[dict(path, branch)] from git worktree list --porcelain; a detached worktree has branch None."""
    rows, row = [], None
    for line in _git_author_text(root, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if row:
                rows.append(row)
            row = None
        elif line.startswith("worktree "):
            row = dict(path=line[len("worktree "):], branch=None)
        elif line.startswith("branch refs/heads/") and row is not None:
            row["branch"] = line[len("branch refs/heads/"):]
    return rows


def _gh_json(argv, root, timeout):
    code, out, err = _run_external(argv, root, timeout)
    if code != 0:
        raise FailClosed("gh failed (%s): %s" % (code, err.strip()[:300]))
    try:
        return json.loads(out)
    except ValueError as exc:
        raise FailClosed("gh returned malformed JSON: %s" % exc)


def _gh_view(ctx):
    try:
        data = _gh_json(["gh", "pr", "view", str(ctx["pr"]), "--json",
                         "headRefName,headRefOid,state"], ctx["root"], ctx["net_timeout"])
    except FailClosed as exc:
        raise Refuse("branch-mismatch", "gh pr view unreadable: %s" % exc)
    if not isinstance(data, dict):
        raise Refuse("branch-mismatch", "gh pr view returned no object")
    return data


def _process_pr(ctx, apply):
    """One PR, read-only outside the private scratch checkout. Returns a partial report; raises
    Refuse for a refusal. The author's worktree, index, HEAD and local branch are never written;
    the only author-side state is the retry marker in the worktree's git dir."""
    marker = _read_marker(ctx["git_dir"])
    if marker is not None and not apply:
        raise Refuse("state-mismatch", "a merge-train marker is present; rerun with --apply")
    done = _guard("reconcile")(ctx, marker)
    if done is not None:
        return done
    view = _gh_view(ctx)
    _guard("branch-match")(ctx["wt"], ctx["branch"], view)
    remote = _remote_head(ctx)
    if remote is None:
        raise Refuse("pr-head-moved", "the remote branch tip could not be read")
    if remote != ctx["head_oid"] or view.get("headRefOid") != ctx["head_oid"]:
        raise Refuse("pr-head-moved", "remote head %s, gh %s, listed %s" % (
            remote, view.get("headRefOid"), ctx["head_oid"]))
    old = ctx["head_oid"]
    ctx["old"] = old
    scratch_root = tempfile.mkdtemp(prefix="merge-train-scratch-")
    SCRATCHES.append(scratch_root)
    try:
        scratch = _scratch_checkout(ctx, scratch_root)
        # The scratch's alternates include the author's store, so this fetch carries the same
        # hardening as the base fetch: the alternate-ref read it spawns in the author's
        # repository inherits GIT_NO_LAZY_FETCH (the -c pairs are scrubbed from that child's
        # environment and do not reach it; see _ALTERNATE_FETCH_CONFIG).
        _git(scratch, *_ALTERNATE_FETCH_CONFIG, "fetch", "--no-tags", "--no-recurse-submodules",
             "--", ctx["remote_url"], "refs/heads/" + ctx["branch"],
             extra_env=dict(_ALTERNATE_FETCH_ENV), timeout=ctx["net_timeout"])
        if _git_text(scratch, "rev-parse", "FETCH_HEAD^0") != old:
            raise Refuse("pr-head-moved", "the remote head moved during the fetch")
        if _git(scratch, "merge-base", "--is-ancestor", ctx["base_sha"], old, ok=None)[0] == 0:
            return dict(result="current", old_head=old, new_head=None, reason="")
        if not apply:
            return dict(result="would-merge", old_head=old, new_head=None,
                        reason="dry run: would merge %s (rerun with --apply)" % ctx["base_sha"])
        _git(scratch, "checkout", "--quiet", "--detach", old, timeout=ctx["cmd_timeout"])
        _guard("scratch-complete")(scratch)
        return _merge_commit_push(ctx, scratch, old, ctx["base_sha"])
    finally:
        _cleanup(lambda: shutil.rmtree(scratch_root, ignore_errors=True))


def _merge_commit_push(ctx, scratch, old, base):
    generated = ctx["generated"]
    ident = dict(GIT_AUTHOR_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_AUTHOR_EMAIL=ctx["cfg"]["commit"]["email"],
                 GIT_COMMITTER_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_COMMITTER_EMAIL=ctx["cfg"]["commit"]["email"])
    scratch_git = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    _git(scratch, "merge", "--no-commit", "--no-ff", "--no-edit", base, extra_env=ident, ok=None,
         timeout=ctx["cmd_timeout"])
    if not os.path.lexists(os.path.join(scratch_git, "MERGE_HEAD")):
        raise Refuse("git-failed", "the merge did not start")
    unmerged = _unmerged(scratch)
    _guard("source-conflict")(unmerged, generated)
    for path in sorted(unmerged):
        if unmerged[path] != set((1, 2, 3)):
            raise Refuse("generated-delete-conflict", path)
    for path in sorted(unmerged):
        _git(scratch, "checkout", "--theirs", "--", path)
        _git(scratch, "add", "--", path)
    _guard("generated-confined")(scratch, generated)
    automatic = _index_entries(scratch)
    for command in ctx["cfg"]["generated"]["regenerate"]:
        code, _out, err = _run_external(command, scratch, ctx["cmd_timeout"],
                                        dict(PYTHONDONTWRITEBYTECODE="1"))
        _guard("regenerate-exit")(code, command, err)
    _git(scratch, "add", "-A", "--", *generated)
    _run_checks(ctx, scratch)
    _guard("fixpoint")(scratch)
    resolved = _index_entries(scratch)
    hand = sorted(p for p in set(automatic) | set(resolved)
                  if automatic.get(p) != resolved.get(p) and p not in generated)
    if hand:
        raise Refuse("undeclared-write", "paths outside the generated set changed: %s" % (
            ", ".join(hand[:10])))
    merge_base = _git_text(scratch, "merge-base", old, base)
    ctx["review_carry"] = (_changed_lines(scratch, merge_base, old, generated)
                           == _changed_lines(scratch, base, None, generated))
    ctx["regenerated"] = [p for p in sorted(generated) if p in resolved]
    validated_tree = _git_text(scratch, "write-tree")
    _guard("pushed-bytes")(scratch, validated_tree)
    view = _gh_view(ctx)
    _guard("branch-match")(ctx["wt"], ctx["branch"], view)
    title = "Merge %s/%s into %s (merge-train)" % (ctx["remote"], ctx["base"], ctx["branch"])
    new = _git_text(scratch, "commit-tree", validated_tree, "-p", old, "-p", base,
                    "-m", title, "-m", "Merge-train: base=%s" % base, extra_env=ident,
                    timeout=ctx["cmd_timeout"])
    _guard("commit-tree")(scratch, new, validated_tree)
    _guard("commit-parents")(scratch, old, new)
    _guard("fresh-checkout")(ctx, scratch, new)
    _write_marker(ctx["git_dir"], dict(pr=ctx["pr"], branch=ctx["branch"], old=old, new=new))
    return _push_and_observe(ctx, scratch, old, new)


def _base_fetch(root, run_dir, remote, base):
    """Resolve the base tip WITHOUT running any fetch in the author's repository (QA rounds 3 and
    4: a fetch there could follow a symbolic refs/remotes/<remote>/<base> and rewind an
    author-owned branch, recurse on demand into a populated submodule with the submodule's own
    refspecs, run the repository's reference-transaction hooks, or execute a configured
    remote.<remote>.uploadpack, and it wrote objects, a reflog and FETCH_HEAD there even on a dry
    run). The fetch runs in a PRIVATE bare repository this run creates inside run_dir: initialized
    empty (so no local configuration is inherited), hooks disabled before any ref is written, the
    author's object store attached as a read-only alternate (the same --shared trust tier the
    scratch checkouts already use, and seeded as negotiation refs so the fetch stays incremental),
    and the fetch passes --no-tags, --no-recurse-submodules and --refmap= explicitly, BY URL
    (never by remote name, so no author-local transport or uploadpack setting applies), into
    refs/merge-train/base, a ref that cannot be symbolic in a fresh repository. Every author-side
    read here goes through _git_author (GIT_NO_LAZY_FETCH, protocol.allow=never, fsmonitor and
    hooks off), so a missing promised object fails the read closed instead of lazy-fetching into
    the author's store; the private fetch itself carries _ALTERNATE_FETCH_ENV and
    _ALTERNATE_FETCH_CONFIG; of those, only the environment variable reaches the alternate-ref
    read git spawns IN the author's repository (the -c pairs are scrubbed from that child's
    environment; see _ALTERNATE_FETCH_CONFIG). Author-side footprint of this whole resolution:
    reads only; no ref, no object
    content, no reflog, no FETCH_HEAD, no hook and no configured command there. Returns
    (base_repo, remote_url, base_sha)."""
    remote_url = _git_author_text(root, "remote", "get-url", remote)
    base_repo = os.path.join(run_dir, "base.git")
    _git(run_dir, "init", "-q", "--bare", base_repo)
    _git(base_repo, "config", "core.hooksPath", os.devnull)
    author_objects = _git_author_text(root, "rev-parse", "--path-format=absolute", "--git-path",
                                      "objects")
    with open(os.path.join(base_repo, "objects", "info", "alternates"), "w",
              encoding="utf-8") as handle:
        handle.write(author_objects + "\n")
    for index, have in enumerate(("refs/remotes/%s/%s" % (remote, base),
                                  "refs/heads/%s" % base)):
        code, out, _err = _git_author(root, "rev-parse", "--verify", "--quiet",
                                      have + "^{commit}", ok=None)
        if code == 0:
            _git(base_repo, "update-ref", "refs/merge-train/have-%d" % index,
                 out.decode("utf-8", "replace").strip())
    _git(base_repo, *_ALTERNATE_FETCH_CONFIG, "fetch", "--no-tags", "--no-recurse-submodules",
         "--refmap=", "--", remote_url, "+refs/heads/%s:refs/merge-train/base" % base,
         extra_env=dict(_ALTERNATE_FETCH_ENV), timeout=GIT_TIMEOUT)
    base_sha = _git_text(base_repo, "rev-parse", "--verify", "refs/merge-train/base^0")
    return base_repo, remote_url, base_sha


def run_train(root, apply=False, only=None):
    """The whole run under the global lock. Returns (exit code, reports, fatal message or None)."""
    reports = []
    try:
        top = _git_author_text(root, "rev-parse", "--show-toplevel")
        common = _git_author_text(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except Refuse as exc:
        return 2, reports, "not a git repository: %s" % exc.reason
    with open(os.path.join(common, LOCK_NAME), "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 2, reports, "another merge train holds %s" % LOCK_NAME

        def _to_exception(signum, _frame):
            raise _Signalled(signum)

        previous = dict()
        try:
            # Every UNWIND_SIGNALS member except SIGINT becomes _Signalled (SIGINT already
            # unwinds as KeyboardInterrupt under Python's default handler), so the per-PR finally
            # removes the private scratch checkout and the interrupted subprocess call kills and
            # reaps its child's whole process group; an inherited SIG_IGN disposition (nohup) is
            # respected and left in place. A group-wide SIGKILL from a supervisor cannot be
            # handled and leaves children running in their own sessions (see _run_child).
            for signum in sorted(UNWIND_SIGNALS - {signal.SIGINT}):
                if signal.getsignal(signum) != signal.SIG_IGN:
                    previous[signum] = signal.signal(signum, _to_exception)
            return _run_locked(Path(top), apply, only or set(), reports)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def _run_locked(root, apply, only, reports):
    # The config lives on the base tip, so v1 resolves the conventional origin/main once, pins M,
    # and requires the config to name that same remote and base. The base fetch runs in the run's
    # PRIVATE base repository (see _base_fetch), never in the author's repository: the author's
    # repository receives NO ref, object content, reflog, FETCH_HEAD or hook execution from this
    # run; its only author-side writes anywhere in the tool are the lock file, the retry marker
    # and the disclosed mtime refresh of an already-present object (see the module docstring).
    remote, base = "origin", "main"
    run_dir = tempfile.mkdtemp(prefix="merge-train-base-")
    SCRATCHES.append(run_dir)
    try:
        return _run_locked_with_base(root, run_dir, remote, base, apply, only, reports)
    finally:
        _cleanup(lambda: shutil.rmtree(run_dir, ignore_errors=True))


def _run_locked_with_base(root, run_dir, remote, base, apply, only, reports):
    try:
        base_repo, remote_url, base_sha = _base_fetch(root, run_dir, remote, base)
    except Refuse as exc:
        return 2, reports, "fetch failed: %s" % exc.reason
    try:
        cfg = load_config(base_repo, base_sha)
        if cfg["remote"] != remote or cfg["base"] != base:
            raise FailClosed("v1 supports remote 'origin' and base 'main' only")
        limits = cfg.get("limits", dict())
        net_timeout = limits.get("network_timeout_seconds", 120)
        cmd_timeout = limits.get("command_timeout_seconds", 900)
        prs = _gh_json(["gh", "pr", "list", "--state", "open", "--base", base, "--json",
                        "number,headRefName,headRefOid,isCrossRepository,baseRefName",
                        "--limit", str(PR_LIST_LIMIT)], root, net_timeout)
        if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
            raise FailClosed("gh pr list did not return a list of objects")
        if len(prs) >= PR_LIST_LIMIT:
            raise FailClosed("gh returned %d PRs; the list may be truncated" % len(prs))
        for pr in prs:
            if (type(pr.get("number")) is not int or not isinstance(pr.get("headRefName"), str)
                    or not isinstance(pr.get("headRefOid"), str)):
                raise FailClosed("gh pr list row malformed: %r" % (pr,))
        trees = _worktrees(root)
    except FailClosed as exc:
        return 2, reports, str(exc)
    except Refuse as exc:
        return 2, reports, "%s: %s" % (exc.status, exc.reason)
    names = dict()
    for pr in prs:
        names.setdefault(pr["headRefName"], []).append(pr["number"])
    generated = list(cfg["generated"]["paths"])
    rc = 0
    for pr in sorted(prs, key=lambda p: p["number"]):
        if only and pr["number"] not in only:
            continue
        branch = pr["headRefName"]
        report = dict(schema=SCHEMA, pr=pr["number"], branch=branch, worktree=None, base=base_sha,
                      old_head=None, new_head=None, result=None, reason="", regenerated=[],
                      hand_resolution=False, review_carry=None)
        ctx = dict(old=None, review_carry=None, regenerated=[])
        try:
            _guard("branch-name")(root, branch)
            if len(names[branch]) > 1:
                raise Refuse("ambiguous-pr", "PRs %s share the branch" % names[branch])
            if pr.get("isCrossRepository") is not False:
                raise Refuse("fork-pr", "cross-repository PRs are not supported")
            if pr.get("baseRefName") != base:
                raise Refuse("base-mismatch", "PR base %r" % pr.get("baseRefName"))
            matches = [t for t in trees if t["branch"] == branch]
            if not matches:
                raise Refuse("no-worktree", "no worktree has %s checked out" % branch)
            if len(matches) > 1:
                raise Refuse("ambiguous-worktree", ", ".join(t["path"] for t in matches))
            wt = matches[0]["path"]
            report["worktree"] = wt
            ctx.update(root=root, wt=wt, pr=pr["number"], branch=branch, head_oid=pr["headRefOid"],
                       remote=remote, remote_url=remote_url, base=base, base_sha=base_sha, cfg=cfg,
                       base_repo=base_repo,
                       generated=generated, cmd_timeout=cmd_timeout, net_timeout=net_timeout,
                       git_dir=_git_author_text(wt, "rev-parse", "--path-format=absolute",
                                                "--git-dir"))
            report.update(_process_pr(ctx, apply))
        except Refuse as refusal:
            report["result"], report["reason"] = refusal.status, refusal.reason
            report["old_head"] = ctx["old"]
        report["review_carry"] = ctx["review_carry"]
        report["regenerated"] = ctx["regenerated"]
        if report["result"] not in OK_RESULTS:
            rc = 1
        reports.append(report)
    return rc, reports, None


def _parse_args(argv):
    """The live-run options, or None for a usage error (--dry-run and --apply together included)."""
    opts = dict(apply=False, repo=".", prs=[])
    dry_run = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--apply":
            opts["apply"] = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--repo" and i + 1 < len(argv):
            i += 1
            opts["repo"] = argv[i]
        elif (arg == "--pr" and i + 1 < len(argv) and argv[i + 1].isascii()
                and argv[i + 1].isdigit() and int(argv[i + 1])):
            i += 1
            opts["prs"].append(int(argv[i]))
        else:
            return None
        i += 1
    if dry_run and opts["apply"]:
        return None
    return opts


def _mode(argv):
    """("self-test", kwargs) for the self-test forms, no arguments included (never a live run);
    ("live", opts) for a live run, which needs an explicit option; ("usage", None) otherwise."""
    if argv in ([], ["--self-test"], ["--self-test", "--red-on-revert"]):
        return "self-test", dict(red_on_revert="--red-on-revert" in argv)
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return "self-test", dict(report_path=argv[1])
    opts = _parse_args(argv)
    return ("usage", None) if opts is None else ("live", opts)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode, opts = _mode(argv)
    if mode == "self-test":
        return self_test(**opts)
    if mode == "usage":
        print("usage: merge_train.py --dry-run [--repo DIR] [--pr N]... | [--repo DIR] [--pr N]... "
              "--apply | [--self-test [--red-on-revert]] | --execution-report ABS_PATH",
              file=sys.stderr)
        return 2
    try:
        rc, reports, fatal = run_train(opts["repo"], opts["apply"], set(opts["prs"]))
    except _Signalled as sig:
        signal.signal(sig.signum, signal.SIG_DFL)
        os.kill(os.getpid(), sig.signum)
        return 128 + sig.signum
    for report in reports:
        print(json.dumps(report, sort_keys=True))
        print("%s PR #%d %s: %s%s" % (_now(), report["pr"], report["branch"], report["result"],
                                      " (%s)" % report["reason"] if report["reason"] else ""),
              file=sys.stderr)
    if fatal:
        print("%s merge-train: %s" % (_now(), fatal), file=sys.stderr)
    return rc


SUITE_ID = "merge-train-selftest"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
_RECORDER = dict(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES)

TOY_GEN = r"""import hashlib, os, sys
root = os.getcwd()
lines = []
for dirpath, dirnames, filenames in os.walk(os.path.join(root, "src")):
    dirnames.sort()
    for name in sorted(filenames):
        path = os.path.join(dirpath, name)
        with open(path, "rb") as handle:
            lines.append("%s %s" % (hashlib.sha256(handle.read()).hexdigest(),
                                    os.path.relpath(path, root)))
text = "\n".join(sorted(lines)) + "\n"
target = os.path.join(root, "gen", "digest.txt")
if "--check" in sys.argv:
    with open(target) as handle:
        sys.exit(0 if handle.read() == text else 1)
if os.environ.get("TOY_FAIL"):
    sys.exit(3)
if os.environ.get("TOY_IGNORED"):
    with open(os.path.join(root, "ignored", "valuable"), "w") as handle:
        handle.write("LOST\n")
    sys.exit(3)
if os.environ.get("TOY_LITTER"):
    with open(os.path.join(root, "litter.txt"), "w") as handle:
        handle.write("x\n")
if os.environ.get("TOY_DRIFT"):
    with open(os.path.join(root, ".gitignore"), "a") as handle:
        handle.write("# drift\n")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w") as handle:
    handle.write(text)
"""

FAKE_GH = r"""import json, os, subprocess, sys
state_path = os.environ["FAKE_GH_STATE"]
with open(state_path) as handle:
    state = json.load(handle)
args = sys.argv[1:]
if state.get("fail"):
    sys.exit(4)
if args[:2] == ["pr", "list"]:
    print(json.dumps(state["prs"]))
elif args[:2] == ["pr", "view"]:
    number = args[2]
    calls = state.setdefault("view_calls", dict())
    calls[number] = calls.get(number, 0) + 1
    with open(state_path, "w") as handle:
        json.dump(state, handle)
    pr = [p for p in state["prs"] if str(p["number"]) == number][0]
    view = dict(headRefName=pr["headRefName"], headRefOid=pr["headRefOid"], state="OPEN")
    if number in state.get("closed", []):
        view["state"] = "CLOSED"
    rename_after = state.get("rename_after", dict()).get(number)
    if rename_after is not None and calls[number] > rename_after:
        view["headRefName"] = "renamed/branch"
    move = state.get("move_on_view", dict()).get(number)
    if move is not None and calls[number] == move["call"]:
        subprocess.run(["git", "--git-dir=" + move["remote"], "update-ref", move["ref"], move["new"],
                        move["old"]], check=True, timeout=60)
    race = state.get("commit_on_view", dict()).get(number)
    if race is not None and calls[number] == race["call"]:
        env = dict(os.environ)
        env.update(GIT_AUTHOR_NAME="Author", GIT_AUTHOR_EMAIL="author@example.invalid",
                   GIT_COMMITTER_NAME="Author", GIT_COMMITTER_EMAIL="author@example.invalid")
        with open(os.path.join(race["worktree"], race["file"]), "w") as handle:
            handle.write("author\n")
        subprocess.run(["git", "-C", race["worktree"], "add", "--", race["file"]],
                       check=True, timeout=60, env=env)
        subprocess.run(["git", "-C", race["worktree"], "commit", "-q", "-m", "author race"],
                       check=True, timeout=60, env=env)
    print(json.dumps(view))
else:
    sys.exit(5)
"""

CONFIG_TOML = """version = 1
remote = "origin"
base = "main"
[commit]
name = "Train Maintainer"
email = "train@example.invalid"
[generated]
paths = ["gen/digest.txt"]
regenerate = [["%(py)s", "-I", "-B", "tools/gen.py"]]
check = [["%(py)s", "-I", "-B", "tools/gen.py", "--check"]]
[busy]
probe = %(probe)s
[limits]
command_timeout_seconds = 60
network_timeout_seconds = 60
"""

FIXTURE_IDENT = dict(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                     GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
NINE = "".join("l%d\n" % n for n in range(1, 10))


def check(name, got, want):
    if name in _RECORDER["executed"]:
        print("SELF-TEST HARNESS ERROR: duplicate check id %r" % name, file=sys.stderr)
        sys.exit(2)
    _RECORDER["executed"].add(name)
    _RECORDER["list"].append(name)
    if got != want:
        _RECORDER["failures"].append("%s: got %r, want %r" % (name, got, want))


ZERO = "0" * 40


def _audit_violation(args):
    """The self-test's own forbidden-operation predicate, written apart from _forbidden so that a
    weakened funnel cannot also blind the audit; the two must AGREE on every argv: no rebase,
    cherry-pick, stash, clean, gc, worktree prune or reset of ANY kind; for push, no force, delete,
    mirror, all or prune option in long, abbreviated or bundled short form (-uf, -fu), no lease
    other than the one exact-value refs/heads compare-and-set form (40- or 64-hex), and no refspec
    other than a plain name or the exact <hex>:refs/heads/<branch> form away from the base (no +
    force spec, no :delete spec, no src:dst alias). The first non-option word is the repository."""
    words = list(args)
    if words[:1] in (["rebase"], ["cherry-pick"], ["stash"], ["clean"], ["gc"], ["prune"],
                     ["reset"]):
        return True
    if words[:2] == ["worktree", "prune"]:
        return True
    if words[:1] != ["push"]:
        return False
    lease = re.compile(r"\A--force-with-lease=refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*:"
                       r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
    spec = re.compile(r"\A[0-9a-f]{40}([0-9a-f]{24})?:refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
    specs_only, repo_seen = False, False
    for w in words[1:]:
        if w == "--" and not specs_only:
            specs_only = True
            continue
        if not specs_only and w.startswith("-"):
            if not w.startswith("--"):
                if any(c in "fd" for c in w[1:]):
                    return True
                continue
            if lease.match(w):
                continue
            stem = w.split("=", 1)[0]
            for banned in ("--force", "--force-with-lease", "--force-if-includes", "--delete",
                           "--mirror", "--all", "--prune"):
                if len(stem) > 2 and (banned.startswith(stem) or stem.startswith(banned)):
                    return True
            continue
        if not repo_seen:
            repo_seen = True
            continue
        if w.startswith("+") or ":" in w:
            if not spec.match(w) or w.endswith(":refs/heads/main"):
                return True
    return False


class Fixture:
    """A bare remote whose pre-receive hook can refuse and whose post-receive hook logs every ref
    update (and, when asked, then makes the remote unreadable to the main checkout), a main checkout
    carrying the toy generator and the config, PR branches as linked worktrees of that checkout, and
    a fake gh answering from a JSON state file. Every tool run records the plan's per-case
    invariants into self.violations."""

    def __init__(self, base, probe="[]"):
        self.base = Path(base)
        self.remote = self.base / "remote.git"
        self.main = self.base / "main"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.state = self.base / "gh-state.json"
        self.push_log = self.base / "push.log"
        self.path = os.environ.get("PATH", os.defpath)
        self.prs = []
        self.runs = 0
        self.violations = []
        self.gone = self.base / "gone.git"
        gh = self.bin / "gh"
        gh.write_text("#!%s\n%s" % (sys.executable, FAKE_GH), encoding="utf-8")
        gh.chmod(0o755)
        _git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        hooks = self.remote / "hooks"
        (hooks / "post-receive").write_text(
            "#!/bin/sh\ncat >> '%s'\nif [ -e '%s' ]; then\n  rm -f '%s'\n"
            "  git config --file '%s' remote.origin.url '%s'\nfi\n" % (
                self.push_log, self.base / "unreadable-after-push",
                self.base / "unreadable-after-push", self.main / ".git" / "config", self.gone),
            encoding="utf-8")
        (hooks / "pre-receive").write_text(
            "#!/bin/sh\nif [ -e '%s' ]; then exit 1; fi\nexit 0\n" % (self.base / "reject"),
            encoding="utf-8")
        for hook in ("post-receive", "pre-receive"):
            (hooks / hook).chmod(0o755)
        _git(self.base, "init", "-q", "-b", "main", str(self.main))
        _git(self.main, "remote", "add", "origin", str(self.remote))
        self.write(self.main, "src/a.txt", NINE)
        self.write(self.main, "tools/gen.py", TOY_GEN)
        self.write(self.main, CONFIG_PATH, CONFIG_TOML % dict(py=sys.executable, probe=probe))
        self.write(self.main, ".gitignore", "ignored/\n")
        self.commit(self.main, "seed")
        _git(self.main, "push", "-q", "origin", "main")
        self.set_gh()

    def write(self, repo, rel, text):
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, repo, message):
        subprocess.run([sys.executable, "-I", "-B", "tools/gen.py"], cwd=str(repo), check=True,
                       env=_clean_env(), timeout=60)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message, extra_env=FIXTURE_IDENT)
        return _git_text(repo, "rev-parse", "HEAD")

    def add_pr(self, number, branch, files, detach=False):
        path = self.base / ("wt-%d" % number)
        _git(self.main, "worktree", "add", "-q", "-b", branch, str(path), "main")
        for rel, text in files.items():
            self.write(path, rel, text)
        head = self.commit(path, "pr %d" % number)
        _git(path, "push", "-q", "origin", "%s:refs/heads/%s" % (head, branch))
        if detach:
            _git(path, "checkout", "-q", "--detach")
        self.prs.append(dict(number=number, headRefName=branch, headRefOid=head,
                             isCrossRepository=False, baseRefName="main"))
        self.set_gh()
        return path

    def advance_main(self, files):
        for rel, text in files.items():
            self.write(self.main, rel, text)
        sha = self.commit(self.main, "main advance")
        _git(self.main, "push", "-q", "origin", "main")
        return sha

    def set_gh(self, **extra):
        state = dict(prs=self.prs)
        state.update(extra)
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def run(self, apply=False, only=None):
        """One tool run; the per-case invariants are recorded even when the run is killed."""
        os.environ["FAKE_GH_STATE"] = str(self.state)
        os.environ["PATH"] = str(self.bin) + os.pathsep + self.path
        refs = self.remote_refs()
        logged = len(self.log_rows())
        audit = len(GIT_AUDIT)
        try:
            return run_train(self.main, apply, only)
        finally:
            attempted = GIT_AUDIT[audit:]
            self.runs += 1
            self.violations += self._violations(refs, logged, attempted, apply)

    def _violations(self, refs, logged, attempted, apply):
        """Plan section 9, for one run: the remote base ref byte-identical; only refs/heads/<PR
        branch> changed, only by fast-forward and only under --apply; every logged ref update a
        fast-forward of a PR branch; no forbidden argv attempted."""
        bad = ["argv %r" % (a,) for a in attempted if _audit_violation(a)]
        after = self.remote_refs()
        if after.get("refs/heads/main") != refs.get("refs/heads/main"):
            bad.append("base ref refs/heads/main %s -> %s" % (
                refs.get("refs/heads/main"), after.get("refs/heads/main")))
        allowed = set("refs/heads/" + p["headRefName"] for p in self.prs)
        rows = self.log_rows()[logged:]
        moved_by_push = set(r[2] for r in rows)
        for ref in sorted(set(refs) | set(after)):
            old, new = refs.get(ref), after.get(ref)
            if old == new or ref not in moved_by_push:
                # An unlogged change is the fixture's own update-ref: the tool can change a ref
                # on the bare remote only through a logged receive.
                continue
            if not (apply and ref in allowed and old and new and self.fast_forward(old, new)):
                bad.append("ref %s %s -> %s" % (ref, old, new))
        for old, new, ref in rows:
            if ref not in allowed or ZERO in (old, new) or not self.fast_forward(old, new):
                bad.append("logged update %s %s -> %s" % (ref, old, new))
            elif old != refs.get(ref):
                bad.append("logged update %s moved from %s but the run started at %s (a mid-run "
                           "move was overwritten)" % (ref, old, refs.get(ref)))
        return bad

    def remote_refs(self):
        out = _git_text(self.remote, "for-each-ref", "--format=%(refname) %(objectname)")
        return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())

    def fast_forward(self, old, new):
        return _git(self.remote, "merge-base", "--is-ancestor", old, new, ok=None)[0] == 0

    def foreign_commit(self, parent, ref=None):
        """A commit made by someone else straight in the remote (parent's tree), optionally moving
        ref onto it from parent; the post-receive log does not see it."""
        sha = _git_text(self.remote, "commit-tree", parent + "^{tree}", "-p", parent, "-m",
                        "foreign", extra_env=FIXTURE_IDENT)
        if ref:
            _git(self.remote, "update-ref", ref, sha, parent)
        return sha

    def remote_ref(self, branch):
        code, out, _err = _git(self.remote, "rev-parse", "--verify", "-q",
                               "refs/heads/" + branch, ok=None)
        return out.decode().strip() if code == 0 else None

    def log_rows(self):
        if not self.push_log.exists():
            return []
        return [line.split() for line in self.push_log.read_text().splitlines() if line.strip()]

    def pushes(self):
        """Tool-made ref updates: branch creations and base advances made by the fixture are
        filtered out."""
        return [r for r in self.log_rows() if r[0] != ZERO and r[2] != "refs/heads/main"]


def _result(reports, number):
    rows = [r for r in reports if r["pr"] == number]
    return rows[0]["result"] if rows else None


def _fixture(tmp, name, probe="[]"):
    base = Path(tmp) / name
    base.mkdir()
    return Fixture(base, probe)


def _stale_pr(fx, number=1, branch="feat/x"):
    """A PR whose generated digest conflicts with the base's after the base advances."""
    wt = fx.add_pr(number, branch, dict([("src/b.txt", "bravo\n")]))
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    return wt


def _git_dir(wt):
    return _git_text(wt, "rev-parse", "--path-format=absolute", "--git-dir")


def _invariants(*fixtures):
    """The per-case invariants recorded over every tool run of the given fixtures; a fixture the
    case never ran through the tool is itself a violation."""
    bad = []
    for fx in fixtures:
        bad += ["%s: %s" % (fx.base.name, v) for v in fx.violations]
        if not fx.runs:
            bad.append("%s: no tool run observed" % fx.base.name)
    return bad


def _has_marker(wt):
    return os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME))


def case_config(_tmp):
    # Pure schema checks: no tool run, no git call and no remote, so the per-case invariants are
    # vacuous here and not asserted.
    good = dict(version=1, remote="origin", base="main",
                commit=dict(name="A", email="a@example.invalid"),
                generated=dict(paths=["gen/d.txt"], regenerate=[["x"]], check=[["y"]]))

    def verdict(mutate):
        data = json.loads(json.dumps(good))
        mutate(data)
        try:
            _guard("config-fail-closed")(data)
        except FailClosed:
            return "refused"
        return "accepted"

    check("config/valid-accepted", verdict(lambda d: None), "accepted")
    check("config/unknown-key", verdict(lambda d: d.update(extra=1)), "refused")
    check("config/bad-type", verdict(lambda d: d.update(version="1")), "refused")
    check("config/absolute-path", verdict(lambda d: d["generated"].update(paths=["/etc/x"])),
          "refused")
    check("config/dotdot-path", verdict(lambda d: d["generated"].update(paths=["a/../b"])),
          "refused")
    check("config/glob-path", verdict(lambda d: d["generated"].update(paths=["gen/*.txt"])),
          "refused")
    check("config/empty-list", verdict(lambda d: d["generated"].update(check=[])), "refused")
    check("config/duplicate-path",
          verdict(lambda d: d["generated"].update(paths=["gen/d.txt", "gen/d.txt"])), "refused")
    check("config/empty-command", verdict(lambda d: d["generated"].update(regenerate=[[]])),
          "refused")
    check("config/missing-identity", verdict(lambda d: d["commit"].pop("email")), "refused")


def case_absent(tmp):
    fx = _fixture(tmp, "absent")
    _git(fx.main, "rm", "-q", CONFIG_PATH)
    _git(fx.main, "commit", "-q", "-m", "drop config", extra_env=FIXTURE_IDENT)
    _git(fx.main, "push", "-q", "origin", "main")
    rc, reports, fatal = fx.run()
    check("config/absent-exit2", (rc, reports, "not enabled" in (fatal or "")), (2, [], True))
    fx2 = _fixture(tmp, "malformed")
    wt = _stale_pr(fx2)
    fx2.write(fx2.main, CONFIG_PATH, "version = 1\nextra = true\n")
    _git(fx2.main, "commit", "-q", "-am", "malformed config", extra_env=FIXTURE_IDENT)
    _git(fx2.main, "push", "-q", "origin", "main")
    before = _snapshot(wt)
    rc, reports, fatal = fx2.run(apply=True)
    check("config/malformed-exit2",
          (rc, reports, "malformed" in (fatal or ""), _snapshot_diff(before, _snapshot(wt))),
          (2, [], True, []))
    check("config/absent-invariants", _invariants(fx, fx2), [])


def case_frombase(tmp):
    fx = _fixture(tmp, "frombase")
    _stale_pr(fx)
    fx.write(fx.main, CONFIG_PATH, "version = 99\n")
    rc, reports, _fatal = fx.run()
    check("config/read-from-base-not-checkout", (rc, _result(reports, 1)), (0, "would-merge"))
    check("config/read-from-base-invariants", _invariants(fx), [])


def case_dry(tmp):
    fx = _fixture(tmp, "dry")
    wt = _stale_pr(fx)
    before = _snapshot(wt)
    remote_before = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run()
    check("dry/would-merge", (rc, _result(reports, 1)), (0, "would-merge"))
    check("dry/changes-nothing", (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
                                  fx.pushes()), ([], remote_before, []))
    check("dry/invariants", _invariants(fx), [])


def case_happy(tmp):
    fx = _fixture(tmp, "happy")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    base = fx.remote_ref("main")
    before = _snapshot(wt)
    del GIT_AUDIT[:]
    rc, reports, _fatal = fx.run(apply=True)
    report = reports[0] if reports else dict()
    new = fx.remote_ref("feat/x")
    check("apply/pushed", (rc, report.get("result"), report.get("new_head")), (0, "pushed", new))
    parents = _git_text(fx.remote, "rev-list", "--parents", "-n", "1", new).split()[1:]
    check("apply/parents-old-then-base", parents, [old, base])
    fields = _git_text(fx.remote, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B", new).split("|")
    check("apply/config-identity-no-ai-trailer",
          (fields[0], fields[1], "Co-Authored-By" in fields[2],
           ("Merge-train: base=" + base) in fields[2]),
          ("Train Maintainer <train@example.invalid>", "Train Maintainer <train@example.invalid>",
           False, True))
    fresh = Path(tmp) / "happy-fresh"
    _git(fx.base, "clone", "--quiet", "--no-local", str(fx.remote), str(fresh))
    _git(fresh, "checkout", "--quiet", "feat/x")
    check("apply/generated-fresh",
          subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"], cwd=str(fresh),
                         env=_clean_env(), timeout=60).returncode, 0)
    check("apply/report-fields",
          (sorted(report), report.get("schema"), report.get("regenerated"),
           report.get("review_carry"), report.get("hand_resolution"), report.get("base"),
           report.get("old_head")),
          (sorted(["schema", "pr", "branch", "worktree", "base", "old_head", "new_head", "result",
                   "reason", "regenerated", "hand_resolution", "review_carry"]),
           SCHEMA, ["gen/digest.txt"], True, False, base, old))
    check("apply/one-fast-forward-push", fx.pushes(), [[old, new, "refs/heads/feat/x"]])
    check("apply/base-ref-untouched", fx.remote_ref("main"), base)
    check("apply/argv-audit-clean", [a for a in GIT_AUDIT if _forbidden(a) or _audit_violation(a)],
          [])
    tool_pushes = [a for a in GIT_AUDIT if a[:1] == ["push"] and "--porcelain" in a]
    check("apply/push-leased-to-validated-head",
          (len(tool_pushes), all(("--force-with-lease=refs/heads/feat/x:" + old) in a
                                 for a in tool_pushes)), (1, True))
    check("apply/marker-removed", _has_marker(wt), False)
    check("apply/author-worktree-untouched",
          (_snapshot_diff(before, _snapshot(wt)), _git_text(wt, "rev-parse", "HEAD")), ([], old))
    check("apply/local-branch-not-fast-forwarded",
          _git_text(fx.main, "rev-parse", "refs/heads/feat/x"), old)
    check("apply/scratch-removed", [p for p in SCRATCHES if os.path.exists(p)], [])
    fx.prs[0]["headRefOid"] = new
    fx.set_gh()
    rc2, reports2, _fatal = fx.run(apply=True)
    check("apply/second-run-current", (rc2, _result(reports2, 1), len(fx.pushes())),
          (0, "current", 1))
    check("apply/invariants", _invariants(fx), [])


def case_carry(tmp):
    fx = _fixture(tmp, "carry")
    fx.add_pr(1, "feat/x", dict([("src/a.txt", NINE.replace("l8", "L8"))]))
    fx.advance_main(dict([("src/a.txt", NINE.replace("l2", "L2"))]))
    rc, reports, _fatal = fx.run(apply=True)
    check("carry/true-when-pr-lines-unchanged",
          (rc, _result(reports, 1), reports[0]["review_carry"] if reports else None),
          (0, "pushed", True))
    fx2 = _fixture(tmp, "carry2")
    fx2.add_pr(1, "feat/x", dict([("src/a.txt", NINE.replace("l8", "L8"))]))
    fx2.advance_main(dict([("src/a.txt", NINE.replace("l8", "L8")), ("src/d.txt", "delta\n")]))
    rc, reports, _fatal = fx2.run(apply=True)
    check("carry/false-when-pr-lines-change",
          (_result(reports, 1), reports[0]["review_carry"] if reports else None), ("pushed", False))
    check("carry/invariants", _invariants(fx, fx2), [])


def case_discover(tmp):
    fx = _fixture(tmp, "discover")
    fx.add_pr(1, "feat/one", dict([("src/one.txt", "1\n")]))
    fx.add_pr(2, "feat/none", dict([("src/none.txt", "2\n")]), detach=True)
    fx.add_pr(3, "feat/fork", dict([("src/fork.txt", "3\n")]))
    fx.add_pr(4, "feat/base", dict([("src/base.txt", "4\n")]))
    fx.add_pr(5, "feat/moved", dict([("src/moved.txt", "5\n")]))
    wt7 = fx.add_pr(7, "feat/detached", dict([("src/seven.txt", "7\n")]))
    fx.advance_main(dict([("src/c.txt", "c\n")]))
    fx.prs[2]["isCrossRepository"] = True
    fx.prs[3]["baseRefName"] = "release"
    fx.prs[4]["headRefOid"] = "0" * 40
    fx.prs.append(dict(fx.prs[0], number=6))
    fx.set_gh()
    _git(wt7, "checkout", "-q", "-b", "feat/other")
    _git(fx.main, "worktree", "add", "-q", "--detach", str(fx.base / "wt-7b"), "main")
    rc, reports, _fatal = fx.run()
    got = dict((r["pr"], r["result"]) for r in reports)
    check("discover/ambiguous-pr", (got.get(1), got.get(6)), ("ambiguous-pr", "ambiguous-pr"))
    check("discover/no-worktree-skipped", got.get(2), "no-worktree")
    check("discover/fork-pr", got.get(3), "fork-pr")
    check("discover/base-mismatch", got.get(4), "base-mismatch")
    check("discover/pr-head-moved", got.get(5), "pr-head-moved")
    check("discover/worktree-on-other-branch", got.get(7), "no-worktree")
    check("discover/refusal-exit1", rc, 1)
    fx.prs[:] = [dict(fx.prs[0], number=n, headRefName="b%d" % n) for n in range(1, 201)]
    fx.set_gh()
    rc, reports, fatal = fx.run()
    check("discover/truncated-list-exit2", (rc, reports, "truncated" in (fatal or "")),
          (2, [], True))
    fx.set_gh(fail=True)
    rc, reports, _fatal = fx.run()
    check("discover/gh-failure-exit2", (rc, reports), (2, []))
    check("discover/invariants", _invariants(fx), [])


def case_ambiguous_worktree(tmp):
    fx = _fixture(tmp, "ambiguous")
    _stale_pr(fx)
    _git(fx.main, "worktree", "add", "-q", "-f", str(fx.base / "wt-dup"), "feat/x")
    rc, reports, _fatal = fx.run()
    check("discover/ambiguous-worktree", (rc, _result(reports, 1)), (1, "ambiguous-worktree"))
    check("discover/ambiguous-worktree-invariants", _invariants(fx), [])


def case_branch(tmp):
    fx = _fixture(tmp, "branch")
    wt = _stale_pr(fx)
    fx.set_gh(rename_after=dict([("1", 0)]))
    rc, reports, _fatal = fx.run()
    check("discover/branch-mismatch", (rc, _result(reports, 1)), (1, "branch-mismatch"))
    fx.set_gh(rename_after=dict([("1", 1)]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("discover/branch-renamed-before-commit",
          (_result(reports, 1), _snapshot_diff(before, _snapshot(wt)), fx.pushes()),
          ("branch-mismatch", [], []))
    # Pin branch-match's state check alone: gh reports the same branch but the PR is CLOSED.
    fx.set_gh(closed=["1"])
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("discover/closed-pr-refused",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)), fx.pushes()),
          (1, "branch-mismatch", [], []))
    check("discover/branch-invariants", _invariants(fx), [])


def case_badname(tmp):
    # Git accepts + and @ in branch names; the lease cannot express them, so the run must refuse
    # with a named status BEFORE any work: no clone, merge, commit or push is attempted.
    fx = _fixture(tmp, "badname")
    trees = []
    for branch in ("feat/x+plus", "feat/x@at"):
        number = len(fx.prs) + 1
        path = fx.base / ("wt-%d" % number)
        _git(fx.main, "worktree", "add", "-q", "-b", branch, str(path), "main")
        fx.write(path, "src/extra-%d.txt" % number, "payload\n")
        head = fx.commit(path, "pr %d" % number)
        _git(fx.remote, "fetch", "--no-tags", str(fx.main),
             "refs/heads/%s:refs/heads/%s" % (branch, branch))
        fx.prs.append(dict(number=number, headRefName=branch, headRefOid=head,
                           isCrossRepository=False, baseRefName="main"))
        trees.append(path)
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    befores = [_snapshot(p) for p in trees]
    audit = len(GIT_AUDIT)
    rc, reports, _fatal = fx.run(apply=True)
    worked = [a for a in GIT_AUDIT[audit:] if a[:1] in (
        ["clone"], ["merge"], ["commit-tree"], ["push"], ["checkout"], ["add"], ["write-tree"])]
    check("refuse/bad-branch-name-plus", (rc, _result(reports, 1)), (1, "bad-branch-name"))
    check("refuse/bad-branch-name-at", _result(reports, 2), "bad-branch-name")
    check("refuse/bad-branch-name-before-any-work",
          (worked, fx.pushes(), [_snapshot_diff(b, _snapshot(p)) for b, p in zip(befores, trees)],
           [_has_marker(p) for p in trees]),
          ([], [], [[], []], [False, False]))
    # Pin the check-ref-format line alone: feat/x.lock satisfies the lease character set but git
    # refuses it, so only the check-ref-format call can catch it.
    try:
        _guard("branch-name")(fx.main, "feat/x.lock")
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/bad-branch-name-check-ref-format", got, "bad-branch-name")
    check("refuse/bad-branch-name-invariants", _invariants(fx), [])


def case_audit(_tmp):
    # Pure predicate checks over forbidden and allowed push argv forms: no tool run, no git call.
    # Both predicates must refuse every forbidden form and accept the one lease shape, and they
    # must AGREE on every probe.
    def verdict(args):
        return (_forbidden(args), _audit_violation(args))

    both = (True, True)
    neither = (False, False)
    l40 = "--force-with-lease=refs/heads/feat/x:" + "6" * 40
    l64 = "--force-with-lease=refs/heads/feat/x:" + "6" * 64
    probes = []

    def probed(args):
        probes.append(list(args))
        return verdict(args)

    check("audit/push-f", probed(["push", "-f", "origin", "x"]), both)
    check("audit/push-bundled-uf", probed(["push", "-uf", "origin", "x"]), both)
    check("audit/push-bundled-fu", probed(["push", "-fu", "origin", "x"]), both)
    check("audit/push-d", probed(["push", "-d", "origin", "x"]), both)
    check("audit/push-delete", probed(["push", "--delete", "origin", "x"]), both)
    check("audit/push-delete-abbrev", probed(["push", "--dele", "origin", "x"]), both)
    check("audit/push-mirror", probed(["push", "--mirror", "origin"]), both)
    check("audit/push-mirror-abbrev", probed(["push", "--mirr", "origin"]), both)
    check("audit/push-all", probed(["push", "--all", "origin"]), both)
    check("audit/push-prune", probed(["push", "--prune", "origin", "x"]), both)
    check("audit/push-force-bare", probed(["push", "--force", "origin", "x"]), both)
    check("audit/push-force-abbrev", probed(["push", "--forc", "origin", "x"]), both)
    check("audit/push-force-if-includes",
          probed(["push", "--force-if-includes", l40, "origin", "x"]), both)
    check("audit/push-lease-bare", probed(["push", "--force-with-lease", "origin", "x"]), both)
    check("audit/push-lease-short-sha",
          probed(["push", "--force-with-lease=refs/heads/feat/x:6666", "origin", "x"]), both)
    check("audit/push-lease-plus-branch",
          probed(["push", "--force-with-lease=refs/heads/feat/x+y:" + "6" * 40, "origin", "x"]),
          both)
    check("audit/push-plus-refspec",
          probed(["push", "--", "origin", "+feat/x:refs/heads/feat/x"]), both)
    check("audit/push-colon-refspec", probed(["push", "--", "origin", ":refs/heads/feat/x"]), both)
    check("audit/push-src-other-refspec",
          probed(["push", "--", "origin", "feat/x:refs/heads/other"]), both)
    check("audit/push-to-base-refspec",
          probed(["push", "--", "origin", ("6" * 40) + ":refs/heads/main"]), both)
    check("audit/reset-refused", probed(["reset", "--keep", "6" * 40]), both)
    check("audit/lease-40-allowed",
          probed(["push", "--porcelain", "--no-follow-tags", l40, "--", "origin",
                  ("6" * 40) + ":refs/heads/feat/x"]), neither)
    check("audit/lease-64-allowed",
          probed(["push", "--porcelain", "--no-follow-tags", l64, "--", "origin",
                  ("6" * 64) + ":refs/heads/feat/x"]), neither)
    check("audit/predicates-agree",
          [a for a in probes if _forbidden(a) != _audit_violation(a)], [])


def case_conflict(tmp):
    fx = _fixture(tmp, "conflict")
    wt = fx.add_pr(1, "feat/x", dict([("src/a.txt", "pr side\n")]))
    fx.advance_main(dict([("src/a.txt", "main side\n")]))
    before = _snapshot(wt)
    old = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/source-conflict",
          (rc, _result(reports, 1), "src/a.txt" in (reports[0]["reason"] if reports else "")),
          (1, "source-conflict", True))
    check("refuse/source-conflict-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes(),
           os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME))),
          ([], old, [], False))
    check("refuse/source-conflict-invariants", _invariants(fx), [])


def _refusal_run(tmp, name, env_key=None, failing_check=False, seen=None):
    """A stale-generated PR run with --apply under one injected generator fault; returns
    (exit code, result, reason, (snapshot differences, tool pushes)). The fixture is appended to
    seen for the invariant check."""
    fx = _fixture(tmp, name)
    if seen is not None:
        seen.append(fx)
    wt = _stale_pr(fx)
    if failing_check:
        text = _read_text(fx.main / CONFIG_PATH).replace(
            '"tools/gen.py", "--check"', '"-c", "import sys; sys.exit(1)"')
        fx.write(fx.main, CONFIG_PATH, text)
        _git(fx.main, "commit", "-q", "-am", "failing check", extra_env=FIXTURE_IDENT)
        _git(fx.main, "push", "-q", "origin", "main")
    before = _snapshot(wt)
    if env_key:
        os.environ[env_key] = "1"
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        os.environ.pop(env_key or "TOY_UNSET", None)
    reason = reports[0]["reason"] if reports else ""
    return rc, _result(reports, 1), reason, (_snapshot_diff(before, _snapshot(wt)), fx.pushes())


def case_regen(tmp):
    # Every generator fault happens in the PRIVATE checkout: the refusal is exit 1 and the author's
    # worktree is untouched by construction; there is no restore-failed state left to reach.
    seen = []
    rc, result, _reason, untouched = _refusal_run(tmp, "regen-fail", "TOY_FAIL", seen=seen)
    check("refuse/regenerate-failed", (rc, result, untouched), (1, "regenerate-failed", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "check-fail", failing_check=True, seen=seen)
    check("refuse/check-failed", (rc, result, untouched), (1, "check-failed", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "not-fixpoint", "TOY_DRIFT", seen=seen)
    check("refuse/regenerate-not-fixpoint", (rc, result, untouched),
          (1, "regenerate-not-fixpoint", ([], [])))
    rc, result, _reason, untouched = _refusal_run(tmp, "litter", "TOY_LITTER", seen=seen)
    check("refuse/undeclared-write", (rc, result, untouched), (1, "undeclared-write", ([], [])))
    check("refuse/scratch-removed", [p for p in SCRATCHES if os.path.exists(p)], [])
    check("refuse/regenerate-invariants", _invariants(*seen), [])


def case_reject(tmp):
    fx = _fixture(tmp, "reject")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    (fx.base / "reject").write_text("", encoding="utf-8")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("push/rejected-restored",
          (rc, _result(reports, 1), _git_text(wt, "rev-parse", "HEAD"), fx.remote_ref("feat/x"),
           _snapshot_diff(before, _snapshot(wt))),
          (1, "push-rejected", old, old, []))
    check("push/rejected-invariants", _invariants(fx), [])


def _killed_run(fx, replacement):
    global _push_and_observe
    real = _push_and_observe
    _push_and_observe = replacement
    try:
        fx.run(apply=True)
    except KeyboardInterrupt:
        pass
    finally:
        _push_and_observe = real


def case_idem(tmp):
    def kill(*_args):
        raise KeyboardInterrupt("simulated kill after the commit")

    def push_then_kill(ctx, scratch, _old, new):
        _git(scratch, "push", "-q", "--", ctx["remote_url"],
             "%s:refs/heads/%s" % (new, ctx["branch"]))
        raise KeyboardInterrupt("simulated kill after the push")

    def kill_in_merge(*_args):
        raise KeyboardInterrupt("simulated kill mid-merge")

    seen = []
    fx = _fixture(tmp, "kill-commit")
    seen.append(fx)
    _stale_pr(fx)
    _killed_run(fx, kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-commit-one-push", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "pushed", 1))
    fx = _fixture(tmp, "kill-push")
    seen.append(fx)
    _stale_pr(fx)
    _killed_run(fx, push_then_kill)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-after-push-already-pushed", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "already-pushed", 1))
    fx = _fixture(tmp, "kill-merge")
    seen.append(fx)
    _stale_pr(fx)
    saved = GUARDS["fixpoint"]
    GUARDS["fixpoint"] = kill_in_merge
    try:
        fx.run(apply=True)
    except KeyboardInterrupt:
        pass
    finally:
        GUARDS["fixpoint"] = saved
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/kill-mid-merge-restarts", (rc, _result(reports, 1), len(fx.pushes())),
          (0, "pushed", 1))
    # An author's own in-progress merge lives in the author's worktree, which the tool never
    # touches or even inspects for state: the run proceeds in its private checkout and the
    # author's half-done merge survives byte-identically.
    fx = _fixture(tmp, "author-merge")
    seen.append(fx)
    wt = _stale_pr(fx)
    _git(wt, "merge", "--no-commit", "--no-ff", fx.remote_ref("main"), ok=None,
         extra_env=FIXTURE_IDENT)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/author-merge-in-progress-ignored",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt))), (0, "pushed", []))
    check("idem/invariants", _invariants(*seen), [])


def _reason(reports, number):
    rows = [r for r in reports if r["pr"] == number]
    return rows[0]["reason"] if rows else ""


def case_remote_changed(tmp):
    fx = _fixture(tmp, "remote-changed")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    other = fx.foreign_commit(old)
    # The third gh view (the re-check after the commit, just before the push) moves the PR branch
    # forward as a concurrent pusher would, so the exact-value lease no longer holds and the
    # remote refuses the push.
    fx.set_gh(move_on_view=dict([("1", dict(call=2, remote=str(fx.remote), ref="refs/heads/feat/x",
                                            old=old, new=other))]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("push/remote-changed", (rc, _result(reports, 1), other in _reason(reports, 1)),
          (1, "remote-changed", True))
    check("push/remote-changed-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes() == pushed,
           _has_marker(wt)), ([], other, True, False))
    check("push/remote-changed-invariants", _invariants(fx), [])


def case_push_unknown(tmp):
    fx = _fixture(tmp, "push-unknown")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    # The post-receive hook points the checkout's origin at a missing path once the push has
    # landed, so the observer's ls-remote cannot read the remote: indeterminate, marker kept,
    # nothing changed anywhere.
    (fx.base / "unreadable-after-push").write_text("", encoding="utf-8")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/unknown-keeps-marker-changes-nothing",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)),
           marker.get("new") == fx.remote_ref("feat/x"), marker.get("old"), len(fx.pushes())),
          (1, "push-unknown", [], True, old, 1))
    _git(fx.main, "config", "remote.origin.url", str(fx.remote))
    rc, reports, _fatal = fx.run(apply=True)
    check("push/unknown-rerun-already-pushed",
          (rc, _result(reports, 1), _has_marker(wt), len(fx.pushes())),
          (0, "already-pushed", False, 1))
    # Pin reconcile's unreadable-remote branch alone: a marker is present and the reconcile
    # ls-remote times out; the run must refuse push-unknown and keep the marker bytes.
    fx2 = _fixture(tmp, "push-unknown-reconcile")
    wt2 = _stale_pr(fx2)
    old2 = fx2.remote_ref("feat/x")
    git_dir2 = _git_dir(wt2)
    _write_marker(git_dir2, dict(pr=1, branch="feat/x", old=old2, new="f" * 40))
    marker_path = Path(git_dir2) / MARKER_NAME
    marker_bytes = marker_path.read_bytes()
    global _run_child
    real_child = _run_child

    def no_ls_remote(argv, **kwargs):
        if "ls-remote" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return real_child(argv, **kwargs)

    _run_child = no_ls_remote
    try:
        rc, reports, _fatal = fx2.run(apply=True)
    finally:
        _run_child = real_child
    check("push/unknown-remote-still-unreadable",
          (rc, _result(reports, 1), marker_path.exists(),
           marker_path.read_bytes() == marker_bytes),
          (1, "push-unknown", True, True))
    check("push/unknown-invariants", _invariants(fx, fx2), [])


def case_generated_delete(tmp):
    fx = _fixture(tmp, "generated-delete")
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    _git(wt, "rm", "-q", "--", "gen/digest.txt")
    _git(wt, "commit", "-q", "-m", "pr drops the generated file", extra_env=FIXTURE_IDENT)
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % _git_text(wt, "rev-parse", "HEAD"))
    fx.prs[0]["headRefOid"] = old = _git_text(wt, "rev-parse", "HEAD")
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/generated-delete-conflict",
          (rc, _result(reports, 1), _reason(reports, 1)), (1, "generated-delete-conflict",
                                                           "gen/digest.txt"))
    check("refuse/generated-delete-conflict-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes() == pushed,
           _has_marker(wt)), ([], old, True, False))
    check("refuse/generated-delete-conflict-invariants", _invariants(fx), [])


def case_author(tmp):
    # The core promise of D-390-PRIVATE-WORKTREE: an author commit, a staged edit, an unstaged
    # edit, an untracked file and an ignored file in the author's worktree survive a pushing run
    # byte-identically (snapshot: worktree, index, refs, ignored contents), present through EVERY
    # phase because they exist before the run starts; the local branch is never fast-forwarded.
    fx = _fixture(tmp, "author")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    fx.write(wt, "ignored/valuable", "KEEP\n")
    fx.write(wt, "untracked.txt", "author untracked\n")
    fx.write(wt, "staged.txt", "author staged\n")
    _git(wt, "add", "--", "staged.txt")
    fx.write(wt, "src/b.txt", "author unstaged edit\n")
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    check("author/dirty-worktree-untouched-and-pushed",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)), new != old),
          (0, "pushed", [], True))
    check("author/local-branch-not-advanced",
          (_git_text(wt, "rev-parse", "HEAD"),
           _git_text(fx.main, "rev-parse", "refs/heads/feat/x")), (old, old))
    # An author commit injected DURING the run, at the start-of-run gh view and at the pre-push gh
    # view: the run still pushes, and the commit stays the local branch tip, never rewound.
    fx2 = _fixture(tmp, "author-start")
    wt2 = _stale_pr(fx2)
    fx2.set_gh(commit_on_view=dict([("1", dict(call=1, worktree=str(wt2),
                                               file="author-start.txt"))]))
    rc, reports, _fatal = fx2.run(apply=True)
    check("author/mid-run-commit-at-start-kept",
          (rc, _result(reports, 1), _git_text(wt2, "log", "-1", "--format=%s"),
           os.path.exists(os.path.join(str(wt2), "author-start.txt"))),
          (0, "pushed", "author race", True))
    fx3 = _fixture(tmp, "author-prepush")
    wt3 = _stale_pr(fx3)
    fx3.set_gh(commit_on_view=dict([("1", dict(call=2, worktree=str(wt3),
                                               file="author-prepush.txt"))]))
    rc, reports, _fatal = fx3.run(apply=True)
    check("author/mid-run-commit-pre-push-kept",
          (rc, _result(reports, 1), _git_text(wt3, "log", "-1", "--format=%s"),
           os.path.exists(os.path.join(str(wt3), "author-prepush.txt"))),
          (0, "pushed", "author race", True))
    check("author/invariants", _invariants(fx, fx2, fx3), [])


def case_observer(tmp):
    # The observer's ls-remote times out AFTER the push was delivered: indeterminate, the marker is
    # kept, nothing local changes, and the rerun reconciles to already-pushed.
    fx = _fixture(tmp, "observer")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    before = _snapshot(wt)
    global _run_child
    real_child = _run_child
    state = dict(pushed=False)

    def hostile_run(argv, **kwargs):
        if "push" in argv and "--porcelain" in argv:
            state["pushed"] = True
            return real_child(argv, **kwargs)
        if state["pushed"] and "ls-remote" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return real_child(argv, **kwargs)

    _run_child = hostile_run
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        _run_child = real_child
    new = fx.remote_ref("feat/x")
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/observer-timeout-indeterminate",
          (rc, _result(reports, 1), new != old, marker.get("new") == new,
           _snapshot_diff(before, _snapshot(wt)), _git_text(wt, "rev-parse", "HEAD")),
          (1, "push-unknown", True, True, [], old))
    rc, reports, _fatal = fx.run(apply=True)
    check("push/observer-timeout-rerun-already-pushed",
          (rc, _result(reports, 1), _has_marker(wt), len(fx.pushes())),
          (0, "already-pushed", False, 1))
    check("push/observer-timeout-invariants", _invariants(fx), [])


def case_rewind(tmp):
    # The author force-rewinds the PR branch between the last re-check and the push: the
    # exact-value lease makes the remote refuse, and the rewind is preserved, never overwritten.
    fx = _fixture(tmp, "rewind")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    parent = _git_text(wt, "rev-parse", old + "^")
    fx.set_gh(move_on_view=dict([("1", dict(call=2, remote=str(fx.remote),
                                            ref="refs/heads/feat/x", old=old, new=parent))]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("push/rewound-branch-refused",
          (rc, _result(reports, 1), fx.remote_ref("feat/x"),
           _snapshot_diff(before, _snapshot(wt)), _has_marker(wt)),
          (1, "remote-changed", parent, [], False))
    # Pin the FETCH_HEAD^0 re-check alone: the remote head moves AFTER the ls-remote agreement but
    # BEFORE the private checkout's fetch, which then delivers the new head; the run must refuse
    # pr-head-moved instead of validating a head the remote no longer carries.
    global _scratch_checkout
    fx2 = _fixture(tmp, "rewind-fetch")
    wt2 = _stale_pr(fx2)
    old2 = fx2.remote_ref("feat/x")
    other2 = fx2.foreign_commit(old2)
    real_scratch = _scratch_checkout

    def moving_scratch(ctx, scratch_root):
        _git(fx2.remote, "update-ref", "refs/heads/feat/x", other2, old2)
        return real_scratch(ctx, scratch_root)

    _scratch_checkout = moving_scratch
    try:
        rc, reports, _fatal = fx2.run(apply=True)
    finally:
        _scratch_checkout = real_scratch
    check("push/moved-during-fetch-refused",
          (rc, _result(reports, 1), "during the fetch" in _reason(reports, 1),
           fx2.remote_ref("feat/x"), _has_marker(wt2)),
          (1, "pr-head-moved", True, other2, False))
    check("push/rewound-invariants", _invariants(fx, fx2), [])


def case_timeout(tmp):
    # A push that times out AFTER delivery is indeterminate: the observer reads the remote and
    # decides, so the delivered push is reported pushed and nothing is rolled back blind.
    fx = _fixture(tmp, "timeout")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    global _run_child
    real_child = _run_child

    def timeout_run(argv, **kwargs):
        proc = real_child(argv, **kwargs)
        if "push" in argv and "--porcelain" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return proc

    _run_child = timeout_run
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        _run_child = real_child
    new = fx.remote_ref("feat/x")
    check("push/timeout-observed-delivered",
          (rc, _result(reports, 1), new != old, _git_text(wt, "rev-parse", "HEAD") == old,
           _has_marker(wt)), (0, "pushed", True, True, False))
    # QA round 5 (claude minor 6): a plain command timeout (regenerate here, and check commands
    # are the same path) ends the WHOLE process group, not only the direct child: a grandchild of
    # the timed-out regenerate must be gone when the run reports the refusal. Only a timed-out
    # PUSH keeps the direct-child-only behaviour (see _run_child); that path is exercised above.
    fx2 = _fixture(tmp, "timeout-regen")
    _stale_pr(fx2)
    pid_file = Path(tmp) / "timeout-regen-pids"
    text = _read_text(fx2.main / CONFIG_PATH)
    plain = 'regenerate = [["%s", "-I", "-B", "tools/gen.py"]]' % sys.executable
    sleeper = ('regenerate = [["%s", "-I", "-B", "tools/gen.py"], ["%s", "-c", "import os,'
               " subprocess, sys, time; gc = subprocess.Popen([sys.executable, '-c',"
               " 'import time; time.sleep(300)']);"
               " open(os.environ['TIMEOUT_PID_FILE'],'w').write(str(os.getpid()) + ' ' +"
               ' str(gc.pid)); time.sleep(300)"]]') % (sys.executable, sys.executable)
    text = text.replace(plain, sleeper).replace("command_timeout_seconds = 60",
                                                "command_timeout_seconds = 3")
    fx2.write(fx2.main, CONFIG_PATH, text)
    _git(fx2.main, "commit", "-q", "-am", "sleeping regenerate", extra_env=FIXTURE_IDENT)
    _git(fx2.main, "push", "-q", "origin", "main")
    os.environ["TIMEOUT_PID_FILE"] = str(pid_file)
    try:
        rc2, reports2, _fatal = fx2.run(apply=True)
    finally:
        os.environ.pop("TIMEOUT_PID_FILE", None)
    tokens = _read_text(pid_file).split()
    dead = []
    for pid_text in tokens:
        for _ in range(100):
            try:
                os.kill(int(pid_text), 0)
            except OSError:
                dead.append(True)
                break
            time.sleep(0.1)
        else:
            dead.append(False)
            try:
                os.kill(int(pid_text), signal.SIGKILL)
            except OSError:
                pass
    check("timeout/regenerate-group-ended",
          (rc2, _result(reports2, 1), len(tokens), dead),
          (1, "regenerate-failed", 2, [True, True]))
    check("push/timeout-invariants", _invariants(fx, fx2), [])


def case_symlink(tmp):
    # A declared generated path that is a symlink would let the regenerator write outside the
    # declared set: refused before any regenerate command runs.
    fx = _fixture(tmp, "symlink")
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    outside = Path(tmp) / "outside-payload.txt"
    outside.write_text("KEEP\n", encoding="utf-8")
    os.unlink(os.path.join(str(wt), "gen", "digest.txt"))
    os.symlink(str(outside), os.path.join(str(wt), "gen", "digest.txt"))
    _git(wt, "add", "-A", "--", "gen/digest.txt")
    _git(wt, "commit", "-q", "-m", "digest becomes a symlink", extra_env=FIXTURE_IDENT)
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % _git_text(wt, "rev-parse", "HEAD"))
    fx.prs[0]["headRefOid"] = old = _git_text(wt, "rev-parse", "HEAD")
    fx.set_gh()
    fx.advance_main(dict([("docs/note.txt", "note\n")]))
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/generated-symlink",
          (rc, _result(reports, 1), outside.read_text(encoding="utf-8"),
           _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)),
          (1, "generated-symlink", "KEEP\n", [], old, True, False))
    probe = Path(tmp) / "symlink-dir"
    (probe / "real").mkdir(parents=True)
    os.symlink(str(probe / "real"), str(probe / "gen"))
    (probe / "real" / "digest.txt").write_text("x\n", encoding="utf-8")
    try:
        _guard("generated-confined")(probe, ["gen/digest.txt"])
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/generated-symlink-parent", got, "generated-symlink")
    # The realpath branch is TOCTOU defence in depth behind the per-component islink loop: pin it
    # alone by simulating the race (islink reports False while the path still resolves outside).
    probe2 = Path(tmp) / "symlink-real"
    (probe2 / "real").mkdir(parents=True)
    outside2 = Path(tmp) / "symlink-outside"
    outside2.mkdir()
    os.symlink(str(outside2), str(probe2 / "gen"))
    real_islink = os.path.islink
    os.path.islink = lambda _path: False
    try:
        _guard("generated-confined")(probe2, ["gen/digest.txt"])
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    finally:
        os.path.islink = real_islink
    check("refuse/generated-symlink-realpath", got, "generated-symlink")
    check("refuse/generated-symlink-invariants", _invariants(fx), [])


def case_tamper(tmp):
    # Author-side hooks never run: the merge, checks and commit happen in the private checkout
    # (hooks disabled, and the commit is commit-tree, which runs no hook anywhere), so a hook that
    # edits and restages sources can neither block the run nor smuggle content into the push.
    fx = _fixture(tmp, "tamper")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    for name in ("pre-commit", "post-commit", "post-merge"):
        hook = fx.main / ".git" / "hooks" / name
        hook.write_text("#!/bin/sh\nprintf 'HOOK CHANGED SOURCE\\n' >> src/a.txt\n"
                        "git add -- src/a.txt\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    blob = _git(fx.remote, "cat-file", "blob", new + ":src/a.txt")[1]
    check("refuse/hooks-never-run",
          (rc, _result(reports, 1), b"HOOK CHANGED SOURCE" in blob,
           _snapshot_diff(before, _snapshot(wt))), (0, "pushed", False, []))
    # Unit probes of the two commit guards over a real private-style checkout: a parentless
    # (hook-rewritten) commit and a commit carrying a foreign tree are both refused, so neither
    # can ever be pushed.
    probe = Path(tmp) / "tamper-probe"
    _git(fx.base, "clone", "--quiet", "--shared", "--no-checkout", str(fx.main), str(probe))
    _guard("scratch-setup")(probe)
    _git(probe, "checkout", "--quiet", "--detach", old)
    tree = _git_text(probe, "rev-parse", old + "^{tree}")
    orphan = _git_text(probe, "commit-tree", tree, "-m", "orphan", extra_env=FIXTURE_IDENT)
    try:
        _guard("commit-parents")(probe, old, orphan)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/commit-parents-probe", got, "commit-mismatch")
    other_tree = _git_text(probe, "rev-parse", fx.remote_ref("main") + "^{tree}")
    swapped = _git_text(probe, "commit-tree", other_tree, "-p", old, "-m", "wrong tree",
                        extra_env=FIXTURE_IDENT)
    try:
        _guard("commit-tree")(probe, swapped, tree)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("refuse/commit-tree-probe", got, "commit-mismatch")
    check("refuse/tamper-invariants", _invariants(fx), [])


def case_filter(tmp):
    # A clean filter in the author's repository (codex round-2 reproduction): the private checkout
    # is a fresh clone that defines no filter and unsets every conversion attribute, so the checks
    # read exactly the stored blobs and the pushed tree is self-consistent: a fresh materialization
    # of the pushed commit passes the declared check.
    fx = _fixture(tmp, "filter")
    _git(fx.main, "config", "filter.constant.clean", "printf FILTERED")
    _git(fx.main, "config", "filter.constant.smudge", "cat")
    wt = fx.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt filter=constant\n")]))
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    new = fx.remote_ref("feat/x")
    fresh = Path(tmp) / "filter-fresh"
    _git(fx.base, "clone", "--quiet", "--no-local", str(fx.remote), str(fresh))
    _git(fresh, "checkout", "--quiet", "feat/x")
    fresh_check = subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"],
                                 cwd=str(fresh), env=_clean_env(), timeout=60).returncode
    check("filter/clean-filter-pushed-correctly",
          (rc, _result(reports, 1), new is not None, fresh_check,
           _snapshot_diff(before, _snapshot(wt))), (0, "pushed", True, 0, []))
    # Unit probe of the every-path byte guard: a private checkout whose file bytes diverge from the
    # committed blob (what any conversion would produce) is refused before the push.
    probe = Path(tmp) / "filter-probe"
    _git(fx.base, "clone", "--quiet", "--shared", "--no-checkout", str(fx.main), str(probe))
    _guard("scratch-setup")(probe)
    _git(probe, "checkout", "--quiet", "--detach", fx.remote_ref("main"))
    tree = _git_text(probe, "rev-parse", "HEAD^{tree}")
    (probe / "src" / "a.txt").write_text("DIVERGED\n", encoding="utf-8")
    try:
        _guard("pushed-bytes")(probe, tree)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("filter/pushed-bytes-probe", got, "commit-mismatch")
    # Pin BYTE equality, not length: a divergence that keeps the file length must still refuse.
    (probe / "src" / "a.txt").write_text(NINE.replace("l1", "L1"), encoding="utf-8")
    try:
        _guard("pushed-bytes")(probe, tree)
        got = "accepted"
    except Refuse as refusal:
        got = refusal.status
    check("filter/pushed-bytes-same-size-probe", got, "commit-mismatch")
    # Pin the symlink branch alone: a correct symlink (readlink equals the committed target) must
    # be ACCEPTED; without the branch the guard would read the linked file's bytes and refuse.
    (probe / "src" / "a.txt").write_text(NINE, encoding="utf-8")
    os.symlink("src/a.txt", str(probe / "lnk"))
    _git(probe, "add", "--", "lnk")
    tree2 = _git_text(probe, "write-tree")
    try:
        _guard("pushed-bytes")(probe, tree2)
        got = "accepted"
    except Refuse as refusal:
        got = "%s: %s" % (refusal.status, refusal.reason)
    check("filter/pushed-bytes-symlink-probe", got, "accepted")
    check("filter/invariants", _invariants(fx), [])


def case_eol(tmp):
    # An eol/text attribute on a digested source file: with conversions disabled in the private
    # checkout the regenerated digest covers the RAW blob bytes, so a NORMAL checkout of the
    # candidate (attribute honoured, CRLF materialization) would fail the declared check; the
    # fresh-checkout guard refuses it before the push with a distinct status. With the
    # scratch-setup guard reverted, the CRLF materialization instead diverges from the blob inside
    # the private checkout and the every-path byte guard refuses commit-mismatch: the named check
    # here goes red either way.
    fx = _fixture(tmp, "eol")
    wt = fx.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt text eol=crlf\n")]))
    old = fx.remote_ref("feat/x")
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("filter/eol-attribute-refused-not-pushed",
          (rc, _result(reports, 1), fx.remote_ref("feat/x"), _has_marker(wt),
           _snapshot_diff(before, _snapshot(wt)), fx.pushes()),
          (1, "normal-checkout-failed", old, False, [], []))
    # Pin the scratch-setup hooksPath line alone: a hook in the private checkout's OWN .git/hooks
    # must never fire there (the suite pins the global config, so this is the one hook source a
    # case can still place).
    probe = Path(tmp) / "eol-hooks-probe"
    _git(fx.base, "clone", "--quiet", "--shared", "--no-checkout", str(fx.main), str(probe))
    fired = Path(tmp) / "eol-hook-fired"
    hook = probe / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n: > '%s'\nexit 0\n" % fired, encoding="utf-8")
    hook.chmod(0o755)
    _guard("scratch-setup")(probe)
    _git(probe, "checkout", "--quiet", "--detach", fx.remote_ref("main"))
    check("filter/scratch-hooks-path-pinned", fired.exists(), False)
    check("filter/eol-invariants", _invariants(fx), [])


def case_lock_held(tmp):
    fx = _fixture(tmp, "lock-held")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    common = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-common-dir")
    before, pushed = _snapshot(wt), fx.pushes()
    with open(os.path.join(common, LOCK_NAME), "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        audit = len(GIT_AUDIT)
        rc, reports, fatal = fx.run(apply=True)
        fetched = [a for a in GIT_AUDIT[audit:] if a[:1] == ["fetch"]]
    check("run/lock-held-exit2", (rc, reports, LOCK_NAME in (fatal or "")), (2, [], True))
    check("run/lock-held-touches-nothing",
          (fetched, _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)), ([], [], old, True, False))
    check("run/lock-held-invariants", _invariants(fx), [])


def case_fetch_failure(tmp):
    fx = _fixture(tmp, "fetch-failure")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    before, pushed = _snapshot(wt), fx.pushes()
    _git(fx.main, "config", "remote.origin.url", str(fx.gone))
    audit = len(GIT_AUDIT)
    try:
        rc, reports, fatal = fx.run(apply=True)
    finally:
        _git(fx.main, "config", "remote.origin.url", str(fx.remote))
    touching = [a for a in GIT_AUDIT[audit:] if a[:1] in (
        ["merge"], ["commit-tree"], ["push"], ["ls-remote"], ["checkout"], ["add"], ["clone"])]
    check("run/fetch-failure-exit2", (rc, reports, "fetch failed" in (fatal or "")), (2, [], True))
    check("run/fetch-failure-touches-nothing",
          (touching, _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)), ([], [], old, True, False))
    check("run/fetch-failure-invariants", _invariants(fx), [])


def case_state_mismatch(tmp):
    # A marker naming another PR or branch is refused untouched; a marker also blocks a dry run.
    fx = _fixture(tmp, "state-mismatch")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    git_dir = _git_dir(wt)
    _write_marker(git_dir, dict(pr=7, branch="feat/other", old=old, new=ZERO))
    marker = Path(git_dir) / MARKER_NAME
    marker_bytes = marker.read_bytes()
    before, pushed = _snapshot(wt), fx.pushes()
    rc, reports, _fatal = fx.run(apply=True)
    check("idem/state-mismatch-refused", (rc, _result(reports, 1)), (1, "state-mismatch"))
    rc, reports, _fatal = fx.run()
    check("idem/state-mismatch-dry-run", (rc, _result(reports, 1)), (1, "state-mismatch"))
    check("idem/state-mismatch-touches-nothing",
          (_snapshot_diff(before, _snapshot(wt)), marker.read_bytes() == marker_bytes,
           fx.remote_ref("feat/x"), fx.pushes() == pushed), ([], True, old, True))
    check("idem/state-mismatch-invariants", _invariants(fx), [])


def case_cli(tmp):
    proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--bogus"],
                          capture_output=True, timeout=60, env=_clean_env())
    check("cli/bad-argument-exit2", proc.returncode, 2)
    # No arguments must select the self-test, never a live run: the config-injection lane launches
    # this suite runner bare under poisoned git configuration and requires a hermetic exit 0.
    check("cli/bare-is-self-test", (_mode([]), _mode(["--self-test"])[0]),
          (("self-test", dict(red_on_revert=False)), "self-test"))
    check("cli/live-needs-option",
          (_mode(["--dry-run"]), _mode(["--apply"])[0], _mode(["--pr", "7"])[0],
           _mode(["--dry-run", "--apply"])),
          (("live", dict(apply=False, repo=".", prs=[])), "live", "live", ("usage", None)))
    # --pr takes ASCII digits only: str.isdigit accepts U+00B2 (which int() then refuses, a crash)
    # and int() accepts U+0661 (ARABIC-INDIC ONE, silently 1); both must be plain usage errors.
    check("cli/pr-non-ascii-digit-usage",
          (_mode(["--pr", "\u00b2"]), _mode(["--pr", "\u0661"]), _mode(["--pr", "7"])[0]),
          (("usage", None), ("usage", None), "live"))
    plain = Path(tmp) / "not-a-repo"
    plain.mkdir()
    audit = len(GIT_AUDIT)
    rc, reports, fatal = run_train(plain)
    check("cli/not-a-repo-exit2", (rc, reports, bool(fatal)), (2, [], True))
    # No remote exists here, so of the per-case invariants only the argv audit applies.
    check("cli/invariants", [a for a in GIT_AUDIT[audit:] if _audit_violation(a)], [])



def case_refmap(tmp):
    # codex round-3 blocker: with remote.origin.fetch mapping remote heads onto LOCAL branches,
    # even a dry run's base fetch rewound refs/heads/author-saved in the author's repository. The
    # base fetch passes --refmap= so only its explicit refspec applies: it writes
    # refs/remotes/origin/main and FETCH_HEAD, never a ref the author owns.
    fx = _fixture(tmp, "refmap")
    wt = _stale_pr(fx)
    head = _git_text(fx.main, "rev-parse", "HEAD")
    unique = _git_text(fx.main, "commit-tree", head + "^{tree}", "-p", head, "-m",
                       "author saved work", extra_env=FIXTURE_IDENT)
    _git(fx.main, "branch", "author-saved", unique)
    _git(fx.main, "config", "remote.origin.fetch", "+refs/heads/main:refs/heads/author-saved")
    before = _snapshot(wt)
    try:
        rc, reports, _fatal = fx.run()
    finally:
        _git(fx.main, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    check("fetch/refmap-keeps-local-branches",
          (rc, _result(reports, 1), _git_text(fx.main, "rev-parse", "refs/heads/author-saved"),
           _snapshot_diff(before, _snapshot(wt))),
          (0, "would-merge", unique, []))
    check("fetch/refmap-invariants", _invariants(fx), [])


def _gitdir_snapshot(repo):
    """Every ref with its target, the packed-refs and FETCH_HEAD bytes (None when absent), every
    reflog file's bytes, every object file's content digest and every object file's mtime of a
    repository's common git dir: the self-test's proof that a run leaves the author's .git
    untouched apart from the lock file (not covered here by construction), the transient --apply
    marker (removed before the run returns) and the ONE disclosed object-store footprint, a
    refreshed mtime on an object byte-identical before and after (see _gitdir_snapshot_diff)."""
    git_dir = _git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    refs = _git_text(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    files = dict()
    for name in ("packed-refs", "FETCH_HEAD"):
        path = os.path.join(git_dir, name)
        files[name] = _read_text(path) if os.path.exists(path) else None
    logs = dict()
    for dirpath, _dirnames, filenames in os.walk(os.path.join(git_dir, "logs")):
        for name in filenames:
            path = os.path.join(dirpath, name)
            logs[os.path.relpath(path, git_dir)] = _read_text(path)
    objects, mtimes = dict(), dict()
    obj_root = os.path.join(git_dir, "objects")
    for dirpath, _dirnames, filenames in os.walk(obj_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, obj_root)
            with open(path, "rb") as handle:
                objects[rel] = hashlib.sha256(handle.read()).hexdigest()
            mtimes[rel] = os.stat(path).st_mtime_ns
    return dict(refs=refs, files=files, logs=logs, objects=objects, mtimes=mtimes)


def _gitdir_snapshot_diff(before, after):
    """The snapshot keys whose state changed, with the ONE disclosed footprint excluded: an mtime
    change on an object file whose bytes are identical on both sides is git FRESHENING an existing
    object (the module docstring's disclosed residual) and is allowed; an mtime change on any
    other object file, and every ref, file, reflog or object-content change, is reported."""
    diffs = [k for k in sorted(before) if k != "mtimes" and before[k] != after[k]]
    freshened_ok = set(p for p in before["objects"]
                       if before["objects"][p] == after["objects"].get(p))
    moved = [p for p in set(before["mtimes"]) | set(after["mtimes"])
             if before["mtimes"].get(p) != after["mtimes"].get(p) and p not in freshened_ok]
    if moved:
        diffs.append("mtimes")
    return diffs


def case_basefetch(tmp):
    # QA round 4 (codex blocker, claude B1): the base fetch must not run IN the author's
    # repository at all. It runs in a private bare repository the run owns, so a symbolic
    # refs/remotes/origin/main, a populated submodule with its own refspecs, an author-side
    # reference-transaction hook and a configured remote.origin.uploadpack can neither rewrite an
    # author-owned ref nor execute an author-configured command, and the author's .git receives
    # no ref, object, reflog or FETCH_HEAD, on a dry run or an apply run.
    fx = _fixture(tmp, "basefetch")
    wt = _stale_pr(fx)
    head = _git_text(fx.main, "rev-parse", "HEAD")
    unique = _git_text(fx.main, "commit-tree", head + "^{tree}", "-p", head, "-m",
                       "author saved work", extra_env=FIXTURE_IDENT)
    _git(fx.main, "branch", "author-saved", unique)
    # codex round-4 blocker: a symbolic remote-tracking ref; a fetch updating it in the author's
    # repository would follow it and rewind refs/heads/author-saved, --refmap= notwithstanding.
    _git(fx.main, "symbolic-ref", "refs/remotes/origin/main", "refs/heads/author-saved")
    hook_fired = Path(tmp) / "basefetch-hook-fired"
    hook = fx.main / ".git" / "hooks" / "reference-transaction"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n: > '%s'\nexit 0\n" % hook_fired, encoding="utf-8")
    hook.chmod(0o755)
    up_fired = Path(tmp) / "basefetch-uploadpack-fired"
    up = Path(tmp) / "basefetch-uploadpack"
    up.write_text('#!/bin/sh\n: > \'%s\'\nexec git upload-pack "$@"\n' % up_fired,
                  encoding="utf-8")
    up.chmod(0o755)
    _git(fx.main, "config", "remote.origin.uploadpack", str(up))
    before = _gitdir_snapshot(fx.main)
    rc, reports, _fatal = fx.run()
    check("fetch/private-symref-keeps-local-branch",
          (rc, _result(reports, 1), _git_text(fx.main, "rev-parse", "refs/heads/author-saved"),
           _git_text(fx.main, "symbolic-ref", "refs/remotes/origin/main")),
          (0, "would-merge", unique, "refs/heads/author-saved"))
    check("fetch/private-dry-run-gitdir-unchanged",
          _gitdir_snapshot_diff(before, _gitdir_snapshot(fx.main)), [])
    # QA round 5 (codex minor 4, claude minor 7): the apply snapshot must be sensitive on its own,
    # not shielded by the dry run having already absorbed an author-side fetch: the remote base
    # advances to a FOREIGN commit the author's repository does not have, so a reverted
    # author-side fetch writes new objects there during the apply and this check alone goes red.
    fx.foreign_commit(fx.remote_refs()["refs/heads/main"], "refs/heads/main")
    before_apply = _gitdir_snapshot(fx.main)
    rc2, reports2, _fatal = fx.run(apply=True)
    check("fetch/private-apply-gitdir-unchanged",
          (rc2, _result(reports2, 1),
           _gitdir_snapshot_diff(before_apply, _gitdir_snapshot(fx.main)), _has_marker(wt)),
          (0, "pushed", [], False))
    check("fetch/private-author-hook-uploadpack-never-run",
          (hook_fired.exists(), up_fired.exists()), (False, False))
    # claude round-4 B1: a populated submodule whose own refspec maps the remote main onto a LOCAL
    # branch. An on-demand-recursing fetch run in the author's repository would rewrite it; the
    # private bare base repository has no submodule and passes --no-recurse-submodules explicitly.
    fx2 = _fixture(tmp, "basefetch-sub")
    subremote = Path(tmp) / "subremote.git"
    _git(fx2.base, "init", "-q", "--bare", "-b", "main", str(subremote))
    seed = Path(tmp) / "basefetch-subseed"
    _git(fx2.base, "init", "-q", "-b", "main", str(seed))
    fx2.write(seed, "s.txt", "s1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "s1", extra_env=FIXTURE_IDENT)
    _git(seed, "push", "-q", str(subremote), "main")
    _git(fx2.main, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(subremote), "sub")
    fx2.commit(fx2.main, "add submodule")
    _git(fx2.main, "push", "-q", "origin", "main")
    sub = fx2.main / "sub"
    _git(sub, "config", "remote.origin.fetch", "+refs/heads/main:refs/heads/saved")
    _git(sub, "config", "protocol.file.allow", "always")
    subhead = _git_text(sub, "rev-parse", "HEAD")
    sub_unique = _git_text(sub, "commit-tree", subhead + "^{tree}", "-p", subhead, "-m",
                           "sub saved work", extra_env=FIXTURE_IDENT)
    _git(sub, "branch", "saved", sub_unique)
    # Someone else advances the submodule and bumps the gitlink on the remote main; the author's
    # repository has neither the new sub commit nor the bump commit.
    fx2.write(seed, "s.txt", "s2\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "s2", extra_env=FIXTURE_IDENT)
    _git(seed, "push", "-q", str(subremote), "main")
    s2 = _git_text(seed, "rev-parse", "HEAD")
    helper = Path(tmp) / "basefetch-helper"
    _git(fx2.base, "clone", "-q", "--no-local", str(fx2.remote), str(helper))
    _git(helper, "update-index", "--cacheinfo", "160000,%s,sub" % s2)
    _git(helper, "commit", "-q", "-m", "bump sub", extra_env=FIXTURE_IDENT)
    _git(helper, "push", "-q", "origin", "main")
    rc3, _reports, _fatal = fx2.run()
    check("fetch/private-submodule-not-recursed",
          (rc3, _git_text(sub, "rev-parse", "refs/heads/saved")), (0, sub_unique))
    # codex round-5 major 1: the author's repository is a PARTIAL clone and the commit named by
    # its remote-tracking ref is a missing PROMISED object (it exists only on the remote). The
    # author-side rev-parse that seeds negotiation must fail closed under GIT_NO_LAZY_FETCH,
    # never launch a lazy fetch, which would execute the configured remote.origin.uploadpack and
    # write pack files into the author's object store. The run itself still succeeds: the private
    # base fetch gets the commit from the remote without the seed.
    fx3 = _fixture(tmp, "basefetch-lazy")
    _stale_pr(fx3)
    up3_fired = Path(tmp) / "basefetch-lazy-uploadpack-fired"
    up3 = Path(tmp) / "basefetch-lazy-uploadpack"
    up3.write_text('#!/bin/sh\n: > \'%s\'\nexec git upload-pack "$@"\n' % up3_fired,
                   encoding="utf-8")
    up3.chmod(0o755)
    for key, value in (("remote.origin.promisor", "true"),
                       ("remote.origin.partialclonefilter", "blob:none"),
                       ("remote.origin.uploadpack", str(up3))):
        _git(fx3.main, "config", key, value)
    foreign = fx3.foreign_commit(fx3.remote_refs()["refs/heads/main"], "refs/heads/main")
    # The tracking ref names the foreign commit the author's store does not have: written as a
    # loose ref file, because update-ref would itself refuse (or lazily fetch) the missing object.
    ref_path = fx3.main / ".git" / "refs" / "remotes" / "origin" / "main"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(foreign + "\n", encoding="utf-8")
    before_lazy = _gitdir_snapshot(fx3.main)
    rc4, reports4, _fatal = fx3.run()
    check("fetch/private-no-lazy-fetch",
          (rc4, _result(reports4, 1), up3_fired.exists(),
           _gitdir_snapshot_diff(before_lazy, _gitdir_snapshot(fx3.main))),
          (0, "would-merge", False, []))
    check("fetch/private-invariants", _invariants(fx, fx2, fx3), [])
    _lazy_fetch_env(tmp)


def _lazy_fetch_env(tmp):
    # QA round 6 (claude minor 3): protocol.allow=never ALONE already fails the author-side read
    # of a missing promised object closed, so dropping GIT_NO_LAZY_FETCH from _git_author or
    # emptying _ALTERNATE_FETCH_ENV changes no tool-level behaviour and cannot be made red by any
    # behavioural case (see the _git_author docstring). Each layer is pinned here by what the
    # child process actually RECEIVES instead: every author-side call (recognized by the
    # protocol.allow=never pin in its argv) and both alternate-carrying fetches (the base fetch
    # and the scratch PR fetch: "fetch" plus the -c pins, without protocol.allow=never) must
    # carry GIT_NO_LAZY_FETCH=1 in the environment handed to the child.
    fx = _fixture(tmp, "lazyenv")
    _stale_pr(fx)
    global _run_child
    real_child = _run_child
    seen = []

    def recording_run(argv, cwd=None, env=None, **kwargs):
        seen.append((list(argv), dict(env or dict())))
        return real_child(argv, cwd=cwd, env=env, **kwargs)

    _run_child = recording_run
    try:
        rc, reports, _fatal = fx.run()
    finally:
        _run_child = real_child
    author = [(a, e) for a, e in seen if "protocol.allow=never" in a]
    alternate = [(a, e) for a, e in seen
                 if "fetch" in a and "core.fsmonitor=false" in a
                 and "protocol.allow=never" not in a]
    check("fetch/author-env-no-lazy-fetch",
          (rc, _result(reports, 1), len(author) > 0,
           [a for a, e in author if e.get("GIT_NO_LAZY_FETCH") != "1"]),
          (0, "would-merge", True, []))
    check("fetch/alternate-env-no-lazy-fetch",
          (len(alternate), [a for a, e in alternate if e.get("GIT_NO_LAZY_FETCH") != "1"]),
          (2, []))
    check("fetch/lazy-env-invariants", _invariants(fx), [])


def case_leak(tmp):
    # A refusal while the private checkout is still being CREATED (the clone itself, or the
    # scratch-setup guard) must still remove the scratch directory: the per-PR finally starts
    # right after mkdtemp, not after the first successful git call.
    fx = _fixture(tmp, "leak-clone")
    wt = _stale_pr(fx)
    before = _snapshot(wt)
    made = len(SCRATCHES)
    global _run_child
    real_child = _run_child

    def clone_timeout(argv, **kwargs):
        if "clone" in argv and "--shared" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return real_child(argv, **kwargs)

    _run_child = clone_timeout
    try:
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        _run_child = real_child
    created = SCRATCHES[made:]
    # created holds TWO directories per run now: the per-run private base repository and the
    # per-PR scratch root; both must be gone.
    check("scratch/clone-failure-removed",
          (rc, _result(reports, 1), len(created), [p for p in created if os.path.exists(p)],
           _snapshot_diff(before, _snapshot(wt))),
          (1, "git-failed", 2, [], []))
    fx2 = _fixture(tmp, "leak-setup")
    wt2 = _stale_pr(fx2)
    before2 = _snapshot(wt2)
    made = len(SCRATCHES)
    saved = GUARDS["scratch-setup"]

    def fail_setup(_scratch):
        raise Refuse("git-failed", "injected scratch-setup failure")

    GUARDS["scratch-setup"] = fail_setup
    try:
        rc, reports, _fatal = fx2.run(apply=True)
    finally:
        GUARDS["scratch-setup"] = saved
    created = SCRATCHES[made:]
    check("scratch/setup-failure-removed",
          (rc, _result(reports, 1), len(created), [p for p in created if os.path.exists(p)],
           _snapshot_diff(before2, _snapshot(wt2))),
          (1, "git-failed", 2, [], []))
    check("scratch/leak-invariants", _invariants(fx, fx2), [])


def case_partial(tmp):
    # QA round 6 (claude, seen but not then established; CONFIRMED here on git 2.53.0): the
    # author's repository is a partial clone and a blob of the PR head is a missing PROMISED
    # object. The --shared scratch clone shares that store, and `git checkout --detach` of the
    # PR head EXITS 0 while printing "error: unable to read sha1 file", leaving the path absent
    # from the scratch worktree: the corruption is silently swallowed at the point it happens.
    # With the scratch-complete guard reverted, THIS fixture happens to die later and UNNAMED
    # (the merge refuses to start over the missing worktree file; the run reports git-failed
    # "the merge did not start" and never mentions the unreadable blob); nothing establishes
    # that every later stage would catch every such tree, so the guard refuses by NAME at the
    # corruption point: scratch-incomplete, nothing pushed, nothing changed anywhere.
    fx = _fixture(tmp, "partial")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    blob = _git_text(fx.main, "rev-parse", old + ":src/b.txt")
    for key, value in (("remote.origin.promisor", "true"),
                       ("remote.origin.partialclonefilter", "blob:none")):
        _git(fx.main, "config", key, value)
    objects = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    os.remove(os.path.join(objects, blob[:2], blob[2:]))
    made = len(SCRATCHES)
    rc, reports, _fatal = fx.run(apply=True)
    check("scratch/partial-clone-missing-blob-refused",
          (rc, _result(reports, 1), fx.remote_ref("feat/x"), fx.pushes(), _has_marker(wt),
           [p for p in SCRATCHES[made:] if os.path.exists(p)]),
          (1, "scratch-incomplete", old, [], False, []))
    check("scratch/partial-clone-invariants", _invariants(fx), [])


def case_signal(tmp):
    # A real SIGTERM or SIGHUP mid-regenerate, against a REAL tool subprocess with a case-private
    # TMPDIR: the handler converts the signal into the normal cleanup (the regenerate child is
    # killed and reaped, the scratch is removed, the lock is released) and the process exits with
    # the conventional killed-by-signal status. The tool runs as a subprocess, so the fixture
    # invariant recorder does not apply here; the remote is asserted directly.
    for signum, check_id in ((signal.SIGTERM, "signal/sigterm-cleans-up"),
                             (signal.SIGHUP, "signal/sighup-cleans-up"),
                             (signal.SIGQUIT, "signal/sigquit-cleans-up")):
        name = "sig%d" % signum
        fx = _fixture(tmp, name)
        wt = _stale_pr(fx)
        old = fx.remote_ref("feat/x")
        probe_dir = Path(tmp) / (name + "-tmp")
        probe_dir.mkdir()
        pid_file = probe_dir / "sleeper.pid"
        text = _read_text(fx.main / CONFIG_PATH)
        plain = 'regenerate = [["%s", "-I", "-B", "tools/gen.py"]]' % sys.executable
        # The sleeping regenerate command spawns its OWN child (a grandchild of the tool), so the
        # case also pins the process-GROUP termination: round-4 M1 showed a plain kill of the
        # direct child leaves such a grandchild running in the scratch TMPDIR. The grandchild
        # IGNORES SIGTERM (round-5: codex major 2, claude major 1), and the GRANDCHILD itself
        # writes the readiness file, AFTER installing SIG_IGN (round-6: claude minor 1 - when the
        # direct child wrote it, the signal could land before the grandchild was stubborn, so the
        # post-exit group probe was exercised only on some runs). The grandchild takes the pid
        # file and the direct child's pid as argv and avoids quote characters (os.open plus chr)
        # because its code sits two -c levels deep inside a TOML basic string.
        sleeper = ('regenerate = [["%s", "-I", "-B", "tools/gen.py"], ["%s", "-c", "import os,'
                   " subprocess, sys, time; subprocess.Popen([sys.executable, '-c',"
                   " 'import os, signal, sys, time;"
                   " signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                   " fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT);"
                   " os.write(fd, (sys.argv[2] + chr(32) + str(os.getpid())).encode());"
                   " os.close(fd); time.sleep(300)', os.environ['SIG_PID_FILE'],"
                   ' str(os.getpid())]); time.sleep(120)"]]') % (sys.executable, sys.executable)
        fx.write(fx.main, CONFIG_PATH, text.replace(plain, sleeper))
        _git(fx.main, "commit", "-q", "-am", "sleeping regenerate", extra_env=FIXTURE_IDENT)
        _git(fx.main, "push", "-q", "origin", "main")
        before = _snapshot(wt)
        env = dict(os.environ)
        env.update(TMPDIR=str(probe_dir), SIG_PID_FILE=str(pid_file),
                   FAKE_GH_STATE=str(fx.state), PATH=str(fx.bin) + os.pathsep + fx.path)
        proc = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                 "--repo", str(fx.main), "--apply"], cwd=str(fx.base), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 120
        tokens = []
        while time.time() < deadline and proc.poll() is None:
            try:
                tokens = pid_file.read_text(encoding="utf-8").split()
            except OSError:
                tokens = []
            if len(tokens) == 2:
                break
            time.sleep(0.1)
        started = len(tokens) == 2
        proc.send_signal(signum)
        try:
            rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
        proc.stdout.close()
        proc.stderr.close()
        sleeper_dead = None
        if started:
            dead = []
            for sleeper_pid in (int(t) for t in tokens):
                for _ in range(100):
                    try:
                        os.kill(sleeper_pid, 0)
                    except ProcessLookupError:
                        dead.append(True)
                        break
                    except PermissionError:
                        dead.append(False)
                        break
                    time.sleep(0.1)
                else:
                    dead.append(False)
                    try:
                        os.kill(sleeper_pid, signal.SIGKILL)
                    except OSError:
                        pass
            sleeper_dead = dead == [True, True]
        leftovers = [p for p in os.listdir(str(probe_dir)) if p.startswith("merge-train-")]
        common = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-common-dir")
        # No stale git ref lock anywhere in the author's .git: the children were sent SIGTERM (git
        # removes its own *.lock files then), and no tool git command runs in the author's
        # repository that could take a ref lock there at all.
        stale_locks = []
        for dirpath, _dirnames, filenames in os.walk(common):
            for fname in filenames:
                if fname.endswith(".lock") and fname != LOCK_NAME:
                    stale_locks.append(os.path.relpath(os.path.join(dirpath, fname), common))
        with open(os.path.join(common, LOCK_NAME), "a+") as holder:
            try:
                fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_free = True
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            except OSError:
                lock_free = False
        check(check_id,
              (started, rc, sleeper_dead, leftovers, stale_locks, lock_free, _has_marker(wt),
               fx.remote_ref("feat/x"), _snapshot_diff(before, _snapshot(wt))),
              (True, -signum, True, [], [], True, False, old, []))
    _sigterm_during_push(tmp)
    _launch_window(tmp)
    _second_signal_during_cleanup(tmp)
    _child_mask_restored()
    _group_remnant_reported(tmp)


def _sigterm_during_push(tmp):
    # QA round 5 (claude minor 7, codex minor 4): the SIGTERM-first contract exercised against a
    # REAL git operation that HOLDS LOCK FILES when the signal lands. The remote's
    # reference-transaction hook parks the push at "prepared", the moment receive-pack holds the
    # ref lock(s) for the update, so the SIGTERM arrives while git push, git receive-pack and the
    # hook are all alive in the child's process group: git's lockfile handler runs on SIGTERM
    # (a plain unlink, async-signal-safe) and removes them, so no stale *.lock may remain in the
    # push-target repository or the author's. A SIGKILL-only mutant leaves the ref locks behind
    # and goes red here. NOT asserted: the receive-pack quarantine (objects/tmp_objdir-*), which
    # git itself deliberately leaves behind on ANY signalled death (its removal is not
    # async-signal-safe), SIGTERM and SIGKILL alike. The marker is expected to REMAIN: the push's
    # outcome is unknown, which is exactly what the retry reconciles.
    fx = _fixture(tmp, "sigpush")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    started_file = fx.base / "push-started"
    hook = fx.remote / "hooks" / "reference-transaction"
    hook.write_text('#!/bin/sh\ncat > /dev/null\nif [ "$1" = prepared ]; then : > \'%s\';'
                    " sleep 60; fi\nexit 0\n" % started_file, encoding="utf-8")
    hook.chmod(0o755)
    env = dict(os.environ)
    env.update(FAKE_GH_STATE=str(fx.state), PATH=str(fx.bin) + os.pathsep + fx.path)
    proc = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                             "--repo", str(fx.main), "--apply"], cwd=str(fx.base), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 120
    while time.time() < deadline and proc.poll() is None and not started_file.exists():
        time.sleep(0.1)
    started = started_file.exists()
    proc.send_signal(signal.SIGTERM)
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = proc.wait()
    proc.stdout.close()
    proc.stderr.close()
    stale = []
    common = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-common-dir")
    for walk_root in (common, str(fx.remote)):
        for dirpath, _dirnames, filenames in os.walk(walk_root):
            for fname in filenames:
                if fname.endswith(".lock") and fname != LOCK_NAME:
                    stale.append(os.path.relpath(os.path.join(dirpath, fname), walk_root))
    check("signal/sigterm-during-push-no-stale-locks",
          (started, rc, sorted(stale), fx.remote_ref("feat/x"), _has_marker(wt)),
          (True, -signal.SIGTERM, [], old, True))


def _launch_window(tmp):
    # QA round 5 (codex major 3, claude minor 5) and round 6 (codex major 1): a signal delivered
    # between the fork and the registration of the child handle must not leak the just-created
    # session. The launch runs with UNWIND_SIGNALS blocked; the pending signal is delivered at
    # the restore INSIDE the protected region and ends the child like any other unwind. SIGTERM
    # exercises the _Signalled conversion path and SIGINT the KeyboardInterrupt path (round 6:
    # SIGINT was missing from the launch mask, reached the launch-failure handler with no handle
    # under cleanup, and leaked the new session). Deterministic and in-process: Popen is patched
    # to send this process the real signal right after the sleeping regenerate child is created,
    # the exact window codex reproduced.
    fx = _fixture(tmp, "launchwin")
    _stale_pr(fx)
    text = _read_text(fx.main / CONFIG_PATH)
    plain = 'regenerate = [["%s", "-I", "-B", "tools/gen.py"]]' % sys.executable
    marker = "import time; time.sleep(120)"
    sleeper = ('regenerate = [["%s", "-I", "-B", "tools/gen.py"], ["%s", "-c", "%s"]]'
               % (sys.executable, sys.executable, marker))
    fx.write(fx.main, CONFIG_PATH, text.replace(plain, sleeper))
    _git(fx.main, "commit", "-q", "-am", "sleeping regenerate", extra_env=FIXTURE_IDENT)
    _git(fx.main, "push", "-q", "origin", "main")
    for signum, expected, check_id in (
            (signal.SIGTERM, signal.SIGTERM, "signal/launch-window-covered"),
            (signal.SIGINT, "keyboard-interrupt", "signal/launch-window-covered-sigint")):
        made = len(SCRATCHES)
        real_popen = subprocess.Popen
        state = dict(proc=None)

        def kicking_popen(argv, _signum=signum, _state=state, **kwargs):
            proc = real_popen(argv, **kwargs)
            if _state["proc"] is None and marker in list(argv):
                _state["proc"] = proc
                os.kill(os.getpid(), _signum)
            return proc

        subprocess.Popen = kicking_popen
        outcome = None
        try:
            fx.run(apply=True)
        except _Signalled as sig:
            outcome = sig.signum
        except KeyboardInterrupt:
            outcome = "keyboard-interrupt"
        finally:
            subprocess.Popen = real_popen
        created = SCRATCHES[made:]
        alive = state["proc"] is not None and state["proc"].poll() is None
        if alive:
            try:
                os.killpg(state["proc"].pid, signal.SIGKILL)
            except OSError:
                pass
            state["proc"].wait()
        check(check_id,
              (state["proc"] is not None, outcome, alive,
               [p for p in created if os.path.exists(p)],
               sorted(s.name for s in signal.pthread_sigmask(signal.SIG_BLOCK, set())
                      if s in UNWIND_SIGNALS)),
              (True, expected, False, [], []))


def _second_signal_during_cleanup(tmp):
    # QA round 4 (claude m1), extended in round 6 (claude minor 5) to EVERY signal in
    # UNWIND_SIGNALS: a second signal arriving WHILE a cleanup finally runs its rmtree must not
    # abort the removal and leak the directory: every cleanup runs with UNWIND_SIGNALS blocked,
    # and the blocked signal is delivered when the cleanup ends (as _Signalled for SIGTERM,
    # SIGHUP and SIGQUIT, as KeyboardInterrupt for SIGINT), so removing ANY one signal from
    # _cleanup's mask goes red on that signal's leg. In-process and deterministic: the first
    # signal is simulated by a guard raising _Signalled at fixpoint, and a REAL second signal is
    # sent to this process at the moment the per-PR cleanup's rmtree starts (the patched
    # shutil.rmtree fires exactly once per leg). The ids are for-loop literals (the execution
    # gate resolves check ids statically); the invariants check below pins the covered set to
    # UNWIND_SIGNALS, so a signal added to the constant without a leg here goes red.
    fx = _fixture(tmp, "sigmask")
    _stale_pr(fx)
    covered = []
    for signum, check_id in (
            (signal.SIGHUP, "signal/second-sighup-during-cleanup-no-leak"),
            (signal.SIGINT, "signal/second-sigint-during-cleanup-no-leak"),
            (signal.SIGQUIT, "signal/second-sigquit-during-cleanup-no-leak"),
            (signal.SIGTERM, "signal/second-sigterm-during-cleanup-no-leak")):
        covered.append(signum)
        made = len(SCRATCHES)
        real_rmtree = shutil.rmtree
        fired = dict(sent=False)

        def kicking_rmtree(path, _signum=signum, _fired=fired, **kwargs):
            if not _fired["sent"]:
                _fired["sent"] = True
                os.kill(os.getpid(), _signum)
            return real_rmtree(path, **kwargs)

        def raise_signalled(*_args, **_kwargs):
            raise _Signalled(signal.SIGTERM)

        saved = GUARDS["fixpoint"]
        GUARDS["fixpoint"] = raise_signalled
        shutil.rmtree = kicking_rmtree
        outcome = None
        try:
            fx.run(apply=True)
        except _Signalled as sig:
            outcome = sig.signum
        except KeyboardInterrupt:
            outcome = "keyboard-interrupt"
        finally:
            shutil.rmtree = real_rmtree
            GUARDS["fixpoint"] = saved
        created = SCRATCHES[made:]
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        expected_outcome = ("keyboard-interrupt" if signum == signal.SIGINT else signum)
        expected_disposition = (signal.default_int_handler if signum == signal.SIGINT
                                else signal.SIG_DFL)
        check(check_id,
              (fired["sent"], outcome, [p for p in created if os.path.exists(p)],
               signal.getsignal(signum),
               sorted(s.name for s in mask if s in UNWIND_SIGNALS)),
              (True, expected_outcome, [], expected_disposition, []))
    check("signal/second-signal-cleanup-invariants",
          (_invariants(fx), sorted(covered) == sorted(UNWIND_SIGNALS)), ([], True))


def _child_mask_restored():
    # QA round 6 (claude minor 2): the child-side mask restore pinned directly. The launch blocks
    # UNWIND_SIGNALS in the parent, and without the preexec_fn the child would inherit that mask
    # across exec (git clears the mask for ITS own children, so the tool-level signal cases stay
    # green without this probe). The child reports which of the four unwind signals are blocked
    # at exec; the set is written out longhand here, independent of UNWIND_SIGNALS, so a drifted
    # constant cannot blind the probe.
    code = ("import signal; print(','.join(sorted(s.name for s in "
            "signal.pthread_sigmask(signal.SIG_BLOCK, set()) if s in (signal.SIGINT, "
            "signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT))))")
    proc = _run_child([sys.executable, "-I", "-B", "-c", code])
    check("signal/child-mask-restored",
          (proc.returncode, proc.stdout.decode("utf-8", "replace").strip()), (0, ""))


def _group_remnant_reported(tmp):
    # QA round 6 (codex minor 2): the final bounded probe in _end_child can expire with the group
    # still populated; the honest contract is REPORT, never a silent return. Reproduced with
    # codex's shape: this process becomes a Linux child subreaper, the direct child dies on the
    # group SIGTERM, the grandchild ignores SIGTERM, is SIGKILLed by the post-exit probe, and is
    # then retained as an unreaped ZOMBIE of this process (the subreaper), so the group is still
    # non-empty when the last bound expires and _end_child must say so on stderr. TERM_GRACE is
    # shortened for the case; the zombie is reaped and the subreaper flag cleared afterwards.
    import contextlib
    import ctypes
    import io
    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_child_subreaper = 36
    if libc.prctl(pr_set_child_subreaper, 1, 0, 0, 0) != 0:
        # Fail closed and loud: the suite targets Linux, where this cannot fail; the missing
        # check id then fails the execution-set reconciliation with this exception recorded.
        raise OSError("prctl(PR_SET_CHILD_SUBREAPER) failed: errno %d" % ctypes.get_errno())
    global TERM_GRACE
    saved_grace = TERM_GRACE
    ready = Path(tmp) / "remnant-grandchild-pid"
    grand = ('import os, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
             'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(300)')
    child = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', %r, "
             "sys.argv[1]]); time.sleep(300)" % grand)
    gc_pid, outcome, reported = None, None, ""
    try:
        TERM_GRACE = 0.5
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", child, str(ready)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.05)
        if ready.exists():
            gc_pid = int(ready.read_text(encoding="utf-8"))
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            _end_child(proc, group=True)
        reported = sink.getvalue()
        outcome = (proc.returncode, _group_alive(proc.pid))
    finally:
        TERM_GRACE = saved_grace
        if gc_pid is not None:
            try:
                os.waitpid(gc_pid, 0)
            except OSError:
                pass
        libc.prctl(pr_set_child_subreaper, 0, 0, 0, 0)
    check("signal/group-remnant-reported",
          (gc_pid is not None, outcome,
           ("process group %d still has members" % proc.pid) in reported,
           "zombies" in reported, _group_alive(proc.pid)),
          (True, (-signal.SIGTERM, True), True, True, False))


def case_crlf(tmp):
    # codex round-3 finding 3, first reproduction: a committed `text eol=crlf` attribute with the
    # digest generated over the CRLF worktree bytes. The PR's own fresh checkout passes; the
    # train's conversion-free regeneration would rewrite the digest over the RAW (LF) bytes, so a
    # normal checkout of the candidate would fail its own check: refused before the push.
    fx = _fixture(tmp, "crlf")
    wt = fx.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\r\n"), (".gitattributes", "src/b.txt text eol=crlf\n")]))
    old = fx.remote_ref("feat/x")
    fresh = Path(tmp) / "crlf-before"
    _git(fx.base, "clone", "--quiet", "--no-local", str(fx.remote), str(fresh))
    _git(fresh, "checkout", "--quiet", "feat/x")
    check_before = subprocess.run([sys.executable, "-I", "-B", "tools/gen.py", "--check"],
                                  cwd=str(fresh), env=_clean_env(), timeout=60).returncode
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    check("fresh/eol-crlf-refused", (check_before, rc, _result(reports, 1)),
          (0, 1, "normal-checkout-failed"))
    check("fresh/eol-crlf-untouched",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes(),
           _has_marker(wt), [p for p in SCRATCHES if os.path.exists(p)]),
          ([], old, [], False, []))
    # Second reproduction: an operator-global core.autocrlf=true. The suite's HOME is the scrubbed
    # scratch home, so the case may own its .gitconfig for the run's duration; the private
    # checkout pins the conversions off, but the fresh NORMAL checkout honours the global setting,
    # materializes CRLF, fails the check, and the candidate is refused.
    fx2 = _fixture(tmp, "autocrlf")
    wt2 = _stale_pr(fx2)
    old2 = fx2.remote_ref("feat/x")
    gitconfig = Path(os.environ["HOME"]) / ".gitconfig"
    gitconfig.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    try:
        rc, reports, _fatal = fx2.run(apply=True)
    finally:
        try:
            os.unlink(str(gitconfig))
        except FileNotFoundError:
            pass
    check("fresh/autocrlf-refused",
          (rc, _result(reports, 1), fx2.remote_ref("feat/x"), _has_marker(wt2), fx2.pushes()),
          (1, "normal-checkout-failed", old2, False, []))
    check("fresh/invariants", _invariants(fx, fx2), [])


def case_latepush(tmp):
    # claude round-3 F3: the push times out while the remote's pre-receive hook is still running.
    # The observer then sees the OLD head, but the push is still in flight and can land after the
    # report: the result must be push-unknown with the marker KEPT, never push-rejected.
    fx = _fixture(tmp, "latepush")
    wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    text = _read_text(fx.main / CONFIG_PATH).replace(
        "network_timeout_seconds = 60", "network_timeout_seconds = 3")
    fx.write(fx.main, CONFIG_PATH, text)
    _git(fx.main, "commit", "-q", "-am", "short network timeout", extra_env=FIXTURE_IDENT)
    _git(fx.main, "push", "-q", "origin", "main")
    hook = fx.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nsleep 6\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    before = _snapshot(wt)
    rc, reports, _fatal = fx.run(apply=True)
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/inflight-timeout-push-unknown",
          (rc, _result(reports, 1), marker.get("old"), bool(marker.get("new")),
           _snapshot_diff(before, _snapshot(wt))),
          (1, "push-unknown", old, True, []))
    deadline = time.time() + 30
    while time.time() < deadline and fx.remote_ref("feat/x") != marker.get("new"):
        time.sleep(0.5)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    rc, reports, _fatal = fx.run(apply=True)
    check("push/inflight-rerun-already-pushed",
          (rc, _result(reports, 1), fx.remote_ref("feat/x") == marker.get("new"),
           _has_marker(wt)),
          (0, "already-pushed", True, False))
    check("push/inflight-invariants", _invariants(fx), [])


def case_rename(tmp):
    # claude round-3 F4: a base-side `git mv utils/a.py utils/b.py`. With rename detection on,
    # the status parser would misread the rename record's original path (its own NUL-separated
    # field, here starting with "u") as a record and falsely refuse the PR; --no-renames keeps it
    # as separate add and delete records and the PR pushes.
    fx = _fixture(tmp, "rename")
    fx.write(fx.main, "utils/a.py", "A = 1\n")
    fx.commit(fx.main, "add utils")
    _git(fx.main, "push", "-q", "origin", "main")
    fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    _git(fx.main, "mv", "--", "utils/a.py", "utils/b.py")
    fx.commit(fx.main, "rename utils")
    _git(fx.main, "push", "-q", "origin", "main")
    rc, reports, _fatal = fx.run(apply=True)
    check("fixpoint/base-rename-pushed", (rc, _result(reports, 1)), (0, "pushed"))
    check("fixpoint/base-rename-invariants", _invariants(fx), [])


CASES = dict([
    ("config", case_config), ("absent", case_absent), ("frombase", case_frombase),
    ("dry", case_dry), ("happy", case_happy), ("carry", case_carry), ("discover", case_discover),
    ("ambiguous", case_ambiguous_worktree), ("branch", case_branch), ("badname", case_badname),
    ("audit", case_audit), ("conflict", case_conflict), ("regen", case_regen),
    ("reject", case_reject), ("idem", case_idem), ("remote-changed", case_remote_changed),
    ("push-unknown", case_push_unknown), ("generated-delete", case_generated_delete),
    ("author", case_author), ("rewind", case_rewind), ("timeout", case_timeout),
    ("observer", case_observer), ("symlink", case_symlink), ("tamper", case_tamper),
    ("filter", case_filter), ("eol", case_eol), ("lock-held", case_lock_held),
    ("fetch-failure", case_fetch_failure), ("state-mismatch", case_state_mismatch),
    ("cli", case_cli), ("refmap", case_refmap), ("basefetch", case_basefetch),
    ("leak", case_leak), ("partial", case_partial), ("signal", case_signal),
    ("crlf", case_crlf), ("latepush", case_latepush), ("rename", case_rename),
])


def _noop(*_args, **_kwargs):
    return None


def _reraise(exc):
    """The reverted push-indeterminate guard: decide from the error alone, without observing."""
    raise exc


# Red-on-revert: each guard, replaced in turn by its reverted form, must turn its named case red.
REVERTS = dict([
    ("config-fail-closed", (_noop, "config")),
    ("config-source", (lambda root, _sha: _read_text(os.path.join(str(root), CONFIG_PATH)),
                       "frombase")),
    ("branch-name", (_noop, "badname")),
    ("branch-match", (_noop, "branch")),
    ("source-conflict", (_noop, "conflict")),
    ("regenerate-exit", (_noop, "regen")),
    ("check-exit", (_noop, "regen")),
    ("fixpoint", (_noop, "regen")),
    ("reconcile", (_noop, "push-unknown")),
    ("scratch-setup", (_noop, "eol")),
    ("scratch-complete", (_noop, "partial")),
    ("generated-confined", (_noop, "symlink")),
    ("pushed-bytes", (_noop, "filter")),
    ("commit-parents", (_noop, "tamper")),
    ("commit-tree", (_noop, "tamper")),
    ("fresh-checkout", (_noop, "crlf")),
    ("push-lease", (lambda branch, old: [], "rewind")),
    ("push-indeterminate", (_reraise, "timeout")),
])


def _run_case(name, tmp):
    """One case; an unexpected exception fails that case and never crashes the suite."""
    try:
        CASES[name](tmp)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        _RECORDER["failures"].append("case %s raised %s: %s" % (name, type(exc).__name__, exc))


def _red_on_revert(tmp):
    """The guards whose reverted form left their named case green (an empty list passes)."""
    stale = []
    for name, (revert, case) in REVERTS.items():
        saved = GUARDS[name]
        GUARDS[name] = revert
        _RECORDER.update(executed=set(), list=[], failures=[])
        try:
            sub = tempfile.mkdtemp(prefix="red-%s-" % name, dir=tmp)
            _run_case(case, sub)
        finally:
            GUARDS[name] = saved
        if not _RECORDER["failures"]:
            stale.append("%s (case %s)" % (name, case))
    _RECORDER.update(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES)
    return stale


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read %s: %s" % (CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite %r in %s" % (
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(dict(format_version=1, suite=SUITE_ID, check_ids=EXECUTED), handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report %s: %s" % (
            report_path, exc), file=sys.stderr)
        return False
    return True


def self_test(red_on_revert=False, report_path=None):
    """Every case inside fixture_git_lifecycle (GIT_* scrubbed, scratch HOME, global and system
    config pinned to os.devnull, a PATH-front git wrapper), under one private temp directory."""
    sys.path.insert(0, str(ROOT / "tools"))
    from _git_fixture_env import fixture_git_lifecycle
    stale = []
    with fixture_git_lifecycle():
        with tempfile.TemporaryDirectory(prefix="merge-train-selftest-") as raw:
            tmp = os.path.realpath(raw)
            for name in CASES:
                _run_case(name, tmp)
            if red_on_revert and not FAILURES:
                stale = _red_on_revert(tmp)
    if not _write_report(report_path):
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: %s" % check_id)
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: %s" % check_id)
    for guard in stale:
        FAILURES.append("red-on-revert: reverting guard %s left its case green" % guard)
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: %d unique checks executed; execution set reconciled against "
          "tools/selftest_checks.toml%s" % (len(EXECUTED), (
              "; all %d guards in GUARDS went red when reverted" % len(REVERTS))
              if red_on_revert else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
