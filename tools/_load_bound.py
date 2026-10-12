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
Hung.returncode carries that exit status, and the caller judges it exactly as it judges the exit status
of a completed run, whatever bound fired, never filing it as cannot-evaluate: a nonzero status where the
caller wanted success is a FAILURE (exit 1), and in the tools/check_gensrc_failclose.py probes, where a
nonzero --check exit is the wanted rejection of a corrupt target, that status stands as the rejection.
The other evidence a caller captured beside the stop is judged by the contract of that caller, never
filed as cannot-evaluate where that contract calls it a failure: tools/selftest_git_fixture_env.py and
the pl239 cases of tools/selftest_aiqt_hooks.py treat captured stderr, a wrong result or a leak marker
beside a stop as a FAILURE (exit 1), while tools/check_gensrc_failclose.py and
tools/check_hooks_preview.py judge the exit status of a member alone (a generator prints its rejection
to stderr, a hook self-test its progress), so captured stderr beside a stop is not failure evidence in
those two and a stop with no exit status stays cannot-evaluate there. A fault in the monitoring itself
keeps the stdout and stderr captured before it: the Hung of kind monitor carries them, so that evidence
is judged the same way. Where tree CPU time cannot be read (no /proc, or a process-table scan cut short at SCAN_S), or once
it becomes unreadable during a run, the stall test is off for the rest of that run and only the
load-adjusted budget applies.

Cleanup. Once the watch has begun (Popen has returned), any exception that leaves the watch (a bound
firing, a fault in the monitoring itself, an interrupt) runs the stop before run() leaves: read whether
the child already exited on its own, kill the process tree, kill the child directly, then reap it waiting
at most REAP_S. No stage signals a reaped or reused pid: the direct child is signalled only through its
own Popen, which polls first and skips a child it has already reaped (while the parent holds a child
unreaped, that pid cannot be reused), and each descendant the kill scan finds is bound to a pidfd as it
is found, its start time re-read under that pidfd, and signalled only through the pidfd, so a pid whose
process was replaced after the scan is never signalled; a descendant that cannot be bound (pidfd_open
unavailable or failing, or the identity no longer matching) is not signalled, and the stop reports it in
the Hung reason. Once Popen has reaped the child, its pid is reusable and its descendants were already
reparented away at its exit, so no kill scan is rooted at that pid at all. Each stage runs in its own guard, so a fault in one stage is noted in the report and the
next stage still runs, and an interrupt (KeyboardInterrupt, SystemExit) that arrives during a stage is held
until every stage has been attempted and is then re-raised. A fault in the monitoring is raised as Hung
(kind "monitor"), cannot-evaluate, never a crash that looks like a failure. What this does not cover: an
interrupt that lands outside a stage guard (inside Popen itself, between Popen returning and the watch,
or between two stage guards), a child killed but left unreaped because the interrupt landed in the reap
stage, a child that survives both kills because every kill stage failed, a child
in uninterruptible sleep (left unreaped after REAP_S, not waited on without bound), a single /proc
read that blocks in the kernel (the scan is bounded between entries, not inside one read), and the part
of the tree a kill scan cut short at SCAN_S never reached: those descendants are left unkilled (the
stall test already reads such a scan as unreadable CPU, but the kill stage runs no second scan).

git. run() does not knowingly launch git (ValueError before any launch). It resolves the program the
exec in the child would run, the way that exec looks it up: a head with a directory part relative to cwd,
a bare head through each PATH entry of env in order, a relative or empty entry taken relative to cwd. It
refuses a program whose given or real (symlink-resolved) name is git, or that is the same file as the git
the parent or the child would find (a hard link), and refuses a head whose name is one of _LAUNCHERS. It
then launches that resolved real path itself (Popen executable=), resolved once: the same real-path
string the refusal checked is the one handed to the exec, so the lookup and the exec cannot disagree (a
candidate the exec would skip is not passed over to a later PATH entry, and a symlink retargeted after
the check does not redirect the exec). _LAUNCHERS is a list
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
unverified on Windows; the file at the resolved real path swapped between the lookup and the exec runs
as swapped (the path itself is resolved once, so only that one path can be run).
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


