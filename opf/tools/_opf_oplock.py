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
     until EXPLICIT recovery (recover=True). The active record persists the FULL owner identity
     (pid, uid, nodename, canonical /proc start time, session, and acquisition stamp) so a later
     recovery can DECIDE the holder's liveness rather than infer staleness from age or timeout.
     Recovery is never granted by age or timeout, and even under recover=True a record is cleared
     ONLY when the recorded holder is CONFIRMED DEAD on this host by the journal's
     possibly-live-never-seized model (_journal.owner_confirmed_dead over the persisted owner
     identity); a possibly-live, cross-host, or malformed holder, or a lone lease with no paired
     active record, is REFUSED rather than seized. Both stale records are first validated against
     their COMPLETE closed schemas (top-key equality, supported schema, required non-empty fields),
     and a paired lease must name the same holder, operation, and acquisition stamp as the active
     record, so a missing field never compares equal to a missing field. The recovery delete is
     bound to the (st_dev, st_ino) identity and exact bytes the liveness gate read: a record
     replaced after that read is refused and PRESERVED, never deleted. So a live holder whose
     records are exposed under a split anchor can never be recovered into a two-holder state.
  3. The spec 5.7 LEASE at <machine-store>/lease.toml (O_CREAT|O_EXCL), mandatory: acquisition
     requires a RESOLVED machine store and every capability carries a lease. A capability without
     a lease cannot exist. The lease schema is single-sourced from the store validator and is not
     extended here; lease-holder liveness is derived from the paired active record's owner (same
     holder).

Both control records are emitted through the checked TOML encoder (_opf_emit.emit_checked) and
re-checked for top-key equality against their closed key sets, written with the shared full-write
loop (_journal._write_all), and fsynced (file and parent directory) before the capability
returns. The capability retains the open descriptors, the (st_dev, st_ino) identities, and the
exact payload bytes of everything it created. A control-record write that FAILS mid-way unlinks
the just-created (O_EXCL, singly-linked) file by verified identity before raising, so a torn
record is not stranded for a later acquisition to meet; where that cleanup itself fails, the raise
carries both the write failure and a reason naming the leftover it could not remove, never a
message that implies a clean unwind.

Release is identity-bound. It refuses, FIRST, any caller that is not the recorded acquirer (pid
plus the /proc start-time identity _journal._pid_start provides); it re-checks the retained
anchor and control-directory identities; it then runs the reverse-order legs (lease, then active
record) with ERROR COLLECTION, so the active-record leg still runs when the lease leg failed;
each removal is a verified unlink (re-open no-follow, type, link count, size, device and inode
against the retained identity, byte equality against the retained payload, and a final pre-unlink
name-stat) and a mismatch refuses and PRESERVES the file rather than removing it; an OS error
inside a leg (an EIO read, a failed fstat) is normalized to OpLockError and collected like any
other leg failure; the anchor is unlocked LAST, after the legs, in a finally that also closes every
retained descriptor, so no leg failure can skip the unlock; and the collected failures aggregate
into one raise after the unlock. The acquisition unwind follows the same shape.

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
degrades to bare pid equality where /proc start times are unavailable (non-Linux), where a
confirmed-dead recovery still rests on os.kill returning ProcessLookupError rather than on a start
time; the machine-store directory is validated for OWNERSHIP ONLY (not group- or other-writability),
so a group-writable machine store can weaken the lease leg where the control root and the machine
store diverge in trust: this is ownership-only BY DESIGN, to avoid over-firing on the shared-group
adopter stores the machine store legitimately lives in, and is disclosed here rather than
prevented; a store or common-dir path with a symlinked ancestor is refused by the no-follow walk
rather than served; and this is the LOCAL in-repository serialization boundary, not a
remote-visible lease.

Nested journal/index lock composition is DELIBERATELY OUT OF SCOPE here (deferred to PR3); this
module makes no cross-lock ordering claim.

Run: python3 -I -B opf/tools/_opf_oplock.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (missing containment
primitive or git binary), never a clean skip.
"""
import errno
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
# correlates the control-side record to the capability that owns it, and (PR2 round 3) the [owner]
# sub-table that persists the acquirer's full identity so recovery can decide liveness without
# inferring staleness. PR2 OWNS the active-record schema (PR1 froze the coupled-init contracts,
# NOT this record), so extending it here is in-scope; the lease schema is NOT ours to change.
ACTIVE_TOP_KEYS = frozenset(("schema", "op_id", "holder", "operation", "acquired_at", "owner"))
_SCHEMA = 1  # == _opf_schema.SUPPORTED_SCHEMA (the lease payload the doctor validates)

# Field bounds: single-sourced from the D2b contract's actor-id bound and control-character class
# (the class already covers C0, DEL, C1, and the BOM).
MAX_FIELD_BYTES = _opf_init_contract.MAX_ACTOR_ID_BYTES
_CONTROL_RE = _opf_init_contract._CONTROL_RE

_GIT_TIMEOUT_SECONDS = 30

# A small control record (active.toml or lease.toml) is tiny; a read for the recovery liveness gate
# is bounded so an oversize plant is a fail-closed refusal, never slurped (SECA resource-bounds).
_MAX_RECORD_BYTES = 65536

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


def _read_control_record(dir_fd, name, label):
    """Read and TOML-parse an on-disk control record beneath dir_fd, no-follow and fail-closed. The
    opened object (not just the name) must be a plain singly-linked regular file, bounded by
    _MAX_RECORD_BYTES, decodable UTF-8, and a TOML table. ANY failure (absence, wrong type,
    oversize, unparseable, or a non-table document) raises OpLockError, so a record the recovery
    gate cannot read is a refusal, never a silent clean pass (check-fails-closed-on-unreadable).
    Returns (the parsed dict, its (st_dev, st_ino) identity, its exact raw bytes) so the recovery
    gate can bind its later delete to the very object and bytes it verified (DEF-1)."""
    try:
        fd = os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise OpLockError("cannot read {} for the recovery liveness gate ({})".format(label, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("{} is not a regular file; refusing recovery".format(label))
        if st.st_nlink != 1:
            raise OpLockError("{} link count is {}, expected exactly 1; refusing recovery".format(
                label, st.st_nlink))
        if st.st_size > _MAX_RECORD_BYTES:
            raise OpLockError("{} is {} bytes, over the {}-byte record cap; refusing recovery".format(
                label, st.st_size, _MAX_RECORD_BYTES))
        ident = (st.st_dev, st.st_ino)
        data = bytearray()
        while len(data) <= _MAX_RECORD_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if len(data) > _MAX_RECORD_BYTES:
            raise OpLockError("{} exceeds the {}-byte record cap; refusing recovery".format(
                label, _MAX_RECORD_BYTES))
    finally:
        os.close(fd)
    try:
        doc = tomllib.loads(bytes(data).decode("utf-8", errors="strict"))
    except (tomllib.TOMLDecodeError, ValueError, UnicodeDecodeError) as exc:
        raise OpLockError("{} is not decodable UTF-8 TOML; refusing recovery ({})".format(label, exc))
    if not isinstance(doc, dict):
        raise OpLockError("{} is not a TOML table; refusing recovery".format(label))
    return doc, ident, bytes(data)


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
    (missing git, launch failure, timeout, nonzero exit, undecodable, unterminated, or
    non-single-line output) refuses; only the one record terminator is stripped, never a trailing
    space that is part of the path; the control root is never guessed and never falls back to the
    repository root."""
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
    # git prints the common-dir path followed by EXACTLY one newline record terminator. Strip ONLY
    # that one terminator, never arbitrary trailing whitespace: a directory path may legitimately end
    # in a space or a tab, and a blanket .strip() would silently redirect the control root to a
    # different (stripped) path (DEF-4).
    if not out.endswith("\n"):
        raise OpLockError("git rev-parse --git-common-dir at {} returned unterminated output; "
                          "refusing".format(store_root))
    common = out[:-1]
    if not common or "\n" in common:
        raise OpLockError("git rev-parse --git-common-dir at {} returned unexpected output; "
                          "refusing".format(store_root))
    if not os.path.isabs(common):
        common = os.path.join(str(store_root), common)
    # Lexical normalization only (abspath, never Path.resolve()): the trust decision belongs to
    # the no-follow walk that opens the result, which refuses any symlinked component.
    return os.path.abspath(common)


