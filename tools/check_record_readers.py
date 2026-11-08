#!/usr/bin/env python3
"""Record-reader inventory gate: every tool under tools/ that this static scan detects as reading this
project's records is on the consumer-switch list (.aiqt/opf-consumer-switch.toml) with a disposition, so a
later switch flips one listed entry and a detected reader nobody listed fails here. A read in a form the
scan cannot see (listed below) is not established by the list.

  check_record_readers.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_record_readers.py --self-test  assert every detection rule, every refusal and a pass over fixtures
  check_record_readers.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

Detection is static. Every .py and .sh file under tools/ is read (any directory named _vendor is skipped,
since vendored code reads no project record). opf/tools/ is not scanned: it is the store's own toolkit,
the place readers switch to, not a reader of the records being switched. The switch list's [detect] table declares what to
look for, and a file is a detected reader when any rule below matches:
  name    a Python string constant equal to a declared record file name, or ending in that name after "/",
          ":" or "}" (a joined path, a format template such as "{}:changelog.toml" for git show REV:PATH,
          or the literal tail of an f-string or a concatenation)
  key     a Python subscript (x["k"]) or .get("k", ...) read of a declared registry key, or a declared key
          as an element of a literal tuple, list or set that a for loop or comprehension iterates
  flag    a Python string constant equal to a declared command-line flag that takes a record path
  module  a Python import of a declared record-reading module (import m, from m import x,
          importlib.import_module("m") or __import__("m")), or a string constant equal to m.py or ending
          in "/m.py", "m.py" after ":" or "}" (the module loaded by file path): an indirect reader
  shell   a .sh line holding a declared record file name or flag as a whole word
The key vocabulary is checked against its authoritative source: every registry key the hook library
(.aiqt/core/hooks/scripts/aiqt_hooks.py) reads from the registry (reg, reg_data) or its record table (rec)
inside an argument of _orch_path(root, value), the helper through which it resolves each registry-declared
record path, directly or through a loop over a tuple of string constants. A key it reads that [detect] keys
lacks, a hook library that cannot be read or parsed, or one that yields no key, is cannot-evaluate.
What the scan cannot see: a name, key or module file name built at run time from parts ("changelog" +
".toml", "aiqt_hooks" + ".py"), a record name in the middle of a longer string that is not a path ending, a
key held in a named collection before a loop iterates it, a module loaded through a variable name, a path
given only on a command line whose flag is not declared, a .sh file that runs a record-reading module, paths
kept in the untracked orchestration registry, readers outside tools/, and a read that never names a file,
key, flag or module. The derivation of registry keys sees only reads whose receiver is named reg, reg_data
or rec inside an _orch_path argument; a record path the hook library resolves another way is not derived.
A self-test that writes record fixtures is detected like a reader; the
fixture-only disposition records it. A tool that imports a declared module for some other part of it is
detected too; the not-a-reader disposition records it, with the reason.

Each [[reader]] entry carries path, records (a non-empty list of names), disposition and reason. The
disposition is one of switch, stays-outside-store, fixture-only, not-a-reader, or indirect-via:<path>,
where <path> is another listed entry. Exit 1 (finding): a detected reader that is not listed; a listed file that is gone;
a listed file the scan no longer detects. Exit 2 (cannot evaluate): a missing, unreadable, non-UTF-8,
malformed, too deeply nested or schema-invalid switch list, a duplicate entry, an unknown disposition, an
indirect-via target that is not listed or an indirect-via cycle; a [detect] keys list that lacks a key the
hook library derives; a missing scan root, a symbolic link to a directory or a directory that cannot be
listed; a scanned source larger than MAX_BYTES or that is unreadable, not a regular file (a FIFO is
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
# The authoritative source of registry keys: the hook library resolves every registry-declared record path
# through its _orch_path(root, value) helper, so the keys it reads from the registry (reg, reg_data) or
# from the registry's record table (rec) inside an _orch_path argument are the keys that name a record.
# [detect] keys must hold each of them; a key the hook library gains and the list lacks is cannot-evaluate.
HOOK_LIBRARY = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
HOOK_PATH_RESOLVER = "_orch_path"
HOOK_REGISTRY_NAMES = frozenset({"reg", "reg_data", "rec"})
# A record name ending a string after one of these is a path built by formatting or joining: "{}:name"
# (a git show REV:PATH argument), "{}/name", f"{d}/name" or rev + ":name".
NAME_JOINERS = ("/", ":", "}")


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
    # An indirect-via chain must end at an entry that is not itself indirect: a cycle (A via B, B via A, or
    # longer) names no reader the switch would flip. Each step visits a new entry, so the walk is bounded.
    for path in readers:
        seen, current = [path], path
        while readers[current]["disposition"].startswith(VIA_PREFIX):
            current = readers[current]["disposition"][len(VIA_PREFIX):]
            if current in seen:
                raise CannotEvaluate("{}: indirect-via cycle {}".format(
                    SWITCH_LIST, " -> ".join(seen + [current])))
            seen.append(current)
    return detect, readers


def _parse_python(path, rel):
    """The parsed module at path, or CannotEvaluate naming rel when it cannot be read, decoded or parsed."""
    text = _read_regular(path, rel)
    try:
        text = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
    try:
        return ast.parse(text, filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: not parseable Python ({})".format(rel, exc)) from None
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to parse".format(rel)) from None


def _is_registry(node):
    """True for a registry name (reg, reg_data, rec) or an `or` whose first operand is one: (reg or {})."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        node = node.values[0]
    return isinstance(node, ast.Name) and node.id in HOOK_REGISTRY_NAMES


