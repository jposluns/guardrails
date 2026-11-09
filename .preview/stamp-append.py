#!/usr/bin/env python3
"""Writer helper (not a hook): append one record to a working-record file, stamped from the clock.

Motivated by records whose current-write time was typed by hand instead of read from the clock
(timestamp-from-clock): a time composed a few minutes behind the clock is not caught by the clock hooks,
because it is neither ahead of the clock nor in prose. This helper owns the stamp. The caller writes the
placeholder @@NOW_UTC@@ where the time goes, and the helper fills it from ONE clock read taken after it
holds the file's lock, as RFC 3339 UTC to the second (YYYY-MM-DDTHH:MM:SSZ). It never accepts a time from
the caller. Point record-remove-check.py's append mode at it (AIQT_STORE_WRITE_ROUTE, .preview/README.md,
Configuration) so a hand-typed shell append onto a store file is denied and names this helper.

Usage:
  python3 -I -S -B /ABSOLUTE/PATH/TO/stamp-append.py --file /ABSOLUTE/PATH [--format text|jsonl]
      [--field NAME] [--create] [--on-clock-failure refuse|unknown]  < record
  python3 -I -S -B /ABSOLUTE/PATH/TO/stamp-append.py --self-test

The record is read from stdin as strict UTF-8, at most 1 MiB.

Text mode (the default). The heading is the first line that is not blank. It must hold the placeholder
exactly once and no other date-time literal (a date and an HH:MM time, YYYY-MM-DD then T or a space then
HH:MM, the literal grammar future-stamp-write.py uses). The placeholder on any other line is refused, since
the helper cannot tell a slot from a quotation. Every other line is copied byte for byte and never read for
dates, so a historical time in quoted text, a code span, a code fence or a `>` line is left alone. A record
that does not end with LF gets one LF added, so the file keeps ending with a newline.

JSONL mode (--format jsonl). stdin holds one JSON object, parsed strictly: a duplicate key at any depth,
NaN, Infinity and -Infinity are refused. The field named by --field (default ts) must be absent or equal
the placeholder; any other value is refused, so the caller cannot supply the time. The placeholder in any
other key or value is refused. The object is written as one compact line (separators "," and ":", UTF-8
kept as is), with the field set to the stamp; an absent field is added first. Other values keep their
meaning, though a number is written in Python's own form (1e5 becomes 100000.0).

Order of steps:
  1. validate the arguments and the record;
  2. open the file with O_APPEND, O_NOFOLLOW and O_NONBLOCK, and require a regular file; an absent file is
     created only with --create;
  3. take an exclusive flock, waiting less than 5 seconds (a wait that reaches 5 seconds is refused);
  4. refuse a non-empty file whose last byte is not LF, and read the last stamp the helper wrote;
  5. read the clock;
  6. write the whole record, then fsync;
  7. print the stamped time on stdout.
If a write or the fsync fails, the file is truncated back to its size at step 4; if that also fails, the
refusal names the offset where the partial record starts.

Clock failure. The clock read raising, a reading earlier than CLOCK_FLOOR (2026-01-01T00:00:00Z, the mark
of an unset clock; a reading exactly at the floor is accepted), or a reading whose whole second is earlier
than the last stamp the helper wrote in the file (the clock stepped back; an equal second is accepted)
each refuse with exit 2 and write nothing. The helper never falls back to a caller value or the file's
modification time. With --on-clock-failure unknown it instead writes `unknown (clock unavailable)` in the
slot, records the time as unknown rather than guessed, and exits 0 with a warning on stderr. The last
stamp is read from the file's last 64 KiB: in JSONL mode the --field value of the last line when that line
is a JSON object and the value has the stamp form; in text mode the last stamp-form token on the last
line that holds one.

Exit status: 0 written (the stamp, or the unknown marker, on stdout); 2 refused or a usage error, with one
line on stderr and the file unchanged. An interpreter older than Python 3.14 that can start the file gets
one line beginning `error: stamp-append.py requires Python 3.14 or newer` on stderr and exit 2.

RESIDUAL COVERAGE. The helper protects only the writes made through it: Write and Edit tool writes,
interpreter writes, scripts and `bash -c` strings are not routed to it, and record-remove-check.py's append
mode is opt-in and covers only shell appends onto existing store files. A past composed time written to a
store by any other route is still not caught. The host clock is trusted, apart from the floor and the
step-back check; the stamp marks the start of the append, not its durable completion. The heading is the
first non-blank line only, so a store that keeps its time on a field line needs JSONL mode or is not
covered. A historical time in a heading is refused (move it into the body), and so is a quoted placeholder
in a body. Stamps are to the second, so two appends in one second carry equal stamps. flock is advisory: it
does not order writers that do not take it, and the truncation after a failed write can cut bytes such a
writer appended meanwhile. O_NOFOLLOW guards the final path component only. The step-back check reads only
the last 64 KiB and, in text mode, the last line holding a stamp-form token, which can be a body line
rather than the last heading: an earlier body stamp there weakens the check, and a later one refuses.

Self-test: python3 -I -S -B stamp-append.py --self-test (T1 to T15, in a temporary directory; no child
process is started, and no live clock decides a result).
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: stamp-append.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import calendar
import errno
import fcntl
import json
import math
import os
import re
import stat
import tempfile
import threading
import time

PLACEHOLDER = "@@NOW_UTC@@"
UNKNOWN_MARK = "unknown (clock unavailable)"
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# 2026-01-01T00:00:00Z: a reading earlier than this comes from a clock that was never set.
CLOCK_FLOOR = 1767225600
LOCK_WAIT_S = 5.0
LOCK_POLL_S = 0.05
MAX_RECORD = 1 << 20
TAIL_BYTES = 64 * 1024
# A date-time literal: a date, then T or a space, then HH:MM (the literal grammar of future-stamp-write.py).
LITERAL_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
# The one form the helper writes.
STAMP_RE = re.compile(r"(?<![0-9A-Za-z])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?![0-9A-Za-z])")

# Seams: the self-test replaces these to drive the clock, the lock wait and the write.
SEAMS = {
    "clock": time.time,
    "monotonic": time.monotonic,
    "sleep": time.sleep,
    "write": os.write,
    "fsync": os.fsync,
    "ftruncate": os.ftruncate,
}

USAGE = ("usage: stamp-append.py --file /ABSOLUTE/PATH [--format text|jsonl] [--field NAME] [--create] "
         "[--on-clock-failure refuse|unknown] < record; or stamp-append.py --self-test")


class Refusal(Exception):
    """A refusal: exit 2, one line on stderr, the file unchanged."""


def _parse_args(argv):
    opts = {"file": None, "format": "text", "field": None, "create": False, "on_clock_failure": "refuse"}
    valued = {"--file": "file", "--format": "format", "--field": "field",
              "--on-clock-failure": "on_clock_failure"}
    seen = set()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in seen:
            raise Refusal("{} is given twice".format(arg))
        seen.add(arg)
        if arg == "--create":
            opts["create"] = True
            i += 1
            continue
        if arg not in valued:
            raise Refusal("unrecognized argument {!r}".format(arg[:80]))
        if i + 1 >= len(argv):
            raise Refusal("{} needs a value".format(arg))
        opts[valued[arg]] = argv[i + 1]
        i += 2
    if opts["file"] is None:
        raise Refusal("--file is required")
    if "\0" in opts["file"] or not os.path.isabs(opts["file"]):
        raise Refusal("--file must be an absolute path")
    if opts["format"] not in ("text", "jsonl"):
        raise Refusal("--format must be text or jsonl")
    if opts["on_clock_failure"] not in ("refuse", "unknown"):
        raise Refusal("--on-clock-failure must be refuse or unknown")
    if opts["field"] is not None and opts["format"] != "jsonl":
        raise Refusal("--field applies to --format jsonl only")
    if opts["field"] is None:
        opts["field"] = "ts"
    if not opts["field"]:
        raise Refusal("--field must not be empty")
    return opts


# --- step 1: the record ---------------------------------------------------------------------------

def _text_template(text):
    """Validate a text record; return a function of the slot value giving the record's bytes."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line + "\n" for line in lines]
    heading = None
    for index, line in enumerate(lines):
        if line.strip():
            heading = index
            break
    if heading is None:
        raise Refusal("the record has no heading (no line that is not blank)")
    head = lines[heading]
    count = head.count(PLACEHOLDER)
    if count == 0:
        raise Refusal("the heading does not hold the placeholder {}".format(PLACEHOLDER))
    if count > 1:
        raise Refusal("the heading holds the placeholder {} times; it must hold it once".format(count))
    if LITERAL_RE.search(head.replace(PLACEHOLDER, "")):
        raise Refusal("the heading holds a date-time literal besides the placeholder; a time in the heading "
                      "comes from the clock, so move a historical time into the body")
    for number, line in enumerate(lines, 1):
        if number - 1 != heading and PLACEHOLDER in line:
            raise Refusal("line {} holds the placeholder outside the heading".format(number))

    def render(slot):
        out = list(lines)
        out[heading] = head.replace(PLACEHOLDER, slot)
        return "".join(out).encode("utf-8")
    return render


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise Refusal("the JSON object repeats the key {!r}".format(key[:80]))
        obj[key] = value
    return obj


