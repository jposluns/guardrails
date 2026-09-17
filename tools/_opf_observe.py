#!/usr/bin/env python3
"""opf observe: the caller-side, git-derived observations the store-integrity engine cannot read itself.

U6's `_opf_check.validate_store` is offline and deterministic: it reads NO git, so the git-aware facts its
across-time and topology checks need (is the store version-controlled, what remote does it actually ride,
what did the prior committed snapshot hold) are supplied by an inert `observations` object. This module is
that supplier for the `opf doctor` verb (and, later, `render --write` and `init`). Its public surface is:

  gather(resolution) -> (observations, notes)
  self_test()        -> 0/1/2

`gather` returns an `observations` dict carrying ONLY the keys it could HONESTLY determine (a subset of
`{"tracked", "actual_remote", "prior"}`, the exact `_opf_check._OBSERVATION_KEYS`) and a `notes` list of
human-readable strings explaining every omission. It NEVER forges a value: when git cannot answer, the key
is OMITTED and a note is added, and the engine then routes the affected check to a named cannot-evaluate,
which is the honest outcome. A partial `prior` is never forged either: any parse failure, absent object, or
read failure while reconstructing the prior snapshot omits `prior` ENTIRELY (a partial prior would falsely
accuse the store), keeping only the honest empty prior for the unborn-HEAD (no-commits) case.

Trust-boundary hardening of the git subprocess surface (this is a new external boundary,
SECI-threat-model-boundaries; the git call is untrusted-context input, SECI-untrusted-content /
guard-input-soundness): every git invocation runs `--no-replace-objects` (a replacement ref cannot
substitute the bytes a read returns), binds explicitly to the store root with `-C` rather than inheriting a
cwd, and runs under an ALLOWLIST-scrubbed environment (every ambient `GIT_*` variable dropped so an inherited
GIT_DIR / GIT_WORK_TREE / GIT_CONFIG cannot rebind or reconfigure the call; only PATH and HOME are carried
over, and the few config-neutralizing variables git genuinely needs are re-applied). Each call is bounded by
a timeout; a timeout, an OS launch failure, or a read the scrub breaks all fail SAFE to omit-plus-note,
never a silent allow. git is resolved via shutil.which and absolutized (os.path.abspath), so the launched
child is pinned to an absolute path and cannot be re-resolved at exec time; when git is absent from PATH
entirely the observations are all omitted (and `self_test` SKIPs clean, like the POSIX-signal watchdogs).

Stdlib only (`subprocess`, `tomllib`, `shutil`); imports `_opf_check` (for the per-record body digest) and
`_opf_emit` (for its EmitError). Launched via opf.py under `-I -B`.
"""
import os
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the guarded sibling imports below

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: tools/_opf_observe.py requires Python 3.11+ (tomllib).")

import _opf_check   # noqa: E402  the store-integrity engine: its _record_digest + observation-key roster
import _opf_emit    # noqa: E402  the canonical emitter: EmitError, raised by an out-of-subset record body
import _opf_store   # noqa: E402  the resolver: the fixed store-tree / machine-store directory-name constants

# The fixed machine-store filenames, reused from the engine so this reader cannot drift from the layout the
# validator enforces (single source of truth; guard-input-soundness).
_MANIFEST_NAME = _opf_check.MANIFEST_NAME
_VERSION_NAME = _opf_check.VERSION_NAME
_COUNTERS_NAME = _opf_check.COUNTERS_NAME
_WORKLOG_NAME = _opf_check.WORKLOG_NAME
_INDEX_SUFFIX = _opf_check.INDEX_SUFFIX

# The two store layouts (spec 4). Reused from the resolver so the reader recognizes exactly what the store
# can declare; an unknown layout is a fail-closed omit, never a guess.
_LAYOUTS = ("inline", "per-record")

# A per-git-call runtime bound (SECA resource-bounds): a hung or pathological git read fails SAFE to an
# omit-plus-note rather than blocking the doctor verb indefinitely. Generous relative to a local object read.
_GIT_TIMEOUT_S = 30

# The honest empty prior for an unborn HEAD (a repository with no commits): nothing was ever committed, so
# every across-time baseline is genuinely empty. Shaped exactly as _opf_check._normalize_prior accepts.
def _empty_prior():
    return {"releases": [], "counters_high": {}, "records": {}, "digests": {}}


_GitOutcome = namedtuple("_GitOutcome", "completed rc out err")


def _git_path():
    """The absolute path to the git executable, or None when git is not on PATH. Resolved via shutil.which
    and then made absolute with os.path.abspath (a relative which() result, from a relative PATH entry, is
    anchored to the cwd at resolution time), so in ALL cases the launched child is pinned to an absolute path
    rather than a bare `git` and cannot be re-resolved at exec time by a later PATH change or a relative-name
    re-lookup. os.path.abspath (not realpath) is used so symlinks are preserved: git is often a symlink and
    the symlink is what must run. One disclosed residual: PATH-based resolution at which()-time reads the
    ambient PATH, so a PATH-shadowing binary can still be CHOSEN at lookup; the chosen binary is then pinned
    absolute. This is consistent with the corpus's disclosed PATH-resolution residual."""
    p = shutil.which("git")
    return os.path.abspath(p) if p else None


def _scrubbed_env():
    """Build the minimal, allowlist environment every git call runs under. Every ambient `GIT_`-prefixed
    variable is DROPPED (an inherited GIT_DIR/GIT_WORK_TREE/GIT_CONFIG/GIT_OBJECT_DIRECTORY could otherwise
    rebind the call to a DIFFERENT repository, inject configuration, or redirect object lookup); only PATH
    and HOME are carried over. The few variables git genuinely needs to run non-interactively and free of
    ambient configuration are then RE-APPLIED: global and system config neutralized to os.devnull, the
    system config search disabled, the terminal prompt disabled, optional locks turned off (read-only), and
    the locale pinned so output is deterministic. Lazy-fetch suppression is NOT applied here: it is a
    property of a read-only OBSERVATION, not of the scrubbed env itself (fixture setup builds real partial
    clones and legitimately lazy-fetches), so _run_git applies GIT_NO_LAZY_FETCH per call instead."""
    env = {}
    for name in ("PATH", "HOME"):
        val = os.environ.get(name)
        if val is not None:
            env[name] = val
    # Re-apply only what git needs, config injection neutralized (allowlist stance).
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"
    return env


