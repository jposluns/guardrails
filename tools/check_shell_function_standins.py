#!/usr/bin/env python3
"""Shell-function stand-in lint over the repository's own Python, first part: environment keys.

Run with: python3 -I -B tools/check_shell_function_standins.py [--root DIR]
          python3 -I -B tools/check_shell_function_standins.py --self-test
Exit: 0 clean within scope; 1 finding; 2 cannot-evaluate.

Rule text (tsthrm.10): where verification substitutes for an external executable, the substitute
is an executable fixture, never a shell function or alias. Bash imports an exported function from
an environment variable named BASH_FUNC_<name>%%, and before it runs a non-interactive script it
sources the file that BASH_ENV names. sh and bash read ENV only for an interactive shell; that key
is listed as a precaution. Either key lets a shell function stand in for an executable. The scan
parses every *.py file under SCAN_ROOTS with ast, never importing or running the file, and reports
a CONSTANT str or bytes key stored into a mapping that reaches an environment:

  env-func     a key that starts with BASH_FUNC_ or ends with %% (the exported-function form);
  env-startup  a key that is exactly BASH_ENV or ENV (a startup file the shell sources).

A mapping reaches an environment when it is the target: os.environ, os.environb, or any name or
attribute whose last component contains "env" in any case (env, child_env, self.env), assigned
(plainly, annotated or by :=), updated (update, setdefault, __setitem__, |=, a subscript store),
or when it is the value of an env= keyword argument of any call, or the mapping or keywords of a
call named dict on an attribute (mock.patch.dict, patch.dict, also as a decorator) whose target,
given as the first argument or as in_dict=, is a target or the string "os.environ" or
"os.environb"; its mapping is the second argument or values=. The key of os.putenv or of a
setenv call (monkeypatch.setenv), given as the first argument or as name=, is a store too. Both
branches of a conditional expression, every value of an or/and and the value of a := given as
such a mapping are read as the mapping. Inside such a mapping a store is one of these routes:

  dict-key          a key of a dict display, also inside a ** entry, a dict(...) argument, a
                    union (os.environ | {...}), a ChainMap(...) argument or the mapping of
                    patch.dict (patch.dict(os.environ, {...}), patch.dict(os.environ,
                    values={...}));
  dict-comp-key     the key of a dict comprehension;
  keyword           a keyword argument of dict(...), of the target's update(...) or of
                    patch.dict(...) other than its own in_dict, values and clear parameters
                    (dict(BASH_ENV=path), os.environ.update(ENV=path),
                    patch.dict(in_dict=os.environ, BASH_ENV=path));
  pair              the key of a literal (KEY, v) pair in a literal list or tuple given to
                    dict(...), update(...), |= or as the mapping of patch.dict(...);
  fromkeys          a literal key listed in fromkeys([KEY], v);
  first-argument    the key of os.putenv, of setenv (also setenv(name=KEY, value=v)) or of the
                    target's setdefault or __setitem__;
  second-argument   the key of an unbound call whose first argument is the target
                    (dict.setdefault(env, KEY, v), dict.__setitem__(env, KEY, v));
  subscript-store   a subscript store into the target (env["BASH_ENV"] = path, an augmented or
                    annotated assignment, a for, with or unpacking target).

Removing or reading a key is not a store, so a filter that drops BASH_FUNC_ keys from an
inherited environment (a startswith test, a comparison, pop, del, get, os.unsetenv) is clean. A
mapping that never reaches a target is clean too: a lookup table, a membership test, an
expected-value literal, a str.format or Template keyword, a counter and a config dict.

A reviewed site is admitted only by an ALLOWLIST entry (path, enclosing qualname, kind, exact
count, reason); an entry whose count no longer matches is itself a finding. The enclosing qualname
is the dotted chain of class and function names around the site, or <module>.

Fail closed: each file is decoded as Python decodes it (tokenize.detect_encoding, so a BOM and a
PEP 263 coding cookie are honoured) and a cookie other than utf-8 is cannot-evaluate. A missing
scan root, a directory the walk cannot list, any symlink inside a scanned root, a *.py entry that
is not a regular file, and a file the scan cannot read, decode or parse are cannot-evaluate
(exit 2), never a clean pass. Only the vendored opf/tools/_vendor tree is excluded.

Residuals (not detected by this part, a best-effort lexical check): a key held in a name
(KEY = "BASH_ENV"; env[KEY] = p); an env built by a helper (env=mk("BASH_ENV", p)) or a mapping
built under another name and merged in later; pairs not written as literals (zip, a generator, a
list held in a name, a list built from environment items with literal pairs appended, as in
dict(list(os.environ.items()) + [("BASH_ENV", p)]), and .items() passed to update, as in
env.update({"BASH_ENV": p}.items())); a key built at run time (an f-string, a concatenation, a
format call); a store through operator.setitem, monkeypatch.setitem, setattr or another
indirection; a mapping loaded from data (json.loads); an environment passed positionally
(os.execve) or held in a name without "env", including run keyword arguments held in a dict
(kw = {"env": {...}} or kw["env"] = {...}, then run(c, **kw)) and a tuple target
(env, x = {"BASH_ENV": p}, 1); a patch.dict target given as any other string; a patch.dict
target or mapping, or a setenv name, passed through a ** mapping
(patch.dict(**{"in_dict": os.environ, "values": m}), setenv(**{"name": "BASH_ENV", ...})); an
environment inherited from the caller (a launch without env=, tsthrm.17); shell sources; and
shell text a source launches (an export -f, a declare -fx, or a function or alias named after an
executable). Those last two are planned as later parts of this lint. A name that contains "env"
but holds no environment (environment_defaults) is still treated as a target.
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
ROUTES = ("dict-key", "dict-comp-key", "keyword", "pair", "fromkeys", "first-argument",
          "second-argument", "subscript-store")

FUNC_PREFIX = "BASH_FUNC_"
FUNC_SUFFIX = "%%"
STARTUP_KEYS = frozenset({"BASH_ENV", "ENV"})
KEY_METHODS = frozenset({"setdefault", "__setitem__"})
# Calls whose first argument, or name= keyword, is a key set in the process environment
# (os.putenv, monkeypatch.setenv(name, value)).
KEY_SETTERS = frozenset({"putenv", "setenv"})
KEY_SETTER_KEYWORD = "name"
# The import-path strings mock.patch.dict accepts for the process environment.
ENVIRON_PATHS = frozenset({"os.environ", "os.environb"})
# mock.patch.dict(in_dict, values=(), clear=False, **kwargs): its own parameters, which are never
# environment keys; only the remaining keywords are keys set in the target.
PATCH_DICT_TARGET = "in_dict"
PATCH_DICT_VALUES = "values"
PATCH_DICT_PARAMS = frozenset({PATCH_DICT_TARGET, PATCH_DICT_VALUES, "clear"})

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
    """The text of a str or bytes constant (bytes read as latin-1, so b"BASH_ENV" matches), or
    None."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
    return None


