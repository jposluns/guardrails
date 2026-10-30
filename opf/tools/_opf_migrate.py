"""OPF-MIGRATE: `opf migrate --store <target>` (spec 5.4), the whole-store relocation verb, for local
`dir:` targets with the local-directory provider only.

This build relocates a RESOLVED store between local git repositories, in every direction a `dir:`
target can name: the in-repo default store into its own local repository, one local repository into
another, and a relocated local store back into the product repository (which is prepared as a review
branch and lands through normal review, never a commit to the current branch). A `git:`, `github:`,
`gitlab:` or bare-URL target, and a bare filesystem-path target, refuse fail-closed: relocating to a
remote needs the sync contract (spec 5.7) a later unit supplies.

The operation is one ordered, journaled sequence. It refuses (exit 2, nothing relocated) unless the
source resolves, its manifest declares exactly spec_version 2.0.0 with homes = 2 and no dedicated
sync target, the single-writer lease is free, the tracked `.working/` tree equals HEAD with no merge
in progress, the source doctor is VALID, no journal transaction is open and no staging run exists,
no approved non-occupying move still waits on a source the relocation would strand outside the
product root, the destination is empty (a new directory, an empty directory, or a commitless
repository) and is not the source, and no `url.*.insteadOf` / `url.*.pushInsteadOf` configuration
visible to the transfer would rewrite the destination. History is preserved by replaying every
commit that touches `.working/` (parents simplified to that set), commit for commit, with the
original author, committer, dates and messages; commit ids are rewritten by the extraction and the
old-to-new mapping is journaled and disclosed. The destination is verified through an independent
clone-back (per-commit `.working` tree identity, the worklog, its archive and `counters.toml` byte
for byte, every carried adoption-archived copy's digest re-verified against its persisted plan)
before the product change is staged: the committed pointer is rewritten to a publishable relative
`dir:` spelling and, for an in-repo source, the tracked `.working/` tree leaves the working tree and
index, all left UNCOMMITTED for review. The machine-local id reservations under
`journals/*/allocations/` are carried byte for byte, untracked, into a local-directory destination;
a relocation into the product repository cannot carry them until its review branch lands (a
disclosed residual, the same shape as any fresh clone of the store). The
relocation is then recorded as a
worklog entry at the destination, through the record verb's own journaled append, naming both
locations, and the final doctor must be VALID through the new pointer. An external old location is
retained in place as the archived copy of the move (spec 5.4 removes OR archives); the recorded
entry names it.

Interruption is journaled: each phase writes a frame under `opf-reloc/<run-id>/` in the PRODUCT
repository's git common directory (beside the operation-lock substrate), outside the tree being
relocated. The next `opf migrate` reconciles an interrupted run FIRST, under the lease, then refuses
its own operation: before verification nothing durable was staged, so the run is marked reconciled
and any partially written destination is reported, never deleted; at or after verification the run
completes forward when the source still holds its journaled state, and rolls the staged product
change back (pointer preimage restored, `.working/` restored from HEAD) when it does not. Recovery
fails closed on a missing journal (spec 4.2/5.4): a journal frame that is unreadable, incomplete or
gone refuses rather than guessing, and a staged, uncommitted pointer change that no journal run
accounts for refuses the next operation outright.

The relocation contract is the published one. The run-id grammar is _opf_store's journal-only
`relocation` kind with its reserved `reloc-` prefix (spec 4.2); only the journal HOME is this
module's own, because the spec puts the migrate journal in the git common directory (spec 5.4),
beside the operation-lock substrate, where it survives the removal of an in-repo source tree, and
the store constructors can name only the in-tree `journals/relocation/` home. The journal, the
destination repository, the pointer files and the temporary clone-back are the only state written
outside `.working/` (spec 4.2). The worklog entry is a separate journaled append at the destination
after verification, outside the counters ban on the transfer itself (spec 8.2). The committed
pointer stays publishable as a relative `dir:` path, refused before anything is written otherwise
(spec 4.3/5.4). Rewritten commit ids with lost signatures are a disclosed residual (spec 17). A
relocation into the product repository lands through a review branch (spec 5.4). `github:` and
`gitlab:` targets refuse as unregistered providers until a reviewed registration lands (spec 5.6).
A destination outside the product root refuses while any admitted adoption run whose retirement is
unrecorded holds a default Move row (spec 14.2). Where the adoption home exists, the relocation
must pass the completion check 5 probe at the destination before the old tree is removed
(spec 14.1/14.2): this build supplies no wiring prober and the shipped enforcement pack carries no
live probe adapter, so that probe cannot pass here and a store whose adoption home exists refuses
fail-closed. The spec 14.1 append-only renewal record is owed only by a relocation that completes
over such a store, so no relocation this build performs owes one; its writer belongs to the build
that can pass the probe, whose doctor must first admit the renewal file that today's evidence
enumeration reads as off-inventory. The bundle bytes themselves relocate unchanged: the clone-back
requires the whole `imported/` tree identical at HEAD, so `plan.toml`, the approval and the sealed
inventories stay byte-identical (spec 14.1).
"""

import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _journal       # noqa: E402  journal-state classification for the open-transaction refusal
import _opf_check     # noqa: E402  the whole-store validator (the doctor engine)
import _opf_observe   # noqa: E402  scrubbed git environment and the doctor observation gather
import _opf_oplock    # noqa: E402  the shared single-writer operation lease
import _opf_record    # noqa: E402  the journaled worklog append that records the relocation
import _opf_store     # noqa: E402  store resolution, target grammar, fixed names

EXIT_OK = 0
EXIT_MALFORMED = 2

_GIT_TIMEOUT_S = 120
# The journal-only store kind this verb runs as: its reserved `reloc-` prefix and run-id grammar
# come from _opf_store's journal vocabulary (spec 4.2), never from a private copy.
_RELOCATION_KIND = "relocation"
# The relocation journal home, in the PRODUCT repository's git common directory (never inside the
# tree being relocated), beside the operation-lock substrate (spec 5.4). The store constructors
# name only the in-tree `journals/relocation/` home, which cannot express this directory, so the
# home is the one piece of the journal addressing that is this module's own.
JOURNAL_DIRNAME = "opf-reloc"
# The sealed retirement inventory of an adoption run's bundle: its presence is how a retirement is
# recorded (spec 14.1/14.2), so its absence is the unrecorded state the stranded-move refusal reads.
_RETIREMENT_INVENTORY_NAME = "inventory-retirement.toml"
# The host-convenience providers the reference tooling does not register: until a reviewed provider
# registration lands, a github: or gitlab: target refuses as an unregistered provider (spec 5.6).
_UNREGISTERED_PROVIDERS = ("github", "gitlab")
# The ordered phase frames one run writes; reconciliation reads presence, never mtimes.
_FRAMES = ("intent", "extracted", "transferred", "verified", "product-written", "recorded",
           "complete", "reconciled")
# A committed pointer must stay publishable (spec 4.3): the relative dir: spelling is held to this
# conservative alphabet so no control character, backslash or quoting hazard lands in .opf.toml.
_POINTER_SAFE_RE = re.compile(r"[A-Za-z0-9._/ -]+\Z")
# The identity the relocation's own commits carry (the review-branch merge and its worklog commit).
_IDENT_NAME = "opf migrate"
_IDENT_EMAIL = "opf-migrate@localhost"

# Interruption seam for the self-test only: when set, it is called at each phase boundary with
# (phase, context) and may raise to model a kill at exactly that boundary. Production leaves it None.
_PHASE_HOOK = None


class MigrateError(Exception):
    """A located refusal or cannot-evaluate: the caller maps it to exit 2, fail-closed."""


class _InjectedKill(BaseException):
    """The self-test's injected interruption; a BaseException so no refusal handler absorbs it."""


def _phase(name, context):
    if _PHASE_HOOK is not None:
        _PHASE_HOOK(name, context)


# --- the scrubbed git runner ---------------------------------------------------------------------------

def _git_exe():
    git = shutil.which("git")
    if git is None:
        raise MigrateError("git not found on PATH; relocation moves a git repository and cannot run "
                           "without it")
    return os.path.abspath(git)


def _run_git(cwd, args, extra_env=None, input_bytes=None):
    """Run git under the scrubbed allowlist environment (_opf_observe._scrubbed_env: every ambient
    GIT_ variable dropped, global and system config neutralized), bound to `cwd` with -C, bounded by
    a timeout. Returns (rc, stdout bytes, stderr text); an OS launch failure or timeout raises
    MigrateError (fail-closed, never a guessed verdict)."""
    env = _opf_observe._scrubbed_env()
    if extra_env:
        env.update(extra_env)
    git = _git_exe()
    cmd = [git, "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false",
           "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
           "-C", str(cwd)] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              input=input_bytes, timeout=_GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrateError("git {} could not run in {} ({})".format(
            " ".join(args[:2]), cwd, exc))
    return proc.returncode, proc.stdout, (proc.stderr or b"").decode("utf-8", "replace")


def _git(cwd, args, extra_env=None, input_bytes=None):
    rc, out, err = _run_git(cwd, args, extra_env=extra_env, input_bytes=input_bytes)
    if rc != 0:
        raise MigrateError("git {} failed in {} (rc {}): {}".format(
            " ".join(args[:2]), cwd, rc, err.strip()))
    return out


def _git_out(cwd, args, extra_env=None, input_bytes=None):
    return _git(cwd, args, extra_env=extra_env, input_bytes=input_bytes).decode(
        "utf-8", "replace").strip()


def _git_ident_env():
    return {"GIT_AUTHOR_NAME": _IDENT_NAME, "GIT_AUTHOR_EMAIL": _IDENT_EMAIL,
            "GIT_COMMITTER_NAME": _IDENT_NAME, "GIT_COMMITTER_EMAIL": _IDENT_EMAIL}


# --- request grammar -----------------------------------------------------------------------------------

def parse_request(rest):
    """Parse `opf migrate --store <target> [--root DIR]` fail-closed (the house parser idiom): an
    unknown token, a missing, empty, option-looking or repeated value, or a missing --store refuses.
    Returns (store_value, root)."""
    store_value = None
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--store":
            if store_value is not None:
                raise MigrateError("--store given more than once")
            if i + 1 >= len(rest):
                raise MigrateError("--store requires a target argument")
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                raise MigrateError("--store requires a non-empty target argument, not {!r}".format(val))
            store_value = val
            i += 2
        elif tok == "--root":
            if root is not None:
                raise MigrateError("--root given more than once")
            if i + 1 >= len(rest):
                raise MigrateError("--root requires a directory argument")
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                raise MigrateError("--root requires a non-empty directory argument, not {!r}".format(val))
            root = val
            i += 2
        else:
            raise MigrateError("unrecognized argument {!r}".format(tok))
    if store_value is None:
        raise MigrateError("--store is required (usage: opf migrate --store <target> [--root DIR])")
    return store_value, (root if root is not None else ".")


# --- admission (before any lease or write) --------------------------------------------------------------

def _read_manifest(resolution):
    """The resolved store's manifest, parsed; bounded and fail-closed."""
    import tomllib
    path = Path(resolution.store_root) / resolution.machine_rel / _opf_store.MANIFEST_NAME
    try:
        st = os.lstat(str(path))
        if not stat.S_ISREG(st.st_mode):
            raise MigrateError("the store manifest {} is not a regular file".format(path))
        if st.st_size > _opf_store.MAX_STORE_READ_BYTES:
            raise MigrateError("the store manifest {} exceeds the read bound".format(path))
        with open(str(path), "rb") as fh:
            raw = fh.read(_opf_store.MAX_STORE_READ_BYTES + 1)
    except OSError as exc:
        raise MigrateError("the store manifest {} cannot be read ({})".format(path, exc))
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise MigrateError("the store manifest {} cannot be parsed ({})".format(path, exc))


def _check_admission(manifest):
    """Refuse, before the lease and before any write, a store this build must not relocate (spec 9.2):
    a spec_version other than exactly the supported one, a missing or non-integer homes declaration,
    any homes generation but 2, and a manifest carrying a dedicated sync target, whose consistency
    contract (fetch and compare, spec 5.7) this local-directory build cannot evaluate."""
    opf_tbl = manifest.get("opf") if isinstance(manifest, dict) else None
    if not isinstance(opf_tbl, dict):
        raise MigrateError("the store manifest carries no [opf] table")
    version = opf_tbl.get("spec_version")
    if version != _opf_store.SPEC_VERSION:
        raise MigrateError("the store declares spec_version {!r}; this build relocates only a store "
                           "declaring exactly {!r} (spec 9.2)".format(version, _opf_store.SPEC_VERSION))
    homes = opf_tbl.get("homes")
    if not (type(homes) is int and homes == _opf_store.HOMES):
        raise MigrateError("the store declares homes {!r}; this build relocates only a homes-{} store "
                           "(spec 4.2/9.2)".format(homes, _opf_store.HOMES))
    store_tbl = manifest.get("store")
    sync_target = store_tbl.get("sync_target") if isinstance(store_tbl, dict) else ""
    if sync_target not in (None, ""):
        raise MigrateError("the store declares a dedicated sync_target {!r}; relocating it needs the "
                           "fetch-and-compare consistency contract (spec 5.7), which this "
                           "local-directory build does not implement".format(sync_target))


def _require_local_directory_target(value):
    """Classify the requested target (spec 5.5) and admit only the local-directory form: a remote
    `git:`, `github:`, `gitlab:` or URL target refuses (it needs a reviewed remote provider and the
    sync contract), and a bare filesystem path refuses toward the publishable `dir:` spelling."""
    try:
        target = _opf_store.classify_target(value)
    except _opf_store.StoreError as exc:
        raise MigrateError("the target {!r} is malformed: {}".format(value, exc))
    if target.kind in _UNREGISTERED_PROVIDERS:
        raise MigrateError("the target {!r} names the {} provider, which this tooling does not "
                           "register: until a reviewed provider registration lands, a {}: target "
                           "refuses as an unregistered provider (spec 5.6)".format(
                               value, target.kind, target.kind))
    if not target.local:
        raise MigrateError("the target {!r} names a remote {} target; this build relocates to local "
                           "dir: targets only, and an unregistered remote provider is unavailable "
                           "(spec 5.6)".format(value, target.kind))
    if target.kind != "dir":
        raise MigrateError("a bare filesystem path target is refused; spell the destination as "
                           "dir:<path> so the committed pointer stays in the publishable dir: form "
                           "(spec 4.3/5.5)")
    return target


# --- preconditions under the lease ----------------------------------------------------------------------

def _require_clean_source(store_root, machine_rel=None):
    """The tracked `.working/` tree must equal HEAD, with no merge in progress and no unmerged entry,
    so the relocation operates on exactly the committed state it replays. The machine-local control
    area is exempt where it is untracked: the operation lease this run itself holds
    (`<machine-store>/lease.toml`) and the journal and staging homes, which the open-run refusal
    judges separately and the relocation carries or refuses on its own terms."""
    rc, _out, err = _run_git(store_root, ["rev-parse", "-q", "--verify", "HEAD^{commit}"])
    if rc != 0:
        raise MigrateError("the store repository {} has no commit at HEAD ({}); there is no history "
                           "to relocate".format(store_root, err.strip() or "unborn HEAD"))
    git_dir = _git_common_dir(store_root)
    for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
        if os.path.lexists(os.path.join(str(git_dir), marker)):
            raise MigrateError("the store repository {} has an operation in progress ({}); finish or "
                               "abort it first".format(store_root, marker))
    out = _git(store_root, ["status", "--porcelain", "-z", "--", _opf_store.WORKING_DIRNAME])
    allowed_untracked = []
    if machine_rel:
        allowed_untracked.append("{}/{}".format(machine_rel, "lease.toml"))
    exempt_prefixes = (_opf_store.JOURNALS_REL + "/", _opf_store.STAGING_REL + "/")
    for entry in out.split(b"\x00"):
        if not entry:
            continue
        text = entry.decode("utf-8", "replace")
        code, path = text[:2], text[3:]
        if code == "??" and (path in allowed_untracked
                             or path.startswith(exempt_prefixes)
                             or any(path == p.rstrip("/") for p in exempt_prefixes)):
            continue
        raise MigrateError("the tracked {}/ tree does not equal HEAD (for example {!r}); commit or "
                           "restore it first (spec 5.7)".format(_opf_store.WORKING_DIRNAME, text))


