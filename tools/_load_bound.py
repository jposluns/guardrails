"""Load-tolerant bounds for self-tests and gates: tell slowness from a stopped or spinning callee on a loaded host.

A plain wall-clock timeout turns host load into a verdict: a correct callee that the scheduler starves
past its bound is killed and reported as if it had failed. The bounds here measure two things:

  load_factor() / LoadClock
      The host run-queue pressure, max(1, 1-minute load average / usable CPUs), capped at
      MAX_LOAD_FACTOR; a reading that is not a finite, non-negative number counts as unknown (1.0). A
      LoadClock advances at wall rate divided by that factor, so a budget read from it stretches while
      the host is overloaded, and still expires for a callee that spins or polls forever: at most
      MAX_LOAD_FACTOR times the budget late, plus one poll interval and one process-table scan (at most
      SCAN_S).
  tree_cpu_seconds(pid)
      The CPU seconds a child and its live descendants have used, plus what they reaped (Linux /proc).
      Slowness still accrues CPU time, so a callee whose tree CPU time and captured output both stand
      still is making no visible progress; that is a sign of a hang, not proof of one (see below).

run() combines them: it is subprocess.run(capture_output=True) whose kill condition is "no CPU or output
progress for stall_s wall seconds" or "the load-adjusted budget_s is spent", never a bare wall-clock
count. It raises Hung, a subprocess.TimeoutExpired, whose kind names the condition that fired.

The stall test is a heuristic: silence is not proof of a hang. A correct callee that legitimately waits
with no CPU use and no output for stall_s seconds (a sleep, a lock or Event wait, a silent child it waits
on, I/O blocked under memory or disk pressure) is stopped exactly like a hung one: a false positive. So a
caller reports a Hung that carries no failure evidence as the bound firing, CANNOT-EVALUATE (exit 2), never
as a proven hang; and each call site sizes stall_s above the longest silent wait its callee legitimately
makes, and states that wait at the call site. A Hung is not a licence to drop failure evidence: when the
child had already exited on its own before the stop (a descendant still held its output open),
Hung.returncode carries that exit status, and a nonzero status, like any other failure evidence the caller
captured beside the stop (stderr, a wrong result, a leak marker), is a FAILURE (exit 1) whatever bound
fired. Where tree CPU time cannot be read (no /proc, or a process-table scan cut short at SCAN_S), or once
it becomes unreadable during a run, the stall test is off for the rest of that run and only the
load-adjusted budget applies.

Cleanup. Once the watch has begun (Popen has returned), any exception that leaves the watch (a bound
firing, a fault in the monitoring itself, an interrupt) runs the stop before run() leaves: read whether
the child already exited on its own, kill the process tree, kill the child directly, then reap it waiting
at most REAP_S. Each stage runs in its own guard, so a fault in one stage is noted in the report and the
next stage still runs, and an interrupt (KeyboardInterrupt, SystemExit) that arrives during a stage is held
until every stage has been attempted and is then re-raised. A fault in the monitoring is raised as Hung
(kind "monitor"), cannot-evaluate, never a crash that looks like a failure. What this does not cover: an
interrupt that lands outside a stage guard (inside Popen itself, between Popen returning and the watch,
or between two stage guards), a child killed but left unreaped because the interrupt landed in the reap
stage, a child that survives both kills because every kill stage failed, a child
in uninterruptible sleep (left unreaped after REAP_S, not waited on without bound), and a single /proc
read that blocks in the kernel (the scan is bounded between entries, not inside one read).

git. run() does not knowingly launch git (ValueError before any launch). It resolves the program the
exec in the child would run, the way that exec looks it up: a head with a directory part relative to cwd,
a bare head through each PATH entry of env in order, a relative or empty entry taken relative to cwd. It
refuses a program whose given or real (symlink-resolved) name is git, or that is the same file as the git
the parent or the child would find (a hard link), and refuses a head whose name is one of _LAUNCHERS. It
then launches that resolved real path itself (Popen executable=), so the lookup and the exec cannot
disagree: a candidate the exec would skip is not passed over to a later PATH entry. _LAUNCHERS is a list
of known spellings of shells and program launchers, not a proof that nothing can reach git: an interpreter
head (python -c, perl, awk, make) or a launcher missing from the list can still run git, and so can a
copy of the git binary under another name. git launches stay on a direct subprocess call, where the
maintenance-pin scan in tools/selftest_git_fixture_env.py reads and enforces their pins; a call site of
run() answers for what its own interpreter code launches.

The shared vectors (vector_failures) run in the check_gensrc_failclose.py self-test only; each other
adopting self-test carries vectors for its own call site.

Residuals: a hang that keeps accruing CPU time or output (a spin or a polling loop) is caught only by the
load-adjusted budget; the load average is a smoothed host-wide figure, so a burst shorter than a minute is
under-counted, and a CPU quota, CPU pinning or niceness that starves the callee is not seen at all; a
descendant that re-parents away from the child (a daemon) leaves the CPU sum and is not killed, and while
the child waits on such a descendant the child looks silent; without /proc (macOS, Windows) only the
direct child is killed and the stall test is off; without os.waitid the exit status a child reached
before the stop is not read (Hung.returncode stays None); the program lookup mirrors POSIX exec and is
unverified on Windows; a file swapped between the lookup and the exec runs as swapped.
"""
import errno
import io
import math
import os
import shutil
import signal
import subprocess
import time

