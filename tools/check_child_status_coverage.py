#!/usr/bin/env python3
"""Child-status coverage gate (child-status-coverage): every entry point either refuses to run when a child's exit
status cannot be read, is reached only through a launcher that does, reads no child status, or is listed as a
disclosed direct-launch residual.

With SIGCHLD ignored, the kernel reaps every child at exit and Python's subprocess module reports a failed child
as exit 0; the ignore survives exec, so a gate started by an ignoring parent reads every failure as a pass. The
canonical guard is opf/tools/_child_status.py (a disposition read, then a fork-and-wait probe; a refusal by name
with exit 2). This gate holds where it runs:

  1. live: the canonical guard runs in this process first, in every mode; a refusal exits 2 by name.
  2. copies: the inline copies in each CLI_COPIES entry (opf/tools/opf.py, tools/check_selftest_execution.py,
     opf/tools/_opf_adopt_complete.py, opf/tools/_opf_adopt_state.py, opf/tools/_opf_import_complete.py,
     tools/check_gitleaks_pin.py and tools/check_must_deliver.py), in opf/tools/_opf_declaration.py and
     tools/check_opf_declaration.py (the constant and both functions) and in the hooks dispatcher and its
     generated plugin copy (the constant and the probe) are each bound exactly once at module scope, by one
     top-level definition (an undecorated one for a function), and AST-identical (ast.dump, docstrings
     included) to the canonical text, so a call by that name reaches the copy. The binding count (_bindings) is
     the one binding counter of legs 2 and 3; it is a static walk of
     every node that runs in the module's own scope: nested if, try, with, for, while and match blocks, tuple,
     list and starred targets, walrus, for, with-as, except-as and match names, import and from-import aliases
     (a star import counts), del, and a global declaration of the name in any function. Every attribute or
     subscript store or delete at module scope or in a class body (_entry.__code__ = ..., globals()["_entry"] =
     ...) is counted for every name and is never clean: through an alias it can reach the module namespace or
     the function. A rebinding made by a call (setattr, globals().update), by a store inside a function body
     other than through a global declaration, or from another module is not seen.
  3. entries: each CLI copy's __main__ body is exactly sys.exit(_child_status_refusal_exit("<basename>") or
     main()), except that of each SELF_TEST_COPIES entry (the three opf/tools self_test modules above), which
     is exactly opf.py's canonical --self-test statement (SELF_TEST_ENTRY, held to opf.py's own
     _ENTRY_STATEMENT) then that line, with self_test bound once at module scope (the same count), by one
     undecorated top-level def whose first statement after the docstring is the guard call
     _child_status_refusal_exit("<basename>"); the canonical file's entry ends in that line, each
     SELF_TEST_FIRST_COPIES file (opf/tools/_opf_declaration.py) has the __main__ body exactly opf.py's canonical
     --self-test statement and then that line (a direct --self-test launch of it is unguarded; the self-test runs
     that launch under a process-creation audit hook and fails on any launch), each ENTRY_COPIES file
     (tools/check_opf_declaration.py, whose entry raises its own exit rather than calling sys.exit) has the
     __main__ body exactly the single call _entry(), and _entry bound exactly once at module scope (the same
     count), by one undecorated top-level def with no parameters whose first statement is exactly
     _child_status_refusal_exit("<basename>"), and each dispatcher's main() holds HOOK_BLOCK as the statements
     right after its is_fail_open assignment. main() is not pinned: the entry line calls the guard before it
     calls main(), so whatever main names runs after the guard.
  4. launchers: the first run_gate line of tools/run_all_checks.sh and of opf/tools/run_all_checks.sh is the
     probe, and no line above it names Python (read in line order through check_ci_parity.read_runner_text).
     A line names Python when, unless it is a whole-line comment, it holds an interpreter word (python with
     any path before it and any version after it) or a .py word; a # later in the line hides nothing, as it
     can sit inside a word or a quoted string. Every workflow under .github/workflows/ must sit inside a
     closed grammar the gate reads line by line (read_workflow): key lines and '- ' items, each block at one
     indentation deeper than its parent, jobs at two spaces under a top-level jobs: key, steps as '- ' items
     that are each a mapping, and each value a one-line scalar (plain, quoted, or a flow sequence of plain
     words) or a literal block scalar (|) whose lines are text, a # line in it included. A line the grammar
     does not classify (a continued scalar, a repeated key, an anchor, alias or tag, a folded or flow-mapping
     value, a trailing comment, a tab in the indentation) is a finding, never a pass. Inside it, every value
     that names Python sits in a step with a run: key, after its job's one setup-python step and the probe
     step right after that; the probe step is exactly PROBE_STEP (its name, shell: bash and run, and no
     other key, so no if: and no continue-on-error). In the five host-patch jobs (HOST_PATCH_JOBS) of the
     one workflow HOST_PATCH_WORKFLOW only, the Python host pin step, exactly HOST_PIN_STEP, may stand
     between setup-python and the probe (tools/check_python_host_pin.py holds that step as the one right
     after setup-python there), and the probe step right after it is then exactly HOST_PROBE_STEP (the same
     shape, its name saying it runs after that gate); no other step, and no other spelling of either step,
     is passed over. The job carries
     no continue-on-error, and the
     interpreter words are python3 or python only. A value before the probe, in a step with no run: key or
     outside every job's steps (a job-level defaults: shell, an env value) and an absolute or versioned
     interpreter (/usr/bin/python3, python3.14) are each a finding. Every
     hook command registered in the plugin's hooks.json is exactly "command": "python3" with
     "args": ["-I", HOOK_TARGET, <mode>] (the shape tools/gen_hooks.py writes), its mode a HANDLERS key with a
     HANDLER_EVENT entry; and every .py word on a non-comment line of a shell launcher (tools/*.sh,
     opf/**/*.sh, and the declared EXTRA_SHELL_LAUNCHERS opf/enforcement/precommit/pre-commit and
     opf/enforcement/ci/github-actions.yml, the two runners aside) resolves to the exact repository path of a
     self-guarded entry. A word is resolved as the launcher resolves it: an override's default
     (${OPF_TOOL:-default}) is the word read; a $here/ prefix is the launcher's own directory, accepted only
     where the launcher assigns here once in a HERE_BINDINGS form; any other word holding $, { or } does not
     resolve; an absolute word, or one that climbs above the root, is outside the repository; any other word
     is read from the repository root (R12). A word that does not resolve, or resolves to
     a path that is not self-guarded (a same-named file elsewhere included), is a finding. A directory the
     launcher walk cannot list is cannot-evaluate, and a declared launcher that is absent is a finding.
  5. completeness: every entry point, enumerated from (a) every .py with a module-level __main__ guard
     (tools/check_python_floor.py's own walk and EXCLUDED_TREES), (b) both runners' rosters, (c) every workflow's
     python3 launch targets, (d) every registry runner (tools/selftest_checks.toml, through the execution gate's
     schema validator) and (e) the hook registrations, is classified SELF (it carries the pinned guard),
     NOT-APPLICABLE (a static scan of it and its local import closure finds no spawn or wait API), LAUNCHER (it
     reads a child's status and every declared launch is through a guarded launcher) or RESIDUAL (it reads one
     and has no declared launcher). A LAUNCHER or RESIDUAL entry's direct launch is unguarded, so each must be
     listed in DIRECT_LAUNCH_RESIDUAL, which every run prints by name. A new such entry that is not listed, a
     listed entry that is now SELF, NOT-APPLICABLE or gone (the list only shrinks), an unregistered hook mode and
     a declared launch target that does not exist are findings.
  6. switch: DIRECT_LAUNCH_CHECK is False until the Python-floor prelude carries a refusal of its own; set True,
     a LAUNCHER or RESIDUAL entry is a finding whether listed or not, so the residual list must then be empty.

The scan counts an import of subprocess, multiprocessing, pty or _posixsubprocess (asyncio.subprocess and
concurrent.futures.process too) and a literal sys.modules subscript naming one of them; any attribute or
from-import of os or posix named fork, forkpty, wait, wait3, wait4, waitid, waitpid, system, popen,
posix_spawn, posix_spawnp, spawn* or exec*, the module spelled as a name bound to it (an alias included), an
attribute named os or posix (os.path.os) or a literal sys.modules subscript (sys.modules['os']); a star import
from os or posix; and any name, attribute or asyncio or concurrent.futures from-import spelled
create_subprocess_exec, create_subprocess_shell, subprocess_exec, subprocess_shell (the asyncio loop methods)
or ProcessPoolExecutor. Local imports are followed from the importing file's own directory, then tools/ and
opf/tools/ (imports inside functions included; a package is read at its __init__.py only). A call that loads a
module or code at run time (exec or eval as a name; __import__, import_module, spec_from_file_location,
exec_module, load_module, run_path, run_module or the resolvers pkgutil.resolve_name and pydoc.locate as an
attribute, a bare name or a from-import alias) in a file the scan would otherwise call NOT-APPLICABLE is
cannot-evaluate, unless the file's row in DYNAMIC_LOADS_REVIEWED names every load call in it by its exact text
(ast.unparse). That text pins a literal target, so a changed literal target unreviews the file; a target held
in a variable or computed is pinned only by the expression the call spells (R13).

  check_child_status_coverage.py                       check the repository
  check_child_status_coverage.py --self-test           the coverage vectors (suite child-status-coverage-selftest)
  check_child_status_coverage.py --self-test --probe   the canonical probe's vectors (suite child-status-selftest)
  ... [--execution-report ABS_PATH]                    also write the execution report
                                                       (tools/_selftest_exit_report.py)
Exit: 0 every leg holds; 1 findings, each named; 2 cannot evaluate (an input missing, not a regular file,
unreadable, not UTF-8, outside the parity reader's grammar or unparsable; a directory that cannot be listed or
walked; an unresolved dynamic load), the live refusal, or usage. There is no warning tier.

Disclosed residuals, not closed by this gate: R1 an ignore or SA_NOCLDWAIT set below Python after interpreter
start (a native extension, ctypes, an embedding host) is not seen by the disposition read, and the fork probe runs
only where a guard is called: at entry and, in opf.py's opf-adopt-state leg, mid-process (that leg calls
opf/tools/_opf_adopt_state.py's self_test() in process); after that, the in-situ checks in opf/tools/_opf_emit.py
stay the only later defence and they read the disposition only; R2 a child that ignores SIGCHLD itself loses
its own children's statuses (opf.py's own
self-test does this in process for fixtures that test the refusal), which no parent guard sees and this gate
does not police; R3 non-Python tools (gh, gitleaks, jq, curl, tar, sha256sum) were not tested under an
inherited ignore, and their statuses are read by bash or by guarded gates; R4 a refusal is an exit status, so a
Python parent that ignores SIGCHLD reads it as 0 (the self-test witnesses this), and the hook platform's own
reader was not observed, the stderr line being the second channel; R5 whether SA_NOCLDWAIT loses statuses on
other kernels (macOS) is not established, and the fork probe covers it only at the choke points; R6 on a
platform without SIGCHLD or fork the guard refuses nothing; R7 a direct launch of a LAUNCHER or RESIDUAL entry
is unguarded until the floor prelude refuses (DIRECT_LAUNCH_RESIDUAL); R8 tools/run_all_checks.sh continues
after the probe fails, so later gates may print PASS lines in a run that ends RESULT: FAIL; R9 a fork in a
multi-threaded process warns, so every guard runs at entry or at the top of the dispatcher's main, except the
mid-process call in R1, which runs after opf.py's earlier self-test legs; R10 the
static scan over-approximates through whole-closure imports, and it does not see a spawn reached (a) inside a
module it does not read, the standard library or a third-party package (platform.architecture and
ctypes.util.find_library each start a child through subprocess), (b) through getattr, sys.modules.get, a
variable or any other computed name (a sys.modules subscript with a computed key included), (c) in a package
submodule no import names (a package is read at its __init__.py only), (d) in a C extension or through ctypes,
or (e) through a star import from a module other than os, posix or a spawn module; R11 process-group and
child-subreaper interplay (PR_SET_CHILD_SUBREAPER) is not checked; R12 a shell launcher's Python launch is
recognised by a .py word on a non-comment line only, an override such as OPF_TOOL set by the operator replaces
the default the gate resolves, here and dir are read from their plain here= and dir= assignments only, and a
relative word is resolved from the repository root, so a launcher started from another directory reads it from
there; the walk for launchers under opf/ does not descend into a symlinked directory (os.walk follows none), so
a launcher reached only through one is not read; R13 a reviewed load whose target is held in a variable or
computed stays reviewed when the assignment, argument or computation changes, since its row pins the call text
only, and the scan then follows the row's targets, not the new one (the self-test shows this limit); R14 a
workflow value or runner line that starts Python through a variable ($PY, ${{ env.PY }}), a console script
(pip, pytest) or any other name the interpreter pattern does not match is not seen as naming Python; R15 the
workflow grammar is this gate's own reading of a YAML subset, held to the self-test's cases and the current
workflows and not compared with the parser GitHub runs, so a file inside it that GitHub reads another way is
not seen; R16 the binding count of legs 2 and 3 (_bindings) reads the module's own binding statements, so a
rebinding through a computed name made by a call (setattr on the module, globals().update) or inside a function
body (a globals() or sys.modules[__name__] store there) is not seen. Whether a real
launcher starts these tools with SIGCHLD ignored, and whether the steps of one CI job share one disposition, are
not established: each job's probe step reads the disposition of its own step only, so it cannot show what the
job's other steps inherit.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_child_status_coverage.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import importlib.util
import io
import json
import os
import posixpath
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import check_ci_parity  # noqa: E402  (the validated roster reader and loader)
import check_entry_guard  # noqa: E402  (the non-blocking regular-file reader and the registry runners)
import check_python_floor  # noqa: E402  (the shipped-entrypoint walk)
import check_selftest_execution  # noqa: E402  (the registry schema)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REL = "opf/tools/_child_status.py"
COVERAGE_REL = "tools/" + Path(__file__).name
CANONICAL_NAMES = ("_CHILD_STATUS_PROBE_CODE", "_child_status_refusal", "_child_status_refusal_exit")
HOOK_NAMES = CANONICAL_NAMES[:2]
CLI_COPIES = (("opf/tools/opf.py", "opf.py"), ("tools/check_selftest_execution.py", "check_selftest_execution.py"),
              ("opf/tools/_opf_adopt_complete.py", "_opf_adopt_complete.py"),
              ("opf/tools/_opf_adopt_state.py", "_opf_adopt_state.py"),
              ("opf/tools/_opf_import_complete.py", "_opf_import_complete.py"),
              ("tools/check_gitleaks_pin.py", "check_gitleaks_pin.py"),
              ("tools/check_must_deliver.py", "check_must_deliver.py"))
# The CLI copy whose __main__ body is the one call _entry() (it raises its own exit rather than calling sys.exit):
# its guard is the first statement of its one top-level _entry(), exactly ENTRY_GUARD.
ENTRY_COPIES = (("tools/check_opf_declaration.py", "check_opf_declaration.py"),)
ENTRY_GUARD = '_child_status_refusal_exit("{}")'
# The CLI copy whose __main__ body must open with opf.py's canonical --self-test statement (opf.py's aggregator
# requires it first in every opf/tools module that binds self_test; that self_test also runs in process under
# opf.py --self-test, behind opf.py's own guard): its body is exactly that statement and then the CLI entry line.
SELF_TEST_FIRST_COPIES = (("opf/tools/_opf_declaration.py", "_opf_declaration.py"),)
SELF_TEST_FIRST = 'if sys.argv[1:] == ["--self-test"]:\n    sys.exit(self_test())\n'
HOOK_COPIES = (".aiqt/core/hooks/scripts/aiqt_hooks.py", "plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py")
HOOKS_JSON_REL = "plugin/aiqt-guardrails-hooks/hooks/hooks.json"
HOOK_TARGET = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/aiqt_hooks.py"
# The one accepted hook registration, as tools/gen_hooks.py writes it: "command" is exactly HOOK_COMMAND and
# "args" is exactly HOOK_ARGS followed by one mode.
HOOK_COMMAND = "python3"
HOOK_ARGS = ["-I", HOOK_TARGET]
# The entries that carry the pinned guard: the canonical file, this gate (leg 1 runs first in main), the CLI
# copies, the _entry() copy, the --self-test-first copy and the two dispatcher copies.
SELF_ENTRIES = frozenset((CANONICAL_REL, COVERAGE_REL)
                         + tuple(rel for rel, _name in CLI_COPIES + ENTRY_COPIES + SELF_TEST_FIRST_COPIES)
                         + HOOK_COPIES)
CLI_ENTRY = 'sys.exit(_child_status_refusal_exit("{}") or main())'
# The CLI copies that are opf/tools self_test modules: opf.py's aggregator requires their __main__ block to start
# with its canonical --self-test statement (SELF_TEST_ENTRY, opf.py's _ENTRY_STATEMENT), so their __main__ body is
# exactly that statement then the CLI entry line, and the guard call SELF_TEST_GUARD is the first statement of
# self_test() after its docstring, so the --self-test path refuses too.
SELF_TEST_COPIES = ("opf/tools/_opf_adopt_complete.py", "opf/tools/_opf_adopt_state.py",
                    "opf/tools/_opf_import_complete.py")
SELF_TEST_ENTRY = 'if sys.argv[1:] == ["--self-test"]:\n    sys.exit(self_test())\n'
SELF_TEST_GUARD = '_child_status_refusal_exit("{}")'
# The file whose _ENTRY_STATEMENT SELF_TEST_ENTRY copies: it must bind that name once, at the top level, to a string
# that parses to the statement SELF_TEST_ENTRY parses to (entry_statement_finding), so the two cannot drift apart.
ENTRY_STATEMENT_REL = "opf/tools/opf.py"
HOOK_ANCHOR = "is_fail_open = HANDLER_EVENT[handler_name] in FAIL_OPEN_EVENTS"
HOOK_BLOCK = """unobservable = _child_status_refusal()
if unobservable is not None:
    if not is_fail_open:
        _stderr_note("aiqt_hooks: {}: {}, so a failed child would read as a success; failing closed"
                     .format(handler_name, unobservable))
        return 2
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    restored = _child_status_refusal()
    if restored is not None:
        try:
            return _dispatcher_fail_open_warn(handler_name, "child exit status unobservable: " + restored)
        except BaseException:
            return 0
    _stderr_note("aiqt_hooks: {}: {}; SIGCHLD restored to SIG_DFL for this hook process and the probe "
                 "then read its child's status".format(handler_name, unobservable))
