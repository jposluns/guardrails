#!/usr/bin/env python3
"""Python-floor gate (PYTHON-FLOOR): the single source .aiqt/core/python-floor.toml names the Python
floor of the pack's executable tooling, and every other statement of that floor agrees with it.

Legs, in order:
  source         the single source parses, carries exactly its declared keys, and names the decided
                 floor (FLOOR below; a change to either is a reviewed change to both).
  pins           every `python-version:` interpreter pin in .github/workflows/*.yml and in PIN_FILES
                 (the shipped adopter CI template and its inline copy) equals the floor; each PIN_FILES
                 entry must carry at least one pin. Every line of a scanned file that contains the text
                 python-version must be one strict pin line (PIN_LINE_RE): optional space indentation,
                 an optional `- ` list marker, the bare key, a colon, one or more spaces, exactly one
                 plain, single-quoted or double-quoted scalar of [A-Za-z0-9._+-] characters (matching
                 quotes, nothing inside them but those characters), then optional whitespace and an
                 optional `#` comment. Any other line naming the key (a value on the next line or a
                 block scalar, a quoted key, a flow mapping, an empty value, a concatenated or
                 escaped value, a comment that names the key) is cannot-evaluate (exit 2), never a
                 pass. A plain (unquoted) pin of a floor whose text ends in 0 is a finding, since
                 YAML reads a plain 3.20 as the number 3.2.
  guard          each guarded-surfaces entrypoint opens with the canonical refusal guard, AST-matched
                 against GUARD_TEMPLATE with the file's own basename and the floor; only a module
                 docstring and `from __future__` imports may precede its `import sys`.
  dynamic        each guarded-surfaces entrypoint, run in a child (-I -B plus each of no flag, -O and
                 -OO) from a fresh empty working directory with sys.version_info patched to each of two
                 versions below the floor, exits 2 with empty stdout and the exact refusal on stderr, and
                 leaves the working directory empty; the guard prefix alone, run at the floor's .0
                 release and at the real interpreter version, continues.
  completeness   OFF until the source sets completeness-check = true (the unit that guards the last
                 shipped entrypoint switches it on): every shipped entrypoint, a .py file outside
                 EXCLUDED_TREES with a module-level `if __name__ == "__main__":`, must be listed in
                 guarded-surfaces.
  documentation  OFF until the source sets documentation-check = true (the declarations unit switches
                 it on): each DECLARATION_FILES entry must contain "Python <floor> or newer".

  check_python_floor.py              run every leg over this repository
  check_python_floor.py --self-test  fixture trees for every leg, plus red-on-revert: for each leg, a
                                     copy of this gate with that leg's check removed passes the fixture
                                     the intact gate refuses
  check_python_floor.py [--self-test] --execution-report ABS_PATH
                                     the self-test, also writing the executed check ids as JSON for
                                     tools/check_selftest_execution.py

Exit convention: 0 every enabled leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing,
unreadable or malformed source; a listed or scanned file that is missing, not a regular file, not UTF-8
or does not parse; a child launch failure or timeout; unavailable temporary storage).

DISCLOSED RESIDUAL. The dynamic leg patches sys.version_info inside a child of THIS interpreter. It is a
proxy for a real older interpreter, faithful only for a guard that reads sys.version_info, which is why
the guard leg pins the one canonical form. Each file is compiled whole before its guard runs, so an
interpreter too old to parse a later statement stops with a SyntaxError instead of the refusal; the
dynamic leg sees a compile failure only on the interpreter running it. The completeness scan walks the
working tree, not the git index: an untracked stray entrypoint is counted, and a directory named in
SKIPPED_DIR_NAMES is not walked. The pins leg does not parse YAML, and that is also its answer to a
malformed workflow: a pin can only be set by a line that names the key, so a file with no such line
has no pin to check, and every line that names it is held to the strict one-line form or the leg
cannot evaluate. The residual is a key spelled through YAML escapes (for example a double-quoted key
with backslash-u escapes, or an escaped line break inside the key), which no line names as written and this
leg does not see; the leg also scans only the files it names. The documentation leg matches the
exact phrase, not its meaning.

Run this gate isolated: python3 -I -B tools/check_python_floor.py
"""
import ast
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = ".aiqt/core/python-floor.toml"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "python-floor-selftest"
# The decided floor. The source leg asserts the single source names it; every other leg reads the
# floor from the single source.
FLOOR = (3, 14)
SOURCE_KEYS = {"format-version", "python-floor", "guarded-surfaces", "completeness-check",
               "documentation-check"}
