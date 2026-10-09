#!/usr/bin/env python3
"""OPF standalone-closure gate (OPF-SELF-CONTAIN, hold H-12 ratified).

This gate proves, by PHYSICAL ABSENCE, that the `opf/` subtree is dependency-closed: it materializes
`opf/` ALONE into a throwaway temporary directory with NO AIQT checkout reachable, then runs the OPF
tooling's own gate subset there. Because the subset runs under `python3 -I` (isolated: neither the current
working directory nor PYTHONPATH is on sys.path) with only the copied `opf/tools/` directory reachable, any
surviving UPWARD edge into `tools/` (a `from check_versions import _parse`, an `import check_byte_canon`,
the emitter's pinned sibling-file authority load) fails at import or run time
in the isolated copy. Grep evidence is not accepted; closure is OBSERVED, not inferred. This is the durable
check the restructure carries: it fails without the self-containment property (see --self-test).

The two closure-critical edges are lazy or dynamic, so an import-only or `--help` smoke test would miss a
survivor. The subset therefore drives the exact command paths that exercise them: `opf.py --self-test` runs
the render leg (the lazy `_byte_canon` import) and the emitter self-test (the pinned sibling-file authority
load); the commonmark selftests exercise the vendored parser and its manifest.

  check_opf_standalone_closure.py             materialize opf/ alone and run the subset (also the default)
  check_opf_standalone_closure.py --self-test  the same, PLUS a deliberate flip proving it fails without the move

Exit convention (matches the repo's gates):
  0  the isolated opf/ subtree verifies (closure holds)
  1  a real finding (a subset gate failed in isolation: closure is broken). Failure-first: a member
     that ran and failed exits 1 even when other members could not be evaluated; both are listed.
  2  malformed input or a harness error (fail-closed), with no member failing: opf/ absent/unreadable,
     the scratch directory could not be allocated, copy failed, a subset script missing from the copy,
     the interpreter could not be launched, or a subset member KILLED at
     its time bound. Each cannot-evaluate row is reported with a FIXED reason code (TIMEOUT,
     HARNESS ERROR missing-script, HARNESS ERROR interpreter-launch-refused) and fixed repair
     guidance, so distinct harness failures stay distinguishable without printing a runtime
     value. Each is cannot-evaluate: the member never ran to
     completion, so closure was neither observed nor refuted, and it is never reported as a closure
     finding.

--self-test runs in two stages. First the preflight: the registration cases and the stubbed cases (no
subset member runs), each run on its own so that a case that could not be evaluated never hides another
case's refutation. Attribution (see _preflight_exit): no opf/ subtree, a host primitive that failed
(a scratch directory, copy, fixture write, store read, flip write or child launch that raised an
OSError, or a child that did not finish within its bound; _harness_watch lists what is not watched),
malformed input, or any other ordinary exception raised by a case's own code outside its attributed
calls is cannot-evaluate; a crash or a wrong result of the code under test is a refutation.
_check_attribution_inventory checks that attribution where _derived_sites can see it: every load of
a code-under-test name in the code a case reaches is a site, and each site needs a pin entry, fired
at the site's own call instruction and covering its first call, or a recorded exclusion with its
reason; each indirection the walk flags (a lookup by string or namespace, an import in a function,
a module-level value holding this module's code, a load of a wrapper holding an excluded site, a
name an annotation loads) needs a recorded reason, or the inventory is cannot-evaluate; so is an
interpreter that gives no instruction columns (-X no_debug_ranges). What the walk does not see is
listed in _derived_sites. Of what run() executes,
the preflight checks: _SUBSET and its bounds against the independent _DECLARED_ROSTER
(closure/declared-roster); with _run_one stubbed, the arguments run() and the negative leg pass to
it (_check_member_calls: roster in order, the copy's source, payload and location, the scratch
directory's contents, the environment, the stream mode); through the real run() and _run_one
over recording children, what is launched and what each child saw (_check_member_boundary,
_check_launches), and that run() exits 1 listing as FAILED a member that exits 1 and one killed
by SIGKILL. Those runs set GIT_ and PYTHON variables in os.environ (_ambient_sentinels), so
the environment checks do not depend on the host; diagnostics are CLOSED (ruling
D-428-CLOSED-DIAGNOSTICS): every refutation, cannot-evaluate, harness failure, print and stream
write of this module emits only fixed literal text and identifiers drawn from closed vocabularies
this module defines (case ids and labels, member and script names from the declared roster,
report field names, fixed reason codes, integer counts and return codes; the _cv/_cvs/_n gates
check membership at run time and emit a fixed marker for any other token; a registered module
literal must be bound exactly once, at module level, and never rebound or shadowed, with _patched
refusing a runtime rebinding of any protected name), so a runtime value is
never formatted into output: where a diagnostic must say which value was wrong, it names the
field and the check that failed, never the value, and a path is named by its role (the
repository, the given root, the scratch directory), never printed, so no comparison of expected
text against a diagnostic can depend on the host's environment values. closure/closed-sites pins
the rule in this file's AST as an ALLOWLIST (ratified for train 2 round 32; made a real
enumeration for round 33): exactly one closed emitter, the module-level _emit (its statements
pinned verbatim against _EMITTER_SOURCE), plus the canonical floor guard below, may reference an
output or exit channel (print, the sys streams and dunder streams, sys.exit, os.write, os._exit,
exit, quit, SystemExit, warnings, logging, traceback, builtins, __builtins__). Everywhere else the
file is held to enumerations: _MODULE_ATTRS lists every module this file imports and, for each,
the exact dotted attribute paths it may reference, so any attribute chain rooted in an imported
module name that is not enumerated (os.sys, sys.modules, sys.displayhook, json.dump,
subprocess.Popen, io.open) is refused in any context, and a bare module name outside an attribute
access is refused as an alias the walk cannot follow; _FROM_IMPORT_ALLOW lists the only permitted
from-imports; every other import (an unlisted module, an asname, a dotted form, every star
import, importlib, __import__) is refused; eval, exec, compile, globals, vars, locals, input,
help and breakpoint are refused by name anywhere; __dict__, __builtins__, modules and fdopen are
refused as attribute names on any base; getattr, setattr and delattr are refused with a
non-literal or refused-literal name and may not be aliased; open, io.FileIO and os.fdopen are
refused on an int-constant descriptor number; any binding of a name the emitter's int clamp or a
gate depends on (type, int, str, isinstance, frozenset) is refused in every binding form; an
attribute or subscript store rooted at a gate or a gate table is refused; a decorated def of a
gate is refused (the gate decorator allowlist is empty on purpose); every _emit call passes
closed text and a constant err= (code= may be any expression: the emitter exits through an int
clamp, so the interpreter never prints an exit value of this module); exception constructors and
assert messages stay checked wherever they appear, since the interpreter prints an uncaught
exception's text; each refused form carries a flip that fails when its rule is removed, and
_check_closed_sites requires _PROTECTED_PROBES, an independent spelling-out of every protected
name, to equal _PROTECTED_NAMES and probes _patched with each, so a dropped table row fails even
where another rule overlaps it; the deliberate introspection sites of the test machinery itself
(globals in _patched, _nth_call and _derived_sites, dynamic getattr in _Overlay and _nth_call,
__dict__ in _Overlay and _holds_code, the module captures in _launch_recorder and
_failing_harness) are each recorded in _CLOSED_SITE_EXCEPTIONS with a reason; _check_env_canaries drives the refusal, wrong-report and launch paths
with a canary, scanning each sub-check's diagnostics and every captured stream on the return, the
exception and the termination path alike; closure/host-independence re-runs the affected cases in
a child whose USER, LOGNAME and HOME value appears in its TMPDIR path, requires PASS with no
environment value of length 4 or more anywhere in either stream, and requires that the same scan
FAILS a copy of this module in which one diagnostic is reverted to interpolate a value. A
captured child stream is parsed as data and never echoed into a diagnostic: where a refutation
must cite child output, it cites the parsed field name and the check that failed, or a byte
count. A recorded launch passing a keyword that could hand a child a descriptor, missing a
required keyword, or redirecting a stream is refused before the child runs (_launch_recorder).
RESIDUALS the closed design does not cover: the interpreter's own traceback on an uncaught
exception (a defect of this module, or a BaseException such as KeyboardInterrupt, which
_preflight_exit re-raises on purpose) is outside the module's control and carries source paths
and raw exception text; the entry point (main through the emitter's exit at the last line) does
NOT catch it. _self_test_exit relays text that reaches it only as str() of exceptions this
module raised and of leg messages built at closed construction sites (the walk checks every
construction site; that relay and the other permitted unclosed-text sites are recorded in
_CLOSED_SITE_EXCEPTIONS, each with its reason; a channel reference outside the emitter has no
such exception). The RESIDUALS of the allowlist, stated plainly: (1) the pin guards against ACCIDENTAL edits,
not against a hostile author of this file, who could edit the enumerations, the recorded
exceptions and the check itself in one commit; review of the diff, not this gate, is the control
for that. (2) Anything the interpreter does OUTSIDE this file is disclosed, not caught:
sitecustomize and usercustomize, a patched or hostile stdlib, an audit hook, a logging handler
another module configured, the interpreter's own traceback printer on an uncaught exception
(with the raw text of the __cause__ chain), and this module's namespace reached at run time from
another module. (3) An enumerated attribute is allowed, not proven harmless: subprocess.run is
enumerated because this gate launches children, and a launch that does not capture its streams
writes to the inherited ones (_launch_recorder and _check_launches pin the launch keywords of
the launches this module makes); io.FileIO is enumerated for the descriptor probe, whose
descriptor is a non-constant expression the int-constant rule cannot see; sys._getframe, and
Path with write_text and open, are enumerated introspection and file surfaces. (4) A value that
has left a module (a subscript item, a call result) is an ordinary object whose attributes are
not enumerated; the refused attribute names and builtins close the known routes back into a
namespace. (5) At run time a hostile stub could still mutate what no rule protects (a gate's
__code__, a table reached through a container); _patched, the one deliberate mutator, refuses
every protected name, _CLOSED_VOCABULARIES is a read-only mapping proxy, and the static store
rules refuse the spelled forms.
The walk does not see a raise of a bare name (re-raising formats nothing) or a BUILT exception
whose class name is neither a builtin exception's nor this module's; and where a diagnostic
reports only a count (the attribution inventory's unpinned sites and unrecorded indirections),
the offending keys are deliberately not printed. The canonical Python floor guard that opens this
file (tools/check_python_floor.py GUARD_TEMPLATE) is the one code outside the emitter the
allowlist exempts: below the
floor it writes its fixed refusal formatted with the interpreter's version and sys.executable, and
exits 2 before anything else runs. closure/closed-sites exempts exactly the guard's two channel
references (its refusal write's sys.stderr and its raise's SystemExit) by their position and
exact shape (_floor_guard) and, through one flip per tested shape condition (each declaring the
count the exemption may remove in its flip module) plus the preamble controls, refutes an
exemption that would cover a reference in any other position or tested shape; the structural length
bounds of the preamble (a module too short to hold the guard) and the qualname component of the
site filter in _unexempted_violations are held jointly with the position flips (guard-in-def,
guard-second-copy), not by a flip of their own. Not
checked: that the process exits with main()'s return (the last line of this file relays it
through the emitter's int clamp; _check_entry_points calls main() directly), and that run()
removes its scratch directory afterwards (a
run that leaves its copy behind still verifies).
When any preflight case was refuted
the self-test exits 1; when none was refuted but one could not be evaluated, it exits 2. In both of
those outcomes the closure legs never run.
Then the two closure legs:
exit 0 (both legs held), 1 (a leg was refuted: the real opf/ did not verify, or the flipped copy was
not caught for the intended reason; failure-first, whatever else could not be evaluated) or 2 (no leg
was refuted but one could not be evaluated: run() returned 2, the flipped copy timed out or could not
be launched, its scratch directory, copy or flip could not be made, or its _opf_store.py is not UTF-8
text).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import ast
import contextlib
import errno
import fcntl
import functools
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import tempfile
import types
from pathlib import Path
from tempfile import gettempdir

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

# CLOSED DIAGNOSTICS (ruling D-428). Every refutation, cannot-evaluate, harness failure and
# stream write of this module emits only fixed literal text and identifiers drawn from closed
# vocabularies this module defines: case ids and labels, member and script names from the declared
# roster, report field names, fixed reason codes, integer counts and return codes. A runtime value
# that is not from such a vocabulary is never formatted into output: where a diagnostic needs to
# say which value was wrong, it names the field and the check that failed, never the value; a path
# is named by its role (the repository, the given root, the scratch directory), never printed.
# closure/closed-sites pins the rule in this file's AST as an ALLOWLIST: only the one
# module-level emitter _emit (pinned verbatim against _EMITTER_SOURCE) and the canonical Python
# floor guard that opens this file (tools/check_python_floor.py GUARD_TEMPLATE, which that gate
# requires verbatim; below the floor it writes its fixed refusal with the interpreter's version
# and sys.executable, before anything else runs, and closure/closed-sites exempts exactly its two
# channel references by position and exact shape, _floor_guard, nothing else) may reference an
# output or exit channel; any other reference, alias, binding or import of one is refused, closed
# text or not. Every _emit call passes each text argument as a string or int constant, a module
# literal registered in _CLOSED_TEXT_NAMES (bound exactly once at module level and never rebound
# or shadowed), a _cv/_cvs/_n gate call, or a format, concatenation or conditional of those;
# exception constructors and assert messages are checked the same way wherever they appear.
# The gates check membership at run time: a token outside its vocabulary is replaced by a fixed
# marker naming the vocabulary, so no runtime value can ride through them. The permitted
# unclosed-text sites are recorded in _CLOSED_SITE_EXCEPTIONS, each with its reason.

# The names of builtin exception classes, this module's own, and NoneType: the closed vocabulary a
# diagnostic naming an exception's type draws from, and the class names whose constructor calls
# closure/closed-sites checks. Computed at import by walking BaseException's subclass tree for the
# classes the builtins module defines (never by importing builtins, whose name the closed-sites
# allowlist reserves to the emitter), plus the two builtins ALIASES of OSError, which the subclass
# walk cannot see because each carries OSError's own __name__; no runtime value is a member.
def _builtin_exception_names():
    """Every builtin exception class's name, from BaseException's subclass tree at call time."""
    names, queue = set(("BaseException",)), [BaseException]
    while queue:
        for sub in queue.pop().__subclasses__():
            if sub.__module__ == "builtins" and sub.__name__ not in names:
                names.add(sub.__name__)
                queue.append(sub)
    return frozenset(names)


_EXCEPTION_NAMES = _builtin_exception_names() | frozenset(
    ("IOError", "EnvironmentError", "_CannotEvaluate", "_HarnessFailure", "TimeoutExpired", "NoneType"))

# Module-level string literals an output call or an exception constructor may pass by name
# (closure/closed-sites): each must be bound exactly once, at module level, to one string
# literal, and never rebound or shadowed anywhere in this file (_registered_name_faults; the
# check refuses the file otherwise, and _patched refuses rebinding one at run time, so a bare
# load of the name can only read that literal).
_CLOSED_TEXT_NAMES = ("_PIN_MARKER", "_INJECT_MARKER", "_STUB_SCRATCH", "_FLIP_EVIDENCE",
                      "_NEG_SCRATCH_ERROR", "_NEG_COPY_ERROR", "_NEG_DECODE_ERROR")

# The message of every OSError _failing_harness injects: a fixed literal, relayed into a
# diagnostic only as this registered name where a catching site saw it in the caught text.
_INJECT_MARKER = "injected harness failure (stubbed)"

# The message of the OSError _no_scratch raises: relayed like _INJECT_MARKER.
_STUB_SCRATCH = "no scratch directory (stubbed)"

# The CHANNEL names (closure/closed-sites allowlist): every builtin or module name through which
# this process could write to a stream or set its exit status. Outside the one emitter below (and
# the canonical floor guard, exempted by position and shape), ANY reference to one of these names
# is refused, closed text or not: a call, a load, a store, an alias, an except clause alike.
_CHANNEL_NAMES = ("print", "exit", "quit", "SystemExit", "warnings", "logging",
                  "traceback", "builtins", "__builtins__")

# The output and exit attributes of the two writer modules this file may import plainly. Any
# reference to one of them outside the emitter is refused; every other attribute (sys.argv,
# os.environ) stays free.
_CHANNEL_ATTRS = dict(sys=("stdout", "stderr", "__stdout__", "__stderr__", "exit"),
                      os=("write", "_exit"))

# The two writer modules this file may import plainly; no stub may rebind either at run time
# (_patched, through _PROTECTED_NAMES). The other channel-bearing module names (builtins,
# warnings, logging, traceback) are _CHANNEL_NAMES rows: every reference to one is refused
# outright, so they need no separate module row here. The import rules themselves live in
# _MODULE_ATTRS and _FROM_IMPORT_ALLOW below: only a plain `import M` of an enumerated module,
# and only the enumerated from-imports, are permitted; every other import is refused.
_CHANNEL_MODULES = ("sys", "os")

# Dynamic importers: a reference to either name, or an import of importlib, is refused outright,
# since a dynamic import could rebind or reach a channel under a name the walk cannot follow.
_DYNAMIC_IMPORT_NAMES = ("importlib", "__import__")

# The REAL allowlist of module references (closure/closed-sites, train 2 round 33): every module
# this file imports and, for each, the exact dotted attribute paths the file may reference. An
# attribute chain rooted in one of these module names (however deep, through attributes alone)
# whose dotted path is neither an enumerated entry nor a prefix of one is refused wherever it
# appears. sys's and os's output and exit attributes are deliberately NOT here (_CHANNEL_ATTRS
# reserves them to the one emitter and the exempted floor guard), so the emitter is the only
# code of this file that may reference an output-capable module attribute. A bare reference to
# any of these module names outside an attribute access is refused (an alias the walk cannot
# follow), and an import of any module outside this enumeration, with an asname, or in dotted
# form is refused outright.
_MODULE_ATTRS = dict(
    ast=frozenset((
        "Add", "Assert", "Assign", "AsyncFunctionDef", "Attribute", "BinOp", "Call", "ClassDef",
        "Compare", "Constant", "Del", "ExceptHandler", "Expr", "FunctionDef", "GeneratorExp",
        "Global", "If", "IfExp", "Import", "ImportFrom", "Lambda", "Load", "Lt", "MatchAs",
        "MatchMapping", "MatchStar", "Mod", "Name", "Nonlocal", "ParamSpec", "Raise", "Starred",
        "Store", "Subscript", "Tuple", "TypeVar", "TypeVarTuple", "alias", "arg", "dump",
        "get_source_segment", "iter_child_nodes", "parse", "walk")),
    contextlib=frozenset(("contextmanager", "redirect_stderr", "redirect_stdout")),
    errno=frozenset(("EACCES", "ENOSPC", "errorcode.get", "errorcode.values")),
    fcntl=frozenset(("F_DUPFD", "fcntl")),
    functools=frozenset(("partial",)),
    hashlib=frozenset(("sha256",)),
    io=frozenset(("FileIO", "StringIO")),
    json=frozenset(("dumps", "loads")),
    os=frozenset(("dup2", "environ.get", "environ.items", "environ.pop", "environ.update",
                  "listdir", "set_inheritable")),
    shutil=frozenset(("copytree", "ignore_patterns", "rmtree")),
    signal=frozenset(("SIGKILL",)),
    subprocess=frozenset(("DEVNULL", "PIPE", "STDOUT", "TimeoutExpired", "run")),
    sys=frozenset(("_getframe", "argv", "executable", "path.insert", "version_info")),
    tempfile=frozenset(("mkdtemp",)),
    types=frozenset(("BuiltinFunctionType", "FunctionType", "MappingProxyType", "MethodType",
                     "ModuleType", "SimpleNamespace")),
)
# The from-imports this file may hold, exactly (module, name), each bound under its own name
# (no asname); every other from-import is refused. The ("__future__", "annotations") row is
# held for the floor-guard preamble controls (_FLOOR_GUARD_CONTROLS), which parse a module that
# may open with it. What the three real rows bind is a stated residual: Path (whose instances
# carry write_text and open), gettempdir and repo_root are not output channels of this process
# by themselves, and a reference that writes through one is caught only where it spells a name
# a rule refuses.
_FROM_IMPORT_ALLOW = frozenset((("pathlib", "Path"), ("tempfile", "gettempdir"),
                                ("_gen_common", "repo_root"), ("__future__", "annotations")))

# Introspection and input builtins refused by name anywhere in this file, in any context: each
# can reach a namespace, build or run code, or write to a stream without spelling a channel
# name. The deliberate introspection sites of the test machinery itself (_patched, _nth_call,
# _derived_sites, _Overlay, _holds_code, _launch_recorder, _failing_harness) are each recorded
# in _CLOSED_SITE_EXCEPTIONS with a reason.
_REFUSED_BUILTIN_NAMES = ("eval", "exec", "compile", "globals", "vars", "locals", "input",
                          "help", "breakpoint")

# Attribute names refused on ANY base expression, however it was built: each is a namespace or
# descriptor door (sys.modules, a __dict__, a __builtins__, os.fdopen) that the module
# enumeration alone cannot close once a value has left a module.
_REFUSED_ATTRS = ("__dict__", "__builtins__", "modules", "fdopen")

# The attribute accessors: a call with a non-literal name argument, with no name argument, or
# with a literal name that is itself refused (a _REFUSED_ATTRS name, a channel attribute, a
# channel or protected name) is refused, and so is a load of an accessor outside a call's
# callee position (an alias the walk cannot follow).
_ATTR_ACCESSOR_NAMES = ("getattr", "setattr", "delattr")

# A call to open, io.FileIO or os.fdopen whose first argument is an int constant is a handle on
# a numbered descriptor (1 and 2 are this process's streams): refused. A descriptor built as a
# non-constant expression (the descriptor probe in _check_env_canaries dups one with fcntl) is
# a stated residual of this rule, held by that probe's own launch checks.
_DESCRIPTOR_OPEN_NAMES = ("open", "FileIO", "fdopen")

# Builtin names the emitter's int clamp and the gates' membership checks depend on: any binding
# of one anywhere in this file, in any _bound_names form (an assignment, a global or nonlocal
# declaration, a parameter, an import alias, a def), is refused by closure/closed-sites, and
# _patched refuses rebinding one at run time, so `int = str` cannot turn the emitter's clamp
# into a string exit.
_GATE_DEPENDENCY_NAMES = ("int", "str", "type", "isinstance", "frozenset")

# The tables the gates read at run time: a subscript or attribute store rooted at a gate name
# or at one of these names, anywhere in this file, is refused (a widened vocabulary or a
# replaced gate __code__ would let a runtime value ride through a gate the walk trusts by
# spelling), and _CLOSED_VOCABULARIES is additionally wrapped read-only at run time. A runtime
# widening by a hostile stub is out of scope: the pin guards against accidental edits, not a
# hostile author (see the module docstring's residuals).
_GATE_TABLE_NAMES = ("_CLOSED_VOCABULARIES", "_PREFLIGHT_CASES", "_CLOSED_FLIPS",
                     "_FLOOR_GUARD_FLIPS", "_BINDING_FLIPS", "_GATE_BINDING_FLIPS",
                     "_EMITTER_FLIPS", "_FLOOR_GUARD_CONTROLS", "_ATTRIBUTION_PINS",
                     "_ATTRIBUTION_EXCLUSIONS", "_ATTRIBUTION_INDIRECTIONS")

# The closed-text gates and the emitter: trusted by spelling at every call site, so each must
# be bound exactly once, by its module-level def with NO decorator (the gate decorator
# allowlist is empty on purpose: a decorator could replace a trusted gate's behavior under its
# trusted name), and never rebound or shadowed anywhere (_function_name_faults).
_GATE_NAMES = ("_emit", "_cv", "_cvs", "_n")

# Every name no stub may rebind (_patched refuses them at run time) and no binding, import alias
# or from-import may target (closure/closed-sites): the channel names, the writer modules, the
# dynamic importers, the gates and the emitter, and the registered text literals.
_PROTECTED_NAMES = (frozenset(_CHANNEL_NAMES) | frozenset(_CHANNEL_MODULES)
                    | frozenset(_DYNAMIC_IMPORT_NAMES) | frozenset(_GATE_NAMES)
                    | frozenset(_CLOSED_TEXT_NAMES) | frozenset(_GATE_DEPENDENCY_NAMES)
                    | frozenset(("_CLOSED_VOCABULARIES",)))

# Every protected name written out ONCE MORE, independently of the tables that feed
# _PROTECTED_NAMES: _check_closed_sites requires this tuple to equal _PROTECTED_NAMES exactly
# and probes _patched with every entry, so dropping a name from any contributing table fails
# the self-test even where a static walk rule overlaps it.
_PROTECTED_PROBES = (
    "print", "exit", "quit", "SystemExit", "warnings", "logging", "traceback", "builtins",
    "__builtins__", "sys", "os", "importlib", "__import__", "_emit", "_cv", "_cvs", "_n",
    "_PIN_MARKER", "_INJECT_MARKER", "_STUB_SCRATCH", "_FLIP_EVIDENCE", "_NEG_SCRATCH_ERROR",
    "_NEG_COPY_ERROR", "_NEG_DECODE_ERROR", "int", "str", "type", "isinstance", "frozenset",
    "_CLOSED_VOCABULARIES")


def _emit(text=None, err=False, code=None):
    """The one closed emitter (closure/closed-sites, ruling D-428): every stream write and every
    process exit of this module goes through this function, whose statements are pinned verbatim
    against _EMITTER_SOURCE. `text`, when given, is written to stderr when `err` is true and to
    stdout otherwise, with one newline appended; the walk requires the text argument at every call
    site to be closed (_closed_argument), so only closed text can reach a stream. `code`, when
    given, leaves the process: a value that is exactly an int (bool is not) is the exit status,
    and any other value exits 2, so the interpreter never prints an exit value of this module,
    which is why a call site may pass any expression as `code` (the entry point passes main())."""
    stream = sys.stderr if err else sys.stdout
    if text is not None:
        stream.write(text + "\n")
    if code is not None:
        sys.exit(code if type(code) is int else 2)


def _cv(kind, token):
    """`token` when it is a str member of the closed vocabulary `kind`; otherwise the fixed marker
    naming the vocabulary (the token itself is then never returned, so a runtime value cannot ride
    through). The static vocabularies live in _CLOSED_VOCABULARIES; "case", "flip" and the
    attribution kinds are read from the live tables (_PREFLIGHT_CASES, _CLOSED_FLIPS, _FLOOR_GUARD_FLIPS,
    _BINDING_FLIPS, _GATE_BINDING_FLIPS, _EMITTER_FLIPS,
    _ATTRIBUTION_PINS, _ATTRIBUTION_EXCLUSIONS, _ATTRIBUTION_INDIRECTIONS), so a stubbed table's
    names are members while it is patched in."""
    if kind == "case":
        allowed = frozenset(part for label, check in _PREFLIGHT_CASES
                            for part in (label, check.__name__) if part is not None)
    elif kind == "flip":
        allowed = (frozenset(flip for flip, _snippet in _CLOSED_FLIPS)
                   | frozenset(row[0] for row in _FLOOR_GUARD_FLIPS)
                   | frozenset(flip for flip, _snippet in _BINDING_FLIPS)
                   | frozenset(flip for flip, _snippet in _GATE_BINDING_FLIPS)
                   | frozenset(row[0] for row in _EMITTER_FLIPS)
                   | frozenset(row[0] for row in _FLOOR_GUARD_CONTROLS))
    elif kind in ("pin-label", "site", "site-key", "indirection-key"):
        rows = _ATTRIBUTION_PINS
        if kind == "pin-label":
            allowed = frozenset(row[1] for row in rows)
        elif kind == "site":
            allowed = frozenset("{} {}{}#{} {}".format(row[1], row[4], "." + row[5] if row[5] else "",
                                                       row[6], row[7]) for row in rows)
        elif kind == "site-key":
            allowed = frozenset(row[0] for row in rows) | frozenset(
                site for site, _reason in _ATTRIBUTION_EXCLUSIONS)
        else:
            allowed = frozenset(key for key, _line, _reason in _ATTRIBUTION_INDIRECTIONS)
    else:
        allowed = _CLOSED_VOCABULARIES[kind]
    if isinstance(token, str) and token in allowed:
        return token
    return "<no {} token>".format(kind)


def _cvs(kind, tokens):
    """Each of `tokens` through _cv(kind, ...), joined with ", "."""
    return ", ".join(_cv(kind, token) for token in tokens)


def _n(value):
    """`value` as decimal text when it is exactly an int (bool is not); otherwise a fixed marker."""
    if type(value) is int:
        return str(value)
    return "<not an integer>"

# The OPF gate subset, relative to the copied opf/tools/ directory. Each entry is (name, [args...]); the
# self-test legs are deterministic and git-independent (they build their own throwaway fixtures), so they
# run correctly in an isolated copy that is not itself an adopter or a git repository, while still driving
# the lazy render / emit paths that a survivor would break.
_SUBSET = [
    ("opf-homes-selftest", "check_opf_homes.py", ["--self-test"]),
    ("opf-homes-contract", "check_opf_homes.py", []),
    ("opf-tooling-selftest", "opf.py", ["--self-test"]),
    ("opf-drift-selftest", "check_opf_drift.py", ["--self-test"]),
    ("opf-doctor-selftest", "check_opf_doctor.py", ["--self-test"]),
    ("opf-init-selftest", "check_opf_init.py", ["--self-test"]),
    ("opf-init-contract-validator-selftest", "_opf_init_contract.py", ["--self-test"]),
    ("opf-init-contract-check-selftest", "check_opf_init_contract.py", ["--self-test"]),
    ("opf-upgrade-selftest", "check_opf_upgrade.py", ["--self-test"]),
    ("opf-adopt-selftest", "_opf_adopt.py", ["--self-test"]),
    ("opf-adopt-apply-selftest", "_opf_adopt_apply.py", ["--self-test"]),
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ["--self-test"]),
    ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]),
    ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]),
    ("opf-prompt-pack-selftest", "check_opf_prompt_pack.py", ["--self-test"]),
    ("opf-prompt-pack", "check_opf_prompt_pack.py", []),
    ("opf-oplock-selftest", "_opf_oplock.py", ["--self-test"]),
    ("opf-init-substrate-selftest", "_opf_init_substrate.py", ["--self-test"]),
    ("opf-init-builders-selftest", "_opf_init.py", ["--self-test"]),
    ("opf-init-operation-selftest", "_opf_init_operation.py", ["--self-test"]),
    ("opf-init-p0-selftest", "check_opf_init_p0.py", ["--self-test", "--red-on-revert"]),
    ("opf-init-observe-selftest", "check_opf_init_observe.py", ["--self-test", "--red-on-revert"]),
    ("commonmark-headings-selftest", "selftest_commonmark_headings.py", ["--self-test"]),
    ("commonmark-conformance", "selftest_commonmark_conformance.py", []),
]

# The DEFAULT kill bound per subset member. Generous for every member except the tooling
# self-test, which carries its own row below.
_SUBSET_TIMEOUT_S = 600

# Per-member kill bounds overriding the default. These are CI DEADLINES, deliberately SHORTER than
# a completion bound. The kill-timeout rule sizes a completion bound ABOVE the callee's own budget,
# and opf.py grants itself a much larger one: its _UNIT_OUTER_BOUNDS give each of its 22 units a
# hard outer bound of its own, summing to 30120 s (the largest, opf-watchdog-regressions, is
# 10800 s). That self-granted budget is a per-unit ceiling against a hung unit, not an expected run
# time, and the gate does not wait for it: a CI step that sits for over eight hours on a hung suite
# delays every verdict behind it. A deadline shorter than the callee's budget is legitimate here
# ONLY because a member killed at it is reported TIMEOUT (cannot evaluate, exit 2), never as a
# closure finding: a deadline that proves too short can cost a verdict, never produce a wrong one.
#
# opf-tooling-selftest (opf.py --self-test, the subprocess unit runner of merge train 2):
#   MEASURED: on a loaded 16-core build host under `nice -n 10` the full suite ran in 741.5 s; on
#   GitHub-hosted CI runners the same suite step took 912 s and 825 s on the two most recent train
#   runs (gh run view, 2026-10-05..07).
#   ESTIMATED: the bound is a 2x margin over the measured CI worst of 912 s, rounded down to
#   1800 s. The 2x factor is an assumption, not a measured ratio: hosted runners are shared and
#   their load varies run to run. The old flat 600 s sat BELOW the measured CI wall time, so it
#   killed a HEALTHY member on every train run.
_MEMBER_TIMEOUT_S = {
    "opf-tooling-selftest": 1800,
}


def _check_pack_manifest_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/pack-manifest-registration")


def _pack_manifest_registration_self_test():
    _attributed("closure/pack-manifest-registration", lambda: _check_pack_manifest_registration(_SUBSET))
    _emit("PASS closure/pack-manifest-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]))
    try:
        _attributed("closure/pack-manifest-registration", lambda: _check_pack_manifest_registration(changed))
    except AssertionError as exc:
        if str(exc) != "closure/pack-manifest-registration":
            raise
    else:
        raise AssertionError("closure/pack-manifest-registration-not-red")
    _emit("RED closure-registration -> closure/pack-manifest-registration")


def _check_adopt_observe_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/adopt-observe-registration")


def _adopt_observe_registration_self_test():
    _attributed("closure/adopt-observe-registration", lambda: _check_adopt_observe_registration(_SUBSET))
    _emit("PASS closure/adopt-observe-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]))
    try:
        _attributed("closure/adopt-observe-registration", lambda: _check_adopt_observe_registration(changed))
    except AssertionError as exc:
        if str(exc) != "closure/adopt-observe-registration":
            raise
    else:
        raise AssertionError("closure/adopt-observe-registration-not-red")
    _emit("RED closure-registration -> closure/adopt-observe-registration")


def _check_member_timeout_rows(subset, table):
    """The per-member bound table names only real members: a renamed or removed subset member must
    not leave a stale override behind (the stale row would silently stop bounding anything while
    its member fell back to the default). Covers exactly the (roster, table) pair it is given."""
    names = set(row[0] for row in subset)
    stale = sorted(set(table) - names)
    if stale:
        raise AssertionError("closure/member-timeout-rows: " + _n(len(stale)) + " stale row(s)")
    for name, bound in table.items():
        if type(bound) is not int or bound <= 0:
            raise AssertionError("closure/member-timeout-rows: " + _cv("member", name))


def _member_timeout_rows_self_test():
    _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S))
    _emit("PASS closure/member-timeout-rows")
    # Mutate a COPY of the real table: a row naming no member must go red.
    changed = dict(_MEMBER_TIMEOUT_S)
    changed["no-such-member"] = 60
    try:
        _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, changed))
    except AssertionError as exc:
        if str(exc) != "closure/member-timeout-rows: 1 stale row(s)":
            raise
    else:
        raise AssertionError("closure/member-timeout-rows-not-red")
    _emit("RED closure-registration -> closure/member-timeout-rows")
    # A bound that is not a positive int (bool is refused even though it subclasses int) must go red.
    for index, bad in enumerate((0, -60, 1800.0, True, "1800")):
        changed = dict(_MEMBER_TIMEOUT_S)
        changed["opf-tooling-selftest"] = bad
        try:
            _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, changed))
        except AssertionError as exc:
            if str(exc) != "closure/member-timeout-rows: opf-tooling-selftest":
                raise
        else:
            raise AssertionError("closure/member-timeout-rows-bad-bound-not-red: case " + _n(index))
        _emit("RED closure-registration -> closure/member-timeout-rows bound case " + _n(index))


def _isolated_env():
    """A minimal environment for the isolated subset. PYTHONPATH and PYTHONHOME are dropped so nothing off
    the copied tree can be imported (belt-and-suspenders atop `python3 -I`, which already ignores them), and
    every ambient GIT_-prefixed variable is dropped so an inherited GIT_DIR/GIT_WORK_TREE cannot rebind the
    copy to a different repository. Bytecode writing is suppressed to keep the copy pristine."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _materialize(opf_src, dest):
    """Copy the opf/ subtree ALONE into dest/opf (no tools/, no .aiqt), skipping bytecode artefacts. Raises
    on any copy failure so a truncated materialization fails closed rather than passing on a partial tree."""
    shutil.copytree(opf_src, dest / "opf",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest / "opf"


# The cannot-evaluate labels _run_one returns in place of None (the member never ran to
# completion). Each is a FIXED reason code (ruling D-428): run() prints the code and its fixed
# repair guidance, so distinct harness failures stay distinguishable without a runtime value.
_TIMEOUT = "TIMEOUT"
_HARNESS_MISSING = "HARNESS ERROR missing-script"
_HARNESS_LAUNCH = "HARNESS ERROR interpreter-launch-refused"

# Fixed repair guidance per cannot-evaluate reason code, printed only through the "repair" gate.
_REPAIR_GUIDANCE = {
    _TIMEOUT: "the member was killed at its kill bound; if it is healthy but slow, raise its "
              "_MEMBER_TIMEOUT_S row (or add one: a member without a row uses the "
              "_SUBSET_TIMEOUT_S default), then re-run this gate",
    _HARNESS_MISSING: "the member's script was not found in the materialized copy; restore it "
                      "under opf/tools/ in the repository, then re-run this gate",
    _HARNESS_LAUNCH: "the interpreter could not be launched for the member; check sys.executable "
                     "and the host's process limits, then re-run this gate",
}


def _run_one(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, separate_streams=False):
    """Run one subset member isolated against the copied opf/tools/<script>, killed at `timeout_s`
    seconds. Returns (rc, text, cannot): `text` is the last three lines of the merged output, except
    that with separate_streams a member that ran to completion gives the pair (stdout, stderr) of its
    whole decoded streams, captured apart so that no line can be assembled across them. `cannot` is
    None when the member ran to completion, _TIMEOUT when it was killed at its bound, _HARNESS_MISSING
    when its script is missing from the copy, or _HARNESS_LAUNCH when the interpreter could not be
    launched (`text` is then the harness detail, which run() never prints, per ruling D-428: run()
    prints the reason code and its fixed repair guidance instead). A non-None `cannot` is rc 2 and CANNOT-EVALUATE (closure was neither observed
    nor refuted), distinct from a member that ran and failed. cwd is run_dir: run() passes the scratch
    directory holding the copy, and the self-test checks that it lies outside the repository at the
    root and holds only the copy (_check_member_calls, _check_launches), so that only the copied
    opf/tools/ is reachable to `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), _HARNESS_MISSING
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if separate_streams else subprocess.STDOUT,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 2, "timed out after {}s (cannot evaluate: the member never finished)".format(timeout_s), _TIMEOUT
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), _HARNESS_LAUNCH
    if separate_streams:
        return proc.returncode, tuple((data or b"").decode("utf-8", "replace")
                                      for data in (proc.stdout, proc.stderr)), None
    lines = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    return proc.returncode, "\n".join(lines[-3:]), None


def _run_subset(opf_root, run_dir):
    """Run every subset member, each under its kill bound (_MEMBER_TIMEOUT_S row or the default).
    Returns a list of (name, rc, tail, cannot) with `cannot` as _run_one returns it."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
    _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S)
    env = _isolated_env()
    return [(name, *(_run_one(opf_root, script, args, run_dir, env,
                              timeout_s=_MEMBER_TIMEOUT_S.get(name, _SUBSET_TIMEOUT_S))))
            for name, script, args in _SUBSET]


