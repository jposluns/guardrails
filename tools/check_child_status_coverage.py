#!/usr/bin/env python3
"""Child-status coverage gate (child-status-coverage): every entry point either refuses to run when a child's exit
status cannot be read, is reached only through a launcher that does, reads no child status, or is listed as a
disclosed direct-launch residual.

With SIGCHLD ignored, the kernel reaps every child at exit and Python's subprocess module reports a failed child
as exit 0; the ignore survives exec, so a gate started by an ignoring parent reads every failure as a pass. The
canonical guard is opf/tools/_child_status.py (a disposition read, then a fork-and-wait probe; a refusal by name
with exit 2). This gate holds where it runs:

  1. live: the canonical guard runs in this process first, in every mode; a refusal exits 2 by name.
  2. copies: the inline copies in opf/tools/opf.py and tools/check_selftest_execution.py (the constant and both
     functions) and in the hooks dispatcher and its generated plugin copy (the constant and the probe) are each
     defined once at the top level and AST-identical (ast.dump, docstrings included) to the canonical text.
  3. entries: each CLI copy's __main__ body is exactly sys.exit(_child_status_refusal_exit("<basename>") or
     main()), the canonical file's entry ends in that line, and each dispatcher's main() holds HOOK_BLOCK as the
     statements right after its is_fail_open assignment.
  4. launchers: the first run_gate line of tools/run_all_checks.sh and of opf/tools/run_all_checks.sh is the
     probe, and no non-comment line above it names python3 or a .py file (read in line order through
     check_ci_parity.read_runner_text); in every job of every workflow under .github/workflows/ with a
     non-comment line that names python3 or a .py file, the first step after the setup-python step is
     PROBE_STEP; every hook command registered in the plugin's hooks.json is exactly "command": "python3" with
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
concurrent.futures.process too), any attribute or from-import of os or posix (or an alias of either) named
fork, forkpty, wait, wait3, wait4, waitid, waitpid, system, popen, posix_spawn, posix_spawnp, spawn* or exec*,
a star import from os or posix, and any name, attribute or asyncio or concurrent.futures from-import spelled
create_subprocess_exec, create_subprocess_shell, subprocess_exec, subprocess_shell (the asyncio loop methods)
or ProcessPoolExecutor. Local imports are followed from the importing file's own directory, then tools/ and
opf/tools/ (imports inside functions included; a package is read at its __init__.py only). A call that loads a
module or code at run time (__import__ as a name or an attribute, exec, eval, import_module,
spec_from_file_location, exec_module, load_module, run_path, run_module) in a file the scan would otherwise
call NOT-APPLICABLE is cannot-evaluate, unless the file's row in DYNAMIC_LOADS_REVIEWED names every load call
in it by its exact text (ast.unparse), so a changed load target unreviews the file.

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
only at entry, so the in-situ checks in opf/tools/_opf_emit.py stay the only later defence and they read the
disposition only; R2 a child that ignores SIGCHLD itself loses its own children's statuses (opf.py's own
self-test does this in process for fixtures that test the refusal), which no parent guard sees and this gate
does not police; R3 non-Python tools (gh, gitleaks, jq, curl, tar, sha256sum) were not tested under an
inherited ignore, and their statuses are read by bash or by guarded gates; R4 a refusal is an exit status, so a
Python parent that ignores SIGCHLD reads it as 0 (the self-test witnesses this), and the hook platform's own
reader was not observed, the stderr line being the second channel; R5 whether SA_NOCLDWAIT loses statuses on
other kernels (macOS) is not established, and the fork probe covers it only at the choke points; R6 on a
platform without SIGCHLD or fork the guard refuses nothing; R7 a direct launch of a LAUNCHER or RESIDUAL entry
is unguarded until the floor prelude refuses (DIRECT_LAUNCH_RESIDUAL); R8 tools/run_all_checks.sh continues
after the probe fails, so later gates may print PASS lines in a run that ends RESULT: FAIL; R9 a fork in a
multi-threaded process warns, so every guard runs at entry or at the top of the dispatcher's main; R10 the
static scan over-approximates through whole-closure imports, and it does not see a spawn reached (a) inside a
module it does not read, the standard library or a third-party package (platform.architecture and
ctypes.util.find_library each start a child through subprocess), (b) through getattr, a variable or any other
computed name, (c) in a package submodule no import names (a package is read at its __init__.py only), (d) in
a C extension or through ctypes, or (e) through a star import from a module other than os, posix or a spawn
module; R11 process-group and child-subreaper interplay (PR_SET_CHILD_SUBREAPER) is not checked; R12 a shell
launcher's Python launch is recognised by a .py word on a non-comment line only, an override such as OPF_TOOL
set by the operator replaces the default the gate resolves, here and dir are read from their plain here= and
dir= assignments only, and a relative word is resolved from the repository root, so a launcher started from
another directory reads it from there. Whether a real launcher starts these tools with SIGCHLD ignored, and whether the steps of
one CI job share one disposition, are not established; the per-job probe tests the latter per job.
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
CLI_COPIES = (("opf/tools/opf.py", "opf.py"), ("tools/check_selftest_execution.py", "check_selftest_execution.py"))
HOOK_COPIES = (".aiqt/core/hooks/scripts/aiqt_hooks.py", "plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py")
HOOKS_JSON_REL = "plugin/aiqt-guardrails-hooks/hooks/hooks.json"
HOOK_TARGET = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/aiqt_hooks.py"
# The one accepted hook registration, as tools/gen_hooks.py writes it: "command" is exactly HOOK_COMMAND and
# "args" is exactly HOOK_ARGS followed by one mode.
HOOK_COMMAND = "python3"
HOOK_ARGS = ["-I", HOOK_TARGET]
# The entries that carry the pinned guard: the canonical file, this gate (leg 1 runs first in main), the two CLI
# copies and the two dispatcher copies.
SELF_ENTRIES = frozenset((CANONICAL_REL, COVERAGE_REL) + tuple(rel for rel, _name in CLI_COPIES) + HOOK_COPIES)
CLI_ENTRY = 'sys.exit(_child_status_refusal_exit("{}") or main())'
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
PROBE_STEP = ("- name: Child exit status observable (before any gate)", "shell: bash",
              "run: python3 -I -B " + CANONICAL_REL)
SETUP_PYTHON_RE = re.compile(r"\s*- uses: actions/setup-python@")
JOB_RE = re.compile(r"  ([A-Za-z0-9_-]+):")
PYTHON_RUN_RE = re.compile(r"(?<![\w./-])python3?(?=\s)")
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
DYNAMIC_ATTRS = frozenset(("__import__", "import_module", "spec_from_file_location", "exec_module", "load_module",
                           "run_path", "run_module"))
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
    "opf/tools/check_opf_upgrade.py",
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
    "tools/gen_renderers.py",
    "tools/gen_rules.py",
    "tools/gen_skill.py",
    "tools/gen_worker_pack.py",
    "tools/import_cwe.py",
    "tools/migrate.py",
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
# loaded). The call text carries the load target, so a file whose load calls or their targets differ from its
# row is unreviewed again (cannot-evaluate when nothing else in it reads a child's status), and a row that no
# longer matches its file is a stale finding. A target held in a variable is pinned by the expression the call
# spells, not by the value assigned to it elsewhere.
DYNAMIC_LOADS_REVIEWED = (
    ("opf/tools/check_opf_init_contract.py", ("runpy.run_path(str(ROOT / 'opf/tools/_opf_init_contract.py'))",),
     ("opf/tools/_opf_init_contract.py",), "runpy.run_path of the literal opf/tools/_opf_init_contract.py"),
    ("tools/check_footer.py", ("exec(compile(source.replace(new, old), __file__, 'exec'), reverted)",),
     ("tools/check_footer.py",), "its own source with one check reverted, compiled and run in its self-test"),
    ("tools/check_opf_enforce_platforms.py",
     ("importlib.util.spec_from_file_location('_opf_enforce_platforms_hook', str(path))",
      "spec.loader.exec_module(module)"),
     ("opf/enforcement/claude/pretooluse_deny.py",), "the Claude Code deny hook, loaded and never run"),
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
    out = []
    for name in names:
        nodes = _top_definitions(tree, name)
        if len(nodes) != 1:
            out.append("{}: the inline copy of {} is defined {} times at the top level, not once".format(
                rel, name, len(nodes)))
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

def _names_python(line):
    """True when a runner or workflow line, outside a trailing comment, names python3 or a .py file."""
    code = line.split("#", 1)[0]
    return bool(PYTHON_RUN_RE.search(code) or PY_WORD_RE.search(code))


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


def workflow_findings(rel, text):
    """Findings for every job that names python3 or a .py file without the probe as the first step after
    setup-python."""
    lines = text.splitlines()
    launches = any(_names_python(line) for line in lines)
    if "jobs:" not in lines:
        return ["{}: launches python3 with no top-level jobs: line".format(rel)] if launches else []
    jobs = []
    for line in lines[lines.index("jobs:") + 1:]:
        match = JOB_RE.fullmatch(line)
        if match:
            jobs.append((match.group(1), []))
        elif line and not line.startswith((" ", "#")):
            break
        elif jobs:
            jobs[-1][1].append(line)
    out = []
    for job, body in jobs:
        if not any(_names_python(line) for line in body):
            continue
        setup = [index for index, line in enumerate(body) if SETUP_PYTHON_RE.match(line)]
        if len(setup) != 1:
            out.append("{}: job {} launches Python with {} setup-python steps, not one".format(rel, job, len(setup)))
            continue
        step = next((index for index in range(setup[0] + 1, len(body))
                     if body[index].lstrip().startswith("- ")), None)
        got = tuple(line.strip() for line in body[step:step + len(PROBE_STEP)]) if step is not None else ()
        if got != PROBE_STEP:
            out.append("{}: job {}: the first step after setup-python is not the child-status probe step".format(
                rel, job))
    return out


def workflow_targets(text):
    return {match.group(1) for line in text.splitlines() for match in PY_LAUNCH_RE.finditer(line.split("#", 1)[0])}


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

def module_reads(tree):
    """(reads, dynamic loads, imports) of one module: reads as sorted names, dynamic loads as the sorted exact
    text (ast.unparse) of each load call, imports as (module name, level) pairs, imports inside functions
    included."""
    os_names = set(OS_MODULES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names.update(alias.asname for alias in node.names if alias.name in OS_MODULES and alias.asname)
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
            if isinstance(node.value, ast.Name) and node.value.id in os_names and OS_SPAWN_RE.fullmatch(node.attr):
                reads.add("os." + node.attr)
            if node.attr in SPAWN_NAMES:
                reads.add(node.attr)
        elif isinstance(node, ast.Name):
            if node.id in SPAWN_NAMES:
                reads.add(node.id)
        elif isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id in DYNAMIC_NAMES:
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
    pairs. A file whose load calls, by exact call text and so by target, are exactly its reviewed set
    (DYNAMIC_LOADS_REVIEWED) has them followed as imports of the files they load instead."""
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
    for rel, name in CLI_COPIES:
        tree = _parse(_read(root, rel), rel)
        findings.extend(copy_findings(rel, tree, tmpl, CANONICAL_NAMES))
        finding = cli_entry_finding(rel, tree, name)
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
            + SHELL_REAL}
    opf, selftest, hook = real["opf/tools/opf.py"], real["tools/check_selftest_execution.py"], real[HOOK_COPIES[0]]
    trees = {"canonical": ast.parse(real[CANONICAL_REL]), "opf": ast.parse(opf), "selftest": ast.parse(selftest),
             "hook": ast.parse(hook)}
    opf_entry = CLI_ENTRY.format("opf.py")
    for key, source in (
            ("opf-drift", _swap(opf, "    if waited != pid or", "    if waited == pid or")),
            ("selftest-missing", _swap(selftest, "def _child_status_refusal_exit(name):",
                                       "def _child_status_refusal_exit_gone(name):")),
            ("hook-drift", _swap(hook, "_CHILD_STATUS_PROBE_CODE = 77", "_CHILD_STATUS_PROBE_CODE = 78")),
            ("opf-entry-bare", _swap(opf, "    " + opf_entry + "\n", "    sys.exit(main())\n")),
            ("opf-entry-extra", _swap(opf, "    " + opf_entry + "\n", "    print('entry')\n    " + opf_entry + "\n")),
            ("hook-block-open", _swap(hook, "            return 2\n        signal.signal(signal.SIGCHLD,",
                                      "            return 0\n        signal.signal(signal.SIGCHLD,")),
            ("hook-block-gone", _swap(hook, "    unobservable = _child_status_refusal()\n", "    unobservable = None\n"))):
        trees[key] = ast.parse(source)
    probe_line = RUNNER_PROBES[0][1] + "\n"
    opf_probe = 'run_gate "child-status"                python3 -I -B "$here/_child_status.py"\n'
    quality = real[WORKFLOWS_REL + "/quality.yml"]
    step = "      " + PROBE_STEP[0] + "\n        " + PROBE_STEP[1] + "\n        " + PROBE_STEP[2] + "\n"
    lone = "jobs:\n  lone:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Gate\n        shell: bash\n"
    here_line = 'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2\n'
    texts = {
        "runner": real[LOCAL_RUNNER], "runner-moved": _swap(real[LOCAL_RUNNER], probe_line, ""),
        "runner-prelude-launch": _swap(real[LOCAL_RUNNER], probe_line,
                                       "python3 -I -B tools/check_leaks.py\n" + probe_line),
        "opf-runner": real[OPF_RUNNER], "opf-runner-moved": _swap(real[OPF_RUNNER], opf_probe, ""),
        "quality": quality, "quality-step-gone": quality.replace(step, "", 1),
        "no-setup": lone + "        run: python3 -I -B tools/check_leaks.py\n",
        "shebang-job": lone + "        run: tools/check_leaks.py\n",
        "shell-unguarded": "#!/bin/sh\n# python3 tools/opf.py in a comment is not a launch\n"
                           "python3 -I -B tools/check_leaks.py\n",
        "shell-outside": "#!/bin/sh\npython3 -I -B /tmp/opf.py\n" + here_line
                         + 'python3 -I -B "$here/../../../../opf/tools/opf.py"\npython3 -I -B "$OPF_DIR/opf.py"\n',
        "shell-same-name": "#!/bin/sh\n" + here_line + "python3 -I -B tools/opf.py\n"
                           'opf_tool=${OPF_TOOL:-$here/../tools/opf.py}\n',
        "shell-here-unbound": _swap(real[SHELL_REAL[0]], here_line, "here=$(pwd)\n"),
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
                "loop.subprocess_shell(None, 'true')\n", "from asyncio import *\ncreate_subprocess_exec('true')\n")