def _scope_walk(scope):
    """Every node of one function or module body, not descending into a nested function, so a loop
    variable is resolved only within the scope that binds it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _loop_strings(scope):
    """Each loop variable in scope bound by `for v in (<string constants>)`, mapped to those strings."""
    loops = {}
    for node in _scope_walk(scope):
        if (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))):
            values = [e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            loops.setdefault(node.target.id, set()).update(values)
    return loops


def hook_registry_keys(root):
    """The sorted registry keys the hook library resolves to a record path: each read reg.get(K), rec[K],
    (reg_data or {}).get(K) inside an argument of _orch_path(root, ...), where K is a string constant or
    a loop variable over a tuple of string constants (`for key in ("findings", ...)`). CannotEvaluate when
    the library cannot be read or parsed, or yields no key at all (a derivation that finds nothing would
    pass every list vacuously)."""
    tree = _parse_python(os.path.join(root, HOOK_LIBRARY), HOOK_LIBRARY)
    keys = set()
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        loops = _loop_strings(scope)
        for call in _scope_walk(scope):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == HOOK_PATH_RESOLVER and len(call.args) == 2):
                continue
            for node in ast.walk(call.args[1]):
                key = None
                if isinstance(node, ast.Subscript) and _is_registry(node.value):
                    key = node.slice
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "get" and _is_registry(node.func.value) and node.args):
                    key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
                elif isinstance(key, ast.Name):
                    keys.update(loops.get(key.id, ()))
    if not keys:
        raise CannotEvaluate("{}: no registry key is read inside a {}(root, ...) argument, so the [detect] "
                             "keys cannot be checked against it".format(HOOK_LIBRARY, HOOK_PATH_RESOLVER))
    return tuple(sorted(keys))


def _ends_in_name(value, names):
    return next((n for n in names if value == n or any(value.endswith(j + n) for j in NAME_JOINERS)), None)


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
        # A key iterated from a literal collection: for k in ("findings", "mode"): reg.get(k).
        if (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))):
            for element in node.iter.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str) and element.value in keys:
                    yield element.lineno, "registry key {!r} iterated".format(element.value)


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
              and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("import_module", "__import__")
              and node.args and isinstance(node.args[0], ast.Constant)):
            names = (node.args[0].value,)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A module loaded by file path (importlib.util.spec_from_file_location, runpy.run_path, a
            # SourceFileLoader): a string naming the module's own file, whole or as its last component.
            hit = _ends_in_name(node.value, tuple(m + ".py" for m in modules))
            if hit is not None:
                yield node.lineno, "names module file {!r}".format(hit)
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
    hits = set()
    if rel.endswith(".sh"):
        text = _read_regular(path, rel)
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
        for rule in SH_RULES.values():
            hits.update(rule(text, detect))
        return sorted(hits)
    tree = _parse_python(path, rel)
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
    try:
        lacking = [k for k in hook_registry_keys(root) if k not in detect["keys"]]
    except CannotEvaluate as exc:
        return [], [str(exc)], {}
    if lacking:
        return [], ["{}: [detect] keys lacks {}, which {} resolves to a record path through {}".format(
            SWITCH_LIST, ", ".join(repr(k) for k in lacking), HOOK_LIBRARY, HOOK_PATH_RESOLVER)], {}
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
    # The fixture hook library: the one registry key it resolves through _orch_path is in [detect] keys.
    HOOK_LIBRARY: 'def a(root, reg):\n    return _orch_path(root, reg.get("ledger_path"))\n',
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
            rc, crash = _guarded(run_gate, tmp)
        # An exception the gate fails to handle makes the row fail by its own name, never abort the run.
        return ("uncaught " + crash) if crash is not None else rc, out.getvalue() + err.getvalue()


def _guarded(fn, *args):
    """(fn(*args), None), or (None, the exception's class and text) when fn raises."""
    try:
        return fn(*args), None
    except Exception as exc:
        return None, "{}: {}".format(type(exc).__name__, exc)


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
             "not another listed entry"),
            # A path that starts under tools/ and ends in .py but climbs out through a .. component.
            ("refuse/malformed-list-path-dotdot", _switch_text() + _entry("tools/../tools/x.py", "switch"),
             "is not a .py or .sh path"),
            ("refuse/indirect-via-cycle", _switch_text((("tools/by_name.py", "switch"),
                                                        ("tools/by_key.py", "switch"),
                                                        ("tools/by_flag.py", "fixture-only"),
                                                        ("tools/by_module.py", "indirect-via:tools/by_shell.sh"),
                                                        ("tools/by_shell.sh", "indirect-via:tools/by_module.py"))),
             "indirect-via cycle tools/by_module.py -> tools/by_shell.sh -> tools/by_module.py")):
        rc, out = _gate(switch=text)
        check(name, rc == 2 and needle in out, (rc, out))

    # Each read form beyond a plain constant, subscript, .get or import, unlisted, exits 1 naming it.
    for case_id, body, evidence in (
            ("detect/unlisted-iterated-key-reader", 'def f(r):\n    for k in ("ledger_path",):\n        r.get(k)\n',
             "registry key 'ledger_path' iterated"),
            ("detect/unlisted-format-name-reader", 'def f(rev):\n    return "{}:ledger.toml".format(rev)\n',
             "name 'ledger.toml'"),
            ("detect/unlisted-import-module-reader", 'import importlib\nM = importlib.import_module("ledger_lib")\n',
             "imports 'ledger_lib'"),
            ("detect/unlisted-dunder-import-reader", 'M = __import__("ledger_lib")\n', "imports 'ledger_lib'"),
            ("detect/unlisted-path-loaded-module",
             'import importlib.util\nS = importlib.util.spec_from_file_location("l", "/x/ledger_lib.py")\n',
             "names module file 'ledger_lib.py'")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/new_form.py": body}))
        check(case_id, rc == 1 and "tools/new_form.py" in out and evidence in out, (rc, out))

    # The [detect] keys are checked against the registry keys the hook library resolves through _orch_path.
    for case_id, hook, needle in (
            ("refuse/hook-key-missing-from-detect",
             'def a(root, reg):\n    return _orch_path(root, reg.get("ledger_path")) or '
             '_orch_path(root, reg["ledger_extra"])\n', "[detect] keys lacks 'ledger_extra'"),
            ("refuse/hook-loop-key-missing-from-detect",
             'def a(root, rec):\n    for key in ("ledger_path", "ledger_loop"):\n        _orch_path(root, rec.get(key))\n',
             "[detect] keys lacks 'ledger_loop'"),
            ("refuse/hook-or-key-missing-from-detect",
             'def a(root, reg_data):\n    return _orch_path(root, (reg_data or {}).get("ledger_dir"))\n',
             "[detect] keys lacks 'ledger_dir'"),
            ("refuse/hook-library-no-keys", 'def a(root, reg):\n    return reg\n', "no registry key is read"),
            ("refuse/hook-library-unparseable", "def a(:\n", HOOK_LIBRARY + ": not parseable Python")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))
    # Reads that are not registry record keys derive nothing: a non-registry receiver inside _orch_path, and a
    # loop variable bound in another function.
    hook = ('def a(root, reg, lease, rec):\n    return _orch_path(root, reg.get("ledger_path")) or '
            '_orch_path(root, lease.get("ledger_other")) or _orch_path(root, rec.get(key))\n'
            'def b():\n    for key in ("ledger_far",):\n        pass\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/hook-unrelated-reads", rc == 0, (rc, out))

    def _no_hook(tmp):
        os.unlink(os.path.join(tmp, HOOK_LIBRARY))
    rc, out = _gate(prepare=_no_hook)
    check("refuse/hook-library-missing", rc == 2 and HOOK_LIBRARY + ": missing" in out, (rc, out))

    def _linked_dir(tmp):
        os.symlink(os.path.join("..", "opf", "tools"), os.path.join(tmp, "tools", "linked"))
    rc, out = _gate(prepare=_linked_dir)
    check("refuse/symlinked-directory-cannot-evaluate",
          rc == 2 and "tools/linked: a symbolic link to a directory" in out, (rc, out))

    import shutil
    import unittest.mock

    def _locked_dir(tmp):
        os.makedirs(os.path.join(tmp, "tools", "locked"))
        os.chmod(os.path.join(tmp, "tools", "locked"), 0)
    if os.geteuid() == 0:
        real_scandir = os.scandir

        def _refuse_locked(path="."):
            if str(path).endswith(os.sep + "locked"):
                raise PermissionError(13, "listing refused by the self-test", str(path))
            return real_scandir(path)
        with unittest.mock.patch.object(os, "scandir", _refuse_locked):
            rc, out = _gate(prepare=_locked_dir)
    else:
        rc, out = _gate(prepare=_locked_dir)
    check("refuse/unlistable-directory-cannot-evaluate", rc == 2 and "cannot list a scanned directory" in out,
          (rc, out))

    def _no_scan_root(tmp):
        shutil.rmtree(os.path.join(tmp, "tools"))
    rc, out = _gate(prepare=_no_scan_root)
    check("refuse/missing-scan-root-cannot-evaluate", rc == 2 and "tools: scan root missing" in out, (rc, out))

    # MAX_BYTES, at each side of the bound: one byte over is refused; exactly at it and one under are read.
    for case_id, size, want in (("refuse/source-over-max-bytes", MAX_BYTES + 1, 2),
                                ("pass/source-at-max-bytes", MAX_BYTES, 0),
                                ("pass/source-under-max-bytes", MAX_BYTES - 1, 0)):
        rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/big.py": b"#" + b"x" * (size - 2) + b"\n"}))
        over = "tools/big.py: larger than {} bytes".format(MAX_BYTES) in out
        check(case_id, rc == want and over == (want == 2), (rc, size, out[-300:]))

    # Shell words are whole: a declared name with a word character before it, or a flag with one after it,
    # is not a match.
    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/near.sh": "#!/bin/bash\ncat myledger.toml\nrun --ledgers\n"}))
    check("ignore/shell-word-boundaries", rc == 0 and "near.sh" not in out, (rc, out))

    # The TOML parse site handles each failure tomllib can raise, by class; a handler that is missing fails
    # the row by name, never by an uncaught exception.
    for exc_class, case_id in ((RecursionError, "parse-site/handles-recursionerror"),
                               (MemoryError, "parse-site/handles-memoryerror"),
                               (ValueError, "parse-site/handles-valueerror")):
        with unittest.mock.patch.object(tomllib, "loads", side_effect=exc_class("raised by the self-test")):
            result, crash = _guarded(_parse_switch_text, "x = 1\n")
        data, error = result if result is not None else (None, None)
        check(case_id, crash is None and data is None and bool(error), (result, crash))

    got = [_guarded(_main_exit, argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                                   ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [(2, None)] * 3, got)

    rc, crash = _guarded(run_gate, ROOT)
    check("live/real-tree-passes", crash is None and rc == 0, (rc, crash))

    if FAILURES:
        print("SELF-TEST FAIL: check_record_readers {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_record_readers {} checks (a clean fixture passes; an unlisted reader found by "
          "each of the name, key, flag, module and shell rules exits 1, and passes with that rule removed; an "
          "iterated key, a formatted name, import_module, __import__ and a module file path are each detected; "
          "a listed file that is gone or no longer reads exits 1; _vendor, near-miss strings and near-miss "
          "shell words are ignored; a FIFO, unreadable, symlinked, oversized, non-UTF-8 or unparseable source, "
          "a symlinked or unlistable directory, a missing scan root, and a missing, malformed, too deeply "
          "nested, duplicated, unknown-disposition or indirect-via-cycle list exit 2; [detect] keys lacking a "
          "key the hook library resolves, or a missing, unparseable or keyless hook library, exits 2; the "
          "parse site handles RecursionError, MemoryError and ValueError; the live tree passes)".format(
              len(EXECUTED)))
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
