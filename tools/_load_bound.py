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
child had already exited on its own before the stop (a descendant still held its output open, or a
fault such as a decode failure arrived only after the wait had completed), Hung.returncode carries
that exit status. A NONZERO status there is judged exactly as the nonzero exit
of a completed run, whatever bound fired, never filed as cannot-evaluate: a failure where the caller
wanted success, and in the tools/check_gensrc_failclose.py probes, where a nonzero --check exit is the
wanted rejection of a corrupt target, that status stands as the rejection (after the same post-probe
containment validation a completed rejection gets). An own exit of 0 beside a stop proves less than a
completed 0 (the stop may have cut the result short), so each caller states its own treatment at its
call site, every one conservative; among them, _run_check in tools/check_gensrc_failclose.py,
leg_selftest in tools/check_hooks_preview.py and the pl239 dispatch cases of
tools/selftest_aiqt_hooks.py (_pl239_launch) state theirs as follows. tools/check_hooks_preview.py files it as
unverifiable (cannot-evaluate); the pl239 cases of tools/selftest_aiqt_hooks.py retry it exactly like a
clean bound stop when no stderr was captured, ending cannot-evaluate; only the
tools/check_gensrc_failclose.py probes let it stand as a completed 0, because exit 0 on a corrupt
target is the F-154 violation there, so cannot-evaluate would be the lenient direction. The other
evidence captured beside a stop is judged by the contract of each caller: the pl239 cases of
tools/selftest_aiqt_hooks.py treat captured stderr or a nonzero own exit
beside a stop as a FAILURE (exit 1) and do not consult stdout beside a stop (a wrong verdict held
there ends cannot-evaluate after the retries); tools/check_gensrc_failclose.py and
tools/check_hooks_preview.py judge the exit status of a member alone (a generator prints its rejection
to stderr, a hook self-test its progress), so captured stderr beside a stop is not failure evidence in
those two and a stop with no exit status stays cannot-evaluate there. Failure evidence survives every
stop that leaves as Hung and every monitoring fault: the watch keeps the bytes captured so far on
every poll, in state independent of any exception; EVERY timeout snapshot, and any fault INSIDE a
communicate call, recovers into that state the bytes the call had already read (Popen holds each
stream's chunks until communicate returns, and the watch copies them out on every snapshot and
before any fault leaves, _recover_partial), so a child that closed both its pipes while it kept
running cannot strand already-read bytes in those buffers; every Hung (kind monitor
included) is built from that state, and text decoding happens only AFTER capture, so a fault in a
later communicate call, a fault between two reads of the current one, or a decode failure itself,
cannot lose them; and bytes the child had WRITTEN that no read had yet taken (they sit in the
kernel pipe when a reader fault, a timeout snapshot or the stop itself lands between a write and
the next read) are drained into that same state AFTER the kill and reap stages, with bounded
non-blocking reads (_drain, at most DRAIN_BYTES per stream), and the Hung's streams are rebuilt
from that state, so those bytes ride the Hung too; a drain cut short at its byte bound, or one
that fails, is reported by name (stdout or stderr) in the Hung reason, and bytes a descendant
that survived the stop writes after the drain are not captured (a disclosed residual). The Hung
carries the captured streams (under text=True decoded with the requested
errors, falling back to errors="replace" where that decode raises), judged the same way. An
interrupt (KeyboardInterrupt, SystemExit) leaves as itself, not as a Hung: the stop stages AND the
drain still run first (each still-open pipe is emptied and closed), but an interrupt carries no
streams, so the captured and drained bytes are discarded with it, the one exit from the watch that
failure evidence does not ride. Where tree CPU time cannot be read (no /proc, or a process-table scan cut short at SCAN_S),
or once it becomes unreadable during a run, the stall test is off for the rest of that run and only
the load-adjusted budget applies.

