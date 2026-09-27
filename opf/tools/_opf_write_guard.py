#!/usr/bin/env python3
"""The pre-mutation guards the in-place OPF writers share: write-scope planning, working-tree cleanliness, and
the single-writer lease (spec 5.7).

`opf upgrade` (spec 9.2) and `opf record` (spec 8.8) both rewrite an in-repo store in place and offer the
uncommitted change for the adopter's own review and merge, so both need the same shell around the mutation:
plan the destinations (the operation's own store files plus every declared view the post-mutation render
writes), prove the working tree is clean over exactly that scope (including ignored files, so HEAD is a
verified restore path), and claim the lease atomically, hold it across mutation, render, and final doctor,
and release it only as this run's own. These helpers were MOVED here from opf.py (they are not a copy); each
takes the calling verb explicitly so its refusals name the operation the operator actually ran.

Stdlib only; the sibling _opf_* helpers are imported directly. Every refusal is a WriteGuardError, which the
calling verb maps to exit 2. Nothing here stages or commits anything.
"""
import datetime
import os
import socket
import stat
import sys
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

def lease_held_message(pfd, name, lease_rel, verb):
    """Compose the EEXIST held-lease refusal, best-effort naming the existing holder/operation/acquired_at.
    A present-but-unreadable or malformed payload STILL refuses (present-is-held, matching C-LEASE); the
    lease is never seized or overwritten (spec 5.7). Names the manual reconciliation remedy the tool never
    performs itself."""
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
                raw = os.read(fd, 65536)
                data = tomllib.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    detail = "held by {!r} (operation {!r}, acquired_at {!r})".format(
                        data.get("holder"), data.get("operation"), data.get("acquired_at"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001  a present-but-unreadable lease still refuses (present is held)
        pass
    return ("another opf run holds the single-writer lease {}: {}. The lease is never seized (spec 5.7). "
            "If you have confirmed NO opf run is live, release the leftover lease as your own reconciliation "
            "step (the tool never removes a foreign lease), then re-run opf {}.".format(lease_rel, detail, verb))


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
            raise WriteGuardError(lease_held_message(pfd, name, lease_rel, verb))
        try:
            try:
                journal._write_all(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
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
                "(the tool never removes it), then re-run opf {}.".format(
                    verb, lease_rel, exc, verb, verb)) from exc
    finally:
        os.close(pfd)
    return payload


def read_lease_payload(pfd, name):
    """Read the lease at (pfd, name) SAFELY for an ownership compare, reusing the round-2 R3 pattern:
    O_RDONLY|O_NOFOLLOW|O_NONBLOCK (a planted FIFO or other special file cannot BLOCK the open), then fstat
    the opened fd and, for any NON-REGULAR file, return None WITHOUT reading. Returns the file's raw bytes,
    or None when it is absent, non-regular, or unreadable. Bounded read (never trusts the on-disk size)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, 65536)
    except OSError:
        return None
    finally:
        os.close(fd)


def lease_holder_of(raw):
    """Best-effort holder identity from raw lease bytes, for a DIAGNOSTIC message only (never raises, never
    load-bearing: the ownership decision is the full-payload byte compare in unlink_owned_lease)."""
    if raw is None:
        return "absent or a non-regular entry"
    try:
        data = tomllib.loads(raw.decode("utf-8"))
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
    violates the documented release-only-when-no-run-is-live reconciliation (spec 5.7). It is vastly smaller
    than the prior ownership-blind unlink and never-seizes under any non-adversarial-mid-window sequence."""
    on_disk = read_lease_payload(pfd, name)
    if on_disk != expected_payload:
        # FIX3: distinguish a genuine ABSENCE (the lease was deleted, not replaced) from a REPLACEMENT (a
        # present-but-different payload). Both stay fail-closed / never-seize; only the operator-facing
        # wording differs. read_lease_payload returns None for absent, non-regular, or unreadable.
        if on_disk is None:
            detail = ("the lease is ABSENT at release (already removed, or present as a non-regular entry); "
                      "this run's own lease is gone")
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
        os.close(pfd)