def _no_constant(name):
    raise Refusal("the JSON record holds {}, which is not JSON".format(name))


def _holds_placeholder(value):
    if isinstance(value, str):
        return PLACEHOLDER in value
    if isinstance(value, dict):
        return any(_holds_placeholder(k) or _holds_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_holds_placeholder(v) for v in value)
    return False


def _jsonl_template(text, field):
    """Validate a JSONL record; return a function of the slot value giving the record's bytes."""
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except Refusal:
        raise
    except (ValueError, RecursionError, MemoryError) as exc:
        raise Refusal("the record is not one JSON object ({})".format(str(exc)[:120]))
    if not isinstance(obj, dict):
        raise Refusal("the record is not a JSON object")
    if field in obj and obj[field] != PLACEHOLDER:
        raise Refusal("the record supplies {!r}; it must be absent or {}, since the helper reads the time "
                      "itself".format(field[:80], PLACEHOLDER))
    if PLACEHOLDER in field:
        raise Refusal("the field name holds the placeholder")
    rest = {k: v for k, v in obj.items() if k != field}
    if _holds_placeholder(rest):
        raise Refusal("the placeholder appears outside the {!r} field".format(field[:80]))

    def render(slot):
        if field in obj:
            out = {k: (slot if k == field else v) for k, v in obj.items()}
        else:
            out = {field: slot}
            out.update(obj)
        try:
            line = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
            return (line + "\n").encode("utf-8")
        except (ValueError, RecursionError, UnicodeEncodeError) as exc:
            raise Refusal("the record cannot be written as UTF-8 JSON ({})".format(str(exc)[:120]))
    render(PLACEHOLDER)  # surface an unwritable record (a lone surrogate) before the file is opened
    return render


