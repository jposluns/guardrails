#!/usr/bin/env python3
"""PostToolUse and PostToolUseFailure hook (all tools): inject the real clock into the assistant's context.

Motivated by an assistant composing console timestamps and "Session elapsed HH:MM" footers from its own
sense of time instead of reading the clock, drifting up to 4h40m ahead (timestamp-from-clock;
claims-rest-on-observation). After a tool call that ran, it emits ONE short additionalContext line read from
the process clock, for example:

    CLOCK (read by hook, authoritative): 2026-09-23 13:45:02 EDT | 2026-09-23T17:45:02Z | session elapsed 03:27

so a fresh authoritative reading is the most recent time value in context.

Coverage. Registered under PostToolUse it fires after a tool call that SUCCEEDED; registered under
PostToolUseFailure it fires after a tool call that started and FAILED. The emitted hookEventName matches the
incoming payload's hook_event_name (PostToolUseFailure when that is the event, otherwise PostToolUse), as the
hooks contract requires. Both registrations are needed for both outcomes. Neither event fires for a tool
call rejected before execution (an unknown tool, input failing validation, or a permission denial), so such
calls, and any stretch of prose with no tool call, see no fresh reading.

Elapsed resolution. The lease file is env AIQT_LEASE_FILE when it is set to a non-empty value (a legacy
spelling is accepted as a fallback, see _cfg); otherwise there is NO lease and the elapsed segment is
omitted. No lease is derived from the project directory, the payload cwd, or the process cwd: set
AIQT_LEASE_FILE (absolute) per session to enable elapsed.

Lease start (shared verbatim with stamp-truth-stop.py; a self-test in each asserts the copies are
identical). A lease FIELD line is `Name: value`, `**Name:** value`, or either form after a `-` or `*` list
bullet, with optional surrounding whitespace; the name is case-sensitive. ONLY the FIRST Active-session field
is read, and its value decides, in this precedence:
  a. `<label>-YYYYMMDDTHHMMSSZ`, where <label> is 1 to 32 characters of [A-Za-z0-9] (for example `sess-` or
     `S88-`): the start is that time (an impossible time, such as month 13, is unknown with no fallback).
  b. `none` (any case) or an empty value: the lease is INACTIVE, elapsed is unknown, and neither the
     Session-start-UTC field nor the transcript is consulted, so an inactive lease never shows elapsed.
  c. Any other value is an active id that carries no time (for example `sess-2026-09-23-opus55-r1`, or a
     malformed id): the start is the FIRST Session-start-UTC field of the lease's header section (the file
     up to the first markdown heading after the Active-session field), valued `YYYY-MM-DDTHH:MM:SSZ` or
     `YYYYMMDDTHHMMSSZ`. A malformed value there falls through to d.
  d. Else, when the payload carries transcript_path, the EARLIEST parseable top-level `timestamp` (UTC; a naive
     value is read as UTC) among the complete JSONL records in the transcript's first 256 KiB, read with the
     lease's posture (non-blocking open, regular files only, bounded). Only a record containing the bytes
     `"timestamp"` is parsed. There is no cache (this hook keeps no state), so this bounded read repeats on
     every call where it applies. Measured on the development host (2026-09-23, a full 256 KiB prefix of
     2 KiB timestamped records): about 0.5 ms per call in-process, within the noise of the roughly 25 ms
     the hook process takes to start and run.
  e. Else unknown.
No Active-session field at all is unknown with no fallback (a project with no lease shows no elapsed). The
lease file is opened non-blocking, must be a regular file, and at most 1 MiB is read. When the file is absent,
unreadable, not regular, or the start lies in the future, the elapsed segment is omitted. Local time is the
process zone.

Contract: context = hookSpecificOutput {hookEventName, additionalContext} JSON on stdout, exit 0. This hook
NEVER fails the tool: any error at all exits 0, including an argument, stdin, or JSON error before the
payload is evaluated. The payload is read as BYTES and parsed by json.loads, so its decoding does not depend
on the process locale. Unparseable hook input still emits the clock line under PostToolUse (the clock does not
depend on the payload). An error writing the output (a closed or full stdout) is swallowed and the hook
still exits 0 (round 24); if the stream cannot even be pointed at /dev/null, the hook ends at once with
os._exit(0), so no exit-time flush can fail it. Kill-switch: a subordinate worker process, detected as env
AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted, see _is_worker), exits 0 with no stdout, so it never
distorts worker output; it writes one warning line to stderr (round 24), which on exit 0 reaches only the
host's debug log.
In-session subagents are deliberately NOT skipped (there is no agent_id check): they benefit from the true
time too, and this hook only adds context, it never blocks.

RESIDUAL COVERAGE. This INFORMS; it enforces nothing. The model can
still ignore the line or mis-copy it; the companion Stop hook (stamp-truth-stop.py) is the check. The line
reflects the host clock, so a wrong host clock (no NTP) is reproduced faithfully. Elapsed is only as right
as the lease: a lease id minted from a wrong clock, a stale id after an unclean exit, or a configured lease
file that belongs to another session yields a wrong or missing elapsed. No session
ownership of the lease is validated. The Session-start-UTC field is trusted as written: a stale value, or
(when the current header section holds none) one from an earlier session's lines that sit above the next
heading, yields a wrong elapsed. The transcript fallback measures from the transcript's FIRST entry, not the
lease: for a resumed or continued session, or a transcript that predates the current lease, it OVERSTATES
elapsed; a transcript whose first 256 KiB holds no complete record with a top-level timestamp (a huge
first record, say) leaves elapsed unknown. A lease field in an unrecognized form (`Active-session**:`, a `+`
bullet, a numbered list) is not a field, so a later line in a recognized form is read instead. The events
covered are exactly those listed under Coverage.

Self-test: python3 -I -S -B clock-inject.py --self-test
"""

