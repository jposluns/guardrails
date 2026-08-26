#!/usr/bin/env python3
"""Crash-durable transactional cutover journal (VER-CORE 9.3), an importable engine module. Stdlib only.

Used by tools/migrate.py (cutover, recover, un-adopt reverse replay). tools/pin.py (Section 12 step 7,
NOT built in this VC-6 slice) will reuse the low-level contained-apply helpers for the corrupt-state
recovery carve-out ONLY; the ordinary re-pin preimage copy deliberately uses none of this journal.

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

Exit convention of the CLIs built on this module: 0 clean/NA, 1 finding, 2 malformed or read error.
"""
import hashlib
import json
import os
import stat
import time
from pathlib import Path

MAGIC = b"AIQTJ1"
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
        return os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    finally:
        os.close(pfd)


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


def acquire_lock(journal_root, session_id):
    """O_CREAT|O_EXCL lock with owner identity (9.3 step 2). Raises JournalError (mapped to a refuse-to-
    proceed) when a lock already exists: one open transaction at a time; a possibly-live owner is never
    seized here (breaking a stale lock is the caller's explicit reconcile step in recover)."""
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
        os.write(fd, json.dumps(owner, sort_keys=True).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_path_dir(journal_root)
    _kill_point("after-lock")
    return lock


def read_lock_owner(journal_root):
    """The recorded owner dict, or None when no lock file is present. JournalError on an unreadable or
    malformed lock (fail-closed: an unreadable lock is never treated as absent)."""
    lock = Path(journal_root) / "lock"
    try:
        raw = lock.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise JournalError("cannot read journal lock ({})".format(exc))
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise JournalError("journal lock is not valid JSON ({})".format(exc))


def owner_confirmed_dead(owner):
    """True ONLY on positive evidence of death. EPERM, a live pid, or any ambiguity reads as possibly-
    live (never seized). Where a start time was recorded and is readable, a differing start time for the
    same pid also confirms death (PID reuse). Residual: PID reuse on a platform with no readable start
    time cannot be distinguished, so it reads as possibly-live and the lock is not broken."""
    pid = owner.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    recorded = owner.get("pid-start")
    if recorded:
        now = _pid_start(pid)
        return bool(now) and now != recorded
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
    explicit reconcile step (break_stale_lock), never this release path."""
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


def break_stale_lock(journal_root, owner):
    """Break a lock whose recorded owner is CONFIRMED DEAD (stale-lock recovery, 9.3 step 1). Re-confirms
    death before unlinking (defence in depth): a lock not confirmed dead is NEVER broken. The caller
    re-acquires via O_EXCL immediately after, so a racing writer that re-created the lock makes that
    acquire fail closed rather than seizing a foreign lock."""
    if not owner_confirmed_dead(owner):
        raise JournalError("refusing to break a journal lock whose owner is not confirmed dead")
    journal_root = Path(journal_root)
    lock = journal_root / "lock"
    try:
        os.unlink(str(lock))
    except FileNotFoundError:
        return
    _fsync_path_dir(journal_root)


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


def _verify_lookup_prestate(root_fd, relpath, prestate):
    st = _lstat_contained(root_fd, relpath)
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
    data, _ = _read_contained(root_fd, relpath)
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


# --- apply (contained, fd-bound) ----------------------------------------------------------------------

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
                    _maybe_torn_payload(fd, data, i)
                    _write_all(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            elif kind == "remove":
                _verify_lookup_prestate(root_fd, op["path"], op["prestate"])
                os.unlink(name, dir_fd=pfd)
            elif kind == "mkdir":
                os.mkdir(name, op["poststate"]["mode"], dir_fd=pfd)
                os.chmod(name, op["poststate"]["mode"], dir_fd=pfd, follow_symlinks=False)
            elif kind == "rmdir":
                _verify_lookup_prestate(root_fd, op["path"], op["prestate"])
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
    st = _lstat_contained(root_fd, path)                  # soft: None if the path OR a parent is absent
    if kind == "create" and st is None:
        return                                            # already absent
    if kind == "mkdir" and st is None:
        return                                            # already absent
    try:
        pfd, name = _open_parent(root_fd, path)
    except OSError as exc:
        raise JournalError("cannot restore {!r} ({})".format(path, exc))
    try:
        if kind == "create":                              # created file: delete it back to absence
            os.unlink(name, dir_fd=pfd)
        elif kind == "mkdir":                             # created dir: remove it back to absence
            if not stat.S_ISDIR(st.st_mode):
                raise JournalError("cannot restore {!r}: expected a created directory to remove, found a "
                                   "non-directory".format(path))
            os.rmdir(name, dir_fd=pfd)
        elif kind == "rmdir":                             # removed dir: recreate it (or re-apply its mode)
            if st is None:                                # absent: recreate then set the exact mode
                os.mkdir(name, prestate["mode"], dir_fd=pfd)
                _maybe_torn_mode(root_fd, path, op_index)   # crash BETWEEN the mkdir and the chmod
                os.chmod(name, prestate["mode"], dir_fd=pfd, follow_symlinks=False)
            elif stat.S_ISDIR(st.st_mode):               # already recreated: re-apply the prestate mode
                os.chmod(name, prestate["mode"], dir_fd=pfd, follow_symlinks=False)
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

def _validate_terminal_agreement(frames):
    """Defence in depth (fix #2): a well-formed engine journal has exactly one INTENT, first, and terminal
    frames (COMPLETE / ROLLBACK-IN-PROGRESS / ROLLBACK-COMPLETE) that agree with it on the txn id and
    appear at most once. Reject a stray or crafted frame that disagrees on the txn id, duplicates a
    terminal record, contradicts one (COMPLETE with a rollback record), or orders a terminal frame before
    the INTENT. Recovery trusts its own checksummed framing; a crafted valid-checksum journal is outside
    the accident-recovery model (see the module Guarantee-scope note). JournalError on any violation."""
    intent_txn = None
    counts = {F_INTENT: 0, F_COMPLETE: 0, F_RIP: 0, F_RC: 0}
    for ftype, obj in frames:
        counts[ftype] = counts.get(ftype, 0) + 1
        txn = obj.get("txn") if isinstance(obj, dict) else None
        if ftype == F_INTENT:
            if intent_txn is not None:
                raise JournalError("duplicate INTENT frame in journal")
            intent_txn = txn
        else:
            if intent_txn is None:
                raise JournalError("terminal frame {} precedes any INTENT frame".format(ftype))
            if txn != intent_txn:
                raise JournalError("frame {} txn id disagrees with the INTENT txn id".format(ftype))
    if counts[F_COMPLETE] > 1 or counts[F_RIP] > 1 or counts[F_RC] > 1:
        raise JournalError("duplicate terminal frame in journal")
    if counts[F_COMPLETE] and (counts[F_RIP] or counts[F_RC]):
        raise JournalError("contradictory terminal frames (COMPLETE alongside a rollback record)")
    if counts[F_RC] and not counts[F_RIP]:
        raise JournalError("ROLLBACK-COMPLETE without a ROLLBACK-IN-PROGRESS frame")


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
    except JournalError:
        # A prestate mismatch (a hostile or racing tree): roll back from the durable preimages and fail.
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
    when it is still open (INTENT or ROLLBACK-IN-PROGRESS without a terminal). JournalError on a corrupt
    mid-log frame (fail-closed)."""
    frames, torn, _ = read_frames(txn_dir)
    types = [t for t, _ in frames]
    if F_INTENT not in types:
        return True                                       # nothing opened (or torn INTENT): not open
    return F_COMPLETE in types or F_RC in types


def build_inverse_ops(intent_ops):
    """The un-adopt engine primitive (10.6): from a terminal transaction's INTENT ops, build the inverse
    op list that returns the tree to that transaction's prestate, in reverse dependency order. write ->
    write the prior bytes back; create -> remove; remove -> create the prior bytes; mkdir -> rmdir;
    rmdir -> mkdir. The caller replays these as a NEW journaled transaction whose staged_reader serves
    the ORIGINAL transaction's retained preimages. Cross-transaction ordering, authorization, repoint
    inversion, and pin removal are Section 12 step 7 (pin.py), NOT this VC-6 slice."""
    inverse = []
    for op in reversed(intent_ops):
        pre, kind, path = op["prestate"], op["op"], op["path"]
        if kind == "write":
            inverse.append({"op": "write", "path": path,
                            "poststate": {"kind": "file", "content-sha256": pre["sha256"]},
                            "_source": op})
        elif kind == "create":
            inverse.append({"op": "remove", "path": path, "poststate": {"kind": "absent"}})
        elif kind == "remove":
            inverse.append({"op": "create", "path": path,
                            "poststate": {"kind": "file", "mode": pre["mode"],
                                          "content-sha256": pre["sha256"]},
                            "_source": op})
        elif kind == "mkdir":
            inverse.append({"op": "rmdir", "path": path, "poststate": {"kind": "absent"}})
        elif kind == "rmdir":
            inverse.append({"op": "mkdir", "path": path,
                            "poststate": {"kind": "dir", "mode": pre["mode"]}})
        else:
            raise JournalError("cannot invert unknown op kind {!r}".format(kind))
    return inverse
