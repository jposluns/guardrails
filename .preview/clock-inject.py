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
Run beside its sibling hooks, the self-test also checks that the code shared verbatim with them is identical.
Run alone (a single-hook install), those sibling-parity checks are SKIPPED, not passed, each naming the absent
sibling; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 an absent sibling FAILS them instead (for a repository gate). A
sibling that is present but unreadable fails them either way.
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

def _sibling_or_skip(name, env=None):
    """Self-test helper, kept identical across the three hooks: the path of sibling hook `name` beside this file.
    A genuinely absent sibling (os.lstat raises FileNotFoundError, nothing broader) SKIPS the calling test with a
    message naming it, so a single-hook install self-tests clean; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 the
    absence FAILS the test instead, so a repository gate never skips parity silently. Any other error (an
    unreadable directory, say) propagates, and a sibling that exists but cannot be loaded fails when it is read,
    so only a genuine absence ever skips."""
    import unittest
    env = os.environ if env is None else env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    try:
        os.lstat(path)
    except FileNotFoundError:
        if env.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
            raise AssertionError(f"sibling hook {name} is absent ({path}) and AIQT_HOOKS_REQUIRE_SIBLINGS=1 "
                                 "requires it") from None
        raise unittest.SkipTest(f"sibling hook {name} is absent (a standalone install); set "
                                "AIQT_HOOKS_REQUIRE_SIBLINGS=1 to require it") from None
    return path


