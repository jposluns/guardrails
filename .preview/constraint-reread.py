#!/usr/bin/env python3
"""Compaction hook (constraint-reread): standing constraints are re-read from the record after a compaction.

WHAT IT DOES
    A constraint that the operator or a governing rule has set (propose but do not act without approval, never
    touch a named resource) stays in force when the working context is compacted; it does not lapse because it
    is no longer visible (standing-constraints-persist). This hook puts the standing constraints back in view
    after a compaction, asks the assistant to re-read them from the project's durable record, and refuses the
    turn end while no re-read entry is recorded, up to a loop cap after which the stop is allowed:

    1. When the platform reports a compaction, the hook reads the clock, records that time as the compaction
       time in a private per-session state file, and adds a reminder to the assistant's context that names the
       standing constraints the durable record lists.
    2. On every later user prompt (UserPromptSubmit) the same reminder is added again, until the durable record
       holds a re-read entry dated after that compaction.
    3. At each turn end (Stop) in that window the hook refuses the stop and says why. A loop cap bounds this:
       it refuses at most BLOCK_CAP times in a row, then allows the stop with a one-line warning, so it never
       blocks without bound (LOOP CAP, below).

    Events: SessionStart (matcher `compact`, or no matcher), PreCompact (optional), UserPromptSubmit, and Stop.
    Output: nothing, or ONE line of JSON on stdout: a hookSpecificOutput additionalContext reminder
    (SessionStart, UserPromptSubmit), a top-level decision "block" object with a reason (Stop), or a top-level
    systemMessage warning (Stop, at the cap or when the count cannot be kept). Exit status: 0, except the
    floor guard's exit 1 on an interpreter older than Python 3.14 (FAILURE DIRECTION).

LOOP CAP
    The refusal count is kept in the state file, so the cap holds when the platform's stop_hook_active field is
    absent or true: a Stop without the field is not read as a new turn, so the count goes on from the stored
    value. An explicit false is trusted as the platform's statement that this stop does not continue a
    stop-hook refusal (a new turn) and resets the count, so the cap does not hold against a host that sends
    false on a Stop that does continue a refusal: each such Stop is refused again. The count is reset to 0 only
    by a signal of a new turn that the hook receives: a UserPromptSubmit event (a prompt the user submitted), a
    new compaction (which records a fresh state), or a Stop whose stop_hook_active field is explicitly false.
    An allowed stop does not reset it: another Stop hook may
    still refuse that same stop, and two hooks that each reset on their own allowed stop could refuse in turn
    without bound. So after BLOCK_CAP refusals each later Stop is allowed with the warning until one of those
    signals arrives.

DETECTION
    The compaction marker is the platform's own, not a heuristic: the SessionStart payload field `source` with
    the value `compact` (the session continuing after a manual or automatic compaction), and the PreCompact
    event (fired just before one). Both are taken from the platform's hooks reference, which .preview/README.md
    links. Those names were not re-fetched from that page on the day this file was written, so treat them as
    the reference's names to be re-checked, not as verified here. A payload without either marker is not a
    compaction to this hook: there is no fallback heuristic (no transcript scan), so a host that reports a
    compaction some other way is not detected. When both events are registered the later reading wins, and a
    compaction recorded at PreCompact that then did not happen still asks for a re-read (the safe direction).

THE DURABLE RECORD
    AIQT_CONSTRAINT_RECORD names the record, as an absolute path to a text file the project keeps (a legacy
    ORCH_ spelling is read when the AIQT_ one is unset). Unset, empty, or relative, the hook does nothing at
    all. Two field lines are read, each `Name: value` at the start of a line, optionally after a `-` or `*`
    bullet; the name is case-sensitive:
        Constraint: <text>                   a standing constraint; the first MAX_NAMED are named in the reminder
        Constraints-reread: <UTC time>       a post-compaction re-read entry, `YYYY-MM-DDTHH:MM:SSZ` or
                                             `YYYYMMDDTHHMMSSZ`
    A re-read entry counts only when its time is AFTER the recorded compaction time and not more than
    CLOCK_SKEW seconds ahead of the clock: an entry dated in the future was composed, not read from the clock,
    so it is ignored. The hook only reads the record; the assistant writes the entry after re-reading it, with
    the time taken from `date -u +%Y-%m-%dT%H:%M:%SZ`. At most RECORD_MAX_BYTES of the record are read; when
    the record is longer, the line cut at that limit is dropped, so a cut line is never read as an entry.

STATE
    The compaction time and the refusal count live in one JSON file per session, named by a SHA-256 of the
    payload's session_id (else its transcript_path), in AIQT_HOOK_STATE_DIR/constraint-reread when that is set
    to an absolute path, else $XDG_STATE_HOME/aiqt-guardrails/constraint-reread, else
    $HOME/.local/state/aiqt-guardrails/constraint-reread (created 0700, written by an atomic replace).

FAILURE DIRECTION
    Each event fails toward its own safe direction. The reminder events remind when in doubt: a record that
    cannot be read keeps the reminder on (it says the record could not be read and asks the assistant to hold
    and check), and a compaction whose state cannot be saved is still reminded once at SessionStart. A state
    file that exists but cannot be parsed, or is longer than STATE_MAX_BYTES (never parsed from a cut prefix),
    is read as a compaction no earlier than the file's modification time, so only an entry after that time
    clears it. A state path under a component that is not a directory is
    read as no state (nothing can have been saved there). A state location that cannot be examined at all
    (any other error) keeps the reminder on with the compaction time unknown, and its Stop is allowed with a
    warning, since no entry can be dated against an unknown time; that reminder says so, and says that only
    restoring access to the state location clears it. The Stop event refuses while a re-read is
    outstanding, but its count is the loop bound: an unknown count (a state file that cannot be parsed) or a
    count that cannot be saved allows the stop with a warning rather than refusing, whether or not the
    payload carries stop_hook_active; only a Stop whose stop_hook_active field is explicitly false (read as a
    new turn, count 0) still refuses once with an unsaveable count, and a later Stop without the field or
    with it true is then allowed. An unknown count persists: a Stop does not repair the state (the hook cannot
    tell how many refusals the lost count held), so every Stop is allowed with the warning until a
    UserPromptSubmit event, a Stop with stop_hook_active explicitly false, or a new compaction writes a
    well-formed state. Any error in the hook itself, an unreadable payload, or an unrecognized
    event exits 0 with no output (fail open). The one exception to exit 0 is an interpreter older than
    Python 3.14 that can start the hook: the guard at the top of this file reads no input, writes one line beginning
    `error: constraint-reread.py requires Python 3.14 or newer` to stderr and exits 1, which every event this
    hook uses treats as a non-blocking error: no reminder is added at SessionStart or UserPromptSubmit, no
    compaction time is recorded at SessionStart or PreCompact, and the stop goes ahead unchecked. It does not
    exit 2: on a Stop exit 2 blocks the stop, and the guard runs before the loop cap, so this hook's own loop
    cap would never run (any limit the host itself applies is outside this hook); on UserPromptSubmit exit 2
    blocks the prompt. An older interpreter that cannot start the hook never reaches the guard and fails with
    Python's own error first: one that predates the -I option exits 2, which blocks every stop, with this
    hook's own loop cap never running, and blocks every UserPromptSubmit prompt (SessionStart cannot block,
    and what the host does with exit 2 on PreCompact is outside this hook); one that accepts -I but cannot
    compile this file exits 1, a non-blocking error, with the same effect as the guard; .preview/README.md
    (Installing a hook, step 4) describes those cases. A worker process (AIQT_HOOKS_WORKER=1, or a legacy
    spelling) is skipped.

RESIDUAL COVERAGE
    This hook proves that a re-read ENTRY was written after the compaction, not that the assistant read the
    record or will honour it: an entry written without reading, or a constraint ignored after reading, passes.
    It sees only constraints the project declares in the record; a constraint set only in conversation and
    never written there is not named, and nothing here checks that the record is complete or current. It
    detects a compaction only through the platform markers above, so a context truncated, summarized, or
    handed to a new session without one (a new session started by hand, a host without these events, a hook
    not registered for SessionStart or PreCompact) is not seen; a new session_id after a resume starts with no
    state. With no state location (no AIQT_HOOK_STATE_DIR, XDG_STATE_HOME, or HOME as an absolute path) or no
    session key in the payload, the compaction is reminded once at SessionStart and then forgotten: no later
    reminder, no Stop refusal. Concurrent hook runs in one session can lose a count update (the replace is
    atomic, so the file is never torn). The loop cap bounds consecutive refusals (LOOP CAP), so a model that
    ignores the refusal is held for at most BLOCK_CAP refusals and then allowed with a warning; the next
    prompt the user submits, a new compaction, or a Stop with stop_hook_active explicitly false starts the
    count again. Without UserPromptSubmit registered and without that field, the count stays at the cap, so
    every later Stop until the next compaction is allowed with the warning (a missed refusal), and a state file
    that cannot be parsed (an unknown count) has the same effect from its first Stop. An explicit false is
    trusted as a new turn, so a host that sends it on a Stop that continues a refusal is refused at every such
    Stop (the cap does not hold). An entry
    beyond the first RECORD_MAX_BYTES of the record is not seen (the reminder stays on). The host clock is
    trusted: a wrong clock dates the compaction wrongly.

Self-test: python3 -I -S -B constraint-reread.py --self-test
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: constraint-reread.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(1)

import datetime
import hashlib
import json
import os
import re
import stat
import tempfile

HOOK = "constraint-reread"
RECORD_MAX_BYTES = 1 << 20
STATE_MAX_BYTES = 1 << 16
BLOCK_CAP = 3  # consecutive refusals before a Stop is allowed with a warning (LOOP CAP in the docstring)
CLOCK_SKEW = 60  # seconds an entry may lie ahead of the clock and still count
MAX_NAMED = 20  # constraints named in one reminder
MAX_ITEM = 300  # characters kept of one named constraint
ENTRY_FIELD = "Constraints-reread"
CONSTRAINT_FIELD = "Constraint"
_FIELD_RE = re.compile(r"[ \t]*(?:[-*][ \t]+)?([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*?)[ \t]*")
_STAMP_RES = (re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z"),
              re.compile(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z"))
_UTC = datetime.timezone.utc


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings


def stamp(dt):
    return dt.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text):
    """A UTC time in either accepted form, or None (a malformed or impossible time)."""
    for rx in _STAMP_RES:
        m = rx.fullmatch(text.strip())
        if m:
            try:
                return datetime.datetime(*(int(g) for g in m.groups()), tzinfo=_UTC)
            except ValueError:
                return None
    return None


def record_path(env):
    v = _cfg("CONSTRAINT_RECORD", env)
    return v if v and os.path.isabs(v) else None


def read_record(path):
    """(text, None) or (None, why). Opened non-blocking (a FIFO never hangs the hook), regular files only,
    at most RECORD_MAX_BYTES read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        return None, "cannot open it: " + (exc.strerror or type(exc).__name__)
    with os.fdopen(fd, "rb") as fh:
        try:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None, "it is not a regular file"
            data = fh.read(RECORD_MAX_BYTES + 1)
        except OSError as exc:
            return None, "cannot read it: " + (exc.strerror or type(exc).__name__)
    if len(data) > RECORD_MAX_BYTES:  # longer than the limit: drop the line the limit cuts
        data = data[:RECORD_MAX_BYTES]
        data = data[:data.rfind(b"\n") + 1]
    return data.decode("utf-8", "replace"), None


