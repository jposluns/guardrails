#!/usr/bin/env python3
"""PostToolUse and PostToolUseFailure hook (all tools): inject the real clock into the assistant's context.

Input bound: at most 8 MiB plus one byte of stdin is read; input over 8 MiB is discarded unparsed and fails
open like malformed input (the clock is still emitted, with hookEventName PostToolUse, see Coverage).

Motivated by an assistant composing console timestamps and "Session elapsed HH:MM" footers from its own
sense of time instead of reading the clock, drifting up to 4h40m ahead (timestamp-from-clock;
claims-rest-on-observation). After a tool call that ran, it emits a short additionalContext of two lines from
ONE reading of the process clock, for example:

    CLOCK (read by hook, authoritative): 2026-09-23 13:45:02 EDT | 2026-09-23T17:45:02Z | session elapsed 03:27
    CONSOLE PREFIX (copy exactly): [2026-09-23 13:45 EDT]

so a fresh authoritative clock reading is the most recent time value in context.
Transcript fallback is labeled "transcript elapsed", not a lease-relative session duration.

Zone. Both lines use ZONE (America/Toronto) from the system zone database whatever the process zone (TZ), so
the CLOCK line's abbreviation is EST or EDT as valid at that instant, never a fixed offset, and the two lines
always name the same zone. Only when zoneinfo or that zone database entry is missing or cannot be loaded or
used (a corrupt, truncated or unreadable zone file can raise any error, not only ZoneInfoNotFoundError) does
the CLOCK line fall back to the process zone; the CONSOLE PREFIX line is then omitted and the CLOCK line is
emitted alone, so a damaged zone database never drops the clock line.

Console prefix. The CLOCK line always comes first. The CONSOLE PREFIX line is the console stamp that
lab_infra's tools/orch-hooks/require-console-timestamp.py accepts, ready to copy: the same instant as the
CLOCK line, in ZONE, FLOORED to the minute (the seconds are dropped, never rounded up, since that hook refuses
a stamp later than the entry's own minute), labeled `EDT` when the zone observes daylight saving time at that
instant and `EST` otherwise (that hook's zone_label). Residual: the prefix is the minute of the tool call, so
a message composed later passes that hook only within its 10-minute window.

Coverage. Registered under PostToolUse it fires after a tool call that SUCCEEDED; registered under
PostToolUseFailure it fires after a tool call that started and FAILED. The emitted hookEventName is the
incoming payload's hook_event_name when that is PostToolUseFailure, otherwise PostToolUse, so it matches the
event as the hooks contract requires whenever the payload was parsed. Input that was not parsed (over 8 MiB,
or not a JSON object) carries no event name it can read, so it is answered as PostToolUse, which does not
match a PostToolUseFailure call (test_oversized_input_answers_as_post_tool_use). Both registrations are
needed for both outcomes. Neither event fires for a tool call rejected before execution (an unknown tool, input failing validation, or a permission denial), so such
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
unreadable, not regular, or the start lies in the future, the elapsed segment is omitted. Local time is ZONE
(see Zone above), else the process zone.

Contract: context = hookSpecificOutput {hookEventName, additionalContext} JSON on stdout, exit 0. This hook
NEVER fails the tool: any error at all exits 0, including an argument, stdin, or JSON error before the
payload is evaluated. The one exception to exit 0 is an interpreter older than Python 3.14 that can start the
hook: the guard at the top of this file reads no input, writes one line beginning
`error: clock-inject.py requires Python 3.14 or newer` to stderr (a best-effort write: the exit does
not depend on it) and exits 2. Under PostToolUse and
PostToolUseFailure the tool call has already run, so that exit blocks nothing: the line reaches the assistant
and no clock line is added. These exits hold while the hook's output (its diagnostic on stderr, and what it
prints on stdout) can be written and flushed. A failing output stream can change the exit status and can lose
output, a decision included; a separate fix in progress addresses this. An older interpreter that cannot start
the hook never reaches the guard and fails with Python's own error first; .preview/README.md (Installing a
hook, step 4) describes those cases. The payload is read as BYTES and parsed by json.loads, so its decoding
does not depend on the process locale. Unparseable hook input still emits the clock line under PostToolUse (the clock does not
depend on the payload). An error writing the output (a closed or full stdout) is swallowed and the hook
still exits 0; if the stream cannot even be pointed at /dev/null, the hook ends at once with
os._exit(0), so no exit-time flush can fail it. Kill-switch: a subordinate worker process, detected as env
AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted, see _is_worker), exits 0 with no stdout, so it never
distorts worker output; it writes one warning line to stderr, which on exit 0 reaches only the
host's debug log.
In-session subagents are deliberately NOT skipped (there is no agent_id check): they benefit from the true
time too, and this hook only adds context, it never blocks.

RESIDUAL COVERAGE. This INFORMS; it enforces nothing. The model can
still ignore the line or mis-copy it; the companion Stop hook (stamp-truth-stop.py) is the check. The line
reflects the host clock and the host zone database, so a wrong host clock (no NTP) or stale zone rules are
reproduced faithfully; a host without a usable America/Toronto entry shows the process zone instead, with no
CONSOLE PREFIX line. Elapsed is only as right
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
sibling that is present but unreadable fails them either way. The check that the CONSOLE PREFIX line is
accepted by lab_infra's require-console-timestamp.py (found at ../../tools/orch-hooks/ from this file) is
SKIPPED when that file is absent; only env AIQT_HOOKS_REQUIRE_CONSOLE_HOOK=1, a separate opt-in for a lab_infra
gate, makes its absence FAIL. AIQT_HOOKS_REQUIRE_SIBLINGS does not affect it.
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: clock-inject.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import json
import os
import re
import stat

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


# The zone of both lines: ZONE in lab_infra's tools/orch-hooks/require-console-timestamp.py.
ZONE = "America/Toronto"
PREFIX_LABEL = "CONSOLE PREFIX (copy exactly): "
# Self-test only: set to 1 by a lab_infra gate to make an absent require-console-timestamp.py FAIL, not skip.
CONSOLE_HOOK_REQUIRE_ENV = "AIQT_HOOKS_REQUIRE_CONSOLE_HOOK"


def _console_zone():
    """The ZoneInfo for ZONE, or None when zoneinfo or the zone data is unavailable."""
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(ZONE)
    except Exception:
        return None


def zone_time(now_utc):
    """now_utc in ZONE from the system zone database, or None when that entry is missing or cannot be loaded
    or used (a corrupt, truncated or unreadable zone file can raise any error, struct.error for a half-truncated
    one, not only ZoneInfoNotFoundError). Every zone call the two lines make is tried here first."""
    zone = _console_zone()
    if zone is None:
        return None
    try:
        local = now_utc.astimezone(zone)
        local.tzname(), local.utcoffset(), local.dst()
        return local
    except Exception:
        return None


def local_time(now_utc):
    """now_utc in ZONE; the process zone when ZONE cannot be loaded or used, so a damaged zone database never
    drops the clock line."""
    local = zone_time(now_utc)
    return now_utc.astimezone() if local is None else local


_UNSET = object()  # "not computed yet" for the zoned argument (None means the zone cannot be used)


def clock_line(now_utc, start=None, source="session", zoned=_UNSET):
    """The CLOCK line. `zoned`, when given, is zone_time(now_utc) already computed (None: the process zone)."""
    if zoned is _UNSET:
        local = local_time(now_utc)
    else:
        local = now_utc.astimezone() if zoned is None else zoned
    line = (f"CLOCK (read by hook, authoritative): {local.strftime('%Y-%m-%d %H:%M:%S')} {local.tzname()} | "
            f"{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if start is not None:
        el = fmt_elapsed(now_utc - start)
        if el is not None:
            line += f" | {source} elapsed {el}"
    return line


def console_prefix(now_utc, zoned=_UNSET):
    """The console stamp for the aware instant now_utc, for example [2026-11-21 18:33 EST]: ZONE's local date
    and time FLOORED to the minute (strftime drops the seconds; nothing is rounded), then `EDT` when the zone
    observes daylight saving time at that instant, else `EST` (require-console-timestamp.py's zone_label and
    stamp). None when the zone cannot be loaded or used. `zoned`, when given, is zone_time(now_utc) already
    computed."""
    local = zone_time(now_utc) if zoned is _UNSET else zoned
    if local is None:
        return None
    return "[%s %s]" % (local.strftime("%Y-%m-%d %H:%M"), "EDT" if local.dst() else "EST")


def clock_context(now_utc, start=None, source="session"):
    """The additionalContext: the CLOCK line, then the CONSOLE PREFIX line for the same instant (omitted when
    the zone cannot be loaded). The zone is loaded and used ONCE per context, and both lines are built from
    that one result, so they can never name different zones (a zone file replaced between two loads once
    could give the CLOCK line the process zone and the prefix Toronto)."""
    zoned = zone_time(now_utc)
    text = clock_line(now_utc, start, source, zoned)
    prefix = console_prefix(now_utc, zoned)
    return text if prefix is None else text + "\n" + PREFIX_LABEL + prefix


def _emit_line(text, *stream):
    """Write one line to `stream` (default stdout) and flush it. An output error fails OPEN: it is
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



