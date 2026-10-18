#!/usr/bin/env python3
"""Merge train (opt-in): refresh the head branch of eligible, selected open PRs targeting the
configured base branch, subject to the refusals documented below, taking the base side for the
declared GENERATED files only, regenerating and checking them, and refusing everything else. It
does nothing unless the project commits .aiqt/merge-train.toml on its base branch.

  merge_train.py --dry-run [--repo DIR] [--pr N]...   live dry run: report what --apply would do
  merge_train.py [--repo DIR] [--pr N]... --apply     live run: commit and push
  merge_train.py --self-test [--red-on-revert]        hermetic fixture self-test (local bare remote, fake gh)
  merge_train.py --execution-report ABS_PATH          the self-test, also writing the executed check ids
  merge_train.py                                      no arguments: the same hermetic self-test

A live run is selected only by an explicit option (--dry-run, --apply, --repo or --pr; without
--apply it is a dry run). With no arguments at all the tool runs its hermetic self-test and never
a live run: tools/merge_train.py is the registered runner of the merge-train-selftest suite, and
each git-fixture-env config-injection lane (the config-combined, config-malformed and
config-system suites) launches every suite runner with no arguments, under the caller's
injected (and deliberately poisoned) git configuration, and requires a hermetic exit
0, as the other suite runners give. A bare live run could not meet that: it fetches the real
remote, and its outcome depends on the remote's state (exit 2 while the base tip carries no
config, then gh and the open PRs once it does). Live runs read the operator's global and system
git configuration for the author-side reads and the transport commands (a credential helper
configured there keeps working for every fetch, ls-remote and push), and NOT in a checkout
holding PR content (see the configuration isolation below). Transport, plainly: the base
fetch, the PR-branch fetch, the ls-remote observer and the push all run FROM PRIVATE storage the
run owns (the per-run private base repository, or the per-PR scratch checkout, named with
--git-dir, a checkout's work tree pointed at an empty directory with --work-tree, and each
launched from that empty private directory, never from the storage itself; see _git_transport)
to the URL that
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

Every git launch the tool itself makes runs ONE git program, resolved before the run's first git
launch the way every configured command head is resolved (see _git_program and _resolve_program):
the first match on the sanitized PATH, refused by name when its realpath or its launch directory
lies inside the repository's toplevel, its git directories or any worktree around it (read from
the filesystem before the first launch, and checked again against the worktree list git
reports), and launched by that absolute path, never by a bare name a child would resolve in its
own working directory; every git launch reads that path's realpath again and refuses the git
when it changed or when it lies inside the launch's working directory (the private base
repository and each private checkout included). A run whose git is refused before discovery or
against the worktree list exits 2 before it takes the lock. The tool walks the PATH for a bare
name itself and refuses, as cannot-evaluate, at the first entry it cannot list ahead of a match
(for git, the run exits 2 before any git launch). Every child receives a narrower PATH than the
one the tool resolves through (see _child_env): no entry that resolves through /proc or /dev/fd,
where /proc/self/cwd and /dev/fd name the child's own working directory, and no entry inside
the child's working directory or a repository directory of the run (the toplevel, the git
directories, every worktree, the private base repository and each private checkout while it
exists), so no child resolves a program from PR content through a PATH entry; and every
transport command (a fetch, a push, an ls-remote, a --shared clone) is launched from an empty
private directory (see _git_transport), so neither its git nor a program that git starts (ssh,
a credential or remote helper) resolves one through its working directory either. A program
the tool launches (gh, or a configured command) that runs git itself resolves it on that
narrowed PATH, outside the pinned-git rule. Disclosed residual: before git's first launch the
confinement reads the repository layout from the filesystem, so a git inside a layout that read
does not model (a core.worktree setting, for one) runs for the three discovery reads before the
check against the worktree list git reports refuses it.

Configuration isolation, stated exactly. Every git child the tool launches through _git with
its working directory inside a private checkout or a fresh checkout (see _PR_DIRS and _git:
the merge, every checkout, diff, status, add, ls-files, ls-tree, cat-file, write-tree,
commit-tree, rev-parse, rev-list, merge-base and config call there) runs with
GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device and GIT_CONFIG_NOSYSTEM=1. The rule
is that location, so it does not cover a git launched outside _git, which the tool does not
make, nor one launched from elsewhere that names a checkout with --git-dir or --work-tree:
transport commands below (the PR-branch fetch and the push among them) do that by design,
naming the private checkout's git directory with the empty transport directory as their work
tree, and read the operator's configuration for the reasons given there. The operator's
global and system configuration files, and every file they include, are not read by those
children: no merge driver, textconv or filter defined there can be selected by an attribute
the PR commits (a command named relative to the working directory would otherwise run a
program the PR supplies), and no include, submodule recursion, signing program or helper set
there applies. _changed_lines also passes --no-textconv. A behaviour change follows: the
operator's merge and diff preferences (merge.renames, merge.directoryRenames and
diff.algorithm, for some) no longer apply in those checkouts, so git's defaults decide how
the merge resolves, which conflicts it reports and what review_carry compares; a conflict
outside the generated files is still refused, never pushed. What such a child
reads instead is passed explicitly or set locally by the tool: the maintenance pins in option
position on every launch (gc.auto=0, gc.autoDetach=false, maintenance.auto=false), the commit
identity in the environment for the merge and commit-tree, and the checkout's local
configuration, which is the --shared clone's own entries plus, in the private checkout,
core.autocrlf=false, core.eol=lf, core.hooksPath at the null device and core.fsmonitor=false
(see guard_scratch_setup), and in the fresh checkout core.hooksPath at the null device,
core.fsmonitor=false and the operator's own core.autocrlf and core.eol values when set (see
guard_fresh_checkout). The children that still read the operator's configuration, and why:
the author-side reads (_git_author), none of which merges, diffs, checks out or filters; the
base fetch, the PR-branch fetch, the ls-remote observer and the push, which need its
credential helpers, ssh command, http settings and URL rewrites and run from the empty
transport directory with no work tree of PR content, so no PR attribute is read and a
command named relative to the working directory finds nothing there (the push also passes
--no-recurse-submodules); the two --shared clones, which check out nothing and run from that
same directory; the read of the operator's core.autocrlf and core.eol, from that same
directory; and the private base repository's own calls (its init, its hooks setting, its
negotiation refs and its reads of the base tip), which hold no PR content. Remaining route, by
disclosed design: the configured regenerate and check commands run the merged tree's
generators, which may include the PR's own code, and a git such a command runs itself reads
the operator's configuration.

The tool NEVER mutates the author's working tree, index, HEAD or any local branch: that capability
was removed, not guarded. Per open PR, in PR-number order, every
merge, conflict resolution, regeneration, check and commit happens in a PRIVATE scratch checkout
the run creates for that PR (a --shared clone of the repository, detached at the PR's remote head)
and removes in a finally entered as soon as the directory exists, so a failure in the clone or in
the scratch setup cannot leak it; a SIGTERM, SIGHUP or SIGQUIT during a live run is converted into that
same cleanup, and a SIGINT unwinds through it as KeyboardInterrupt (the in-flight git or generator
child is terminated with its WHOLE process group, SIGTERM first so git removes its own lock files,
then SIGKILL after a short grace, and reaped, and the group is then probed: a grandchild that
ignored the SIGTERM is SIGKILLed even when the direct child exited first, and a group that still
has members when the bounded wait expires (unreaped zombies held for a subreaper, members in
uninterruptible sleep, D state) or that the probe cannot evaluate (any error other than ESRCH,
such as EPERM for a member this user cannot signal) is REPORTED on stderr, never silently
claimed gone; every
cleanup finally runs with every signal in UNWIND_SIGNALS blocked, so a second signal cannot abort a removal
half-way and is re-delivered when that cleanup ends; the scratch and the private base repository
are removed, the lock is released) and the process then exits with the
conventional killed-by-signal status (an inherited SIG_IGN disposition, as under nohup, is left
alone; a group-wide SIGKILL from a supervisor, such as timeout -k or kill -9 on the group, cannot
be handled and leaves children running in their own sessions); checkout filters, text/eol/autocrlf conversion, ident expansion, the
working-tree encoding, hooks (core.hooksPath pinned to the null device) and fsmonitor are disabled
there for every git call, and no such call reads the operator's global or system configuration.
The author's worktree is only READ: the run locates the worktree that has
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
runs the check commands (each regenerate and check command is accepted only with exit 0 and no
fault on stderr, such as a Python traceback or an "Exception ignored" line from an atexit callback,
since such a fault leaves the exit status 0), proves nothing outside the generated set differs from
the automatic merge,
then proves that EVERY blob of the validated tree is byte-identical to the private-checkout file
the checks read (what was validated is what is pushed; a clean filter or autocrlf transform cannot
smuggle unvalidated bytes), commits with `git commit-tree` against that validated tree (no commit
hook runs), verifies the new commit's first parent is the exact PR head this run validated (which
by itself makes that head an ancestor of the new commit), checks the candidate in a SECOND, normal
private checkout (the committed attributes honoured with the operator's core.autocrlf and core.eol
carried over, the rest of the operator's configuration not read, no local configuration carried
over, hooks disabled) and refuses normal-checkout-failed
when that checkout reports any error or any declared check fails there (regeneration with
conversions disabled must not publish a
commit whose own fresh checkout fails its generators), and pushes from the private checkout with an
exact-value
compare-and-set (--force-with-lease=refs/heads/<branch>:<the head this run validated>, a 40- or
64-hex object id, which can only narrow what a plain push would accept) to the PR's own head
branch, so a branch rewound, advanced or deleted since the validation is refused by the remote,
never overwritten. One observer reads the remote after every push attempt, a timed-out or failed
one included; a push whose outcome cannot be observed (a push timeout, an observer timeout, an
unreadable remote, and a timed-out push with the remote still at the old head, which may still be
in flight in the remote's receive-pack) is INDETERMINATE: a small marker is kept so the retry can
classify it, the author's working tree, index, HEAD and local branches are unchanged, and the push
itself may have landed on the remote. There is no rollback machinery, because nothing local is
mutated and so there is nothing to roll back.

A DRY RUN (--dry-run, or any live selection without --apply) prepares and validates the SAME
candidate in the same private scratch checkout: the branch and head agreement, the
checkout-completeness probe, the merge and its conflict policy, regeneration, the declared checks
and the pushed-bytes, commit-tree, parent and fresh-normal-checkout guards all run exactly as under
--apply, and the run stops before the retry marker and the push. What a dry run still WRITES,
stated exactly: the lock file (after repository discovery the run opens it and attempts
acquisition), and the objects the private
scratch's merge, regeneration and commit-tree work writes through the SHARED object store, whose
byte-identical writes may refresh the mtime of an already-present author-side object (the
disclosed residual above); no retry marker, no push, no ref, and nothing else outside the private
scratch. would-merge is therefore reported only for a candidate that passed that whole
validation; a conflict or a failing check is refused with the same status --apply would give
(a would-merge reported before that validation ran would claim a result nothing had checked; the
lock file and the shared-object writes are named above so a dry run's writes are stated in full).
Dry/apply agreement is pinned per refusal class by the *-dry-agrees vectors, stated exactly:
source-conflict, regenerate-failed, check-failed, regenerate-not-fixpoint, undeclared-write, the
child-fault routes, generated-symlink, normal-checkout-failed, the abnormal-merge-exit refusals
and commit-mismatch (the latter through injected wrong-tree, swapped-tree and dropped-parent
candidates). Two differences remain by construction, not by vector gap: the push-side classes
(push-rejected, remote-changed, push-unknown and the push timeout) exist only under --apply,
because a dry run stops before the push; and a present retry marker refuses a dry run
(state-mismatch) where --apply reconciles it.

The config is read from the fetched base tip (`git show <M>:.aiqt/merge-train.toml`), never from a
PR branch or the checkout; the [busy] table is still accepted, and ignored, for config
compatibility. The branch pushed to is the PR's own head branch as gh names it, re-confirmed before
the push; the tool takes no branch-name argument. The tool ships with the pack; this repository's
own .aiqt/merge-train.toml does not (.aiqt/core/ownership.toml excludes it), because its presence
is what enables the tool. An adopting project commits its own.

Stdout carries one JSON line per PR (schema merge-train/1); stderr carries one human line per PR.
Exit codes: 0 every PR current, pushed, already pushed or (dry run) validated as mergeable; 1 any
PR refused or skipped, an explicitly requested PR (--pr) absent from the open-PR list included; 2
the run failed closed and touched nothing (no config, malformed config, lock held, gh or fetch
failure, a possibly truncated PR list, bad arguments).

THREAT MODEL (cooperative): agents on a single-operator host. The repository's local config, the
operator's global and system config, hooks and the object store are trusted; the tool guards
against accidents and odd local state, not hostile repository state. Trusted configuration is
still not read by the tool's git children in the private and fresh checkouts (see the
configuration isolation above), because a trusted command named relative to the working
directory runs whatever program the PR places there. The author's own PR worktree also holds
PR content and its author-side reads do read that configuration; they merge, diff, check out
and filter nothing, and hooks and fsmonitor are pinned off for them. Disclosed residuals: fork PRs
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
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: merge_train.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import fcntl
import gettext
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sysconfig
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "merge-train/1"
CONFIG_PATH = ".aiqt/merge-train.toml"
MARKER_NAME = "merge-train.json"
LOCK_NAME = "merge-train.lock"
TRANSPORT_DIR = "transport"
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

# Git's global (pre-subcommand) options, for resolving which subcommand an argv names: flags that
# take no value, options whose value is the NEXT word, and options that take a value only after
# "=". An argv whose subcommand cannot be resolved through these (an unknown or truncated global
# option, an option-only argv) is treated as FORBIDDEN: the audit fails closed, so a forbidden
# push or reset behind "-c k=v" or "-C dir" can never read as allowed.
GIT_GLOBAL_FLAGS = frozenset((
    "-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
    "--no-optional-locks", "--no-lazy-fetch", "--no-advice"))
GIT_GLOBAL_VALUED = frozenset(("-c", "-C", "--config-env", "--git-dir", "--work-tree",
                               "--namespace", "--attr-source"))
GIT_GLOBAL_EQUALS = frozenset(("--config-env", "--git-dir", "--work-tree", "--namespace",
                               "--attr-source", "--exec-path", "--list-cmds"))

# Configured-command heads guard_config_fail_closed refuses outright, stated exactly: these heads
# execute their own argument vector (env and the exec-wrapper family) or a shell command string,
# so a trusted-but-mistaken config could hand git an unpinned argv one hop past the head check.
CONFIG_LAUNCHERS = frozenset((
    "env", "sh", "ash", "bash", "dash", "ksh", "mksh", "zsh", "csh", "tcsh", "fish",
    "busybox",
    "xargs", "nohup", "setsid", "nice", "ionice", "stdbuf", "timeout", "doas", "sudo"))
# Interpreter heads whose leading option words are scanned in a configured command. For every
# family but python, the words are parsed against
# _INTERPRETER_GRAMMAR: the first word the grammar does not consume as an option or an operand
# is the script, and later words belong to it. A python-family head takes no grammar walk at
# all: it is accepted only in the ONE pinned shape (see guard_config_fail_closed and
# _PYTHON_SCRIPT_OPTIONS). A build suffix after the version names the same interpreter and is
# scanned the same way: free-threaded t, debug d and pymalloc m in any combination
# (python3.13t, python3.13td, python3.7m, python3.7dm), with or without a distribution -dbg
# tail (python3.12-dbg, python3.13t-dbg) (a pattern accepting one suffix letter only would let
# a td, m or dm build name SKIP the scan and carry inline code around it).
# Matched case-insensitively and with an optional .exe tail (otherwise a Python3 or
# python3.exe spelling would miss the match and fall through to the trusted-config residual,
# outside every interpreter rule), so no recognized interpreter spelling dodges its
# grammar by case or suffix.
CONFIG_INTERPRETERS = re.compile(
    r"\A(python|pypy|perl|ruby|node|nodejs|lua)[0-9.]*[tdm]*(-dbg)?(\.exe)?\Z", re.IGNORECASE)
# The per-interpreter option grammar for the NON-python families, fail closed: a 3-tuple of
# exact no-operand flags, exact options whose operand is the NEXT word, and prefixes whose
# operand is attached in the same word. ANY other leading option word is refused as
# unclassifiable: every inline-code option (perl, ruby and lua -e, perl -E, node -e, -p and
# --eval), every bundled cluster and every unknown option lands there, so an option operand or
# a cluster tail can never be misread as a harmless script path. Over-refusal of an exotic but
# legitimate option (a flag cluster, perl -s, node -i) is the accepted fail-closed boundary.
# The python family is deliberately ABSENT from this table: a
# configured command whose HEAD is a recognized python-family spelling is accepted only as
# <interpreter> -I -B <repo-relative .py path present in the base tip tree as a regular file>,
# and python -m draws the module-naming diagnostic in the spellings the module scan recognizes,
# with every other python form refused by the one-shape rule (what -m NAME imports cannot be
# proved from tree membership, because under -I a
# standard-library name wins over a checkout file and a package needs more than __init__.py,
# so the capability is removed instead of modelled; see guard_config_fail_closed).
_INTERPRETER_GRAMMAR = dict(
    perl=(frozenset(("-a", "-l", "-n", "-p", "-T", "-t", "-U", "-w", "-W", "-X")),
          frozenset(("-I",)), ("-I",)),
    ruby=(frozenset(("-a", "-l", "-n", "-p", "-w")), frozenset(("-I", "-r")), ("-I",)),
    node=(frozenset(("--no-deprecation", "--no-warnings")), frozenset(("-r", "--require")),
          ("--require=",)),
    lua=(frozenset(("-v", "-E", "-W")), frozenset(("-l",)), ("-l",)),
)
_INTERPRETER_FAMILY = dict(pypy="python", nodejs="node")

# The exact leading option words of the ONE accepted python command shape: -I (isolated mode:
# no current or script directory on sys.path, no user site
# packages, no PYTHON* environment) and -B (no bytecode writes). The options pin the INITIAL
# launch only: the selected script runs first, and it can itself import further checkout code
# or extend sys.path (the live generators do), inside the trusted-config residual stated in
# guard_config_fail_closed: the named script is NOT the only checkout code the launch can run.
_PYTHON_SCRIPT_OPTIONS = ["-I", "-B"]


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


class _AuditRefused(AssertionError):
    """Raised by _git for an argv its audit refuses: a DEDICATED type, so the one handler that
    converts the refusal into a result shape (case_badname) can
    never match an unrelated AssertionError that happens to carry the same words."""


def _now():
    return time.strftime("[%Y-%m-%dT%H:%MZ]", time.gmtime())


def _clean_env(extra=None):
    """The calling environment with every GIT_-prefixed variable dropped (no repository-selecting
    or config-injecting variable reaches git), plus explicit additions, then pinned to the C
    locale (LC_ALL=C set and LANGUAGE dropped) AFTER those additions, so no caller can override
    the pin through extra. Both funnels launch through this
    environment, so git children AND the configured regenerate and check commands _run_external
    runs all receive LC_ALL=C with LANGUAGE unset: git translates the "fatal:" and "error:"
    prefixes the merge diagnostic scan matches through its shipped message catalogs, so every
    child whose stderr the train parses must emit untranslated diagnostics. LANGUAGE is
    dropped as defence in depth, not from an observed need: on the git
    and glibc gettext observed here, LC_ALL=C alone already selects the untranslated messages
    even with LANGUAGE set (gettext consults LANGUAGE only once the locale enables translation
    at all), but LANGUAGE is the highest-priority message-language selector when translation IS
    enabled, so the pin removes it instead of relying on that observation. The PATH both
    funnels resolve their program through is rebuilt LAST, after the
    explicit additions, from the caller value (os.defpath when PATH is unset) with every empty,
    dot or other relative entry removed: a cwd-resolving entry would resolve a bare configured
    head in the child working directory, which for the regenerate and check commands is the
    merged scratch checkout, content the PR itself supplies. The
    rebuilt PATH is the FIRST layer only: _run_external resolves every program head against it
    and refuses, by name, an empty sanitized PATH, an unresolvable head, a PATH entry it cannot
    list ahead of a match and a head whose realpath or launch directory lies inside the
    checkout or a repository directory, then launches the resolved absolute launch path with
    its checked realpath as the file exec-ed; git itself is resolved the same way, once (see
    _resolve_program and _git_program). The CHILD receives a narrower PATH still (see
    _child_env)."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or dict())
    env.pop("LANGUAGE", None)
    env["LC_ALL"] = "C"
    env["PATH"] = os.pathsep.join(
        entry for entry in env.get("PATH", os.defpath).split(os.pathsep)
        if os.path.isabs(entry))
    return env


# The directories under which a path resolves through per-process state: /proc/self/cwd,
# /proc/<pid>/cwd and /dev/fd (itself a symlink into /proc/self/fd) name whatever the RESOLVING
# process has there, so one PATH entry can name a different directory in each child.
_PROCESS_STATE_ROOTS = ("/proc", "/dev/fd")

# The repository directories of the current run (the toplevel and every directory the
# filesystem records around it, the git directories, every worktree git lists, the private
# base repository and each private checkout while it exists), set by run_train for the length
# of a run and restored on exit (see _child_env).
_RUN_DIRS = []

# The directories holding PR content while they exist: each private checkout's directory and
# each fresh checkout's directory, registered by _process_pr and guard_fresh_checkout. Every
# git child launched with its working directory inside one runs with _PR_GIT_ENV (see _git).
_PR_DIRS = []