def run(root):
    """Materialize opf/ alone and run the subset in isolation. Returns 0 (closure holds), 1 (a subset gate
    failed: closure broken, even when another member could not be evaluated), or 2 (no member failed,
    but a harness error, a member timeout, an unallocatable scratch directory or an unreadable opf/
    left closure unevaluated), fail-closed."""
    opf_src = Path(root) / "opf"
    if not (opf_src.is_dir() and (opf_src / "tools" / "opf.py").is_file()):
        _emit("error: opf/ subtree not found under the given root (cannot evaluate closure)",
              err=True)
        return 2
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-"))
        except OSError as exc:
            _emit("error: could not allocate a scratch directory for the isolated opf/ copy "
                  "(cannot evaluate closure; cause {})".format(
                      _cv("errno", errno.errorcode.get(exc.errno, "unknown errno"))), err=True)
            return 2
        try:
            opf_root = _materialize(opf_src, tmp)
        except OSError as exc:
            _emit("error: could not materialize the isolated opf/ copy (cannot evaluate closure; "
                  "cause {})".format(_cv("errno", errno.errorcode.get(exc.errno, "unknown errno"))),
                  err=True)
            return 2
        try:
            results = _run_subset(opf_root, tmp)
        except AssertionError as exc:
            # Which registration check failed is a closed label of this module (the gate refuses
            # any other token), so naming it discloses no runtime value (ruling D-428).
            _emit("STANDALONE CLOSURE: FAILED (a registration check was refuted: {})".format(
                _cv("label", str(exc).split(":")[0])), err=True)
            return 1
        spec = {name: (script, " ".join(args)) for name, script, args in _SUBSET}
        cannot = [(name, why) for name, rc, tail, why in results if why is not None]
        failed = [(name, rc) for name, rc, tail, why in results if rc != 0 and why is None]
        for name, rc, tail, why in results:
            _emit("  {:32s} {}".format(_cv("member", name),
                                       "{} (cannot evaluate)".format(_cv("reason", why))
                                       if why is not None
                                       else ("OK" if rc == 0 else "FAILED rc=" + _n(rc))))
        if failed:
            _emit("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself):",
                  err=True)
            for name, rc in failed:
                # The member's own output is captured and never echoed (ruling D-428); this fixed
                # line names the member's script and arguments from the declared roster, so the
                # user can re-run the member in isolation and read its output directly.
                _emit("  {} (rc={}): reproduce in isolation: copy opf/ alone into an empty "
                      "scratch directory, clear GIT_ and PYTHON variables from the environment, "
                      "and run under Python 3.14 or newer: python3 -I -B opf/tools/{}{}".format(
                          _cv("member", name), _n(rc), _cv("script", spec[name][0]),
                          " " + _cv("args", spec[name][1]) if spec[name][1] else ""), err=True)
        if cannot:
            # A member killed at its bound, missing from the copy, or not launchable is
            # CANNOT-EVALUATE (exit 2), never a closure finding: the member did not run to
            # completion, so this run observed neither closure nor its absence. Each row carries
            # its fixed reason code and fixed repair guidance (ruling D-428: no runtime value).
            _emit("STANDALONE CLOSURE: CANNOT EVALUATE (a subset member did not run to completion):",
                  err=True)
            for name, why in cannot:
                _emit("  {} [{}]: {}".format(
                    _cv("member", name), _cv("reason", why),
                    _cv("repair", _REPAIR_GUIDANCE.get(
                        why, "no fixed repair guidance for this reason"))), err=True)
        # Failure-first: a member that ran and failed is a definite closure break whatever else could
        # not be evaluated, so it decides the exit; cannot-evaluate (2) only when nothing failed.
        if failed:
            return 1
        if cannot:
            return 2
        _emit("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
        return 0
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# The intended reason the flipped copy must fail for: opf.py's bootstrap refusing the re-introduced
# `check_versions` import (the line opf.py prints when that import raises ImportError).
_FLIP_EVIDENCE = "opf: cannot bootstrap: check_versions (cannot evaluate)"

# The negative leg's cannot-evaluate messages for a broken copy it could not build.
_NEG_SCRATCH_ERROR = "negative: could not allocate a scratch directory for the broken copy"
_NEG_COPY_ERROR = "negative: harness error building the broken copy"
_NEG_DECODE_ERROR = "negative: the copied opf/tools/_opf_store.py is not UTF-8 text, so the flip cannot be made"


def _positive_leg_verdict(rc):
    """Classify run() over the real opf/: ("ok", None), ("cannot", msg) for exit 2, else ("fail", msg)."""
    if rc == 0:
        return "ok", None
    if rc == 2:
        return "cannot", "positive: run() could not evaluate the real opf/ subtree (exit 2)"
    return "fail", "positive: the real opf/ subtree did not verify in isolation (expected closure)"


def _output_lines(stream):
    """The complete lines of ONE decoded output stream: split on "\\n" only, each line losing at most
    one trailing "\\r" (CRLF output). str.splitlines is not used: it also breaks at CR, VT, FF, 0x1c
    to 0x1e, U+0085, U+2028 and U+2029, so text after any of them would read as a line of its own."""
    return [line[:-1] if line.endswith("\r") else line for line in stream.split("\n")]


def _negative_leg_verdict(rc, output, cannot):
    """Classify the flipped copy's run. ("caught", None) ONLY on positive evidence that it failed for
    the intended reason: a non-zero exit with _FLIP_EVIDENCE as a complete line of ONE output stream
    (see _output_lines); ("cannot", msg) when it never ran to completion (a timeout or a harness error
    says nothing about the edge); ("fail", msg) when it passed, or failed for some other reason (no
    evidence the edge itself was refused). `output` is the (stdout, stderr) pair _run_one gives with
    separate_streams, or one string standing for a single stream, so evidence split across the two
    streams is no evidence. The evidence is textual and does not authenticate its producer: any code
    in the flipped copy that printed that exact line would read as caught. It shows that the line was
    printed, not that opf.py's bootstrap refusal printed it."""
    streams = (output,) if isinstance(output, str) else tuple(output)
    if cannot is not None:
        return "cannot", "negative: the flipped copy did not run to completion ({})".format(
            _cv("reason", cannot))
    if rc == 0:
        return "fail", ("negative: a re-introduced upward edge (import check_versions) was NOT "
                        "caught in isolation (the gate would give no coverage)")
    if not any(_FLIP_EVIDENCE in _output_lines(stream) for stream in streams):
        return "fail", ("negative: the flipped copy exited rc={} without the import refusal line; a "
                        "failure for another reason is no evidence the edge is caught".format(_n(rc)))
    return "caught", None


def _read_copied_store(store):
    """The negative leg's read of the copied opf/tools/_opf_store.py: one function, so that the
    self-test's harness watch can observe it."""
    return store.read_text(encoding="utf-8")


def _write_copied_store(store, text):
    """The negative leg's write of the flipped _opf_store.py: one function, so that the self-test's
    harness watch can observe it."""
    store.write_text(text, encoding="utf-8")


def _closure_legs(root):
    """Run the positive and negative legs. Returns (failures, cannot): lists of messages."""
    failures, cannot = [], []
    kind, msg = _positive_leg_verdict(run(root))
    if kind == "fail":
        failures.append(msg)
    elif kind == "cannot":
        cannot.append(msg)

    # Negative (deliberate flip): a surviving upward edge must be caught.
    opf_src = Path(root) / "opf"
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-neg-"))
        except OSError as exc:
            cannot.append(_NEG_SCRATCH_ERROR)
            return failures, cannot
        opf_root = _materialize(opf_src, tmp)
        store = opf_root / "tools" / "_opf_store.py"
        try:
            text = _read_copied_store(store)
        except UnicodeDecodeError as exc:
            # Malformed input (the exit-2 convention): bytes that are not UTF-8 text cannot be flipped at
            # all, so nothing was observed. A UTF-8 store without the _semver import is well-formed input
            # the flip no longer matches: that is fixture drift, a failure below, because the negative leg
            # has lost the edge it exists to re-introduce and must be updated.
            cannot.append(_NEG_DECODE_ERROR)
            return failures, cannot
        flipped = text.replace("from _semver import _parse",
                               "from check_versions import _parse", 1)
        if flipped == text:
            failures.append("negative: could not locate the _semver import to flip (fixture drift)")
        else:
            _write_copied_store(store, flipped)
            env = _isolated_env()
            rc, output, why = _run_one(
                opf_root, "opf.py", ["--self-test"], tmp, env,
                timeout_s=_MEMBER_TIMEOUT_S.get("opf-tooling-selftest", _SUBSET_TIMEOUT_S),
                separate_streams=True)
            kind, msg = _negative_leg_verdict(rc, output, why)
            if kind == "fail":
                failures.append(msg)
            elif kind == "cannot":
                cannot.append(msg)
    except OSError as exc:
        cannot.append(_NEG_COPY_ERROR)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    return failures, cannot


def _self_test_exit(failures, cannot):
    """The self-test verdict: 1 when a leg was refuted, else 2 when a leg could not be evaluated, else 0."""
    for f in failures:
        _emit("SELF-TEST FAIL: {}".format(f), err=True)
    for c in cannot:
        _emit("SELF-TEST CANNOT EVALUATE: {}".format(c), err=True)
    if failures:
        return 1
    if cannot:
        return 2
    _emit("SELF-TEST PASS: standalone closure holds for opf/, and a re-introduced upward edge is caught.")
    return 0


@contextlib.contextmanager
def _patched(**values):
    """Rebind module globals for the duration of a stubbed case, restoring them on every path.
    Refuses a _PROTECTED_NAMES key before touching anything (closure/closed-sites pins the refusal
    with probes): a stub that could rebind a channel, a writer module, a dynamic importer, a gate,
    the emitter or a registered text literal would put a runtime value behind a name the static
    walk trusts by spelling."""
    refused = sorted(set(values) & _PROTECTED_NAMES)
    if refused:
        raise AssertionError("closure/patched-protected: a stub tried to rebind a protected "
                             "name: " + _cvs("protected-name", refused))
    g = globals()
    saved = {name: g[name] for name in values}
    g.update(values)
    try:
        yield
    finally:
        g.update(saved)


# Every (stdout, stderr) pair _captured collected, appended on BOTH its return and its exception
# path, so output a nested capture collected (or a callback printed before raising) stays
# scannable: _check_env_canaries notes len(_CAPTURE_LOG) before a sub-check and scans every pair
# appended since, whatever path the sub-check took. Append-only: entries are never removed, so a
# noted position stays valid for the whole run.
_CAPTURE_LOG = []


def _captured(fn, *args):
    """Call fn(*args) with stdout and stderr captured; returns (result, stdout_text, stderr_text).
    The captured pair is also appended to _CAPTURE_LOG on every path, the exception path included
    (the exception itself propagates unchanged)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = fn(*args)
    finally:
        _CAPTURE_LOG.append((out.getvalue(), err.getvalue()))
    return result, out.getvalue(), err.getvalue()


# The self-test's own declaration of the member roster, written out apart from _SUBSET and the bound
# table on purpose: (name, script, args, kill bound in seconds), in run order. closure/declared-roster
# compares _SUBSET, with each row's bound looked up as run() looks it up, to this list row for row;
# the stubbed runs (_check_member_calls) and the boundary case (_check_member_boundary) reconcile
# run()'s member calls and launches against it. So a change to _SUBSET, or a change to
# _SUBSET_TIMEOUT_S or _MEMBER_TIMEOUT_S that changes a member's effective bound (the bound run()
# looks up for it), fails the self-test until this list is changed with it. What is pinned is that
# effective bound, not the tables: a row that restates the default, for example, changes no bound
# and is not seen.
_DECLARED_ROSTER = (
    ("opf-homes-selftest", "check_opf_homes.py", ("--self-test",), 600),
    ("opf-homes-contract", "check_opf_homes.py", (), 600),
    ("opf-tooling-selftest", "opf.py", ("--self-test",), 1800),
    ("opf-drift-selftest", "check_opf_drift.py", ("--self-test",), 600),
    ("opf-doctor-selftest", "check_opf_doctor.py", ("--self-test",), 600),
    ("opf-init-selftest", "check_opf_init.py", ("--self-test",), 600),
    ("opf-init-contract-validator-selftest", "_opf_init_contract.py", ("--self-test",), 600),
    ("opf-init-contract-check-selftest", "check_opf_init_contract.py", ("--self-test",), 600),
    ("opf-upgrade-selftest", "check_opf_upgrade.py", ("--self-test",), 600),
    ("opf-adopt-selftest", "_opf_adopt.py", ("--self-test",), 600),
    ("opf-adopt-apply-selftest", "_opf_adopt_apply.py", ("--self-test",), 600),
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ("--self-test",), 600),
    ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ("--self-test",), 600),
    ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ("--self-test",), 600),
    ("opf-prompt-pack-selftest", "check_opf_prompt_pack.py", ("--self-test",), 600),
    ("opf-prompt-pack", "check_opf_prompt_pack.py", (), 600),
    ("opf-oplock-selftest", "_opf_oplock.py", ("--self-test",), 600),
    ("opf-init-substrate-selftest", "_opf_init_substrate.py", ("--self-test",), 600),
    ("opf-init-builders-selftest", "_opf_init.py", ("--self-test",), 600),
    ("opf-init-operation-selftest", "_opf_init_operation.py", ("--self-test",), 600),
    ("opf-init-p0-selftest", "check_opf_init_p0.py", ("--self-test", "--red-on-revert"), 600),
    ("opf-init-observe-selftest", "check_opf_init_observe.py", ("--self-test", "--red-on-revert"), 600),
    ("commonmark-headings-selftest", "selftest_commonmark_headings.py", ("--self-test",), 600),
    ("commonmark-conformance", "selftest_commonmark_conformance.py", (), 600),
)

# The negative leg's one member call, as _DECLARED_ROSTER declares it.
_DECLARED_NEGATIVE = tuple(row for row in _DECLARED_ROSTER if row[0] == "opf-tooling-selftest")


def _check_declared_roster():
    """_SUBSET, each row with the bound run() looks up for it (_MEMBER_TIMEOUT_S, else _SUBSET_TIMEOUT_S),
    equals _DECLARED_ROSTER row for row and in order: names, scripts, args (compared as tuples) and
    bounds. Raises AssertionError naming the first row that differs."""
    got = [(name, script, tuple(args), _MEMBER_TIMEOUT_S.get(name, _SUBSET_TIMEOUT_S))
           for name, script, args in _SUBSET]
    for index in range(max(len(got), len(_DECLARED_ROSTER))):
        have, want = got[index:index + 1], list(_DECLARED_ROSTER[index:index + 1])
        if have != want:
            raise AssertionError("closure/declared-roster: row {} of _SUBSET is not the declared "
                                 "row".format(_n(index + 1)))


# Ambient variables the isolated environment must drop. _ambient_sentinels sets them in os.environ
# while the stubbed runs and the boundary case execute, so their environment checks observe a drop
# (or a leak) of these variables on any host, whatever the host's own environment holds.
_AMBIENT_SENTINELS = (
    ("GIT_DIR", "/nonexistent/opf-closure-sentinel.git"),
    ("GIT_OPF_CLOSURE_SENTINEL", "1"),
    ("PYTHONPATH", "/nonexistent/opf-closure-sentinel"),
    ("PYTHONHOME", "/nonexistent/opf-closure-sentinel"),
)


# A variable whose VALUE no diagnostic and no inherited descriptor may carry (_check_env_canaries).
_ENV_CANARY = ("OPF_CLOSURE_CANARY", "opf-closure-canary-value-0c2c")


@contextlib.contextmanager
def _ambient_sentinels(pairs=_AMBIENT_SENTINELS):
    """Set every (name, value) of `pairs` (by default _AMBIENT_SENTINELS) in os.environ for the
    duration, restoring each variable's previous value (or its absence) on every path."""
    saved = [(key, os.environ.get(key)) for key, _value in pairs]
    os.environ.update(pairs)
    try:
        yield
    finally:
        for key, value in saved:
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _env_fault(env):
    """None when `env` is a dict with no GIT_-prefixed variable, no PYTHONPATH or PYTHONHOME, and
    PYTHONDONTWRITEBYTECODE equal to "1"; otherwise one of the fixed fault texts of the "fault"
    vocabulary. The description never carries a value, a name or a type of the environment (see
    _env_names_fault)."""
    if not isinstance(env, dict):
        return "an environment that is not a dict"
    return _env_names_fault(_env_leaked(env), env.get("PYTHONDONTWRITEBYTECODE") == "1")


def _env_leaked(env):
    """The sorted names of the variables in `env` the isolated environment must drop."""
    return sorted(key for key in env if key.startswith("GIT_") or key in ("PYTHONPATH", "PYTHONHOME"))


def _env_names_fault(leaked, no_bytecode):
    """_env_fault's verdict from what it needs and no more: the leaked variables' names and whether
    PYTHONDONTWRITEBYTECODE equals "1". None when nothing leaked and it does; otherwise one of the
    fixed fault texts of the "fault" vocabulary, never a variable's name or value."""
    if type(leaked) is not list or not all(type(key) is str for key in leaked) or type(no_bytecode) is not bool:
        return "an environment report that is not a list of names and a Boolean"
    if leaked or not no_bytecode:
        return "a leaked variable, or PYTHONDONTWRITEBYTECODE not equal to 1"
    return None


def _digest(path):
    """The sha256 hex digest of the file at path, or None when it is absent or cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _entries(path):
    """The sorted names directly inside the directory at path, or None when it cannot be listed."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return None


def _stub_member_runner(calls, outcomes):
    """A stand-in for _run_one: records each call it receives as a dict and returns outcomes.get(name)
    or a pass. Recorded as the call passed them: script, args, timeout, opf_root, run_dir, env and
    separate_streams; name, the _DECLARED_ROSTER member with that script and args (None for a row it
    does not declare). Read when the call is made (the copy is removed once run() returns): entries,
    the names directly inside run_dir; payload, the sha256 of opf_root/tools/opf.py and of
    opf_root/tools/<script> (None for a file absent or unreadable)."""
    by_row = {(script, args): name for name, script, args, _bound in _DECLARED_ROSTER}

    def stub(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, separate_streams=False):
        name = by_row.get((script, tuple(args)))
        tools = Path(opf_root) / "tools"
        calls.append(dict(name=name, script=script, args=tuple(args), timeout=timeout_s, opf_root=opf_root,
                          run_dir=run_dir, env=dict(env), separate_streams=separate_streams,
                          entries=_entries(run_dir),
                          payload=tuple((part, _digest(tools / part)) for part in ("opf.py", script))))
        return outcomes.get(name, (0, "ok", None))
    return stub


def _copy_recorder(made):
    """A stand-in for _materialize that delegates to the one bound now and records each copy as (the
    source it was given, the destination it was given, the path it returned)."""
    inner = _materialize

    def materialize(opf_src, dest):
        made.append((opf_src, dest, inner(opf_src, dest)))
        return made[-1][2]
    return materialize


def _check_copy(label, made, source, root):
    """The one copy a run made, as _copy_recorder recorded it in `made`: exactly one copy, from
    <source>/opf into <dest>/opf for the dest it was given (resolved paths compared), lying outside
    the repository at `root`. Returns the copy's resolved path. Raises AssertionError (`label`-copy,
    `label`-scratch), or _CannotEvaluate (`label`-scratch) when the host's temporary directory
    (tempfile.gettempdir(), which TMPDIR sets) is itself inside that repository: no scratch directory
    outside it can then be observed, so the case says so instead of refuting run() (a residual: such
    a host cannot evaluate this check)."""
    if len(made) != 1:
        raise AssertionError("{}-copy: the run made {} copies of opf/, expected one".format(
            _cv("label", label), _n(len(made))))
    repo = Path(root).resolve()
    src, dest, copy = (Path(part).resolve() for part in made[0])
    want = Path(source).resolve() / "opf"
    if src != want or copy != dest / "opf":
        raise AssertionError("{}-copy: the run copied another source, or into another destination "
                             "(no path is printed)".format(_cv("label", label)))
    host_tmp = Path(gettempdir()).resolve()
    if host_tmp.is_relative_to(repo):
        raise _CannotEvaluate("{}-scratch: the host's temporary directory is inside the repository "
                              "(TMPDIR), so no scratch directory outside it can be observed".format(
                                  _cv("label", label)))
    if copy.is_relative_to(repo):
        raise AssertionError("{}-scratch: the copy is inside the repository".format(_cv("label", label)))
    return copy


def _check_member_calls(label, calls, made, root, separate_streams, roster):
    """Reconcile the member calls a stubbed run made, as _stub_member_runner recorded them, with what
    the self-test requires. Each refutes with its own suffix of `label`:
    -roster: the calls' (name, script, args, bound) equal `roster` in order (no omission, extra,
      repeat or reordering);
    -copy and -scratch: see _check_copy, with the source <root>/opf (-scratch is cannot-evaluate when
      the host's temporary directory is inside the repository);
    -root: each call's opf_root is that copy;
    -run-dir: each call's run_dir is the directory holding the copy, and held only "opf" when the
      call was made;
    -payload: opf.py and the call's own script were in the copy when the call was made, each with
      the sha256 of the file of the same name under <root>/opf/tools now;
    -env: each call's env passes _env_fault;
    -streams: each call's separate_streams is `separate_streams`.
    These are the arguments run() (or the negative leg) passed to _run_one; what _run_one launches
    with them is _check_member_boundary's. Not compared: the copy's other files. A payload file that
    exists but cannot be read reads as missing (a refutation, never a pass)."""
    got = [(call["name"], call["script"], call["args"], call["timeout"]) for call in calls]
    for index in range(max(len(got), len(roster))):
        have, want = got[index:index + 1], list(roster[index:index + 1])
        if have != want:
            raise AssertionError("{}-roster: call {} is not the declared row ({} calls, {} expected; "
                                 "no call value is printed)".format(
                                     _cv("label", label), _n(index + 1), _n(len(got)), _n(len(roster))))
    copy = _check_copy(label, made, root, root)
    tools = Path(root).resolve() / "opf" / "tools"
    for call in calls:
        name = call["name"]
        where = Path(call["opf_root"]).resolve()
        if where != copy:
            raise AssertionError("{}-root: {} ran over a directory that is not the copy".format(
                _cv("label", label), _cv("member", name)))
        run_dir = Path(call["run_dir"]).resolve()
        if run_dir != copy.parent or call["entries"] != ["opf"]:
            raise AssertionError("{}-run-dir: {} ran in a directory that is not the one holding only "
                                 "the copy".format(_cv("label", label), _cv("member", name)))
        for part, digest in call["payload"]:
            if digest is None or digest != _digest(tools / part):
                raise AssertionError("{}-payload: {} ran over a copy whose tools/{} is {}".format(
                    _cv("label", label), _cv("member", name), _cv("script", part),
                    "missing" if digest is None else "not the source's"))
        fault = _env_fault(call["env"])
        if fault is not None:
            raise AssertionError("{}-env: {} ran with {}".format(
                _cv("label", label), _cv("member", name), _cv("fault", fault)))
        if call["separate_streams"] is not separate_streams:
            raise AssertionError("{}-streams: {} ran with the wrong separate_streams".format(
                _cv("label", label), _cv("member", name)))


# The recording child of _check_member_boundary, written as every member's script in a scratch copy:
# it prints one JSON line on stdout saying how it was launched. Of its environment it reports only
# what _env_names_fault needs: the names of the variables that must have been dropped, and whether
# PYTHONDONTWRITEBYTECODE equals "1"; never a value.
_RECORDER = (
    "import json, os, sys\n"
    "print(json.dumps(dict(argv=sys.argv, isolated=sys.flags.isolated,\n"
    "                      no_bytecode=sys.flags.dont_write_bytecode, cwd=os.getcwd(),\n"
    "                      entries=sorted(os.listdir(os.getcwd())),\n"
    "                      env_leaked=sorted(key for key in os.environ\n"
    "                                        if key.startswith('GIT_') or key in ('PYTHONPATH', 'PYTHONHOME')),\n"
    "                      env_no_bytecode=os.environ.get('PYTHONDONTWRITEBYTECODE') == '1')))\n")

# The fields of a _RECORDER report. A wrong report's diagnostic names the fields that failed and
# the checks they failed, from fixed text, never a field's value (_report_mismatches).
_REPORT_FIELDS = ("argv", "isolated", "no_bytecode", "cwd", "entries", "env_leaked", "env_no_bytecode")

# The only keyword arguments a launch through _launch_recorder may pass (_run_one's), sorted.
_LAUNCH_KEYWORDS = ("cwd", "env", "stderr", "stdout", "timeout")

# The static closed vocabularies _cv and _cvs draw identifiers from (see the CLOSED DIAGNOSTICS
# comment at the top). Only fixed literals and names this file declares are members, so no
# runtime value can enter one; "case", "flip" and the attribution kinds are read from the live
# tables inside _cv instead.
_CLOSED_LABELS = (
    "closure/absent-subtree", "closure/absent-subtree-cannot-evaluate", "closure/absent-subtree-scratch",
    "closure/adopt-observe-registration", "closure/adopt-observe-registration-not-red",
    "closure/attribution-first-call", "closure/attribution-indirection", "closure/attribution-inventory",
    "closure/attribution-pin", "closure/attribution-positions", "closure/attribution-stale-exclusion",
    "closure/attribution-stale-indirection", "closure/attribution-stale-pin",
    "closure/attribution-unpinned", "closure/attribution-walk", "closure/boundary-failed",
    "closure/boundary-negative", "closure/boundary-run", "closure/boundary-run-env",
    "closure/boundary-scratch", "closure/closed-sites", "closure/copy-before-members", "closure/copy-run", "closure/copy-run-full",
    "closure/declared-roster", "closure/entry-main", "closure/entry-points", "closure/entry-self-test",
    "closure/env-canaries", "closure/env-canary", "closure/env-canary-descriptors",
    "closure/env-canary-descriptors-kwargs", "closure/env-canary-kwargs", "closure/env-canary-launch",
    "closure/env-canary-launch-argv", "closure/env-canary-launch-cwd", "closure/env-canary-m17",
    "closure/env-canary-noise", "closure/env-canary-report", "closure/env-canary-report-child",
    "closure/env-canary-scratch", "closure/env-canary-streams", "closure/evidence-streams",
    "closure/failure-first", "closure/failure-first-listing", "closure/harness-cannot-evaluate",
    "closure/harness-exit", "closure/harness-launch", "closure/harness-line",
    "closure/harness-missing-script", "closure/harness-reason", "closure/harness-scratch",
    "closure/holds-code",
    "closure/host-independence", "closure/host-independence-disclosure", "closure/host-independence-drift",
    "closure/host-independence-red", "closure/host-independence-scratch", "closure/member",
    "closure/member-bound-lookup", "closure/member-boundary", "closure/member-calls",
    "closure/member-roster", "closure/member-timeout-rows",
    "closure/member-timeout-rows-bad-bound-not-red", "closure/member-timeout-rows-not-red",
    "closure/members-never-copied", "closure/members-never-ran", "closure/missing-input",
    "closure/missing-input-empty", "closure/missing-input-no-opf-py", "closure/missing-input-nonexistent",
    "closure/missing-input-scratch", "closure/negative-bound-lookup", "closure/negative-copy",
    "closure/negative-copy-cannot-evaluate", "closure/negative-harness", "closure/negative-run",
    "closure/negative-verdict", "closure/no-subtree", "closure/pack-manifest-registration",
    "closure/pack-manifest-registration-not-red", "closure/pin-probe", "closure/pin-rules",
    "closure/positive-root", "closure/positive-verdict", "closure/preflight-absent-scratch",
    "closure/preflight-absent-subtree", "closure/preflight-collection", "closure/preflight-error",
    "closure/preflight-failure-first", "closure/preflight-stop", "closure/red-bound-lookup",
    "closure/run-one-timeout", "closure/scratch-and-copy-cannot-evaluate", "closure/scratch-harness",
    "closure/scratch-run", "closure/scratch-run-denied", "closure/self-test-exit", "closure/self-test-leg-verdicts",
    "closure/self-test-leg-wiring", "closure/stubbed-label", "closure/stubbed-refutation",
    "closure/stubbed-run", "closure/stubbed-sentinel", "closure/timeout-attribution",
    "closure/timeout-cannot-evaluate", "closure/timeout-exit", "closure/timeout-line",
    "closure/undecodable-attribution", "closure/undecodable-fixture",
    "closure/undecodable-fixture-cannot-evaluate", "closure/undecodable-scratch",
    "closure/undecodable-wiring",
)
_CLOSED_VOCABULARIES = types.MappingProxyType(dict((
    ("label", frozenset(_CLOSED_LABELS)),
    ("member", frozenset(row[0] for row in _DECLARED_ROSTER)),
    ("script", frozenset(row[1] for row in _DECLARED_ROSTER) | frozenset(("opf.py",))),
    ("probe", frozenset(("split.py", "carriage.py", "evidence.py", "evidence-zero.py", "sleeper.py",
                         "no_such_member.py"))),
    ("reason", frozenset((_TIMEOUT, _HARNESS_MISSING, _HARNESS_LAUNCH))),
    ("repair", frozenset(_REPAIR_GUIDANCE.values())
     | frozenset(("no fixed repair guidance for this reason",))),
    ("args", frozenset(" ".join(row[2]) for row in _DECLARED_ROSTER)),
    ("verdict", frozenset(("ok", "fail", "cannot", "caught"))),
    ("fault", frozenset(("an environment that is not a dict",
                         "an environment report that is not a list of names and a Boolean",
                         "a leaked variable, or PYTHONDONTWRITEBYTECODE not equal to 1"))),
    ("mismatch", frozenset(("argv (not the script path and its args)", "isolated (not 1)",
                            "no_bytecode (not 1)", "cwd (not the directory holding the copy)",
                            "entries (not exactly the copy)", "env_leaked (not an empty list of names)",
                            "env_no_bytecode (not true)"))),
    ("stream-fault", frozenset(("stdout (not the pipe mode)",
                                "stderr (neither the pipe nor the merge mode)"))),
    ("keyword", frozenset(_LAUNCH_KEYWORDS) | frozenset(("pass_fds", "close_fds", "stdin", "preexec_fn"))),
    ("exception", _EXCEPTION_NAMES),
    ("primitive", frozenset(("scratch allocation", "copy", "fixture write", "copied store read",
                             "flip write", "store read", "child process"))),
    ("text-name", frozenset(_CLOSED_TEXT_NAMES)),
    ("gate-name", frozenset(_GATE_NAMES)),
    ("protected-name", _PROTECTED_NAMES),
    ("errno", frozenset(errno.errorcode.values()) | frozenset(("unknown errno",))),
)))


def _report_mismatches(report, argv, where):
    """The _REPORT_FIELDS of the _RECORDER report `report` (a dict) that fail their check, in that
    order, each as "<field> (<the check it failed>)" from fixed text: argv must equal `argv`,
    isolated and no_bytecode 1, cwd a string naming the directory `where`, entries exactly ["opf"],
    env_leaked an empty list and env_no_bytecode true (together, what _env_names_fault passes). A
    field that is absent fails its check. Never carries a value of the report."""
    cwd = report.get("cwd")
    leaked = report.get("env_leaked")
    checks = (
        ("argv", report.get("argv") == argv, "not the script path and its args"),
        ("isolated", report.get("isolated") == 1, "not 1"),
        ("no_bytecode", report.get("no_bytecode") == 1, "not 1"),
        ("cwd", type(cwd) is str and "\0" not in cwd and Path(cwd).resolve() == where,
         "not the directory holding the copy"),
        ("entries", report.get("entries") == ["opf"], "not exactly the copy"),
        ("env_leaked", type(leaked) is list and not leaked, "not an empty list of names"),
        ("env_no_bytecode", report.get("env_no_bytecode") is True, "not true"),
    )
    return ["{} ({})".format(field, check) for field, held, check in checks if not held]


def _launch_recorder(launches, label):
    """A stand-in for the subprocess module bound now: run delegates to its run and records each
    launch as (the argument list, the keyword arguments, the child's stdout decoded); every other
    attribute reads through. Built once the watch is bound, so a launch the host refuses is observed.
    The keywords and the streams are checked BEFORE the child is launched, and a launch failing
    either is refused without running the child: one passing any keyword argument but
    _LAUNCH_KEYWORDS (for example pass_fds, close_fds, preexec_fn or stdin, which can hand a
    descriptor to the child and its descendants) raises AssertionError `label`-kwargs naming the
    keywords (never their values; a missing required keyword is refused by the same test, since the
    keywords must be exactly _LAUNCH_KEYWORDS); one whose stdout is not PIPE, or whose stderr is
    neither PIPE nor STDOUT (an inherited or redirected stream), raises `label`-streams naming the
    stream field and the check it failed from fixed text, never a rejected value. The child it runs takes its
    stdin from DEVNULL (a keyword the recorder adds and does not record) and close_fds keeps its
    default, so the child and its descendants inherit no descriptor of this process and no child
    output reaches an inherited descriptor. _check_launches then requires the exact stderr mode."""
    inner = subprocess.run

    def launch(cmd, **kwargs):
        if sorted(kwargs) != list(_LAUNCH_KEYWORDS):
            raise AssertionError("{}-kwargs: a launch with the keywords {} was refused before the child "
                                 "ran (only the recorded launch keywords are permitted; a keyword "
                                 "outside the closed vocabulary is not printed)".format(
                                     _cv("label", label), _cvs("keyword", sorted(kwargs))))
        wrong = [field for field, held in (
            ("stdout (not the pipe mode)", kwargs["stdout"] == subprocess.PIPE),
            ("stderr (neither the pipe nor the merge mode)",
             kwargs["stderr"] in (subprocess.PIPE, subprocess.STDOUT))) if not held]
        if wrong:
            raise AssertionError("{}-streams: a launch whose {} was refused before the child ran; no "
                                 "rejected stream value is printed".format(
                                     _cv("label", label), _cvs("stream-fault", wrong)))
        proc = inner(cmd, stdin=subprocess.DEVNULL, **kwargs)
        launches.append((list(cmd), dict(kwargs), (proc.stdout or b"").decode("utf-8", "replace")))
        return proc
    return _Overlay(subprocess, run=launch)


def _check_launches(label, launches, made, source, root, roster, stderr):
    """Reconcile the launches a run made through the real _run_one, as _launch_recorder recorded
    them, with `roster`. Each refutes with its own suffix of `label`: -copy and -scratch (see
    _check_copy, the source <source>/opf); -launches: one launch per roster row; -argv: in roster
    order, the argument list is [sys.executable, "-I", "-B", <copy>/tools/<script>, *args]; -kwargs:
    exactly cwd, env, stdout, stderr and timeout were passed; -cwd: cwd is the directory holding the
    copy; -env: env passes _env_fault; -streams: stdout is PIPE and stderr is `stderr`; -timeout: the
    row's bound; -child: the child's own report (the last line of its stdout, from _RECORDER) gives
    its argv as the script path and args, sys.flags.isolated and dont_write_bytecode set, a working
    directory that is the directory holding the copy and holds only "opf", and an environment that
    passes _env_fault (judged from the report's names and Boolean; the report carries no value).
    Every refutation emits only closed text: an argument list, cwd, path, stream mode or timeout
    the code under test passed is never printed; the refutation names the member, the field and
    the check that failed, and fixed counts only. Nothing a child
    supplied is printed: a child that gave no report is named with the byte count of its output,
    and a wrong report with that byte count and the fixed identifiers of the fields that failed
    and of the checks they failed (_report_mismatches), never a field's value."""
    copy = _check_copy(label, made, source, root)
    if len(launches) != len(roster):
        raise AssertionError("{}-launches: {} launches, expected {}".format(
            _cv("label", label), _n(len(launches)), _n(len(roster))))
    for (cmd, kwargs, out), (name, script, args, bound) in zip(launches, roster):
        target = copy / "tools" / script
        if (cmd[:3] != [sys.executable, "-I", "-B"] or len(cmd) < 4 or Path(cmd[3]).resolve() != target
                or cmd[4:] != list(args)):
            raise AssertionError("{}-argv: {} launched a wrong argument list (no argument value is "
                                 "printed)".format(_cv("label", label), _cv("member", name)))
        if sorted(kwargs) != ["cwd", "env", "stderr", "stdout", "timeout"]:
            raise AssertionError("{}-kwargs: {} launched with wrong keyword names".format(
                _cv("label", label), _cv("member", name)))
        if kwargs["cwd"] is None or Path(kwargs["cwd"]).resolve() != copy.parent:
            raise AssertionError("{}-cwd: {} launched in a directory that is not the one holding the "
                                 "copy".format(_cv("label", label), _cv("member", name)))
        fault = _env_fault(kwargs["env"])
        if fault is not None:
            raise AssertionError("{}-env: {} launched with {}".format(
                _cv("label", label), _cv("member", name), _cv("fault", fault)))
        if kwargs["stdout"] != subprocess.PIPE or kwargs["stderr"] != stderr:
            raise AssertionError("{}-streams: {} launched with a wrong stdout or stderr mode".format(
                _cv("label", label), _cv("member", name)))
        if kwargs["timeout"] != bound:
            raise AssertionError("{}-timeout: {} launched with a wrong timeout, not {}".format(
                _cv("label", label), _cv("member", name), _n(bound)))
        lines = out.strip().splitlines()
        size = len(out.encode("utf-8", "replace"))
        try:
            report = json.loads(lines[-1])
        except (IndexError, ValueError):
            report = None
        if not isinstance(report, dict):
            raise AssertionError("{}-child: {} gave no report ({} bytes of output)".format(
                _cv("label", label), _cv("member", name), _n(size)))
        wrong = _report_mismatches(report, [cmd[3], *args], copy.parent)
        if wrong:
            raise AssertionError("{}-child: {} reported a wrong {} ({} bytes of output; no field value "
                                 "is printed)".format(_cv("label", label), _cv("member", name),
                                                      _cvs("mismatch", wrong), _n(size)))


class _CannotEvaluate(Exception):
    """A self-test case or sub-check observed nothing it could judge; never a refuted leg. Raised when
    the root has no opf/ subtree; when a scratch directory, copy, fixture write, store read, flip
    write or child launch failed, or a child did not finish within its bound (observed by
    _harness_watch, or by a case's own except OSError around building its fixture); when the copied
    _opf_store.py is not UTF-8 text (malformed input); and when an attribution pin's case could not
    reach the call it pins (see _pin). The self-test exits 2 on it only when no case and no leg was
    refuted (failure-first)."""


class _HarnessFailure(_CannotEvaluate):
    """The _CannotEvaluate that _observed raises when its OWN _harness_watch saw a host primitive
    fail, as opposed to one the code under test raised or one a nested watch raised."""


def _write_fixture(base, files):
    """Write each (relative path, bytes) of `files` under base, making its parent directories. Every
    self-test fixture is written through this one function, so _harness_watch observes its
    directory creation and its writes."""
    for rel, data in files:
        path = Path(base) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _harness_watch(broke):
    """Stand-ins for the host primitives tempfile.mkdtemp, _materialize, _write_fixture,
    _read_copied_store, _write_copied_store, _not_utf8 and subprocess.run. Each delegates to the one
    bound now and appends to `broke` when it raised an OSError (the host refused the allocation, copy,
    write, read or launch) or, for subprocess.run only, TimeoutExpired (a child did not finish within its
    bound). Such a failure is the host's: the fixture or the child's result was not made. Any other
    exception raised inside a watched call is not recorded: it is a defect of the code that raised
    it (the caller, for a TypeError from an invalid call; _materialize itself, for a crash inside
    it), so where that code is the code under test it is a refutation. The watch observes the
    primitive itself, never a message the code under test produced. Residuals: an OSError or a
    timeout that the caller's own wrong argument caused (a nonexistent dir= for mkdtemp, a too-short
    timeout for a child) still reads as the host's, so the outcome is cannot-evaluate (exit 2),
    fail-closed, never a pass. Host I/O outside these primitives is not watched: the removal of
    scratch directories (errors ignored), the path resolution and the file and directory reads of
    _check_copy, _check_member_calls, _check_launches, _stub_member_runner (_digest, _entries) and
    gettempdir, where an unreadable payload file reads as missing (a refutation, never a pass),
    the opening and reading of _check_env_canaries's sink and the marker probes there (an OSError
    from the sink is cannot-evaluate through _preflight_exit), and the is_dir and is_file probes in
    run(), _run_one and
    _require_subtree, where an unreadable path reads as absent. _require_subtree then makes the case
    cannot-evaluate, but a probe that reads a path as absent after the case found or wrote it (a path
    that became unreadable in between) is a refutation where the case judges that probe's outcome:
    a probe in run() or _run_one; closure/undecodable-wiring of _check_undecodable_fixture (also
    reached through _check_undecodable_attribution), whose _check_leg_wiring then names no-subtree
    instead of the decode cause; and the UTF-8 sub-check of _check_undecodable_attribution. These
    read a host fault as exit 1: misattribution, never a pass. A child the host kills or starves
    after it launched (for example out of memory) still completes with an exit code, which is
    judged."""
    def watched(what, inner, refused=(OSError,)):
        def call(*args, **kwargs):
            try:
                return inner(*args, **kwargs)
            except refused as exc:
                broke.append((what, str(exc)))
                raise
        return call
    return dict(
        tempfile=types.SimpleNamespace(mkdtemp=watched("scratch allocation", tempfile.mkdtemp)),
        _materialize=watched("copy", _materialize),
        _write_fixture=watched("fixture write", _write_fixture),
        _read_copied_store=watched("copied store read", _read_copied_store),
        _write_copied_store=watched("flip write", _write_copied_store),
        _not_utf8=watched("store read", _not_utf8),
        subprocess=types.SimpleNamespace(
            run=watched("child process", subprocess.run, (OSError, subprocess.TimeoutExpired)),
            PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired))


def _observed(label, call, injected=None):
    """call() under a _harness_watch of the host primitives bound now, with `injected` (the case's own
    stubs and stubbed failures, which are its fixture, not a host failure) patched over the watch.
    `injected` is a dict, or a function returning one that is called once the watch is bound (so a
    stub that delegates wraps the watch). Raises _HarnessFailure when the watch saw a host failure,
    whatever call() then did. Otherwise returns (result, None) when call() returned, or (None, exc):
    exc is call()'s AssertionError or _CannotEvaluate as raised, or, for any other Exception (a crash
    of the code under test, or a wrong result that the interpretation inside call() could not index
    or unpack), an AssertionError naming it. A BaseException that is not an Exception
    (KeyboardInterrupt, SystemExit) passes through."""
    broke = []
    with _patched(**_harness_watch(broke)):
        with _patched(**(injected() if callable(injected) else (injected or {}))):
            try:
                outcome = call(), None
            except (AssertionError, _CannotEvaluate) as exc:
                outcome = None, exc
            except Exception as exc:
                crash = AssertionError("{}: the code under test raised {}{}".format(
                    _cv("label", label), _cv("exception", type(exc).__name__),
                    ": " + _PIN_MARKER if str(exc) == _PIN_MARKER else ""))
                crash.__cause__ = exc
                outcome = None, crash
    if broke:
        raise _HarnessFailure("{}: harness failure: {} failed{}{}".format(
            _cv("label", label), _cvs("primitive", sorted(set(what for what, _text in broke))),
            " " + _PIN_MARKER if any(_PIN_MARKER in text for _what, text in broke) else "",
            " " + _INJECT_MARKER if any(_INJECT_MARKER in text for _what, text in broke) else "")
        ) from outcome[1]
    return outcome


def _attributed(label, call, injected=None):
    """_observed's outcome, raised: call()'s result is returned; its AssertionError or _CannotEvaluate,
    or an AssertionError naming any other Exception it raised, is raised; so is _HarnessFailure when
    the host failed. A case puts its interpretation of the code under test's result (indexing,
    unpacking, comparing) inside call(), so a malformed result is a refutation too."""
    result, exc = _observed(label, call, injected)
    if exc is not None:
        raise exc
    return result


def _stubbed_run(root, outcomes):
    """run(root) with _run_one stubbed by outcomes, through _attributed, while _ambient_sentinels
    sets GIT_ and PYTHON variables in os.environ; returns (rc, stdout, stderr, calls).
    Cannot-evaluate when root has no opf/ subtree, or when the watch saw the scratch directory or
    the copy fail (the message then names run()'s exit code and its own error). Otherwise a run()
    that crashed, or exited before any subset member ran, is refuted; the refutation says whether
    its copy was built, as observed at _materialize. So is a run() whose member calls do not
    reconcile with _DECLARED_ROSTER as _check_member_calls states it, with merged streams (labels
    starting closure/member-)."""
    _require_subtree(root)
    calls, seen, made = [], [], []

    def stubs():
        return dict(_run_one=_stub_member_runner(calls, outcomes), _materialize=_copy_recorder(made))
    try:
        with _ambient_sentinels():
            _attributed("closure/stubbed-run", lambda: seen.append(_captured(run, root)), injected=stubs)
    except _HarnessFailure as exc:
        if not seen:
            raise
        raise _HarnessFailure("closure/stubbed-run: run() exited {} after the watch saw {} fail".format(
            _n(seen[0][0]),
            _cvs("primitive", sorted(what for what in _CLOSED_VOCABULARIES["primitive"]
                                      if what in str(exc))))) from exc
    rc, out, err = seen[0]
    if not calls:
        raise AssertionError("closure/stubbed-run: run() exited {} before any subset member ran, "
                             "{}".format(_n(rc),
                                         "with its copy built" if made else "without building its copy"))
    _attributed("closure/member-calls", lambda: _check_member_calls(
        "closure/member", calls, made, root, False, _DECLARED_ROSTER))
    return rc, out, err, calls


def _check_timeout_mapping(root):
    """run() with _run_one stubbed so opf-tooling-selftest is killed at its bound: run() must exit 2
    with a TIMEOUT (cannot evaluate) line and no closure finding, and the per-member bound lookup must
    pass 1800 s to opf-tooling-selftest and 600 s to another member."""
    outcomes = {"opf-tooling-selftest": (2, "timed out after 1800s", _TIMEOUT)}
    rc, out, err, calls = _stubbed_run(root, outcomes)
    bounds = {call["name"]: call["timeout"] for call in calls}
    if bounds.get("opf-tooling-selftest") != 1800 or bounds.get("opf-homes-selftest") != 600:
        raise AssertionError("closure/member-bound-lookup: a wrong bound was passed to a member")
    if rc != 2:
        raise AssertionError("closure/timeout-exit: run() returned {} for a timed-out member".format(_n(rc)))
    lines = out.splitlines()
    if "  {:32s} {}".format("opf-tooling-selftest", "TIMEOUT (cannot evaluate)") not in lines:
        raise AssertionError("closure/timeout-line: no TIMEOUT (cannot evaluate) line")
    if "CANNOT EVALUATE" not in err or "BROKEN" in err:
        raise AssertionError("closure/timeout-attribution: a timeout was not reported as cannot-evaluate")


def _check_harness_mapping(root):
    """A missing subset script, a member killed at its bound, or an interpreter that cannot be
    launched is cannot-evaluate (exit 2), both from _run_one itself and through run(). The kill and
    the launch refusal are stubbed subprocess.run results patched over the watch (this case's
    fixture), so no real child is killed: _run_one's own mapping, and the bound it passes, are what
    is judged. Real children pin the negative leg's stream handling: evidence split across stdout
    and stderr, or after a CR on one line, is not caught, while the evidence alone on a stderr line
    (where opf.py prints it) is; and _run_one's exit code with the streams captured apart is the
    child's own (a child that prints the evidence and exits 0 is not caught). A real child that
    cannot be launched or does not finish within its bound is a host failure the watch observes
    (cannot-evaluate); any other result that is not a completed run refutes _run_one."""
    children = (
        ("split.py", "sys.stdout.write(E[:20])\nsys.stdout.flush()\nsys.stderr.write(E[20:] + '\\n')\n",
         "fail", 1),
        ("carriage.py", "sys.stdout.write('AssertionError: expected\\r' + E + '\\n')\n", "fail", 1),
        ("evidence.py", "print('unit opf-store: FAIL')\nsys.stderr.write(E + '\\n')\n", "caught", 1),
        ("evidence-zero.py", "sys.stderr.write(E + '\\n')\n", "fail", 0),
    )
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-harness-"))
            _write_fixture(tmp, [("tools/sleeper.py", b"import time\ntime.sleep(60)\n")] + [
                ("tools/" + name, "import sys\nE = {!r}\n{}sys.exit({})\n".format(
                    _FLIP_EVIDENCE, body, code).encode("utf-8")) for name, body, _want, code in children])
        except OSError as exc:
            raise _CannotEvaluate("closure/harness-scratch: could not build the harness fixture"
                                  + (": " + _STUB_SCRATCH if _STUB_SCRATCH in str(exc) else "")
                                  + (": " + _INJECT_MARKER if _INJECT_MARKER in str(exc) else ""))

        def missing_script():
            rc, _text, why = _run_one(tmp, "no_such_member.py", [], tmp, _isolated_env())
            if (rc, why) != (2, _HARNESS_MISSING):
                raise AssertionError("closure/harness-missing-script: rc {} with cannot label {}".format(
                    _n(rc), _cv("reason", why)))
        _attributed("closure/harness-missing-script", missing_script)

        bounds = []

        def expire(cmd, **kwargs):
            bounds.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        def refuse(*_args, **_kwargs):
            raise OSError("no interpreter (stubbed)")

        def stubbed(fn):
            return dict(subprocess=types.SimpleNamespace(
                run=fn, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))

        def killed():
            rc, _text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env(), timeout_s=1)
            if (rc, why) != (2, _TIMEOUT) or bounds != [1]:
                raise AssertionError("closure/run-one-timeout: rc {} with cannot label {} and {} recorded "
                                     "bound(s)".format(_n(rc), _cv("reason", why), _n(len(bounds))))
        _attributed("closure/run-one-timeout", killed, injected=stubbed(expire))

        def no_launch():
            rc, text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env())
            if (rc, why) != (2, _HARNESS_LAUNCH) or "could not launch the interpreter" not in text:
                raise AssertionError("closure/harness-launch: rc {} with cannot label {}".format(
                    _n(rc), _cv("reason", why)))
        _attributed("closure/harness-launch", no_launch, injected=stubbed(refuse))
        for name, _body, want, code in children:
            def judge(name=name, want=want, code=code):
                rc, text, why = _run_one(tmp, name, [], tmp, _isolated_env(), timeout_s=120,
                                         separate_streams=True)
                if why is not None:
                    raise AssertionError("closure/evidence-streams: {} did not run to completion ({}) "
                                         "with no host failure observed".format(
                                             _cv("probe", name), _cv("reason", why)))
                if rc != code:
                    raise AssertionError("closure/evidence-streams: {} exited {}, _run_one gave {}".format(
                        _cv("probe", name), _n(code), _n(rc)))
                got = _negative_leg_verdict(rc, text, why)[0]
                if got != want:
                    raise AssertionError("closure/evidence-streams: {} gave {}, expected {}".format(
                        _cv("probe", name), _cv("verdict", got), _cv("verdict", want)))
            _attributed("closure/evidence-streams", judge)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    listings = []
    for why, stub_text in ((_HARNESS_MISSING, "missing subset script in the copy: stubbed"),
                           (_HARNESS_LAUNCH, "could not launch the interpreter: stubbed")):
        outcomes = {"opf-drift-selftest": (2, stub_text, why)}
        rc, out, err, _calls = _stubbed_run(root, outcomes)
        if rc != 2 or "BROKEN" in err:
            raise AssertionError("closure/harness-exit: run() returned {} for a harness error".format(_n(rc)))
        if "  {:32s} {}".format("opf-drift-selftest",
                                  _cv("reason", why) + " (cannot evaluate)") not in out.splitlines():
            raise AssertionError("closure/harness-line: no {} (cannot evaluate) line".format(
                _cv("reason", why)))
        want_row = "  {} [{}]: {}".format(_cv("member", "opf-drift-selftest"), _cv("reason", why),
                                          _cv("repair", _REPAIR_GUIDANCE[why]))
        rows = [line for line in err.splitlines() if line == want_row]
        if len(rows) != 1:
            raise AssertionError("closure/harness-reason: the cannot-evaluate listing does not carry "
                                 "exactly the {} reason code line with its full fixed repair "
                                 "guidance".format(_cv("reason", why)))
        listings += rows
    if len(set(listings)) != 2:
        raise AssertionError("closure/harness-reason: the two harness failures were not told apart "
                             "in the cannot-evaluate listing")


