#!/usr/bin/env python3
"""Bind an asynchronous result marker to the one dispatch that produced it (expbnd.6).

A marker file in a location that more than one dispatch can write to (a reused scratch directory, a
fixed result file name) cannot tell an earlier dispatch's result from the current one's: its presence,
its content and its age all fit a stale file. This helper gives a dispatcher both bindings the rule
names: a location created exclusively for one dispatch, and an id the dispatcher issues before launch
that the result must carry before it is accepted.

  orch_dispatch_marker.py claim DIR
      Create DIR with one exclusive mkdir (never following, clearing or adopting an existing entry)
      and print a fresh id (32 lowercase hexadecimal characters) on stdout.
      Exit 0 created; 5 something already exists at DIR (a directory, a file or a symlink, even an
      empty one); 2 cannot evaluate (bad arguments, a relative path, a missing or non-directory
      parent, any other operating-system error, a failed write of the id to stdout included, such as
      a broken pipe, after which DIR exists but no id was delivered, so DIR is never used).
  orch_dispatch_marker.py write --marker PATH --id HEX --status N
      Write the line `aiqt-dispatch-marker v1 <id> <status>` to a temporary file in PATH's directory,
      fsync it, then rename it onto PATH, so PATH keeps its previous complete content until the
      rename. N is an integer from 0 to 255 with no sign or leading zero.
      Exit 0 published; 2 cannot evaluate (bad arguments, an oversized or unconvertible number
      included, a missing directory, a target that is a directory, a failure to create the temporary
      file, or a write, fsync or rename failure, after which the temporary file this process created
      is removed and PATH is left as it was; an entry it did not create is never removed).
  orch_dispatch_marker.py wait --marker PATH --expect-id HEX --deadline-s S [--interval-s I]
      Poll PATH on a monotonic clock until S seconds (an integer from 1 to 86400) pass, every I seconds
      (from 0.05 to 60, default 1). A marker is accepted only when lstat shows a regular file of at
      most MAX_MARKER_BYTES bytes whose whole content is one v1 line carrying the expected id; the
      worker status it carries is then printed on stdout. A missing marker (a temporary file not yet
      renamed included) is still pending. A marker with another id, a legacy bare status line, or
      malformed content (not ASCII, not one line ending in a line feed, another magic or version, a
      bad id or status field) is present but unbound: it is never accepted, it never ends the wait
      early and it never exits 2; waiting continues, since the matching marker may still be renamed
      over it, and only if none is by the deadline does the wait exit 4.
      Exit 0 matched (the worker status goes on stdout and never becomes this exit code); 3 the
      deadline passed and no marker was ever seen; 4 the deadline passed and only unbound markers
      (another id, a legacy bare status or malformed content) were seen, each named on stderr; 2
      cannot evaluate, at once (bad arguments, an oversized or unconvertible number included, a
      relative path, a missing result directory, a symlink, FIFO, directory or other non-regular
      marker, a stat, open, read or permission error, an oversized marker, any other operating-system
      error, a failed write of a matched status to stdout included, such as a broken pipe). Marker
      content that was read successfully and is at most MAX_MARKER_BYTES bytes is never alone a
      cause of exit 2, whatever it holds; a file system error still exits 2 at once, whatever the
      marker's size. The marker is opened without following a symlink and without blocking on a
      FIFO, and its type and size are checked again on the open descriptor, so a marker replaced by a
      symlink, FIFO or oversized file between the lstat and the open still exits 2; one removed in
      that gap is still pending.
  orch_dispatch_marker.py --self-test [--execution-report ABS_PATH]
      Run the self-test (exit 0 pass, 1 a failed check, 2 a harness error).

The dispatcher is meant to use both bindings: claim a fresh directory for each dispatch attempt
(retries included), pass the id it prints to the producer, and always wait with that id. Each covers a
failure the other misses: a waiter handed a stale path defeats the directory alone, and an id alone
leaves payload files from an earlier dispatch beside the marker. Timestamps play no part, and deleting
an earlier result first is not a binding.

Launch it as `python3 -I -B tools/orch_dispatch_marker.py`. It uses the standard library only and
starts no process. An interpreter older than Python 3.14 that can start this file is refused at exit 2
before anything runs. One that cannot start it fails with Python's own error first, and that exit is
Python's: 1 for a compile failure, or 2 for an interpreter predating -I when run with it.

RESIDUALS (not covered): a dispatcher or waiter that does not use this helper; a waiter given the wrong
expected id or directory; a producer that writes a correctly identified marker before its work is done
(the payload's correctness is the evidence rule's concern); forgery or path substitution by a process
running as the same user (the id matches a result to its dispatch, it authenticates nothing, and it is
visible in process listings); exclusive mkdir and atomic rename are assumed to behave as on a local
POSIX file system, and network file systems are not covered; cleanup must wait until an attempt's
producers and waiters have stopped, and a dispatch directory is never reused.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: orch_dispatch_marker.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import secrets
import stat
import time

MARKER_MAGIC = "aiqt-dispatch-marker"
MARKER_VERSION = "v1"
MAX_MARKER_BYTES = 4096
ID_LENGTH = 32
HEX_DIGITS = "0123456789abcdef"
DECIMAL_DIGITS = "0123456789"
STATUS_MAX = 255
DEADLINE_MAX_S = 86400
INTERVAL_MIN_S = 0.05
INTERVAL_MAX_S = 60.0
INTERVAL_DEFAULT_S = 1.0
# Numeric arguments longer than these are refused before any conversion (no int or float of an
# unbounded string).
STATUS_TEXT_MAX = len(str(STATUS_MAX))
DEADLINE_TEXT_MAX = len(str(DEADLINE_MAX_S))
INTERVAL_TEXT_MAX = 16

# The numeric conversions, looked up at call time so the self-test can observe that an oversized
# argument is refused before it reaches them.
_to_int = int
_to_float = float

EXIT_OK = 0
EXIT_CANNOT_EVALUATE = 2
EXIT_NEVER_SEEN = 3
EXIT_ONLY_UNBOUND = 4
EXIT_EXISTS = 5


class CannotEvaluate(Exception):
    """An input or state this helper cannot judge; always exit 2, never a pass."""


def is_dispatch_id(text):
    """True only for exactly ID_LENGTH lowercase hexadecimal characters."""
    return isinstance(text, str) and len(text) == ID_LENGTH and all(c in HEX_DIGITS for c in text)


def parse_status(text):
    """The integer of a canonical status (0 to STATUS_MAX, no sign, no leading zero), or None."""
    if not text or len(text) > STATUS_TEXT_MAX or not all(c in DECIMAL_DIGITS for c in text):
        return None
    if len(text) > 1 and text[0] == "0":
        return None
    try:
        value = _to_int(text)
    except (ValueError, OverflowError):
        return None
    return value if value <= STATUS_MAX else None


def parse_deadline(text):
    """The integer deadline in seconds (1 to DEADLINE_MAX_S, canonical digits), or None."""
    if not text or len(text) > DEADLINE_TEXT_MAX or not all(c in DECIMAL_DIGITS for c in text) \
            or text[0] == "0":
        return None
    try:
        value = _to_int(text)
    except (ValueError, OverflowError):
        return None
    return value if value <= DEADLINE_MAX_S else None


def parse_interval(text):
    """The poll interval in seconds (INTERVAL_MIN_S to INTERVAL_MAX_S, plain decimal), or None."""
    if not text or len(text) > INTERVAL_TEXT_MAX or text.count(".") > 1 \
            or not all(c in DECIMAL_DIGITS + "." for c in text):
        return None
    if not text.replace(".", ""):
        return None
    try:
        value = _to_float(text)
    except (ValueError, OverflowError):
        return None
    return value if INTERVAL_MIN_S <= value <= INTERVAL_MAX_S else None


def marker_line(dispatch_id, status):
    return "{} {} {} {}\n".format(MARKER_MAGIC, MARKER_VERSION, dispatch_id, status)


def _id_matches(got, want):
    """The binding itself: a marker belongs to this dispatch only when it carries this dispatch's id."""
    return got == want


