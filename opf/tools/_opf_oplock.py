#!/usr/bin/env python3
"""OPF shared operation lock (OPF-D2B PR2 redesign).

SENSITIVE-TIER, concurrency-critical: a lock bug means data corruption or a race. Fail-closed
throughout, stdlib-only, Linux/macOS (POSIX advisory locks).

The lock identity is THREE LEGS, held together or the acquisition fails and unwinds:

  1. A persistent flock ANCHOR at <control-root>/opf-oplock/mutex.lock. The control root is the
     AUTHORITATIVE common git directory (git rev-parse --git-common-dir, asked of git itself with
     an explicit -C binding to the resolved store root over a scrubbed environment with every
     GIT_* variable removed; never inferred from a name or a worktrees-layout heuristic), or the
     store root itself when .git is GENUINELY absent. The .git entry is classified three-way on a
     no-follow basis: genuine absence roots the control tree at the store root; a real directory
     or a regular gitdir-pointer file asks git; ANYTHING else (a symlink, live or dangling, a
     FIFO, an unreadable entry, or a failed or missing git on a git store) refuses, never falls
     back to the repository root. The anchor is created once and NEVER unlinked by release, so
     this module's own paths can never swap the flock inode under a waiting peer.
  2. An exclusive ACTIVE RECORD at <control-root>/opf-oplock/active.toml (O_CREAT|O_EXCL). A
     crash leaves it behind, and a pre-existing record under a free anchor refuses acquisition
     until EXPLICIT recovery (recover=True); staleness is never inferred from pid, host, age, or
     timeout, and a possibly-live holder is never seized.
  3. The spec 5.7 LEASE at <machine-store>/lease.toml (O_CREAT|O_EXCL), mandatory: acquisition
     requires a RESOLVED machine store and every capability carries a lease. A capability without
     a lease cannot exist.

Both control records are emitted through the checked TOML encoder (_opf_emit.emit_checked) and
re-checked for top-key equality against their closed key sets, written with the shared full-write
loop (_journal._write_all), and fsynced (file and parent directory) before the capability
returns. The capability retains the open descriptors, the (st_dev, st_ino) identities, and the
exact payload bytes of everything it created.

Release is identity-bound. It refuses, FIRST, any caller that is not the recorded acquirer (pid
plus the /proc start-time identity _journal._pid_start provides); it re-checks the retained
anchor and control-directory identities; it then runs the reverse-order legs (lease, then active
record) with ERROR COLLECTION, so the active-record leg still runs when the lease leg failed;
each removal is a verified unlink (re-open no-follow, type, link count, size, device and inode
against the retained identity, byte equality against the retained payload, and a final pre-unlink
name-stat) and a mismatch refuses and PRESERVES the file rather than removing it; the anchor is
unlocked LAST, after the legs, and the collected failures aggregate into one raise after the
unlock.

Every control path is reached only through dir_fd opens beneath no-follow walked parents
(_opf_store._open_dir_nofollow); every control open carries O_NOFOLLOW and O_NONBLOCK, so a
symlink at a control name is refused rather than followed and a FIFO cannot block the type check.
Nothing here creates a missing input root (only the single opf-oplock component is ever created,
under an already-open control root), and a link-count check applies to regular files ONLY, never
to a directory.

DISCLOSED RESIDUALS (this guard does not cover): the flock is advisory, binding only cooperating
processes; a same-uid actor who unlinks and recreates the anchor out of band is not prevented,
only bounded by the control-directory ownership and permission checks and surfaced by the
stale-record refusal; the final unlink is by name, so a window remains between the pre-unlink
name-stat and the unlink itself, narrowed by flock exclusivity, never closed; acquirer identity
degrades to bare pid equality where /proc start times are unavailable (non-Linux); a store or
common-dir path with a symlinked ancestor is refused by the no-follow walk rather than served;
and this is the LOCAL in-repository serialization boundary, not a remote-visible lease.

Nested journal/index lock composition is DELIBERATELY OUT OF SCOPE here (deferred to PR3); this
module makes no cross-lock ordering claim.

Run: python3 -I -B opf/tools/_opf_oplock.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (missing containment
primitive or git binary), never a clean skip.
"""
import fcntl
import os
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _containment        # noqa: E402
import _journal            # noqa: E402
import _opf_check          # noqa: E402
import _opf_emit           # noqa: E402
import _opf_init_contract  # noqa: E402
import _opf_store          # noqa: E402

# Fixed control names. The control directory holds exactly the anchor and, while an operation is
# active, the active record; nothing else is created there (the earlier draft's ops/<uuid>/phases
# tree is retired with the nested-lock scope-out).
CONTROL_DIRNAME = "opf-oplock"
ANCHOR_NAME = "mutex.lock"
ACTIVE_NAME = "active.toml"

# Closed top-level key sets for the two control records. The lease shape is single-sourced from
# the store validator (_opf_check.LEASE_TOP_KEYS, spec 5.7); the active record adds the op_id that
# correlates the control-side record to the capability that owns it.
ACTIVE_TOP_KEYS = frozenset(("schema", "op_id", "holder", "operation", "acquired_at"))
_SCHEMA = 1  # == _opf_schema.SUPPORTED_SCHEMA (the lease payload the doctor validates)

# Field bounds: single-sourced from the D2b contract's actor-id bound and control-character class
# (the class already covers C0, DEL, C1, and the BOM).
MAX_FIELD_BYTES = _opf_init_contract.MAX_ACTOR_ID_BYTES
_CONTROL_RE = _opf_init_contract._CONTROL_RE

_GIT_TIMEOUT_SECONDS = 30

# Control opens: no-follow always; O_NONBLOCK always, so a FIFO planted at a control name cannot
# block the open that feeds the type check (c13); O_CLOEXEC so a retained fd never leaks across an
# exec boundary.
_DIR_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class OpLockError(Exception):
    """A fail-closed locking error."""


