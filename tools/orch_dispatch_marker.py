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
      parent, any other operating-system error).
  orch_dispatch_marker.py write --marker PATH --id HEX --status N
      Write the line `aiqt-dispatch-marker v1 <id> <status>` to a temporary file in PATH's directory,
      fsync it, then rename it onto PATH. N is an integer from 0 to 255 with no sign or leading zero.
      Exit 0 published; 2 cannot evaluate (bad arguments, a missing directory, a target that is a
      directory, a write or rename failure).
  orch_dispatch_marker.py wait --marker PATH --expect-id HEX --deadline-s S [--interval-s I]
      Poll PATH on a monotonic clock until S seconds (an integer from 1 to 86400) pass, every I seconds
      (from 0.05 to 60, default 1). A marker is accepted only when lstat shows a regular file of at
      most MAX_MARKER_BYTES bytes whose whole content is one v1 line carrying the expected id; the
      worker status it carries is then printed on stdout. A missing marker (a temporary file not yet
      renamed included) is still pending. A marker with another id, a legacy bare status line, or a
      malformed one is present but unbound: it is never accepted, and waiting continues, since the
      matching marker may still be renamed over it.
      Exit 0 matched (the worker status goes on stdout and never becomes this exit code); 3 the
      deadline passed and no marker was ever seen; 4 the deadline passed and only unbound markers
      were seen, each named on stderr; 2 cannot evaluate, at once (bad arguments, a relative path, a
      missing result directory, a symlink, FIFO, directory or other non-regular marker, a read or
      permission error, an oversized marker).
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
    if not text or not all(c in DECIMAL_DIGITS for c in text):
        return None
    if len(text) > 1 and text[0] == "0":
        return None
    value = int(text)
    return value if value <= STATUS_MAX else None


def parse_deadline(text):
    """The integer deadline in seconds (1 to DEADLINE_MAX_S, canonical digits), or None."""
    if not text or not all(c in DECIMAL_DIGITS for c in text) or text[0] == "0":
        return None
    value = int(text)
    return value if value <= DEADLINE_MAX_S else None


def parse_interval(text):
    """The poll interval in seconds (INTERVAL_MIN_S to INTERVAL_MAX_S, plain decimal), or None."""
    if not text or text.count(".") > 1 or not all(c in DECIMAL_DIGITS + "." for c in text):
        return None
    if not text.replace(".", ""):
        return None
    value = float(text)
    return value if INTERVAL_MIN_S <= value <= INTERVAL_MAX_S else None


def marker_line(dispatch_id, status):
    return "{} {} {} {}\n".format(MARKER_MAGIC, MARKER_VERSION, dispatch_id, status)


def _id_matches(got, want):
    """The binding itself: a marker belongs to this dispatch only when it carries this dispatch's id."""
    return got == want


def judge_marker(data, expect_id):
    """("match", status) or ("unbound", reason) for a marker's bytes, which are never accepted on
    presence, age or a bare status alone."""
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


def _open_marker(path):
    """A read-only descriptor for the marker that never follows a symlink and never blocks on a FIFO."""
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


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
    temp = os.path.join(directory, ".{}.tmp-{}".format(os.path.basename(path), secrets.token_hex(8)))
    data = marker_line(dispatch_id, status).encode("ascii")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
    except OSError as exc:
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

        d, m = fresh()
        for name, argv in (
                ("wait/bad-args-2/relative-path",
                 ["wait", "--marker", "rel/exit-status", "--expect-id", new_id, "--deadline-s", "5"]),
                ("wait/bad-args-2/id-not-32-hex",
                 ["wait", "--marker", m, "--expect-id", new_id.upper(), "--deadline-s", "5"]),
                ("wait/bad-args-2/deadline-zero",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "0"]),
                ("wait/bad-args-2/deadline-not-integer",
                 ["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "1.5"]),
                ("wait/bad-args-2/missing-expect-id",
                 ["wait", "--marker", m, "--deadline-s", "5"])):
            fake = _FakeClock()
            code, out, err = run_command(argv, fake.clock, fake.sleep)
            check(name, code == EXIT_CANNOT_EVALUATE and fake.sleeps == 0
                  and any("wait needs" in line or "usage:" in line for line in err), (code, out, err))
        _publish(m,new_id, 3)
        fake = _FakeClock()
        code, out, err = run_command(["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "5",
                                      "--interval-s", "0.05"], fake.clock, fake.sleep)
        check("wait/cli-valid-args-0", code == EXIT_OK and out == "3\n", (code, out, err))

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
                results[i] = claim_dir(paths[i])[0]

            threads = [threading.Thread(target=one, args=(i,)) for i in range(len(paths))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return results

        same = os.path.join(base, "claim-race")
        got = race([same, same])
        check("claim/race-one-winner", sorted(got) == [EXIT_OK, EXIT_EXISTS], got)
        got = race([os.path.join(base, "claim-race-a"), os.path.join(base, "claim-race-b")])
        check("claim/race-distinct-both-0", got == [EXIT_OK, EXIT_OK], got)

        # --- write ---
        d, m = fresh()
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "0"])
        content = None
        if os.path.lexists(m):
            with open(m, "rb") as handle:
                content = handle.read()
        check("write/publish-atomic-0", code == EXIT_OK and content == marker_line(new_id, 0).encode("ascii")
              and sorted(os.listdir(d)) == ["exit-status"], (code, content, sorted(os.listdir(d))))

        d, m = fresh()
        for name, status in (("write/bad-status-2/256", "256"), ("write/bad-status-2/minus-1", "-1"),
                             ("write/bad-status-2/leading-zero", "07")):
            code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", status])
            check(name, code == EXIT_CANNOT_EVALUATE and not os.path.lexists(m), (code, out, err))
        code, out, err = run_command(["write", "--marker", m, "--id", new_id, "--status", "255"])
        code2, out2, err2, _f = _wait(m, new_id)
        check("write/status-255-0", code == EXIT_OK and code2 == EXIT_OK and out2 == "255\n",
              (code, err, code2, out2))

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
          "status, a late write by the earlier dispatch and a fresh mtime on a wrong id are never accepted; "
          "a matching id is accepted whatever its age and its worker status is printed, never used as the "
          "exit; non-regular, unreadable and oversized markers and bad arguments cannot evaluate; a claim "
          "never adopts an existing entry and one of two racing claims wins; a write publishes atomically "
          "and refuses a bad status; with the id comparison removed the incident case would pass)".format(
              len(EXECUTED)))
    return 0


def main(argv):
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
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    for line in err:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
