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
kept as is), with the field set to the stamp; an absent field is added first. Numbers: an integer keeps
its exact value, written in plain decimal (-0 becomes 0). A number with a fraction or an exponent is read
as a binary float and written in Python's shortest form for that float (1e5 becomes 100000.0, 1.10 becomes
1.1); one whose written form would be a different value is refused, whether too large for a float (1e400,
-1e400), too small (1e-400) or more precise than a float holds (9007199254740993.0, 1.00000000000000000001).
A number whose exponent is past what the decimal module can hold (1e-999999999999999999999,
0e999999999999999999999) is refused too, since its exact value cannot be checked. Strings, booleans and
null are kept.

Order of steps:
  1. validate the arguments and the record;
  2. open the file with O_APPEND, O_NOFOLLOW and O_NONBLOCK, and require a regular file; an absent file is
     created only with --create, and a run that creates it and then refuses removes it again only when
     cleanup can proceed safely (see Exit status), otherwise the refusal line says the file was left;
  3. take an exclusive flock, waiting less than 5 seconds (a wait that reaches 5 seconds is refused);
  4. refuse a non-empty file whose last byte is not LF, and read the last stamp the helper wrote;
  5. read the clock;
  6. write the whole record, then fsync;
  7. print the stamped time on stdout.
If a write or the fsync fails, the file is truncated back to its size at step 4; if that also fails, the
refusal names the offset where the record, whole or in part, may start.

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
line on stderr. That line ends `nothing was written` only when the file holds what it held before the run.
When bytes of the record may remain (a write or the fsync failed and the truncation back failed too, or
closing the file failed after the record was written and synced), it ends instead `the file may hold part
or all of the record, so check it before retrying`; a file --create made is then left in place with those
bytes. Otherwise a file --create made is removed only when cleanup can proceed safely (the run takes its
lock, the path still names it, it is still empty and the removal succeeds); when it is not removed, the
line says the empty file was left in place and why. A control character in a path or message (LF, CR,
and the like) is written escaped (\\n, \\r, \\xNN), so the refusal stays one line. An interpreter older
than Python 3.14 that can start the file gets one line beginning `error: stamp-append.py requires Python
3.14 or newer` on stderr and exit 2.

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
rather than the last heading: an earlier body stamp there weakens the check, and a later one refuses. A
file --create made is removed on a refusal only while the run holds its lock and the path still names it
empty; between that check and the removal another process could replace the path, and a writer that opened
the file before the removal goes on writing to an unlinked file. A backslash in a path is not escaped in a
refusal line, so a name holding a backslash then n reads the same there as a name holding LF.

Self-test: python3 -I -S -B stamp-append.py --self-test (T1 to T19, in a temporary directory; no child
process is started, and no live clock decides a passing result: T6 waits on a worker thread with a 30-second
live hang guard that can only fail it).
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
import decimal
import errno
import fcntl
import io
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

# Seams: the self-test replaces these to drive the clock, the lock wait, the write, the close and the
# removal of a file --create made.
SEAMS = {
    "clock": time.time,
    "monotonic": time.monotonic,
    "sleep": time.sleep,
    "write": os.write,
    "fsync": os.fsync,
    "ftruncate": os.ftruncate,
    "close": os.close,
    "unlink": os.unlink,
}

USAGE = ("usage: stamp-append.py --file /ABSOLUTE/PATH [--format text|jsonl] [--field NAME] [--create] "
         "[--on-clock-failure refuse|unknown] < record; or stamp-append.py --self-test")


class Refusal(Exception):
    """A refusal: exit 2 and one line on stderr.

    changed is True when bytes of the record may remain in the file; left names why an empty file that
    --create made was left in place. With neither, the file holds what it held before the run.
    """

    def __init__(self, message, changed=False):
        super().__init__(message)
        self.message = message
        self.changed = changed
        self.left = None