def _wall_clock_asserts(source, exempt=()):
    """Self-test helper, kept identical across the three hooks: [(function, line)] of every assertion in
    `source` whose operand is a TIME figure, so no test's verdict rests on a wall-clock bound, which depends on
    the host's speed. A time figure is a clock reading (time.time, perf_counter, monotonic, process_time,
    thread_time, clock_gettime, or an _ns variant; datetime.now, utcnow, or today), a result of a timing
    harness (run_timed, growth_in_child), a name assigned from one (a for target or unpacking included), or
    arithmetic, a comparison, a subscript, an attribute, min, max, abs, sum, int, float, round, or a method
    call on one; a quotient of two time figures is a dimensionless ratio and may be bounded. A tuple or list
    literal assigned to a tuple or list target is matched element by element, so only the names that receive
    a time figure are tainted, and a shape it cannot match (a starred element, a length mismatch) taints
    nothing, the not-flagging direction. A clock read through an alias
    counts too: a module alias (`import time as t`, `import datetime as d`), an imported one (`from time import
    X as Y`, or `*`; `from datetime import datetime as Y`), and an assigned one (a plain `name = time.X` or
    `name = X` of a clock or alias), each bound at module level or within the function. An assertion's operands
    are its leading positional arguments and every keyword argument but msg. Every assertion in the source is
    scanned, under its enclosing test_ function (else its innermost function); `exempt` names the hang-guard
    tests, whose bound on elapsed time is their point. Residual (disclosed): the taint is by name within one
    test_ function and its nested functions, so a time figure passed through a container mutation, a global, a
    call to another helper, or a harness this list does not name escapes the scan; so does one routed through
    an expression form the scan does not follow: an assignment expression (walrus) in an asserted operand, a
    dict literal, a container built by a comprehension or filled by a store into a subscript, or any other
    routing not listed above; so does the time figure in a starred or mismatched unpacking; a clock this list does not
    name (os.times, date.today, time.localtime or gmtime, a third-party clock, a file's mtime) escapes too, as
    does an alias bound any other way (an attribute, a tuple target, getattr, a module or class assigned to a
    name); a subprocess timeout is not an assertion and is not scanned."""
    import ast
    clocks = {"perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns", "process_time", "process_time_ns",
              "thread_time", "thread_time_ns", "clock_gettime", "clock_gettime_ns", "run_timed", "growth_in_child"}
    stdclocks = {"time", "time_ns", "perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns",
                 "process_time", "process_time_ns", "thread_time", "thread_time_ns", "clock_gettime",
                 "clock_gettime_ns"}
    dtclocks = ("now", "utcnow", "today")
    single = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}

    def dtclass(node, al):  # a reference to the datetime class: a class alias, or <datetime module>.datetime
        if isinstance(node, ast.Name):
            return node.id in al["dtclass"]
        return isinstance(node, ast.Attribute) and node.attr == "datetime" and \
            isinstance(node.value, ast.Name) and node.value.id in al["datetime"]

    def timed(node, names, al):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id in clocks or f.id in al["clock"]:
                    return True
                return f.id in ("min", "max", "abs", "sum", "int", "float", "round") and any(
                    timed(a, names, al) for a in node.args)
            if isinstance(f, ast.Attribute):
                if f.attr in ("time", "time_ns"):
                    return isinstance(f.value, ast.Name) and f.value.id in al["time"]
                if f.attr in dtclocks and dtclass(f.value, al):
                    return True
                return f.attr in clocks or timed(f.value, names, al)
            return False
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.BinOp):
            left, right = timed(node.left, names, al), timed(node.right, names, al)
            return left != right if isinstance(node.op, ast.Div) else left or right
        if isinstance(node, ast.Compare):
            return any(timed(x, names, al) for x in [node.left] + node.comparators)
        if isinstance(node, ast.BoolOp):
            return any(timed(x, names, al) for x in node.values)
        if isinstance(node, ast.UnaryOp):
            return timed(node.operand, names, al)
        if isinstance(node, (ast.Subscript, ast.Starred, ast.Attribute)):
            return timed(node.value, names, al)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(timed(e, names, al) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return timed(node.body, names, al) or timed(node.orelse, names, al)
        return False

    def targets(node):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                yield from targets(e)
        elif isinstance(node, ast.Starred):
            yield from targets(node.value)

    def split(target, value):  # an assignment's (target, value) pairs, a tuple or list literal matched in step
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts) and not any(
                    isinstance(e, ast.Starred) for e in target.elts + value.elts):
                for t, v in zip(target.elts, value.elts):
                    yield from split(t, v)
            return  # otherwise a shape it cannot match: nothing is tainted, the not-flagging direction
        yield target, value

    def aliases(node, al):  # the (kind, name) aliases node binds; kind: time, datetime (modules), dtclass, clock
        if isinstance(node, ast.Import):
            return [(a.name, a.asname or a.name) for a in node.names if a.name in ("time", "datetime")]
        if isinstance(node, ast.ImportFrom) and node.module == "time" and not node.level:
            bound = []
            for a in node.names:
                if a.name == "*":
                    bound += [("clock", c) for c in stdclocks]
                elif a.name in stdclocks:
                    bound.append(("clock", a.asname or a.name))
            return bound
        if isinstance(node, ast.ImportFrom) and node.module == "datetime" and not node.level:
            return [("dtclass", a.asname or "datetime") for a in node.names if a.name in ("datetime", "*")]
        if isinstance(node, ast.Assign) and (
                isinstance(node.value, ast.Name) and (node.value.id in clocks or node.value.id in al["clock"]) or
                isinstance(node.value, ast.Attribute) and node.value.attr in stdclocks and
                isinstance(node.value.value, ast.Name) and node.value.value.id in al["time"] or
                isinstance(node.value, ast.Attribute) and node.value.attr in dtclocks and
                dtclass(node.value.value, al)):
            return [("clock", t.id) for t in node.targets if isinstance(t, ast.Name)]
        return []

    tree, parent, cache = ast.parse(source), {}, {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def scope(node):  # the enclosing test_ function, else the innermost function (None at module level)
        inner, up = None, parent.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if up.name.startswith("test_"):
                    return up
                inner = inner or up
            up = parent.get(up)
        return inner

    def bind(nodes, al):  # add the aliases nodes bind to al, to a fixed point; True if any was new
        grew, changed = False, True
        while changed:
            changed = False
            for node in nodes:
                for kind, name in aliases(node, al):
                    if name not in al[kind]:
                        al[kind].add(name)
                        grew = changed = True
        return grew

    binders = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign))]
    top = {"time": {"time"}, "datetime": {"datetime"}, "dtclass": {"datetime"}, "clock": set()}
    bind([node for node in binders if scope(node) is None], top)

    def tainted(fn):  # (names, al): the time figures and aliases fn (nested functions included) binds
        if fn not in cache:
            names, al = set(), {kind: set(bound) for kind, bound in top.items()}
            nodes = list(ast.walk(fn))
            changed = True
            while changed:
                changed = bind(nodes, al)
                for node in nodes:
                    if isinstance(node, ast.Assign):
                        pairs = [pair for t in node.targets for pair in split(t, node.value)]
                    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)) and \
                            node.value is not None:
                        pairs = [(node.target, node.value)]
                    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                        pairs = [(node.target, node.iter)]
                    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                        pairs = [(node.optional_vars, node.context_expr)]
                    else:
                        continue
                    for target, value in pairs:
                        new = set(targets(target)) - names if timed(value, names, al) else set()
                        if new:
                            names |= new
                            changed = True
            cache[fn] = names, al
        return cache[fn]

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            operands = [node.test]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr.startswith("assert"):
            operands = node.args[:1] if node.func.attr in single else node.args[:2]
            operands = operands + [k.value for k in node.keywords if k.arg != "msg"]
        else:
            continue
        fn = scope(node)
        name = fn.name if fn is not None else "<module>"
        names, al = tainted(fn) if fn is not None else (set(), top)
        if name not in exempt and any(timed(x, names, al) for x in operands):
            found.append((name, node.lineno))
    return sorted(found, key=lambda item: item[1])