def _open_control_dir(control_root_fd, control_root_desc, dirname=CONTROL_DIRNAME):
    """Open (creating on genuine absence) a single-component control home beneath an already-open
    parent: by default this module's own opf-oplock control directory, and by explicit `dirname` a
    SIBLING control home (the OPF-D2B resume substrate's opf-init tree composes this rather than
    reimplementing it). A pre-existing symlink or wrong type at the name refuses up front; only
    the single component is ever created (no parents, and never a missing input root)."""
    label = "{}/{}".format(control_root_desc, dirname)
    st = _lstat_at(control_root_fd, dirname, label)
    if st is None:
        try:
            os.mkdir(dirname, 0o755, dir_fd=control_root_fd)
            os.fsync(control_root_fd)
        except FileExistsError:
            pass  # a concurrent creator won the race; classify what is there now
        except OSError as exc:
            raise OpLockError("cannot create control directory {} ({})".format(label, exc))
        st = _lstat_at(control_root_fd, dirname, label)
        if st is None:
            raise OpLockError("control directory {} vanished after creation; refusing".format(
                label))
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError("control directory name {} is a symlink; refusing".format(label))
    if not stat.S_ISDIR(st.st_mode):
        raise OpLockError("control directory name {} is not a directory".format(label))
    fd = _open_dir_at(control_root_fd, dirname, label)
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
        # directory, so the group/other-write hardening is not imposed on it (a DISCLOSED
        # residual, above); the lease file itself is created 0o644 by us and fully identity- and
        # byte-verified at release.
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


def _validate_recovery_active(doc):
    """Validate a stale ACTIVE record against its COMPLETE schema before its holder or owner is
    trusted for a recovery decision (DEF-2). Without this a record with a missing holder yields
    None, and a paired lease with a missing holder also yields None, so None == None would compare
    equal and a live holder be seized; an unchecked top-key set would likewise admit a forged or
    truncated record. The full closed top-key set, the supported integer schema, a required
    non-empty holder / operation / op_id / acquired_at, an [owner] table, and owner/record
    consistency (the owner's session is the record's op_id and the owner's utc is the record's
    acquired_at) are all required; any failure refuses recovery. The [owner] identity fields
    themselves are validated by _journal.owner_confirmed_dead, which reads a malformed owner as
    possibly-live. Returns the validated [owner] table."""
    if set(doc) != set(ACTIVE_TOP_KEYS):
        raise OpLockError("the stale active record top-level keys {} do not equal the closed set "
                          "{}; refusing recovery (manual intervention required)".format(
                              sorted(doc), sorted(ACTIVE_TOP_KEYS)))
    if type(doc["schema"]) is not int or doc["schema"] != _SCHEMA:
        raise OpLockError("the stale active record schema is not the supported version {}; refusing "
                          "recovery (manual intervention required)".format(_SCHEMA))
    for key in ("holder", "operation", "op_id", "acquired_at"):
        val = doc.get(key)
        if type(val) is not str or not val:
            raise OpLockError("the stale active record {} is missing or not a non-empty string; "
                              "refusing recovery (manual intervention required)".format(key))
    owner = doc.get("owner")
    if not isinstance(owner, dict):
        raise OpLockError("the stale active record carries no [owner] identity; its holder may be "
                          "live; refusing recovery (manual intervention required)")
    if owner.get("session") != doc["op_id"] or owner.get("utc") != doc["acquired_at"]:
        raise OpLockError("the stale active record [owner] is inconsistent with the record it sits "
                          "in (owner session/utc do not match op_id/acquired_at); refusing recovery "
                          "(manual intervention required)")
    return owner


def _validate_recovery_lease(doc, active_doc):
    """Validate a stale LEASE against its COMPLETE schema before it is paired with the validated
    active record (DEF-2): the full closed top-key set, the supported integer schema, and a required
    non-empty holder / operation / acquired_at, so a missing lease holder can never compare equal to
    a missing active holder as None == None. The pairing then requires the holder, operation, and
    acquired_at to EQUAL the active record's, since acquisition writes both records from the same
    values; a lease from any other acquisition is not this dead holder's and is never seized."""
    if set(doc) != set(_opf_check.LEASE_TOP_KEYS):
        raise OpLockError("the stale lease top-level keys {} do not equal the closed set {}; "
                          "refusing recovery (manual intervention required)".format(
                              sorted(doc), sorted(_opf_check.LEASE_TOP_KEYS)))
    if type(doc["schema"]) is not int or doc["schema"] != _SCHEMA:
        raise OpLockError("the stale lease schema is not the supported version {}; refusing "
                          "recovery (manual intervention required)".format(_SCHEMA))
    for key in ("holder", "operation", "acquired_at"):
        val = doc.get(key)
        if type(val) is not str or not val:
            raise OpLockError("the stale lease {} is missing or not a non-empty string; refusing "
                              "recovery (manual intervention required)".format(key))
    if doc["holder"] != active_doc["holder"]:
        raise OpLockError("the stale lease holder does not match the confirmed-dead active "
                          "record holder; refusing recovery (manual intervention required)")
    if doc["operation"] != active_doc["operation"] \
            or doc["acquired_at"] != active_doc["acquired_at"]:
        raise OpLockError("the stale lease operation/acquired_at do not match the confirmed-dead "
                          "active record (not the same acquisition); refusing recovery (manual "
                          "intervention required)")