"""
LOCAL_RUNNER = check_ci_parity.LOCAL_SOURCE
OPF_RUNNER = check_ci_parity.STANDALONE_SOURCE
RUNNER_PROBES = ((LOCAL_RUNNER, 'run_gate "child-status" python3 -I -B ' + CANONICAL_REL),
                 (OPF_RUNNER, 'run_gate "child-status" python3 -I -B "$here/_child_status.py"'))
WORKFLOWS_REL = ".github/workflows"
# The probe step, exactly: these keys in this order with these values, and no other key.
PROBE_STEP = (("name", "Child exit status observable (before any gate)"), ("shell", "bash"),
              ("run", "python3 -I -B " + CANONICAL_REL))
# The five host-patch jobs, the one workflow they live in and the Python host pin step, exactly:
# tools/check_python_host_pin.py holds that step as the one right after each of these jobs' setup-python
# step, so in these jobs of HOST_PATCH_WORKFLOW only, exactly that step may stand between setup-python and
# the probe, and the probe step right after it is HOST_PROBE_STEP, the probe with the name that placement
# makes true. The exception is keyed on the workflow path and the job id together, so a job with any of these
# ids in any other workflow gets the plain reading; the exactness of both steps is the bound.
HOST_PATCH_WORKFLOW = WORKFLOWS_REL + "/quality.yml"
HOST_PATCH_JOBS = ("opf-host-patch", "git-fixture-env-core-host-patch", "git-fixture-env-config-combined-host-patch",
                   "git-fixture-env-config-malformed-host-patch", "git-fixture-env-config-system-host-patch")
HOST_PIN_STEP = (("name", "Python host pin"), ("shell", "bash"),
                 ("run", "python3 -I -B tools/check_python_host_pin.py"))
HOST_PROBE_STEP = (("name", "Child exit status observable (after the host pin gate)"), ("shell", "bash"),
                   ("run", "python3 -I -B " + CANONICAL_REL))
SETUP_PYTHON_USES = "actions/setup-python@"
JOBS_INDENT = 2
# The closed workflow grammar (read_workflow): a key line, and the one-line scalars a value may be (quoted and
# closed on its line, a flow sequence of plain words, or a plain scalar that starts with no indicator and holds
# no ': ' and no ' #').
KEY_LINE_RE = re.compile(r"([A-Za-z0-9_-]+):(?: (.*))?")
SINGLE_QUOTED_RE = re.compile(r"'(?:[^']|'')*'")
DOUBLE_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
FLOW_WORDS_RE = re.compile(r"\[[A-Za-z0-9_.-]+(?:, [A-Za-z0-9_.-]+)*\]")
PLAIN_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"
PLAIN_BREAK_RE = re.compile(r":(?:\s|$)|\s#")
# An interpreter word: python with any path before it and any version or ABI letter after it (/usr/bin/python3,
# python3.14, python3.14t), not part of a longer word (setup-python, python-version, check_python_floor.py).
# Only PLAIN_INTERPRETERS are accepted in a workflow: the interpreter setup-python puts on PATH.
PYTHON_WORD_RE = re.compile(r"(?<![\w.])(?<![A-Za-z0-9]-)(?:[^\s;|&()<>=]*/)?python[0-9.]*[a-z]?(?![\w.-])")
PLAIN_INTERPRETERS = ("python3", "python")
PY_LAUNCH_RE = re.compile(r"(?<![\w./-])python3?\s+(?:-[A-Za-z]+\s+)*([A-Za-z0-9_./-]+\.py)(?![\w.])")
PY_WORD_RE = re.compile(r"[A-Za-z0-9_./${}:-]*\.py(?![\w.])")
EXTRA_SHELL_LAUNCHERS = ("opf/enforcement/precommit/pre-commit", "opf/enforcement/ci/github-actions.yml")
# A shell word's override default (${NAME:-default}), the prefixes that name the launcher's own directory, and
# the only assignments that bind here to it (the second through dir, itself bound once as DIR_BINDING).
SHELL_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-")
HERE_PREFIXES = ("$here/", "${here}/")
HERE_BINDINGS = ('here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)', 'here=$(CDPATH= cd -- "$dir" && pwd)')
DIR_BINDING = 'dir=$(dirname -- "$0")'
HERE_ASSIGN_RE = re.compile(r"(?<![\w$])here=")
DIR_ASSIGN_RE = re.compile(r"(?<![\w$])dir=")
SPAWN_MODULES = ("subprocess", "multiprocessing", "pty", "_posixsubprocess")
SPAWN_SUBMODULES = ("asyncio.subprocess", "concurrent.futures.process")
OS_MODULES = ("os", "posix")
OS_SPAWN_RE = re.compile(r"fork|forkpty|wait[34]?|waitid|waitpid|system|popen|posix_spawnp?|spawn[lv]p?e?|"
                         r"exec[lv]p?e?")
# Spawn names counted wherever they appear: the asyncio functions and loop methods, and the process pool.
SPAWN_NAMES = frozenset(("create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell",
                         "ProcessPoolExecutor"))
SPAWN_NAME_PACKAGES = ("asyncio", "concurrent")
DYNAMIC_NAMES = frozenset(("__import__", "exec", "eval"))
# Load calls counted as an attribute, a bare name or a from-import alias: the importlib and runpy loaders and the
# resolvers that turn a dotted string into a module attribute (pkgutil.resolve_name, pydoc.locate).
DYNAMIC_ATTRS = frozenset(("__import__", "import_module", "spec_from_file_location", "exec_module", "load_module",
                           "run_path", "run_module", "resolve_name", "locate"))
LOCAL_DIRS = ("tools", "opf/tools")
CHILD_TIMEOUT = 120

# Stage 1 keeps the direct-launch check off; it turns on, with DIRECT_LAUNCH_RESIDUAL emptied in the same
# change, once the Python-floor prelude refuses an ignored SIGCHLD in every entry point.
DIRECT_LAUNCH_CHECK = False
# Every entry point that reads a child's exit status and carries no guard of its own, sorted: a direct launch of
# it from an ignoring parent is unguarded. Computed by this gate's own classification and reviewed; it only
# shrinks.
DIRECT_LAUNCH_RESIDUAL = (
    ".github/check_cited_standards_contract.py",
    ".preview/char-policy-write.py",
    ".preview/clock-inject.py",
    ".preview/constraint-reread.py",
    ".preview/future-stamp-write.py",
    ".preview/pattern-self-match.py",
    ".preview/record-remove-check.py",
    ".preview/rerun-pass-check.py",
    ".preview/stamp-truth-stop.py",
    ".preview/unbounded-wait.py",
    ".preview/ungated-record.py",
    "opf/tools/_opf_absorb.py",
    "opf/tools/_opf_adopt.py",
    "opf/tools/_opf_adopt_apply.py",
    "opf/tools/_opf_adopt_discover.py",
    "opf/tools/_opf_adopt_hook.py",
    "opf/tools/_opf_adopt_observe.py",
    "opf/tools/_opf_adopt_plan.py",
    "opf/tools/_opf_changelog.py",
    "opf/tools/_opf_check.py",
    "opf/tools/_opf_emit.py",
    "opf/tools/_opf_fuzz.py",
    "opf/tools/_opf_import_batch.py",
    "opf/tools/_opf_init.py",
    "opf/tools/_opf_init_operation.py",
    "opf/tools/_opf_init_substrate.py",
    "opf/tools/_opf_observe.py",
    "opf/tools/_opf_oplock.py",
    "opf/tools/_opf_pack_manifest.py",
    "opf/tools/_opf_record.py",
    "opf/tools/_opf_release.py",
    "opf/tools/_opf_schema.py",
    "opf/tools/_opf_store.py",
    "opf/tools/_opf_views.py",
    "opf/tools/_opf_worklog.py",
    "opf/tools/_opf_worklog_regressions.py",
    "opf/tools/_opf_write_guard.py",
    "opf/tools/check_opf_doctor.py",
    "opf/tools/check_opf_drift.py",
    "opf/tools/check_opf_homes.py",
    "opf/tools/check_opf_init.py",
    "opf/tools/check_opf_init_observe.py",
    "opf/tools/check_opf_init_p0.py",
    "opf/tools/check_opf_init_qa.py",
    "opf/tools/check_opf_prompt_pack.py",
    "opf/tools/check_opf_record.py",
    "opf/tools/selftest_commonmark_conformance.py",
    "tools/_qa_adapter.py",
    "tools/aei_backlog_md.py",
    "tools/aiqt_corpus.py",
    "tools/audit_reference.py",
    "tools/check_applies.py",
    "tools/check_artifact_checksums.py",
    "tools/check_branch_root.py",
    "tools/check_brief_capability.py",
    "tools/check_byte_canon.py",
    "tools/check_ci_parity.py",
    "tools/check_clauses.py",
    "tools/check_crosswalk.py",
    "tools/check_entry_guard.py",
    "tools/check_gensrc_failclose.py",
    "tools/check_git_option_table.py",
    "tools/check_hooks_preview.py",
    "tools/check_instruction_budget.py",
    "tools/check_internal_names.py",
    "tools/check_license_qualification.py",
    "tools/check_manifest.py",
    "tools/check_mappings.py",
    "tools/check_mistakes_register.py",
    "tools/check_msg_leaks.py",
    "tools/check_newtab.py",
    "tools/check_no_dashes.py",
    "tools/check_opf_standalone_closure.py",
    "tools/check_overclaim.py",
    "tools/check_portability.py",
    "tools/check_python_floor.py",
    "tools/check_python_host_pin.py",
    "tools/check_python_launcher_isolation.py",
    "tools/check_record_drift.py",
    "tools/check_record_sections.py",
    "tools/check_reference_facts.py",
    "tools/check_release_build.py",
    "tools/check_release_cut.py",
    "tools/check_release_delta.py",
    "tools/check_rule_placement.py",
    "tools/check_site.py",
    "tools/check_sized_instructions.py",
    "tools/check_standards_currency.py",
    "tools/check_standards_urls_live.py",
    "tools/check_version_monotonicity.py",
    "tools/conformance.py",
    "tools/doctor.py",
    "tools/gen_adapters.py",
    "tools/gen_agents.py",
    "tools/gen_claude.py",
    "tools/gen_cursor.py",
    "tools/gen_enforceability.py",
    "tools/gen_enforcement_register.py",
    "tools/gen_first_pin_demo.py",
    "tools/gen_hooks.py",
    "tools/gen_install.py",
    "tools/gen_manifest.py",
    "tools/gen_mappings.py",
    "tools/gen_reference_facts.py",
    "tools/gen_renderers.py",
    "tools/gen_rules.py",
    "tools/gen_skill.py",
    "tools/gen_worker_pack.py",
    "tools/import_cwe.py",
    "tools/measure_hook_latency.py",
    "tools/migrate.py",
    "tools/orch_register.py",
    "tools/pin.py",
    "tools/selftest_aiqt_corpus.py",
    "tools/selftest_aiqt_hooks.py",
    "tools/selftest_ci_status.py",
    "tools/selftest_git_fixture_env.py",
    "tools/selftest_git_fixture_env_config_combined.py",
    "tools/selftest_git_fixture_env_config_malformed.py",
    "tools/selftest_git_fixture_env_config_system.py",
    "tools/selftest_git_fixture_env_core.py",
    "tools/selftest_orch_hooks.py",
)

# Run-time code loads reviewed by hand: (file, the exact text (ast.unparse) of every load call the scan sees in
# it, sorted, the repository files those calls load, which the scan then follows as imports, and what is
# loaded). The call text carries a literal load target, so a file whose load calls or literal targets differ
# from its row is unreviewed again (cannot-evaluate when nothing else in it reads a child's status), and a row
# that no longer matches its file is a stale finding. A target held in a variable or computed is pinned only by
# the expression the call spells: a changed assignment, argument or computation leaves the row reviewed, and the
# scan follows the row's targets, not the new one (R13; the self-test shows this limit).
DYNAMIC_LOADS_REVIEWED = (
    ("opf/tools/check_opf_init_contract.py", ("runpy.run_path(str(ROOT / 'opf/tools/_opf_init_contract.py'))",),
     ("opf/tools/_opf_init_contract.py",), "runpy.run_path of the literal opf/tools/_opf_init_contract.py"),
    ("tools/check_footer.py", ("exec(compile(source.replace(new, old), __file__, 'exec'), reverted)",),
     ("tools/check_footer.py",), "its own source with one check reverted, compiled and run in its self-test"),
    ("tools/check_opf_enforce_platforms.py",
     ("importlib.util.spec_from_file_location('_opf_enforce_platforms_hook', str(path))",
      "spec.loader.exec_module(module)"),
     ("opf/enforcement/claude/pretooluse_deny.py",), "the Claude Code deny hook, loaded and never run"),
    ("tools/check_python_host_pin.py",
     ("importlib.util.spec_from_file_location(name, base / (name + '.py'))", "spec.loader.exec_module(module)"),
     ("tools/check_python_floor.py", "tools/check_python_host_pin.py"),
     "its own source, whole or with one check changed, and the python-floor gate's source for the names it "
     "shares, loaded in its self-test"),
    ("tools/gen_crosswalk.py",
     ("importlib.util.spec_from_file_location('_crosswalk_fdopen_flip_' + flip_path.stem, flip_path)",
      "spec.loader.exec_module(reverted)"),
     ("tools/gen_crosswalk.py",), "its own source with one writer flipped, loaded in its self-test"),
)

CannotEvaluate = check_entry_guard.CannotEvaluate


def _read(root, rel):
    """One input's bytes, opened non-blocking without following a final symlink, regular files only."""
    return check_entry_guard._read_regular(Path(root) / rel, rel)


def _text(root, rel):
    """A runner or workflow, through the parity gate's one byte-level reader (printable ASCII, tab, newline)."""
    text, diagnostic = check_ci_parity.read_runner_text(Path(root) / rel, rel, reader=lambda path: _read(root, rel))
    if diagnostic is not None:
        raise CannotEvaluate("{}: {}".format(rel, diagnostic.message))
    return text


def _decoded(data, rel):
    """hooks.json or a shell launcher as strict UTF-8: a byte that does not decode is cannot-evaluate, never
    replaced."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 (byte {})".format(rel, exc.start))


def _listdir(root, rel, listdir=os.listdir):
    """The sorted names in one directory; a directory that cannot be listed is cannot-evaluate."""
    try:
        return sorted(listdir(Path(root) / rel))
    except OSError as exc:
        raise CannotEvaluate("{}: cannot be listed ({})".format(rel, type(exc).__name__))


def _parse(data, rel):
    try:
        return ast.parse(data, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({})".format(rel, type(exc).__name__))


# ------------------------------------------------------------------------------------- legs 2 and 3: copies

def _top_definitions(tree, name):
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            found.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                found.append(node)
    return found


# The form each binding _bindings finds is named by in a finding: a def or class by its kind, and a stored or
# deleted name by the statement or walrus it is a target of.
DEF_FORMS = {"FunctionDef": "def", "AsyncFunctionDef": "async def", "ClassDef": "class"}
TARGET_FORMS = {"Assign": "an assignment", "AugAssign": "an augmented assignment",
                "AnnAssign": "an annotated assignment", "For": "a for target", "AsyncFor": "a for target",
                "With": "a with-as target", "AsyncWith": "a with-as target", "NamedExpr": "a walrus (:=)",
                "Delete": "a del", "TypeAlias": "a type alias"}


def _binding_forms(node, owner, name):
    """The form of each binding of name that one module-scope node makes (owner is the statement or walrus it
    sits in), one per binding."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        decorated = "a decorated " if node.decorator_list else "a "
        return [decorated + DEF_FORMS[type(node).__name__]] if node.name == name else []
    if isinstance(node, ast.Name):
        stored = node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
        return [TARGET_FORMS.get(type(owner).__name__, "a target")] if stored else []
    if isinstance(node, ast.Import):
        return ["an import" for alias in node.names if (alias.asname or alias.name.partition(".")[0]) == name]
    if isinstance(node, ast.ImportFrom):
        return ["a star import" if alias.name == "*" else "a from-import" for alias in node.names
                if (alias.asname or alias.name) in (name, "*")]
    if isinstance(node, ast.ExceptHandler):
        return ["an except-as name"] if node.name == name else []
    if isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
        return ["a match capture"] if (node.rest if isinstance(node, ast.MatchMapping) else node.name) == name else []
    return []


def _bindings(tree, name):
    """Every binding of name at module scope, as (line, form) in line order. The walk reads every node that runs
    in the module's own scope: nested if, try, with, for, while and match blocks, and the decorators, defaults,
    annotations and bases of a def, lambda or class, but no function or lambda body and no comprehension's own
    plain targets; a class body is read for stores only. It counts a def, async def or class so named, each Name
    stored or deleted (an assignment, augmented or annotated assignment, for, with-as, walrus or del target,
    tuple, list and starred targets included, or a type alias), each import or from-import alias that binds it
    (a star import counts, as it may), an except-as or match name, and each global declaration of name anywhere
    in the file. Every store to or delete of an attribute or a subscript at module scope or in a class body is
    counted for every name: through an alias it can reach the module namespace or the function itself, so the
    walk cannot classify it. A binding made by a call, inside a function body other than through a global
    declaration, or from another module is not seen."""
    found = [(node.lineno, "a global declaration") for node in ast.walk(tree)
             if isinstance(node, ast.Global) and name in node.names]
    stack = [(tree, None, False)]
    while stack:
        node, owner, in_class = stack.pop()
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            found.append((node.lineno, "a store the walk cannot classify ({})".format(ast.unparse(node))))
        elif not in_class:
            found.extend((node.lineno, form) for form in _binding_forms(node, owner, name))
        owner = node if isinstance(node, (ast.stmt, ast.NamedExpr)) else owner
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children = node.decorator_list + [node.args] + ([node.returns] if node.returns else [])
        elif isinstance(node, ast.ClassDef):
            children = node.decorator_list + node.bases + node.keywords
            stack.extend((statement, statement, True) for statement in node.body)
        elif isinstance(node, ast.Lambda):
            children = [node.args]
        elif isinstance(node, ast.comprehension):
            children, targets = [node.iter] + node.ifs, [node.target]
            while targets:
                target = targets.pop()
                if isinstance(target, (ast.Tuple, ast.List)):
                    targets.extend(target.elts)
                elif isinstance(target, ast.Starred):
                    targets.append(target.value)
                elif not isinstance(target, ast.Name):
                    children.append(target)
        else:
            children = list(ast.iter_child_nodes(node))
        stack.extend((child, owner, in_class) for child in children)
    return sorted(found)


def _forms(bindings):
    return ", ".join("{} (line {})".format(form, line) for line, form in bindings) or "none"


def template(canonical):
    """{name: ast.dump} of the canonical definitions; a canonical file without exactly one of each cannot be
    evaluated."""
    out = {}
    for name in CANONICAL_NAMES:
        nodes = _top_definitions(canonical, name)
        if len(nodes) != 1:
            raise CannotEvaluate("{}: the canonical {} is defined {} times".format(CANONICAL_REL, name, len(nodes)))
        out[name] = ast.dump(nodes[0])
    return out


def copy_findings(rel, tree, tmpl, names):
    """Each name's inline copy is bound exactly once at module scope (_bindings), by one top-level definition,
    undecorated and AST-identical to the canonical one."""
    out = []
    for name in names:
        bindings = _bindings(tree, name)
        nodes = _top_definitions(tree, name)
        if len(bindings) != 1 or len(nodes) != 1:
            out.append("{}: the inline copy of {} is bound {} times at module scope ({}), not once by one top-level "
                       "definition".format(rel, name, len(bindings), _forms(bindings)))
        elif getattr(nodes[0], "decorator_list", None):
            out.append("{}: the inline copy of {} is decorated, so the name need not hold the function".format(
                rel, name))
        elif ast.dump(nodes[0]) != tmpl[name]:
            out.append("{}: the inline copy of {} differs from {}".format(rel, name, CANONICAL_REL))
    return out


def cli_entry_finding(rel, tree, name, exact=True):
    """None when the module's __main__ body is exactly the CLI entry line (or, with exact false, ends in it)."""
    block = tree.body[-1] if tree.body and check_entry_guard._is_guard(tree.body[-1]) else None
    want = ast.dump(ast.parse(CLI_ENTRY.format(name)).body[0])
    if (block is None or not block.body or ast.dump(block.body[-1]) != want
            or (exact and len(block.body) != 1)):
        return "{}: its __main__ entry is not {}{}".format(rel, "exactly " if exact else "ending in ",
                                                          CLI_ENTRY.format(name))
    return None


def entry_function_finding(rel, tree, name):
    """None when the module's __main__ body is exactly _entry(), _entry is bound exactly once at module scope
    (_bindings), by one undecorated top-level function with no parameters, and that function opens with exactly
    the guard call ENTRY_GUARD."""
    block = tree.body[-1] if tree.body and check_entry_guard._is_guard(tree.body[-1]) else None
    body = block.body if block is not None else []
    entry = ast.dump(ast.parse("_entry()").body[0])
    if len(body) != 1 or ast.dump(body[-1]) != entry:
        return "{}: its __main__ entry is not exactly _entry()".format(rel)
    bindings = _bindings(tree, "_entry")
    definitions = _top_definitions(tree, "_entry")
    if len(bindings) != 1 or len(definitions) != 1 or not isinstance(definitions[0], ast.FunctionDef):
        return "{}: _entry is bound {} times at module scope ({}), not once by one top-level function".format(
            rel, len(bindings), _forms(bindings))
    if definitions[0].decorator_list:
        return "{}: its _entry() is decorated, so the name need not hold the function".format(rel)
    if ast.dump(definitions[0].args) != ast.dump(ast.parse("def _entry():\n    pass\n").body[0].args):
        return "{}: its _entry() takes parameters, so a default could stand in for the guard".format(rel)
    guard = ast.dump(ast.parse(ENTRY_GUARD.format(name)).body[0])
    statements = [ast.dump(statement) for statement in definitions[0].body]
    if statements[:1] != [guard]:
        return "{}: its _entry() does not open with {}".format(rel, ENTRY_GUARD.format(name))
    return None


def self_test_first_finding(rel, tree, name):
    """None when the module's __main__ body is exactly SELF_TEST_FIRST and then the CLI entry line."""
    block = tree.body[-1] if tree.body and check_entry_guard._is_guard(tree.body[-1]) else None
    expected = [ast.dump(statement) for statement in ast.parse(SELF_TEST_FIRST + CLI_ENTRY.format(name)).body]
    if block is None or [ast.dump(statement) for statement in block.body] != expected:
        return "{}: its __main__ entry is not exactly the --self-test statement and then {}".format(
            rel, CLI_ENTRY.format(name))
    return None


