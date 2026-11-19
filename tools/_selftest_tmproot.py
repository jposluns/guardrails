"""Owned temporary roots for the two long hook self-tests (tools/selftest_aiqt_hooks.py and
tools/selftest_orch_hooks.py): a marker written into each root when it is made, a checked removal
that reports a root it could not remove instead of silencing the failure, and a sweep, run by each of
those suites once its own root is made, of that suite's own stale marked roots in the directory that
root was made in (and nowhere else).

A finally or a context manager removes a root on success, on a reported failure and on an exception,
but never when its owner is killed (SIGKILL, an out-of-memory kill, a launcher timeout that kills the
process). The sweep is the recovery path for those. It removes a directory only when all of these
hold: it sits directly in one of the parents it is given; its name starts with the suite's own
prefix; it is a real directory (not a symbolic link) owned by this user; it holds a regular-file
marker (MARKER) naming the same suite and this host; the marker's recorded creation time and its
modification time are both at least STALE_SECONDS (24 hours) old; and the owner process the marker
records no longer exists. An unmarked directory (one made before the marker existed, or by another
tool) and one whose owner still runs are left alone; a process identifier the system has reused
reads as a live owner, so reuse keeps a root rather than removing one. There is deliberately no
broad aiqt-* pattern here: each suite sweeps only its own prefix and suite name.

Removal fails closed. Nothing is removed that is not proven to be the pinned directory, and success
is reported only when the pinned directory is confirmed gone; a root that is left behind is always
reported, and a reported leftover is the accepted cost of every doubt. Exactly:
- remove_root removes only the directory its RootPin holds. A suite's own root is pinned when the run
  makes it: each suite calls pin_root on the path _fixture_tmpdir returned, as the first step after
  that call and before the marker is written, and keeps the RootPin it returns (the directory's device
  and inode, and an O_DIRECTORY and O_NOFOLLOW descriptor of it held open, so that inode number cannot
  be given to another directory while the run lasts) until its finally passes the pin to remove_root.
  When pin_root failed or was never reached, the finally passes None, and remove_root removes nothing
  at all and reports the root as left behind (there is no unpinned removal to fall back on). A
  self-test row that removes a fixture of its own pins it first with pin_root.
- remove_root judges the root by the pin alone, never by what occupies the path at cleanup time: when
  the path names an entry of another identity (the root moved away and another directory, or a
  symbolic link, put in its place), that entry is not touched and the run's root is reported as
  surviving; when the path names nothing, the root is reported as surviving unless the pinned directory
  itself no longer exists. A stale root of the sweep is pinned when the sweep judges it: the stat of its
  name in the opened parent, matched by the descriptor the marker is then read through, is its identity
  from there on.
- After the final rmdir, whatever it returned, the pinned descriptor is asked whether the directory is
  gone (no link left, or a listing that reports it deleted). Only then is the root reported removed.
  Otherwise (the rmdir failed, or it succeeded on another directory put at the root's name after the
  last check) the pinned directory gets its marker back through the descriptor (as below) and the root
  is reported as not removed, so a later sweep of wherever it now is still recognises it.
- A marker is never deleted unless its full content was read first: an entry with the marker's name
  that cannot be opened, is not a regular file, is too long or cannot be read is kept and its root is
  reported (an absent marker is the only case in which the removal goes on without one).

Every removal works on pinned directory descriptors, never by re-resolving a path. The root's parent
is opened once; the root's name must name the pinned directory, and the pinned descriptor is used, or
nothing is touched and the root is reported. The sweep reads and
judges the marker through that descriptor. Listing, permission repair, unlinking and removal of every
entry go through descriptors opened the same way, each matched against the no-follow stat of its name.
Both walks (emptying and permission repair) are iterative, on an explicit stack that holds descriptors
only for the 64 directories nearest the root (_HELD_DEPTH) and the one being worked on (a deeper
directory is reopened as ".." of the one below it and matched against the stat taken on the way
down), so a tree of any depth is handled without recursion and with a bounded number of descriptors; any failure while
judging or removing one root, of whatever kind, is returned as that root's problem (by the sweep, which
goes on to the next root, and by remove_root), never raised. Before the marker is deleted, and again
before the root itself is removed, the root's name in the pinned parent must still name the pinned
directory; a root replaced (by a symbolic link or another directory) after it was checked is reported
as replaced, keeps its marker and is never followed, so nothing outside the checked tree is listed,
repaired or deleted. When the name has gone instead, the root counts as removed only when the pinned
descriptor shows the directory gone (as above).

Removal deletes the marker last. Everything else in the root goes first; the marker is deleted only
once nothing else is left. Before it is deleted, a full copy of it (its bytes and its modification
time) is made as an unnamed file in the root (O_TMPFILE, which does not keep the root from being
removed); when that copy cannot be made the removal stops there, the marker is kept whole and the
root is reported. When the empty root itself then cannot be removed, the copy is linked back as the
marker, so the marker returns with its full content and time and a later sweep still recognises and
retries the root; an unmarked survivor would be skipped by every sweep. Where O_TMPFILE or /proc is
not available, or the link fails, the marker is written back instead to a temporary name in the root
(every short write continued, a write that makes no progress treated as an error) and renamed into
place, so a partly written marker never takes the marker's name. A marker that cannot be written back
either way is an explicit failure in remove_root's result, never an exception, and such a root (left
unmarked) is skipped by later sweeps and must be removed by hand. When removal stops before the marker
is deleted (emptying the root, or reading, copying or deleting the marker fails), it adds owner read, write and
search permission to the directories of the tree that this user owns, the root included (a run killed
while a fixture directory sat at mode 0000 leaves one that cannot be entered, and a root that has lost
owner write cannot lose even its marker), and tries once more. A root whose final rmdir fails is not
retried: that needs write permission on the parent, which is outside the tree and is never changed.

The marker is opened without blocking and without following a symbolic link, and checked with fstat
to be a regular file before anything is read, so a FIFO or other special file named as the marker is
rejected at once rather than hanging the sweep. A marker that is not standard JSON (NaN, Infinity or
-Infinity anywhere in it) or whose creation time is not a finite number is not one this module wrote,
and its root is left alone. Where the owner's /proc entry cannot be read for any reason other than
its absence, the owner reads as alive.

Residuals (known and not handled here):
- The owner test is the bare process identifier on this host name. A run in another PID namespace
  with the same host name and a shared temporary directory reads as gone, so its root can be removed
  once it is a day old while that run still works in it. Recording the process start time or the PID
  namespace identity would close this.
- The ownership test is the directory's user id; workers that share one user id are not told apart.
- Concurrent mutation of a parent directory (the root's, or a directory's inside the tree) by another
  process of this user is outside what a test helper can exclude. A directory is removed with rmdir by
  name in its pinned parent, just after that name was checked to still name the pinned (by then empty)
  directory; a process that swaps the name for another empty directory between the check and the rmdir
  gets that empty directory removed. For the root, the descriptor check that follows then reports the
  root as not removed and puts its marker back, so the swap is never reported as success; for a
  directory inside the tree no such check is made, and the moved directory survives wherever it was
  moved to (reported only when it keeps the root from being removed). rmdir never follows a symbolic
  link and never removes a directory holding anything.
- The one window before a suite's root is pinned is between the mkdtemp inside _fixture_tmpdir and
  pin_root. A directory put at the root's name in that window that pin_root can open is pinned in the
  root's place, and is the one the finally removes; one it cannot open (a mode 0000 directory) makes
  pin_root fail, and then nothing is removed and the root is reported as left behind.
- The parent given to remove_root (the root path's directory) and each sweep parent are resolved by
  name once, when they are opened.
- Permission repair of a directory this user cannot read or search uses an O_PATH descriptor and
  /proc/self/fd; without them such a directory is not repaired and its root is reported.
- tools/_entry_restore_selftest.py still removes its directories with ignore_errors, so a directory
  it fails to remove is not reported there.
- The plan's temp/failed-test-cleans-root is not a row of its own, and the hooks suite's
  (temp-failed-setup-cleans-root), (temp-setup-child-confined), (temp-sweep-only-root-parent) and
  (temp-sweep-problem-not-stderr) are failure lines, not registered checks.
- The row that kills a real run (temp/killed-run-recovered) judges the killed process's identifier as
  gone just after reaping it; a new process given that identifier in between would keep the root and
  fail the row.

Imported only by a self-test; nothing here runs on a production path."""