def measured_load_factor(read=None):
    """max(1, 1-minute load average / usable CPUs), capped at MAX_LOAD_FACTOR, or None when the reading
    is unavailable or is not a finite, non-negative number: an unknown load is never handed back as a
    measured one. The load average is host-wide and smoothed, and a CPU quota, CPU pinning or niceness
    that starves one callee does not show in it at all, so even a low measured factor is no proof the
    callee had CPU. read stands in for os.getloadavg."""
    try:
        load = (read or os.getloadavg)()[0]
    except Exception:  # noqa: BLE001  an unreadable load is unknown, never an error in the bound
        return None
    if not _real(load) or load < 0:
        return None
    cpus = getattr(os, "process_cpu_count", os.cpu_count)()
    cpus = cpus if isinstance(cpus, int) and not isinstance(cpus, bool) and cpus >= 1 else 1
    return min(MAX_LOAD_FACTOR, max(1.0, load / cpus))


def load_factor(read=None):
    """measured_load_factor, with an unknown reading (unavailable or malformed) counted as 1.0."""
    measured = measured_load_factor(read)
    return 1.0 if measured is None else measured


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


def _proc_table(limit_s=None, names=None, clock=None, opener=None):
    """(table, complete): table is {pid: (ppid, utime+stime+cutime+cstime ticks, start ticks)} for
    every readable /proc/<pid>/stat, or None when none was read. The scan stops between entries once it
    has taken limit_s (SCAN_S) wall seconds; a scan cut short, or a /proc that cannot be listed, is not
    complete. names, clock and opener stand in for the /proc listing, time.monotonic and open, so the
    cut-short vector drives a table and a clock that must cross the limit on any host."""
    limit_s = SCAN_S if limit_s is None else limit_s
    clock = time.monotonic if clock is None else clock
    opener = open if opener is None else opener
    if names is None:
        try:
            names = os.listdir("/proc")
        except OSError:
            return None, False
    began = clock()
    table = {}
    for name in names:
        if clock() - began >= limit_s:
            return table or None, False
        if not name.isdigit():
            continue
        try:
            with opener("/proc/" + name + "/stat", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        fields = raw[raw.rfind(b")") + 2:].split()
        try:
            table[int(name)] = (int(fields[1]), sum(int(value) for value in fields[11:15]),
                                int(fields[19]))
        except (IndexError, ValueError):
            continue
    return table or None, True


def _descendants(table, pid):
    children = {}
    for child, entry in table.items():
        children.setdefault(entry[0], []).append(child)
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
    output open), or None when the stop ended it or the status could not be read: the caller judges the
    returncode exactly as it judges the exit status of a completed run, never filing it as
    cannot-evaluate. output and stderr hold what was captured before the stop, also when the monitoring
    itself failed (a Hung of kind monitor keeps them), as str when run() was given text=True or
    errors."""

    def __init__(self, cmd, timeout, kind, reason, output=None, stderr=None, returncode=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.reason = reason
        self.returncode = returncode

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


def _signal_descendant(pid, start_ticks):
    """Signal one scanned descendant, only ever through a pidfd bound here: the pidfd pins whatever
    process holds pid at the bind, and the start time re-read under that pin must equal the scanned one
    (a pid is only reusable after its holder was reaped, and a later holder starts later), so a pid
    reused after the scan is never signalled. Returns "" when the signal was delivered or the
    process was already gone, else why the descendant was not signalled."""
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if opener is None or sender is None:
        return "pidfd_open or pidfd_send_signal is unavailable on this platform"
    try:
        fd = opener(pid)
    except OSError as exc:
        return "pidfd_open failed ({}: {})".format(type(exc).__name__, exc)
    try:
        try:
            with open("/proc/{}/stat".format(pid), "rb") as handle:
                raw = handle.read()
            held_start = int(raw[raw.rfind(b")") + 2:].split()[19])
        except (OSError, IndexError, ValueError) as exc:
            return "its identity could not be re-read under the pidfd ({}: {})".format(
                type(exc).__name__, exc)
        if held_start != start_ticks:
            return ("the pid was reused after the scan (start ticks {} where the scan read {})"
                    .format(held_start, start_ticks))
        try:
            sender(fd, _KILL)
        except ProcessLookupError:
            pass  # it exited after the bind; the pidfd names that process alone, so nothing else is hit
        except OSError as exc:
            return "pidfd_send_signal failed ({}: {})".format(type(exc).__name__, exc)
        return ""
    finally:
        os.close(fd)


def _kill_tree(proc):
    """Kill the scanned descendants of proc, each only through _signal_descendant (a pidfd bound at
    the scan), and proc itself only through its own Popen: send_signal polls first and skips a child it
    has already reaped, and the pid of a child the parent holds unreaped cannot be reused, so no stage
    here signals a reaped or reused pid numerically. Once Popen has reaped proc, its descendants were
    already reparented away at its exit, so no scan is rooted at its (reusable) pid. Returns "" or a
    note naming each descendant that was not signalled; the part of the tree a scan cut short at SCAN_S
    never reached is left unkilled (module docstring)."""
    notes = []
    if proc.returncode is None:
        table, _complete = _proc_table()
        if table and proc.pid in table:
            for member in _descendants(table, proc.pid):
                if member == proc.pid:
                    continue
                why = _signal_descendant(member, table[member][2])
                if why:
                    notes.append("descendant {} was not signalled: {}".format(member, why))
    try:
        proc.send_signal(_KILL)
    except OSError:
        pass
    return "; ".join(notes)


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
                skipped = _kill_tree(proc)
                if skipped:
                    notes.append(skipped)
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


def _refused_head(argv, env, cwd, program=None, real=None):
    """A reason run() must not launch argv, or None. program is argv[0] as _program resolves it, and
    real its real path, resolved ONCE by run() and handed both here and to the exec, so the path this
    refusal checks is the path that runs."""
    names = {os.path.basename(os.fsdecode(os.fspath(argv[0])))}
    if program is not None:
        names.add(os.path.basename(os.path.realpath(program) if real is None else real))
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


def _captured_streams(exc):
    """The (output, stderr) a subprocess.TimeoutExpired in the exception chain of exc captured before
    a monitoring fault, so a Hung of kind monitor keeps that evidence; (None, None) when the chain holds
    none."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, subprocess.TimeoutExpired):
            return exc.output, exc.stderr
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return None, None


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
    real = None if program is None else os.path.realpath(program)
    refused = _refused_head(argv, env, cwd, program, real)
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
    # executable= is the real path the refusal above checked, resolved once, so the exec runs that path
    # and no other; only the file at it swapped afterwards runs as swapped (module docstring).
    proc = subprocess.Popen(argv, executable=real,
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
        if isinstance(exc, Hung):
            hung = exc
        else:
            captured_out, captured_err = _captured_streams(exc)
            hung = Hung(argv, budget_s, "monitor",
                        "the bound's own monitoring failed ({}: {}); the child stop was run"
                        .format(type(exc).__name__, exc), captured_out, captured_err)
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
    fault after the launch must still stop and reap the child, a monitoring fault keeps the streams
    captured before it, a fault or an interrupt in any one stop stage (the status read included) must
    not skip the stages after it, and a process-table scan is cut short at its limit, driven by an
    injected table and clock so a small real process table cannot pass it vacuously; a child Popen has
    already reaped is never signalled numerically, a pid reused after the kill scan is never signalled
    (an unbindable descendant is skipped and reported), and a live descendant is killed through its
    pidfd; a child that exited nonzero while a leaked descendant held its pipe keeps its exit status (as
    text where text was asked for); git must not launch through a launcher, a relative or empty PATH
    entry (taken from the child cwd), a PATH candidate the exec cannot run (the exec must not fall
    through to a later git), or a hard link to git under another name.

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

    for stage, want in (("status read", "interrupt"), ("tree kill", "interrupt"),
                        ("direct kill", "hung: monitor"), ("reap", "interrupt")):
        injected = KeyboardInterrupt("injected during the {}".format(stage))
        made, saved_popen, saved_tree, saved_own = [], subprocess.Popen, _kill_tree, _own_exit

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

        def interrupted_own_exit(proc, injected=injected):
            raise injected
        subprocess.Popen = Faulty
        if stage == "tree kill":
            globals()["_kill_tree"] = interrupted_tree
        elif stage == "status read":
            globals()["_own_exit"] = interrupted_own_exit
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
            globals()["_own_exit"] = saved_own
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

    # A process-table scan is cut short at its limit, driven by an injected table and clock that must
    # cross SCAN_S, so a small real process table cannot complete early and pass this vacuously; a zero
    # real limit still reads as unreadable CPU where /proc exists.
    def fixture_stat(path, *args, **kwargs):
        return io.BytesIO(b"99 (probe) S 1 0 0 0 0 0 0 0 0 0 4 4 0 0 20 0 1 0 7")
    crossing = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    table, complete = _proc_table(names=["11", "12", "13", "14", "15"],
                                  clock=lambda: next(crossing, 6.0), opener=fixture_stat)
    held = iter((0.0,) * 8)
    full, full_complete = _proc_table(names=["11", "12", "13", "14", "15"],
                                      clock=lambda: next(held, 0.0), opener=fixture_stat)
    got = (complete, sorted(table or {}), full_complete, sorted(full or {}))
    if got != (False, [11, 12, 13, 14], True, [11, 12, 13, 14, 15]):
        failures.append("_load_bound vector (a process-table scan is cut short at its limit, on an "
                        "injected table and clock): got {!r}".format(got))
    if tree_cpu_seconds(os.getpid()) is not None and tree_cpu_seconds(os.getpid(), limit_s=0.0) is not None:
        failures.append("_load_bound vector (a scan cut short reads as unreadable CPU): got a reading")

    # A monitoring fault keeps the streams captured before it: the monitor Hung carries them, so the
    # caller can judge that evidence; the stop still kills and reaps the real child.
    class SeededTimeout(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(self.args, timeout, output=b"OUT\n",
                                            stderr=b"FAIL: seeded diagnostic\n")

    fault_ticks = iter((0.0, 0.0))

    def seeded_fault_clock():
        value = next(fault_ticks, None)
        if value is None:
            raise RuntimeError("injected monitoring fault")
        return value
    saved_popen_cls = subprocess.Popen
    subprocess.Popen = SeededTimeout
    try:
        try:
            run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token], budget_s=3600,
                stall_s=3600, poll_s=0.05, clock=seeded_fault_clock)
            got = "returned"
        except Hung as exc:
            got = (exc.kind, exc.output, exc.stderr)
        except Exception as exc:  # noqa: BLE001  any escape is this vector failing
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        subprocess.Popen = saved_popen_cls
    for pid in _alive_with(token):
        _kill_pid(pid)
    if got != ("monitor", b"OUT\n", b"FAIL: seeded diagnostic\n"):
        failures.append("_load_bound vector (a monitoring fault keeps the captured streams): got {!r}"
                        .format(got))

    # A child Popen has already reaped (here a text decode that fails after a clean exit) is never
    # signalled numerically: its pid may already be reused. The monitor Hung keeps the exit status.
    numeric_kills = []
    saved_os_kill = os.kill

    def recording_kill(pid, sig):
        numeric_kills.append(pid)
        return saved_os_kill(pid, sig)
    os.kill = recording_kill
    try:
        try:
            run([python, "-I", "-B", "-c", "import sys\nsys.stdout.buffer.write(bytes([255]))\n", token],
                budget_s=60, stall_s=60, text=True)
            got = "returned"
        except Hung as exc:
            got = (exc.kind, exc.returncode, numeric_kills)
        except Exception as exc:  # noqa: BLE001  any escape is this vector failing
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        os.kill = saved_os_kill
    if got != ("monitor", 0, []):
        failures.append("_load_bound vector (a reaped child is never signalled numerically): expected "
                        "(monitor, 0, []), got {!r}".format(got))
    if hasattr(os, "pidfd_open") and tree_cpu_seconds(os.getpid()) is not None:
        # A live descendant of a stopped child is killed through its pidfd (the delivery control for
        # the reuse refusal below).
        bounded("a live descendant is killed with the tree, through its pidfd",
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, \"-I\", \"-c\","
                " \"import time; time.sleep(3600)\", sys.argv[1]])\ntime.sleep(3600)\n",
                "hung: stall", 25, budget_s=3600, stall_s=1.5, poll_s=0.2)
        # A pid reused after the scan is never signalled: the start time read under the pidfd no longer
        # matches the scanned one, so that descendant is skipped and reported; a pid that cannot be
        # bound at all is skipped and reported too. The bystander (the reuser) must survive.
        def scanned_start(pid):
            with open("/proc/{}/stat".format(pid), "rb") as handle:
                raw = handle.read()
            return int(raw[raw.rfind(b")") + 2:].split()[19])
        bystander = subprocess.Popen(["sleep", "3600"])
        child = subprocess.Popen(["sleep", "3600"])
        gone = subprocess.Popen(["sleep", "0"])
        gone.wait()
        saved_table = _proc_table
        try:
            crafted = {child.pid: (os.getpid(), 0, scanned_start(child.pid)),
                       bystander.pid: (child.pid, 0, scanned_start(bystander.pid) + 1),
                       gone.pid: (child.pid, 0, 1)}
            globals()["_proc_table"] = lambda *args, **kwargs: (crafted, True)
            note = _kill_tree(child)
            time.sleep(0.2)
            got = (bystander.poll(),
                   "descendant {} was not signalled".format(bystander.pid) in note and "reused" in note,
                   "descendant {} was not signalled".format(gone.pid) in note)
        finally:
            globals()["_proc_table"] = saved_table
            for stopped in (bystander, child):
                stopped.kill()
                try:
                    stopped.wait(timeout=REAP_S)
                except subprocess.TimeoutExpired:
                    pass
        if got != (None, True, True):
            failures.append("_load_bound vector (a reused or unbindable pid is never signalled, and "
                            "the stop reports it): got {!r}".format(got))

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
        # A hard link to the git the CHILD would find is refused by file identity (_same_file_as_git),
        # driven with files this vector owns; the same bytes under a different inode are the disclosed
        # copy residual and must still launch, so the refusal is the identity of the link, not its name.
        fake = os.path.join(scratch, "fakegit")
        os.makedirs(fake)
        with open(os.path.join(fake, "git"), "w", encoding="utf-8") as handle:
            handle.write("{}!{}\nprint(\"fake-git ran\")\n".format(chr(35), python))
        os.chmod(os.path.join(fake, "git"), 0o755)
        os.link(os.path.join(fake, "git"), os.path.join(fake, "linked"))
        shutil.copy2(os.path.join(fake, "git"), os.path.join(fake, "copied"))
        try:
            run(["linked"], budget_s=10, stall_s=10, cwd=fake, env={"PATH": fake})
            failures.append("_load_bound vector (a hard link to the git the child would find is "
                            "refused by identity): it launched")
        except ValueError:
            pass
        except Exception as exc:  # noqa: BLE001  not refused by identity: this vector failing
            failures.append("_load_bound vector (a hard link to the git the child would find is "
                            "refused by identity): not refused ({}: {})"
                            .format(type(exc).__name__, exc))
        try:
            done = run(["copied"], budget_s=60, stall_s=60, cwd=fake, env={"PATH": fake}, text=True)
            got = (done.returncode, done.stdout)
        except Exception as exc:  # noqa: BLE001  an escape is this vector failing
            got = "{}: {}".format(type(exc).__name__, exc)
        if got != (0, "fake-git ran\n"):
            failures.append("_load_bound vector (a copy of that file under a different inode still "
                            "launches, the disclosed residual): got {!r}".format(got))
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
