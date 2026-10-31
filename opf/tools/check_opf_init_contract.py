"""OPF-D2B PR1: contract/source consistency gate for the init Keep contract.

Verifies the frozen contract constants stay aligned with their source authorities and that the PR1
contract surface is registered. Fail-closed: exit 2 (CANNOT-EVALUATE) on a missing/unreadable authority
or a changed source shape that means the contract can no longer be checked; exit 1 (DRIFT) on a concrete
mismatch; exit 0 clean. Run: python3 -I -B opf/tools/check_opf_init_contract.py [--self-test].

Detection is SEMANTIC, never a bare substring: a constant is matched as a whole module-level line
(ignoring trailing comments); the pinned view tuple and the reserved-namespace set are parsed with
comments stripped and compared by membership (so a commented-out literal does not count); the machine
store path is checked against the store authority's DEFAULT_MACHINE_SUBDIR; and a roster registration
must appear on an ACTIVE (non-comment) line, with the live gate present in the repo-root runner and CI.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_init_contract.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACTOR_LINE = 'ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")'

# The frozen limit and identity lines the validator must carry verbatim (drift tripwire over the values).
FROZEN_LINES = (
    "KEEP_SCHEMA = 1",
    'KEEP_OPERATION = "opf-init-keep"',
    "MAX_RAW_BYTES = 1048576",
    "MAX_CANONICAL_BYTES = 1048576",
    "MAX_JSON_NESTING = 16",
    "MAX_DECISIONS = 4096",
    "MAX_INVENTORY_ENTRIES = 4096",
    "MAX_PATH_DEPTH = 32",
    "MAX_AGGREGATE_PATH_BYTES = 1048576",
    "MAX_PATH_BYTES = 4096",
    "MAX_COMPONENT_BYTES = 255",
    "MAX_ACTOR_ID_BYTES = 256",
    "MAX_REASON_BYTES = 4096",
    "MAX_STRING_BYTES = 4096",
    "MAX_FILE_SIZE = (1 << 63) - 1",
    "MAX_MODE = 0o777",
    "MAX_IDENTITY_INT = (1 << 64) - 1",
)

# The reserved-namespace literals the validator's _RESERVED tuple must carry as MEMBERS (the pointer,
# import staging, archive; the machine store is checked against DEFAULT_MACHINE_SUBDIR; the 13 views via
# the view-tuple compare).
RESERVED_MEMBERS = (".opf.toml", ".working/imports", ".working/archive")

# Every roster surface that must register BOTH the validator self-test and the consistency gate.
ROSTER_FILES = (
    "tools/run_all_checks.sh",
    "opf/tools/run_all_checks.sh",
    "tools/check_opf_standalone_closure.py",
    ".github/workflows/quality.yml",
)
# Surfaces where the LIVE consistency gate (invoked without --self-test) must run.
LIVE_GATE_FILES = ("tools/run_all_checks.sh", ".github/workflows/quality.yml")


def _cant(msg):
    sys.stderr.write("CANNOT-EVALUATE: {}\n".format(msg))
    sys.exit(2)


def _drift(msg):
    sys.stderr.write("DRIFT: {}\n".format(msg))
    sys.exit(1)


# Threat model: the validator this gate verifies (_opf_init_contract.py) is reviewed in-repo code; the
# guard catches an ACCIDENTAL process ending from it (a stray sys.exit, SystemExit, KeyboardInterrupt,
# GeneratorExit, an os._exit with ANY status, any other BaseException, or a background fault) at load
# and in the membership test, because the validator is loaded in a CHILD process under the fail-closed
# child contract (_validator_missing), never in this gate's process. An operator's Ctrl-C reaches THIS
# process (the signal goes to its process group) and propagates to stop the gate runner; the child runs in
# its own session, so it never receives that signal and is left to end by itself (a stated residual).


def _ending_kind(exc):
    """A fixed name for the family of exc's class, found by issubclass on type(exc) alone, so a diagnostic
    never calls back into a loaded object (no repr, str or format of exc, of exc.code, or of any loaded
    value)."""
    cls = type(exc)
    for base, name in ((SystemExit, "SystemExit"), (KeyboardInterrupt, "KeyboardInterrupt"),
                       (GeneratorExit, "GeneratorExit"), (Exception, "Exception")):
        if issubclass(cls, base):
            return name
    return "BaseException"


# The child that loads the validator. Capability removal comes first, before any loaded code runs: the
# subinterpreter modules (_interpreters, concurrent.interpreters and the test modules that create one) are
# made unimportable (a thread
# started inside a subinterpreter is invisible to this interpreter's census and live count, so the
# capability is removed rather than tracked; an accidental import is an ImportError the load refuses),
# and faulthandler.dump_traceback_later is wrapped so arming its C-level watchdog thread is recorded and
# refused like a running worker, while any armed watchdog is also cancelled before the verdict. The child
# records the loaded code's background faults (a destructor or other unraisable fault; a worker ending by
# an exception or by a SystemExit whose code is not None or 0, the low-level _thread entry points
# included, whose wrap records the ending the interpreter would drop silently) from BEFORE the load,
# writes a stage marker showing the load began (so the parent and the refusal oracle can tell a refusal
# of the loaded code from a bootstrap failure that never evaluated it), computes the result (the load and
# the membership test), then runs the loaded code's cleanup itself. Termination is counted from the
# runtime itself, never from a callback's exit alone: the settle (_CHILD_SETTLE_SECONDS in all) waits
# until threading's census shows no other thread AND no started low-level worker remains (its start flag,
# set synchronously at the start call, covers the brief bootstrap window the runtime count misses) AND the
# runtime's live count of workers (_thread._count) is zero (which covers a low-level worker whose callback
# already returned while its return value's or arguments' destructors still run) AND the OS's own list of
# the child's threads shows no other (Linux; it still shows a worker while the runtime clears its thread
# state, where its thread-local and context values' destructors run after the live count dropped it; an
# unreadable list, or none off Linux, is live, so the child then refuses), then the child runs the
# atexit callbacks the loaded code registered and collects garbage. An atexit callback or a collected destructor can itself start a worker, which the
# settle (taken before those cleanup phases) never saw: every thread-start entry is wrapped from BEFORE
# the load (threading.Thread.start, the low-level _thread entry points, and every alias of those entry
# points the threading module itself holds, bound when it was imported, so a start method or alias the
# loaded code saved at load time still lands in a wrapper), a start during the cleanup phases is
# recorded, and the thread list and live count are read afresh after them, so a worker started by cleanup
# is a fault whether it finishes or not, and one still live at the late look is refused like a late
# thread. Only when no fault was recorded, no worker was started by cleanup, no watchdog was armed and no
# other thread or live worker remains does it write the result, LAST, and end by os._exit(0) bound before
# the load, so no loaded code runs after the result is written (an os._exit at load, in the membership
# test, in a worker or in an atexit callback ends the child before the result). The parent launches the
# child in its own session, requires that no process remains in that session before reading the verdict,
# and accepts only exit 0, both streams empty and a well-formed result.
_CHILD_SETTLE_SECONDS = 60.0
_CHILD_TIMEOUT_SECONDS = 600.0
_CHILD_PYTHON = sys.executable
_LOADED_CHILD = """import atexit, faulthandler, gc, json, os, runpy, sys, threading, time, _thread
validator, out, settle = sys.argv[1], sys.argv[2], float(sys.argv[3])
views = sys.argv[4:]
end = os._exit
for blocked in ("_interpreters", "concurrent.interpreters", "_testinternalcapi", "_testcapi"):
    sys.modules[blocked] = None
faults = []
previous_unraisable, previous_thread = sys.unraisablehook, threading.excepthook
def record_unraisable(args):
    faults.append("unraisable")
    previous_unraisable(args)
def record_thread(args):
    if args.exc_type is not None and issubclass(args.exc_type, SystemExit):
        code = getattr(args.exc_value, "code", None) if args.exc_value is not None else None
        if code is None or (type(code) is int and code == 0):
            return
    faults.append("worker-thread")
    sys.stderr.write("LOADED-CODE-FAULT: a worker-thread fault in the validator child\\n")
    previous_thread(args)
sys.unraisablehook = record_unraisable
threading.excepthook = record_thread
in_cleanup = False
started = []
workers = []
def live_lowlevel():
    # A low-level worker is live when its start flag is still set (set synchronously at the start call, so
    # a worker whose OS thread has not finished bootstrapping, which the runtime live count misses for a
    # brief window, is already pending and the settle never breaks on that transient zero) OR when the
    # runtime live count is non-zero (so a worker whose callback already returned while its return value's
    # or arguments' destructors still run, which the start flag misses, is still pending). Termination is
    # counted from the runtime itself, never from callback exit alone.
    return any(state[0] for state in workers) or _thread._count() != 0
def threads_live():
    # The OS's own list of this process's threads (Linux): it still shows a worker while the runtime clears
    # its thread state, where its thread-local and context values' destructors run after the runtime live
    # count already dropped it. An unreadable list, or none off Linux, is live (fail-closed).
    try:
        return len(os.listdir("/proc/self/task")) != 1
    except OSError:
        return True
def record_starts(original):
    def record(*args, **kwargs):
        if in_cleanup:
            started.append("cleanup-started-worker")
        return original(*args, **kwargs)
    return record
def record_lowlevel(original):
    def record(function, *args, **kwargs):
        if in_cleanup:
            started.append("cleanup-started-worker")
        state = [True]
        def run(*wargs, **wkwargs):
            try:
                return function(*wargs, **wkwargs)
            except BaseException as exc:
                code = getattr(exc, "code", None) if isinstance(exc, SystemExit) else None
                if not (isinstance(exc, SystemExit) and (code is None or (type(code) is int and code == 0))):
                    faults.append("lowlevel-worker")
                raise
            finally:
                state[0] = False
        result = original(run, *args, **kwargs)
        workers.append(state)
        return result
    return record
threading.Thread.start = record_starts(threading.Thread.start)
wrapped = []
for entry in ("start_new_thread", "start_new", "start_joinable_thread"):
    if hasattr(_thread, entry):
        original = getattr(_thread, entry)
        replacement = record_lowlevel(original)
        setattr(_thread, entry, replacement)
        wrapped.append((original, replacement))
for alias in dir(threading):
    value = getattr(threading, alias)
    for original, replacement in wrapped:
        if value is original:
            setattr(threading, alias, replacement)
cancel_watchdog = faulthandler.cancel_dump_traceback_later
arm_watchdog = faulthandler.dump_traceback_later
def record_watchdog(*args, **kwargs):
    started.append("faulthandler-watchdog")
    return arm_watchdog(*args, **kwargs)
faulthandler.dump_traceback_later = record_watchdog
with open(out + ".stage", "w", encoding="utf-8") as handle:
    handle.write("loaded\\n")
ns = runpy.run_path(validator)
live = ns.get("_RESERVED")
if type(live) is not tuple:
    payload = {"not_tuple": True}
else:
    payload = {"missing": [view for view in views if (".working/" + view) not in live]}
del ns, live
cancel_watchdog()
current = threading.current_thread()
deadline = time.monotonic() + settle
while True:
    running = [worker for worker in threading.enumerate() if worker is not current and worker.is_alive()]
    pending = live_lowlevel() or threads_live()
    if (not running and not pending) or time.monotonic() >= deadline:
        break
    if running:
        running[0].join(min(max(deadline - time.monotonic(), 0.0), 0.5))
    else:
        time.sleep(0.05)
