#!/usr/bin/env python3
"""Crash-durable transactional cutover journal (VER-CORE 9.3), an importable engine module. Stdlib only.

Used by tools/migrate.py for cutover and recover; it also carries the INERT reverse-replay primitive
(build_inverse_ops, 10.6) that Section 12 step 7's un-adopt will build on but which THIS VC-6 slice only
exercises in the self-test (no un-adopt subcommand is wired here). tools/pin.py (Section 12 step 7, NOT
built in this VC-6 slice) will reuse the low-level contained-apply helpers for the corrupt-state recovery
carve-out ONLY; the ordinary re-pin preimage copy deliberately uses none of this journal.

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
"""
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


def _open_parent(root_fd, relpath):
    """Open the parent directory of relpath by walking each intermediate component beneath root_fd with
    O_DIRECTORY|O_NOFOLLOW (a symlinked component raises rather than redirects the walk). Returns
    (parent_fd, final_name); the caller closes parent_fd, and root_fd is never closed (the single-
    component case dups it). A MISSING intermediate component raises FileNotFoundError (a clean signal
    the caller reads as absent, since a missing parent means the target is absent); any other error (a
    non-directory or symlinked intermediate component) raises JournalError."""
    parts = _check_rel(relpath)
    cur = root_fd
    opened = []
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
        for fd in opened:
            os.close(fd)
    return pfd, parts[-1]


def _read_fd(fd):
    chunks = []
    while True:
        block = os.read(fd, _READ_CHUNK)
        if not block:
            break
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
        os.close(pfd)


def _lstat_at(pfd, name):
    """lstat the final component 'name' beneath an ALREADY-OPEN parent fd, or None when it is absent.
    Never follows a final-component symlink. E1: binds the prestate check to the SAME parent handle the
    mutation uses (9.3 step 4, spec 1291/1300: check AND mutate beneath one pre-opened directory handle),
    so an ancestor swap between the check and the mutation cannot redirect either onto a different tree."""
    try:
        return os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read_at(pfd, name, relpath):
    """Read the final component's bytes through an O_NOFOLLOW fd opened beneath the SAME parent handle,
    confirming on the opened fd it is a regular file. The fd-bound sibling of _read_contained, used where
    the read MUST bind to the parent handle the mutation uses (E1, 9.3 step 4). JournalError on a symlink,
    a non-regular file, or a read error."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise JournalError("contained path {!r} is not a regular file".format(relpath))
        return _read_fd(fd), st
    finally:
        os.close(fd)


def _read_contained(root_fd, relpath):
    """Read a contained regular file's bytes through an O_NOFOLLOW fd, confirming on the opened fd that
    it is a regular file. JournalError on a symlink, a non-regular file, a missing path, or a read
    error."""
    try:
        pfd, name = _open_parent(root_fd, relpath)
    except OSError as exc:                                 # includes FileNotFoundError
        raise JournalError("cannot read contained file {!r} ({})".format(relpath, exc))
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
    except OSError as exc:
        os.close(pfd)
        raise JournalError("cannot read contained file {!r} ({})".format(relpath, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise JournalError("contained path {!r} is not a regular file".format(relpath))
        return _read_fd(fd), st
    finally:
        os.close(fd)
        os.close(pfd)


def _fsync_dir_fd(fd):
    os.fsync(fd)


def _fsync_parent(root_fd, relpath):
    pfd, _ = _open_parent(root_fd, relpath)
    try:
        os.fsync(pfd)
    finally:
        os.close(pfd)


def _fsync_path_dir(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_contained_dir(pfd, name):
    """Fix #1 (directory mode durability): fsync a just-created/recreated directory's OWN fd, so its mode
    (set via chmod) is durable before COMPLETE, not merely the parent link. Open it O_DIRECTORY|O_NOFOLLOW
    beneath its bound parent fd (a swapped-in symlink raises rather than redirecting the fsync), fsync the
    dir fd, then close it; the caller fsyncs the parent separately."""
    dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# --- frame layer (checksummed framing; a torn final frame is detectably unwritten) --------------------

def _frame(ftype, payload):
    digest = hashlib.sha256(payload).hexdigest()
    header = (MAGIC + b" " + ftype.encode() + b" " + str(len(payload)).encode()
              + b" " + digest.encode() + b"\n")
    return header + payload + b"\n"


def publish(txn_dir, ftype, obj):
    """Append one checksummed-framed record (9.3 steps 4 and 7 discipline), fsync the log and the txn
    directory. A torn write of THIS frame is detectably-unwritten to read_frames; the torn:<TYPE>
    injection writes a half frame, fsyncs it, and dies, exactly as a power loss mid-write would."""
    if ftype not in FRAME_TYPES:
        raise JournalError("refusing to publish unknown frame type {!r}".format(ftype))
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    frame = _frame(ftype, payload)
    log = Path(txn_dir) / "frames.log"
    torn = os.environ.get(KILL_ENV, "") == "torn:" + ftype
    with open(log, "ab") as fh:
        if torn:
            half = frame[: max(1, len(frame) // 2)]
            fh.write(half)
            fh.flush()
            os.fsync(fh.fileno())
            os._exit(137)
        fh.write(frame)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_path_dir(txn_dir)
    _kill_point("after-publish-" + ftype)


def read_frames(txn_dir):
    """Parse frames.log. Returns (frames, torn, good_len): frames is [(ftype, obj)] for every checksum-
    valid frame in order; torn is True when the FINAL region is a detectably incomplete frame; good_len
    is the byte length of the clean prefix (everything before a torn tail), so recovery can truncate the
    tail before appending a terminal frame and keep the log parseable and idempotent. Any malformation
    NOT at the tail raises JournalError (9.3: a FAIL state, never silently skipped)."""
    log = Path(txn_dir) / "frames.log"
    try:
        raw = log.read_bytes()
    except FileNotFoundError:
        return [], False, 0
    except OSError as exc:
        raise JournalError("cannot read journal frames.log ({})".format(exc))
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


def _truncate_log(txn_dir, good_len):
    """Cut a torn tail off frames.log so a fresh terminal frame appends onto a clean prefix. Fsync'd."""
    log = Path(txn_dir) / "frames.log"
    fd = os.open(str(log), os.O_RDWR)
    try:
        os.ftruncate(fd, good_len)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_path_dir(txn_dir)


