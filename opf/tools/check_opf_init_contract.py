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
    sys.stderr.write(
        "error: check_opf_init_contract.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import re
import runpy
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


# Threat model: the validator this gate loads in-process (_opf_init_contract.py) is reviewed in-repo code; the
# guard catches an ACCIDENTAL process ending from it (a stray sys.exit, SystemExit, KeyboardInterrupt,
# GeneratorExit, or any other BaseException) at load and in every later call this gate makes into it. A
# KeyboardInterrupt (an operator's Ctrl-C and one raised by loaded code are not told apart) is re-raised as a
# fresh KeyboardInterrupt, so it stops the gate runner instead of reading as this gate's exit 2.
_PROCESS_ENDING = (BaseException,)


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


def _in_loaded(what, call, *args):
    """Run `call` (a load of in-repo code in this process, or a call this gate makes into it) so that the
    loaded code can never end the gate with its own status: ANY exception (every BaseException, SystemExit
    0, None or a non-int code included) becomes CANNOT-EVALUATE (exit 2) with a fixed message naming `what`,
    except a KeyboardInterrupt, which is re-raised after a fixed message as a fresh KeyboardInterrupt (its
    context suppressed) so it stops the runner. Background fault channels are settled at this gate's entry
    (_fail_closed_main): a fault ending a worker thread or a destructor is recorded by the hooks installed
    there and forces exit 2 over a passing verdict, and the verdict ends the process with os._exit, so an
    atexit callback registered by loaded code never runs after it. Residuals, not covered: os._exit called
    by the loaded code itself, signal handlers it installs, mutation of sys or of this module's globals by
    the loaded code, deliberately
    hostile objects (the one residual stated in the _opf_views class disclosure), and a
    process exit raised while this module's own top-level imports run, before this guard is entered."""
    try:
        return call(*args)
    except KeyboardInterrupt:
        sys.stderr.write("INTERRUPTED: {}: KeyboardInterrupt re-raised to stop the run\n".format(what))
        raise KeyboardInterrupt from None
    except _PROCESS_ENDING as exc:
        _cant("{} raised {}; fail-closed".format(what, _ending_kind(exc)))


def _missing_views(live_reserved, views):
    """The pinned views whose .working/ path is not a member of the loaded validator's _RESERVED tuple."""
    return [view for view in views if (".working/" + view) not in live_reserved]


def _validator_missing(path, views):
    """Load the validator at `path` and return the views missing from its ACTUAL _RESERVED tuple. The load
    and the membership test run through _in_loaded; the type test is `type(...) is tuple`, which reads no
    attribute of the loaded value (isinstance would read its __class__)."""
    ns = _in_loaded("loading the validator to verify _RESERVED membership", runpy.run_path, path)
    live_reserved = ns.get("_RESERVED")
    if type(live_reserved) is not tuple:
        _cant("validator _RESERVED is not a tuple at load time")
    return _in_loaded("the validator _RESERVED membership test", _missing_views, live_reserved, views)


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

    # Value-based membership: load the pure validator and confirm the ACTUAL _RESERVED tuple contains
    # every pinned view path (the source-text checks above cannot see the view comprehension). runpy sets
    # __name__ to the module path, not "__main__", so the self-test does not run on load; any load failure,
    # including the loaded module ending the process (SystemExit 0 or None too), is fail-closed (exit 2).
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


# The KeyboardInterrupt the self-test's interrupt fixtures raise on purpose. Only the fresh KeyboardInterrupt
# _in_loaded raises for it is recorded; any other propagates, so an operator's Ctrl-C is never recorded.
_LOADED_INTERRUPT = "opf-init-contract-selftest-loaded-interrupt"


def _is_loaded_interrupt(exc, sent):
    """True only for the fresh KeyboardInterrupt _in_loaded raises for a loaded KeyboardInterrupt(sent): an
    exact KeyboardInterrupt with its context suppressed whose context is an exact KeyboardInterrupt with args
    exactly (sent,), read through exact built-in types alone. An operator's Ctrl-C carries no args, so it never
    matches: in the loaded code its replacement's context has no args, in _in_loaded's handler it is not
    context-suppressed, and later its context is the fresh one."""
    if type(exc) is not KeyboardInterrupt or exc.__suppress_context__ is not True:
        return False
    context = exc.__context__
    if type(context) is not KeyboardInterrupt:
        return False
    args = context.args
    return len(args) == 1 and type(args[0]) is str and args[0] == sent


def _loaded_exit_failure(label, path):
    """None when _validator_missing over the validator at `path` ends as the case `label` requires, else the
    failure. A label naming KeyboardInterrupt requires the fresh KeyboardInterrupt _in_loaded raises for a
    loaded KeyboardInterrupt(_LOADED_INTERRUPT), with the INTERRUPTED line; any other requires exit 2 with
    the CANNOT-EVALUATE line. Every other KeyboardInterrupt (an operator's Ctrl-C included) propagates
    unchanged; any other escape is recorded, never this test's own end."""
    import contextlib
    import io
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            _validator_missing(str(path), ("a.md",))
    except KeyboardInterrupt as exc:
        if not _is_loaded_interrupt(exc, _LOADED_INTERRUPT):
            raise
        if "KeyboardInterrupt" not in label:
            return "{}: expected exit 2, got the loaded KeyboardInterrupt".format(label)
        if not captured.getvalue().startswith("INTERRUPTED: "):
            return "{}: a fresh KeyboardInterrupt without the INTERRUPTED line".format(label)
    except BaseException as exc:  # noqa: BLE001  any other escape is recorded, never the test's own end
        if "KeyboardInterrupt" in label:
            return "{}: expected a fresh KeyboardInterrupt, got {}".format(label, _ending_kind(exc))
        if not (type(exc) is SystemExit and type(exc.code) is int and exc.code == 2):
            return "{}: expected exit 2, got {}".format(label, _ending_kind(exc))
        if not captured.getvalue().startswith("CANNOT-EVALUATE: "):
            return "{}: exit 2 without the CANNOT-EVALUATE line".format(label)
    else:
        return "{}: the loaded code's exit was not reached".format(label)
    return None


def _self_test_loaded_exit():
    """A loaded validator that ends the process, at load or in a later call the gate makes into it, yields
    this gate's CANNOT-EVALUATE exit 2 through _validator_missing (the function _checks calls), never its
    own status. Any exception escaping is recorded by _loaded_exit_failure, so the vector is red if the guard
    is reverted (_PROCESS_ENDING emptied) or removed from _validator_missing. A case whose label names
    KeyboardInterrupt must instead re-raise a fresh KeyboardInterrupt with the INTERRUPTED line, so the
    vector is red if the interrupt is absorbed as exit 2. Any other KeyboardInterrupt (one carrying another
    value, standing in for an operator's Ctrl-C) must propagate, so the vector is red if the recorder records
    it. The CANNOT-EVALUATE line each
    case is expected to write is captured and required, never printed, so a passing self-test shows none."""
    import tempfile
    repr_exits = "class R:\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
    cases = (
        ("load SystemExit(0)", "raise SystemExit(0)\n"),
        ("load SystemExit(None)", "raise SystemExit\n"),
        ("load KeyboardInterrupt", "raise KeyboardInterrupt({!r})\n".format(_LOADED_INTERRUPT)),
        ("call KeyboardInterrupt in a member __eq__",
         "class M:\n    def __eq__(self, other):\n        raise KeyboardInterrupt({!r})\n    __hash__ = None\n"
         "_RESERVED = (M(),)\n".format(_LOADED_INTERRUPT)),
        ("load GeneratorExit", "raise GeneratorExit\n"),
        ("load BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n"),
        ("load SystemExit(code whose repr exits 0)", repr_exits + "raise SystemExit(R())\n"),
        ("load Exception whose str exits 0",
         "class E(Exception):\n    def __str__(self):\n        raise SystemExit(0)\n    __repr__ = __str__\n"
         "raise E()\n"),
        ("_RESERVED whose __class__ exits 0",
         "class C:\n    @property\n    def __class__(self):\n        raise SystemExit(0)\n_RESERVED = C()\n"),
        ("call SystemExit(0) in a member __eq__",
         "class M:\n    def __eq__(self, other):\n        raise SystemExit(0)\n    __hash__ = None\n"
         "_RESERVED = (M(),)\n"),
    )
    failures = []
    with tempfile.TemporaryDirectory(prefix="opf-init-contract-selftest-") as tmp:
        for index, (label, body) in enumerate(cases):
            path = Path(tmp) / "loaded_exit_{}.py".format(index)
            path.write_text(body, encoding="utf-8")
            failure = _loaded_exit_failure(label, path)
            if failure is not None:
                failures.append(failure)
        # A KeyboardInterrupt carrying another value (standing in for an operator's Ctrl-C) propagates out of
        # the recorder, in a KeyboardInterrupt case and in an exit-2 case. Red if the recorder records it.
        other = _LOADED_INTERRUPT + "-other"
        for label, body in (("load KeyboardInterrupt", "raise KeyboardInterrupt({!r})\n".format(other)),
                            ("load SystemExit(0)", "raise KeyboardInterrupt({!r})\n".format(other))):
            path = Path(tmp) / "loaded_exit_other.py"
            path.write_text(body, encoding="utf-8")
            try:
                _loaded_exit_failure(label, path)
            except KeyboardInterrupt as exc:
                if not _is_loaded_interrupt(exc, other):
                    raise
            else:
                failures.append("{}: another KeyboardInterrupt was recorded, not propagated".format(label))
    _expect(not failures, "loaded-exit vectors: " + "; ".join(failures))


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
    _self_test_loaded_exit()
    _self_test_fault_channels()
    sys.stdout.write("PASS check_opf_init_contract self-test\n")
    sys.exit(0)



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass; the
# verdict then ends the process with os._exit, so an atexit callback registered by loaded code can never
# run after it (that channel is unreachable, not merely disclosed). A SystemExit ending a worker thread
# is the interpreter's normal thread exit (default-hook parity) and is not recorded. Each recorder
# chains to the hook it wrapped, so the usual traceback still reaches stderr after the marker line.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    import threading

    previous_unraisable = sys.unraisablehook
    previous_thread = threading.excepthook

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

    def _record_thread(args):
        if args.exc_type is not None and issubclass(args.exc_type, SystemExit):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    threading.excepthook = _record_thread


def _gate_exit(code):
    """Settle and END the process: flush both streams, force a recorded background fault to exit 2 over a
    passing verdict, then os._exit, so no atexit callback registered by loaded code runs after the verdict
    (the gate's own cleanup runs inside its work; this gate registers no atexit work of its own)."""
    import os
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
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
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through this gate's REAL entry in a child: the probe runs
# this file with run_name "__main__" and wraps runpy.run_path so the gate's own validator load is followed
# by a load of one fixture, the way a validator fault would arrive. An atexit callback registered by
# loaded code never runs (unreachable behind os._exit); a worker-thread fault and a destructor fault are
# recorded by the hooks installed before the load and force exit 2 over the passing verdict.
_FAULT_CHANNEL_PROBE = """import runpy, sys
gate, fixture = sys.argv[1:3]
real = runpy.run_path
def hooked(path, *args, **kwargs):
    loaded = real(path, *args, **kwargs)
    real(fixture)
    return loaded
runpy.run_path = hooked
real(gate, run_name="__main__")
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
)


def _self_test_fault_channels():
    """Each fault-channel case must end the child as required: the expected exit code, the fault marker
    exactly when a fault must be recorded, and for the atexit case no trace of the callback (it must never
    run). Red without the entry wrapper: the gate then ends 0 for every channel (the atexit child with the
    callback's trace on stderr, the thread and destructor children with the default hooks' output alone),
    where the atexit case requires a clean stderr and the other two require exit 2 with the marker."""
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
    _expect(not failures, "fault-channel vectors: " + "; ".join(failures))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _fail_closed_main(_self_test)
    _fail_closed_main(_checks)