# The operator's global and system configuration files, and every file they include, are not
# read by a git child working on PR content: an attribute the PR commits can otherwise select a
# merge driver, textconv or filter they define, and a command named there relative to the
# working directory runs a program the PR supplies (see the configuration isolation in the
# module docstring).
_PR_GIT_ENV = dict(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1")

# The operator settings a fresh checkout carries over (see guard_fresh_checkout): values that
# select a built-in conversion, never a command, each with the git config options that read
# it the way git itself does. git reads core.autocrlf as "input" or a boolean, a valueless
# key being true, so it is read as bool-or-str: every boolean spelling becomes true or false
# (a plain read prints a valueless key as the empty string, which the fresh checkout would
# then read as false) and any other value is carried as written. core.eol is carried as
# written.
_FRESH_CHECKOUT_KEYS = (("core.autocrlf", ("--type=bool-or-str",)), ("core.eol", ()))


def _names_process_state(entry):
    """Whether resolving the absolute PATH entry, component by component with every symlink
    followed, reaches a directory in _PROCESS_STATE_ROOTS (or follows more than 40 symlinks):
    such an entry can name a child's own working directory whatever it resolves to in this
    process. A component readlink cannot read as a link is taken as written: it is no link, it
    does not exist, or it lies under a directory this user cannot search, through which a child
    running as this user resolves nothing either."""
    resolved, parts, hops = os.sep, entry.split(os.sep), 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        resolved = os.path.dirname(resolved) if part == ".." else os.path.join(resolved, part)
        if any(_inside(resolved, root) for root in _PROCESS_STATE_ROOTS):
            return True
        try:
            target = os.readlink(resolved)
        except OSError:
            continue
        hops += 1
        if hops > 40:
            return True
        resolved = os.sep if os.path.isabs(target) else os.path.dirname(resolved)
        parts = target.split(os.sep) + parts
    return False


def _child_env(extra=None, cwd=None, confine=()):
    """The environment a child is LAUNCHED with: _clean_env's, with its PATH narrowed so that
    neither the child nor a program it starts in turn (ssh or a credential or remote helper git
    runs, git or any program a configured command or gh runs) can resolve a program from
    repository or checkout content through a PATH entry. Every entry that resolves through
    per-process state (see _names_process_state; /proc/self/cwd and /dev/fd name the child's
    own working directory there) is dropped, and so is every entry whose realpath, or whose
    absolute text (normalized, with no symlink followed), lies inside the child's working
    directory, a directory in confine or a repository directory of the run (_RUN_DIRS), each
    such directory compared by its realpath and by its absolute text: an entry that names a
    run directory in its text but resolves outside it through a symlink the directory holds
    is dropped too, because the child resolves that symlink again when it walks its PATH. The
    one error handled is OSError from computing those forms, which for a relative directory
    means os.getcwd failed (the working directory was removed): every entry is then dropped.
    os.path.realpath raises nothing for a component this process cannot search (it returns
    the path resolved up to there), so such a directory is compared by that partial result.
    A PATH with no entry left names the null device, never the empty string, which execvp,
    the shell and subprocess read as the working directory. The tool's own resolution
    (_resolve_program) walks _clean_env's PATH, which keeps these entries, so a head or git
    found there is still refused by name."""
    env = _clean_env(extra)
    try:
        bases = [form for base in [cwd] + list(confine) + _RUN_DIRS if base
                 for form in (os.path.realpath(str(base)), os.path.abspath(str(base)))]
        kept = [entry for entry in env["PATH"].split(os.pathsep)
                if entry and not _names_process_state(entry)
                and not any(_inside(form, base) for base in bases
                            for form in (os.path.realpath(entry), os.path.normpath(entry)))]
    except OSError:
        kept = []
    env["PATH"] = os.pathsep.join(kept) or os.devnull
    return env


# How long a terminated child's process group is given to exit on SIGTERM before SIGKILL.
TERM_GRACE = 5.0

# Every signal the run unwinds on: SIGINT (Python's own KeyboardInterrupt) plus SIGTERM, SIGHUP
# and SIGQUIT (converted into _Signalled by run_train). ONE constant, used by the launch mask in
# _run_child, by _cleanup's mask, by the child-side mask restore and by the self-test's
# launch-window and second-signal cases, so the set cannot drift apart per call site (with
# SIGINT missing from the launch mask, a KeyboardInterrupt at the launch boundary could reach
# the launch-failure handler with no handle under cleanup and leak the just-created session).
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


def _group_probe(pgid):
    """None when the process group is GONE, else why it may still have members. ONLY ESRCH means
    gone (reading every OSError as gone would let an EPERM from a member this user cannot
    signal silence the remnant report); a successful probe (a
    not-yet-reaped member counts) and any other error, EPERM included, mean "cannot claim gone"."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return None
    except OSError as exc:
        return "may still have members (the probe failed: %s)" % exc
    return "still has members"


def _group_alive(pgid):
    """Whether the process group may still have a member (see _group_probe: ESRCH alone is gone)."""
    return _group_probe(pgid) is not None


def _end_child(proc, group):
    """Terminate a child and reap it: SIGTERM first (git removes its own *.lock files on SIGTERM,
    never on SIGKILL), a bounded grace, then SIGKILL for whatever ignored the SIGTERM. With group
    True the child's WHOLE process group is signalled and then probed (the child was
    started with start_new_session=True, so its pid names the group and every grandchild that
    stayed in it is included; a grandchild that itself calls setsid starts a new session, ESCAPES
    the group and can be neither ended nor reported here): the direct child exiting says nothing
    about a grandchild
    that ignored the SIGTERM, so after the direct child is reaped the group is probed, given the
    bounded grace to finish its own SIGTERM handling, SIGKILLed if anything remains, and probed
    again within the same bound. What that final probe CONFIRMS is only this: either the group
    was gone within the bound (the probe got ESRCH), or a stderr line reports the process group
    that remains or could not be probed (its members can be unreaped zombies, e.g. SIGKILLed
    descendants held for a subreaper that has not reaped them yet, or members in uninterruptible
    sleep, D state; a probe error such as EPERM, a member this user cannot signal, is reported
    with that error and never read as gone); the group being GONE on return is NOT guaranteed. That is the dying-run path and the regenerate-or-check TIMEOUT
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
        state = _group_probe(proc.pid)
        if state is not None:
            print("%s merge-train: process group %d %s after SIGKILL; its members may be unreaped "
                  "zombies (held for a subreaper), members in uninterruptible sleep (D state) or "
                  "members this user cannot signal; leaving them behind"
                  % (_now(), proc.pid, state), file=sys.stderr)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def _run_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
    """The single child-process funnel under _git and _run_external: executable, when given, is
    the file the child execs while argv[0] stays the launch path (both from _resolve_program,
    the funnels' only source of either). Every child starts in its OWN
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
    fork and exec), so git and the generators see the signals unblocked. That is the contract
    even for a caller that blocked one of these four on purpose: the child inherits every OTHER
    signal the caller had blocked unchanged (SIG_UNBLOCK, never SIG_SETMASK), and these four are
    ALWAYS unblocked in the child, because _end_child's SIGTERM-first termination relies on git
    acting on SIGTERM to remove its own lock files; the caller's own mask in this process is
    restored unchanged. An unwind ends the whole
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
                                start_new_session=True, preexec_fn=_child_mask,
                                executable=executable)
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


def _config_pair_forbidden(pair, env=False):
    """A -c or --config-env payload whose KEY can redefine what a later argv word MEANS or where
    a push GOES, stated exactly: alias.* (the resolved subcommand would dispatch elsewhere);
    push.*; include.* and includeIf.* (an included file can define any of the others);
    remote.<name>.mirror, .push, .pushurl, .url and remote.pushDefault; branch.<name>.pushRemote;
    and url.<base>.insteadOf and .pushInsteadOf (the LISTED keys, a deliberate deny-list; git
    documents further routing keys,
    branch.<name>.remote and remote.<name>.receivepack among them, that stay outside it, inside
    the trusted-argv residual the predicates already accept for a harmless resolved
    subcommand). Git reads section and variable names case-insensitively; the subsection
    between them is not inspected. The key ends at the FIRST "=" of a -c payload but at the
    LAST "=" of a --config-env payload, whose trailing word names an environment variable,
    never part of a value, so a subsection carrying "=" cannot shift the variable name out of
    the key (splitting every payload at the first "=" would read the key of
    --config-env=remote.a=b.pushurl=VAR as remote.a); env says
    which form pair came from. The tool's own -c keys (gc.*, maintenance.*, core.*,
    protocol.*) are outside these classes and stay allowed (the predicates read what a -c
    value DOES, not only which subcommand follows it)."""
    key = (pair.rsplit("=", 1)[0] if env else pair.split("=", 1)[0]).split(".")
    section, var = key[0].lower(), key[-1].lower()
    if section in ("alias", "push", "include", "includeif"):
        return True
    if section == "remote" and var in ("mirror", "push", "pushurl", "url", "pushdefault"):
        return True
    if section == "branch" and var == "pushremote":
        return True
    return section == "url" and var in ("insteadof", "pushinsteadof")


def _forbidden(args):
    """The argv audit enforced on every git call, kept in exact agreement with the self-test's
    independently written _audit_violation: no rebase, cherry-pick, stash, clean, gc, worktree
    prune, and no reset of ANY kind (the tool has nothing local to reset); for push, no force,
    delete, mirror, all or prune option in long, abbreviated (git accepts unique prefixes) or
    bundled short form (-uf, -fu), no lease other than the one exact-value compare-and-set form
    (PUSH_LEASE, 40- or 64-hex), and no refspec other than a plain name or the exact
    <hex>:refs/heads/<branch> form away from the base (no + force spec, no :delete spec, no
    src:dst alias). The subcommand is resolved after git's global options (-c k=v, -C dir,
    --git-dir, --work-tree and the rest git accepts before a subcommand; see GIT_GLOBAL_FLAGS,
    GIT_GLOBAL_VALUED and GIT_GLOBAL_EQUALS), and an argv whose subcommand cannot be resolved is
    forbidden outright, never read as harmless. A -c or
    --config-env payload whose key falls in _config_pair_forbidden is forbidden even when the
    resolved subcommand is harmless. After the subcommand, the
    first non-option word is the repository (a URL may carry a colon)."""
    words = list(args)
    i = 0
    while i < len(words) and words[i].startswith("-"):
        w = words[i]
        if w in GIT_GLOBAL_FLAGS:
            i += 1
        elif w in GIT_GLOBAL_VALUED:
            if (w in ("-c", "--config-env") and i + 1 < len(words)
                    and _config_pair_forbidden(words[i + 1], env=w == "--config-env")):
                return True
            i += 2
        elif "=" in w and w.split("=", 1)[0] in GIT_GLOBAL_EQUALS:
            if (w.split("=", 1)[0] == "--config-env"
                    and _config_pair_forbidden(w.split("=", 1)[1], env=True)):
                return True
            i += 1
        else:
            return True
    if i >= len(words):
        return True
    words = words[i:]
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


def _git(cwd, *args, extra_env=None, timeout=GIT_TIMEOUT, ok=(0,), push=False, confine=()):
    """The single pinned git funnel: the ONE git program _git_program resolved (launched by its
    absolute launch path, never a bare name a child resolves; refused, as git-failed, when its
    launch path no longer resolves to the realpath it was checked at, or when it lies inside
    this launch's working directory or a directory in confine), auto-maintenance pinned off in
    option position, scrubbed GIT_*, the narrowed child PATH (see _child_env), a timeout. A
    launch whose working directory lies inside a directory in _PR_DIRS (compared by realpath,
    and taken as inside when its realpath cannot be computed) also receives _PR_GIT_ENV, after
    extra_env, so no caller can lift it: the operator's global and system configuration is
    not read there. The rule is the launch's working directory, never a caller's choice, so a
    later call through _git with its working directory in such a checkout is isolated as
    well; a git launched outside _git, or from elsewhere naming a checkout with --git-dir or
    --work-tree, is not.
    Returns (returncode, stdout bytes, stderr text); raises Refuse("git-failed") on a refused
    git, a timeout and an exit code outside ok when ok is not None. The audit records every
    attempted argv, a refused one included."""
    GIT_AUDIT.append(list(args))
    if _forbidden(args):
        raise _AuditRefused("forbidden git operation refused by the argv audit: %r" % (args,))
    launch, real, refusal = _git_program((cwd,) + tuple(confine))
    if launch is None:
        raise Refuse("git-failed", refusal)
    argv = [launch, "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
    argv += list(args)
    try:
        here = os.path.realpath(str(cwd))
        isolated = any(_inside(here, os.path.realpath(base)) for base in _PR_DIRS)
    except OSError:
        isolated = True
    if isolated:
        extra_env = dict(extra_env or dict())
        extra_env.update(_PR_GIT_ENV)
    try:
        proc = _run_child(argv, cwd=str(cwd), env=_child_env(extra_env, cwd, confine),
                          timeout=timeout, push=push, executable=real)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refuse("git-failed", "git %s: %s" % (" ".join(args[:2]), exc))
    if ok is not None and proc.returncode not in ok:
        raise Refuse("git-failed", "git %s exited %d: %s" % (
            " ".join(args[:2]), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip()[:300]))
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def _git_text(cwd, *args, **kw):
    return _git(cwd, *args, **kw)[1].decode("utf-8", "replace").strip()


def _git_transport(where, *args, git_dir=None, checkout=None, confine=(), **kw):
    """A git transport command (a fetch, a push, an ls-remote or a --shared clone) launched FROM
    where, an empty directory inside the run's private directory that nothing writes, never
    from the repository or checkout it works on: git_dir, when given, names that repository
    (--git-dir), and checkout, when given, is its work tree, which git is pointed away from
    (--work-tree=where). Both are confined for the launch beside where and confine (see _git
    and _child_env), so neither git nor a program it starts (ssh, a credential helper, a remote
    helper) can resolve a program from PR content through its working directory or a PATH
    entry: /proc/self/cwd names the empty directory for them, and the PATH they receive keeps
    no entry inside that repository, its checkout or a repository directory of the run."""
    options = []
    if git_dir is not None:
        options.append("--git-dir=%s" % git_dir)
    if checkout is not None:
        options.append("--work-tree=%s" % where)
    dirs = tuple(str(path) for path in (git_dir, checkout) if path is not None)
    return _git(where, *(options + list(args)), confine=dirs + tuple(confine), **kw)


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
    writes through the shared store, never from these reads; see _scratch_checkout). The git
    run here is _git's pinned program (see _git_program), so a git that a PATH entry such as
    /proc/self/cwd names in the author's worktree never runs here."""
    env = dict(GIT_NO_LAZY_FETCH="1")
    env.update(extra_env or dict())
    return _git(cwd, *(_AUTHOR_GIT_CONFIG + args), extra_env=env, timeout=timeout, ok=ok)


def _git_author_text(cwd, *args, **kw):
    return _git_author(cwd, *args, **kw)[1].decode("utf-8", "replace").strip()


def _inside(path, base):
    """Whether path is base or lies below it, both given in the same form (realpaths, or
    normalized absolute text; a base of "/" holds every path, which a plain base + os.sep prefix
    test would miss)."""
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _confinement_refusal(head, launch, real, confine):
    """The refusal naming head when the realpath it execs, or the directory its launch path
    names, lies inside any directory in confine (each compared by realpath), else None. A
    confined directory whose realpath cannot be computed at all refuses too (fail closed):
    that is a relative directory once os.getcwd fails, as when the working directory was
    removed. That is the whole fail-closed scope: os.path.realpath raises nothing for a
    component this process cannot search, it returns the path resolved up to that component
    with the rest as written, and the confined directory is then compared by that partial
    result, not refused. The launch
    directory counts as well as the file: a program can read its own location from argv[0] (a
    venv interpreter reads the pyvenv.cfg beside it, and site then runs the .pth files in that
    venv's site-packages, even under -I), so a launch path inside a confined directory runs
    content from there even when the file it execs lies outside."""
    for base in confine:
        try:
            base_real = os.path.realpath(str(base))
        except OSError as exc:
            return ("program %r cannot be confined, so the launch is refused: the realpath of "
                    "%r cannot be read (%s)" % (head, str(base), exc))
        for what, path in (("realpath", real), ("launch directory", os.path.dirname(launch))):
            if _inside(path, base_real):
                return ("program %r resolves to repository or checkout content, which never "
                        "runs as a configured program head or as git, so the launch is "
                        "refused: its %s %r lies inside %r" % (head, what, path, base_real))
    return None


def _launch_refusal(head, launch, real, confine):
    """The refusal naming head, else None, checked at EVERY launch of a resolved program: the
    realpath of its launch path is read AGAIN and must still be the realpath that was checked
    (so the file argv[0] names is the very file exec-ed, and a launch path re-pointed at
    another program since the check is refused, never launched beside the checked file), and
    neither that realpath nor the launch directory may lie inside a directory in confine (see
    _confinement_refusal)."""
    again = os.path.realpath(launch)
    if again != real:
        return ("program %r is refused: its launch path %r now resolves to %r, not to the "
                "realpath %r it was checked at" % (head, launch, again, real))
    return _confinement_refusal(head, launch, real, confine)


def _path_lookup(name, path_value):
    """(match, None) for the first executable match of the bare name on path_value, (None, None)
    when no entry holds one, or (None, reason) refusing the lookup as cannot-evaluate. The walk
    is this tool's own, entry by entry in PATH order. An entry that does not exist or is not a
    directory holds no program and is passed over. An entry this process cannot list (any other
    error, EACCES on an unreadable directory among them), and a name an entry lists that cannot
    be stat-ed (EACCES, a symlink loop), refuse the lookup at that entry, before any later
    entry is consulted: shutil.which passes such an entry over and takes a later match, so a
    program the earlier entry may hold would never be evaluated. A match is a name that is not
    a directory and carries the execute bit for this process, shutil.which's rule."""
    for entry in path_value.split(os.pathsep):
        try:
            os.listdir(entry)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            return None, ("program %r cannot be evaluated, so the launch is refused: PATH entry "
                          "%r cannot be listed (%s)" % (name, entry, exc))
        candidate = os.path.join(entry, name)
        try:
            mode = os.stat(candidate).st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            return None, ("program %r cannot be evaluated, so the launch is refused: %r on the "
                          "PATH cannot be read (%s)" % (name, candidate, exc))
        if not stat.S_ISDIR(mode) and os.access(candidate, os.X_OK):
            return candidate, None
    return None, None


def _resolve_program(head, env, confine):
    """(launch path, realpath, None) for a program head, or (None, None, reason) refusing the
    launch by name. The CANDIDATE is an absolute head exactly as given or, for a bare head, the
    first match _path_lookup finds on the SANITIZED PATH of env (see _clean_env; an empty
    sanitized PATH, an entry that cannot be listed ahead of the match and an unresolvable head
    are refused, never handed to the child's own PATH walk); any other head (a relative path)
    is refused outright. The candidate's realpath must
    be an executable regular file, and neither it nor the candidate's directory, by realpath,
    may lie inside a directory in confine (see _confinement_refusal), so a /proc/self/cwd PATH
    entry, a symlink alias and every other absolute alias of confined content are refused
    before anything runs. The LAUNCH path is the candidate with its directory resolved and its
    final name kept, and its realpath is read again and must be the checked realpath (see
    _launch_refusal); the funnels start the child with it as argv[0] and with the checked
    realpath as the file to exec (subprocess executable=). What the launch path as argv[0]
    carries, stated as observed: the Python interpreter reads its location from argv[0], so a
    venv's bin/python3 still finds its pyvenv.cfg (config/venv-interpreter-keeps-identity);
    a #! script does NOT see its candidate name, because the kernel hands its interpreter the
    file exec-ed, so the script sees the checked realpath as $0
    (config/script-head-sees-realpath-as-dollar-zero). The child repeats no PATH
    walk and resolves no cwd-relative alias: /proc/self/cwd names the CHILD's working directory
    once the child has changed into it, so launching the unresolved candidate would run content
    from there, and no later PATH entry can substitute another program. Resolution happens in
    THIS process at launch time; a file swapped between this check and the exec is the accepted
    time-of-check residual of any path-based rule."""
    if os.path.isabs(head):
        candidate = head
    elif "/" in head or "\\" in head:
        return None, None, ("program %r is a relative path, neither a bare name nor an absolute "
                            "path, so it is refused unresolved (fail closed)" % head)
    else:
        path_value = env.get("PATH", "")
        if not path_value:
            return None, None, ("program %r cannot be resolved: the sanitized PATH is empty "
                                "(every caller PATH entry was empty, dot or otherwise relative)"
                                % head)
        candidate, refusal = _path_lookup(head, path_value)
        if refusal is not None:
            return None, None, refusal
        if candidate is None:
            return None, None, ("program %r cannot be resolved to an executable on the "
                                "sanitized PATH" % head)
    real = os.path.realpath(candidate)
    if not os.path.isfile(real) or not os.access(real, os.X_OK):
        return None, None, ("program %r resolves to %r, which is not an executable regular "
                            "file, so the launch is refused" % (head, real))
    launch = os.path.join(os.path.realpath(os.path.dirname(candidate)),
                          os.path.basename(candidate))
    refusal = _launch_refusal(head, launch, real, confine)
    if refusal is not None:
        return None, None, refusal
    return launch, real, None


# The git program every git launch runs, [launch path, realpath] once resolved (see _git_program).
_GIT_PROGRAM = []


def _git_program(confine):
    """(launch path, realpath, None) of the ONE git every git launch runs, or (None, None,
    reason). It is resolved ONCE, at the first call, through _resolve_program with confine as
    the confined directories (the same sanitized PATH walk, realpath and launch-directory
    containment, and refusal by name every configured command head gets), then reused
    unchanged: no git child repeats a PATH walk or resolves a cwd-relative alias, so a git that
    a PATH entry such as /proc/self/cwd names in a child's working directory never runs. Every
    call reads the pinned launch path's realpath again (it must still be the pinned realpath)
    and re-checks both pinned paths against its own confine (see _launch_refusal): _git passes
    each launch's working directory (the author's worktrees, the private base repository and
    every private checkout among them) and, for a transport command run from the empty
    transport directory, the repository and checkout it works on (see _git_transport), and
    run_train passes every repository directory it learns. run_train resolves a fresh pin for
    its run before the run's first git launch and
    restores the caller's on exit; outside a run (the self-test fixtures) the first git launch
    resolves it. Outside these funnels, the self-test's locale probe (_native_probe) and its
    fake gh run git by name."""
    if not _GIT_PROGRAM:
        launch, real, refusal = _resolve_program("git", _clean_env(), confine)
        if launch is None:
            return None, None, refusal
        _GIT_PROGRAM[:] = [launch, real]
    launch, real = _GIT_PROGRAM
    refusal = _launch_refusal("git", launch, real, confine)
    if refusal is not None:
        return None, None, refusal
    return launch, real, None


def _first_line(path):
    """The first line of a small text file, stripped, or "" when it cannot be read or decoded."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readline().strip()
    except (OSError, ValueError):
        return ""


def _repository_dirs_on_disk(repo):
    """The repository directories around repo, read from the filesystem WITHOUT launching git
    (run_train resolves git before its first launch, so git cannot be asked yet): from repo's
    realpath up to the first directory holding a .git entry, that worktree; its git directory
    (the .git directory, or the one a "gitdir:" file names); the common directory its commondir
    file names (else the git directory) and, when that is named .git, its parent, the main
    worktree; and every linked worktree the common directory records (worktrees/<id>/gitdir
    names <worktree>/.git). An over-approximation used only to confine git's resolution: an
    absent, unreadable or malformed record adds nothing. run_train re-checks the pinned git
    against the worktrees git itself lists before any later launch, so a git inside a layout
    this walk does not model (a core.worktree setting, for one) is refused only after the
    three discovery reads (rev-parse --show-toplevel, rev-parse --git-common-dir and worktree
    list) have run it."""
    try:
        here = os.path.realpath(str(repo))
    except OSError:
        return []
    while not os.path.lexists(os.path.join(here, ".git")):
        if os.path.dirname(here) == here:
            return []
        here = os.path.dirname(here)
    found = [here]
    git_dir = os.path.join(here, ".git")
    if not os.path.isdir(git_dir):
        line = _first_line(git_dir)
        if not line.startswith("gitdir:"):
            return found
        git_dir = os.path.join(here, line[len("gitdir:"):].strip())
    common = os.path.normpath(os.path.join(
        git_dir, _first_line(os.path.join(git_dir, "commondir")) or "."))
    found += [git_dir, common]
    if os.path.basename(common) == ".git":
        found.append(os.path.dirname(common))
    records = os.path.join(common, "worktrees")
    try:
        names = sorted(os.listdir(records))
    except OSError:
        names = []
    for name in names:
        line = _first_line(os.path.join(records, name, "gitdir"))
        if line:
            found.append(os.path.dirname(os.path.join(records, name, line)))
    return found


def _run_external(argv, cwd, timeout, extra_env=None, confine=()):
    """Launch a non-git program named by the trusted config, or the gh CLI: an argv list, no shell, a
    timeout. Returns (returncode, or None on a refusal, a timeout or a launch error, stdout
    text, stderr text). Before anything runs, the program HEAD is resolved (this tool's own
    walk of the sanitized PATH, or the given absolute path) and refused, by name, when the
    sanitized PATH is empty, when a PATH entry ahead of the match cannot be listed, when the
    head cannot be resolved, when its launch path does not resolve to the realpath checked, or
    when its realpath or its launch directory lies inside the child working directory or a
    directory in confine (at the config-command and gh call sites, every repository directory
    of the run: the toplevel, the common git directory, every worktree git lists and the
    private base repository); the child is launched with the resolved absolute launch path as
    argv[0], the checked realpath as the file it execs and the narrowed child PATH (see
    _child_env), never the bare name (the tree-based head refusals in guard_config_fail_closed
    stay in place as the first layer). See _resolve_program."""
    dirs = [str(cwd)] + [str(entry) for entry in confine if entry]
    launch, real, refusal = _resolve_program(argv[0], _clean_env(extra_env), dirs)
    if launch is None:
        return None, "", refusal
    env = _child_env(extra_env, cwd, dirs)
    try:
        proc = _run_child([launch] + list(argv[1:]), cwd=str(cwd), env=env, timeout=timeout,
                          executable=real)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


# ---------------------------------------------------------------- guards (the GUARDS table below)


def _python_module_spelling(word):
    """Whether one leading python option word SPELLS the module launch: -m itself, an attached
    operand (-mpkg), or an m inside a short flag cluster before any operand-taking letter (-Im,
    -IBm, -im). A letter in cWX takes the REST of the word as its operand, so an m behind it is
    operand text, never the module option, and a long option (--) never spells -m. Used for the
    module-NAMING diagnostic only: every argv it matches is outside the one pinned
    -I -B <script.py> shape and is refused by that rule anyway."""
    if not word.startswith("-") or word.startswith("--"):
        return False
    for ch in word[1:]:
        if ch == "m":
            return True
        if ch in "cWX" or not ch.isalpha():
            return False
    return False


def _python_scan_step(word):
    # How the module-diagnostic scan advances past ONE leading python option word that did not
    # spell -m: None STOPS the scan (-c and a cluster
    # carrying c take the rest of the command as program text, -- ends option parsing and a
    # bare - runs stdin, so nothing later is an option word), 2 consumes the word AND the
    # separate operand of its trailing valued option (--check-hash-based-pycs, and a short
    # option or bundled cluster ENDING in the valued letter W or X with no attached operand:
    # -W, -X, -IW), else 1.
    if word in ("--", "-", "-c"):
        return None
    if word == "--check-hash-based-pycs":
        return 2
    if word.startswith("--"):
        return 1
    body = word[1:]
    for pos, ch in enumerate(body):
        if ch == "c":
            return None
        if ch in "WX":
            return 2 if pos == len(body) - 1 else 1
        if not ch.isalpha():
            return 1
    return 1


def guard_config_fail_closed(data, tree=None, head_tree=None):
    """Raise FailClosed unless data is a well-formed version-1 merge-train config. tree, when
    given, is the set of REGULAR-FILE paths of the base tip tree the config text came from (see
    _base_tree): it resolves the script path of a configured command whose head is a recognized
    python-family interpreter spelling, and with tree None every
    such command is refused, fail closed. head_tree, when given, is every tree entry a command
    HEAD could name, the regular files PLUS the symlinks, kept apart from tree (a committed
    symlink can name a program as well as a regular file can); with head_tree None the head
    refusal falls back to tree. A command HEAD
    must be a bare program name: a path head is refused outright, and a bare head naming a
    base tip tree entry is refused, case-insensitively, whenever the tree is given (with BOTH
    sets None the run has no tree
    to refuse against, so that guarantee needs the tree, which load_config always supplies)."""
    heads = head_tree if head_tree is not None else tree
    folded_heads = None if heads is None else frozenset(path.casefold() for path in heads)
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
            # The maintenance-scan exception premise is ENFORCED here, never just asserted: a
            # configured command whose argv head names git (any path form) would reach
            # _run_child without the funnel maintenance pins, and a launcher head or an
            # interpreter handed inline code could hand git that same unpinned argv one hop
            # later. Deny-list, stated exactly: a head that is a path (any separator) and a
            # bare head naming a base tip tree file; a head naming
            # git; a head in CONFIG_LAUNCHERS; a CONFIG_INTERPRETERS head (matched
            # case-insensitively, optional .exe) whose leading option words leave the supported
            # per-interpreter grammar (python: the ONE pinned shape below; every other family:
            # _INTERPRETER_GRAMMAR exact flags, valued options whose operand is the next word
            # and attached-operand prefixes; anything else, every inline-code option and
            # bundled cluster included, is refused unclassified). A configured program outside
            # this list can still run git itself: the trusted-config threat model accepts that
            # residual. THE ONE RESIDUAL STATEMENT for the python pinning; every other
            # comment here and the commit record defer to it: the
            # python claims hold for commands whose HEAD matches a CONFIG_INTERPRETERS
            # python-family spelling. Another Python launcher resolved on PATH (ipython, uv,
            # coverage, py, a renamed or symlinked interpreter) and the imports a selected
            # script makes from the checkout are TRUSTED CONFIGURATION, not proven by this
            # guard; what the guard adds over that trust is that a head is never a path, that
            # with the base tip tree given (load_config always gives it; the guarantee needs
            # the tree) a head never names a base tip tree ENTRY, regular file or symlink,
            # compared case-insensitively, and that _run_external resolves every head BEFORE
            # the launch (its own walk of the sanitized PATH _clean_env rebuilt with every
            # cwd-relative entry removed) and refuses, by name, an empty sanitized PATH, a
            # PATH entry it cannot list ahead of the match, an unresolvable head, a head whose
            # launch path no longer resolves to the realpath checked, and a head whose
            # realpath or launch directory lies inside
            # the private checkout or ANY repository directory of the run (the toplevel, the
            # common git directory, every worktree git lists, each PR's and not only the one
            # being processed, and the private base repository), then launches the resolved
            # absolute launch path with its checked realpath as the file exec-ed, never the
            # bare name (a /proc/self/cwd PATH entry and every other absolute alias of that
            # content fail the containment, and the child repeats no PATH walk), so checkout
            # and worktree content (tools/gen.py, ./python3, tools/python3, a committed
            # python3 or a committed symlink, content a PR adds that any PATH entry would
            # resolve in the scratch checkout, a program in another PR's worktree, and a venv
            # there) never runs as a configured program head.
            head = command[0]
            if "/" in head or "\\" in head:
                # A PATH-form head (absolute, ./-prefixed or
                # repo-relative, tools/gen.py and tools/python3 included) is refused outright:
                # it can name checkout content or shadow a recognized interpreter, and
                # basename matching alone would let tools/gen.py skip every interpreter rule.
                bad("%s command %r head is a path, not a bare program name: a path head can "
                    "run checkout content or a shadowed interpreter, so it is refused; "
                    "configure a bare program name resolved on PATH" % (where, command[0]))
            if folded_heads is not None and head.casefold() in folded_heads:
                bad("%s command %r head names a base tip tree entry (a regular file or a "
                    "symlink, compared case-insensitively): checkout content is never a "
                    "configured program, so the head is refused" % (where, command[0]))
            # Git and the launcher heads are matched like the
            # recognized interpreters, case-insensitively and with an optional .exe tail, so
            # neither rule is dodged by a spelling a case-insensitive filesystem resolves to
            # the same program.
            program = head.casefold()
            if program.endswith(".exe"):
                program = program[:-len(".exe")]
            if program == "git":
                bad("%s command %r must not launch git: the merge train runs git only through "
                    "its own pinned funnel" % (where, command[0]))
            if program in CONFIG_LAUNCHERS:
                bad("%s command %r must not run through launcher %r: a launcher can hand git an "
                    "unpinned argv around the funnel" % (where, command[0], head))
            interp = CONFIG_INTERPRETERS.match(head)
            if not interp:
                continue
            base_name = interp.group(1).lower()
            family = _INTERPRETER_FAMILY.get(base_name, base_name)
            if family == "python":
                # A configured python command is accepted ONLY as
                # <interpreter> -I -B <repo-relative .py path that exists in tree as a regular
                # file>, with every later word belonging to that script. python -m draws the
                # module-naming diagnostic in every spelling THIS SCAN RECOGNIZES: separated
                # (-m mod), attached (-mmod), bundled in a short cluster (-Im, -IBm) and behind
                # the separate operand of a valued option (-W ignore -m, -X dev -m,
                # --check-hash-based-pycs always -m) or of a bundled cluster ending in the
                # valued letter (-IW ignore -m); the scan advances per _python_scan_step and
                # STOPS at -c, -- and a bare -, behind which every word is program text or a
                # script argument, so a -m there draws the generic shape refusal below, never
                # the module diagnostic (so bundled and valued-option-preceded spellings never
                # fall to the generic refusal, and a -m behind -c, -- or - never draws the
                # module diagnostic). What -m NAME imports
                # cannot be proved from tree membership (under -I or
                # -P a standard-library name wins over a checkout file, and a package needs
                # more than __init__.py), so the capability is removed instead of modelled;
                # a spelling outside the scan is still refused by the shape rule, only with
                # the generic diagnostic. -I and -B are REQUIRED: isolated mode keeps the
                # checkout and the PYTHON* environment off the INITIAL sys.path (the selected
                # script can import further checkout code; see the residual statement above).
                words = command[1:]
                i = 0
                while i < len(words) and words[i].startswith("-"):
                    word = words[i]
                    if _python_module_spelling(word):
                        bad("%s command %r must not launch a python module: python -m is "
                            "refused because tree membership cannot prove what -m would "
                            "import; name a checkout script instead, as "
                            "<interpreter> -I -B <path.py>" % (where, command[0]))
                    step = _python_scan_step(word)
                    if step is None:
                        break
                    i += step
                leading = 0
                for word in words:
                    if not word.startswith("-"):
                        break
                    leading += 1
                script = words[leading] if leading < len(words) else None
                if words[:leading] != _PYTHON_SCRIPT_OPTIONS or script is None:
                    bad("%s command %r may run python only as <interpreter> -I -B "
                        "<repo-relative .py path>: any other option, shape or bundled cluster "
                        "could run inline code and hand git an unpinned argv around the "
                        "funnel, so the form is refused" % (where, command[0]))
                if not script.endswith(".py") or tree is None or script not in tree:
                    bad("%s command %r script %r does not name a repo-relative .py path "
                        "present in the base tip tree as a regular file (tree None resolves "
                        "nothing, fail closed), so the launch is refused" % (
                            where, command[0], script))
                continue
            flags, valued, attached = _INTERPRETER_GRAMMAR[family]
            words, i = command[1:], 0
            while i < len(words) and words[i].startswith("-"):
                word = words[i]
                if word in flags:
                    i += 1
                elif word in valued:
                    # The operand is consumed blind, never scanned as an option: -I lib,
                    # -r mod, -l mod. A trailing valued option with no operand leaves nothing
                    # to run.
                    i += 2
                elif any(word.startswith(p) and len(word) > len(p) for p in attached):
                    i += 1
                else:
                    bad("%s command %r option %r is outside the supported %s option "
                        "grammar: inline code, a bundled option cluster or an unknown "
                        "option could hand git an unpinned argv around the funnel, so the "
                        "form is refused unclassified" % (where, command[0], word, family))

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


# The stderr text by which a Python child reports a fault it survived with exit status 0: an
# exception raised in an atexit callback, a finalizer, a thread or an unraisable-hook path is
# printed and does NOT change the exit status (observed on Python 3.14: atexit 1/0 exits 0 after
# "Exception ignored in atexit callback ...", a thread's exception after "Exception in thread",
# and a finalizer's under sys.tracebacklimit=0 after "Exception ignored while calling deallocator"
# with no Traceback line).
CHILD_FAULT_MARKERS = ("Traceback (most recent call last):", "Exception ignored",
                       "Exception in thread ", "Fatal Python error")


def guard_child_fault(err):
    """The first stderr line of a configured command that reports a fault (see
    CHILD_FAULT_MARKERS), or None. Every place this tool accepts a child's result as success was
    enumerated (a validator whose atexit callback raised exits 0, so an exit-status-only rule
    would let the train push). The children that decide a merge (the [generated].regenerate
    commands, the
    [generated].check commands in the private checkout, and the same check commands in the fresh
    normal checkout) are code the PR and the base carry: each is accepted only with exit 0 AND no
    fault on stderr (_child_failure, used by guard_regenerate_exit, guard_check_exit and
    guard_fresh_checkout). Every other subprocess result is NOT such a validator and keeps its own
    rule: the git children (config-source, branch-name, branch-match, ancestry, _remote_head, the
    _git ok=(0,) funnel) are the operator's pinned git, not code a PR or base supplies, run with
    hooks, filters and fsmonitor disabled, and the git reads whose exit 0 can hide an incomplete
    evaluation already refuse ANY stderr (fixpoint, scratch-complete, the fresh checkout itself)
    or treat a non-answer as an error (ancestry); _gh_json runs the operator's gh, whose result is
    structured JSON parsed and shape-checked before use. Disclosed residual: a configured command
    has no structured-result channel, and code it loads can still silence its own stderr (replace
    sys.unraisablehook or close descriptor 2); the exit status and the stderr scan are the two
    channels this tool can read."""
    for line in err.splitlines():
        if any(marker in line for marker in CHILD_FAULT_MARKERS):
            return line.strip()[:300]
    return None


def _child_failure(code, command, err):
    """The refusal reason for a configured command's result; None only for exit 0 with no fault on
    stderr (see guard_child_fault). A timeout or launch failure arrives as code None."""
    if code != 0:
        return "%s exited %s: %s" % (" ".join(command), code, err.strip()[:300])
    fault = _guard("child-fault")(err)
    if fault is not None:
        return "%s exited 0 with a fault on stderr: %s" % (" ".join(command), fault)
    return None


def guard_regenerate_exit(code, command, err):
    reason = _child_failure(code, command, err)
    if reason is not None:
        raise Refuse("regenerate-failed", reason)


def guard_check_exit(code, command, err):
    reason = _child_failure(code, command, err)
    if reason is not None:
        raise Refuse("check-failed", reason)


def guard_fixpoint(wt):
    """After regenerate, add and check: nothing unstaged or unmerged (regenerate-not-fixpoint) and
    no untracked file (undeclared-write). --no-renames keeps a base-side rename as separate add
    and delete records: a rename record's ORIGINAL path arrives as its own NUL-separated field,
    which this parser would misread as a record of its own and falsely refuse. Like
    scratch-complete, the probe FAILS CLOSED: git status exits 0 with an empty
    stdout and only a stderr line when it cannot read part of the checkout, so ANY stderr output
    is refused by name, never read as a fixpoint."""
    out, err = _git(wt, "status", "--porcelain=v2", "-z", "--no-renames",
                    "--untracked-files=all")[1:]
    if err:
        raise Refuse("regenerate-not-fixpoint",
                     "git status could not evaluate the checkout: %s" % err.strip()[:300])
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


def guard_ancestry(scratch, ancestor, descendant):
    """True when ancestor is an ancestor of descendant, False when it is not, and Refuse
    git-failed when git cannot tell. `merge-base --is-ancestor` is TRI-STATE: exit 0 is an
    ancestor, exit 1 is not, and any other exit evaluated nothing (observed on git 2.53.0:
    exit 128 with "fatal: Not a valid commit name" when the base commit cannot be read from the
    scratch's store; a reader of "nonzero" would take that as "not an ancestor", so a dry run
    would report would-merge after a git error). Only exit 0, or exit 1 with no stderr
    output, is an answer; anything else is refused with git's exit code and stderr."""
    rc, _out, err = _git(scratch, "merge-base", "--is-ancestor", ancestor, descendant, ok=None)
    if rc == 0:
        return True
    if rc == 1 and not err.strip():
        return False
    raise Refuse("git-failed", "git merge-base --is-ancestor %s %s could not be evaluated "
                 "(exit %d): %s" % (ancestor, descendant, rc, err.strip()[:300] or "(no stderr)"))


def guard_fresh_checkout(ctx, scratch, new):
    """Refuse normal-checkout-failed unless the candidate commit also passes every declared check
    in a SECOND, normal private checkout: a fresh --shared clone of the private checkout with the
    committed attributes honoured, the operator's own core.autocrlf and core.eol carried over
    (read from the operator's global configuration, else its system configuration, includes
    followed, by git config launched from the empty transport directory, and set in the fresh
    checkout's local configuration), no other operator setting read (its git children run
    with _PR_GIT_ENV, see _PR_DIRS), no local configuration carried over, and
    hooks disabled. The conversion-free private checkout proves the stored bytes are the
    validated bytes; this proves the commit is also correct the way a consumer materializes it
    with the built-in conversions the operator configures (a committed eol=crlf attribute or an
    operator core.autocrlf=true would otherwise turn a passing PR into a pushed commit whose
    own fresh checkout fails its generators). Those two settings carry values, never a
    command, so carrying them selects no program. A filter is NOT modelled here, whether a
    repository's local configuration or the operator's global or system configuration
    defines it (a global git-lfs filter among them): honouring an operator filter would run
    its command in a checkout holding PR content, where a command named relative to the
    working directory runs a program the PR commits, so a consumer whose checkout depends on
    a filter is out of this check's scope. The checkout itself FAILS CLOSED (observed on git
    2.53.0: with a blob of the candidate missing from, or unreadable in, the shared object
    store, this checkout prints "error: unable to read sha1 file" on stderr, EXITS 0 and leaves
    the path out of the worktree, so the checks would run over an incomplete tree): a nonzero
    exit or ANY stderr output is refused as normal-checkout-failed before any check runs. Each
    check there is accepted only with exit 0 and no fault on stderr (see guard_child_fault)."""
    carried = []
    for key, typed in _FRESH_CHECKOUT_KEYS:
        for scope in ("--global", "--system"):
            code, out, _err = _git(ctx["transport"], "config", "--includes", scope, *typed,
                                   "--get", key, ok=(0, 1))
            if code == 0:
                carried.append((key, out.decode("utf-8", "replace").strip()))
                break
    fresh_root = tempfile.mkdtemp(prefix="merge-train-fresh-")
    SCRATCHES.append(fresh_root)
    _RUN_DIRS.append(fresh_root)
    _PR_DIRS.append(fresh_root)
    try:
        fresh = os.path.join(fresh_root, "co")
        _git_transport(ctx["transport"], "clone", "--quiet", "--shared", "--no-checkout",
                       str(scratch), fresh, confine=(str(scratch),))
        for key, value in [("core.hooksPath", os.devnull), ("core.fsmonitor", "false")] + carried:
            _git(fresh, "config", key, value)
        rc, _out, err = _git(fresh, "checkout", "--quiet", "--detach", new,
                             timeout=ctx["cmd_timeout"], ok=None)
        if rc != 0 or err:
            raise Refuse("normal-checkout-failed",
                         "git checkout could not materialize the candidate in a fresh normal "
                         "checkout (exit %d): %s" % (rc, err.strip()[:300] or "(no stderr)"))
        for command in ctx["cfg"]["generated"]["check"]:
            code, _out, err = _run_external(command, fresh, ctx["cmd_timeout"],
                                            dict(PYTHONDONTWRITEBYTECODE="1"),
                                            confine=ctx.get("confine", ()))
            reason = _child_failure(code, command, err)
            if reason is not None:
                raise Refuse("normal-checkout-failed",
                             "in a fresh normal checkout of the candidate, %s" % reason)
    finally:
        _cleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))
        _RUN_DIRS.remove(fresh_root)
        _PR_DIRS.remove(fresh_root)


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
    supply (observed on git 2.53.0: in a --shared clone of a blob:none partial clone
    whose promised blob is absent, checkout prints "error: unable to read sha1 file", exits 0,
    and leaves the path missing from the worktree), and regenerating and validating over that
    incomplete tree would push content the PR never contained. The probe is plain `git status`
    against the just-detached HEAD: any worktree difference immediately after a fresh checkout
    means the checkout was incomplete. The probe FAILS CLOSED when it cannot evaluate the
    checkout (observed on git 2.53.0: with a directory of the checkout
    unreadable, status prints "dir/file: Permission denied" on stderr, prints NOTHING on stdout
    and exits 0, so reading stdout alone would accept it): a nonzero exit or ANY stderr output is
    refused by name, and any stdout record at all refuses, so output this guard cannot parse is
    refused too, never read as clean."""
    rc, out, err = _git(scratch, "status", "--porcelain=v2", "-z", "--untracked-files=no",
                        ok=None)
    if rc != 0 or err:
        raise Refuse("scratch-incomplete",
                     "git status could not evaluate the scratch checkout (exit %d): %s" % (
                         rc, err.strip()[:300] or "(no stderr)"))
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
    ("child-fault", guard_child_fault),
    ("regenerate-exit", guard_regenerate_exit),
    ("check-exit", guard_check_exit),
    ("fixpoint", guard_fixpoint),
    ("reconcile", guard_reconcile),
    ("scratch-setup", guard_scratch_setup),
    ("scratch-complete", guard_scratch_complete),
    ("generated-confined", guard_generated_confined),
    ("pushed-bytes", guard_pushed_bytes),
    ("ancestry", guard_ancestry),
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
    """The remote branch tip through ls-remote, run on the run's PRIVATE base repository (from the
    empty transport directory, see _git_transport) and BY URL: the URL is re-read from the
    author's repository per call (a read of plain config, so a mid-run remote.<remote>.url
    change is honoured as before), but the ls-remote itself never runs in the author's
    repository and never by remote name, so no author-local transport setting
    (remote.<remote>.uploadpack, core.sshCommand, http.*) can name a command to execute or apply.
    Returns a SHA, "" when absent, None when unreadable for ANY reason, a timeout included; the
    caller treats None as indeterminate, never as a cue to change anything."""
    try:
        url = _git_author_text(ctx["root"], "remote", "get-url", ctx["remote"])
        code, out, _err = _git_transport(ctx["transport"], "ls-remote", "--", url,
                                         "refs/heads/" + ctx["branch"],
                                         git_dir=ctx["base_repo"], ok=None,
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
    written: no merge, checkout, add, commit or reset ever runs there. The clone itself is
    launched from the empty transport directory, never from the author's worktree (see
    _git_transport)."""
    scratch = os.path.join(scratch_root, "co")
    _git_transport(ctx["transport"], "clone", "--quiet", "--shared", "--no-checkout",
                   str(ctx["root"]), scratch, confine=(str(ctx["root"]),))
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
    right None compares left with the index. --no-textconv and --no-ext-diff keep every
    configured diff program out of the comparison, whatever diff attribute the PR commits; the
    private checkout's git children do not read the operator's configuration either (see
    _PR_DIRS)."""
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"]
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
                                        dict(PYTHONDONTWRITEBYTECODE="1"),
                                        confine=ctx.get("confine", ()))
        _guard("check-exit")(code, command, err)


def _push_and_observe(ctx, scratch, scratch_git, old, new):
    """Compare-and-set push of new from the private checkout to the PR's own branch (the
    exact-value lease makes the remote refuse unless the branch is still at old, the head this run
    validated), then one observer over the remote. A push whose outcome is unknown (a timeout, a
    launch failure) and an observation failure of ANY kind, an observer timeout included, are
    INDETERMINATE: the marker is kept, the author's working tree, index, HEAD and local branches
    are unchanged, and the push may have landed on the remote; nothing local exists to roll
    back. A push that raised AND left the remote still at the old head is ALSO indeterminate, not
    push-rejected: the remote's receive-pack may still be running (a slow pre-receive hook) and
    the push can land after this run reports, so the marker is kept for the retry to classify.
    The push names the private checkout's git directory, scratch_git, and is launched from the
    empty transport directory (see _git_transport); it reads the operator's configuration for
    its credentials and passes --no-recurse-submodules, so an operator submodule.recurse or
    push.recurseSubmodules setting cannot start a push from a submodule the PR names."""
    branch = ctx["branch"]
    indeterminate = False
    try:
        # push=True: a TIMED-OUT push ends the direct child only, because its in-flight
        # receive-pack may still land the push; that outcome is exactly what the marker
        # reconciles on the retry (see _run_child and _end_child).
        _git_transport(ctx["transport"], "push", "--porcelain", "--no-follow-tags",
                       "--no-recurse-submodules",
                       *_guard("push-lease")(branch, old), "--", ctx["remote_url"],
                       "%s:refs/heads/%s" % (new, branch), git_dir=scratch_git,
                       checkout=scratch, ok=None, timeout=ctx["net_timeout"], push=True)
    except Refuse as exc:
        _guard("push-indeterminate")(exc)
        indeterminate = True
    observed = _remote_head(ctx)
    if observed == new:
        _remove_marker(ctx["git_dir"])
        return dict(result="pushed", old_head=old, new_head=new, reason="")
    if observed is None:
        raise Refuse("push-unknown",
                     "remote unreadable after the push, which may have landed; the marker is "
                     "kept and the author's worktree, index, HEAD and local branches are "
                     "unchanged")
    if indeterminate and observed == old:
        raise Refuse("push-unknown",
                     "the push timed out with the remote still at the old head; it may still be "
                     "in flight, so the marker is kept")
    _remove_marker(ctx["git_dir"])
    status = "push-rejected" if observed == old else "remote-changed"
    raise Refuse(status, "remote at %s" % (observed or "(absent)"))


# ---------------------------------------------------------------- the run


def _base_tree(repo, base_sha):
    """(files, heads) of the base tip tree, read from the private base repository. files is
    every REGULAR-FILE path (blob mode 100644 or 100755): the set guard_config_fail_closed
    resolves a configured python script path against (a symlink
    (120000), a gitlink (160000) and a tree are excluded, because such an entry can point
    outside the checkout or name no runnable script, so it never satisfies the resolution).
    heads is files PLUS every symlink path: the set the configured-command HEAD refusal
    matches, kept apart from files (a committed symlink resolves
    to runnable checkout content, so it is refused as a head even though the script admission
    never accepts it)."""
    out = _git(repo, "ls-tree", "-r", "-z", base_sha)[1]
    files, links = [], []
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition(b"\t")
        mode = meta.split(b" ", 1)[0]
        if mode in (b"100644", b"100755"):
            files.append(path.decode("utf-8", "surrogateescape"))
        elif mode == b"120000":
            links.append(path.decode("utf-8", "surrogateescape"))
    files = frozenset(files)
    return files, files | frozenset(links)


def load_config(root, base_sha):
    text = _guard("config-source")(root, base_sha)
    if text is None:
        raise FailClosed("merge-train not enabled: %s is absent on the base tip %s" % (
            CONFIG_PATH, base_sha))
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise FailClosed("malformed %s: %s" % (CONFIG_PATH, exc))
    files, heads = _base_tree(root, base_sha)
    _guard("config-fail-closed")(data, tree=files, head_tree=heads)
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


def _gh_json(argv, root, timeout, confine=()):
    code, out, err = _run_external(argv, root, timeout, confine=confine)
    if code != 0:
        raise FailClosed("gh failed (%s): %s" % (code, err.strip()[:300]))
    try:
        return json.loads(out)
    except ValueError as exc:
        raise FailClosed("gh returned malformed JSON: %s" % exc)


def _gh_view(ctx):
    try:
        data = _gh_json(["gh", "pr", "view", str(ctx["pr"]), "--json",
                         "headRefName,headRefOid,state"], ctx["root"], ctx["net_timeout"],
                        ctx.get("confine", ()))
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
    _RUN_DIRS.append(scratch_root)
    _PR_DIRS.append(scratch_root)
    try:
        scratch = _scratch_checkout(ctx, scratch_root)
        # The scratch's alternates include the author's store, so this fetch carries the same
        # hardening as the base fetch: the alternate-ref read it spawns in the author's
        # repository inherits GIT_NO_LAZY_FETCH (the -c pairs are scrubbed from that child's
        # environment and do not reach it; see _ALTERNATE_FETCH_CONFIG). Like every transport
        # command it is launched from the empty transport directory (see _git_transport).
        scratch_git = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
        _git_transport(ctx["transport"], *_ALTERNATE_FETCH_CONFIG, "fetch", "--no-tags",
                       "--no-recurse-submodules", "--", ctx["remote_url"],
                       "refs/heads/" + ctx["branch"], git_dir=scratch_git, checkout=scratch,
                       extra_env=dict(_ALTERNATE_FETCH_ENV), timeout=ctx["net_timeout"])
        if _git_text(scratch, "rev-parse", "FETCH_HEAD^0") != old:
            raise Refuse("pr-head-moved", "the remote head moved during the fetch")
        if _guard("ancestry")(scratch, ctx["base_sha"], old):
            return dict(result="current", old_head=old, new_head=None, reason="")
        # A dry run takes the SAME path as --apply through candidate preparation and validation
        # (would-merge is never reported here, before any of it has run); _merge_commit_push
        # stops before the retry marker and the push when apply is false.
        _git(scratch, "checkout", "--quiet", "--detach", old, timeout=ctx["cmd_timeout"])
        _guard("scratch-complete")(scratch)
        return _merge_commit_push(ctx, scratch, old, ctx["base_sha"], apply)
    finally:
        _cleanup(lambda: shutil.rmtree(scratch_root, ignore_errors=True))
        _RUN_DIRS.remove(scratch_root)
        _PR_DIRS.remove(scratch_root)


def _merge_commit_push(ctx, scratch, old, base, apply):
    generated = ctx["generated"]
    ident = dict(GIT_AUTHOR_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_AUTHOR_EMAIL=ctx["cfg"]["commit"]["email"],
                 GIT_COMMITTER_NAME=ctx["cfg"]["commit"]["name"],
                 GIT_COMMITTER_EMAIL=ctx["cfg"]["commit"]["email"])
    scratch_git = _git_text(scratch, "rev-parse", "--path-format=absolute", "--git-dir")
    # ok=None: the return code is decided HERE, before any conflict processing (an acceptance
    # that read only MERGE_HEAD would let a merge reporting an abnormal exit after creating
    # MERGE_HEAD go on to commit and push). Exit 0 is accepted;
    # exit 1 is accepted only as the supported conflict condition (MERGE_HEAD exists, the
    # index holds unmerged entries and stderr carries no fatal or error diagnostic; see the
    # boundary below), resolved below; a negative code (the merge child died by a
    # signal) or any other code proves nothing about the resulting state and is refused with
    # git's exit code and stderr, whether or not MERGE_HEAD exists:
    # nothing after an abnormally ended merge may commit or push.
    rc, _out, err = _git(scratch, "merge", "--no-commit", "--no-ff", "--no-edit", base,
                         extra_env=ident, ok=None, timeout=ctx["cmd_timeout"])
    merge_started = os.path.lexists(os.path.join(scratch_git, "MERGE_HEAD"))
    if rc not in (0, 1):
        raise Refuse("git-failed", "git merge exited %d%s: %s" % (
            rc, " after creating MERGE_HEAD" if merge_started else "",
            err.strip()[:300] or "(no stderr)"))
    if not merge_started:
        raise Refuse("git-failed", "the merge did not start (git merge exited %d): %s" % (
            rc, err.strip()[:300] or "(no stderr)"))
    unmerged = _unmerged(scratch)
    # The accepted diagnostic boundary, stated exactly: a merge may write conflict notices,
    # warnings and hints to stderr, but never a line git prefixes "fatal:" or "error:"; the scan
    # runs before exit 0 or exit 1 is ACCEPTED, while every other status, and an absent
    # MERGE_HEAD, is refused outright above without reaching the scan. Such a diagnostic
    # refuses HERE, before any conflict resolution (a scan run only on exit 1 would let a merge
    # that reported exit 0 beside an error diagnostic go on to commit and push).
    # The English prefixes are
    # reliable because the funnel pins every child to the C locale: _clean_env sets LC_ALL=C and
    # drops LANGUAGE after any explicit additions, so the shipped git message catalogs that
    # translate both prefixes never apply.
    diagnostic = [line for line in err.splitlines()
                  if line.startswith(("fatal:", "error:"))]
    if diagnostic:
        raise Refuse("git-failed", "git merge exited %d with a fatal or error diagnostic, not "
                     "a supported merge outcome: %s" % (rc, diagnostic[0][:300]))
    if rc == 1 and not unmerged:
        raise Refuse("git-failed", "git merge exited 1 with no unmerged index entry, not the "
                     "supported conflict condition: %s" % (err.strip()[:300] or "(no stderr)"))
    if rc == 0 and unmerged:
        # A SUCCESS exit that left unmerged index entries is no supported outcome either, even
        # with a clean stderr: nothing after an abnormally reported merge may commit or push.
        raise Refuse("git-failed", "git merge exited 0 with unmerged index entries, not a "
                     "supported merge outcome: %s" % ", ".join(sorted(unmerged)[:10]))
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
                                        dict(PYTHONDONTWRITEBYTECODE="1"),
                                        confine=ctx.get("confine", ()))
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
    if not apply:
        # The dry run validated the same candidate the apply path would push; it stops here,
        # before the retry marker and the push.
        return dict(result="would-merge", old_head=old, new_head=None,
                    reason="dry run: would merge %s (rerun with --apply)" % base)
    _write_marker(ctx["git_dir"], dict(pr=ctx["pr"], branch=ctx["branch"], old=old, new=new))
    return _push_and_observe(ctx, scratch, scratch_git, old, new)


def _base_fetch(root, run_dir, remote, base):
    """Resolve the base tip WITHOUT running any fetch in the author's repository (a fetch there
    could follow a symbolic refs/remotes/<remote>/<base> and rewind an
    author-owned branch, recurse on demand into a populated submodule with the submodule's own
    refspecs, run the repository's reference-transaction hooks, or execute a configured
    remote.<remote>.uploadpack, and it would write objects, a reflog and FETCH_HEAD there even on
    a dry run). The fetch runs in a PRIVATE bare repository this run creates inside run_dir:
    initialized
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
    environment; see _ALTERNATE_FETCH_CONFIG). The fetch names the base repository with
    --git-dir and is launched from the run's empty transport directory, run_dir/TRANSPORT_DIR
    (see _git_transport). Author-side footprint of this whole resolution:
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
    _git_transport(os.path.join(run_dir, TRANSPORT_DIR), *_ALTERNATE_FETCH_CONFIG, "fetch",
                   "--no-tags", "--no-recurse-submodules", "--refmap=", "--", remote_url,
                   "+refs/heads/%s:refs/merge-train/base" % base, git_dir=base_repo,
                   extra_env=dict(_ALTERNATE_FETCH_ENV), timeout=GIT_TIMEOUT)
    base_sha = _git_text(base_repo, "rev-parse", "--verify", "refs/merge-train/base^0")
    return base_repo, remote_url, base_sha