def _doctor_exit(resolution):
    """The doctor verdict over a RESOLVED store: gather the git observations and run the whole-store
    validator, returning (exit_code, result)."""
    obs, _notes = _opf_observe.gather(resolution)
    result = _opf_check.validate_store(resolution, observations=obs)
    return _opf_check.exit_code(result), result


def _require_valid_source(resolution):
    code, result = _doctor_exit(resolution)
    if code != EXIT_OK:
        raise MigrateError("the source store is not doctor-VALID (status {}); relocation refuses to "
                           "move a store it cannot verify (spec 5.4)".format(result.status))


def _refuse_open_runs(resolution):
    """Refuse while any journal transaction is open or any staging run exists: machine-local
    reservations and staged state do not travel with history extraction, so an open run would be
    stranded (spec 4.2/5.4)."""
    store_root = Path(resolution.store_root)
    for kind in _opf_store.STAGING_KINDS:
        staging = store_root / _opf_store.STAGING_REL / kind
        if staging.is_dir():
            entries = sorted(os.listdir(str(staging)))
            if entries:
                raise MigrateError("a staging run exists ({}/{}/{}); finish or retire it before "
                                   "relocating".format(_opf_store.STAGING_REL, kind, entries[0]))
    try:
        root_fd = _opf_store._open_root_fd(store_root)
    except OSError as exc:
        raise MigrateError("cannot open the store root {} ({})".format(store_root, exc))
    try:
        for kind in _opf_store.JOURNAL_KINDS:
            rel = _opf_store.journal_root(kind)
            if not (store_root / rel).is_dir():
                continue
            try:
                jr_fd = _journal.open_journal_root_fd(root_fd, rel)
            except (OSError, _journal.JournalError) as exc:
                raise MigrateError("cannot open the {} journal ({}); fail-closed".format(kind, exc))
            try:
                txns = _journal._journal_txn_dirs(jr_fd, store_root / rel)
                for txn in txns:
                    state = _journal.classify_state(jr_fd, txn)
                    if state == "open":
                        raise MigrateError("an open {} journal transaction exists ({}); reconcile it "
                                           "before relocating".format(kind, txn.name))
            except _journal.JournalError as exc:
                raise MigrateError("the {} journal cannot be classified ({}); fail-closed".format(
                    kind, exc))
            finally:
                _opf_store._close_fd_exc_safe(jr_fd)
    finally:
        _opf_store._close_fd_exc_safe(root_fd)


def _is_default_move(plan, row):
    """Whether a plan move row takes the spec 14.2 DEFAULT destination,
    `.working/archive/moved/<source-path>`. A non-occupying move row records the destination as its
    preservation; an occupying one records it on the plan's move-file op. A move row whose
    destination cannot be read is graded default, fail-closed: an unprovable named destination
    never excuses the refusal."""
    path = row.get("path")
    if not isinstance(path, str):
        return True
    try:
        default = _opf_store.moved_dest(path)
    except ValueError:
        return True
    if row.get("occupying") is False:
        return row.get("preservation") == default
    for op in plan.get("ops", ()):
        if isinstance(op, dict) and op.get("op") == "move-file" and op.get("source") == path:
            return op.get("destination") == default
    return True


def _refuse_stranded_moves(store_root, dest_outside_product):
    """Refuse a destination outside the product root while any admitted adoption run whose retirement
    is unrecorded holds a default Move row (spec 14.2): the default Move destination refuses a store
    resolved outside the product root until a multi-root coordinator exists, so relocating the store
    out while such a row is pending would strand it. A retirement is recorded only by the sealed
    retirement inventory in the run's bundle, so a bundle holding a persisted plan with no such
    inventory is the unrecorded state, whether or not the row's source file is still live."""
    import tomllib
    if not dest_outside_product:
        return
    store_root = Path(store_root)
    bundles = store_root / _opf_store.IMPORTED_REL / "adoption"
    if not bundles.is_dir():
        return
    for run in sorted(os.listdir(str(bundles))):
        plan_path = bundles / run / "plan.toml"
        if not plan_path.is_file():
            continue
        if (bundles / run / _RETIREMENT_INVENTORY_NAME).is_file():
            continue
        try:
            with open(str(plan_path), "rb") as fh:
                plan = tomllib.loads(fh.read(_opf_store.MAX_STORE_READ_BYTES).decode("utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise MigrateError("the persisted adoption plan {} cannot be read ({}); fail-closed".format(
                plan_path, exc))
        for row in plan.get("sources", ()):
            if not isinstance(row, dict) or row.get("disposition") != "move":
                continue
            if _is_default_move(plan, row):
                raise MigrateError("the adoption run {} holds a default Move row ({!r}) and its "
                                   "retirement is unrecorded (no {} in its bundle); the default move "
                                   "destination refuses a store outside the product root (spec 14.2), "
                                   "so this relocation would strand it".format(
                                       run, row.get("path"), _RETIREMENT_INVENTORY_NAME))


# --- destination validation -----------------------------------------------------------------------------

def _require_empty_destination(dest):
    """The destination must be new or empty: a missing directory, an empty directory, or a directory
    holding only a commitless git repository. Anything else refuses with the destination untouched;
    a store is never merged into or pushed over existing content (spec 5.4/5.6)."""
    dest = Path(dest)
    if not os.path.lexists(str(dest)):
        return "new"
    if not dest.is_dir() or os.path.islink(str(dest)):
        raise MigrateError("the destination {} exists and is not a plain directory; refused with the "
                           "destination untouched".format(dest))
    entries = sorted(os.listdir(str(dest)))
    if not entries:
        return "empty-dir"
    if entries == [".git"]:
        rc, _out, _err = _run_git(dest, ["rev-parse", "-q", "--verify", "HEAD^{commit}"])
        if rc != 0:
            return "empty-repo"
        raise MigrateError("the destination {} is a repository that already has commits; refused with "
                           "the destination untouched".format(dest))
    raise MigrateError("the destination {} is not empty (for example {!r}); refused with the "
                       "destination untouched".format(dest, entries[0]))


def _refuse_config_rewrites(repos, destinations):
    """Refuse when any `url.*.insteadOf` or `url.*.pushInsteadOf` configuration visible to the
    transfer (the involved repositories' own config; the ambient scopes are already neutralized by
    the scrubbed environment) would rewrite a destination spelling: a rewritten destination is a push
    or fetch endpoint that appeared from ambient state, which is surfaced and refused, never used
    (spec 5.6)."""
    spellings = [str(d) for d in destinations]
    for repo in repos:
        rc, out, err = _run_git(repo, ["config", "-z", "--get-regexp", r"^url\."])
        if rc not in (0, 1):
            raise MigrateError("cannot enumerate url rewrite configuration in {} (rc {}: {}); "
                               "fail-closed".format(repo, rc, err.strip()))
        if rc == 1:
            continue
        for entry in out.split(b"\x00"):
            if not entry:
                continue
            text = entry.decode("utf-8", "replace")
            key, _nl, value = text.partition("\n")
            lowered = key.lower()
            if not (lowered.endswith(".insteadof") or lowered.endswith(".pushinsteadof")):
                continue
            for spelling in spellings:
                if value and spelling.startswith(value):
                    raise MigrateError("the git configuration of {} rewrites the destination spelling "
                                       "{!r} ({} = {!r}); a rewritten transfer endpoint is refused, "
                                       "never used (spec 5.6)".format(repo, spelling, key, value))


# --- the relocation journal -----------------------------------------------------------------------------

def _git_common_dir(repo_root):
    common = _git_out(repo_root, ["rev-parse", "--git-common-dir"])
    if not os.path.isabs(common):
        common = os.path.join(str(repo_root), common)
    return Path(os.path.abspath(common))


def _journal_home(product_root):
    return _git_common_dir(product_root) / JOURNAL_DIRNAME


def _is_run_id(name):
    """Whether name is a relocation run id, judged by the store's own journal-run constructor for
    the `relocation` kind (spec 4.2): the reserved `reloc-` prefix with the shared timestamp/hash
    shape, and nothing else."""
    try:
        _opf_store._home_run(_RELOCATION_KIND, name, _opf_store.JOURNAL_KINDS)
    except ValueError:
        return False
    return True


def _new_run_id():
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_id = "{}-{}-{}".format(_opf_store._HOME_RUN_PREFIXES[_RELOCATION_KIND], stamp,
                               secrets.token_hex(8))
    # The constructor is the one grammar authority: a generated id it refuses is a harness error.
    _opf_store._home_run(_RELOCATION_KIND, run_id, _opf_store.JOURNAL_KINDS)
    return run_id


