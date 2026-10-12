"""Load-tolerant bounds for self-tests and gates: tell slowness from a stopped or spinning callee on a loaded host.

A plain wall-clock timeout turns host load into a verdict: a correct callee that the scheduler starves
past its bound is killed and reported as if it had failed. The bounds here measure two things:

  load_factor() / LoadClock
      The host run-queue pressure, max(1, 1-minute load average / usable CPUs), capped at
      MAX_LOAD_FACTOR; a reading that is not a finite, non-negative number counts as unknown (1.0). A
      LoadClock advances at wall rate divided by that factor, so a budget read from it stretches while
      the host is overloaded, and still expires for a callee that spins or polls forever: at most
      MAX_LOAD_FACTOR times the budget late, plus one poll interval and one process-table scan.
  tree_cpu_seconds(pid)
      The CPU seconds a child and its live descendants have used, plus what they reaped (Linux /proc).
      Slowness still accrues CPU time; a callee whose tree CPU time and captured output both stand still
      is blocked or asleep, however loaded the host is.

run() combines them: it is subprocess.run(capture_output=True) whose kill condition is "no CPU or output
progress for stall_s wall seconds" or "the load-adjusted budget_s is spent", never a bare wall-clock
count. It raises Hung, a subprocess.TimeoutExpired, whose kind names the condition that fired.

The stall test is a heuristic: silence is not proof of a hang. A correct callee that legitimately waits
with no CPU use and no output for stall_s seconds (a sleep, a lock or Event wait, a silent child it waits
on, I/O blocked under memory or disk pressure) is stopped exactly like a hung one: a false positive. So
every caller reports a Hung as the bound firing, CANNOT-EVALUATE (exit 2), never as the callee's failure
and never as a proven hang; and each call site sizes stall_s above the longest silent wait its callee
legitimately makes, and states that wait at the call site. Where tree CPU time cannot be read (no /proc),
or once it becomes unreadable during a run, the stall test is off for the rest of that run and only the
load-adjusted budget applies.

run() always stops what it launched: on ANY exception after the launch (a bound firing, a fault in the
monitoring itself, an interrupt) it kills the child's process tree and waits at most REAP_S seconds to reap
the child before the exception leaves. A fault in the monitoring is raised as Hung (kind "monitor"), so it
is cannot-evaluate too, never a crash that looks like a failure.

run() does not launch git (ValueError before any launch): it resolves the program that would execute
(PATH from env, then symlinks) and refuses a git head, and refuses outright a head that is a shell or a
program launcher (env, nice, timeout and the rest of _LAUNCHERS), whose arguments could name git. git
launches stay on a direct subprocess call, where the maintenance-pin scan in
tools/selftest_git_fixture_env.py reads and enforces their pins.

The shared vectors (vector_failures) run in the check_gensrc_failclose.py self-test only; each other
adopting self-test carries vectors for its own call site.

Residuals: a hang that keeps accruing CPU time or output (a spin or a polling loop) is caught only by the
load-adjusted budget; the load average is a smoothed host-wide figure, so a burst shorter than a minute is
under-counted, and a CPU quota, CPU pinning or niceness that starves the callee is not seen at all; a
descendant that re-parents away from the child (a daemon) leaves the CPU sum and is not killed, and while
the child waits on such a descendant the child looks silent; without /proc (macOS, Windows) only the
direct child is killed and the stall test is off; a child in uninterruptible sleep can outlive SIGKILL, and
is then left unreaped after REAP_S rather than waited on without bound; the git refusal sees the launched
program only, not git launched by code an interpreter runs (python -c), which its own call site answers for.
"""
import math
import os
import shutil
import signal
import subprocess
import time