Cleanup. run() launches the child as the LEADER OF A NEW SESSION and process group
(start_new_session) and binds a pidfd to it at the spawn, while it is an unreaped child of this
process. The spawn also arms a parent-death signal in the child (prctl PR_SET_PDEATHSIG with
SIGKILL, through a preexec call prebound at import; libc prctl hosts only), so an OUTER stop of
this process (GNU timeout's group signal, a SIGTERM or SIGKILL of this process itself) still ends
the direct child, which its own new session would otherwise shield from the outer group signal.
The arming is CHECKED, fail closed: the preexec verifies the prctl return and then that the pid
recorded before the fork is still this child's parent (os.getppid), and on a refused prctl or a
parent already gone (the fork-to-prctl race, where the signal would never fire) the child exits
at once with UNARMED_EXIT and a best-effort diagnostic on its captured stderr, so an unprotected
child never runs; a COMPLETED arming writes ONE SUCCESS BYTE on a dedicated inherited pipe
(_arm_pdeathsig), so run() surfaces UNARMED_EXIT beside a MISSING success byte as Unarmed, a
named launch refusal (an OSError, which every caller's launch-failure handling already files),
never as an ordinary exit status a caller could judge as the program's own, and never inferred
from the best-effort stderr diagnostic (whose write can itself fail); a program that itself
exits with UNARMED_EXIT is told apart by the byte its completed arming wrote, and an arming
whose success byte cannot be written ends the child as a refusal too (fail closed); the limits
of that mechanism are in the not-covered list below.
The cleanup scope is entered BEFORE the spawn and owns the child from BEFORE IT EXISTS: the
spawn appends the acquiring Popen object to a cleanup-visible holder at the START of its
__init__, before the fork (_acquiring_popen), holds the pid the fork returns on that holder
from INSIDE the spawn primitive (the fork call runs through a per-call copy of Popen's own
_execute_child whose fork primitive stores the pid on the holder before returning it, so the
round-9 window between the fork returning and Popen's own pid binding is owned, not merely
disclosed), and the stop falls back from the proc binding
(None until the spawn statement completes) to that holder, normalized for partial
initialization (_partial_child), so any exception that leaves after the fork (a fault inside
Popen.__init__ itself, the store of the pid onto the Popen object included, a fault between
Popen returning and the local binding to proc, the
pidfd acquisition included, a bound firing, a fault in the monitoring itself, an
interrupt) runs the stop before run() leaves: read whether
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
(kind "monitor"), cannot-evaluate, never a crash that looks like a failure. What this does not
cover: the boundary inside the pid-capturing step of the spawn (_acquiring_popen), between the
fork syscall returning and the held store of the pid on the acquiring object; for an
asynchronous signal that window is WIDER than one instruction, because a signal arriving at ANY
point during the fork syscall is serviced exactly at that boundary, before the store runs. A
fault exactly there leaves a child the holder cannot name (Popen's own unwind closes its pipe
ends but never stops an exec'd child, so only the armed parent-death signal still covers it),
and on a Popen implementation whose _execute_child does not call a module-global _fork_exec the
capture falls back to the plain spawn, so the WHOLE stretch from the fork returning to Popen's
pid binding stays uncovered there, the disclosed fallback. Also not
covered: a child whose exec already failed and was reaped by Popen
itself (nothing is stopped or signalled: its pid may be reused), an interrupt landing between
two stage guards, a child killed but left unreaped because the interrupt landed in the reap
stage, a child that survives both kills because every kill stage failed, a child
in uninterruptible sleep (left unreaped after REAP_S, not waited on without bound), a single /proc
read that blocks in the kernel (the stall test's scan is bounded between entries, not inside one
read), and a descendant that left the child's process group before the stop: that one is not signalled
and is left running (no scan hunts it; the stall test's bounded process-table scan only ever reads
CPU, never selects victims). The parent-death signal has its own limits: it reaches the DIRECT
child only, so when this process is killed outright (no stop stage runs then) the child's group
members survive it, orphaned; it is armed only where libc prctl is available (elsewhere the child
itself survives this process's death, as before); an exec of a set-user-id or set-group-id program
clears it; it fires on the death of the SPAWNING THREAD, so run() must be called from a thread
that lives until the child is stopped, which run() itself ensures by blocking the spawning thread
until the stop completes, so the requirement rests on run()'s own blocking, not on which thread a
caller uses; a parent killed between the fork and the child's prctl cannot
be protected after the fact, so the preexec detects it (the recorded pid is no longer the parent)
and the child exits with UNARMED_EXIT instead of running unarmed, exactly as it does where the
host refuses the prctl itself (a seccomp or LSM policy can return nonzero without raising): on
such a host every bounded child exits UNARMED_EXIT rather than running unprotected (run()
raises Unarmed for each), the fail-closed direction, disclosed in the residuals below.

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

The shared vectors (vector_failures) run in two places: this module's own `--self-test` entry
(_self_test_main) and the check_gensrc_failclose.py self-test; each other adopting self-test carries
vectors for its own call site.

Residuals: a hang that keeps accruing CPU time or output (a spin or a polling loop) is caught only by the
load-adjusted budget; the load average is a smoothed host-wide figure, so a burst shorter than a minute is
under-counted, and a CPU quota, CPU pinning or niceness that starves the callee is not seen at all; a
descendant that leaves the child's process group (setsid or setpgid; a daemon also re-parents and so
leaves the CPU sum) is not killed, and while
the child waits on such a descendant the child looks silent; without /proc (macOS, Windows) the stall
test is off, and a /proc mounted with hidepid (other processes' entries present but unreadable to
this user) marks every scan incomplete, so the stall test is off for EVERY run there and the
load-adjusted budget alone bounds children; without POSIX process groups (Windows, where start_new_session is ignored) only the
direct child is killed, and without the pidfd group signal a stop that comes after Popen reaped the
leader kills no group member (disclosed in the Hung reason); the unreaped-leader premise behind
the numeric killpg fallback assumes this process has no other reaper of the leader (no thread of
it calling os.wait or os.waitpid(-1), and SIGCHLD not set to SIG_IGN): another reaper could free
the leader's pid, and with it the group id, while Popen still shows the leader unreaped; without os.waitid the exit status a child reached
before the stop is not read (Hung.returncode stays None); the program lookup mirrors POSIX exec and is
unverified on Windows; the file at the resolved real path swapped between the lookup and the exec runs
as swapped (the path itself is resolved once, so only that one path can be run); the post-stop drain
reads only what is in each pipe at the drain, at most DRAIN_BYTES per stream, so bytes a surviving
out-of-group descendant writes later are not captured; and a host whose libc prctl exists but is
refused by policy runs no bounded child at all (each run() raises Unarmed), the fail-closed trade
for the checked parent-death arming.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _load_bound.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import errno
import io
import math
import os
import select
import shutil
import signal
import subprocess
import time
import types

try:
    import fcntl
except ImportError:  # non-POSIX: the checked arming (gated on _PRCTL, None there) never acquires a pipe
    fcntl = None

MAX_LOAD_FACTOR = 16.0
POLL_S = 1.0
REAP_S = 10.0  # seconds run() waits to reap a SIGKILLed child before leaving it, so a stop stays bounded
SCAN_S = 5.0  # wall seconds one process-table scan may take; a scan cut short reads as unreadable CPU
# Bound on the post-stop drain of ONE pipe: a descendant that survived the stop can hold the write end
# open and feed it, so the non-blocking reads stop at this many bytes and the cut is reported by name.
DRAIN_BYTES = 1 << 20
# Exit status of a child that refused to run because its parent-death signal was not armed: the
# checked preexec ends the child with this status on a refused prctl or a parent already gone.
UNARMED_EXIT = 125
# The diagnostic _arm_pdeathsig writes to fd 2 carries this marker, for a human reading the
# captured stderr. Detection does NOT rest on it: a refused arming is signalled by the ABSENCE of
# the one success byte on the dedicated arming pipe, so UNARMED_EXIT with no success byte is
# Unarmed whatever stderr holds (the diagnostic write is best-effort and can fail). The byte
# literal inside _arm_pdeathsig must stay in sync with this marker (post-fork code resolves no
# module symbol, so it cannot read this constant).
_UNARMED_MARKER = b"parent-death signal was not armed"
_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows has no SIGKILL
PR_SET_PDEATHSIG = 1  # linux/prctl.h: deliver the given signal to this child when its parent thread dies
try:
    import ctypes
    # Prebound at import, BEFORE any fork: the preexec call in the child must not import or resolve
    # anything (post-fork context). A host without libc prctl (non-Linux) leaves this None and the
    # parent-death signal off: the child then survives this process's death, the disclosed residual.
    _PRCTL = ctypes.CDLL(None, use_errno=True).prctl
except Exception:  # noqa: BLE001  any failure to bind libc prctl only disables the parent-death signal
    _PRCTL = None
# Linux's PIDFD_SIGNAL_PROCESS_GROUP (include/uapi/linux/pidfd.h, Linux 6.9 and later): a
# pidfd_send_signal flag that signals the process group the pidfd's process leads; a kernel without
# it refuses the flag with EINVAL and the stop falls back as _kill_group states.
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


def _arm_pdeathsig(parent_pid, success_fd):
    """Runs in the forked child, between the fork and the exec: arm PR_SET_PDEATHSIG so the kernel
    SIGKILLs this child when the thread that spawned it dies, covering an outer stop of the parent
    (GNU timeout's group signal, a plain SIGTERM or SIGKILL of the parent) that the child's own new
    session would otherwise shield it from. CHECKED, fail closed: a prctl that does not return 0 (a
    seccomp or LSM policy can refuse it without raising; a raising call is the same refusal), or a
    parent already gone once the arming completed (parent_pid, recorded before the fork, is no
    longer this child's parent: the fork-to-prctl race, where the armed signal would never fire),
    ends this child at once with UNARMED_EXIT and a best-effort diagnostic on fd 2 (the captured
    stderr), so an unprotected child never runs. A COMPLETED arming is signalled AFFIRMATIVELY,
    independent of that diagnostic: one success byte on success_fd, the dedicated inherited pipe
    run() reads (the fork inherits the descriptor, the exec closes it), so run() never infers the
    refusal from best-effort stderr; a success byte that cannot be written ends this child as a
    refusal too (an arming whose success cannot be verified is not trusted), the fail-closed
    direction. Direct child only; the remaining limits are in the module
    docstring. Nothing here imports or resolves a symbol (the libc call is prebound at module
    import; post-fork context)."""
    try:
        armed = _PRCTL(PR_SET_PDEATHSIG, int(_KILL), 0, 0, 0)
    except Exception:  # noqa: BLE001  a raising prctl is a refusal, judged below, never a child traceback
        armed = -1
    if armed == 0 and os.getppid() == parent_pid:
        try:
            os.write(success_fd, b"a")
            return
        except OSError:
            pass  # a success that cannot be signalled is judged a refusal, below
    try:
        os.write(2, b"_load_bound: the parent-death signal was not armed (prctl refused, the "
                    b"parent died before the arming, or the arming could not signal its "
                    b"success); this child exits instead of running unprotected\n")
    except OSError:
        pass
    os._exit(UNARMED_EXIT)


def _pipe_above_stdio():
    """os.pipe with BOTH descriptors moved to 3 or above (fcntl F_DUPFD_CLOEXEC; close-on-exec
    kept): the spawn dup2s the child's stdio pipes onto 0, 1 and 2 BEFORE the preexec writes
    the arming success byte, so an arming descriptor at or below 2 (a caller running with
    closed standard streams) would alias the child's stdin, stdout or stderr: the byte would
    land in the captured output, and a program exiting UNARMED_EXIT would be misread as
    Unarmed. The raw pair is closed here on every path; the relocated pair belongs to the
    caller (run() closes it in its finally). Called only where _PRCTL exists (Linux), so fcntl
    is importable there."""
    raw_read, raw_write = os.pipe()
    moved = []
    try:
        for raw in (raw_read, raw_write):
            moved.append(fcntl.fcntl(raw, fcntl.F_DUPFD_CLOEXEC, 3))
    except BaseException:
        for fd in moved:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    finally:
        for raw in (raw_read, raw_write):
            try:
                os.close(raw)
            except OSError:
                pass
    return moved[0], moved[1]


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
    has taken limit_s (SCAN_S) wall seconds; a scan cut short, a /proc that cannot be listed, and a
    scan that SKIPPED a present entry (a stat unreadable for any reason other than the process
    having exited mid-scan, ENOENT or ESRCH, or one whose record cannot be parsed) are each NOT
    complete: a table that under-counts a live tree never reports itself complete, so the stall
    heuristic switches off (tree_cpu_seconds returns None) instead of judging silence on partial
    CPU accounting. names, clock and opener stand in for the /proc listing, time.monotonic and
    open, so the cut-short and unreadable-entry vectors drive tables that must cross each branch
    on any host."""
    limit_s = SCAN_S if limit_s is None else limit_s
    clock = time.monotonic if clock is None else clock
    opener = open if opener is None else opener
    if names is None:
        try:
            names = os.listdir("/proc")
        except OSError:
            return None, False
    began = clock()
    table, complete = {}, True
    for name in names:
        if clock() - began >= limit_s:
            return table or None, False
        if not name.isdigit():
            continue
        try:
            with opener("/proc/" + name + "/stat", "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue  # exited between the listing and the read: absent, the scan stays complete
            complete = False  # unreadable while present: the scan under-counts, never complete
            continue
        fields = raw[raw.rfind(b")") + 2:].split()
        try:
            table[int(name)] = (int(fields[1]), sum(int(value) for value in fields[11:15]),
                                int(fields[19]))
        except (IndexError, ValueError):
            complete = False  # a present entry whose record cannot be parsed is uncounted
            continue
    return table or None, complete


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
    output open, or the fault arrived only after a completed wait), or None when the stop ended it or
    the status could not be read: the caller judges a
    NONZERO returncode exactly as it judges the exit status of a completed run, never filing it as
    cannot-evaluate; an own exit of 0 beside a stop is judged by each caller's stated contract (module
    docstring), conservatively. output and stderr hold what was captured before the stop PLUS the
    post-stop drain of each still-open pipe (bytes the child had written that no read had yet taken,
    _drain), kept in state
    independent of any exception (a Hung of kind monitor keeps them even when a communicate call
    faulted between two reads, recovered by _recover_partial, or the text decode itself failed), as str when run() was given text=True or errors (decoded
    AFTER capture with the requested errors, falling back to errors="replace" where that raises)."""

    def __init__(self, cmd, timeout, kind, reason, output=None, stderr=None, returncode=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.reason = reason
        self.returncode = returncode

    def __str__(self):
        return "Command {!r} was stopped by its load-tolerant bound: {}".format(self.cmd, self.reason)


class Unarmed(OSError):
    """The bounded child refused to run: its parent-death signal could not be armed (a refused
    prctl, or a parent gone before the arming completed), so it exited at once with UNARMED_EXIT
    and the arming diagnostic on its stderr, and the requested program never ran. A HOST
    condition, surfaced as its own named outcome so no caller reads UNARMED_EXIT as the requested
    program's exit status; an OSError, so every caller's existing launch-failure handling
    (cannot-evaluate or a loud launch failure, never a judged exit status) already files it. The
    refusal is detected by the MISSING success byte on the dedicated arming pipe beside
    UNARMED_EXIT, never by the stderr diagnostic: a program that itself exits with UNARMED_EXIT
    is told apart by the byte its completed arming wrote (that status stands as its own), and a
    refusal whose diagnostic write failed is still this named outcome.
    stderr carries the captured diagnostic where one was written (bytes, or str under text
    mode)."""

    def __init__(self, cmd, stderr=None):
        super().__init__("the bounded child {!r} refused to run: its parent-death signal was not "
                         "armed (a refused prctl, or a parent gone before the arming); the "
                         "requested program never ran".format(cmd))
        self.cmd = cmd
        self.stderr = stderr


def _armed_byte(fd):
    """True when the one success byte of the checked arming (_arm_pdeathsig) is waiting on the
    dedicated pipe, observed WITHOUT consuming it (select.poll, POLLIN required: a pipe at EOF
    reports POLLHUP, never a byte): the affirmative signal that the arming completed,
    independent of the best-effort stderr diagnostic. Non-consuming, so the consult is
    REPEATABLE: the byte stays in the pipe until run()'s finally closes it, and a fault landing
    after one consult can never turn an already-observed byte into a reported refusal on the
    next. The preexec is the pipe's only writer (the parent writes nothing and close-on-exec
    drops the child's copy at the exec), so one readable byte IS the success byte. Consulted
    only beside an UNARMED_EXIT exit status, once the child has exited. A poll that fails or
    finds no byte counts as no byte, so an arming whose success cannot be observed is judged
    refused, the fail-closed direction."""
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        events = poller.poll(0)
        return bool(events) and bool(events[0][1] & select.POLLIN)
    except (AttributeError, OSError, ValueError):
        return False


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
    # DIVERGENCE from the "never a numeric os.killpg" policy stated in opf/tools/_opf_emit.py (a
    # freed pgid can name an unrelated group): this numeric fallback runs ONLY while Popen shows
    # the leader unreaped (the check above), so the leader's pid, and with it the group id, is
    # still allocated to it and cannot have been reused. That premise holds only while nothing
    # else in this process reaps the leader behind Popen's back: no thread calling os.wait or
    # os.waitpid(-1), and SIGCHLD not set to SIG_IGN (module docstring).
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


def _group_signal_supported():
    """True where this kernel accepts the pidfd group-signal flag, probed with signal 0 against
    this process's own pid record: EINVAL is the one refusal that means the flag itself is
    unsupported (Linux before 6.9); any other outcome of a probe that RAN (delivered, ESRCH where
    this pid leads no group, EPERM) shows the flag was understood. False where the API itself is
    absent (no pidfd_send_signal or pidfd_open: genuinely UNSUPPORTED). None where the probe could
    not run at all (pidfd_open on this process's own pid failed, for example EMFILE): UNOBSERVABLE,
    distinct from unsupported, and the shared vectors refuse a clean verdict on None instead of
    silently dropping the pidfd coverage. The vectors use True to require the exact pidfd path
    wherever the kernel offers it."""
    sender = getattr(signal, "pidfd_send_signal", None)
    opener = getattr(os, "pidfd_open", None)
    if sender is None or opener is None:
        return False
    try:
        fd = opener(os.getpid())
    except OSError:
        return None  # the probe could not run: unobservable, never read as unsupported
    try:
        sender(fd, 0, None, PIDFD_SIGNAL_PROCESS_GROUP)
        return True
    except OSError as exc:
        return exc.errno != errno.EINVAL
    finally:
        try:
            os.close(fd)
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


def _drain(proc, held):
    """After the kill and reap stages: read, without blocking, the bytes the child had written that
    no communicate read had yet taken (they sit in the kernel pipe when a stop, a timeout snapshot
    or a reader fault lands between a write and the next read), and grow held with them, so the
    Hung run() rebuilds from held carries them. Bounded: at most DRAIN_BYTES per stream, and a read
    that would block ends that stream's drain (a descendant that survived the stop can hold the
    write end open; what it writes after the drain is not captured, the disclosed residual).
    Returns "" or notes naming each stream whose drain was cut short or failed, in the stop-note
    format run() appends to the Hung reason."""
    notes = []
    for index, name in ((0, "stdout"), (1, "stderr")):
        stream = (proc.stdout, proc.stderr)[index]
        if stream is None or stream.closed:
            continue
        try:
            fd = stream.fileno()
            os.set_blocking(fd, False)
            taken = 0
            while taken < DRAIN_BYTES:
                data = os.read(fd, min(65536, DRAIN_BYTES - taken))
                if not data:
                    break
                held[index] = (held[index] or b"") + data
                taken += len(data)
            else:
                notes.append("the {} drain was cut short at its {} byte bound".format(name, DRAIN_BYTES))
        except BlockingIOError:
            pass  # the pipe is empty for now; a surviving writer's later bytes are the disclosed residual
        except Exception as exc:  # noqa: BLE001  a failed drain is reported by name, never a lost stop
            notes.append("the {} drain failed ({}: {})".format(name, type(exc).__name__, exc))
    return "".join("; " + note for note in notes)


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


def _pidfd_of(pid):
    """A pidfd bound to pid (os.pidfd_open), or None where the API is absent or the open fails:
    the group kill then falls back as _kill_group states. A helper rather than a nested try in
    run(): the compiler leaves a nested try's own line outside every protected range of the
    enclosing scope, so a fault landing exactly there (the post-spawn boundary vector traces one
    onto the first statement after the spawn) would escape the cleanup instead of stopping the
    child."""
    try:
        return os.pidfd_open(pid)
    except (AttributeError, OSError):
        return None


def _recover_partial(proc, held):
    """Recover into held the bytes the current communicate call had already read, run on EVERY
    timeout snapshot and on any fault inside the call: CPython's Popen
    appends each stream's chunks (cumulative across polls) to proc._fileobj2output as every read
    returns and never clears that state, so a fault between two reads
    cannot lose the chunks earlier reads captured. Best-effort by design: a Popen without that
    state (another implementation, a fault before any read) leaves held exactly as the last
    completed poll kept it, and held is only ever grown, never shrunk."""
    chunks = getattr(proc, "_fileobj2output", None)
    if not isinstance(chunks, dict):
        return
    for index, stream in ((0, proc.stdout), (1, proc.stderr)):
        parts = chunks.get(stream)
        if not parts:
            continue
        data = b"".join(part for part in parts if part)
        if data and len(data) >= len(held[index] or b""):
            held[index] = data


def _watch(proc, argv, pending, budget_s, stall_s, clock, start, wall_start, poll_s, cpu_seconds, held):
    """Wait for proc under both bounds; return (stdout, stderr) bytes, or raise Hung (kind stall or
    budget) carrying the bytes captured so far. Every poll also keeps those bytes in held (a two-slot
    list run() owns), state independent of any exception; every timeout snapshot, and any fault
    INSIDE a communicate call, first recovers into held the bytes that call had already read
    (_recover_partial), so neither a child that closed both pipes while it kept running, nor a
    fault in a LATER communicate call, nor one between two reads of the current call, nor a fault
    in the monitoring itself, can lose evidence a read had already captured."""
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
            # EVERY timeout snapshot also recovers the bytes the call had already read from
            # Popen's own buffers: once the child has closed both pipes (while it keeps running),
            # communicate times out in its own wait and snapshots NOTHING, so without this the
            # bytes it had read would stay stranded in those buffers and the Hung would carry
            # None. Output progress is then measured on held, which recovery can only grow.
            _recover_partial(proc, held)
            output = len(held[0] or b"") + len(held[1] or b"")
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
        except BaseException:
            # A fault INSIDE communicate (not its timeout): the bytes it had already read are in
            # Popen's own buffers, not on any exception; copy them into held before leaving, so the
            # monitor Hung run() builds from held still carries them.
            _recover_partial(proc, held)
            raise
        else:
            if stdout is not None:
                held[0] = stdout
            if stderr is not None:
                held[1] = stderr
            return stdout, stderr


def _acquiring_popen(owned):
    """A subclass of the CURRENT subprocess.Popen (so a vector's patch class stays chained, not
    displaced) that appends itself to owned at the START of its __init__, BEFORE the fork (the
    cleanup-visible owner exists before the child does), and that captures the pid the fork
    returns on itself (_acquired_pid) from INSIDE the spawn primitive: _execute_child runs
    through a per-call copy of Popen's own _execute_child whose module-global fork primitive
    (_fork_exec) is a recorder storing the pid on this object before returning it, so a fault
    anywhere from the fork on (one inside Popen.__init__ itself, the store of the pid onto the
    Popen object included, or one landing after Popen returned but before the caller's local
    binding ran) leaves the acquired object where run()'s cleanup can stop it (_partial_child
    falls back to _acquired_pid). Per call and per instance (no shared state is patched), so
    concurrent run() calls on worker threads stay independent. On a Popen implementation whose
    _execute_child does not call a module-global _fork_exec the spawn runs unchanged and the
    round-9 window between the fork returning and Popen's pid binding stands, the disclosed
    fallback; the residual inside the capture itself (the boundary between the fork syscall
    returning and the held store) is disclosed in the module docstring."""
    spawn_cls = subprocess.Popen

    class Acquiring(spawn_cls):
        def __init__(self, *args, **kwargs):
            owned.append(self)  # before super().__init__: ownership precedes the fork
            super().__init__(*args, **kwargs)

        def _execute_child(self, *args, **kwargs):
            base = spawn_cls._execute_child
            inner = getattr(base, "__func__", base)
            bound_globals = getattr(inner, "__globals__", None)
            if not isinstance(bound_globals, dict) or "_fork_exec" not in bound_globals:
                return base(self, *args, **kwargs)  # the disclosed fallback: no pid capture
            real_fork_exec = bound_globals["_fork_exec"]
            holder = self

            def holding_fork_exec(*fork_args, **fork_kwargs):
                holder._acquired_pid = real_fork_exec(*fork_args, **fork_kwargs)
                return holder._acquired_pid
            copied = types.FunctionType(inner.__code__,
                                        dict(bound_globals, _fork_exec=holding_fork_exec),
                                        inner.__name__, inner.__defaults__, inner.__closure__)
            copied.__kwdefaults__ = inner.__kwdefaults__
            return copied(self, *args, **kwargs)
    return Acquiring


def _partial_child(proc):
    """The acquiring Popen object (owned[-1]), normalized for cleanup when a fault left run()'s
    local proc unbound, or None when there is nothing to stop. None when no child was forked
    (neither Popen's own pid binding nor the in-spawn capture (_acquired_pid, stored by the
    fork recorder in _acquiring_popen) recorded one: a fault before the fork), or when Popen
    itself already REAPED the child (a FAILED EXEC closes the pipes and reaps, setting
    returncode, inside __init__: its pid, and with it its group id, may already be reused, so
    nothing may be signalled by number, and the exec OSError propagates untouched). Otherwise
    a child is running: where the fault landed inside the spawn before Popen's own pid binding
    (the round-9 window, now owned), the held _acquired_pid is bound as pid so the stop stages
    can name the child; the stop stages and the pipe closes read pid and the three streams, so
    any attribute __init__ never reached is filled with None (CPython binds the streams before
    the fork, so a holder with a pid has them; the fill covers other orderings)."""
    if getattr(proc, "pid", None) is None:
        acquired = getattr(proc, "_acquired_pid", None)
        if acquired is None:
            return None  # no child was forked: there is nothing to stop
        proc.pid = acquired  # captured inside the spawn primitive (_acquiring_popen)
    if getattr(proc, "returncode", None) is not None:
        return None  # Popen reaped it inside __init__ (a failed exec): its pid may be reused
    for name in ("stdin", "stdout", "stderr"):
        if not hasattr(proc, name):
            setattr(proc, name, None)
    return proc


def run(argv, *, budget_s, stall_s, input=None, cwd=None, env=None, text=False, errors=None, clock=None,
        poll_s=POLL_S, cpu_seconds=None):
    """subprocess.run(argv, capture_output=True, input=, cwd=, env=, text=, errors=) bounded by progress.
    The child is launched as the leader of a new session and process group, armed where libc prctl
    exists with a CHECKED parent-death signal (a child whose arming fails or whose parent died
    before the arming exits at once with UNARMED_EXIT instead of running; module docstring), and
    that group (the child and
    every descendant that has not left it) is killed and Hung raised when its tree CPU time and output
    have not grown for stall_s wall seconds (a heuristic, off once tree CPU time is unreadable), or when
    budget_s seconds of load-adjusted time (clock, a fresh LoadClock by default) have passed. Any other
    exception that leaves the watch also runs the staged stop (at most REAP_S to reap); a monitoring
    fault leaves as Hung kind "monitor", an interrupt as itself once every stop stage was attempted. A
    Hung carries the exit status the child reached on its own before the stop, if any (Hung.returncode).
    After the stop's kill and reap stages, each still-open pipe is drained with bounded non-blocking
    reads (_drain) and the Hung's streams are rebuilt from the grown capture state, so bytes the
    child had written that no read had yet taken ride the Hung too (a cut-short or failed drain is
    named in the reason).
    The streams are captured as BYTES and, where text or errors asks for text, decoded only AFTER
    capture, with the encoding, newline translation and errors Popen's own text mode would use, so no
    decode failure can lose captured evidence: it leaves as Hung kind "monitor" whose output and stderr
    carry the full captured streams decoded with errors="replace", and whose returncode carries the exit
    status the child had reached (read by the stop). argv is refused (ValueError) when its program is
    git or a known launcher; otherwise the resolved program is launched by its real path, and a head
    that resolves to nothing raises FileNotFoundError without a launch. A child that refused to
    run because its arming failed (UNARMED_EXIT with no success byte on the dedicated arming
    pipe, whatever the captured stderr holds) raises Unarmed, a named OSError, never an ordinary
    exit status; a program that itself exits with UNARMED_EXIT keeps that status (the byte its
    completed arming wrote tells it apart). Returns a CompletedProcess
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
    # group, the group the stop kills; the preexec arms the parent-death signal in the child, so an
    # outer stop of THIS process still ends the direct child its new session would otherwise shield
    # (where libc prctl exists; limits in the module docstring).
    parent_pid = os.getpid()  # recorded BEFORE the fork: _arm_pdeathsig verifies it is still the parent
    armed_read = None
    armed_write = None  # a separate binding (not chained): the two pipe ends are distinct
    group_fd = None  # ownership state initialized before any acquisition, so cleanup can always run
    held = [None, None]  # stdout and stderr bytes captured so far, kept independent of exceptions
    proc = None  # the acquired child, once the local binding below has run
    owned = []  # cleanup-visible ownership: the spawn appends the acquiring object BEFORE the fork
    refused_arming = False
    try:
        if _PRCTL is not None:
            # The dedicated arming pipe, acquired INSIDE the protected scope (its cleanup
            # state above is initialized first, so a fault landing right after this
            # acquisition still reaches the finally that closes both ends) and with both
            # descriptors at 3 or above (_pipe_above_stdio): the child's preexec writes ONE
            # success byte once the checked arming completed, so a refused arming is
            # signalled by that byte's ABSENCE beside UNARMED_EXIT, independent of the
            # best-effort stderr diagnostic. The fork inherits the write end; the exec (or
            # the refusing child's exit) closes the child's copy.
            armed_read, armed_write = _pipe_above_stdio()
        # The cleanup scope is entered BEFORE the spawn and owns the child from BEFORE it
        # exists: _acquiring_popen appends the acquiring object to owned at the start of
        # __init__, before the fork, and holds the pid the fork returns on that object from
        # inside the spawn primitive itself, so a fault anywhere after the fork (inside
        # Popen.__init__ itself, the store of the pid onto the Popen object included, between
        # Popen returning and the binding to proc, the pidfd acquisition included (os.pidfd_open
        # can raise more than OSError; _pidfd_of guards only the expected two, without a nested
        # try whose own line the compiler would leave outside this scope's protected range), a
        # bound firing, a fault in the monitoring) runs the staged stop and closes the pipes
        # instead of leaving the child running behind an escaped exception: the handler below
        # falls back from proc (None until the binding runs) to owned[-1], normalized for
        # partial initialization (_partial_child).
        proc = _acquiring_popen(owned)(argv, executable=real,
                                stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                                start_new_session=True,
                                preexec_fn=(lambda: _arm_pdeathsig(parent_pid, armed_write))
                                if _PRCTL is not None else None)
        group_fd = _pidfd_of(proc.pid)  # bound at the spawn, while the leader is unreaped
        stdout, stderr = _watch(proc, argv, pending, budget_s, stall_s, clock, start, wall_start,
                                poll_s, cpu_seconds, held)
        if text_mode:
            # After capture: a strict-decode failure raises into the handler below, which keeps the
            # bytes from held on a monitor Hung, decoded with errors="replace".
            stdout = _decode(stdout, encoding, decode_errors)
            stderr = _decode(stderr, encoding, decode_errors)
        # Consult the arming verdict while the pipe is still open (the finally below closes it;
        # the consult is non-consuming, so the exception path below can consult it again): the
        # named refusal is raised AFTER the finally, outside this handler.
        refused_arming = (proc.returncode == UNARMED_EXIT and armed_read is not None
                          and not _armed_byte(armed_read))
    except BaseException as exc:
        if proc is None and owned:
            proc = _partial_child(owned[-1])  # acquired before the fork; the binding never ran
        if proc is None:
            raise  # no child was forked (or Popen reaped a failed exec): there is nothing to stop
        own_exit, notes, interrupt = _stop(proc, group_fd)
        # The drain runs on EVERY stop, the two interrupt exits below included (bytes written but
        # not yet read would otherwise close with the pipes). An interrupt still leaves as itself
        # and carries no streams, so on those two exits the captured and drained bytes are
        # discarded with held: failure evidence does not accompany an interrupt, the stated limit.
        notes += _drain(proc, held)
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
        hung.output, hung.stderr = held[0], held[1]  # rebuilt: the drain can only have grown them
        hung.reason += notes
        if text_mode:
            hung.output = _decode_replace(hung.output, encoding, decode_errors)
            hung.stderr = _decode_replace(hung.stderr, encoding, decode_errors)
        if own_exit is not None:
            hung.returncode = own_exit
            hung.reason += ("; the child itself had already exited with status {} before the stop, "
                            "and that status stands".format(own_exit))
        if (own_exit == UNARMED_EXIT and armed_read is not None
                and not _armed_byte(armed_read)):
            # The child had refused to run before the stop: the named host condition, signalled
            # by the missing success byte (module docstring), never an ordinary exit status and
            # never inferred from best-effort stderr, on the stopped path too.
            raise Unarmed(argv, hung.stderr) from exc
        if hung is exc:
            raise
        raise hung from exc
    finally:
        if armed_write is not None:
            try:
                os.close(armed_write)
            except OSError:
                pass
        if armed_read is not None:
            try:
                os.close(armed_read)
            except OSError:
                pass
        if group_fd is not None:
            try:
                os.close(group_fd)
            except OSError:
                pass
        if proc is not None:
            for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None),
                           getattr(proc, "stderr", None)):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
    if refused_arming:
        # The child exited refusing to run (a refused prctl, a parent gone before the arming, or
        # an arming whose success could not be signalled): a HOST condition surfaced as its own
        # named outcome, never an ordinary exit status the caller would judge as the requested
        # program's own (the program never ran). A program that itself exits with UNARMED_EXIT is
        # NOT caught here: the success byte its completed arming wrote distinguishes it.
        raise Unarmed(argv, stderr)
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _alive_with(token, opener=None):
    """Pids whose command line carries token (Linux /proc), [] where none does; None where the
    check is UNOBSERVABLE: /proc cannot be listed (absent or refused), or a command line could not
    be read for a reason other than the process having exited meanwhile (ENOENT and ESRCH mid-scan
    are absence, not blindness). The vectors refuse to read blindness as absence: where /proc
    exists, None is a vector failure, never a clean "no survivors"; a host with no /proc at all
    has no survivor check, the disclosed platform limit. opener stands in for open, so a vector
    can inject a refused read (EACCES) without touching the real /proc. A cleanup caller passes
    this result through _leak_cleanup_outcome, which reads a list as an observed scan, None on a
    host WITH /proc as a failure, and None on a host with NO /proc as a NAMED cannot-evaluate
    (the disclosed platform limit, exit 2 at the caller's suite level), never a false leak."""
    opener = open if opener is None else opener
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    found = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with opener("/proc/" + name + "/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue  # the process exited between the listing and the read: absent
            return None  # unreadable while present (for example EACCES): unobservable
        if token.encode() in raw:
            found.append(int(name))
    return found


def _kill_pid(pid):
    try:
        os.kill(pid, _KILL)
    except OSError:
        pass


def _leak_cleanup_outcome(leaked_pids, has_proc):
    """The three DISTINCT results of a leaked-descendant survivor scan (_alive_with), for a
    cleanup caller: "observed" for a pid list or [] (the scan ran; the caller kills what it
    lists), "failure" for an UNOBSERVABLE scan (None) while /proc EXISTS (the leak cannot be
    proven absent, a real failure), and "cannot-evaluate" for None with NO /proc at all (the
    host has no survivor check, the disclosed platform limit): every caller reports that last
    result BY NAME with exit 2 at suite level, never a silent pass and never a false leak.
    The callers drive all three legs against this helper and through their own call sites."""
    if leaked_pids is not None:
        return "observed"
    return "failure" if has_proc else "cannot-evaluate"


def _leak_cleanup_is_failure(leaked_pids, has_proc):
    """True exactly where _leak_cleanup_outcome reads the scan as a real FAILURE: None
    (unobservable) while /proc exists. The two non-failure results stay distinct there:
    "observed" is a scan that ran, and "cannot-evaluate" (None with no /proc) is reported by
    name at suite level, never silently passed."""
    return _leak_cleanup_outcome(leaked_pids, has_proc) == "failure"


def _stat_entry(pid, proc_table=None):
    """The complete (ppid, cpu ticks, start ticks) /proc stat entry for pid, or None when the
    read failed or the table lacks the pid. The crafted-table vector requires a complete usable
    entry and records None as UNOBSERVABLE (no clean verdict), never substituting zero start
    ticks: a table-driven kill that validates start ticks would reject a zero-tick entry, and the
    vector would then accept the very survival it claims to discriminate. proc_table stands in
    for _proc_table, so the None branch is itself testable."""
    table, _ = (_proc_table if proc_table is None else proc_table)(names=[str(pid)])
    entry = (table or {}).get(pid)
    if not (isinstance(entry, tuple) and len(entry) == 3):
        return None
    return entry


def vector_failures(python):
    """The shared vectors, run by this module's --self-test and the check_gensrc_failclose.py
    self-test; returns a list of failure strings. A child that outlives its bound while making CPU or output progress must pass; a silent child
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
    child is also killed by a watchdog (handle_watchdog) that stops the WHOLE fixture process
    groups the vector OWNS, through each retained Popen handle and the group id held on it (run()
    starts each fixture as its own session leader, so the group id is the leader's own retained
    pid), independent of /proc; the survivor scan beside those kills is an extra observation, not
    the enforcement (it can be blind where /proc is unreadable; the group and handle kills
    cannot), so a regression shows as a failure here, never as a hung self-test. EXACTLY these
    vectors install that watchdog, each at 30 s unless stated: every bounded() vector (the
    silent-stall, spinning-budget, malformed-load, monitoring-fault, group, kill-path and
    crafted-table vectors run through it), the four stop-stage fault vectors, the two
    interrupt-route drain vectors, the seeded and second-communicate monitor-fault vectors, the
    two mid-communicate fault vectors, the reader-fault vector, the two released drain timings
    (60 s; their children wait on a go file), the closed-pipe recovery vector, the spawn-fault
    vector, the two boundary-fault vectors (line trace and opcode trace), and the watchdog-expiry
    vector itself (6 s, so the expiry is observed). The vectors that install NO watchdog start no
    child that could outlive them unstopped: the refusal, mismatch, collision and outer-stop legs
    end their children by construction, the reaped-member vectors' group members die with the
    group under test or (on the residual leg, by design) through the survivor cleanup, and the
    leaked-descendant vector's descendant sleeps a bounded 60 s.

    Added vectors: a fault INSIDE a communicate call, in the first poll and in a later one
    (WHICH poll faulted is asserted), keeps the bytes that call had already read (recovered from
    Popen's own buffers, never from an exception); bytes WRITTEN but not yet read when the stop
    lands are drained after the kill and reap stages, proven for three timings (a reader fault
    after both pipes were readable, output arriving after the last timeout snapshot, and output
    written during the status-read stop stage); a non-OSError fault after the spawn (pidfd_open)
    still runs the stop and closes both pipes; the kill path a stop takes is OBSERVED, and the
    pidfd group kill is required wherever the kernel accepts the group flag (probed, never
    assumed; a probe that cannot run is UNOBSERVABLE and its own failure, never silently read as
    unsupported, and the same distinction holds for a survivor scan that cannot read /proc); a
    crafted ancestor table cannot redirect a signal at a bystander, crafted with the bystander's
    REAL cpu and start ticks so even a table-driven kill that re-validates start ticks is caught
    (no stop stage selects victims from a process table); the pidfd group kill stays exact AFTER
    Popen reaped the leader (the group member still dies), while the forced-EINVAL leg observes
    the disclosed residual (the member survives, the EINVAL itself is recorded on BOTH the
    unreaped and the post-reap legs, and the post-reap leg also records that NOTHING ran by
    number) and one bystander stays alive through the numeric-fallback stop too; the parent-death
    arming is CHECKED (an injected prctl refusal and a recorded parent that is gone each end the
    child at once, survival marker absent); and an outer SIGTERM and an outer SIGKILL of this
    process each reach the direct child through that armed signal: every outer leg requires the
    inner child's started marker before judging, validates the launcher's exit status, and a
    positive control proves an UNARMED child would have survived and been seen (libc prctl hosts
    only; elsewhere that survival is the disclosed residual).

    Round-8 vectors: EVERY timeout snapshot recovers the bytes communicate had already read, so a
    child that closed both pipes and kept running keeps them on the budget Hung; a fault raised
    right after Popen returns, before the next statement (a trace-injected boundary fault), still
    stops the child and closes both pipes (the cleanup owns the child from before the spawn); the
    drain runs before EITHER interrupt re-raise, observed through a drain recorder on both
    interruption routes, while the interrupt itself still carries no streams (the stated limit);
    a capability probe that cannot run (injected EMFILE) reads as unobservable, never as a
    verdict, a refused /proc cmdline read (injected EACCES) reads as unobservable, never as
    absence (mid-scan ENOENT stays absence), and a group stop still runs with CPU accounting
    unreadable (the budget fallback); the drain clamp's boundaries are committed (one byte
    inside, exactly at, one byte over a small injected bound, a partial-read sequence over an
    over-bound input whose EVERY request is asserted against the allowance remaining before it,
    and a one-stream failure that names its stream); the crafted-table vector requires a complete
    usable bystander stat entry and records a failed or missing one as unobservable, never a
    zero-tick substitute; and a refused prctl surfaces as the named Unarmed outcome, never an
    ordinary exit status.

    Round-9 vectors: a fault raised after Popen returned but BEFORE the local binding to proc
    (injected at the store instruction through OPCODE tracing, where a line-event trace cannot
    reach) still stops the child and closes both pipes: ownership is appended to a
    cleanup-visible holder before the fork, so the pre-assignment boundary is covered beside the
    post-assignment one; a refused arming is signalled by the missing success byte on the
    dedicated arming pipe, independent of best-effort stderr (a refusal whose diagnostic write
    fails is still the named Unarmed with EMPTY captured stderr; a monitor fault after a refused
    child completed still converts to Unarmed on the stopped path; and a program that itself
    exits with the shared status while PRINTING the marker keeps that status as its own: the byte
    tells them apart); the EXPIRED watchdog stops the fixture's whole process group through the
    retained group id beside an injected BLIND survivor scan, so a pipe-holding descendant dies
    with the group and the blocked communicate releases; and a partial CPU scan that skipped a
    present entry (an unreadable or unparsable stat) reports itself incomplete, while mid-scan
    ENOENT stays complete absence.

    Round-10 vectors: the arming descriptors sit at 3 or above, proven inside subprocesses
    whose standard descriptors 0 and 1 (and 0 and 2) are closed (exact captured stdout and
    stderr, and a program exiting with UNARMED_EXIT keeps status 125, never Unarmed); the
    arming consult is non-consuming and repeatable, so an injected fault after a first consult
    never turns a completed arming into a reported refusal (the program's own 125 rides the
    monitor Hung); the STOPPED path detects a refusal by the missing byte alone (a refusal
    whose diagnostic write failed, EMPTY stderr, beside a monitor fault is still the named
    Unarmed); the arming pipe is acquired INSIDE the protected scope with cleanup state
    initialized first, so a fault right after the acquisition leaves no descriptor open; and a
    fault injected at the store of the pid onto the Popen object INSIDE the spawn
    (opcode-traced on Popen's own _execute_child code) still stops the child and closes both
    pipes, because the fork primitive holds the pid on the acquiring object before returning
    (_acquiring_popen)."""
    import tempfile
    import threading

    failures = []
    token = "load-bound-vector-{}".format(os.getpid())
    has_proc = os.path.isdir("/proc")
    unobservable_noted = []

    def alive(tok):
        """_alive_with, refusing to read blindness as absence: an UNOBSERVABLE check (None) on a
        host that has /proc is recorded once as its own vector failure (no vector here can then
        prove absence), and cleanup continues with an empty list; a host with no /proc at all has
        no survivor check, the disclosed platform limit."""
        found = _alive_with(tok)
        if found is None:
            if has_proc and not unobservable_noted:
                unobservable_noted.append(True)
                failures.append("_load_bound vector (the survivor check is observable): /proc "
                                "exists but could not be read, so no vector here can prove absence")
            return []
        return found

    def handle_watchdog(seconds, made):
        """A watchdog for a non-terminating fixture: after seconds, stop the WHOLE process group
        of every fixture this vector OWNS, through the group id retained on its Popen handle
        (run() starts each fixture as the leader of its own session, so the group id is the
        leader's own pid, held on the handle; no /proc read and no scanned pid is involved, so a
        pipe-holding descendant dies with its group even where the survivor scan is blind), then
        kill each leader directly through its handle, then scan for survivors as an EXTRA
        observation, not the enforcement. The numeric group signal runs ONLY while the handle
        shows the leader unreaped (returncode None: an exited-but-unreaped leader keeps its pid,
        and with it the group id, allocated, the same premise _kill_group states), so a group id
        freed by a reap is never signalled by number; the check and the signal are two steps, so
        a reap landing between them is the remaining disclosed window, and a fixture whose
        leader was already reaped at expiry gets no group signal at all (its surviving members
        fall to the survivor scan below, which can be blind), the fail-closed trade for
        enforcement that does not depend on /proc. Returns the started timer; the caller
        cancels it."""
        def stop_owned():
            for proc in list(made):
                if getattr(proc, "returncode", None) is None:
                    try:
                        # The retained group id: the fixture leads its own session, and the
                        # unreaped-leader check above keeps the id allocated to it.
                        os.killpg(proc.pid, _KILL)
                    except (AttributeError, OSError):
                        pass  # no killpg on this platform, or the group is already empty
                try:
                    proc.kill()
                except OSError:
                    pass
            for pid in (_alive_with(token) or []):
                _kill_pid(pid)
        timer = threading.Timer(seconds, stop_owned)
        timer.daemon = True
        timer.start()
        return timer

    def recording_popen(made, outer_cls):
        """A subclass of outer_cls, the CURRENT subprocess.Popen its caller saved to restore (so a
        vector's own patch class is chained, not displaced), that retains every spawned handle in
        made, for watchdogs that must own what they stop."""

        class Recorder(outer_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made.append(self)
        return Recorder

    def outcome(code, **bounds):
        try:
            done = run([python, "-I", "-B", "-c", code, token], **bounds)
        except Hung as exc:
            return "hung: " + exc.kind
        return done.returncode

    def bounded(label, code, want, limit, **bounds):
        # A non-terminating child: the outcome must arrive within limit wall seconds, leaving
        # nothing alive. The watchdog stops the child through the handle the recorder retains;
        # the survivor scan inside it is an extra observation, not the enforcement.
        made = []
        saved_outer_cls = subprocess.Popen
        subprocess.Popen = recording_popen(made, saved_outer_cls)
        timer = handle_watchdog(30.0, made)
        began = time.monotonic()
        try:
            got = outcome(code, **bounds)
        except Exception as exc:  # noqa: BLE001  any escape is this vector's failure
            got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_outer_cls
            timer.cancel()
        took, left = time.monotonic() - began, alive(token)
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
        stage_watchdog = handle_watchdog(30.0, made)
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
            stage_watchdog.cancel()
        left = alive(token)
        for _ in range(50):  # a SIGKILLed child can take a moment to leave the process table
            if not left:
                break
            time.sleep(0.1)
            left = alive(token)
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

    # The drain runs BEFORE either interrupt re-raise: on both interruption routes (an interrupt
    # that is itself the leaving exception, and one held from a stop stage while a monitoring
    # fault leaves), the stop stages and then the drain are attempted first, OBSERVED through a
    # drain recorder. The interrupt still carries no streams: the captured and drained bytes are
    # discarded with it, the stated limit.
    for route in ("the leaving exception", "held from a stop stage"):
        route_interrupt = KeyboardInterrupt("injected interrupt, {}".format(route))
        drained_calls = []
        saved_route_drain = _drain

        def recording_drain(proc, held, saved_route_drain=saved_route_drain,
                            drained_calls=drained_calls):
            drained_calls.append(True)
            return saved_route_drain(proc, held)

        saved_route_own = _own_exit

        def interrupting_own_exit(proc, route_interrupt=route_interrupt):
            raise route_interrupt

        route_ticks = iter((0.0, 0.0))

        def route_clock(route=route, route_interrupt=route_interrupt):
            value = next(route_ticks, None)
            if value is None:
                if route == "the leaving exception":
                    raise route_interrupt
                raise RuntimeError("injected monitoring fault")
            return value
        globals()["_drain"] = recording_drain
        if route == "held from a stop stage":
            globals()["_own_exit"] = interrupting_own_exit
        route_made = []
        saved_route_cls = subprocess.Popen
        subprocess.Popen = recording_popen(route_made, saved_route_cls)
        route_watchdog = handle_watchdog(30.0, route_made)
        try:
            try:
                run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token],
                    budget_s=3600, stall_s=3600, poll_s=0.05, clock=route_clock)
                got = "returned"
            except KeyboardInterrupt as exc:
                got = "interrupt" if exc is route_interrupt else "foreign interrupt"
            except Exception as exc:  # noqa: BLE001  any other escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_route_cls
            globals()["_drain"] = saved_route_drain
            globals()["_own_exit"] = saved_route_own
            route_watchdog.cancel()
        left = alive(token)
        for _ in range(50):  # a SIGKILLed child can take a moment to leave the process table
            if not left:
                break
            time.sleep(0.1)
            left = alive(token)
        for pid in left:
            _kill_pid(pid)
        if (got, len(drained_calls), left) != ("interrupt", 1, []):
            failures.append("_load_bound vector (the drain runs before the interrupt re-raise, "
                            "{}): expected the interrupt back beside exactly one drain call and "
                            "no child left, got {!r}, drain calls {}, left alive {!r}"
                            .format(route, got, len(drained_calls), left))

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

    # A scan that SKIPPED a present entry never reports itself complete: an unreadable stat
    # (EACCES while the process exists) and an unparsable record each mark the scan incomplete
    # (tree CPU would under-count, so the stall heuristic must switch off), while an entry that
    # exited mid-scan (ENOENT) stays absence and the scan stays complete.
    def mixed_opener(error):
        def opener(path, *args, **kwargs):
            if "/12/" in path:
                raise error
            return io.BytesIO(b"99 (probe) S 1 0 0 0 0 0 0 0 0 0 4 4 0 0 20 0 1 0 7")
        return opener

    def malformed_opener(path, *args, **kwargs):
        if "/12/" in path:
            return io.BytesIO(b"12 (broken")
        return io.BytesIO(b"99 (probe) S 1 0 0 0 0 0 0 0 0 0 4 4 0 0 20 0 1 0 7")
    mixed_results = []
    for case_opener in (mixed_opener(PermissionError(errno.EACCES, "injected: permission denied")),
                        mixed_opener(FileNotFoundError(errno.ENOENT, "injected: exited mid-scan")),
                        malformed_opener):
        held_ticks = iter((0.0,) * 8)
        case_table, case_complete = _proc_table(names=["11", "12"],
                                                clock=lambda: next(held_ticks, 0.0),
                                                opener=case_opener)
        mixed_results.append((sorted(case_table or {}), case_complete))
    if mixed_results != [([11], False), ([11], True), ([11], False)]:
        failures.append("_load_bound vector (a partial CPU scan reports itself incomplete after "
                        "an unreadable or unparsable present entry, while mid-scan ENOENT stays "
                        "complete absence): expected [([11], False), ([11], True), ([11], "
                        "False)], got {!r}".format(mixed_results))

    # A monitoring fault keeps the streams captured before it: the monitor Hung carries them, so the
    # caller can judge that evidence; the stop still kills and reaps the real child.
    seeded_made = []
    saved_popen_cls = subprocess.Popen  # the class subclassed below, and restored after the run

    class SeededTimeout(saved_popen_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seeded_made.append(self)

        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(self.args, timeout, output=b"OUT\n",
                                            stderr=b"FAIL: seeded diagnostic\n")

    fault_ticks = iter((0.0, 0.0))

    def seeded_fault_clock():
        value = next(fault_ticks, None)
        if value is None:
            raise RuntimeError("injected monitoring fault")
        return value
    subprocess.Popen = SeededTimeout
    seeded_watchdog = handle_watchdog(30.0, seeded_made)
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
        seeded_watchdog.cancel()
    for pid in alive(token):
        _kill_pid(pid)
    if got != ("monitor", b"OUT\n", b"FAIL: seeded diagnostic\n"):
        failures.append("_load_bound vector (a monitoring fault keeps the captured streams): got {!r}"
                        .format(got))

    # A fault in a LATER communicate call keeps the streams an earlier poll captured: the held state,
    # never an exception chain (the later fault carries no TimeoutExpired), feeds the monitor Hung.
    second_made = []
    saved_popen_cls = subprocess.Popen  # the class subclassed below, and restored after the run

    class SecondFault(saved_popen_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            second_made.append(self)

        def communicate(self, input=None, timeout=None):
            polls = getattr(self, "_vector_polls", 0) + 1
            self._vector_polls = polls
            if polls == 1:
                raise subprocess.TimeoutExpired(self.args, timeout, output=b"OUT\n",
                                                stderr=b"FAIL: seeded diagnostic\n")
            raise OSError("injected second-communicate fault")

    subprocess.Popen = SecondFault
    second_watchdog = handle_watchdog(30.0, second_made)
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
        second_watchdog.cancel()
    for pid in alive(token):
        _kill_pid(pid)
    if got != ("monitor", b"OUT\n", b"FAIL: seeded diagnostic\n"):
        failures.append("_load_bound vector (a fault in a later communicate keeps the earlier captured "
                        "streams): got {!r}".format(got))

    # A fault INSIDE a communicate call (no TimeoutExpired snapshots these bytes) keeps what that
    # very call had already read: run() recovers Popen's partial buffers into held, so the monitor
    # Hung carries evidence read during the faulting call itself. The injected os.read fault fires
    # on the first read attempt AFTER every marker was read; the stream that carried the last
    # marker still has its EOF queued then (the child closed both streams), so the fault lands in
    # the same communicate call that read the markers.
    def mid_read_fault(label, code, markers, want_out, want_err, poll_s, first_fault_poll,
                       last_fault_poll):
        faulted_polls = []
        made_procs = []
        saved_cls = subprocess.Popen  # the class subclassed below, and restored after the run

        class MidReadFault(saved_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made_procs.append(self)

            def communicate(self, input=None, timeout=None):
                saved_read = os.read
                seen = getattr(self, "_vector_seen", set())
                self._vector_seen = seen
                polls = getattr(self, "_vector_polls", 0) + 1
                self._vector_polls = polls

                def faulting_read(fd, size):
                    if len(seen) == len(markers):
                        faulted_polls.append(polls)
                        raise OSError("injected mid-communicate fault")
                    data = saved_read(fd, size)
                    for marker in markers:
                        if marker in data:
                            seen.add(marker)
                    return data
                os.read = faulting_read
                try:
                    return super().communicate(input, timeout)
                finally:
                    os.read = saved_read

        # An independent watchdog: a child that never reaches its markers must not hold this
        # vector for the full 3600 s budget. It stops the child through the handle MidReadFault
        # retains; the survivor scan inside it is an extra observation.
        watchdog = handle_watchdog(30.0, made_procs)
        subprocess.Popen = MidReadFault
        try:
            try:
                run([python, "-I", "-B", "-c", code, token], budget_s=3600, stall_s=3600,
                    poll_s=poll_s)
                got = "returned"
            except Hung as exc:
                got = (exc.kind, exc.output or b"", exc.stderr or b"")
            except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_cls
            watchdog.cancel()
        for pid in alive(token):
            _kill_pid(pid)
        # WHICH communicate call faulted is asserted, so the label (first call, later call) is
        # proven, never assumed from the fixture shape alone.
        fault_in_range = (len(faulted_polls) == 1 and first_fault_poll <= faulted_polls[0]
                          and (last_fault_poll is None or faulted_polls[0] <= last_fault_poll))
        if got != ("monitor", want_out, want_err) or not fault_in_range:
            failures.append("_load_bound vector ({}): expected (monitor, {!r}, {!r}) with the fault "
                            "in poll {} to {}, got {!r}, faulted in {!r}"
                            .format(label, want_out, want_err, first_fault_poll, last_fault_poll,
                                    got, faulted_polls))

    mid_read_fault("a fault inside the first communicate call keeps the bytes it had read",
                   "import os, sys, time\n"
                   "sys.stderr.write(\"FAIL: early diagnostic\\n\")\nsys.stderr.flush()\n"
                   "os.close(2)\n"
                   "sys.stdout.write(\"OUT: early evidence\\n\")\nsys.stdout.flush()\n"
                   "os.close(1)\n"
                   "time.sleep(3600)\n",
                   (b"early diagnostic", b"early evidence"),
                   b"OUT: early evidence\n", b"FAIL: early diagnostic\n", 1.0, 1, 1)
    # Later poll: the first marker rides earlier TimeoutExpired snapshots into held; the second is
    # read only inside the later, faulting call, whose recovery must merge the two.
    mid_read_fault("a fault inside a later communicate call keeps its own bytes and the earlier ones",
                   "import os, sys, time\n"
                   "sys.stdout.write(\"OUT: first-poll evidence\\n\")\nsys.stdout.flush()\n"
                   "time.sleep(1.0)\n"
                   "sys.stderr.write(\"FAIL: later diagnostic\\n\")\nsys.stderr.flush()\n"
                   "os.close(2)\nos.close(1)\n"
                   "time.sleep(3600)\n",
                   (b"first-poll evidence", b"later diagnostic"),
                   b"OUT: first-poll evidence\n", b"FAIL: later diagnostic\n", 0.25, 2, None)

    # Bytes WRITTEN but not yet READ when a fault lands are drained after the stop: a reader
    # fault that fires once both pipes are readable, before any read, would otherwise lose every
    # byte the child wrote (nothing reached Popen's buffers or any timeout snapshot).
    reader_fault_procs = []
    saved_cls = subprocess.Popen  # the class subclassed below, and restored after the run

    class ReaderFault(saved_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            reader_fault_procs.append(self)

        def communicate(self, input=None, timeout=None):
            import select
            deadline = time.monotonic() + 15.0
            waiting = {self.stdout.fileno(), self.stderr.fileno()}
            while waiting and time.monotonic() < deadline:
                ready, _, _ = select.select(sorted(waiting), [], [], 0.2)
                waiting.difference_update(ready)
            raise OSError("injected reader fault after both pipes were readable")

    watchdog = handle_watchdog(30.0, reader_fault_procs)
    subprocess.Popen = ReaderFault
    try:
        try:
            run([python, "-I", "-B", "-c",
                 "import sys, time\nsys.stdout.write(\"OUT before fault\\n\")\nsys.stdout.flush()\n"
                 "sys.stderr.write(\"FAIL before fault\\n\")\nsys.stderr.flush()\ntime.sleep(3600)\n",
                 token], budget_s=3600, stall_s=3600, poll_s=0.2)
            got = "returned"
        except Hung as exc:
            got = (exc.kind, exc.output, exc.stderr)
        except Exception as exc:  # noqa: BLE001  any escape is this vector failing
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        subprocess.Popen = saved_cls
        watchdog.cancel()
    for pid in alive(token):
        _kill_pid(pid)
    if got != ("monitor", b"OUT before fault\n", b"FAIL before fault\n"):
        failures.append("_load_bound vector (unread bytes at a reader fault are drained after the "
                        "stop): got {!r}".format(got))

    # Output that arrives AFTER the last timeout snapshot, and output written DURING the stop
    # stages, are drained too. Each child writes only once released (a go file this vector writes
    # from inside the window under test), so the bytes deterministically miss every communicate
    # read; an injected clock fires the budget on that same poll.
    drain_dir = tempfile.mkdtemp(prefix="load-bound-vector-drain-")
    try:
        def released_writer(go, done, text):
            return ("import os, sys, time\n"
                    "while not os.path.exists({go!r}):\n    time.sleep(0.05)\n"
                    "sys.stderr.write({text!r})\nsys.stderr.flush()\n"
                    "open({done!r}, \"w\").write(\"d\")\n"
                    "time.sleep(3600)\n").format(go=go, done=done, text=text)

        def release_and_wait(go, done):
            with open(go, "w", encoding="utf-8") as handle:
                handle.write("g")
            deadline = time.monotonic() + 15.0
            while not os.path.exists(done) and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)  # the pipe write lands before the done marker; a grace beat regardless

        def drained_stop(label, code, want_err, release_point):
            budget_ticks = iter((0.0,))
            made = []
            saved_outer = subprocess.Popen
            subprocess.Popen = recording_popen(made, saved_outer)
            watchdog = handle_watchdog(60.0, made)
            try:
                try:
                    run([python, "-I", "-B", "-c", code, token], budget_s=60, stall_s=3600,
                        poll_s=0.5, clock=lambda: next(budget_ticks, 3600.0),
                        cpu_seconds=release_point)
                    got = "returned"
                except Hung as exc:
                    got = (exc.kind, exc.output, exc.stderr)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.Popen = saved_outer
                watchdog.cancel()
            for pid in alive(token):
                _kill_pid(pid)
            if got != ("budget", None, want_err):
                failures.append("_load_bound vector ({}): expected (budget, None, {!r}), got {!r}"
                                .format(label, want_err, got))

        # Timing 1: the write lands between the last timeout snapshot and the bound test (the
        # cpu_seconds hook runs exactly there).
        late_go = os.path.join(drain_dir, "late-go")
        late_done = os.path.join(drain_dir, "late-done")

        def releasing_cpu(pid):
            release_and_wait(late_go, late_done)
            return 0.0
        drained_stop("output after the timeout snapshot is drained after the stop",
                     released_writer(late_go, late_done, "FAIL: late diagnostic\n"),
                     b"FAIL: late diagnostic\n", releasing_cpu)

        # Timing 2: the write lands during the status-read stop stage (the patched _own_exit runs
        # exactly there, before the kill stages).
        stop_go = os.path.join(drain_dir, "stop-go")
        stop_done = os.path.join(drain_dir, "stop-done")
        saved_own_exit = _own_exit

        def releasing_own_exit(proc):
            release_and_wait(stop_go, stop_done)
            return saved_own_exit(proc)
        globals()["_own_exit"] = releasing_own_exit
        try:
            drained_stop("output during the status-read stop stage is drained after the stop",
                         released_writer(stop_go, stop_done, "FAIL: stop-stage diagnostic\n"),
                         b"FAIL: stop-stage diagnostic\n", lambda pid: 0.0)
        finally:
            globals()["_own_exit"] = saved_own_exit

        # EVERY timeout snapshot recovers the bytes communicate had already read: a child that
        # writes both streams, CLOSES both pipes and keeps running makes every later communicate
        # call time out in its own wait with NO snapshot (the bytes then sit only in Popen's
        # buffers, where the post-stop drain cannot see them either: the streams are closed), so
        # without the per-snapshot recovery the budget Hung would carry None for both streams.
        closed_done = os.path.join(drain_dir, "closed-done")
        closed_code = ("import os, sys, time\n"
                       "sys.stdout.write(\"OUT: closed-pipe evidence\\n\")\nsys.stdout.flush()\n"
                       "sys.stderr.write(\"FAIL: closed-pipe diagnostic\\n\")\nsys.stderr.flush()\n"
                       "os.close(1)\nos.close(2)\n"
                       "open(sys.argv[2], \"w\").write(\"d\")\n"
                       "time.sleep(3600)\n")
        closed_calls, closed_ready = [], []

        def closed_clock():
            # The budget fires only two polls after the child wrote, closed both pipes and
            # signalled so through its done file: communicate gets a full window to consume the
            # bytes and both EOFs first, so the losing snapshot shape is deterministic, not raced.
            closed_calls.append(True)
            if len(closed_calls) == 1:
                return 0.0
            if os.path.exists(closed_done):
                closed_ready.append(True)
                if len(closed_ready) >= 3:
                    return 3600.0
            return 0.0
        closed_made = []
        saved_closed_cls = subprocess.Popen
        subprocess.Popen = recording_popen(closed_made, saved_closed_cls)
        closed_watchdog = handle_watchdog(30.0, closed_made)
        try:
            try:
                run([python, "-I", "-B", "-c", closed_code, token, closed_done], budget_s=3,
                    stall_s=3600, poll_s=0.2, clock=closed_clock, cpu_seconds=lambda pid: 0.0)
                got = "returned"
            except Hung as exc:
                got = (exc.kind, exc.output, exc.stderr)
            except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_closed_cls
            closed_watchdog.cancel()
        for pid in alive(token):
            _kill_pid(pid)
        if got != ("budget", b"OUT: closed-pipe evidence\n", b"FAIL: closed-pipe diagnostic\n"):
            failures.append("_load_bound vector (a child that closed both pipes and kept running "
                            "keeps the bytes its reads captured, on every timeout snapshot): got "
                            "{!r}".format(got))
    finally:
        shutil.rmtree(drain_dir)

    # The drain clamp's committed boundaries, driven on a small injected bound (a kernel pipe can
    # hold the whole over-bound input): one byte inside the bound drains fully with no note; an
    # input that exactly exhausts the bound stops AT it and reports the cut (the drain cannot
    # know nothing remained: the conservative direction); one byte over it drains exactly the
    # bound and no more; a partial-read sequence (7-byte chunks) still sums to the exact bound
    # and never requests past it; and a failure on ONE stream is reported naming that stream
    # while the other still drains. Each case runs in its own helper call, so every descriptor
    # name is bound once and closed once, in the helper's finally.
    class RaisingStream:
        closed = False

        def fileno(self):
            raise OSError("injected stderr drain fault")

    def drained_fake(out_payload, err_payload, read=None):
        """(held, notes) after _drain over one fake proc whose stdout and stderr pipes hold the
        two payloads, each write end already closed (EOF sits behind the payload); read stands in
        for os.read during the drain."""
        out_read, out_write = os.pipe()
        with os.fdopen(out_write, "wb") as out_handle:
            out_handle.write(out_payload)
        err_read, err_write = os.pipe()
        with os.fdopen(err_write, "wb") as err_handle:
            err_handle.write(err_payload)
        fake = subprocess.CompletedProcess([], 0)  # anything with the two stream attributes
        fake.stdout = os.fdopen(out_read, "rb")
        fake.stderr = os.fdopen(err_read, "rb")
        held_case = [None, None]
        saved_read = os.read
        os.read = read if read is not None else saved_read
        try:
            notes = _drain(fake, held_case)
        finally:
            os.read = saved_read
            fake.stdout.close()
            fake.stderr.close()
        return held_case, notes

    def drained_fault(out_payload):
        """(held, notes) when the stderr stream's own fileno fails while stdout still drains."""
        out_read, out_write = os.pipe()
        with os.fdopen(out_write, "wb") as out_handle:
            out_handle.write(out_payload)
        fake = subprocess.CompletedProcess([], 0)
        fake.stdout = os.fdopen(out_read, "rb")
        fake.stderr = RaisingStream()
        held_case = [None, None]
        try:
            notes = _drain(fake, held_case)
        finally:
            fake.stdout.close()
        return held_case, notes

    saved_drain_bytes = DRAIN_BYTES
    globals()["DRAIN_BYTES"] = 1000
    try:
        for label, payload, want_len, want_cut in (
                ("one byte inside the bound", b"e" * 999, 999, False),
                ("exactly at the bound", b"e" * 1000, 1000, True),
                ("one byte over the bound", b"e" * 1001, 1000, True)):
            held_case, notes = drained_fake(b"ok", payload)
            got = (held_case[0], len(held_case[1] or b""), held_case[1] == payload[:1000],
                   "the stderr drain was cut short at its 1000 byte bound" in notes,
                   "stdout" in notes)
            if got != (b"ok", want_len, True, want_cut, False):
                failures.append("_load_bound vector (drain clamp, {}): expected (b'ok', {}, "
                                "True, {}, False), got {!r}"
                                .format(label, want_len, want_cut, got))
        # Partial reads over an OVER-BOUND input, every request judged against the allowance
        # remaining BEFORE it (recorded per stream with its returned length): a drain that asks
        # for min(65536, DRAIN_BYTES) instead of the remaining allowance passes a whole-run
        # maximum but over-reads here, keeping 1001 bytes and requesting 1000 where only 993
        # remain.
        partial_log = []
        saved_os_read = os.read

        def chunked_read(fd, size, saved_os_read=saved_os_read):
            data = saved_os_read(fd, min(size, 7))
            partial_log.append((fd, size, len(data)))
            return data
        held_case, notes = drained_fake(b"o" * 1001, b"p" * 1001, read=chunked_read)
        remaining = {}
        clamped = True
        for fd, requested, returned in partial_log:
            left = remaining.get(fd, 1000)
            if requested > left:
                clamped = False
            remaining[fd] = left - returned
        got = (held_case[0] == b"o" * 1000, held_case[1] == b"p" * 1000,
               len(held_case[0] or b""), len(held_case[1] or b""),
               "the stdout drain was cut short at its 1000 byte bound" in notes,
               "the stderr drain was cut short at its 1000 byte bound" in notes,
               clamped)
        if got != (True, True, 1000, 1000, True, True, True):
            failures.append("_load_bound vector (drain clamp, every partial read is clamped to "
                            "the allowance remaining before it, on both streams of a 1001-byte "
                            "input): got {!r}, per-stream (fd, requested, returned) log {!r}"
                            .format(got, partial_log))
        held_case, notes = drained_fault(b"kept")
        got = (held_case[0], held_case[1],
               "the stderr drain failed (OSError: injected stderr drain fault)" in notes,
               "stdout" in notes)
        if got != (b"kept", None, True, False):
            failures.append("_load_bound vector (drain clamp, a one-stream failure names that "
                            "stream while the other drains): got {!r}".format(got))
    finally:
        globals()["DRAIN_BYTES"] = saved_drain_bytes

    # A non-OSError fault AFTER the spawn (pidfd_open here) must not route around cleanup: the
    # staged stop still runs (the child dies, reported as a monitor Hung naming the fault) and
    # both pipes are closed, instead of the fault escaping run() over a running child.
    if hasattr(os, "pidfd_open"):
        made_procs = []
        saved_cls = subprocess.Popen

        class SpawnRecorder(saved_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made_procs.append(self)

        saved_pidfd_open = os.pidfd_open

        def raising_pidfd_open(pid, flags=0):
            raise RuntimeError("injected pidfd_open fault")
        spawn_watchdog = handle_watchdog(30.0, made_procs)
        subprocess.Popen = SpawnRecorder
        os.pidfd_open = raising_pidfd_open
        try:
            try:
                run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token],
                    budget_s=3600, stall_s=3600, poll_s=0.2)
                got = "returned"
            except Hung as exc:
                got = ("hung: " + exc.kind, "injected pidfd_open fault" in exc.reason)
            except Exception as exc:  # noqa: BLE001  the pinned defect escapes exactly here
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen = saved_cls
            os.pidfd_open = saved_pidfd_open
            spawn_watchdog.cancel()
        state = "no child spawned"
        if made_procs:
            spawned = made_procs[-1]
            for _ in range(50):  # a SIGKILLed child can take a moment to be reaped
                if spawned.poll() is not None:
                    break
                time.sleep(0.1)
            state = (spawned.poll() is not None,
                     spawned.stdout is None or spawned.stdout.closed,
                     spawned.stderr is None or spawned.stderr.closed)
            if spawned.poll() is None:
                spawned.kill()
                try:
                    spawned.wait(timeout=REAP_S)
                except subprocess.TimeoutExpired:
                    pass
        for pid in alive(token):
            _kill_pid(pid)
        if got != ("hung: monitor", True) or state != (True, True, True):
            failures.append("_load_bound vector (a non-OSError fault after the spawn still stops "
                            "the child and closes the pipes): got {!r}, child stopped and pipes "
                            "closed {!r}".format(got, state))

    # A fault raised right AFTER Popen returned, BEFORE the very next statement runs (injected
    # through a trace function armed on run()'s own frame: it fires on the first line event at
    # which the acquired child is bound to proc), must run the staged stop and close both pipes:
    # the cleanup owns the child from before the spawn, so no boundary between the Popen return
    # and a later protected scope is uncovered.
    trace_procs = []
    saved_trace_cls = subprocess.Popen

    class TraceSpawnRecorder(saved_trace_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            trace_procs.append(self)

    trace_state = {"raised": False}
    run_code = run.__code__

    def trace_local(frame, event, arg):
        if (event == "line" and not trace_state["raised"] and trace_procs
                and frame.f_locals.get("proc") is trace_procs[-1]):
            trace_state["raised"] = True
            raise RuntimeError("injected post-spawn boundary fault")
        return trace_local

    def trace_global(frame, event, arg):
        if frame.f_code is run_code:
            return trace_local
        return None
    trace_watchdog = handle_watchdog(30.0, trace_procs)
    subprocess.Popen = TraceSpawnRecorder
    sys.settrace(trace_global)
    try:
        try:
            run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token],
                budget_s=3600, stall_s=3600, poll_s=0.2)
            got = "returned"
        except Hung as exc:
            got = ("hung: " + exc.kind, "injected post-spawn boundary fault" in exc.reason)
        except Exception as exc:  # noqa: BLE001  an uncovered boundary escapes exactly here
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        sys.settrace(None)
        subprocess.Popen = saved_trace_cls
        trace_watchdog.cancel()
    state = "no child spawned"
    if trace_procs:
        spawned = trace_procs[-1]
        for _ in range(50):  # a SIGKILLed child can take a moment to be reaped
            if spawned.poll() is not None:
                break
            time.sleep(0.1)
        state = (spawned.poll() is not None,
                 spawned.stdout is None or spawned.stdout.closed,
                 spawned.stderr is None or spawned.stderr.closed)
        if spawned.poll() is None:
            spawned.kill()
            try:
                spawned.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
        for stream in (spawned.stdin, spawned.stdout, spawned.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    for pid in alive(token):
        _kill_pid(pid)
    if got != ("hung: monitor", True) or state != (True, True, True) or not trace_state["raised"]:
        failures.append("_load_bound vector (a fault right after Popen returns, before the next "
                        "statement, still stops the child and closes the pipes): got {!r}, child "
                        "stopped and pipes closed {!r}, fault injected {!r}"
                        .format(got, state, trace_state["raised"]))

    # A fault raised after Popen returned but BEFORE the local binding to proc ran (injected
    # through OPCODE tracing: the raise lands on the store instruction itself, which no line
    # event reaches) must run the staged stop and close both pipes too: the ownership holder,
    # appended before the fork, owns the child while proc is still None, so the PRE-assignment
    # boundary is covered beside the post-assignment one above.
    opcode_procs = []
    saved_opcode_cls = subprocess.Popen

    class OpcodeSpawnRecorder(saved_opcode_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opcode_procs.append(self)

    opcode_state = {"raised": False}

    def opcode_trace_local(frame, event, arg):
        frame.f_trace_opcodes = True
        if (event == "opcode" and not opcode_state["raised"] and opcode_procs
                and frame.f_locals.get("proc") is None):
            opcode_state["raised"] = True
            raise RuntimeError("injected pre-assignment boundary fault")
        return opcode_trace_local

    def opcode_trace_global(frame, event, arg):
        if frame.f_code is run_code:
            frame.f_trace_opcodes = True
            return opcode_trace_local
        return None
    opcode_watchdog = handle_watchdog(30.0, opcode_procs)
    subprocess.Popen = OpcodeSpawnRecorder
    sys.settrace(opcode_trace_global)
    try:
        try:
            run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token],
                budget_s=3600, stall_s=3600, poll_s=0.2)
            got = "returned"
        except Hung as exc:
            got = ("hung: " + exc.kind, "injected pre-assignment boundary fault" in exc.reason)
        except Exception as exc:  # noqa: BLE001  an uncovered boundary escapes exactly here
            got = "{}: {}".format(type(exc).__name__, exc)
    finally:
        sys.settrace(None)
        subprocess.Popen = saved_opcode_cls
        opcode_watchdog.cancel()
    state = "no child spawned"
    if opcode_procs:
        spawned = opcode_procs[-1]
        for _ in range(50):  # a SIGKILLed child can take a moment to be reaped
            if spawned.poll() is not None:
                break
            time.sleep(0.1)
        state = (spawned.poll() is not None,
                 spawned.stdout is None or spawned.stdout.closed,
                 spawned.stderr is None or spawned.stderr.closed)
        if spawned.poll() is None:
            spawned.kill()
            try:
                spawned.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
        for stream in (spawned.stdin, spawned.stdout, spawned.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    for pid in alive(token):
        _kill_pid(pid)
    if got != ("hung: monitor", True) or state != (True, True, True) or not opcode_state["raised"]:
        failures.append("_load_bound vector (a fault after Popen returns, before proc is bound, "
                        "still stops the child and closes the pipes): got {!r}, child stopped "
                        "and pipes closed {!r}, fault injected {!r}"
                        .format(got, state, opcode_state["raised"]))

    # Round-10: a fault raised INSIDE the spawn, at the store of the child's pid onto the
    # Popen object (opcode-traced on Popen._execute_child's own code: the instruction right
    # after the fork primitive returned), must still stop the child and close both pipes.
    # The fork primitive handed to the per-call _execute_child copy (_acquiring_popen) holds
    # the pid on the acquiring object before control returns to interpreted code, so the
    # round-9 window between the fork returning and Popen's pid binding is owned, not merely
    # disclosed (red when that capture, or the _partial_child fallback reading it, is
    # removed: the injected fault then escapes run() over a running child).
    saved_fork_cls = subprocess.Popen  # the class inspected and subclassed below, and restored after the run
    exec_child_attr = getattr(saved_fork_cls, "_execute_child", None)
    exec_child_inner = getattr(exec_child_attr, "__func__", exec_child_attr)
    pid_store_offsets = set()
    if exec_child_inner is not None and "_fork_exec" in getattr(exec_child_inner,
                                                                "__globals__", {}):
        import dis
        pid_store_offsets = {ins.offset
                             for ins in dis.get_instructions(exec_child_inner.__code__)
                             if ins.opname == "STORE_ATTR" and ins.argval == "pid"}
    if os.name == "posix" and pid_store_offsets:
        fork_procs = []

        class ForkWindowRecorder(saved_fork_cls):
            def __init__(self, *args, **kwargs):
                fork_procs.append(self)  # before super().__init__: the fault lands inside it
                super().__init__(*args, **kwargs)

        fork_state = {"raised": False}
        exec_child_code = exec_child_inner.__code__

        def fork_trace_local(frame, event, arg):
            frame.f_trace_opcodes = True
            if (event == "opcode" and not fork_state["raised"]
                    and frame.f_lasti in pid_store_offsets):
                fork_state["raised"] = True
                raise RuntimeError("injected pre-pid-store spawn fault")
            return fork_trace_local

        def fork_trace_global(frame, event, arg):
            if frame.f_code is exec_child_code:
                frame.f_trace_opcodes = True
                return fork_trace_local
            return None
        fork_watchdog = handle_watchdog(30.0, fork_procs)
        subprocess.Popen = ForkWindowRecorder
        sys.settrace(fork_trace_global)
        try:
            try:
                run([python, "-I", "-B", "-c", "import time\ntime.sleep(3600)\n", token],
                    budget_s=3600, stall_s=3600, poll_s=0.2)
                got = "returned"
            except Hung as exc:
                got = ("hung: " + exc.kind, "injected pre-pid-store spawn fault" in exc.reason)
            except Exception as exc:  # noqa: BLE001  an unowned window escapes exactly here
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            sys.settrace(None)
            subprocess.Popen = saved_fork_cls
            fork_watchdog.cancel()
        state = "no child spawned"
        if fork_procs:
            spawned = fork_procs[-1]
            if getattr(spawned, "pid", None) is None:
                state = "no pid recorded on the acquiring object"
            else:
                for _ in range(50):  # a SIGKILLed child can take a moment to be reaped
                    if spawned.poll() is not None:
                        break
                    time.sleep(0.1)
                state = (getattr(spawned, "_acquired_pid", None) is not None,
                         spawned.poll() is not None,
                         spawned.stdout is None or spawned.stdout.closed,
                         spawned.stderr is None or spawned.stderr.closed)
                if spawned.poll() is None:
                    spawned.kill()
                    try:
                        spawned.wait(timeout=REAP_S)
                    except subprocess.TimeoutExpired:
                        pass
        for pid in alive(token):
            _kill_pid(pid)
        if (got != ("hung: monitor", True) or state != (True, True, True, True)
                or not fork_state["raised"]):
            failures.append("_load_bound vector (a fault at the in-spawn pid store still stops "
                            "the child and closes the pipes: the fork window is owned): got "
                            "{!r}, (pid held, child stopped, pipes closed) {!r}, fault "
                            "injected {!r}".format(got, state, fork_state["raised"]))

    # The watchdog ITSELF is enforced: at expiry it must stop the fixture's whole process group
    # through the retained group id, with the survivor scan forced BLIND (injected), so a
    # pipe-holding descendant cannot outlive it even where /proc is unreadable. The fixture's
    # leader exits at once while its descendant inherits and holds both output pipes, so only the
    # group kill can release the blocked communicate; the descendant's own bounded 30 s sleep
    # turns a regression here into a failure, never a hung self-test.
    if os.name == "posix" and getattr(os, "killpg", None) is not None:
        expiry_made = []
        blind_scans = []
        saved_expiry_cls = subprocess.Popen
        saved_expiry_alive = _alive_with

        def blind_alive(tok, opener=None):
            blind_scans.append(tok)
            return None
        subprocess.Popen = recording_popen(expiry_made, saved_expiry_cls)
        globals()["_alive_with"] = blind_alive
        expiry_watchdog = handle_watchdog(6.0, expiry_made)
        expiry_began = time.monotonic()
        try:
            try:
                done = run([python, "-I", "-B", "-c",
                            "import subprocess, sys\n"
                            "subprocess.Popen([sys.executable, \"-I\", \"-c\","
                            " \"import time; time.sleep(30)\", sys.argv[1]])\n"
                            "sys.exit(7)\n", token], budget_s=3600, stall_s=3600, poll_s=0.2)
                got = done.returncode
            except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            expiry_watchdog.cancel()
            expiry_watchdog.join(15.0)  # the firing timer must finish BEFORE the scan is restored
            globals()["_alive_with"] = saved_expiry_alive
            subprocess.Popen = saved_expiry_cls
        expiry_took = time.monotonic() - expiry_began
        left = alive(token)
        for pid in left:
            _kill_pid(pid)
        if (got, expiry_took <= 20.0, bool(blind_scans), left) != (7, True, True, []):
            failures.append("_load_bound vector (the expired watchdog stops the whole fixture "
                            "group through the retained group id, beside a blind survivor scan): "
                            "expected exit 7 within 20 s beside an attempted scan and no child "
                            "left, got {!r} after {:.1f} s, scans {!r}, left alive {!r}"
                            .format(got, expiry_took, blind_scans, left))

    # UNOBSERVABLE is never a verdict: a capability probe that cannot RUN (pidfd_open refused,
    # for example EMFILE) must read as None, never as unsupported (False) or supported, and a
    # survivor scan whose /proc cmdline read is REFUSED while the process exists (EACCES) must
    # read as None, never as absence, while mid-scan ENOENT stays absence. Both injected, so the
    # real /proc and the real descriptor table are never touched.
    if hasattr(os, "pidfd_open") and getattr(signal, "pidfd_send_signal", None) is not None:
        saved_probe_opener = os.pidfd_open

        def emfile_pidfd_open(pid, flags=0):
            raise OSError(errno.EMFILE, "injected: too many open files")
        os.pidfd_open = emfile_pidfd_open
        try:
            probed = _group_signal_supported()
        finally:
            os.pidfd_open = saved_probe_opener
        if probed is not None:
            failures.append("_load_bound vector (a capability probe that cannot run is "
                            "unobservable, never a verdict): expected None, got {!r}"
                            .format(probed))
    if has_proc:
        def eacces_cmdline(path, mode="rb"):
            raise PermissionError(errno.EACCES, "injected: permission denied", path)

        def enoent_cmdline(path, mode="rb"):
            raise FileNotFoundError(errno.ENOENT, "injected: exited mid-scan", path)
        got = (_alive_with(token, opener=eacces_cmdline),
               _alive_with("absent-" + token, opener=enoent_cmdline))
        if got != (None, []):
            failures.append("_load_bound vector (a refused /proc read is unobservable, never "
                            "absence; mid-scan ENOENT is absence): expected (None, []), got {!r}"
                            .format(got))

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
    if os.name == "posix":
        # A live descendant of a stopped child is killed with it: the stop signals the child's own
        # process group (which the descendant inherited), never a scanned pid list, and a bystander
        # this process launched (outside that group) survives every stop, the forced numeric
        # fallback below included: with no process-table scan there is no injected ancestry that
        # could redirect a signal at it. These process-group vectors do NOT depend on CPU
        # accounting: where tree CPU time is readable the heuristic stall bound stops each child;
        # where it is not (the stall test is off by design) a small load-adjusted budget stops it
        # instead, so the group, bystander, kill-path, crafted-table and reaped-leader coverage
        # runs either way.
        cpu_ok = tree_cpu_seconds(os.getpid()) is not None
        stop_want = "hung: stall" if cpu_ok else "hung: budget"

        def group_stop_bounds():
            if cpu_ok:
                return dict(budget_s=3600, stall_s=1.5, poll_s=0.2)
            return dict(budget_s=2, stall_s=3600, clock=LoadClock(factor=lambda: 1.0), poll_s=0.2)
        descendant_code = ("import subprocess, sys, time\n"
                           "subprocess.Popen([sys.executable, \"-I\", \"-c\","
                           " \"import time; time.sleep(3600)\", sys.argv[1]])\ntime.sleep(3600)\n")
        try:
            probe_fd = os.pidfd_open(os.getpid()) if hasattr(os, "pidfd_open") else None
        except OSError:
            probe_fd = None
        if probe_fd is not None:
            os.close(probe_fd)
        sender_available = (probe_fd is not None
                            and getattr(signal, "pidfd_send_signal", None) is not None)
        bystander = subprocess.Popen(["sleep", "3600"])
        einval_flags, fallback_pgids = [], []
        try:
            bounded("a live descendant is killed with the tree, through the child's process group",
                    descendant_code, stop_want, 25, **group_stop_bounds())
            # The group stop does not depend on CPU accounting: with tree CPU seconds UNREADABLE
            # (injected None), the load-adjusted budget still stops the child and its descendant
            # through the process group, so this coverage cannot silently narrow to hosts where
            # the stall heuristic happens to be available.
            bounded("a group stop runs with CPU accounting unreadable (the budget fallback)",
                    descendant_code, "hung: budget", 25, budget_s=2, stall_s=3600,
                    clock=LoadClock(factor=lambda: 1.0), poll_s=0.2, cpu_seconds=lambda pid: None)
            survived = bystander.poll()
            # A kernel refusing the pidfd group flag (EINVAL, forced here with a flag no kernel
            # accepts) falls back to the numeric group id while the leader is unreaped, so the
            # descendant still dies with the group; the EINVAL refusal and the numeric fallback
            # are both OBSERVED through recorders (where the sender exists), never assumed from
            # the flag alone, and the SAME bystander lives through this stop too; after a reap
            # that fallback sends nothing (the reaped-leader vectors below).
            saved_flag = PIDFD_SIGNAL_PROCESS_GROUP
            saved_sender = getattr(signal, "pidfd_send_signal", None)
            saved_fallback_killpg = getattr(os, "killpg", None)

            def einval_recorder(pidfd, sig, info=None, flags=0):
                try:
                    return saved_sender(pidfd, sig, info, flags)
                except OSError as exc:
                    if exc.errno == errno.EINVAL:
                        einval_flags.append(flags)
                    raise

            def fallback_killpg_recorder(pgid, sig):
                fallback_pgids.append(pgid)
                return saved_fallback_killpg(pgid, sig)
            globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = 1 << 30
            if saved_sender is not None:
                signal.pidfd_send_signal = einval_recorder
            if saved_fallback_killpg is not None:
                os.killpg = fallback_killpg_recorder
            try:
                bounded("the killpg fallback kills the group while the leader is unreaped",
                        descendant_code, stop_want, 25, **group_stop_bounds())
            finally:
                globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = saved_flag
                if saved_sender is not None:
                    signal.pidfd_send_signal = saved_sender
                if saved_fallback_killpg is not None:
                    os.killpg = saved_fallback_killpg
            survived_fallback = bystander.poll()
        finally:
            bystander.kill()
            try:
                bystander.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
        if survived is not None or survived_fallback is not None:
            failures.append("_load_bound vector (a bystander outside the child's group survives "
                            "every stop, the killpg fallback included): it was signalled (poll "
                            "{!r}, fallback poll {!r})".format(survived, survived_fallback))
        if sender_available and (einval_flags != [1 << 30] or len(fallback_pgids) != 1):
            failures.append("_load_bound vector (the forced-EINVAL fallback is observed, never "
                            "assumed): expected exactly one EINVAL refusal of flag {} and one "
                            "numeric killpg, got refusals {!r} and killpg of {!r}"
                            .format(1 << 30, einval_flags, fallback_pgids))

        # The kill path a stop takes is OBSERVED: where the kernel accepts the pidfd group-signal
        # flag (probed on this process's own pid record), the stop must take the exact pidfd path
        # and never the numeric fallback; without that support the numeric fallback must run while
        # the leader is unreaped. A stop that silently trades the exact path for the numeric one
        # turns this vector red on every group-flag kernel.
        paths_taken = []
        saved_sender2 = getattr(signal, "pidfd_send_signal", None)
        saved_killpg2 = getattr(os, "killpg", None)

        def path_recording_sender(pidfd, sig, info=None, flags=0):
            paths_taken.append(("pidfd", flags))
            return saved_sender2(pidfd, sig, info, flags)

        def path_recording_killpg(pgid, sig):
            paths_taken.append(("killpg",))
            return saved_killpg2(pgid, sig)
        if saved_sender2 is not None:
            signal.pidfd_send_signal = path_recording_sender
        if saved_killpg2 is not None:
            os.killpg = path_recording_killpg
        try:
            bounded("the kill path taken by the stop is recorded", "import time\ntime.sleep(3600)\n",
                    stop_want, 25, **group_stop_bounds())
        finally:
            if saved_sender2 is not None:
                signal.pidfd_send_signal = saved_sender2
            if saved_killpg2 is not None:
                os.killpg = saved_killpg2
        group_flag = _group_signal_supported()
        if group_flag is None:
            # UNOBSERVABLE is not unsupported: a probe that could not run must not silently select
            # the fallback expectations or drop the pidfd coverage below.
            failures.append("_load_bound vector (the pidfd group-signal capability is observable): "
                            "the probe could not run, so the kill-path and reaped-leader vectors "
                            "have no clean verdict")
        if group_flag:
            if ("pidfd", PIDFD_SIGNAL_PROCESS_GROUP) not in paths_taken or ("killpg",) in paths_taken:
                failures.append("_load_bound vector (the pidfd group kill runs wherever the kernel "
                                "offers it): kill path ran {!r}".format(paths_taken))
        elif group_flag is False and saved_killpg2 is not None and ("killpg",) not in paths_taken:
            failures.append("_load_bound vector (the numeric fallback runs, while the leader is "
                            "unreaped, where the kernel lacks the group flag): kill path ran {!r}"
                            .format(paths_taken))

        # A crafted ancestor table cannot redirect a signal: no stop stage selects victims from a
        # process table (the removed table-driven kill did), so a _proc_table that names this live
        # bystander as the stopped child's descendant must leave it unsignalled. A regression to
        # table-driven victim selection kills the bystander here and turns this vector red.
        table_bystander = subprocess.Popen(["sleep", "3600"])
        spawned_pids = []
        saved_popen_cls2 = subprocess.Popen

        class RecordingPopen(saved_popen_cls2):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                spawned_pids.append(self.pid)

        saved_proc_table = _proc_table
        # The lie names the bystander as a descendant of the child, keeping the bystander's REAL
        # cpu and start ticks (read from its own stat): a table-driven kill that re-validates the
        # start ticks before signalling would still accept this entry, so a regression to the
        # removed table-driven kill, with or without that validation, turns this vector red. The
        # crafting REQUIRES a complete usable stat entry (_stat_entry): a stat read that fails,
        # and a run whose table injection never happened, are each recorded as UNOBSERVABLE (no
        # clean verdict), never patched over with zero start ticks (which a validating kill would
        # reject, leaving this vector unable to discriminate) and never a silent pass.
        bystander_entry = _stat_entry(table_bystander.pid)
        if bystander_entry is None:
            table_bystander.kill()
            try:
                table_bystander.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
            if has_proc:
                failures.append("_load_bound vector (the crafted-table bystander stat is "
                                "observable): its /proc stat could not be read, so the "
                                "crafted-table vector has no clean verdict")
        else:
            injected_tables = []

            def crafted_table(limit_s=None, names=None, clock=None, opener=None):
                table, complete = saved_proc_table(limit_s, names, clock, opener)
                if table is not None and spawned_pids:
                    table = dict(table)
                    table[table_bystander.pid] = (spawned_pids[-1],) + bystander_entry[1:]
                    injected_tables.append(True)
                return table, complete
            subprocess.Popen = RecordingPopen
            globals()["_proc_table"] = crafted_table
            try:
                bounded("a crafted ancestor table cannot redirect a signal at a bystander",
                        "import time\ntime.sleep(3600)\n", stop_want, 25, **group_stop_bounds())
                table_survived = table_bystander.poll()
            finally:
                subprocess.Popen = saved_popen_cls2
                globals()["_proc_table"] = saved_proc_table
                table_bystander.kill()
                try:
                    table_bystander.wait(timeout=REAP_S)
                except subprocess.TimeoutExpired:
                    pass
            if table_survived is not None:
                failures.append("_load_bound vector (a crafted ancestor table cannot redirect a "
                                "signal): the bystander was signalled (poll {!r})"
                                .format(table_survived))
            if not injected_tables:
                failures.append("_load_bound vector (the crafted table is injected): no process-"
                                "table read succeeded during the stop, so the crafted-table "
                                "vector has no clean verdict")
        # The unobservable branch itself is tested: a stat read that fails, and a table lacking
        # the pid, each yield None from the shared _stat_entry, never a zero-tick substitute.
        probe_got = (_stat_entry(table_bystander.pid, proc_table=lambda names: (None, False)),
                     _stat_entry(table_bystander.pid,
                                 proc_table=lambda names: ({-1: (1, 2, 3)}, True)))
        if probe_got != (None, None):
            failures.append("_load_bound vector (a failed or missing bystander stat yields no "
                            "entry): expected (None, None), got {!r}".format(probe_got))

        # The pidfd group kill stays exact AFTER Popen has reaped the leader: a child that spawns
        # a group member on devnull, prints an undecodable byte and exits 0 is reaped by
        # communicate before the strict decode fails, so the stop runs with the leader's status
        # already read; where the kernel offers the group flag the member must still die with the
        # group (through the leader's own pid record, which its reap does not free).
        reap_code = ("import subprocess, sys\n"
                     "subprocess.Popen([sys.executable, \"-I\", \"-c\","
                     " \"import time; time.sleep(3600)\", sys.argv[1]],"
                     " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                     "sys.stdout.buffer.write(bytes([255]))\nsys.exit(0)\n")
        if group_flag:
            reaped_member = "load-bound-reaped-member-{}".format(os.getpid())
            try:
                run([python, "-I", "-B", "-c", reap_code, reaped_member], budget_s=60, stall_s=60,
                    text=True)
                got = "returned"
            except Hung as exc:
                got = (exc.kind, exc.returncode, exc.output)
            except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
            left = alive(reaped_member)
            for _ in range(50):  # a SIGKILLed member can take a moment to leave the process table
                if not left:
                    break
                time.sleep(0.1)
                left = alive(reaped_member)
            for pid in left:
                _kill_pid(pid)
            if got != ("monitor", 0, "\ufffd") or left:
                failures.append("_load_bound vector (the pidfd group kill is exact after the "
                                "leader reap): expected (monitor, 0, the replacement character) "
                                "and no group member left, got {!r}, left alive {!r}"
                                .format(got, left))
        # Without the group flag the same stop after a reap signals NOTHING by number: the member
        # SURVIVES, the disclosed fail-closed residual, and the Hung reason says so. Forced with a
        # flag no kernel accepts, so this leg runs on every kernel. The refusal and the absence of
        # numeric signalling are OBSERVED on this leg too, never inferred from the member's
        # survival alone: the forced flag must be refused with EINVAL exactly once, and no os.kill
        # or os.killpg may run after the leader was reaped.
        residual_member = "load-bound-residual-member-{}".format(os.getpid())
        saved_flag2 = PIDFD_SIGNAL_PROCESS_GROUP
        residual_einvals, residual_numeric = [], []
        saved_sender3 = getattr(signal, "pidfd_send_signal", None)
        saved_kill3, saved_killpg3 = os.kill, getattr(os, "killpg", None)

        def residual_sender(pidfd, sig, info=None, flags=0):
            try:
                return saved_sender3(pidfd, sig, info, flags)
            except OSError as exc:
                if exc.errno == errno.EINVAL:
                    residual_einvals.append(flags)
                raise

        def residual_kill(pid, sig):
            residual_numeric.append(("kill", pid))
            return saved_kill3(pid, sig)

        def residual_killpg(pgid, sig):
            residual_numeric.append(("killpg", pgid))
            return saved_killpg3(pgid, sig)
        globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = 1 << 30
        if saved_sender3 is not None:
            signal.pidfd_send_signal = residual_sender
        os.kill = residual_kill
        if saved_killpg3 is not None:
            os.killpg = residual_killpg
        try:
            try:
                run([python, "-I", "-B", "-c", reap_code, residual_member], budget_s=60, stall_s=60,
                    text=True)
                got = "returned"
            except Hung as exc:
                got = (exc.kind, exc.returncode,
                       "a descendant still in that group survives the stop" in exc.reason)
            except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
        finally:
            globals()["PIDFD_SIGNAL_PROCESS_GROUP"] = saved_flag2
            if saved_sender3 is not None:
                signal.pidfd_send_signal = saved_sender3
            os.kill = saved_kill3
            if saved_killpg3 is not None:
                os.killpg = saved_killpg3
        survivors = alive(residual_member)
        for pid in survivors:
            _kill_pid(pid)
        if got != ("monitor", 0, True) or not survivors:
            failures.append("_load_bound vector (after a reaped leader without the group flag, "
                            "the member survives as the disclosed residual and the reason says "
                            "so): got {!r}, survivors {!r}".format(got, survivors))
        if saved_sender3 is not None and residual_einvals != [1 << 30]:
            failures.append("_load_bound vector (the post-reap EINVAL itself is recorded): expected "
                            "exactly one refusal of flag {}, got {!r}"
                            .format(1 << 30, residual_einvals))
        if residual_numeric:
            failures.append("_load_bound vector (nothing is signalled by number after the leader "
                            "reap): numeric calls ran {!r}".format(residual_numeric))

    # The checked parent-death arming, and the outer stops it exists for. Skipped without libc
    # prctl: there the survival of the child is the disclosed residual.
    if _PRCTL is not None:
        outer_dir = tempfile.mkdtemp(prefix="load-bound-vector-outer-")
        try:
            # A REFUSED prctl (injected return of -1) ends the child before it runs: run() returns
            # that child's UNARMED_EXIT with the diagnostic on its captured stderr, and the child's
            # survival marker stays absent.
            refused_marker = os.path.join(outer_dir, "refused-survival")
            saved_prctl = _PRCTL
            globals()["_PRCTL"] = lambda *args: -1
            try:
                try:
                    done = run([python, "-I", "-B", "-c",
                                "open({!r}, \"w\").write(\"survived\")\n".format(refused_marker),
                                token], budget_s=60, stall_s=60)
                    got = ("returned", done.returncode)
                except Unarmed as exc:
                    # The named outcome, never an ordinary exit status: an OSError whose message
                    # and whose captured stderr both name the refused arming.
                    got = ("unarmed", "refused to run" in str(exc),
                           _UNARMED_MARKER in (exc.stderr or b""))
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                globals()["_PRCTL"] = saved_prctl
            if got != ("unarmed", True, True) or os.path.exists(refused_marker):
                failures.append("_load_bound vector (a refused prctl surfaces as the named "
                                "Unarmed outcome, the child never run): got {!r}, survival "
                                "marker written {!r}"
                                .format(got, os.path.exists(refused_marker)))

            # A parent gone BEFORE the arming completed (the fork-to-prctl race) is detected by the
            # recorded-pid check: armed against a recorded pid that is not this child's parent (pid
            # 1 here stands in for the dead spawner), the child exits at once, marker absent.
            mismatch_marker = os.path.join(outer_dir, "mismatch-survival")
            probe = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                      "open({!r}, \"w\").write(\"survived\")\n"
                                      .format(mismatch_marker)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     preexec_fn=lambda: _arm_pdeathsig(1, 2))  # fd 2 stands in:
                                     # the mismatch (recorded parent 1) exits before any success write
            probe_err = probe.stderr.read()
            probe.stderr.close()
            probe_rc = probe.wait(timeout=30)
            if ((probe_rc, b"parent-death signal was not armed" in probe_err,
                 os.path.exists(mismatch_marker)) != (UNARMED_EXIT, True, False)):
                failures.append("_load_bound vector (a parent gone before the arming ends the "
                                "child): rc {!r}, diagnostic carried {!r}, survival marker written "
                                "{!r}".format(probe_rc, probe_err,
                                              os.path.exists(mismatch_marker)))

            # A refused arming whose DIAGNOSTIC write also fails is still the named Unarmed: the
            # refusal is signalled by the missing success byte on the dedicated pipe, never
            # inferred from best-effort stderr (EMPTY here by injection: the diagnostic raises
            # EIO in the child, which inherits the patched os.write across the fork).
            diag_marker = os.path.join(outer_dir, "diag-survival")
            saved_prctl2 = _PRCTL
            saved_diag_write = os.write

            def failing_diag_write(fd, data, saved_diag_write=saved_diag_write):
                if fd == 2 and data.startswith(b"_load_bound: the parent-death signal"):
                    raise OSError(errno.EIO, "injected diagnostic-write failure")
                return saved_diag_write(fd, data)
            globals()["_PRCTL"] = lambda *args: -1
            os.write = failing_diag_write
            try:
                try:
                    done = run([python, "-I", "-B", "-c",
                                "open({!r}, \"w\").write(\"survived\")\n".format(diag_marker),
                                token], budget_s=60, stall_s=60)
                    got = ("returned", done.returncode)
                except Unarmed as exc:
                    got = ("unarmed", (exc.stderr or b"") == b"")
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                os.write = saved_diag_write
                globals()["_PRCTL"] = saved_prctl2
            if got != ("unarmed", True) or os.path.exists(diag_marker):
                failures.append("_load_bound vector (a refused arming with a failing diagnostic "
                                "write is still the named Unarmed, whatever stderr holds): got "
                                "{!r}, survival marker written {!r}"
                                .format(got, os.path.exists(diag_marker)))

            # A monitor fault AFTER a refused child completed leaves through the STOPPED path:
            # that path must still convert UNARMED_EXIT beside the missing byte into Unarmed
            # (removing the stopped-path conversion would leave a monitor Hung carrying the
            # ordinary-looking status 125 for a program that never ran).
            saved_prctl3 = _PRCTL
            saved_refusal_cls = subprocess.Popen

            class FaultAfterRefusal(saved_refusal_cls):
                def communicate(self, input=None, timeout=None):
                    self.wait(timeout=30)  # the refused child exits at once with UNARMED_EXIT
                    raise OSError("injected monitor fault after the refused child completed")
            globals()["_PRCTL"] = lambda *args: -1
            subprocess.Popen = FaultAfterRefusal
            try:
                try:
                    run([python, "-I", "-B", "-c", "pass\n", token], budget_s=60, stall_s=60)
                    got = "returned"
                except Unarmed as exc:
                    got = ("unarmed", _UNARMED_MARKER in (exc.stderr or b""))
                except Hung as exc:
                    got = ("hung: " + exc.kind, exc.returncode)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.Popen = saved_refusal_cls
                globals()["_PRCTL"] = saved_prctl3
            if got != ("unarmed", True):
                failures.append("_load_bound vector (a monitor fault after a refused child "
                                "completed still raises the named Unarmed on the stopped path): "
                                "expected the marker-carrying Unarmed, got {!r}".format(got))

            # Round-10: the STOPPED path detects a refusal by the missing success byte alone.
            # A refused child whose diagnostic write also failed (stderr EMPTY) beside a
            # monitor fault must still raise the named Unarmed (red when the stopped-path
            # condition is reverted to a marker test: with no marker on stderr, a marker test
            # would let the ordinary-looking status 125 ride the Hung for a program that
            # never ran).
            saved_prctl4 = _PRCTL
            saved_byte_write = os.write
            saved_stopped_cls = subprocess.Popen

            def failing_stopped_diag(fd, data, saved_byte_write=saved_byte_write):
                if fd == 2 and data.startswith(b"_load_bound: the parent-death signal"):
                    raise OSError(errno.EIO, "injected diagnostic-write failure")
                return saved_byte_write(fd, data)

            class FaultAfterSilentRefusal(saved_stopped_cls):
                def communicate(self, input=None, timeout=None):
                    self.wait(timeout=30)  # the refused child exits at once with UNARMED_EXIT
                    raise OSError("injected monitor fault after the silent refusal completed")
            globals()["_PRCTL"] = lambda *args: -1
            os.write = failing_stopped_diag
            subprocess.Popen = FaultAfterSilentRefusal
            try:
                try:
                    run([python, "-I", "-B", "-c", "pass\n", token], budget_s=60, stall_s=60)
                    got = "returned"
                except Unarmed as exc:
                    got = ("unarmed", (exc.stderr or b"") == b"")
                except Hung as exc:
                    got = ("hung: " + exc.kind, exc.returncode)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.Popen = saved_stopped_cls
                os.write = saved_byte_write
                globals()["_PRCTL"] = saved_prctl4
            if got != ("unarmed", True):
                failures.append("_load_bound vector (the stopped path detects a refusal by the "
                                "missing success byte alone: a silent refusal beside a monitor "
                                "fault is still the named Unarmed with empty stderr): got {!r}"
                                .format(got))

            # Round-10: an interruption AFTER the arming verdict was consulted can never turn
            # a completed arming into a reported refusal. The consult is non-consuming, so the
            # stopped path's own consult still sees the byte and a program that really exited
            # with the shared status keeps it (red on a consuming read, where the second
            # consult finds the pipe empty and misreports Unarmed).
            armed_consults = []
            saved_consult = _armed_byte

            def faulting_consult(fd, saved_consult=saved_consult,
                                 armed_consults=armed_consults):
                seen = saved_consult(fd)
                armed_consults.append(seen)
                if len(armed_consults) == 1:
                    raise RuntimeError("injected fault after the arming consult")
                return seen
            globals()["_armed_byte"] = faulting_consult
            try:
                try:
                    run([python, "-I", "-B", "-c", "import sys\nsys.exit(125)\n", token],
                        budget_s=60, stall_s=60)
                    got = "returned"
                except Unarmed:
                    got = "unarmed"
                except Hung as exc:
                    got = ("hung: " + exc.kind, exc.returncode)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                globals()["_armed_byte"] = saved_consult
            if got != ("hung: monitor", UNARMED_EXIT) or armed_consults != [True, True]:
                failures.append("_load_bound vector (an interruption after the arming consult "
                                "never reports Unarmed for a program that ran: the non-"
                                "consuming check is repeatable): expected (hung: monitor, 125) "
                                "and consults [True, True], got {!r}, consults {!r}"
                                .format(got, armed_consults))

            # Round-10: the arming descriptors sit at 3 or above, so a caller whose standard
            # descriptors are closed can neither receive the success byte into the child's
            # captured stdout or stderr nor misread a program's own UNARMED_EXIT as a refusal.
            # Each leg runs run() inside a subprocess whose descriptors 0 and 1 (or 0 and 2)
            # are closed, asserts the EXACT captured stdout and stderr, and requires a program
            # exiting with UNARMED_EXIT to keep status 125, never Unarmed.
            lowfd_script = os.path.join(outer_dir, "lowfd.py")
            with open(lowfd_script, "w", encoding="utf-8") as handle:
                handle.write(
                    "import os, sys\n"
                    "sys.path.insert(0, sys.argv[1])\n"
                    "import _load_bound\n"
                    "for fd in (int(sys.argv[3]), int(sys.argv[4])):\n"
                    "    os.close(fd)\n"
                    "results = []\n"
                    "done = _load_bound.run([sys.executable, \"-I\", \"-c\","
                    " \"print(12345)\"], budget_s=60, stall_s=60)\n"
                    "results.append((\"printer\", done.returncode, done.stdout, done.stderr))\n"
                    "try:\n"
                    "    done = _load_bound.run([sys.executable, \"-I\", \"-c\","
                    " \"import sys; sys.exit(125)\"], budget_s=60, stall_s=60)\n"
                    "    results.append((\"status\", done.returncode))\n"
                    "except _load_bound.Unarmed:\n"
                    "    results.append((\"status\", \"unarmed\"))\n"
                    "open(sys.argv[2], \"w\", encoding=\"utf-8\").write(repr(results))\n")
            vector_libdir = os.path.dirname(os.path.abspath(__file__))
            for leg, closed_pair in (("descriptors 0 and 1 closed", ("0", "1")),
                                     ("descriptors 0 and 2 closed", ("0", "2"))):
                lowfd_out = os.path.join(outer_dir,
                                         "lowfd-{}-{}".format(closed_pair[0], closed_pair[1]))
                try:
                    done = run([python, "-I", "-B", lowfd_script, vector_libdir, lowfd_out,
                                closed_pair[0], closed_pair[1], token], budget_s=120,
                               stall_s=60)
                    got = ("returned", done.returncode)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
                recorded = None
                if os.path.exists(lowfd_out):
                    with open(lowfd_out, "r", encoding="utf-8") as handle:
                        recorded = handle.read()
                want = repr([("printer", 0, b"12345\n", b""), ("status", 125)])
                if got != ("returned", 0) or recorded != want:
                    failures.append("_load_bound vector (the arming descriptors sit above the "
                                    "standard streams, {}): expected exit 0 and results {}, "
                                    "got {!r} with results {!r}"
                                    .format(leg, want, got, recorded))

            # Round-10: the arming pipe is acquired INSIDE the protected scope with cleanup
            # state initialized first, so a fault landing right after the acquisition (a line
            # trace raising at the first line event where armed_read is bound while proc is
            # still None) leaves NO descriptor open, and both descriptors sit at 3 or above
            # (red when the acquisition precedes the try, where the fault escapes before the
            # finally can close the pair).
            acquired_fds = []
            saved_pipe_above = _pipe_above_stdio

            def recording_pipe_above(saved_pipe_above=saved_pipe_above,
                                     acquired_fds=acquired_fds):
                pair = saved_pipe_above()
                acquired_fds.extend(pair)
                return pair

            pipe_state = {"raised": False}

            def pipe_trace_local(frame, event, arg):
                if (event == "line" and not pipe_state["raised"]
                        and frame.f_locals.get("armed_read") is not None
                        and frame.f_locals.get("proc") is None):
                    pipe_state["raised"] = True
                    raise RuntimeError("injected post-acquisition fault")
                return pipe_trace_local

            def pipe_trace_global(frame, event, arg):
                if frame.f_code is run_code:
                    return pipe_trace_local
                return None
            globals()["_pipe_above_stdio"] = recording_pipe_above
            sys.settrace(pipe_trace_global)
            try:
                try:
                    run([python, "-I", "-B", "-c", "pass\n", token], budget_s=60, stall_s=60)
                    got = "returned"
                except RuntimeError as exc:
                    got = str(exc)
                except Exception as exc:  # noqa: BLE001  any escape is this vector failing
                    got = "{}: {}".format(type(exc).__name__, exc)
            finally:
                sys.settrace(None)
                globals()["_pipe_above_stdio"] = saved_pipe_above

            def fd_open(fd):
                try:
                    os.fstat(fd)
                    return True
                except OSError:
                    return False
            fd_state = (len(acquired_fds), [fd for fd in acquired_fds if fd_open(fd)],
                        [fd for fd in acquired_fds if fd < 3])
            if (got, pipe_state["raised"],
                    fd_state) != ("injected post-acquisition fault", True, (2, [], [])):
                failures.append("_load_bound vector (the arming pipe is acquired inside the "
                                "protected scope: a fault right after the acquisition leaves "
                                "no descriptor open, both at 3 or above): got {!r}, fault "
                                "injected {!r}, (count, open, below 3) {!r}"
                                .format(got, pipe_state["raised"], fd_state))

            # The collision is RESOLVED, not merely disclosed: a program that itself exits with
            # UNARMED_EXIT while PRINTING the arming marker is not the refusal (its completed
            # arming wrote the success byte), so its exit status stands as its own.
            try:
                done = run([python, "-I", "-B", "-c",
                            "import sys\nsys.stderr.write(\"parent-death signal was not armed\\n\")\n"
                            "sys.exit(125)\n", token], budget_s=60, stall_s=60)
                got = ("returned", done.returncode)
            except Exception as exc:  # noqa: BLE001  an Unarmed here is this vector failing
                got = "{}: {}".format(type(exc).__name__, exc)
            if got != ("returned", UNARMED_EXIT):
                failures.append("_load_bound vector (a program exiting with the shared status "
                                "beside the printed marker keeps its own exit status: the "
                                "success byte distinguishes it): expected a returned 125, got "
                                "{!r}".format(got))

            # An outer stop of THIS process reaches the direct child through its armed parent-death
            # signal, for SIGTERM and SIGKILL of the parent INDEPENDENTLY. Each leg requires the
            # inner child's STARTED marker before judging (a leg whose child never ran judges
            # nothing and is its own failure), validates the outer launcher's exit status, and the
            # positive control below proves the survival detection itself, so none of these legs
            # can pass vacuously.
            libdir = os.path.dirname(os.path.abspath(__file__))
            inner = ("import sys, time\nopen(sys.argv[1], \"w\").write(\"started\")\n"
                     "time.sleep(6)\nopen(sys.argv[2], \"w\").write(\"survived\")\n")
            outer = os.path.join(outer_dir, "outer.py")
            with open(outer, "w", encoding="utf-8") as handle:
                handle.write("import sys\n"
                             "sys.path.insert(0, sys.argv[1])\n"
                             "import _load_bound\n"
                             "_load_bound.run([sys.executable, \"-I\", \"-c\", " + repr(inner) +
                             ", sys.argv[2], sys.argv[3], sys.argv[4]], budget_s=3600, "
                             "stall_s=3600)\n")
            unprotected = os.path.join(outer_dir, "unprotected.py")
            with open(unprotected, "w", encoding="utf-8") as handle:
                handle.write("import subprocess, sys, time\n"
                             "subprocess.Popen([sys.executable, \"-I\", \"-c\", " + repr(inner) +
                             ", sys.argv[2], sys.argv[3], sys.argv[4]], "
                             "start_new_session=True)\ntime.sleep(3600)\n")

            def outer_leg(label, script, send, want_rc):
                """(survival marker path, survivors, leg token) for one outer stop, or None when
                the inner child never started (recorded as that leg's own failure)."""
                leg_token = "{}-{}".format(token, label)
                started = os.path.join(outer_dir, label + "-started")
                survival = os.path.join(outer_dir, label + "-survived")
                outer_proc = subprocess.Popen([sys.executable, "-I", "-B", script, libdir, started,
                                               survival, leg_token],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                began = time.monotonic()
                while (not os.path.exists(started) and time.monotonic() - began < 30.0
                       and outer_proc.poll() is None):
                    time.sleep(0.05)
                if not os.path.exists(started):
                    rc = outer_proc.poll()
                    outer_proc.kill()
                    try:
                        outer_proc.wait(timeout=REAP_S)
                    except subprocess.TimeoutExpired:
                        pass
                    for pid in alive(leg_token):
                        _kill_pid(pid)
                    failures.append("_load_bound vector (outer stop, {}): the inner child never "
                                    "started (launcher status {!r}), so nothing was judged"
                                    .format(label, rc))
                    return None
                outer_proc.send_signal(send)
                try:
                    rc = outer_proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    outer_proc.kill()
                    try:
                        outer_proc.wait(timeout=REAP_S)
                    except subprocess.TimeoutExpired:
                        pass
                    rc = "launcher unreaped"
                wait_until = time.monotonic() + 8.0
                survivors = alive(leg_token)
                while (time.monotonic() < wait_until and not os.path.exists(survival)
                       and survivors):
                    time.sleep(0.25)
                    survivors = alive(leg_token)
                if rc != want_rc:
                    failures.append("_load_bound vector (outer stop, {}): launcher exit status "
                                    "{!r}, want {!r}".format(label, rc, want_rc))
                return (survival, survivors, leg_token)

            for label, send in (("sigterm", signal.SIGTERM), ("sigkill", _KILL)):
                leg = outer_leg(label, outer, send, -int(send))
                if leg is None:
                    continue
                survival, survivors, leg_token = leg
                for pid in survivors:
                    _kill_pid(pid)
                if os.path.exists(survival) or survivors:
                    failures.append("_load_bound vector (an outer {} of the parent reaches the "
                                    "child through its parent-death signal): marker written {!r}, "
                                    "left alive {!r}"
                                    .format(label.upper(), os.path.exists(survival), survivors))

            # POSITIVE CONTROL: the same inner child under a parent that does NOT arm the signal
            # SURVIVES the same outer SIGKILL and writes its marker, proving the survival detection
            # (marker and survivor scan) observes a real escape, so the legs above cannot pass
            # vacuously.
            control = outer_leg("control", unprotected, _KILL, -int(_KILL))
            if control is not None:
                survival, survivors, leg_token = control
                wait_until = time.monotonic() + 20.0
                while not os.path.exists(survival) and time.monotonic() < wait_until:
                    time.sleep(0.25)
                for pid in alive(leg_token):
                    _kill_pid(pid)
                if not os.path.exists(survival):
                    failures.append("_load_bound vector (outer stop positive control): an UNARMED "
                                    "child should survive the parent kill and write its marker, "
                                    "but none appeared")
        finally:
            shutil.rmtree(outer_dir)

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
            for pid in alive(leaked):
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


def _self_test_main(argv):
    """CLI entry: `--self-test` runs the shared vectors (vector_failures) against this
    interpreter and prints each failure, exiting 1 on any and 0 on none, so the command
    itself is discriminating. The same vectors also run inside the check_gensrc_failclose.py
    self-test; this entry changes no import-time behavior."""
    if argv != ["--self-test"]:
        print("usage: _load_bound.py --self-test", file=sys.stderr)
        return 2
    failures = vector_failures(sys.executable)
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: the shared load-bound vectors pass")
    return 0


def main():
    """The __main__ entry, run after the child-status guard: the --self-test CLI (_self_test_main)."""
    return _self_test_main(sys.argv[1:])


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The --self-test vectors launch children
# and read their exit statuses, so under an ignored SIGCHLD a failing child would read as a pass; the guard
# runs at the entry line, before main() runs them. Importing this module runs no guard: an importer's own
# entry guards its process.
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
    sys.exit(_child_status_refusal_exit("_load_bound.py") or main())