def _env_named(node):
    """Whether node is a name or attribute whose last component contains env in any case
    (os.environ, os.environb, env, child_env, self.env)."""
    if isinstance(node, ast.Name):
        return "env" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "env" in node.attr.lower()
    return False


def _patched_environment(node):
    """Whether node, the first argument of a patch.dict call, names an environment: a target
    (os.environ, os.environb, a name containing env) or the string "os.environ" or
    "os.environb"."""
    return _env_named(node) or _constant_text(node) in ENVIRON_PATHS


def _keyword_value(keywords, name):
    """The value of the keyword argument called name, or None."""
    for keyword in keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _call_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _mapping_keys(expr, pairs):
    """Yield (node, text, route) for each key the mapping expression expr stores. pairs admits a
    literal list or tuple of literal (KEY, v) pairs at the top. Both branches of a conditional
    expression, every value of an or/and and the value of a := stand for the mapping itself.
    Iterative, so a long union chain does not recurse."""
    todo = [(expr, pairs)]
    while todo:
        node, pairs_ok = todo.pop()
        if isinstance(node, ast.IfExp):
            todo.append((node.body, pairs_ok))
            todo.append((node.orelse, pairs_ok))
        elif isinstance(node, ast.BoolOp):
            todo.extend((value, pairs_ok) for value in node.values)
        elif isinstance(node, ast.NamedExpr):
            todo.append((node.value, pairs_ok))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    todo.append((value, False))
                else:
                    yield key, _constant_text(key), "dict-key"
        elif isinstance(node, ast.DictComp):
            yield node.key, _constant_text(node.key), "dict-comp-key"
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            todo.append((node.left, False))
            todo.append((node.right, False))
        elif isinstance(node, (ast.List, ast.Tuple)) and pairs_ok:
            for item in node.elts:
                if isinstance(item, (ast.Tuple, ast.List)) and len(item.elts) == 2:
                    yield item.elts[0], _constant_text(item.elts[0]), "pair"
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name == "dict" and isinstance(node.func, ast.Name):
                if node.args:
                    todo.append((node.args[0], True))
                for keyword in node.keywords:
                    if keyword.arg is None:
                        todo.append((keyword.value, False))
                    else:
                        yield keyword, keyword.arg, "keyword"
            elif name == "ChainMap":
                todo.extend((arg, False) for arg in node.args)
            elif (name == "fromkeys" and isinstance(node.func, ast.Attribute) and node.args
                  and isinstance(node.args[0], (ast.List, ast.Tuple, ast.Set))):
                for item in node.args[0].elts:
                    yield item, _constant_text(item), "fromkeys"


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class _Scanner:
    def __init__(self, disabled):
        self.disabled = disabled
        self.qualname = "<module>"
        self.findings = []

    def _record(self, node, text, route):
        if text is None or route in self.disabled:
            return
        kind = key_kind(text)
        if kind is not None:
            self.findings.append((node.lineno, node.col_offset, self.qualname, kind, route, text))

    def _mapping(self, expr, pairs):
        for key, text, route in _mapping_keys(expr, pairs):
            self._record(key, text, route)

    def _keywords(self, keywords, params=frozenset()):
        """Record each keyword argument as a key, except the call's own parameters in params."""
        for keyword in keywords:
            if keyword.arg is None:
                self._mapping(keyword.value, False)
            elif keyword.arg not in params:
                self._record(keyword, keyword.arg, "keyword")

    def scan(self, tree):
        """Walk tree with an explicit stack, never recursing, so a long chain of binary operators
        cannot exhaust the interpreter's recursion limit."""
        todo = [(tree, ())]
        while todo:
            node, scope = todo.pop()
            self.qualname = ".".join(scope) or "<module>"
            self._visit(node)
            if isinstance(node, _SCOPES):
                scope = scope + (node.name,)
            todo.extend((child, scope) for child in ast.iter_child_nodes(node))

    def _visit(self, node):
        if isinstance(node, ast.Subscript):
            if isinstance(node.ctx, ast.Store) and _env_named(node.value):
                self._record(node.slice, _constant_text(node.slice), "subscript-store")
        elif isinstance(node, ast.Assign):
            if any(_env_named(target) for target in node.targets):
                self._mapping(node.value, False)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            if node.value is not None and _env_named(node.target):
                self._mapping(node.value, False)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.op, ast.BitOr) and _env_named(node.target):
                self._mapping(node.value, True)
        elif isinstance(node, ast.Call):
            self._call(node)

    def _call(self, node):
        for keyword in node.keywords:
            if keyword.arg == "env":
                self._mapping(keyword.value, False)
        func = node.func
        name = _call_name(func)
        if name in KEY_SETTERS:
            key = node.args[0] if node.args else _keyword_value(node.keywords, KEY_SETTER_KEYWORD)
            if key is not None:
                self._record(key, _constant_text(key), "first-argument")
            return
        if name == "dict" and isinstance(func, ast.Attribute):
            # mock.patch.dict(os.environ, {...}, KEY=v) sets the keys like update, in call,
            # with and decorator form (a decorator is a Call node the walk reaches). The target
            # may be given as in_dict= and the mapping as values=.
            target = node.args[0] if node.args else _keyword_value(node.keywords,
                                                                   PATCH_DICT_TARGET)
            if target is not None and _patched_environment(target):
                if len(node.args) > 1:
                    self._mapping(node.args[1], True)
                values = _keyword_value(node.keywords, PATCH_DICT_VALUES)
                if values is not None:
                    self._mapping(values, True)
                self._keywords(node.keywords, PATCH_DICT_PARAMS)
            return
        if not isinstance(func, ast.Attribute) or (name != "update" and name not in KEY_METHODS):
            return
        if _env_named(func.value):
            args, route = node.args, "first-argument"
        elif node.args and _env_named(node.args[0]):
            args, route = node.args[1:], "second-argument"
        else:
            return
        if name == "update":
            if args:
                self._mapping(args[0], True)
            self._keywords(node.keywords)
        elif args:
            self._record(args[0], _constant_text(args[0]), route)


