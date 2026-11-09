#!/usr/bin/env python3
"""Shell-function stand-in lint over the repository's own Python, first part: environment keys.

Run with: python3 -I -B tools/check_shell_function_standins.py [--root DIR]
          python3 -I -B tools/check_shell_function_standins.py --self-test
Exit: 0 clean within scope; 1 finding; 2 cannot-evaluate.

Rule text (tsthrm.10): where verification substitutes for an external executable, the substitute
is an executable fixture, never a shell function or alias. Bash imports an exported function from
an environment variable named BASH_FUNC_<name>%%, and before it runs a non-interactive script it
sources the file that BASH_ENV names (ENV for sh, and for bash in POSIX mode). Either key lets a
shell function stand in for an executable. The scan parses every *.py file under SCAN_ROOTS with
ast, never importing or running the file, and reports a CONSTANT string key stored into a mapping:

  env-func     a key that starts with BASH_FUNC_ or ends with %% (the exported-function form);
  env-startup  a key that is exactly BASH_ENV or ENV (a startup file the shell sources).

A store is one of these routes:

  dict-key          a key of a dict display ({"BASH_ENV": path});
  dict-comp-key     the key of a dict comprehension;
  keyword           a keyword argument of any call (dict(BASH_ENV=path), env.update(ENV=path));
  first-argument    the first positional argument of a method or function named setdefault,
                    putenv or __setitem__ (env.setdefault, os.putenv, os.environ.__setitem__);
  subscript-store   a subscript store target (env["BASH_ENV"] = path, an augmented or annotated
                    assignment, a for, with or unpacking target).

Removing or reading a key is not a store, so a filter that drops BASH_FUNC_ keys from an
inherited environment (a startswith test, a comparison, pop, del, get, os.unsetenv) is clean.

A reviewed site is admitted only by an ALLOWLIST entry (path, enclosing qualname, kind, exact
count, reason); an entry whose count no longer matches is itself a finding. The enclosing qualname
is the dotted chain of class and function names around the site, or <module>.

Fail closed: each file is decoded as Python decodes it (tokenize.detect_encoding, so a BOM and a
PEP 263 coding cookie are honoured) and a cookie other than utf-8 is cannot-evaluate. A missing
scan root, a directory the walk cannot list, any symlink inside a scanned root, a *.py entry that
is not a regular file, and a file the scan cannot read, decode or parse are cannot-evaluate
(exit 2), never a clean pass. Only the vendored opf/tools/_vendor tree is excluded.

Residuals (not detected by this part): a key built at run time (a name, an f-string, a
concatenation, a format call); a mapping built from pairs (dict([("BASH_ENV", p)])); a store
through setattr, operator or another indirection; an environment inherited from the caller (a
launch without env=, tsthrm.17); shell sources; and shell text a source launches (an export -f,
a declare -fx, or a function or alias named after an executable). Those last two are planned as
later parts of this lint.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_shell_function_standins.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import ast
import io
import os
import stat
import tempfile
import tokenize
from pathlib import Path

SCAN_ROOTS = ("tools", "opf/tools", ".aiqt/core/hooks/scripts")
EXCLUDED_DIRS = frozenset({"opf/tools/_vendor"})

KIND_RULES = {
    "env-func": ("tsthrm",),
    "env-startup": ("tsthrm",),
}
ROUTES = ("dict-key", "dict-comp-key", "keyword", "first-argument", "subscript-store")

FUNC_PREFIX = "BASH_FUNC_"
FUNC_SUFFIX = "%%"
STARTUP_KEYS = frozenset({"BASH_ENV", "ENV"})
FIRST_ARGUMENT_STORES = frozenset({"setdefault", "putenv", "__setitem__"})

# The reviewed allowlist: (path, enclosing qualname, kind, exact count, reason). Every entry names
# a current site and why it is accepted for now; a count that no longer matches the scan is a
# finding, so a new site in a reviewed function still needs its own review.
_CI_PARITY = ("removed by the CI parity executable-stub change; delete this entry when it merges")
ALLOWLIST = (
    ("tools/check_ci_parity.py", "_run_runner_copy", "env-startup", 1, _CI_PARITY),
    ("tools/check_ci_parity.py", "_dirname_guard_outcome", "env-startup", 1, _CI_PARITY),
    ("tools/check_ci_parity.py", "_cdpath_outcome", "env-startup", 1, _CI_PARITY),
)


class CannotEvaluate(Exception):
    """The scan cannot reach a verdict for the tree."""


def key_kind(text):
    """The finding kind for a constant key text, or None."""
    if text.startswith(FUNC_PREFIX) or text.endswith(FUNC_SUFFIX):
        return "env-func"
    if text in STARTUP_KEYS:
        return "env-startup"
    return None


def _constant_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, disabled):
        self.disabled = disabled
        self.stack = []
        self.findings = []

    def _record(self, node, text, route):
        if text is None or route in self.disabled:
            return
        kind = key_kind(text)
        if kind is not None:
            self.findings.append((node.lineno, node.col_offset, ".".join(self.stack) or "<module>",
                                  kind, route, text))

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

    def visit_Dict(self, node):
        for key in node.keys:
            if key is not None:
                self._record(key, _constant_text(key), "dict-key")
        self.generic_visit(node)

    def visit_DictComp(self, node):
        self._record(node.key, _constant_text(node.key), "dict-comp-key")
        self.generic_visit(node)

    def visit_Call(self, node):
        for keyword in node.keywords:
            if keyword.arg is not None:
                self._record(keyword, keyword.arg, "keyword")
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in FIRST_ARGUMENT_STORES and node.args:
            self._record(node.args[0], _constant_text(node.args[0]), "first-argument")
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if isinstance(node.ctx, ast.Store):
            self._record(node.slice, _constant_text(node.slice), "subscript-store")
        self.generic_visit(node)


def scan_source(source, filename="<source>", disabled=()):
    """Return the sorted findings (line, column, qualname, kind, route, key) for one source text;
    SyntaxError, ValueError, RecursionError and MemoryError propagate."""
    tree = ast.parse(source, filename=filename)
    scanner = _Scanner(frozenset(disabled))
    scanner.visit(tree)
    return sorted(set(scanner.findings))


def _read_bytes(path):
    return Path(path).read_bytes()


def _decode(data, rel):
    """The source bytes exactly as Python compiles them; a declared encoding other than utf-8
    is cannot-evaluate, and UnicodeDecodeError propagates as unreadable."""
    try:
        encoding, _lines = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as exc:
        raise CannotEvaluate("cannot detect the source encoding of " + rel + ": " + str(exc))
    if encoding not in ("utf-8", "utf-8-sig"):
        raise CannotEvaluate(rel + " declares source encoding " + encoding
                             + "; only utf-8 is evaluated")
    data.decode(encoding)
    return data


def _python_files(root):
    def rel(path):
        return Path(path).relative_to(root).as_posix()

    def linked(path):
        try:
            mode = os.lstat(path).st_mode
        except OSError as exc:
            raise CannotEvaluate("cannot stat " + rel(path) + ": " + str(exc))
        if stat.S_ISLNK(mode):
            raise CannotEvaluate("symlink inside a scanned root: " + rel(path))
        return False

    def walk_error(exc):
        raise CannotEvaluate("cannot list a directory inside a scanned root: " + str(exc))

    files = []
    for sub in SCAN_ROOTS:
        base = root / sub
        try:
            mode = os.lstat(base).st_mode
        except OSError as exc:
            raise CannotEvaluate("scan root missing: " + sub + ": " + str(exc))
        if not stat.S_ISDIR(mode):
            raise CannotEvaluate("scan root is not a directory: " + sub)
        for current, dirnames, filenames in os.walk(base, onerror=walk_error, followlinks=False):
            rel_dir = rel(current)
            dirnames[:] = [d for d in sorted(dirnames) if (rel_dir + "/" + d) not in EXCLUDED_DIRS
                           and not linked(Path(current) / d)]
            for name in sorted(filenames):
                if not linked(Path(current) / name) and name.endswith(".py"):
                    files.append(Path(current) / name)
    return files


def _check_allowlist(allowlist):
    if not isinstance(allowlist, tuple):
        raise CannotEvaluate("the allowlist is not a tuple: " + type(allowlist).__name__)
    for entry in allowlist:
        if not (isinstance(entry, tuple) and len(entry) == 5
                and all(isinstance(entry[i], str) and entry[i].strip() for i in (0, 1, 2, 4))
                and entry[2] in KIND_RULES and type(entry[3]) is int and entry[3] >= 1):
            raise CannotEvaluate("malformed allowlist entry: " + repr(entry))
    keys = [entry[:3] for entry in allowlist]
    if len(set(keys)) != len(keys):
        raise CannotEvaluate("duplicate allowlist key")


def scan_tree(root, allowlist=ALLOWLIST, reader=_read_bytes):
    """Return the finding lines for the tree under root; raise CannotEvaluate when it cannot."""
    root = Path(root)
    _check_allowlist(allowlist)
    hits = []
    try:
        paths = _python_files(root)
    except OSError as exc:
        raise CannotEvaluate("cannot walk the scan roots: " + str(exc))
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            regular = stat.S_ISREG(os.lstat(path).st_mode)
        except OSError:
            regular = False
        if not regular:
            raise CannotEvaluate("not a readable regular file: " + rel)
        try:
            source = _decode(reader(path), rel)
        except (OSError, UnicodeDecodeError) as exc:
            raise CannotEvaluate("cannot read " + rel + ": " + str(exc))
        try:
            found = scan_source(source, rel)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            raise CannotEvaluate("cannot parse " + rel + ": " + type(exc).__name__ + " " + str(exc))
        hits.extend((rel,) + item for item in found)
    counts = {}
    for rel, _line, _column, qual, kind, _route, _key in hits:
        counts[(rel, qual, kind)] = counts.get((rel, qual, kind), 0) + 1
    allowed = {entry[:3]: entry[3] for entry in allowlist}
    findings = []
    for rel, line, _column, qual, kind, route, key in hits:
        if allowed.get((rel, qual, kind)) == counts[(rel, qual, kind)]:
            continue
        findings.append("{}:{}: [{} {}] {}: key {!r} stored ({})".format(
            rel, line, kind, "/".join(KIND_RULES[kind]), qual, key, route))
    for key, expected in sorted(allowed.items()):
        if counts.get(key, 0) != expected:
            findings.append("allowlist entry {} {} {} expects {} site(s), scan found {}"
                            .format(key[0], key[1], key[2], expected, counts.get(key, 0)))
    return findings


def run(root):
    try:
        findings = scan_tree(root)
    except CannotEvaluate as exc:
        print("ERROR: shell-function-standins cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    if findings:
        for line in findings:
            print("VIOLATION: shell-function-standins: " + line, file=sys.stderr)
        return 1
    print("PASS: shell-function-standins: no unreviewed constant BASH_FUNC_, %%-suffixed, BASH_ENV"
          " or ENV key stored into a mapping in the scanned roots; see residuals.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Shell-function stand-in lint (environment keys) over this repo's Python.")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.self_test:
        if args.root is not None:
            parser.print_usage(sys.stderr)
            return 2
        return self_test()
    root = args.root if args.root is not None else Path(__file__).resolve().parent.parent
    return run(root)


# Self-test rows, numbered as in the plan's table: (row, source, expected kind, flip source). The
# row must report its kind and the flip must be clean.
ROWS = (
    ("1 env/bash-func-key", 'env = {"BASH_FUNC_python3%%": "() { :; }"}\n', "env-func",
     'import os\nenv = {k: v for k, v in os.environ.items() if not k.startswith("BASH_FUNC_")}\n'),
    ("2 env/subscript-store", 'env = {}\nenv["BASH_FUNC_git%%"] = "() { :; }"\n', "env-func",
     'env = {}\nenv.pop("BASH_FUNC_git%%", None)\n'),
    ("3 env/percent-suffix", 'env = {"python3%%": "x"}\n', "env-func",
     'env = {"python3%": "x"}\n'),
    ("4 env/bash-env", 'env = dict(BASH_ENV=f)\n', "env-startup",
     'env = dict(HOME=t)\n'),
)

# Each positive vector is reported by its route alone: with that route disabled it is clean.
POSITIVE_VECTORS = (
    ('env = {"BASH_FUNC_": f}\n', "env-func", "dict-key"),
    ('env = {"BASH_FUNC_ls": f}\n', "env-func", "dict-key"),
    ('env = {"x%%%": f}\n', "env-func", "dict-key"),
    ('env = {"x%%": f}\n', "env-func", "dict-key"),
    ('env = {"ENV": f}\n', "env-startup", "dict-key"),
    ('env = {"BASH_ENV": p for p in ps}\n', "env-startup", "dict-comp-key"),
    ('env.update(BASH_ENV=f)\n', "env-startup", "keyword"),
    ('run(cmd, ENV=f)\n', "env-startup", "keyword"),
    ('env.setdefault("BASH_ENV", f)\n', "env-startup", "first-argument"),
    ('import os\nos.putenv("BASH_FUNC_ls%%", f)\n', "env-func", "first-argument"),
    ('import os\nos.environ.__setitem__("ENV", f)\n', "env-startup", "first-argument"),
    ('env["ENV"] = f\n', "env-startup", "subscript-store"),
    ('import os\nos.environ["BASH_ENV"] = f\n', "env-startup", "subscript-store"),
    ('env["BASH_FUNC_x%%"] += f\n', "env-func", "subscript-store"),
    ('env["BASH_ENV"]: str = f\n', "env-startup", "subscript-store"),
    ('env["ENV"], x = f, 1\n', "env-startup", "subscript-store"),
    ('for env["BASH_ENV"] in paths:\n    pass\n', "env-startup", "subscript-store"),
)

# Boundary and removal vectors that must stay clean.
NEGATIVE_VECTORS = (
    'env = {"BASH_FUNC": f}\n',
    'env = {"BASH_FUNCx_ls": f}\n',
    'env = {"x%": f}\n',
    'env = {"x%%y": f}\n',
    'env = {"BASH_ENVX": f}\n',
    'env = {"ENV_FILE": f}\n',
    'env = {"env": f}\n',
    'env = {"k": "BASH_ENV"}\n',
    'env = dict(HOME=t, PATH=p)\n',
    'del env["BASH_ENV"]\n',
    'env.pop("ENV")\n',
    'x = env["BASH_ENV"]\n',
    'x = env.get("BASH_FUNC_ls%%")\n',
    'ok = "BASH_ENV" in env\n',
    'import os\nos.unsetenv("BASH_ENV")\n',
    'name = "BASH_ENV"\n',
    'env[key] = f\n',
    'env.setdefault(key, "BASH_ENV")\n',
    'env = {k: v for k, v in pairs if k != "BASH_ENV"}\n',
)

_REASON = "self-test reason"
_FUNC_SITE = ROWS[0][1].encode("utf-8")


def _scan_outcome(root, allowlist, reader):
    try:
        findings = scan_tree(root, allowlist, reader)
    except CannotEvaluate:
        return 2
    return 1 if findings else 0


def _tree_outcome(files, allowlist=(), reader=_read_bytes, roots=SCAN_ROOTS, setup=None):
    """Build a scratch tree from {rel: bytes} and return its exit code."""
    with tempfile.TemporaryDirectory(prefix="shfn-standins-") as tmp:
        root = Path(tmp)
        for sub in roots:
            (root / sub).mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if setup is not None:
            setup(root)
        return _scan_outcome(root, allowlist, reader)


def _refusing_reader(path):
    if Path(path).name == "a.py":
        raise PermissionError(13, "Permission denied (self-test reader)", str(path))
    return _read_bytes(path)


def _link_a(root):
    os.symlink("b.py", root / "tools" / "a.py")


def _fifo_a(root):
    """A FIFO named a.py: the scan refuses it by its mode and never opens it."""
    os.mkfifo(root / "tools" / "a.py")


# Row 12 and the fail-closed and allowlist tree vectors: (name, kwargs, expected, flip kwargs,
# flip expected). The flip must change the outcome.
_VALID = {"tools/a.py": b"x = 1\n"}
TREE_VECTORS = (
    ("12 errors/unparseable", dict(files={"tools/a.py": b"def (:\n"}), 2,
     dict(files=_VALID), 0),
    ("12 errors/unreadable", dict(files=_VALID, reader=_refusing_reader), 2,
     dict(files=_VALID), 0),
    ("errors/undecodable", dict(files={"tools/a.py": b"x = '\xff'\n"}), 2,
     dict(files=_VALID), 0),
    ("errors/encoding-cookie", dict(files={"tools/a.py": b"# coding: latin-1\nx = 1\n"}), 2,
     dict(files={"tools/a.py": b"# coding: utf-8\nx = 1\n"}), 0),
    ("errors/nonregular", dict(files={}, setup=_fifo_a), 2,
     dict(files=_VALID), 0),
    ("errors/symlink", dict(files={"tools/b.py": b"x = 1\n"}, setup=_link_a), 2,
     dict(files={"tools/b.py": b"x = 1\n"}), 0),
    ("errors/missing-root", dict(files=_VALID, roots=("tools", "opf/tools")), 2,
     dict(files=_VALID), 0),
    ("tree/finding", dict(files={"opf/tools/a.py": _FUNC_SITE}), 1,
     dict(files={"opf/tools/a.py": ROWS[0][3].encode("utf-8")}), 0),
    ("tree/hooks-root", dict(files={".aiqt/core/hooks/scripts/a.py": _FUNC_SITE}), 1,
     dict(files={".aiqt/core/hooks/scripts/a.txt": _FUNC_SITE}), 0),
    ("tree/vendored", dict(files={"tools/_vendor/a.py": _FUNC_SITE}), 1,
     dict(files={"opf/tools/_vendor/a.py": _FUNC_SITE}), 0),
    ("allow/admitted", dict(files={"tools/a.py": _FUNC_SITE}), 1,
     dict(files={"tools/a.py": _FUNC_SITE},
          allowlist=(("tools/a.py", "<module>", "env-func", 1, _REASON),)), 0),
    ("allow/stale-count", dict(files={"tools/a.py": _FUNC_SITE},
                               allowlist=(("tools/a.py", "<module>", "env-func", 2, _REASON),)), 1,
     dict(files={"tools/a.py": _FUNC_SITE},
          allowlist=(("tools/a.py", "<module>", "env-func", 1, _REASON),)), 0),
    ("allow/other-kind", dict(files={"tools/a.py": _FUNC_SITE},
                              allowlist=(("tools/a.py", "<module>", "env-startup", 1, _REASON),)),
     1, dict(files={"tools/a.py": _FUNC_SITE},
             allowlist=(("tools/a.py", "<module>", "env-func", 1, _REASON),)), 0),
    ("allow/qualname", dict(files={"tools/a.py": b"class C:\n    def f(self):\n"
                                                b"        return dict(BASH_ENV=1)\n"},
                            allowlist=(("tools/a.py", "f", "env-startup", 1, _REASON),)), 1,
     dict(files={"tools/a.py": b"class C:\n    def f(self):\n        return dict(BASH_ENV=1)\n"},
          allowlist=(("tools/a.py", "C.f", "env-startup", 1, _REASON),)), 0),
    ("allow/malformed", dict(files=_VALID, allowlist=(("tools/a.py", "<module>", "env-func", 0,
                                                       _REASON),)), 2,
     dict(files=_VALID), 0),
)


def self_test():
    failures = []
    for row, source, kind, flip in ROWS:
        got = scan_source(source)
        if [item[3] for item in got] != [kind]:
            failures.append("row " + row + ": expected one " + kind + " finding, got " + repr(got))
        got = scan_source(flip)
        if got:
            failures.append("row " + row + " flip: expected clean, got " + repr(got))
    exercised = set()
    for source, kind, route in POSITIVE_VECTORS:
        exercised.add(route)
        got = scan_source(source)
        if [(item[3], item[4]) for item in got] != [(kind, route)]:
            failures.append("positive " + repr(source) + ": expected " + kind + " by " + route
                            + ", got " + repr(got))
        if scan_source(source, disabled=(route,)):
            failures.append("positive " + repr(source) + " still reported without " + route)
    for source in NEGATIVE_VECTORS:
        got = scan_source(source)
        if got:
            failures.append("negative flagged: " + repr(source) + " -> " + repr(got))
    unexercised = sorted(set(ROUTES) - exercised)
    if unexercised:
        failures.append("routes without a vector: " + ", ".join(unexercised))
    for name, case, expected, flip, flip_expected in TREE_VECTORS:
        got = _tree_outcome(**case)
        if got != expected:
            failures.append("tree " + name + ": expected " + str(expected) + ", got " + str(got))
        got = _tree_outcome(**flip)
        if got != flip_expected or got == expected:
            failures.append("tree " + name + " flip: expected " + str(flip_expected) + ", got "
                            + str(got))
    try:
        _check_allowlist(ALLOWLIST)
    except CannotEvaluate as exc:
        failures.append("shipped allowlist malformed: " + str(exc))
    if failures:
        print("SELF-TEST FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("SELF-TEST PASS: shell-function-standins: {} table rows and their flips, {} positive"
          " vectors (each clean without its route), {} negatives, {} fail-closed and allowlist"
          " tree vectors and their flips.".format(
              len(ROWS), len(POSITIVE_VECTORS), len(NEGATIVE_VECTORS), len(TREE_VECTORS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