def _check_failure_first(root):
    """run() is failure-first: a member that ran and failed exits 1 even while another member timed
    out, and both are listed (the break under BROKEN, with its fixed reproduce-in-isolation line
    naming the member's script and arguments; the timeout under CANNOT EVALUATE, with its fixed
    reason code). And a refuted registration check (the bound-table check stubbed to raise, with
    the copy stubbed hollow since no member runs) exits 1 with the fixed FAILED line naming the
    refuted check's label through the label gate, covering run()'s registration branch."""
    outcomes = {"opf-drift-selftest": (1, "ImportError: check_versions", None),
                "opf-tooling-selftest": (2, "timed out after 1800s", _TIMEOUT)}
    rc, _out, err, _calls = _stubbed_run(root, outcomes)
    if rc != 1:
        raise AssertionError("closure/failure-first: run() returned {} for a failed member beside a "
                             "timed-out one".format(_n(rc)))
    lines = err.splitlines()
    if (not any(line.startswith("  opf-drift-selftest (rc=1): reproduce in isolation: ")
                and line.endswith("python3 -I -B opf/tools/check_opf_drift.py --self-test")
                for line in lines)
            or not any(line.startswith("  opf-tooling-selftest [TIMEOUT]: ") for line in lines)
            or not any(line.startswith("STANDALONE CLOSURE: BROKEN") for line in lines)
            or not any(line.startswith("STANDALONE CLOSURE: CANNOT EVALUATE") for line in lines)):
        raise AssertionError("closure/failure-first-listing: the break and the timeout are not both listed")

    def refuted_registration():
        rc, _out, err = _captured(run, root)
        if rc != 1 or ("STANDALONE CLOSURE: FAILED (a registration check was refuted: "
                       "closure/member-timeout-rows)") not in err.splitlines():
            raise AssertionError("closure/failure-first: a refuted registration check did not exit 1 "
                                 "with the FAILED line naming its label")
    _attributed("closure/failure-first", refuted_registration, injected=dict(
        _check_member_timeout_rows=_refuted_rows, _materialize=_hollow_copy))


def _check_verdict_tables():
    """The self-test's own leg verdicts, pure (no scratch directory and no opf/ subtree, so no fixture
    that cannot be set up can hide them): a negative-leg timeout or harness error is cannot-evaluate
    and never caught; caught requires the import-refusal evidence as a complete line of one output
    stream; run()==2 on the positive leg is cannot-evaluate."""
    cases = [
        ((2, "timed out after 1800s", _TIMEOUT), "cannot"),
        ((2, _FLIP_EVIDENCE, _TIMEOUT), "cannot"),
        ((2, "could not launch the interpreter: x", _HARNESS_LAUNCH), "cannot"),
        ((0, "SELF-TEST PASS", None), "fail"),
        ((1, "Traceback (most recent call last):\nSomeOtherError: unrelated", None), "fail"),
        ((2, "opf: cannot bootstrap: _opf_schema (cannot evaluate)", None), "fail"),
        ((2, _FLIP_EVIDENCE, None), "caught"),
        ((1, _FLIP_EVIDENCE, None), "caught"),
        ((2, "unit opf-store: FAIL\n" + _FLIP_EVIDENCE + "\nopf: self-test FAILED", None), "caught"),
        # Caught needs a non-zero exit: rc 0 with the evidence line is not caught.
        ((0, _FLIP_EVIDENCE, None), "fail"),
        # The evidence must be a whole line: prefixed, suffixed or quoted evidence is no evidence.
        ((2, "x " + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + " x", None), "fail"),
        ((2, repr(_FLIP_EVIDENCE), None), "fail"),
        ((2, "AssertionError: expected {!r}".format(_FLIP_EVIDENCE), None), "fail"),
        # Whitespace around the evidence is no evidence (a strip()-based match would accept it).
        ((2, _FLIP_EVIDENCE + " ", None), "fail"),
        ((2, "  " + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + "\t", None), "fail"),
        # Lines split on "\n" only, each losing at most one trailing "\r": CRLF output is evidence,
        # but no other str.splitlines boundary makes a line of its own.
        ((2, _FLIP_EVIDENCE + "\r\n", None), "caught"),
        ((2, "unit opf-store: FAIL\r\n" + _FLIP_EVIDENCE + "\r\nopf: self-test FAILED\r\n", None), "caught"),
        ((2, "AssertionError: expected\r" + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + "\r\r", None), "fail"),
    ]
    for sep in ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        cases.append(((2, "x" + sep + _FLIP_EVIDENCE, None), "fail"))
        cases.append(((2, _FLIP_EVIDENCE + sep + "x", None), "fail"))
    # The evidence must be whole within ONE stream of the (stdout, stderr) pair.
    cases += [
        ((1, ("", _FLIP_EVIDENCE + "\n"), None), "caught"),
        ((1, (_FLIP_EVIDENCE + "\n", "Traceback (most recent call last):\n"), None), "caught"),
        ((1, (_FLIP_EVIDENCE[:20], _FLIP_EVIDENCE[20:] + "\n"), None), "fail"),
        ((1, (_FLIP_EVIDENCE[:20] + "\n", _FLIP_EVIDENCE[20:]), None), "fail"),
        ((2, ("timed out after 1800s", ""), _TIMEOUT), "cannot"),
    ]
    for index, (args, want) in enumerate(cases):
        def judge(index=index, args=args, want=want):
            got = _negative_leg_verdict(*args)[0]
            if got != want:
                raise AssertionError("closure/negative-verdict: case {} gave {}, expected {}".format(
                    _n(index), _cv("verdict", got), _cv("verdict", want)))
        _attributed("closure/negative-verdict", judge)
    for rc, want in ((0, "ok"), (1, "fail"), (2, "cannot")):
        def judge(rc=rc, want=want):
            got = _positive_leg_verdict(rc)[0]
            if got != want:
                raise AssertionError("closure/positive-verdict: rc {} gave {}, expected {}".format(
                    _n(rc), _cv("verdict", got), _cv("verdict", want)))
        _attributed("closure/positive-verdict", judge)


def _check_leg_wiring(root):
    """Both legs stubbed (run() and the flipped copy's runner) through the real _closure_legs, while
    _ambient_sentinels sets GIT_ and PYTHON variables in os.environ: the positive leg gives run()
    the root itself (closure/positive-root, resolved paths compared); the flipped copy's one member
    call is _DECLARED_NEGATIVE, reconciled as _check_member_calls states it with its stdout and
    stderr captured apart (labels starting closure/negative-run-); and the self-test exits 2 (not 0,
    not 1) when only cannot-evaluate remains, 1 when a leg was refuted whatever else could not be
    evaluated."""
    wiring = [
        (2, (2, "timed out after 1800s", _TIMEOUT), 2),
        (0, (2, "timed out after 1800s", _TIMEOUT), 2),
        (2, (2, _FLIP_EVIDENCE, None), 2),
        (0, (2, _FLIP_EVIDENCE, None), 0),
        (1, (2, _FLIP_EVIDENCE, None), 1),
        (0, (0, "SELF-TEST PASS", None), 1),
        # Failure-first: a refuted leg exits 1 whichever leg could not be evaluated.
        (1, (2, "timed out after 1800s", _TIMEOUT), 1),
        (1, (2, "could not launch the interpreter: x", _HARNESS_LAUNCH), 1),
        (2, (0, "SELF-TEST PASS", None), 1),
        (2, (1, "Traceback (most recent call last):\nSomeOtherError: unrelated", None), 1),
        # The (stdout, stderr) pair: evidence alone on stderr is caught, split across the two is not.
        (0, (1, ("", _FLIP_EVIDENCE + "\n"), None), 0),
        (0, (1, (_FLIP_EVIDENCE[:20], _FLIP_EVIDENCE[20:] + "\n"), None), 1),
    ]
    _require_subtree(root)
    for run_rc, neg, want in wiring:
        calls, made, roots = [], [], []

        def wired(run_rc=run_rc, neg=neg, want=want, calls=calls, made=made, roots=roots):
            failures, cannot = _closure_legs(root)
            # A store that is not UTF-8 text (judged here through the watched _not_utf8, not from the
            # leg's message) is malformed input: the flip cannot be made, so the case is
            # cannot-evaluate, naming the leg's own message.
            if not calls and _not_utf8(Path(root) / "opf" / "tools" / "_opf_store.py"):
                raise _CannotEvaluate("closure/self-test-exit: the flipped copy could not be built"
                                      + (": " + _NEG_DECODE_ERROR
                                         if any(text.startswith(_NEG_DECODE_ERROR) for text in cannot)
                                         else ""))
            # The negative leg's own bound lookup: the flipped opf.py --self-test runs under its row.
            bounds = [(call["name"], call["timeout"]) for call in calls]
            if bounds != [("opf-tooling-selftest", 1800)]:
                raise AssertionError("closure/negative-bound-lookup: the flipped copy's one member call "
                                     "or bound is wrong")
            # What the flipped copy's run executes: opf.py --self-test over the negative leg's own
            # copy, in its scratch directory, isolated, with the two streams captured apart.
            _check_member_calls("closure/negative-run", calls, made, root, True, _DECLARED_NEGATIVE)
            if [Path(where).resolve() for where in roots] != [Path(root).resolve()]:
                raise AssertionError("closure/positive-root: run() was not given the root itself "
                                     "exactly once")
            got = _captured(_self_test_exit, failures, cannot)[0]
            if got != want:
                raise AssertionError("closure/self-test-exit: a wiring row gave {}, expected {}".format(
                    _n(got), _n(want)))
        stub_run = (lambda rc, roots: (lambda where: roots.append(where) or rc))(run_rc, roots)
        with _ambient_sentinels():
            _attributed("closure/self-test-exit", wired, injected=(
                lambda stub_run=stub_run, neg=neg, calls=calls, made=made: dict(
                    run=stub_run, _run_one=_stub_member_runner(calls, {"opf-tooling-selftest": neg}),
                    _materialize=_copy_recorder(made))))


