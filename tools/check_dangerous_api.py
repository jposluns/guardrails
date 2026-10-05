#!/usr/bin/env python3
"""Dangerous-API AST lint over the repository's own Python (plan item 6).

Run with: python3 -I -B tools/check_dangerous_api.py [--root DIR]
          python3 -I -B tools/check_dangerous_api.py --self-test
Exit: 0 clean within scope; 1 finding; 2 cannot-evaluate.

Rule text: "parsing cannot become execution" (secdsz), "certificate and hostname validation
stay on" (seccry), and the operating-system command and code injection classes prevented by
construction (secinp). Each is covered in PART only; the residuals below name what is not. The
scan parses every *.py file under SCAN_ROOTS and every plugin/*/hooks/scripts tree with ast,
never importing or running the file, and reports:

  deserialize  a reference to an unsafe loader (pickle, _pickle, cPickle, dill, cloudpickle
               load/loads/Unpickler and their private _load/_loads/_Unpickler, marshal,
               shelve and its Unpickler re-export, jsonpickle, the multiprocessing
               ForkingPickler.loads, pandas.read_pickle, joblib.load, torch.load, the yaml
               unsafe and full loader functions, constructors and Loader classes), a
               yaml.load or yaml.load_all whose Loader is not proven a safe or base loader,
               and an allow_pickle= keyword that is not literal False;
  tls          verify= or verify_ssl= that is False, 0 or not a literal, ssl= that is False or
               0, a .verify attribute set to anything but a non-false literal, any reference to
               _create_unverified_context, _create_stdlib_context, _create_default_https_context,
               CERT_NONE, CERT_OPTIONAL or CLIENT_AUTH, an ssl.SSLContext whose protocol is not
               proven ssl.PROTOCOL_TLS_CLIENT (or referenced uncalled), and a check_hostname or
               verify_mode set to anything but True or a proven ssl.CERT_REQUIRED (assignment,
               a literal setattr, or a keyword, cert_reqs= included);
  shell        a subprocess run/call/check_call/check_output/Popen whose shell is on, or cannot
               be seen (a ** spread that is not a literal dict, a * spread, or a ninth
               positional argument that is not literal False), over a non-literal command;
               os.system, os.popen, posix.system, nt.system, subprocess.getoutput and
               getstatusoutput, asyncio.create_subprocess_shell (asyncio.subprocess included)
               and any loop.subprocess_shell over a non-literal command or one a * or **
               spread hides; and any of these referenced without a call (an alias);
  code         any reference to eval or exec (builtins and __builtins__ included),
               types.FunctionType, and the runpy, timeit, cProfile, profile, pdb and code
               runners, and any call of a method named exec_module or load_module (an importlib
               loader running a module's code, whatever object it is called on).

Names resolve PER SCOPE as Python binds them: a binding in a function is local to it (unless
declared global or nonlocal), a nested function sees its enclosing functions but not a class
body, and a module, class or comprehension scope that binds a name may still see the outer
binding, so every binding it could have is a candidate and the reference is a finding if any
candidate is a sink. An unbound name falls back to the builtin of that name, where one exists.
A sink module reached as an attribute of another module names that module (shutil.os.system
is os.system, and from logging.handlers import pickle binds pickle), except an attribute named
like the module that holds it (timeit.timeit is a function). getattr(x, "name") is read as
x.name, and so is a name or attribute directly followed by a string literal in a call's
positional arguments or a tuple or list display (the monkeypatch idiom: (os, "system") is a
reference to os.system), except in setattr, delattr, hasattr and a mock patch.object, which
store, delete or test the attribute rather than read it.

A sink module (each STAR_MODULES name) used other than as an attribute base is a finding: an
attribute base is m.x, the first argument of getattr, setattr, delattr or a mock patch.object
with a literal attribute name, the argument of hasattr, or the name read with a string literal
as above. So a sink module bound to another name in any form (assignment, unpacking, walrus,
conditional or boolean expression, default parameter, match capture, for or with target),
passed as an argument, returned, yielded, held in a container, or passed to getattr with a
non-literal name, is a finding. A judged callable referenced without a call (r = subprocess.run),
a star import from a sink module and a literal importlib.import_module or __import__ of one are
findings.

Keywords are read as Python binds them: a literal ** dict is read by its keys, a duplicate key
keeping its LAST value as Python does, and dict(key=value) only where the name dict can only be
the builtin. A safe value (a yaml Loader, an SSLContext protocol, a verify_mode or cert_reqs)
is PROVEN only when every binding the name may hold names the safe value; a name that may also
hold an assignment, a parameter or any other value the scan cannot name, an unbound name, and
an expression prove nothing, so they are findings. A reviewed site is admitted only by an
ALLOWLIST entry (path, enclosing qualname, kind, exact count, reason); an entry whose count no
longer matches is itself a finding.

Fail closed: each file is decoded as Python decodes it (tokenize.detect_encoding, so a BOM and
a PEP 263 coding cookie are honoured) and a cookie other than utf-8 is cannot-evaluate, since
the scan would otherwise read a different program than Python runs. A file the scan cannot
stat, read, decode or parse, a directory it cannot list or search (each plugin directory and
its hooks directory included: discovery lists them, so an unlistable one is never read as a
plugin without hooks), ANY symlink inside a scanned root or directly under plugin/ (file or
directory, valid or broken), a missing root, and a non-regular *.py entry are cannot-evaluate
(exit 2), never a clean pass. Every directory under a scanned root is walked, __pycache__
included; only the vendored opf/tools/_vendor tree is excluded.

Residuals (not seen): dynamic dispatch (getattr with a non-literal name on an object that is
not a sink module, setattr with a non-literal name, importlib with a non-literal name,
globals(), vars(), __dict__, sys.modules or __builtins__[...] lookups); an attribute chain
whose base is not an import binding (a parameter, self, a call result or a subscript, so
self.os.system and sys.modules["os"].system are not resolved), and a sink module re-exported
under a name other than its own (a module that binds pickle as _p, reached as mod._p.loads);
a sink function (not module) obtained from such an object or from a call; a method run on a
profiler, debugger or trace object (cProfile.Profile().run, bdb.Bdb.run); a shell launched
through an argv list naming a shell (["sh", "-c", text]), an executable= override, or os.exec*,
os.spawn* or pty.spawn of a shell; a verify= or ssl= value hidden in a ** spread that is not a
literal dict (only the subprocess shell switch and the command, Loader and protocol of the
judged sinks are denied when unseen); TLS verification disabled inside a third-party library's
own defaults, through a library option this scan does not name, or through environment
variables (PYTHONHTTPSVERIFY, CURL_CA_BUNDLE, REQUESTS_CA_BUNDLE); a deserializer outside the
named set; a star import from a module outside STAR_MODULES; literalness is judged, not data
flow, so a literal command or a non-false verify can still be attacker-shaped; and code
outside the scanned roots (the vendored opf/tools/_vendor tree, .github, .preview, site, and
Python embedded in a non-.py file). The class and comprehension scopes only widen the
candidate set (they fall through to the outer scope), so their own bindings never hide a sink.
"""
import argparse
import ast
import builtins
import io
import os
import shutil
import stat
import sys
import tempfile
import tokenize
from pathlib import Path

SCAN_ROOTS = ("tools", "opf/tools", ".aiqt/core/hooks/scripts")
PLUGIN_ROOT = "plugin"
PLUGIN_SCRIPTS = ("hooks", "scripts")
EXCLUDED_DIRS = frozenset({"opf/tools/_vendor"})

KIND_RULES = {
    "deserialize": ("secdsz",),
    "tls": ("seccry",),
    "shell": ("secinp",),
    "code": ("secinp",),
}

_PICKLE_MODULES = ("pickle", "_pickle", "cPickle", "dill", "cloudpickle")
_UNSAFE_LOADER_FUNCS = ("load", "loads", "Unpickler", "_load", "_loads", "_Unpickler")
_YAML_UNSAFE = ("unsafe_load", "unsafe_load_all", "full_load", "full_load_all", "Loader",
                "CLoader", "UnsafeLoader", "CUnsafeLoader", "FullLoader", "CFullLoader",
                "UnsafeConstructor", "FullConstructor", "constructor.UnsafeConstructor",
                "constructor.FullConstructor", "loader.Loader", "loader.UnsafeLoader",
                "loader.FullLoader")
DESERIALIZE_SINKS = frozenset(
    [mod + "." + fn for mod in _PICKLE_MODULES for fn in _UNSAFE_LOADER_FUNCS]
    + ["yaml." + name for name in _YAML_UNSAFE]
    + ["marshal.load", "marshal.loads", "shelve.open", "shelve.Shelf", "shelve.DbfilenameShelf",
       "shelve.BsdDbShelf", "shelve.Unpickler", "jsonpickle.decode",
       "jsonpickle.unpickler.decode", "multiprocessing.reduction.ForkingPickler.loads",
       "pandas.read_pickle", "joblib.load", "torch.load"])
YAML_LOAD = frozenset({"yaml.load", "yaml.load_all"})
YAML_SAFE_LOADERS = frozenset("yaml." + name for name in (
    "SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"))
TLS_ATTRS = frozenset({"_create_unverified_context", "_create_stdlib_context",
                       "_create_default_https_context", "CERT_NONE", "CERT_OPTIONAL",
                       "CLIENT_AUTH"})