WORKFLOWS_REL = ".github/workflows"
PIN_FILES = ("opf/enforcement/ci/github-actions.yml", "opf/tools/check_opf_doctor.py")
PIN_KEY = "python-version"
# The one accepted spelling of a line naming PIN_KEY (fullmatch on a "\n"-split line, one trailing "\r"
# removed). Group 1 is a plain value, group 2 single-quoted, group 3 double-quoted. A comment may not
# carry a character a YAML 1.1 reader treats as a line break.
PIN_LINE_RE = re.compile(
    r" *(?:- +)?python-version: +(?:([A-Za-z0-9._+-]+)|'([A-Za-z0-9._+-]+)'|\"([A-Za-z0-9._+-]+)\")"
    r"(?:[ \t]+#[^\r\x85\u2028\u2029]*|[ \t]*)")
# Not shipped (repo-only CI) or byte-exact vendored third-party code under a provenance manifest.
EXCLUDED_TREES = (".github/", "opf/tools/_vendor/")
SKIPPED_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}
DECLARATION_FILES = ("README.md", "docs/development.md", "site/development.html", "site/install.html",
                     "opf/site/adopt.md", "opf/site/adopt.html", "opf/spec/OPF-QUICKSTART.md",
                     ".preview/README.md")
SURFACE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\.py")
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
FLAG_SETS = ((), ("-O",), ("-OO",))
CHILD_TIMEOUT = 60
CONTINUED = "CONTINUED"
# The canonical guard. The CLI form: stdout stays empty and the process exits 2 (cannot evaluate).
GUARD_TEMPLATE = '''import sys

if tuple(sys.version_info[:2]) < ({major}, {minor}):
    sys.stderr.write(
        "error: {name} requires Python {major}.{minor} or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)
'''
# Child programs. sys.version_info is replaced by a plain tuple before the entrypoint (or the guard
# prefix) runs; the guard reads only sys.version_info, so the tuple stands in for an older interpreter.
REFUSAL_CHILD = (
    "import runpy, sys\n"
    "path = sys.argv[1]\n"
    "version = tuple(int(part) for part in sys.argv[2].split('.'))\n"
    "sys.argv = [path]\n"
    "sys.version_info = version + ('final', 0)\n"
    "runpy.run_path(path, run_name='__main__')\n")