def _not_utf8(path):
    """True when the bytes at path are not UTF-8 text, judged independently of the code under test."""
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _expect_negative_cannot(label, legs, calls, prefix):
    """The negative leg's result `legs`, interpreted inside the caller's _attributed call: exactly one
    cannot-evaluate message, starting with `prefix`, no member run, and a self-test exit of 2 over
    it. Anything else, a malformed `legs` included, refutes `label`."""
    failures, cannot = legs
    if failures or calls or len(cannot) != 1 or not cannot[0].startswith(prefix):
        raise AssertionError("{}: {} failure(s) and {} cannot-evaluate message(s) with {} member "
                             "call(s), or a wrong prefix".format(
                                 _cv("label", label), _n(len(failures)), _n(len(cannot)),
                                 _n(len(calls))))
    if _captured(_self_test_exit, failures, cannot)[0] != 2:
        raise AssertionError("{}-exit: not exit 2".format(_cv("label", label)))


def _require_subtree(root):
    """Raise _CannotEvaluate unless root carries the opf/ subtree run() needs: a case built on the real
    subtree observes nothing without it, so its absence never reads as a refuted case."""
    if not (Path(root) / "opf" / "tools" / "opf.py").is_file():
        raise _CannotEvaluate("closure/no-subtree: opf/ subtree not found under the given root")


def _no_scratch(*_args, **_kwargs):
    raise OSError("no scratch directory (stubbed)")


def _broken_copy(_src, _dest):
    raise OSError("copy refused (stubbed)")


def _no_scratch_denied(*_args, **_kwargs):
    """_no_scratch with a symbolic cause: a denied allocation (EACCES)."""
    exc = OSError(_STUB_SCRATCH)
    exc.errno = errno.EACCES
    raise exc


def _broken_copy_full(_src, _dest):
    """_broken_copy with a symbolic cause: a full filesystem (ENOSPC)."""
    exc = OSError("copy refused (stubbed)")
    exc.errno = errno.ENOSPC
    raise exc


def _hollow_copy(_src, dest):
    """A copy stand-in for cases that refute run() before any member would need the copy."""
    return dest / "opf"


def _refuted_rows(_subset, _table):
    """A registration-check stand-in that is refuted, driving run()'s refuted-registration exit."""
    raise AssertionError("closure/member-timeout-rows: refuted (stubbed)")


def _check_scratch_and_copy_errors(root):
    """A scratch directory that cannot be allocated, or a copy of opf/ that run() cannot make, is
    cannot-evaluate, never a traceback and never a refuted leg: no scratch in the harness fixture, in
    the negative leg (one cannot-evaluate message, self-test exit 2) or in run() (exit 2, no member
    runs), and a refused copy in run() (exit 2, no member runs). Each refusal is a stub patched over
    the harness watch, so it is this case's fixture, not a host failure, and run()'s own exit code
    is what is checked. Setup order: the first two sub-checks need neither a scratch directory of
    the host's nor an opf/ subtree, so they run before the subtree is required; the run() scratch
    sub-check needs the subtree; the run() copy sub-check needs the subtree and a scratch directory
    of the host's, so it runs last. The negative leg's refused copy needs a scratch directory but no
    subtree, so it is its own case (_check_negative_copy_error). The denied and full rows drive an
    EACCES allocation refusal and an ENOSPC copy refusal and require run()'s FULL fixed cause
    line, so the symbolic errno cause, not only the message prefix, is pinned discriminably."""
    no_scratch = types.SimpleNamespace(mkdtemp=_no_scratch)
    _expect_cannot(_check_harness_mapping, root,
                   "closure/harness-scratch: could not build the harness fixture: " + _STUB_SCRATCH,
                   "closure/scratch-harness", injected=dict(tempfile=no_scratch))

    calls = []
    _attributed("closure/negative-harness", lambda: _expect_negative_cannot(
        "closure/negative-harness", _closure_legs(root), calls, _NEG_SCRATCH_ERROR), injected=dict(
            run=lambda _root: 0, _run_one=_stub_member_runner(calls, {}), tempfile=no_scratch))

    _require_subtree(root)
    scratch_line = ("error: could not allocate a scratch directory for the isolated opf/ copy "
                    "(cannot evaluate closure; cause {})")
    copy_line = ("error: could not materialize the isolated opf/ copy (cannot evaluate closure; "
                 "cause {})")
    for label, refusal, line in (
            ("closure/scratch-run", dict(tempfile=no_scratch),
             scratch_line.format(_cv("errno", "unknown errno"))),
            ("closure/copy-run", dict(_materialize=_broken_copy),
             copy_line.format(_cv("errno", "unknown errno"))),
            ("closure/scratch-run-denied",
             dict(tempfile=types.SimpleNamespace(mkdtemp=_no_scratch_denied)),
             scratch_line.format(_cv("errno", "EACCES"))),
            ("closure/copy-run-full", dict(_materialize=_broken_copy_full),
             copy_line.format(_cv("errno", "ENOSPC")))):
        calls = []

        def refused(label=label, line=line, calls=calls):
            rc, _out, err = _captured(run, root)
            if rc != 2 or calls or line not in err.splitlines():
                raise AssertionError("{}: run() gave {} with {} member call(s), or without its "
                                     "full fixed cause line".format(
                                         _cv("label", label), _n(rc), _n(len(calls))))
        _attributed(label, refused, injected=dict(refusal, _run_one=_stub_member_runner(calls, {})))


def _check_negative_copy_error(root):
    """A broken copy the negative leg cannot build is one cannot-evaluate message (self-test exit 2),
    never a traceback and never a refuted leg. The stubbed copy refuses before reading anything, so
    this needs no opf/ subtree; it needs a scratch directory, and when the host cannot allocate one
    (a host failure, observed by _attributed) the case is cannot-evaluate."""
    calls = []
    _attributed("closure/negative-copy", lambda: _expect_negative_cannot(
        "closure/negative-copy", _closure_legs(root), calls, _NEG_COPY_ERROR), injected=dict(
            run=lambda _root: 0, _run_one=_stub_member_runner(calls, {}), _materialize=_broken_copy))


def _expect_cannot(check, root, cause, label, injected=None):
    """check(root), through _observed with `injected` patched over the watch, must raise
    _CannotEvaluate naming `cause`: not pass, not refute a leg, not crash. A host failure that this
    call's own watch saw is cannot-evaluate (_HarnessFailure passes through); a _CannotEvaluate that
    check(root) raised, a nested watch's included, is judged by its cause. A check that refuted a
    leg instead raises its own AssertionError as is (its text is closed by construction and the
    attribution pins match on it)."""
    _result, got = _observed(label, lambda: check(root), injected)
    if got is None:
        raise AssertionError("{}: {} passed with nothing to evaluate".format(
            _cv("label", label), _cv("case", check.__name__)))
    if isinstance(got, _CannotEvaluate):
        if cause not in str(got):
            raise AssertionError("{}: {} did not name the expected cause".format(
                _cv("label", label), _cv("case", check.__name__)))
        return
    raise got


def _check_absent_subtree(root):
    """With no opf/ subtree under the root, each stubbed case is cannot-evaluate naming the real cause
    (never a refuted leg). Over the real subtree, a copy that the watch sees fail before any member
    runs makes _stubbed_run cannot-evaluate, naming run()'s own error (run()'s exit 2 for a refused
    copy is pinned by closure/copy-run). Conversely, a run() that exits before any member runs with
    no host failure is a wrong result of the code under test: a refutation, never cannot-evaluate,
    saying whether its copy was built (here a refused registration check after the copy, and a run()
    that returns 0 without copying)."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-absent-"))
        except OSError as exc:
            raise _CannotEvaluate("closure/absent-subtree-scratch: no scratch directory")
        absent = tmp / "no-opf-root"
        _expect_cannot(_check_timeout_mapping, absent, "closure/no-subtree", "closure/absent-subtree")
        _expect_cannot(_check_harness_mapping, absent, "closure/no-subtree", "closure/absent-subtree")
        _expect_cannot(_check_failure_first, absent, "closure/no-subtree", "closure/absent-subtree")
        _expect_cannot(_check_leg_wiring, absent, "closure/no-subtree", "closure/absent-subtree")
        _expect_cannot(_check_scratch_and_copy_errors, absent, "closure/no-subtree",
                       "closure/absent-subtree")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    _require_subtree(root)
    # The refused copy sits OVER this call's watch and UNDER _stubbed_run's, so for _stubbed_run it
    # is a host failure.
    _expect_cannot(_check_timeout_mapping, root, "the watch saw copy fail", "closure/copy-before-members",
                   injected=dict(_materialize=_broken_copy))

    def refused_rows(_subset, _table):
        raise AssertionError("closure/member-timeout-rows: stubbed")
    for label, stub, want in (
            ("closure/members-never-ran", dict(_check_member_timeout_rows=refused_rows),
             "closure/stubbed-run: run() exited 1 before any subset member ran, with its copy built"),
            ("closure/members-never-copied", dict(run=lambda _root: 0),
             "closure/stubbed-run: run() exited 0 before any subset member ran, without building its copy")):
        _result, got = _observed(label, lambda: _check_timeout_mapping(root), stub)
        if type(got) is not AssertionError or not str(got).startswith(want):
            if isinstance(got, AssertionError):
                raise got
            raise AssertionError("{}: a run() that ran no member gave the wrong outcome".format(
                _cv("label", label)))


def _check_missing_input(_root):
    """run() over a root it cannot evaluate returns 2 with no subset member run: a root that does not
    exist, an empty root, and a root whose opf/tools/ has no opf.py. Each run() call goes through
    _attributed with _run_one stubbed to record its calls, so a run() that returns anything but 2, or
    runs a member, is refuted. Builds its own roots in one scratch directory (the empty root is that
    directory before anything is written to it), so it needs no opf/ subtree under the root; a host
    failure building them is cannot-evaluate."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-missing-"))
        except OSError as exc:
            raise _CannotEvaluate("closure/missing-input-scratch: no scratch directory")
        for label, where, files in (
                ("closure/missing-input-nonexistent", tmp / "no-such-root", None),
                ("closure/missing-input-empty", tmp, None),
                ("closure/missing-input-no-opf-py", tmp / "tools-only",
                 (("opf/tools/_opf_store.py", b"from _semver import _parse\n"),))):
            if files is not None:
                try:
                    _write_fixture(where, files)
                except OSError as exc:
                    raise _CannotEvaluate("{}: could not build the root".format(_cv("label", label)))
            calls = []

            def refused(label=label, where=where, calls=calls):
                rc, _out, _err = _captured(run, where)
                if rc != 2 or calls:
                    raise AssertionError("{}: run() gave {} with {} member call(s)".format(
                        _cv("label", label), _n(rc), _n(len(calls))))
            _attributed(label, refused, injected=dict(_run_one=_stub_member_runner(calls, {})))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _check_member_boundary(root):
    """The subprocess boundary, through the REAL run() and _run_one, over a scratch root whose
    opf/tools/ holds _RECORDER as opf.py and as every _DECLARED_ROSTER script (and an _opf_store.py
    the negative leg can flip), while _ambient_sentinels sets GIT_ and PYTHON variables in
    os.environ and _launch_recorder records each launch over the watch. closure/boundary-run: run()
    exits 0 and its launches reconcile with _DECLARED_ROSTER as _check_launches states it, with
    stderr merged into stdout. closure/boundary-negative: the real _closure_legs over the same root,
    run() stubbed to record its argument, gives run() that root, observes no cannot-evaluate, and its
    one launch reconciles with _DECLARED_NEGATIVE, stderr captured apart. closure/boundary-failed:
    the real run() over a second scratch root, where opf-drift-selftest's script exits 1 and
    opf-doctor-selftest's kills itself with SIGKILL (every other member a recorder), exits 1 and
    lists both as FAILED (rc=1 and rc=-9, on stdout and on stderr in lines after its first line
    starting "STANDALONE CLOSURE: BROKEN "), every other member OK and nothing cannot-evaluate: so
    a _run_one or run() that reads a failed or killed member as passed fails here. Needs a scratch
    directory but no opf/ subtree; `root` is the repository the copies must lie outside. A host failure
    building the fixture, or one the watch sees, is
    cannot-evaluate. Not covered: the members' own scripts (the children here are recorders), and
    what run() prints of a real member's output beyond its exit code."""
    failed = {"opf-drift-selftest": ("check_opf_drift.py", b"import sys\nsys.exit(1)\n", 1),
              "opf-doctor-selftest": ("check_opf_doctor.py",
                                      b"import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
                                      -signal.SIGKILL)}
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-boundary-"))
            fixture, failing = tmp / "root", tmp / "failing"
            scripts = sorted(set(["opf.py"] + [row[1] for row in _DECLARED_ROSTER]))
            _write_fixture(fixture, [("opf/tools/" + script, _RECORDER.encode("utf-8")) for script in scripts]
                           + [("opf/tools/_opf_store.py", b"from _semver import _parse\n")])
            broken = dict((script, body) for script, body, _code in failed.values())
            _write_fixture(failing, [("opf/tools/" + script, broken.get(script, _RECORDER.encode("utf-8")))
                                     for script in scripts])
        except OSError as exc:
            raise _CannotEvaluate("closure/boundary-scratch: could not build the fixture")
        made, launches = [], []

        def boundary_run():
            rc = _captured(run, fixture)[0]
            if rc != 0:
                raise AssertionError("closure/boundary-run: run() exited {} over recording "
                                     "children".format(_n(rc)))
            _check_launches("closure/boundary-run", launches, made, fixture, root, _DECLARED_ROSTER,
                            subprocess.STDOUT)
        with _ambient_sentinels():
            _attributed("closure/boundary-run", boundary_run, injected=lambda: dict(
                subprocess=_launch_recorder(launches, "closure/boundary-run"), _materialize=_copy_recorder(made)))
        neg_made, neg_launches, roots = [], [], []

        def boundary_negative():
            _failures, cannot = _closure_legs(fixture)
            if cannot or [Path(where).resolve() for where in roots] != [fixture.resolve()]:
                raise AssertionError("closure/boundary-negative: run() was not given the fixture exactly "
                                     "once, or a leg could not be evaluated ({} cannot-evaluate "
                                     "message(s))".format(_n(len(cannot))))
            _check_launches("closure/boundary-negative", neg_launches, neg_made, fixture, root,
                            _DECLARED_NEGATIVE, subprocess.PIPE)
        with _ambient_sentinels():
            _attributed("closure/boundary-negative", boundary_negative, injected=lambda: dict(
                run=lambda where: roots.append(where) or 0,
                subprocess=_launch_recorder(neg_launches, "closure/boundary-negative"),
                _materialize=_copy_recorder(neg_made)))

        def boundary_failed():
            rc, out, err = _captured(run, failing)
            lines, errs = out.splitlines(), err.splitlines()
            codes = dict((name, code) for name, (_script, _body, code) in failed.items())
            wrong = [name for name, _script, _args, _bound in _DECLARED_ROSTER if "  {:32s} {}".format(
                name, "FAILED rc={}".format(codes[name]) if name in codes else "OK") not in lines]
            heads = [index for index, line in enumerate(errs)
                     if line.startswith("STANDALONE CLOSURE: BROKEN ")]
            under = errs[heads[0] + 1:] if heads else []
            wrong += [name for name, code in codes.items()
                      if not any(line.startswith("  {} (rc={})".format(name, code)) for line in under)]
            if rc != 1 or wrong or "CANNOT EVALUATE" in err:
                raise AssertionError("closure/boundary-failed: run() exited {} over a member that exits "
                                     "1 and one killed by SIGKILL, with wrong or missing lines for "
                                     "{}".format(_n(rc), _cvs("member", wrong)))
        _attributed("closure/boundary-failed", boundary_failed)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _check_env_canaries(root):
    """No diagnostic carries a value of the environment (every diagnostic emits only closed text,
    and closure/closed-sites pins that structurally; this case observes it with a canary),
    and no recorded launch writes to an inherited descriptor, while _ENV_CANARY is set in
    os.environ (beside _AMBIENT_SENTINELS). Each sub-check's refutation text, everything it
    printed, and every (stdout, stderr) pair any nested _captured collected while it ran
    (_CAPTURE_LOG, kept on the return and the exception path alike) must not contain the canary's
    value; an AssertionError, _CannotEvaluate or _HarnessFailure a sub-check raises is scanned,
    with those captures, before it is re-raised:
    closure/env-canary-m17: _check_member_boundary with _isolated_env returning os.environ itself
      is refuted at closure/boundary-run-env (an environment of the wrong type, named by a fixed
      fault text);
      what run() printed inside the sub-check's own nested captures is scanned on the return path,
      and a raised _HarnessFailure or _CannotEvaluate is scanned before the re-raise;
    closure/env-canary-streams: a launch through _launch_recorder whose stdout is None, DEVNULL,
      the integer descriptor 1, the canary's value or a list holding it (stderr STDOUT), or whose
      stderr is None, DEVNULL, the integer descriptor 2, the canary's value or a tuple holding it
      (stdout PIPE), is refused (closure/env-canary-streams, naming the stream field and the check
      it failed from fixed text) before the child runs: the child, which would create a marker
      file, created nothing and nothing was recorded;
    closure/env-canary-kwargs: a launch through _launch_recorder missing a required keyword
      (timeout) is refused (closure/env-canary-kwargs) before the child runs, the same refusal as
      an extra keyword: the child created nothing and nothing was recorded;
    closure/env-canary-descriptors: a launch through _launch_recorder with the permitted keywords
      plus one of pass_fds, close_fds=False, stdin or preexec_fn, each handing the child a
      descriptor of a sink file in the scratch directory (duplicated above the standard
      descriptors with F_DUPFD and made inheritable, so a host whose own stdin, stdout or stderr
      is closed cannot put the sink on a number a permitted child legitimately holds; dup'd onto
      the child's stdin, for preexec_fn), is refused (closure/env-canary-descriptors-kwargs)
      before the child runs: the child, which would create a marker file and start a grandchild
      that writes the canary's value to that descriptor, created nothing, nothing was recorded,
      and the sink stays empty; a permitted launch, while the sink's descriptor is inheritable,
      starts a probe child that holds no descriptor that IS the sink (the probe stats the sink's
      path FIRST and reports an unreadable sink as such, which is cannot-evaluate here, never a
      pass; only an fstat that fails with the closed-descriptor error EBADF establishes absence,
      any other fstat error is cannot-evaluate too; os.path.samestat against the sink, so a number
      that is open in the child as another file never reads as held: the probe asked about the
      number 0, open as the child's null stdin, must report not held) and whose stdin is the null
      device (a recorder that dropped its stdin=DEVNULL is seen whenever this process's own stdin
      is not the null device);
    closure/env-canary-launch: _check_launches over a recorded launch whose cwd is the canary's
      value, and over one whose argument list carries it, is refuted (-cwd, -argv) with no value
      printed in the refutation;
    closure/env-canary-report: _check_launches passes a valid report (the control); over a launch
      whose output ends in a line that is not a report (the canary on the line before) it names the
      byte count only; over each report that is valid but for one of _REPORT_FIELDS, which holds
      the canary's value, a list holding it or a dict keyed and valued by it, it is refuted naming
      that field and its check, and no value; so it is over a report wrong in isolated carrying the
      canary in a field of its own;
    closure/env-canary-noise: a callback that prints the canary's value and then raises an
      AssertionError, or a _CannotEvaluate, is caught by the capture scan on the exception path.
    Needs a scratch directory but no opf/ subtree; a host failure building the fixture, or one a
    watch sees, is cannot-evaluate, and so is a sub-check that observed nothing it could judge (a
    _CannotEvaluate, for example TMPDIR inside the repository), once its text and its captures are
    found free of the canary. Opening, duplicating and reading the sink is this case's own host
    I/O, unwatched: an OSError there is cannot-evaluate through _preflight_exit. Inherited
    descriptors are judged from the child's marker and the sink (a refused launch never ran) and
    from _launch_recorder, which launches only with _LAUNCH_KEYWORDS, stdout piped and stdin from
    DEVNULL; output this process writes itself is captured (_captured) and scanned."""
    name, value = _ENV_CANARY
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-canary-"))
            child, descendant, sink = tmp / "child.py", tmp / "descendant.py", tmp / "sink"
            _write_fixture(tmp, [
                ("child.py", b"import sys\nopen(sys.argv[1], 'wb').close()\n"),
                ("descendant.py", (
                    "import os, subprocess, sys\n"
                    "open(sys.argv[1], 'wb').close()\n"
                    "fd = int(sys.argv[2])\n"
                    "grandchild = 'import os, sys\\nos.write(int(sys.argv[1]), os.environ[{!r}].encode())\\n'\n"
                    "subprocess.run([sys.executable, '-I', '-c', grandchild, str(fd)],\n"
                    "               pass_fds=(fd,) if fd > 2 else (), timeout=60)\n").format(name).encode("utf-8")),
                ("probe.py", b"import errno, json, os, sys\n"
                             b"try:\n    sink = os.stat(sys.argv[2])\nexcept OSError:\n"
                             b"    print(json.dumps(dict(sink='unreadable')))\n    raise SystemExit(0)\n"
                             b"try:\n    own = os.fstat(int(sys.argv[1]))\nexcept OSError as exc:\n"
                             b"    held = False if exc.errno == errno.EBADF else None\nelse:\n"
                             b"    held = os.path.samestat(own, sink)\n"
                             b"print(json.dumps(dict(held=held, "
                             b"stdin_null=os.path.samestat(os.fstat(0), os.stat(os.devnull)))))\n"),
                ("sink", b"")])
        except OSError as exc:
            raise _CannotEvaluate("closure/env-canary-scratch: could not build the fixture")

        def carried(mark):
            return any(value in text for pair in _CAPTURE_LOG[mark:] for text in pair)

        def judged(label, outcome, start, mark):
            got, out, err = outcome
            if value in str(got) or value in out or value in err or carried(mark):
                raise AssertionError("{}: a diagnostic or a captured stream carries the canary "
                                     "variable's value".format(_cv("label", label)))
            if isinstance(got, _CannotEvaluate):
                raise got
            if start is None:
                if got is not None:
                    raise AssertionError("{}: expected no refutation".format(_cv("label", label)))
            elif not isinstance(got, AssertionError) or not str(got).startswith(start):
                raise AssertionError("{}: expected a refutation with the stated start, got another "
                                     "outcome{}".format(
                                         _cv("label", label),
                                         " (the code under test raised: " + _PIN_MARKER + ")"
                                         if "the code under test raised" in str(got)
                                         and _PIN_MARKER in str(got) else ""))

        def attempt(label, fn):
            mark = len(_CAPTURE_LOG)
            try:
                result, out, err = _captured(_attributed, label, fn)
            except (AssertionError, _CannotEvaluate) as exc:
                if value in str(exc) or carried(mark):
                    raise AssertionError("{}: a diagnostic or a captured stream carries the canary "
                                         "variable's value".format(_cv("label", label))) from None
                raise
            except BaseException:
                # A terminating exception (SystemExit, KeyboardInterrupt) must not skip the
                # capture scan: _captured kept the pair, so a canary printed before the
                # termination is still caught (the alarm takes priority over the termination).
                if carried(mark):
                    raise AssertionError("{}: a captured stream carries the canary variable's "
                                         "value".format(_cv("label", label))) from None
                raise
            return result, out, err, mark

        def permitted(launches, number):
            _launch_recorder(launches, "closure/env-canary-descriptors").run(
                [sys.executable, "-I", "-B", str(tmp / "probe.py"), str(number), str(sink)], cwd=str(tmp),
                env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            try:
                return json.loads(launches[-1][2].strip().splitlines()[-1])
            except (IndexError, ValueError):
                raise AssertionError("closure/env-canary-descriptors: the permitted probe gave no report")

        def refused(label, launches, script, args, **kwargs):
            try:
                _launch_recorder(launches, label).run([sys.executable, "-I", "-B", str(script), *args], **kwargs)
            except AssertionError as exc:
                return exc
            return None

        with _ambient_sentinels(_AMBIENT_SENTINELS + (_ENV_CANARY,)):
            mark = len(_CAPTURE_LOG)
            try:
                result, out, err = _captured(lambda: _observed("closure/env-canary-m17", lambda: _check_member_boundary(
                    root), injected=dict(_isolated_env=lambda: os.environ)))
            except (AssertionError, _CannotEvaluate) as exc:
                if value in str(exc) or carried(mark):
                    raise AssertionError("closure/env-canary-m17: a diagnostic or a captured stream "
                                         "carries the canary variable's value") from None
                raise
            judged("closure/env-canary-m17", (result[1], out, err), "closure/boundary-run-env: ", mark)

            pipe, merged, null = subprocess.PIPE, subprocess.STDOUT, subprocess.DEVNULL
            stream_cases = ((None, merged), (null, merged), (1, merged), (value, merged), ([value], merged),
                            (pipe, None), (pipe, null), (pipe, 2), (pipe, value), (pipe, (value,)))
            for index, (stdout, stderr) in enumerate(stream_cases):
                launches, marker = [], tmp / "ran-stream-{}".format(index)
                got, out, err, mark = attempt("closure/env-canary-streams", functools.partial(
                    refused, "closure/env-canary", launches, child, [str(marker)], cwd=str(tmp),
                    env=dict(os.environ), stdout=stdout, stderr=stderr, timeout=120))
                judged("closure/env-canary-streams", (got, out, err), "closure/env-canary-streams: ", mark)
                if launches or marker.exists():
                    raise AssertionError("closure/env-canary-streams: the child ran (stream case {}; no "
                                         "stream value is printed)".format(_n(index)))

            launches, marker = [], tmp / "ran-missing-timeout"
            got, out, err, mark = attempt("closure/env-canary-kwargs", functools.partial(
                refused, "closure/env-canary", launches, child, [str(marker)], cwd=str(tmp),
                env=dict(os.environ), stdout=pipe, stderr=merged))
            judged("closure/env-canary-kwargs", (got, out, err), "closure/env-canary-kwargs: ", mark)
            if launches or marker.exists():
                raise AssertionError("closure/env-canary-kwargs: the child ran with a required keyword missing")

            with open(sink, "r+b") as low, io.FileIO(fcntl.fcntl(low.fileno(), fcntl.F_DUPFD, 3), "r+") as held:
                fd = held.fileno()
                os.set_inheritable(fd, True)
                for extra, passed in ((dict(pass_fds=(fd,)), fd), (dict(close_fds=False), fd),
                                      (dict(stdin=fd), 0), (dict(preexec_fn=functools.partial(os.dup2, fd, 0)), 0)):
                    keyword = sorted(extra)[0]
                    launches, marker = [], tmp / "ran-{}".format(keyword)
                    got, out, err, mark = attempt("closure/env-canary-descriptors", functools.partial(
                        refused, "closure/env-canary-descriptors", launches, descendant, [str(marker), str(passed)],
                        cwd=str(tmp), env=dict(os.environ), stdout=pipe, stderr=merged, timeout=120, **extra))
                    judged("closure/env-canary-descriptors", (got, out, err),
                           "closure/env-canary-descriptors-kwargs: ", mark)
                    if launches or marker.exists():
                        raise AssertionError("closure/env-canary-descriptors: the child ran with "
                                             "{}".format(_cv("keyword", keyword)))
                for number in (fd, 0):
                    launches = []
                    got, out, err, mark = attempt("closure/env-canary-descriptors",
                                                  functools.partial(permitted, launches, number))
                    if isinstance(got, dict) and (got.get("sink") == "unreadable" or got.get("held") is None):
                        raise _CannotEvaluate("closure/env-canary-descriptors: the probe could not "
                                              "identify the sink, or its fstat failed with an error "
                                              "other than the closed-descriptor one, so absence was "
                                              "not established (cannot evaluate)")
                    if value in out or value in err or carried(mark) or got != dict(held=False, stdin_null=True):
                        raise AssertionError(
                            "closure/env-canary-descriptors: a permitted launch's child held a descriptor that is "
                            "the sink, a stdin other than the null device, or (asked about descriptor number {}) "
                            "read another file's descriptor as held".format(
                                "0, its null stdin" if number == 0 else "the one it was handed"))
            if sink.read_bytes():
                raise AssertionError("closure/env-canary-descriptors: a descendant wrote to the sink's descriptor")

            dest = tmp / "run"
            made = [(tmp / "root" / "opf", dest, dest / "opf")]
            cmd = [sys.executable, "-I", "-B", str(dest / "opf" / "tools" / "opf.py"), "--self-test"]
            kwargs = dict(cwd=str(dest), env={"PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=_DECLARED_NEGATIVE[0][3])
            valid = dict(argv=cmd[3:], isolated=1, no_bytecode=1, cwd=str(dest), entries=["opf"], env_leaked=[],
                         env_no_bytecode=True)

            def launched(label, one_cmd, one_kwargs, out_text):
                try:
                    _check_launches(label, [(one_cmd, one_kwargs, out_text)], made, tmp / "root",
                                    root, _DECLARED_NEGATIVE, subprocess.PIPE)
                except (AssertionError, _CannotEvaluate) as exc:
                    return exc
                return None

            report_text = json.dumps(valid) + "\n"
            for want, wrong_cmd, wrong_kwargs in (
                    ("closure/env-canary-launch-cwd: ", cmd, dict(kwargs, cwd=value)),
                    ("closure/env-canary-launch-argv: ", [sys.executable, "-I", "-B", cmd[3], value], kwargs)):
                got, out, err, mark = attempt("closure/env-canary-launch", functools.partial(
                    launched, "closure/env-canary-launch", wrong_cmd, wrong_kwargs, report_text))
                judged("closure/env-canary-launch", (got, out, err), want, mark)

            start = "closure/env-canary-report-child: opf-tooling-selftest "
            cases = [(json.dumps(valid) + "\n", None),
                     ("{} {}\nTraceback: not a report\n".format(name, value), start + "gave no report ("),
                     (json.dumps(dict(valid, isolated=0, canary=value)) + "\n", start + "reported a wrong isolated (")]
            cases += [(json.dumps(dict(valid, **{field: wrong})) + "\n", start + "reported a wrong {} (".format(field))
                      for field in _REPORT_FIELDS for wrong in (value, [value], {value: value})]
            for out_text, want in cases:
                got, out, err, mark = attempt("closure/env-canary-report", functools.partial(
                    launched, "closure/env-canary-report", cmd, kwargs, out_text))
                judged("closure/env-canary-report", (got, out, err), want, mark)

            def noisy(exc_type):
                def probe():
                    _emit(value)
                    raise exc_type("closure/env-canary-noise: synthetic refusal (stubbed)")
                return probe
            for exc_type in (AssertionError, _CannotEvaluate):
                try:
                    attempt("closure/env-canary-noise", noisy(exc_type))
                except AssertionError as alarm:
                    if value in str(alarm) or not str(alarm).startswith("closure/env-canary-noise: "):
                        raise AssertionError("closure/env-canary-noise: a wrong alarm")
                else:
                    raise AssertionError("closure/env-canary-noise: a callback that printed the canary and raised "
                                         "was not caught by the capture scan")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# The sites closure/closed-sites permits although their arguments are not closed, as (the
# qualname of the code holding the site, the callee's dotted name, the recorded reason). These
# record UNCLOSED TEXT at an emitter call or an exception constructor only; a channel reference
# outside the emitter has no exception here (the allowlist admits exactly the emitter and the
# floor guard). An entry matching no site, a duplicate entry, or one with no reason is stale (a
# refutation).
_CLOSED_SITE_EXCEPTIONS = (
    ("_self_test_exit", "_emit",
     "relays refutation and cannot-evaluate text that reaches it only as str() of exceptions this "
     "module raised and of leg messages built at closed sites; every construction site is checked "
     "by this walk, so the relayed text is closed by construction"),
    ("_check_attribution_inventory", "_CannotEvaluate",
     "joins cannot-evaluate texts its own entries appended at closed sites above; no other text "
     "enters the list"),
    ("_check_closed_sites", "AssertionError",
     "every text it formats derives from this file's own source, its registries and its flip "
     "table, never from a runtime value"),
    ("_check_harness_mapping.<locals>.expire", "subprocess.TimeoutExpired",
     "a stubbed child kill whose arguments are a fixture command and bound: _run_one maps it to "
     "fixed text, and the watch relays only the primitive's name and a marker"),
    ("_check_env_canaries.<locals>.noisy.<locals>.probe", "_emit",
     "emits the raw canary inside a capture on purpose, to prove the capture scan catches it"),
    ("_patched", "globals",
     "the one deliberate runtime mutator: rebinds module globals for a stubbed case and refuses "
     "every protected name first; the replacement values are the case's own code, walked where "
     "it is defined"),
    ("_nth_call", "globals",
     "reads the global a pin entry names so the entry can patch it; the patch is installed "
     "through _patched, which refuses every protected name"),
    ("_nth_call", "getattr",
     "reads the attribute a pin entry names on the target it patches; the entry's name and "
     "attribute come from the pinned _ATTRIBUTION_PINS table, not from runtime input"),
    ("_derived_sites", "globals",
     "reads this module's own values to find which hold code (_holds_code); it writes nothing "
     "and calls nothing it finds"),
    ("_Overlay.__getattr__", "getattr",
     "reads, by the name Python asked for, an attribute of the wrapped target so unpatched "
     "attributes pass through; it writes nothing"),
    ("_Overlay.__init__", "__dict__",
     "stores the replacement attributes a pin entry passed on the overlay instance itself, "
     "never on a module or a gate"),
    ("_holds_code", "getattr",
     "reads an object's __dict__ by literal name to inspect instance attributes for held code; "
     "it writes nothing"),
    ("_launch_recorder", "subprocess",
     "wraps the subprocess module in an _Overlay so a recorded launch is refused or recorded "
     "before any child starts; the wrap replaces run alone and reads everything else through"),
    ("_failing_harness", "tempfile",
     "captures the tempfile module bound now so the stand-in can delegate to it; the stand-in "
     "raises only the fixed injected OSError"),
)

# The emitter's statements, pinned verbatim: _check_closed_sites requires this file's one
# module-level _emit def to match this source exactly (its docstring aside, compared by AST
# dump), so the emitter cannot grow a statement, a parameter, a decorator, a different stream
# pick or a weaker exit clamp without failing there (_EMITTER_FLIPS each break one condition).
_EMITTER_SOURCE = (
    "def _emit(text=None, err=False, code=None):\n"
    "    stream = sys.stderr if err else sys.stdout\n"
    "    if text is not None:\n"
    '        stream.write(text + "\\n")\n'
    "    if code is not None:\n"
    "        sys.exit(code if type(code) is int else 2)\n")


def _emitter_matches(node):
    """True when `node` is a def of the one closed emitter in exactly the _EMITTER_SOURCE shape:
    the same name, signature and statements by AST dump (a leading docstring is dropped first),
    with no decorator, no return annotation and no type parameters."""
    want = ast.parse(_EMITTER_SOURCE).body[0]
    if not isinstance(node, ast.FunctionDef) or node.name != "_emit":
        return False
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return (not node.decorator_list and node.returns is None and not node.type_params
            and ast.dump(node.args) == ast.dump(want.args)
            and [ast.dump(part) for part in body] == [ast.dump(part) for part in want.body])


def _callee_name(func):
    """The dotted name of a call's callee node, or "<dynamic>" for one no name chain reaches."""
    if isinstance(func, ast.Name):
        return func.id
    parts, node = [], func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if parts and isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return "<dynamic>"


def _closed_argument(node):
    """True when `node` can evaluate only to closed text (closure/closed-sites): a str or int
    constant (bool is not), a Name registered in _CLOSED_TEXT_NAMES (trusted because
    _check_closed_sites requires each registered name to be bound exactly once at module level to
    a string literal and never rebound or shadowed in this file), a call to a gate (_cv, _cvs,
    _n) with positional arguments only (the gate closes them at run time), a .format(...) whose
    receiver and every argument are closed, a + concatenation of closed pieces, or an if/else whose
    both branches are closed (the test only chooses between closed texts, it cannot add one)."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int)) and not isinstance(node.value, bool)
    if isinstance(node, ast.Name):
        return node.id in _CLOSED_TEXT_NAMES
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in ("_cv", "_cvs", "_n"):
            return not node.keywords and not any(isinstance(arg, ast.Starred) for arg in node.args)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "format"
                and _closed_argument(node.func.value)):
            return (not node.keywords and not any(isinstance(arg, ast.Starred) for arg in node.args)
                    and all(_closed_argument(arg) for arg in node.args))
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _closed_argument(node.left) and _closed_argument(node.right)
    if isinstance(node, ast.IfExp):
        return _closed_argument(node.body) and _closed_argument(node.orelse)
    return False


def _bound_names(node):
    """Every name `node` binds, syntactically: a Name stored or deleted (an assignment, an
    augmented, annotated or walrus assignment, a del, a for, with or comprehension target), a
    parameter, an except name, an import alias, a def or class name, a global or nonlocal
    declaration, a match capture, or a type parameter. Yields (name, node): comparing the node
    against a known permitted target tells the one permitted binding apart."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        yield node.id, node
    elif isinstance(node, ast.arg):
        yield node.arg, node
    elif isinstance(node, ast.ExceptHandler) and node.name is not None:
        yield node.name, node
    elif isinstance(node, ast.alias):
        yield (node.asname if node.asname is not None else node.name.split(".")[0]), node
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name, node
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        for name in node.names:
            yield name, node
    elif isinstance(node, ast.MatchAs) and node.name is not None:
        yield node.name, node
    elif isinstance(node, ast.MatchStar) and node.name is not None:
        yield node.name, node
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        yield node.rest, node
    elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        yield node.name, node


def _registered_name_faults(tree, names):
    """For each of `names`, why it cannot serve as a registered closed-text name over `tree`, as
    (name, why): the name must be bound EXACTLY ONCE in the whole module, by one module-level
    assignment of one string literal to the bare name, and never rebound or shadowed anywhere
    (_bound_names lists the binding forms). The scan is syntactic over every scope on purpose: a
    shadowing binding is refused even where Python's scoping would keep the module value readable
    elsewhere, because _closed_argument trusts the bare name and cannot see scopes."""
    permitted = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            permitted.setdefault(node.targets[0].id, []).append(node.targets[0])
    faults = []
    for name in names:
        targets = permitted.get(name, [])
        if len(targets) != 1:
            faults.append((name, "not one module-level string literal"))
            continue
        others = [node for bound, node in
                  (pair for walked in ast.walk(tree) for pair in _bound_names(walked))
                  if bound == name and node is not targets[0]]
        if others:
            faults.append((name, "rebound or shadowed"))
    return faults


def _function_name_faults(tree, names):
    """For each of `names`, why it cannot serve as a trusted gate or emitter name over `tree`, as
    (name, why): the name must be bound exactly once in the whole module, by one module-level def
    of that name, and never rebound or shadowed anywhere (_bound_names lists the binding forms),
    because _closed_argument and the walk trust these names by spelling and cannot see scopes.
    A def carrying ANY decorator is refused: the gate decorator allowlist is empty on purpose,
    since a decorator binds the trusted name to whatever the decorator returns."""
    faults = []
    for name in names:
        bindings = [node for walked in ast.walk(tree) for bound, node in _bound_names(walked)
                    if bound == name]
        defs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if (len(defs) != 1 or bindings != defs
                or any(node.decorator_list for node in defs)):
            faults.append((name, "not exactly one module-level def, rebound, shadowed or "
                           "decorated (the gate decorator allowlist is empty)"))
    return faults


# The values the canonical floor guard's refusal formats (tools/check_python_floor.py
# GUARD_TEMPLATE): the interpreter's version and sys.executable, nothing else.
_FLOOR_GUARD_VALUES = 'tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)'


def _floor_guard(tree):
    """The module-level `if` statement whose refusal write closure/closed-sites exempts as the
    canonical Python floor guard, or None. It directly follows a plain `import sys` at the end of
    the preamble (a docstring and `from __future__` imports, as tools/check_python_floor.py reads
    it), has no else, tests `tuple(sys.version_info[:2]) < (M, N)` with two int constants, and holds
    exactly two statements: a sys.stderr.write passing one positional argument, a str constant %
    exactly _FLOOR_GUARD_VALUES, then `raise SystemExit(<int>)`. A guard in any other position or
    shape (a third statement, another value formatted, a second copy later in the module, one
    inside a def) is not this one, so its write is not exempted."""
    body, start = tree.body, 0
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        start = 1
    while (start < len(body) and isinstance(body[start], ast.ImportFrom)
           and body[start].module == "__future__" and body[start].level == 0):
        start += 1
    if len(body) < start + 2:
        return None
    head, guard = body[start], body[start + 1]
    if not (isinstance(head, ast.Import) and len(head.names) == 1 and head.names[0].name == "sys"
            and head.names[0].asname is None):
        return None
    if not (isinstance(guard, ast.If) and not guard.orelse and len(guard.body) == 2):
        return None
    test, write, leave = guard.test, guard.body[0], guard.body[1]
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Lt)
            and ast.dump(test.left) == ast.dump(ast.parse("tuple(sys.version_info[:2])", mode="eval").body)
            and isinstance(test.comparators[0], ast.Tuple) and len(test.comparators[0].elts) == 2
            and all(isinstance(elt, ast.Constant) and type(elt.value) is int
                    for elt in test.comparators[0].elts)):
        return None
    if not (isinstance(leave, ast.Raise) and leave.cause is None and isinstance(leave.exc, ast.Call)
            and _callee_name(leave.exc.func) == "SystemExit" and not leave.exc.keywords
            and len(leave.exc.args) == 1 and isinstance(leave.exc.args[0], ast.Constant)
            and type(leave.exc.args[0].value) is int):
        return None
    call = write.value if isinstance(write, ast.Expr) else None
    if not (isinstance(call, ast.Call) and _callee_name(call.func) == "sys.stderr.write"
            and not call.keywords and len(call.args) == 1):
        return None
    text = call.args[0]
    if not (isinstance(text, ast.BinOp) and isinstance(text.op, ast.Mod)
            and isinstance(text.left, ast.Constant) and isinstance(text.left.value, str)
            and ast.dump(text.right) == ast.dump(ast.parse(_FLOOR_GUARD_VALUES, mode="eval").body)):
        return None
    return guard


def _unexempted_violations(tree):
    """_closed_output_violations(tree) less the canonical floor guard's two channel references
    (_floor_guard: its refusal write's sys.stderr and its raise's SystemExit, each matched by
    qualname, token and line), with the number of violations that exemption removed (the guard
    holds exactly those two references, so a count other than 2 means the exemption is stale or
    too wide)."""
    guard = _floor_guard(tree)
    found = _closed_output_violations(tree)
    exempt = () if guard is None else (("<module>", "sys.stderr", guard.body[0].lineno),
                                       ("<module>", "SystemExit", guard.body[1].lineno))
    kept = [site for site in found if site[:3] not in exempt]
    return kept, len(found) - len(kept)


def _closed_output_violations(tree):
    """Every reference under `tree` that the closed-diagnostics ALLOWLIST refuses, as (qualname,
    token, line, why). Only the one module-level def named _emit (whose statements
    _check_closed_sites pins verbatim against _EMITTER_SOURCE) may reference a channel; the
    canonical floor guard's two references are exempted by position and shape in
    _unexempted_violations; everywhere else:
    any reference to a _CHANNEL_NAMES name (print, exit, quit, SystemExit, warnings, logging,
    traceback, builtins, __builtins__), in any context, is refused, closed text or not; a bare
    reference to ANY _MODULE_ATTRS module name outside an attribute access (an assigned alias, a
    call or accessor argument, a store) is refused; a _CHANNEL_ATTRS attribute of sys or os (the
    streams, the dunder streams, sys.exit, os.write, os._exit) is refused wherever it appears;
    an attribute chain rooted in an imported module name whose dotted path is not enumerated in
    _MODULE_ATTRS (nor a prefix of an entry) is refused, however deep the chain; an attribute
    named in _REFUSED_ATTRS (__dict__, __builtins__, modules, fdopen) is refused on any base;
    any reference to a _DYNAMIC_IMPORT_NAMES or _REFUSED_BUILTIN_NAMES name (importlib,
    __import__, eval, exec, compile, globals, vars, locals, input, help, breakpoint) is refused;
    a getattr, setattr or delattr call with a non-literal name, with no name argument, or with a
    literal name that is itself refused is refused, and so is an accessor loaded outside a
    call's callee position; a call to open, io.FileIO or os.fdopen with an int-constant first
    argument (a numbered descriptor) is refused; only a plain `import M` of a module enumerated
    in _MODULE_ATTRS is permitted (no asname, no dotted form, no star import), and only the
    exact (module, name) pairs in _FROM_IMPORT_ALLOW may be from-imported, each bound under its
    own name; an import binding a _PROTECTED_NAMES name is refused; any binding of a
    _GATE_DEPENDENCY_NAMES name (int, str, type, isinstance, frozenset: the names the emitter's
    clamp and the gates' checks read) is refused in every _bound_names form; any other binding
    of a protected name (a parameter, an except name, a def or class name, a match capture, a
    global or nonlocal declaration) is refused, except the gates' and the emitter's own
    module-level defs; an attribute or subscript store rooted at a gate name or a
    _GATE_TABLE_NAMES name is refused; a load of _emit outside a call's callee position is
    refused; and every _emit call must pass closed text (_closed_argument) as its at most one
    positional or text= argument, a True or False constant as err=, and no other keyword and no
    starred argument; code= may be any expression, because the emitter exits through an int
    clamp, so the interpreter never prints an exit value of this module. Exception constructors
    stay checked wherever they appear, the emitter included (the interpreter prints an uncaught
    exception's text): a call to a name in _EXCEPTION_NAMES, built or raised, whose positional
    or keyword argument is not closed; any other RAISED constructor, checked the same way; and
    an assert whose message is not closed. The deliberate introspection sites of this module's
    own machinery are recorded in _CLOSED_SITE_EXCEPTIONS, each with a reason. Residuals are
    stated in the module docstring: what the interpreter runs outside this file, a value that
    has left a module, the enumerated but output-capable rows (subprocess.run, io.FileIO), a
    raise of a bare name (re-raising formats nothing), and a BUILT exception whose class name is
    not in _EXCEPTION_NAMES."""
    emitter_defs = [node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_emit"]
    exempt_nodes = set()
    for emitter in emitter_defs:
        for node in ast.walk(emitter):
            exempt_nodes.add(id(node))
    callee_funcs, attribute_bases = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee_funcs.add(id(node.func))
        if isinstance(node, ast.Attribute):
            attribute_bases.add(id(node.value))
    found = []

    def closed_call(child):
        return (all(_closed_argument(arg) for arg in child.args)
                and all(kw.arg is not None and _closed_argument(kw.value) for kw in child.keywords))

    def emit_fault(child):
        if len(child.args) > 1 or any(isinstance(arg, ast.Starred) for arg in child.args):
            return "more than one positional emitter argument, or a starred one"
        wrong = [arg for arg in child.args if not _closed_argument(arg)]
        for kw in child.keywords:
            if kw.arg == "code":
                continue
            if kw.arg == "err":
                if not (isinstance(kw.value, ast.Constant) and type(kw.value.value) is bool):
                    wrong.append(kw.value)
            elif kw.arg != "text" or not _closed_argument(kw.value):
                wrong.append(kw.value)
        return "unclosed emitter text, or an emitter keyword outside text, err and code" if wrong else None

    def walk(node, qualname, prefix, exempt):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                if not exempt:
                    for bound, bnode in _bound_names(child):
                        if (bound in _PROTECTED_NAMES
                                and not (isinstance(bnode, ast.FunctionDef) and bound in _GATE_NAMES
                                         and any(bnode is top for top in tree.body))):
                            found.append((qualname, bound, child.lineno,
                                          "a binding of a protected name"))
                own = prefix + ("<lambda>" if isinstance(child, ast.Lambda) else child.name)
                walk(child, own, own + ("." if isinstance(child, ast.ClassDef) else ".<locals>."),
                     exempt or id(child) in exempt_nodes)
                continue
            if not exempt:
                if isinstance(child, ast.Name) and child.id in _CHANNEL_NAMES:
                    found.append((qualname, child.id, child.lineno,
                                  "a channel name outside the emitter"))
                if (isinstance(child, ast.Name) and child.id in _MODULE_ATTRS
                        and not (isinstance(child.ctx, ast.Load) and id(child) in attribute_bases)):
                    found.append((qualname, child.id, child.lineno,
                                  "a bare module name (an alias the walk cannot follow)"))
                if (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
                        and child.attr in _CHANNEL_ATTRS.get(child.value.id, ())):
                    found.append((qualname, child.value.id + "." + child.attr, child.lineno,
                                  "a channel attribute outside the emitter"))
                if isinstance(child, ast.Attribute):
                    parts, base = [child.attr], child.value
                    while isinstance(base, ast.Attribute):
                        parts.append(base.attr)
                        base = base.value
                    if (isinstance(base, ast.Name) and base.id in _MODULE_ATTRS
                            and parts[-1] not in _CHANNEL_ATTRS.get(base.id, ())):
                        suffix = ".".join(parts[::-1])
                        if not any(entry == suffix or entry.startswith(suffix + ".")
                                   for entry in _MODULE_ATTRS[base.id]):
                            found.append((qualname, base.id + "." + suffix, child.lineno,
                                          "a module attribute outside the enumerated allowlist"))
                if isinstance(child, ast.Attribute) and child.attr in _REFUSED_ATTRS:
                    found.append((qualname, child.attr, child.lineno,
                                  "a namespace or descriptor attribute refused by name"))
                if isinstance(child, ast.Name) and child.id in _REFUSED_BUILTIN_NAMES:
                    found.append((qualname, child.id, child.lineno,
                                  "an introspection or input builtin refused by name"))
                if (isinstance(child, ast.Name) and child.id in _ATTR_ACCESSOR_NAMES
                        and not (isinstance(child.ctx, ast.Load) and id(child) in callee_funcs)):
                    found.append((qualname, child.id, child.lineno,
                                  "an attribute accessor outside a call's callee position"))
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id in _ATTR_ACCESSOR_NAMES):
                    literal = None
                    if (len(child.args) > 1 and isinstance(child.args[1], ast.Constant)
                            and isinstance(child.args[1].value, str)):
                        literal = child.args[1].value
                    if (literal is None or literal in _REFUSED_ATTRS or literal in _CHANNEL_NAMES
                            or literal in _PROTECTED_NAMES
                            or any(literal in attrs for attrs in _CHANNEL_ATTRS.values())):
                        found.append((qualname, child.func.id, child.lineno,
                                      "an attribute accessor with a non-literal or refused name"))
                if isinstance(child, ast.Call):
                    opener = _callee_name(child.func)
                    if (opener.split(".")[-1] in _DESCRIPTOR_OPEN_NAMES and child.args
                            and isinstance(child.args[0], ast.Constant)
                            and type(child.args[0].value) is int):
                        found.append((qualname, opener, child.lineno,
                                      "an open of a numbered descriptor"))
                if (isinstance(child, (ast.Attribute, ast.Subscript))
                        and isinstance(child.ctx, (ast.Store, ast.Del))):
                    root = child
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in _GATE_NAMES + _GATE_TABLE_NAMES:
                        found.append((qualname, root.id, child.lineno,
                                      "an attribute or subscript store on a gate or a gate table"))
                if isinstance(child, ast.Name) and child.id in _DYNAMIC_IMPORT_NAMES:
                    found.append((qualname, child.id, child.lineno, "a dynamic import"))
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        if alias.name.split(".")[0] in _DYNAMIC_IMPORT_NAMES:
                            found.append((qualname, alias.name, child.lineno, "a dynamic import"))
                        elif alias.name not in _MODULE_ATTRS or alias.asname is not None:
                            found.append((qualname, alias.name, child.lineno,
                                          "an import outside the enumerated module allowlist, "
                                          "with an asname, or dotted"))
                        if alias.asname is not None and alias.asname in _PROTECTED_NAMES:
                            found.append((qualname, alias.asname, child.lineno,
                                          "an import binding a protected name"))
                if isinstance(child, ast.ImportFrom):
                    module_head = (child.module or "").split(".")[0]
                    if any(alias.name == "*" for alias in child.names):
                        found.append((qualname, (child.module or ".") + ".*", child.lineno,
                                      "a star import (bindings the walk cannot follow)"))
                    else:
                        for alias in child.names:
                            bound = alias.asname if alias.asname is not None else alias.name
                            if ((child.module, alias.name) not in _FROM_IMPORT_ALLOW
                                    or alias.asname is not None or child.level != 0):
                                found.append((qualname, module_head + "." + alias.name,
                                              child.lineno,
                                              "a from-import outside the enumerated allowlist"))
                            if bound in _PROTECTED_NAMES:
                                found.append((qualname, bound, child.lineno,
                                              "an import binding a protected name"))
                for bound, bnode in _bound_names(child):
                    if bound in _GATE_DEPENDENCY_NAMES:
                        found.append((qualname, bound, child.lineno,
                                      "a binding of a builtin the emitter clamp or a gate "
                                      "depends on"))
                    if bound not in _PROTECTED_NAMES or isinstance(bnode, ast.alias):
                        continue
                    if isinstance(bnode, ast.Name) and bound not in _GATE_NAMES:
                        continue
                    if (isinstance(bnode, ast.FunctionDef) and bound in _GATE_NAMES
                            and any(bnode is top for top in tree.body)):
                        continue
                    found.append((qualname, bound, child.lineno, "a binding of a protected name"))
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id == "_emit"):
                    why = emit_fault(child)
                    if why:
                        found.append((qualname, "_emit", child.lineno, why))
                if (isinstance(child, ast.Name) and child.id == "_emit"
                        and isinstance(child.ctx, ast.Load) and id(child) not in callee_funcs):
                    found.append((qualname, "_emit", child.lineno,
                                  "the emitter loaded outside a call (an alias)"))
            if isinstance(child, ast.Call):
                name = _callee_name(child.func)
                if name.split(".")[-1] in _EXCEPTION_NAMES and not closed_call(child):
                    found.append((qualname, name, child.lineno, "unclosed exception text"))
            if (isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call)
                    and _callee_name(child.exc.func).split(".")[-1] not in _EXCEPTION_NAMES
                    and not closed_call(child.exc)):
                found.append((qualname, _callee_name(child.exc.func), child.lineno,
                              "unclosed raised-constructor text"))
            if isinstance(child, ast.Assert) and child.msg is not None and not _closed_argument(child.msg):
                found.append((qualname, "assert", child.lineno, "unclosed assert message"))
            walk(child, qualname, prefix, exempt)

    walk(tree, "<module>", "", False)
    return found