def scan_source(source, filename="<source>", disabled=()):
    """Return the sorted findings (line, column, qualname, kind, route, key) for one source text;
    SyntaxError, ValueError, RecursionError and MemoryError propagate."""
    tree = ast.parse(source, filename=filename)
    scanner = _Scanner(frozenset(disabled))
    scanner.scan(tree)
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
          " or ENV key stored into a mapping that reaches an environment in the scanned roots;"
          " see residuals.")
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
    ('run(cmd, env=dict(ENV=f))\n', "env-startup", "keyword"),
    ('env.setdefault("BASH_ENV", f)\n', "env-startup", "first-argument"),
    ('import os\nos.putenv("BASH_FUNC_ls%%", f)\n', "env-func", "first-argument"),
    ('import os\nos.environ.__setitem__("ENV", f)\n', "env-startup", "first-argument"),
    ('env["ENV"] = f\n', "env-startup", "subscript-store"),
    ('import os\nos.environ["BASH_ENV"] = f\n', "env-startup", "subscript-store"),
    ('env["BASH_FUNC_x%%"] += f\n', "env-func", "subscript-store"),
    ('env["BASH_ENV"]: str = f\n', "env-startup", "subscript-store"),
    ('env["ENV"], x = f, 1\n', "env-startup", "subscript-store"),
    ('for env["BASH_ENV"] in paths:\n    pass\n', "env-startup", "subscript-store"),
    # bytes keys
    ('import os\nos.environb[b"BASH_ENV"] = b"/x"\n', "env-startup", "subscript-store"),
    ('env = {b"BASH_FUNC_ls%%": b"() { :; }"}\n', "env-func", "dict-key"),
    ('import os\nos.putenv(b"BASH_ENV", b"/x")\n', "env-startup", "first-argument"),
    ('import os\nos.environb.update({b"BASH_FUNC_x%%": f})\n', "env-func", "dict-key"),
    # unbound methods, fromkeys and literal pairs
    ('dict.__setitem__(env, "BASH_ENV", p)\n', "env-startup", "second-argument"),
    ('import os\ndict.setdefault(os.environ, "ENV", p)\n', "env-startup", "second-argument"),
    ('import collections.abc, os\ncollections.abc.MutableMapping.update(os.environ, BASH_ENV=p)\n',
     "env-startup", "keyword"),
    ('dict.update(env, [("BASH_FUNC_ls%%", f)])\n', "env-func", "pair"),
    ('env = dict.fromkeys(["BASH_ENV"], p)\n', "env-startup", "fromkeys"),
    ('import os\nos.environ.update([("BASH_ENV", p)])\n', "env-startup", "pair"),
    ('env.update((("ENV", p),))\n', "env-startup", "pair"),
    ('env = dict([("BASH_ENV", p)])\n', "env-startup", "pair"),
    ('env |= [("BASH_FUNC_ls%%", f)]\n', "env-func", "pair"),
    # the contexts that reach an environment
    ('import os, subprocess\nsubprocess.run(c, env={**os.environ, "BASH_ENV": p})\n',
     "env-startup", "dict-key"),
    ('import os\nrun(c, env=dict(os.environ, **{"ENV": p}))\n', "env-startup", "dict-key"),
    ('import os\nrun(c, env=os.environ | {"BASH_ENV": p})\n', "env-startup", "dict-key"),
    ('import os\nfrom collections import ChainMap\nrun(c, env=ChainMap({"ENV": p}, os.environ))\n',
     "env-startup", "dict-key"),
    ('import os\nos.environ |= {"BASH_ENV": p}\n', "env-startup", "dict-key"),
    ('self.child_env = {"BASH_FUNC_ls%%": f}\n', "env-func", "dict-key"),
    ('childEnv: dict = {"ENV": p}\n', "env-startup", "dict-key"),
    ('if (env := {"BASH_ENV": p}):\n    pass\n', "env-startup", "dict-key"),
    # a conditional, an or/and and a := given as the mapping
    ('import os, subprocess\n'
     'subprocess.run(c, env={**os.environ, "BASH_ENV": p} if p else None)\n',
     "env-startup", "dict-key"),
    ('import os\nenv = {**os.environ, "BASH_ENV": p} if p else None\n', "env-startup", "dict-key"),
    ('import os\nenv = None if p is None else {**os.environ, "ENV": p}\n', "env-startup",
     "dict-key"),
    ('import os\nrun(c, env={**os.environ, **({"BASH_ENV": p} if p else {})})\n', "env-startup",
     "dict-key"),
    ('import os\nrun(c, env=os.environ | ({"ENV": p} if p else {}))\n', "env-startup", "dict-key"),
    ('env.update({"BASH_ENV": p} if p else {})\n', "env-startup", "dict-key"),
    ('env.update([("BASH_FUNC_ls%%", f)] if f else [])\n', "env-func", "pair"),
    ('run(c, env=extra or {"BASH_ENV": p})\n', "env-startup", "dict-key"),
    ('env = base and {"ENV": p}\n', "env-startup", "dict-key"),
    ('run(c, env=(e := {"BASH_ENV": p}))\n', "env-startup", "dict-key"),
    ('env.update((extra := {"BASH_FUNC_ls%%": f}))\n', "env-func", "dict-key"),
    # patch.dict on the environment, called, as a context manager and as a decorator
    ('import os\nfrom unittest import mock\nwith mock.patch.dict(os.environ, BASH_ENV=p):\n'
     '    pass\n', "env-startup", "keyword"),
    ('import os\nfrom unittest import mock\nmock.patch.dict(os.environ, {"BASH_FUNC_ls%%": f})'
     '.start()\n', "env-func", "dict-key"),
    ('import os\nfrom unittest.mock import patch\npatch.dict(os.environb, {b"ENV": p}).start()\n',
     "env-startup", "dict-key"),
    ('from unittest import mock\n@mock.patch.dict("os.environ", {"BASH_ENV": p})\n'
     'def test():\n    pass\n', "env-startup", "dict-key"),
    ('from unittest import mock\n@mock.patch.dict("os.environ", ENV=p)\ndef test():\n    pass\n',
     "env-startup", "keyword"),
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(os.environ, [("BASH_ENV", p)]).start()\n', "env-startup", "pair"),
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(os.environ, {"BASH_ENV": p} if p else {}, clear=True).start()\n',
     "env-startup", "dict-key"),
    # setenv like os.putenv
    ('def test(monkeypatch):\n    monkeypatch.setenv("BASH_ENV", p)\n', "env-startup",
     "first-argument"),
    ('def test(monkeypatch):\n    monkeypatch.setenv("BASH_FUNC_ls%%", f)\n', "env-func",
     "first-argument"),
    # patch.dict's target given as in_dict= and its mapping as values=, and setenv's name=
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(os.environ, values={"BASH_ENV": p}).start()\n', "env-startup", "dict-key"),
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(os.environ, values=[("BASH_FUNC_ls%%", f)]).start()\n', "env-func", "pair"),
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(in_dict=os.environ, BASH_ENV=p).start()\n', "env-startup", "keyword"),
    ('from unittest import mock\n@mock.patch.dict("os.environ", values={"BASH_ENV": p})\n'
     'def test():\n    pass\n', "env-startup", "dict-key"),
    ('import os\nfrom unittest import mock\n'
     'with mock.patch.dict(in_dict=os.environb, values={b"ENV": p}, clear=True):\n    pass\n',
     "env-startup", "dict-key"),
    ('def test(monkeypatch):\n    monkeypatch.setenv(name="BASH_ENV", value=p)\n', "env-startup",
     "first-argument"),
    ('def test(monkeypatch):\n    monkeypatch.setenv(value=p, name="BASH_FUNC_ls%%")\n',
     "env-func", "first-argument"),
)