MAX_LOAD_FACTOR = 16.0
POLL_S = 1.0
REAP_S = 10.0  # seconds run() waits to reap a SIGKILLed child before leaving it, so a stop stays bounded
SCAN_S = 5.0  # wall seconds one process-table scan may take; a scan cut short reads as unreadable CPU
_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows has no SIGKILL
# Known spellings of heads run() refuses outright: each runs a program its arguments name, so git could
# hide behind it. A list of spellings, not a proof: an interpreter head or an unlisted launcher can still
# run git (see the module docstring).
_LAUNCHERS = frozenset((
    "env", "exec", "command", "builtin", "nice", "nohup", "setsid", "stdbuf", "timeout", "ionice", "chrt",
    "taskset", "time", "xargs", "flock", "sudo", "doas", "su", "runuser", "sg", "newgrp", "chroot", "unshare",
    "nsenter", "prlimit", "systemd-run", "firejail", "bwrap", "proot", "strace", "ltrace", "valgrind",
    "busybox", "script", "watch", "parallel", "sh", "bash", "dash", "zsh", "ksh", "mksh", "fish", "csh",
    "tcsh", "cmd", "powershell", "pwsh", "setarch", "linux32", "linux64", "i386", "x86_64", "numactl",
    "pkexec", "cgexec", "fakeroot", "eatmydata", "faketime", "find", "scalar"))


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


def _proc_table(limit_s=None):
    """(table, complete): table is {pid: (ppid, utime+stime+cutime+cstime ticks)} for every readable
    /proc/<pid>/stat, or None when none was read. The scan stops between entries once it has taken
    limit_s (SCAN_S) wall seconds; a scan cut short, or a /proc that cannot be listed, is not complete."""
    limit_s = SCAN_S if limit_s is None else limit_s
    try:
        names = os.listdir("/proc")
    except OSError:
        return None, False
    began = time.monotonic()
    table = {}
    for name in names:
        if time.monotonic() - began >= limit_s:
            return table or None, False
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
    return table or None, True


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


def tree_cpu_seconds(pid, table=None, limit_s=None):
    """CPU seconds used by pid and its live descendants, including children they reaped; None when the
    process table cannot be read in full within limit_s (SCAN_S) seconds (no /proc, or a scan cut short,
    which could under-count the tree) or pid is gone."""
    if table is None:
        table, complete = _proc_table(limit_s)
        if not complete:
            return None
    if not table or pid not in table:
        return None
    return sum(table[member][1] for member in _descendants(table, pid)) / float(_TICKS)