# Characters that could end or garble the one stderr line: C0 and C1 controls (LF and CR among them), DEL,
# and the Unicode line and paragraph separators.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")
_CONTROL_NAMES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _one_line(text):
    """Text with every control character escaped (LF as \\n, CR as \\r, others as \\xNN or \\uNNNN)."""
    def escape(match):
        char = match.group()
        if char in _CONTROL_NAMES:
            return _CONTROL_NAMES[char]
        return "\\x{:02x}".format(ord(char)) if ord(char) < 0x100 else "\\u{:04x}".format(ord(char))
    return _CONTROL_RE.sub(escape, str(text))


def _refusal_line(exc):
    # A path or an OS message can hold LF or CR; escaping them keeps the refusal on one stderr line.
    if exc.changed:
        tail = "the file may hold part or all of the record, so check it before retrying"
    elif exc.left is not None:
        tail = "no record was written, but the empty file --create made was left in place ({})".format(
            _one_line(exc.left))
    else:
        tail = "nothing was written"
    return "stamp-append: refused: {}; {}\n".format(_one_line(exc.message), tail)


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


def _exact_float(token):
    """Parse a JSON number with a fraction or exponent; refuse one the float written for it would change."""
    # A conversion that fails is a refusal, never a traceback: decimal raises InvalidOperation for an
    # exponent past its limits (1e-999999999999999999999, 0e999999999999999999999).
    try:
        value = float(token)
    except (ArithmeticError, ValueError, MemoryError) as exc:
        raise _unreadable_number(token, exc)
    if not math.isfinite(value):
        raise Refusal("the JSON record holds the number {}, which is too large for a float".format(token[:40]))
    try:
        same = decimal.Decimal(repr(value)) == decimal.Decimal(token)
    except (ArithmeticError, ValueError, MemoryError) as exc:
        raise _unreadable_number(token, exc)
    if not same:
        raise Refusal("the JSON record holds the number {}, which would be written as {}, a different "
                      "value".format(token[:40], repr(value)))
    return value


def _unreadable_number(token, exc):
    return Refusal("the JSON record holds the number {}, whose exact value cannot be checked ({})".format(
        token[:40], type(exc).__name__))


def _holds_placeholder(value):
    # Iterative, so a deeply nested record cannot exhaust the interpreter's recursion limit.
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if PLACEHOLDER in item:
                return True
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.append(key)
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _jsonl_template(text, field):
    """Validate a JSONL record; return a function of the slot value giving the record's bytes."""
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                         parse_float=_exact_float)
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
            # The parse already refuses NaN, Infinity and a number too large for a float, so no such value
            # reaches this point; allow_nan=False stays as a second guard.
            line = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            return (line + "\n").encode("utf-8")
        except (ValueError, RecursionError, UnicodeEncodeError) as exc:
            raise Refusal("the record cannot be written as UTF-8 JSON ({})".format(str(exc)[:120]))
    render(PLACEHOLDER)  # surface an unwritable record (a lone surrogate) before the file is opened
    return render


# --- steps 2 to 4: the file -----------------------------------------------------------------------

OPEN_FLAGS = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _open(path, create):
    """Open the file; return (fd, created), created being True when this run made the file."""
    try:
        if create:
            try:
                # O_EXCL tells a file this run makes from one that was there, so a refusal removes only its own.
                return os.open(path, OPEN_FLAGS | os.O_CREAT | os.O_EXCL, 0o666), True
            except FileNotFoundError:
                raise Refusal("the directory that would hold {} does not exist; --create makes the file, "
                              "not its directory".format(path))
            except FileExistsError:
                pass
        return os.open(path, OPEN_FLAGS), False
    except FileNotFoundError:
        if create:
            raise Refusal("{} was removed while it was being opened".format(path))
        raise Refusal("{} does not exist (pass --create to create it)".format(path))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refusal("{} is a symbolic link".format(path))
        raise Refusal("cannot open {}: {}".format(path, exc.strerror or exc))