BOUNDARY_CHILD = (
    "import sys\n"
    "prefix, version = sys.argv[1], sys.argv[2]\n"
    "if version != 'real':\n"
    "    sys.version_info = tuple(int(part) for part in version.split('.')) + ('final', 0)\n"
    "exec(compile(prefix, '<guard prefix>', 'exec'), {'__name__': '__main__'})\n"
    "print('" + CONTINUED + "')\n")
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret; reported as exit 2, never as a pass."""


def _read_text(path):
    """Read a regular, non-symlink UTF-8 file, or raise CannotEvaluate."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat: {}".format(path, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{}: cannot read as UTF-8: {}".format(path, exc))


def load_source(root):
    """Parse and schema-check the single source; return its normalized fields."""
    try:
        data = tomllib.loads(_read_text(root / SOURCE_REL))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(SOURCE_REL, exc))
    if set(data) != SOURCE_KEYS:
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            SOURCE_REL, sorted(data), sorted(SOURCE_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SOURCE_REL))
    floor_text = data["python-floor"]
    match = FLOOR_RE.fullmatch(floor_text) if isinstance(floor_text, str) else None
    if match is None:
        raise CannotEvaluate("{}: python-floor must be a \"MAJOR.MINOR\" string, got {!r}".format(
            SOURCE_REL, floor_text))
    for key in ("completeness-check", "documentation-check"):
        if type(data[key]) is not bool:
            raise CannotEvaluate("{}: {} must be a boolean".format(SOURCE_REL, key))
    surfaces = data["guarded-surfaces"]
    if not isinstance(surfaces, list) or not all(isinstance(item, str) for item in surfaces):
        raise CannotEvaluate("{}: guarded-surfaces must be a list of strings".format(SOURCE_REL))
    for item in surfaces:
        if not SURFACE_RE.fullmatch(item) or any(part in (".", "..") for part in item.split("/")):
            raise CannotEvaluate("{}: guarded-surfaces entry {!r} is not a repo-relative .py path"
                                 .format(SOURCE_REL, item))
    if surfaces != sorted(set(surfaces)):
        raise CannotEvaluate("{}: guarded-surfaces must be sorted and unique".format(SOURCE_REL))
    for item in surfaces:
        _read_text(root / item)
    return {"floor": (int(match.group(1)), int(match.group(2))), "surfaces": tuple(surfaces),
            "completeness": data["completeness-check"], "documentation": data["documentation-check"]}


def source_findings(source):
    if source["floor"] != FLOOR:
        return ["{}: python-floor is {}.{}, but the decided floor is {}.{}".format(
            SOURCE_REL, *source["floor"], *FLOOR)]
    return []


def pin_findings(root, floor):
    want = "%d.%d" % floor
    try:
        names = sorted(os.listdir(root / WORKFLOWS_REL))
    except OSError as exc:
        raise CannotEvaluate("{}: cannot list: {}".format(WORKFLOWS_REL, exc))
    targets = [(WORKFLOWS_REL + "/" + name, False) for name in names
               if name.endswith((".yml", ".yaml"))]
    targets += [(rel, True) for rel in PIN_FILES]
    findings = []
    for rel, required in targets:
        pins = 0
        for number, line in enumerate(_read_text(root / rel).split("\n"), 1):
            line = line[:-1] if line.endswith("\r") else line
            if PIN_KEY not in line:
                continue
            match = PIN_LINE_RE.fullmatch(line)
            if match is None:
                raise CannotEvaluate("{}:{}: unrecognized python-version spelling; write the pin on one "
                                     "line as python-version: 'X.Y'".format(rel, number))
            pins += 1
            plain, value = match.group(1), next(group for group in match.groups() if group is not None)
            if value != want:
                findings.append("{}:{}: python-version {!r} differs from the floor {!r}".format(
                    rel, number, value, want))
            elif plain is not None and want.endswith("0"):
                findings.append("{}:{}: python-version {} is unquoted; YAML reads it as a number, so "
                                "quote it as '{}'".format(rel, number, value, want))
        if required and not pins:
            findings.append("{}: carries no python-version pin (want {!r})".format(rel, want))
    return findings


def _parse(root, rel):
    try:
        return ast.parse(_read_text(root / rel), filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(rel, exc))


def _dump(node):
    return ast.dump(node, include_attributes=False)


def guard_text(name, floor):
    return GUARD_TEMPLATE.format(name=name, major=floor[0], minor=floor[1])


def _canonical(name, floor):
    return [_dump(node) for node in ast.parse(guard_text(name, floor)).body]


def _preamble_end(tree):
    """Index of the first statement after the module docstring and any `from __future__` imports."""
    body, index = tree.body, 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        index = 1
    while index < len(body) and isinstance(body[index], ast.ImportFrom) \
            and body[index].module == "__future__" and body[index].level == 0:
        index += 1
    return index


def guard_findings(root, surfaces, floor):
    findings = []
    for rel in surfaces:
        tree = _parse(root, rel)
        want = _canonical(Path(rel).name, floor)
        start = _preamble_end(tree)
        if [_dump(node) for node in tree.body[start:start + len(want)]] != want:
            findings.append(
                "{}: does not open with the canonical floor guard (only a docstring and `from "
                "__future__` imports may precede `import sys` and `if tuple(sys.version_info[:2]) < "
                "({}, {}):`, refusing as {} with exit 2; see GUARD_TEMPLATE)".format(
                    rel, floor[0], floor[1], Path(rel).name))
    return findings


