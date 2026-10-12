#!/usr/bin/env python3
"""Entry-guard gate (entry-guard): every Python script the local check roster launches, and every runner the
self-test execution manifest registers, keeps its canonical module entry.

A script that loses its `if __name__ == "__main__":` block (a block splice that runs past its intended end, a
bad merge) still exits 0 when launched, with no output: its checks are defined and never called, and a runner
that judges a gate by exit code alone reads that as a pass. This gate holds the entry in place statically, so
the loss is a finding before any run is trusted.

Surface: every Python script tools/run_all_checks.sh launches, read through the parity gate's validated roster
loader (check_ci_parity.read_runner_text and extract_local, never a second shell parser), plus every runner in
tools/selftest_checks.toml, whose schema is validated by the execution gate's own validator
(check_selftest_execution._manifest_rows). Every control input (the roster, the registry and each listed
script) is opened non-blocking without following a final symlink, and is refused unless the opened descriptor
is a regular file, so a FIFO, device, directory or symlink is cannot-evaluate and never blocks the run.

This file is on its own surface, but no gate can hold its own entry: with its guard lost, both of its steps
exit 0 with no output, because nothing in the file runs. Its own entry is therefore held by an independent,
existing mechanism. Its self-test is registered in tools/selftest_checks.toml (suite entry-guard-selftest), and
both rosters launch it only through check_selftest_execution.py --suite, which requires the child to write an
execution report naming every registered check; a child that runs nothing writes none, and that gate reads a
missing report as cannot-evaluate (exit 2). The self-test reproduces this on a scratch copy of this file with
its guard deleted, and launches this file's live step as a child and requires its PASS line, so a live path
that exits 0 without checking fails the self-test, and with it the execution gate.

Invariant, per script: the file parses (from its raw bytes, so a byte-order mark or a coding declaration is
honoured as Python honours it), its top-level body holds exactly one `if` statement whose test is
`__name__ == "__main__"`, that statement is the last top-level statement, has no else (or elif), its body ends
in a call statement (an expression statement whose value is a call, or `raise SystemExit(<call>)`) with no
`raise` statement anywhere before it in that body (at any depth: inside an if, a try, a with or a loop, and
conservatively inside a definition too), and no such `if` appears below the top level. The test's left side
must be the bare name `__name__` and its right side the exact string `"__main__"`; only that orientation is
recognised, so a reversed `"__main__" == __name__`, a `!=` test, a misspelled string or another name on the
left (`__file__`) reads as a missing guard. A call inside a definition, a lambda, a nested branch or a try in
the guard body is not a call statement and fails.

Intent is carried by the declared surface: a deliberate removal (a script turned into a library module)
removes its roster line or registry row in the same reviewed change. A guard lost while the script is still
launched or registered is a finding. The gate does not read a diff.

  check_entry_guard.py              check the repository
  check_entry_guard.py --self-test  assert the gate's own vectors, and that each vector is discriminating
  check_entry_guard.py [--self-test] --execution-report ABS_PATH
                                    the self-test, also writing its execution report, finalized at interpreter
                                    exit (tools/_selftest_exit_report.py), as the execution gate runs it
Exit: 0 every script passes; 1 findings, each failing script named with one reason; 2 cannot evaluate (the
roster or registry is missing, not a regular file, unreadable, malformed or schema-invalid, the roster loader
reports a diagnostic, the roster launches no Python script, or a listed script is missing, not a regular file,
unreadable, or does not parse). An unparsable script is never read as passing. There is no warning tier.
The self-test exits 0 when every check passes, 1 when any fails, and 2 (cannot evaluate) when none fails but a
gate-runner vector was not exercised in any of its ATTEMPTS runs (host scheduling let the bound overtake each run,
widened a timer read's bracket past TIMER_BRACKET, or made a run whose cleanup is proven overrun its elapsed
bound); such a run is never read as a pass or as a failure. In the mutation aggregation a fixture gate run that
GATE_TIMEOUT (3 s) overtakes is re-run once at the larger BLOCK_CONFIRM (10 s) bound, so a scheduling stall (which
completes with more time) is told from a run the mutation left unbounded: the stall's completed result decides and
it earns no exposure merely by hanging, while a run that still does not complete is a deterministic block (a
control input opened without O_NONBLOCK blocks on a FIFO) and is a genuine exposure. Host scheduling can still
fail a clean run where a bound has no such reading: a stall past BLOCK_CONFIRM in the gate run of a fixture vector
(read as a block); one of HANG_SECONDS (60 s) between a witnessed run's deadline and its kill, after which the run
reports WOKE; a holder of a witness that takes WITNESS_WAIT (5 s) after its run to die; the outer 2 * WITNESS_WAIT
(10 s) bound of the parent-death vector (a stall of WITNESS_WAIT in its stand-in reads cannot-evaluate, not a
failure); and the CHILD_TIMEOUT (60 s) bounds of the timer vector, the unarmed and audited descendant legs, the
window sweeps, the _launch of each run/* and own-entry/* leg, and the live legs.

The self-test requires Linux: its gate runner and witnesses use os.waitid with WNOWAIT, fcntl.F_SETSIG,
os.pidfd_open and /proc. Elsewhere it never passes (the parent-death check fails without F_SETSIG, and a missing
call raises).

It proves only that the entry exists in that form and that its final call statement is reached when nothing
before it leaves the module. A static check cannot see a neutralization that keeps that form: the call
statement calling something that does not run the checks or that exits with a constant status (sys.exit(0),
print(), a bare main() whose returned failure status is dropped); an earlier statement in the guard body that
exits or raises through a call (sys.exit, os._exit, a function that raises), rebinds the called name
(main = lambda: 0) or replaces the exit function (sys.exit = print); a top-level statement before the guard
that exits, raises or never returns (raise SystemExit(0), sys.exit, os._exit, an endless loop), rebinds
__name__, rebinds or shadows the called name (an assignment, a later def, an import, a module __getattr__), or
registers a hook that overrides the exit status (atexit.register(os._exit, 0), a signal handler,
sys.excepthook); an import whose side effect exits, replaces sys.exit or replaces a builtin; a dispatch or
check deleted inside a function the entry calls, which is caught for a registered suite only by the execution
gate; and any other statement that changes control flow or the exit status before or during the final call.
This file's own entry is held by the execution gate only while its registry row and both --suite roster lines
stay; a coordinated removal of them is outside it. A symlinked parent directory is followed (only the final
path component is opened without following). It does not read the CI workflow (the parity gate holds the two
rosters equal), does not see a script the roster never launches, and cannot tell a legitimate from an
illegitimate coordinated removal of an entry and its roster line in one reviewed change. The self-test proves
each vector discriminating against its own table of single-check removal mutants, not against every possible
regression, and the execution gate accounts for its checks by family (every vector, every mutant, each launch
leg), not per vector.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_entry_guard.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import collections
import contextlib
import ctypes
import dis
import errno
import fcntl
import importlib.util
import io
import itertools
import json
import operator
import os
import select
import signal
import stat
import subprocess
import tempfile
import time
import tomllib
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import check_ci_parity  # noqa: E402  (the validated roster loader)
import check_selftest_execution  # noqa: E402  (the authoritative registry schema)

ROOT = Path(__file__).resolve().parents[1]
ROSTER_REL = check_ci_parity.LOCAL_SOURCE
REGISTRY_REL = "tools/selftest_checks.toml"
# This gate's own self-test suite in the registry: the execution gate holds this file's entry.
SUITE_ID = "entry-guard-selftest"
# Non-blocking, so a FIFO opens at once and is then refused by type; no final-symlink follow, so a symlink
# is refused at open (ELOOP); close-on-exec, so no launched child inherits the descriptor.
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
READ_CHUNK = 1 << 16
# The live leg of the self-test requires at least this many scripts on the real surface; lowering it is a
# reviewed change, so a roster that silently shrinks is seen.
LIVE_FLOOR = 107
CHILD_TIMEOUT = 60
# A self-test gate run on a fixture tree takes milliseconds; one still running after this is a hang (the
# live leg over the real surface is allowed CHILD_TIMEOUT).
GATE_TIMEOUT = 3

# The descriptor primitives, named so the self-test can refuse an open or a read without filesystem
# permissions (the self-test may run as root).
_open = os.open
_read = os.read


class CannotEvaluate(Exception):
    """An input the gate cannot read or parse; the run exits 2."""


def _read_regular(path, label):
    """Read one control input as bytes. It is opened non-blocking without following a final symlink, and
    refused unless the opened descriptor is a regular file; every failure raises CannotEvaluate."""
    try:
        fd = _open(str(path), OPEN_FLAGS)
    except FileNotFoundError:
        raise CannotEvaluate("{}: missing".format(label))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CannotEvaluate("{}: is a symlink (refused)".format(label))
        raise CannotEvaluate("{}: cannot open ({})".format(label, type(exc).__name__))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CannotEvaluate("{}: not a regular file".format(label))
        chunks = []
        while True:
            chunk = _read(fd, READ_CHUNK)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(label, type(exc).__name__))
    finally:
        os.close(fd)


def roster_scripts(root):
    """Return the set of Python scripts the roster launches, as repository-relative paths."""
    text, diagnostic = check_ci_parity.read_runner_text(
        Path(root) / ROSTER_REL, ROSTER_REL, reader=lambda path: _read_regular(path, ROSTER_REL))
    if diagnostic is not None:
        raise CannotEvaluate("{}: {}".format(ROSTER_REL, diagnostic.message))
    extraction = check_ci_parity.extract_local(text)
    if extraction.diagnostics:
        first = extraction.diagnostics[0]
        raise CannotEvaluate("{}:{}: {} ({} roster diagnostic(s))".format(
            ROSTER_REL, first.line, first.message, len(extraction.diagnostics)))
    scripts = set()
    for member in extraction.members:
        target = member.split(" ", 1)[0]
        if target.endswith(".py"):
            scripts.add(target)
        elif target != "gitleaks":
            raise CannotEvaluate("{}: unrecognised launch target {!r}".format(ROSTER_REL, target))
    if not scripts:
        raise CannotEvaluate("{}: the roster launches no Python script".format(ROSTER_REL))
    return scripts


def registry_runners(root):
    """Return the set of runners the self-test execution manifest registers, after the execution gate's own
    schema validation; a schema-invalid manifest cannot be evaluated."""
    data = _read_regular(Path(root) / REGISTRY_REL, REGISTRY_REL)
    try:
        manifest = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("{}: malformed ({})".format(REGISTRY_REL, type(exc).__name__))
    violation = io.StringIO()
    with contextlib.redirect_stderr(violation):
        rows = check_selftest_execution._manifest_rows(manifest, REGISTRY_REL)
    if rows is None:
        raise CannotEvaluate("schema-invalid ({})".format(" ".join(violation.getvalue().split())))
    runners = set()
    for number, row in enumerate(rows, 1):
        runner = row["runner"]
        if not check_ci_parity.PY_TARGET_RE.fullmatch(runner):
            raise CannotEvaluate("{}: [[suite]] #{} runner {!r} is not a literal tools/*.py or "
                                 "opf/tools/*.py path".format(REGISTRY_REL, number, runner))
        runners.add(runner)
    return runners


def parse_script(root, rel):
    """Parse one listed script from its raw bytes; anything that cannot be read and parsed raises
    CannotEvaluate."""
    data = _read_regular(Path(root) / rel, rel)
    try:
        return ast.parse(data, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({})".format(rel, type(exc).__name__))


def _is_guard(node):
    test = node.test if isinstance(node, ast.If) else None
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and type(test.comparators[0].value) is str and test.comparators[0].value == "__main__")


def _call_statement(node):
    """True for an expression statement whose value is a call, or `raise SystemExit(<call>)`."""
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Call)
    if isinstance(node, ast.Raise):
        exc = node.exc
        return (node.cause is None and isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit"
                and len(exc.args) == 1 and not exc.keywords
                and isinstance(exc.args[0], ast.Call))
    return False


def entry_finding(tree):
    """Return the one reason a parsed module fails the entry invariant, or None."""
    body = tree.body
    guards = [index for index, node in enumerate(body) if _is_guard(node)]
    nested = any(_is_guard(inner) for node in body for inner in ast.walk(node) if inner is not node)
    if not guards:
        return "guard nested below the top level" if nested else "guard missing"
    if len(guards) > 1:
        return "more than one guard"
    guard = body[guards[0]]
    if guards[0] != len(body) - 1:
        return "guard not the last statement"
    if guard.orelse:
        return "guard has an else"
    if not _call_statement(guard.body[-1]):
        return "guard body does not end in a call statement"
    if any(isinstance(node, ast.Raise) for statement in guard.body[:-1] for node in ast.walk(statement)):
        return "guard call statement unreachable after a raise"
    if nested:
        return "guard nested below the top level"
    return None


def evaluate(root):
    """Return (checked_count, findings); raises CannotEvaluate naming every input it could not read."""
    scripts = roster_scripts(root) | registry_runners(root)
    trees, problems = dict(), []
    for rel in sorted(scripts):
        try:
            trees[rel] = parse_script(root, rel)
        except CannotEvaluate as exc:
            problems.append(str(exc))
    if problems:
        raise CannotEvaluate("; ".join(problems))
    findings = []
    for rel, tree in trees.items():
        reason = entry_finding(tree)
        if reason is not None:
            findings.append("{}: {}".format(rel, reason))
    return len(trees), findings


def run(root):
    try:
        count, findings = evaluate(Path(root))
    except CannotEvaluate as exc:
        print("error: cannot evaluate entry guards ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: entry-guard: {} launched script(s) without the canonical entry".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: entry-guard checked {} launched scripts".format(count))
    return 0


# ---------------------------------------------------------------------------------------------- self-test

INTACT = """import sys


