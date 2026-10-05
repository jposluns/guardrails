#!/usr/bin/env python3
"""Entry-guard gate (entry-guard): every Python script the local check roster launches, and every runner the
self-test execution manifest registers, keeps its canonical module entry.

A script that loses its `if __name__ == "__main__":` block (a block splice that runs past its intended end, a
bad merge) still exits 0 when launched, with no output: its checks are defined and never called, and a runner
that judges a gate by exit code alone reads that as a pass. This gate holds the entry in place statically, so
the loss is a finding before any run is trusted.

Surface: every Python script tools/run_all_checks.sh launches, read through the parity gate's validated roster
loader (check_ci_parity.read_runner_text and extract_local, never a second shell parser), plus every runner in
tools/selftest_checks.toml. The gate is in the roster, so it checks itself.

Invariant, per script: the file parses, its top-level body holds exactly one `if` statement whose test is
`__name__ == "__main__"`, that statement is the last top-level statement, has no else (or elif), and makes at
least one call in its body, and no such `if` appears below the top level. Only that orientation of the test is
recognised; a reversed `"__main__" == __name__` reads as a missing guard.

Intent is carried by the declared surface: a deliberate removal (a script turned into a library module)
removes its roster line or registry row in the same reviewed change. A guard lost while the script is still
launched or registered is a finding. The gate does not read a diff.

  check_entry_guard.py              check the repository
  check_entry_guard.py --self-test  assert the gate's own vectors
Exit: 0 every script passes; 1 findings, each failing script named with one reason; 2 cannot evaluate (the
roster or registry is missing, unreadable or malformed, the roster loader reports a diagnostic, the roster
launches no Python script, or a listed script is missing, not a regular file, unreadable, not valid UTF-8, or
does not parse). An unparsable script is never read as passing. There is no warning tier.

It does not prove that the entry reaches the script's checks: a dispatch or check deleted inside a function
the entry calls is outside it, and is seen for a registered suite only by the execution gate. It sees no other
top-level statement a splice removes, does not read the CI workflow (the parity gate holds the two rosters
equal), does not see a script the roster never launches, and cannot tell a legitimate from an illegitimate
coordinated removal of an entry and its roster line in one reviewed change.
"""
import ast
import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_ci_parity  # noqa: E402  (the validated roster loader)

ROOT = Path(__file__).resolve().parents[1]
ROSTER_REL = check_ci_parity.LOCAL_SOURCE
REGISTRY_REL = "tools/selftest_checks.toml"
# The live leg of the self-test requires at least this many scripts on the real surface; lowering it is a
# reviewed change, so a roster that silently shrinks is seen.
LIVE_FLOOR = 107
CHILD_TIMEOUT = 60


class CannotEvaluate(Exception):
    """An input the gate cannot read or parse; the run exits 2."""


def _read_bytes(path):
    return Path(path).read_bytes()


def roster_scripts(root):
    """Return the set of Python scripts the roster launches, as repository-relative paths."""
    text, diagnostic = check_ci_parity.read_runner_text(Path(root) / ROSTER_REL, ROSTER_REL)
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
    """Return the set of runners the self-test execution manifest registers."""
    try:
        data = tomllib.loads(_read_bytes(Path(root) / REGISTRY_REL).decode("utf-8"))
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(REGISTRY_REL, type(exc).__name__))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CannotEvaluate("{}: malformed ({})".format(REGISTRY_REL, type(exc).__name__))
    suites = data.get("suite")
    if not isinstance(suites, list) or not suites:
        raise CannotEvaluate("{}: no [[suite]] rows".format(REGISTRY_REL))
    runners = set()
    for number, row in enumerate(suites, 1):
        runner = row.get("runner") if isinstance(row, dict) else None
        if not isinstance(runner, str) or not check_ci_parity.PY_TARGET_RE.fullmatch(runner):
            raise CannotEvaluate("{}: [[suite]] #{} has no literal tools/*.py runner".format(
                REGISTRY_REL, number))
        runners.add(runner)
    return runners


def parse_script(root, rel):
    """Parse one listed script; anything that cannot be read and parsed raises CannotEvaluate."""
    path = Path(root) / rel
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        raise CannotEvaluate("{}: missing".format(rel))
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(rel, type(exc).__name__))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(rel))
    try:
        data = _read_bytes(path)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(rel, type(exc).__name__))
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{}: not valid UTF-8".format(rel))
    try:
        return ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({})".format(rel, type(exc).__name__))


def _is_guard(node):
    test = node.test if isinstance(node, ast.If) else None
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and type(test.comparators[0].value) is str and test.comparators[0].value == "__main__")


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
    if not any(isinstance(inner, ast.Call) for statement in guard.body for inner in ast.walk(statement)):
        return "guard body has no call"
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