def _run_git(git, store_root, args, timeout=_GIT_TIMEOUT_S, allow_lazy_fetch=False):
    """Run `git --no-pager --no-replace-objects -c core.fsmonitor=false -C <store_root> <args>` under the
    scrubbed environment, bounded by a
    timeout. Returns a _GitOutcome: `completed` is True only when the process ran to completion (then `rc`,
    `out` (bytes), and `err` (text) are meaningful); it is False on a timeout or an OS launch failure, with
    `err` naming the reason -- the fail-safe omit-plus-note signal. `--no-replace-objects` is passed so a
    replacement ref cannot substitute the bytes a read returns; `-C` binds the call explicitly to the store
    root rather than inheriting a cwd (explicit-binding-over-ambient-context). core.fsmonitor is forced off
    by a command-scope `-c` (which overrides file and runtime config for fsmonitor) so an fsmonitor program
    configured in the observed repository cannot LAUNCH A PROCESS during any of these read-only observations
    (the scrubbed env already neutralizes global/system config, but the repository's OWN .git/config is read
    by design; every caller here is an observation -- rev-parse, ls-files, remote, show, status, check --
    that never wants to start the monitor, so suppressing it changes no observed result). GIT_NO_LAZY_FETCH
    is forced on for every observation (allow_lazy_fetch defaults False), so a read of an object ABSENT
    locally in a partial clone fails closed to the omit-plus-note path (a `git show` of an absent blob
    returns rc != 0, handled exactly as any other absent object) rather than triggering a promisor fetch,
    which would EXECUTE the repository-configured core.sshCommand -- the same code-execution class as the
    fsmonitor vector, reached through git's promisor machinery. This changes no result for a PRESENT object,
    and (being an allowlist scrub) the env drops any ambient GIT_NO_LAZY_FETCH, so it is applied here rather
    than relied on from the environment. allow_lazy_fetch=True is passed ONLY by self-test FIXTURE SETUP that
    must reproduce the adopter's real git (a partial-clone checkout or a real `git add` that legitimately
    lazy-fetches); no production observation passes it. --no-pager is
    passed for parity with _run_git_config_discovery and defence in depth: the captured, non-TTY stdout
    already suppresses the pager, so a pager configured for a subcommand cannot launch a process."""
    cmd = [git, "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(store_root)] + list(args)
    env = _scrubbed_env()
    if not allow_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"   # no promisor fetch on a missing object (a fetch can reach core.sshCommand)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _GitOutcome(False, None, b"", "git timed out after {}s".format(timeout))
    except OSError as exc:
        return _GitOutcome(False, None, b"", "could not launch git ({})".format(exc))
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return _GitOutcome(True, proc.returncode, proc.stdout or b"", err)


# --- OPF-STATUS-FILTER-SUPPRESS: neutralize repo/worktree clean-process filters on a worktree-content probe -
# A `git status` that COMPARES WORKTREE CONTENT against the index runs any repository- or worktree-configured
# `clean` / `process` filter driver for a matched path, EXECUTING that driver's external command during a
# read-only observation (exec-on-observe; SECI-config-is-executable-trust-gate). This is the same
# executable-config trust-gate class the fsmonitor and lazy-fetch->core.sshCommand suppressions in _run_git
# already close, but those vectors never read worktree content, so `-c core.fsmonitor=false` + GIT_NO_LAZY_FETCH
# cover them while a clean/process filter is reached ONLY through worktree-content comparison and needs the
# targeted neutralization below. The scrubbed env already points global/system config at os.devnull, so the
# only filter git can exec on such a probe is one defined in the repo's OWN .git/config or .git/config.worktree
# -- exactly the set _filter_neutralizing_args enumerates and empties.

def _filter_neutralizing_args(git, store_root, timeout=_GIT_TIMEOUT_S):
    """Return the command-scope `-c` flags that neutralize every repository/worktree-configured clean/process
    filter driver reachable when git compares worktree content against the index at `store_root`, so a
    `git status` over that root cannot EXECUTE a repo-planted external filter command on a read-only
    observation (exec-on-observe; SECI-config-is-executable-trust-gate, the class OPF-FSMON-SUPPRESS closed
    for core.fsmonitor and the lazy-fetch->core.sshCommand vector).

    Enumerates the exec-capable driver names through the SAME _run_git boundary and scrubbed env the status
    probe uses (`config -z --name-only --list`), so the neutralized set provably EQUALS the set git could
    exec on the probe (global/system config is neutralized to os.devnull for both; guard-input-soundness).
    Using _run_git, NOT _run_git_config_discovery, is load-bearing for that equality: the config-discovery
    variant would let global/system config in and enumerate keys git will not read on the scrubbed status.
    `--name-only` returns keys WITHOUT values, so a malicious command BODY is never read or logged; reading
    config runs no filter and no hook, so the enumeration is itself inert, and `-c core.fsmonitor=false`
    (added by _run_git) keeps fsmonitor suppressed during it too.

    For each distinct driver <name> whose `filter.<name>.clean` or `filter.<name>.process` key is configured
    (git config section/subkey are case-insensitive, matched here case-insensitively; the subsection <name>
    is case-sensitive and may itself contain dots, so exactly ONE fixed trailing component is stripped),
    emits `-c filter.<name>.clean= -c filter.<name>.process= -c filter.<name>.required=false`. Command-scope
    `-c` is highest precedence (it overrides repo AND worktree config, the relationship `-c
    core.fsmonitor=false` already relies on); an empty clean/process makes git treat the driver as identity
    so NO external command is spawned; required=false stops an emptied-but-required driver from erroring and
    masking the verdict. git's BUILT-IN text/eol/working-tree-encoding conversion is not a filter driver and
    is untouched, so a text=auto / CRLF store is not falsely flagged dirty: that built-in conversion is
    deliberately PRESERVED (the A-vs-B property a raw --no-filters reimplementation would regress).

    FAIL-CLOSED (guard-input-soundness, check-fails-closed-on-unreadable): neutralization completeness rests
    entirely on this enumeration, so any state where the exec-able set cannot be proven is a cannot-evaluate
    that RAISES RuntimeError (the caller turns it into a fail-closed refusal), never a silent empty list read
    as "no filters". Raises when the enumeration could not run (timeout / launch failure), returned a nonzero
    rc (a corrupt .git/config makes --list fail; unreadable input is not "no filters"), was not
    NUL-terminated as `-z` promises, carried a filter clean/process key with a non-UTF-8 driver name that
    cannot be safely re-emitted as a matching `-c` override (emitting a replacement-mangled name would leave
    the REAL driver exec-able -- a fail-OPEN, so it fails closed, mirroring _is_partial_clone), or carried a
    filter clean/process key that does not parse into a well-formed (non-empty) driver name.

    FUTURE CALLERS (disclose-guard-residuals): any NEW observation that reads WORKTREE CONTENT through git (a
    non-`--cached` diff, `diff-files`, `ls-files -m`, `update-index --refresh`, or a `status` elsewhere) runs
    clean/process filters and MUST prepend these args too; a verb that never compares worktree content
    (rev-parse, ls-files, config, cat-file, remote, show HEAD:<blob>) does not and must not pay the cost. A
    residual TOCTOU remains: enumeration and the status are two calls, so a driver ADDED to .git/config
    between them could exec on the status; under the held OPF lease with no legitimate concurrent writer the
    window is milliseconds and the same adversary could already rewrite HEAD, so only a raw filter-free
    reimplementation (rejected for its text/eol regression) would close it fully."""
    listing = _run_git(git, store_root, ["config", "-z", "--name-only", "--list"], timeout=timeout)
    if not listing.completed:
        raise RuntimeError("could not enumerate the store's git filter configuration ({})".format(
            listing.err.strip()))
    if listing.rc != 0:
        raise RuntimeError("git config --list failed (rc {}); the store's filter configuration is unreadable, "
                           "so its exec-capable filters cannot be neutralized".format(listing.rc))
    raw = listing.out
    if raw and not raw.endswith(b"\x00"):
        raise RuntimeError("git config --list returned a payload that is not NUL-terminated; the store's "
                           "filter configuration cannot be trusted (fail-closed)")
    names = []
    seen = set()
    for kb in raw.split(b"\x00"):
        if not kb:
            continue
        low = kb.lower()   # git section/subkey are case-insensitive; ASCII lower on the raw bytes suffices
        if not low.startswith(b"filter."):
            continue
        if not (low.endswith(b".clean") or low.endswith(b".process")):
            continue   # a filter.<name>.smudge / .required (etc.) is not exec-able on status: not neutralized
        # A filter clean/process key: its <name> must round-trip cleanly to emit a MATCHING -c override. A
        # non-UTF-8 subsection name cannot, and emitting a replacement-mangled name would leave the REAL
        # driver exec-able (a fail-OPEN), so a non-UTF-8 name is a cannot-evaluate -> fail-closed (mirrors
        # _is_partial_clone's non-UTF-8 handling).
        try:
            key = kb.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("git config carries a filter driver key with a non-UTF-8 name; it cannot be "
                               "safely neutralized, so the store's filter configuration is refused "
                               "(fail-closed)")
        # <name> is everything between the fixed `filter.` prefix and the fixed trailing `.clean`/`.process`
        # component. The subsection may itself contain dots, so strip exactly ONE trailing component (rfind).
        name = key[len("filter."):key.rfind(".")]
        if not name:
            raise RuntimeError("git config carries a malformed filter key {!r} with no driver name; the "
                               "store's filter configuration cannot be trusted (fail-closed)".format(key))
        if name not in seen:
            seen.add(name)
            names.append(name)
    args = []
    for name in names:
        args += ["-c", "filter.{}.clean=".format(name),
                 "-c", "filter.{}.process=".format(name),
                 "-c", "filter.{}.required=false".format(name)]
    return args


# --- OPF-D2B: the config-discovery ignore probe --------------------------------------------------------
# The default _scrubbed_env above NEUTRALIZES global/system git configuration so an observation reads only
# the repository. The `opf init` ignore preflight (opf.py:_init_unignored) needs the OPPOSITE for one
# read-only question: whether the adopter's REAL `git add` would ignore the planned store, which depends on
# the adopter's global and system core.excludesFile. This variant lets git DISCOVER that real configuration
# through its DEFAULT locations (HOME and XDG_CONFIG_HOME reach ~/.gitconfig and ~/.config/git/{config,ignore},
# and the system config is read normally), while every other ambient GIT_* variable is dropped by
# construction (allowlist), so an inherited GIT_DIR / GIT_WORK_TREE / GIT_OBJECT_DIRECTORY / GIT_INDEX_FILE /
# pathspec variable cannot rebind the repository or redirect object lookup.
#
# The env-based CONFIG OVERRIDES (GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM, the runtime
# GIT_CONFIG_COUNT + its indexed GIT_CONFIG_KEY_<n> / GIT_CONFIG_VALUE_<n> pairs, and the legacy GIT_CONFIG /
# GIT_CONFIG_PARAMETERS) are deliberately DROPPED, not honoured: they can set ANY config key (trace2.* to
# make the read-only probe write or append an outside file, core.fsmonitor to launch a process; a file-based
# core.worktree does NOT rebind a -C-bound check-ignore, only --work-tree does and it is never passed, so
# core.worktree is not among the reasons these are dropped), so carrying them would re-open exactly the
# redirect/trace surface the scrub exists to close (SECI-threat-model-boundaries, prefer-removing-a-path).
# GIT_CONFIG_NOSYSTEM is NOT among the dropped
# overrides: it is instead CARRIED as a safe on/off toggle (it only enables/disables the system config and
# cannot set a key). DISCLOSED RESIDUAL: because the env-based
# overrides are dropped, an adopter who runs `opf init` with GIT_CONFIG_GLOBAL or GIT_CONFIG_* set (a wrapper,
# or an explicit `git -c ...`) may see this check diverge from what that same environment's `git add` would
# do; the common case (a real ~/.gitconfig, no env override) is honoured exactly.
#
# Even the adopter's REAL config files can carry trace2 or fsmonitor settings, so the probe FORCES trace off
# through the GIT_TRACE2* / GIT_TRACE* environment (which git honours ahead of any config; a command-line
# `-c trace2.*` cannot, because trace2 reads its config before `-c` is applied) and disables core.fsmonitor
# through a command-scope `-c` at the call site. So no configuration reachable by the probe can turn a
# read-only check-ignore into a file write or a launched process. (One benign exception: reading a SPLIT
# INDEX refreshes the shared index file's mtime, a pre-existing git metadata touch on the adopter's own .git
# that GIT_OPTIONAL_LOCKS does not suppress; this is not a content write, an outside write, or command
# execution.)

# The ambient names carried over so git can discover the adopter's real config through its default paths.
# GIT_CONFIG_NOSYSTEM is a SAFE toggle (it only enables/disables the system config, it cannot set a key), so
# carrying it lets an adopter (or the self-test) opt out of /etc/gitconfig without any injection surface.
_CONFIG_DISCOVERY_KEEP = ("PATH", "HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_NOSYSTEM")

# Trace toggles FORCED to a disabling value in the probe environment. The GIT_TRACE2* settings take
# precedence over trace2.* CONFIG (a command-line `-c` cannot, since trace2 reads its config early); the
# legacy GIT_TRACE* have no config form and are dropped by the allowlist anyway, but are pinned off too.
_TRACE_OFF = {
    "GIT_TRACE": "0", "GIT_TRACE_PACKET": "0", "GIT_TRACE_PERFORMANCE": "0", "GIT_TRACE_SETUP": "0",
    "GIT_TRACE2": "0", "GIT_TRACE2_EVENT": "0", "GIT_TRACE2_PERF": "0",
}


def _config_discovery_env():
    """Build the environment for the config-discovery ignore probe (OPF-D2B). Carry only PATH, HOME,
    XDG_CONFIG_HOME, and the safe GIT_CONFIG_NOSYSTEM toggle, so git DISCOVERS the adopter's real global and
    system configuration through its default
    locations (honouring a global or system core.excludesFile the same way `git add` would), and DROP every
    other ambient GIT_* variable by construction (allowlist), so no inherited redirect, object, pathspec, or
    config-override variable survives. The env-based config overrides (GIT_CONFIG_GLOBAL/SYSTEM, the runtime
    GIT_CONFIG_COUNT/KEY/VALUE pairs, GIT_CONFIG, GIT_CONFIG_PARAMETERS) are dropped, not honoured, because
    they can set any key (core.worktree, trace2.*, core.fsmonitor) and would re-open the redirect/trace
    surface. Trace is FORCED off through the GIT_TRACE2* / GIT_TRACE* env (which outranks trace2.* config),
    and the call is forced non-interactive and deterministic; core.fsmonitor is disabled by command-scope
    config at the call site. Lazy fetching is forced off (GIT_NO_LAZY_FETCH), so a missing indexed .gitignore
    blob cannot trigger a promisor fetch that would reach core.sshCommand."""
    env = {}
    for name in _CONFIG_DISCOVERY_KEEP:
        val = os.environ.get(name)
        if val is not None:
            env[name] = val
    env.update(_TRACE_OFF)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"   # no promisor fetch during the read-only probe (a fetch can reach core.sshCommand)
    env["LC_ALL"] = "C"
    return env


def _run_git_config_discovery(git, store_root, args, timeout=_GIT_TIMEOUT_S):
    """Like _run_git, but under the config-discovery environment (OPF-D2B), and with core.fsmonitor forced
    off by a command-scope `-c` so an adopter fsmonitor config cannot launch a monitor process during the
    read-only probe (a command-line `-c` overrides file and runtime config for fsmonitor; trace2 is instead
    forced off through the environment in _config_discovery_env, since its early config read ignores `-c`).
    The probe also passes --no-pager, so a pager configured for check-ignore cannot launch a process (defence
    in depth: the captured, non-TTY stdout already suppresses the pager).
    Returns a _GitOutcome shaped exactly as _run_git's."""
    cmd = [git, "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(store_root)] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=_config_discovery_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return _GitOutcome(False, None, b"", "git timed out after {}s".format(timeout))
    except OSError as exc:
        return _GitOutcome(False, None, b"", "could not launch git ({})".format(exc))
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return _GitOutcome(True, proc.returncode, proc.stdout or b"", err)


def _worktree_open_succeeds(worktree_path):
    """Replicate git's own worktree-file open for a skip-worktree ignore entry (dir.c add_patterns ->
    read_skip_worktree_file_from_index, git 2.53.0): git opens the worktree path with O_RDONLY | O_NOFOLLOW
    and falls back to reading the INDEX BLOB ONLY when that open, or the fstat that follows it, is
    UNSUCCESSFUL. This returns True exactly when git's open+fstat would succeed -- so git reads from the
    descriptor and NEVER index-reads (a readable regular file is read from disk, matching the probe; a
    readable directory opens but its read then fails with no index fallback), meaning an unavailable blob
    cannot change what `git add` ignores. It returns False when git's open fails (an absent path, a symlink
    under O_NOFOLLOW, an unreadable regular file, or an UNREADABLE directory whose O_RDONLY open is denied),
    the cases where git falls back to the index blob. O_NONBLOCK is added purely so a worktree path that is
    a FIFO or device cannot BLOCK this read-only probe (git would itself block on such a path, which is not
    a fetch-and-ignore case); it does not change the open outcome for a regular file, directory, symlink, or
    absent path, the arrangements git's fallback decision actually turns on."""
    try:
        fd = os.open(str(worktree_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False   # git's O_NOFOLLOW open fails -> it falls back to the index blob
    try:
        os.fstat(fd)   # git falls back too if the post-open fstat fails (near-impossible on a live fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def _is_partial_clone(git, store_root):
    """True when the repository at store_root is a PARTIAL clone -- one with a promisor remote git can
    lazy-fetch from -- OR when partial-clone-ness cannot be determined. Returns False ONLY when all three
    probes complete cleanly and find no promisor filter, no boolean-true promisor remote, and no
    partialClone extension, i.e. a definite full (or otherwise non-promisor) clone.

    This gates the whole indexed-ignore availability check: the silent-fetch-and-ignore hazard that check
    guards against can arise ONLY in a partial clone, because only there can `git add` lazy-fetch an absent
    indexed .gitignore blob and then ignore the store. In a FULL clone an absent OID never causes a silent
    fetch-and-ignore -- git either stages (an absent submodule COMMIT is not a blob, so do_read_blob rejects
    it and nothing is ignored) or fails LOUDLY (a genuinely-needed absent object aborts `git add`) -- so a
    full-clone availability refusal would be a pure over-refusal (see indexed_ignore_availability's docstring).

    The predicate is git 2.53.0's OWN registration test, repo_has_promisor_remote (promisor-remote.c
    promisor_remote_config / promisor_remote_init, reached by odb.c's missing-object fallback): git will
    lazy-fetch a missing object iff its promisor-remote list is non-empty, which any ONE of these populates
    (each empirically confirmed against a real `git add` on a blobless clone with an absent skip-worktree
    .gitignore blob, git 2.53.0):
      (a) any `remote.<name>.partialclonefilter` is PRESENT with ANY value -- including the empty string and
          the literal `false`. Presence alone unconditionally registers the promisor and OVERRIDES a
          sibling `remote.<name>.promisor=false`, so this is tested by PRESENCE, never by value.
      (b) any `remote.<name>.promisor` that evaluates BOOLEAN-TRUE under git's own bool parser
          (true/yes/on/1/nonzero-int; false/no/off/empty/0 do NOT register). A `promisor=false` does NOT
          remove a registration made by (a) or (c). Because value semantics decide this, each occurrence is
          re-evaluated with git's own `--type=bool` rather than hand-parsed, and a garbage/unparseable bool
          (a git error) fails closed to partial. The re-query addresses the ENUMERATED key by name, so a
          promisor remote whose name does not cleanly UTF-8 round-trip (e.g. a non-UTF-8 remote name like
          `[remote "up\xffstream"]`) cannot be re-queried -- a lossy decode would ask for a different,
          nonexistent key and read rc 1 (not found). An enumerated key that cannot be cleanly re-queried
          (a non-UTF-8 name, or any re-query rc other than 0) is a cannot-determine that fails closed to
          partial: it is NEVER read as boolean-false / full, because the key demonstrably exists (it was just
          enumerated) and git would still lazy-fetch through the real promisor remote it names.
      (c) `extensions.partialClone` is PRESENT (its value names the default promisor remote, so it is tested
          by presence, and a `false` value merely names a remote called "false"). The stated predicate
          gated this on `core.repositoryformatversion >= 1`; that gate is DROPPED. Empirically (isolated
          /dev/shm repro, git 2.53.0: extension-only + format 0 vs an identical control without the
          extension), git HONOURS the extension at format version 0 -- the format-0 control staged the store
          while the extension case lazy-fetched and ignored it -- matching git's handle_extension_v0
          historical-compatibility handling for `partialclone`. Reading the extension by presence with NO
          format gate is therefore both the git-faithful direction AND the fail-closed one (a stray format
          version can never turn a real extension into a false pass), so no format read is needed.

    Round 10's promisor-only probe missed (a) (a filter with promisor unset read as a FULL clone: a false
    pass) and over-refused on (b) (a `promisor=false` LINE read as present-therefore-partial: an
    over-refusal); both are corrected here. The probes run under _run_git_config_discovery (same
    allowlist / GIT_NO_LAZY_FETCH env the availability probe uses). ALTERNATES do not share promisor config,
    so reading this LOCAL repository's config is correct (a repo whose only promisor config lives in an
    alternate does not lazy-fetch). A probe that cannot RUN, or returns an rc other than 0 (found) or 1 (not
    found), is a cannot-determine that resolves to partial = keep checking -- the safe, fail-closed direction
    (guard-input-soundness, check-fails-closed-on-unreadable)."""
    # (a) A partialclonefilter on ANY remote registers the promisor by presence (value-blind), overriding a
    # sibling promisor=false; --get-regexp rc 0 means at least one such key exists.
    filt = _run_git_config_discovery(
        git, store_root, ["config", "--get-regexp", r"^remote\..*\.partialclonefilter$"])
    if not filt.completed or filt.rc not in (0, 1):
        return True    # cannot determine -> partial (fail-closed)
    if filt.rc == 0:
        return True    # a partialclonefilter is present: a promisor is registered
    # (c) The partialClone extension registers the named default promisor remote by presence (no format gate,
    # per the empirical resolution above).
    ext = _run_git_config_discovery(git, store_root, ["config", "--get", "extensions.partialClone"])
    if not ext.completed or ext.rc not in (0, 1):
        return True    # cannot determine -> partial (fail-closed)
    if ext.rc == 0:
        return True    # the partialClone extension is declared: a partial clone
    # (b) A promisor remote registers only when its value is boolean-TRUE. Enumerate the promisor keys, then
    # re-evaluate EACH with git's own bool parser (--type=bool), so promisor=false does not register and a
    # garbage bool (a git error) fails closed to partial.
    prom = _run_git_config_discovery(
        git, store_root, ["config", "-z", "--name-only", "--get-regexp", r"^remote\..*\.promisor$"])
    if not prom.completed or prom.rc not in (0, 1):
        return True    # cannot determine -> partial (fail-closed)
    if prom.rc == 1:
        return False   # no promisor filter, no extension, and no promisor key at all: a full clone
    for kb in prom.out.split(b"\0"):
        if not kb:
            continue
        # The enumerated key name must round-trip cleanly to re-query it. A remote name carrying non-UTF-8
        # bytes (e.g. `[remote "up\xffstream"]`) enumerates fine as raw bytes, but a lossy decode would mangle
        # it, so the --type=bool re-query below would ask for a DIFFERENT (nonexistent) key and read rc 1
        # (not found) -- which must NOT be mistaken for boolean-false / full. The key demonstrably EXISTS (it
        # was just enumerated), so a name that cannot be re-queried is a cannot-determine -> partial.
        try:
            key = kb.decode("utf-8")
        except UnicodeDecodeError:
            return True    # a non-UTF-8 promisor key name cannot be cleanly re-queried: fail-closed to partial
        val = _run_git_config_discovery(git, store_root, ["config", "--type=bool", "--get-all", key])
        if not val.completed or val.rc != 0:
            return True    # an ENUMERATED key that does not cleanly re-query -- rc 1 (not found: the name did
                           # not round-trip), a garbage/unparseable bool (rc 128), or a probe that cannot run
                           # -- is a cannot-determine -> partial (fail-closed), NEVER read as boolean-false/full
        if any(line.strip() == "true" for line in val.out.decode("utf-8", "replace").splitlines()):
            return True    # a boolean-true promisor remote: a partial clone
    return False   # every enumerated promisor key cleanly re-queried (rc 0) AND evaluated boolean-false, and
                   # no filter or extension: a full clone


def indexed_ignore_availability(git, store_root, gitignore_relpaths):
    """Repo-relative candidate .gitignore paths whose ignore rule the adopter's own `git add` would read
    from the INDEX but whose blob is NOT available locally without a promisor fetch. The config-discovery
    ignore probe forces GIT_NO_LAZY_FETCH, so it reads such a blob as no-rule (destination not-ignored),
    while `git add` fetches it and can then silently ignore the store: a cannot-evaluate the caller must
    fail closed on, not pass (guard-input-soundness, check-fails-closed-on-unreadable). A blob that IS
    available is read identically by this probe and by `git add` (parity, confirmed empirically), and a
    .gitignore with a readable regular working-tree file is read from disk by both, so neither is returned.

    The whole check is GATED on partial-clone-ness (_is_partial_clone): the silent-fetch-and-ignore hazard
    exists ONLY in a partial clone, because only a promisor remote lets `git add` lazy-fetch an absent OID. In
    a full (non-promisor) clone git CANNOT lazy-fetch, so an absent OID never causes a silent ignore -- `git
    add` either stages (an absent submodule COMMIT is not a blob) or aborts LOUDLY on a genuinely-needed absent
    object -- and this function returns [] immediately (no availability refusal), the only git-faithful answer.
    Only in a partial clone is the per-entry logic below reached.

    git index-reads a .gitignore ONLY for a stage-0 skip-worktree entry whose worktree path its own
    O_NOFOLLOW open cannot use (add_patterns -> read_skip_worktree_file_from_index -> do_read_blob):
    confirmed against git 2.53.0. That fallback is MODE-BLIND -- do_read_blob reads the entry's OID and
    applies the object as ignore patterns whenever it is a blob, NEVER consulting the index mode -- so this
    guard is mode-blind too: it refuses on an unavailable OID regardless of the entry's mode. An entry is
    returned only when ALL hold -- (1) its returned path is EXACTLY one of the requested candidate paths (a
    --literal-pathspecs listing still matches DESCENDANTS of a candidate directory, e.g. ".gitignore/data"
    under a candidate ".gitignore", and a tree / sparse-dir entry, whose ls-files path carries a trailing
    "/"; neither is a file git reads as ignore patterns, so exact membership drops both); (2) stage 0 with
    the skip-worktree flag set (`ls-files -t`/`-v` tags it "S"); (3) a worktree path where git's own
    O_RDONLY|O_NOFOLLOW open is UNSUCCESSFUL, so git falls back to the index OID (see _worktree_open_succeeds:
    this covers an absent path, a symlink, an unreadable regular file, and an UNREADABLE directory, while a
    readable regular file -- read from disk, parity with the probe -- and a readable directory -- opened, its
    read then fails with no fallback -- both PROCEED); and (4) an unavailable OID. Any other entry -- not an
    exact candidate, not stage 0, not skip-worktree, or one whose worktree open succeeds -- is one git never
    index-reads, so an absent object for it cannot change what `git add` ignores and it is not returned (no
    over-refusal).

    The mode allowlist that earlier gated this (regular file / symlink only) is deliberately GONE, because
    it was a FALSE PASS of the guarded class: a fabricated stage-0 skip-worktree GITLINK-mode (160000) entry
    whose OID is a promisor-fetchable BLOB slips a mode check, yet git's mode-blind do_read_blob fetches that
    blob and silently ignores the store, so refusing on the unavailable OID regardless of mode is what
    matches `git add`. An AVAILABLE OID is not evaluated here at all: the sibling layer-1 check-ignore probe
    in opf.py reads it mode-blind, and with the object present that probe itself refuses a matching store, so
    this availability check is reached only for an unavailable OID. A VALID submodule (gitlink 160000) has a
    directory at its worktree path, so its worktree open SUCCEEDS and it proceeds (no over-refusal).
    DISCLOSED CONSERVATIVE RESIDUAL (disclose-guard-residuals): the per-entry check above engages ONLY inside
    a partial clone (the _is_partial_clone gate). In a FULL clone an absent indexed OID -- a real submodule
    whose commit is sparse-omitted or lives at a `.gitignore` worktree path git's open cannot use (absent,
    unreadable directory, symlink) -- PROCEEDS, matching `git add`, which stages the store there because git
    cannot lazy-fetch and do_read_blob rejects the non-blob commit. WITHIN a partial clone the one remaining
    conservative residual is an absent submodule COMMIT OID: it is refused here even though `git add` would
    stage it (do_read_blob rejects the non-blob object) and even though the fetch it would attempt fails
    LOUDLY (exit 128) rather than silently ignoring -- a rare fail-closed over-refusal confined to the
    partial-clone case (commit objects are usually present, since a blob:none filter omits only blobs) and the
    safe direction, where the discarded mode allowlist was instead a false pass of the guarded class. A second
    conservative residual, unchanged: an unavailable applicable ignore
    OID is refused even though its (unfetchable) rules might not in fact match the store. Candidate
    paths are matched as LITERAL pathspecs (--literal-pathspecs) so a pathspec-magic sigil (a leading colon)
    is not read as magic (an empty listing that would MISS the ignoring blob) and a glob metacharacter (a
    bracketed class) is not expanded onto an unrelated indexed path (a false refusal); this mirrors the
    literal-"./"-prefix trick the sibling check-ignore call in opf.py uses for the same reason.

    Returns a sorted list of the unavailable repo-relative .gitignore paths; an empty list means every
    applicable ignore OID is answerable without a fetch. Raises RuntimeError only when git could not be RUN
    to list the index or to probe an OID (the call did not complete), or when `ls-files` itself failed -- a
    cannot-evaluate the caller fails closed on. A nonzero `cat-file -e` rc, whether the object is genuinely
    absent or its pack is unreadable, is not raised: it is treated as an unavailable OID and REFUSED
    (appended to the returned list). Both paths are fail-closed."""
    if not gitignore_relpaths:
        return []
    if not _is_partial_clone(git, store_root):
        return []   # a full (non-promisor) clone: git cannot lazy-fetch, so an absent indexed OID can never
                    # cause a silent fetch-and-ignore -- `git add` either stages or fails loudly -- and a
                    # refusal here would be a pure over-refusal. The hazard exists only in a partial clone.
    listing = _run_git_config_discovery(
        git, store_root,
        ["--literal-pathspecs", "ls-files", "-s", "-t", "-z", "--"] + list(gitignore_relpaths))
    if not listing.completed:
        raise RuntimeError("could not list indexed ignore files ({})".format(listing.err))
    if listing.rc != 0:
        raise RuntimeError("git ls-files failed (rc={}): {}".format(listing.rc, listing.err))
    requested = set(gitignore_relpaths)
    unavailable = []
    for entry in os.fsdecode(listing.out).split("\0"):
        if not entry:
            continue
        meta, tab, path = entry.partition("\t")
        fields = meta.split()
        if not tab or len(fields) != 4:
            raise RuntimeError("unparseable ls-files entry: {!r}".format(entry))
        tag, _mode, oid, stage = fields
        if path not in requested:
            continue  # a --literal-pathspecs listing still matches DESCENDANTS of a candidate directory
                      # (e.g. ".gitignore/data" under candidate ".gitignore"), and a tree / sparse-dir entry
                      # (trailing "/"); only an entry whose path IS a requested candidate is a .gitignore git
                      # reads, so exact membership drops both (no false refusal)
        # No mode gate: git's read_skip_worktree_file_from_index -> do_read_blob is MODE-BLIND (it reads the
        # entry's OID and applies the object as ignore patterns whenever it is a blob, never consulting the
        # index mode), so a mode allowlist would false-pass a fabricated gitlink-mode (160000) entry whose
        # OID is a promisor-fetchable blob that `git add` fetches and ignores the store with. Refusing on an
        # unavailable OID regardless of mode is git-faithful; see the docstring for the disclosed residual.
        if stage != "0" or tag != "S":
            continue  # git index-reads only a stage-0 skip-worktree entry; nothing else can fall back
        if _worktree_open_succeeds(store_root / path):
            continue  # git's O_RDONLY|O_NOFOLLOW open succeeds, so git reads from the descriptor (or its
                      # read fails, for a directory) and never index-reads: an absent OID cannot change
                      # what `git add` ignores (a readable regular file is parity with the probe)
        # git's open is unsuccessful (absent, symlink, unreadable regular file, or unreadable directory), so
        # git falls back to the index OID (mode-blind); an unavailable OID is a cannot-evaluate the caller
        # refuses. cat-file -e forces no lazy fetch via _run_git_config_discovery, so it never fetches here.
        avail = _run_git_config_discovery(git, store_root, ["cat-file", "-e", oid])
        if not avail.completed:
            raise RuntimeError("could not probe ignore-OID availability ({})".format(avail.err))
        if avail.rc != 0:  # genuinely absent OR an unreadable pack: both refuse (not raise), fail-closed
            unavailable.append(path)
    return sorted(set(unavailable))


def _is_no_repo(outcome):
    """True when a completed git call failed specifically because there is no repository (git's own
    `fatal: not a git repository` on stderr), the CLEAN not-a-repo case the caller maps to `untracked`,
    distinct from a genuine git error the caller must omit-plus-note."""
    return "not a git repository" in outcome.err.lower()


def _observe_tracked(git, store_root, machine_rel, notes):
    """`tracked`: whether the store manifest is under version control. Returns "tracked", "untracked", or
    None (omit + note). A repository with the manifest in the index is "tracked"; a repository without it,
    or no repository at all (the clean not-a-repo case), is "untracked"; any OTHER git error omits the key
    and records a note, so an unverifiable state is never reported as a definite "untracked"."""
    out = _run_git(git, store_root, ["rev-parse", "--is-inside-work-tree"])
    if not out.completed:
        notes.append("tracked: could not run git to determine version-control tracking ({}); "
                     "the tracked observation is omitted".format(out.err.strip()))
        return None
    if out.rc != 0:
        if _is_no_repo(out):
            return "untracked"     # no repository at all: the manifest is not version-controlled here
        notes.append("tracked: git could not determine whether the store is a work tree (rc {}); "
                     "the tracked observation is omitted".format(out.rc))
        return None
    if out.out.strip() != b"true":
        # A bare repository or a path inside the git directory has no work tree here: nothing tracked.
        return "untracked"
    pathspec = "{}/{}".format(machine_rel, _MANIFEST_NAME)
    ls = _run_git(git, store_root, ["ls-files", "--", pathspec])
    if not ls.completed:
        notes.append("tracked: could not run git ls-files ({}); the tracked observation is omitted".format(
            ls.err.strip()))
        return None
    if ls.rc != 0:
        notes.append("tracked: git ls-files failed (rc {}); the tracked observation is omitted".format(ls.rc))
        return None
    return "tracked" if ls.out.strip() else "untracked"


def _observe_remote(git, store_root, notes):
    """`actual_remote`: the store repository's real push URL. Returns the URL string, "" (an OBSERVED
    no-remote, which C-SYNC-AGREE needs as a present empty string), or None (omit + note). Zero remotes is
    the empty string; exactly one remote is its push URL; MORE than one omits the key (which remote is
    authoritative is not git's to guess); any git error omits the key with a note."""
    listing = _run_git(git, store_root, ["remote"])
    if not listing.completed:
        notes.append("actual_remote: could not run git remote ({}); the remote observation is omitted".format(
            listing.err.strip()))
        return None
    if listing.rc != 0:
        notes.append("actual_remote: git remote failed (rc {}); the remote observation is omitted".format(
            listing.rc))
        return None
    names = [ln.strip() for ln in listing.out.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not names:
        return ""     # observed no-remote: a present empty string, not an omission
    if len(names) > 1:
        notes.append("actual_remote: the store repository declares {} remotes ({}); the remote observation "
                     "is omitted (which remote is authoritative is not git's to determine)".format(
                         len(names), ", ".join(sorted(names))))
        return None
    url = _run_git(git, store_root, ["remote", "get-url", "--push", names[0]])
    if not url.completed:
        notes.append("actual_remote: could not read the push URL of remote {!r} ({}); the remote "
                     "observation is omitted".format(names[0], url.err.strip()))
        return None
    if url.rc != 0:
        notes.append("actual_remote: git could not read the push URL of remote {!r} (rc {}); the remote "
                     "observation is omitted".format(names[0], url.rc))
        return None
    return url.out.decode("utf-8", "replace").strip()


def _show_toml(git, store_root, prefix, relpath, disp, notes):
    """Read and parse one committed TOML file from HEAD via `git show HEAD:<prefix><relpath>`. `prefix` is
    the store root's path from the repository toplevel (from `git rev-parse --show-prefix`), so the object
    path is unambiguous whether or not the store sits at the toplevel. Returns the parsed dict, or None (omit
    prior + note) on any git error, absent object, or parse failure. A bare ValueError from tomllib (an
    oversized base-10 integer literal, which is not a TOMLDecodeError) is caught too, so a hostile committed
    file fails SAFE rather than escaping (guard-input-soundness)."""
    out = _run_git(git, store_root, ["show", "HEAD:{}{}".format(prefix, relpath)])
    if not out.completed:
        notes.append("prior: could not read {} at HEAD ({}); the prior committed snapshot is omitted".format(
            disp, out.err.strip()))
        return None
    if out.rc != 0:
        notes.append("prior: {} is absent or unreadable at HEAD (git show rc {}); the prior committed "
                     "snapshot is omitted".format(disp, out.rc))
        return None
    try:
        data = tomllib.loads(out.out.decode("utf-8"))
    except (tomllib.TOMLDecodeError, ValueError, UnicodeDecodeError) as exc:
        notes.append("prior: {} at HEAD is not valid TOML ({}); the prior committed snapshot is "
                     "omitted".format(disp, exc))
        return None
    if not isinstance(data, dict):
        notes.append("prior: {} at HEAD is not a table; the prior committed snapshot is omitted".format(disp))
        return None
    return data


def _reconstruct_prior(git, store_root, machine_rel, prefix, notes):
    """Reconstruct the prior committed snapshot from HEAD, shaped exactly as _opf_check._normalize_prior
    accepts: {releases, counters_high, records, digests}. Reads the HEAD manifest to learn the layout and the
    declared types, then the version and counters ledgers and each declared non-worklog type index. Records
    and digests are built per layout: an INLINE index row IS the record body, so its status is the body's and
    its digest is _opf_check._record_digest(body); a PER-RECORD index row already carries {id, state, digest},
    so the row supplies both directly. ANY absent object or malformed shape omits the WHOLE prior (a partial
    prior would falsely accuse the store); the note names what could not be read. Returns the dict or None."""
    manifest = _show_toml(git, store_root, prefix, "{}/{}".format(machine_rel, _MANIFEST_NAME),
                         _MANIFEST_NAME, notes)
    if manifest is None:
        return None
    # The prior committed snapshot may predate the OPFiles base-table rename: during `opf upgrade` the
    # working tree is already [opf] while HEAD is still the legacy [devprocess] store. Read the base under
    # the current token, falling back to the retired one, so an across-time comparison against a legacy
    # prior reconstructs (spec 9.2) instead of falsely omitting the whole prior.
    base = manifest.get(_opf_store.STANDARD_TOKEN)
    if not isinstance(base, dict):
        base = manifest.get(_opf_store.PRIOR_STANDARD_TOKEN)
    layout = base.get("layout") if isinstance(base, dict) else None
    if layout not in _LAYOUTS:
        notes.append("prior: HEAD manifest declares layout {!r}, which is not a recognized store layout; "
                     "the prior committed snapshot is omitted".format(layout))
        return None
    types_tbl = manifest.get("types")
    if not isinstance(types_tbl, dict):
        notes.append("prior: HEAD manifest carries no [types] table; the prior committed snapshot is omitted")
        return None

    version = _show_toml(git, store_root, prefix, "{}/{}".format(machine_rel, _VERSION_NAME),
                        _VERSION_NAME, notes)
    if version is None:
        return None
    releases = version.get("release", [])
    if not isinstance(releases, list):
        notes.append("prior: HEAD version.toml [[release]] is not an array; the prior committed snapshot is "
                     "omitted")
        return None

    counters = _show_toml(git, store_root, prefix, "{}/{}".format(machine_rel, _COUNTERS_NAME),
                         _COUNTERS_NAME, notes)
    if counters is None:
        return None
    counters_high = counters.get("counters", {})
    if not isinstance(counters_high, dict):
        notes.append("prior: HEAD counters.toml [counters] is not a table; the prior committed snapshot is "
                     "omitted")
        return None

    records = {}
    digests = {}
    for tname in sorted(types_tbl):
        if tname == "worklog":
            continue      # the worklog is a ledger (worklog.toml), never a type index; it carries no records
        idx_name = "{}{}".format(tname, _INDEX_SUFFIX)
        idx = _show_toml(git, store_root, prefix, "{}/{}".format(machine_rel, idx_name), idx_name, notes)
        if idx is None:
            return None
        rows = idx.get("record", [])
        if not isinstance(rows, list):
            notes.append("prior: HEAD {} [[record]] is not an array; the prior committed snapshot is "
                         "omitted".format(idx_name))
            return None
        for row in rows:
            if not isinstance(row, dict):
                notes.append("prior: HEAD {} carries a non-table record row; the prior committed snapshot "
                             "is omitted".format(idx_name))
                return None
            if layout == "inline":
                rid = row.get("id")
                status = row.get("status")
                if not (isinstance(rid, str) and isinstance(status, str)):
                    notes.append("prior: HEAD {} carries an inline record with no string id/status; the "
                                 "prior committed snapshot is omitted".format(idx_name))
                    return None
                try:
                    digest = _opf_check._record_digest(row)
                except _opf_emit.EmitError as exc:
                    notes.append("prior: cannot canonicalize the body of record {!r} to compute its digest "
                                 "({}); the prior committed snapshot is omitted".format(rid, exc))
                    return None
                records[rid] = (tname, status)
                digests[rid] = digest
            else:   # per-record: the registry row itself carries {id, state, path, digest}
                rid = row.get("id")
                state = row.get("state")
                digest = row.get("digest")
                if not (isinstance(rid, str) and isinstance(state, str) and isinstance(digest, str)):
                    notes.append("prior: HEAD {} carries a per-record row with no string id/state/digest; "
                                 "the prior committed snapshot is omitted".format(idx_name))
                    return None
                records[rid] = (tname, state)
                digests[rid] = digest
    return {"releases": releases, "counters_high": counters_high, "records": records, "digests": digests}


def _observe_prior(git, store_root, machine_rel, notes):
    """`prior`: the prior committed snapshot, reconstructed from HEAD. Returns the snapshot dict, the honest
    empty prior for an unborn HEAD (a repository with no commits), or None (omit + note) when the store root
    is not a work tree, HEAD cannot be resolved, or any part of the reconstruction fails."""
    tree = _run_git(git, store_root, ["rev-parse", "--is-inside-work-tree"])
    if not tree.completed:
        notes.append("prior: could not run git ({}); the prior committed snapshot is omitted".format(
            tree.err.strip()))
        return None
    if tree.rc != 0 or tree.out.strip() != b"true":
        notes.append("prior: the store root is not a git work tree; the prior committed snapshot is omitted")
        return None
    head = _run_git(git, store_root, ["rev-parse", "--verify", "--quiet", "HEAD"])
    if not head.completed:
        notes.append("prior: could not resolve HEAD ({}); the prior committed snapshot is omitted".format(
            head.err.strip()))
        return None
    if head.rc == 1:
        # --verify --quiet returns 1 when HEAD names no commit: an unborn HEAD. The honest empty prior.
        return _empty_prior()
    if head.rc != 0:
        notes.append("prior: git could not verify HEAD (rc {}); the prior committed snapshot is "
                     "omitted".format(head.rc))
        return None
    pfx = _run_git(git, store_root, ["rev-parse", "--show-prefix"])
    if not pfx.completed or pfx.rc != 0:
        notes.append("prior: could not determine the store's path within the repository; the prior "
                     "committed snapshot is omitted")
        return None
    prefix = pfx.out.decode("utf-8", "replace").strip()   # "" at the toplevel, else "<dir>/" with a trailing /
    return _reconstruct_prior(git, store_root, machine_rel, prefix, notes)


def gather(resolution):
    """Gather the inert, git-derived observations for a RESOLVED store. Returns `(observations, notes)`:
    `observations` is a dict carrying ONLY the keys honestly determined (a subset of
    _opf_check._OBSERVATION_KEYS: "tracked", "actual_remote", "prior"), suitable to pass straight to
    `_opf_check.validate_store(..., observations=...)`; `notes` explains every omission. Never forges a value:
    when git cannot answer, the key is omitted and a note is added, and the engine routes the affected check
    to a named cannot-evaluate."""
    observations = {}
    notes = []
    store_root = getattr(resolution, "store_root", None)
    machine_rel = getattr(resolution, "machine_rel", None)
    if store_root is None or not isinstance(machine_rel, str) or not machine_rel:
        notes.append("observations: the resolution carries no store root or machine-store path; no "
                     "git-derived observation could be gathered")
        return observations, notes
    git = _git_path()
    if git is None:
        notes.append("observations: git was not found on PATH; the tracked, remote, and prior-snapshot "
                     "observations are all omitted (each affected check routes to a named cannot-evaluate)")
        return observations, notes
    store_root = str(store_root)
    tracked = _observe_tracked(git, store_root, machine_rel, notes)
    if tracked is not None:
        observations["tracked"] = tracked
    remote = _observe_remote(git, store_root, notes)
    if remote is not None:
        observations["actual_remote"] = remote
    prior = _observe_prior(git, store_root, machine_rel, notes)
    if prior is not None:
        observations["prior"] = prior
    return observations, notes


# --- self-test ---------------------------------------------------------------------------------------

class _Res:
    """A minimal stand-in for a resolver Resolution: gather reads only store_root and machine_rel."""
    __slots__ = ("store_root", "machine_rel")

    def __init__(self, store_root, machine_rel):
        self.store_root = store_root
        self.machine_rel = machine_rel


def self_test():
    """Exercise gather over synthetic git repositories built in a tempdir (removed in a finally, for
    test-hermeticity). Covered: tracked vs untracked manifest; zero / one / multiple remotes; a prior
    extracted from a committed HEAD (records and the immutable-body digest matching the engine's own
    _record_digest over the same committed content); an unborn HEAD -> the honest empty prior; and a
    non-repository directory -> the remote and prior fields OMITTED with a note while tracked reads
    "untracked". Judged ONLY on gather's RETURNED structure (never by grepping git's stdout for verdicts).
    Returns 0 clean, 1 on a failed check, 2 on a harness error. When git is not on PATH at all it SKIPs
    clean, like the POSIX-signal watchdogs, since every path depends on git."""
    import shutil as _shutil
    import tempfile as _tempfile

    git = _git_path()
    if git is None:
        print("opf observe self-test: SKIP (git not found on PATH)")
        return 0

    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)

    machine_rel = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)

    # A git environment for the FIXTURE setup calls: config-neutralized (no host identity or hooks), with a
    # committer identity supplied per-call via -c so a commit needs no ambient config.
    def _setup_env(home):
        env = _scrubbed_env()
        env["HOME"] = str(home)
        return env

    def _git_setup(cwd, home, *args):
        """Run a git FIXTURE command, raising OSError (mapped to a harness error, exit 2) on failure or
        timeout. --no-replace-objects so a replacement ref cannot substitute the bytes a git data command
        reads; a bounded timeout so a hung fixture call fails SAFE rather than hangs (mirrors _run_git)."""
        cmd = [git, "--no-replace-objects", "-C", str(cwd),
               "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"] + list(args)
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=_setup_env(home), timeout=_GIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise OSError("git {} timed out after {}s".format(" ".join(args), _GIT_TIMEOUT_S))
        if proc.returncode != 0:
            raise OSError("git {} failed (rc {}): {}".format(
                " ".join(args), proc.returncode, (proc.stderr or b"").decode("utf-8", "replace").strip()))

    def _write(root, relpath, text):
        p = Path(root) / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    # A minimal-but-shaped inline machine store: enough for the prior reconstruction to populate records,
    # digests, releases, and counters. It need not be validate_store-VALID; the reconstruction is what is
    # under test. Types are the three the fixtures reference; each declared non-worklog type has an index.
    def _write_store(root):
        base = "{}/".format(machine_rel)
        _write(root, base + _MANIFEST_NAME,
               '[opf]\n'
               'standard = "opf"\n'
               'spec_version = "1.0.0"\n'
               'layout = "inline"\n'
               'posture = "required"\n'
               'import_status = "none"\n\n'
               '[store]\n'
               'sync_target = ""\n\n'
               '[types.backlog_item]\nnamespace = "BI"\n'
               '[types.done]\nnamespace = "DN"\n'
               '[types.worklog]\nnamespace = "WL"\n\n'
               '[vendors]\nregistered = []\n')
        _write(root, base + _VERSION_NAME,
               'schema = 1\n\n[[release]]\n'
               'version = "0.1.0"\n'
               'date = "2026-01-01T00:00:00Z"\n'
               'worklog_span = []\n'
               'coverage_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"\n')
        _write(root, base + _COUNTERS_NAME,
               'schema = 1\n\n[counters]\nBI = 1\nDN = 1\nWL = 0\n')
        _write(root, base + "backlog_item" + _INDEX_SUFFIX,
               'schema = 1\n\n[[record]]\n'
               'id = "BI-1"\n'
               'type = "backlog_item"\n'
               'status = "open"\n'
               'title = "t"\n'
               'created_at = "2026-01-01T00:00:00Z"\n'
               'updated_at = "2026-01-01T00:00:00Z"\n'
               '[record.actor]\nkind = "maintainer"\n')
        _write(root, base + "done" + _INDEX_SUFFIX,
               'schema = 1\n\n[[record]]\n'
               'id = "DN-1"\n'
               'type = "done"\n'
               'status = "recorded"\n'
               'title = "t"\n'
               'created_at = "2026-01-01T00:00:00Z"\n'
               'updated_at = "2026-01-01T00:00:00Z"\n'
               '[record.actor]\nkind = "maintainer"\n')
        _write(root, base + _WORKLOG_NAME, 'schema = 1\n')

    base = Path(_tempfile.mkdtemp(prefix="opf-observe-selftest-")).resolve()
    try:
        home = base / "home"
        home.mkdir()

        # 1. A committed store: manifest tracked, zero remotes -> "", and a reconstructable prior.
        tracked_repo = base / "tracked"
        tracked_repo.mkdir()
        _git_setup(tracked_repo, home, "init")
        _write_store(tracked_repo)
        _git_setup(tracked_repo, home, "add", "-A")
        _git_setup(tracked_repo, home, "commit", "-m", "seed store")
        obs, notes = gather(_Res(tracked_repo, machine_rel))
        check("tracked-manifest", obs.get("tracked") == "tracked")
        check("no-remote-empty-string", obs.get("actual_remote") == "")
        prior = obs.get("prior")
        check("prior-present", isinstance(prior, dict))
        if isinstance(prior, dict):
            check("prior-records", prior.get("records", {}).get("BI-1") == ("backlog_item", "open")
                  and prior.get("records", {}).get("DN-1") == ("done", "recorded"))
            check("prior-releases", isinstance(prior.get("releases"), list) and len(prior["releases"]) == 1)
            check("prior-counters", prior.get("counters_high", {}).get("BI") == 1)
            # Digest honesty: gather's digest for the immutable done record must equal the engine's own
            # _record_digest over the SAME committed body (parsed here independently from the working tree).
            with open(str(tracked_repo / machine_rel / ("done" + _INDEX_SUFFIX)), "rb") as fh:
                done_idx = tomllib.loads(fh.read().decode("utf-8"))
            expected = _opf_check._record_digest(done_idx["record"][0])
            check("prior-digest-matches-engine", prior.get("digests", {}).get("DN-1") == expected)

        # 2. A repository with the manifest present on disk but NOT committed -> "untracked".
        untracked_repo = base / "untracked"
        untracked_repo.mkdir()
        _git_setup(untracked_repo, home, "init")
        _write(untracked_repo, "README", "seed\n")
        _git_setup(untracked_repo, home, "add", "README")
        _git_setup(untracked_repo, home, "commit", "-m", "seed")
        _write_store(untracked_repo)     # written but never added: not under version control
        obs, notes = gather(_Res(untracked_repo, machine_rel))
        check("untracked-manifest", obs.get("tracked") == "untracked")

        # 3. Exactly one remote -> its push URL.
        one_remote = base / "one-remote"
        one_remote.mkdir()
        _git_setup(one_remote, home, "init")
        _git_setup(one_remote, home, "remote", "add", "origin", "https://example.invalid/store.git")
        obs, notes = gather(_Res(one_remote, machine_rel))
        check("one-remote-url", obs.get("actual_remote") == "https://example.invalid/store.git")

        # 4. Multiple remotes -> the remote field OMITTED with a note.
        multi_remote = base / "multi-remote"
        multi_remote.mkdir()
        _git_setup(multi_remote, home, "init")
        _git_setup(multi_remote, home, "remote", "add", "origin", "https://example.invalid/a.git")
        _git_setup(multi_remote, home, "remote", "add", "mirror", "https://example.invalid/b.git")
        obs, notes = gather(_Res(multi_remote, machine_rel))
        check("multi-remote-omitted", "actual_remote" not in obs)
        check("multi-remote-note", any("remote" in n and "omitted" in n for n in notes))

        # 5. An unborn HEAD (init, no commit) -> the honest empty prior.
        unborn = base / "unborn"
        unborn.mkdir()
        _git_setup(unborn, home, "init")
        _write_store(unborn)     # on disk, uncommitted
        obs, notes = gather(_Res(unborn, machine_rel))
        check("unborn-empty-prior", obs.get("prior") == _empty_prior())
        check("unborn-untracked", obs.get("tracked") == "untracked")

        # 6. A non-repository directory -> remote and prior OMITTED with a note; tracked reads "untracked".
        non_repo = base / "non-repo"
        non_repo.mkdir()
        _write_store(non_repo)
        obs, notes = gather(_Res(non_repo, machine_rel))
        check("non-repo-tracked-untracked", obs.get("tracked") == "untracked")
        check("non-repo-remote-omitted", "actual_remote" not in obs)
        check("non-repo-prior-omitted", "prior" not in obs)
        check("non-repo-notes", any("omitted" in n for n in notes))
    except OSError as exc:
        print("opf observe self-test: harness error: could not build a git fixture ({}); fail-closed".format(
            exc), file=sys.stderr)
        return 2
    finally:
        _shutil.rmtree(str(base), ignore_errors=True)

    if failures:
        for f in failures:
            print("opf observe self-test: FAIL: {}".format(f), file=sys.stderr)
        return 1
    print("opf observe self-test: PASS (tracked/untracked; 0/1/multiple remotes; prior from a committed "
          "HEAD with an engine-matching immutable-body digest; unborn HEAD -> empty prior; a non-repo dir "
          "omits remote and prior with a note)")
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