def scan_record(text):
    """([constraint text], [re-read entry time]) from the record's field lines."""
    constraints, entries = [], []
    for line in text.splitlines():
        m = _FIELD_RE.fullmatch(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if name == CONSTRAINT_FIELD and value:
            constraints.append(value)
        elif name == ENTRY_FIELD:
            t = parse_stamp(value)
            if t is not None:
                entries.append(t)
    return constraints, entries


def state_dir(env):
    v = _cfg("HOOK_STATE_DIR", env)
    if v and os.path.isabs(v):
        return os.path.join(v, HOOK)
    x = env.get("XDG_STATE_HOME")
    if x and os.path.isabs(x):
        return os.path.join(x, "aiqt-guardrails", HOOK)
    h = env.get("HOME")
    if h and os.path.isabs(h):
        return os.path.join(h, ".local", "state", "aiqt-guardrails", HOOK)
    return None


def state_path(payload, env):
    sid = payload.get("session_id")
    tp = payload.get("transcript_path")
    if isinstance(sid, str) and sid:
        key = "s:" + sid
    elif isinstance(tp, str) and tp:
        key = "t:" + tp
    else:
        return None
    d = state_dir(env)
    if d is None:
        return None
    return os.path.join(d, hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32] + ".json")


def load_state(path):
    """("absent", None, None), ("ok", compacted_at, blocks), or ("bad", bound_or_None, None). A bad state's
    bound is the file's modification time (no earlier than the last write), else None (unknown)."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "absent", None, None  # a path under a non-directory: no state can have been saved there
    except OSError:
        return "bad", None, None
    bound = datetime.datetime.fromtimestamp(int(st.st_mtime), _UTC)
    if not stat.S_ISREG(st.st_mode):
        return "bad", bound, None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(STATE_MAX_BYTES + 1)
        if len(data) > STATE_MAX_BYTES:
            raise ValueError("oversized state")  # never parse a cut prefix
        obj = json.loads(data)
        when = parse_stamp(obj["compacted_at"])
        blocks = obj["blocks"]
        if when is None or type(blocks) is not int or blocks < 0:
            raise ValueError("malformed state")
        return "ok", when, blocks
    except Exception:
        return "bad", bound, None


def save_state(path, compacted_at, blocks):
    """Write the state by an atomic replace; True on success, False on any failure (never raises)."""
    tmp = None
    try:
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(dict(compacted_at=stamp(compacted_at), blocks=blocks), fh)
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def status(spath, rpath, now):
    """The outstanding re-read, or None when none is outstanding or none can be known (no state file)."""
    if spath is None:
        return None
    kind, since, blocks = load_state(spath)
    if kind == "absent":
        return None
    text, why = read_record(rpath)
    constraints = []
    if text is not None:
        constraints, entries = scan_record(text)
        limit = now + datetime.timedelta(seconds=CLOCK_SKEW)
        if since is not None and any(since < t <= limit for t in entries):
            return None
    return dict(since=since, blocks=blocks, constraints=constraints, why=why, kind=kind)


def reminder(since, rpath, constraints, why):
    if since is not None:
        head = "a context compaction at " + stamp(since) + " may have removed standing constraints from view"
    else:
        head = ("this hook's state location could not be examined, so it cannot tell whether or when a context "
                "compaction removed standing constraints from view")
    parts = ["STANDING CONSTRAINTS (re-injected by the constraint-reread hook): " + head + "; compaction does "
             "not lift them, and only the authority that set a constraint can lift it. The durable record is "
             + rpath + "."]
    if why is not None:
        parts.append("The record could not be read (" + why + "); hold and check before any action a "
                     "constraint might cover.")
    elif constraints:
        named = ["(" + str(i) + ") " + c[:MAX_ITEM] for i, c in enumerate(constraints[:MAX_NAMED], 1)]
        more = len(constraints) - MAX_NAMED
        parts.append("Constraints the record names: " + " ".join(named)
                     + (" (and " + str(more) + " more in the record)" if more > 0 else ""))
    else:
        parts.append("The record names no `" + CONSTRAINT_FIELD + ":` line; re-read it whole.")
    if since is None:
        parts.append("Re-read the record before any action a constraint might cover. No `" + ENTRY_FIELD
                     + ":` entry can clear this reminder while the state location cannot be examined; it stops "
                     "when access to this hook's state directory is restored. The turn end is not refused in "
                     "this state.")
    else:
        parts.append("Re-read the record now, then append one line to it: `" + ENTRY_FIELD + ": ` followed by "
                     "the output of `date -u +%Y-%m-%dT%H:%M:%SZ`. Until that entry exists this reminder repeats "
                     "each turn, and each turn end is refused, at most " + str(BLOCK_CAP) + " times in a row "
                     "before the stop is allowed with a warning.")
    return " ".join(parts)


def _context(event, text):
    return dict(hookSpecificOutput=dict(hookEventName=event, additionalContext=text))


def decide(payload, env, now):
    """The output object for one hook call, or None for no output. Saves state as a side effect."""
    rpath = record_path(env)
    if rpath is None:
        return None  # off: no durable record declared
    event = payload.get("hook_event_name")
    now = now.replace(microsecond=0)
    spath = state_path(payload, env)
    if event == "PreCompact" or (event == "SessionStart" and payload.get("source") == "compact"):
        if spath is not None:
            save_state(spath, now, 0)  # a failed save still reminds once below; later turns cannot know
        if event == "PreCompact":
            return None  # PreCompact output does not reach the assistant's context
        text, why = read_record(rpath)
        constraints = scan_record(text)[0] if text is not None else []
        return _context(event, reminder(now, rpath, constraints, why))
    if event not in ("SessionStart", "UserPromptSubmit", "Stop"):
        return None
    st = status(spath, rpath, now)
    if st is None:
        return None
    if event != "Stop":
        if event == "UserPromptSubmit" and st["since"] is not None and st["blocks"] != 0:
            save_state(spath, st["since"], 0)  # a prompt the user submitted starts a new turn: the count restarts
        return _context(event, reminder(st["since"], rpath, st["constraints"], st["why"]))
    new_turn = payload.get("stop_hook_active") is False  # an absent field is not a new turn (LOOP CAP)
    blocks = 0 if new_turn else st["blocks"]
    allowed = "constraint-reread: turn end allowed without a post-compaction re-read entry in " + rpath
    if st["since"] is None:  # no entry can be dated against an unknown compaction time: never hold on it
        return dict(systemMessage=allowed + " (this hook's state could not be read)")
    if blocks is None or blocks >= BLOCK_CAP:
        return dict(systemMessage=allowed + (" (refusal count unknown)" if blocks is None else " (loop cap)"))
    saved = save_state(spath, st["since"], blocks + 1)
    if not saved and not new_turn:
        return dict(systemMessage=allowed + " (refusal count cannot be saved)")
    return dict(decision="block", reason="constraint-reread: the context was compacted at " + stamp(st["since"])
                + " and "
                + rpath + " holds no `" + ENTRY_FIELD + ":` entry dated after it. Standing constraints do not "
                "lapse with the context: re-read the record, append `" + ENTRY_FIELD + ": ` followed by the "
                "output of `date -u +%Y-%m-%dT%H:%M:%SZ`, then end the turn (refusal " + str(blocks + 1)
                + " of at most " + str(BLOCK_CAP) + ").")


def _emit_line(text):
    """Write one line to stdout; an output error is swallowed so the hook still exits 0."""
    try:
        print(text, flush=True)
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    if len(argv) > 1 and argv[1] == "--self-test":
        return _self_test()
    try:
        if _is_worker():
            return 0
        buf = getattr(sys.stdin, "buffer", None)
        payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        out = decide(payload, os.environ, datetime.datetime.now(_UTC))
        if out is not None:
            _emit_line(json.dumps(out))
    except Exception:
        pass  # fail open: any error exits 0 with no output
    return 0


def _self_test():
    import shutil
    import subprocess
    import unittest

    here = os.path.abspath(__file__)
    t0 = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=_UTC)

    def at(seconds):
        return t0 + datetime.timedelta(seconds=seconds)

    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.mkdtemp(prefix="constraint-reread-test-")
            self.rec = os.path.join(self.tmp, "record.md")
            self.env = dict(AIQT_CONSTRAINT_RECORD=self.rec, AIQT_HOOK_STATE_DIR=os.path.join(self.tmp, "st"))
            self.write("# Record\n- Constraint: propose, do not merge without approval\nConstraint: never touch prod\n")

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)

        def write(self, text, mode="w"):
            with open(self.rec, mode, encoding="utf-8") as fh:
                fh.write(text)

        def call(self, event, seconds, env=None, **extra):
            payload = dict(hook_event_name=event, session_id="sess-1")
            payload.update(extra)
            return decide(payload, self.env if env is None else env, at(seconds))

        def compact(self, seconds=0):
            return self.call("SessionStart", seconds, source="compact")

        def spath(self):
            return state_path(dict(session_id="sess-1"), self.env)

        def test_01_off_without_a_record(self):
            for value in (None, "", "rel/record.md"):
                env = dict(AIQT_HOOK_STATE_DIR=self.env["AIQT_HOOK_STATE_DIR"])
                if value is not None:
                    env["AIQT_CONSTRAINT_RECORD"] = value
                self.assertIsNone(self.call("SessionStart", 0, env=env, source="compact"))
                self.assertIsNone(self.call("Stop", 1, env=env))
            self.assertFalse(os.path.exists(self.env["AIQT_HOOK_STATE_DIR"]))

        def test_02_compaction_reminds_and_names_constraints(self):
            ctx = self.compact()["hookSpecificOutput"]
            self.assertEqual(ctx["hookEventName"], "SessionStart")
            self.assertIn("propose, do not merge without approval", ctx["additionalContext"])
            self.assertIn("never touch prod", ctx["additionalContext"])
            self.assertIn(stamp(t0), ctx["additionalContext"])

        def test_03_reminder_repeats_every_turn_until_the_entry(self):
            self.compact()
            for s in (10, 20, 30):
                out = self.call("UserPromptSubmit", s)
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
                self.assertIn("never touch prod", out["hookSpecificOutput"]["additionalContext"])
            self.write("Constraints-reread: " + stamp(at(35)) + "\n", "a")
            self.assertIsNone(self.call("UserPromptSubmit", 40))
            self.assertIsNone(self.call("Stop", 41))

        def test_04_stop_refuses_with_a_loop_cap(self):
            self.compact()
            out = self.call("Stop", 5)
            self.assertEqual(out["decision"], "block")
            n = 1
            for s in range(6, 20):
                out = self.call("Stop", s, stop_hook_active=True)
                if "decision" not in out:
                    break
                n += 1
            self.assertEqual(n, BLOCK_CAP)
            self.assertIn("loop cap", out["systemMessage"])
            self.assertIn("loop cap", self.call("Stop", 29)["systemMessage"])  # no field: not a new turn
            self.assertIn("(refusal 1 of", self.call("Stop", 30, stop_hook_active=False)["reason"])  # a new turn

        def test_23_the_cap_holds_without_stop_hook_active(self):
            # Round 20: with no stop_hook_active field in the payload the count was reset at every Stop, so the
            # cap was never reached. Each Stop below carries no such field.
            self.compact()
            outs = [self.call("Stop", s) for s in range(5, 5 + BLOCK_CAP + 3)]
            refusals = [o for o in outs if o.get("decision") == "block"]
            self.assertEqual(len(refusals), BLOCK_CAP)
            self.assertEqual(outs[:BLOCK_CAP], refusals)
            for o in outs[BLOCK_CAP:]:
                self.assertEqual(o, dict(systemMessage="constraint-reread: turn end allowed without a "
                                         "post-compaction re-read entry in " + self.rec + " (loop cap)"))
            self.assertEqual(load_state(self.spath())[2], BLOCK_CAP)
            self.assertIsNotNone(self.call("UserPromptSubmit", 20))  # a prompt the user submitted: a new turn
            self.assertEqual(load_state(self.spath())[2], 0)
            self.assertIn("(refusal 1 of", self.call("Stop", 21)["reason"])
            self.compact(30)  # a new compaction records a fresh count too
            self.assertIn("(refusal 1 of", self.call("Stop", 31)["reason"])

        def test_24_unknown_or_unsaveable_counts_allow_without_the_field(self):
            self.compact()
            with open(self.spath(), "w", encoding="ascii") as fh:
                fh.write("\x7bnot json")
            mtime = int(at(1).timestamp())
            os.utime(self.spath(), (mtime, mtime))
            self.assertIn("refusal count unknown", self.call("Stop", 5)["systemMessage"])
            self.assertIn("refusal count unknown", self.call("Stop", 6, stop_hook_active=True)["systemMessage"])
            # Round 21 (claude MINOR): the unknown count persists over Stops until a new-turn signal.
            for s in range(8, 11):
                self.assertIn("refusal count unknown", self.call("Stop", s).get("systemMessage"))
            self.assertEqual(load_state(self.spath())[0], "bad")
            self.assertIn("(refusal 1 of", self.call("Stop", 7, stop_hook_active=False)["reason"])
            self.assertEqual(load_state(self.spath())[2], 1)  # the refusal wrote a well-formed state
            d = os.path.dirname(self.spath())
            os.chmod(d, 0o500)
            try:
                if os.access(d, os.W_OK):
                    self.skipTest("SKIPPED, running with privileges that ignore directory modes")
                for s in range(8, 8 + BLOCK_CAP + 2):
                    self.assertIn("cannot be saved", self.call("Stop", s)["systemMessage"])
            finally:
                os.chmod(d, 0o700)

        def test_05_entry_must_postdate_the_compaction_and_not_be_future(self):
            self.write("Constraints-reread: " + stamp(at(-5)) + "\n", "a")
            self.compact()
            self.assertEqual(self.call("Stop", 10)["decision"], "block")
            self.write("Constraints-reread: " + stamp(at(10 + CLOCK_SKEW + 5)) + "\n", "a")
            self.assertEqual(self.call("Stop", 10)["decision"], "block")
            self.write("- Constraints-reread: " + stamp(at(9)).replace("-", "").replace(":", "") + "\n", "a")
            self.assertIsNone(self.call("Stop", 10))

        def test_06_a_later_compaction_needs_a_later_entry(self):
            self.compact(0)
            self.write("Constraints-reread: " + stamp(at(5)) + "\n", "a")
            self.assertIsNone(self.call("Stop", 6))
            self.compact(100)
            self.assertEqual(self.call("Stop", 101)["decision"], "block")

        def test_07_no_compaction_no_output(self):
            self.assertIsNone(self.call("SessionStart", 0, source="startup"))
            self.assertIsNone(self.call("UserPromptSubmit", 1))
            self.assertIsNone(self.call("Stop", 2))
            self.assertIsNone(self.call("Stop", 3, stop_hook_active=True))
            self.assertIsNone(self.call("PostToolUse", 4))

        def test_08_precompact_records_silently(self):
            self.assertIsNone(self.call("PreCompact", 0, trigger="auto"))
            self.assertEqual(self.call("Stop", 1)["decision"], "block")
            out = self.call("SessionStart", 2, source="resume")
            self.assertIn("never touch prod", out["hookSpecificOutput"]["additionalContext"])

        def test_09_unreadable_record_keeps_reminding(self):
            self.compact()
            os.unlink(self.rec)
            out = self.call("UserPromptSubmit", 5)
            self.assertIn("could not be read", out["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(self.call("Stop", 6)["decision"], "block")

        def test_10_bad_state_reads_as_a_compaction_at_its_mtime(self):
            self.compact()
            with open(self.spath(), "w", encoding="ascii") as fh:
                fh.write("\x7bnot json")
            mtime = int(at(50).timestamp())
            os.utime(self.spath(), (mtime, mtime))
            self.write("Constraints-reread: " + stamp(at(20)) + "\n", "a")
            self.assertIsNotNone(self.call("UserPromptSubmit", 60))
            self.write("Constraints-reread: " + stamp(at(55)) + "\n", "a")
            self.assertIsNone(self.call("UserPromptSubmit", 60))

        def test_11_no_state_location_reminds_once_then_forgets(self):
            env = dict(AIQT_CONSTRAINT_RECORD=self.rec)
            self.assertIsNotNone(self.call("SessionStart", 0, env=env, source="compact"))
            self.assertIsNone(self.call("UserPromptSubmit", 1, env=env))
            self.assertIsNone(self.call("Stop", 2, env=env))

        def test_12_unsaveable_count_allows_inside_the_loop(self):
            self.compact()
            d = os.path.dirname(self.spath())
            os.chmod(d, 0o500)
            try:
                if os.access(d, os.W_OK):
                    self.skipTest("SKIPPED, running with privileges that ignore directory modes")
                self.assertEqual(self.call("Stop", 5, stop_hook_active=False)["decision"], "block")
                self.assertIn("cannot be saved", self.call("Stop", 6, stop_hook_active=True)["systemMessage"])
                self.assertIn("cannot be saved", self.call("Stop", 7)["systemMessage"])
            finally:
                os.chmod(d, 0o700)

        def test_13_sessions_are_separate(self):
            self.compact()
            self.assertIsNone(decide(dict(hook_event_name="Stop", session_id="other"), self.env, at(5)))

        def test_14_legacy_spelling_and_field_grammar(self):
            env = dict(ORCH_CONSTRAINT_RECORD=self.rec, ORCH_HOOK_STATE_DIR=self.env["AIQT_HOOK_STATE_DIR"])
            self.assertIsNotNone(self.call("SessionStart", 0, env=env, source="compact"))
            for bad in ("constraints-reread: ", "Constraints-reread - ", "x Constraints-reread: "):
                self.write(bad + stamp(at(5)) + "\n", "a")
            self.write("Constraints-reread: 2026-13-01T00:00:00Z\n", "a")
            self.assertEqual(self.call("Stop", 10, env=env)["decision"], "block")

        def test_17_a_record_cut_at_the_limit_drops_the_cut_line(self):
            head = open(self.rec, "rb").read()
            line = ("Constraints-reread: " + stamp(at(1))).encode()
            pad = RECORD_MAX_BYTES - len(head) - len(line) - 1
            with open(self.rec, "wb") as fh:
                fh.write(head + b"#" * pad + b"\n" + line + b"NOT-A-TIMESTAMP\n")
            self.compact(0)
            self.assertEqual(self.call("Stop", 2)["decision"], "block")
            with open(self.rec, "wb") as fh:  # exactly at the limit and complete: the entry is read
                fh.write(head + b"#" * (pad - 1) + b"\n" + line + b"\n")
            self.assertIsNone(self.call("Stop", 3))

        def test_18_a_state_dir_under_a_file_is_no_state(self):
            blocker = os.path.join(self.tmp, "plain-file")
            open(blocker, "w").close()
            env = dict(self.env, AIQT_HOOK_STATE_DIR=blocker)
            self.assertIsNone(self.call("SessionStart", 0, env=env, source="startup"))
            self.assertIsNone(self.call("UserPromptSubmit", 1, env=env))
            self.assertIsNone(self.call("Stop", 2, env=env))

        def test_19_unexaminable_state_reminds_but_never_holds_the_stop(self):
            self.compact()
            d = os.path.dirname(self.spath())
            os.chmod(d, 0)
            try:
                if os.access(d, os.X_OK):
                    self.skipTest("SKIPPED, running with privileges that ignore directory modes")
                self.write("Constraints-reread: " + stamp(at(3)) + "\n", "a")
                ctx = self.call("UserPromptSubmit", 5)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("could not be examined", ctx)
                self.assertIn("can clear this reminder while the state location cannot be examined", ctx)
                self.assertIn("The turn end is not refused in this state.", ctx)
                self.assertNotIn("each turn end is refused", ctx)
                out = self.call("Stop", 6)
                self.assertNotIn("decision", out)
                self.assertIn("could not be read", out["systemMessage"])
            finally:
                os.chmod(d, 0o700)

        def test_20_a_non_regular_state_reads_as_a_compaction_at_its_mtime(self):
            os.makedirs(self.spath())
            mtime = int(at(50).timestamp())
            os.utime(self.spath(), (mtime, mtime))
            self.write("Constraints-reread: " + stamp(at(20)) + "\n", "a")
            ctx = self.call("UserPromptSubmit", 60)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(stamp(at(50)), ctx)
            self.write("Constraints-reread: " + stamp(at(55)) + "\n", "a")
            self.assertIsNone(self.call("UserPromptSubmit", 60))

        def test_21_an_oversized_state_reads_as_a_compaction_at_its_mtime(self):
            self.compact()
            with open(self.spath(), "rb") as fh:
                body = fh.read()
            with open(self.spath(), "wb") as fh:  # valid JSON, padded to the limit: still read
                fh.write(body + b" " * (STATE_MAX_BYTES - len(body)))
            self.assertEqual(load_state(self.spath())[0], "ok")
            with open(self.spath(), "ab") as fh:  # one byte more: never parsed from the cut prefix
                fh.write(b"CORRUPT")
            mtime = int(at(100).timestamp())
            os.utime(self.spath(), (mtime, mtime))
            self.assertEqual(load_state(self.spath())[:2], ("bad", at(100)))
            self.write("Constraints-reread: " + stamp(at(50)) + "\n", "a")
            self.assertIn("refusal count unknown", self.call("Stop", 110)["systemMessage"])
            self.assertEqual(self.call("Stop", 110, stop_hook_active=False)["decision"], "block")
            self.write("Constraints-reread: " + stamp(at(105)) + "\n", "a")
            self.assertIsNone(self.call("Stop", 110))

        def test_22_the_reminder_states_the_loop_cap(self):
            ctx = self.compact()["hookSpecificOutput"]["additionalContext"]
            self.assertIn("at most " + str(BLOCK_CAP) + " times in a row before the stop is allowed", ctx)
            self.assertNotIn("is held", ctx)
            doc = " ".join(__doc__.split())  # round 20: the docstring describes the cap as it works
            self.assertNotIn("Exit status: always 0", doc)
            self.assertNotIn("per continuous stop_hook_active run", doc)
            # Round 21 (claude MEDIUM): an explicit false resets the count, so the cap does not hold whatever the field.
            self.assertNotIn("does not rely on the platform's stop_hook_active field", doc)
            self.assertNotIn("absent, false or true", doc)
            self.assertIn("so the cap holds when the platform's stop_hook_active field is absent or true", doc)
            self.assertIn("the cap does not hold against a host that sends false on a Stop that does continue a "
                          "refusal", doc)
            # Round 21 (claude MINOR): an unknown count persists until a new-turn signal, and both sections say so.
            self.assertIn("An unknown count persists: a Stop does not repair the state", doc)
            self.assertIn("a state file that cannot be parsed (an unknown count) has the same effect", doc)

        def run_hook(self, payload, env):
            base = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"))
            base.update(env)
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=payload, capture_output=True,
                               env=base, timeout=30)
            return p.returncode, p.stdout, p.stderr

        def test_15_process_contract(self):
            start = json.dumps(dict(hook_event_name="SessionStart", source="compact", session_id="p")).encode()
            stop = json.dumps(dict(hook_event_name="Stop", session_id="p")).encode()
            rc, out, err = self.run_hook(start, self.env)
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))
            self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])
            rc, out, err = self.run_hook(stop, self.env)
            self.assertEqual((rc, err), (0, b""))
            self.assertEqual(json.loads(out)["decision"], "block")
            for junk in (b"", b"\x7b", b"[]", b"\xff\xfe", b"null"):
                self.assertEqual(self.run_hook(junk, self.env), (0, b"", b""))
            self.assertEqual(self.run_hook(stop, dict(self.env, AIQT_HOOKS_WORKER="1")), (0, b"", b""))

        def test_16_source_house_rules(self):
            with open(here, "rb") as fh:
                src = fh.read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