def run_train(root, apply=False, only=None):
    """The whole run under the global lock. Returns (exit code, reports, fatal message or None).
    The run resolves its own git (see _git_program) before its first git launch, records its
    repository directories for the PATH its children receive (see _child_env), and restores
    the caller's pin and directories on exit."""
    saved_git, saved_dirs = list(_GIT_PROGRAM), list(_RUN_DIRS)
    del _GIT_PROGRAM[:]
    del _RUN_DIRS[:]
    try:
        return _run_train(root, apply, only)
    finally:
        _GIT_PROGRAM[:] = saved_git
        _RUN_DIRS[:] = saved_dirs


def _run_train(root, apply, only):
    reports = []
    # git is resolved BEFORE its first launch, confined to root and every repository
    # directory the filesystem records around it (git cannot be asked yet; see
    # _repository_dirs_on_disk), and checked again against every worktree git itself lists
    # before any later launch. Every directory either reading yields is kept out of every
    # child's PATH (see _child_env).
    on_disk = [root] + _repository_dirs_on_disk(root)
    refusal = _git_program(on_disk)[2]
    if refusal is not None:
        return 2, reports, "git refused before any git launch: %s" % refusal
    _RUN_DIRS[:] = [str(path) for path in on_disk]
    try:
        top = _git_author_text(root, "rev-parse", "--show-toplevel")
        common = _git_author_text(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except Refuse as exc:
        return 2, reports, "not a git repository: %s" % exc.reason
    # The worktree list is read during discovery, before any program head launches, so gh is
    # confined to every repository directory as well (see _run_locked_with_base).
    try:
        trees = _worktrees(top)
    except Refuse as exc:
        return 2, reports, "%s: %s" % (exc.status, exc.reason)
    repo_dirs = [top, common] + [t["path"] for t in trees]
    refusal = _git_program(repo_dirs)[2]
    if refusal is not None:
        return 2, reports, "git refused after discovery: %s" % refusal
    _RUN_DIRS.extend(repo_dirs)
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
            return _run_locked(Path(top), repo_dirs, trees, apply, only or set(), reports)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def _run_locked(root, repo_dirs, trees, apply, only, reports):
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
        # The empty directory every transport command is launched from (see _git_transport).
        os.mkdir(os.path.join(run_dir, TRANSPORT_DIR))
        return _run_locked_with_base(root, run_dir, remote, base, repo_dirs, trees, apply, only,
                                     reports)
    finally:
        _cleanup(lambda: shutil.rmtree(run_dir, ignore_errors=True))


def _run_locked_with_base(root, run_dir, remote, base, repo_dirs, trees, apply, only, reports):
    try:
        base_repo, remote_url, base_sha = _base_fetch(root, run_dir, remote, base)
    except Refuse as exc:
        return 2, reports, "fetch failed: %s" % exc.reason
    # Every program head the run launches (gh, and each configured regenerate and check
    # command) is confined to EVERY repository directory of the run: the toplevel, the common
    # git directory, every worktree git lists (each PR's, not only the one being processed)
    # and the private base repository, beside each launch's own working directory (see
    # _run_external); from here on, no child's PATH keeps an entry inside the base repository
    # either (see _child_env).
    confine = tuple(repo_dirs) + (base_repo,)
    _RUN_DIRS.append(base_repo)
    transport = os.path.join(run_dir, TRANSPORT_DIR)
    try:
        cfg = load_config(base_repo, base_sha)
        if cfg["remote"] != remote or cfg["base"] != base:
            raise FailClosed("v1 supports remote 'origin' and base 'main' only")
        limits = cfg.get("limits", dict())
        net_timeout = limits.get("network_timeout_seconds", 120)
        cmd_timeout = limits.get("command_timeout_seconds", 900)
        prs = _gh_json(["gh", "pr", "list", "--state", "open", "--base", base, "--json",
                        "number,headRefName,headRefOid,isCrossRepository,baseRefName",
                        "--limit", str(PR_LIST_LIMIT)], root, net_timeout, confine)
        if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
            raise FailClosed("gh pr list did not return a list of objects")
        if len(prs) >= PR_LIST_LIMIT:
            raise FailClosed("gh returned %d PRs; the list may be truncated" % len(prs))
        for pr in prs:
            if (type(pr.get("number")) is not int or not isinstance(pr.get("headRefName"), str)
                    or not isinstance(pr.get("headRefOid"), str)):
                raise FailClosed("gh pr list row malformed: %r" % (pr,))
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
                       base_repo=base_repo, confine=confine, transport=transport,
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
    # An explicitly requested PR the run did not find is a
    # non-success outcome reported by number, never a silent exit 0.
    for number in sorted(n for n in only if n not in set(p["number"] for p in prs)):
        reports.append(dict(schema=SCHEMA, pr=number, branch=None, worktree=None, base=base_sha,
                            old_head=None, new_head=None, result="pr-not-found",
                            reason="PR #%d was requested but is not in the open-PR list for "
                                   "base %s" % (number, base),
                            regenerated=[], hand_resolution=False, review_carry=None))
        rc = 1
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
SKIPPED = []
# Case exceptions, kept APART from check failures: in a normal run a crash fails the suite like
# any failure, but red-on-revert never counts one as a red mutant (a
# guard is proven red only by its case's own named assertions failing WITH no crash recorded; a
# case exception during a revert is cannot-evaluate for that guard even when a named check also
# failed, and the suite fails; see _revert_outcome).
CRASHES = []
_RECORDER = dict(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES, skipped=SKIPPED,
                 crashes=CRASHES)

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
if "--check-fail" in sys.argv:
    sys.exit(1)
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

# Fixture sleeper scripts, committed into the fixture repo and named by its config: the config
# schema refuses an interpreter handed inline -c code, so these cases route their sleeping
# regenerate through a committed script instead. The -c spawns
# INSIDE the scripts are direct child launches, never config-routed argvs.
TOY_SLEEP_TIMEOUT = r"""import os, subprocess, sys, time
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
with open(os.environ["TIMEOUT_PID_FILE"], "w") as handle:
    handle.write(str(os.getpid()) + " " + str(gc.pid))
time.sleep(300)
"""

TOY_SLEEP_SIGNAL = r"""import os, subprocess, sys, time
CODE = ("import os, signal, sys, time;"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        " fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT);"
        " os.write(fd, (sys.argv[2] + chr(32) + str(os.getpid())).encode());"
        " os.close(fd); time.sleep(300)")
subprocess.Popen([sys.executable, "-c", CODE, os.environ["SIG_PID_FILE"], str(os.getpid())])
time.sleep(120)
"""

TOY_SLEEP_LAUNCH = r"""import time
time.sleep(120)
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
    if name in _RECORDER["executed"] or name in [n for n, _why in _RECORDER["skipped"]]:
        print("SELF-TEST HARNESS ERROR: duplicate check id %r" % name, file=sys.stderr)
        sys.exit(2)
    _RECORDER["executed"].add(name)
    _RECORDER["list"].append(name)
    if got != want:
        _RECORDER["failures"].append("%s: got %r, want %r" % (name, got, want))


def skip(name, why):
    """Record a registered check that was NOT run: it stays out of the executed set (and so out of
    the execution report and the executed count), and the verdict line names it as not run."""
    if name in _RECORDER["executed"] or name in [n for n, _why in _RECORDER["skipped"]]:
        print("SELF-TEST HARNESS ERROR: duplicate check id %r" % name, file=sys.stderr)
        sys.exit(2)
    _RECORDER["skipped"].append((name, why))


def _can_read(path):
    """Whether this process can still read path after its mode was set to 0 (root and a process
    holding CAP_DAC_OVERRIDE can): OBSERVED, never inferred from the uid."""
    return os.access(path, os.R_OK)


ZERO = "0" * 40


def _audit_violation(args):
    """The self-test's own forbidden-operation predicate, written apart from _forbidden so that a
    weakened funnel cannot also blind the audit; the two must AGREE on every argv: no rebase,
    cherry-pick, stash, clean, gc, worktree prune or reset of ANY kind; for push, no force, delete,
    mirror, all or prune option in long, abbreviated or bundled short form (-uf, -fu), no lease
    other than the one exact-value refs/heads compare-and-set form (40- or 64-hex), and no refspec
    other than a plain name or the exact <hex>:refs/heads/<branch> form away from the base (no +
    force spec, no :delete spec, no src:dst alias). The subcommand is resolved after git's
    global options through this predicate's OWN tables (kept apart from GIT_GLOBAL_*), and an
    argv whose subcommand cannot be resolved is a violation outright. A -c or --config-env
    payload whose key names alias.*, push.*, include.* or
    includeIf.*, remote.<name>.mirror, .push, .pushurl, .url or remote.pushDefault,
    branch.<name>.pushRemote, or url.<base>.insteadOf or .pushInsteadOf is a violation even
    under a harmless resolved subcommand; the key ends at the FIRST "=" of a -c payload and
    at the LAST "=" of a --config-env payload, the same split
    rule as the funnel's; these key classes are DELIBERATELY identical in
    membership to _config_pair_forbidden's (agreement on every argv is the invariant;
    independence lives in the implementation), and case_audit probes each predicate
    independently so a shared gap still turns its vectors red. After the subcommand, the first
    non-option word is the repository."""
    words = list(args)
    flags = ("-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects",
             "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
             "--icase-pathspecs", "--no-optional-locks", "--no-lazy-fetch", "--no-advice")
    valued = ("-c", "-C", "--config-env", "--git-dir", "--work-tree", "--namespace",
              "--attr-source")
    equals = ("--config-env", "--git-dir", "--work-tree", "--namespace", "--attr-source",
              "--exec-path", "--list-cmds")

    def key_violation(payload, env=False):
        key = (payload.rsplit("=", 1)[0] if env else payload.split("=", 1)[0]).split(".")
        section, var = key[0].lower(), key[-1].lower()
        return (section in ("alias", "push", "include", "includeif")
                or (section == "remote"
                    and var in ("mirror", "push", "pushurl", "url", "pushdefault"))
                or (section == "branch" and var == "pushremote")
                or (section == "url" and var in ("insteadof", "pushinsteadof")))

    i = 0
    while i < len(words) and words[i].startswith("-"):
        w = words[i]
        if w in flags:
            i += 1
        elif w in valued:
            if (w in ("-c", "--config-env") and i + 1 < len(words)
                    and key_violation(words[i + 1], env=w == "--config-env")):
                return True
            i += 2
        elif "=" in w and w.split("=", 1)[0] in equals:
            if (w.split("=", 1)[0] == "--config-env"
                    and key_violation(w.split("=", 1)[1], env=True)):
                return True
            i += 1
        else:
            return True
    if i >= len(words):
        return True
    words = words[i:]
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
        # The configured interpreter head is the BARE name python3, resolved through this
        # fixture-front PATH entry to the running interpreter: the config schema refuses a
        # path head outright, so the fixture configures its
        # commands the way a live project now must.
        os.symlink(sys.executable, self.bin / "python3")
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
        self.write(self.main, CONFIG_PATH, CONFIG_TOML % dict(py="python3", probe=probe))
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


def _audited_subcommand(args):
    """The subcommand word of a recorded git argv, read past git's global options (a transport
    command carries --git-dir and --work-tree, and the private fetches carry -c pins), else
    None."""
    words = list(args)
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_GLOBAL_VALUED else 1
    return words[i] if i < len(words) else None


def case_config(_tmp):
    # Pure schema checks: no tool run, no git call and no remote, so the per-case invariants are
    # vacuous here and not asserted.
    good = dict(version=1, remote="origin", base="main",
                commit=dict(name="A", email="a@example.invalid"),
                generated=dict(paths=["gen/d.txt"], regenerate=[["x"]], check=[["y"]]))

    def verdict(mutate, tree=None):
        data = json.loads(json.dumps(good))
        mutate(data)
        try:
            _guard("config-fail-closed")(data, tree=tree)
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
    # No configured command may launch git, in any path form, so
    # no unpinned git argv can reach _run_child through _run_external.
    check("config/git-check-command-refused",
          verdict(lambda d: d["generated"].update(check=[["git", "status", "--porcelain"]])),
          "refused")
    check("config/git-regenerate-command-refused",
          verdict(lambda d: d["generated"].update(regenerate=[["/usr/bin/git", "fetch"]])),
          "refused")
    # Nor may a configured command reach git one hop away through a
    # launcher head or inline interpreter code; the boundary case, an interpreter running a
    # script with leading isolation options, stays accepted.
    check("config/env-git-command-refused",
          verdict(lambda d: d["generated"].update(check=[["env", "git", "status"]])), "refused")
    check("config/env-path-git-command-refused",
          verdict(lambda d: d["generated"].update(check=[["/usr/bin/env", "git", "status"]])),
          "refused")
    check("config/shell-inline-command-refused",
          verdict(lambda d: d["generated"].update(check=[["sh", "-c", "git status"]])), "refused")
    check("config/interpreter-inline-code-refused",
          verdict(lambda d: d["generated"].update(check=[["python3", "-c", "print(1)"]])),
          "refused")
    # The acceptance boundaries are COMPATIBILITY checks guarding against refusing too much,
    # never red-on-revert evidence; the tree carries the named script as a regular file, plus a
    # suffixless twin for the suffix vector below.
    tree_with_gen = frozenset(("tools/gen.py", "tools/gen"))
    check("config/interpreter-script-accepted",
          verdict(lambda d: d["generated"].update(
              check=[["python3", "-I", "-B", "tools/gen.py", "--check"]]),
              tree=tree_with_gen), "accepted")
    check("config/interpreter-valued-option-script-accepted",
          verdict(lambda d: d["generated"].update(
              check=[["perl", "-I", "lib", "tools/gen.pl"]])), "accepted")

    def refusal(mutate, tree=None):
        data = json.loads(json.dumps(good))
        mutate(data)
        try:
            _guard("config-fail-closed")(data, tree=tree)
        except FailClosed as exc:
            return ("refused", str(exc))
        return ("accepted", "")

    # Python -m draws the diagnostic NAMING the module form in every spelling the module scan
    # recognizes: separated, attached, trailing, bundled in a short cluster (-Im, -IBm) and
    # behind the separate operand of a valued option or of a bundled cluster ending in its
    # letter (-W ignore -m, -X dev -m, --check-hash-based-pycs always -m, -IW ignore -m),
    # checkout module or not (what -m NAME imports cannot be proved from tree
    # membership, so the capability is removed
    # instead of modelled; the named diagnostic is
    # asserted so the dedicated -m scan cannot silently decay into the generic shape refusal
    # covered below).
    for check_id, argv in (
            ("config/python-module-timeit-inline-code-refused",
             ["python3", "-m", "timeit", "-n1", "print(1)"]),
            ("config/python-module-code-repl-refused", ["python3", "-I", "-m", "code"]),
            ("config/python-module-local-refused", ["python3", "-I", "-m", "tools.gen"]),
            ("config/python-module-attached-refused", ["python3", "-I", "-mtools.gen"]),
            ("config/python-module-trailing-refused", ["python3", "-I", "-m"]),
            ("config/python-module-bundled-refused", ["python3", "-Im", "timeit"]),
            ("config/python-module-bundled-double-refused", ["python3", "-IBm", "timeit"]),
            ("config/python-module-after-valued-option-refused",
             ["python3", "-W", "ignore", "-m", "timeit"]),
            ("config/python-module-after-valued-x-refused",
             ["python3", "-X", "dev", "-m", "mod"]),
            ("config/python-module-after-check-hash-pycs-refused",
             ["python3", "--check-hash-based-pycs", "always", "-m", "timeit"]),
            ("config/python-module-after-bundled-valued-refused",
             ["python3", "-IW", "ignore", "-m", "timeit"])):
        got = refusal(lambda d, a=argv: d["generated"].update(check=[list(a)]),
                      tree=tree_with_gen)
        check(check_id, (got[0], "must not launch a python module" in got[1]),
              ("refused", True))
    # The module scan STOPS at -c, -- and a bare - (everything
    # behind each is program text or a script argument, never an option word), so a -m there
    # draws the generic one-shape refusal, never the module diagnostic; each form stays
    # refused either way.
    for check_id, argv in (
            ("config/python-inline-code-then-m-generic-refused", ["python3", "-c", "-m"]),
            ("config/python-double-dash-then-m-generic-refused", ["python3", "--", "-m"]),
            ("config/python-stdin-then-m-generic-refused", ["python3", "-", "-m"]),
            # The BUNDLED c stop (-Ic) draws the same generic
            # refusal; a scan stepping past the cluster would reach the -m behind it and draw
            # the module diagnostic instead, so this vector discriminates that stop.
            ("config/python-bundled-inline-code-then-m-generic-refused",
             ["python3", "-Ic", "-m"])):
        got = refusal(lambda d, a=argv: d["generated"].update(check=[list(a)]),
                      tree=tree_with_gen)
        check(check_id, (got[0], "must not launch a python module" in got[1],
                         "may run python only as" in got[1]), ("refused", False, True))
    # A command HEAD that is a
    # path never runs, whatever it names and whichever separator it carries (the backslash
    # half of the path rule gets its own vector), and a bare head naming a base tip tree entry
    # never runs either, compared case-insensitively; each refusal is asserted WITH its own
    # named diagnostic, so neither head rule can silently decay into another refusal. The tree
    # for these vectors carries the bare heads themselves.
    tree_with_heads = frozenset(("tools/gen.py", "gen.py", "python3"))
    for check_id, argv, named in (
            ("config/path-head-script-refused", ["tools/gen.py", "--check"],
             "head is a path"),
            ("config/path-head-dotslash-interpreter-refused",
             ["./python3", "-I", "-B", "tools/gen.py"], "head is a path"),
            ("config/path-head-checkout-interpreter-refused",
             ["tools/python3", "-I", "-B", "tools/gen.py"], "head is a path"),
            ("config/backslash-path-head-refused", ["tools\\gen.py", "--check"],
             "head is a path"),
            ("config/tree-file-head-refused", ["gen.py", "--check"],
             "names a base tip tree entry"),
            ("config/tree-entry-head-case-insensitive-refused", ["GEN.PY", "--check"],
             "names a base tip tree entry"),
            ("config/tree-file-interpreter-head-refused",
             ["python3", "-I", "-B", "tools/gen.py"], "names a base tip tree entry")):
        got = refusal(lambda d, a=argv: d["generated"].update(check=[list(a)]),
                      tree=tree_with_heads)
        check(check_id, (got[0], named in got[1]), ("refused", True))
    # A recognized interpreter head is matched case-insensitively
    # and with an optional .exe tail, so neither spelling escapes the python one-shape rule
    # into the trusted-config residual; the accepted boundary below keeps the uppercase .exe
    # spelling of the pinned shape working (a compatibility check, never red-on-revert
    # evidence).
    check("config/python-uppercase-inline-code-refused",
          verdict(lambda d: d["generated"].update(check=[["Python3", "-c", "print(1)"]]),
                  tree=tree_with_gen), "refused")
    check("config/python-exe-inline-code-refused",
          verdict(lambda d: d["generated"].update(check=[["python3.exe", "-c", "print(1)"]]),
                  tree=tree_with_gen), "refused")
    check("config/python-uppercase-exe-script-accepted",
          verdict(lambda d: d["generated"].update(
              check=[["PYTHON3.EXE", "-I", "-B", "tools/gen.py"]]), tree=tree_with_gen),
          "accepted")
    # Git and every launcher head take their rule in any case
    # spelling and with an optional .exe tail, like the interpreters, so neither rule is
    # dodged by a spelling a case-insensitive filesystem resolves to the same program.
    for check_id, argv, named in (
            ("config/git-uppercase-command-refused", ["Git", "push", "origin"],
             "must not launch git"),
            ("config/git-allcaps-command-refused", ["GIT", "status"],
             "must not launch git"),
            ("config/git-exe-command-refused", ["git.exe", "push"],
             "must not launch git"),
            ("config/launcher-uppercase-command-refused", ["Bash", "-c", "git push -f"],
             "must not run through launcher"),
            ("config/launcher-uppercase-exe-command-refused", ["SH.EXE", "-c", "git status"],
             "must not run through launcher"),
            ("config/launcher-env-uppercase-command-refused", ["Env", "git", "push"],
             "must not run through launcher"),
            ("config/launcher-exe-command-refused", ["bash.exe", "-c", "git status"],
             "must not run through launcher")):
        got = refusal(lambda d, a=argv: d["generated"].update(check=[list(a)]))
        check(check_id, (got[0], named in got[1]), ("refused", True))
    # The ONE accepted python shape is
    # <interpreter> -I -B <repo-relative .py path in the tree as a regular file>; a missing
    # script, a suffixless path and a shape without the isolation options are each refused even
    # though the head is clean (the tree carries tools/gen.py AND tools/gen, so each refusal
    # comes from its own rule, never from membership alone).
    check("config/python-script-missing-refused",
          verdict(lambda d: d["generated"].update(
              check=[["python3", "-I", "-B", "tools/absent.py"]]), tree=tree_with_gen),
          "refused")
    check("config/python-script-non-py-suffix-refused",
          verdict(lambda d: d["generated"].update(
              check=[["python3", "-I", "-B", "tools/gen"]]), tree=tree_with_gen), "refused")
    check("config/python-script-without-isolation-refused",
          verdict(lambda d: d["generated"].update(check=[["python3", "tools/gen.py"]]),
                  tree=tree_with_gen), "refused")
    # EACH isolation flag is independently required, with the
    # script present in the tree, so a relaxation accepting -B alone or -I alone fails its own
    # vector (a vector omitting both flags together would discriminate neither).
    check("config/python-script-b-without-i-refused",
          verdict(lambda d: d["generated"].update(check=[["python3", "-B", "tools/gen.py"]]),
                  tree=tree_with_gen), "refused")
    check("config/python-script-i-without-b-refused",
          verdict(lambda d: d["generated"].update(check=[["python3", "-I", "tools/gen.py"]]),
                  tree=tree_with_gen), "refused")

    # For the non-python families the option scan
    # parses each grammar, so an option operand never ends the scan, a bundled cluster never
    # hides attached code, and a long inline-code option is never read as a script; a form
    # outside the grammar is refused unclassified. A python-family form outside the one pinned
    # -I -B <script.py> shape (inline -c code, clusters, unknown or build-suffixed spellings
    # with options) is refused by that shape rule.
    for check_id, argv in (
            ("config/python-valued-x-then-inline-code-refused",
             ["python3", "-X", "dev", "-c", "print(123)"]),
            ("config/python-valued-w-then-inline-code-refused",
             ["python3", "-W", "ignore", "-c", "print(123)"]),
            ("config/python-bundled-attached-code-refused", ["python3", "-Icprint(123)"]),
            ("config/perl-valued-i-then-inline-code-refused",
             ["perl", "-I", "lib", "-e", "print 1"]),
            ("config/perl-uppercase-e-inline-code-refused", ["perl", "-E", "say 1"]),
            ("config/node-long-eval-inline-code-refused", ["node", "--eval", "1"]),
            ("config/node-print-inline-code-refused", ["node", "-p", "1"]),
            ("config/node-require-then-inline-code-refused",
             ["node", "-r", "mod", "-e", "1"]),
            ("config/ruby-valued-i-then-inline-code-refused",
             ["ruby", "-I", "lib", "-e", "puts 1"]),
            ("config/lua-valued-l-then-inline-code-refused",
             ["lua", "-l", "mod", "-e", "print(1)"]),
            ("config/ash-shell-inline-command-refused", ["ash", "-c", "git status"]),
            ("config/interpreter-unclassified-option-refused",
             ["python3", "--jit", "tools/gen.py"]),
            # A build-suffixed interpreter name
            # (free-threaded t, debug d, pymalloc m, combinations such as td and dm, and a
            # -dbg tail after any of them) parses the same grammar instead of skipping the
            # scan.
            ("config/python-free-threaded-inline-code-refused",
             ["python3.13t", "-c", "print(1)"]),
            ("config/python-debug-build-inline-code-refused",
             ["python3.13d", "-c", "print(1)"]),
            ("config/python-dbg-suffix-inline-code-refused",
             ["python3.12-dbg", "-c", "print(1)"]),
            ("config/python-td-build-inline-code-refused",
             ["python3.13td", "-c", "print(1)"]),
            ("config/python-pymalloc-build-inline-code-refused",
             ["python3.7m", "-c", "print(1)"]),
            ("config/python-debug-pymalloc-build-inline-code-refused",
             ["python3.7dm", "-c", "print(1)"]),
            ("config/python-free-threaded-dbg-build-inline-code-refused",
             ["python3.13t-dbg", "-c", "print(1)"])):
        check(check_id,
              verdict(lambda d, a=argv: d["generated"].update(check=[list(a)])), "refused")


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

    def load_verdict(repo, sha):
        try:
            return ("accepted", load_config(repo, sha)["generated"]["check"][0][3])
        except FailClosed as exc:
            return ("refused", str(exc))

    # The load_config to _base_tree wiring over a REAL base
    # tip. The fixture config python commands are accepted only because load_config resolves
    # their script path against the base tip tree of the SAME repository and commit it read the
    # config text from; a wiring handing the guard tree None (or a tree missing the script)
    # refuses this acceptance, fail closed.
    base_sha = _git_text(fx.main, "rev-parse", "refs/heads/main")
    check("config/base-tree-wiring-script-accepted", load_verdict(fx.main, base_sha),
          ("accepted", "tools/gen.py"))
    cfg_text = CONFIG_TOML % dict(py="python3", probe="[]")
    # The wiring must resolve against the SAME COMMIT the config
    # text came from, not merely the same repository: this config names a script that exists
    # ONLY at the tip commit, so a wiring resolving one commit back (base_sha~1, which the
    # wiring vector above cannot tell from the tip, both trees carrying tools/gen.py) refuses
    # what the correct wiring accepts; the expected tuple pins the script present at the tip
    # and absent one commit back.
    fx.write(fx.main, "tools/tip_only.py", "print(1)\n")
    fx.write(fx.main, CONFIG_PATH, cfg_text.replace("tools/gen.py", "tools/tip_only.py"))
    tip_sha = fx.commit(fx.main, "tip-only script")
    check("config/base-tree-wiring-same-commit-accepted",
          (load_verdict(fx.main, tip_sha),
           "tools/tip_only.py" in _base_tree(fx.main, tip_sha)[0],
           "tools/tip_only.py" in _base_tree(fx.main, tip_sha + "~1")[0]),
          (("accepted", "tools/tip_only.py"), True, False))
    # A SYMLINK at the named .py path is not a regular file: the SCRIPT set of _base_tree
    # excludes it, so the resolution refuses even though ls-tree lists the path and even
    # though the HEAD set carries it (the two sets are kept apart).
    fx.write(fx.main, CONFIG_PATH, cfg_text.replace("tools/gen.py", "tools/alias_gen.py"))
    os.symlink("gen.py", os.path.join(str(fx.main), "tools", "alias_gen.py"))
    link_sha = fx.commit(fx.main, "symlink script")
    link_files, link_heads = _base_tree(fx.main, link_sha)
    check("config/base-tree-symlink-script-refused",
          (load_verdict(fx.main, link_sha)[0], "tools/alias_gen.py" in link_files,
           "tools/alias_gen.py" in link_heads, "tools/gen.py" in link_files),
          ("refused", False, True, True))
    # A committed SYMLINK configured as a bare HEAD is refused
    # with the tree given, end to end through load_config over a real base tip: the
    # head-refusal set reads every tree entry a head could name, symlinks included.
    fx.write(fx.main, "tools/head.sh", "#!/bin/sh\nexit 0\n")
    fx.write(fx.main, CONFIG_PATH, cfg_text.replace(
        'check = [["python3", "-I", "-B", "tools/gen.py", "--check"]]',
        'check = [["train-head"]]'))
    os.symlink("tools/head.sh", os.path.join(str(fx.main), "train-head"))
    head_sha = fx.commit(fx.main, "symlink head")
    head_files, head_heads = _base_tree(fx.main, head_sha)
    try:
        load_config(fx.main, head_sha)
        head_got = ("accepted", "")
    except FailClosed as exc:
        head_got = ("refused", str(exc))
    check("config/base-tree-symlink-head-refused",
          (head_got[0], "names a base tip tree entry" in head_got[1],
           "train-head" in head_files, "train-head" in head_heads),
          ("refused", True, False, True))
    # A script ABSENT from the base tip refuses END TO END: the run exits 2 before touching
    # anything, naming the script.
    fx2 = _fixture(tmp, "frombase-missing-script")
    _stale_pr(fx2)
    fx2.write(fx2.main, CONFIG_PATH, cfg_text.replace("tools/gen.py", "tools/absent.py"))
    _git(fx2.main, "commit", "-q", "-am", "missing script", extra_env=FIXTURE_IDENT)
    _git(fx2.main, "push", "-q", "origin", "main")
    rc2, reports2, fatal2 = fx2.run()
    check("config/base-tree-missing-script-refused",
          (rc2, reports2, "tools/absent.py" in (fatal2 or "")), (2, [], True))
    check("config/read-from-base-invariants", _invariants(fx, fx2), [])


def _impostor_pr(tmp, name, sentinel):
    # A fixture whose PR branch COMMITS an executable python3 impostor at the checkout root
    # (the base tip tree, which the head refusal reads, never carries it) and whose fixture
    # bin/python3 symlink is removed, so the configured bare python3 head resolves on the
    # remaining PATH alone; the impostor records into sentinel that it ran.
    fx = _fixture(tmp, name)
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    impostor = Path(wt) / "python3"
    impostor.write_text("#!/bin/sh\n: > '%s'\nexit 7\n" % sentinel, encoding="utf-8")
    impostor.chmod(0o755)
    head = fx.commit(wt, "impostor interpreter")
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % head)
    fx.prs[0]["headRefOid"] = head
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    os.remove(fx.bin / "python3")
    return fx


def case_pathenv(tmp):
    # _clean_env rebuilds the PATH every child resolves its
    # program through with every empty, dot or other relative entry removed: a cwd-resolving
    # entry would resolve a bare configured head in the child working directory, which for the
    # regenerate and check commands is the merged scratch checkout, content the PR itself
    # supplies. The boundary is absoluteness: absolute entries stay, in order; every other
    # entry (empty, dot, relative) is dropped.
    saved = _put_env(dict(PATH=os.pathsep.join(["/usr/bin", ".", "", "rel/bin", "/bin"])))
    try:
        got_path = _clean_env().get("PATH")
    finally:
        _restore_env(saved)
    check("config/clean-env-strips-relative-path-entries", got_path,
          os.pathsep.join(["/usr/bin", "/bin"]))
    # End to end, dry run over a PR that commits an executable ./python3 at the checkout root:
    # with a dot (and then an empty) PATH entry prepended, the checkout impostor never runs
    # (the sentinel it would write stays absent) and the run completes to would-merge through
    # the real interpreter resolved on the sanitized PATH.
    fixtures = []
    for check_id, name, entry in (
            ("config/dot-path-entry-impostor-never-runs", "dot-path-impostor", "."),
            ("config/empty-path-entry-impostor-never-runs", "empty-path-impostor", "")):
        sentinel = Path(tmp) / (name + "-ran")
        fx = _impostor_pr(tmp, name, str(sentinel))
        fixtures.append(fx)
        fx.path = entry + os.pathsep + fx.path
        rc, reports, _fatal = fx.run()
        check(check_id, (rc, _result(reports, 1), sentinel.exists()),
              (0, "would-merge", False))
    # PATH-syntax filtering alone cannot confine the head; the
    # launch funnel now RESOLVES every head to an absolute executable realpath BEFORE anything
    # runs. A caller PATH whose sanitized form is EMPTY (built from empty, dot and relative
    # entries only) refuses the head by name: at the pin, _run_external handed the bare name
    # to the child, whose own PATH walk resolved it IN the child working directory, checkout
    # content (the impostor here would record that it ran).
    unit_dir = Path(tmp) / "unit-impostor"
    unit_dir.mkdir()
    unit_sentinel = unit_dir / "unit-ran"
    unit_impostor = unit_dir / "python3"
    unit_impostor.write_text("#!/bin/sh\n: > '%s'\nexit 7\n" % unit_sentinel,
                             encoding="utf-8")
    unit_impostor.chmod(0o755)
    empty_got = []
    for raw_path in ("", ".", "rel/bin", os.pathsep + "." + os.pathsep + "rel/bin"):
        saved_path = _put_env(dict(PATH=raw_path))
        try:
            code, _out, err = _run_external(["python3", "--version"], unit_dir, 5)
        finally:
            _restore_env(saved_path)
        empty_got.append((code, "sanitized PATH is empty" in err and "python3" in err))
    check("config/empty-sanitized-path-head-refused",
          (empty_got, unit_sentinel.exists()), ([(None, True)] * 4, False))
    # An absolute sanitized PATH that does not carry the head refuses it by name too: a bare
    # name is never handed to the child to resolve.
    vacant = Path(tmp) / "vacant-bin"
    vacant.mkdir()
    saved_path = _put_env(dict(PATH=str(vacant)))
    try:
        code, _out, err = _run_external(["merge-train-absent-program", "--x"], unit_dir, 5)
    finally:
        _restore_env(saved_path)
    check("config/unresolvable-head-refused",
          (code, "cannot be resolved" in err, unit_sentinel.exists()), (None, True, False))
    # A RELATIVE path head (a separator, not absolute) is refused unresolved, by name, even
    # where it names an executable relative to THIS process's working directory, which
    # shutil.which would otherwise return as is and the containment would accept (it lies
    # outside the child working directory): the impostor it names records that it ran.
    rel_cwd = Path(tmp) / "relative-head-cwd"
    (rel_cwd / "rel").mkdir(parents=True)
    rel_sentinel = rel_cwd / "relative-ran"
    rel_impostor = rel_cwd / "rel" / "python3"
    rel_impostor.write_text("#!/bin/sh\n: > '%s'\nexit 7\n" % rel_sentinel, encoding="utf-8")
    rel_impostor.chmod(0o755)
    cwd_before = os.getcwd()
    try:
        os.chdir(str(rel_cwd))
        code, _out, err = _run_external(["rel/python3", "--version"], unit_dir, 5)
    finally:
        os.chdir(cwd_before)
    check("config/relative-head-refused",
          (code, "is a relative path" in err, rel_sentinel.exists()), (None, True, False))
    # A head resolving to anything but an executable regular file (an absolute head naming a
    # plain file without the execute bit, or a directory, which carries one) is refused by
    # name BEFORE any launch: a recording child observes that nothing was started.
    plain = Path(tmp) / "plain-head"
    plain.write_text("not a program\n", encoding="utf-8")
    plain.chmod(0o644)
    started = []

    def _start_recording_child(argv, cwd=None, env=None, timeout=None, push=False,
                               executable=None):
        started.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_child = globals()["_run_child"]
    globals()["_run_child"] = _start_recording_child
    try:
        refused = [(code, "not an executable regular file" in err)
                   for code, _out, err in (_run_external([str(plain), "--version"], unit_dir, 5),
                                           _run_external([str(unit_dir), "--version"], vacant,
                                                         5))]
    finally:
        globals()["_run_child"] = saved_child
    check("config/non-executable-head-refused", (refused, started), ([(None, True)] * 2, []))
    # The accepted boundary (a compatibility check, never red-on-revert evidence: launching the
    # realpath as argv[0] satisfies it too): a head resolved OUTSIDE the confined directories
    # launches by an absolute path, never the bare name, observed through a recording child
    # that also records the file exec-ed.
    outside = Path(tmp) / "outside-bin"
    outside.mkdir()
    os.symlink(sys.executable, outside / "python3")
    launched = []

    def _argv_recording_child(argv, cwd=None, env=None, timeout=None, push=False,
                              executable=None):
        launched.append((list(argv), executable))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_child = globals()["_run_child"]
    saved_path = _put_env(dict(PATH=str(outside)))
    globals()["_run_child"] = _argv_recording_child
    try:
        code, _out, _err = _run_external(["python3", "--version"], unit_dir, 5)
    finally:
        globals()["_run_child"] = saved_child
        _restore_env(saved_path)
    check("config/resolved-head-launched-absolute",
          (code, [os.path.isabs(argv[0]) for argv, _exe in launched],
           launched[0][0][1:] if launched else None),
          (0, [True], ["--version"]))
    # The launch keeps the CANDIDATE's identity: argv[0] is the PATH match with its directory
    # resolved and its own name kept (here a symlink to the interpreter), and the file the
    # child execs is the checked realpath. Red while the realpath itself was argv[0].
    check("config/resolved-head-launched-as-candidate",
          [(argv[0], exe) for argv, exe in launched],
          [(os.path.join(os.path.realpath(str(outside)), "python3"),
            os.path.realpath(sys.executable))])
    # The tool walks the PATH for a bare head itself (see _path_lookup): an entry that does not
    # exist, and one naming a plain file, hold no program and are passed over to the later
    # match, while a name an entry lists that cannot be stat-ed (here a symlink loop named
    # python3) refuses the head by name as cannot-evaluate before any later entry is
    # consulted. Red while shutil.which resolved the head: it passed the loop over too and the
    # later match launched.
    loop_bin = Path(tmp) / "loop-bin"
    loop_bin.mkdir()
    os.symlink("python3", loop_bin / "python3")
    walked = []
    for ahead in (str(Path(tmp) / "absent-bin"), str(plain), str(loop_bin)):
        saved_path = _put_env(dict(PATH=ahead + os.pathsep + str(outside)))
        try:
            code, _out, err = _run_external(["python3", "--version"], unit_dir, 30)
        finally:
            _restore_env(saved_path)
        walked.append((code, "cannot be evaluated" in err))
    check("config/path-walk-entry-boundary", walked, [(0, False), (0, False), (None, True)])
    # The unlistable-entry branch of the same walk, evaluated without a mode-0 directory, so a
    # process that reads every directory (root among them) still runs it: os.listdir raises
    # for one entry, the way it raises EACCES for an unreadable directory, ahead of a
    # directory holding the program. A configured head and git itself are each refused at
    # that entry as cannot-evaluate, before the later match. Red with that branch turned into
    # a pass-over: the head then launched from the later entry, and git resolved there.
    blocked = Path(tmp) / "injected-unlistable-entry"
    blocked.mkdir()
    git_holder = Path(tmp) / "injected-unlistable-git-bin"
    git_holder.mkdir()
    os.symlink(shutil.which("git"), git_holder / "git")
    empty_cwd = Path(tmp) / "injected-unlistable-cwd"
    empty_cwd.mkdir()
    real_listdir = os.listdir

    def _refusing_listdir(path="."):
        if os.fspath(path) == str(blocked):
            raise PermissionError("listing %s refused for this vector" % blocked)
        return real_listdir(path)

    saved_pin = list(_GIT_PROGRAM)
    del _GIT_PROGRAM[:]
    os.listdir = _refusing_listdir
    try:
        saved_path = _put_env(dict(PATH=str(blocked) + os.pathsep + str(outside)))
        try:
            code, _out, err = _run_external(["python3", "--version"], unit_dir, 30)
        finally:
            _restore_env(saved_path)
        saved_path = _put_env(dict(PATH=str(blocked) + os.pathsep + str(git_holder)))
        try:
            git_got = (_git(empty_cwd, "version", ok=None)[0], "")
        except Refuse as exc:
            git_got = (exc.status, exc.reason)
        finally:
            _restore_env(saved_path)
    finally:
        os.listdir = real_listdir
        _GIT_PROGRAM[:] = saved_pin
    check("config/injected-unlistable-path-entry-refused",
          ((code, "cannot be evaluated" in err), (git_got[0], "cannot be evaluated" in git_got[1])),
          ((None, True), ("git-failed", True)))
    # The PATH a child RECEIVES is narrower than the one the tool resolves through (see
    # _child_env): an entry that resolves through /proc or /dev/fd (/proc/self/cwd names the
    # child's own working directory once it has changed into it, and so does a symlink to it),
    # and an entry whose realpath lies inside the child's working directory or a confined
    # directory, are dropped; an entry outside all of them is kept. Red while the child
    # received every absolute entry.
    confined = Path(tmp) / "child-path-confined"
    (confined / "bin").mkdir(parents=True)
    (unit_dir / "bin").mkdir()
    proc_alias = Path(tmp) / "child-path-proc-alias"
    os.symlink("/proc/self/cwd", proc_alias)
    received = []

    def _path_recording_child(argv, cwd=None, env=None, timeout=None, push=False,
                              executable=None):
        received.append((env or dict()).get("PATH"))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_child = globals()["_run_child"]
    saved_path = _put_env(dict(PATH=os.pathsep.join([
        "/proc/self/cwd", "/dev/fd", str(proc_alias), str(unit_dir / "bin"), str(confined / "bin"),
        str(outside)])))
    globals()["_run_child"] = _path_recording_child
    try:
        _run_external([sys.executable, "--version"], unit_dir, 5, confine=(str(confined),))
    finally:
        globals()["_run_child"] = saved_child
        _restore_env(saved_path)
    check("config/child-path-drops-cwd-confined-and-process-entries", received, [str(outside)])
    # An entry that names a confined directory in its TEXT but resolves outside it, through a
    # symlink that directory holds, is dropped as well: the child resolves that symlink again
    # when it walks its PATH, so one re-pointed after the environment was built would name
    # content inside. Red while entries were compared by realpath alone: the alias resolved
    # to the outside directory and was kept.
    os.symlink(str(outside), confined / "alias-bin")
    received_alias = []

    def _alias_recording_child(argv, cwd=None, env=None, timeout=None, push=False,
                               executable=None):
        received_alias.append((env or dict()).get("PATH"))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_child = globals()["_run_child"]
    saved_path = _put_env(dict(PATH=os.pathsep.join([str(confined / "alias-bin"),
                                                     str(outside)])))
    globals()["_run_child"] = _alias_recording_child
    try:
        _run_external([sys.executable, "--version"], unit_dir, 5, confine=(str(confined),))
    finally:
        globals()["_run_child"] = saved_child
        _restore_env(saved_path)
    check("config/child-path-drops-entry-named-inside-confined", received_alias,
          [str(outside)])
    # A #! script head does NOT keep its candidate name: the kernel hands its interpreter the
    # file exec-ed, the checked realpath, so the script sees that realpath as $0 (the scope the
    # _resolve_program docstring states; the Python interpreter reads argv[0] instead, see
    # config/venv-interpreter-keeps-identity). A compatibility check, never red-on-revert
    # evidence: any launch that execs the checked realpath satisfies it, whatever argv[0] names,
    # and the funnel already did so before the child PATH was narrowed.
    script_real = Path(tmp) / "script-real"
    script_real.mkdir()
    (script_real / "probe-script").write_text('#!/bin/sh\nprintf "%s\\n" "$0"\n',
                                              encoding="utf-8")
    (script_real / "probe-script").chmod(0o755)
    script_bin = Path(tmp) / "script-bin"
    script_bin.mkdir()
    os.symlink(str(script_real / "probe-script"), script_bin / "probe-script")
    saved_path = _put_env(dict(PATH=str(script_bin)))
    try:
        code, out, _err = _run_external(["probe-script"], unit_dir, 30)
    finally:
        _restore_env(saved_path)
    check("config/script-head-sees-realpath-as-dollar-zero", (code, out.splitlines()),
          (0, [os.path.realpath(str(script_real / "probe-script"))]))
    # Observed end to end: a venv interpreter found on PATH keeps its venv, because argv[0]
    # names the venv's bin/python3 and the interpreter finds the pyvenv.cfg beside it (the
    # child reports sys.prefix as the venv). Red while the realpath was launched: the child
    # then reported the base installation.
    venv = Path(tmp) / "outside-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(
        "home = %s\n" % os.path.dirname(os.path.realpath(sys.executable)), encoding="utf-8")
    os.symlink(sys.executable, venv / "bin" / "python3")
    saved_path = _put_env(dict(PATH=str(venv / "bin")))
    try:
        code, out, _err = _run_external(
            ["python3", "-I", "-B", "-c",
             "import sys; print(sys.prefix != sys.base_prefix); print(sys.prefix)"],
            unit_dir, 30)
    finally:
        _restore_env(saved_path)
    check("config/venv-interpreter-keeps-identity", (code, out.splitlines()),
          (0, ["True", os.path.realpath(str(venv))]))
    # A confined directory of "/" holds every path (a plain base + os.sep prefix test would
    # match nothing below it), so every head is refused by name.
    code, _out, err = _run_external([sys.executable, "--version"], unit_dir, 5, confine=("/",))
    check("config/root-directory-confine-refuses-every-head",
          (code, "lies inside '/'" in err), (None, True))
    # End to end, each red at the pin with the impostor's
    # sentinel written: an absolute PATH entry naming a checkout directory (the PR worktree,
    # which carries the committed impostor), a /proc/self/cwd entry (an absolute alias of
    # whatever the RESOLVING process has as its working directory; at the pin the CHILD
    # resolved it, after its chdir into the merged scratch checkout, so the scratch impostor
    # ran), and a PATH directory whose python3 is a SYMLINK into the checkout are each refused
    # by realpath containment before any checkout content runs: the run refuses
    # regenerate-failed naming the containment, and the sentinel stays absent.
    for check_id, name, proc_cwd in (
            ("config/absolute-checkout-path-entry-head-refused",
             "abs-checkout-path-impostor", False),
            ("config/proc-self-cwd-path-entry-head-refused",
             "proc-cwd-path-impostor", True),
            ("config/checkout-symlink-alias-head-refused",
             "symlink-alias-impostor", False)):
        sentinel = Path(tmp) / (name + "-ran")
        fx = _impostor_pr(tmp, name, str(sentinel))
        fixtures.append(fx)
        if check_id.endswith("symlink-alias-head-refused"):
            alias_bin = fx.base / "alias-bin"
            alias_bin.mkdir()
            os.symlink(str(fx.base / "wt-1" / "python3"), str(alias_bin / "python3"))
            entry = str(alias_bin)
        elif proc_cwd:
            entry = "/proc/self/cwd"
        else:
            entry = str(fx.base / "wt-1")
        fx.path = entry + os.pathsep + fx.path
        cwd_before = os.getcwd()
        try:
            if proc_cwd:
                # The resolver runs in THIS process: point its /proc/self/cwd at the PR
                # worktree, the checkout alias the containment must refuse.
                os.chdir(str(fx.base / "wt-1"))
            rc, reports, _fatal = fx.run()
        finally:
            os.chdir(cwd_before)
        reason = reports[0]["reason"] if reports else ""
        check(check_id,
              (rc, _result(reports, 1), sentinel.exists(),
               "never runs as a configured program head" in reason),
              (1, "regenerate-failed", False, True))
    # The launch DIRECTORY is confined as well as the file exec-ed: a venv inside the PR
    # worktree whose bin/python3 is a symlink to an interpreter OUTSIDE it would, launched
    # under its own name, read that venv's pyvenv.cfg and run the .pth files in its
    # site-packages (worktree content, even under -I; the .pth here records that it ran), so
    # the head is refused by name.
    sentinel = Path(tmp) / "checkout-venv-pth-ran"
    fx = _fixture(tmp, "checkout-venv-impostor")
    fixtures.append(fx)
    venv_in = Path(_stale_pr(fx)) / "venv"
    (venv_in / "bin").mkdir(parents=True)
    (venv_in / "pyvenv.cfg").write_text(
        "home = %s\n" % os.path.dirname(os.path.realpath(sys.executable)), encoding="utf-8")
    os.symlink(sys.executable, venv_in / "bin" / "python3")
    purelib = Path(sysconfig.get_path("purelib", scheme="venv",
                                      vars=dict(base=str(venv_in), platbase=str(venv_in))))
    purelib.mkdir(parents=True)
    (purelib / "probe.pth").write_text("import os; open(%r, 'w').close()\n" % str(sentinel),
                                       encoding="utf-8")
    os.remove(fx.bin / "python3")
    fx.path = str(venv_in / "bin") + os.pathsep + fx.path
    rc, reports, _fatal = fx.run()
    check("config/checkout-venv-directory-head-refused",
          (rc, _result(reports, 1), sentinel.exists(),
           "launch directory" in _reason(reports, 1)),
          (1, "regenerate-failed", False, True))
    # Every repository directory of the run is confined, never only the toplevel and the
    # worktree of the PR being processed: with a PATH entry naming ANOTHER PR's worktree,
    # whose python3 records that it ran, processing PR 1 refuses the configured head by name.
    # Red while the confinement held the toplevel and PR 1's worktree alone: the other
    # worktree's program ran.
    sentinel = Path(tmp) / "sibling-worktree-ran"
    fx = _fixture(tmp, "sibling-worktree-impostor")
    fixtures.append(fx)
    _stale_pr(fx)
    sibling = Path(fx.add_pr(2, "feat/y", dict([("src/d.txt", "delta\n")])))
    for name in ("python3", "gh"):
        impostor = sibling / name
        impostor.write_text("#!/bin/sh\n: > '%s-%s'\nexit 7\n" % (sentinel, name),
                            encoding="utf-8")
        impostor.chmod(0o755)
    os.remove(fx.bin / "python3")
    fx.path = str(sibling) + os.pathsep + fx.path
    rc, reports, _fatal = fx.run(only=set([1]))
    check("config/sibling-worktree-path-entry-head-refused",
          (rc, _result(reports, 1), os.path.lexists("%s-python3" % sentinel),
           "lies inside" in _reason(reports, 1)),
          (1, "regenerate-failed", False, True))
    # gh is confined the same way: once the fixture's own gh is gone, the same PATH entry
    # resolves gh to the other PR's worktree, and the PR list refuses it by name before any
    # PR is processed (red while gh was confined to the toplevel alone: the impostor ran).
    os.remove(fx.bin / "gh")
    rc, reports, fatal = fx.run(only=set([1]))
    check("config/gh-sibling-worktree-head-refused",
          (rc, reports, os.path.lexists("%s-gh" % sentinel), "lies inside" in (fatal or "")),
          (2, [], False, True))
    # The run's PRIVATE base repository is confined too: a python3 planted there once the
    # directory exists (by a wrapper around _base_fetch) and named by a PATH entry prepended
    # at that moment is refused by name. Red while the base repository was outside the
    # confinement: the planted program ran.
    sentinel = Path(tmp) / "base-repository-ran"
    fx = _fixture(tmp, "base-repository-impostor")
    fixtures.append(fx)
    _stale_pr(fx)
    real_base_fetch = globals()["_base_fetch"]

    def _planting_base_fetch(root, run_dir, remote, base):
        fetched = real_base_fetch(root, run_dir, remote, base)
        planted = Path(fetched[0]) / "python3"
        planted.write_text("#!/bin/sh\n: > '%s'\nexit 7\n" % sentinel, encoding="utf-8")
        planted.chmod(0o755)
        os.environ["PATH"] = fetched[0] + os.pathsep + os.environ.get("PATH", os.defpath)
        return fetched

    saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
    globals()["_base_fetch"] = _planting_base_fetch
    try:
        rc, reports, _fatal = fx.run()
    finally:
        globals()["_base_fetch"] = real_base_fetch
        _restore_env(saved_path)
    check("config/base-repository-path-entry-head-refused",
          (rc, _result(reports, 1), sentinel.exists(), "lies inside" in _reason(reports, 1)),
          (1, "regenerate-failed", False, True))
    check("config/path-entries-invariants", _invariants(*fixtures), [])


def case_gitpin(tmp):
    # git itself is resolved ONCE through _resolve_program (see _git_program) and launched by
    # that absolute launch path with its checked realpath as the file it execs: no git child
    # repeats a PATH walk, so a git that a PATH entry names in a child's working directory
    # never runs, and a git that resolves into a repository directory is refused by name
    # before the run's first git launch. Each decoy records into its sentinel that it ran and
    # then hands its argv to the real git, so a run through it would otherwise look normal.
    # Against the earlier funnel, which launched the bare name git and resolved or checked
    # nothing, only the first four vectors evaluate, and they are red (a decoy runs, or the
    # run is not refused); the case then raises at the walk patch, a name that funnel lacks,
    # so the vectors after them cannot evaluate there, and their red evidence is each one's
    # own targeted mutation, never that funnel.
    real_git = shutil.which("git")
    fixtures = []

    def decoy(directory, sentinel):
        path = Path(directory) / "git"
        path.write_text("#!/bin/sh\n: > '%s'\nexec '%s' \"$@\"\n" % (sentinel, real_git),
                        encoding="utf-8")
        path.chmod(0o755)

    empty = Path(tmp) / "gitpin-empty-cwd"
    empty.mkdir()
    fx = _fixture(tmp, "gitpin-author-decoy")
    fixtures.append(fx)
    wt = _stale_pr(fx)
    sentinel = Path(tmp) / "gitpin-author-decoy-ran"
    decoy(wt, sentinel)
    decoy(fx.main, sentinel)
    # The author-side funnel itself: with /proc/self/cwd first on PATH and a decoy git in the
    # author's worktree, the branch-name read runs the pinned git, never the decoy the child's
    # working directory holds.
    saved_path = _put_env(dict(PATH="/proc/self/cwd" + os.pathsep
                               + os.environ.get("PATH", os.defpath)))
    try:
        code = _git_author(wt, "check-ref-format", "--branch", "feat/x", ok=None)[0]
    finally:
        _restore_env(saved_path)
    check("git/author-funnel-proc-self-cwd-decoy-never-runs", (code, sentinel.exists()),
          (0, False))
    # A whole dry run with that PATH, started from an empty directory: the run resolves git
    # there (/proc/self/cwd holds none) to a program outside the repository, and every launch,
    # in the author's worktrees included, runs that absolute path, so no decoy runs.
    fx.path = "/proc/self/cwd" + os.pathsep + fx.path
    cwd_before = os.getcwd()
    try:
        os.chdir(str(empty))
        rc, reports, _fatal = fx.run()
    finally:
        os.chdir(cwd_before)
    check("git/proc-self-cwd-decoy-in-author-worktree-never-runs",
          (rc, _result(reports, 1), sentinel.exists()), (0, "would-merge", False))
    # Started from INSIDE the author's main worktree, the same PATH resolves git to the decoy
    # there; its realpath lies inside the repository, so the run refuses git by name before
    # its first git launch.
    try:
        os.chdir(str(fx.main))
        rc, reports, fatal = fx.run()
    finally:
        os.chdir(cwd_before)
    check("git/proc-self-cwd-resolving-into-author-worktree-refused",
          (rc, reports, sentinel.exists(), "lies inside" in (fatal or "")),
          (2, [], False, True))
    # A PATH entry naming ANOTHER PR's worktree that holds a decoy git: the filesystem walk
    # around the repository (run before any git launch) lists that worktree, so the run
    # refuses git by name before its first launch.
    fx2 = _fixture(tmp, "gitpin-sibling-decoy")
    fixtures.append(fx2)
    _stale_pr(fx2)
    sibling = Path(fx2.add_pr(2, "feat/y", dict([("src/d.txt", "delta\n")])))
    sentinel2 = Path(tmp) / "gitpin-sibling-decoy-ran"
    decoy(sibling, sentinel2)
    fx2.path = str(sibling) + os.pathsep + fx2.path
    rc, reports, fatal = fx2.run()
    check("git/sibling-worktree-path-entry-git-refused",
          (rc, reports, sentinel2.exists(),
           "before any git launch" in (fatal or "") and "lies inside" in (fatal or "")),
          (2, [], False, True))
    # The walk is an over-approximation, and the worktree list git itself reports is checked
    # again before any later launch: with the walk blinded, the same run still refuses git by
    # name once that list names the other worktree (the decoy has then run for the discovery
    # reads, the disclosed residual of a layout the walk does not model).
    real_walk = globals()["_repository_dirs_on_disk"]
    globals()["_repository_dirs_on_disk"] = lambda _repo: []
    try:
        rc, reports, fatal = fx2.run()
    finally:
        globals()["_repository_dirs_on_disk"] = real_walk
    check("git/worktree-list-recheck-refuses-unwalked-git",
          (rc, reports, "after discovery" in (fatal or "") and "lies inside" in (fatal or "")),
          (2, [], True))
    # A run whose working directory no longer exists (with a relative root) refuses git by
    # name instead of raising: the confinement cannot read that directory's realpath.
    gone = Path(tmp) / "gitpin-gone-cwd"
    gone.mkdir()
    try:
        os.chdir(str(gone))
        gone.rmdir()
        try:
            rc, reports, fatal = run_train(".")
        except OSError as exc:
            rc, reports, fatal = "raised", [], str(exc)
    finally:
        os.chdir(cwd_before)
    check("git/deleted-working-directory-refused",
          (rc, reports, "cannot be confined" in (fatal or "")), (2, [], True))
    # Every git launch re-checks the pinned paths against ITS OWN working directory (the
    # private base repository and each private checkout among them): a launch whose working
    # directory holds the pinned git refuses it by name as git-failed, while a launch from
    # elsewhere runs it. Red while only the run's repository directories confined git.
    holder = Path(tmp) / "gitpin-holder"
    (holder / "bin").mkdir(parents=True)
    os.symlink(real_git, holder / "bin" / "git")
    saved_pin = list(_GIT_PROGRAM)
    del _GIT_PROGRAM[:]
    saved_path = _put_env(dict(PATH=str(holder / "bin")))
    try:
        outside_code = _git(empty, "version", ok=None)[0]
        try:
            inside = (_git(holder, "version", ok=None)[0], "")
        except Refuse as exc:
            inside = (exc.status, exc.reason)
    finally:
        _restore_env(saved_path)
        _GIT_PROGRAM[:] = saved_pin
    check("git/launch-directory-holding-pinned-git-refused",
          (outside_code, inside[0], "lies inside" in inside[1]), (0, "git-failed", True))
    # The realpath is read again at EVERY git launch: with the pin taken through a launch path
    # that is a symlink, re-pointing that symlink at a decoy afterwards refuses the next launch
    # as git-failed, naming the change, and the decoy never runs. Red while each launch only
    # re-checked the pinned paths for containment: the launch then went ahead with argv[0]
    # naming the decoy and the pinned file exec-ed beside it.
    swap_bin = Path(tmp) / "gitpin-swap-bin"
    swap_bin.mkdir()
    os.symlink(real_git, swap_bin / "git")
    swap_decoy = Path(tmp) / "gitpin-swap-decoy"
    swap_decoy.mkdir()
    swap_sentinel = Path(tmp) / "gitpin-swap-decoy-ran"
    decoy(swap_decoy, swap_sentinel)
    saved_pin = list(_GIT_PROGRAM)
    del _GIT_PROGRAM[:]
    saved_path = _put_env(dict(PATH=str(swap_bin)))
    try:
        first = _git(empty, "version", ok=None)[0]
        os.remove(swap_bin / "git")
        os.symlink(str(swap_decoy / "git"), swap_bin / "git")
        try:
            second = (_git(empty, "version", ok=None)[0], "")
        except Refuse as exc:
            second = (exc.status, exc.reason)
    finally:
        _restore_env(saved_path)
        _GIT_PROGRAM[:] = saved_pin
    check("git/pinned-launch-path-realpath-rechecked",
          (first, second[0], "now resolves to" in second[1], swap_sentinel.exists()),
          (0, "git-failed", True, False))
    # The file each git launch execs is the checked realpath, handed to the child as
    # executable= beside argv[0]: with the pin taken through a launch path that is a symlink,
    # the recorded launch names that path as argv[0] and the realpath as the file exec-ed. Red
    # with executable=real dropped from _git: the file exec-ed is then whatever argv[0] names
    # at exec time, and the recorder sees None. A compatibility check, never red-on-revert
    # evidence for the child PATH narrowing: _git already passed executable=real before it.
    exec_seen = []

    def _exec_recording_child(argv, cwd=None, env=None, timeout=None, push=False,
                              executable=None):
        exec_seen.append((argv[0], executable))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_pin = list(_GIT_PROGRAM)
    del _GIT_PROGRAM[:]
    saved_path = _put_env(dict(PATH=str(holder / "bin")))
    real_child = globals()["_run_child"]
    globals()["_run_child"] = _exec_recording_child
    try:
        _git(empty, "version")
    finally:
        globals()["_run_child"] = real_child
        _restore_env(saved_path)
        _GIT_PROGRAM[:] = saved_pin
    check("git/launch-execs-checked-realpath", exec_seen,
          [(os.path.join(os.path.realpath(str(holder / "bin")), "git"),
            os.path.realpath(real_git))])
    # No git on the sanitized PATH: the run refuses git by name before any git launch and
    # exits 2. Red when that refusal is removed: the first discovery read then fails, and the
    # fatal message names that read instead. A compatibility check, never red-on-revert
    # evidence for the PATH walk or the child PATH narrowing: the run already refused an
    # unresolvable git this way before either.
    vacant = Path(tmp) / "gitpin-no-git-bin"
    vacant.mkdir()
    fx_none = _fixture(tmp, "gitpin-missing-git")
    fixtures.append(fx_none)
    fx_none.path = str(vacant)
    saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
    try:
        rc, reports, fatal = fx_none.run()
    finally:
        _restore_env(saved_path)
    check("git/missing-git-refused-before-any-launch",
          (rc, reports, "before any git launch" in (fatal or "")
           and "cannot be resolved" in (fatal or "")), (2, [], True))
    # Every child of a whole run receives a PATH with no entry inside a repository directory
    # of the run and none resolving through /proc or /dev/fd (see _child_env). Planted here:
    # the main worktree's tools directory, the common git directory, another PR's worktree,
    # /proc/self/cwd, /dev/fd and, once each exists, the private base repository, the private
    # checkout's directory and the fresh checkout's directory (each checkout lies one level
    # below its directory, so the working-directory rule never drops the directory itself;
    # only its _RUN_DIRS entry does). None of them holds a program the tool resolves, so the
    # run goes ahead, and no child's PATH may carry any of them. Red while every child
    # received the caller's absolute entries, and red with either checkout directory's
    # _RUN_DIRS append removed.
    fx_paths = _fixture(tmp, "gitpin-child-path")
    fixtures.append(fx_paths)
    _stale_pr(fx_paths)
    other = fx_paths.add_pr(2, "feat/y", dict([("src/d.txt", "delta\n")]))
    planted = [str(fx_paths.main / "tools"), str(fx_paths.main / ".git"), str(other),
               "/proc/self/cwd", "/dev/fd"]
    fx_paths.path = os.pathsep.join(planted + [fx_paths.path])
    child_paths, in_run = [], []
    real_base_fetch = globals()["_base_fetch"]
    real_run_train = globals()["run_train"]

    def _path_seeing_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        # Only the run's own children: the fixture's reads around the run are not the tool's.
        if in_run:
            child_paths.append((env or dict()).get("PATH", "").split(os.pathsep))
        return real_child(argv, cwd=cwd, env=env, timeout=timeout, push=push,
                          executable=executable)

    def _flagged_run_train(*args, **kwargs):
        in_run.append(True)
        try:
            return real_run_train(*args, **kwargs)
        finally:
            in_run.pop()

    def _base_entry_fetch(root, run_dir, remote, base):
        fetched = real_base_fetch(root, run_dir, remote, base)
        planted.append(fetched[0])
        os.environ["PATH"] = fetched[0] + os.pathsep + os.environ.get("PATH", os.defpath)
        return fetched

    real_mkdtemp = tempfile.mkdtemp

    def _checkout_entry_mkdtemp(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        if in_run and kwargs.get("prefix") in ("merge-train-scratch-", "merge-train-fresh-"):
            planted.append(made)
            os.environ["PATH"] = made + os.pathsep + os.environ.get("PATH", os.defpath)
        return made

    saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
    globals()["_run_child"] = _path_seeing_child
    globals()["_base_fetch"] = _base_entry_fetch
    globals()["run_train"] = _flagged_run_train
    tempfile.mkdtemp = _checkout_entry_mkdtemp
    try:
        os.chdir(str(empty))
        rc, reports, _fatal = fx_paths.run(only=set([1]))
    finally:
        os.chdir(cwd_before)
        globals()["_run_child"] = real_child
        globals()["_base_fetch"] = real_base_fetch
        globals()["run_train"] = real_run_train
        tempfile.mkdtemp = real_mkdtemp
        _restore_env(saved_path)
    check("git/child-path-drops-repository-and-process-entries",
          (rc, _result(reports, 1), len(planted), len(child_paths) > 0,
           sorted(set(e for path in child_paths for e in path if e in planted))),
          (0, "would-merge", 8, True, []))
    # Every transport command of an --apply run (the base fetch, each ls-remote, both
    # --shared clones, the PR-branch fetch and the push) is launched from an empty directory,
    # and every one but the clones names its repository with --git-dir (see _git_transport),
    # observed at each launch. Red while they ran in the private base repository, the private
    # checkout and the author's worktree.
    fx_cwd = _fixture(tmp, "gitpin-transport-cwd")
    fixtures.append(fx_cwd)
    _stale_pr(fx_cwd)
    pins = ["-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
    transports = []

    def _cwd_seeing_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        sub = _audited_subcommand(argv[7:]) if list(argv[1:7]) == pins else None
        if sub in ("fetch", "push", "ls-remote", "clone"):
            transports.append((sub, os.listdir(str(cwd)),
                               any(word.startswith("--git-dir=") for word in argv)))
        return real_child(argv, cwd=cwd, env=env, timeout=timeout, push=push,
                          executable=executable)

    saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
    globals()["_run_child"] = _cwd_seeing_child
    try:
        rc, reports, _fatal = fx_cwd.run(apply=True)
    finally:
        globals()["_run_child"] = real_child
        _restore_env(saved_path)
    check("git/transport-runs-from-empty-private-directory",
          (rc, _result(reports, 1), sorted(set(row[0] for row in transports)),
           [row for row in transports if row[1] != [] or row[2] != (row[0] != "clone")]),
          (0, "pushed", ["clone", "fetch", "ls-remote", "push"], []))
    # End to end, an ssh remote with /proc/self/cwd first on PATH and a PR that commits an
    # executable ssh at its tree root, a decoy that records that it ran. A stand-in ssh outside
    # every repository directory carries the transport by running the remote command locally.
    # Red while the push ran from the merged private checkout with /proc/self/cwd on its PATH:
    # git's own PATH walk for ssh then ran the PR's decoy. Either defence alone (the empty
    # transport directory, or /proc/self/cwd dropped from every child's PATH) keeps it green;
    # each has its own vector above.
    shim_bin = Path(tmp) / "gitpin-ssh-shim"
    shim_bin.mkdir()
    shim = shim_bin / "ssh"
    shim.write_text('#!/bin/sh\nfor last; do :; done\nexec /bin/sh -c "$last"\n',
                    encoding="utf-8")
    shim.chmod(0o755)
    fx_ssh = _fixture(tmp, "gitpin-ssh-decoy")
    fixtures.append(fx_ssh)
    wt_ssh = Path(fx_ssh.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")])))
    ssh_sentinel = Path(tmp) / "gitpin-ssh-decoy-ran"
    (wt_ssh / "ssh").write_text("#!/bin/sh\n: > '%s'\nexec '%s' \"$@\"\n" % (ssh_sentinel, shim),
                                encoding="utf-8")
    (wt_ssh / "ssh").chmod(0o755)
    head = fx_ssh.commit(wt_ssh, "pr commits an ssh at its tree root")
    _git(wt_ssh, "push", "-q", "origin", "%s:refs/heads/feat/x" % head)
    fx_ssh.prs[0]["headRefOid"] = head
    fx_ssh.set_gh()
    fx_ssh.advance_main(dict([("src/c.txt", "charlie\n")]))
    _git(fx_ssh.main, "config", "remote.origin.url", "ssh://example.invalid" + str(fx_ssh.remote))
    fx_ssh.path = "/proc/self/cwd" + os.pathsep + str(shim_bin) + os.pathsep + fx_ssh.path
    saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
    try:
        os.chdir(str(empty))
        rc, reports, _fatal = fx_ssh.run(apply=True)
    finally:
        os.chdir(cwd_before)
        _restore_env(saved_path)
    check("git/transport-decoy-ssh-never-runs",
          (rc, _result(reports, 1), ssh_sentinel.exists()), (0, "pushed", False))
    fixtures += _unreadable_path_entry(tmp)
    check("git/pin-invariants", _invariants(*fixtures), [])


def _unreadable_path_entry(tmp):
    # The tool walks the PATH for a bare name itself (see _path_lookup) and refuses, as
    # cannot-evaluate, at the first entry it cannot list, before any later entry is consulted:
    # here a mode-0 directory ahead of a directory holding the program. Red while shutil.which
    # resolved the name: it passed the unreadable entry over and took the later match, so the
    # configured head launched and the whole run went ahead with the later git. A process that
    # still reads the mode-0 directory (root, CAP_DAC_OVERRIDE; observed with _can_read) cannot
    # produce the error: both checks are then recorded as NOT RUN through skip(). Returns the
    # fixtures it ran through the tool.
    locked = Path(tmp) / "unreadable-path-entry"
    locked.mkdir()
    holder = Path(tmp) / "unreadable-path-holder"
    holder.mkdir()
    os.symlink(sys.executable, holder / "python3")
    launch_cwd = Path(tmp) / "unreadable-path-cwd"
    launch_cwd.mkdir()
    os.chmod(str(locked), 0)
    try:
        if _can_read(str(locked)):
            for name in ("config/unreadable-path-entry-head-refused",
                         "git/unreadable-path-entry-refuses-git"):
                skip(name, "this process reads a mode-0 directory, so the permission error "
                           "cannot be produced")
            return []
        saved_path = _put_env(dict(PATH=str(locked) + os.pathsep + str(holder)))
        try:
            code, _out, err = _run_external(["python3", "--version"], launch_cwd, 30)
        finally:
            _restore_env(saved_path)
        check("config/unreadable-path-entry-head-refused", (code, "cannot be evaluated" in err),
              (None, True))
        fx = _fixture(tmp, "unreadable-path-git")
        _stale_pr(fx)
        fx.path = str(locked) + os.pathsep + fx.path
        saved_path = _put_env(dict(PATH=os.environ.get("PATH", os.defpath)))
        try:
            rc, reports, fatal = fx.run()
        finally:
            _restore_env(saved_path)
        check("git/unreadable-path-entry-refuses-git",
              (rc, reports, "before any git launch" in (fatal or "")
               and "cannot be evaluated" in (fatal or "")), (2, [], True))
        return [fx]
    finally:
        os.chmod(str(locked), 0o755)


def case_dry(tmp):
    fx = _fixture(tmp, "dry")
    wt = _stale_pr(fx)
    before = _snapshot(wt)
    remote_before = fx.remote_ref("feat/x")
    rc, reports, _fatal = fx.run()
    check("dry/would-merge", (rc, _result(reports, 1)), (0, "would-merge"))
    check("dry/changes-nothing", (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
                                  fx.pushes()), ([], remote_before, []))
    # An explicitly requested PR the run did not find must be a
    # non-success outcome reported by number, and a found selection still processes normally.
    rc2, reports2, _fatal = fx.run(only=set([999]))
    check("select/absent-pr-nonzero",
          (rc2, [(r["pr"], r["result"]) for r in reports2]), (1, [(999, "pr-not-found")]))
    rc3, reports3, _fatal = fx.run(only=set([1, 999]))
    check("select/mixed-selection-processes-found-and-reports-absent",
          (rc3, [(r["pr"], r["result"]) for r in reports3]),
          (1, [(1, "would-merge"), (999, "pr-not-found")]))
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
    tool_pushes = [a for a in GIT_AUDIT if _audited_subcommand(a) == "push" and "--porcelain" in a]
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
    try:
        rc, reports, _fatal = fx.run(apply=True)
    except _AuditRefused as exc:
        # With the branch-name guard reverted, the argv audit refuses the inexpressible lease at
        # the push; surface EXACTLY that refusal as a result shape so red-on-revert reads this
        # case's own NAMED assertions going red, never a crash.
        # The handler matches the audit's DEDICATED exception type, never message text, so any
        # other exception, an AssertionError carrying the audit's own words included, passes
        # through into crash recording and never masquerades as mutation discrimination (pinned
        # by meta/unrelated-assertion-reaches-crash-recording and
        # meta/phrase-collision-assertion-reaches-crash-recording).
        rc, reports = "argv-audit refused: %s" % exc, []
    worked = [a for a in GIT_AUDIT[audit:] if _audited_subcommand(a) in (
        "clone", "merge", "commit-tree", "push", "checkout", "add", "write-tree")]
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


def case_audit(tmp):
    # Pure predicate checks over forbidden and allowed argv forms: no tool run, no real git call
    # (the funnel probe at the end records argv through a replaced _run_child and launches
    # nothing). Both predicates must refuse every forbidden form and accept the one lease shape,
    # and they must AGREE on every probe.
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
    # Forbidden operations behind git global options; each
    # predicate is probed INDEPENDENTLY so a blind spot in one cannot hide behind the pair.
    prefixed_force = ["-c", "color.ui=false", "push", "--force", "origin", "feat/x"]
    prefixed_reset = ["-C", "/tmp/example", "reset", "--hard"]
    probes.append(list(prefixed_force))
    probes.append(list(prefixed_reset))
    check("audit/global-option-push-force-funnel", _forbidden(prefixed_force), True)
    check("audit/global-option-push-force-audit", _audit_violation(prefixed_force), True)
    check("audit/global-option-reset-funnel", _forbidden(prefixed_reset), True)
    check("audit/global-option-reset-audit", _audit_violation(prefixed_reset), True)
    # An argv whose subcommand cannot be resolved is forbidden outright (fail closed), and a
    # benign global-option prefix still resolves to its harmless subcommand (a COMPATIBILITY
    # check guarding against forbidding too much, NOT a red-on-revert vector: both predicates
    # return False for this argv without the global-option resolution too).
    check("audit/global-option-unresolvable-fails-closed",
          (probed(["--exec-path"]), probed(["-c"]), probed([])), (both, both, both))
    check("audit/global-option-benign-resolves",
          probed(["-c", "color.ui=false", "status", "--porcelain"]), neither)
    # A forbidden operation DEFINED through configuration (-c or
    # --config-env): alias dispatch, the push destination and mirror keys, and the push.*
    # section. The two predicates' option and key tables are DELIBERATELY identical in
    # membership (they must agree on every argv; their independence is in implementation, not
    # in membership), so each probe below tests each predicate INDEPENDENTLY: a gap shared by
    # both tables still turns these vectors red.
    alias_push = ["-c", "alias.x=push --force", "x", "origin", "feat/x"]
    alias_reset = ["-c", "alias.r=reset --hard", "r"]
    env_alias = ["--config-env=alias.x=EVIL", "x"]
    remote_mirror = ["-c", "remote.origin.mirror=true", "push", "origin"]
    remote_push = ["-c", "remote.origin.push=+refs/heads/*:refs/heads/*", "push", "origin"]
    push_section = ["-c", "push.default=matching", "status"]
    for vector in (alias_push, alias_reset, env_alias, remote_mirror, remote_push,
                   push_section):
        probes.append(list(vector))
    check("audit/config-key-alias-push-funnel", _forbidden(alias_push), True)
    check("audit/config-key-alias-push-audit", _audit_violation(alias_push), True)
    check("audit/config-key-alias-reset-funnel", _forbidden(alias_reset), True)
    check("audit/config-key-alias-reset-audit", _audit_violation(alias_reset), True)
    check("audit/config-key-config-env-alias-funnel", _forbidden(env_alias), True)
    check("audit/config-key-config-env-alias-audit", _audit_violation(env_alias), True)
    check("audit/config-key-remote-mirror-funnel", _forbidden(remote_mirror), True)
    check("audit/config-key-remote-mirror-audit", _audit_violation(remote_mirror), True)
    check("audit/config-key-remote-push-funnel", _forbidden(remote_push), True)
    check("audit/config-key-remote-push-audit", _audit_violation(remote_push), True)
    check("audit/config-key-push-section-funnel", _forbidden(push_section), True)
    check("audit/config-key-push-section-audit", _audit_violation(push_section), True)
    # The remaining push-moving keys, the case-folded
    # spellings and the separated --config-env form, each probed against each predicate
    # independently (both tables are deliberately identical in membership, as above).
    for funnel_id, audit_id, vector in (
            ("audit/config-key-pushurl-funnel", "audit/config-key-pushurl-audit",
             ["-c", "remote.origin.pushurl=/tmp/elsewhere", "status"]),
            ("audit/config-key-pushurl-config-env-funnel",
             "audit/config-key-pushurl-config-env-audit",
             ["--config-env=remote.origin.pushurl=EVIL", "status"]),
            ("audit/config-key-pushurl-config-env-separated-funnel",
             "audit/config-key-pushurl-config-env-separated-audit",
             ["--config-env", "remote.origin.pushurl=EVIL", "status"]),
            ("audit/config-key-case-folded-alias-funnel",
             "audit/config-key-case-folded-alias-audit",
             ["-c", "Alias.x=push --force", "x"]),
            ("audit/config-key-case-folded-remote-funnel",
             "audit/config-key-case-folded-remote-audit",
             ["-c", "REMOTE.origin.PushUrl=/tmp/elsewhere", "status"]),
            ("audit/config-key-config-env-separated-alias-funnel",
             "audit/config-key-config-env-separated-alias-audit",
             ["--config-env", "alias.x=EVIL", "x"]),
            ("audit/config-key-remote-url-funnel", "audit/config-key-remote-url-audit",
             ["-c", "remote.origin.url=/tmp/elsewhere", "status"]),
            ("audit/config-key-push-default-funnel", "audit/config-key-push-default-audit",
             ["-c", "remote.pushDefault=other", "status"]),
            ("audit/config-key-branch-pushremote-funnel",
             "audit/config-key-branch-pushremote-audit",
             ["-c", "branch.feat/x.pushRemote=other", "status"]),
            ("audit/config-key-url-insteadof-funnel", "audit/config-key-url-insteadof-audit",
             ["-c", "url./tmp/elsewhere.insteadOf=/tmp/origin", "status"]),
            ("audit/config-key-url-push-insteadof-funnel",
             "audit/config-key-url-push-insteadof-audit",
             ["-c", "url./tmp/elsewhere.pushInsteadOf=/tmp/origin", "status"]),
            ("audit/config-key-include-path-funnel", "audit/config-key-include-path-audit",
             ["-c", "include.path=/tmp/extra.gitconfig", "status"])):
        probes.append(list(vector))
        check(funnel_id, _forbidden(vector), True)
        check(audit_id, _audit_violation(vector), True)
    # Conditional includes (the camel-case and folded
    # spellings and the config-env form), and --config-env keys read before the LAST "=" (the
    # trailing word names an environment variable), so a subsection carrying "=" cannot shift
    # the variable out of the key; attached and separated forms, each probed against each
    # predicate independently (both tables stay deliberately identical in membership, as
    # above).
    for funnel_id, audit_id, vector in (
            ("audit/config-key-includeif-funnel", "audit/config-key-includeif-audit",
             ["-c", "includeIf.gitdir:/tmp/x/.path=/tmp/extra.gitconfig", "status"]),
            ("audit/config-key-includeif-case-folded-funnel",
             "audit/config-key-includeif-case-folded-audit",
             ["-c", "INCLUDEIF.gitdir:/tmp/x/.PATH=/tmp/extra.gitconfig", "status"]),
            ("audit/config-key-includeif-config-env-funnel",
             "audit/config-key-includeif-config-env-audit",
             ["--config-env=includeIf.gitdir:/tmp/x/.path=EXTRA", "status"]),
            ("audit/config-env-last-equals-pushurl-funnel",
             "audit/config-env-last-equals-pushurl-audit",
             ["--config-env=remote.a=b.pushurl=VAR", "status"]),
            ("audit/config-env-last-equals-pushurl-separated-funnel",
             "audit/config-env-last-equals-pushurl-separated-audit",
             ["--config-env", "remote.a=b.pushurl=VAR", "status"]),
            ("audit/config-env-last-equals-insteadof-funnel",
             "audit/config-env-last-equals-insteadof-audit",
             ["--config-env=url./x=1.insteadOf=VAR", "status"]),
            ("audit/config-env-last-equals-insteadof-separated-funnel",
             "audit/config-env-last-equals-insteadof-separated-audit",
             ["--config-env", "url./x=1.insteadOf=VAR", "status"])):
        probes.append(list(vector))
        check(funnel_id, _forbidden(vector), True)
        check(audit_id, _audit_violation(vector), True)
    # A -c key still ends at the FIRST "=", so a VALUE carrying
    # "=" never shifts the variable name out of the key; probed against each predicate
    # independently, red under an always-last-"=" split.
    for funnel_id, audit_id, vector in (
            ("audit/config-key-c-first-equals-pushurl-funnel",
             "audit/config-key-c-first-equals-pushurl-audit",
             ["-c", "remote.a.pushurl=https://h/p?x=1", "status"]),
            ("audit/config-key-c-first-equals-insteadof-funnel",
             "audit/config-key-c-first-equals-insteadof-audit",
             ["-c", "url./tmp/elsewhere.insteadOf=/tmp/o?a=1", "status"])):
        probes.append(list(vector))
        check(funnel_id, _forbidden(vector), True)
        check(audit_id, _audit_violation(vector), True)
    check("audit/predicates-agree",
          [a for a in probes if _forbidden(a) != _audit_violation(a)], [])
    # The maintenance-pin scan cannot resolve _run_child's head and relies on
    # the _SCAN_ALLOWED_UNPINNED justification in tools/selftest_git_fixture_env.py; this check
    # goes red if the pin-bearing _git funnel stops handing _run_child the pinned git's
    # absolute launch path and the three maintenance pins in option position. No child is
    # launched: _run_child is replaced by a recorder. The probes here run in the case's own
    # temp directory, never the caller's working directory: a working directory that holds
    # the pinned git (an ancestor of the temp tree the fixture git wrapper lives in) refuses
    # it by name (see _git_program).
    recorded = []

    def _recording_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        recorded.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_child = globals()["_run_child"]
    globals()["_run_child"] = _recording_child
    try:
        _git(tmp, "version")
    finally:
        globals()["_run_child"] = saved_child
    check("audit/pinned-git-funnel-prefix", recorded[0][:7] if recorded else [],
          [(_GIT_PROGRAM or ["(no pinned git)"])[0], "-c", "gc.auto=0", "-c",
           "gc.autoDetach=false", "-c", "maintenance.auto=false"])

    # Every child EITHER funnel launches runs under
    # LC_ALL=C with LANGUAGE unset (see _clean_env), so a git whose message catalogs would
    # translate the "fatal:" and "error:" prefixes the merge diagnostic scan matches always
    # emits them in English, and the configured regenerate and check commands run under the same
    # C locale. Probed under an INJECTED non-C locale, so the pin itself, never an empty ambient
    # environment, is what passes; red when either half of the pin is removed. BOTH funnels are
    # probed under this one id: the git funnel through _git, the external funnel through
    # _run_external.
    recorded_env = []

    def _env_recording_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        recorded_env.append(dict(env or dict()))
        return subprocess.CompletedProcess(list(argv), 0, b"", b"")

    saved_locale = _put_env(_NON_C_LOCALE)
    globals()["_run_child"] = _env_recording_child
    try:
        _git(tmp, "version")
        # The probe head must RESOLVE (an absolute executable outside the cwd): _run_external
        # refuses an unresolvable head before reaching the recording child.
        _run_external([sys.executable, "--version"], tmp, 1)
    finally:
        globals()["_run_child"] = saved_child
        _restore_env(saved_locale)
    check("audit/git-env-c-locale",
          [(child_env.get("LC_ALL"), "LANGUAGE" in child_env) for child_env in recorded_env],
          [("C", False), ("C", False)])
    # The locale pin lands AFTER the explicit additions, so no
    # caller of either funnel can override it through extra entries.
    pinned = _clean_env(dict(LC_ALL="de_DE.UTF-8", LANGUAGE="de"))
    check("audit/clean-env-locale-pin-after-extra",
          (pinned.get("LC_ALL"), "LANGUAGE" in pinned), ("C", False))


def case_conflict(tmp):
    fx = _fixture(tmp, "conflict")
    wt = fx.add_pr(1, "feat/x", dict([("src/a.txt", "pr side\n")]))
    fx.advance_main(dict([("src/a.txt", "main side\n")]))
    before = _snapshot(wt)
    old = fx.remote_ref("feat/x")
    # The dry run runs the same candidate preparation and
    # validation as --apply and must AGREE with it on this conflict: both refuse by name, and
    # the dry run changes nothing anywhere.
    rc_dry, reports_dry, _fatal = fx.run()
    check("refuse/source-conflict-dry-agrees",
          (rc_dry, _result(reports_dry, 1), _snapshot_diff(before, _snapshot(wt)),
           fx.remote_ref("feat/x"), fx.pushes(), _has_marker(wt)),
          (1, "source-conflict", [], old, [], False))
    rc, reports, _fatal = fx.run(apply=True)
    check("refuse/source-conflict",
          (rc, _result(reports, 1), "src/a.txt" in (reports[0]["reason"] if reports else "")),
          (1, "source-conflict", True))
    check("refuse/source-conflict-restored",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes(),
           os.path.exists(os.path.join(_git_dir(wt), MARKER_NAME))),
          ([], old, [], False))
    check("refuse/source-conflict-invariants", _invariants(fx), [])


# The LC_ALL/LANGUAGE environment OBSERVED IN the merge child itself: _mergefault_run puts a
# recording git wrapper at the front of the fixture PATH, and for a merge argv the wrapper
# writes the values it (the exec-ed merge child) sees (a capture of the env ARGUMENT at the
# _run_child boundary would miss a leak INSIDE _run_child, below that capture point, and keep
# the translated vectors green). The
# translated-stderr stand-in derives its prefix from this observed value, never from a fresh
# _clean_env() of its own, so a merge-only locale leak anywhere up to the child exec turns the
# translated vectors red instead of green.
_MERGE_CHILD_ENV = []


def _merge_env_wrapper(fx, tmp):
    """Install a recording git wrapper at the front of the fixture PATH (fx.bin precedes
    everything during fx.run): for a MERGE argv it appends the LC_ALL and LANGUAGE values it
    observes, in the exec-ed child itself, to the fixture record file, then hands the whole
    argv to the first git OUTSIDE the self-test temp tree (the lifecycle wrapper, which
    reasserts the maintenance pins; an earlier fixture recorder is never chained). Returns a
    reader that appends each newly recorded observation to _MERGE_CHILD_ENV as a dict carrying
    LC_ALL and LANGUAGE only when the child saw them set (the observation point is the child,
    so a leak anywhere between _clean_env and the exec, the
    inside of _run_child included, is recorded)."""
    record = fx.base / "merge-child-env.log"
    real_tmp = os.path.realpath(str(tmp)) + os.sep
    next_git = None
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not entry or os.path.realpath(entry).startswith(real_tmp):
            continue
        candidate = os.path.join(entry, "git")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            next_git = os.path.abspath(candidate)
            break
    if next_git is None:
        raise RuntimeError("self-test requires git on PATH outside the fixture tree")
    wrapper = fx.bin / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        "  if [ \"$a\" = merge ]; then\n"
        "    printf '%s\\n' \"${LC_ALL+LC_ALL=$LC_ALL }${LANGUAGE+LANGUAGE=$LANGUAGE}\""
        " >> '" + str(record) + "'\n"
        "    break\n"
        "  fi\n"
        "done\n"
        "exec '" + next_git + "' \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    seen = dict(count=0)

    def observed():
        try:
            lines = record.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines[seen["count"]:]:
            _MERGE_CHILD_ENV.append(dict(part.split("=", 1) for part in line.split()))
        seen["count"] = len(lines)
    return observed


def _mergefault_run(tmp, name, fault, apply, clean=False, env=None, leak=None,
                    leak_inside=False):
    """One tool run over a FRESH fixture with the given merge fault injected at the funnel
    return boundary (the real merge runs first, so MERGE_HEAD and the merge state exist exactly
    as git leaves them; only the REPORTED code and stderr are replaced), the observations
    captured immediately after the run, before any later run can disturb them. env, when
    given, is set around the run through _put_env (the translated vectors
    inject a non-C locale). leak, when given, is merged into the environment of the MERGE child
    alone at the _run_child boundary, AFTER _clean_env pinned it: the deliberate merge-only
    locale route-around, which the translated stand-in must turn
    red. With leak_inside True the same leak lands INSIDE production _run_child instead, at
    its subprocess.Popen call, below any parent-side capture of the env argument. The wrapped
    funnel records the REAL exit code of each merge it
    faults and every conflict-resolution operation (a checkout --theirs, a resolution add); the LC_ALL/LANGUAGE
    values the merge child OBSERVES are recorded in the child itself by the _merge_env_wrapper
    recording wrapper (_MERGE_CHILD_ENV; a record of the env argument at the _run_child
    boundary would miss a leak below that point). Returns ((exit
    code, result, snapshot
    diff, remote head unchanged, pushes, marker, real merge exit codes reached, resolution
    operations), refusal reason, fixture)."""
    fx = _fixture(tmp, name)
    if clean:
        # A CLEAN merge (MERGE_HEAD, no unmerged index entry): the base advances a path the
        # generated digest does not read.
        wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
        fx.advance_main(dict([("docs/note.txt", "note\n")]))
    else:
        wt = _stale_pr(fx)
    old = fx.remote_ref("feat/x")
    before = _snapshot(wt)
    global _git, _run_child
    real_git = _git
    real_child = _run_child
    reached, resolutions, in_merge = [], [], []
    del _MERGE_CHILD_ENV[:]
    del _NATIVE_FAULT[:]
    del _FAULT_STDERR[:]
    observed = _merge_env_wrapper(fx, tmp)

    def leaking_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        # leak lands at the _run_child boundary, AFTER _clean_env pinned env; the OBSERVATION
        # happens later still, in the exec-ed child itself (the recording wrapper), so even a
        # leak below this point would be observed.
        if in_merge and leak and not leak_inside:
            env = dict(env or dict())
            env.update(leak)
        return real_child(argv, cwd=cwd, env=env, timeout=timeout, push=push,
                          executable=executable)

    real_popen = subprocess.Popen

    def leaking_popen(argv, *p_args, **p_kwargs):
        # The merge-only leak landed INSIDE production _run_child, at its Popen call BELOW any
        # parent-side capture of the env argument: only the
        # child-side observation sees it, so a capture moved back to the _run_child
        # boundary turns the inside-child vector red.
        if in_merge and p_kwargs.get("env") is not None:
            p_env = dict(p_kwargs["env"])
            p_env.update(leak)
            p_kwargs["env"] = p_env
        return real_popen(argv, *p_args, **p_kwargs)

    def fault_git(cwd, *args, **kwargs):
        if args[:2] in (("checkout", "--theirs"), ("add", "--")):
            resolutions.append(list(args[:2]))
        if args[:1] != ("merge",):
            return real_git(cwd, *args, **kwargs)
        in_merge.append(True)
        try:
            rc, out, err = real_git(cwd, *args, **kwargs)
        finally:
            in_merge.pop()
        observed()
        reached.append(rc)
        faulted = fault(rc, out, err)
        # The stderr handed back INTO the funnel, exactly what the merge diagnostic scan
        # reads: the native validation asserts its diagnostic REACHED it.
        _FAULT_STDERR.append(faulted[2])
        return faulted

    saved_env = _put_env(env or dict())
    _git = fault_git
    _run_child = leaking_child
    if leak and leak_inside:
        subprocess.Popen = leaking_popen
    try:
        rc, reports, _fatal = fx.run(apply=apply)
    finally:
        _git = real_git
        _run_child = real_child
        subprocess.Popen = real_popen
        _restore_env(saved_env)
    reason = reports[0]["reason"] if reports else ""
    return ((rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)),
             fx.remote_ref("feat/x") == old, fx.pushes(), _has_marker(wt),
             reached, resolutions), reason, fx)


def _fault_signal(_rc, out, err):
    return -11, out, err


def _fault_fatal(_rc, out, err):
    return 128, out, err


def _fault_exit1(_rc, out, err):
    return 1, out, err


def _fault_fatal_stderr(rc, out, err):
    # The REAL conflicted exit (1, unmerged entries present) with a fatal diagnostic appended:
    # a merge that wrote its index and then failed.
    return rc, out, err + "fatal: injected merge failure after index write\n"


def _fault_error_stderr(rc, out, err):
    # The boundary twin of _fault_fatal_stderr: the "error:" half of the prefix scan gets its
    # own dry and apply vectors on fresh fixtures.
    return rc, out, err + "error: injected merge failure after index write\n"


def _fault_exit0_error_stderr(_rc, out, err):
    # An error diagnostic beside a REPORTED SUCCESS exit: the diagnostic scan must read stderr
    # before accepting EITHER supported exit status, 0 included (a scan run only on exit 1
    # would let this shape go on to commit and push; statuses outside 0 and 1, and an absent
    # MERGE_HEAD, refuse before the scan).
    return 0, out, err + "error: injected merge failure with reported exit zero\n"


def _fault_exit0(_rc, out, err):
    # A reported success exit with NO diagnostic over a real conflicted merge: the unmerged
    # index entries alone must refuse a success exit.
    return 0, out, err


def _locale_probe_env(locale):
    """A full child environment carrying EXACTLY the given LC_ALL/LANGUAGE entries and NO other
    message-locale selector: _clean_env pins LC_ALL=C and drops LANGUAGE, so LC_ALL, LANGUAGE,
    LC_MESSAGES and LANG are ALL removed after the pin and the given entries applied over the
    hole, replaying the captured variables and their absence explicitly (a probe keeping the
    ambient LANG would, over a capture with no LC_ALL, read the HOST locale, never the observed
    one). Self-test harness only: this deliberately undoes the production pin to observe
    native translation."""
    env = _clean_env()
    for key in ("LC_ALL", "LANGUAGE", "LC_MESSAGES", "LANG"):
        env.pop(key, None)
    env.update(locale)
    return env


# The ONE diagnostic the probe forces: git run with an EXPLICIT
# --git-dir naming a RELATIVE path that is absent from the probe's directory. An explicit git
# directory turns repository discovery off, so git never walks the probe directory's
# ancestors and dies at setup with the explicit-directory message alone. A probe relying on
# discovery from an empty directory would get a message that depends on WHERE that directory
# sits: below a filesystem boundary git names the mount point and adds a second "Stopping at
# filesystem boundary" line, and inside a repository it reports a missing revision instead,
# so a probe directory on another filesystem (TMPDIR on a separate mount) would never match
# the control and every candidate would read as not translating. The operand is relative, so the
# message carries only that name, never the probe directory's own path, and the exit status
# and text are the same in every directory where the name is absent. The option is spelled
# as one literal so the maintenance-pin scan resolves the subcommand; the control below is
# rendered from _PROBE_GIT_DIR, so the two spellings disagreeing fails every control check.
_PROBE_GIT_DIR = "merge-train-locale-probe-absent"
_PROBE_ARGV = ("git", "--git-dir=merge-train-locale-probe-absent", "rev-parse", "--verify",
               "HEAD")


def _native_probe(locale, where=None):
    """(exit status, stderr text) of ONE forced git fatal diagnostic (_PROBE_ARGV: rev-parse
    under an explicit, absent, relative git directory, read-only: git dies at setup before it
    reads or writes anything) under EXACTLY the given LC_ALL/LANGUAGE entries: the shape both
    the availability rule and the native-fault validation read, exit status included. With
    where None the probe runs in a fresh empty temporary directory;
    the self-test passes where to run it in an EXISTING directory instead, read-only, to show
    the control does not depend on the directory's location."""
    if where is not None:
        proc = subprocess.run(list(_PROBE_ARGV), cwd=where, env=_locale_probe_env(locale),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return proc.returncode, proc.stderr.decode("utf-8", "replace")
    with tempfile.TemporaryDirectory(prefix="merge-train-locale-probe-") as probe_dir:
        proc = subprocess.run(list(_PROBE_ARGV), cwd=probe_dir, env=_locale_probe_env(locale),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    return proc.returncode, proc.stderr.decode("utf-8", "replace")


# Where git's own message catalogs live on the hosts this suite supports: the expected
# diagnostic is rendered from the catalog git itself translates through, so the expectation
# is INDEPENDENT of the probe output it validates.
_GIT_LOCALEDIRS = ("/usr/share/locale", "/usr/local/share/locale")
# The exact msgid of the ONE diagnostic the probe forces (git's message for an explicit git
# directory that is not a repository) and its English rendering over the probe's relative
# operand: the C-locale control must produce exactly this, or nothing is validated (fail
# closed; a git whose message changed reads as not translating, never as translated).
_PROBE_MSGID = "not a git repository: '%s'"
_PROBE_CONTROL = "fatal: " + _PROBE_MSGID % _PROBE_GIT_DIR + "\n"


def _git_catalog(locale):
    """The gettext catalog git reads for the given LC_ALL/LANGUAGE entries, or None: the
    language is the first LANGUAGE entry when one is set (the highest-priority selector once
    translation is enabled), else LC_ALL, and the C and POSIX locales name no catalog."""
    lang = (locale.get("LANGUAGE") or locale.get("LC_ALL") or "").split(":")[0]
    if not lang or lang.split(".")[0] in ("C", "POSIX"):
        return None
    for localedir in _GIT_LOCALEDIRS:
        try:
            return gettext.translation("git", localedir=localedir, languages=[lang])
        except OSError:
            continue
    return None


def _expected_probe_diag(locale):
    """The INDEPENDENT expected probe diagnostic under locale: the translated form of the ONE
    specific fatal message the probe forces, rendered from git's own message catalog, or None
    when no catalog serves the locale or the catalog leaves the rendering identical to the
    English control (then nothing distinguishes translation from English, fail closed).
    A rule accepting ANY same-exit non-empty stderr without a leading English prefix would
    count an unrelated warning, and a warning line ahead of an English fatal line, as the
    translated diagnostic."""
    catalog = _git_catalog(locale)
    if catalog is None:
        return None
    expected = (catalog.gettext("fatal: ") + catalog.gettext(_PROBE_MSGID) % _PROBE_GIT_DIR
                + "\n")
    if expected == _PROBE_CONTROL:
        return None
    return expected


def _native_diag_valid(locale, control_rc, control_err, probe_rc, diag):
    """Whether diag is VALIDATED as the translated form of the probe's specific fatal
    diagnostic: the C-locale control failed with exactly the
    expected English message, the probe failed with the SAME exit status, and its stderr is
    byte-identical to the expected diagnostic rendered from git's own message catalog, an
    expectation independent of the probe. A same-exit unrelated warning, a warning ahead of
    an English fatal line, an identity (English) diagnostic and empty output all fail this
    equality."""
    expected = _expected_probe_diag(locale)
    return (control_rc != 0 and control_err == _PROBE_CONTROL and expected is not None
            and probe_rc == control_rc and diag == expected)


def _translates(locale, where=None):
    """Whether real git translates the probe's fatal diagnostic under the given locale on THIS
    host: _native_diag_valid over a C-locale CONTROL of the same probe and the catalog-rendered
    expected text (reading any same-exit non-empty stderr without the English prefixes as
    translation would let an unrelated diagnostic count); the C locale itself therefore never
    counts as translated. Availability is checked before the native leak vector runs, and a
    host with no translating locale reports that vector NOT RUN (cannot-evaluate), never a
    pass. where is passed to both probes (see _native_probe)."""
    control_rc, control_err = _native_probe(dict(LC_ALL="C"), where=where)
    if control_rc == 0 or control_err != _PROBE_CONTROL:
        return False
    rc, err = _native_probe(locale, where=where)
    return _native_diag_valid(locale, control_rc, control_err, rc, err)


def _other_filesystem_ancestor(path):
    """The nearest ancestor of path on a DIFFERENT filesystem (device) than path itself, or None
    when every ancestor shares its filesystem or one cannot be stat-ed: the location the
    probe-location vectors use to put the probe across a filesystem boundary from the
    self-test scratch without writing outside it."""
    try:
        current = os.path.realpath(path)
        device = os.stat(current).st_dev
        while os.path.dirname(current) != current:
            current = os.path.dirname(current)
            if os.stat(current).st_dev != device:
                return current
    except OSError:
        return None
    return None


def _probe_locations(tmp, repo):
    """(label, directory) of every location the probe-location vectors run the probe in: a
    fresh directory in the self-test scratch, a fresh directory
    INSIDE the worktree of the given fixture repository (discovery from there finds that
    repository), the checkout this tool runs from, and, when the self-test scratch has an
    ancestor on another filesystem (a scratch or TMPDIR on its own mount), that ancestor. The
    last two are EXISTING directories, probed read-only; nothing is written outside the
    self-test scratch."""
    locations = [("scratch", tempfile.mkdtemp(prefix="locale-probe-", dir=tmp)),
                 ("inside-repository", tempfile.mkdtemp(prefix="locale-probe-", dir=repo)),
                 ("checkout", str(ROOT))]
    other = _other_filesystem_ancestor(tmp)
    if other is not None:
        locations.append(("other-filesystem", other))
    return locations


def _probe_controls(locations):
    """(label, probe operand present, exit status, stderr) of the C-locale control probe at
    each location: the operand must be ABSENT there for the probe to force its one diagnostic."""
    return [(label, os.path.lexists(os.path.join(where, _PROBE_GIT_DIR)))
            + _native_probe(dict(LC_ALL="C"), where=where) for label, where in locations]


# One record per native-fault injection, cleared per _mergefault_run: observed says a
# SUFFICIENT merge-child locale
# observation existed (none at all, and a record that captured no non-empty LC_ALL, the [{}]
# shape included, are both cannot-evaluate: nothing is probed, nothing is appended, and NEVER
# a fallback to the host LANG, whose value in the child the observation did not establish),
# diag is the probe stderr appended to the merge stderr, and valid says that diagnostic was
# VALIDATED through _native_diag_valid as the translated form of the probe's one specific
# fatal diagnostic, byte-identical to git's own catalog rendering, never merely same-exit
# non-empty output without a leading English prefix.
_NATIVE_FAULT = []
# The stderr each injected fault handed back into the funnel, exactly what the merge
# diagnostic scan reads, cleared per _mergefault_run: the native validation asserts its
# diagnostic REACHED it.
_FAULT_STDERR = []


def _fault_probe(observed):
    """The diagnostic subprocess of the native fault, an entry point kept apart from the
    availability rule on purpose: a degradation of THIS probe
    (empty output, an unrelated exit-0 warning) must turn the validation check red while the
    availability rule still reports a translating locale."""
    return _native_probe(observed)


def _locale_record_sufficient(record):
    """Whether ONE observed merge-child locale record pins the child's effective message
    locale: only a record carrying a non-empty LC_ALL does (gettext reads LC_ALL first; with
    it unset or empty the child's LANG, which the observation does not capture, decided the
    locale, so the record proves nothing and the fault is cannot-evaluate; probing the [{}]
    shape would run under whatever LANG the host happened to have)."""
    return bool(record.get("LC_ALL"))


def _fault_native_stderr(rc, out, err):
    # REAL git stderr under the locale OBSERVED in the merge child, appended to the real merge
    # output: with the production pin intact the observed locale is C and the appended
    # diagnostic arrives as English "fatal: ...", which the scan refuses; under the deliberate
    # merge-only leak of a host-translating locale it arrives translated, which the
    # English-only scan misses, with no stand-in text involved. The probe outcome is
    # VALIDATED against the catalog-rendered expected diagnostic (_native_diag_valid) and
    # recorded in _NATIVE_FAULT for meta/native-fault-diagnostic-validated; with NO observed
    # merge-child locale, and with an observation that is INSUFFICIENT (no non-empty LC_ALL
    # captured, the [{}] shape included), nothing is probed and nothing is appended,
    # cannot-evaluate, never a fallback to the host LANG.
    record = _MERGE_CHILD_ENV[-1] if _MERGE_CHILD_ENV else dict()
    if not _MERGE_CHILD_ENV or not _locale_record_sufficient(record):
        _NATIVE_FAULT.append(dict(observed=False, valid=False, diag=""))
        return rc, out, err
    probe_rc, diag = _fault_probe(record)
    control_rc, control_err = _native_probe(dict(LC_ALL="C"))
    _NATIVE_FAULT.append(dict(
        observed=True, diag=diag,
        valid=_native_diag_valid(record, control_rc, control_err, probe_rc, diag)))
    return rc, out, err + diag


# The non-C locale the translated-diagnostic vectors inject around the run.
_NON_C_LOCALE = dict(LC_ALL="de_DE.UTF-8", LANGUAGE="de")


def _fault_translated_stderr(rc, out, err):
    # A LOCALIZED git stand-in over the locale OBSERVED IN the merge child by the recording
    # wrapper (_MERGE_CHILD_ENV; a stand-in reconstructing a fresh _clean_env() of its own, or
    # a capture reading the env ARGUMENT at the _run_child boundary, would let a leak inside
    # _run_child, below the capture point, keep these vectors green). The injected diagnostic
    # keeps the English
    # "fatal:" prefix ONLY when the observed environment pins LC_ALL=C with LANGUAGE unset;
    # under any other observed environment, and when NOTHING was observed (fail closed), it
    # arrives translated, exactly as the shipped de catalog translates it ("Schwerwiegend: "),
    # which the English-only prefix scan misses. Requiring BOTH halves of the pin is the
    # DELIBERATELY conservative rule of this stand-in, stricter than the observed gettext
    # behaviour (LC_ALL=C alone suffices on the git observed here; see _clean_env), so removing
    # either half of the pin turns the vectors red.
    captured = _MERGE_CHILD_ENV[-1] if _MERGE_CHILD_ENV else dict()
    if _MERGE_CHILD_ENV and captured.get("LC_ALL") == "C" and "LANGUAGE" not in captured:
        return rc, out, err + "fatal: injected localized merge failure after index write\n"
    return (rc, out,
            err + "Schwerwiegend: injizierter lokalisierter Fehler nach dem Indexschreiben\n")


def _put_env(env):
    """Set the given os.environ entries, returning what _restore_env needs to undo them (a
    None value marks a variable that was unset)."""
    saved = dict((key, os.environ.get(key)) for key in env)
    os.environ.update(env)
    return saved


def _restore_env(saved):
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def case_mergefault(tmp):
    # A merge whose child reports an abnormal exit AFTER
    # creating MERGE_HEAD (a signal death, a fatal error), an exit 1 that presents no unmerged
    # index entry, an exit 1 whose real conflict sits BESIDE a fatal, error or (under a non-C
    # locale) translated stderr diagnostic, an exit 0 reported beside an error diagnostic (clean
    # and conflicted fixtures), and an exit 0 reported over unmerged index entries must each
    # refuse with nothing pushed, in both modes, BEFORE any conflict resolution runs (no
    # checkout --theirs, no resolution add), with the injection reached exactly once at the real
    # merge exit code and the refusal reason naming the fault. Each fault and each mode runs on
    # its own fresh fixture through _mergefault_run.
    fixtures = []
    for dry_id, apply_id, clean, fault, merge_rc, reason_prefix, env in (
            ("refuse/merge-signal-exit-dry-agrees", "refuse/merge-signal-exit", False,
             _fault_signal, 1, "git merge exited -11", None),
            ("refuse/merge-fatal-exit-dry-agrees", "refuse/merge-fatal-exit", False,
             _fault_fatal, 1, "git merge exited 128", None),
            ("refuse/merge-exit1-no-conflict-dry-agrees", "refuse/merge-exit1-no-conflict",
             True, _fault_exit1, 0, "git merge exited 1 with no unmerged index entry", None),
            ("refuse/merge-conflict-fatal-stderr-dry-agrees",
             "refuse/merge-conflict-fatal-stderr", False, _fault_fatal_stderr, 1,
             "git merge exited 1 with a fatal or error diagnostic, not a supported merge "
             "outcome: fatal: injected merge failure", None),
            ("refuse/merge-conflict-error-stderr-dry-agrees",
             "refuse/merge-conflict-error-stderr", False, _fault_error_stderr, 1,
             "git merge exited 1 with a fatal or error diagnostic, not a supported merge "
             "outcome: error: injected merge failure", None),
            ("refuse/merge-conflict-translated-stderr-dry-agrees",
             "refuse/merge-conflict-translated-stderr", False, _fault_translated_stderr, 1,
             "git merge exited 1 with a fatal or error diagnostic, not a supported merge "
             "outcome: fatal: injected localized merge failure", _NON_C_LOCALE),
            ("refuse/merge-exit0-error-clean-dry-agrees", "refuse/merge-exit0-error-clean",
             True, _fault_exit0_error_stderr, 0,
             "git merge exited 0 with a fatal or error diagnostic, not a supported merge "
             "outcome: error: injected merge failure with reported exit zero", None),
            ("refuse/merge-exit0-error-conflicted-dry-agrees",
             "refuse/merge-exit0-error-conflicted", False, _fault_exit0_error_stderr, 1,
             "git merge exited 0 with a fatal or error diagnostic, not a supported merge "
             "outcome: error: injected merge failure with reported exit zero", None),
            ("refuse/merge-exit0-unmerged-dry-agrees", "refuse/merge-exit0-unmerged", False,
             _fault_exit0, 1, "git merge exited 0 with unmerged index entries, not a "
             "supported merge outcome", None)):
        refused = (1, "git-failed", [], True, [], False, [merge_rc], [], True)
        got_dry, reason_dry, fx_dry = _mergefault_run(tmp, dry_id.split("/", 1)[1], fault,
                                                      False, clean=clean, env=env)
        fixtures.append(fx_dry)
        check(dry_id, got_dry + (reason_dry.startswith(reason_prefix) or reason_dry,), refused)
        got_apply, reason_apply, fx_apply = _mergefault_run(tmp, apply_id.split("/", 1)[1],
                                                            fault, True, clean=clean, env=env)
        fixtures.append(fx_apply)
        check(apply_id, got_apply + (reason_apply.startswith(reason_prefix) or reason_apply,),
              refused)
    # The stand-in itself fails CLOSED with no OBSERVED merge
    # environment, so an observation regression turns the translated vectors red, never green.
    del _MERGE_CHILD_ENV[:]
    check("meta/translated-stand-in-fail-closed-without-capture",
          _fault_translated_stderr(1, b"", "")[2].startswith("Schwerwiegend: "), True)
    # The native fault is CANNOT-EVALUATE (stderr unchanged, an
    # observed=False record, no probe) with NO observation, with an EMPTY observation record
    # (the child saw neither LC_ALL nor LANGUAGE set, so its LANG, which the observation does
    # not capture, decided its locale) and with a LANGUAGE-only record (the same hole). None
    # of these probes anything, so none depends on a translated locale being installed; at
    # the pin the [{}] shape ran a probe under the ambient LANG.
    cannot = (dict(observed=False, valid=False, diag=""),)
    for cannot_id, records in (
            ("meta/native-no-observation-cannot-evaluate", []),
            ("meta/native-empty-observation-cannot-evaluate", [dict()]),
            ("meta/native-language-only-observation-cannot-evaluate",
             [dict(LANGUAGE="de")])):
        del _MERGE_CHILD_ENV[:]
        _MERGE_CHILD_ENV.extend(records)
        del _NATIVE_FAULT[:]
        got_cannot = _fault_native_stderr(1, b"", "merge conflict stderr\n")
        check(cannot_id, (got_cannot, tuple(_NATIVE_FAULT)),
              ((1, b"", "merge conflict stderr\n"), cannot))
    del _MERGE_CHILD_ENV[:]
    del _NATIVE_FAULT[:]
    # The probe environment replays the captured variables AND
    # their absence explicitly; an ambient LANG or LC_MESSAGES never reaches a probe.
    saved_lang = _put_env(dict(LANG="de_DE.UTF-8", LC_MESSAGES="de_DE.UTF-8"))
    try:
        env_empty = _locale_probe_env(dict())
        env_set = _locale_probe_env(dict(LC_ALL="fr_FR.UTF-8", LANGUAGE="fr"))
    finally:
        _restore_env(saved_lang)
    check("meta/locale-probe-env-replays-absence-explicitly",
          (sorted(k for k in env_empty if k in ("LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE")),
           env_set.get("LC_ALL"), env_set.get("LANGUAGE"),
           "LANG" in env_set, "LC_MESSAGES" in env_set),
          ([], "fr_FR.UTF-8", "fr", False, False))
    # The availability rule validates against the INDEPENDENT
    # expected diagnostic (git's own catalog rendering of the probe's one fatal message), so a
    # same-exit unrelated warning, and a warning line ahead of an English fatal line, are both
    # NOT translation. Probed over a substituted _native_probe, so neither check depends on a
    # translated locale being installed; at the pin both shapes read as translated (same exit,
    # non-empty, no leading English prefix).
    real_probe = globals()["_native_probe"]

    def _probe_returning(text):
        def _fake_probe(locale, where=None):
            if locale == dict(LC_ALL="C"):
                return 128, _PROBE_CONTROL
            return 128, text
        return _fake_probe

    try:
        globals()["_native_probe"] = _probe_returning("warning: unrelated diagnostic\n")
        warning_only = _translates(dict(LC_ALL="xx_XX.UTF-8", LANGUAGE="xx"))
        globals()["_native_probe"] = _probe_returning("warning: x\n" + _PROBE_CONTROL)
        warning_then_fatal = _translates(dict(LC_ALL="xx_XX.UTF-8", LANGUAGE="xx"))
    finally:
        globals()["_native_probe"] = real_probe
    check("meta/native-translates-rejects-same-exit-unrelated-warning", warning_only, False)
    check("meta/native-translates-rejects-warning-before-english-fatal",
          warning_then_fatal, False)
    # The REQUIRED-RED mutation, run in band: hand ONLY the merge
    # child a non-C locale, merged at the _run_child boundary AFTER _clean_env pinned the
    # environment (the route-around no funnel-level audit observes; the git-funnel locale probe
    # in case_audit stays green under it). With the stand-in reading the locale OBSERVED in
    # the merge child, the injected diagnostic arrives translated, the English-only scan misses
    # it and the run completes to would-merge THROUGH the conflict resolution: exactly the red
    # the translated vector must show under that mutation. This check pins that sensitivity; a
    # stand-in that reconstructs its own environment turns it red by
    # refusing the run. DELIBERATE SENSITIVITY PROBE: the expected
    # value PINS the UNSAFE outcome, would-merge through the conflict resolution, on purpose,
    # as the red the leak must produce; a production hardening that catches translated
    # diagnostics locale-independently would turn this check red, and the check is then
    # REWRITTEN with that hardening, never read as a regression.
    got_leak, reason_leak, fx_leak = _mergefault_run(
        tmp, "translated-locale-leak", _fault_translated_stderr, False,
        env=_NON_C_LOCALE, leak=dict(_NON_C_LOCALE))
    fixtures.append(fx_leak)
    check("meta/translated-vector-red-under-merge-locale-leak",
          (got_leak, reason_leak.startswith("dry run: would merge")),
          ((0, "would-merge", [], True, [], False, [1],
            [["checkout", "--theirs"], ["add", "--"]]), True))
    # The SAME merge-only leak, injected INSIDE production
    # _run_child at its subprocess.Popen call, BELOW any parent-side capture of the env
    # argument: the child-side observation still records it, the stand-in still arrives
    # translated and the run still completes to the same pinned unsafe would-merge. An
    # observation moved back to a _run_child boundary capture records the pinned C
    # environment instead, the stand-in keeps the English prefix, the scan refuses, and THIS
    # registered check goes red, so the sensitivity is shown in band.
    got_inside, reason_inside, fx_inside = _mergefault_run(
        tmp, "translated-locale-leak-inside-child", _fault_translated_stderr, False,
        env=_NON_C_LOCALE, leak=dict(_NON_C_LOCALE), leak_inside=True)
    fixtures.append(fx_inside)
    check("meta/translated-vector-red-under-inside-child-locale-leak",
          (got_inside, reason_inside.startswith("dry run: would merge")),
          ((0, "would-merge", [], True, [], False, [1],
            [["checkout", "--theirs"], ["add", "--"]]), True))
    # The same merge-only leak shown with REAL git output, under
    # a locale THIS host translates: the availability probe keeps the first candidate whose
    # fatal: prefix arrives translated from real git; with none available the vector is
    # reported NOT RUN (cannot-evaluate), never a pass. Under the leak the
    # _fault_native_stderr stand-in appends stderr real git produced under the locale OBSERVED
    # in the merge child, so the English-only scan missing the translated prefix is native git
    # behaviour, not stand-in text (the expected would-merge is the same pinned unsafe outcome
    # as above).
    native = None
    for candidate in (dict(LC_ALL="de_DE.UTF-8", LANGUAGE="de"),
                      dict(LC_ALL="en_US.UTF-8", LANGUAGE="de"),
                      dict(LC_ALL="fr_FR.UTF-8", LANGUAGE="fr")):
        if _translates(candidate):
            native = candidate
            break
    # The C locale is the control of the availability rule and
    # must NEVER read as translated (its probe diagnostic is the English fatal: prefix); an
    # availability rule that counts any non-empty non-English-prefixed stderr turns this red.
    check("meta/native-c-locale-not-translated", _translates(dict(LC_ALL="C")), False)
    # The C-locale control is byte-identical wherever the probe
    # runs: in the self-test scratch, inside a repository worktree, in the checkout and, where
    # the scratch has an ancestor on another filesystem, there. A probe relying on repository
    # discovery reports a missing revision inside a repository (and, below a filesystem
    # boundary, names the mount point), so restoring such a probe turns this check red
    # through its inside-repository row on any host.
    locations = _probe_locations(tmp, str(fx_leak.main))
    check("meta/native-probe-control-location-independent", _probe_controls(locations),
          [(label, False, 128, _PROBE_CONTROL) for label, _where in locations])
    if native is None:
        why = ("no locale translating the git fatal: prefix is available on this host "
               "(candidates: de_DE.UTF-8+de, en_US.UTF-8+de, fr_FR.UTF-8+fr), so the native "
               "leak vector cannot be evaluated")
        skip("meta/native-translated-vector-red-under-merge-locale-leak", why)
        skip("meta/native-fault-diagnostic-validated", why)
        skip("meta/native-availability-location-independent", why)
    else:
        # The availability rule itself finds the native locale
        # translating at EVERY probe location, the filesystem-boundary one included.
        check("meta/native-availability-location-independent",
              [(label, _translates(native, where=where)) for label, where in locations],
              [(label, True) for label, _where in locations])
        got_native, reason_native, fx_native = _mergefault_run(
            tmp, "native-translated-locale-leak", _fault_native_stderr, False,
            env=dict(native), leak=dict(native))
        fixtures.append(fx_native)
        check("meta/native-translated-vector-red-under-merge-locale-leak",
              (got_native, reason_native.startswith("dry run: would merge")),
              ((0, "would-merge", [], True, [], False, [1],
                [["checkout", "--theirs"], ["add", "--"]]), True))
        # The vector above pins the same unsafe outcome a plain
        # conflict resolution reaches, so by ITSELF it stays green when the fault produces no
        # diagnostic at all; this check requires the fault to have run exactly once over an
        # OBSERVED merge-child locale, its diagnostic VALIDATED as the translated form of a
        # fatal diagnostic (same failure exit as the C-locale control, non-empty, neither
        # English prefix), and that diagnostic to have REACHED the stderr handed back into
        # the funnel, the exact text the merge diagnostic scan read and missed. It goes red
        # when _fault_native_stderr is replaced by an identity function and when its probe
        # degrades to empty output, an unrelated exit-0 warning or an unrelated SAME-EXIT
        # warning: the validation is byte-equality against the catalog-rendered expected
        # diagnostic, never same-exit non-empty output without a leading English prefix.
        record = _NATIVE_FAULT[0] if _NATIVE_FAULT else dict()
        diag = record.get("diag", "")
        check("meta/native-fault-diagnostic-validated",
              (len(_NATIVE_FAULT), record.get("observed"), record.get("valid"),
               bool(diag.strip()), diag.startswith(("fatal:", "error:")),
               len(_FAULT_STDERR) == 1 and _FAULT_STDERR[0].endswith(diag)),
              (1, True, True, True, False, True))
    check("refuse/mergefault-invariants", _invariants(*fixtures), [])


def _refusal_run(tmp, name, env_key=None, failing_check=False, seen=None):
    """A stale-generated PR run twice under one injected generator fault, dry first and then
    with --apply; returns (exit code, result, reason, (snapshot differences, tool pushes),
    (dry exit code, dry result)). The dry run must refuse with the SAME status --apply gives.
    The fixture is appended to seen for the invariant check."""
    fx = _fixture(tmp, name)
    if seen is not None:
        seen.append(fx)
    wt = _stale_pr(fx)
    if failing_check:
        # The failing check stays a config-routed command: the schema now refuses inline
        # interpreter code, so the toy generator has a --check-fail mode instead.
        text = _read_text(fx.main / CONFIG_PATH).replace(
            '"tools/gen.py", "--check"', '"tools/gen.py", "--check-fail"')
        fx.write(fx.main, CONFIG_PATH, text)
        _git(fx.main, "commit", "-q", "-am", "failing check", extra_env=FIXTURE_IDENT)
        _git(fx.main, "push", "-q", "origin", "main")
    before = _snapshot(wt)
    if env_key:
        os.environ[env_key] = "1"
    try:
        rc_dry, reports_dry, _fatal = fx.run()
        rc, reports, _fatal = fx.run(apply=True)
    finally:
        os.environ.pop(env_key or "TOY_UNSET", None)
    reason = reports[0]["reason"] if reports else ""
    return (rc, _result(reports, 1), reason,
            (_snapshot_diff(before, _snapshot(wt)), fx.pushes()),
            (rc_dry, _result(reports_dry, 1)))


def case_regen(tmp):
    # Every generator fault happens in the PRIVATE checkout: the refusal is exit 1 and the author's
    # worktree is untouched by construction; there is no restore-failed state left to reach. The
    # dry run must agree with --apply on every refusal here (these -dry-agrees vectors go red
    # if the dry-run return ever moves before regeneration and the declared checks).
    seen = []
    rc, result, _reason, untouched, dry = _refusal_run(tmp, "regen-fail", "TOY_FAIL", seen=seen)
    check("refuse/regenerate-failed", (rc, result, untouched), (1, "regenerate-failed", ([], [])))
    check("refuse/regenerate-failed-dry-agrees", dry, (1, "regenerate-failed"))
    rc, result, _reason, untouched, dry = _refusal_run(tmp, "check-fail", failing_check=True,
                                                       seen=seen)
    check("refuse/check-failed", (rc, result, untouched), (1, "check-failed", ([], [])))
    check("refuse/check-failed-dry-agrees", dry, (1, "check-failed"))
    rc, result, _reason, untouched, dry = _refusal_run(tmp, "not-fixpoint", "TOY_DRIFT", seen=seen)
    check("refuse/regenerate-not-fixpoint", (rc, result, untouched),
          (1, "regenerate-not-fixpoint", ([], [])))
    check("refuse/regenerate-not-fixpoint-dry-agrees", dry, (1, "regenerate-not-fixpoint"))
    rc, result, _reason, untouched, dry = _refusal_run(tmp, "litter", "TOY_LITTER", seen=seen)
    check("refuse/undeclared-write", (rc, result, untouched), (1, "undeclared-write", ([], [])))
    check("refuse/undeclared-write-dry-agrees", dry, (1, "undeclared-write"))
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

    def push_then_kill(ctx, scratch, _scratch_git, _old, new):
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
    # landed, so the observer's ls-remote cannot read the remote: indeterminate. What holds,
    # stated exactly: the retry marker is kept, the author's working tree, index, HEAD and
    # local branches are unchanged, and the push itself DID land on the remote here (the
    # outcome the marker lets the rerun classify).
    (fx.base / "unreadable-after-push").write_text("", encoding="utf-8")
    before = _snapshot(wt)
    branches = _git_text(fx.main, "for-each-ref", "--format=%(refname) %(objectname)",
                         "refs/heads")
    rc, reports, _fatal = fx.run(apply=True)
    marker = _read_marker(_git_dir(wt)) or dict()
    check("push/unknown-keeps-marker-author-unchanged-push-landed",
          (rc, _result(reports, 1), _snapshot_diff(before, _snapshot(wt)),
           _git_text(fx.main, "for-each-ref", "--format=%(refname) %(objectname)",
                     "refs/heads") == branches,
           marker.get("new") == fx.remote_ref("feat/x"), marker.get("old"), len(fx.pushes())),
          (1, "push-unknown", [], True, True, old, 1))
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
    # The core promise of the private scratch checkout: an author commit, a staged edit, an unstaged
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
    # A plain command timeout (regenerate here, and check commands
    # are the same path) ends the WHOLE process group, not only the direct child: a grandchild of
    # the timed-out regenerate must be gone when the run reports the refusal. Only a timed-out
    # PUSH keeps the direct-child-only behaviour (see _run_child); that path is exercised above.
    fx2 = _fixture(tmp, "timeout-regen")
    _stale_pr(fx2)
    pid_file = Path(tmp) / "timeout-regen-pids"
    text = _read_text(fx2.main / CONFIG_PATH)
    plain = 'regenerate = [["python3", "-I", "-B", "tools/gen.py"]]'
    # The sleeper is a committed SCRIPT, not inline -c code (the config schema refuses an
    # interpreter handed inline code); the grandchild -c spawn
    # inside it is a direct child launch, never a config-routed argv.
    sleeper = ('regenerate = [["python3", "-I", "-B", "tools/gen.py"],'
               ' ["python3", "-I", "-B", "tools/sleep_timeout.py"]]')
    fx2.write(fx2.main, "tools/sleep_timeout.py", TOY_SLEEP_TIMEOUT)
    _git(fx2.main, "add", "--", "tools/sleep_timeout.py")
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
    # The dry run refuses this class with the same status and touches nothing.
    rc_dry, reports_dry, _fatal = fx.run()
    check("refuse/generated-symlink-dry-agrees",
          (rc_dry, _result(reports_dry, 1), outside.read_text(encoding="utf-8"),
           _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)),
          (1, "generated-symlink", "KEEP\n", [], old, True, False))
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
    global _git
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
    # The commit-mismatch class through the FULL run, in both
    # modes: the injected fault first swaps the committed tree (the commit-tree guard refuses),
    # then drops the validated first parent (the commit-parents guard refuses); each run refuses
    # by name with nothing pushed and no marker, and the dry run agrees. The injection rewrites
    # only the commit-tree argv, the way a hook rewrite would land; the real git does the work.
    fx2 = _fixture(tmp, "tamper-commit")
    wt2 = _stale_pr(fx2)
    old2 = fx2.remote_ref("feat/x")
    before2 = _snapshot(wt2)
    real_git = _git

    def swap_tree_git(cwd, *args, **kwargs):
        if args[:1] == ("commit-tree",) and "-p" in args:
            words = list(args)
            words[1] = real_git(cwd, "rev-parse", old2 + "^{tree}")[1].decode().strip()
            return real_git(cwd, *words, **kwargs)
        return real_git(cwd, *args, **kwargs)

    def drop_parent_git(cwd, *args, **kwargs):
        if args[:1] == ("commit-tree",) and "-p" in args:
            words = list(args)
            first = words.index("-p")
            del words[first:first + 2]
            return real_git(cwd, *words, **kwargs)
        return real_git(cwd, *args, **kwargs)

    for dry_id, apply_id, fault in (
            ("tamper/commit-tree-swapped-dry-agrees", "tamper/commit-tree-swapped-refused",
             swap_tree_git),
            ("tamper/commit-parents-dropped-dry-agrees",
             "tamper/commit-parents-dropped-refused", drop_parent_git)):
        _git = fault
        try:
            rc_dry, reports_dry, _fatal = fx2.run()
            rc2, reports2, _fatal = fx2.run(apply=True)
        finally:
            _git = real_git
        check(dry_id,
              (rc_dry, _result(reports_dry, 1), _snapshot_diff(before2, _snapshot(wt2)),
               fx2.remote_ref("feat/x"), fx2.pushes(), _has_marker(wt2)),
              (1, "commit-mismatch", [], old2, [], False))
        check(apply_id,
              (rc2, _result(reports2, 1), _snapshot_diff(before2, _snapshot(wt2)),
               fx2.remote_ref("feat/x"), fx2.pushes(), _has_marker(wt2)),
              (1, "commit-mismatch", [], old2, [], False))
    check("refuse/tamper-invariants", _invariants(fx, fx2), [])


def case_filter(tmp):
    global _git
    # A clean filter in the author's repository: the private checkout
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
    # The pushed-bytes guard through the FULL run, in both modes:
    # the injected fault answers write-tree with the base's tree, so the validated tree is not
    # what the private checkout holds; the run refuses commit-mismatch by name with nothing
    # pushed and no marker, and the dry run agrees.
    fx2 = _fixture(tmp, "filter-tree")
    wt2 = _stale_pr(fx2)
    old2 = fx2.remote_ref("feat/x")
    base_tree = _git_text(fx2.main, "rev-parse", fx2.remote_ref("main") + "^{tree}")
    before2 = _snapshot(wt2)
    real_git = _git

    def wrong_tree_git(cwd, *args, **kwargs):
        if args[:1] == ("write-tree",):
            real_git(cwd, *args, **kwargs)
            return 0, (base_tree + "\n").encode(), ""
        return real_git(cwd, *args, **kwargs)

    _git = wrong_tree_git
    try:
        rc_dry, reports_dry, _fatal = fx2.run()
        rc2, reports2, _fatal = fx2.run(apply=True)
    finally:
        _git = real_git
    check("filter/pushed-bytes-wrong-tree-dry-agrees",
          (rc_dry, _result(reports_dry, 1), _snapshot_diff(before2, _snapshot(wt2)),
           fx2.remote_ref("feat/x"), fx2.pushes(), _has_marker(wt2)),
          (1, "commit-mismatch", [], old2, [], False))
    check("filter/pushed-bytes-wrong-tree-refused",
          (rc2, _result(reports2, 1), _snapshot_diff(before2, _snapshot(wt2)),
           fx2.remote_ref("feat/x"), fx2.pushes(), _has_marker(wt2)),
          (1, "commit-mismatch", [], old2, [], False))
    check("filter/invariants", _invariants(fx, fx2), [])


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
    # Both tool fetches are recorded with the _ALTERNATE_FETCH_CONFIG -c pins right before the
    # subcommand (after the --git-dir and --work-tree a transport command carries), so the
    # filter matches that recorded shape (a bare ["fetch"] head can never
    # match a recorded tool fetch); the lock-released run below pins the filter non-vacuous.
    prefix = list(_ALTERNATE_FETCH_CONFIG)

    def tool_fetches(rows):
        return [a for a in rows if _audited_subcommand(a) == "fetch"
                and a[a.index("fetch") - len(prefix):a.index("fetch")] == prefix]

    with open(os.path.join(common, LOCK_NAME), "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        audit = len(GIT_AUDIT)
        rc, reports, fatal = fx.run(apply=True)
        fetched = tool_fetches(GIT_AUDIT[audit:])
    check("run/lock-held-exit2", (rc, reports, LOCK_NAME in (fatal or "")), (2, [], True))
    check("run/lock-held-touches-nothing",
          (fetched, _snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"),
           fx.pushes() == pushed, _has_marker(wt)), ([], [], old, True, False))
    audit = len(GIT_AUDIT)
    rc2, reports2, _fatal = fx.run()
    check("run/lock-released-fetch-filter-matches",
          (rc2, _result(reports2, 1), len(tool_fetches(GIT_AUDIT[audit:]))),
          (0, "would-merge", 2))
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
    touching = [a for a in GIT_AUDIT[audit:] if _audited_subcommand(a) in (
        "merge", "commit-tree", "push", "ls-remote", "checkout", "add", "clone")]
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
    # No arguments must select the self-test, never a live run: each config-injection lane launches
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
    # With remote.origin.fetch mapping remote heads onto LOCAL branches, a plain base fetch, even
    # a dry run's, would rewind refs/heads/author-saved in the author's repository. The
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
    # The base fetch must not run IN the author's
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
    # A symbolic remote-tracking ref; a fetch updating it in the author's
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
    # The apply snapshot must be sensitive on its own,
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
    # A populated submodule whose own refspec maps the remote main onto a LOCAL
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
    # The author's repository is a PARTIAL clone and the commit named by
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
    # protocol.allow=never ALONE already fails the author-side read
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
    # Observed on git 2.53.0: the author's repository is a partial clone and a blob of the PR
    # head is a missing PROMISED
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
    _unreadable_checkout(tmp)
    _base_missing_blob(tmp)


def _base_missing_blob(tmp):
    # Observed on git 2.53.0: the same partial-clone shape on the
    # BASE side. A promised blob of the base tip is missing from the author's store; the private
    # base fetch succeeds without it (its connectivity check trusts objects reachable from the
    # alternate's refs) and `git merge` exits 2 with "error: unable to read sha1 file of
    # src/a.txt". The refusal must carry git's exit code and stderr, so the cause is named.
    fx = _fixture(tmp, "base-partial")
    wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")]))
    base = fx.advance_main(dict([("src/a.txt", NINE + "l10\n")]))
    old = fx.remote_ref("feat/x")
    blob = _git_text(fx.main, "rev-parse", base + ":src/a.txt")
    for key, value in (("remote.origin.promisor", "true"),
                       ("remote.origin.partialclonefilter", "blob:none")):
        _git(fx.main, "config", key, value)
    objects = _git_text(fx.main, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    os.remove(os.path.join(objects, blob[:2], blob[2:]))
    rc, reports, _fatal = fx.run(apply=True)
    reason = "".join(r["reason"] for r in reports if r["pr"] == 1)
    check("scratch/base-missing-blob-merge-refused-named",
          (rc, _result(reports, 1), "git merge exited" in reason, "unable to read" in reason,
           fx.remote_ref("feat/x"), fx.pushes(), _has_marker(wt)),
          (1, "git-failed", True, True, old, [], False))
    check("scratch/base-missing-blob-invariants", _invariants(fx), [])


def _unreadable_checkout(tmp):
    # Observed on git 2.53.0: with a directory of the scratch checkout
    # unreadable, `git status` prints "dir/file: Permission denied" on stderr, prints NOTHING on
    # stdout and exits 0, so a probe that reads stdout alone accepts the checkout. Both status
    # probes (scratch-complete right after the checkout, fixpoint after regenerate) must refuse
    # by name. A process that still reads the mode-0 directory (root, CAP_DAC_OVERRIDE; observed
    # with _can_read) cannot produce the error: the checks are then recorded as NOT RUN through
    # skip(), out of the executed set and named in the verdict line, never as an evaluated pass.
    got = _unreadable_probe(tmp)
    if got is None:
        for name in ("scratch/unreadable-checkout-refused",
                     "scratch/unreadable-checkout-fixpoint-refused"):
            skip(name, "this process reads a mode-0 directory, so the permission error cannot "
                       "be produced")
        return
    check("scratch/unreadable-checkout-refused", got[0], ("scratch-incomplete", True))
    check("scratch/unreadable-checkout-fixpoint-refused", got[1], ("regenerate-not-fixpoint", True))


def _unreadable_probe(tmp):
    src = Path(tmp) / "unreadable-src"
    _git(tmp, "init", "--quiet", str(src))
    os.makedirs(str(src / "dir"))
    with open(str(src / "dir" / "file"), "w", encoding="utf-8") as handle:
        handle.write("x\n")
    _git(src, "add", "dir/file")
    _git(src, "commit", "-q", "-m", "unreadable fixture", extra_env=FIXTURE_IDENT)
    scratch = str(Path(tmp) / "unreadable-co")
    _git(src, "clone", "--quiet", "--shared", "--no-checkout", str(src), scratch)
    _guard("scratch-setup")(scratch)
    _git(scratch, "checkout", "--quiet", "--detach", "HEAD")
    target = os.path.join(scratch, "dir")
    got = []
    os.chmod(target, 0)
    try:
        if _can_read(target):
            return None
        for name in ("scratch-complete", "fixpoint"):
            try:
                _guard(name)(scratch)
                got.append("accepted")
            except Refuse as exc:
                got.append((exc.status, "Permission denied" in exc.reason))
    finally:
        os.chmod(target, 0o755)
    return got


@contextlib.contextmanager
def _object_unavailable(repo, sha, how):
    """A REAL read failure of one loose object of repo's store for the duration: "missing" moves
    the file aside, "unreadable" sets its mode to 0; either is undone on exit. Yields the
    object's path."""
    objects = _git_text(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    path = os.path.join(objects, sha[:2], sha[2:])
    if how == "missing":
        os.rename(path, path + ".aside")
    else:
        os.chmod(path, 0)
    try:
        yield path
    finally:
        if how == "missing":
            os.rename(path + ".aside", path)
        else:
            os.chmod(path, 0o444)


def _status_of(call):
    try:
        result = call()
    except Refuse as exc:
        return exc.status, exc.reason
    except FailClosed as exc:
        return "fail-closed", str(exc)
    return "accepted", result


def case_gitread(tmp):
    # Every GUARDS function that runs git over the object store, against a REAL read
    # failure of one object of a private-style --shared checkout's store (the shape of a missing
    # promised blob, as in case_partial). fresh-checkout: `git checkout --detach` in the fresh clone
    # EXITS 0 with only "error: unable to read sha1 file" on stderr (observed on git 2.53.0), so
    # the guard must refuse by name on stderr output, before any check runs (the check here always
    # passes, so a guard that ignores stderr accepts). pushed-bytes (ls-tree -r over a missing
    # subtree exits 1; cat-file of a missing symlink blob exits 128), commit-parents (rev-list of a
    # missing commit exits 128) and commit-tree (rev-parse <missing>^{tree} exits 128) call git with
    # ok=(0,), so _git refuses git-failed. config-source (git show of a missing config blob exits
    # 128) reads as no config, which load_config fails closed as "not enabled", never as an empty
    # config. A process that still reads a mode-0 file (observed with _can_read) cannot produce
    # the unreadable variant, which is then recorded as NOT RUN through skip().
    src = Path(tmp) / "gitread-src"
    _git(tmp, "init", "--quiet", str(src))
    for rel, text in (("dir/file", "x\n"), ("top", "t\n"), (CONFIG_PATH, "version = 1\n")):
        os.makedirs(str((src / rel).parent), exist_ok=True)
        with open(str(src / rel), "w", encoding="utf-8") as handle:
            handle.write(text)
    os.symlink("top", str(src / "ln"))
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "gitread fixture", extra_env=FIXTURE_IDENT)
    head = _git_text(src, "rev-parse", "HEAD")
    tree, sub, blob, link, cfg = [_git_text(src, "rev-parse", head + spec) for spec in (
        "^{tree}", ":dir", ":dir/file", ":ln", ":" + CONFIG_PATH)]
    scratch = str(Path(tmp) / "gitread-co")
    _git(src, "clone", "--quiet", "--shared", "--no-checkout", str(src), scratch)
    _guard("scratch-setup")(scratch)
    _git(scratch, "checkout", "--quiet", "--detach", head)
    transport = Path(tmp) / "gitread-transport"
    transport.mkdir()
    ctx = dict(cmd_timeout=60, transport=str(transport),
               cfg=dict(generated=dict(check=[[sys.executable, "-I", "-B", "-c", "pass"]])))

    def fresh():
        return _guard("fresh-checkout")(ctx, scratch, head)

    made = len(SCRATCHES)
    check("gitread/fresh-checkout-baseline-accepted", _status_of(fresh)[0], "accepted")
    with _object_unavailable(src, blob, "missing"):
        got = _status_of(fresh)
    check("gitread/fresh-checkout-missing-blob-refused",
          (got[0], "unable to read" in str(got[1])), ("normal-checkout-failed", True))
    with _object_unavailable(src, blob, "unreadable") as path:
        got = None if _can_read(path) else _status_of(fresh)
    if got is None:
        skip("gitread/fresh-checkout-unreadable-blob-refused",
             "this process reads a mode-0 file, so the permission error cannot be produced")
    else:
        check("gitread/fresh-checkout-unreadable-blob-refused",
              (got[0], "Permission denied" in str(got[1])), ("normal-checkout-failed", True))
    check("gitread/fresh-checkout-removed",
          [p for p in SCRATCHES[made:] if os.path.exists(p)], [])

    def pushed():
        return _guard("pushed-bytes")(scratch, tree)

    with _object_unavailable(src, sub, "missing"):
        missing_sub = _status_of(pushed)[0]
    with _object_unavailable(src, link, "missing"):
        missing_link = _status_of(pushed)[0]
    check("gitread/pushed-bytes-missing-object-refused",
          (_status_of(pushed)[0], missing_sub, missing_link),
          ("accepted", "git-failed", "git-failed"))
    with _object_unavailable(src, head, "missing"):
        got = (_status_of(lambda: _guard("commit-parents")(scratch, head, head))[0],
               _status_of(lambda: _guard("commit-tree")(scratch, head, tree))[0])
    check("gitread/commit-guards-missing-commit-refused", got, ("git-failed", "git-failed"))
    with _object_unavailable(src, cfg, "missing"):
        got = (_status_of(lambda: _guard("config-source")(src, head))[1],
               _status_of(lambda: load_config(src, head))[0])
    check("gitread/config-source-missing-blob-fails-closed",
          (_status_of(lambda: _guard("config-source")(src, head))[1], got),
          ("version = 1\n", (None, "fail-closed")))


def case_ancestry(tmp):
    # Observed on git 2.53.0: `merge-base --is-ancestor` exits 128 when it cannot read a
    # commit, and a PR step reading every nonzero exit as "not an ancestor" would let a dry run
    # report would-merge after a git error. The ancestry guard is
    # tri-state. Legs: real git exits 0, 1 and 128; exit 1 WITH stderr (injected at _run_child,
    # since no real read failure found here produces it); and the end-to-end dry run with the
    # base commit's object moved aside for the duration of the real git call alone.
    global _git, _run_child
    src = Path(tmp) / "ancestry-src"
    _git(tmp, "init", "--quiet", str(src))
    shas = []
    for n in range(2):
        with open(str(src / "f"), "w", encoding="utf-8") as handle:
            handle.write("%d\n" % n)
        _git(src, "add", "f")
        _git(src, "commit", "-q", "-m", "c%d" % n, extra_env=FIXTURE_IDENT)
        shas.append(_git_text(src, "rev-parse", "HEAD"))
    first, second = shas

    def probe():
        return _status_of(lambda: _guard("ancestry")(src, first, second))

    with _object_unavailable(src, first, "missing"):
        missing = probe()
    check("ancestry/tri-state-real-exits",
          (probe(), _status_of(lambda: _guard("ancestry")(src, second, first)), missing[0],
           "(exit 128)" in str(missing[1])),
          (("accepted", True), ("accepted", False), "git-failed", True))
    real_child = _run_child

    def exit_1_with_stderr(argv, **_kw):
        return subprocess.CompletedProcess(list(argv), 1, b"", b"error: injected read failure\n")

    _run_child = exit_1_with_stderr
    try:
        injected = probe()
    finally:
        _run_child = real_child
    check("ancestry/exit-1-with-stderr-refused",
          (injected[0], "injected read failure" in str(injected[1])), ("git-failed", True))
    fx = _fixture(tmp, "ancestry")
    wt = _stale_pr(fx)
    old, base = fx.remote_ref("feat/x"), fx.remote_ref("main")
    real_git = _git

    def unreadable_base(cwd, *args, **kw):
        if args[:2] != ("merge-base", "--is-ancestor"):
            return real_git(cwd, *args, **kw)
        with _object_unavailable(fx.main, base, "missing"):
            return real_git(cwd, *args, **kw)

    _git = unreadable_base
    try:
        rc, reports, _fatal = fx.run(apply=False)
    finally:
        _git = real_git
    reason = "".join(r["reason"] for r in reports if r["pr"] == 1)
    check("ancestry/dry-run-unreadable-base-refused",
          (rc, _result(reports, 1), "(exit 128)" in reason, fx.remote_ref("feat/x"),
           fx.pushes(), _has_marker(wt)),
          (1, "git-failed", True, old, [], False))
    check("ancestry/invariants", _invariants(fx), [])


def case_rootskip(tmp):
    # As root the permission checks must not be counted as executed and passed. With _can_read
    # forced true (what root observes), the five permission checks must
    # be recorded as NOT RUN, kept out of the executed set, with no failure recorded.
    global _can_read
    names = ["config/unreadable-path-entry-head-refused", "git/unreadable-path-entry-refuses-git",
             "gitread/fresh-checkout-unreadable-blob-refused",
             "scratch/unreadable-checkout-fixpoint-refused", "scratch/unreadable-checkout-refused"]
    saved, real = dict(_RECORDER), _can_read
    inner = dict(executed=set(), list=[], failures=[], skipped=[])
    _RECORDER.update(inner)

    def reads_everything(_path):
        return True

    sub = tempfile.mkdtemp(prefix="rootskip-", dir=tmp)
    _can_read = reads_everything
    try:
        _unreadable_checkout(sub)
        case_gitread(sub)
        _unreadable_path_entry(sub)
    finally:
        _can_read = real
        _RECORDER.update(saved)
    check("selftest/unreadable-skips-not-counted-as-run",
          (sorted(set(names) & inner["executed"]), sorted(n for n, _why in inner["skipped"]),
           inner["failures"]),
          ([], names, []))


def case_signal(tmp):
    # A real SIGTERM, SIGHUP, SIGQUIT or SIGINT mid-regenerate, against a REAL tool subprocess
    # with a case-private TMPDIR: the handler converts the signal into the normal cleanup (SIGINT
    # unwinds as KeyboardInterrupt; the regenerate child is killed and reaped, the scratch is
    # removed, the lock is released) and the process exits with the conventional
    # killed-by-signal status (the SIGINT leg pins the documented SIGINT exit, so a main() that
    # swallowed KeyboardInterrupt goes red here). Python installs its
    # KeyboardInterrupt handler only when SIGINT is not ignored at startup, and a self-test
    # started in the background of a non-interactive shell inherits SIG_IGN, so the SIGINT leg
    # resets SIGINT to its default in the tool child. The tool runs as a subprocess, so the fixture
    # invariant recorder does not apply here; the remote is asserted directly.
    for signum, check_id in ((signal.SIGTERM, "signal/sigterm-cleans-up"),
                             (signal.SIGHUP, "signal/sighup-cleans-up"),
                             (signal.SIGQUIT, "signal/sigquit-cleans-up"),
                             (signal.SIGINT, "signal/sigint-cleans-up")):
        name = "sig%d" % signum
        fx = _fixture(tmp, name)
        wt = _stale_pr(fx)
        old = fx.remote_ref("feat/x")
        probe_dir = Path(tmp) / (name + "-tmp")
        probe_dir.mkdir()
        pid_file = probe_dir / "sleeper.pid"
        text = _read_text(fx.main / CONFIG_PATH)
        plain = 'regenerate = [["python3", "-I", "-B", "tools/gen.py"]]'
        # The sleeping regenerate command (a committed SCRIPT: the config schema refuses inline
        # -c code) spawns its OWN child (a grandchild of the tool),
        # so the case also pins the process-GROUP termination: a plain kill of
        # the direct child leaves such a grandchild running in the scratch TMPDIR. The grandchild
        # IGNORES SIGTERM, and the GRANDCHILD itself
        # writes the readiness file, AFTER installing SIG_IGN (if the direct child wrote it, the
        # signal could land before the grandchild was stubborn, so the post-exit group probe
        # would be exercised only on some runs). The grandchild takes the pid
        # file and the direct child's pid as argv (see TOY_SLEEP_SIGNAL).
        sleeper = ('regenerate = [["python3", "-I", "-B", "tools/gen.py"],'
                   ' ["python3", "-I", "-B", "tools/sleep_signal.py"]]')
        fx.write(fx.main, "tools/sleep_signal.py", TOY_SLEEP_SIGNAL)
        _git(fx.main, "add", "--", "tools/sleep_signal.py")
        fx.write(fx.main, CONFIG_PATH, text.replace(plain, sleeper))
        _git(fx.main, "commit", "-q", "-am", "sleeping regenerate", extra_env=FIXTURE_IDENT)
        _git(fx.main, "push", "-q", "origin", "main")
        before = _snapshot(wt)
        env = dict(os.environ)
        env.update(TMPDIR=str(probe_dir), SIG_PID_FILE=str(pid_file),
                   FAKE_GH_STATE=str(fx.state), PATH=str(fx.bin) + os.pathsep + fx.path)
        proc = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                 "--repo", str(fx.main), "--apply"], cwd=str(fx.base), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=(lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
                                if signum == signal.SIGINT else None)
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
    _group_probe_error_reported()


def _sigterm_during_push(tmp):
    # The SIGTERM-first contract exercised against a
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
    # A signal delivered
    # between the fork and the registration of the child handle must not leak the just-created
    # session. The launch runs with UNWIND_SIGNALS blocked; the pending signal is delivered at
    # the restore INSIDE the protected region and ends the child like any other unwind. SIGTERM
    # exercises the _Signalled conversion path and SIGINT the KeyboardInterrupt path (a SIGINT
    # missing from the launch mask would reach the launch-failure handler with no handle under
    # cleanup and leak the new session). Deterministic and in-process: Popen is patched to send
    # this process the real signal right after the sleeping regenerate child is created, the
    # exact window at risk.
    fx = _fixture(tmp, "launchwin")
    _stale_pr(fx)
    text = _read_text(fx.main / CONFIG_PATH)
    plain = 'regenerate = [["python3", "-I", "-B", "tools/gen.py"]]'
    # A committed sleeper script (the config schema refuses inline -c code); its path doubles
    # as the launch marker kicking_popen watches for.
    marker = "tools/sleep_launch.py"
    sleeper = ('regenerate = [["python3", "-I", "-B", "tools/gen.py"],'
               ' ["python3", "-I", "-B", "%s"]]' % marker)
    fx.write(fx.main, "tools/sleep_launch.py", TOY_SLEEP_LAUNCH)
    _git(fx.main, "add", "--", "tools/sleep_launch.py")
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
    # For EVERY signal in UNWIND_SIGNALS: a second signal arriving WHILE a cleanup finally
    # runs its rmtree must not
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
    # The child-side mask restore pinned directly. The launch blocks
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
    # The final bounded probe in _end_child can expire with the group
    # still populated; the honest contract is REPORT, never a silent return. Reproduced with
    # this shape: this process becomes a Linux child subreaper, the direct child dies on the
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
            # SIGKILL before the wait: the grandchild ignores SIGTERM and
            # sleeps 300 s, so when the regression this case guards drops the post-exit SIGKILL,
            # a bare wait would block until the sleep ends and CI would report a timeout instead
            # of this named failure. A zombie (the fixed path) takes the SIGKILL harmlessly.
            try:
                os.kill(gc_pid, signal.SIGKILL)
            except OSError:
                pass
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


def _group_probe_error_reported():
    # Only ESRCH means the group is gone. An EPERM from the
    # probe (a member this user cannot signal) or any other error is "cannot tell", which
    # _end_child must REPORT like a surviving group, never read as gone. Error injection over
    # a real direct child that already exited and was reaped (the control: the real probe
    # says gone), then os.killpg patched to raise EPERM for the probe and _end_child itself.
    import contextlib
    import errno
    import io
    global TERM_GRACE
    proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", "pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    proc.wait()
    gone_control = _group_alive(proc.pid)
    real_killpg = os.killpg

    def _eperm(_pgid, _signum):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    saved_grace = TERM_GRACE
    sink = io.StringIO()
    try:
        TERM_GRACE = 0.2
        os.killpg = _eperm
        alive = _group_alive(proc.pid)
        with contextlib.redirect_stderr(sink):
            _end_child(proc, group=True)
    finally:
        os.killpg = real_killpg
        TERM_GRACE = saved_grace
    reported = sink.getvalue()
    check("signal/group-probe-error-reported",
          (gone_control, alive,
           ("process group %d may still have members" % proc.pid) in reported,
           "Operation not permitted" in reported),
          (False, True, True, True))


def case_crlf(tmp):
    # First reproduction: a committed `text eol=crlf` attribute with the
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
    # The dry run must refuse this candidate with the same status
    # --apply gives; this goes red if the dry-run return moves before the fresh-checkout guard.
    rc_dry, reports_dry, _fatal = fx.run()
    check("fresh/eol-crlf-dry-agrees",
          (rc_dry, _result(reports_dry, 1), fx.pushes(), _has_marker(wt)),
          (1, "normal-checkout-failed", [], False))
    rc, reports, _fatal = fx.run(apply=True)
    check("fresh/eol-crlf-refused", (check_before, rc, _result(reports, 1)),
          (0, 1, "normal-checkout-failed"))
    check("fresh/eol-crlf-untouched",
          (_snapshot_diff(before, _snapshot(wt)), fx.remote_ref("feat/x"), fx.pushes(),
           _has_marker(wt), [p for p in SCRATCHES if os.path.exists(p)]),
          ([], old, [], False, []))
    # Second reproduction: an operator-global core.autocrlf=true. The suite's HOME is the scrubbed
    # scratch home, so the case may own its .gitconfig for the run's duration; the private
    # checkout pins the conversions off, but the fresh NORMAL checkout carries the operator's
    # core.autocrlf (it reads no other operator setting), materializes CRLF, fails the check,
    # and the candidate is refused. Red with that carry removed.
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
    # Third reproduction: an operator-global core.eol=crlf with a committed `text` attribute and
    # no eol attribute. The fresh checkout carries the operator's core.eol, materializes CRLF,
    # fails the check, and the candidate is refused. A compatibility check for the operator
    # settings the fresh checkout honoured before its configuration was isolated; red with the
    # core.eol carry removed.
    fx3 = _fixture(tmp, "operator-eol")
    wt3 = fx3.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt text\n")]))
    old3 = fx3.remote_ref("feat/x")
    fx3.advance_main(dict([("src/c.txt", "charlie\n")]))
    gitconfig.write_text("[core]\n\teol = crlf\n", encoding="utf-8")
    try:
        rc, reports, _fatal = fx3.run()
    finally:
        try:
            os.unlink(str(gitconfig))
        except FileNotFoundError:
            pass
    check("fresh/operator-eol-carried",
          (rc, _result(reports, 1), fx3.remote_ref("feat/x"), _has_marker(wt3), fx3.pushes()),
          (1, "normal-checkout-failed", old3, False, []))
    # Fourth: a valueless core.autocrlf in the global configuration, which git reads as true.
    # The fresh checkout carries it as true (see _FRESH_CHECKOUT_KEYS) and the candidate is
    # refused as for autocrlf = true. Red with the bool-or-str read dropped: git config then
    # prints the empty string, and the fresh checkout reads that as false.
    fx4 = _fixture(tmp, "valueless-autocrlf")
    wt4 = _stale_pr(fx4)
    old4 = fx4.remote_ref("feat/x")
    gitconfig.write_text("[core]\n\tautocrlf\n", encoding="utf-8")
    try:
        rc, reports, _fatal = fx4.run()
    finally:
        try:
            os.unlink(str(gitconfig))
        except FileNotFoundError:
            pass
    check("fresh/valueless-autocrlf-carried-as-true",
          (rc, _result(reports, 1), fx4.remote_ref("feat/x"), _has_marker(wt4), fx4.pushes()),
          (1, "normal-checkout-failed", old4, False, []))
    # The system fallback of the carry, with no global setting: the operator's system
    # configuration (reached through GIT_CONFIG_SYSTEM, see _system_config_git) sets
    # core.autocrlf = true, and the candidate is refused as for the global setting. Red with
    # the --system read removed. Then a global core.autocrlf = false over that system true:
    # the global value alone is carried and the candidate goes ahead. Red when the system
    # value is read and carried as well (the later setting wins).
    fx5 = _fixture(tmp, "system-autocrlf")
    wt5 = _stale_pr(fx5)
    old5 = fx5.remote_ref("feat/x")
    wrapper5 = _system_config_git(fx5, "[core]\n\tautocrlf = true\n")
    try:
        rc, reports, _fatal = fx5.run()
        system_got = (rc, _result(reports, 1), fx5.remote_ref("feat/x"), _has_marker(wt5),
                      fx5.pushes())
        gitconfig.write_text("[core]\n\tautocrlf = false\n", encoding="utf-8")
        try:
            rc, reports, _fatal = fx5.run()
        finally:
            os.unlink(str(gitconfig))
        outranked = (rc, _result(reports, 1))
    finally:
        os.unlink(str(wrapper5))
    check("fresh/system-autocrlf-carried", system_got,
          (1, "normal-checkout-failed", old5, False, []))
    check("fresh/global-autocrlf-outranks-system", outranked, (0, "would-merge"))
    # The system fallback for core.eol: a system core.eol = crlf with a committed `text`
    # attribute and no global setting, refused as for the global setting. Red with the
    # --system read removed.
    fx6 = _fixture(tmp, "system-eol")
    wt6 = fx6.add_pr(1, "feat/x", dict([
        ("src/b.txt", "bravo\n"), (".gitattributes", "src/b.txt text\n")]))
    old6 = fx6.remote_ref("feat/x")
    fx6.advance_main(dict([("src/c.txt", "charlie\n")]))
    wrapper6 = _system_config_git(fx6, "[core]\n\teol = crlf\n")
    try:
        rc, reports, _fatal = fx6.run()
    finally:
        os.unlink(str(wrapper6))
    check("fresh/system-eol-carried",
          (rc, _result(reports, 1), fx6.remote_ref("feat/x"), _has_marker(wt6), fx6.pushes()),
          (1, "normal-checkout-failed", old6, False, []))
    check("fresh/invariants", _invariants(fx, fx2, fx3, fx4, fx5, fx6), [])


def _scripted_pr(fx, attributes, scripts):
    """PR 1 on feat/x committing a .gitattributes and executable scripts at its tree root, with
    the base then advanced so the generated digest conflicts; returns the PR worktree."""
    wt = Path(fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n")])))
    fx.write(wt, ".gitattributes", attributes)
    for rel, text in scripts.items():
        fx.write(wt, rel, text)
        (wt / rel).chmod(0o755)
    head = fx.commit(wt, "pr commits scripts and the attributes naming them")
    _git(wt, "push", "-q", "origin", "%s:refs/heads/feat/x" % head)
    fx.prs[0]["headRefOid"] = head
    fx.set_gh()
    fx.advance_main(dict([("src/c.txt", "charlie\n")]))
    return wt


def _system_config_git(fx, text):
    """Give fx's tool runs an operator system configuration holding text. The file is written
    beside the fixture, and fx's bin directory (the front of every run's PATH, so a run
    resolves its git there) gets a git wrapper naming that file in GIT_CONFIG_SYSTEM for every
    launch that neither sets GIT_CONFIG_NOSYSTEM=1 nor names a system file itself, the way git
    reads its compiled-in system file for such a launch. The suite's own git wrapper pins the
    system configuration off for every launch, so no run could read one otherwise. The wrapper
    execs the git in git's own exec path with the maintenance pins. Returns the wrapper; the
    caller removes it once its runs and controls are done, because a fixture made later takes
    this bin directory into its own PATH."""
    system = fx.base / "system.gitconfig"
    system.write_text(text, encoding="utf-8")
    exec_path = subprocess.run(["git", "--exec-path"], stdout=subprocess.PIPE, env=_clean_env(),
                               timeout=60, check=True).stdout.decode("utf-8", "replace").strip()
    wrapper = fx.bin / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        "if [ \"${GIT_CONFIG_NOSYSTEM:-}\" != 1 ] && [ \"${GIT_CONFIG_SYSTEM+x}\" != x ]; then\n"
        "  export GIT_CONFIG_SYSTEM='%s'\n"
        "fi\n"
        "exec '%s' -c gc.auto=0 -c gc.autoDetach=false -c maintenance.auto=false \"$@\"\n" % (
            system, os.path.join(exec_path, "git")), encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def case_isolation(tmp):
    # No git child working in a checkout that holds PR content (the private checkout and the
    # fresh checkout) reads the operator's global or system configuration (see _PR_DIRS and
    # _PR_GIT_ENV). The suite's HOME is the scrubbed scratch home, so the case owns its
    # .gitconfig for each run's duration, as case_crlf does; a system configuration reaches a
    # run through _system_config_git. Each PR here commits, at its tree root, a script that
    # records that it ran, and an attribute selecting a driver the operator's configuration
    # defines with a command relative to the working directory. Red while those children read
    # that configuration: git then ran the PR's script from the checkout. Each vector has its
    # own fixture and run, so it goes red through the defence it names and no other, and each
    # also runs the same configuration outside the tool, where the script does run, so a
    # green vector cannot come from a dead script or a configuration that never took effect.
    gitconfig = Path(os.environ["HOME"]) / ".gitconfig"
    real_child = globals()["_run_child"]
    pins = ["-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]

    def recorder(name, tail):
        sentinel = Path(tmp) / ("isolation-%s-ran" % name)
        return sentinel, "#!/bin/sh\n: > '%s'\n%s" % (sentinel, tail)

    def configured(text):
        gitconfig.write_text(text, encoding="utf-8")

    def unconfigured():
        try:
            os.unlink(str(gitconfig))
        except FileNotFoundError:
            pass

    # A merge driver from the global configuration: the merge of the conflicting digest runs
    # it. The control merges the base in a plain clone of the PR.
    fx = _fixture(tmp, "isolation-merge-driver")
    merge_ran, merge_script = recorder("merge-driver", "exit 1\n")
    _scripted_pr(fx, "* merge=prdriver\n", dict([("prdriver.sh", merge_script)]))
    configured("[merge \"prdriver\"]\n\tdriver = ./prdriver.sh %O %A %B\n")
    try:
        rc, reports, _fatal = fx.run()
        in_run = merge_ran.exists()
        probe = Path(tmp) / "isolation-merge-driver-control"
        _git(fx.base, "clone", "--quiet", "--no-local", "-b", "feat/x", str(fx.remote),
             str(probe))
        _git(probe, "merge", "--no-commit", "--no-ff", "--no-edit", "origin/main",
             extra_env=FIXTURE_IDENT, ok=None)
        control = merge_ran.exists()
    finally:
        unconfigured()
    check("isolation/pr-merge-driver-never-runs",
          (rc, _result(reports, 1), in_run, control), (0, "would-merge", False, True))
    # A textconv from the global configuration, alone in its run. Nothing the run does asks for
    # a textconv (_changed_lines passes --no-textconv), so the run's own diffs cannot show
    # whether the private checkout reads that configuration; a probe does. At the first git
    # launch of the run in the private checkout once the PR's script is there, it asks git
    # diff for textconv output over the PR's own commit, through _git with that checkout as
    # its working directory. Red with the isolation removed, whatever _changed_lines passes:
    # the probe then runs the PR's script. --no-textconv has its own check below.
    fx2 = _fixture(tmp, "isolation-textconv")
    diff_ran, diff_script = recorder("textconv", "exec cat \"$1\"\n")
    wt2 = _scripted_pr(fx2, "* diff=prdiff\n", dict([("prdiff.sh", diff_script)]))
    head2 = fx2.prs[0]["headRefOid"]
    probes = []

    def _textconv_probe_child(argv, cwd=None, env=None, timeout=None, push=False,
                              executable=None):
        where = os.path.basename(os.path.dirname(str(cwd)))
        if (not probes and where.startswith("merge-train-scratch-") and list(argv[1:7]) == pins
                and os.path.exists(os.path.join(str(cwd), "prdiff.sh"))):
            probes.append(None)
            probes[0] = _git(cwd, "diff", "--no-color", "--textconv", head2 + "~1", head2,
                             ok=None)[0]
        return real_child(argv, cwd=cwd, env=env, timeout=timeout, push=push,
                          executable=executable)

    configured("[diff \"prdiff\"]\n\ttextconv = ./prdiff.sh\n")
    try:
        globals()["_run_child"] = _textconv_probe_child
        try:
            rc, reports, _fatal = fx2.run()
        finally:
            globals()["_run_child"] = real_child
        in_run = diff_ran.exists()
        # _changed_lines itself, over the author's PR worktree (no checkout of the run, so
        # the configuration is read there): --no-textconv alone keeps the textconv out. Red
        # with that option dropped, whatever the isolation does.
        if diff_ran.exists():
            diff_ran.unlink()
        _changed_lines(wt2, "HEAD~1", "HEAD", [])
        unit_textconv = diff_ran.exists()
        # Outside the tool, a plain diff in the PR worktree runs the textconv.
        _git(wt2, "diff", "--no-color", "HEAD~1", "HEAD")
        control = diff_ran.exists()
    finally:
        unconfigured()
    check("isolation/pr-textconv-never-runs",
          (rc, _result(reports, 1), probes, in_run, control),
          (0, "would-merge", [0], False, True))
    check("isolation/changed-lines-passes-no-textconv", (unit_textconv, control),
          (False, True))
    # A filter in the fresh checkout: the private checkout unsets every filter attribute, so
    # only the fresh checkout could run it. The control is a plain clone of the PR, whose
    # checkout runs the smudge filter.
    fx3 = _fixture(tmp, "isolation-filter")
    filter_ran, filter_script = recorder("filter", "exec cat\n")
    _scripted_pr(fx3, "src/b.txt filter=prfilter\n", dict([("prfilter.sh", filter_script)]))
    configured("[filter \"prfilter\"]\n\tsmudge = ./prfilter.sh\n")
    try:
        rc, reports, _fatal = fx3.run()
        in_run = filter_ran.exists()
        _git(fx3.base, "clone", "--quiet", "--no-local", "-b", "feat/x", str(fx3.remote),
             str(Path(tmp) / "isolation-filter-control"))
        control = filter_ran.exists()
    finally:
        unconfigured()
    check("isolation/fresh-checkout-operator-filter-never-runs",
          (rc, _result(reports, 1), in_run, control), (0, "would-merge", False, True))
    # A merge driver from the system configuration, reached through GIT_CONFIG_SYSTEM (see
    # _system_config_git). Red with GIT_CONFIG_SYSTEM and GIT_CONFIG_NOSYSTEM both taken out
    # of _PR_GIT_ENV (either one alone keeps the system file unread). The control merges the
    # base in a plain clone of the PR through the same wrapper, found first on its PATH.
    fx4 = _fixture(tmp, "isolation-system-driver")
    system_ran, system_script = recorder("system-merge-driver", "exit 1\n")
    _scripted_pr(fx4, "* merge=prdriver\n", dict([("prdriver.sh", system_script)]))
    wrapper = _system_config_git(fx4, "[merge \"prdriver\"]\n\tdriver = ./prdriver.sh %O %A %B\n")
    try:
        rc, reports, _fatal = fx4.run()
        in_run = system_ran.exists()
        probe = Path(tmp) / "isolation-system-driver-control"
        control_env = _clean_env(dict(FIXTURE_IDENT, PATH=str(fx4.bin) + os.pathsep + fx4.path))
        subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                        "maintenance.auto=false", "clone", "--quiet", "--no-local", "-b",
                        "feat/x", str(fx4.remote), str(probe)], cwd=str(fx4.base),
                       env=control_env, timeout=60, check=True)
        subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                        "maintenance.auto=false", "merge", "--no-commit", "--no-ff",
                        "--no-edit", "origin/main"], cwd=str(probe), env=control_env,
                       timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        control = system_ran.exists()
    finally:
        os.unlink(str(wrapper))
    check("isolation/system-merge-driver-never-runs",
          (rc, _result(reports, 1), in_run, control), (0, "would-merge", False, True))
    # The push passes --no-recurse-submodules. The push reads the operator's configuration
    # (for its credentials), and an operator push.recurseSubmodules=only makes git push the
    # submodules alone and leave the branch unpushed, exiting 0 (observed on git 2.55.0). Red
    # with that option dropped: the run then reports something other than pushed. The control
    # is a plain push from the PR worktree under the same configuration, which lands nothing.
    fx5 = _fixture(tmp, "isolation-push")
    wt5 = _stale_pr(fx5)
    configured("[push]\n\trecurseSubmodules = only\n")
    try:
        rc, reports, _fatal = fx5.run(apply=True)
        _git(wt5, "push", "-q", "origin", "%s:refs/heads/isolation-push-control" % (
            _git_text(wt5, "rev-parse", "HEAD")))
        control = fx5.remote_ref("isolation-push-control")
    finally:
        unconfigured()
    report = reports[0] if reports else dict()
    check("isolation/push-passes-no-recurse-submodules",
          (rc, report.get("result"), fx5.remote_ref("feat/x") == report.get("new_head"),
           control), (0, "pushed", True, None))
    # The configuration each git child sees, read at every launch in the run's private
    # directories by git config with that launch's own environment (the git its PATH names)
    # and working directory: a global submodule.recurse, and a key set only in a file a global
    # include names. The children in the private base repository and the transport directory
    # read the operator's configuration by design and are the control. Each read keeps git's
    # exit code beside its output: git config --get exits 1 for a key that is not set, so a
    # read that failed for another reason is never taken for "not set".
    fx6 = _fixture(tmp, "isolation-config-view")
    _stale_pr(fx6)
    included = Path(tmp) / "isolation-included.gitconfig"
    included.write_text("[mergetrainprobe]\n\tincluded = yes\n", encoding="utf-8")
    views = []

    def _config_view_child(argv, cwd=None, env=None, timeout=None, push=False, executable=None):
        # Only the run's git launches (their argv carries the maintenance pins), never a
        # configured command launched in the same directories.
        where = os.path.basename(os.path.dirname(str(cwd)))
        for kind in ("scratch", "fresh", "base"):
            if where.startswith("merge-train-%s-" % kind) and list(argv[1:7]) == pins:
                values = []
                for key in ("submodule.recurse", "mergetrainprobe.included"):
                    read = subprocess.run(["git", "config", "--get", key], cwd=str(cwd),
                                          env=env, timeout=60, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL)
                    values.append((read.returncode,
                                   read.stdout.decode("utf-8", "replace").strip()))
                views.append((kind, values[0], values[1]))
        return real_child(argv, cwd=cwd, env=env, timeout=timeout, push=push,
                          executable=executable)

    configured("[submodule]\n\trecurse = true\n[include]\n\tpath = %s\n" % included)
    globals()["_run_child"] = _config_view_child
    try:
        rc, reports, _fatal = fx6.run()
    finally:
        globals()["_run_child"] = real_child
        unconfigured()
    kinds = sorted(set(row[0] for row in views))

    def seen(column, private):
        return sorted(set(row[column] for row in views if (row[0] != "base") == private))

    check("isolation/global-submodule-recurse-not-read",
          (rc, _result(reports, 1), kinds, seen(1, True), seen(1, False)),
          (0, "would-merge", ["base", "fresh", "scratch"], [(1, "")], [(0, "true")]))
    check("isolation/global-include-not-read",
          (rc, _result(reports, 1), kinds, seen(2, True), seen(2, False)),
          (0, "would-merge", ["base", "fresh", "scratch"], [(1, "")], [(0, "yes")]))
    # A general safety check over every run above (the remote base untouched, no forbidden
    # argv, only fast-forward pushes of the PR branch and only under --apply), never a
    # detector for the isolation: it stays green with the isolation removed.
    check("isolation/invariants", _invariants(fx, fx2, fx3, fx4, fx5, fx6), [])


def case_latepush(tmp):
    # The push times out while the remote's pre-receive hook is still running.
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
    # A base-side `git mv utils/a.py utils/b.py`. With rename detection on,
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


def case_faultscan(_tmp):
    # The stderr fault scan alone, per marker: each fault shape below was observed with exit 0.
    scan = _guard("child-fault")
    check("childfault/scan-atexit", scan(
        "Exception ignored in atexit callback <function <lambda>>:\n"
        "Traceback (most recent call last):\nZeroDivisionError: division by zero\n"),
        "Exception ignored in atexit callback <function <lambda>>:")
    check("childfault/scan-traceback", scan("note\nTraceback (most recent call last):\n"),
          "Traceback (most recent call last):")
    check("childfault/scan-ignored-no-traceback", scan(
        "Exception ignored while calling deallocator <function A.__del__>:\n"
        "ZeroDivisionError: division by zero\n"),
        "Exception ignored while calling deallocator <function A.__del__>:")
    check("childfault/scan-thread", scan("Exception in thread Thread-1:\nValueError: x\n"),
          "Exception in thread Thread-1:")
    check("childfault/scan-fatal", scan("Fatal Python error: Aborted\n"),
          "Fatal Python error: Aborted")
    check("childfault/scan-clean", (scan(""), scan("warning: regenerated 3 files\n")),
          (None, None))


# Prepended to the fixture generator: an exception in an atexit callback, which Python prints and
# then exits 0, raised only where WHEN holds.
FAULTY_GEN = "import os, sys, atexit\nif %s: atexit.register(lambda: 1/0)\n"
# A finalizer fault under sys.tracebacklimit=0: "Exception ignored ..." with no Traceback line.
QUIET_FAULT_GEN = ("import sys\nclass _F:\n    def __del__(self): 1/0\n"
                   "if '--check' in sys.argv:\n    sys.tracebacklimit = 0\n    _f = _F(); del _f\n")


def case_childfault(tmp):
    # Every route by which a configured command's result decides a merge, each with a child that
    # exits 0 after a fault on stderr: the train must refuse by that route's name and push nothing.
    seen = []
    for check_id, dry_id, prefix, status in (
            ("childfault/check-atexit", "childfault/check-atexit-dry-agrees",
             FAULTY_GEN % "'--check' in sys.argv", "check-failed"),
            ("childfault/check-ignored-no-traceback",
             "childfault/check-ignored-no-traceback-dry-agrees", QUIET_FAULT_GEN,
             "check-failed"),
            ("childfault/regenerate-atexit", "childfault/regenerate-atexit-dry-agrees",
             FAULTY_GEN % "'--check' not in sys.argv and 'merge-train-scratch-' in os.getcwd()",
             "regenerate-failed"),
            ("childfault/fresh-checkout-atexit",
             "childfault/fresh-checkout-atexit-dry-agrees",
             FAULTY_GEN % "'--check' in sys.argv and 'merge-train-fresh-' in os.getcwd()",
             "normal-checkout-failed")):
        fx = _fixture(tmp, "childfault-" + check_id.split("/", 1)[1])
        seen.append(fx)
        wt = fx.add_pr(1, "feat/x", dict([("src/b.txt", "bravo\n"),
                                          ("tools/gen.py", prefix + TOY_GEN)]))
        fx.advance_main(dict([("src/c.txt", "charlie\n")]))
        before = _snapshot(wt)
        # Each route refuses identically on a dry run first.
        rc_dry, reports_dry, fatal_dry = fx.run()
        check(dry_id,
              (rc_dry, _result(reports_dry, 1), fatal_dry, fx.pushes(),
               _snapshot_diff(before, _snapshot(wt))), (1, status, None, [], []))
        rc, reports, fatal = fx.run(apply=True)
        check(check_id, (rc, _result(reports, 1), fatal, fx.pushes(),
                         _snapshot_diff(before, _snapshot(wt))),
              (1, status, None, [], []))
    check("childfault/invariants", _invariants(*seen), [])


def case_harness(tmp):
    # The mutation-run accounting itself. The pure checks pin
    # _revert_outcome, the precedence _red_on_revert counts with; the two probes below pin the
    # routing those outcomes depend on, through the REAL _run_case and case_badname, each under
    # a scoped recorder restored before this case's own checks run.
    check("meta/revert-crash-only-cannot-evaluate", _revert_outcome([], ["boom"]), "cannot")
    check("meta/revert-assertion-then-crash-cannot-evaluate",
          _revert_outcome(["named: got 1, want 2"], ["boom"]), "cannot")
    check("meta/revert-assertion-only-red", _revert_outcome(["named: got 1, want 2"], []), "red")
    check("meta/revert-neither-stale", _revert_outcome([], []), "stale")

    def probe_raise(_tmp):
        raise RuntimeError("injected case exception")

    saved = dict((k, _RECORDER[k]) for k in ("executed", "list", "failures", "skipped",
                                             "crashes"))
    try:
        _RECORDER.update(executed=set(), list=[], failures=[], skipped=[], crashes=[])
        _run_case("probe-raise", tmp, fn=probe_raise)
        routed = (list(_RECORDER["failures"]),
                  ["injected case exception" in c for c in _RECORDER["crashes"]])
    finally:
        _RECORDER.update(saved)
    # A case exception lands in CRASHES, never in the failures a revert would read as red.
    check("meta/case-exception-recorded-as-crash-not-failure", routed, ([], [True]))
    real_run = Fixture.run

    def unrelated_run(self, apply=False, only=None):
        raise AssertionError("unrelated fixture failure")

    Fixture.run = unrelated_run
    try:
        _RECORDER.update(executed=set(), list=[], failures=[], skipped=[], crashes=[])
        _run_case("badname", tempfile.mkdtemp(prefix="harness-badname-", dir=tmp))
        routed = ["case badname raised AssertionError: unrelated fixture failure" in c
                  for c in _RECORDER["crashes"]]
    finally:
        Fixture.run = real_run
        _RECORDER.update(saved)
    # An AssertionError the argv audit did not raise passes THROUGH case_badname's handler into
    # crash recording: an unrelated fixture exception never masquerades as a refusal result.
    check("meta/unrelated-assertion-reaches-crash-recording", routed, [True])

    def collision_run(self, apply=False, only=None):
        raise AssertionError("unrelated fixture failure: forbidden git operation refused by "
                             "the argv audit was expected but absent")

    Fixture.run = collision_run
    try:
        _RECORDER.update(executed=set(), list=[], failures=[], skipped=[], crashes=[])
        _run_case("badname", tempfile.mkdtemp(prefix="harness-collision-", dir=tmp))
        routed = ["forbidden git operation refused by the argv audit was expected but absent"
                  in c for c in _RECORDER["crashes"]]
    finally:
        Fixture.run = real_run
        _RECORDER.update(saved)
    # The handler matches the audit's dedicated exception TYPE: an ordinary AssertionError
    # carrying the audit's own words still reaches crash recording.
    check("meta/phrase-collision-assertion-reaches-crash-recording", routed, [True])

    def probe_fail_then_raise(_probe_tmp):
        # A named-check failure recorded the way check() records one, with no check() site: the
        # synthetic id must stay OUT of the registered execution set.
        _RECORDER["failures"].append("probe/synthetic-failure: got 1, want 2")
        raise RuntimeError("injected probe exception after a recorded failure")

    GUARDS["probe-synthetic"] = _noop
    probe_stale, probe_cannot = [], []
    try:
        outcome = _revert_one("probe-synthetic", _noop, "probe-synthetic", tmp,
                              probe_stale, probe_cannot, fn=probe_fail_then_raise)
    finally:
        del GUARDS["probe-synthetic"]
        _RECORDER.update(saved)
    # The precedence AT THE SWEEP'S OWN STEP, not only in the pure helper: a synthetic guard
    # whose case records a failure and then raises counts cannot-evaluate through _revert_one,
    # the exact step every REVERTS entry goes through.
    check("meta/revert-call-site-crash-outranks-failure", outcome, "cannot")
    # The stale/cannot BOOKKEEPING lives in _revert_one too: the
    # synthetic crash lands in the cannot list _revert_one itself appended, never in stale and
    # never as silent red credit, so the sweep keeps no outcome handling of its own that a
    # failures-first re-read could weaken unexercised.
    check("meta/revert-bookkeeping-in-revert-one",
          (probe_stale,
           [c.startswith("probe-synthetic (case probe-synthetic): ") for c in probe_cannot]),
          ([], [True]))

    def probe_green(_probe_tmp):
        # A reverted case that records neither a failure nor a crash: the exact STALE outcome.
        return None

    probe_stale2, probe_cannot2 = [], []
    GUARDS["probe-synthetic-stale"] = _noop
    try:
        outcome_stale = _revert_one("probe-synthetic-stale", _noop, "probe-synthetic-stale",
                                    tmp, probe_stale2, probe_cannot2, fn=probe_green)
    finally:
        del GUARDS["probe-synthetic-stale"]
        _RECORDER.update(saved)
    # The OTHER bookkeeping branch: a reverted guard whose case
    # stays green must append its exact stale entry inside _revert_one itself; dropping that
    # append would let --red-on-revert report a stale guard as a pass, and this is the vector
    # that exercises it.
    check("meta/revert-stale-bookkeeping-in-revert-one",
          (outcome_stale, probe_stale2, probe_cannot2),
          ("stale", ["probe-synthetic-stale (case probe-synthetic-stale)"], []))

    GUARDS["probe-sweep"] = _noop
    CASES["probe-sweep"] = probe_green
    saved_reverts = dict(REVERTS)
    REVERTS.clear()
    REVERTS["probe-sweep"] = (_noop, "probe-sweep")
    try:
        swept = _red_on_revert(tmp)
    finally:
        REVERTS.clear()
        REVERTS.update(saved_reverts)
        del CASES["probe-sweep"]
        del GUARDS["probe-sweep"]
        _RECORDER.update(saved)
    # The sweep call site itself: _red_on_revert must hand
    # _revert_one the very lists it returns, so a call site passing throwaway lists (which
    # would report every sweep green regardless of the per-guard bookkeeping) goes red. One
    # synthetic stale guard runs through the REAL _red_on_revert over a swapped REVERTS table.
    check("meta/red-on-revert-returns-revert-one-bookkeeping", swept,
          (["probe-sweep (case probe-sweep)"], []))


CASES = dict([
    ("config", case_config), ("absent", case_absent), ("frombase", case_frombase),
    ("pathenv", case_pathenv), ("gitpin", case_gitpin),
    ("dry", case_dry), ("happy", case_happy), ("carry", case_carry), ("discover", case_discover),
    ("ambiguous", case_ambiguous_worktree), ("branch", case_branch), ("badname", case_badname),
    ("audit", case_audit), ("conflict", case_conflict), ("mergefault", case_mergefault),
    ("regen", case_regen),
    ("reject", case_reject), ("idem", case_idem), ("remote-changed", case_remote_changed),
    ("push-unknown", case_push_unknown), ("generated-delete", case_generated_delete),
    ("author", case_author), ("rewind", case_rewind), ("timeout", case_timeout),
    ("observer", case_observer), ("symlink", case_symlink), ("tamper", case_tamper),
    ("filter", case_filter), ("eol", case_eol), ("lock-held", case_lock_held),
    ("fetch-failure", case_fetch_failure), ("state-mismatch", case_state_mismatch),
    ("cli", case_cli), ("refmap", case_refmap), ("basefetch", case_basefetch),
    ("leak", case_leak), ("partial", case_partial), ("signal", case_signal),
    ("crlf", case_crlf), ("isolation", case_isolation), ("latepush", case_latepush),
    ("rename", case_rename),
    ("gitread", case_gitread), ("ancestry", case_ancestry), ("rootskip", case_rootskip),
    ("faultscan", case_faultscan), ("childfault", case_childfault),
    ("harness", case_harness),
])


def _noop(*_args, **_kwargs):
    return None


def _ancestry_reverted(scratch, ancestor, descendant):
    """The reverted ancestry guard: every nonzero exit, a failed read included, reads as "not an
    ancestor"."""
    return _git(scratch, "merge-base", "--is-ancestor", ancestor, descendant, ok=None)[0] == 0


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
    ("child-fault", (_noop, "childfault")),
    ("regenerate-exit", (_noop, "regen")),
    ("check-exit", (_noop, "regen")),
    ("fixpoint", (_noop, "regen")),
    ("reconcile", (_noop, "push-unknown")),
    ("scratch-setup", (_noop, "eol")),
    ("scratch-complete", (_noop, "partial")),
    ("generated-confined", (_noop, "symlink")),
    ("pushed-bytes", (_noop, "filter")),
    ("ancestry", (_ancestry_reverted, "ancestry")),
    ("commit-parents", (_noop, "tamper")),
    ("commit-tree", (_noop, "tamper")),
    ("fresh-checkout", (_noop, "crlf")),
    ("push-lease", (lambda branch, old: [], "rewind")),
    ("push-indeterminate", (_reraise, "timeout")),
])


