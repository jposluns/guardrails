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
Hung.returncode carries that exit status. A NONZERO status there is judged exactly as the nonzero exit
of a completed run, whatever bound fired, never filed as cannot-evaluate: a failure where the caller
wanted success, and in the tools/check_gensrc_failclose.py probes, where a nonzero --check exit is the
wanted rejection of a corrupt target, that status stands as the rejection (after the same post-probe
containment validation a completed rejection gets). An own exit of 0 beside a stop proves less than a
completed 0 (the stop may have cut the result short), so each caller states its own treatment, every
one conservative: tools/selftest_git_fixture_env.py files it as a bound stop (cannot-evaluate) unless
failure-marker stderr stands beside it; tools/check_hooks_preview.py files it as unverifiable
(cannot-evaluate); the pl239 cases of tools/selftest_aiqt_hooks.py retry it exactly like a clean bound
stop when no stderr was captured, ending cannot-evaluate; only the tools/check_gensrc_failclose.py
probes let it stand as a completed 0, because exit 0 on a corrupt target is the F-154 violation there,
so cannot-evaluate would be the lenient direction. The other evidence captured beside a stop is judged
by the contract of each caller: tools/selftest_git_fixture_env.py treats a hook, monitor or exposure
marker or a wrong value beside a stop as a FAILURE (exit 1), and captured stderr as failure evidence
only when it carries a failure marker the completed path would also fail on (a Traceback, FAIL or
ERROR line), never the unittest progress (dots, "Ran N tests") its suites write to stderr in passing
runs too; the pl239 cases of tools/selftest_aiqt_hooks.py treat captured stderr or a nonzero own exit
beside a stop as a FAILURE (exit 1) and do not consult stdout beside a stop (a wrong verdict held
there ends cannot-evaluate after the retries); tools/check_gensrc_failclose.py and
tools/check_hooks_preview.py judge the exit status of a member alone (a generator prints its rejection
to stderr, a hook self-test its progress), so captured stderr beside a stop is not failure evidence in
those two and a stop with no exit status stays cannot-evaluate there. Failure evidence survives every
stop and every monitoring fault: the watch keeps the bytes captured so far on every poll, in state
independent of any exception, every Hung (kind monitor included) is built from that state, and text
decoding happens only AFTER capture, so a fault in a later communicate call, or a decode failure
itself, cannot lose them: the Hung carries the captured streams (under text=True decoded with the
requested errors, falling back to errors="replace" where that decode raises), judged the same way. Where tree CPU time cannot be read (no /proc, or a process-table scan cut short at SCAN_S), or once
it becomes unreadable during a run, the stall test is off for the rest of that run and only the
load-adjusted budget applies.

Cleanup. run() launches the child as the LEADER OF A NEW SESSION and process group
(start_new_session) and binds a pidfd to it at the spawn, while it is an unreaped child of this
process. Once the watch has begun (Popen has returned), any exception that leaves the watch (a bound
firing, a fault in the monitoring itself, an interrupt) runs the stop before run() leaves: read whether
the child already exited on its own, kill the child's own process group, kill the child directly, then
reap it waiting at most REAP_S. No process-table scan selects victims and no stage signals a numeric
pid that could have been reused: the group kill addresses the group through the leader's own pid
record (pidfd_send_signal with PIDFD_SIGNAL_PROCESS_GROUP), which a reused pid number never names, so
it is exact before AND after Popen has reaped the leader; without a usable pidfd, or where the kernel
refuses the flag (EINVAL, before Linux 6.9), the group is named by number (os.killpg) ONLY while the
leader is unreaped (its pid, and with it the group id, stays allocated until the reap), and once Popen
has reaped the leader nothing is signalled by number at all, the fail-closed fallback, reported in the
Hung reason; the direct kill goes only through the child's own Popen, which polls first and skips a
child it has already reaped. A bystander outside the child's group is never selected (there is no scan
whose table could redirect a signal), and a descendant that LEFT the group (setsid or setpgid) is not
hunted down: it survives as the disclosed residual below, while the output it held was captured by the
watch and stays on the Hung. Each stage runs in its own guard, so a fault in one stage is noted in the report and the
next stage still runs, and an interrupt (KeyboardInterrupt, SystemExit) that arrives during a stage is held
until every stage has been attempted and is then re-raised. A fault in the monitoring is raised as Hung
(kind "monitor"), cannot-evaluate, never a crash that looks like a failure. What this does not cover: an
interrupt that lands outside a stage guard (inside Popen itself, between Popen returning and the watch,
or between two stage guards), a child killed but left unreaped because the interrupt landed in the reap
stage, a child that survives both kills because every kill stage failed, a child
in uninterruptible sleep (left unreaped after REAP_S, not waited on without bound), a single /proc
read that blocks in the kernel (the stall test's scan is bounded between entries, not inside one
read), and a descendant that left the child's process group before the stop: that one is not signalled
and is left running (no scan hunts it; the stall test's bounded process-table scan only ever reads
CPU, never selects victims).