in_cleanup = True
atexit._run_exitfuncs()
gc.collect()
cancel_watchdog()
late = [worker for worker in threading.enumerate() if worker is not current and worker.is_alive()]
# A thread that was just joined can stay on the OS list for a moment after its join returns, so the late
# look waits (bounded) for that list to settle before it is read.
late_deadline = time.monotonic() + settle
while threads_live() and time.monotonic() < late_deadline:
    time.sleep(0.01)
late_pending = live_lowlevel() or threads_live()
if faults or running or pending or started or late or late_pending:
    sys.stderr.write("LOADED-CODE-FAULT: the validator child recorded a fault, a running thread or "
                     "worker, a pending watchdog, or a worker started by cleanup\\n")
    sys.stderr.flush()
    end(3)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
sys.stdout.flush()
sys.stderr.flush()
end(0)
"""


def _child_contract_kept(returncode, stdout, stderr):
    """The fail-closed child contract's process legs: exit 0 and both streams empty."""
    return returncode == 0 and not stdout and not stderr


# The parent's diagnostics, each a single exact line so the refusal oracle can match a structured reason
# instead of a substring: the first four are written only after the child began the load and completed
# (the credited refusals); the rest can never earn refusal credit in the self-test.
_REFUSED_OUTSIDE_CONTRACT = ("loading the validator to verify _RESERVED membership: the child ended "
                             "outside the fail-closed contract (the loaded code ended it, or a fault was "
                             "reported); fail-closed")
_REFUSED_NO_RESULT = ("loading the validator to verify _RESERVED membership: the child wrote no result "
                      "(the loaded code ended the child before the verdict); fail-closed")
_REFUSED_SURVIVOR = ("loading the validator to verify _RESERVED membership: a process started by the "
                     "loaded code was still alive in the child's session after it ended; it was killed; "
                     "fail-closed")
_REFUSED_NOT_TUPLE = "validator _RESERVED is not a tuple at load time"
_REFUSED_BEFORE_LOAD = ("loading the validator to verify _RESERVED membership: the child ended before "
                        "the load began (its bootstrap failed, so the fixture was never evaluated); "
                        "fail-closed")
_CANT_NOT_COMPLETE = "loading the validator to verify _RESERVED membership: the child did not complete"
_CANT_MALFORMED = "loading the validator to verify _RESERVED membership: the child result is malformed"
_CANT_CENSUS = ("loading the validator to verify _RESERVED membership: the child's session could not be "
                "read after it ended (the process listing, a process's status or the group probe was "
                "refused), so no survivor can be ruled out; fail-closed")


def _stage_began(out):
    """True only when the child's stage marker (written just before the load begins) is present and
    exact, so a refusal of the loaded code can be told from a bootstrap failure that never evaluated
    the fixture."""
    try:
        return Path(str(out) + ".stage").read_text(encoding="utf-8") == "loaded\n"
    except (OSError, UnicodeDecodeError, ValueError):
        return False


def _survivors_of_session(session):
    """Kill every process left in the child's own session once the child has ended, returning the number
    found (0 when none). The census keys on the session id the child was launched with: field 6 of
    /proc/<pid>/stat, read past the LAST ')' so a command name cannot shift it, plus a process-group
    existence probe (signal 0; the leader's group id equals the session id); each survivor gets SIGKILL
    and the census is re-polled until empty or a 10-second bound (it signals and polls; the survivors
    are not its children, so it never waits on them). Only an absence reads as absence: a /proc entry
    whose stat is gone (ENOENT, or ESRCH from a process that ended during the read) and a group probe
    answering ESRCH. Any other answer (the /proc listing missing or refused, a process's stat refused or
    too short to carry the session field, the group probe refused) is _CANT_CENSUS (exit 2) after the
    kill pass, never an empty session, so a host whose /proc hides other users' process details
    (hidepid=1) or a confinement that refuses reading another process's stat is refused, never passed.
    Stated residuals, not closed: a descendant that called setsid left the session and is not found, and
    neither is one running as another user under a /proc that hides it (hidepid=2) once it has also left
    the child's process group; and a pid recycled into this session id between the child's end and the
    census is indistinguishable from a survivor."""
    import os
    import signal
    import time
    found = 0
    token = str(session).encode("ascii")
    deadline = time.monotonic() + 10.0
    while True:
        pids = []
        unreadable = False
        try:
            entries = os.listdir("/proc")
        except OSError:
            entries = []
            unreadable = True
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open("/proc/" + entry + "/stat", "rb") as handle:
                    fields = handle.read().rsplit(b")", 1)[-1].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                unreadable = True
                continue
            if len(fields) <= 3:
                unreadable = True
            elif fields[3] == token:
                pids.append(int(entry))
        group_alive = False
        try:
            os.killpg(session, 0)
            group_alive = True
        except ProcessLookupError:
            pass
        except OSError:
            unreadable = True
        if not pids and not group_alive and not unreadable:
            return found
        found = max(found, len(pids), 1)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if group_alive:
            try:
                os.killpg(session, signal.SIGKILL)
            except OSError:
                pass
        if unreadable:
            _cant(_CANT_CENSUS)
        if time.monotonic() >= deadline:
            return found
        time.sleep(0.05)