def _require_holder_confirmed_dead(ctl_fd, machine_fd, stale_active, stale_lease):
    """The recovery LIVENESS GATE (fail-closed). Under recover=True a stale record is cleared ONLY
    when the recorded holder is CONFIRMED DEAD, reusing the journal's possibly-live-never-seized
    model (_journal.owner_confirmed_dead) over the owner identity the active record persists. A
    possibly-live, cross-host, or malformed holder, or a stale lease with no paired active record,
    RAISES rather than being seized, so a live holder whose records are exposed under a split
    anchor can never be recovered into a two-holder state. Both records are validated against their
    COMPLETE schemas first (DEF-2). Returns the (st_dev, st_ino) identity and exact bytes it read
    for the active record and, when present, the lease, so the caller's delete removes ONLY those
    same objects with those same bytes (DEF-1: a record swapped to a LIVE holder B's after this
    read is refused and preserved, never seized). Called ONLY on the explicit recover=True path,
    under the held anchor flock, before any _recover_stale delete runs."""
    if not stale_active:
        raise OpLockError("a stale lease exists with no paired active record; its holder carries no "
                          "owner identity and cannot be confirmed dead (possibly live); refusing "
                          "recovery (manual intervention required)")
    doc, active_ident, active_bytes = _read_control_record(ctl_fd, ACTIVE_NAME, "active record")
    owner = _validate_recovery_active(doc)
    node = owner.get("nodename")
    if not isinstance(node, str) or not node:
        raise OpLockError("the stale active record owner has no usable nodename; its holder may be "
                          "live; refusing recovery (manual intervention required)")
    here = os.uname().nodename
    if node != here:
        raise OpLockError("the stale active record was written on host {!r}, not this host {!r}; a "
                          "cross-host holder is never seized; refusing recovery (manual "
                          "intervention required)".format(node, here))
    if not _journal.owner_confirmed_dead(owner):
        raise OpLockError("the stale active record holder is not confirmed dead (possibly live or a "
                          "malformed owner identity); a possibly-live holder is never seized; "
                          "refusing recovery (manual intervention required)")
    lease_ident = lease_bytes = None
    if stale_lease:
        lease_doc, lease_ident, lease_bytes = _read_control_record(
            machine_fd, _opf_check.LEASE_NAME, "lease")
        _validate_recovery_lease(lease_doc, doc)
    return active_ident, active_bytes, lease_ident, lease_bytes


def _recover_stale(dir_fd, name, ident, expected_bytes, label):
    """Identity- and byte-verified unlink of an OBSERVED stale control record, under the held anchor
    flock and only on the explicit recover=True path AFTER the liveness gate confirmed the holder
    dead. `ident` and `expected_bytes` are the (st_dev, st_ino) identity and exact bytes the
    liveness gate read for THIS record; the record is removed ONLY when it is STILL that same object
    carrying those same bytes, re-verified here before the unlink. A record swapped after the
    liveness read (a LIVE holder B publishing fresh records under a split anchor) fails the identity
    or byte check and is refused and PRESERVED rather than deleted into a two-holder state (DEF-1).
    The opened object (not just the name) must be a plain singly-linked regular file; the name must
    still bind that same inode at the final pre-unlink re-check; anything else refuses and
    preserves."""
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
        if (st.st_dev, st.st_ino) != ident:
            raise OpLockError("stale {} was replaced after the liveness gate read it (device/inode "
                              "mismatch); refusing recovery and PRESERVING it (a live holder's "
                              "record is never seized)".format(label))
        if st.st_size != len(expected_bytes):
            raise OpLockError("stale {} size {} no longer matches the {} bytes the liveness gate "
                              "read; refusing recovery and PRESERVING it (a live holder's record is "
                              "never seized)".format(label, st.st_size, len(expected_bytes)))
        data = bytearray()
        while len(data) <= len(expected_bytes):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if bytes(data) != expected_bytes:
            raise OpLockError("stale {} no longer carries the bytes the liveness gate confirmed "
                              "dead; refusing recovery and PRESERVING it (a live holder's record is "
                              "never seized)".format(label))
    except OSError as exc:
        raise OpLockError("cannot verify stale {} for recovery ({})".format(label, exc))
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