git. run() does not knowingly launch git (ValueError before any launch). It resolves the program the
exec in the child would run, the way that exec looks it up: a head with a directory part relative to cwd,
a bare head through each PATH entry of env in order, a relative or empty entry taken relative to cwd. It
refuses a program whose given or real (symlink-resolved) name is git, or that is the same file as the git
the parent or the child would find (a hard link), and refuses a head whose name is one of _LAUNCHERS. It
then launches that resolved real path itself (Popen executable=), resolved once: the same real-path
string is what the name refusals AND the git file-identity check inspect and what is handed to the
exec, so the lookup, the checks and the exec cannot disagree (a candidate the exec would skip is not
passed over to a later PATH entry, and an alias retargeted after the resolution redirects neither the
identity check nor the exec). _LAUNCHERS is a list
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
descendant that leaves the child's process group (setsid or setpgid; a daemon also re-parents and so
leaves the CPU sum) is not killed, and while
the child waits on such a descendant the child looks silent; without /proc (macOS, Windows) the stall
test is off; without POSIX process groups (Windows, where start_new_session is ignored) only the
direct child is killed, and without the pidfd group signal a stop that comes after Popen reaped the
leader kills no group member (disclosed in the Hung reason); without os.waitid the exit status a child reached
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
# Linux's PIDFD_SIGNAL_PROCESS_GROUP (include/uapi/linux/pidfd.h, Linux 6.9 and later): a
# pidfd_send_signal flag that signals the process group the pidfd's process leads, the same mechanism
# tools/selftest_git_fixture_env.py uses for its member groups; a kernel without it refuses the flag
# with EINVAL and the stop falls back as _kill_group states.
PIDFD_SIGNAL_PROCESS_GROUP = getattr(signal, "PIDFD_SIGNAL_PROCESS_GROUP", 1 << 2)
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
    output open), or None when the stop ended it or the status could not be read: the caller judges a
    NONZERO returncode exactly as it judges the exit status of a completed run, never filing it as
    cannot-evaluate; an own exit of 0 beside a stop is judged by each caller's stated contract (module
    docstring), conservatively. output and stderr hold what was captured before the stop, kept in state
    independent of any exception (a Hung of kind monitor keeps them even when a later communicate call
    faulted or the text decode itself failed), as str when run() was given text=True or errors (decoded
    AFTER capture with the requested errors, falling back to errors="replace" where that raises)."""

    def __init__(self, cmd, timeout, kind, reason, output=None, stderr=None, returncode=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.reason = reason
        self.returncode = returncode

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


def _kill_group(proc, pidfd):
    """SIGKILL the process group proc leads (run() starts the child with start_new_session, so the
    group is the child and every descendant that has not left it; no process-table scan selects
    victims). With pidfd (bound at the spawn, while the leader was an unreaped child), the kernel
    addresses the group through the leader's own pid record (pidfd_send_signal with
    PIDFD_SIGNAL_PROCESS_GROUP), which a reused pid number never names, so the kill is exact before
    AND after Popen has reaped the leader. Without one, or where the kernel refuses the flag (EINVAL,
    before Linux 6.9), the group is named by number (os.killpg), exact only while the leader is
    unreaped (its pid, and with it the group id, stays allocated until the reap); once Popen has
    reaped the leader nothing is signalled by number at all, the fail-closed fallback. Returns ""
    when the group was signalled or has no process left, else why it was not signalled."""
    sender = getattr(signal, "pidfd_send_signal", None)
    if pidfd is not None and sender is not None:
        try:
            sender(pidfd, _KILL, None, PIDFD_SIGNAL_PROCESS_GROUP)
            return ""
        except ProcessLookupError:
            return ""  # the group has no process left; the pidfd names that group alone
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                return "pidfd_send_signal failed ({}: {})".format(type(exc).__name__, exc)
            # EINVAL: this kernel lacks the group flag; fall back to the numeric group id below.
    if proc.returncode is not None:
        return ("the leader was already reaped and no pidfd group signal was available, so its group "
                "was not signalled by number (a reused group id must never be signalled); a "
                "descendant still in that group survives the stop")
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        return "os.killpg is unavailable on this platform; only the direct kill stage runs"
    try:
        killpg(proc.pid, _KILL)
    except ProcessLookupError:
        pass  # the group is already empty
    except OSError as exc:
        return "killpg failed ({}: {})".format(type(exc).__name__, exc)
    return ""


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


def _stop(proc, pidfd):
    """Stop proc in four stages: read the status it reached on its own, kill the process group it
    leads (_kill_group, through pidfd where one was bound at the spawn), kill it directly, reap it
    (waiting at most REAP_S). Each stage runs in its own guard: a fault in one is noted
    and the next still runs, and an interrupt arriving during a stage is held until every stage has been
    attempted. Returns (own_exit, note, interrupt): the status proc reached before the stop or None, ""
    or a note naming each stage that failed, and the first held interrupt or None (the caller re-raises
    it)."""
    own_exit, notes, interrupt = None, [], None
    for stage in ("status read", "group kill", "direct kill", "reap"):
        try:
            if stage == "status read":
                own_exit = _own_exit(proc)
            elif stage == "group kill":
                skipped = _kill_group(proc, pidfd)
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


def _decode(data, encoding, errors):
    """Captured bytes decoded as Popen's text mode would decode them (newlines translated). Decoding
    happens only AFTER capture: a failure here can raise (under errors="strict") without losing the
    captured bytes, which stay in run()'s held state."""
    if not isinstance(data, bytes):
        return data
    return data.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n")


