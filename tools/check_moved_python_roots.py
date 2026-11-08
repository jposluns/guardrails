#!/usr/bin/env python3
"""Moved-Python-roots gate: a Python file the change MOVES must not keep deriving a repository
directory from __file__ by a fixed number of parent hops that the move silently repointed.

  check_moved_python_roots.py [--root DIR] [--base REV] [--head REV]
                                      exit 0 no finding within scope, 1 findings, 2 cannot evaluate
  check_moved_python_roots.py --self-test
                                      assert the fixture matrix over synthetic git repositories in a
                                      tempdir (never the live repository), each quiet row paired with
                                      its flag-everything flip
  check_moved_python_roots.py [--self-test] --execution-report ABS_PATH
                                      the self-test, also writing its execution report, finalized at
                                      interpreter exit (tools/_selftest_exit_report.py), as the
                                      execution gate runs it

--head defaults to HEAD; --base defaults to the merge base of HEAD and origin/HEAD, and an
origin/HEAD that does not resolve is exit 2. Each revision is resolved with
`git rev-parse --verify --end-of-options REV^{commit}`. Every git call runs with the ambient GIT_*
variables scrubbed and GIT_NO_REPLACE_OBJECTS=1, as the branch-root gate's calls do. The change is
read per merge base of base and head (`git merge-base --all`; none is exit 2, several are each
judged and the findings combined) with
`git diff-tree -r -z --name-status --find-renames=50% -l0 <merge-base> <head> --` (-l0 turns the
rename limit off), parsed strictly: R<nn> takes an old and a new path, A/D/M/T one path, and any
other status letter or malformed record is exit 2. Judged movers: each rename whose both sides end
in .py, and each added .py file whose file name matches at least one deleted .py file (compared
with every such deletion and reported only when none of them derived the head directory); an added
file matching no deletion is new and not judged. Each side read of a judged pairing must be a
regular blob (100644/100755); a symlink or submodule there is exit 2. Blobs are parsed without
being executed (tokenize.detect_encoding honouring PEP 263 cookies, then ast.parse); SyntaxError,
ValueError (null bytes), RecursionError, MemoryError, UnicodeDecodeError or an unreadable blob is
exit 2, for the base version as well as the head version.

Recognized derivations are expressions anchored on __file__ under the accepted wrappers (Path,
pathlib.Path, PurePath, os.path.abspath/realpath/normpath, .resolve(), .absolute(), import aliases
and `from os import path`), hopping with .parent (1), .parents[k] for a literal int k >= 0 (k+1),
os.path.dirname or an imported dirname (1), and literal ".." segments under os.path.join or the /
operator (1 each; path parts after the last hop are ignored); a name bound once by a simple
assignment in the same scope can be extended by later hops. Not recognized: a Path, dirname or
__file__ redefined locally, and text inside comments or strings. In scope only at 2 hops or more
(one hop is the file's own directory, which moves with the file). Anything else anchored on
__file__ (a non-literal index, a helper function, a name bound more than once) is not judged; the
tool prints how many such expressions it skipped. A head derivation is a finding when the base
version at the old path derived no such repository-relative directory (hops above the root are a
distinct outside-the-repository value) or when the directory is absent from the head tree; an
inline `# fixed-hop-target: <dir>` comment naming exactly the head-computed directory clears it,
and lapses by itself when the file moves again.

Exit 0 prints both revisions and the judged and compared counts, or an empty-scope line when the
change moves no Python file, never a claim that anything works. A pass establishes neither that the
moved code runs nor that its paths are correct.

DISCLOSED RESIDUALS of this implementation: the marker is read textually from the finding's source
line, so a string literal on that same line spelling the marker can clear that line's finding;
import aliases are collected from every import statement in the module, whatever scope it is in, so
a function-local import is honoured module-wide; a function's decorators and default expressions
are judged in the function's own scope rather than the enclosing one; and the absent-directory leg
of the verdict is reachable end to end only for hops above the repository root, because a k-hop
derivation at the new path names an ancestor of the moved file, which necessarily exists in the
head tree (the self-test exercises the in-tree branch through a patched tree probe and says so).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_moved_python_roots.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import io
import os
import re
import subprocess
import tempfile
import tokenize
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import _git_fixture_env  # noqa: E402  (self-test fixtures only: hermetic git environment)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "tools/selftest_checks.toml"
SUITE_ID = "moved-python-roots-selftest"
GIT_TIMEOUT = 60
CHILD_TIMEOUT = 240
MIN_HOPS = 2
USAGE = ("usage: check_moved_python_roots.py [--root DIR] [--base REV] [--head REV] | "
         "[--self-test] [--execution-report ABS_PATH]")
_REGULAR_BLOB_MODES = ("100644", "100755")
_MARKER_RE = re.compile(r"#\s*fixed-hop-target:[ \t]*(.+?)\s*$")
_SCOPE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


class CannotEvaluate(RuntimeError):
    """A required repository input or git result could not answer the question."""


def _git_env():
    """Ambient GIT_* scrubbed and an offline, non-interactive, replace-refs-free posture, as the
    branch-root gate's calls run (a replace ref could otherwise redirect merge-base or a tree read)."""
    env = dict(os.environ)
    for key in [key for key in env if key.startswith("GIT_")]:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _proc_failure(exc):
    raise CannotEvaluate("git invocation failed: {}".format(exc))


def _stderr_text(proc):
    return proc.stderr.decode("utf-8", "replace").strip()


def _resolve_commit(root, rev):
    """The full commit id REV resolves to, or CannotEvaluate naming the revision."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--end-of-options",
             rev + "^{commit}"],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    if proc.returncode != 0:
        raise CannotEvaluate("revision {!r} does not resolve to a commit: {}".format(
            rev, _stderr_text(proc) or "git rev-parse failed"))
    return proc.stdout.decode("ascii", "replace").strip()


def _default_base(root, head_sha):
    """The default --base: the merge base of head and origin/HEAD, each failure a cannot-evaluate."""
    origin_sha = _resolve_commit(root, "origin/HEAD")
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "merge-base", head_sha, origin_sha],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    if proc.returncode != 0:
        raise CannotEvaluate(
            "no merge base between HEAD and origin/HEAD ({}); fetch enough history or pass "
            "--base REV".format(_stderr_text(proc) or "git merge-base failed"))
    return proc.stdout.decode("ascii", "replace").strip()


def _merge_bases(root, base, head):
    """Every merge base of base and head; none (a shallow or unrelated history) is cannot-evaluate."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--all", base, head],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    bases = proc.stdout.decode("ascii", "replace").split()
    if proc.returncode != 0 or not bases:
        raise CannotEvaluate(
            "no merge base between {} and {} ({}); a shallow or unrelated history cannot be "
            "judged, fetch enough history or choose related revisions".format(
                base, head, _stderr_text(proc) or "git merge-base found none"))
    return bases


