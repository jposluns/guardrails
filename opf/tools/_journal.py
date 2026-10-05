#!/usr/bin/env python3
"""Crash-durable transactional cutover journal (VER-CORE 9.3), an importable engine module. Stdlib only.

Used by tools/migrate.py for cutover and recover; it also carries the INERT reverse-replay primitive
(build_inverse_ops, 10.6). tools/pin.py (Section 12 step 7) is now built: it reuses the low-level
contained-apply helpers, and its 1.0.0 un-adopt reverses onboarding through a CONTAINED reverse-swap
(no 9.3 journal); the corrupt-state recovery carve-out and forward re-pin remain DEFERRED at 1.0.0.

CRASH-SAFETY MODEL (journal first, then apply, then complete):
  The seven normative steps (9.3, spec lines 1252 to 1339), in order, are
    1. require_containment    fail closed BEFORE the lock on a platform without the race-free primitive.
    2. acquire_lock           one O_EXCL lock at the journal root: one open transaction at a time.
    3. capture_preimages      full prior bytes plus metadata, fsync'd, BEFORE anything is touched.
    4. publish INTENT         the ordered op list, framed and fsync'd. Only now is the transaction OPEN.
    5. apply_ops              fd-bound, no-follow, component-by-component beneath a pre-opened root
                              handle; each op's prestate is re-verified on the opened fd, never on a
                              re-resolved absolute path, then the mutation is made and fsync'd.
    6. durability             every written file and the parent directory of every touched entry fsync'd.
    7. publish COMPLETE       framed and fsync'd. The transaction is now terminal.
  Recovery reads state from the last DURABLY PUBLISHED record and never trusts a torn tail:
    no INTENT               nothing was opened; nothing to undo.
    COMPLETE or RC present  terminal; a no-op.
    INTENT, no RIP          roll FORWARD (publish COMPLETE) only when EVERY op's domain-separated
                            post-state already verifies; otherwise elect rollback (publish RIP).
    rollback                restore preimages in REVERSE dependency order (reversed(ops)) under the
                            same contained discipline, fsync, publish ROLLBACK-COMPLETE.
  Recovery is idempotent: a torn tail is truncated before any terminal frame is appended, restores are
  per-op no-ops when the path is already in its prestate, and a second recover of a terminal journal
  does nothing. The kill-injection self-test in migrate.py drives a real subprocess to os._exit at each
  named point and proves a fresh process recovers the tree to EXACTLY the prestate or the verified
  poststate, both directions, torn tails and mid-rollback included.

Guarantee scope, disclosed (spec lines 1311 to 1323): the pre-opened directory handle closes ancestor
and absolute-path re-resolution; QUIESCENCE of the effective tree, not the prestate check, is what
excludes a concurrent final-component swap; an arbitrary external writer mutating the tree during an
open cutover is outside the transaction's control and outside this guarantee. Stale-lock liveness rests
on kill(pid, 0) plus, on Linux, the /proc start-time; PID reuse on a platform without a readable start
time is a residual, and any ambiguity always reads as possibly-live (the lock is never seized).
Recovery trusts its own journal's checksummed framing: it validates that terminal frames agree with the
INTENT on the txn id and rejects duplicate or out-of-order terminal records, but a crafted valid-checksum
journal is outside the accident-recovery model (accident-detection, not tamper-resistance).
The lock's pid plus /proc-start-time liveness check is itself accident-detection, not tamper-resistance,
of the SAME class: a crafted lock file carrying a LIVE pid together with a valid-but-false canonical start
time could make recovery classify the live owner as dead and seize its lock. The engine always writes the
real start time, so normal single-writer, engine-written operation never triggers this; it requires an
external actor to forge a false lock file, which is outside the accident-recovery model, exactly like the
forged-checksum journal above. The robust fix, an OS-held fcntl lease bound to the owner process's
lifetime, is a tracked post-1.0.0 hardening.

Exit convention of the CLIs built on this module: 0 clean/NA, 1 finding, 2 malformed or read error.

  _journal.py --self-test   the #378 close vectors and the descriptor-helper vectors (read-error
                            conversion, quiet cleanup close)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _journal.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

MAGIC = b"AIQTJ1"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PIDSTART_RE = re.compile(r"^[0-9]+$")   # ASCII-digit gate for a /proc starttime (canonical bound in _is_canonical_pid_start)
_PIDSTART_MAX = 1 << 64                   # a /proc starttime is an unsigned long long: the valid range is 0 <= v < 2**64
_PIDSTART_MAX_DIGITS = 20                 # 2**64 - 1 is a 20-digit decimal, so a canonical start time is at most 20 digits
F_INTENT = "INTENT"
F_COMPLETE = "COMPLETE"
F_RIP = "ROLLBACK-IN-PROGRESS"
F_RC = "ROLLBACK-COMPLETE"
FRAME_TYPES = (F_INTENT, F_COMPLETE, F_RIP, F_RC)
OP_KINDS = ("write", "create", "remove", "mkdir", "rmdir")
KILL_ENV = "AIQT_JOURNAL_KILL"   # crash-injection hook; inert unless the self-test harness sets it
_READ_CHUNK = 1 << 20
# MINOR-1 (journal-reader memory bound): the journal CONTROL readers (read_frames, read_lock_owner) cap how
# much they will read, so a pre-planted oversize journal file (e.g. a 128 MiB frames.log in a hostile tree)
# meets a controlled fail-closed refusal rather than being slurped whole into memory (SECA resource-bounds).
# This mirrors the store-read cap discipline in _opf_store.py (a pre-open st.st_size fast-reject plus a
# post-read re-check). It bounds ONLY the small journal control files: frames.log holds a cutover's INTENT
# op-list METADATA (the file payloads live under preimages/, never here) and the lock holds a small owner
# record, so 16 MiB is generous headroom over any real cutover's framed op list while decisively refusing an
# oversize plant. The prestate verifiers (_verify_fd_prestate, _verify_prestate_at) instead cap at the
# op's KNOWN recorded size (with a fast-reject on a changed size), so a legitimate large preimage is never
# refused by this control cap while a file grown past its recorded size is still refused fail-closed (F2/F3).
_MAX_JOURNAL_READ_BYTES = 16 << 20
# The contained PRODUCT-FILE readers (_read_at, _read_contained) carry their own hard incremental ceiling.
# A caller's pre-open st.st_size fast-reject (e.g. _opf_store._read_toml_contained's store cap, or
# _opf_changelog._load_inputs's changelog ceiling) can be DEFEATED by a writer GROWING the file past
# st.st_size between the caller's lstat and this open, so the read itself is bounded here: a file grown or
# swapped past its pre-open size is refused fail-closed AT the ceiling rather than slurped whole into memory
# (SECA resource-bounds; the racing-grow read residual those callers disclosed and routed to these readers).
# 16 MiB matches the journal-control ceiling and is generous over any legitimate store control file
# (manifests, the changelog, and pointers are all well under 1 MiB), so a normal-size product file reads
# unchanged on the happy path. A caller keeping a TIGHTER cap (the 1 MiB store cap, the changelog ceiling)
# still refuses earlier on its own post-read re-check, so this is a broad memory-safety net BENEATH those,
# never a double-cap that changes their verdict for a file between the tighter cap and this ceiling.
_MAX_PRODUCT_READ_BYTES = 16 << 20


class JournalError(Exception):
    """Malformed journal state other than a detectable torn tail, or a prestate/containment violation: a
    FAIL, never skipped. The CLIs map it to exit 2 (malformed/unreadable)."""


# --- capability probe and the crash-injection hook ----------------------------------------------------

def require_containment():
    """Fail closed BEFORE the lock on a platform lacking the race-free containment primitive (dir-fd
    relative open plus O_NOFOLLOW). Both supported platforms (Linux, macOS) expose it; this is the 3.6
    forward-compat guard that no supported platform triggers (S13-4). A guard is only as good as its
    input: this reads the process's actual os capabilities, never an os-name label."""
    needed = {os.open, os.unlink, os.rename, os.mkdir, os.rmdir, os.stat}
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise JournalError("race-free containment primitive absent (no O_NOFOLLOW/O_DIRECTORY): "
                           "cutover fails closed (3.6b)")
    try:
        ok = needed.issubset(os.supports_dir_fd)
    except Exception:
        ok = False
    if not ok:
        raise JournalError("race-free containment primitive absent (dir_fd unsupported): cutover fails "
                           "closed (3.6b)")


def _kill_point(name):
    """The crash-injection point. os._exit skips every atexit/flush so the process dies exactly as a
    power loss would, leaving only what was already fsync'd on disk."""
    if os.environ.get(KILL_ENV, "") == name:
        os._exit(137)


# --- path containment helpers (fd-bound, no-follow, never a re-resolved absolute path) ----------------

def _check_rel(relpath):
    """A clean POSIX repo-relative multi-component path or JournalError. No absolute, backslash, empty,
    '.'/'..' segment, trailing slash, or control character. Returns the component list."""
    if not isinstance(relpath, str) or not relpath:
        raise JournalError("op path must be a non-empty string")
    if "\\" in relpath or relpath.startswith("/") or relpath.endswith("/"):
        raise JournalError("op path {!r} must be a clean POSIX repo-relative path".format(relpath))
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in relpath):
        raise JournalError("op path {!r} carries a control character".format(relpath))
    parts = relpath.split("/")
    if any(seg in ("", ".", "..") for seg in parts):
        raise JournalError("op path {!r} has an empty, '.', or '..' segment".format(relpath))
    return parts


def _close_fd_quietly(fd):
    """Close a descriptor on a cleanup / teardown path, swallowing an OSError so a close that raises
    (EINTR / EIO / EBADF) mid-teardown cannot ABORT the remaining cleanup and leak the sibling fds after
    it in the loop, nor propagate a raw OSError out of a `finally` in place of the JournalError the engine
    maps every other failure to. Used by the contained-walk cleanup loops (_open_parent, _open_dir_contained,
    ensure_journal_dirs), each of which closes SEVERAL opened fds in a `finally`: a raw `os.close` there,
    when one close raised, abandoned the rest (codex round-8, the sibling of _opf_check._close_fd_quietly).

    Single close (P1, #378): the descriptor is closed with exactly ONE os.close. If it raises, the number
    counts as released and is never touched again: no fstat, no second close. close(2) on Linux "always
    releases the file descriptor early in the close operation, freeing it for reuse", and retrying "is the
    wrong thing to do, since this may cause a reused file descriptor from another thread to be closed"
    (man 2 close, "Dealing with error returns from close()"); an fstat that finds the number open after a
    failed close is looking at whatever reused it, so a probe-then-reclose recovery closed another owner's
    descriptor. The error is swallowed silently, as on every teardown path. This mirrors
    _opf_check._close_fd_quietly; _journal cannot import it (the dependency runs the other way), so the
    idiom is duplicated rather than shared."""
    try:
        os.close(fd)
    except OSError:
        pass                                              # released either way (close(2)); never re-touched


def _close_fd_propagating(fd):
    """Close a descriptor on a FAIL-CLOSED path where the close error must PROPAGATE to the caller (unlike
    _close_fd_quietly's teardown swallow). Single close (P1, #378): exactly ONE os.close; if it raises, the
    number counts as released (close(2) on Linux releases it early, even when the close then reports EINTR
    or EIO, and a retry can close another thread's reused descriptor: man 2 close), so it is never probed
    or closed again, and the ORIGINAL close error propagates unchanged, preserving every existing
    fail-closed mapping of a raising close."""
    os.close(fd)


def _in_flight_in(frame):
    """The exception unwinding through, or being handled in, `frame` (the current exception's traceback
    head is that frame), else None: an exception a CALLER is handling is not in flight there."""
    exc, tb = sys.exc_info()[1:]
    return exc if tb is not None and tb.tb_frame is frame else None


def _yield_close_exceptions(inflight, raised):
    """The rule for every exception a close raised that is not an OSError (`raised`, in order). With none
    in flight (`inflight` None) the first is raised, each later one noted on it. With one in flight, none
    replaces it: each is noted on it, and it keeps propagating. The one exception: an interrupt (a
    BaseException that is not an Exception) never yields to an ERROR in flight (an Exception), so an
    interrupt is never dropped: the first such interrupt propagates as itself, with that error and every
    other one noted on it. Nothing is lost without trace."""
    if not raised:
        return
    if inflight is None:
        first = raised[0]
        for later in raised[1:]:
            first.add_note("a later close exception, recorded here and not re-raised: {!r}".format(later))
        raise first
    stop = None
    if isinstance(inflight, Exception):
        stop = next((exc for exc in raised if not isinstance(exc, Exception)), None)
    if stop is None:
        for exc in raised:
            inflight.add_note("a close raised {!r} while this exception was in flight: recorded here, never "
                              "raised in its place".format(exc))
        return
    stop.add_note("raised by a close while {!r} was in flight: an interrupt never yields to an error, which "
                  "is recorded here".format(inflight))
    for exc in raised:
        if exc is not stop:
            stop.add_note("another close exception, recorded here and not re-raised: {!r}".format(exc))
    raise stop


def _close_fds_yielding(fds, frame):
    """Close every descriptor in the list `fds` (a cleanup loop's), in order, each taken out of the list
    BEFORE its one quiet close (_close_fd_quietly), so a close that raises anything but OSError never
    abandons the rest; then every such exception yields to the one in flight in `frame`
    (_yield_close_exceptions), or with none in flight the first is raised."""
    inflight = _in_flight_in(frame)
    raised = []
    while fds:
        try:
            _close_fd_quietly(fds.pop(0))
        except BaseException as exc:    # noqa: BLE001  keep closing; yielded or raised below
            raised.append(exc)
    _yield_close_exceptions(inflight, raised)


def _close_fd_yielding(fd):
    """Close a descriptor from an `except` handler or a `finally` block without letting a close error
    REPLACE the exception already in flight there (#378): the close error _close_fd_propagating raises would
    otherwise mask the body's error whenever the body raised. When an exception is unwinding through, or
    being handled in, the CALLING frame, the same single close still runs (P1: one os.close, the number
    released either way and never touched again) but its close error is dropped, so the ORIGINAL exception
    keeps propagating; when none is (the normal path through a `finally`), this is exactly
    _close_fd_propagating and a close error still fails closed. "In flight in the calling frame" means the
    current exception's traceback head is that frame: an exception a CALLER is handling (this code reached
    normally from inside the caller's `except` block) is not in flight here and never quiets the close.
    Residual (disclosed): a `finally` reached NORMALLY while lexically inside an `except` handler of the
    SAME function would read that handled exception as in flight; no call site is nested that way.
    A close that raises anything but OSError (an interrupt, or an injected exception) while an exception
    is in flight never replaces it either: it is noted on it (_yield_close_exceptions), except that an
    interrupt never yields to an error in flight, and then propagates with that error noted on it."""
    inflight = _in_flight_in(sys._getframe(1))
    if inflight is None:
        _close_fd_propagating(fd)
        return
    try:
        _close_fd_propagating(fd)
    except OSError:
        pass                                      # the in-flight exception wins; the fd was still released
    except BaseException as exc:    # noqa: BLE001  yielded to the one in flight (_yield_close_exceptions)
        _yield_close_exceptions(inflight, [exc])


def _open_parent(root_fd, relpath):
    """Open the parent directory of relpath by walking each intermediate component beneath root_fd with
    O_DIRECTORY|O_NOFOLLOW (a symlinked component raises rather than redirects the walk). Returns
    (parent_fd, final_name); the caller closes parent_fd, and root_fd is never closed (the single-
    component case dups it). A MISSING intermediate component raises FileNotFoundError (a clean signal
    the caller reads as absent, since a missing parent means the target is absent); any other error (a
    non-directory or symlinked intermediate component) raises JournalError. The intermediate
    descriptors are closed through _close_fds_yielding: a close that raises anything but OSError never
    abandons the rest nor replaces an exception in flight here; one raised with none in flight closes the
    duplicated parent descriptor too (it is never returned), yielding to that exception."""
    parts = _check_rel(relpath)
    cur = root_fd
    opened = []
    pfd = None
    try:
        for comp in parts[:-1]:
            try:
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise JournalError("cannot open contained directory component {!r} of {!r} ({})"
                                   .format(comp, relpath, exc))
            opened.append(nfd)
            cur = nfd
        pfd = os.dup(cur)
    finally:
        try:
            _close_fds_yielding(opened, sys._getframe(0))     # a raising close never aborts the rest
        except BaseException:   # noqa: BLE001  re-raised: the parent descriptor is never returned
            if pfd is not None:
                _close_fd_yielding(pfd)
            raise
    return pfd, parts[-1]


def _read_fd(fd, cap=None):
    chunks = []
    total = 0
    while True:
        try:
            block = os.read(fd, _READ_CHUNK)
        except OSError as exc:
            # CLASS 1: a read error (EIO, EBADF, ...) mid-read is fail-closed, never a truncated or empty
            # result. Every contained reader (read_frames, read_lock_owner, _read_at, _read_contained)
            # routes its bytes through here, so converting os.read at this one choke point makes an
            # unreadable descriptor a JournalError at every call site, matching the broad-OSError posture of
            # _lstat_at and _open_parent (check-fails-closed-on-unreadable). JournalError is not an OSError
            # subclass, so a caller catching OSError does not swallow it, and a caller catching JournalError
            # fails closed as it already does for every other contained-read failure.
            raise JournalError("read error on a contained file descriptor ({})".format(exc))
        if not block:
            break
        total += len(block)
        if cap is not None and total > cap:
            # A capped reader refuses an oversize file AT the cap rather than reading it whole into memory.
            # Bounding INCREMENTALLY (never accumulating more than the cap plus one chunk) is the post-read
            # re-check a caller does with len(data), made memory-safe here so a file grown or swapped past
            # its pre-open st.st_size is still refused fail-closed (SECA resource-bounds). The journal
            # CONTROL readers pass _MAX_JOURNAL_READ_BYTES; the contained PRODUCT-FILE readers (_read_at,
            # _read_contained) pass _MAX_PRODUCT_READ_BYTES; the prestate verifiers (_verify_fd_prestate,
            # _verify_prestate_at) pass the op's KNOWN recorded size, so a valid at-size preimage is never
            # refused while a grown file is (F2/F3). cap=None (unbounded) is the bare default no caller uses.
            raise JournalError("contained file exceeds the {}-byte read cap (fail-closed)".format(cap))
        chunks.append(block)
    return b"".join(chunks)


