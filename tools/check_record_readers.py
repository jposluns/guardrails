#!/usr/bin/env python3
"""Record-reader inventory gate: every tool under tools/ that reads this project's records is on the
consumer-switch list (.aiqt/opf-consumer-switch.toml) with a disposition, so a later switch flips one listed
entry and a reader nobody listed fails here.

  check_record_readers.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_record_readers.py --self-test  assert every detection rule, every refusal and a pass over fixtures
  check_record_readers.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

Detection is static. Every .py and .sh file under tools/ is read (any directory named _vendor is skipped,
since vendored code reads no project record). opf/tools/ is not scanned: it is the store's own toolkit,
the place readers switch to, not a reader of the records being switched. The switch list's [detect] table declares what to
look for, and a file is a detected reader when any rule below matches:
  name    a Python string constant equal to a declared record file name, or ending in "/" and that name
  key     a Python subscript (x["k"]) or .get("k", ...) read of a declared registry key
  flag    a Python string constant equal to a declared command-line flag that takes a record path
  module  a Python import of a declared record-reading module (import m, from m import x, or
          importlib.import_module("m")): an indirect reader
  shell   a .sh line holding a declared record file name or flag as a whole word
What the scan cannot see: a path built at run time from parts, a path given only on a command line whose
flag is not declared, paths kept in the untracked orchestration registry, readers outside tools/
(the hook library under .aiqt/core is seen only through the module rule), and a read that never names a
file, key, flag or module. A self-test that writes record fixtures is detected like a reader; the
fixture-only disposition records it. A tool that imports a declared module for some other part of it is
detected too; the not-a-reader disposition records it, with the reason.

Each [[reader]] entry carries path, records (a non-empty list of names), disposition and reason. The
disposition is one of switch, stays-outside-store, fixture-only, not-a-reader, or indirect-via:<path>,
where <path> is another listed entry. Exit 1 (finding): a detected reader that is not listed; a listed file that is gone;
a listed file the scan no longer detects. Exit 2 (cannot evaluate): a missing, unreadable, non-UTF-8,
malformed, too deeply nested or schema-invalid switch list, a duplicate entry, an unknown disposition or an
indirect-via target that is not listed; a scanned source that is unreadable, not a regular file (a FIFO is
refused by the opened descriptor's type, never a blocked read), a symbolic link, not UTF-8 or not parseable
Python. A run with any cannot-evaluate cause exits 2 even when it also has findings.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_record_readers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import re
import stat
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH_LIST = ".aiqt/opf-consumer-switch.toml"
SCAN_ROOTS = ("tools",)
SKIP_DIRS = frozenset({"_vendor"})
SUFFIXES = (".py", ".sh")
MAX_BYTES = 8 * 1024 * 1024
FIXED_DISPOSITIONS = frozenset({"switch", "stays-outside-store", "fixture-only", "not-a-reader"})
VIA_PREFIX = "indirect-via:"
DETECT_FIELDS = ("names", "keys", "flags", "modules")
READER_FIELDS = frozenset({"path", "records", "disposition", "reason"})
TOP_FIELDS = frozenset({"format-version", "detect", "reader"})


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, not a regular file, not UTF-8, unparseable or malformed."""