import errno
import json
import math
import os
import socket
import stat
import time

MARKER = ".aiqt-selftest-root.json"
STALE_SECONDS = 24 * 60 * 60
_MARKER_LIMIT = 4096

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_PARENT_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC
_DIR_FLAGS = _PARENT_FLAGS | _O_NOFOLLOW


class _Replaced(OSError):
    """A pinned directory's name no longer names it (it was replaced after it was checked)."""


def write_marker(root, suite):
    """Write root's marker: the suite name, this process's identifier, the host name and the creation
    time, as one JSON object, created exclusively (an existing entry is never followed or replaced)."""
    record = {"suite": suite, "pid": os.getpid(), "host": socket.gethostname(), "created": time.time()}
    fd = os.open(os.path.join(str(root), MARKER), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))


def _same(a, b):
    """Whether the stat results a and b are of the same file (device and inode)."""
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _replaced(name):
    return _Replaced(
        "{} was replaced after it was checked; nothing in its place was touched".format(name))


def _check_name(dir_fd, name, expect):
    """Raise _Replaced unless name in the pinned directory dir_fd still names the directory expect."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _Replaced("{} could not be checked again ({})".format(name, exc)) from None
    if not stat.S_ISDIR(st.st_mode) or not _same(st, expect):
        raise _replaced(name)


def _list(fd):
    """The names in the pinned directory fd, read from its start."""
    os.lseek(fd, 0, os.SEEK_SET)
    return os.listdir(fd)


def _unlock_at(dir_fd, name, expect):
    """Add owner read, write and search permission to the directory name in dir_fd, when it is still
    the directory expect and this user owns it, through an O_PATH descriptor (which needs no permission
    on the directory itself) and its /proc/self/fd entry, so the name is not resolved again. Errors are
    ignored: the open or removal that follows judges by what it finds."""
    o_path = getattr(os, "O_PATH", 0)
    if not o_path or not os.path.isdir("/proc/self/fd"):
        return
    try:
        fd = os.open(name, o_path | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return
    try:
        st = os.fstat(fd)
        if _same(st, expect) and stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid():
            os.chmod("/proc/self/fd/{}".format(fd), stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
    except OSError:
        pass
    finally:
        os.close(fd)


def _open_dir_at(dir_fd, name, expect, repair=False):
    """Open the directory name in the pinned directory dir_fd with O_DIRECTORY and O_NOFOLLOW and return
    its descriptor, which must be the directory expect (else _Replaced). With repair, a directory this
    user cannot open is given owner access first (_unlock_at) and opened once more."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except PermissionError:
        if not repair:
            raise
        _unlock_at(dir_fd, name, expect)
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _replaced(name) from None
        raise
    try:
        if not _same(os.fstat(fd), expect):
            raise _replaced(name)
    except BaseException:
        os.close(fd)
        raise
    return fd