# --- steps 2 to 4: the file -----------------------------------------------------------------------

def _open_flags(create):
    flags = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    return flags


def _open(path, create):
    try:
        fd = os.open(path, _open_flags(create), 0o666)
    except FileNotFoundError:
        raise Refusal("{} does not exist (pass --create to create it)".format(path))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refusal("{} is a symbolic link".format(path))
        raise Refusal("cannot open {}: {}".format(path, exc.strerror or exc))
    try:
        mode = os.fstat(fd).st_mode
    except OSError as exc:
        os.close(fd)
        raise Refusal("cannot stat {}: {}".format(path, exc.strerror or exc))
    if not stat.S_ISREG(mode):
        os.close(fd)
        raise Refusal("{} is not a regular file".format(path))
    return fd


def _lock(fd):
    start = SEAMS["monotonic"]()
    while True:
        if SEAMS["monotonic"]() - start >= LOCK_WAIT_S:
            raise Refusal("the file's lock was not free within {:g} seconds".format(LOCK_WAIT_S))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise Refusal("cannot lock the file: {}".format(exc.strerror or exc))
        SEAMS["sleep"](LOCK_POLL_S)


def _stamp_epoch(token):
    try:
        parsed = time.strptime(token, STAMP_FORMAT)
    except ValueError:
        return None
    return calendar.timegm(parsed)


