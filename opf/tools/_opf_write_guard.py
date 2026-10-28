#!/usr/bin/env python3
"""The pre-mutation guards the in-place OPF writers share: write-scope planning, working-tree cleanliness, and
the single-writer lease (spec 5.7).

`opf record` (spec 8.8) rewrites an in-repo store in place and offers the uncommitted change for the
adopter's own review and merge, so it needs this shell around the mutation: plan the destinations (the
operation's own store files plus every declared view the post-mutation render writes), prove the working
tree is clean over exactly that scope (including ignored files, so HEAD is a verified restore path), and
claim the lease atomically, hold it across mutation, render, and final doctor, and release it only as this
run's own. These helpers were MOVED here from opf.py (they are not a copy); each takes the calling verb
explicitly so its refusals name the operation the operator actually ran.

Stdlib only; the sibling _opf_* helpers are imported directly. Every refusal is a WriteGuardError, which the
calling verb maps to exit 2. Nothing here stages or commits anything. `--self-test` runs the lease message,
porcelain grammar, dirty-probe, lease ownership and filter-neutralization vectors (self_test), which opf.py
--self-test also runs.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_write_guard.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import errno
import os
import re
import socket
import stat
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_check    # noqa: E402
import _opf_emit     # noqa: E402
import _opf_observe  # noqa: E402
import _opf_schema   # noqa: E402
import _opf_store    # noqa: E402
import _opf_views    # noqa: E402


class WriteGuardError(Exception):
    """A fail-closed pre-mutation or lease refusal carrying the operator-facing reason (mapped to exit 2)."""


class LeaseHeldError(WriteGuardError):
    """acquire_lease's held-lease refusal, exactly the EEXIST present-is-held case (spec 5.7). A distinct
    type so the RECOVERY claim (acquire_lease_for_recovery) can apply the spec 5.7 never-seize and
    dead-run release clauses to exactly this case; every other acquisition failure stays a plain
    WriteGuardError and is never examined for release."""


# --- write scope: the operation's store files plus every declared render destination ------------------

def plan_write_scope(machine_rel, manifest_model, store_paths, verb):
    """Plan the destinations of an in-place store mutation and the declared-view render that follows it.
    `store_paths` are the operation's own store-root-relative files; every view the manifest declares adds its
    render destination (store or product scope). Use the POST-mutation manifest where the mutation changes it:
    it names every view the render will write. Unmanaged content is not selected; a declaration colliding
    with a managed destination refuses before mutation, rather than licensing an overwrite. The lease is
    checked separately and is never a restore or staging target."""
    store = set(store_paths)
    product = set()
    for name in (manifest_model.get("views") or {}):
        try:
            _opf_views._resolve_view(name)
        except _opf_views.ViewsError as exc:
            raise WriteGuardError("cannot plan {} render destinations: {}".format(verb, exc)) from exc
        scope, relpath = _opf_views._spec_destination(name)
        (product if scope == "product" else store).add(relpath)
    findings = []
    _opf_store._validate_unmanaged(manifest_model.get("unmanaged"), findings)
    # The SAME single authority the doctor reads (F10-1), which itself derives the imported-series
    # activation from the manifest (spec 8.2/4.2) through the SAME function the doctor driver reads
    # (_opf_check.imported_series_active, keyed on the homes-2 declaration): once activation makes the
    # flag true, an [unmanaged] declaration naming an imported leaf refuses here exactly as the doctor
    # flags it, so the writer and the doctor cannot diverge on the imported managed set.
    classification = _opf_check.classify_containment(manifest_model, machine_rel)
    findings += classification.malformed + classification.colliding
    if findings:
        raise WriteGuardError("cannot plan {} writes: {}".format(verb, "; ".join(findings)))
    return {"store": tuple(sorted(store)), "product": tuple(sorted(product))}


# --- working-tree cleanliness over the planned scope ----------------------------------------------------

# The EXACT set of valid `git status --porcelain=v1 -z --untracked-files=all --ignored=matching --no-renames` XY status PAIRS,
# enumerated precisely from the git-status(1) "Short Format" table for the installed git (git 2.53.0 in this
# env; the table is stable across modern git) and EMPIRICALLY cross-checked by driving real index/worktree
# states and observing the emitted XY. Validating the whole PAIR (not each char independently) closes the
# fail-open where an IMPOSSIBLE pair whose two chars each sit in a per-char set passes a char check and, for
# the lease path, matches the exclusion and is SILENTLY DROPPED -- the M3 cleanliness guard then reads dirt
# as clean. This is the EXACT man-page enumeration, NOT the {space,M,T,A,D} cartesian, which is a strict
# SUPERSET that would admit impossible ordinary pairs: git emits X=D ONLY with a space Y, and Y=A ONLY with a
# space X, so DM DT DA (and MA TA) are unemittable and MUST be refused, not silently accepted as dirt.
# Composition:
#   - "??" untracked and "!!" ignored (--untracked-files=all --ignored=matching are passed).
#   - ORDINARY (non-unmerged) changed entries, per the first table section with rename R and copy C EXCLUDED
#     (--no-renames guarantees git emits neither) and U reserved to the unmerged tier. For each index letter X
#     the EXACT set of worktree letters Y git can pair it with, straight off the man-page rows:
#         X=' ' (index clean):        Y in {A, M, T, D}   (' A' intent-to-add is VALID and accepted)
#         X in {M, T, A}:             Y in {' ', M, T, D}  (staged change, worktree clean/modified/typechg/del)
#         X='D' (deleted from index): Y in {' '}           (a deleted-in-index path pairs ONLY with space)
#   - the seven UNMERGED pairs, verbatim from git-status(1): DD AU UD UA DU AA UU.
# In-scope ignored/untracked destinations are dirt; the exact lease is handled by O_EXCL instead.
# The per-X worktree sets are enumerated here, not the whole valid
# set hardcoded flat, so each line stays auditable against the man-page table. Bytes throughout (2-byte keys).
_PORCELAIN_ORDINARY_YSET = {
    0x20:     b"AMTD",   # X=' ': worktree added(intent-to-add)/modified/type-changed/deleted
    ord("M"): b" MTD",   # X='M' (updated in index): worktree unmodified/modified/type-changed/deleted
    ord("T"): b" MTD",   # X='T' (type changed in index): worktree unmodified/modified/type-changed/deleted
    ord("A"): b" MTD",   # X='A' (added to index): worktree unmodified/modified/type-changed/deleted
    ord("D"): b" ",      # X='D' (deleted from index): worktree unmodified only
}
_PORCELAIN_UNMERGED_PAIRS = (b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU")
_PORCELAIN_VALID_PAIRS = frozenset(
    [b"??", b"!!"]
    + [bytes((x, y)) for x, ys in _PORCELAIN_ORDINARY_YSET.items() for y in ys]
    + list(_PORCELAIN_UNMERGED_PAIRS))


def status_path(pbytes, prefix):
    """Normalize a repository-relative status path without losing filesystem bytes.

    A path that cannot be normalized is refusing dirt, never an out-of-scope ignored record.
    The same normalization serves scope filtering and collapsed-ancestor expansion.
    """
    prefix_b = os.fsencode(prefix)
    if prefix_b:
        if not prefix_b.endswith(b"/") or not pbytes.startswith(prefix_b):
            raise WriteGuardError("git status returned a path outside the reported repository prefix; "
                                  "the store cleanliness cannot be verified (fail-closed)")
        pbytes = pbytes[len(prefix_b):]
    # Git emits normalized relative paths; only directory records may have a final slash.
    if any(part in (b"", b".", b"..") for part in pbytes.removesuffix(b"/").split(b"/")):
        raise WriteGuardError("git status returned a path that cannot be normalized; "
                              "the store cleanliness cannot be verified (fail-closed)")
    return pbytes


def parse_porcelain(raw, prefix, lease_excl, pathspecs, verb):
    """Parse a `git status --porcelain=v1 -z --untracked-files=all --ignored=matching --no-renames` payload into the list of
    dirty paths, each normalized `root`-relative (the `prefix`, the store's repo-root-relative path with a
    trailing '/', is stripped from every repository-root-relative porcelain path) and EXCLUDING `lease_excl`
    (only its untracked/ignored record). Ignored paths must be within a component-bounded pathspec;
    malformed records and other statuses are never filtered out. Factored PURE so the refusal is unit-
    testable. The -z grammar is VALIDATED (guard-input-soundness): a non-empty payload is a run of
    NUL-TERMINATED records, each `XY<space>PATH` (two status chars, a space, then >=1 path byte); --no-renames
    means there is no second NUL-separated origin-path field. A payload that is not NUL-terminated, that
    carries a record shorter than `XY PATH` or lacking the status/space framing, or whose XY status is
    outside the porcelain v1 vocabulary (a bogus pair, a blank pair, or a rename/copy the --no-renames probe
    cannot emit), is MALFORMED and refuses fail-closed -- never a clean empty result on an unparseable
    payload (e.g. a lone NUL, which a naive split would read as clean), and never a silent lease-exclusion
    drop of a malformed-status record (check-fails-closed-on-unreadable)."""
    if not raw:
        return []
    parts = raw.split(b"\x00")
    if parts[-1] != b"":
        raise WriteGuardError("git status returned a porcelain payload that is not NUL-terminated; the store "
                              "cleanliness cannot be verified (fail-closed)")
    lease_b = os.fsencode(lease_excl) if lease_excl is not None else None
    specs_b = [os.fsencode(p) for p in pathspecs]
    dirty = []
    for rec in parts[:-1]:
        # porcelain v1 -z: two status chars, a space, then the path bytes (verbatim under -z, no quoting).
        if len(rec) < 4 or rec[2:3] != b" ":
            raise WriteGuardError("git status returned a malformed porcelain record ({!r}); the store "
                                  "cleanliness cannot be verified (fail-closed)".format(rec[:16]))
        # Validate the whole XY status PAIR against the enumerated valid-pair set BEFORE the lease exclusion
        # below (guard-input-soundness): a per-CHAR check accepts an IMPOSSIBLE pair (UT, ZZ, a blank pair, a
        # rename R, a copy C) whose chars each sit in a per-char set, and for the lease path that bogus record
        # would match the exclusion and be SILENTLY DROPPED -- a fail-open in the M3 cleanliness guard. The
        # pair is validated whole against _PORCELAIN_VALID_PAIRS (?? and !! plus the EXACT man-page ordinary
        # enumeration, plus the seven unmerged pairs; rename, copy, an impossible ordinary pair such as DM
        # or MA, and any U in a non-unmerged position all excluded).
        # Anything outside that set is MALFORMED -> fail-closed, never a drop.
        if rec[:2] not in _PORCELAIN_VALID_PAIRS:
            raise WriteGuardError("git status returned a porcelain record with an out-of-vocabulary "
                                  "status pair ({!r}); the store cleanliness cannot be verified "
                                  "(fail-closed)".format(rec[:16]))
        pbytes = status_path(rec[3:], prefix)
        if (lease_b is not None and rec[:2] in (b"??", b"!!")
                and (pbytes == lease_b or pbytes.startswith(lease_b + b"/"))):
            # A directory at lease.toml is also present-is-held. Leave it to the lease's never-seize refusal.
            continue
        if lease_b is not None and (pbytes == lease_b or pbytes.startswith(lease_b + b"/")):
            # An untracked or ignored lease is the legitimate held-lease case
            # that lease acquisition handles as its never-seize refusal, so it is EXCLUDED here. Any OTHER
            # (tracked) status on the lease path -- " D", "D ", " M", "MM", ... -- means the lease is COMMITTED
            # or otherwise version-controlled, which VIOLATES spec 5.7 (a lease is present only while held): the
            # store is anomalous. That is REFUSED fail-closed and NAMED DISTINCTLY here, never silently
            # excluded. A silent drop of a " D" (a committed lease deleted in the worktree) would let the
            # O_EXCL acquire succeed on the now-absent file and sweep the tracked lease's DELETION into the
            # operation's uncommitted change set (outside its allowed delta). Recovery and staging advice
            # therefore also exclude the lease; no restore may resurrect one from HEAD.
            raise WriteGuardError(
                "the single-writer lease {!r} is TRACKED in git (porcelain status {!r}, neither "
                "untracked '??' nor ignored '!!'); a committed or otherwise version-controlled lease violates spec 5.7 (a lease is "
                "present only while held) and leaves the store in an anomalous state. Reconcile the store "
                "(remove the lease from version control) before re-running opf {} (fail-closed)".format(
                    lease_excl, rec[:2].decode("ascii", "replace"), verb))
        # --ignored=matching can report prefix siblings even with literal pathspecs. Drop ONLY
        # well-formed ignored records outside the component-bounded scope; other statuses stay fail-closed.
        if rec[:2] == b"!!" and not any(
                pbytes == p or pbytes == p + b"/" or pbytes.startswith(p + b"/") for p in specs_b):
            continue
        dirty.append(os.fsdecode(pbytes))
    return dirty


def probe_dirty(git, root, pathspecs, lease_excl, verb):
    """Run a hardened `git status --porcelain=v1 -z --untracked-files=all --ignored=matching --no-renames` over `pathspecs`
    beneath `root`, returning the list of dirty paths, each normalized to `root`-relative (excluding
    `lease_excl`, a byte-literal store-relative path, when given). The porcelain paths are REPOSITORY-root-
    relative, so the store's path within the repository (git rev-parse --show-prefix) is stripped, which is
    what lets `lease_excl` be excluded even when the store root lies BELOW the git repository root (a NESTED
    store, where the un-normalized compare missed and drew the dirty-store refusal in place of the
    held-lease message). Reuses _opf_observe's scrubbed-env, --no-replace-objects, -C-bound, timeout-
    bounded boundary (G6). Fail-closed: a non-completed probe, a not-a-repository, a nonzero exit, an
    undeterminable store prefix, or a malformed payload refuses; never a clean pass on an unreadable probe
    (check-fails-closed-on-unreadable, SECA-verified-restore-path).

    This is the ONE caller that COMPARES WORKTREE CONTENT against the index (the operation that makes git run
    a repo/worktree-configured clean/process filter), so it passes _filter_neutralizing_config to _run_git as
    config_overrides: git EXECUTES such a filter's external command during this read-only observation, an
    exec-on-observe vector of the executable-config-trust-gate class (SECI-config-is-executable-trust-gate,
    OPF-STATUS-FILTER-SUPPRESS) that OPF-FSMON-SUPPRESS closed for core.fsmonitor and GIT_NO_LAZY_FETCH closed
    for the lazy-fetch->core.sshCommand vector. The overrides are injected as SEPARATE GIT_CONFIG_KEY/VALUE
    env strings (never `-c key=value` argv, whose first-`=` split a subsection name containing `=` would
    exploit to leave the real driver executable, F-OPF-STATUSFILTER-EQ-BYPASS). Both check_clean call
    sites (store and product_root) inherit the neutralization through this single narrow call. Any FUTURE
    worktree-content observation (a non-`--cached` diff, diff-files, ls-files -m, update-index --refresh, a
    status elsewhere) must pass _filter_neutralizing_config the same way; the other observe verbs read no
    worktree content and do not.

    RESIDUAL (disclose-guard-residuals, F-OPF-STATUSFILTER-LFS-FALSEPOS): neutralizing an EXTERNAL NORMALIZING
    clean/process filter (the git-lfs shape, index=cleaned/pointer blob, worktree=smudged body, required=true)
    stops git reproducing the cleaned blob, so in the racy-clean mtime window (normal post-add/checkout/clone
    state) status re-hashes the raw worktree bytes, which differ from the index blob, and a genuinely-CLEAN
    store reads DIRTY. This is inherent to driver neutralization and is FAIL-CLOSED (it over-refuses the
    operation; never a false-clean, never a filter exec). check_clean surfaces it with operator-clear
    guidance to settle the worktree first."""
    try:
        neutralizing = _opf_observe._filter_neutralizing_config(git, root)
    except RuntimeError as exc:
        # The enumeration that proves which clean/process filters git could exec on the status is a guard; an
        # unreadable enumeration is a cannot-evaluate, so the probe refuses rather than run a status that might
        # execute a repo-planted filter (guard-input-soundness, check-fails-closed-on-unreadable).
        raise WriteGuardError("could not verify the store is clean before the {}: its git clean/process "
                              "filter configuration is unreadable ({}), so a read-only status probe cannot run "
                              "without risking filter execution; refusing the destructive rewrite "
                              "(fail-closed)".format(verb, exc))
    # Disable configured and default global exclude files consistently with check-ignore.
    # Keep working-tree .gitignore and .git/info/exclude; existing ignored content still refuses.
    neutralizing = list(neutralizing) + [("core.excludesFile", os.devnull)]
    args = (["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--ignored=matching", "--no-renames", "--"] + list(pathspecs))
    out = _opf_observe._run_git(git, root, args, config_overrides=neutralizing)
    if not out.completed:
        raise WriteGuardError("could not verify the store is clean before the {} ({}); refusing the "
                              "destructive rewrite (fail-closed)".format(verb, out.err.strip()))
    if out.rc != 0 or out.err:
        if _opf_observe._is_no_repo(out):
            raise WriteGuardError("the store at {!r} is not a git repository, so HEAD is not a verified "
                                  "restore path for the in-place rewrite (spec 5.1); refusing "
                                  "(fail-closed)".format(str(root)))
        raise WriteGuardError("git could not verify the store is clean (rc {}); refusing the destructive "
                              "rewrite (fail-closed)".format(out.rc))
    pfx = _opf_observe._run_git(git, root, ["rev-parse", "--show-prefix"])
    if not pfx.completed or pfx.rc != 0 or pfx.err:
        raise WriteGuardError("could not determine the store's path within its git repository; without it a "
                              "nested store's clean probe cannot be trusted, so the rewrite is refused "
                              "(fail-closed)")
    # Strip ONLY the trailing newline git appends, NEVER leading whitespace: a store dir whose name begins
    # with a space (" leading/") would lose that space under .strip(), breaking the prefix match and the
    # lease exclusion. "" at the repo toplevel, else "<dir>/" (trailing /).
    # Keep git path bytes intact through prefix stripping, scope and ancestor matching.
    prefix = pfx.out.removesuffix(b"\n")
    # status omits owner edits hidden by these index flags. Refuse even if the flagged
    # entry happens to be clean: this observation cannot establish its worktree state.
    flags = _opf_observe._run_git(
        git, root, ["--literal-pathspecs", "ls-files", "--cached", "-v", "-z", "--"] + list(pathspecs),
        config_overrides=neutralizing)
    if not flags.completed or flags.rc != 0 or flags.err:
        raise WriteGuardError("cannot inspect {} destination index flags (fail-closed)".format(verb))
    if flags.out and not flags.out.endswith(b"\x00"):
        raise WriteGuardError("malformed {} destination index flags (fail-closed)".format(verb))
    for record in flags.out.split(b"\x00")[:-1]:
        if len(record) < 3 or record[1:2] != b" " or record[:1] not in (b"H", b"S", b"h", b"s", b"M", b"m"):
            raise WriteGuardError("malformed {} destination index flags (fail-closed)".format(verb))
        path = status_path(record[2:], b"")
        if record[:1] in (b"M", b"m"):
            raise WriteGuardError(
                "{} destination {!r} is unmerged; resolve the conflict before retrying "
                "(fail-closed)".format(verb, os.fsdecode(path)))
        if record[:1] != b"H":
            raise WriteGuardError(
                "{} destination {!r} has skip-worktree or assume-unchanged set; "
                "clear the flags and reconcile owner edits before retrying (fail-closed)".format(
                    verb, os.fsdecode(path)))
    dirty = parse_porcelain(out.out, prefix, lease_excl, pathspecs, verb)
    # Matching mode collapses ignored ancestors (e.g. !! .working/ for a selected view). Expand
    # those with traditional mode over the SAME destinations, never by adding the ancestor to scope.
    # The first parse validates the entire payload before any record is used here.
    ancestors = []
    for rec in out.out.split(b"\x00")[:-1]:
        p = status_path(rec[3:], prefix)
        if rec[:2] == b"!!" and p.endswith(b"/"):
            ancestors.append(p)
    if any(os.fsencode(spec).startswith(p) for spec in pathspecs for p in ancestors):
        expanded_args = ["--ignored=traditional" if a == "--ignored=matching" else a for a in args]
        expanded = _opf_observe._run_git(git, root, expanded_args, config_overrides=neutralizing)
        if not expanded.completed or expanded.rc != 0 or expanded.err:
            raise WriteGuardError("could not expand ignored ancestor records within the {} scope "
                                  "(fail-closed)".format(verb))
        dirty += parse_porcelain(expanded.out, prefix, lease_excl, pathspecs, verb)
    return dirty


def check_ignored(root, relpaths, verb):
    """Conservatively refuse absent destinations ignored under the scrubbed configuration.
    Status cannot observe an absent entry. Keep config/filter neutralization and literal NUL-framed
    transport. Reads working-tree .gitignore files and .git/info/exclude. Global/system configuration
    is scrubbed; core.excludesFile is overridden with os.devnull, disabling configured exclude files
    (including repository/worktree settings) AND Git's default HOME/XDG global ignore file. --no-index
    omits indexed ignore fallback. This is NOT a prediction of ordinary git add: the explicit,
    path-scoped add -f advice bypasses the omitted rules. Any diagnostic refuses before mutation.
    Configuration or filesystem changes after observation remain outside the single-writer contract.
    """
    if not relpaths:
        return
    git = _opf_observe._git_path()
    if git is None:
        raise WriteGuardError("cannot locate git to check ignored planned destinations (fail-closed)")
    try:
        neutralizing = _opf_observe._filter_neutralizing_config(git, root)
    except RuntimeError as exc:
        raise WriteGuardError("cannot check ignored planned destinations: {} (fail-closed)".format(exc)) from exc
    neutralizing = list(neutralizing) + [("core.excludesFile", os.devnull)]
    # check-ignore rejects --literal-pathspecs. Its stdin entries are literal filenames;
    # "./" also prevents a leading ":" from being parsed as pathspec magic.
    paths = {b"./" + os.fsencode(p) for p in relpaths}
    out = _opf_observe._run_git(
        git, root, ["check-ignore", "--no-index", "-z", "--stdin"],
        config_overrides=neutralizing, input_bytes=b"".join(p + b"\x00" for p in sorted(paths)))
    if not out.completed or out.rc not in (0, 1) or out.err:
        raise WriteGuardError("cannot check ignored planned destinations at {!r}: {} (rc {}; fail-closed)".format(
            str(root), out.err.strip(), out.rc))
    if out.rc == 1 and not out.out:
        return
    matches = out.out.split(b"\x00")
    if (out.rc != 0 or matches[-1] != b"" or len(matches) < 2
            or any(p not in paths for p in matches[:-1])):
        raise WriteGuardError("git check-ignore returned a malformed or inconsistent payload (fail-closed)")
    raise WriteGuardError("ignored planned destinations under {!r}: {}. These paths match working-tree "
                          ".gitignore or .git/info/exclude rules; this conservative check refuses even "
                          "though force-add could stage them. Adjust those rules before re-running "
                          "opf {}; nothing was written.".format(
                              str(root), ", ".join(repr(os.fsdecode(p[2:])) for p in matches[:-1]), verb))


# --- homes-2 gitignore reconciliation: the read-only inspector (spec 4.2; generation-2 init only) ------
# The store-relative control paths this inspector reasons about, derived from the topology constants so
# this reader cannot drift from the layout the validator enforces (single source of truth).
_HOMES_GITIGNORE_REL = "{}/.gitignore".format(_opf_store.WORKING_DIRNAME)
_HOMES_DURABLE_RELS = (_opf_store.IMPORTED_REL, _opf_store.ARCHIVE_REL)    # MUST stay tracked (spec 4.2)
_HOMES_IGNORED_RELS = (_opf_store.STAGING_REL, _opf_store.JOURNALS_REL)    # the managed block ignores these
# The EXACT read-only, index-preserving git verbs the inspection may run: never status (probe_dirty's
# verb, which can refresh the index), add, rm, update-index or reset; both git environments set
# GIT_OPTIONAL_LOCKS=0. Disclosed residual: reading a SPLIT index refreshes the shared index file's
# mtime, which GIT_OPTIONAL_LOCKS does not suppress; the index content is never mutated, and the
# tracked-control-path advice below only NAMES the untracking, it never performs it.
_HOMES_INSPECT_VERBS = frozenset(("rev-parse", "ls-files", "check-ignore", "config", "cat-file"))
# The argument-less git global options a homes argv may carry before its verb. Any other leading
# option is refused: one that takes a separate argument (-C <path>, -c <name=value>) would make a
# first-non-option parse read that argument as the verb and forward a different command to git.
_HOMES_GLOBAL_FLAGS = frozenset(("--literal-pathspecs",))
# config is allowlisted only with one of these read actions (git refuses to combine two actions), named
# where git still parses it as an option (_homes_config_read), beside only _HOMES_CONFIG_FLAGS: argument-less
# options, so no option can take the action token as its argument (`--file --get`).
_HOMES_CONFIG_READS = frozenset(("--get", "--get-all", "--get-regexp"))
_HOMES_CONFIG_FLAGS = frozenset(("-z", "--name-only", "--type=bool"))
# The untracked child every home effectiveness probe asks about. check-ignore answers "not ignored"
# for any path whose pathspec matches an index entry, so probing a home itself reads the tracked
# content already there (durable evidence after the first import, the steady state) as not ignored.
# A child answers for NEW content, and git classifies every home above it as a directory whatever its
# current type, even while absent. An index entry matching a durable probe child is itself refused.
_HOMES_PROBE_CHILD = ".opf-ignore-probe"


def _ignore_file_candidates(prefix, paths):
    """Repo-relative .gitignore paths git consults when deciding whether the planned destinations are
    ignored: one per ancestor directory from the repository root down to each destination's own directory.
    A .gitignore in directory D governs paths under D, so every ancestor directory of a planned path is a
    candidate ignore source (git add reads them all). MOVED here from opf.py (not a copy): opf.py's init
    ignore preflight and the homes gitignore inspection below share this one authority."""
    dirs = set()
    for path in paths:
        for parent in (prefix / path).parents:
            dirs.add(parent)
    candidates = set()
    for directory in dirs:
        posix = directory.as_posix()
        candidates.add(".gitignore" if posix == "." else posix + "/.gitignore")
    return sorted(candidates)


INIT_IGNORED = "ignored"
INIT_IGNORE_UNEVALUATED = "cannot-evaluate"


def init_ignore_refusal(git, repo, prefix, paths):
    """The ONE init ignore preflight (OPF spec 5.1: an untracked or ignored store is a hard failure in
    scaffolding, with no gitignore fallback). MOVED here from opf.py _init_unignored (not a copy): the plain
    `opf init` (opf.py _init_unignored, an adapter) and the init operation (_opf_init_operation
    run_init_operation, which adoption's init-store composes, under its held mutex before its first write)
    both call it. `repo` is the repository toplevel, `prefix` the product root relative to it (a PurePath,
    "." at the toplevel), and `paths` the product-relative destinations the caller CREATES. Returns None when
    no planned destination is ignored and that answer is sound, else (kind, message): INIT_IGNORED for a
    destination git would ignore, INIT_IGNORE_UNEVALUATED for a check that cannot evaluate. Each caller
    refuses on either kind; a cannot-evaluate never reads as not ignored.

    check-ignore reports at least one ignored path with rc 0, none ignored with rc 1, and an error
    with rc >= 128. The rc drives the three-way decision; the output is only for the diagnostic. Paths
    are passed as arguments (git check-ignore accepts neither -z without --stdin, which the run helper has
    no channel for, nor --literal-pathspecs, which it rejects as unsupported magic). Each scoped path is
    given a literal "./" prefix so a leading colon or other pathspec-magic sigil in an adopter-supplied
    --root prefix is read as a path, not as magic: without it a ":name/..." prefix has its colon consumed
    as an empty magic signature and the check silently bypasses (rc 1, the store looks unignored), while a
    recognized short-magic letter over-refuses (rc >= 128). The prefix is a plain string because Path
    would normalize "./x" back to "x". The output is then one path per line over the controlled,
    newline-free internal destinations. Outside a git work tree (no repository, or a bare one) check-ignore
    exits 128, so the check answers cannot-evaluate; both callers already refuse such a root earlier.

    OPF-D2B: the probe runs under _opf_observe._run_git_config_discovery, which lets git DISCOVER the
    adopter's real global and system configuration through its default locations (unlike the default scrubbed
    observer environment, which neutralizes it), so a store ignored ONLY by the adopter's global or system
    core.excludesFile is caught here, matching what the adopter's own `git add` would honour
    (guard-input-soundness). Env-based config overrides are dropped and trace/fsmonitor are forced off, so no
    reachable configuration can turn the read-only probe into a write or a launched process. Lazy fetching is
    forced off so a missing indexed ignore blob cannot trigger a promisor fetch that would reach
    core.sshCommand. DISCLOSED RESIDUAL (disclose-guard-residuals): this rc-only check reads ignore rules
    exactly as git add does when git add can read them too. For an ignore input git cannot READ (a
    permission-denied core.excludesFile, ~/.config/git/ignore, or .git/info/exclude), git add is equally unable
    to read it and stages the destination, so reading not-ignored here matches git add and the store is never
    silently ignored. The one input where that parity BREAKS is a skip-worktree (or otherwise unmaterialized)
    .gitignore whose blob is absent in a partial clone: this probe forces lazy fetch off and reads no-rule, but
    the adopter's own git add fetches the blob and can then ignore the store. That case is NOT left as a
    residual: after an rc-1 result the availability of every applicable indexed .gitignore blob is checked
    (_opf_observe.indexed_ignore_availability), and an unavailable one is a cannot-evaluate that REFUSES rather
    than passing (guard-input-soundness); so is that check failing to run (its RuntimeError). A fail-closed
    treatment of the unreadable-FILE arms above is deliberately not attempted, because git and the caller
    resolve config paths differently and refusing there would over-refuse a case git add itself skips. A
    timeout, launch failure, or unexpected rc fails closed and refuses. Matching `git add`, a benign git
    diagnostic on an rc-1 (not-ignored) result is not itself a refusal.

    rc 1 (not ignored) is sound only when every applicable .gitignore is answerable WITHOUT a promisor fetch.
    In a partial clone a skip-worktree (or otherwise unmaterialized) .gitignore whose blob is absent reads as
    no-rule here (lazy fetch is forced off), yet the adopter's own `git add` fetches that blob and can then
    silently ignore the store; an available blob is read identically by both (parity) and a checked-out
    .gitignore is read from disk by both. So a missing indexed ignore blob is a cannot-evaluate, not a clean
    pass (guard-input-soundness)."""
    scoped = sorted("./" + str(prefix / path) for path in paths)
    result = _opf_observe._run_git_config_discovery(
        git, repo, ["check-ignore", "--"] + scoped)
    if not result.completed:
        return INIT_IGNORE_UNEVALUATED, (
            "git preflight: could not evaluate ignore status for planned destinations ({})".format(
                result.err))
    if result.rc == 0:
        ignored = [p for p in os.fsdecode(result.out).splitlines() if p]
        return INIT_IGNORED, "planned destination is git-ignored: {!r}".format(ignored)
    if result.rc != 1:
        return INIT_IGNORE_UNEVALUATED, "git preflight: check-ignore failed (rc={}): {}".format(
            result.rc, result.err)
    try:
        unavailable = _opf_observe.indexed_ignore_availability(
            git, repo, _ignore_file_candidates(prefix, paths))
    except RuntimeError as exc:
        return INIT_IGNORE_UNEVALUATED, str(exc)
    if unavailable:
        return INIT_IGNORE_UNEVALUATED, (
            "git preflight: an indexed .gitignore blob is unavailable in this partial clone, so ignore "
            "status cannot be determined; `git add` would fetch it and could silently ignore the store "
            "({}). Check out or fetch the blob and retry.".format(sorted(unavailable)))
    return None


def _homes_config_read(options):
    """Whether the tokens after a homes `config` verb form a read. git parses config options only up to
    `--` or the first positional (git 2.53.0: `config --file F -- k --get` and `config -z --file F k
    --get-all` each write the action token as the value), so exactly one _HOMES_CONFIG_READS action
    must come before that point, beside only _HOMES_CONFIG_FLAGS; every later token is then a key or
    value pattern of that read, never an action or a value to write."""
    reads = 0
    for token in options:
        if token == "--" or not token.startswith("-"):
            break
        if token in _HOMES_CONFIG_READS:
            reads += 1
        elif token not in _HOMES_CONFIG_FLAGS:
            return False
    return reads == 1


def _homes_allowlisted_verb(args):
    """The verb of a homes git argv, refused BEFORE launch unless allowlisted. The verb is the token
    after the leading _HOMES_GLOBAL_FLAGS, so any other leading option is itself the refused verb and
    an option argument can never be mistaken for it; config passes only in a read form
    (_homes_config_read)."""
    index = 0
    while index < len(args) and args[index] in _HOMES_GLOBAL_FLAGS:
        index += 1
    head = args[index] if index < len(args) else None
    if head not in _HOMES_INSPECT_VERBS or (
            head == "config" and not _homes_config_read(args[index + 1:])):
        raise WriteGuardError("homes gitignore inspection attempted the non-allowlisted git verb "
                              "{!r} (argv {!r}); refusing (fail-closed)".format(head, list(args)))
    return head


def _homes_checked(head, out, own_stderr):
    """A completed homes git call that wrote to stderr is cannot-evaluate, whatever its rc: git
    reports an unreadable ignore source as a warning and still exits 1 from check-ignore, the
    not-ignored answer, so no answer carrying a diagnostic is read as clean. Only the repository
    probe classifies its own stderr (own_stderr), to name git's not-a-repository refusal, and it
    refuses every diagnostic itself. Each caller still checks the rc against its documented set."""
    if out.completed and out.err and not own_stderr:
        raise WriteGuardError("git {} wrote a diagnostic during the homes gitignore inspection ({}); "
                              "its answer cannot be read as clean; cannot-evaluate "
                              "(fail-closed)".format(head, out.err.strip()))
    return out


def _homes_run_git(git, root, args, own_stderr=False):
    """_opf_observe._run_git behind the structural verb allowlist (_homes_allowlisted_verb), so no
    future edit can quietly add an index-refreshing or mutating call to this read-only inspection,
    and the stderr refusal (_homes_checked)."""
    head = _homes_allowlisted_verb(args)
    return _homes_checked(head, _opf_observe._run_git(git, root, args), own_stderr)


def _homes_config_overrides():
    """The sorted names of the ambient runtime git configuration overrides the config-discovery
    environment drops (_opf_observe._config_discovery_env): GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM,
    GIT_CONFIG_COUNT with its GIT_CONFIG_KEY_<n> / GIT_CONFIG_VALUE_<n> pairs, GIT_CONFIG_PARAMETERS (a
    wrapper's `git -c`) and the legacy GIT_CONFIG: every GIT_CONFIG name but the carried
    GIT_CONFIG_NOSYSTEM toggle."""
    return sorted(name for name in os.environ if name != "GIT_CONFIG_NOSYSTEM"
                  and (name == "GIT_CONFIG" or name.startswith("GIT_CONFIG_")))


def _homes_logical_path(physical):
    """The path, spelled from the ambient PWD exactly as given, through which the working directory
    reaches `physical`, or None when there is none. git 2.53.0 names its current directory by PWD
    VERBATIM, never normalized, whenever PWD is the same directory as the cwd, so an includeIf
    "gitdir:" rule can match the spelling `R/.`, `R//` or `R/sub/..` of a repository R and not `R`
    itself. This helper takes PWD only when it is absolute and the same directory as the cwd. When
    the cwd is `physical` (the worktree top), the path is PWD itself, byte for byte, so the probe
    reproduces the adopter's own git invocation there; from any other cwd it is PWD joined,
    unnormalized, with the relative path from the cwd to `physical` (`<PWD>/..` from a subdirectory),
    a spelling git run from that cwd does not use (the over-refusal _homes_run_git_discovery
    discloses). None when PWD is unset, relative or another directory, when that spelling is exactly
    the physical path, or when it does not reach the same directory; the inspection never reaches
    this helper with a present relative PWD, which _homes_run_git_discovery refuses first."""
    pwd = os.environ.get("PWD")
    if not pwd or not os.path.isabs(pwd):
        return None
    try:
        cwd = os.getcwd()
        if not os.path.samefile(pwd, cwd):
            return None
        rel = os.path.relpath(str(physical), cwd)
        logical = pwd if rel == os.curdir else os.path.join(pwd, rel)
        if logical == str(physical) or not os.path.samefile(logical, str(physical)):
            return None
    except (OSError, ValueError):
        return None
    return logical


def _homes_run_git_discovery(git, root, args, input_bytes=None):
    """_opf_observe._run_git_config_discovery behind the same verb allowlist and stderr refusal, refused
    before launch while the environment carries a runtime configuration override that runner drops
    (_homes_config_overrides): the probe's answer could then differ from the adopter's own git there
    (with core.ignoreCase=true supplied through GIT_CONFIG_COUNT, `!/STAGING/` re-includes the staging
    home), so it is cannot-evaluate, never replayed. A present but relative ambient PWD is refused the
    same way before launch, naming it: git 2.53.0 can name its current directory by a relative PWD
    verbatim (PWD `.` makes a `gitdir:[.]/` rule apply to the adopter's git at the worktree top), and
    no probe here reproduces that spelling; an unset or empty PWD is not refused. When the absolute
    ambient PWD names the working
    directory and spells a path to `root` other than the physical one (_homes_logical_path: through a
    symlink, or a spelling such as `R/.`, `R//` or `R/sub/..`), the probe runs ALSO from that path,
    with cwd and PWD there and PWD passed verbatim, never normalized. At the worktree top the path is
    the ambient PWD itself, so the probe reproduces the adopter's own git there: git names the
    repository by PWD's exact spelling, so an includeIf "gitdir:" rule matching only that spelling
    applies to the adopter's git but not to the physical probe, and any disagreement between the two
    answers is cannot-evaluate, naming both paths. Disclosed over-refusal (fail-closed): from a
    working directory below the worktree top, git discovers the repository and names it by its
    physical path, so no such rule applies to the adopter's git there, yet the probe runs from
    `<PWD>/..` (and so on up) and refuses any rule matching that spelling but not the physical path
    (`gitdir:<PWD>/../` or a directory rule `gitdir:<link>/` above it). Disclosed residual
    (configuration divergence): the probe reads the configuration git discovers at inspection time through
    HOME, XDG_CONFIG_HOME and the system config, in the store's containing repository, from the physical
    path and the one path spelled from the absolute ambient PWD; a later configuration edit, a
    different HOME, another spelling of the working directory (a different symlink or another
    shell's PWD), a replacement ref the adopter's git would follow (the probe passes
    --no-replace-objects, so it never reads replaced objects), or a
    repository or index variable (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, dropped by the runner's
    allowlist) at the adopter's own git call is not bound by this inspection. One such spelling,
    named explicitly: below the worktree top, getcwd does not unify a bind-mount alias of the store
    as it does a symlink, so an adopter working below the top inside such an alias can satisfy an
    includeIf "gitdir:" rule for that alias that neither probe reproduces (untested here; disclosed,
    not engineered against). Disclosed residual
    (path namespace race): each probe resolves its pathname again when git launches, so a symlink or
    directory on either path that a concurrent local actor swaps during the inspection (between
    _homes_logical_path's check and a launch, or between probes) can direct a probe at another
    repository whose answer is then read as this one's; such an actor is outside this read-only
    helper's threat model, and the inspection does not bind the path namespace against it."""
    head = _homes_allowlisted_verb(args)
    dropped = _homes_config_overrides()
    if dropped:
        raise WriteGuardError("the environment carries the runtime git configuration override(s) {}, which "
                              "the homes gitignore inspection's config-discovery probe drops, so its answer "
                              "could differ from the adopter's own git; unset them and retry "
                              "(cannot-evaluate, fail-closed)".format(", ".join(dropped)))
    pwd = os.environ.get("PWD")
    if pwd and not os.path.isabs(pwd):
        raise WriteGuardError("the ambient PWD {!r} is relative: git can name the repository by that "
                              "spelling (an includeIf \"gitdir:\" rule can then match it and not the "
                              "physical path), and the homes gitignore inspection's probes do not "
                              "reproduce a relative PWD, so the adopter's effective ignore rules cannot "
                              "be determined; set PWD to the absolute working directory or unset it and "
                              "retry (cannot-evaluate, fail-closed)".format(pwd))
    out = _opf_observe._run_git_config_discovery(git, root, args, input_bytes=input_bytes)
    logical = _homes_logical_path(root)
    if logical is not None and _opf_observe._run_git_config_discovery(
            git, logical, args, input_bytes=input_bytes, logical=True) != out:
        raise WriteGuardError("git {} answers differently from the physical path {!r} and from the logical "
                              "path {!r} the working directory reaches it through (an includeIf "
                              "\"gitdir:\" rule, for example, can match only one), so the adopter's "
                              "effective ignore rules cannot be determined; cannot-evaluate "
                              "(fail-closed)".format(head, str(root), logical))
    return _homes_checked(head, out, False)


def _homes_repo_prefix(store_root, git, verb):
    """The containing non-bare worktree of `store_root`, the store's path within it as a relative Path,
    and its string prefix ("" at the repository toplevel, else "<dirs>/"): the opf.py _init_repo pattern
    rehosted on the scrubbed observer boundary. Not a repository, a bare repository, or an answer that
    cannot be confirmed is a WriteGuardError cannot-evaluate; git-absent is the caller's refusal."""
    args = ["rev-parse", "--is-inside-work-tree", "--is-bare-repository", "--show-toplevel"]
    out = _homes_run_git(git, store_root, args, own_stderr=True)
    if not out.completed:
        raise WriteGuardError("cannot run git for the {} gitignore inspection ({}); cannot-evaluate "
                              "(fail-closed)".format(verb, out.err.strip()))
    if out.rc != 0 or out.err:
        if _opf_observe._is_no_repo(out):
            raise WriteGuardError(
                "the store at {!r} is not inside a git repository, so the {} gitignore inspection "
                "cannot read effective ignore rules or the index; cannot-evaluate "
                "(fail-closed)".format(str(store_root), verb))
        raise WriteGuardError("git could not confirm the store's repository for the {} gitignore "
                              "inspection (rc {}); cannot-evaluate (fail-closed)".format(verb, out.rc))
    lines = out.out.split(b"\n")
    if (len(lines) != 4 or lines[:2] != [b"true", b"false"] or lines[-1] != b""
            or not os.path.isabs(os.fsdecode(lines[2]))):
        raise WriteGuardError("the store at {!r} is not a confirmed non-bare worktree; the {} "
                              "gitignore inspection cannot proceed (cannot-evaluate, "
                              "fail-closed)".format(str(store_root), verb))
    repo = Path(os.path.abspath(os.fsdecode(lines[2])))
    root = Path(os.path.abspath(str(store_root)))
    if root != repo and repo not in root.parents:
        raise WriteGuardError("the repository git reported does not contain the store root {!r}; the "
                              "{} gitignore inspection cannot proceed (cannot-evaluate, "
                              "fail-closed)".format(str(store_root), verb))
    rel = root.relative_to(repo)
    prefix = "" if rel == Path(".") else rel.as_posix() + "/"
    return repo, rel, prefix


def _homes_read_gitignore(store_root, verb):
    """The current .working/.gitignore bytes, or None when the file (or .working/ itself) is absent.
    A contained no-follow read bound to the store root, require_single_link, under the product read
    ceiling: a symlink, a non-regular file, a second hard link, an oversize file, a symlinked or
    non-directory .working/, or any read error is the named cannot-evaluate refusal
    homes-gitignore-unreadable, never a guess at the bytes."""
    journal = _opf_store._journal
    root = Path(os.path.abspath(str(store_root)))
    try:
        root_fd = _opf_store._open_dir_nofollow(root)
    except OSError as exc:
        raise WriteGuardError("homes-gitignore-unreadable: cannot open the store root for the {} "
                              "gitignore inspection ({}); cannot-evaluate (fail-closed)".format(
                                  verb, exc))
    try:
        try:
            pfd, name = journal._open_parent(root_fd, _HOMES_GITIGNORE_REL)
        except FileNotFoundError:
            return None            # no .working/ directory yet: the control file is absent
        except (OSError, journal.JournalError) as exc:    # a symlinked or non-directory .working/
            raise WriteGuardError("homes-gitignore-unreadable: cannot reach {} ({}); cannot-evaluate "
                                  "(fail-closed)".format(_HOMES_GITIGNORE_REL, exc))
        try:
            os.lstat(name, dir_fd=pfd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WriteGuardError("homes-gitignore-unreadable: cannot stat {} ({}); cannot-evaluate "
                                  "(fail-closed)".format(_HOMES_GITIGNORE_REL, exc))
        finally:
            _opf_store._close_fd_exc_safe(pfd)
        try:
            data, _st = journal._read_contained(root_fd, _HOMES_GITIGNORE_REL,
                                                require_single_link=True)
        except journal.JournalError as exc:
            raise WriteGuardError("homes-gitignore-unreadable: {} ({}); cannot-evaluate "
                                  "(fail-closed)".format(_HOMES_GITIGNORE_REL, exc))
        return data
    finally:
        _opf_store._close_fd_exc_safe(root_fd)


def _homes_index_holds(git, repo, prefix, verb):
    """The read-only tracked-control-path probe: any cached index entry under the store's staging or
    journals homes is the named HELD refusal, a finding whose remedy is the adopter's own explicit
    reviewed untracking (spec 4.2), never an index mutation here."""
    specs = [prefix + rel for rel in _HOMES_IGNORED_RELS]
    out = _homes_run_git(git, repo, ["--literal-pathspecs", "ls-files", "--cached", "-z", "--"] + specs)
    if not out.completed:
        raise WriteGuardError("cannot read the index over the store control paths for the {} "
                              "gitignore inspection ({}); cannot-evaluate (fail-closed)".format(
                                  verb, out.err.strip()))
    if out.rc != 0 or out.err:
        raise WriteGuardError("git could not list the store control paths for the {} gitignore "
                              "inspection (rc {}): {}; cannot-evaluate (fail-closed)".format(
                                  verb, out.rc, out.err.strip()))
    if out.out and not out.out.endswith(b"\x00"):
        raise WriteGuardError("git returned a malformed index listing for the {} gitignore "
                              "inspection; cannot-evaluate (fail-closed)".format(verb))
    tracked = sorted(os.fsdecode(entry) for entry in out.out.split(b"\x00")[:-1] if entry)
    if not tracked:
        return []
    return ["tracked-control-path: tracked control path(s) {}: tracked staging or journals require "
            "an explicit reviewed untracking change (spec 4.2); OPF did not modify the index and "
            "wrote nothing; run and commit the untracking yourself, then retry opf {}".format(
                ", ".join(repr(path) for path in tracked), verb)]


def _homes_gitignore_flags(git, repo, prefix, verb):
    """Refuse (cannot-evaluate) a skip-worktree, assume-unchanged or unmerged index entry for
    .working/.gitignore itself: under such a flag the worktree bytes just read may not be the
    committed ones, so no reconciliation can honestly be planned from them. The
    `ls-files --cached -v -z` tag grammar mirrors probe_dirty's destination-flag check above."""
    rel = prefix + _HOMES_GITIGNORE_REL
    flags = _homes_run_git(git, repo,
                           ["--literal-pathspecs", "ls-files", "--cached", "-v", "-z", "--", rel])
    if not flags.completed or flags.rc != 0 or flags.err:
        raise WriteGuardError("cannot inspect the index flags of {} for the {} gitignore inspection "
                              "(fail-closed)".format(_HOMES_GITIGNORE_REL, verb))
    if flags.out and not flags.out.endswith(b"\x00"):
        raise WriteGuardError("malformed index flags for {} in the {} gitignore inspection "
                              "(fail-closed)".format(_HOMES_GITIGNORE_REL, verb))
    for record in flags.out.split(b"\x00")[:-1]:
        if len(record) < 3 or record[1:2] != b" " or record[:1] not in (b"H", b"S", b"h", b"s",
                                                                        b"M", b"m"):
            raise WriteGuardError("malformed index flags for {} in the {} gitignore inspection "
                                  "(fail-closed)".format(_HOMES_GITIGNORE_REL, verb))
        if record[:1] != b"H":
            raise WriteGuardError(
                "the tracked {} carries a skip-worktree, assume-unchanged or unmerged index flag, "
                "so its worktree bytes may not be the committed ones; clear the flag and reconcile "
                "before retrying opf {} (cannot-evaluate, fail-closed)".format(
                    _HOMES_GITIGNORE_REL, verb))


def _homes_rel_ignored(git, repo, prefix, rel, verb):
    """Whether the adopter's EFFECTIVE ignore rules, the real configuration their own `git add` reads
    (global and system core.excludesFile included), ignore `rel` beneath the store: one rc-classified
    check-ignore per path over the config-discovery boundary, the opf.py _init_unignored pattern. The
    literal "./" prefix keeps a leading colon in an adopter store prefix a path, never pathspec magic.
    A home is probed through its _HOMES_PROBE_CHILD, so a directory-only pattern on the home matches
    whether the home is absent or present, whatever its type, and tracked content there cannot mask
    the answer (confirmed against git 2.53.0). rc 0 is ignored, rc 1 is not ignored, anything else,
    or any stderr diagnostic (_homes_checked), is cannot-evaluate."""
    result = _homes_run_git_discovery(git, repo, ["check-ignore", "--", "./" + prefix + rel])
    if not result.completed:
        raise WriteGuardError("cannot evaluate effective ignore rules for the {} gitignore "
                              "inspection ({}); cannot-evaluate (fail-closed)".format(
                                  verb, result.err.strip()))
    if result.rc == 0:
        return True
    if result.rc == 1:
        return False
    raise WriteGuardError("git check-ignore failed during the {} gitignore inspection (rc {}): {}; "
                          "cannot-evaluate (fail-closed)".format(verb, result.rc, result.err.strip()))


def _homes_unignored_content(git, repo, prefix, verb):
    """Existing untracked content under the staging and journals homes that the adopter's effective
    ignore rules leave unignored (ls-files --others --exclude-standard over the config-discovery
    boundary): a regular file or symlink AT a home, which the block's directory-only rules never
    match, or content a rule re-includes. A probe of a hypothetical child cannot see either."""
    out = _homes_run_git_discovery(git, repo, ["--literal-pathspecs", "ls-files", "--others",
                                               "--exclude-standard", "-z", "--"]
                                   + [prefix + rel for rel in _HOMES_IGNORED_RELS])
    if not out.completed or out.rc != 0 or out.err:
        raise WriteGuardError("cannot list unignored content under the store control paths for the "
                              "{} gitignore inspection (rc {}): {}; cannot-evaluate "
                              "(fail-closed)".format(verb, out.rc, out.err.strip()))
    if out.out and not out.out.endswith(b"\x00"):
        raise WriteGuardError("git returned a malformed untracked listing for the {} gitignore "
                              "inspection; cannot-evaluate (fail-closed)".format(verb))
    return [os.fsdecode(entry) for entry in out.out.split(b"\x00")[:-1] if entry]


def _homes_home_excluded(git, repo, prefix, rel, verb):
    """Whether the adopter's effective rules exclude the ignored home `rel` ITSELF as a directory, read
    from git's own rule report (check-ignore -v) for the home's _HOMES_PROBE_CHILD. git never descends
    into an excluded directory, so no later negation or nested .gitignore can re-include any path
    beneath it: coverage then holds for every present and future path by construction, not by sampled
    names. The report proves that only when its rule is an anchored `/<home>/` line of the store's
    .working/.gitignore (the managed block's form), which matches nothing but the home directory. No
    rule, or a negation, is False (not ignored). A child ignored by any OTHER rule (`/staging/*`, `*`,
    an excluded .working/) does not show the home itself excluded, so a partial or nested re-include
    beneath it (`!/staging/import/`) cannot be ruled out: cannot-evaluate. Disclosed residual, the
    fail-closed direction: a later adopter rule that does exclude the whole home in another spelling
    (`*`, `staging/`, or an excluded .working/) is refused too."""
    name = rel.rpartition("/")[2]
    probe = os.fsencode("./" + prefix + rel + "/" + _HOMES_PROBE_CHILD)
    result = _homes_run_git_discovery(git, repo, ["check-ignore", "-v", "-z", "--stdin"],
                                      input_bytes=probe + b"\x00")
    if not result.completed:
        raise WriteGuardError("cannot evaluate effective ignore rules for the {} gitignore "
                              "inspection ({}); cannot-evaluate (fail-closed)".format(
                                  verb, result.err.strip()))
    if result.rc == 1 and not result.out:
        return False
    fields = result.out.split(b"\x00")
    if result.rc != 0 or len(fields) != 5 or fields[3:] != [probe, b""]:
        raise WriteGuardError("git check-ignore -v returned rc {} or a malformed report during the {} "
                              "gitignore inspection; cannot-evaluate (fail-closed)".format(result.rc, verb))
    source, line, pattern = fields[:3]
    if pattern.startswith(b"!"):
        return False
    if source == os.fsencode(prefix + _HOMES_GITIGNORE_REL) and pattern == "/{}/".format(name).encode():
        return True
    raise WriteGuardError(
        "the effective ignore rule {}:{}:{} ignores the probe beneath {}{}/ without excluding that home "
        "itself, so a partial or nested re-include beneath it cannot be ruled out; exclude the whole "
        "home (the managed block's /{}/ line, not overridden) before retrying opf {} (cannot-evaluate, "
        "fail-closed)".format(os.fsdecode(source), os.fsdecode(line), os.fsdecode(pattern), prefix, rel,
                              name, verb))


def _homes_effective_holds(git, repo, prefix, block_present, verb):
    """The effective-ignore findings: a durable evidence home the adopter's real rules ignore
    (spec 4.2: durable imported/ and archive/ evidence MUST stay tracked), an ignored
    .working/.gitignore (spec 4.2: the block travels with the store, and an ignored control file is
    never committed), and, only when the managed block is already present, an ignored home the block
    should cover but the effective rules do not (an adopter negation after the block, for example)
    or existing unignored content at it (a regular file or symlink at the home). Homes are probed
    through _HOMES_PROBE_CHILD, so each answer is for new content, regardless of tracked entries
    and of the home's current type; an ignored home counts as covered only when its rule excludes
    the home itself (_homes_home_excluded). Disclosed residual: a durable home answers for the probe
    child's name only, so a rule ignoring just some names beneath it (`/.working/imported/*.log`) is
    not seen here."""
    # check-ignore rejects --literal-pathspecs, so it matches each probe path against the index as a
    # GLOB pathspec: an index entry at the path, or one a glob character in the store prefix matches
    # (`s[t]/` matching `st/`), turns the answer into "not ignored". The same glob-mode listing names
    # every such entry, so none can mask a durable or control-file answer; the one entry allowed is
    # the tracked control file itself, whose tracked state is the answer (it travels with the store).
    # An ignored-home probe needs no guard: a masked answer reads as not ignored, which holds.
    control = prefix + _HOMES_GITIGNORE_REL
    probes = ["./" + prefix + rel + "/" + _HOMES_PROBE_CHILD for rel in _HOMES_DURABLE_RELS]
    masked = _homes_run_git(git, repo, ["ls-files", "--cached", "-z", "--"] + probes + ["./" + control])
    if not masked.completed or masked.rc != 0 or masked.err:
        raise WriteGuardError("cannot list the ignore probe paths for the {} gitignore inspection "
                              "(rc {}): {}; cannot-evaluate (fail-closed)".format(
                                  verb, masked.rc, masked.err.strip()))
    if masked.out and not masked.out.endswith(b"\x00"):
        raise WriteGuardError("git returned a malformed probe-path listing for the {} gitignore "
                              "inspection; cannot-evaluate (fail-closed)".format(verb))
    masking = [e for e in masked.out.split(b"\x00")[:-1] if e and e != os.fsencode(control)]
    if masking:
        raise WriteGuardError("the index entry {!r} matches an ignore probe path and would mask the "
                              "effective ignore answer for the {} gitignore inspection; cannot-evaluate "
                              "(fail-closed)".format(os.fsdecode(masking[0]), verb))
    holds = []
    for rel in _HOMES_DURABLE_RELS:
        if _homes_rel_ignored(git, repo, prefix, rel + "/" + _HOMES_PROBE_CHILD, verb):
            holds.append("durable-evidence-ignored: the effective ignore rules ignore {}{}/, but "
                         "durable imported/ and archive/ evidence MUST stay tracked (spec 4.2); fix "
                         "the adopter ignore rule, then retry opf {}".format(prefix, rel, verb))
    if _homes_rel_ignored(git, repo, prefix, _HOMES_GITIGNORE_REL, verb):
        holds.append("homes-gitignore-ignored: the effective ignore rules ignore {}{}, so the managed "
                     "block would never be committed and would not travel with the store (spec 4.2); "
                     "fix the adopter ignore rule, then retry opf {}".format(
                         prefix, _HOMES_GITIGNORE_REL, verb))
    if block_present:
        unignored = _homes_unignored_content(git, repo, prefix, verb)
        for rel in _HOMES_IGNORED_RELS:
            home = prefix + rel
            present = [p for p in unignored if p == home or p.startswith(home + "/")]
            if present or not _homes_home_excluded(git, repo, prefix, rel, verb):
                holds.append("homes-gitignore-ineffective: the managed block is present but the "
                             "effective ignore rules do not ignore {}{}/ (an adopter negation, a "
                             "higher-precedence rule, or a non-directory entry at that path "
                             "overrides it){}; reconcile the adopter rules, then retry opf {}".format(
                                 prefix, rel, "".join("; unignored {!r}".format(p) for p in present),
                                 verb))
    return holds


def _homes_indexed_ignore_guard(git, repo, rel_prefix, verb):
    """Fail closed when an indexed .gitignore blob governing the probed control paths is unavailable
    in a partial clone: the probe above read it as no-rule, but the adopter's own `git add` would
    fetch it, so the effective answer cannot be trusted (the same cannot-evaluate opf.py's init
    preflight applies, through the same _opf_observe.indexed_ignore_availability, here run through
    the verb allowlist and `strict`, so a failed partial-clone config probe is cannot-evaluate, never the
    partial fallback that can still end clean). The candidates govern every probed path: each home's
    probe child and the control file itself."""
    probed = [rel + "/" + _HOMES_PROBE_CHILD for rel in _HOMES_DURABLE_RELS + _HOMES_IGNORED_RELS]
    candidates = _ignore_file_candidates(rel_prefix, probed + [_HOMES_GITIGNORE_REL])
    try:
        unavailable = _opf_observe.indexed_ignore_availability(git, repo, candidates,
                                                               run=_homes_run_git_discovery, strict=True)
    except RuntimeError as exc:
        raise WriteGuardError("cannot evaluate indexed ignore availability for the {} gitignore "
                              "inspection ({}); cannot-evaluate (fail-closed)".format(verb, exc))
    if unavailable:
        raise WriteGuardError("an indexed .gitignore blob is unavailable in this partial clone ({}); "
                              "the adopter's own git add would fetch it, so effective ignore rules "
                              "cannot be determined for opf {}; fetch or check out the blob and "
                              "retry (cannot-evaluate, fail-closed)".format(
                                  ", ".join(sorted(unavailable)), verb))


def inspect_homes_gitignore(store_root, verb, approved_rewrite=None, reviewed_existing=None):
    """The read-only homes-2 .working/.gitignore reconciliation inspection (spec 4.2). Its one caller
    is the generation-2 init operation (_opf_init_operation._inspect_homes_gitignore), reached only
    through that operation's explicit homes2 input: before its plan is persisted it requires an
    absent file, no hold, and planned bytes equal to its own create-only payload, writes those bytes
    through its journal, and re-checks with verify_homes_gitignore_effective after the write. No
    default verb calls it.

    Returns (planned, holds, prestate): `planned` is the full new file bytes
    _opf_store.plan_homes_gitignore computed (None for no change), `prestate` is the exact file
    bytes (None when absent) that plan was computed from, which the caller's journal prestate MUST
    byte-match before writing (the append case cannot be rebuilt from `planned`: a file with and
    without a final newline plan the same bytes), and `holds` is the list of named refusals that
    HOLD installation:
    tracked-control-path (a cached index entry under staging/ or journals/, held for the adopter's
    explicit reviewed untracking, never an index mutation here), durable-evidence-ignored,
    homes-gitignore-ignored, homes-gitignore-ineffective, and homes-gitignore-block-drift. The
    caller may write only when holds is empty. Raises WriteGuardError on every cannot-evaluate
    state: git missing from PATH, no repository or a bare one, an unreadable .working/.gitignore
    (homes-gitignore-unreadable), a flagged index entry for that file, a probe that cannot run, fails
    or writes a diagnostic to stderr, an ambient runtime git configuration override the config-discovery
    probe drops (named; the remaining configuration divergence is disclosed at _homes_run_git_discovery),
    a present but relative ambient PWD (named: git can name the repository by that spelling, which no
    probe reproduces; an unset or empty PWD is not refused),
    a probe answer that differs between the physical store path and the path the ambient PWD spells
    to it (both named; the subdirectory over-refusal and the path namespace race are disclosed at
    _homes_run_git_discovery), an index entry that would mask a probe, an ignored home
    whose governing rule does not exclude the home itself, or an unavailable indexed ignore blob in a
    partial clone.

    The index is never mutated: only rev-parse, ls-files, check-ignore, config and cat-file run
    (structurally allowlisted: every call, the shared indexed_ignore_availability's included, runs
    through _homes_run_git or _homes_run_git_discovery), never status, add, rm, update-index or
    reset, and both git environments set GIT_OPTIONAL_LOCKS=0; the one disclosed metadata residual
    is the split-index shared-index mtime refresh a read can cause. `approved_rewrite` and
    `reviewed_existing` are the reviewed-rewrite approval forwarded to plan_homes_gitignore: the
    exact previewed replacement bytes and the exact file bytes they were previewed from, applied
    only on that explicit approval and only while the current file still byte-matches the reviewed
    bytes, never silently."""
    git = _opf_observe._git_path()
    if git is None:
        raise WriteGuardError("git is missing from PATH, so the {} gitignore inspection cannot read "
                              "effective ignore rules or the index; cannot-evaluate "
                              "(fail-closed)".format(verb))
    repo, rel_prefix, prefix = _homes_repo_prefix(store_root, git, verb)
    existing = _homes_read_gitignore(store_root, verb)
    holds = _homes_index_holds(git, repo, prefix, verb)
    _homes_gitignore_flags(git, repo, prefix, verb)
    block_present = existing is not None and _opf_store.homes_gitignore_matches(
        existing.decode("utf-8", "surrogateescape"))
    holds += _homes_effective_holds(git, repo, prefix, block_present, verb)
    _homes_indexed_ignore_guard(git, repo, rel_prefix, verb)
    try:
        planned = _opf_store.plan_homes_gitignore(existing, approved_rewrite=approved_rewrite,
                                                  reviewed_existing=reviewed_existing)
    except ValueError as exc:
        holds.append(str(exc))
        planned = None
    return planned, holds, existing


def verify_homes_gitignore_effective(store_root, verb):
    """The post-write effectiveness re-check for the caller that installs the managed block (the
    generation-2 init operation, after its journaled write and on every later attempt of that
    plan, _opf_init_operation._verify_homes_gitignore): re-runs the inspector's contained read of
    .working/.gitignore (homes-gitignore-unreadable), confirms the exact managed block in those bytes
    (homes-gitignore-block-missing when it is absent, adopter-only or drifted: other ignore rules
    covering the homes do not stand in for it), the read-only tracked-control-path index probe, the
    .working/.gitignore index-flag check, the effectiveness probes, and the partial-clone
    indexed-ignore guard, and returns the list of named findings (empty when the installed block is
    effective and no control path is tracked). Raises WriteGuardError on the same cannot-evaluate
    states as inspect_homes_gitignore. Read-only; the index is never mutated."""
    git = _opf_observe._git_path()
    if git is None:
        raise WriteGuardError("git is missing from PATH, so the {} gitignore verification cannot "
                              "read effective ignore rules or the index; cannot-evaluate "
                              "(fail-closed)".format(verb))
    repo, rel_prefix, prefix = _homes_repo_prefix(store_root, git, verb)
    existing = _homes_read_gitignore(store_root, verb)    # the inspector's unreadable-input refusals
    block_present = existing is not None and _opf_store.homes_gitignore_matches(
        existing.decode("utf-8", "surrogateescape"))
    holds = _homes_index_holds(git, repo, prefix, verb)
    if not block_present:
        holds.append("homes-gitignore-block-missing: {} does not carry the exact managed block after "
                     "the write (absent, adopter-only or drifted), so the post-write re-check cannot "
                     "confirm it; re-plan with the inspection, then retry opf {}".format(
                         prefix + _HOMES_GITIGNORE_REL, verb))
    _homes_gitignore_flags(git, repo, prefix, verb)
    holds += _homes_effective_holds(git, repo, prefix, block_present, verb)
    _homes_indexed_ignore_guard(git, repo, rel_prefix, verb)
    return holds


def check_clean(res, write_scope, verb, scope_label):
    """Check the planned destinations (`write_scope`, from plan_write_scope plus any collision candidates the
    caller adds), plus the lease, immediately before mutation. Tracked dirt and untracked/ignored destination
    content refuse: HEAD must preserve pre-existing content. Exact untracked "??" and ignored "!!" leases are
    excluded for acquire_lease's O_EXCL never-seize refusal; tracked lease dirt refuses distinctly (spec 5.7).
    This follows read-only triage/plan and precedes lease acquisition and the first write. Refuses
    fail-closed (exit 2) on dirt, naming up to 10 paths plus the total and advising commit or move aside,
    never a restore of the owner's work. `scope_label` names the planned scope in that refusal.

    Literal pathspecs alone do not bound --ignored=matching: the parser filters ignored prefix siblings,
    and the probe expands collapsed ignored ancestors within the same scope. Declared unmanaged paths
    and other unrelated store content are outside this gate and outside recovery/staging advice.

    Residual over-approximation: every declared render destination is checked even if its bytes would
    remain unchanged, as is each create-only candidate even if already present. Descendants of
    a destination occupied by a directory also refuse as collisions. Ignored content at these paths
    must be moved aside before retrying; no whole-.working cleanliness requirement remains. Random
    temporary names use O_EXCL and cannot overwrite pre-existing entries; an interrupted run can leave
    its own temporary files, which require identification before manual removal.

    Residual (F-OPF-STATUSFILTER-LFS-FALSEPOS, disclose-guard-residuals): a store with an EXTERNAL NORMALIZING
    clean/process filter (the git-lfs shape) can read DIRTY here even when genuinely clean, because the probe
    neutralizes filter execution (so no repo-planted filter runs on this read-only observation) and the
    racy-clean window then re-hashes raw worktree bytes against the cleaned index blob. This is a fail-closed
    over-refusal (never a false-clean, never a filter exec); the refusal message adds operator-clear guidance
    to settle the worktree when the store carries such a filter."""
    git = _opf_observe._git_path()
    if git is None:
        raise WriteGuardError("cannot locate git to verify the store is clean before the {}; without a "
                              "verified restore path the destructive rewrite is refused (spec 5.1, "
                              "fail-closed)".format(verb))
    store_root = res.store_root
    product_root = res.product_root if res.product_root is not None else store_root
    lease_excl = "{}/{}".format(res.machine_rel, _opf_check.LEASE_NAME)
    store_specs = list(write_scope["store"]) + [lease_excl]
    product_specs = []
    same_root = os.path.abspath(str(product_root)) == os.path.abspath(str(store_root))
    for relpath in write_scope["product"]:
        (store_specs if same_root else product_specs).append(relpath)

    dirty = probe_dirty(git, store_root, store_specs, lease_excl, verb)
    if product_specs:
        dirty += probe_dirty(git, product_root, product_specs, None, verb)
    if dirty:
        shown = sorted(set(dirty))
        head = shown[:10]
        # A store with a configured external NORMALIZING clean/process filter (the git-lfs shape) can read
        # DIRTY here even when genuinely clean: the probe neutralizes filter EXECUTION (fail-closed, so no
        # repo-planted filter runs on this read-only observation), so the cleaned index blob cannot be
        # reproduced and the racy-clean window re-hashes raw worktree bytes as a mismatch
        # (F-OPF-STATUSFILTER-LFS-FALSEPOS). It is a fail-closed over-refusal, never a false-clean. When the
        # store carries such a filter, guide the operator to settle the worktree rather than leaving a bare
        # "dirty". The presence probe is on the error path only; a RuntimeError there (a config that turned
        # unreadable since the probe) resolves to the generic message, never a crash.
        def _has_clean_process_filter(rt):
            try:
                return bool(_opf_observe._filter_neutralizing_config(git, rt))
            except RuntimeError:
                return False
        filtered = _has_clean_process_filter(store_root) or (
            bool(product_specs) and _has_clean_process_filter(product_root))
        note = (" This store has a configured clean/process filter (git-lfs-shape): the {} probe "
                "neutralizes filter execution for safety, so a normalizing filter can make a genuinely-clean "
                "store read dirty in the racy-clean window. Settle the worktree (commit, or check out so the "
                "index and worktree agree for the filtered path) before re-running opf {}.".format(verb, verb)
                if filtered else "")
        raise WriteGuardError(
            "the working tree is not clean over this {}'s planned {} (store and product roots): {} dirty "
            "path(s), showing {}: {}. Commit your changes (or move them aside), then re-run opf {}; the "
            "uncommitted work is yours and the {} never restores or discards it.{}".format(
                verb, scope_label, len(shown), len(head), ", ".join(head), verb, verb, note))


# --- the single-writer lease (spec 5.7) -----------------------------------------------------------------

# The one case in which opf itself removes another run's lease (acquire_lease_for_recovery), stated
# wherever a refusal tells the operator what opf does not remove, so no refusal claims it never does.
FOREIGN_LEASE_SCOPE = ("opf removes another run's lease only while reconciling an interrupted run of the same "
                       "verb, once that lease's holder is confirmed dead on this host")
# Where the held-lease refusal's operator remedy begins: _lock_refusal drops that remedy under a
# concurrently HELD examination lock (a live reconciliation is running, so its condition cannot hold).
HELD_REMEDY = " If you have confirmed NO opf run is live,"


def lease_held_message(pfd, name, lease_rel, verb):
    """Compose the EEXIST held-lease refusal, best-effort naming the existing holder/operation/acquired_at.
    A present-but-unreadable or malformed payload STILL refuses (present-is-held, matching C-LEASE); the
    lease is never seized or overwritten (spec 5.7). Names the manual reconciliation remedy and the one case
    in which opf itself releases another run's lease (FOREIGN_LEASE_SCOPE). Its denial names only the lease
    now present: a refusal met by the claim retried after this run released a confirmed-dead leftover
    (_claim_after_release) appends that release, so no clause here denies every removal by this run."""
    detail = "a present lease with an unreadable payload"
    try:
        # O_NONBLOCK so a FIFO (or other special file) planted at lease.toml cannot BLOCK the open (an
        # O_RDONLY open of a writer-less FIFO would hang indefinitely); fstat the opened fd and, for any
        # NON-REGULAR file, refuse WITHOUT reading (presence is refusal, matching C-LEASE; never block).
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                detail = "a present non-regular lease (held)"
            else:
                raw = _read_capped(fd)
                if raw is None:
                    detail = ("a present lease with an OVERSIZED payload (over the 65536-byte bound, so it is "
                              "never read as a lease and no holder is taken from a prefix of it)")
                else:
                    try:
                        data = tomllib.loads(raw.decode("utf-8"))
                    except (ValueError, RecursionError, MemoryError):
                        # disposed at the parse (deep nesting, a many-part key, an over-long integer or
                        # undecodable bytes keep the generic detail); the outer handler stays the backstop
                        data = None
                    if isinstance(data, dict):
                        detail = "held by {!r} (operation {!r}, acquired_at {!r})".format(
                            data.get("holder"), data.get("operation"), data.get("acquired_at"))
        finally:
            _opf_store._journal._close_fd_quietly(fd)
    except Exception:  # noqa: BLE001  a present-but-unreadable lease still refuses (present is held)
        pass
    return ("another opf run holds the single-writer lease {}: {}. The lease is never seized (spec 5.7).{} "
            "release that lease as your own reconciliation step (this run has not removed the lease now present; "
            "{}), then re-run "
            "opf {}.".format(lease_rel, detail, HELD_REMEDY, FOREIGN_LEASE_SCOPE, verb))


def lease_holder(verb):
    """The holder identity THIS run stamps on the lease it creates: "opf-<verb>:<host>:<pid>". Single-
    sourced so the acquire WRITE and the release OWNERSHIP-CHECK reason about the same identity (the release
    proves ownership by a full-payload byte compare, which subsumes this holder, and names a foreign holder
    only in its diagnostic)."""
    return "opf-{}:{}:{}".format(verb, socket.gethostname(), os.getpid())


def acquire_lease(root_fd, machine_rel, verb):
    """Claim the single-writer lease ATOMICALLY (atomic-claim-from-pool). The claim IS the create: open
    machine_rel/lease.toml O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW beneath the parent fd, so there is no
    check-then-create gap and EEXIST IS the held-lease refusal. The closed payload (schema/holder/operation/
    acquired_at read from the clock, G5; the operation is `verb`) satisfies C-LEASE exactly, so the mid-run
    doctor stays VALID with the lease held (containment-clean, spec 5.7/11). RETURNS the exact payload bytes
    written, so the ownership-verified release can prove the lease it removes is still THIS run's own
    (never-seize, spec 5.7)."""
    journal = _opf_store._journal
    lease_rel = "{}/{}".format(machine_rel, _opf_check.LEASE_NAME)
    payload = _opf_emit.emit_checked({
        "schema": _opf_schema.SUPPORTED_SCHEMA,
        "holder": lease_holder(verb),
        "operation": verb,
        "acquired_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }).encode("utf-8")
    pfd, name = journal._open_parent(root_fd, lease_rel)
    try:
        try:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=pfd)
        except FileExistsError:
            raise LeaseHeldError(lease_held_message(pfd, name, lease_rel, verb))
        try:
            try:
                journal._write_all(fd, payload)
                os.fsync(fd)
            finally:
                _opf_store._close_fd_exc_safe(fd)
            os.fsync(pfd)
        except BaseException as exc:
            # Any failure AFTER the O_EXCL create but BEFORE successful acquisition (a failed payload write,
            # fsync, or the durability fsync of the parent) LEAVES the lease in place: it is a leftover from
            # THIS failed run, released through operator reconciliation exactly like a lease from a dead
            # run (spec 5.7), never removed here. A by-name unlink would be OWNERSHIP-BLIND: if a peer replaced
            # the lease in the failure window (A-create / B-replace / A-fail), the unlink would delete the
            # REPLACEMENT holder's lease, a never-seize violation (spec 5.7). This code never removes a lease
            # it cannot prove is still the one it created, so it leaves-and-reconciles rather than racing an
            # unlink. A KeyboardInterrupt/SystemExit propagates untouched (the lease is still left); an
            # ordinary failure is surfaced as a reconcilable WriteGuardError so the operator gets clear advice.
            if isinstance(exc, WriteGuardError) or not isinstance(exc, Exception):
                raise
            raise WriteGuardError(
                "{} lease acquisition failed after the lease {} was created ({}); the lease is LEFT in "
                "place as a leftover from this failed {} and is never seized (spec 5.7). If you have "
                "confirmed NO opf run is live, release the leftover lease as your own reconciliation step "
                "(this run never removes it; {}), then re-run opf {}.".format(
                    verb, lease_rel, exc, verb, FOREIGN_LEASE_SCOPE, verb)) from exc
    finally:
        _opf_store._close_fd_exc_safe(pfd)
    return payload


def _read_capped(fd):
    """The COMPLETE bytes of the open file `fd`, read through EOF, or None once more than 65536 bytes
    arrive: an oversized payload is never truncated into a readable prefix. Descriptor-free: the caller
    owns the open and the close."""
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, 65537 - total)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > 65536:
            return None
        chunks.append(chunk)


def read_lease_payload(pfd, name, why=None):
    """Read the lease at (pfd, name) SAFELY for an ownership compare, reusing the round-2 R3 pattern:
    O_RDONLY|O_NOFOLLOW|O_NONBLOCK (a planted FIFO or other special file cannot BLOCK the open), then fstat
    the opened fd and, for any NON-REGULAR file, return None WITHOUT reading. Returns the file's COMPLETE
    raw bytes, read through EOF, so no later validation or ownership compare ever judges a valid-looking
    PREFIX of a larger payload (QA round 1: a 64 KiB valid prefix of an oversized malformed lease qualified
    for release under the former single bounded read). Returns None when the file is absent, non-regular,
    unreadable, or larger than the 64 KiB bound: the bound still never trusts the on-disk size, and an
    oversized payload is never truncated into a readable one (fail-closed, present-is-held upstream).
    When `why` (a list) is given, a None return appends its one cause, for a DIAGNOSTIC only: "absent",
    "non-regular", "oversized", or "unreadable (<error>)", so a caller never reports an oversized or
    non-regular entry as an absent one."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    except FileNotFoundError:
        if why is not None:
            why.append("absent")
        return None
    except OSError as exc:
        if why is not None:
            why.append("non-regular" if exc.errno == errno.ELOOP else "unreadable ({})".format(exc))
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            if why is not None:
                why.append("non-regular")
            return None
        raw = _read_capped(fd)
        if raw is None and why is not None:
            why.append("oversized")
        return raw
    except OSError as exc:
        if why is not None:
            why.append("unreadable ({})".format(exc))
        return None
    finally:
        _opf_store._close_fd_exc_safe(fd)


# What lease_holder_of says of an entry it read no bytes from, by the read_lease_payload cause.
_UNREAD_ENTRY = {"absent": "the lease is absent",
                 "non-regular": "the lease is a non-regular entry",
                 "oversized": "the lease payload is OVERSIZED (over the 65536-byte bound)"}


def lease_holder_of(raw, cause=None):
    """Best-effort holder identity from raw lease bytes, for a DIAGNOSTIC message only (never raises, never
    load-bearing: the ownership decision is the full-payload byte compare in unlink_owned_lease). With no
    bytes (raw None) it names no holder and says why, from `cause` (a read_lease_payload cause: absent,
    non-regular, oversized, or unreadable with its error), or that the lease was not read when the caller
    gives no cause; it never reports one cause as another. No shipped caller passes raw None today (the
    one caller, unlink_owned_lease, names a holder only from bytes it read), so that branch is defensive
    and self_test exercises it by calling this function directly."""
    if raw is None:
        if cause is None:
            return "no holder named (the lease was not read)"
        if cause.startswith("unreadable"):
            return "no holder named (the lease is {})".format(cause)
        return "no holder named ({})".format(_UNREAD_ENTRY.get(cause, "the lease was not read"))
    try:
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, MemoryError):
            # disposed at the parse (deep nesting, a many-part key, an over-long integer or undecodable
            # bytes fall through to the malformed-replacement text); the outer handler stays the backstop
            data = None
        if isinstance(data, dict) and data.get("holder") is not None:
            return "holder {!r}".format(data.get("holder"))
    except Exception:  # noqa: BLE001  diagnostic only; an unreadable replacement still refuses above
        pass
    return "an unreadable or malformed replacement lease"


def unlink_owned_lease(pfd, name, lease_rel, expected_payload, verb):
    """The SINGLE ownership-verified lease-removal site (class-width never-seize, spec 5.7: a lease is
    "never seized from a live holder"). Unlink the lease at (pfd, name) ONLY when the file still there is
    byte-for-byte the exact lease THIS run created (`expected_payload`, the bytes acquire_lease
    returned; a full-payload proof that subsumes a holder-only compare). If it does NOT match -- a peer
    replaced the lease in the interval -- it is NEVER unlinked (never-seize) and the replacement is LEFT in
    place for operator reconciliation, surfaced as a fail-closed WriteGuardError naming the replacement's
    holder where legible. A failed unlink is likewise SURFACED, never swallowed (no-concealed-failure). No
    lease this run cannot prove is its own is ever removed here.

    DISCLOSED RESIDUAL (disclose-guard-residuals): a tiny TOCTOU window remains between the ownership read and
    the unlink -- a swap in exactly that window could still unlink a replacement, and because that unlink then
    succeeds the run also reports exit-0 SUCCESS (a false "released") over the deleted peer lease rather than
    the never-seize refusal. This is inherent to unlink-by-name (there is no unlink-this-exact-inode primitive
    available here) and cannot be eliminated, only disclosed; it is reachable ONLY when an operator or peer
    violates the documented release-only-when-no-run-is-live reconciliation (spec 5.7). Concurrent
    RECOVERIES cannot occupy the window: acquire_lease_for_recovery examines, releases, and re-claims under
    one exclusive examination lock (_exclusive_examination), so the recovery path is serialized and that
    operator-or-peer violation stays the only reachability, within the locking boundary that lock
    discloses (one kernel's flock arbitration; spec section 17). It is vastly smaller than the prior
    ownership-blind unlink and never-seizes under any non-adversarial-mid-window sequence."""
    why = []
    on_disk = read_lease_payload(pfd, name, why)
    if on_disk != expected_payload:
        # FIX3: distinguish a genuine ABSENCE (the lease was deleted, not replaced) from a REPLACEMENT (a
        # present-but-different payload). Both stay fail-closed / never-seize; only the operator-facing
        # wording differs. read_lease_payload returns None for absent, non-regular, oversized, or
        # unreadable, and `why` names which, so an oversized or non-regular replacement is never ABSENT.
        cause = why[0] if why else "unreadable (no cause recorded)"
        if on_disk is None and cause == "absent":
            detail = "the lease is ABSENT at release (already removed); this run's own lease is gone"
        elif on_disk is None and cause == "oversized":
            detail = ("the lease was REPLACED before release by an OVERSIZED payload (over the 65536-byte "
                      "bound, never read as a lease, so no holder is named); this run's own lease is gone")
        elif on_disk is None and cause == "non-regular":
            detail = ("the lease was REPLACED before release by a non-regular entry; this run's own lease "
                      "is gone")
        elif on_disk is None:
            detail = ("the lease present at release could not be read, {}, so this run cannot prove it is "
                      "its own".format(cause))
        else:
            detail = ("the lease was REPLACED by another holder ({}) before release; this run's own lease "
                      "is gone".format(lease_holder_of(on_disk)))
        raise WriteGuardError(
            "the {} lease {} could not be released as this run's own: {}. It is NEVER seized (spec 5.7) "
            "and is LEFT in place for operator reconciliation, so no lease is removed here "
            "(fail-closed).".format(verb, lease_rel, detail))
    try:
        os.unlink(name, dir_fd=pfd)
    except OSError as exc:
        raise WriteGuardError("could not release the {} lease {} ({}); surfaced, never swallowed "
                              "(fail-closed)".format(verb, lease_rel, exc))


def release_lease(root_fd, machine_rel, expected_payload, verb):
    """Release the lease created by acquire_lease: OWNERSHIP-VERIFIED unlink of machine_rel/lease.toml
    no-follow via the parent fd, then fsync the parent. The removal routes through the SINGLE
    ownership-verified site (unlink_owned_lease): the lease is removed ONLY when it is still THIS
    run's own (`expected_payload`), never seized from a peer that replaced it (spec 5.7); a replaced lease is
    LEFT and surfaced (exit 2). A failed unlink is SURFACED (exit 2), never swallowed (no-concealed-failure).
    A killed run leaves the lease, which is spec-conformant (present only while held; a leftover is released
    through operator reconciliation, spec 5.7) and is what the EEXIST refusal covers."""
    journal = _opf_store._journal
    lease_rel = "{}/{}".format(machine_rel, _opf_check.LEASE_NAME)
    pfd, name = journal._open_parent(root_fd, lease_rel)
    try:
        unlink_owned_lease(pfd, name, lease_rel, expected_payload, verb)
        os.fsync(pfd)
    finally:
        _opf_store._close_fd_exc_safe(pfd)


# --- the recovery claim: the spec 5.7 never-seize and dead-run release clauses over a leftover lease ---

# The holder identity acquire_lease stamps (lease_holder): "opf-<verb>:<host>:<pid>". The verb is a
# lowercase token, so its first ':' ends it and is captured for the verb binding below, and the pid is the
# LAST colon-separated field, so a host name that itself carries ':' still parses; the pid is bounded and
# canonical (digits, no leading zero), so an overlong or padded value never reaches os.kill.
_LEASE_HOLDER_RE = re.compile(r"opf-([a-z][a-z0-9-]*):(.+):([1-9][0-9]{0,9})\Z")


def _closed_lease_model(raw):
    """The parsed model of a COMPLETE well-formed single-writer lease (the closed spec 5.7 shape C-LEASE
    validates: exactly the closed keys, the supported schema, a non-empty holder and operation, an RFC
    3339 UTC acquired_at), or None. The COMPLETE closed schema is validated FIRST (the
    _opf_oplock._validate_recovery_lease model), so a malformed or foreign-shape lease and an unparseable
    payload each refuse upstream rather than ever reaching the holder comparison."""
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        # RecursionError: tomllib's refusal of deep nesting and (CPython 3.14.8 on) a 1001-part key, and
        # MemoryError, a many-part key under a memory limit: such a lease is malformed like any other and
        # recovery refuses it as held.
        return None
    if not isinstance(data, dict) or set(data) != set(_opf_check.LEASE_TOP_KEYS):
        return None
    if type(data.get("schema")) is not int or data["schema"] != _opf_schema.SUPPORTED_SCHEMA:
        return None
    for key in ("holder", "operation", "acquired_at"):
        if type(data.get(key)) is not str or not data[key]:
            return None
    if not _opf_check._valid_timestamp(data["acquired_at"]):
        return None
    return data


def _holder_names_this_host(host):
    """True when the examined holder's host part names THIS host. A cross-host holder always reads
    possibly-live (never seized): liveness is observable only where the probe runs."""
    return host == socket.gethostname()


def _examined_leftover_holder(raw, verb):
    """(holder, pid) when `raw` is a COMPLETE well-formed single-writer lease (_closed_lease_model)
    whose holder is the exact identity acquire_lease stamps for THIS verb and whose host part names THIS
    host; None otherwise, and None always reads possibly-live (never seized). The verb is bound twice
    (the operation key and the holder's verb field both equal `verb`): a dead run of ANOTHER opf verb
    left writes this verb's reconciliation does nothing to reconcile, so its leftover lease stays the
    operator's own reconciliation step, exactly as before this rule existed. A cross-host holder refuses
    via _holder_names_this_host."""
    data = _closed_lease_model(raw)
    if data is None:
        return None
    match = _LEASE_HOLDER_RE.fullmatch(data["holder"])
    if match is None or match.group(1) != verb or data["operation"] != verb:
        return None
    if not _holder_names_this_host(match.group(2)):
        return None
    return data["holder"], int(match.group(3))


def _lease_holder_confirmed_dead(pid):
    """True ONLY on positive evidence that the holder process is dead on THIS host: os.kill(pid, 0) raises
    ProcessLookupError (the journal's possibly-live-never-seized model, _journal.owner_confirmed_dead, on
    its no-start-time leg: the spec 5.7 lease payload records no process start time). A live pid, EPERM (a
    live foreign-uid holder), an out-of-range pid, or any other outcome reads as possibly-live, never
    seized. DISCLOSED RESIDUALS, each shared with the journal-lock owner model where no start time is
    readable: a dead holder whose pid was REUSED by a live process reads possibly-live and still refuses
    (the operator remedy stands, fail-closed); a live holder in another PID namespace that shares this
    hostname and store can read dead, exactly as a recorded pid absent from the prober's namespace reads
    there."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except Exception:  # noqa: BLE001  EPERM, OverflowError, or anything else: possibly-live, never seized
        return False
    return False


def _exclusive_examination(pfd, lease_rel, verb):
    """Serialize the WHOLE leftover-lease reconciliation (examine, release, re-claim) across concurrent
    recoveries: one EXCLUSIVE non-blocking flock on the lease's parent directory, taken through the open
    `pfd` and released by its close (the kernel releases it when the holder dies, so a killed recoverer
    leaves nothing of it to reconcile, and no new on-disk name is ever created). Without this lock two
    recoveries could BOTH read the same dead-holder bytes, BOTH pass the bound-to-exact-bytes release
    compare, and the slower unlink would then remove the faster recoverer's freshly created LIVE lease
    (the QA round 1 blocker: both writers then recover under one pathname), so the examined-bytes binding
    alone cannot close that window; only serialization of the full examine-release-reclaim span can. A
    HELD lock (EWOULDBLOCK/EAGAIN) refuses fail-closed and never blocks (matching the O_NONBLOCK lease
    reads), naming the concurrent reconciliation; ANY OTHER flock failure (ENOLCK, ENOTSUP, EINVAL, EBADF,
    or any other errno: a filesystem that cannot lock this directory) also refuses, with its own message
    naming the lock failure, never a concurrent reconciliation that need not exist (QA round 2); a
    platform without POSIX fcntl refuses rather than racing (the _journal stale-lock arbitration posture).

    DISCLOSED LOCKING BOUNDARY (spec section 17): a SUCCESSFUL flock serializes only recoveries whose locks
    one kernel arbitrates (one host, containers sharing it, and killed recoverers, whose lock that kernel
    drops). Where the store sits on NFS or another filesystem whose flock is local to each client kernel,
    two hosts sharing the store under one hostname can both take the lock and race the release, and the
    examined-bytes binding alone does not close that race; where flock is unsupported or fails, every
    dead-run release refuses here and stays the operator's reconciliation step."""
    try:
        import fcntl
    except ImportError as exc:
        raise WriteGuardError(
            "reconciling the present {} lease {} needs the POSIX fcntl serialization primitive (absent "
            "on this platform: {}); the lease is never examined or removed without it "
            "(fail-closed).".format(verb, lease_rel, exc)) from exc
    try:
        fcntl.flock(pfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise WriteGuardError(
                "another reconciliation is concurrently examining the {} lease {} ({}); this run refuses "
                "rather than race it (at most one recoverer ever examines, releases, and re-claims the "
                "lease), and no lease was examined or removed by this run. Re-run opf {} once that "
                "reconciliation finishes.".format(verb, lease_rel, exc, verb)) from exc
        raise WriteGuardError(
            "the examination lock that serializes reconciling the present {} lease {} could not be taken "
            "({}): the store's filesystem does not support or refused the directory lock, which is a lock "
            "failure, not a concurrent reconciliation. This run refuses rather than examine without it, and "
            "no lease was examined or removed by this run.".format(verb, lease_rel, exc)) from exc


def _lock_refusal(exc, held):
    """The examination-lock refusal with the held-lease refusal appended as its own sentence: the lock
    failed before the lease was examined, so whether its holder is live is not known. The refusal names
    that holder (the held-lease message, read when the claim met the lease) and never calls the lease
    leftover. Under a concurrently HELD lock the held-lease remedy is dropped: a live reconciliation is
    running, so its confirmed-no-run-is-live condition cannot hold."""
    text = str(held)
    cause = exc.__cause__
    while cause is not None and not isinstance(cause, OSError):
        cause = cause.__cause__
    if cause is not None and cause.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
        # The LAST occurrence: the held-lease refusal ends with the remedy, and the holder it quotes from
        # the lease file comes before it, so a holder carrying the remedy's words never cuts the text.
        text = text.rsplit(HELD_REMEDY, 1)[0]
    return WriteGuardError("{} {}{}".format(exc, text[:1].upper(), text[1:]))


def _claim_after_release(pfd, root_fd, machine_rel, verb, lease_rel, released):
    """After the examined leftover lease was UNLINKED: make that release durable and retry the atomic claim
    once, returning the claimed payload. EVERY failure from here on carries `released` (the release
    report), whatever its type (QA round 2: a JournalError reopening the parent, or an EMFILE or EACCES
    from the O_EXCL create, escaped without it): a WriteGuardError keeps its text with the report
    appended, any other ordinary exception is surfaced as a WriteGuardError naming it with the report
    appended, and a KeyboardInterrupt or SystemExit propagates as itself, the report attached as a note."""
    try:
        os.fsync(pfd)
    except Exception as exc:  # noqa: BLE001  any ordinary failure after the release carries its report
        raise WriteGuardError(
            "the release of the leftover {} lease {} could not be made durable ({}); re-run opf "
            "{} once the store volume is healthy. Before that failure, {}.".format(
                verb, lease_rel, exc, verb, released)) from exc
    except BaseException as exc:
        exc.add_note("Before that interruption, {}.".format(released))
        raise
    try:
        return acquire_lease(root_fd, machine_rel, verb)
    except WriteGuardError as exc:
        raise WriteGuardError("{} Before that claim, {}.".format(exc, released)) from exc
    except Exception as exc:  # noqa: BLE001  any ordinary failure after the release carries its report
        raise WriteGuardError(
            "the {} lease claim retried after that release failed ({!r}) (fail-closed); re-run opf {} once "
            "the cause is cleared. Before that claim, {}.".format(verb, exc, verb, released)) from exc
    except BaseException as exc:
        exc.add_note("Before that interruption, {}.".format(released))
        raise


def acquire_lease_for_recovery(root_fd, machine_rel, verb):
    """Claim the single-writer lease for a RECONCILIATION write (spec 5.7, 8.8 item 1, 16.1): the ordinary
    ATOMIC claim first and, exactly when it refuses because a lease is PRESENT, the spec 5.7 never-seize
    and dead-run release clauses under a per-store EXCLUSIVE EXAMINATION LOCK (_exclusive_examination,
    held from before the leftover is read until after the retried claim): a leftover lease whose complete well-formed payload
    names THIS verb's holder on THIS host CONFIRMED DEAD (positive evidence only) is released THROUGH
    this reconciliation, bound to the exact bytes examined (a lease replaced in the interval is never
    removed), and the atomic claim is retried ONCE under the same lock, so at most one of any number of
    concurrent recoveries whose locks one kernel arbitrates ever examines, releases, and re-claims, and
    such a recoverer can never remove a peer recoverer's freshly created live lease; every other present
    lease (a live or possibly-live holder, a cross-host holder, another verb's holder, a malformed,
    oversized or foreign-shape payload, an unreadable or non-regular entry) refuses exactly as
    acquire_lease does and is never seized. Returns (payload, released): the exact lease bytes this run
    wrote, and None, or the one-line report of the leftover lease this reconciliation released, which the
    caller MUST surface; every failure raised AFTER that release carries the report, whatever its type
    (_claim_after_release), the release's durability fsync included, so no failure reads as
    written-nothing over the released lease. The serialization holds only within the locking boundary
    _exclusive_examination discloses (one kernel's flock arbitration). Spec 5.7: a lease is present only
    while held, MUST NOT be seized from a live holder, and a leftover lease from a dead run MUST be
    released only through the resume-or-close reconciliation; spec 8.8 item 1 grants this reconciliation
    exactly that release; spec 16.1: after resolving the store, a command reconciles a leftover lease and
    its own writer's interrupted journal, as sections 5.7 and 8.8 require, before its admission check.
    Ordinary (non-recovery) acquisition keeps the unexamined present-is-held refusal, so a lone leftover
    with no interrupted journal behind it stays the operator's reconciliation step, matching the homes-2
    refusal of a lone lease with no paired active record."""
    journal = _opf_store._journal
    try:
        return acquire_lease(root_fd, machine_rel, verb), None
    except LeaseHeldError as held:
        lease_rel = "{}/{}".format(machine_rel, _opf_check.LEASE_NAME)
        pfd, name = journal._open_parent(root_fd, lease_rel)
        try:
            try:
                _exclusive_examination(pfd, lease_rel, verb)
            except WriteGuardError as exc:
                raise _lock_refusal(exc, held) from exc
            raw = read_lease_payload(pfd, name)
            examined = _examined_leftover_holder(raw, verb) if raw is not None else None
            if examined is None or not _lease_holder_confirmed_dead(examined[1]):
                raise held
            holder = examined[0]
            try:
                unlink_owned_lease(pfd, name, lease_rel, raw, verb)
            except WriteGuardError as exc:
                raise WriteGuardError(
                    "the leftover {} lease {} of confirmed-dead holder {!r} could not be released by this "
                    "reconciliation ({}); nothing was removed beyond what that release itself reports, and "
                    "no lease this reconciliation cannot prove is that examined leftover is ever removed "
                    "(never-seize, spec 5.7)".format(verb, lease_rel, holder, exc)) from exc
            released = ("the leftover single-writer lease {} of confirmed-dead holder {!r} was released "
                        "through this reconciliation (spec 5.7: a lease is never seized from a live "
                        "holder, and a leftover lease from a dead run is released only through this "
                        "reconciliation)".format(lease_rel, holder))
            return _claim_after_release(pfd, root_fd, machine_rel, verb, lease_rel, released), released
        finally:
            _opf_store._close_fd_exc_safe(pfd)


# --- self-test: plain vectors for the lease refusal and release messages (spec 5.7) ---------------------

def self_test():
    """Plain vectors for the lease messages, each judged against literal text and never against a constant
    of this module, so a reverted message cannot carry its expectation with it. A failed examination lock
    (held, failed, or no fcntl at all) names the live holder and never calls its lease leftover; it offers
    the confirmed-no-run remedy only when no reconciliation holds the lock, and a holder quoting that remedy
    never cuts the refusal. The held-lease and failed-acquisition refusals state the one case in which opf
    removes another run's lease, never that it removes none. An oversized lease is reported oversized, never
    by a holder read from its prefix. The ownership-verified release reports an oversized, non-regular or
    unreadable replacement as what it is, never ABSENT, and the holder diagnostic names each unread lease by
    its cause. Each vector runs over a real lease beneath a private temporary root. The porcelain grammar,
    dirty-probe, ignore-probe, lease ownership and filter-neutralization vectors follow
    (_self_test_guard_vectors). Returns 0 clean, 1 on a failure, 2 on a harness error."""
    import shutil
    import tempfile
    failures = []
    checked = [0]

    def check(name, vector):
        checked[0] += 1
        try:
            problem = vector()
        except Exception as exc:  # noqa: BLE001  a vector that raises is a failure, never a pass
            problem = "raised {!r}".format(exc)
        if problem:
            failures.append("{}: {}".format(name, problem))

    try:
        base = tempfile.mkdtemp(prefix="opf-write-guard-selftest-")
    except OSError as exc:
        print("opf-write-guard self-test: harness error (no writable temporary directory: {}); "
              "fail-closed".format(exc), file=sys.stderr)
        return 2
    try:
        _self_test_vectors(check, base)
        _self_test_guard_vectors(check, base)
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a pass
        print("opf-write-guard self-test: harness error ({!r}); fail-closed".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("opf-write-guard self-test: FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for failure in failures:
            print("  - " + failure, file=sys.stderr)
        return 1
    print("opf-write-guard self-test: PASS ({} checks: examination-lock refusals, held-lease and "
          "failed-acquisition remedies, oversized lease, release over each replacement, holder "
          "diagnostic, porcelain grammar, dirty and ignore probes, lease ownership, filter "
          "neutralization over real git)".format(checked[0]))
    return 0


def _self_test_vectors(check, base):
    """The vectors self_test runs, over the lease m/lease.toml beneath `base`."""
    import fcntl
    from unittest import mock
    os.mkdir(os.path.join(base, "m"))
    path = os.path.join(base, "m", "lease.toml")
    holder = "opf-record:{}:{}".format(socket.gethostname(), os.getpid())   # this process: a live holder
    live = ('acquired_at = "2026-10-12T00:00:00Z"\nholder = "{}"\noperation = "record"\n'
            'schema = 1\n'.format(holder)).encode("utf-8")
    remedy = "If you have confirmed NO opf run is live, "
    scope = ("release that lease as your own reconciliation step (this run has not removed the lease now "
             "present; opf removes another run's lease only while reconciling an interrupted run of the same "
             "verb, once that lease's holder is confirmed dead on this host), then re-run opf {}.")
    own = b"this run's own lease"

    def clear():
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        elif os.path.lexists(path):
            os.unlink(path)

    def plant(raw):
        clear()
        Path(path).write_bytes(raw)

    def refusal(call):
        try:
            call()
        except WriteGuardError as exc:
            return str(exc)
        return None

    def judged(text, present=(), absent=(), named=None, left=None):
        """The first problem with refusal `text`, or None: each `present` phrase in it, each `absent` phrase
        not in it outside the echoed holder `named`, and the planted lease `left` byte for byte."""
        if text is None:
            return "no refusal"
        for words in present:
            if words not in text:
                return "lacks {!r}: {}".format(words, text)
        own_words = text if named is None else text.replace(named, "")
        for words in absent:
            if words in own_words:
                return "carries {!r}: {}".format(words, text)
        if left is not None and Path(path).read_bytes() != left:
            return "the lease was changed"
        return None

    def lock_refused(code, lease):
        """acquire_lease_for_recovery over `lease`, the examination lock failing with OSError(code), or with
        fcntl unimportable when `code` is None."""
        plant(lease)
        claim = lambda: acquire_lease_for_recovery(root_fd, "m", "record")
        if code is not None:
            def failing(fd, operation):
                raise OSError(code, os.strerror(code))
            with mock.patch.object(fcntl, "flock", failing):
                return refusal(claim)
        saved = sys.modules.get("fcntl")
        sys.modules["fcntl"] = None
        try:
            return refusal(claim)
        finally:
            sys.modules["fcntl"] = saved

    def released(plant_entry, read_fault=None):
        """unlink_owned_lease over the entry `plant_entry` leaves, every read failing when `read_fault`."""
        plant_entry()
        if read_fault is None:
            return refusal(lambda: unlink_owned_lease(pfd, "lease.toml", "m/lease.toml", own, "record"))
        with mock.patch.object(os, "read", read_fault):
            return refusal(lambda: unlink_owned_lease(pfd, "lease.toml", "m/lease.toml", own, "record"))

    def read_fault(fd, size):
        raise OSError(errno.EIO, "injected read failure")

    def acquired_failing():
        """acquire_lease over no lease, its payload write failing after the create."""
        clear()

        def write_fault(fd, data):
            raise OSError(errno.EIO, "injected write failure")
        with mock.patch.object(_opf_store._journal, "_write_all", write_fault):
            return refusal(lambda: acquire_lease(root_fd, "m", "record"))

    oversized = live + b"#" * (65536 - len(live) - 1) + b"\n" + b"schema = 1\n"
    crafted = "opf-record:h If you have confirmed NO opf run is live, x:1"
    root_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        pfd = os.open(os.path.join(base, "m"), os.O_RDONLY | os.O_DIRECTORY)
        try:
            check("a held examination lock names the live holder, never leftover, with no remedy", lambda: judged(
                lock_refused(errno.EWOULDBLOCK, live),
                present=(holder, "another reconciliation is concurrently examining the record lease m/lease.toml",
                         ". Another opf run holds the single-writer lease m/lease.toml: held by '{}'".format(holder),
                         "The lease is never seized (spec 5.7)."),
                absent=("leftover", remedy, "could not be taken"), named=holder, left=live))
            check("a failed examination lock names the live holder, never leftover", lambda: judged(
                lock_refused(errno.ENOLCK, live),
                present=(holder, "the examination lock that serializes reconciling the present record lease "
                         "m/lease.toml could not be taken",
                         ". Another opf run holds the single-writer lease m/lease.toml: held by '{}'".format(holder),
                         remedy + scope.format("record")),
                absent=("leftover", "concurrently examining", "never removes a foreign lease"), named=holder,
                left=live))
            check("a platform without fcntl names the live holder, never leftover", lambda: judged(
                lock_refused(None, live),
                present=(holder, "reconciling the present record lease m/lease.toml needs the POSIX fcntl "
                         "serialization primitive",
                         ". Another opf run holds the single-writer lease m/lease.toml: held by '{}'".format(holder),
                         remedy + scope.format("record")),
                absent=("leftover",), named=holder, left=live))
            check("a holder quoting the remedy never cuts the held-lock refusal", lambda: judged(
                lock_refused(errno.EWOULDBLOCK, 'holder = "{}"\n'.format(crafted).encode("utf-8")),
                present=("held by '{}'".format(crafted), "The lease is never seized (spec 5.7)."),
                absent=("opf removes another run's lease only while",), named=crafted))
            check("the held-lease refusal states the one case opf removes another run's lease", lambda: judged(
                (plant(live), refusal(lambda: acquire_lease(root_fd, "m", "upgrade")))[1],
                present=(holder, "The lease is never seized (spec 5.7). " + remedy + scope.format("upgrade")),
                absent=("leftover", "never removes a foreign lease"), named=holder, left=live))
            check("an oversized lease is reported oversized, never by its prefix's holder", lambda: (
                "the fixture's 64 KiB prefix does not parse as the live lease"
                if tomllib.loads(oversized[:65536].decode("utf-8")).get("holder") != holder else judged(
                    (plant(oversized), refusal(lambda: acquire_lease(root_fd, "m", "record")))[1],
                    present=("a present lease with an OVERSIZED payload (over the 65536-byte bound, so it is "
                             "never read as a lease and no holder is taken from a prefix of it)",),
                    absent=(holder,), left=oversized)))
            check("a failed acquisition states the one case opf removes another run's lease", lambda: judged(
                acquired_failing(),
                present=("the lease is LEFT in place as a leftover from this failed record",
                         "(this run never removes it; opf removes another run's lease only while reconciling an "
                         "interrupted run of the same verb, once that lease's holder is confirmed dead on this "
                         "host), then re-run opf record."),
                absent=("the tool never removes it",)))
            check("an oversized replacement is released as oversized, never ABSENT", lambda: judged(
                released(lambda: plant(b"#" * 70000)),
                present=("the lease was REPLACED before release by an OVERSIZED payload (over the 65536-byte "
                         "bound, never read as a lease, so no holder is named)",),
                absent=("ABSENT",), left=b"#" * 70000))
            check("a directory replacement is released as non-regular, never ABSENT", lambda: judged(
                released(lambda: (clear(), os.mkdir(path))),
                present=("the lease was REPLACED before release by a non-regular entry",), absent=("ABSENT",))
                or (None if os.path.isdir(path) else "the replacement was removed"))
            check("a symlink replacement is released as non-regular, never ABSENT", lambda: judged(
                released(lambda: (clear(), os.symlink("lease.toml.elsewhere", path))),
                present=("the lease was REPLACED before release by a non-regular entry",), absent=("ABSENT",))
                or (None if os.path.islink(path) else "the replacement was removed"))
            check("an unreadable lease is released as unreadable, never ABSENT", lambda: judged(
                released(lambda: plant(live), read_fault),
                present=("the lease present at release could not be read, unreadable (",
                         "injected read failure", "so this run cannot prove it is its own"),
                absent=("ABSENT",), left=live))
            check("an absent lease is released as ABSENT, never a non-regular entry", lambda: judged(
                released(clear),
                present=("the lease is ABSENT at release (already removed); this run's own lease is gone",),
                absent=("non-regular",)))
        finally:
            os.close(pfd)
    finally:
        os.close(root_fd)

    def diagnosed(cause, words):
        said = lease_holder_of(None) if cause is None else lease_holder_of(None, cause)
        return None if said == words else "said {!r}".format(said)

    for cause, words in (("absent", "no holder named (the lease is absent)"),
                         ("non-regular", "no holder named (the lease is a non-regular entry)"),
                         ("oversized", "no holder named (the lease payload is OVERSIZED (over the 65536-byte "
                          "bound))"),
                         ("unreadable (injected read failure)", "no holder named (the lease is unreadable "
                          "(injected read failure))"),
                         (None, "no holder named (the lease was not read)")):
        check("the holder diagnostic names an unread lease by its cause ({})".format(cause or "none given"),
              lambda cause=cause, words=words: diagnosed(cause, words))


def _self_test_guard_vectors(check, base):
    """The porcelain grammar, dirty-probe, ignore-probe and lease ownership vectors self_test runs. They
    were carried by the removed store-upgrade gate; `opf record` still uses each guard they exercise.
    parse_porcelain is called directly; probe_dirty and check_ignored run over a stubbed git; the lease
    vectors run over the lease g/lease.toml beneath `base`; the lease-removal docstring keeps its disclosed
    residual; the filter vectors (_self_test_filter_vectors) run probe_dirty over real git."""
    from unittest import mock
    obs = _opf_observe
    outcome = obs._GitOutcome
    target = ".working/CONTRIBUTIONS.md"
    lease = ".working/toml/lease.toml"

    def refused(call):
        try:
            call()
        except WriteGuardError:
            return None
        return "no refusal"

    def parsed(raw, prefix="", lease_rel=None, specs=(".working",)):
        """(records, None) for parse_porcelain over `raw`, or (None, its refusal)."""
        try:
            return parse_porcelain(raw, prefix, lease_rel, list(specs), "record"), None
        except WriteGuardError as exc:
            return None, str(exc)

    def records(raw, wanted, **kwargs):
        got, error = parsed(raw, **kwargs)
        return None if error is None and got == wanted else "got {!r} ({})".format(got, error)

    def refusal(raw, **kwargs):
        got, error = parsed(raw, **kwargs)
        return None if got is None and error is not None else "parsed as {!r}".format(got)

    # Git diagnostics on stderr: an ignore input git could not read refuses, whatever the exit code, and a
    # quiet non-match passes; a status diagnostic refuses on the matching and the expanded probe alike.
    with mock.patch.object(obs, "_git_path", return_value="git"), \
            mock.patch.object(obs, "_filter_neutralizing_config", return_value=[]):
        for source in (".gitignore", ".git/info/exclude", "core.excludesFile"):
            warning = "warning: unable to access '{}': Permission denied\n".format(source)
            for rc, payload in ((1, b""), (0, b"./.working/CONTRIBUTIONS.md\x00")):
                with mock.patch.object(obs, "_run_git", return_value=outcome(True, rc, payload, warning)):
                    check("an ignore-probe diagnostic refuses ({}, rc {})".format(source, rc),
                          lambda: refused(lambda: check_ignored("/fixture", [target], "record")))
        with mock.patch.object(obs, "_run_git", return_value=outcome(True, 1, b"", "")):
            check("a quiet ignore-probe non-match passes",
                  lambda: None if refused(lambda: check_ignored("/fixture", [target], "record")) else "refused")

        for where in ("matching", "expanded"):
            def status(_git, _root, args, where=where, **kwargs):
                if "rev-parse" in args:
                    return outcome(True, 0, b"\n", "")
                if "ls-files" in args:
                    return outcome(True, 0, b"", "")
                expanded = "--ignored=traditional" in args
                err = "warning: cannot read ignore input\n" if expanded == (where == "expanded") else ""
                return outcome(True, 0, b"" if expanded else b"!! .working/\x00", err)
            with mock.patch.object(obs, "_run_git", side_effect=status):
                check("a status diagnostic on the {} probe refuses".format(where),
                      lambda: refused(lambda: probe_dirty("git", "/fixture", [target], None, "record")))

        # Index flags: H (tracked, ordinary) passes; S, h and s (skip-worktree, assume-unchanged) refuse.
        for tag in (b"H", b"S", b"h", b"s"):
            def index_flags(_git, _root, args, tag=tag, **kwargs):
                raw = tag + b" " + os.fsencode(target) + b"\x00" if "ls-files" in args else b""
                return outcome(True, 0, raw, "")
            with mock.patch.object(obs, "_run_git", side_effect=index_flags):
                check("index flag {!r} {}".format(tag, "passes" if tag == b"H" else "refuses"),
                      lambda tag=tag: (
                          refused(lambda: probe_dirty("git", "/fixture", [target], None, "record"))
                          if tag != b"H" else
                          None if refused(lambda: probe_dirty("git", "/fixture", [target], None, "record"))
                          else "refused"))
        for tag in (b"M", b"m"):
            def unmerged(_git, _root, args, tag=tag, **kwargs):
                raw = ((tag + b" " + os.fsencode(target) + b"\x00") * 3
                       if "ls-files" in args else
                       b"UU " + os.fsencode(target) + b"\x00" if "status" in args else b"\n")
                return outcome(True, 0, raw, "")

            def names_conflict():
                try:
                    probe_dirty("git", "/fixture", [target], None, "record")
                except WriteGuardError as exc:
                    text = str(exc)
                    return None if (target in text and "is unmerged; resolve the conflict" in text
                                    and "malformed" not in text) else "said {!r}".format(text)
                return "no refusal"
            with mock.patch.object(obs, "_run_git", side_effect=unmerged):
                check("an unmerged index entry {!r} refuses naming the conflict".format(tag), names_conflict)
        for bad in (b"H", b"H path", b"\x00", b"? path\x00"):
            def bad_flags(_git, _root, args, bad=bad, **kwargs):
                return outcome(True, 0, bad if "ls-files" in args else b"", "")
            with mock.patch.object(obs, "_run_git", side_effect=bad_flags):
                check("malformed index flags {!r} refuse".format(bad),
                      lambda: refused(lambda: probe_dirty("git", "/fixture", [target], None, "record")))

    # A tracked record beneath the lease path is a spec 5.7 anomaly and refuses; an untracked or ignored one
    # is the held-lease exclusion.
    for pair in (b" M", b"M ", b" D"):
        def tracked(pair=pair):
            try:
                parse_porcelain(pair + b" " + os.fsencode(lease) + b"/child\x00", b"", lease, [lease], "record")
            except WriteGuardError as exc:
                return None if "TRACKED" in str(exc) else "said {!r}".format(str(exc))
            return "no refusal"
        check("a tracked lease descendant {!r} refuses".format(pair), tracked)
    for pair in (b"??", b"!!"):
        check("a held lease descendant {!r} is excluded".format(pair),
              lambda pair=pair: records(pair + b" " + os.fsencode(lease) + b"/child\x00", [],
                                        prefix=b"", lease_rel=lease, specs=(lease,)))

    # The porcelain -z grammar: a malformed payload refuses, never reads as clean.
    check("an empty payload is clean", lambda: records(b"", []))
    check("a well-formed untracked record parses", lambda: records(b"?? .working/toml/x\x00", [".working/toml/x"]))
    check("a lone NUL refuses", lambda: refusal(b"\x00"))
    check("a payload without its NUL terminator refuses", lambda: refusal(b"?? .working/toml/x"))
    check("a record without status framing refuses", lambda: refusal(b"XYZno-space\x00"))
    check("a nested store's lease is excluded after the prefix strip",
          lambda: records(b"?? sub/.working/toml/lease.toml\x00", [], prefix="sub/", lease_rel=lease))
    # An out-of-vocabulary or impossible status pair refuses on the lease path (where a silent drop would read
    # dirt as clean) and on any other path: the whole XY pair is checked against git's enumeration.
    for bad, label in ((b"ZZ", "out-of-vocabulary ZZ"), (b"  ", "blank status"), (b"R ", "rename R"),
                       (b"C ", "copy C"), (b"UT", "UT"), (b"TU", "TU"), (b"DM", "DM"), (b"DT", "DT"),
                       (b"DA", "DA"), (b"MA", "MA"), (b"TA", "TA")):
        check("status {} refuses on the lease path".format(label),
              lambda bad=bad: refusal(bad + b" " + os.fsencode(lease) + b"\x00", lease_rel=lease))
        check("status {} refuses on another path".format(label),
              lambda bad=bad: refusal(bad + b" .working/toml/x\x00", lease_rel=lease))
    # Every pair git emits for this command parses: ??, !!, the 17 ordinary pairs and the 7 unmerged pairs.
    for pair in (b"??", b"!!", b" A", b" M", b" T", b" D", b"M ", b"MM", b"MT", b"MD", b"T ", b"TM", b"TT",
                 b"TD", b"A ", b"AM", b"AT", b"AD", b"D ", b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"):
        check("emittable pair {!r} parses".format(pair),
              lambda pair=pair: records(pair + b" .working/toml/x\x00", [".working/toml/x"]))
    check("an untracked exact lease is excluded",
          lambda: records(b"?? " + os.fsencode(lease) + b"\x00", [], lease_rel=lease))
    check("an ignored exact lease beneath a prefix is excluded",
          lambda: records(b"!! sub/" + os.fsencode(lease) + b"\x00", [], prefix="sub/", lease_rel=lease))
    for pair in (b" D", b"D ", b" M", b"MM", b"M ", b"MD", b"A ", b"AD", b"DD", b"AU", b"UU"):
        def tracked_lease(pair=pair):
            got, error = parsed(pair + b" " + os.fsencode(lease) + b"\x00", lease_rel=lease)
            return None if got is None and error is not None and "5.7" in error else "parsed as {!r}".format(got)
        check("a tracked lease status {!r} refuses under spec 5.7".format(pair), tracked_lease)
    check("a dirty record off the lease path is surfaced",
          lambda: records(b" M .working/toml/x\x00", [".working/toml/x"], lease_rel=lease))
    # Scope filtering applies to well-formed ignored records only, by path component.
    for payload, wanted in ((b"!! .working\x00", [".working"]), (b"!! .working/\x00", [".working/"]),
                            (b"!! .working/child\x00", [".working/child"]), (b"!! .working.bak/\x00", []),
                            (b"?? .working.bak/child\x00", [".working.bak/child"]),
                            (b" M .working.bak/child\x00", [".working.bak/child"])):
        check("component scope {!r}".format(payload), lambda payload=payload, wanted=wanted: records(payload, wanted))
    check("a malformed status out of scope still refuses", lambda: refusal(b"ZZ .working.bak/child\x00"))
    # Byte paths stay lossless through prefix stripping, lease exclusion and scope matching.
    byte_prefix, byte_path = b"nested-\xff/", b".working/\xfe"
    check("a byte path keeps its filesystem bytes",
          lambda: records(b"!! " + byte_prefix + byte_path + b"\x00", [os.fsdecode(byte_path)],
                          prefix=byte_prefix, specs=(os.fsdecode(byte_path),)))
    check("a byte prefix still excludes the lease",
          lambda: records(b"!! " + byte_prefix + os.fsencode(lease) + b"\x00", [], prefix=byte_prefix,
                          lease_rel=lease))
    for bad_path in (b"elsewhere/file", b"sub/", b"sub//file", b"sub/../file", b"sub/./file", b"/sub/file"):
        check("an unnormalizable ignored path {!r} refuses".format(bad_path),
              lambda bad_path=bad_path: refusal(b"!! " + bad_path + b"\x00", prefix="sub/"))

    # The show-prefix strip removes only the trailing newline, so a store directory whose name begins with a
    # space keeps its prefix and its untracked lease stays excluded.
    def leading_space_git(_git, _root, args, **kwargs):
        if "ls-files" in args:
            return outcome(True, 0, b"", "")
        if "rev-parse" in args:
            return outcome(True, 0, b" leading/\n", b"")
        if "config" in args:
            return outcome(True, 0, b"", b"")
        return outcome(True, 0, b"??  leading/.working/toml/lease.toml\x00", b"")

    def leading_space():
        with mock.patch.object(obs, "_run_git", leading_space_git):
            dirty = probe_dirty("git", base, [".working"], lease, "record")
        return None if dirty == [] else "surfaced {!r}".format(dirty)
    check("a leading-space store prefix keeps the lease excluded", leading_space)

    # Lease ownership: a failed acquisition leaves its lease as a leftover, a lease a peer substituted in that
    # window survives, and release removes this run's own lease but never a peer's replacement.
    os.mkdir(os.path.join(base, "g"))
    path = os.path.join(base, "g", "lease.toml")
    peer = b'acquired_at = "2026-02-02T00:00:00Z"\nholder = "peer-runner"\noperation = "record"\nschema = 1\n'

    def clear():
        if os.path.lexists(path):
            os.unlink(path)

    root_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        def failed_acquisition():
            clear()

            def write_fault(fd, data):
                raise OSError(errno.EIO, "injected write failure")
            with mock.patch.object(_opf_store._journal, "_write_all", write_fault):
                try:
                    acquire_lease(root_fd, "g", "record")
                except WriteGuardError as exc:
                    if "never seized" not in str(exc):
                        return "said {!r}".format(str(exc))
                else:
                    return "no refusal"
            return None if os.path.isfile(path) else "the lease was removed"
        check("a failed acquisition leaves its lease in place", failed_acquisition)

        def replaced_acquisition():
            clear()

            def replace_then_fail(fd, data):
                os.unlink(path)
                Path(path).write_bytes(peer)
                raise OSError(errno.EIO, "injected write failure after a peer replaced the lease")
            with mock.patch.object(_opf_store._journal, "_write_all", replace_then_fail):
                if refused(lambda: acquire_lease(root_fd, "g", "record")) is not None:
                    return "no refusal"
            return None if Path(path).read_bytes() == peer else "the peer's lease was removed or changed"
        check("a failed acquisition never removes a peer's replacement", replaced_acquisition)

        def replaced_release():
            clear()
            payload = acquire_lease(root_fd, "g", "record")
            if payload != Path(path).read_bytes():
                return "acquire did not return the on-disk payload"
            os.unlink(path)
            Path(path).write_bytes(peer)
            try:
                release_lease(root_fd, "g", payload, "record")
            except WriteGuardError as exc:
                text = str(exc)
                if ("never seized" not in text and "NEVER seized" not in text) or "peer-runner" not in text:
                    return "said {!r}".format(text)
            else:
                return "no refusal"
            if not os.path.isfile(path) or Path(path).read_bytes() != peer:
                return "the peer's replacement was removed or changed"
            clear()
            Path(path).write_bytes(payload)
            release_lease(root_fd, "g", payload, "record")
            return None if not os.path.lexists(path) else "this run's own lease was not removed"
        check("release refuses a peer's replacement and removes this run's own lease", replaced_release)
    finally:
        os.close(root_fd)

    # The ownership-verified release keeps its disclosed residual: the false "released" over a swapped peer
    # lease, reachable only when the release-only-when-no-run-is-live reconciliation is violated. Read from
    # the source, so the check holds under -OO too.
    import _optlevel
    disclosure = " ".join((_optlevel.source_docstring(__file__, "unlink_owned_lease") or "").lower().split())
    check("the lease-removal residual discloses the false release and its reachability", lambda: None if (
        "reports exit-0 success" in disclosure and 'false "released"' in disclosure
        and "release-only-when-no-run-is-live" in disclosure) else "the disclosure is incomplete")

    _self_test_filter_vectors(check, base)


def _self_test_filter_vectors(check, base):
    """probe_dirty over real git repositories beneath `base`: a repository- or worktree-configured clean or
    process filter never runs during the dirty probe (its command would create a sentinel), whatever its
    name, while real dirt is still detected and git's built-in text/eol conversion is kept. A corrupt
    configuration fails closed, and a normalizing filter of the git-lfs shape over-refuses (the record gate
    drives its refusal note end to end). An un-neutralized status runs each planted filter (the positive
    control), and the neutralizer stubbed to return [] or rendered as the old `-c key=value` argv runs it
    again (the flips)."""
    import subprocess
    from unittest import mock
    obs = _opf_observe
    git = obs._git_path()
    if git is None:
        check("the real-git filter vectors find git", lambda: "git is not on PATH")
        return
    lease = ".working/toml/lease.toml"
    target = ".working/toml/pwn.dat"
    future = (4102444800, 4102444800)   # 2100-01-01: a fixed future mtime, so status must re-hash the target
    status_args = ["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                   "--no-renames", "--", ".working"]

    def refused(call):
        try:
            call()
        except WriteGuardError:
            return None
        return "no refusal"

    def fixture_git(root, *args):
        proc = subprocess.run(
            [git, "-C", str(root), "-c", "init.defaultBranch=main", "-c", "user.email=gate@example.invalid",
             "-c", "user.name=gate", "-c", "commit.gpgsign=false", "-c", "gc.auto=0",
             "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=obs._scrubbed_env(), timeout=60)
        if proc.returncode != 0:
            raise OSError("fixture git {} failed: {}".format(args, proc.stderr.decode("utf-8", "replace")))
        return proc.stdout

    def store(name, files=None):
        """A committed repository holding .working/toml/pwn.dat and `files` (relpath -> bytes)."""
        root = Path(base, "filter", name)
        (root / ".working" / "toml").mkdir(parents=True)
        fixture_git(root, "init", "-q")
        for rel, data in {target: b"hello\n", **(files or {})}.items():
            (root / rel).write_bytes(data)
        fixture_git(root, "--literal-pathspecs", "add", "-A")
        fixture_git(root, "commit", "-q", "-m", "seed")
        return root

    def filtered(name, driver, form="clean", required=None, worktree=False):
        """A committed store whose pwn.dat a `driver` filter claims; its command creates <store>/SENTINEL."""
        root = store(name)
        scope = ["--worktree"] if worktree else []
        if worktree:
            fixture_git(root, "config", "extensions.worktreeConfig", "true")
        fixture_git(root, "config", *scope, "filter.{}.{}".format(driver, form),
                    "sh -c 'touch \"{}\"; cat'".format(root / "SENTINEL"))
        if required is not None:
            fixture_git(root, "config", *scope, "filter.{}.required".format(driver), str(required).lower())
        (root / ".gitattributes").write_text("{} filter={}\n".format(target, driver), encoding="utf-8")
        return root

    def reset(root):
        """Remove any sentinel and bump pwn.dat's mtime, content unchanged, so the next status re-hashes it."""
        Path(root, "SENTINEL").unlink(missing_ok=True)
        os.utime(str(Path(root, target)), future)

    def probe(root, lease_rel=lease):
        return probe_dirty(git, str(root), [".working"], lease_rel, "record")

    def ran(root, call):
        """Whether `call` ran the planted filter, after a reset."""
        reset(root)
        call()
        return Path(root, "SENTINEL").exists()

    def suppressed(root, lease_rel=lease):
        """None when an un-neutralized status runs the filter and the probe neither runs it nor reads dirt."""
        if not ran(root, lambda: obs._run_git(git, str(root), status_args)):
            return "the positive control did not run the planted filter"
        reset(root)
        dirty = probe(root, lease_rel)
        if Path(root, "SENTINEL").exists():
            return "the dirty probe ran the planted filter"
        return None if dirty == [] else "an unchanged store read dirty: {!r}".format(dirty)

    for label, name, args in (("a clean filter", "clean", ("pwn",)),
                              ("a process filter", "process", ("pwn", "process")),
                              ("a dotted filter name", "dotted", ("a.b",)),
                              ("a filter name holding '='", "equals", ("pwn=bypass",)),
                              ("a required filter", "required", ("pwn", "clean", True)),
                              ("a worktree-scoped filter", "worktree", ("wpwn", "clean", None, True))):
        check("{} never runs during the dirty probe".format(label),
              lambda name=name, args=args: suppressed(filtered(name, *args)))
    check("a filter on a product-root binding never runs during the dirty probe",
          lambda: suppressed(filtered("product", "ppwn"), lease_rel=None))

    def unneutralized():
        root = filtered("flip-empty", "pwn")
        with mock.patch.object(obs, "_filter_neutralizing_config", return_value=[]):
            return None if ran(root, lambda: probe(root)) else "the probe without neutralization ran no filter"
    check("FLIP: a probe without neutralization runs the planted filter", unneutralized)

    def old_argv():
        root = filtered("flip-argv", "pwn=bypass")
        argv = []
        for key, value in obs._filter_neutralizing_config(git, str(root)):
            argv += ["-c", "{}={}".format(key, value)]
        return None if ran(root, lambda: obs._run_git(git, str(root), argv + status_args)) else (
            "the -c key=value form neutralized the '='-named filter")
    check("FLIP: the old -c key=value form leaves a filter name holding '=' executable", old_argv)

    def required_unreleased():
        root = filtered("flip-required", "pwn", required=True)
        reset(root)
        with mock.patch.object(obs, "_filter_neutralizing_config",
                               return_value=[("filter.pwn.clean", ""), ("filter.pwn.process", "")]):
            return refused(lambda: probe(root))
    check("FLIP: emptying a required filter without required=false breaks the probe", required_unreleased)

    def overrides():
        root = store("overrides")
        fixture_git(root, "config", "filter.pwn.clean", "sh -c cat")
        fixture_git(root, "config", "filter.lfs.smudge", "git-lfs smudge -- %f")
        got = obs._filter_neutralizing_config(git, str(root))
        return None if got == [("filter.pwn.clean", ""), ("filter.pwn.process", ""),
                               ("filter.pwn.required", "false")] else "got {!r}".format(got)
    check("the neutralizer empties a clean driver with three overrides and leaves a smudge-only driver",
          overrides)

    # Real dirt is still detected under neutralization, without running the filter.
    def modified(root):
        Path(root, target).write_bytes(b"changed\n")

    def untracked(root):
        Path(root, ".working/toml/new.txt").write_bytes(b"new\n")
        return ".working/toml/new.txt"

    def staged(root):
        Path(root, target).write_bytes(b"staged change\n")
        fixture_git(root, "--literal-pathspecs", "add", "--", target)

    for label, change in (("an unstaged modification", modified), ("an untracked file", untracked),
                          ("a deleted tracked file", lambda root: Path(root, target).unlink()),
                          ("a staged change", staged),
                          ("an executable-mode change", lambda root: os.chmod(str(Path(root, target)), 0o755))):
        def detected(label=label, change=change):
            root = filtered("dirt-" + label.replace(" ", "-"), "pwn")
            wanted = change(root) or target
            Path(root, "SENTINEL").unlink(missing_ok=True)
            dirty = probe(root)
            if wanted not in dirty:
                return "not detected: {!r}".format(dirty)
            return "the planted filter ran" if Path(root, "SENTINEL").exists() else None
        check("{} is detected dirty under neutralization".format(label), detected)

    def eol_kept():
        root = store("eol", {".gitattributes": b".working/toml/text.dat text eol=crlf\n",
                             ".working/toml/text.dat": b"a\nb\nc\n"})
        Path(root, ".working/toml/text.dat").unlink()
        fixture_git(root, "--literal-pathspecs", "checkout", "--", ".working/toml/text.dat")
        if b"\r\n" not in Path(root, ".working/toml/text.dat").read_bytes():
            return "the fixture's worktree file is not CRLF"
        os.utime(str(Path(root, ".working/toml/text.dat")), future)
        dirty = probe(root)
        return None if dirty == [] else "the CRLF worktree read dirty: {!r}".format(dirty)
    check("built-in text/eol conversion is kept: a CRLF worktree of an LF blob reads clean", eol_kept)

    def corrupt():
        root = filtered("corrupt", "pwn")
        Path(root, ".git", "config").write_text("[this is not valid\n = = =\n", encoding="utf-8")
        try:
            obs._filter_neutralizing_config(git, str(root))
        except RuntimeError:
            return refused(lambda: probe(root))
        return "the neutralizer read a corrupt configuration"
    check("a corrupt configuration fails closed in the neutralizer and the probe", corrupt)

    def normalizing():
        """The git-lfs shape: the index holds the cleaned blob and the worktree the uncleaned body."""
        root = store("normalizing")
        fixture_git(root, "config", "filter.norm.clean", "sed 's/[0-9]//g'")
        fixture_git(root, "config", "filter.norm.required", "true")
        Path(root, ".gitattributes").write_text(".working/norm.md filter=norm\n", encoding="utf-8")
        Path(root, ".working/norm.md").write_bytes(b"abc123\n")
        fixture_git(root, "--literal-pathspecs", "add", "-A")
        fixture_git(root, "commit", "-q", "-m", "a normalizing filter")
        if fixture_git(root, "show", ":.working/norm.md") != b"abc\n":
            return "the fixture's index blob is not the cleaned body"
        os.utime(str(Path(root, ".working/norm.md")), future)
        native = obs._run_git(git, str(root), status_args)
        if not native.completed or native.out != b"":
            return "the store is not clean with its filter active"
        os.utime(str(Path(root, ".working/norm.md")), future)
        if ".working/norm.md" not in probe(root):
            return "the normalizing store read clean under neutralization"
        Path(root, ".working/toml/genuine.txt").write_bytes(b"real\n")
        dirty = probe(root)
        return None if ".working/toml/genuine.txt" in dirty else "real dirt was missed: {!r}".format(dirty)
    check("a normalizing filter over-refuses under neutralization and real dirt is still detected",
          normalizing)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_write_guard.py --self-test", file=sys.stderr)
    sys.exit(2)