class First:
    def run(self):
        return 0


class Second:
    def run(self):
        print("checks ran")
        return 0


def main():
    return First().run() + Second().run()


if __name__ == "__main__":
    sys.exit(main())
"""
GUARD = 'if __name__ == "__main__":\n    sys.exit(main())\n'
REPLACEMENT = """class Second:
    def run(self):
        print("checks ran, revised")
        return 0
"""
TARGET, REGISTERED = "tools/target.py", "tools/registered.py"


def _indented(text):
    return "".join("    " + line + "\n" for line in text.splitlines())


def _guarded(body):
    """INTACT with its guard body replaced; body is the guard body's lines, unindented."""
    return INTACT.replace(GUARD, 'if __name__ == "__main__":\n' + _indented(body))


NO_CALL = "guard body does not end in a call statement"
UNREACHABLE = "guard call statement unreachable after a raise"
# (vector, target script text, reason) for a target the roster launches: each is one edit from INTACT.
SHAPES = (
    ("late-statement", INTACT + "\nprint('late')\n", "guard not the last statement"),
    ("two-guards", INTACT + "\n\n" + GUARD, "more than one guard"),
    ("guard-else", INTACT + "else:\n    pass\n", "guard has an else"),
    ("not-equal", INTACT.replace('__name__ == "__main__"', '__name__ != "__main__"'), "guard missing"),
    ("guard-pass", _guarded("pass"), NO_CALL),
    ("guard-bare-name", _guarded("main"), NO_CALL),
    ("guard-def", _guarded("def entry():\n    sys.exit(main())"), NO_CALL),
    ("guard-if-false", _guarded("if False:\n    sys.exit(main())"), NO_CALL),
    ("guard-lambda", _guarded("(lambda: sys.exit(main()))"), NO_CALL),
    ("guard-assign-lambda", _guarded("entry = lambda: sys.exit(main())"), NO_CALL),
    ("guard-try", _guarded("try:\n    sys.exit(main())\nexcept SystemExit:\n    pass"), NO_CALL),
    ("guard-raise-constant", _guarded("raise SystemExit(0)"), NO_CALL),
    ("guard-raise-other", _guarded("raise ValueError(main())"), NO_CALL),
    ("guard-raise-first", _guarded("raise SystemExit(0)\nsys.exit(main())"), UNREACHABLE),
    ("guard-raise-nested-if", _guarded("if True:\n    raise SystemExit(0)\nsys.exit(main())"), UNREACHABLE),
    ("guard-raise-nested-try", _guarded("try:\n    raise SystemExit(0)\nfinally:\n    pass\nsys.exit(main())"),
     UNREACHABLE),
    ("name-lhs-file", INTACT.replace('if __name__ == "__main__"', 'if __file__ == "__main__"'), "guard missing"),
    ("main-literal-short", INTACT.replace('__name__ == "__main__"', '__name__ == "main"'), "guard missing"),
    ("main-literal-transposed", INTACT.replace('__name__ == "__main__"', '__name__ == "__mian__"'),
     "guard missing"),
    ("nested-if", INTACT.replace(GUARD, "if True:\n" + _indented(GUARD)), "guard nested below the top level"),
    ("nested-deep", INTACT.replace(GUARD, "if True:\n" + _indented("if True:\n" + _indented(GUARD))),
     "guard nested below the top level"),
    ("nested-def", INTACT.replace(GUARD, "def entry():\n" + _indented(GUARD)),
     "guard nested below the top level"),
    ("nested-plus-valid", INTACT.replace("def main():", "def helper():\n" + _indented(GUARD.replace(
        "sys.exit(main())", "pass")) + "\n\ndef main():"), "guard nested below the top level"),
)
# (vector, target script bytes): scripts Python runs, which must pass.
ACCEPTED = (
    ("raise-call", _guarded("raise SystemExit(main())").encode("utf-8")),
    ("byte-order-mark", b"\xef\xbb\xbf" + INTACT.encode("utf-8")),
    ("coding-latin-1", b"# -*- coding: latin-1 -*-\n# caf\xe9\n" + INTACT.encode("utf-8")),
)


def splice(text, start_line, replacement):
    """Replace from the line equal to start_line up to the next line that starts with `class`, or to the end
    of the file when there is none: the block splice whose overrun this gate exists to catch."""
    lines = text.splitlines(True)
    start = lines.index(start_line)
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("class")),
               len(lines))
    return "".join(lines[:start] + [replacement] + lines[end:])


def _roster_text(entries, extra=()):
    """The real roster with its gate lines replaced by fixture launches, so the loader's grammar holds."""
    lines = (ROOT / ROSTER_REL).read_text(encoding="ascii").splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith('run_gate "'))
    kept = [line for line in lines if not line.startswith('run_gate "')]
    launches = ['run_gate "fixture-{}" python3 -I -B {}'.format(number, rel)
                for number, rel in enumerate(entries, 1)]
    return "\n".join(kept[:first] + launches + list(extra) + kept[first:]) + "\n"


def _registry_text(runners):
    rows = "".join('\n[[suite]]\nid = "fixture-{}"\nrunner = "{}"\nexpected-check-ids = ["fixture-check"]\n'
                   .format(number, rel) for number, rel in enumerate(runners, 1))
    return "format-version = 1\n" + rows


def _tree(base, name, scripts, roster=(TARGET,), registry=(REGISTERED,), registry_text=None,
          roster_text=None):
    """Write a synthetic repository; scripts is a sequence of (relative path, text or bytes) pairs."""
    root = Path(base) / name
    (root / "tools").mkdir(parents=True)
    for rel, text in scripts:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    (root / ROSTER_REL).write_bytes((_roster_text(roster) if roster_text is None else roster_text)
                                    .encode("utf-8"))
    (root / REGISTRY_REL).write_bytes((_registry_text(registry) if registry_text is None else registry_text)
                                      .encode("utf-8"))
    return root


def _replace_with(root, rel, kind):
    """Replace one input by a symlink to a regular copy of it, a FIFO, or a directory, or delete it."""
    path = root / rel
    original = path.read_bytes()
    path.unlink()
    if kind == "symlink":
        real = path.with_name("real-" + path.name)
        real.write_bytes(original)
        path.symlink_to(real.name)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()


def _child_leg(thunk, fds):
    """The forked child of _in_child, given its four pipe ends (report write, report read, lifeline write, lifeline
    read): run thunk and write its JSON report to the report pipe. Before thunk runs, the child leads a new process
    group, so the group kill of _in_child reaches every descendant that stays in the group, and, where the platform
    has F_SETSIG (Linux), it arms the lifeline: the kernel sends SIGKILL to that group when the last copy of the
    lifeline write end closes while a copy of its read end is still open, as when the parent dies, however it dies,
    or when the cleanup of _in_child closes it (a copy held by a process another thread of the caller forked defers
    both; see _in_child). Every path, a BaseException included, ends in os._exit, here or, for anything raised
    before this function's try, in the finally of _in_child, so no finally block, cleanup or interpreter-exit
    hook of the caller runs in the child."""
    status = 1
    try:
        report_w, report_r, lifeline_w, lifeline_r = fds
        os.setpgid(0, 0)
        if hasattr(fcntl, "F_SETSIG"):
            fcntl.fcntl(lifeline_r, fcntl.F_SETOWN, -os.getpid())
            fcntl.fcntl(lifeline_r, fcntl.F_SETSIG, signal.SIGKILL)
            fcntl.fcntl(lifeline_r, fcntl.F_SETFL, fcntl.fcntl(lifeline_r, fcntl.F_GETFL) | os.O_ASYNC)
        os.close(report_r)
        os.close(lifeline_w)
        try:
            report = [True, thunk()]
        except BaseException as exc:
            report = [False, "crashed ({})".format(type(exc).__name__)]
        data = json.dumps(report, default=repr).encode("utf-8")
        while data:
            data = data[os.write(report_w, data):]
        status = 0
    finally:
        os._exit(status)