# Clean sources, each with a flip that is reported: mappings that never reach an environment
# (the flip reaches one), and bytes and literal-shape boundaries.
GATED_VECTORS = (
    ('env = {b"BASH_ENVX": f}\n', 'env = {b"BASH_ENV": f}\n'),
    ('import os\ndel os.environb[b"BASH_ENV"]\n', 'import os\nos.environb[b"BASH_ENV"] = f\n'),
    ('import os\nrun(c, env=os.environ.copy())\n',
     'import os\nrun(c, env=os.environ.copy() | {"ENV": f})\n'),
    ('env = {"A": {"BASH_ENV": p}}\n', 'env = {"A": 1, **{"BASH_ENV": p}}\n'),
    ('env = [("BASH_ENV", p)]\n', 'env = dict([("BASH_ENV", p)])\n'),
    ('dict.update(counts, BASH_ENV=p)\n', 'dict.update(env, BASH_ENV=p)\n'),
    ('blocked = {"BASH_ENV": "startup file"}\nfor k in blocked:\n    env.pop(k, None)\n',
     'blocked_env = {"BASH_ENV": "startup file"}\nfor k in blocked_env:\n    env.pop(k, None)\n'),
    ('UNSET = {"BASH_ENV": None, "ENV": None}\nok = k not in UNSET\n',
     'UNSET_ENV = {"BASH_ENV": None, "ENV": None}\nok = k not in UNSET_ENV\n'),
    ('drop = {"BASH_ENV": True}\n', 'drop.env = {"BASH_ENV": True}\n'),
    ('ok = key in {"BASH_ENV": 1, "ENV": 1}\n', 'ok = run(c, env={"BASH_ENV": 1, "ENV": 1})\n'),
    ('expect = {"BASH_ENV": None}\n', 'expect_env = {"BASH_ENV": None}\n'),
    ('s = "{BASH_ENV}".format(BASH_ENV=p)\n', 's = env.update(BASH_ENV=p)\n'),
    ('import string\ns = string.Template("$ENV").substitute(ENV="prod")\n',
     'import string\ns = string.Template("$ENV").substitute(env=dict(ENV="prod"))\n'),
    ('counts["BASH_ENV"] = counts.get("BASH_ENV", 0) + 1\n',
     'env["BASH_ENV"] = env.get("BASH_ENV", 0) + 1\n'),
    ('seen.setdefault("BASH_ENV", []).append(p)\n',
     'seen_env.setdefault("BASH_ENV", []).append(p)\n'),
    ('config = {"ENV": "production"}\n', 'config.ENV = {"ENV": "production"}\n'),
    ('import logging\nlogging.info("m", extra={"ENV": "ci"})\n',
     'import logging\nlogging.info("m", env={"ENV": "ci"})\n'),
    ('from typing import TypedDict\nE = TypedDict("E", {"BASH_ENV": str})\n',
     'from typing import TypedDict\nE = TypedDict("E", env={"BASH_ENV": str})\n'),
    ('run(cmd, ENV=1)\n', 'run(cmd, env=dict(ENV=1))\n'),
    ('dict.setdefault(counts, "BASH_ENV", 0)\n', 'dict.setdefault(env, "BASH_ENV", 0)\n'),
    ('dict.__setitem__(counts, b"ENV", 0)\n', 'dict.__setitem__(env, b"ENV", 0)\n'),
    ('lookup = dict.fromkeys(["BASH_ENV"], None)\n',
     'lookup_env = dict.fromkeys(["BASH_ENV"], None)\n'),
    ('table = dict([("BASH_ENV", p)])\n', 'table_env = dict([("BASH_ENV", p)])\n'),
    ('table.update([("BASH_ENV", p)])\n', 'table.environ.update([("BASH_ENV", p)])\n'),
    ('seen[b"BASH_ENV"] = 1\n', 'import os\nos.environb[b"BASH_ENV"] = 1\n'),
    # a conditional, an or/and and a := whose mapping does not reach an environment
    ('table = {"BASH_ENV": p} if p else None\n', 'env = {"BASH_ENV": p} if p else None\n'),
    ('import os\nrun(c, env=os.environ.copy() if p else None)\n',
     'import os\nrun(c, env=os.environ.copy() if p else {"ENV": p})\n'),
    ('import os\nrun(c, env={**os.environ} if {"BASH_ENV": p} else None)\n',
     'import os\nrun(c, env={**os.environ} if p else {"BASH_ENV": p})\n'),
    ('table = extra or {"BASH_ENV": p}\n', 'env = extra or {"BASH_ENV": p}\n'),
    ('run(c, extra=(e := {"BASH_ENV": p}))\n', 'run(c, env=(e := {"BASH_ENV": p}))\n'),
    # patch.dict on a mapping that is not the environment, and setenv's neighbours
    ('from unittest import mock\nmock.patch.dict(config, BASH_ENV=p).start()\n',
     'from unittest import mock\nimport os\nmock.patch.dict(os.environ, BASH_ENV=p).start()\n'),
    ('from unittest import mock\n@mock.patch.dict("app.settings", {"ENV": "x"})\n'
     'def test():\n    pass\n',
     'from unittest import mock\n@mock.patch.dict("os.environ", {"ENV": "x"})\n'
     'def test():\n    pass\n'),
    ('import os\nfrom unittest import mock\nmock.patch.dict(os.environ, {"HOME": t}, ENV_X=p)'
     '.start()\n',
     'import os\nfrom unittest import mock\nmock.patch.dict(os.environ, {"HOME": t}, ENV=p)'
     '.start()\n'),
    ('import os\nd = os.environ.dict(a, BASH_ENV=p)\n',
     'import os\nd = mod.dict(os.environ, BASH_ENV=p)\n'),
    ('def test(monkeypatch):\n    monkeypatch.delenv("BASH_ENV", raising=False)\n',
     'def test(monkeypatch):\n    monkeypatch.setenv("BASH_ENV", p)\n'),
    ('def test(monkeypatch):\n    monkeypatch.setenv("HOME", "BASH_ENV")\n',
     'def test(monkeypatch):\n    monkeypatch.setenv("ENV", "BASH_ENV")\n'),
    # the keyword forms of patch.dict and setenv on a mapping that is not the environment, or
    # with the key in the value
    ('from unittest import mock\nmock.patch.dict(config, values={"BASH_ENV": p}).start()\n',
     'from unittest import mock\nimport os\n'
     'mock.patch.dict(os.environ, values={"BASH_ENV": p}).start()\n'),
    ('from unittest import mock\n'
     'mock.patch.dict(config, values=[("BASH_FUNC_ls%%", f)]).start()\n',
     'from unittest import mock\nimport os\n'
     'mock.patch.dict(os.environ, values=[("BASH_FUNC_ls%%", f)]).start()\n'),
    ('from unittest import mock\nmock.patch.dict(in_dict=config, BASH_ENV=p).start()\n',
     'from unittest import mock\nimport os\n'
     'mock.patch.dict(in_dict=os.environ, BASH_ENV=p).start()\n'),
    ('from unittest import mock\n@mock.patch.dict("app.settings", values={"BASH_ENV": p})\n'
     'def test():\n    pass\n',
     'from unittest import mock\n@mock.patch.dict("os.environ", values={"BASH_ENV": p})\n'
     'def test():\n    pass\n'),
    ('from unittest import mock\n'
     'mock.patch.dict(in_dict="app.settings", values={"ENV": p}).start()\n',
     'from unittest import mock\n'
     'mock.patch.dict(in_dict="os.environb", values={"ENV": p}).start()\n'),
    ('def test(monkeypatch):\n    monkeypatch.setenv(name="HOME", value="BASH_ENV")\n',
     'def test(monkeypatch):\n    monkeypatch.setenv(name="BASH_ENV", value="HOME")\n'),
    ('def test(monkeypatch):\n    monkeypatch.setenv(value="BASH_FUNC_ls%%", name="HOME")\n',
     'def test(monkeypatch):\n    monkeypatch.setenv(value="HOME", name="BASH_FUNC_ls%%")\n'),
)

