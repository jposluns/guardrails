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
# process (the signal goes to the process group) and propagates to stop the gate runner.


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


# The child that loads the validator. It records the loaded code's background faults (a destructor or
# other unraisable fault; a worker thread ending by an exception or by a SystemExit whose code is not None
# or 0) from BEFORE the load, computes the result (the load and the membership test), then runs the
# loaded code's cleanup itself: it waits for every other thread (_CHILD_SETTLE_SECONDS in all), runs the
# atexit callbacks the loaded code registered and collects garbage. An atexit callback or a collected
# destructor can itself start a worker, which the settle (taken before those cleanup phases) never saw:
# every thread-start entry is wrapped from BEFORE the load (threading.Thread.start, the low-level _thread
# entry points, and every alias of those entry points the threading module itself holds, bound when it was
# imported, so a start method or alias the loaded code saved at load time still lands in a wrapper), a
# start during the cleanup phases is recorded, and the thread list is enumerated afresh after them, so a
# worker started by cleanup is a fault whether it finishes or not. A low-level worker never joins
# threading's census, so each low-level start also wraps the function it runs: the worker's lifetime is
# tracked to its end, an ending by an exception or by a SystemExit whose code is not None or 0 is a
# recorded fault whenever it was started, the settle waits for tracked workers too, and one still pending
# at the late look is refused like a late thread. Only when no fault was recorded, no worker was started
# by cleanup and no other thread or tracked worker still runs does it write the result, LAST, and end by
# os._exit(0) bound before the load, so no loaded code runs after the result is written (an os._exit at
# load, in the membership test, in a worker or in an atexit callback ends the child before the result).
# The parent accepts only exit 0, both streams empty and a well-formed result.
_CHILD_SETTLE_SECONDS = 60.0
_LOADED_CHILD = """import atexit, gc, json, os, runpy, sys, threading, time, _thread
validator, out, settle = sys.argv[1], sys.argv[2], float(sys.argv[3])
views = sys.argv[4:]
end = os._exit
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
        workers.append(state)
        return original(run, *args, **kwargs)
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
ns = runpy.run_path(validator)
live = ns.get("_RESERVED")
if type(live) is not tuple:
    payload = {"not_tuple": True}
else:
    payload = {"missing": [view for view in views if (".working/" + view) not in live]}
del ns, live
current = threading.current_thread()
deadline = time.monotonic() + settle
while True:
    running = [worker for worker in threading.enumerate() if worker is not current and worker.is_alive()]
    pending = [state for state in workers if state[0]]
    if (not running and not pending) or time.monotonic() >= deadline:
        break
    if running:
        running[0].join(min(max(deadline - time.monotonic(), 0.0), 0.5))
    else:
        time.sleep(0.05)
in_cleanup = True
atexit._run_exitfuncs()
gc.collect()
late = [worker for worker in threading.enumerate() if worker is not current and worker.is_alive()]
late_pending = [state for state in workers if state[0]]
if faults or running or pending or started or late or late_pending:
    sys.stderr.write("LOADED-CODE-FAULT: the validator child recorded a fault, a running thread or "
                     "worker, or a worker started by cleanup\\n")
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


def _validator_missing(path, views):
    """Load the validator at path in a CHILD process under the fail-closed child contract and return the
    views missing from its ACTUAL _RESERVED tuple. The contract: the child computes the result, runs the
    loaded code's cleanup (waits for its threads, runs its atexit callbacks, collects garbage), writes the
    result LAST and ends by os._exit(0) with both streams empty; this gate accepts only that exact
    outcome. ANY other ending of the loaded code, an os._exit with ANY status (in a worker or an atexit
    callback included), a sys.exit or SystemExit (0 or None included), a KeyboardInterrupt, a signal
    handler it installs, any other BaseException, a background fault (a worker's exception or unsuccessful
    SystemExit, a destructor or atexit fault, recorded in the child; a low-level worker's unsuccessful
    ending is recorded through its tracked lifetime whenever it was started), a worker its atexit callbacks
    or garbage collection start (every start entry is wrapped from before the load: threading.Thread.start,
    the low-level _thread entry points and threading's own module-level aliases of them alike, so a start
    method or alias saved at load time is still seen; a start during those cleanup phases is a fault
    whether or not the worker finishes, and the thread list is re-enumerated after them), or a thread or
    tracked low-level worker still running at the
    bound, leaves a missing result, a non-zero exit, or a non-empty stream, and is CANNOT-EVALUATE (exit
    2), never this gate's pass: the loaded code never runs in this gate's process, so the os._exit channel
    of the in-process load is unreachable here, not disclosed. An operator's Ctrl-C is a KeyboardInterrupt
    in THIS process (the signal reaches the whole group) and propagates to stop the runner. Residual:
    deliberately hostile loaded code forging the child contract (writing the result file itself and
    ending cleanly) is the reporting-machinery channel disclosed once in the _opf_views class disclosure
    (accepted there, not closed), with the hostile-object residual stated there."""
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-loaded-") as tmp:
        child_py = Path(tmp) / "loaded_child.py"
        child_py.write_text(_LOADED_CHILD, encoding="utf-8")
        out = Path(tmp) / "result.json"
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-B", str(child_py), str(path), str(out), str(_CHILD_SETTLE_SECONDS)]
                + [str(v) for v in views],
                capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            _cant("loading the validator to verify _RESERVED membership: the child did not complete")
        if not _child_contract_kept(child.returncode, child.stdout, child.stderr):
            _cant("loading the validator to verify _RESERVED membership: the child ended outside the "
                  "fail-closed contract (the loaded code ended it, or a fault was reported); fail-closed")
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cant("loading the validator to verify _RESERVED membership: the child wrote no result "
                  "(the loaded code ended the child before the verdict); fail-closed")
        if type(payload) is not dict or ("missing" in payload) == ("not_tuple" in payload):
            _cant("loading the validator to verify _RESERVED membership: the child result is malformed")
        if payload.get("not_tuple"):
            _cant("validator _RESERVED is not a tuple at load time")
        missing = payload["missing"]
        if type(missing) is not list or any(type(view) is not str for view in missing):
            _cant("loading the validator to verify _RESERVED membership: the child result is malformed")
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

    # C-SPEC-VERSION: the base spec_version the schema-compat obligation targets. Re-verified at 1.2.0 for
    # OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decision 5): the bump admits the managed init.toml provenance as a
    # C-CONTAINMENT leaf. For a 1.1.0 store, the upgrade schema delta changes only spec_version;
    # declared views are then regenerated, so a stale committed view can change.
    if not _has_line(store, 'SUPPORTED_SPEC_VERSION = "1.2.0"'):
        _cant("_opf_store.py SUPPORTED_SPEC_VERSION changed; re-verify the schema-compat boundary")

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
    empty, a result written that carries the production payload, an empty missing list for these bodies),
    "MALFORMED" when the process legs held but the result payload is absent or wrong (distinct, so an
    acceptance is never credited to a child that did not evaluate the body), "REFUSED" when it ran and
    refused, "NO-RUN" when it did not run (distinct, so a control expecting either outcome fails on a dead
    child); the leg controls drive a deliberately weakened copy of _LOADED_CHILD through this to show
    which refusal leg carries each cleanup-worker vector."""
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-leg-control-") as tmp:
        child_py = Path(tmp) / "loaded_child.py"
        child_py.write_text(child_source, encoding="utf-8")
        val = Path(tmp) / "validator.py"
        val.write_text(body, encoding="utf-8")
        out = Path(tmp) / "result.json"
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-B", str(child_py), str(val), str(out),
                 str(_CHILD_SETTLE_SECONDS), "a.md"],
                capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            return "NO-RUN"
        if child.returncode == 0 and not child.stdout and not child.stderr:
            try:
                payload = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return "MALFORMED"
            return "ACCEPTED" if payload == {"missing": []} else "MALFORMED"
        return "REFUSED"