def _diff_tree_bytes(root, merge_base, head):
    """The raw NUL-delimited --name-status records of the change, rename detection on with no
    rename limit (-l0: a 91-file move must not lose pairings to diff.renameLimit, and plumbing
    ignores diff.renames). Named so the self-test can patch in a malformed record."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff-tree", "-r", "-z", "--name-status",
             "--find-renames=50%", "-l0", merge_base, head, "--"],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    if proc.returncode != 0:
        raise CannotEvaluate("git diff-tree {} {} failed: {}".format(
            merge_base, head, _stderr_text(proc) or proc.returncode))
    return proc.stdout


def _changes(root, merge_base, head):
    """(renames [(old, new)], added [path], deleted [path]) parsed strictly from the NUL-separated
    records; any other status letter or a malformed record is cannot-evaluate."""
    tokens = _diff_tree_bytes(root, merge_base, head).split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    renames, added, deleted = [], [], []
    i = 0
    while i < len(tokens):
        try:
            status = tokens[i].decode("ascii")
        except UnicodeDecodeError:
            raise CannotEvaluate("malformed diff-tree record: undecodable status token")
        if status in ("A", "D", "M", "T"):
            if i + 1 >= len(tokens):
                raise CannotEvaluate(
                    "malformed diff-tree record: status {} without its path".format(status))
            path = tokens[i + 1].decode("utf-8", "surrogateescape")
            if status == "A":
                added.append(path)
            elif status == "D":
                deleted.append(path)
            i += 2
        elif len(status) >= 2 and status[0] == "R" and status[1:].isdigit():
            if i + 2 >= len(tokens):
                raise CannotEvaluate(
                    "malformed diff-tree record: status {} without both paths".format(status))
            renames.append((tokens[i + 1].decode("utf-8", "surrogateescape"),
                            tokens[i + 2].decode("utf-8", "surrogateescape")))
            i += 3
        else:
            raise CannotEvaluate(
                "malformed diff-tree record: unrecognized status {!r}".format(status))
    return renames, added, deleted


def _tree_entry(root, rev, path):
    """(mode, type, object id) of path in rev's tree, or CannotEvaluate (absent included)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-z", "--full-tree", rev, "--",
             ":(literal)" + path],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    record = proc.stdout.split(b"\0", 1)[0]
    if proc.returncode != 0 or not record:
        raise CannotEvaluate("cannot read {!r} from {}: {}".format(
            path, rev, _stderr_text(proc) or "not in that tree"))
    try:
        header = record.split(b"\t", 1)[0].decode("ascii").split()
        mode, otype, oid = header[0], header[1], header[2]
    except (UnicodeDecodeError, IndexError):
        raise CannotEvaluate("cannot read {!r} from {}: malformed ls-tree record".format(path, rev))
    return mode, otype, oid


def _require_blob(mode, otype, rev, path):
    if otype != "blob" or mode not in _REGULAR_BLOB_MODES:
        raise CannotEvaluate(
            "{!r} in {} is not a regular file (mode {}, type {}); a symlink or submodule on a "
            "pairing side cannot be judged".format(path, rev, mode, otype))


def _blob_bytes(root, oid, rev, path):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", oid],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        _proc_failure(exc)
    if proc.returncode != 0:
        raise CannotEvaluate("cannot read blob {} ({!r} in {}): {}".format(
            oid, path, rev, _stderr_text(proc) or "git cat-file failed"))
    return proc.stdout


def _dir_in_head_tree(root, head, directory, cache):
    """Whether the repository-relative directory exists in head's tree; an outside-the-repository
    value never does and the root always does."""
    if directory == ".":
        return True
    if directory.startswith(".."):
        return False
    if directory not in cache:
        try:
            _mode, otype, _oid = _tree_entry(root, head, directory)
        except CannotEvaluate:
            otype = "absent"
        cache[directory] = otype == "tree"
    return cache[directory]


def _parse_python(blob, label):
    """The decoded source text and its AST, read without being executed; every parse failure the
    plan names is cannot-evaluate, for a base version as for a head version."""
    try:
        encoding, _first_lines = tokenize.detect_encoding(io.BytesIO(blob).readline)
        text = blob.decode(encoding)
        return text, ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("cannot parse {}: {}: {}".format(label, type(exc).__name__, exc))


# --- derivation recognition over the AST -----------------------------------------------------------

_SKIP = "skip"