class OpCapability:
    """The held operation lock: inert data plus the retained descriptors.

    Carries the three legs (the flocked anchor, the active record, the lease), the recorded
    (st_dev, st_ino) identities and exact payload bytes that release verifies against, and the
    acquirer identity (pid plus /proc start time) that release refuses any other caller on. It
    exposes NO further lock acquisition: nested journal/index composition is out of scope here
    (PR3), so this object cannot be used to widen what was acquired.
    """
    __slots__ = ("op_id", "holder", "operation", "store_root", "machine_rel",
                 "_ctl_fd", "_machine_fd", "_anchor_fd", "_active_fd", "_lease_fd",
                 "_anchor_ident", "_ctl_ident", "_machine_ident", "_active_ident", "_lease_ident",
                 "_active_bytes", "_lease_bytes",
                 "_acquirer_pid", "_acquirer_pid_start", "_released")

    def __init__(self, op_id, holder, operation, store_root, machine_rel,
                 ctl_fd, machine_fd, anchor_fd, active_fd, lease_fd,
                 anchor_ident, ctl_ident, machine_ident, active_ident, lease_ident,
                 active_bytes, lease_bytes, acquirer_pid, acquirer_pid_start):
        self.op_id = op_id
        self.holder = holder
        self.operation = operation
        self.store_root = store_root
        self.machine_rel = machine_rel
        self._ctl_fd = ctl_fd
        self._machine_fd = machine_fd
        self._anchor_fd = anchor_fd
        self._active_fd = active_fd
        self._lease_fd = lease_fd
        self._anchor_ident = anchor_ident
        self._ctl_ident = ctl_ident
        self._machine_ident = machine_ident
        self._active_ident = active_ident
        self._lease_ident = lease_ident
        self._active_bytes = active_bytes
        self._lease_bytes = lease_bytes
        self._acquirer_pid = acquirer_pid
        self._acquirer_pid_start = acquirer_pid_start
        self._released = False


# --- small fail-closed primitives ---------------------------------------------------------------


def _utc_now():
    """RFC 3339 UTC, read from the clock at the acquisition event (timestamp-from-clock)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lstat_at(dir_fd, name, label):
    """No-follow stat of `name` under dir_fd: the stat result, or None on GENUINE absence. Any
    other error refuses (an unreadable control name is a failure, never nothing-to-check)."""
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OpLockError("cannot stat {} ({})".format(label, exc))


def _validate_file_fd(fd, label):
    """Type/link-count/ownership/permission validation of an OPEN control file. The link-count
    check (exactly 1) applies here, to a regular FILE, and nowhere else (c4). Returns the stat."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {} ({})".format(label, exc))
    if not stat.S_ISREG(st.st_mode):
        raise OpLockError("{} is not a regular file".format(label))
    if st.st_nlink != 1:
        raise OpLockError("{} link count is {}, expected exactly 1".format(label, st.st_nlink))
    if st.st_uid != os.getuid():
        raise OpLockError("{} is owned by uid {}, not the current uid {}".format(
            label, st.st_uid, os.getuid()))
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OpLockError("{} is group- or other-writable (mode {:o})".format(
            label, stat.S_IMODE(st.st_mode)))
    return st