SSL_CONTEXT = "ssl.SSLContext"
SUBPROCESS_FUNCS = frozenset("subprocess." + name for name in (
    "run", "call", "check_call", "check_output", "Popen"))
ALWAYS_SHELL = frozenset({"os.system", "os.popen", "posix.system", "nt.system",
                          "subprocess.getoutput", "subprocess.getstatusoutput",
                          "asyncio.create_subprocess_shell",
                          "asyncio.subprocess.create_subprocess_shell"})
CODE_SINKS = frozenset({"eval", "exec", "builtins.eval", "builtins.exec", "types.FunctionType",
                        "types.LambdaType", "runpy.run_path", "runpy.run_module",
                        "runpy._run_code", "runpy._run_module_code", "timeit.timeit",
                        "timeit.repeat", "timeit.Timer", "cProfile.run", "cProfile.runctx",
                        "profile.run", "profile.runctx", "pdb.run", "pdb.runeval", "pdb.runctx",
                        "code.InteractiveInterpreter", "code.InteractiveConsole",
                        "code.interact"})
# A loader method that runs a module's code (spec.loader.exec_module(m), loader.load_module()):
# its receiver is an object the scan cannot name, so the method name alone is the finding.
LOADER_METHODS = frozenset({"exec_module", "load_module"})
# The callables whose call site is judged (shell switch, command, Loader, protocol): any other
# reference to them is an alias the call-site check cannot follow, so it is a finding.
CALL_JUDGED = YAML_LOAD | SUBPROCESS_FUNCS | ALWAYS_SHELL | {SSL_CONTEXT}
# A star import from a sink module makes its sinks bare names; the same module object, bound
# to another name or passed to getattr with a non-literal name, is an alias it cannot follow.
STAR_MODULES = {"pickle": "deserialize", "_pickle": "deserialize", "cPickle": "deserialize",
                "dill": "deserialize", "cloudpickle": "deserialize", "marshal": "deserialize",
                "shelve": "deserialize", "jsonpickle": "deserialize", "yaml": "deserialize",
                "pandas": "deserialize", "joblib": "deserialize",
                "ssl": "tls", "os": "shell", "posix": "shell", "nt": "shell",
                "subprocess": "shell", "asyncio": "shell",
                "builtins": "code", "types": "code", "runpy": "code", "timeit": "code",
                "cProfile": "code", "profile": "code", "pdb": "code", "code": "code"}
# An unbound name falls back to the builtin of that name only where one exists (any other
# unbound name is a NameError when run, so it names nothing).
BUILTIN_NAMES = frozenset(builtins.__dict__) | {"__builtins__"}
_GETATTR = frozenset({"getattr", "builtins.getattr"})
_HASATTR = frozenset({"hasattr", "builtins.hasattr"})
_DELATTR = frozenset({"delattr", "builtins.delattr"})
_SETATTR = frozenset({"setattr", "builtins.setattr"})
# A mock patch of one literal attribute names that attribute (patch.object(os, "kill") is os.kill)
# and leaves the module itself in place, so its target is an attribute base like getattr's.
_PATCH_OBJECT = frozenset({"unittest.mock.patch.object", "mock.patch.object"})
_IMPORTERS = frozenset({"importlib.import_module", "__import__", "builtins.__import__"})

# Every detector the scan runs; the self-test disables each in turn to prove its vectors
# depend on it. "alias" is import-alias resolution, "scope" its per-scope binding (off, it
# falls back to one file-global, last-wins map) and "chain" the reading of a sink module
# reached as an attribute of another module (shutil.os.system is os.system); the rest are
# the sink checks and the fail-closed and allowlist legs of the tree scan.
CHECKS = ("alias", "scope", "chain", "star-import", "module-escape", "dsz-ref", "dsz-yaml",
          "dsz-numpy", "tls-verify", "tls-ref", "tls-hostname", "tls-verify-mode", "tls-context",
          "shell-subprocess", "shell-always", "code-ref", "code-loader",
          "fail-encoding", "fail-read", "fail-parse", "fail-nonregular", "fail-symlink",
          "fail-walk", "fail-root", "allowlist-stale", "allowlist-shape")

# The reviewed allowlist: (path, enclosing qualname, kind, exact count, reason). Every entry
# names a current legitimate site and why it is safe; a count that no longer matches the
# scan is a finding, so a new site in a reviewed function still needs its own review.
_MUTANT = ("self-test mutant: compiles this repository's own source text with one reviewed"
           " literal substitution, so a reverted guard is shown to fail; no external input")
_SAVED = ("self-test stub: keeps the real subprocess.run or Popen to delegate to or restore after"
          " patching; the wrapper forwards its caller's own arguments, and no call in this file"
          " turns shell on")
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
     "self-test mutant: compiles an AST of the repository's own tools/_git_fixture_env.py"
     " source (read from _git_fixture_env.__file__), its lifecycle PATH installation with one"
     " parsed literal replacement, into a fresh module; no external input"),
    ("tools/selftest_git_fixture_env.py", "_manifest_extra_setup_failures", "code", 1,
     "self-test probe: runs AST statements lifted from the repository's own"
     " tools/gen_manifest.py source (its fixture setup) against a stubbed git; no external"
     " input"),
    ("opf/tools/_opf_adopt_observe.py", "_cancellation_self_test", "code", 2,
     _MUTANT + " (the descriptor cleanup and cancellation reverts)"),
    ("opf/tools/_opf_adopt_observe.py", "self_test.source_mutant", "code", 1,
     _MUTANT + " (source_mutant requires exactly one matching site)"),
    ("opf/tools/_opf_emit.py", "_fixture_main", "code", 1,
     "self-test fixture runner: runs the Python body its own supervisor passes after an"
     " explicit isolated sys.executable -I -B -c argv, the same trust as python3 -c; the body"
     " is a self-test literal, never adopter or network input"),
    ("opf/tools/_opf_emit.py", "_st_guardian_close_reuse", "code", 2,
     _MUTANT + " (the guardian close revert); exec_module loads the repository's own _journal.py"
     " beside this file as the close harness"),
    ("opf/tools/_opf_emit.py", "_load_byte_canon_authority", "code", 1,
     "authority loader: exec_module of the repository's own opf/tools/_byte_canon.py beside"
     " this file, the equivalent of an import; no external input"),
    ("opf/tools/_opf_init_substrate.py", "_t_s23_plan_close_reuse", "code", 1,
     _MUTANT + " (the T-s23 plan close revert)"),
    ("opf/tools/_opf_manifest_regressions.py", "_validator_namespace", "code", 1,
     "regression harness: compiles a transformed AST of the repository's own _opf_store"
     " source into a namespace; no external input"),
    ("opf/tools/check_opf_init_observe.py", "load_candidate", "code", 1,
     "check harness: loads the repository's own candidate module source (or its literal"
     " mutant) as a fresh module, the equivalent of an import; no external input"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.candidate", "code", 1,
     "check harness: compiles a candidate body its callers extract from the repository's own"
     " opf/tools/_opf_observe.py source (or the self-test literal unsafe reversal body), with"
     " the module's own run source; no external input"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.policy", "code", 1,
     "check harness: compiles a policy body its callers extract from the repository's own"
     " opf/tools/_opf_observe.py source (or a literal mutant of it) into a candidate module; no"
     " external input"),
    ("opf/tools/check_opf_init_p0.py", "red_on_revert", "code", 1,
     _MUTANT + " (each guard revert, matched exactly once)"),
    ("opf/tools/check_opf_prompt_pack.py", "_close_vectors", "code", 2,
     _MUTANT + " (the _read_regular close revert); exec_module loads the repository's own"
     " _journal.py beside this file as the close harness"),
    ("opf/tools/check_opf_record.py", "flip_t70", "code", 1,
     _MUTANT + " (the _opf_oplock _acquire_body T70 flip)"),
    ("opf/tools/opf.py", "_watchdog_completion_case", "code", 5,
     "self-test mutants: compile ASTs of the repository's own _opf_emit members with parsed"
     " literal mutations; no external input"),
    ("opf/tools/opf.py", "_watchdog_completion_case.displaced_outward", "code", 1,
     "self-test vector: compiles the dedented self-test literal for the leg 11 vector; no"
     " external input"),
    ("opf/tools/opf.py", "_watchdog_completion_case.resolve_call", "code", 1,
     "static analysis: getattr(builtins, name) only tests whether a call name in the"
     " repository's own _opf_emit source is a builtin callable; the result is never called"),
    ("opf/tools/opf.py", "_watchdog_completion_case.resolve_exception_classes", "code", 1,
     "static analysis: getattr(builtins, name) resolves an except-clause name in the"
     " repository's own _opf_emit source and asserts it is an exception class; never called"),
    ("opf/tools/opf.py", "_cli_self_test._import_leg", "code", 5,
     "self-test probes: types.FunctionType rebinds the repository's own _cmd_import code object"
     " (or a planted swap of it compiled from this file's own source) to a probe namespace, and"
     " the builtins module is planted as a probe namespace value; no external input"),
    ("opf/tools/opf.py", "_cli_self_test._import_leg", "shell", 2,
     "self-test deny table: (module, name) seams patched to refuse process and file effects"
     " (subprocess.Popen and os names from a fixed literal tuple); replaced, never called"),
    ("opf/tools/check_opf_upgrade.py", "_suite_isolated", "code", 2,
     "self-test flip: exec_module builds a module from the repository's own _opf_upgrade"
     " source with one literal replacement, and types.FunctionType rebinds its code; no"
     " external input"),
    ("tools/check_instruction_budget.py", "_mutant", "code", 1,
     "self-test mutant: exec_module of a scratch copy of this gate's own production source with"
     " one reviewed literal substitution; no external input"),
    ("tools/check_python_floor.py", "_rule_reverts", "code", 1,
     "self-test mutant: exec_module of a copy of this gate's own source with one rule removed"
     " from RULES; no external input"),
    ("tools/gen_crosswalk.py", "_fdopen_vectors.flipped", "code", 1,
     _MUTANT + " (each fdopen close revert, loaded by exec_module from a scratch file)"),
    ("tools/_close_selftest.py", "_StCloseFault.__init__", "shell", 1,
     "self-test fault injector: wraps the os functions named in its own fixed _ST_WATCHED"
     " tuple (descriptor stat and seek calls) to count closes; never runs a shell"),
    ("opf/tools/_journal.py", "_StCloseFault.__init__", "shell", 1,
     "self-test fault injector: wraps the os functions named in its own fixed _ST_WATCHED"
     " tuple (descriptor stat and seek calls) to count closes; never runs a shell"),
    ("opf/tools/check_opf_record.py", "_t77_claim_failing", "shell", 1,
     "self-test fault injector: passes os to the journal's own _st_supports to find the support"
     " table holding the one function it patches; never runs a shell"),
    ("opf/tools/_opf_adopt_hook.py", "self_test", "shell", 4,
     "self-test deny table: the (module, name) seams it patches to refuse file, socket and"
     " process effects (subprocess.Popen and os names from a fixed literal tuple); replaced,"
     " never called"),
    ("opf/tools/_opf_adopt_observe.py", "self_test.deny_effects", "shell", 1,
     "self-test deny patch: mock.patch.object(os, name) over a fixed literal tuple of spawn and"
     " exec names, each replaced with a refusal; never called"),
    ("tools/selftest_aiqt_hooks.py", "_main_isolated", "shell", 1,
     _SAVED + " (the hook module's own subprocess.run, reached as aiqt_hooks.subprocess.run)"),
    ("tools/selftest_orch_hooks.py", "_main_isolated._recheck_under", "shell", 1,
     "self-test fault injector: picks the hook module's own os (or os.path) to patch one"
     " function its caller names from a fixed fault table; never runs a shell"),
    ("tools/selftest_orch_hooks.py", "_main_isolated", "shell", 1,
     "self-test fault injector: setattr on the hook module's own os with a function name from"
     " its fixed fault table; never runs a shell"),
    ("opf/tools/check_opf_init_contract.py", "_checks", "code", 1,
     "check harness: runpy.run_path loads the repository's own"
     " opf/tools/_opf_init_contract.py, the equivalent of an import; no external input"),
    ("opf/tools/_opf_adopt_observe.py", "self_test", "tls", 1,
     "self-test fixture server: a PROTOCOL_TLS_SERVER context for a local listener serving a"
     " freshly generated certificate; server-side, so it verifies no peer; no client context"),
    ("tools/check_portability.py", "<module>", "shell", 1,
     _SAVED + " (the injectable runner default of _list_tracked, which passes a git argv list)"),
    ("tools/check_manifest.py", "_self_test_main_isolated", "shell", 1,
     "self-test precondition: __import__('subprocess').run with a literal git --version argv"
     " list, no shell"),
    ("tools/check_byte_canon.py", "self_test_main", "shell", 1, _SAVED),
    ("tools/check_ci_parity.py", "_stubs_prepared_before_pool", "shell", 1, _SAVED),
    ("tools/check_ci_parity.py", "self_test", "shell", 1, _SAVED),
    ("tools/check_gensrc_failclose.py", "_self_test_main_isolated", "shell", 1, _SAVED),
    ("tools/check_release_build.py", "_self_test_main_isolated", "shell", 1, _SAVED),
    ("tools/check_release_delta.py", "_self_test_main_isolated", "shell", 1, _SAVED),
    ("opf/tools/_opf_adopt_observe.py", "_runner_red_checks", "shell", 2, _SAVED),
    ("opf/tools/_opf_pack_manifest.py", "_runner_red_checks", "shell", 3, _SAVED),
    ("opf/tools/check_opf_doctor.py", "_self_test_isolated._doctor_suite", "shell", 3, _SAVED),
    ("opf/tools/check_opf_drift.py", "_self_test_isolated._drift_suite", "shell", 4, _SAVED),
    ("opf/tools/check_opf_init_p0.py", "runner_red_checks", "shell", 3, _SAVED),
)


