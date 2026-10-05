#!/usr/bin/env python3
"""Dangerous-API AST lint over the repository's own Python (plan item 6).

Run with: python3 -I -B tools/check_dangerous_api.py [--root DIR]
          python3 -I -B tools/check_dangerous_api.py --self-test
Exit: 0 clean within scope; 1 finding; 2 cannot-evaluate.

Rule text: "parsing cannot become execution" (secdsz) and "certificate and hostname
validation stay on" (seccry); the shell and code injection sinks cite secinp with secenc
(a shell string is a sink that needs its own encoding) or secout (code text is never run
merely because it was produced). The scan parses every *.py file under SCAN_ROOTS and every
plugin/*/hooks/scripts tree with ast, never importing or running the file, and reports:

  deserialize  a reference to an unsafe loader (pickle, _pickle, cPickle, dill, cloudpickle,
               marshal, shelve, jsonpickle, pandas.read_pickle, yaml unsafe_load/full_load),
               and yaml.load or yaml.load_all whose Loader is not a safe or base loader;
  tls          verify=False (or 0, or a non-literal value) as a call keyword, any reference
               to _create_unverified_context, _create_stdlib_context, CERT_NONE or
               CERT_OPTIONAL, and a check_hostname or verify_mode set to anything but True or
               ssl.CERT_REQUIRED (assignment or keyword, cert_reqs= included);
  shell        a subprocess run/call/check_call/check_output/Popen whose shell is on (or
               cannot be seen, through ** keywords) with a non-literal command, and
               os.system, os.popen, subprocess.getoutput/getstatusoutput or
               asyncio.create_subprocess_shell on a non-literal command, or referenced
               without a call;
  code         any reference to eval or exec (builtins included).

Names are resolved through the file's own import aliases (import x as y, from x import y as
z); a star import from a sink module is itself a finding. A reviewed site is admitted only
by an ALLOWLIST entry (path, enclosing qualname, kind, exact count, reason); an entry whose
count no longer matches is itself a finding. A file the scan cannot stat, read, decode as
UTF-8 or parse, a missing root, and a non-regular *.py entry are cannot-evaluate (exit 2),
never a clean pass.

Residuals: dynamic dispatch (getattr(module, name), importlib, globals() or vars() lookups,
a sink passed through a container or a call return), aliasing of a sink through getattr or
through a plain-variable binding of a module object, a shell launched through an argv list
naming a shell (["sh", "-c", text]) or an executable= override, TLS verification disabled
inside a third-party library's own defaults or through environment variables, a deserializer
outside the named set, a star import from a module outside STAR_MODULES, a symlinked
directory inside a root (not descended), and code outside the scanned roots (the vendored
opf/tools/_vendor tree, .github, .preview, site, and Python embedded in a non-.py file) are
not seen.
"""
import argparse
import ast
import os
import stat
import sys
import tempfile
from pathlib import Path

SCAN_ROOTS = ("tools", "opf/tools", ".aiqt/core/hooks/scripts")
PLUGIN_ROOT = "plugin"
PLUGIN_SCRIPTS = ("hooks", "scripts")
EXCLUDED_DIRS = frozenset({"opf/tools/_vendor"})
SKIPPED_NAMES = frozenset({"__pycache__"})

KIND_RULES = {
    "deserialize": ("secdsz",),
    "tls": ("seccry",),
    "shell": ("secinp", "secenc"),
    "code": ("secinp", "secout"),
}

_UNSAFE_LOADER_FUNCS = ("load", "loads", "Unpickler")
DESERIALIZE_SINKS = frozenset(
    [mod + "." + fn for mod in ("pickle", "_pickle", "cPickle", "dill", "cloudpickle")
     for fn in _UNSAFE_LOADER_FUNCS]
    + ["marshal.load", "marshal.loads", "shelve.open", "shelve.Shelf", "shelve.DbfilenameShelf",
       "jsonpickle.decode", "pandas.read_pickle", "yaml.unsafe_load", "yaml.unsafe_load_all",
       "yaml.full_load", "yaml.full_load_all"])