# A run-time load per line: each, alone in a module, is a dynamic load the scan must see.
LOAD_SNIPPETS = ("__import__('subprocess')\n", "import importlib\nimportlib.__import__('subprocess')\n",
                 "import builtins\nbuiltins.__import__('subprocess')\n", "exec('pass')\n", "eval('1')\n",
                 "import importlib\nimportlib.import_module('subprocess')\n", "import runpy\nrunpy.run_path('x.py')\n",
                 "import runpy\nrunpy.run_module('x')\n", "spec.loader.exec_module(module)\n",
                 "loader.load_module('x')\n",
                 "import importlib.util\nimportlib.util.spec_from_file_location('x', 'x.py')\n")
# A synthetic entry tree for the completeness vectors: (rel, source).
SYNTHETIC = (("tools/new_reader.py", "import helper_mod\n"), ("tools/helper_mod.py", "import subprocess\n"),
             ("tools/plain.py", "import json\n"), ("tools/dynamic.py", "import importlib\nimportlib.import_module(name)\n"),
             ("tools/launched.py", "import os\nos.waitpid(1, 0)\n"),
             ("tools/loader.py", "import runpy\nrunpy.run_path('tools/helper_mod.py')\n"),
             ("tools/loader_moved.py", "import runpy\nrunpy.run_path('tools/plain.py')\n"))