def _last_stamp(fd, size, fmt, field):
    """The epoch second of the last stamp the helper wrote, read from the file's last TAIL_BYTES, or None."""
    if size == 0:
        return None
    start = max(0, size - TAIL_BYTES)
    tail = os.pread(fd, size - start, start).decode("utf-8", "replace")
    lines = tail.split("\n")[:-1]
    if start > 0 and lines:
        lines = lines[1:]  # the first line may be cut by the window
    if fmt == "jsonl":
        if not lines:
            return None
        try:
            obj = json.loads(lines[-1])
        except (ValueError, RecursionError):
            return None
        value = obj.get(field) if isinstance(obj, dict) else None
        if isinstance(value, str) and STAMP_RE.fullmatch(value):
            return _stamp_epoch(value)
        return None
    for line in reversed(lines):
        for token in reversed(STAMP_RE.findall(line)):
            epoch = _stamp_epoch(token)
            if epoch is not None:
                return epoch
    return None


def _check_end(fd):
    size = os.fstat(fd).st_size
    if size > 0 and os.pread(fd, 1, size - 1) != b"\n":
        raise Refusal("the file does not end with a newline; a record appended now would join its last line")
    return size


# --- step 5: the clock ----------------------------------------------------------------------------

def _read_clock(last):
    """Return (stamp, None) from one clock read, or (None, reason) on a clock failure."""
    try:
        reading = SEAMS["clock"]()
    except Exception as exc:  # noqa: BLE001  any failure of the clock read is a clock failure
        return None, "the clock read failed ({})".format(type(exc).__name__)
    if not isinstance(reading, (int, float)) or math.isnan(reading):
        return None, "the clock gave no usable reading"
    if not reading >= CLOCK_FLOOR:
        return None, "the clock reads earlier than {} (an unset clock)".format(
            time.strftime(STAMP_FORMAT, time.gmtime(CLOCK_FLOOR)))
    try:
        second = math.floor(reading)
        stamp = time.strftime(STAMP_FORMAT, time.gmtime(second))
    except (OverflowError, ValueError, OSError):
        return None, "the clock reading cannot be written as a UTC time"
    if last is not None and second < last:
        return None, "the clock reads {}, earlier than the file's last stamp {} (the clock stepped back)".format(
            stamp, time.strftime(STAMP_FORMAT, time.gmtime(last)))
    return stamp, None


# --- step 6: the write ----------------------------------------------------------------------------

def _write_all(fd, data, size):
    written = 0
    try:
        while written < len(data):
            n = SEAMS["write"](fd, data[written:])
            if not n or n < 0:
                raise OSError(errno.EIO, "a write wrote no bytes")
            written += n
        SEAMS["fsync"](fd)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        try:
            SEAMS["ftruncate"](fd, size)
        except OSError as exc2:
            raise Refusal("the write failed ({}) and the file could not be truncated back ({}); a partial "
                          "record may start at offset {}".format(reason, exc2.strerror or exc2, size))
        raise Refusal("the write failed ({}); the file was truncated back to its original size {}".format(
            reason, size))


