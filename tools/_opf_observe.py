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
    the locale pinned so output is deterministic."""
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


def _run_git(git, store_root, args, timeout=_GIT_TIMEOUT_S):
    """Run `git --no-replace-objects -C <store_root> <args>` under the scrubbed environment, bounded by a
    timeout. Returns a _GitOutcome: `completed` is True only when the process ran to completion (then `rc`,
    `out` (bytes), and `err` (text) are meaningful); it is False on a timeout or an OS launch failure, with
    `err` naming the reason -- the fail-safe omit-plus-note signal. `--no-replace-objects` is passed so a
    replacement ref cannot substitute the bytes a read returns; `-C` binds the call explicitly to the store
    root rather than inheriting a cwd (explicit-binding-over-ambient-context)."""
    cmd = [git, "--no-replace-objects", "-C", str(store_root)] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=_scrubbed_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return _GitOutcome(False, None, b"", "git timed out after {}s".format(timeout))
    except OSError as exc:
        return _GitOutcome(False, None, b"", "could not launch git ({})".format(exc))
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return _GitOutcome(True, proc.returncode, proc.stdout or b"", err)


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
# GIT_CONFIG_PARAMETERS) are deliberately DROPPED, not honoured: they can set ANY config key (core.worktree
# to rebind the tree, trace2.* to make the read-only probe write or append an outside file, core.fsmonitor to
# launch a process), so carrying them would re-open exactly the redirect/trace surface the scrub exists to
# close (SECI-threat-model-boundaries, prefer-removing-a-path). GIT_CONFIG_NOSYSTEM is NOT among the dropped
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
# read-only check-ignore into a file write or a launched process.

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