def _lstat_contained(root_fd, relpath):
    """lstat the final component beneath its contained parent, or None when the target OR any parent
    component along the way is absent. Never follows a final-component symlink. A missing parent means
    the target is absent, which is exactly the prestate a create/mkdir op expects."""
    try:
        pfd, name = _open_parent(root_fd, relpath)
    except FileNotFoundError:
        return None
    try:
        return _lstat_at(pfd, name)
    finally:
        _close_fd_yielding(pfd)


def _lstat_at(pfd, name):
    """lstat the final component 'name' beneath an ALREADY-OPEN parent fd, or None when it is absent.
    Never follows a final-component symlink. E1: binds the prestate check to the SAME parent handle the
    mutation uses (9.3 step 4, spec 1291/1300: check AND mutate beneath one pre-opened directory handle),
    so an ancestor swap between the check and the mutation cannot redirect either onto a different tree.
    Only ENOENT (a genuinely absent final component) reads as absence (None); EVERY other OSError
    (ENAMETOOLONG, EACCES, ELOOP, ENOTDIR, ...) is a fail-closed JournalError, mirroring _read_contained's
    broad-OSError posture, so a stat that cannot answer its question is never silently read as 'absent' by
    any caller (_lstat_contained -> _read_sources/_read_toml, _verify_prestate_at, the rollback restore),
    per check-fails-closed-on-unreadable."""
    try:
        return os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise JournalError("cannot lstat contained final component {!r} ({})".format(name, exc))


def _read_at(pfd, name, relpath, cap=None):
    """Read the final component's bytes through an O_NOFOLLOW fd opened beneath the SAME parent handle,
    confirming on the opened fd it is a regular file. The fd-bound sibling of _read_contained, used where
    the read MUST bind to the parent handle the mutation uses (E1, 9.3 step 4). JournalError on a symlink,
    a non-regular file, or a read error. cap=None (the default) resolves at CALL time to the generic
    product-file ceiling _MAX_PRODUCT_READ_BYTES (read from the module global here, not baked as a default
    argument, so a runtime override of the ceiling is honoured); the prestate verifier instead passes the
    KNOWN recorded size so a legitimate large preimage is never refused by an unrelated memory cap (F2/F3)
    while a file grown past that size is still refused fail-closed."""
    if cap is None:
        cap = _MAX_PRODUCT_READ_BYTES
    # O_NONBLOCK so a non-regular final component (e.g. a FIFO raced in for the regular file after an
    # lstat gate) returns at once instead of blocking forever on a writer-less FIFO; the fstat below then
    # refuses it. O_NONBLOCK is a no-op for a regular file (SECA resource-bounds; mirrors _read_contained).
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise JournalError("contained path {!r} is not a regular file".format(relpath))
        return _read_fd(fd, cap=cap), st
    finally:
        _close_fd_yielding(fd)


