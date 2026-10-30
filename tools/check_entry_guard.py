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
(check_selftest_execution._manifest_rows); a row's optional args, which the execution gate forwards to its
runner, do not change which script this gate reads, and a runner that several rows share is read once. Every
control input (the roster, the registry and each listed script) is opened non-blocking without following a
final symlink, and is refused unless the opened descriptor is a regular file, so a FIFO, device, directory or
symlink is cannot-evaluate and never blocks the run.

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
check is unresolved: a gate-runner vector was not exercised in any of its ATTEMPTS runs (host scheduling let the
bound overtake each run, widened a timer read's bracket past TIMER_BRACKET, or made a run whose cleanup is proven
overrun its elapsed bound), a mutant's fixture run hung without earning credit, the hung-credit check's FIFO
subject never showed the kernel's FIFO open wait while every case needing no such reading held (a failure among
those cases fails, never cannot-evaluate), a refusal fixture's dumpability setup (PR_SET_DUMPABLE) or bindability
probe could not be made, the double-fork walk leg with no child subreaper, not exercised, or its timing
precondition missed in its attempts, or the high-descriptor probe leg where the descriptor bound cannot reach
past 1024; such a run is never read as a pass or as a failure. No run earns
mutation credit by hanging alone. Under a mutant, a fixture gate run that GATE_TIMEOUT (3 s)
overtakes is re-run once at the larger BLOCK_CONFIRM (10 s) bound, and a re-run that completes decides by its
result. A re-run still running at BLOCK_CONFIRM is credited as a block only for a (mutant, vector) pair declared in
BLOCK_CREDIT (open-nonblock on the three FIFO vectors), and only when its own child, read before it is killed, is
shown blocked opening a FIFO (/proc/<pid>/syscall an open-family call, /proc/<pid>/wchan the kernel's FIFO open
wait); any other such hang, or one whose evidence is absent or unreadable, cannot evaluate. This gate's own run on
a fixture is never re-run: its hang is a failure. Host scheduling can still fail a clean run where a bound has no
such reading: a stall of GATE_TIMEOUT (3 s) in this gate's run of a fixture vector (under a mutant, such a stall
that lasts past BLOCK_CONFIRM too reads cannot-evaluate, and no longer reads that mutant as caught); one of
HANG_SECONDS (60 s) between a witnessed run's deadline and its kill, after which the run reports WOKE; a holder
of a witness that takes WITNESS_WAIT (5 s) after its run to die; the outer 2 * WITNESS_WAIT (10 s) bound of the
parent-death vector (a stall of WITNESS_WAIT in its stand-in reads cannot-evaluate, not a failure); and the
CHILD_TIMEOUT (60 s) bounds of the timer vector, the unarmed and audited descendant legs, the window sweeps, the
conversion-limit observation, the _launch of each run/* and own-entry/* leg, and the live legs.

The self-test requires Linux: its gate runner and witnesses use os.waitid with WNOWAIT, fcntl.F_SETSIG,
os.pidfd_open and /proc. Elsewhere it never passes (the parent-death check fails without F_SETSIG, and a missing
call raises). Witness cleanup binds a holder through pidfd_getfd, and a hang's evidence is read from
/proc/<pid>/syscall, by syscall numbers verified for x86_64 and aarch64 only; on another machine every binding is
NOT EVALUATED and no hang earns credit, so the self-test does not pass there. While its fixtures run, the self-test
makes this process a child subreaper (prctl), restored after, so an orphaned witness holder can be bound under
Yama ptrace_scope 1; the restoring prctl's result is checked, and a restore that fails is a named failure of the
run, never silent (a run that unwinds with an exception names it on stderr); a set that is refused changes
nothing and attempts no restore.

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
import builtins
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
import resource
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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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
    """The real roster with its gate lines replaced by fixture launches, so the loader's grammar holds. Its
    first gate line, the child-status probe (tools/check_child_status_coverage.py holds it first), stays, with
    the special-file precheck gate and STOP lines the loader accepts only right after it, and the fixture
    launches stand where the real roster's first gate after them stood."""
    lines = (ROOT / ROSTER_REL).read_text(encoding="ascii").splitlines()
    precheck = set(check_ci_parity.PRECHECK_BOOTSTRAP_GATE_LINES) | set(check_ci_parity.PRECHECK_GATE_LINES)
    gates = [index for index, line in enumerate(lines) if line.startswith('run_gate "')]
    dropped = [index for index in gates[1:] if lines[index].strip() not in precheck]
    slot = dropped[0]
    kept = [line for index, line in enumerate(lines) if index not in dropped]
    launches = ['run_gate "fixture-{}" python3 -I -B {}'.format(number, rel)
                for number, rel in enumerate(entries, 1)]
    return "\n".join(kept[:slot] + launches + list(extra) + kept[slot:]) + "\n"


def _scaffold_launches():
    """The Python scripts the fixture roster launches outside its fixture gate lines (the real roster's
    child-status probe and the special-file precheck right after it, both ahead of every other gate), read
    through the gate's own roster loader."""
    targets = (member.split(" ", 1)[0] for member in check_ci_parity.extract_local(_roster_text(())).members)
    return sorted(target for target in targets if target.endswith(".py"))


def _without_precheck(text):
    """A roster text with the special-file precheck gate lines and their STOP lines removed, by the exact
    spellings the roster loader accepts, and with the child-status probe gate line before them, so the roster
    launches no Python script at all."""
    drop = (set(check_ci_parity.PRECHECK_BOOTSTRAP_GATE_LINES) | set(check_ci_parity.PRECHECK_GATE_LINES)
            | {check_ci_parity.PRECHECK_STOP_LINE})
    lines = text.splitlines(True)
    probe = next(index for index, line in enumerate(lines) if line.startswith('run_gate "'))
    return "".join(line for index, line in enumerate(lines) if index != probe and line.strip() not in drop)


def _registry_text(runners):
    rows = "".join('\n[[suite]]\nid = "fixture-{}"\nrunner = "{}"\nexpected-check-ids = ["fixture-check"]\n'
                   .format(number, rel) for number, rel in enumerate(runners, 1))
    return "format-version = 1\n" + rows


def _tree(base, name, scripts, roster=(TARGET,), registry=(REGISTERED,), registry_text=None,
          roster_text=None):
    """Write a synthetic repository; scripts is a sequence of (relative path, text or bytes) pairs. Every
    script the roster launches outside its gate lines is written as INTACT too, as a registered runner is,
    so the gate finds it and each vector differs from the intact tree only in its own edit."""
    root = Path(base) / name
    (root / "tools").mkdir(parents=True)
    for rel, text in tuple(scripts) + tuple((rel, INTACT) for rel in _scaffold_launches()):
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


def _in_child(thunk, timeout, forked=None, expiring=None):
    """Run thunk() in a forked child (_child_leg); returns (True, its result through JSON) or (False, "hung",
    "hung; unreaped (...)", "crashed (...)" or "unreaped (...)"). The bound is a monotonic deadline this
    parent owns. forked, if given, must be an empty list: the child's pid is recorded in it as the fork
    returns, so the caller knows the child whether or not thunk ever runs. expiring, if given, is called with
    the child's pid when the deadline overtakes the run, before the cleanup kills the child, so the caller can
    read what the still-running child is doing. However this function is left
    (a result, the deadline, or any exception), its finally sends SIGKILL to the child's process group and to the
    child, closes every pipe end, each write end before its read end (so this process still holds the lifeline read
    end when it closes the write end, and the lifeline fires), and reaps the child.

    No exception can come between the fork and that cleanup, in either process. A signal handler of the caller
    runs, and may raise, at a check between bytecodes or inside a call that waits and is interrupted. The pipes
    and the fork are made inside the try, each inside one C-level iteration that also records its result (fds,
    forked) and makes no such wait, so the finally holds every descriptor and the child whenever the call that
    made them returned. In the child, that finally calls os._exit before any bytecode at which a handler can
    run: from the return of os.fork on, nothing raised in the child unwinds into the caller's frames. (Inside
    os.fork, as with any fork, the hooks registered with os.register_at_fork run in the child; CPython reports
    an exception raised there as unraisable and does not propagate it.) The cleanup sends SIGKILL before any
    such bytecode, and each later step sits in its own finally, so a handler that raises again during the
    cleanup cannot skip a step; it can only interrupt the bounded reap, which leaves the killed child unreaped (a zombie
    until this process reaps or exits). An audit hook of the caller (sys.addaudithook) is the exception: os.killpg
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
    an audit hook that refuses os.kill, is reached by neither kill nor the lifeline: the bounded reap's first
    window expires and its retry kill raises at that same hook (or, with the kill silently refused, the run
    reads unreaped); either is a failure, never a pass, with the child left running. A child in
    uninterruptible sleep past both reap deadlines reads unreaped, a named failure, never a reaped child; a run
    that also hung reads "hung; unreaped (...)", so the hang stays visible and the reap failure is attached to
    it, never in its place (a caller that credits a completed run must refuse both forms). A fork copies only
    the calling thread: a lock another thread of the caller holds at the fork stays held in the child, which
    then reads as hung.

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
        if hung and expiring is not None:
            expiring(forked[0])
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
                    status = _reap_within(forked[0], WITNESS_WAIT)
    # A hang stays visible whatever the reap outcome: a hung run whose killed child was not reaped reads
    # "hung; unreaped (...)", never a bare "unreaped" a caller could take for a completed run (_vector_outcome).
    unreaped = ("unreaped (the killed child {} was still {})".format(forked[0], UNREAPED)
                if status is UNREAPED else "")
    if hung:
        return False, "hung; " + unreaped if unreaped else "hung"
    if unreaped:
        return False, unreaped
    if not isinstance(status, int) or not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
        return False, "crashed (child wait status {})".format(status)
    try:
        done, result = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return False, "crashed (unreadable child report)"
    return done, result


def _gate(module, root, timeout=GATE_TIMEOUT, forked=None, expiring=None):
    """Run module.run(root) in a bounded child (_in_child, which records the child's pid in forked, if given, and
    calls expiring with that pid, if given, when the bound overtakes the run), so every patch and mutant in place at
    the call is in force there; returns (exit code, or "hung", "hung; unreaped (...)", "unreaped (...)" or
    "crashed (...)", combined output): an unreaped form names the killed child still unreaped at the reap
    deadline (_in_child) and is never a completed run's exit code."""
    def gated():
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = module.run(root)
        except Exception as exc:
            code = "crashed ({})".format(type(exc).__name__)
        return [code, out.getvalue() + err.getvalue()]
    done, result = _in_child(gated, timeout, forked, expiring)
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


# The syscall numbers this self-test makes through ctypes, per machine (os.uname().machine), as the kernel UAPI
# headers give them: asm/unistd_64.h for x86_64, and asm-generic/unistd.h, which arm64 uses, for aarch64.
# pidfd_getfd(2) duplicates a descriptor of another process into this one; CPython exposes no wrapper for it. The
# open family (open, creat, openat, openat2) is what a gate run blocked opening a FIFO shows in /proc/<pid>/syscall.
# On any other machine the binding is refused (NOT EVALUATED, named) and a hang carries no evidence.
_SYSCALLS = dict(x86_64=dict(pidfd_getfd=438, open=frozenset((2, 85, 257, 437))),
                 aarch64=dict(pidfd_getfd=438, open=frozenset((56, 437))))
_MACHINE = os.uname().machine
_libc = ctypes.CDLL(None, use_errno=True)
# The syscall entry, named so the self-test can refuse a binding without a foreign process.
_syscall = _libc.syscall
# What witness cleanup finds for one candidate pid (_kill_holder): it ended (it had exited, or was sent SIGKILL
# through its pidfd), it is not a writer of the witness, or it could not be evaluated, and is then named.
ENDED, NOT_WRITER, NOT_EVALUATED = "ended", "not a writer", "NOT EVALUATED"
WRITER = "writer"


def _refusal(call, code):
    """The reason a call failed with errno code, as it is named in a vector detail."""
    return "{} failed (errno {} {})".format(call, code, errno.errorcode.get(code, "unknown"))


def _writer_bound(pidfd, fd, dev, ino):
    """Whether descriptor fd of the process pidfd names is the pipe (dev, ino) held for writing, read from one
    open file description: the descriptor is duplicated into this process through the pidfd (pidfd_getfd), and the
    duplicate's identity (os.fstat: st_dev and st_ino) and access mode (fcntl F_GETFL: O_WRONLY or O_RDWR) come
    from that same description, so a descriptor swapped before the duplicate is taken cannot be taken for a writer.
    Returns True or False, or a string, the reason it was not evaluated: the machine is not one whose syscall
    number was verified (_SYSCALLS), pidfd_getfd failed (its errno named), or the duplicate could not be read.
    The syscall runs, and its result is recorded in owned, in one C-level iteration (itertools.starmap consumed by
    list.extend), so no bytecode, and so no signal handler or trace function, runs between the kernel installing
    the duplicate and the finally owning it. The windows that remain leave the duplicate open: a memory error raised
    while ctypes converts the returned descriptor into a Python int, after the kernel has installed it, and an
    exception raised inside the finally before its close (a trace function can raise at any of its instructions)."""
    numbers = _SYSCALLS.get(_MACHINE)
    if numbers is None:
        return "pidfd_getfd number not verified for machine {!r} (binding refused)".format(_MACHINE)
    owned = []
    try:
        owned.extend(itertools.starmap(_syscall, ((ctypes.c_long(numbers["pidfd_getfd"]), ctypes.c_int(pidfd),
                                                   ctypes.c_int(int(fd)), ctypes.c_uint(0)),)))
        if owned[0] < 0:
            return _refusal("pidfd_getfd", ctypes.get_errno())
        info = os.fstat(owned[0])
        mode = fcntl.fcntl(owned[0], fcntl.F_GETFL) & os.O_ACCMODE
    except (OSError, ValueError) as exc:
        return "duplicate unreadable ({})".format(exc)
    finally:
        if owned and owned[0] >= 0:
            os.close(owned[0])
    return info.st_dev == dev and info.st_ino == ino and mode in (os.O_WRONLY, os.O_RDWR)


def _pipe_writer(pidfd, pid, dev, ino):
    """Whether the process pidfd names holds the write end of the pipe (dev, ino), proven by binding: /proc names
    each descriptor that links to the pipe, and each is bound to one open file description through the pidfd
    (_writer_bound), which proves its identity and access mode together. Returns (state, reason): WRITER when a
    bound writer is found; NOT_EVALUATED when none is and /proc/<pid>/fd cannot be listed, a descriptor link cannot
    be read, or a candidate descriptor could not be bound (every reason named); else NOT_WRITER. A descriptor
    closed between the listing and its readlink is skipped: it is no longer held."""
    target = "pipe:[{}]".format(ino)
    try:
        names = os.listdir("/proc/{}/fd".format(pid))
    except OSError as exc:
        return NOT_EVALUATED, "/proc/{}/fd unreadable ({})".format(pid, exc)
    unbound = []
    for name in names:
        try:
            link = os.readlink("/proc/{}/fd/{}".format(pid, name))
        except FileNotFoundError:
            continue
        except OSError as exc:
            unbound.append("fd {} link unreadable ({})".format(name, exc))
            continue
        if link != target:
            continue
        bound = _writer_bound(pidfd, name, dev, ino)
        if bound is True:
            return WRITER, "fd {}".format(name)
        if bound is not False:
            unbound.append("fd {}: {}".format(name, bound))
    if unbound:
        return NOT_EVALUATED, "; ".join(unbound)
    return NOT_WRITER, "no descriptor bound as a writer of the pipe"


def _proc_is_own():
    """None when /proc/self reads as this process's pid (a numeric pid match: os.readlink("/proc/self") equals
    str(os.getpid()), so the pids /proc names are the pids this process sees), else the reason it does not."""
    try:
        link = os.readlink("/proc/self")
    except OSError as exc:
        return "/proc/self unresolvable ({})".format(exc)
    return None if link == str(os.getpid()) else "/proc/self reads {!r}, not this pid".format(link)


# The descendant walk is repeated until two consecutive walks return the same set with no recorded pid's exit
# between them (_descendants); one still unsettled after this many walks is cannot-evaluate (named), never a
# partial candidate list.
SCAN_WALKS = 8

# A task-directory or children-file token can only be a pid when it is a positive, bounded integer: pid_t is a
# C int, so no pid exceeds PID_BOUND, and the bound's width caps the int conversion, so an oversized token can
# never raise and is the walk's named problem, exactly as zero or any other malformed token is.
PID_BOUND = 2 ** 31 - 1


def _pid_token(text):
    """The pid the task or children entry text (str from a listing, bytes from a read) names, or None when it
    is not one: ASCII digits only (no sign, so never a negative), no longer than PID_BOUND is wide, no leading
    zero (the kernel emits only the canonical decimal form, so b"007" is malformed, never 7), and a value
    within 1..PID_BOUND (b"0" is itself a leading zero, so zero is refused by that clause and no dead
    zero-or-below comparison remains, and PID_BOUND + 1 even at the permitted width is refused by the bound;
    leg tokens holds each clause at its boundaries)."""
    if (not (text.isascii() and text.isdigit()) or len(text) > len(str(PID_BOUND))
            or text[:1] in ("0", b"0")):
        return None
    value = int(text)
    return value if value <= PID_BOUND else None


def _token_preview(token):
    """token bounded for a problem message: at most 40 characters with the marker included, a longer one cut
    to 37 and marked with three dots, so the bound is exact (a 40-character token passes whole)."""
    if len(token) <= 40:
        return token
    return token[:37] + (b"..." if isinstance(token, bytes) else "...")


def _pidfd_dead(pidfd):
    """Whether the process the pidfd names has exited (a zombie included): its pidfd then reads ready. Probed
    with select.poll, which unlike select.select has no FD_SETSIZE ceiling, so a pidfd numbered 1024 or higher
    is probed the same way, never a ValueError. The event mask decides, never bare readiness: POLLIN (or
    POLLHUP) is the exit; POLLERR or POLLNVAL is a failed probe, raised as OSError (EBADF for POLLNVAL, else
    EIO), so a closed or broken descriptor can never read as an exit; no event reads as alive. An OSError,
    raised here or by the poll itself, is the caller's to turn into its named problem."""
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    events = poller.poll(0)
    if not events:
        return False
    mask = events[0][1]
    if mask & (select.POLLERR | select.POLLNVAL):
        raise OSError(errno.EBADF if mask & select.POLLNVAL else errno.EIO,
                      "liveness poll read event mask {:#x}, an error, not an exit".format(mask))
    return bool(mask & (select.POLLIN | select.POLLHUP))


def _recorded_alive(found, opened):
    """One pidfd per pid of found that was still alive when this walk recorded it, for the exit test of
    _descendants: (alive pidfds, problem). The pidfds are opened only after the whole walk returned its set,
    never as each children entry is discovered, so each names the process that held the pid at this recording;
    a pid that exited and was reused between its discovery in a children read and this recording binds the
    reusing process (the discovery-to-binding interval, a residual _descendants states). Each descriptor
    pidfd_open returns lands in the caller-owned opened in the same C-level step (map consumed by list.extend,
    as _writer_bound owns its duplicate), so no signal handler or trace function runs between the kernel
    installing it and the list owning it; the caller closes the list once at the end of the whole walk (one
    close site), so none is closed twice or leaked. A pid already gone (ProcessLookupError) held nothing and
    can fork nothing, so its exit is no movement and it is excluded, as is one already exited when recorded (a
    zombie, its pidfd ready). Any other pidfd_open failure, and any liveness-probe failure, is the walk's
    named problem, never a silently skipped exit test. The windows that remain here: a memory error raised
    after the kernel installed the descriptor, while it is converted to a Python int or while the list grows,
    drops that descriptor, left open (a stated residual; _writer_bound states its own windows, which are not
    these)."""
    alive = []
    for pid in sorted(found):
        try:
            opened.extend(map(os.pidfd_open, (pid,)))
        except ProcessLookupError:
            continue
        except OSError as exc:
            return [], "pidfd_open of recorded pid {} failed ({})".format(pid, exc)
        pidfd = opened[-1]
        try:
            dead = _pidfd_dead(pidfd)
        except OSError as exc:
            return [], "liveness probe of recorded pid {} failed ({})".format(pid, exc)
        if not dead:
            alive.append(pidfd)
    return alive, None


def _walk_once(own):
    """One top-down walk from the process own over /proc/<pid>/task/<tid>/children: (found, visited, problem).
    found holds every pid reachable from own's tasks through recorded children; visited holds one (pid, tid)
    pair per task whose children file this walk read, so two walks' visited sets agree only when no such task
    disappeared between them (a task read as exited is in neither; a tid reused within a pid between the
    walks is a residual _descendants states). problem names, as one reason, a read
    the walk could not complete: own's task directory unlistable, a discovered pid's task directory unlistable
    other than by having exited, a children file unreadable or missing while its task directory is still
    present (a kernel without the children interface included), a task-directory probe refused other than by
    the task having exited (only FileNotFoundError and ProcessLookupError mean an exit; an unreadable
    directory cannot establish one), or a task or children entry that is not a pid (_pid_token: zero, an
    oversized number and any other malformed token are the named problem, never an exception); the walk is
    then no candidate list and the caller treats it as cannot-evaluate, never as a silent skip. A discovered
    pid whose task directory is gone exited after it was recorded: a live child it had has been reparented (to
    this child-subreaper process, or to a subreaper below it, itself discovered), where a later walk can find
    it (_descendants holds the acceptance rule and states what it does not prove); the exited pid stays in
    found and holds nothing. Residual: under hidepid=invisible a descendant of another uid has no visible
    /proc entry, reads here as exited, and its subtree is beyond the walk."""
    found, visited, queue = set(), set(), collections.deque((own,))
    while queue:
        pid = queue.popleft()
        try:
            tasks = os.listdir("/proc/{}/task".format(pid))
        except (FileNotFoundError, ProcessLookupError) as exc:
            if pid == own:
                return found, visited, "/proc/{}/task of this process unlistable ({})".format(pid, exc)
            continue
        except OSError as exc:
            return found, visited, "/proc/{}/task unlistable ({})".format(pid, exc)
        for tid in tasks:
            token = _pid_token(str(tid))
            if token is None:
                return found, visited, "/proc/{}/task holds a non-pid entry {!r}".format(
                    pid, _token_preview(str(tid)))
            name = "/proc/{}/task/{}/children".format(pid, tid)
            try:
                with open(name, "rb") as handle:
                    data = handle.read()
            except (FileNotFoundError, ProcessLookupError) as exc:
                # the task exited, or the children interface is missing: told apart by its directory, whose
                # probe reads as exited only for these same two errors; any other refusal is the walk's problem
                try:
                    os.listdir("/proc/{}/task/{}".format(pid, tid))
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError as probe:
                    return found, visited, "/proc/{}/task/{} unlistable ({})".format(pid, tid, probe)
                return found, visited, "{} missing with its task directory present ({})".format(name, exc)
            except OSError as exc:
                return found, visited, "{} unreadable ({})".format(name, exc)
            visited.add((pid, token))
            for part in data.split():
                child = _pid_token(part)
                if child is None:
                    return found, visited, "{} holds a non-pid entry {!r}".format(name, _token_preview(part))
                if child != own and child not in found:
                    found.add(child)
                    queue.append(child)
    return found, visited, None


def _descendants():
    """The live descendants of this process, discovered top-down from this process alone (_walk_once), never
    from a scan of other pids: (found, problem). A walk is accepted only when it returns the same pid set and
    the same visited task set as the walk before it and no exit was observed by the probes (one pidfd per pid
    a walk recorded alive, opened after that walk: _recorded_alive, _pidfd_dead), because the children
    interface is not synchronized and agreement alone proves nothing: a parent's exit mid-walk can move a live
    child, reparented to this child subreaper (or to a discovered subreaper below it), under a children file
    the walk has already read, and two walks then agree while that live child is missing from both (the
    double-fork shape); the exit test reads the parent's exit from its pidfd and refuses the agreement, and
    the walk repeats. The task agreement covers the move no pidfd can observe: children files are per task,
    and a non-leader thread that exits hands its children to a live sibling thread of the same process, no pid
    exiting, so the pid agreement and the exit test both hold while a child so moved is missing from both
    walks; the exited thread's (pid, tid) is then absent from the later walk's visited tasks, the agreement is
    refused, and the walk repeats over the re-read children files. problem names, once, a walk that could not
    be completed (_walk_once), an exit test that could not be made or probed (_recorded_alive, and a liveness
    probe failing at the acceptance), or a walk still unsettled after SCAN_WALKS walks; found is then empty
    and the caller treats the scan as cannot-evaluate, never as a candidate list. No pid outside this process
    and its discovered descendants is read, with one qualification: the pidfds too are opened on discovered
    pids alone, so a pid reused before its recording binds the one process that then holds it (the
    discovery-to-binding interval below). Acceptance is agreement plus no exit observed by the probes, never
    completeness; the races that remain, each stated: a process never recorded alive by the earlier of the two
    agreeing walks can fork a child and exit wholly between that walk's reads and this walk's, hiding the
    child from both while no probe observes the exit; a recorded process can exit in the final-probe interval,
    after the last probe read it alive and before the accepted set is returned; the pidfds are opened only
    after each whole walk (_recorded_alive), so an exit hidden by pid reuse between a children read and that
    recording is observed only where it changes a later walk's pid or task agreement; after tid wrap-around
    a new thread of the same process can take the tid of an exited sibling between the walks, so the visited
    (pid, tid) sets agree while a thread-exit move stays hidden (tid reuse within a pid); a process reparented
    past this process before the walk, or given a descriptor another way, is not a descendant and is beyond
    the walk; and under hidepid=invisible a descendant of another uid is invisible (_walk_once). Witness
    cleanup therefore names a write end still open with no writer killed and nothing named, never ending
    silently (_witnessed)."""
    own, previous, preceding, earlier, opened = os.getpid(), None, None, [], []
    try:
        for _walk in range(SCAN_WALKS):
            found, visited, problem = _walk_once(own)
            if problem is not None:
                return set(), problem
            alive, problem = _recorded_alive(found, opened)
            if problem is not None:
                return set(), problem
            if found == previous and visited == preceding:
                try:
                    exited = any(_pidfd_dead(pidfd) for pidfd in earlier + alive)
                except OSError as exc:
                    return set(), "liveness probe at the walk acceptance failed ({})".format(exc)
                if not exited:
                    return found, None
            previous, preceding, earlier = found, visited, alive
        return set(), "descendant walk unsettled after {} walks".format(SCAN_WALKS)
    finally:
        for pidfd in opened:
            os.close(pidfd)


def _pipe_writers(dev, ino, extra=()):
    """Every process among this process's descendants (_descendants), and the pids in extra, that holds a
    descriptor linking to the pipe (dev, ino): the candidates _kill_holder binds before it signals, found from
    the kernel's descriptor tables, never from what any process reported. Returns (holders, unlisted, problem):
    problem names, once, why the scan could not be made (/proc/self is not this pid, or the descendant walk
    could not be completed, probed or settled: _descendants), and holders and unlisted are then empty, never
    one candidate per unreadable pid. unlisted names, as (pid, reason), every candidate whose /proc/<pid>/fd
    could not be listed (a non-dumpable descendant included) or one of whose descriptor links could not be
    read for any reason other than the descriptor having closed: such a process may hold the pipe unseen, so
    the cleanup must evaluate or name it, never clear it; one that exited between the walk and the read is
    skipped, as it holds nothing. No pid outside the walked descendants and extra is read. Residual, stated:
    a holder that is not a descendant of this process (reparented past it before the walk, or given a
    descriptor another way) is beyond the walk and is found only when the caller names it in extra."""
    target = "pipe:[{}]".format(ino)
    problem = _proc_is_own()
    if problem is not None:
        return [], [], problem
    found, problem = _descendants()
    if problem is not None:
        return [], [], problem
    holders, unlisted = [], []
    for pid in sorted(found.union(int(named) for named in extra)):
        try:
            descriptors = os.listdir("/proc/{}/fd".format(pid))
        except FileNotFoundError:
            continue
        except OSError as exc:
            unlisted.append((pid, "/proc/{}/fd unlistable ({})".format(pid, exc)))
            continue
        for fd in descriptors:
            try:
                link = os.readlink("/proc/{}/fd/{}".format(pid, fd))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError) as exc:
                unlisted.append((pid, "/proc/{}/fd/{} link unreadable ({})".format(pid, fd, exc)))
                break
            if link == target:
                holders.append(pid)
                break
    return holders, unlisted, None


def _kill_holder(pid, pipe):
    """Send SIGKILL to pid only when it is not this process, is alive, and held the write end of pipe at the
    binding, observed through its pidfd: a pidfd opened first names one process and never a pid reused since, and
    each descriptor /proc shows it holding is duplicated through that pidfd (_pipe_writer, _writer_bound), so the
    pipe identity (st_dev and st_ino) and the access mode (O_WRONLY or O_RDWR) are read together from one open
    file description, never a readlink or an fdinfo flag on its own. No unsynchronized check makes that ownership
    atomic with the signal: a process that closes or swaps its write end after the binding and before the signal
    is still signalled (a residual). This process is never signalled, whatever pid names. Returns (state,
    reason): ENDED (it had exited, or SIGKILL was sent through its pidfd), NOT_WRITER (no descriptor it holds
    binds as a writer of the pipe), or NOT_EVALUATED, never signalled: /proc/self is not this pid (a numeric pid
    match), pidfd_open failed other than with ProcessLookupError, /proc/<pid>/fd or a descriptor link could not be
    read, a binding was refused (pidfd_getfd failed, or the machine is unverified) or its duplicate could not be
    read, the liveness probe failed (_pidfd_dead), the pidfd send failed, or pid is this process and holds the
    write end."""
    try:
        problem = _proc_is_own()
        if problem is not None:
            return NOT_EVALUATED, problem
        witness = os.fstat(pipe)
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return ENDED, "no such process"
    except OSError as exc:
        return NOT_EVALUATED, "pidfd_open or the witness unreadable ({})".format(exc)
    try:
        state, reason = _pipe_writer(pidfd, pid, witness.st_dev, witness.st_ino)
        try:
            dead = _pidfd_dead(pidfd)
        except OSError as exc:
            return NOT_EVALUATED, "liveness probe failed ({})".format(exc)
        if dead:
            return ENDED, "exited"
        if state != WRITER:
            return state, reason
        if pid == os.getpid():
            return NOT_EVALUATED, "this process holds the write end ({}); never signalled".format(reason)
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            return ENDED, "exited"
        except OSError as exc:
            return NOT_EVALUATED, "pidfd_send_signal failed ({})".format(exc)
        return ENDED, "SIGKILL sent through its pidfd ({})".format(reason)
    finally:
        os.close(pidfd)


def _kill_confirmed():
    """Whether _kill_holder sends no kill to this process (while it still holds the write end of the pipe, which
    it names NOT_EVALUATED), to a live process that holds only the read end, or to one that holds neither end (each
    NOT_WRITER), and kills a live one that holds the write end through the pidfd it opened before it read /proc,
    with no kill by pid (ENDED): (verdict, detail)."""
    read_end, write_end = os.pipe()
    sleeper = [sys.executable, "-I", "-c", "import time; time.sleep({})".format(HANG_SECONDS)]
    sent, trace, this, states = [], [], sys.modules[__name__], []
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
                        states.append(_kill_holder(pid, read_end)[0])
                with contextlib.ExitStack() as stack:
                    for owner, name, call in calls:
                        stack.enter_context(_patched(owner, name, traced(name, call)))
                    stack.enter_context(_patched(os, "kill", lambda *args: trace.append(("kill",) + args)))
                    states.append(_kill_holder(holder.pid, read_end)[0])
                status = holder.wait()
            finally:
                other.kill()
                reading.kill()
                holder.kill()
        pidfd = next((result for name, _arg, result in trace if name == "pidfd_open"), None)
        through = ([step[:2] for step in trace] == [("pidfd_open", holder.pid), ("_pipe_writer", pidfd),
                                                    ("pidfd_send_signal", pidfd)]
                   and trace[0][2] == pidfd and trace[1][2][0] == WRITER and trace[2][2] is None)
        return (not sent and through and status == -signal.SIGKILL
                and states == [NOT_EVALUATED, NOT_WRITER, NOT_WRITER, ENDED],
                dict(sent=sent, trace=trace, holder=status, states=states))
    finally:
        os.close(read_end)
        os.close(write_end)


def _write_end_open(read_end):
    """Whether some process still holds a write end of the pipe read_end reads: with every write end closed
    the kernel reports POLLHUP on the read end, buffered data or not, so a holder that died just before this
    read is never named."""
    poller = select.poll()
    poller.register(read_end, select.POLLIN)
    return not any(event & select.POLLHUP for _fd, event in poller.poll(0))


def _witnessed(launch, known=(), wait=None):
    """Call launch(witness write end) while this process holds a witness pipe, then close the write end; returns
    (what launch returned, {role: pid} the processes reported, whether every holder of the witness died within
    wait seconds, WITNESS_WAIT by default, and unevaluated). If one did not, witness cleanup takes each pid in known
    first (a list launch fills, as with the pid of a child it forked), then each holder a top-down walk of this
    process's descendants finds among them and the known pids, and each candidate that scan could not complete
    (a walked descendant, or a known pid, whose descriptors it could not list or read: _pipe_writers), and
    kills each that is alive and held the witness write end at the binding, observed through
    its pidfd (_kill_holder): a failing vector leaves behind no writer it could bind at the scan (a holder that
    forks after it can leave its child, and a process that drops its write end after the binding is still
    signalled). unevaluated names, as (pid, reason), every candidate the cleanup could not evaluate (a binding
    refused, an unreadable /proc/<pid>/fd or descriptor link, a failed pidfd_open or pidfd send), which survives
    and is never read as cleared, and as ("/proc scan", reason) a walk that could not be made or settled, named
    once, never one candidate per unreadable pid; the witness read has already read such a holder as live. A
    cleanup that ends with the witness write end still open, no writer killed and nothing named has not
    resolved the holder (discovery can miss a process beyond the walk: _descendants states the residuals); it
    is then named once as ("/proc scan", an unresolved-discovery reason), never ended silently. A
    holder outside this process's descendants and known is beyond the walk (_pipe_writers states the residual).
    What a process reports on the witness never chooses a target: a report can name any pid, this process's own
    included, and this process, which by then holds only the read end, is never signalled (_kill_holder)."""
    read_end, write_end = os.pipe()
    ended, unevaluated = False, []
    try:
        try:
            returned = launch(write_end)
        finally:
            os.close(write_end)
        data, ended = _read_to_eof(read_end, WITNESS_WAIT if wait is None else wait)
        pids = dict((role, int(pid)) for role, pid in (line.split() for line in data.decode("ascii").splitlines()))
    finally:
        try:
            if not ended:
                witness = os.fstat(read_end)
                holders, unlisted, problem = _pipe_writers(witness.st_dev, witness.st_ino, known)
                if problem is not None:
                    unevaluated.append(("/proc scan", problem))
                candidates = itertools.chain(known, holders, (named for named, _why in unlisted))
                killed = []
                for pid in dict.fromkeys(candidates):
                    state, reason = _kill_holder(pid, read_end)
                    if state == NOT_EVALUATED:
                        unevaluated.append((pid, reason))
                    elif state == ENDED and reason.startswith("SIGKILL sent"):
                        killed.append(pid)
                if not killed and not unevaluated and _write_end_open(read_end):
                    unevaluated.append(("/proc scan", "the witness write end is still open after cleanup with "
                                        "no writer killed and nothing named (unresolved discovery): a live "
                                        "holder the walk could not see holds it unseen"))
        finally:
            os.close(read_end)
    return returned, pids, ended, unevaluated


# _reap_within past both of its deadlines: the killed child was still not reaped (compared by identity, never
# read as a wait status or as a reaped child).
UNREAPED = "unreaped at the reap deadline"


def _reap_within(pid, wait):
    """The wait status of the child pid of this process once it ends within wait seconds (checked once more past
    the deadline, so a slow check is not read as a live child); else the child is killed and the reap is retried
    against a second deadline of the same wait: None once the killed child is reaped, UNREAPED when it still was
    not (a child in uninterruptible sleep, or one a caller's SIGCHLD handling took first, can outlive both
    deadlines). Every wait here is a WNOHANG poll against a monotonic deadline, never a blocking waitpid, so no
    caller can block in this helper (leg reap-deadline); a caller reads UNREAPED as its own named failure or
    cannot-evaluate, never as a reaped child. Only the callers of this helper are bounded by it: the
    blocking child waits that remain, the waitpid calls in _reuse_bound, _refused_named, _leftover_children
    and _hang_credit and the Popen waits in _kill_confirmed (holder.wait and its Popen context exits),
    do not use it, and are not claimed bounded here."""
    def polled():
        deadline = time.monotonic() + wait
        while True:
            late = time.monotonic() >= deadline
            reaped, status = os.waitpid(pid, os.WNOHANG)
            if reaped:
                return status
            if late:
                return UNREAPED
            time.sleep(0.01)
    status = polled()
    if status is not UNREAPED:
        return status
    os.kill(pid, signal.SIGKILL)
    return None if polled() is not UNREAPED else UNREAPED


def _ended_by_pid(pid, wait):
    """End the child pid of this process without ever signalling a reaped pid: the exit is first observed
    without reaping (os.waitid with WNOWAIT), the SIGKILL by pid is sent only when the child has not exited
    (the still-unreaped child holds the pid against reuse, so the kill cannot reach another process), and the
    child is then reaped once, bounded (_reap_within): (killed, unreaped): whether the kill was sent, and None
    once the child was reaped, else the named reap failure (leg holder-exited holds the ordering, and leg
    holder-fixture holds it through the real holder fixture). This assumes no concurrent reaper: nothing else
    in this process (a SIGCHLD handler that reaps, SIGCHLD set to SIG_IGN, another thread's wait) may reap
    the child between the observation and the kill; one that does frees the pid, and the kill may then
    reach a reused pid (the self-test installs no such reaper)."""
    exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    killed = exited is None
    if killed:
        os.kill(pid, signal.SIGKILL)
    reaped = _reap_within(pid, wait)
    unreaped = ("child {} was still unreaped at the reap deadline".format(pid)
                if reaped is UNREAPED else None)
    return killed, unreaped


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
    returned = pids = ended = unevaluated = status = None
    try:
        with _patched(signal, "pidfd_send_signal", send):
            returned, pids, ended, unevaluated = _witnessed(launch, wait=WITNESS_TIMEOUT)
    finally:
        if forked:
            status = _reap_within(forked[0], WITNESS_WAIT)
    killed = isinstance(status, int) and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    return (pids == {"child": owner} and ended is False and unevaluated == [] and owner not in aimed
            and returned in aimed and killed,
            dict(owner=owner, child=returned, pids=pids, ended=ended, unevaluated=unevaluated, aimed=aimed,
                 status=status))


def _reuse_bound():
    """Whether witness cleanup binds a holder's write end to one open file description before it signals.
    Case A (descriptor reuse): a child holds only the read end and, the instant the scan has read that
    descriptor's identity, swaps it to a write-only /dev/null; a bound cleanup reads the swapped description,
    which is not the witness, so it signals nothing and reads NOT_WRITER. Case B (/proc not this pid): with
    os.readlink("/proc/self") not reading as this pid, a genuine writer is not signalled and reads NOT_EVALUATED.
    (verdict, detail)."""
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
        detail["swap"] = dict(attempts=attempts, state=survivor, child=child)
        return not attempts and survivor[0] == NOT_WRITER

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
        detail["refused"] = dict(attempts=attempts, state=survivor, child=child)
        return not attempts and survivor[0] == NOT_EVALUATED

    verdict = swap_case() and refused_case()
    return verdict, detail


# prctl(2) options, as <linux/prctl.h> gives them.
PR_SET_CHILD_SUBREAPER, PR_GET_CHILD_SUBREAPER, PR_SET_DUMPABLE, PR_SET_NAME = 36, 37, 4, 15


@contextlib.contextmanager
def _subreaper(problems):
    """Make this process a child subreaper (prctl PR_SET_CHILD_SUBREAPER) while the block runs, so a process
    orphaned below it is reparented to it and stays its descendant, which Yama ptrace_scope 1 requires of a
    pidfd_getfd binding; the prior value, read with PR_GET_CHILD_SUBREAPER, is restored in a finally. Yields True
    when set, else the reason it was not (errno named). The set runs inside the protected region, so an exception
    at any instruction after a successful set still reaches the restore; a set that was refused changed nothing,
    so no restore is attempted after it. The restoring prctl's result is checked, and a restore that fails is
    named in problems (never silent), for the caller to surface as a failure. An adopted orphan that dies stays a
    zombie of this process until this process reaps it or exits."""
    prior, zero = ctypes.c_int(0), ctypes.c_ulong(0)
    if _libc.prctl(ctypes.c_int(PR_GET_CHILD_SUBREAPER), ctypes.byref(prior), zero, zero, zero) != 0:
        yield _refusal("prctl PR_GET_CHILD_SUBREAPER", ctypes.get_errno())
        return
    refused = None
    try:
        if _libc.prctl(ctypes.c_int(PR_SET_CHILD_SUBREAPER), ctypes.c_ulong(1), zero, zero, zero) != 0:
            refused = _refusal("prctl PR_SET_CHILD_SUBREAPER", ctypes.get_errno())
            yield refused
        else:
            yield True
    finally:
        if refused is None and _libc.prctl(ctypes.c_int(PR_SET_CHILD_SUBREAPER), ctypes.c_ulong(prior.value),
                                           zero, zero, zero) != 0:
            problems.append(_refusal("prctl PR_SET_CHILD_SUBREAPER restore", ctypes.get_errno()))


@contextlib.contextmanager
def _surfaced(problems):
    """Name each entry of problems on stderr when the block unwinds with an exception, so a subreaper restore
    failure is named even on a run that never reaches its report; the exception then continues unchanged. On a
    normal exit the caller reports them itself."""
    try:
        yield
    except BaseException:
        for trouble in problems:
            print("subreaper/restore: {!r}".format(trouble), file=sys.stderr)
        raise


def _subreaper_restored():
    """Whether _subreaper restores the prior subreaper value it read, whatever that value (0 and 1 are driven),
    checks its restoring prctl, attempts no restore after a refused set, and protects the set against an
    exception raised before the block is entered, driven over a stand-in for libc that only tracks the value the
    code under test asked it to store (this check never changes the real subreaper state): a refused restore is
    named in problems with the tracked value left set and the refused call asking for the prior value; a refused
    set yields the refusal, changes nothing and is followed by no further prctl call; an exception right after a
    successful set still reaches the restore, which runs and restores the prior value; an exception inside the
    block does too; a clean pass restores the prior value and names nothing. (verdict, {initial: detail})."""
    this = sys.modules[__name__]

    def stand_in(initial, refuse_restore=False, refuse_set=False, raise_after_set=False):
        state = dict(value=initial, sets=0, calls=[])

        def tracked(option, argument, *rest):
            if option.value == PR_GET_CHILD_SUBREAPER:
                argument._obj.value = state["value"]
                state["calls"].append(("get", state["value"]))
                return 0
            asked = argument.value
            state["sets"] += 1
            if (refuse_set and state["sets"] == 1) or (refuse_restore and state["sets"] > 1):
                state["calls"].append(("refused", asked))
                ctypes.set_errno(errno.EPERM)
                return -1
            state["value"] = asked
            state["calls"].append(("set", asked))
            if raise_after_set and state["sets"] == 1:
                raise RuntimeError("raised by the self-test right after the set")
            return 0
        return state, types.SimpleNamespace(prctl=tracked)

    def run(initial, body_raises=False, **how):
        state, libc = stand_in(initial, **how)
        problems, seen = [], []
        with _patched(this, "_libc", libc):
            try:
                with _subreaper(problems) as adopted:
                    seen.append(adopted)
                    if body_raises:
                        raise RuntimeError("raised by the self-test inside the block")
            except RuntimeError as exc:
                seen.append(str(exc))
        return dict(state, problems=problems, seen=seen)

    def held(initial):
        clean = run(initial)
        refused = run(initial, refuse_restore=True)
        unset = run(initial, refuse_set=True)
        window = run(initial, raise_after_set=True)
        unwound = run(initial, body_raises=True)
        through = [("get", initial), ("set", 1), ("set", initial)]
        ok = (clean["value"] == initial and clean["seen"] == [True] and not clean["problems"]
              and clean["calls"] == through
              and refused["value"] == 1 and refused["seen"] == [True] and len(refused["problems"]) == 1
              and "restore" in refused["problems"][0] and "EPERM" in refused["problems"][0]
              and refused["calls"] == [("get", initial), ("set", 1), ("refused", initial)]
              and unset["value"] == initial and not unset["problems"] and len(unset["seen"]) == 1
              and isinstance(unset["seen"][0], str) and "PR_SET_CHILD_SUBREAPER" in unset["seen"][0]
              and unset["calls"] == [("get", initial), ("refused", 1)]
              and window["value"] == initial and window["calls"] == through and not window["problems"]
              and window["seen"] == ["raised by the self-test right after the set"]
              and unwound["value"] == initial and unwound["calls"] == through
              and len(unwound["seen"]) == 2 and not unwound["problems"])
        return ok, dict(clean=clean, refused=refused, unset=unset, window=window, unwound=unwound)
    outcomes = dict((initial, held(initial)) for initial in (0, 1))
    return all(ok for ok, _detail in outcomes.values()), outcomes


def _restore_surfaced():
    """Whether self_test names a recorded restore problem on stderr when its run unwinds with an exception at
    any point up to and including the final report, and the exception then continues unchanged, driven over a
    stand-in run that records a problem and then raises (self_test's _surfaced wrapper encloses the entire
    run, so one raise stands for an exception at any instruction of it, the report included): (verdict,
    detail)."""
    this, mark = sys.modules[__name__], "injected restore problem"
    note = "raised by the self-test after the problem was recorded"

    def stand_in(problems, report_path):
        problems.append(mark)
        raise RuntimeError(note)
    captured, raised = io.StringIO(), []
    try:
        with _patched(this, "_self_test", stand_in), contextlib.redirect_stderr(captured):
            self_test()
    except RuntimeError as exc:
        raised.append(str(exc))
    named = "subreaper/restore: {!r}".format(mark) in captured.getvalue()
    return raised == [note] and named, dict(raised=raised, named=named, stderr=captured.getvalue())


def _pidfd_pid(pidfd):
    """The pid a pidfd of this process names, from its fdinfo; 0 when it names none."""
    with open("/proc/self/fdinfo/{}".format(pidfd), encoding="ascii") as info:
        return int(next((line.split()[1] for line in info if line.startswith("Pid:")), "0"))


def _bindable(pid):
    """Whether this process can bind a descriptor of the process pid: /proc/<pid>/fd is listable, a descriptor
    link is readable, and pidfd_getfd duplicates that descriptor (the duplicate is closed at once). False when
    any of those reads is refused: the holder can then only be found through the descendant walk and named.
    None when the probe could not be made (no verified syscall number for this machine, no descriptor to
    probe, or pidfd_open failed): a fixture setup failure, read as cannot-evaluate, never as either branch."""
    numbers = _SYSCALLS.get(_MACHINE)
    if numbers is None:
        return None
    try:
        names = os.listdir("/proc/{}/fd".format(pid))
    except OSError:
        return False
    if not names:
        return None
    try:
        pidfd = os.pidfd_open(pid)
    except OSError:
        return None
    try:
        try:
            os.readlink("/proc/{}/fd/{}".format(pid, names[0]))
        except OSError:
            return False
        taken = _syscall(ctypes.c_long(numbers["pidfd_getfd"]), ctypes.c_int(pidfd),
                         ctypes.c_int(int(names[0])), ctypes.c_uint(0))
        if taken < 0:
            return False
        os.close(taken)
        return True
    finally:
        os.close(pidfd)


def _refused_named(adopted):
    """Whether witness cleanup names every candidate it cannot evaluate, never signals one it has not bound, and
    the witnessed verdict then reads False: (verdict, detail). In each leg a forked process holds the witness
    write end and sleeps, and _witnessed runs its cleanup over it. Leg refused: pidfd_getfd refused with EPERM
    for that holder (injected through _syscall). Leg machine: a machine whose syscall number is unverified. Leg
    foreign: /proc/self unresolvable, which names the /proc scan too. Leg unreadable: /proc/<holder>/fd cannot be
    listed. Leg link: the holder's descriptor links cannot be read. Leg duplicate: F_GETFL on a duplicate fails.
    Leg stat: fstat on the duplicate fails. Leg pidfd: pidfd_open of the holder fails with EACCES. Each names the
    holder in unevaluated with its reason, sends it no signal, and _witness_verdict reads False on the run, and on
    a run clean but for that unevaluated entry. Leg send: pidfd_send_signal fails after the binding; the one send
    is aimed at the holder, which survives and is named. Legs scanfd and scanlink: a holder outside known, found
    only by the scan, whose /proc/<pid>/fd listing (scanfd) or descriptor readlink (scanlink) is refused by an
    injected refusal, verified to have fired: each such holder must be named through the scan, never left unnamed
    and alive. Legs unlisted and comm: a non-dumpable holder outside known (comm also bears a process name that
    is not ASCII, verified from its stat bytes, and has its fd listing refused by an injected refusal, verified
    to have fired past the probe's own hit, so only the descendant walk can make it a candidate, on every
    host), found only as a walked descendant; each reads its preconditions (the PR_SET_DUMPABLE result, and
    whether this process can bind the holder's descriptors: _bindable) and requires the branch they imply:
    with the descriptors not bindable from here the holder must be named, and with them bindable (a privileged
    run) the holder must be bound and killed; a fixture whose setup did not succeed, or whose probe could not
    be made, is cannot-evaluate, never read as either branch. The comm probe runs inside that leg's injected
    refusal and reads it, so comm takes the non-bindable branch on every host, and the injection's first hit
    is the probe's own. Leg undumped: the PR_SET_DUMPABLE call itself is refused through a libc stand-in, so
    the setup guard must read the fixture as cannot-evaluate, never as either branch. Leg unresolved:
    _pipe_writers is replaced to report an empty, problem-free scan while a forked holder outside known keeps
    the witness write end open; the cleanup must name that once as a ("/proc scan", unresolved-discovery)
    entry, send nothing, and read False. Leg orphan: the holder is a grandchild whose parent has
    exited, found only by the scan; with this process a child subreaper (adopted is True) it is reparented here
    and stays a descendant, so it is bound and killed; otherwise it is named or killed, never left unnamed."""
    this, detail = sys.modules[__name__], {}
    clean = dict(result=("hung", ""), elapsed=WITNESS_TIMEOUT, pid=1, pids={"child": 1}, ended=True, reaped=True,
                 unevaluated=[])

    def leg(orphan, patches, known=True, undumpable=False, breaks=None, name_bytes=None):
        held, holder, sent, sender = [], [], [], signal.pidfd_send_signal
        setup, listable, renamed = [], [], []

        def send(pidfd, signum):
            sent.append(_pidfd_pid(pidfd))
            if breaks is not None and sent[-1] in holder:
                raise breaks
            return sender(pidfd, signum)

        def launch(witness):
            pid_r, pid_w = os.pipe()
            try:
                held.append(os.fork())
                if held == [0]:
                    try:
                        os.close(pid_r)
                        if not orphan or os.fork() == 0:
                            report = [os.getpid()]
                            if name_bytes is not None:
                                _libc.prctl(ctypes.c_int(PR_SET_NAME), ctypes.c_char_p(name_bytes),
                                            ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))
                            if undumpable:
                                report.append(_libc.prctl(ctypes.c_int(PR_SET_DUMPABLE), ctypes.c_ulong(0),
                                                          ctypes.c_ulong(0), ctypes.c_ulong(0),
                                                          ctypes.c_ulong(0)))
                            os.write(pid_w, " ".join(str(part) for part in report).encode("ascii") + b"\n")
                            time.sleep(HANG_SECONDS)
                    finally:
                        os._exit(0)
            finally:
                os.close(pid_w)
            try:
                if select.select([pid_r], [], [], WITNESS_WAIT)[0]:
                    reported = [int(part) for part in os.read(pid_r, READ_CHUNK).split()]
                    holder.extend(reported[:1])
                    setup.extend(reported[1:])
            finally:
                os.close(pid_r)
            for pid in (holder if undumpable else ()):
                listable.append(_bindable(pid))
            for pid in (holder if name_bytes is not None else ()):
                with open("/proc/{}/stat".format(pid), "rb") as handle:
                    renamed.append(name_bytes in handle.read())
            if orphan:
                os.waitpid(held[0], 0)
        result, died = None, None
        try:
            with contextlib.ExitStack() as stack:
                for owner, attribute, replacement in patches(holder):
                    stack.enter_context(_patched(owner, attribute, replacement))
                stack.enter_context(_patched(signal, "pidfd_send_signal", send))
                result = _witnessed(launch, held if known and not orphan else (), wait=WITNESS_TIMEOUT)
        finally:
            for pid in holder[:1]:
                try:
                    pidfd = os.pidfd_open(pid)
                except ProcessLookupError:
                    died = True
                    continue
                try:
                    died = bool(select.select([pidfd], [], [], WITNESS_WAIT if orphan else 0)[0])
                    if not died:
                        sender(pidfd, signal.SIGKILL)
                finally:
                    os.close(pidfd)
            for pid in dict.fromkeys(holder[:1] + held[:1]):
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, 0)
        pid = holder[0] if holder else None
        unevaluated = result[3] if result else []
        reasons = [reason for named, reason in unevaluated if named == pid]
        run = dict(clean, pid=pid, pids=result[1] if result else {}, ended=result[2] if result else None,
                   unevaluated=unevaluated)
        return dict(holder=pid, sent=sent, died=died, unevaluated=unevaluated, reasons=reasons,
                    setup=setup, listable=listable, renamed=renamed,
                    verdicts=[_witness_verdict(run, ("hung", ""), None, {"child"}),
                              _witness_verdict(dict(clean, unevaluated=unevaluated), ("hung", ""), None, {"child"}),
                              _witness_verdict(clean, ("hung", ""), None, {"child"})])

    def refusing(holder):
        def syscall(number, pidfd, fd, flags):
            if _pidfd_pid(pidfd.value) in holder:
                ctypes.set_errno(errno.EPERM)
                return -1
            return _libc.syscall(number, pidfd, fd, flags)
        return [(this, "_syscall", syscall)]

    def failing(owner, name, fails, hits=None):
        # owner.name, raising PermissionError (EACCES) whenever fails accepts its arguments; each refusal is
        # recorded in hits when a list is given, so an injected refusal is verified to have fired
        real = getattr(owner, name)

        def call(*args):
            if fails(*args):
                if hits is not None:
                    hits.append(args)
                raise PermissionError(errno.EACCES, "refused by the self-test")
            return real(*args)
        return [(owner, name, call)]

    def duplicated(holder):
        # records the descriptors pidfd_getfd installs for the holder's binding, so only the duplicate's own
        # fstat fails, never the witness's
        taken = []

        def syscall(number, pidfd, fd, flags):
            result = _libc.syscall(number, pidfd, fd, flags)
            if result >= 0 and _pidfd_pid(pidfd.value) in holder:
                taken.append(result)
            return result
        return [(this, "_syscall", syscall)] + failing(os, "fstat", lambda fd: fd in taken)
    legs = dict(
        refused=(refusing, "errno {} EPERM".format(errno.EPERM)),
        machine=(lambda holder: [(this, "_MACHINE", "unverified-machine")], "not verified"),
        foreign=(lambda holder: failing(os, "readlink", lambda path: path == "/proc/self"), "unresolvable"),
        unreadable=(lambda holder: failing(os, "listdir", lambda path=".": str(path) in [
            "/proc/{}/fd".format(pid) for pid in holder]), "fd unreadable"),
        link=(lambda holder: failing(os, "readlink", lambda path: str(path).startswith(
            tuple("/proc/{}/fd/".format(pid) for pid in holder))), "link unreadable"),
        duplicate=(lambda holder: failing(fcntl, "fcntl", lambda fd, cmd, *rest: cmd == fcntl.F_GETFL),
                   "duplicate unreadable"),
        stat=(duplicated, "duplicate unreadable"),
        pidfd=(lambda holder: failing(os, "pidfd_open", lambda pid, *rest: pid in holder), "pidfd_open"))
    for name, (patches, _mark) in legs.items():
        detail[name] = leg(False, patches)
    detail["send"] = leg(False, lambda holder: [],
                         breaks=PermissionError(errno.EPERM, "refused by the self-test"))
    scan_hits = dict(comm=[], scanfd=[], scanlink=[])
    detail["unlisted"] = leg(False, lambda holder: [], known=False, undumpable=True)
    detail["comm"] = leg(False, lambda holder: failing(os, "listdir", lambda path=".": str(path) in [
        "/proc/{}/fd".format(pid) for pid in holder], scan_hits["comm"]), known=False, undumpable=True,
        name_bytes=b"qa-\xff\xf0")
    detail["scanfd"] = leg(False, lambda holder: failing(os, "listdir", lambda path=".": str(path) in [
        "/proc/{}/fd".format(pid) for pid in holder], scan_hits["scanfd"]), known=False)
    detail["scanlink"] = leg(False, lambda holder: failing(os, "readlink", lambda path: str(path).startswith(
        tuple("/proc/{}/fd/".format(pid) for pid in holder)), scan_hits["scanlink"]), known=False)
    detail["orphan"] = leg(True, lambda holder: [])
    detail["unresolved"] = leg(False, lambda holder: [(this, "_pipe_writers",
                                                       lambda dev, ino, extra=(): ([], [], None))], known=False)
    real_libc = _libc

    def undumpable_refused(holder):
        def prctl(option, *rest):
            if option.value == PR_SET_DUMPABLE:
                ctypes.set_errno(errno.EPERM)
                return -1
            return real_libc.prctl(option, *rest)
        return [(this, "_libc", types.SimpleNamespace(prctl=prctl, syscall=real_libc.syscall))]
    detail["undumped"] = leg(False, undumpable_refused, known=False, undumpable=True)
    named = all(detail[name]["reasons"] and not detail[name]["sent"] and detail[name]["died"] is False
                and detail[name]["verdicts"] == [False, False, True] for name in legs)
    marks = [mark in "".join(detail[name]["reasons"]) for name, (_patches, mark) in legs.items()]
    marks.append("/proc scan" in [who for who, _reason in detail["foreign"]["unevaluated"]])
    send = detail["send"]
    sending = ("pidfd_send_signal failed" in "".join(send["reasons"]) and send["sent"] == [send["holder"]]
               and send["died"] is False and send["verdicts"] == [False, False, True])

    def hidden_ok(record):
        # the branch the read preconditions imply, never just the branch the outcome happened to take; a
        # fixture whose PR_SET_DUMPABLE setup did not succeed, or whose bindability probe could not be made,
        # is cannot-evaluate, never read as either branch
        if record["setup"] != [0] or record["listable"] not in ([True], [False]):
            return None
        if record["listable"] == [False]:
            return (bool(record["reasons"]) and not record["sent"] and record["died"] is False
                    and record["verdicts"] == [False, False, True])
        return record["died"] is True and record["sent"] == [record["holder"]] and not record["reasons"]
    scoped = _combined([hidden_ok(detail["unlisted"]), hidden_ok(detail["comm"]),
                        detail["comm"]["renamed"] == [True], len(scan_hits["comm"]) >= 2])
    injected = all(bool(scan_hits[name]) and mark in "".join(detail[name]["reasons"])
                   and not detail[name]["sent"] and detail[name]["died"] is False
                   and detail[name]["verdicts"] == [False, False, True]
                   for name, mark in (("comm", "fd unreadable"), ("scanfd", "fd unreadable"),
                                      ("scanlink", "link unreadable")))
    # the setup guard, discriminated: with the PR_SET_DUMPABLE call itself refused the fixture must read as
    # cannot-evaluate, never as either branch
    guarded = hidden_ok(detail["undumped"]) is None and detail["undumped"]["setup"] == [-1]
    unresolved = detail["unresolved"]
    # a cleanup whose scan reported nothing while the write end stayed open must name the unresolved
    # discovery once, send nothing, and read False, never end silently
    discovery = (len(unresolved["unevaluated"]) == 1 and unresolved["unevaluated"][0][0] == "/proc scan"
                 and "unresolved discovery" in unresolved["unevaluated"][0][1] and not unresolved["sent"]
                 and unresolved["died"] is False and unresolved["verdicts"] == [False, False, True])
    orphan = detail["orphan"]
    if adopted is True:
        reaped = orphan["died"] is True and not orphan["reasons"] and orphan["sent"] == [orphan["holder"]]
    else:
        reaped = orphan["died"] is True or bool(orphan["reasons"])
    return (_combined([named, all(marks), sending, scoped, injected, guarded, discovery, reaped]),
            dict(detail, adopted=adopted, scan_hits=scan_hits))


_WALK_ABSENT = object()

# The scripted walk legs' fake pids sit at and past this bound, past any host's pid ceiling (the kernel's
# PID_MAX_LIMIT is 4194304), so a real call that reaches one (os.pidfd_open, a /proc path left unscripted) can
# only read no such process, never a live pid.
FAKE_PID_BASE = 5000000


def _walk_stub(table, own):
    """os.listdir and open stand-ins for one scripted /proc subtree: table maps a walk ordinal (counted at each
    listing of the process own's task directory; later walks reuse the last entry) to {path: a list to return,
    bytes to read, or an exception to raise}; every other path reaches the real call. Returns (listing,
    opened, state), state counting the walks seen in state["walk"]."""
    state = dict(walk=0)
    real_listdir, real_open = os.listdir, builtins.open

    def lookup(path):
        if str(path) == "/proc/{}/task".format(own):
            state["walk"] += 1
        return table[max(1, min(state["walk"], max(table)))].get(str(path), _WALK_ABSENT)

    def listing(path="."):
        got = lookup(path)
        if got is _WALK_ABSENT:
            return real_listdir(path)
        if isinstance(got, BaseException):
            raise got
        return list(got)

    def opened(path, *args, **kwargs):
        got = lookup(path)
        if got is _WALK_ABSENT:
            return real_open(path, *args, **kwargs)
        if isinstance(got, BaseException):
            raise got
        return io.BytesIO(got)
    return listing, opened, state


def _walk_scripted(table):
    """_descendants run over the scripted /proc subtree table (_walk_stub) for this process: ((found, problem),
    walks seen)."""
    return _walk_patched(table, ())


def _walk_patched(table, patches):
    """_descendants over the scripted /proc subtree table (_walk_stub) with the extra (owner, name,
    replacement) attribute patches installed: ((found, problem), walks seen)."""
    listing, opened, state = _walk_stub(table, os.getpid())
    with contextlib.ExitStack() as stack:
        stack.enter_context(_patched(os, "listdir", listing))
        stack.enter_context(_patched(builtins, "open", opened))
        for owner, name, replacement in patches:
            stack.enter_context(_patched(owner, name, replacement))
        result = _descendants()
    return result, state["walk"]


def _scan_broken_witnessed(table, patches, marks):
    """_witnessed over a real forked holder of the witness write end while the descendant walk is broken by the
    scripted /proc subtree table and the extra attribute patches, no known pid given: the cleanup must name the
    broken scan once, as exactly one ("/proc scan", reason) entry bearing every mark in marks, derive no
    candidate from the incomplete walk and send no signal; the holder survives until this leg reaps it:
    (ok, detail)."""
    listing, opened, _state = _walk_stub(table, os.getpid())
    sent, forked = [], []

    def send(pidfd, signum):
        sent.append(_pidfd_pid(pidfd))

    def launch(witness):
        ready_r, ready_w = os.pipe()
        forked.append(os.fork())
        if forked == [0]:
            try:
                os.write(witness, "child {}\n".format(os.getpid()).encode("ascii"))
                os.write(ready_w, b"1")
                time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        os.close(ready_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        return forked[0]
    result = status = None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(_patched(os, "listdir", listing))
            stack.enter_context(_patched(builtins, "open", opened))
            stack.enter_context(_patched(signal, "pidfd_send_signal", send))
            for owner, name, replacement in patches:
                stack.enter_context(_patched(owner, name, replacement))
            result = _witnessed(launch, wait=WITNESS_TIMEOUT)
    finally:
        if forked:
            status = _reap_within(forked[0], WITNESS_WAIT)
    unevaluated = result[3] if result else None
    ok = (result is not None and result[2] is False and sent == [] and status is None
          and unevaluated is not None and len(unevaluated) == 1 and unevaluated[0][0] == "/proc scan"
          and all(mark in unevaluated[0][1] for mark in marks))
    return ok, dict(child=forked, reported=result and result[1], ended=result and result[2],
                    unevaluated=unevaluated, sent=sent, status=status, marks=marks)


def _broken_walk_witnessed(probe_error):
    """The scripted task-directory probe refusal (probe_error: the children file reads absent and the directory
    probe raises) driven end to end through a real witnessed cleanup (_scan_broken_witnessed), the one scan
    entry naming the probe."""
    own = os.getpid()
    table = {1: {"/proc/{}/task".format(own): [str(own)],
                 "/proc/{0}/task/{0}/children".format(own): FileNotFoundError(errno.ENOENT, "absent"),
                 "/proc/{0}/task/{0}".format(own): probe_error}}
    return _scan_broken_witnessed(table, (), ("unlistable", probe_error.strerror or str(probe_error)))


def _double_fork_verdict(state, a_pid, found, problem):
    """The verdict one double-fork attempt earns from its recorded state (every input is recorded before this
    reads it, so the decision is deterministic and held by its own leg, fork-verdict): None, retried by
    _attempted, when the double fork was not exercised (no D forked, or the reparenting wait failed) or when
    the timing precondition was missed (B already exited when the walk 2 hook fired, or D already visible in
    the children bytes that read returned: the intended race was not established, so a walk that found D
    early is no failure); else whether the walk named no problem and found D and A in at least four walks."""
    detail = dict(state, a=a_pid, found=sorted(found), problem=problem)
    if not (state["d"] and state["exited"] is True):
        return None, dict(detail, note="the double fork was not exercised")
    if state["early"] or not state["b_alive"]:
        return None, dict(detail, note="the timing precondition was missed (B already exited when walk 2's "
                                       "hook fired, or D already visible to walk 2): not the intended race")
    return (problem is None and state["d"] in found and a_pid in found and state["walk"] >= 4), detail


def _double_fork_found():
    """Whether the live hook-timed double fork is still found: (verdict, detail). A child A forks a grandchild
    B, which holds nothing and sleeps; two hooks pause the walk, each after a real read. Walk 1 has read B's
    (empty) children file when B forks D; walk 2 has read this process's own children file when B exits, so D,
    reparented to this child subreaper, has moved under a children file walk 2 already read: walks 1 and 2
    then agree while the live D is missing from both, and the exit test (B, recorded alive by walk 1, exited
    before the end of walk 2) must refuse the agreement and walk on until D is recorded. Every byte the walk
    reads is the kernel's own; the hooks only choose when B forks and when it exits. None (cannot evaluate,
    retried by _attempted) when a bounded handshake or the reparenting wait timed out, or when the timing
    precondition was missed: B already exited (a zombie or gone) when walk 2's hook fired, or D already
    visible in the children bytes that read returned, so the intended race was not established and a walk that
    found D early is no failure; host scheduling therefore never fails this leg. The four-walk requirement is
    read only once the race is established, and a run whose hooks both fired on the established race and whose
    walk still misses D is False. Every cleanup reap here is bounded (_reap_within, never a blocking waitpid),
    and every cleanup kill or reap that raises, or a killed pid still unreaped at its deadline, is recorded
    with its pid in the detail (cleanup), never suppressed; the "B never reported" attempt keeps those
    findings too. The walk verdict (_double_fork_verdict) is decided first and cleanup uncertainty can only
    turn its True into None (retried, never read as reaped): a definite False stays False, so a failed walk
    is never retried into success (leg fork-cleanup)."""
    own = os.getpid()
    cmd_r, cmd_w = os.pipe()
    rep_r, rep_w = os.pipe()

    def waited(fd):
        return os.read(fd, 32) if select.select([fd], [], [], WITNESS_WAIT)[0] else b""
    state = dict(b=0, d=0, walk=0, exited=None, early=None, b_alive=None)
    a_pid = os.fork()
    if a_pid == 0:
        try:
            if os.fork() == 0:
                os.write(rep_w, "{}\n".format(os.getpid()).encode("ascii"))
                if waited(cmd_r) == b"f":
                    child = os.fork()
                    if child == 0:
                        time.sleep(HANG_SECONDS)
                    else:
                        os.write(rep_w, "{}\n".format(child).encode("ascii"))
                        waited(cmd_r)
            else:
                time.sleep(HANG_SECONDS)
        finally:
            os._exit(0)
    os.close(cmd_r)
    os.close(rep_w)
    real_listdir, real_open = os.listdir, builtins.open
    found = problem = None
    trouble = []
    try:
        line = waited(rep_r)
        state["b"] = int(line) if line else 0
        own_children = "/proc/{0}/task/{0}/children".format(own)
        b_children = "/proc/{0}/task/{0}/children".format(state["b"])

        def reparented(pid):
            deadline = time.monotonic() + WITNESS_WAIT
            while time.monotonic() < deadline:
                with real_open("/proc/{}/stat".format(pid), "rb") as handle:
                    fields = handle.read().rsplit(b")", 1)[1].split()
                if int(fields[1]) == own:
                    return True
                time.sleep(0.005)
            return False

        def still_alive(pid):
            # a zombie (state Z) or a gone /proc entry reads as exited: the race window was already missed
            try:
                with real_open("/proc/{}/stat".format(pid), "rb") as handle:
                    return handle.read().rsplit(b")", 1)[1].split()[0] != b"Z"
            except OSError:
                return False

        def listing(path="."):
            if str(path) == "/proc/{}/task".format(own):
                state["walk"] += 1
            return real_listdir(path)

        def opened(path, *args, **kwargs):
            name = str(path)
            if name not in (own_children, b_children):
                return real_open(path, *args, **kwargs)
            with real_open(path, *args, **kwargs) as handle:
                data = handle.read()
            if name == b_children and state["walk"] == 1 and not state["d"]:
                os.write(cmd_w, b"f")
                reply = waited(rep_r)
                state["d"] = int(reply) if reply else 0
            elif name == own_children and state["walk"] == 2 and state["d"] and state["exited"] is None:
                state["early"] = str(state["d"]).encode("ascii") in data.split()
                state["b_alive"] = still_alive(state["b"])
                os.write(cmd_w, b"x")
                state["exited"] = reparented(state["d"])
            return io.BytesIO(data)
        if state["b"]:
            with _patched(os, "listdir", listing), _patched(builtins, "open", opened):
                found, problem = _descendants()
    finally:
        os.close(cmd_w)
        os.close(rep_r)
        for pid in (state["d"], a_pid):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError as exc:
                    trouble.append("kill of pid {} failed ({!r})".format(pid, exc))
        for pid in (a_pid, state["b"], state["d"]):
            if pid:
                try:
                    if _reap_within(pid, WITNESS_WAIT) is UNREAPED:
                        trouble.append("killed pid {} was still {}".format(pid, UNREAPED))
                except OSError as exc:
                    trouble.append("reap of pid {} failed ({!r})".format(pid, exc))
    verdict, detail = (_double_fork_verdict(state, a_pid, found, problem) if state["b"]
                       else (None, dict(state, a=a_pid, note="B never reported")))
    if trouble:
        detail = dict(detail, cleanup=trouble)
        if verdict is True:
            verdict = None
            detail["note"] = "the walk held, but its cleanup is unestablished: the attempt cannot be evaluated"
    return verdict, detail


def _double_fork_cleanup_named():
    """The cleanup of the live double fork combined with its walk verdict (_double_fork_found), over real
    processes with only the verdict and the first cleanup reap scripted: _double_fork_verdict is wrapped to
    judge an established race (D forked and reparented, B alive at walk 2's hook, D not early, four walks)
    over a scripted found set, and the first cleanup reap (of A) runs for real before its result is replaced,
    so no child is left behind. Case eio: a held walk ({A, D} found) whose first reap raises EIO is None
    with that reap named with A's pid, never True (the reap error suppressed into a pass is the regression).
    Case missing: a walk missing D ({A} found) whose first reap reads UNREAPED stays False with A named
    unreaped, and through _attempted, whose next attempt is scripted to hold, it is decided at the first
    attempt, never retried into success. An attempt in which B never reported exercised neither, and the
    leg is then None: (ok, detail)."""
    this, real_verdict, real_reap = sys.modules[__name__], _double_fork_verdict, _reap_within

    def driven(missing, failure):
        reaps = []

        def judged(state, a_pid, found, problem):
            raced = dict(state, d=state["d"] or FAKE_PID_BASE + 2, walk=max(state["walk"], 4), exited=True,
                         early=False, b_alive=True)
            return real_verdict(raced, a_pid, {a_pid} if missing else {a_pid, raced["d"]}, None)

        def reaping(pid, wait):
            status = real_reap(pid, wait)
            reaps.append(pid)
            if len(reaps) > 1:
                return status
            if failure == "eio":
                raise OSError(errno.EIO, "io error of the self-test")
            return UNREAPED
        with _patched(this, "_double_fork_verdict", judged), _patched(this, "_reap_within", reaping):
            return _double_fork_found()
    eio = driven(False, "eio")
    attempts = iter((lambda: driven(True, "unreaped"), lambda: (True, "a retry scripted to hold")))
    missing = _attempted(lambda: next(attempts)())
    first = missing[1][0]
    if not (eio[1].get("b") and isinstance(first, dict) and first.get("b")):
        return None, dict(eio=eio, missing=missing, note="B never reported: the cleanup was not exercised")
    ok = (eio[0] is None and "reap of pid {} failed".format(eio[1]["a"]) in str(eio[1].get("cleanup"))
          and "io error of the self-test" in str(eio[1].get("cleanup"))
          and missing[0] is False and len(missing[1]) == 1
          and "killed pid {} was still {}".format(first["a"], UNREAPED) in str(first.get("cleanup")))
    return ok, dict(eio=eio, missing=missing)


def _unrelated_unread():
    """Whether cleanup of a real holder reads no unrelated pid (the refused-init-stat shape): with /proc/1/stat
    refused by an injected refusal, every /proc/<pid>/stat open and every /proc/<pid>/fd listing recorded, the
    holder is still found through the descendant walk and killed, nothing is named, the injection never fires
    (no stat is opened at all), and every recorded descriptor listing is of a walked descendant, so one
    unreadable pid outside them can never become candidates: (verdict, detail)."""
    this = sys.modules[__name__]
    stat_opens, fd_lists, walks = [], [], []
    real_open, real_listdir, real_walk = builtins.open, os.listdir, _descendants

    def names(path, last):
        parts = str(path).split("/")
        return len(parts) == 4 and parts[1] == "proc" and parts[2].isdigit() and parts[3] == last

    def opened(path, *args, **kwargs):
        if names(path, "stat"):
            stat_opens.append(str(path))
            if str(path) == "/proc/1/stat":
                raise PermissionError(errno.EACCES, "refused by the self-test")
        return real_open(path, *args, **kwargs)

    def listing(path="."):
        if names(path, "fd"):
            fd_lists.append(int(str(path).split("/")[2]))
        return real_listdir(path)

    def walking():
        walks.append(real_walk())
        return walks[-1]
    forked = []

    def launch(witness):
        ready_r, ready_w = os.pipe()
        forked.append(os.fork())
        if forked == [0]:
            try:
                os.write(witness, "child {}\n".format(os.getpid()).encode("ascii"))
                os.write(ready_w, b"1")
                time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        os.close(ready_w)
        os.read(ready_r, 1)
        os.close(ready_r)
        return forked[0]
    status = result = None
    try:
        with (_patched(builtins, "open", opened), _patched(os, "listdir", listing),
              _patched(this, "_descendants", walking)):
            result = _witnessed(launch, wait=WITNESS_TIMEOUT)
    finally:
        if forked:
            status = _reap_within(forked[0], WITNESS_WAIT)
    killed = isinstance(status, int) and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    scope = set().union(*(found for found, _problem in walks)) if walks else set()
    ok = (result is not None and result[2] is False and result[3] == [] and killed and stat_opens == []
          and bool(walks) and all(problem is None for _found, problem in walks)
          and set(fd_lists) <= scope and bool(forked) and forked[0] in set(fd_lists))
    return ok, dict(child=forked, reported=result and result[1], ended=result and result[2],
                    unevaluated=result and result[3], killed=killed, status=status, stat_opens=stat_opens,
                    fd_lists=sorted(set(fd_lists)), scope=sorted(scope))


def _scripted_probes(opens, probes):
    """Patches scripting the exit test over fake pids (FAKE_PID_BASE and past): os.pidfd_open of a fake pid
    consumes the next entry of opens (an exception instance to raise; an exhausted list opens the null device,
    a real descriptor the walk owns and closes), and the liveness probe consumes the next entry of probes (an
    exception instance to raise, else the reading; an exhausted list reads False), each consumed probe recorded
    in used["probes"] (an exception by its type name): (patches for _walk_patched, used). A real pid reaches
    the real os.pidfd_open."""
    this, real_pidfd_open = sys.modules[__name__], os.pidfd_open
    used = dict(opens=0, probes=[])

    def opening(pid, *rest):
        if pid < FAKE_PID_BASE:
            return real_pidfd_open(pid, *rest)
        used["opens"] += 1
        step = opens[used["opens"] - 1] if used["opens"] <= len(opens) else None
        if isinstance(step, BaseException):
            raise step
        return os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)

    def probing(pidfd):
        step = probes[len(used["probes"])] if len(used["probes"]) < len(probes) else False
        used["probes"].append(type(step).__name__ if isinstance(step, BaseException) else step)
        if isinstance(step, BaseException):
            raise step
        return step
    return ((os, "pidfd_open", opening), (this, "_pidfd_dead", probing)), used


def _probe_table(own, fake):
    """The one-fake-pid scripted subtree of the probe legs: own's children name fake, whose task directory
    reads gone (exited after its recording, so fake stays in the walked set the exit test probes)."""
    return {1: {"/proc/{}/task".format(own): [str(own)],
                "/proc/{0}/task/{0}/children".format(own): str(fake).encode("ascii"),
                "/proc/{}/task".format(fake): FileNotFoundError(errno.ENOENT, "scripted as exited")}}


def _pidfd_open_refused():
    """A pidfd_open refused (EACCES) on a walked live pid in range is the walk's named problem, never a
    silently skipped exit test: a real forked child is discovered through a scripted children file and its
    pidfd_open alone is refused. The child is killed by its pid (still unreaped, so the pid is held) and
    reaped bounded (_reap_within, never a blocking waitpid); one still unreaped at the reap deadline is a
    named failure: (ok, detail)."""
    own, forked = os.getpid(), []
    real_pidfd_open = os.pidfd_open

    def refusing(pid, *rest):
        if forked and pid == forked[0]:
            raise PermissionError(errno.EACCES, "refused by the self-test")
        return real_pidfd_open(pid, *rest)
    forked.append(os.fork())
    if forked == [0]:
        try:
            time.sleep(HANG_SECONDS)
        finally:
            os._exit(0)
    table = {1: {"/proc/{}/task".format(own): [str(own)],
                 "/proc/{0}/task/{0}/children".format(own): str(forked[0]).encode("ascii")}}
    try:
        (found, problem), walks = _walk_patched(table, ((os, "pidfd_open", refusing),))
    finally:
        os.kill(forked[0], signal.SIGKILL)
        reaped = _reap_within(forked[0], WITNESS_WAIT)
    unreaped = ("child {} killed, still {}".format(forked[0], UNREAPED) if reaped is UNREAPED else None)
    mark = "pidfd_open of recorded pid {} failed".format(forked[0])
    ok = (found == set() and problem is not None and mark in problem
          and "refused by the self-test" in problem and walks == 1 and unreaped is None)
    return ok, dict(child=forked[0], found=found, problem=problem, walks=walks, unreaped=unreaped)


def _alive_probe_refuses():
    """The alive half of the exit test refuses an agreement on its own: a recorded pid skipped by the earlier
    walk's recording (gone at it: ProcessLookupError) is alive at the later walk's recording and reads exited
    at the acceptance, so only the later walk's recorded pidfd can observe the exit; the agreement at walk 2 is
    refused and the walk settles at walk 3, with the scripted probe sequence consumed exactly (an acceptance
    that probes only the earlier walk's pidfds settles at walk 2, one that probes only the later walk's leaves
    a probe unconsumed; either reads red): (ok, detail)."""
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    sequence = [False, True, False, False, False]
    patches, used = _scripted_probes([ProcessLookupError(errno.ESRCH, "gone at the recording")], list(sequence))
    result, walks = _walk_patched(_probe_table(own, fake), patches)
    ok = result == ({fake}, None) and walks == 3 and used["probes"] == sequence
    return ok, dict(result=result, walks=walks, used=used)


def _probe_failure_named(probes, walks, mark):
    """A liveness probe failing after probes - 1 clean readings is the walk's named problem at that call site
    (the recording, or the acceptance), observed at the expected walk count, never an escaping exception:
    (ok, detail)."""
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    error = OSError(errno.EIO, "io error of the self-test")
    patches, used = _scripted_probes([], [False] * (probes - 1) + [error])
    (found, problem), seen = _walk_patched(_probe_table(own, fake), patches)
    ok = (found == set() and problem is not None and mark in problem
          and "io error of the self-test" in problem and seen == walks)
    return ok, dict(found=found, problem=problem, walks=seen, used=used)


def _high_descriptor_probes(hold=False, bound=WITNESS_WAIT):
    """The liveness probe holds at descriptor 1024 and above, where select.select raises ValueError
    (FD_SETSIZE): a live process's pidfd duplicated to 1024 or higher reads not exited, and an exited child's
    reads exited: (ok, detail); (None, reason) where the probe was not exercised, each cause named: a
    duplicate at or past descriptor 1024 was refused under RLIMIT_NOFILE (the capability is decided by that
    allocation itself, never by a fixed threshold; the soft bound is first raised as far as the hard bound
    allows, to at most 1100), or the probe child (made to stop itself by hold, the regression on this bound)
    did not exit within bound seconds, in which case it is killed by its pid (this test started it) and the
    reason is dict(stalled, reaped, reason), reaped True only once its bounded reap confirmed it. Every wait
    here is bounded: the exit is observed through the child's pidfd against a monotonic deadline, never an
    unbounded waitid, and each reap is _reap_within, a WNOHANG poll against its own deadlines, never a
    blocking waitpid; a child still unreaped at the reap deadline, the stalled one included, is a named
    cleanup failure and the verdict is then False, never read as reaped or as not exercised (leg
    probe-stopped-unreaped). The restore of the soft bound sits in the outermost finally of the cleanup, so
    no earlier cleanup step (one loop over the closes, then the reap, each inside a finally the restore
    encloses) can skip it, whatever that step raises (an exception other than OSError included; a close that
    raises one ends the close loop, so the later descriptors are neither closed nor recorded, and the
    exception escapes with the reap and the restore still run); a failed raise of the bound is named and
    the verdict is False, the probe not run, after the restore; any cleanup failure, a failed restore
    included, is named in the returned detail and the verdict is then False, never a silent pass; and while
    an exception unwinds out of this function, every cleanup failure recorded, a failed restore included, is
    attached to it as a note, never lost behind it (leg probe-cleanup injects a close OSError, a close
    RuntimeError, a reap failure, a failed raise, a failed restore and a failed restore under a close
    RuntimeError, and holds the restore as the last limit call)."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = 1100 if hard == resource.RLIM_INFINITY else min(hard, 1100)
    lifted = soft < target
    child, opened, trouble = [0], [], []

    def probed():
        child[0] = os.fork()
        if child[0] == 0:
            try:
                if hold:
                    os.kill(os.getpid(), signal.SIGSTOP)
                    time.sleep(HANG_SECONDS)
            finally:
                os._exit(0)
        opened.append(os.pidfd_open(child[0]))
        waiter = select.poll()
        waiter.register(opened[0], select.POLLIN)
        deadline = time.monotonic() + bound
        while not waiter.poll(10):
            if time.monotonic() >= deadline:
                stalled, child[0] = child[0], 0
                os.kill(stalled, signal.SIGKILL)
                reaped = _reap_within(stalled, bound) is not UNREAPED
                if not reaped:
                    trouble.append("stalled probe child {} killed by its pid, still {}".format(stalled, UNREAPED))
                return None, dict(stalled=stalled, reaped=reaped, reason=(
                    "probe child {} did not exit within {} s (killed by its pid, {}): the high-descriptor "
                    "probe was not exercised".format(stalled, bound, "reaped" if reaped else UNREAPED)))
        opened.append(os.pidfd_open(os.getpid()))
        try:
            opened.append(fcntl.fcntl(opened[0], fcntl.F_DUPFD_CLOEXEC, 1024))
            opened.append(fcntl.fcntl(opened[1], fcntl.F_DUPFD_CLOEXEC, 1024))
        except OSError as exc:
            held = resource.getrlimit(resource.RLIMIT_NOFILE)
            return None, ("RLIMIT_NOFILE {} (hard {}) refused a duplicate at or past descriptor 1024 ({}): "
                          "the high-descriptor probe was not exercised".format(held[0], held[1], exc))
        high = opened[2:]
        try:
            readings = [_pidfd_dead(high[0]), _pidfd_dead(high[1])]
        except (OSError, ValueError) as exc:
            return False, "the probe at descriptors {} raised {!r}".format(high, exc)
        return readings == [True, False] and min(high) >= 1024, dict(high=high, readings=readings)
    verdict = detail = None
    try:
        try:
            if lifted:
                try:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                except (OSError, ValueError) as exc:
                    trouble.append("RLIMIT_NOFILE raise to {} failed ({})".format((target, hard), exc))
            if not trouble:
                verdict, detail = probed()
        finally:
            try:
                try:
                    for descriptor in opened:
                        try:
                            os.close(descriptor)
                        except OSError as exc:
                            trouble.append("close of descriptor {} failed ({})".format(descriptor, exc))
                finally:
                    if child[0]:
                        try:
                            if _reap_within(child[0], bound) is UNREAPED:
                                trouble.append("probe child {} killed, still {}".format(child[0], UNREAPED))
                        except OSError as exc:
                            trouble.append("reap of probe child {} failed ({})".format(child[0], exc))
            finally:
                if lifted:
                    try:
                        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
                    except (OSError, ValueError) as exc:
                        trouble.append("RLIMIT_NOFILE restore to {} failed ({})".format((soft, hard), exc))
    except BaseException as exc:
        if trouble:
            exc.add_note("cleanup of the high-descriptor probe: " + "; ".join(trouble))
        raise
    if trouble:
        return False, dict(verdict=verdict, detail=detail, cleanup="; ".join(trouble))
    return verdict, detail


def _liveness_broken_pipe_writers():
    """The probe-broken walk reaches _pipe_writers as ([], [], problem) naming the liveness probe, never one
    candidate per pid: (ok, detail)."""
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    patches, _used = _scripted_probes([], [OSError(errno.EIO, "io error of the self-test")])
    listing, opened, _state = _walk_stub(_probe_table(own, fake), own)
    with contextlib.ExitStack() as stack:
        stack.enter_context(_patched(os, "listdir", listing))
        stack.enter_context(_patched(builtins, "open", opened))
        for owner, name, replacement in patches:
            stack.enter_context(_patched(owner, name, replacement))
        writers = _pipe_writers(0, 0)
    ok = (writers[0] == [] and writers[1] == [] and writers[2] is not None
          and "liveness probe of recorded pid" in writers[2] and "io error of the self-test" in writers[2])
    return ok, writers


def _probe_broken_witnessed():
    """The liveness-probe failure driven end to end through a real witnessed cleanup (_scan_broken_witnessed):
    exactly one ("/proc scan", reason) entry naming the probe, no candidate derived, nothing signalled."""
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    patches, _used = _scripted_probes([], [OSError(errno.EIO, "io error of the self-test")])
    return _scan_broken_witnessed(_probe_table(own, fake), patches,
                                  ("liveness probe of recorded pid", "io error of the self-test"))


def _token_boundaries():
    """Each clause of _pid_token at its boundaries, str and bytes, with a value just inside, exactly at and
    just outside each end (the low end is owned by the leading-zero clause, which refuses b"0" itself, so no
    dead zero-or-below comparison remains in _pid_token). The width clause is held by entries only it can
    refuse: digit strings past the integer-conversion limit (sys.get_int_max_str_digits), bytes and str,
    which int would refuse with ValueError, so with the width clause removed those entries read as a raise,
    never as None (in an interpreter that has disabled the conversion limit, a per-interpreter setting, where
    int would convert any width, the default limit is set around the judgements and the disabled state
    restored after them, so those entries still read as a raise there and never as the numeric bound's None,
    with the restore held by _conversion_limit_restored); the ten- and
    eleven-digit entries hold the width end itself, though past it the numeric bound refuses them too. A
    raise inside _pid_token is recorded as the mismatch, never an escape: (ok, mismatches)."""
    saved = sys.get_int_max_str_digits()
    limit = saved or sys.int_info.default_max_str_digits
    over = max(limit, len(str(PID_BOUND))) + 1

    def judged(token):
        try:
            return _pid_token(token)
        except Exception as exc:
            return "raised ({!r})".format(exc)
    table = (
        (b"1", 1), ("1", 1), (b"42", 42),
        (str(PID_BOUND), PID_BOUND), (str(PID_BOUND).encode("ascii"), PID_BOUND),
        (b"2147483646", PID_BOUND - 1), (b"2147483648", None), ("2147483648", None),
        (b"9" * 10, None), (b"9" * 11, None), ("9" * 11, None),
        (b"9" * over, None), ("9" * over, None),
        (b"0", None), ("0", None), (b"007", None), ("007", None), (b"02147483647", None),
        (b"", None), ("", None), (b"-1", None), (b"+1", None), ("1a", None), (b"1 2", None),
        ("\u0662\u0663", None), (" 1", None))
    try:
        if not saved:
            sys.set_int_max_str_digits(limit)
        mismatched = [(_token_preview(token), judged(token), want) for token, want in table
                      if judged(token) != want]
    finally:
        if not saved:
            sys.set_int_max_str_digits(saved)
    return not mismatched, mismatched


# The integer-conversion settings a caller of _token_boundaries starts from in the restore legs: disabled (0,
# the only state it changes), the smallest limit the interpreter accepts, the default limit, and one above it.
CONVERSION_SETTINGS = (0, sys.int_info.str_digits_check_threshold, sys.int_info.default_max_str_digits,
                       2 * sys.int_info.default_max_str_digits)


def _conversion_limit_restored():
    """_token_boundaries leaves the caller's integer-conversion setting as it found it, the setting read back
    independently (sys.get_int_max_str_digits) after each call rather than trusted from the fixture's verdict:
    from each of CONVERSION_SETTINGS (the disabled setting, the only state _token_boundaries changes, and three
    limits it must never write: the smallest, the default and one above it), each on the return path and with an
    injected judgement escape (a BaseException the judgement recorder does not catch, so it unwinds through the
    restore and must still be re-raised, never swallowed). A call that leaves the setting moved, on either path
    from any starting setting, is a failure, so a removed or ineffective restore, or a write of any fixed limit,
    reads red here while the token verdicts alone stay green. This function's own final restore of its caller's
    setting is held by _conversion_limit_observed: (ok, detail)."""
    this, entry = sys.modules[__name__], sys.get_int_max_str_digits()

    class Escape(BaseException):
        pass

    def escaping(token):
        raise Escape("judgement escape of the self-test")
    outcomes = {}
    try:
        for caller in CONVERSION_SETTINGS:
            sys.set_int_max_str_digits(caller)
            held, mismatched = _token_boundaries()
            outcomes["returned-from-{}".format(caller)] = (
                held is True and not mismatched and sys.get_int_max_str_digits() == caller,
                dict(after=sys.get_int_max_str_digits(), mismatched=mismatched))
            sys.set_int_max_str_digits(caller)
            escaped = None
            try:
                with _patched(this, "_pid_token", escaping):
                    _token_boundaries()
            except Escape as exc:
                escaped = str(exc)
            outcomes["escaped-from-{}".format(caller)] = (
                escaped == "judgement escape of the self-test" and sys.get_int_max_str_digits() == caller,
                dict(after=sys.get_int_max_str_digits(), escaped=escaped))
    finally:
        sys.set_int_max_str_digits(entry)
    return _combined(ok for ok, _seen in outcomes.values()), outcomes


def _conversion_limit_observed():
    """_conversion_limit_restored, run from each of CONVERSION_SETTINGS, holds and leaves that setting as it found
    it, read back after it returns, so its own final restore is held as well as the restores it reads inside
    _token_boundaries (with that restore removed, the last setting it exercised is left behind). The whole
    observation runs in a disposable child (_in_child, bounded by CHILD_TIMEOUT) that sets the setting freely and
    then exits, so this process's own setting is never written and no restore here is left unobserved; it is
    read before and after the child and must be unchanged. A child that does not report its readings (hung,
    crashed or unreaped) is a failure, never a pass: (ok, detail)."""
    before = sys.get_int_max_str_digits()

    def observed():
        seen = []
        for entry in CONVERSION_SETTINGS:
            sys.set_int_max_str_digits(entry)
            held, outcomes = _conversion_limit_restored()
            seen.append([entry, held is True, sys.get_int_max_str_digits(),
                         sorted(name for name, (ok, _seen) in outcomes.items() if ok is not True)])
        return seen
    done, seen = _in_child(observed, CHILD_TIMEOUT)
    want = [[entry, True, entry, []] for entry in CONVERSION_SETTINGS]
    after = sys.get_int_max_str_digits()
    return done is True and seen == want and after == before, dict(seen=seen, before=before, after=after)


def _probe_event_masks():
    """_pidfd_dead decides by the event mask at each boundary: a live pidfd (no event) reads alive; an exited
    child pidfd (POLLIN) and the read end of a pipe whose write end closed (POLLHUP) each read exited; the
    write end of a pipe whose read end closed (POLLERR) and a closed descriptor (POLLNVAL) each raise OSError
    naming the mask, never an exit; every wait here is bounded (_reap_within, a WNOHANG poll against its
    deadlines), and a probe child still unreaped at the reap deadline is a named failure (unreaped names the
    child's pid and the reason; None once it was reaped), never read as reaped: (ok, readings)."""
    readings, opened = {}, []
    child = os.fork()
    if child == 0:
        os._exit(0)
    try:
        opened.append(os.pidfd_open(child))
        waiter = select.poll()
        waiter.register(opened[0], select.POLLIN)
        waiter.poll(int(WITNESS_WAIT * 1000))
        readings["exited"] = _pidfd_dead(opened[0])
        opened.append(os.pidfd_open(os.getpid()))
        readings["alive"] = _pidfd_dead(opened[1])
        hup_read, hup_write = os.pipe()
        opened.append(hup_read)
        os.close(hup_write)
        readings["hup"] = _pidfd_dead(hup_read)
        err_read, err_write = os.pipe()
        opened.append(err_write)
        os.close(err_read)
        try:
            readings["err"] = _pidfd_dead(err_write)
        except OSError as exc:
            readings["err"] = "raised ({})".format(exc)
        closed = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        os.close(closed)
        try:
            readings["nval"] = _pidfd_dead(closed)
        except OSError as exc:
            readings["nval"] = "raised ({})".format(exc)
    finally:
        for descriptor in opened:
            os.close(descriptor)
        reaped = _reap_within(child, WITNESS_WAIT)
    unreaped = ("probe child {} killed, still {}".format(child, UNREAPED) if reaped is UNREAPED else None)
    ok = (readings.get("exited") is True and readings.get("alive") is False and readings.get("hup") is True
          and isinstance(readings.get("err"), str) and "an error, not an exit" in readings["err"]
          and isinstance(readings.get("nval"), str) and "an error, not an exit" in readings["nval"]
          and unreaped is None)
    return ok, dict(readings, child=child, unreaped=unreaped)


def _erroring_opens():
    """An os.pidfd_open stand-in whose fake-pid descriptor polls as an error: the write end of a pipe whose
    read end is already closed (POLLERR); a real pid reaches the real call. The descriptor is a real open
    one, so the single close site of _descendants closes it like any pidfd."""
    real_pidfd_open = os.pidfd_open

    def opening(pid, *rest):
        if pid < FAKE_PID_BASE:
            return real_pidfd_open(pid, *rest)
        read_end, write_end = os.pipe()
        os.close(read_end)
        return write_end
    return (os, "pidfd_open", opening)


def _probe_error_recorded():
    """The poll-error event driven end to end through the real probe at the recording: the scripted pidfd of
    the fake pid polls POLLERR, the real _pidfd_dead raises, and the walk names the probe failure, never
    reading the error as an exit: (ok, detail)."""
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    (found, problem), walks = _walk_patched(_probe_table(own, fake), (_erroring_opens(),))
    ok = (found == set() and problem is not None and "liveness probe of recorded pid" in problem
          and "an error, not an exit" in problem and walks == 1)
    return ok, dict(found=found, problem=problem, walks=walks)


def _probe_error_accepted():
    """The poll-error event driven end to end through the real probe at the acceptance: the scripted pidfd of
    the fake pid is the write end of a pipe whose read end this leg still holds, so each recording reads it
    alive (no event); once both walks have recorded, the read ends are closed and the first acceptance probe
    polls POLLERR, so the walk must name the acceptance probe failure (reading the error as an exit instead
    would refuse this agreement and accept a later one): (ok, detail)."""
    this, real_pidfd_open, real_dead = sys.modules[__name__], os.pidfd_open, _pidfd_dead
    own, fake = os.getpid(), FAKE_PID_BASE + 1
    readers, calls = [], [0]

    def opening(pid, *rest):
        if pid < FAKE_PID_BASE:
            return real_pidfd_open(pid, *rest)
        read_end, write_end = os.pipe()
        readers.append(read_end)
        return write_end

    def probing(pidfd):
        calls[0] += 1
        if calls[0] == 3:
            while readers:
                os.close(readers.pop())
        return real_dead(pidfd)
    try:
        (found, problem), walks = _walk_patched(
            _probe_table(own, fake), ((os, "pidfd_open", opening), (this, "_pidfd_dead", probing)))
    finally:
        while readers:
            os.close(readers.pop())
    ok = (found == set() and problem is not None
          and "liveness probe at the walk acceptance failed" in problem
          and "an error, not an exit" in problem and walks == 2 and calls[0] == 3)
    return ok, dict(found=found, problem=problem, walks=walks, calls=calls[0])


def _limited_descriptor_probes():
    """_high_descriptor_probes with RLIMIT_NOFILE lowered to each boundary, one child per bound (a lowered
    hard bound cannot be raised back): hard bounds 1024 (the duplicate request is at the bound) and 1025
    (room for one duplicate alone) each name the refused allocation, decided by the allocation itself, never
    by a fixed threshold, and are never a pass; hard bounds 1026 (the least that holds descriptors 1024 and
    1025) and 1050 are exercised and hold; and a soft bound of 1024 under a hard bound of 1050 is raised for
    the probe and read back restored after it: (ok, detail). A case whose bounds this host refuses to set
    (its own hard RLIMIT_NOFILE sits below the case's hard bound, and raising a hard bound needs privilege)
    is named cannot-evaluate, and the leg is then None, never False for that cause (leg probe-capped drives
    hard bounds 1040 and 1024); a genuine failure of another case still reads False."""
    outcomes = {}
    for soft, hard in ((1024, 1024), (1025, 1025), (1026, 1026), (1050, 1050), (1024, 1050)):
        def lowered(bounds=(soft, hard)):
            held = resource.getrlimit(resource.RLIMIT_NOFILE)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, bounds)
            except (OSError, ValueError) as exc:
                return ["capped", "RLIMIT_NOFILE {} could not be set under this host's hard bound {} ({}): "
                        "raising a hard bound needs privilege".format(bounds, held[1], exc)], None
            result = _high_descriptor_probes()
            return result, resource.getrlimit(resource.RLIMIT_NOFILE)
        outcomes[(soft, hard)] = _in_child(lowered, CHILD_TIMEOUT)

    def judged(outcome, bounds, exercised):
        done, result = outcome
        if not done or not isinstance(result, list) or len(result) != 2:
            return False
        (verdict, detail), after = result
        if verdict == "capped":
            return None
        if after != list(bounds):
            return False
        if not exercised:
            return (verdict is None and "refused a duplicate at or past descriptor 1024" in detail
                    and "not exercised" in detail)
        return verdict is True and detail["readings"] == [True, False] and min(detail["high"]) >= 1024
    judgements = dict((bounds, judged(outcomes[bounds], bounds, bounds[1] >= 1026)) for bounds in outcomes)
    verdict = _combined(judgements.values())
    detail = dict(outcomes=outcomes, judgements=judgements)
    if verdict is None:
        capped = sorted(bounds for bounds, judgement in judgements.items() if judgement is None)
        detail["note"] = ("this host's hard RLIMIT_NOFILE refused the bounds {}: those cases cannot be "
                          "evaluated (raising a hard bound needs privilege), never a failure".format(capped))
    return verdict, detail


def _stopped_probe_named():
    """A probe child that stops itself never exits: the bounded wait must end at its deadline, kill the child
    by its pid, reap it, and name the probe unexercised, never hang or read a pass; run in a child so a wait
    that lost its bound is cut at CHILD_TIMEOUT and read as red, never as a hang of this run. The confirmed
    reap is required on its own (reaped True), apart from the expected timeout text, so a stalled child
    still unreaped at the reap deadline is red here, never a pass: (ok, detail)."""
    def held():
        began = time.monotonic()
        verdict, reason = _high_descriptor_probes(hold=True, bound=WITNESS_TIMEOUT)
        return verdict, reason, time.monotonic() - began
    done, result = _in_child(held, CHILD_TIMEOUT)
    ok = (done is True and isinstance(result, list) and result[0] is None and isinstance(result[1], dict)
          and result[1].get("reaped") is True and "did not exit within" in result[1].get("reason", "")
          and "not exercised" in result[1].get("reason", "") and result[2] < WITNESS_WAIT)
    return ok, (done, result)


def _stopped_unreaped_named():
    """Leg probe-stopped against a stalled child still unreaped at the reap deadline: _reap_within, in the
    probe's own child only (this process's reap of that child stays real), reaps for real and then returns
    UNREAPED, so the stalled probe child reads unreaped; _high_descriptor_probes is then False with the
    child named still unreaped, and _stopped_probe_named is red, never a pass (reading the unreaped stall as
    an unexercised probe is the regression): (ok, detail)."""
    this, owner, real = sys.modules[__name__], os.getpid(), _reap_within

    def reaping(pid, wait):
        status = real(pid, wait)
        return status if os.getpid() == owner else UNREAPED
    with _patched(this, "_reap_within", reaping):
        verdict, (done, result) = _stopped_probe_named()
    ok = (verdict is False and done is True and isinstance(result, list) and result[0] is False
          and "still {}".format(UNREAPED) in str(result[1]))
    return ok, dict(verdict=verdict, done=done, result=result)


def _unreaped_named():
    """_reap_within past both deadlines over a scripted still-live child: every wait is a WNOHANG poll (a
    blocking waitpid, options without WNOHANG, recorded here is the regression), exactly one SIGKILL is sent
    at the first deadline, and the result is UNREAPED, an explicit named failure, never a wait status and
    never the reaped None: (ok, detail)."""
    fake = FAKE_PID_BASE + 1
    calls, kills = [], []

    def polling(pid, options):
        calls.append((pid, options))
        return (0, 0)

    def killing(pid, signum):
        kills.append((pid, signum))
    with _patched(os, "waitpid", polling), _patched(os, "kill", killing):
        result = _reap_within(fake, 0)
    blocking = [call for call in calls if not call[1] & os.WNOHANG]
    ok = (result is UNREAPED and kills == [(fake, signal.SIGKILL)] and blocking == []
          and len(calls) >= 2 and all(call[0] == fake for call in calls))
    return ok, dict(result=result, kills=kills, calls=calls, blocking=blocking)


def _cleanup_restores_limit():
    """The RLIMIT_NOFILE restore of _high_descriptor_probes under an injected failure, each case in its own
    child over scripted limit calls (getrlimit reads (1024, 1100) so the lift is taken; setrlimit records
    and changes nothing unless the case makes it raise, so the restore is observable), every reap recorded:
    with every close raising OSError, and with the reap raising, the failed step is named in the returned
    cleanup detail and the verdict is False; with every close raising RuntimeError (no OSError), that
    exception escapes, yet the probe child is still reaped and the restore still runs (a cleanup whose
    restore an earlier step's exception skips is the regression); with the raise of the bound failing, the
    probe is not run and the failed raise is named, the verdict False after the restore (an escaping raise
    failure is the regression); with the restore failing, it is named and the verdict is False; and with a
    close raising RuntimeError and the restore failing, the RuntimeError escapes carrying the failed restore
    as a note, never lost behind it. In every case the restore (1024, 1100) is the last recorded limit call
    and the raise (1100, 1100) was attempted: (ok, detail)."""
    def driven(failing):
        def run():
            this = sys.modules[__name__]
            calls, reaps = [], []
            real_close, real_reap = os.close, _reap_within

            def reading(which):
                return (1024, 1100)

            def setting(which, bounds):
                calls.append(list(bounds))
                if (failing == "raise" and list(bounds) == [1100, 1100]
                        or failing in ("restore", "unwinding") and list(bounds) == [1024, 1100]):
                    raise ValueError("limit failure of the self-test")

            def closing(fd):
                real_close(fd)
                if failing in ("runtime", "unwinding"):
                    raise RuntimeError("close runtime failure of the self-test")
                raise OSError(errno.EIO, "close failure of the self-test")

            def reaping(pid, wait):
                status = real_reap(pid, wait)
                reaps.append(pid)
                if failing == "reap":
                    raise OSError(errno.ECHILD, "reap failure of the self-test")
                return status
            patches = [(resource, "getrlimit", reading), (resource, "setrlimit", setting),
                       (this, "_reap_within", reaping)]
            if failing in ("close", "runtime", "unwinding"):
                patches.append((os, "close", closing))
            try:
                with contextlib.ExitStack() as stack:
                    for owner, name, replacement in patches:
                        stack.enter_context(_patched(owner, name, replacement))
                    verdict, detail = _high_descriptor_probes()
            except Exception as exc:
                return ["raised", repr(exc), list(getattr(exc, "__notes__", ())), calls, reaps]
            return [verdict, repr(detail), [], calls, reaps]
        return run
    marks = dict(close=(False, "close failure of the self-test"), reap=(False, "reap failure of the self-test"),
                 runtime=("raised", "close runtime failure of the self-test"),
                 raise_=(False, "RLIMIT_NOFILE raise to (1100, 1100) failed"),
                 restore=(False, "RLIMIT_NOFILE restore to (1024, 1100) failed"),
                 unwinding=("raised", "close runtime failure of the self-test"))
    outcomes = dict((name, _in_child(driven(name.rstrip("_")), CHILD_TIMEOUT)) for name in marks)

    def judged(name):
        done, result = outcomes[name]
        verdict, mark = marks[name]
        if not (done is True and isinstance(result, list) and len(result) == 5):
            return False
        held, text, notes, calls, reaps = result
        return (held == verdict and mark in text and calls != [] and calls[-1] == [1024, 1100]
                and [1100, 1100] in calls and (reaps != []) is (name != "raise_")
                and (name != "unwinding" or "RLIMIT_NOFILE restore to (1024, 1100) failed" in str(notes)))
    judgements = dict((name, judged(name)) for name in marks)
    return all(judgements.values()), dict(outcomes=outcomes, judgements=judgements)


def _capped_limits_named():
    """_limited_descriptor_probes where this host's hard RLIMIT_NOFILE sits below some case bounds, driven
    for real in a child whose own hard bound is first lowered (a lowered hard bound cannot be raised back, so
    the refusal is the kernel's own, never a stub): at hard 1040 the 1050-bound cases, and at hard 1024 every
    case over 1024, are each cannot-evaluate, and the leg verdict is None with the refused bounds named,
    never False. This leg's own lowered bound is subject to the same host: each child first reads its
    inherited hard RLIMIT_NOFILE, and a case whose bound it cannot set (the setrlimit is refused: raising a
    hard bound needs privilege) is itself cannot-evaluate, named with the host's hard bound, never a crash
    read as False, so a host whose hard bound sits below 1040 makes this leg None and the suite exit 2,
    never 1 (leg capped-host): (ok, detail)."""
    def capped(hard):
        def run():
            held = resource.getrlimit(resource.RLIMIT_NOFILE)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
            except (OSError, ValueError) as exc:
                return ["capped", "hard bound {} could not be set under this host's hard bound {} ({})".format(
                    hard, held[1], exc), None]
            verdict, detail = _limited_descriptor_probes()
            refused = sorted(bounds for bounds, judgement in detail["judgements"].items()
                             if judgement is None)
            return [verdict, detail.get("note"), [list(bounds) for bounds in refused]]
        return run
    outcomes = dict((hard, _in_child(capped(hard), CHILD_TIMEOUT)) for hard in (1040, 1024))

    def judged(outcome, refused):
        done, result = outcome
        if done is True and isinstance(result, list) and result[:1] == ["capped"]:
            return None
        return (done is True and isinstance(result, list) and result[0] is None and result[1] is not None
                and "cannot be evaluated" in result[1] and result[2] == refused)
    verdict = _combined([judged(outcomes[1040], [[1024, 1050], [1050, 1050]]),
                         judged(outcomes[1024], [[1024, 1050], [1025, 1025], [1026, 1026], [1050, 1050]])])
    return verdict, outcomes


def _capped_host_named():
    """Leg probe-capped on a capped host, driven for real: in a child whose inherited hard RLIMIT_NOFILE is
    first lowered to 1024, and in one lowered to 1030 (each below probe-capped's own 1040 case), the leg
    _capped_limits_named reads None (cannot-evaluate), never False (its 1040 setup crashing into a False is
    the regression). A lowered bound this host itself refuses (its own hard bound below it) leaves that case
    cannot-evaluate, and the leg None: (ok, detail)."""
    def capped(host):
        def run():
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (host, host))
            except (OSError, ValueError) as exc:
                return ["capped", "hard bound {} could not be set ({})".format(host, exc)]
            verdict, detail = _capped_limits_named()
            return [verdict, repr(detail)]
        return run
    outcomes = dict((host, _in_child(capped(host), CHILD_TIMEOUT)) for host in (1024, 1030))

    def judged(host):
        done, result = outcomes[host]
        if done is True and isinstance(result, list) and result[:1] == ["capped"]:
            return None
        return (done is True and isinstance(result, list) and result[0] is None
                and "could not be set under this host's hard bound {}".format(host) in result[1])
    return _combined([judged(1024), judged(1030)]), outcomes


def _exited_child_unsignalled():
    """The cleanup ownership rule (_ended_by_pid) at both of its ends: over a child already exited (the exit
    first made observable through its pidfd), the exit is observed without reaping, no kill at all is sent
    (the recorded os.kill list stays empty: a reap-then-kill ordering would free the pid and then signal it,
    the regression), and the child is reaped exactly once; over a live child the kill is sent and the child
    is reaped: (ok, detail)."""
    kills = []
    ended = os.fork()
    if ended == 0:
        os._exit(0)
    pidfd = os.pidfd_open(ended)
    try:
        waiter = select.poll()
        waiter.register(pidfd, select.POLLIN)
        waiter.poll(int(WITNESS_WAIT * 1000))
    finally:
        os.close(pidfd)
    with _patched(os, "kill", lambda pid, signum: kills.append((pid, signum))):
        dead = _ended_by_pid(ended, WITNESS_WAIT)
    living = os.fork()
    if living == 0:
        try:
            time.sleep(HANG_SECONDS)
        finally:
            os._exit(0)
    live = _ended_by_pid(living, WITNESS_WAIT)
    ok = (dead == (False, None) and kills == [] and live == (True, None))
    return ok, dict(kills=kills, dead=dead, live=live, ended=ended, living=living)


def _holder_probe_failure_named():
    """A liveness probe failing inside _kill_holder is NOT_EVALUATED with the probe named and nothing
    signalled, never an escaping exception or a kill: the probe raises over a real forked child that holds
    the write end of the pipe, and the child must still be alive after the call, observed without reaping
    (os.waitid with WNOWAIT, so the pid stays held and no later kill here can reach a reused pid). The
    cleanup ends the child through _ended_by_pid (this leg started it): the kill is sent only while the child
    is unreaped and observed not exited, then one bounded reap; a child still unreaped at the reap deadline
    is this leg's own named failure: (ok, detail)."""
    this, sent = sys.modules[__name__], []

    def send(pidfd, signum):
        sent.append((_pidfd_pid(pidfd), signum))

    def raising(pidfd):
        raise OSError(errno.EIO, "io error of the self-test")
    read_end, write_end = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            time.sleep(HANG_SECONDS)
        finally:
            os._exit(0)
    os.close(read_end)
    state = reason = exited = killed = unreaped = None
    try:
        with _patched(this, "_pidfd_dead", raising), _patched(signal, "pidfd_send_signal", send):
            try:
                state, reason = _kill_holder(child, write_end)
            except OSError as exc:
                state, reason = "escaped", repr(exc)
        exited = os.waitid(os.P_PID, child, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    finally:
        try:
            os.close(write_end)
        finally:
            killed, unreaped = _ended_by_pid(child, WITNESS_WAIT)
    ok = (state == NOT_EVALUATED and "liveness probe failed" in str(reason)
          and "io error of the self-test" in str(reason) and sent == [] and exited is None
          and killed is True and unreaped is None)
    return ok, dict(state=state, reason=reason, sent=sent,
                    exited=None if exited is None else repr(exited),
                    killed=killed, unreaped=unreaped, child=child)


def _holder_exited_unsignalled():
    """The real holder fixture (_holder_probe_failure_named) with its child already exited before the
    fixture's liveness observation: the child's HANG_SECONDS sleep returns at once (time.sleep is wrapped for
    that duration only, inherited through the fork), and _kill_holder is wrapped to wait, bounded, through
    the child's pidfd for that exit before the real call runs. The fixture must then send no kill at all
    (every os.kill recorded and none sent: a reap-then-kill ordering frees the pid and then signals it, the
    regression), make no blocking waitpid (every os.waitpid recorded; one without WNOHANG is the
    regression), report no unreaped child, and leave the child reaped. The fixture's own verdict is not
    asserted (its child did not stay alive): (ok, detail)."""
    this, real_sleep, real_kill_holder, real_waitpid = sys.modules[__name__], time.sleep, _kill_holder, os.waitpid
    kills, waits, raised = [], [], []

    def sleeping(seconds):
        if seconds != HANG_SECONDS:
            real_sleep(seconds)

    def holding(pid, write_end):
        pidfd = os.pidfd_open(pid)
        try:
            waiter = select.poll()
            waiter.register(pidfd, select.POLLIN)
            waiter.poll(int(WITNESS_WAIT * 1000))
        finally:
            os.close(pidfd)
        return real_kill_holder(pid, write_end)

    def waiting(pid, options):
        waits.append((pid, options))
        return real_waitpid(pid, options)
    detail = {}
    with (_patched(time, "sleep", sleeping), _patched(this, "_kill_holder", holding),
          _patched(os, "kill", lambda pid, signum: kills.append((pid, signum))), _patched(os, "waitpid", waiting)):
        try:
            _held, detail = _holder_probe_failure_named()
        except Exception as exc:
            raised.append(repr(exc))
    child = detail.get("child")
    blocking = [call for call in waits if not call[1] & os.WNOHANG]
    ok = (raised == [] and child is not None and detail.get("exited") is not None and kills == []
          and detail.get("killed") is False and detail.get("unreaped") is None and blocking == []
          and _reaped(child))
    return ok, dict(detail, kills=kills, waits=waits, blocking=blocking, raised=raised)


def _double_fork_verdicts():
    """The deterministic verdict table of _double_fork_verdict, one synthetic attempt state per branch: the
    exercised race whose walk found D and A over at least four walks is True; B already exited when the walk
    2 hook fired (b_alive False) and D already visible to walk 2 (early True) are each None with the missed
    precondition named, never a pass, whatever the walk found; an attempt with no D, or whose reparenting
    wait failed, is None as not exercised; and a named walk problem, a missing D with A still
    found (case missing), a missing A with D still found (case missing_a), or fewer than four walks is False,
    with nothing signalled anywhere (a pure function): (ok, detail)."""
    raced = dict(b=11, d=12, walk=4, exited=True, early=False, b_alive=True)
    found = {10, 12}
    cases = dict(
        good=_double_fork_verdict(dict(raced), 10, found, None),
        dead_b=_double_fork_verdict(dict(raced, b_alive=False), 10, found, None),
        early_d=_double_fork_verdict(dict(raced, early=True), 10, found, None),
        unforked=_double_fork_verdict(dict(raced, d=0), 10, set(), None),
        unparented=_double_fork_verdict(dict(raced, exited=False), 10, found, None),
        named=_double_fork_verdict(dict(raced), 10, found, "scripted problem"),
        missing=_double_fork_verdict(dict(raced), 10, {10}, None),
        missing_a=_double_fork_verdict(dict(raced), 10, {12}, None),
        short=_double_fork_verdict(dict(raced, walk=3), 10, found, None))
    ok = (cases["good"][0] is True
          and cases["dead_b"][0] is None and "precondition" in cases["dead_b"][1]["note"]
          and cases["early_d"][0] is None and "precondition" in cases["early_d"][1]["note"]
          and cases["unforked"][0] is None and "not exercised" in cases["unforked"][1]["note"]
          and cases["unparented"][0] is None and "not exercised" in cases["unparented"][1]["note"]
          and cases["named"][0] is False and cases["missing"][0] is False
          and cases["missing_a"][0] is False and cases["short"][0] is False)
    return ok, cases


def _recording_sweep():
    """Run the scripted walk over a real forked child once per opcode of _recorded_alive, raising _Injected at
    that opcode (as a caller's signal handler that raises would), from the first opcode until a run sees no
    injection: dict(points, injected, complete, count, findings, child, clean, unreaped): points counts every
    run attempted (the completing, uninjected run included), injected only the runs whose injection fired
    (points - 1 on completion: the completing run is an attempt, never an injection, and the caller's floor
    compares injected), clean the completing run's own walk result (found, problem, walks seen), so the
    caller can refuse a sweep that never exercised the real recording, and unreaped the named reap failure of
    the probe child, None once it was reaped (the cleanup kill precedes any reap, so it never aims at a
    reused pid, and the reap is bounded: _reap_within). No run may leave a descriptor behind: each descriptor
    pidfd_open returns lands in the caller-owned list in the same C-level step (_recorded_alive), so an
    exception at any opcode either precedes the open or finds the descriptor owned, and _descendants closes
    what the list owns. A NOT_TAKEN instruction is not a point (_injector states why). Run only in a child
    (_in_child): it installs a trace function."""
    child = os.fork()
    if child == 0:
        try:
            time.sleep(HANG_SECONDS)
        finally:
            os._exit(0)
    own, code, findings = os.getpid(), _recorded_alive.__code__, []
    inert = set(instruction.offset for instruction in dis.get_instructions(code)
                if instruction.opname == "NOT_TAKEN")
    table = {1: {"/proc/{}/task".format(own): [str(own)],
                 "/proc/{0}/task/{0}/children".format(own): str(child).encode("ascii")}}
    try:
        for ordinal in range(1, SWEEP_LIMIT + 1):
            seen = [0]

            def trace(frame, event, arg):
                if frame.f_code is not code:
                    return None
                frame.f_trace_opcodes = True
                if event == "opcode" and seen[0] >= 0 and frame.f_lasti not in inert:
                    seen[0] += 1
                    if seen[0] == ordinal:
                        seen[0] = -1
                        raise _Injected()
                return trace
            descriptors = set(os.listdir(DESCRIPTOR_DIR))
            raised = result = None
            try:
                try:
                    sys.settrace(trace)
                    result = _walk_scripted(table)
                finally:
                    sys.settrace(None)
            except BaseException as exc:
                raised = type(exc).__name__
            leaked = sorted(set(os.listdir(DESCRIPTOR_DIR)) - descriptors)
            if leaked or raised not in (None, "_Injected"):
                findings.append(dict(point=ordinal, raised=raised, result=repr(result), leaked=leaked))
            if raised is None:
                (cleared, walk_problem), walks = result
                swept = dict(points=ordinal, injected=ordinal - 1, complete=True, count=len(findings),
                             findings=findings[:10], child=child,
                             clean=[sorted(cleared), walk_problem, walks])
                break
        else:
            swept = dict(points=SWEEP_LIMIT, injected=SWEEP_LIMIT, complete=False, count=len(findings),
                         findings=findings[:10], child=child, clean=None)
    finally:
        os.kill(child, signal.SIGKILL)
        reaped = _reap_within(child, WITNESS_WAIT)
    swept["unreaped"] = ("probe child {} killed, still unreaped at the reap deadline".format(child)
                         if reaped is UNREAPED else None)
    return swept


def _sweep_accepted(done, sweep):
    """The acceptance of _recording_swept as one predicate over the sweep's record, so its floor boundary is
    held by its own vector (leg sweep-floor): ok only when the sweep ran (done), completed (a run no
    injection reached), made at least SWEEP_FLOOR completed injections (the completing run is an attempt,
    never an injection, so a floor over points alone would overcount by one), found nothing at any point,
    reaped its probe child, and proved what was swept: the completing run found exactly the forked child,
    nothing named, settled at walk 2."""
    return bool(done and sweep["complete"] and sweep["injected"] >= SWEEP_FLOOR and not sweep["count"]
                and sweep["unreaped"] is None and sweep["clean"] == [[sweep["child"]], None, 2])


def _recording_swept():
    """The recording sweep, run in a child: (ok, detail), ok the acceptance _sweep_accepted holds, so a sweep
    whose injections never reached the real recording cannot pass vacuously."""
    done, sweep = _in_child(_recording_sweep, CHILD_TIMEOUT)
    return _sweep_accepted(done, sweep), (done, sweep)


def _sweep_floor_boundary():
    """The injection floor of the sweep acceptance at its boundary, over synthetic sweep records (the real
    sweep is leg probe-interrupt): a completed sweep of SWEEP_FLOOR - 1 injections (its points read
    SWEEP_FLOOR: the overcount a floor over attempts would accept) is refused, one of exactly SWEEP_FLOOR
    and one just past it are accepted, and an incomplete, finding-bearing, unreaped, wrong-clean or undone
    record is refused whatever its count: (ok, cases)."""
    child = FAKE_PID_BASE + 1

    def record(**kwargs):
        base = dict(points=SWEEP_FLOOR + 1, injected=SWEEP_FLOOR, complete=True, count=0, findings=[],
                    child=child, clean=[[child], None, 2], unreaped=None)
        base.update(kwargs)
        return base
    cases = dict(
        just_below=_sweep_accepted(True, record(points=SWEEP_FLOOR, injected=SWEEP_FLOOR - 1)),
        at_floor=_sweep_accepted(True, record(points=SWEEP_FLOOR + 1, injected=SWEEP_FLOOR)),
        just_above=_sweep_accepted(True, record(points=SWEEP_FLOOR + 2, injected=SWEEP_FLOOR + 1)),
        incomplete=_sweep_accepted(True, record(complete=False, clean=None)),
        found=_sweep_accepted(True, record(count=1, findings=[dict(point=3, raised="OSError")])),
        unreaped=_sweep_accepted(True, record(unreaped="scripted reap failure")),
        unclean=_sweep_accepted(True, record(clean=[[child], None, 3])),
        undone=_sweep_accepted(False, "hung"))
    ok = (cases["just_below"] is False and cases["at_floor"] is True and cases["just_above"] is True
          and not cases["incomplete"] and not cases["found"] and not cases["unreaped"]
          and not cases["unclean"] and not cases["undone"])
    return ok, cases


def _walk_tables(own):
    """The scripted /proc subtrees of the walk legs (_descendant_walk_held), keyed by leg: each legs value is
    (table for _walk_scripted, expected (found, problem), expected walks seen), each problems value (table, a
    problem mark). The fake pids sit past this host's pid ceiling (FAKE_PID_BASE); every path of theirs is
    scripted, so none is ever opened. The leadzero token is the leading-zero form of a fake pid, so even a
    revert of the leading-zero refusal walks no real pid."""
    base = FAKE_PID_BASE
    first, second, third = base + 1, base + 2, base + 3
    task = "/proc/{}/task".format
    directory = "/proc/{}/task/{}".format
    children = "/proc/{}/task/{}/children".format
    refusal = PermissionError(errno.EACCES, "refused by the self-test")
    gone = FileNotFoundError(errno.ENOENT, "exited")

    def settle(boundary):
        # distinct sets for walks 1 through boundary - 1; the table reuses its last walk, so the first two
        # equal walks are boundary - 1 and boundary, and the walk settles exactly at walk boundary
        return dict((walk, {task(own): [str(own)],
                            children(own, own): str(third + walk).encode("ascii"),
                            task(third + walk): [str(third + walk)],
                            children(third + walk, third + walk): b""})
                    for walk in range(1, boundary))
    legs = dict(
        reparented=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                         task(first): gone},
                     2: {task(own): [str(own)], children(own, own): str(second).encode("ascii"),
                         task(second): [str(second)], children(second, second): b""}},
                    ({second}, None), 3),
        exited=({1: {task(own): [str(own)],
                     children(own, own): "{} {}".format(first, second).encode("ascii"),
                     task(first): [str(first)], children(first, first): gone,
                     directory(first, first): gone,
                     task(second): [str(second)], children(second, second): b""}},
                ({first, second}, None), 2),
        threadmoved=({1: {task(own): [str(own), str(first)], children(own, own): b"",
                          children(own, first): b""},
                      2: {task(own): [str(own), str(first)], children(own, own): b"",
                          children(own, first): gone, directory(own, first): gone},
                      3: {task(own): [str(own)], children(own, own): str(third).encode("ascii"),
                          task(third): [str(third)], children(third, third): b""}},
                     ({third}, None), 4),
        bounded=({1: {task(own): [str(own)], children(own, own): str(PID_BOUND).encode("ascii"),
                      task(PID_BOUND): gone}},
                 ({PID_BOUND}, None), 2),
        settled_early=(settle(2), ({third + 1}, None), 2),
        settled_at_bound=(settle(SCAN_WALKS), ({third + SCAN_WALKS - 1}, None), SCAN_WALKS))
    problems = dict(
        refused=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                      task(first): [str(first)], children(first, first): refusal}}, "unreadable"),
        malformed=({1: {task(own): [str(own)], children(own, own): b"not-a-pid"}}, "non-pid"),
        zero=({1: {task(own): [str(own)], children(own, own): b"0"}}, "non-pid entry b'0'"),
        leadzero=({1: {task(own): [str(own)],
                       children(own, own): "0{}".format(first).encode("ascii")}},
                  "non-pid entry b'0{}'".format(first)),
        oversized=({1: {task(own): [str(own)], children(own, own): b"9" * 5000}}, "non-pid"),
        width=({1: {task(own): [str(own)], children(own, own): b"9" * 11}}, "non-pid"),
        pastbound=({1: {task(own): [str(own)], children(own, own): b"2147483648"}},
                   "non-pid entry b'2147483648'"),
        taskname=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                       task(first): ["not-a-tid"]}}, "not-a-tid"),
        taskzero=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                       task(first): ["0"]}}, "non-pid entry '0'"),
        taskwidth=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                        task(first): ["9" * 11]}}, "non-pid"),
        tasknonascii=({1: {task(own): [str(own)], children(own, own): str(first).encode("ascii"),
                           task(first): ["\u0662\u0663"]}}, "non-pid"),
        probe=({1: {task(own): [str(own)],
                    children(own, own): FileNotFoundError(errno.ENOENT, "absent"),
                    directory(own, own): refusal}}, "unlistable"),
        missing=({1: {task(own): [str(own)],
                      children(own, own): FileNotFoundError(errno.ENOENT, "absent"),
                      directory(own, own): ["cwd"]}}, "task directory present"),
        unsettled=(settle(SCAN_WALKS + 1), "unsettled"))
    return legs, problems


def _descendant_walk_held(adopted):
    """Whether the descendant walk is held at class width: a stand-in /proc (_walk_stub: os.listdir and open
    patched for the scripted paths only, every other path reaching the real call) drives each walk shape of
    _walk_tables through _walk_scripted, broken walks are driven end to end through _pipe_writers and
    _witnessed, and live legs rerun the cleanup over real processes: (verdict, {leg: (ok, ...)}). Leg
    reparented: an intermediate parent exits mid-walk; its child, reparented to this subreaper, is found by
    the repeated walk. Leg exited: a recorded child whose task directory is gone is skipped as exited and its
    sibling is still found. Leg threadmoved: a task of a live recorded process disappears between two walks
    that agree on pids (the thread-exit move: an exiting thread's children move to a sibling task of the same
    process, no pid exiting and no pidfd reading ready), so the task agreement alone must refuse the agreement
    and walk on until the moved child is found at walk 4; on pid agreement alone the walk settles at walk 2
    with that child missing. Leg bounded: the token at PID_BOUND exactly is a pid. Legs settled_early and
    settled_at_bound, with leg bound pinning SCAN_WALKS to its literal value: a walk settles at walk 2, and at
    walk SCAN_WALKS exactly, while leg unsettled, whose first agreement would come one walk past the bound, is
    a named problem, never a partial candidate list. Every legs entry asserts its observed walk count, so an
    acceptance that needs extra walks (a minimum-walk mutant included) reads red. Legs refused, malformed,
    zero, leadzero, oversized, width, pastbound, taskname, taskzero, taskwidth, tasknonascii, missing and
    probe: a children read refused with the task still present, children and task-name tokens that are not a
    pid (malformed, zero, a leading zero, an oversized number that must never raise, a token one digit past
    PID_BOUND's width, PID_BOUND + 1 at that width, and a non-ASCII digit name that int would accept), a
    children file absent with its task directory listable (a kernel without the interface), and a
    task-directory probe refused other than by the task having exited are each one named problem. Leg preview:
    the token preview bound is exact at 39, 40 and 41 characters, bytes and str. Legs pidfd-refused and
    alive-probe: a pidfd_open refused on a walked live pid is the walk's named problem, never a silently
    skipped exit test (red under a skip), and an exit only the later walk's recorded pidfd can observe refuses
    the agreement, the scripted probe sequence consumed exactly. Legs probe-recorded and probe-accept: a
    liveness probe failing at the recording, and at the acceptance, is each the walk's named problem, never an
    escaping exception. Leg probe-high: the probe holds at descriptor 1024 and above for a live and for an
    exited process (_high_descriptor_probes, cannot-evaluate where a descriptor at or past 1024 cannot be
    allocated, the allocation itself deciding, or where its probe child does not exit in its bound).
    Legs probe-writers and witnessed-probe: the probe-broken walk reaches _pipe_writers as ([], [], problem)
    and a real witnessed cleanup as exactly one ("/proc scan", reason) entry naming the probe. Leg
    probe-interrupt: an injected exception at every opcode of _recorded_alive leaves no descriptor behind
    (_recording_sweep, run in a child, its completing run proving the recording was exercised: at least
    SWEEP_FLOOR completed injections, the completing run counted as an attempt and never as an injection, and
    the forked child found; leg sweep-floor holds that floor at its boundary over synthetic records). Legs writers, witnessed-eacces and
    witnessed-eio: the probe-broken walk reaches _pipe_writers as ([], [], problem) and reaches a real
    witnessed cleanup as exactly one
    ("/proc scan", reason) entry naming the probe, with no candidate derived from the incomplete walk and
    nothing signalled (_broken_walk_witnessed, EACCES and EIO driven). Leg tokens: each clause of _pid_token
    at its boundaries, str and bytes, just inside, at and just outside each end, the width clause held by
    conversion-limit-sized entries only it can refuse. Leg tokens-restore: that fixture leaves the caller's
    integer-conversion setting as it found it, the setting read back after each call, from the disabled
    setting and three limits (the smallest, the default and one above it), on the return path and under an
    escaping judgement (_conversion_limit_restored), and that check's own restore is read back after it
    returns, all in a disposable child, so this process's setting is never written (_conversion_limit_observed).
    Leg probe-events: the probe
    decides by the event mask (POLLIN or POLLHUP is the exit; POLLERR and POLLNVAL each raise, never read as
    an exit or as alive). Legs probe-error-recorded and probe-error-accept: a poll-error event at the
    recording, and at the acceptance, is the named problem of the walk, end to end through the real probe.
    Leg probe-limits: the capability decision is the allocation itself, at hard bounds 1024, 1025, 1026 and
    1050, with a raised soft bound restored; a case whose bounds this host's hard RLIMIT_NOFILE refuses is
    named cannot-evaluate, never False. Leg probe-stopped: a probe child that stops itself is killed by
    its pid at the deadline, its reap confirmed, and named unexercised, never a hang; leg
    probe-stopped-unreaped: that stalled child still unreaped at the reap deadline is False, and
    probe-stopped red, never a pass. Leg holder-probe: a liveness probe
    failing inside _kill_holder is NOT_EVALUATED, named, nothing signalled. Leg fork-verdict: the double-fork
    verdict table (_double_fork_verdict): the missed-precondition branches (B already exited, D already
    visible) are each None, never a pass, and a missing D alone and a missing A alone are each False. Leg
    reap-deadline: _reap_within over a scripted still-live child makes no blocking wait and returns the named
    UNREAPED result, one SIGKILL sent. Leg probe-cleanup: the soft-bound restore of the high-descriptor probe
    survives an injected close failure (OSError and RuntimeError), an injected reap failure, a failed raise
    and a failed restore, each named (a failed restore under an escaping exception as its note), the
    restore the last limit call. Leg probe-capped: _limited_descriptor_probes under lowered host hard bounds
    1040 and 1024 reads cannot-evaluate, never False; leg capped-host: probe-capped itself, run where the
    inherited hard bound is 1024 and 1030, is None, never False. Leg holder-exited: the cleanup ownership
    rule (_ended_by_pid): an already-exited child is observed without reaping and never signalled, a live
    one killed before any reap; leg holder-fixture: the real holder fixture over a child already exited
    before its liveness observation sends no kill and makes no blocking wait. Leg double-fork: the live
    hook-timed double fork (_double_fork_found), cannot-evaluate without a child subreaper or with its
    timing precondition missed in every attempt; leg fork-cleanup: its cleanup findings are named with their
    pids, an EIO at a cleanup reap turns a held walk into None, never True, and a missing D with an
    unreaped pid stays False, never retried into success (None without a child subreaper). Leg unrelated:
    _unrelated_unread."""
    legs, problems = _walk_tables(os.getpid())
    outcomes = {}
    for name, (table, want, walks) in legs.items():
        got, seen = _walk_scripted(table)
        outcomes[name] = (got == want and seen == walks, got, want, seen, walks)
    for name, (table, mark) in problems.items():
        (found, problem), _seen = _walk_scripted(table)
        outcomes[name] = (found == set() and problem is not None and mark in problem, found, problem)
    outcomes["bound"] = (SCAN_WALKS == 8, SCAN_WALKS, 8)
    outcomes["preview"] = (_token_preview(b"9" * 39) == b"9" * 39 and _token_preview(b"9" * 40) == b"9" * 40
                           and _token_preview(b"9" * 41) == b"9" * 37 + b"..."
                           and _token_preview("x" * 39) == "x" * 39 and _token_preview("x" * 40) == "x" * 40
                           and _token_preview("x" * 41) == "x" * 37 + "...",
                           _token_preview(b"9" * 41))
    broken, _mark = problems["probe"]
    listing, opened, _state = _walk_stub(broken, os.getpid())
    with _patched(os, "listdir", listing), _patched(builtins, "open", opened):
        writers = _pipe_writers(0, 0)
    outcomes["writers"] = (writers[0] == [] and writers[1] == [] and writers[2] is not None
                           and "unlistable" in writers[2], writers)
    outcomes["witnessed-eacces"] = _broken_walk_witnessed(PermissionError(errno.EACCES,
                                                                          "refused by the self-test"))
    outcomes["witnessed-eio"] = _broken_walk_witnessed(OSError(errno.EIO, "io error of the self-test"))
    outcomes["pidfd-refused"] = _pidfd_open_refused()
    outcomes["alive-probe"] = _alive_probe_refuses()
    outcomes["probe-recorded"] = _probe_failure_named(1, 1, "liveness probe of recorded pid")
    outcomes["probe-accept"] = _probe_failure_named(3, 2, "liveness probe at the walk acceptance")
    outcomes["probe-high"] = _high_descriptor_probes()
    outcomes["probe-writers"] = _liveness_broken_pipe_writers()
    outcomes["witnessed-probe"] = _probe_broken_witnessed()
    outcomes["probe-interrupt"] = _recording_swept()
    outcomes["tokens"] = _token_boundaries()
    outcomes["tokens-restore"] = _conversion_limit_observed()
    outcomes["probe-events"] = _probe_event_masks()
    outcomes["probe-error-recorded"] = _probe_error_recorded()
    outcomes["probe-error-accept"] = _probe_error_accepted()
    outcomes["probe-limits"] = _limited_descriptor_probes()
    outcomes["probe-stopped"] = _stopped_probe_named()
    outcomes["holder-probe"] = _holder_probe_failure_named()
    outcomes["fork-verdict"] = _double_fork_verdicts()
    outcomes["sweep-floor"] = _sweep_floor_boundary()
    outcomes["reap-deadline"] = _unreaped_named()
    outcomes["probe-cleanup"] = _cleanup_restores_limit()
    outcomes["probe-capped"] = _capped_limits_named()
    outcomes["holder-exited"] = _exited_child_unsignalled()
    outcomes["holder-fixture"] = _holder_exited_unsignalled()
    outcomes["probe-stopped-unreaped"] = _stopped_unreaped_named()
    outcomes["capped-host"] = _capped_host_named()
    if adopted is True:
        outcomes["double-fork"] = _attempted(_double_fork_found)
        outcomes["fork-cleanup"] = _double_fork_cleanup_named()
    else:
        outcomes["double-fork"] = (None, "no child subreaper ({}): the reparenting race cannot be driven"
                                   .format(adopted))
        outcomes["fork-cleanup"] = (None, "no child subreaper ({}): the double-fork cleanup cannot be driven"
                                    .format(adopted))
    outcomes["unrelated"] = _unrelated_unread()
    return _combined(entry[0] for entry in outcomes.values()), outcomes


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
    (every holder of the witness died), reaped (the child was reaped when start_child returned), unevaluated (every
    holder witness cleanup could not evaluate, named: _witnessed))."""
    forked = []

    def launch(witness):
        start = time.monotonic()
        result = start_child(witness, forked)
        return result, time.monotonic() - start
    (result, elapsed), pids, ended, unevaluated = _witnessed(launch, forked)
    pid = forked[0] if forked else None
    return dict(result=result, elapsed=elapsed, pid=pid, pids=pids, ended=ended,
                reaped=pid is not None and _reaped(pid), unevaluated=unevaluated)


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
    a holder witness cleanup could not evaluate (named in unevaluated), a role reported outside roles (WOKE among
    them: a run or a descendant that outlived its sleep, so no kill reached it in time), the role own reporting
    another pid than the forked one, or a result that is neither expected nor late. None (cannot evaluate) when
    it shows none of these but was not exercised (a role in roles never reported, or the result is late: the bound
    overtook the run before it did its part, as when the child starts late), or took bound or longer: its cleanup
    is proven (every holder ended, the child reaped, own reporting the forked pid, nothing outlived its sleep), so
    the overrun shows only that this process was slow, as under host scheduling. An elapsed time cannot tell that
    from a cleanup that kills late by bound or more but before any run outlives its sleep, so a cleanup that
    overruns the bound reads as cannot-evaluate, not as a pass; one that kills late by less than bound is not told
    from a clean run and still reads as a pass. Else True."""
    pids = run["pids"]
    if (run["pid"] is None or not run["ended"] or not run["reaped"] or run["unevaluated"] or not set(pids) <= roles
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
    for). The wait on a live leftover is a blocking waitpid with no kill (its pid is not known here), not
    bounded by this function: a stated residual, bounded only by the caller, as the window sweeps run inside
    _in_child, whose deadline (CHILD_TIMEOUT) kills and reaps a sweep blocked here; _reap_within does not
    cover it."""
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
    no injection; returns dict(points, injected, complete, count, findings), points counting every run
    attempted (the completing, uninjected run included) and injected only the runs before it, each of which
    an injection reached (points - 1 on completion: the completing run is an attempt, never an injection,
    and _swept compares injected with SWEEP_FLOOR), each finding a point after which a child of this process
    (alive or a zombie) or a descriptor remained, at which an exception other than the injected one left
    _in_child, or at which the gate child ran its caller's finally. Run only inside _in_child: it installs a
    trace function and expects no other child of its process."""
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
            return dict(points=ordinal, injected=ordinal - 1, complete=True, count=len(findings),
                        findings=findings[:10])
    return dict(points=SWEEP_LIMIT, injected=SWEEP_LIMIT, complete=False, count=len(findings),
                findings=findings[:10])


def _swept(probe):
    """True when a window sweep, run in a child, completed after at least SWEEP_FLOOR completed injections
    (the completing run is an attempt, never an injection, so a floor over points would overcount by one)
    with no finding."""
    done, sweep = probe
    return bool(done and sweep["complete"] and sweep["injected"] >= SWEEP_FLOOR and not sweep["count"])


def _window_floor_boundary():
    """The injection floor of _swept at its boundary, over synthetic window-sweep records (the real sweeps
    are gate/fork-window-cleaned and gate/child-never-unwinds): a completed sweep of SWEEP_FLOOR - 1
    injections (its points read SWEEP_FLOOR: the overcount a floor over attempts would accept) is refused,
    one of exactly SWEEP_FLOOR and one just past it are accepted, and an incomplete, finding-bearing or
    undone record is refused whatever its count: (ok, cases)."""
    def record(**kwargs):
        return (True, dict(dict(points=SWEEP_FLOOR + 1, injected=SWEEP_FLOOR, complete=True, count=0,
                                findings=[]), **kwargs))
    cases = dict(
        just_below=_swept(record(points=SWEEP_FLOOR, injected=SWEEP_FLOOR - 1)),
        at_floor=_swept(record(points=SWEEP_FLOOR + 1, injected=SWEEP_FLOOR)),
        just_above=_swept(record(points=SWEEP_FLOOR + 2, injected=SWEEP_FLOOR + 1)),
        incomplete=_swept(record(complete=False)),
        found=_swept(record(count=1, findings=[dict(point=3, raised="OSError")])),
        undone=_swept((False, "hung")))
    ok = (cases["just_below"] is False and cases["at_floor"] is True and cases["just_above"] is True
          and cases["incomplete"] is False and cases["found"] is False and cases["undone"] is False)
    return ok, cases


def _launch(path, *args, cwd=None):
    result = subprocess.run([sys.executable, "-I", "-B", str(path)] + list(args), capture_output=True, text=True,
                            timeout=CHILD_TIMEOUT, cwd=str(cwd or Path(path).parent), env=dict(LC_ALL="C.UTF-8"))
    return result.returncode, result.stdout + result.stderr


OWN_ENTRY = '\n\nif __name__ == "__main__":\n    _selftest_exit_report.exit_with(main())\n'
OWN_REL = "tools/" + Path(__file__).name
EXECUTION_GATE_REL = "tools/check_selftest_execution.py"


def _import_closure(seeds):
    """Every sibling module the seed scripts import, directly or through one another, with the seeds: read
    from their parsed imports at any depth, so an import deferred into a function (the execution gate imports
    the special-file precheck only when it runs a suite) is followed too. A name resolves to a sibling when
    the importing script's directory holds that name as a .py file, as each script puts its own directory
    first on sys.path; any other name is left to the interpreter's own path (the standard library) and is
    not copied."""
    closure, pending = set(), list(seeds)
    while pending:
        rel = pending.pop()
        if rel in closure:
            continue
        closure.add(rel)
        directory = rel.rpartition("/")[0]
        for node in ast.walk(ast.parse(_read_regular(ROOT / rel, rel), filename=rel)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module]
            else:
                continue
            for name in names:
                sibling = "{}/{}.py".format(directory, name.partition(".")[0])
                if (ROOT / sibling).is_file():
                    pending.append(sibling)
    return closure


def _guardless_copy(base):
    """A scratch repository holding this checker with its own entry deleted, beside every sibling module it or
    the execution gate imports (the execution gate, the report finalizer, the roster loader and the special-file
    precheck, derived by _import_closure), the real registry, every other runner it registers and the real
    launch rosters (before it launches any suite, the execution gate resolves every registered runner strictly
    and requires every registered suite to have a launch line in both rosters), or None when this file does
    not end in its own entry."""
    head, entry, tail = Path(__file__).read_text(encoding="utf-8").rpartition(OWN_ENTRY)
    if not entry or tail:
        return None
    root = Path(base) / "guardless"
    (root / "tools").mkdir(parents=True)
    (root / ".git").write_text("gitdir: absent\n", encoding="ascii")
    (root / OWN_REL).write_text(head + "\n", encoding="utf-8")
    carried = (_import_closure((OWN_REL, EXECUTION_GATE_REL)) | registry_runners(ROOT)) - {OWN_REL}
    for rel in (REGISTRY_REL, check_ci_parity.LOCAL_SOURCE, check_ci_parity.CI_SOURCE, *sorted(carried)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    return root


def _registered_check_ids():
    """This suite's registered check ids, read through the gate's own reader and schema validator: (set of ids,
    None), or (None, reason)."""
    try:
        data = tomllib.loads(_read_regular(ROOT / REGISTRY_REL, REGISTRY_REL).decode("utf-8"))
    except (CannotEvaluate, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError,
            MemoryError) as exc:
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

    # a passing tree checks the target, the registered runner and every script launched ahead of the gates
    checked = "checked {} launched scripts".format(2 + len(_scaffold_launches()))
    intact = _tree(base, "intact", ((TARGET, INTACT), (REGISTERED, INTACT)))
    add("intact/passes", intact, 0, checked)
    spliced_text = splice(INTACT, "class Second:\n", REPLACEMENT)
    add("splice/guard-missing", _tree(base, "spliced", ((TARGET, spliced_text), (REGISTERED, INTACT))), 1,
        TARGET + ": guard missing")
    for shape, text, reason in SHAPES:
        add("shape/" + shape, _tree(base, shape, ((TARGET, text), (REGISTERED, INTACT))), 1,
            TARGET + ": " + reason)
    for name, data in ACCEPTED:
        add("accepted/" + name, _tree(base, name, ((TARGET, data), (REGISTERED, INTACT))), 0, checked)
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
    add("roster/no-python", _tree(base, "roster-empty", ((REGISTERED, INTACT),),
                                  roster_text=_without_precheck(_roster_text(()))), 2, "no Python script")
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
            ("outside-runner", _registry_text(["lib/registered.py"]), "is not a literal tools/*.py"),
            ("bad-args",
             good.replace("expected-check-ids", 'args = ["--execution-report"]\nexpected-check-ids'),
             "args element")):
        root = _tree(base, "registry-" + case, scripts + (("lib/registered.py", INTACT),),
                     registry_text="" if text is None else text)
        if text is None:
            (root / REGISTRY_REL).write_bytes(good.encode("utf-8").replace(b"fixture-1", b"fixture-\xff"))
        add("registry/" + case, root, 2, reason)
    # a split registry (two rows sharing one runner, each with its own args) passes the shared schema
    split = "".join('\n[[suite]]\nid = "fixture-{0}"\nrunner = "{1}"\nexpected-check-ids = ["fixture-{0}"]\n'
                    'args = ["--lane", "{0}"]\n'.format(lane, REGISTERED) for lane in ("one", "two"))
    add("registry/split-rows",
        _tree(base, "registry-split", scripts, registry_text="format-version = 1\n" + split), 0, checked)
    return vectors


# A fixture gate run takes milliseconds; GATE_TIMEOUT (3 s) bounds it. Under a mutant, a run that bound overtakes
# is re-run once at this larger bound, so a scheduling stall completes there and its result decides.
BLOCK_CONFIRM = 10
# The (mutant, vector) pairs whose gate run may be credited as blocked when it still runs at BLOCK_CONFIRM: the
# mutant that drops O_NONBLOCK leaves the open of a FIFO control input waiting for a writer. A hang of any other
# pair cannot evaluate.
BLOCK_CREDIT = frozenset(("open-nonblock", vector) for vector in ("script/fifo", "roster/fifo", "registry/fifo"))
# What /proc/<pid>/wchan names while a process waits in the open of a FIFO for its other end: fifo_open in
# fs/pipe.c waits in wait_for_partner, which is what this check was written against; fifo_open is accepted for a
# kernel that inlines it.
FIFO_OPEN_WAITS = frozenset(("wait_for_partner", "fifo_open"))


def _fifo_open_evidence(pid):
    """Whether process pid is blocked opening a FIFO, read from /proc while it still runs: (True or False, what was
    read). True only when the machine is one whose syscall numbers were verified (_SYSCALLS), /proc/<pid>/syscall
    shows it inside an open-family call (open, creat, openat, openat2), and /proc/<pid>/wchan names a FIFO open wait
    (FIFO_OPEN_WAITS). Anything unreadable, or another call or wait, is False: no evidence."""
    numbers = _SYSCALLS.get(_MACHINE)
    if numbers is None:
        return False, "machine {!r} has no verified syscall numbers".format(_MACHINE)
    try:
        with open("/proc/{}/syscall".format(pid), encoding="ascii") as handle:
            call = handle.read().split()
        with open("/proc/{}/wchan".format(pid), encoding="ascii") as handle:
            wait = handle.read().strip()
    except (OSError, ValueError) as exc:
        return False, "unreadable ({})".format(exc)
    seen = "syscall {}; wchan {}".format(" ".join(call[:4]), wait)
    return bool(call) and call[0].isdigit() and int(call[0]) in numbers["open"] and wait in FIFO_OPEN_WAITS, seen


def _evidence_halves():
    """Whether each half of _fifo_open_evidence is necessary on its own, driven over synthesized readings for
    one pid that never exists (the open of its two /proc files is redirected for the call; every other open
    passes through): a reading inside an open-family call but in another wait carries no evidence, nor does one
    in a FIFO open wait but inside another call, an empty syscall field, or a non-numeric one; only an
    open-family call in a FIFO open wait does. (verdict, [(reading, verdict, expected)])."""
    numbers = _SYSCALLS.get(_MACHINE)
    if numbers is None:
        return False, "machine {!r} has no verified syscall numbers".format(_MACHINE)
    opening, waiting, foreign = min(numbers["open"]), sorted(FIFO_OPEN_WAITS)[0], max(numbers["open"]) + 1
    cases = (("{} 0x1 0x2".format(opening), waiting, True),
             ("{} 0x1 0x2".format(opening), "hrtimer_nanosleep", False),
             ("{} 0x1 0x2".format(foreign), waiting, False),
             ("", waiting, False),
             ("running", waiting, False))
    real, outcomes = open, []
    for call, wait, expected in cases:
        def reading(path, *args, call=call, wait=wait, **kwargs):
            if str(path) == "/proc/0/syscall":
                return io.StringIO(call)
            if str(path) == "/proc/0/wchan":
                return io.StringIO(wait)
            return real(path, *args, **kwargs)
        with _patched(builtins, "open", reading):
            verdict, seen = _fifo_open_evidence(0)
        outcomes.append((seen, verdict, expected))
    return all(verdict is expected for _seen, verdict, expected in outcomes), outcomes


def _vector_outcome(module, vector, mutant=None):
    """Run one vector against module (this gate when mutant is None, else the mutant it names), classifying its
    result: True (the vector holds), False (a completed run with another result: a failure of this gate, an
    exposure under a mutant), "blocked" (a run the mutation left blocked opening a FIFO, an exposure) or None
    (cannot evaluate). This gate is never re-run: its run overtaken by GATE_TIMEOUT is a failure. Under a mutant
    such a run is re-run once at BLOCK_CONFIRM, and a re-run that completes decides by its result, so no run earns
    exposure by hanging alone. A re-run still running at BLOCK_CONFIRM is "blocked" only when (mutant, vector) is
    in BLOCK_CREDIT and the re-run's own child, read before it is killed (_fifo_open_evidence), is blocked opening
    a FIFO; else it is None. A run whose killed child was still unreaped at the reap deadline ("unreaped (...)"
    or "hung; unreaped (...)", _in_child) is no completed run: under a mutant it is None, never an exposure,
    so no mutant earns exposure by hanging with an unreaped child either (leg unreaped-hang of
    _hang_credit); against this gate it is a failure. (outcome, detail)."""
    vid, root, code, text, patch = vector

    def run(timeout, expiring=None):
        with contextlib.ExitStack() as stack:
            if patch is not None:
                owner = check_ci_parity if patch[0] == "extract_local" else module
                stack.enter_context(_patched(owner, patch[0], patch[1]))
            return _gate(module, root, timeout=timeout, expiring=expiring)

    def unreaped(got):
        return isinstance(got, str) and got.startswith(("unreaped (", "hung; unreaped ("))
    got, output = run(GATE_TIMEOUT)
    if mutant is not None and unreaped(got):
        return None, (got, output)
    if got != "hung" or mutant is None:
        return (got == code and text in output), (got, output)
    evidence = []
    got, output = run(BLOCK_CONFIRM, lambda pid: evidence.append(_fifo_open_evidence(pid)))
    if unreaped(got):
        return None, (got, dict(evidence=evidence))
    if got != "hung":
        return (got == code and text in output), (got, output)
    declared = (mutant, vid) in BLOCK_CREDIT
    if declared and evidence and evidence[0][0] is True:
        return "blocked", (got, evidence[0][1])
    return None, (got, dict(declared=declared, evidence=evidence))


def _run_vectors(module, vectors, mutant=None):
    """Run every vector against module (this gate, or the mutant named mutant): [(vector id, outcome, detail)], with
    outcome as _vector_outcome gives it (True, False, "blocked" or None)."""
    return [(vector[0],) + _vector_outcome(module, vector, mutant) for vector in vectors]


def _mutation_verdicts(records, all_vectors):
    """The two mutation-aggregation verdicts from records [(mutant, named vector, {vector: outcome})], outcome as
    _vector_outcome gives it. A False or a "blocked" is an exposure; a True is not; a None (a hang that earned no
    credit) is neither and cannot evaluate. every-mutant-caught: False when a mutant's named vector held or has no
    outcome, else None when any outcome is None, else True. every-vector-discriminates: False when a non-twin vector
    was exposed under no mutant and has no None outcome, else None when any outcome is None, else True. Returns
    (caught, discriminates, detail)."""
    exposed = set(v for _m, _n, outcomes in records for v, ok in outcomes.items() if ok in (False, "blocked"))
    unresolved = [(mutant, v) for mutant, _n, outcomes in records for v, ok in outcomes.items() if ok is None]
    missed = [(mutant, named) for mutant, named, outcomes in records
              if named not in outcomes or outcomes[named] is True]
    never = sorted(set(all_vectors) - exposed - UNMUTATED)
    undecided = set(v for _m, v in unresolved)
    caught = False if missed else None if unresolved else True
    discriminates = False if set(never) - undecided else None if unresolved else True
    return caught, discriminates, dict(missed=missed, never=never, unresolved=unresolved)


def _unreaped_hang_uncredited():
    """A run whose killed child is still unreaped earns no mutation credit, driven through the real _gate,
    _in_child and _vector_outcome with only _reap_within stubbed (it reaps the killed child for real, then
    returns UNREAPED, as for a child in uninterruptible sleep past both reap deadlines) and GATE_TIMEOUT cut
    to WITNESS_TIMEOUT: a run that hangs reads "hung; unreaped (...)" (the hang visible, the reap failure
    attached) and is None under a mutant, so that mutant is not caught by it (an exposure here, from a bare
    "unreaped" read as a completed run, is the regression); a run that returns with its child unreaped is
    None under a mutant as well. The confirmation re-run refuses both unreaped forms the same way whatever
    evidence it carries, held by injected _gate result sequences, an initial hang then each unreaped form,
    for an undeclared pair and for a pair declared in BLOCK_CREDIT, each with no evidence, with a reading
    that shows no FIFO open wait and with one that shows it. The injected gate reports expiry with
    FAKE_PID_BASE + 1 (never a live process) whenever the case scripts a reading, and _fifo_open_evidence is
    replaced by a reader that returns that scripted reading. The real runner reports expiry only for a run
    that reads hung, and with that run's own child; scripting a reading for the returned form as well holds
    the refusal independent of evidence, so a refusal narrowed by evidence of any kind is exposed. The
    positive reading is shown to be crediting evidence by controls on the declared pair: an initial hang
    then a plain hang with that reading reads "blocked", crediting the mutant, while with no evidence or the
    negative reading it reads None, each with the same two runs and reader calls. A
    gate call past the scripted sequence reads as one more hung run, failing the expected call list, never an
    escaping error. In every unreaped case the outcome is None, both aggregate verdicts are unresolved, the
    two runs were made at GATE_TIMEOUT then BLOCK_CONFIRM, and the reader was called once with the reported
    pid when a reading was scripted and never otherwise (a confirmation run whose killed child is still
    unreaped read as a completed run, or as a block, an exposure crediting the mutant, is the regression):
    (ok, detail)."""
    this, real = sys.modules[__name__], _reap_within
    reported = FAKE_PID_BASE + 1

    def reaping(pid, wait):
        real(pid, wait)
        return UNREAPED
    vector = ("scratch/unreaped", ROOT, 0, "PASS", None)
    undeclared, declared = ("scratch-mutant", vector[0]), ("open-nonblock", "registry/fifo")
    readings = (("absent", None), ("negative", (False, "scripted reading: another wait")),
                ("positive", (True, "scripted reading: a FIFO open wait")))

    def confirmed(pair, form, reading):
        mutant, vid = pair
        results, calls, read = [("hung", ""), (form, "")], [], []

        def gate(module, root, timeout=GATE_TIMEOUT, forked=None, expiring=None):
            calls.append(timeout)
            got = results.pop(0) if results else ("hung", "")
            if expiring is not None and reading is not None:
                expiring(reported)
            return got

        def evidence(pid):
            read.append(pid)
            return reading
        with _patched(this, "_gate", gate), _patched(this, "_fifo_open_evidence", evidence):
            outcome = _vector_outcome(this, (vid, ROOT, 0, "PASS", None), mutant)[0]
        verdicts = _mutation_verdicts([(mutant, vid, {vid: outcome})], [vid])[:2]
        return outcome, verdicts, calls, read
    with _patched(this, "_reap_within", reaping), _patched(this, "GATE_TIMEOUT", WITNESS_TIMEOUT):
        hung = _vector_outcome(types.SimpleNamespace(run=lambda root: time.sleep(HANG_SECONDS)), vector,
                               "scratch-mutant")
        returned = _vector_outcome(types.SimpleNamespace(run=lambda root: 1), vector, "scratch-mutant")
    caught = _mutation_verdicts([("scratch-mutant", vector[0], {vector[0]: hung[0]})], [vector[0]])[0]
    confirm, want = {}, {}
    for pair in (undeclared, declared):
        for form in ("unreaped (scripted)", "hung; unreaped (scripted)"):
            for name, reading in readings:
                key = "{} {} {}: {}".format(pair[0], pair[1], name, form)
                confirm[key] = confirmed(pair, form, reading)
                want[key] = (None, (None, None), [GATE_TIMEOUT, BLOCK_CONFIRM],
                             [] if reading is None else [reported])
    controls = dict((name, confirmed(declared, "hung", reading)) for name, reading in readings)
    credited = dict((name, (("blocked", (True, True)) if name == "positive" else (None, (None, None)))
                     + ([GATE_TIMEOUT, BLOCK_CONFIRM], [] if reading is None else [reported]))
                    for name, reading in readings)
    ok = (hung[0] is None and str(hung[1][0]).startswith("hung; unreaped (") and returned[0] is None
          and str(returned[1][0]).startswith("unreaped (") and caught is None
          and declared in BLOCK_CREDIT and undeclared not in BLOCK_CREDIT and confirm == want
          and controls == credited)
    return ok, dict(hung=hung, returned=returned, caught=caught, confirm=confirm, controls=controls)


def _credit_verdict(detail):
    """The hung-credit verdict over detail as _hang_credit fills it: False when any case that needs no reading
    from the FIFO subject does not hold, the undeclared pair's stall (outside) and the unreaped hang
    (unreaped-hang) among them, decided before the evidence gate; else None (cannot evaluate) when that
    subject never showed its wait; else whether the evidence case credits the block."""
    unresolved = (None, (None, None))
    settled = ([detail[name][:2] for name in ("held", "outside", "no-evidence", "finite-delay")]
               == [(True, (False, False)), unresolved, unresolved, unresolved]
               and detail["held"][2] == [GATE_TIMEOUT, BLOCK_CONFIRM] and detail["this-gate"][0] is False
               and detail["this-gate"][2] == [GATE_TIMEOUT] and detail["subjects"][1][0] is False
               and detail["halves"][0] is True and detail["unreaped-hang"][0] is True
               and detail["all-hung"][0] == [False] and True not in detail["all-hung"][1])
    if not settled:
        return False
    if detail["subjects"][0][0] is not True:
        return None
    return detail["evidence"][:2] == ("blocked", (True, True))


def _stall_decided():
    """Whether _credit_verdict decides every case that needs no reading from the FIFO subject before the
    evidence gate, driven over injected details: with the undeclared pair's stall (outside) definitively not
    unresolved and the FIFO subject never showing its wait, the verdict is False, never cannot-evaluate; with
    outside unresolved and the same unshown wait it is None; and with every case holding and the wait shown it
    is True: (verdict, detail)."""
    unresolved = (None, (None, None))
    clean = {"held": (True, (False, False), [GATE_TIMEOUT, BLOCK_CONFIRM]),
             "outside": unresolved, "no-evidence": unresolved, "finite-delay": unresolved,
             "this-gate": (False, (False, False), [GATE_TIMEOUT]),
             "subjects": [(True, "waiting on the FIFO open"), (False, "sleeping")],
             "halves": (True, {}), "unreaped-hang": (True, {}), "all-hung": ([False], (False, False)),
             "evidence": ("blocked", (True, True), [GATE_TIMEOUT, BLOCK_CONFIRM])}
    unshown = [(False, "never showed its wait"), (False, "sleeping")]
    stalled = dict(clean, outside=("blocked", (True, True)), subjects=unshown)
    undecided = dict(clean, subjects=unshown)
    verdicts = [_credit_verdict(stalled), _credit_verdict(undecided), _credit_verdict(clean)]
    return verdicts == [False, None, True], dict(stalled=verdicts[0], undecided=verdicts[1], clean=verdicts[2])


def _hang_credit(vectors, base):
    """Whether a hang earns mutation credit only as a declared FIFO-open block with evidence, driven through
    _vector_outcome, _run_vectors and _mutation_verdicts with injected _gate result sequences: (verdict, detail).
    The evidence is read from real processes: one blocked opening a FIFO, one sleeping (a finite delay). Under
    open-nonblock on registry/fifo: hung then held reads True and leaves its mutant uncaught; hung then hung reads
    None without evidence or with the sleeper's, and "blocked", crediting the mutant, with the FIFO subject's.
    Hung then hung outside BLOCK_CREDIT reads None even with the FIFO subject's evidence. This gate, hung then
    held, reads False with no re-run. With every run hung and no evidence, every vector fails against this gate
    and neither aggregate passes. Each half of the evidence is necessary on its own (_evidence_halves). A run
    whose killed child is still unreaped, hung or not, is None under a mutant, never an exposure, on the
    initial run through the real _gate and on the confirmation re-run through injected result sequences,
    with or without evidence of a FIFO open wait (_unreaped_hang_uncredited). The cases
    that need no reading from the FIFO subject, the undeclared pair's stall among them, are decided first and
    fail as False; only when every one of them holds and that subject never showed its wait is the verdict None
    (cannot evaluate). The decision is _credit_verdict, held over injected details by _stall_decided."""
    this, twin, detail = sys.modules[__name__], "intact/passes", {}
    by_id = dict((vector[0], vector) for vector in vectors)
    fifo = Path(base) / "hang-credit-fifo"
    subjects = []

    def gate_of(results, subject=None, calls=None):
        queue = list(results)

        def gate(module, root, timeout=GATE_TIMEOUT, forked=None, expiring=None):
            if calls is not None:
                calls.append(timeout)
            got = queue.pop(0) if queue else ("hung", "")
            if got[0] == "hung" and expiring is not None and subject is not None:
                expiring(subject)
            return got
        return gate

    def case(mutant, vid, results, subject=None):
        calls = []
        with _patched(this, "_gate", gate_of(results, subject, calls)):
            (_vid, outcome, _detail), = _run_vectors(this, [by_id[vid]], mutant)
        return outcome, _mutation_verdicts([(mutant, vid, dict([(vid, outcome)]))], [vid, twin])[:2], calls
    os.mkfifo(fifo)
    try:
        for opens in (True, False):
            subjects.append(os.fork())
            if subjects[-1] == 0:
                try:
                    if opens:
                        os.open(fifo, os.O_RDONLY)
                    time.sleep(HANG_SECONDS)
                finally:
                    os._exit(0)
        blocked, sleeping = subjects
        deadline = time.monotonic() + WITNESS_WAIT
        while not _fifo_open_evidence(blocked)[0] and time.monotonic() < deadline:
            time.sleep(0.01)
        detail["subjects"] = [_fifo_open_evidence(blocked), _fifo_open_evidence(sleeping)]
        named = by_id["registry/fifo"]
        done = (named[2], named[3])
        detail["held"] = case("open-nonblock", "registry/fifo", [("hung", ""), done])
        detail["outside"] = case("open-nofollow", "script/symlink", [("hung", ""), ("hung", "")], blocked)
        detail["no-evidence"] = case("open-nonblock", "registry/fifo", [("hung", ""), ("hung", "")])
        detail["finite-delay"] = case("open-nonblock", "registry/fifo", [("hung", ""), ("hung", "")], sleeping)
        detail["evidence"] = case("open-nonblock", "registry/fifo", [("hung", ""), ("hung", "")], blocked)
        detail["this-gate"] = case(None, "intact/passes", [("hung", ""), (0, "checked 2 launched scripts")])
        all_vectors = [vector[0] for vector in vectors]
        with _patched(this, "_gate", gate_of([])):
            holds = _run_vectors(this, vectors)
            records = [(mutant, target, dict((v, ok) for v, ok, _d in _run_vectors(this, vectors, mutant)))
                       for mutant, _old, _new, target in MUTANTS]
        detail["all-hung"] = (sorted(set(ok for _v, ok, _d in holds), key=repr),
                              _mutation_verdicts(records, all_vectors)[:2])
    finally:
        for pid in subjects:
            if pid > 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
    detail["halves"] = _evidence_halves()
    detail["unreaped-hang"] = _unreaped_hang_uncredited()
    return _credit_verdict(detail), detail


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
     "except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:",
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
# The intact twin is the one vector no removal can fail: each finding vector is one edit from it. The split
# registry twin is the other: it differs from the intact tree only in a schema the execution gate owns, whose
# own self-test holds it.
UNMUTATED = {"intact/passes", "registry/split-rows"}


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
    # Problems recorded while the fixtures ran (a failed subreaper restore) are named on stderr whenever the
    # run unwinds with an exception at any point up to and including its final report (_surfaced encloses the
    # entire run); on a normal exit the report itself names them.
    restore_problems = []
    with _surfaced(restore_problems):
        return _self_test(restore_problems, report_path)


def _self_test(restore_problems, report_path):
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
    # While the fixtures run this process is a child subreaper (restored after, with the restore's result
    # checked: one that fails is surfaced below as a named failure, and named on stderr by self_test's
    # _surfaced wrapper when the run unwinds with an exception at any point up to and including this report),
    # so an orphaned witness holder stays its descendant and may be bound; when that is refused, such a holder
    # is named, never cleared
    with (tempfile.TemporaryDirectory(prefix="entry-guard-selftest-") as base,
          _subreaper(restore_problems) as adopted):
        vectors = _fixtures(base)
        # 1. every vector holds against this gate (its gate run is never re-run: a hang is a failure)
        outcomes = _run_vectors(this, vectors)
        not_held = [(vector, detail) for vector, ok, detail in outcomes if ok is not True]
        check("vector/every-vector-holds",
              bool(vectors) and len(outcomes) == len(vectors) and not not_held, not_held)
        # the no-python roster drops the precheck gate AND its bootstrap gate and their STOP lines: a bootstrap
        # gate left behind registers no roster member, so roster/no-python alone stays green when only the
        # precheck gate is dropped
        precheck_lines = (set(check_ci_parity.PRECHECK_BOOTSTRAP_GATE_LINES)
                          | set(check_ci_parity.PRECHECK_GATE_LINES) | {check_ci_parity.PRECHECK_STOP_LINE})
        full = [line.strip() for line in _roster_text(()).splitlines()]
        stripped = [line.strip() for line in _without_precheck(_roster_text(())).splitlines()]
        check("roster/no-python-drops-precheck-and-bootstrap",
              any(line in check_ci_parity.PRECHECK_GATE_LINES for line in full)
              and any(line in check_ci_parity.PRECHECK_BOOTSTRAP_GATE_LINES for line in full)
              and check_ci_parity.PRECHECK_STOP_LINE in full
              and not precheck_lines.intersection(stripped),
              sorted(precheck_lines.intersection(stripped)))

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
            caught = _launch(guardless / EXECUTION_GATE_REL, "--suite", SUITE_ID, cwd=guardless)
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
            outcomes = dict((name, ok) for name, ok, _detail in _run_vectors(module, vectors, mutant))
            records.append((mutant, vector, outcomes))
        caught, discriminates, mdetail = _mutation_verdicts(records, all_vectors)
        check("mutant/every-mutant-caught", bool(MUTANTS) and not anchors and caught,
              dict(mdetail, anchors=anchors))
        check("mutant/every-vector-discriminates", discriminates, mdetail)
        # a hang earns mutation credit only as a declared FIFO-open block with evidence read from the confirm
        # run's own child: injected _gate sequences through _vector_outcome, _run_vectors and the aggregation
        # (hung then held, hung then hung outside the set, inside it without evidence or with a sleeper's, and
        # with a FIFO-blocked process's), this gate never re-run, and an all-hung run that passes neither
        # aggregate
        check("mutant/hung-earns-no-credit", *_hang_credit(vectors, base))
        # the cases needing no reading from the FIFO subject, the undeclared pair's stall among them, are
        # decided before the evidence gate: a definite failure there is False, never cannot-evaluate
        check("mutant/stall-decided-before-evidence", *_stall_decided())

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
        # signals only a live process, never this one, that held the witness write end at the binding, observed
        # through a pidfd opened before it, so no kill reaches this process or a reused pid, whatever a run
        # reports; a write end dropped after the binding is a residual. A holder it cannot evaluate is named and
        # fails its vector, and an orphaned holder adopted by this subreaper is bound and killed (_kill_holder,
        # _witnessed)
        exercised = dict(result=("hung", ""), elapsed=WITNESS_TIMEOUT, pid=1, pids={"child": 1}, ended=True,
                         reaped=True, unevaluated=[])
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
        check("gate/witness-refusal-named", *_refused_named(adopted))
        check("gate/descendant-walk", *_descendant_walk_held(adopted))
        check("gate/subreaper-restored", *_subreaper_restored())
        check("gate/restore-named-on-unwind", *_restore_surfaced())

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
        # each sweep's acceptance floor counts completed injections, held at its 19/20/21 boundary over
        # synthetic records (_window_floor_boundary), so the completing run is never counted as an injection
        floor = _window_floor_boundary()
        sweep = _in_child(lambda: _window_sweep(False), CHILD_TIMEOUT)
        check("gate/fork-window-cleaned", _swept(sweep) and floor[0], (sweep, floor))
        sweep = _in_child(lambda: _window_sweep(True), CHILD_TIMEOUT)
        check("gate/child-never-unwinds", _swept(sweep) and floor[0], (sweep, floor))

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
    for trouble in restore_problems:
        harness.append(("subreaper/restore", trouble))

    failed = [(check_id, detail) for check_id, ok, detail in results if ok is False] + harness
    unevaluated = [(check_id, detail) for check_id, ok, detail in results if ok is None]
    if failed or unevaluated:
        print("SELF-TEST FAIL: entry-guard {} of {} check(s) failed".format(len(failed), len(results)) if failed
              else "SELF-TEST CANNOT EVALUATE: entry-guard {} of {} check(s) unresolved, none showing a failure "
              "(each is named below with its detail: a witnessed or timer vector was not exercised in any of its "
              "{} runs, a mutant's fixture run hung without earning credit, the hung-credit check's FIFO "
              "subject never showed its wait, a refusal fixture's dumpability setup or bindability probe could "
              "not be made, the double-fork walk leg had no child subreaper, was not exercised or missed "
              "its timing precondition, or the high-descriptor probe leg could not allocate a descriptor at "
              "or past 1024 or its probe child did not exit in its bound, or the probe-limits, probe-capped "
              "or capped-host leg could not set a case's RLIMIT_NOFILE bounds under this host's hard bound, "
              "or a mutant's fixture run left its killed child unreaped)".format(
                  len(unevaluated), len(results), ATTEMPTS))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        for check_id, detail in unevaluated:
            print("  {} (cannot evaluate): {!r}".format(check_id, detail))
        return 1 if failed else 2
    print("SELF-TEST PASS: entry-guard {} checks ({} vectors: splice reproduced and caught; displaced, "
          "duplicated, else-bearing, misnamed, callless, dormant-call, unreachable, nested and unlaunched-runner "
          "entries are findings; non-regular, unreadable, malformed and schema-invalid inputs fail closed; a "
          "split registry passes; {} "
          "mutants each caught (a hang credited only as a declared FIFO-open block that /proc shows), every vector "
          "discriminating; this gate's own lost guard caught by the execution gate; a caller timer survives the "
          "gate runner and keeps its expiry instant to within {:g} s plus a sub-microsecond truncation term (a move "
          "beyond that either way, and a reset to the starting countdown, are refused; a smaller move may not "
          "be seen) and a delayed timer read is no failure; an overrun is bounded as hung; an expired gate child, "
          "one that left its group, its descendants (an audit hook refusing the kills included) and an orphaned "
          "one are killed, and a run its bound overtook, or that overran its elapsed bound with its cleanup "
          "proven, is no failure, and a failure is never retried or masked; witness cleanup finds its candidates "
          "top-down from this process's own task children alone, repeated until two walks agree on pids and "
          "visited tasks with no exit observed by the probes (a poll error event is a named probe failure, "
          "never an exit; the hook-timed double fork is re-walked, never "
          "dropped, and a child moved by an exiting thread of a live process breaks the task agreement), reads "
          "no unrelated pid, names a walk, an exit test or a liveness probe it cannot complete or settle once "
          "as a /proc-scan problem, never as candidates, and names a witness write end still open with no "
          "writer killed and nothing named as an unresolved discovery, never ending silently; it never "
          "signals this process and signals only a live process "
          "that held the witness "
          "write end at the binding, observed through its pidfd (a write end dropped after the binding is a "
          "residual), and names every candidate it cannot evaluate (a known pid, or a walked descendant whose "
          "descriptors it cannot list or read; a holder outside its descendants and the known pids is beyond "
          "the walk, a stated residual); the subreaper flag is restored to the prior value read, "
          "the restore checked and never attempted after a refused set; orphaned holders {}; an exception at "
          "any point of the fork window leaves no child or "
          "descriptor and never unwinds a child into its caller; live surface {} scripts)".format(
              len(results), len(vectors), len(MUTANTS), TIMER_FLOOR + 2 * TIMER_BRACKET,
              "adopted by this child subreaper and bound" if adopted is True else
              "not adopted ({}): one whose binding is refused is named, not killed".format(adopted), count))
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