def below_floor(floor):
    """The two patched versions the dynamic leg refuses: a late micro release of the previous minor,
    and the .0 release two minors back."""
    return ((floor[0], floor[1] - 1, 9), (floor[0], max(floor[1] - 2, 0), 0))


def expected_refusal(name, floor, version, executable):
    return ("error: %s requires Python %d.%d or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % ((name,) + tuple(floor) + tuple(version) + (executable or "unknown interpreter",)))


def _child(code, args, flags, cwd):
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", *flags, "-c", code, *args], cwd=cwd,
                              env=CHILD_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=CHILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("child launch failed: {}".format(exc))
    return (proc.returncode, proc.stdout.decode("utf-8", "backslashreplace"),
            proc.stderr.decode("utf-8", "backslashreplace"))


def refusal_observed(path, version, flags):
    """Run one entrypoint at a patched version; return (exit, stdout, stderr, working-dir entries)."""
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-cwd-") as cwd:
            rc, out, err = _child(REFUSAL_CHILD, [str(path), "%d.%d.%d" % version], flags, cwd)
            return rc, out, err, sorted(os.listdir(cwd))
    except OSError as exc:
        raise CannotEvaluate("temporary working directory: {}".format(exc))


def boundary_observed(prefix, version, flags):
    """Run a guard prefix at a patched version (or "real"); return (exit, stdout, stderr)."""
    arg = version if version == "real" else "%d.%d.%d" % version
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-cwd-") as cwd:
            return _child(BOUNDARY_CHILD, [prefix, arg], flags, cwd)
    except OSError as exc:
        raise CannotEvaluate("temporary working directory: {}".format(exc))


def guard_prefix(tree, name, floor):
    """Source of the top-level statements up to and including the canonical guard, or None."""
    want = _canonical(name, floor)[-1]
    for index, node in enumerate(tree.body):
        if _dump(node) == want:
            return ast.unparse(ast.Module(body=tree.body[:index + 1], type_ignores=[]))
    return None


def dynamic_findings(root, surfaces, floor):
    findings = []
    for rel in surfaces:
        name = Path(rel).name
        for version in below_floor(floor):
            want = (2, "", expected_refusal(name, floor, version, sys.executable), [])
            for flags in FLAG_SETS:
                got = refusal_observed(root / rel, version, flags)
                if got != want:
                    findings.append(
                        "{} at patched {}.{}.{} under {}: got exit {}, stdout {!r}, stderr "
                        "{!r}, working-dir entries {!r}; want exit 2, empty stdout, the exact refusal "
                        "and an untouched working directory".format(
                            rel, *version, " ".join(flags) or "no flag", *got))
        prefix = guard_prefix(_parse(root, rel), name, floor)
        if prefix is None:
            findings.append("{}: no canonical guard statement to run at the floor boundary".format(rel))
            continue
        for version in (floor + (0,), "real"):
            label = version if version == "real" else "%d.%d.%d" % version
            for flags in FLAG_SETS:
                rc, out, err = boundary_observed(prefix, version, flags)
                if (rc, out) != (0, CONTINUED + "\n"):
                    findings.append(
                        "{}: the guard prefix at {} under {} did not continue (exit {}, stdout "
                        "{!r}, stderr {!r})".format(rel, label, " ".join(flags) or "no flag", rc, out,
                                                      err))
    return findings


def _excluded(rel):
    return any(rel == tree.rstrip("/") or rel.startswith(tree) for tree in EXCLUDED_TREES)


def _has_main_block(tree):
    for node in tree.body:
        test = node.test if isinstance(node, ast.If) else None
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
                and test.left.id == "__name__" and len(test.ops) == 1 \
                and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1 \
                and isinstance(test.comparators[0], ast.Constant) \
                and test.comparators[0].value == "__main__":
            return True
    return False


def shipped_entrypoints(root):
    def _fail(exc):
        raise CannotEvaluate("cannot walk the tree: {}".format(exc))

    found = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_fail):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES
                             and not _excluded(prefix + name + "/"))
        for name in sorted(filenames):
            rel = prefix + name
            if name.endswith(".py") and not _excluded(rel) and _has_main_block(_parse(root, rel)):
                found.append(rel)
    return found