YAML_LOAD = frozenset({"yaml.load", "yaml.load_all"})
YAML_SAFE_LOADERS = frozenset("yaml." + name for name in (
    "SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"))
TLS_ATTRS = frozenset({"_create_unverified_context", "_create_stdlib_context", "CERT_NONE",
                       "CERT_OPTIONAL"})
SUBPROCESS_FUNCS = frozenset("subprocess." + name for name in (
    "run", "call", "check_call", "check_output", "Popen"))
ALWAYS_SHELL = frozenset({"os.system", "os.popen", "subprocess.getoutput",
                          "subprocess.getstatusoutput", "asyncio.create_subprocess_shell"})
CODE_SINKS = frozenset({"eval", "exec", "builtins.eval", "builtins.exec"})
# A star import from a sink module makes its sinks bare names the alias map cannot see.
STAR_MODULES = {"pickle": "deserialize", "_pickle": "deserialize", "cPickle": "deserialize",
                "dill": "deserialize", "cloudpickle": "deserialize", "marshal": "deserialize",
                "shelve": "deserialize", "jsonpickle": "deserialize", "yaml": "deserialize",
                "ssl": "tls", "os": "shell", "subprocess": "shell", "asyncio": "shell",
                "builtins": "code"}

# Every detector the scan runs; the self-test disables each in turn to prove its vectors
# depend on it. "alias" is the import-alias resolution, the rest are the sink checks and
# the fail-closed and allowlist legs of the tree scan.
CHECKS = ("alias", "star-import", "dsz-ref", "dsz-yaml", "tls-verify", "tls-ref", "tls-hostname",
          "tls-verify-mode", "shell-subprocess", "shell-always", "code-ref",
          "fail-read", "fail-parse", "fail-nonregular", "fail-root", "allowlist-stale",
          "allowlist-shape")

# The reviewed allowlist: (path, enclosing qualname, kind, exact count, reason). Every entry
# names a current legitimate site and why it is safe; a count that no longer matches the
# scan is a finding, so a new site in a reviewed function still needs its own review.
_MUTANT = ("self-test mutant: compiles this repository's own source text with one reviewed"
           " literal substitution, so a reverted guard is shown to fail; no external input")
ALLOWLIST = (
    ("tools/check_footer.py", "_close_vectors", "code", 1,
     _MUTANT + " (the _read_regular_page close revert)"),
    ("tools/check_release_cut.py", "_close_vectors", "code", 1,
     _MUTANT + " (the working_blob close revert)"),
    ("tools/check_release_cut.py", "_self_test_isolated", "code", 1,
     _MUTANT + " (each _CLOSE_SWEEP_REVERTS sweep revert, matched exactly once)"),
    ("tools/pin.py", "_recover_close_vectors", "code", 1,
     _MUTANT + " (the recover close reverts)"),
    ("tools/selftest_git_fixture_env.py", "_system_pin_checks", "code", 1,
     "self-test mutant: compiles an AST of this file's own lifecycle PATH installation with"
     " one parsed literal replacement, into a fresh module; no external input"),
    ("tools/selftest_git_fixture_env.py", "_manifest_extra_setup_failures", "code", 1,
     "self-test probe: runs AST statements lifted from this file's own fixture setup against"
     " a stubbed git; no external input"),
    ("opf/tools/_opf_adopt_observe.py", "_cancellation_self_test", "code", 2,
     _MUTANT + " (the descriptor cleanup and cancellation reverts)"),
    ("opf/tools/_opf_adopt_observe.py", "self_test.source_mutant", "code", 1,
     _MUTANT + " (source_mutant requires exactly one matching site)"),
    ("opf/tools/_opf_emit.py", "_fixture_main", "code", 1,
     "self-test fixture runner: runs the Python body its own supervisor passes after an"
     " explicit isolated sys.executable -I -B -c argv, the same trust as python3 -c; the body"
     " is a self-test literal, never adopter or network input"),
    ("opf/tools/_opf_emit.py", "_st_guardian_close_reuse", "code", 1,
     _MUTANT + " (the guardian close revert)"),
    ("opf/tools/_opf_init_substrate.py", "_t_s23_plan_close_reuse", "code", 1,
     _MUTANT + " (the T-s23 plan close revert)"),
    ("opf/tools/_opf_manifest_regressions.py", "_validator_namespace", "code", 1,
     "regression harness: compiles a transformed AST of the repository's own _opf_store"
     " source into a namespace; no external input"),
    ("opf/tools/check_opf_init_observe.py", "load_candidate", "code", 1,
     "check harness: loads the repository's own candidate module source (or its literal"
     " mutant) as a fresh module, the equivalent of an import; no external input"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.candidate", "code", 1,
     "check harness: compiles a self-test literal candidate body with the module's own"
     " run source; no external input"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.policy", "code", 1,
     "check harness: compiles a self-test literal policy body into a candidate module; no"
     " external input"),
    ("opf/tools/check_opf_init_p0.py", "red_on_revert", "code", 1,
     _MUTANT + " (each guard revert, matched exactly once)"),
    ("opf/tools/check_opf_prompt_pack.py", "_close_vectors", "code", 1,
     _MUTANT + " (the _read_regular close revert)"),
    ("opf/tools/check_opf_record.py", "flip_t70", "code", 1,
     _MUTANT + " (the _opf_oplock _acquire_body T70 flip)"),
    ("opf/tools/opf.py", "_watchdog_completion_case", "code", 5,
     "self-test mutants: compile ASTs of the repository's own _opf_emit members with parsed"
     " literal mutations; no external input"),
    ("opf/tools/opf.py", "_watchdog_completion_case.displaced_outward", "code", 1,
     "self-test vector: compiles the dedented self-test literal for the leg 11 vector; no"
     " external input"),
)


