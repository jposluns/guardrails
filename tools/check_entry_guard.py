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
    sys.stderr.write(
        "error: check_entry_guard.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import ast
import contextlib
import errno
import importlib.util
import io
import os
import signal
import stat
import subprocess
import tempfile
import tomllib
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


class _Hung(BaseException):
    """A gate run still blocked after GATE_TIMEOUT (BaseException, so no handler under test swallows it)."""


def _on_alarm(signum, frame):
    raise _Hung()


def _gate(module, root, timeout=GATE_TIMEOUT):
    """Run module.run(root); returns (exit code, or "hung" or "crashed (...)", combined output)."""
    out, err = io.StringIO(), io.StringIO()
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.run(root)
    except _Hung:
        code = "hung"
    except Exception as exc:
        code = "crashed ({})".format(type(exc).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return code, out.getvalue() + err.getvalue()


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


def _run_vectors(module, vectors):
    """Run every vector against module (this gate or a mutant of it); return [(vector id, ok, detail)]."""
    results = []
    for vector, root, code, text, patch in vectors:
        with contextlib.ExitStack() as stack:
            if patch is not None:
                owner = check_ci_parity if patch[0] == "extract_local" else module
                stack.enter_context(_patched(owner, patch[0], patch[1]))
            got, output = _gate(module, root)
        results.append((vector, got == code and text in output, (got, output)))
    return results


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
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    this = sys.modules[__name__]
    with tempfile.TemporaryDirectory(prefix="entry-guard-selftest-") as base:
        vectors = _fixtures(base)
        # 1. every vector holds against this gate
        outcomes = _run_vectors(this, vectors)
        failing = [(vector, detail) for vector, ok, detail in outcomes if not ok]
        check("vector/every-vector-holds", vectors and len(outcomes) == len(vectors) and not failing, failing)

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
        exposed, uncaught = set(), []
        mutant_dir = Path(base) / "mutants"
        mutant_dir.mkdir()
        for number, (mutant, old, new, vector) in enumerate(MUTANTS, 1):
            if gate_source.count(old) != 1:
                uncaught.append((mutant, "anchor found {} times".format(gate_source.count(old))))
                continue
            module = _load_mutant(gate_source.replace(old, new) + fixtures + self_test_source, mutant_dir,
                                  number)
            failed = set(name for name, ok, _detail in _run_vectors(module, vectors) if not ok)
            exposed |= failed
            if vector not in failed:
                uncaught.append((mutant, sorted(failed)))
        check("mutant/every-mutant-caught", MUTANTS and not uncaught, uncaught)
        never = sorted(set(vector for vector, _root, _code, _text, _patch in vectors) - exposed - UNMUTATED)
        check("mutant/every-vector-discriminates", not never, never)

    # 5. the live leg: the real surface passes and is no smaller than the recorded floor, both in process and
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

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: entry-guard {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: entry-guard {} checks ({} vectors: splice reproduced and caught; displaced, "
          "duplicated, else-bearing, misnamed, callless, dormant-call, unreachable, nested and unlaunched-runner "
          "entries are findings; non-regular, unreadable, malformed and schema-invalid inputs fail closed; {} "
          "mutants each caught, every vector discriminating; this gate's own lost guard caught by the execution "
          "gate; live surface {} scripts)".format(len(results), len(vectors), len(MUTANTS), count))
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