MAX_LOAD_FACTOR = 16.0
POLL_S = 1.0
REAP_S = 10.0  # seconds run() waits to reap a SIGKILLed child before leaving it, so a stop stays bounded
_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows has no SIGKILL
# Heads run() refuses outright: each runs a program its arguments name, so git could hide behind it.
_LAUNCHERS = frozenset((
    "env", "exec", "command", "builtin", "nice", "nohup", "setsid", "stdbuf", "timeout", "ionice", "chrt",
    "taskset", "time", "xargs", "flock", "sudo", "doas", "su", "runuser", "sg", "newgrp", "chroot", "unshare",
    "nsenter", "prlimit", "systemd-run", "firejail", "bwrap", "proot", "strace", "ltrace", "valgrind",
    "busybox", "script", "watch", "parallel", "sh", "bash", "dash", "zsh", "ksh", "mksh", "fish", "csh",
    "tcsh", "cmd", "powershell", "pwsh"))


def _real(value):
    """True for a finite int or float (not a bool): a reading safe to do arithmetic with."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_factor(read=None):
    """max(1, 1-minute load average / usable CPUs), capped at MAX_LOAD_FACTOR; 1.0 when unknown, which
    includes a reading that is not a finite, non-negative number. read stands in for os.getloadavg."""
    try:
        load = (read or os.getloadavg)()[0]
    except Exception:  # noqa: BLE001  an unreadable load is unknown, never an error in the bound
        return 1.0
    if not _real(load) or load < 0:
        return 1.0
    cpus = getattr(os, "process_cpu_count", os.cpu_count)()
    cpus = cpus if isinstance(cpus, int) and not isinstance(cpus, bool) and cpus >= 1 else 1
    return min(MAX_LOAD_FACTOR, max(1.0, load / cpus))


class LoadClock:
    """A monotonic clock in unloaded-equivalent seconds: each interval between two reads advances by its
    wall length divided by the mean load factor at its two ends. Callable, like time.monotonic. A factor
    reading that is not a finite number of at least 1 counts as 1."""

    def __init__(self, wall=time.monotonic, factor=None):
        self._wall = wall
        self._factor = factor
        self._last_wall = wall()
        self._last_factor = self._read_factor()
        self._value = 0.0

    def _read_factor(self):
        value = (self._factor or load_factor)()
        return float(value) if _real(value) and value >= 1.0 else 1.0

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
    """A bounded child was stopped: kind "stall" (no CPU or output progress for stall_s, a heuristic that
    a correct silent wait also trips), "budget" (the load-adjusted budget was spent) or "monitor" (the
    monitoring itself failed). Attributed to the bound, never to the child: callers report it as
    cannot-evaluate, never as the child's failure or as a proven hang."""

    def __init__(self, cmd, timeout, kind, reason, output=None, stderr=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.reason = reason

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


def _kill_tree(proc):
    table = _proc_table()
    members = _descendants(table, proc.pid) if table and proc.pid in table else [proc.pid]
    for member in members:
        try:
            os.kill(member, _KILL)
        except OSError:
            pass


def _stop(proc):
    """Kill proc and its process tree, then reap proc, waiting at most REAP_S. Returns "" once reaped, or
    a note that the child was left unreaped. Never raises for a child that is already gone."""
    try:
        _kill_tree(proc)
    except Exception:  # noqa: BLE001  a failed tree scan must not skip the direct kill below
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=REAP_S)
    except subprocess.TimeoutExpired:
        return "; the child outlived SIGKILL for {} s and was left unreaped".format(REAP_S)
    return ""


def _launched_names(argv, env, cwd):
    """The lowercased names of the program argv would execute: the head as given and, where it resolves
    (PATH from env, as Popen searches it, or a path relative to cwd), the basename of its real path."""
    given = os.fsdecode(os.fspath(argv[0]))
    if os.sep in given or (os.altsep and os.altsep in given):
        found = os.path.join(os.fsdecode(os.fspath(cwd)) if cwd is not None else os.getcwd(), given)
    else:
        found = shutil.which(given, path=os.pathsep.join(os.get_exec_path(env)))
    names = {os.path.basename(given)}
    if found:
        names.add(os.path.basename(os.path.realpath(found)))
    return {name.lower().removesuffix(".exe") for name in names}