class _Level:
    """One directory on the explicit stack of an iterative walk (_clear, _grant_owner_access): its name
    in the directory above, its stat result, its descriptor (None while it is not held: see
    _HELD_DEPTH), the names of it still to be worked on, and whether anything in it could not go."""

    __slots__ = ("name", "st", "fd", "names", "failed")

    def __init__(self, name, st, fd, names):
        self.name, self.st, self.fd, self.names, self.failed = name, st, fd, iter(names), False


# How many levels of a walk, counted from its top, keep their descriptors while a directory below them is
# worked on. Deeper than this, a directory's descriptor is closed while one below it is worked on and
# reopened afterwards as ".." of that one, matched against the stat result taken when the walk entered
# it, so a tree of any depth needs about this many descriptors at most.
_HELD_DEPTH = 64


def _push(stack, name, st, fd, names):
    """Enter the directory fd (name, st) below the top of stack, closing the top's descriptor when the
    stack is deeper than _HELD_DEPTH. The bottom of the stack (the caller's descriptor) is never closed."""
    if len(stack) > _HELD_DEPTH:
        top = stack[-1]
        os.close(top.fd)
        top.fd = None
    stack.append(_Level(name, st, fd, names))


def _climb(stack):
    """Leave the directory on top of stack (closing its descriptor) and return it and the level below,
    whose descriptor is reopened as ".." of the one left when it is not held. Raises _Replaced when that
    ".." is not the directory the walk entered (the tree was moved while it was walked)."""
    done = stack.pop()
    below = stack[-1]
    try:
        if below.fd is None:
            try:
                below.fd = _open_dir_at(done.fd, "..", below.st)
            except _Replaced:
                raise _Replaced("a directory of the tree was moved while it was walked, so the walk "
                                "stopped") from None
    finally:
        os.close(done.fd)
        done.fd = None
    return done, below