def _run_case(name, tmp, fn=None):
    """One case (fn overrides the CASES lookup for the harness probes in case_harness); an
    unexpected exception fails that case (recorded apart from check failures, see CRASHES) and
    never crashes the suite."""
    try:
        (fn or CASES[name])(tmp)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        _RECORDER["crashes"].append("case %s raised %s: %s" % (name, type(exc).__name__, exc))


def _revert_outcome(failures, crashes):
    """How one reverted guard's case counts: any crash is cannot-evaluate, EVEN beside a failed
    named check (the exception may have forced that failure, so it proves nothing about the
    guard); a failure with no crash is the red credit; neither is a
    stale mutant. Pinned by the meta/revert-* vectors in case_harness, and at the sweep's own
    per-guard step by meta/revert-call-site-crash-outranks-failure (see _revert_one)."""
    if crashes:
        return "cannot"
    if failures:
        return "red"
    return "stale"


def _revert_one(name, revert, case, tmp, stale, cannot, fn=None):
    """The case of ONE reverted guard under a fresh recorder, counted through _revert_outcome:
    the crash-outranks-failure precedence AND the stale/cannot bookkeeping are applied HERE, at
    the step every sweep entry goes through, so the sweep keeps no outcome handling of its own
    (re-deriving the bookkeeping from the return value at the sweep's own call site would be a
    step no vector pins). Pinned by
    meta/revert-call-site-crash-outranks-failure and meta/revert-bookkeeping-in-revert-one
    over a synthetic guard whose case records a failure and then raises, by
    meta/revert-stale-bookkeeping-in-revert-one over a synthetic guard
    whose case stays green (the stale append itself), and at the sweep call site by
    meta/red-on-revert-returns-revert-one-bookkeeping."""
    saved = GUARDS[name]
    GUARDS[name] = revert
    _RECORDER.update(executed=set(), list=[], failures=[], skipped=[], crashes=[])
    try:
        _run_case(case, tempfile.mkdtemp(prefix="red-%s-" % name, dir=tmp), fn=fn)
    finally:
        GUARDS[name] = saved
    outcome = _revert_outcome(_RECORDER["failures"], _RECORDER["crashes"])
    if outcome == "cannot":
        cannot.append("%s (case %s): %s" % (name, case, "; ".join(_RECORDER["crashes"])))
    elif outcome == "stale":
        stale.append("%s (case %s)" % (name, case))
    return outcome