def _refused_head(argv, env, cwd):
    """A reason run() must not launch argv, or None."""
    for name in sorted(_launched_names(argv, env, cwd)):
        if name.startswith("git"):
            return "its program resolves to git ({})".format(name)
        if name in _LAUNCHERS:
            return "its program is a shell or launcher ({}) whose arguments could run git".format(name)
    return None


def _watch(proc, argv, pending, budget_s, stall_s, clock, start, wall_start, poll_s, cpu_seconds):
    """Wait for proc under both bounds; return (stdout, stderr), or raise Hung (kind stall or budget)."""
    seen_cpu, seen_output, cpu_readable = None, -1, True
    last_progress = wall_start
    while True:
        try:
            return proc.communicate(pending, timeout=poll_s)
        except subprocess.TimeoutExpired as exc:
            pending = None
            output = len(exc.output or b"") + len(exc.stderr or b"")
            cpu = cpu_seconds(proc.pid) if cpu_readable else None
            if not _real(cpu) or cpu < 0:
                # Unreadable once means unobservable for the rest of the run: a valid earlier sample does
                # not show that the tree is still measured, so the stall test is switched off for good.
                cpu_readable, seen_cpu = False, None
            if seen_output < output or (cpu_readable and (seen_cpu is None or seen_cpu < cpu)):
                last_progress = time.monotonic()
            seen_output = max(seen_output, output)
            if cpu_readable:
                seen_cpu = cpu if seen_cpu is None else max(seen_cpu, cpu)
            idle = time.monotonic() - last_progress
            if cpu_readable and idle >= stall_s:
                raise Hung(argv, budget_s, "stall",
                           "heuristic stall bound fired: no CPU or output progress for {:.1f} s (stall bound "
                           "{} s); silence is not proof of a hang, so the run is cannot-evaluate"
                           .format(idle, stall_s), exc.output, exc.stderr) from None
            if clock() - start >= budget_s:
                raise Hung(argv, budget_s, "budget",
                           "load-adjusted budget of {} s spent after {:.1f} s of wall time (load factor now "
                           "{:.1f})".format(budget_s, time.monotonic() - wall_start, load_factor()),
                           exc.output, exc.stderr) from None