def _emit_frame_toml(mapping):
    import json
    lines = []
    for key in sorted(mapping):
        value = mapping[key]
        if type(value) is bool:
            lines.append("{} = {}".format(key, "true" if value else "false"))
        elif type(value) is int:
            lines.append("{} = {}".format(key, value))
        elif type(value) is str:
            lines.append("{} = {}".format(key, json.dumps(value)))
        else:
            raise MigrateError("a journal frame value must be a string, int or bool ({!r})".format(key))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_frame(run_dir, name, mapping):
    """Publish one phase frame durably: exclusive create, fsync, then a directory fsync."""
    if name not in _FRAMES:
        raise MigrateError("unknown journal frame {!r}".format(name))
    run_dir = Path(run_dir)
    os.makedirs(str(run_dir), exist_ok=True)
    path = run_dir / (name + ".toml")
    payload = _emit_frame_toml(mapping)
    try:
        with open(str(path), "xb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        dfd = os.open(str(run_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            _opf_store._close_fd_exc_safe(dfd)
    except OSError as exc:
        raise MigrateError("cannot write the relocation journal frame {} ({})".format(path, exc))


def _read_frame(run_dir, name):
    import tomllib
    path = Path(run_dir) / (name + ".toml")
    if not path.is_file():
        return None
    try:
        with open(str(path), "rb") as fh:
            return tomllib.loads(fh.read(_opf_store.MAX_STORE_READ_BYTES).decode("utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise MigrateError("the relocation journal frame {} cannot be read ({}); recovery fails "
                           "closed on an unreadable journal (spec 4.2)".format(path, exc))


# --- history extraction ---------------------------------------------------------------------------------

def _rev_list_working(store_root):
    """Every commit that touches `.working/`, oldest first, each with its parents simplified to that
    same set (git's parent rewriting under a pathspec), as (sha, parents) pairs."""
    out = _git(store_root, ["rev-list", "--reverse", "--topo-order", "--parents", "--full-history",
                            "HEAD", "--", _opf_store.WORKING_DIRNAME])
    rows = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        rows.append((parts[0], parts[1:]))
    if not rows:
        raise MigrateError("no commit in {} touches {}/; there is no tracked store history to "
                           "relocate (spec 5.1)".format(store_root, _opf_store.WORKING_DIRNAME))
    return rows


def _subtree_for(store_root, sha):
    """The `.working` tree object of one source commit, or None where that commit holds none."""
    rc, out, _err = _run_git(store_root, ["rev-parse", "-q", "--verify",
                                          "{}:{}".format(sha, _opf_store.WORKING_DIRNAME)])
    if rc != 0:
        return None
    return out.decode("ascii", "replace").strip()


def _commit_meta(store_root, sha):
    out = _git(store_root, ["show", "-s", "--date=raw",
                            "--format=%an%x1f%ae%x1f%ad%x1f%cn%x1f%ce%x1f%cd%x1f%B", sha])
    parts = out.split(b"\x1f", 6)
    if len(parts) != 7:
        raise MigrateError("cannot read the identity and message of commit {}".format(sha))
    an, ae, ad, cn, ce, cd, message = parts
    return {"GIT_AUTHOR_NAME": an.decode("utf-8", "replace"),
            "GIT_AUTHOR_EMAIL": ae.decode("utf-8", "replace"),
            "GIT_AUTHOR_DATE": ad.decode("ascii", "replace"),
            "GIT_COMMITTER_NAME": cn.decode("utf-8", "replace"),
            "GIT_COMMITTER_EMAIL": ce.decode("utf-8", "replace"),
            "GIT_COMMITTER_DATE": cd.decode("ascii", "replace")}, message


def _extract_history(store_root, extract_dir, branch):
    """Replay every `.working`-touching source commit, commit for commit, into a bare extraction
    repository whose object lookup borrows the source's objects (an alternates file), so only the
    replayed history is ever transferred onward. Each replayed commit holds exactly the source
    commit's `.working` tree at the destination root, with the original author, committer, dates and
    message; parents map through the replay, so the shape of the simplified history is preserved.
    Returns (mapping pairs oldest first, tip)."""
    _git(extract_dir, ["init", "--bare", "--quiet", str(extract_dir)])
    objects = _git_common_dir(store_root) / "objects"
    info = Path(extract_dir) / "objects" / "info"
    os.makedirs(str(info), exist_ok=True)
    with open(str(info / "alternates"), "w", encoding="utf-8") as fh:
        fh.write(str(objects) + "\n")
    mapping = []
    new_ids = {}
    for sha, parents in _rev_list_working(store_root):
        subtree = _subtree_for(store_root, sha)
        if subtree is None:
            tree_input = b""
        else:
            tree_input = "040000 tree {}\t{}\n".format(
                subtree, _opf_store.WORKING_DIRNAME).encode("ascii")
        tree = _git_out(extract_dir, ["mktree"], input_bytes=tree_input)
        env, message = _commit_meta(store_root, sha)
        args = ["commit-tree", tree]
        for parent in parents:
            mapped = new_ids.get(parent)
            if mapped is None:
                raise MigrateError("the replay of commit {} names a parent {} outside the replayed "
                                   "set; fail-closed".format(sha, parent))
            args += ["-p", mapped]
        new = _git_out(extract_dir, args, extra_env=env, input_bytes=message)
        new_ids[sha] = new
        mapping.append((sha, new))
    tip = mapping[-1][1]
    _git(extract_dir, ["update-ref", "refs/heads/{}".format(branch), tip])
    _git(extract_dir, ["symbolic-ref", "HEAD", "refs/heads/{}".format(branch)])
    return mapping, tip


def _mapping_digest(mapping):
    body = "".join("{} {}\n".format(old, new) for old, new in mapping).encode("ascii")
    return "sha256:" + hashlib.sha256(body).hexdigest()


# --- transfer -------------------------------------------------------------------------------------------

def _materialize_destination(dest, mode, extract_dir, branch, tip):
    """Create or adopt the validated empty destination and bring the extracted history into it: the
    destination fetches the replayed branch from the extraction repository by its literal path (the
    confirmed endpoint, never a rewritten one), then checks it out."""
    dest = Path(dest)
    if mode == "new":
        os.makedirs(str(dest))
    if mode in ("new", "empty-dir"):
        _git(dest, ["init", "--quiet", str(dest)])
    _git(dest, ["fetch", "--quiet", str(extract_dir), "refs/heads/{}".format(branch)])
    _git(dest, ["update-ref", "refs/heads/{}".format(branch), tip])
    _git(dest, ["symbolic-ref", "HEAD", "refs/heads/{}".format(branch)])
    _git(dest, ["reset", "--hard", "--quiet"])


def _prepare_review_branch(product_root, extract_dir, branch, tip, run_id, pointer_bytes):
    """A relocation INTO the product repository never commits to the current branch: it prepares a
    review branch holding one merge of the replayed `.working` history onto the current HEAD, with
    the committed pointer rewritten to the in-repo spelling in the same change, and the move lands
    through normal review. Returns (review branch name, merge commit)."""
    review = "opf-migrate-{}".format(run_id)
    rc, _out, _err = _run_git(product_root, ["rev-parse", "-q", "--verify",
                                             "refs/heads/{}".format(review)])
    if rc == 0:
        raise MigrateError("the review branch {} already exists in {}; refused".format(
            review, product_root))
    _git(product_root, ["fetch", "--quiet", str(extract_dir), "refs/heads/{}".format(branch)])
    head = _git_out(product_root, ["rev-parse", "HEAD"])
    entries = _git(product_root, ["ls-tree", "-z", "HEAD"])
    pointer_blob = _git_out(product_root, ["hash-object", "-w", "--stdin"],
                            input_bytes=pointer_bytes)
    working_tree = _git_out(product_root, ["rev-parse",
                                           "{}:{}".format(tip, _opf_store.WORKING_DIRNAME)])
    rows = []
    for entry in entries.split(b"\x00"):
        if not entry:
            continue
        meta, _tab, name = entry.partition(b"\t")
        if name == _opf_store.POINTER_REL.encode("utf-8"):
            continue
        if name == _opf_store.WORKING_DIRNAME.encode("utf-8"):
            raise MigrateError("the product repository already tracks {}/ at HEAD; refused".format(
                _opf_store.WORKING_DIRNAME))
        rows.append(entry)
    rows.append("100644 blob {}\t{}".format(pointer_blob, _opf_store.POINTER_REL).encode("utf-8"))
    rows.append("040000 tree {}\t{}".format(working_tree, _opf_store.WORKING_DIRNAME).encode("utf-8"))
    tree = _git_out(product_root, ["mktree", "-z"], input_bytes=b"\x00".join(rows) + b"\x00")
    message = ("Relocate the OPF store into the product repository\n\n"
               "Prepared by opf migrate as a review branch; the replayed store history is the "
               "second parent.\n").encode("utf-8")
    merge = _git_out(product_root, ["commit-tree", tree, "-p", head, "-p", tip],
                     extra_env=_git_ident_env(), input_bytes=message)
    _git(product_root, ["update-ref", "refs/heads/{}".format(review), merge])
    return review, merge


# --- verification ---------------------------------------------------------------------------------------

def _verify_adoption_archive(store_root):
    """Re-verify every carried adoption-archived copy's digest against its run's persisted plan at the
    relocated store (spec 14.2): a missing, unreadable, unplanned or digest-mismatched archived copy
    refuses, naming the path."""
    import tomllib
    store_root = Path(store_root)
    archive = store_root / _opf_store.ARCHIVE_REL / "adoption"
    if not archive.is_dir():
        return 0
    checked = 0
    for run in sorted(os.listdir(str(archive))):
        run_dir = archive / run
        if not run_dir.is_dir() or os.path.islink(str(run_dir)):
            raise MigrateError("the adoption archive entry {} is not a plain directory; "
                               "fail-closed".format(run_dir))
        plan_path = store_root / _opf_store.IMPORTED_REL / "adoption" / run / "plan.toml"
        try:
            with open(str(plan_path), "rb") as fh:
                plan = tomllib.loads(fh.read(_opf_store.MAX_STORE_READ_BYTES).decode("utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise MigrateError("the archived copies of adoption run {} cannot be verified: the "
                               "persisted plan {} cannot be read ({}); fail-closed".format(
                                   run, plan_path, exc))
        expected = {}
        for row in plan.get("sources", ()):
            if (isinstance(row, dict) and isinstance(row.get("preservation"), str)
                    and isinstance(row.get("digest"), str)):
                expected[row["preservation"]] = row["digest"]
        for dirpath, dirnames, filenames in os.walk(str(run_dir)):
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, str(store_root)).replace(os.sep, "/")
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    raise MigrateError("the archived copy {} is not a regular file; "
                                       "fail-closed".format(rel))
                digest = expected.get(rel)
                if digest is None:
                    raise MigrateError("the archived copy {} is not claimed by its run's persisted "
                                       "plan; fail-closed".format(rel))
                with open(path, "rb") as fh:
                    actual = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
                if actual != digest:
                    raise MigrateError("the archived copy {} does not match its plan digest at the "
                                       "destination; the relocation is refused before the source is "
                                       "touched (spec 14.2)".format(rel))
                checked += 1
    return checked


def _require_wiring_probe(root):
    """Spec 14.2: where the adoption home exists, the relocation must pass the completion check 5
    probe (spec 14.1) at the destination before the old tree is removed, and a destination that
    fails that probe refuses the relocation. This build supplies no wiring prober and the shipped
    enforcement pack carries no live probe adapter, so check 5 cannot grade VALID here: a store
    whose adoption home exists refuses fail-closed, naming the first run, until a build that can
    run the probes lands. That refusal also fail-closes the spec 14.1 renewal obligation: no
    relocation this build completes carries an adoption bundle, so none owes the append-only
    renewal record. Returns the number of runs probed (0 where no adoption home exists)."""
    root = Path(root)
    runs = set()
    for rel in ("{}/adoption".format(_opf_store.IMPORTED_REL),
                "{}/adoption".format(_opf_store.ARCHIVE_REL)):
        home = root / rel
        if not home.is_dir():
            continue
        try:
            runs.update(os.listdir(str(home)))
        except OSError as exc:
            raise MigrateError("the adoption home {} cannot be listed ({}); fail-closed".format(
                home, exc))
    if not runs:
        return 0
    import _opf_adopt_complete
    for run in sorted(runs):
        results = _opf_adopt_complete.evaluate(root, run, prober=None)
        wiring = results.get(_opf_adopt_complete.WIRING)
        detail = "no verdict"
        if wiring is not None and wiring.findings:
            detail = wiring.findings[0]
        if wiring is None or wiring.status != _opf_adopt_complete.VALID:
            raise MigrateError("the adoption home exists and the completion check 5 probe did not "
                               "pass at the destination for run {} ({}); a destination that fails "
                               "that probe refuses the relocation, before the old tree is removed "
                               "(spec 14.1/14.2)".format(run, detail))
    return len(runs)


def _blob_bytes(repo, spec):
    rc, out, _err = _run_git(repo, ["cat-file", "blob", spec])
    if rc != 0:
        return None
    return out


def _verify_clone_back(ctx):
    """Clone the destination back independently and reconcile it against the source before anything
    is retired (spec 5.4): the destination still holds exactly the extracted tip, every mapped
    commit's `.working` tree is identical, the worklog, the record archive and `counters.toml` are
    byte for byte the source's, the whole `.working` tree at HEAD is identical, every carried
    adoption-archived copy re-verifies against its plan, and the clone resolves as a store. Any
    mismatch refuses with the source and the pointer untouched."""
    import tempfile
    source = ctx["store_root"]
    machine_rel = ctx["machine_rel"]
    clone_dir = Path(tempfile.mkdtemp(prefix="opf-migrate-verify-"))
    try:
        if ctx["direction"] == "into-product":
            origin, head_name = ctx["product_root"], ctx["review"]
            _git(clone_dir, ["clone", "--quiet", "--no-hardlinks", "--branch", head_name,
                             str(origin), str(clone_dir / "r")])
        else:
            origin, head_name = ctx["dest"], ctx["branch"]
            _git(clone_dir, ["clone", "--quiet", "--no-hardlinks", "--branch", head_name,
                             str(origin), str(clone_dir / "r")])
        repo = clone_dir / "r"
        _git(repo, ["remote", "remove", "origin"])
        # The replay roster is recounted here INDEPENDENTLY of the extraction's own listing, so an
        # extraction that flattened or dropped commits cannot grade its own homework (spec 5.4).
        src_count = int(_git_out(source, ["rev-list", "--count", "--full-history", "HEAD", "--",
                                          _opf_store.WORKING_DIRNAME]))
        if src_count != len(ctx["mapping"]):
            raise MigrateError("the extraction replayed {} commits where the source holds {} touching "
                               "{}/; a flattened or partial replay is refused (spec 5.4)".format(
                                   len(ctx["mapping"]), src_count, _opf_store.WORKING_DIRNAME))
        if ctx["direction"] == "into-product":
            head = _git_out(repo, ["rev-parse", "HEAD"])
            if head != ctx["merge"]:
                raise MigrateError("the review branch no longer holds the prepared merge; refused")
            if _git_out(repo, ["rev-parse", "HEAD^2"]) != ctx["tip"]:
                raise MigrateError("the review merge no longer carries the extracted history as its "
                                   "second parent; refused")
            for name in ("VERSION", "CHANGELOG.md"):
                ours = _blob_bytes(ctx["product_root"], "HEAD:{}".format(name))
                theirs = _blob_bytes(repo, "HEAD:{}".format(name))
                if ours != theirs:
                    raise MigrateError("the product deliverable {} is not byte-identical on the review "
                                       "branch; refused".format(name))
        else:
            dest_tip = _git_out(ctx["dest"], ["rev-parse", "refs/heads/{}".format(ctx["branch"])])
            if dest_tip != ctx["tip"]:
                raise MigrateError("the destination {} no longer holds the extracted tip (it holds {}); "
                                   "a changed destination is refused, never retired into "
                                   "(spec 5.4)".format(ctx["dest"], dest_tip))
            head = _git_out(repo, ["rev-parse", "HEAD"])
            if head != ctx["tip"]:
                raise MigrateError("the clone-back does not hold the extracted tip; refused")
            status = _git(ctx["dest"], ["status", "--porcelain", "-z"])
            if status.strip(b"\x00").strip():
                raise MigrateError("the destination working tree does not equal its HEAD; refused")
            count = int(_git_out(repo, ["rev-list", "--count", "HEAD"]))
            if count != len(ctx["mapping"]):
                raise MigrateError("the destination holds {} commits where the extraction replayed {}; "
                                   "a flattened or amended history is refused (spec 5.4)".format(
                                       count, len(ctx["mapping"])))
        for old, new in ctx["mapping"]:
            src_tree = _subtree_for(source, old)
            rc, out, _err = _run_git(repo, ["rev-parse", "-q", "--verify",
                                            "{}:{}".format(new, _opf_store.WORKING_DIRNAME)])
            dst_tree = out.decode("ascii", "replace").strip() if rc == 0 else None
            if src_tree != dst_tree:
                raise MigrateError("the replayed commit of {} does not hold an identical {} tree; "
                                   "refused (spec 5.4)".format(old, _opf_store.WORKING_DIRNAME))
        src_working = _subtree_for(source, "HEAD")
        dst_working = _subtree_for(repo, "HEAD")
        if src_working != dst_working:
            raise MigrateError("the relocated {} tree at HEAD is not identical to the source's; "
                               "refused".format(_opf_store.WORKING_DIRNAME))
        for rel, label in (("{}/worklog.toml".format(machine_rel), "worklog"),
                           ("{}/{}".format(machine_rel, _opf_store.COUNTERS_NAME), "counters")):
            ours = _blob_bytes(source, "HEAD:{}".format(rel))
            theirs = _blob_bytes(repo, "HEAD:{}".format(rel))
            if ours != theirs:
                raise MigrateError("the {} ledger {} is not byte-identical at the destination; a "
                                   "relocation that rewrites it is nonconformant (spec 5.4/6.2)".format(
                                       label, rel))
        for subtree in ("{}/archive".format(machine_rel), _opf_store.IMPORTED_REL):
            rc1, out1, _e1 = _run_git(source, ["rev-parse", "-q", "--verify",
                                               "HEAD:{}".format(subtree)])
            rc2, out2, _e2 = _run_git(repo, ["rev-parse", "-q", "--verify",
                                             "HEAD:{}".format(subtree)])
            if (rc1 == 0) != (rc2 == 0) or (rc1 == 0 and out1 != out2):
                raise MigrateError("the {} tree is not identical at the destination; refused "
                                   "(spec 5.4/6.2)".format(subtree))
        archived = _verify_adoption_archive(repo)
        res = _opf_store.resolve_store(repo)
        if res.status != _opf_store.RESOLVED:
            raise MigrateError("the clone-back does not resolve as a store ({}); refused".format(
                res.detail))
        if res.machine_dir != ctx["machine_dir"]:
            raise MigrateError("the clone-back resolves a different machine store ({!r}); "
                               "refused".format(res.machine_dir))
        if ctx["direction"] == "into-product":
            code, result = _doctor_exit(res)
            if code != EXIT_OK:
                raise MigrateError("the review branch store is not doctor-VALID (status {}); "
                                   "refused".format(result.status))
        # The spec 14.1/14.2 completion check 5 probe runs at the DESTINATION, before anything is
        # staged or removed; a review-branch destination is probed through the clone-back, its one
        # checked-out image.
        probed = _require_wiring_probe(repo if ctx["direction"] == "into-product" else ctx["dest"])
        return {"archived_copies": archived, "working_tree": src_working or "",
                "probed_runs": probed}
    finally:
        shutil.rmtree(str(clone_dir), ignore_errors=True)


# --- the staged product change --------------------------------------------------------------------------

def _pointer_value(product_root, dest):
    """The publishable committed-pointer spelling of a local destination: a relative dir: path against
    the product root (spec 4.3/5.8). A spelling outside the conservative pointer alphabet refuses."""
    rel = os.path.relpath(str(dest), str(product_root))
    value = "dir:{}".format(rel.replace(os.sep, "/"))
    if _POINTER_SAFE_RE.fullmatch(value[len("dir:"):]) is None:
        raise MigrateError("the destination path {!r} cannot be written as a publishable committed "
                           "pointer (spec 4.3); refused".format(rel))
    return value


def _pointer_bytes(value):
    import json
    return "[store]\ntarget = {}\n".format(json.dumps(value)).encode("utf-8")


def _carry_allocations(store_root, dest):
    """Copy the machine-local id reservations (journals/*/allocations/) byte for byte into the
    destination working copy, left untracked: they are ignored by version control, so history
    extraction cannot carry them, yet the doctor's counter grading reads them."""
    carried = []
    for kind in _opf_store.JOURNAL_KINDS:
        src = Path(store_root) / _opf_store.JOURNALS_REL / kind / "allocations"
        if not src.is_dir():
            continue
        for name in sorted(os.listdir(str(src))):
            spath = src / name
            if not stat.S_ISREG(os.lstat(str(spath)).st_mode):
                continue
            drel = "{}/{}/allocations/{}".format(_opf_store.JOURNALS_REL, kind, name)
            dpath = Path(dest) / drel
            os.makedirs(str(dpath.parent), exist_ok=True)
            if not os.path.lexists(str(dpath)):
                shutil.copy2(str(spath), str(dpath))
            carried.append(drel)
    return carried


def _write_pointer(product_root, payload):
    path = Path(product_root) / _opf_store.POINTER_REL
    preimage = None
    if os.path.lexists(str(path)):
        with open(str(path), "rb") as fh:
            preimage = fh.read()
    with open(str(path), "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return preimage


def _remove_working_tree(product_root):
    """Stage the in-repo source's retirement, uncommitted: the tracked `.working/` tree leaves the
    index and the working tree (HEAD keeps every byte until the migration change lands), and the
    ignored machine-local leftovers are removed with it."""
    _git(product_root, ["rm", "-r", "-q", "--", _opf_store.WORKING_DIRNAME])
    leftover = Path(product_root) / _opf_store.WORKING_DIRNAME
    if os.path.lexists(str(leftover)):
        shutil.rmtree(str(leftover))


def _rollback_product_change(product_root, pointer_pre, remove_working):
    path = Path(product_root) / _opf_store.POINTER_REL
    if pointer_pre is None:
        if os.path.lexists(str(path)):
            os.unlink(str(path))
    else:
        with open(str(path), "wb") as fh:
            fh.write(pointer_pre)
    if remove_working:
        _git(product_root, ["checkout", "HEAD", "--", _opf_store.WORKING_DIRNAME])


# --- the relocation worklog entry -----------------------------------------------------------------------

def _record_relocation(root, old_desc, new_desc, mapping_digest):
    """Record the relocation at the destination as a separate journaled worklog append through the
    record verb (its own lease, id claim, render and final doctor), naming both locations."""
    argv = ["worklog-append", "--root", str(root), "--kind", "infra",
            "--summary", "relocated the OPF store from {} to {}".format(old_desc, new_desc),
            "--detail", "opf migrate; commit ids rewritten by history extraction; old-to-new commit "
                        "mapping digest {}".format(mapping_digest),
            "--actor", "automation"]
    rc = _opf_record.cli(argv)
    if rc != EXIT_OK:
        raise MigrateError("the relocation worklog entry could not be recorded at the destination "
                           "(opf record exited {}); fail-closed".format(rc))


# --- reconciliation of an interrupted run ----------------------------------------------------------------

def _pending_runs(jdir):
    if not Path(jdir).is_dir():
        return []
    pending = []
    for name in sorted(os.listdir(str(jdir))):
        run_dir = Path(jdir) / name
        if not run_dir.is_dir() or not _is_run_id(name):
            continue
        if (run_dir / "complete.toml").is_file() or (run_dir / "reconciled.toml").is_file():
            continue
        pending.append(run_dir)
    return pending


def _intent_field(frame, key, run_dir):
    """A required field of a journaled frame; a frame that lost one is a damaged journal, and
    recovery fails closed on it rather than guessing (spec 4.2/5.4)."""
    if not isinstance(frame, dict) or key not in frame:
        raise MigrateError("the relocation journal run {} is missing {!r} in its journaled frame; "
                           "recovery fails closed on a missing or damaged journal "
                           "(spec 4.2/5.4)".format(Path(run_dir).name, key))
    return frame[key]


def _require_journal_for_staged_change(product_root, jdir):
    """Spec 5.4: requested recovery of an interrupted relocation fails closed on a missing journal.
    The staged, uncommitted committed-pointer change is the durable footprint a relocation leaves at
    or after its verified phase, so a working-tree pointer that differs from HEAD with no relocation
    journal run accounting for those bytes means the journal that would drive recovery is gone, and
    this run refuses rather than operate over state it cannot reconcile."""
    path = Path(product_root) / _opf_store.POINTER_REL
    working = None
    if os.path.lexists(str(path)):
        try:
            with open(str(path), "rb") as fh:
                working = fh.read(_opf_store.MAX_STORE_READ_BYTES + 1)
        except OSError as exc:
            raise MigrateError("the committed pointer {} cannot be read ({}); fail-closed".format(
                path, exc))
    rc, out, _err = _run_git(product_root, ["cat-file", "blob",
                                            "HEAD:{}".format(_opf_store.POINTER_REL)])
    committed = out if rc == 0 else None
    if working == committed:
        return
    staged_hex = "absent" if working is None else working.hex()
    jdir = Path(jdir)
    if jdir.is_dir():
        for name in sorted(os.listdir(str(jdir))):
            run_dir = jdir / name
            if not run_dir.is_dir():
                continue
            intent = _read_frame(run_dir, "intent")
            if intent is not None and intent.get("pointer_post_hex") == staged_hex:
                return
    raise MigrateError("the committed pointer at {} differs from HEAD with no relocation journal "
                       "run accounting for it; recovery of an interrupted relocation fails closed "
                       "on a missing journal (spec 4.2/5.4)".format(product_root))


def _reconcile_run(run_dir, product_root):
    """Reconcile one interrupted relocation run from its journal. Before the verified frame nothing
    durable was staged: the run is marked reconciled and a partially written destination is reported,
    never deleted. At or after the verified frame the run completes forward when the source still
    holds its journaled state (the staged pointer and removal are re-established under the caller's
    lease, and the returned continuation appends the worklog entry once, through the record verb's
    own lease, and requires the final doctor VALID), and rolls the staged product change back
    otherwise. Returns (report line, continuation or None)."""
    intent = _read_frame(run_dir, "intent")
    if intent is None:
        _write_frame(run_dir, "reconciled", {"outcome": "no intent frame; nothing was applied"})
        return "{}: no intent was journaled; nothing to reconcile".format(run_dir.name), None
    verified = _read_frame(run_dir, "verified")
    if verified is None:
        _write_frame(run_dir, "reconciled",
                     {"outcome": "interrupted before verification; nothing durable was staged"})
        return ("{}: interrupted before verification; the source and the pointer are untouched, and "
                "any partially written destination at {} is reported, never deleted".format(
                    run_dir.name, intent.get("dest", "?"))), None
    if intent.get("direction") == "into-product":
        _write_frame(run_dir, "reconciled",
                     {"outcome": "interrupted after the review branch was prepared; the branch is "
                                 "inert and lands or is deleted through review"})
        return ("{}: the prepared review branch is left for review; no store or pointer was "
                "touched".format(run_dir.name)), None
    store_root = Path(_intent_field(intent, "store_root", run_dir))
    head_now = _git_out(store_root, ["rev-parse", "HEAD"])
    try:
        pointer_post = bytes.fromhex(_intent_field(intent, "pointer_post_hex", run_dir))
        pre_hex = _intent_field(intent, "pointer_pre_hex", run_dir)
        pointer_pre = None if pre_hex == "absent" else bytes.fromhex(pre_hex)
    except ValueError:
        raise MigrateError("the relocation journal run {} holds an unreadable pointer image; "
                           "recovery fails closed on a damaged journal (spec 4.2/5.4)".format(
                               run_dir.name))
    remove_working = bool(_intent_field(intent, "remove_working", run_dir))
    if head_now != _intent_field(intent, "source_head", run_dir):
        _rollback_product_change(product_root, pointer_pre, remove_working)
        _write_frame(run_dir, "reconciled",
                     {"outcome": "rolled back: the source changed after extraction"})
        return ("{}: the source HEAD moved after extraction; the staged product change was rolled "
                "back (pointer restored, {} restored from HEAD) and the relocation is refused".format(
                    run_dir.name, _opf_store.WORKING_DIRNAME)), None
    # A verified run was extracted first, so a missing extracted frame is a damaged journal; refuse
    # BEFORE re-staging anything (spec 4.2/5.4).
    extracted = _read_frame(run_dir, "extracted")
    if extracted is None:
        raise MigrateError("the relocation journal run {} reached its verified frame with no "
                           "extracted frame; recovery fails closed on a missing journal frame "
                           "(spec 4.2/5.4)".format(run_dir.name))
    # Complete forward: re-establish the staged product change idempotently under the caller's lease.
    _write_pointer(product_root, pointer_post)
    if remove_working and os.path.lexists(str(Path(product_root) / _opf_store.WORKING_DIRNAME)):
        _remove_working_tree(product_root)

    def continuation():
        if _read_frame(run_dir, "recorded") is None:
            _record_relocation(product_root, _intent_field(intent, "old_desc", run_dir),
                               _intent_field(intent, "new_desc", run_dir),
                               _intent_field(extracted, "mapping_digest", run_dir))
            _write_frame(run_dir, "recorded", {"forward": True})
        res = _opf_store.resolve_store(Path(product_root))
        if res.status != _opf_store.RESOLVED:
            raise MigrateError("forward completion of {} cannot resolve the relocated store ({}); "
                               "fail-closed".format(run_dir.name, res.detail))
        code, result = _doctor_exit(res)
        if code != EXIT_OK:
            raise MigrateError("forward completion of {} leaves the destination store not "
                               "doctor-VALID (status {}); fail-closed".format(
                                   run_dir.name, result.status))
        _write_frame(run_dir, "complete", {"forward": True})
        _write_frame(run_dir, "reconciled", {"outcome": "completed forward"})

    return ("{}: the interrupted relocation was completed forward (pointer staged, relocation "
            "recorded, doctor VALID through the new pointer)".format(run_dir.name)), continuation


def _reconcile_interrupted(jdir, product_root):
    """(report lines, forward continuations) over every pending run, oldest first."""
    reports, continuations = [], []
    for run_dir in _pending_runs(jdir):
        line, continuation = _reconcile_run(run_dir, product_root)
        reports.append(line)
        if continuation is not None:
            continuations.append(continuation)
    return reports, continuations


# --- the ordered operation ------------------------------------------------------------------------------

def _acquire_lease(store_root):
    try:
        return _opf_oplock.acquire_operation(store_root, "migrate")
    except _opf_oplock.OpLockError as exc:
        raise MigrateError("the single-writer operation lease could not be acquired ({}); a held "
                           "lease is never seized (spec 5.7)".format(exc))


def _release_lease(cap):
    try:
        _opf_oplock.release_operation(cap)
    except _opf_oplock.OpLockError as exc:
        # A failing release never masks the operation's own outcome, but it is surfaced: a stale
        # record left behind refuses the next acquisition until recovery.
        print("opf migrate: note: the operation lease did not release cleanly ({})".format(exc),
              file=sys.stderr)


def _migrate(rest):
    import tempfile
    store_value, root = parse_request(rest)
    res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if res.status == _opf_store.NOT_ADOPTED:
        raise MigrateError("no OPF store was found at {} ({}); there is nothing to relocate".format(
            root, res.detail))
    if res.status != _opf_store.RESOLVED:
        raise MigrateError("the store at {} cannot be evaluated ({})".format(root, res.detail))
    product_root = Path(res.product_root)
    # The resolver keeps a relative pointer's joined spelling (p/../q) by design; normalize it
    # LEXICALLY for this run's path comparisons and git bindings (the resolver's no-follow walk
    # already opened the real root; nothing here re-resolves a symlink).
    store_root = Path(os.path.normpath(str(res.store_root)))
    manifest = _read_manifest(res)
    _check_admission(manifest)
    jdir = _journal_home(product_root)
    if _pending_runs(jdir):
        # Reconcile-first (spec 5.7): the interrupted run is reconciled under the lease, then THIS
        # operation refuses. A forward completion's worklog append runs after the lease is released,
        # because the record verb takes the destination store's lease itself.
        cap0 = _acquire_lease(store_root)
        try:
            reports, continuations = _reconcile_interrupted(jdir, product_root)
        finally:
            _release_lease(cap0)
        for continuation in continuations:
            continuation()
        for line in reports:
            print("opf migrate: reconciled: {}".format(line), file=sys.stderr)
        raise MigrateError("an interrupted relocation was reconciled first; re-run the "
                           "operation (spec 5.7)")
    _require_journal_for_staged_change(product_root, jdir)
    target = _require_local_directory_target(store_value)
    dest = Path(os.path.abspath(_opf_store._target_store_root(target, product_root)))
    if dest == store_root:
        raise MigrateError("the destination {} is the source store itself; refused".format(dest))
    for inner, outer, what in ((dest, store_root, "inside the source store"),
                               (store_root, dest, "an ancestor of the source store")):
        if str(inner).startswith(str(outer) + os.sep):
            raise MigrateError("the destination {} is {}; refused".format(dest, what))
    direction = "into-product" if dest == product_root else (
        "out-of-repo" if store_root == product_root else "local-to-local")
    if direction != "into-product" and (dest == product_root
                                        or str(dest).startswith(str(product_root) + os.sep)):
        raise MigrateError("the destination {} lies inside the product repository; a companion store "
                           "lives outside it, and the in-repo form is dir:. (refused)".format(dest))
    if direction == "into-product":
        pointer_value = "dir:."
    else:
        pointer_value = _pointer_value(product_root, dest)
    pointer_post = _pointer_bytes(pointer_value)

    cap = _acquire_lease(store_root)
    try:
        _require_clean_source(store_root, res.machine_rel)
        if direction == "into-product":
            _require_clean_source(product_root)
        _refuse_open_runs(res)
        _refuse_stranded_moves(store_root, dest != product_root)
        _require_valid_source(res)
        if direction == "into-product":
            if os.path.lexists(str(product_root / _opf_store.WORKING_DIRNAME)):
                raise MigrateError("the product repository already holds a {}/ tree; refused".format(
                    _opf_store.WORKING_DIRNAME))
            mode = "into-product"
        else:
            mode = _require_empty_destination(dest)
        _refuse_config_rewrites([store_root] if store_root == product_root
                                else [store_root, product_root], [dest, store_value])

        branch = _git_out(store_root, ["symbolic-ref", "--short", "-q", "HEAD"]) or "main"
        source_head = _git_out(store_root, ["rev-parse", "HEAD"])
        pointer_path = product_root / _opf_store.POINTER_REL
        pointer_pre = None
        if os.path.lexists(str(pointer_path)):
            with open(str(pointer_path), "rb") as fh:
                pointer_pre = fh.read()
        run_id = _new_run_id()
        run_dir = jdir / run_id
        old_desc = str(store_root) if direction != "out-of-repo" \
            else "{} (in-repo {}/)".format(product_root, _opf_store.WORKING_DIRNAME)
        new_desc = str(dest) if direction != "into-product" \
            else "{} (in-repo {}/, via review branch)".format(product_root,
                                                              _opf_store.WORKING_DIRNAME)
        intent = {"run_id": run_id, "direction": direction, "store_root": str(store_root),
                  "product_root": str(product_root), "dest": str(dest), "branch": branch,
                  "target": pointer_value, "source_head": source_head,
                  "remove_working": direction == "out-of-repo",
                  "pointer_pre_hex": "absent" if pointer_pre is None else pointer_pre.hex(),
                  "pointer_post_hex": pointer_post.hex(),
                  "old_desc": old_desc, "new_desc": new_desc}
        _write_frame(run_dir, "intent", intent)
        _phase("intent", intent)

        extract_root = Path(tempfile.mkdtemp(prefix="opf-migrate-extract-"))
        try:
            extract_dir = extract_root / "x.git"
            os.makedirs(str(extract_dir))
            mapping, tip = _extract_history(store_root, extract_dir, branch)
            digest = _mapping_digest(mapping)
            with open(str(run_dir / "mapping.txt"), "w", encoding="utf-8") as fh:
                for old, new in mapping:
                    fh.write("{} {}\n".format(old, new))
            _write_frame(run_dir, "extracted",
                         {"tip": tip, "count": len(mapping), "mapping_digest": digest})
            _phase("extracted", intent)

            ctx = {"direction": direction, "store_root": store_root, "product_root": product_root,
                   "dest": dest, "branch": branch, "tip": tip, "mapping": mapping,
                   "machine_rel": res.machine_rel, "machine_dir": res.machine_dir}
            if direction == "into-product":
                review, merge = _prepare_review_branch(product_root, extract_dir, branch, tip,
                                                       run_id, pointer_post)
                ctx["review"], ctx["merge"] = review, merge
                _write_frame(run_dir, "transferred", {"review": review, "merge": merge})
            else:
                _materialize_destination(dest, mode, extract_dir, branch, tip)
                _write_frame(run_dir, "transferred", {"dest": str(dest)})
            _phase("transferred", ctx)
        finally:
            shutil.rmtree(str(extract_root), ignore_errors=True)

        digests = _verify_clone_back(ctx)
        _write_frame(run_dir, "verified", dict(digests, mapping_digest=digest))
        _phase("verified", ctx)

        if direction == "into-product":
            review_tip = _record_on_review_branch(product_root, ctx, old_desc, new_desc, digest)
            _write_frame(run_dir, "recorded", {"review_tip": review_tip})
            _phase("recorded", ctx)
            _write_frame(run_dir, "complete", {"review": ctx["review"]})
            print("opf migrate: prepared the relocation of the store from {} into the product "
                  "repository as review branch {} (nothing committed to the current branch); the "
                  "move lands through normal review, and the old location is retained until it "
                  "does".format(store_root, ctx["review"]))
            print("opf migrate: replayed {} commits (commit ids rewritten; mapping digest {}; the "
                  "mapping is journaled at {})".format(len(mapping), digest, run_dir))
            return EXIT_OK

        carried = _carry_allocations(store_root, dest)
        _write_pointer(product_root, pointer_post)
        if intent["remove_working"]:
            # The lease substrate (the machine store's lease.toml) lives inside the very tree this
            # staged removal takes out of the working tree, so the lease is released FIRST; from
            # here migrate writes only the already-journaled product change, and the worklog append
            # below runs under the record verb's own lease at the destination.
            _release_lease(cap)
            cap = None
            _remove_working_tree(product_root)
        _write_frame(run_dir, "product-written",
                     {"carried": len(carried), "removed_working": bool(intent["remove_working"])})
        _phase("product-written", ctx)

        try:
            _record_relocation(product_root, old_desc, new_desc, digest)
            _write_frame(run_dir, "recorded", {"forward": False})
            _phase("recorded", ctx)
            res2 = _opf_store.resolve_store(product_root)
            if res2.status != _opf_store.RESOLVED:
                raise MigrateError("the relocated store does not resolve through the new pointer "
                                   "({}); rolled back".format(res2.detail))
            code, result = _doctor_exit(res2)
            if code != EXIT_OK:
                raise MigrateError("the relocated store is not doctor-VALID through the new pointer "
                                   "(status {}); rolled back".format(result.status))
        except MigrateError:
            _rollback_product_change(product_root, pointer_pre, intent["remove_working"])
            raise
        _write_frame(run_dir, "complete", {"forward": False})
        print("opf migrate: relocated the store from {} to {}; the committed pointer now names {} "
              "and the migration change is staged, uncommitted, for review".format(
                  old_desc, dest, pointer_value))
        if direction == "out-of-repo":
            print("opf migrate: the old in-repo {}/ tree was removed from the index and working "
                  "tree only; HEAD retains every byte until the migration change lands".format(
                      _opf_store.WORKING_DIRNAME))
        else:
            print("opf migrate: the old location {} is retained in place as the archived copy of "
                  "this move; the recorded worklog entry names it".format(store_root))
        print("opf migrate: replayed {} commits (commit ids rewritten; mapping digest {}; the "
              "mapping is journaled at {})".format(len(mapping), digest, run_dir))
        return EXIT_OK
    finally:
        if cap is not None:
            _release_lease(cap)


def _record_on_review_branch(product_root, ctx, old_desc, new_desc, digest):
    """Append the relocation worklog entry on the review branch, never the current branch: the entry
    is authored in a temporary clone checked out at the review branch (through the record verb's own
    journaled append), committed there, and fetched back as a fast-forward of the review ref."""
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="opf-migrate-record-"))
    try:
        repo = work / "r"
        _git(work, ["clone", "--quiet", "--no-hardlinks", "--branch", ctx["review"],
                    str(product_root), str(repo)])
        _record_relocation(repo, old_desc, new_desc, digest)
        _git(repo, ["add", "-A"])
        _git(repo, ["commit", "-q", "-m", "Record the store relocation in the worklog"],
             extra_env=_git_ident_env())
        res = _opf_store.resolve_store(repo)
        if res.status != _opf_store.RESOLVED:
            raise MigrateError("the review branch store does not resolve after the relocation was "
                               "recorded ({}); refused".format(res.detail))
        code, result = _doctor_exit(res)
        if code != EXIT_OK:
            raise MigrateError("the review branch store is not doctor-VALID after the relocation was "
                               "recorded (status {}); refused".format(result.status))
        tip = _git_out(repo, ["rev-parse", "HEAD"])
        _git(product_root, ["fetch", "--quiet", str(repo),
                            "refs/heads/{}:refs/heads/{}".format(ctx["review"], ctx["review"])])
        return tip
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def cli(rest):
    """`opf migrate --store <target> [--root DIR]`: exit 0 relocated (the product change staged,
    uncommitted), exit 2 on every refusal or cannot-evaluate; exit 1 is not used."""
    try:
        return _migrate(list(rest))
    except MigrateError as exc:
        print("opf migrate: refused: {} (fail-closed)".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


# --- self-test ------------------------------------------------------------------------------------------

def self_test():
    """Isolate fixture configuration (HOME, XDG, system git config) and restore the caller even on
    failure, the house self-test envelope."""
    _child_status_refusal_exit("_opf_migrate.py")
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-migrate-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1"):
            return self_test_isolated()


def _st_machine():
    """One doctor-VALID committed-store document set, built through the production emitters and
    builders (the construction the doctor suite proves VALID), never hand-built bytes. Returns
    (documents, changelog text)."""
    import _opf_changelog
    import _opf_init
    import _opf_release
    import _opf_schema
    ts = "2026-06-01T00:00:00Z"

    def wl(n):
        return {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
                "actor": {"kind": "maintainer"}, "kind": "added", "summary": "w{}".format(n)}

    def envelope(rid, rtype, status, **over):
        r = {"id": rid, "type": rtype, "status": status, "title": "t",
             "created_at": ts, "updated_at": ts, "actor": {"kind": "maintainer"}}
        r.update(over)
        return r

    def idx(records):
        return {"schema": 1, "record": records}

    types = {t: {"namespace": ns} for t, ns in (
        ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
        ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
        ("autonomous_decision", "AD"), ("block", "BL"),
        ("contribution", "CN"), ("maintainer_decision", "MD"), ("preference_pattern", "PP"))}
    manifest = {"opf": dict(_opf_init._manifest_model()["opf"]), "store": {"sync_target": ""},
                "types": types, "vendors": {"registered": []}, "archive": {"period": "year"}}
    full_wl = {n: wl(n) for n in (1, 2, 3, 4)}
    dig12 = _opf_release.compute_span_digest(full_wl, (1, 2))
    dig34 = _opf_release.compute_span_digest(full_wl, (3, 4))
    lines = ["# Changelog", "", "## unreleased", ""]
    for tok in ("1.1.0", "1.0.0"):
        lines += ["## {}".format(tok), "", "- {} notes".format(tok), ""]
    changelog = "\n".join(lines) + "\n"
    entries, _f = _opf_changelog._changelog_entries(changelog)
    emap = {}
    for tok, text in entries:
        emap.setdefault(tok, []).append(text)
    digs = {tok: _opf_changelog.freeze_digest(emap[tok][0]) for tok in ("1.1.0", "1.0.0")}
    version = {
        "schema": 1,
        "release": [{"version": "1.0.0", "date": ts, "worklog_span": ["WL-1", "WL-2"],
                     "coverage_digest": dig12},
                    {"version": "1.1.0", "date": ts, "worklog_span": ["WL-3", "WL-4"],
                     "coverage_digest": dig34}],
        "summary": [{"covers": "1.0.0", "status": "published", "digest": digs["1.0.0"]},
                    {"covers": "1.1.0", "status": "published", "digest": digs["1.1.0"]},
                    {"covers": "unreleased", "status": "working"}]}
    docs = {
        "manifest.toml": manifest,
        "counters.toml": {"schema": 1,
                          "counters": {"BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0,
                                       "BL": 0, "HO": 1, "RF": 0, "CN": 0, "MD": 0, "PP": 0}},
        "backlog_item.index.toml": idx([envelope("BI-1", "backlog_item", "done"),
                                        envelope("BI-2", "backlog_item", "open")]),
        "done.index.toml": idx([envelope("DN-1", "done", "recorded",
                                         links=[{"rel": "receipt_of", "id": "BI-1"}])]),
        "finding.index.toml": idx([]),
        "pending_decision.index.toml": idx([]),
        "handoff.index.toml": idx([envelope("HO-1", "handoff", "current")]),
        "reference.index.toml": idx([]),
        "autonomous_decision.index.toml": idx([]),
        "block.index.toml": idx([]),
        "contribution.index.toml": idx([]),
        "maintainer_decision.index.toml": idx([]),
        "preference_pattern.index.toml": idx([]),
        "worklog.toml": {"schema": 1, "entry": [wl(3), wl(4)]},
        "version.toml": version,
        "archive/2026/archive.toml": {"schema": 1, "moved": [],
                                      "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                         "destination": "archive/2026/worklog.toml"}]},
        "archive/2026/worklog.toml": {"schema": 1, "entry": [wl(1), wl(2)]},
    }
    docs.update(_opf_init.imported_leaf_documents(docs["manifest.toml"]))
    docs["counters.toml"]["counters"].update(
        {_opf_schema.IMPORTED_PREFIX + ns: 0
         for ns in _opf_init.imported_counter_namespaces(docs["manifest.toml"]) or ()})
    return docs, changelog


def _st_write_machine(root, docs):
    import _opf_emit
    mdir = Path(root) / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
    for rel, doc in docs.items():
        p = mdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")


def _st_git(cwd, *args, input_bytes=None):
    """A fixture git call with a pinned identity, over the same scrubbed environment the module's own
    runner uses; a failure is an OSError (a harness error, never a silent pass)."""
    git = _git_exe()
    cmd = [git, "--no-replace-objects", "-C", str(cwd),
           "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
           "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=_opf_observe._scrubbed_env(), input=input_bytes,
                              timeout=_GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("fixture git {} could not run in {} ({})".format(args, cwd, exc))
    if proc.returncode != 0:
        raise OSError("fixture git {} failed in {} (rc {}): {}".format(
            args, cwd, proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout.decode("utf-8", "replace").strip()


def _st_build_store(base, name):
    """A committed product root holding a doctor-VALID in-repo store, with TWO commits touching
    `.working/` so the replay has a shape to preserve (the intermediate commit is deliberately
    partial: only HEAD needs a doctor verdict)."""
    root = Path(base) / name
    root.mkdir()
    docs, changelog = _st_machine()
    first = {k: v for k, v in docs.items() if not k.startswith("archive/")}
    _st_write_machine(root, first)
    (root / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _st_git(root, "init", "-q")
    _st_git(root, "add", "-A")
    _st_git(root, "commit", "-q", "-m", "seed store")
    _st_write_machine(root, {k: v for k, v in docs.items() if k.startswith("archive/")})
    _st_git(root, "add", "-A")
    _st_git(root, "commit", "-q", "-m", "archive rotation")
    return root


def _st_run(argv):
    """Run the verb in process with stdout and stderr captured; returns (rc, out, err). A raised
    MigrateError never escapes cli, so any exception here is the vector's own injected one."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli(argv)
    return rc, out.getvalue(), err.getvalue()


def _st_snapshot(root):
    """(relpath, kind, content digest) of every entry under root, `.git` and the machine-local lease
    excluded, so a refusal vector can assert byte identity of the operand tree."""
    rows = {}
    root = str(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d != ".git"]
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                rows[rel] = ("symlink", os.readlink(path))
            else:
                with open(path, "rb") as fh:
                    rows[rel] = ("file", hashlib.sha256(fh.read()).hexdigest())
    return rows


def _st_doctored_working_tree(repo, machine_rel):
    """A `.working` tree object equal to HEAD's except that the worklog ledger blob is re-serialized
    (one appended comment byte run): the mutant extraction the byte-identity verification must turn
    red."""
    wl_rel = "{}/worklog.toml".format(machine_rel)
    blob = _git(repo, ["cat-file", "blob", "HEAD:{}".format(wl_rel)]) + b"# re-serialized\n"
    new_blob = _git_out(repo, ["hash-object", "-w", "--stdin"], input_bytes=blob)
    machine_name = machine_rel.split("/", 1)[1]

    def retree(spec, replace_name, replace_line):
        entries = _git(repo, ["ls-tree", "-z", spec])
        rows = []
        for entry in entries.split(b"\x00"):
            if not entry:
                continue
            meta, _tab, name = entry.partition(b"\t")
            if name.decode("utf-8") == replace_name:
                rows.append(replace_line)
            else:
                rows.append(entry)
        return _git_out(repo, ["mktree", "-z"], input_bytes=b"\x00".join(rows) + b"\x00")

    new_machine = retree("HEAD:{}".format(machine_rel), "worklog.toml",
                         "100644 blob {}\tworklog.toml".format(new_blob).encode("utf-8"))
    new_working = retree("HEAD:{}".format(_opf_store.WORKING_DIRNAME), machine_name,
                         "040000 tree {}\t{}".format(new_machine, machine_name).encode("utf-8"))
    return new_working


def _st_plant_archive(root, run, content, digest):
    """A minimal relocated-store tree carrying one adoption-archived copy and its persisted plan."""
    import json
    root = Path(root)
    copy = root / _opf_store.ARCHIVE_REL / "adoption" / run / "notes.txt"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(content)
    plan = root / _opf_store.IMPORTED_REL / "adoption" / run / "plan.toml"
    plan.parent.mkdir(parents=True, exist_ok=True)
    preservation = "{}/adoption/{}/notes.txt".format(_opf_store.ARCHIVE_REL, run)
    plan.write_text("[[sources]]\npath = \"notes.txt\"\ndigest = {}\npreservation = {}\n".format(
        json.dumps(digest), json.dumps(preservation)), encoding="utf-8")
    return preservation


def self_test_isolated():
    """End-to-end vectors over synthetic committed stores, each judged on exit codes, refusal
    messages, and byte or object identity, with the load-bearing checks each proven by an in-place
    mutation that turns the vector red. Returns 0 clean, 1 on a failing check, 2 on a harness
    error; SKIP (0) without git."""
    import tempfile
    from unittest.mock import patch
    this = sys.modules[__name__]
    if shutil.which("git") is None:
        print("opf-migrate self-test: SKIP (git not found on PATH; the committed-store fixtures "
              "cannot be built)")
        return EXIT_OK
    failures = []
    checked = [0]

    def check(name, cond, note=""):
        checked[0] += 1
        if not cond:
            failures.append("{}{}".format(name, " ({})".format(note) if note else ""))

    base = Path(tempfile.mkdtemp(prefix="opf-migrate-selftest-"))
    try:
        # --- grammar refusals (the parser, before any store resolution) --------------------------------
        for argv, needle in (([], "--store is required"),
                             (["--store"], "requires a target"),
                             (["--store", ""], "requires a non-empty target"),
                             (["--store", "dir:x", "--store", "dir:y"], "more than once"),
                             (["--store", "dir:x", "--root"], "requires a directory"),
                             (["--store", "dir:x", "--root", ""], "non-empty directory"),
                             (["--bogus"], "unrecognized argument")):
            rc, _out, err = _st_run(argv)
            check("grammar refuses {!r}".format(argv), rc == EXIT_MALFORMED and needle in err,
                  err.strip()[:120])

        # --- a NOT-ADOPTED root refuses, byte-identical --------------------------------------------------
        plain = base / "plain"
        plain.mkdir()
        (plain / "README").write_text("x\n", encoding="utf-8")
        _st_git(plain, "init", "-q")
        _st_git(plain, "add", "-A")
        _st_git(plain, "commit", "-q", "-m", "seed")
        before = _st_snapshot(plain)
        rc, _out, err = _st_run(["--store", "dir:../elsewhere", "--root", str(plain)])
        check("a NOT-ADOPTED root refuses", rc == EXIT_MALFORMED and "nothing to relocate" in err,
              err.strip()[:120])
        check("a NOT-ADOPTED refusal writes nothing", _st_snapshot(plain) == before)

        # --- remote and bare-path targets refuse, nothing written (the local-directory provider only) ---
        p_ref = _st_build_store(base, "p-ref")
        before = _st_snapshot(p_ref)
        for value in ("git:git@git.example.invalid:acme/ops.git",
                      "https://git.example.invalid/acme/ops.git",
                      "git@git.example.invalid:acme/ops.git"):
            rc, _out, err = _st_run(["--store", value, "--root", str(p_ref)])
            check("a remote target {!r} refuses".format(value),
                  rc == EXIT_MALFORMED and "remote" in err and "dir: targets only" in err,
                  err.strip()[:120])
        for value in ("github:acme/ops", "gitlab:acme/ops"):
            rc, _out, err = _st_run(["--store", value, "--root", str(p_ref)])
            check("the host target {!r} refuses as an unregistered provider".format(value),
                  rc == EXIT_MALFORMED and "unregistered provider" in err, err.strip()[:120])
        with patch.object(this, "_UNREGISTERED_PROVIDERS", ()):
            rc_flip, _out, err_flip = _st_run(["--store", "github:acme/ops", "--root", str(p_ref)])
        check("emptying the unregistered-provider roster turns the spec 5.6 refusal red",
              rc_flip == EXIT_MALFORMED and "unregistered provider" not in err_flip,
              err_flip.strip()[:120])
        rc, _out, err = _st_run(["--store", "/abs/elsewhere", "--root", str(p_ref)])
        check("a bare path target refuses toward dir:",
              rc == EXIT_MALFORMED and "dir:<path>" in err, err.strip()[:120])
        rc, _out, err = _st_run(["--store", "dir:.", "--root", str(p_ref)])
        check("relocating an in-repo store onto itself refuses",
              rc == EXIT_MALFORMED and "the source" in err, err.strip()[:120])
        check("the refused targets wrote nothing", _st_snapshot(p_ref) == before)

        # --- the relocation run-id grammar is the store's own journal vocabulary (spec 4.2) -------------
        rid = _new_run_id()
        check("a fresh run id satisfies the store's relocation grammar", _is_run_id(rid), rid)
        for bad in ("record-20260101T000000Z-0123456789abcdef", "reloc-20260101T000000Z-XYZ",
                    "RELOC-20260101T000000Z-0123456789abcdef",
                    "reloc-20260101T000000Z-0123456789abcdef/x"):
            check("the store grammar refuses the run id {!r}".format(bad), not _is_run_id(bad))
        with patch.object(_opf_store, "JOURNAL_KINDS", _opf_store.STAGING_KINDS + ("record",)):
            check("dropping the relocation kind from the store vocabulary turns the grammar red",
                  not _is_run_id(rid))
        foreign = _git_common_dir(p_ref) / JOURNAL_DIRNAME / "record-20260101T000000Z-0123456789abcdef"
        foreign.mkdir(parents=True)
        check("a foreign-kind name in the journal home is never a pending relocation run",
              _pending_runs(_git_common_dir(p_ref) / JOURNAL_DIRNAME) == [])
        shutil.rmtree(str(_git_common_dir(p_ref) / JOURNAL_DIRNAME))

        # --- a target no publishable committed pointer can express refuses before anything is written ---
        rc, _out, err = _st_run(["--store", "dir:../bad?dest", "--root", str(p_ref)])
        check("an unpublishable pointer spelling refuses before anything is written",
              rc == EXIT_MALFORMED and "publishable" in err
              and not (base / "bad?dest").exists()
              and not (_git_common_dir(p_ref) / JOURNAL_DIRNAME).exists(), err.strip()[:160])
        p_bad = _st_build_store(base, "p-bad-pointer")
        with patch.object(this, "_POINTER_SAFE_RE", re.compile(r"[^\0]+\Z")):
            rc_flip, _out, _err = _st_run(["--store", "dir:../bad?dest", "--root", str(p_bad)])
        check("widening the pointer alphabet turns the publishable-pointer refusal red",
              rc_flip == EXIT_OK, "rc {}".format(rc_flip))

        # --- admission refuses before the lease (spec 9.2) ----------------------------------------------
        import _opf_emit
        for label, mutate in (
                ("homes missing", lambda m: m["opf"].pop("homes")),
                ("spec_version below the floor", lambda m: m["opf"].update(spec_version="1.9.0")),
                ("a dedicated sync_target", lambda m: m["store"].update(
                    sync_target="git@git.example.invalid:acme/ops.git"))):
            root = _st_build_store(base, "p-adm-{}".format(label.split()[0] if "sync" not in label
                                                           else "sync"))
            docs, _cl = _st_machine()
            mutate(docs["manifest.toml"])
            mpath = (root / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR /
                     "manifest.toml")
            mpath.write_text(_opf_emit.emit(docs["manifest.toml"]), encoding="utf-8")
            _st_git(root, "add", "-A")
            _st_git(root, "commit", "-q", "-m", "manifest variant")
            rc, _out, err = _st_run(["--store", "dir:../adm-dest", "--root", str(root)])
            check("admission refuses {}".format(label), rc == EXIT_MALFORMED, err.strip()[:160])
            common = _git_common_dir(root)
            check("admission for {} refused before the lease and the journal".format(label),
                  not (common / _opf_oplock.CONTROL_DIRNAME).exists()
                  and not (common / JOURNAL_DIRNAME).exists())
            check("admission for {} wrote no destination".format(label),
                  not (base / "adm-dest").exists())

        # --- the happy path: in-repo to a local companion repository ------------------------------------
        p1 = _st_build_store(base, "p1")
        dest1 = base / "companion"
        version_before = (p1 / "VERSION").read_bytes()
        changelog_before = (p1 / "CHANGELOG.md").read_bytes()
        src_head = _st_git(p1, "rev-parse", "HEAD")
        src_count = int(_st_git(p1, "rev-list", "--count", "--full-history", "HEAD", "--",
                                _opf_store.WORKING_DIRNAME))
        rc, out, err = _st_run(["--store", "dir:../companion", "--root", str(p1)])
        check("the in-repo to local relocation succeeds", rc == EXIT_OK, (err or out).strip()[:300])
        pointer = (p1 / _opf_store.POINTER_REL).read_bytes()
        check("the committed pointer is rewritten to the relative dir: spelling",
              pointer == b'[store]\ntarget = "dir:../companion"\n', pointer[:80])
        check("the tracked working tree leaves the index, uncommitted",
              _st_git(p1, "ls-files", "--", _opf_store.WORKING_DIRNAME) == ""
              and not (p1 / _opf_store.WORKING_DIRNAME).exists()
              and _st_git(p1, "rev-parse", "HEAD") == src_head
              and _st_git(p1, "rev-parse", "HEAD:{}".format(_opf_store.WORKING_DIRNAME)) != "")
        check("the public deliverables stay byte-identical at the product root",
              (p1 / "VERSION").read_bytes() == version_before
              and (p1 / "CHANGELOG.md").read_bytes() == changelog_before)
        dest_count = int(_st_git(dest1, "rev-list", "--count", "HEAD"))
        check("history is replayed commit for commit", dest_count == src_count == 2,
              "dest {} src {}".format(dest_count, src_count))
        jruns = sorted((_git_common_dir(p1) / JOURNAL_DIRNAME).iterdir())
        check("one relocation journal run exists with a complete frame",
              len(jruns) == 1 and (jruns[0] / "complete.toml").is_file())
        pairs = [line.split() for line in
                 (jruns[0] / "mapping.txt").read_text(encoding="utf-8").splitlines()]
        check("the journaled mapping covers the replay", len(pairs) == src_count)
        trees_equal = all(
            _st_git(p1, "rev-parse", "{}:{}".format(old, _opf_store.WORKING_DIRNAME))
            == _st_git(dest1, "rev-parse", "{}:{}".format(new, _opf_store.WORKING_DIRNAME))
            for old, new in pairs)
        check("every mapped commit holds an identical working tree", trees_equal)
        meta_equal = all(
            _st_git(p1, "show", "-s", "--format=%an %ae %ad %cn %ce %cd %s", "--date=raw", old)
            == _st_git(dest1, "show", "-s", "--format=%an %ae %ad %cn %ce %cd %s", "--date=raw", new)
            for old, new in pairs)
        check("author, committer, dates and subject are preserved", meta_equal)
        wl_rel = ".working/toml/worklog.toml"
        ct_rel = ".working/toml/counters.toml"
        check("the worklog is byte-identical at the destination HEAD",
              _st_git(p1, "cat-file", "blob", "HEAD:{}".format(wl_rel))
              == _st_git(dest1, "cat-file", "blob", "HEAD:{}".format(wl_rel)))
        check("the transfer leaves counters.toml byte-identical at the destination HEAD",
              _st_git(p1, "cat-file", "blob", "HEAD:{}".format(ct_rel))
              == _st_git(dest1, "cat-file", "blob", "HEAD:{}".format(ct_rel)))
        wl_text = (dest1 / wl_rel).read_text(encoding="utf-8")
        check("the recorded worklog entry names both locations, in the destination working copy",
              str(p1) in wl_text and str(dest1) in wl_text and "WL-5" in wl_text)
        check("the destination claims no remote and no sync target",
              _st_git(dest1, "remote") == ""
              and 'sync_target = ""' in (dest1 / ".working/toml/manifest.toml").read_text(
                  encoding="utf-8"))
        res1 = _opf_store.resolve_store(p1)
        code1, _result1 = _doctor_exit(res1)
        check("the doctor is VALID through the new pointer",
              res1.status == _opf_store.RESOLVED and code1 == EXIT_OK
              and os.path.abspath(str(res1.store_root)) == os.path.abspath(str(dest1)))

        # --- local to local, with the machine-local reservations carried --------------------------------
        # land only the recorded change: the journal home is machine-local and stays untracked, as the
        # record verb's own report says
        _st_git(dest1, "add", "--", ".working/toml")
        _st_git(dest1, "commit", "-q", "-m", "land the relocation record")
        alloc_rel = None
        alloc_dir = dest1 / _opf_store.JOURNALS_REL / "record" / "allocations"
        if alloc_dir.is_dir():
            names = sorted(os.listdir(str(alloc_dir)))
            if names:
                alloc_rel = "{}/record/allocations/{}".format(_opf_store.JOURNALS_REL, names[0])
        check("the record append left a machine-local reservation to carry", alloc_rel is not None)
        dest2 = base / "companion2"
        rc, out, err = _st_run(["--store", "dir:../companion2", "--root", str(p1)])
        check("the local to local relocation succeeds", rc == EXIT_OK, (err or out).strip()[:300])
        check("the pointer now names the second companion",
              (p1 / _opf_store.POINTER_REL).read_bytes()
              == b'[store]\ntarget = "dir:../companion2"\n')
        check("the old local store is retained in place as the archived copy",
              (dest1 / ".working/toml/manifest.toml").is_file()
              and "retained in place" in out)
        check("the machine-local reservations are carried byte for byte, untracked",
              alloc_rel is not None and (dest2 / alloc_rel).is_file()
              and (dest2 / alloc_rel).read_bytes() == (dest1 / alloc_rel).read_bytes()
              and alloc_rel not in _st_git(dest2, "ls-files"))
        res2 = _opf_store.resolve_store(p1)
        code2, _result2 = _doctor_exit(res2)
        check("the doctor is VALID through the second pointer", code2 == EXIT_OK)

        # --- round trip: back into the product repository, as a review branch ---------------------------
        _st_git(p1, "add", "-A")
        _st_git(p1, "commit", "-q", "-m", "land the migration change")
        _st_git(dest2, "add", "--", ".working/toml")
        _st_git(dest2, "commit", "-q", "-m", "land the relocation record")
        head_before = _st_git(p1, "rev-parse", "HEAD")
        branch_before = _st_git(p1, "symbolic-ref", "--short", "HEAD")
        rc, out, err = _st_run(["--store", "dir:.", "--root", str(p1)])
        check("the relocation back into the product repository succeeds", rc == EXIT_OK,
              (err or out).strip()[:300])
        check("the current branch and working tree are untouched",
              _st_git(p1, "rev-parse", "HEAD") == head_before
              and _st_git(p1, "symbolic-ref", "--short", "HEAD") == branch_before
              and not (p1 / _opf_store.WORKING_DIRNAME).exists())
        reviews = [line for line in _st_git(p1, "for-each-ref", "--format=%(refname:short)",
                                            "refs/heads/opf-migrate-*").splitlines() if line]
        check("one review branch was prepared", len(reviews) == 1, repr(reviews))
        review = reviews[0]
        merge = _st_git(p1, "rev-parse", "{}~1".format(review))
        check("the review merge carries the replayed history as its second parent",
              _st_git(p1, "rev-parse", "{}^1".format(merge)) == head_before
              and _st_git(p1, "rev-parse", "{}:{}".format(merge, _opf_store.WORKING_DIRNAME))
              == _st_git(dest2, "rev-parse", "HEAD:{}".format(_opf_store.WORKING_DIRNAME)))
        check("the review branch pointer returns to the in-repo spelling",
              _git(p1, ["cat-file", "blob", "{}:{}".format(review, _opf_store.POINTER_REL)])
              == _pointer_bytes("dir:."))
        check("the deliverables are byte-identical on the review branch",
              _st_git(p1, "cat-file", "blob", "{}:VERSION".format(review))
              == _st_git(p1, "cat-file", "blob", "HEAD:VERSION")
              and _st_git(p1, "cat-file", "blob", "{}:CHANGELOG.md".format(review))
              == _st_git(p1, "cat-file", "blob", "HEAD:CHANGELOG.md"))
        review_wl = _st_git(p1, "cat-file", "blob", "{}:{}".format(review, wl_rel))
        check("the review branch worklog entry names both locations",
              str(dest2) in review_wl and str(p1) in review_wl)
        clone = base / "review-clone"
        _st_git(base, "clone", "-q", "--no-hardlinks", "--branch", review, str(p1), str(clone))
        _st_git(clone, "remote", "remove", "origin")
        res3 = _opf_store.resolve_store(clone)
        code3, _result3 = _doctor_exit(res3)
        check("the review branch store is doctor-VALID in a fresh clone",
              res3.status == _opf_store.RESOLVED and code3 == EXIT_OK)

        # --- refusals over live stores -------------------------------------------------------------------
        p2 = _st_build_store(base, "p2")

        # a non-empty destination refuses, destination untouched
        full_dest = base / "occupied"
        full_dest.mkdir()
        (full_dest / "keep.txt").write_text("keep\n", encoding="utf-8")
        occupied_before = _st_snapshot(full_dest)
        rc, _out, err = _st_run(["--store", "dir:../occupied", "--root", str(p2)])
        check("a non-empty destination refuses", rc == EXIT_MALFORMED and "not empty" in err,
              err.strip()[:160])
        check("the non-empty destination is untouched",
              _st_snapshot(full_dest) == occupied_before and not (full_dest / ".git").exists())
        # the emptiness check is load-bearing: with it skipped, the same run touches the destination
        with patch.object(this, "_require_empty_destination", lambda dest: "empty-dir"):
            rc_flip, _out, _err = _st_run(["--store", "dir:../occupied", "--root", str(p2)])
        check("skipping the emptiness check turns the refusal vector red",
              (full_dest / ".git").exists(), "rc {}".format(rc_flip))
        shutil.rmtree(str(full_dest))
        # the flip run journaled an intent before its own verification refused; retire it so the
        # next vector exercises its own refusal, not reconcile-first
        shutil.rmtree(str(_git_common_dir(p2) / JOURNAL_DIRNAME))

        # an insteadOf rewrite of the destination refuses, and the redirected repository stays empty
        redirect = base / "redirect"
        redirect.mkdir()
        _st_git(p2, "config", "url.{}.pushInsteadOf".format(redirect), str(base))
        rc, _out, err = _st_run(["--store", "dir:../insteadof-dest", "--root", str(p2)])
        check("a pushInsteadOf rewrite of the destination refuses",
              rc == EXIT_MALFORMED and "rewrites the destination" in err, err.strip()[:160])
        check("the rewrite refusal writes neither endpoint",
              not (base / "insteadof-dest").exists() and os.listdir(str(redirect)) == [])
        with patch.object(this, "_refuse_config_rewrites", lambda repos, destinations: None):
            rc_flip, _out, _err = _st_run(["--store", "dir:../insteadof-dest", "--root", str(p2)])
        check("dropping the rewrite filter turns the refusal vector red", rc_flip == EXIT_OK,
              "rc {}".format(rc_flip))
        check("the transfer itself never used the rewritten endpoint",
              os.listdir(str(redirect)) == [])
        # restore p2 for the next vectors: undo the staged product change and the destination
        _st_git(p2, "config", "--unset", "url.{}.pushInsteadOf".format(redirect))
        os.unlink(str(p2 / _opf_store.POINTER_REL))
        _st_git(p2, "checkout", "HEAD", "--", _opf_store.WORKING_DIRNAME)
        shutil.rmtree(str(base / "insteadof-dest"))
        shutil.rmtree(str(_git_common_dir(p2) / JOURNAL_DIRNAME))

        # a dirty or mid-merge source refuses
        wl_path = p2 / wl_rel
        original = wl_path.read_bytes()
        wl_path.write_bytes(original + b"# dirty\n")
        rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
        check("a dirty working tree refuses", rc == EXIT_MALFORMED and "does not equal HEAD" in err,
              err.strip()[:160])
        wl_path.write_bytes(original)
        merge_marker = _git_common_dir(p2) / "MERGE_HEAD"
        merge_marker.write_text(_st_git(p2, "rev-parse", "HEAD") + "\n", encoding="utf-8")
        rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
        check("a merge in progress refuses", rc == EXIT_MALFORMED and "in progress" in err,
              err.strip()[:160])
        os.unlink(str(merge_marker))

        # a live lease held by another holder refuses and is never seized
        held = _opf_oplock.acquire_operation(p2, "migrate")
        try:
            rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
            check("a held lease refuses", rc == EXIT_MALFORMED and "never seized" in err,
                  err.strip()[:160])
        finally:
            _opf_oplock.release_operation(held)
        check("the refused run did not seize the lease (the holder still releases cleanly)", True)

        class _FakeCap:
            pass

        # the flip runs on its own fixture: proceeding under a held lease wrecks that holder's
        # substrate (the relocation removes the tree carrying its lease), which is the damage the
        # acquisition exists to prevent
        p2b = _st_build_store(base, "p2b")
        held2 = _opf_oplock.acquire_operation(p2b, "migrate")
        wrecked = False
        try:
            with patch.object(this, "_acquire_lease", lambda root: _FakeCap()), \
                    patch.object(this, "_release_lease", lambda cap: None):
                rc_flip, _out, _err = _st_run(["--store", "dir:../p2b-dest", "--root", str(p2b)])
        finally:
            try:
                _opf_oplock.release_operation(held2)
            except _opf_oplock.OpLockError:
                wrecked = True
        check("skipping the lease acquisition turns the held-lease vector red",
              rc_flip == EXIT_OK and wrecked, "rc {} wrecked {}".format(rc_flip, wrecked))

        # an open journal transaction or a staging run refuses
        staging_run = p2 / _opf_store.STAGING_REL / "import" / "imp-20260101T000000Z-0123456789abcdef"
        staging_run.mkdir(parents=True)
        rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
        check("a staging run refuses", rc == EXIT_MALFORMED and "staging run" in err,
              err.strip()[:160])
        shutil.rmtree(str(p2 / _opf_store.STAGING_REL))
        jrel = _opf_store.journal_root("record")
        txn = p2 / jrel / "txn-open"
        txn.mkdir(parents=True)
        root_fd = _opf_store._open_root_fd(p2)
        try:
            jr_fd = _journal.open_journal_root_fd(root_fd, jrel)
            try:
                _journal.publish(jr_fd, txn, _journal.F_INTENT,
                                 {"txn": "txn-open", "header": {"kind": "record"}, "ops": []})
            finally:
                _opf_store._close_fd_exc_safe(jr_fd)
        finally:
            _opf_store._close_fd_exc_safe(root_fd)
        rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
        check("an open journal transaction refuses",
              rc == EXIT_MALFORMED and "open record journal transaction" in err, err.strip()[:160])
        shutil.rmtree(str(p2 / _opf_store.JOURNALS_REL))
        (p2 / jrel).mkdir(parents=True)
        os.symlink("elsewhere", str(p2 / jrel / "txn-link"))
        rc, _out, err = _st_run(["--store", "dir:../p2-dest", "--root", str(p2)])
        check("an unclassifiable journal refuses fail-closed",
              rc == EXIT_MALFORMED and "journal" in err, err.strip()[:160])
        shutil.rmtree(str(p2 / _opf_store.JOURNALS_REL))

        # a default Move row with an unrecorded retirement refuses a destination outside the product
        # root (its own fixture: the persisted plan and the live source are committed store content)
        import json
        p2c = _st_build_store(base, "p2c")
        plan_dir = p2c / _opf_store.IMPORTED_REL / "adoption" / "adopt-20260101T000000Z-0123456789abcdef"
        plan_dir.mkdir(parents=True)
        (p2c / "legacy-notes.md").write_text("legacy\n", encoding="utf-8")
        (plan_dir / "plan.toml").write_text(
            "[[sources]]\npath = \"legacy-notes.md\"\ndigest = {}\ndisposition = \"move\"\n"
            "occupying = false\npreservation = {}\n".format(
                json.dumps("sha256:" + hashlib.sha256(b"legacy\n").hexdigest()),
                json.dumps(".working/archive/moved/legacy-notes.md")), encoding="utf-8")
        _st_git(p2c, "add", "-A")
        _st_git(p2c, "commit", "-q", "-m", "a pending move")
        rc, _out, err = _st_run(["--store", "dir:../p2c-dest", "--root", str(p2c)])
        check("a pending default move refuses an outside destination",
              rc == EXIT_MALFORMED and "strand" in err, err.strip()[:160])
        # a recorded retirement (the sealed retirement inventory in the bundle) ends the refusal
        (plan_dir / _RETIREMENT_INVENTORY_NAME).write_text("sealed = true\n", encoding="utf-8")
        try:
            _refuse_stranded_moves(p2c, True)
            recorded_ends = True
        except MigrateError:
            recorded_ends = False
        check("a recorded retirement ends the stranded-move refusal", recorded_ends)
        os.unlink(str(plan_dir / _RETIREMENT_INVENTORY_NAME))
        # the refusal reads the recorded state, not the live file: a source gone out of band with
        # its retirement still unrecorded refuses the same way
        os.unlink(str(p2c / "legacy-notes.md"))
        try:
            _refuse_stranded_moves(p2c, True)
            gone_refuses = False
        except MigrateError as exc:
            gone_refuses = "strand" in str(exc)
        check("an unrecorded retirement refuses even after the source file is gone", gone_refuses)
        check("an inside-product destination never trips the stranded-move refusal",
              _refuse_stranded_moves(p2c, False) is None)
        with patch.object(this, "_refuse_stranded_moves",
                          lambda store_root, dest_outside_product: None):
            rc_flip, _out, err_flip = _st_run(["--store", "dir:../p2c-dest", "--root", str(p2c)])
        check("dropping the stranded-move refusal turns its vector red", "strand" not in err_flip,
              err_flip.strip()[:160])
        # an explicitly named destination is not the default row the refusal covers, for a
        # non-occupying row (its preservation IS the destination) and for an occupying row (the
        # plan's move-file op names it); a default occupying row still refuses through its op
        for name, body, wants_refusal in (
                ("mv-explicit", "[[sources]]\npath = \"a.md\"\ndisposition = \"move\"\n"
                                "occupying = false\npreservation = \"docs/a.md\"\n", False),
                ("mv-occ-named", "[[sources]]\npath = \"b.md\"\ndisposition = \"move\"\n"
                                 "occupying = true\n[[ops]]\nop = \"move-file\"\nsource = \"b.md\"\n"
                                 "destination = \"docs/b.md\"\n", False),
                ("mv-occ-default", "[[sources]]\npath = \"c.md\"\ndisposition = \"move\"\n"
                                   "occupying = true\n[[ops]]\nop = \"move-file\"\nsource = \"c.md\"\n"
                                   "destination = \".working/archive/moved/c.md\"\n", True),
                ("mv-occ-unreadable", "[[sources]]\npath = \"d.md\"\ndisposition = \"move\"\n"
                                      "occupying = true\n", True)):
            mv_root = base / name
            mv_dir = mv_root / _opf_store.IMPORTED_REL / "adoption" / "adopt-20260101T000000Z-0123456789abcdef"
            mv_dir.mkdir(parents=True)
            (mv_dir / "plan.toml").write_text(body, encoding="utf-8")
            try:
                _refuse_stranded_moves(mv_root, True)
                refused = False
            except MigrateError:
                refused = True
            check("the {} move row {} the outside-destination refusal".format(
                name, "trips" if wants_refusal else "does not trip"), refused == wants_refusal)

        # --- the extraction and verification mutants over fresh fixtures --------------------------------
        # flattening the replay to one commit is caught by the independent commit-count reconcile
        p3 = _st_build_store(base, "p3")
        real_rev_list = _rev_list_working
        with patch.object(this, "_rev_list_working",
                          lambda root: [(real_rev_list(root)[-1][0], [])]):
            rc, _out, err = _st_run(["--store", "dir:../p3-dest", "--root", str(p3)])
        check("a flattened extraction refuses at verification",
              rc == EXIT_MALFORMED and "commits" in err and "replayed" in err, err.strip()[:200])
        check("the flattened run left the source and pointer untouched",
              not (p3 / _opf_store.POINTER_REL).exists()
              and (p3 / _opf_store.WORKING_DIRNAME).is_dir())

        # a re-serialized worklog in the extraction is caught by the byte-identity reconcile
        p4 = _st_build_store(base, "p4")
        doctored = _st_doctored_working_tree(p4, ".working/toml")
        real_subtree = _subtree_for

        def tampered_subtree(repo, sha):
            tree = real_subtree(repo, sha)
            if Path(repo) == p4 and sha not in ("HEAD",):
                head_tree = real_subtree(repo, "HEAD")
                if tree == head_tree:
                    return doctored
            return tree

        with patch.object(this, "_subtree_for", tampered_subtree):
            rc, _out, err = _st_run(["--store", "dir:../p4-dest", "--root", str(p4)])
        check("a re-serialized worklog refuses at verification",
              rc == EXIT_MALFORMED and ("worklog" in err or "identical" in err), err.strip()[:200])
        check("the tampered run left the source and pointer untouched",
              not (p4 / _opf_store.POINTER_REL).exists()
              and (p4 / _opf_store.WORKING_DIRNAME).is_dir())

        # a destination tampered between transfer and verification refuses; skipping the clone-back
        # verification turns that red
        p5 = _st_build_store(base, "p5")

        def tamper(phase, ctx):
            if phase == "transferred":
                _st_git(ctx["dest"], "commit", "-q", "--amend", "-m", "tampered")

        with patch.object(this, "_PHASE_HOOK", tamper):
            rc, _out, err = _st_run(["--store", "dir:../p5-dest", "--root", str(p5)])
        check("a tampered destination refuses before anything is retired",
              rc == EXIT_MALFORMED and "no longer holds the extracted" in err, err.strip()[:200])
        check("the tampered-destination run left the source and pointer untouched",
              not (p5 / _opf_store.POINTER_REL).exists()
              and (p5 / _opf_store.WORKING_DIRNAME).is_dir())
        shutil.rmtree(str(base / "p5-dest"))
        shutil.rmtree(str(_git_common_dir(p5) / JOURNAL_DIRNAME))
        with patch.object(this, "_PHASE_HOOK", tamper), \
                patch.object(this, "_verify_clone_back",
                             lambda ctx: {"archived_copies": 0, "working_tree": ""}):
            rc_flip, _out, _err = _st_run(["--store", "dir:../p5-dest", "--root", str(p5)])
        check("skipping the clone-back verification turns the tamper vector red",
              rc_flip == EXIT_OK and (p5 / _opf_store.POINTER_REL).exists(),
              "rc {}".format(rc_flip))

        # --- the adoption-archive digest re-verification ------------------------------------------------
        arun = "adopt-20260101T000000Z-0123456789abcdef"
        good = base / "arch-good"
        digest = "sha256:" + hashlib.sha256(b"frozen\n").hexdigest()
        _st_plant_archive(good, arun, b"frozen\n", digest)
        check("a digest-matched archived copy verifies", _verify_adoption_archive(good) == 1)
        bad = base / "arch-bad"
        planted = _st_plant_archive(bad, arun, b"tampered\n", digest)

        def archive_refuses(root, needle):
            try:
                _verify_adoption_archive(root)
            except MigrateError as exc:
                return needle in str(exc) and planted in str(exc)
            return False

        check("a digest-mismatched archived copy refuses, naming the path",
              archive_refuses(bad, "does not match its plan digest"))
        noplan = base / "arch-noplan"
        _st_plant_archive(noplan, arun, b"frozen\n", digest)
        os.unlink(str(noplan / _opf_store.IMPORTED_REL / "adoption" / arun / "plan.toml"))
        try:
            _verify_adoption_archive(noplan)
            check("a missing persisted plan refuses", False)
        except MigrateError as exc:
            check("a missing persisted plan refuses", "cannot be read" in str(exc), str(exc)[:160])
        unplanned = base / "arch-unplanned"
        _st_plant_archive(unplanned, arun, b"frozen\n", digest)
        stray = unplanned / _opf_store.ARCHIVE_REL / "adoption" / arun / "stray.txt"
        stray.write_bytes(b"stray\n")
        try:
            _verify_adoption_archive(unplanned)
            check("an archived copy no plan claims refuses", False)
        except MigrateError as exc:
            check("an archived copy no plan claims refuses", "not claimed" in str(exc),
                  str(exc)[:160])
        # the re-verification is wired into the clone-back reconcile, and dropping it turns the
        # corrupt fixture green
        calls = []
        real_verify_archive = _verify_adoption_archive

        def recording(root):
            calls.append(Path(root))
            return real_verify_archive(root)

        p6 = _st_build_store(base, "p6")
        probe_calls = []
        real_probe = _require_wiring_probe

        def recording_probe(root):
            probe_calls.append(Path(root))
            return real_probe(root)

        with patch.object(this, "_verify_adoption_archive", recording), \
                patch.object(this, "_require_wiring_probe", recording_probe):
            rc, _out, err = _st_run(["--store", "dir:../p6-dest", "--root", str(p6)])
        check("the archive re-verification runs on the clone-back", rc == EXIT_OK and len(calls) >= 1,
              (err or "rc {}".format(rc))[:160])
        with patch.object(this, "_verify_adoption_archive", lambda root: 0):
            try:
                refused = False
                _verify_adoption_archive(bad)
            except MigrateError:
                refused = True
        check("dropping the archive re-verification turns the corrupt-copy vector red", not refused)

        # --- the completion check 5 probe at the destination (spec 14.1/14.2) ---------------------------
        check("the completion probe gate runs at the destination on the verification reconcile",
              probe_calls == [base / "p6-dest"], repr(probe_calls))
        check("no adoption home needs no probe", _require_wiring_probe(base / "p6-dest") == 0)
        probed = base / "probe-bundle"
        arun2 = "adopt-20260202T000000Z-fedcba9876543210"
        probed_dir = probed / _opf_store.IMPORTED_REL / "adoption" / arun2
        probed_dir.mkdir(parents=True)
        (probed_dir / "plan.toml").write_text("[[sources]]\npath = \"x.md\"\n", encoding="utf-8")
        try:
            _require_wiring_probe(probed)
            probe_refuses = False
        except MigrateError as exc:
            probe_refuses = "completion check 5" in str(exc) and arun2 in str(exc)
        check("an adoption bundle refuses at the completion check 5 probe, naming the run",
              probe_refuses)
        arch_only = base / "probe-archive-only"
        copy_dir = arch_only / _opf_store.ARCHIVE_REL / "adoption" / arun2
        copy_dir.mkdir(parents=True)
        (copy_dir / "notes.txt").write_bytes(b"frozen\n")
        try:
            _require_wiring_probe(arch_only)
            arch_refuses = False
        except MigrateError as exc:
            arch_refuses = "completion check 5" in str(exc)
        check("an adoption archive with no bundle refuses at the probe the same way", arch_refuses)
        with patch.object(this, "_require_wiring_probe", lambda root: 0):
            check("dropping the probe gate turns the adoption-home refusal red",
                  _require_wiring_probe(probed) == 0)
        # a probe refusal on the live path refuses the relocation with nothing durable staged, and
        # before any old tree is removed

        def raising_probe(root):
            raise MigrateError("the adoption home exists and the completion check 5 probe did not "
                               "pass at the destination (spec 14.1/14.2)")

        p6b = _st_build_store(base, "p6b")
        with patch.object(this, "_require_wiring_probe", raising_probe):
            rc, _out, err = _st_run(["--store", "dir:../p6b-dest", "--root", str(p6b)])
        check("a probe refusal on the live path refuses before anything durable is staged",
              rc == EXIT_MALFORMED and "completion check 5" in err
              and not (p6b / _opf_store.POINTER_REL).exists()
              and (p6b / _opf_store.WORKING_DIRNAME).is_dir(), err.strip()[:200])

        # --- interruption, reconcile-first, and forward completion --------------------------------------
        def kill_at(phase_name):
            def hook(phase, ctx):
                if phase == phase_name:
                    raise _InjectedKill(phase_name)
            return hook

        def run_killed(root, dest_rel, phase_name):
            import contextlib
            import io
            with patch.object(this, "_PHASE_HOOK", kill_at(phase_name)):
                try:
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        cli(["--store", dest_rel, "--root", str(root)])
                    return False
                except _InjectedKill:
                    return True

        # killed before verification: nothing durable, reconcile marks and refuses, a re-run succeeds
        p7 = _st_build_store(base, "p7")
        check("the extraction-boundary kill lands", run_killed(p7, "dir:../p7-dest", "extracted"))
        check("before verification nothing durable is staged",
              not (p7 / _opf_store.POINTER_REL).exists()
              and (p7 / _opf_store.WORKING_DIRNAME).is_dir())
        rc, _out, err = _st_run(["--store", "dir:../p7-dest", "--root", str(p7)])
        check("the next run reconciles first and refuses",
              rc == EXIT_MALFORMED and "reconciled" in err
              and "before verification" in err, err.strip()[:300])
        check("the reconciled run is marked and no run stays pending",
              _pending_runs(_git_common_dir(p7) / JOURNAL_DIRNAME) == []
              and any((run / "reconciled.toml").is_file()
                      for run in sorted((_git_common_dir(p7) / JOURNAL_DIRNAME).iterdir())))
        rc, _out, err = _st_run(["--store", "dir:../p7-dest2", "--root", str(p7)])
        check("a fresh run after reconciliation succeeds", rc == EXIT_OK, err.strip()[:300])

        # killed after the product change: the next run completes forward, then refuses
        p8 = _st_build_store(base, "p8")
        check("the product-change-boundary kill lands",
              run_killed(p8, "dir:../p8-dest", "product-written"))
        check("the staged pointer survives the kill", (p8 / _opf_store.POINTER_REL).exists())
        rc, _out, err = _st_run(["--store", "dir:../p8-dest", "--root", str(p8)])
        check("the next run completes the interrupted relocation forward and refuses",
              rc == EXIT_MALFORMED and "completed forward" in err, err.strip()[:300])
        p8_runs = sorted((_git_common_dir(p8) / JOURNAL_DIRNAME).iterdir())
        check("forward completion writes the complete frame",
              len(p8_runs) == 1 and (p8_runs[0] / "complete.toml").is_file()
              and (p8_runs[0] / "recorded.toml").is_file())
        wl8 = (base / "p8-dest" / wl_rel).read_text(encoding="utf-8")
        check("forward completion records the relocation at the destination",
              str(p8) in wl8 and str(base / "p8-dest") in wl8)
        code8, _r8 = _doctor_exit(_opf_store.resolve_store(p8))
        check("forward completion leaves the doctor VALID through the new pointer",
              code8 == EXIT_OK)

        # a source changed after extraction rolls back instead of completing forward
        p9 = _st_build_store(base, "p9")
        check("the second product-change-boundary kill lands",
              run_killed(p9, "dir:../p9-dest", "product-written"))
        # Move the source HEAD without landing the staged removal: unstage it, then commit new work.
        _st_git(p9, "reset", "-q", "HEAD", "--", _opf_store.WORKING_DIRNAME)
        (p9 / "note.txt").write_text("moved\n", encoding="utf-8")
        _st_git(p9, "add", "note.txt")
        _st_git(p9, "commit", "-q", "-m", "moved after extraction")
        rc, _out, err = _st_run(["--store", "dir:../p9-dest", "--root", str(p9)])
        check("a source changed after extraction is rolled back and refused",
              rc == EXIT_MALFORMED and "rolled back" in err, err.strip()[:300])
        check("the rollback restores the pointer and the working tree",
              not (p9 / _opf_store.POINTER_REL).exists()
              and (p9 / _opf_store.WORKING_DIRNAME).is_dir()
              and (p9 / wl_rel).read_bytes()
              == _git(p9, ["cat-file", "blob", "HEAD:{}".format(wl_rel)]))

        # removing the reconcile-first step turns the forward-completion vector red
        p10 = _st_build_store(base, "p10")
        check("the third product-change-boundary kill lands",
              run_killed(p10, "dir:../p10-dest", "product-written"))
        with patch.object(this, "_pending_runs", lambda jdir: []):
            rc_flip, _out, err_flip = _st_run(["--store", "dir:../p10-dest", "--root", str(p10)])
        p10_runs = sorted((_git_common_dir(p10) / JOURNAL_DIRNAME).iterdir())
        check("removing the reconcile-first step turns the interruption vector red",
              rc_flip == EXIT_MALFORMED and "reconciled" not in err_flip
              and len(p10_runs) == 1 and not (p10_runs[0] / "complete.toml").is_file()
              and not (p10_runs[0] / "recorded.toml").is_file(), err_flip.strip()[:200])

        # --- recovery fails closed on a missing or damaged journal (spec 4.2/5.4) ------------------------
        p11 = _st_build_store(base, "p11")
        rc, _out, err = _st_run(["--store", "dir:../p11-dest", "--root", str(p11)])
        check("the p11 relocation succeeds", rc == EXIT_OK, err.strip()[:200])
        _st_git(base / "p11-dest", "add", "--", ".working/toml")
        _st_git(base / "p11-dest", "commit", "-q", "-m", "land the relocation record")
        shutil.rmtree(str(_git_common_dir(p11) / JOURNAL_DIRNAME))
        rc, _out, err = _st_run(["--store", "dir:../p11-dest2", "--root", str(p11)])
        check("a staged product change with no journal refuses fail-closed",
              rc == EXIT_MALFORMED and "missing journal" in err, err.strip()[:200])
        check("the missing-journal refusal writes no second destination",
              not (base / "p11-dest2").exists())
        with patch.object(this, "_require_journal_for_staged_change",
                          lambda product_root, jdir: None):
            rc_flip, _out, _err = _st_run(["--store", "dir:../p11-dest2", "--root", str(p11)])
        check("dropping the missing-journal refusal turns its vector red", rc_flip == EXIT_OK,
              "rc {}".format(rc_flip))

        p14 = _st_build_store(base, "p14")
        p14_journal = _git_common_dir(p14) / JOURNAL_DIRNAME
        jrun = p14_journal / _new_run_id()
        _write_frame(jrun, "intent", {"direction": "out-of-repo"})
        _write_frame(jrun, "verified", {})
        rc, _out, err = _st_run(["--store", "dir:../p14-dest", "--root", str(p14)])
        check("an intent frame that lost its fields fails recovery closed, never a guess",
              rc == EXIT_MALFORMED and "fails closed" in err, err.strip()[:300])
        shutil.rmtree(str(jrun))
        jrun2 = p14_journal / _new_run_id()
        head14 = _st_git(p14, "rev-parse", "HEAD")
        _write_frame(jrun2, "intent",
                     {"direction": "out-of-repo", "store_root": str(p14), "source_head": head14,
                      "remove_working": False, "pointer_pre_hex": "absent",
                      "pointer_post_hex": _pointer_bytes("dir:../p14-dest").hex(),
                      "old_desc": "old", "new_desc": "new"})
        _write_frame(jrun2, "verified", {})
        rc, _out, err = _st_run(["--store", "dir:../p14-dest", "--root", str(p14)])
        check("a verified run without its extracted frame fails recovery closed before any staging",
              rc == EXIT_MALFORMED and "fails closed" in err
              and not (p14 / _opf_store.POINTER_REL).exists(), err.strip()[:300])
        shutil.rmtree(str(jrun2))
        jrun3 = p14_journal / _new_run_id()
        _write_frame(jrun3, "intent", {"direction": "out-of-repo"})
        _write_frame(jrun3, "verified", {})
        flip_red = False
        with patch.object(this, "_intent_field", lambda frame, key, run_dir: frame[key]):
            try:
                rc_flip, _out, _err = _st_run(["--store", "dir:../p14-dest", "--root", str(p14)])
            except Exception:  # noqa: BLE001  the raw access escapes as an uncaught exception
                flip_red = True
        check("raw frame access turns the damaged-journal vector red", flip_red)
        shutil.rmtree(str(p14_journal))

        # --- a global-scope url rewrite under the one shared git environment ----------------------------
        # The refusal is read under the SAME scrubbed environment every transfer git call runs with
        # (_opf_observe._scrubbed_env neutralizes global and system config), so a global rewrite is
        # neither honoured by the transfer nor surfaced by the refusal.
        p15 = _st_build_store(base, "p15")
        redirect_global = base / "redirect-global"
        redirect_global.mkdir()
        gconfig = Path(os.environ["HOME"]) / ".gitconfig"
        gconfig.write_text("[url \"{}\"]\n\tinsteadOf = {}\n".format(redirect_global, base),
                           encoding="utf-8")
        rc, _out, err = _st_run(["--store", "dir:../p15-dest", "--root", str(p15)])
        check("a global-scope rewrite is neither honoured by the transfer nor a refusal",
              rc == EXIT_OK and os.listdir(str(redirect_global)) == []
              and (base / "p15-dest" / _opf_store.WORKING_DIRNAME).is_dir(),
              (err or "rc {}".format(rc)).strip()[:200])
        # an environment that DID honour global configuration surfaces the same rewrite to the
        # refusal first, because the enumeration runs under the transfer's own environment
        _st_git(base / "p15-dest", "add", "--", ".working/toml")
        _st_git(base / "p15-dest", "commit", "-q", "-m", "land the relocation record")
        real_env = _opf_observe._scrubbed_env

        def honouring_env():
            env = real_env()
            env.pop("GIT_CONFIG_GLOBAL", None)
            return env

        with patch.object(_opf_observe, "_scrubbed_env", honouring_env):
            rc_flip, _out, err_flip = _st_run(["--store", "dir:../p15-dest2", "--root", str(p15)])
        check("an environment honouring global config surfaces the rewrite to the refusal, under "
              "the one shared runner", rc_flip == EXIT_MALFORMED
              and "rewrites the destination" in err_flip, err_flip.strip()[:200])
        os.unlink(str(gconfig))
    except Exception as exc:  # noqa: BLE001  a fixture that cannot be built or read is a harness
        # error (cannot evaluate), never a pass
        import traceback
        traceback.print_exc()
        print("opf-migrate self-test: harness error ({!r}); fail-closed".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        shutil.rmtree(str(base), ignore_errors=True)
    if failures:
        print("opf-migrate self-test: FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 1
    print("opf-migrate self-test: PASS ({} checks: grammar, the store run-id vocabulary, admission, "
          "the unregistered providers, the publishable pointer, in-repo to local, local to local, "
          "the review-branch return, destination and lease refusals, the stranded default move, "
          "extraction and verification mutants, the adoption-archive re-verification, the "
          "completion check 5 probe, interruption reconciliation, the fail-closed journal "
          "recovery, and the shared transfer environment)".format(checked[0]))
    return EXIT_OK


def main():
    if sys.argv[1:] in (["--self-test"], ["--selftest"]):
        return self_test()
    print("usage: _opf_migrate.py --self-test (the verb is `opf migrate`)", file=sys.stderr)
    return EXIT_MALFORMED


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This module launches git children
# and reads their exit statuses throughout, and its self-test replays whole stores through them, so
# under an ignored SIGCHLD a failing child would read as a pass. The guard runs as the first
# statement of self_test(), and at the entry line before main(); the entry line comes after the
# --self-test statement that opf.py requires first in the __main__ block. An import of this module
# runs nothing.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_opf_migrate.py") or main())