def append(argv, data):
    """Run one append. Returns (exit code, stdout text, stderr text)."""
    try:
        opts = _parse_args(argv)
        if len(data) > MAX_RECORD:
            raise Refusal("the record is larger than {} bytes".format(MAX_RECORD))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise Refusal("the record is not valid UTF-8")
        if opts["format"] == "jsonl":
            render = _jsonl_template(text, opts["field"])
        else:
            render = _text_template(text)
        fd = _open(opts["file"], opts["create"])
        try:
            _lock(fd)
            size = _check_end(fd)
            last = _last_stamp(fd, size, opts["format"], opts["field"])
            stamp, failure = _read_clock(last)
            warning = ""
            if failure is not None:
                if opts["on_clock_failure"] != "unknown":
                    raise Refusal(failure)
                stamp = UNKNOWN_MARK
                warning = "stamp-append: warning: {}; the time was recorded as unknown\n".format(failure)
            _write_all(fd, render(stamp), size)
        finally:
            os.close(fd)
        return 0, stamp + "\n", warning
    except Refusal as exc:
        return 2, "", "stamp-append: refused: {}; nothing was written\n".format(exc)
    except OSError as exc:
        return 2, "", "stamp-append: refused: {}; nothing was written\n".format(exc.strerror or exc)


# --- self-test ------------------------------------------------------------------------------------

T0 = 1793534400  # 2026-11-01T12:00:00Z, a fixed reading: no live clock decides a result