def completeness_findings(root, surfaces):
    listed = set(surfaces)
    return ["{}: a shipped entrypoint missing from guarded-surfaces in {}".format(rel, SOURCE_REL)
            for rel in shipped_entrypoints(root) if rel not in listed]


def documentation_findings(root, floor):
    phrase = "Python %d.%d or newer" % floor
    return ["{}: does not state {!r}".format(rel, phrase) for rel in DECLARATION_FILES
            if phrase not in _read_text(root / rel)]


def evaluate(root):
    """Run every enabled leg over root; return (exit code, report lines)."""
    try:
        source = load_source(root)
        floor = source["floor"]
        findings = []
        findings.extend(source_findings(source))
        findings.extend(pin_findings(root, floor))
        findings.extend(guard_findings(root, source["surfaces"], floor))
        findings.extend(dynamic_findings(root, source["surfaces"], floor))
        if source["completeness"]:
            findings.extend(completeness_findings(root, source["surfaces"]))
        if source["documentation"]:
            findings.extend(documentation_findings(root, floor))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    return 0, ["PASS: python floor {}.{} ({}): source, pins, guard and dynamic legs over {} "
               "guarded surface(s); completeness check {}, documentation check {}".format(
                   floor[0], floor[1], SOURCE_REL, len(source["surfaces"]),
                   "ON" if source["completeness"] else "OFF (completeness-check = false)",
                   "ON" if source["documentation"] else "OFF (documentation-check = false)")]


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
# Red-on-revert: each leg's call in evaluate(), removed in a copy of this gate.
REVERT_CALLS = (
    ("source", "source_findings(source)"),
    ("pins", "pin_findings(root, floor)"),
    ("guard", "guard_findings(root, source[\"surfaces\"], floor)"),
    ("dynamic", "dynamic_findings(root, source[\"surfaces\"], floor)"),
    ("completeness", "completeness_findings(root, source[\"surfaces\"])"),
    ("documentation", "documentation_findings(root, floor)"),
)


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _source_text(floor="3.14", surfaces=(), completeness=False, documentation=False, extra=""):
    return ("format-version = 1\npython-floor = {}\nguarded-surfaces = {}\ncompleteness-check = {}\n"
            "documentation-check = {}\n{}".format(
                json.dumps(floor), json.dumps(list(surfaces)), "true" if completeness else "false",
                "true" if documentation else "false", extra))


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(base, source=None, workflow_pin="3.14", template_pin="3.14", files=None, pin_quote="'"):
    """A clean tree; workflow_pin is the version on quality.yml line 6, or with pin_quote="" the whole
    text after that line's indentation."""
    root = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
    _write(root, SOURCE_REL, _source_text() if source is None else source)
    pin = "python-version: {0}{1}{0}".format(pin_quote, workflow_pin) if pin_quote else workflow_pin
    _write(root, WORKFLOWS_REL + "/quality.yml",
           "jobs:\n  q:\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n"
           "          {}\n".format(pin))
    _write(root, PIN_FILES[0], "      - uses: actions/setup-python@v5\n        with:\n"
           "          python-version: '{}'\n".format(template_pin))
    _write(root, PIN_FILES[1], "TEMPLATE = \"\"\"\n          python-version: '{}'\n\"\"\"\n".format(
        template_pin))
    for rel, text in (files or {}).items():
        _write(root, rel, text)
    return root


def _entry(guard, before="", after="open(\"RAN\", \"w\").close()\n"):
    return before + guard + "\n" + after + "\nif __name__ == \"__main__\":\n    pass\n"