def _validate_ctl_dir_fd(fd, label):
    """Type/ownership/permission validation of an OPEN control directory. Deliberately NO
    link-count check: a directory's link count grows with its subdirectories, so an nlink ceiling
    on a directory is a false-positive generator, not a guard (c4). Returns the stat."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {} ({})".format(label, exc))
    if not stat.S_ISDIR(st.st_mode):
        raise OpLockError("{} is not a directory".format(label))
    if st.st_uid != os.getuid():
        raise OpLockError("{} is owned by uid {}, not the current uid {}".format(
            label, st.st_uid, os.getuid()))
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OpLockError("{} is group- or other-writable (mode {:o})".format(
            label, stat.S_IMODE(st.st_mode)))
    return st


def _open_dir_at(parent_fd, name, label):
    """Open a directory component beneath an already-trusted dir fd, no-follow."""
    try:
        return os.open(name, _DIR_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise OpLockError("cannot open directory {} no-follow ({})".format(label, exc))


def _validate_field(label, value):
    """A control-record field: a non-empty, strictly-encodable, bounded string with no character
    of the single-sourced control class (C0, DEL, C1, BOM; _opf_init_contract._CONTROL_RE)."""
    if type(value) is not str or not value:
        raise OpLockError("{} must be a non-empty string".format(label))
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise OpLockError("{} is not encodable UTF-8 (lone surrogate)".format(label))
    if len(raw) > MAX_FIELD_BYTES:
        raise OpLockError("{} exceeds {} bytes".format(label, MAX_FIELD_BYTES))
    if _CONTROL_RE.search(value):
        raise OpLockError("{} carries a forbidden control character".format(label))


def _control_payload(document, closed_keys, label):
    """The exact bytes of a control record: emitted through the checked encoder (round-trip
    proven), then independently reparsed and checked for top-key EQUALITY against the closed set,
    so a record with a missing or extra top-level key can never be written."""
    try:
        text = _opf_emit.emit_checked(document)
    except _opf_emit.EmitError as exc:
        raise OpLockError("cannot emit {} ({})".format(label, exc))
    try:
        reparsed = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:  # defensive: emit_checked already reparsed
        raise OpLockError("emitted {} did not reparse ({})".format(label, exc))
    if set(reparsed) != set(closed_keys):
        raise OpLockError("{} top-level keys {} do not equal the closed set {}".format(
            label, sorted(reparsed), sorted(closed_keys)))
    return text.encode("utf-8")


# --- the control root (authoritative common git dir) --------------------------------------------


def _classify_git_entry(store_root_fd, store_root):
    """Three-way classification of <store_root>/.git on a no-follow basis: "absent" for genuine
    absence, "present" for a real directory or a regular gitdir-pointer file (confirmed on the
    OPENED object, not just the name), and a refusal for everything else. Never a fallback."""
    st = _lstat_at(store_root_fd, ".git", ".git at {}".format(store_root))
    if st is None:
        return "absent"
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError(".git at {} is a symlink; refusing (never followed, and never a "
                          "repository-root fallback)".format(store_root))
    if stat.S_ISDIR(st.st_mode):
        fd = _open_dir_at(store_root_fd, ".git", ".git at {}".format(store_root))
        os.close(fd)
        return "present"
    if stat.S_ISREG(st.st_mode):
        try:
            fd = os.open(".git", _FILE_READ_FLAGS, dir_fd=store_root_fd)
        except OSError as exc:
            raise OpLockError("cannot open .git at {} no-follow ({})".format(store_root, exc))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OpLockError(".git at {} changed type under the open; refusing".format(
                    store_root))
        finally:
            os.close(fd)
        return "present"
    raise OpLockError(".git at {} has an unexpected type (mode {:o}); refusing, never a "
                      "repository-root fallback".format(store_root, stat.S_IFMT(st.st_mode)))


def _git_common_dir(store_root):
    """The authoritative common git directory of `store_root`, asked of git itself: rev-parse
    --git-common-dir with an explicit -C binding and a scrubbed environment (every GIT_* variable
    removed, so an ambient GIT_DIR or GIT_COMMON_DIR cannot redirect the answer). Any failure
    (missing git, launch failure, timeout, nonzero exit, undecodable or non-single-line output)
    refuses; the control root is never guessed and never falls back to the repository root."""
    git = shutil.which("git")
    if git is None:
        raise OpLockError("git binary not found on PATH while the store carries a .git entry; "
                          "refusing (the control root is never guessed)")
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    try:
        proc = subprocess.run(
            [git, "--no-replace-objects", "-C", str(store_root), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpLockError("git rev-parse --git-common-dir could not run at {} ({}); "
                          "refusing".format(store_root, exc))
    if proc.returncode != 0:
        raise OpLockError("git rev-parse --git-common-dir failed at {} (exit {}); refusing, "
                          "never a repository-root fallback".format(store_root, proc.returncode))
    try:
        out = proc.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise OpLockError("git rev-parse --git-common-dir output at {} is not decodable; "
                          "refusing".format(store_root))
    lines = out.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise OpLockError("git rev-parse --git-common-dir at {} returned unexpected output; "
                          "refusing".format(store_root))
    common = lines[0].strip()
    if not os.path.isabs(common):
        common = os.path.join(str(store_root), common)
    # Lexical normalization only (abspath, never Path.resolve()): the trust decision belongs to
    # the no-follow walk that opens the result, which refuses any symlinked component.
    return os.path.abspath(common)


def _open_control_dir(control_root_fd, control_root_desc):
    """Open (creating on genuine absence) the opf-oplock control directory beneath the control
    root. A pre-existing symlink or wrong type at the name refuses up front; only the single
    component is ever created (no parents, and never a missing input root)."""
    label = "{}/{}".format(control_root_desc, CONTROL_DIRNAME)
    st = _lstat_at(control_root_fd, CONTROL_DIRNAME, label)
    if st is None:
        try:
            os.mkdir(CONTROL_DIRNAME, 0o755, dir_fd=control_root_fd)
            os.fsync(control_root_fd)
        except FileExistsError:
            pass  # a concurrent creator won the race; classify what is there now
        except OSError as exc:
            raise OpLockError("cannot create control directory {} ({})".format(label, exc))
        st = _lstat_at(control_root_fd, CONTROL_DIRNAME, label)
        if st is None:
            raise OpLockError("control directory {} vanished after creation; refusing".format(
                label))
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError("control directory name {} is a symlink; refusing".format(label))
    if not stat.S_ISDIR(st.st_mode):
        raise OpLockError("control directory name {} is not a directory".format(label))
    fd = _open_dir_at(control_root_fd, CONTROL_DIRNAME, label)
    try:
        _validate_ctl_dir_fd(fd, label)
    except OpLockError:
        os.close(fd)
        raise
    return fd


# --- the anchor (leg 1) ---------------------------------------------------------------------------


def _open_anchor(ctl_fd):
    """Open the persistent mutex anchor, existing-first: a pre-existing regular file is opened
    O_RDWR|O_NOFOLLOW; creation (O_CREAT|O_EXCL) happens only on genuine absence; a symlink or any
    other type at the name refuses. The opened file is validated (regular, link count exactly 1,
    owned by us, not group/other-writable) before it may carry the flock."""
    st = _lstat_at(ctl_fd, ANCHOR_NAME, ANCHOR_NAME)
    if st is None:
        try:
            fd = os.open(ANCHOR_NAME,
                         os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, 0o644, dir_fd=ctl_fd)
        except OSError as exc:
            # EEXIST here means a lost creation race or a dangling symlink at the name: refuse
            # rather than guess which; the next attempt classifies what is there.
            raise OpLockError("cannot create mutex anchor ({})".format(exc))
        try:
            os.fsync(ctl_fd)
        except OSError as exc:
            os.close(fd)
            raise OpLockError("cannot fsync control directory after anchor creation ({})".format(
                exc))
    else:
        if stat.S_ISLNK(st.st_mode):
            raise OpLockError("mutex anchor name is a symlink; refusing")
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("mutex anchor name is not a regular file; refusing")
        try:
            fd = os.open(ANCHOR_NAME,
                         os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=ctl_fd)
        except OSError as exc:
            raise OpLockError("cannot open mutex anchor no-follow ({})".format(exc))
    try:
        _validate_file_fd(fd, "mutex anchor")
    except OpLockError:
        os.close(fd)
        raise
    return fd


def _post_lock_anchor_check(ctl_fd, anchor_fd):
    """After the flock: re-fstat the locked fd (still a regular file, link count still exactly 1)
    and take a FRESH no-follow name-stat, requiring the name to still bind the locked inode. An
    anchor unlinked or swapped between our open and our flock would otherwise leave two holders
    locked on two different inodes. Returns the anchor (st_dev, st_ino) identity."""
    st = _validate_file_fd(anchor_fd, "mutex anchor (post-lock)")
    name_st = _lstat_at(ctl_fd, ANCHOR_NAME, "mutex anchor (post-lock name check)")
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != (st.st_dev, st.st_ino):
        raise OpLockError("mutex anchor was unlinked or replaced between open and lock; refusing "
                          "(the locked inode is no longer the inode the name binds)")
    return (st.st_dev, st.st_ino)


# --- the machine store (leg 3 parent) --------------------------------------------------------------


def _open_machine_dir(store_root_fd, machine_rel, store_root):
    """Walk the RESOLVED machine-store relative path beneath the store root, one no-follow dir_fd
    component at a time. Every failure refuses (no silent skip, and nothing is created: the
    machine store is an input root this module never creates)."""
    if type(machine_rel) is not str or not machine_rel:
        raise OpLockError("machine-store relative path is missing from the resolution")
    parts = machine_rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise OpLockError("machine-store relative path {!r} is not canonical".format(machine_rel))
    fd = None
    try:
        for comp in parts:
            parent = store_root_fd if fd is None else fd
            nfd = _open_dir_at(parent, comp, "{}/{}".format(store_root, comp))
            if fd is not None:
                os.close(fd)
            fd = nfd
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise OpLockError("cannot fstat machine store {}/{} ({})".format(
                store_root, machine_rel, exc))
        # Ownership only: the machine store is adopter content, not this module's control
        # directory, so the group/other-write hardening is not imposed on it; the lease file
        # itself is created 0o644 by us and fully identity- and byte-verified at release.
        if st.st_uid != os.getuid():
            raise OpLockError("machine store {}/{} is owned by uid {}, not the current uid "
                              "{}".format(store_root, machine_rel, st.st_uid, os.getuid()))
        return fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise


# --- stale records and explicit recovery ------------------------------------------------------------


def _classify_stale(dir_fd, name, label):
    """Whether a control record already exists at `name` (a crash artefact under a free anchor).
    True for a plain regular file; False for genuine absence; anything else (a symlink, a FIFO, a
    wrong type) refuses outright, recovery included: manual intervention is required there."""
    st = _lstat_at(dir_fd, name, label)
    if st is None:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError("{} name is a symlink; refusing (manual intervention required)".format(
            label))
    if not stat.S_ISREG(st.st_mode):
        raise OpLockError("{} name is not a regular file; refusing (manual intervention "
                          "required)".format(label))
    return True


def _recover_stale(dir_fd, name, label):
    """Type-verified unlink of an OBSERVED stale control record, under the held anchor flock and
    only on the explicit recover=True path. The opened object (not just the name) must be a plain
    regular file with link count exactly 1, and the name must still bind that same inode at the
    final pre-unlink re-check; anything else refuses and preserves."""
    try:
        fd = os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise OpLockError("stale {} vanished during recovery; refusing (another actor is "
                          "interfering)".format(label))
    except OSError as exc:
        raise OpLockError("cannot open stale {} for recovery ({})".format(label, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OpLockError("stale {} is not a plain singly-linked regular file; refusing "
                              "recovery (manual intervention required)".format(label))
        ident = (st.st_dev, st.st_ino)
    finally:
        os.close(fd)
    name_st = _lstat_at(dir_fd, name, "stale {} (pre-unlink re-check)".format(label))
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != ident:
        raise OpLockError("stale {} changed identity during recovery; refusing".format(label))
    try:
        os.unlink(name, dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError as exc:
        raise OpLockError("cannot unlink stale {} ({})".format(label, exc))


# --- control-record create and verified unlink ------------------------------------------------------


def _create_control_file(dir_fd, name, payload, label):
    """O_CREAT|O_EXCL creation of a control record, the full-byte write loop, and durability
    (fsync of the file AND its parent directory) before returning. Returns the still-open fd and
    the (st_dev, st_ino) identity; the caller retains both."""
    try:
        fd = os.open(name,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, 0o644, dir_fd=dir_fd)
    except FileExistsError:
        raise OpLockError("stale {} already exists; a previous operation did not release cleanly "
                          "(EXPLICIT recovery required: recover=True)".format(label))
    except OSError as exc:
        raise OpLockError("cannot create {} ({})".format(label, exc))
    try:
        _journal._write_all(fd, payload)
        os.fsync(fd)
        os.fsync(dir_fd)
        st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise OpLockError("cannot write {} ({})".format(label, exc))
    return fd, (st.st_dev, st.st_ino)


def _verified_unlink(dir_fd, name, ident, expected_bytes, label):
    """Remove a control record ONLY when it is verifiably the one this capability created: re-open
    no-follow, require a plain regular file with link count exactly 1, the retained (st_dev,
    st_ino) identity, the exact retained size, and byte equality with the retained payload; then a
    final pre-unlink name-stat re-check; then unlink by dir_fd and fsync the parent. ANY mismatch
    refuses and PRESERVES the file (never-seize: a replaced or modified record is somebody's
    evidence, not ours to delete)."""
    try:
        fd = os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise OpLockError("{} is already absent; this capability did not remove it and refuses "
                          "to certify a release leg it did not perform".format(label))
    except OSError as exc:
        raise OpLockError("cannot reopen {} for verified unlink ({})".format(label, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("{} is no longer a regular file; preserved".format(label))
        if st.st_nlink != 1:
            raise OpLockError("{} link count is {}, expected exactly 1; preserved".format(
                label, st.st_nlink))
        if (st.st_dev, st.st_ino) != ident:
            raise OpLockError("{} was replaced (device/inode mismatch); preserved".format(label))
        if st.st_size != len(expected_bytes):
            raise OpLockError("{} size {} does not match the recorded payload ({} bytes); "
                              "preserved".format(label, st.st_size, len(expected_bytes)))
        data = bytearray()
        while len(data) <= len(expected_bytes):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if bytes(data) != expected_bytes:
            raise OpLockError("{} payload does not match the recorded bytes; preserved".format(
                label))
    finally:
        os.close(fd)
    name_st = _lstat_at(dir_fd, name, "{} (pre-unlink re-check)".format(label))
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != ident:
        raise OpLockError("{} identity changed before the unlink; preserved".format(label))
    try:
        os.unlink(name, dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError as exc:
        raise OpLockError("cannot unlink {} ({})".format(label, exc))


# --- acquire / release ------------------------------------------------------------------------------


def acquire_operation(store_root, operation, holder=None, recover=False):
    """Acquire the shared operation lock for the RESOLVED machine store at `store_root`.

    All three legs are taken or the acquisition unwinds: the anchor flock under the authoritative
    control root, the exclusive active record, and the mandatory lease. A pre-existing active
    record or lease under a free anchor is a crash artefact: it refuses acquisition unless
    recover=True explicitly clears it (staleness is never inferred; a held flock is never
    seized). Returns an OpCapability; raises OpLockError fail-closed on everything else.
    """
    if not _containment.probe():
        raise OpLockError("race-free containment primitive absent; fail-closed")
    if type(recover) is not bool:
        raise OpLockError("recover must be a bool (a control parameter is validated, never "
                          "coerced)")
    if holder is None:
        holder = "opf:{}:{}".format(os.uname().nodename, os.getpid())
    _validate_field("holder", holder)
    _validate_field("operation", operation)

    res = _opf_store.resolve_store(store_root)
    if res.status != _opf_store.RESOLVED:
        raise OpLockError("no RESOLVED machine store at {} ({}: {}); the operation lock requires "
                          "a resolved store".format(store_root, res.status, res.detail))
    store_root_abs = str(res.store_root)
    try:
        store_fd = _opf_store._open_dir_nofollow(store_root_abs)
    except OSError as exc:
        raise OpLockError("cannot open store root {} no-follow ({})".format(store_root_abs, exc))

    machine_fd = None
    control_root_fd = None
    ctl_fd = None
    anchor_fd = None
    active_fd = None
    lease_fd = None
    locked = False
    active_created = False
    lease_created = False
    active_ident = lease_ident = None
    active_payload = lease_payload = None
    try:
        machine_fd = _open_machine_dir(store_fd, res.machine_rel, store_root_abs)

        if _classify_git_entry(store_fd, store_root_abs) == "absent":
            control_root_fd = os.dup(store_fd)
            control_root_desc = store_root_abs
        else:
            control_root_desc = _git_common_dir(store_root_abs)
            try:
                control_root_fd = _opf_store._open_dir_nofollow(control_root_desc)
            except OSError as exc:
                raise OpLockError("cannot open common git dir {} no-follow ({})".format(
                    control_root_desc, exc))
        ctl_fd = _open_control_dir(control_root_fd, control_root_desc)
        os.close(control_root_fd)
        control_root_fd = None

        anchor_fd = _open_anchor(ctl_fd)
        try:
            fcntl.flock(anchor_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OpLockError("operation lock is held by another process (contention; a held "
                              "anchor is never seized)")
        except OSError as exc:
            raise OpLockError("cannot flock mutex anchor ({})".format(exc))
        locked = True
        anchor_ident = _post_lock_anchor_check(ctl_fd, anchor_fd)

        # Stale-state classification under the held flock: a record under a FREE anchor is a
        # crash artefact; only explicit recovery clears it.
        stale_active = _classify_stale(ctl_fd, ACTIVE_NAME, "active record")
        stale_lease = _classify_stale(machine_fd, _opf_check.LEASE_NAME, "lease")
        if (stale_active or stale_lease) and not recover:
            stale = ", ".join(n for n, s in ((ACTIVE_NAME, stale_active),
                                             (_opf_check.LEASE_NAME, stale_lease)) if s)
            raise OpLockError("stale operation record(s) under a free anchor ({}); a previous "
                              "holder did not release cleanly; refusing without EXPLICIT "
                              "recovery (recover=True)".format(stale))
        if stale_active:
            _recover_stale(ctl_fd, ACTIVE_NAME, "active record")
        if stale_lease:
            _recover_stale(machine_fd, _opf_check.LEASE_NAME, "lease")

        op_id = str(uuid.uuid4())
        _validate_field("op_id", op_id)
        acquired_at = _utc_now()
        active_payload = _control_payload(
            dict(schema=_SCHEMA, op_id=op_id, holder=holder, operation=operation,
                 acquired_at=acquired_at),
            ACTIVE_TOP_KEYS, "active record")
        lease_payload = _control_payload(
            dict(schema=_SCHEMA, holder=holder, operation=operation, acquired_at=acquired_at),
            _opf_check.LEASE_TOP_KEYS, "lease")

        active_fd, active_ident = _create_control_file(ctl_fd, ACTIVE_NAME, active_payload,
                                                       "active record")
        active_created = True
        lease_fd, lease_ident = _create_control_file(machine_fd, _opf_check.LEASE_NAME,
                                                     lease_payload, "lease")
        lease_created = True

        ctl_st = os.fstat(ctl_fd)
        machine_st = os.fstat(machine_fd)
        return OpCapability(
            op_id=op_id, holder=holder, operation=operation,
            store_root=store_root_abs, machine_rel=res.machine_rel,
            ctl_fd=ctl_fd, machine_fd=machine_fd, anchor_fd=anchor_fd,
            active_fd=active_fd, lease_fd=lease_fd,
            anchor_ident=anchor_ident,
            ctl_ident=(ctl_st.st_dev, ctl_st.st_ino),
            machine_ident=(machine_st.st_dev, machine_st.st_ino),
            active_ident=active_ident, lease_ident=lease_ident,
            active_bytes=active_payload, lease_bytes=lease_payload,
            acquirer_pid=os.getpid(),
            acquirer_pid_start=_journal._pid_start(os.getpid()))
    except BaseException as exc:
        # Unwind in reverse leg order; every unwind failure is collected, never swallowed.
        unwind = []
        if lease_created:
            try:
                _verified_unlink(machine_fd, _opf_check.LEASE_NAME, lease_ident, lease_payload,
                                 "lease (unwind)")
            except OpLockError as uexc:
                unwind.append(str(uexc))
        if active_created:
            try:
                _verified_unlink(ctl_fd, ACTIVE_NAME, active_ident, active_payload,
                                 "active record (unwind)")
            except OpLockError as uexc:
                unwind.append(str(uexc))
        if locked:
            try:
                fcntl.flock(anchor_fd, fcntl.LOCK_UN)
            except OSError as uexc:
                unwind.append("cannot unlock mutex anchor ({})".format(uexc))
        for open_fd in (lease_fd, active_fd, anchor_fd, ctl_fd, control_root_fd, machine_fd):
            if open_fd is not None:
                try:
                    os.close(open_fd)
                except OSError as uexc:
                    unwind.append("cannot close a control descriptor ({})".format(uexc))
        if unwind and isinstance(exc, Exception):
            raise OpLockError("{}; additionally the unwind failed: {}".format(
                exc, "; ".join(unwind))) from exc
        raise
    finally:
        os.close(store_fd)


def release_operation(cap):
    """Identity-bound, verified release of an OpCapability.

    Refuses FIRST any caller that is not the recorded acquirer (pid plus /proc start time). Then
    re-checks the retained anchor and control-directory identities, runs the reverse-order legs
    (lease, then active record) with ERROR COLLECTION so the active-record leg runs even when the
    lease leg failed, verifies each unlink byte-for-byte (a mismatch refuses and PRESERVES the
    file), unlocks the anchor LAST, closes every retained descriptor, and only then raises the
    collected failures as one OpLockError. The anchor itself is NEVER unlinked.
    """
    if not isinstance(cap, OpCapability):
        raise OpLockError("release requires an OpCapability")
    if cap._released:
        raise OpLockError("capability already released")
    pid = os.getpid()
    start = _journal._pid_start(pid)
    recorded = cap._acquirer_pid_start
    if recorded != "" and not _journal._is_canonical_pid_start(recorded):
        raise OpLockError("recorded acquirer start time is malformed; refusing release "
                          "(a malformed control input is a failure, never trusted)")
    if pid != cap._acquirer_pid or start != recorded:
        raise OpLockError("release refused: caller (pid {}) is not the recorded acquirer "
                          "(pid {})".format(pid, cap._acquirer_pid))

    errors = []
    # Retained-identity re-checks. Collected rather than early-raised: the legs below still run,
    # so a genuine release cleans what it verifiably owns and the anomaly is surfaced with it.
    try:
        st = os.fstat(cap._anchor_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 \
                or (st.st_dev, st.st_ino) != cap._anchor_ident:
            raise OpLockError("mutex anchor identity or link count changed while held "
                              "(nlink {}); the flock no longer excludes anyone".format(
                                  st.st_nlink))
    except (OSError, OpLockError) as exc:
        errors.append("anchor: {}".format(exc))
    try:
        st = os.fstat(cap._ctl_fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_nlink == 0 \
                or (st.st_dev, st.st_ino) != cap._ctl_ident:
            raise OpLockError("control directory identity changed while held")
    except (OSError, OpLockError) as exc:
        errors.append("control dir: {}".format(exc))

    # Reverse-order legs with error collection: the active-record leg runs even when the lease
    # leg failed, so one preserved mismatch cannot strand the other record.
    try:
        _verified_unlink(cap._machine_fd, _opf_check.LEASE_NAME, cap._lease_ident,
                         cap._lease_bytes, "lease")
    except OpLockError as exc:
        errors.append("lease leg: {}".format(exc))
    try:
        _verified_unlink(cap._ctl_fd, ACTIVE_NAME, cap._active_ident, cap._active_bytes,
                         "active record")
    except OpLockError as exc:
        errors.append("active leg: {}".format(exc))

    # The anchor is unlocked LAST and NEVER unlinked; then every retained descriptor closes.
    try:
        fcntl.flock(cap._anchor_fd, fcntl.LOCK_UN)
    except OSError as exc:
        errors.append("unlock: {}".format(exc))
    for open_fd in (cap._lease_fd, cap._active_fd, cap._anchor_fd, cap._ctl_fd, cap._machine_fd):
        try:
            os.close(open_fd)
        except OSError as exc:
            errors.append("close: {}".format(exc))
    cap._released = True
    if errors:
        raise OpLockError("release completed with failures (mismatched files preserved): "
                          + "; ".join(errors))


# --- self-test --------------------------------------------------------------------------------------


def _st_git_env(home):
    """A pinned, hermetic environment for FIXTURE git commands: ambient GIT_* dropped, HOME bound
    to the fixture (no user config), system config disabled."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _st_git(args, cwd, env):
    proc = subprocess.run(["git"] + args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("fixture git {} failed: {}".format(
            args, proc.stdout.decode("utf-8", "replace")))


def _st_store_tree(root):
    """A minimal adopted-store tree that resolve_store resolves (RESOLVED at the default
    location)."""
    md = os.path.join(root, ".working", "toml")
    os.makedirs(md)
    manifest = ('[opf]\nstandard = "opf"\nspec_version = "1.1.0"\nlayout = "inline"\n'
                'posture = "required"\nimport_status = "none"\n\n'
                '[modules]\nconcurrent_operation = true\n')
    with open(os.path.join(md, "manifest.toml"), "w", encoding="utf-8") as fh:
        fh.write(manifest)


def _st_git_store(parent, name, env):
    """A REAL git repository carrying a committed adopted-store tree."""
    root = os.path.join(parent, name)
    os.mkdir(root)
    _st_git(["init", "-q"], root, env)
    _st_store_tree(root)
    _st_git(["add", "-A"], root, env)
    _st_git(["-c", "user.name=opf-selftest", "-c", "user.email=selftest@example.invalid",
             "commit", "-q", "-m", "fixture"], root, env)
    return root


def _st_expect_refusal(fn, *args, needle=None, **kwargs):
    """Run fn expecting an OpLockError; assert the message carries `needle` when given."""
    try:
        fn(*args, **kwargs)
    except OpLockError as exc:
        if needle is not None and needle not in str(exc):
            raise AssertionError("refusal message {!r} lacks {!r}".format(str(exc), needle))
        return str(exc)
    raise AssertionError("{} did not refuse".format(getattr(fn, "__name__", fn)))


def _st_ctl_dir(root):
    return os.path.join(root, ".git", CONTROL_DIRNAME)


def _st_lease_path(root):
    return os.path.join(root, ".working", "toml", _opf_check.LEASE_NAME)


def _t_c1_common_dir_authority(d, env):
    """T-c1: the control root is the AUTHORITATIVE common git dir (git itself, scrubbed env),
    exercised over a real linked worktree; a bogus gitdir pointer refuses (the retired
    name-heuristic would have trusted it); a poisoned ambient GIT_DIR is inert."""
    main = _st_git_store(d, "main", env)
    wt = os.path.join(d, "wt")
    _st_git(["worktree", "add", "--detach", "-q", wt], main, env)
    os.environ["GIT_DIR"] = os.path.join(d, "decoy-git-dir")  # must be scrubbed, so inert
    try:
        cap = acquire_operation(wt, "op-worktree")
    finally:
        del os.environ["GIT_DIR"]
    anchor = os.path.join(_st_ctl_dir(main), ANCHOR_NAME)
    active = os.path.join(_st_ctl_dir(main), ACTIVE_NAME)
    assert os.path.isfile(anchor), "anchor not under the MAIN common git dir"
    assert os.path.isfile(active), "active record not under the MAIN common git dir"
    # Cross-checkout serialization through the one common anchor:
    _st_expect_refusal(acquire_operation, main, "op-main", needle="held")
    release_operation(cap)
    assert os.path.isfile(anchor), "release must NEVER unlink the anchor"
    assert not os.path.exists(active)
    cap2 = acquire_operation(main, "op-main")
    release_operation(cap2)
    # A bogus gitdir pointer file refuses; nothing is created and nothing falls back.
    bogus = os.path.join(d, "bogus")
    os.mkdir(bogus)
    _st_store_tree(bogus)
    with open(os.path.join(bogus, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: /nonexistent-opf-oplock-decoy\n")
    _st_expect_refusal(acquire_operation, bogus, "op-bogus", needle="rev-parse")
    assert not os.path.exists(os.path.join(bogus, CONTROL_DIRNAME)), \
        "a failed git answer must never fall back to a repo-root control tree"


def _t_c2_anchor_validation(d, env):
    """T-c2: anchor persistence and the pre-lock validation roster (symlink, hard link,
    group/other write bits)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    assert os.path.isfile(anchor), "anchor must persist across release"
    victim = os.path.join(d, "victim")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("victim\n")
    os.unlink(anchor)
    os.symlink(victim, anchor)
    _st_expect_refusal(acquire_operation, root, "op", needle="symlink")
    os.unlink(anchor)
    cap = acquire_operation(root, "op")   # clean recreation on genuine absence
    release_operation(cap)
    os.link(anchor, os.path.join(ctl, "hardlink-victim"))
    _st_expect_refusal(acquire_operation, root, "op", needle="link count")
    os.unlink(os.path.join(ctl, "hardlink-victim"))
    os.chmod(anchor, 0o666)
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(anchor, 0o644)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_crit1_anchor_swap(d, env):
    """T-crit1: an out-of-band unlink-and-recreate of the anchor while held cannot yield a second
    holder (the active record refuses it), and the anomaly is surfaced at release while both
    records are still cleaned (identity checks are collected, not legs-blocking)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    os.unlink(anchor)                      # out-of-model interference
    with open(anchor, "wb"):
        pass
    os.chmod(anchor, 0o644)
    # The new inode's flock is free, but the active record under the free anchor refuses:
    _st_expect_refusal(acquire_operation, root, "op2", needle="stale")
    msg = _st_expect_refusal(release_operation, cap, needle="anchor")
    assert "lease leg" not in msg and "active leg" not in msg, msg
    assert not os.path.exists(os.path.join(ctl, ACTIVE_NAME)), "active leg must still run"
    assert not os.path.exists(_st_lease_path(root)), "lease leg must still run"


def _t_c3_c11_mandatory_lease_and_bytes(d, env):
    """T-c3 plus T-c11: acquisition requires a RESOLVED store; the lease and active record exist
    while held with EXACTLY the recorded bytes (checked-encoder TOML, closed key sets); the lease
    is gone after release."""
    plain = os.path.join(d, "plain")
    os.mkdir(plain)
    _st_git(["init", "-q"], plain, env)
    _st_expect_refusal(acquire_operation, plain, "op", needle="RESOLVED")
    assert not os.path.exists(os.path.join(plain, ".git", CONTROL_DIRNAME))
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op-bytes")
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    with open(lease, "rb") as fh:
        on_disk = fh.read()
    assert on_disk == cap._lease_bytes, "lease bytes must equal the recorded payload"
    with open(active, "rb") as fh:
        assert fh.read() == cap._active_bytes, "active bytes must equal the recorded payload"
    lease_doc = tomllib.loads(on_disk.decode("utf-8"))
    assert set(lease_doc) == set(_opf_check.LEASE_TOP_KEYS), lease_doc
    assert lease_doc["schema"] == _SCHEMA and type(lease_doc["schema"]) is int
    assert lease_doc["operation"] == "op-bytes"
    assert lease_doc["acquired_at"].endswith("Z") and "T" in lease_doc["acquired_at"]
    active_doc = tomllib.loads(cap._active_bytes.decode("utf-8"))
    assert set(active_doc) == set(ACTIVE_TOP_KEYS), active_doc
    assert active_doc["op_id"] == cap.op_id
    release_operation(cap)
    assert not os.path.exists(lease) and not os.path.exists(active)


def _t_c4_dir_nlink_free(d, env):
    """T-c4: the link-count check is FILE-only; a control directory that gained a subdirectory
    (nlink above 2) still acquires."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    os.mkdir(os.path.join(_st_ctl_dir(root), "unrelated-subdir"))
    cap = acquire_operation(root, "op")   # the retired draft refused here (dir nlink ceiling)
    release_operation(cap)


def _t_c5_stale_and_recovery(d, env):
    """T-c5: a stale lease or active record under a free anchor refuses without recover=True and
    is cleared by explicit recovery; a simulated crash (descriptors dropped, no release) leaves
    both records and the same discipline applies."""
    root = _st_git_store(d, "repo", env)
    with open(_st_lease_path(root), "wb") as fh:
        fh.write(b'schema = 1\nholder = "crashed"\noperation = "old"\n'
                 b'acquired_at = "2026-01-01T00:00:00Z"\n')
    _st_expect_refusal(acquire_operation, root, "op", needle="stale")
    cap = acquire_operation(root, "op", recover=True)
    release_operation(cap)
    # Simulated kill -9: the holder vanishes without releasing; only the on-disk records remain.
    cap = acquire_operation(root, "op")
    fcntl.flock(cap._anchor_fd, fcntl.LOCK_UN)
    for fd in (cap._lease_fd, cap._active_fd, cap._anchor_fd, cap._ctl_fd, cap._machine_fd):
        os.close(fd)
    cap._released = True                  # the object is dead; the crash artefacts are on disk
    _st_expect_refusal(acquire_operation, root, "op", needle="recover=True")
    cap = acquire_operation(root, "op", recover=True)
    release_operation(cap)


def _t_c6_c7_med6_verified_release(d, env):
    """T-c6/T-c7 plus T-med6: a tampered record is refused and PRESERVED at release, and the
    OTHER leg still runs (error collection, not an early raise)."""
    root = _st_git_store(d, "repo", env)
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    cap = acquire_operation(root, "op")
    with open(lease, "ab") as fh:
        fh.write(b"tampered = true\n")
    _st_expect_refusal(release_operation, cap, needle="lease leg")
    assert os.path.exists(lease), "a mismatched lease must be PRESERVED"
    assert not os.path.exists(active), "the active leg must still run (T-med6)"
    cap = acquire_operation(root, "op", recover=True)
    with open(active, "wb") as fh:                    # replaced content, same inode
        fh.write(b"forged = true\n")
    _st_expect_refusal(release_operation, cap, needle="active leg")
    assert os.path.exists(active), "a mismatched active record must be PRESERVED"
    assert not os.path.exists(lease), "the lease leg must still have run"
    cap = acquire_operation(root, "op", recover=True)
    release_operation(cap)


def _t_c9_acquirer_identity(d, env):
    """T-c9: release refuses any caller that is not the recorded acquirer, BEFORE any leg runs
    (witnessed from a forked child sharing the descriptors)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                          # the child: a different pid, same inherited fds
        try:
            try:
                release_operation(cap)
            except OpLockError:
                os._exit(0)
            os._exit(1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "child release must refuse with OpLockError (status {})".format(status)
    assert os.path.exists(_st_lease_path(root)), "the refused child must not have run any leg"
    assert os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME))
    release_operation(cap)                # the true acquirer still releases cleanly


def _t_c10_git_classification(d, env):
    """T-c10: the three-way .git classification: symlink and FIFO refuse; genuine absence roots
    the control tree at the store root; a symlink at the control name refuses; a missing git
    binary on a git store refuses with NO repo-root fallback."""
    real = _st_git_store(d, "real", env)
    sym = os.path.join(d, "symgit")
    os.mkdir(sym)
    _st_store_tree(sym)
    os.symlink(os.path.join(real, ".git"), os.path.join(sym, ".git"))
    _st_expect_refusal(acquire_operation, sym, "op", needle="symlink")
    fifo_root = os.path.join(d, "fifogit")
    os.mkdir(fifo_root)
    _st_store_tree(fifo_root)
    os.mkfifo(os.path.join(fifo_root, ".git"))
    _st_expect_refusal(acquire_operation, fifo_root, "op", needle="unexpected type")
    nogit = os.path.join(d, "nogit")
    os.mkdir(nogit)
    _st_store_tree(nogit)
    cap = acquire_operation(nogit, "op")  # genuine absence: the store root is the control root
    assert os.path.isfile(os.path.join(nogit, CONTROL_DIRNAME, ANCHOR_NAME))
    release_operation(cap)
    r2 = _st_git_store(d, "r2", env)
    elsewhere = os.path.join(d, "elsewhere")
    os.mkdir(elsewhere)
    os.symlink(elsewhere, os.path.join(r2, ".git", CONTROL_DIRNAME))
    _st_expect_refusal(acquire_operation, r2, "op", needle="symlink")
    r3 = _st_git_store(d, "r3", env)
    emptybin = os.path.join(d, "emptybin")
    os.mkdir(emptybin)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = emptybin
    try:
        _st_expect_refusal(acquire_operation, r3, "op", needle="git binary")
    finally:
        os.environ["PATH"] = old_path
    assert not os.path.exists(os.path.join(r3, CONTROL_DIRNAME)), \
        "missing git must never fall back to a repo-root control tree"


def _t_c12_contention_and_double_release(d, env):
    """T-c12: in-process contention on a second open file description refuses without blocking,
    and a second release of the same capability refuses."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op2", needle="held")
    assert time.monotonic() - t0 < 10, "contention must refuse promptly, never block"
    release_operation(cap)
    _st_expect_refusal(release_operation, cap, needle="already released")


def _t_c13_fifo_control_names(d, env):
    """T-c13: a FIFO planted at a control name is refused promptly (no-follow, O_NONBLOCK, type
    check), never blocked on and never consumed as a record."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    fifo_active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    os.mkfifo(fifo_active)
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op", needle="not a regular file")
    assert time.monotonic() - t0 < 10, "a FIFO must not block the type check"
    os.unlink(fifo_active)
    os.mkfifo(_st_lease_path(root))
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op", needle="not a regular file")
    assert time.monotonic() - t0 < 10, "a FIFO must not block the type check"
    os.unlink(_st_lease_path(root))
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_c8_c14_scope_out(d, env):
    """T-c8/T-c14: the nested-lock surface and the phases subtree are REMOVED (PR3 scope-out),
    and no cross-lock ordering claim survives in the module's public docstrings."""
    assert not hasattr(OpCapability, "acquire_journal_lock"), "nested journal lock is PR3"
    assert not hasattr(OpCapability, "acquire_index_lock"), "nested index lock is PR3"
    for doc in (__doc__, OpCapability.__doc__, acquire_operation.__doc__,
                release_operation.__doc__):
        text = doc or ""
        assert "canonical lock order" not in text and "canonical order" not in text \
            and "repository -> journal -> index" not in text, "retired ordering claim survives"
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    ctl = _st_ctl_dir(root)
    assert not os.path.exists(os.path.join(ctl, "ops"))
    assert not os.path.exists(os.path.join(ctl, "phases"))
    assert sorted(os.listdir(ctl)) == sorted([ANCHOR_NAME, ACTIVE_NAME]), os.listdir(ctl)
    release_operation(cap)
    assert os.listdir(ctl) == [ANCHOR_NAME], os.listdir(ctl)


def _t_low1_field_validation(d, env):
    """T-low1: holder and operation are validated against the single-sourced control class (C0,
    DEL, C1, BOM), non-emptiness, exact type, and the byte bound, BEFORE any filesystem action.
    The C1 and BOM probes are built with chr() so the test source itself stays ASCII-clean."""
    root = _st_git_store(d, "repo", env)
    for bad_holder in ("bad\x00nul", "bad\x1fctl", "bad" + chr(0x85) + "c1", "bad\x7fdel",
                       "bad" + chr(0xFEFF) + "bom", "", 123, "x" * (MAX_FIELD_BYTES + 1)):
        _st_expect_refusal(acquire_operation, root, "op", holder=bad_holder)
    for bad_op in ("", "op\nnewline", "op" + chr(0x9C) + "c1", None):
        _st_expect_refusal(acquire_operation, root, bad_op)
    assert not os.path.exists(_st_ctl_dir(root)), "validation must precede any filesystem action"
    assert not os.path.exists(_st_lease_path(root))


def self_test():
    """Regression roster (plan section (e)): T-c1..T-c14, T-crit1, T-med6, T-low1, each a witness
    against a named defect of the retired draft. A missing containment primitive or git binary is
    a REFUSAL (non-zero), never a clean skip."""
    import tempfile
    import traceback

    if not _containment.probe():
        print("REFUSED: race-free containment primitive absent; the operation lock cannot be "
              "exercised safely (fail-closed, non-zero)")
        return 2
    if shutil.which("git") is None:
        print("REFUSED: git binary not found; the self-test requires real git stores "
              "(fail-closed, non-zero)")
        return 2

    tests = (
        ("T-c1 authoritative common git dir (worktree, scrubbed env, bogus gitdir)",
         _t_c1_common_dir_authority),
        ("T-c2 anchor validation and persistence", _t_c2_anchor_validation),
        ("T-crit1 anchor unlink/recreate is caught, never a second holder",
         _t_crit1_anchor_swap),
        ("T-c3/T-c11 mandatory lease, exact checked-encoder bytes",
         _t_c3_c11_mandatory_lease_and_bytes),
        ("T-c4 link-count check is file-only", _t_c4_dir_nlink_free),
        ("T-c5 stale records refuse; explicit recovery clears", _t_c5_stale_and_recovery),
        ("T-c6/T-c7/T-med6 verified release preserves mismatches, legs collected",
         _t_c6_c7_med6_verified_release),
        ("T-c9 release is bound to the acquirer identity", _t_c9_acquirer_identity),
        ("T-c10 three-way .git classification, no fallback", _t_c10_git_classification),
        ("T-c12 contention and double release refuse", _t_c12_contention_and_double_release),
        ("T-c13 FIFO control names cannot block or pass", _t_c13_fifo_control_names),
        ("T-c8/T-c14 nested-lock scope-out (PR3)", _t_c8_c14_scope_out),
        ("T-low1 single-sourced field validation", _t_low1_field_validation),
    )

    base = os.path.realpath(tempfile.mkdtemp(prefix="opf-oplock-selftest-"))
    failures = []
    try:
        env = _st_git_env(base)
        for index, (name, fn) in enumerate(tests):
            d = os.path.join(base, "t{:02d}".format(index))
            os.mkdir(d)
            try:
                fn(d, env)
                print("PASS  {}".format(name))
            except BaseException:
                failures.append(name)
                print("FAIL  {}".format(name))
                traceback.print_exc()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL: {} of {} tests failed: {}".format(
            len(failures), len(tests), "; ".join(failures)))
        return 1
    print("SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    sys.exit("usage: _opf_oplock.py --self-test")