import datetime
import json
import os
import re
import stat
import sys

LEASE_MAX_BYTES = 1 << 20
_SESS_RE = re.compile(r"[A-Za-z0-9]{1,32}-(\d{8}T\d{6}Z)")
_EVENTS = ("PostToolUse", "PostToolUseFailure")


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1. Kept identical across the three hooks
    (python3 -I forbids a sibling import)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings


# ---- lease / elapsed (kept identical to stamp-truth-stop.py; python3 -I forbids a sibling import) ----

def read_regular(path, limit):
    """Bytes (at most `limit`) of a REGULAR file, or None. Opened non-blocking so a FIFO cannot stall us."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, TypeError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, got = [], 0
        while got < limit:
            b = os.read(fd, min(1 << 16, limit - got))
            if not b:
                break
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def lease_file():
    """Return the configured lease file path (env AIQT_LEASE_FILE, see _cfg), or None when none is set."""
    env = _cfg("LEASE_FILE")
    if env:
        return env
    return None


_LEASE_FIELD_RE = re.compile(r"[ \t]*(?:[-*][ \t]+)?(\*\*)?(Active-session|Session-start-UTC):(?(1)\*\*)(.*)")
_START_VALUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d{8}T\d{6}Z")
_HEADING_RE = re.compile(r"[ \t]{0,3}#{1,6}(?:[ \t]|$)")
TRANSCRIPT_PREFIX_BYTES = 256 << 10


def _utc_field(value):
    """Aware UTC datetime for a lease time value, YYYYMMDDTHHMMSSZ or YYYY-MM-DDTHH:MM:SSZ, else None."""
    if not isinstance(value, str) or not _START_VALUE_RE.fullmatch(value):
        return None
    try:
        return datetime.datetime.strptime(value.replace("-", "").replace(":", ""), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def transcript_start(path):
    """Earliest parseable top-level `timestamp` (UTC; a naive value is read as UTC) among the complete JSONL
    records in the first TRANSCRIPT_PREFIX_BYTES of a REGULAR transcript file, else None. Same read posture as
    the lease (non-blocking open, regular files only, bounded read). A record the bound may have cut is not
    parsed, and only a record holding the bytes `"timestamp"` is parsed at all."""
    if not isinstance(path, str) or not path:
        return None
    data = read_regular(path, TRANSCRIPT_PREFIX_BYTES)
    if not data:
        return None
    recs = data.split(b"\n")
    if len(data) >= TRANSCRIPT_PREFIX_BYTES:
        recs.pop()  # the last piece may be cut by the bound
    best = None
    for rec in recs:
        if b'"timestamp"' not in rec:
            continue
        try:
            entry = json.loads(rec)
            ts = entry.get("timestamp") if isinstance(entry, dict) else None
            if not isinstance(ts, str):
                continue
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt.astimezone(
                datetime.timezone.utc)
        except (ValueError, TypeError, OverflowError, RecursionError):
            continue
        if best is None or dt < best:
            best = dt
    return best


def lease_start(path, transcript=None):
    """UTC session start, or None. Only the FIRST Active-session field (`Active-session:`, `**Active-session:**`,
    or either after a `-` or `*` bullet) is read. Precedence: (a) its id's time when the value is
    <label>-YYYYMMDDTHHMMSSZ; (b) when the id carries no time, the FIRST Session-start-UTC field (same forms)
    of the lease's header section (the file up to the first heading after the Active-session field); (c)
    else the earliest timestamp of `transcript`, when given; (d) else None. An inactive lease (`none`, any
    case, or an empty value), a well-formed id with an impossible time, no Active-session field, or an
    unreadable lease is None with no fallback."""
    if not path:
        return None
    data = read_regular(path, LEASE_MAX_BYTES)
    if data is None:
        return None
    active, start = False, None  # start: the text of the first Session-start-UTC value seen
    for line in data.decode("utf-8", "replace").splitlines():
        if active and _HEADING_RE.match(line):
            break  # the header section ends at the first heading after the Active-session field
        m = _LEASE_FIELD_RE.match(line)
        if not m:
            continue
        value = m.group(3).strip()
        if m.group(2) == "Session-start-UTC":
            if start is None:
                start = value
                if active:
                    break
            continue
        if active:
            continue  # only the FIRST Active-session field is read
        if not value or value.lower() == "none":
            return None  # inactive: no elapsed, and no fallback
        sess = _SESS_RE.fullmatch(value)
        if sess:
            return _utc_field(sess.group(1))
        active = True  # an active id that carries no time
        if start is not None:
            break
    if not active:
        return None
    got = _utc_field(start)
    return got if got is not None else transcript_start(transcript)


def fmt_elapsed(delta):
    """HH:MM (hours may exceed 24) for a non-negative timedelta, else None."""
    secs = int(delta.total_seconds())
    if secs < 0:
        return None
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


def clock_line(now_utc, start=None):
    local = now_utc.astimezone()
    line = (f"CLOCK (read by hook, authoritative): {local.strftime('%Y-%m-%d %H:%M:%S')} {local.tzname()} | "
            f"{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if start is not None:
        el = fmt_elapsed(now_utc - start)
        if el is not None:
            line += f" | session elapsed {el}"
    return line


def _emit_line(text, stream=None):
    """Write one line to `stream` (default stdout) and flush it. An output error fails OPEN (round 24): it is
    swallowed, and the stream's descriptor is pointed at /dev/null so the interpreter's exit flush cannot fail
    either, so the hook still exits 0. If that rescue fails too, the process ends at once with os._exit(0)
    (no flush is retried), since any later write or exit flush could fail the hook."""
    s = sys.stdout if stream is None else stream
    try:
        print(text, file=s, flush=True)
        return True
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, s.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)
        return False


def _emit(line, event="PostToolUse"):
    _emit_line(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}))


def main(argv):
    try:
        self_test = len(argv) > 1 and argv[1] == "--self-test"
    except Exception:
        return 0  # an unusable argv fails open: exit 0 at once, evaluating nothing
    if self_test:
        return _self_test()
    try:
        if _is_worker():
            # round 24: the skip is no longer silent (stderr only, so worker output is never distorted)
            _emit_line("clock-inject: skipped, worker marker present (AIQT_HOOKS_WORKER=1 or a legacy spelling)",
                       sys.stderr)
            return 0
        try:
            buf = getattr(sys.stdin, "buffer", None)  # bytes; a text stream (the self-test) has no buffer
            payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        event = payload.get("hook_event_name")
        event = event if event in _EVENTS else "PostToolUse"
        tp = payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None
        now = datetime.datetime.now(datetime.timezone.utc)
        _emit(clock_line(now, lease_start(lease_file(), tp)), event)
    except Exception:
        pass  # never fail the tool
    return 0


def _self_test():
    import io
    import shutil
    import subprocess
    import tempfile
    import unittest

    utc = datetime.timezone.utc
    line_re = re.compile(r"^CLOCK \(read by hook, authoritative\): \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+ \| "
                         r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z( \| session elapsed \d{2,}:\d{2})?$")

    def run_main(stdin_text, env=None, argv=("clock-inject.py",)):
        old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
        sys.stdin = io.StringIO(stdin_text) if isinstance(stdin_text, str) else stdin_text
        sys.stdout = io.StringIO()
        try:
            for k in ("AIQT_HOOKS_WORKER", "ORCH_WORKER", "ORCH_VERIFY_OWNER", "AIQT_LEASE_FILE", "ORCH_LEASE_FILE",
                      "CLAUDE_PROJECT_DIR"):
                os.environ.pop(k, None)
            os.environ.update(env or {})
            rc = main(list(argv) if isinstance(argv, tuple) else argv)
            return rc, sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            os.environ.clear()
            os.environ.update(old_env)

    class T(unittest.TestCase):
        def setUp(self):
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            self.tmp = tempfile.mkdtemp(prefix="clk.", dir=base)
            self.lease = os.path.join(self.tmp, "lease.md")
            self._env = {k: os.environ.pop(k, None) for k in ("AIQT_LEASE_FILE", "ORCH_LEASE_FILE",
                                                               "CLAUDE_PROJECT_DIR")}

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)
            for k, v in self._env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

        def test_line_well_formed_with_elapsed(self):
            now = datetime.datetime(2026, 9, 23, 17, 45, 2, tzinfo=utc)
            ln = clock_line(now, datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=utc))
            self.assertRegex(ln, line_re)
            self.assertIn("| 2026-09-23T17:45:02Z | session elapsed 03:27", ln)

        def test_line_omits_elapsed_when_unknown(self):
            ln = clock_line(datetime.datetime(2026, 9, 23, 17, 45, 2, tzinfo=utc), None)
            self.assertRegex(ln, line_re)
            self.assertNotIn("elapsed", ln)

        def test_future_lease_omits_elapsed(self):
            now = datetime.datetime(2026, 9, 23, 17, 0, 0, tzinfo=utc)
            self.assertNotIn("elapsed", clock_line(now, now + datetime.timedelta(hours=1)))

        def test_elapsed_over_24h(self):
            self.assertEqual(fmt_elapsed(datetime.timedelta(hours=27, minutes=5)), "27:05")

        def test_lease_parse_first_field_only(self):
            with open(self.lease, "w") as f:
                f.write("Status: active\nActive-session: sess-20260923T141710Z\n"
                        "Active-session: sess-20200101T000000Z\n")
            self.assertEqual(lease_start(self.lease), datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=utc))

        def test_inactive_lease_never_reads_history(self):
            # finding: `none` followed by a history line used to resolve to the historical id
            with open(self.lease, "w") as f:
                f.write("Active-session: none\n## History\nActive-session: sess-20200101T000000Z\n")
            self.assertIsNone(lease_start(self.lease))
            with open(self.lease, "w") as f:
                f.write("Active-session: sess-2026BAD\nActive-session: sess-20200101T000000Z\n")
            self.assertIsNone(lease_start(self.lease))

        def test_lease_missing(self):
            self.assertIsNone(lease_start(os.path.join(self.tmp, "absent.md")))
            self.assertIsNone(lease_start(None))

        def test_fifo_lease_does_not_block(self):
            fifo = os.path.join(self.tmp, "fifo")
            os.mkfifo(fifo)
            code = ("import importlib.util as u;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print(m.lease_start(%r))" % (os.path.abspath(__file__), fifo))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=5)
            self.assertEqual(r.stdout.strip(), "None")

        def test_lease_file_derivation(self):
            self.assertIsNone(lease_file())  # nothing configured: no lease, no derivation
            os.environ["ORCH_LEASE_FILE"] = self.lease + ".legacy"
            self.assertEqual(lease_file(), self.lease + ".legacy")  # the legacy spelling is a fallback
            os.environ["AIQT_LEASE_FILE"] = self.lease
            self.assertEqual(lease_file(), self.lease)  # AIQT_ beats ORCH_
            os.environ["AIQT_LEASE_FILE"] = ""
            self.assertIsNone(lease_file())  # a set-but-empty AIQT_ value still beats ORCH_: no lease

        def test_cfg_precedence(self):
            self.assertEqual(_cfg("X", {"AIQT_X": "a", "ORCH_X": "o"}), "a")
            self.assertEqual(_cfg("X", {"AIQT_X": "", "ORCH_X": "o"}), "")
            self.assertEqual(_cfg("X", {"ORCH_X": "o"}), "o")
            self.assertIsNone(_cfg("X", {}))

        def test_no_implicit_lease_discovery(self):
            # the project-directory and cwd tiers were removed: only an explicit env lease enables elapsed
            proj = os.path.join(self.tmp, "proj")
            for sub in (".working", ".aiqt", "private"):
                os.makedirs(os.path.join(proj, sub))
                for name in ("lease.md", "session-lease.md"):
                    with open(os.path.join(proj, sub, name), "w") as f:
                        f.write("Active-session: S88-20000101T000000Z\n")
            os.environ["CLAUDE_PROJECT_DIR"] = proj
            self.assertIsNone(lease_file())
            rc, out = run_main(json.dumps({"tool_name": "Bash", "cwd": proj}), {"CLAUDE_PROJECT_DIR": proj})
            self.assertNotIn("elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"])

        def test_garbage_input_emits_and_exits_0(self):
            rc, out = run_main("}{ not json", {"AIQT_LEASE_FILE": self.lease})
            self.assertEqual(rc, 0)
            obj = json.loads(out)
            self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            self.assertRegex(obj["hookSpecificOutput"]["additionalContext"], line_re)

        def test_payload_with_lease_emits_elapsed(self):
            with open(self.lease, "w") as f:
                f.write("Active-session: sess-20000101T000000Z\n")
            rc, out = run_main(json.dumps({"tool_name": "Bash", "cwd": "/"}), {"AIQT_LEASE_FILE": self.lease})
            self.assertEqual(rc, 0)
            self.assertIn("session elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"])
            rc, out = run_main(json.dumps({"tool_name": "Bash", "cwd": "/"}), {"ORCH_LEASE_FILE": self.lease})
            self.assertIn("session elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"])  # legacy

        def test_failure_event_name_matches(self):
            rc, out = run_main(json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
            rc, out = run_main(json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash"}))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            rc, out = run_main(json.dumps({"hook_event_name": "Bogus"}))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUse")

        def test_worker_kill_switch_silent(self):
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}):
                rc, out = run_main(json.dumps({"tool_name": "Bash"}), env)
                self.assertEqual((rc, out), (0, ""), env)

        def test_worker_detected_by_verify_owner(self):
            # legacy spelling: a launcher that exports ORCH_VERIFY_OWNER (any value, even empty) marks a worker
            for value in ("x", ""):
                rc, out = run_main(json.dumps({"tool_name": "Bash"}), {"ORCH_VERIFY_OWNER": value})
                self.assertEqual((rc, out), (0, ""))
            self.assertTrue(_is_worker({"ORCH_VERIFY_OWNER": ""}))
            self.assertTrue(_is_worker({"ORCH_WORKER": "1"}))
            self.assertFalse(_is_worker({"ORCH_WORKER": "0"}))
            self.assertFalse(_is_worker({}))

        def test_aiqt_hooks_worker(self):
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "1"}))
            for value in ("0", "", "true", "yes", " 1"):
                self.assertFalse(_is_worker({"AIQT_HOOKS_WORKER": value}), value)
                rc, out = run_main(json.dumps({"tool_name": "Bash"}), {"AIQT_HOOKS_WORKER": value})
                self.assertRegex(json.loads(out)["hookSpecificOutput"]["additionalContext"], line_re)
            # the legacy spellings still mark a worker whatever AIQT_HOOKS_WORKER says
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "0", "ORCH_VERIFY_OWNER": ""}))

        def test_subagent_still_gets_clock(self):
            # deliberate: in-session subagents are not skipped (context only, never a block)
            rc, out = run_main(json.dumps({"tool_name": "Bash", "agent_id": "a1"}))
            self.assertRegex(json.loads(out)["hookSpecificOutput"]["additionalContext"], line_re)

        def test_lease_label_portability(self):
            want = datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=utc)
            for label in ("sess", "S88", "a", "A" * 32):
                with open(self.lease, "w") as f:
                    f.write(f"Active-session: {label}-20260923T141710Z\n")
                self.assertEqual(lease_start(self.lease), want, label)
            for bad in ("A" * 33 + "-20260923T141710Z", "-20260923T141710Z", "S_88-20260923T141710Z",
                        "S88 20260923T141710Z", "S88-20260923T141710"):
                with open(self.lease, "w") as f:
                    f.write(f"Active-session: {bad}\nActive-session: sess-20200101T000000Z\n")
                self.assertIsNone(lease_start(self.lease), bad)

        # -- round 13 (peer report): lease start sources --
        def write_lease(self, text):
            with open(self.lease, "w") as f:
                f.write(text)
            return self.lease

        def transcript(self, *stamps):
            tp = os.path.join(self.tmp, "t.jsonl")
            with open(tp, "w") as f:
                f.write(json.dumps({"type": "summary", "summary": "s"}) + "\n")
                for ts in stamps:
                    f.write(json.dumps({"type": "user", "timestamp": ts, "message": {"content": "x"}}) + "\n")
            return tp

        def test_r13_lease_field_forms(self):
            want = datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=utc)
            for line in ("Active-session: sess-20260923T141710Z", "**Active-session:** sess-20260923T141710Z",
                         "- Active-session: sess-20260923T141710Z", "* Active-session: sess-20260923T141710Z",
                         "  - **Active-session:**  sess-20260923T141710Z "):
                self.assertEqual(lease_start(self.write_lease(f"# Lease\n\n{line}\n")), want, line)
            self.assertEqual(lease_start(self.write_lease("+ Active-session: sess-20200101T000000Z\n"
                                                          "Active-session: sess-20260923T141710Z\n")), want)

        def test_r13_date_only_id_with_and_without_session_start(self):
            ids = "# Lease\n\n**Active-session:** sess-2026-09-23-opus55-r1\n\n**Status:** active\n"
            self.assertIsNone(lease_start(self.write_lease(ids)))
            want = datetime.datetime(2026, 9, 23, 14, 18, 0, tzinfo=utc)
            for field in ("**Session-start-UTC:** 2026-09-23T14:18:00Z", "* Session-start-UTC: 20260923T141800Z"):
                self.assertEqual(lease_start(self.write_lease(ids + field + "\n")), want, field)
            self.assertIsNone(lease_start(self.write_lease(ids + "## History\nSession-start-UTC: 20260920T010000Z\n")))

        def test_r13_transcript_fallback_and_main(self):
            ids = "**Active-session:** sess-2026-09-23-opus55-r1\n"
            tp = self.transcript("2026-09-23T14:30:00.000Z", "2026-09-23T14:18:00.000Z", "garbage")
            self.assertEqual(lease_start(self.write_lease(ids), tp), datetime.datetime(2026, 9, 23, 14, 18, tzinfo=utc))
            self.assertIsNone(lease_start(self.lease))  # no transcript given: unknown
            start = datetime.datetime.now(utc) - datetime.timedelta(hours=2, minutes=5, seconds=30)
            tp = self.transcript(start.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": tp}),
                               {"AIQT_LEASE_FILE": self.lease})
            self.assertRegex(json.loads(out)["hookSpecificOutput"]["additionalContext"], r"session elapsed 02:0[56]$")
            rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": 7}), {"AIQT_LEASE_FILE": self.lease})
            self.assertNotIn("elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"])

        def test_r13_inactive_lease_stays_unknown(self):
            tp = self.transcript("2026-09-23T14:18:00Z")
            for head in ("Active-session: none", "**Active-session:** none", "- Active-session: None", "Active-session:"):
                self.write_lease(f"{head}\nSession-start-UTC: 2026-09-23T14:18:00Z\n")
                self.assertIsNone(lease_start(self.lease, tp), head)
                rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": tp}),
                                   {"AIQT_LEASE_FILE": self.lease})
                self.assertNotIn("elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"], head)
            self.assertIsNone(lease_start(self.write_lease("# no lease field\n"), tp))

        def test_r13_fifo_transcript_does_not_block(self):
            fifo = os.path.join(self.tmp, "tfifo")
            os.mkfifo(fifo)
            self.write_lease("**Active-session:** sess-2026-09-23-opus55-r1\n")
            code = ("import importlib.util as u;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print(m.lease_start(%r, %r))" % (os.path.abspath(__file__), self.lease, fifo))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=5)
            self.assertEqual(r.stdout.strip(), "None")

        def test_r13_shared_lease_code_identical_to_stop_hook(self):
            import importlib.util
            import inspect
            sib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stamp-truth-stop.py")
            spec = importlib.util.spec_from_file_location("sts_sibling", sib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for name in ("read_regular", "lease_file", "_utc_field", "transcript_start", "lease_start", "_is_worker",
                     "_cfg"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)
            for name in ("_SESS_RE", "_LEASE_FIELD_RE", "_START_VALUE_RE", "_HEADING_RE"):
                self.assertEqual((getattr(mod, name).pattern, getattr(mod, name).flags),
                                 (globals()[name].pattern, globals()[name].flags), name)
            self.assertEqual((mod.LEASE_MAX_BYTES, mod.TRANSCRIPT_PREFIX_BYTES),
                             (LEASE_MAX_BYTES, TRANSCRIPT_PREFIX_BYTES))


        # -- round 24 (field validation of round 12) --
        def test_r24_worker_skip_warns_and_output_error_fails_open(self):
            old_err, sys.stderr = sys.stderr, io.StringIO()
            try:
                rc, out = run_main(json.dumps({"tool_name": "Bash"}), {"AIQT_HOOKS_WORKER": "1"})
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old_err
            self.assertEqual((rc, out), (0, ""))
            self.assertEqual(err.count("\n"), 1)
            self.assertIn("skipped, worker marker present", err)
            if not os.path.exists("/dev/full"):
                self.skipTest("/dev/full absent")
            env = {k: v for k, v in os.environ.items() if k not in ("AIQT_HOOKS_WORKER", "ORCH_WORKER",
                                                               "ORCH_VERIFY_OWNER")}
            env["AIQT_LEASE_FILE"] = os.path.join(self.tmp, "no-lease.md")  # never the host's real lease
            with open("/dev/full", "w") as full:
                p = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__)], stdout=full,
                                   stderr=subprocess.PIPE, text=True, env=env, timeout=30, input="{}")
            self.assertEqual(p.returncode, 0, p.stderr)

        # -- generic port: bytes payload, a failed output rescue, fail-open before evaluation --
        def test_payload_read_as_bytes(self):
            # a UTF-8 payload behind a text layer that cannot decode it: only a bytes read parses it
            note = chr(0xe9) + chr(0x2603)
            raw = json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "note": note},
                             ensure_ascii=False).encode("utf-8")
            stream = io.TextIOWrapper(io.BytesIO(raw), encoding="ascii")
            rc, out = run_main(stream)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
            stream = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="ascii")  # undecodable: fails open
            rc, out = run_main(stream)
            self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["hookEventName"]), (0, "PostToolUse"))

        def test_emit_rescue_failure_exits_0(self):
            code = ("import importlib.util as u, io;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print('before', flush=True);b=io.StringIO();b.close();"
                    "m._emit_line('x', b);print('after', flush=True)" % os.path.abspath(__file__))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "before\n", ""))

        def test_fail_open_before_evaluation(self):
            class Unreadable:
                def read(self, *a):
                    raise OSError("unreadable stdin")
            for stdin, argv in ((Unreadable(), ("clock-inject.py",)), ("[1, 2]", ("clock-inject.py",)),
                                ('"text"', ("clock-inject.py",)), ("{}", [None, 3])):
                rc, out = run_main(stdin, argv=argv)
                self.assertEqual(rc, 0, (stdin, argv))
                obj = json.loads(out)["hookSpecificOutput"]
                self.assertEqual(obj["hookEventName"], "PostToolUse")
                self.assertRegex(obj["additionalContext"], line_re)
            for argv in (None, 7):  # argv that cannot be inspected fails open: exit 0, silent, nothing evaluated
                self.assertEqual(run_main("{}", argv=argv), (0, ""), argv)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromTestCase(T))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