def _red_on_revert(tmp):
    """(stale, cannot): the guards whose reverted form left their named case green, and the
    guards whose case raised (cannot-evaluate, and the crash OUTRANKS any assertion failure
    recorded beside it: an exception proves nothing about the guard even when a named check
    failed first; see _revert_one and _revert_outcome: the per-guard
    bookkeeping lives in _revert_one, so no step here re-reads the recorder around the
    precedence). Both lists must be empty to pass; the crash diagnostics are kept in the
    cannot entries."""
    stale, cannot = [], []
    for name, (revert, case) in REVERTS.items():
        _revert_one(name, revert, case, tmp, stale, cannot)
    _RECORDER.update(executed=_EXECUTED_SET, list=EXECUTED, failures=FAILURES, skipped=SKIPPED,
                     crashes=CRASHES)
    return stale, cannot


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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


def self_test(red_on_revert=False, report_path=None):
    """Every case inside fixture_git_lifecycle (GIT_* scrubbed, scratch HOME, global and system
    config pinned to os.devnull, a PATH-front git wrapper), under one private temp directory."""
    sys.path.insert(0, str(ROOT / "tools"))
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    from _git_fixture_env import fixture_git_lifecycle
    stale, cannot = [], []
    with fixture_git_lifecycle():
        with tempfile.TemporaryDirectory(prefix="merge-train-selftest-") as raw:
            tmp = os.path.realpath(raw)
            for name in CASES:
                _run_case(name, tmp)
            if red_on_revert and not FAILURES and not CRASHES:
                stale, cannot = _red_on_revert(tmp)
    expected = _expected_check_ids()
    if expected is None:
        return 2
    not_run = set(n for n, _why in SKIPPED)
    for check_id in sorted(expected - _EXECUTED_SET - not_run):
        FAILURES.append("execution-set/missing: %s" % check_id)
    for check_id in sorted((_EXECUTED_SET | not_run) - expected):
        FAILURES.append("execution-set/extra: %s" % check_id)
    for check_id, why in SKIPPED:
        print("SELF-TEST NOT RUN %s: %s" % (check_id, why), file=sys.stderr)
    for guard in stale:
        FAILURES.append("red-on-revert: reverting guard %s left its case green" % guard)
    for guard in cannot:
        FAILURES.append("red-on-revert: cannot evaluate guard %s: its case raised (a crash "
                        "outranks its assertion failures)" % guard)
    FAILURES.extend(CRASHES)
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: %d unique checks executed; %sexecution set reconciled against "
          "tools/selftest_checks.toml%s" % (len(EXECUTED), (
              "%d registered checks NOT RUN (%s), not counted as executed; " % (
                  len(SKIPPED), ", ".join(n for n, _why in SKIPPED))) if SKIPPED else "", (
              "; all %d guards in GUARDS went red when reverted through their cases' own "
              "assertions with no case exception recorded (a crash counts as cannot-evaluate, "
              "never as red)" % len(REVERTS))
              if red_on_revert else ""))
    return 0


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