class Hung(subprocess.TimeoutExpired):
    """A bounded child was stopped: kind "stall" (no CPU or output progress for stall_s, a heuristic that
    a correct silent wait also trips), "budget" (the load-adjusted budget was spent) or "monitor" (the
    monitoring itself failed). The stop is attributed to the bound, never proven a hang. returncode is the
    exit status the child had already reached on its own before the stop (a descendant still held its
    output open), or None when the stop ended it or the status could not be read: a nonzero returncode is
    the child failing, which callers report as a failure, not as cannot-evaluate. output and stderr hold
    what was captured before the stop, as str when run() was given text=True or errors."""

    def __init__(self, cmd, timeout, kind, reason, output=None, stderr=None, returncode=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.reason = reason
        self.returncode = returncode

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


def _kill_tree(proc):
    table, _complete = _proc_table()
    members = _descendants(table, proc.pid) if table and proc.pid in table else [proc.pid]
    for member in members:
        try:
            os.kill(member, _KILL)
        except OSError:
            pass


def _own_exit(proc):
    """The status proc reached on its own (an exit code, or minus the signal that ended it), read without
    reaping it; None while it still runs, or where os.waitid is unavailable."""
    if proc.returncode is not None:
        return proc.returncode
    if not hasattr(os, "waitid"):
        return None
    info = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status


def _stop(proc):
    """Stop proc in four stages: read the status it reached on its own, kill its process tree, kill it
    directly, reap it (waiting at most REAP_S). Each stage runs in its own guard: a fault in one is noted
    and the next still runs, and an interrupt arriving during a stage is held until every stage has been
    attempted. Returns (own_exit, note, interrupt): the status proc reached before the stop or None, ""
    or a note naming each stage that failed, and the first held interrupt or None (the caller re-raises
    it)."""
    own_exit, notes, interrupt = None, [], None
    for stage in ("status read", "tree kill", "direct kill", "reap"):
        try:
            if stage == "status read":
                own_exit = _own_exit(proc)
            elif stage == "tree kill":
                _kill_tree(proc)
            elif stage == "direct kill":
                proc.kill()
            else:
                proc.wait(timeout=REAP_S)
        except subprocess.TimeoutExpired:
            notes.append("the child outlived SIGKILL for {} s and was left unreaped".format(REAP_S))
        except OSError as exc:
            if stage in ("status read", "reap"):  # a kill of a child that is already gone is no fault
                notes.append("the {} failed ({}: {})".format(stage, type(exc).__name__, exc))
        except Exception as exc:  # noqa: BLE001  a fault in one stage must not skip the stages after it
            notes.append("the {} failed ({}: {})".format(stage, type(exc).__name__, exc))
        except BaseException as exc:  # an interrupt is held until every stage has been attempted
            interrupt = exc if interrupt is None else interrupt
            notes.append("the {} was interrupted ({})".format(stage, type(exc).__name__))
    return own_exit, "".join("; " + note for note in notes), interrupt


def _as_text(data, stream):
    """Bytes captured before a stop, decoded as Popen decodes them in text mode (newlines translated)."""
    if not isinstance(data, bytes) or not isinstance(stream, io.TextIOWrapper):
        return data
    try:
        text = data.decode(stream.encoding, stream.errors)
    except (UnicodeError, LookupError):  # a stop can cut a multibyte character in half
        text = data.decode(stream.encoding, "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _program(argv, env, cwd):
    """The absolute path of the program Popen(argv, cwd=cwd, env=env) would execute, found the way the
    exec in the child finds it: a head with a directory part is taken relative to cwd; a bare head is
    looked up in each PATH entry of env (os.get_exec_path) in order, a relative or empty entry taken
    relative to cwd, and the first regular file this process may execute is the program. None when no
    candidate is found."""
    head = os.fsdecode(os.fspath(argv[0]))
    base = os.path.abspath(os.fsdecode(os.fspath(cwd)) if cwd is not None else os.getcwd())
    suffixes = [""]
    if os.name == "nt":
        suffixes += os.environ.get("PATHEXT", ".EXE").split(os.pathsep)
    entries = [""] if os.path.dirname(head) else [os.fsdecode(entry) for entry in os.get_exec_path(env)]
    for entry in entries:
        for suffix in suffixes:
            candidate = os.path.join(base, entry, head + suffix)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def _same_file_as_git(program, env, cwd):
    """True when program is the same file (device and inode) as a git the parent or the child would find."""
    try:
        identity = os.stat(program)
    except OSError:
        return False
    for git in (_program(["git"], env, cwd), shutil.which("git")):
        try:
            if git is not None and os.path.samestat(identity, os.stat(git)):
                return True
        except OSError:
            continue
    return False


def _refused_head(argv, env, cwd, program=None):
    """A reason run() must not launch argv, or None. program is argv[0] as _program resolves it."""
    names = {os.path.basename(os.fsdecode(os.fspath(argv[0])))}
    if program is not None:
        names.add(os.path.basename(os.path.realpath(program)))
    for name in sorted({name.lower().removesuffix(".exe") for name in names}):
        if name.startswith("git"):
            return "its program resolves to git ({})".format(name)
        if name in _LAUNCHERS:
            return "its program is a shell or launcher ({}) whose arguments could run git".format(name)
    if program is not None and _same_file_as_git(program, env, cwd):
        return "its program is the git binary under another name"
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
                           "{} s); silence is not proof of a hang".format(idle, stall_s),
                           exc.output, exc.stderr) from None
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
    that leaves the watch also runs the staged stop (one bounded scan, at most REAP_S to reap); a
    monitoring fault leaves as Hung kind "monitor", an interrupt as itself once every stop stage was
    attempted. A Hung carries the exit status the child reached on its own before the stop, if any
    (Hung.returncode). argv is refused (ValueError) when its program is git or a known launcher; otherwise
    the resolved program is launched by its real path, and a head that resolves to nothing raises
    FileNotFoundError without a launch. Returns a CompletedProcess otherwise. Without input the child
    stdin is os.devnull, so a child that reads stdin sees EOF instead of blocking. cpu_seconds stands in
    for tree_cpu_seconds."""
    program = _program(argv, env, cwd)
    refused = _refused_head(argv, env, cwd, program)
    if refused is not None:
        # git launches stay on a direct subprocess call, where the maintenance-pin scan in
        # tools/selftest_git_fixture_env.py can read and enforce their pins.
        raise ValueError("_load_bound.run does not launch {!r}: {}".format(argv[0], refused))
    if program is None:
        raise FileNotFoundError(errno.ENOENT, "no executable program for this head (PATH from env, relative "
                                "entries from cwd); nothing was launched", os.fsdecode(os.fspath(argv[0])))
    cpu_seconds = tree_cpu_seconds if cpu_seconds is None else cpu_seconds
    clock = LoadClock() if clock is None else clock
    start, wall_start = clock(), time.monotonic()
    # executable= is the resolved real path, so the exec runs the program checked above and no other.
    proc = subprocess.Popen(argv, executable=os.path.realpath(program),
                            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, text=text,
                            errors=errors)
    try:
        stdout, stderr = _watch(proc, argv, input, budget_s, stall_s, clock, start, wall_start, poll_s,
                                cpu_seconds)
    except BaseException as exc:
        own_exit, notes, interrupt = _stop(proc)
        if interrupt is not None and isinstance(exc, Exception):
            raise interrupt from exc  # an interrupt during the stop leaves once every stage was attempted
        if not isinstance(exc, Exception):
            raise  # an interrupt or exit leaves as itself, after every stop stage was attempted
        hung = exc if isinstance(exc, Hung) else Hung(
            argv, budget_s, "monitor", "the bound's own monitoring failed ({}: {}); the child stop was run"
            .format(type(exc).__name__, exc))
        hung.reason += notes
        hung.output, hung.stderr = _as_text(hung.output, proc.stdout), _as_text(hung.stderr, proc.stderr)
        if own_exit is not None:
            hung.returncode = own_exit
            hung.reason += ("; the child itself had already exited with status {} before the stop (a "
                            "descendant still held its output open), and that status stands".format(own_exit))
        if hung is exc:
            raise
        raise hung from exc
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
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
    fault after the launch must still stop and reap the child, a fault or an interrupt in any one stop
    stage must not skip the stages after it, and a process-table scan is cut short at its limit; a child
    that exited nonzero while a leaked descendant held its pipe keeps its exit status (as text where text
    was asked for); git must not launch through a launcher, a relative or empty PATH entry (taken from the
    child cwd), or a PATH candidate the exec cannot run (the exec must not fall through to a later git).

    They do not need an unloaded host, but they are not independent of load: the progress vectors set the
    budget out of reach and pass whenever the child gets some CPU or prints within each stall window; the
    stretched-budget vector needs interpreter start-up and its prints to fit in about 13 s of its 16 s of
    budget; each stop vector allows a fixed wall limit (25 s, 10 s or 5 + REAP_S s) that a host starved
    past it would overrun. A load that extreme shows here as a failure, never as a pass. A non-terminating
    child is also killed by a watchdog after 30 s, so a regression shows as a failure here, never as a hung
    self-test."""
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

    # A fault or an interrupt in one stop stage must not skip the stages after it: each case ends with no
    # child left alive; an injected interrupt leaves as itself, a direct-kill fault as Hung kind "monitor".
    def fault_after_two_ticks():
        ticks = iter((0.0, 0.0))

        def clock():
            value = next(ticks, None)
            if value is None:
                raise RuntimeError("injected monitoring fault")
            return value
        return clock

    for stage, want in (("tree kill", "interrupt"), ("direct kill", "hung: monitor"), ("reap", "interrupt")):
        injected = KeyboardInterrupt("injected during the {}".format(stage))
        made, saved_popen, saved_tree = [], subprocess.Popen, _kill_tree

        class Faulty(saved_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made.append(self)

            def kill(self, stage=stage):
                if stage == "direct kill":
                    raise RuntimeError("injected direct-kill fault")
                super().kill()

            def wait(self, timeout=None, stage=stage, injected=injected):
                if stage == "reap" and timeout == REAP_S:
                    raise injected
                return super().wait(timeout)

        def interrupted_tree(proc, injected=injected):
            raise injected
        subprocess.Popen = Faulty
        if stage == "tree kill":
            globals()["_kill_tree"] = interrupted_tree
        try:
            run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token], budget_s=3600,
                stall_s=3600, poll_s=0.05, clock=fault_after_two_ticks())
            got = "returned"
        except Hung as exc:
            got = "hung: " + exc.kind
        except KeyboardInterrupt as exc:
            if exc is not injected:
                raise
            got = "interrupt"
        except Exception as exc:  # noqa: BLE001  any other escape is this vector's failure
            got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_popen
            globals()["_kill_tree"] = saved_tree
        left = _alive_with(token)
        for _ in range(50):  # a SIGKILLed child can take a moment to leave the process table
            if not left:
                break
            time.sleep(0.1)
            left = _alive_with(token)
        for pid in left:
            _kill_pid(pid)
        for proc in made:
            try:
                saved_popen.wait(proc, timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
        if (got, left) != (want, []):
            failures.append("_load_bound vector (a fault in the {} stop stage): expected {!r} and no child left, "
                            "got {!r}, left alive {!r}".format(stage, want, got, left))

    # A process-table scan is cut short at its limit, and a scan cut short reads as unreadable CPU.
    if tree_cpu_seconds(os.getpid()) is not None:
        import builtins

        def slow_open(path, *args, **kwargs):
            time.sleep(0.05)
            return builtins.open(path, *args, **kwargs)
        globals()["open"] = slow_open
        began = time.monotonic()
        try:
            table, complete = _proc_table(limit_s=0.5)
        finally:
            del globals()["open"]
        took = time.monotonic() - began
        if complete or took > 5.0 or tree_cpu_seconds(os.getpid(), limit_s=0.0) is not None:
            failures.append("_load_bound vector (a slow process-table scan is cut short at its limit): got "
                            "complete={!r} after {:.1f} s".format(complete, took))

    # A child that exits nonzero while a leaked descendant still holds its output keeps its exit status.
    if hasattr(os, "waitid") and tree_cpu_seconds(os.getpid()) is not None:
        leaked = "load-bound-leaked-{}".format(os.getpid())
        code = ("import subprocess, sys\nprint(\"FAIL\", flush=True)\nsubprocess.Popen([sys.executable, "
                "\"-I\", \"-c\", \"import time; time.sleep(60)\", sys.argv[2]])\nsys.exit(3)\n")
        try:
            done = run([python, "-I", "-B", "-c", code, token, leaked], budget_s=60, stall_s=1.5, poll_s=0.2,
                       text=True)
            got = ("returned", done.returncode)
        except Hung as exc:
            got = (exc.kind, exc.returncode, isinstance(exc.output, str) and "FAIL" in exc.output)
        except Exception as exc:  # noqa: BLE001  an escape is this vector's failure
            got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            for pid in _alive_with(leaked):
                _kill_pid(pid)
        if got != ("stall", 3, True):
            failures.append("_load_bound vector (a nonzero exit beside a leaked descendant keeps its status, as "
                            "text): expected (stall, 3, True), got {!r}".format(got))

    clock = LoadClock(wall=iter((0.0, 10.0, 20.0)).__next__, factor=iter((1.0, 3.0, 1.0)).__next__)
    readings = (clock(), clock())
    if readings != (5.0, 10.0):
        failures.append("_load_bound vector (load clock integrates wall / mean factor): got {!r}"
                        .format(readings))

    # git is refused however the head reaches it: through a launcher, a symlink, a relative or empty PATH
    # entry (taken from the child cwd), and a first PATH candidate the exec cannot run must not fall through
    # to a later git. An interpreter head is not refused, and a relative PATH entry finds a program in the
    # child cwd.
    scratch = tempfile.mkdtemp(prefix="load-bound-vector-")
    try:
        heads = [(["git", "--version"], None, None), (["/usr/bin/env", "git", "--version"], None, None),
                 (["env", "-i", "git", "--version"], None, None), (["nice", "-n", "1", "git", "--version"], None, None),
                 (["sh", "-c", "git --version"], None, None), (["setarch", "x86_64", "git", "--version"], None, None)]
        real_git = shutil.which("git")
        if real_git is not None:
            alias = os.path.join(scratch, "tool")
            os.symlink(real_git, alias)
            os.makedirs(os.path.join(scratch, "bin"))
            os.symlink(real_git, os.path.join(scratch, "bin", "tool"))
            heads += [([real_git, "--version"], None, None), ([alias, "--version"], None, None),
                      (["tool", "--version"], scratch, {"PATH": "bin"}),
                      (["tool", "--version"], scratch, {"PATH": os.pathsep + "/nonexistent"})]
        for argv, cwd, env in heads:
            try:
                run(argv, budget_s=10, stall_s=10, cwd=cwd, env=env)
                failures.append("_load_bound vector (git refused through {!r}, PATH {!r}): it launched"
                                .format(argv, (env or {}).get("PATH")))
            except ValueError:
                pass
            except Exception as exc:  # noqa: BLE001  not refused as git: this vector's failure
                failures.append("_load_bound vector (git refused through {!r}, PATH {!r}): not refused ({}: {})"
                                .format(argv, (env or {}).get("PATH"), type(exc).__name__, exc))
        if real_git is not None:
            first, second = os.path.join(scratch, "one"), os.path.join(scratch, "two")
            os.makedirs(first)
            os.makedirs(second)
            with open(os.path.join(first, "probe"), "wb") as handle:
                handle.write(b"\x00 not an executable format\n")
            os.chmod(os.path.join(first, "probe"), 0o755)
            os.symlink(real_git, os.path.join(second, "probe"))
            try:
                done = run(["probe", "--version"], budget_s=10, stall_s=10, env={"PATH": first + os.pathsep + second})
                failures.append("_load_bound vector (an exec failure on the first PATH candidate falls through "
                                "to git): it launched {!r}".format(done.stdout[:40]))
            except (OSError, ValueError):
                pass
        own = os.path.join(scratch, "own", "bin")
        os.makedirs(own)
        with open(os.path.join(own, "probe"), "w", encoding="utf-8") as handle:
            handle.write("{}!{}\nprint(\"from the child cwd\")\n".format(chr(35), python))
        os.chmod(os.path.join(own, "probe"), 0o755)
        try:
            done = run(["probe"], budget_s=60, stall_s=60, cwd=os.path.dirname(own), env={"PATH": "bin"},
                       text=True)
            got = (done.returncode, done.stdout)
        except Exception as exc:  # noqa: BLE001  an escape is this vector's failure
            got = "{}: {}".format(type(exc).__name__, exc)
        if got != (0, "from the child cwd\n"):
            failures.append("_load_bound vector (a relative PATH entry finds a program in the child cwd): got "
                            "{!r}".format(got))
        if outcome("pass\n", budget_s=60, stall_s=60) != 0:
            failures.append("_load_bound vector (an interpreter head is not refused as git)")
    finally:
        shutil.rmtree(scratch)
    return failures