def _read_regular(path, what):
    """The bytes of path, opened non-blocking without following a final symbolic link, and refused unless
    the opened descriptor is a regular file of at most MAX_BYTES."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        raise CannotEvaluate("{}: missing".format(what)) from None
    except OSError as exc:
        if os.path.islink(path):
            raise CannotEvaluate("{}: a symbolic link, not a regular file".format(what)) from None
        raise CannotEvaluate("{}: cannot be opened ({})".format(what, exc)) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            kind = "a FIFO" if stat.S_ISFIFO(info.st_mode) else "not a regular file"
            raise CannotEvaluate("{}: {}, not a regular file".format(what, kind))
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise CannotEvaluate("{}: larger than {} bytes".format(what, MAX_BYTES))
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(what, exc)) from None
    finally:
        os.close(fd)


def _parse_switch_text(text):
    """(data, error): the parsed switch list, or None and the reason it cannot be parsed. Every failure
    tomllib can raise on hostile input is handled here, at the parse site."""
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError as exc:
        return None, "not valid TOML ({})".format(exc)
    except RecursionError:
        return None, "nested too deeply to parse"
    except MemoryError:
        return None, "out of memory while parsing"
    except ValueError as exc:
        return None, "not valid TOML ({})".format(exc)


def _string_list(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise CannotEvaluate("{} must be a non-empty list of non-empty strings".format(where))
    if len(set(value)) != len(value):
        raise CannotEvaluate("{} repeats an element".format(where))
    return tuple(value)


def _valid_rel(path):
    parts = path.split("/")
    return (not path.startswith("/") and "" not in parts and "." not in parts and ".." not in parts
            and any(path.startswith(r + "/") for r in SCAN_ROOTS) and path.endswith(SUFFIXES))


def load_switch_list(root):
    """(detect, readers): the validated switch list. detect maps each DETECT_FIELDS name to a tuple;
    readers maps a repository-relative path to its entry. CannotEvaluate on any defect."""
    raw = _read_regular(os.path.join(root, SWITCH_LIST), SWITCH_LIST)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(SWITCH_LIST, exc)) from None
    data, error = _parse_switch_text(text)
    if error is not None:
        raise CannotEvaluate("{}: {}".format(SWITCH_LIST, error))
    unknown = sorted(set(data) - TOP_FIELDS)
    if unknown:
        raise CannotEvaluate("{}: unknown top-level field(s) {}".format(SWITCH_LIST, ", ".join(unknown)))
    if type(data.get("format-version")) is not int or data.get("format-version") != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SWITCH_LIST))
    block = data.get("detect")
    if not isinstance(block, dict) or set(block) != set(DETECT_FIELDS):
        raise CannotEvaluate("{}: [detect] must hold exactly {}".format(SWITCH_LIST, ", ".join(DETECT_FIELDS)))
    detect = {field: _string_list(block[field], "[detect] " + field) for field in DETECT_FIELDS}
    rows = data.get("reader")
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("{}: needs at least one [[reader]] entry".format(SWITCH_LIST))
    readers = {}
    for number, row in enumerate(rows, 1):
        where = "{} [[reader]] #{}".format(SWITCH_LIST, number)
        if not isinstance(row, dict) or set(row) != READER_FIELDS:
            raise CannotEvaluate("{}: must hold exactly {}".format(where, ", ".join(sorted(READER_FIELDS))))
        path, disposition, reason = row["path"], row["disposition"], row["reason"]
        if not isinstance(path, str) or not _valid_rel(path):
            raise CannotEvaluate("{}: path {!r} is not a .py or .sh path under {}".format(
                where, path, " or ".join(SCAN_ROOTS)))
        if path in readers:
            raise CannotEvaluate("{}: duplicate entry for {}".format(where, path))
        _string_list(row["records"], where + " records")
        if not isinstance(reason, str) or not reason.strip():
            raise CannotEvaluate("{}: reason must be a non-empty string".format(where))
        if not isinstance(disposition, str) or not (
                disposition in FIXED_DISPOSITIONS
                or (disposition.startswith(VIA_PREFIX) and _valid_rel(disposition[len(VIA_PREFIX):]))):
            raise CannotEvaluate("{}: unknown disposition {!r}".format(where, disposition))
        readers[path] = row
    for path, row in readers.items():
        disposition = row["disposition"]
        if disposition.startswith(VIA_PREFIX):
            target = disposition[len(VIA_PREFIX):]
            if target == path or target not in readers:
                raise CannotEvaluate("{}: {} names {}, which is not another listed entry".format(
                    SWITCH_LIST, path, disposition))
    return detect, readers


def _ends_in_name(value, names):
    return next((n for n in names if value == n or value.endswith("/" + n)), None)


def _rule_name(tree, detect):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = _ends_in_name(node.value, detect["names"])
            if hit is not None:
                yield node.lineno, "name {!r}".format(hit)


def _rule_key(tree, detect):
    keys = detect["keys"]
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
              and node.args and isinstance(node.args[0], ast.Constant)):
            key = node.args[0].value
        if isinstance(key, str) and key in keys:
            yield node.lineno, "registry key {!r}".format(key)


def _rule_flag(tree, detect):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in detect["flags"]:
            yield node.lineno, "flag {!r}".format(node.value)


def _rule_module(tree, detect):
    modules = detect["modules"]
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = (node.module,)
        elif (isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
              and getattr(node.func, "attr", getattr(node.func, "id", None)) == "import_module"
              and node.args and isinstance(node.args[0], ast.Constant)):
            names = (node.args[0].value,)
        for name in names:
            if isinstance(name, str) and name in modules:
                yield node.lineno, "imports {!r}".format(name)


def _rule_shell(text, detect):
    for number, line in enumerate(text.split("\n"), 1):
        for word in detect["names"] + detect["flags"]:
            if re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(word) + r"(?![A-Za-z0-9_-])", line):
                yield number, "shell word {!r}".format(word)


# Each detection rule, by name; the self-test removes each in turn to prove a vector depends on it.
PY_RULES = {"name": _rule_name, "key": _rule_key, "flag": _rule_flag, "module": _rule_module}
SH_RULES = {"shell": _rule_shell}


def detect_file(path, rel, detect):
    """The sorted (line, evidence) hits for one source file. CannotEvaluate when it cannot be judged."""
    text = _read_regular(path, rel)
    try:
        text = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
    hits = set()
    if rel.endswith(".sh"):
        for rule in SH_RULES.values():
            hits.update(rule(text, detect))
        return sorted(hits)
    try:
        tree = ast.parse(text, filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: not parseable Python ({})".format(rel, exc)) from None
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to parse".format(rel)) from None
    for rule in PY_RULES.values():
        hits.update(rule(tree, detect))
    return sorted(hits)


def walk_sources(root):
    """(sources, errors): every repository-relative .py and .sh path under the scan roots, outside _vendor;
    a symbolic link to a file or directory is an error, never followed and never skipped."""
    sources, errors = [], []
    for scan_root in SCAN_ROOTS:
        base = os.path.join(root, scan_root)
        if not os.path.isdir(base) or os.path.islink(base):
            errors.append("{}: scan root missing or not a plain directory".format(scan_root))
            continue

        def _walk_error(exc):
            errors.append("cannot list a scanned directory ({})".format(exc))
        for directory, dirnames, filenames in os.walk(base, onerror=_walk_error):
            rel_dir = os.path.relpath(directory, root).replace(os.sep, "/")
            for name in list(dirnames):
                full = os.path.join(directory, name)
                if name in SKIP_DIRS:
                    dirnames.remove(name)
                elif os.path.islink(full):
                    dirnames.remove(name)
                    errors.append("{}/{}: a symbolic link to a directory, not scanned".format(rel_dir, name))
            dirnames.sort()
            for name in sorted(filenames):
                if name.endswith(SUFFIXES):
                    sources.append("{}/{}".format(rel_dir, name))
    return sources, errors


def scan(root):
    """(findings, errors, detected): findings fail the gate, errors make it cannot-evaluate."""
    try:
        detect, readers = load_switch_list(root)
    except CannotEvaluate as exc:
        return [], [str(exc)], {}
    sources, errors = walk_sources(root)
    detected = {}
    for rel in sources:
        try:
            hits = detect_file(os.path.join(root, rel), rel, detect)
        except CannotEvaluate as exc:
            errors.append(str(exc))
            continue
        if hits:
            detected[rel] = hits
    findings = []
    for rel in sorted(detected):
        if rel not in readers:
            line, evidence = detected[rel][0]
            findings.append("{}: reads a project record ({} at line {}) but is not on the switch list".format(
                rel, evidence, line))
    present = set(sources)
    for rel in sorted(readers):
        if rel not in present:
            findings.append("{}: listed on the switch list but the file is gone".format(rel))
        elif rel not in detected and not any(e.startswith(rel + ":") for e in errors):
            findings.append("{}: listed on the switch list but no longer detected as a reader".format(rel))
    return findings, errors, detected


def run_gate(root):
    findings, errors, detected = scan(root)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: {} record reader(s) under {} are each on {} with a disposition".format(
        len(detected), " and ".join(SCAN_ROOTS), SWITCH_LIST))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record the
# instrumented check() helper appends to; the execution gate reconciles the report against the registry.
SUITE_ID = "record-readers-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


# Fixture vocabulary: deliberately not this project's record names, so the gate's own source never reads
# as a reader of them.
_FIXTURE_DETECT = (
    "[detect]\n"
    'names = ["ledger.toml"]\n'
    'keys = ["ledger_path"]\n'
    'flags = ["--ledger"]\n'
    'modules = ["ledger_lib"]\n')
_FIXTURE_FILES = {
    "tools/by_name.py": 'P = "data/ledger.toml"\n',
    "tools/by_key.py": 'def f(reg):\n    return reg.get("ledger_path")\n',
    "tools/by_flag.py": 'import sys\nA = sys.argv.index("--ledger")\n',
    "tools/by_module.py": "import ledger_lib\n",
    "tools/by_shell.sh": "#!/bin/bash\ncat ledger.toml\n",
    "tools/plain.py": 'X = "ledger.toml.bak"\nY = "myledger.toml"\n',
    "tools/_vendor/ignored.py": 'P = "ledger.toml"\n',
}
_FIXTURE_ENTRIES = (
    ("tools/by_name.py", "switch"),
    ("tools/by_key.py", "stays-outside-store"),
    ("tools/by_flag.py", "fixture-only"),
    ("tools/by_module.py", "indirect-via:tools/by_name.py"),
    ("tools/by_shell.sh", "switch"),
)
# The clean fixture's reader that only each detection rule finds.
_RULE_FIXTURE = {"name": "tools/by_name.py", "key": "tools/by_key.py", "flag": "tools/by_flag.py",
                 "module": "tools/by_module.py", "shell": "tools/by_shell.sh"}


def _entry(path, disposition):
    return ('[[reader]]\npath = "{}"\nrecords = ["ledger"]\ndisposition = "{}"\n'
            'reason = "a fixture reader"\n'.format(path, disposition))


def _switch_text(entries=_FIXTURE_ENTRIES, detect=_FIXTURE_DETECT):
    return "format-version = 1\n" + detect + "".join(_entry(p, d) for p, d in entries)


def _fixture(tmp, files=None, switch=None):
    files = dict(_FIXTURE_FILES if files is None else files)
    for rel, body in files.items():
        full = Path(tmp) / rel
        os.makedirs(full.parent, exist_ok=True)
        full.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    os.makedirs(Path(tmp) / "tools", exist_ok=True)
    os.makedirs(Path(tmp) / "opf" / "tools", exist_ok=True)
    os.makedirs(Path(tmp) / ".aiqt", exist_ok=True)
    if switch is not None:
        (Path(tmp) / SWITCH_LIST).write_bytes(switch if isinstance(switch, bytes) else switch.encode("utf-8"))
    return tmp


def _gate(files=None, switch=None, prepare=None):
    import contextlib
    import io
    import tempfile
    with tempfile.TemporaryDirectory(prefix="record-readers-selftest-") as tmp:
        _fixture(tmp, files, _switch_text() if switch is None else switch)
        if prepare is not None:
            prepare(tmp)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_gate(tmp)
        return rc, out.getvalue() + err.getvalue()


def _without_rule(table, name, files, switch=None):
    """The gate's exit on a fixture with one detection rule removed."""
    saved = table.pop(name)
    try:
        return _gate(files, switch)
    finally:
        table[name] = saved