def self_test_entry_finding(rel, tree, name):
    """None when the module's __main__ body is exactly SELF_TEST_ENTRY then the CLI entry line, self_test is bound
    once at module scope (_bindings), by one undecorated top-level def, so the self_test() the entry calls is
    that def, and that def starts, after any docstring, with the guard call naming it."""
    block = tree.body[-1] if tree.body and check_entry_guard._is_guard(tree.body[-1]) else None
    want = [ast.dump(statement) for statement in ast.parse(SELF_TEST_ENTRY + CLI_ENTRY.format(name)).body]
    if block is None or [ast.dump(statement) for statement in block.body] != want:
        return "{}: its __main__ entry is not exactly the --self-test statement then {}".format(
            rel, CLI_ENTRY.format(name))
    tests = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "self_test"]
    if len(tests) != 1 or tests[0].decorator_list or len(_bindings(tree, "self_test")) != 1:
        return ("{}: self_test is not bound once, by one undecorated top-level def, so the self_test() its entry "
                "calls may not start with {}".format(rel, SELF_TEST_GUARD.format(name)))
    body = tests[0].body
    if ast.get_docstring(tests[0], clean=False) is not None:
        body = body[1:]
    guard = ast.dump(ast.parse(SELF_TEST_GUARD.format(name)).body[0])
    if not body or ast.dump(body[0]) != guard:
        return "{}: its self_test() does not start with {}".format(rel, SELF_TEST_GUARD.format(name))
    return None


def entry_statement_finding(rel, tree):
    """None when the module binds _ENTRY_STATEMENT once at module scope (_bindings), at the top level, to a string
    that parses to the statement SELF_TEST_ENTRY parses to."""
    nodes = _top_definitions(tree, "_ENTRY_STATEMENT")
    value = (nodes[0].value if len(nodes) == 1 and isinstance(nodes[0], (ast.Assign, ast.AnnAssign))
             and len(_bindings(tree, "_ENTRY_STATEMENT")) == 1 else None)
    try:
        same = (isinstance(value, ast.Constant) and type(value.value) is str
                and ast.dump(ast.parse(value.value)) == ast.dump(ast.parse(SELF_TEST_ENTRY)))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        same = False
    if not same:
        return "{}: its _ENTRY_STATEMENT is not one top-level string that parses to SELF_TEST_ENTRY {!r}".format(
            rel, SELF_TEST_ENTRY)
    return None


def cli_copy_findings(load, tmpl):
    """Legs 2 and 3 for every CLI_COPIES entry, each read through load(rel) as a parsed tree: its copy, its entry
    (self_test_entry_finding for a SELF_TEST_COPIES entry, cli_entry_finding for any other) and, for
    ENTRY_STATEMENT_REL, the pin of SELF_TEST_ENTRY to its _ENTRY_STATEMENT."""
    findings = []
    for rel, name in CLI_COPIES:
        tree = load(rel)
        findings.extend(copy_findings(rel, tree, tmpl, CANONICAL_NAMES))
        finding = (self_test_entry_finding(rel, tree, name) if rel in SELF_TEST_COPIES
                   else cli_entry_finding(rel, tree, name))
        findings.extend([finding] if finding else [])
        finding = entry_statement_finding(rel, tree) if rel == ENTRY_STATEMENT_REL else None
        findings.extend([finding] if finding else [])
    return findings


def hook_block_finding(rel, tree):
    mains = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    if len(mains) != 1:
        return "{}: {} top-level main() definitions, not one".format(rel, len(mains))
    body = mains[0].body
    anchor = ast.dump(ast.parse(HOOK_ANCHOR).body[0])
    at = [index for index, statement in enumerate(body) if ast.dump(statement) == anchor]
    if len(at) != 1:
        return "{}: main() holds the is_fail_open assignment {} times, not once".format(rel, len(at))
    want = [ast.dump(statement) for statement in ast.parse(HOOK_BLOCK).body]
    got = [ast.dump(statement) for statement in body[at[0] + 1:at[0] + 1 + len(want)]]
    if got != want:
        return "{}: main() does not hold the child-status block right after its is_fail_open assignment".format(rel)
    return None


# ---------------------------------------------------------------------------------------- leg 4: launchers

def _text_names_python(text):
    """True when text holds an interpreter word or a .py word, wherever a # sits in it."""
    return bool(PYTHON_WORD_RE.search(text) or PY_WORD_RE.search(text))


def _names_python(line):
    """True when a runner line names Python (_text_names_python). Only a whole-line comment is skipped; a # later
    in the line can sit inside a word or a quoted string, where it starts no comment, so the whole line is
    read."""
    if line.lstrip().startswith("#"):
        return False
    return _text_names_python(line)


def runner_first_gate_finding(rel, text, probe_line):
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith('run_gate "'):
            if line.split() != probe_line.split():
                return "{}: the first gate line is {!r}, not the child-status probe {!r}".format(
                    rel, line.strip(), probe_line)
            return None
        if _names_python(line):
            return "{}:{}: a Python launch above the child-status probe".format(rel, number)
    return "{}: no run_gate line".format(rel)


class _OutsideGrammar(Exception):
    """A workflow line the closed grammar does not classify; args are (line index, what the line is)."""


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _next_line(lines, at):
    """The index of the first line from at that is neither blank nor a whole-line comment (outside a literal block
    scalar, a # line is a YAML comment)."""
    while at < len(lines) and (not lines[at].strip() or lines[at].lstrip(" ").startswith("#")):
        at += 1
    return at


def _scalar_ok(value):
    """True when value is a one-line scalar the grammar reads: quoted and closed on its line, a flow sequence of
    plain words, or a plain scalar with no indicator first, no ': ', no ' #' and no space at either end."""
    if value[:1] == "'":
        return bool(SINGLE_QUOTED_RE.fullmatch(value))
    if value[:1] == '"':
        return bool(DOUBLE_QUOTED_RE.fullmatch(value))
    if value[:1] == "[":
        return bool(FLOW_WORDS_RE.fullmatch(value))
    return (bool(value) and value == value.strip() and value[0] not in PLAIN_INDICATORS
            and not PLAIN_BREAK_RE.search(value))


def _block(lines, at, indent):
    """(node, next index) for the block whose first line, lines[at], sits at indent: a sequence when that line is a
    '- ' item, else a mapping."""
    if lines[at][indent:].startswith("- "):
        return _sequence(lines, at, indent)
    return _mapping(lines, at, indent)


def _mapping(lines, at, indent, first=None):
    """(("map", indent, entries), next index) for the key lines at indent from lines[at]; first is the (index,
    text) of a key that shares a '- ' item's line. Each entry is (index, key, value, child): a one-line scalar
    value, a literal block scalar (value "|" and its ("text", None, lines) child), a nested block (value None)
    or nothing."""
    entries, keys = [], set()
    while True:
        if first is not None:
            (index, text), first = first, None
        else:
            at = _next_line(lines, at)
            if at == len(lines) or _indent(lines[at]) < indent:
                return ("map", indent, entries), at
            if _indent(lines[at]) > indent:
                raise _OutsideGrammar(at, "a line deeper than the keys around it (a continued scalar or a misplaced "
                                          "key)")
            index, text, at = at, lines[at][indent:], at + 1
        match = KEY_LINE_RE.fullmatch(text)
        if match is None:
            raise _OutsideGrammar(index, "not a key line at the indentation of the keys around it")
        key, value = match.groups()
        if key in keys:
            raise _OutsideGrammar(index, "a repeated key {}".format(key))
        keys.add(key)
        child = None
        if value is None:
            after = _next_line(lines, at)
            if after < len(lines) and _indent(lines[after]) > indent:
                child, at = _block(lines, after, _indent(lines[after]))
        elif value == "|":
            child, at = _literal(lines, at, indent)
        elif not _scalar_ok(value):
            raise _OutsideGrammar(index, "a value that is not a one-line scalar or a literal block scalar (|)")
        entries.append((index, key, value, child))


def _sequence(lines, at, indent):
    """(("seq", indent, items), next index) for the '- ' items at indent from lines[at]: each item is a one-line
    scalar (index, None, value, None) or a mapping whose first key shares its line (index, None, None,
    mapping)."""
    items = []
    while True:
        at = _next_line(lines, at)
        if at == len(lines) or _indent(lines[at]) < indent:
            return ("seq", indent, items), at
        if not lines[at][indent:].startswith("- "):
            raise _OutsideGrammar(at, "not a '- ' item at the indentation of the items around it")
        index, body = at, lines[at][indent + 2:]
        if KEY_LINE_RE.fullmatch(body):
            child, at = _mapping(lines, at + 1, indent + 2, (index, body))
            items.append((index, None, None, child))
        elif _scalar_ok(body):
            items.append((index, None, body, None))
            at += 1
        else:
            raise _OutsideGrammar(index, "an item that is not a one-line scalar or a mapping")


def _literal(lines, at, indent):
    """(("text", None, lines), next index) for a literal block scalar whose key sits at indent: every following
    line that is blank or deeper, each an (index, line) read whole as text (a # line in it is text, not a
    comment), at one indentation set by its first non-blank line."""
    text, depth = [], None
    while at < len(lines):
        line = lines[at]
        if line.strip():
            if _indent(line) <= indent:
                break
            if depth is None:
                depth = _indent(line)
            elif _indent(line) < depth:
                raise _OutsideGrammar(at, "a literal block scalar line less indented than its first line")
        text.append((at, line))
        at += 1
    return ("text", None, text), at


def read_workflow(text):
    """The workflow's top-level ("map", 0, entries), every line read under the closed grammar; raises
    _OutsideGrammar naming the first line the grammar does not classify."""
    return _mapping(text.splitlines(), 0, 0)[0]


def _values(node):
    """(line index, text) of every value under node: each one-line scalar, and each line of a literal block
    scalar."""
    kind, _depth, body = node
    if kind == "text":
        return list(body)
    out = []
    for index, _key, value, child in body:
        if child is not None:
            out.extend(_values(child))
        elif value is not None:
            out.append((index, value))
    return out


def _workflow_jobs(top):
    """[(job id, job entries, steps)] under the top-level jobs: key, each step the entries of one '- ' item;
    raises _OutsideGrammar where jobs: does not hold a mapping at two spaces, a job is not a mapping, or steps:
    does not hold '- ' items that are each a mapping."""
    out = []
    for index, key, _value, child in top[2]:
        if key != "jobs":
            continue
        if child is None or child[0] != "map" or child[1] != JOBS_INDENT:
            raise _OutsideGrammar(child[2][0][0] if child is not None and child[2] else index,
                                  "jobs: does not hold a mapping of jobs at two spaces")
        for job_index, job, _job_value, body in child[2]:
            if body is None or body[0] != "map":
                raise _OutsideGrammar(job_index, "job {} does not hold a mapping".format(job))
            steps = []
            for steps_index, job_key, _steps_value, node in body[2]:
                if job_key != "steps":
                    continue
                if node is None or node[0] != "seq" or any(item[3] is None for item in node[2]):
                    raise _OutsideGrammar(steps_index, "steps: does not hold '- ' items that are each a mapping")
                steps = [item[3][2] for item in node[2]]
            out.append((job, body[2], steps))
    return out


def _step_pairs(step):
    return tuple((key, value) for _index, key, value, _child in step)


def workflow_findings(rel, text):
    """Findings for one workflow. A workflow outside the closed grammar (read_workflow, _workflow_jobs) is one
    finding, naming the first line the grammar does not classify, and nothing else in it is placed. Inside it,
    every value that names Python must sit in a step with a run: key after its job's one setup-python step and
    the probe step right after that, which is exactly PROBE_STEP; in a HOST_PATCH_JOBS job of the workflow
    HOST_PATCH_WORKFLOW only, the step right after setup-python may instead be exactly HOST_PIN_STEP, with
    exactly HOST_PROBE_STEP right after it; the
    job carries no continue-on-error, and every
    interpreter word is one of PLAIN_INTERPRETERS."""
    try:
        top = read_workflow(text)
        jobs = _workflow_jobs(top)
    except _OutsideGrammar as exc:
        return ["{}:{}: outside the workflow grammar the gate reads: {}".format(rel, exc.args[0] + 1, exc.args[1])]
    named = [(index, value) for index, value in _values(top) if _text_names_python(value)]
    out = ["{}:{}: names the interpreter {}, not the python3 or python setup-python puts on PATH".format(
        rel, index + 1, word) for index, value in named for word in PYTHON_WORD_RE.findall(value)
        if word not in PLAIN_INTERPRETERS]
    placed = set()
    for job, entries, steps in jobs:
        step_named = [[index for index, value in _values(("map", None, step)) if _text_names_python(value)]
                      for step in steps]
        placed.update(index for indexes in step_named for index in indexes)
        if not any(step_named):
            continue
        setups = [position for position, step in enumerate(steps) if any(
            key == "uses" and (value or "").startswith(SETUP_PYTHON_USES) for _index, key, value, _child in step)]
        if len(setups) != 1:
            out.append("{}: job {} launches Python with {} setup-python steps, not one".format(rel, job, len(setups)))
            continue
        probe = setups[0] + 1
        pin, want_probe = None, PROBE_STEP
        if (rel == HOST_PATCH_WORKFLOW and job in HOST_PATCH_JOBS and probe < len(steps)
                and _step_pairs(steps[probe]) == HOST_PIN_STEP):
            pin, probe, want_probe = probe, probe + 1, HOST_PROBE_STEP
        if probe == len(steps) or _step_pairs(steps[probe]) != want_probe:
            out.append("{}: job {}: the step right after {} is not the child-status probe step exactly "
                       "(its name, shell: bash and run, and no other key)".format(
                           rel, job, "setup-python" if pin is None else "the Python host pin step"))
            continue
        if any(key == "continue-on-error" for _index, key, _value, _child in entries):
            out.append("{}: job {} launches Python and carries continue-on-error, which would mask a probe "
                       "failure".format(rel, job))
        for position, indexes in enumerate(step_named):
            if position < probe and position != pin:
                out.extend("{}:{}: job {}: names Python before the child-status probe step".format(
                    rel, index + 1, job) for index in indexes)
            elif position > probe and not any(key == "run" for _index, key, _value, _child in steps[position]):
                out.extend("{}:{}: job {}: names Python in a step with no run: key".format(rel, index + 1, job)
                           for index in indexes)
    out.extend("{}:{}: names Python outside the steps of every job".format(rel, index + 1)
               for index, _value in named if index not in placed)
    return out


def workflow_targets(text):
    """The .py files a workflow launches through an interpreter word, read in every value the grammar reads (a #
    inside a word and a literal block scalar's # line included); a workflow outside the grammar, already a
    finding, is read whole, line by line."""
    try:
        values = [value for _index, value in _values(read_workflow(text))]
    except _OutsideGrammar:
        values = text.splitlines()
    return {match.group(1) for value in values for match in PY_LAUNCH_RE.finditer(value)}


def _single_binding(lines, pattern, forms):
    """The form of the one assignment pattern finds on lines, or None (none, several, or another form; only
    a trailing || clause with no assignment in it may follow the form)."""
    found = [line.strip() for line in lines if pattern.search(line)]
    if len(found) != 1:
        return None
    form = next((form for form in forms if found[0].startswith(form)), None)
    rest = found[0][len(form):] if form is not None else None
    if rest is None or not (rest == "" or (rest.startswith(" || ") and "=" not in rest)):
        return None
    return form


def binds_here(text):
    """True when a launcher's non-comment lines bind here, once, to the launcher's own directory."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    form = _single_binding(lines, HERE_ASSIGN_RE, HERE_BINDINGS)
    if form == HERE_BINDINGS[1]:
        return _single_binding(lines, DIR_ASSIGN_RE, (DIR_BINDING,)) is not None
    return form is not None


def resolve_shell_word(launcher, word, here_bound):
    """The repository path a launcher's .py word names, as the launcher resolves it, or None: outside the
    repository or not resolvable."""
    default = SHELL_DEFAULT_RE.match(word)
    if default:
        word = word[default.end():]
    base = ""
    for prefix in HERE_PREFIXES:
        if word.startswith(prefix):
            if not here_bound:
                return None
            word, base = word[len(prefix):], posixpath.dirname(launcher)
            break
    if not word or word.startswith("/") or any(char in word for char in "${}"):
        return None
    path = posixpath.normpath(posixpath.join(base, word))
    if path == ".." or path.startswith("../"):
        return None
    return path


def shell_findings(rel, text, self_rels):
    """Every .py word on a non-comment line of a shell launcher must resolve to the exact repository path of a
    self-guarded entry."""
    here_bound = binds_here(text)
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for word in PY_WORD_RE.findall(line):
            target = resolve_shell_word(rel, word, here_bound)
            if target is None:
                out.append("{}:{}: launches {}, which does not resolve to a path inside the repository".format(
                    rel, number, word))
            elif target not in self_rels:
                out.append("{}:{}: launches {}, resolved to {}, which is not a self-guarded entry".format(
                    rel, number, word, target))
    return out


def hook_registration_findings(text, handlers, events):
    """Findings for the plugin's hooks.json: each command is exactly HOOK_COMMAND with HOOK_ARGS and one mode,
    and that mode is a HANDLERS key with a HANDLER_EVENT entry."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CannotEvaluate("{}: malformed ({})".format(HOOKS_JSON_REL, type(exc).__name__))
    out, commands = [], []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type") == "command":
                commands.append(node)
            else:
                stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if not commands:
        raise CannotEvaluate("{}: no hook command".format(HOOKS_JSON_REL))
    for command in commands:
        args = command.get("args")
        if command.get("command") != HOOK_COMMAND:
            out.append("{}: a hook command runs {!r}, not exactly {!r}".format(
                HOOKS_JSON_REL, command.get("command"), HOOK_COMMAND))
            continue
        if not (isinstance(args, list) and len(args) == len(HOOK_ARGS) + 1 and args[:-1] == HOOK_ARGS
                and isinstance(args[-1], str)):
            out.append("{}: a hook command's args are {!r}, not exactly {!r} and one mode".format(
                HOOKS_JSON_REL, args, HOOK_ARGS))
            continue
        mode = args[-1]
        if mode not in handlers or mode not in events:
            out.append("{}: hook mode {!r} is not a HANDLERS key with a HANDLER_EVENT entry (unclassified)".format(
                HOOKS_JSON_REL, mode))
    return out


def _dict_keys(tree, name):
    nodes = _top_definitions(tree, name)
    if len(nodes) != 1 or not isinstance(nodes[0].value, ast.Dict):
        raise CannotEvaluate("{}: no single literal {} table".format(HOOK_COPIES[0], name))
    return {key.value for key in nodes[0].value.keys if isinstance(key, ast.Constant)}


# ------------------------------------------------------------------------------------ leg 5: completeness