def judge_marker(data, expect_id):
    """("match", status) or ("unbound", reason) for a marker's bytes, which are never accepted on
    presence, age or a bare status alone."""
    # As the plan decided, there is no third outcome here: legacy and malformed content (not ASCII,
    # not one line, another magic or version, a bad id or status field) is present but unbound, like a
    # marker with another id. It never exits 2; the wait goes on, because a stale file may still be
    # replaced by the matching marker, and exits 4 only at the deadline.
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return ("unbound", "malformed marker (not ASCII)")
    bare = text[:-1] if text.endswith("\n") else text
    if bare and all(c in DECIMAL_DIGITS for c in bare):
        return ("unbound", "legacy bare status marker {!r} carries no dispatch id".format(bare[:16]))
    if not text.endswith("\n") or text.count("\n") != 1:
        return ("unbound", "malformed marker (not one line ending in a line feed)")
    fields = text[:-1].split(" ")
    if len(fields) != 4 or fields[0] != MARKER_MAGIC or fields[1] != MARKER_VERSION:
        return ("unbound", "malformed marker (not an {} {} line)".format(MARKER_MAGIC, MARKER_VERSION))
    got_id, status = fields[2], parse_status(fields[3])
    if not is_dispatch_id(got_id) or status is None:
        return ("unbound", "malformed marker (bad id or status field)")
    if not _id_matches(got_id, expect_id):
        return ("unbound", "marker carries dispatch id {} which is not the expected id".format(got_id))
    return ("match", status)


# The open system call, looked up at call time so the self-test can see the flags and change the marker
# between the lstat and the open.
_open_fd = os.open