# The key texts each route reads before the kind test, as sorted (text, route) pairs: patch.dict's
# own in_dict, values and clear parameters and setenv's value= are never read as keys, while the
# same names given to the target's update are keys.
RECORDED_VECTORS = (
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(in_dict=os.environ, values={"HOME": t}, clear=True, PATH=p).start()\n',
     [("HOME", "dict-key"), ("PATH", "keyword")]),
    ('import os\nfrom unittest import mock\n'
     'mock.patch.dict(os.environ, values=[("HOME", t)], clear=False).start()\n',
     [("HOME", "pair")]),
    ('import os\nos.environ.update(in_dict=t, values=t, clear=t)\n',
     [("clear", "keyword"), ("in_dict", "keyword"), ("values", "keyword")]),
    ('def test(monkeypatch):\n    monkeypatch.setenv(value="BASH_ENV", name="HOME")\n',
     [("HOME", "first-argument")]),
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
    'import os\nfrom unittest import mock\nmock.patch.dict(os.environ, clear=True).start()\n',
    'def test(monkeypatch):\n    monkeypatch.delenv("BASH_FUNC_ls%%")\n',
    'env = {"BASH_ENV": p}.get(k) if p else None\n',
    'import os\nfrom unittest import mock\n'
    'mock.patch.dict(in_dict=os.environ, values={"HOME": t}, clear=True).start()\n',
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
# 500 concatenated terms and a 500-term union: valid code a recursive walk cannot finish.
_LONG_CHAIN = b"x = " + b" + ".join([b"'a'"] * 500) + b"\n"
_LONG_UNION = b"env = os.environ" + b" | {}" * 500
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
                                                b"        env = dict(BASH_ENV=1)\n"},
                            allowlist=(("tools/a.py", "f", "env-startup", 1, _REASON),)), 1,
     dict(files={"tools/a.py": b"class C:\n    def f(self):\n        env = dict(BASH_ENV=1)\n"},
          allowlist=(("tools/a.py", "C.f", "env-startup", 1, _REASON),)), 0),
    ("allow/malformed", dict(files=_VALID, allowlist=(("tools/a.py", "<module>", "env-func", 0,
                                                       _REASON),)), 2,
     dict(files=_VALID), 0),
    ("errors/long-chain",
     dict(files={"tools/a.py": _LONG_CHAIN + _LONG_UNION + b' | {"ENV": f}\n'}), 1,
     dict(files={"tools/a.py": _LONG_CHAIN + _LONG_UNION + b"\n"}), 0),
)