STDIN_MAX_BYTES = 8 << 20


def _read_payload():
    """Read at most 8 MiB plus one overflow byte; malformed/oversized input fails open."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read(STDIN_MAX_BYTES + 1)
    if len(raw) > STDIN_MAX_BYTES:
        raise ValueError("hook input exceeds 8 MiB")
    return json.loads(raw)


def main(argv):
    try:
        self_test = len(argv) > 1 and argv[1] == "--self-test"
    except Exception:
        return 0  # an unusable argv fails open: exit 0 at once, evaluating nothing
    if self_test:
        return _self_test()
    try:
        if _is_worker():
            # the skip is not silent: one line on stderr only, so worker output is never distorted
            _emit_line("clock-inject: skipped, worker marker present (AIQT_HOOKS_WORKER=1 or a legacy spelling)",
                       sys.stderr)
            return 0
        try:
            # Oversized input follows the same fail-open path as malformed JSON.
            payload = _read_payload()
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        event = payload.get("hook_event_name")
        event = event if event in _EVENTS else "PostToolUse"
        tp = payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None
        now = datetime.datetime.now(datetime.timezone.utc)
        path = lease_file()
        start = lease_start(path)
        source = "session"
        if start is None:
            start, source = lease_start(path, tp), "transcript"
        _emit(clock_context(now, start, source), event)
    except Exception:
        pass  # never fail the tool
    return 0


def _self_test():
    import io
    import shutil
    import struct
    import subprocess
    import tempfile
    import time
    import unittest
    import unittest.mock
    import zoneinfo

    utc = datetime.timezone.utc
    # a FIFO test's child timeout is a hang guard only (a blocking open never returns), far above the child's own
    # run (well under a second), so the verdict never depends on the host's speed
    HANG_TIMEOUT = 120
    line_re = re.compile(r"^CLOCK \(read by hook, authoritative\): \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \S+ \| "
                         r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z( \| (?:session|transcript) elapsed \d{2,}:\d{2})?$")
    prefix_line_re = re.compile(r"^CONSOLE PREFIX \(copy exactly\): \[\d{4}-\d{2}-\d{2} \d{2}:\d{2} (?:EST|EDT)\]$")
    # PREFIX in lab_infra's tools/orch-hooks/require-console-timestamp.py, copied; the stamp is followed by a space
    console_re = re.compile(r"\[([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}) (EST|EDT)\] ")

    def clock_of(ctx):
        """The CLOCK line of an emitted context, after checking the context is that line then exactly one
        CONSOLE PREFIX line (the CLOCK line alone when the zone cannot be loaded)."""
        lines = ctx.split("\n")
        want = 1 if _console_zone() is None else 2
        if len(lines) != want or (want == 2 and not prefix_line_re.match(lines[1])):
            raise AssertionError("not a CLOCK line then one CONSOLE PREFIX line: %r" % (ctx,))
        return lines[0]

    def timestamp_hook():
        """lab_infra's require-console-timestamp.py, loaded from ../../tools/orch-hooks beside this file; the test
        is SKIPPED when it is absent (any install outside lab_infra), and FAILS then only under its own opt-in
        AIQT_HOOKS_REQUIRE_CONSOLE_HOOK=1 (a lab_infra gate). AIQT_HOOKS_REQUIRE_SIBLINGS, which requires the
        sibling clock hooks, does not require this lab_infra file."""
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "orch-hooks",
                            "require-console-timestamp.py")
        if not os.path.exists(path):
            if os.environ.get(CONSOLE_HOOK_REQUIRE_ENV) == "1":
                raise AssertionError(f"{path} is absent and {CONSOLE_HOOK_REQUIRE_ENV}=1 requires it")
            raise unittest.SkipTest(f"{path} is absent (not a lab_infra checkout)")
        spec = importlib.util.spec_from_file_location("rct_sibling", path)
        mod = importlib.util.module_from_spec(spec)
        old_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = old_dwb
        return mod

    # (UTC instant, wanted prefix): 59 seconds and 59.999999 seconds still print their own minute; EST in winter,
    # EDT in summer; each side of both 2026 transitions; and a floor that stays on the previous day and year
    prefix_cases = (
        ((2026, 11, 21, 23, 33, 0, 0), "[2026-11-21 18:33 EST]"),
        ((2026, 11, 21, 23, 33, 37, 0), "[2026-11-21 18:33 EST]"),
        ((2026, 11, 21, 23, 33, 59, 0), "[2026-11-21 18:33 EST]"),
        ((2026, 11, 21, 23, 33, 59, 999999), "[2026-11-21 18:33 EST]"),
        ((2026, 1, 15, 17, 0, 59, 0), "[2026-01-15 12:00 EST]"),
        ((2026, 7, 15, 16, 0, 59, 0), "[2026-07-15 12:00 EDT]"),
        ((2026, 3, 8, 6, 59, 59, 0), "[2026-03-08 01:59 EST]"),
        ((2026, 3, 8, 7, 0, 0, 0), "[2026-03-08 03:00 EDT]"),
        ((2026, 11, 1, 5, 59, 59, 0), "[2026-11-01 01:59 EDT]"),
        ((2026, 11, 1, 6, 0, 0, 0), "[2026-11-01 01:00 EST]"),
        ((2027, 1, 1, 4, 59, 59, 0), "[2026-12-31 23:59 EST]"),
    )

    class FixedClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 23, 17, 45, 30, tzinfo=datetime.timezone.utc).astimezone(tz)

    def run_main(stdin_text, env=None, argv=("clock-inject.py",)):
        old_clock, datetime.datetime = datetime.datetime, FixedClock
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
            datetime.datetime = old_clock
            sys.stdin, sys.stdout = old_in, old_out
            os.environ.clear()
            os.environ.update(old_env)

    class T(unittest.TestCase):

        def test_fleet_stdin_bound(self):
            class Input:
                def __init__(self, raw):
                    self.raw, self.requests = raw, []

                def read(self, size=-1):
                    self.requests.append(size)
                    if size < 0:
                        raise AssertionError("unbounded stdin read")
                    return self.raw[:size]
            old = sys.stdin
            try:
                for raw in (b"{}", b" " * STDIN_MAX_BYTES + b"{}"):
                    stream = Input(raw)
                    sys.stdin = stream
                    if len(raw) > STDIN_MAX_BYTES:
                        with self.assertRaises(ValueError):
                            _read_payload()
                    else:
                        self.assertEqual(_read_payload(), {})
                    self.assertEqual(stream.requests, [STDIN_MAX_BYTES + 1])
            finally:
                sys.stdin = old

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
            self.assertRegex(clock_of(obj["hookSpecificOutput"]["additionalContext"]), line_re)

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

        def test_oversized_input_answers_as_post_tool_use(self):
            # input over 8 MiB is discarded unparsed, so its event name is unknown: the reply names PostToolUse,
            # even for a PostToolUseFailure call (the docstring's Coverage paragraph and the README say so)
            small = json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"})
            for text, want in ((small + " " * 16, "PostToolUseFailure"),
                               (small + " " * STDIN_MAX_BYTES, "PostToolUse")):
                rc, out = run_main(text)
                self.assertEqual(rc, 0)
                obj = json.loads(out)
                self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], want, len(text))
                self.assertRegex(clock_of(obj["hookSpecificOutput"]["additionalContext"]), line_re)

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
                self.assertRegex(clock_of(json.loads(out)["hookSpecificOutput"]["additionalContext"]), line_re)
            # the legacy spellings still mark a worker whatever AIQT_HOOKS_WORKER says
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "0", "ORCH_VERIFY_OWNER": ""}))

        def test_subagent_still_gets_clock(self):
            # deliberate: in-session subagents are not skipped (context only, never a block)
            rc, out = run_main(json.dumps({"tool_name": "Bash", "agent_id": "a1"}))
            self.assertRegex(clock_of(json.loads(out)["hookSpecificOutput"]["additionalContext"]), line_re)

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

        # -- lease start sources --
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

        def test_lease_field_forms(self):
            want = datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=utc)
            for line in ("Active-session: sess-20260923T141710Z", "**Active-session:** sess-20260923T141710Z",
                         "- Active-session: sess-20260923T141710Z", "* Active-session: sess-20260923T141710Z",
                         "  - **Active-session:**  sess-20260923T141710Z "):
                self.assertEqual(lease_start(self.write_lease(f"# Lease\n\n{line}\n")), want, line)
            self.assertEqual(lease_start(self.write_lease("+ Active-session: sess-20200101T000000Z\n"
                                                          "Active-session: sess-20260923T141710Z\n")), want)

        def test_date_only_id_with_and_without_session_start(self):
            ids = "# Lease\n\n**Active-session:** sess-2026-09-23-opus55-r1\n\n**Status:** active\n"
            self.assertIsNone(lease_start(self.write_lease(ids)))
            want = datetime.datetime(2026, 9, 23, 14, 18, 0, tzinfo=utc)
            for field in ("**Session-start-UTC:** 2026-09-23T14:18:00Z", "* Session-start-UTC: 20260923T141800Z"):
                self.assertEqual(lease_start(self.write_lease(ids + field + "\n")), want, field)
            self.assertIsNone(lease_start(self.write_lease(ids + "## History\nSession-start-UTC: 20260920T010000Z\n")))

        def test_transcript_fallback_and_main(self):
            ids = "**Active-session:** sess-2026-09-23-opus55-r1\n"
            tp = self.transcript("2026-09-23T14:30:00.000Z", "2026-09-23T14:18:00.000Z", "garbage")
            self.assertEqual(lease_start(self.write_lease(ids), tp), datetime.datetime(2026, 9, 23, 14, 18, tzinfo=utc))
            self.assertIsNone(lease_start(self.lease))  # no transcript given: unknown
            start = FixedClock(2026, 9, 23, 15, 40, tzinfo=utc)
            tp = self.transcript(start.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": tp}),
                               {"AIQT_LEASE_FILE": self.lease})
            self.assertRegex(clock_of(json.loads(out)["hookSpecificOutput"]["additionalContext"]),
                             r"transcript elapsed 02:05$")
            rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": 7}), {"AIQT_LEASE_FILE": self.lease})
            self.assertNotIn("elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"])

        def test_inactive_lease_stays_unknown(self):
            tp = self.transcript("2026-09-23T14:18:00Z")
            for head in ("Active-session: none", "**Active-session:** none", "- Active-session: None", "Active-session:"):
                self.write_lease(f"{head}\nSession-start-UTC: 2026-09-23T14:18:00Z\n")
                self.assertIsNone(lease_start(self.lease, tp), head)
                rc, out = run_main(json.dumps({"tool_name": "Bash", "transcript_path": tp}),
                                   {"AIQT_LEASE_FILE": self.lease})
                self.assertNotIn("elapsed", json.loads(out)["hookSpecificOutput"]["additionalContext"], head)
            self.assertIsNone(lease_start(self.write_lease("# no lease field\n"), tp))

        def test_fifo_transcript_does_not_block(self):
            fifo = os.path.join(self.tmp, "tfifo")
            os.mkfifo(fifo)
            self.write_lease("**Active-session:** sess-2026-09-23-opus55-r1\n")
            code = ("import importlib.util as u;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print(m.lease_start(%r, %r))" % (os.path.abspath(__file__), self.lease, fifo))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                               timeout=HANG_TIMEOUT)
            self.assertEqual(r.stdout.strip(), "None")

        def test_shared_lease_code_identical_to_stop_hook(self):
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


        # -- console prefix: a ready-to-copy console stamp after the CLOCK line --
        def test_console_prefix_floored_and_zoned(self):
            if _console_zone() is None:
                self.skipTest("zone data for America/Toronto is unavailable")
            for parts, want in prefix_cases:
                self.assertEqual(console_prefix(datetime.datetime(*parts, tzinfo=utc)), want, parts)

        def test_console_prefix_matches_timestamp_hook_regex(self):
            if _console_zone() is None:
                self.skipTest("zone data for America/Toronto is unavailable")
            for parts, _want in prefix_cases:
                got = console_prefix(datetime.datetime(*parts, tzinfo=utc))
                self.assertTrue(console_re.match(got + " text"), got)
                self.assertTrue(prefix_line_re.match(PREFIX_LABEL + got), got)

        def test_console_prefix_accepted_by_timestamp_hook(self):
            if _console_zone() is None:
                self.skipTest("zone data for America/Toronto is unavailable")
            rct = timestamp_hook()
            self.assertEqual((rct.PREFIX.pattern, rct.ZONE), (console_re.pattern, ZONE))
            zone = rct.zoneinfo.ZoneInfo(rct.ZONE)
            for parts, want in prefix_cases:
                now = datetime.datetime(*parts, tzinfo=utc)
                entry = now.isoformat()
                self.assertEqual(console_prefix(now), rct.stamp(now, zone), parts)
                self.assertTrue(rct.compliant(console_prefix(now) + " outcome", entry, zone), parts)
                # the minute after, which rounding the seconds up gives, is refused for the same entry
                ahead = rct.stamp(now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1), zone)
                self.assertFalse(rct.compliant(ahead + " outcome", entry, zone), parts)

        def test_context_keeps_clock_line_then_prefix(self):
            if _console_zone() is None:
                self.skipTest("zone data for America/Toronto is unavailable")
            rc, out = run_main(json.dumps({"tool_name": "Bash"}))
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            instant = datetime.datetime(2026, 9, 23, 17, 45, 30, tzinfo=utc)  # FixedClock's reading
            self.assertEqual(ctx, clock_line(instant) + "\nCONSOLE PREFIX (copy exactly): [2026-09-23 13:45 EDT]")
            for parts, want in prefix_cases:
                now = datetime.datetime(*parts, tzinfo=utc)
                self.assertEqual(clock_context(now).split("\n"), [clock_line(now), PREFIX_LABEL + want], parts)

        def test_zone_loaded_once_per_context(self):
            # both lines come from ONE zone load, so a load that would fail on a second call (a zone file
            # replaced mid-run) can never give the CLOCK line the process zone and the prefix Toronto
            real = _console_zone
            if real() is None:
                self.skipTest("zone data for America/Toronto is unavailable")
            calls = []

            def first_only():
                calls.append(1)
                return real() if len(calls) == 1 else None
            now = datetime.datetime(2026, 11, 22, 3, 0, 30, tzinfo=utc)
            with unittest.mock.patch.dict(globals(), {"_console_zone": first_only}):
                ctx = clock_context(now)
            self.assertEqual(len(calls), 1)
            self.assertEqual(ctx, "CLOCK (read by hook, authoritative): 2026-11-21 22:00:30 EST | 2026-11-22T03:00:30Z"
                             "\n" + PREFIX_LABEL + "[2026-11-21 22:00 EST]")
            calls.clear()

            def fail_first():
                calls.append(1)
                return None if len(calls) == 1 else real()
            with unittest.mock.patch.dict(globals(), {"_console_zone": fail_first}):
                ctx = clock_context(now)
            self.assertEqual(len(calls), 1)
            self.assertEqual(ctx, clock_line(now, zoned=None))  # the process zone, and no prefix line

        def test_console_prefix_omitted_without_zone(self):
            global _console_zone
            saved = _console_zone
            _console_zone = lambda: None
            try:
                rc, out = run_main(json.dumps({"tool_name": "Bash"}))
                now = datetime.datetime(2026, 9, 23, 17, 45, 30, tzinfo=utc)
                self.assertIsNone(console_prefix(now))
                self.assertEqual(clock_context(now), clock_line(now))
                # without the zone the CLOCK line is in the process zone (see the zone fallback tests below)
                want = clock_line(now)
            finally:
                _console_zone = saved
            self.assertEqual(rc, 0)
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(ctx, want)

        def test_console_hook_opt_in_is_separate_from_siblings(self):
            # the lab_infra file is required only by its own opt-in, never by AIQT_HOOKS_REQUIRE_SIBLINGS
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "orch-hooks",
                                "require-console-timestamp.py")
            if os.path.exists(path):
                self.skipTest(f"{path} is present (a lab_infra checkout)")
            old_env = dict(os.environ)
            try:
                os.environ.pop(CONSOLE_HOOK_REQUIRE_ENV, None)
                os.environ["AIQT_HOOKS_REQUIRE_SIBLINGS"] = "1"
                with self.assertRaises(unittest.SkipTest):
                    timestamp_hook()
                os.environ[CONSOLE_HOOK_REQUIRE_ENV] = "1"
                with self.assertRaisesRegex(AssertionError, CONSOLE_HOOK_REQUIRE_ENV):
                    timestamp_hook()
                os.environ.pop("AIQT_HOOKS_REQUIRE_SIBLINGS")
                with self.assertRaisesRegex(AssertionError, CONSOLE_HOOK_REQUIRE_ENV):
                    timestamp_hook()
            finally:
                os.environ.clear()
                os.environ.update(old_env)
            self.assertEqual(CONSOLE_HOOK_REQUIRE_ENV, "AIQT_HOOKS_REQUIRE_CONSOLE_HOOK")

        # -- the CLOCK line's zone is ZONE whatever the process zone (from the session copy) --
        def pin_process_zone(self, tz):
            # a POSIX TZ string needs no tzdata; the process zone must never leak into the local segment
            old = os.environ.get("TZ")
            os.environ["TZ"] = tz
            time.tzset()

            def restore():
                os.environ.pop("TZ", None)
                if old is not None:
                    os.environ["TZ"] = old
                time.tzset()
            self.addCleanup(restore)

        def test_local_segment_is_toronto_across_dst(self):
            # both 2026 changes (2026-03-08 07:00Z and 2026-11-01 06:00Z), each just before, at, and just after,
            # plus the repeated hour; the zone database supplies EST or EDT, never a fixed offset; the CONSOLE
            # PREFIX line names the same zone as the CLOCK line under every process zone
            self.assertIsNotNone(zoneinfo.ZoneInfo(ZONE))
            vectors = (((2026, 3, 8, 6, 59, 59), "2026-03-08 01:59:59 EST"),
                       ((2026, 3, 8, 7, 0, 0), "2026-03-08 03:00:00 EDT"),
                       ((2026, 3, 8, 7, 0, 1), "2026-03-08 03:00:01 EDT"),
                       ((2026, 11, 1, 5, 30, 0), "2026-11-01 01:30:00 EDT"),
                       ((2026, 11, 1, 5, 59, 59), "2026-11-01 01:59:59 EDT"),
                       ((2026, 11, 1, 6, 0, 0), "2026-11-01 01:00:00 EST"),
                       ((2026, 11, 1, 6, 0, 1), "2026-11-01 01:00:01 EST"),
                       ((2026, 11, 1, 6, 30, 0), "2026-11-01 01:30:00 EST"),
                       ((2026, 11, 4, 21, 14, 35), "2026-11-04 16:14:35 EST"),
                       ((2026, 9, 23, 17, 45, 2), "2026-09-23 13:45:02 EDT"))
            for tz in ("UTC0", "<+05>-5", "EST5EDT,M3.2.0,M11.1.0"):
                self.pin_process_zone(tz)
                for fields, want in vectors:
                    now = datetime.datetime(*fields, tzinfo=utc)
                    ln = clock_line(now)
                    self.assertRegex(ln, line_re)
                    # the UTC segment keeps its existing form
                    self.assertEqual(ln, f"CLOCK (read by hook, authoritative): {want} | "
                                         f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}", tz)
                    ctx = clock_context(now).split("\n")
                    self.assertEqual(ctx[0], ln, tz)
                    self.assertEqual(ctx[1], f"{PREFIX_LABEL}[{want[:16]} {want[-3:]}]", tz)

        def test_wrong_abbreviation_never_shown(self):
            self.pin_process_zone("UTC0")
            for fields, wrong in (((2026, 11, 4, 21, 14, 35), "EDT"), ((2026, 7, 1, 12, 0, 0), "EST"),
                                  ((2026, 3, 8, 7, 0, 0), "EST"), ((2026, 11, 1, 6, 0, 0), "EDT")):
                ln = clock_line(datetime.datetime(*fields, tzinfo=utc))
                zone = ln.split(": ", 1)[1].split(" | ")[0].rsplit(" ", 1)[1]
                self.assertIn(zone, ("EST", "EDT"))  # never UTC, never a numeric offset
                self.assertNotEqual(zone, wrong)

        def test_missing_zone_entry_falls_back_to_process_zone(self):
            self.pin_process_zone("<+05>-5")
            g = globals()
            old = g["ZONE"]
            g["ZONE"] = "Nowhere/Missing_Zone"
            try:
                now = datetime.datetime(2026, 11, 4, 21, 14, 35, tzinfo=utc)
                ln = clock_line(now)
                ctx = clock_context(now)
            finally:
                g["ZONE"] = old
            self.assertRegex(ln, line_re)
            self.assertIn(": 2026-11-05 02:14:35 +05 | 2026-11-04T21:14:35Z", ln)
            self.assertEqual(ctx, ln)  # no CONSOLE PREFIX line without the zone

        def damaged_zone(self, exc, method=None):
            """A stand-in for zoneinfo.ZoneInfo with a present but damaged ZONE file: building the zone
            raises `exc` (method None), or the named conversion (utcoffset, fromutc or tzname) does."""
            zone = zoneinfo.ZoneInfo(ZONE)

            class Damaged(datetime.tzinfo):
                def call(self, name, dt):
                    if name == method:
                        raise exc("damaged zone data")
                    return getattr(zone, name)(dt.replace(tzinfo=zone))

                def utcoffset(self, dt):
                    return self.call("utcoffset", dt)

                def dst(self, dt):
                    return self.call("dst", dt)

                def tzname(self, dt):
                    return self.call("tzname", dt)

                def fromutc(self, dt):
                    return self.call("fromutc", dt).replace(tzinfo=self)

            def build(key):
                if method is None:
                    raise exc("damaged zone data")
                return Damaged()
            return build

        def truncated_zone(self):
            """Point the zone search path at a copy of the ZONE file cut to half its length, a real damaged
            TZif file (loading it raises struct.error, neither ValueError nor OSError); the search path and the
            zone cache are restored afterwards."""
            src = next((os.path.join(d, ZONE) for d in zoneinfo.TZPATH
                        if os.path.isfile(os.path.join(d, ZONE))), None)
            self.assertIsNotNone(src, "no ZONE file on the zone search path")
            with open(src, "rb") as f:
                data = f.read()
            zdir = os.path.join(self.tmp, "zoneinfo")
            os.makedirs(os.path.dirname(os.path.join(zdir, ZONE)))
            with open(os.path.join(zdir, ZONE), "wb") as f:
                f.write(data[:len(data) // 2])
            old = zoneinfo.TZPATH

            def restore():
                zoneinfo.reset_tzpath(to=old)
                zoneinfo.ZoneInfo.clear_cache()
            self.addCleanup(restore)
            zoneinfo.reset_tzpath(to=[zdir])
            zoneinfo.ZoneInfo.clear_cache()  # a cached ZONE would hide the damaged file
            with self.assertRaises(struct.error):
                zoneinfo.ZoneInfo(ZONE)

        def assert_process_zone_line(self, now):
            # the clock line is still emitted, in the process zone, with no CONSOLE PREFIX line
            ln = clock_line(now)
            self.assertRegex(ln, line_re)
            self.assertIn(": 2026-11-05 02:14:35 +05 | 2026-11-04T21:14:35Z", ln)
            self.assertIsNone(console_prefix(now))
            rc, out = run_main(json.dumps({"tool_name": "Bash", "cwd": "/"}))
            self.assertEqual(rc, 0)
            ln = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertRegex(ln, line_re)
            self.assertEqual(ln.split(": ", 1)[1].split(" | ")[0].rsplit(" ", 1)[1], "+05")

        def test_damaged_zone_falls_back_to_process_zone(self):
            # a corrupt, truncated or unreadable zone file can raise any error, at construction or in a conversion
            self.pin_process_zone("<+05>-5")
            now = datetime.datetime(2026, 11, 4, 21, 14, 35, tzinfo=utc)
            self.assertIn(": 2026-11-04 16:14:35 EST | ", clock_line(now))  # the control: an undamaged zone
            for exc in (ValueError, OSError, struct.error):
                for method in (None, "utcoffset", "fromutc", "tzname"):
                    with self.subTest(exc=exc.__name__, method=method), \
                            unittest.mock.patch.object(zoneinfo, "ZoneInfo", self.damaged_zone(exc, method)):
                        self.assert_process_zone_line(now)

        def test_truncated_zone_file_falls_back_to_process_zone(self):
            # a real half-truncated ZONE file, loaded through the zone search path
            self.pin_process_zone("<+05>-5")
            now = datetime.datetime(2026, 11, 4, 21, 14, 35, tzinfo=utc)
            self.assertIn(": 2026-11-04 16:14:35 EST | ", clock_line(now))  # the control: the intact file
            self.truncated_zone()
            self.assert_process_zone_line(now)

        # -- field validation of the worker skip and the output error path --
        def test_worker_skip_warns_and_output_error_fails_open(self):
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
                self.assertRegex(clock_of(obj["additionalContext"]), line_re)
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
        PARITY_TESTS = ("test_shared_lease_code_identical_to_stop_hook",)
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