# One snippet per refused form: each must be flagged by _closed_output_violations, so removing
# a recognizer fails closure/closed-sites. Where rules overlap on purpose (an unlisted writer
# module is refused by the import enumeration whether or not it also stays a channel name), the
# deletion of a single protected-name table row is held by the _PROTECTED_PROBES equality check
# in _check_closed_sites, not by a walk flip. The emitter rows cover each argument rule at an
# _emit call; the rows from "os-sys-chain" on are the train 2 round-32 reproductions (attribute
# chains, namespace subscripts, eval/exec strings, input, descriptor opens, accessor forms) and
# one row per round-33 enumeration rule.
_CLOSED_FLIPS = (
    ("print", "print(value)\n"),
    ("print-closed-text", "print('fixed')\n"),
    ("print-star", "print(*values)\n"),
    ("print-keyword", "print(1, end=value)\n"),
    ("print-file", "print(1, file=handle)\n"),
    ("builtins-print", "import builtins\nbuiltins.print(value)\n"),
    ("dunder-builtins-print", "__builtins__.print(value)\n"),
    ("stdout-write", "import sys\nsys.stdout.write(value)\n"),
    ("stderr-write", "import sys\nsys.stderr.write(value)\n"),
    ("dunder-stdout-write", "import sys\nsys.__stdout__.write(value)\n"),
    ("dunder-stderr-write", "import sys\nsys.__stderr__.write(value)\n"),
    ("stdout-assigned", "import sys\nstream = sys.stdout\n"),
    ("stderr-assigned", "import sys\nstream = sys.stderr\n"),
    ("stderr-buffer-write", "import sys\nsys.stderr.buffer.write(data)\n"),
    ("stdout-writelines", "import sys\nsys.stdout.writelines(lines)\n"),
    ("os-write", "import os\nos.write(1, value)\n"),
    ("os-exit", "import os\nos._exit(2)\n"),
    ("aliased-os-write", "import os\nwriter = os.write\n"),
    ("sys-module-alias", "import sys\ns = sys\n"),
    ("os-module-alias", "import os\no = os\n"),
    ("sys-as-argument", "import sys\nprobe(sys)\n"),
    ("getattr-stream", "import sys\nwriter = getattr(sys, name)\n"),
    ("sys-rebound", "sys = stub\n"),
    ("sys-exit-call", "import sys\nsys.exit(value)\n"),
    ("entry-sys-exit", "import sys\nsys.exit(main())\n"),
    ("aliased-sys-exit", "import sys\nleave = sys.exit\n"),
    ("exit-value", "exit(value)\n"),
    ("quit-value", "quit(value)\n"),
    ("aliased-exit-name", "leave = exit\n"),
    ("aliased-quit-name", "leave = quit\n"),
    ("exit-string-concat", "import sys\nsys.exit('error: cannot read ' + str(path))\n"),
    ("systemexit-built", "exc = SystemExit(value)\n"),
    ("systemexit-raised", "raise SystemExit(2)\n"),
    ("systemexit-caught", "try:\n    probe()\nexcept SystemExit:\n    pass\n"),
    ("warnings-call", "import warnings\nwarnings.warn(1)\n"),
    ("logging-call", "import logging\nlogging.error(1)\n"),
    ("traceback-call", "import traceback\ntraceback.print_exc()\n"),
    ("import-warnings", "import warnings\n"),
    ("import-logging", "import logging\n"),
    ("import-traceback", "import traceback\n"),
    ("import-builtins", "import builtins\n"),
    ("import-sys-alias", "import sys as s\n"),
    ("import-os-alias", "import os as o\n"),
    ("import-os-dotted", "import os.path\n"),
    ("from-sys-any", "from sys import argv\n"),
    ("from-sys-stderr-renamed", "from sys import stderr as err\n"),
    ("from-os-any", "from os import environ\n"),
    ("from-builtins-print", "from builtins import print\n"),
    ("from-warnings-warn", "from warnings import warn\n"),
    ("from-sys-star", "from sys import *\n"),
    ("star-import-any", "from json import *\n"),
    ("import-as-channel", "import json as print\n"),
    ("from-import-channel", "from json import dumps as quit\n"),
    ("importlib-import", "import importlib\n"),
    ("importlib-reference", "module = importlib.import_module(name)\n"),
    ("dunder-import", "module = __import__('sys')\n"),
    ("channel-parameter", "def probe(print):\n    pass\n"),
    ("channel-def-name", "def exit(value):\n    pass\n"),
    ("channel-except-name", "try:\n    probe()\nexcept OSError as print:\n    pass\n"),
    ("channel-global", "def probe():\n    global print\n    print = stub\n"),
    ("channel-match-capture", "def probe(value):\n    match value:\n"
     "        case [*print]:\n            pass\n"),
    ("emit-unclosed-text", "_emit(value)\n"),
    ("emit-star", "_emit(*values)\n"),
    ("emit-double-star", "_emit(**values)\n"),
    ("emit-keyword-unclosed", "_emit(text=value)\n"),
    ("emit-err-not-constant", "_emit('x', err=flag)\n"),
    ("emit-err-not-bool", "_emit('x', err=1)\n"),
    ("emit-unknown-keyword", "_emit('x', flush=True)\n"),
    ("emit-two-positionals", "_emit('x', True)\n"),
    ("emit-aliased", "writer = _emit\n"),
    ("emit-rebound", "_emit = stub\n"),
    ("emit-parameter", "def probe(_emit):\n    pass\n"),
    ("emit-nested-def", "def probe():\n    def _emit(text):\n        pass\n"),
    ("gate-shadow-parameter", "def probe(_n, value):\n    _emit(_n(value))\n"),
    ("gate-rebound-assignment", "_cv = stub\n"),
    ("fstring", "raise AssertionError(f'x and more')\n"),
    ("percent", "raise AssertionError('x: %s' % value)\n"),
    ("str-of-value", "raise AssertionError(str(value))\n"),
    ("format-of-value", "raise AssertionError('x {}'.format(value))\n"),
    ("raised-positional", "raise AssertionError(value)\n"),
    ("raised-keyword", "raise Wrapped(message=value)\n"),
    ("raised-star", "raise AssertionError(*values)\n"),
    ("raised-double-star", "raise Wrapped(**values)\n"),
    ("built-constructor", "exc = AssertionError(value)\n"),
    ("built-keyword", "exc = OSError(filename=value)\n"),
    ("gate-keyword", "_emit(_cv('member', token=value))\n"),
    ("assert-message", "assert flag, value\n"),
    ("assert-formatted-message", "assert flag, 'missing: 0'.replace('0', str(path))\n"),
    ("os-sys-chain", "import os\nos.sys.stderr.write(value)\n"),
    ("os-sys-exit", "import os\nos.sys.exit(value)\n"),
    ("sys-modules-subscript", "import sys\nsys.modules['sys'].stderr.write(value)\n"),
    ("dunder-dict-subscript", "import sys\nsys.__dict__['stderr'].write(value)\n"),
    ("dunder-builtins-attribute", "ns = module.__builtins__\n"),
    ("sys-displayhook", "import sys\nsys.displayhook(value)\n"),
    ("sys-excepthook", "import sys\nhook = sys.excepthook\n"),
    ("eval-string", "eval('print(value)')\n"),
    ("exec-string", "exec('print(value)')\n"),
    ("compile-call", "code = compile(text, 'probe', 'single')\n"),
    ("globals-subscript", "globals()['table'] = value\n"),
    ("vars-call", "table = vars(module)\n"),
    ("locals-call", "table = locals()\n"),
    ("input-echo", "input(value)\n"),
    ("help-call", "help(value)\n"),
    ("breakpoint-call", "breakpoint()\n"),
    ("getattr-dynamic-name", "reader = getattr(target, name)\n"),
    ("getattr-refused-literal", "table = getattr(module, '__dict__', None)\n"),
    ("getattr-aliased", "reader = getattr\n"),
    ("setattr-dynamic-name", "setattr(target, name, value)\n"),
    ("delattr-dynamic-name", "delattr(target, name)\n"),
    ("os-fdopen-descriptor", "import os\nos.fdopen(2, 'w', closefd=False).write(value)\n"),
    ("open-descriptor", "open(2, 'w', closefd=False).write(value)\n"),
    ("fileio-descriptor", "import io\nio.FileIO(2, 'w').write(value)\n"),
    ("module-attr-unlisted", "import json\njson.dump(value, handle)\n"),
    ("subprocess-popen", "import subprocess\nsubprocess.Popen(command)\n"),
    ("bare-module-alias", "parser = ast\n"),
    ("module-rebound", "json = stub\n"),
    ("import-unlisted-module", "import pprint\n"),
    ("from-import-unlisted", "from pprint import pformat\n"),
    ("from-pathlib-unlisted", "from pathlib import PurePath\n"),
    ("from-import-renamed", "from pathlib import Path as P\n"),
    ("from-logging-renamed", "from logging import error as report\n"),
    ("from-traceback-renamed", "from traceback import print_exc as report\n"),
    ("from-builtins-renamed", "from builtins import print as report\n"),
    ("warnings-reference", "handler = warnings\n"),
    ("logging-reference", "handler = logging\n"),
    ("traceback-reference", "handler = traceback\n"),
    ("builtins-reference", "handler = builtins\n"),
    ("built-ioerror", "exc = IOError(value)\n"),
    ("built-environmenterror", "exc = EnvironmentError(value)\n"),
    ("dependency-int-store", "int = str\n"),
    ("dependency-global-int", "def probe(value):\n    global int\n    int = str\n"),
    ("dependency-str-parameter", "def probe(str):\n    pass\n"),
    ("dependency-type-rebound", "type = probe\n"),
    ("dependency-isinstance-rebound", "isinstance = probe\n"),
    ("dependency-frozenset-import", "import json as frozenset\n"),
    ("vocabulary-subscript-store", "_CLOSED_VOCABULARIES['reason'] = values\n"),
    ("vocabulary-attribute-store", "_PREFLIGHT_CASES.rows = values\n"),
    ("gate-attribute-store", "_cv.__code__ = probe.__code__\n"),
    ("gate-subscript-store", "_n[0] = probe\n"),
)