class CannotEvaluate(Exception):
    pass


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))


def _literal_command(node):
    if _is_str(node):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_str(e) for e in node.elts)
    return False


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node):
    return isinstance(node, ast.Constant) and not node.value


def _verify_ok(node):
    """A verify-style value passes only as a literal that is not False or 0 (True, a CA path)."""
    return isinstance(node, ast.Constant) and node.value is not False and node.value != 0


def _effective_keywords(call, builtin_dict):
    """The call's keywords as (name, value) pairs in source order; a ** spread of a literal dict
    with string keys, or of dict(key=value) when the name dict can only be the builtin, is read
    as its keys; any other ** spread is one (None, value) pair."""
    out = []
    for kw in call.keywords:
        value = kw.value
        if kw.arg is not None:
            out.append((kw.arg, value))
        elif isinstance(value, ast.Dict) and all(
                isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys):
            out.extend((k.value, v) for k, v in zip(value.keys, value.values))
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id == "dict" and builtin_dict and not value.args \
                and all(k.arg is not None for k in value.keywords):
            out.extend((k.arg, k.value) for k in value.keywords)
        else:
            out.append((None, value))
    return out


def _kw(pairs, name):
    """The value Python binds to the keyword: the LAST pair for it, as a literal dict keeps its
    last duplicate key (a keyword given twice across a spread is a TypeError, never a call)."""
    found = None
    for key, value in pairs:
        if key == name:
            found = value
    return found


def _all_args(args):
    return args.posonlyargs + args.args + args.kwonlyargs + [
        a for a in (args.vararg, args.kwarg) if a is not None]


def _scope_parts(node):
    """(kind, outer, inner) for a scope-creating node: outer is evaluated in the enclosing
    scope (decorators, defaults, annotations, bases, a comprehension's first iterable), inner
    in its own."""
    type_params = list(getattr(node, "type_params", None) or [])
    if isinstance(node, ast.Lambda):
        a = node.args
        return "function", a.defaults + [d for d in a.kw_defaults if d is not None], [node.body]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        outer = (node.decorator_list + a.defaults + [d for d in a.kw_defaults if d is not None]
                 + type_params + [x.annotation for x in _all_args(a) if x.annotation is not None]
                 + ([node.returns] if node.returns is not None else []))
        return "function", outer, node.body
    if isinstance(node, ast.ClassDef):
        return "class", node.decorator_list + node.bases + node.keywords + type_params, node.body
    gens = node.generators
    inner = [gens[0].target] + gens[0].ifs
    for gen in gens[1:]:
        inner += [gen.target, gen.iter] + gen.ifs
    inner += [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
    return "comprehension", [gens[0].iter], inner


class _Scope:
    def __init__(self, kind, parent):
        self.kind = kind
        self.parent = parent
        self.bindings = {}
        self.globals = set()
        self.nonlocals = set()


class _Binder(ast.NodeVisitor):
    """Pass one: every binding of every name, per scope. An import binds its dotted target; any
    other binding (assignment, parameter, def, class, for, with, except, match, del) binds None."""

    def __init__(self):
        self.module = _Scope("module", None)
        self.current = self.module
        self.scope_of = {}

    def bind(self, name, value):
        scope = self.current
        if name in scope.globals:
            scope = self.module
        elif name in scope.nonlocals:
            # Every enclosing function is a candidate: the one that owns the name may bind it
            # only later in source order, after this pass has visited the nested function.
            scope = scope.parent
            while scope.parent is not None:
                if scope.kind == "function":
                    scope.bindings.setdefault(name, set()).add(value)
                scope = scope.parent
            return
        scope.bindings.setdefault(name, set()).add(value)

    def _enter(self, node):
        kind, outer, inner = _scope_parts(node)
        if not isinstance(node, ast.Lambda) and hasattr(node, "name"):
            self.bind(node.name, None)
        for child in outer:
            self.visit(child)
        saved = self.current
        self.current = self.scope_of[id(node)] = _Scope(kind, saved)
        if kind == "function":
            for arg in _all_args(node.args):
                self.bind(arg.arg, None)
        for child in inner:
            self.visit(child)
        self.current = saved

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = _enter
    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _enter

    def visit_Global(self, node):
        self.current.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.current.nonlocals.update(node.names)

    def visit_Import(self, node):
        for item in node.names:
            if item.asname:
                self.bind(item.asname, item.name)
            else:
                head = item.name.split(".")[0]
                self.bind(head, head)

    def visit_ImportFrom(self, node):
        for item in node.names:
            if item.name != "*":
                target = node.module + "." + item.name if node.module and not node.level else None
                self.bind(item.asname or item.name, target)

    def visit_Name(self, node):
        if not isinstance(node.ctx, ast.Load):
            self.bind(node.id, None)

    def visit_NamedExpr(self, node):
        saved = self.current
        while self.current.kind == "comprehension":
            self.current = self.current.parent
        self.bind(node.target.id, None)
        self.current = saved
        self.visit(node.value)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name, None)
        self.generic_visit(node)

    def _capture(self, node):
        for attr in ("name", "rest"):
            if getattr(node, attr, None):
                self.bind(getattr(node, attr), None)
        self.generic_visit(node)

    visit_MatchAs = visit_MatchStar = visit_MatchMapping = _capture


