"""Load-tolerant bounds for self-tests and gates: tell a hang from slowness on a loaded host.

A plain wall-clock timeout turns host load into a verdict: a correct callee that the scheduler starves
past its bound is killed and reported as if it had failed. The bounds here measure two things a busy
host cannot fake:

  load_factor() / LoadClock
      The host run-queue pressure, max(1, 1-minute load average / usable CPUs), capped at
      MAX_LOAD_FACTOR. A LoadClock advances at wall rate divided by that factor, so a budget read from it
      stretches while the host is overloaded, and still expires (at most MAX_LOAD_FACTOR times late) for
      a callee that spins or polls forever.
  tree_cpu_seconds(pid)
      The CPU seconds a child and its live descendants have used, plus what they reaped (Linux /proc).
      A callee whose tree CPU time and captured output both stand still for stall_s seconds is hung
      (blocked or asleep), however loaded the host is; slowness still accrues CPU time.

run() combines them: it is subprocess.run(capture_output=True) whose kill condition is "no progress for
stall_s" or "the load-adjusted budget_s is spent", never a bare wall-clock count. It raises Hung, a
subprocess.TimeoutExpired, so a caller that maps a timeout to cannot-evaluate keeps doing so, and the
message names which condition fired. Where /proc is unavailable the stall test is skipped and only the
load-adjusted budget applies.

Residuals: a hang that keeps accruing CPU time or output (a spin or a polling loop) is caught only by the
load-adjusted budget; the load average is a smoothed host-wide figure, so a burst shorter than a minute is
under-counted and a CPU quota below the visible CPU count is not seen; a descendant that re-parents away
from the child (a daemon) leaves the CPU sum and is not killed.
"""
import os
import signal
import subprocess
import time

MAX_LOAD_FACTOR = 16.0
POLL_S = 1.0
_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def load_factor():
    """max(1, 1-minute load average / usable CPUs), capped at MAX_LOAD_FACTOR; 1.0 when unknown."""
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 1.0
    cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return min(MAX_LOAD_FACTOR, max(1.0, load / cpus))


class LoadClock:
    """A monotonic clock in unloaded-equivalent seconds: each interval between two reads advances by its
    wall length divided by the mean load factor at its two ends. Callable, like time.monotonic."""

    def __init__(self, wall=time.monotonic, factor=None):
        self._wall = wall
        self._factor = factor
        self._last_wall = wall()
        self._last_factor = self._read_factor()
        self._value = 0.0

    def _read_factor(self):
        return (self._factor or load_factor)()

    def __call__(self):
        now, factor = self._wall(), self._read_factor()
        self._value += max(0.0, now - self._last_wall) / ((factor + self._last_factor) / 2.0)
        self._last_wall, self._last_factor = now, factor
        return self._value