def _has(lines, marker):
    return any(marker in line for line in lines)


def _self_test_cases(base):
    floor = FLOOR
    good = guard_text("demo.py", floor)
    demo = {"tools/demo.py": _entry(
        good, before="\"\"\"Fixture.\"\"\"\nfrom __future__ import annotations\n")}
    listed = _source_text(surfaces=["tools/demo.py"])

    check("fixture/clean-tree-passes", evaluate(_fixture(base))[0], 0)

    root = _fixture(base)
    (root / SOURCE_REL).unlink()
    check("source/missing-cannot-evaluate", evaluate(root)[0], 2)
    check("source/unparseable-cannot-evaluate",
          evaluate(_fixture(base, source="python-floor = \n"))[0], 2)
    check("source/unknown-key-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(extra="floor-note = \"x\"\n")))[0], 2)
    check("source/bool-format-version-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text().replace("format-version = 1", "format-version = true")))[0], 2)
    check("source/malformed-floor-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(floor="3.14.0")))[0], 2)
    check("source/non-bool-switch-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text().replace("completeness-check = false", "completeness-check = 0")))[0],
          2)
    check("source/escaping-surface-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(surfaces=["../demo.py"])))[0], 2)
    check("source/unsorted-surfaces-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text(surfaces=["tools/demo.py", "tools/a.py"]),
        files={"tools/a.py": _entry(guard_text("a.py", floor)), **demo}))[0], 2)
    check("source/missing-surface-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(surfaces=["tools/absent.py"])))[0], 2)
    code, lines = evaluate(_fixture(base, source=_source_text(floor="3.13"), workflow_pin="3.13",
                                    template_pin="3.13"))
    check("source/wrong-floor-finding", (code, _has(lines, "decided floor")), (1, True))

    code, lines = evaluate(_fixture(base, workflow_pin="3.12"))
    check("pins/workflow-mismatch-finding", (code, _has(lines, "quality.yml:6")), (1, True))
    root = _fixture(base)
    _write(root, PIN_FILES[0], "jobs: none\n")
    code, lines = evaluate(root)
    check("pins/template-without-pin-finding", (code, _has(lines, "carries no python-version pin")),
          (1, True))
    unrecognized = "quality.yml:6: unrecognized python-version spelling"
    for check_id, pin in (
            ("pins/block-scalar-value-cannot-evaluate", "python-version:\n            '3.12'"),
            ("pins/block-indicator-cannot-evaluate", "python-version: |\n            3.12"),
            ("pins/json-quoted-key-cannot-evaluate", '"python-version": "3.12"'),
            ("pins/concatenated-quotes-cannot-evaluate", "python-version: '3.14''3.12'"),
            ("pins/empty-value-cannot-evaluate", "python-version:   "),
            ("pins/empty-quoted-value-cannot-evaluate", "python-version: ''"),
            ("pins/flow-mapping-cannot-evaluate", "{python-version: 3.12}"),
            ("pins/mismatched-quotes-cannot-evaluate", "python-version: '3.14\""),
            ("pins/comment-mention-cannot-evaluate", "# python-version: '3.12'"),
            ("pins/version-file-input-cannot-evaluate", "python-version-file: .python-version"),
            ("pins/line-separator-cannot-evaluate", "python-version: 3.14\u2028.12"),
            ("pins/comment-line-separator-cannot-evaluate",
             "python-version: '3.14' # x\u2028python-version: '3.12'"),
            ("pins/bare-carriage-return-cannot-evaluate",
             "python-version: '3.14'\r          python-version: '3.12'")):
        code, lines = evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))
        check(check_id, (code, _has(lines, unrecognized)), (2, True))
    for check_id, pin in (
            ("pins/trailing-comment-passes", "python-version: '3.14'  # the floor"),
            ("pins/double-quoted-passes", 'python-version: "3.14"'),
            ("pins/list-marker-passes", "- python-version: '3.14'"),
            ("pins/crlf-line-passes", "python-version: '3.14'\r")):
        check(check_id, evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))[0], 0)
    code, lines = evaluate(_fixture(base, workflow_pin="python-version: 3.12", pin_quote=""))
    check("pins/wrong-single-line-finding",
          (code, _has(lines, "quality.yml:6: python-version '3.12' differs")), (1, True))
    root = _fixture(base, workflow_pin="python-version: 3.10", template_pin="3.10", pin_quote="")
    check("pins/unquoted-trailing-zero-finding",
          [line for line in pin_findings(root, (3, 10)) if "unquoted" in line],
          ["{}/quality.yml:6: python-version 3.10 is unquoted; YAML reads it as a number, so quote it "
           "as '3.10'".format(WORKFLOWS_REL)])

    check("guard/canonical-passes", evaluate(_fixture(base, source=listed, files=demo)), (0, [
        "PASS: python floor 3.14 ({}): source, pins, guard and dynamic legs over 1 guarded "
        "surface(s); completeness check OFF (completeness-check = false), documentation check OFF "
        "(documentation-check = false)".format(SOURCE_REL)]))
    guard_marker = "canonical floor guard"
    for check_id, text in (
            ("guard/absent-finding", _entry("import sys\n")),
            ("guard/wrong-floor-literal-finding",
             _entry(guard_text("demo.py", (floor[0], floor[1] - 1)))),
            ("guard/wrong-basename-finding", _entry(guard_text("other.py", floor))),
            ("guard/late-placement-finding", _entry(good, before="import os\n")),
            ("guard/assert-form-finding",
             _entry("import sys\n\nassert tuple(sys.version_info[:2]) >= (%d, %d)\n" % floor))):
        code, lines = evaluate(_fixture(base, source=listed, files={"tools/demo.py": text}))
        check(check_id, (code, _has(lines, guard_marker)), (1, True))

    path = _fixture(base, files=demo) / "tools" / "demo.py"
    version = below_floor(floor)[0]
    check("dynamic/refusal-exact-under-OO", refusal_observed(path, version, ("-OO",)),
          (2, "", expected_refusal("demo.py", floor, version, sys.executable), []))
    check("dynamic/below-floor-vectors", below_floor((3, 14)), ((3, 13, 9), (3, 12, 0)))
    code, lines = evaluate(_fixture(base, source=listed, files={
        "tools/demo.py": _entry(good, after="return\n")}))
    check("dynamic/compile-failure-finding",
          (code, _has(lines, "at patched"), _has(lines, guard_marker)), (1, True, False))
    prefix = guard_prefix(ast.parse(demo["tools/demo.py"]), "demo.py", floor)
    check("dynamic/boundary-continues-at-floor", boundary_observed(prefix, floor + (0,), ()),
          (0, CONTINUED + "\n", ""))
    check("dynamic/boundary-refuses-below-floor", boundary_observed(prefix, version, ())[:2], (2, ""))

    unguarded = {"tools/demo.py": _entry("import sys\n")}
    check("completeness/off-ignores-unguarded", evaluate(_fixture(base, files=unguarded))[0], 0)
    code, lines = evaluate(_fixture(base, source=_source_text(completeness=True), files=unguarded))
    check("completeness/on-unguarded-finding",
          (code, _has(lines, "tools/demo.py: a shipped entrypoint")), (1, True))
    check("completeness/on-all-listed-passes", evaluate(_fixture(
        base, source=_source_text(surfaces=["tools/demo.py"], completeness=True), files=demo))[0], 0)
    check("completeness/excluded-trees-ignored", evaluate(_fixture(
        base, source=_source_text(completeness=True), files={
            ".github/repo_only.py": _entry("import sys\n"),
            "opf/tools/_vendor/lib/__main__.py": _entry("import sys\n"),
            ".venv/lib/tool.py": _entry("import sys\n"),
            "tools/helper.py": "VALUE = 1\n"}))[0], 0)

    declared = dict.fromkeys(DECLARATION_FILES, "Requires Python 3.14 or newer.\n")
    check("documentation/off-ignores-missing", evaluate(_fixture(base))[0], 0)
    code, lines = evaluate(_fixture(base, source=_source_text(documentation=True), files=dict(
        declared, **{DECLARATION_FILES[0]: "Requires Python.\n"})))
    check("documentation/on-missing-phrase-finding",
          (code, [line for line in lines if "does not state" in line]),
          (1, ["FAIL: README.md: does not state 'Python 3.14 or newer'"]))
    check("documentation/on-present-passes",
          evaluate(_fixture(base, source=_source_text(documentation=True), files=declared))[0], 0)
    check("documentation/on-absent-file-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(documentation=True)))[0], 2)

    _red_on_revert(base, good, declared)


def _gate_rc(root, gate_source):
    _write(root, "tools/check_python_floor.py", gate_source)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(root / "tools" / "check_python_floor.py")], cwd=root,
            env=CHILD_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=CHILD_TIMEOUT * 20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("gate copy launch failed: {}".format(exc))
    return proc.returncode