def _modules_key(node):
    """The literal string key of a modules[...] subscript (sys.modules['os']), or None."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "modules"
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
        return node.slice.value
    return None


def _spells_os(node, os_names):
    """True when node spells the os or posix module statically: a name bound to it, an attribute named os or
    posix (os.path.os) or a literal sys.modules subscript (sys.modules['os'])."""
    if isinstance(node, ast.Name):
        return node.id in os_names
    if isinstance(node, ast.Attribute):
        return node.attr in OS_MODULES
    return _modules_key(node) in OS_MODULES


def module_reads(tree):
    """(reads, dynamic loads, imports) of one module: reads as sorted names, dynamic loads as the sorted exact
    text (ast.unparse) of each load call, imports as (module name, level) pairs, imports inside functions
    included."""
    os_names, load_names = set(OS_MODULES), set(DYNAMIC_NAMES | DYNAMIC_ATTRS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names.update(alias.asname for alias in node.names if alias.name in OS_MODULES and alias.asname)
        elif isinstance(node, ast.ImportFrom):
            load_names.update(alias.asname for alias in node.names if alias.name in DYNAMIC_ATTRS and alias.asname)
    reads, dynamic, imports = set(), set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in SPAWN_MODULES or alias.name in SPAWN_SUBMODULES:
                    reads.add("import " + alias.name)
                imports.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (module.split(".")[0] in SPAWN_MODULES or module in SPAWN_SUBMODULES):
                reads.add("from " + module)
            for alias in node.names:
                os_spawn = alias.name == "*" or OS_SPAWN_RE.fullmatch(alias.name)
                if node.level == 0 and module in OS_MODULES and os_spawn:
                    reads.add(module + "." + alias.name)
                if node.level == 0 and module.split(".")[0] in SPAWN_NAME_PACKAGES and alias.name in SPAWN_NAMES:
                    reads.add(alias.name)
                if not module:
                    imports.append((alias.name, node.level))
            if module:
                imports.append((module, node.level))
        elif isinstance(node, ast.Attribute):
            if _spells_os(node.value, os_names) and OS_SPAWN_RE.fullmatch(node.attr):
                reads.add("os." + node.attr)
            if node.attr in SPAWN_NAMES:
                reads.add(node.attr)
        elif isinstance(node, ast.Subscript):
            key = _modules_key(node)
            if key is not None and (key.split(".")[0] in SPAWN_MODULES or key in SPAWN_SUBMODULES):
                reads.add("sys.modules[{!r}]".format(key))
        elif isinstance(node, ast.Name):
            if node.id in SPAWN_NAMES:
                reads.add(node.id)
        elif isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id in load_names:
                dynamic.add(ast.unparse(node))
            elif isinstance(callee, ast.Attribute) and callee.attr in DYNAMIC_ATTRS:
                dynamic.add(ast.unparse(node))
    return sorted(reads), sorted(dynamic), imports


def resolve_import(rel, name, level, exists):
    """The repository-relative file a local import names, or None (a standard-library or unresolved name)."""
    first = name.split(".")[0] if name else ""
    if not first or (level == 0 and first in sys.stdlib_module_names):
        return None
    if level:
        base = Path(rel).parent
        for _up in range(level - 1):
            base = base.parent
        bases = [base.as_posix()]
    else:
        bases = [Path(rel).parent.as_posix()] + list(LOCAL_DIRS)
    for base in bases:
        prefix = "" if base in ("", ".") else base + "/"
        for candidate in (prefix + first + ".py", prefix + first + "/__init__.py"):
            if exists(candidate):
                return candidate
    return None


def closure_reads(rel, load, exists, reviewed=None):
    """(reads, dynamic loads) of rel and its local import closure, each as sorted (file, name or call text)
    pairs. A file whose load calls, by exact call text (and so by target where the target is a literal), are
    exactly its reviewed set (DYNAMIC_LOADS_REVIEWED) has them followed as imports of the files its row names
    instead."""
    reviewed = {} if reviewed is None else reviewed
    seen, stack, reads, dynamic = set(), [rel], set(), set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        found, loads, imports = module_reads(load(current))
        reads.update((current, name) for name in found)
        if loads and current in reviewed and tuple(loads) == reviewed[current][0]:
            stack.extend(reviewed[current][1])
        else:
            dynamic.update((current, name) for name in loads)
        for name, level in imports:
            target = resolve_import(current, name, level, exists)
            if target is not None:
                stack.append(target)
    return sorted(reads), sorted(dynamic)


def classify(entries, launched, reads_of, residual, direct_check):
    """(classes, findings, cannot): classes maps each entry to SELF, NOT-APPLICABLE, LAUNCHER or RESIDUAL."""
    classes, findings, cannot = {}, [], []
    for rel in sorted(entries):
        if rel in SELF_ENTRIES:
            classes[rel] = "SELF"
            continue
        reads, dynamic = reads_of(rel)
        if not reads:
            if dynamic:
                cannot.append("{}: no child-status read found, but it loads code at run time ({}); "
                              "cannot classify".format(rel, ", ".join(sorted({name for _f, name in dynamic}))))
                continue
            classes[rel] = "NOT-APPLICABLE"
            continue
        classes[rel] = "LAUNCHER" if rel in launched else "RESIDUAL"
    listed = set(residual)
    if list(residual) != sorted(listed):
        findings.append("DIRECT_LAUNCH_RESIDUAL is not a sorted tuple of unique entries")
    for rel, kind in sorted(classes.items()):
        if kind not in ("LAUNCHER", "RESIDUAL"):
            continue
        if direct_check:
            findings.append("{}: reads a child's exit status and does not refuse a direct launch itself "
                            "(DIRECT_LAUNCH_CHECK is on)".format(rel))
        elif rel not in listed:
            findings.append("{}: a new entry point that reads a child's exit status ({}) with no guard of its own; "
                            "guard it or list it in DIRECT_LAUNCH_RESIDUAL".format(
                                rel, "reached through a guarded launcher" if kind == "LAUNCHER"
                                else "no declared launcher"))
    for rel in sorted(listed):
        if rel not in classes:
            findings.append("{}: listed in DIRECT_LAUNCH_RESIDUAL but no longer an entry point (stale)".format(rel))
        elif classes[rel] in ("SELF", "NOT-APPLICABLE"):
            findings.append("{}: listed in DIRECT_LAUNCH_RESIDUAL but now {} (stale)".format(rel, classes[rel]))
    return classes, findings, cannot


# ------------------------------------------------------------------------------------------------ evaluate

def _shell_launchers(root, listdir=os.listdir, walk=os.walk, lexists=os.path.lexists):
    """(launchers, findings): tools/*.sh, every *.sh under opf/ and the declared EXTRA_SHELL_LAUNCHERS, the two
    runners aside. A directory that cannot be listed or walked is cannot-evaluate; a declared launcher that is
    absent is a finding."""
    def _fail(exc):
        raise CannotEvaluate("cannot walk opf/ for shell launchers: {}".format(exc))

    out = ["tools/" + name for name in _listdir(root, "tools", listdir) if name.endswith(".sh")]
    for dirpath, dirnames, filenames in walk(Path(root) / "opf", onerror=_fail):
        dirnames[:] = sorted(name for name in dirnames if name not in check_python_floor.SKIPPED_DIR_NAMES)
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        out.extend(rel_dir + "/" + name for name in sorted(filenames) if name.endswith(".sh"))
    findings = []
    for rel in EXTRA_SHELL_LAUNCHERS:
        if lexists(Path(root) / rel):
            out.append(rel)
        else:
            findings.append("{}: a declared shell launcher (EXTRA_SHELL_LAUNCHERS) that does not exist".format(rel))
    return [rel for rel in out if rel not in (LOCAL_RUNNER, OPF_RUNNER)], findings


def evaluate(root, residual=None, direct_check=None):
    """(classes, findings); raises CannotEvaluate naming what could not be read or classified."""
    root = Path(root)
    residual = DIRECT_LAUNCH_RESIDUAL if residual is None else residual
    direct_check = DIRECT_LAUNCH_CHECK if direct_check is None else direct_check
    findings, launched = [], set()
    canonical = _parse(_read(root, CANONICAL_REL), CANONICAL_REL)
    tmpl = template(canonical)
    finding = cli_entry_finding(CANONICAL_REL, canonical, Path(CANONICAL_REL).name, exact=False)
    findings.extend([finding] if finding else [])
    findings.extend(cli_copy_findings(lambda rel: _parse(_read(root, rel), rel), tmpl))
    for rel, name in ENTRY_COPIES:
        tree = _parse(_read(root, rel), rel)
        findings.extend(copy_findings(rel, tree, tmpl, CANONICAL_NAMES))
        finding = entry_function_finding(rel, tree, name)
        findings.extend([finding] if finding else [])
    for rel, name in SELF_TEST_FIRST_COPIES:
        tree = _parse(_read(root, rel), rel)
        findings.extend(copy_findings(rel, tree, tmpl, CANONICAL_NAMES))
        finding = self_test_first_finding(rel, tree, name)
        findings.extend([finding] if finding else [])
    hook_trees = []
    for rel in HOOK_COPIES:
        tree = _parse(_read(root, rel), rel)
        hook_trees.append(tree)
        findings.extend(copy_findings(rel, tree, tmpl, HOOK_NAMES))
        finding = hook_block_finding(rel, tree)
        findings.extend([finding] if finding else [])

    for rel, probe_line in RUNNER_PROBES:
        text = _text(root, rel)
        finding = runner_first_gate_finding(rel, text, probe_line)
        findings.extend([finding] if finding else [])
        if rel == OPF_RUNNER:
            text, diagnostic = check_ci_parity.adapt_standalone_runner(text)
            if diagnostic is not None:
                raise CannotEvaluate("{}: {}".format(rel, diagnostic.message))
        extraction = check_ci_parity.extract_local(text)
        if extraction.diagnostics:
            raise CannotEvaluate("{}:{}: {}".format(rel, extraction.diagnostics[0].line,
                                                    extraction.diagnostics[0].message))
        launched.update(member.split(" ", 1)[0] for member in extraction.members
                        if member.split(" ", 1)[0].endswith(".py"))
    workflows = [name for name in _listdir(root, WORKFLOWS_REL) if name.endswith((".yml", ".yaml"))]
    if not workflows:
        raise CannotEvaluate("{}: no workflow".format(WORKFLOWS_REL))
    for name in workflows:
        rel = WORKFLOWS_REL + "/" + name
        text = _text(root, rel)
        findings.extend(workflow_findings(rel, text))
        launched.update(workflow_targets(text))
    launched.update(check_entry_guard.registry_runners(root))
    hooks_text = _decoded(_read(root, HOOKS_JSON_REL), HOOKS_JSON_REL)
    for tree in hook_trees:
        findings.extend(hook_registration_findings(hooks_text, _dict_keys(tree, "HANDLERS"),
                                                   _dict_keys(tree, "HANDLER_EVENT")))
    launchers, missing = _shell_launchers(root)
    findings.extend(missing)
    for rel in launchers:
        findings.extend(shell_findings(rel, _decoded(_read(root, rel), rel), SELF_ENTRIES))

    for rel in sorted(launched):
        if not (root / rel).is_file():
            findings.append("{}: a declared launch target that does not exist (unclassified)".format(rel))
    launched = {rel for rel in launched if (root / rel).is_file()}
    try:
        entries = set(check_python_floor.shipped_entrypoints(root)) | launched
    except check_python_floor.CannotEvaluate as exc:
        raise CannotEvaluate(str(exc))
    trees = {}

    def load(rel):
        if rel not in trees:
            trees[rel] = _parse(_read(root, rel), rel)
        return trees[rel]

    reviewed = {rel: (names, targets) for rel, names, targets, _what in DYNAMIC_LOADS_REVIEWED}
    for rel, (names, targets) in sorted(reviewed.items()):
        if not (root / rel).is_file() or tuple(module_reads(load(rel))[1]) != names:
            findings.append("{}: its DYNAMIC_LOADS_REVIEWED entry no longer matches the load calls in it "
                            "(stale)".format(rel))
        findings.extend("{}: DYNAMIC_LOADS_REVIEWED names {}, which does not exist".format(rel, target)
                        for target in targets if not (root / target).is_file())

    def reads_of(rel):
        return closure_reads(rel, load, lambda candidate: (root / candidate).is_file(), reviewed)

    classes, more, cannot = classify(entries, launched, reads_of, residual, direct_check)
    if cannot:
        raise CannotEvaluate("; ".join(cannot))
    return classes, findings + more


def run(root):
    try:
        classes, findings = evaluate(root)
    except CannotEvaluate as exc:
        print("error: cannot evaluate child-status coverage ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    counts = {kind: sum(1 for value in classes.values() if value == kind)
              for kind in ("SELF", "LAUNCHER", "RESIDUAL", "NOT-APPLICABLE")}
    for rel, kind in sorted(classes.items()):
        if kind in ("LAUNCHER", "RESIDUAL"):
            print("RESIDUAL: {} ({}; a direct launch from an ignoring parent is unguarded until the floor prelude "
                  "refuses)".format(rel, "reached through a guarded launcher" if kind == "LAUNCHER"
                                    else "no declared launcher"))
    summary = ("{} entry points: {} self-guarded, {} reached through a guarded launcher, {} with no declared "
               "launcher, {} with no child-status read found".format(
                   len(classes), counts["SELF"], counts["LAUNCHER"], counts["RESIDUAL"], counts["NOT-APPLICABLE"]))
    if findings:
        print("FAIL: child-status-coverage: {} finding(s); {}".format(len(findings), summary))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: child-status-coverage: {}; every probe placement, inline copy and entry holds".format(summary))
    return 0


def _canonical_module(root=ROOT):
    spec = importlib.util.spec_from_file_location("child_status_canonical", Path(root) / CANONICAL_REL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------------- self-test

COVERAGE_SUITE = "child-status-coverage-selftest"
PROBE_SUITE = "child-status-selftest"
README_REL = "README.md"
# The real shell launchers that launch a .py file, each through $here.
SHELL_REAL = ("opf/enforcement/ci/opf-ci.sh", "opf/enforcement/precommit/pre-commit")
# The root of the in-memory launcher tree the walk vectors read; nothing is read from it.
FAKE_ROOT = Path("/nonexistent-child-status-coverage-root")
# One module-scope rebinding per binding form the count reads: (form, the statement with {0} for the name, the
# form the finding names). Each is placed after every definition in tools/check_opf_declaration.py and is a
# finding for _entry and for _child_status_refusal_exit alike; the top-level-only count they replace accepted
# each one.
REBINDINGS = (
    ("tuple", "{0}, _other = main, None\n", "an assignment"),
    ("starred", "*{0}, = (main,)\n", "an assignment"),
    ("if", "if True:\n    {0} = main\n", "an assignment"),
    ("try", "try:\n    {0} = main\nexcept Exception:\n    pass\n", "an assignment"),
    ("while", "while True:\n    {0} = main\n    break\n", "an assignment"),
    ("with", "with open(__file__) as {0}:\n    pass\n", "a with-as target"),
    ("for", "for {0} in (main,):\n    pass\n", "a for target"),
    ("walrus", "({0} := main)\n", "a walrus (:=)"),
    ("except-as", "try:\n    pass\nexcept Exception as {0}:\n    pass\n", "an except-as name"),
    ("match", "match main:\n    case {0}:\n        pass\n", "a match capture"),
    ("import", "import json as {0}\n", "an import"),
    ("from-import", "from json import loads as {0}\n", "a from-import"),
    ("global", "def _rebind():\n    global {0}\n    {0} = main\n", "a global declaration"),
    ("del", "del {0}\n", "a del"),
    ("attribute-store", "{0}.__code__ = main.__code__\n", "a store the walk cannot classify ({0}.__code__)"),
    ("namespace-store", 'globals()["{0}"] = main\n', "a store the walk cannot classify (globals()['{0}'])"))
# The decorated form, swapped in place: (tree key, the def line, the decorated def line).
DECORATED = (("entry", "\ndef _entry():\n", "\n@(lambda function: main)\ndef _entry():\n"),
             ("guard", "\ndef _child_status_refusal_exit(name):\n",
              "\n@(lambda function: lambda name: 0)\ndef _child_status_refusal_exit(name):\n"))
# Bindings of _entry and of the guard function that are not at module scope (a function's locals, a class body,
# a comprehension's own targets): the clean control, a finding only for a count that reads too much.
LOCAL_BINDINGS = ("def _local_bindings():\n    _entry = _child_status_refusal_exit = None\n"
                  "    return _entry, _child_status_refusal_exit\n\n\n"
                  "class _ClassBindings:\n    _entry = _child_status_refusal_exit = None\n\n\n"
                  "_comprehension = [(_entry, _child_status_refusal_exit)\n"
                  "                  for _entry, _child_status_refusal_exit in ()]\n")
# The process-creation audit: the driver runs the script named by its first argument as __main__, with the rest
# as its arguments, under an audit hook that records every launch event (the events os.fork, os.forkpty,
# os.posix_spawn and os.posix_spawnp, subprocess.Popen, os.system, os.exec* and os.spawn* raise), and prints
# them with the script's exit status as its last stdout line. The control script launches through each audited
# API (os.spawnv raises os.fork on POSIX; the exec names a path that does not exist, so it fails after its event).
AUDIT_DRIVER = '''import json
import runpy
import sys

LAUNCHES = ("os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn", "os.startfile", "os.system",
            "subprocess.Popen")
seen = []


def _audit(event, args):
    if event in LAUNCHES:
        seen.append(event)


sys.addaudithook(_audit)
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
    code = 0
except SystemExit as exc:
    code = exc.code
print("AUDITED " + json.dumps({"code": code, "launches": seen}))
'''
AUDIT_CONTROL = '''import os
import subprocess
import sys

child = [sys.executable, "-I", "-B", "-c", "pass"]
pid = os.fork()
if pid == 0:
    os._exit(0)
os.waitpid(pid, 0)
os.waitpid(os.posix_spawn(sys.executable, child, dict(os.environ)), 0)
subprocess.run(child, check=True)
os.system("exit 0")
os.spawnv(os.P_WAIT, sys.executable, child)
try:
    os.execv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "absent", "program"), ["program"])
except OSError:
    pass
'''
AUDIT_CONTROL_LAUNCHES = ("os.exec", "os.fork", "os.posix_spawn", "os.system", "subprocess.Popen")


def _registered_check_ids(suite):
    try:
        data = tomllib.loads(_read(ROOT, check_entry_guard.REGISTRY_REL).decode("utf-8"))
    except (CannotEvaluate, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError,
            MemoryError) as exc:
        return None, "{}: {}".format(check_entry_guard.REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, check_entry_guard.REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == suite), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(check_entry_guard.REGISTRY_REL, suite)
    return set(row["expected-check-ids"]), None


def _swap(text, old, new):
    if text.count(old) != 1:
        raise CannotEvaluate("self-test anchor found {} times: {!r}".format(text.count(old), old[:60]))
    return text.replace(old, new)


def _hook_edit(text, field, edit):
    """hooks.json with one field of its first registered command replaced by edit(old value)."""
    data = json.loads(text)
    command = data["hooks"]["PreToolUse"][0]["hooks"][0]
    command[field] = edit(command[field])
    return json.dumps(data)


def _inputs():
    """The real inputs, read once, and their single-edit departures, parsed once."""
    def text(rel):
        return _read(ROOT, rel).decode("utf-8")
    real = {rel: text(rel) for rel in (CANONICAL_REL, HOOK_COPIES[0], LOCAL_RUNNER, OPF_RUNNER, HOOKS_JSON_REL,
                                       WORKFLOWS_REL + "/quality.yml") + tuple(rel for rel, _ in CLI_COPIES)
            + tuple(rel for rel, _ in ENTRY_COPIES + SELF_TEST_FIRST_COPIES) + SHELL_REAL}
    opf, selftest, hook = real["opf/tools/opf.py"], real["tools/check_selftest_execution.py"], real[HOOK_COPIES[0]]
    declaration, opf_declaration = real["tools/check_opf_declaration.py"], real["opf/tools/_opf_declaration.py"]
    trees = {"canonical": ast.parse(real[CANONICAL_REL]), "opf": ast.parse(opf), "selftest": ast.parse(selftest),
             "hook": ast.parse(hook), "declaration": ast.parse(declaration),
             "opf-declaration": ast.parse(opf_declaration)}
    opf_entry = CLI_ENTRY.format("opf.py")
    declaration_guard = "    " + ENTRY_GUARD.format("check_opf_declaration.py") + "\n"
    for key, source in (
            ("opf-drift", _swap(opf, "    if waited != pid or", "    if waited == pid or")),
            ("selftest-missing", _swap(selftest, "def _child_status_refusal_exit(name):",
                                       "def _child_status_refusal_exit_gone(name):")),
            ("hook-drift", _swap(hook, "_CHILD_STATUS_PROBE_CODE = 77", "_CHILD_STATUS_PROBE_CODE = 78")),
            ("opf-entry-bare", _swap(opf, "    " + opf_entry + "\n", "    sys.exit(main())\n")),
            ("opf-entry-extra", _swap(opf, "    " + opf_entry + "\n", "    print('entry')\n    " + opf_entry + "\n")),
            ("hook-block-open", _swap(hook, "            return 2\n        signal.signal(signal.SIGCHLD,",
                                      "            return 0\n        signal.signal(signal.SIGCHLD,")),
            ("hook-block-gone", _swap(hook, "    unobservable = _child_status_refusal()\n", "    unobservable = None\n")),
            ("declaration-guard-gone", _swap(declaration, declaration_guard, "")),
            ("declaration-guard-late", _swap(declaration, declaration_guard, "    pass\n" + declaration_guard)),
            ("declaration-entry-rebound", _swap(declaration, '\nif __name__ == "__main__":\n',
                                                '\n_entry = main\n\nif __name__ == "__main__":\n')),
            ("declaration-entry-bare", _swap(declaration, "\n    _entry()\n", "\n    main()\n")),
            ("declaration-entry-extra", _swap(declaration, "\n    _entry()\n", "\n    print('entry')\n    _entry()\n")),
            ("declaration-entry-parameter", _swap(declaration, "\ndef _entry():\n",
                                                  "\ndef _entry(_child_status_refusal_exit=lambda name: 0):\n")),
            ("declaration-guard-other-name", _swap(declaration, declaration_guard,
                                                   "    " + ENTRY_GUARD.format("opf.py") + "\n")),
            ("opf-declaration-guard-gone", _swap(opf_declaration, CLI_ENTRY.format("_opf_declaration.py"),
                                                 "sys.exit(main())")),
            ("opf-declaration-extra", _swap(opf_declaration, CLI_ENTRY.format("_opf_declaration.py"),
                                            "print('entry')\n    " + CLI_ENTRY.format("_opf_declaration.py"))),
            ("opf-declaration-widened", _swap(opf_declaration, 'if sys.argv[1:] == ["--self-test"]:',
                                              'if sys.argv[1:2] == ["--self-test"]:')),
            ("local-bindings", _swap(declaration, '\nif __name__ == "__main__":\n',
                                     "\n" + LOCAL_BINDINGS + '\nif __name__ == "__main__":\n'))):
        trees[key] = ast.parse(source)
    for form, statement, _named in REBINDINGS:
        for key, name in (("entry", "_entry"), ("guard", "_child_status_refusal_exit")):
            trees["{}-rebound-{}".format(key, form)] = ast.parse(_swap(
                declaration, '\nif __name__ == "__main__":\n',
                "\n" + statement.format(name) + '\nif __name__ == "__main__":\n'))
    for key, old, new in DECORATED:
        trees[key + "-rebound-decorated"] = ast.parse(_swap(declaration, old, new))
    # A self_test module whose __main__ block starts with the CLI entry line (no canonical --self-test statement
    # first), and one whose self_test() lost its guard call.
    state_rel = "opf/tools/_opf_adopt_state.py"
    state, state_guard = real[state_rel], "    " + SELF_TEST_GUARD.format("_opf_adopt_state.py") + "\n"
    trees.update((rel, ast.parse(real[rel])) for rel, _name in CLI_COPIES)
    trees["state-entry-bare"] = ast.parse(_swap(state, "    " + SELF_TEST_ENTRY.replace("\n    ", "\n        "), ""))
    trees["state-guard-gone"] = ast.parse(_swap(state, state_guard, ""))
    # A self_test() the entry calls that is not the def starting with the guard: the def decorated, or the name
    # bound again after it by an assignment, a lambda or an import; a CLI copy whose guard name an import binds
    # again after its copy; a plain CLI copy with a bare entry; and opf.py's _ENTRY_STATEMENT drifted.
    main_line = '\nif __name__ == "__main__":\n'
    trees["state-decorated"] = ast.parse(_swap(state, "\ndef self_test():\n", "\n@_wrapped\ndef self_test():\n"))
    for key, binding in (("state-rebound-assign", "self_test = main\n"),
                         ("state-rebound-lambda", "self_test = lambda: 0\n"),
                         ("state-rebound-import", "from _opf_adopt_apply import self_test\n")):
        trees[key] = ast.parse(_swap(state, main_line, "\n" + binding + main_line))
    trees["selftest-guard-import"] = ast.parse(_swap(
        selftest, main_line, "\nfrom operator import not_ as _child_status_refusal_exit\n" + main_line))
    trees["gitleaks-entry-bare"] = ast.parse(_swap(real["tools/check_gitleaks_pin.py"],
                                                   CLI_ENTRY.format("check_gitleaks_pin.py"), "sys.exit(main())"))
    trees["opf-entry-statement-drift"] = ast.parse(_swap(
        opf, "_ENTRY_STATEMENT = 'if sys.argv[1:] == [\"--self-test\"]:",
        "_ENTRY_STATEMENT = 'if sys.argv[1:] == [\"--selftest\"]:"))
    probe_line = RUNNER_PROBES[0][1] + "\n"
    opf_probe = 'run_gate "child-status"                python3 -I -B "$here/_child_status.py"\n'
    quality = real[WORKFLOWS_REL + "/quality.yml"]
    probe_lines = ["{}: {}".format(key, value) for key, value in PROBE_STEP]
    step = "      - " + probe_lines[0] + "\n" + "".join("        " + line + "\n" for line in probe_lines[1:])
    head = "    runs-on: ubuntu-latest\n    steps:\n"
    gate = "      - name: Gate\n        shell: bash\n"
    lone = "jobs:\n  lone:\n" + head + gate
    setup = "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"
    guarded = "jobs:\n  lone:\n" + head + setup + step
    launch = "        run: python3 -I -B tools/check_leaks.py\n"
    pin_lines = ["{}: {}".format(key, value) for key, value in HOST_PIN_STEP]
    pin_step = "      - " + pin_lines[0] + "\n" + "".join("        " + line + "\n" for line in pin_lines[1:])
    host_probe_lines = ["{}: {}".format(key, value) for key, value in HOST_PROBE_STEP]
    host_probe = ("      - " + host_probe_lines[0] + "\n"
                  + "".join("        " + line + "\n" for line in host_probe_lines[1:]))
    # Each a workflow the closed grammar does not classify at one line: an anchor, a merge key, a tag, a folded
    # value, a flow mapping, a repeated key, a tab, a literal line less indented than its first, a document
    # marker, a quoted scalar continued, a step that is not a mapping, and a trailing comment.
    outside_grammar = (
        guarded + gate + "        run: &launch python3 -I -B tools/check_leaks.py\n",
        guarded + "      - <<: *probe\n",
        guarded + gate + "        run: !!str python3 -I -B tools/check_leaks.py\n",
        guarded + gate + "        run: >\n          python3 -I -B tools/check_leaks.py\n",
        "jobs:\n  lone:\n" + head + "      - {uses: actions/setup-python@v5}\n" + step + gate + launch,
        guarded + gate + launch + "        run: python3 -I -B tools/check_leaks.py --self-test\n",
        guarded + gate + "\trun: python3 -I -B tools/check_leaks.py\n",
        guarded + gate + "        run: |\n            echo one\n          python3 -I -B tools/check_leaks.py\n",
        "---\n" + guarded + gate + launch,
        guarded + gate + "        run: 'python3 -I -B\n          tools/check_leaks.py'\n",
        "jobs:\n  lone:\n" + head + "      - python3 -I -B tools/check_leaks.py\n",
        guarded + gate + "        run: python3 -I -B tools/check_leaks.py # a gate\n")
    workflows = tuple((WORKFLOWS_REL + "/" + name, _text(ROOT, WORKFLOWS_REL + "/" + name))
                      for name in _listdir(ROOT, WORKFLOWS_REL) if name.endswith((".yml", ".yaml")))
    here_line = 'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2\n'
    texts = {
        "runner": real[LOCAL_RUNNER], "runner-moved": _swap(real[LOCAL_RUNNER], probe_line, ""),
        "runner-prelude-launch": _swap(real[LOCAL_RUNNER], probe_line,
                                       "python3 -I -B tools/check_leaks.py\n" + probe_line),
        "opf-runner": real[OPF_RUNNER], "opf-runner-moved": _swap(real[OPF_RUNNER], opf_probe, ""),
        "quality": quality, "quality-step-gone": quality.replace(step, "", 1),
        "no-setup": lone + launch,
        "shebang-job": lone + "        run: tools/check_leaks.py\n",
        "no-jobs-line": "on: push\nsteps:\n  - run: python3 -I -B tools/check_leaks.py\n",
        "python-before-setup": lone + launch + setup + step,
        "job-key-indented": "jobs:\n    lone:\n      runs-on: ubuntu-latest\n      steps:\n"
                            "        - run: python3 -I -B tools/check_leaks.py\n",
        "job-key-comment": guarded + gate + launch + "  later:  # a second job\n" + head + gate + launch,
        "interpreter-path-job": lone + "        run: /usr/bin/python3 -m tools.check_leaks\n",
        "interpreter-spelling": guarded + gate + "        run: /usr/bin/python3 -m tools.check_leaks\n" + gate
                                + "        run: python3.14 -I -B tools/check_leaks.py\n",
        "hash-inside-word": lone + "        run: echo a#b; python3 -I -B tools/check_leaks.py\n",
        "probe-in-block-scalar": "jobs:\n  lone:\n" + head + setup + "          note: |\n"
                                 + "".join("            " + line + "\n" for line in ["- " + probe_lines[0]]
                                           + probe_lines[1:]) + gate + launch,
        "probe-if-false": guarded + "        if: false\n" + gate + launch,
        "probe-continued": guarded + "          || true\n" + gate + launch,
        # The order the host-patch jobs of quality.yml run: the Python host pin step right after
        # setup-python, the probe right after it; the same order under any other job id, or under either
        # host-patch job id in any other workflow, is a finding. The departures: the pin step extended by
        # one key (a prefix-only match of HOST_PIN_STEP), the pin followed by the probe named for the plain
        # placement, and the probe ahead of the pin, which this gate accepts (the pin's own placement is
        # the host pin gate's to hold).
        "host-pin-before-probe": "jobs:\n  opf-host-patch:\n" + head + setup + pin_step + host_probe
                                 + gate + launch,
        "host-pin-other-job": "jobs:\n  lone:\n" + head + setup + pin_step + host_probe + gate + launch,
        "host-pin-git-job": "jobs:\n  git-fixture-env-core-host-patch:\n" + head + setup + pin_step + host_probe
                            + gate + launch,
        "host-pin-prefix": "jobs:\n  opf-host-patch:\n" + head + setup + pin_step
                           + "        timeout-minutes: 5\n" + host_probe + gate + launch,
        "host-pin-old-probe": "jobs:\n  opf-host-patch:\n" + head + setup + pin_step + step + gate + launch,
        "host-probe-ahead": "jobs:\n  opf-host-patch:\n" + head + setup + step + pin_step + gate + launch,
        "later-job-key": "jobs:\n  one:\n" + head + setup + step + "   two:\n" + head + gate + launch,
        "targets-hash": guarded + gate + "        run: echo a#b; python3 -I -B tools/child.py\n",
        "block-hash-line": "jobs:\n  lone:\n" + head + gate + "        run: |\n          echo x\\\n"
                           "          #; python3 -I -B tools/check_leaks.py\n" + setup + step,
        "defaults-after-steps": "jobs:\n  lone:\n" + head + "      - run: print('before setup')\n" + setup + step
                                + "    defaults:\n      run:\n        shell: python {0}\n",
        "python-outside-run-step": guarded + "      - uses: actions/github-script@v7\n        with:\n"
                                   "          script: tools/check_leaks.py\n",
        "job-continue-on-error": "jobs:\n  lone:\n    continue-on-error: true\n" + head + setup + step + gate + launch,
        "outside-grammar": outside_grammar, "workflows": workflows,
        "shell-unguarded": "#!/bin/sh\n# python3 tools/opf.py in a comment is not a launch\n"
                           "python3 -I -B tools/check_leaks.py\n",
        "shell-outside": "#!/bin/sh\npython3 -I -B /tmp/opf.py\n" + here_line
                         + 'python3 -I -B "$here/../../../../opf/tools/opf.py"\npython3 -I -B "$OPF_DIR/opf.py"\n',
        "shell-same-name": "#!/bin/sh\n" + here_line + "python3 -I -B tools/opf.py\n"
                           'opf_tool=${OPF_TOOL:-$here/../tools/opf.py}\n',
        "shell-here-unbound": _swap(real[SHELL_REAL[0]], here_line, "here=$(pwd)\n"),
        "shell-dir-unbound": _swap(real[SHELL_REAL[1]], 'dir=$(dirname -- "$0")', "dir=$(pwd)"),
        "hooks": real[HOOKS_JSON_REL],
        "hooks-unregistered": _swap(real[HOOKS_JSON_REL], '"absolute_paths"\n', '"no_such_mode"\n'),
        "hooks-echo": _hook_edit(real[HOOKS_JSON_REL], "command", lambda old: "echo"),
        "hooks-prepended": _hook_edit(real[HOOKS_JSON_REL], "args", lambda old: ["-c", "pass"] + old),
    }
    texts.update((rel, real[rel]) for rel in SHELL_REAL)
    return trees, texts


# A spawn or wait API per line: each, alone in a module, is a child-status read the scan must see.
API_SNIPPETS = ("import subprocess\n", "from subprocess import run\n", "import multiprocessing\n", "import pty\n",
                "import os\nos.fork()\n", "import os as o\no.waitpid(1, 0)\n", "from os import system\n",
                "import os\nos.popen('true')\n", "import os\nos.posix_spawn('/bin/true', [], {})\n",
                "import os\nos.spawnv(0, '/bin/true', [])\n", "import os\nos.execv('/bin/true', [])\n",
                "import asyncio\nasyncio.create_subprocess_exec('true')\n",
                "from asyncio import create_subprocess_shell\n", "import concurrent.futures.process\n",
                "def later():\n    import subprocess\n", "import posix\nposix.fork()\n",
                "import posix as p\np.waitpid(1, 0)\n", "from posix import posix_spawn\n", "from os import *\nfork()\n",
                "import _posixsubprocess\n", "from concurrent.futures import ProcessPoolExecutor\n",
                "import concurrent.futures\nconcurrent.futures.ProcessPoolExecutor()\n",
                "from concurrent.futures import *\nProcessPoolExecutor()\n", "loop.subprocess_exec(None, 'true')\n",
                "loop.subprocess_shell(None, 'true')\n", "from asyncio import *\ncreate_subprocess_exec('true')\n",
                "import os.path\nos.path.os.system('true')\n", "import sys\nsys.modules['os'].system('true')\n",
                "import sys\nsys.modules['subprocess'].run(['true'])\n")
# A run-time load per line: each, alone in a module, is a dynamic load the scan must see.
LOAD_SNIPPETS = ("__import__('subprocess')\n", "import importlib\nimportlib.__import__('subprocess')\n",
                 "import builtins\nbuiltins.__import__('subprocess')\n", "exec('pass')\n", "eval('1')\n",
                 "import importlib\nimportlib.import_module('subprocess')\n", "import runpy\nrunpy.run_path('x.py')\n",
                 "import runpy\nrunpy.run_module('x')\n", "spec.loader.exec_module(module)\n",
                 "loader.load_module('x')\n",
                 "import importlib.util\nimportlib.util.spec_from_file_location('x', 'x.py')\n",
                 "from importlib import import_module\nimport_module('subprocess')\n",
                 "from importlib import import_module as load\nload('subprocess')\n",
                 "import pkgutil\npkgutil.resolve_name('subprocess:run')\n",
                 "import pydoc\npydoc.locate('subprocess.run')\n",
                 "from pydoc import locate\nlocate('subprocess.run')\n")
# A synthetic entry tree for the completeness vectors: (rel, source).
SYNTHETIC = (("tools/new_reader.py", "import helper_mod\n"), ("tools/helper_mod.py", "import subprocess\n"),
             ("tools/plain.py", "import json\n"), ("tools/dynamic.py", "import importlib\nimportlib.import_module(name)\n"),
             ("tools/launched.py", "import os\nos.waitpid(1, 0)\n"),
             ("tools/loader.py", "import runpy\nrunpy.run_path('tools/helper_mod.py')\n"),
             ("tools/loader_moved.py", "import runpy\nrunpy.run_path('tools/plain.py')\n"),
             ("tools/loader_var.py", "import runpy\ntarget = 'tools/helper_mod.py'\nrunpy.run_path(target)\n"),
             ("tools/loader_var_moved.py", "import runpy\ntarget = 'tools/plain.py'\nrunpy.run_path(target)\n"))
# One reviewed row per loader, each naming the load of tools/helper_mod.py: tools/loader_moved.py loads another
# literal target, and tools/loader_var_moved.py assigns another target to the variable its unchanged call reads.
SYNTHETIC_REVIEWED = {rel: (("runpy.run_path('tools/helper_mod.py')",), ("tools/helper_mod.py",))
                      for rel in ("tools/loader.py", "tools/loader_moved.py")}
SYNTHETIC_REVIEWED.update((rel, (("runpy.run_path(target)",), ("tools/helper_mod.py",)))
                          for rel in ("tools/loader_var.py", "tools/loader_var_moved.py"))


def _synthetic_reads(module, reviewed=None):
    trees = {rel: ast.parse(source) for rel, source in SYNTHETIC}

    def reads_of(rel):
        return module.closure_reads(rel, trees.__getitem__, trees.__contains__, reviewed)
    return reads_of


def _fake_fs(unreadable=False, unlistable=False, absent=()):
    """(listdir, walk, lexists) over an in-memory launcher tree under FAKE_ROOT: tools/ holds a.sh and b.py,
    opf/ holds seen.sh, and each declared launcher exists unless absent names it. unreadable makes the walk
    report opf/hidden unreadable, as os.walk does, through onerror only; unlistable makes tools/ unlistable."""
    def listdir(path):
        if unlistable:
            raise PermissionError(13, "Permission denied", str(path))
        return ["b.py", "a.sh"]

    def walk(top, onerror=None):
        yield str(top), [], ["seen.sh"]
        if unreadable and onerror is not None:
            onerror(PermissionError(13, "Permission denied", str(Path(top) / "hidden")))

    def lexists(path):
        return Path(path).relative_to(FAKE_ROOT).as_posix() not in absent
    return listdir, walk, lexists


def _vectors(module, trees, texts, canonical_tree):
    """{vector id: ok} for every static vector, against module (this gate or a mutant of it); a vector whose
    evaluation raises is not ok."""
    m = module
    handlers, events = _dict_keys(trees["hook"], "HANDLERS"), _dict_keys(trees["hook"], "HANDLER_EVENT")
    local_probe, opf_probe = RUNNER_PROBES[0][1], RUNNER_PROBES[1][1]
    reads_of = _synthetic_reads(m)
    entries = {"tools/new_reader.py", "tools/plain.py", "tools/launched.py"}
    launched = {"tools/launched.py"}

    def tmpl():
        return m.template(canonical_tree)

    def copies(key, names):
        return m.copy_findings(key, trees[key], tmpl(), names)

    state_rel, gitleaks_rel = "opf/tools/_opf_adopt_state.py", "tools/check_gitleaks_pin.py"

    def cli_copies(rel, key):
        """The CLI copies' leg 2 and 3 findings, every copy read from its real tree except rel, read from key's."""
        return m.cli_copy_findings(lambda each: trees[key] if each == rel else trees[each], tmpl())

    def raises(thunk):
        """True when thunk is cannot-evaluate; any other exception propagates, so the vector is not ok."""
        try:
            thunk()
        except m.CannotEvaluate:
            return True
        return False

    def shell(rel, key):
        return [finding.split(": ", 1)[1] for finding in m.shell_findings(rel, texts[key], m.SELF_ENTRIES)]

    def hooks(key):
        return m.hook_registration_findings(texts[key], handlers, events)

    def launchers(**fs):
        return m._shell_launchers(FAKE_ROOT, *_fake_fs(**fs))

    every_launcher = ["tools/a.sh", "opf/seen.sh"] + list(m.EXTRA_SHELL_LAUNCHERS)
    reviewed_reads = _synthetic_reads(m, SYNTHETIC_REVIEWED)
    ci_launcher = "opf/enforcement/ci/x.sh"
    outside = "names Python outside the steps of every job"
    grammar = "outside the workflow grammar the gate reads: "
    deeper = grammar + "a line deeper than the keys around it (a continued scalar or a misplaced key)"
    def not_probe_in(rel, job, after="setup-python"):
        return ("{}: job {}: the step right after {} is not the child-status probe step exactly (its "
                "name, shell: bash and run, and no other key)").format(rel, job, after)

    not_probe = not_probe_in("w", "lone")

    def workflow(key):
        return m.workflow_findings("w", texts[key])

    def quality_workflow(key):
        return m.workflow_findings(m.HOST_PATCH_WORKFLOW, texts[key])

    # The five host-patch job ids, written out so that an id added to or dropped from HOST_PATCH_JOBS turns a
    # vector red; each id is held by the host-pin order under it, accepted in quality.yml only.
    host_jobs = ("opf-host-patch", "git-fixture-env-core-host-patch", "git-fixture-env-config-combined-host-patch",
                 "git-fixture-env-config-malformed-host-patch", "git-fixture-env-config-system-host-patch")

    def host_job_held(job):
        text = texts["host-pin-before-probe"].replace("opf-host-patch", job, 1)
        return (not m.workflow_findings(m.HOST_PATCH_WORKFLOW, text)
                and m.workflow_findings("w", text) == [not_probe_in("w", job)])

    vectors = (
        ("copies/real-copies-identical", lambda: not copies("opf", m.CANONICAL_NAMES)
         and not copies("selftest", m.CANONICAL_NAMES) and not copies("hook", m.HOOK_NAMES)),
        ("copies/drift-is-finding", lambda: any("_child_status_refusal differs" in finding
                                                for finding in copies("opf-drift", m.CANONICAL_NAMES))),
        ("copies/missing-copy-is-finding", lambda: any("bound 0 times" in finding
                                                       for finding in copies("selftest-missing", m.CANONICAL_NAMES))),
        ("copies/hook-drift-is-finding", lambda: any("_CHILD_STATUS_PROBE_CODE differs" in finding
                                                     for finding in copies("hook-drift", m.HOOK_NAMES))),
        ("copies/rebound-guard-is-finding", lambda: any("_child_status_refusal_exit is bound 2 times" in finding
                                                        for finding in copies("selftest-guard-import",
                                                                              m.CANONICAL_NAMES))),
        ("entries/real-entries-pass", lambda: m.cli_entry_finding("opf", trees["opf"], "opf.py") is None
         and m.cli_entry_finding("canonical", canonical_tree, "_child_status.py", exact=False) is None
         and m.hook_block_finding("hook", trees["hook"]) is None
         and all(m.self_test_entry_finding(rel, trees[rel], Path(rel).name) is None for rel in m.SELF_TEST_COPIES)
         and not m.cli_copy_findings(trees.__getitem__, tmpl())),
        ("entries/self-test-entry-shape", lambda: m.self_test_entry_finding(
            "state", trees["state-entry-bare"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-guard-missing", lambda: m.self_test_entry_finding(
            "state", trees["state-guard-gone"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-decorated", lambda: m.self_test_entry_finding(
            "state", trees["state-decorated"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-rebound-assign", lambda: m.self_test_entry_finding(
            "state", trees["state-rebound-assign"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-rebound-lambda", lambda: m.self_test_entry_finding(
            "state", trees["state-rebound-lambda"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-rebound-import", lambda: m.self_test_entry_finding(
            "state", trees["state-rebound-import"], "_opf_adopt_state.py") is not None),
        ("entries/self-test-entry-checked", lambda: cli_copies(state_rel, "state-guard-gone") == [
            state_rel + ": its self_test() does not start with " + m.SELF_TEST_GUARD.format("_opf_adopt_state.py")]),
        ("entries/cli-entry-checked", lambda: cli_copies(gitleaks_rel, "gitleaks-entry-bare") == [
            gitleaks_rel + ": its __main__ entry is not exactly " + m.CLI_ENTRY.format("check_gitleaks_pin.py")]),
        ("entries/self-test-entry-pinned", lambda: [finding.split(": ", 1)[0] for finding in cli_copies(
            m.ENTRY_STATEMENT_REL, "opf-entry-statement-drift")] == [m.ENTRY_STATEMENT_REL]),
        ("entries/cli-line-shape", lambda: m.cli_entry_finding("opf", trees["opf-entry-bare"], "opf.py") is not None),
        ("entries/cli-extra-statement",
         lambda: m.cli_entry_finding("opf", trees["opf-entry-extra"], "opf.py") is not None),
        ("entries/hook-block-shape", lambda: m.hook_block_finding("hook", trees["hook-block-open"]) is not None),
        ("entries/hook-block-missing", lambda: m.hook_block_finding("hook", trees["hook-block-gone"]) is not None),
        ("entries/entry-function-real-pass", lambda: m.entry_function_finding(
            "decl", trees["declaration"], "check_opf_declaration.py") is None
         and m.self_test_first_finding("opf-decl", trees["opf-declaration"], "_opf_declaration.py") is None
         and not copies("declaration", m.CANONICAL_NAMES) and not copies("opf-declaration", m.CANONICAL_NAMES)),
        ("entries/self-test-first-guard-missing", lambda: m.self_test_first_finding(
            "opf-decl", trees["opf-declaration-guard-gone"], "_opf_declaration.py") is not None),
        ("entries/entry-function-guard-missing", lambda: m.entry_function_finding(
            "decl", trees["declaration-guard-gone"], "check_opf_declaration.py") is not None),
        ("entries/entry-function-guard-not-first", lambda: m.entry_function_finding(
            "decl", trees["declaration-guard-late"], "check_opf_declaration.py") is not None),
        ("entries/entry-function-rebound", lambda: m.entry_function_finding(
            "decl", trees["declaration-entry-rebound"], "check_opf_declaration.py") is not None),
        ("entries/entry-function-call-shape", lambda: m.entry_function_finding(
            "decl", trees["declaration-entry-bare"], "check_opf_declaration.py") is not None),
        ("entries/entry-function-extra-statement", lambda: m.entry_function_finding(
            "decl", trees["declaration-entry-extra"], "check_opf_declaration.py") is not None),
        ("entries/entry-function-parameter-default", lambda: "takes parameters" in (m.entry_function_finding(
            "decl", trees["declaration-entry-parameter"], "check_opf_declaration.py") or "")),
        ("entries/entry-function-guard-other-name", lambda: m.entry_function_finding(
            "decl", trees["declaration-guard-other-name"], "check_opf_declaration.py") is not None),
        ("entries/entry-rebound-decorated", lambda: "its _entry() is decorated" in (m.entry_function_finding(
            "decl", trees["entry-rebound-decorated"], "check_opf_declaration.py") or "")),
        ("copies/guard-rebound-decorated", lambda: any(
            "the inline copy of _child_status_refusal_exit is decorated" in finding
            for finding in copies("guard-rebound-decorated", m.CANONICAL_NAMES))),
        ("entries/entry-local-bindings-clean", lambda: m.entry_function_finding(
            "decl", trees["local-bindings"], "check_opf_declaration.py") is None
         and not copies("local-bindings", m.CANONICAL_NAMES)),
        ("entries/self-test-first-extra-statement", lambda: m.self_test_first_finding(
            "opf-decl", trees["opf-declaration-extra"], "_opf_declaration.py") is not None),
        ("entries/self-test-first-widened-test", lambda: m.self_test_first_finding(
            "opf-decl", trees["opf-declaration-widened"], "_opf_declaration.py") is not None),
        ("launchers/real-launchers-pass",
         lambda: m.runner_first_gate_finding("runner", texts["runner"], local_probe) is None
         and m.runner_first_gate_finding("opf", texts["opf-runner"], opf_probe) is None
         and not m.workflow_findings(m.HOST_PATCH_WORKFLOW, texts["quality"])
         and not hooks("hooks")
         and all(m.binds_here(texts[rel]) and not m.shell_findings(rel, texts[rel], m.SELF_ENTRIES)
                 for rel in SHELL_REAL)),
        ("launchers/runner-first-gate",
         lambda: m.runner_first_gate_finding("runner", texts["runner-moved"], local_probe) is not None),
        ("launchers/opf-runner-first-gate",
         lambda: m.runner_first_gate_finding("opf", texts["opf-runner-moved"], opf_probe) is not None),
        ("launchers/runner-prelude-launch", lambda: "a Python launch above the child-status probe" in (
            m.runner_first_gate_finding("runner", texts["runner-prelude-launch"], local_probe) or "")),
        ("launchers/workflow-first-step", lambda: any("job secrets:" in finding for finding in m.workflow_findings(
            m.HOST_PATCH_WORKFLOW, texts["quality-step-gone"]))),
        ("launchers/workflow-no-setup", lambda: any("0 setup-python" in finding for finding in m.workflow_findings(
            "lone", texts["no-setup"]))),
        ("launchers/workflow-names-py-file", lambda: any("0 setup-python" in finding for finding in
                                                         m.workflow_findings("lone", texts["shebang-job"]))),
        ("launchers/workflow-no-jobs-line", lambda: workflow("no-jobs-line") == ["w:3: " + outside]),
        ("launchers/workflow-python-before-setup", lambda: workflow("python-before-setup")
         == ["w:7: job lone: names Python before the child-status probe step"]),
        ("launchers/workflow-job-key-indented", lambda: workflow("job-key-indented")
         == ["w:2: " + grammar + "jobs: does not hold a mapping of jobs at two spaces"]),
        ("launchers/workflow-job-key-comment", lambda: workflow("job-key-comment")
         == ["w:14: " + grammar + "a value that is not a one-line scalar or a literal block scalar (|)"]),
        ("launchers/workflow-later-job-key", lambda: workflow("later-job-key") == ["w:11: " + deeper]),
        ("launchers/workflow-probe-in-block-scalar", lambda: workflow("probe-in-block-scalar") == [not_probe]),
        ("launchers/workflow-probe-if-false", lambda: workflow("probe-if-false") == [not_probe]),
        ("launchers/workflow-probe-continued", lambda: workflow("probe-continued") == ["w:11: " + deeper]),
        ("launchers/workflow-host-pin-before-probe", lambda: not quality_workflow("host-pin-before-probe")),
        ("launchers/workflow-host-pin-other-job", lambda: quality_workflow("host-pin-other-job")
         == [not_probe_in(m.HOST_PATCH_WORKFLOW, "lone")]),
        ("launchers/workflow-host-pin-opf-job-other-workflow", lambda: workflow("host-pin-before-probe")
         == [not_probe_in("w", "opf-host-patch")]),
        ("launchers/workflow-host-pin-git-job-other-workflow", lambda: workflow("host-pin-git-job")
         == [not_probe_in("w", "git-fixture-env-core-host-patch")]),
        ("launchers/workflow-host-pin-prefix-step", lambda: quality_workflow("host-pin-prefix")
         == [not_probe_in(m.HOST_PATCH_WORKFLOW, "opf-host-patch")]),
        ("launchers/workflow-host-pin-old-probe-name", lambda: quality_workflow("host-pin-old-probe")
         == [not_probe_in(m.HOST_PATCH_WORKFLOW, "opf-host-patch", "the Python host pin step")]),
        ("launchers/workflow-host-probe-ahead-of-pin", lambda: not quality_workflow("host-probe-ahead")),
        ("launchers/workflow-host-pin-jobs-exact", lambda: sorted(m.HOST_PATCH_JOBS) == sorted(host_jobs)),
        ("launchers/workflow-host-pin-job-opf", lambda: host_job_held(host_jobs[0])),
        ("launchers/workflow-host-pin-job-core", lambda: host_job_held(host_jobs[1])),
        ("launchers/workflow-host-pin-job-config-combined", lambda: host_job_held(host_jobs[2])),
        ("launchers/workflow-host-pin-job-config-malformed", lambda: host_job_held(host_jobs[3])),
        ("launchers/workflow-host-pin-job-config-system", lambda: host_job_held(host_jobs[4])),
        ("launchers/workflow-block-hash-line", lambda: workflow("block-hash-line")
         == ["w:9: job lone: names Python before the child-status probe step"]),
        ("launchers/workflow-defaults-after-steps", lambda: workflow("defaults-after-steps") == ["w:14: " + outside]),
        ("launchers/workflow-python-outside-run-step", lambda: workflow("python-outside-run-step")
         == ["w:13: job lone: names Python in a step with no run: key"]),
        ("launchers/workflow-job-continue-on-error", lambda: workflow("job-continue-on-error")
         == ["w: job lone launches Python and carries continue-on-error, which would mask a probe failure"]),
        ("launchers/workflow-outside-grammar", lambda: [
            [grammar in finding for finding in m.workflow_findings("w", text)] for text in texts["outside-grammar"]]
         == [[True]] * len(texts["outside-grammar"])),
        ("launchers/workflow-targets-hash-inside-word", lambda: m.workflow_targets(texts["targets-hash"])
         == {CANONICAL_REL, "tools/child.py"}),
        ("launchers/real-workflows-in-grammar", lambda: bool(texts["workflows"]) and all(
            m._workflow_jobs(m.read_workflow(text)) and not m.workflow_findings(rel, text)
            for rel, text in texts["workflows"])),
        ("launchers/workflow-interpreter-path-needs-probe", lambda: any(
            "0 setup-python" in finding for finding in m.workflow_findings("w", texts["interpreter-path-job"]))),
        ("launchers/workflow-interpreter-spelling", lambda: m.workflow_findings("w", texts["interpreter-spelling"])
         == ["w:13: names the interpreter /usr/bin/python3, not the python3 or python setup-python puts on PATH",
             "w:16: names the interpreter python3.14, not the python3 or python setup-python puts on PATH"]),
        ("launchers/workflow-hash-inside-word", lambda: any(
            "0 setup-python" in finding for finding in m.workflow_findings("w", texts["hash-inside-word"]))),
        ("launchers/hook-command-exact", lambda: [finding.split(": ", 1)[1] for finding in hooks("hooks-echo")]
         == ["a hook command runs 'echo', not exactly 'python3'"]),
        ("launchers/hook-args-exact", lambda: [finding.split(": ", 1)[1].split(" are ")[0]
                                               for finding in hooks("hooks-prepended")] == ["a hook command's args"]),
        ("launchers/shell-target-unguarded", lambda: shell("shell", "shell-unguarded")
         == ["launches tools/check_leaks.py, resolved to tools/check_leaks.py, which is not a self-guarded entry"]),
        ("launchers/shell-outside-repository", lambda: shell(ci_launcher, "shell-outside") == [
            "launches /tmp/opf.py, which does not resolve to a path inside the repository",
            "launches $here/../../../../opf/tools/opf.py, which does not resolve to a path inside the repository",
            "launches $OPF_DIR/opf.py, which does not resolve to a path inside the repository"]),
        ("launchers/shell-same-name-unguarded", lambda: shell(ci_launcher, "shell-same-name") == [
            "launches tools/opf.py, resolved to tools/opf.py, which is not a self-guarded entry",
            "launches ${OPF_TOOL:-$here/../tools/opf.py, resolved to opf/enforcement/tools/opf.py, which is not a "
            "self-guarded entry"]),
        ("launchers/shell-here-unbound", lambda: shell(SHELL_REAL[0], "shell-here-unbound") == [
            "launches ${OPF_TOOL:-$here/../../tools/opf.py, which does not resolve to a path inside the "
            "repository"]),
        ("launchers/shell-dir-unbound", lambda: shell(SHELL_REAL[1], "shell-dir-unbound") == [
            "launches ${OPF_TOOL:-$here/../../tools/opf.py, which does not resolve to a path inside the "
            "repository"]),
        ("launchers/unreadable-dir-cannot-evaluate", lambda: launchers() == (every_launcher, [])
         and raises(lambda: launchers(unreadable=True))),
        ("launchers/unlistable-dir-cannot-evaluate", lambda: raises(lambda: launchers(unlistable=True))),
        ("launchers/absent-declared-launcher", lambda: launchers(absent=m.EXTRA_SHELL_LAUNCHERS[:1]) == (
            every_launcher[:2] + list(m.EXTRA_SHELL_LAUNCHERS[1:]),
            [m.EXTRA_SHELL_LAUNCHERS[0] + ": a declared shell launcher (EXTRA_SHELL_LAUNCHERS) that does not exist"])),
        ("launchers/undecodable-cannot-evaluate", lambda: m._decoded(b"ok\n", "x.sh") == "ok\n"
         and raises(lambda: m._decoded(b"python3 -I -B \xff.py\n", "x.sh"))),
        ("completeness/unclassified-hook-mode", lambda: any("no_such_mode" in finding for finding in
                                                            m.hook_registration_findings(
                                                                texts["hooks-unregistered"], handlers, events))),
        ("completeness/unlisted-residual-is-finding", lambda: [
            finding.split(": ", 1)[0] for finding in m.classify(
                entries, launched, reads_of, ("tools/launched.py",), False)[1]] == ["tools/new_reader.py"]),
        ("completeness/stale-residual-is-finding", lambda: m.classify(
            entries, launched, reads_of, ("tools/launched.py", "tools/new_reader.py", "tools/plain.py"), False)[:2]
         == ({"tools/launched.py": "LAUNCHER", "tools/new_reader.py": "RESIDUAL", "tools/plain.py": "NOT-APPLICABLE"},
             ["tools/plain.py: listed in DIRECT_LAUNCH_RESIDUAL but now NOT-APPLICABLE (stale)"])),
        ("completeness/gone-residual-is-finding", lambda: m.classify(
            launched, launched, reads_of, ("tools/gone.py", "tools/launched.py"), False)[1]
         == ["tools/gone.py: listed in DIRECT_LAUNCH_RESIDUAL but no longer an entry point (stale)"]),
        ("completeness/dynamic-load-cannot-evaluate", lambda: [
            "import_module" in reason for reason in m.classify({"tools/dynamic.py"}, set(), reads_of, (), False)[2]]
         == [True]),
        ("completeness/switch-flips", lambda: [
            "DIRECT_LAUNCH_CHECK is on" in finding
            for finding in m.classify(launched, launched, reads_of, ("tools/launched.py",), True)[1]] == [True]),
        ("completeness/scan-sees-every-api",
         lambda: all(m.module_reads(ast.parse(snippet))[0] for snippet in API_SNIPPETS)),
        ("completeness/scan-sees-every-load",
         lambda: all(m.module_reads(ast.parse(snippet))[1] for snippet in LOAD_SNIPPETS)),
        ("completeness/reviewed-load-target-pinned", lambda: reviewed_reads("tools/loader.py")
         == ([("tools/helper_mod.py", "import subprocess")], [])
         and reviewed_reads("tools/loader_moved.py") == ([], [("tools/loader_moved.py",
                                                               "runpy.run_path('tools/plain.py')")])),
        # The limit (R13): the call text pins a variable target's expression, not its value, so the moved
        # assignment stays reviewed and the scan follows the row's target, not the one now assigned.
        ("completeness/reviewed-variable-target-limit", lambda: reviewed_reads("tools/loader_var_moved.py")
         == reviewed_reads("tools/loader_var.py") == ([("tools/helper_mod.py", "import subprocess")], [])),
        ("completeness/scan-follows-local-imports",
         lambda: reads_of("tools/new_reader.py")[0] == [("tools/helper_mod.py", "import subprocess")]),
        ("completeness/scan-clean-module", lambda: reads_of("tools/plain.py") == ([], [])),
    )

    def rebound(form, named):
        """True when the rebinding of form is a finding for _entry and names it (form, line)."""
        finding = m.entry_function_finding("decl", trees["entry-rebound-" + form], "check_opf_declaration.py") or ""
        return "_entry is bound 2 times" in finding and named.format("_entry") + " (line " in finding

    def guard_rebound(form, named):
        """True when the rebinding of form is a copy finding for _child_status_refusal_exit that names it."""
        return any("the inline copy of _child_status_refusal_exit is bound 2 times" in finding
                   and named.format("_child_status_refusal_exit") + " (line " in finding
                   for finding in copies("guard-rebound-" + form, m.CANONICAL_NAMES))

    for form, _statement, named in REBINDINGS:
        vectors += (("entries/entry-rebound-" + form, lambda form=form, named=named: rebound(form, named)),
                    ("copies/guard-rebound-" + form, lambda form=form, named=named: guard_rebound(form, named)))
    out = {}
    for vector, thunk in vectors:
        try:
            out[vector] = bool(thunk())
        except Exception:
            out[vector] = False
    return out

# Each mutant removes one check from the gate half of this file (the part before the self-test section): (mutant
# id, exact source text, replacement, the vector that must then fail).
MUTANTS = (
    ("copy-compare", "elif ast.dump(nodes[0]) != tmpl[name]:", "elif False:", "copies/drift-is-finding"),
    ("copy-count", "        if len(bindings) != 1 or len(nodes) != 1:\n            out.append(",
     "        if False:\n            out.append(", "copies/missing-copy-is-finding"),
    ("copy-binding-count", "        bindings = _bindings(tree, name)\n",
     "        bindings = [(node.lineno, \"a top-level definition\") for node in _top_definitions(tree, name)]\n",
     "copies/guard-rebound-tuple"),
    ("copy-decorated", "elif getattr(nodes[0], \"decorator_list\", None):", "elif False:",
     "copies/guard-rebound-decorated"),
    ("binding-global", "if isinstance(node, ast.Global) and name in node.names]", "if False]",
     "entries/entry-rebound-global"),
    ("binding-store",
     "if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):",
     "if False:", "copies/guard-rebound-namespace-store"),
    ("binding-nested", "            children = list(ast.iter_child_nodes(node))\n",
     "            children = [child for child in ast.iter_child_nodes(node)\n"
     "                        if node is tree or not isinstance(child, ast.stmt)]\n", "entries/entry-rebound-if"),
    ("binding-import", "        return [\"an import\" for alias in node.names",
     "        return [\"an import\" for alias in node.names if False", "copies/guard-rebound-import"),
    ("binding-from-import", "                if (alias.asname or alias.name) in (name, \"*\")]",
     "                if False]", "entries/entry-rebound-from-import"),
    ("binding-handler", "        return [\"an except-as name\"] if node.name == name else []\n",
     "        return []\n", "entries/entry-rebound-except-as"),
    ("binding-match", "        return [\"a match capture\"] if (node.rest",
     "        return [] if (node.rest", "copies/guard-rebound-match"),
    ("binding-function-bodies",
     "children = node.decorator_list + [node.args] + ([node.returns] if node.returns else [])",
     "children = node.decorator_list + [node.args] + ([node.returns] if node.returns else []) + node.body",
     "entries/entry-local-bindings-clean"),
    ("binding-class-bodies", "stack.extend((statement, statement, True) for statement in node.body)",
     "stack.extend((statement, statement, False) for statement in node.body)", "entries/entry-local-bindings-clean"),
    ("binding-comprehension-targets", "                elif not isinstance(target, ast.Name):\n",
     "                else:\n", "entries/entry-local-bindings-clean"),
    ("cli-entry-call", "or ast.dump(block.body[-1]) != want", "or False", "entries/cli-line-shape"),
    ("cli-entry-exact", "or (exact and len(block.body) != 1)", "or False", "entries/cli-extra-statement"),
    ("entry-function-call", "if len(body) != 1 or ast.dump(body[-1]) != entry:", "if len(body) != 1:",
     "entries/entry-function-call-shape"),
    ("entry-function-exact", "if len(body) != 1 or ast.dump(body[-1]) != entry:",
     "if not body or ast.dump(body[-1]) != entry:", "entries/entry-function-extra-statement"),
    ("entry-function-once",
     "if len(bindings) != 1 or len(definitions) != 1 or not isinstance(definitions[0], ast.FunctionDef):",
     "if not isinstance(definitions[0], ast.FunctionDef):", "entries/entry-function-rebound"),
    ("entry-binding-count", "    bindings = _bindings(tree, \"_entry\")\n",
     "    bindings = [(node.lineno, \"a top-level definition\") for node in _top_definitions(tree, \"_entry\")]\n",
     "entries/entry-rebound-tuple"),
    ("entry-decorated", "    if definitions[0].decorator_list:\n", "    if False:\n",
     "entries/entry-rebound-decorated"),
    ("entry-parameters", "if ast.dump(definitions[0].args) != ", "if False and ast.dump(definitions[0].args) != ",
     "entries/entry-function-parameter-default"),
    ("entry-guard-present", "    if statements[:1] != [guard]:\n", "    if False:\n",
     "entries/entry-function-guard-missing"),
    ("entry-guard-first", "if statements[:1] != [guard]:", "if guard not in statements:",
     "entries/entry-function-guard-not-first"),
    ("entry-guard-name", "if statements[:1] != [guard]:",
     "if [statement.split(\", args=\")[0] for statement in statements[:1]] != [guard.split(\", args=\")[0]]:",
     "entries/entry-function-guard-other-name"),
    ("self-test-first-compare", "if block is None or [ast.dump(statement) for statement in block.body] != expected:",
     "if block is None:", "entries/self-test-first-guard-missing"),
    ("self-test-first-last-only",
     "if block is None or [ast.dump(statement) for statement in block.body] != expected:",
     "if block is None or [ast.dump(statement) for statement in block.body][-1:] != expected[-1:]:",
     "entries/self-test-first-widened-test"),
    ("self-test-first-ends-only",
     "if block is None or [ast.dump(statement) for statement in block.body] != expected:",
     "if block is None or [ast.dump(statement) for statement in block.body[:1] + block.body[-1:]] != expected:",
     "entries/self-test-first-extra-statement"),
    ("self-test-entry", "if block is None or [ast.dump(statement) for statement in block.body] != want:",
     "if block is None:", "entries/self-test-entry-shape"),
    ("self-test-guard", "    if not body or ast.dump(body[0]) != guard:\n", "    if not body:\n",
     "entries/self-test-guard-missing"),
    ("self-test-decorated", " or tests[0].decorator_list", "", "entries/self-test-decorated"),
    ("self-test-bound-once", ' or len(_bindings(tree, "self_test")) != 1', "",
     "entries/self-test-rebound-lambda"),
    ("binding-name-store", "        stored = node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))\n",
     "        stored = False\n", "entries/self-test-rebound-assign"),
    ("binding-from-import-self-test", "                if (alias.asname or alias.name) in (name, \"*\")]",
     "                if False]", "entries/self-test-rebound-import"),
    ("copy-bindings", "        if len(bindings) != 1 or len(nodes) != 1:\n", "        if len(nodes) != 1:\n",
     "copies/rebound-guard-is-finding"),
    ("copy-loop-self-test-entry", "finding = (self_test_entry_finding(rel, tree, name) if rel in SELF_TEST_COPIES",
     "finding = (None if rel in SELF_TEST_COPIES", "entries/self-test-entry-checked"),
    ("copy-loop-cli-entry", "                   else cli_entry_finding(rel, tree, name))", "                   else None)",
     "entries/cli-entry-checked"),
    ("entry-statement-call", "finding = entry_statement_finding(rel, tree) if rel == ENTRY_STATEMENT_REL else None",
     "finding = None", "entries/self-test-entry-pinned"),
    ("entry-statement-compare",
     "\n                and ast.dump(ast.parse(value.value)) == ast.dump(ast.parse(SELF_TEST_ENTRY)))", ")",
     "entries/self-test-entry-pinned"),
    ("hook-block-compare", "    if got != want:\n", "    if False:\n", "entries/hook-block-shape"),
    ("runner-first", "if line.split() != probe_line.split():", "if False:", "launchers/runner-first-gate"),
    ("runner-prelude", "        if _names_python(line):\n            return", "        if False:\n            return",
     "launchers/runner-prelude-launch"),
    ("workflow-step", "if probe == len(steps) or _step_pairs(steps[probe]) != want_probe:",
     "if probe == len(steps):", "launchers/workflow-first-step"),
    ("workflow-step-keys", "_step_pairs(steps[probe]) != want_probe",
     "_step_pairs(steps[probe])[:len(want_probe)] != want_probe", "launchers/workflow-probe-if-false"),
    ("workflow-host-pin-accept",
     "if (rel == HOST_PATCH_WORKFLOW and job in HOST_PATCH_JOBS and probe < len(steps)\n"
     "                and _step_pairs(steps[probe]) == HOST_PIN_STEP):",
     "if False:", "launchers/workflow-host-pin-before-probe"),
    ("workflow-host-pin-scope", "rel == HOST_PATCH_WORKFLOW and job in HOST_PATCH_JOBS and probe",
     "rel == HOST_PATCH_WORKFLOW and probe", "launchers/workflow-host-pin-other-job"),
    ("workflow-host-pin-workflow-scope", "rel == HOST_PATCH_WORKFLOW and job in HOST_PATCH_JOBS",
     "job in HOST_PATCH_JOBS", "launchers/workflow-host-pin-opf-job-other-workflow"),
    ("workflow-host-pin-workflow-true", "if (rel == HOST_PATCH_WORKFLOW and", "if (True and",
     "launchers/workflow-host-pin-git-job-other-workflow"),
    ("workflow-host-pin-prefix", "_step_pairs(steps[probe]) == HOST_PIN_STEP",
     "_step_pairs(steps[probe])[:len(HOST_PIN_STEP)] == HOST_PIN_STEP",
     "launchers/workflow-host-pin-prefix-step"),
    ("workflow-probe-either-name", "if probe == len(steps) or _step_pairs(steps[probe]) != want_probe:",
     "if probe == len(steps) or _step_pairs(steps[probe]) not in (PROBE_STEP, HOST_PROBE_STEP):",
     "launchers/workflow-host-pin-old-probe-name"),
    ("workflow-host-pin-early", "if position < probe and position != pin:", "if position < probe:",
     "launchers/workflow-host-pin-before-probe"),
    ("host-patch-job-extra", '("opf-host-patch", ', '("opf-host-patch", "git-fixture-env-host-patch", ',
     "launchers/workflow-host-pin-jobs-exact"),
    ("host-patch-job-opf", '("opf-host-patch", ', "(", "launchers/workflow-host-pin-job-opf"),
    ("host-patch-job-core", '"git-fixture-env-core-host-patch", ', "", "launchers/workflow-host-pin-job-core"),
    ("host-patch-job-config-combined", '"git-fixture-env-config-combined-host-patch",', "",
     "launchers/workflow-host-pin-job-config-combined"),
    ("host-patch-job-config-malformed", '"git-fixture-env-config-malformed-host-patch", ', "",
     "launchers/workflow-host-pin-job-config-malformed"),
    ("host-patch-job-config-system", ', "git-fixture-env-config-system-host-patch")', ")",
     "launchers/workflow-host-pin-job-config-system"),
    ("workflow-setup", "        if len(setups) != 1:\n", "        if False:\n", "launchers/workflow-no-setup"),
    ("workflow-py-word", "return bool(PYTHON_WORD_RE.search(text) or PY_WORD_RE.search(text))",
     "return bool(PYTHON_WORD_RE.search(text))", "launchers/workflow-names-py-file"),
    ("workflow-early", "            if position < probe and position != pin:\n", "            if False:\n",
     "launchers/workflow-python-before-setup"),
    ("workflow-outside", "for index, _value in named if index not in placed)", "for index, _value in named if False)",
     "launchers/workflow-defaults-after-steps"),
    ("workflow-run-step", "elif position > probe and not any(", "elif False and not any(",
     "launchers/workflow-python-outside-run-step"),
    ("workflow-job-masking", "if any(key == \"continue-on-error\" for",
     "if False and any(key == \"continue-on-error\" for", "launchers/workflow-job-continue-on-error"),
    ("workflow-jobs-indent", " or child[1] != JOBS_INDENT:", ":", "launchers/workflow-job-key-indented"),
    ("grammar-deeper", "            if _indent(lines[at]) > indent:\n                raise _OutsideGrammar(",
     "            if _indent(lines[at]) > indent:\n                at += 1\n                continue\n"
     "                raise _OutsideGrammar(", "launchers/workflow-probe-continued"),
    ("grammar-plain", "    return (bool(value) and value == value.strip()",
     "    return True or (bool(value) and value == value.strip()", "launchers/workflow-job-key-comment"),
    ("grammar-repeated-key", "        if key in keys:\n", "        if False:\n", "launchers/workflow-outside-grammar"),
    ("literal-hash", "    if kind == \"text\":\n        return list(body)\n",
     "    if kind == \"text\":\n        return [(index, line) for index, line in body\n"
     "                if not line.lstrip().startswith(\"#\")]\n", "launchers/workflow-block-hash-line"),
    ("workflow-interpreter", "if word not in PLAIN_INTERPRETERS]", "if False]",
     "launchers/workflow-interpreter-spelling"),
    ("workflow-interpreter-word", r"(?<![\w.])(?<![A-Za-z0-9]-)(?:[^\s;|&()<>=]*/)?python[0-9.]*[a-z]?(?![\w.-])",
     r"(?<![\w./-])python3?(?=\s)", "launchers/workflow-interpreter-path-needs-probe"),
    ("comment-split", "return bool(PYTHON_WORD_RE.search(text) or PY_WORD_RE.search(text))",
     "return bool(PYTHON_WORD_RE.search(text.split(\"#\", 1)[0]) or PY_WORD_RE.search(text.split(\"#\", 1)[0]))",
     "launchers/workflow-hash-inside-word"),
    ("targets-comment-split", "for match in PY_LAUNCH_RE.finditer(value)}",
     "for match in PY_LAUNCH_RE.finditer(value.split(\"#\", 1)[0])}", "launchers/workflow-targets-hash-inside-word"),
    ("hook-command", "        if command.get(\"command\") != HOOK_COMMAND:\n", "        if False:\n",
     "launchers/hook-command-exact"),
    ("hook-args", "and len(args) == len(HOOK_ARGS) + 1 and args[:-1] == HOOK_ARGS",
     "and HOOK_TARGET in args and args.index(HOOK_TARGET) == len(args) - 2", "launchers/hook-args-exact"),
    ("shell-target", "            elif target not in self_rels:\n", "            elif False:\n",
     "launchers/shell-same-name-unguarded"),
    ("shell-absolute", "if not word or word.startswith(\"/\") or any(", "if not word or any(",
     "launchers/shell-outside-repository"),
    ("shell-climb", "    if path == \"..\" or path.startswith(\"../\"):\n", "    if False:\n",
     "launchers/shell-outside-repository"),
    ("shell-here", "    return form is not None\n", "    return True\n", "launchers/shell-here-unbound"),
    ("shell-dir", "        return _single_binding(lines, DIR_ASSIGN_RE, (DIR_BINDING,)) is not None\n",
     "        return True\n", "launchers/shell-dir-unbound"),
    ("walk-error", "walk(Path(root) / \"opf\", onerror=_fail)", "walk(Path(root) / \"opf\")",
     "launchers/unreadable-dir-cannot-evaluate"),
    ("listdir-error", "    except OSError as exc:\n        raise CannotEvaluate(\"{}: cannot be listed",
     "    except ArithmeticError as exc:\n        raise CannotEvaluate(\"{}: cannot be listed",
     "launchers/unlistable-dir-cannot-evaluate"),
    ("declared-launcher", "        if lexists(Path(root) / rel):\n", "        if True:\n",
     "launchers/absent-declared-launcher"),
    ("decode-strict", "        return data.decode(\"utf-8\")\n", "        return data.decode(\"utf-8\", \"replace\")\n",
     "launchers/undecodable-cannot-evaluate"),
    ("hook-mode", "if mode not in handlers or mode not in events:", "if False:",
     "completeness/unclassified-hook-mode"),
    ("unlisted", "        elif rel not in listed:\n", "        elif False:\n",
     "completeness/unlisted-residual-is-finding"),
    ("stale", 'elif classes[rel] in ("SELF", "NOT-APPLICABLE"):', "elif False:",
     "completeness/stale-residual-is-finding"),
    ("gone", "        if rel not in classes:\n            findings.append(",
     "        if False:\n            findings.append(", "completeness/gone-residual-is-finding"),
    ("dynamic", "            if dynamic:\n", "            if False:\n", "completeness/dynamic-load-cannot-evaluate"),
    ("switch", "        if direct_check:\n", "        if False:\n", "completeness/switch-flips"),
    ("scan-os", "if _spells_os(node.value, os_names) and OS_SPAWN_RE.fullmatch(node.attr):",
     "if False:", "completeness/scan-sees-every-api"),
    ("scan-os-chain", "        return node.attr in OS_MODULES\n", "        return False\n",
     "completeness/scan-sees-every-api"),
    ("scan-os-modules", "    return _modules_key(node) in OS_MODULES\n", "    return False\n",
     "completeness/scan-sees-every-api"),
    ("scan-modules-spawn", "            if key is not None and (key.split", "            if False and (key.split",
     "completeness/scan-sees-every-api"),
    ("scan-os-star", "os_spawn = alias.name == \"*\" or OS_SPAWN_RE.fullmatch(alias.name)",
     "os_spawn = OS_SPAWN_RE.fullmatch(alias.name)", "completeness/scan-sees-every-api"),
    ("scan-names", "            if node.id in SPAWN_NAMES:\n", "            if False:\n",
     "completeness/scan-sees-every-api"),
    ("scan-load-attr", "            elif isinstance(callee, ast.Attribute) and callee.attr in DYNAMIC_ATTRS:\n",
     "            elif False:\n", "completeness/scan-sees-every-load"),
    ("scan-load-name", "if isinstance(callee, ast.Name) and callee.id in load_names:",
     "if isinstance(callee, ast.Name) and callee.id in DYNAMIC_NAMES:", "completeness/scan-sees-every-load"),
    ("scan-load-alias", "load_names.update(alias.asname for alias in node.names if alias.name in DYNAMIC_ATTRS",
     "load_names.update(alias.asname for alias in node.names if False", "completeness/scan-sees-every-load"),
    ("scan-resolvers", "\"run_path\", \"run_module\", \"resolve_name\", \"locate\"))",
     "\"run_path\", \"run_module\"))", "completeness/scan-sees-every-load"),
    ("reviewed-target", "and tuple(loads) == reviewed[current][0]:", "and len(loads) == len(reviewed[current][0]):",
     "completeness/reviewed-load-target-pinned"),
    ("reviewed-follow", "            stack.extend(reviewed[current][1])\n", "            pass\n",
     "completeness/reviewed-variable-target-limit"),
    ("scan-closure", "                stack.append(target)\n", "                pass\n",
     "completeness/scan-follows-local-imports"),
)
UNMUTATED = {"copies/real-copies-identical", "entries/real-entries-pass", "entries/entry-function-real-pass",
             "launchers/real-launchers-pass",
             "launchers/real-workflows-in-grammar", "launchers/workflow-host-probe-ahead-of-pin",
             "completeness/scan-clean-module"}


