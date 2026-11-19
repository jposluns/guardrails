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

Removal deletes the marker last. Everything else in the root goes first; the marker is deleted only
once nothing else is left, and is written back (with its modification time) when the empty root
itself then cannot be removed. A removal that stops part way therefore leaves a marked root, which a
later sweep still recognises and retries; an unmarked survivor would be skipped by every sweep. Before
giving up, removal adds owner read, write and search permission to the directories of the tree that
this user owns (a run killed while a fixture directory sat at mode 0000 leaves one rmtree cannot
enter) and tries once more.

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
- The permission repair checks each directory with lstat before chmod; an entry another process of
  this user swaps for a symbolic link between the two is not prevented.
- tools/_entry_restore_selftest.py still removes its directories with ignore_errors, so a directory
  it fails to remove is not reported there.
- The plan's temp/failed-test-cleans-root is not a row of its own, and the hooks suite's
  (temp-failed-setup-cleans-root) is a failure line, not a registered check.

Imported only by a self-test; nothing here runs on a production path."""

import json
import math
import os
import shutil
import socket
import stat
import time

MARKER = ".aiqt-selftest-root.json"
STALE_SECONDS = 24 * 60 * 60
_MARKER_LIMIT = 4096


def write_marker(root, suite):
    """Write root's marker: the suite name, this process's identifier, the host name and the creation
    time, as one JSON object, created exclusively (an existing entry is never followed or replaced)."""
    record = {"suite": suite, "pid": os.getpid(), "host": socket.gethostname(), "created": time.time()}
    fd = os.open(os.path.join(str(root), MARKER), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))


def _clear(root):
    """Remove every entry of the directory root except its marker. Returns the first error, or None
    when nothing but the marker is left."""
    try:
        names = os.listdir(root)
    except OSError as exc:
        return exc
    error = None
    for name in names:
        if name == MARKER:
            continue
        path = os.path.join(root, name)
        try:
            if stat.S_ISDIR(os.lstat(path).st_mode):
                shutil.rmtree(path)
            else:
                os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if error is None:
                error = exc
    return error


def _grant_owner_access(root):
    """Add owner read, write and search permission to root and every real directory under it that this
    user owns, so a directory left at mode 0000 can be entered and emptied. Errors are ignored: the
    removal that follows judges by what is left."""
    uid = os.getuid()

    def grant(path):
        try:
            st = os.lstat(path)
            if stat.S_ISDIR(st.st_mode) and st.st_uid == uid:
                os.chmod(path, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
        except OSError:
            pass

    grant(root)
    for dirpath, dirnames, _files in os.walk(root):
        for name in dirnames:
            grant(os.path.join(dirpath, name))


def _read_marker_bytes(marker):
    """The raw bytes (at most _MARKER_LIMIT) and modification time of the marker file marker, or None
    when it is absent, not a regular file or unreadable. Opened with O_NONBLOCK and O_NOFOLLOW and
    checked with fstat before any read, so a FIFO is rejected without blocking."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker, flags | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        data = os.read(fd, _MARKER_LIMIT + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > _MARKER_LIMIT:
        return None
    return data, st.st_mtime


def _restore_marker(marker, saved):
    """Write the marker back from saved ((bytes, mtime) from _read_marker_bytes), exclusively, with its
    original modification time, so a root whose last step failed stays recognisable to the sweep."""
    data, mtime = saved
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.utime(marker, (mtime, mtime))
    except OSError:
        pass


def remove_root(root):
    """Remove the directory tree root, its marker last (the module docstring). Returns None when root is
    gone afterwards, else a one-line description naming root and the error that stopped the removal, so
    the caller can report the root that survived."""
    root = str(root)
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "could not remove the temporary root {} ({})".format(root, exc)
    if not stat.S_ISDIR(st.st_mode):
        return "could not remove the temporary root {} (it is not a directory)".format(root)
    error = _clear(root)
    if error is not None:
        _grant_owner_access(root)
        error = _clear(root)
    if error is None:
        marker = os.path.join(root, MARKER)
        saved = _read_marker_bytes(marker)
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        except OSError as exc:
            error = exc
        if error is None:
            try:
                os.rmdir(root)
            except FileNotFoundError:
                pass
            except OSError as exc:
                error = exc
                if saved is not None:
                    _restore_marker(marker, saved)
    if os.path.lexists(root):
        return "could not remove the temporary root {} ({})".format(
            root, error if error is not None else "it is still present")
    return None


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


def _read_marker(path):
    """The marker record of the directory path and the marker's modification time, or None when the
    marker is absent, not a regular file, unreadable, not standard JSON or not a JSON object."""
    read = _read_marker_bytes(os.path.join(path, MARKER))
    if read is None:
        return None
    data, mtime = read
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    return (record, mtime) if isinstance(record, dict) else None


def _stale(path, suite, host, now):
    """Whether the directory path is one of suite's stale marked roots (the module docstring's tests)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False
    read = _read_marker(path)
    if read is None:
        return False
    record, marker_mtime = read
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


def sweep_stale(suite, prefix, parents, now=None):
    """Remove suite's stale marked roots named prefix* directly in each directory of parents.
    Returns (removed, problems): the paths removed and a description of each one that could not be."""
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
            names = sorted(os.listdir(real))
        except OSError:
            continue
        for name in names:
            if not name.startswith(prefix):
                continue
            path = os.path.join(real, name)
            if not _stale(path, suite, host, now):
                continue
            problem = remove_root(path)
            if problem is None:
                removed.append(path)
            else:
                problems.append(problem)
    return removed, problems