def _collect_aliases(tree):
    """(direct callables {name: kind}, module names {name: module}) from every import statement.
    kind is one of ctor (Path/PurePath), pure (abspath/realpath/normpath), dirname, join."""
    direct, modules = {}, {}
    from_kinds = {"dirname": "dirname", "abspath": "pure", "realpath": "pure",
                  "normpath": "pure", "join": "join"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    modules[alias.asname or "os"] = "os"
                elif alias.name == "os.path":
                    if alias.asname:
                        modules[alias.asname] = "os.path"
                    else:
                        modules["os"] = "os"
                elif alias.name == "pathlib":
                    modules[alias.asname or "pathlib"] = "pathlib"
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "pathlib":
                for alias in node.names:
                    if alias.name in ("Path", "PurePath"):
                        direct[alias.asname or alias.name] = "ctor"
            elif node.module == "os":
                for alias in node.names:
                    if alias.name == "path":
                        modules[alias.asname or "path"] = "os.path"
            elif node.module == "os.path":
                for alias in node.names:
                    kind = from_kinds.get(alias.name)
                    if kind:
                        direct[alias.asname or alias.name] = kind
    return direct, modules


class _Scope:
    def __init__(self):
        self.bind_counts = {}
        self.simple = {}
        self.owned = []

    def poison(self, name):
        self.bind_counts[name] = self.bind_counts.get(name, 0) + 1


def _poison_target(scope, target):
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            scope.poison(node.id)


def _build_scopes(tree):
    """One _Scope per module, function, lambda and class body, each owning the nodes within it
    (stopping at nested scopes), with its non-import bindings counted and its simple single-name
    assignments kept for the bound-once indirection rule."""
    scopes = []

    def collect(scope_node):
        scope = _Scope()
        scopes.append(scope)
        if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = scope_node.args
            for arg in (list(arguments.posonlyargs) + list(arguments.args)
                        + list(arguments.kwonlyargs)):
                scope.poison(arg.arg)
            for arg in (arguments.vararg, arguments.kwarg):
                if arg is not None:
                    scope.poison(arg.arg)
        stack = list(ast.iter_child_nodes(scope_node))
        while stack:
            node = stack.pop()
            if isinstance(node, _SCOPE_TYPES):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    scope.poison(node.name)
                collect(node)
                continue
            scope.owned.append(node)
            if isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    scope.poison(name)
                    scope.simple.setdefault(name, []).append(node.value)
                else:
                    for target in node.targets:
                        _poison_target(scope, target)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                _poison_target(scope, node.target)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                _poison_target(scope, node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                _poison_target(scope, node.optional_vars)
            elif isinstance(node, ast.NamedExpr):
                _poison_target(scope, node.target)
            elif isinstance(node, ast.comprehension):
                _poison_target(scope, node.target)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                scope.poison(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                for name in node.names:
                    scope.poison(name)
            stack.extend(ast.iter_child_nodes(node))
        return scope

    collect(tree)
    return scopes


def _hop(state, count):
    up, downs = state
    for _unused in range(count):
        if downs:
            downs = downs[:-1]
        else:
            up += 1
    return up, downs


def _segments(state, literal):
    for segment in literal.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            state = _hop(state, 1)
        else:
            state = (state[0], state[1] + (segment,))
    return state


def _module_ref(node, scope, modules):
    if isinstance(node, ast.Name):
        if scope.bind_counts.get(node.id):
            return None
        return modules.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr == "path":
        if _module_ref(node.value, scope, modules) == "os":
            return "os.path"
    return None


def _callable_kind(func, scope, aliases):
    direct, modules = aliases
    if isinstance(func, ast.Name):
        if scope.bind_counts.get(func.id):
            return None
        return direct.get(func.id)
    if isinstance(func, ast.Attribute):
        owner = _module_ref(func.value, scope, modules)
        if owner == "pathlib" and func.attr in ("Path", "PurePath"):
            return "ctor"
        if owner == "os.path":
            if func.attr == "dirname":
                return "dirname"
            if func.attr in ("abspath", "realpath", "normpath"):
                return "pure"
            if func.attr == "join":
                return "join"
    return None


def _eval(node, scope, aliases, visiting):
    """None (not anchored on __file__ here), _SKIP (anchored but not judged), or (up, downs):
    up parent hops from the file with the trailing descent parts downs, which the directory
    computation ignores."""
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            return None
        if node.id == "__file__":
            return None if scope.bind_counts.get("__file__") else (0, ())
        binds = scope.bind_counts.get(node.id, 0)
        if binds == 0:
            return None
        if node.id in visiting:
            return _SKIP
        values = scope.simple.get(node.id, [])
        visiting.add(node.id)
        try:
            if binds == 1 and len(values) == 1:
                return _eval(values[0], scope, aliases, visiting)
            anchored = any(_eval(value, scope, aliases, visiting) is not None
                           for value in values)
        finally:
            visiting.discard(node.id)
        return _SKIP if anchored else None
    if isinstance(node, ast.Attribute):
        if node.attr == "parent":
            state = _eval(node.value, scope, aliases, visiting)
            if state is None or state == _SKIP:
                return state
            return _hop(state, 1)
        if node.attr == "parents":
            state = _eval(node.value, scope, aliases, visiting)
            return None if state is None else _SKIP
        return None
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            state = _eval(node.value.value, scope, aliases, visiting)
            if state is None or state == _SKIP:
                return state
            index = node.slice
            if (isinstance(index, ast.Constant) and type(index.value) is int
                    and index.value >= 0):
                return _hop(state, index.value + 1)
            return _SKIP
        return None
    if isinstance(node, ast.Call):
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute")
                and not node.args and not node.keywords):
            return _eval(func.value, scope, aliases, visiting)
        kind = _callable_kind(func, scope, aliases)
        if kind is None:
            return None
        plain = [argument for argument in node.args if not isinstance(argument, ast.Starred)]
        irregular = node.keywords or len(plain) != len(node.args) or not plain
        if irregular:
            anchored = any(
                _eval(value, scope, aliases, visiting) is not None
                for value in ([argument.value if isinstance(argument, ast.Starred) else argument
                               for argument in node.args]
                              + [keyword.value for keyword in node.keywords]))
            return _SKIP if anchored else None
        first = _eval(plain[0], scope, aliases, visiting)
        if first is None or first == _SKIP:
            return first
        if kind == "pure":
            return first if len(plain) == 1 else _SKIP
        if kind == "dirname":
            return _hop(first, 1) if len(plain) == 1 else _SKIP
        state = first
        for argument in plain[1:]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                state = _segments(state, argument.value)
            else:
                return _SKIP
        return state
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _eval(node.left, scope, aliases, visiting)
        if left is None or left == _SKIP:
            return left
        right = node.right
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            return _segments(left, right.value)
        return _SKIP
    return None


def _derivations(tree, text):
    """([(line, column, expression text, up hops)] for every maximal recognized derivation with at
    least one hop, the count of anchored-but-not-judged expressions): each expression is judged once
    at its outermost recognized node (an anchored node under a recognized or skipped ancestor is
    that ancestor's)."""
    aliases = _collect_aliases(tree)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    derivs, skipped = [], 0
    for scope in _build_scopes(tree):
        for node in scope.owned:
            if not isinstance(node, ast.expr):
                continue
            result = _eval(node, scope, aliases, set())
            if result is None:
                continue
            ancestor, suppressed = parents.get(node), False
            while ancestor is not None and not isinstance(ancestor, ast.stmt):
                if (isinstance(ancestor, ast.expr)
                        and _eval(ancestor, scope, aliases, set()) is not None):
                    suppressed = True
                    break
                ancestor = parents.get(ancestor)
            if suppressed:
                continue
            if result == _SKIP or result[0] == 0:
                skipped += 1
                continue
            try:
                expr = ast.get_source_segment(text, node) or ast.unparse(node)
            except (ValueError, RecursionError, MemoryError):
                expr = "<expression at line {}>".format(node.lineno)
            derivs.append((node.lineno, node.col_offset, " ".join(expr.split()), result[0]))
    derivs.sort()
    return derivs, skipped


def _derived_dir(rel_path, up):
    """The repository-relative directory up hops from the file at rel_path, working on the path as
    text: '.' is the root, and hops above the root give a distinct ../ value per level."""
    parts = rel_path.split("/")[:-1]
    climb = up - 1
    if climb <= len(parts):
        return "/".join(parts[:len(parts) - climb]) or "."
    return "/".join([".."] * (climb - len(parts)))


def _blob_analysis(root, rev, path, cache):
    """(derivations, skipped count, source lines) of the regular blob at path in rev, cached."""
    key = (rev, path)
    if key not in cache:
        mode, otype, oid = _tree_entry(root, rev, path)
        _require_blob(mode, otype, rev, path)
        blob = _blob_bytes(root, oid, rev, path)
        text, tree = _parse_python(blob, "{!r} in {}".format(path, rev))
        derivs, skipped = _derivations(tree, text)
        cache[key] = (derivs, skipped, text.splitlines())
    return cache[key]


def scan(root, base, head, min_hops=MIN_HOPS, compare=True):
    """Judge the change from every merge base of base to head and return
    (findings, moved files judged, derivations compared, skipped expressions, merge bases).
    compare=False is the flag-everything flip the self-test pairs with every quiet row: every
    in-scope head derivation of a judged mover is a finding, with no base comparison, no head-tree
    probe and no marker."""
    bases = _merge_bases(root, base, head)
    findings, seen = [], set()
    judged, compared, skipped_by_file = set(), set(), {}
    cache, tree_cache = {}, {}
    for merge_base in bases:
        renames, added, deleted = _changes(root, merge_base, head)
        deleted_by_name = {}
        for path in deleted:
            if path.endswith(".py"):
                deleted_by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        pairs = [([old], new) for old, new in renames
                 if old.endswith(".py") and new.endswith(".py")]
        for path in added:
            if path.endswith(".py"):
                candidates = deleted_by_name.get(path.rsplit("/", 1)[-1])
                if candidates:
                    pairs.append((list(candidates), path))
        for olds, new in pairs:
            judged.add(new)
            head_derivs, head_skipped, head_lines = _blob_analysis(root, head, new, cache)
            skipped_by_file[new] = head_skipped
            base_dirs = set()
            if compare:
                # In-scope derivations only (min_hops and up): a one-hop base derivation names
                # the file's own directory, which after a move one level deeper is exactly the
                # stale two-hop head directory, so counting it would mask the seed shape in any
                # mover that also derives its own directory (a sys.path insert line).
                for old in olds:
                    old_derivs, _old_skipped, _old_lines = _blob_analysis(
                        root, merge_base, old, cache)
                    for _line, _col, _expr, up in old_derivs:
                        if up >= min_hops:
                            base_dirs.add(_derived_dir(old, up))
            for line, col, expr, up in head_derivs:
                if up < min_hops:
                    continue
                compared.add((new, line, col))
                head_dir = _derived_dir(new, up)
                if compare:
                    bad = (head_dir not in base_dirs
                           or not _dir_in_head_tree(root, head, head_dir, tree_cache))
                    if bad and 0 < line <= len(head_lines):
                        marker = _MARKER_RE.search(head_lines[line - 1])
                        if marker is not None and marker.group(1) == head_dir:
                            bad = False
                else:
                    bad = True
                if bad:
                    key = (tuple(olds), new, line, col)
                    if key not in seen:
                        seen.add(key)
                        findings.append({
                            "old": ", ".join(olds), "new": new, "line": line, "expr": expr,
                            "hops": up, "head_dir": head_dir,
                            "base_dirs": sorted(base_dirs)})
    findings.sort(key=lambda item: (item["new"], item["line"], item["old"]))
    return findings, len(judged), len(compared), sum(skipped_by_file.values()), bases


def repo_root_default():
    probe = Path.cwd().resolve()
    return next((ancestor for ancestor in [probe, *probe.parents]
                 if (ancestor / ".git").exists()), probe)


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    options = {}
    i = 0
    while i < len(args):
        if args[i] in ("--base", "--head", "--root") and i + 1 < len(args) \
                and args[i] not in options:
            options[args[i]] = args[i + 1]
            i += 2
        else:
            print(USAGE, file=sys.stderr)
            return 2
    root_arg = options.get("--root")
    if root_arg is not None:
        if not root_arg or not os.path.isdir(root_arg):
            print("error: --root {!r} is not an existing directory (cannot evaluate)".format(
                root_arg), file=sys.stderr)
            return 2
        root = Path(root_arg)
    else:
        root = repo_root_default()
    try:
        head_sha = _resolve_commit(root, options.get("--head", "HEAD"))
        if "--base" in options:
            base_sha = _resolve_commit(root, options["--base"])
        else:
            base_sha = _default_base(root, head_sha)
        findings, moved, compared, skipped, _bases = scan(root, base_sha, head_sha)
    except CannotEvaluate as exc:
        print("error: {} (cannot evaluate)".format(exc), file=sys.stderr)
        return 2
    print("base {}".format(base_sha))
    print("head {}".format(head_sha))
    if skipped:
        print("skipped {} expression(s) anchored on __file__ but not judged".format(skipped))
    if findings:
        print("FAIL: {} moved-root finding(s) in {} moved Python file(s)".format(
            len(findings), moved))
        for finding in findings:
            print("  FINDING: {} -> {}: line {}: {} ({} hops): head derives {!r}; base derived "
                  "{}".format(finding["old"], finding["new"], finding["line"], finding["expr"],
                              finding["hops"], finding["head_dir"],
                              ", ".join(repr(d) for d in finding["base_dirs"]) or "nothing"))
        return 1
    if moved == 0:
        print("empty scope: this change moves no Python file; nothing was judged")
        return 0
    print("moved Python files judged: {}; derivations compared: {}".format(moved, compared))
    print("no finding within scope")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# The fixture matrix over synthetic git repositories in a tempdir only (never the live repository).
# Every quiet row is paired with its flag-everything flip (scan(compare=False), the plain matcher
# with no before-and-after comparison; for the one-hop row the flip also lowers the hop minimum to
# 1, and for the not-recognized rows the flip removes exactly the property that earned the quiet, a
# local redefinition or a comment/string placement, since the plain matcher does not recognize
# those either). Fixture git commands run under tools/_git_fixture_env.git_fixture_env, and the
# whole run scrubs the ambient git environment in place first.

FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def check(name, ok, detail=""):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _registered_check_ids():
    """This suite's registered check ids: (set of ids, None), or (None, reason)."""
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    # ValueError and RecursionError too, as the sibling gates' registry reads handle them.
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError,
            MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    suites = data.get("suite")
    for row in suites if isinstance(suites, list) else ():
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if (isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids)
                    and len(set(ids)) == len(ids)):
                return set(ids), None
            break
    return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)