def _indented(text):
    return "".join("    " + line + "\n" for line in text.splitlines())


SHAPES = (
    ("late-statement", INTACT + "\nprint('late')\n", "guard not the last statement"),
    ("two-guards", INTACT + "\n\n" + GUARD, "more than one guard"),
    ("guard-else", INTACT + "else:\n    pass\n", "guard has an else"),
    ("guard-pass", INTACT.replace("    sys.exit(main())\n", "    pass\n"), "guard body has no call"),
    ("nested-if", INTACT.replace(GUARD, "if True:\n" + _indented(GUARD)), "guard nested below the top level"),
    ("nested-def", INTACT.replace(GUARD, "def entry():\n" + _indented(GUARD)),
     "guard nested below the top level"),
)


def splice(text, start_line, replacement):
    """Replace from the line equal to start_line up to the next line that starts with `class`, or to the end
    of the file when there is none: the block splice whose overrun this gate exists to catch."""
    lines = text.splitlines(True)
    start = lines.index(start_line)
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("class")),
               len(lines))
    return "".join(lines[:start] + [replacement] + lines[end:])


def _roster_text(entries):
    """The real roster with its gate lines replaced by fixture launches, so the loader's grammar holds."""
    lines = (ROOT / ROSTER_REL).read_text(encoding="ascii").splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith('run_gate "'))
    kept = [line for line in lines if not line.startswith('run_gate "')]
    launches = ['run_gate "fixture-{}" python3 -I -B {}'.format(number, rel)
                for number, rel in enumerate(entries, 1)]
    return "\n".join(kept[:first] + launches + kept[first:]) + "\n"


def _registry_text(runners):
    rows = "".join('\n[[suite]]\nid = "fixture-{}"\nrunner = "{}"\nexpected-check-ids = []\n'.format(
        number, rel) for number, rel in enumerate(runners, 1))
    return "format-version = 1\n" + rows


def _tree(base, name, scripts, roster, registry, registry_text=None):
    """Write a synthetic repository; scripts is a sequence of (relative path, text) pairs."""
    root = Path(base) / name
    (root / "tools").mkdir(parents=True)
    for rel, text in scripts:
        (root / rel).write_text(text, encoding="utf-8")
    (root / ROSTER_REL).write_text(_roster_text(roster), encoding="ascii")
    (root / REGISTRY_REL).write_text(
        _registry_text(registry) if registry_text is None else registry_text, encoding="utf-8")
    return root


def _gate(root):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run(root)
    return code, out.getvalue() + err.getvalue()


def _launch(path):
    result = subprocess.run([sys.executable, "-I", "-B", str(path)], capture_output=True, text=True,
                            timeout=CHILD_TIMEOUT, cwd=str(Path(path).parent), env=dict(LC_ALL="C.UTF-8"))
    return result.returncode, result.stdout + result.stderr


@contextlib.contextmanager
def _patched(name, replacement):
    module = sys.modules[__name__]
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        yield
    finally:
        setattr(module, name, original)


_ORIGINAL_PARSE = parse_script
_ORIGINAL_READ = _read_bytes


def _skipping_parse(root, rel):
    """Mutant: an unreadable or unparsable script read as an empty module instead of cannot-evaluate."""
    try:
        return _ORIGINAL_PARSE(root, rel)
    except CannotEvaluate:
        return ast.Module(body=[], type_ignores=[])


def _denying_reader(path):
    """Refuse the read of the one script under a tree named `unreadable`, whoever runs the self-test."""
    path = Path(path)
    if path.name == "target.py" and path.parent.parent.name == "unreadable":
        raise PermissionError("read refused by the self-test")
    return _ORIGINAL_READ(path)