def _first(frames, ftype):
    for t, obj in frames:
        if t == ftype:
            return obj
    return None


# --- lock (O_EXCL owner identity; stale-lock recovery is the caller's reconcile step) -----------------

def _pid_start(pid):
    """The process start-time field from /proc/<pid>/stat (Linux only; empty string elsewhere). Read
    from after the last ')' so a program name containing ') ' cannot shift the field split."""
    try:
        with open("/proc/{}/stat".format(pid), "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
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
    create is itself the mutual-exclusion point and refuses to follow a final-component symlink."""
    journal_root = Path(journal_root)
    lock = journal_root / "lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise JournalError("journal lock {} already held: an open transaction exists (run recover)"
                           .format(lock))
    try:
        owner = {"uid": os.getuid(), "pid": os.getpid(), "session": session_id,
                 "pid-start": _pid_start(os.getpid()),
                 "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_all(fd, json.dumps(owner, sort_keys=True).encode())   # loop: a short write cannot leave a malformed lock
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_path_dir(journal_root)
    _kill_point("after-lock")
    return lock


def read_lock_owner(journal_root):
    """The recorded owner dict, or None when no lock file is present. JournalError on an unreadable or
    malformed lock (fail-closed: an unreadable lock is never treated as absent). HARDENING: the decoded
    JSON MUST be an object, so owner_confirmed_dead / _owner_is_current can call .get without a non-dict
    (a bare list/int/string) reaching them as an uncaught AttributeError. The lock is opened beneath the
    journal-dir handle with O_NOFOLLOW and confirmed a regular file on the opened fd (mirror B1), so a
    symlinked or non-regular `lock` fails closed rather than redirecting the read off-tree."""
    journal_root = Path(journal_root)
    try:
        jr_fd = os.open(str(journal_root), os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise JournalError("cannot open journal root ({})".format(exc))
    try:
        try:
            lfd = os.open("lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=jr_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:                            # ELOOP on a symlinked lock, or any read error: fail closed
            raise JournalError("cannot read journal lock ({})".format(exc))
        try:
            if not stat.S_ISREG(os.fstat(lfd).st_mode):
                raise JournalError("journal lock is not a regular file (fail-closed)")
            raw = _read_fd(lfd)
        finally:
            os.close(lfd)
    finally:
        os.close(jr_fd)
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


def _journal_txn_dirs(journal_root):
    """The transaction subdirectories of a journal root, sorted (the reconcile order). Skips the lock and
    arbitration files and any stray non-directory entry."""
    out = []
    for entry in sorted(Path(journal_root).iterdir()):
        if entry.is_dir():
            out.append(entry)
    return out


def reconcile_and_claim_stale(journal_root, root_fd, session_id):
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
        if not stat.S_ISREG(os.fstat(afd).st_mode):          # trust it as the arbitration inode only if regular
            raise JournalError("arbitration file {} is not a regular file; refusing to break the stale "
                               "lock (fails closed)".format(arb))
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
            for txn_dir in _journal_txn_dirs(journal_root):
                recover(txn_dir, root_fd)
            for txn_dir in _journal_txn_dirs(journal_root):
                if not is_terminal(txn_dir):
                    raise JournalError("journal {} did not reconcile to terminal; refusing to break the "
                                       "stale lock (fail-closed)".format(txn_dir.name))
            try:                                             # every journal terminal: NOW break the stale lock
                os.unlink(str(journal_root / "lock"))
            except FileNotFoundError:
                pass
            _fsync_path_dir(journal_root)
            acquire_lock(journal_root, session_id)           # O_EXCL under the arbitration lock
            return "acquired"
        finally:
            fcntl.flock(afd, fcntl.LOCK_UN)
    finally:
        os.close(afd)


# --- preimages (durably FIRST; the whole reversal is reconstructable from them alone) -----------------

def capture_preimages(txn_dir, root_fd, ops):
    """9.3 step 3: full prior bytes plus metadata, durably, FIRST. Mutates each op in place, adding its
    prestate. Creates/mkdirs record an explicit PRIOR-ABSENCE and REFUSE if the path already exists;
    directory removals record existence and mode with no payload; every other touched file's full prior
    bytes are copied to preimages/<seq> and fsync'd. Fsync the preimages dir and the txn dir at the end
    so the whole reversal is durable before INTENT opens the transaction."""
    txn_dir = Path(txn_dir)
    pre = txn_dir / "preimages"
    pre.mkdir(exist_ok=True)
    # E2: a REVERSE (un-adopt) transaction pins, per inverse op, the SOURCE poststate it expects to still
    # hold. Verify every pin here, at capture, so a non-engine write that landed after the caller's drift
    # check but before capture is caught (fail closed) rather than captured-and-clobbered (spec 1319/1323:
    # quiescence excludes the post-check/pre-replay race; this makes the drift check contiguous with the
    # capture under the held lock). Cutover ops carry no pin, so this is inert for them.
    for op in ops:
        exp = op.get("source-poststate")
        if exp is not None:
            _verify_source_poststate(root_fd, op["path"], exp)
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
                raise JournalError("{}: expected an existing directory before rmdir".format(op["path"]))
            op["prestate"] = {"kind": "dir", "mode": stat.S_IMODE(st.st_mode)}
        else:                                             # write, remove: file prestate with payload
            if st is None or not stat.S_ISREG(st.st_mode):
                raise JournalError("{}: expected an existing regular file before {}"
                                   .format(op["path"], kind))
            data, _fst = _read_contained(root_fd, op["path"])
            ref = str(seq)
            payload_path = pre / ref
            payload_path.write_bytes(data)
            with open(payload_path, "rb") as fh:
                os.fsync(fh.fileno())
            op["prestate"] = {"kind": "file", "mode": stat.S_IMODE(st.st_mode),
                              "size": len(data), "payload": ref,
                              "sha256": hashlib.sha256(data).hexdigest()}
        _kill_point("after-preimage-{}".format(seq))
    _fsync_path_dir(pre)
    _fsync_path_dir(txn_dir)
    _kill_point("after-preimages")


# --- prestate verification and post-state verification (domain separated per kind) --------------------

def _verify_fd_prestate(fd, prestate, where):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise JournalError("{}: target is not a regular file at apply time".format(where))
    if stat.S_IMODE(st.st_mode) != prestate["mode"]:
        raise JournalError("{}: mode changed since preimage capture".format(where))
    data = _read_fd(fd)
    if hashlib.sha256(data).hexdigest() != prestate["sha256"]:
        raise JournalError("{}: content changed since preimage capture".format(where))


def _verify_prestate_at(pfd, name, relpath, prestate):
    """E1 (spec 1291/1300): verify a lookup-based prestate (a dir for rmdir, a regular file for remove)
    through the SAME already-open parent fd the mutation will use, by bare 'name' with no-follow, so an
    ancestor swap between the check and the unlinkat/rmdir cannot redirect either. Supersedes a re-walk
    from root_fd, which bound the check to a freshly-resolved parent rather than the one bound at apply."""
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
    data, _ = _read_at(pfd, name, relpath)
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
    if not (isinstance(expected, str) and _HEX64_RE.match(expected)):
        raise JournalError("{}: poststate content-sha256 must be a 64-hex lowercase digest (absent or "
                           "malformed; the staged-digest check is never silently skipped)".format(op["path"]))
    if hashlib.sha256(data).hexdigest() != expected:
        raise JournalError("{}: staged payload bytes changed since planning (digest does not match the "
                           "INTENT poststate content-sha256)".format(op["path"]))


def apply_ops(root_fd, ops, staged_reader):
    """9.3 step 5: fd-bound prestate check and mutation beneath the pre-opened directory handle, no-
    follow, by final component; never a re-resolved absolute path between check and write. ops are in
    dependency order (parents before children for creates, children before parents for removes), so
    reversed(ops) is the normative reverse-dependency rollback order. Every write is fsync'd and every
    touched entry's parent directory is fsync'd (step 6). Any prestate mismatch raises JournalError and
    the caller rolls back from the preimages."""
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
                finally:
                    os.close(fd)
            elif kind == "create":
                fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             op["poststate"]["mode"], dir_fd=pfd)
                try:
                    os.fchmod(fd, op["poststate"]["mode"])   # pin exact perms (umask independence)
                    data = staged_reader(op)
                    _verify_staged_digest(op, data)          # C3: staged bytes must match the INTENT digest
                    _maybe_torn_payload(fd, data, i)
                    _write_all(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
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
            os.close(pfd)
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

def _restore_preimage(txn_dir, root_fd, op, op_index=0):
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
            data = (txn_dir / "preimages" / prestate["payload"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != prestate["sha256"]:
                raise JournalError("preimage for {!r} does not match recorded prestate digest".format(path))
            if st is None:
                _recreate_file(pfd, name, data, prestate["mode"])
            elif stat.S_ISREG(st.st_mode):
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=pfd)
                try:
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    _write_all(fd, data)
                    os.fchmod(fd, prestate["mode"])
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:                                     # a racing external writer left a non-regular file where a regular file is expected: fail closed
                raise JournalError("cannot restore {!r}: unexpected non-regular file at restore time".format(path))
        os.fsync(pfd)
    except OSError as exc:
        raise JournalError("cannot restore {!r} ({})".format(path, exc))
    finally:
        os.close(pfd)


def _recreate_file(pfd, name, data, mode):
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode, dir_fd=pfd)
    try:
        os.fchmod(fd, mode)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


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


def classify_state(txn_dir):
    """Classify a transaction's DURABLE journal state via the C2 state machine (never a bare boolean).
    Returns 'nothing-opened' (no INTENT: pre-INTENT/capture-phase failure, nothing applied), 'complete',
    'rolled-back' ([INTENT,RIP,RC] terminal rollback), or 'open' (INTENT present without a terminal
    COMPLETE or RC). JournalError on a corrupt or invalid-sequence journal (fail-closed). A torn tail is
    treated as never written (read_frames), consistent with recover()."""
    frames, _torn, _ = read_frames(txn_dir)
    _validate_terminal_agreement(frames)
    types = [t for t, _ in frames]
    if F_INTENT not in types:
        return "nothing-opened"
    if F_COMPLETE in types:
        return "complete"
    if F_RC in types:
        return "rolled-back"
    return "open"


def recover(txn_dir, root_fd):
    """Reconcile one transaction directory to a terminal state from its journal ALONE, at every crash
    point, both directions, idempotently. Returns one of: nothing-opened, terminal, rolled-forward,
    rolled-back. A torn tail is truncated before any terminal frame is appended so the log stays
    parseable and a second recover is a no-op."""
    frames, torn, good_len = read_frames(txn_dir)
    if torn:
        _truncate_log(txn_dir, good_len)
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
            publish(txn_dir, F_COMPLETE, {"txn": intent["txn"]})
            return "rolled-forward"
        publish(txn_dir, F_RIP, {"txn": intent["txn"]})
    total = len(ops)
    for j, op in enumerate(reversed(ops)):
        _restore_preimage(txn_dir, root_fd, op, total - 1 - j)
        _kill_point("after-restore-{}".format(total - 1 - j))
    publish(txn_dir, F_RC, {"txn": intent["txn"]})
    return "rolled-back"


# --- transaction driver (the shared cutover primitive migrate.py builds on) ---------------------------

def run_transaction(root_fd, journal_root, txn_id, header, ops, staged_reader, session_id):
    """Drive one cutover transaction through the seven normative steps under a fresh O_EXCL lock, rolling
    back from preimages on any prestate mismatch during apply. Assumes require_containment already
    passed. Returns 'complete' on success or raises JournalError (the caller maps to exit 2). The lock is
    released by the caller's higher-level flow via release_lock after a terminal outcome."""
    txn_dir = Path(journal_root) / txn_id
    txn_dir.mkdir(parents=True, exist_ok=False)
    _fsync_path_dir(journal_root)
    capture_preimages(txn_dir, root_fd, ops)
    publish(txn_dir, F_INTENT, {"txn": txn_id, "header": header, "ops": ops})
    try:
        apply_ops(root_fd, ops, staged_reader)
        # C3: COMPLETE must mean every poststate was installed. Verify ALL domain-separated post-states
        # before publishing COMPLETE; if any fails, do NOT publish COMPLETE, roll back, and fail closed.
        if not all(_poststate_verifies(root_fd, op) for op in ops):
            raise JournalError("post-apply poststate verification failed; refusing to publish COMPLETE")
    except JournalError:
        # A prestate mismatch, a staged-digest mismatch, or a failed poststate (a hostile or racing tree):
        # roll back from the durable preimages and fail.
        publish(txn_dir, F_RIP, {"txn": txn_id})
        total = len(ops)
        for j, op in enumerate(reversed(ops)):
            _restore_preimage(txn_dir, root_fd, op, total - 1 - j)
        publish(txn_dir, F_RC, {"txn": txn_id})
        raise
    publish(txn_dir, F_COMPLETE, {"txn": txn_id})
    return "complete"


def is_terminal(txn_dir):
    """True when the transaction has a durable terminal record (COMPLETE or ROLLBACK-COMPLETE); False
    when it is still open (INTENT or ROLLBACK-IN-PROGRESS without a terminal). Invokes the SAME C2 state
    machine as recover() so the two classify identically; a corrupt mid-log frame or an invalid frame
    sequence is a JournalError (fail-closed)."""
    frames, torn, _ = read_frames(txn_dir)
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
