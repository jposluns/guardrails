#!/usr/bin/env python3
"""Fixture-pin gate (fixture-pins): every file a declared pin table names is tracked, committed in HEAD as a
regular blob whose sha256 is the pin, and present in the working tree as a regular, non-symlink file whose
sha256 is the pin.

A self-test that reads a committed fixture file trusts that file's bytes. A pin table (a module-level dict
literal mapping a repo-relative path to its sha256) records which bytes the test was written against, and
this gate holds the repository to it. Tables are found by DECLARATION, never by a naming convention: the
hand-written manifest tools/fixture-pins.toml lists each table as a (source, name) row, so a table that is
renamed or deleted is a missing table (exit 2), never one that silently drops out of a scan.

The manifest carries exactly format-version = 1 and [[table]] rows of exactly source (a repo-relative .py
path) and name (an identifier). It must be a tracked regular non-symlink UTF-8 TOML file with at least one
row and no repeated (source, name) pair. Each source must be tracked and committed in HEAD as a regular blob,
and is read from the working tree as a regular non-symlink file and AST-parsed, never imported. The name must
be bound exactly once in the module's own scope, by a top-level assignment whose value is a dict literal. The
binding count is a static walk of every node evaluated in that scope (function, lambda and class bodies are
not entered; their decorators, argument defaults, bases and class keywords are): every non-load ast.Name
(assignment, augmented, annotated, for, with-as, walrus, del, type alias), a def or class of the name, an
import or from-import alias (a star import counts), a global declaration, and every binding held in a string
field (NAME_FIELDS: an except or except* handler's name, a match capture, star or mapping rest). Any store,
augmented store or delete on a subscript or attribute of the name in that scope is refused. Every key and
value must be a string literal (a ** unpacking, an f-string, a computed path or a name is a non-literal
entry), keys are checked for duplicates on the AST before any dict is built, and an empty table is refused.
A key is a repo-relative POSIX path of the closed character set PIN_PATH_RE with no '.' or '..' component;
a value is 64 lowercase hex digits.

Each pin is then checked, every finding reported rather than the first:
  tracked     the path is in the index (git ls-files -z, read once);
  committed   HEAD holds it as a regular blob (git ls-tree -r -z --full-tree HEAD, read once; mode 100644 or
              100755) whose bytes (git cat-file blob) hash to the pin;
  working     lstat of the path is not a symbolic link, its real path is the repository's real path joined
              with it (no ancestor symlink), and the file opened O_NOFOLLOW|O_NONBLOCK is regular and hashes to
              the pin.
The same path pinned to two different digests by two tables is a finding. git is resolved once on PATH and
run with the caller's environment minus every GIT_-prefixed variable (_git_fixture_env.caller_env_without_git),
and every git read carries --no-replace-objects, so a local refs/replace/* entry cannot make another blob or
an older commit read as HEAD's.

DISCLOSED RESIDUAL: a pin table nobody declared in the manifest is out of scope, and so is a fixture a
self-test reads without a pin; the manifest itself is hand-written, so removing a row passes this gate and
only review can judge it. A rebinding or mutation of a table made inside a function body, by a call
(setattr, globals().update, or a method such as PINNED.update or PINNED.pop), through an alias of the table,
or from another module is not seen. A table source that is tracked and in HEAD is still read from the
working tree, as the manifest is, so an uncommitted edit to a committed source decides the result. The
legacy .git/info/grafts file is not disabled (--no-replace-objects does not cover it). Four self-test flips
(MESSAGE_ONLY_FLIPS) keep the vector's exit code and change only its report, because another leg still fails
the vector closed; the tracked, lstat and git-exit legs each have a second vector whose flip changes the exit
code, but a git that cannot launch fails every read, so its flip is shown by the report alone. A symlink
swapped in between the lstat and the open of the final component is refused by O_NOFOLLOW, but an ancestor
directory swapped between the real-path check and the open is not detected. A matching digest shows the
bytes are the pinned ones, not that the fixture is adequate, and a pass is about fixture integrity, not about
the health of any self-test. A path outside PIN_PATH_RE (a space or any non-ASCII character) cannot be
pinned.

  check_fixture_pins.py                                check the live tree
  check_fixture_pins.py --self-test                    run the vectors in throwaway repositories
  check_fixture_pins.py [--self-test] --execution-report ABS_PATH   (the execution-set gate's launch)

Exit convention: 0 every declared table produced at least one pin and every pin verified; 1 a pin finding
(untracked, not in HEAD, committed as a symlink or non-regular entry, HEAD blob mismatch, missing, symbolic
link, ancestor symlink, non-regular, working-tree mismatch, conflicting pins); 2 cannot evaluate (a missing,
unreadable, malformed or table-less manifest; a source not tracked, not committed in HEAD, unreadable or
unparseable; a table not found, bound twice, altered by a subscript or attribute store, not a dict literal,
empty, non-literal, holding a duplicate key, or holding a malformed path or digest; git unavailable, a git
error or timeout; a read error other than a missing file; an unknown argument). 2 takes precedence over 1.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_fixture_pins.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_REL = "tools/fixture-pins.toml"
MANIFEST_KEYS = {"format-version", "table"}
TABLE_KEYS = {"source", "name"}
PIN_PATH_RE = re.compile(r"[A-Za-z0-9_.+@-]+(?:/[A-Za-z0-9_.+@-]+)*")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REGULAR_MODES = ("100644", "100755")
GIT_TIMEOUT = 120
CHUNK = 1 << 16


class CannotEvaluate(Exception):
    """An input the gate cannot read or judge: exit 2."""


def _git(git, root, env, *args):
    """stdout bytes of one git read in root; any launch failure, timeout or nonzero exit is CannotEvaluate."""
    try:
        result = subprocess.run([git, "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-C", str(root), "--no-replace-objects", *args], capture_output=True, env=env,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git {} could not run ({})".format(args[0], type(exc).__name__))
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CannotEvaluate("git {} exited {}: {}".format(args[0], result.returncode,
                                                           detail[0] if detail else "no message"))
    return result.stdout


def _index_paths(root, git, env):
    return {raw.decode("utf-8", "surrogateescape") for raw in _git(git, root, env, "ls-files", "-z").split(b"\0")
            if raw}


def _head_entries(root, git, env):
    """{path: (mode, type, oid)} for every non-tree entry of HEAD."""
    entries = {}
    for record in _git(git, root, env, "ls-tree", "-r", "-z", "--full-tree", "HEAD").split(b"\0"):
        if not record:
            continue
        meta, sep, raw_path = record.partition(b"\t")
        parts = meta.decode("ascii", "replace").split(" ")
        if not sep or len(parts) != 3 or not OID_RE.fullmatch(parts[2]):
            raise CannotEvaluate("git ls-tree: unexpected record {!r}".format(record[:80]))
        entries[raw_path.decode("utf-8", "surrogateescape")] = tuple(parts)
    return entries


def _valid_rel(rel):
    return (isinstance(rel, str) and PIN_PATH_RE.fullmatch(rel) is not None
            and all(part not in (".", "..") for part in rel.split("/")))


def _read_regular(path, label):
    """The bytes of path, which must be a regular non-symlink file; anything else is CannotEvaluate."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(label, type(exc).__name__))
    if stat.S_ISLNK(info.st_mode):
        raise CannotEvaluate("{}: is a symbolic link".format(label))
    if not stat.S_ISREG(info.st_mode):
        raise CannotEvaluate("{}: is not a regular file".format(label))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot open ({})".format(label, type(exc).__name__))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CannotEvaluate("{}: is not a regular file".format(label))
        chunks = []
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(label, type(exc).__name__))
    finally:
        os.close(fd)