def _validator_missing(path, views):
    """Load the validator at path in a CHILD process under the fail-closed child contract and return the
    views missing from its ACTUAL _RESERVED tuple. The contract: the child removes the subinterpreter and
    watchdog capabilities, writes the stage marker, computes the result, runs the loaded code's cleanup
    (waits until the runtime itself shows no other thread and a zero low-level live count and the OS's own
    thread list shows no other thread, runs its atexit callbacks, collects garbage, cancels any armed
    watchdog), writes the result LAST and ends by
    os._exit(0) with both streams empty; this gate launches the child in its own session and accepts only
    that exact outcome with no process left in that session. ANY other ending of the loaded code, an
    os._exit with ANY status (in a worker or an atexit callback included), a sys.exit or SystemExit (0 or
    None included), a KeyboardInterrupt, a signal handler it installs, any other BaseException, a
    background fault (a worker's exception or unsuccessful SystemExit, through the low-level _thread entry
    points included; a destructor or atexit fault, one running after a low-level callback already returned
    included, because termination is counted from the runtime's live count, never from callback exit), a
    worker its atexit callbacks or garbage collection start (every start entry is wrapped from before the
    load: threading.Thread.start, the low-level _thread entry points and threading's own module-level
    aliases of them alike, so a start method or alias saved at load time is still seen; a start during
    those cleanup phases is a fault whether or not the worker finishes, and the thread list and live count
    are read again after them), a thread or low-level worker still live at the bound, an armed
    faulthandler watchdog, a process still alive in the child's session after it ended (killed, then
    refused), a session the census cannot read, a missing result, a non-zero exit, or a non-empty stream,
    is CANNOT-EVALUATE (exit 2), never
    this gate's pass: the loaded code never runs in this gate's process, so the os._exit channel of the
    in-process load is unreachable here, not disclosed. The diagnostic separates a refusal of the loaded
    code (stage marker present) from a bootstrap failure that never evaluated it and from a malformed
    result, so the self-test's refusal oracle credits only completed refusals of code the child began to
    load. An operator's Ctrl-C is a KeyboardInterrupt in THIS process (the signal reaches this process's
    group) and propagates to stop the runner; the child, in its own session, never receives it and is left
    to end by itself, the gate still ending non-zero. Residuals, stated exactly: deliberately hostile
    loaded code forging the child contract (writing the stage and result files itself and ending cleanly)
    is the
    reporting-machinery channel disclosed once in the _opf_views class disclosure (accepted there, not
    closed), with the hostile-object residual stated there; and the session-census residuals are stated
    on _survivors_of_session. A thread outside the Python runtime (one a C extension starts) is invisible
    to threading's census and the live count, so on Linux the OS's own thread list the child also waits on
    carries it, and where that list cannot be read (outside Linux) the child refuses every load."""
    import json
    import os
    import signal
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-loaded-") as tmp:
        child_py = Path(tmp) / "loaded_child.py"
        child_py.write_text(_LOADED_CHILD, encoding="utf-8")
        out = Path(tmp) / "result.json"
        try:
            child = subprocess.Popen(
                [_CHILD_PYTHON, "-I", "-B", str(child_py), str(path), str(out),
                 str(_CHILD_SETTLE_SECONDS)] + [str(v) for v in views],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except (OSError, subprocess.SubprocessError):
            _cant(_CANT_NOT_COMPLETE)
        with child:
            try:
                stdout, stderr = child.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
                child.kill()
                try:
                    child.communicate(timeout=30)
                except (OSError, subprocess.SubprocessError):
                    pass
                _survivors_of_session(child.pid)
                _cant(_CANT_NOT_COMPLETE)
        began = _stage_began(out)
        if _survivors_of_session(child.pid):
            _cant(_REFUSED_SURVIVOR if began else _REFUSED_BEFORE_LOAD)
        if not _child_contract_kept(child.returncode, stdout, stderr):
            _cant(_REFUSED_OUTSIDE_CONTRACT if began else _REFUSED_BEFORE_LOAD)
        try:
            raw = out.read_bytes()
        except FileNotFoundError:
            _cant(_REFUSED_NO_RESULT if began else _REFUSED_BEFORE_LOAD)
        except OSError:
            # A result that exists but cannot be read (a directory, say) is malformed, never the credited
            # no-result refusal.
            _cant(_CANT_MALFORMED)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _cant(_CANT_MALFORMED)
        if type(payload) is not dict or ("missing" in payload) == ("not_tuple" in payload):
            _cant(_CANT_MALFORMED)
        if payload.get("not_tuple"):
            _cant(_REFUSED_NOT_TUPLE)
        missing = payload["missing"]
        if type(missing) is not list or any(type(view) is not str for view in missing):
            _cant(_CANT_MALFORMED)
        return missing


def _read(rel):
    p = ROOT / rel
    if not p.is_file():
        _cant("missing input: {}".format(rel))
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:
        _cant("unreadable {}: {}".format(rel, exc))


def _has_line(text, exact):
    """True only if `exact` appears as a whole module-level line, ignoring any trailing #-comment and
    surrounding whitespace, never as a substring (a longer value cannot satisfy a shorter one)."""
    return any(line.split("#", 1)[0].rstrip() == exact for line in text.splitlines())


def _quoted_in_tuple(text, name):
    """The set of double-quoted string literals in the first `NAME = ( ... )` group, comments stripped so
    a commented-out literal does not count. None if the assignment is absent/unparseable."""
    m = re.search(re.escape(name) + r"\s*=\s*\((.*?)\)", text, re.S)
    if not m:
        return None
    body = "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())
    return set(re.findall(r'"([^"]+)"', body))


def _ordered_quoted_in_tuple(text, name):
    """As _quoted_in_tuple but preserving order (for the view-tuple order compare)."""
    m = re.search(re.escape(name) + r"\s*=\s*\((.*?)\)", text, re.S)
    if not m:
        return None
    body = "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())
    return tuple(re.findall(r'"([^"]+)"', body))


def _value_of(text, name):
    """The double-quoted value of a `NAME = "value"` module assignment (comments ignored), or None."""
    m = re.search(r"(?m)^" + re.escape(name) + r'\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _active_line_has(text, needle):
    """True if `needle` appears on a line whose stripped form does not start with '#' (i.e. not commented)."""
    return any(needle in line and not line.strip().startswith("#") for line in text.splitlines())


def _active_line_matches(text, pattern):
    """True if `pattern` (a regex) matches on a non-comment line."""
    rx = re.compile(pattern)
    return any(rx.search(line) and not line.strip().startswith("#") for line in text.splitlines())


def _checks():
    contract = _read("opf/tools/_opf_init_contract.py")
    schema = _read("opf/tools/_opf_schema.py")
    store = _read("opf/tools/_opf_store.py")
    check = _read("opf/tools/_opf_check.py")
    init = _read("opf/tools/_opf_init.py")
    opf = _read("opf/tools/opf.py")
    spec = _read("opf/spec/OPF-INIT-D2B.md")
    review = _read("opf/spec/OPF-INIT-D2B-REVIEW.md")

    # C-ACTOR: the schema authority holds its shape, and the validator mirrors it exactly.
    if not _has_line(schema, ACTOR_LINE):
        _cant("_opf_schema.py ACTOR_KINDS shape changed; re-verify the contract")
    if not _has_line(contract, ACTOR_LINE):
        _drift("_opf_init_contract.py ACTOR_KINDS does not mirror _opf_schema.py")

    # C-FROZEN: each frozen constant is present as a whole line (exact value, not a substring).
    for line in FROZEN_LINES:
        if not _has_line(contract, line):
            _drift("frozen constant line absent or changed in the validator: {!r}".format(line))
    if "opf-init-keep" not in spec:
        _drift("OPF-INIT-D2B.md does not state the opf-init-keep operation")

    # C-RESERVED: the reserved literals are MEMBERS of the parsed _RESERVED tuple (a commented-out literal
    # does not count), and the machine store path matches the store authority's DEFAULT_MACHINE_SUBDIR.
    reserved = _quoted_in_tuple(contract, "_RESERVED")
    if reserved is None:
        _drift("_opf_init_contract.py _RESERVED tuple missing or unparseable")
    for lit in RESERVED_MEMBERS:
        if lit not in reserved:
            _drift("reserved namespace not a member of _RESERVED: {}".format(lit))
    machine = _value_of(store, "DEFAULT_MACHINE_SUBDIR")
    if machine is None:
        _cant("_opf_store.py DEFAULT_MACHINE_SUBDIR missing")
    if (".working/" + machine) not in reserved:
        _drift("validator machine-store reserved path does not match "
               "_opf_store DEFAULT_MACHINE_SUBDIR={!r}".format(machine))

    # C-KEEP: the frozen operation id is named in the spec (the values are covered by C-FROZEN).
    # C-VIEWS: the validator's pinned view tuple equals _opf_init.py's authority, in order (comments off).
    src_views = _ordered_quoted_in_tuple(init, "_INITIAL_VIEW_NAMES")
    if src_views is None:
        _cant("_opf_init.py _INITIAL_VIEW_NAMES missing or unparseable")
    con_views = _ordered_quoted_in_tuple(contract, "_INITIAL_VIEWS")
    if con_views is None:
        _drift("_opf_init_contract.py _INITIAL_VIEWS missing or unparseable")
    if con_views != src_views:
        _drift("validator view tuple diverges from _opf_init.py: {} vs {}".format(con_views, src_views))

    # Value-based membership: load the pure validator IN A CHILD process under the fail-closed child
    # contract and confirm the ACTUAL _RESERVED tuple contains every pinned view path (the source-text
    # checks above cannot see the view comprehension). The child never sets __name__ to __main__, so the
    # validator self-test does not run on load; ANY other ending of the loaded code, an os._exit with any
    # status included, is fail-closed (exit 2), never this gate's status (_validator_missing).
    for view in _validator_missing(str(ROOT / "opf/tools/_opf_init_contract.py"), src_views):
        _drift("pinned view not a member of the validator _RESERVED set: .working/{}".format(view))

    # C-D2A: the shipped D2a success wording is present (D2b must not silently retrofit an envelope).
    if "tracking and rendering are pending." not in opf:
        _cant("D2a success wording changed at source; re-verify the D2a/D2b boundary")

    # C-SPEC-VERSION: the base spec_version the schema-compat obligation targets. Re-verified at 2.0.0,
    # the only base this tooling accepts (homes 2 is its baseline): a store declaring any other
    # spec_version is refused, never upgraded, so a change to this line re-opens the boundary.
    if not _has_line(store, 'SPEC_VERSION = "2.0.0"'):
        _cant("_opf_store.py SPEC_VERSION changed; re-verify the schema-compat boundary")

    # C-CHECKER-ROSTER: the required-checks authority the coupled success contract depends on.
    if "REQUIRED_CHECKS" not in check:
        _cant("_opf_check.py REQUIRED_CHECKS missing")

    # C-REVIEW: the durable review register enumerates F01..F30.
    for n in range(1, 31):
        if "F{:02d}".format(n) not in review:
            _drift("review register missing F{:02d}".format(n))

    # C-RUNNERS: BOTH scripts registered on an ACTIVE (non-comment) line in every roster surface; and the
    # LIVE consistency gate (no --self-test) present in the repo-root runner and the CI workflow.
    for rel in ROSTER_FILES:
        text = _read(rel)
        # The validator name is a substring of the gate name, so match it on a word boundary; the gate
        # name is unique and matched as a plain active-line substring.
        if not _active_line_matches(text, r"\b_opf_init_contract\.py"):
            _drift("_opf_init_contract.py not actively registered in {}".format(rel))
        if not _active_line_has(text, "check_opf_init_contract.py"):
            _drift("check_opf_init_contract.py not actively registered in {}".format(rel))
    for rel in LIVE_GATE_FILES:
        text = _read(rel)
        live = any("check_opf_init_contract.py" in line and "--self-test" not in line
                   and not line.strip().startswith("#") for line in text.splitlines())
        if not live:
            _drift("live consistency gate (no --self-test) not registered in {}".format(rel))

    sys.stdout.write("PASS check_opf_init_contract: contract/source consistency\n")
    sys.exit(0)


def _expect(condition, message=None):
    """A self-test verdict that python -O and -OO cannot strip, unlike an assert statement."""
    if not condition:
        if message is None:
            raise AssertionError
        raise AssertionError(message)


def _child_outcome(child_source, body):
    """"ACCEPTED" when the child at child_source accepts the validator source body (exit 0, both streams
    empty, no process left in the child's session, a result written that carries the production payload,
    an empty missing list for these bodies), "MALFORMED" when the process legs held but the result payload
    is absent or wrong (distinct, so an acceptance is never credited to a child that did not evaluate the
    body), "REFUSED" when it began the load and refused (a survivor left in its session included),
    "NO-LOAD" when it ended without the stage marker (its bootstrap failed, so the body was never
    evaluated; a control expecting a refusal fails on it), "NO-RUN" when it did not run or timed out
    (distinct, so a control expecting either outcome fails on a dead child); the leg controls drive a
    deliberately weakened copy of _LOADED_CHILD through this to show which refusal leg carries each
    vector."""
    import json
    import os
    import signal
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-leg-control-") as tmp:
        child_py = Path(tmp) / "loaded_child.py"
        child_py.write_text(child_source, encoding="utf-8")
        val = Path(tmp) / "validator.py"
        val.write_text(body, encoding="utf-8")
        out = Path(tmp) / "result.json"
        try:
            child = subprocess.Popen(
                [_CHILD_PYTHON, "-I", "-B", str(child_py), str(val), str(out),
                 str(_CHILD_SETTLE_SECONDS), "a.md"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except (OSError, subprocess.SubprocessError):
            return "NO-RUN"
        with child:
            try:
                stdout, stderr = child.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
                child.kill()
                try:
                    child.communicate(timeout=30)
                except (OSError, subprocess.SubprocessError):
                    pass
                _survivors_of_session(child.pid)
                return "NO-RUN"
        began = _stage_began(out)
        survivors = _survivors_of_session(child.pid)
        if not began:
            return "NO-LOAD"
        if survivors:
            return "REFUSED"
        if child.returncode == 0 and not stdout and not stderr:
            try:
                payload = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return "MALFORMED"
            return "ACCEPTED" if payload == {"missing": []} else "MALFORMED"
        return "REFUSED"


_CREDITED_DIAGNOSTICS = tuple("CANNOT-EVALUATE: {}\n".format(reason) for reason in (
    _REFUSED_OUTSIDE_CONTRACT, _REFUSED_NO_RESULT, _REFUSED_SURVIVOR, _REFUSED_NOT_TUPLE))


def _loaded_exit_failure(label, path, expect_missing=None):
    """None when _validator_missing over the validator at path ends as the case requires, else the failure.
    A refused case (expect_missing is None) must end exit 2 with a captured diagnostic that is EXACTLY one
    of the parent's credited refusal lines (the child ending outside the contract after the stage marker
    showed the load began, ending inside it without a result, leaving a survivor in its session, or
    reporting a loaded _RESERVED that is not a tuple), never this process's own end and never a pass; a
    clean case must return exactly expect_missing. An absent fixture, a child that could not launch or
    timed out, a bootstrap failure (no stage marker, so the fixture was never evaluated) and a malformed
    child result (unreadable, invalid JSON or a wrong shape) are each this test's failure, never a
    refusal, so a case whose fixture was never evaluated cannot read as refused. The CANNOT-EVALUATE line
    is captured and required, never printed, so a passing self-test shows none. An operator's Ctrl-C
    propagates (nothing here catches KeyboardInterrupt)."""
    import contextlib
    import io
    if not Path(path).is_file():
        return "{}: the fixture to load does not exist".format(label)
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            got = _validator_missing(str(path), ("a.md",))
    except SystemExit as exc:
        if expect_missing is not None:
            return "{}: expected a clean child run, got SystemExit".format(label)
        if not (type(exc) is SystemExit and type(exc.code) is int and exc.code == 2):
            return "{}: expected exit 2, got another SystemExit".format(label)
        if captured.getvalue() not in _CREDITED_DIAGNOSTICS:
            return ("{}: exit 2 for a reason other than the child refusing the loaded code after the "
                    "load began".format(label))
        return None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001  any other escape is recorded, never the test's own end
        return "{}: expected exit 2, got {}".format(label, _ending_kind(exc))
    if expect_missing is None:
        return "{}: the loaded code's ending was not refused".format(label)
    if got != expect_missing:
        return "{}: wrong missing-views result from the clean child".format(label)
    return None


def _self_test_loaded_exit():
    """A loaded validator that ends the child, at load or in the membership test, an os._exit(0) included,
    yields this gate's CANNOT-EVALUATE exit 2 through _validator_missing (the function _checks calls),
    never its own status and never a pass: the fail-closed child contract (exit 0, a result written after
    the loaded code's cleanup, both streams empty, no process left in the child's session) leaves every
    such ending refusable. Red if a contract leg is relaxed: a missing result read as clean fails the
    os._exit cases; an accepted non-empty stdout fails "load stdout noise" and an accepted non-empty
    stderr fails "stderr noise after a valid result" (each the only leg its case breaks); the child's
    recorders, its own atexit run and its running-thread refusal each have a case with a valid _RESERVED
    that only that leg refuses (worker SystemExit(7), an atexit os._exit(0) or fault, a worker still
    running at the bound), and its settle has clean cases (workers finishing after the load) that are
    refused without it. Termination is counted from the runtime itself, never from a callback's exit: the
    live count covers a low-level worker to its real end, destructors of its return value and arguments
    included, so the destructor-fault cases are refused (red when the live count is disabled: their leg
    control accepts them), a load-started low-level worker still live at the verdict is refused the same
    way, and a low-level start that failed and was handled by the loaded code leaves nothing pending (its
    clean case is red when a start-time record, not the runtime, is the authority). A worker started by
    the child's OWN cleanup run (an atexit callback or a destructor the child's garbage collection runs)
    is refused whether it finished before cleanup returned, faults later or never ends, started through a
    start method saved at load time and through the low-level _thread entry points alike: the
    joined-worker case is red without the start record (its leg control shows a record-disabled child
    accepts it); the never-ending case is refused by the late census ALONE (shown by a control child with
    the start record and live count both disabled that still refuses it, paired with one whose late census
    is also disabled and must accept, so the census demonstrably carries verdict weight). A cleanup worker
    started through threading's own saved module alias of a low-level start entry is refused (red when
    only the _thread attributes are rebound); a cleanup worker through each further low-level entry has a
    case of its own (red when that entry's wrap is dropped: the narrowed-entry control child accepts
    both); and a low-level worker's unsuccessful ending is refused (red without the lifetime fault record,
    shown by its leg control). Capability removal has legs of its own: the subinterpreter modules are
    unimportable at load (red without the module block: its control child re-enables the import and must
    accept) and arming a faulthandler watchdog is recorded and refused while the cancel still disarms it
    (red without the arming record: its control child drops only the record and must accept). A process
    the loaded code started and left alive is killed and refused by the session census (red without it:
    its control stubs the census and must accept). The refusal oracle itself is pinned: an absent fixture,
    a bootstrap failure before the load began, a launch failure, a timeout, an invalid JSON result and a
    wrong-shape result must each read as this test's failure, never a credited refusal, while a completed
    child that wrote no result is the credited no-result refusal (each has a control that breaks the child
    or the launch exactly that way). The exit-status leg, which no
    loaded code reaches once the child ends by its own os._exit(0) after the result, is pinned by
    _child_contract_kept's own cases. A clean child must still return the exact missing-views list, red if
    the child result is not read back. Run by _self_test in a CHILD of the self-test (_LOADED_EXIT_PROBE),
    so a load moved back into this process cannot end the self-test with the os._exit(0) cases' status."""
    import tempfile
    _expect(_child_contract_kept(0, b"", b""), "child contract: exit 0 with empty streams is kept")
    _expect(not _child_contract_kept(3, b"", b""), "child contract: a non-zero exit is refused")
    _expect(not _child_contract_kept(-9, b"", b""), "child contract: a signal ending is refused")
    _expect(not _child_contract_kept(0, b"x", b""), "child contract: stdout output is refused")
    _expect(not _child_contract_kept(0, b"", b"x"), "child contract: stderr output is refused")
    valid = '_RESERVED = (".working/a.md",)\n'
    repr_exits = "class R:\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
    local_teardown = ("import _thread, threading, time\nentered = threading.Event()\nstore = threading.local()\n"
                      "class Held:\n    def __del__(self):\n        entered.set()\n        time.sleep(0.3)\n"
                      "        raise RuntimeError('loaded')\n"
                      "def worker():\n    store.value = Held()\n"
                      "{start}\nentered.wait()\n")
    cases = (
        ("load SystemExit(0)", "raise SystemExit(0)\n"),
        ("load SystemExit(None)", "raise SystemExit\n"),
        ("load KeyboardInterrupt", "raise KeyboardInterrupt\n"),
        ("load KeyboardInterrupt subclass", "class K(KeyboardInterrupt):\n    pass\nraise K()\n"),
        ("load GeneratorExit", "raise GeneratorExit\n"),
        ("load BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n"),
        ("load os._exit(0)", "import os\nos._exit(0)\n"),
        ("load os._exit(0) from a joined thread",
         "import os\nimport threading\nworker = threading.Thread(target=lambda: os._exit(0))\n"
         "worker.start()\nworker.join()\n"),
        ("load SystemExit(code whose repr exits 0)", repr_exits + "raise SystemExit(R())\n"),
        ("load Exception whose str exits 0",
         "class E(Exception):\n    def __str__(self):\n        raise SystemExit(0)\n    __repr__ = __str__\n"
         "raise E()\n"),
        ("_RESERVED whose __class__ exits 0",
         "class C:\n    @property\n    def __class__(self):\n        raise SystemExit(0)\n_RESERVED = C()\n"),
        ("call SystemExit(0) in a member __eq__",
         "class M:\n    def __eq__(self, other):\n        raise SystemExit(0)\n    __hash__ = None\n"
         "_RESERVED = (M(),)\n"),
        ("call KeyboardInterrupt in a member __eq__",
         "class M:\n    def __eq__(self, other):\n        raise KeyboardInterrupt\n    __hash__ = None\n"
         "_RESERVED = (M(),)\n"),
        ("atexit fault after a valid result",
         valid + "import atexit\ndef callback():\n    raise RuntimeError('loaded-atexit')\n"
         "atexit.register(callback)\n"),
        ("atexit os._exit(0) after a valid result",
         valid + "import atexit, os\natexit.register(os._exit, 0)\n"),
        ("worker SystemExit(7) after a valid result",
         valid + "import threading\ndef fault():\n    raise SystemExit(7)\n"
         "worker = threading.Thread(target=fault)\nworker.start()\nworker.join()\n"),
        ("unjoined worker fault after a valid result",
         valid + "import threading, time\ndef fault():\n    time.sleep(0.5)\n"
         "    raise RuntimeError('loaded')\n"
         "threading.Thread(target=fault).start()\n"),
        ("worker still running at the bound after a valid result",
         valid + "import threading\nthreading.Thread(target=threading.Event().wait, daemon=True).start()\n"),
        ("worker started by an atexit callback, still running at the late census",
         valid + "import atexit, threading, time\ndef cleanup():\n"
         "    threading.Thread(target=time.sleep, args=(0.2,)).start()\n"
         "atexit.register(cleanup)\n"),
        ("worker started and joined by an atexit callback, finished before it returns",
         valid + "import atexit, threading\ndef cleanup():\n"
         "    worker = threading.Thread(target=lambda: None)\n"
         "    worker.start()\n    worker.join()\n"
         "atexit.register(cleanup)\n"),
        ("worker started by an atexit callback through a start method saved at load",
         valid + "import atexit, threading\n"
         "worker = threading.Thread(target=lambda: None)\n"
         "saved_start = worker.start\ndef cleanup():\n"
         "    saved_start()\n    worker.join()\n"
         "atexit.register(cleanup)\n"),
        ("low-level thread worker started by an atexit callback, faulting later",
         valid + "import atexit, _thread, time\ndef fault():\n    time.sleep(0.3)\n"
         "    raise RuntimeError('loaded')\ndef cleanup():\n"
         "    _thread.start_new_thread(fault, ())\n"
         "atexit.register(cleanup)\n"),
        ("low-level thread worker started by an atexit callback, never ending",
         valid + "import atexit, _thread, threading\ndef cleanup():\n"
         "    _thread.start_new_thread(threading.Event().wait, ())\n"
         "atexit.register(cleanup)\n"),
        ("worker started by an atexit callback through threading's saved module alias",
         valid + "import atexit, threading\n"
         "atexit.register(lambda: threading._start_joinable_thread(threading.Event().wait))\n"),
        ("low-level worker started by an atexit callback through start_new",
         valid + "import atexit, _thread, threading\n"
         "atexit.register(lambda: _thread.start_new(threading.Event().wait, ()))\n"),
        ("low-level worker started by an atexit callback through start_joinable_thread",
         valid + "import atexit, _thread, threading\n"
         "atexit.register(lambda: _thread.start_joinable_thread(threading.Event().wait))\n"),
        ("low-level thread worker started at load, still running at the verdict",
         valid + "import _thread, threading\n"
         "_thread.start_new_thread(threading.Event().wait, ())\n"),
        ("low-level thread worker started at load, ending by SystemExit(7)",
         valid + "import _thread, threading\ndone = threading.Event()\n"
         "def worker():\n    try:\n        raise SystemExit(7)\n    finally:\n        done.set()\n"
         "_thread.start_new_thread(worker, ())\ndone.wait()\n"),
        ("worker started by an atexit callback, faulting later",
         valid + "import atexit, threading, time\ndef fault():\n    time.sleep(0.3)\n"
         "    raise RuntimeError('loaded')\ndef cleanup():\n"
         "    threading.Thread(target=fault).start()\n"
         "atexit.register(cleanup)\n"),
        ("worker started by an atexit callback, never ending",
         valid + "import atexit, threading\ndef cleanup():\n"
         "    threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
         "atexit.register(cleanup)\n"),
        ("worker started by a destructor in the child's garbage collection",
         valid + "import gc, threading\ngc.disable()\n"
         "class Late:\n    def __del__(self):\n"
         "        threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
         "cycle = Late()\ncycle.self = cycle\ndel cycle\n"),
        ("destructor fault after a valid result",
         valid + "class Fault:\n    def __del__(self):\n        raise RuntimeError('loaded')\nFault()\n"),
        ("stderr noise after a valid result", valid + "import sys\nsys.stderr.write('loaded noise\\n')\n"),
        ("load stdout noise", "_RESERVED = ()\nprint('loaded noise')\n"),
        ("process started at load, left alive past the child's end",
         valid + "import subprocess\nsubprocess.Popen(['sleep', '5'], stdin=subprocess.DEVNULL, "
         "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"),
        ("low-level worker return value destroyed after its callback returned, destructor fault",
         valid + "import _thread, threading, time\nentered = threading.Event()\n"
         "class Result:\n    def __del__(self):\n        entered.set()\n        time.sleep(0.3)\n"
         "        raise RuntimeError('loaded')\n"
         "def worker():\n    return Result()\n"
         "_thread.start_new_thread(worker, ())\nentered.wait()\n"),
        ("low-level worker argument destroyed after its callback returned, destructor fault",
         valid + "import _thread, threading, time\nentered = threading.Event()\n"
         "class Arg:\n    def __del__(self):\n        entered.set()\n        time.sleep(0.3)\n"
         "        raise RuntimeError('loaded')\n"
         "def worker(arg):\n    return None\n"
         "_thread.start_new_thread(worker, (Arg(),))\nentered.wait()\n"),
        ("subinterpreter module import at load",
         valid + "import _interpreters\n_interpreters.create()\n"),
        ("concurrent.interpreters import at load",
         valid + "from concurrent import interpreters\ninterpreters.create()\n"),
        ("faulthandler watchdog armed at load",
         valid + "import faulthandler\nfaulthandler.dump_traceback_later(600)\n"),
        ("_testinternalcapi import at load", valid + "import _testinternalcapi\n"),
        ("low-level worker thread-local value destroyed at thread teardown, destructor fault",
         valid + local_teardown.format(start="_thread.start_new_thread(worker, ())")),
        ("threading worker thread-local value destroyed at thread teardown, destructor fault",
         valid + local_teardown.format(start="threading.Thread(target=worker).start()")),
        ("low-level worker context value destroyed at thread teardown, destructor fault",
         valid + "import _thread, contextvars, threading, time\nentered = threading.Event()\n"
         "held = contextvars.ContextVar('held')\n"
         "class Held:\n    def __del__(self):\n        entered.set()\n        time.sleep(0.3)\n"
         "        raise RuntimeError('loaded')\n"
         "def worker():\n    held.set(Held())\n"
         "_thread.start_new_thread(worker, ())\nentered.wait()\n"),
    )
    clean_cases = (
        ("clean with the view present", '_RESERVED = (".working/a.md",)\n', []),
        ("clean with the view missing", "_RESERVED = ()\n", ["a.md"]),
        ("clean with a worker that finishes after the load",
         '_RESERVED = (".working/a.md",)\nimport threading, time\n'
         "threading.Thread(target=time.sleep, args=(0.3,)).start()\n", []),
        ("clean with a low-level worker that finishes after the load",
         '_RESERVED = (".working/a.md",)\nimport _thread, time\n'
         "_thread.start_new_thread(time.sleep, (0.3,))\n", []),
        ("clean with a failed low-level start the loaded code handled",
         '_RESERVED = (".working/a.md",)\nimport _thread\n'
         "try:\n    _thread.start_new_thread(print)\nexcept TypeError:\n    pass\n", []),
    )
    failures = []
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-selftest-") as tmp:
        for index, (label, body) in enumerate(cases):
            path = Path(tmp) / "loaded_exit_{}.py".format(index)
            path.write_text(body, encoding="utf-8")
            failure = _loaded_exit_failure(label, path)
            if failure is not None:
                failures.append(failure)
        for index, (label, body, expected) in enumerate(clean_cases):
            path = Path(tmp) / "loaded_clean_{}.py".format(index)
            path.write_text(body, encoding="utf-8")
            failure = _loaded_exit_failure(label, path, expect_missing=expected)
            if failure is not None:
                failures.append(failure)
        # The refusal oracle's own control: a fixture that was never evaluated (the file is absent, so
        # the child's load could not have run it as intended) must be this test's failure, never a
        # credited refusal.
        if _loaded_exit_failure("an absent fixture (oracle control)", Path(tmp) / "loaded_absent.py") is None:
            failures.append("oracle control: an absent fixture was read as a refused loaded ending")
    _expect(not failures, "loaded-exit vectors: " + "; ".join(failures))
    # Leg controls: each refusal leg must carry weight on its own, so each control drives a deliberately
    # weakened copy of the child (or a stubbed parent census) that must ACCEPT the vector its leg refuses;
    # an acceptance requires the production payload, so each control also shows its child ran and
    # evaluated the body. The start record alone sees a cleanup worker that already finished.
    record_off = _LOADED_CHILD.replace('started.append("cleanup-started-worker")', "pass")
    _expect(record_off != _LOADED_CHILD, "leg control: the start-recording line is present to disable")
    joined = (valid + "import atexit, threading\ndef cleanup():\n"
              "    worker = threading.Thread(target=lambda: None)\n"
              "    worker.start()\n    worker.join()\n"
              "atexit.register(cleanup)\n")
    _expect(_child_outcome(record_off, joined) == "ACCEPTED",
            "leg control: the joined cleanup worker is refused by the start record alone")
    tasks_off = _LOADED_CHILD.replace('return len(os.listdir("/proc/self/task")) != 1', "return False")
    _expect(tasks_off != _LOADED_CHILD, "leg control: the OS thread-list term is present to disable")
    count_off = tasks_off.replace("or _thread._count() != 0", "or False")
    _expect(count_off != _LOADED_CHILD, "leg control: the runtime live-count term is present to disable")
    track_off = tasks_off.replace("workers.append(state)", "pass")
    _expect(track_off != _LOADED_CHILD, "leg control: the low-level start-flag line is present to disable")
    track_count_off = track_off.replace("or _thread._count() != 0", "or False")
    _expect(track_count_off != track_off, "leg control: both low-level liveness terms are present to disable")
    # The late census is isolated from the start record AND from both low-level liveness terms (a
    # threading.Thread start reaches a wrapped _thread entry, so the low-level flag and count would
    # otherwise catch this cleanup worker too): with the record, the flags and the count all disabled a
    # never-ending cleanup worker is refused by the census ALONE, and with the census also disabled it is
    # accepted, so the census demonstrably carries the verdict weight.
    census_only = (record_off
                   .replace('return len(os.listdir("/proc/self/task")) != 1', "return False")
                   .replace("workers.append(state)", "pass")
                   .replace("or _thread._count() != 0", "or False"))
    _expect(census_only != record_off, "leg control: the low-level liveness terms are present to disable")
    never = (valid + "import atexit, threading\ndef cleanup():\n"
             "    threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
             "atexit.register(cleanup)\n")
    _expect(_child_outcome(census_only, never) == "REFUSED",
            "leg control: the late census alone refuses a cleanup worker with the start record and both "
            "low-level liveness terms disabled")
    census_off = census_only.replace(
        "late = [worker for worker in threading.enumerate() if worker is not current and worker.is_alive()]",
        "late = []")
    _expect(census_off != census_only, "leg control: the late-census line is present to disable")
    _expect(_child_outcome(census_off, never) == "ACCEPTED",
            "leg control: with the late census also disabled that cleanup worker is accepted, so the "
            "census carries the verdict weight")
    # Low-level liveness legs. The start flag, set synchronously, reliably catches a load-started
    # never-ending worker (the runtime count alone would miss the brief bootstrap window): with the count
    # off the start flag still refuses it, and with both low-level liveness terms off it is accepted, so
    # the start flag carries that verdict weight.
    low_still = valid + "import _thread, threading\n_thread.start_new_thread(threading.Event().wait, ())\n"
    _expect(_child_outcome(count_off, low_still) == "REFUSED",
            "leg control: the load-started low-level worker is refused by the start flag with the count off")
    _expect(_child_outcome(track_count_off, low_still) == "ACCEPTED",
            "leg control: with both low-level liveness terms off the load-started worker is accepted, so "
            "the start flag carries its verdict weight")
    # The runtime count ALONE carries the destructor window the start flag cannot reach (the flag is
    # cleared when the callback returns): a low-level worker whose return value's destructor still runs
    # after its callback returned is refused in production and accepted only when the count term is off.
    low_linger = (valid + "import _thread, threading, time\n"
                  "class Result:\n    def __del__(self):\n        threading.Event().wait()\n"
                  "def worker():\n    return Result()\n"
                  "_thread.start_new_thread(worker, ())\ntime.sleep(0.3)\n")
    _expect(_child_outcome(_LOADED_CHILD, low_linger) == "REFUSED",
            "leg control: the lingering destructor is refused in production by the runtime live count")
    _expect(_child_outcome(count_off, low_linger) == "ACCEPTED",
            "leg control: the destructor still running after its callback returned is refused by the "
            "runtime live count alone (with the OS thread list also off)")
    # The OS thread list ALONE carries the thread-state teardown window: a worker's thread-local value is
    # destroyed after the runtime live count already dropped it, so with only that term off the teardown
    # destructor fault is accepted (production refuses it: the cases above).
    _expect(_child_outcome(tasks_off, valid + local_teardown.format(
        start="_thread.start_new_thread(worker, ())")) == "ACCEPTED",
            "leg control: the thread-local teardown destructor fault is refused by the OS thread list alone")
    fault_off = _LOADED_CHILD.replace('faults.append("lowlevel-worker")', "pass")
    _expect(fault_off != _LOADED_CHILD, "leg control: the low-level fault line is present to disable")
    low_exit = (valid + "import _thread, threading\ndone = threading.Event()\n"
                "def worker():\n    try:\n        raise SystemExit(7)\n    finally:\n        done.set()\n"
                "_thread.start_new_thread(worker, ())\ndone.wait()\n")
    _expect(_child_outcome(fault_off, low_exit) == "ACCEPTED",
            "leg control: the low-level unsuccessful ending is refused by the lifetime fault record alone")
    record_only = (tasks_off
                   .replace("or _thread._count() != 0", "or False")
                   .replace("workers.append(state)", "pass"))
    _expect(record_only != tasks_off, "leg control: the low-level liveness terms are present to disable")
    alias_off = record_only.replace("setattr(threading, alias, replacement)", "pass")
    _expect(alias_off != record_only, "leg control: the alias rebinding line is present to disable")
    alias_worker = (valid + "import atexit, threading\n"
                    "atexit.register(lambda: threading._start_joinable_thread(threading.Event().wait))\n")
    _expect(_child_outcome(record_only, alias_worker) == "REFUSED",
            "leg control: with every liveness term off the start record still refuses the cleanup worker "
            "through threading's saved alias")
    _expect(_child_outcome(alias_off, alias_worker) == "ACCEPTED",
            "leg control: the cleanup worker through threading's saved alias is refused by the rebinding "
            "alone")
    entry_off = record_only.replace('("start_new_thread", "start_new", "start_joinable_thread")',
                                    '("start_new_thread",)')
    _expect(entry_off != record_only, "leg control: the low-level entry tuple is present to narrow")
    via_start_new = (valid + "import atexit, _thread, threading\n"
                     "atexit.register(lambda: _thread.start_new(threading.Event().wait, ()))\n")
    _expect(_child_outcome(record_only, via_start_new) == "REFUSED",
            "leg control: with every liveness term off the start record still refuses the cleanup worker "
            "through _thread.start_new")
    _expect(_child_outcome(entry_off, via_start_new) == "ACCEPTED",
            "leg control: the cleanup worker through _thread.start_new is refused by its wrap alone")
    via_joinable = (valid + "import atexit, _thread, threading\n"
                    "atexit.register(lambda: _thread.start_joinable_thread(threading.Event().wait))\n")
    _expect(_child_outcome(record_only, via_joinable) == "REFUSED",
            "leg control: with every liveness term off the start record still refuses the cleanup worker "
            "through _thread.start_joinable_thread")
    _expect(_child_outcome(entry_off, via_joinable) == "ACCEPTED",
            "leg control: the cleanup worker through _thread.start_joinable_thread is refused by its "
            "wrap alone")
    # Capability-removal legs: re-enable the subinterpreter import, or drop only the watchdog arming
    # record (the cancel stays), and the corresponding vector must be accepted.
    interp_on = _LOADED_CHILD.replace("    sys.modules[blocked] = None", "    pass")
    _expect(interp_on != _LOADED_CHILD, "leg control: the subinterpreter block lines are present to disable")
    _expect(_child_outcome(interp_on, valid + "import _interpreters\n_interpreters.create()\n") == "ACCEPTED",
            "leg control: the subinterpreter import is refused by the module block alone")
    _expect(_child_outcome(interp_on, valid + "import _testinternalcapi\n") == "ACCEPTED",
            "leg control: the _testinternalcapi import is refused by the module block alone")
    watchdog_off = _LOADED_CHILD.replace('started.append("faulthandler-watchdog")', "pass")
    _expect(watchdog_off != _LOADED_CHILD, "leg control: the watchdog record line is present to disable")
    _expect(_child_outcome(watchdog_off,
                           valid + "import faulthandler\nfaulthandler.dump_traceback_later(600)\n")
            == "ACCEPTED",
            "leg control: the armed watchdog is refused by the arming record alone (the cancel still "
            "disarms it before the verdict)")
    # Session-census leg: with the census stubbed out, the spawned process is unseen and the child is
    # accepted, so the census alone refuses the survivor vector above (the stubbed run's sleeper ends by
    # itself; the production census kills what it finds).
    spawner = (valid + "import subprocess\nsubprocess.Popen(['sleep', '5'], stdin=subprocess.DEVNULL, "
               "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n")
    module_globals = globals()
    saved_census = module_globals["_survivors_of_session"]
    module_globals["_survivors_of_session"] = lambda session: 0
    try:
        census_stubbed = _child_outcome(_LOADED_CHILD, spawner)
    finally:
        module_globals["_survivors_of_session"] = saved_census
    _expect(census_stubbed == "ACCEPTED",
            "leg control: the surviving process is refused by the session census alone")
    # Refusal-oracle controls: a child broken before the load, a launch that fails or times out, and a
    # malformed result must each be this test's failure, never a credited refusal; a completed child
    # that wrote no result is the one credited no-result class. Each control patches one module global
    # and restores it.
    def _oracle_with(name, value, label):
        saved_value = module_globals[name]
        module_globals[name] = value
        try:
            return _loaded_exit_failure(label, oracle_fixture)
        finally:
            module_globals[name] = saved_value

    with tempfile.TemporaryDirectory(prefix="opf-init-contract-oracle-") as oracle_tmp:
        oracle_fixture = Path(oracle_tmp) / "oracle_fixture.py"
        oracle_fixture.write_text(valid, encoding="utf-8")
        _expect(_oracle_with("_LOADED_CHILD", "def broken(:\n", "oracle bootstrap syntax error") is not None,
                "oracle control: a child that failed before the load began must be this test's failure")
        _expect(_oracle_with("_LOADED_CHILD", "raise RuntimeError('bootstrap')\n" + _LOADED_CHILD,
                             "oracle bootstrap fault") is not None,
                "oracle control: a child fault before the load began must be this test's failure")
        _expect(_oracle_with("_CHILD_PYTHON", str(Path(oracle_tmp) / "absent-python"),
                             "oracle launch failure") is not None,
                "oracle control: a child that could not launch must be this test's failure")
        _expect(_oracle_with("_CHILD_TIMEOUT_SECONDS", 0.001, "oracle timeout") is not None,
                "oracle control: a child that timed out must be this test's failure")
        bad_json = _LOADED_CHILD.replace("json.dump(payload, handle)", 'handle.write("{")')
        _expect(bad_json != _LOADED_CHILD, "oracle control: the result-write line is present to break")
        _expect(_oracle_with("_LOADED_CHILD", bad_json, "oracle invalid JSON") is not None,
                "oracle control: an invalid JSON result must be this test's failure")
        bad_shape = _LOADED_CHILD.replace("json.dump(payload, handle)", 'json.dump({"shape": 1}, handle)')
        _expect(bad_shape != _LOADED_CHILD, "oracle control: the result-write line is present to reshape")
        _expect(_oracle_with("_LOADED_CHILD", bad_shape, "oracle invalid shape") is not None,
                "oracle control: a wrong-shape result must be this test's failure")
        no_result = _LOADED_CHILD.replace('with open(out, "w", encoding="utf-8") as handle:\n'
                                          "    json.dump(payload, handle)", "pass")
        _expect(no_result != _LOADED_CHILD, "oracle control: the result-write block is present to remove")
        _expect(_oracle_with("_LOADED_CHILD", no_result, "oracle missing output") is None,
                "oracle control: a completed child that wrote no result is the credited no-result refusal")
        dir_result = _LOADED_CHILD.replace('with open(out, "w", encoding="utf-8") as handle:\n'
                                           "    json.dump(payload, handle)", "os.mkdir(out)")
        _expect(dir_result != _LOADED_CHILD, "oracle control: the result-write block is present to replace")
        _expect(_oracle_with("_LOADED_CHILD", dir_result, "oracle unreadable result") is not None,
                "oracle control: a result that exists but cannot be read must be this test's failure")


def _self_test_census_errors():
    """The session census reads only an absence as absence. Each vector drives the real
    _survivors_of_session with one answer faked (the /proc listing, a process's stat or the group probe;
    os.kill only records) over one listed process that is not a session member, so it differs from the
    clean control in that one answer alone. A refused listing, a missing /proc, a refused stat, a stat too
    short to carry the session field and a refused group probe must each end exit 2 with exactly the
    census diagnostic, never a count of 0 (each is red when its answer is read as absence again); a
    process that ended before or during its stat read and an absent group must still read as absence; a
    member found beside an unreadable entry is killed before the census refuses; and a member found
    alone is killed and counted."""
    import contextlib
    import io
    import os
    module_globals = globals()
    census_line = "CANNOT-EVALUATE: {}\n".format(_CANT_CENSUS)
    killed = []

    def _injected(error):
        def _raise(*args, **kwargs):
            raise error("injected census fault")
        return _raise

    def _stat_of(session_field):
        # The command name holds a ')' so the session field is found past the LAST one.
        line = "4242 (a) b) S 1 4242 {} 0\n".format(session_field).encode("ascii")
        return lambda path, mode="r": io.BytesIO(line)

    def _census_with(listdir, stat, killpg):
        saved = (os.listdir, os.killpg, os.kill)
        captured = io.StringIO()
        os.listdir, os.killpg, os.kill = listdir, killpg, lambda pid, sig: killed.append(pid)
        module_globals["open"] = stat
        try:
            with contextlib.redirect_stderr(captured):
                return _survivors_of_session(4242)
        except SystemExit as exc:
            refused = type(exc.code) is int and exc.code == 2 and captured.getvalue() == census_line
            return "CENSUS-REFUSED" if refused else "OTHER-EXIT"
        finally:
            os.listdir, os.killpg, os.kill = saved
            del module_globals["open"]

    listing = lambda path: ["4242", "self"]
    non_member = _stat_of("1")
    no_group = _injected(ProcessLookupError)
    _expect(_census_with(listing, non_member, no_group) == 0,
            "census control: a readable non-member and an absent group read as an empty session")
    _expect(_census_with(listing, _injected(PermissionError), no_group) == "CENSUS-REFUSED",
            "census: a refused stat read is cannot-evaluate, never an absent process")
    _expect(_census_with(listing, _injected(FileNotFoundError), no_group) == 0,
            "census: a process that ended before its stat read reads as absence")
    _expect(_census_with(listing, _injected(ProcessLookupError), no_group) == 0,
            "census: a process that ended during its stat read reads as absence")
    _expect(_census_with(listing, lambda path, mode="r": io.BytesIO(b"4242 (a) S 1\n"), no_group)
            == "CENSUS-REFUSED",
            "census: a stat too short to carry the session field is cannot-evaluate, never a non-member")
    _expect(_census_with(listing, non_member, _injected(PermissionError)) == "CENSUS-REFUSED",
            "census: a refused group probe is cannot-evaluate, never an absent group")
    _expect(_census_with(_injected(PermissionError), non_member, no_group) == "CENSUS-REFUSED",
            "census: a refused /proc listing is cannot-evaluate, never an empty session")
    _expect(_census_with(_injected(FileNotFoundError), non_member, no_group) == "CENSUS-REFUSED",
            "census: a missing /proc is cannot-evaluate, never an empty session")
    member = _stat_of("4242")

    def _member_beside_unreadable(path, mode="r"):
        if path == "/proc/4242/stat":
            return member(path, mode)
        raise PermissionError("injected census fault")

    del killed[:]
    _expect(_census_with(lambda path: ["4242", "4243"], _member_beside_unreadable, no_group)
            == "CENSUS-REFUSED" and killed == [4242],
            "census: a member found beside an unreadable entry is killed before the census refuses")
    reads = []

    def _member_then_gone(path, mode="r"):
        reads.append(path)
        if len(reads) == 1:
            return member(path, mode)
        raise FileNotFoundError("injected census fault")

    del killed[:]
    _expect(_census_with(listing, _member_then_gone, no_group) == 1 and killed == [4242],
            "census: a member found alone is killed and counted")


# The loaded-exit vectors run in a child of the self-test that must end 0 with exactly this file's
# completion line on stdout: a validator load moved back into the process running them would let a
# loaded os._exit(0) end that child 0 before the line, which is red here, never a silent pass.
_LOADED_EXIT_PROBE = """import importlib.util, sys
tool = sys.argv[1]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_loaded_exit_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
gate._CHILD_SETTLE_SECONDS = 2.0
gate._self_test_loaded_exit()
sys.stdout.write("LOADED-EXIT-VECTORS-COMPLETE\\n")
"""


def _self_test_loaded_exit_in_child():
    """Run _self_test_loaded_exit in a child (see _LOADED_EXIT_PROBE); require exit 0 and the completion
    line alone on stdout."""
    import subprocess
    import tempfile
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-loaded-exit-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_LOADED_EXIT_PROBE, encoding="utf-8")
        try:
            child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool],
                                   capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            _expect(False, "loaded-exit vectors: the child did not run ({})".format(type(exc).__name__))
    _expect(child.returncode == 0 and child.stdout == "LOADED-EXIT-VECTORS-COMPLETE\n",
            "loaded-exit vectors: the child ended {} without the completion line alone on stdout: {}".format(
                child.returncode, child.stderr[-4000:]))


def _self_test():
    # Git-free / source-free: exercise the matchers on in-memory text, including the comment-evasion cases.
    _expect(_has_line("KEEP_SCHEMA = 1  # comment", "KEEP_SCHEMA = 1"), "exact line ignores comment")
    _expect(not _has_line("KEEP_SCHEMA = 10\n", "KEEP_SCHEMA = 1"), "substring must NOT match a longer value")
    _expect(_quoted_in_tuple('_RESERVED = (\n".opf.toml",\n".working/toml",\n)', "_RESERVED") == \
        {".opf.toml", ".working/toml"}, "tuple membership")
    _expect(".x" not in _quoted_in_tuple('_RESERVED = (\n# ".x",\n".y",\n)', "_RESERVED"), \
        "a commented-out literal must NOT count as a member")
    _expect(_ordered_quoted_in_tuple('_INITIAL_VIEWS = (\n"A.md", "B.md",\n)', "_INITIAL_VIEWS") == \
        ("A.md", "B.md"), "ordered view parse")
    _expect(_value_of('DEFAULT_MACHINE_SUBDIR = "toml"  # c', "DEFAULT_MACHINE_SUBDIR") == "toml", "value")
    _expect(_active_line_has('run x check_opf_init_contract.py', "check_opf_init_contract.py"), "active line")
    _expect(not _active_line_matches('run check_opf_init_contract.py --self-test', r"\b_opf_init_contract\.py"), \
        "validator boundary must NOT match inside the gate filename")
    _expect(_active_line_matches('run "$here/_opf_init_contract.py" --self-test', r"\b_opf_init_contract\.py"), \
        "validator boundary must match a real validator invocation")
    _expect(not _active_line_has('# run check_opf_init_contract.py', "check_opf_init_contract.py"), \
        "a commented registration is not active")
    labels = ["F{:02d}".format(n) for n in range(1, 31)]
    _expect(labels[0] == "F01" and labels[-1] == "F30" and len(labels) == 30, "F-range")
    _expect(ACTOR_LINE.count('"') == 8, "actor line shape")
    _self_test_census_errors()
    _self_test_loaded_exit_in_child()
    _self_test_fault_channels()
    sys.stdout.write("PASS check_opf_init_contract self-test\n")
    sys.exit(0)



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass. Before
# a passing verdict the settle waits (_SETTLE_SECONDS in all) for every other thread still running,
# threading's census and the runtime's live count of low-level _thread workers together, so a worker's
# later fault is recorded instead of killed silently by the exit, and a thread or low-level worker still
# running at the bound fails the verdict closed (exit 2). On Linux the settle also waits on the OS's own
# thread list for this process (_os_threads_pending), which still shows a worker while the runtime clears
# its thread state (its thread-local and context values' destructors run there, after the runtime's live
# count and threading's census already dropped it), a faulthandler watchdog and a thread a C extension
# starts. The verdict then ends the process with os._exit, so an
# atexit callback registered by loaded code can never run after it (that channel is unreachable, not
# merely disclosed). Only a SystemExit with code None or 0 ending a worker thread is the interpreter's
# normal SUCCESSFUL thread exit (default-hook parity) and is not recorded; any other code is an
# unsuccessful exit a worker reported, so it is recorded.
# A low-level _thread worker's SystemExit never reaches any hook (the interpreter drops it), so the
# start entries and threading's saved module aliases of them are wrapped from before the gate's work and
# a non-zero code ending such a worker is recorded as a fault there. The subinterpreter modules are made
# unimportable before the gate's work, because a thread inside a subinterpreter reports its faults to
# that interpreter's own hooks, never these recorders. Stated residuals, not closed: where the platform
# has no per-process thread list (outside Linux) a thread outside the Python runtime stays invisible to
# the census and the live count; and a process the loaded code starts is not followed, so one still
# running after this gate ends is outside its verdict.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. After the exit flushes the settle stops the collector, blocks every signal, runs any
# handler already tripped, and re-reads the record and every liveness term, so a fault recorded or a
# worker a signal handler started while a flush was blocked on a full pipe still fails the verdict, and
# no loaded code can run between that last read and the exit.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (deliberately hostile loaded code is accepted there and not closed).
_LOADED_FAULTS = []
# A started low-level worker's liveness flag, set synchronously at the start call: the runtime
# live count misses the brief window before a just-started worker's OS thread registers, so the
# settle also waits while any flag is set, never breaking on that transient zero.
_LOADED_WORKERS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    # A local alias: opf.py's close-lifecycle census reads an attribute store through the bare module name
    # as shadowing that module.
    import threading as _threads

    # The modules that create a subinterpreter are made unimportable (an import is an ImportError the
    # gate's work fails on): a thread inside one reports its faults to that interpreter's own hooks.
    for _name in ("_interpreters", "concurrent.interpreters", "_testinternalcapi", "_testcapi"):
        sys.modules[_name] = None

    previous_unraisable = sys.unraisablehook
    previous_thread = _threads.excepthook

    def _record(channel, chained, args):
        _LOADED_FAULTS.append(channel)
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a {} fault was recorded; a passing verdict will fail closed\n".format(
                _FAULT_MARKER, channel))
        finally:
            chained(args)

    def _record_unraisable(args):
        _record("destructor-or-callback", previous_unraisable, args)

    def _benign_thread_exit(args):
        # Only a worker thread ending by SystemExit(None) or SystemExit(0) is the interpreter's normal
        # SUCCESSFUL thread exit (default-hook parity). Any other SystemExit code is an unsuccessful exit
        # a worker reported, recorded like any other fault (never a silent pass); the code is read
        # through exact built-in types alone and never formatted.
        if args.exc_type is None or not issubclass(args.exc_type, SystemExit):
            return False
        code = getattr(args.exc_value, "code", None) if args.exc_value is not None else None
        return code is None or (type(code) is int and code == 0)

    def _record_thread(args):
        if _benign_thread_exit(args):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    _threads.excepthook = _record_thread

    import _thread as _lowlevel

    def _record_lowlevel_fault():
        _LOADED_FAULTS.append("lowlevel-worker")
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a lowlevel-worker fault was recorded; a passing verdict will fail "
                             "closed\n".format(_FAULT_MARKER))
        except Exception:
            pass

    import types as _types
    _bootstrap = _threads.Thread._bootstrap

    def _genuine_bootstrap(target):
        # A thread start is threading's own only when its target is exactly a bound method whose function
        # is threading.Thread._bootstrap and whose instance is a threading.Thread (read through the exact
        # method type, never an attribute the target supplies). Such a worker's fault already reaches
        # threading.excepthook, so it is passed through untouched; wrapping it would also replace the
        # target an audit-based observer reads, making threading's own start look like a raw one.
        return (type(target) is _types.MethodType and target.__func__ is _bootstrap
                and isinstance(target.__self__, _threads.Thread))

    def _wrap_lowlevel(original):
        # A raw low-level worker's SystemExit reaches no hook (the interpreter drops it), so the start
        # entry wraps the worker's function and records a non-zero code as a fault (None or 0 is the
        # successful low-level exit, default-interpreter parity; the code is read through exact built-in
        # types alone and never formatted), and tracks the worker's liveness flag. A genuine threading
        # start is passed through untouched.
        def _start(function, *args, **kwargs):
            if _genuine_bootstrap(function):
                return original(function, *args, **kwargs)
            state = [True]
            def _run(*wargs, **wkwargs):
                try:
                    return function(*wargs, **wkwargs)
                except SystemExit as exc:
                    code = getattr(exc, "code", None)
                    if not (code is None or (type(code) is int and code == 0)):
                        _record_lowlevel_fault()
                    raise
                finally:
                    state[0] = False
            result = original(_run, *args, **kwargs)
            _LOADED_WORKERS.append(state)
            return result
        return _start

    _wrapped = []
    for _entry in ("start_new_thread", "start_new", "start_joinable_thread"):
        if hasattr(_lowlevel, _entry):
            _original = getattr(_lowlevel, _entry)
            _replacement = _wrap_lowlevel(_original)
            setattr(_lowlevel, _entry, _replacement)
            _wrapped.append((_original, _replacement))
    for _alias in dir(_threads):
        _value = getattr(_threads, _alias)
        for _original, _replacement in _wrapped:
            if _value is _original:
                setattr(_threads, _alias, _replacement)