def _positional(call, index):
    """The positional argument at index, or None when it is absent or a * spread hides it."""
    for arg in call.args[:index + 1]:
        if isinstance(arg, ast.Starred):
            return None
    return call.args[index] if len(call.args) > index else None


def _first(*nodes):
    for node in nodes:
        if node is not None:
            return node
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, disabled, binder, flat):
        self.disabled = frozenset(disabled)
        self.binder = binder
        self.flat = flat
        self.scope = binder.module
        self.qual = []
        self.findings = []
        self.called = set()
        self.based = set()

    def on(self, check):
        return check not in self.disabled

    def add(self, node, kind, check, detail):
        qual = ".".join(self.qual) if self.qual else "<module>"
        self.findings.append((getattr(node, "lineno", 0), qual, kind, check, detail))

    def candidates(self, name, fallback=True):
        """Every binding the bare name may hold at this point (a dotted import target, or None
        for any other value); a name that may be unbound here also falls back to the builtin."""
        if not self.on("alias"):
            return {name}
        if not self.on("scope"):
            return {self.flat.get(name, name)}
        start = scope = self.binder.module if name in self.scope.globals else self.scope
        out = set()
        while scope is not None:
            if scope.kind == "class" and scope is not start:
                scope = scope.parent
                continue
            if name in scope.bindings:
                out |= scope.bindings[name]
                if scope.kind == "function":
                    return out
            scope = scope.parent
        if fallback and name in BUILTIN_NAMES:
            out.add("builtins" if name == "__builtins__" else name)
        return out

    def resolve(self, node):
        """Every dotted name the node may name; a binding the scan cannot name is dropped, which
        is sound for sink detection only (a reference is a finding if ANY candidate is a sink)."""
        return {name for name in self._names(node) if name is not None}

    def _names(self, node):
        """As resolve, but None stands for any value the scan cannot name (an assignment, a
        parameter, a call result), so a proof of a safe value can see it is not proven."""
        if isinstance(node, ast.Name):
            out = set(self.candidates(node.id)) or {None}
        elif isinstance(node, ast.Attribute):
            out = {None if base is None else base + "." + node.attr
                   for base in self._names(node.value)}
        else:
            return {None}
        return out | {alias for name in out if name is not None for alias in self._chain(name)}

    def _chain(self, name):
        """A sink module reached as an attribute of another module names the sink module itself:
        shutil.os.system is os.system and pydoc.builtins.exec is builtins.exec."""
        if not self.on("chain"):
            return []
        parts = name.split(".")
        # A member named after its own module (timeit.timeit, code.code) is not a re-export.
        return [".".join(parts[i:]) for i in range(1, len(parts))
                if parts[i] in STAR_MODULES and parts[i] != parts[i - 1]]

    def proves(self, node, allowed):
        """True only when every value the node may hold is a name in allowed (or a chain to one);
        an absent node, an expression, or any binding the scan cannot name proves nothing."""
        names = self._names(node) if node is not None else {None}
        return None not in names and all(
            name in allowed or set(self._chain(name)) & allowed for name in names)

    def _enter(self, node):
        _kind, outer, inner = _scope_parts(node)
        for child in outer:
            self.visit(child)
        saved = self.scope
        self.scope = self.binder.scope_of[id(node)]
        named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        if named:
            self.qual.append(node.name)
        for child in inner:
            self.visit(child)
        if named:
            self.qual.pop()
        self.scope = saved

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = _enter
    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _enter

    def visit_ImportFrom(self, node):
        if node.module in STAR_MODULES and not node.level and self.on("star-import") \
                and any(item.name == "*" for item in node.names):
            self.add(node, STAR_MODULES[node.module], "star-import",
                     "star import from " + node.module + " hides its sinks")

    def _pairs(self, items):
        """A name or attribute followed by a string literal, in a call's positional arguments or
        a tuple or list display, is read as that attribute (the monkeypatch idiom (os, "stat")
        names os.stat): it is judged as a reference to it, and the module is its base."""
        for first, second in zip(items, items[1:]):
            if isinstance(first, (ast.Name, ast.Attribute)) and isinstance(second, ast.Constant) \
                    and isinstance(second.value, str):
                self.based.add(id(first))
                for base in sorted(self.resolve(first)):
                    self._reference_name(first, base + "." + second.value, False)
                self._tls_ref(first, {second.value})

    def visit_Tuple(self, node):
        self._pairs(node.elts)
        self.generic_visit(node)

    visit_List = visit_Tuple

    def visit_Call(self, node):
        self.called.add(id(node.func))
        pairs = _effective_keywords(node, self.candidates("dict") == {"dict"})
        names = self.resolve(node.func)
        # A store, delete, patch or test of a literal attribute reads no sink (judged below).
        if not names & (_SETATTR | _DELATTR | _PATCH_OBJECT | _HASATTR):
            self._pairs(node.args)
        for name in sorted(names):
            self._judge_call(node, name, pairs)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in LOADER_METHODS \
                and self.on("code-loader"):
            self.add(node, "code", "code-loader", func.attr + " runs a loaded module's code")
        if isinstance(func, ast.Attribute) and func.attr == "subprocess_shell" \
                and self.on("shell-always"):
            command = _first(_positional(node, 1), _kw(pairs, "cmd"))
            if command is None or not _literal_command(command):
                self.add(node, "shell", "shell-always",
                         "subprocess_shell over a non-literal command")
        self._judge_keywords(node, pairs)
        self.generic_visit(node)

    def _judge_call(self, node, name, pairs):
        if name in YAML_LOAD and self.on("dsz-yaml"):
            loader = _first(_kw(pairs, "Loader"), _positional(node, 1))
            if not self.proves(loader, YAML_SAFE_LOADERS):
                self.add(node, "deserialize", "dsz-yaml", name + " without a safe Loader")
        if name in SUBPROCESS_FUNCS and self.on("shell-subprocess"):
            shell = _kw(pairs, "shell")
            if shell is not None:
                shell_on = not _is_false(shell)
            else:
                ninth = node.args[8] if len(node.args) > 8 else None
                shell_on = (any(key is None for key, _value in pairs)
                            or any(isinstance(arg, ast.Starred) for arg in node.args)
                            or (ninth is not None and not _is_false(ninth)))
            command = _first(_positional(node, 0), _kw(pairs, "args"))
            if shell_on and (command is None or not _literal_command(command)):
                self.add(node, "shell", "shell-subprocess",
                         name + " with shell on (or unseen) over a non-literal command")
        if name in ALWAYS_SHELL and self.on("shell-always"):
            command = _first(_positional(node, 0), _kw(pairs, "cmd"), _kw(pairs, "command"))
            if command is None or not _literal_command(command):
                self.add(node, "shell", "shell-always", name + " over a non-literal command")
        if name == SSL_CONTEXT and self.on("tls-context"):
            protocol = _first(_positional(node, 0), _kw(pairs, "protocol"))
            if not self.proves(protocol, {"ssl.PROTOCOL_TLS_CLIENT"}):
                self.add(node, "tls", "tls-context",
                         "ssl.SSLContext without ssl.PROTOCOL_TLS_CLIENT (no verification)")
        if name in _HASATTR and _positional(node, 0) is not None:
            # hasattr returns a bool, so its target never escapes, whatever the name.
            self.based.add(id(_positional(node, 0)))
        if name in _GETATTR or name in _DELATTR or name in _SETATTR or name in _PATCH_OBJECT:
            # A literal attribute name makes the target an attribute base (getattr(m, "x") is
            # m.x); a non-literal one leaves the target a bare use, so a sink module there is
            # an alias the scan cannot follow (module-escape in visit_Name).
            target, attr = _positional(node, 0), _positional(node, 1)
            if isinstance(attr, ast.Constant) and isinstance(attr.value, str) \
                    and target is not None:
                self.based.add(id(target))
                if name in _SETATTR:
                    self._check_store(attr.value, _positional(node, 2), node, True)
        if name in _IMPORTERS and self.on("module-escape"):
            target = _first(_positional(node, 0), _kw(pairs, "name"))
            if isinstance(target, ast.Constant) and isinstance(target.value, str) \
                    and target.value.split(".")[0] in STAR_MODULES:
                self.add(node, STAR_MODULES[target.value.split(".")[0]], "module-escape",
                         "dynamic import of sink module " + target.value)

    def _judge_keywords(self, node, pairs):
        if self.on("tls-verify"):
            for key in ("verify", "verify_ssl"):
                value = _kw(pairs, key)
                if value is not None and not _verify_ok(value):
                    self.add(node, "tls", "tls-verify", key + "= is False, 0 or not a literal")
            value = _kw(pairs, "ssl")
            if isinstance(value, ast.Constant) and (value.value is False or value.value == 0):
                self.add(node, "tls", "tls-verify", "ssl= is False or 0")
        if self.on("tls-hostname"):
            value = _kw(pairs, "check_hostname")
            if value is not None and not _is_true(value):
                self.add(node, "tls", "tls-hostname", "check_hostname= is not True")
        if self.on("tls-verify-mode"):
            for key in ("verify_mode", "cert_reqs"):
                value = _kw(pairs, key)
                if value is not None and not self.proves(value, {"ssl.CERT_REQUIRED"}):
                    self.add(node, "tls", "tls-verify-mode", key + "= is not ssl.CERT_REQUIRED")
        if self.on("dsz-numpy"):
            value = _kw(pairs, "allow_pickle")
            if value is not None and not _is_false(value):
                self.add(node, "deserialize", "dsz-numpy", "allow_pickle= is not literal False")

    def _check_store(self, attr, value, node, attribute):
        if attr == "check_hostname" and self.on("tls-hostname") \
                and (value is None or not _is_true(value)):
            self.add(node, "tls", "tls-hostname", "check_hostname set to a value other than True")
        if attr == "verify_mode" and self.on("tls-verify-mode") \
                and not self.proves(value, {"ssl.CERT_REQUIRED"}):
            self.add(node, "tls", "tls-verify-mode",
                     "verify_mode set to a value other than ssl.CERT_REQUIRED")
        if attr == "verify" and attribute and self.on("tls-verify") \
                and (value is None or not _verify_ok(value)):
            self.add(node, "tls", "tls-verify", ".verify set to False, 0 or not a literal")
        if attr in TLS_ATTRS and attribute and self.on("tls-ref"):
            self.add(node, "tls", "tls-ref", "assignment to " + attr)

    def _target(self, target, value, node):
        if isinstance(target, ast.Attribute):
            self._check_store(target.attr, value, node, True)
        elif isinstance(target, ast.Name):
            self._check_store(target.id, value, node, False)

    def visit_Assign(self, node):
        for target in node.targets:
            for leaf in ast.walk(target):
                if isinstance(leaf, (ast.Attribute, ast.Name)) \
                        and isinstance(leaf.ctx, ast.Store):
                    self._target(leaf, node.value if leaf is target else None, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self._target(node.target, node.value, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._target(node.target, None, node)
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self._target(node.target, node.value, node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self.based.add(id(node.value))
        if isinstance(node.ctx, ast.Load):
            names = self._reference(node, node.attr)
            self._escape(node, names)
        self.generic_visit(node)

    def visit_Name(self, node):
        if not isinstance(node.ctx, ast.Load):
            return
        names = self._reference(node, None)
        imported = self.candidates(node.id, False) if self.on("alias") else set()
        self._escape(node, names & imported | {alias for name in imported if name
                                               for alias in self._chain(name)})

    def _escape(self, node, names):
        """A sink module used other than as an attribute base (m.x, or the target of a literal
        getattr, hasattr, delattr or setattr) is an alias the scan cannot follow: bound to any
        other name, passed, returned, held, defaulted, matched or tested, it is a finding."""
        if id(node) in self.based or not self.on("module-escape"):
            return
        for name in sorted(names & set(STAR_MODULES)):
            self.add(node, STAR_MODULES[name], "module-escape",
                     "sink module " + name + " used other than as an attribute base (an alias)")

    def _reference(self, node, attr):
        names = self.resolve(node)
        called = id(node) in self.called
        for name in sorted(names):
            self._reference_name(node, name, called)
        self._tls_ref(node, {attr} if attr is not None
                      else {node.id} | {n.rsplit(".", 1)[-1] for n in names})
        return names

    def _tls_ref(self, node, lasts):
        for last in sorted(lasts & TLS_ATTRS):
            if self.on("tls-ref"):
                self.add(node, "tls", "tls-ref", "reference to " + last)

    def _reference_name(self, node, name, called):
        if name in DESERIALIZE_SINKS and self.on("dsz-ref"):
            self.add(node, "deserialize", "dsz-ref", "unsafe deserializer " + name)
        if name in CODE_SINKS and self.on("code-ref"):
            self.add(node, "code", "code-ref", "reference to " + name)
        if called or name not in CALL_JUDGED:
            return
        if name in YAML_LOAD:
            kind, check = "deserialize", "dsz-yaml"
        elif name == SSL_CONTEXT:
            kind, check = "tls", "tls-context"
        elif name in SUBPROCESS_FUNCS:
            kind, check = "shell", "shell-subprocess"
        else:
            kind, check = "shell", "shell-always"
        if self.on(check):
            self.add(node, kind, check, name + " referenced without a call (an alias)")


def _flat_aliases(tree):
    """The scope-blind map the "scope" check replaces: one binding per name, last wins."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                head = item.name.split(".")[0]
                aliases[item.asname or head] = item.name if item.asname else head
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = node.module + "." + item.name
    return aliases


def scan_source(source, filename="<source>", disabled=()):
    """Return the sorted findings for one source text (str, or bytes parsed as Python decodes
    them); SyntaxError, ValueError and RecursionError propagate."""
    tree = ast.parse(source, filename=filename)
    binder = _Binder()
    binder.visit(tree)
    scanner = _Scanner(disabled, binder, _flat_aliases(tree))
    scanner.visit(tree)
    return sorted(set(scanner.findings))


def _read_bytes(path):
    return Path(path).read_bytes()


def _decode(data, rel, disabled):
    """The source exactly as Python compiles it: tokenize.detect_encoding honours a BOM and a
    PEP 263 coding cookie, and a declared encoding other than utf-8 is cannot-evaluate (a
    UTF-8 reading of such a file can be a different program). The bytes are returned, so
    ast.parse applies the same cookie and BOM; UnicodeDecodeError propagates as unreadable."""
    if "fail-encoding" in disabled:
        return data.decode("utf-8")
    try:
        encoding, _lines = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as exc:
        raise CannotEvaluate("cannot detect the source encoding of " + rel + ": " + str(exc))
    if encoding not in ("utf-8", "utf-8-sig"):
        raise CannotEvaluate(rel + " declares source encoding " + encoding
                             + "; only utf-8 is evaluated")
    data.decode(encoding)
    return data


def _python_files(root, disabled):
    def rel(path):
        return Path(path).relative_to(root).as_posix()

    def linked(path):
        """Refuse a symlink (or an entry the scan cannot stat) inside a scanned root."""
        try:
            mode = os.lstat(path).st_mode
        except OSError as exc:
            if "fail-walk" in disabled:
                return True
            raise CannotEvaluate("cannot stat " + rel(path) + ": " + str(exc))
        if stat.S_ISLNK(mode) and "fail-symlink" not in disabled:
            raise CannotEvaluate("symlink inside a scanned root: " + rel(path))
        return False

    def walk_error(exc):
        raise CannotEvaluate("cannot list a directory inside a scanned root: " + str(exc))

    def listing(path):
        """The entries of a directory plugin discovery descends: one it cannot list is
        cannot-evaluate, never read as empty or absent."""
        try:
            return [path / name for name in sorted(os.listdir(path))]
        except OSError as exc:
            if "fail-walk" in disabled:
                return []
            raise CannotEvaluate("cannot list a directory inside a scanned root: "
                                 + rel(path) + ": " + str(exc))

    def subdirs(path):
        """The real subdirectories of path; a symlink or an unstatable entry is refused."""
        return [entry for entry in listing(path)
                if not linked(entry) and stat.S_ISDIR(os.lstat(entry).st_mode)]

    roots = [root / sub for sub in SCAN_ROOTS]
    plugin = root / PLUGIN_ROOT
    if plugin.is_dir():
        # Every plugin directory and every hooks directory is listed, so an unlistable or
        # unsearchable one is cannot-evaluate rather than a plugin that seems to have no hooks.
        for entry in ([] if linked(plugin) else subdirs(plugin)):
            for hooks in subdirs(entry):
                if hooks.name == PLUGIN_SCRIPTS[0]:
                    roots.extend(s for s in subdirs(hooks) if s.name == PLUGIN_SCRIPTS[1])
    elif "fail-root" not in disabled:
        raise CannotEvaluate("scan root missing: " + PLUGIN_ROOT)
    files = []
    onerror = None if "fail-walk" in disabled else walk_error
    for base in roots:
        if not base.is_dir():
            if "fail-root" in disabled:
                continue
            raise CannotEvaluate("scan root missing: " + rel(base))
        if linked(base):
            continue
        for current, dirnames, filenames in os.walk(base, onerror=onerror, followlinks=False):
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


def scan_tree(root, allowlist=ALLOWLIST, disabled=()):
    """Return the finding lines for the tree under root; raise CannotEvaluate when it cannot."""
    root = Path(root)
    disabled = frozenset(disabled)
    if "allowlist-shape" not in disabled:
        _check_allowlist(allowlist)
    hits = []
    try:
        paths = _python_files(root, disabled)
    except OSError as exc:
        raise CannotEvaluate("cannot walk the scan roots: " + str(exc))
    for path in paths:
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
            source = _decode(_read_bytes(path), rel, disabled)
        except (OSError, UnicodeDecodeError) as exc:
            if "fail-read" in disabled:
                continue
            raise CannotEvaluate("cannot read " + rel + ": " + str(exc))
        try:
            found = scan_source(source, rel, disabled)
        except (SyntaxError, ValueError, RecursionError) as exc:
            if "fail-parse" in disabled:
                continue
            raise CannotEvaluate("cannot parse " + rel + ": " + type(exc).__name__ + " " + str(exc))
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
# finding with any one of its checks disabled, so every vector fails without its check. Each
# branch of a check has its own vector, so a branch removed is a vector no longer detected.
POSITIVE_VECTORS = (
    ("import pickle\npickle.loads(b'')\n", ("dsz-ref",)),
    ("import marshal\nmarshal.loads(b'')\n", ("dsz-ref",)),
    ("import shelve\nshelve.open('db')\n", ("dsz-ref",)),
    ("import pickle\nreader = pickle.load\n", ("dsz-ref",)),
    ("import yaml\nyaml.unsafe_load(text)\n", ("dsz-ref",)),
    ("import yaml\nyaml.UnsafeLoader(text).get_single_data()\n", ("dsz-ref",)),
    ("import pickle as p\np.loads(b'')\n", ("alias", "dsz-ref")),
    ("from pickle import loads\nloads(b'')\n", ("alias", "dsz-ref")),
    ("import pickle\ngetattr(pickle, 'loads')(data)\n", ("dsz-ref",)),
    ("import yaml\nyaml.load(text)\n", ("dsz-yaml",)),
    ("import yaml\nyaml.load(text, Loader=chosen)\n", ("dsz-yaml",)),
    ("import yaml\nyaml.load(text, chosen)\n", ("dsz-yaml",)),
    ("import yaml\nloader = yaml.load_all\n", ("dsz-yaml",)),
    ("import numpy\nnumpy.load(path, allow_pickle=True)\n", ("dsz-numpy",)),
    ("import requests\nrequests.get(url, verify=False)\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, verify=0)\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, verify=flag)\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, **{'verify': False})\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, **dict(verify=False))\n", ("tls-verify",)),
    ("import requests\nrequests.get(url, **{'verify': True, 'verify': False})\n", ("tls-verify",)),
    ("session.verify = False\n", ("tls-verify",)),
    ("client.get(url, verify_ssl=False)\n", ("tls-verify",)),
    ("connect(host, ssl=False)\n", ("tls-verify",)),
    ("import ssl\nctx = ssl._create_unverified_context()\n", ("tls-ref",)),
    ("import ssl\nmode = ssl.CERT_NONE\n", ("tls-ref",)),
    ("from ssl import CERT_NONE as none\nmode = none\n", ("alias", "tls-ref")),
    ("mode = CERT_NONE\n", ("tls-ref",)),
    ("import ssl\ngetattr(ssl, '_create_unverified_context')()\n", ("tls-ref",)),
    ("import ssl\nfactories = [(ssl, '_create_unverified_context')]\n", ("tls-ref",)),
    ("handler._create_default_https_context = factory\n", ("tls-ref",)),
    ("import ssl\nssl.create_default_context(ssl.Purpose.CLIENT_AUTH)\n", ("tls-ref",)),
    ("import ssl\nctx = ssl.SSLContext(ssl.PROTOCOL_TLS)\n", ("tls-context",)),
    ("import ssl\nctx = ssl.SSLContext()\n", ("tls-context",)),
    ("import ssl\nfactory = ssl.SSLContext\n", ("tls-context",)),
    # A safe-looking import later overwritten in the same scope proves nothing.
    ("import ssl\ndef f():\n    from ssl import PROTOCOL_TLS_CLIENT as protocol\n"
     "    protocol = 2\n    return ssl.SSLContext(protocol)\n", ("tls-context",)),
    ("import yaml\ndef f():\n    from yaml import SafeLoader as L\n    L = make()\n"
     "    return yaml.load(text, Loader=L)\n", ("dsz-yaml",)),
    ("def f(ctx):\n    from ssl import CERT_REQUIRED as mode\n    mode = 0\n"
     "    ctx.verify_mode = mode\n", ("tls-verify-mode",)),
    ("import http.client\nhttp.client.ssl.SSLContext()\n", ("chain", "tls-context")),
    ("ctx.check_hostname = False\n", ("tls-hostname",)),
    ("class Ctx:\n    check_hostname = chosen\n", ("tls-hostname",)),
    ("make_context(check_hostname=False)\n", ("tls-hostname",)),
    ("setattr(ctx, 'check_hostname', False)\n", ("tls-hostname",)),
    ("ctx.check_hostname: bool = flag\n", ("tls-hostname",)),
    ("if (check_hostname := False):\n    pass\n", ("tls-hostname",)),
    ("ctx.verify_mode = chosen\n", ("tls-verify-mode",)),
    ("ctx.verify_mode |= extra\n", ("tls-verify-mode",)),
    ("wrap(sock, cert_reqs=chosen)\n", ("tls-verify-mode",)),
    ("make(verify_mode=chosen)\n", ("tls-verify-mode",)),
    ("import subprocess\nsubprocess.run(cmd, shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(f'ls {name}', shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(['ls', name], shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(args=cmd, shell=True)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.Popen(cmd, **opts)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(cmd, shell=use_shell)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(cmd, shell=1)\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.run(cmd, **{'shell': False, 'shell': True})\n",
     ("shell-subprocess",)),
    ("import subprocess\ndef dict(**kw):\n    return {'shell': True}\n"
     "subprocess.run(cmd, **dict(shell=False))\n", ("shell-subprocess",)),
    ("import subprocess\nsubprocess.call(cmd, -1, None, None, None, None, None, True, True)\n",
     ("shell-subprocess",)),
    ("import subprocess\nsubprocess.Popen(*[cmd, -1, None, None, None, None, None, True, True])\n",
     ("shell-subprocess",)),
    ("import subprocess\nr = subprocess.run\nr(cmd, shell=True, check=True)\n",
     ("shell-subprocess",)),
    ("import subprocess\ngetattr(subprocess, 'run')(cmd, shell=True)\n", ("shell-subprocess",)),
    ("from subprocess import run as r\nr(cmd, shell=True)\n", ("alias", "shell-subprocess")),
    ("import os\nos.system(cmd)\n", ("shell-always",)),
    ("import os\nos.system(*argv)\n", ("shell-always",)),
    ("import os\nos.popen('ls ' + path)\n", ("shell-always",)),
    ("import subprocess\nsubprocess.getoutput(cmd)\n", ("shell-always",)),
    ("import asyncio\nasyncio.create_subprocess_shell(command=cmd)\n", ("shell-always",)),
    ("import os\nlaunch = os.system\n", ("shell-always",)),
    ("from os import system\nsystem(cmd)\n", ("alias", "shell-always")),
    ("loop.subprocess_shell(factory, cmd)\n", ("shell-always",)),
    ("import shutil\nshutil.os.system(cmd)\n", ("chain", "shell-always")),
    ("import posix\nposix.system(cmd)\n", ("shell-always",)),
    ("from asyncio.subprocess import create_subprocess_shell as s\ns(cmd)\n",
     ("alias", "shell-always")),
    ("import os\ntable = [(os, 'system')]\n", ("shell-always",)),
    ("eval(text)\n", ("code-ref",)),
    ("def f(text):\n    exec(text)\n", ("code-ref",)),
    ("run = exec\n", ("code-ref",)),
    ("import builtins as b\nb.exec(text)\n", ("alias", "code-ref")),
    ("__builtins__.exec(text)\n", ("alias", "code-ref")),
    ("import types\ntypes.FunctionType(compile(text, 'x', 'exec'), {})()\n", ("code-ref",)),
    ("import pydoc\npydoc.builtins.exec(text)\n", ("chain", "code-ref")),
    ("spec.loader.exec_module(module)\n", ("code-loader",)),
    ("loader.load_module()\n", ("code-loader",)),
    ("from logging.handlers import pickle\npickle.loads(data)\n", ("chain", "dsz-ref")),
    ("from pickle import *\nloads(b'')\n", ("star-import",)),
    ("import subprocess\nm = subprocess\nm.run(cmd, shell=True)\n", ("module-escape",)),
    ("import pickle\ngetattr(pickle, name)(data)\n", ("module-escape",)),
    ("import importlib\nimportlib.import_module('pickle')\n", ("module-escape",)),
    ("import importlib\nimportlib.import_module(name='pickle')\n", ("module-escape",)),
    # A sink module used other than as an attribute base, in each binding form.
    ("import pickle\na, b = pickle, 1\n", ("module-escape",)),
    ("import subprocess\nm: object = subprocess\n", ("module-escape",)),
    ("import pickle\nif (m := pickle):\n    pass\n", ("module-escape",)),
    ("import subprocess\nm = subprocess if c else subprocess\nm.run(cmd, shell=True)\n",
     ("module-escape",)),
    ("import pickle\nm = c or pickle\n", ("module-escape",)),
    ("import os\ndef f(m=os):\n    m.system(cmd)\n", ("module-escape",)),
    ("import os\ng = lambda m=os: m.system(cmd)\n", ("module-escape",)),
    ("import pickle\nmatch pickle:\n    case m:\n        m.loads(d)\n", ("module-escape",)),
    ("import os\nrun(os)\n", ("module-escape",)),
    ("import shutil\nm = shutil.os\n", ("chain", "module-escape")),
    # Scope: a binding in another scope never hides the one Python resolves here.
    ("import pickle\ndef _u():\n    import json as pickle\npickle.loads(data)\n",
     ("scope", "dsz-ref")),
    ("from pickle import loads as decode\ndef f():\n    from json import loads as decode\n"
     "decode(data)\n", ("scope", "dsz-ref")),
    ("import subprocess\nif False:\n    import shlex as subprocess\n"
     "subprocess.run(cmd, shell=True)\n", ("scope", "shell-subprocess")),
    ("import os\ndef f():\n    from pathlib import Path as os\nos.system(cmd)\n",
     ("scope", "shell-always")),
    ("def outer():\n    import pickle as p\n    def inner():\n        return p.loads(x)\n",
     ("alias", "dsz-ref")),
    ("def f():\n    global p\n    import pickle as p\ndef g():\n    return p.loads(x)\n",
     ("alias", "dsz-ref")),
    ("def outer():\n    def inner():\n        nonlocal p\n        import pickle as p\n"
     "    p = None\n    inner()\n    return p.loads(x)\n", ("alias", "dsz-ref")),
)

# Negative vectors: the safe spellings each check must leave alone.
NEGATIVE_VECTORS = (
    "import yaml\nyaml.load(text, Loader=yaml.SafeLoader)\nyaml.safe_load(text)\n"
    "yaml.load(text, yaml.CSafeLoader)\n",
    "import json\njson.loads(text)\n",
    "import subprocess\nsubprocess.run(['git', 'status'])\nsubprocess.run(cmd)\n",
    "import subprocess\nsubprocess.run('ls -l', shell=True)\nsubprocess.run(cmd, shell=False)\n"
    "subprocess.run(args='ls -l', shell=True)\nsubprocess.run(['ls', '-l'], shell=True)\n",
    "import subprocess\nsubprocess.run(cmd, -1, None, None, None, None, None, True, False)\n"
    "subprocess.run(cmd, **{'shell': False})\nsubprocess.run(cmd, **dict(shell=False))\n",
    "import os\nos.system('true')\n",
    "import os, subprocess\nflag = getattr(os, 'O_NOFOLLOW', 0)\nok = hasattr(subprocess, 'run')\n",
    "import requests\nrequests.get(url, verify=True)\nrequests.get(url, verify='/ca.pem')\n"
    "requests.get(url, **{'verify': True})\nsession.verify = '/ca.pem'\n",
    "import ssl\nctx.check_hostname = True\nctx.verify_mode = ssl.CERT_REQUIRED\n"
    "ok = ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED\n"
    "setattr(ctx, 'check_hostname', True)\nctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)\n",
    "import asyncio\nasyncio.open_connection(host, port, ssl=ctx)\n",
    "import numpy\nnumpy.load(path, allow_pickle=False)\n",
    "class Ctx:\n    check_hostname = True\n",
    "pattern.exec(text)\nre.compile(text)\n",
    "import pickle\ndef f(pickle):\n    return pickle.loads(x)\n",
    "import os, subprocess, code\nfrom unittest.mock import patch\n"
    "with patch.object(subprocess, 'run', fake):\n    pass\nsetattr(os, 'stat', fake)\n"
    "ok = hasattr(os, name)\ndef f():\n    code = make()\n    alias = code\n",
    "import pickle\ndef f():\n    pickle = Codec()\n    return pickle.loads(x)\n",
    # A class body's binding is not visible inside its methods.
    "class C:\n    from pickle import loads\n    def f(self):\n        return loads(x)\n",
    # An unbound name that is not a builtin names nothing, so no chain is read through it.
    "ok = any(item.code == 'x' for item in items)\n",
    "import http.client\nctx = http.client.ssl.SSLContext(http.client.ssl.PROTOCOL_TLS_CLIENT)\n",
)


# The pinned sink roster the self-test holds every set to, written out independently of the sets:
# a member dropped from a set is a pinned vector no longer detected, and a member added to a set
# without its roster line fails the equality check.
_ROSTER = (
    ("dsz-ref", (
        "_pickle.Unpickler _pickle._Unpickler _pickle._load _pickle._loads _pickle.load "
        "_pickle.loads cPickle.Unpickler cPickle._Unpickler cPickle._load cPickle._loads "
        "cPickle.load cPickle.loads cloudpickle.Unpickler cloudpickle._Unpickler "
        "cloudpickle._load cloudpickle._loads cloudpickle.load cloudpickle.loads dill.Unpickler "
        "dill._Unpickler dill._load dill._loads dill.load dill.loads joblib.load "
        "jsonpickle.decode jsonpickle.unpickler.decode marshal.load marshal.loads "
        "pandas.read_pickle pickle.Unpickler pickle._Unpickler pickle._load pickle._loads "
        "multiprocessing.reduction.ForkingPickler.loads "
        "pickle.load pickle.loads shelve.BsdDbShelf shelve.DbfilenameShelf shelve.Shelf "
        "shelve.Unpickler shelve.open torch.load yaml.CFullLoader yaml.CLoader yaml.CUnsafeLoader "
        "yaml.FullConstructor yaml.FullLoader yaml.Loader yaml.UnsafeConstructor "
        "yaml.UnsafeLoader yaml.constructor.FullConstructor yaml.constructor.UnsafeConstructor "
        "yaml.full_load yaml.full_load_all yaml.loader.FullLoader yaml.loader.Loader "
        "yaml.loader.UnsafeLoader yaml.unsafe_load yaml.unsafe_load_all").split()),
    ("code-ref", (
        "builtins.eval builtins.exec cProfile.run cProfile.runctx code.InteractiveConsole "
        "code.InteractiveInterpreter code.interact eval exec pdb.run pdb.runctx pdb.runeval "
        "profile.run profile.runctx runpy._run_code runpy._run_module_code runpy.run_module "
        "runpy.run_path timeit.Timer timeit.repeat timeit.timeit types.FunctionType "
        "types.LambdaType").split()),
    ("shell-always", (
        "asyncio.create_subprocess_shell asyncio.subprocess.create_subprocess_shell nt.system "
        "os.popen os.system posix.system subprocess.getoutput subprocess.getstatusoutput"
        ).split()),
    ("shell-subprocess", (
        "subprocess.Popen subprocess.call subprocess.check_call subprocess.check_output "
        "subprocess.run").split()),
    ("dsz-yaml", (
        "yaml.load yaml.load_all").split()),
    ("tls-context", (
        "ssl.SSLContext").split()),
    ("tls-ref", (
        "ssl.CERT_NONE ssl.CERT_OPTIONAL ssl.CLIENT_AUTH ssl._create_default_https_context "
        "ssl._create_stdlib_context ssl._create_unverified_context").split()),
    ("star-import", (
        "_pickle asyncio builtins cPickle cProfile cloudpickle code dill joblib jsonpickle "
        "marshal nt os pandas pdb pickle posix profile runpy shelve ssl subprocess timeit "
        "types yaml").split()),
)


def _sink_vectors():
    """One generated vector per pinned roster member: (source, check). A sink-set member is a
    reference to it; a STAR_MODULES member is a star import and an alias of the module."""
    out = []
    for check, names in _ROSTER:
        for name in names:
            if check == "star-import":
                out.append(("from " + name + " import *\n", check))
                out.append(("import " + name + "\nalias = " + name + "\n", "module-escape"))
                continue
            head = "import " + name.rsplit(".", 1)[0] + "\n" if "." in name else ""
            out.append((head + "x = " + name + "\n", check))
    return out


def _roster_drift():
    """The sets that no longer equal their pinned roster line."""
    sets = {"dsz-ref": DESERIALIZE_SINKS, "code-ref": CODE_SINKS, "shell-always": ALWAYS_SHELL,
            "shell-subprocess": SUBPROCESS_FUNCS, "dsz-yaml": YAML_LOAD,
            "tls-context": frozenset([SSL_CONTEXT]),
            "tls-ref": frozenset("ssl." + attr for attr in TLS_ATTRS),
            "star-import": frozenset(STAR_MODULES)}
    return sorted(check for check, names in _ROSTER if frozenset(names) != frozenset(sets[check]))


def _write_tree(base, files):
    for rel in SCAN_ROOTS + (PLUGIN_ROOT + "/p/hooks/scripts", "outside"):
        os.makedirs(os.path.join(base, rel), exist_ok=True)
    for rel, data in files.items():
        os.makedirs(os.path.dirname(os.path.join(base, rel)), exist_ok=True)
        Path(base, rel).write_bytes(data)


def _link(rel, target):
    def setup(base):
        os.symlink(os.path.join(base, target), os.path.join(base, rel))
    return setup


def _drop(rel):
    def setup(base):
        shutil.rmtree(os.path.join(base, rel))
    return setup


def _fifo(rel):
    def setup(base):
        os.mkfifo(os.path.join(base, rel))
    return setup


def _unlisted(rel):
    """Make a directory unlistable; where permissions do not bind (a privileged run), the
    listing is refused by patching os.scandir for that one path instead."""
    def setup(base):
        path = os.path.join(base, rel)
        os.chmod(path, 0)
        real = os.scandir

        real_list = os.listdir

        def refuse(target=".", _real=real, _path=path):
            if os.fspath(target) == _path:
                raise PermissionError(13, "listing refused", _path)
            return _real(target)

        def refuse_list(target=".", _real=real_list, _path=path):
            if os.fspath(target) == _path:
                raise PermissionError(13, "listing refused", _path)
            return _real(target)
        if os.access(path, os.R_OK):
            os.scandir = refuse
            os.listdir = refuse_list

        def undo():
            os.scandir = real
            os.listdir = real_list
            os.chmod(path, 0o755)
        return undo
    return setup


def _unstatable(rel, child="a.py"):
    """Leave a directory listable but its entries unstatable (read, no search permission); where
    permissions do not bind, os.lstat refuses the entries under that path instead."""
    def setup(base):
        path = os.path.join(base, rel)
        os.chmod(path, 0o444)
        real = os.lstat

        def refuse(target, *args, _real=real, _path=path + os.sep, **kwargs):
            if os.fspath(target).startswith(_path):
                raise PermissionError(13, "stat refused", os.fspath(target))
            return _real(target, *args, **kwargs)
        if os.access(os.path.join(path, child), os.R_OK):
            os.lstat = refuse

        def undo():
            os.lstat = real
            os.chmod(path, 0o755)
        return undo
    return setup


def _unreadable(rel):
    """Make a file unreadable; where permissions do not bind, _read_bytes refuses that path."""
    def setup(base):
        path = os.path.join(base, rel)
        os.chmod(path, 0)
        module = sys.modules[__name__]
        real = module._read_bytes

        def refuse(target, _real=real, _path=path):
            if os.fspath(target) == _path:
                raise PermissionError(13, "read refused", _path)
            return _real(target)
        if os.access(path, os.R_OK):
            module._read_bytes = refuse

        def undo():
            module._read_bytes = real
            os.chmod(path, 0o644)
        return undo
    return setup


def _stat_refused(rel):
    """os.stat refuses one listed file (it vanished or lost permission after the walk)."""
    def setup(base):
        path = os.path.join(base, rel)
        real = os.stat

        def refuse(target, *args, _real=real, _path=path, **kwargs):
            if os.fspath(target) == _path:
                raise PermissionError(13, "stat refused", _path)
            return _real(target, *args, **kwargs)
        os.stat = refuse

        def undo():
            os.stat = real
        return undo
    return setup


def _parse_raises(exc_type):
    """scan_source raises exc_type for every file (a parser failure that is not SyntaxError)."""
    def setup(base):
        module = sys.modules[__name__]
        real = module.scan_source

        def refuse(source, filename="<source>", disabled=()):
            raise exc_type("parser refused " + filename)
        module.scan_source = refuse

        def undo():
            module.scan_source = real
        return undo
    return setup


def _tree_outcome(files, allowlist=(), disabled=(), setup=None):
    """The scan's outcome on a synthetic tree: "cannot-evaluate", or the number of findings."""
    with tempfile.TemporaryDirectory() as base:
        _write_tree(base, files)
        undo = setup(base) if setup else None
        try:
            return len(scan_tree(base, allowlist, disabled))
        except CannotEvaluate:
            return "cannot-evaluate"
        except Exception as exc:
            return "crash " + type(exc).__name__
        finally:
            if undo:
                undo()


_SITE = b"def f(text):\n    exec(text)\n"
_REASON = "self-test reason"
_ENTRY = ("tools/a.py", "f", "code", 1, _REASON)
# The QA reproduction: valid UTF-8 whose gbk reading closes the string, so Python runs exec.
_GBK = (b"# -*- coding: gbk -*-\ns = \x22\xe3\x81\x82\x5c\x22; exec(\x27print(1+1)\x27);"
        b" print(\x27X\x27) #\x22\n")
CE = "cannot-evaluate"
# Tree vectors: (name, files, allowlist, setup, expected, check, outcome without the check).
TREE_VECTORS = (
    # Parses, but nests deeper than the visitor can recurse: RecursionError is cannot-evaluate.
    ("nesting past the recursion limit",
     {"tools/deep.py": b"x = " + b"+".join([b"a"] * 3000) + b"\n"}, (), None, CE,
     "fail-parse", 0),
    ("parse failure", {"tools/bad.py": b"def broken(:\n"}, (), None, CE, "fail-parse", 0),
    # Past the two lines detect_encoding reads, so the decode (not the cookie scan) refuses it.
    ("undecodable file", {"tools/bad.py": b"x = 1\ny = 2\nz = '\xff'\n"}, (), None, CE,
     "fail-read", 0),
    ("non-utf-8 coding cookie", {"tools/enc.py": _GBK}, (), None, CE, "fail-encoding", 0),
    ("utf-8 BOM honoured", {"tools/bom.py": b"\xef\xbb\xbf" + _SITE}, (), None, 1,
     "code-ref", 0),
    ("utf-8 cookie honoured", {"tools/u.py": b"# -*- coding: utf-8 -*-\n" + _SITE}, (),
     None, 1, "code-ref", 0),
    ("unreadable file", {"tools/a.py": _SITE}, (), _unreadable("tools/a.py"), CE,
     "fail-read", 0),
    ("unlistable directory", {"tools/sub/a.py": _SITE}, (), _unlisted("tools/sub"), CE,
     "fail-walk", 0),
    ("unstatable entry", {"tools/sub/a.py": _SITE}, (), _unstatable("tools/sub"), CE,
     "fail-walk", 0),
    ("file symlink", {"outside/a.py": _SITE}, (), _link("tools/l.py", "outside/a.py"),
     CE, "fail-symlink", 1),
    ("directory symlink", {"outside/a.py": _SITE}, (), _link("tools/d", "outside"), CE,
     "fail-symlink", 0),
    ("non-regular file", {}, (), _fifo("tools/f.py"), CE, "fail-nonregular", 0),
    ("missing root", {}, (), _drop("opf/tools"), CE, "fail-root", 0),
    ("missing plugin root", {}, (), _drop(PLUGIN_ROOT), CE, "fail-root", 0),
    ("plugin hook script scanned", {"plugin/p/hooks/scripts/h.py": _SITE}, (), None, 1,
     "code-ref", 0),
    ("unlistable plugin directory", {"plugin/p/hooks/scripts/h.py": _SITE}, (),
     _unlisted("plugin/p"), CE, "fail-walk", 0),
    ("unlistable plugin hooks directory", {"plugin/p/hooks/scripts/h.py": _SITE}, (),
     _unlisted("plugin/p/hooks"), CE, "fail-walk", 0),
    ("unsearchable plugin hooks directory", {"plugin/p/hooks/scripts/h.py": _SITE}, (),
     _unstatable("plugin/p/hooks", "scripts"), CE, "fail-walk", 0),
    ("plugin directory symlink", {"outside/hooks/scripts/h.py": _SITE}, (),
     _link("plugin/q", "outside"), CE, "fail-symlink", 0),
    ("file unstatable after the walk", {"tools/a.py": _SITE}, (), _stat_refused("tools/a.py"),
     CE, "fail-nonregular", 0),
    ("parser ValueError", {"tools/a.py": _SITE}, (), _parse_raises(ValueError), CE,
     "fail-parse", 0),
    ("allowlist count drift", {"tools/a.py": _SITE}, (_ENTRY[:3] + (2, _REASON),), None,
     2, "allowlist-stale", 0),
    ("allowlist entry vanished", {"tools/a.py": b"x = 1\n"}, (_ENTRY,), None, 1,
     "allowlist-stale", 0),
    ("allowlist entry without reason", {"tools/a.py": _SITE}, (_ENTRY[:4] + (" ",),),
     None, CE, "allowlist-shape", 0),
    ("allowlist boolean count", {"tools/a.py": _SITE}, (_ENTRY[:3] + (True, _REASON),),
     None, CE, "allowlist-shape", 0),
    ("allowlist zero count", {"tools/a.py": _SITE}, (_ENTRY[:3] + (0, _REASON),), None,
     CE, "allowlist-shape", 2),
    ("allowlist duplicate key", {"tools/a.py": _SITE}, (_ENTRY, _ENTRY), None, CE,
     "allowlist-shape", 0),
    ("allowlist not a tuple", {"tools/a.py": _SITE}, [_ENTRY], None, CE,
     "allowlist-shape", 0),
)


def self_test():
    failures = []
    exercised = set()
    generated = _sink_vectors()
    for source, checks in POSITIVE_VECTORS + tuple((s, (c,)) for s, c in generated):
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
    for name, files, allowlist, setup, expected, check, without in TREE_VECTORS:
        exercised.add(check)
        got = _tree_outcome(files, allowlist, (), setup)
        if got != expected:
            failures.append("tree " + name + ": expected " + repr(expected) + ", got " + repr(got))
        got = _tree_outcome(files, allowlist, (check,), setup)
        if got != without or got == expected:
            failures.append("tree " + name + ": without " + check + " expected " + repr(without)
                            + ", got " + repr(got))
    method = b"class C:\n    def f(self, text):\n        exec(text)\n"
    if _tree_outcome({"tools/a.py": method},
                     (("tools/a.py", "C.f", "code", 1, _REASON),)) != 0:
        failures.append("tree: a reviewed method site (class-qualified) is not admitted")
    if _tree_outcome({"tools/__pycache__/a.py": _SITE}) != 1:
        failures.append("tree: a __pycache__ directory is not scanned")
    if _tree_outcome({"opf/tools/_vendor/v.py": _SITE}) != 0:
        failures.append("tree: the vendored tree is not excluded")
    if _tree_outcome({"tools/a.py": _SITE}) != 1:
        failures.append("tree: an unreviewed site passes")
    drift = _roster_drift()
    if drift:
        failures.append("sink sets differ from the pinned roster: " + ", ".join(drift))
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
    print("SELF-TEST PASS: dangerous-api: {} positive vectors and {} generated sink-set vectors"
          " (each fails without its check), {} negatives, {} fail-closed and allowlist tree"
          " vectors (each changes outcome without its check), 4 admission vectors.".format(
              len(POSITIVE_VECTORS), len(generated), len(NEGATIVE_VECTORS), len(TREE_VECTORS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