def _unlink_created_on_failure(dir_fd, name, fd):
    """LOW-1: best-effort removal of a just-created control file whose write/fsync FAILED, so a
    torn record is not stranded for a later acquisition to meet. The file was O_CREAT|O_EXCL-created
    and is singly-linked, so it is ours; the name is required to still bind the open fd's (st_dev,
    st_ino) before the unlink, and any mismatch or error leaves the file in place (never unlink a
    substituted target). Returns None when the torn record was removed (or was already gone under
    its name); returns a reason string NAMING the stranded leftover when it could not be removed, so
    the caller aggregates it into the raise rather than claim a clean unwind it did not achieve
    (DEF-5: the disclosure stays accurate, the cleanup failure is never swallowed)."""
    try:
        fst = os.fstat(fd)
    except OSError as exc:
        return "could not stat the torn {} to remove it ({}); it may be stranded".format(name, exc)
    if fst.st_nlink != 1:
        return ("the torn {} has {} links (not exclusively ours); left in place and possibly "
                "stranded".format(name, fst.st_nlink))
    ident = (fst.st_dev, fst.st_ino)
    try:
        name_st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None                        # the name is already gone: nothing stranded under it
    except OSError as exc:
        return "could not re-stat the torn {} before removal ({}); it may be stranded".format(
            name, exc)
    if not stat.S_ISREG(name_st.st_mode) or (name_st.st_dev, name_st.st_ino) != ident:
        return ("the torn {} name no longer binds the created inode (substituted); left in "
                "place".format(name))
    try:
        os.unlink(name, dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError as exc:
        return "could not unlink the torn {} ({}); it is stranded".format(name, exc)
    return None


def _create_control_file(dir_fd, name, payload, label):
    """O_CREAT|O_EXCL creation of a control record, the full-byte write loop, and durability
    (fsync of the file AND its parent directory) before returning. Returns the still-open fd and
    the (st_dev, st_ino) identity; the caller retains both. On a write/fsync FAILURE the just-created
    file is unlinked by verified identity before raising (LOW-1: no torn record is stranded), UNLESS
    that cleanup itself fails, in which case the raise NAMES the stranded leftover rather than
    implying a clean unwind (DEF-5)."""
    try:
        fd = os.open(name,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, 0o644, dir_fd=dir_fd)
    except FileExistsError:
        raise OpLockError("{} already exists under a free anchor (EXPLICIT recovery required: "
                          "recover=True)".format(label))
    except OSError as exc:
        raise OpLockError("cannot create {} ({})".format(label, exc))
    try:
        _journal._write_all(fd, payload)
        os.fsync(fd)
        os.fsync(dir_fd)
        st = os.fstat(fd)
    except OSError as exc:
        strand = _unlink_created_on_failure(dir_fd, name, fd)
        os.close(fd)
        if strand is not None:
            raise OpLockError("cannot write {} ({}); additionally the torn record could not be "
                              "cleaned up: {}".format(label, exc, strand))
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
    except OSError as exc:
        # DEF-3: normalize a raw OS failure (an fstat or an EIO read) to OpLockError, so the
        # error-collecting callers (release_operation and the acquire unwind) that catch ONLY
        # OpLockError still run the remaining legs, the final unlock, and the descriptor closes,
        # rather than a raw OSError skipping them and leaking the fd and the records.
        raise OpLockError("cannot verify {} for the release leg ({}); preserved".format(label, exc))
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
    recover=True. recover=True is NOT a licence to seize: the recovery liveness gate clears a stale
    record only when the recorded holder is CONFIRMED DEAD on this host (via the journal's
    possibly-live-never-seized model over the owner identity the active record persists); a
    possibly-live, cross-host, or malformed holder, or a stale lease with no paired active record,
    is refused so a live holder whose records are exposed under a split anchor is never seized into
    a two-holder state. Returns an OpCapability; raises OpLockError fail-closed on everything else.
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
        # crash artefact; only explicit recovery clears it, and only for a CONFIRMED-DEAD holder.
        stale_active = _classify_stale(ctl_fd, ACTIVE_NAME, "active record")
        stale_lease = _classify_stale(machine_fd, _opf_check.LEASE_NAME, "lease")
        if stale_active or stale_lease:
            if not recover:
                stale = ", ".join(n for n, s in ((ACTIVE_NAME, stale_active),
                                                 (_opf_check.LEASE_NAME, stale_lease)) if s)
                raise OpLockError(
                    "stale operation record(s) under a free anchor ({}); refusing without EXPLICIT "
                    "recovery (recover=True), which itself proceeds only when the recorded holder "
                    "is confirmed dead".format(stale))
            # recover=True: CONFIRM the recorded holder is dead before any delete (never seize a
            # possibly-live, cross-host, or malformed holder, nor a lone owner-less lease). The gate
            # returns the identity and bytes it read for each record so the delete below removes ONLY
            # those same objects with those same bytes (DEF-1: a record swapped to a live holder's
            # after the liveness read is preserved, never seized).
            rec_active_ident, rec_active_bytes, rec_lease_ident, rec_lease_bytes = \
                _require_holder_confirmed_dead(ctl_fd, machine_fd, stale_active, stale_lease)
            if stale_active:
                _recover_stale(ctl_fd, ACTIVE_NAME, rec_active_ident, rec_active_bytes,
                               "active record")
            if stale_lease:
                _recover_stale(machine_fd, _opf_check.LEASE_NAME, rec_lease_ident, rec_lease_bytes,
                               "lease")

        op_id = str(uuid.uuid4())
        _validate_field("op_id", op_id)
        acquired_at = _utc_now()
        # The active record persists the FULL owner identity a later recovery needs to decide
        # liveness WITHOUT inferring it: exactly the schema _journal.owner_confirmed_dead validates
        # (pid, uid, session, utc, and the canonical /proc start time), plus the nodename that lets
        # recovery refuse a cross-host holder it can never probe by pid.
        owner = dict(pid=os.getpid(), uid=os.getuid(), nodename=os.uname().nodename,
                     session=op_id, utc=acquired_at)
        owner["pid-start"] = _journal._pid_start(os.getpid())
        active_payload = _control_payload(
            dict(schema=_SCHEMA, op_id=op_id, holder=holder, operation=operation,
                 acquired_at=acquired_at, owner=owner),
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
        # Unwind in reverse leg order; every unwind failure is collected, never swallowed. The
        # record legs run inside a try whose finally ALWAYS unlocks the anchor and closes every
        # descriptor, so even an unexpected failure in a leg cannot skip the unlock and leak the
        # fds, the flock, and the records; a leg's raw OSError is collected like an OpLockError
        # (DEF-3, defence in depth over _verified_unlink's own normalization).
        unwind = []
        try:
            if lease_created:
                try:
                    _verified_unlink(machine_fd, _opf_check.LEASE_NAME, lease_ident, lease_payload,
                                     "lease (unwind)")
                except (OpLockError, OSError) as uexc:
                    unwind.append(str(uexc))
            if active_created:
                try:
                    _verified_unlink(ctl_fd, ACTIVE_NAME, active_ident, active_payload,
                                     "active record (unwind)")
                except (OpLockError, OSError) as uexc:
                    unwind.append(str(uexc))
        finally:
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
    # leg failed, so one preserved mismatch cannot strand the other record. The legs run inside a
    # try whose finally ALWAYS unlocks the anchor and closes every retained descriptor, so even an
    # unexpected failure in a leg cannot skip the unlock and leak the fds and the flock; a leg's raw
    # OSError is collected like an OpLockError (DEF-3, defence in depth over _verified_unlink's own
    # normalization).
    try:
        try:
            _verified_unlink(cap._machine_fd, _opf_check.LEASE_NAME, cap._lease_ident,
                             cap._lease_bytes, "lease")
        except (OpLockError, OSError) as exc:
            errors.append("lease leg: {}".format(exc))
        try:
            _verified_unlink(cap._ctl_fd, ACTIVE_NAME, cap._active_ident, cap._active_bytes,
                             "active record")
        except (OpLockError, OSError) as exc:
            errors.append("active leg: {}".format(exc))
    finally:
        # The anchor is unlocked LAST and NEVER unlinked; then every retained descriptor closes.
        try:
            fcntl.flock(cap._anchor_fd, fcntl.LOCK_UN)
        except OSError as exc:
            errors.append("unlock: {}".format(exc))
        for open_fd in (cap._lease_fd, cap._active_fd, cap._anchor_fd, cap._ctl_fd,
                        cap._machine_fd):
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
    """A pinned, hermetic environment for FIXTURE git commands: ambient GIT_* dropped, then HOME,
    XDG_CONFIG_HOME, and the global/system config files bound into the fixture so no ambient user
    or system git config can affect a fixture command (LOW-5). The production _git_common_dir keeps
    HOME/XDG_CONFIG_HOME (scrubbing only GIT_*), so self_test() also pins those in os.environ."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, "xdg")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.path.join(home, "gitconfig-global")
    env["GIT_CONFIG_SYSTEM"] = os.path.join(home, "gitconfig-system")
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


def _st_reaped_child():
    """A pid that is CONFIRMED DEAD on this host: a forked child reports its /proc starttime and
    exits, and the parent reaps it, so os.kill(pid, 0) later raises ProcessLookupError (or, on the
    astronomically-unlikely reuse, a differing start time confirms death). Returns (pid,
    pid_start)."""
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:                          # the child: report its own start time, then die
        os.close(rfd)
        try:
            os.write(wfd, _journal._pid_start(os.getpid()).encode("utf-8"))
        except OSError:
            pass
        os._exit(0)
    os.close(wfd)
    data = b""
    while True:
        chunk = os.read(rfd, 4096)
        if not chunk:
            break
        data += chunk
    os.close(rfd)
    os.waitpid(pid, 0)                    # reap: the pid is now dead
    return pid, data.decode("utf-8")


def _st_write_active_owned(root, pid, pid_start, nodename, op_id, holder=None):
    """Write a stale active.toml carrying an [owner] identity directly (a crash artefact the
    recovery gate reads). Returns the holder string it wrote, so a paired lease can match it."""
    if holder is None:
        holder = "opf:{}:{}".format(nodename, pid)
    lines = (
        'schema = 1',
        'op_id = "{}"'.format(op_id),
        'holder = "{}"'.format(holder),
        'operation = "recovered-op"',
        'acquired_at = "2026-01-01T00:00:00Z"',
        '',
        '[owner]',
        'pid = {}'.format(pid),
        'uid = {}'.format(os.getuid()),
        'nodename = "{}"'.format(nodename),
        'session = "{}"'.format(op_id),
        'utc = "2026-01-01T00:00:00Z"',
        'pid-start = "{}"'.format(pid_start),
        '',
    )
    with open(os.path.join(_st_ctl_dir(root), ACTIVE_NAME), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return holder


def _st_write_lease_owned(root, holder):
    """Write a stale lease.toml naming `holder` (a crash artefact paired with the active record)."""
    with open(_st_lease_path(root), "w", encoding="utf-8") as fh:
        fh.write('schema = 1\nholder = "{}"\noperation = "recovered-op"\n'
                 'acquired_at = "2026-01-01T00:00:00Z"\n'.format(holder))


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


def _t_c2_dirperms(d, env):
    """T-c2-dirperms (LOW-4): a group- or other-writable CONTROL DIRECTORY refuses acquisition (the
    directory ownership/permission vector, distinct from the anchor-file bits in T-c2)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    os.chmod(ctl, 0o775)                  # group-writable control dir
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(ctl, 0o757)                  # other-writable control dir
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(ctl, 0o755)
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
    while held with EXACTLY the recorded bytes (checked-encoder TOML, closed key sets); the active
    record now persists an [owner] identity; the lease is gone after release."""
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
    owner = active_doc["owner"]
    assert isinstance(owner, dict), owner
    assert owner["pid"] == os.getpid() and type(owner["pid"]) is int
    assert owner["nodename"] == os.uname().nodename
    assert isinstance(owner["pid-start"], str)
    assert owner["session"] == cap.op_id and owner["utc"] == active_doc["acquired_at"]
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
    """T-c5: a stale record under a free anchor refuses without recover=True; and a LONE stale
    lease with NO paired active record is fail-closed even WITH recover=True (its holder carries no
    owner identity and cannot be confirmed dead, so it is possibly-live and never seized). The
    confirmed-dead recovery path is exercised by T-r3-dead."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    with open(_st_lease_path(root), "wb") as fh:
        fh.write(b'schema = 1\nholder = "opf:host:1"\noperation = "old"\n'
                 b'acquired_at = "2026-01-01T00:00:00Z"\n')
    _st_expect_refusal(acquire_operation, root, "op", needle="stale")
    _st_expect_refusal(acquire_operation, root, "op", recover=True,
                       needle="no paired active record")
    assert os.path.exists(_st_lease_path(root)), "a possibly-live lone lease is never seized"
    os.unlink(_st_lease_path(root))       # operator manual intervention (no confirmable owner)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_r3_live_recover_refuses(d, env):
    """T-r3-live-recover-refuses (the gemini-CRITICAL regression): a LIVE holder's records exposed
    under a FRESH/free anchor are NEVER seized under recover=True. The anchor is split BOTH ways (a
    fresh anchor via unlink of mutex.lock, and via rename aside); recovery must REFUSE and delete
    nothing, so no two-holder state can arise."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)                # establish the control dir + persistent anchor
    node = os.uname().nodename
    live_pid = os.getpid()
    live_start = _journal._pid_start(live_pid)
    holder = _st_write_active_owned(root, live_pid, live_start, node, op_id="live-op-id")
    _st_write_lease_owned(root, holder=holder)
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    active = os.path.join(ctl, ACTIVE_NAME)
    lease = _st_lease_path(root)
    for split in ("unlink", "rename"):
        # Force a SECOND acquirer to mint a fresh, free anchor while the LIVE holder's records stay:
        if split == "unlink":
            if os.path.exists(anchor):
                os.unlink(anchor)
        else:
            if os.path.exists(anchor):
                os.rename(anchor, anchor + ".aside")
        _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="live")
        assert os.path.exists(active), "the LIVE holder's active record must be preserved ({})".format(split)
        assert os.path.exists(lease), "the LIVE holder's lease must be preserved ({})".format(split)
        if split == "rename" and os.path.exists(anchor + ".aside"):
            os.unlink(anchor + ".aside")
    # No two-holder state ever arose; the (fictitious) live holder's records are cleared manually.
    os.unlink(active)
    os.unlink(lease)


def _t_r3_dead_recover_proceeds(d, env):
    """T-r3-dead-recover-proceeds: a CONFIRMED-DEAD owner (same host, a reaped child's pid plus its
    recorded /proc start time) refuses without recover=True and is cleared WITH recover=True; the
    paired lease (matching holder) is cleared with it."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    dead_pid, dead_start = _st_reaped_child()
    node = os.uname().nodename
    holder = _st_write_active_owned(root, dead_pid, dead_start, node, op_id="dead-op-id")
    _st_write_lease_owned(root, holder=holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_expect_refusal(acquire_operation, root, "op", needle="recover=True")
    cap = acquire_operation(root, "op", recover=True)   # confirmed dead: proceeds and clears
    release_operation(cap)
    assert not os.path.exists(active), "a confirmed-dead active record is cleared"
    assert not os.path.exists(lease), "the paired lease is cleared with it"


def _t_r3_crosshost_refuses(d, env):
    """T-r3-crosshost-refuses: an owner with a FOREIGN nodename is never seized (a remote pid can
    never be probed locally), so recover=True REFUSES and preserves the records."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    foreign = "some-other-host.invalid"
    assert foreign != os.uname().nodename
    holder = _st_write_active_owned(root, os.getpid(), _journal._pid_start(os.getpid()),
                                    foreign, op_id="foreign-op-id")
    _st_write_lease_owned(root, holder=holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_expect_refusal(acquire_operation, root, "op", recover=True, needle="host")
    assert os.path.exists(active) and os.path.exists(lease), \
        "a cross-host holder's records are never seized"
    os.unlink(active)
    os.unlink(lease)


def _t_c6_c7_med6_verified_release(d, env):
    """T-c6/T-c7 plus T-med6: a tampered record is refused and PRESERVED at release, and the OTHER
    leg still runs (error collection, not an early raise). A preserved, owner-less leftover is
    cleared by explicit manual intervention (recover=True no longer seizes it: it has no confirmable
    owner)."""
    root = _st_git_store(d, "repo", env)
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    cap = acquire_operation(root, "op")
    with open(lease, "ab") as fh:
        fh.write(b"tampered = true\n")
    _st_expect_refusal(release_operation, cap, needle="lease leg")
    assert os.path.exists(lease), "a mismatched lease must be PRESERVED"
    assert not os.path.exists(active), "the active leg must still run (T-med6)"
    os.unlink(lease)                                     # manual clear of the owner-less leftover
    cap = acquire_operation(root, "op")
    with open(active, "wb") as fh:                       # replaced content, same inode
        fh.write(b"forged = true\n")
    _st_expect_refusal(release_operation, cap, needle="active leg")
    assert os.path.exists(active), "a mismatched active record must be PRESERVED"
    assert not os.path.exists(lease), "the lease leg must still have run"
    os.unlink(active)                                    # manual clear of the owner-less leftover
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_c6_diffinode(d, env):
    """T-c6-diffinode (LOW-4): a same-BYTES but DIFFERENT-INODE lease swap under a held capability
    is refused and PRESERVED at release (identity is device+inode, not bytes), and the active leg
    still runs."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    with open(lease, "rb") as fh:
        same_bytes = fh.read()
    os.unlink(lease)                                     # swap the inode: same bytes, new inode
    with open(lease, "wb") as fh:
        fh.write(same_bytes)
    os.chmod(lease, 0o644)
    _st_expect_refusal(release_operation, cap, needle="device/inode")
    assert os.path.exists(lease), "a byte-identical but inode-swapped lease is PRESERVED"
    assert not os.path.exists(active), "the active leg still runs"
    os.unlink(lease)                                     # manual clear of the preserved swap
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_h4_quote(d, env):
    """T-h4-quote (LOW-4): a holder carrying a double-quote (allowed: it is outside the control
    class) round-trips through the checked encoder to a schema-valid lease with the value intact,
    distinct from the control-character refusal (T-low1)."""
    root = _st_git_store(d, "repo", env)
    holder = 'opf:host:pid with a "double quote" inside'
    cap = acquire_operation(root, "op", holder=holder)
    lease = _st_lease_path(root)
    with open(lease, "rb") as fh:
        doc = tomllib.loads(fh.read().decode("utf-8"))
    assert set(doc) == set(_opf_check.LEASE_TOP_KEYS), doc
    assert doc["holder"] == holder, "the double-quoted holder must round-trip intact"
    assert cap.holder == holder
    active_doc = tomllib.loads(cap._active_bytes.decode("utf-8"))
    assert active_doc["holder"] == holder
    release_operation(cap)
    assert not os.path.exists(lease)


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


def _t_c3_companion(d, env):
    """T-c3-companion (LOW-4): two product roots companion-pointed at ONE store contend on the
    single shared anchor; the lease is rooted at the RESOLVED store root, not at either product
    root."""
    store = _st_git_store(d, "store", env)
    lease = os.path.join(store, ".working", "toml", _opf_check.LEASE_NAME)
    prod_a = os.path.join(d, "prodA")
    prod_b = os.path.join(d, "prodB")
    for prod in (prod_a, prod_b):
        os.mkdir(prod)
        with open(os.path.join(prod, _opf_store.POINTER_REL), "w", encoding="utf-8") as fh:
            fh.write('[store]\ntarget = "dir:{}"\n'.format(store))
    cap = acquire_operation(prod_a, "op-a")
    assert os.path.isfile(lease), "the lease is rooted at the resolved store, not the product root"
    assert not os.path.exists(os.path.join(prod_a, ".working", "toml", _opf_check.LEASE_NAME))
    _st_expect_refusal(acquire_operation, prod_b, "op-b", needle="held")
    release_operation(cap)
    cap = acquire_operation(prod_b, "op-b")   # freed: the companion product now acquires
    release_operation(cap)
    assert not os.path.exists(lease)


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


def _t_low1_torn_write(d, env):
    """T-low1-torn (LOW-1): a forced short/failed control write leaves NO stranded file. The
    active-record write is made to write a partial prefix and then raise; the just-created record
    must be unlinked (not stranded as a crash artefact), and the lock must remain re-acquirable."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    saved_write_all = _journal._write_all

    def _boom(fd, payload):
        os.write(fd, payload[:5])         # a torn partial write, then fail
        raise OSError("simulated control-write failure (T-low1-torn)")

    _journal._write_all = _boom
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="cannot write")
    finally:
        _journal._write_all = saved_write_all
    assert not os.path.exists(active), "a failed control write must not strand a torn active record"
    assert not os.path.exists(lease), "no lease is created when the active write fails first"
    cap = acquire_operation(root, "op")   # fully unwound and re-acquirable
    release_operation(cap)


def _t_d1_swap_after_liveness_gate(d, env):
    """T-d1 (DEF-1, BLOCKER): after the liveness gate confirms holder A dead, an anchor split lets a
    LIVE holder B publish FRESH records at the same names before the recovery delete runs. The delete
    must remove ONLY the very objects+bytes the gate read, so B's swapped-in live records are refused
    and PRESERVED, never seized into a two-holder state. The interleave is injected by wrapping the
    module's own _require_holder_confirmed_dead to swap the records the instant it returns."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)                # establish the control dir + persistent anchor
    node = os.uname().nodename
    dead_pid, dead_start = _st_reaped_child()
    a_holder = _st_write_active_owned(root, dead_pid, dead_start, node, op_id="dead-A-op-id",
                                      holder="opf:holderA-dead")
    _st_write_lease_owned(root, holder=a_holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    mod = sys.modules[__name__]
    saved_gate = mod._require_holder_confirmed_dead

    def _wrapped_gate(*a, **k):
        result = saved_gate(*a, **k)      # confirms A dead, captures A's ident+bytes
        # A split anchor: a LIVE holder B (this process) replaces A's records RIGHT NOW, new inodes.
        os.unlink(active)
        b_holder = _st_write_active_owned(root, os.getpid(), _journal._pid_start(os.getpid()),
                                          node, op_id="live-B-op-id", holder="opf:holderB-live")
        os.unlink(lease)
        _st_write_lease_owned(root, holder=b_holder)
        return result

    mod._require_holder_confirmed_dead = _wrapped_gate
    try:
        _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="never seized")
    finally:
        mod._require_holder_confirmed_dead = saved_gate
    assert os.path.exists(active) and os.path.exists(lease), \
        "a live holder B's swapped-in records must be PRESERVED, never deleted into two holders"
    with open(active, "rb") as fh:
        assert b"holderB-live" in fh.read(), "B's record must be intact (not A's, not deleted)"
    os.unlink(active)                      # manual clear of the fictitious live-B records
    os.unlink(lease)


def _t_d2_missing_holder_fields(d, env):
    """T-d2 (DEF-2): stale records whose holder is MISSING or EMPTY must NOT be seized under
    recover=True. A missing holder yields None on both records (None == None) and an empty holder
    yields "" on both ("" == ""), so the pre-fix equality would match and seize a possibly-live
    holder. Both the completed-schema keyset check and the required-non-empty-holder check refuse,
    with a CONFIRMED-DEAD reaped-child owner so ONLY that validation stands in the way."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    dead_pid, dead_start = _st_reaped_child()
    node = os.uname().nodename
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)

    def _write_active(holder_line):
        lines = ["schema = 1", 'op_id = "d2-op-id"']
        if holder_line is not None:
            lines.append(holder_line)
        lines += ['operation = "recovered-op"', 'acquired_at = "2026-01-01T00:00:00Z"', "",
                  "[owner]", "pid = {}".format(dead_pid), "uid = {}".format(os.getuid()),
                  'nodename = "{}"'.format(node), 'session = "d2-op-id"',
                  'utc = "2026-01-01T00:00:00Z"', 'pid-start = "{}"'.format(dead_start), ""]
        with open(active, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(lines))

    def _write_lease(holder_line):
        lines = ["schema = 1"]
        if holder_line is not None:
            lines.append(holder_line)
        lines += ['operation = "recovered-op"', 'acquired_at = "2026-01-01T00:00:00Z"', ""]
        with open(lease, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(lines))

    # Sub-case A: holder KEY MISSING on both (None == None) -> refused by the keyset check.
    _write_active(None)
    _write_lease(None)
    _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="refusing recovery")
    assert os.path.exists(active) and os.path.exists(lease), \
        "missing-holder records are never seized"

    # Sub-case B: holder EMPTY on both ("" == "") -> refused by the required-non-empty-holder check.
    _write_active('holder = ""')
    _write_lease('holder = ""')
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="holder is missing or not a non-empty string")
    assert os.path.exists(active) and os.path.exists(lease), "empty-holder records are never seized"

    # Sub-case C: complete, equal holders, but the lease is from a DIFFERENT acquisition (its
    # operation differs) -> refused by the pairing check; the pre-fix holder-only match seized it.
    _write_active('holder = "opf:d2-holder"')
    _write_lease('holder = "opf:d2-holder"')
    with open(lease, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(lease, "w", encoding="utf-8") as fh:
        fh.write(text.replace('operation = "recovered-op"', 'operation = "another-op"'))
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="not the same acquisition")
    assert os.path.exists(active) and os.path.exists(lease), "an unpaired lease is never seized"

    # Sub-case D: complete and paired, but an unsupported schema -> refused.
    _write_lease('holder = "opf:d2-holder"')
    with open(active, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(active, "w", encoding="utf-8") as fh:
        fh.write(text.replace("schema = 1", "schema = 2", 1))
    _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="supported version")
    assert os.path.exists(active) and os.path.exists(lease), "an unsupported schema is never seized"
    os.unlink(active)
    os.unlink(lease)


def _t_d3_eio_release_and_unwind(d, env):
    """T-d3 (DEF-3): a raw OS failure (EIO) inside a verified-unlink leg is normalized to OpLockError
    and collected, so BOTH the lease and active legs and the final anchor unlock still run instead of
    a raw OSError skipping the active leg, the unlock, and _released and leaking the fds. Exercised
    for BOTH the release path and the acquire unwind path. os.read is faulted ONLY for the control
    records (matched by the read fd's (st_dev, st_ino) against the records' current identities, a
    portable match with no /proc dependency), so git subprocess pipe reads are unaffected."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    mod = sys.modules[__name__]
    saved_read = os.read

    def _record_idents():
        idents = set()
        for path in (active, lease):
            try:
                st = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            idents.add((st.st_dev, st.st_ino))
        return idents

    def _eio_read(fd, n):
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) in _record_idents():
            raise OSError(errno.EIO, "simulated read failure (T-d3)")
        return saved_read(fd, n)

    # Phase 1: EIO in RELEASE. Both legs run (both preserve), the anchor is unlocked, _released set.
    cap = acquire_operation(root, "op")
    os.read = _eio_read
    try:
        msg = _st_expect_refusal(release_operation, cap, needle="active leg")
    finally:
        os.read = saved_read
    assert "lease leg" in msg and "active leg" in msg, msg
    assert cap._released is True, "the unlock/close/_released must run despite the EIO legs"
    assert os.path.exists(active) and os.path.exists(lease), \
        "both records PRESERVED: both legs ran, neither skipped"
    os.unlink(active)
    os.unlink(lease)
    cap = acquire_operation(root, "op")    # re-acquirable: the anchor was truly unlocked
    release_operation(cap)

    # Phase 2: EIO in the acquire UNWIND. Force a post-creation failure (a raising OpCapability) so
    # both records exist when the unwind runs, with os.read still faulting each unwind leg's read.
    saved_cap = mod.OpCapability

    class _BoomCap:
        def __init__(self, *a, **k):
            raise OSError("simulated post-creation failure (T-d3 unwind)")

    os.read = _eio_read
    mod.OpCapability = _BoomCap
    try:
        msg = _st_expect_refusal(acquire_operation, root, "op", needle="unwind failed")
    finally:
        os.read = saved_read
        mod.OpCapability = saved_cap
    assert "lease (unwind)" in msg and "active record (unwind)" in msg, msg
    assert os.path.exists(active) and os.path.exists(lease), \
        "the EIO-failed unwind legs PRESERVED both records (both ran)"
    os.unlink(active)                      # manual clear of the preserved records
    os.unlink(lease)
    cap = acquire_operation(root, "op")    # anchor unlocked + fds closed by the unwind finally
    release_operation(cap)


def _t_d4_gitdir_trailing_space(d, env):
    """T-d4 (DEF-4): _git_common_dir strips ONLY the single newline record terminator, never
    arbitrary trailing whitespace, so a common git dir whose real name ends in a space is honoured
    and the control root is not silently redirected to a stripped decoy. Witnessed end to end over
    a REAL git store whose separate git dir is named "common " beside a decoy "common": the buggy
    .strip() created the control tree in the decoy."""
    true_dir = os.path.join(d, "common ")          # trailing space IS part of the name
    decoy_dir = os.path.join(d, "common")          # the stripped path: the decoy the bug would pick
    os.mkdir(decoy_dir)
    root = os.path.join(d, "repo")
    os.mkdir(root)
    _st_git(["init", "-q", "--separate-git-dir", true_dir], root, env)
    _st_store_tree(root)
    assert _git_common_dir(root) == os.path.abspath(true_dir), \
        "the one record terminator is stripped, never the trailing space"
    cap = acquire_operation(root, "op")
    try:
        assert os.path.isfile(os.path.join(true_dir, CONTROL_DIRNAME, ANCHOR_NAME)), \
            "the anchor must live under the real space-suffixed common git dir"
        assert not os.path.exists(os.path.join(decoy_dir, CONTROL_DIRNAME)), \
            "the control tree must never be redirected to the stripped decoy"
    finally:
        release_operation(cap)


def _t_d5_failed_cleanup(d, env):
    """T-d5 (DEF-5): when the torn-record cleanup ITSELF cannot remove the file, the write failure
    AND the stranded leftover are both surfaced (the leftover named), never swallowed into a bare
    'cannot write' that falsely implies a clean unwind. Witnessed by hardlinking the just-created
    active record during the failing write so cleanup finds nlink != 1 and leaves it in place; the
    buggy code returned only 'cannot write' with the strand unnamed."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")    # create the control dir + anchor first
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    active = os.path.join(ctl, ACTIVE_NAME)
    hardlink = os.path.join(ctl, "torn-hardlink-victim")
    saved_write_all = _journal._write_all

    def _boom(fd, payload):
        os.write(fd, payload[:5])          # a torn partial write
        os.link(active, hardlink)          # pin the inode: cleanup finds nlink != 1
        raise OSError("simulated control-write failure (T-d5)")

    _journal._write_all = _boom
    try:
        msg = _st_expect_refusal(acquire_operation, root, "op", needle="could not")
    finally:
        _journal._write_all = saved_write_all
    assert "active" in msg and "cannot write" in msg, msg
    assert os.path.exists(active), \
        "a torn record the cleanup could not remove IS surfaced, not hidden"
    os.unlink(hardlink)                    # manual clear of the pinned inode
    os.unlink(active)
    cap = acquire_operation(root, "op")    # re-acquirable after manual cleanup
    release_operation(cap)


def self_test():
    """Regression roster (plan section (e)): the PR2 T-c/T-crit/T-med/T-low roster PLUS the PR2
    round-3 recovery-liveness witnesses (T-r3-live-recover-refuses, T-r3-dead-recover-proceeds,
    T-r3-crosshost-refuses), the LOW-4 coverage tests (T-c2-dirperms, T-c3-companion, T-h4-quote,
    T-c6-diffinode), the LOW-1 torn-write witness (T-low1-torn), and the recovery-hardening
    witnesses T-d1 to T-d5 (DEF-1 to DEF-5), each a witness against a named defect. A missing containment primitive or git binary is a REFUSAL (non-zero), never a clean
    skip. The git fixtures are pinned hermetically (LOW-5)."""
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
        ("T-c2-dirperms group/other-writable control dir refuses", _t_c2_dirperms),
        ("T-crit1 anchor unlink/recreate is caught, never a second holder",
         _t_crit1_anchor_swap),
        ("T-c3/T-c11 mandatory lease, exact checked-encoder bytes, owner identity",
         _t_c3_c11_mandatory_lease_and_bytes),
        ("T-c3-companion two product roots, one store, one shared anchor", _t_c3_companion),
        ("T-c4 link-count check is file-only", _t_c4_dir_nlink_free),
        ("T-c5 stale refuses; lone lease is fail-closed even under recovery",
         _t_c5_stale_and_recovery),
        ("T-r3-live recover REFUSES a live holder (never a second holder)",
         _t_r3_live_recover_refuses),
        ("T-r3-dead recover PROCEEDS on a confirmed-dead holder", _t_r3_dead_recover_proceeds),
        ("T-r3-crosshost recover REFUSES a foreign-host holder", _t_r3_crosshost_refuses),
        ("T-c6/T-c7/T-med6 verified release preserves mismatches, legs collected",
         _t_c6_c7_med6_verified_release),
        ("T-c6-diffinode same-bytes inode swap is preserved", _t_c6_diffinode),
        ("T-h4-quote a double-quoted holder round-trips intact", _t_h4_quote),
        ("T-c9 release is bound to the acquirer identity", _t_c9_acquirer_identity),
        ("T-c10 three-way .git classification, no fallback", _t_c10_git_classification),
        ("T-c12 contention and double release refuse", _t_c12_contention_and_double_release),
        ("T-c13 FIFO control names cannot block or pass", _t_c13_fifo_control_names),
        ("T-c8/T-c14 nested-lock scope-out (PR3)", _t_c8_c14_scope_out),
        ("T-low1 single-sourced field validation", _t_low1_field_validation),
        ("T-low1-torn a failed control write strands no file", _t_low1_torn_write),
        ("T-d1 (DEF-1) live holder B swapped in after the liveness gate is never seized",
         _t_d1_swap_after_liveness_gate),
        ("T-d2 (DEF-2) missing holder fields are validated, not compared as None == None",
         _t_d2_missing_holder_fields),
        ("T-d3 (DEF-3) EIO in a release/unwind leg runs both legs and the unlock",
         _t_d3_eio_release_and_unwind),
        ("T-d4 (DEF-4) git-common-dir strips only the record terminator",
         _t_d4_gitdir_trailing_space),
        ("T-d5 (DEF-5) a failed torn-record cleanup names the stranded leftover",
         _t_d5_failed_cleanup),
    )

    base = os.path.realpath(tempfile.mkdtemp(prefix="opf-oplock-selftest-"))
    # LOW-5: pin the git fixtures AND the production rev-parse hermetically. Bind HOME and
    # XDG_CONFIG_HOME (which production _git_common_dir keeps, scrubbing only GIT_*) plus
    # GIT_CONFIG_GLOBAL/SYSTEM into the per-run temp dir, and restore them afterwards, so no ambient
    # user or system git config can affect a fixture command or the module's own rev-parse.
    _saved_env = {}
    for _k in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
               "GIT_CONFIG_NOSYSTEM"):
        _saved_env[_k] = os.environ.get(_k)
    os.environ["HOME"] = base
    os.environ["XDG_CONFIG_HOME"] = os.path.join(base, "xdg")
    os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(base, "gitconfig-global")
    os.environ["GIT_CONFIG_SYSTEM"] = os.path.join(base, "gitconfig-system")
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.makedirs(os.path.join(base, "xdg"), exist_ok=True)
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
        for _k, _v in _saved_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
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
