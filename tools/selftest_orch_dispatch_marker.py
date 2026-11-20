#!/usr/bin/env python3
"""dispatch-marker-selftest: the self-test of tools/orch_dispatch_marker.py (expbnd.6), and the suite's
registered runner in tools/selftest_checks.toml.

  selftest_orch_dispatch_marker.py                              exit 0 on pass, 1 on a failed check
  selftest_orch_dispatch_marker.py --execution-report ABS_PATH  also write the executed check IDs
                                                                (tools/_selftest_exit_report.py)

Exit 2 is cannot-evaluate: a harness error, any other argument (refused before anything runs), or the
child-status refusal at the entry line; never a pass. A bare launch runs the whole self-test, so the
git-fixture-env config lanes, which launch every registered suite's runner bare under their poisoned
configuration, run it as it is; it starts no git.

The helper is imported from beside this file as a module. Its call-time seams (_to_int, _to_float,
_open_fd, _open_marker, _write_fd, _fsync_fd, _replace_path, _temp_suffix_hex and _id_matches) are swapped
on that module, so the helper's own functions see each swap exactly as they would in one file. The
process cases launch tools/orch_dispatch_marker.py itself under the same interpreter and read each
launch's exit status, which is why this runner carries the inline child-status guard at its entry line.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_orch_dispatch_marker.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import stat
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import orch_dispatch_marker as _odm  # noqa: E402
from orch_dispatch_marker import (  # noqa: E402
    DEADLINE_MAX_S, DEADLINE_TEXT_MAX, EXIT_CANNOT_EVALUATE, EXIT_EXISTS, EXIT_NEVER_SEEN, EXIT_OK,
    EXIT_ONLY_UNBOUND, ID_LENGTH, INTERVAL_TEXT_MAX, MARKER_MAGIC, MARKER_VERSION, MAX_MARKER_BYTES,
    STATUS_TEXT_MAX, claim_dir, is_dispatch_id, marker_line, run_command, wait_marker, write_marker)


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
        real_open = vars(_odm)["_open_marker"]

        def denied(path):
            raise PermissionError(13, "read refused by the self-test", path)

        vars(_odm)["_open_marker"] = denied
        try:
            code, out, err, _f = _wait(m, new_id)
        finally:
            vars(_odm)["_open_marker"] = real_open
        check("wait/permission-error-2", code == EXIT_CANNOT_EVALUATE
              and any("cannot be opened" in line for line in err), (code, out, err))
        code, out, err, _f = _wait(m, new_id)
        check("wait/permission-control-0", code == EXIT_OK and out == "0\n", (code, out, err))

        # The open itself. The lstat before it is only a first look, so each case below changes the marker
        # inside the open call, after the lstat saw a small regular file, and the defences on the open and
        # on the open descriptor must hold alone.
        real_open_fd = vars(_odm)["_open_fd"]

        def wait_hooked(path, hook, on_sleep=None):
            def hooked(p, flags, *args):
                hook(p, flags)
                return real_open_fd(p, flags, *args)

            vars(_odm)["_open_fd"] = hooked
            try:
                return _wait(path, new_id, on_sleep=on_sleep)
            finally:
                vars(_odm)["_open_fd"] = real_open_fd

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
        real_int, real_float = vars(_odm)["_to_int"], vars(_odm)["_to_float"]
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
            vars(_odm)["_to_int"], vars(_odm)["_to_float"] = recording_int, recording_float
            try:
                code, out, err = _run_guarded(argv, fake.clock, fake.sleep)
            finally:
                vars(_odm)["_to_int"], vars(_odm)["_to_float"] = real_int, real_float
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
        real_write = vars(_odm)["_write_fd"]

        def half_then_look(fd, data):
            if paused:
                return real_write(fd, data)
            written = real_write(fd, bytes(data[:len(data) // 2]))
            others = sorted(name for name in os.listdir(d) if name != "exit-status")
            paused.append((_read_or_none(m), [_read_or_none(os.path.join(d, name)) for name in others]))
            return written

        vars(_odm)["_write_fd"] = half_then_look
        try:
            code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
        finally:
            vars(_odm)["_write_fd"] = real_write
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

        real_fsync, real_replace = vars(_odm)["_fsync_fd"], vars(_odm)["_replace_path"]
        for name, fsync_fn, replace_fn in (("write/fsync-failure-2", fail_fsync, real_replace),
                                           ("write/rename-failure-2", real_fsync, fail_rename)):
            d, m = fresh()
            _publish(m, old_id, 0)
            vars(_odm)["_fsync_fd"], vars(_odm)["_replace_path"] = fsync_fn, replace_fn
            try:
                code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
            finally:
                vars(_odm)["_fsync_fd"], vars(_odm)["_replace_path"] = real_fsync, real_replace
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
        real_suffix = vars(_odm)["_temp_suffix_hex"]
        for name, foreign, fsync_fn in (("write/temp-name-taken-left-alone-2", True, real_fsync),
                                        ("write/temp-name-own-removed-control-2", False, fail_fsync)):
            d, m = fresh()
            _publish(m, old_id, 0)
            temp_name = ".exit-status.tmp-" + fixed_suffix
            foreign_data = b"another writer's temporary file\n"
            if foreign:
                _put(os.path.join(d, temp_name), foreign_data)
            vars(_odm)["_temp_suffix_hex"], vars(_odm)["_fsync_fd"] = (lambda nbytes: fixed_suffix), fsync_fn
            try:
                code, out, err = _run_guarded(["write", "--marker", m, "--id", new_id, "--status", "0"])
            finally:
                vars(_odm)["_temp_suffix_hex"], vars(_odm)["_fsync_fd"] = real_suffix, real_fsync
            left = sorted(os.listdir(d))
            check(name, code == EXIT_CANNOT_EVALUATE and _read_or_none(m) == previous
                  and left == (sorted(["exit-status", temp_name]) if foreign else ["exit-status"])
                  and (_read_or_none(os.path.join(d, temp_name)) == foreign_data if foreign else True)
                  and any("cannot publish" in line for line in err), (code, err, left))

        # Any other exception that escapes the publish (here an interrupt half way through the temporary
        # write) removes the temporary file and is raised again, and the previous marker stays.
        d, m = fresh()
        _publish(m, old_id, 0)

        def half_then_interrupt(fd, data):
            real_write(fd, bytes(data[:len(data) // 2]))
            raise KeyboardInterrupt

        vars(_odm)["_write_fd"] = half_then_interrupt
        try:
            try:
                write_marker(m, new_id, 0)
                outcome = "returned"
            except KeyboardInterrupt:
                outcome = "raised again"
        finally:
            vars(_odm)["_write_fd"] = real_write
        check("write/interrupt-temp-removed-raised-again", outcome == "raised again"
              and _read_or_none(m) == previous and sorted(os.listdir(d)) == ["exit-status"],
              (outcome, _read_or_none(m), sorted(os.listdir(d))))

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
                code = _odm.main(argv, stdout, stderr)
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

        # --- output, as real processes: descriptor 1 closed before Python starts (sys.stdout is None) or
        # after it starts (the null device's open reuses descriptor 1) is still exit 2, measured as the
        # process's own exit status, so the interpreter's shutdown flush is included. Each control runs
        # the same launcher with descriptor 1 left open. ---
        import subprocess
        script = os.path.abspath(_odm.__file__)
        # Each launcher takes a mode (keep, close, full: the descriptor on /dev/full, pipe: on a pipe whose
        # reader is closed, or unflushable: sys.stderr replaced by an object with no descriptor whose
        # write and flush always fail, so it cannot be pointed at the null device) and a comma-separated
        # list of descriptors to apply it to.
        redirect = ("class Unflushable:\n"
                    "    def write(self, text):\n"
                    "        raise OSError(5, 'stderr failure injected by the self-test')\n"
                    "    def flush(self):\n"
                    "        raise OSError(5, 'stderr failure injected by the self-test')\n"
                    "mode, fds = sys.argv[1], [int(fd) for fd in sys.argv[2].split(',')]\n"
                    "for fd in fds:\n"
                    "    if mode == 'unflushable':\n"
                    "        sys.stderr = Unflushable()\n"
                    "    elif mode == 'close':\n"
                    "        os.close(fd)\n"
                    "    elif mode == 'full':\n"
                    "        os.dup2(os.open('/dev/full', os.O_WRONLY), fd)\n"
                    "    elif mode == 'pipe':\n"
                    "        read_end, write_end = os.pipe()\n"
                    "        os.close(read_end)\n"
                    "        os.dup2(write_end, fd)\n")
        launchers = {
            "at-start": ("import os, sys\n" + redirect +
                         "os.execv(sys.executable, [sys.executable, '-I', '-B'] + sys.argv[3:])\n"),
            "after-start": ("import os, runpy, sys\n" + redirect +
                            "sys.argv = sys.argv[3:]\n"
                            "runpy.run_path(sys.argv[0], run_name='__main__')\n"),
        }

        def run_process(when, close, argv, mode=None, fds="1"):
            try:
                proc = subprocess.run([sys.executable, "-I", "-B", "-c", launchers[when],
                                       mode or ("close" if close else "keep"), fds, script] + argv,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, cwd=base, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                return "raised", "", "{}: {}".format(type(exc).__name__, exc)
            return (proc.returncode, proc.stdout.decode("ascii", "replace"),
                    proc.stderr.decode("utf-8", "replace"))

        def refused_cleanly(code, out, err):
            return code == EXIT_CANNOT_EVALUATE and out == "" and "Traceback" not in err \
                and "could not be written to stdout" in err

        for when, claim_closed, wait_closed, claim_open, wait_open in (
                ("at-start", "output/process-claim-stdout-closed-at-start-2",
                 "output/process-wait-stdout-closed-at-start-2",
                 "output/process-claim-stdout-open-at-start-control-0",
                 "output/process-wait-stdout-open-at-start-control-0"),
                ("after-start", "output/process-claim-stdout-closed-after-start-2",
                 "output/process-wait-stdout-closed-after-start-2",
                 "output/process-claim-stdout-open-after-start-control-0",
                 "output/process-wait-stdout-open-after-start-control-0")):
            claim_path = os.path.join(base, "output-claim-closed-" + when)
            code, out, err = run_process(when, True, ["claim", claim_path])
            check(claim_closed, refused_cleanly(code, out, err) and os.path.isdir(claim_path),
                  (code, out, err[-400:]))
            code, out, err = run_process(when, True, wait_argv)
            check(wait_closed, refused_cleanly(code, out, err), (code, out, err[-400:]))
            claim_path = os.path.join(base, "output-claim-open-" + when)
            code, out, err = run_process(when, False, ["claim", claim_path])
            check(claim_open, code == EXIT_OK and err == "" and out.endswith("\n") and is_dispatch_id(out[:-1])
                  and os.path.isdir(claim_path), (code, out, err[-400:]))
            code, out, err = run_process(when, False, wait_argv)
            check(wait_open, code == EXIT_OK and err == "" and out == "9\n", (code, out, err[-400:]))

        # --- output, as real processes: a stderr that is closed before or after Python starts, on a full
        # device, on a pipe with no reader or that cannot be flushed at all keeps every exit code of the
        # table (never Python's own 120 from the final flush) and never moves a diagnostic to stdout. Each
        # control runs the same command with stderr open and finds its diagnostic there. ---
        d, m = fresh()
        _publish(m, old_id, 0)
        _d, never_marker = fresh()
        existing = os.path.join(base, "output-stderr-existing")
        os.mkdir(existing)
        stderr_commands = {
            "claim-existing": (["claim", existing], EXIT_EXISTS, "refused: something already exists"),
            "claim-missing-parent": (["claim", os.path.join(base, "output-stderr-no-parent", "dispatch")],
                                     EXIT_CANNOT_EVALUATE, "cannot evaluate: cannot create"),
            "wait-unbound": (["wait", "--marker", m, "--expect-id", new_id, "--deadline-s", "1",
                              "--interval-s", "0.05"], EXIT_ONLY_UNBOUND, "unbound marker at"),
            "wait-never-seen": (["wait", "--marker", never_marker, "--expect-id", new_id, "--deadline-s", "1",
                                 "--interval-s", "0.05"], EXIT_NEVER_SEEN, "no marker appeared at"),
        }
        for name, when, mode, command in (
                ("output/process-claim-existing-stderr-closed-at-start-5", "at-start", "close", "claim-existing"),
                ("output/process-claim-missing-parent-stderr-closed-at-start-2", "at-start", "close",
                 "claim-missing-parent"),
                ("output/process-wait-unbound-stderr-closed-at-start-4", "at-start", "close", "wait-unbound"),
                ("output/process-wait-never-seen-stderr-closed-at-start-3", "at-start", "close",
                 "wait-never-seen"),
                ("output/process-claim-existing-stderr-closed-after-start-5", "after-start", "close",
                 "claim-existing"),
                ("output/process-claim-missing-parent-stderr-closed-after-start-2", "after-start", "close",
                 "claim-missing-parent"),
                ("output/process-wait-unbound-stderr-closed-after-start-4", "after-start", "close",
                 "wait-unbound"),
                ("output/process-wait-never-seen-stderr-closed-after-start-3", "after-start", "close",
                 "wait-never-seen"),
                ("output/process-claim-existing-stderr-on-dev-full-5", "at-start", "full", "claim-existing"),
                ("output/process-claim-missing-parent-stderr-on-dev-full-2", "at-start", "full",
                 "claim-missing-parent"),
                ("output/process-wait-unbound-stderr-on-dev-full-4", "at-start", "full", "wait-unbound"),
                ("output/process-wait-never-seen-stderr-on-dev-full-3", "at-start", "full", "wait-never-seen"),
                ("output/process-claim-existing-stderr-on-pipe-without-reader-5", "at-start", "pipe",
                 "claim-existing"),
                ("output/process-claim-missing-parent-stderr-on-pipe-without-reader-2", "at-start", "pipe",
                 "claim-missing-parent"),
                ("output/process-wait-unbound-stderr-on-pipe-without-reader-4", "at-start", "pipe",
                 "wait-unbound"),
                ("output/process-wait-never-seen-stderr-on-pipe-without-reader-3", "at-start", "pipe",
                 "wait-never-seen"),
                ("output/process-claim-existing-stderr-unflushable-after-start-5", "after-start", "unflushable",
                 "claim-existing"),
                ("output/process-claim-missing-parent-stderr-unflushable-after-start-2", "after-start",
                 "unflushable", "claim-missing-parent"),
                ("output/process-wait-unbound-stderr-unflushable-after-start-4", "after-start", "unflushable",
                 "wait-unbound"),
                ("output/process-wait-never-seen-stderr-unflushable-after-start-3", "after-start",
                 "unflushable", "wait-never-seen")):
            argv, want, _needle = stderr_commands[command]
            code, out, err = run_process(when, None, argv, mode, "2")
            check(name, code == want and out == "" and err == "", (code, out, err[-400:]))
        for name, command in (("output/process-claim-existing-stderr-open-control-5", "claim-existing"),
                              ("output/process-claim-missing-parent-stderr-open-control-2", "claim-missing-parent"),
                              ("output/process-wait-unbound-stderr-open-control-4", "wait-unbound"),
                              ("output/process-wait-never-seen-stderr-open-control-3", "wait-never-seen")):
            argv, want, needle = stderr_commands[command]
            code, out, err = run_process("at-start", None, argv, "keep", "2")
            check(name, code == want and out == "" and needle in err and "Traceback" not in err,
                  (code, out, err[-400:]))
        claim_path = os.path.join(base, "output-claim-stdout-stderr-closed")
        code, out, err = run_process("after-start", None, ["claim", claim_path], "close", "1,2")
        check("output/process-claim-stdout-and-stderr-closed-after-start-2", code == EXIT_CANNOT_EVALUATE
              and out == "" and err == "" and os.path.isdir(claim_path), (code, out, err[-400:]))

        # --- revert: with the id comparison removed, the incident case would pass as a match ---
        d, m = fresh()
        _publish(m,old_id, 0)
        real_matches = vars(_odm)["_id_matches"]
        vars(_odm)["_id_matches"] = lambda got, want: True
        try:
            reverted, _o, _e, _f = _wait(m, new_id)
        finally:
            vars(_odm)["_id_matches"] = real_matches
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
          "did not create alone, removes its temporary file when interrupted and refuses a bad status; a "
          "failed stdout write is exit 2, as is a descriptor 1 closed before or after Python starts, "
          "measured as the process's exit; a stderr closed before or after start, full, without a reader "
          "or unflushable keeps exits 2, 3, 4 and 5 and puts nothing on stdout; with the id "
          "comparison removed the incident case would pass)".format(
              len(EXECUTED)))
    return 0


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH`; accepted only as that whole two-token
    # argv, with an absolute path. A bare launch is the self-test itself.
    report_path = None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        report_path, argv = argv[1], []
    if argv:
        print("error: unrecognized arguments {!r}; fail closed (cannot evaluate)".format(argv), file=sys.stderr)
        return 2
    if report_path is not None:
        # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    return _self_test()


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The self-test launches the helper as real
# processes and reads their exit statuses (subprocess), so under an ignored SIGCHLD a failing launch would
# read as a pass; the guard runs at the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("selftest_orch_dispatch_marker.py") or main())