def _proc_table():
    """{pid: (ppid, utime+stime+cutime+cstime ticks)} for every readable /proc/<pid>/stat, or None."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    table = {}
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        fields = raw[raw.rfind(b")") + 2:].split()
        try:
            table[int(name)] = (int(fields[1]), sum(int(value) for value in fields[11:15]))
        except (IndexError, ValueError):
            continue
    return table or None


def _descendants(table, pid):
    children = {}
    for child, (parent, _ticks) in table.items():
        children.setdefault(parent, []).append(child)
    found, todo = [], [pid]
    while todo:
        current = todo.pop()
        if current in table and current not in found:
            found.append(current)
            todo.extend(children.get(current, ()))
    return found


def tree_cpu_seconds(pid, table=None):
    """CPU seconds used by pid and its live descendants, including children they reaped; None when the
    process table cannot be read (no /proc) or pid is gone."""
    table = _proc_table() if table is None else table
    if not table or pid not in table:
        return None
    return sum(table[member][1] for member in _descendants(table, pid)) / float(_TICKS)


class Hung(subprocess.TimeoutExpired):
    """A bounded child was killed: it made no progress for stall_s, or spent its load-adjusted budget.
    Attributed to the bound, never to the child: callers report it as cannot-evaluate or a timeout."""

    def __init__(self, cmd, timeout, reason, output=None, stderr=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.reason = reason

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


def _kill_tree(proc):
    table = _proc_table()
    members = _descendants(table, proc.pid) if table and proc.pid in table else [proc.pid]
    for member in members:
        try:
            os.kill(member, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run(argv, *, budget_s, stall_s, input=None, cwd=None, env=None, text=False, errors=None, clock=None,
        poll_s=POLL_S):
    """subprocess.run(argv, capture_output=True, input=, cwd=, env=, text=, errors=) bounded by progress. The child (and its
    descendants) is killed and Hung raised when its tree CPU time and output have not grown for stall_s
    wall seconds (skipped where tree CPU time is unreadable), or when budget_s seconds of load-adjusted
    time (clock, a fresh LoadClock by default) have passed. Returns a CompletedProcess otherwise. Without
    input the child stdin is os.devnull, so a child that reads stdin sees EOF instead of blocking."""
    if os.path.basename(str(argv[0])).lower().startswith("git"):
        # git launches stay on a direct subprocess call, where the maintenance-pin scan in
        # tools/selftest_git_fixture_env.py can read and enforce their pins.
        raise ValueError("_load_bound.run does not launch git: {!r}".format(argv[0]))
    clock = LoadClock() if clock is None else clock
    start, wall_start = clock(), time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL if input is None else subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=cwd, env=env, text=text, errors=errors)
    with proc:
        pending, seen_cpu, seen_output = input, None, -1
        last_progress = wall_start
        while True:
            try:
                stdout, stderr = proc.communicate(pending, timeout=poll_s)
            except subprocess.TimeoutExpired as exc:
                pending = None
                output = len(exc.output or b"") + len(exc.stderr or b"")
                cpu = tree_cpu_seconds(proc.pid)
                if output > seen_output or (cpu is not None and (seen_cpu is None or cpu > seen_cpu)):
                    last_progress = time.monotonic()
                seen_output = max(seen_output, output)
                if cpu is not None:
                    seen_cpu = cpu if seen_cpu is None else max(seen_cpu, cpu)
                idle = time.monotonic() - last_progress
                reason = None
                if seen_cpu is not None and idle >= stall_s:
                    reason = ("no CPU or output progress for {:.0f} s (stall bound {} s): a hang, not "
                              "slowness".format(idle, stall_s))
                elif clock() - start >= budget_s:
                    reason = ("load-adjusted budget of {} s spent after {:.0f} s of wall time (load "
                              "factor now {:.1f})".format(budget_s, time.monotonic() - wall_start,
                                                          load_factor()))
                if reason is not None:
                    _kill_tree(proc)
                    proc.kill()
                    proc.wait()
                    raise Hung(argv, budget_s, reason, exc.output, exc.stderr) from None
                continue
            return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def vector_failures(python):
    """The shared vectors every adopting self-test runs; returns a list of failure strings. A slowed child
    that outlives the old wall-clock bound while making progress must pass; a sleeping child must be
    stopped by the stall bound, and a spinning one by the load-adjusted budget, each as Hung."""
    failures = []
    heavy = LoadClock(factor=lambda: 8.0)

    def outcome(code, **bounds):
        try:
            done = run([python, "-I", "-B", "-c", code], **bounds)
        except Hung as exc:
            return "hung: " + exc.reason.split(" ", 1)[0]
        return done.returncode

    spin = "import time\nend = time.process_time() + {}\nwhile time.process_time() < end:\n    pass\n"
    cases = (
        ("cpu-progress past the old 1 s bound under an 8x load", spin.format(2.5),
         dict(budget_s=1, stall_s=1, clock=heavy, poll_s=0.2), 0),
        ("output-progress past the old 1 s bound under an 8x load",
         "import sys, time\nfor _ in range(12):\n    print(1, flush=True)\n    time.sleep(0.25)\n",
         dict(budget_s=1, stall_s=1, clock=heavy, poll_s=0.2), 0),
        ("grandchild cpu counts as progress",
         "import subprocess, sys\nsys.exit(subprocess.run([sys.executable, chr(45) + chr(99), "
         + repr(spin.format(2.5)) + "]).returncode)\n",
         dict(budget_s=1, stall_s=1, clock=LoadClock(factor=lambda: 8.0), poll_s=0.2), 0),
        ("a sleeping hang is stopped by the stall bound", "import time\ntime.sleep(3600)\n",
         dict(budget_s=3600, stall_s=1.5, poll_s=0.2), "hung: no"),
        ("a spinning hang is stopped by the load-adjusted budget", "while True:\n    pass\n",
         dict(budget_s=2, stall_s=3600, clock=LoadClock(factor=lambda: 1.0), poll_s=0.2),
         "hung: load-adjusted"),
    )
    for label, code, bounds, want in cases:
        if tree_cpu_seconds(os.getpid()) is None and want == "hung: no":
            continue  # no /proc: the stall test is skipped by design, so this vector cannot apply
        got = outcome(code, **bounds)
        if got != want:
            failures.append("_load_bound vector ({}): expected {!r}, got {!r}".format(label, want, got))
    clock = LoadClock(wall=iter((0.0, 10.0, 20.0)).__next__, factor=iter((1.0, 3.0, 1.0)).__next__)
    readings = (clock(), clock())
    if readings != (5.0, 10.0):
        failures.append("_load_bound vector (load clock integrates wall / mean factor): got {!r}"
                        .format(readings))
    return failures