def _manifest_rows(root, index):
    """[(source, name)] from the manifest, fail-closed."""
    if MANIFEST_REL not in index:
        raise CannotEvaluate("{}: not tracked".format(MANIFEST_REL))
    raw = _read_regular(os.path.join(root, MANIFEST_REL), MANIFEST_REL)
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    # A ValueError covers invalid UTF-8, invalid TOML and an over-long integer literal. tomllib raises
    # RecursionError (a RuntimeError, not a ValueError) on a deeply nested array or inline table and, from
    # CPython 3.14.8, on a key of more than 1000 dotted parts, and MemoryError on a many-part key under a
    # memory limit; each is a manifest the gate cannot evaluate, never a traceback.
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: malformed ({})".format(MANIFEST_REL, type(exc).__name__))
    unknown = sorted(set(data) - MANIFEST_KEYS)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(MANIFEST_REL, ", ".join(unknown)))
    if type(data.get("format-version")) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be 1".format(MANIFEST_REL))
    tables = data.get("table", [])
    if not isinstance(tables, list):
        raise CannotEvaluate("{}: table must be an array of tables".format(MANIFEST_REL))
    if not tables:
        raise CannotEvaluate("{}: declares no tables, so there is nothing it could verify".format(MANIFEST_REL))
    rows = []
    for number, table in enumerate(tables, 1):
        where = "{}: table row {}".format(MANIFEST_REL, number)
        if not isinstance(table, dict) or set(table) != TABLE_KEYS:
            raise CannotEvaluate("{}: must carry exactly source and name".format(where))
        source, name = table["source"], table["name"]
        if not _valid_rel(source) or not source.endswith(".py"):
            raise CannotEvaluate("{}: source is not a repo-relative .py path".format(where))
        if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
            raise CannotEvaluate("{}: name is not an identifier".format(where))
        if (source, name) in rows:
            raise CannotEvaluate("{}: repeats {}:{}".format(where, source, name))
        rows.append((source, name))
    return rows


# The binding forms whose target is a plain string field rather than an ast.Name: an except or except* handler's
# "as" name, a match capture (case NAME, case [*NAME], case {**NAME}).
NAME_FIELDS = ((ast.ExceptHandler, "name"), (ast.MatchAs, "name"), (ast.MatchStar, "name"),
               (ast.MatchMapping, "rest"))


def _module_scope_nodes(tree):
    """Every node evaluated in the module's own scope. A function, lambda or class body is not entered, but its
    decorators, argument defaults, bases and class keywords are, since they run in the enclosing scope."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(getattr(node, "decorator_list", []))
            stack.extend(default for default in node.args.defaults + node.args.kw_defaults if default is not None)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.decorator_list + node.bases + node.keywords)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _module_bindings(tree, name):
    """Every node that binds name in the module's own scope."""
    found = []
    for node in _module_scope_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            found.append(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.extend(node for alias in node.names
                         if alias.name == "*" or (alias.asname or alias.name.split(".")[0]) == name)
        if isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            found.append(node)
        if any(isinstance(node, kind) and getattr(node, field) == name for kind, field in NAME_FIELDS):
            found.append(node)
        if isinstance(node, ast.Global) and name in node.names:
            found.append(node)
    return found


def _module_stores(tree, name):
    """Every store, augmented store or delete in the module's own scope on a subscript or attribute of name
    (name["k"] = ..., name["k"] += ..., name.attr = ..., del name["k"], and the same through a longer chain)."""
    found = []
    for node in _module_scope_nodes(tree):
        if isinstance(node, (ast.Subscript, ast.Attribute)) and not isinstance(node.ctx, ast.Load):
            base = node.value
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name) and base.id == name:
                found.append(node)
    return found