def _in_child(thunk, timeout, forked=None):
    """Run thunk() in a forked child (_child_leg); returns (True, its result through JSON) or (False, "hung" or
    "crashed (...)"). The bound is a monotonic deadline this parent owns. forked, if given, must be an empty list:
    the child's pid is recorded in it as the fork returns, so the caller knows the child whether or not thunk ever
    runs. However this function is left (a result, the deadline, or any exception), its finally sends SIGKILL to
    the child's process group and to the child, closes every pipe end, each write end before its read end (so this
    process still holds the lifeline read end when it closes the write end, and the lifeline fires), and reaps the
    child.

    No exception can come between the fork and that cleanup, in either process. A signal handler of the caller
    runs, and may raise, at a check between bytecodes or inside a call that waits and is interrupted. The pipes
    and the fork are made inside the try, each inside one C-level iteration that also records its result (fds,
    forked) and makes no such wait, so the finally holds every descriptor and the child whenever the call that
    made them returned. In the child, that finally calls os._exit before any bytecode at which a handler can
    run: from the return of os.fork on, nothing raised in the child unwinds into the caller's frames. (Inside
    os.fork, as with any fork, the hooks registered with os.register_at_fork run in the child; CPython reports
    an exception raised there as unraisable and does not propagate it.) The cleanup sends SIGKILL before any
    such bytecode, and each later step sits in its own finally, so a handler that raises again during the
    cleanup cannot skip a step; it can only interrupt the blocking reap, which leaves the killed child a zombie
    until this process reaps or exits. An audit hook of the caller (sys.addaudithook) is the exception: os.killpg
    and os.kill each call it before they signal, so it, or a signal handler that runs inside it, can raise there
    and skip that kill (the other kill still runs, in its own finally). On Linux the group is killed even then:
    the close of the lifeline write end, which calls no audit hook, makes the kernel send SIGKILL to the group,
    and a child that arms the lifeline only after that close kills its own group when it closes its own copy of
    the write end, before thunk runs. Without F_SETSIG, a hook that raises at os.killpg leaves every descendant
    running. The child is reaped only in the cleanup (the wait before it reads with WNOWAIT), so every kill
    reaches this process's own child, never a reused pid, unless the caller takes the child first: with SIGCHLD
    set to SIG_IGN, or a SIGCHLD handler that reaps, this raises ChildProcessError or ProcessLookupError, or reads
    hung; it never reads as a pass, and a kill may then reach a reused pid.

    Bounds and limits: a descendant that leaves the child's process group (setpgid, setsid) escapes both the
    group kill and the lifeline (the child itself, if it leaves, is still killed by its pid). Without F_SETSIG,
    or once the child and every descendant have closed the lifeline read end, a parent killed before its cleanup
    leaves the group running. A process that another thread of the caller forks at any point from the making of
    the pipes until the cleanup closes them holds copies of their ends (the pipes are close-on-exec, not
    close-on-fork) until it execs or exits. Until then neither the parent's death nor the cleanup's close fires
    the lifeline, so a parent killed before its cleanup leaves the group running, and in the cleanup only
    os.killpg reaches the group; and if that fork came before this process closed its report write end, the
    report reads no EOF, so even a run that passed reads hung (never a pass). A child that left its group, under
    an audit hook that refuses os.kill, is reached by neither kill nor the lifeline, so the reap waits until it
    exits. A child in uninterruptible sleep holds up the reap until it wakes. A fork copies only the calling
    thread: a lock another thread of the caller holds at the fork stays held in the child, which then reads as
    hung.

    Nothing here reads, arms or restores a process timer, a signal handler or the signal mask, so the
    ITIMER_REAL of a caller, its interval, its SIGALRM handler and its mask are untouched (the rule
    elapsed-aware-timer-restore: a mechanism that is not process-global, rather than a borrow); a forked
    child inherits no interval timer."""
    deadline = time.monotonic() + timeout
    forked = [] if forked is None else forked
    if forked:
        raise ValueError("_in_child records the child pid in an empty list")
    fds, chunks, status, hung = [], [], None, False
    closing = map(os.close, fds)  # consumed only by the finally, so it closes whatever fds then holds
    try:
        # Each pipe is recorded write end first, so the cleanup closes the lifeline write end before its read end.
        fds.extend(itertools.chain.from_iterable(map(reversed, itertools.starmap(os.pipe, ((), ())))))
        forked.extend(itertools.starmap(os.fork, ((),)))
        if forked == [0]:
            _child_leg(thunk, fds)
        report_w, report_r, _lifeline_w, _lifeline_r = fds
        # The parent drops its report write end, so the report reads EOF once the child (and every
        # descendant holding a copy) has closed it; forgotten and closed in one C-level iteration as well.
        collections.deque(itertools.starmap(operator.call, ((fds.remove, report_w), (os.close, report_w))), 0)
        poller = select.poll()
        poller.register(report_r, select.POLLIN)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                hung = True
                break
            if not poller.poll(int(remaining * 1000) + 1):
                continue
            chunk = os.read(report_r, READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        while not hung and os.waitid(os.P_PID, forked[0], os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            if time.monotonic() >= deadline:
                hung = True
            else:
                time.sleep(0.001)
    finally:
        if forked == [0]:
            os._exit(1)  # the child's copy of this frame: whatever raised in it, it never unwinds into the caller
        try:
            try:
                if forked:
                    os.killpg(forked[0], signal.SIGKILL)
            except ProcessLookupError:
                pass  # no group yet: the child makes it before thunk runs, so it has started nothing
            finally:
                if forked:
                    os.kill(forked[0], signal.SIGKILL)
        finally:
            try:
                collections.deque(closing, 0)
            finally:
                if forked:
                    status = os.waitpid(forked[0], 0)[1]
    if hung:
        return False, "hung"
    if not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
        return False, "crashed (child wait status {})".format(status)
    try:
        done, result = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return False, "crashed (unreadable child report)"
    return done, result


def _gate(module, root, timeout=GATE_TIMEOUT, forked=None):
    """Run module.run(root) in a bounded child (_in_child, which records the child's pid in forked, if given), so
    every patch and mutant in place at the call is in force there; returns (exit code, or "hung" or
    "crashed (...)", combined output)."""
    def gated():
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = module.run(root)
        except Exception as exc:
            code = "crashed ({})".format(type(exc).__name__)
        return [code, out.getvalue() + err.getvalue()]
    done, result = _in_child(gated, timeout, forked)
    return (result[0], result[1]) if done else (result, "")


# The caller timer the timer vector arms in its own child: (countdown, interval) in seconds, far beyond the run.
CALLER_TIMER = (600.0, 300.0)
# How far apart the two expiry instants the timer vector reads may lie for one timer (a margin over the
# microsecond truncation of the timer reads), and the widest the monotonic bracket of either read may be for the
# vector to judge it. A change to the timer during the run moves its expiry instant, and the predicate sees only
# a move larger than TIMER_FLOOR plus the parts of the two brackets no stamp observes, so a move of up to
# TIMER_FLOOR + 2 * TIMER_BRACKET (0.101 s) can read as kept; a wider bracket hides more, so that observation
# cannot evaluate.
TIMER_FLOOR = 0.001
TIMER_BRACKET = 0.05
# How long the timer vector waits between arming its timer and reading it first, so a reset to the starting
# countdown moves the expiry instant by more than TIMER_FLOOR and two brackets can hide.
TIMER_SETTLE = 0.2
# How long the gate run of the timer vector lasts at least, so a timer restored at the end of the run to the
# countdown it held at the start (a borrow that drops its elapsed time) moves its expiry instant that much later.
TIMER_RUN_SECONDS = 0.3
# The witness vectors: the bound of their gate runs, how long a run (or a descendant it forks) sleeps when
# nothing kills it, and how long they wait after the gate returns for every holder of the witness pipe to die.
WITNESS_TIMEOUT = 0.5
HANG_SECONDS = 60
WITNESS_WAIT = 5.0
# The role a witnessed run (or a descendant it forks) reports once its sleep is over: no kill reached it in time.
WOKE = "woke"
# The bound of the overrun vector, how long its gate run sleeps with SIGALRM ignored before reporting WOKE and
# printing a pass, and the elapsed time past which its run cannot evaluate.
OVERRUN_TIMEOUT = 0.5
OVERRUN_SECONDS = HANG_SECONDS
OVERRUN_BOUND = 2.0
# How many runs a witness or timer vector makes while every run shows no failure but cannot be evaluated (host
# scheduling let its bound overtake the run, widened a timer read's bracket, or made a run whose cleanup is proven
# overrun its elapsed bound), before it reports cannot-evaluate.
ATTEMPTS = 3
# The window sweeps: the bound of each gate run, the most injection points a sweep may take, and the fewest it
# must take (so a sweep that never reached the window cannot pass).
SWEEP_TIMEOUT = 2.0
SWEEP_LIMIT = 2000
SWEEP_FLOOR = 20
SWEEP_RESULT = "swept"
DESCRIPTOR_DIR = "/dev/fd"


def _caller_alarm(signum, frame):
    """The SIGALRM handler of the caller in the timer vector; its timer never comes due during the run."""


def _paced_run(module):
    """The gate run of the timer vector: module.run, after TIMER_RUN_SECONDS."""
    def run(root):
        time.sleep(TIMER_RUN_SECONDS)
        return module.run(root)
    return run


def _caller_timer_probe(module, root):
    """Arm a caller timer with an interval and a handler, run root through the _gate of module (paced), and
    report the timer, handler and mask after it, with stamps: the monotonic time before and after the timer read
    before the run, and before and after the read after it. The first read comes TIMER_SETTLE after the timer is
    armed. Run only inside _in_child, so the timer it arms is its own."""
    signal.signal(signal.SIGALRM, _caller_alarm)
    signal.setitimer(signal.ITIMER_REAL, *CALLER_TIMER)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    time.sleep(TIMER_SETTLE)
    stamps = [time.monotonic()]
    before = signal.getitimer(signal.ITIMER_REAL)
    stamps.append(time.monotonic())
    code, _output = module._gate(types.SimpleNamespace(run=_paced_run(module)), root)
    stamps.append(time.monotonic())
    after = signal.getitimer(signal.ITIMER_REAL)
    stamps.append(time.monotonic())
    return dict(code=code, before=before, after=after, stamps=stamps,
                handler=signal.getsignal(signal.SIGALRM) is _caller_alarm,
                mask=signal.pthread_sigmask(signal.SIG_BLOCK, ()) == mask)


def _timer_kept(probe):
    """Whether the caller timer of the probe survived its gate run, to the precision its stamps give. False when
    the run neither passed nor was overtaken by its bound (hung), lasted less than TIMER_RUN_SECONDS between the
    reads, the timer lost its interval, its handler or its mask, the first read shows it counted down less than
    TIMER_SETTLE - TIMER_FLOOR since it was armed (so it was reset or rewound before that read), or it came due at
    another instant after the run than before it; None (cannot evaluate) when none of that shows but the run read
    hung or a read's bracket is wider than TIMER_BRACKET; else True. A read made between stamps s and t that finds
    the timer due in r places its expiry instant in [s + r, t + r] (ITIMER_REAL and time.monotonic both count
    CLOCK_MONOTONIC). An untouched timer has one expiry instant, so the two intervals meet, within TIMER_FLOOR,
    however long a delay falls anywhere between the stamps. A change to the timer during the run moves that
    instant, later (a reset) or earlier (a rewind), and the intervals part, so the change is refused, only when the
    move is larger than TIMER_FLOOR plus how far each read lies from its bracket's edge on the side of the move,
    which no stamp observes. A move of up to TIMER_FLOOR + 2 * TIMER_BRACKET (0.101 s), plus a sub-microsecond term
    from the truncation of each timer read to the microsecond, can therefore read as kept, and a move larger than
    that never does. A reset to the starting countdown moves the instant by more than TIMER_SETTLE - TIMER_FLOOR
    (the probe waits TIMER_SETTLE between arming the timer and reading it), so it never reads as kept."""
    (before, _interval), (after, interval) = probe["before"], probe["after"]
    first, ran, done, last = probe["stamps"]
    if not (probe["code"] in (0, "hung") and interval == CALLER_TIMER[1] and probe["handler"] and probe["mask"]
            and done - ran >= TIMER_RUN_SECONDS and CALLER_TIMER[0] - before >= TIMER_SETTLE - TIMER_FLOOR
            and done + after - TIMER_FLOOR <= ran + before and first + before - TIMER_FLOOR <= last + after):
        return False
    return True if probe["code"] == 0 and max(ran - first, last - done) <= TIMER_BRACKET else None


def _witness_line(witness, role):
    """Write role and this process's pid to the witness pipe, one line."""
    os.write(witness, "{} {}\n".format(role, os.getpid()).encode("ascii"))


def _overrunning_run(witness):
    """A gate run that ignores SIGALRM, reports its pid on the witness pipe and overruns its bound, then reports
    WOKE and prints a pass: a hang no in-process alarm can interrupt."""
    def run(root):
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        _witness_line(witness, "child")
        time.sleep(OVERRUN_SECONDS)
        _witness_line(witness, WOKE)
        print("PASS: entry-guard checked 2 launched scripts")
        return 0
    return run


def _hanging_run(witness):
    """A gate run that reports its pid on the witness pipe and sleeps past any bound, then reports WOKE."""
    def run(root):
        _witness_line(witness, "child")
        time.sleep(HANG_SECONDS)
        _witness_line(witness, WOKE)
        return 0
    return run


def _forking_run(witness, hang):
    """A gate run that forks a descendant, which reports its pid on the witness pipe, closes every inherited
    descriptor but stdio and the witness (the report pipe included) and sleeps past any bound; once it has
    closed them, the run reports its own pid and then sleeps too (hang) or returns 0 at once. Each that sleeps
    reports WOKE once its sleep is over."""
    def run(root):
        ready_r, ready_w = os.pipe()
        if os.fork() == 0:
            try:
                _witness_line(witness, "descendant")
                os.closerange(3, witness)
                os.closerange(witness + 1, os.sysconf("SC_OPEN_MAX"))
                time.sleep(HANG_SECONDS)
                _witness_line(witness, WOKE)
            finally:
                os._exit(0)
        os.close(ready_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        _witness_line(witness, "child")
        if hang:
            time.sleep(HANG_SECONDS)
            _witness_line(witness, WOKE)
        return 0
    return run


def _stand_in_killing_run(witness, stand_in):
    """A gate run that reports its pid on the witness pipe, sends SIGKILL to stand_in (the process whose gate
    runs it), and sleeps past any bound, then reports WOKE."""
    def run(root):
        _witness_line(witness, "child")
        os.kill(stand_in, signal.SIGKILL)
        time.sleep(HANG_SECONDS)
        _witness_line(witness, WOKE)
        return 0
    return run


def _stand_in(witness):
    """The stand-in parent of the parent-death vector, run in its own _in_child: it runs a gate whose run kills
    this stand-in mid-run."""
    return _gate(types.SimpleNamespace(run=_stand_in_killing_run(witness, os.getpid())), ROOT,
                 timeout=WITNESS_WAIT)


def _read_to_eof(fd, wait):
    """(bytes read, True) once fd reads EOF within wait seconds, else (bytes read, False). Past the deadline it
    still takes what the pipe already holds, without waiting, for two reads (one empties the pipe of a writer that
    is gone, the next reads its EOF), so a reader that was itself slow (host scheduling) reads writers that closed
    in time as closed, and a writer still alive as alive."""
    deadline = time.monotonic() + wait
    chunks, poller, late = [], select.poll(), 0
    poller.register(fd, select.POLLIN)
    while True:
        remaining = deadline - time.monotonic()
        late += remaining <= 0
        if late > 2 or not poller.poll(int(remaining * 1000) + 1 if remaining > 0 else 0):
            return b"".join(chunks), False
        chunk = os.read(fd, READ_CHUNK)
        if not chunk:
            return b"".join(chunks), True
        chunks.append(chunk)


def _late_read(closed):
    """_read_to_eof with no time left over a pipe that holds one line, its write end closed first (closed) or
    still open: what a reader that was itself slow reads."""
    read_end, write_end = os.pipe()
    try:
        try:
            os.write(write_end, b"child 1\n")
            late = None if closed else _read_to_eof(read_end, 0)
        finally:
            os.close(write_end)
        return _read_to_eof(read_end, 0) if closed else late
    finally:
        os.close(read_end)


# pidfd_getfd(2) duplicates a descriptor of another process into this one, binding its identity and access mode
# to one open file description; CPython exposes no wrapper, so the syscall is made through ctypes. 438 is the
# asm-generic number, shared by x86-64, arm64 and the other Linux ports this Linux-only self-test runs on.
_SYS_pidfd_getfd = 438
_libc = ctypes.CDLL(None, use_errno=True)


def _pidfd_getfd(pidfd, fd):
    """Duplicate descriptor fd of the process pidfd names into this process (pidfd_getfd); the new descriptor, or
    None when the kernel refuses (no such descriptor, or no permission to reach the process)."""
    dup = _libc.syscall(_SYS_pidfd_getfd, pidfd, int(fd), 0)
    return dup if dup >= 0 else None


def _writer_bound(pidfd, fd, dev, ino):
    """Whether descriptor fd of the process pidfd names is the pipe (dev, ino) held for writing, read from one
    open file description: the descriptor is duplicated into this process through the pidfd (_pidfd_getfd), and the
    duplicate's identity (os.fstat: st_dev and st_ino) and access mode (fcntl F_GETFL: O_WRONLY or O_RDWR) come
    from that same description, so a descriptor swapped after the scan named it cannot be taken for a writer. None
    when the duplicate cannot be taken (pidfd_getfd refused), so the holder cannot be cleared here."""
    dup = _pidfd_getfd(pidfd, fd)
    if dup is None:
        return None
    try:
        info = os.fstat(dup)
        mode = fcntl.fcntl(dup, fcntl.F_GETFL) & os.O_ACCMODE
    finally:
        os.close(dup)
    return info.st_dev == dev and info.st_ino == ino and mode in (os.O_WRONLY, os.O_RDWR)


def _pipe_writer(pidfd, pid, dev, ino):
    """Whether the process pidfd names holds the write end of the pipe (dev, ino), proven by binding: /proc names
    each descriptor that links to the pipe, and each is bound to one open file description through the pidfd
    (_writer_bound), which proves its identity and access mode together. Returns (holds, cleared): holds is True
    when a bound writer is found; cleared is False when a candidate could not be bound (pidfd_getfd refused), so
    the process cannot be cleared of holding the write end and must not be taken for spared."""
    target = "pipe:[{}]".format(ino)
    cleared = True
    with contextlib.suppress(OSError):
        for name in os.listdir("/proc/{}/fd".format(pid)):
            with contextlib.suppress(OSError, ValueError):
                if os.readlink("/proc/{}/fd/{}".format(pid, name)) != target:
                    continue
                bound = _writer_bound(pidfd, name, dev, ino)
                if bound is None:
                    cleared = False
                elif bound:
                    return True, cleared
    return False, cleared


def _pipe_writers(dev, ino):
    """Every process that holds a descriptor linking to the pipe (dev, ino), from a scan of /proc: the candidates
    _kill_holder binds before it signals, found from the kernel's descriptor tables, never from what any process
    reported. Empty when this process's own /proc identity cannot be confirmed (os.readlink('/proc/self') must
    name this process, so a scan whose /proc is a foreign pid namespace resolves no pid) or /proc cannot be read."""
    target = "pipe:[{}]".format(ino)
    try:
        if os.readlink("/proc/self") != str(os.getpid()):
            return []
        names = os.listdir("/proc")
    except OSError:
        return []
    holders = []
    for name in names:
        if not name.isdigit():
            continue
        with contextlib.suppress(OSError):
            for fd in os.listdir("/proc/{}/fd".format(name)):
                with contextlib.suppress(OSError, ValueError):
                    if os.readlink("/proc/{}/fd/{}".format(name, fd)) == target:
                        holders.append(int(name))
                        break
    return holders


def _kill_holder(pid, pipe):
    """Send SIGKILL to pid, and return None, only when it is not this process, is confirmed alive, and is bound to
    hold the write end of pipe. The evidence is read from one open file description: a pidfd opened first names one
    process and never a pid reused since, and each descriptor /proc shows it holding is duplicated through that
    pidfd (_pipe_writer, _writer_bound), so the pipe identity (st_dev and st_ino) and the access mode (O_WRONLY or
    O_RDWR) are read together, never a readlink or an fdinfo flag on its own. A process that holds only the read
    end, or whose descriptor was swapped after the scan, is never a target. No kill is sent, and pid is returned as
    a surviving holder, when the binding is unavailable or refused: no permission for pidfd_getfd, or a scan whose
    /proc is a foreign pid namespace (os.readlink('/proc/self') must name this process, checked before the scan).
    This process is never signalled, whatever pid names."""
    if pid == os.getpid():
        return None
    if os.readlink("/proc/self") != str(os.getpid()):
        return pid
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return None
    try:
        witness = os.fstat(pipe)
        holds, cleared = _pipe_writer(pidfd, pid, witness.st_dev, witness.st_ino)
        if holds and not select.select([pidfd], [], [], 0)[0]:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            return None
        return None if cleared else pid
    finally:
        os.close(pidfd)


def _kill_confirmed():
    """Whether _kill_holder sends no kill to this process (while it still holds the write end of the pipe), to a
    live process that holds only the read end, or to one that holds neither end, and kills a live one that holds
    the write end through the pidfd it opened before it read /proc, with no kill by pid: (verdict, detail)."""
    read_end, write_end = os.pipe()
    sleeper = [sys.executable, "-I", "-c", "import time; time.sleep({})".format(HANG_SECONDS)]
    sent, trace, this = [], [], sys.modules[__name__]
    calls = ((os, "pidfd_open", os.pidfd_open), (this, "_pipe_writer", _pipe_writer),
             (signal, "pidfd_send_signal", signal.pidfd_send_signal))

    def traced(name, call):
        def step(*args):
            result = call(*args)
            trace.append((name, args[0], result))
            return result
        return step
    try:
        with (subprocess.Popen(sleeper) as other, subprocess.Popen(sleeper, pass_fds=(read_end,)) as reading,
              subprocess.Popen(sleeper, pass_fds=(write_end,)) as holder):
            try:
                with _patched(signal, "pidfd_send_signal", lambda pidfd, signum: sent.append(signum)):
                    for pid in (os.getpid(), reading.pid, other.pid):
                        _kill_holder(pid, read_end)
                with contextlib.ExitStack() as stack:
                    for owner, name, call in calls:
                        stack.enter_context(_patched(owner, name, traced(name, call)))
                    stack.enter_context(_patched(os, "kill", lambda *args: trace.append(("kill",) + args)))
                    _kill_holder(holder.pid, read_end)
                status = holder.wait()
            finally:
                other.kill()
                reading.kill()
                holder.kill()
        pidfd = next((result for name, _arg, result in trace if name == "pidfd_open"), None)
        through = trace == [("pidfd_open", holder.pid, pidfd), ("_pipe_writer", pidfd, (True, True)),
                            ("pidfd_send_signal", pidfd, None)]
        return not sent and through and status == -signal.SIGKILL, dict(sent=sent, trace=trace, holder=status)
    finally:
        os.close(read_end)
        os.close(write_end)


def _witnessed(launch, known=(), wait=None):
    """Call launch(witness write end) while this process holds a witness pipe, then close the write end; returns
    (what launch returned, {role: pid} the processes reported, whether every holder of the witness died within
    wait seconds, WITNESS_WAIT by default). If one did not, every process then holding the witness write end is
    killed once confirmed alive and holding it (_kill_holder): each pid in known first (a list launch fills, as
    with the pid of a child it forked), then each holder a scan of /proc finds (_pipe_writers), each bound to one
    file description before it is signalled, so a failing vector leaves behind no writer it could bind at the scan
    (a holder that forks after it can leave its child; a writer whose binding is refused survives, named, and the
    witness read has already read it as live).
    What a process reports on the witness never chooses a target: a report can name any pid, this process's own
    included, and this process, which by then holds only the read end, is never signalled (_kill_holder)."""
    read_end, write_end = os.pipe()
    ended = False
    try:
        try:
            returned = launch(write_end)
        finally:
            os.close(write_end)
        data, ended = _read_to_eof(read_end, WITNESS_WAIT if wait is None else wait)
        pids = dict((role, int(pid)) for role, pid in (line.split() for line in data.decode("ascii").splitlines()))
        return returned, pids, ended
    finally:
        try:
            if not ended:
                witness = os.fstat(read_end)
                for pid in dict.fromkeys(itertools.chain(known, _pipe_writers(witness.st_dev, witness.st_ino))):
                    _kill_holder(pid, read_end)
        finally:
            os.close(read_end)


def _reap_within(pid, wait):
    """The wait status of the child pid of this process once it ends within wait seconds (checked once more past
    the deadline, so a slow check is not read as a live child); else None, and the child is killed and reaped."""
    deadline = time.monotonic() + wait
    while True:
        late = time.monotonic() >= deadline
        reaped, status = os.waitpid(pid, os.WNOHANG)
        if reaped:
            return status
        if late:
            break
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return None


def _owner_spared():
    """Whether the cleanup of _witnessed, given no known pid, kills a child that holds the witness write end and
    reported this process's pid as its own, and aims no kill at this process, which then holds only the read end:
    (verdict, detail). The child has written its report before launch returns, so the verdict does not depend on
    when it was scheduled. Every pidfd kill is recorded by the pid its pidfd names (from /proc), and one aimed at
    this process is recorded and never sent."""
    owner, aimed, sender, forked = os.getpid(), [], signal.pidfd_send_signal, []

    def send(pidfd, signum):
        with open("/proc/self/fdinfo/{}".format(pidfd), encoding="ascii") as info:
            aimed.append(int(next((line.split()[1] for line in info if line.startswith("Pid:")), "0")))
        return None if aimed[-1] == owner else sender(pidfd, signum)

    def launch(witness):
        ready_r, ready_w = os.pipe()
        forked.append(os.fork())
        if forked == [0]:
            try:
                os.write(witness, "child {}\n".format(owner).encode("ascii"))
                os.write(ready_w, b"1")
                time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        os.close(ready_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        return forked[0]
    returned = pids = ended = status = None
    try:
        with _patched(signal, "pidfd_send_signal", send):
            returned, pids, ended = _witnessed(launch, wait=WITNESS_TIMEOUT)
    finally:
        if forked:
            status = _reap_within(forked[0], WITNESS_WAIT)
    killed = status is not None and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    return (pids == {"child": owner} and ended is False and owner not in aimed and returned in aimed and killed,
            dict(owner=owner, child=returned, pids=pids, ended=ended, aimed=aimed, status=status))


def _reuse_bound():
    """Whether witness cleanup binds a holder's write end to one open file description before it signals.
    Case A (descriptor reuse): a child holds only the read end and, the instant the scan has read that
    descriptor's identity, swaps it to a write-only /dev/null; a bound cleanup reads the swapped description,
    which is not the witness, so it signals nothing. Case B (binding refused): with the scan's /proc read as a
    foreign pid namespace (os.readlink('/proc/self') does not name this process), a genuine writer is not
    signalled but is returned as a surviving holder. (verdict, detail)."""
    detail = {}

    def swap_case():
        read_end, write_end = os.pipe()
        cmd_r, cmd_w = os.pipe()
        ack_r, ack_w = os.pipe()
        devnull = os.open(os.devnull, os.O_WRONLY)
        attempts, survivor = [], "unset"
        child = os.fork()
        if child == 0:
            try:
                os.close(write_end)
                os.read(cmd_r, 1)
                os.dup2(devnull, read_end)
                os.write(ack_w, b"1")
                time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        os.close(cmd_r)
        os.close(ack_w)
        os.close(devnull)
        target = "pipe:[{}]".format(os.fstat(read_end).st_ino)
        real_readlink = os.readlink

        def readlink(path):
            link = real_readlink(path)
            if link == target and "/{}/fd/".format(child) in str(path):
                os.write(cmd_w, b"1")
                os.read(ack_r, 1)
            return link
        try:
            with _patched(os, "readlink", readlink), _patched(
                    signal, "pidfd_send_signal", lambda pidfd, signum: attempts.append(signum)):
                survivor = _kill_holder(child, read_end)
        finally:
            os.close(write_end)
            os.close(cmd_w)
            os.close(ack_r)
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
            os.close(read_end)
        detail["swap"] = dict(attempts=attempts, survivor=survivor, child=child)
        return not attempts and survivor is None

    def refused_case():
        read_end, write_end = os.pipe()
        attempts, survivor = [], "unset"
        child = os.fork()
        if child == 0:
            try:
                os.close(read_end)
                time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        real_readlink = os.readlink
        try:
            with _patched(os, "readlink",
                          lambda path: "0" if path == "/proc/self" else real_readlink(path)), _patched(
                    signal, "pidfd_send_signal", lambda pidfd, signum: attempts.append(signum)):
                survivor = _kill_holder(child, read_end)
        finally:
            os.close(read_end)
            os.close(write_end)
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        detail["refused"] = dict(attempts=attempts, survivor=survivor, child=child)
        return not attempts and survivor == child

    verdict = swap_case() and refused_case()
    return verdict, detail


def _reaped(pid):
    """Whether the child pid of this process was reaped already (a child still alive or a zombie was not; a
    zombie is reaped here)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


def _witnessed_child(start_child):
    """_witnessed over start_child(witness, forked), which runs one child of this process through _in_child and
    has its pid recorded in forked, so the child is known from the fork whether or not it ever reports: dict(result
    (what start_child returned), elapsed (how long it took), pid, pids ({role: pid} reported on the witness), ended
    (every holder of the witness died), reaped (the child was reaped when start_child returned))."""
    forked = []

    def launch(witness):
        start = time.monotonic()
        result = start_child(witness, forked)
        return result, time.monotonic() - start
    (result, elapsed), pids, ended = _witnessed(launch, forked)
    pid = forked[0] if forked else None
    return dict(result=result, elapsed=elapsed, pid=pid, pids=pids, ended=ended,
                reaped=pid is not None and _reaped(pid))


def _stalled(start_child):
    """A start_child for _witnessed_child: start_child, then a stall of this process for WITNESS_TIMEOUT after the
    gate has returned from its cleanup, as host scheduling can stall it."""
    def start(witness, forked):
        result = start_child(witness, forked)
        time.sleep(WITNESS_TIMEOUT)
        return result
    return start


def _gate_start(make_run):
    """A start_child for _witnessed_child: a gate run of make_run(witness), bounded by WITNESS_TIMEOUT."""
    return lambda witness, forked: _gate(types.SimpleNamespace(run=make_run(witness)), ROOT,
                                         timeout=WITNESS_TIMEOUT, forked=forked)


def _witness_verdict(run, expected, late, roles, own="child", bound=WITNESS_WAIT):
    """The verdict on one witnessed run (_witnessed_child). False when it shows a failure, whether or not the run
    was exercised: no child forked, the child not reaped, a holder of the witness alive WITNESS_WAIT after the run,
    a role reported outside roles (WOKE among them: a run or a descendant that outlived its sleep, so no kill
    reached it in time), the role own reporting another pid than the forked one, or a result that is neither
    expected nor late. None (cannot evaluate) when it shows none of these but was not exercised (a role in roles
    never reported, or the result is late: the bound overtook the run before it did its part, as when the child
    starts late), or took bound or longer: its cleanup is proven (every holder ended, the child reaped, own
    reporting the forked pid, nothing outlived its sleep), so the overrun shows only that this process was slow, as
    under host scheduling. An elapsed time cannot tell that from a cleanup that kills late by bound or more but
    before any run outlives its sleep, so a cleanup that overruns the bound reads as cannot-evaluate, not as a
    pass; one that kills late by less than bound is not told from a clean run and still reads as a pass. Else
    True."""
    pids = run["pids"]
    if (run["pid"] is None or not run["ended"] or not run["reaped"] or not set(pids) <= roles
            or (own is not None and pids.get(own, run["pid"]) != run["pid"])
            or run["result"] not in (expected, late)):
        return False
    overran = bound is not None and run["elapsed"] >= bound
    return True if run["result"] == expected and set(pids) == roles and not overran else None


def _witness_attempt(start_child, *verdict_args):
    """One run of a witnessed vector, for _attempted: (_witness_verdict(run, *verdict_args), run)."""
    run = _witnessed_child(start_child)
    return _witness_verdict(run, *verdict_args), run


def _attempted(attempt):
    """Call attempt(), which returns (True, False or None for cannot evaluate, detail), until it returns a verdict
    that is not None, at most ATTEMPTS times: (that verdict, else None, [every detail])."""
    details = []
    for _ in range(ATTEMPTS):
        verdict, detail = attempt()
        details.append(detail)
        if verdict is not None:
            return verdict, details
    return None, details


def _combined(verdicts):
    """False if any verdict is False, else None if any is None, else True."""
    verdicts = list(verdicts)
    return False if any(verdict is False for verdict in verdicts) else None if None in verdicts else True


def _escaping_run(witness):
    """A gate run that moves the gate child out of its own process group into its caller's, out of reach of the
    group kill and the lifeline, reports its pid on the witness pipe and sleeps past any bound, then reports WOKE:
    only the kill of the child by its pid ends it."""
    def run(root):
        os.setpgid(0, os.getpgid(os.getppid()))
        _witness_line(witness, "child")
        time.sleep(HANG_SECONDS)
        _witness_line(witness, WOKE)
        return 0
    return run


class _Refused(Exception):
    """What _refusing_audit raises."""


def _refusing_audit(event, args):
    """An audit hook that refuses every os.killpg and os.kill sending SIGKILL, raising before either signals, as
    any audit hook of a caller can."""
    if event in ("os.killpg", "os.kill") and args[1] == signal.SIGKILL:
        raise _Refused(event)


def _refused_start(witness, forked):
    """A start_child for _witnessed_child, run under _refusing_audit: a gate run that forks a descendant and
    returns at once, so the gate child has exited before the cleanup and only a group kill reaches the descendant;
    "refused" when the cleanup raised the refusal within WITNESS_TIMEOUT (the run returned before its bound),
    "overtaken" when only after it."""
    start = time.monotonic()
    try:
        return _gate(types.SimpleNamespace(run=_forking_run(witness, False)), ROOT, timeout=WITNESS_TIMEOUT,
                     forked=forked)
    except _Refused:
        return "refused" if time.monotonic() - start < WITNESS_TIMEOUT else "overtaken"


def _audited_group_kill():
    """Install _refusing_audit (in a child: an audit hook stays for the life of its process), then attempt the
    witnessed vector of _refused_start: (verdict, runs)."""
    sys.addaudithook(_refusing_audit)
    return _attempted(lambda: _witness_attempt(_refused_start, "refused", "overtaken", {"child", "descendant"}))


def _without_lifeline(attempt):
    """_attempted(attempt) with fcntl.F_SETSIG removed from this process, so no gate child it starts arms the
    lifeline, as on a platform without it, and only the group kill reaches a descendant (run only inside
    _in_child)."""
    vars(fcntl).pop("F_SETSIG", None)
    return _attempted(attempt)


class _Injected(BaseException):
    """The asynchronous exception the window sweeps raise, as a caller's signal handler that raises would."""


def _sweep_thunk():
    """The thunk the window sweeps run in the gate child."""
    return SWEEP_RESULT


def _cleanup_lines():
    """The first and last line of the cleanup of _in_child (the finally of its last top-level try), read from
    this file."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_in_child")
    cleanup = [node.finalbody for node in function.body if isinstance(node, ast.Try) and node.finalbody][-1]
    return cleanup[0].lineno, cleanup[-1].end_lineno


def _injector(ordinal, in_child, owner, cleanup):
    """A trace function that raises _Injected once, at the ordinal-th instruction it sees in _in_child (outside
    its cleanup lines), _child_leg or the sweep thunk, counting only in the gate child (in_child) or only in
    the process owner. Every instruction is a point, not only those at which a signal handler can run, so a
    sweep covers more than a real handler can reach. The cleanup is left out: a handler can first run there
    after its SIGKILL, and each later step of it sits in its own finally. A NOT_TAKEN instruction is not a
    point: nothing raises at that no-op outside tracing, and the compiler leaves it out of every exception
    range, so only a trace function can raise there."""
    window = (_in_child.__code__, _child_leg.__code__, _sweep_thunk.__code__)
    inert = dict((code, set(instruction.offset for instruction in dis.get_instructions(code)
                            if instruction.opname == "NOT_TAKEN")) for code in window)
    seen = [0]

    def trace(frame, event, arg):
        if frame.f_code not in window:
            return None
        frame.f_trace_opcodes = True
        if (event == "opcode" and seen[0] >= 0 and (os.getpid() != owner) == in_child
                and frame.f_lasti not in inert[frame.f_code]
                and not (frame.f_code is window[0] and cleanup[0] <= (frame.f_lineno or 0) <= cleanup[1])):
            seen[0] += 1
            if seen[0] == ordinal:
                seen[0] = -1
                raise _Injected()
        return trace
    return trace


def _leftover_children():
    """Reap every child of this process; returns one entry per child found: "zombie", or "alive" (then waited
    for)."""
    left = []
    with contextlib.suppress(ChildProcessError):
        while True:
            pid, _status = os.waitpid(-1, os.WNOHANG)
            left.append("zombie" if pid else "alive")
            if not pid:
                os.waitpid(-1, 0)
    return left


def _marker_text(fd):
    """What was written to the marker pipe fd so far, without waiting."""
    os.set_blocking(fd, False)
    try:
        return os.read(fd, READ_CHUNK).decode("ascii")
    except BlockingIOError:
        return ""


def _window_sweep(in_child):
    """Run _in_child over _sweep_thunk once per injection point of _injector, from the first, until a run sees
    no injection; returns dict(points, complete, count, findings), each finding a point after which a child of
    this process (alive or a zombie) or a descriptor remained, at which an exception other than the injected
    one left _in_child, or at which the gate child ran its caller's finally. Run only inside _in_child: it
    installs a trace function and expects no other child of its process."""
    owner, cleanup, findings = os.getpid(), _cleanup_lines(), []
    for ordinal in range(1, SWEEP_LIMIT + 1):
        descriptors = set(os.listdir(DESCRIPTOR_DIR))
        marker_r, marker_w = os.pipe()
        result = raised = None
        try:
            try:
                try:
                    sys.settrace(_injector(ordinal, in_child, owner, cleanup))
                    result = _in_child(_sweep_thunk, SWEEP_TIMEOUT)
                finally:
                    sys.settrace(None)
            except BaseException as exc:
                raised = type(exc).__name__
            finally:
                if os.getpid() != owner:
                    os.write(marker_w, b"caller finally ran in the gate child")
                    os._exit(1)
        finally:
            os.close(marker_w)
        try:
            marker = _marker_text(marker_r)
        finally:
            os.close(marker_r)
        left, leaked = _leftover_children(), sorted(set(os.listdir(DESCRIPTOR_DIR)) - descriptors)
        if left or leaked or marker or raised not in (None, "_Injected"):
            findings.append(dict(point=ordinal, raised=raised, result=result, children=left, leaked=leaked,
                                 marker=marker))
        if (result == (True, SWEEP_RESULT)) if in_child else raised is None:
            return dict(points=ordinal, complete=True, count=len(findings), findings=findings[:10])
    return dict(points=SWEEP_LIMIT, complete=False, count=len(findings), findings=findings[:10])


def _swept(probe):
    """True when a window sweep, run in a child, completed over at least SWEEP_FLOOR points with no finding."""
    done, sweep = probe
    return done and sweep["complete"] and sweep["points"] >= SWEEP_FLOOR and not sweep["count"]


def _launch(path, *args, cwd=None):
    result = subprocess.run([sys.executable, "-I", "-B", str(path)] + list(args), capture_output=True, text=True,
                            timeout=CHILD_TIMEOUT, cwd=str(cwd or Path(path).parent), env=dict(LC_ALL="C.UTF-8"))
    return result.returncode, result.stdout + result.stderr


OWN_ENTRY = '\n\nif __name__ == "__main__":\n    _selftest_exit_report.exit_with(main())\n'
OWN_REL = "tools/" + Path(__file__).name


def _guardless_copy(base):
    """A scratch repository holding this checker with its own entry deleted, beside the execution gate, the
    report finalizer, the roster loader and the real registry, or None when this file does not end in its own
    entry."""
    head, entry, tail = Path(__file__).read_text(encoding="utf-8").rpartition(OWN_ENTRY)
    if not entry or tail:
        return None
    root = Path(base) / "guardless"
    (root / "tools").mkdir(parents=True)
    (root / ".git").write_text("gitdir: absent\n", encoding="ascii")
    (root / OWN_REL).write_text(head + "\n", encoding="utf-8")
    for rel in ("tools/check_selftest_execution.py", "tools/_selftest_exit_report.py", "tools/check_ci_parity.py",
                REGISTRY_REL):
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    return root


def _registered_check_ids():
    """This suite's registered check ids, read through the gate's own reader and schema validator: (set of ids,
    None), or (None, reason)."""
    try:
        data = tomllib.loads(_read_regular(ROOT / REGISTRY_REL, REGISTRY_REL).decode("utf-8"))
    except (CannotEvaluate, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


@contextlib.contextmanager
def _patched(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _denying_open(path, flags):
    """Refuse the open of the fixture target, whoever runs the self-test."""
    if str(path).endswith(TARGET):
        raise PermissionError(errno.EACCES, "open refused by the self-test")
    return os.open(path, flags)


def _failing_read_of(path):
    """A read that fails with EIO, as a failing disk would, for the one file at path."""
    inode = os.stat(path).st_ino

    def failing_read(fd, size):
        if os.fstat(fd).st_ino == inode:
            raise OSError(errno.EIO, "read refused by the self-test")
        return os.read(fd, size)
    return failing_read


_ORIGINAL_EXTRACT = check_ci_parity.extract_local


def _odd_extraction(text):
    """The roster loader returning a launch target that is neither a Python script nor gitleaks."""
    extraction = _ORIGINAL_EXTRACT(text)
    return extraction._replace(members=extraction.members | {"tools/odd.sh --flag"})


def _fixtures(base):
    """Build every vector's tree once: (vector id, root, expected exit, expected text, patch)."""
    vectors = []

    def add(vector, root, code, text, patch=None):
        vectors.append((vector, root, code, text, patch))

    intact = _tree(base, "intact", ((TARGET, INTACT), (REGISTERED, INTACT)))
    add("intact/passes", intact, 0, "checked 2 launched scripts")
    spliced_text = splice(INTACT, "class Second:\n", REPLACEMENT)
    add("splice/guard-missing", _tree(base, "spliced", ((TARGET, spliced_text), (REGISTERED, INTACT))), 1,
        TARGET + ": guard missing")
    for shape, text, reason in SHAPES:
        add("shape/" + shape, _tree(base, shape, ((TARGET, text), (REGISTERED, INTACT))), 1,
            TARGET + ": " + reason)
    for name, data in ACCEPTED:
        add("accepted/" + name, _tree(base, name, ((TARGET, data), (REGISTERED, INTACT))), 0,
            "checked 2 launched scripts")
    add("registry/unlaunched-runner", _tree(base, "unlaunched", ((TARGET, INTACT), (REGISTERED, spliced_text))),
        1, REGISTERED + ": guard missing")

    # a listed script that is unparsable, missing, not a regular file, or unreadable cannot be evaluated
    for case, data, reason in (
            ("unparsable", b"def broken(:\n", "does not parse"),
            ("invalid-utf8", b"# \xff\n" + INTACT.encode("utf-8"), "does not parse"),
            ("missing", None, "missing"), ("directory", None, "not a regular file"),
            ("symlink", None, "is a symlink"), ("fifo", None, "not a regular file"),
            ("unreadable-open", None, "cannot open (PermissionError)"),
            ("unreadable-read", None, "unreadable (OSError)")):
        root = _tree(base, "script-" + case, ((TARGET, INTACT if data is None else data), (REGISTERED, INTACT)))
        if case in ("missing", "directory", "symlink", "fifo"):
            _replace_with(root, TARGET, case)
        patch = None
        if case == "unreadable-open":
            patch = ("_open", _denying_open)
        elif case == "unreadable-read":
            patch = ("_read", _failing_read_of(root / TARGET))
        add("script/" + case, root, 2, TARGET + ": " + reason, patch)

    # the roster: missing, symlinked, a FIFO, a forbidden byte, a malformed line, no Python script, or an
    # unrecognised target from the loader cannot be evaluated
    scripts = ((TARGET, INTACT), (REGISTERED, INTACT))
    reasons = dict(missing="missing", symlink="is a symlink", fifo="not a regular file")
    for case in ("missing", "symlink", "fifo"):
        root = _tree(base, "roster-" + case, scripts)
        _replace_with(root, ROSTER_REL, case)
        add("roster/" + case, root, 2, ROSTER_REL + ": " + reasons[case])
    add("roster/non-ascii", _tree(base, "roster-non-ascii", scripts,
                                  roster_text=_roster_text([TARGET]) + "# caf\u00e9\n"),
        2, "outside printable ASCII")
    add("roster/malformed-line", _tree(base, "roster-malformed", scripts, roster_text=_roster_text(
        [TARGET], ['run_gate "odd" bash tools/odd.sh'])), 2, "unsupported gate target")
    add("roster/no-python", _tree(base, "roster-empty", ((REGISTERED, INTACT),), roster=()), 2,
        "no Python script")
    add("roster/odd-target", _tree(base, "roster-odd", scripts), 2, "unrecognised launch target 'tools/odd.sh'",
        ("extract_local", _odd_extraction))

    # the registry: missing, symlinked, a FIFO, malformed, schema-invalid, or a runner outside the script
    # surface cannot be evaluated
    for case in ("missing", "symlink", "fifo"):
        root = _tree(base, "registry-" + case, scripts)
        _replace_with(root, REGISTRY_REL, case)
        add("registry/" + case, root, 2, REGISTRY_REL + ": " + reasons[case])
    good = _registry_text([REGISTERED])
    for case, text, reason in (
            ("unparsable", "[[suite]\n", "malformed (TOMLDecodeError)"),
            ("invalid-utf8", None, "malformed (UnicodeDecodeError)"),
            ("format-version", good.replace("format-version = 1", "format-version = 999"),
             "format-version must be exactly the integer 1"),
            ("no-id", good.replace('id = "fixture-1"\n', ""), "keys must be exactly"),
            ("string-ids", good.replace('["fixture-check"]', '"fixture-check"'),
             "expected-check-ids must be a non-empty array"),
            ("no-suite", "format-version = 1\n", "top-level keys must be exactly"),
            ("outside-runner", _registry_text(["lib/registered.py"]), "is not a literal tools/*.py")):
        root = _tree(base, "registry-" + case, scripts + (("lib/registered.py", INTACT),),
                     registry_text="" if text is None else text)
        if text is None:
            (root / REGISTRY_REL).write_bytes(good.encode("utf-8").replace(b"fixture-1", b"fixture-\xff"))
        add("registry/" + case, root, 2, reason)
    return vectors


# A fixture gate run takes milliseconds; GATE_TIMEOUT (3 s) bounds it. When a run is overtaken by that bound it
# is re-run once at this larger bound, to tell a scheduling stall (which completes with more time) from a run the
# mutation left unbounded (a control input opened without O_NONBLOCK blocks on a FIFO), which never completes.
BLOCK_CONFIRM = 10


def _vector_outcome(module, vector):
    """Run one vector against module, classifying its result as True (the vector holds), False (a genuine bounded
    failure, an exposure) or 'blocked' (a run the mutation left unbounded, an exposure). A gate run overtaken by
    GATE_TIMEOUT is re-run once at the larger BLOCK_CONFIRM bound: a scheduling stall completes there and its real
    result decides, so a stall earns no exposure merely by hanging, while a run the mutation left unbounded (a
    control input opened without O_NONBLOCK blocks on a FIFO) still does not complete and is a block. (outcome,
    detail)."""
    vid, root, code, text, patch = vector

    def run(timeout):
        with contextlib.ExitStack() as stack:
            if patch is not None:
                owner = check_ci_parity if patch[0] == "extract_local" else module
                stack.enter_context(_patched(owner, patch[0], patch[1]))
            return _gate(module, root, timeout=timeout)
    got, output = run(GATE_TIMEOUT)
    if got != "hung":
        return (got == code and text in output), (got, output)
    got, output = run(BLOCK_CONFIRM)
    if got == "hung":
        return "blocked", (got, output)
    return (got == code and text in output), (got, output)


def _run_vectors(module, vectors):
    """Run every vector against module (this gate or a mutant of it): [(vector id, outcome, detail)], with outcome
    as _vector_outcome gives it (True, False or 'blocked')."""
    return [(vector[0],) + _vector_outcome(module, vector) for vector in vectors]


def _mutation_verdicts(records, all_vectors):
    """The two mutation-aggregation verdicts from records [(mutant, named vector, {vector: outcome})], outcome in
    (True, False, 'blocked'). A False or a 'blocked' is an exposure (a genuine bounded failure, or a run the
    mutation left unbounded); a True is not, so a scheduling stall that completed at BLOCK_CONFIRM to the expected
    result earns no exposure by hanging. every-mutant-caught: False when a mutant's named vector held (its mutation
    was not exposed), else True. every-vector-discriminates: False when a non-twin vector held under every mutant
    (no mutation exposed it), else True. Returns (caught, discriminates, detail)."""
    exposed = set(v for _m, _n, outcomes in records for v, ok in outcomes.items() if ok in (False, "blocked"))
    missed = [(mutant, named) for mutant, named, outcomes in records
              if outcomes.get(named) not in (False, "blocked")]
    never = sorted(set(all_vectors) - exposed - UNMUTATED)
    return (not missed), (not never), dict(missed=missed, never=never)


# Each mutant removes one check (or one fail-closed path) from the gate half of this file's source, the
# part before the self-test fixtures: (mutant id, the exact source text, its replacement, the vector that
# must then fail). The self-test loads every mutant as a scratch module, requires its named vector to fail,
# and requires every vector but the intact twin to fail under at least one mutant, so no vector is left that
# removing a check would not expose.
MUTANTS = (
    ("open-nonblock", "os.O_RDONLY | os.O_NONBLOCK | ", "os.O_RDONLY | ", "registry/fifo"),
    ("open-nofollow", "os.O_NOFOLLOW | ", "", "script/symlink"),
    ("symlink-reason", "if exc.errno == errno.ELOOP:", "if False:", "script/symlink"),
    ("regular-only", "if not stat.S_ISREG(os.fstat(fd).st_mode):", "if False:", "script/fifo"),
    ("missing-closed", 'raise CannotEvaluate("{}: missing".format(label))', 'return b""', "script/missing"),
    ("open-error-closed", 'raise CannotEvaluate("{}: cannot open ({})".format(label, type(exc).__name__))',
     'return b""', "script/unreadable-open"),
    ("read-error-closed", 'raise CannotEvaluate("{}: unreadable ({})".format(label, type(exc).__name__))',
     'return b""', "script/unreadable-read"),
    ("roster-diagnostic", "if diagnostic is not None:", "if False:", "roster/non-ascii"),
    ("roster-extraction", "if extraction.diagnostics:", "if False:", "roster/malformed-line"),
    ("roster-target", 'elif target != "gitleaks":', "elif False:", "roster/odd-target"),
    ("roster-nonempty", "if not scripts:", "if False:", "roster/no-python"),
    ("registry-parse",
     "except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:",
     "except ZeroDivisionError as exc:", "registry/unparsable"),
    ("registry-schema", "if rows is None:", "if False:", "registry/format-version"),
    ("registry-runner", "if not check_ci_parity.PY_TARGET_RE.fullmatch(runner):", "if False:",
     "registry/outside-runner"),
    ("script-parse", "except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:",
     "except ZeroDivisionError as exc:", "script/unparsable"),
    ("parse-raw-bytes", "ast.parse(data, filename=rel)", 'ast.parse(data.decode("utf-8"), filename=rel)',
     "accepted/coding-latin-1"),
    ("guard-eq", "and isinstance(test.ops[0], ast.Eq)", "and True", "shape/not-equal"),
    ("name-lhs", 'and isinstance(test.left, ast.Name) and test.left.id == "__name__"', "", "shape/name-lhs-file"),
    ("main-literal", 'and test.comparators[0].value == "__main__")', ")", "shape/main-literal-short"),
    ("guards-none", "if not guards:", "if False:", "splice/guard-missing"),
    ("nested-reason", 'return "guard nested below the top level" if nested else "guard missing"',
     'return "guard missing"', "shape/nested-if"),
    ("guards-many", "if len(guards) > 1:", "if False:", "shape/two-guards"),
    ("guard-last", "if guards[0] != len(body) - 1:", "if False:", "shape/late-statement"),
    ("guard-else", "if guard.orelse:", "if False:", "shape/guard-else"),
    ("guard-call", "if not _call_statement(guard.body[-1]):", "if False:", "shape/guard-def"),
    ("expr-call", "return isinstance(node.value, ast.Call)", "return True", "shape/guard-bare-name"),
    ("raise-form", "if isinstance(node, ast.Raise):", "if False:", "accepted/raise-call"),
    ("raise-systemexit", 'isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit"', "True",
     "shape/guard-raise-other"),
    ("raise-call-arg", "and isinstance(exc.args[0], ast.Call))", ")", "shape/guard-raise-constant"),
    ("guard-reachable",
     "if any(isinstance(node, ast.Raise) for statement in guard.body[:-1] for node in ast.walk(statement)):",
     "if False:", "shape/guard-raise-first"),
    ("guard-reachable-depth", "for statement in guard.body[:-1] for node in ast.walk(statement)",
     "for node in guard.body[:-1]", "shape/guard-raise-nested-if"),
    ("guard-nested", "    if nested:\n", "    if False:\n", "shape/nested-plus-valid"),
    ("nested-depth", "for inner in ast.walk(node) if inner is not node", "for inner in ast.iter_child_nodes(node)",
     "shape/nested-deep"),
)
# The intact twin is the one vector no removal can fail: each finding vector is one edit from it.
UNMUTATED = {"intact/passes"}


def _load_mutant(source, directory, number):
    """Write one mutant module to the scratch directory and load it with importlib."""
    path = Path(directory) / "entry_guard_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("entry_guard_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def self_test(report_path=None):
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        # ok is True, False, or None: cannot evaluate (a vector that was never exercised), never read as a pass
        results.append((check_id, None if ok is None else bool(ok), detail))
        executed.append(check_id)

    this = sys.modules[__name__]
    with tempfile.TemporaryDirectory(prefix="entry-guard-selftest-") as base:
        vectors = _fixtures(base)
        # 1. every vector holds against this gate
        outcomes = _run_vectors(this, vectors)
        not_held = [(vector, detail) for vector, ok, detail in outcomes if ok is not True]
        check("vector/every-vector-holds",
              bool(vectors) and len(outcomes) == len(vectors) and not not_held, not_held)

        # 2. the splice reproduction, run directly: the intact script reaches its checks, and the spliced one,
        # a guard whose body only defines a function, a guard whose call follows a nested raise, and a
        # misspelled guard each exit 0 silent
        roots = dict((vector, root) for vector, root, _code, _text, _patch in vectors)
        code, output = _launch(roots["intact/passes"] / TARGET)
        check("run/intact-reaches-checks", code == 0 and "checks ran" in output, output)
        for check_id, vector in (("run/splice-silent-green", "splice/guard-missing"),
                                 ("run/guard-def-silent-green", "shape/guard-def"),
                                 ("run/raise-nested-silent-green", "shape/guard-raise-nested-if"),
                                 ("run/main-literal-silent-green", "shape/main-literal-short")):
            code, output = _launch(roots[vector] / TARGET)
            check(check_id, code == 0 and output == "", output)

        # 3. this gate's own entry, held independently: with its guard deleted, this file exits 0 silent when
        # launched, and the execution gate, launched as both rosters launch this self-test, refuses it
        # because the child wrote no execution report
        guardless = _guardless_copy(base)
        silent = caught = (None, "this file does not end in its own entry")
        if guardless is not None:
            silent = _launch(guardless / OWN_REL)
            caught = _launch(guardless / "tools" / "check_selftest_execution.py", "--suite", SUITE_ID,
                             cwd=guardless)
        check("own-entry/guard-loss-silent-green", silent == (0, ""), silent)
        check("own-entry/guard-loss-caught-by-execution-gate",
              caught[0] == 2 and "no execution report" in caught[1], caught)

        # 4. every mutant (one check removed) fails its named vector, and every vector but the intact twin
        # fails under at least one mutant
        gate_source, fixtures, self_test_source = Path(__file__).read_text(encoding="utf-8").partition(
            "\nINTACT = ")
        all_vectors = [vector for vector, _root, _code, _text, _patch in vectors]
        records, anchors = [], []
        mutant_dir = Path(base) / "mutants"
        mutant_dir.mkdir()
        for number, (mutant, old, new, vector) in enumerate(MUTANTS, 1):
            if gate_source.count(old) != 1:
                anchors.append((mutant, "anchor found {} times".format(gate_source.count(old))))
                continue
            module = _load_mutant(gate_source.replace(old, new) + fixtures + self_test_source, mutant_dir,
                                  number)
            outcomes = dict((name, ok) for name, ok, _detail in _run_vectors(module, vectors))
            records.append((mutant, vector, outcomes))
        caught, discriminates, mdetail = _mutation_verdicts(records, all_vectors)
        check("mutant/every-mutant-caught", bool(MUTANTS) and not anchors and caught,
              dict(mdetail, anchors=anchors))
        check("mutant/every-vector-discriminates", discriminates, mdetail)
        # a scheduling stall (a run overtaken by GATE_TIMEOUT that completes at BLOCK_CONFIRM) earns no exposure by
        # hanging: only a genuine bounded failure or a deterministic block credits a mutant, so a named vector that
        # held (as a resolved stall does) leaves its mutant uncaught, while a block or a failure catches it
        twin = next(iter(UNMUTATED))
        pv = "probe/vector"
        held = _mutation_verdicts([("probe", pv, dict([(pv, True)]))], [pv, twin])
        blocked = _mutation_verdicts([("probe", pv, dict([(pv, "blocked")]))], [pv, twin])
        failed = _mutation_verdicts([("probe", pv, dict([(pv, False)]))], [pv, twin])
        check("mutant/hung-earns-no-credit",
              held[:2] == (False, False) and blocked[:2] == (True, True) and failed[:2] == (True, True),
              (held, blocked, failed))

        # 5. the gate runner borrows no process timer: an armed caller ITIMER_REAL keeps its interval, its SIGALRM
        # handler, its signal mask and its expiry instant through a paced gate run, to the precision of its stamps
        # (probed in a child, so this self-test never arms its own process timer); the timer predicate refuses a
        # timer reset or rewound during the run by more than that precision, and a reset to the starting countdown
        # however early, reads a smaller move as kept, and reads a timer read delayed past its bracket as
        # cannot-evaluate, never as a failure; a gate run that ignores SIGALRM and overruns its bound is still
        # bounded and reported hung, never as its late pass
        hangs = (("hung", ""), None)

        def timer_attempt():
            done, probe = _in_child(lambda: _caller_timer_probe(this, roots["intact/passes"]), CHILD_TIMEOUT)
            return (_timer_kept(probe) if done else False), probe
        check("gate/caller-timer-survives", *_attempted(timer_attempt))
        settled = (CALLER_TIMER[0] - TIMER_SETTLE, CALLER_TIMER[1])
        stamps = [0.0, 0.0, TIMER_RUN_SECONDS, TIMER_RUN_SECONDS]
        counted = dict(code=0, before=settled, after=(settled[0] - TIMER_RUN_SECONDS, CALLER_TIMER[1]),
                       stamps=stamps, handler=True, mask=True)
        reset = dict(counted, after=CALLER_TIMER)
        rewound = dict(counted, after=(settled[0] - 2 * TIMER_RUN_SECONDS, CALLER_TIMER[1]))
        check("gate/timer-reset-refused",
              [_timer_kept(reset), _timer_kept(rewound), _timer_kept(counted)] == [False, False, True],
              (reset, rewound, counted))
        # a timer read TIMER_RUN_SECONDS late after the run, or a run overtaken by its bound: with the timer
        # untouched it cannot evaluate; with the timer reset it is refused
        gapped = dict(counted, stamps=stamps[:3] + [2 * TIMER_RUN_SECONDS],
                      after=(settled[0] - 2 * TIMER_RUN_SECONDS, CALLER_TIMER[1]))
        gapped_reset = dict(gapped, after=CALLER_TIMER)
        overtaken = dict(counted, code="hung")
        gap_cases = [_timer_kept(gapped), _timer_kept(gapped_reset), _timer_kept(overtaken),
                     _timer_kept(dict(overtaken, after=CALLER_TIMER))]
        check("gate/timer-gap-cannot-fail", gap_cases == [None, False, None, False],
              (gap_cases, gapped, gapped_reset, overtaken))
        # the precision claimed: the counterexample (a first read 40 ms inside its bracket, then a reset to the
        # starting countdown at the gate's entry) is refused, as its first read shows a timer not armed
        # TIMER_SETTLE before it, and so is the same reset after a settled first read; with both brackets
        # TIMER_BRACKET - TIMER_FLOOR wide, a move later or earlier by TIMER_FLOOR plus both widths, less a
        # margin, reads as kept (a move of that size is not seen), and one a margin more is refused
        counterexample = dict(code=0, before=CALLER_TIMER, after=(CALLER_TIMER[0] - 0.3, CALLER_TIMER[1]),
                              stamps=[0.0, 0.04, 0.34, 0.34], handler=True, mask=True)
        width, margin = TIMER_BRACKET - TIMER_FLOOR, TIMER_FLOOR / 2
        wide = [0.0, width, width + TIMER_RUN_SECONDS + TIMER_FLOOR, 2 * width + TIMER_RUN_SECONDS + TIMER_FLOOR]

        def moved(move):
            # a later move read at the far edge of each bracket (first, last), an earlier one at the near edge
            _first, ran, done, last = wide
            after = settled[0] + move - last if move > 0 else settled[0] + move + ran - done
            return dict(counted, stamps=wide, after=(after, CALLER_TIMER[1]))
        unseen = TIMER_FLOOR + 2 * width
        precision = [_timer_kept(counterexample), _timer_kept(dict(counterexample, before=settled))] + [
            _timer_kept(moved(sign * (unseen + step))) for sign in (1, -1) for step in (-margin, margin)]
        check("gate/timer-precision-stated", precision == [False, False, True, False, True, False],
              (precision, counterexample, wide))
        check("gate/overrun-bounded-as-hung", *_attempted(lambda: _witness_attempt(
            lambda witness, forked: _gate(types.SimpleNamespace(run=_overrunning_run(witness)),
                                          roots["intact/passes"], timeout=OVERRUN_TIMEOUT, forked=forked),
            *hangs, {"child"}, "child", OVERRUN_BOUND)))

        # 6. the gate child cannot outlive its bound or run its caller's cleanup: on expiry it is killed and
        # reaped, and so is one that left its process group; a descendant it forked dies with it, whether the run
        # hangs or returns, by the group kill alone when the lifeline is unarmed, and by the lifeline alone when
        # an audit hook refuses both of the cleanup's kills (each run in a child, so neither change reaches this
        # process); a gate child whose parent is killed mid-run dies with that parent; and an exception at any
        # instruction of the fork window leaves, in the parent, no child, zombie or descriptor behind, and in the
        # child never unwinds into its caller (each sweep runs in a child, as it installs a trace function). Each
        # witnessed run knows its child from the fork; one that shows no failure but was not exercised (its bound
        # overtook it before it did its part, as when the child starts late) is retried, and is cannot-evaluate,
        # never a failure, if no run is exercised; one that overran its elapsed bound with its cleanup proven is
        # cannot-evaluate too, while one that also shows a failure still fails; a witness read that was itself slow
        # still reads holders that died in time as dead; and a failure is never retried or masked. Witness cleanup
        # signals only a process, never this one, that /proc shows holding the witness write end and that is alive
        # after that read, through a pidfd opened before it, so no kill reaches this process or a reused pid,
        # whatever a run reports (_kill_holder, _witnessed)
        exercised = dict(result=("hung", ""), elapsed=WITNESS_TIMEOUT, pid=1, pids={"child": 1}, ended=True,
                         reaped=True)
        unexercised = dict(exercised, pids={})
        cases = ((exercised, True), (unexercised, None), (dict(unexercised, reaped=False), False),
                 (dict(unexercised, ended=False), False), (dict(unexercised, result=(0, "")), False),
                 (dict(exercised, pids={"child": 2}), False))
        verdicts = [_witness_verdict(run, *hangs, {"child"}) for run, _want in cases]
        returned = dict(exercised, result=("hung", ""), pids={"child": 1, "descendant": 3})
        late = _witness_verdict(returned, (0, ""), ("hung", ""), {"child", "descendant"})
        check("gate/unexercised-witness-cannot-evaluate",
              verdicts == [want for _run, want in cases] and late is None, (verdicts, late))
        proven = dict(exercised, elapsed=WITNESS_WAIT)
        overruns = [_witness_verdict(run, *hangs, {"child"}) for run in (
            proven, dict(proven, pids={}), dict(proven, pids={"child": 1, WOKE: 1}), dict(proven, ended=False),
            dict(proven, reaped=False), dict(proven, pids={"child": 2}), dict(proven, result=(0, "")))]
        unbounded = _witness_verdict(proven, *hangs, {"child"}, "child", None)
        drained = [_late_read(True), _late_read(False)]

        def stalled_attempt():
            verdict, run = _witness_attempt(_stalled(_gate_start(_hanging_run)), *hangs, {"child"}, "child",
                                            WITNESS_TIMEOUT)
            return (False if verdict is False else (verdict is None) if run["pids"] == {"child": run["pid"]}
                    else None), run
        stalled = _attempted(stalled_attempt)
        check("gate/overrun-proven-cleanup-cannot-fail",
              _combined([overruns == [None, None, False, False, False, False, False] and unbounded is True
                         and drained == [(b"child 1\n", True), (b"child 1\n", False)], stalled[0]]),
              (overruns, unbounded, drained, stalled))
        check("gate/witness-kill-confirmed", *_kill_confirmed())
        check("gate/witness-cleanup-spares-owner", *_owner_spared())
        check("gate/witness-cleanup-binds-write-end", *_reuse_bound())

        def sequence(*verdicts):
            queue = list(verdicts)
            return lambda: (queue.pop(0), len(queue))
        retried = [_attempted(sequence(*verdicts)) for verdicts in (
            (False, True, True), (None, False, True), (None, None, None), (None, True, False))]
        masked = [_combined(verdicts) for verdicts in (
            (True, None, False), (None, False), (False, True), (None, True), (True, True))]
        check("gate/failure-never-retried-or-masked",
              [(verdict, len(details)) for verdict, details in retried] == [(False, 1), (False, 2), (None, 3),
                                                                            (True, 2)]
              and masked == [False, False, False, None, True], (retried, masked))
        check("gate/expiry-kills-and-reaps",
              *_attempted(lambda: _witness_attempt(_gate_start(_hanging_run), *hangs, {"child"})))
        check("gate/escaped-child-killed",
              *_attempted(lambda: _witness_attempt(_gate_start(_escaping_run), *hangs, {"child"})))

        def descendant_attempt(hang):
            return lambda: _witness_attempt(_gate_start(lambda witness: _forking_run(witness, hang)),
                                            *(hangs if hang else ((0, ""), ("hung", ""))), {"child", "descendant"})
        legs = [_attempted(descendant_attempt(True)), _attempted(descendant_attempt(False))]
        done, unarmed = _in_child(lambda: _without_lifeline(descendant_attempt(False)), CHILD_TIMEOUT)
        legs.append(unarmed if done else (False, unarmed))
        check("gate/descendants-killed", _combined(verdict for verdict, _runs in legs), legs)
        done, audited = _in_child(_audited_group_kill, CHILD_TIMEOUT)
        check("gate/audit-hook-cannot-skip-group-kill", done and audited[0], (done, audited))
        orphaned = _attempted(lambda: _witness_attempt(
            lambda witness, forked: _in_child(lambda: _stand_in(witness), 2 * WITNESS_WAIT, forked),
            (False, "crashed (child wait status 9)"), (True, ["hung", ""]), {"child"}, None, None))
        check("gate/parent-death-kills-group", hasattr(fcntl, "F_SETSIG") and orphaned[0],
              (hasattr(fcntl, "F_SETSIG"), orphaned))
        sweep = _in_child(lambda: _window_sweep(False), CHILD_TIMEOUT)
        check("gate/fork-window-cleaned", _swept(sweep), sweep)
        sweep = _in_child(lambda: _window_sweep(True), CHILD_TIMEOUT)
        check("gate/child-never-unwinds", _swept(sweep), sweep)

    # 7. the live leg: the real surface passes and is no smaller than the recorded floor, both in a gate child and
    # through this file's own entry, launched as the roster launches the gate step
    code, output = _gate(this, ROOT, timeout=CHILD_TIMEOUT)
    marker = "entry-guard checked "
    count = int(output.split(marker, 1)[1].split()[0]) if code == 0 and marker in output else None
    check("live/real-tree-passes", count is not None and count >= LIVE_FLOOR, output)
    code, output = _launch(Path(__file__), cwd=ROOT)
    check("live/direct-launch-passes", code == 0 and output == "PASS: {}{} launched scripts\n".format(
        marker, count), (code, output))

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))

    failed = [(check_id, detail) for check_id, ok, detail in results if ok is False] + harness
    unevaluated = [(check_id, detail) for check_id, ok, detail in results if ok is None]
    if failed or unevaluated:
        print("SELF-TEST FAIL: entry-guard {} of {} check(s) failed".format(len(failed), len(results)) if failed
              else "SELF-TEST CANNOT EVALUATE: entry-guard {} of {} check(s) not exercised in {} runs each "
              "(every run was overtaken by its bound before it did its part, bracketed its timer reads too "
              "widely, or overran its elapsed bound with its cleanup proven; none showed a failure)".format(
                  len(unevaluated), len(results), ATTEMPTS))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        for check_id, detail in unevaluated:
            print("  {} (cannot evaluate): {!r}".format(check_id, detail))
        return 1 if failed else 2
    print("SELF-TEST PASS: entry-guard {} checks ({} vectors: splice reproduced and caught; displaced, "
          "duplicated, else-bearing, misnamed, callless, dormant-call, unreachable, nested and unlaunched-runner "
          "entries are findings; non-regular, unreadable, malformed and schema-invalid inputs fail closed; {} "
          "mutants each caught, every vector discriminating; this gate's own lost guard caught by the execution "
          "gate; a caller timer survives the gate runner and keeps its expiry instant to within {:g} s (a move "
          "beyond that either way, and a reset to the starting countdown, are refused; a smaller move may not "
          "be seen) and a delayed timer read is no failure; an overrun is bounded as hung; an expired gate child, "
          "one that left its group, its descendants (an audit hook refusing the kills included) and an orphaned "
          "one are killed, and a run its bound overtook, or that overran its elapsed bound with its cleanup "
          "proven, is no failure, and a failure is never retried or masked; witness cleanup never signals this "
          "process or a pid it has not confirmed holding the witness write end; an exception at any point of the "
          "fork window leaves no child or descriptor and never unwinds a child into its caller; live surface {} "
          "scripts)".format(
              len(results), len(vectors), len(MUTANTS), TIMER_FLOOR + 2 * TIMER_BRACKET, count))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    if not args:
        return run(ROOT)
    print("usage: check_entry_guard.py [--self-test] [--execution-report ABS_PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