class CannotEvaluate(Exception):
    pass


def _literal_command(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(isinstance(e, ast.Constant) and isinstance(e.value, (str, bytes))
                   for e in node.elts)
    return False


def _keyword(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw
    return None


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


class _Scanner(ast.NodeVisitor):
    def __init__(self, disabled):
        self.disabled = frozenset(disabled)
        self.aliases = {}
        self.findings = []
        self.scope = []
        self.handled = set()

    def on(self, check):
        return check not in self.disabled

    def add(self, node, kind, check, detail):
        qual = ".".join(self.scope) if self.scope else "<module>"
        self.findings.append((getattr(node, "lineno", 0), qual, kind, check, detail))

    def collect_aliases(self, tree):
        if not self.on("alias"):
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.asname:
                        self.aliases[item.asname] = item.name
                    else:
                        head = item.name.split(".")[0]
                        self.aliases[head] = head
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for item in node.names:
                    if item.name != "*":
                        self.aliases[item.asname or item.name] = node.module + "." + item.name

    def resolve(self, node):
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return None if base is None else base + "." + node.attr
        return None

    def _scoped(self, node, name):
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._scoped(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node):
        if node.module in STAR_MODULES and not node.level and self.on("star-import") \
                and any(item.name == "*" for item in node.names):
            self.add(node, STAR_MODULES[node.module], "star-import",
                     "star import from " + node.module + " hides its sinks")

    def visit_ClassDef(self, node):
        self._scoped(node, node.name)

    @staticmethod
    def _command(node, keys):
        if node.args:
            return node.args[0]
        for key in keys:
            if _keyword(node, key) is not None:
                return _keyword(node, key).value
        return None

    def visit_Call(self, node):
        name = self.resolve(node.func)
        if name in YAML_LOAD and self.on("dsz-yaml"):
            self.handled.add(id(node.func))
            loader = _keyword(node, "Loader")
            loader = loader.value if loader is not None else (
                node.args[1] if len(node.args) > 1 else None)
            if loader is None or self.resolve(loader) not in YAML_SAFE_LOADERS:
                self.add(node, "deserialize", "dsz-yaml", name + " without a safe Loader")
        if name in SUBPROCESS_FUNCS and self.on("shell-subprocess"):
            shell = _keyword(node, "shell")
            if shell is not None:
                shell_on = not (isinstance(shell.value, ast.Constant) and not shell.value.value)
            else:
                shell_on = any(kw.arg is None for kw in node.keywords) or (
                    name == "subprocess.Popen" and len(node.args) > 8)
            command = self._command(node, ("args",))
            if shell_on and (command is None or not _literal_command(command)):
                self.add(node, "shell", "shell-subprocess",
                         name + " with shell on (or unseen) over a non-literal command")
        if name in ALWAYS_SHELL and self.on("shell-always"):
            self.handled.add(id(node.func))
            command = self._command(node, ("cmd", "command", "program"))
            if command is None or not _literal_command(command):
                self.add(node, "shell", "shell-always", name + " over a non-literal command")
        if self.on("tls-verify"):
            verify = _keyword(node, "verify")
            if verify is not None and not (isinstance(verify.value, ast.Constant)
                                           and verify.value.value is not False
                                           and verify.value.value != 0):
                self.add(node, "tls", "tls-verify", "verify= is False, 0 or not a literal")
        if self.on("tls-hostname"):
            kw = _keyword(node, "check_hostname")
            if kw is not None and not _is_true(kw.value):
                self.add(node, "tls", "tls-hostname", "check_hostname= is not True")
        if self.on("tls-verify-mode"):
            for key in ("verify_mode", "cert_reqs"):
                kw = _keyword(node, key)
                if kw is not None and self.resolve(kw.value) != "ssl.CERT_REQUIRED":
                    self.add(node, "tls", "tls-verify-mode", key + "= is not ssl.CERT_REQUIRED")
        self.generic_visit(node)

    def _check_store(self, target, value, node):
        attr = target.attr if isinstance(target, ast.Attribute) else (
            target.id if isinstance(target, ast.Name) else None)
        if attr == "check_hostname" and self.on("tls-hostname") \
                and (value is None or not _is_true(value)):
            self.add(node, "tls", "tls-hostname", "check_hostname set to a value other than True")
        if attr == "verify_mode" and self.on("tls-verify-mode") \
                and (value is None or self.resolve(value) != "ssl.CERT_REQUIRED"):
            self.add(node, "tls", "tls-verify-mode",
                     "verify_mode set to a value other than ssl.CERT_REQUIRED")

    def visit_Assign(self, node):
        for target in node.targets:
            for leaf in ast.walk(target):
                if isinstance(leaf, (ast.Attribute, ast.Name)) \
                        and isinstance(leaf.ctx, ast.Store):
                    self._check_store(leaf, node.value if leaf is target else None, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self._check_store(node.target, node.value, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._check_store(node.target, None, node)
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self._check_store(node.target, node.value, node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self.handled:
            self._reference(node, node.attr)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and id(node) not in self.handled:
            self._reference(node, None)

    def _reference(self, node, attr):
        name = self.resolve(node)
        if name in DESERIALIZE_SINKS and self.on("dsz-ref"):
            self.add(node, "deserialize", "dsz-ref", "unsafe deserializer " + name)
        elif name in YAML_LOAD and self.on("dsz-yaml"):
            self.add(node, "deserialize", "dsz-yaml", name + " referenced without a call")
        if self.on("tls-ref"):
            last = attr if attr is not None else (name or "").rsplit(".", 1)[-1]
            if last in TLS_ATTRS:
                self.add(node, "tls", "tls-ref", "reference to " + last)
        if name in ALWAYS_SHELL and self.on("shell-always"):
            self.add(node, "shell", "shell-always", name + " referenced without a call")
        if name in CODE_SINKS and self.on("code-ref"):
            self.add(node, "code", "code-ref", "reference to " + name)


def scan_source(source, filename="<source>", disabled=()):
    """Return the sorted findings for one source text; SyntaxError propagates."""
    tree = ast.parse(source, filename=filename)
    scanner = _Scanner(disabled)
    scanner.collect_aliases(tree)
    scanner.visit(tree)
    return sorted(set(scanner.findings))


def _python_files(root, disabled):
    roots = [root / rel for rel in SCAN_ROOTS]
    plugin = root / PLUGIN_ROOT
    if plugin.is_dir():
        for entry in sorted(plugin.iterdir()):
            scripts = entry.joinpath(*PLUGIN_SCRIPTS)
            if scripts.is_dir():
                roots.append(scripts)
    elif "fail-root" not in disabled:
        raise CannotEvaluate("scan root missing: " + PLUGIN_ROOT)
    files = []
    for base in roots:
        if not base.is_dir():
            if "fail-root" in disabled:
                continue
            raise CannotEvaluate("scan root missing: " + base.relative_to(root).as_posix())
        for current, dirnames, filenames in os.walk(base, followlinks=False):
            rel_dir = Path(current).relative_to(root).as_posix()
            dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_NAMES
                                 and (rel_dir + "/" + d) not in EXCLUDED_DIRS)
            for name in sorted(filenames):
                if name.endswith(".py"):
                    files.append(Path(current) / name)
    return files


def _check_allowlist(allowlist):
    for entry in allowlist:
        if not (isinstance(entry, tuple) and len(entry) == 5
                and all(isinstance(entry[i], str) and entry[i].strip() for i in (0, 1, 2, 4))
                and entry[2] in KIND_RULES and type(entry[3]) is int and entry[3] >= 1):
            raise CannotEvaluate("malformed allowlist entry: " + repr(entry))
    keys = [entry[:3] for entry in allowlist]
    if len(set(keys)) != len(keys):
        raise CannotEvaluate("duplicate allowlist key")


def scan_tree(root, allowlist=ALLOWLIST, disabled=()):
    """Return the finding lines for the tree under root; raise CannotEvaluate when it cannot."""
    root = Path(root)
    disabled = frozenset(disabled)
    if "allowlist-shape" not in disabled:
        _check_allowlist(allowlist)
    hits = []
    for path in _python_files(root, disabled):
        rel = path.relative_to(root).as_posix()
        try:
            regular = stat.S_ISREG(os.stat(path).st_mode)
        except OSError:
            regular = False
        if not regular:
            if "fail-nonregular" in disabled:
                continue
            raise CannotEvaluate("not a readable regular file: " + rel)
        try:
            source = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            if "fail-read" in disabled:
                continue
            raise CannotEvaluate("cannot read " + rel + ": " + str(exc))
        try:
            found = scan_source(source, rel, disabled)
        except (SyntaxError, ValueError) as exc:
            if "fail-parse" in disabled:
                continue
            raise CannotEvaluate("cannot parse " + rel + ": " + str(exc))
        hits.extend((rel,) + item for item in found)
    counts = {}
    for rel, _line, qual, kind, _check, _detail in hits:
        counts[(rel, qual, kind)] = counts.get((rel, qual, kind), 0) + 1
    allowed = {entry[:3]: entry[3] for entry in allowlist}
    stale_off = "allowlist-stale" in disabled
    findings = []
    for rel, line, qual, kind, check, detail in hits:
        key = (rel, qual, kind)
        if key in allowed and (stale_off or counts[key] == allowed[key]):
            continue
        findings.append("{}:{}: [{} {}] {}: {} ({})".format(
            rel, line, kind, "/".join(KIND_RULES[kind]), qual, detail, check))
    if not stale_off:
        for key, expected in sorted(allowed.items()):
            if counts.get(key, 0) != expected:
                findings.append("allowlist entry {} {} {} expects {} site(s), scan found {}"
                                .format(key[0], key[1], key[2], expected, counts.get(key, 0)))
    return findings


def run(root):
    try:
        findings = scan_tree(root)
    except CannotEvaluate as exc:
        print("ERROR: dangerous-api cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    if findings:
        for line in findings:
            print("VIOLATION: dangerous-api: " + line, file=sys.stderr)
        return 1
    print("PASS: dangerous-api: no unreviewed deserialization, TLS-verification, shell or"
          " code-execution sink in the scanned roots; see residuals.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dangerous-API AST lint over this repo's Python.")
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


# Positive vectors: (source, checks). Each must yield a finding with all checks on, and NO
# finding with any one of its checks disabled, so every vector fails without its check.
POSITIVE_VECTORS = (
    ("import pickle\npickle.loads(b'')\n", ("dsz-ref",)),
    ("import marshal\nmarshal.loads(b'')\n", ("dsz-ref",)),
    ("import shelve\nshelve.open('db')\n", ("dsz-ref",)),
    ("import pickle\nreader = pickle.load\n", ("dsz-ref",)),
    ("import yaml\nyaml.unsafe_load(text)\n", ("dsz-ref",)),
    ("import pickle as p\np.loads(b'')\n", ("alias", "dsz-ref")),
    ("from pickle import loads\nloads(b'')\n", ("alias", "dsz-ref")),
    ("import yaml\nyaml.load(text)\n", ("dsz-yaml",)),
    ("import yaml\nyaml.load(text, Loader=yaml.FullLoader)\n", ("dsz-yaml",)),
    ("import yaml\nloader = yaml.load_all\n", ("dsz-yaml",)),
    ("import requests\nrequests.get(url, verify=False)\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, verify=flag)\n", ("tls-verify",)),
    ("import ssl\nctx = ssl._create_unverified_context()\n", ("tls-ref",)),
    ("import ssl\nmode = ssl.CERT_NONE\n", ("tls-ref",)),
    ("from ssl import CERT_NONE as none\nmode = none\n", ("alias", "tls-ref")),
    ("ctx.check_hostname = False\n", ("tls-hostname",)),
    ("class Ctx:\n    check_hostname = chosen\n", ("tls-hostname",)),
    ("make_context(check_hostname=False)\n", ("tls-hostname",)),
    ("ctx.verify_mode = chosen\n", ("tls-verify-mode",)),
    ("wrap(sock, cert_reqs=chosen)\n", ("tls-verify-mode",)),
    ("import subprocess\nsubprocess.run(cmd, shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(f'ls {name}', shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.Popen(cmd, **opts)\n", ("shell-subprocess",)),
    ("from subprocess import run as r\nr(cmd, shell=True)\n", ("alias", "shell-subprocess")),
    ("import os\nos.system(cmd)\n", ("shell-always",)),
    ("import os\nos.popen('ls ' + path)\n", ("shell-always",)),
    ("import subprocess\nsubprocess.getoutput(cmd)\n", ("shell-always",)),
    ("import os\nlaunch = os.system\n", ("shell-always",)),
    ("from os import system\nsystem(cmd)\n", ("alias", "shell-always")),
    ("eval(text)\n", ("code-ref",)),
    ("def f(text):\n    exec(text)\n", ("code-ref",)),
    ("run = exec\n", ("code-ref",)),
    ("import builtins as b\nb.exec(text)\n", ("alias", "code-ref")),
    ("from pickle import *\nloads(b'')\n", ("star-import",)),
    ("mode = CERT_NONE\n", ("tls-ref",)),
)

# Negative vectors: the safe spellings each check must leave alone.
NEGATIVE_VECTORS = (
    "import yaml\nyaml.load(text, Loader=yaml.SafeLoader)\nyaml.safe_load(text)\n",
    "import json\njson.loads(text)\n",
    "import subprocess\nsubprocess.run(['git', 'status'])\nsubprocess.run(cmd)\n",
    "import subprocess\nsubprocess.run('ls -l', shell=True)\nsubprocess.run(cmd, shell=False)\n",
    "import os\nos.system('true')\n",
    "import requests\nrequests.get(url, verify=True)\nrequests.get(url, verify='/ca.pem')\n",
    "import ssl\nctx.check_hostname = True\nctx.verify_mode = ssl.CERT_REQUIRED\n"
    "ok = ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED\n",
    "class Ctx:\n    check_hostname = True\n",
    "pattern.exec(text)\nre.compile(text)\n",
)


def _write_tree(base, files):
    for rel in SCAN_ROOTS + (PLUGIN_ROOT + "/p/hooks/scripts",):
        os.makedirs(os.path.join(base, rel), exist_ok=True)
    for rel, data in files.items():
        os.makedirs(os.path.dirname(os.path.join(base, rel)), exist_ok=True)
        Path(base, rel).write_bytes(data)


def _tree_outcome(files, allowlist=(), disabled=(), symlink=None, drop=None):
    with tempfile.TemporaryDirectory() as base:
        _write_tree(base, files)
        if symlink:
            os.symlink(os.path.join(base, "absent-target"), os.path.join(base, symlink))
        if drop:
            os.rmdir(os.path.join(base, drop))
        try:
            return scan_tree(base, allowlist, disabled)
        except CannotEvaluate:
            return "cannot-evaluate"


_SITE = b"def f(text):\n    exec(text)\n"
_REASON = "self-test reason"
# Tree vectors: (name, files, allowlist, symlink, drop, expected, check, expected-without).
TREE_VECTORS = (
    ("parse failure", {"tools/bad.py": b"def broken(:\n"}, (), None, None,
     "cannot-evaluate", "fail-parse", []),
    ("undecodable file", {"tools/bad.py": b"x = '\xff'\n"}, (), None, None,
     "cannot-evaluate", "fail-read", []),
    ("broken symlink", {}, (), "tools/link.py", None, "cannot-evaluate", "fail-nonregular", []),
    ("missing root", {}, (), None, "opf/tools", "cannot-evaluate", "fail-root", []),
    ("plugin hook script scanned", {"plugin/p/hooks/scripts/h.py": _SITE}, (), None, None,
     1, "code-ref", []),
    ("allowlist count drift", {"tools/a.py": _SITE},
     (("tools/a.py", "f", "code", 2, _REASON),), None, None, 2, "allowlist-stale", []),
    ("allowlist entry vanished", {"tools/a.py": b"x = 1\n"},
     (("tools/a.py", "f", "code", 1, _REASON),), None, None, 1, "allowlist-stale", []),
    ("allowlist entry without reason", {"tools/a.py": _SITE},
     (("tools/a.py", "f", "code", 1, " "),), None, None, "cannot-evaluate", "allowlist-shape", []),
)


def self_test():
    failures = []
    exercised = set()
    for source, checks in POSITIVE_VECTORS:
        exercised.update(checks)
        got = scan_source(source)
        if not any(item[3] in checks for item in got):
            failures.append("positive not detected: " + repr(source))
        for check in checks:
            if scan_source(source, disabled=(check,)):
                failures.append("vector passes without " + check + ": " + repr(source))
    for source in NEGATIVE_VECTORS:
        got = scan_source(source)
        if got:
            failures.append("negative flagged: " + repr(source) + " -> " + repr(got))
    for name, files, allowlist, symlink, drop, expected, check, without in TREE_VECTORS:
        exercised.add(check)
        got = _tree_outcome(files, allowlist, (), symlink, drop)
        if (got if expected == "cannot-evaluate" else len(got)) != expected:
            failures.append("tree " + name + ": expected " + repr(expected) + ", got " + repr(got))
        if _tree_outcome(files, allowlist, (check,), symlink, drop) != without:
            failures.append("tree " + name + ": does not fail without " + check)
    if _tree_outcome({"tools/a.py": _SITE}, (("tools/a.py", "f", "code", 1, _REASON),)) != []:
        failures.append("tree: a reviewed site with a matching count is not admitted")
    if _tree_outcome({"opf/tools/_vendor/v.py": _SITE}) != []:
        failures.append("tree: the vendored tree is not excluded")
    if _tree_outcome({"tools/a.py": _SITE}) == []:
        failures.append("tree: an unreviewed site passes")
    unexercised = sorted(set(CHECKS) - exercised)
    if unexercised:
        failures.append("checks without a vector: " + ", ".join(unexercised))
    try:
        _check_allowlist(ALLOWLIST)
    except CannotEvaluate as exc:
        failures.append("shipped allowlist malformed: " + str(exc))
    if failures:
        print("SELF-TEST FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("SELF-TEST PASS: dangerous-api: {} positive vectors (each fails without its check),"
          " {} negatives, {} fail-closed and allowlist tree vectors.".format(
              len(POSITIVE_VECTORS), len(NEGATIVE_VECTORS), len(TREE_VECTORS) + 3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