def _read_contained(root_fd, relpath, require_single_link=False):
    """Read a contained regular file's bytes through an O_NOFOLLOW fd, confirming on the opened fd that
    it is a regular file. JournalError on a symlink, a non-regular file, a missing path, or a read
    error. When require_single_link is set the OPENED-fd stat must show exactly one hard link BEFORE any
    byte is read: a store CONTROL file (a manifest or `.opf.toml`/`.opf.local.toml` pointer) hardlinked to
    an out-of-tree victim passes O_NOFOLLOW and S_ISREG, and its resolved posture would then track the
    victim's inode, so it is refused here class-consistent with the journal's own frames.log nlink==1
    identity guard (SECI-symlink-resolution). The default is OFF, so a generic product read keeps an
    intentional hardlink (F-R17-A1)."""
    try:
        pfd, name = _open_parent(root_fd, relpath)
    except OSError as exc:                                 # includes FileNotFoundError
        raise JournalError("cannot read contained file {!r} ({})".format(relpath, exc))
    try:
        # O_NONBLOCK so opening a non-regular final component (e.g. a FIFO swapped in for the regular file
        # between an earlier lstat gate and this open) returns at once instead of blocking forever on a
        # writer-less FIFO; the fstat below then refuses the non-regular object. O_NONBLOCK is a no-op for a
        # regular file (SECA resource-bounds; the TOCTOU-hang backstop behind a check-then-open gate).
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    except OSError as exc:
        _close_fd_yielding(pfd)
        raise JournalError("cannot read contained file {!r} ({})".format(relpath, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise JournalError("contained path {!r} is not a regular file".format(relpath))
        if require_single_link and st.st_nlink != 1:
            # A HARDLINK to an out-of-tree victim passes O_NOFOLLOW and S_ISREG; a link count above 1 means
            # a second name references this inode. Refuse a multiply-linked CONTROL file on the OPENED fd
            # BEFORE reading a byte, so its posture cannot track a victim inode (codex round-6 sibling;
            # F-R17-A1).
            raise JournalError("contained control file {!r} has {} hard links; refusing to read a "
                               "multiply-linked control file (a hardlink to an out-of-tree victim, never "
                               "our singly-linked control file)".format(relpath, st.st_nlink))
        return _read_fd(fd, cap=_MAX_PRODUCT_READ_BYTES), st
    finally:
        # Each close in its own try/finally (round-5 defect 3): a FILE close that reports an error must
        # not skip the parent close and leak pfd. Each close is a single os.close (P1: a raising close has
        # released its number, close(2), and it is never touched again), so both descriptors are released
        # either way, and the file-close error keeps propagating fail-closed to the caller.
        try:
            _close_fd_yielding(fd)
        finally:
            _close_fd_yielding(pfd)


def _fsync_dir_fd(fd):
    os.fsync(fd)


def _fsync_parent(root_fd, relpath):
    pfd, _ = _open_parent(root_fd, relpath)
    try:
        os.fsync(pfd)
    finally:
        _close_fd_yielding(pfd)


def _fsync_path_dir(path):
    """Durability flush of a directory by path. O_NOFOLLOW refuses a symlinked FINAL component (cheap
    defence in depth). After the F1 containment work this is used ONLY for journal_root inside acquire_lock
    and release_lock, where jr_fd is not in scope; every txn-tree fsync goes through a contained fd instead.
    RESIDUAL (disclosed): a fsync makes no content mutation and discloses nothing, so following an ANCESTOR
    symlink on journal_root's path here flushes an unintended directory but can neither write off-tree nor
    leak. The co-located lock-file operations (the O_EXCL create in acquire_lock, which POSIX refuses a
    symlinked final `lock` for, and the unlink in release_lock) likewise address journal_root by absolute
    path as the lock protocol's design; fully containing that journal_root-level lock protocol beneath a
    threaded journal-root fd is a separate hardening beyond F1's txn-dir-containment scope."""
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        _close_fd_yielding(fd)


def _fsync_contained_dir(pfd, name):
    """Fix #1 (directory mode durability): fsync a just-created/recreated directory's OWN fd, so its mode
    (set via chmod) is durable before COMPLETE, not merely the parent link. Open it O_DIRECTORY|O_NOFOLLOW
    beneath its bound parent fd (a swapped-in symlink raises rather than redirecting the fsync), fsync the
    dir fd, then close it; the caller fsyncs the parent separately."""
    dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    try:
        os.fsync(dfd)
    finally:
        _close_fd_yielding(dfd)


def ensure_journal_dirs(root_fd, journal_rel):
    """J1 (journal-hierarchy crash durability): create every missing component of journal_rel beneath
    root_fd, CONTAINED (an O_DIRECTORY|O_NOFOLLOW walk, mkdirat), fsyncing each newly-created directory's
    PARENT fd immediately after creating it, so the COMPLETE journal hierarchy is durable BEFORE any INTENT
    or apply begins. Without the per-parent fsync a power loss could make an applied tree mutation durable
    (apply_ops fsyncs each touched file AND its parent) while the never-fsynced journal subtree is lost, so
    a fresh recover would find no journal and falsely report 'nothing to recover' for a transaction whose
    tree mutation had already begun. Idempotent: an already-present component is descended into, not
    re-created (its durability was established when it was made). A symlinked or non-directory component
    fails closed (JournalError), consistent with the engine's no-follow containment idiom."""
    parts = _check_rel(journal_rel)
    cur = root_fd
    opened = []
    try:
        for comp in parts:
            try:
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except FileNotFoundError:
                os.mkdir(comp, 0o777, dir_fd=cur)             # create the single missing component
                os.fsync(cur)                                 # J1: its entry durable in the PARENT first
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except OSError as exc:
                raise JournalError("cannot open/create journal component {!r} of {!r} ({})"
                                   .format(comp, journal_rel, exc))
            opened.append(nfd)
            cur = nfd
    finally:
        for fd in opened:
            _close_fd_quietly(fd)                         # guarded: a raising close never aborts the rest


def _open_dir_contained(root_fd, relpath):
    """Open the directory at relpath beneath root_fd via a component-by-component O_DIRECTORY|O_NOFOLLOW
    walk (a symlinked or non-directory component raises rather than redirecting the walk), returning a dir
    fd the caller closes; root_fd is never closed. The read-only sibling of ensure_journal_dirs' walk: it
    reaches a journal directory (the journal root) from the trusted repo-root anchor with NO re-resolved
    absolute path, so an ANCESTOR symlink cannot escape containment (SECI-symlink-resolution).
    FileNotFoundError propagates (a caller reads it as absent); any other OSError is a fail-closed
    JournalError."""
    parts = _check_rel(relpath)
    cur = root_fd
    opened = []
    try:
        for comp in parts:
            try:
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise JournalError("cannot open contained directory component {!r} of {!r} ({})"
                                   .format(comp, relpath, exc))
            opened.append(nfd)
            cur = nfd
        result = os.dup(cur)
    finally:
        for fd in opened:
            _close_fd_quietly(fd)                         # guarded: a raising close never aborts the rest
    return result


def open_journal_root_fd(root_fd, journal_rel):
    """Open the journal root beneath the TRUSTED repo-root fd via a contained no-follow walk, returning a
    dir fd the caller closes. This is the trusted ANCHOR threaded to publish/read_frames/_truncate_log
    (and to classify_state/is_terminal/recover): each reaches its txn dir by a dir-fd-relative no-follow
    open BENEATH this handle, never a re-resolved absolute path whose ancestor symlink could escape
    containment (SECI-symlink-resolution)."""
    return _open_dir_contained(root_fd, journal_rel)


def open_journal_root_from_path(root, journal_rel):
    """Convenience for a read-only caller holding a root PATH (not an fd): open the repo-root fd
    (O_NOFOLLOW on the operator-supplied root, the same trust anchor migrate._open_root_fd uses) and walk
    down to the journal root contained, returning a jr fd the caller closes. FileNotFoundError when the
    root or a journal-rel component is absent."""
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    jr_fd = None
    try:
        jr_fd = _open_dir_contained(root_fd, journal_rel)
    finally:
        # ROUND-6 defect sibling (_opf_store._open_working_dir_fd): the new jr fd is HELD in a local
        # across the root close, so a root close that reports an error cannot abandon the return and
        # leak the just-opened journal-root fd. It is released quietly, the root close is a single
        # os.close (P1: a raising close has released its number, close(2), and it is never touched
        # again), and the close error keeps propagating fail-closed.
        try:
            _close_fd_yielding(root_fd)
        except OSError:
            if jr_fd is not None:
                _close_fd_quietly(jr_fd)
            raise
    return jr_fd


def _open_txn_beneath(jr_fd, txn_dir):
    """Open the txn directory (a single path component: Path(txn_dir).name) beneath the trusted
    journal-root fd with O_DIRECTORY|O_NOFOLLOW, so a symlinked txn component fails closed AND, because
    jr_fd was reached by a contained walk, an ancestor symlink on the journal path cannot redirect the
    open off-tree (SECI-symlink-resolution). FileNotFoundError propagates (an absent txn reads as nothing
    to a reader); any other OSError is the caller's to map. Rejects a non-single-component name."""
    name = Path(txn_dir).name
    if not name or "/" in name or name in (".", ".."):
        raise JournalError("invalid journal txn name {!r}".format(name))
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jr_fd)


# --- frame layer (checksummed framing; a torn final frame is detectably unwritten) --------------------

def _frame(ftype, payload):
    digest = hashlib.sha256(payload).hexdigest()
    header = (MAGIC + b" " + ftype.encode() + b" " + str(len(payload)).encode()
              + b" " + digest.encode() + b"\n")
    return header + payload + b"\n"


def _create_frames_excl(jr_fd, txn_dir):
    """Create the txn's frames.log EXACTLY ONCE, EXCLUSIVELY, inside the freshly-made txn dir (contained
    beneath the trusted journal-root fd). O_CREAT|O_EXCL refuses ANY pre-existing entry at that name (a
    planted hardlink to a victim, or a plain regular file), so the log is provably freshly created by us
    before the first publish ever appends (codex round-6; the FIRST-creation half of the hardlink defence,
    complementing the per-open st_nlink==1 identity check on every reopen/append/read/truncate). The empty
    file is fsync'd and its dir entry made durable through the same contained fd."""
    try:
        txnfd = _open_txn_beneath(jr_fd, txn_dir)
    except OSError as exc:
        raise JournalError("cannot open journal txn dir {!r} contained no-follow ({})"
                           .format(str(txn_dir), exc))
    try:
        try:
            fd = os.open("frames.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=txnfd)
        except OSError as exc:
            raise JournalError("cannot exclusively create journal frames.log; a pre-existing entry at that "
                               "name is refused (fail-closed) ({})".format(exc))
        try:
            os.fsync(fd)
        finally:
            _close_fd_yielding(fd)
        os.fsync(txnfd)                                   # the new dir entry durable through the CONTAINED fd
    finally:
        _close_fd_yielding(txnfd)


def publish(jr_fd, txn_dir, ftype, obj):
    """Append one checksummed-framed record (9.3 steps 4 and 7 discipline), fsync the log and the txn
    directory. A torn write of THIS frame is detectably-unwritten to read_frames; the torn:<TYPE>
    injection writes a half frame, fsyncs it, and dies, exactly as a power loss mid-write would.
    Symlink-race containment: the txn dir (Path(txn_dir).name) is opened by a dir-fd-relative
    O_DIRECTORY|O_NOFOLLOW open BENEATH the trusted journal-root fd (jr_fd, itself reached by a contained
    walk from the repo root), and frames.log is opened dir-fd-relative to THAT txn fd
    (O_CREAT|O_APPEND|O_NOFOLLOW, confirmed a regular file on the opened fd). No absolute path is
    re-resolved, so neither an ANCESTOR symlink on the txn dir's path nor a symlinked frames.log can
    redirect the append onto a victim file: both fail closed (SECI-symlink-resolution). The txn dir itself
    is fsync'd through the SAME contained fd, never a re-resolved absolute path."""
    if ftype not in FRAME_TYPES:
        raise JournalError("refusing to publish unknown frame type {!r}".format(ftype))
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    frame = _frame(ftype, payload)
    torn = os.environ.get(KILL_ENV, "") == "torn:" + ftype
    try:
        txnfd = _open_txn_beneath(jr_fd, txn_dir)
    except OSError as exc:
        raise JournalError("cannot open journal txn dir {!r} contained no-follow ({})"
                           .format(str(txn_dir), exc))
    try:
        try:
            # O_NONBLOCK so a pre-planted FIFO frames.log is refused at the fstat gate below instead of
            # blocking the append open forever; a no-op for the regular file this expects. O_NOFOLLOW
            # refuses a symlinked frames.log (a swapped-in symlink cannot redirect the append off-tree).
            fd = os.open("frames.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
                         | os.O_NONBLOCK, 0o600, dir_fd=txnfd)
        except OSError as exc:
            raise JournalError("cannot open journal frames.log no-follow for append ({})".format(exc))
        try:
            _st = os.fstat(fd)
            if not stat.S_ISREG(_st.st_mode):
                raise JournalError("journal frames.log is not a regular file (no-follow append)")
            # O_NOFOLLOW refuses a SYMLINK but a HARDLINK is a regular file that passes S_ISREG, so a
            # frames.log hardlinked to an out-of-tree victim would be appended-to on the victim's inode. A
            # frames.log we created is unlinked (exactly one link); a link count above 1 means a second name
            # references this inode (a planted hardlink to a victim), so refuse it (codex round-6;
            # SECI-symlink-resolution: confirm the opened object's identity, a hardlink is not a swap-in-name
            # a symlink defence catches).
            if _st.st_nlink != 1:
                raise JournalError("journal frames.log has {} hard links; refusing to append (a hardlink "
                                   "to an out-of-tree victim, never our unlinked log)".format(_st.st_nlink))
            # A2 (cumulative append bound, defence in depth): the recovery reader caps frames.log at
            # _MAX_JOURNAL_READ_BYTES, so a publish that would carry the log past that cap produces a journal
            # recovery refuses as oversize. Refuse the append here, fail-closed, so no publish path (INTENT,
            # the terminal frames from run_transaction, or the terminal frames from recover()) can ever write
            # an unreadable journal (F-R17-A2). run_transaction's pre-mutation budget check reserves the whole
            # INTENT+rollback envelope so a legitimate transaction never trips this.
            if _st.st_size + len(frame) > _MAX_JOURNAL_READ_BYTES:
                raise JournalError("journal frames.log would reach {} bytes after a {}-byte {} frame, over "
                                   "the {}-byte journal-read cap; refusing so recovery can always re-read it "
                                   "(fail-closed)".format(_st.st_size + len(frame), len(frame), ftype,
                                                          _MAX_JOURNAL_READ_BYTES))
            if torn:
                half = frame[: max(1, len(frame) // 2)]
                _write_all(fd, half)
                os.fsync(fd)
                os._exit(137)
            _write_all(fd, frame)
            os.fsync(fd)
        finally:
            _close_fd_yielding(fd)
        os.fsync(txnfd)                                   # the txn dir durable through the CONTAINED fd
    finally:
        _close_fd_yielding(txnfd)
    _kill_point("after-publish-" + ftype)


def read_frames(jr_fd, txn_dir, txn_fd=None):
    """Parse frames.log. Returns (frames, torn, good_len): frames is [(ftype, obj)] for every checksum-
    valid frame in order; torn is True when the FINAL region is a detectably incomplete frame; good_len
    is the byte length of the clean prefix (everything before a torn tail), so recovery can truncate the
    tail before appending a terminal frame and keep the log parseable and idempotent. Any malformation
    NOT at the tail raises JournalError (9.3: a FAIL state, never silently skipped). The txn dir
    (Path(txn_dir).name) is opened by a dir-fd-relative O_DIRECTORY|O_NOFOLLOW open beneath the trusted
    journal-root fd (jr_fd), and frames.log dir-fd-relative to THAT, so no re-resolved absolute path is
    walked and an ANCESTOR symlink on the txn path fails closed (SECI-symlink-resolution). An absent txn
    dir (or absent frames.log) reads as no frames. txn_fd (round 4): a txn-dir descriptor the caller HELD
    from its enumeration (_journal_txn_dirs hold=True); when given, frames.log is read through a dup of
    THAT directory identity, never a by-name reopen beneath jr_fd, so a transaction directory swapped
    onto its name after the enumeration can neither hide the enumerated transaction's frames nor
    substitute its own (the caller's descriptor stays open; only the dup is closed here)."""
    if txn_fd is not None:
        try:
            txnfd = os.dup(txn_fd)
        except OSError as exc:
            raise JournalError("cannot dup the held journal txn descriptor for {!r} ({})"
                               .format(str(txn_dir), exc))
    else:
        try:
            txnfd = _open_txn_beneath(jr_fd, txn_dir)
        except FileNotFoundError:
            return [], False, 0
        except OSError as exc:
            raise JournalError("cannot open journal txn dir {!r} contained no-follow ({})"
                               .format(str(txn_dir), exc))
    try:
        try:
            # O_NONBLOCK so a FIFO frames.log (a hostile pre-planted tree) is refused at the fstat gate
            # below instead of blocking the open forever; a no-op for the regular file this expects.
            ffd = os.open("frames.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=txnfd)
        except FileNotFoundError:
            return [], False, 0
        except OSError as exc:
            raise JournalError("cannot read journal frames.log no-follow ({})".format(exc))
        try:
            _st = os.fstat(ffd)
            if not stat.S_ISREG(_st.st_mode):
                raise JournalError("journal frames.log is not a regular file (no-follow)")
            # A HARDLINK to an out-of-tree victim passes O_NOFOLLOW and S_ISREG; a link count above 1 means
            # another name references this inode, so refuse it rather than read a victim's bytes into the
            # frame parse (codex round-6; SECI-symlink-resolution identity confirmation).
            if _st.st_nlink != 1:
                raise JournalError("journal frames.log has {} hard links; refusing to read (a hardlink to "
                                   "an out-of-tree victim, never our unlinked log)".format(_st.st_nlink))
            # MINOR-1: pre-open-size fast-reject on the fstat already taken, then a capped read whose
            # incremental post-read re-check catches a file grown/swapped past this size (SECA
            # resource-bounds; mirrors the store cap). frames.log holds only INTENT op-list metadata.
            if _st.st_size > _MAX_JOURNAL_READ_BYTES:
                raise JournalError("journal frames.log is {} bytes, over the {}-byte journal-read cap "
                                   "(fail-closed)".format(_st.st_size, _MAX_JOURNAL_READ_BYTES))
            raw = _read_fd(ffd, cap=_MAX_JOURNAL_READ_BYTES)
        finally:
            _close_fd_yielding(ffd)
    finally:
        _close_fd_yielding(txnfd)
    frames, off = [], 0
    while off < len(raw):
        nl = raw.find(b"\n", off)
        if nl < 0:
            return frames, True, off                      # header itself torn at the tail
        parts = raw[off:nl].split(b" ")
        if len(parts) != 4 or parts[0] != MAGIC:
            raise JournalError("corrupt frame header at offset {}".format(off))
        ftype = parts[1].decode("utf-8", "replace")
        try:
            length = int(parts[2])
        except ValueError:
            raise JournalError("corrupt frame length at offset {}".format(off))
        if length < 0:
            raise JournalError("negative frame length at offset {}".format(off))
        digest = parts[3].decode("utf-8", "replace")
        body_start = nl + 1
        body = raw[body_start:body_start + length]
        end = body_start + length + 1                     # +1 for the trailing "\n"
        short = len(body) < length or end > len(raw) or raw[end - 1:end] != b"\n"
        if short or hashlib.sha256(body).hexdigest() != digest or ftype not in FRAME_TYPES:
            if end >= len(raw):
                return frames, True, off                  # torn tail: treated as never written
            raise JournalError("corrupt frame mid-log at offset {}".format(off))
        try:
            obj = json.loads(body)
        except ValueError as exc:
            raise JournalError("corrupt frame payload at offset {} ({})".format(off, exc))
        frames.append((ftype, obj))
        off = end
    return frames, False, len(raw)


def _truncate_log(jr_fd, txn_dir, good_len):
    """Cut a torn tail off frames.log so a fresh terminal frame appends onto a clean prefix. Fsync'd.
    Symlink-race containment: the txn dir (Path(txn_dir).name) is opened CONTAINED beneath the trusted
    journal-root fd (jr_fd, O_DIRECTORY|O_NOFOLLOW) and frames.log dir-fd-relative to it (confirmed a
    regular file on the opened fd), so no re-resolved absolute path is walked; a symlinked frames.log OR
    an ANCESTOR symlink on the txn path cannot redirect the ftruncate onto a victim file
    (SECI-symlink-resolution). The txn dir is fsync'd through the same contained fd."""
    try:
        txnfd = _open_txn_beneath(jr_fd, txn_dir)
    except OSError as exc:
        raise JournalError("cannot open journal txn dir {!r} contained no-follow ({})"
                           .format(str(txn_dir), exc))
    try:
        try:
            # O_NONBLOCK so a pre-planted FIFO frames.log is refused at the fstat gate rather than blocking
            # the open; a no-op for the regular file this expects. O_NOFOLLOW refuses a symlinked target.
            fd = os.open("frames.log", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=txnfd)
        except OSError as exc:
            raise JournalError("cannot open journal frames.log no-follow for truncate ({})".format(exc))
        try:
            _st = os.fstat(fd)
            if not stat.S_ISREG(_st.st_mode):
                raise JournalError("journal frames.log is not a regular file (no-follow truncate)")
            # A HARDLINK to an out-of-tree victim passes O_NOFOLLOW and S_ISREG; refusing a link count above
            # 1 stops the ftruncate from truncating a victim's inode (codex round-6; recovery is the
            # destructive path this most needs, SECI-symlink-resolution identity confirmation).
            if _st.st_nlink != 1:
                raise JournalError("journal frames.log has {} hard links; refusing to truncate (a hardlink "
                                   "to an out-of-tree victim, never our unlinked log)".format(_st.st_nlink))
            os.ftruncate(fd, good_len)
            os.fsync(fd)
        finally:
            _close_fd_yielding(fd)
        os.fsync(txnfd)                                   # the txn dir durable through the CONTAINED fd
    finally:
        _close_fd_yielding(txnfd)


def _first(frames, ftype):
    for t, obj in frames:
        if t == ftype:
            return obj
    return None


# --- lock (O_EXCL owner identity; stale-lock recovery is the caller's reconcile step) -----------------

def _pid_start(pid):
    """The process start-time field from /proc/<pid>/stat (Linux only; empty string elsewhere). Read
    from after the last ')' so a program name containing ') ' cannot shift the field split. The file is
    closed in a `finally` whose close yields to an exception in flight there (_yield_close_exceptions), so
    a close that raises never replaces an interrupt in the read; a close OSError reads as unknown, as the
    read's own does."""
    try:
        fh = open("/proc/{}/stat".format(pid), "rb")
    except OSError:
        return ""
    raw = None
    try:
        raw = fh.read()
    except OSError:
        pass
    finally:
        inflight = _in_flight_in(sys._getframe(0))
        try:
            fh.close()
        except OSError:
            raw = None
        except BaseException as exc:    # noqa: BLE001  yielded to the one in flight, or raised with none
            _yield_close_exceptions(inflight, [exc])
    if raw is None:
        return ""
    try:
        return raw.rsplit(b")", 1)[1].split()[19].decode()
    except IndexError:
        return ""


def _is_canonical_pid_start(value):
    """True ONLY for a value that is EXACTLY what _pid_start emits for a Linux process: the /proc starttime
    field, which the kernel prints as an unsigned long long (%llu). That canonical form is a string of ASCII
    digits, no sign, no leading zero (except the single digit "0"), whose integer value lies in the valid
    unsigned range 0 <= v < 2**64. An overlong value (e.g. "9"*1000), an out-of-range value, a whitespace-
    padded or non-decimal value, or a leading-zero value is NOT canonical: _pid_start could never have
    written it, so it must never be trusted as a genuine start time and read as a differing (dead) one
    (spec 1262 to 1265, possibly-live is never seized). The empty string (the disclosed non-Linux/unreadable
    case) is handled by the caller, not here: this returns False for it."""
    if not isinstance(value, str) or not _PIDSTART_RE.match(value):
        return False
    if len(value) > _PIDSTART_MAX_DIGITS:                 # reject an overlong value BEFORE int(): an all-digit string
        return False                                      # past CPython's ~4300-digit conversion limit raises ValueError
    try:                                                  # (_pid_start could never emit such a value; fail-closed)
        v = int(value)
    except (ValueError, MemoryError):                     # not canonical: an unconvertible value is never trusted as a
        return False                                      # genuine start time (defence in depth behind the digit cap)
    return v < _PIDSTART_MAX and str(v) == value          # in range AND canonical (str(int(...)) rejects leading zeros)


def acquire_lock(journal_root, session_id):
    """O_CREAT|O_EXCL lock with owner identity (9.3 step 2). Raises JournalError (mapped to a refuse-to-
    proceed) when a lock already exists: one open transaction at a time; a possibly-live owner is never
    seized here (breaking a stale lock is the caller's explicit reconcile step in recover). The O_EXCL
    create is itself the mutual-exclusion point and refuses to follow a final-component symlink. An OSError
    raised AFTER that create carries lock_created = True, so a caller tells a lock THIS call created (and
    may release) from one it did not, which process identity alone cannot (two threads of one process)."""
    journal_root = Path(journal_root)
    lock = journal_root / "lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise JournalError("journal lock {} already held: an open transaction exists (run recover)"
                           .format(lock))
    try:
        try:
            owner = {"uid": os.getuid(), "pid": os.getpid(), "session": session_id,
                     "pid-start": _pid_start(os.getpid()),
                     "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            _write_all(fd, json.dumps(owner, sort_keys=True).encode())   # loop: a short write cannot leave a malformed lock
            os.fsync(fd)
        finally:
            _close_fd_yielding(fd)
        _fsync_path_dir(journal_root)
    except OSError as exc:
        exc.lock_created = True
        raise
    _kill_point("after-lock")
    return lock


def read_lock_owner(journal_root):
    """The recorded owner dict, or None when no lock file is present. JournalError on an unreadable or
    malformed lock (fail-closed: an unreadable lock is never treated as absent). HARDENING: the decoded
    JSON MUST be an object, so owner_confirmed_dead / _owner_is_current can call .get without a non-dict
    (a bare list/int/string) reaching them as an uncaught AttributeError. The lock is opened beneath the
    journal-dir handle with O_NOFOLLOW and confirmed a regular file on the opened fd (mirror B1), so a
    symlinked or non-regular `lock` fails closed rather than redirecting the read off-tree. The journal dir
    itself is opened here by PATH; a caller already holding a contained journal-root descriptor reads
    through read_lock_owner_at instead, so the journal path is never re-resolved after that open."""
    journal_root = Path(journal_root)
    try:
        jr_fd = os.open(str(journal_root), os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise JournalError("cannot open journal root ({})".format(exc))
    try:
        return read_lock_owner_at(jr_fd)
    finally:
        _close_fd_yielding(jr_fd)


def read_lock_owner_at(jr_fd):
    """read_lock_owner bound to an already-open journal-root descriptor (jr_fd, the caller's, never closed
    here): `lock` is opened beneath jr_fd, never by re-resolving a journal PATH, so a journal path swapped
    for a symlink after the caller's contained open can neither hide the held lock nor substitute an
    out-of-tree one. Same contract as read_lock_owner: None when no lock file is present, JournalError on
    an unreadable, non-regular, multiply-linked, oversize or malformed lock."""
    try:
        # O_NONBLOCK so a FIFO lock (a hostile pre-planted tree) is refused at the fstat gate below
        # instead of blocking the open forever; a no-op for the regular file this expects.
        lfd = os.open("lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=jr_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:                                # ELOOP on a symlinked lock, or any read error: fail closed
        raise JournalError("cannot read journal lock ({})".format(exc))
    try:
        _st = os.fstat(lfd)
        if not stat.S_ISREG(_st.st_mode):
            raise JournalError("journal lock is not a regular file (fail-closed)")
        # O_NOFOLLOW refuses a SYMLINK but a HARDLINK is a regular file that passes S_ISREG, so a `lock`
        # hardlinked to an out-of-tree victim would be READ through the victim's inode. A lock acquire_lock
        # created is singly-linked (O_CREAT|O_EXCL, exactly one name); a link count above 1 means a second
        # name references this inode (a planted hardlink), so refuse it fail-closed, class-consistent with
        # the other journal opens' st_nlink==1 identity guards (frames.log reopen/append/read/truncate,
        # the product-file prestate checks, and the lock.break arbitration inode). Round-12 F4: this closes
        # read_lock_owner, the last unguarded acceptor of a hardlinked inode in the journal.
        if _st.st_nlink != 1:
            raise JournalError("journal lock has {} hard links; refusing to read (a hardlink to an "
                               "out-of-tree victim, never our singly-linked lock)".format(_st.st_nlink))
        # MINOR-1: the same journal-read cap bounds the lock (a small owner record); an oversize plant
        # is refused fail-closed rather than slurped (SECA resource-bounds; pre-open-size fast-reject
        # plus the capped read's incremental post-read re-check).
        if _st.st_size > _MAX_JOURNAL_READ_BYTES:
            raise JournalError("journal lock is {} bytes, over the {}-byte journal-read cap "
                               "(fail-closed)".format(_st.st_size, _MAX_JOURNAL_READ_BYTES))
        raw = _read_fd(lfd, cap=_MAX_JOURNAL_READ_BYTES)
    finally:
        _close_fd_yielding(lfd)
    try:
        owner = json.loads(raw)
    except ValueError as exc:
        raise JournalError("journal lock is not valid JSON ({})".format(exc))
    if not isinstance(owner, dict):
        raise JournalError("journal lock JSON is not an object (fail-closed)")
    _validate_owner_schema(owner)
    return owner


def _validate_owner_schema(owner):
    """Validate the FULL lock-owner identity schema BEFORE any liveness evaluation (fail-closed). Spec 1262
    requires the owner identity to carry a UID, a PID, a session id, and a UTC stamp (exactly the four fields
    acquire_lock writes alongside pid-start), so EVERY one is validated here; a valid JSON object can still
    carry a boolean or non-int `pid`, a boolean or negative `uid`, a `pid-start` that is a non-string OR a
    malformed/overlong string, or a missing/empty `session` or `utc`, and any malformed field must never let
    a LIVE owner read as confirmed-dead and be seized (possibly-live-never-seized, spec 1262 to 1265). bool
    is an int subclass, so it is excluded explicitly. `pid-start` must be EITHER empty (the disclosed
    non-Linux/unreadable case _pid_start returns) OR a CANONICAL /proc decimal start-time (ASCII digits, in
    the unsigned range 0 <= v < 2**64, no leading zeros: exactly what _pid_start writes, see
    _is_canonical_pid_start); an overlong ("9"*1000), out-of-range, whitespace-padded, leading-zero, or
    otherwise non-canonical value is rejected, so it can never be mistaken for a genuine start time and read
    as a differing (dead) one."""
    pid = owner.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise JournalError("journal lock pid is not a positive integer (fail-closed)")
    uid = owner.get("uid")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise JournalError("journal lock uid is not a non-negative integer (fail-closed)")
    pid_start = owner.get("pid-start")
    if not isinstance(pid_start, str):
        raise JournalError("journal lock pid-start is not a string (fail-closed)")
    if pid_start != "" and not _is_canonical_pid_start(pid_start):
        raise JournalError("journal lock pid-start is not empty or a canonical /proc decimal start time "
                           "(fail-closed)")
    session = owner.get("session")
    if not isinstance(session, str) or not session:
        raise JournalError("journal lock session is not a non-empty string (fail-closed)")
    utc = owner.get("utc")
    if not isinstance(utc, str) or not utc:
        raise JournalError("journal lock utc is not a non-empty string (fail-closed)")


def owner_confirmed_dead(owner):
    """True ONLY on positive evidence of death. EPERM, a live pid, an out-of-range pid that overflows
    pid_t at os.kill (OverflowError), or any ambiguity reads as possibly-live (never seized). Where a start
    time was recorded and is readable, a differing start time for the
    same pid also confirms death (PID reuse). Residual: PID reuse on a platform with no readable start
    time cannot be distinguished, so it reads as possibly-live and the lock is not broken. The FULL owner
    schema is validated defensively FIRST, so ANY malformed or ambiguous identity field (a boolean/non-int/
    non-positive pid, a bad uid, a pid-start that is not empty and not a canonical /proc start time, or a
    missing/empty session or utc) reads as possibly-live here too: recovery can never seize a live owner
    behind a malformed lock, whose garbage or overlong pid-start would otherwise differ from the real start
    time and read as dead (defence in depth: read_lock_owner already rejects such a lock)."""
    if not isinstance(owner, dict):
        return False                                      # non-dict: possibly-live, never seized
    try:
        _validate_owner_schema(owner)                     # any malformed identity field reads possibly-live
    except JournalError:
        return False
    pid = owner.get("pid")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True                                       # positive evidence of death: the pid no longer exists
    except Exception:                                     # EPERM (a live foreign owner), OverflowError (a pid too large
        return False                                      # for pid_t), or any other error: possibly-live, never seized
    recorded = owner.get("pid-start")                     # validated: empty, or a canonical /proc start time
    if recorded:
        now = _pid_start(pid)
        if not _is_canonical_pid_start(now):              # current start empty/unreadable/malformed: possibly-live
            return False
        return now != recorded                            # both canonical: a differing start time confirms PID reuse
    return False


def _owner_is_current(owner):
    """True when the recorded lock owner identifies THIS running process: same uid and pid, and where a
    /proc start-time was recorded and is readable, the same start-time (so a reused pid cannot
    masquerade as the owner). The identity fields are exactly those acquire_lock writes."""
    if not isinstance(owner, dict):
        return False
    if owner.get("uid") != os.getuid() or owner.get("pid") != os.getpid():
        return False
    recorded = owner.get("pid-start")
    if recorded:
        return recorded == _pid_start(os.getpid())
    return True


def release_lock(journal_root):
    """Release the journal lock ONLY when it identifies THIS process (ownership-checked, 9.3 step 1): a
    lock owned by another process is NEVER unlinked, so a foreign live lock is never deleted (the
    concurrency-lease fail-safe). An absent lock is a clean no-op; an unreadable or malformed lock is left
    in place (fail-closed, never blind-unlinked). Breaking a confirmed-dead stale lock is the caller's
    explicit reconcile step (break_stale_and_acquire), never this release path."""
    journal_root = Path(journal_root)
    lock = journal_root / "lock"
    try:
        owner = read_lock_owner(journal_root)
    except JournalError:
        return                                            # unreadable/malformed: never blind-unlink
    if owner is None:
        return                                            # already absent
    if not _owner_is_current(owner):
        return                                            # foreign lock: never delete a lock we do not own
    try:
        os.unlink(str(lock))
    except FileNotFoundError:
        return
    _fsync_path_dir(journal_root)


def _journal_txn_dirs(jr_fd, journal_root, strict=False, hold=False):
    """The transaction subdirectories of a journal root, sorted (the reconcile order). Skips the lock and
    arbitration files and any stray non-directory entry. A symlinked entry is REFUSED (JournalError), not
    followed or silently skipped, class-consistent with doctor.assert_open_journal and migrate._txn_dirs so
    a symlinked/dangling txn entry cannot slip through the stale-lock reconcile as 'all terminal'
    (SECI-symlink-resolution; F-R17-C1 sibling). strict=True (a read-only state report) also REFUSES any
    wrong-type entry instead of skipping it: only a directory, or a REGULAR, SINGLY-LINKED `lock` /
    `lock.break` (the only non-directory names this engine creates in a journal root, each created with
    exactly one link), is accepted -- a multiply-linked `lock` or `lock.break` is a second name for a
    foreign inode (a planted hardlink), refused fail-closed class-consistent with the journal's other
    nlink==1 identity guards (round 4). hold=True: each transaction directory is ALSO opened
    O_DIRECTORY|O_NOFOLLOW beneath jr_fd AT enumeration and (path, fd) pairs are returned in place of
    bare paths, so the caller classifies and reads frames through the SAME held directory identity this
    listing produced (round 4: a transaction directory swapped onto its name after the enumeration is
    never reopened by name); the caller closes every returned fd (on this function's own error paths
    they are closed here).

    F-R18-JTOCTOU: enumerate and classify FD-RELATIVE to the TRUSTED, already-open journal-root descriptor
    (os.scandir(jr_fd), os.lstat(name, dir_fd=jr_fd)), never by re-resolving the journal PATH. A path-based
    Path(journal_root).iterdir() FOLLOWS the journal path at enumeration time, so a swapped journal ANCESTOR
    (a symlink to an empty decoy) planted between the open and the listing would report false-clean (an open
    txn is missed and the caller reads the journal as 'all terminal'). Binding the enumeration to jr_fd keeps
    it on the same directory identity every other journal op is bound to; `journal_root` is used only to build
    the returned entry paths whose basenames the contained per-txn opens resolve beneath jr_fd.
    F-R18-OSESC: a listing or entry-stat OSError (e.g. EIO) is wrapped in a contextual JournalError
    (fail-closed), never left to escape as a raw OSError (CLI exit 1 + traceback); the CLI maps it to exit 2,
    _all_terminal to False, and _latest_txn to its documented failure."""
    out = []
    try:
        try:
            with os.scandir(jr_fd) as it:
                names = sorted(e.name for e in it)
        except OSError as exc:
            raise JournalError("cannot list journal dir contained ({}); fail-closed".format(exc))
        for name in names:
            try:
                est = os.lstat(name, dir_fd=jr_fd)
            except OSError as exc:
                raise JournalError("cannot stat journal entry {!r} contained ({}); "
                                   "fail-closed".format(name, exc))
            if stat.S_ISLNK(est.st_mode):
                raise JournalError("a symlinked journal entry {!r} is refused, not followed "
                                   "(fail-closed)".format(name))
            if strict and name in ("lock", "lock.break"):
                if not stat.S_ISREG(est.st_mode):
                    raise JournalError("journal entry {!r} is not a regular file (a wrong-type entry is "
                                       "refused, never skipped; fail-closed)".format(name))
                if est.st_nlink != 1:
                    raise JournalError("journal entry {!r} has {} hard links; refusing a multiply-linked "
                                       "lock or arbitration file (a hardlink to an out-of-tree victim, "
                                       "never this engine's singly-linked file; fail-closed)".format(
                                           name, est.st_nlink))
                continue
            if stat.S_ISDIR(est.st_mode):
                if hold:
                    # Round 7 MINOR: build the returned entry path BEFORE the open, so a path
                    # construction that raises (a malformed journal_root) can never strand a
                    # just-opened txn descriptor outside `out`, where the except-cleanup below
                    # cannot close it.
                    entry = Path(journal_root) / name
                    try:
                        tfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jr_fd)
                    except OSError as exc:
                        raise JournalError("cannot open journal txn dir {!r} contained no-follow ({}); "
                                           "fail-closed".format(name, exc))
                    out.append((entry, tfd))
                else:
                    out.append(Path(journal_root) / name)
            elif strict:
                raise JournalError("journal entry {!r} is neither a transaction directory nor a regular "
                                   "lock or arbitration file (a wrong-type entry is refused, never "
                                   "skipped; fail-closed)".format(name))
    except BaseException:
        if hold:
            for _path, tfd in out:
                _close_fd_quietly(tfd)
        raise
    return out


def reconcile_and_claim_stale(journal_root, jr_fd, root_fd, session_id):
    """E4 (spec 1262): a confirmed-dead stale lock is broken ONLY after (a) its recorded owner is confirmed
    dead AND (b) the journal is reconciled to a consistent (terminal) state. Under a kernel advisory lock on
    a STABLE, never-replaced arbitration file (<journal>/lock.break) that serializes recoverers (C1), RE-READ
    the CURRENT owner: if it is still a confirmed-dead stale lock, RECONCILE every transaction to terminal
    via recover() WHILE RETAINING the stale lease record, VALIDATE that every journal is terminal (the C2
    classifier), and ONLY THEN remove the stale lock and acquire a fresh O_EXCL lock. The stale lease is thus
    the recovery claim held across reconciliation: a crash or a journal that does not reconcile leaves the
    stale lock in place for the next recoverer rather than an unlocked, half-reconciled tree. A lock a
    concurrent recoverer already re-acquired (now live) is left untouched and reported 'possibly-live'.
    Returns 'acquired' or 'possibly-live'; JournalError on a lost O_EXCL acquire, an unreadable current lock,
    or a journal that does not reconcile to terminal (fail-closed: the stale lock is NOT broken).

    PORTABILITY: fcntl is POSIX-only. On a platform without it (e.g. Windows) this FAILS CLOSED, refusing
    to break the stale lock rather than falling back to the unguarded race, consistent with the engine's
    existing non-Linux fail-safe posture (3.6b). Both supported platforms (Linux, macOS) expose fcntl."""
    try:
        import fcntl
    except ImportError:
        raise JournalError("stale-lock break needs the POSIX fcntl arbitration primitive (absent on this "
                           "platform): refusing to break the stale lock (fails closed, 3.6b)")
    journal_root = Path(journal_root)
    arb = journal_root / "lock.break"
    try:                                                     # STABLE arbitration file, never replaced
        afd = os.open(str(arb), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)   # O_NOFOLLOW: refuse a symlink
    except OSError as exc:
        raise JournalError("cannot open arbitration file {} ({}); refusing to break the stale lock "
                           "(fails closed)".format(arb, exc))
    try:
        _ast = os.fstat(afd)
        if not stat.S_ISREG(_ast.st_mode):                   # trust it as the arbitration inode only if regular
            raise JournalError("arbitration file {} is not a regular file; refusing to break the stale "
                               "lock (fails closed)".format(arb))
        # A MULTIPLY-LINKED arbitration inode (st_nlink > 1) is a second name for the same inode, so a
        # foreign flock holder on that other name can block this LOCK_EX indefinitely and wedge stale-lock
        # recovery; a legitimate, singly-created lock.break has exactly one link. Refuse it, class-consistent
        # with the journal nlink==1 identity guards on every other inode open (frames.log
        # reopen/append/read/truncate, the product-file prestate checks, and read_lock_owner's own `lock`
        # open), so no unguarded acceptor of a hardlinked inode remains in the journal (round-10 F3, extended
        # by round-12 F4 which closed read_lock_owner; fails closed, never breaks a hardlinked lock).
        if _ast.st_nlink != 1:
            raise JournalError("arbitration file {} has {} hard links (expected exactly 1); a multiply-linked "
                               "arbitration inode lets a foreign flock holder block stale-lock recovery, so we "
                               "refuse to break the stale lock (fails closed)".format(arb, _ast.st_nlink))
        fcntl.flock(afd, fcntl.LOCK_EX)
        try:
            current = read_lock_owner(journal_root)          # RE-READ the CURRENT owner under the lock
            if current is None:
                acquire_lock(journal_root, session_id)
                return "acquired"
            if not owner_confirmed_dead(current):
                return "possibly-live"                       # a concurrent recoverer re-acquired: never break
            # (b) reconcile every transaction to terminal BEFORE breaking the stale lock (spec 1262), the
            # stale lease RETAINED throughout so a crash mid-reconcile leaves the stale lock in place.
            for txn_dir in _journal_txn_dirs(jr_fd, journal_root):
                recover(jr_fd, txn_dir, root_fd)
            for txn_dir in _journal_txn_dirs(jr_fd, journal_root):
                if not is_terminal(jr_fd, txn_dir):
                    raise JournalError("journal {} did not reconcile to terminal; refusing to break the "
                                       "stale lock (fail-closed)".format(txn_dir.name))
            try:                                             # every journal terminal: NOW break the stale lock
                os.unlink(str(journal_root / "lock"))
            except FileNotFoundError:
                pass
            os.fsync(jr_fd)                                  # F1: durability flush through the CONTAINED jr fd
            acquire_lock(journal_root, session_id)           # O_EXCL under the arbitration lock
            return "acquired"
        finally:
            fcntl.flock(afd, fcntl.LOCK_UN)
    finally:
        _close_fd_yielding(afd)


# --- preimages (durably FIRST; the whole reversal is reconstructable from them alone) -----------------

def capture_preimages(parent_fd, txn_dir, root_fd, ops):
    """9.3 step 3: full prior bytes plus metadata, durably, FIRST. Mutates each op in place, adding its
    prestate. Creates/mkdirs record an explicit PRIOR-ABSENCE and REFUSE if the path already exists;
    directory removals record existence and mode with no payload; every other touched file's full prior
    bytes are copied to preimages/<seq> and fsync'd. Fsync the preimages dir and the txn dir at the end
    so the whole reversal is durable before INTENT opens the transaction.

    F1 containment: the (already-created) txn directory (Path(txn_dir).name) is reached CONTAINED beneath
    the trusted parent_fd (the journal-root fd for a cutover from run_transaction, the pin-preimages dir fd
    for a pin) by an O_DIRECTORY|O_NOFOLLOW open; its preimages/ subdir is created and opened dir-fd-relative
    to that txn fd (a symlinked/non-directory preimages fails closed), every payload is written through a
    dir-fd-relative O_NOFOLLOW open beneath the preimages fd, and BOTH the preimages dir and the txn dir are
    fsync'd through those contained fds. No absolute path on the journal tree is re-resolved, so an ANCESTOR
    symlink on the txn dir's path cannot redirect the preimage store or the durability fsyncs off-tree
    (SECI-symlink-resolution). The product-file preimage READS stay contained beneath root_fd as before.

    Contract limit (disclosed, not silent): a preimage is captured by _read_contained, which bounds a
    single product file at _MAX_PRODUCT_READ_BYTES (16 MiB) since no expected size is yet known. A touched
    file larger than that ceiling is REFUSED fail-closed with a JournalError naming the cap, never
    truncated or slurped unbounded (SECA resource-bounds / disclose-guard-residuals). Once captured, the
    recorded size governs the apply-time re-read, so a valid preimage at its captured size is never later
    refused by this ceiling (F2)."""
    # E2: a REVERSE (un-adopt) transaction pins, per inverse op, the SOURCE poststate it expects to still
    # hold. Verify every pin here, at capture, so a non-engine write that landed after the caller's drift
    # check but before capture is caught (fail closed) rather than captured-and-clobbered (spec 1319/1323:
    # quiescence excludes the post-check/pre-replay race; this makes the drift check contiguous with the
    # capture under the held lock). Cutover ops carry no pin, so this is inert for them.
    for op in ops:
        exp = op.get("source-poststate")
        if exp is not None:
            _verify_source_poststate(root_fd, op["path"], exp)
    try:
        txnfd = _open_txn_beneath(parent_fd, txn_dir)
    except OSError as exc:
        raise JournalError("cannot open journal txn dir {!r} contained no-follow ({})"
                           .format(str(txn_dir), exc))
    try:
        # preimages/ created + opened CONTAINED beneath the txn fd (idempotent: an existing dir is reused);
        # a symlinked or non-directory preimages fails closed rather than redirecting the payload store.
        try:
            os.mkdir("preimages", 0o777, dir_fd=txnfd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise JournalError("cannot create contained preimages dir ({})".format(exc))
        try:
            prefd = os.open("preimages", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=txnfd)
        except OSError as exc:
            raise JournalError("cannot open contained preimages dir no-follow ({})".format(exc))
        try:
            for seq, op in enumerate(ops):
                kind = op["op"]
                st = _lstat_contained(root_fd, op["path"])
                if kind in ("create", "mkdir"):
                    if st is not None:
                        raise JournalError("{}: expected absent before {} (prestate violation)"
                                           .format(op["path"], kind))
                    op["prestate"] = {"kind": "absent"}
                elif kind == "rmdir":
                    if st is None or not stat.S_ISDIR(st.st_mode):
                        raise JournalError("{}: expected an existing directory before rmdir"
                                           .format(op["path"]))
                    op["prestate"] = {"kind": "dir", "mode": stat.S_IMODE(st.st_mode)}
                else:                                     # write, remove: file prestate with payload
                    if st is None or not stat.S_ISREG(st.st_mode):
                        raise JournalError("{}: expected an existing regular file before {}"
                                           .format(op["path"], kind))
                    data, _fst = _read_contained(root_fd, op["path"])
                    if kind == "write" and _fst.st_nlink != 1:
                        # DEFENCE IN DEPTH (codex round-8): a `write` op mutates the product file in place at
                        # apply/restore, so a multiply-linked target would corrupt an out-of-tree victim through
                        # the shared inode. The authoritative refusal is on the OPENED fd at apply
                        # (_verify_fd_prestate) and at restore, but reject a hard-linked write target HERE too
                        # (on the contained-read fstat) so a hard-linked product file never even enters the
                        # transaction. A `remove` unlinks its own name only (the victim keeps its content), so
                        # a link count above 1 is not a mutation hazard there and is not refused.
                        raise JournalError("{}: product file has {} hard links (>1); a `write` op refuses a "
                                           "multiply-linked target (a second name would be mutated through the "
                                           "shared inode)".format(op["path"], _fst.st_nlink))
                    ref = str(seq)
                    # payload written dir-fd-relative to the CONTAINED preimages fd, EXCLUSIVELY created
                    # (O_EXCL, no O_TRUNC): a pre-planted FIFO or a hard link to a victim regular file at the
                    # slot is REFUSED (the open fails closed) rather than being truncated and overwritten
                    # through the shared inode. O_NOFOLLOW refuses a symlinked slot; O_NONBLOCK so a raced
                    # non-regular final component returns at once instead of blocking the open forever; the
                    # fstat confirms the opened object is the regular file we just created. The slot is fresh
                    # per transaction (the txn dir is created with a collision-refusing mkdir), so O_EXCL
                    # never trips on a legitimate re-run (SECI-symlink-resolution; fail closed).
                    try:
                        pfd = os.open(ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                      | os.O_NONBLOCK, 0o600, dir_fd=prefd)
                    except OSError as exc:
                        raise JournalError("cannot exclusively create contained preimage payload {!r} ({})"
                                           .format(ref, exc))
                    try:
                        if not stat.S_ISREG(os.fstat(pfd).st_mode):
                            raise JournalError("contained preimage payload {!r} is not a regular file after "
                                               "exclusive create (fail-closed)".format(ref))
                        _write_all(pfd, data)
                        os.fsync(pfd)
                    finally:
                        _close_fd_yielding(pfd)
                    op["prestate"] = {"kind": "file", "mode": stat.S_IMODE(st.st_mode),
                                      "size": len(data), "payload": ref,
                                      "sha256": hashlib.sha256(data).hexdigest()}
                _kill_point("after-preimage-{}".format(seq))
            os.fsync(prefd)                               # the preimages dir durable, CONTAINED
        finally:
            _close_fd_yielding(prefd)
        os.fsync(txnfd)                                   # the txn dir durable, CONTAINED
    finally:
        _close_fd_yielding(txnfd)
    _kill_point("after-preimages")


# --- prestate verification and post-state verification (domain separated per kind) --------------------

def _verify_fd_prestate(fd, prestate, where):
    """Verify a write/remove op's apply-time prestate on an ALREADY-OPEN fd against the recorded preimage.
    The read is bounded by the KNOWN prestate size, not the generic product-file ceiling: a fast-reject on
    st_size catches a grown/shrunk/tampered file cleanly (an integrity change), and reading capped at the
    recorded size means a file GROWN between capture and apply is refused fail-closed AT that size rather
    than slurped whole into memory (OOM safety), while a VALID file at its expected size is NEVER refused,
    however large it is (it was captured at that size). This is the F2/F3 reconciliation with the generic
    16 MiB product-read ceiling: the ceiling bounds capture where no size is yet known; verification uses
    the recorded size and so never re-imposes an unrelated memory cap on a legitimate large preimage."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise JournalError("{}: target is not a regular file at apply time".format(where))
    if st.st_nlink != 1:
        # A product file with a link count above 1 shares its inode with another name (an out-of-tree
        # victim, or a second in-tree name): the ftruncate+rewrite below would mutate that other name
        # THROUGH the shared inode. Refuse fail-closed on the OPENED fd (the race-safe measurement, not a
        # pre-open lstat) BEFORE any truncation, exactly as the frames.log nlink==1 identity check does for
        # the journal log (the PRODUCT-FILE sibling of that defence; SECI-symlink-resolution / codex round-8).
        raise JournalError("{}: product file has {} hard links (>1); refusing to truncate/write a multiply-"
                           "linked file (a second name would mutate an out-of-tree victim through the shared "
                           "inode)".format(where, st.st_nlink))
    if stat.S_IMODE(st.st_mode) != prestate["mode"]:
        raise JournalError("{}: mode changed since preimage capture".format(where))
    if st.st_size != prestate["size"]:
        raise JournalError("{}: size changed since preimage capture".format(where))
    data = _read_fd(fd, cap=prestate["size"])
    if hashlib.sha256(data).hexdigest() != prestate["sha256"]:
        raise JournalError("{}: content changed since preimage capture".format(where))


def _verify_prestate_at(pfd, name, relpath, prestate):
    """E1 (spec 1291/1300): verify a lookup-based prestate (a dir for rmdir, a regular file for remove)
    through the SAME already-open parent fd the mutation will use, by bare 'name' with no-follow, so an
    ancestor swap between the check and the unlinkat/rmdir cannot redirect either. Supersedes a re-walk
    from root_fd, which bound the check to a freshly-resolved parent rather than the one bound at apply.
    A file prestate is verified against the KNOWN recorded size (a fast-reject on a changed size, then a
    read capped at that size), the same F2/F3 discipline as _verify_fd_prestate, so a legitimate large
    preimage is never refused by the generic product-file ceiling while a grown file fails closed."""
    st = _lstat_at(pfd, name)
    if prestate["kind"] == "dir":
        if st is None or not stat.S_ISDIR(st.st_mode):
            raise JournalError("{}: expected a directory at apply time".format(relpath))
        if stat.S_IMODE(st.st_mode) != prestate["mode"]:
            raise JournalError("{}: directory mode changed since preimage capture".format(relpath))
        return
    if st is None or not stat.S_ISREG(st.st_mode):
        raise JournalError("{}: expected a regular file at apply time".format(relpath))
    if stat.S_IMODE(st.st_mode) != prestate["mode"]:
        raise JournalError("{}: mode changed since preimage capture".format(relpath))
    if st.st_size != prestate["size"]:
        raise JournalError("{}: size changed since preimage capture".format(relpath))
    data, _ = _read_at(pfd, name, relpath, cap=prestate["size"])
    if hashlib.sha256(data).hexdigest() != prestate["sha256"]:
        raise JournalError("{}: content changed since preimage capture".format(relpath))


def _poststate_verifies(root_fd, op):
    """Domain-separated post-state check per op kind (file: exists, regular, mode, content digest; dir:
    exists, directory, mode, NO digest; removed: absent). Used ONLY by the roll-forward election, which
    fires solely when EVERY op already verifies. Never raises: a lookup error reads as does-not-verify."""
    try:
        post = op["poststate"]
        st = _lstat_contained(root_fd, op["path"])
        if post["kind"] == "absent":
            return st is None
        if post["kind"] == "dir":
            return st is not None and stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == post["mode"]
        if post["kind"] == "file":
            if st is None or not stat.S_ISREG(st.st_mode):
                return False
            expected_mode = post["mode"] if op["op"] == "create" else op["prestate"]["mode"]
            if stat.S_IMODE(st.st_mode) != expected_mode:
                return False
            data, _ = _read_contained(root_fd, op["path"])
            return hashlib.sha256(data).hexdigest() == post["content-sha256"]
    except (JournalError, OSError, KeyError):
        return False
    return False


def _verify_source_poststate(root_fd, relpath, exp):
    """E2 (spec 1319/1323): fail closed unless the effective tree STILL holds the source transaction's
    poststate for one reversed path (a file's mode+content, a directory's mode, or an absence). Called at
    un-adopt CAPTURE so the drift check is contiguous with the reverse capture under the held migration lock
    and proven quiescence, closing the post-check/pre-replay window: a non-engine write that lands after the
    caller's drift check but before capture is caught here rather than captured-and-clobbered."""
    st = _lstat_contained(root_fd, relpath)
    kind = exp["kind"]
    if kind == "absent":
        if st is not None:
            raise JournalError("{}: reversed path is no longer absent (tree drifted from the source "
                               "poststate since the cutover)".format(relpath))
        return
    if kind == "dir":
        if st is None or not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != exp["mode"]:
            raise JournalError("{}: reversed directory drifted from the source poststate".format(relpath))
        return
    if st is None or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != exp["mode"]:
        raise JournalError("{}: reversed file drifted from the source poststate".format(relpath))
    data, _ = _read_contained(root_fd, relpath)
    if hashlib.sha256(data).hexdigest() != exp["sha256"]:
        raise JournalError("{}: reversed file content drifted from the source poststate".format(relpath))


# --- apply (contained, fd-bound) ----------------------------------------------------------------------

def _verify_staged_digest(op, data):
    """C3: hash the staged write's bytes and compare to the op's recorded poststate content-sha256 from
    the INTENT, at mutation time. A mismatch means the staged tree changed after planning; raise
    JournalError so the transaction rolls back rather than installing bytes the INTENT never described.
    HARDENING: a write/create op MUST carry a 64-hex LOWERCASE content-sha256; a missing, non-string, or
    malformed (uppercase / not 64-hex) expected digest is itself a JournalError, never a silent skip that
    would let an undescribed payload install."""
    expected = op.get("poststate", {}).get("content-sha256")
    if not (isinstance(expected, str) and _HEX64_RE.fullmatch(expected)):
        raise JournalError("{}: poststate content-sha256 must be a 64-hex lowercase digest (absent or "
                           "malformed; the staged-digest check is never silently skipped)".format(op["path"]))
    if hashlib.sha256(data).hexdigest() != expected:
        raise JournalError("{}: staged payload bytes changed since planning (digest does not match the "
                           "INTENT poststate content-sha256)".format(op["path"]))


def _read_back_verify(fd, expected_sha, path, what):
    """Spec 14.2 verification checkpoint: after fsync, RE-READ the bytes just written THROUGH THE KERNEL
    from the SAME still-open descriptor (never a re-resolved path) and digest-verify them against the
    recorded expectation BEFORE the sequence moves on, so an archived copy is proven written before its
    source removal runs, and a rollback's restored live bytes are proven before the aborted run's copy is
    discarded or a prestate is reported. The re-read is the kernel's view of the file for this same
    descriptor, so it deterministically catches THIS PROCESS'S OWN write-path faults (wrong, short, or
    torn bytes handed to the kernel: a mismatch raises JournalError and fails closed); verification of
    the physical medium below the syscall boundary is OUT OF SCOPE (no portable userspace re-read can
    bypass the kernel's cache)."""
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            break
        digest.update(chunk)
    if digest.hexdigest() != expected_sha:
        raise JournalError("{}: the {} bytes re-read through the kernel from the same descriptor do "
                           "not match their recorded digest (verification checkpoint, spec 14.2: the "
                           "process's own write path handed the kernel different bytes); failing "
                           "closed".format(path, what))


def apply_ops(root_fd, ops, staged_reader):
    """9.3 step 5: fd-bound prestate check and mutation beneath the pre-opened directory handle, no-
    follow, by final component; never a re-resolved absolute path between check and write. ops are in
    dependency order (parents before children for creates, children before parents for removes), so
    reversed(ops) is the normative reverse-dependency rollback order. Every write is fsync'd, RE-READ
    through the kernel from the same descriptor and digest-verified against its INTENT poststate before
    the next op runs (the spec 14.2 verification checkpoint: a removal paired with an archive copy runs
    only after that copy's written bytes verified), and every touched entry's parent directory is
    fsync'd (step 6). Any prestate
    mismatch raises JournalError and the caller rolls back from the preimages."""
    for i, op in enumerate(ops):
        try:
            pfd, name = _open_parent(root_fd, op["path"])
        except OSError as exc:                             # includes FileNotFoundError
            raise JournalError("cannot open parent of {!r} at apply time ({})".format(op["path"], exc))
        try:
            kind = op["op"]
            if kind == "write":
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=pfd)
                try:
                    _verify_fd_prestate(fd, op["prestate"], op["path"])
                    data = staged_reader(op)
                    _verify_staged_digest(op, data)          # C3: staged bytes must match the INTENT digest
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    _maybe_torn_payload(fd, data, i)
                    _write_all(fd, data)
                    os.fsync(fd)
                    _read_back_verify(fd, op["poststate"]["content-sha256"], op["path"], "written")
                finally:
                    _close_fd_yielding(fd)
            elif kind == "create":
                # O_RDWR (not O_WRONLY): the spec 14.2 checkpoint re-reads the written bytes through this
                # same descriptor; creation-time access is granted regardless of the created mode.
                fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                             op["poststate"]["mode"], dir_fd=pfd)
                try:
                    os.fchmod(fd, op["poststate"]["mode"])   # pin exact perms (umask independence)
                    data = staged_reader(op)
                    _verify_staged_digest(op, data)          # C3: staged bytes must match the INTENT digest
                    _maybe_torn_payload(fd, data, i)
                    _write_all(fd, data)
                    os.fsync(fd)
                    _read_back_verify(fd, op["poststate"]["content-sha256"], op["path"], "written")
                finally:
                    _close_fd_yielding(fd)
            elif kind == "remove":
                _verify_prestate_at(pfd, name, op["path"], op["prestate"])   # E1: check bound to the SAME pfd
                os.unlink(name, dir_fd=pfd)
            elif kind == "mkdir":
                os.mkdir(name, op["poststate"]["mode"], dir_fd=pfd)
                os.chmod(name, op["poststate"]["mode"], dir_fd=pfd, follow_symlinks=False)
                _fsync_contained_dir(pfd, name)              # fix #1: the dir's own mode durable, not just the parent
            elif kind == "rmdir":
                _verify_prestate_at(pfd, name, op["path"], op["prestate"])   # E1: check bound to the SAME pfd
                os.rmdir(name, dir_fd=pfd)
            else:
                raise JournalError("unknown op kind {!r}".format(kind))
            os.fsync(pfd)
        except OSError as exc:
            raise JournalError("apply of {} {!r} failed ({})".format(op["op"], op["path"], exc))
        finally:
            _close_fd_yielding(pfd)
        _kill_point("after-apply-{}".format(i))


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _maybe_torn_payload(fd, data, i):
    """Crash-injection: when the harness targets op i, write only a partial prefix of the payload,
    fsync it, and die, leaving a torn (mid-write) payload for recovery to repair. Inert unless the
    self-test harness sets KILL_ENV."""
    if os.environ.get(KILL_ENV, "") == "torn-payload:{}".format(i) and data:
        _write_all(fd, data[:len(data) // 2])       # always a strict partial prefix (0..len-1 bytes), never the full payload
        os.fsync(fd)
        os._exit(137)


def _maybe_torn_mode(root_fd, relpath, i):
    """Crash-injection between a rmdir-undo's mkdir and its chmod, when the harness targets op i: the
    directory has just been recreated at a umask-reduced mode but its exact prestate mode is not yet set.
    Fsync the parent so the recreated directory is durable, then die, so a fresh recover must re-apply the
    prestate mode (idempotent mode-resume). Inert unless the self-test harness sets KILL_ENV."""
    if os.environ.get(KILL_ENV, "") == "torn-mode:{}".format(i):
        _fsync_parent(root_fd, relpath)
        os._exit(137)


def _maybe_torn_dirsync(root_fd, relpath, i):
    """Crash-injection for fix #1: die immediately AFTER a rmdir-undo has recreated the directory, set its
    exact prestate mode, and fsync'd the directory's own fd (the durability point). A fresh recover must
    still land the directory at its exact prestate mode, terminal and idempotent. Inert unless the harness
    sets KILL_ENV; the kill point exists only on the fixed durable-fsync path."""
    if os.environ.get(KILL_ENV, "") == "torn-dirsync:{}".format(i):
        _fsync_parent(root_fd, relpath)
        os._exit(137)


# --- restore (idempotent, contained) ------------------------------------------------------------------

def _restore_preimage(jr_fd, txn_dir, root_fd, op, op_index=0):
    """Restore one op to its prestate, idempotently and contained. A no-op when the path already holds
    its prestate (including when a parent is still absent, so a create/mkdir undo whose subtree was
    never built is a clean no-op), so replaying a rollback is safe. Restores run in reverse dependency
    order (reversed(ops)), so the parent a recreate needs has already been recreated when it runs.
    Fail-closed on an unexpected on-disk type: a mkdir undo that finds a NON-directory where it must
    remove a created dir, and a rmdir undo that finds a non-directory where it must recreate a removed
    dir, each raise JournalError rather than silently skipping (matching the write/remove non-regular
    branch below). A rmdir undo whose directory ALREADY exists RE-APPLIES the prestate mode (idempotent
    mode-resume), so a crash between the mkdir and the chmod leaves no umask-reduced mode behind."""
    txn_dir = Path(txn_dir)
    path, prestate, kind = op["path"], op["prestate"], op["op"]
    try:
        pfd, name = _open_parent(root_fd, path)           # E1: bind the parent FIRST, then check on THIS fd
    except FileNotFoundError:
        # A parent is absent, so the target is absent too. For a create/mkdir undo whose subtree was never
        # built that IS the prestate (clean no-op); a write/remove/rmdir undo needs its parent to restore
        # into, so an absent parent is fail-closed.
        if kind in ("create", "mkdir"):
            return
        raise JournalError("cannot restore {!r}: parent directory absent".format(path))
    except OSError as exc:
        raise JournalError("cannot restore {!r} ({})".format(path, exc))
    try:
        st = _lstat_at(pfd, name)                         # E1: check bound to the SAME pfd the mutation uses
        if kind == "create":                              # created file: delete it back to absence
            if st is None:
                return                                    # already absent
            os.unlink(name, dir_fd=pfd)
        elif kind == "mkdir":                             # created dir: remove it back to absence
            if st is None:
                return                                    # already absent
            if not stat.S_ISDIR(st.st_mode):
                raise JournalError("cannot restore {!r}: expected a created directory to remove, found a "
                                   "non-directory".format(path))
            os.rmdir(name, dir_fd=pfd)
        elif kind == "rmdir":                             # removed dir: recreate it (or re-apply its mode)
            if st is None:                                # absent: recreate then set the exact mode
                os.mkdir(name, prestate["mode"], dir_fd=pfd)
                _maybe_torn_mode(root_fd, path, op_index)   # crash BETWEEN the mkdir and the chmod
                os.chmod(name, prestate["mode"], dir_fd=pfd, follow_symlinks=False)
                _fsync_contained_dir(pfd, name)             # fix #1: recreated dir's mode durable
                _maybe_torn_dirsync(root_fd, path, op_index)  # crash AFTER the durability fsync (fix #1 test)
            elif stat.S_ISDIR(st.st_mode):               # already recreated: re-apply the prestate mode
                os.chmod(name, prestate["mode"], dir_fd=pfd, follow_symlinks=False)
                _fsync_contained_dir(pfd, name)             # fix #1: re-applied mode durable
            else:                                        # a non-directory sits where the removed dir was
                raise JournalError("cannot restore {!r}: expected a directory or absence, found a "
                                   "non-directory".format(path))
        else:                                             # write or remove: recreate/rewrite prior bytes
            # Read the retained preimage CONTAINED (an O_DIRECTORY|O_NOFOLLOW walk beneath the trusted
            # journal-root fd, never a re-resolved absolute pathname that could follow a swapped txn-dir
            # component out of containment) and BOUNDED by the recorded prestate size (so a preimage grown
            # or swapped past its recorded length is refused fail-closed BEFORE the whole file is read into
            # memory, rather than read unbounded and only then digest-checked). _read_at confirms a regular
            # file on the opened fd; the digest check below still gates the restore (SECI-symlink-resolution,
            # SECA-resource-bounds).
            pre_rel = "{}/preimages/{}".format(Path(txn_dir).name, prestate["payload"])
            ppfd, pname = _open_parent(jr_fd, pre_rel)
            try:
                data, _pst = _read_at(ppfd, pname, pre_rel, cap=prestate["size"])
            finally:
                _close_fd_yielding(ppfd)
            if hashlib.sha256(data).hexdigest() != prestate["sha256"]:
                raise JournalError("preimage for {!r} does not match recorded prestate digest".format(path))
            if st is None:
                _recreate_file(pfd, name, data, prestate["mode"])
            elif stat.S_ISREG(st.st_mode):
                observed_mode = stat.S_IMODE(st.st_mode)
                granted = False

                def _revert_grant(vfd=None):
                    # Best-effort revert of the temporary owner-rw grant, ATTEMPTED on every failed
                    # exit from the grant chmod up to and including the checkpoint that the
                    # BaseException handler below observes: through the opened descriptor when its
                    # identity has been verified (vfd: an fchmod that touches exactly the inode
                    # the lstat saw), else by name beneath the same parent fd, no-follow (the same
                    # channel the grant itself used). The revert can itself fail under the same
                    # fault that aborted the attempt, and a hard kill -- or an exception raised at
                    # an interpreter instruction the compiled exception table does not cover (see
                    # the INTERPRETER-INSTRUCTION RESIDUAL below) -- can skip it entirely, so it
                    # is NOT unconditional; a failed exit AT the post-checkpoint prestate-mode
                    # install below DELIBERATELY does not revert at all (the bytes already
                    # verified; see the comment there). The grant then persists ONLY on an inode
                    # whose link count was 1 at the pre-grant gate below, i.e. on the product file
                    # itself. A persisted grant always carries owner rw, so the next reconcile's
                    # O_RDWR reopen succeeds DIRECTLY and finishes without re-entering this grant
                    # path at all; the grant cycle runs again only on an exit that left NO grant
                    # behind a still-unwritable mode (a reverted failure, an interruption that
                    # beat the grant chmod, or a post-checkpoint exit whose prestate-mode fchmod
                    # already took effect and left a read-only prestate mode). The pre-grant
                    # hard-link gate, not this revert, is what keeps an inode reachable outside
                    # the product root from ever being widened.
                    if vfd is not None:
                        try:
                            os.fchmod(vfd, observed_mode)
                            return
                        except OSError:
                            pass
                    try:
                        os.chmod(name, observed_mode, dir_fd=pfd, follow_symlinks=False)
                    except (OSError, ValueError):
                        pass

                fd = None
                identity_verified = False
                try:
                    try:
                        try:
                            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
                        except PermissionError:
                            # RESTARTABLE RESTORATION (codex U1 round-2): a live file whose mode
                            # denies owner write (a 0400 or 0000 prestate, or the debris of an
                            # interrupted earlier restore) cannot be reopened O_RDWR, which would
                            # wedge recovery forever on EACCES with the preimage still retained.
                            # Grant a TEMPORARY owner-rw bit by name (no-follow, beneath the same
                            # parent fd) and reopen. HARD-LINK GATE (codex U1 round-3): the grant
                            # is applied ONLY to an inode whose lstat link count is exactly 1,
                            # checked BEFORE any chmod, so an inode carrying a second name
                            # (possibly outside the product root) is never widened, not even
                            # transiently, and not by a fault or an interruption after the grant,
                            # because for such an inode no grant chmod ever runs. The grant also
                            # requires OWNERSHIP of the file: the kernel refuses a non-owner's
                            # chmod with EPERM, so a non-owned unwritable file fails closed with
                            # the named JournalError below, its mode unchanged. On success the
                            # exact prestate mode replaces the grant after the checkpoint. The
                            # revert above is attempted on every failed exit from the grant chmod
                            # up to and including the checkpoint that the handler observes (a
                            # reopen failure, an fstat failure, an identity refusal, a checkpoint
                            # failure, an exception or interruption delivered in that span: the
                            # protection is established BEFORE the chmod). The residuals that
                            # leave the grant installed, all only on the singly-linked product
                            # file and all restored by the next reconcile, are: a hard kill (no
                            # handler runs; the revert can also itself fail under the same
                            # fault); an exception raised at an interpreter instruction the
                            # compiled exception table does not cover, equivalent to a hard kill
                            # (see the INTERPRETER-INSTRUCTION RESIDUAL below); and a failed exit
                            # AT the post-checkpoint prestate-mode install BEFORE its fchmod takes
                            # effect, which DELIBERATELY leaves the grant for the next reconcile
                            # to finish (see the comment there; a failure AFTER that fchmod leaves
                            # the exact prestate mode, no grant). The
                            # lstat-to-chmod window is the same accident-model TOCTOU the
                            # pre-existing lstat-to-open window carries; the post-open fstat
                            # identity check below stays the arbiter, and an adversarial same-user
                            # racer remains outside the journal's disclosed quiescence guarantee.
                            if st.st_nlink != 1:
                                raise JournalError("cannot restore {!r}: product file has {} hard "
                                                   "links (>1); refusing to grant temporary "
                                                   "owner-write to a multiply-linked inode (a "
                                                   "second name, possibly outside the product "
                                                   "root, would be widened through the shared "
                                                   "inode); no chmod was "
                                                   "applied".format(path, st.st_nlink))
                            # REVERT PROTECTION BEFORE THE GRANT (codex U1 round-4): granted is
                            # set BEFORE the grant chmod, and the BaseException handler below
                            # spans the chmod, the reopen and the whole restore body at source
                            # level, so an exception or an interruption the handler observes
                            # after the grant syscall takes effect -- even between the chmod
                            # returning and the next statement -- reaches the revert. If the
                            # interruption instead beats the chmod, the revert is an idempotent
                            # chmod back to the mode the file already holds.
                            # INTERPRETER-INSTRUCTION RESIDUAL (codex U1 round-5): the handler's
                            # span is a source-level guarantee. The compiled body can hold
                            # individual instructions that no exception-table entry covers
                            # (OBSERVED on CPython 3.14.4: a NOT_TAKEN instruction of the
                            # post-reopen identity-check branch sits in a one-instruction gap
                            # between two covered ranges). An exception that a tracing or
                            # monitoring hook raises AT such an instruction escapes this handler
                            # with the grant still installed; that escape was demonstrated only
                            # under opcode-level trace injection. That a real asynchronously
                            # delivered signal cannot land on such an instruction is INFERRED
                            # from the interpreter's safe-point delivery, not observed. The
                            # residual is treated exactly as a hard kill: the grant persists only
                            # on the singly-linked product file (the pre-grant hard-link gate)
                            # and the next reconcile restores the exact prestate bytes and mode.
                            # No machinery chases interpreter instruction gaps here.
                            granted = True
                            try:
                                os.chmod(name, observed_mode | 0o600, dir_fd=pfd,
                                         follow_symlinks=False)
                            except ValueError:
                                # A symlink raced in between the failed open and this chmod:
                                # os.chmod with dir_fd and follow_symlinks=False refuses a symlink
                                # with ValueError on this platform. Fail closed as a JournalError,
                                # never a raw traceback; nothing was widened.
                                raise JournalError("cannot restore {!r}: a symlink was raced in "
                                                   "at the temporary-grant chmod (dir_fd with "
                                                   "follow_symlinks=False refuses a symlink); "
                                                   "failing closed, nothing was "
                                                   "widened".format(path))
                            except PermissionError as exc:
                                # The temporary grant REQUIRES OWNERSHIP of the file: chmod on a
                                # file this process does not own raises EPERM. A non-owned
                                # unwritable file fails closed here, its mode unchanged, rather
                                # than wedging recovery or escaping with a raw error.
                                raise JournalError("cannot restore {!r}: the temporary "
                                                   "owner-write grant requires ownership of the "
                                                   "file and the kernel refused the chmod ({}); "
                                                   "a non-owned unwritable file fails closed "
                                                   "with its mode unchanged".format(path, exc))
                            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                         dir_fd=pfd)
                        # SECI-symlink-resolution: the S_ISREG decision above rests on the PRE-open
                        # lstat, which describes a name that may no longer point where it did.
                        # O_NOFOLLOW refuses a symlink but NOT a hardlink or a regular-file swap
                        # raced in between the lstat and this open (both are regular files, so a
                        # post-open S_ISREG alone would not catch it). Before truncating and
                        # rewriting, confirm on the OPENED fd that it is STILL a regular file AND
                        # the SAME object (st_ino/st_dev) the lstat saw; a mismatch means a
                        # different inode was swapped in and is refused fail-closed rather than
                        # truncating and overwriting an unintended victim. This mirrors the apply
                        # path's _verify_fd_prestate post-open confirmation, which the restore
                        # path previously lacked. (O_NONBLOCK matches the contained-reader
                        # pattern: a no-op for a regular file, and it keeps a raced-in FIFO from
                        # blocking the open.)
                        fst = os.fstat(fd)
                        if (not stat.S_ISREG(fst.st_mode) or fst.st_ino != st.st_ino
                                or fst.st_dev != st.st_dev):
                            raise JournalError("cannot restore {!r}: the regular file was swapped for a "
                                               "different object between the pre-open check and the open "
                                               "(fail-closed)".format(path))
                        identity_verified = True
                        if fst.st_nlink != 1:
                            # A multiply-linked target shares its inode with another name, so the
                            # ftruncate+rewrite below would mutate that out-of-tree victim through the
                            # shared inode. Refuse on the OPENED fd BEFORE truncating, the same
                            # product-file nlink==1 defence _verify_fd_prestate applies on the apply path
                            # (SECI-symlink-resolution / codex round-8).
                            raise JournalError("cannot restore {!r}: product file has {} hard links (>1); "
                                               "refusing to truncate/write a multiply-linked file (a second "
                                               "name would mutate an out-of-tree victim through the shared "
                                               "inode)".format(path, fst.st_nlink))
                        os.ftruncate(fd, 0)
                        os.lseek(fd, 0, os.SEEK_SET)
                        _write_all(fd, data)
                        os.fsync(fd)
                        # Spec 14.2 rollback checkpoint: the restored live bytes verify BEFORE this restore
                        # returns, so the reversal never discards the aborted run's archive copy (a later
                        # create-undo in the reverse order) or reports a prestate over a faulty restore.
                        # Verified BEFORE the prestate mode is installed (below), so a failed checkpoint
                        # never strands the file behind a read-only mode: the attempt is restartable.
                        _read_back_verify(fd, prestate["sha256"], path, "restored")
                    except BaseException:
                        if granted:
                            # The revert is attempted on every failed exit up to and including
                            # the checkpoint that this handler observes: through the fd once its
                            # identity is verified (it touches exactly the file the lstat saw),
                            # else by name (a reopen failure, an fstat failure or an identity
                            # refusal); see _revert_grant for why a revert that itself fails is
                            # still confined to the product file (the pre-grant hard-link gate),
                            # and the INTERPRETER-INSTRUCTION RESIDUAL above for the exits this
                            # handler never sees.
                            _revert_grant(fd if identity_verified else None)
                        raise
                    # POST-CHECKPOINT EXITS NEVER REVERT (claude U1 round-4; split by the fchmod
                    # boundary in round-5): the checkpoint above has verified the restored live
                    # bytes, so the only missing step is the exact prestate mode, and no exit
                    # past this point reverts to the pre-grant mode. WHICH state a failed exit
                    # leaves depends on whether the prestate-mode fchmod below took effect.
                    # BEFORE it takes effect (the fchmod itself faulting, or an interruption
                    # beating it): the temporary owner-rw grant is DELIBERATELY left installed --
                    # exactly as on the recreate path (_recreate_file), an owner-WRITABLE product
                    # file lets the next reconcile reopen it and finish installing the prestate
                    # mode directly, where a revert to a read-only debris mode would force the
                    # whole grant cycle to run again for no gain; this is the post-checkpoint
                    # grant residual the gates-manifest residue discloses, only on the
                    # singly-linked product file. AFTER it takes effect (the durability fsync
                    # faulting, or an interruption landing past the fchmod): the live mode is
                    # ALREADY the exact prestate mode -- NO grant remains, only the mode's
                    # durability is unconfirmed, and a read-only prestate mode makes the next
                    # reconcile run the whole grant cycle again before it finishes. On the fault
                    # paths the exit is a NAMED JournalError stating which of the two states was
                    # left; an interruption leaves the same state un-named.
                    mode_installed = False
                    try:
                        os.fchmod(fd, prestate["mode"])   # the exact prestate mode, only after the checkpoint
                        mode_installed = True
                        os.fsync(fd)                      # the final mode durable alongside the verified bytes
                    except OSError as exc:
                        if granted and not mode_installed:
                            raise JournalError("cannot restore {!r}: installing the exact prestate "
                                               "mode after the checkpoint failed ({}); the "
                                               "temporary owner-write grant is deliberately left "
                                               "in place (the restored bytes already passed the "
                                               "checkpoint, and an owner-writable product file "
                                               "lets the next reconcile finish installing the "
                                               "prestate mode directly)".format(path, exc))
                        if granted:
                            raise JournalError("cannot restore {!r}: the exact prestate mode was "
                                               "already installed after the checkpoint and only "
                                               "its durability fsync failed ({}); no grant "
                                               "remains (the live mode is the prestate mode and "
                                               "the restored bytes already passed the "
                                               "checkpoint), and the next reconcile finishes "
                                               "from that mode, re-running the grant cycle "
                                               "first when the prestate mode is itself "
                                               "unwritable".format(path, exc))
                        raise
                finally:
                    if fd is not None:
                        _close_fd_yielding(fd)
            else:                                     # a racing external writer left a non-regular file where a regular file is expected: fail closed
                raise JournalError("cannot restore {!r}: unexpected non-regular file at restore time".format(path))
        os.fsync(pfd)
    except OSError as exc:
        raise JournalError("cannot restore {!r} ({})".format(path, exc))
    finally:
        _close_fd_yielding(pfd)


def _recreate_file(pfd, name, data, mode):
    # O_RDWR (not O_WRONLY): the spec 14.2 rollback checkpoint re-reads the restored bytes through this
    # same descriptor before the reversal moves on (see _read_back_verify). The file is created and
    # verified under a TEMPORARY owner-rw mode; the exact prestate mode is installed only AFTER the
    # checkpoint passes. A checkpoint failure (a faulty restore write, or the verification read itself
    # failing) therefore leaves an owner-writable file a LATER reconcile can reopen and finish restoring
    # once the fault is gone: a read-only prestate mode (0400, 0000) never wedges recovery behind an
    # EACCES reopen (restartable restoration; codex U1 round-2).
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
    try:
        os.fchmod(fd, 0o600)                 # pin the temporary grant exactly (umask independence)
        _write_all(fd, data)
        os.fsync(fd)
        _read_back_verify(fd, hashlib.sha256(data).hexdigest(), name, "restored")
        os.fchmod(fd, mode)                  # the exact prestate mode, only after the bytes verified
        os.fsync(fd)                         # the final mode durable alongside the verified bytes
    finally:
        _close_fd_yielding(fd)


# --- recovery (from the journal alone, both directions, idempotent) -----------------------------------

# The ONLY valid frame-type sequences an engine journal may hold (C2). Every other ordering, duplicate,
# or contradiction (COMPLETE alongside a rollback record, ROLLBACK-COMPLETE before ROLLBACK-IN-PROGRESS, a
# terminal frame before INTENT, two INTENTs) is rejected by not being a member of this set.
_ACCEPTED_SEQUENCES = frozenset([
    (),
    (F_INTENT,),
    (F_INTENT, F_COMPLETE),
    (F_INTENT, F_RIP),
    (F_INTENT, F_RIP, F_RC),
])


def _validate_terminal_agreement(frames):
    """Explicit accepted-sequence state machine (C2), invoked identically from recover() and is_terminal().
    The ONLY valid frame-type sequences are [], [INTENT], [INTENT,COMPLETE], [INTENT,RIP], and
    [INTENT,RIP,RC], with EXACTLY ONE INTENT carrying a single nonempty txn id and every subsequent frame
    agreeing on that txn id. This rejects, by construction: ROLLBACK-COMPLETE before ROLLBACK-IN-PROGRESS
    (the sequence is not a member); COMPLETE alongside a rollback frame; a duplicate INTENT INCLUDING two
    null-txn INTENTs (two INTENT tokens are never an accepted sequence, so a null-txn sentinel is not
    relied on); any terminal frame before INTENT; and any out-of-order or duplicate terminal. Recovery
    trusts its own checksummed framing; a crafted valid-checksum journal is outside the accident-recovery
    model (see the module Guarantee-scope note). JournalError on any violation."""
    types = tuple(t for t, _ in frames)
    if types not in _ACCEPTED_SEQUENCES:
        raise JournalError("journal frame sequence {} is not an accepted terminal sequence".format(
            list(types)))
    if not types:
        return
    intent_obj = frames[0][1]                             # types[0] is F_INTENT by construction of the set
    intent_txn = intent_obj.get("txn") if isinstance(intent_obj, dict) else None
    if not isinstance(intent_txn, str) or not intent_txn:
        raise JournalError("INTENT frame carries no single nonempty txn id")
    for ftype, obj in frames[1:]:
        txn = obj.get("txn") if isinstance(obj, dict) else None
        if txn != intent_txn:
            raise JournalError("frame {} txn id disagrees with the INTENT txn id".format(ftype))


def classify_state(jr_fd, txn_dir, txn_fd=None):
    """Classify a transaction's DURABLE journal state via the C2 state machine (never a bare boolean).
    Returns 'nothing-opened' (no INTENT: pre-INTENT/capture-phase failure, nothing applied), 'complete',
    'rolled-back' ([INTENT,RIP,RC] terminal rollback), or 'open' (INTENT present without a terminal
    COMPLETE or RC). JournalError on a corrupt or invalid-sequence journal (fail-closed). A torn tail is
    treated as never written (read_frames), consistent with recover(). The txn dir is reached contained
    beneath the trusted journal-root fd (jr_fd), or, when txn_fd is given (a txn-dir descriptor the
    caller HELD from its enumeration), read through that SAME held directory identity, so a read-only
    reporter classifies exactly the directory it listed (round 4; see read_frames)."""
    frames, _torn, _ = read_frames(jr_fd, txn_dir, txn_fd=txn_fd)
    _validate_terminal_agreement(frames)
    types = [t for t, _ in frames]
    if F_INTENT not in types:
        return "nothing-opened"
    if F_COMPLETE in types:
        return "complete"
    if F_RC in types:
        return "rolled-back"
    return "open"


def recover(jr_fd, txn_dir, root_fd):
    """Reconcile one transaction directory to a terminal state from its journal ALONE, at every crash
    point, both directions, idempotently. Returns one of: nothing-opened, terminal, rolled-forward,
    rolled-back. A torn tail is truncated before any terminal frame is appended so the log stays
    parseable and a second recover is a no-op. The txn journal is read and its terminal frames published
    beneath the trusted journal-root fd (jr_fd); preimage restore uses the contained root_fd as before."""
    frames, torn, good_len = read_frames(jr_fd, txn_dir)
    if torn:
        _truncate_log(jr_fd, txn_dir, good_len)
    _validate_terminal_agreement(frames)
    types = [t for t, _ in frames]
    if F_INTENT not in types:
        return "nothing-opened"
    intent = _first(frames, F_INTENT)
    if F_COMPLETE in types or F_RC in types:
        return "terminal"
    ops = intent["ops"]
    if F_RIP not in types:
        if all(_poststate_verifies(root_fd, op) for op in ops):
            publish(jr_fd, txn_dir, F_COMPLETE, {"txn": intent["txn"]})
            return "rolled-forward"
        publish(jr_fd, txn_dir, F_RIP, {"txn": intent["txn"]})
    total = len(ops)
    for j, op in enumerate(reversed(ops)):
        _restore_preimage(jr_fd, txn_dir, root_fd, op, total - 1 - j)
        _kill_point("after-restore-{}".format(total - 1 - j))
    publish(jr_fd, txn_dir, F_RC, {"txn": intent["txn"]})
    return "rolled-back"


# --- transaction driver (the shared cutover primitive migrate.py builds on) ---------------------------

def run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops, staged_reader, session_id):
    """Drive one cutover transaction through the seven normative steps under a fresh O_EXCL lock, rolling
    back from preimages on any prestate mismatch during apply. Assumes require_containment already
    passed. Returns 'complete' on success or raises JournalError (the caller maps to exit 2). The lock is
    released by the caller's higher-level flow via release_lock after a terminal outcome. Every framed
    record is published beneath the trusted journal-root fd (jr_fd), reached by a contained walk, so no
    txn-dir absolute path is re-resolved for the frame writes (F1 / SECI-symlink-resolution)."""
    txn_dir = Path(journal_root) / txn_id
    # A2: enforce a transaction-wide serialized journal BUDGET before any product mutation and before the
    # txn dir is even created. The recovery reader caps frames.log at _MAX_JOURNAL_READ_BYTES; the largest
    # terminal log this transaction can produce is INTENT + ROLLBACK-IN-PROGRESS + ROLLBACK-COMPLETE (the
    # rollback path, larger than INTENT + COMPLETE), so reserve room for all three HERE. Over budget => raise
    # BEFORE any mutation, so a crash can never leave an applied tree beside a journal recovery would refuse
    # as oversize (F-R17-A2). The frames are serialized exactly as publish() does.
    def _budget_frame(ftype, obj):
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return _frame(ftype, payload)
    _journal_budget = (
        len(_budget_frame(F_INTENT, {"txn": txn_id, "header": header, "ops": ops}))
        + len(_budget_frame(F_RIP, {"txn": txn_id}))
        + len(_budget_frame(F_RC, {"txn": txn_id}))
    )
    if _journal_budget > _MAX_JOURNAL_READ_BYTES:
        raise JournalError("transaction journal frames would total {} bytes (INTENT+ROLLBACK), over the "
                           "{}-byte journal-read cap; refusing BEFORE any mutation so a crash leaves a "
                           "recoverable journal (fail-closed)".format(_journal_budget,
                                                                      _MAX_JOURNAL_READ_BYTES))
    # F1: create the txn dir (a single component) CONTAINED beneath the trusted journal-root fd, and make
    # its dir entry durable by fsync'ing THAT contained fd, never a re-resolved absolute journal_root path.
    # os.mkdir raises FileExistsError on a collision, preserving the exist_ok=False refusal.
    try:
        os.mkdir(txn_id, 0o777, dir_fd=jr_fd)
    except OSError as exc:
        raise JournalError("cannot create contained journal txn dir {!r} ({})".format(txn_id, exc))
    os.fsync(jr_fd)
    # Create frames.log EXCLUSIVELY in the fresh txn dir before any publish appends to it, so a pre-planted
    # entry (a hardlink to a victim or a plain regular file) is refused at creation (codex round-6).
    _create_frames_excl(jr_fd, txn_dir)
    capture_preimages(jr_fd, txn_dir, root_fd, ops)
    # F-R18-A2BUD: capture_preimages MUTATES each op in place, adding its prestate metadata, so the INTENT
    # actually published now carries MORE bytes than the pre-mutation budget above reserved. Re-check the
    # FINALIZED serialized envelope (the post-capture INTENT plus the two rollback terminal frames, the
    # largest terminal log) against the reader cap HERE, BEFORE INTENT is published and BEFORE apply_ops
    # touches the product tree. A boundary transaction whose pre-capture ops fit the cap but whose
    # post-capture INTENT+ROLLBACK does not would otherwise apply the mutation, then fail to publish a
    # terminal frame (over the append cap), leaving an applied tree beside a journal recovery cannot complete
    # (unrecoverable). Refuse fail-closed before any product mutation reaches a non-terminal state; the txn
    # dir carries only preimages and no INTENT, so recovery reads it as nothing-opened (terminal).
    _final_budget = (
        len(_budget_frame(F_INTENT, {"txn": txn_id, "header": header, "ops": ops}))
        + len(_budget_frame(F_RIP, {"txn": txn_id}))
        + len(_budget_frame(F_RC, {"txn": txn_id}))
    )
    if _final_budget > _MAX_JOURNAL_READ_BYTES:
        raise JournalError("finalized transaction journal frames would total {} bytes (post-capture "
                           "INTENT+ROLLBACK), over the {}-byte journal-read cap; refusing BEFORE any product "
                           "mutation so a crash leaves a recoverable journal (fail-closed; F-R18-A2BUD)".format(
                               _final_budget, _MAX_JOURNAL_READ_BYTES))
    publish(jr_fd, txn_dir, F_INTENT, {"txn": txn_id, "header": header, "ops": ops})
    try:
        apply_ops(root_fd, ops, staged_reader)
        # C3: COMPLETE must mean every poststate was installed. Verify ALL domain-separated post-states
        # before publishing COMPLETE; if any fails, do NOT publish COMPLETE, roll back, and fail closed.
        if not all(_poststate_verifies(root_fd, op) for op in ops):
            raise JournalError("post-apply poststate verification failed; refusing to publish COMPLETE")
    except JournalError:
        # A prestate mismatch, a staged-digest mismatch, or a failed poststate (a hostile or racing tree):
        # roll back from the durable preimages and fail.
        publish(jr_fd, txn_dir, F_RIP, {"txn": txn_id})
        total = len(ops)
        for j, op in enumerate(reversed(ops)):
            _restore_preimage(jr_fd, txn_dir, root_fd, op, total - 1 - j)
        publish(jr_fd, txn_dir, F_RC, {"txn": txn_id})
        raise
    publish(jr_fd, txn_dir, F_COMPLETE, {"txn": txn_id})
    return "complete"


def is_terminal(jr_fd, txn_dir):
    """True when the transaction has a durable terminal record (COMPLETE or ROLLBACK-COMPLETE); False
    when it is still open (INTENT or ROLLBACK-IN-PROGRESS without a terminal). Invokes the SAME C2 state
    machine as recover() so the two classify identically; a corrupt mid-log frame or an invalid frame
    sequence is a JournalError (fail-closed). The txn dir is reached contained beneath the trusted
    journal-root fd (jr_fd)."""
    frames, torn, _ = read_frames(jr_fd, txn_dir)
    _validate_terminal_agreement(frames)
    types = [t for t, _ in frames]
    if F_INTENT not in types:
        return True                                       # nothing opened (or torn INTENT): not open
    return F_COMPLETE in types or F_RC in types


def build_inverse_ops(intent_ops):
    """The INERT reverse-replay primitive (10.6): from a terminal transaction's INTENT ops, build the
    inverse op list that returns the tree to that transaction's prestate, in reverse dependency order.
    write -> write the prior bytes back; create -> remove; remove -> create the prior bytes; mkdir ->
    rmdir; rmdir -> mkdir. The caller replays these as a NEW journaled transaction whose staged_reader
    serves the ORIGINAL transaction's retained preimages. This is inert internal code that Step 7 builds
    on: the un-adopt CLI and workflow (cross-transaction ordering, authorization, repoint inversion, and
    pin removal) are Section 12 step 7 (spec 1586 to 1590), NOT this step-6 slice, which exposes no
    un-adopt subcommand."""
    inverse = []
    for op in reversed(intent_ops):
        pre, post, kind, path = op["prestate"], op.get("poststate", {}), op["op"], op["path"]
        # E2: each inverse op pins the SOURCE poststate the effective tree must still hold at capture (the
        # state the ORIGINAL op installed), so un-adopt's reverse capture is itself a drift check.
        if kind == "write":                               # after the write: prestate mode, poststate bytes
            inverse.append({"op": "write", "path": path,
                            "poststate": {"kind": "file", "content-sha256": pre["sha256"]},
                            "source-poststate": {"kind": "file", "mode": pre["mode"],
                                                 "sha256": post.get("content-sha256")},
                            "_source": op})
        elif kind == "create":                            # after the create: the created file must be present
            inverse.append({"op": "remove", "path": path, "poststate": {"kind": "absent"},
                            "source-poststate": {"kind": "file", "mode": post.get("mode"),
                                                 "sha256": post.get("content-sha256")}})
        elif kind == "remove":                            # after the remove: the path must be absent
            inverse.append({"op": "create", "path": path,
                            "poststate": {"kind": "file", "mode": pre["mode"],
                                          "content-sha256": pre["sha256"]},
                            "source-poststate": {"kind": "absent"},
                            "_source": op})
        elif kind == "mkdir":                             # after the mkdir: the created dir must be present
            inverse.append({"op": "rmdir", "path": path, "poststate": {"kind": "absent"},
                            "source-poststate": {"kind": "dir", "mode": post.get("mode")}})
        elif kind == "rmdir":                             # after the rmdir: the path must be absent
            inverse.append({"op": "mkdir", "path": path,
                            "poststate": {"kind": "dir", "mode": pre["mode"]},
                            "source-poststate": {"kind": "absent"}})
        else:
            raise JournalError("cannot invert unknown op kind {!r}".format(kind))
    return inverse


# --- self-test: the #378 close vectors -------------------------------------------------------------------
# `_journal.py --self-test` runs these, and opf.py registers self_test so `opf.py --self-test` runs them in
# CI. The four tools that import _journal (check_crosswalk, doctor, migrate, pin) drive their own
# representative site through the same harness; the six tools with a local helper copy use
# tools/_close_selftest.py, a copy of it kept in step, and _opf_check runs the helper vectors (V1, V2) for
# its own _close_fd_quietly through this one.

class _StSentinel(Exception):
    """The in-flight exception a masking vector raises: not an OSError, so no site's `except OSError`
    converts it, and it must reach the caller as the same object."""


class _StCloseFault:
    """While active, the FIRST close of an ARMED descriptor fails the way close(2) documents for Linux:
    the number is released first (a real close), an unrelated descriptor then takes it (the reuse another
    thread may make the moment the number is free), and only then does the close raise EIO (or the errno
    the arm names). Every other close is the real one. `fired` records, per injected failure, the
    descriptor's fstat taken BEFORE the close (None if it was already closed): a vector whose fault never
    fired, or fired on a closed descriptor, proves nothing and is red (NOFIRE). The reuser is a pipe end
    put on the number with dup2, inline or, armed with thread=True, by a real second thread that takes
    the number while the close is still in progress and checks afterwards that it still owns it; settle()
    reports each reuser that lost its number (REUSE). While `watch` is set, every later os.close of a
    released number by the faulting thread, and every call that inspects it by number (_ST_WATCHED: os.stat
    given the number itself, os.fstat, os.fstatvfs, os.lseek, os.get_inheritable, os.isatty, fcntl.fcntl,
    fcntl.flock, fcntl.lockf, fcntl.ioctl), is recorded in `probes` (PROBE)."""

    def __init__(self, watch=True):
        import errno
        import threading
        self.armed = {}
        self.fired = []
        self.probes = []
        self.lost = []
        self.watch = watch
        self.err = OSError(errno.EIO, "self-test injected close failure")
        self._close = os.close
        self._fstat = os.fstat
        fcntl = _st_fcntl()
        self._watched = [(module, name, getattr(module, name)) for module, names in ((os, _ST_WATCHED[0]),
                         (fcntl, _ST_WATCHED[1])) if module for name in names if hasattr(module, name)]
        self._threading = threading
        self._released = set()
        self._faulting = None
        self._inline = []
        self._threads = []

    def arm(self, fd, errnum=None, thread=False):
        if errnum is not None:
            self.err = OSError(errnum, "self-test injected close failure")
        state = None
        if thread:
            ev = self._threading.Event
            state = {"fd": None, "ident": None, "owned": None, "started": ev(), "go": ev(), "ready": ev(),
                     "done": ev()}
            worker = self._threading.Thread(target=self._reuse_in_thread, args=(state,), daemon=True)
            worker.start()
            state["started"].wait(10)
            self._threads.append((worker, state))
        self.armed[fd] = state
        return fd

    def _reuse_in_thread(self, state):
        """The second thread: its pipe is opened BEFORE the number is free, so dup2 is what puts it there."""
        rfd, wfd = os.pipe()
        try:
            state["started"].set()
            state["go"].wait(10)
            if state["fd"] is not None:
                os.dup2(rfd, state["fd"])
                st = self._fstat(state["fd"])
                state["ident"] = (st.st_dev, st.st_ino)
        finally:
            self._close(rfd)
            self._close(wfd)
            state["ready"].set()
        if state["ident"] is None or not state["done"].wait(10):
            return
        try:
            st = self._fstat(state["fd"])
            state["owned"] = (st.st_dev, st.st_ino) == state["ident"]
        except OSError:
            state["owned"] = False
        if state["owned"]:
            self._close(state["fd"])                      # the second thread releases what it still owns

    def _fake_close(self, fd):
        if fd in self._released and self.watch and self._threading.get_ident() == self._faulting:
            self.probes.append(("close", fd))
        if fd not in self.armed:
            return self._close(fd)
        state = self.armed.pop(fd)
        try:
            self.fired.append(self._fstat(fd))
        except OSError:
            self.fired.append(None)
        if self.fired[-1] is not None:
            self._faulting = self._threading.get_ident()
            if state is None:
                rfd, wfd = os.pipe()                      # opened before the release, never on fd itself
                try:
                    self._close(fd)
                    self._released.add(fd)
                    os.dup2(rfd, fd)
                    st = self._fstat(fd)
                    self._inline.append((fd, (st.st_dev, st.st_ino)))
                finally:
                    self._close(rfd)
                    self._close(wfd)
            else:
                self._close(fd)
                self._released.add(fd)
                state["fd"] = fd
                state["go"].set()
                if not state["ready"].wait(10) or state["ident"] is None:
                    self.lost.append((fd, "the second thread never took the number"))
        raise self.err

    def _watcher(self, name, real):
        """The stand-in for one watched call: it records a released number it is given, then calls the real
        one. Only an int is a number (os.stat of a path, or fcntl of a file object, is not one)."""
        def watched(fd, *args, **kwargs):
            if type(fd) is int and fd in self._released and self.watch \
                    and self._threading.get_ident() == self._faulting:
                self.probes.append((name, fd))
            return real(fd, *args, **kwargs)
        return watched

    def settle(self):
        """After the call: confirm each reuser still owns its number, then release it. Returns the losses."""
        for fd, ident in self._inline:
            try:
                st = self._fstat(fd)
                owned = (st.st_dev, st.st_ino) == ident
            except OSError:
                owned = False
            if owned:
                self._close(fd)
            else:
                self.lost.append((fd, "the inline reuser lost the number"))
        for worker, state in self._threads:
            state["go"].set()                             # an unfired arm: the thread exits untouched
            state["done"].set()
            worker.join(10)
            if state["ident"] is not None and not state["owned"]:
                self.lost.append((state["fd"], "the second thread lost the number"))
        self._inline, self._threads = [], []
        return self.lost

    def __enter__(self):
        os.close = self._fake_close
        self._stand_ins = []
        for module, name, real in self._watched:
            watched = self._watcher(name, real)
            setattr(module, name, watched)
            for table in _st_supports(module):
                if real in table:                         # a capability probe (os.stat in
                    table.add(watched)                    # os.supports_dir_fd) still finds the call
                    self._stand_ins.append((table, watched))
        return self

    def __exit__(self, *exc_info):
        os.close = self._close
        for module, name, real in self._watched:
            setattr(module, name, real)
        for table, watched in self._stand_ins:
            table.discard(watched)
        return False


# The calls _StCloseFault watches, from os and from fcntl, each taking the descriptor number first. Not
# watched: calls that use a number without inspecting it (read, write, dup, fsync, fchmod, ...), a number
# passed as dir_fd= to a path call, and a number reached through a wrapping object (os.fdopen, socket).
_ST_WATCHED = (("stat", "fstat", "fstatvfs", "lseek", "get_inheritable", "isatty"),
               ("fcntl", "flock", "lockf", "ioctl"))


def _st_supports(module):
    """The capability sets of `module` (os.supports_dir_fd and its kin) a stand-in must join while active."""
    tables = (getattr(module, name, None) for name in ("supports_dir_fd", "supports_fd",
                                                        "supports_follow_symlinks", "supports_effective_ids"))
    return [table for table in tables if isinstance(table, set)]


def _st_fcntl():
    """The fcntl module, or None where it does not exist (it is POSIX-only): there nothing can call
    fcntl.fcntl, flock, lockf or ioctl, so there is nothing to watch there, and the fcntl probe flip (F)
    is not run."""
    try:
        import fcntl
    except ImportError:
        return None
    return fcntl


def _st_fd_table():
    """The open descriptors below 1024 and the file each names, so a leak is found even when its number
    is reused by a different file."""
    table = {}
    for fd in range(1024):
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        table[fd] = (st.st_dev, st.st_ino)
    return table


def _st_close_run(call, masking, expect, watch=True):
    """Run one vector: call(fault) drives the site with the fault active. Returns its problems, each
    "TAG: detail": NOFIRE (the fault did not fire on an open descriptor), REUSE (the descriptor that took
    the released number no longer owns it: the number was closed again), PROBE (the faulting thread closed
    the released number, or inspected it by a call _ST_WATCHED names, after the failed close; checked only
    while `watch`),
    LEAK (a descriptor the call opened is still open), MASKED (the injected close error replaced the in-flight
    exception), SILENT (a normal-path failing close did not raise), WRONG (any other outcome). `masking` is
    True where an exception is in flight, False on a normal path whose close error must raise, and None on
    a quiet teardown path that must swallow it. Empty means green."""
    before = _st_fd_table()
    fault = _StCloseFault(watch)
    raised = None
    try:
        with fault:
            call(fault)
    except Exception as exc:  # noqa: BLE001  every outcome is classified below
        raised = exc
    lost = fault.settle()
    after = _st_fd_table()
    problems = []
    if len(fault.fired) != 1 or fault.fired[0] is None:
        problems.append("NOFIRE: injected close failures {}".format(fault.fired))
    if lost:
        problems.append("REUSE: a released number was closed again under its new owner {}".format(lost))
    if fault.probes:
        problems.append("PROBE: a released number was touched again {}".format(fault.probes))
    # A deliberately leaked number is reported and left open: the harness records no opens, so it cannot
    # prove the number is still the tested call's own. Another thread may have reused it (a released
    # number included) through os.open, builtin open() or any other route, so closing it could close
    # someone else's descriptor (#378 P1).
    leaked = sorted(fd for fd, ident in after.items() if before.get(fd) != ident)
    if leaked:
        problems.append("LEAK: descriptor(s) {} survived the failing close".format(leaked))
    if masking is None:
        if raised is not None:
            problems.append("WRONG: expected the quiet close to swallow its error, got {!r}".format(raised))
    elif masking:
        if raised is fault.err:
            problems.append("MASKED: the injected close error replaced the in-flight exception")
        elif raised is None or not expect(raised):
            problems.append("WRONG: expected the in-flight exception, got {!r}".format(raised))
    elif raised is None:
        problems.append("SILENT: the failing normal-path close did not raise")
    elif raised is not fault.err:
        problems.append("WRONG: expected the injected close error, got {!r}".format(raised))
    return problems


def _st_close_check(ns, vectors):
    """Run each vector green, then under each flip it names, requiring the flip turn it red by its own
    assertion alone. `ns` is the namespace whose close helpers the sites resolve at call time. A vector is
    (label, masking, flips, call, expect). The flips: A, _close_fd_yielding replaced by
    _close_fd_propagating, so every site is back to propagating (red: MASKED); B, the helper always quiet
    (red: SILENT); C, the caller-frame check removed (red: SILENT); R (RECLOSE), the pre-P1 bodies of
    _close_fd_propagating and _close_fd_quietly put back, which fstat the number after a failed close and
    close it again when it looks open (red: REUSE alone, so this leg runs with the PROBE watch off); P, an
    fstat probe after the failed close with no second close (red: PROBE); F, the same probe made with
    fcntl.fcntl(fd, F_GETFD) (red: PROBE; run where fcntl exists); S, _close_fd_quietly replaced by
    the propagating close, so a cleanup loop stops at the first failing close (red: LEAK and WRONG).
    Returns (failures, runs)."""
    prop = ns.get("_close_fd_propagating")

    def quiet(fd):
        try:
            prop(fd)
        except OSError:
            pass

    def frameless(fd):
        if sys.exc_info()[2] is None:
            prop(fd)
            return
        try:
            prop(fd)
        except OSError:
            pass

    def reclose(swallow):
        def body(fd):
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                os.fstat(fd)
            except OSError:
                if swallow:
                    return
                raise first
            try:
                os.close(fd)                              # the retry close(2) warns against
            except OSError:
                pass
            if not swallow:
                raise first
        return body

    def probe(swallow, touch=lambda fd: os.fstat(fd)):
        def body(fd):
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                touch(fd)
            except OSError:
                pass
            if not swallow:
                raise first
        return body

    fcntl = _st_fcntl()

    flips = {"A": ({"_close_fd_yielding": prop}, ("MASKED",), True),
             "B": ({"_close_fd_yielding": quiet}, ("SILENT",), True),
             "C": ({"_close_fd_yielding": frameless}, ("SILENT",), True),
             "R": ({"_close_fd_propagating": reclose(False), "_close_fd_quietly": reclose(True)}, ("REUSE",),
                   False),
             "P": ({"_close_fd_propagating": probe(False), "_close_fd_quietly": probe(True)}, ("PROBE",),
                   True),
             "F": ({"_close_fd_propagating": probe(False, lambda fd: fcntl.fcntl(fd, fcntl.F_GETFD)),
                    "_close_fd_quietly": probe(True, lambda fd: fcntl.fcntl(fd, fcntl.F_GETFD))}, ("PROBE",),
                   True),
             "S": ({"_close_fd_quietly": prop}, ("LEAK", "WRONG"), True)}
    failures, runs = [], 0
    for label, masking, want, call, expect in vectors:
        if fcntl is None:
            want = want.replace("F", "")
        runs += 1
        got = _st_close_run(call, masking, expect)
        if got:
            failures.append("{}: {}".format(label, "; ".join(got)))
        for flip in want:
            swaps, tags, watch = flips[flip]
            real = {name: ns[name] for name in swaps if name in ns}
            ns.update({name: swaps[name] for name in real})
            try:
                red = _st_close_run(call, masking, expect, watch)
            finally:
                ns.update(real)
            runs += 1
            if not real or tuple(p.split(":")[0] for p in red) != tags:
                failures.append("{} under flip {}: expected red by {} alone, got {}".format(
                    label, flip, " and ".join(tags), red or ("green" if real else "no helper to flip")))
    return failures, runs


def _st_helper_vectors(ns):
    """The helpers' own vectors, calling each close helper through `ns` so a flip applies: the harness's
    own watch check, the four _close_fd_yielding vectors, then for each helper `ns` defines, V1 (the
    released number reused inline, once per errno, EINTR and EIO; red under R, P and F) and V2 (reused by a
    real second thread)."""
    import errno

    def devnull(fault, **arm):
        return fault.arm(os.open(os.devnull, os.O_RDONLY), **arm)

    sent = _StSentinel("in flight")

    def mask_finally(fault):
        fd = devnull(fault)
        try:
            raise sent
        finally:
            ns["_close_fd_yielding"](fd)

    def mask_except(fault):
        fd = devnull(fault)
        try:
            raise sent
        except _StSentinel:
            ns["_close_fd_yielding"](fd)
            raise

    def normal(fault):
        ns["_close_fd_yielding"](devnull(fault))

    def inner(fd):
        ns["_close_fd_yielding"](fd)

    def caller_except(fault):
        try:
            raise _StSentinel("handled by the caller")
        except _StSentinel:
            inner(devnull(fault))                         # reached normally from the caller's except block

    def direct(name, **arm):
        def call(fault):
            ns[name](devnull(fault, **arm))
        return call

    def watch(fault):
        """The watch itself: the watched set is exactly every touch below, each on the module it belongs to
        (an fcntl name on fcntl, never on os), each stand-in answers every capability probe as the real call
        does, and after a failed close each watched call on the released number is recorded once, under its
        own name, and os.stat of a path is not; the probes are then cleared and the close error raised, so a
        watch that drops or misplaces a call, misses one, records a path, or hides a call from
        os.supports_dir_fd is red by WRONG."""
        for module, name, real in fault._watched:
            for table in _st_supports(module):
                if (getattr(module, name) in table) != (real in table):
                    raise AssertionError("the stand-in for {} changes a capability probe".format(name))
        fd = devnull(fault)
        try:
            os.close(fd)
        except OSError as exc:
            first = exc
        else:
            raise AssertionError("the armed close did not fail")
        fcntl = _st_fcntl()
        os.stat(os.devnull)
        touches = ((os, "stat", lambda: os.stat(fd)), (os, "fstat", lambda: os.fstat(fd)),
                   (os, "fstatvfs", lambda: os.fstatvfs(fd)), (os, "lseek", lambda: os.lseek(fd, 0, os.SEEK_CUR)),
                   (os, "get_inheritable", lambda: os.get_inheritable(fd)), (os, "isatty", lambda: os.isatty(fd)))
        if fcntl:
            touches += ((fcntl, "fcntl", lambda: fcntl.fcntl(fd, fcntl.F_GETFD)),
                        (fcntl, "flock", lambda: fcntl.flock(fd, fcntl.LOCK_UN)),
                        (fcntl, "lockf", lambda: fcntl.lockf(fd, fcntl.LOCK_UN)),
                        (fcntl, "ioctl", lambda: fcntl.ioctl(fd, 0)))
        watched = sorted((module.__name__, name) for module, name, _real in fault._watched)
        required = sorted((module.__name__, name) for module, name, _touch in touches if hasattr(module, name))
        if watched != required:
            raise AssertionError("the PROBE watch covers {}, expected {}".format(watched, required))
        expected = []
        for module, name, touch in touches:
            if hasattr(module, name):
                expected.append((name, fd))
                try:
                    touch()
                except OSError:
                    pass
        seen, fault.probes[:] = list(fault.probes), []
        if seen != expected:
            raise AssertionError("the PROBE watch recorded {}, expected {}".format(seen, expected))
        raise first

    vectors = (("harness: the PROBE watch records every watched call on a released number", False, "", watch,
                None),)
    if "_close_fd_yielding" in ns:
        vectors += (("helper: finally while an exception unwinds", True, "AR", mask_finally,
                     lambda e: e is sent),
                    ("helper: except handler re-raising", True, "AR", mask_except, lambda e: e is sent),
                    ("helper: normal path", False, "BR", normal, None),
                    ("helper: normal path under a caller's except", False, "BCR", caller_except, None))
    for name, masking in (("_close_fd_propagating", False), ("_close_fd_quietly", None),
                          ("_close_fd_yielding", False)):
        if name not in ns:
            continue
        for errnum in (errno.EINTR, errno.EIO):
            vectors += (("helper {} V1: {} after the number is released and reused".format(
                name, errno.errorcode[errnum]), masking, "RPF", direct(name, errnum=errnum), None),)
        vectors += (("helper {} V2: a second thread takes the released number".format(name), masking, "R",
                     direct(name, thread=True), None),)
    return vectors


def _st_watch_drop_check():
    """The watch self-check's own flips: with fcntl.flock, then fcntl.ioctl, dropped from _ST_WATCHED, the
    watch vector must be red by WRONG alone, so losing either call from the watched set cannot pass
    unnoticed. Run where fcntl exists (elsewhere there is no fcntl name to drop). Returns (failures, runs)."""
    if _st_fcntl() is None:
        return [], 0
    label, masking, _flips, call, expect = _st_helper_vectors(globals())[0]
    real = _ST_WATCHED
    failures, runs = [], 0
    for dropped in ("flock", "ioctl"):
        globals()["_ST_WATCHED"] = (real[0], tuple(name for name in real[1] if name != dropped))
        try:
            red = _st_close_run(call, masking, expect)
        finally:
            globals()["_ST_WATCHED"] = real
        runs += 1
        if [problem.split(":")[0] for problem in red] != ["WRONG"]:
            failures.append("{} with {} dropped from the watch: expected red by WRONG alone, got {}".format(
                label, dropped, red or "green"))
    return failures, runs


def _st_site_vectors(base):
    """One representative finally site (_read_at), one except-handler site (_read_contained), and V3, the
    sibling closes of a contained-walk cleanup loop (_open_dir_contained)."""
    ns = globals()
    with open(os.path.join(base, "f"), "wb") as fh:
        fh.write(b"payload")
    os.makedirs(os.path.join(base, "s1", "s2", "s3"))
    sent = _StSentinel("in flight at _read_at")

    def read_at(raise_sent):
        def call(fault):
            real = ns["_read_fd"]

            def spy(fd, cap=None):
                fault.arm(fd)
                if raise_sent:
                    raise sent
                return real(fd, cap=cap)
            dfd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
            try:                                          # the seam is swapped only where its restore runs
                ns["_read_fd"] = spy
                _read_at(dfd, "f", "f")
            finally:
                ns["_read_fd"] = real
                os.close(dfd)
        return call

    def read_contained_missing(fault):
        real = ns["_open_parent"]

        def spy(root_fd, relpath):
            pfd, name = real(root_fd, relpath)
            return fault.arm(pfd), name
        dfd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            ns["_open_parent"] = spy
            _read_contained(dfd, "missing")
        finally:
            ns["_open_parent"] = real
            os.close(dfd)

    def handler_error(e):
        return (type(e) is JournalError and str(e).startswith("cannot read contained file")
                and isinstance(e.__context__, FileNotFoundError))

    def siblings(fault):
        real_open = os.open

        def spy(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "s1" and dir_fd is not None:
                fault.arm(fd)                             # the FIRST of the three closes the loop makes
            return fd
        dfd = real_open(base, os.O_RDONLY | os.O_DIRECTORY)
        os.open = spy
        try:
            os.close(_open_dir_contained(dfd, "s1/s2/s3"))
        finally:
            os.open = real_open
            os.close(dfd)

    return (("site _read_at: finally while an exception unwinds", True, "AR", read_at(True),
             lambda e: e is sent),
            ("site _read_at: normal path", False, "BR", read_at(False), None),
            ("site _read_contained: except handler raising its own JournalError", True, "AR",
             read_contained_missing, handler_error),
            ("site _open_dir_contained V3: the first cleanup close fails, the walk completes and its "
             "siblings still close", None, "RS", siblings, None))


def _st_c1_control():
    """C1-osread's positive control: a readable descriptor returns its bytes."""
    rfd, wfd = os.pipe()
    try:
        try:
            os.write(wfd, b"journal bytes")
        finally:
            os.close(wfd)
        return _read_fd(rfd) == b"journal bytes"
    finally:
        os.close(rfd)


def _st_c1_osread():
    """C1-osread: with os.read failing EIO, _read_fd raises a JournalError, never a raw OSError."""
    import errno
    rfd, wfd = os.pipe()
    saved_read = os.read

    def failing_read(_fd, _n):
        raise OSError(errno.EIO, "simulated read error")

    os.read = failing_read
    try:
        try:
            _read_fd(rfd)
            got = "no-raise"
        except JournalError:
            got = "journal-error"
        except OSError:
            got = "raw-oserror"
    finally:
        os.read = saved_read
        os.close(rfd)
        os.close(wfd)
    return got == "journal-error"


def _st_n2a():
    """N2a: a close that raises EBADF returns cleanly; a raw os.close would raise it to the caller. -1 is
    never a descriptor, so the EBADF is the kernel's own and no open number is touched."""
    try:
        _close_fd_quietly(-1)
    except OSError:
        return False
    return True


def _st_n2d():
    """N2d: the helper's one os.close raises EIO while a stderr write would raise OSError too (a broken
    stderr): the helper must RETURN, having made exactly one close (P1, #378: no probe, no second close).
    The close is a stub, so no open number is touched. Returns (returned, single_close)."""
    import errno
    saved_close, saved_stderr = os.close, sys.stderr
    closes = []

    class BrokenStderr:
        def write(self, *_a, **_k):
            raise OSError(errno.EIO, "broken stderr write")

        def flush(self, *_a, **_k):
            raise OSError(errno.EIO, "broken stderr flush")

    def failing_close(fd):
        closes.append(fd)
        raise OSError(errno.EIO, "injected close failure")

    returned = True
    try:
        os.close = failing_close                     # the helper's close now raises
        sys.stderr = BrokenStderr()                  # ... and a diagnostic write would raise too
        try:
            _close_fd_quietly(-1)
        except BaseException:
            returned = False
    finally:
        os.close, sys.stderr = saved_close, saved_stderr
    return returned, closes == [-1]


def _st_descriptor_helper_checks():
    """Descriptor-helper vectors, ported from the retired import engine's self-test (their only former
    home), each driving the helper directly, with a patched os primitive where the contract needs one:
      C1-osread  _read_fd converts a read-time OSError to a JournalError, never a raw OSError or a
                 truncated result (with a positive control: a readable descriptor returns its bytes).
      N2a        _close_fd_quietly swallows a close-time EBADF rather than propagating it.
      N2d        _close_fd_quietly never raises and closes exactly once (P1, #378): its one close fails while
                 stderr itself is broken, and the helper still returns without a second close.
    No store, no journal and no subprocess; every patched primitive is restored in a finally, and each
    leg's descriptors are its own, closed once. Returns (failures, checks)."""
    n2d_returned, n2d_single = _st_n2d()
    results = (("C1-osread-control-reads", _st_c1_control()),
               ("C1-osread-converts-to-journalerror", _st_c1_osread()),
               ("N2a-close-quietly-swallows-oserror", _st_n2a()),
               ("N2d-close-quietly-nonthrow", n2d_returned),
               ("N2d-close-quietly-single-close", n2d_single))
    return [name for name, ok in results if ok is not True], len(results)


def self_test():
    """The #378 close vectors for the three close helpers (V1, V2), three representative _journal sites,
    and the sibling closes of a cleanup loop (V3), each green and each red under its flip; then the
    descriptor-helper vectors (_st_descriptor_helper_checks). Returns 0 clean, 1 a failure, 2
    cannot-evaluate."""
    import shutil
    import tempfile
    try:
        base = tempfile.mkdtemp(prefix="aiqt-journal-selftest-")
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        vectors = _st_helper_vectors(globals()) + _st_site_vectors(base)
        failures, runs = _st_close_check(globals(), vectors)
        drop_failures, drop_runs = _st_watch_drop_check()
        failures, runs = failures + drop_failures, runs + drop_runs
    finally:
        shutil.rmtree(base, ignore_errors=True)
    helper_failures, checks = _st_descriptor_helper_checks()
    if failures or helper_failures:
        print("JOURNAL SELF-TEST: FAIL ({} of {} close-vector runs and {} of {} descriptor-helper checks "
              "failed)".format(len(failures), runs, len(helper_failures), checks))
        for f in failures + helper_failures:
            print("  FAILED: {}".format(f))
        return 1
    print("JOURNAL SELF-TEST: PASS ({} close vectors, {} runs including each flip leg red; {} descriptor-helper "
          "checks)".format(len(vectors), runs, checks))
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    print("usage: _journal.py --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