# The OS's own list of this process's threads (Linux): it still shows a worker while the runtime clears
# its thread state, a faulthandler watchdog and a thread a C extension starts. None where the platform has
# no such list; the runtime's own counts then carry the settle alone (a stated residual).
_TASK_DIR = "/proc/self/task" if sys.platform.startswith("linux") else None


def _os_threads_pending():
    """True while the OS lists a thread of this process besides the calling one, and when that list
    exists but cannot be read (fail-closed); False where the platform has no such list."""
    import os
    if _TASK_DIR is None:
        return False
    try:
        return len(os.listdir(_TASK_DIR)) != 1
    except OSError:
        return True


def _drain_pending_signals():
    """A Python-level call: the interpreter runs any signal handler already tripped on entry here."""
    return None


_SETTLE_SECONDS = 60.0


def _gate_exit(code):
    """Settle and END the process: before a passing verdict, wait (up to _SETTLE_SECONDS in all) for every
    other thread still running, so a fault it raises later is recorded, never killed silently by the exit,
    and fail closed (exit 2) if one still runs at the bound (the census covers threading's threads and,
    through the runtime's live count, workers started through the low-level _thread entry points, so a
    low-level worker still running at the bound also fails closed, and on Linux the OS's own thread list
    covers a worker still clearing its thread state, a watchdog and a C extension's thread; elsewhere a
    thread outside the Python runtime is invisible: a stated residual); force a recorded background fault
    to exit 2 over a passing verdict, flush both streams, then stop the collector, block every signal, run
    any handler already tripped and re-read the record and every liveness term AFTER the flushes (a fault
    recorded or a worker started while a flush was blocked, on a full pipe say, must still fail the
    verdict; its late marker is written straight to fd 2, past the buffers), then os._exit, so no atexit
    callback registered by loaded code
    runs after the verdict (the gate's own cleanup runs inside its work; this gate registers no atexit work
    of its own)."""
    import os
    import threading as _threads
    import time
    import _thread as _lowlevel
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    low = code & 0xFF
    if code != 0 and low == 0:
        # os._exit truncates its status to the low 8 bits, so this non-zero code would end the
        # process with exit status 0, a silent pass; fail closed instead.
        sys.stderr.write("{}: a SystemExit code outside the exit-status range at the gate entry; exit 2\n".format(
            _FAULT_MARKER))
        code = 2
    else:
        # os._exit refuses a code outside the C int range (OverflowError) instead of exiting, and
        # that escape would let atexit callbacks registered by loaded code run after the verdict:
        # take the low byte, the status any in-range code would report, so the exit below always runs.
        code = low
    if code == 0:
        current = _threads.current_thread()
        deadline = time.monotonic() + _SETTLE_SECONDS
        while True:
            running = [worker for worker in _threads.enumerate()
                       if worker is not current and worker.is_alive()]
            pending = (_lowlevel._count() or any(_state[0] for _state in _LOADED_WORKERS)
                       or _os_threads_pending())
            remaining = deadline - time.monotonic()
            if (not running and not pending) or remaining <= 0:
                break
            if running:
                running[0].join(min(remaining, 0.5))
            else:
                time.sleep(min(remaining, 0.05))
        if running or pending:
            sys.stderr.write("{}: a worker thread or low-level worker was still running at the verdict; "
                             "fail-closed\n".format(_FAULT_MARKER))
            code = 2
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        sys.stderr.write("{}: {} background fault(s) from loaded code over a passing verdict; "
                         "fail-closed\n".format(_FAULT_MARKER, len(_LOADED_FAULTS) or 1))
        code = 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        if code == 0:
            code = 2
    if code == 0:
        # The verdict stands only once no loaded code can run before the exit: the collector is stopped,
        # every signal is blocked (no other thread is left to take one), a handler already tripped runs
        # now, and the record and every liveness term are read afresh, so a fault or a worker a handler
        # started while a flush was blocked fails the verdict.
        import gc as _gc
        import signal as _signals
        try:
            _gc.disable()
            _signals.pthread_sigmask(_signals.SIG_BLOCK, _signals.valid_signals())
            _drain_pending_signals()
        except BaseException:  # noqa: BLE001  a handler's own escape fails the verdict, never the exit
            code = 2
        if (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False) or _lowlevel._count()
                or any(_state[0] for _state in _LOADED_WORKERS) or _os_threads_pending()):
            code = 2
        if code != 0:
            try:
                os.write(2, ("{}: a background fault or a worker was recorded during the exit flush; "
                             "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
            except OSError:
                pass
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through the gate's REAL _fail_closed_main in a child that
# loads one fixture the way in-process code would fault in this gate's own process (the validator
# itself now loads in a grandchild under the fail-closed child contract, so a validator fault arrives as
# that child's refused contract instead, covered by the loaded-exit vectors): a clean load passes; an
# atexit callback registered by loaded code never runs (unreachable behind os._exit); a worker-thread
# fault and a destructor fault are recorded and force exit 2.
_FAULT_CHANNEL_PROBE = """import importlib.util, sys
tool, fixture = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_fault_channel_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
gate._SETTLE_SECONDS = 2.0
def load():
    case = importlib.util.spec_from_file_location("_fault_channel_fixture", fixture)
    module = importlib.util.module_from_spec(case)
    case.loader.exec_module(module)
    return 0
gate._fail_closed_main(load)
"""

# (label, fixture source, expected exit, marker required, text that must NOT appear on stderr)
_FAULT_CHANNEL_CASES = (
    ("clean", "x = 1\n", 0, False, None),
    ("atexit", "import atexit, sys\n"
     "def callback():\n"
     "    sys.stderr.write('FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "    raise RuntimeError('fault-fixture-atexit')\n"
     "atexit.register(callback)\n", 0, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("joined-thread", "import threading\n"
     "def fault():\n"
     "    raise RuntimeError('fault-fixture-thread')\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("destructor", "import gc\n"
     "class Fault:\n"
     "    def __del__(self):\n"
     "        raise RuntimeError('fault-fixture-destructor')\n"
     "Fault()\n"
     "gc.collect()\n", 2, True, None),
    ("thread-systemexit", "import threading\n"
     "def fault():\n"
     "    raise SystemExit(7)\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("thread-systemexit-zero", "import sys\nimport threading\n"
     "def done():\n"
     "    sys.exit(0)\n"
     "worker = threading.Thread(target=done)\n"
     "worker.start()\n"
     "worker.join()\n", 0, False, None),
    ("finishing-thread", "import threading, time\n"
     "threading.Thread(target=time.sleep, args=(0.3,)).start()\n", 0, False, None),
    ("unjoined-thread", "import threading, time\n"
     "def fault():\n"
     "    time.sleep(0.5)\n"
     "    raise RuntimeError('fault-fixture-unjoined-thread')\n"
     "threading.Thread(target=fault).start()\n", 2, True, None),
    ("unfinished-thread", "import threading\n"
     "threading.Thread(target=threading.Event().wait, daemon=True).start()\n", 2, True, None),
    # Low-level _thread workers: the interpreter drops a SystemExit ending one without calling any hook,
    # so the wrapped start entry records a non-zero code as a fault (None or 0 stays the successful
    # low-level exit), and the settle waits on the runtime's live count, so one still running at the
    # bound fails the verdict closed while one that finishes is waited out.
    ("lowlevel-systemexit", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(7)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 2, True, None),
    ("lowlevel-systemexit-zero", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(0)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 0, False, None),
    ("lowlevel-finishing", "import _thread, time\n"
     "_thread.start_new_thread(time.sleep, (0.3,))\n", 0, False, None),
    ("lowlevel-unfinished", "import _thread, threading\n"
     "_thread.start_new_thread(threading.Event().wait, ())\n", 2, True, None),
    # A handled failed low-level start leaves no pending worker: red when the start flag is recorded
    # before the start call (the flag then stays set and the settle fails closed at the bound).
    ("lowlevel-failed-start", "import _thread\n"
     "try:\n"
     "    _thread.start_new_thread(print)\n"
     "except TypeError:\n"
     "    pass\n", 0, False, None),
    # Thread-state teardown: a worker's thread-local and context-variable values are destroyed while the
    # runtime clears its thread state, after its live count and threading's census already dropped it;
    # the OS thread list still shows the worker, so the settle waits and records the fault (red without
    # that term: each ends 0).
    ("lowlevel-local-destructor", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    ("thread-local-destructor", "import threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-thread-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "threading.Thread(target=worker).start()\n"
     "entered.wait()\n", 2, True, None),
    ("contextvar-destructor", "import _thread, contextvars, threading, time\n"
     "entered = threading.Event()\n"
     "held = contextvars.ContextVar('held')\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-contextvar-destructor')\n"
     "def worker():\n"
     "    held.set(Held())\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The runtime's live count, the term that carries the settle where the platform has no thread list:
    # the fixture switches the OS list off in the probe's gate, and a low-level worker whose return value's
    # destructor still runs after its callback returned must still be waited out and recorded (red
    # without the live count: it ends 0).
    ("runtime-count-return-destructor", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-return-destructor')\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The settle's own reads of those two terms: a teardown destructor or a return value's destructor
    # that finishes without a fault is waited out and passes (red without the settle-position read: the
    # re-read after the flushes then still sees the worker and fails the clean run closed).
    ("lowlevel-local-finishing", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    ("runtime-count-return-finishing", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    # Capability removal: every subinterpreter module is unimportable (red when any one is left
    # importable: the fixture then ends 3).
    ("subinterpreter-modules", "import importlib\n"
     "for name in ('_interpreters', 'concurrent.interpreters', '_testinternalcapi', '_testcapi'):\n"
     "    try:\n"
     "        importlib.import_module(name)\n"
     "    except ImportError:\n"
     "        continue\n"
     "    raise SystemExit(3)\n", 0, False, None),
    # An armed faulthandler watchdog is a C-level thread only the OS thread list shows; still armed at
    # the bound it fails the verdict closed (red without that term: it ends 0).
    ("watchdog-armed", "import faulthandler\n"
     "faulthandler.dump_traceback_later(600)\n", 2, True, None),
    # Exit-status range: os._exit truncates its status to the low 8 bits, so a non-zero SystemExit
    # code that is a multiple of 256 would end the child with status 0, a silent pass; the gate maps
    # it to 2. The neighbouring codes pass through (257 reaches the parent as its low byte, 1).
    ("exit-status-255", "raise SystemExit(255)\n", 255, False, None),
    ("exit-status-256", "raise SystemExit(256)\n", 2, True, None),
    ("exit-status-257", "raise SystemExit(257)\n", 1, False, None),
    ("exit-status-negative-256", "raise SystemExit(-256)\n", 2, True, None),
    # Codes around the ends of the C int range: os._exit raises OverflowError on a code outside that
    # range instead of exiting, which would let the loaded code's atexit callbacks run after the
    # verdict; the gate takes the low byte first, so the exit always runs. The wide codes (beyond
    # each end, with non-zero low bytes) reach the parent as their low bytes only through that
    # ordering. The boundary codes (2**31, just past the positive end, and -(2**31), exactly the
    # negative end) have a zero low byte and fail closed as exit 2; the multiple-of-256 rule alone
    # already gives both that outcome, so those two cases are preservation controls over the range
    # ends, not evidence that the ordering is required. The code at the positive end itself,
    # 2**31 - 1, has low byte 255 and is inside os._exit's accepted range.
    ("exit-status-wide-positive", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(2**31 + 1)\n", 1, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-wide-negative", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(-(2**31) - 1)\n", 255, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-boundary-positive", "raise SystemExit(2**31)\n", 2, True, None),
    ("exit-status-boundary-negative", "raise SystemExit(-(2**31))\n", 2, True, None),
)


# The flush-window channel: _gate_exit's fault decision must hold across the exit flushes. The fixture
# fills the child's stdout pipe to its exact capacity (F_GETPIPE_SZ) and leaves one byte in the stream's
# buffer, then arms a timer (SIGALRM unblocked: a caller's blocked mask is inherited) whose signal handler
# drops an object with a faulting destructor, so the fault is recorded while the exit flush is blocked on
# the full pipe (after the settle's thread wait, which a worker-thread fault would not outlast); the
# parent drains stdout only after stderr shows the fault marker. Exit 2 with the marker is required. Red
# when the fault decision runs only before the flushes: the child then ends 0 with the marker on stderr.
_FLUSH_WINDOW_FIXTURE = """import fcntl, os, signal, sys
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
class Fault:
    def __del__(self):
        raise RuntimeError("fault-fixture-flush-window")
def fault(signum, frame):
    Fault()
signal.signal(signal.SIGALRM, fault)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""

# The same window with a start instead of a fault: the handler starts a low-level worker that never ends
# while the exit flush is blocked, then reports on fd 2, and the parent drains stdout only after that
# line. Exit 2 with the marker is required. Red when only the record is read after the flushes: the
# child then ends 0 with the worker still running.
_FLUSH_WINDOW_START_FIXTURE = """import _thread, fcntl, os, signal, sys, threading
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
def start(signum, frame):
    _thread.start_new_thread(threading.Event().wait, ())
    os.write(2, b"FAULT-FIXTURE-FLUSH-START\\n")
signal.signal(signal.SIGALRM, start)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""


def _flush_window_failures():
    """One failure string per flush-window requirement a child missed (see _FLUSH_WINDOW_FIXTURE and
    _FLUSH_WINDOW_START_FIXTURE)."""
    import subprocess
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for label, fixture, ready in (
                ("flush-window", _FLUSH_WINDOW_FIXTURE, _FAULT_MARKER + ":"),
                ("flush-window-start", _FLUSH_WINDOW_START_FIXTURE, "FAULT-FIXTURE-FLUSH-START")):
            case = Path(tmp) / (label + ".py")
            case.write_text(fixture, encoding="utf-8")
            try:
                child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            with child:
                killer = threading.Timer(120, child.kill)
                killer.start()
                try:
                    header = b""
                    while ready.encode("ascii") not in header:
                        line = child.stderr.readline()
                        if not line:
                            break
                        header += line
                    child.stdout.read()
                    trailer = child.stderr.read()
                    code = child.wait()
                finally:
                    killer.cancel()
            stderr_text = (header + trailer).decode("utf-8", "replace")
            if code != 2:
                failures.append("fault-channel/{}: expected exit 2, got {}".format(label, code))
            elif (_FAULT_MARKER + ":") not in stderr_text:
                failures.append("fault-channel/{}: fault marker absent".format(label))
    return failures


# The entry wiring, checked statically on this file's own source: the final __main__ block must be
# exactly _ENTRY_WIRING by AST (spacing and comments aside), so a rewiring that keeps _fail_closed_main
# defined but routes an entry branch around it (a plain sys.exit around the work, say) is red even though
# the channel probes above drive _fail_closed_main directly.
_ENTRY_WIRING = '''if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _fail_closed_main(_self_test)
    _fail_closed_main(_checks)
'''


def _entry_wiring_failures():
    """One failure string per entry-wiring requirement this file's own source misses (see _ENTRY_WIRING)."""
    import ast
    failures = []
    try:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return ["fault-channel/entry-wiring: this file could not be parsed ({})".format(type(exc).__name__)]
    last = tree.body[-1] if tree.body else None
    expected = ast.parse(_ENTRY_WIRING).body[0]
    if last is None or ast.dump(last) != ast.dump(expected):
        failures.append("fault-channel/entry-wiring: the final __main__ block is not the expected "
                        "fail-closed form")
    return failures


def _self_test_fault_channels():
    """Each fault-channel case must end the child as required: the expected exit code, the fault marker
    exactly when a fault must be recorded, and for the atexit case no trace of the callback (it must never
    run). Red without the entry wrapper (the probe then ends 1, AttributeError on _fail_closed_main, where
    clean and atexit expect 0) and red with the wrapper reverted to a plain exit (the thread and destructor
    children then end 0 with the default hooks' output alone, where exit 2 with the marker is required,
    and the atexit child then runs the callback)."""
    import subprocess
    import tempfile
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-fault-channel-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for index, (label, body, expected, marked, absent) in enumerate(_FAULT_CHANNEL_CASES):
            case = Path(tmp) / "fault_channel_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            if child.returncode != expected:
                failures.append("fault-channel/{}: expected exit {}, got {}".format(
                    label, expected, child.returncode))
            elif marked != ((_FAULT_MARKER + ":") in child.stderr):
                failures.append("fault-channel/{}: fault marker {}".format(
                    label, "absent" if marked else "present"))
            elif absent is not None and absent in child.stderr:
                failures.append("fault-channel/{}: the atexit callback ran".format(label))
    failures.extend(_flush_window_failures())
    failures.extend(_entry_wiring_failures())
    _expect(not failures, "fault-channel vectors: " + "; ".join(failures))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _fail_closed_main(_self_test)
    _fail_closed_main(_checks)