def self_test():
    results = []

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))

    target, registered = "tools/target.py", "tools/registered.py"
    with tempfile.TemporaryDirectory(prefix="entry-guard-selftest-") as base:
        # 1. the intact twin of every shape below passes, and run directly it reaches its checks
        intact = _tree(base, "intact", ((target, INTACT), (registered, INTACT)), [target], [registered])
        code, output = _gate(intact)
        check("intact/passes", code == 0 and "checked 2 launched scripts" in output, output)
        code, output = _launch(intact / target)
        check("intact/run-reaches-checks", code == 0 and "checks ran" in output, output)

        # 2. the splice reproduction: the overrun drops the entry, and the script runs green and silent
        spliced_text = splice(INTACT, "class Second:\n", REPLACEMENT)
        spliced = _tree(base, "spliced", ((target, spliced_text), (registered, INTACT)), [target], [registered])
        code, output = _launch(spliced / target)
        check("splice/silent-green-reproduced", code == 0 and output == "", output)
        code, output = _gate(spliced)
        check("splice/guard-missing-finding", code == 1 and target + ": guard missing" in output, output)

        # 3 to 7. a displaced, duplicated, else-bearing, empty, or nested entry is a finding
        finding_roots = [spliced]
        for shape, text, reason in SHAPES:
            root = _tree(base, shape, ((target, text), (registered, INTACT)), [target], [registered])
            finding_roots.append(root)
            code, output = _gate(root)
            check("shape/" + shape + "-finding", code == 1 and target + ": " + reason in output, output)

        # 8. a registered runner the roster never launches is still held to the entry
        unlaunched = _tree(base, "registered", ((target, INTACT), (registered, spliced_text)),
                           [target], [registered])
        finding_roots.append(unlaunched)
        code, output = _gate(unlaunched)
        check("registry/unlaunched-runner-finding", code == 1 and registered + ": guard missing" in output,
              output)

        # 9 and 10. an unparsable, undecodable, missing, non-regular, or unreadable script cannot be evaluated
        closed_roots = []
        for case in ("unparsable", "invalid-utf8", "missing", "directory", "unreadable"):
            root = _tree(base, case, ((registered, INTACT),), [target], [registered])
            if case == "unparsable":
                (root / target).write_text("def broken(:\n", encoding="utf-8")
            elif case == "invalid-utf8":
                (root / target).write_bytes(b"\xff" + INTACT.encode("utf-8"))
            elif case == "directory":
                (root / target).mkdir()
            elif case == "unreadable":
                (root / target).write_text(INTACT, encoding="utf-8")
            closed_roots.append(root)
        with _patched("_read_bytes", _denying_reader):
            for root in closed_roots:
                code, output = _gate(root)
                check("script/" + root.name + "-cannot-evaluate", code == 2 and target in output, output)

        # 11. a roster that launches no Python script, or no roster at all, cannot be evaluated
        empty = _tree(base, "empty-roster", ((registered, INTACT),), [], [registered])
        code, output = _gate(empty)
        check("roster/no-python-cannot-evaluate", code == 2 and "no Python script" in output, output)
        no_roster = _tree(base, "no-roster", ((target, INTACT), (registered, INTACT)), [target], [registered])
        (no_roster / ROSTER_REL).unlink()
        code, output = _gate(no_roster)
        check("roster/missing-cannot-evaluate", code == 2, output)

        # 12. a malformed or missing registry cannot be evaluated
        for case, registry_text in (("unparsable", "[[suite]\n"), ("no-suite", "format-version = 1\n"),
                                    ("no-runner", 'format-version = 1\n[[suite]]\nid = "x"\n'),
                                    ("bad-runner", '[[suite]]\nrunner = "../outside.py"\n')):
            root = _tree(base, "registry-" + case, ((target, INTACT),), [target], [],
                         registry_text=registry_text)
            code, output = _gate(root)
            check("registry/" + case + "-cannot-evaluate", code == 2, output)
        no_registry = _tree(base, "registry-missing", ((target, INTACT),), [target], [], registry_text="")
        (no_registry / REGISTRY_REL).unlink()
        code, output = _gate(no_registry)
        check("registry/missing-cannot-evaluate", code == 2, output)

        # revert legs: with detection removed the finding vectors pass, and with fail-closed handling
        # replaced by skipping the cannot-evaluate vectors stop exiting 2, so each vector discriminates
        with _patched("entry_finding", lambda tree: None):
            codes = [_gate(root)[0] for root in finding_roots]
        check("revert/detection-removed-vectors-pass", codes == [0] * len(finding_roots), codes)
        with _patched("parse_script", _skipping_parse), _patched("_read_bytes", _denying_reader):
            codes = [_gate(root)[0] for root in closed_roots]
        check("revert/skipping-parse-vectors-not-closed", 2 not in codes, codes)

    # 13. the live leg: the real surface passes and is no smaller than the recorded floor
    code, output = _gate(ROOT)
    marker = "entry-guard checked "
    count = int(output.split(marker, 1)[1].split()[0]) if code == 0 and marker in output else None
    check("live/real-tree-passes", count is not None and count >= LIVE_FLOOR, output)

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok]
    if failed:
        print("SELF-TEST FAIL: entry-guard {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: entry-guard {} checks (splice reproduced and caught; displaced, duplicated, "
          "else-bearing, empty, nested and unlaunched-runner entries are findings; unreadable inputs fail "
          "closed; live surface {} scripts)".format(len(results), count))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    if not args:
        return run(ROOT)
    print("usage: check_entry_guard.py [--self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