def _red_on_revert(base, good, declared):
    """Each leg red on its fixture with the intact gate, green on the same fixture with that leg's
    check removed from a copy of this gate."""
    gate_source = _read_text(Path(__file__).resolve())
    mutants = {}
    for leg, call in REVERT_CALLS:
        line = "findings.extend(" + call + ")"
        if gate_source.count(line) != 1:
            raise CannotEvaluate("red-on-revert anchor for the {} leg occurs {} times".format(
                leg, gate_source.count(line)))
        mutants[leg] = gate_source.replace(line, "findings.extend(())")
    listed = _source_text(surfaces=["tools/demo.py"])
    cases = (
        ("source", dict(source=_source_text(floor="3.13"), workflow_pin="3.13", template_pin="3.13")),
        ("pins", dict(workflow_pin="3.12")),
        ("guard", dict(source=listed, files={"tools/demo.py": _entry(good, before="import os\n")})),
        ("dynamic", dict(source=listed, files={"tools/demo.py": _entry(good, after="return\n")})),
        ("completeness", dict(source=_source_text(completeness=True),
                              files={"tools/demo.py": _entry("import sys\n")})),
        ("documentation", dict(source=_source_text(documentation=True), files=dict(
            declared, **{DECLARATION_FILES[-1]: "Requires Python.\n"}))),
    )
    results = {}
    for leg, kwargs in cases:
        results[leg] = (_gate_rc(_fixture(base, **kwargs), gate_source),
                        _gate_rc(_fixture(base, **kwargs), mutants[leg]))
    check("revert/source-leg", results["source"], (1, 0))
    check("revert/pins-leg", results["pins"], (1, 0))
    check("revert/guard-leg", results["guard"], (1, 0))
    check("revert/dynamic-leg", results["dynamic"], (1, 0))
    check("revert/completeness-leg", results["completeness"], (1, 0))
    check("revert/documentation-leg", results["documentation"], (1, 0))


def _expected_check_ids():
    try:
        manifest = tomllib.loads(_read_text(CHECKS_MANIFEST))
    except (CannotEvaluate, tomllib.TOMLDecodeError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in manifest.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and all(isinstance(item, str) for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(dict(format_version=1, suite=SUITE_ID, check_ids=EXECUTED), handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report {}: {}".format(
            report_path, exc), file=sys.stderr)
        return False
    return True


def self_test(report_path=None):
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-selftest-") as raw:
            _self_test_cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    if not _write_report(report_path):
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each leg red on its fixture and green with its "
          "check removed); execution set reconciled against tools/selftest_checks.toml".format(
              len(EXECUTED)))
    return 0


def main(argv):
    if not argv:
        code, lines = evaluate(ROOT)
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    rest = argv[1:] if argv[0] == "--self-test" else list(argv)
    report_path = None
    if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
        report_path, rest = rest[1], []
    if rest or (argv[0] != "--self-test" and report_path is None):
        print("usage: check_python_floor.py [--self-test] [--execution-report ABS_PATH] "
              "(the report path must be absolute)", file=sys.stderr)
        return 2
    return self_test(report_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