def _literal_dict(tree, name):
    """The ast.Dict value of the one top-level assignment of name, or None."""
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) \
                and stmt.targets[0].id == name:
            return stmt.value if isinstance(stmt.value, ast.Dict) else None
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name:
            return stmt.value if isinstance(stmt.value, ast.Dict) else None
    return None


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _table_pins(root, source, name, index, head):
    """[(path, digest)] of one declared table, read from its source's AST; any defect is CannotEvaluate."""
    where = "{}:{}".format(source, name)
    if source not in index:
        raise CannotEvaluate("{}: source not tracked".format(where))
    entry = head.get(source)
    if entry is None or entry[0] not in REGULAR_MODES or entry[1] != "blob":
        raise CannotEvaluate("{}: source not committed in HEAD as a regular file".format(where))
    real_root = os.path.realpath(root)
    path = os.path.join(root, source)
    if os.path.realpath(path) != os.path.join(real_root, source):
        raise CannotEvaluate("{}: source path traverses a symbolic link".format(where))
    raw = _read_regular(path, source)
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=source)
    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({})".format(source, type(exc).__name__))
    bindings = _module_bindings(tree, name)
    if not bindings:
        raise CannotEvaluate("{}: table not found".format(where))
    if len(bindings) > 1:
        raise CannotEvaluate("{}: table bound {} times in the module scope".format(where, len(bindings)))
    stores = _module_stores(tree, name)
    if stores:
        raise CannotEvaluate("{}: table altered by a subscript or attribute store at module scope, line {}".format(
            where, stores[0].lineno))
    table = _literal_dict(tree, name)
    if table is None:
        raise CannotEvaluate("{}: table is not a dict literal assigned at the top level".format(where))
    keys, pins = [], []
    for key, value in zip(table.keys, table.values):
        if not (key is not None and _is_str(key) and _is_str(value)):
            raise CannotEvaluate("{}: non-literal entry at line {}".format(where, value.lineno))
        keys.append(key.value)
        pins.append((key.value, value.value))
    if len(set(keys)) != len(keys):
        raise CannotEvaluate("{}: duplicate key {!r}".format(where, sorted(k for k in keys if keys.count(k) > 1)[0]))
    if not keys:
        raise CannotEvaluate("{}: table empty".format(where))
    for rel, digest in pins:
        if not _valid_rel(rel):
            raise CannotEvaluate("{}: {!r} is not a repo-relative path of the pinnable character set".format(where, rel))
        if DIGEST_RE.fullmatch(digest) is None:
            raise CannotEvaluate("{}: {}: the pin is not 64 lowercase hex digits".format(where, rel))
    return pins


def _blob_digest(root, git, env, oid):
    return hashlib.sha256(_git(git, root, env, "cat-file", "blob", oid)).hexdigest()