def _close_levels(stack):
    """Close every descriptor the walk opened (all but the bottom level's, which is the caller's)."""
    for level in stack[1:]:
        if level.fd is not None:
            os.close(level.fd)
            level.fd = None


def _clear(fd, keep_marker):
    """Remove every entry of the pinned directory fd (except its marker when keep_marker), each through
    a descriptor, never by path. Iterative (an explicit stack holding a bounded number of descriptors:
    _HELD_DEPTH), so a tree of any depth is walked without recursion. Returns the first error, or None when nothing (but
    the marker) is left."""
    try:
        stack = [_Level(None, os.fstat(fd), fd, _list(fd))]
    except OSError as exc:
        return exc
    error = None
    try:
        while True:
            level = stack[-1]
            name = next(level.names, None)
            if name is not None:
                if keep_marker and len(stack) == 1 and name == MARKER:
                    continue
                try:
                    st = os.stat(name, dir_fd=level.fd, follow_symlinks=False)
                    if not stat.S_ISDIR(st.st_mode):
                        os.unlink(name, dir_fd=level.fd)
                        continue
                    sub = _open_dir_at(level.fd, name, st)
                    try:
                        sub_names = _list(sub)
                    except BaseException:
                        os.close(sub)
                        raise
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    error = exc if error is None else error
                    level.failed = True
                    continue
                _push(stack, name, st, sub, sub_names)
                continue
            if len(stack) == 1:
                return error
            try:
                done, below = _climb(stack)
            except OSError as exc:
                return exc if error is None else error
            if done.failed:
                below.failed = True
                continue
            try:
                _check_name(below.fd, done.name, done.st)
                os.rmdir(done.name, dir_fd=below.fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                error = exc if error is None else error
                below.failed = True
    finally:
        _close_levels(stack)


def _grant_owner_access(fd):
    """Add owner read, write and search permission to the pinned directory fd and every real directory
    under it that this user owns, so a directory left at mode 0000 can be entered and emptied. Every
    directory is reached through a descriptor matched against the no-follow stat of its name. Iterative,
    like _clear. Errors are ignored: the removal that follows judges by what is left."""
    uid = os.getuid()

    def grant(dir_fd):
        st = os.fstat(dir_fd)
        if st.st_uid == uid:
            os.chmod(dir_fd, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
        return st, _list(dir_fd)

    try:
        st, names = grant(fd)
    except OSError:
        return
    stack = [_Level(None, st, fd, names)]
    try:
        while True:
            level = stack[-1]
            name = next(level.names, None)
            if name is not None:
                try:
                    st = os.stat(name, dir_fd=level.fd, follow_symlinks=False)
                    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
                        continue
                    sub = _open_dir_at(level.fd, name, st, repair=True)
                except OSError:
                    continue
                try:
                    sub_names = grant(sub)[1]
                except OSError:
                    os.close(sub)
                    continue
                _push(stack, name, st, sub, sub_names)
                continue
            if len(stack) == 1:
                return
            try:
                _climb(stack)
            except OSError:
                return
    finally:
        _close_levels(stack)


def _read_marker_bytes(dir_fd):
    """The raw bytes (at most _MARKER_LIMIT) and stat result of the marker in the pinned directory
    dir_fd, or None when no entry has the marker's name. An entry of that name that is not a regular
    file, cannot be opened or read, or is longer than _MARKER_LIMIT raises an OSError: it exists but
    cannot be saved, which is never the same as absent. Opened with O_NONBLOCK and O_NOFOLLOW and
    checked with fstat before any read, so a FIFO is rejected without blocking."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(MARKER, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "the marker is not a regular file")
        data = os.read(fd, _MARKER_LIMIT + 1)
    finally:
        os.close(fd)
    if len(data) > _MARKER_LIMIT:
        raise OSError(errno.EFBIG, "the marker is longer than {} bytes".format(_MARKER_LIMIT))
    return data, st


def _write_all(fd, data):
    """Write all of data to fd, continuing after every short write; a write that makes no progress, or
    a file that does not end up holding exactly data's length, is an OSError."""
    view = memoryview(data)
    done = 0
    while done < len(data):
        count = os.write(fd, view[done:])
        if count <= 0:
            raise OSError(errno.EIO, "short write: {} of {} bytes written".format(done, len(data)))
        done += count
    size = os.fstat(fd).st_size
    if size != len(data):
        raise OSError(errno.EIO, "short write: the file holds {} of {} bytes".format(size, len(data)))


def _marker_copy(dir_fd, saved):
    """An unnamed file (O_TMPFILE) in the pinned directory dir_fd holding the marker saved ((bytes,
    stat) from _read_marker_bytes) with its modification time, or None where O_TMPFILE or /proc is not
    available. Any other failure (ENOSPC, a short write) raises, so the marker is never deleted without
    a full copy where a copy can be made."""
    o_tmpfile = getattr(os, "O_TMPFILE", 0)
    if not o_tmpfile or not os.path.isdir("/proc/self/fd"):
        return None
    data, st = saved
    try:
        fd = os.open(".", o_tmpfile | os.O_RDWR | _O_CLOEXEC, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.EOPNOTSUPP, errno.EISDIR, errno.EINVAL):
            return None
        raise
    try:
        _write_all(fd, data)
        os.utime(fd, ns=(st.st_mtime_ns, st.st_mtime_ns))
    except BaseException:
        os.close(fd)
        raise
    return fd


def _restore_marker(dir_fd, saved, copy):
    """Put the marker saved back into the pinned directory dir_fd with its modification time: by
    linking copy (from _marker_copy) as MARKER, else by writing it to a temporary name and renaming that
    into place. Returns None when the marker is back whole, else the error (never raises)."""
    link_error = None
    if copy is not None:
        try:
            os.link("/proc/self/fd/{}".format(copy), MARKER, dst_dir_fd=dir_fd, follow_symlinks=True)
            return None
        except OSError as exc:
            link_error = exc
    data, st = saved
    temp = "{}.restore-{}".format(MARKER, os.getpid())
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, 0o600,
                     dir_fd=dir_fd)
    except OSError as exc:
        return exc if link_error is None else "{}; then {}".format(link_error, exc)
    try:
        try:
            _write_all(fd, data)
            os.utime(fd, ns=(st.st_mtime_ns, st.st_mtime_ns))
        finally:
            os.close(fd)
        os.rename(temp, MARKER, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError as exc:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except OSError:
            pass
        return exc if link_error is None else "{}; then {}".format(link_error, exc)
    return None


def _remove_pinned(parent_fd, name, fd, expect):
    """Remove the directory fd, pinned as name in the pinned directory parent_fd and matching expect,
    its marker last (the module docstring). Returns None when it is gone, else the error that stopped
    the removal (never raises an OSError). A removal that stops before the marker is deleted (emptying
    the root, copying the marker or deleting it fails) is retried once after owner access is granted
    to the tree, so a root that has lost owner write is repaired even when it holds only its marker."""
    error, retry = _remove_attempt(parent_fd, name, fd, expect)
    if retry:
        _grant_owner_access(fd)
        error = _remove_attempt(parent_fd, name, fd, expect)[0]
    return error


def _remove_attempt(parent_fd, name, fd, expect):
    """One removal of the pinned directory fd (_remove_pinned). Returns (None when it is gone, else the
    error that stopped it; whether it stopped before the marker was deleted and a retry after
    permission repair may succeed)."""
    error = _clear(fd, True)
    if error is not None:
        return error, True
    try:
        saved = _read_marker_bytes(fd)
    except OSError as exc:
        # It exists but cannot be saved: deleting it could leave an unmarked survivor, so it is kept.
        return "its marker exists but could not be read ({}), so the marker was kept".format(exc), True
    copy = None
    if saved is not None:
        try:
            copy = _marker_copy(fd, saved)
        except OSError as exc:
            return "its marker could not be copied ({}), so the marker was kept".format(exc), True
    try:
        try:
            _check_name(parent_fd, name, expect)
        except FileNotFoundError:
            # The name went before the marker was touched: the marker is still in the pinned directory,
            # which is gone or survives (marked) under another name.
            return _survivor(fd, name), False
        except _Replaced as exc:
            return exc, False
        if saved is not None:
            try:
                os.unlink(MARKER, dir_fd=fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return exc, True
        try:
            _check_name(parent_fd, name, expect)
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as caught:
            exc = caught
        else:
            exc = None
        # Whatever rmdir said, only the held descriptor tells whether the pinned directory is gone: an
        # rmdir that succeeded may have removed another directory put at its name after the check.
        if _pinned_gone(fd):
            return None, False
        if exc is None or isinstance(exc, FileNotFoundError):
            exc = _moved(name)
        if saved is None:
            return exc, False
        failed = _restore_marker(fd, saved, copy)
        if failed is None:
            return exc, False
        return "{}; its marker could not be written back ({})".format(exc, failed), False
    finally:
        if copy is not None:
            os.close(copy)


class _Moved(OSError):
    """A pinned directory that still exists no longer has its name (it was moved away)."""


def _pinned_gone(fd):
    """Whether the pinned directory fd has been removed: it has no link left, or (where a file system
    keeps a removed directory's link count) listing it reports it deleted. Anything else, including an
    error, reads as a directory that still exists."""
    try:
        if os.fstat(fd).st_nlink == 0:
            return True
        _list(fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _survivor(fd, name):
    """None when the pinned directory fd, whose name name has gone, has been removed; else the error
    that says it survives under another name."""
    if _pinned_gone(fd):
        return None
    return _moved(name)


def _moved(name):
    return _Moved("{} no longer names the pinned directory, which still exists under another name; it "
                  "was not removed".format(name))


def _problem(root, error):
    return "could not remove the temporary root {} ({})".format(root, error)


class RootPin:
    """A temporary root's identity, recorded by pin_root when the run makes the root: its path, the
    stat result (device and inode) of the directory there, and a descriptor of that directory, held open
    until remove_root is given the pin, so the inode cannot be freed and its number given to another
    directory meanwhile."""

    def __init__(self, root):
        self.root = str(root)
        fd = os.open(self.root, _DIR_FLAGS)
        try:
            self.stat = os.fstat(fd)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self):
        """Close the held descriptor (once; remove_root closes the pin it is given)."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def pin_root(root):
    """Pin the directory root (just made by this run): open it with O_DIRECTORY and O_NOFOLLOW and
    return a RootPin, which remove_root(root, pin) then holds the root's removal to."""
    return RootPin(root)


def remove_root(root, pin):
    """Remove the directory tree root, its marker last, through pinned descriptors (the module
    docstring). Only the directory pin (a RootPin from pin_root) holds is ever removed: a directory of
    another identity now at root is refused untouched, and the pinned one is reported as surviving; the
    pin is closed. With pin None (pin_root failed or was never reached) nothing is removed and root is
    reported as left behind. Returns None only when the pinned directory is confirmed gone through the
    pin's descriptor, else a one-line description naming root and what stopped the removal (no pin, a
    root replaced or moved away, a marker that could not be read or written back, a failure walking the
    tree), so the caller can report the root that survived. Never raises an Exception."""
    root = str(root)
    if pin is None:
        return _problem(root, "its identity was never pinned, so nothing at its path was removed and "
                              "whatever is there was left behind")
    try:
        return _remove_root(root, pin)
    except Exception as exc:  # noqa: BLE001  any failure is this root's report, never the caller's exception
        return _problem(root, "{}: {}".format(type(exc).__name__, exc))
    finally:
        pin.close()


def _remove_root(root, pin):
    """remove_root's work with a pin (the same arguments and result; it may raise)."""
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        if _pinned_gone(pin.fd):
            return None
        return _problem(root, "it was moved away before cleanup: the directory this run made still exists "
                              "under another name and was not removed")
    except OSError as exc:
        return _problem(root, exc)
    if not (stat.S_ISDIR(st.st_mode) and _same(st, pin.stat)):
        return _problem(root, "it was replaced before cleanup: its name now holds another entry, which "
                              "was not touched, and the directory this run made was not removed")
    parent, name = os.path.split(os.path.abspath(root))
    if name in ("", ".", ".."):
        return _problem(root, "it has no name in a parent directory")
    try:
        parent_fd = os.open(parent, _PARENT_FLAGS)
    except OSError as exc:
        return _problem(root, exc)
    try:
        error = _remove_pinned(parent_fd, name, pin.fd, pin.stat)
    finally:
        os.close(parent_fd)
    return None if error is None else _problem(root, error)


def _owner_alive(pid):
    """Whether the process pid may still exist. Read from /proc where the system has one; elsewhere
    by os.kill with signal 0, which delivers no signal. Anything undecidable reads as alive: only a
    /proc entry that is absent (FileNotFoundError) or a ProcessLookupError reads as gone."""
    if os.path.isdir("/proc/self"):
        try:
            os.lstat("/proc/{}".format(pid))
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _reject_constant(name):
    raise ValueError("non-standard JSON constant {}".format(name))


def _read_marker(dir_fd):
    """The marker record of the pinned directory dir_fd and the marker's modification time, or None
    when the marker is absent, not a regular file, unreadable, not standard JSON or not a JSON object."""
    try:
        read = _read_marker_bytes(dir_fd)
    except OSError:
        return None
    if read is None:
        return None
    data, st = read
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    return (record, st.st_mtime) if isinstance(record, dict) else None


def _stale(dir_fd, suite, host, now):
    """Whether the pinned directory dir_fd is one of suite's stale marked roots (the module docstring's
    tests), judged by the marker read through dir_fd."""
    read = _read_marker(dir_fd)
    if read is None:
        return False
    return _stale_record(read[0], read[1], suite, host, now)


def _stale_record(record, marker_mtime, suite, host, now):
    """Whether a marker record (a dict) with modification time marker_mtime names one of suite's stale
    roots on host at time now (the module docstring's tests, past reading the marker)."""
    pid, created = record.get("pid"), record.get("created")
    if record.get("suite") != suite or record.get("host") != host:
        return False
    if type(pid) is not int or pid <= 0 or type(created) not in (int, float):
        return False
    # A NaN or infinite time would make the age comparison below meaningless (NaN compares false), and
    # an integer too large for a float would make it raise.
    try:
        created = float(created)
    except OverflowError:
        return False
    if not (math.isfinite(created) and math.isfinite(marker_mtime)):
        return False
    if now - max(created, marker_mtime) < STALE_SECONDS:
        return False
    return not _owner_alive(pid)


def _sweep_entry(parent_fd, name, suite, host, now):
    """Judge and, when stale, remove the entry name of the pinned sweep parent parent_fd. Returns
    (False, None) when it is not one of suite's stale roots, else (True, None when removed or the error
    that stopped its removal)."""
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False, None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False, None
    try:
        fd = _open_dir_at(parent_fd, name, st)
    except OSError:
        return False, None
    try:
        if not _stale(fd, suite, host, now):
            return False, None
        return True, _remove_pinned(parent_fd, name, fd, st)
    finally:
        os.close(fd)


def sweep_stale(suite, prefix, parents, now=None):
    """Remove suite's stale marked roots named prefix* directly in each directory of parents.
    Returns (removed, problems): the paths removed and a description of each one that could not be.
    A failure judging or removing one root (any Exception) is that root's problem; the sweep goes on."""
    removed, problems = [], []
    host = socket.gethostname()
    now = time.time() if now is None else now
    seen = set()
    for parent in parents:
        try:
            real = os.path.realpath(str(parent))
            if real in seen:
                continue
            seen.add(real)
            parent_fd = os.open(real, _PARENT_FLAGS)
        except OSError:
            continue
        try:
            try:
                names = sorted(_list(parent_fd))
            except OSError:
                continue
            for name in names:
                if not name.startswith(prefix):
                    continue
                path = os.path.join(real, name)
                try:
                    stale, error = _sweep_entry(parent_fd, name, suite, host, now)
                except Exception as exc:  # noqa: BLE001  a failure is this root's problem, never the sweep's
                    stale, error = True, "{}: {}".format(type(exc).__name__, exc)
                if not stale:
                    continue
                if error is None:
                    removed.append(path)
                else:
                    problems.append(_problem(path, error))
        finally:
            os.close(parent_fd)
    return removed, problems