@contextlib.contextmanager
def _patched(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _fx(repo, *args):
    """One fixture git command under the hermetic fixture environment; a failure is a harness
    error (the fixture, not the gate, is broken)."""
    proc = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                          timeout=GIT_TIMEOUT, env=_git_fixture_env.git_fixture_env())
    if proc.returncode != 0:
        raise RuntimeError("fixture git {} failed in {}: {}".format(
            args, repo, proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "surrogateescape").strip()


def _write_files(repo, files):
    for rel, content in files.items():
        target = Path(repo) / rel
        os.makedirs(target.parent, exist_ok=True)
        if isinstance(content, bytes):
            with open(target, "wb") as handle:
                handle.write(content)
        else:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(content)


def _init_repo(base, name):
    repo = Path(base) / name
    repo.mkdir()
    _fx(repo, "init", "-q", "-b", "m")
    _fx(repo, "config", "user.name", "Self Test")
    _fx(repo, "config", "user.email", "selftest@example.invalid")
    return repo


def _commit_state(repo, previous, files, message):
    for rel in set(previous) - set(files):
        (Path(repo) / rel).unlink()
    _write_files(repo, files)
    _fx(repo, "add", "-A")
    _fx(repo, "commit", "-q", "-m", message)
    return _fx(repo, "rev-parse", "HEAD")


def _repo_with(base, name, base_files, head_files, config=()):
    repo = _init_repo(base, name)
    for key, value in config:
        _fx(repo, "config", key, value)
    base_sha = _commit_state(repo, {}, base_files, "base")
    head_sha = _commit_state(repo, base_files, head_files, "head")
    return repo, base_sha, head_sha


def _outcome(repo, base, head, **kwargs):
    """("ok", findings, judged, compared, skipped) or ("cannot", message)."""
    try:
        findings, judged, compared, skipped, _bases = scan(Path(repo), base, head, **kwargs)
        return ("ok", findings, judged, compared, skipped)
    except CannotEvaluate as exc:
        return ("cannot", str(exc))


def _counts(outcome):
    """(status, finding count, judged, compared, skipped) for compact assertions."""
    if outcome[0] != "ok":
        return outcome
    return ("ok", len(outcome[1]), outcome[2], outcome[3], outcome[4])


def _launch(*args, cwd=None):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(__file__).resolve())] + list(args),
        capture_output=True, text=True, timeout=CHILD_TIMEOUT,
        cwd=str(cwd) if cwd is not None else str(ROOT),
        env=_git_fixture_env.git_fixture_env(LC_ALL="C.UTF-8"))
    return result.returncode, result.stdout, result.stderr