def _worktree_digest(path, rel):
    """The sha256 of the regular file at path, or None when what O_NOFOLLOW opens is not a regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot open ({})".format(rel, type(exc).__name__))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(rel, type(exc).__name__))
    finally:
        os.close(fd)


def _worktree_findings(root, rel, pin):
    path = os.path.join(root, rel)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return ["missing from the working tree"]
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(rel, type(exc).__name__))
    if stat.S_ISLNK(info.st_mode):
        return ["is a symbolic link in the working tree"]
    if os.path.realpath(path) != os.path.join(os.path.realpath(root), rel):
        return ["working-tree path traverses a symbolic link"]
    digest = _worktree_digest(path, rel)
    if digest is None:
        return ["is not a regular file in the working tree"]
    if digest != pin:
        return ["working-tree digest mismatch"]
    return []


def _pin_findings(root, git, env, rel, pin, index, head):
    findings = []
    if rel not in index:
        findings.append("not tracked")
    entry = head.get(rel)
    if entry is None:
        findings.append("not in HEAD")
    elif entry[0] == "120000":
        findings.append("committed in HEAD as a symbolic link")
    elif entry[0] not in REGULAR_MODES or entry[1] != "blob":
        findings.append("committed in HEAD as a non-regular entry (mode {})".format(entry[0]))
    elif _blob_digest(root, git, env, entry[2]) != pin:
        findings.append("HEAD blob digest mismatch")
    findings.extend(_worktree_findings(root, rel, pin))
    return findings


def _verdict(cannot, findings):
    if cannot:
        return 2
    return 0 if not findings else 1


def evaluate(root, git, env):
    """(exit code, report lines) for the repository at root, running git as git with env."""
    if git is None:
        return 2, ["error: git unavailable on PATH (cannot evaluate)"]
    try:
        index = _index_paths(root, git, env)
        head = _head_entries(root, git, env)
        rows = _manifest_rows(root, index)
    except CannotEvaluate as exc:
        return 2, ["error: {} (cannot evaluate)".format(exc)]
    cannot, findings, pins = [], [], {}
    for source, name in rows:
        try:
            table = _table_pins(root, source, name, index, head)
        except CannotEvaluate as exc:
            cannot.append(str(exc))
            continue
        for rel, pin in table:
            pins.setdefault(rel, {}).setdefault(pin, []).append("{}:{}".format(source, name))
    verified = 0
    for rel in sorted(pins):
        if len(pins[rel]) > 1:
            findings.append("{}: pinned to different digests by {}".format(
                rel, ", ".join(sorted(where for wheres in pins[rel].values() for where in wheres))))
            continue
        ((pin, wheres),) = pins[rel].items()
        try:
            found = _pin_findings(root, git, env, rel, pin, index, head)
        except CannotEvaluate as exc:
            cannot.append(str(exc))
            continue
        findings.extend("{} ({}): {}".format(rel, ", ".join(wheres), item) for item in found)
        verified += 0 if found else 1
    lines = ["error: {} (cannot evaluate)".format(item) for item in cannot]
    lines += ["FAIL: {}".format(item) for item in findings]
    code = _verdict(cannot, findings)
    if code == 0:
        lines.append("fixture-pins: {} pin(s) in {} declared table(s) verified".format(verified, len(rows)))
    return code, lines


def _live():
    from _git_fixture_env import caller_env_without_git
    code, lines = evaluate(ROOT, shutil.which("git"), caller_env_without_git())
    for line in lines:
        print(line, file=sys.stdout if code == 0 else sys.stderr)
    return code


# --- self-test --------------------------------------------------------------------------------------
# Everything below this marker is the self-test; a flip edits only the gate code above it.
SELF_TEST_MARKER = "# --- self-test ---"
SUITE_ID = "fixture-pins-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
# Flips that keep the vector's exit code and change only its report, because another leg of the gate still
# fails the vector closed: an untracked fixture is also not in HEAD, a final-component link to another file
# also fails the real-path leg, and a git that cannot launch or exits nonzero on every read leaves an empty
# index, so the manifest reads as untracked. Every other flip must change the exit code. The tracked, lstat
# and git-exit legs each have a second vector whose flip does (index-only-removal, symlink-loop,
# head-blob-object-missing); the launch-error leg has none.
MESSAGE_ONLY_FLIPS = ("flip/untracked-as-tracked", "flip/symlink-lstat-dropped", "flip/git-launch-error-swallowed",
                      "flip/git-exit-ignored")
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
ALPHA = b"alpha fixture\n"
BRAVO = b"bravo fixture\n"
ALPHA_PIN = hashlib.sha256(ALPHA).hexdigest()
BRAVO_PIN = hashlib.sha256(BRAVO).hexdigest()
TABLES_REL = "tools/fixture_tables.py"
GOOD_SOURCE = 'PINNED = {\n    "fx/a.txt": "%s",\n}\n' % ALPHA_PIN
GOOD_ROW = '\n[[table]]\nsource = "tools/fixture_tables.py"\nname = "PINNED"\n'


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _manifest(*names):
    return "format-version = 1\n" + "".join(
        '\n[[table]]\nsource = "{}"\nname = "{}"\n'.format(TABLES_REL, name) for name in names)


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _fixture_git(root, *args):
    from _git_fixture_env import git_fixture_env
    result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                             "-C", str(root), *args], capture_output=True, text=True,
                            env=git_fixture_env(), timeout=GIT_TIMEOUT, check=False)
    if result.returncode != 0:
        raise CannotEvaluate("fixture git {} failed: {}".format(args[0], result.stderr.strip()))
    return result.stdout


def _repo(root, source=GOOD_SOURCE, manifest=_manifest("PINNED"), extra=None, init=True):
    """A throwaway repository holding fx/a.txt, the table source, the manifest and extra files, committed."""
    _write(root, "fx/a.txt", ALPHA)
    _write(root, TABLES_REL, source)
    _write(root, MANIFEST_REL, manifest)
    for rel, data in (extra or {}).items():
        _write(root, rel, data)
    if init:
        _fixture_git(root, "init", "-q")
        _commit(root)
    return root


def _commit(root):
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")


def _pins_source(**pins):
    return "PINNED = {\n" + "".join('    "{}": "{}",\n'.format(rel, pin) for rel, pin in pins.items()) + "}\n"


def _v_clean(root):
    return _repo(root)


def _v_missing(root):
    _repo(root)
    (root / "fx/a.txt").unlink()
    return root


def _v_untracked(root):
    _repo(root, source=GOOD_SOURCE + 'UNTRACKED = {\n    "fx/u.txt": "%s",\n}\n' % ALPHA_PIN,
          manifest=_manifest("PINNED", "UNTRACKED"))
    _write(root, "fx/u.txt", ALPHA)
    return root


def _v_head_mismatch(root):
    _repo(root, source='PINNED = {\n    "fx/a.txt": "%s",\n}\n' % BRAVO_PIN)
    _write(root, "fx/a.txt", BRAVO)
    _fixture_git(root, "add", "fx/a.txt")
    return root


def _v_worktree_mismatch(root):
    _repo(root)
    _write(root, "fx/a.txt", BRAVO)
    return root


def _v_symlink_final(root):
    _repo(root, extra={"fx/real.txt": ALPHA})
    (root / "fx/a.txt").unlink()
    (root / "fx/a.txt").symlink_to("real.txt")
    return root


def _v_symlink_ancestor(root):
    _repo(root)
    (root / "fx").rename(root / "fy")
    (root / "fx").symlink_to("fy")
    return root


def _v_staged(root):
    _repo(root, source=GOOD_SOURCE + 'STAGED = {\n    "fx/s.txt": "%s",\n}\n' % ALPHA_PIN,
          manifest=_manifest("PINNED", "STAGED"))
    _write(root, "fx/s.txt", ALPHA)
    _fixture_git(root, "add", "fx/s.txt")
    return root


def _v_table_missing(root):
    return _repo(root, manifest=_manifest("PINNED", "RENAMED"))


def _v_table_empty(root):
    return _repo(root, source=GOOD_SOURCE + "EMPTY = {}\n", manifest=_manifest("PINNED", "EMPTY"))


def _v_computed_path(root):
    computed = 'COMPUTED = {\n    "fx/a.txt": "%s",\n    BASE / f"{n}.txt": "%s",\n}\n' % (ALPHA_PIN, ALPHA_PIN)
    return _repo(root, source=GOOD_SOURCE + computed, manifest=_manifest("PINNED", "COMPUTED"))


def _v_duplicate_key(root):
    duplicate = 'DUPLICATE = {\n    "fx/a.txt": "%s",\n    "fx/a.txt": "%s",\n}\n' % (ALPHA_PIN, ALPHA_PIN)
    return _repo(root, source=GOOD_SOURCE + duplicate, manifest=_manifest("PINNED", "DUPLICATE"))


def _v_manifest_empty(root):
    return _repo(root, manifest="format-version = 1\n")


def _v_outside_repo(root):
    return _repo(root, init=False)


def _v_conflicting(root):
    other = 'OTHER = {\n    "fx/a.txt": "%s",\n}\n' % BRAVO_PIN
    return _repo(root, source=GOOD_SOURCE + other, manifest=_manifest("PINNED", "OTHER"))


def _v_bad_digest(root):
    return _repo(root, source=_pins_source(**{"fx/a.txt": ALPHA_PIN.upper()}))


def _v_bad_path(root):
    return _repo(root, source=_pins_source(**{"fx/../fx/a.txt": ALPHA_PIN}))


def _v_precedence(root):
    _repo(root, manifest=_manifest("PINNED", "RENAMED"))
    (root / "fx/a.txt").unlink()
    return root


def _v_manifest_absent(root):
    _repo(root)
    _fixture_git(root, "rm", "-q", MANIFEST_REL)
    return root


def _v_manifest_nested(root):
    depth = 2000
    return _repo(root, manifest=_manifest("PINNED") + "deep = " + "[" * depth + "]" * depth + "\n")


def _v_manifest_invalid(root):
    return _repo(root, manifest=_manifest("PINNED") + "this is not toml\n")


def _v_manifest_unknown_key(root):
    return _repo(root, manifest="extra = 1\n" + _manifest("PINNED"))


def _v_bound_twice(root):
    return _repo(root, source=GOOD_SOURCE + "PINNED = {}\n")


def _v_fifo(root):
    _repo(root)
    (root / "fx/a.txt").unlink()
    os.mkfifo(root / "fx/a.txt")
    return root


def _v_symlink_loop(root):
    # A link to itself: its real path is its own path, so only the lstat leg reports it as a link.
    _repo(root)
    (root / "fx/a.txt").unlink()
    (root / "fx/a.txt").symlink_to("a.txt")
    return root


def _v_index_removed(root):
    # Removed from the index only: HEAD and the working tree still hold the pinned bytes.
    _repo(root)
    _fixture_git(root, "rm", "-q", "--cached", "fx/a.txt")
    return root


def _v_blob_missing(root):
    # HEAD names the blob but the object database lacks it, so only git cat-file's exit status reports it.
    _repo(root)
    oid = _fixture_git(root, "rev-parse", "HEAD:fx/a.txt").strip()
    (root / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    return root


def _v_blob_replaced(root):
    # HEAD holds BRAVO; a replace ref makes its blob read as ALPHA, the pinned bytes.
    _repo(root)
    alpha = _fixture_git(root, "rev-parse", "HEAD:fx/a.txt").strip()
    _write(root, "fx/a.txt", BRAVO)
    _commit(root)
    _write(root, "fx/a.txt", ALPHA)
    _fixture_git(root, "replace", _fixture_git(root, "rev-parse", "HEAD:fx/a.txt").strip(), alpha)
    return root


def _v_commit_replaced(root):
    # HEAD holds BRAVO; a replace ref makes HEAD read as the older commit that held ALPHA.
    _repo(root)
    older = _fixture_git(root, "rev-parse", "HEAD").strip()
    _write(root, "fx/a.txt", BRAVO)
    _commit(root)
    _write(root, "fx/a.txt", ALPHA)
    _fixture_git(root, "replace", _fixture_git(root, "rev-parse", "HEAD").strip(), older)
    return root


def _v_source_index_removed(root):
    _repo(root)
    _fixture_git(root, "rm", "-q", "--cached", TABLES_REL)
    return root


def _v_source_staged(root):
    _repo(root, manifest=_manifest("PINNED") + '\n[[table]]\nsource = "tools/staged_tables.py"\nname = "STAGED"\n')
    _write(root, "tools/staged_tables.py", 'STAGED = {\n    "fx/a.txt": "%s",\n}\n' % ALPHA_PIN)
    _fixture_git(root, "add", "tools/staged_tables.py")
    return root


def _v_source_untracked(root):
    _repo(root, manifest=_manifest("PINNED") + '\n[[table]]\nsource = "tools/loose_tables.py"\nname = "LOOSE"\n')
    _write(root, "tools/loose_tables.py", 'LOOSE = {\n    "fx/a.txt": "%s",\n}\n' % ALPHA_PIN)
    return root


def _with_source(tail):
    """A builder for a repository whose table source is the good one followed by tail."""
    return lambda root: _repo(root, source=GOOD_SOURCE + tail)


def _load_mutant(source, directory, tag):
    """Write one flip of this gate's own source under the self-test's scratch directory and load it with
    importlib (never a dynamic code string). This helper is the self-test's ONE code-loading site, so the
    dangerous-api allowlist entry is scoped to it."""
    import importlib.util
    path = Path(directory) / "fixture_pins_flip_{}.py".format(tag)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fixture_pins_flip_{}".format(tag), str(path))
    module = importlib.util.module_from_spec(spec)
    saved_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_sys_path
    return module


def _flip(old, new, directory, tag):
    """The gate with its one occurrence of old (above the self-test marker) replaced by new, loaded; or a
    string naming why no flip could be built (an anchor that is absent or repeated fails its check)."""
    text = Path(__file__).read_text(encoding="utf-8")
    head, marker, tail = text.partition("\n" + SELF_TEST_MARKER)
    if not marker:
        return "self-test marker not found"
    if head.count(old) != 1:
        return "flip anchor occurs {} times".format(head.count(old))
    return _load_mutant(head.replace(old, new) + marker + tail, directory, tag)


def _outcome(module, root, git, env, marker):
    """(exit code, whether the report names marker) of module.evaluate, or the class of what it raised."""
    try:
        code, lines = module.evaluate(root, git, env)
    except Exception as exc:
        return "crashed ({})".format(type(exc).__name__)
    return code, marker in "\n".join(lines)


def _self_test_cases(base, git):
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_CEILING_DIRECTORIES=str(base))
    this = sys.modules[__name__]
    missing_git = str(base / "no-such-git")
    flips = base / "flips"
    flips.mkdir()
    # Each row: the vector, its flip and its control after the flip, the builder, which git the gate runs,
    # the expected exit and report marker, and the flip as (old, new) gate source text.
    for vector_id, flip_id, control_id, build, use_git, want, marker, old, new in (
            ("vector/clean", "flip/clean-always-fails", "control/clean", _v_clean, git, 0, "1 pin(s)",
             "return 0 if not findings else 1", "return 1"),
            ("vector/missing", "flip/missing-skipped", "control/missing", _v_missing, git, 1,
             "missing from the working tree",
             'return ["missing from the working tree"]', "return []"),
            ("vector/untracked", "flip/untracked-as-tracked", "control/untracked", _v_untracked, git, 1,
             "fx/u.txt (tools/fixture_tables.py:UNTRACKED): not tracked",
             "if rel not in index:", "if False:"),
            ("vector/head-blob-mismatch", "flip/head-blob-skipped", "control/head-blob-mismatch",
             _v_head_mismatch, git, 1, "HEAD blob digest mismatch",
             "elif _blob_digest(root, git, env, entry[2]) != pin:", "elif False:"),
            ("vector/worktree-mismatch", "flip/worktree-hash-skipped", "control/worktree-mismatch",
             _v_worktree_mismatch, git, 1, "working-tree digest mismatch",
             "if digest != pin:", "if False:"),
            ("vector/symlink-final", "flip/symlink-lstat-dropped", "control/symlink-final", _v_symlink_final, git,
             1, "is a symbolic link in the working tree",
             "if stat.S_ISLNK(info.st_mode):\n        return [", "if False:\n        return ["),
            ("vector/symlink-ancestor", "flip/symlink-realpath-dropped", "control/symlink-ancestor",
             _v_symlink_ancestor, git, 1, "working-tree path traverses a symbolic link",
             "if os.path.realpath(path) != os.path.join(os.path.realpath(root), rel):", "if False:"),
            ("vector/staged-not-committed", "flip/empty-ls-tree-passes", "control/staged-not-committed",
             _v_staged, git, 1, "fx/s.txt (tools/fixture_tables.py:STAGED): not in HEAD",
             'findings.append("not in HEAD")', "pass"),
            ("vector/table-missing", "flip/absent-table-skipped", "control/table-missing", _v_table_missing, git, 2,
             "tools/fixture_tables.py:RENAMED: table not found",
             'raise CannotEvaluate("{}: table not found".format(where))', "return []"),
            ("vector/table-empty", "flip/empty-table-passes", "control/table-empty", _v_table_empty, git, 2,
             "tools/fixture_tables.py:EMPTY: table empty",
             "if not keys:", "if False:"),
            ("vector/computed-path", "flip/non-literal-skipped", "control/computed-path", _v_computed_path, git, 2,
             "tools/fixture_tables.py:COMPUTED: non-literal entry at line",
             'raise CannotEvaluate("{}: non-literal entry at line {}".format(where, value.lineno))', "continue"),
            ("vector/duplicate-key", "flip/duplicate-key-dropped", "control/duplicate-key", _v_duplicate_key, git, 2,
             "tools/fixture_tables.py:DUPLICATE: duplicate key 'fx/a.txt'",
             "if len(set(keys)) != len(keys):", "if False:"),
            ("vector/manifest-empty", "flip/zero-tables-pass", "control/manifest-empty", _v_manifest_empty, git, 2,
             "declares no tables", "if not tables:", "if False:"),
            ("vector/manifest-nested", "flip/manifest-recursion-unhandled", "control/manifest-nested",
             _v_manifest_nested, git, 2, "tools/fixture-pins.toml: malformed (RecursionError)",
             "except (ValueError, RecursionError, MemoryError) as exc:", "except (ValueError, MemoryError) as exc:"),
            ("vector/manifest-invalid-toml", "flip/manifest-valueerror-unhandled", "control/manifest-invalid-toml",
             _v_manifest_invalid, git, 2, "tools/fixture-pins.toml: malformed (TOMLDecodeError)",
             "except (ValueError, RecursionError, MemoryError) as exc:",
             "except (RecursionError, MemoryError) as exc:"),
            ("vector/git-unavailable", "flip/git-launch-error-swallowed", "control/git-unavailable", _v_clean,
             missing_git, 2, "git ls-files could not run",
             'raise CannotEvaluate("git {} could not run ({})".format(args[0], type(exc).__name__))',
             'return b""'),
            ("vector/outside-repository", "flip/git-exit-ignored", "control/outside-repository", _v_outside_repo, git,
             2, "git ls-files exited 128", "if result.returncode != 0:", "if False:"),
            ("vector/index-only-removal", "flip/index-removal-as-tracked", "control/index-only-removal",
             _v_index_removed, git, 1, "fx/a.txt (tools/fixture_tables.py:PINNED): not tracked",
             "if rel not in index:", "if False:"),
            ("vector/symlink-loop", "flip/symlink-loop-lstat-dropped", "control/symlink-loop", _v_symlink_loop, git,
             1, "is a symbolic link in the working tree",
             "if stat.S_ISLNK(info.st_mode):\n        return [", "if False:\n        return ["),
            ("vector/head-blob-object-missing", "flip/blob-read-exit-ignored", "control/head-blob-object-missing",
             _v_blob_missing, git, 2, "git cat-file exited 128", "if result.returncode != 0:", "if False:"),
            ("vector/blob-replaced", "flip/blob-replace-honoured", "control/blob-replaced", _v_blob_replaced, git, 1,
             "HEAD blob digest mismatch",
             '"-C", str(root), "--no-replace-objects", *args]', '"-C", str(root), *args]'),
            ("vector/commit-replaced", "flip/commit-replace-honoured", "control/commit-replaced", _v_commit_replaced,
             git, 1, "HEAD blob digest mismatch",
             '"-C", str(root), "--no-replace-objects", *args]', '"-C", str(root), *args]'),
            ("vector/bound-by-match-as", "flip/match-as-binding-unseen", "control/bound-by-match-as",
             _with_source("match 0:\n    case PINNED:\n        pass\n"), git, 2, "table bound 2 times",
             '(ast.MatchAs, "name")', '(ast.MatchAs, "lineno")'),
            ("vector/bound-by-match-star", "flip/match-star-binding-unseen", "control/bound-by-match-star",
             _with_source("match []:\n    case [*PINNED]:\n        pass\n"), git, 2, "table bound 2 times",
             '(ast.MatchStar, "name")', '(ast.MatchStar, "lineno")'),
            ("vector/bound-by-match-rest", "flip/match-rest-binding-unseen", "control/bound-by-match-rest",
             _with_source("match {}:\n    case {**PINNED}:\n        pass\n"), git, 2, "table bound 2 times",
             '(ast.MatchMapping, "rest")', '(ast.MatchMapping, "lineno")'),
            ("vector/bound-by-except-as", "flip/except-binding-unseen", "control/bound-by-except-as",
             _with_source("try:\n    pass\nexcept ValueError as PINNED:\n    pass\n"), git, 2, "table bound 2 times",
             '(ast.ExceptHandler, "name")', '(ast.ExceptHandler, "lineno")'),
            ("vector/bound-by-except-star-as", "flip/except-star-binding-unseen", "control/bound-by-except-star-as",
             _with_source("try:\n    pass\nexcept* ValueError as PINNED:\n    pass\n"), git, 2, "table bound 2 times",
             '(ast.ExceptHandler, "name")', '(ast.ExceptHandler, "lineno")'),
            ("vector/bound-by-walrus-decorator", "flip/decorator-walk-dropped", "control/bound-by-walrus-decorator",
             _with_source("@(PINNED := lambda f: f)\ndef g():\n    pass\n"), git, 2, "table bound 2 times",
             'stack.extend(getattr(node, "decorator_list", []))', "pass"),
            ("vector/bound-by-walrus-default", "flip/default-walk-dropped", "control/bound-by-walrus-default",
             _with_source("def g(x=(PINNED := 1)):\n    pass\n"), git, 2, "table bound 2 times",
             "stack.extend(default for default in node.args.defaults + node.args.kw_defaults if default is not None)",
             "pass"),
            ("vector/bound-by-walrus-class-base", "flip/class-base-walk-dropped", "control/bound-by-walrus-class-base",
             _with_source("class C((PINNED := object)):\n    pass\n"), git, 2, "table bound 2 times",
             "stack.extend(node.decorator_list + node.bases + node.keywords)", "pass"),
            ("vector/subscript-store", "flip/store-check-dropped", "control/subscript-store",
             _with_source('PINNED["fx/a.txt"] = "%s"\n' % BRAVO_PIN), git, 2,
             "table altered by a subscript or attribute store at module scope, line 4", "if stores:", "if False:"),
            ("vector/source-index-removed", "flip/source-tracked-check-dropped", "control/source-index-removed",
             _v_source_index_removed, git, 2, "tools/fixture_tables.py:PINNED: source not tracked",
             "if source not in index:", "if False:"),
            ("vector/source-staged-only", "flip/source-head-check-dropped", "control/source-staged-only",
             _v_source_staged, git, 2, "tools/staged_tables.py:STAGED: source not committed in HEAD",
             'if entry is None or entry[0] not in REGULAR_MODES or entry[1] != "blob":', "if False:"),
    ):
        tag = vector_id.partition("/")[2]
        root = build(base / tag)
        check(vector_id, _outcome(this, root, use_git, env, marker), (want, True))
        mutant = _flip(old, new, flips, tag)
        flipped = mutant if isinstance(mutant, str) else _outcome(mutant, root, use_git, env, marker)
        # An uncaught exception exits the process 1; a string that is not a crash means no flip was built.
        flipped_exit = flipped[0] if isinstance(flipped, tuple) else 1 if flipped.startswith("crashed") else None
        if flip_id in MESSAGE_ONLY_FLIPS:
            red = flipped_exit == want and flipped != (want, True)
        else:
            red = flipped_exit is not None and flipped_exit != want
        check(flip_id, (not isinstance(mutant, str), red), (True, True))
        print("{}: intact {!r}, flipped {!r}".format(flip_id, (want, True), flipped))
        check(control_id, _outcome(this, root, use_git, env, marker), (want, True))
    # Vectors with no flip: the remaining fail-closed legs, each a single red or green outcome.
    for vector_id, build, want, marker in (
            ("vector/conflicting-pins", _v_conflicting, 1, "fx/a.txt: pinned to different digests by "
             "tools/fixture_tables.py:OTHER, tools/fixture_tables.py:PINNED"),
            ("vector/malformed-digest", _v_bad_digest, 2, "the pin is not 64 lowercase hex digits"),
            ("vector/dotdot-path", _v_bad_path, 2, "is not a repo-relative path"),
            ("vector/precedence-two-over-one", _v_precedence, 2, "table not found"),
            ("vector/manifest-absent", _v_manifest_absent, 2, "tools/fixture-pins.toml: not tracked"),
            ("vector/manifest-unknown-key", _v_manifest_unknown_key, 2, "unknown key(s) extra"),
            ("vector/table-bound-twice", _v_bound_twice, 2, "table bound 2 times"),
            ("vector/fifo-not-regular", _v_fifo, 1, "is not a regular file in the working tree"),
            ("vector/bound-by-walrus-lambda-default", _with_source("f = lambda x=(PINNED := 1): x\n"), 2,
             "table bound 2 times"),
            ("vector/subscript-augmented-store", _with_source('PINNED["fx/a.txt"] += ""\n'), 2,
             "table altered by a subscript or attribute store at module scope, line 4"),
            ("vector/attribute-store", _with_source("PINNED.extra = 1\n"), 2,
             "table altered by a subscript or attribute store at module scope, line 4"),
            ("vector/subscript-delete", _with_source('if True:\n    del PINNED["fx/a.txt"]\n'), 2,
             "table altered by a subscript or attribute store at module scope, line 5"),
            ("vector/source-untracked", _v_source_untracked, 2, "tools/loose_tables.py:LOOSE: source not tracked"),
    ):
        root = build(base / vector_id.partition("/")[2])
        check(vector_id, _outcome(this, root, git, env, marker), (want, True))
    check("vector/no-git-on-path", evaluate(base / "clean", None, env), (2, [
        "error: git unavailable on PATH (cannot evaluate)"]))


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    from _git_fixture_env import fixture_git_lifecycle
    try:
        with fixture_git_lifecycle() as git, tempfile.TemporaryDirectory(prefix="fixture-pins-selftest-") as raw:
            _self_test_cases(Path(raw), git)
    except (CannotEvaluate, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
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
    print("SELF-TEST PASS: {} unique checks executed (each vector red or green on its throwaway repository; "
          "each flip/ check edits one gate line and shows the vector's exit code change, except {} {}, which "
          "keep the exit code and show only the report change; each control/ check reruns the intact gate on "
          "it); execution set reconciled against tools/selftest_checks.toml".format(
              len(EXECUTED), len(MESSAGE_ONLY_FLIPS), ", ".join(MESSAGE_ONLY_FLIPS)))
    return 0


USAGE = "usage: check_fixture_pins.py [--self-test] [--execution-report ABS_PATH]"


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    if not argv:
        return _live()
    rest = argv[1:] if argv[0] == "--self-test" else list(argv)
    if not rest and argv[0] == "--self-test":
        return self_test()
    if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
        return self_test(rest[1])
    print(USAGE, file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate runs git as a child, so under an
# ignored SIGCHLD a failing git read would come back as exit 0; the guard runs at the entry line, before
# main() dispatches the live run or the self-test.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_fixture_pins.py") or main())