def _wall_clock_alias_fixtures():
    """Self-test data, kept identical across the three hooks: (bad, flagged, good) for _wall_clock_asserts.
    `bad` bounds a time figure read through each alias form, through keyword operands, and from each added
    clock (datetime.now, utcnow, today; clock_gettime), and every function it names in `flagged` must be
    flagged; `good` uses the same alias forms and clocks only for a hang-guard timeout, an assertion on a count,
    a msg keyword, a ratio, a datetime constructor, or a name that is an alias only in another function, and
    nothing in it may be flagged."""
    bad = ("import time as tm\nfrom time import perf_counter as clock\ntick = tm.monotonic_ns\n"
           "import datetime as dt\n"
           "def test_i(self):\n    t = clock()\n    self.assertLess(clock() - t, 0.5)\n"
           "def test_j(self):\n    my_time = time.time\n    t0 = my_time()\n    self.assertLess(my_time() - t0, 1)\n"
           "def test_k(self):\n    t0 = tm.time()\n    self.assertLessEqual(tm.time() - t0, 1)\n"
           "def test_l(self):\n    self.assertLess(tick() - start, 10)\n"
           "def test_m(self):\n    from time import process_time as cpu\n    c0 = cpu()\n"
           "    assert cpu() - c0 < 2\n"
           "def test_n(self):\n    c1 = clock\n    c2 = c1\n    self.assertGreater(1.0, c2() - base)\n"
           "def test_o(self):\n    from time import time\n    self.assertTrue(time() - t0 < 1)\n"
           "def test_p(self):\n    from time import *\n    self.assertLess(time_ns() - t0, 5)\n"
           "def test_q(self):\n    t = time.monotonic()\n    self.assertLess(a=time.monotonic()-t, b=0.5)\n"
           "def test_r(self):\n    self.assertTrue(expr=time.perf_counter() - t0 < 1, msg='slow')\n"
           "def test_s(self):\n    now = tm.perf_counter\n    elapsed = now() - t0\n"
           "    self.assertLessEqual(first=elapsed, second=2)\n"
           "def test_t(self):\n    pc = perf_counter\n    self.assertLess(pc() - t, 1)\n"
           "def test_ba(self):\n    t0 = datetime.datetime.now()\n"
           "    self.assertLess((datetime.datetime.now() - t0).total_seconds(), 2.0)\n"
           "def test_bb(self):\n    from datetime import datetime as DT\n    t0 = DT.utcnow()\n"
           "    self.assertLess((DT.utcnow() - t0).total_seconds(), 2)\n"
           "def test_bc(self):\n    stamp = dt.datetime.now\n    t0 = stamp()\n"
           "    self.assertLess((stamp() - t0).seconds, 2)\n"
           "def test_bd(self):\n    t0 = time.clock_gettime(time.CLOCK_MONOTONIC)\n"
           "    self.assertLess(time.clock_gettime(time.CLOCK_MONOTONIC) - t0, 1)\n"
           "def test_be(self):\n    from time import clock_gettime_ns as cg\n    t0 = cg(1)\n"
           "    assert cg(1) - t0 < 10\n"
           "def test_bf(self):\n    from datetime import *\n    self.assertLess((datetime.today() - t0).seconds, 5)\n"
           "def test_bj(self):\n    elapsed, n = clock() - t0, 3\n    self.assertLess(elapsed, 1)\n"
           "def test_bk(self):\n    t0 = time.monotonic()\n    e = int((time.monotonic() - t0) * 1000)\n"
           "    self.assertLess(e, 500)\n")
    flagged = ["test_i", "test_j", "test_k", "test_l", "test_m", "test_n", "test_o", "test_p", "test_q", "test_r",
               "test_s", "test_t", "test_ba", "test_bb", "test_bc", "test_bd", "test_be", "test_bf", "test_bj",
               "test_bk"]
    good = ("import time as tm\nfrom time import perf_counter as clock\ntick = tm.monotonic\n"
            "import datetime as dt\n"
            "def test_u(self):\n    deadline = clock() + HANG_TIMEOUT\n    out = run(timeout=deadline - clock())\n"
            "    proc.wait(timeout=tick() + 1)\n    self.assertEqual(len(out), 3)\n"
            "def test_v(self):\n    my_time = tm.time\n    proc.wait(timeout=my_time() + 30)\n"
            "    self.assertEqual(proc.returncode, 0, msg=my_time())\n"
            "def test_w(self):\n    from time import process_time as cpu\n    limit = cpu() + 5\n"
            "    calls = count_calls(limit)\n    self.assertEqual(first=calls, second=2, msg=cpu() - limit)\n"
            "def test_x(self):\n    from time import time\n    subprocess.run(cmd, timeout=time() + 5)\n"
            "    self.assertEqual(n_lines, 4)\n"
            "def test_y(self):\n    my_time = len\n    self.assertEqual(my_time([1, 2]), 2)\n"
            "def test_z(self):\n    a, b = clock(), clock()\n    self.assertLess(b / max(a, 1e-3), LIMIT)\n"
            "def test_bg(self):\n    stamp = dt.datetime.now(dt.timezone.utc)\n"
            "    self.assertEqual(len(render(stamp)), 17)\n    self.assertEqual(dt.time(12, 0).hour, 12)\n"
            "    self.assertEqual(datetime.date(2026, 9, 24).day, 24)\n"
            "def test_bh(self):\n    t0 = time.clock_gettime(time.CLOCK_MONOTONIC)\n"
            "    lines = run(timeout=HANG_TIMEOUT - (time.clock_gettime(time.CLOCK_MONOTONIC) - t0))\n"
            "    self.assertEqual(lines.count, 2)\n"
            "def test_bi(self):\n    started, count = clock(), 3\n    self.assertEqual(count, 3)\n"
            "    [(t1, n), m] = [(tick(), 4), 5]\n    self.assertEqual(n + m, 9)\n"
            "    first, *rest = clock(), 1, 2\n    self.assertEqual(rest, [1, 2])\n")
    return bad, flagged, good


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