# One snippet per refused rebinding or shadowing form of a registered text name: each must make
# _registered_name_faults fault _PIN_MARKER, so removing a binding detector fails
# closure/closed-sites. The control (one module-level literal binding, loads only) must pass.
_BINDING_FLIPS = (
    ("binding-missing", "print(_PIN_MARKER)\n"),
    ("binding-not-literal", "import os\n_PIN_MARKER = str(os.environ)\n"),
    ("binding-not-one-target", "_PIN_MARKER = _OTHER = 'fixed'\n"),
    ("binding-second-assignment", "_PIN_MARKER = 'fixed'\n_PIN_MARKER = other\n"),
    ("binding-augmented", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    _PIN_MARKER += value\n"),
    ("binding-annotated", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    _PIN_MARKER: str = value\n"),
    ("binding-parameter", "_PIN_MARKER = 'fixed'\ndef probe(_PIN_MARKER):\n    print(_PIN_MARKER)\n"),
    ("binding-lambda-parameter", "_PIN_MARKER = 'fixed'\nprobe = lambda _PIN_MARKER: _PIN_MARKER\n"),
    ("binding-for-target", "_PIN_MARKER = 'fixed'\ndef probe(rows):\n"
     "    for _PIN_MARKER in rows:\n        print(_PIN_MARKER)\n"),
    ("binding-with-target", "_PIN_MARKER = 'fixed'\ndef probe(ctx):\n"
     "    with ctx as _PIN_MARKER:\n        print(_PIN_MARKER)\n"),
    ("binding-except-name", "_PIN_MARKER = 'fixed'\ndef probe(fn):\n    try:\n        fn()\n"
     "    except OSError as _PIN_MARKER:\n        print(_PIN_MARKER)\n"),
    ("binding-walrus", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    print(_PIN_MARKER := value)\n"),
    ("binding-comprehension", "_PIN_MARKER = 'fixed'\ndef probe(rows):\n"
     "    return [_PIN_MARKER for _PIN_MARKER in rows]\n"),
    ("binding-import-alias", "_PIN_MARKER = 'fixed'\nimport json as _PIN_MARKER\n"),
    ("binding-def-name", "_PIN_MARKER = 'fixed'\ndef _PIN_MARKER():\n    pass\n"),
    ("binding-class-name", "_PIN_MARKER = 'fixed'\nclass _PIN_MARKER:\n    pass\n"),
    ("binding-global-declaration", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    global _PIN_MARKER\n"),
    ("binding-del", "_PIN_MARKER = 'fixed'\ndel _PIN_MARKER\n"),
    ("binding-nested-assignment", "_PIN_MARKER = 'fixed'\ndef probe(value):\n"
     "    _PIN_MARKER = value\n    print(_PIN_MARKER)\n"),
    ("binding-match-capture", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    match value:\n"
     "        case _PIN_MARKER:\n            print(_PIN_MARKER)\n"),
    ("binding-nonlocal", "_PIN_MARKER = 'fixed'\ndef probe():\n    nonlocal _PIN_MARKER\n"),
    ("binding-match-star", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    match value:\n"
     "        case [*_PIN_MARKER]:\n            pass\n"),
    ("binding-match-mapping-rest", "_PIN_MARKER = 'fixed'\ndef probe(value):\n    match value:\n"
     "        case {**_PIN_MARKER}:\n            pass\n"),
    ("binding-type-parameter", "_PIN_MARKER = 'fixed'\ndef probe[_PIN_MARKER]():\n    pass\n"),
)

# The registered-name binding control: one module-level literal binding and loads only must pass.
_BINDING_CONTROL = "_PIN_MARKER = 'fixed'\nprint(_PIN_MARKER)\n"

# One snippet per refused rebinding or shadowing form of a gate or emitter name: each must make
# _function_name_faults fault _n, so removing a detector or the def requirement fails
# closure/closed-sites. The control (one module-level def, loads only) must pass.
_GATE_BINDING_FLIPS = (
    ("gate-missing", "def probe():\n    pass\n"),
    ("gate-decorated", "@wrapper\ndef _n(value):\n    return '1'\n"),
    ("gate-decorated-lambda",
     "@(lambda fn: lambda value: value)\ndef _n(value):\n    return '1'\n"),
    ("gate-parameter", "def _n(value):\n    return '1'\ndef probe(_n):\n    pass\n"),
    ("gate-reassigned", "def _n(value):\n    return '1'\n_n = probe\n"),
    ("gate-nested-def", "def _n(value):\n    return '1'\ndef probe():\n    def _n(value):\n        pass\n"),
    ("gate-import-alias", "def _n(value):\n    return '1'\nimport json as _n\n"),
    ("gate-lambda", "_n = lambda value: value\n"),
    ("gate-class", "class _n:\n    pass\n"),
    ("gate-two-defs", "def _n(value):\n    return '1'\ndef _n(value):\n    return '2'\n"),
)

# The gate-binding control: one module-level def of the gate, loads only, must pass.
_GATE_BINDING_CONTROL = "def _n(value):\n    return '1'\ndef probe(value):\n    return _n(value)\n"

# Flips of the emitter shape pin, as (name, old, new): _EMITTER_SOURCE with old replaced by new
# once must NOT satisfy _emitter_matches, so each condition the matcher tests (the statement
# list, the stream pick, the int clamp and its exactness, the signature, a decorator, the name)
# is discriminating.
_EMITTER_FLIPS = (
    ("emitter-extra-statement", "    if code is not None:", "    stream.flush()\n    if code is not None:"),
    ("emitter-swapped-streams", "sys.stderr if err else sys.stdout", "sys.stdout if err else sys.stderr"),
    ("emitter-no-clamp", "sys.exit(code if type(code) is int else 2)", "sys.exit(code)"),
    ("emitter-isinstance-clamp", "type(code) is int", "isinstance(code, int)"),
    ("emitter-str-of-text", "(text + ", "(str(text) + "),
    ("emitter-extra-parameter", "text=None, err=False, code=None", "text=None, err=False, code=None, **extra"),
    ("emitter-write-on-exit", "    if code is not None:\n        sys.exit",
     "    if code is not None:\n        stream.write(str(code))\n        sys.exit"),
    ("emitter-decorated", "def _emit", "@staticmethod\ndef _emit"),
    ("emitter-renamed", "def _emit", "def _emitter"),
    ("emitter-return-annotation", "def _emit(text=None, err=False, code=None):",
     "def _emit(text=None, err=False, code=None) -> None:"),
    ("emitter-type-parameter", "def _emit(", "def _emit[T]("),
)

# Flips of the floor-guard exemption, as (name, old, new, module, exempted): the module text with
# "{guard}" replaced by this file's own guard source (the `if` statement) after replacing old with
# new in it once (no change when old is empty), and "{indented}" by that guard indented one level.
# Each module must keep an unexempted violation AND the exemption must remove exactly the declared
# `exempted` count there (0 for a guard made non-canonical; 2 where the flip module also carries
# the canonical guard, whose refusal write and exit raise are its two exempted channel
# references), so the exemption covers the canonical guard's two references in their canonical
# position and shape and nothing else, one flip per tested _floor_guard shape condition.
_FLOOR_GUARD_FLIPS = (
    ("guard-extra-write", "    raise SystemExit(", "    sys.stderr.write(value)\n    raise SystemExit(",
     "import sys\n{guard}\n", 0),
    ("guard-other-values", "sys.executable or", "value or", "import sys\n{guard}\n", 0),
    ("guard-not-after-import", "", "", "import sys\nimport os\n{guard}\n", 0),
    ("guard-head-other-module", "", "", "import os\n{guard}\n", 0),
    ("guard-import-two-names", "", "", "import sys, os\n{guard}\n", 0),
    ("guard-import-as", "", "", "import sys as sys\n{guard}\n", 0),
    ("guard-second-copy", "", "", "import sys\n{guard}\n{guard}\n", 2),
    ("guard-in-def", "", "", "import sys\n{guard}\ndef f():\n{indented}\n", 2),
    ("guard-then-write", "", "", "import sys\n{guard}\nsys.stderr.write(value)\n", 2),
    ("guard-test-not-compare", "tuple(sys.version_info[:2]) < (3, 14)", "flag",
     "import sys\n{guard}\n", 0),
    ("guard-version-expression", "tuple(sys.version_info[:2])", "sys.version_info[:2]",
     "import sys\n{guard}\n", 0),
    ("guard-not-less-than", "< (3, 14)", "<= (3, 14)", "import sys\n{guard}\n", 0),
    ("guard-chained-compare", "< (3, 14):", "< (3, 14) < (4, 0):", "import sys\n{guard}\n", 0),
    ("guard-floor-not-tuple", "(3, 14):", "floor:", "import sys\n{guard}\n", 0),
    ("guard-floor-three-parts", "(3, 14)", "(3, 14, 0)", "import sys\n{guard}\n", 0),
    ("guard-floor-not-int", "(3, 14)", "(3, '14')", "import sys\n{guard}\n", 0),
    ("guard-format-not-constant",
     "\"error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python "
     "%d.%d.%d (%s). \"\n        \"Nothing was run (cannot evaluate).\\n\"",
     "template", "import sys\n{guard}\n", 0),
    ("guard-write-assigned", "    sys.stderr.write(", "    _refusal = sys.stderr.write(",
     "import sys\n{guard}\n", 0),
    ("guard-write-keyword", "interpreter\",)))", "interpreter\",)), flush=True)",
     "import sys\n{guard}\n", 0),
    ("guard-write-two-args", "interpreter\",)))", "interpreter\",)), 2)",
     "import sys\n{guard}\n", 0),
    ("guard-write-not-mod", "% (tuple(sys.version_info[:3])", "+ (tuple(sys.version_info[:3])",
     "import sys\n{guard}\n", 0),
    ("guard-write-plain-name",
     "\"error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python "
     "%d.%d.%d (%s). \"\n        \"Nothing was run (cannot evaluate).\\n\"\n        "
     "% (tuple(sys.version_info[:3]) + (sys.executable or \"unknown interpreter\",))",
     "template", "import sys\n{guard}\n", 0),
    ("guard-write-stdout", "sys.stderr.write", "sys.stdout.write", "import sys\n{guard}\n", 0),
    ("guard-raise-bare", "raise SystemExit(2)", "raise SystemExit", "import sys\n{guard}\n", 0),
    ("guard-exit-call-not-raise", "raise SystemExit(2)", "sys.exit(2)", "import sys\n{guard}\n", 0),
    ("guard-raise-from", "raise SystemExit(2)", "raise SystemExit(2) from None",
     "import sys\n{guard}\n", 0),
    ("guard-raise-other-name", "SystemExit(2)", "GuardExit(2)", "import sys\n{guard}\n", 0),
    ("guard-raise-keyword", "SystemExit(2)", "SystemExit(2, code=2)", "import sys\n{guard}\n", 0),
    ("guard-raise-two-args", "SystemExit(2)", "SystemExit(2, 2)", "import sys\n{guard}\n", 0),
    ("guard-raise-not-int", "SystemExit(2)", "SystemExit(code)", "import sys\n{guard}\n", 0),
    ("guard-raise-string-int", "SystemExit(2)", "SystemExit('2')", "import sys\n{guard}\n", 0),
    ("guard-raise-bool", "SystemExit(2)", "SystemExit(True)", "import sys\n{guard}\n", 0),
    ("guard-third-statement", "raise SystemExit(2)", "raise SystemExit(2)\n    sys.stderr.flush()",
     "import sys\n{guard}\n", 0),
    ("guard-with-else", "    raise SystemExit(2)", "    raise SystemExit(2)\nelse:\n    pass",
     "import sys\n{guard}\n", 0),
)

# Positive controls of the exemption, as (name, module): each module holds the canonical guard in
# a permitted preamble (plain, after a docstring, after a docstring and a __future__ import) and
# must come back fully exempt (no violation kept, exactly the guard's two references removed), so
# the preamble tolerances of _floor_guard cannot be narrowed without failing here.
_FLOOR_GUARD_CONTROLS = (
    ("guard-plain", "import sys\n{guard}\n"),
    ("guard-after-docstring", "\"\"\"module docstring\"\"\"\nimport sys\n{guard}\n"),
    ("guard-after-future",
     "\"\"\"module docstring\"\"\"\nfrom __future__ import annotations\nimport sys\n{guard}\n"),
)

# A snippet of every closed form, which _closed_output_violations must pass untouched: closed
# emitter calls on both streams, a free sys and os attribute, an exit relay through code=, and a
# closed exception constructor.
_CLOSED_CONTROL = (
    "import sys\n"
    "import os\n"
    "import json\n"
    "_emit('fixed')\n"
    "_emit('a: {}'.format(_cv('member', name)), err=True)\n"
    "_emit('x ' + _n(count) + ('a' if flag else 'b'), err=False)\n"
    "_emit('b: ' + _PIN_MARKER)\n"
    "_emit(code=main())\n"
    "_emit('done', code=0)\n"
    "version = sys.version_info\n"
    "home = os.environ.get('HOME')\n"
    "rows = json.loads(text)\n"
    "label = getattr(probe, '__name__', 'fixed')\n"
    "raise AssertionError('{} and {}'.format(_cvs('member', names), _n(3)))\n")


def _check_closed_sites(_root):
    """Structural pin of the closed-diagnostics rule (ruling D-428) as an ALLOWLIST over this
    file's own AST (see _closed_output_violations for the rules): exactly one module-level _emit
    def, matching _EMITTER_SOURCE verbatim (a docstring aside), and the canonical floor guard are
    the only code that may reference an output or exit channel; every other reference, alias,
    binding, import or star import of one is refused, and every _emit call, exception constructor
    and assert passes closed text (_closed_argument), unless _CLOSED_SITE_EXCEPTIONS records the
    unclosed-text site with a reason. A registered _CLOSED_TEXT_NAMES name must be bound exactly
    once, at module level, to one string literal, never rebound or shadowed
    (_registered_name_faults; one flip per refused form in _BINDING_FLIPS, and the check must
    pass _BINDING_CONTROL); each gate and the emitter must be bound exactly once by its
    module-level def (_function_name_faults, with _GATE_BINDING_FLIPS and _GATE_BINDING_CONTROL), and a gate
    def carrying any decorator is refused (the decorator allowlist is empty);
    the emitter shape pin must refuse every _EMITTER_FLIPS mutation of _EMITTER_SOURCE and accept
    it unchanged, with and without a docstring; the floor-guard exemption must remove exactly the
    guard's two channel references, flag every _FLOOR_GUARD_FLIPS module built from this file's
    own guard (removing exactly the declared count there) and come back fully exempt on every
    _FLOOR_GUARD_CONTROLS module; the walk must flag every _CLOSED_FLIPS snippet and pass
    _CLOSED_CONTROL; a site failing a rule is refuted naming its qualname, token and line; a
    stale registry entry (an exception matching no site, a duplicate, or one with no reason) is
    refuted. At run time it also pins the emitter and the patch guard: _emit must write a probe
    line to exactly the selected stream, must leave through SystemExit with an int code passed
    through and any other code clamped to 2 with nothing written, and _patched must refuse every
    _PROTECTED_PROBES name, a tuple required to equal _PROTECTED_NAMES exactly (so a dropped
    protected-name table row fails unprobed), while passing an unprotected control. Residuals are stated in
    _closed_output_violations and the module docstring. A source that cannot be read is
    cannot-evaluate."""
    try:
        source = Path(_THIS_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _CannotEvaluate("closure/closed-sites: could not read this file's source")
    tree = ast.parse(source)
    guard_node = _floor_guard(tree)
    guard = None if guard_node is None else ast.get_source_segment(source, guard_node)
    if not guard:
        raise AssertionError("closure/closed-sites: this file does not open with the canonical Python "
                             "floor guard in the shape the exemption accepts")
    faults = _registered_name_faults(tree, _CLOSED_TEXT_NAMES)
    if faults or len(set(_CLOSED_TEXT_NAMES)) != len(_CLOSED_TEXT_NAMES):
        raise AssertionError("closure/closed-sites: a registered text name is not one module-level "
                             "string literal bound exactly once and never rebound or shadowed, or "
                             "is registered twice: {}".format(
                                 _cvs("text-name", [name for name, _why in faults])))
    for flip, snippet in _BINDING_FLIPS:
        if not _registered_name_faults(ast.parse(snippet), ("_PIN_MARKER",)):
            raise AssertionError("closure/closed-sites: the registered-name binding check passed the "
                                 "{} flip, so a rebound or shadowed registered name would be "
                                 "trusted".format(_cv("flip", flip)))
    if _registered_name_faults(ast.parse(_BINDING_CONTROL), ("_PIN_MARKER",)):
        raise AssertionError("closure/closed-sites: the registered-name binding check refused the "
                             "control snippet (one module-level literal binding, loads only)")
    gate_faults = _function_name_faults(tree, _GATE_NAMES)
    if gate_faults or len(set(_GATE_NAMES)) != len(_GATE_NAMES):
        raise AssertionError("closure/closed-sites: a gate or the emitter is not bound exactly once "
                             "by its module-level def, is rebound or shadowed, or is listed twice: "
                             "{}".format(_cvs("gate-name", [name for name, _why in gate_faults])))
    for flip, snippet in _GATE_BINDING_FLIPS:
        if not _function_name_faults(ast.parse(snippet), ("_n",)):
            raise AssertionError("closure/closed-sites: the gate-binding check passed the {} flip, "
                                 "so a rebound or shadowed gate would be trusted".format(
                                     _cv("flip", flip)))
    if _function_name_faults(ast.parse(_GATE_BINDING_CONTROL), ("_n",)):
        raise AssertionError("closure/closed-sites: the gate-binding check refused the control "
                             "snippet (one module-level def, loads only)")
    emitters = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_emit"]
    if len(emitters) != 1 or not _emitter_matches(emitters[0]):
        raise AssertionError("closure/closed-sites: this file does not hold exactly one module-level "
                             "_emit def matching _EMITTER_SOURCE (the pinned emitter shape)")
    for flip, old, new in _EMITTER_FLIPS:
        flipped = _EMITTER_SOURCE.replace(old, new, 1)
        if flipped == _EMITTER_SOURCE:
            raise AssertionError("closure/closed-sites: the {} flip changed nothing in the emitter "
                                 "source".format(_cv("flip", flip)))
        if _emitter_matches(ast.parse(flipped).body[0]):
            raise AssertionError("closure/closed-sites: the emitter shape pin passed the {} flip, so "
                                 "the emitter could change shape unseen".format(_cv("flip", flip)))
    for control in (_EMITTER_SOURCE,
                    _EMITTER_SOURCE.replace("):", '):\n    """a docstring"""', 1)):
        if not _emitter_matches(ast.parse(control).body[0]):
            raise AssertionError("closure/closed-sites: the emitter shape pin refused its own control")
    matched = dict()
    for qualname, name, reason in _CLOSED_SITE_EXCEPTIONS:
        if (qualname, name) in matched or not reason.strip():
            raise AssertionError("closure/closed-sites: a duplicate exception entry, or one with no "
                                 "reason: {} {}".format(qualname, name))
        matched[qualname, name] = 0
    unclosed = []
    violations, exempted = _unexempted_violations(tree)
    if exempted != 2:
        raise AssertionError("closure/closed-sites: the floor-guard exemption removed {} violation(s), "
                             "not exactly the guard's refusal write and its exit raise".format(
                                 _n(exempted)))
    for qualname, name, lineno, why in violations:
        if (qualname, name) in matched:
            matched[qualname, name] += 1
        else:
            unclosed.append("{} {} (line {}): {}".format(qualname, name, lineno, why))
    if unclosed:
        raise AssertionError("closure/closed-sites: a channel reference outside the emitter, or an "
                             "output or constructor site carrying unclosed text: {}".format(
                                 "; ".join(unclosed)))
    stale = ["{} {}".format(*key) for key, count in matched.items() if count == 0]
    if stale:
        raise AssertionError("closure/closed-sites: a recorded exception matches no site: {}".format(
            "; ".join(stale)))
    for flip, snippet in _CLOSED_FLIPS:
        if not _closed_output_violations(ast.parse(snippet)):
            raise AssertionError("closure/closed-sites: the walk passed the {} flip, so that refused "
                                 "form is no longer recognized".format(_cv("flip", flip)))
    if _closed_output_violations(ast.parse(_CLOSED_CONTROL)):
        raise AssertionError("closure/closed-sites: the walk refused the closed control snippet")
    indented = '\n'.join("    " + line for line in guard.splitlines())
    for flip, old, new, module, exempted in _FLOOR_GUARD_FLIPS:
        flipped = guard.replace(old, new, 1)
        if old and flipped == guard:
            raise AssertionError("closure/closed-sites: the {} flip changed nothing in the guard".format(
                _cv("flip", flip)))
        text = module.replace('{guard}', flipped).replace('{indented}', indented)
        kept, removed = _unexempted_violations(ast.parse(text))
        if not kept or removed != exempted:
            raise AssertionError("closure/closed-sites: the floor-guard exemption passed the {} flip, "
                                 "or removed {} violation(s) instead of the flip's declared {}, "
                                 "so it covers more than the canonical guard's references".format(
                                     _cv("flip", flip), _n(removed), _n(exempted)))
    for control, module in _FLOOR_GUARD_CONTROLS:
        if _unexempted_violations(ast.parse(module.replace('{guard}', guard))) != ([], 2):
            raise AssertionError("closure/closed-sites: the floor-guard exemption did not cover exactly "
                                 "the canonical guard's two references in the {} control".format(
                                     _cv("flip", control)))
    _result, probe_out, probe_err = _captured(lambda: _emit("closure/closed-sites emitter probe"))
    if (probe_out, probe_err) != ("closure/closed-sites emitter probe\n", ""):
        raise AssertionError("closure/closed-sites: the emitter did not write the probe line to "
                             "stdout alone")
    _result, probe_out, probe_err = _captured(lambda: _emit("closure/closed-sites emitter probe",
                                                            err=True))
    if (probe_out, probe_err) != ("", "closure/closed-sites emitter probe\n"):
        raise AssertionError("closure/closed-sites: the emitter did not write the probe line to "
                             "stderr alone")
    for leave_code, want_code in ((7, 7), (True, 2), ("closure/closed-sites clamp probe (not an int)", 2)):
        seen = []
        try:
            _captured(lambda code=leave_code: _emit(code=code))
        except Exception:
            raise
        except BaseException as leave:
            seen.append((type(leave).__name__, leave.code))
        if seen != [("SystemExit", want_code)] or _CAPTURE_LOG[-1] != ("", ""):
            raise AssertionError("closure/closed-sites: the emitter did not leave through SystemExit "
                                 "with the int-clamped code and nothing written")
    if (frozenset(_PROTECTED_PROBES) != _PROTECTED_NAMES
            or len(set(_PROTECTED_PROBES)) != len(_PROTECTED_PROBES)):
        raise AssertionError("closure/closed-sites: _PROTECTED_PROBES does not list every "
                             "protected name exactly once, so a probe for a protected-name "
                             "table row could go stale unprobed")
    for protected in _PROTECTED_PROBES:
        try:
            with _patched(**dict(((protected, None),))):
                raise AssertionError("closure/closed-sites: _patched rebound the {} protected "
                                     "name".format(_cv("protected-name", protected)))
        except AssertionError as refusal:
            if not str(refusal).startswith("closure/patched-protected: "):
                raise
    with _patched(_SUBSET_TIMEOUT_S=_SUBSET_TIMEOUT_S):
        pass


def _check_entry_points():
    """How the entry points combine the preflight, the legs and run(), with _preflight_exit,
    _closure_legs, run, self_test_main and repo_root stubbed to record their calls (the real
    _self_test_exit judges the legs). self_test_main returns the preflight's 1 or 2 as is and never
    runs the legs; when the preflight returns None it runs the legs over the same root and exits by
    them, failure-first (0 when both held, 1 on a refutation whatever else could not be evaluated, 2
    on cannot-evaluate alone). main runs self_test_main under --self-test and otherwise run() over
    the repository root, returning its exit; it reads the arguments through _cli_args, stubbed
    here, so no case rebinds the sys module (closure/closed-sites refuses a bare load of sys
    outside an attribute access). Pure: no scratch directory and no opf/ subtree."""
    root = Path("entry-point-root (stubbed)")
    for pre, legs, want in ((1, None, 1), (2, None, 2), (None, ([], []), 0), (None, (["refuted"], []), 1),
                            (None, ([], ["cannot"]), 2), (None, (["refuted"], ["cannot"]), 1)):
        seen = []

        def entry(pre=pre, legs=legs, want=want, seen=seen):
            rc = _captured(self_test_main)[0]
            expected = [("preflight", root)] + ([("legs", root)] if pre is None else [])
            if rc != want or seen != expected:
                raise AssertionError("closure/entry-self-test: a wiring row gave the wrong exit or "
                                     "call order")
        _attributed("closure/entry-self-test", entry, injected=dict(
            repo_root=lambda: root,
            _preflight_exit=lambda where, pre=pre, seen=seen: seen.append(("preflight", where)) or pre,
            _closure_legs=lambda where, legs=legs, seen=seen: seen.append(("legs", where)) or legs))
    for argv, want, expected in ((["check_opf_standalone_closure.py", "--self-test"], 7, [("self-test",)]),
                                 (["check_opf_standalone_closure.py"], 5, [("run", root)])):
        seen = []

        def entry(argv=argv, want=want, expected=expected, seen=seen):
            rc = _captured(main)[0]
            if rc != want or seen != expected:
                raise AssertionError("closure/entry-main: an argv row gave the wrong exit or call order")
        _attributed("closure/entry-main", entry, injected=dict(
            _cli_args=lambda argv=argv: argv[1:], repo_root=lambda: root,
            self_test_main=lambda seen=seen: seen.append(("self-test",)) or 7,
            run=lambda where, seen=seen: seen.append(("run", where)) or 5))


def _check_undecodable_fixture(_root):
    """A copied _opf_store.py that is not UTF-8 text is cannot-evaluate at the read boundary: the
    negative leg gives one cannot-evaluate message (never a traceback, never a refuted leg, self-test
    exit 2), and the leg-wiring case is cannot-evaluate naming that cause. Builds its own fixture tree,
    so it needs no opf/ subtree under the root. Attribution (see _preflight_exit): an OSError building
    the fixture, or a host failure inside either sub-check that the watch sees, is cannot-evaluate;
    once the fixture is built, a crash or a wrong result of _closure_legs or _check_leg_wiring is a
    refutation (_check_undecodable_attribution pins both directions)."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-undecodable-"))
            fixture = tmp / "root"
            _write_fixture(fixture, (("opf/tools/opf.py", b""), ("opf/tools/_opf_store.py", b"\xff")))
        except OSError as exc:
            raise _CannotEvaluate("closure/undecodable-scratch: could not build the fixture"
                                  + (": " + _INJECT_MARKER if _INJECT_MARKER in str(exc) else ""))
        calls = []
        _attributed("closure/undecodable-fixture", lambda: _expect_negative_cannot(
            "closure/undecodable-fixture", _closure_legs(fixture), calls, _NEG_DECODE_ERROR), injected=dict(
                run=lambda _root: 0, _run_one=_stub_member_runner(calls, {})))
        _expect_cannot(_check_leg_wiring, fixture, _NEG_DECODE_ERROR, "closure/undecodable-wiring")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _failing_harness(scratch_at=None, copy_at=None, write_at=None):
    """Stand-ins for tempfile, _materialize and _write_fixture that delegate to the ones bound now,
    except that the scratch_at-th allocation, the copy_at-th copy or the write_at-th fixture write
    (1-based, counted from now) raises an injected OSError: a selective harness failure, after the
    steps before it succeeded."""
    inner_tempfile, inner_materialize, inner_write = tempfile, _materialize, _write_fixture
    count = {"scratch": 0, "copy": 0, "write": 0}

    def mkdtemp(*args, **kwargs):
        count["scratch"] += 1
        if count["scratch"] == scratch_at:
            raise OSError(_INJECT_MARKER)
        return inner_tempfile.mkdtemp(*args, **kwargs)

    def materialize(opf_src, dest):
        count["copy"] += 1
        if count["copy"] == copy_at:
            raise OSError(_INJECT_MARKER)
        return inner_materialize(opf_src, dest)

    def write_fixture(base, files):
        count["write"] += 1
        if count["write"] == write_at:
            raise OSError(_INJECT_MARKER)
        return inner_write(base, files)
    return dict(tempfile=types.SimpleNamespace(mkdtemp=mkdtemp), _materialize=materialize,
                _write_fixture=write_fixture)


def _check_undecodable_attribution(_root):
    """Pins _check_undecodable_fixture's attribution in both directions. A selective harness failure
    injected over this case's watch (the fixture's own allocation or write, then the broken copy's
    allocation or copy in the negative-leg sub-check, then the same in the leg-wiring sub-check) is
    cannot-evaluate naming the injected failure, never a refutation. With the harness intact, a crash
    of _closure_legs or of the leg-wiring check, or a wrong result (a copy-error message where nothing
    failed to copy), is a refutation naming it, never cannot-evaluate; the same wrong result through
    the leg-wiring check over a UTF-8 store is refuted too, because that check reads a harness failure
    from the watched primitives themselves, not from the leg's message. A host failure this case's
    own watch sees (a scratch directory, copy or fixture write of the host's that failed) is
    _HarnessFailure: this case is cannot-evaluate, never refuted."""
    def crash(_root):
        raise UnboundLocalError("stubbed crash")

    def wrong_result(_root):
        return [], [_NEG_COPY_ERROR + "stubbed, although nothing failed to copy"]

    def wiring_crash(_root):
        raise RuntimeError("stubbed wiring crash")
    # Each run's patches are built only once the watch is bound, so an injected failure sits over
    # the watch and a real one under it is still observed.
    runs = [
        ("scratch_at=1", lambda: _failing_harness(scratch_at=1), _CannotEvaluate, "injected"),
        ("write_at=1", lambda: _failing_harness(write_at=1), _CannotEvaluate, "injected"),
        ("scratch_at=2", lambda: _failing_harness(scratch_at=2), _CannotEvaluate, "injected"),
        ("copy_at=1", lambda: _failing_harness(copy_at=1), _CannotEvaluate, "injected"),
        ("scratch_at=3", lambda: _failing_harness(scratch_at=3), _CannotEvaluate, "injected"),
        ("copy_at=2", lambda: _failing_harness(copy_at=2), _CannotEvaluate, "injected"),
        ("crash", lambda: dict(_closure_legs=crash), AssertionError,
         "closure/undecodable-fixture: the code under test raised UnboundLocalError"),
        ("wrong-result", lambda: dict(_closure_legs=wrong_result), AssertionError,
         "closure/undecodable-fixture: "),
        ("wiring-crash", lambda: dict(_check_leg_wiring=wiring_crash), AssertionError,
         "closure/undecodable-wiring: the code under test raised RuntimeError"),
    ]
    for index, (name, patches, want_type, want_text) in enumerate(runs):
        _result, got = _observed("closure/undecodable-attribution", lambda: _check_undecodable_fixture(None),
                                 patches)
        if not isinstance(got, want_type) or want_text not in str(got):
            raise AssertionError("closure/undecodable-attribution: run {} gave the wrong outcome, or "
                                 "one not naming its expected text{}".format(
                                     _n(index),
                                     " (the code under test raised: " + _PIN_MARKER + ")"
                                     if "the code under test raised" in str(got)
                                     and _PIN_MARKER in str(got) else ""))

    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-utf8-"))
            _write_fixture(tmp, (("opf/tools/opf.py", b""), ("opf/tools/_opf_store.py", b"")))
        except OSError as exc:
            raise _CannotEvaluate("closure/undecodable-attribution: could not build the UTF-8 fixture")
        _result, got = _observed("closure/undecodable-attribution", lambda: _check_leg_wiring(tmp),
                                 dict(_closure_legs=wrong_result))
        if type(got) is not AssertionError or not str(got).startswith("closure/negative-bound-lookup"):
            raise AssertionError("closure/undecodable-attribution: wiring wrong-result did not give "
                                 "the closure/negative-bound-lookup refutation{}".format(
                                     " (the code under test raised: " + _PIN_MARKER + ")"
                                     if "the code under test raised" in str(got)
                                     and _PIN_MARKER in str(got) else ""))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _check_preflight_collection():
    """_preflight_exit runs every case whatever an earlier case did: a _CannotEvaluate, or any other
    ordinary exception (here a RuntimeError and a decode failure), is that case's cannot-evaluate, the
    latter naming the case (by its label, or by its function's name when the label is None) and the
    exception; only an AssertionError is a refutation; the exit is failure-first over everything
    collected. A BaseException that is not an Exception stops the collection: it re-raises and
    no later case runs. The probe drives KeyboardInterrupt and GeneratorExit; SystemExit is
    deliberately NOT driven (a channel name the closed-sites allowlist reserves to the emitter),
    so its stop rests on the collection catching Exception alone, which the two driven types pin
    only for themselves. Each _preflight_exit call goes through _attributed. Pure: no scratch directory and no opf/
    subtree."""
    ran = []

    def _cannot_case(_root):
        raise _CannotEvaluate("closure/stubbed-run: stubbed")

    def _boom_case():
        raise RuntimeError("boom (stubbed)")

    def _undecodable_case(_root):
        b"\xff".decode("utf-8")

    def _sentinel_case(_root):
        ran.append("sentinel")

    def _refuting_case(_root):
        ran.append("refute")
        raise AssertionError("closure/stubbed-refutation")
    sentinel = ("closure/stubbed-sentinel", _sentinel_case)
    runs = (
        (((None, _cannot_case), sentinel), 2, ["closure/stubbed-run: stubbed"], ["sentinel"]),
        (((None, _rootless(_boom_case)), sentinel), 2,
         ["closure/preflight-error: _boom_case raised RuntimeError"], ["sentinel"]),
        ((("closure/stubbed-label", _rootless(_boom_case)), sentinel), 2,
         ["closure/preflight-error: closure/stubbed-label raised RuntimeError"], ["sentinel"]),
        (((None, _undecodable_case), (None, _refuting_case), sentinel), 1,
         ["closure/preflight-error: _undecodable_case raised UnicodeDecodeError"], ["refute", "sentinel"]),
    )
    for cases, want_rc, want_cannot, want_ran in runs:
        del ran[:]
        rc, _out, err = _attributed("closure/preflight-collection", lambda: _captured(_preflight_exit, None),
                                    injected=dict(_PREFLIGHT_CASES=cases))
        lines = err.splitlines()
        cannot_head = "SELF-TEST CANNOT EVALUATE: "
        unevaluated = [line[len(cannot_head):] for line in lines if line.startswith(cannot_head)]
        refuted = [line for line in lines if line.startswith("SELF-TEST FAIL: ")]
        if (rc != want_rc or ran != want_ran or len(unevaluated) != len(want_cannot)
                or not all(got.startswith(want) for got, want in zip(unevaluated, want_cannot))
                or refuted != (["SELF-TEST FAIL: closure/stubbed-refutation"] if want_rc == 1 else [])):
            raise AssertionError("closure/preflight-collection: a collection row gave the wrong exit, "
                                 "case order or lines ({} given)".format(_n(rc)))
    for stop in (KeyboardInterrupt, GeneratorExit):
        def _stop_case(_root, stop=stop):
            raise stop("stubbed")
        del ran[:]
        try:
            _attributed("closure/preflight-stop", lambda: _captured(_preflight_exit, None),
                        injected=dict(_PREFLIGHT_CASES=((None, _stop_case), sentinel)))
        except stop:
            pass
        except AssertionError:
            raise
        except BaseException:
            raise AssertionError("closure/preflight-stop: {} became another exception".format(
                _cv("exception", stop.__name__)))
        else:
            raise AssertionError("closure/preflight-stop: {} did not stop the collection".format(
                _cv("exception", stop.__name__)))
        if ran:
            raise AssertionError("closure/preflight-stop: {} did not stop the collection before a later "
                                 "case".format(_cv("exception", stop.__name__)))


def _check_preflight_failure_first(root):
    """_preflight_exit is failure-first, the same rule as run() and _self_test_exit. With no scratch
    directory (so every case that needs one is cannot-evaluate) and the negative verdict mutated to a
    substring match, the other preflight cases, run in REVERSE so the pure verdict case comes after
    the fixture cases, must exit 1 with both the refutation and the cannot-evaluate cases listed;
    without the mutant the same scratch failure exits 2. Over a root with no opf/ subtree (scratch
    available), the same cases exit 2 with nothing refuted: a missing subtree is never a refutation.
    "The other cases" leaves out this case, _check_attribution_inventory, which runs this case
    (running either here would recurse), and _check_host_value_independence, whose child runs this
    case (running it here would recurse through a child of a child). Each _preflight_exit call
    goes through _attributed."""
    no_scratch = types.SimpleNamespace(mkdtemp=_no_scratch)

    def substring_verdict(rc, output, cannot):
        if cannot is not None:
            return "cannot", "stubbed"
        text = output if isinstance(output, str) else "".join(output)
        return ("caught", None) if rc != 0 and _FLIP_EVIDENCE in text else ("fail", "stubbed")
    cases = tuple(reversed([case for case in _PREFLIGHT_CASES
                            if case[1] not in (_check_preflight_failure_first, _check_attribution_inventory,
                                               _check_host_value_independence)]))
    for patches, want in ((dict(_negative_leg_verdict=substring_verdict), 1), (dict(), 2)):
        rc, _out, err = _attributed("closure/preflight-failure-first", lambda: _captured(_preflight_exit, root),
                                    injected=dict(tempfile=no_scratch, _PREFLIGHT_CASES=cases, **patches))
        lines = err.splitlines()
        refuted = [line for line in lines if line.startswith("SELF-TEST FAIL: ")]
        unevaluated = [line for line in lines if line.startswith("SELF-TEST CANNOT EVALUATE: ")]
        wrong_refutation = any(not line.startswith("SELF-TEST FAIL: closure/negative-verdict")
                               for line in refuted)
        if rc != want or not unevaluated or bool(refuted) != (want == 1) or wrong_refutation:
            raise AssertionError("closure/preflight-failure-first: {} gave {}, expected {}".format(
                "substring mutant" if patches else "scratch failure alone", _n(rc), _n(want)))

    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-preflight-absent-"))
        except OSError as exc:
            raise _CannotEvaluate("closure/preflight-absent-scratch: no scratch directory")
        rc, _out, err = _attributed("closure/preflight-absent-subtree",
                                    lambda: _captured(_preflight_exit, tmp / "no-opf-root"),
                                    injected=dict(_PREFLIGHT_CASES=cases))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    lines = err.splitlines()
    if (rc != 2 or any(line.startswith("SELF-TEST FAIL: ") for line in lines)
            or not any(line.startswith("SELF-TEST CANNOT EVALUATE: ") for line in lines)):
        raise AssertionError("closure/preflight-absent-subtree: a root with no opf/ gave {}".format(
            _n(rc)))


# The value the host-independence child's USER, LOGNAME and HOME carry, chosen so that it appears
# in the child's TMPDIR path (closure/host-independence). A fixed literal of this file: it never
# carries anything of the host this process runs on.
_HOSTIND_CANARY = "canaryuser"

# The host-independence child: with "cases" it runs the named preflight cases over this module,
# with _preflight_exit's attribution (an AssertionError is that case's refutation, a
# _CannotEvaluate its cannot-evaluate) and _self_test_exit's verdict; with "probe" (the red leg)
# it calls _require_subtree over a missing root and prints what it raised RAW, so the parent's
# scan can observe what a diagnostic reverted to interpolate a value would disclose.
_HOSTIND_RUNNER = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import check_opf_standalone_closure as closure\n"
    "if sys.argv[3] == 'probe':\n"
    "    try:\n"
    "        closure._require_subtree(sys.argv[4])\n"
    "    except closure._CannotEvaluate as exc:\n"
    "        print(exc)\n"
    "        sys.exit(2)\n"
    "    sys.exit(0)\n"
    "failures, cannot = [], []\n"
    "cases = (('closure/absent-subtree-cannot-evaluate', closure._check_absent_subtree),\n"
    "         ('closure/preflight-failure-first', closure._check_preflight_failure_first),\n"
    "         ('closure/env-canaries', closure._check_env_canaries))\n"
    "for label, case in cases:\n"
    "    try:\n"
    "        case(sys.argv[4])\n"
    "    except closure._CannotEvaluate as exc:\n"
    "        cannot.append('{}: {}'.format(label, exc))\n"
    "    except AssertionError as exc:\n"
    "        failures.append('{}: {}'.format(label, exc))\n"
    "sys.exit(closure._self_test_exit(failures, cannot))\n")

# The one diagnostic the host-independence red leg reverts to interpolate a runtime value: a COPY
# of this module (this file is never changed) has every occurrence of the first text replaced by
# the second. The first text no longer appearing in the source is fixture drift (a refutation):
# the red leg has lost the site it exists to revert.
_HOSTIND_FLIP_OLD = (
    'raise _CannotEvaluate("closure/no-subtree: opf/ subtree not found under the given root")')
_HOSTIND_FLIP_NEW = (
    'raise _CannotEvaluate("closure/no-subtree: opf/ subtree not found under {}".format(root))')


def _check_host_value_independence(root):
    """Closed diagnostics make the self-test host-value independent, shown in both directions
    (ruling D-428 E). Green leg: a child interpreter runs _check_absent_subtree,
    _check_preflight_failure_first and _check_env_canaries over this module, with USER, LOGNAME and
    HOME set to _HOSTIND_CANARY and TMPDIR a fresh directory whose path carries that value
    (<scratch>/canaryuser/tmp), under a minimal controlled environment; the child must exit 0
    (PASS), and no value of that environment of length 4 or more may appear ANYWHERE in either of
    its streams, captured apart (there is no path exemption left to carve out: a closed diagnostic
    names a path's role, never the path). A disclosure is refuted by occurrence count; the output
    is not printed. Red leg: a copy of this module with ONE diagnostic reverted to interpolate a
    runtime value (_HOSTIND_FLIP_OLD -> _HOSTIND_FLIP_NEW) is driven over a missing root under that
    TMPDIR in the same environment; the same scan must now FIND the value and the probe must exit
    2, or the case is refuted: a scan that cannot see an interpolating diagnostic would prove
    nothing. A green-leg exit of 2 is cannot-evaluate (the child's cases observed nothing to
    judge); any other non-zero green exit is a refutation naming the exit only. Needs a scratch
    directory and the opf/ subtree (the child's absent-subtree case needs the real subtree for its
    later sub-checks). _check_preflight_failure_first leaves this case out of the cases it runs:
    the child runs that case, so running this one there would recurse through a child of a child.
    A host failure building the fixture, or one the watch sees at a launch (an OSError, or a child
    that did not finish within its bound), is cannot-evaluate."""
    _require_subtree(root)
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-hostind-"))
            child_tmp = tmp / _HOSTIND_CANARY / "tmp"
            source = Path(_THIS_FILE).read_text(encoding="utf-8")
            flipped = source.replace(_HOSTIND_FLIP_OLD, _HOSTIND_FLIP_NEW)
            _write_fixture(tmp, [("runner.py", _HOSTIND_RUNNER.encode("utf-8")),
                                 ("mutant/check_opf_standalone_closure.py", flipped.encode("utf-8")),
                                 (_HOSTIND_CANARY + "/tmp/.keep", b"")])
        except (OSError, UnicodeError):
            raise _CannotEvaluate("closure/host-independence-scratch: could not build the fixture")
        if flipped == source:
            raise AssertionError("closure/host-independence-drift: the reverted diagnostic's text was "
                                 "not found in this file's source (fixture drift)")
        env = dict(TMPDIR=str(child_tmp), USER=_HOSTIND_CANARY, LOGNAME=_HOSTIND_CANARY,
                   HOME=_HOSTIND_CANARY, PYTHONDONTWRITEBYTECODE="1")
        values = sorted(set(value for value in env.values() if len(value) >= 4))
        tools = str(Path(_THIS_FILE).resolve().parent)

        def launch(primary, mode, where):
            proc = subprocess.run([sys.executable, "-I", "-B", str(tmp / "runner.py"), primary,
                                   tools, mode, where],
                                  cwd=str(tmp), env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
            return proc.returncode, tuple((data or b"").decode("utf-8", "replace")
                                          for data in (proc.stdout, proc.stderr))

        def independent():
            rc, streams = launch(tools, "cases", str(root))
            count = sum(stream.count(value) for stream in streams for value in values)
            if count:
                raise AssertionError("closure/host-independence-disclosure: the child's output carries "
                                     "a value of its environment ({} occurrence(s); the output is not "
                                     "printed)".format(_n(count)))
            if rc == 2:
                raise _CannotEvaluate("closure/host-independence: the child could not evaluate its "
                                      "cases")
            if rc != 0:
                raise AssertionError("closure/host-independence: the cases gave {} under a user name "
                                     "that appears in TMPDIR's path".format(_n(rc)))
            red_rc, red_streams = launch(str(tmp / "mutant"), "probe", str(child_tmp / "no-opf-root"))
            disclosed = sum(stream.count(value) for stream in red_streams for value in values)
            if red_rc != 2 or not disclosed:
                raise AssertionError("closure/host-independence-red: the copy with one diagnostic "
                                     "reverted to interpolate a value gave {} with {} disclosure(s); "
                                     "the scan must catch the revert".format(_n(red_rc), _n(disclosed)))
        _attributed("closure/host-independence", independent)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _red_bound_lookup(root):
    """RED cases: with the bound table emptied, run() passes 600 s to opf-tooling-selftest, and each
    check must refuse it: _check_timeout_mapping at its reconciliation with _DECLARED_ROSTER
    (closure/member-roster), _check_leg_wiring at the negative leg's bound lookup."""
    _red_one(root, _check_timeout_mapping, "closure/member-roster")
    _red_one(root, _check_leg_wiring, "closure/negative-bound-lookup")


def _red_one(root, check, label):
    """check(root) with the bound table emptied, through _attributed under its own label (so a crash is
    not read as the RED), must be refuted with a message starting `label`."""
    try:
        _attributed("closure/red-bound-lookup", lambda: check(root), injected=dict(_MEMBER_TIMEOUT_S=dict()))
    except AssertionError as exc:
        if not str(exc).startswith(label):
            raise
    else:
        raise AssertionError(_cv("label", label) + "-not-red")
    _emit("RED closure-bound-lookup -> " + _cv("label", label))


def _rootless(fn):
    """fn() as a preflight check(root) that ignores root, keeping fn's name for the case's diagnostics."""
    def check(_root):
        return fn()
    check.__name__ = fn.__name__
    check.__wrapped__ = fn
    return check


# The text every stubbed crash or host failure of the attribution pins carries.
_PIN_MARKER = "attribution pin (stubbed)"


class _Overlay:
    """`target` with some attributes replaced; every other attribute is read through to `target`."""

    def __init__(self, target, **attrs):
        self._target = target
        self.__dict__.update(attrs)

    def __getattr__(self, key):
        return getattr(self._target, key)


def _stack_positions():
    """(co_qualname, (line, end line, column, end column)) of every frame of this file on the caller's
    stack, innermost first: the source position, read through co_positions, of the instruction at the
    frame's f_lasti. For a frame that is waiting on a call, that instruction is the call, so two
    calls on one line have different positions."""
    here = sys._getframe()
    frame, found = here.f_back, []
    while frame is not None:
        if frame.f_code.co_filename == here.f_code.co_filename:
            found.append((frame.f_code.co_qualname, tuple(list(frame.f_code.co_positions())[frame.f_lasti // 2])))
        frame = frame.f_back
    return found


def _positions_available():
    """True when this interpreter gives instruction columns through co_positions, read from
    _stack_positions's own code; False under -X no_debug_ranges (or PYTHONNODEBUGRANGES), where
    every column is None, so no call can be told apart from another call on its line."""
    return any(position[2] is not None for position in _stack_positions.__code__.co_positions())


def _nth_call(name, attr, n, mode, fired, stacks=None):
    """A patch for the global `name` (or its attribute `attr`) that delegates to the one bound now,
    except that its n-th outermost call (1-based, counted from now; a call made while an earlier one
    is still running is delegated and not counted) appends n to `fired` and then, by mode:
    "crash" raises RuntimeError, "none" returns None (a malformed result), "oserror" raises OSError
    and "timeout" raises subprocess.TimeoutExpired (the last two stand for a host failure). When
    `stacks` is given, EVERY counted call first appends _stack_positions() to it, so stacks[k - 1]
    is where call k was made."""
    target = globals()[name]
    inner = getattr(target, attr) if attr else target
    count, depth = [0], [0]

    def call(*args, **kwargs):
        if depth[0]:
            return inner(*args, **kwargs)
        count[0] += 1
        if stacks is not None:
            stacks.append(_stack_positions())
        if count[0] != n:
            depth[0] += 1
            try:
                return inner(*args, **kwargs)
            finally:
                depth[0] -= 1
        fired.append(n)
        if mode == "crash":
            raise RuntimeError(_PIN_MARKER)
        if mode == "oserror":
            raise OSError(_PIN_MARKER)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(_PIN_MARKER, 120)
        return None
    call.__name__ = getattr(inner, "__name__", name)
    return {name: _Overlay(target, **{attr: call}) if attr else call}


def _pin(entry, root, absent_root, where=None):
    """Run one _ATTRIBUTION_PINS entry: the case's check, over root (or over absent_root, a path with
    no opf/ subtree, when the entry says so), with its target's n-th call patched by mode, under this
    pin's own watch (a host failure there is _HarnessFailure). Nothing is attributed for the check:
    its own raw outcome is judged. When the patched call fired, a "crash" or "none" entry must give
    an AssertionError naming the entry's expected label (its last field) and "the code under test
    raised" (and, for a crash, the marker), and an "oserror" or "timeout" entry a _CannotEvaluate
    naming "harness failure" and the marker; anything else is a refutation. When it did not fire:
    the case's own AssertionError is raised as is (a refutation); a _CannotEvaluate is
    cannot-evaluate (its setup was missing), except for an absent-root entry, where reaching the
    call without the subtree is the point, so it is a refutation; a pass is a refutation (the call
    was skipped); any other exception is raised as is.
    With `where` (the entry's derived site, as (qualname, dispatch); see _site_walk), a patched call
    that fired holds only when, at that call, a frame of the site's code was executing the site's
    dispatch call: the instruction at its f_lasti has the dispatch's exact source position (line,
    end line, column, end column), so a call on the same line, or one made by another call of the
    same function, does not hold for it. A site whose dispatch is None is refuted (no call can be
    identified with it). Columns that this interpreter does not give (_positions_available false)
    cannot establish where the call was made, so the entry is then cannot-evaluate
    (closure/attribution-positions), never a refutation. Returns True when no earlier counted call
    of the patched target was made at the site (this entry pins the site's first such call), else
    False; without `where`, False."""
    label, check, absent, name, attr, n, mode, want = entry
    site = "{} {}{}#{} {}".format(label, name, "." + attr if attr else "", n, mode)
    if absent and absent_root is None:
        raise _CannotEvaluate("closure/attribution-pin: {}: no scratch directory for a root with no "
                              "opf/".format(_cv("site", site)))
    fired, host, stacks = [], [], []
    with _patched(**_harness_watch(host)):
        with _patched(**_nth_call(name, attr, n, mode, fired, stacks)):
            try:
                _captured(check, absent_root if absent else root)
            except Exception as exc:
                got = exc
            else:
                got = None
    if host:
        raise _HarnessFailure("closure/attribution-pin: {}: harness failure: {} failed{}{}".format(
            _cv("site", site), _cvs("primitive", sorted(set(what for what, _text in host))),
            " " + _PIN_MARKER if any(_PIN_MARKER in text for _what, text in host) else "",
            " " + _INJECT_MARKER if any(_INJECT_MARKER in text for _what, text in host) else ""))
    text = str(got)
    if fired:
        if mode in ("oserror", "timeout"):
            held = (isinstance(got, _CannotEvaluate) and "harness failure" in text
                    and _PIN_MARKER in text)
            if not held:
                raise AssertionError("closure/attribution-pin: {} gave {}, expected cannot-evaluate "
                                     "naming the host failure".format(
                                         _cv("site", site), _cv("exception", type(got).__name__)))
        else:
            held = (type(got) is AssertionError and want in text
                    and "the code under test raised" in text
                    and (mode != "crash" or _PIN_MARKER in text))
            if not held:
                raise AssertionError("closure/attribution-pin: {} gave {}, expected a refutation "
                                     "naming {}".format(_cv("site", site),
                                                        _cv("exception", type(got).__name__),
                                                        _cv("label", want)))
        if where is None:
            return False
        if where[1] is None:
            raise AssertionError("closure/attribution-pin: {} names a site that is neither a call nor a "
                                 "call's argument, so no call can be identified with it".format(
                                     _cv("site", site)))
        if not _positions_available():
            raise _CannotEvaluate("closure/attribution-positions: {}: this interpreter gives no "
                                  "instruction columns (-X no_debug_ranges), so the call's site cannot "
                                  "be checked".format(_cv("site", site)))
        if where not in stacks[n - 1]:
            raise AssertionError("closure/attribution-pin: {} fired outside its site".format(
                _cv("site", site)))
        return not any(where in stack for stack in stacks[:n - 1])
    if isinstance(got, AssertionError):
        raise got
    if isinstance(got, _CannotEvaluate) and not absent:
        raise _CannotEvaluate("closure/attribution-pin: {} was not reached".format(
            _cv("site", site))) from got
    if got is None or isinstance(got, _CannotEvaluate):
        raise AssertionError("closure/attribution-pin: {} was not reached{}".format(
            _cv("site", site), " over a root with no opf/ subtree" if absent else ""))
    raise got


def _check_pin_rules(spans):
    """Pins _pin itself, over a pure target: an attributed crash holds; a crash that escapes raw, or
    that the case turns into cannot-evaluate, is refuted; a case that never reaches the call is
    cannot-evaluate, or refuted when the entry is over a root with no opf/ (or the case passed); a
    watched host failure holds, and the same failure outside any watch is refuted. With the site
    positions `spans` (from _derived_sites): an attributed call pinned at its own site holds as its
    site's first call; a call that fired at another call on the same line as the site is refuted;
    a call pinned at the second pass of a loop holds but is not its site's first call."""
    def attributed(_root):
        _attributed("closure/pin-probe", lambda: _positive_leg_verdict(0))

    def sameline(_root):
        _attributed("closure/pin-probe", lambda: _positive_leg_verdict(0)); _positive_leg_verdict(0)  # noqa: E702

    def later(_root):
        for bare in (True, False):
            probe = lambda: _positive_leg_verdict(0)  # noqa: E731
            if bare:
                probe()
            else:
                _attributed("closure/pin-probe", probe)

    def bare(_root):
        _positive_leg_verdict(0)

    def masked(_root):
        try:
            _positive_leg_verdict(0)
        except RuntimeError:
            raise _CannotEvaluate("closure/pin-probe: masked (stubbed)")

    def unreached(_root):
        raise _CannotEvaluate("closure/pin-probe: no fixture (stubbed)")

    def skipped(_root):
        return None

    def watched(_root):
        _attributed("closure/pin-probe", lambda: _write_fixture(Path("."), ()))

    def unwatched(_root):
        _write_fixture(Path("."), ())
    here = "_check_pin_rules.<locals>."
    probes = (
        (attributed, False, "_positive_leg_verdict", "crash", 1, None, None, False),
        (bare, False, "_positive_leg_verdict", "crash", 1, None, AssertionError, None),
        (masked, False, "_positive_leg_verdict", "crash", 1, None, AssertionError, None),
        (unreached, False, "_positive_leg_verdict", "crash", 1, None, _CannotEvaluate, None),
        (unreached, True, "_positive_leg_verdict", "crash", 1, None, AssertionError, None),
        (skipped, False, "_positive_leg_verdict", "crash", 1, None, AssertionError, None),
        (watched, False, "_write_fixture", "oserror", 1, None, None, False),
        (unwatched, False, "_write_fixture", "oserror", 1, None, AssertionError, None),
        (attributed, False, "_positive_leg_verdict", "crash", 1,
         here + "attributed.<locals>.<lambda> _positive_leg_verdict#1", None, True),
        (sameline, False, "_positive_leg_verdict", "crash", 1, here + "sameline _positive_leg_verdict#1",
         AssertionError, None),
        (later, False, "_positive_leg_verdict", "crash", 2, here + "later.<locals>.<lambda> _positive_leg_verdict#1",
         None, False),
    )
    for index, (check, absent, name, mode, n, key, want, want_first) in enumerate(probes):
        first = None
        try:
            first = _pin(("closure/pin-probe", check, absent, name, None, n, mode, "closure/pin-probe"),
                         None, Path("no-opf-root") if absent else None, None if key is None else spans[key])
        except (AssertionError, _CannotEvaluate) as exc:
            got = exc
        else:
            got = None
        if (type(got) if got is not None else None) is not want or first is not want_first:
            raise AssertionError("closure/pin-rules: probe {} gave the wrong outcome or first-call "
                                 "flag".format(_n(index)))


# What a load must not do unseen (see _derived_sites): these builtins and module-level names look
# code up by a string or through a namespace, and these attributes reach a namespace of globals.
_INDIRECTION_NAMES = frozenset(("globals", "locals", "vars", "getattr", "setattr", "delattr", "eval", "exec",
                                "compile", "__import__", "__name__", "__file__", "__spec__", "__loader__",
                                "__builtins__"))
_INDIRECTION_ATTRS = frozenset(("modules", "f_globals", "f_locals", "f_builtins", "__globals__", "__dict__",
                                "__builtins__"))
_THIS_MODULE, _THIS_FILE = __name__, __file__


def _site_walk(node, scope, targets, held, found, dispatch=None):
    """Collect under `node`, into the dict `found`: in "names", every name it loads; in "raw", every
    load of a name in `targets` as a site (qualname, name, line, column, dispatch); in "flags", every
    indirection as (qualname, token, line, column): a load of a name in _INDIRECTION_NAMES or in
    `held` (token: the name), an attribute in _INDIRECTION_ATTRS (token: "." and the attribute), or
    an import statement (token: "import"). `scope` is (the co_qualname of the code that evaluates
    `node`, the qualname prefix of a function or class defined there). A site's dispatch is the
    (line, end line, column, end column) of the call it belongs to: the call whose callee it is, or
    whose positional or keyword argument it is, directly; it is None for every other load (an
    element of a tuple, an assigned value, an operand, a starred argument), which no call can be
    identified with. A def, lambda or class has its defaults, decorators and bases walked where it
    stands and its body under its own qualname; a generator expression, which runs in a frame of its
    own, likewise (its first iterable where it stands). The annotations of a def's parameters and
    return, and the bounds and defaults of a def's or class's type parameters, are not walked for
    sites: every name such an annotation loads is flagged instead (token: "annotation"), since an
    annotation can be evaluated (for example through __annotations__) and run code at no site."""
    qualname, prefix = scope
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        notes = []
        if not isinstance(node, ast.ClassDef):
            params = (node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                      + [node.args.vararg, node.args.kwarg])
            notes += [param.annotation for param in params if param is not None and param.annotation is not None]
            notes += [node.returns] if node.returns is not None else []
        for param in node.type_params:
            notes += [part for part in (param.bound if isinstance(param, ast.TypeVar) else None,
                                        param.default_value) if part is not None]
        for note in notes:
            for sub in ast.walk(note):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    found["flags"].append((qualname, "annotation", sub.lineno, sub.col_offset))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
        outer = list(getattr(node, "decorator_list", ()))
        if isinstance(node, ast.ClassDef):
            outer += node.bases + [keyword.value for keyword in node.keywords]
        else:
            outer += node.args.defaults + [value for value in node.args.kw_defaults if value is not None]
        for sub in outer:
            _site_walk(sub, scope, targets, held, found)
        own = prefix + ("<lambda>" if isinstance(node, ast.Lambda) else node.name)
        inner = (own, own + ("." if isinstance(node, ast.ClassDef) else ".<locals>."))
        for sub in ([node.body] if isinstance(node, ast.Lambda) else node.body):
            _site_walk(sub, inner, targets, held, found)
        return
    if isinstance(node, ast.GeneratorExp):
        _site_walk(node.generators[0].iter, scope, targets, held, found)
        own = prefix + "<genexpr>"
        inner = (own, own + ".<locals>.")
        for index, generator in enumerate(node.generators):
            for sub in [generator.target] + ([generator.iter] if index else []) + generator.ifs:
                _site_walk(sub, inner, targets, held, found)
        _site_walk(node.elt, inner, targets, held, found)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        found["flags"].append((qualname, "import", node.lineno, node.col_offset))
    if isinstance(node, ast.Attribute) and node.attr in _INDIRECTION_ATTRS:
        found["flags"].append((qualname, "." + node.attr, node.lineno, node.col_offset))
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        found["names"].add(node.id)
        if node.id in _INDIRECTION_NAMES or node.id in held:
            found["flags"].append((qualname, node.id, node.lineno, node.col_offset))
        if node.id in targets:
            found["raw"].append((qualname, node.id, node.lineno, node.col_offset, dispatch))
    if isinstance(node, ast.Call):
        here = (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)
        for sub in [node.func] + node.args + [keyword.value for keyword in node.keywords]:
            _site_walk(sub, scope, targets, held, found, here if isinstance(sub, ast.Name) else None)
        return
    for child in ast.iter_child_nodes(node):
        _site_walk(child, scope, targets, held, found)


def _holds_code(value, depth=0):
    """True when `value` is, or holds within 8 levels (deeper counts as holding), a function, class,
    module or other object of this module: through a tuple, list, set, frozenset or dict, a
    functools.partial, a method, a builtin's __self__, or an object's instance attributes. An object
    whose class is this module's holds code whatever it subclasses (a str subclass included). Only an
    object whose type is EXACTLY None's, str, bytes, int, bool, float, complex or this platform's
    Path class is skipped unread; an instance of a subclass of one of them, or of a container, is read
    for its items and its instance attributes too. A callable of any other kind (not a function,
    class, method, partial or builtin) counts as holding code, since what it calls cannot be read
    here."""
    if depth > 8:
        return True
    if type(value) in (type(None), str, bytes, int, bool, float, complex, type(Path())):
        return False
    if type(value).__module__ == _THIS_MODULE:
        return True
    if isinstance(value, types.ModuleType):
        return value.__name__ == _THIS_MODULE
    if isinstance(value, type):
        return value.__module__ == _THIS_MODULE
    if isinstance(value, types.FunctionType):
        return value.__module__ == _THIS_MODULE or value.__code__.co_filename == _THIS_FILE
    if isinstance(value, types.MethodType):
        return _holds_code(value.__func__, depth + 1) or _holds_code(value.__self__, depth + 1)
    if isinstance(value, types.BuiltinFunctionType):
        return _holds_code(value.__self__, depth + 1)
    if isinstance(value, functools.partial):
        held = [value.func, value.args, value.keywords]
    elif isinstance(value, (tuple, list, set, frozenset)):
        held = list(value)
    elif isinstance(value, dict):
        held = [item for pair in value.items() for item in pair]
    elif callable(value):
        return True
    else:
        held = []
    return any(_holds_code(item, depth + 1) for item in held + list(getattr(value, "__dict__", dict()).values()))


def _check_holder_rules():
    """Pins _holds_code where its shortcut could skip a holder: an instance of a str or int subclass
    whose class is this module's, holding this module's function as an instance attribute, holds
    code; so does an instance of a str subclass, and an empty instance of a list or dict subclass,
    of another module (here a class whose __module__ is set to another name) holding one as an
    instance attribute (a container's instance attributes are read beside its items); the exact
    str, int, list and dict values do not. Raises AssertionError (closure/holds-code) naming the
    first value judged wrongly."""
    class _StrHolder(str):
        pass

    class _IntHolder(int):
        pass

    class _ForeignStrHolder(str):
        pass

    class _ForeignListHolder(list):
        pass

    class _ForeignDictHolder(dict):
        pass
    for foreign in (_ForeignStrHolder, _ForeignListHolder, _ForeignDictHolder):
        foreign.__module__ = "opf_closure_foreign_module (stubbed)"
    holders = [_StrHolder("x"), _IntHolder(1), _ForeignStrHolder("y"), _ForeignListHolder(), _ForeignDictHolder()]
    for holder in holders:
        holder.fn = _check_holder_rules
    for index, (value, want) in enumerate([(holder, True) for holder in holders]
                                          + [("x", False), (1, False), ([], False), ({}, False)]):
        if _holds_code(value) is not want:
            raise AssertionError("closure/holds-code: value {} was judged wrongly".format(_n(index)))


def _derived_sites():
    """The attribution inventory's authoritative sets, derived from this file's own source by an AST
    walk (_site_walk). The code under test is every module-level function that main reaches by name
    (the gate, the closure legs, the preflight runner and the entry points) plus every
    _PREFLIGHT_CASES case's function (a case that calls another case's check tests it). The code a
    case reaches is the case's own function and, transitively, every top-level def or class it
    loads by name that is not code under test, with their nested defs, lambdas and generator
    expressions. A site is EVERY load of a code-under-test name in that code (a call, an argument,
    an identity test, an assignment: every load counts). Its key is "<co_qualname of the code holding
    it> <name>#<k>", k counting that name's loads in that code in source order. An indirection is a
    load or statement in that code through which code can be reached without a site (see
    _site_walk's flags): a builtin or module-level name in _INDIRECTION_NAMES (globals, getattr and
    the like), an attribute in _INDIRECTION_ATTRS (modules, __dict__, f_globals and the like), an
    import statement, a load of a module-level name that is not a top-level def or class and
    whose value, read from this module now, holds this module's code (_holds_code: a table such as
    _PREFLIGHT_CASES, an alias, a functools.partial), a load of a WRAPPER (a top-level def holding
    an _ATTRIBUTION_EXCLUSIONS site, such as _harness_watch, _failing_harness or _copy_recorder:
    a call through it reaches the code under test at that excluded site, which carries no pin, so
    each load of a wrapper needs a recorded reason), or a name an annotation loads (see _site_walk).
    Its key is "<co_qualname> <token>#<k>".
    The closed-text gates _cv, _cvs and _n, and the emitter _emit (_GATE_NAMES), are left out of
    the targets on purpose (see the comment at the targets set): they format diagnostic text on every raise and print and call no code
    under test, and closure/closed-sites pins their coverage structurally. Returns (spans, reach, flagged): spans maps every site key in any top-level definition to
    (qualname, dispatch); reach maps each case's function name to the set of site keys it reaches;
    flagged maps each indirection key any case reaches to the text of the source line holding it
    (stripped of surrounding whitespace), so a record names the load it describes and not only its
    count (_check_attribution_inventory). Not seen by the walk: a load of a
    name it does not list here (for example an imported API such as importlib.import_module given
    this module's name as a literal string, or an object whose class is not this module's reached
    through another module); code reached through an argument supplied from outside the code a case
    reaches; and annotations, which are not walked for sites or followed (code reached only through
    one is not in what a case reaches), though every name one loads is flagged, so a case reaching
    an annotation is cannot-evaluate until it is recorded or removed. A source that cannot be read
    is cannot-evaluate; a case with no module-level definition is a refutation."""
    try:
        source = Path(_THIS_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _CannotEvaluate("closure/attribution-walk: could not read this file's source")
    defs = {node.name: node for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    functions = {name for name, node in defs.items() if isinstance(node, ast.FunctionDef)}
    held = frozenset(name for name, value in globals().items()
                     if name not in defs and not name.startswith("__") and _holds_code(value))
    wrappers = frozenset(site.split(" ")[0].split(".")[0] for site, _reason in _ATTRIBUTION_EXCLUSIONS)
    held |= wrappers & set(defs)

    def walk(name, targets):
        found = dict(names=set(), raw=[], flags=[])
        _site_walk(defs[name], ("", ""), targets, held, found)
        return found
    production, queue = set(), ["main"]
    while queue:
        name = queue.pop()
        if name not in production:
            production.add(name)
            queue.extend(sorted(walk(name, frozenset())["names"] & functions))
    cases = []
    for label, check in _PREFLIGHT_CASES:
        function = getattr(check, "__wrapped__", check)
        if function.__name__ not in functions or function.__code__.co_qualname != function.__name__:
            raise AssertionError("closure/attribution-walk: a case has no module-level definition: "
                                 "{}".format(_cv("case", function.__name__)))
        cases.append(function.__name__)
    # The closed-text gates _cv, _cvs and _n, and the emitter _emit, format or write diagnostic
    # text on every raise and emission (closure/closed-sites pins that every diagnostic goes
    # through them, a constant, or a registered literal, and pins the emitter's statements
    # verbatim) and call no code under test; attributing their calls would make every diagnostic
    # a derived site, so they are left out of the targets on purpose (their bodies are still
    # walked like any helper's, and _check_env_canaries observes their effect).
    targets = frozenset(production | set(cases)) - frozenset(_GATE_NAMES)
    lines = source.splitlines()
    spans, keys, flag_keys, follows = {}, {}, {}, {}
    for name in defs:
        found = walk(name, targets)
        count, keys[name], flag_keys[name] = {}, [], []
        follows[name] = sorted(found["names"] & set(defs) - targets)
        for qualname, target, line, column, dispatch in sorted(found["raw"], key=lambda site: (site[2], site[3])):
            count[qualname, target] = count.get((qualname, target), 0) + 1
            key = "{} {}#{}".format(qualname, target, count[qualname, target])
            spans[key] = (qualname, dispatch)
            keys[name].append(key)
        count = {}
        for qualname, token, line, column in sorted(found["flags"], key=lambda flag: (flag[2], flag[3])):
            count[qualname, token] = count.get((qualname, token), 0) + 1
            flag_keys[name].append(("{} {}#{}".format(qualname, token, count[qualname, token]),
                                    lines[line - 1].strip()))
    reach, flagged = {}, {}
    for case in cases:
        seen, queue = set(), [case]
        while queue:
            name = queue.pop()
            if name not in seen:
                seen.add(name)
                queue.extend(follows[name])
        reach[case] = set(key for name in seen for key in keys[name])
        flagged.update(pair for name in seen for pair in flag_keys[name])
    return spans, reach, flagged


def _check_attribution_inventory(root):
    """The attribution inventory, against the sets _derived_sites derives from this file's AST (the
    sites and indirections it can see; its docstring lists what it cannot). Refuted: a derived site
    with neither an _ATTRIBUTION_PINS entry nor an _ATTRIBUTION_EXCLUSIONS entry (so a new load of
    the code under test in the code a case reaches fails until it is pinned or excluded with a
    reason); a pin naming a site the walk does not find in what its case reaches; an exclusion
    naming a site the walk does not find, one also pinned or excluded twice, or one with no reason;
    an _ATTRIBUTION_INDIRECTIONS record naming an indirection the walk does not find, one whose
    source line is not the text it records, one recorded twice, or one with no reason (so a load
    that replaces a recorded one at the same key, the earlier load removed, is stale unless its
    line reads exactly as the recorded one's). Then every pin entry is fired (see _pin): its
    patched call must fire while a frame of its site's code is executing the site's own call
    instruction (exact source position, columns included), and give the attribution its mode requires;
    and each pinned site needs one
    entry whose call is the first call of its patched target made at that site (closure/attribution-
    first-call), so a site reached bare on a loop's first pass and attributed later is refuted. So
    reverting a pinned site's attribution (calling it bare, or interpreting its result outside
    _attributed) fails here, and so do widening the watch past OSError and TimeoutExpired and
    requiring the opf/ subtree before the sub-checks of _check_scratch_and_copy_errors,
    _check_negative_copy_error and _check_absent_subtree that do not need it (the entries over a
    root with no opf/). Not proved: the attribution of a site's later calls (pins cover chosen
    calls, the first among them), and a site execution that never calls the patched target.
    Cannot-evaluate: an unreadable source, an indirection with no record, an entry whose case could
    not reach its call, and an interpreter that gives no instruction columns (-X no_debug_ranges:
    closure/attribution-positions, before _check_pin_rules and before any entry is fired, since
    no call could then be told from another on its line; the walk's own checks still run). Order:
    _check_holder_rules, the walk and its checks, the column check, _check_pin_rules, the entries.
    Unlike the other cases, this one does not stop at its first cannot-evaluate entry: it runs every
    entry and is failure-first over them, raising the first refutation at once and the
    cannot-evaluate entries together at the end."""
    _check_holder_rules()
    spans, reach, flagged = _derived_sites()
    derived = set().union(*reach.values())
    pinned, excluded = set(), set()
    for site, label, check, *_entry in _ATTRIBUTION_PINS:
        case = getattr(check, "__wrapped__", check).__name__
        if site not in reach.get(case, ()):
            raise AssertionError("closure/attribution-stale-pin: {} pins {}, which the walk does not "
                                 "find in what {} reaches".format(
                                     _cv("pin-label", label), _cv("site-key", site), _cv("case", case)))
        pinned.add(site)
    for site, reason in _ATTRIBUTION_EXCLUSIONS:
        if site not in derived or site in pinned or site in excluded or not reason.strip():
            raise AssertionError("closure/attribution-stale-exclusion: {} (a site the walk does not "
                                 "find, one also pinned or excluded, or no reason)".format(
                                     _cv("site-key", site)))
        excluded.add(site)
    recorded = set()
    for key, line, reason in _ATTRIBUTION_INDIRECTIONS:
        if flagged.get(key) != line or key in recorded or not reason.strip():
            raise AssertionError("closure/attribution-stale-indirection: {} (an indirection the walk "
                                 "does not find, one on another source line, one recorded twice, or no "
                                 "reason)".format(_cv("indirection-key", key)))
        recorded.add(key)
    unpinned = sorted(derived - pinned - excluded)
    if unpinned:
        raise AssertionError("closure/attribution-unpinned: {} derived site(s) carry neither a pin nor "
                             "an exclusion (a derived key is drawn from this file's own source but is "
                             "outside the declared registries, so it is not printed)".format(
                                 _n(len(unpinned))))
    unevaluated, unrecorded = [], sorted(set(flagged) - recorded)
    if unrecorded:
        unevaluated.append("closure/attribution-indirection: the walk cannot follow {} unrecorded "
                           "indirection(s) (code reached through a string, a namespace, a value, a "
                           "wrapper or an annotation is no site; record each in "
                           "_ATTRIBUTION_INDIRECTIONS with a reason, or remove it; an unrecorded key "
                           "is not printed)".format(_n(len(unrecorded))))
    if not _positions_available():
        unevaluated.append("closure/attribution-positions: this interpreter gives no instruction columns "
                           "(-X no_debug_ranges), so no pin's call can be told from another call on its line; "
                           "_check_pin_rules and the entries were not run")
        raise _CannotEvaluate(" | ".join(unevaluated))
    _check_pin_rules(spans)
    first, unknown = set(), set()
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-pins-"))
        except OSError as exc:
            unevaluated.append("closure/attribution-pin: no scratch directory for a root with no opf/")
        for site, *entry in _ATTRIBUTION_PINS:
            try:
                if _pin(tuple(entry), root, None if tmp is None else tmp / "no-opf-root", spans[site]):
                    first.add(site)
            except _CannotEvaluate as exc:
                unevaluated.append(str(exc))
                unknown.add(site)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    late = sorted(pinned - first - unknown)
    if late:
        raise AssertionError("closure/attribution-first-call: no entry pins the first call made at "
                             "{}".format(_cvs("site-key", late)))
    if unevaluated:
        raise _CannotEvaluate(" | ".join(unevaluated))


# The attribution inventory (see _check_attribution_inventory and _pin): entries pinning the sites
# _derived_sites finds, as (the site's key, the case's label, its check, True when the entry runs
# over a root with no opf/ subtree, the patched global, its patched attribute or None, which call to
# patch, mode, the label the refutation must name or None for a host failure).
# _check_attribution_inventory requires every derived site to have an entry here or one in
# _ATTRIBUTION_EXCLUSIONS, and each pinned site an entry for the first call made there; some sites
# carry several entries (one per mode, or per iteration); and an entry may patch, instead of the
# site's own callee, a host primitive or a function that callee calls, provided the patched call
# fires while the site's call instruction is executing (which _pin checks).
_PACK_REG, _ADOPT_REG, _ROWS = (_rootless(_pack_manifest_registration_self_test),
                                _rootless(_adopt_observe_registration_self_test),
                                _rootless(_member_timeout_rows_self_test))
_VERDICTS, _COLLECTION = _rootless(_check_verdict_tables), _rootless(_check_preflight_collection)
_ENTRY, _DECLARED = _rootless(_check_entry_points), _rootless(_check_declared_roster)
_ATTRIBUTION_PINS = (
    ("_pack_manifest_registration_self_test.<locals>.<lambda> _check_pack_manifest_registration#1",
     "pack-manifest-registration", _PACK_REG, False, "_check_pack_manifest_registration", None, 1, "crash",
     "closure/pack-manifest-registration"),
    ("_pack_manifest_registration_self_test.<locals>.<lambda> _check_pack_manifest_registration#2",
     "pack-manifest-registration", _PACK_REG, False, "_check_pack_manifest_registration", None, 2, "crash",
     "closure/pack-manifest-registration"),
    ("_adopt_observe_registration_self_test.<locals>.<lambda> _check_adopt_observe_registration#1",
     "adopt-observe-registration", _ADOPT_REG, False, "_check_adopt_observe_registration", None, 1, "crash",
     "closure/adopt-observe-registration"),
    ("_adopt_observe_registration_self_test.<locals>.<lambda> _check_adopt_observe_registration#2",
     "adopt-observe-registration", _ADOPT_REG, False, "_check_adopt_observe_registration", None, 2, "crash",
     "closure/adopt-observe-registration"),
    ("_member_timeout_rows_self_test.<locals>.<lambda> _check_member_timeout_rows#1",
     "member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 1, "crash",
     "closure/member-timeout-rows"),
    ("_member_timeout_rows_self_test.<locals>.<lambda> _check_member_timeout_rows#2",
     "member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 2, "crash",
     "closure/member-timeout-rows"),
    ("_member_timeout_rows_self_test.<locals>.<lambda> _check_member_timeout_rows#3",
     "member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 3, "crash",
     "closure/member-timeout-rows"),
    ("_check_verdict_tables.<locals>.judge _negative_leg_verdict#1",
     "self-test-leg-verdicts", _VERDICTS, False, "_negative_leg_verdict", None, 1, "none", "closure/negative-verdict"),
    ("_check_verdict_tables.<locals>.judge _positive_leg_verdict#1",
     "self-test-leg-verdicts", _VERDICTS, False, "_positive_leg_verdict", None, 1, "none", "closure/positive-verdict"),
    ("_check_preflight_collection.<locals>.<lambda> _preflight_exit#1",
     "preflight-collection", _COLLECTION, False, "_preflight_exit", None, 1, "crash", "closure/preflight-collection"),
    ("_check_preflight_collection.<locals>.<lambda> _preflight_exit#2",
     "preflight-collection", _COLLECTION, False, "_preflight_exit", None, 5, "crash", "closure/preflight-stop"),
    ("_stubbed_run.<locals>.<lambda> run#1",
     "timeout-cannot-evaluate", _check_timeout_mapping, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("_stubbed_run.<locals>.<lambda> run#1",
     "timeout-cannot-evaluate", _check_timeout_mapping, False, "tempfile", "mkdtemp", 1, "crash",
     "closure/stubbed-run"),
    ("_stubbed_run.<locals>.<lambda> run#1",
     "timeout-cannot-evaluate", _check_timeout_mapping, False, "_materialize", None, 1, "crash", "closure/stubbed-run"),
    ("_check_harness_mapping.<locals>.missing_script _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 1, "none",
     "closure/harness-missing-script"),
    ("_check_harness_mapping.<locals>.killed _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 2, "none", "closure/run-one-timeout"),
    ("_check_harness_mapping.<locals>.no_launch _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 3, "none", "closure/harness-launch"),
    ("_check_harness_mapping.<locals>.judge _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 4, "none", "closure/evidence-streams"),
    ("_check_harness_mapping.<locals>.judge _negative_leg_verdict#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_negative_leg_verdict", None, 1, "none",
     "closure/evidence-streams"),
    ("_check_harness_mapping.<locals>.missing_script _isolated_env#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 1, "crash",
     "closure/harness-missing-script"),
    ("_check_harness_mapping.<locals>.killed _isolated_env#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 2, "crash",
     "closure/run-one-timeout"),
    ("_check_harness_mapping.<locals>.no_launch _isolated_env#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 3, "crash",
     "closure/harness-launch"),
    ("_check_harness_mapping.<locals>.judge _isolated_env#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 4, "crash",
     "closure/evidence-streams"),
    ("_check_harness_mapping.<locals>.judge _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 1, "crash",
     "closure/evidence-streams"),
    ("_check_harness_mapping.<locals>.judge _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 1, "oserror", None),
    ("_check_harness_mapping.<locals>.judge _run_one#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 2, "timeout", None),
    ("_stubbed_run.<locals>.<lambda> run#1",
     "harness-cannot-evaluate", _check_harness_mapping, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("_stubbed_run.<locals>.<lambda> run#1",
     "failure-first", _check_failure_first, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("_check_failure_first.<locals>.refuted_registration run#1",
     "failure-first", _check_failure_first, False, "run", None, 2, "crash", "closure/failure-first"),
    ("_check_leg_wiring.<locals>.wired _closure_legs#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_closure_legs", None, 1, "none", "closure/self-test-exit"),
    ("_check_leg_wiring.<locals>.wired _self_test_exit#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_self_test_exit", None, 1, "crash", "closure/self-test-exit"),
    ("_check_leg_wiring.<locals>.wired _closure_legs#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_materialize", None, 1, "crash", "closure/self-test-exit"),
    ("_check_leg_wiring.<locals>.wired _closure_legs#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_read_copied_store", None, 1, "crash",
     "closure/self-test-exit"),
    ("_check_leg_wiring.<locals>.wired _closure_legs#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_read_copied_store", None, 1, "oserror", None),
    ("_check_leg_wiring.<locals>.wired _closure_legs#1",
     "self-test-leg-wiring", _check_leg_wiring, False, "_write_copied_store", None, 1, "oserror", None),
    ("_check_scratch_and_copy_errors _check_harness_mapping#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_check_harness_mapping", None, 1,
     "crash",
     "closure/scratch-harness"),
    ("_check_scratch_and_copy_errors.<locals>.<lambda> _closure_legs#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_closure_legs", None, 1, "none",
     "closure/negative-harness"),
    ("_expect_negative_cannot _self_test_exit#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_self_test_exit", None, 1, "crash",
     "closure/negative-harness"),
    ("_check_scratch_and_copy_errors.<locals>.refused run#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "run", None, 1, "crash",
     "closure/scratch-run"),
    ("_check_scratch_and_copy_errors.<locals>.refused run#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "run", None, 2, "crash",
     "closure/copy-run"),
    ("_check_scratch_and_copy_errors.<locals>.<lambda> _closure_legs#1",
     "scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, True, "_closure_legs", None, 1, "none",
     "closure/negative-harness"),
    ("_check_negative_copy_error.<locals>.<lambda> _closure_legs#1",
     "negative-copy-cannot-evaluate", _check_negative_copy_error, False, "_closure_legs", None, 1, "none",
     "closure/negative-copy"),
    ("_expect_negative_cannot _self_test_exit#1",
     "negative-copy-cannot-evaluate", _check_negative_copy_error, False, "_self_test_exit", None, 1, "crash",
     "closure/negative-copy"),
    ("_check_negative_copy_error.<locals>.<lambda> _closure_legs#1",
     "negative-copy-cannot-evaluate", _check_negative_copy_error, True, "_closure_legs", None, 1, "none",
     "closure/negative-copy"),
    ("_check_absent_subtree _check_timeout_mapping#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_timeout_mapping#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, True, "_check_timeout_mapping", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_harness_mapping#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_harness_mapping", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_failure_first#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_failure_first", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_leg_wiring#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_leg_wiring", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_scratch_and_copy_errors#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_scratch_and_copy_errors", None, 1, "crash",
     "closure/absent-subtree"),
    ("_check_absent_subtree _check_timeout_mapping#2",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 2, "crash",
     "closure/copy-before-members"),
    ("_check_absent_subtree.<locals>.<lambda> _check_timeout_mapping#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 3, "crash",
     "closure/members-never-ran"),
    ("_check_absent_subtree.<locals>.<lambda> _check_timeout_mapping#1",
     "absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 4, "crash",
     "closure/members-never-copied"),
    ("_check_undecodable_fixture.<locals>.<lambda> _closure_legs#1",
     "undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_closure_legs", None, 1, "none",
     "closure/undecodable-fixture"),
    ("_expect_negative_cannot _self_test_exit#1",
     "undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_self_test_exit", None, 1, "crash",
     "closure/undecodable-fixture"),
    ("_check_undecodable_fixture _check_leg_wiring#1",
     "undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_check_leg_wiring", None, 1, "crash",
     "closure/undecodable-wiring"),
    ("_check_undecodable_fixture _check_leg_wiring#1",
     "undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_not_utf8", None, 1, "oserror", None),
    ("_check_undecodable_attribution.<locals>.<lambda> _check_undecodable_fixture#1",
     "undecodable-attribution", _check_undecodable_attribution, False, "_check_undecodable_fixture", None, 1, "crash",
     "closure/undecodable-attribution"),
    ("_check_undecodable_attribution.<locals>.<lambda> _check_leg_wiring#1",
     "undecodable-attribution", _check_undecodable_attribution, False, "_check_leg_wiring", None, 3, "crash",
     "closure/undecodable-attribution"),
    ("_check_undecodable_attribution.<locals>.<lambda> _check_undecodable_fixture#1",
     "undecodable-attribution", _check_undecodable_attribution, False, "_write_fixture", None, 1, "oserror", None),
    ("_check_undecodable_attribution.<locals>.<lambda> _check_leg_wiring#1",
     "undecodable-attribution", _check_undecodable_attribution, False, "_not_utf8", None, 3, "oserror", None),
    ("_check_preflight_failure_first.<locals>.<lambda> _preflight_exit#1",
     "preflight-failure-first", _check_preflight_failure_first, False, "_preflight_exit", None, 1, "crash",
     "closure/preflight-failure-first"),
    ("_check_preflight_failure_first.<locals>.<lambda> _preflight_exit#2",
     "preflight-failure-first", _check_preflight_failure_first, False, "_preflight_exit", None, 3, "crash",
     "closure/preflight-absent-subtree"),
    ("_red_bound_lookup _check_timeout_mapping#1",
     "red-bound-lookup", _red_bound_lookup, False, "_check_timeout_mapping", None, 1, "crash",
     "closure/red-bound-lookup"),
    ("_red_bound_lookup _check_leg_wiring#1",
     "red-bound-lookup", _red_bound_lookup, False, "_check_leg_wiring", None, 1, "crash", "closure/red-bound-lookup"),
    ("_check_member_boundary.<locals>.boundary_run run#1",
     "member-boundary", _check_member_boundary, False, "run", None, 1, "crash", "closure/boundary-run"),
    ("_check_member_boundary.<locals>.boundary_run run#1",
     "member-boundary", _check_member_boundary, False, "subprocess", "run", 1, "oserror", None),
    ("_check_member_boundary.<locals>.boundary_negative _closure_legs#1",
     "member-boundary", _check_member_boundary, False, "_closure_legs", None, 1, "none", "closure/boundary-negative"),
    ("_check_member_boundary.<locals>.boundary_failed run#1",
     "member-boundary", _check_member_boundary, False, "run", None, 2, "crash", "closure/boundary-failed"),
    ("_check_env_canaries.<locals>.<lambda>.<locals>.<lambda> _check_member_boundary#1",
     "env-canaries", _check_env_canaries, False, "_check_member_boundary", None, 1, "crash",
     "closure/env-canary-m17"),
    ("_check_entry_points.<locals>.entry self_test_main#1",
     "entry-points", _ENTRY, False, "self_test_main", None, 1, "crash", "closure/entry-self-test"),
    ("_check_entry_points.<locals>.entry main#1",
     "entry-points", _ENTRY, False, "main", None, 1, "crash", "closure/entry-main"),
    ("_check_missing_input.<locals>.refused run#1",
     "missing-input", _check_missing_input, False, "run", None, 1, "crash", "closure/missing-input-nonexistent"),
    ("_check_missing_input.<locals>.refused run#1",
     "missing-input", _check_missing_input, False, "run", None, 2, "crash", "closure/missing-input-empty"),
    ("_check_missing_input.<locals>.refused run#1",
     "missing-input", _check_missing_input, False, "run", None, 3, "crash", "closure/missing-input-no-opf-py"),
)

# The indirections (see _derived_sites) the cases reach on purpose, as (the key, the text of the
# source line holding it, stripped, the recorded reason). A wrapper's load (a top-level def holding
# an _ATTRIBUTION_EXCLUSIONS site) is recorded here only where the wrapper is bound for, or injected
# into, an attributed or pinned call, so that what it delegates runs inside that call; a new load of
# a wrapper (calling through it bare) is unrecorded, and one that replaces a recorded load in the same
# co_qualname (the keys are numbered by order there) is stale, since its line is not the recorded
# one. Not seen: a replacement whose source line reads exactly as the recorded load's line.
_ATTRIBUTION_INDIRECTIONS = (
    ("_observed _harness_watch#1",
     "with _patched(**_harness_watch(broke)):",
     "binds the watch around the case's call(); each watched primitive runs inside that attributed call"),
    ("_pin _harness_watch#1",
     "with _patched(**_harness_watch(host)):",
     "binds the pin's own watch around the case it fires; the case's calls are made at its own sites"),
    ("_stubbed_run.<locals>.stubs _copy_recorder#1",
     "return dict(_run_one=_stub_member_runner(calls, outcomes), _materialize=_copy_recorder(made))",
     "injected into the closure/stubbed-run _attributed call; run() makes the copy inside it (pinned "
     "by the _materialize crash entry at _stubbed_run's run#1)"),
    ("_check_leg_wiring.<locals>.<lambda> _copy_recorder#1",
     "_materialize=_copy_recorder(made))))",
     "injected into the closure/self-test-exit _attributed call; _closure_legs makes the copy inside it "
     "(pinned by the _materialize crash entry at wired's _closure_legs#1)"),
    ("_check_member_boundary.<locals>.<lambda> _copy_recorder#1",
     "subprocess=_launch_recorder(launches, \"closure/boundary-run\"), _materialize=_copy_recorder(made)))",
     "injected into the closure/boundary-run _attributed call; run() makes the copy inside it"),
    ("_check_member_boundary.<locals>.<lambda> _copy_recorder#2",
     "_materialize=_copy_recorder(neg_made)))",
     "injected into the closure/boundary-negative _attributed call; _closure_legs makes the copy inside it"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#1",
     "(\"scratch_at=1\", lambda: _failing_harness(scratch_at=1), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#2",
     "(\"write_at=1\", lambda: _failing_harness(write_at=1), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#3",
     "(\"scratch_at=2\", lambda: _failing_harness(scratch_at=2), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#4",
     "(\"copy_at=1\", lambda: _failing_harness(copy_at=1), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#5",
     "(\"scratch_at=3\", lambda: _failing_harness(scratch_at=3), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_undecodable_attribution.<locals>.<lambda> _failing_harness#6",
     "(\"copy_at=2\", lambda: _failing_harness(copy_at=2), _CannotEvaluate, \"injected\"),",
     "injected through _observed as this case's selective failure; it delegates inside the observed call"),
    ("_check_attribution_inventory _check_pin_rules#1",
     "_check_pin_rules(spans)",
     "runs _pin's rule probes, whose bare calls are excluded on purpose: they judge _pin, not the code"),
    ("_check_preflight_failure_first _check_preflight_failure_first#1",
     "if case[1] not in (_check_preflight_failure_first, _check_attribution_inventory,",
     "the identity test that leaves this case out of the cases it runs; never called"),
    ("_Overlay.__getattr__ getattr#1",
     "return getattr(self._target, key)",
     "reads an attribute of the wrapped target by the name Python asked for; calls nothing itself"),
    ("_Overlay.__init__ .__dict__#1",
     "self.__dict__.update(attrs)",
     "stores the replacement attributes the caller passed; their code is the caller's, walked there"),
    ("_check_attribution_inventory _ATTRIBUTION_PINS#1",
     "for site, label, check, *_entry in _ATTRIBUTION_PINS:",
     "reads the pin table to check each entry's site key against the walk; calls nothing from it"),
    ("_check_attribution_inventory _ATTRIBUTION_PINS#2",
     "for site, *entry in _ATTRIBUTION_PINS:",
     "fires each pin entry through _pin, which runs the entry's case raw on purpose to judge its outcome"),
    ("_check_attribution_inventory getattr#1",
     "case = getattr(check, \"__wrapped__\", check).__name__",
     "reads __wrapped__ to name the case function of a pin entry; calls nothing"),
    ("_check_preflight_failure_first _PREFLIGHT_CASES#1",
     "cases = tuple(reversed([case for case in _PREFLIGHT_CASES",
     "runs the other preflight cases through _preflight_exit, under _attributed; each case is walked as a case"),
    ("_derived_sites _PREFLIGHT_CASES#1",
     "for label, check in _PREFLIGHT_CASES:",
     "reads the case table to find each case's function; calls nothing from it"),
    ("_derived_sites getattr#1",
     "function = getattr(check, \"__wrapped__\", check)",
     "reads __wrapped__ to find a case's function; calls nothing"),
    ("_derived_sites globals#1",
     "held = frozenset(name for name, value in globals().items()",
     "reads this module's values to find which hold code (_holds_code); calls nothing"),
    ("_holds_code getattr#1",
     "return any(_holds_code(item, depth + 1) for item in held + list(getattr(value,"
     " \"__dict__\", dict()).values()))",
     "reads an object's instance attributes to inspect them; calls nothing"),
    ("_nth_call getattr#1",
     "inner = getattr(target, attr) if attr else target",
     "reads the attribute a pin entry names, to patch it; the call is made at the case's site, pinned there"),
    ("_nth_call getattr#2",
     "call.__name__ = getattr(inner, \"__name__\", name)",
     "reads the patched callable's __name__ for diagnostics; calls nothing"),
    ("_nth_call globals#1",
     "target = globals()[name]",
     "reads the global a pin entry names, to patch it; the call is made at the case's site, pinned there"),
    ("_patched globals#1",
     "g = globals()",
     "rebinds the globals a case names; the replacement values are the case's own code, walked where defined"),
    ("_site_walk getattr#1",
     "outer = list(getattr(node, \"decorator_list\", ()))",
     "reads an AST node's decorator list; calls nothing"),
    ("_cv _PREFLIGHT_CASES#1",
     "allowed = frozenset(part for label, check in _PREFLIGHT_CASES",
     "reads the live case table only to build the \"case\" vocabulary's membership set; calls "
     "nothing from it"),
    ("_cv _ATTRIBUTION_PINS#1",
     "rows = _ATTRIBUTION_PINS",
     "reads the live pin table only to build the attribution vocabularies' membership sets; calls "
     "nothing from it"),
)

# The derived sites that carry no pin, each with the recorded reason (see _check_attribution_inventory).
_ATTRIBUTION_EXCLUSIONS = (
    ("_harness_watch _materialize#1",
     "wrapped for the watch, which delegates every call to it: the call is made at a case's site, pinned there"),
    ("_harness_watch _read_copied_store#1",
     "wrapped for the watch, which delegates every call to it: the call is made at a case's site, pinned there"),
    ("_harness_watch _write_copied_store#1",
     "wrapped for the watch, which delegates every call to it: the call is made at a case's site, pinned there"),
    ("_failing_harness _materialize#1",
     "wrapped to inject one selective failure, delegating every other call: made at a case's site, pinned there"),
    ("_copy_recorder _materialize#1",
     "wrapped to record the copy, delegating every call: the call is made at a case's site, pinned there"),
    ("_check_pin_rules.<locals>.attributed.<locals>.<lambda> _positive_leg_verdict#1",
     "a probe target of _check_pin_rules, which judges _pin's own rules, not the attribution of the code"),
    ("_check_pin_rules.<locals>.bare _positive_leg_verdict#1",
     "a probe target of _check_pin_rules, called bare on purpose to judge _pin's own rules"),
    ("_check_pin_rules.<locals>.masked _positive_leg_verdict#1",
     "a probe target of _check_pin_rules, masked on purpose to judge _pin's own rules"),
    ("_check_pin_rules.<locals>.sameline.<locals>.<lambda> _positive_leg_verdict#1",
     "a probe target of _check_pin_rules: the attributed call beside the bare one on the same line"),
    ("_check_pin_rules.<locals>.sameline _positive_leg_verdict#1",
     "a probe target of _check_pin_rules, called bare on purpose beside an attributed call on its line"),
    ("_check_pin_rules.<locals>.later.<locals>.<lambda> _positive_leg_verdict#1",
     "a probe target of _check_pin_rules, called bare on a loop's first pass and attributed on its second"),
    ("_check_preflight_failure_first _check_preflight_failure_first#1",
     "an identity test that leaves this case out of the cases it runs (running it would recurse); never called"),
    ("_check_preflight_failure_first _check_attribution_inventory#1",
     "an identity test that leaves the inventory out of the cases it runs (it runs this case); never called"),
    ("_check_preflight_failure_first _check_host_value_independence#1",
     "an identity test that leaves the host-independence case out of the cases it runs (its child runs "
     "this case; running it here would recurse through a child of a child); never called"),
)


# The self-test's preflight: the registration cases and the in-process stubbed cases (no subset member
# runs), as (PASS label, or None for a case that prints its own lines, check(root)). The first seven need
# no scratch directory and no opf/ subtree; the rest build fixtures or stub run() over the real subtree,
# each ordered by the setup its sub-checks need (see _preflight_exit). The attribution inventory runs
# last, once every case it pins has run on its own.
_PREFLIGHT_CASES = (
    (None, _PACK_REG),
    (None, _ADOPT_REG),
    (None, _ROWS),
    ("closure/declared-roster", _DECLARED),
    ("closure/self-test-leg-verdicts", _VERDICTS),
    ("closure/preflight-collection", _COLLECTION),
    ("closure/entry-points", _ENTRY),
    ("closure/closed-sites", _check_closed_sites),
    ("closure/timeout-cannot-evaluate", _check_timeout_mapping),
    ("closure/harness-cannot-evaluate", _check_harness_mapping),
    ("closure/failure-first", _check_failure_first),
    ("closure/self-test-leg-wiring", _check_leg_wiring),
    ("closure/scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors),
    ("closure/negative-copy-cannot-evaluate", _check_negative_copy_error),
    ("closure/missing-input", _check_missing_input),
    ("closure/member-boundary", _check_member_boundary),
    ("closure/env-canaries", _check_env_canaries),
    ("closure/absent-subtree-cannot-evaluate", _check_absent_subtree),
    ("closure/undecodable-fixture-cannot-evaluate", _check_undecodable_fixture),
    ("closure/undecodable-attribution", _check_undecodable_attribution),
    ("closure/preflight-failure-first", _check_preflight_failure_first),
    ("closure/host-independence", _check_host_value_independence),
    (None, _red_bound_lookup),
    ("closure/attribution-inventory", _check_attribution_inventory),
)


def _preflight_exit(root):
    """Run every _PREFLIGHT_CASES case on its own, collecting each outcome, so no case's outcome stops a
    later case.

    The attribution rule: a failure of the HOST (a scratch directory, copy, fixture write, store read,
    flip write or child launch that raised an OSError, or a child that did not finish within its
    bound, at any point, including one step inside a case after others succeeded) is cannot-evaluate;
    a crash or a wrong result of the CODE UNDER TEST is a refutation. The cases are written so that
    each call they make to the code under test goes through _attributed (or _observed, when the case
    judges the raised outcome itself), with any indexing or unpacking of its result inside that
    call; that is a design rule, checked only as stated below. (Around run(), _captured
    always gives a (rc, stdout, stderr) triple whose rc alone comes from the code under test, and rc
    is only compared and printed.) _attributed tells a host failure apart by watching the host
    primitives themselves (_harness_watch, which documents what it does not watch), never the code's
    messages. _check_attribution_inventory checks this rule at the sites _derived_sites can see
    (a pin that fires at the site's call, or a recorded exclusion); it and _check_pin_rules call
    cases and a probe target without _attributed on purpose, to judge their raw outcome (see _pin).

    So an AssertionError is that case's refutation (SELF-TEST FAIL). A _CannotEvaluate, or any other
    Exception (one raised by the case's own code outside every attributed call: a fixture it could not
    build, a decode failure, a RuntimeError), is that case's cannot-evaluate (SELF-TEST CANNOT
    EVALUATE; for any other Exception the line names the case, by its label or else its function's
    name, and the exception). Only a BaseException that is not an Exception (KeyboardInterrupt,
    SystemExit) stops the collection: it re-raises. Failure-first, the same rule as run() and
    _self_test_exit: 1 when any case was refuted, else 2 when any could not be evaluated, else None
    (every case held). Within ONE case (except _check_attribution_inventory, which runs all its
    entries) the first sub-check that cannot be evaluated ends that case: its later sub-checks do not
    run, so a refutation they would give stays unobserved until that case can be set up. Hence the
    cases are split and ordered by setup: the pure cases need none; _check_negative_copy_error,
    _check_missing_input, _check_member_boundary and _check_env_canaries need a scratch directory
    but no opf/ subtree, so each is its own case; _check_host_value_independence needs a scratch
    directory and the opf/ subtree (its child runs cases over the real root); in
    _check_scratch_and_copy_errors and _check_absent_subtree the sub-checks that need no opf/
    subtree run before it is required (the inventory's entries over a root with no opf/ pin that
    order). Within a case whose fixture
    is built first, every sub-check waits for that fixture even when it would need less (in
    _check_harness_mapping, closure/harness-missing-script needs no scratch directory)."""
    failures, cannot = [], []
    for label, check in _PREFLIGHT_CASES:
        try:
            check(root)
        except _CannotEvaluate as exc:
            cannot.append(str(exc))
            continue
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        except Exception as exc:
            cannot.append("closure/preflight-error: {} raised {}".format(
                _cv("case", label if label is not None else check.__name__),
                _cv("exception", type(exc).__name__)))
            continue
        if label is not None:
            _emit("PASS " + _cv("case", label))
    if not failures and not cannot:
        return None
    return _self_test_exit(failures, cannot)


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The positive leg
    runs run() over the real opf/ and requires 0. The negative leg materializes opf/ alone, then RE-INTRODUCES
    the pre-move upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT's `check_versions` instead
    of the extracted `_semver`); with no AIQT tree reachable that import fails, and the flipped copy counts as
    caught only when it exits non-zero WITH opf.py's import-refusal line. A gate that passed the
    deliberately-broken copy would provide no coverage. The preflight (_preflight_exit) runs first and
    follows its attribution rule: when any of its cases was refuted (a wrong result or a crash of the
    code under test) the self-test exits 1; when none was refuted but one could not be evaluated (no
    opf/ subtree, a host primitive that failed, malformed input, or any other ordinary exception
    raised by a case's own code outside its attributed calls) it exits 2; in both outcomes the legs
    never run. Otherwise the legs decide, failure-first: 1 when either leg was refuted, whatever else
    could not be evaluated; 2 when neither was refuted but one could not be evaluated (run() exit 2,
    or the flipped copy timed out, could not launch, its scratch directory, copy or flip could not be
    made, or its _opf_store.py is not UTF-8 text); 0 only when both held, never PASS on a leg that was
    not evaluated."""
    root = repo_root()
    rc = _preflight_exit(root)
    if rc is not None:
        return rc
    return _self_test_exit(*_closure_legs(root))


def _cli_args():
    """The process arguments after the script name, read in one place so the entry cases can stub
    the read (closure/closed-sites refuses a bare load of the sys module outside an attribute
    access, so no case rebinds sys to stub argv)."""
    return sys.argv[1:]


def main():
    if "--self-test" in _cli_args():
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    _emit(code=main())