P1 = "import pathlib\nROOT = pathlib.Path(__file__).parents[1]\n"


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    this = sys.modules[__name__]
    _git_fixture_env.scrub_git_environment()
    with tempfile.TemporaryDirectory(prefix="moved-python-roots-selftest-") as tmp:
        base = Path(tmp)

        # fixture 1: moved one directory deeper, parents[1] kept (the seed's shape). The body also
        # derives its OWN directory with one hop, as a real tool's sys.path insert line does: the
        # base's one-hop directory equals the stale head directory here, so this row discriminates
        # a comparison set that wrongly admits out-of-scope base derivations.
        seed_body = (P1 + "import sys\n"
                     "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))\n")
        seed, seed_b, seed_h = _repo_with(
            base, "seed",
            {"pkg/tool.py": seed_body + "# v1\n", "pkg/keep.txt": "k\n"},
            {"pkg/sub/tool.py": seed_body + "# v2\n", "pkg/keep.txt": "k\n"})
        got = _outcome(seed, seed_b, seed_h)
        check("move/one-deeper-parents1-flagged",
              _counts(got) == ("ok", 1, 1, 1, 0) and got[1][0]["head_dir"] == "pkg"
              and got[1][0]["base_dirs"] == ["."], got)

        # fixture 2: the same move with hops corrected to parents[2], and its flip
        fixed, fixed_b, fixed_h = _repo_with(
            base, "fixed",
            {"pkg/tool.py": P1, "pkg/keep.txt": "k\n"},
            {"pkg/sub/tool.py": "import pathlib\nROOT = pathlib.Path(__file__).parents[2]\n",
             "pkg/keep.txt": "k\n"})
        check("move/hops-corrected-passes", _counts(_outcome(fixed, fixed_b, fixed_h))
              == ("ok", 0, 1, 1, 0), _outcome(fixed, fixed_b, fixed_h))
        check("move/hops-corrected-companion-flags",
              _counts(_outcome(fixed, fixed_b, fixed_h, compare=False))[:2] == ("ok", 1),
              _outcome(fixed, fixed_b, fixed_h, compare=False))

        # fixture 3: same-depth move between sibling directories, target unchanged, and its flip
        sib, sib_b, sib_h = _repo_with(
            base, "sibling", {"a/tool.py": P1, "x.txt": "1\n"}, {"b/tool.py": P1, "x.txt": "1\n"})
        check("move/same-depth-passes",
              _counts(_outcome(sib, sib_b, sib_h)) == ("ok", 0, 1, 1, 0),
              _outcome(sib, sib_b, sib_h))
        check("move/same-depth-companion-flags",
              _counts(_outcome(sib, sib_b, sib_h, compare=False))[:2] == ("ok", 1),
              _outcome(sib, sib_b, sib_h, compare=False))

        # fixture 4: unedited blob (R100) moved deeper, .parent.parent
        body4 = "from pathlib import Path\nROOT = str(Path(__file__).parent.parent)\n"
        r100, r100_b, r100_h = _repo_with(
            base, "r100", {"x/mod.py": body4}, {"x/deep/mod.py": body4})
        got = _outcome(r100, r100_b, r100_h)
        check("move/unedited-r100-flagged",
              _counts(got) == ("ok", 1, 1, 1, 0) and got[1][0]["hops"] == 2, got)

        # fixture 5: nested dirname around abspath, realpath and normpath
        wraps, wraps_b, wraps_h = _repo_with(
            base, "wraps",
            {"w/a.py": "import os.path\nROOT = os.path.dirname(os.path.dirname("
                       "os.path.abspath(__file__)))\n",
             "w/b.py": "from os.path import dirname, realpath\n"
                       "ROOT = dirname(dirname(realpath(__file__)))\n",
             "w/c.py": "from os.path import dirname, normpath\n"
                       "ROOT = dirname(dirname(normpath(__file__)))\n"},
            {"w/d/a.py": "import os.path\nROOT = os.path.dirname(os.path.dirname("
                         "os.path.abspath(__file__)))\n",
             "w/d/b.py": "from os.path import dirname, realpath\n"
                         "ROOT = dirname(dirname(realpath(__file__)))\n",
             "w/d/c.py": "from os.path import dirname, normpath\n"
                         "ROOT = dirname(dirname(normpath(__file__)))\n"})
        check("derive/nested-dirname-wrappers-flagged",
              _counts(_outcome(wraps, wraps_b, wraps_h)) == ("ok", 3, 3, 3, 0),
              _outcome(wraps, wraps_b, wraps_h))

        # fixture 6: os.path.join(dirname(__file__), "..", ".."), and / ".."
        dots, dots_b, dots_h = _repo_with(
            base, "dots",
            {"p/q/t1.py": "import os\nROOT = os.path.join(os.path.dirname(__file__), '..', "
                          "'..')\n",
             "p/q/t2.py": "from pathlib import Path\nROOT = Path(__file__) / '..' / '..'\n"},
            {"p/q/deep/t1.py": "import os\nROOT = os.path.join(os.path.dirname(__file__), '..', "
                               "'..')\n",
             "p/q/deep/t2.py": "from pathlib import Path\nROOT = Path(__file__) / '..' / '..'\n"})
        got = _outcome(dots, dots_b, dots_h)
        flagged = {finding["new"] for finding in got[1]} if got[0] == "ok" else set()
        check("derive/join-dotdot-flagged", "p/q/deep/t1.py" in flagged, got)
        check("derive/truediv-dotdot-flagged",
              "p/q/deep/t2.py" in flagged and _counts(got)[:2] == ("ok", 2), got)

        # fixture 7: import aliases
        alias_bodies = {
            "a1/f1.py": "import pathlib as pl\nROOT = pl.Path(__file__).parents[1]\n",
            "a2/f2.py": "from pathlib import Path as P\nROOT = P(__file__).parents[1]\n",
            "a3/f3.py": "from os.path import dirname as d\nROOT = d(d(__file__))\n",
            "a4/f4.py": "from os import path\nROOT = path.dirname(path.dirname(__file__))\n"}
        ali, ali_b, ali_h = _repo_with(
            base, "aliases", alias_bodies,
            {rel.split("/")[0] + "/d/" + rel.split("/")[1]: body
             for rel, body in alias_bodies.items()})
        check("derive/aliases-flagged",
              _counts(_outcome(ali, ali_b, ali_h)) == ("ok", 4, 4, 4, 0),
              _outcome(ali, ali_b, ali_h))

        # fixture 8: name bound once, then extended
        body8 = ("from pathlib import Path\nHERE = Path(__file__).resolve().parent\n"
                 "ROOT = HERE.parent.parent\n")
        bound, bound_b, bound_h = _repo_with(
            base, "bound", {"a/b/t.py": body8}, {"a/b/c/t.py": body8})
        got = _outcome(bound, bound_b, bound_h)
        check("derive/bound-name-extended-flagged",
              _counts(got) == ("ok", 1, 1, 1, 0) and got[1][0]["hops"] == 3, got)

        # fixture 9: name bound twice, a non-literal index and a helper-built root are not judged
        body9 = ("from pathlib import Path\nR = Path(__file__).parent\nR = R.parent\n"
                 "X = Path(__file__).parents[len('x')]\nY = locate(__file__)\n")
        skipf, skip_b, skip_h = _repo_with(
            base, "skipped", {"s/t.py": body9}, {"s/d/t.py": body9})
        check("derive/unjudged-anchors-skip-counted",
              _counts(_outcome(skipf, skip_b, skip_h)) == ("ok", 0, 1, 0, 3),
              _outcome(skipf, skip_b, skip_h))

        # fixture 10: a locally redefined Path, and the expression only in a comment or string
        body10 = ("from pathlib import Path\nPath = str\n"
                  "ROOT = Path(__file__).parents[1]\n")
        redef, redef_b, redef_h = _repo_with(
            base, "redefined", {"r/t.py": body10}, {"r/d/t.py": body10})
        check("derive/redefined-path-not-recognized",
              _counts(_outcome(redef, redef_b, redef_h))[:2] == ("ok", 0),
              _outcome(redef, redef_b, redef_h))
        body10c = "from pathlib import Path\nROOT = Path(__file__).parents[1]\n"
        redefc, redefc_b, redefc_h = _repo_with(
            base, "redefined-companion", {"r/t.py": body10c}, {"r/d/t.py": body10c})
        check("derive/redefined-path-companion-flags",
              _counts(_outcome(redefc, redefc_b, redefc_h, compare=False))[:2] == ("ok", 1),
              _outcome(redefc, redefc_b, redefc_h, compare=False))
        body10t = ("from pathlib import Path\n# ROOT = Path(__file__).parents[1]\n"
                   "S = 'Path(__file__).parents[1]'\n")
        text10, text10_b, text10_h = _repo_with(
            base, "comment-string", {"c/t.py": body10t}, {"c/d/t.py": body10t})
        check("derive/comment-string-not-recognized",
              _counts(_outcome(text10, text10_b, text10_h, compare=False)) == ("ok", 0, 1, 0, 0),
              _outcome(text10, text10_b, text10_h, compare=False))
        check("derive/comment-string-companion-flags",
              _counts(_outcome(redefc, redefc_b, redefc_h, compare=False, min_hops=2))[:2]
              == ("ok", 1), "same code outside comment/string flags")

        # fixture 11: one-hop Path(__file__).parent in a moved file, flip at a 1-hop minimum
        body11 = "from pathlib import Path\nHERE = Path(__file__).parent\n"
        one, one_b, one_h = _repo_with(base, "onehop", {"o/t.py": body11}, {"o/d/t.py": body11})
        check("scope/one-hop-passes",
              _counts(_outcome(one, one_b, one_h)) == ("ok", 0, 1, 0, 0),
              _outcome(one, one_b, one_h))
        check("scope/one-hop-companion-min1-flags",
              _counts(_outcome(one, one_b, one_h, compare=False, min_hops=1))[:2] == ("ok", 1),
              _outcome(one, one_b, one_h, compare=False, min_hops=1))

        # fixture 12: an unmoved file (M) with parents[2] is out of scope
        body12 = "import pathlib\nROOT = pathlib.Path(__file__).parents[2]\n"
        unmoved, unm_b, unm_h = _repo_with(
            base, "unmoved", {"a/t.py": body12, "n.txt": "1\n"},
            {"a/t.py": body12 + "# edited\n", "n.txt": "2\n"})
        check("scope/unmoved-file-out-of-scope",
              _counts(_outcome(unmoved, unm_b, unm_h)) == ("ok", 0, 0, 0, 0),
              _outcome(unmoved, unm_b, unm_h))

        # fixture 13: an environment variable with a fixed-hop fallback
        body13 = ("import os\nfrom pathlib import Path\n"
                  "ROOT = os.environ.get('APP_ROOT') or Path(__file__).parents[2]\n")
        envf, envf_b, envf_h = _repo_with(
            base, "env-fallback", {"a/b/t.py": body13}, {"a/b/c/t.py": body13})
        check("derive/env-fallback-flagged",
              _counts(_outcome(envf, envf_b, envf_h)) == ("ok", 1, 1, 1, 0),
              _outcome(envf, envf_b, envf_h))

        # fixture 14: only an environment variable, the working directory or importlib.resources
        body14 = ("import importlib.resources\nimport os\nROOT = os.environ['APP_ROOT']\n"
                  "CWD = os.getcwd()\nDATA = importlib.resources.files('pkg')\n")
        envo, envo_b, envo_h = _repo_with(
            base, "env-only", {"e/t.py": body14}, {"e/d/t.py": body14})
        check("scope/env-only-residue-passes",
              _counts(_outcome(envo, envo_b, envo_h)) == ("ok", 0, 1, 0, 0),
              _outcome(envo, envo_b, envo_h))

        # fixture 15: an added file whose name matches no deletion is new, not judged
        fresh, fresh_b, fresh_h = _repo_with(
            base, "fresh", {"keep.txt": "1\n"},
            {"keep.txt": "1\n", "new/brand_new.py": P1.replace("[1]", "[5]")})
        check("scope/new-file-not-judged",
              _counts(_outcome(fresh, fresh_b, fresh_h)) == ("ok", 0, 0, 0, 0),
              _outcome(fresh, fresh_b, fresh_h))

        # fixture 16: an unpaired delete and add with one matching name, hops kept
        filler = "".join("BASE_{0} = {0}\n".format(n) for n in range(40))
        um, um_b, um_h = _repo_with(
            base, "unpaired", {"a/t.py": P1 + filler}, {"b/c/t.py": P1})
        got = _outcome(um, um_b, um_h)
        check("match/unpaired-single-name-flagged",
              _counts(got) == ("ok", 1, 1, 1, 0) and got[1][0]["head_dir"] == "b", got)

        # fixture 17: a name matching two deletions: one derived the head directory, then neither
        two_a, two_a_b, two_a_h = _repo_with(
            base, "two-dels-pass",
            {"a/t.py": P1 + filler, "lib/pkg/t.py": P1 + "L = 9\n" + filler.replace("BASE", "LIB")},
            {"x/t.py": P1})
        check("match/two-deletions-one-derives-passes",
              _counts(_outcome(two_a, two_a_b, two_a_h))[:2] == ("ok", 0),
              _outcome(two_a, two_a_b, two_a_h))
        two_b, two_b_b, two_b_h = _repo_with(
            base, "two-dels-flag",
            {"m/a/t.py": P1 + filler, "n/b/t.py": P1 + "L = 9\n" + filler.replace("BASE", "LIB")},
            {"q/w/t.py": P1})
        check("match/two-deletions-none-derives-flagged",
              _counts(_outcome(two_b, two_b_b, two_b_h))[:2] == ("ok", 1),
              _outcome(two_b, two_b_b, two_b_h))

        # fixture 18: the head-tree probe refusing the derived directory is a finding. End to end
        # the in-tree branch is unreachable (a k-hop derivation at the new path names an ancestor
        # of the moved file, which exists in the head tree; above the root is fixture 23), so the
        # branch is exercised here through a patched probe over the corrected-move repository.
        with _patched(this, "_dir_in_head_tree",
                      lambda _root, _head, _directory, _cache: False):
            got = _outcome(fixed, fixed_b, fixed_h)
        check("verdict/target-absent-from-head-tree-flagged", _counts(got)[:2] == ("ok", 1), got)

        # fixture 19: the exemption marker clears the named directory only, and lapses on a move
        marked = _init_repo(base, "marked")
        m1 = _commit_state(marked, {}, {"pkg/t.py": P1}, "one")
        marked_two = {"pkg/sub/t.py":
                      "import pathlib\nROOT = pathlib.Path(__file__).parents[1]  "
                      "# fixed-hop-target: pkg\n"}
        m2 = _commit_state(marked, {"pkg/t.py": None}, marked_two, "two")
        m3 = _commit_state(marked, marked_two,
                           {"pkg/sub/deep/t.py": marked_two["pkg/sub/t.py"]}, "three")
        check("marker/exact-directory-clears",
              _counts(_outcome(marked, m1, m2)) == ("ok", 0, 1, 1, 0), _outcome(marked, m1, m2))
        other, other_b, other_h = _repo_with(
            base, "marked-other", {"pkg/t.py": P1},
            {"pkg/sub/t.py": "import pathlib\nROOT = pathlib.Path(__file__).parents[1]  "
                             "# fixed-hop-target: other\n"})
        check("marker/other-directory-does-not-clear",
              _counts(_outcome(other, other_b, other_h))[:2] == ("ok", 1),
              _outcome(other, other_b, other_h))
        check("marker/lapses-after-second-move",
              _counts(_outcome(marked, m2, m3))[:2] == ("ok", 1), _outcome(marked, m2, m3))

        # fixture 20: paths containing a space, a tab, a newline and a non-ASCII character
        odd_olds = {"sp ace/t 1.py": P1 + "# sp\n", "tab/a\tb.py": P1 + "# tab\n",
                    "nl/a\nb.py": P1 + "# nl\n", "uni/café.py": P1 + "# uni\n"}
        odd_heads = {rel.split("/")[0] + "/deep/" + rel.split("/", 1)[1]: body
                     for rel, body in odd_olds.items()}
        odd, odd_b, odd_h = _repo_with(base, "oddpaths", odd_olds, odd_heads)
        check("enum/odd-path-characters-enumerated",
              _counts(_outcome(odd, odd_b, odd_h)) == ("ok", 4, 4, 4, 0),
              _outcome(odd, odd_b, odd_h))

        # fixture 21: repository diff.renameLimit=1 with two inexact renames: -l0 pairs both (the
        # new names match no deletion, so only a rename pairing can judge them)
        lim, lim_b, lim_h = _repo_with(
            base, "renamelimit",
            {"a/one.py": P1 + "ONE = 1\nPAD_ONE = 'one'\n",
             "a/two.py": P1 + "TWO = 1\nPAD_TWO = 'two'\n"},
            {"r/s/first.py": P1 + "ONE = 2\nPAD_ONE = 'one'\n",
             "r/s/second.py": P1 + "TWO = 2\nPAD_TWO = 'two'\n"},
            config=(("diff.renameLimit", "1"),))
        check("enum/rename-limit-disabled",
              _counts(_outcome(lim, lim_b, lim_h)) == ("ok", 2, 2, 2, 0),
              _outcome(lim, lim_b, lim_h))

        # fixture 22: several merge bases (a criss-cross), the findings combined
        criss = _init_repo(base, "crisscross")
        state_a = {"f1/t1.py": P1 + "# f1\n", "f2/t2.py": P1 + "# f2\n"}
        sha_a = _commit_state(criss, {}, state_a, "a")
        _fx(criss, "checkout", "-q", "-b", "b1")
        state_b = {"f1/deep/t1.py": state_a["f1/t1.py"], "f2/t2.py": state_a["f2/t2.py"]}
        sha_b = _commit_state(criss, state_a, state_b, "b")
        _fx(criss, "checkout", "-q", "-b", "b2", sha_a)
        state_c = {"f1/t1.py": state_a["f1/t1.py"], "f2/deep/t2.py": state_a["f2/t2.py"]}
        sha_c = _commit_state(criss, state_a, state_c, "c")
        _fx(criss, "checkout", "-q", "b1")
        _fx(criss, "merge", "-q", "--no-edit", "-m", "d", sha_c)
        sha_d = _fx(criss, "rev-parse", "HEAD")
        _fx(criss, "checkout", "-q", "b2")
        _fx(criss, "merge", "-q", "--no-edit", "-m", "e", sha_b)
        sha_e = _fx(criss, "rev-parse", "HEAD")
        bases_count = len(_merge_bases(Path(criss), sha_d, sha_e))
        got = _outcome(criss, sha_d, sha_e)
        check("merge/multiple-bases-combined",
              bases_count == 2 and _counts(got) == ("ok", 2, 2, 2, 0), (bases_count, got))

        # fixture 23: hops above the repository root are a distinct directory, and a finding
        body23 = "import pathlib\nROOT = pathlib.Path(__file__).parents[3]\n"
        above, above_b, above_h = _repo_with(
            base, "above", {"t.py": body23}, {"d/t.py": body23})
        got = _outcome(above, above_b, above_h)
        check("verdict/above-root-distinct-flagged",
              _counts(got) == ("ok", 1, 1, 1, 0)
              and got[1][0]["head_dir"].startswith(".."), got)

        # fixture 24: a base syntax error, a head null byte and deep nesting are cannot-evaluate
        bad_base, bb_b, bb_h = _repo_with(
            base, "bad-base", {"a/t.py": "def broken(:\n" + filler}, {"b/t.py": P1})
        check("parse/base-syntax-error-cannot-evaluate",
              _outcome(bad_base, bb_b, bb_h)[0] == "cannot", _outcome(bad_base, bb_b, bb_h))
        nul, nul_b, nul_h = _repo_with(
            base, "null-byte", {"a/t.py": P1 + filler}, {"b/t.py": b"x = 1\n\x00\n"})
        check("parse/head-null-byte-cannot-evaluate",
              _outcome(nul, nul_b, nul_h)[0] == "cannot", _outcome(nul, nul_b, nul_h))
        deep_body = "x = " + "(" * 100000 + "0" + ")" * 100000 + "\n"
        deep, deep_b, deep_h = _repo_with(
            base, "deep-nesting", {"a/t.py": P1 + filler}, {"b/t.py": deep_body})
        check("parse/deep-nesting-cannot-evaluate",
              _outcome(deep, deep_b, deep_h)[0] == "cannot", _outcome(deep, deep_b, deep_h)[0])

        # fixture 25: a latin-1 coding cookie parses; undecodable bytes are cannot-evaluate
        latin = (b"# -*- coding: latin-1 -*-\n# caf\xe9\nimport os.path\n"
                 b"ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n")
        lat, lat_b, lat_h = _repo_with(base, "latin", {"a/t.py": latin}, {"a/sub/t.py": latin})
        check("parse/latin1-cookie-parsed",
              _counts(_outcome(lat, lat_b, lat_h)) == ("ok", 1, 1, 1, 0),
              _outcome(lat, lat_b, lat_h))
        undec, undec_b, undec_h = _repo_with(
            base, "undecodable", {"a/t.py": P1 + filler}, {"b/t.py": b"# x\n\xff\xfe = 1\n"})
        check("parse/undecodable-cannot-evaluate",
              _outcome(undec, undec_b, undec_h)[0] == "cannot", _outcome(undec, undec_b, undec_h))

        # fixture 26: an unresolvable revision, no merge base, and a missing blob
        try:
            _resolve_commit(Path(seed), "no-such-ref")
            got = "resolved"
        except CannotEvaluate as exc:
            got = "cannot: {}".format(exc)
        check("resolve/unresolvable-revision-cannot-evaluate", got.startswith("cannot"), got)
        orphan = _init_repo(base, "orphan")
        o1 = _commit_state(orphan, {}, {"a.txt": "1\n"}, "main")
        _fx(orphan, "checkout", "-q", "--orphan", "lone")
        _fx(orphan, "rm", "-rfq", "--ignore-unmatch", ".")
        o2 = _commit_state(orphan, {}, {"b.txt": "2\n"}, "lone")
        check("resolve/no-merge-base-cannot-evaluate",
              _outcome(orphan, o1, o2)[0] == "cannot", _outcome(orphan, o1, o2))
        lost, lost_b, lost_h = _repo_with(base, "lost-blob", {"pkg/t.py": P1},
                                          {"pkg/sub/t.py": P1})
        blob_sha = _fx(lost, "rev-parse", lost_b + ":pkg/t.py")
        os.remove(Path(lost) / ".git" / "objects" / blob_sha[:2] / blob_sha[2:])
        check("read/missing-blob-cannot-evaluate",
              _outcome(lost, lost_b, lost_h)[0] == "cannot", _outcome(lost, lost_b, lost_h)[0])

        # fixture 27: a symlink or a submodule on one side of a pairing
        sym = _init_repo(base, "symlink")
        s1 = _commit_state(sym, {}, {"a/t.py": P1}, "base")
        (Path(sym) / "a" / "t.py").unlink()
        os.makedirs(Path(sym) / "b", exist_ok=True)
        os.symlink("../a", Path(sym) / "b" / "t.py")
        _fx(sym, "add", "-A")
        _fx(sym, "commit", "-q", "-m", "head")
        s2 = _fx(sym, "rev-parse", "HEAD")
        check("mode/symlink-side-cannot-evaluate",
              _outcome(sym, s1, s2)[0] == "cannot", _outcome(sym, s1, s2))
        sub = _init_repo(base, "gitlink")
        g1 = _commit_state(sub, {}, {"a/t.py": P1}, "base")
        _fx(sub, "rm", "-q", "a/t.py")
        _fx(sub, "update-index", "--add", "--cacheinfo", "160000,{},b/t.py".format(g1))
        _fx(sub, "commit", "-q", "-m", "head")
        g2 = _fx(sub, "rev-parse", "HEAD")
        check("mode/submodule-side-cannot-evaluate",
              _outcome(sub, g1, g2)[0] == "cannot", _outcome(sub, g1, g2))

        # fixture 28: a malformed record and an unrecognized status letter, via a patched runner
        with _patched(this, "_diff_tree_bytes", lambda _r, _m, _h: b"R90\0only-old.py\0"):
            got = _outcome(seed, seed_b, seed_h)
        check("enum/malformed-record-cannot-evaluate", got[0] == "cannot", got)
        with _patched(this, "_diff_tree_bytes", lambda _r, _m, _h: b"C75\0a.py\0b.py\0"):
            got = _outcome(seed, seed_b, seed_h)
        check("enum/unknown-status-cannot-evaluate", got[0] == "cannot", got)

        # launch vectors: the gate as an operator or a roster runs it
        got = _launch("--root", str(seed), "--base", seed_b, "--head", seed_h)
        check("launch/findings-exit-1",
              got[0] == 1 and "FINDING: pkg/tool.py -> pkg/sub/tool.py" in got[1]
              and "(2 hops)" in got[1], got)
        got = _launch("--root", str(fixed), "--base", fixed_b, "--head", fixed_h)
        check("launch/pass-exit-0-counts",
              got == (0, "base {}\nhead {}\nmoved Python files judged: 1; derivations "
                         "compared: 1\nno finding within scope\n".format(fixed_b, fixed_h), ""),
              got)
        moved_txt, mt_b, mt_h = _repo_with(
            base, "txt-only", {"a/n.txt": "1\n", "a/t.py": P1}, {"b/n.txt": "1\n", "a/t.py": P1})
        got = _launch("--root", str(moved_txt), "--base", mt_b, "--head", mt_h)
        check("launch/empty-scope-line",
              got[0] == 0 and "empty scope: this change moves no Python file" in got[1], got)
        got = _launch("--root", str(seed))
        check("launch/origin-head-missing-cannot-evaluate",
              got[0] == 2 and got[1] == "" and "origin/HEAD" in got[2], got)
        clone_dir = base / "clone"
        _fx(Path(base), "clone", "-q", str(fixed), str(clone_dir))
        got = _launch("--root", str(clone_dir))
        check("launch/default-base-merge-base",
              got[0] == 0 and "empty scope" in got[1], got)
        got = _launch("--frobnicate")
        check("launch/unknown-argument-usage", got[0] == 2 and "usage:" in got[2], got)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    if problem is not None:
        print("SELF-TEST HARNESS ERROR: {}".format(problem), file=sys.stderr)
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL: check_moved_python_roots {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_moved_python_roots {} checks over synthetic git fixtures (the "
          "seed shape, R100, wrapper, dot-dot, alias and bound-name derivations flagged; corrected "
          "hops, same-depth moves, one-hop derivations, redefined or quoted names, unmoved and new "
          "files and non-__file__ roots quiet, each quiet row with its flag-everything flip; "
          "unpaired adds matched by name against every same-named deletion; the marker clearing "
          "exactly its directory and lapsing on the next move; odd path bytes enumerated; -l0 "
          "pairing under diff.renameLimit=1; criss-cross merge bases combined; above-root hops "
          "distinct; parse, mode, blob, revision, merge-base and record failures each "
          "cannot-evaluate; and the launch contract); execution set reconciled against "
          "tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