class _RecordingScanner(_Scanner):
    """A scanner that keeps every (key text, route) a route reads, before the kind test."""

    def __init__(self):
        super().__init__(frozenset())
        self.recorded = []

    def _record(self, node, text, route):
        self.recorded.append((text, route))


def _recorded(source):
    scanner = _RecordingScanner()
    scanner.scan(ast.parse(source))
    return sorted(scanner.recorded)


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
    for source, flip in GATED_VECTORS:
        got = scan_source(source)
        if got:
            failures.append("gated negative flagged: " + repr(source) + " -> " + repr(got))
        if not scan_source(flip):
            failures.append("gated flip " + repr(flip) + ": expected a finding, got none")
    for source, expected in RECORDED_VECTORS:
        got = _recorded(source)
        if got != expected:
            failures.append("recorded keys " + repr(source) + ": expected " + repr(expected)
                            + ", got " + repr(got))
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
          " vectors (each clean without its route), {} negatives, {} clean sources and their"
          " reported flips, {} recorded-key vectors, {} fail-closed and allowlist tree vectors"
          " and their flips.".format(len(ROWS), len(POSITIVE_VECTORS), len(NEGATIVE_VECTORS),
                                     len(GATED_VECTORS), len(RECORDED_VECTORS),
                                     len(TREE_VECTORS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
