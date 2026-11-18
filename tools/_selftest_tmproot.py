"""Owned temporary roots for the two long hook self-tests (tools/selftest_aiqt_hooks.py and
tools/selftest_orch_hooks.py): a marker written into each root when it is made, a checked removal
that reports a root it could not remove instead of silencing the failure, and a sweep, run at the
start of each of those suites, of that suite's own stale marked roots.

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

Imported only by a self-test; nothing here runs on a production path."""

import json
import os
import shutil
import socket
import stat
import time

MARKER = ".aiqt-selftest-root.json"
STALE_SECONDS = 24 * 60 * 60


def write_marker(root, suite):
    """Write root's marker: the suite name, this process's identifier, the host name and the creation
    time, as one JSON object, created exclusively (an existing entry is never followed or replaced)."""
    record = {"suite": suite, "pid": os.getpid(), "host": socket.gethostname(), "created": time.time()}
    fd = os.open(os.path.join(str(root), MARKER), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))


def remove_root(root):
    """Remove the directory tree root. Returns None when root is gone afterwards, else a one-line
    description naming root and the first error, so the caller can report the root that survived."""
    error = None
    try:
        shutil.rmtree(str(root))
    except FileNotFoundError:
        pass
    except OSError as exc:
        error = exc
        # Remove what can still be removed, then judge by what is left.
        shutil.rmtree(str(root), ignore_errors=True)
    if os.path.lexists(str(root)):
        return "could not remove the temporary root {} ({})".format(
            root, error if error is not None else "it is still present")
    return None


def _owner_alive(pid):
    """Whether the process pid may still exist. Read from /proc where the system has one; elsewhere
    by os.kill with signal 0, which delivers no signal. Anything undecidable reads as alive."""
    if os.path.isdir("/proc/self"):
        return os.path.lexists("/proc/{}".format(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _read_marker(path):
    """The marker record of the directory path and the marker's modification time, or None when the
    marker is absent, not a regular file, unreadable or not a JSON object."""
    marker = os.path.join(path, MARKER)
    try:
        fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            record = json.loads(handle.read(4096))
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
    return (record, st.st_mtime) if isinstance(record, dict) else None


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