def run(argv, *, budget_s, stall_s, input=None, cwd=None, env=None, text=False, errors=None, clock=None,
        poll_s=POLL_S, cpu_seconds=None):
    """subprocess.run(argv, capture_output=True, input=, cwd=, env=, text=, errors=) bounded by progress.
    The child (and its descendants) is killed and Hung raised when its tree CPU time and output have not
    grown for stall_s wall seconds (a heuristic, off once tree CPU time is unreadable), or when budget_s
    seconds of load-adjusted time (clock, a fresh LoadClock by default) have passed. Any other exception
    after the launch also kills and reaps the child (at most REAP_S); a monitoring fault leaves as Hung
    kind "monitor". Returns a CompletedProcess otherwise. Without input the child stdin is os.devnull, so a
    child that reads stdin sees EOF instead of blocking. cpu_seconds stands in for tree_cpu_seconds."""
    refused = _refused_head(argv, env, cwd)
    if refused is not None:
        # git launches stay on a direct subprocess call, where the maintenance-pin scan in
        # tools/selftest_git_fixture_env.py can read and enforce their pins.
        raise ValueError("_load_bound.run does not launch {!r}: {}".format(argv[0], refused))
    cpu_seconds = tree_cpu_seconds if cpu_seconds is None else cpu_seconds
    clock = LoadClock() if clock is None else clock
    start, wall_start = clock(), time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL if input is None else subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=cwd, env=env, text=text, errors=errors)
    try:
        stdout, stderr = _watch(proc, argv, input, budget_s, stall_s, clock, start, wall_start, poll_s,
                                cpu_seconds)
    except BaseException as exc:
        unreaped = _stop(proc)
        if isinstance(exc, Hung):
            exc.reason += unreaped
            raise
        if not isinstance(exc, Exception):
            raise  # an interrupt or exit leaves as itself, after the child is stopped
        raise Hung(argv, budget_s, "monitor",
                   "the bound's own monitoring failed ({}: {}); the child was stopped{}, so the run is "
                   "cannot-evaluate".format(type(exc).__name__, exc, unreaped)) from exc
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _alive_with(token):
    """Pids whose command line carries token (Linux /proc); [] where /proc is unreadable."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    found = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if token.encode() in raw:
            found.append(int(name))
    return found


def _kill_pid(pid):
    try:
        os.kill(pid, _KILL)
    except OSError:
        pass


def vector_failures(python):
    """The shared vectors, run by the check_gensrc_failclose.py self-test; returns a list of failure
    strings. A child that outlives its bound while making CPU or output progress must pass; a silent child
    must be stopped by the stall bound and a spinning one by the load-adjusted budget, each as Hung; a load
    reading that is malformed, or a CPU reading that turns unreadable, must not break either bound; any
    fault after the launch must still stop and reap the child; git must not launch through a launcher.

    None of them depends on the real host load. The progress vectors set the budget out of reach, so they
    pass whenever the child gets some CPU or prints within each stall window, however slowly it finishes;
    the stretched-budget vector needs only interpreter start-up CPU against 16 s of budget for about 3 s of
    sleeping; every stop vector expects a stop, which load can only delay. A non-terminating child is also
    killed by a watchdog after 30 s, so a regression shows as a failure here, never as a hung self-test."""
    import tempfile
    import threading

    failures = []
    token = "load-bound-vector-{}".format(os.getpid())

    def outcome(code, **bounds):
        try:
            done = run([python, "-I", "-B", "-c", code, token], **bounds)
        except Hung as exc:
            return "hung: " + exc.kind
        return done.returncode

    def bounded(label, code, want, limit, **bounds):
        # A non-terminating child: the outcome must arrive within limit wall seconds, leaving nothing alive.
        timer = threading.Timer(30.0, lambda: [_kill_pid(pid) for pid in _alive_with(token)])
        timer.daemon = True
        began = time.monotonic()
        timer.start()
        try:
            got = outcome(code, **bounds)
        except Exception as exc:  # noqa: BLE001  any escape is this vector's failure
            got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            timer.cancel()
        took, left = time.monotonic() - began, _alive_with(token)
        for pid in left:
            _kill_pid(pid)  # a regression must not leak its child past this vector
        if (got, took <= limit, left) != (want, True, []):
            failures.append("_load_bound vector ({}): expected {!r} within {} s and no child left, got {!r} "
                            "after {:.1f} s, left alive {!r}".format(label, want, limit, got, took, left))

    spin = "import time\nend = time.process_time() + {}\nwhile time.process_time() < end:\n    pass\n"
    cases = (
        ("cpu progress outlives the stall bound", spin.format(2.0),
         dict(budget_s=3600, stall_s=1.5, poll_s=0.2), 0),
        ("output progress outlives a 1 s budget stretched by a 16x load",
         "import sys, time\nfor _ in range(12):\n    print(1, flush=True)\n    time.sleep(0.25)\n",
         dict(budget_s=1, stall_s=1.5, clock=LoadClock(factor=lambda: MAX_LOAD_FACTOR), poll_s=0.2), 0),
        ("grandchild cpu counts as progress",
         "import subprocess, sys\nsys.exit(subprocess.run([sys.executable, chr(45) + chr(99), "
         + repr(spin.format(2.0)) + "]).returncode)\n",
         dict(budget_s=3600, stall_s=1.5, poll_s=0.2), 0),
    )
    for label, code, bounds, want in cases:
        got = outcome(code, **bounds)
        if got != want:
            failures.append("_load_bound vector ({}): expected {!r}, got {!r}".format(label, want, got))
    if tree_cpu_seconds(os.getpid()) is not None:  # no /proc: the stall test is off by design
        bounded("a silent child is stopped by the heuristic stall bound", "import time\ntime.sleep(3600)\n",
                "hung: stall", 25, budget_s=3600, stall_s=1.5, poll_s=0.2)
    bounded("a spinning child is stopped by the load-adjusted budget", "while True:\n    pass\n",
            "hung: budget", 25, budget_s=2, stall_s=3600, clock=LoadClock(factor=lambda: 1.0), poll_s=0.2)

    # CPU readable once, then unreadable: the stall test stays off for the rest of the run, even once a
    # later reading is readable again, so a child silent for longer than stall_s still finishes.
    readings = iter((0.0, None))
    got = outcome("import time\ntime.sleep(1.0)\nprint(1)\n", budget_s=3600, stall_s=0.3, poll_s=0.05,
                  cpu_seconds=lambda pid: next(readings, 0.0))
    if got != 0:
        failures.append("_load_bound vector (cpu turning unreadable switches the stall test off): expected 0, "
                        "got {!r}".format(got))

    # Malformed load readings are unknown (1.0), never an exception or a shrunken or infinite budget.
    def raising():
        raise OSError("injected unreadable load")
    for reading in (("malformed", 0, 0), (float("nan"), 0, 0), (-1.0, 0, 0), (float("inf"), 0, 0), (None,),
                    (), (True, 0, 0), raising):
        read = reading if callable(reading) else (lambda reading=reading: reading)
        try:
            got = load_factor(read=read)
        except Exception as exc:  # noqa: BLE001  an escape is this vector's failure
            got = exc
        if got != 1.0:
            failures.append("_load_bound vector (malformed load reading {!r} counts as 1.0): got {!r}"
                            .format(reading, got))
    try:
        LoadClock(factor=lambda: "malformed")()
    except Exception as exc:  # noqa: BLE001  a malformed injected factor must count as 1, not raise
        failures.append("_load_bound vector (a malformed injected factor counts as 1): {!r}".format(exc))

    # After the launch, a malformed reading still ends in a bounded stop (the budget fires), and a fault in
    # the monitoring itself still kills and reaps a non-terminating child, reported as cannot-evaluate.
    sequence = iter(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    bounded("a malformed load reading mid-run; non-terminating child", "import time\ntime.sleep(3600)\n",
            "hung: budget", 10, budget_s=0.5, stall_s=3600, poll_s=0.05,
            clock=LoadClock(factor=lambda: load_factor(read=lambda: next(sequence, ("malformed", 0, 0)))))
    ticks = iter((0.0, 0.0))

    def faulty_clock():
        value = next(ticks, None)
        if value is None:
            raise RuntimeError("injected monitoring fault")
        return value
    bounded("a monitoring fault mid-run; non-terminating child", "import time\ntime.sleep(3600)\n",
            "hung: monitor", 5 + REAP_S, budget_s=3600, stall_s=3600, poll_s=0.05, clock=faulty_clock)

    clock = LoadClock(wall=iter((0.0, 10.0, 20.0)).__next__, factor=iter((1.0, 3.0, 1.0)).__next__)
    readings = (clock(), clock())
    if readings != (5.0, 10.0):
        failures.append("_load_bound vector (load clock integrates wall / mean factor): got {!r}"
                        .format(readings))

    # git is refused however the head reaches it (the vectors never reach a launch); an interpreter is not.
    scratch = tempfile.mkdtemp(prefix="load-bound-vector-")
    try:
        heads = [["git", "--version"], ["/usr/bin/env", "git", "--version"], ["env", "-i", "git", "--version"],
                 ["nice", "-n", "1", "git", "--version"], ["sh", "-c", "git --version"]]
        real_git = shutil.which("git")
        if real_git is not None:
            alias = os.path.join(scratch, "tool")
            os.symlink(real_git, alias)
            heads += [[real_git, "--version"], [alias, "--version"]]
        for argv in heads:
            try:
                run(argv, budget_s=10, stall_s=10)
                failures.append("_load_bound vector (git refused through {!r}): it launched".format(argv))
            except ValueError:
                pass
        if outcome("pass\n", budget_s=60, stall_s=60) != 0:
            failures.append("_load_bound vector (an interpreter head is not refused as git)")
    finally:
        shutil.rmtree(scratch)
    return failures
