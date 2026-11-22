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
      a broken pipe or a closed descriptor 1, after which DIR exists but no id was delivered, so DIR
      is never used).
  orch_dispatch_marker.py write --marker PATH --id HEX --status N
      Write the line `aiqt-dispatch-marker v1 <id> <status>` to a temporary file in PATH's directory,
      fsync it, then rename it onto PATH, so PATH keeps its previous complete content until the
      rename. N is an integer from 0 to 255 with no sign or leading zero. The publish is atomic but
      not durable: the directory is not fsynced after the rename, so after a host crash PATH may
      hold its previous complete content again.
      Exit 0 published; 2 cannot evaluate (bad arguments, an oversized or unconvertible number
      included, a missing directory, a target that is a directory, a failure to create the temporary
      file, or a write, fsync or rename failure, after which the temporary file this process created
      is removed and PATH is left as it was; an entry it did not create is never removed). Any other
      exception that escapes the publish (an interrupt included) also removes that temporary file
      and is then raised again.
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
      error, a failed write of a matched status to stdout included, such as a broken pipe or a
      closed descriptor 1). Marker
      content that was read successfully and is at most MAX_MARKER_BYTES bytes is never alone a
      cause of exit 2, whatever it holds; a file system error still exits 2 at once, whatever the
      marker's size. The marker is opened without following a symlink and without blocking on a
      FIFO, and its type and size are checked again on the open descriptor, so a marker replaced by a
      symlink, FIFO or oversized file between the lstat and the open still exits 2; one removed in
      that gap is still pending.
  Any other command line, an empty one included, is a usage error: exit 2 (cannot evaluate). The
  self-test is its own runner, tools/selftest_orch_dispatch_marker.py (suite dispatch-marker-selftest).

Every command is refused at exit 2 (cannot evaluate), before it runs, when this process cannot read a
child's exit status (SIGCHLD ignored or not at its default, or a probe child's status lost): the inline
child-status guard at the entry line, a copy of opf/tools/_child_status.py, names the refusal on stderr.

Output: only the id or the matched status is ever written to stdout; every diagnostic goes to stderr
alone. A stderr that is unavailable or fails (descriptor 2 closed before or after Python starts, a full
device, a pipe with no reader) loses those diagnostics, but never changes the exit codes above, the
interpreter's final flush of either stream included, and they are never written to stdout instead.

The dispatcher is meant to use both bindings: claim a fresh directory for each dispatch attempt
(retries included), pass the id it prints to the producer, and always wait with that id. Each covers a
failure the other misses: a waiter handed a stale path defeats the directory alone, and an id alone
leaves payload files from an earlier dispatch beside the marker. Timestamps play no part, and deleting
an earlier result first is not a binding.

Launch it as `python3 -I -B tools/orch_dispatch_marker.py`. It uses the standard library only and
starts no process other than the guard's probe child at entry (a fork that exits at once with a known
status); its self-test runner launches this file under the same interpreter to observe real process
exits. An interpreter older than Python 3.14 that can start this file is refused at exit 2
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
    created = published = False
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
        published = True
    except BaseException as exc:
        # Only a temporary file this process created, and has not yet renamed, is removed; an entry
        # already at that name (another writer's) is left alone. Anything other than an operating-system
        # error (an interrupt, for one) is raised again once the temporary file is gone.
        if created and not published:
            try:
                os.unlink(temp)
            except OSError:
                pass
        if not isinstance(exc, OSError):
            raise
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


def main(argv=None, stdout=None, stderr=None):
    if argv is None:
        # The process entry: the command line, ended through _exit_settled so that the interpreter's
        # final flush of either stream cannot replace the exit code.
        _exit_settled(main(sys.argv[1:]))
    code, out, err = run_command(argv)
    return _deliver(code, out, err, sys.stdout if stdout is None else stdout,
                    sys.stderr if stderr is None else stderr)


def _discard_unwritten(stream):
    """Point a stream whose write failed at the null device, so the interpreter's final flush of the
    data still buffered cannot fail again and replace this exit code with Python's own 120. When the
    stream's descriptor was closed, the open reuses that very number; it is then the stream's new
    target and is kept open, since closing it would leave the final flush to fail after all."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        if null != fd:
            os.dup2(null, fd)
    except OSError:
        pass
    finally:
        if null != fd:
            os.close(null)


def _deliver(code, out, err, stdout, stderr):
    """The exit code after writing out to stdout and err to stderr. A stdout write or flush that fails
    (a broken pipe or any other operating-system error) is exit 2: the id or status never reached the
    caller, so the command cannot be relied on, whatever its own exit would have been. So is a stdout
    that is unavailable (None, as Python leaves sys.stdout when descriptor 1 is closed at start). The
    diagnostics go to stderr only, and a stderr that fails never changes the exit code (_diagnose)."""
    if out and stdout is None:
        code = EXIT_CANNOT_EVALUATE
        err = list(err) + ["cannot evaluate: the result could not be written to stdout (stdout is "
                           "unavailable, as when descriptor 1 is closed before Python starts)"]
    elif out:
        try:
            stdout.write(out)
            stdout.flush()
        except Exception as exc:
            _discard_unwritten(stdout)
            code = EXIT_CANNOT_EVALUATE
            err = list(err) + ["cannot evaluate: the result could not be written to stdout ({}: {})".format(
                type(exc).__name__, exc)]
    _diagnose(err, stderr)
    return code


def _diagnose(lines, stderr):
    """Write the diagnostic lines to stderr and nowhere else. None (as Python leaves sys.stderr when
    descriptor 2 is closed at start) drops them; print would send them to stdout. A write or flush that
    fails (a closed descriptor, a full device, a pipe with no reader) drops the rest and points the
    stream at the null device, so neither the failure nor the interpreter's final flush can change the
    exit code."""
    if not lines or stderr is None:
        return
    try:
        for line in lines:
            stderr.write(line + "\n")
        stderr.flush()
    except Exception:
        _discard_unwritten(stderr)


def _flushed(stream):
    """True when the stream is unavailable or holds nothing that a later flush could fail on."""
    if stream is None:
        return True
    try:
        stream.flush()
    except Exception:
        return False
    return True


def _exit_settled(code):
    """Exit with code, which the interpreter's final flush of stdout or stderr must not replace with its
    own 120. Each stream is flushed here first; one that still fails is pointed at the null device and
    flushed again, and if one fails even then the process ends at once with code, skipping that flush."""
    settled = True
    for stream in (sys.stdout, sys.stderr):
        if not _flushed(stream):
            _discard_unwritten(stream)
            settled = _flushed(stream) and settled
    if not settled:
        os._exit(code)
    sys.exit(code)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The helper starts no child of its own; the
# guard runs at the entry line, before main() dispatches, so a launch from a parent under which a child's
# exit status cannot be read is refused by name at exit 2, as every other self-guarded entry point is, and
# its self-test runner (tools/selftest_orch_dispatch_marker.py), which reads the exit statuses of real
# launches of this file, carries the same copy.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("orch_dispatch_marker.py") or main())