def _check_regular(fd, path):
    try:
        mode = os.fstat(fd).st_mode
    except OSError as exc:
        raise Refusal("cannot stat {}: {}".format(path, exc.strerror or exc))
    if not stat.S_ISREG(mode):
        raise Refusal("{} is not a regular file".format(path))


def _discard_created(fd, path):
    """Remove the empty file this run made; return None, or why it was left in place."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return "another writer holds its lock"
    try:
        here = os.fstat(fd)
        there = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        return "it cannot be checked: {}".format(exc.strerror or exc)
    if (here.st_dev, here.st_ino) != (there.st_dev, there.st_ino):
        return "the path no longer names it"
    if here.st_size != 0:
        return "it is no longer empty"
    try:
        SEAMS["unlink"](path)
    except OSError as exc:
        return "it cannot be removed: {}".format(exc.strerror or exc)
    return None


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
            raise Refusal("the write failed ({}) and the file could not be truncated back ({}); the record "
                          "or part of it may start at offset {}".format(reason, exc2.strerror or exc2, size),
                          changed=True)
        raise Refusal("the write failed ({}); the file was truncated back to its original size {}".format(
            reason, size))


def _append_open(fd, opts, render):
    """Steps 3 to 6 on the open file. Returns (stamp, warning)."""
    _check_regular(fd, opts["file"])
    _lock(fd)
    try:
        size = _check_end(fd)
        last = _last_stamp(fd, size, opts["format"], opts["field"])
    except OSError as exc:
        raise Refusal("cannot read {}: {}".format(opts["file"], exc.strerror or exc))
    stamp, failure = _read_clock(last)
    warning = ""
    if failure is not None:
        if opts["on_clock_failure"] != "unknown":
            raise Refusal(failure)
        stamp = UNKNOWN_MARK
        warning = "stamp-append: warning: {}; the time was recorded as unknown\n".format(_one_line(failure))
    _write_all(fd, render(stamp), size)
    return stamp, warning


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
        fd, created = _open(opts["file"], opts["create"])
        try:
            try:
                stamp, warning = _append_open(fd, opts, render)
            except OSError as exc:
                # _write_all handles every failure from the write on, so this one came before any byte.
                raise Refusal(exc.strerror or str(exc))
        except Refusal as exc:
            if created and not exc.changed:
                exc.left = _discard_created(fd, opts["file"])
            try:
                SEAMS["close"](fd)
            except OSError:
                pass  # the refusal already says what the file holds; a failed close does not change it
            raise
        try:
            SEAMS["close"](fd)
        except OSError as exc:
            raise Refusal("the record was written and synced, but closing the file failed ({})".format(
                exc.strerror or exc), changed=True)
        return 0, stamp + "\n", warning
    except Refusal as exc:
        return 2, "", _refusal_line(exc)
    except OSError as exc:
        # Not expected: every known failure is a Refusal above. Claim nothing about the file.
        return 2, "", _refusal_line(Refusal(exc.strerror or str(exc), changed=True))


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
    _expect(err.count("\n") == 1 and err.endswith("; nothing was written\n"),
            "the refusal is not one line ending 'nothing was written': {!r}".format(err))
    _expect(_read(path) == before, "the file changed on a refusal")


def _kept_bytes(code, err, path, want, needle):
    """A refusal after which bytes of the record may remain: it must say so, never 'nothing was written'."""
    _expect(code == 2, "expected exit 2, got {} ({})".format(code, err.strip()))
    _expect(needle in err, "the refusal lacks {!r}: {}".format(needle, err.strip()))
    _expect("nothing was written" not in err, "the refusal says nothing was written: {}".format(err.strip()))
    _expect(err.count("\n") == 1 and err.endswith("; the file may hold part or all of the record, so check "
                                                  "it before retrying\n"),
            "the refusal does not say the file may hold the record: {!r}".format(err))
    _expect(_read(path) == want, "the file holds {!r}, not {!r}".format(_read(path), want))


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
        # The lock wait reads a fixed monotonic time, so the 5-second bound never ends it however slowly
        # the threads run; only the 30-second hang guards below read the live clock.
        with _Seams(clock=lambda: reading["now"], monotonic=_fixed(0.0), sleep=sleep):
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
    # Kept: the floats on each side of 9007199254740993.0, the largest finite float and the smallest
    # subnormal; refused below: the value between them, a value past the largest and one under the smallest.
    numbers = (b'{"a":1e5,"b":1.10,"c":9007199254740993,"d":-0.0,"e":0.1,"f":9007199254740992.0,'
               b'"g":9007199254740994.0,"h":1.7976931348623157e308,"i":5e-324}')
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path, "--format", "jsonl"], numbers)
    expected += (b'{"ts":"2026-11-01T12:00:00Z","a":100000.0,"b":1.1,"c":9007199254740993,"d":-0.0,"e":0.1,'
                 b'"f":9007199254740992.0,"g":9007199254740994.0,"h":1.7976931348623157e+308,"i":5e-324}\n')
    _expect(code == 0 and _read(path) == expected, "numbers: exit {} ({}), wrote {!r}".format(
        code, err.strip(), _read(path)))
    # The decimal module's exponent limits: a zero with an exponent up to MAX_EMAX is kept as 0.0, and
    # 1 times ten to the power MIN_ETINY parses but is not the float 0.0 it would become. One past either
    # limit makes decimal raise; that and the far larger exponents are refused, never a traceback.
    emax, etiny = decimal.MAX_EMAX, decimal.MIN_ETINY
    zeros = '{{"a":0e{},"b":0e{}}}'.format(emax - 1, emax).encode()
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path, "--format", "jsonl"], zeros)
    expected += b'{"ts":"2026-11-01T12:00:00Z","a":0.0,"b":0.0}\n'
    _expect(code == 0 and _read(path) == expected, "zeros at the exponent limit: exit {} ({}), wrote {!r}".format(
        code, err.strip(), _read(path)))
    limits = (('{{"n":1e{}}}'.format(etiny + 1).encode(), "a different value"),
              ('{{"n":1e{}}}'.format(etiny).encode(), "a different value"),
              ('{{"n":1e{}}}'.format(etiny - 1).encode(), "exact value cannot be checked"),
              (b'{"n":1e-999999999999999999999}', "exact value cannot be checked (InvalidOperation)"),
              ('{{"n":0e{}}}'.format(emax + 1).encode(), "exact value cannot be checked"),
              (b'{"n":0e999999999999999999999}', "exact value cannot be checked (InvalidOperation)"),
              (b'{"a":[{"n":-0E+999999999999999999999}]}', "exact value cannot be checked"))
    bad = ((b'{"ts":"2026-11-01T11:59:00Z"}', "supplies"),
           (b'{"n":1e400}', "too large for a float"),
           (b'{"n":-1e400}', "too large for a float"),
           (b'{"n":[{"m":1E+999}]}', "too large for a float"),
           (b'{"n":9007199254740993.0}', "a different value"),
           (b'{"n":1.00000000000000000001}', "a different value"),
           (b'{"n":1e-400}', "a different value"),
           (b'{"n":1.7976931348623158e308}', "a different value"),
           (b'{"n":1.7976931348623159e308}', "too large for a float"),
           (b'{"n":2e-324}', "a different value"),
           (b'{"a":1,"a":2}', "repeats the key"),
           (b'{"a":{"b":1,"b":2}}', "repeats the key"),
           (b'{"a":NaN}', "NaN"),
           (b'{"a":-Infinity}', "Infinity"),
           (b'{"note":"@@NOW_UTC@@"}', "outside"),
           (b'{"x":["@@NOW_UTC@@"]}', "outside"),
           (b'[1,2]', "not a JSON object"),
           (b'{"a":1}{"b":2}', "not one JSON object"))
    for record, needle in limits + bad:
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


def t16(work):
    # A run that --create made the file for, then refused, removes the file it made when cleanup can proceed
    # safely; when it cannot, the refusal line says the file was left (below).
    for label, clock in (("unset clock", _fixed(0)), ("raising clock", _raise)):
        path = os.path.join(work, "t16-" + label.replace(" ", "-"))
        with _Seams(clock=clock):
            code, _, err = append(["--file", path, "--create"], b"## @@NOW_UTC@@ x\n")
        _expect(code == 2 and err.count("\n") == 1 and err.endswith("; nothing was written\n"),
                "{}: exit {} ({!r})".format(label, code, err))
        _expect(not os.path.lexists(path), "{}: the file --create made was left after a refusal".format(label))
    # A file that was there before keeps its bytes on a refusal, even with --create.
    path = _store(work, "t16-existing", b"")
    with _Seams(clock=_fixed(0)):
        code, _, err = append(["--file", path, "--create"], b"## @@NOW_UTC@@ x\n")
    _refused(code, err, b"", path, "unset clock")
    # When the file cannot be removed, the refusal says it was left in place.
    path = os.path.join(work, "t16-kept")

    def no_unlink(_path):
        raise OSError(errno.EACCES, "seeded")

    with _Seams(clock=_fixed(0), unlink=no_unlink):
        code, _, err = append(["--file", path, "--create"], b"## @@NOW_UTC@@ x\n")
    _expect(code == 2 and err.count("\n") == 1 and "nothing was written" not in err
            and "the empty file --create made was left in place (it cannot be removed: seeded)" in err,
            "an unremovable created file: exit {} ({!r})".format(code, err))
    _expect(_read(path) == b"", "the file left in place is not empty")
    # A missing directory gets a hint that fits --create.
    path = os.path.join(work, "no-such-dir", "log")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path, "--create"], b"## @@NOW_UTC@@ x\n")
    _expect(code == 2 and "pass --create" not in err and "directory that would hold" in err
            and err.endswith("; nothing was written\n"), "a missing directory: exit {} ({!r})".format(code, err))


def t17(work):
    # The depths at which a recursive placeholder scan crashed: refused or written, never a traceback.
    path = _store(work, "t17", b"")
    expected = b""
    for depth, record in ((498, "[" * 498 + "]" * 498), (497, '{"k":' * 497 + "1" + "}" * 497)):
        data = '{"d":' + record + "}"
        with _Seams(clock=_fixed(T0)):
            code, _, err = append(["--file", path, "--format", "jsonl"], data.encode())
        _expect(code == 0, "depth {}: expected exit 0, got {} ({})".format(depth, code, err.strip()[:200]))
        expected += b'{"ts":"2026-11-01T12:00:00Z",' + data[1:].encode() + b"\n"
        _expect(_read(path) == expected, "depth {}: wrote the wrong bytes".format(depth))
    deep = '{"d":' + "[" * 200000 + "]" * 200000 + "}"
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path, "--format", "jsonl"], deep.encode())
    _refused(code, err, expected, path, "not one JSON object")


def t18(work):
    # --self-test with another argument refuses with one stderr line and runs no test.
    saved = sys.argv, sys.stderr, sys.stdout
    err, out = io.StringIO(), io.StringIO()
    sys.argv, sys.stderr, sys.stdout = ["stamp-append.py", "--self-test", "--file", "/x"], err, out
    try:
        code = main()
    finally:
        sys.argv, sys.stderr, sys.stdout = saved
    _expect(code == 2 and out.getvalue() == "", "exit {}, stdout {!r}".format(code, out.getvalue()))
    _expect(err.getvalue() == "stamp-append: refused: --self-test takes no other argument; nothing was written\n",
            "stderr {!r} is not one refusal line".format(err.getvalue()))


def t19(work):
    # A path or message holding LF, CR or another control character still gives one refusal line.
    def one_line(code, err, needle, label):
        _expect(code == 2, "{}: expected exit 2, got {} ({!r})".format(label, code, err))
        _expect(needle in err, "{}: the refusal lacks {!r}: {!r}".format(label, needle, err))
        _expect(err.endswith("\n") and len(err.splitlines()) == 1 and not _CONTROL_RE.search(err[:-1]),
                "{}: the refusal is not one line: {!r}".format(label, err))

    path = os.path.join(work, "t19-a\nb")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
    one_line(code, err, "t19-a\\nb does not exist", "an LF in the file name")
    _expect(err.endswith("; nothing was written\n"), "an LF in the file name: {!r}".format(err))
    _expect(not os.path.lexists(path), "an LF in the file name: a file was made")
    path = os.path.join(work, "t19-c\rd\x1be\N{LINE SEPARATOR}f\x85g")
    with _Seams(clock=_fixed(T0)):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ x\n")
    one_line(code, err, "t19-c\\rd\\x1be\\u2028f\\x85g", "CR and other controls in the file name")
    # The reason a file --create made was left is escaped as well.
    path = os.path.join(work, "t19-left\nname")

    def no_unlink(_path):
        raise OSError(errno.EACCES, "seeded\r\nsecond line")

    with _Seams(clock=_fixed(0), unlink=no_unlink):
        code, _, err = append(["--file", path, "--create"], b"## @@NOW_UTC@@ x\n")
    one_line(code, err, "(it cannot be removed: seeded\\r\\nsecond line)", "an LF in the reason a file was left")
    _expect(_read(path) == b"", "the file left in place is not empty")
    every = "".join(chr(c) for c in range(0x20)) + "\x7f\x80\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"
    err = _refusal_line(Refusal(every))
    one_line(2, err, "\\x00", "every control character")


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
    _kept_bytes(code, err, path, b"# log\n## 2026-11-01T12:00:00Z a longer rec", "offset 6")

    def no_fsync(_fd):
        raise OSError(errno.EIO, "seeded fsync")

    path = _store(work, "t15-whole")
    with _Seams(clock=_fixed(T0), fsync=no_fsync, ftruncate=no_truncate):
        code, _, err = append(["--file", path], b"## @@NOW_UTC@@ whole record\n")
    _kept_bytes(code, err, path, b"# log\n## 2026-11-01T12:00:00Z whole record\n", "offset 6")

    def bad_close(fd):
        os.close(fd)
        raise OSError(errno.EIO, "seeded close")

    path = _store(work, "t15-close")
    with _Seams(clock=_fixed(T0), close=bad_close):
        code, out, err = append(["--file", path], b"## @@NOW_UTC@@ closed\n")
    _kept_bytes(code, err, path, b"# log\n## 2026-11-01T12:00:00Z closed\n", "closing the file failed")
    _expect(out == "", "stdout {!r} on a refusal".format(out))


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
         ("T15", "a failed write is truncated back, or the refusal says bytes may remain", t15),
         ("T16", "a refusal removes the file --create made when it safely can, else says it was left, "
                 "and a missing directory is named", t16),
         ("T17", "a deeply nested JSONL record is written or refused, never a traceback", t17),
         ("T18", "--self-test with another argument refuses on one stderr line", t18),
         ("T19", "a control character in a path or message is escaped, so a refusal stays one line", t19))


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
        sys.stderr.write("stamp-append: refused: --self-test takes no other argument; nothing was written\n")
        return 2
    if not argv:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        data = sys.stdin.buffer.read(MAX_RECORD + 1)
    except (OSError, AttributeError, ValueError) as exc:
        sys.stderr.write("stamp-append: refused: cannot read the record from stdin ({}); nothing was "
                         "written\n".format(_one_line(exc)))
        return 2
    code, out, err = append(argv, data)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