class _Seams:
    """Replace SEAMS entries for one test and restore them afterwards."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        self.saved = {k: SEAMS[k] for k in self.values}
        SEAMS.update(self.values)
        return self

    def __exit__(self, *exc):
        SEAMS.update(self.saved)
        return False


def _fixed(value):
    return lambda: value


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _put(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _expect(cond, message):
    if not cond:
        raise AssertionError(message)


def _refused(code, err, before, path, needle):
    _expect(code == 2, "expected exit 2, got {} ({})".format(code, err.strip()))
    _expect(needle in err, "the refusal lacks {!r}: {}".format(needle, err.strip()))
    _expect(_read(path) == before, "the file changed on a refusal")


def _store(work, name, data=b"# log\n"):
    path = os.path.join(work, name)
    _put(path, data)
    return path


def t1(work):
    path = _store(work, "t1")
    for head in ("## 2026-10-01 09:50 @@NOW_UTC@@ note\n", "## @@NOW_UTC@@ 2026-10-01T09:50 note\n"):
        code, _, err = append(["--file", path], head.encode())
        _refused(code, err, b"# log\n", path, "date-time literal")


def t2(work):
    path = _store(work, "t2")
    code, _, err = append(["--file", path], b"## note\nbody\n")
    _refused(code, err, b"# log\n", path, "does not hold the placeholder")


def t3(work):
    path = _store(work, "t3")
    code, _, err = append(["--file", path], b"## @@NOW_UTC@@ and @@NOW_UTC@@\n")
    _refused(code, err, b"# log\n", path, "2 times")


def t4(work):
    path = _store(work, "t4")
    code, _, err = append(["--file", path], b"## @@NOW_UTC@@ note\nquoted @@NOW_UTC@@ here\n")
    _refused(code, err, b"# log\n", path, "outside the heading")


def t5(work):
    path = _store(work, "t5")
    body = ("plain 2026-10-01 09:50 UTC was the old time\n"
            "> quoted 2026-10-01T09:50:00Z\n"
            "```\n2025-01-01T00:00:00Z in a fence\n```\n"
            "a span `2026-02-03 04:05` and a CR line\r\n"
            "last line with no newline")
    record = "\n## @@NOW_UTC@@ entry\n" + body
    with _Seams(clock=_fixed(T0)):
        code, out, err = append(["--file", path], record.encode())
    _expect(code == 0, "expected exit 0, got {} ({})".format(code, err.strip()))
    want = b"# log\n" + ("\n## 2026-11-01T12:00:00Z entry\n" + body + "\n").encode()
    _expect(_read(path) == want, "the record was not written with only the slot filled: {!r}".format(_read(path)))
    _expect(out == "2026-11-01T12:00:00Z\n", "stdout {!r} is not the stamp".format(out))


def t6(work):
    path = _store(work, "t6")
    reading = {"now": T0}
    blocked = threading.Event()
    result = {}

    def sleep(seconds):
        blocked.set()
        time.sleep(seconds)

    def worker():
        result["r"] = append(["--file", path], b"## @@NOW_UTC@@ x\n")

    holder = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with _Seams(clock=lambda: reading["now"], sleep=sleep):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            _expect(blocked.wait(30), "the helper never waited on the held lock")
            reading["now"] = T0 + 7
            fcntl.flock(holder, fcntl.LOCK_UN)
            thread.join(30)
            _expect(not thread.is_alive(), "the helper did not finish after the lock was released")
    finally:
        os.close(holder)
    code, out, err = result.get("r", (None, "", "no result"))
    _expect(code == 0, "expected exit 0, got {} ({})".format(code, err.strip()))
    _expect(out == "2026-11-01T12:00:07Z\n", "the stamp {!r} is not the reading after the lock".format(out))


def _raise():
    raise OSError(errno.EINVAL, "clock unavailable (seeded)")


def t7(work):
    path = _store(work, "t7")
    with _Seams(clock=_raise):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
    _refused(code, err, b"# log\n", path, "clock read failed")


def t8(work):
    cases = (("just outside", CLOCK_FLOOR - 1e-6, 2), ("exactly at", CLOCK_FLOOR, 0),
             ("just inside", CLOCK_FLOOR + 1e-6, 0))
    for label, reading, want in cases:
        path = _store(work, "t8-" + label.replace(" ", "-"))
        with _Seams(clock=_fixed(reading)):
            code, out, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
        _expect(code == want, "floor {}: expected exit {}, got {} ({})".format(label, want, code, err.strip()))
        if want == 0:
            _expect(out == "2026-01-01T00:00:00Z\n", "floor {}: stamp {!r}".format(label, out))
        else:
            _refused(code, err, b"# log\n", path, "unset clock")


def t9(work):
    prior = b"# log\nbody 2020-01-01T00:00:00Z\n## 2026-11-01T12:00:00Z earlier\nbody text\n"
    jprior = b'{"ts":"2026-11-01T12:00:00Z","n":1}\n'
    cases = (("-1 s", T0 - 1, 2), ("-1 us", T0 - 1e-6, 2), ("equal", T0, 0), ("+1 s", T0 + 1, 0))
    for label, reading, want in cases:
        for fmt, data, record in (("text", prior, b"## @@NOW_UTC@@ x\n"), ("jsonl", jprior, b'{"n":2}')):
            path = _store(work, "t9-{}-{}".format(fmt, label.replace(" ", "")), data)
            with _Seams(clock=_fixed(reading)):
                code, _, err = append(["--file", path, "--format", fmt], record)
            _expect(code == want, "last stamp {} ({}): expected exit {}, got {} ({})".format(
                label, fmt, want, code, err.strip()))
            if want == 2:
                _refused(code, err, data, path, "stepped back")


def t10(work):
    path = _store(work, "t10", b"")
    good = (b'{"msg":"seen 2026-10-01T09:50:00Z","n":1.5}', b'{"ts":"@@NOW_UTC@@","msg":"x"}')
    wants = (b'{"ts":"2026-11-01T12:00:00Z","msg":"seen 2026-10-01T09:50:00Z","n":1.5}\n',
             b'{"ts":"2026-11-01T12:00:00Z","msg":"x"}\n')
    expected = b""
    for record, want in zip(good, wants):
        with _Seams(clock=_fixed(T0)):
            code, _, err = append(["--file", path, "--format", "jsonl"], record)
        _expect(code == 0, "jsonl {!r}: expected exit 0, got {} ({})".format(record, code, err.strip()))
        expected += want
        _expect(_read(path) == expected, "jsonl {!r}: wrote {!r}".format(record, _read(path)))
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path, "--format", "jsonl", "--field", "at"], b'{"ts":"kept"}')
    expected += b'{"at":"2026-11-01T12:00:00Z","ts":"kept"}\n'
    _expect(code == 0 and _read(path) == expected, "--field at: exit {}, wrote {!r}".format(code, _read(path)))
    bad = ((b'{"ts":"2026-11-01T11:59:00Z"}', "supplies"),
           (b'{"a":1,"a":2}', "repeats the key"),
           (b'{"a":{"b":1,"b":2}}', "repeats the key"),
           (b'{"a":NaN}', "NaN"),
           (b'{"a":-Infinity}', "Infinity"),
           (b'{"note":"@@NOW_UTC@@"}', "outside"),
           (b'{"x":["@@NOW_UTC@@"]}', "outside"),
           (b'[1,2]', "not a JSON object"),
           (b'{"a":1}{"b":2}', "not one JSON object"))
    for record, needle in bad:
        with _Seams(clock=_fixed(T0)):
            code, _, err = append(["--file", path, "--format", "jsonl"], record)
        _refused(code, err, expected, path, needle)


def t11(work):
    path = _store(work, "t11", b"# log\nno final newline")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
    _refused(code, err, b"# log\nno final newline", path, "does not end with a newline")


def t12(work):
    real = _store(work, "t12-real")
    link = os.path.join(work, "t12-link")
    os.symlink(real, link)
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", link], b"## @@NOW_UTC@@ x\n")
    _refused(code, err, b"# log\n", real, "symbolic link")
    fifo = os.path.join(work, "t12-fifo")
    os.mkfifo(fifo)
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", fifo], b"## @@NOW_UTC@@ x\n")
    _expect(code == 2 and "not a regular file" in err, "a FIFO target: exit {} ({})".format(code, err.strip()))
    missing = os.path.join(work, "t12-missing")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", missing], b"## @@NOW_UTC@@ x\n")
    _expect(code == 2 and not os.path.exists(missing), "an absent file without --create was not refused")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", missing, "--create"], b"## @@NOW_UTC@@ x\n")
    _expect(code == 0 and _read(missing) == b"## 2026-11-01T12:00:00Z x\n", "--create did not create the file")


def t13(work):
    # Attempts happen at the listed times; the lock is held until `release`. The deadline is LOCK_WAIT_S
    # after the first attempt: an attempt strictly inside it may take the lock; at the deadline it refuses.
    times = [0.0, LOCK_WAIT_S - 1e-6, LOCK_WAIT_S, LOCK_WAIT_S + 1e-6]
    cases = (("released just inside", LOCK_WAIT_S - 1e-6, 0), ("held to the deadline", LOCK_WAIT_S, 2),
             ("held past the deadline", LOCK_WAIT_S + 1e-6, 2))
    for label, release, want in cases:
        path = _store(work, "t13-" + label.replace(" ", "-"))
        clock = {"i": 0}
        real_flock = fcntl.flock

        def mono():
            return times[min(clock["i"], len(times) - 1)]

        def sleep(_seconds):
            clock["i"] += 1
            if clock["i"] >= len(times) + 2:
                raise OSError(errno.ETIMEDOUT, "the lock wait did not end (seeded bound)")

        def flock(fd, op, release=release):
            if op & fcntl.LOCK_EX and mono() < release:
                raise OSError(errno.EWOULDBLOCK, "held (seeded)")
            return real_flock(fd, op)

        fcntl.flock = flock
        try:
            with _Seams(clock=_fixed(T0), monotonic=mono, sleep=sleep):
                code, _, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
        finally:
            fcntl.flock = real_flock
        _expect(code == want, "{}: expected exit {}, got {} ({})".format(label, want, code, err.strip()))
        if want == 2:
            _refused(code, err, b"# log\n", path, "not free within")


def t14(work):
    for label, clock in (("raising clock", _raise), ("unset clock", _fixed(0))):
        path = _store(work, "t14-" + label.replace(" ", "-"))
        with _Seams(clock=clock):
            code, out, err = append(["--file", path, "--on-clock-failure", "unknown"], b"## @@NOW_UTC@@ x\n")
        _expect(code == 0, "{}: expected exit 0, got {} ({})".format(label, code, err.strip()))
        added = _read(path)[len(b"# log\n"):]
        _expect(added == b"## unknown (clock unavailable) x\n", "{}: wrote {!r}".format(label, added))
        _expect(not STAMP_RE.search(added.decode()), "{}: a time was written".format(label))
        _expect(out == UNKNOWN_MARK + "\n" and "warning" in err, "{}: stdout {!r}".format(label, out))
    path = _store(work, "t14-jsonl", b"")
    with _Seams(clock=_raise):
        code, _, _ = append(["--file", path, "--format", "jsonl", "--on-clock-failure", "unknown"], b'{"a":1}')
    _expect(code == 0 and _read(path) == b'{"ts":"unknown (clock unavailable)","a":1}\n',
            "jsonl unknown: wrote {!r}".format(_read(path)))


def t15(work):
    path = _store(work, "t15")

    def partial(fd, data):
        if len(data) > 4:
            return os.write(fd, data[:4])
        raise OSError(errno.ENOSPC, "No space left on device (seeded)")

    with _Seams(clock=_fixed(T0), write=partial):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ a longer record\n")
    _refused(code, err, b"# log\n", path, "truncated back")

    def no_truncate(fd, size):
        raise OSError(errno.EIO, "seeded")

    with _Seams(clock=_fixed(T0), write=partial, ftruncate=no_truncate):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ a longer record\n")
    _expect(code == 2 and "offset 6" in err, "an untruncatable partial write: exit {} ({})".format(code, err.strip()))


TESTS = (("T1", "a date-time literal in the heading is refused", t1),
         ("T2", "a missing placeholder is refused", t2),
         ("T3", "a duplicate placeholder is refused", t3),
         ("T4", "the placeholder in the body is refused", t4),
         ("T5", "body bytes are kept and only the slot is filled", t5),
         ("T6", "the clock is read after the lock is taken", t6),
         ("T7", "a raising clock refuses with the file unchanged", t7),
         ("T8", "the clock floor boundary", t8),
         ("T9", "the last-stamp boundary", t9),
         ("T10", "JSONL strictness and the owned field", t10),
         ("T11", "a file with no final newline is refused", t11),
         ("T12", "a symbolic link or non-regular target is refused", t12),
         ("T13", "the lock wait bound", t13),
         ("T14", "the unknown mode writes the marker, never a time", t14),
         ("T15", "a partial write is truncated back", t15))


def self_test():
    failures = []
    try:
        base = tempfile.mkdtemp(prefix="stamp-append-selftest-")
    except OSError as exc:
        print("SELF-TEST FAIL: no temporary directory: {}".format(exc))
        return 1
    try:
        for test_id, label, fn in TESTS:
            work = os.path.join(base, test_id)
            os.mkdir(work)
            try:
                fn(work)
                print("{} PASS: {}".format(test_id, label))
            except Exception as exc:  # noqa: BLE001  any escape is that test's failure
                failures.append(test_id)
                print("{} FAIL: {}: {}: {}".format(test_id, label, type(exc).__name__, exc))
    finally:
        for root, dirs, files in os.walk(base, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(base)
    if failures:
        print("SELF-TEST FAIL: {} of {} test(s) failed: {}".format(len(failures), len(TESTS), ", ".join(failures)))
        return 1
    print("SELF-TEST PASS: {} test(s)".format(len(TESTS)))
    return 0


def main():
    argv = sys.argv[1:]
    if argv == ["--self-test"]:
        return self_test()
    if "--self-test" in argv:
        sys.stderr.write("stamp-append: refused: --self-test takes no other argument\n" + USAGE + "\n")
        return 2
    if not argv:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        data = sys.stdin.buffer.read(MAX_RECORD + 1)
    except (OSError, AttributeError, ValueError) as exc:
        sys.stderr.write("stamp-append: refused: cannot read the record from stdin ({}); nothing was "
                         "written\n".format(exc))
        return 2
    code, out, err = append(argv, data)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