def _emit_line(text, *stream):
    """Write one line to `stream` (default stdout) and flush it. An output error fails OPEN (round 24): it is
    swallowed, and the stream's descriptor is pointed at /dev/null so the interpreter's exit flush cannot fail
    either, so the hook still exits 0. If that rescue fails too, the process ends at once with os._exit(0)
    (no flush is retried), since any later write or exit flush could fail the hook."""
    # a line meant for another stream (stderr) is written there or dropped, NEVER sent to stdout, the hook's
    # protocol channel: sys.stderr is None when descriptor 2 was closed at startup, and a None default once
    # read that as stdout
    s = stream[0] if stream else sys.stdout
    if s is None:
        return False
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
    # a FIFO test's child timeout is a hang guard only (a blocking open never returns), far above the child's own
    # run (well under a second), so the verdict never depends on the host's speed
    HANG_TIMEOUT = 120
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
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                               timeout=HANG_TIMEOUT)
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
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                               timeout=HANG_TIMEOUT)
            self.assertEqual(r.stdout.strip(), "None")

        def test_r13_shared_lease_code_identical_to_stop_hook(self):
            import importlib.util
            import inspect
            sib = _sibling_or_skip("stamp-truth-stop.py")
            spec = importlib.util.spec_from_file_location("sts_sibling", sib)
            mod = importlib.util.module_from_spec(spec)
            # the sibling is loaded with no bytecode written, so no __pycache__ is left beside the hooks
            old_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True
            try:
                spec.loader.exec_module(mod)
            finally:
                sys.dont_write_bytecode = old_dwb
            for name in ("read_regular", "lease_file", "_utc_field", "transcript_start", "lease_start", "_is_worker",
                     "_cfg", "_sibling_or_skip", "_wall_clock_asserts", "_wall_clock_alias_fixtures"):
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

        def test_closed_stderr_line_never_reaches_stdout(self):
            # the worker-skip line is for stderr only: with descriptor 2 closed (sys.stderr is None) it is dropped,
            # never redirected to stdout, the hook's protocol channel; the open-stderr control shows it is emitted
            env = {k: v for k, v in os.environ.items() if k not in ("ORCH_WORKER", "ORCH_VERIFY_OWNER")}
            env["AIQT_HOOKS_WORKER"] = "1"
            hook = [sys.executable, "-I", "-S", "-B", os.path.abspath(__file__)]
            close2 = "import os, sys; os.close(2); os.execv(sys.argv[1], sys.argv[1:])"
            for closed in (False, True):
                argv = [sys.executable, "-I", "-S", "-B", "-c", close2] + hook if closed else hook
                p = subprocess.run(argv, input="{}", capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual((p.returncode, p.stdout), (0, ""), (closed, p.stderr))
                self.assertEqual("skipped, worker marker present" in p.stderr, not closed, (closed, p.stderr))

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

        # -- no wall-clock verdict (a 2.0 s ceiling failed at 2.53 s on a slower CI runner) --
        HANG_GUARD_TESTS = ()

        def test_no_wall_clock_verdict(self):
            """Residual (disclosed): the scan covers direct calls, imported aliases, and assigned aliases within a
            function, not values passed between functions, so a time figure handed to a helper that asserts on it
            is not flagged. Nor is a reading from a clock the scan does not name (os.times, date.today,
            time.localtime or gmtime, a third-party clock, a file's mtime), nor a time figure routed through an
            expression form the scan does not follow: an assignment expression (walrus), a dict literal, a
            container built by a comprehension or filled by a store into a subscript, a starred or mismatched
            unpacking, or any other routing; see _wall_clock_asserts."""
            # every assertion of this file is scanned (see _wall_clock_asserts): none bounds a time figure, a
            # ratio of two time figures excepted, outside the hang-guard tests named in HANG_GUARD_TESTS
            with open(os.path.abspath(__file__), encoding="utf-8") as f:
                src = f.read()
            self.assertEqual(_wall_clock_asserts(src, self.HANG_GUARD_TESTS), [])
            flagged = [name for name, _line in _wall_clock_asserts(src)]
            for name in self.HANG_GUARD_TESTS:  # an exemption names a real hang guard the scan would flag
                self.assertIn(name, flagged)
            # the forms a substring check missed are caught: a harness result, an elapsed name, a bare assert, a
            # for target, a time.time difference, and a comparison inside assertTrue
            bad = ("def test_a(self):\n    small, large = run_timed(code, HANG_TIMEOUT)\n"
                   "    self.assertLess(large, 2.0)\n"
                   "def test_b(self):\n    started = time.monotonic()\n    run()\n"
                   "    elapsed = time.monotonic() - started\n    self.assertLess(elapsed, 0.5)\n"
                   "def test_c(self):\n    t0 = time.perf_counter()\n    assert time.perf_counter() - t0 < 1\n"
                   "def test_d(self):\n    for name, (s, big) in run_timed(c, 9).items():\n"
                   "        self.assertTrue(big < 3, name)\n"
                   "def test_e(self):\n    t = time.time()\n    self.assertLessEqual(time.time() - t, 1)\n"
                   "def test_f(self):\n    def inner():\n        self.assertLess(growth_in_child(src, 9)[1], 1)\n")
            self.assertEqual([name for name, _line in _wall_clock_asserts(bad)],
                             ["test_a", "test_b", "test_c", "test_d", "test_e", "test_f"])
            # a ratio of two time figures, a time figure in the message only, and an exempt hang guard pass
            good = ("def test_g(self):\n    small, large = run_timed(code, HANG_TIMEOUT)\n"
                    "    self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))\n"
                    "    self.assertTrue(large / small < 2, (small, large))\n"
                    "def test_h(self):\n    t0 = time.monotonic()\n    self.assertLess(time.monotonic() - t0, 30.0)\n")
            self.assertEqual(_wall_clock_asserts(good, ("test_h",)), [])
            self.assertEqual(_wall_clock_asserts(good), [("test_h", 7)])
            # a clock read through an alias (import-as, from-import, an assigned name) or a keyword operand is
            # caught; the same aliases used for a hang-guard timeout, a count, or a msg keyword are not
            bad, want, good = _wall_clock_alias_fixtures()
            self.assertEqual([name for name, _line in _wall_clock_asserts(bad)], want)
            self.assertEqual(_wall_clock_asserts(good), [])

        # -- sibling parity on a single-hook install --
        PARITY_TESTS = ("test_r13_shared_lease_code_identical_to_stop_hook",)
        PARITY_SIBLINGS = ("stamp-truth-stop.py",)

        def _parity_in_copy(self, siblings, value, dangling=False):
            """Copy this file (and `siblings`, found beside it) into a fresh directory, run ONLY the copy's
            sibling-parity tests in a child interpreter with AIQT_HOOKS_REQUIRE_SIBLINGS set to `value` (None:
            unset, whatever the caller has), and return ([rc, run, skipped, failures, errors], child stderr).
            With `dangling`, each sibling is a symlink to a missing target: it EXISTS but cannot be read."""
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            d = tempfile.mkdtemp(prefix="sib.", dir=base)
            try:
                me = os.path.join(d, os.path.basename(os.path.abspath(__file__)))
                shutil.copyfile(os.path.abspath(__file__), me)
                for sib in siblings:
                    shutil.copyfile(_sibling_or_skip(sib), os.path.join(d, sib))
                if dangling:
                    for sib in self.PARITY_SIBLINGS:
                        os.symlink(os.path.join(d, "no-such-target"), os.path.join(d, sib))
                env = {k: v for k, v in os.environ.items() if k != "AIQT_HOOKS_REQUIRE_SIBLINGS"}
                if value is not None:
                    env["AIQT_HOOKS_REQUIRE_SIBLINGS"] = value
                code = ("import importlib.util as u, json, unittest\n"
                        "s = u.spec_from_file_location('m', %r)\n"
                        "m = u.module_from_spec(s)\n"
                        "s.loader.exec_module(m)\n"
                        "names = %r\n"
                        "unittest.TestLoader.loadTestsFromTestCase = lambda self, tc: unittest.TestSuite("
                        "tc(n) for n in names)\n"
                        "box, run = [], unittest.TextTestRunner.run\n"
                        "unittest.TextTestRunner.run = lambda self, t: box.append(run(self, t)) or box[-1]\n"
                        "rc = m._self_test()\n"
                        "r = box[0]\n"
                        "print(json.dumps([rc, r.testsRun, len(r.skipped), len(r.failures), len(r.errors)]))\n"
                        ) % (me, self.PARITY_TESTS)
                p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], env=env, capture_output=True,
                                   text=True, timeout=120)
                self.assertTrue(p.stdout.strip(), p.stderr)
                return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr
            finally:
                shutil.rmtree(d, ignore_errors=True)

        def test_sibling_parity_skips_alone_and_fails_when_required(self):
            # a single-hook install: each sibling-parity test is SKIPPED (not passed) with a message naming the
            # absent sibling, unless AIQT_HOOKS_REQUIRE_SIBLINGS=1, when the same absence FAILS it
            n = len(self.PARITY_TESTS)
            for value in (None, "0", ""):
                got, err = self._parity_in_copy((), value)
                self.assertEqual(got, [0, n, n, 0, 0], (value, err))
                for sib in self.PARITY_SIBLINGS:
                    self.assertIn(f"sibling hook {sib} is absent (a standalone install)", err)
            got, err = self._parity_in_copy((), "1")
            self.assertEqual(got, [1, n, 0, n, 0], err)
            self.assertIn("AIQT_HOOKS_REQUIRE_SIBLINGS=1 requires it", err)
            # a sibling that EXISTS but cannot be read fails (never skips), with or without the variable
            for value in (None, "1"):
                got, err = self._parity_in_copy((), value, dangling=True)
                self.assertEqual((got[0], got[1], got[2], got[3] + got[4]), (1, n, 0, n), (value, err))

        def test_sibling_parity_runs_and_passes_with_siblings_present(self):
            # with every sibling beside the copy the parity tests RUN and pass, whatever the variable says
            n = len(self.PARITY_TESTS)
            for value in (None, "1"):
                got, err = self._parity_in_copy(self.PARITY_SIBLINGS, value)
                self.assertEqual(got, [0, n, 0, 0, 0], (value, err))

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromTestCase(T))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