def _open_marker(path):
    """A read-only descriptor for the marker that never follows a symlink and never blocks on a FIFO."""
    return _open_fd(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


def probe(path, expect_id):
    """One look at the marker: ("pending",), ("match", status) or ("unbound", reason); CannotEvaluate
    for a missing result directory, a non-regular or oversized marker, or any read error."""
    directory = os.path.dirname(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        try:
            dir_st = os.stat(directory)
        except OSError as exc:
            raise CannotEvaluate("result directory {} cannot be read ({})".format(directory, exc)) from None
        if not stat.S_ISDIR(dir_st.st_mode):
            raise CannotEvaluate("result directory {} is not a directory".format(directory))
        return ("pending",)
    except OSError as exc:
        raise CannotEvaluate("marker {} cannot be stat'ed ({})".format(path, exc)) from None
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("marker {} is not a regular file (a symlink, FIFO, directory or other "
                             "entry is never read as a result)".format(path))
    if st.st_size > MAX_MARKER_BYTES:
        raise CannotEvaluate("marker {} is larger than {} bytes".format(path, MAX_MARKER_BYTES))
    try:
        fd = _open_marker(path)
    except FileNotFoundError:
        return ("pending",)  # renamed away between lstat and open; look again on the next poll
    except OSError as exc:
        raise CannotEvaluate("marker {} cannot be opened ({})".format(path, exc)) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CannotEvaluate("marker {} is not a regular file".format(path))
        chunks, size = [], 0
        while size <= MAX_MARKER_BYTES:
            chunk = os.read(fd, MAX_MARKER_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as exc:
        raise CannotEvaluate("marker {} cannot be read ({})".format(path, exc)) from None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_MARKER_BYTES:
        raise CannotEvaluate("marker {} is larger than {} bytes".format(path, MAX_MARKER_BYTES))
    return judge_marker(data, expect_id)


def wait_marker(path, expect_id, deadline_s, interval_s, clock=time.monotonic, sleep=time.sleep):
    """(exit code, stdout text, stderr lines) of a wait; the worker status is stdout, never the code."""
    start = clock()
    end = start + deadline_s
    unbound = []
    while True:
        try:
            outcome = probe(path, expect_id)
        except CannotEvaluate as exc:
            return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: {}".format(exc)]
        if outcome[0] == "match":
            return EXIT_OK, "{}\n".format(outcome[1]), []
        if outcome[0] == "unbound" and outcome[1] not in unbound:
            unbound.append(outcome[1])
        now = clock()
        if now >= end:
            break
        sleep(min(interval_s, end - now))
    if unbound:
        return EXIT_ONLY_UNBOUND, "", ["unbound marker at {}: {}".format(path, reason) for reason in unbound]
    return EXIT_NEVER_SEEN, "", ["no marker appeared at {} before the {} s deadline".format(path, deadline_s)]


def claim_dir(path):
    """(exit code, stdout text, stderr lines) of an exclusive claim of the directory path."""
    if not os.path.isabs(path):
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: {!r} is not an absolute path".format(path)]
    try:
        os.mkdir(path)
    except FileExistsError:
        return EXIT_EXISTS, "", ["refused: something already exists at {}; a dispatch directory is never "
                                 "reused or adopted".format(path)]
    except OSError as exc:
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: cannot create {} ({})".format(path, exc)]
    return EXIT_OK, secrets.token_hex(ID_LENGTH // 2) + "\n", []


# The publishing system calls, looked up at call time so the self-test can pause a partial write or fail
# the fsync or the rename and observe the destination meanwhile, and fix the temporary name's suffix.
_write_fd = os.write
_fsync_fd = os.fsync
_replace_path = os.replace
_temp_suffix_hex = secrets.token_hex


def write_marker(path, dispatch_id, status):
    """(exit code, stdout text, stderr lines) of an atomic publish of the marker at path."""
    directory = os.path.dirname(path)
    try:
        dir_st = os.stat(directory)
    except OSError as exc:
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: result directory {} cannot be read ({})".format(
            directory, exc)]
    if not stat.S_ISDIR(dir_st.st_mode):
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: {} is not a directory".format(directory)]
    try:
        target_st = os.lstat(path)
    except FileNotFoundError:
        target_st = None
    except OSError as exc:
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: marker {} cannot be stat'ed ({})".format(path, exc)]
    if target_st is not None and stat.S_ISDIR(target_st.st_mode):
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: marker path {} is a directory".format(path)]
    temp = os.path.join(directory, ".{}.tmp-{}".format(os.path.basename(path), _temp_suffix_hex(8)))
    data = marker_line(dispatch_id, status).encode("ascii")
    created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        created = True
        try:
            view = memoryview(data)
            while view:
                view = view[_write_fd(fd, view):]
            _fsync_fd(fd)
        finally:
            os.close(fd)
        _replace_path(temp, path)
    except OSError as exc:
        # Only a temporary file this process created is removed; an entry already at that name (another
        # writer's) is left alone.
        if created:
            try:
                os.unlink(temp)
            except OSError:
                pass
        return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: cannot publish {} ({})".format(path, exc)]
    return EXIT_OK, "", []


def _options(argv, allowed, required):
    """The {option: value} map of argv, each option once with a value; None on any other shape."""
    options, rest = {}, list(argv)
    while rest:
        if len(rest) < 2 or rest[0] not in allowed or rest[0] in options:
            return None
        options[rest[0]] = rest[1]
        rest = rest[2:]
    return options if all(name in options for name in required) else None


def run_command(argv, clock=time.monotonic, sleep=time.sleep):
    """(exit code, stdout text, stderr lines) for one claim, write or wait command line."""
    usage = ["cannot evaluate: usage: claim DIR | write --marker PATH --id HEX --status N | "
             "wait --marker PATH --expect-id HEX --deadline-s S [--interval-s I]"]
    if not argv:
        return EXIT_CANNOT_EVALUATE, "", usage
    verb, rest = argv[0], argv[1:]
    if verb == "claim":
        if len(rest) != 1:
            return EXIT_CANNOT_EVALUATE, "", usage
        return claim_dir(rest[0])
    if verb == "write":
        options = _options(rest, ("--marker", "--id", "--status"), ("--marker", "--id", "--status"))
        if options is None:
            return EXIT_CANNOT_EVALUATE, "", usage
        status = parse_status(options["--status"])
        if not os.path.isabs(options["--marker"]) or not is_dispatch_id(options["--id"]) or status is None:
            return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: write needs an absolute --marker, a "
                                              "32-character lowercase hex --id and a --status from 0 to 255"]
        return write_marker(options["--marker"], options["--id"], status)
    if verb == "wait":
        options = _options(rest, ("--marker", "--expect-id", "--deadline-s", "--interval-s"),
                           ("--marker", "--expect-id", "--deadline-s"))
        if options is None:
            return EXIT_CANNOT_EVALUATE, "", usage
        deadline = parse_deadline(options["--deadline-s"])
        interval = parse_interval(options.get("--interval-s", str(INTERVAL_DEFAULT_S)))
        if not os.path.isabs(options["--marker"]) or not is_dispatch_id(options["--expect-id"]) \
                or deadline is None or interval is None:
            return EXIT_CANNOT_EVALUATE, "", ["cannot evaluate: wait needs an absolute --marker, a "
                                              "32-character lowercase hex --expect-id, --deadline-s from 1 "
                                              "to 86400 and --interval-s from 0.05 to 60"]
        return wait_marker(options["--marker"], options["--expect-id"], deadline, interval, clock, sleep)
    return EXIT_CANNOT_EVALUATE, "", usage


# SELF-TEST. Every case runs in a private temporary directory and is paired with a control that differs
# in one variable only. The clock and sleep are injected, so the waits are deterministic and fast.

SUITE_ID = "dispatch-marker-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


class _FakeClock:
    """A monotonic clock that advances only when the wait sleeps; on_sleep runs before each advance."""

    def __init__(self, on_sleep=None):
        self.now, self.sleeps, self.on_sleep = 100.0, 0, on_sleep

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        if self.on_sleep is not None:
            self.on_sleep(self.sleeps)
        self.now += seconds


def _wait(path, expect_id, deadline_s=5, interval_s=1.0, on_sleep=None):
    fake = _FakeClock(on_sleep)
    code, out, err = wait_marker(path, expect_id, deadline_s, interval_s, fake.clock, fake.sleep)
    return code, out, err, fake


def _put(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def _publish(path, dispatch_id, status):
    """A fixture marker renamed into place, independent of write_marker so the write cases test it alone."""
    temp = path + ".fixture-tmp"
    _put(temp, marker_line(dispatch_id, status).encode("ascii"))
    os.replace(temp, path)


def _read_or_none(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _run_guarded(argv, clock=time.monotonic, sleep=time.sleep):
    """run_command, with an escaped exception reported as a failed check rather than a crashed suite."""
    try:
        return run_command(argv, clock, sleep)
    except Exception as exc:
        return "raised", "", ["{}: {}".format(type(exc).__name__, str(exc)[:160])]


def _self_test():
    import tempfile
    import threading
    old_id, new_id = "a" * ID_LENGTH, "b" * ID_LENGTH
    with tempfile.TemporaryDirectory(prefix="dispatch-marker-selftest-") as tmp:
        base = os.path.realpath(tmp)
        case_dirs = iter(range(1000))

        def fresh():
            d = os.path.join(base, "d{}".format(next(case_dirs)))
            os.mkdir(d)
            return d, os.path.join(d, "exit-status")

        # --- wait: the incident and its control ---
        d, m = fresh()
        _publish(m,old_id, 0)
        code, out, err, _f = _wait(m, new_id)
        check("wait/incident-stale-id-4", code == EXIT_ONLY_UNBOUND and out == ""
              and any(old_id in line and m in line for line in err), (code, out, err))
        d, m = fresh()
        _publish(m,new_id, 0)
        code, out, err, _f = _wait(m, new_id)
        check("wait/match-status-0", code == EXIT_OK and out == "0\n" and err == [], (code, out, err))

        d, m = fresh()
        _publish(m,new_id, 7)
        code, out, err, _f = _wait(m, new_id)
        check("wait/match-status-7", code == EXIT_OK and out == "7\n", (code, out, err))

        d, m = fresh()
        _put(m, b"0\n")
        code, out, err, _f = _wait(m, new_id)
        check("wait/legacy-bare-zero-4", code == EXIT_ONLY_UNBOUND and out == ""
              and any("legacy" in line for line in err), (code, out, err))
        _publish(m,new_id, 0)
        code, out, err, _f = _wait(m, new_id)
        check("wait/legacy-control-v1-match-0", code == EXIT_OK and out == "0\n", (code, out, err))

        # Malformed content is present but unbound (exit 4 at the deadline, never 2), and the matching
        # marker renamed over it is still accepted.
        d, m = fresh()
        for name, data in (("wait/malformed-unbound-4/garbage", b"garbage\n"),
                           ("wait/malformed-unbound-4/not-ascii", b"\xff\xfe\n"),
                           ("wait/malformed-unbound-4/status-256",
                            "{} {} {} 256\n".format(MARKER_MAGIC, MARKER_VERSION, new_id).encode("ascii"))):
            _put(m, data)
            code, out, err, fake = _wait(m, new_id)
            check(name, code == EXIT_ONLY_UNBOUND and out == "" and fake.sleeps >= 2
                  and any("malformed" in line for line in err), (code, out, err, fake.sleeps))

        def replace_malformed(n):
            if n == 2:
                _publish(m, new_id, 0)

        code, out, err, _f = _wait(m, new_id, on_sleep=replace_malformed)
        check("wait/malformed-replaced-by-match-0", code == EXIT_OK and out == "0\n", (code, out, err))

        # Marker version: only v1 is read; a nearby wrong version is unbound, the same line at v1 matches.
        d, m = fresh()
        for name, version in (("wait/wrong-version-4/v2", "v2"), ("wait/wrong-version-4/v0", "v0"),
                              ("wait/wrong-version-4/upper-v1", "V1")):
            _put(m, "{} {} {} 0\n".format(MARKER_MAGIC, version, new_id).encode("ascii"))
            code, out, err, _f = _wait(m, new_id)
            check(name, code == EXIT_ONLY_UNBOUND and out == ""
                  and any("malformed" in line for line in err), (code, out, err))
        _put(m, "{} {} {} 0\n".format(MARKER_MAGIC, "v1", new_id).encode("ascii"))
        code, out, err, _f = _wait(m, new_id)
        check("wait/wrong-version-control-v1-0", code == EXIT_OK and out == "0\n", (code, out, err))

        d, m = fresh()
        _publish(m,old_id, 0)
        os.utime(m, None)
        code, out, err, _f = _wait(m, new_id)
        check("wait/stale-id-fresh-mtime-4", code == EXIT_ONLY_UNBOUND, (code, out, err))
        d, m = fresh()
        _publish(m,new_id, 0)
        os.utime(m, (1_000_000_000, 1_000_000_000))
        code, out, err, _f = _wait(m, new_id)
        check("wait/match-old-mtime-0", code == EXIT_OK and out == "0\n", (code, out, err))

        # The id comparison is exact: an id that differs from the expected one only in its last character
        # (which a prefix comparison misses) or only in its first (which a suffix comparison misses) is
        # refused, and the marker's own id is accepted.
        for name, control_name, got_id, want_id in (
                ("wait/near-id-last-char-4", "wait/near-id-last-char-control-0",
                 "f" * (ID_LENGTH - 1) + "0", "f" * (ID_LENGTH - 1) + "1"),
                ("wait/near-id-first-char-4", "wait/near-id-first-char-control-0",
                 "0" + "f" * (ID_LENGTH - 1), "1" + "f" * (ID_LENGTH - 1))):
            d, m = fresh()
            _publish(m, got_id, 0)
            code, out, err, _f = _wait(m, want_id)
            check(name, code == EXIT_ONLY_UNBOUND and out == "" and any(got_id in line for line in err),
                  (code, out, err))
            code, out, err, _f = _wait(m, got_id)
            check(control_name, code == EXIT_OK and out == "0\n", (code, out, err))

        d, m = fresh()
        _publish(m,old_id, 0)
        seen = []

        def replace_on_second(n):
            seen.append(n)
            if n == 2:
                _publish(m,new_id, 0)

        code, out, err, fake = _wait(m, new_id, on_sleep=replace_on_second)
        check("wait/stale-replaced-by-match-0", code == EXIT_OK and out == "0\n" and fake.sleeps == 2,
              (code, out, err, fake.sleeps))
        d, m = fresh()
        _publish(m,old_id, 0)
        code, out, err, fake = _wait(m, new_id)
        check("wait/stale-not-replaced-4", code == EXIT_ONLY_UNBOUND and fake.sleeps >= 2,
              (code, out, err, fake.sleeps))

        d, m = fresh()

        def late_old_write(n):
            if n == 1:
                _publish(m,old_id, 0)

        code, out, err, _f = _wait(m, new_id, on_sleep=late_old_write)
        check("wait/late-old-dispatch-write-4", code == EXIT_ONLY_UNBOUND
              and any(old_id in line for line in err), (code, out, err))

        d, m = fresh()
        code, out, err, fake = _wait(m, new_id, deadline_s=5, interval_s=1.0)
        elapsed = fake.now - 100.0
        check("wait/no-marker-3", code == EXIT_NEVER_SEEN and out == "" and 5 <= elapsed <= 5 + 1.0,
              (code, out, err, elapsed))

        # The marker is looked at once more after the last sleep, at the deadline itself: one published
        # during the last interval (a whole one, or one cut short by the deadline) is accepted.
        for name, interval_s, last in (("wait/published-in-last-interval-0", 1.0, 5),
                                       ("wait/published-in-short-last-interval-0", 2.0, 3)):
            d, m = fresh()

            def publish_in_last(n, m=m, last=last):
                if n == last:
                    _publish(m, new_id, 9)

            code, out, err, fake = _wait(m, new_id, deadline_s=5, interval_s=interval_s,
                                         on_sleep=publish_in_last)
            check(name, code == EXIT_OK and out == "9\n" and fake.sleeps == last and fake.now == 105.0,
                  (code, out, err, fake.sleeps, fake.now))

        d, m = fresh()
        temp = os.path.join(d, ".exit-status.tmp-0123456789abcdef")
        _put(temp, marker_line(new_id, 0).encode("ascii"))
        code, out, err, _f = _wait(m, new_id)
        check("wait/temp-only-pending-3", code == EXIT_NEVER_SEEN, (code, out, err))
        os.replace(temp, m)
        code, out, err, _f = _wait(m, new_id)
        check("wait/temp-renamed-0", code == EXIT_OK and out == "0\n", (code, out, err))

        d, m = fresh()
        target = os.path.join(d, "real")
        _put(target, marker_line(new_id, 0).encode("ascii"))
        os.symlink(target, m)
        code, out, err, fake = _wait(m, new_id)
        check("wait/symlink-marker-2", code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0
              and any("not a regular file" in line for line in err), (code, out, err))
        os.unlink(m)
        _put(m, marker_line(new_id, 0).encode("ascii"))
        code, out, err, _f = _wait(m, new_id)
        check("wait/symlink-control-regular-0", code == EXIT_OK and out == "0\n", (code, out, err))

        d, m = fresh()
        os.mkfifo(m)
        code, out, err, fake = _wait(m, new_id)
        check("wait/fifo-marker-2", code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0
              and any("not a regular file" in line for line in err), (code, out, err))
        d, m = fresh()
        os.mkdir(m)
        code, out, err, fake = _wait(m, new_id)
        check("wait/dir-marker-2", code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0
              and any("not a regular file" in line for line in err), (code, out, err))

        d, m = fresh()
        _publish(m,new_id, 0)
        real_open = globals()["_open_marker"]

        def denied(path):
            raise PermissionError(13, "read refused by the self-test", path)

        globals()["_open_marker"] = denied
        try:
            code, out, err, _f = _wait(m, new_id)
        finally:
            globals()["_open_marker"] = real_open
        check("wait/permission-error-2", code == EXIT_CANNOT_EVALUATE
              and any("cannot be opened" in line for line in err), (code, out, err))
        code, out, err, _f = _wait(m, new_id)
        check("wait/permission-control-0", code == EXIT_OK and out == "0\n", (code, out, err))

        # The open itself. The lstat before it is only a first look, so each case below changes the marker
        # inside the open call, after the lstat saw a small regular file, and the defences on the open and
        # on the open descriptor must hold alone.
        real_open_fd = globals()["_open_fd"]

        def wait_hooked(path, hook, on_sleep=None):
            def hooked(p, flags, *args):
                hook(p, flags)
                return real_open_fd(p, flags, *args)

            globals()["_open_fd"] = hooked
            try:
                return _wait(path, new_id, on_sleep=on_sleep)
            finally:
                globals()["_open_fd"] = real_open_fd

        d, m = fresh()
        _publish(m, new_id, 0)
        flags_seen = []
        code, out, err, _f = wait_hooked(m, lambda p, flags: flags_seen.append(flags))
        check("wait/open-flags-no-follow-non-blocking", code == EXIT_OK and out == "0\n" and len(flags_seen) == 1
              and bool(flags_seen[0] & os.O_NOFOLLOW) and bool(flags_seen[0] & os.O_NONBLOCK)
              and flags_seen[0] & os.O_ACCMODE == os.O_RDONLY, (code, out, err, flags_seen))

        d, m = fresh()
        _publish(m, old_id, 0)
        decoy = os.path.join(d, "decoy")
        _put(decoy, marker_line(new_id, 0).encode("ascii"))

        def to_symlink(p, flags):
            if not os.path.islink(p):
                os.unlink(p)
                os.symlink(decoy, p)

        code, out, err, fake = wait_hooked(m, to_symlink)
        check("wait/open-swapped-to-symlink-2", code == EXIT_CANNOT_EVALUATE and out == "" and fake.sleeps == 0
              and os.path.islink(m) and any("cannot be opened" in line for line in err), (code, out, err))

        d, m = fresh()
        _publish(m, old_id, 0)
        swapped = []

        def to_matching_regular(p, flags):
            if not swapped:
                swapped.append(p)
                _publish(p, new_id, 0)

        code, out, err, _f = wait_hooked(m, to_matching_regular)
        check("wait/open-swapped-to-regular-control-0", code == EXIT_OK and out == "0\n" and swapped == [m],
              (code, out, err, swapped))

        # A FIFO opened for reading blocks until a writer opens it; a writer is held open here, so the open
        # returns either way and only the check on the descriptor can refuse it.
        d, m = fresh()
        _publish(m, new_id, 0)
        holders = []

        def to_fifo(p, flags):
            if not holders:
                os.unlink(p)
                os.mkfifo(p)
                holders.append(os.open(p, os.O_RDWR | os.O_NONBLOCK))

        try:
            code, out, err, fake = wait_hooked(m, to_fifo)
        finally:
            for holder in holders:
                os.close(holder)
        check("wait/open-swapped-to-fifo-2", code == EXIT_CANNOT_EVALUATE and out == "" and fake.sleeps == 0
              and len(holders) == 1 and any(line.endswith("is not a regular file") for line in err),
              (code, out, err, len(holders)))

        # A marker that grows past the bound after the lstat is refused on what was read; it is restored
        # to a matching marker on the first sleep, so reading only a prefix and waiting on would end at 0.
        d, m = fresh()
        _publish(m, new_id, 0)
        grown = []

        def grow(p, flags):
            if not grown:
                grown.append(p)
                with open(p, "ab") as handle:
                    handle.write(b"x" * MAX_MARKER_BYTES)

        code, out, err, fake = wait_hooked(m, grow, on_sleep=lambda n: _publish(m, new_id, 0))
        check("wait/open-grown-oversize-2", code == EXIT_CANNOT_EVALUATE and out == "" and fake.sleeps == 0
              and grown == [m] and any("larger than" in line for line in err), (code, out, err, fake.sleeps))

        # A marker removed between the lstat and the open is still pending, and the matching marker
        # renamed in on the first sleep is accepted.
        d, m = fresh()
        _publish(m, new_id, 0)
        removed = []

        def vanish(p, flags):
            if not removed:
                removed.append(p)
                os.unlink(p)

        def republish(n):
            if n == 1:
                _publish(m, new_id, 0)

        code, out, err, fake = wait_hooked(m, vanish, on_sleep=republish)
        check("wait/open-vanished-pending-then-match-0", code == EXIT_OK and out == "0\n" and fake.sleeps == 1
              and removed == [m], (code, out, err, fake.sleeps))

        d, m = fresh()
        _put(m, b"x" * (MAX_MARKER_BYTES + 1))
        code, out, err, _f = _wait(m, new_id)
        check("wait/oversize-marker-2", code == EXIT_CANNOT_EVALUATE
              and any("larger than" in line for line in err), (code, out, err))
        _put(m, b"x" * MAX_MARKER_BYTES)
        code, out, err, _f = _wait(m, new_id)
        check("wait/size-at-bound-unbound-4", code == EXIT_ONLY_UNBOUND, (code, out, err))

        m = os.path.join(base, "absent-dir", "exit-status")
        code, out, err, fake = _wait(m, new_id)
        check("wait/missing-dir-2", code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0, (code, out, err))

        # A matching marker is already in place, so an argument wrongly accepted ends at exit 0 at once
        # instead of passing as a refusal.
        d, m = fresh()
        _publish(m,new_id, 3)
        for name, argv in (
                ("wait/bad-args-2/relative-path",
                 ["wait", "--marker", "rel/exit-status", "--expect-id", new_id, "--deadline-s", "5"]),
                ("wait/bad-args-2/id-not-32-hex",
                 ["wait", "--marker", m, "--expect-id", new_id.upper(), "--deadline-s", "5"]),
                ("wait/bad-args-2/deadline-zero",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "0"]),
                ("wait/bad-args-2/deadline-not-integer",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "1.5"]),
                ("wait/bad-args-2/deadline-86401",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", str(DEADLINE_MAX_S + 1)]),
                ("wait/bad-args-2/deadline-5000-digits",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "9" * 5000]),
                ("wait/bad-args-2/interval-0.04",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5", "--interval-s", "0.04"]),
                ("wait/bad-args-2/interval-60.01",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5", "--interval-s", "60.01"]),
                ("wait/bad-args-2/interval-5000-digits",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5", "--interval-s",
                  "9" * 5000]),
                ("wait/bad-args-2/missing-expect-id",
                 ["wait", "--marker", m, "--deadline-s", "5"]),
                ("wait/bad-args-2/expect-id-31-hex",
                 ["wait", "--marker", m, "--expect-id", new_id[:-1], "--deadline-s", "5"]),
                ("wait/bad-args-2/expect-id-33-hex",
                 ["wait", "--marker", m, "--expect-id", new_id + "b", "--deadline-s", "5"]),
                ("wait/bad-args-2/duplicate-expect-id",
                 ["wait", "--marker", m, "--expect-id", old_id, "--expect-id", new_id, "--deadline-s", "5"]),
                ("wait/bad-args-2/duplicate-deadline",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5", "--deadline-s", "6"])):
            fake = _FakeClock()
            code, out, err = _run_guarded(argv, fake.clock, fake.sleep)
            check(name, code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0
                  and any("wait needs" in line or "usage:" in line for line in err), (code, out, err))
        for name, extra in (("wait/cli-valid-args-0", ["--deadline-s", "5", "--interval-s", "0.05"]),
                            ("wait/deadline-ceiling-control-86400-0", ["--deadline-s", str(DEADLINE_MAX_S)]),
                            ("wait/interval-bound-control-60-0", ["--deadline-s", "5", "--interval-s", "60"])):
            fake = _FakeClock()
            code, out, err = _run_guarded(["wait", "--marker", m, "--expect-id", new_id] + extra,
                                          fake.clock, fake.sleep)
            check(name, code == EXIT_OK and out == "3\n", (code, out, err))

        # The length bound is applied before any conversion. The conversions are swapped for recording
        # ones: an oversized argument passes only when its text never reaches int or float, and each
        # control at the bound shows that a valid text does reach it. A leading-zero interval longer than
        # the bound would convert to an in-range 1.0, so only the bound refuses it.
        real_int, real_float = globals()["_to_int"], globals()["_to_float"]
        converted = []

        def recording_int(text):
            converted.append(("int", text))
            return real_int(text)

        def recording_float(text):
            converted.append(("float", text))
            return real_float(text)

        def run_recorded(argv):
            del converted[:]
            fake = _FakeClock()
            globals()["_to_int"], globals()["_to_float"] = recording_int, recording_float
            try:
                code, out, err = _run_guarded(argv, fake.clock, fake.sleep)
            finally:
                globals()["_to_int"], globals()["_to_float"] = real_int, real_float
            return code, out, err, list(converted)

        def lengths(seen):
            return [(kind, len(text)) for kind, text in seen]

        for name, option, kind, text in (
                ("wait/deadline-bound-before-int-2/6-digits", "--deadline-s", "int",
                 "9" * (DEADLINE_TEXT_MAX + 1)),
                ("wait/deadline-bound-before-int-2/5000-digits", "--deadline-s", "int", "9" * 5000),
                ("wait/interval-bound-before-float-2/17-chars-leading-zeros", "--interval-s", "float",
                 "0" * INTERVAL_TEXT_MAX + "1"),
                ("wait/interval-bound-before-float-2/5001-chars-leading-zeros", "--interval-s", "float",
                 "0" * 5000 + "1"),
                ("wait/interval-bound-before-float-2/5000-digits", "--interval-s", "float", "9" * 5000)):
            extra = [option, text] if option == "--deadline-s" else ["--deadline-s", "5", option, text]
            code, out, err, seen = run_recorded(["wait", "--marker", m, "--expect-id", new_id] + extra)
            check(name, code == EXIT_CANNOT_EVALUATE and out == "" and (kind, text) not in seen
                  and any("wait needs" in line for line in err), (code, out, err[:1], lengths(seen)))
        for name, option, kind, text in (
                ("wait/deadline-bound-control-int-86400-0", "--deadline-s", "int", str(DEADLINE_MAX_S)),
                ("wait/interval-bound-control-float-16-chars-0", "--interval-s", "float",
                 "0" * (INTERVAL_TEXT_MAX - 1) + "1")):
            extra = [option, text] if option == "--deadline-s" else ["--deadline-s", "5", option, text]
            code, out, err, seen = run_recorded(["wait", "--marker", m, "--expect-id", new_id] + extra)
            check(name, code == EXIT_OK and out == "3\n" and (kind, text) in seen,
                  (code, out, err, lengths(seen)))

        # --- claim ---
        fresh_path = os.path.join(base, "claim-fresh")
        code, out, err = run_command(["claim", fresh_path])
        check("claim/fresh-0", code == EXIT_OK and is_dispatch_id(out[:-1]) and out.endswith("\n")
              and os.path.isdir(fresh_path), (code, out, err))

        existing_dir = os.path.join(base, "claim-existing-dir")
        os.mkdir(existing_dir)
        existing_file = os.path.join(base, "claim-existing-file")
        _put(existing_file, b"")
        existing_link = os.path.join(base, "claim-existing-link")
        link_target = os.path.join(base, "claim-link-target")
        os.symlink(link_target, existing_link)
        for name, path in (("claim/existing-empty-dir-5", existing_dir),
                           ("claim/existing-file-5", existing_file),
                           ("claim/existing-symlink-5", existing_link)):
            before = os.lstat(path)
            code, out, err = run_command(["claim", path])
            after = os.lstat(path)
            check(name, code == EXIT_EXISTS and out == "" and before.st_ino == after.st_ino
                  and stat.S_IFMT(before.st_mode) == stat.S_IFMT(after.st_mode)
                  and not os.path.lexists(link_target), (code, out, err))

        for name, path, needle in (
                ("claim/missing-parent-2", os.path.join(base, "no-parent", "dispatch"), "cannot create"),
                ("claim/relative-2", "relative/dispatch", "not an absolute path")):
            code, out, err = run_command(["claim", path])
            check(name, code == EXIT_CANNOT_EVALUATE and out == "" and not os.path.lexists(path)
                  and any(needle in line for line in err), (code, out, err))

        def race(paths):
            barrier, results = threading.Barrier(len(paths)), [None] * len(paths)

            def one(i):
                barrier.wait()
                results[i] = claim_dir(paths[i])[:2]

            threads = [threading.Thread(target=one, args=(i,)) for i in range(len(paths))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return results

        same = os.path.join(base, "claim-race")
        got = race([same, same])
        check("claim/race-one-winner", sorted(r[0] for r in got) == [EXIT_OK, EXIT_EXISTS], got)
        got = race([os.path.join(base, "claim-race-a"), os.path.join(base, "claim-race-b")])
        check("claim/race-distinct-both-0", [r[0] for r in got] == [EXIT_OK, EXIT_OK]
              and len({r[1] for r in got}) == 2, got)

        # Every successful claim issues a fresh id: several claims keep their ids, which must be valid and
        # pairwise distinct, and a marker written under one claim's id is refused by a wait for another's.
        claimed = []
        for i in range(5):
            claim_path = os.path.join(base, "claim-many-{}".format(i))
            code, out, err = run_command(["claim", claim_path])
            claimed.append((claim_path, out[:-1] if code == EXIT_OK and out.endswith("\n") else None))
        ids = [dispatch_id for _p, dispatch_id in claimed]
        check("claim/ids-fresh-distinct", all(is_dispatch_id(x) for x in ids) and len(set(ids)) == len(ids),
              ids)
        (dir_a, id_a), (_dir_b, id_b) = claimed[0], claimed[1]
        marker_a = os.path.join(dir_a, "exit-status")
        code_w, _o, err_w = run_command(["write", "--marker", marker_a, "--id", str(id_a), "--status", "0"])
        code, out, err, _f = _wait(marker_a, id_b)
        check("claim/other-claim-id-refused-4", code_w == EXIT_OK and is_dispatch_id(id_b)
              and code == EXIT_ONLY_UNBOUND and out == "" and any(str(id_a) in line for line in err),
              (code_w, err_w, code, out, err))
        code, out, err, _f = _wait(marker_a, id_a)
        check("claim/own-claim-id-control-0", code_w == EXIT_OK and code == EXIT_OK and out == "0\n",
              (code_w, err_w, code, out, err))

        # --- write ---
        d, m = fresh()
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "0"])
        content = None
        if os.path.lexists(m):
            with open(m, "rb") as handle:
                content = handle.read()
        check("write/publish-atomic-0", code == EXIT_OK and content == marker_line(new_id, 0).encode("ascii")
              and sorted(os.listdir(d)) == ["exit-status"], (code, content, sorted(os.listdir(d))))

        # Atomic publication: pause the temporary write half way and look at the destination, which must
        # still hold the previous complete marker; it changes only at the rename.
        previous = marker_line(old_id, 0).encode("ascii")
        wanted = marker_line(new_id, 0).encode("ascii")
        d, m = fresh()
        _publish(m, old_id, 0)
        paused = []
        real_write = globals()["_write_fd"]

        def half_then_look(fd, data):
            if paused:
                return real_write(fd, data)
            written = real_write(fd, bytes(data[:len(data) // 2]))
            others = sorted(name for name in os.listdir(d) if name != "exit-status")
            paused.append((_read_or_none(m), [_read_or_none(os.path.join(d, name)) for name in others]))
            return written

        globals()["_write_fd"] = half_then_look
        try:
            code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
        finally:
            globals()["_write_fd"] = real_write
        check("write/paused-partial-keeps-previous", len(paused) == 1 and paused[0][0] == previous
              and paused[0][1] == [wanted[:len(wanted) // 2]], paused)
        check("write/paused-partial-then-published-0", code == EXIT_OK and _read_or_none(m) == wanted
              and sorted(os.listdir(d)) == ["exit-status"], (code, err, _read_or_none(m), os.listdir(d)))

        # An fsync or rename failure is exit 2, the temporary file is removed and the previous complete
        # marker is left in place.
        def fail_fsync(fd):
            raise OSError(5, "fsync failure injected by the self-test")

        def fail_rename(src, dst):
            raise OSError(5, "rename failure injected by the self-test")

        real_fsync, real_replace = globals()["_fsync_fd"], globals()["_replace_path"]
        for name, fsync_fn, replace_fn in (("write/fsync-failure-2", fail_fsync, real_replace),
                                           ("write/rename-failure-2", real_fsync, fail_rename)):
            d, m = fresh()
            _publish(m, old_id, 0)
            globals()["_fsync_fd"], globals()["_replace_path"] = fsync_fn, replace_fn
            try:
                code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
            finally:
                globals()["_fsync_fd"], globals()["_replace_path"] = real_fsync, real_replace
            check(name, code == EXIT_CANNOT_EVALUATE and _read_or_none(m) == previous
                  and sorted(os.listdir(d)) == ["exit-status"]
                  and any("injected" in line and "cannot publish" in line for line in err),
                  (code, err, _read_or_none(m), sorted(os.listdir(d))))

        d, m = fresh()
        for name, status in (("write/bad-status-2/256", "256"), ("write/bad-status-2/minus-1", "-1"),
                             ("write/bad-status-2/leading-zero", "07"),
                             ("write/bad-status-2/5000-digits", "9" * 5000)):
            code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", status])
            check(name, code == EXIT_CANNOT_EVALUATE and not os.path.lexists(m)
                  and any("write needs" in line for line in err), (code, out, err))
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "255"])
        code2, out2, err2, _f = _wait(m, new_id)
        check("write/status-255-0", code == EXIT_OK and code2 == EXIT_OK and out2 == "255\n",
              (code, err, code2, out2))

        # An oversized status never reaches int; the control at the bound does and is published.
        d, m = fresh()
        for name, status in (("write/status-bound-before-int-2/4-digits", "9" * (STATUS_TEXT_MAX + 1)),
                             ("write/status-bound-before-int-2/5000-digits", "9" * 5000)):
            code, out, err, seen = run_recorded(["write", "--marker", m, "--id", new_id, "--status", status])
            check(name, code == EXIT_CANNOT_EVALUATE and not os.path.lexists(m)
                  and ("int", status) not in seen and any("write needs" in line for line in err),
                  (code, err, lengths(seen)))
        code, out, err, seen = run_recorded(["write", "--marker", m, "--id", new_id, "--status", "255"])
        check("write/status-bound-control-int-255-0", code == EXIT_OK and ("int", "255") in seen
              and _read_or_none(m) == marker_line(new_id, 255).encode("ascii"), (code, err, lengths(seen)))

        m = os.path.join(base, "absent-write-dir", "exit-status")
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "0"])
        check("write/missing-dir-2", code == EXIT_CANNOT_EVALUATE and not os.path.lexists(m)
              and any("result directory" in line for line in err), (code, out, err))

        d, m = fresh()
        os.mkdir(m)
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "0"])
        check("write/target-is-directory-2", code == EXIT_CANNOT_EVALUATE and os.listdir(m) == []
              and any("is a directory" in line for line in err)
              and sorted(os.listdir(d)) == ["exit-status"], (code, out, err))

        # Write arguments: a bad or wrong-length id, a relative marker path (one that resolves to a real
        # result directory from the working directory, when that can be read) and a repeated option are
        # refused before anything is written; the control with a valid form publishes. Each case has its
        # own directory, so one wrongly accepted case cannot change another's result.
        def relative(path):
            try:
                return os.path.relpath(path)
            except OSError:
                return os.path.join(os.path.basename(os.path.dirname(path)), os.path.basename(path))

        for name, tail in (
                ("write/bad-args-2/id-not-hex", ["--id", "g" * ID_LENGTH, "--status", "0"]),
                ("write/bad-args-2/id-upper-hex", ["--id", new_id.upper(), "--status", "0"]),
                ("write/bad-args-2/id-31-hex", ["--id", new_id[:-1], "--status", "0"]),
                ("write/bad-args-2/id-33-hex", ["--id", new_id + "b", "--status", "0"]),
                ("write/bad-args-2/relative-marker", ["--id", new_id, "--status", "0"]),
                ("write/bad-args-2/duplicate-status", ["--id", new_id, "--status", "0", "--status", "1"]),
                ("write/bad-args-2/duplicate-id", ["--id", old_id, "--id", new_id, "--status", "0"])):
            d, m = fresh()
            marker_arg = relative(m) if name.endswith("relative-marker") else m
            code, out, err = _run_guarded(["write", "--marker", marker_arg] + tail)
            check(name, code == EXIT_CANNOT_EVALUATE and out == "" and os.listdir(d) == []
                  and any("write needs" in line or "usage:" in line for line in err),
                  (code, out, err, os.listdir(d)))
        d, m = fresh()
        code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
        check("write/args-control-0", code == EXIT_OK and _read_or_none(m) == marker_line(new_id, 0).encode("ascii")
              and os.listdir(d) == ["exit-status"], (code, out, err, os.listdir(d)))

        # The temporary name: an entry already at it (another writer's) makes the exclusive create fail at
        # exit 2 and is left as it was. The control fixes the same name with nothing there and fails the
        # fsync instead, and the temporary file this process created is removed.
        fixed_suffix = "0123456789abcdef"
        real_suffix = globals()["_temp_suffix_hex"]
        for name, foreign, fsync_fn in (("write/temp-name-taken-left-alone-2", True, real_fsync),
                                        ("write/temp-name-own-removed-control-2", False, fail_fsync)):
            d, m = fresh()
            _publish(m, old_id, 0)
            temp_name = ".exit-status.tmp-" + fixed_suffix
            foreign_data = b"another writer's temporary file\n"
            if foreign:
                _put(os.path.join(d, temp_name), foreign_data)
            globals()["_temp_suffix_hex"], globals()["_fsync_fd"] = (lambda nbytes: fixed_suffix), fsync_fn
            try:
                code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
            finally:
                globals()["_temp_suffix_hex"], globals()["_fsync_fd"] = real_suffix, real_fsync
            left = sorted(os.listdir(d))
            check(name, code == EXIT_CANNOT_EVALUATE and _read_or_none(m) == previous
                  and left == (sorted(["exit-status", temp_name]) if foreign else ["exit-status"])
                  and (_read_or_none(os.path.join(d, temp_name)) == foreign_data if foreign else True)
                  and any("cannot publish" in line for line in err), (code, err, left))

        # --- output: a stdout write that fails is exit 2, never Python's own 120 or a silent loss ---
        import io

        class FailingStdout:
            def __init__(self, exc):
                self.exc = exc

            def write(self, text):
                raise self.exc

            def flush(self):
                pass

        def main_to(argv, stdout):
            stderr = io.StringIO()
            try:
                code = main(argv, stdout, stderr)
            except Exception as exc:
                return "raised", ["{}: {}".format(type(exc).__name__, exc)]
            return code, stderr.getvalue().splitlines()

        def main_to_closed_pipe(argv):
            """main with stdout on a pipe whose reader is closed: a real BrokenPipeError on the flush."""
            read_end, write_end = os.pipe()
            os.close(read_end)
            stream = open(write_end, "w", encoding="ascii")
            code, lines = main_to(argv, stream)
            try:
                discarded = os.path.samestat(os.fstat(write_end), os.stat(os.devnull))
            except OSError:
                discarded = False
            try:
                stream.close()
                closed_cleanly = True
            except OSError:
                closed_cleanly = False
            return code, lines, discarded, closed_cleanly

        def stdout_refused(lines, exc_name):
            return any("could not be written to stdout" in line and exc_name in line for line in lines)

        d, m = fresh()
        _publish(m, new_id, 9)
        wait_argv = ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5"]
        claim_path = os.path.join(base, "output-claim-pipe")
        code, lines, discarded, closed_cleanly = main_to_closed_pipe(["claim", claim_path])
        check("output/claim-broken-pipe-2", code == EXIT_CANNOT_EVALUATE and os.path.isdir(claim_path)
              and stdout_refused(lines, "BrokenPipeError") and discarded and closed_cleanly,
              (code, lines, discarded, closed_cleanly))
        code, lines, discarded, closed_cleanly = main_to_closed_pipe(wait_argv)
        check("output/wait-broken-pipe-2", code == EXIT_CANNOT_EVALUATE
              and stdout_refused(lines, "BrokenPipeError") and discarded and closed_cleanly,
              (code, lines, discarded, closed_cleanly))
        claim_path = os.path.join(base, "output-claim-eio")
        code, lines = main_to(["claim", claim_path], FailingStdout(OSError(5, "stdout failure injected")))
        check("output/claim-other-os-error-2", code == EXIT_CANNOT_EVALUATE and os.path.isdir(claim_path)
              and stdout_refused(lines, "OSError"), (code, lines))
        code, lines = main_to(wait_argv, FailingStdout(OSError(28, "stdout failure injected")))
        check("output/wait-other-os-error-2", code == EXIT_CANNOT_EVALUATE and stdout_refused(lines, "OSError"),
              (code, lines))
        claim_path = os.path.join(base, "output-claim-control")
        stdout = io.StringIO()
        code, lines = main_to(["claim", claim_path], stdout)
        check("output/claim-control-0", code == EXIT_OK and lines == [] and stdout.getvalue().endswith("\n")
              and is_dispatch_id(stdout.getvalue()[:-1]), (code, lines, stdout.getvalue()))
        stdout = io.StringIO()
        code, lines = main_to(wait_argv, stdout)
        check("output/wait-control-0", code == EXIT_OK and lines == [] and stdout.getvalue() == "9\n",
              (code, lines, stdout.getvalue()))

        # --- revert: with the id comparison removed, the incident case would pass as a match ---
        d, m = fresh()
        _publish(m,old_id, 0)
        real_matches = globals()["_id_matches"]
        globals()["_id_matches"] = lambda got, want: True
        try:
            reverted, _o, _e, _f = _wait(m, new_id)
        finally:
            globals()["_id_matches"] = real_matches
        intact, _o, _e, _f = _wait(m, new_id)
        check("revert/id-check-removed", reverted == EXIT_OK and intact == EXIT_ONLY_UNBOUND,
              (reverted, intact))

    if FAILURES:
        print("SELF-TEST FAIL: orch_dispatch_marker {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: orch_dispatch_marker {} checks (a stale marker with an earlier id, a legacy bare "
          "status, malformed content, another marker version, a late write by the earlier dispatch and a "
          "fresh mtime on a wrong id are never accepted, nor is an id differing only in its first or last "
          "character, and malformed content waits to exit 4, not 2; a matching id is accepted whatever its "
          "age, also when published in the last interval, and its worker status is printed, never used as "
          "the exit; non-regular, unreadable and oversized markers, also when swapped in after the lstat, "
          "and bad, wrong-length, repeated or out-of-range arguments, oversized numbers included, cannot "
          "evaluate, and an oversized number never reaches int or float; a marker removed before the open "
          "is pending; a claim never adopts an existing entry, one of two racing claims wins, and claims "
          "issue distinct ids, one claim's marker refused under another's; a write keeps the previous "
          "marker until the rename, fails cleanly on an fsync or rename error, leaves a temporary name it "
          "did not create alone and refuses a bad status; a failed stdout write is exit 2; with the id "
          "comparison removed the incident case would pass)".format(
              len(EXECUTED)))
    return 0


def main(argv, stdout=None, stderr=None):
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(os.path.dirname(os.path.realpath(__file__)))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    code, out, err = run_command(argv)
    return _deliver(code, out, err, sys.stdout if stdout is None else stdout,
                    sys.stderr if stderr is None else stderr)


def _discard_unwritten(stream):
    """Point a stream whose write failed at the null device, so the interpreter's final flush of the
    data still buffered cannot fail again and replace this exit code with Python's own 120."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(null, fd)
    except OSError:
        pass
    finally:
        os.close(null)


def _deliver(code, out, err, stdout, stderr):
    """The exit code after writing out to stdout and err to stderr. A stdout write or flush that fails
    (a broken pipe or any other operating-system error) is exit 2: the id or status never reached the
    caller, so the command cannot be relied on, whatever its own exit would have been."""
    if out:
        try:
            stdout.write(out)
            stdout.flush()
        except OSError as exc:
            _discard_unwritten(stdout)
            code = EXIT_CANNOT_EVALUATE
            err = list(err) + ["cannot evaluate: the result could not be written to stdout ({}: {})".format(
                type(exc).__name__, exc)]
    for line in err:
        print(line, file=stderr)
    return code


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