def _loaded_exit_failure(label, path, expect_missing=None):
    """None when _validator_missing over the validator at path ends as the case requires, else the failure.
    A refused case (expect_missing is None) must end exit 2 with the CANNOT-EVALUATE line (the child
    contract refuses the loaded code's ending, an os._exit with any status included), never this
    process's own end and never a pass; a clean case must return exactly expect_missing. A refusal is
    credited only when the fixture at path exists and the captured diagnostic is one the parent writes
    after the child process COMPLETED (the child ending outside the contract, ending inside it without a
    result, or reporting a loaded _RESERVED that is not a tuple): an absent fixture, a child that could
    not launch or timed out, and a malformed child result are each this test's failure, never a refusal,
    so a case whose fixture was never evaluated cannot read as refused. The CANNOT-EVALUATE line is
    captured and required, never printed, so a passing self-test shows none. An operator's Ctrl-C
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
        diagnostic = captured.getvalue()
        if not diagnostic.startswith("CANNOT-EVALUATE: "):
            return "{}: exit 2 without the CANNOT-EVALUATE line".format(label)
        if ("the child ended outside the fail-closed contract" not in diagnostic
                and "the child wrote no result" not in diagnostic
                and "validator _RESERVED is not a tuple" not in diagnostic):
            return "{}: exit 2 for a reason other than the completed child refusing the loaded code".format(
                label)
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
    the loaded code's cleanup, both streams empty) leaves every such ending refusable. Red if a contract
    leg is relaxed: a missing result read as clean fails the os._exit cases; an accepted non-empty stdout
    fails "load stdout noise" and an accepted non-empty stderr fails "stderr noise after a valid result"
    (each the only leg its case breaks); the child's recorders, its own atexit run and its running-thread
    refusal each have a case with a valid _RESERVED that only that leg refuses (worker SystemExit(7), an
    atexit os._exit(0) or fault, a worker still running at the bound), and its thread wait has a clean
    case (a worker finishing after the load) that is refused without it. A worker started by the child's
    OWN cleanup run (an atexit callback or a destructor the child's garbage collection runs) is refused
    whether it finished before cleanup returned, faults later or never ends, started through a start
    method saved at load time and through the low-level _thread entry points alike: those cases are red
    when the start record is installed only around the cleanup phases or covers only
    threading.Thread.start; the joined-worker case is red without the start record (its leg control
    shows a record-disabled child accepts it); the never-ending case is red without the late census
    (the same control child still refuses it through the census alone); and the clean finishing-worker
    case above shows a load-started worker the settle waits out still passes. A cleanup worker started
    through threading's own saved module alias of a low-level start entry is refused (red when only the
    _thread attributes are rebound: its leg control shows a child without the alias rebinding accepts it);
    a cleanup worker through each further low-level entry has a case of its own (red when that entry's
    wrap is dropped: the narrowed-entry control child accepts both); and a low-level worker started at
    LOAD time is refused when it ends unsuccessfully (red without the lifetime fault record, shown by its
    leg control) or still runs at the verdict (red without the lifetime tracking, shown by its leg
    control), while the clean low-level finishing-worker case shows one the settle waits out still passes.
    The refusal oracle itself is pinned: an absent fixture must read as this test's failure, never as a
    refusal (red when any CANNOT-EVALUATE exit 2 is accepted without evidence the child completed). The
    exit-status leg, which no
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
    # Leg controls: each refusal leg added for cleanup-started workers must carry weight on its own. A
    # copy of the child with the start record disabled (the recording line replaced, required to exist)
    # shows the joined-worker vector above is refused by the start record alone (that copy accepts it:
    # nothing else can see a cleanup worker that already finished), and shows the late census still
    # refuses a cleanup worker the disabled record missed (red if the census is taken only before the
    # cleanup phases).
    record_off = _LOADED_CHILD.replace('started.append("cleanup-started-worker")', "pass")
    _expect(record_off != _LOADED_CHILD, "leg control: the start-recording line is present to disable")
    joined = (valid + "import atexit, threading\ndef cleanup():\n"
              "    worker = threading.Thread(target=lambda: None)\n"
              "    worker.start()\n    worker.join()\n"
              "atexit.register(cleanup)\n")
    _expect(_child_outcome(record_off, joined) == "ACCEPTED",
            "leg control: the joined cleanup worker is refused by the start record alone")
    never = (valid + "import atexit, threading\ndef cleanup():\n"
             "    threading.Thread(target=threading.Event().wait, daemon=True).start()\n"
             "atexit.register(cleanup)\n")
    _expect(_child_outcome(record_off, never) == "REFUSED",
            "leg control: the late census refuses a cleanup worker the start record missed")
    # Low-level leg controls, each a deliberately weakened child that must ACCEPT the vector its leg
    # refuses (an acceptance requires the production payload, so each control also shows its child ran
    # and evaluated the body): without the lifetime tracking a load-started low-level worker still
    # running at the verdict is invisible; without the lifetime fault record its unsuccessful ending is
    # silent; without the alias rebinding a worker started through threading's saved module alias is
    # unseen; and with the entry tuple narrowed to start_new_thread the other two entries are unseen.
    track_off = _LOADED_CHILD.replace("workers.append(state)", "pass")
    _expect(track_off != _LOADED_CHILD, "leg control: the low-level tracking line is present to disable")
    low_still = valid + "import _thread, threading\n_thread.start_new_thread(threading.Event().wait, ())\n"
    _expect(_child_outcome(track_off, low_still) == "ACCEPTED",
            "leg control: the load-started low-level worker is refused by the lifetime tracking alone")
    fault_off = _LOADED_CHILD.replace('faults.append("lowlevel-worker")', "pass")
    _expect(fault_off != _LOADED_CHILD, "leg control: the low-level fault line is present to disable")
    low_exit = (valid + "import _thread, threading\ndone = threading.Event()\n"
                "def worker():\n    try:\n        raise SystemExit(7)\n    finally:\n        done.set()\n"
                "_thread.start_new_thread(worker, ())\ndone.wait()\n")
    _expect(_child_outcome(fault_off, low_exit) == "ACCEPTED",
            "leg control: the low-level unsuccessful ending is refused by the lifetime fault record alone")
    alias_off = _LOADED_CHILD.replace("setattr(threading, alias, replacement)", "pass")
    _expect(alias_off != _LOADED_CHILD, "leg control: the alias rebinding line is present to disable")
    alias_worker = (valid + "import atexit, threading\n"
                    "atexit.register(lambda: threading._start_joinable_thread(threading.Event().wait))\n")
    _expect(_child_outcome(alias_off, alias_worker) == "ACCEPTED",
            "leg control: the cleanup worker through threading's saved alias is refused by the rebinding "
            "alone")
    entry_off = _LOADED_CHILD.replace('("start_new_thread", "start_new", "start_joinable_thread")',
                                      '("start_new_thread",)')
    _expect(entry_off != _LOADED_CHILD, "leg control: the low-level entry tuple is present to narrow")
    via_start_new = (valid + "import atexit, _thread, threading\n"
                     "atexit.register(lambda: _thread.start_new(threading.Event().wait, ()))\n")
    _expect(_child_outcome(entry_off, via_start_new) == "ACCEPTED",
            "leg control: the cleanup worker through _thread.start_new is refused by its wrap alone")
    via_joinable = (valid + "import atexit, _thread, threading\n"
                    "atexit.register(lambda: _thread.start_joinable_thread(threading.Event().wait))\n")
    _expect(_child_outcome(entry_off, via_joinable) == "ACCEPTED",
            "leg control: the cleanup worker through _thread.start_joinable_thread is refused by its "
            "wrap alone")


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
# a passing verdict the settle waits (_SETTLE_SECONDS in all) for every other thread still running, so a
# worker's later fault is recorded instead of killed silently by the exit, and a thread still running at
# the bound fails the verdict closed (exit 2). The verdict then ends the process with os._exit, so an
# atexit callback registered by loaded code can never run after it (that channel is unreachable, not
# merely disclosed). Only a SystemExit with code None or 0 ending a worker thread is the interpreter's
# normal SUCCESSFUL thread exit (default-hook parity) and is not recorded; any other code is an
# unsuccessful exit a worker reported, so it is recorded.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. The settle re-checks the record after the exit flushes, so a fault recorded while a
# flush was blocked on a full pipe still fails the verdict.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (deliberately hostile loaded code is accepted there and not closed).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    # A local alias: opf.py's close-lifecycle census reads an attribute store through the bare module name
    # as shadowing that module.
    import threading as _threads

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


_SETTLE_SECONDS = 60.0


def _gate_exit(code):
    """Settle and END the process: before a passing verdict, wait (up to _SETTLE_SECONDS in all) for every
    other thread still running, so a fault it raises later is recorded, never killed silently by the exit,
    and fail closed (exit 2) if one still runs at the bound (the census covers threading's threads; a worker
    started through the low-level _thread entry points is invisible to it, so only a fault such a worker
    has already raised is recorded: a disclosed residual, not closed); force a recorded background fault to exit 2
    over a passing verdict, flush both streams, re-check the record AFTER the flushes (a fault recorded
    while a flush was blocked, on a full pipe say, must still fail the verdict; its late marker is written
    straight to fd 2, past the buffers), then os._exit, so no atexit callback registered by loaded code
    runs after the verdict (the gate's own cleanup runs inside its work; this gate registers no atexit work
    of its own)."""
    import os
    import threading as _threads
    import time
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
            remaining = deadline - time.monotonic()
            if not running or remaining <= 0:
                break
            running[0].join(min(remaining, 0.5))
        if running:
            sys.stderr.write("{}: a worker thread was still running at the verdict; fail-closed\n".format(
                _FAULT_MARKER))
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
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        try:
            os.write(2, ("{}: a background fault was recorded during the exit flush; "
                         "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
        except OSError:
            pass
        code = 2
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


def _flush_window_failures():
    """One failure string per flush-window requirement the child missed (see _FLUSH_WINDOW_FIXTURE)."""
    import subprocess
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        case = Path(tmp) / "flush_window.py"
        case.write_text(_FLUSH_WINDOW_FIXTURE, encoding="utf-8")
        try:
            child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as exc:
            return ["fault-channel/flush-window: probe did not run ({})".format(type(exc).__name__)]
        with child:
            killer = threading.Timer(120, child.kill)
            killer.start()
            try:
                header = b""
                while (_FAULT_MARKER + ":").encode("ascii") not in header:
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
            failures.append("fault-channel/flush-window: expected exit 2, got {}".format(code))
        elif (_FAULT_MARKER + ":") not in stderr_text:
            failures.append("fault-channel/flush-window: fault marker absent")
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