# One reviewed row per loader, both naming the load of tools/helper_mod.py; tools/loader_moved.py loads another.
SYNTHETIC_REVIEWED = {rel: (("runpy.run_path('tools/helper_mod.py')",), ("tools/helper_mod.py",))
                      for rel in ("tools/loader.py", "tools/loader_moved.py")}


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

    vectors = (
        ("copies/real-copies-identical", lambda: not copies("opf", m.CANONICAL_NAMES)
         and not copies("selftest", m.CANONICAL_NAMES) and not copies("hook", m.HOOK_NAMES)),
        ("copies/drift-is-finding", lambda: any("_child_status_refusal differs" in finding
                                                for finding in copies("opf-drift", m.CANONICAL_NAMES))),
        ("copies/missing-copy-is-finding", lambda: any("defined 0 times" in finding
                                                       for finding in copies("selftest-missing", m.CANONICAL_NAMES))),
        ("copies/hook-drift-is-finding", lambda: any("_CHILD_STATUS_PROBE_CODE differs" in finding
                                                     for finding in copies("hook-drift", m.HOOK_NAMES))),
        ("entries/real-entries-pass", lambda: m.cli_entry_finding("opf", trees["opf"], "opf.py") is None
         and m.cli_entry_finding("canonical", canonical_tree, "_child_status.py", exact=False) is None
         and m.hook_block_finding("hook", trees["hook"]) is None),
        ("entries/cli-line-shape", lambda: m.cli_entry_finding("opf", trees["opf-entry-bare"], "opf.py") is not None),
        ("entries/cli-extra-statement",
         lambda: m.cli_entry_finding("opf", trees["opf-entry-extra"], "opf.py") is not None),
        ("entries/hook-block-shape", lambda: m.hook_block_finding("hook", trees["hook-block-open"]) is not None),
        ("entries/hook-block-missing", lambda: m.hook_block_finding("hook", trees["hook-block-gone"]) is not None),
        ("launchers/real-launchers-pass",
         lambda: m.runner_first_gate_finding("runner", texts["runner"], local_probe) is None
         and m.runner_first_gate_finding("opf", texts["opf-runner"], opf_probe) is None
         and not m.workflow_findings("quality", texts["quality"])
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
            "quality", texts["quality-step-gone"]))),
        ("launchers/workflow-no-setup", lambda: any("0 setup-python" in finding for finding in m.workflow_findings(
            "lone", texts["no-setup"]))),
        ("launchers/workflow-names-py-file", lambda: any("0 setup-python" in finding for finding in
                                                         m.workflow_findings("lone", texts["shebang-job"]))),
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
        ("completeness/scan-follows-local-imports",
         lambda: reads_of("tools/new_reader.py")[0] == [("tools/helper_mod.py", "import subprocess")]),
        ("completeness/scan-clean-module", lambda: reads_of("tools/plain.py") == ([], [])),
    )
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
    ("copy-count", "        if len(nodes) != 1:\n            out.append(", "        if False:\n            out.append(",
     "copies/missing-copy-is-finding"),
    ("cli-entry-call", "or ast.dump(block.body[-1]) != want", "or False", "entries/cli-line-shape"),
    ("cli-entry-exact", "or (exact and len(block.body) != 1)", "or False", "entries/cli-extra-statement"),
    ("hook-block-compare", "    if got != want:\n", "    if False:\n", "entries/hook-block-shape"),
    ("runner-first", "if line.split() != probe_line.split():", "if False:", "launchers/runner-first-gate"),
    ("runner-prelude", "        if _names_python(line):\n            return", "        if False:\n            return",
     "launchers/runner-prelude-launch"),
    ("workflow-step", "        if got != PROBE_STEP:\n", "        if False:\n", "launchers/workflow-first-step"),
    ("workflow-setup", "        if len(setup) != 1:\n", "        if False:\n", "launchers/workflow-no-setup"),
    ("workflow-py-word", "return bool(PYTHON_RUN_RE.search(code) or PY_WORD_RE.search(code))",
     "return bool(PYTHON_RUN_RE.search(code))", "launchers/workflow-names-py-file"),
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
    ("scan-os", "and node.value.id in os_names and OS_SPAWN_RE.fullmatch(node.attr):",
     "and False:", "completeness/scan-sees-every-api"),
    ("scan-os-star", "os_spawn = alias.name == \"*\" or OS_SPAWN_RE.fullmatch(alias.name)",
     "os_spawn = OS_SPAWN_RE.fullmatch(alias.name)", "completeness/scan-sees-every-api"),
    ("scan-names", "            if node.id in SPAWN_NAMES:\n", "            if False:\n",
     "completeness/scan-sees-every-api"),
    ("scan-load-attr", "            elif isinstance(callee, ast.Attribute) and callee.attr in DYNAMIC_ATTRS:\n",
     "            elif False:\n", "completeness/scan-sees-every-load"),
    ("reviewed-target", "and tuple(loads) == reviewed[current][0]:", "and len(loads) == len(reviewed[current][0]):",
     "completeness/reviewed-load-target-pinned"),
    ("scan-closure", "                stack.append(target)\n", "                pass\n",
     "completeness/scan-follows-local-imports"),
)
UNMUTATED = {"copies/real-copies-identical", "entries/real-entries-pass", "launchers/real-launchers-pass",
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
            (COVERAGE_REL, ("--usage",), Path(COVERAGE_REL).name, "usage: check_child_status_coverage.py"))
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
                                ("behaviour/coverage-gate-refuses", COVERAGE_REL, "check_child_status_coverage.py")):
        out.append((check_id, refused(results[rel][0], name), results[rel][0]))
    for check_id, rel, marker in (("control/opf-cli-default-runs", "opf/tools/opf.py", "opf: the OPFiles"),
                                  ("control/launcher-cli-default-runs", "tools/check_selftest_execution.py",
                                   "usage: check_selftest_execution.py"),
                                  ("control/coverage-gate-default-runs", COVERAGE_REL,
                                   "usage: check_child_status_coverage.py")):
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
    tree = Path(base) / "guardless-runner"
    _copy(CANONICAL_REL, tree)
    text = _truncated_runner((ROOT / LOCAL_RUNNER).read_text(encoding="utf-8"), 'if [ "$failed" -ne 0 ]; then')
    runner = _copy(LOCAL_RUNNER, tree, _swap(text, RUNNER_PROBES[0][1] + "\n", ""))
    got = via("ignore", ["bash", str(runner)], cwd=str(tree))
    out.append(("revert/runner-probe-removed-red", got[0] == 0 and "RESULT: PASS" in got[1], got))
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
                                 ("layer/lost-status-seen-by-probe", 5), ("layer/no-fork-nothing-probed", 6)):
            check(check_id, got.get(check_id, (False, "not run"))[0], got.get(check_id, (False, "not run"))[1])
    else:
        this = sys.modules[__name__]
        trees, texts = _inputs()
        canonical_tree = trees["canonical"]
        vectors = _vectors(this, trees, texts, canonical_tree)
        for check_id, _kind in (("copies/real-copies-identical", 0), ("copies/drift-is-finding", 0),
                                ("copies/missing-copy-is-finding", 0), ("copies/hook-drift-is-finding", 0),
                                ("entries/real-entries-pass", 0), ("entries/cli-line-shape", 0),
                                ("entries/cli-extra-statement", 0), ("entries/hook-block-shape", 0),
                                ("entries/hook-block-missing", 0), ("launchers/real-launchers-pass", 0),
                                ("launchers/runner-first-gate", 0), ("launchers/opf-runner-first-gate", 0),
                                ("launchers/runner-prelude-launch", 0),
                                ("launchers/workflow-first-step", 0), ("launchers/workflow-no-setup", 0),
                                ("launchers/workflow-names-py-file", 0), ("launchers/hook-command-exact", 0),
                                ("launchers/hook-args-exact", 0),
                                ("launchers/shell-target-unguarded", 0), ("launchers/shell-outside-repository", 0),
                                ("launchers/shell-same-name-unguarded", 0), ("launchers/shell-here-unbound", 0),
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
                                ("completeness/scan-follows-local-imports", 0),
                                ("completeness/scan-clean-module", 0)):
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
                                ("behaviour/coverage-gate-refuses", 0), ("control/opf-cli-default-runs", 0),
                                ("control/launcher-cli-default-runs", 0), ("control/coverage-gate-default-runs", 0),
                                ("behaviour/pretool-refuses", 0), ("behaviour/plugin-pretool-refuses", 0),
                                ("control/pretool-default-runs", 0), ("control/fail-open-default-runs", 0),
                                ("behaviour/fail-open-restores", 0), ("behaviour/python-reader-loses-refusal", 0),
                                ("behaviour/runner-first-gate-fails", 0), ("control/runner-default-passes", 0),
                                ("behaviour/opf-runner-first-gate-fails", 0), ("control/opf-runner-default-passes", 0),
                                ("revert/launcher-guard-removed-red", 0), ("revert/opf-guard-removed-red", 0),
                                ("revert/hook-guard-removed-red", 0), ("revert/probe-guard-removed-red", 0),
                                ("revert/coverage-guard-removed-red", 0), ("revert/runner-probe-removed-red", 0)):
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