def _decode_replace(data, encoding, errors):
    """_decode, falling back to errors="replace" where the requested errors raise (a stop can cut a
    multibyte character in half, and a Hung must keep the evidence): never an exception."""
    try:
        return _decode(data, encoding, errors)
    except (UnicodeError, LookupError):
        return _decode(data, encoding, "replace")


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
    refusal checks (the name refusals and the git file-identity check alike) is the path that runs,
    and an alias retargeted after the resolution cannot redirect the identity check."""
    names = {os.path.basename(os.fsdecode(os.fspath(argv[0])))}
    if program is not None:
        names.add(os.path.basename(os.path.realpath(program) if real is None else real))
    for name in sorted({name.lower().removesuffix(".exe") for name in names}):
        if name.startswith("git"):
            return "its program resolves to git ({})".format(name)
        if name in _LAUNCHERS:
            return "its program is a shell or launcher ({}) whose arguments could run git".format(name)
    if program is not None and _same_file_as_git(program if real is None else real, env, cwd):
        return "its program is the git binary under another name"
    return None


def _watch(proc, argv, pending, budget_s, stall_s, clock, start, wall_start, poll_s, cpu_seconds, held):
    """Wait for proc under both bounds; return (stdout, stderr) bytes, or raise Hung (kind stall or
    budget) carrying the bytes captured so far. Every poll also keeps those bytes in held (a two-slot
    list run() owns), state independent of any exception, so a fault in a LATER communicate call or in
    the monitoring itself can never lose evidence an earlier poll had already captured."""
    seen_cpu, seen_output, cpu_readable = None, -1, True
    last_progress = wall_start
    while True:
        try:
            stdout, stderr = proc.communicate(pending, timeout=poll_s)
        except subprocess.TimeoutExpired as exc:
            pending = None
            if exc.output is not None:  # cumulative; a later poll must never erase earlier evidence
                held[0] = exc.output
            if exc.stderr is not None:
                held[1] = exc.stderr
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
                           held[0], held[1]) from None
            if clock() - start >= budget_s:
                raise Hung(argv, budget_s, "budget",
                           "load-adjusted budget of {} s spent after {:.1f} s of wall time (load factor now "
                           "{:.1f})".format(budget_s, time.monotonic() - wall_start, load_factor()),
                           held[0], held[1]) from None
        else:
            if stdout is not None:
                held[0] = stdout
            if stderr is not None:
                held[1] = stderr
            return stdout, stderr


def run(argv, *, budget_s, stall_s, input=None, cwd=None, env=None, text=False, errors=None, clock=None,
        poll_s=POLL_S, cpu_seconds=None):
    """subprocess.run(argv, capture_output=True, input=, cwd=, env=, text=, errors=) bounded by progress.
    The child is launched as the leader of a new session and process group, and that group (the child and
    every descendant that has not left it) is killed and Hung raised when its tree CPU time and output
    have not grown for stall_s wall seconds (a heuristic, off once tree CPU time is unreadable), or when
    budget_s seconds of load-adjusted time (clock, a fresh LoadClock by default) have passed. Any other
    exception that leaves the watch also runs the staged stop (at most REAP_S to reap); a monitoring
    fault leaves as Hung kind "monitor", an interrupt as itself once every stop stage was attempted. A
    Hung carries the exit status the child reached on its own before the stop, if any (Hung.returncode).
    The streams are captured as BYTES and, where text or errors asks for text, decoded only AFTER
    capture, with the encoding, newline translation and errors Popen's own text mode would use, so no
    decode failure can lose captured evidence: it leaves as Hung kind "monitor" whose output and stderr
    carry the full captured streams decoded with errors="replace", and whose returncode carries the exit
    status the child had reached (read by the stop). argv is refused (ValueError) when its program is
    git or a known launcher; otherwise the resolved program is launched by its real path, and a head
    that resolves to nothing raises FileNotFoundError without a launch. Returns a CompletedProcess
    otherwise. Without input the child stdin is os.devnull, so a child that reads stdin sees EOF
    instead of blocking. cpu_seconds stands in for tree_cpu_seconds."""
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
    text_mode = bool(text) or errors is not None
    decode_errors = "strict" if errors is None else errors
    # The encoding Popen's own text mode would pick for its pipe wrappers (locale-dependent).
    encoding = io.TextIOWrapper(io.BytesIO()).encoding if text_mode else None
    pending = input.encode(encoding, decode_errors) if text_mode and isinstance(input, str) else input
    # executable= is the real path the refusal above checked, resolved once, so the exec runs that path
    # and no other; only the file at it swapped afterwards runs as swapped (module docstring). The pipes
    # stay BINARY: text decoding happens after capture, never inside communicate, so a decode failure
    # cannot lose the captured bytes. start_new_session makes the child the leader of its own process
    # group, the group the stop kills.
    proc = subprocess.Popen(argv, executable=real,
                            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                            start_new_session=True)
    try:
        group_fd = os.pidfd_open(proc.pid)  # bound at the spawn, while the leader is unreaped
    except (AttributeError, OSError):
        group_fd = None  # the group kill falls back as _kill_group states
    held = [None, None]  # stdout and stderr bytes captured so far, kept independent of exceptions
    try:
        stdout, stderr = _watch(proc, argv, pending, budget_s, stall_s, clock, start, wall_start,
                                poll_s, cpu_seconds, held)
        if text_mode:
            # After capture: a strict-decode failure raises into the handler below, which keeps the
            # bytes from held on a monitor Hung, decoded with errors="replace".
            stdout = _decode(stdout, encoding, decode_errors)
            stderr = _decode(stderr, encoding, decode_errors)
    except BaseException as exc:
        own_exit, notes, interrupt = _stop(proc, group_fd)
        if interrupt is not None and isinstance(exc, Exception):
            raise interrupt from exc  # an interrupt during the stop leaves once every stage was attempted
        if not isinstance(exc, Exception):
            raise  # an interrupt or exit leaves as itself, after every stop stage was attempted
        if isinstance(exc, Hung):
            hung = exc
        else:
            hung = Hung(argv, budget_s, "monitor",
                        "the bound's own monitoring failed ({}: {}); the child stop was run"
                        .format(type(exc).__name__, exc), held[0], held[1])
        hung.reason += notes
        if text_mode:
            hung.output = _decode_replace(hung.output, encoding, decode_errors)
            hung.stderr = _decode_replace(hung.stderr, encoding, decode_errors)
        if own_exit is not None:
            hung.returncode = own_exit
            hung.reason += ("; the child itself had already exited with status {} before the stop (a "
                            "descendant still held its output open), and that status stands".format(own_exit))
        if hung is exc:
            raise
        raise hung from exc
    finally:
        if group_fd is not None:
            try:
                os.close(group_fd)
            except OSError:
                pass
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
    captured before it (a fault in a LATER communicate call included: the held state, never an
    exception chain, feeds the monitor Hung), a fault or an interrupt in any one stop stage (the status
    read included) must
    not skip the stages after it, and a process-table scan is cut short at its limit, driven by an
    injected table and clock so a small real process table cannot pass it vacuously; a child Popen has
    already reaped is never signalled by number (and a text-decode failure keeps the captured streams,
    decoded with errors="replace", beside the clean exit status), a live descendant is killed with the
    child through its process group (also under the forced numeric killpg fallback for a kernel
    refusing the pidfd group flag), and a bystander outside that group survives every stop (with no
    process-table scan there is no injected ancestry that could redirect a signal at it); a child that
    exited nonzero while a leaked descendant held its pipe keeps its exit status (as
    text where text was asked for); git must not launch through a launcher, a relative or empty PATH
    entry (taken from the child cwd), a PATH candidate the exec cannot run (the exec must not fall
    through to a later git), or a hard link to git under another name, and the file-identity check
    inspects the resolved real path handed to the exec, never the original alias.

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

    for stage, want in (("status read", "interrupt"), ("group kill", "interrupt"),
                        ("direct kill", "hung: monitor"), ("reap", "interrupt")):
        injected = KeyboardInterrupt("injected during the {}".format(stage))
        made, saved_popen, saved_group, saved_own = [], subprocess.Popen, _kill_group, _own_exit

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

        def interrupted_group(proc, pidfd, injected=injected):
            raise injected

        def interrupted_own_exit(proc, injected=injected):
            raise injected
        subprocess.Popen = Faulty
        if stage == "group kill":
            globals()["_kill_group"] = interrupted_group
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
            globals()["_kill_group"] = saved_group
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

    # A fault in a LATER communicate call keeps the streams an earlier poll captured: the held state,
    # never an exception chain (the later fault carries no TimeoutExpired), feeds the monitor Hung.
    class SecondFault(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            polls = getattr(self, "_vector_polls", 0) + 1
            self._vector_polls = polls
            if polls == 1:
                raise subprocess.TimeoutExpired(self.args, timeout, output=b"OUT\n",
                                                stderr=b"FAIL: seeded diagnostic\n")
            raise OSError("injected second-communicate fault")

    saved_popen_cls = subprocess.Popen
    subprocess.Popen = SecondFault
    try:
        try:
            run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token], budget_s=3600,
                stall_s=3600, poll_s=0.05)
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
        failures.append("_load_bound vector (a fault in a later communicate keeps the earlier captured "
                        "streams): got {!r}".format(got))

    # A child Popen has already reaped (here a text decode that fails after a clean exit) is never
    # signalled by number (neither os.kill nor os.killpg: its pid, and with it its group id, may
    # already be reused). The monitor Hung keeps the clean exit status AND the streams captured before
    # the decode failure, decoded with errors="replace", so a diagnostic on stderr is still judged.
    numeric_kills = []
    saved_os_kill, saved_os_killpg = os.kill, getattr(os, "killpg", None)

    def recording_kill(pid, sig):
        numeric_kills.append(("kill", pid))
        return saved_os_kill(pid, sig)

    def recording_killpg(pgid, sig):
        numeric_kills.append(("killpg", pgid))
        return saved_os_killpg(pgid, sig)
    os.kill = recording_kill
    if saved_os_killpg is not None:
        os.killpg = recording_killpg
    try:
        try:
            run([python, "-I", "-B", "-c", "import sys\nsys.stdout.buffer.write(bytes([255]))\n"
                 "sys.stderr.write(\"FAIL: real diagnostic\\n\")\n", token],
                budget_s=60, stall_s=60, text=True)
            got = "returned"
        except Hung as exc:
            got = (exc.kind, exc.returncode, exc.output, exc.stderr, numeric_kills)
        except Exception as exc:  # noqa: BLE001  any escape is this vector failing
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        os.kill = saved_os_kill
        if saved_os_killpg is not None:
            os.killpg = saved_os_killpg
    if got != ("monitor", 0, "\ufffd", "FAIL: real diagnostic\n", []):
        failures.append("_load_bound vector (a reaped child is never signalled by number, and a decode "
                        "failure keeps the captured streams): expected (monitor, 0, the replacement "
                        "character, the diagnostic, []), got {!r}".format(got))
    if tree_cpu_seconds(os.getpid()) is not None:
        # A live descendant of a stopped child is killed with it: the stop signals the child's own
        # process group (which the descendant inherited), never a scanned pid list, and a bystander
        # this process launched (outside that group) survives every stop: with no process-table scan
        # there is no injected ancestry that could redirect a signal at it.
        descendant_code = ("import subprocess, sys, time\n"
                           "subprocess.Popen([sys.executable, \"-I\", \"-c\","
                           " \"import time; time.sleep(3600)\", sys.argv[1]])\ntime.sleep(3600)\n")
        bystander = subprocess.Popen(["sleep", "3600"])
        try:
            bounded("a live descendant is killed with the tree, through the child's process group",
                    descendant_code, "hung: stall", 25, budget_s=3600, stall_s=1.5, poll_s=0.2)
            survived = bystander.poll()
        finally:
            bystander.kill()
            try:
                bystander.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
        if survived is not None:
            failures.append("_load_bound vector (a bystander outside the child's group survives the "
                            "stop): it was signalled (poll {!r})".format(survived))
        # A kernel refusing the pidfd group flag (EINVAL, forced here with a flag no kernel accepts)
        # falls back to the numeric group id while the leader is unreaped, so the descendant still
        # dies with the group; after a reap that fallback sends nothing (the reaped-child vector).
        saved_flag = PIDFD_SIGNAL_PROCESS_GROUP
        globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = 1 << 30
        try:
            bounded("the killpg fallback kills the group while the leader is unreaped",
                    descendant_code, "hung: stall", 25, budget_s=3600, stall_s=1.5, poll_s=0.2)
        finally:
            globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = saved_flag

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
        # The file-identity check inspects the RESOLVED REAL PATH handed to the exec, never the
        # original alias: an alias retargeted after the resolution could otherwise redirect the check
        # to a different file than the one the exec runs. Driven with files this vector owns.
        seen_identity = []
        saved_same = _same_file_as_git

        def recording_same(path, env, cwd):
            seen_identity.append(path)
            return saved_same(path, env, cwd)
        entry = os.path.join(scratch, "entry")
        os.symlink(os.path.join(fake, "linked"), entry)
        globals()["_same_file_as_git"] = recording_same
        try:
            try:
                run([entry], budget_s=10, stall_s=10, cwd=fake, env={"PATH": fake})
                got = "launched"
            except ValueError:
                got = "refused"
            except Exception as exc:  # noqa: BLE001  an escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            globals()["_same_file_as_git"] = saved_same
        if (got, seen_identity) != ("refused", [os.path.realpath(os.path.join(fake, "linked"))]):
            failures.append("_load_bound vector (the git file-identity check runs on the resolved real "
                            "path, never the alias): got {!r}, checked {!r}".format(got, seen_identity))
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