def _self_test():
    rc, out = _gate()
    check("pass/clean-fixture", rc == 0 and "PASS: 5 record reader" in out, (rc, out))

    # One unlisted reader per detection rule exits 1, naming the file and the rule that found it.
    for rule, rel, body, evidence, detect_id, nonvacuity_id in (
            ("name", "tools/new_name.py", 'P = "ledger.toml"\n', "name 'ledger.toml'",
             "detect/unlisted-name-reader", "nonvacuity/name-rule-removed-passes"),
            ("key", "tools/new_key.py", 'def f(r):\n    return r["ledger_path"]\n', "registry key 'ledger_path'",
             "detect/unlisted-key-reader", "nonvacuity/key-rule-removed-passes"),
            ("flag", "tools/new_flag.py", 'F = "--ledger"\n', "flag '--ledger'",
             "detect/unlisted-flag-reader", "nonvacuity/flag-rule-removed-passes"),
            ("module", "tools/new_module.py", "from ledger_lib import read\n", "imports 'ledger_lib'",
             "detect/unlisted-module-reader", "nonvacuity/module-rule-removed-passes"),
            ("shell", "tools/new_shell.sh", "#!/bin/bash\npython3 x.py --ledger \"$1\"\n",
             "shell word '--ledger'", "detect/unlisted-shell-reader", "nonvacuity/shell-rule-removed-passes")):
        files = dict(_FIXTURE_FILES, **{rel: body})
        rc, out = _gate(files)
        check(detect_id, rc == 1 and rel in out and evidence in out and "not on the switch list" in out,
              (rc, out))
        # Non-vacuity: with this rule removed the same unlisted reader passes unseen, so the vector above
        # depends on the rule. The clean fixture's own reader for this rule is dropped from the files and
        # the list (it too would go unseen); by_module's indirect-via target is by_name, so when by_name is
        # dropped by_module is listed as a plain switch entry instead.
        own = _RULE_FIXTURE[rule]
        files = {k: v for k, v in _FIXTURE_FILES.items() if k != own}
        files[rel] = body
        entries = tuple((p, "switch" if p == "tools/by_module.py" and own == "tools/by_name.py" else d)
                        for p, d in _FIXTURE_ENTRIES if p != own)
        table = SH_RULES if rule == "shell" else PY_RULES
        rc, out = _without_rule(table, rule, files, _switch_text(entries))
        check(nonvacuity_id, rc == 0, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"opf/tools/store_reader.py": 'P = "ledger.toml"\n'}))
    check("ignore/outside-scan-root", rc == 0 and "store_reader" not in out, (rc, out))

    files = dict(_FIXTURE_FILES)
    del files["tools/by_key.py"]
    rc, out = _gate(files)
    check("refuse/listed-file-gone", rc == 1 and "tools/by_key.py: listed on the switch list but the file is gone"
          in out, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/by_key.py": "def f(reg):\n    return reg\n"}))
    check("refuse/listed-file-no-longer-reads", rc == 1 and "tools/by_key.py: listed on the switch list but no "
          "longer detected" in out, (rc, out))

    check("ignore/vendor-dir", "_vendor" not in _gate()[1] and _gate()[0] == 0, _gate())
    check("ignore/near-miss-strings", "plain.py" not in _gate()[1], _gate())

    def _fifo(tmp):
        os.mkfifo(os.path.join(tmp, "tools", "pipe.py"))
    rc, out = _gate(prepare=_fifo)
    check("refuse/fifo-source-cannot-evaluate", rc == 2 and "tools/pipe.py: a FIFO" in out, (rc, out))

    def _unreadable(tmp):
        os.chmod(os.path.join(tmp, "tools", "by_name.py"), 0)
    if os.geteuid() == 0:
        original = _read_regular

        def _deny(path, what):
            if what == "tools/by_name.py":
                raise CannotEvaluate("{}: cannot be opened (read refused by the self-test)".format(what))
            return original(path, what)
        globals()["_read_regular"] = _deny
        try:
            rc, out = _gate()
        finally:
            globals()["_read_regular"] = original
    else:
        rc, out = _gate(prepare=_unreadable)
    check("refuse/unreadable-source-cannot-evaluate", rc == 2 and "tools/by_name.py: cannot be opened" in out,
          (rc, out))

    def _symlink(tmp):
        os.symlink("by_name.py", os.path.join(tmp, "tools", "alias.py"))
    rc, out = _gate(prepare=_symlink)
    check("refuse/symlinked-source-cannot-evaluate", rc == 2 and "tools/alias.py: a symbolic link" in out,
          (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/latin.py": b'X = "caf\xe9"\n'}))
    check("refuse/non-utf8-source-cannot-evaluate", rc == 2 and "tools/latin.py: not UTF-8" in out, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/broken.py": "def f(:\n"}))
    check("refuse/syntax-error-cannot-evaluate", rc == 2 and "tools/broken.py: not parseable Python" in out,
          (rc, out))

    # A listed source that cannot be read is cannot-evaluate, never also reported as no longer reading.
    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/by_key.py": "def f(:\n"}))
    check("refuse/listed-unparseable-not-stale", rc == 2 and "no longer detected" not in out, (rc, out))

    def _no_list(tmp):
        os.unlink(os.path.join(tmp, SWITCH_LIST))
    rc, out = _gate(prepare=_no_list)
    check("refuse/missing-list-cannot-evaluate", rc == 2 and SWITCH_LIST + ": missing" in out, (rc, out))

    for name, text, needle in (
            ("refuse/malformed-list-not-toml", "format-version = 1\n[detect\n", "not valid TOML"),
            ("refuse/malformed-list-not-utf8", b"format-version = 1\n# \xff\n", "not UTF-8"),
            ("refuse/malformed-list-bad-version", _switch_text().replace("format-version = 1",
                                                                         "format-version = true"),
             "format-version"),
            ("refuse/malformed-list-unknown-field", "extra = 1\n" + _switch_text(), "unknown top-level"),
            ("refuse/malformed-list-entry-field", _switch_text().replace('reason = "a fixture reader"',
                                                                         'why = "x"', 1), "must hold exactly"),
            ("refuse/malformed-list-empty-records", _switch_text().replace('records = ["ledger"]',
                                                                           "records = []", 1), "records"),
            ("refuse/malformed-list-detect-missing", _switch_text(detect=_FIXTURE_DETECT.replace(
                'modules = ["ledger_lib"]\n', "")), "[detect] must hold exactly"),
            ("refuse/malformed-list-path-outside", _switch_text() + _entry("../tools/x.py", "switch"),
             "is not a .py or .sh path"),
            ("refuse/deep-list-recursion", "format-version = 1\nx = " + "[" * 100000 + "]" * 100000 + "\n",
             "nested too deeply"),
            ("refuse/duplicate-entry", _switch_text() + _entry("tools/by_name.py", "switch"),
             "duplicate entry for tools/by_name.py"),
            ("refuse/unknown-disposition", _switch_text((("tools/by_name.py", "switch"),
                                                         ("tools/by_key.py", "moved"),
                                                         ("tools/by_flag.py", "fixture-only"),
                                                         ("tools/by_module.py", "switch"),
                                                         ("tools/by_shell.sh", "switch"))),
             "unknown disposition 'moved'"),
            ("refuse/indirect-via-unlisted", _switch_text((("tools/by_name.py", "switch"),
                                                           ("tools/by_key.py", "switch"),
                                                           ("tools/by_flag.py", "fixture-only"),
                                                           ("tools/by_module.py", "indirect-via:tools/x.py"),
                                                           ("tools/by_shell.sh", "switch"))),
             "not another listed entry")):
        rc, out = _gate(switch=text)
        check(name, rc == 2 and needle in out, (rc, out))

    # The TOML parse site handles each failure tomllib can raise, by class.
    import unittest.mock
    for exc_class, case_id in ((RecursionError, "parse-site/handles-recursionerror"),
                               (MemoryError, "parse-site/handles-memoryerror"),
                               (ValueError, "parse-site/handles-valueerror")):
        with unittest.mock.patch.object(tomllib, "loads", side_effect=exc_class("raised by the self-test")):
            data, error = _parse_switch_text("x = 1\n")
        check(case_id, data is None and bool(error), (data, error))

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    rc = run_gate(ROOT)
    check("live/real-tree-passes", rc == 0, rc)

    if FAILURES:
        print("SELF-TEST FAIL: check_record_readers {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_record_readers {} checks (a clean fixture passes; an unlisted reader found by "
          "each of the name, key, flag, module and shell rules exits 1, and passes with that rule removed; a "
          "listed file that is gone or no longer reads exits 1; _vendor and near-miss strings are ignored; a "
          "FIFO, unreadable, symlinked, non-UTF-8 or unparseable source and a missing, malformed, too deeply "
          "nested, duplicated or unknown-disposition list exit 2; the parse site handles RecursionError, "
          "MemoryError and ValueError; the live tree passes)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in that
    # trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