def _load_mutant(source, directory, number):
    path = Path(directory) / "child_status_coverage_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("child_status_coverage_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _truncated_runner(text, tail_marker):
    """The runner's scaffold, its first gate line and its terminal summary: the choke point alone, runnable."""
    lines = text.splitlines(True)
    first = next(index for index, line in enumerate(lines) if line.startswith('run_gate "'))
    tail = next(index for index, line in enumerate(lines) if line.startswith(tail_marker))
    return "".join(lines[:first + 1] + lines[tail:])


def _copy(rel, base, text=None):
    path = Path(base) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.write_bytes((ROOT / rel).read_bytes())
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _behaviour(probe, base):
    """[(check id, ok, detail)] for every behaviour, control and guard-removed vector."""
    out = []
    python = [sys.executable, "-I", "-B"]
    via = probe.through_bash
    refusal = probe.refusal_text
    clis = (("opf/tools/opf.py", ("--help",), "opf.py", "opf: the OPFiles"),
            ("tools/check_selftest_execution.py", (), "check_selftest_execution.py",
             "usage: check_selftest_execution.py"),
            (COVERAGE_REL, ("--usage",), Path(COVERAGE_REL).name, "usage: check_child_status_coverage.py"),
            ("opf/tools/_opf_declaration.py", ("--usage",), "_opf_declaration.py", "usage: _opf_declaration.py"),
            ("tools/check_opf_declaration.py", ("--usage",), "check_opf_declaration.py",
             "usage: check_opf_declaration.py"))
    results = {}
    for rel, args, name, control in clis:
        results[rel] = (via("ignore", python + [str(ROOT / rel)] + list(args), cwd=str(ROOT)),
                        via("default", python + [str(ROOT / rel)] + list(args), cwd=str(ROOT)))

    def refused(got, name):
        return got[0] == 2 and got[1] == "" and got[2].startswith(refusal(name)) and got[2].count("\n") == 1

    def ran(got, code, marker):
        return got[0] == code and marker in got[1] + got[2] and "refuses to run" not in got[2]

    for check_id, rel, name in (("behaviour/opf-cli-refuses", "opf/tools/opf.py", "opf.py"),
                                ("behaviour/launcher-cli-refuses", "tools/check_selftest_execution.py",
                                 "check_selftest_execution.py"),
                                ("behaviour/coverage-gate-refuses", COVERAGE_REL, "check_child_status_coverage.py"),
                                ("behaviour/opf-declaration-cli-refuses", "opf/tools/_opf_declaration.py",
                                 "_opf_declaration.py"),
                                ("behaviour/declaration-gate-refuses", "tools/check_opf_declaration.py",
                                 "check_opf_declaration.py")):
        out.append((check_id, refused(results[rel][0], name), results[rel][0]))
    # The other CLI copies under an inherited ignore, each read by its refusal line, not its exit status alone: each
    # refuses at its entry line, and each opf/tools self_test module on its --self-test path too, there at the first
    # statement of self_test().
    for check_id, rel, args in (
            ("behaviour/adopt-complete-refuses", "opf/tools/_opf_adopt_complete.py", ()),
            ("behaviour/adopt-complete-self-test-refuses", "opf/tools/_opf_adopt_complete.py", ("--self-test",)),
            ("behaviour/adopt-state-refuses", "opf/tools/_opf_adopt_state.py", ()),
            ("behaviour/adopt-state-self-test-refuses", "opf/tools/_opf_adopt_state.py", ("--self-test",)),
            ("behaviour/import-complete-refuses", "opf/tools/_opf_import_complete.py", ()),
            ("behaviour/import-complete-self-test-refuses", "opf/tools/_opf_import_complete.py", ("--self-test",)),
            ("behaviour/gitleaks-pin-refuses", "tools/check_gitleaks_pin.py", ()),
            ("behaviour/must-deliver-refuses", "tools/check_must_deliver.py", ())):
        got = via("ignore", python + [str(ROOT / rel)] + list(args), cwd=str(ROOT))
        out.append((check_id, refused(got, Path(rel).name), got))
    for check_id, rel, marker in (("control/opf-cli-default-runs", "opf/tools/opf.py", "opf: the OPFiles"),
                                  ("control/launcher-cli-default-runs", "tools/check_selftest_execution.py",
                                   "usage: check_selftest_execution.py"),
                                  ("control/coverage-gate-default-runs", COVERAGE_REL,
                                   "usage: check_child_status_coverage.py"),
                                  ("control/opf-declaration-cli-default-runs", "opf/tools/_opf_declaration.py",
                                   "usage: _opf_declaration.py"),
                                  ("control/declaration-gate-default-runs", "tools/check_opf_declaration.py",
                                   "usage: check_opf_declaration.py")):
        out.append((check_id, ran(results[rel][1], 2, marker), results[rel][1]))

    pre = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                      "tool_input": {"file_path": str(ROOT / README_REL)}, "cwd": str(ROOT)})
    stop = json.dumps({"hook_event_name": "Stop", "stop_hook_active": False, "cwd": str(ROOT)})
    note = "aiqt_hooks: absolute_paths: SIGCHLD is ignored (SIG_IGN) in this process, so a failed child would " \
           "read as a success; failing closed\n"
    restore = ("aiqt_hooks: diff_wall_stop: SIGCHLD is ignored (SIG_IGN) in this process; SIGCHLD restored to "
               "SIG_DFL for this hook process and the probe then read its child's status\n")
    for check_id, rel in (("behaviour/pretool-refuses", HOOK_COPIES[0]),
                          ("behaviour/plugin-pretool-refuses", HOOK_COPIES[1])):
        got = via("ignore", python + [str(ROOT / rel), "absolute_paths"], stdin=pre, cwd=str(ROOT))
        out.append((check_id, got == (2, "", note), got))
    got = via("default", python + [str(ROOT / HOOK_COPIES[0]), "absolute_paths"], stdin=pre, cwd=str(ROOT))
    out.append(("control/pretool-default-runs", got[0] == 0 and got[2] == "", got))
    default = via("default", python + [str(ROOT / HOOK_COPIES[0]), "diff_wall_stop"], stdin=stop, cwd=str(ROOT))
    ignored = via("ignore", python + [str(ROOT / HOOK_COPIES[0]), "diff_wall_stop"], stdin=stop, cwd=str(ROOT))
    out.append(("control/fail-open-default-runs", default[0] == 0 and default[1] != "" and default[2] == "", default))
    out.append(("behaviour/fail-open-restores", ignored == (0, default[1], restore), (default, ignored)))
    got = probe.run_child(["-c", probe.TRAMPOLINE, "ignore"] + python + [
        "-c", "import subprocess, sys; print(subprocess.run(sys.argv[1:], capture_output=True).returncode)"]
        + python + [str(ROOT / CANONICAL_REL)])
    out.append(("behaviour/python-reader-loses-refusal", got[:2] == (0, "0\n"), got))

    for check_id, rel, marker, tail_marker, rc_line in (
            ("behaviour/runner-first-gate-fails", LOCAL_RUNNER, "GATE FAILED: child-status (exit 2)",
             'if [ "$failed" -ne 0 ]; then', "RESULT: FAIL"),
            ("behaviour/opf-runner-first-gate-fails", OPF_RUNNER, "GATE FAILED: child-status (exit 2)",
             'if [ "$failed" -ne 0 ]; then', "OPF STANDALONE SUBSET: FAILED")):
        tree = Path(base) / ("runner-" + check_id.rsplit("/", 1)[1])
        _copy(CANONICAL_REL, tree)
        runner = _copy(rel, tree, _truncated_runner((ROOT / rel).read_text(encoding="utf-8"), tail_marker))
        ignored = via("ignore", ["bash", str(runner)], cwd=str(tree))
        default = via("default", ["bash", str(runner)], cwd=str(tree))
        out.append((check_id, ignored[0] == 1 and marker in ignored[1] and rc_line in ignored[1]
                    and refusal("_child_status.py") in ignored[2], ignored))
        out.append((check_id.replace("behaviour/", "control/").replace("-first-gate-fails", "-default-passes"),
                    default[0] == 0 and "GATE FAILED" not in default[1], default))

    # Each guard removed, computed by running: the vector above would then be red.
    guardless = Path(base) / "guardless"
    selftest = _copy("tools/check_selftest_execution.py", guardless, _swap(
        (ROOT / "tools/check_selftest_execution.py").read_text(encoding="utf-8"),
        CLI_ENTRY.format("check_selftest_execution.py"), "sys.exit(main())"))
    got = via("ignore", python + [str(selftest)], cwd=str(guardless))
    out.append(("revert/launcher-guard-removed-red", not refused(got, "check_selftest_execution.py")
                and "usage: check_selftest_execution.py" in got[2], got))
    opf_tree = Path(base) / "guardless-opf"
    shutil.copytree(ROOT / "opf" / "tools", opf_tree / "opf" / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    opf = _copy("opf/tools/opf.py", opf_tree, _swap((ROOT / "opf/tools/opf.py").read_text(encoding="utf-8"),
                                                    CLI_ENTRY.format("opf.py"), "sys.exit(main())"))
    got = via("ignore", python + [str(opf), "--help"], cwd=str(opf_tree))
    out.append(("revert/opf-guard-removed-red", not refused(got, "opf.py") and "opf: the OPFiles" in got[2], got))
    declaration_module = _copy("opf/tools/_opf_declaration.py", opf_tree, _swap(
        (ROOT / "opf/tools/_opf_declaration.py").read_text(encoding="utf-8"), CLI_ENTRY.format("_opf_declaration.py"),
        "sys.exit(main())"))
    got = via("ignore", python + [str(declaration_module), "--usage"], cwd=str(opf_tree))
    out.append(("revert/opf-declaration-guard-removed-red", not refused(got, "_opf_declaration.py")
                and "usage: _opf_declaration.py" in got[2], got))
    hook = _copy(HOOK_COPIES[0], guardless, _swap((ROOT / HOOK_COPIES[0]).read_text(encoding="utf-8"),
                                                  "    unobservable = _child_status_refusal()\n",
                                                  "    unobservable = None\n"))
    got = via("ignore", python + [str(hook), "absolute_paths"], stdin=pre, cwd=str(ROOT))
    out.append(("revert/hook-guard-removed-red", got[0] == 0 and "failing closed" not in got[2], got))
    canonical = _copy(CANONICAL_REL, guardless, _swap((ROOT / CANONICAL_REL).read_text(encoding="utf-8"),
                                                      CLI_ENTRY.format("_child_status.py"), "sys.exit(main())"))
    got = via("ignore", python + [str(canonical)], cwd=str(guardless))
    out.append(("revert/probe-guard-removed-red", got[0] == 0 and got[1].startswith("PASS"), got))
    gate_tree = Path(base) / "guardless-gate"
    for rel in ("tools/_selftest_exit_report.py", "tools/check_ci_parity.py", "tools/check_entry_guard.py",
                "tools/check_python_floor.py", "tools/check_selftest_execution.py", CANONICAL_REL):
        _copy(rel, gate_tree)
    gate = _copy(COVERAGE_REL, gate_tree, _swap(Path(__file__).read_text(encoding="utf-8"),
                                                "    if _live_refusal():\n        return 2\n", ""))
    got = via("ignore", python + [str(gate), "--usage"], cwd=str(gate_tree))
    out.append(("revert/coverage-guard-removed-red", not refused(got, NAME)
                and "usage: check_child_status_coverage.py" in got[2], got))
    declaration_gate = _copy("tools/check_opf_declaration.py", gate_tree, _swap(
        (ROOT / "tools/check_opf_declaration.py").read_text(encoding="utf-8"),
        "    " + ENTRY_GUARD.format("check_opf_declaration.py") + "\n", ""))
    got = via("ignore", python + [str(declaration_gate), "--usage"], cwd=str(gate_tree))
    out.append(("revert/declaration-gate-guard-removed-red", not refused(got, "check_opf_declaration.py")
                and "usage: check_opf_declaration.py" in got[2], got))
    tree = Path(base) / "guardless-runner"
    _copy(CANONICAL_REL, tree)
    text = _truncated_runner((ROOT / LOCAL_RUNNER).read_text(encoding="utf-8"), 'if [ "$failed" -ne 0 ]; then')
    runner = _copy(LOCAL_RUNNER, tree, _swap(text, RUNNER_PROBES[0][1] + "\n", ""))
    got = via("ignore", ["bash", str(runner)], cwd=str(tree))
    out.append(("revert/runner-probe-removed-red", got[0] == 0 and "RESULT: PASS" in got[1], got))

    # The --self-test statement of opf/tools/_opf_declaration.py runs before its guard, so that launch is unguarded:
    # run it under the process-creation audit, which must record no launch, and the control, which must record
    # each audited API.
    driver = _copy("audit/driver.py", base, AUDIT_DRIVER)

    def audited(*argv):
        got = via("default", python + [str(driver)] + list(argv), cwd=str(ROOT))
        last = got[1].splitlines()[-1] if got[1].splitlines() else ""
        try:
            return got, json.loads(last[len("AUDITED "):]) if last.startswith("AUDITED ") else None
        except ValueError:
            return got, None

    got, report = audited(str(ROOT / "opf/tools/_opf_declaration.py"), "--self-test")
    out.append(("behaviour/opf-declaration-self-test-launches-nothing", got[0] == 0
                and report == {"code": 0, "launches": []} and "opf declaration self-test: PASS" in got[1], got))
    got, report = audited(str(_copy("audit/launches.py", base, AUDIT_CONTROL)))
    out.append(("control/audit-sees-every-launch", got[0] == 0 and isinstance(report, dict)
                and report.get("code") == 0 and set(AUDIT_CONTROL_LAUNCHES) <= set(report.get("launches", ())), got))
    return out


def self_test(lane, report_path=None):
    suite = PROBE_SUITE if lane == "probe" else COVERAGE_SUITE
    results, executed = [], []
    if report_path is not None:
        _selftest_exit_report.arm(report_path, suite, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    probe = _canonical_module()
    if lane == "probe":
        got = dict((check_id, (ok, detail)) for check_id, ok, detail in
                   probe.self_test_results(str(ROOT / CANONICAL_REL)))
        for check_id, _index in (("repro/ignored-reads-failed-child-as-zero", 0),
                                 ("repro/default-reads-failed-child", 1), ("live/default-passes", 2),
                                 ("live/ignored-refuses-by-name", 3), ("layer/handler-seen-by-disposition", 4),
                                 ("layer/lost-status-seen-by-probe", 5), ("layer/no-fork-nothing-probed", 6),
                                 ("layer/no-sigchld-nothing-probed", 7)):
            check(check_id, got.get(check_id, (False, "not run"))[0], got.get(check_id, (False, "not run"))[1])
    else:
        this = sys.modules[__name__]
        trees, texts = _inputs()
        canonical_tree = trees["canonical"]
        vectors = _vectors(this, trees, texts, canonical_tree)
        for check_id, _kind in (("copies/real-copies-identical", 0), ("copies/drift-is-finding", 0),
                                ("copies/missing-copy-is-finding", 0), ("copies/hook-drift-is-finding", 0),
                                ("copies/rebound-guard-is-finding", 0),
                                ("entries/real-entries-pass", 0), ("entries/self-test-entry-shape", 0),
                                ("entries/self-test-guard-missing", 0), ("entries/self-test-decorated", 0),
                                ("entries/self-test-rebound-assign", 0), ("entries/self-test-rebound-lambda", 0),
                                ("entries/self-test-rebound-import", 0), ("entries/self-test-entry-checked", 0),
                                ("entries/cli-entry-checked", 0), ("entries/self-test-entry-pinned", 0),
                                ("entries/cli-line-shape", 0),
                                ("entries/cli-extra-statement", 0), ("entries/hook-block-shape", 0),
                                ("entries/hook-block-missing", 0), ("entries/entry-function-real-pass", 0),
                                ("entries/entry-function-guard-missing", 0),
                                ("entries/entry-function-guard-not-first", 0),
                                ("entries/entry-function-rebound", 0), ("entries/entry-function-call-shape", 0),
                                ("entries/entry-function-extra-statement", 0),
                                ("entries/entry-function-parameter-default", 0),
                                ("entries/entry-function-guard-other-name", 0),
                                ("entries/entry-rebound-decorated", 0), ("copies/guard-rebound-decorated", 0),
                                ("entries/entry-local-bindings-clean", 0),
                                ("entries/self-test-first-guard-missing", 0),
                                ("entries/self-test-first-extra-statement", 0),
                                ("entries/self-test-first-widened-test", 0), ("launchers/real-launchers-pass", 0),
                                ("launchers/runner-first-gate", 0), ("launchers/opf-runner-first-gate", 0),
                                ("launchers/runner-prelude-launch", 0),
                                ("launchers/workflow-first-step", 0), ("launchers/workflow-no-setup", 0),
                                ("launchers/workflow-names-py-file", 0), ("launchers/workflow-no-jobs-line", 0),
                                ("launchers/workflow-python-before-setup", 0),
                                ("launchers/workflow-job-key-indented", 0),
                                ("launchers/workflow-job-key-comment", 0),
                                ("launchers/workflow-later-job-key", 0),
                                ("launchers/workflow-probe-in-block-scalar", 0),
                                ("launchers/workflow-probe-if-false", 0), ("launchers/workflow-probe-continued", 0),
                                ("launchers/workflow-host-pin-before-probe", 0),
                                ("launchers/workflow-host-pin-other-job", 0),
                                ("launchers/workflow-host-pin-opf-job-other-workflow", 0),
                                ("launchers/workflow-host-pin-git-job-other-workflow", 0),
                                ("launchers/workflow-host-pin-prefix-step", 0),
                                ("launchers/workflow-host-pin-old-probe-name", 0),
                                ("launchers/workflow-host-probe-ahead-of-pin", 0),
                                ("launchers/workflow-host-pin-jobs-exact", 0),
                                ("launchers/workflow-host-pin-job-opf", 0),
                                ("launchers/workflow-host-pin-job-core", 0),
                                ("launchers/workflow-host-pin-job-config-combined", 0),
                                ("launchers/workflow-host-pin-job-config-malformed", 0),
                                ("launchers/workflow-host-pin-job-config-system", 0),
                                ("launchers/workflow-block-hash-line", 0),
                                ("launchers/workflow-defaults-after-steps", 0),
                                ("launchers/workflow-python-outside-run-step", 0),
                                ("launchers/workflow-job-continue-on-error", 0),
                                ("launchers/workflow-outside-grammar", 0),
                                ("launchers/workflow-targets-hash-inside-word", 0),
                                ("launchers/real-workflows-in-grammar", 0),
                                ("launchers/workflow-interpreter-path-needs-probe", 0),
                                ("launchers/workflow-interpreter-spelling", 0),
                                ("launchers/workflow-hash-inside-word", 0), ("launchers/hook-command-exact", 0),
                                ("launchers/hook-args-exact", 0),
                                ("launchers/shell-target-unguarded", 0), ("launchers/shell-outside-repository", 0),
                                ("launchers/shell-same-name-unguarded", 0), ("launchers/shell-here-unbound", 0),
                                ("launchers/shell-dir-unbound", 0),
                                ("launchers/unreadable-dir-cannot-evaluate", 0),
                                ("launchers/unlistable-dir-cannot-evaluate", 0),
                                ("launchers/absent-declared-launcher", 0),
                                ("launchers/undecodable-cannot-evaluate", 0),
                                ("completeness/unclassified-hook-mode", 0),
                                ("completeness/unlisted-residual-is-finding", 0),
                                ("completeness/stale-residual-is-finding", 0),
                                ("completeness/gone-residual-is-finding", 0),
                                ("completeness/dynamic-load-cannot-evaluate", 0), ("completeness/switch-flips", 0),
                                ("completeness/scan-sees-every-api", 0), ("completeness/scan-sees-every-load", 0),
                                ("completeness/reviewed-load-target-pinned", 0),
                                ("completeness/reviewed-variable-target-limit", 0),
                                ("completeness/scan-follows-local-imports", 0),
                                ("completeness/scan-clean-module", 0)):
            check(check_id, vectors.get(check_id), sorted(key for key, ok in vectors.items() if not ok))
        # One vector per REBINDINGS form for _entry and for the guard function, each id written out.
        for check_id, _kind in (("entries/entry-rebound-tuple", 0), ("copies/guard-rebound-tuple", 0),
                                ("entries/entry-rebound-starred", 0), ("copies/guard-rebound-starred", 0),
                                ("entries/entry-rebound-if", 0), ("copies/guard-rebound-if", 0),
                                ("entries/entry-rebound-try", 0), ("copies/guard-rebound-try", 0),
                                ("entries/entry-rebound-while", 0), ("copies/guard-rebound-while", 0),
                                ("entries/entry-rebound-with", 0), ("copies/guard-rebound-with", 0),
                                ("entries/entry-rebound-for", 0), ("copies/guard-rebound-for", 0),
                                ("entries/entry-rebound-walrus", 0), ("copies/guard-rebound-walrus", 0),
                                ("entries/entry-rebound-except-as", 0), ("copies/guard-rebound-except-as", 0),
                                ("entries/entry-rebound-match", 0), ("copies/guard-rebound-match", 0),
                                ("entries/entry-rebound-import", 0), ("copies/guard-rebound-import", 0),
                                ("entries/entry-rebound-from-import", 0), ("copies/guard-rebound-from-import", 0),
                                ("entries/entry-rebound-global", 0), ("copies/guard-rebound-global", 0),
                                ("entries/entry-rebound-del", 0), ("copies/guard-rebound-del", 0),
                                ("entries/entry-rebound-attribute-store", 0),
                                ("copies/guard-rebound-attribute-store", 0),
                                ("entries/entry-rebound-namespace-store", 0),
                                ("copies/guard-rebound-namespace-store", 0)):
            check(check_id, vectors.get(check_id), sorted(key for key, ok in vectors.items() if not ok))
        with tempfile.TemporaryDirectory(prefix="child-status-coverage-selftest-") as base:
            gate_source, marker, rest = Path(__file__).read_text(encoding="utf-8").partition(
                "\n# ---------------------------------------------------------------------------------------------- "
                "self-test\n")
            exposed, uncaught = set(), []
            for number, (mutant, old, new, vector) in enumerate(MUTANTS, 1):
                if gate_source.count(old) != 1:
                    uncaught.append((mutant, "anchor found {} times".format(gate_source.count(old))))
                    continue
                module = _load_mutant(gate_source.replace(old, new) + marker + rest, base, number)
                failed = {key for key, ok in _vectors(module, trees, texts, canonical_tree).items() if not ok}
                exposed |= failed
                if vector not in failed:
                    uncaught.append((mutant, sorted(failed)))
            check("mutant/every-mutant-caught", MUTANTS and not uncaught, uncaught)
            never = sorted(set(vectors) - exposed - UNMUTATED)
            check("mutant/every-vector-discriminates", not never, never)
            behaviour = dict((check_id, (ok, detail)) for check_id, ok, detail in _behaviour(probe, base))
        for check_id, _kind in (("behaviour/opf-cli-refuses", 0), ("behaviour/launcher-cli-refuses", 0),
                                ("behaviour/coverage-gate-refuses", 0), ("behaviour/adopt-complete-refuses", 0),
                                ("behaviour/adopt-complete-self-test-refuses", 0),
                                ("behaviour/adopt-state-refuses", 0), ("behaviour/adopt-state-self-test-refuses", 0),
                                ("behaviour/import-complete-refuses", 0),
                                ("behaviour/import-complete-self-test-refuses", 0),
                                ("behaviour/gitleaks-pin-refuses", 0), ("behaviour/must-deliver-refuses", 0),
                                ("control/opf-cli-default-runs", 0),
                                ("control/launcher-cli-default-runs", 0), ("control/coverage-gate-default-runs", 0),
                                ("behaviour/opf-declaration-cli-refuses", 0), ("behaviour/declaration-gate-refuses", 0),
                                ("control/opf-declaration-cli-default-runs", 0),
                                ("control/declaration-gate-default-runs", 0),
                                ("revert/opf-declaration-guard-removed-red", 0),
                                ("revert/declaration-gate-guard-removed-red", 0),
                                ("behaviour/pretool-refuses", 0), ("behaviour/plugin-pretool-refuses", 0),
                                ("control/pretool-default-runs", 0), ("control/fail-open-default-runs", 0),
                                ("behaviour/fail-open-restores", 0), ("behaviour/python-reader-loses-refusal", 0),
                                ("behaviour/runner-first-gate-fails", 0), ("control/runner-default-passes", 0),
                                ("behaviour/opf-runner-first-gate-fails", 0), ("control/opf-runner-default-passes", 0),
                                ("revert/launcher-guard-removed-red", 0), ("revert/opf-guard-removed-red", 0),
                                ("revert/hook-guard-removed-red", 0), ("revert/probe-guard-removed-red", 0),
                                ("revert/coverage-guard-removed-red", 0), ("revert/runner-probe-removed-red", 0),
                                ("behaviour/opf-declaration-self-test-launches-nothing", 0),
                                ("control/audit-sees-every-launch", 0)):
            check(check_id, behaviour.get(check_id, (False, "not run"))[0], behaviour.get(check_id, (False, None))[1])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(ROOT)
        check("live/real-tree-passes", code == 0 and "PASS: child-status-coverage" in out.getvalue(),
              (code, out.getvalue()[-2000:], err.getvalue()))

    expected, problem = _registered_check_ids(suite)
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: {} {} of {} check(s) failed".format(suite, len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: {} {} checks".format(suite, len(results)))
    return 0


NAME = "check_child_status_coverage.py"


def _live_refusal():
    """Leg 1: the canonical guard, run in this process before anything else; it writes the refusal naming this
    gate and exits 2 itself. A canonical file that cannot be loaded is cannot-evaluate (2)."""
    try:
        canonical = _canonical_module()
    except (OSError, ImportError, SyntaxError, ValueError) as exc:
        print("error: cannot evaluate child-status coverage (cannot load {}: {}); fail-closed".format(
            CANONICAL_REL, type(exc).__name__), file=sys.stderr)
        return 2
    return canonical._child_status_refusal_exit(NAME)


def main(argv=None):
    if _live_refusal():
        return 2
    args = sys.argv[1:] if argv is None else argv
    report = None
    if len(args) >= 2 and args[-2] == "--execution-report" and os.path.isabs(args[-1]):
        args, report = args[:-2], args[-1]
    if args == ["--self-test"]:
        return self_test("coverage", report)
    if args == ["--self-test", "--probe"]:
        return self_test("probe", report)
    if not args and report is None:
        return run(ROOT)
    print("usage: check_child_status_coverage.py [--self-test [--probe]] [--execution-report ABS_PATH]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
