#!/usr/bin/env python3
"""Behavioural self-test for tools/_git_fixture_env.py and its routing across the fixture class.

An inherited repository-selecting git variable (GIT_INDEX_FILE, which git itself exports to hook
children; GIT_DIR; GIT_WORK_TREE; the GIT_CONFIG_* family; ...) redirects a self-test fixture's
git init/add/commit into the CALLER's repository. This suite locks the fix in four layers. First,
unit checks on the shared scrub itself: every GIT_-prefixed variable is dropped (allowlist, so a
variable outside the enumerated family cannot slip through), the config pins and the scratch
HOME / XDG_CONFIG_HOME are applied, an explicit override survives, a non-git variable is preserved,
the in-place form is exercised in a child interpreter under a poisoned environment, and the
caller-env companion (caller_env_without_git()) is proven to KEEP the caller's HOME and global
config (the safe.directory trust surface a REAL-repository read needs) while still dropping every
GIT_* variable and leaving the process environment scrubbed. Second, ROUTING checks: each member of
the fixture class is statically confirmed to call its scrub (per member, so removing one member's
routing fails at that member's own named check), tightened by function-SCOPED checks (the named
self-test entry itself calls the in-place scrub as a top-level statement with no process-launching
call before it), explicit hook-fixture env checks, an OPF inherited-env probe, and TRUST checks
(every literal `git ... archive ...` launch in the two members that read the checkout under test
passes env=caller_env_without_git(), so the scrub cannot regress
a foreign-owned checkout to a rc=128 refusal). Third, an end-to-end leak probe: the originally
defective fixture suite (tools/selftest_aiqt_corpus.py) runs to green under a poisoned
GIT_INDEX_FILE and under a poisoned GIT_DIR, through the direct __main__ run AND the imported
run_self_test() surface (which bypasses the entry-point scrub), each aimed at a decoy caller
repository built here, and the decoy's .git content is asserted BYTE-IDENTICAL afterwards, so the
leak cannot silently return. Fourth, a repo-wide maintenance-pin tripwire scan (F-367) over
the launch forms it can resolve (its residual is disclosed at the scan): every git launch
under tools/ and opf/tools/ must be a direct
subprocess list-argv call this scan can resolve, a resolved maintenance-triggering launch must
carry the EFFECTIVE F-367 argv pins (`-c key=value` pairs in option position), route through a
covered environment or a CALLED scrub, or carry a written justification, and every launch the
scan cannot resolve (an os-level or shell-string launch, a helper-built or parameterized argv,
an alias spelling) is itself a loud cannot-evaluate finding unless justified, so an omitted
fixture harness (the class member a hand enumeration misses) or an unreviewable launch form
fails this suite instead of racing CI.

Verdicts use child return codes and byte comparisons, never output tokens. DISCLOSED RESIDUAL: a
routing/scope check proves the scrub call site and its position in the named entry, not that every
later git call in the same process still runs under it; launch aliases, indirect helper calls,
early returns and control-flow reachability are outside this syntactic check's coverage. The
config-injection lane derives registered self-test commands and runs them under caller hooks and ignore
files, attributes and fsmonitor, with separate malformed-config probes. Selection is by
selftest_ script name or --self-test/--selftest/--suite flag, not by behaviour: a
registered gate that builds fixtures only through another entry mode is outside this roster;
the repository-selector lanes below exercise the corpus member. The trust check covers LITERAL
subprocess.run launches (a launch built
through a variable is outside its reach); and the end-to-end probe poisons two representative
variables, the allowlist unit checks covering the rest of the GIT_-prefixed family.

This module is the suite family's SHARED ENGINE: the checks run through four registered runners, each
of which assigns SUITE_ID and hands its lane callable to main(). tools/selftest_git_fixture_env_core.py
carries the scrub unit checks, the routing/scope/trust checks, the roster checks, the
member-timeout and member-close controls, the maintenance-pin scan and the end-to-end leak
probe; tools/selftest_git_fixture_env_config_combined.py, tools/selftest_git_fixture_env_config_malformed.py
and tools/selftest_git_fixture_env_config_system.py carry the three config-injection lanes,
split into their own suites so they run in parallel in CI. Run directly, this module runs
nothing and exits 2 (cannot evaluate).

  <runner>.py                              exit 0 on pass, 1 on assertion failure
  <runner>.py --execution-report ABS_PATH  also write the executed check IDs as JSON

Exit 2 is cannot-evaluate: a harness/setup error, including bad arguments, no git binary, no writable
temp directory, a failed report write, or an unreadable, malformed, or suite-missing expectation
manifest; and a bounded child that reached its time bound (see _run_bounded): a config-injection
member (_run_config_member) or a corpus leak lane (_run_corpus, _run_corpus_imported); and an OPF
lifecycle probe still running at its own bound (_lifecycle_probe in
tools/selftest_git_fixture_env_config_system.py, recorded the same way, its message naming the last
stage the probe reported). A member
reached its time bound, so this run is no verdict on that member: the timeout is listed under SELF-TEST
CANNOT EVALUATE, never as a failure of that member. A finding is folded into the cannot-evaluate
list only when it is attributed to a recorded timeout (see _timeout_cause and
_registered_offenders: the finding's own result is that timeout's message, which names the member,
or it lists only members whose run timed out with no hook, monitor or exposure evidence, or, for a
check over several scenarios, every scenario that did not time out matched; see
_partial_timeout_cause); every other finding is definite and outranks cannot-evaluate, so the run
exits 1 (SELF-TEST FAIL, with the timeouts listed beside it). TIMING ALONE IS NEVER A DEFINITE
FINDING: an observation whose only evidence of a defect is timing (a deadline missed, a signal
late, a signal the harness owed but never sent, a completion budget exceeded) is recorded under
cannot-evaluate, which fails the run closed and is never a pass; a definite finding needs
non-timing evidence the code under test produced (state it left, an exit status, a refusal
record or a message). An observation carrying ANY recorded timeout keeps only the explicit
_STATE_EVIDENCE allowlist of final-state fields compared and stands every other field down
(_scenario_timeout_record), so no timeout site decides per field what stays definite. A definite finding over a lane never
names a member whose run only timed out: that member is listed by its own timeout message under
cannot evaluate (_registered_offenders, _lifecycle_finding). tools/check_selftest_execution.py treats any child exit outside {0, 1}
as CANNOT EVALUATE and itself exits 2 (its self-test leg st/child-rc2-2), so a timeout never reads as
a pass or as a code failure there either.

Only those children are bounded this way (the lifecycle probe keeps its own subprocess timeout and
records reaching it as above). The suite's own git probes and fixture setup commands
keep their 60 s subprocess timeouts, and reaching one of those raises (a traceback, exit 1) instead
of becoming cannot-evaluate; the other helper children keep the timeout handling each documents.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_fixture_env  # noqa: E402
import _selftest_exit_report  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opf" / "tools"))
import _optlevel  # noqa: E402  level-0 parses for the mutants, shared with opf/tools

ROOT = Path(__file__).resolve().parents[1]
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
CORPUS_SELFTEST = ROOT / "tools" / "selftest_aiqt_corpus.py"
# Assigned by each registered runner before main() runs; None fails _expected_check_ids
# closed (exit 2), so running the engine with no runner never reads as a pass.
SUITE_ID = None

# The four registered runners over this engine; each assigns SUITE_ID and hands its lane
# callable to main(). The config-lane exclusions below keep every one of them out of the
# config-injection roster, so no lane ever re-runs a git-fixture-env suite recursively.
_SUITE_RUNNERS = (
    "tools/selftest_git_fixture_env_core.py",
    "tools/selftest_git_fixture_env_config_combined.py",
    "tools/selftest_git_fixture_env_config_malformed.py",
    "tools/selftest_git_fixture_env_config_system.py",
)
FAILURES = []
CANNOT_EVALUATE = []
# finding text -> the timeout messages it is attributed to (see _verdict).
TIMEOUT_CAUSED = {}
EXECUTED = []
_EXECUTED_SET = set()

# The repository-selecting family a caller can export (git itself exports GIT_INDEX_FILE, and can
# export GIT_DIR and GIT_WORK_TREE, to hook children), plus config injection and a NOVEL name the
# allowlist must also drop. Every value is a decoy the scrubbed result must not carry.
POISON_VARS = {
    "GIT_INDEX_FILE": "/decoy/.git/index",
    "GIT_DIR": "/decoy/.git",
    "GIT_WORK_TREE": "/decoy",
    "GIT_COMMON_DIR": "/decoy/.git",
    "GIT_OBJECT_DIRECTORY": "/decoy/.git/objects",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/decoy/.git/objects",
    "GIT_NAMESPACE": "decoy-namespace",
    "GIT_CEILING_DIRECTORIES": "/decoy",
    "GIT_CONFIG_PARAMETERS": "'core.hooksPath=/decoy/hooks'",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.hooksPath",
    "GIT_CONFIG_VALUE_0": "/decoy/hooks",
    "GIT_CONFIG_GLOBAL": "/decoy/gitconfig",
    "GIT_CONFIG_SYSTEM": "/decoy/gitconfig",
    "GIT_FIXTURE_NOVEL_VARIABLE": "novel",
}
SENTINEL = "AIQT_FIXTURE_ENV_SENTINEL"


def check(name, got, want, cause=None):
    """Record one check. A mismatch whose result is wholly a timeout (cause, or _timeout_cause(got)
    when cause is None) is attributed to those timeout messages in TIMEOUT_CAUSED; _verdict folds
    it into cannot-evaluate only when each of them was recorded."""
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        finding = "{}: got {!r}, want {!r}".format(name, got, want)
        FAILURES.append(finding)
        cause = _timeout_cause(got) if cause is None else tuple(cause)
        if cause:
            TIMEOUT_CAUSED[finding] = cause


def _is_timeout(value):
    return isinstance(value, str) and value.startswith("TIMEOUT: ")


def _timeout_cause(got):
    """The timeout messages a mismatching member result is wholly explained by, or () when any
    part of it is not: the result is a timeout message (which names the member), or it is a list
    of one member's registered argument variants (_member_result's exit-code column) each either
    exit 0 or a timeout message."""
    if _is_timeout(got):
        return (got,)
    if isinstance(got, list) and any(_is_timeout(value) for value in got) and all(
            _is_timeout(value) or (type(value) is int and value == 0) for value in got):
        return tuple(value for value in got if _is_timeout(value))
    return ()


def _poison_environ():
    """Set every POISON_VARS entry plus the non-git sentinel in os.environ; return a restorer."""
    saved = {}
    for key, value in list(POISON_VARS.items()) + [(SENTINEL, "kept")]:
        saved[key] = os.environ.get(key)
        os.environ[key] = value

    def restore():
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    return restore


def _calls_any(member_path, callable_names):
    """True when the member's source carries a DIRECT call to one of callable_names (a bare name or
    an attribute), read from a parsed AST rather than a text match, so a mention in a comment or a
    string can never satisfy the routing check. A member that is unreadable or does not parse is a
    loud failure value, never a silent pass."""
    try:
        tree = ast.parse(member_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return "unreadable or unparseable: {}".format(exc)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in callable_names:
            return True
    return False


_LAUNCH_NAMES = frozenset({
    "run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
})


def _scrub_scoped_first(member_path, func_name, scrub_name):
    """True when the named function calls scrub_name as a TOP-LEVEL statement of its OWN body
    (function-scoped: a call anywhere else in the file cannot satisfy this) with no
    recognized direct process-launching call before it. This is a syntactic check, with
    the indirect-call and reachability residuals documented above. Read from a parsed AST; a missing
    or duplicated function, a missing top-level scrub, an earlier launch call, or an unreadable
    or unparseable member is a loud failure value, never a silent pass."""
    try:
        tree = ast.parse(member_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return "unreadable or unparseable: {}".format(exc)

    def _call_name(node):
        func = node.func
        return func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)

    owner = tree
    for part in func_name.split("."):
        funcs = [n for n in owner.body
                 if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == part]
        if len(funcs) != 1:
            return "{} definitions of {} found, want exactly 1".format(len(funcs), func_name)
        owner = funcs[0]
    if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "{} is not a function".format(func_name)
    body = owner.body
    # Lifecycle wrappers may put the context inside try/finally for extra restoration.
    body = [inner for stmt in body
            for inner in (stmt.body if isinstance(stmt, ast.Try) else [stmt])]
    for stmt in body:
        if (isinstance(stmt, ast.With) and any(
                isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr) == scrub_name for item in stmt.items)):
            return True
        if (isinstance(stmt, (ast.Expr, ast.Assign)) and isinstance(stmt.value, ast.Call)
                and _call_name(stmt.value) == scrub_name):
            return True
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and _call_name(node) in _LAUNCH_NAMES:
                return "{}(): a process-launching call ({}) precedes the {}() scrub".format(
                    func_name, _call_name(node), scrub_name)
    return "{}(): no top-level {}() call".format(func_name, scrub_name)


def _archive_reads_use_caller_env(member_path, fixture_calls=False):
    """True when EVERY literal subprocess.run(["git", ..., "archive", ...]) launch in the member
    passes env=caller_env_without_git(): the REAL-repository reads must carry the caller's trust
    configuration (a safe.directory entry in the caller's global config), which the member's
    in-place fixture scrub drops from the process environment. A member with no such launch, or
    a launch with a missing or different env, is a loud failure value, never a silent pass.
    With fixture_calls=True, check literal git launches and the hook suite's _git cmd
    builder for git_fixture_env() instead. Other computed commands remain outside coverage."""
    try:
        tree = ast.parse(member_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return "unreadable or unparseable: {}".format(exc)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name != "run" or not node.args:
            continue
        head = node.args[0]
        if fixture_calls and isinstance(head, ast.Name) and head.id == "cmd":
            pass  # selftest_aiqt_hooks._git constructs its fixture command here.
        elif isinstance(head, (ast.List, ast.Tuple)) and head.elts:
            first = head.elts[0]
            if not (isinstance(first, ast.Constant) and first.value == "git"):
                continue
            if not fixture_calls and not any(
                    isinstance(e, ast.Constant) and e.value == "archive" for e in head.elts):
                continue
        else:
            continue
        found += 1
        env_kw = next((kw for kw in node.keywords if kw.arg == "env"), None)
        if env_kw is None or not isinstance(env_kw.value, ast.Call):
            return "a matching git launch without an env= call"
        env_func = env_kw.value.func
        env_name = env_func.id if isinstance(env_func, ast.Name) else (
            env_func.attr if isinstance(env_func, ast.Attribute) else None)
        expected = "git_fixture_env" if fixture_calls else "caller_env_without_git"
        if env_name != expected:
            return "a git launch whose env is not {}()".format(expected)
    return True if found else "no matching git launch found"


def _call_name(node):
    func = node.func if isinstance(node, ast.Call) else node
    return func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)


def _binding_calls(member_path, owner_name, binding, factory, launches=False):
    """Check a named factory assignment and its selected subprocess.run launches.
    True selects literal git commands; "computed" selects every run in the owner;
    "attestation" selects literal git commands rooted at ac in release-build.
    Syntactic only: aliases, later reassignment and indirect calls are not proved.
    OPF's dict(_scrubbed_env(), HOME=...) is an intentional standalone adapter."""
    try:
        tree = ast.parse(member_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return str(exc)
    owners = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == owner_name]
    if len(owners) != 1:
        return False
    owner = owners[0]
    assignments = [n for n in ast.walk(owner) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == binding for t in n.targets)]
    if len(assignments) != 1:
        return False
    value = assignments[0].value
    if _call_name(value) == "dict" and isinstance(value, ast.Call) and len(value.args) == 1:
        value = value.args[0]
    if not isinstance(value, ast.Call) or _call_name(value) != factory:
        return False
    if not launches:
        return True
    found = []
    for n in ast.walk(owner):
        if not isinstance(n, ast.Call) or _call_name(n) != "run" or not n.args:
            continue
        argv = n.args[0]
        literal_git = (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts
                       and isinstance(argv.elts[0], ast.Constant)
                       and argv.elts[0].value == "git")
        if launches == "computed":
            found.append(n)
        elif launches == "attestation":
            if (literal_git and len(argv.elts) >= 3
                    and ast.unparse(argv.elts[1]) == "'-C'"
                    and ast.unparse(argv.elts[2]) == "str(ac)"):
                found.append(n)
        elif launches is True and literal_git:
            found.append(n)
    return bool(found) and all(any(kw.arg == "env" and isinstance(kw.value, ast.Name)
                                  and kw.value.id == binding for kw in n.keywords)
                               for n in found)


def _caller_env_archive_only():
    """Reject caller-env uses outside literal archives of the real checkout, across both trees.
    Direct name/attribute references and ImportFrom aliases are checked. Dynamic getattr,
    exec strings and rebinding repo_root itself remain outside this syntactic check's coverage.
    The child-code trust probe intentionally uses a string, not an operational call site."""
    def fail_walk(exc):
        raise exc

    try:
        found = []
        for directory in (ROOT / "tools", ROOT / "opf" / "tools"):
            for base, dirs, files in os.walk(directory, onerror=fail_walk):
                for filename in files:
                    if not filename.endswith(".py"):
                        continue
                    path = Path(base) / filename
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    parents = {child: node for node in ast.walk(tree)
                               for child in ast.iter_child_nodes(node)}
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and any(
                                alias.name == "caller_env_without_git" and alias.asname is not None
                                for alias in node.names):
                            return False
                        if not isinstance(node, (ast.Name, ast.Attribute)):
                            continue
                        if _call_name(node) != "caller_env_without_git":
                            continue
                        call = parents.get(node)
                        kw = parents.get(call)
                        launch = parents.get(kw)
                        if not (isinstance(call, ast.Call) and call.func is node
                                and not call.args and not call.keywords
                                and isinstance(kw, ast.keyword) and kw.arg == "env"
                                and isinstance(launch, ast.Call)
                                and ast.unparse(launch.func) == "subprocess.run"
                                and launch.args):
                            return False
                        argv = ast.unparse(launch.args[0])
                        rel = path.relative_to(ROOT).as_posix()
                        if rel == "tools/check_release_build.py":
                            if argv != "['git', '-C', str(repo_root()), '-c', 'core.attributesFile=/dev/null', 'archive', 'HEAD']":
                                return False
                        elif rel == "tools/check_release_delta.py":
                            if argv != "['git', '-C', str(real), '-c', 'core.attributesFile=/dev/null', 'archive', 'HEAD']":
                                return False
                            owner = launch
                            while owner in parents and not isinstance(owner, ast.FunctionDef):
                                owner = parents[owner]
                            if not isinstance(owner, ast.FunctionDef) or owner.name != "_archive_head":
                                return False
                            if [a.arg for a in owner.args.args] != ["real"]:
                                return False
                            if any(isinstance(n, ast.Name) and n.id == "real"
                                   and isinstance(n.ctx, ast.Store) for n in ast.walk(owner)):
                                return False
                            refs = [n for n in ast.walk(tree) if isinstance(n, ast.Name)
                                    and n.id == "_archive_head"]
                            if not refs or any(not isinstance(parents.get(n), ast.Call)
                                               or ast.unparse(parents[n]) != "_archive_head(repo_root())"
                                               for n in refs):
                                return False
                        else:
                            return False
                        found.append(rel)
        return sorted(found) == ["tools/check_release_build.py", "tools/check_release_build.py",
                                 "tools/check_release_delta.py"]
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        return "cannot inspect caller-env uses: {}".format(exc)


CONFIG_EXCLUSIONS = {
    "all": {
        ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-core"):
            "A git-fixture-env lane cannot recursively launch a git-fixture-env suite;"
            " the suite's controls run inside its four registered runners.",
        ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-config-combined"):
            "A git-fixture-env lane cannot recursively launch a git-fixture-env suite;"
            " the suite's controls run inside its four registered runners.",
        ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-config-malformed"):
            "A git-fixture-env lane cannot recursively launch a git-fixture-env suite;"
            " the suite's controls run inside its four registered runners.",
        ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-config-system"):
            "A git-fixture-env lane cannot recursively launch a git-fixture-env suite;"
            " the suite's controls run inside its four registered runners.",
        ("tools/measure_hook_latency.py",):
            "A bare launch is a real timing measurement of the shipped hooks, not a self-test:"
            " minutes at its defaults, and the hooks' own git calls drop GIT_CONFIG_NOSYSTEM by"
            " design; its self-test stays in this roster as the hook-latency-selftest suite step.",
    },
    "archive": {
        ("tools/check_release_build.py", "--self-test"):
            "Only the exact real-root archive launch may retain caller configuration.",
        ("tools/check_release_delta.py", "--self-test"):
            "Only the exact real-root archive launch may retain caller configuration.",
    },
    "malformed": {
        ("tools/check_release_build.py", "--self-test"):
            "Real-checkout archives intentionally retain caller trust configuration.",
        ("tools/check_release_delta.py", "--self-test"):
            "Real-checkout archives intentionally retain caller trust configuration.",
    },
}


def _command_identity(argv):
    """Match CI-parity's single leading ./ normalization; retain argv for execution."""
    script = argv[0][2:] if argv[0].startswith("./") else argv[0]
    return (script, *argv[1:])


def _registered_selftests(root=ROOT):
    """Parse every registry before selecting self-tests; preserve exact script arguments.
    Reuse CI-parity's fail-closed shell/YAML grammar. The standalone runner is
    normalized through check_ci_parity.adapt_standalone_runner, the ONE shared
    adapter, which screens the raw text against the character allowlist before
    its own line split, then removes the validated directory binding and
    terminal exit; any adapter diagnostic refuses the roster here.
    Each registry file is read through check_ci_parity.read_runner_text,
    the ONE byte-level reader: the raw bytes are screened against
    printable ASCII plus tab and newline BEFORE any decode, so a
    decode-layer newline translation cannot erase a forbidden byte ahead
    of the screen, and a reader diagnostic refuses the roster here too.
    Declaration coverage only: selection uses a selftest_ basename or an explicit
    --self-test, --selftest or --suite flag, not fixture-building behaviour.
    Other entry modes, unregistered entries and conditional reachability are outside
    this inventory. Manifest runners also run directly, because the
    execution gate deliberately sanitizes its child environment.
    """
    from check_ci_parity import (
        adapt_standalone_runner, extract_local, extract_ci,
        read_runner_text, _strip_comment, _tokenize, normalize)
    from check_selftest_execution import _manifest_suites

    commands = set()
    for relative, extract in (("tools/run_all_checks.sh", extract_local),
                              ("opf/tools/run_all_checks.sh", extract_local),
                              (".github/workflows/quality.yml", extract_ci),
                              (".github/workflows/msg-leaks.yml", extract_ci)):
        source, read_diagnostic = read_runner_text(root / relative, relative)
        if read_diagnostic is not None:
            raise ValueError("{}: registry diagnostics: {}".format(
                relative, (read_diagnostic,)))
        if relative.startswith("opf/"):
            source, adapter_diagnostic = adapt_standalone_runner(source)
            if adapter_diagnostic is not None:
                if adapter_diagnostic.code == "standalone-scaffold":
                    raise ValueError("unsupported standalone runner scaffold")
                raise ValueError("{}: registry diagnostics: {}".format(
                    relative, (adapter_diagnostic,)))
        result = extract(source)
        if result.diagnostics:
            raise ValueError("{}: registry diagnostics: {}".format(relative, result.diagnostics))
        if not result.members:
            raise ValueError(relative + ": empty registry")
        selected = set()
        for member in result.members:
            argv = tuple(member.split(" "))
            if not ("--self-test" in argv or "--selftest" in argv or "--suite" in argv
                    or Path(argv[0]).name.startswith("selftest_")):
                continue
            if any("<ref:" in arg for arg in argv):
                raise ValueError("dynamic self-test arguments: " + member)
            candidates = []
            for line_number in result.origins:
                code = _strip_comment(source.splitlines()[line_number - 1]).strip()
                if code.startswith("run:"):
                    code = code[4:].strip()
                parsed = _tokenize(code)
                if not parsed.ok:
                    raise ValueError("unparseable self-test command: " + code)
                tokens = parsed.value
                if tokens[:1] == ["run_gate"]:
                    tokens = tokens[2:]
                normalized = normalize(tokens)
                if normalized.ok and normalized.value == member:
                    if tokens[:3] != ["python3", "-I", "-B"]:
                        raise ValueError("unsupported self-test launcher: " + code)
                    candidates.append(tuple(tokens[3:]))
            if not candidates:
                raise ValueError("cannot recover exact self-test arguments: " + member)
            selected.update(candidates)
        if not selected:
            raise ValueError(relative + ": empty self-test roster")
        commands.update(selected)
    suites = _manifest_suites(root / "tools" / "selftest_checks.toml")
    if suites is None:
        raise ValueError("cannot read suite runner registry")
    runners = {row["id"]: row["runner"] for row in suites}
    for argv in sorted(commands):
        identity = _command_identity(argv)
        if identity[0] == "tools/check_selftest_execution.py" and "--suite" in identity:
            if len(argv) != 3 or argv[1] != "--suite" or argv[2] not in runners:
                raise ValueError("unparseable suite invocation: {!r}".format(argv))
            if identity not in CONFIG_EXCLUSIONS["all"]:
                commands.add((runners[argv[2]],))
    identities = {_command_identity(argv) for argv in commands}
    for lane, exclusions in CONFIG_EXCLUSIONS.items():
        for argv, reason in exclusions.items():
            if argv not in identities or not reason.strip():
                raise ValueError("stale or unreasoned {} exclusion: {!r}".format(lane, argv))
    commands = {argv for argv in commands
                if _command_identity(argv) not in CONFIG_EXCLUSIONS["all"]}
    if not commands:
        raise ValueError("empty config-injection roster")
    for argv in commands:
        (root / argv[0]).read_bytes()
    return tuple(sorted(commands))


def _config_results(roster, env, marker, monitor_marker, system=False):
    """Observe launches as well as verdicts, including silent configuration reads.
    Each command receives a private copy of the poison tree so concurrent markers
    cannot clear or contaminate another command's evidence.
    The shim covers basename launches and executables resolved through this PATH.
    Hardcoded absolute executables and descendants replacing PATH are residuals.
    Exact git --version is exempt: it does not discover repository configuration.
    Real-root archives have the exact-command exception documented above.
    Every file a child process EXECUTES (each observer wrapper, each tripwire,
    the copied caller hooks and fsmonitor) is fully written and closed in the
    sequential preparation pass BEFORE the worker pool starts, so no fork can
    inherit a write descriptor still open on it: execve of a file any process
    holds open for writing fails ETXTBSY, and CPython's exec PATH search skips
    a failed candidate and continues, which would run the REAL git in the
    observer's place and record nothing. A tripwire directory sits directly
    after each observer on PATH, so a fall-through past the observer (any
    execve failure, not only ETXTBSY) runs the tripwire, which records the
    bypass and exits nonzero; _require_wrapper_observed then fails the run
    closed instead of accepting an unobserved, apparently clean result.
    _prepared_before_pool_control pins the preparation ordering itself.
    The members run on the pool's worker threads (_run_bounded inline there). When the pool's
    results raise (an interrupt reaching the main thread, or a failed member), closing is set
    FIRST, as a plain store (so no signal handler raises once the main thread takes the registry
    lock) and then under the lock (so a worker still in its setup steps, the positive control,
    can never start a member after the sweep), then no queued member starts and every registered
    member group is killed (_signal_member_groups) BEFORE the wait for the workers, and every
    running member stops waiting (_run_member), so that wait never runs to a member's bound.
    config/member-close-on-interrupt pins it with a real SIGINT,
    config/member-failed-no-late-launch with a failed member and a worker that reaches its member
    only after the sweep, and config/member-close-signal-sweep with a signal at every point of
    this branch through main()'s closes and restore. A caller that catches that raise and goes on
    clears closing again (_reopen_members).
    """
    import shlex
    from concurrent.futures import ThreadPoolExecutor

    def prepare(private, argv):
        caller = private / "caller"
        shutil.copytree(marker.parent, caller)
        # Rebind the copied poison's absolute paths, including hook and monitor
        # outputs. Binary git-control objects are copied unchanged.
        for path in caller.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            replaced = text.replace(str(marker.parent), str(caller))
            if replaced != text:
                path.write_text(replaced, encoding="utf-8")
        member_env = {k: v.replace(str(marker.parent), str(caller)) for k, v in env.items()}
        real_git = shutil.which("git", path=member_env.get("PATH", os.defpath))
        if real_git is None:
            raise ValueError("config observation requires git")
        exposure = private / "exposure"
        bypass = private / "bypass"
        wrapper = private / "git"
        wrapper.write_text(
            '#!/bin/sh\n'
            'if [ "$#" = 1 ] && [ "$1" = --version ]; then exec {git} "$@"; fi\n'
            'if [ {archive} = yes ] && [ "$#" = 6 ] && [ "$1" = -C ] && '
            '[ "$2" = {root} ] && [ "$3" = -c ] && '
            '[ "$4" = core.attributesFile=/dev/null ] && '
            '[ "$5" = archive ] && [ "$6" = HEAD ]; then exec {git} "$@"; fi\n'
            '[ "${{HOME:-}}" != {home} ] || printf "home\\n" >> {log}\n'
            '[ "${{XDG_CONFIG_HOME:-}}" != {xdg} ] || printf "xdg\\n" >> {log}\n'
            'if [ {system} = yes ] && [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && '
            '[ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then printf "system\\n" >> {log}; fi\n'
            'exec {git} "$@"\n'.format(
                git=shlex.quote(real_git),
                archive="yes" if _command_identity(argv) in CONFIG_EXCLUSIONS["archive"] else "no",
                root=shlex.quote(str(ROOT)), home=shlex.quote(member_env["HOME"]),
                xdg=shlex.quote(member_env["XDG_CONFIG_HOME"]), log=shlex.quote(str(exposure)),
                system="yes" if system else "no"), encoding="utf-8")
        wrapper.chmod(0o700)
        trap = private / "fallback-trap"
        trap.mkdir()
        tripwire = trap / "git"
        tripwire.write_text(
            '#!/bin/sh\nprintf "bypassed\\n" >> {log}\nexit 66\n'.format(
                log=shlex.quote(str(bypass))), encoding="utf-8")
        tripwire.chmod(0o700)
        member_env["PATH"] = (str(private) + os.pathsep + str(trap) + os.pathsep
                              + member_env.get("PATH", os.defpath))
        exposure.write_bytes(b"")
        bypass.write_bytes(b"")
        return (argv, member_env, caller / marker.name, caller / monitor_marker.name,
                exposure, bypass)

    def run(prepared):
        argv, member_env, hook_log, monitor_log, exposure, bypass = prepared
        subprocess.run(["git", "config", "--get", "user.name"], env=member_env,
                       capture_output=True, timeout=60)
        _require_wrapper_observed(bypass, "positive control: " + " ".join(argv))
        live = exposure.read_bytes()
        if b"home\n" not in live or b"xdg\n" not in live or (system and b"system\n" not in live):
            raise ValueError("config observer did not detect its positive control")
        exposure.write_bytes(b"")
        hook_log.write_bytes(b"")
        monitor_log.write_bytes(b"")
        rc = _run_config_member(argv, member_env)
        _require_wrapper_observed(bypass, "member run: " + " ".join(argv))
        result = (rc, hook_log.read_bytes(), monitor_log.read_bytes(), exposure.read_bytes())
        print("CONFIG RUN {}: rc={}, exposure={}".format(
            " ".join(argv), rc, sorted(set(result[3].decode().splitlines()))), flush=True)
        return argv, result

    stage = Path(tempfile.mkdtemp(prefix="config-observe-"))
    try:
        prepared = []
        for index, argv in enumerate(roster):
            private = stage / "member-{:03d}".format(index)
            private.mkdir()
            prepared.append(prepare(private, argv))
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            results = dict(pool.map(run, prepared))
        except BaseException:
            # An interrupt or a failed member: closing is set (a plain store before the lock is
            # taken, then under the lock), no queued member starts, and every running one is
            # killed now, so the wait below returns at once instead of at each member's bound.
            _MEMBERS["closing"] = True
            try:
                with _MEMBERS_LOCK:
                    _MEMBERS["closing"] = True
            except RegistryLockTimeout as exc:
                # The plain store already refuses every later start; the refusal is recorded.
                _lock_refused("failed-member branch", exc)
            pool.shutdown(wait=False, cancel_futures=True)
            _signal_member_groups()
            raise
        finally:
            pool.shutdown(wait=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    marker.write_bytes(b"")
    monitor_marker.write_bytes(b"")
    return results


def _require_wrapper_observed(bypass, what):
    """FAIL CLOSED on a bypassed observer. The tripwire records every git launch
    that fell through past the observer wrapper, so a recorded bypass means git
    ran unobserved and any clean exposure or marker evidence from this run is
    void; raise instead of returning a result that could read as clean."""
    recorded = bypass.read_bytes()
    if recorded:
        raise ValueError(
            "config observer wrapper was bypassed ({}): exec fell through past the wrapper "
            "to the tripwire {} time(s)".format(what, len(recorded.splitlines())))


class HolderNotReady(Exception):
    """The bypass control's write holder never signalled readiness in time."""


def _await_holder_ready(holder, seconds=60):
    """Read the holder's readiness line under a DEADLINE, never an unbounded
    readline(): return only on exactly b"held\\n", and raise HolderNotReady on
    the deadline, on EOF, or on any other bytes, so a stalled holder fails the
    control closed instead of hanging it before its bounded cleanup."""
    import select
    import time

    fd = holder.stdout.fileno()
    deadline = time.monotonic() + seconds
    line = b""
    while not line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise HolderNotReady("no readiness line within {} s, got {!r}".format(seconds, line))
        chunk = os.read(fd, 64)
        if not chunk:
            raise HolderNotReady("holder closed its output before readiness, got {!r}".format(line))
        line += chunk
    if line != b"held\n":
        raise HolderNotReady("unexpected readiness line {!r}".format(line))


_WRITE_RECORDERS = []
_WRITE_AUDIT_INSTALLED = False


def _dispatch_write_audit(event, args):
    """Process-wide audit hook (CPython cannot remove one); inert unless a
    recorder is registered in _WRITE_RECORDERS."""
    for recorder in tuple(_WRITE_RECORDERS):
        recorder(event, args)


def _member_result(results, member, column):
    """Legacy IDs report derived runs, including every registered argument variant."""
    values = [value[column] for argv, value in results.items() if _command_identity(argv)[0] == member]
    if not values:
        return "missing registered member: " + member
    want = 0 if column == 0 else b""
    return want if all(value == want for value in values) else values


def _system_pin_probe(base, lifecycle):
    """Observe basename resolution after the same GIT_* scrub production uses."""
    from unittest.mock import patch
    observer_dir = base / "observe-bin"
    observer_dir.mkdir(exist_ok=True)
    observer = observer_dir / "git"
    observer.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$GIT_CONFIG_NOSYSTEM\" \"$GIT_CONFIG_SYSTEM\"\n",
        encoding="utf-8")
    observer.chmod(0o700)
    with patch.dict(os.environ, PATH=str(observer_dir) + os.pathsep + os.defpath):
        saved = dict(os.environ)
        try:
            with lifecycle():
                stripped = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
                child = subprocess.run(["git"], env=stripped, capture_output=True,
                                       text=True, timeout=60)
                pins = (child.returncode, child.stdout.splitlines())
                raise RuntimeError("fixture restoration probe")
        except RuntimeError as exc:
            if str(exc) != "fixture restoration probe":
                raise
        return pins, dict(os.environ) == saved


def _config_poison_fixture(base):
    """The shared CONFIG-INJECTION poison tree and control repository, built identically for
    the three config-lane suites (combined, malformed and system; see _SUITE_RUNNERS): the
    caller HOME/XDG config, hook, ignore, attributes and fsmonitor poison, the hook and
    fsmonitor marker files, the deliberately UNSCRUBBED environment, and a committed control
    repository (with one untracked file) whose commit ran under the poison, so the caller
    hooks demonstrably execute under this configuration before any lane result is read. This
    helper only BUILDS the fixture; every check() verdict over it belongs to the lane
    runners. Commands come from the shell/Quality registries, with exact script arguments.
    Hardcoded absolute executables, a replaced PATH, and repository-local config remain
    outside the observer shim's coverage; see _config_results."""
    import shlex

    roster = _registered_selftests()
    print("CONFIG ROSTER: {} commands".format(len(roster)), flush=True)
    for argv in roster:
        print("  " + " ".join(argv), flush=True)
    home, xdg, hooks = (base / name for name in ("config-home", "config-xdg", "config-hooks"))
    for directory in (home / ".config" / "git", xdg / "git", hooks):
        directory.mkdir(parents=True)
    marker = base / "hook-invocations"
    marker.write_bytes(b"")
    ignore = home / "ignore"
    attributes = home / "attributes"
    for path in (ignore, xdg / "git" / "ignore", home / ".config" / "git" / "ignore"):
        path.write_text("*\n", encoding="utf-8")
    for path in (attributes, xdg / "git" / "attributes", home / ".config" / "git" / "attributes"):
        path.write_text("* export-ignore fixture-poison\n", encoding="utf-8")
    monitor = base / "fsmonitor"
    monitor_marker = base / "fsmonitor-invocations"
    monitor_marker.write_bytes(b"")
    monitor.write_text(
        "#!/bin/sh\nprintf 'invoked\\n' >> {}\nprintf 'token\\000'\n".format(
            shlex.quote(str(monitor_marker))), encoding="utf-8")
    monitor.chmod(0o700)
    config = ('[safe]\n\tdirectory = {}\n[core]\n\thooksPath = {}\n'
              '\texcludesFile = {}\n\tattributesFile = {}\n\tfsmonitor = {}\n').format(
                  *(json.dumps(str(p)) for p in (ROOT, hooks, ignore, attributes, monitor)))
    for path in (home / ".gitconfig", xdg / "git" / "config", home / ".config" / "git" / "config"):
        path.write_text(config, encoding="utf-8")
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
                 "post-checkout", "post-merge", "reference-transaction"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nprintf 'invoked\\n' >> {}\n".format(shlex.quote(str(marker))),
                        encoding="utf-8")
        hook.chmod(0o700)
    # Deliberately UNSCRUBBED input: using git_fixture_env here would mask the defect.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(xdg), PYTHONDONTWRITEBYTECODE="1")
    control = base / "config-control"
    control.mkdir()
    (control / "seed").write_text("seed\n", encoding="utf-8")
    (control / "survivor").write_text("kept\n", encoding="utf-8")
    (control / ".gitattributes").write_text("survivor -export-ignore\n", encoding="utf-8")
    # The env above is DELIBERATELY unscrubbed (the lane tests config injection), so the F-367
    # pins ride the argv in option position: the control commit must not spawn a detached
    # auto-gc/auto-maintenance child that outlives it (the pins do not touch the injection
    # surfaces under test: hooks, ignore files, attributes, fsmonitor).
    for args in (("init", "-q"), ("add", "-f", "seed", "survivor", ".gitattributes"),
                 ("-c", "user.name=Selftest", "-c", "user.email=selftest@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "control")):
        subprocess.run(["git", "-C", str(control), "-c", "gc.auto=0",
                        "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false", *args],
                       env=env, check=True, capture_output=True, timeout=60)
    (control / "untracked").write_text("dirt\n", encoding="utf-8")
    return {"roster": roster, "env": env, "home": home, "xdg": xdg, "hooks": hooks,
            "marker": marker, "monitor": monitor, "monitor_marker": monitor_marker,
            "ignore": ignore, "attributes": attributes, "config": config,
            "control": control}


# Per-member time bounds (seconds) for _run_config_member. A member absent here keeps
# CONFIG_MEMBER_BOUND_DEFAULT. config/member-bound-table pins this table (every key an exact
# registered roster argv, each override, the default), config/member-bound-opf-selftest pins
# the opf.py --self-test entry, config/member-bound-reaches-run pins that _run_config_member
# hands the looked-up bound to _run_bounded, and config/member-roster-figures pins the SIZING_*
# constants below against _registered_selftests() through _roster_figures and prints the
# figures it derives (all in _member_bound_checks); none of them reads this comment, and none
# of them tests any sizing.
# Figures in this comment carry one of four labels: MEASURED (run for SIZING below), ESTIMATED
# (derived by the SIZING formula), REPORTED (an earlier account, not reproduced here) or
# READ FROM SOURCE (read from the member's own code, not measured). The exceptions are the five
# tools/check_opf_standalone_closure.py --self-test --ci-job entries, sized outside SIZING: their
# MEASURED figures are standalone runs of the pre-split whole self-test (and one of the
# opf-closure-c member when it held one leg) and their ESTIMATED figure scales the latest
# whole-self-test run by load alone, not by the SIZING formula (their shared comment gives both),
# and the tools/check_selftest_execution.py --suite child-status-coverage-selftest entry, whose
# MEASURED figures are standalone runs (its own comment), fed to the SIZING formula.
#
# CONFIG_MEMBER_BOUND_DEFAULT is a DELIBERATE SHORTER DEADLINE, not a bound sized above every
# member's own end-to-end budget. Budgets the members grant themselves, READ FROM SOURCE (not
# measured): opf/tools/opf.py --self-test grants its blocked-isolation leg alone
# 31 * _deadline_case_budget(180) = 31 * (180 + 5 + 1) = 5766 s (the isolation matrix's 31 cases,
# each its 180 s timeout plus the 5 s fixture cleanup grace and the 1 s receipt allowance), and its
# regression runner bounds that leg at 5766 + 30 = 5796 s (the 30 s launch margin); that is one leg,
# so the whole self-test grants itself more. The tools/check_selftest_execution.py members set no
# internal bound of their own (the CI job timeout is their outer bound). The kill-timeout rule
# ("A kill timeout outlives the wait it bounds", AGENTS.md) asks that a bound meant to let a command
# finish be sized above the budget the command grants itself; it allows a deliberately shorter
# deadline where cutting the work short is the explicit intent, provided reaching it is attributed
# to the deadline rather than reported as a failure of the callee. Every bound in this table takes
# that exception: none of them is offered as a bound above its member's whole self-granted budget.
# CHOICE: the default is NOT raised above 5766 s, and neither is any entry below (each but the
# five tools/check_opf_standalone_closure.py --self-test --ci-job entries and the
# child-status-coverage-selftest entry is sized from config-lane runs MEASURED on this host, see
# SIZING; those five are sized from standalone runs of the pre-split whole self-test, see their
# shared comment, and that one from standalone runs, see its own comment); each entry
# stays a deliberate shorter deadline relative to the member's self-granted budget, yet each
# formula-sized entry is sized above the member's measured runs (lane runs, or standalone runs for
# the child-status-coverage-selftest entry) so that the member finishes under the load the SIZING
# factor covers;
# reaching a bound stays cannot-evaluate, attributed to the bound, never a FAIL of that member
# and never a pass, because this lane owns no member's verdict.
# Every roster member is a registered step or the runner behind a registered --suite step
# (_registered_selftests reads both CI workflows, both standalone runners and the suite manifest),
# and that step runs the member to completion under no deadline from this suite, so no member's
# pass or fail depends on this bound. This lane asks a different question (does the
# member stay clean under injected caller hooks, ignore files, attributes and fsmonitor). The
# roster is every registered self-test except CONFIG_EXCLUSIONS["all"] (the four git-fixture-env
# suite runners and the bare hook-latency measurement, whose self-test stays in the roster as its
# --suite step); the combined and system lanes rerun every roster member, the malformed lane
# every one outside CONFIG_EXCLUSIONS["malformed"], the fsmonitor pass four of them under
# --self-test (its _run_config_member calls), and the bound caps how long one stalled rerun can
# hold that answer back. Cutting a rerun short leaves the question unanswered, never answered
# wrongly: reaching the bound is cannot-evaluate (exit 2 when nothing definite was found), never a
# FAIL of that member and never a pass.
#
# SIZING. MEASURED (2026-10-12, 24-CPU host, nice 10, every bound lifted to 10800 s): the suite's
# own _config_results (four workers, run through main() with its lanes replaced) under the combined
# lane's injected configuration, rebuilt by a copy of that lane's setup code (NOT a call to
# _config_injection_lane itself), over opf.py, check_opf_record.py --red-on-revert,
# _opf_adopt_observe.py, check_release_delta.py and check_opf_init.py --self-test plus six shorter
# opf/tools self-tests (_opf_adopt.py, _opf_oplock.py, _opf_init_operation.py,
# check_opf_upgrade.py, check_opf_homes.py and check_opf_doctor.py), eleven members then (ten
# now in SIZING_MEASURED), twice (load5 24.02 to 29.29, then 29.29 to 33.68); once over the whole
# combined roster as registered then, SIZING_ACCOUNT_ROSTER (load5 27.35 to 32.99); and once
# under the malformed lane's configuration over those eleven members less its exclusion, ten
# members then (nine now in SIZING_MALFORMED_MEASURED, load5 30.01 to 32.14). Every member
# exited 0 with no hook, fsmonitor or exposure record in every run. Each entry below gives its
# lane time per run.
# SIZING_ACCOUNT_ROSTER is the set _registered_selftests() returns at commit e5191908, the parent
# of the commit that recorded these runs; a member registered later is outside the account.
# The closure gate's leg-bounds change, merged with main on 2026-10-15, 2026-10-17, 2026-10-18,
# 2026-10-19 and 2026-10-20, registers two such members, tools/check_opf_standalone_closure.py
# --self-test --ci-job opf-closure-a and --ci-job opf-closure-b (its CI steps, one per closure CI
# job since the split). Counted by _roster_figures on the merged tree of 2026-10-20 (a dated
# record, not pinned: config/member-roster-figures prints the live figures), the roster is 118
# members, 116 of them in the malformed lane, 17 of them outside the account. On 2026-10-23 the
# change registers a third, --ci-job opf-closure-c (then the closure job that held
# opf-homes-selftest alone), and then a fourth and a fifth, --ci-job opf-closure-d and
# --ci-job opf-closure-e (added when every closure leg whose command these lanes run was bounded
# at or above its deadline here, since the closure work then no longer fit in fewer than five
# jobs), so each of those three counts is three higher on a tree carrying all three new members.
# One measured member, opf/tools/check_opf_upgrade.py --self-test (one of the six shorter
# opf/tools self-tests, timed in every run), was later removed with the store-upgrade engine, so
# the account constants below omit it: 100 account members, 88 timed, 10 measured in the
# combined lane and 9 in the malformed lane. The runs and times recorded here are unchanged.
# NOT MEASURED, though these bounds govern those reruns all the same: the system lane; the
# malformed lane's configuration for every member of that lane outside SIZING_MALFORMED_MEASURED;
# and the fsmonitor pass's own configuration (fsmonitor set in both config files, ignore and
# attributes files removed, its four members run one at a time).
# The opf.py --self-test times above and below and its 3600 s entry were sized before the
# 2026-10-13 merge added the opf-write-guard delegate to opf.py --self-test; the delegate's own
# self-test, MEASURED standalone on the merged tree (2026-10-13), exited 0 in 0.23 s wall, so the
# entry is not resized for it.
# ESTIMATED: bound = the slowest MEASURED lane time * 20 / 3, rounded up to a multiple of 300 s;
# the formula value exceeds the 1200 s default exactly when that time exceeds 180 s. REPORTED times
# (opf.py's 1192 s lane run, and the opf.py and check_opf_record.py lane runs that reached a 1200 s
# bound) are not fed to the formula; the factor's headroom is meant to allow for them, but a run
# that reached a bound has no known length, so no bound here is shown to cover it (below).
# Roster coverage: the account of the whole-roster run gives a time or an upper bound for
# SIZING_ACCOUNT_TIMED of the SIZING_ACCOUNT_ROSTER members. Five took over 180 s and carry the
# five formula-sized entries below. Nine more are named here (the six shorter opf/tools self-tests,
# check_python_floor.py, check_release_cut.py --self-test --red-on-revert and
# check_selftest_execution.py --suite orch-behaviour-selftest), the slowest 142.55 s; and 75 are
# REPORTED by that account as each 62.42 s or less, with no per-member time and no names given.
# The other account members, which the account does not name, have no recorded time; nor does a
# member registered after the account unless SIZING_LATER_MEASURED records a measurement of it.
# Their default bound is NOT sized from a measurement. _roster_figures derives the live counts
# from these constants (members, malformed-lane members, members outside the account, members
# with no recorded time, malformed-lane members not measured in that lane), and
# config/member-roster-figures prints them with one line per member outside the account.
# A bound sized from a measurement taken after the account (an entry whose own comment says it
# was sized outside SIZING) is allowed only when SIZING_LATER_MEASURED maps its argv to a
# one-line citation of that measurement; _roster_figures reports any other CONFIG_MEMBER_BOUNDS
# key with no measurement behind it. A later measurement counts as a recorded time for a member
# outside the account. It never lowers the count of malformed-lane members not measured in that
# lane, which reflects only the account's malformed-lane run, nor the account's own count of
# members with no recorded time, whose members the account does not name.
# The five tools/check_opf_standalone_closure.py --self-test --ci-job entries are such bounds:
# the members have standalone times of the pre-split whole self-test only, but for one standalone
# time of the opf-closure-c member when it held one leg, none a config-lane time, and each carries
# its own entry below, sized outside SIZING. On the merged tree of 2026-10-20
# (counted by _roster_figures; dated, not pinned), 29 roster members had no time in the account:
# 27 had no recorded time (the 12 account members the account does not name, and the 15 later
# members SIZING_LATER_MEASURED does not cover), and the closure gate's two self-test halves had
# pre-split standalone times only; 106 malformed-lane members were not measured in that lane.
# The three --ci-job members registered on 2026-10-23 each carry a SIZING_LATER_MEASURED entry.
# On the tree of 2026-10-24 (counted by _roster_figures; dated, not pinned), which carries all
# five --ci-job members and two later members more (tools/check_rule_placement.py and the
# rule-placement-selftest suite run), the roster had 123 members, 121 of them in the malformed
# lane; 29 had no recorded time (the 12 account members the account does not name, and the 17
# later members SIZING_LATER_MEASURED does not cover), 34 had no time in the account (those 29 and
# the five --ci-job members), and 111 malformed-lane members were not measured in that lane
# (config/member-roster-figures prints the live figures).
# The child-status change (2026-10-17) registers four more members, each outside the account
# and with no recorded time: opf/tools/_child_status.py --self-test,
# tools/check_child_status_coverage.py, and tools/check_selftest_execution.py --suite
# child-status-selftest and the same with --suite child-status-coverage-selftest. Of these, only
# the --suite child-status-coverage-selftest member has a later measurement since (2026-10-29,
# standalone runs, no config-lane time known): it carries a SIZING_LATER_MEASURED entry and its own
# entry below, sized outside SIZING by the SIZING formula; the other three still have no recorded
# time.
# For every member, reaching its bound is cannot-evaluate, never a pass.
# The factor is 40 / 24 * 4. 40 / 24 covers load5 40 under the model that wall time grows in
# proportion to load5 once load5 exceeds the 24 CPUs, and not below (every measured run had load5
# above 24 throughout, so this overstates the growth from where it was measured). 4 is headroom for
# what that model does not explain: the REPORTED (not reproduced here) opf.py --self-test lane runs
# of 1192 s and the four that reached the then 1200 s default, at least 2.2 times its slowest
# measured lane time. Each bound stays below 5766 s (see CHOICE).
CONFIG_MEMBER_BOUND_DEFAULT = 1200
CONFIG_MEMBER_BOUNDS = {
    # MEASURED lane times (see SIZING): 650.49 s, 709.77 s, 734.81 s (whole roster) and 683.69 s
    # (malformed). ESTIMATED: 734.81 * 20 / 3 = 4898.7, so 5100 s. REPORTED by the earlier sizing,
    # not reproduced here (2026-10-07, load5 about 12 to 14 on a 16-CPU host): one standalone
    # nice 10 run took 582.30 s wall and passed; a 1200 s bound was reached twice in the lanes at
    # load5 above 20.
    ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert"): 5100,
    # MEASURED lane times (see SIZING): 482.53 s, 529.80 s, 510.39 s (whole roster) and 493.53 s
    # (malformed). ESTIMATED: 529.80 * 20 / 3 = 3532.0, so 3600 s. REPORTED by the earlier sizing,
    # not reproduced here (2026-10-12, load5 about 18 to 21): one standalone nice 10 run took
    # 467.16 s wall and 255.64 s CPU and passed; lane runs of 730 s and 1192 s, and four lane
    # reruns that reached the 1200 s default in config/opf/rc or config/opf/malformed.
    ("opf/tools/opf.py", "--self-test"): 3600,
    # MEASURED lane times (see SIZING): 265.55 s, 259.96 s, 257.71 s (whole roster) and 261.91 s
    # (malformed). ESTIMATED: 265.55 * 20 / 3 = 1770.3, so 1800 s.
    ("opf/tools/_opf_adopt_observe.py", "--self-test"): 1800,
    # MEASURED lane times (see SIZING): 214.08 s, 243.45 s and 189.62 s (whole roster); the
    # malformed lane excludes this member. ESTIMATED: 243.45 * 20 / 3 = 1623.0, so 1800 s.
    ("tools/check_release_delta.py", "--self-test"): 1800,
    # MEASURED lane times (see SIZING): 207.87 s, 220.76 s, 229.02 s (whole roster) and 219.70 s
    # (malformed). ESTIMATED: 229.02 * 20 / 3 = 1526.8, so 1800 s.
    ("opf/tools/check_opf_init.py", "--self-test"): 1800,
    # The closure gate's self-test runs as FIVE registered members since the closure CI work was
    # split between CI jobs (--ci-job: the gate partitions its legs between the jobs, and the flip
    # runs in the job whose legs hold opf-tooling-selftest); this comment sizes all five. Each part
    # sweeps only its own part of the current roster, plus the same fast vectors, so each part's
    # sweep is a proper part of the whole self-test's over the same tree; that is NOT a subset of
    # what the measured pre-split runs below swept, since opf-closure-c holds two legs
    # (opf-adopt-state-selftest and opf-adopt-complete-selftest) absent from the tree those runs
    # measured. Every part keeps the whole self-test's 3600 s entry as a
    # deliberate shorter deadline rather than a bound sized from a run of its own.
    # Sized outside SIZING (no config-lane time of this member is known: the lane runs below
    # reached the 1200 s default, so they have no known length and none is fed to the formula).
    # Every run below is of the PRE-SPLIT whole self-test (one sweep over every leg plus the flip).
    # MEASURED (2026-10-13, load5 about 29 to 36 on a 24-CPU host): one complete run took 1313 s
    # wall on the pre-fix tree and 1345 s with its leg bounds, both passed; the 1200 s default was
    # reached in all three config lanes on three consecutive runs at that load.
    # MEASURED (2026-10-14, plain, load5 about 20 to 24 on the same 24-CPU host): with the member's
    # added cleanup vectors one complete run took 1497 s wall and passed. That run predates the
    # member's import-batch leg and its re-measured leg table, so it no longer sizes this bound.
    # MEASURED (2026-10-15, plain, on a shared 24-CPU host, load5 22.8 at the start and 32.6 at the
    # end), at the member's legs, leg table and vectors of that date, recorded at a revision whose
    # opf/ subtree is git tree 7bf8107f4c6d23f5c89298ef121af566f5e48614 (the tree its leg table was
    # measured at): one complete run took 1667.7 s wall and passed. The member's vectors and the opf/
    # tree its legs run have changed since, so for this tree that run time is INFERRED, not measured,
    # and so is the estimate below, which scales it.
    # ESTIMATED (SIZING's model that wall time grows in proportion to load5 once load5 exceeds the
    # CPU count, applied to that standalone run): 3600 s, about 2.2 times that run (3600 / 1667.7 =
    # 2.159), covers load5 up to about 51.8 scaled from the run's start (its starting load5, 22.8,
    # sits below the 24 CPUs, where the model adds no growth, so it scales from 24: 24 x 2.159 =
    # 51.8), and about 70 from its ending load5 (32.6 x 2.159 = 70.4).
    # DELIBERATE SHORTER DEADLINE: 3600 s sits far BELOW each member's own worst case, READ FROM
    # SOURCE (tools/check_opf_standalone_closure.py, its own fit arithmetic on this tree: the
    # member's legs' bounds, one 10 s cleanup deadline per leg, the flip and its cleanup where it
    # runs, and the 350 s vector allowance), 12732 s (opf-closure-a), 8775 s (b), 7780 s (c),
    # 8570 s (d) and 9526 s (e), and below each CI job's worst case (20793 s, 17800 s, 15810 s,
    # 17390 s and 19302 s; see _LEG_CEILING_S and _job_worst_case_s in the member itself), exactly
    # as the default's comment above sanctions: this lane owns no member's
    # verdict, and reaching this 3600 s bound is cannot-evaluate, never a pass or a fail of the
    # member. The member's OWN inner bounds are read differently: when one of its legs is killed at
    # its leg bound, ended by a signal, or its flip is not evaluated, the member itself exits 2, and
    # this lane reads any non-zero member exit as a definite offender (_split_offenders: only this
    # suite's own recorded timeout is cannot-evaluate), so under lane load such a trip reads here as
    # a FAIL of the member, not as cannot-evaluate. Each of those inner leg bounds is itself a
    # deliberate shorter deadline (the member says so beside _LEG_CEILING_S), held, for each leg
    # whose command is a member here, at or above this table's deadline for that command plus the
    # member's 60 s launch margin, so that the closure never kills such a leg before this lane would.
    # The opf-closure-c entry was 1800 s while that member swept opf-homes-selftest alone, sized above
    # its then worst case, 1620 s; it now sweeps five legs, so it takes the 3600 s of the others.
    # MEASURED (2026-10-23, at the tree of that one-leg member, plain, on a 24-CPU host, load5 5.4 at
    # the start and 5.9 at the end): one complete standalone run of it took 110.3 s wall and passed,
    # its homes leg 53.4 s. No config-lane time of any of the five members is known.
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-a"): 3600,
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-b"): 3600,
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-c"): 3600,
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-d"): 3600,
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-e"): 3600,
    # Sized outside SIZING, by its formula (no config-lane time of this member is known: its lane
    # runs reached the 1200 s default in two of three config lanes of one CI run, so they have no
    # known length and none is fed to the formula; the same lanes had passed on the same tree in an
    # earlier run).
    # MEASURED (2026-10-29, CI, the "Child-status coverage self-test (execution-reconciled)" step of
    # the Execution-reconciled self-tests job, this same command run standalone): started 00:21:14Z,
    # completed 00:34:30Z, 796 s wall, passed.
    # MEASURED (2026-10-29, on an 80-CPU host, load5 about 6): one standalone run of
    # tools/check_child_status_coverage.py --self-test (the self-test this suite runs) took 604.9 s
    # wall.
    # ESTIMATED: 796 * 20 / 3 = 5306.7, so 5400 s.
    ("tools/check_selftest_execution.py", "--suite", "child-status-coverage-selftest"): 5400,
}
# The sizing account's frozen facts (see SIZING above); _roster_figures derives the live figures
# from them and config/member-roster-figures pins them against _registered_selftests().
# SIZING_ACCOUNT_ROSTER: every argv the whole-roster run covered, as registered when it was taken.
SIZING_ACCOUNT_ROSTER = frozenset({
    ("opf/tools/_journal.py", "--self-test"),
    ("opf/tools/_opf_adopt.py", "--self-test"),
    ("opf/tools/_opf_adopt_apply.py", "--self-test"),
    ("opf/tools/_opf_adopt_hook.py", "--self-test"),
    ("opf/tools/_opf_adopt_observe.py", "--self-test"),
    ("opf/tools/_opf_init.py", "--self-test"),
    ("opf/tools/_opf_init_contract.py", "--self-test"),
    ("opf/tools/_opf_init_operation.py", "--self-test"),
    ("opf/tools/_opf_init_substrate.py", "--self-test"),
    ("opf/tools/_opf_observe.py", "--self-test"),
    ("opf/tools/_opf_oplock.py", "--self-test"),
    ("opf/tools/_opf_pack_manifest.py", "--self-test"),
    ("opf/tools/check_opf_doctor.py", "--self-test"),
    ("opf/tools/check_opf_drift.py", "--self-test"),
    ("opf/tools/check_opf_homes.py", "--self-test"),
    ("opf/tools/check_opf_init.py", "--self-test"),
    ("opf/tools/check_opf_init_contract.py", "--self-test"),
    ("opf/tools/check_opf_init_observe.py", "--self-test", "--red-on-revert"),
    ("opf/tools/check_opf_init_p0.py", "--self-test", "--red-on-revert"),
    ("opf/tools/check_opf_prompt_pack.py", "--self-test"),
    ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert"),
    ("opf/tools/opf.py", "--self-test"),
    ("opf/tools/selftest_commonmark_conformance.py",),
    ("opf/tools/selftest_commonmark_headings.py",),
    ("tools/_qa_adapter.py", "--self-test"),
    ("tools/aei_backlog_md.py", "--self-test"),
    ("tools/audit_reference.py", "--self-test"),
    ("tools/check_applies.py", "--self-test"),
    ("tools/check_artifact_checksums.py", "--self-test"),
    ("tools/check_branch_root.py", "--self-test"),
    ("tools/check_byte_canon.py", "--self-test"),
    ("tools/check_ci_parity.py", "--self-test"),
    ("tools/check_cited_standards.py", "--self-test"),
    ("tools/check_clauses.py", "--self-test"),
    ("tools/check_crosswalk.py", "--self-test"),
    ("tools/check_derived_command_parameters.py", "--self-test"),
    ("tools/check_entry_guard.py",),
    ("tools/check_footer.py", "--self-test"),
    ("tools/check_gensrc_failclose.py", "--self-test"),
    ("tools/check_git_option_table.py", "--self-test"),
    ("tools/check_hooks_preview.py", "--self-test"),
    ("tools/check_install_page.py", "--self-test"),
    ("tools/check_instruction_budget.py",),
    ("tools/check_instruction_budget.py", "--self-test"),
    ("tools/check_internal_names.py", "--self-test"),
    ("tools/check_license_qualification.py", "--self-test"),
    ("tools/check_manifest.py", "--self-test"),
    ("tools/check_mistakes_register.py", "--self-test"),
    ("tools/check_msg_leaks.py", "--self-test"),
    ("tools/check_newtab.py", "--self-test"),
    ("tools/check_no_dashes.py", "--self-test"),
    ("tools/check_opf_enforce_platforms.py", "--self-test"),
    ("tools/check_overclaim.py", "--self-test"),
    ("tools/check_portability.py", "--self-test"),
    ("tools/check_python_floor.py",),
    ("tools/check_python_launcher_isolation.py", "--self-test"),
    ("tools/check_record_drift.py", "--self-test"),
    ("tools/check_record_sections.py", "--self-test"),
    ("tools/check_reference_facts.py", "--self-test"),
    ("tools/check_release_build.py", "--self-test"),
    ("tools/check_release_cut.py", "--self-test", "--red-on-revert"),
    ("tools/check_release_delta.py", "--self-test"),
    ("tools/check_secrets.py", "--self-test"),
    ("tools/check_selftest_execution.py", "--self-test"),
    ("tools/check_selftest_execution.py", "--suite", "ci-status-behaviour-selftest"),
    ("tools/check_selftest_execution.py", "--suite", "entry-guard-selftest"),
    ("tools/check_selftest_execution.py", "--suite", "instruction-budget-selftest"),
    ("tools/check_selftest_execution.py", "--suite", "orch-behaviour-selftest"),
    ("tools/check_selftest_execution.py", "--suite", "python-floor-selftest"),
    ("tools/check_site.py", "--self-test"),
    ("tools/check_site_versions.py", "--self-test"),
    ("tools/check_sized_instructions.py", "--self-test"),
    ("tools/check_standards_currency.py", "--self-test"),
    ("tools/check_version_monotonicity.py", "--self-test"),
    ("tools/conformance.py", "--self-test"),
    ("tools/doctor.py", "--self-test"),
    ("tools/gen_char_policy.py", "--self-test"),
    ("tools/gen_crosswalk.py", "--self-test"),
    ("tools/gen_cursor.py", "--self-test"),
    ("tools/gen_disclosure.py", "--self-test"),
    ("tools/gen_enforceability.py", "--self-test"),
    ("tools/gen_enforcement_register.py", "--self-test"),
    ("tools/gen_first_pin_demo.py", "--self-test"),
    ("tools/gen_gensrc.py", "--self-test"),
    ("tools/gen_hooks.py", "--self-test"),
    ("tools/gen_install.py", "--self-test"),
    ("tools/gen_manifest.py", "--self-test"),
    ("tools/gen_reference_facts.py", "--self-test"),
    ("tools/gen_renderers.py", "--self-test"),
    ("tools/gen_rules.py", "--self-test"),
    ("tools/gen_skill.py", "--self-test"),
    ("tools/gen_worker_pack.py", "--self-test"),
    ("tools/import_cwe.py", "--self-test"),
    ("tools/migrate.py", "--self-test"),
    ("tools/pin.py", "--self-test"),
    ("tools/selftest_aiqt_corpus.py",),
    ("tools/selftest_aiqt_hooks.py",),
    ("tools/selftest_ci_status.py",),
    ("tools/selftest_opf_render.py",),
    ("tools/selftest_orch_hooks.py",),
})
# How many SIZING_ACCOUNT_ROSTER members the account gives a time or an upper bound for.
SIZING_ACCOUNT_TIMED = 88
# The members of the two shorter-roster combined-lane runs (each timed again in the whole-roster
# run) that are still registered.
SIZING_MEASURED = frozenset({
    ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert"),
    ("opf/tools/opf.py", "--self-test"),
    ("opf/tools/_opf_adopt_observe.py", "--self-test"),
    ("tools/check_release_delta.py", "--self-test"),
    ("opf/tools/check_opf_init.py", "--self-test"),
    ("opf/tools/_opf_adopt.py", "--self-test"),
    ("opf/tools/_opf_oplock.py", "--self-test"),
    ("opf/tools/_opf_init_operation.py", "--self-test"),
    ("opf/tools/check_opf_homes.py", "--self-test"),
    ("opf/tools/check_opf_doctor.py", "--self-test"),
})
# The SIZING_MEASURED members the malformed-lane run measured, written out rather than computed
# from CONFIG_EXCLUSIONS, so a later change to that lane's exclusions on a measured member is
# reported instead of silently moving what the run is said to have covered.
SIZING_MALFORMED_MEASURED = frozenset({
    ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert"),
    ("opf/tools/opf.py", "--self-test"),
    ("opf/tools/_opf_adopt_observe.py", "--self-test"),
    ("opf/tools/check_opf_init.py", "--self-test"),
    ("opf/tools/_opf_adopt.py", "--self-test"),
    ("opf/tools/_opf_oplock.py", "--self-test"),
    ("opf/tools/_opf_init_operation.py", "--self-test"),
    ("opf/tools/check_opf_homes.py", "--self-test"),
    ("opf/tools/check_opf_doctor.py", "--self-test"),
})
# Measurements taken after the account: registered argv -> a one-line citation of where that
# measurement, and its config-lane times when any are known, are recorded (normally the bound
# entry's own comment). A CONFIG_MEMBER_BOUNDS key needs its argv here or in SIZING_MEASURED.
SIZING_LATER_MEASURED = {
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-a"): (
        "Standalone runs of the pre-split whole self-test only (2026-10-13 to 2026-10-15), no"
        " config-lane time known: see the shared CONFIG_MEMBER_BOUNDS comment."),
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-b"): (
        "Standalone runs of the pre-split whole self-test only (2026-10-13 to 2026-10-15), no"
        " config-lane time known: see the shared CONFIG_MEMBER_BOUNDS comment."),
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-c"): (
        "Standalone runs of the pre-split whole self-test, and one of this member when it held one"
        " leg (2026-10-23, 110.3 s), no config-lane time known: see the shared CONFIG_MEMBER_BOUNDS"
        " comment."),
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-d"): (
        "Standalone runs of the pre-split whole self-test only (2026-10-13 to 2026-10-15), no"
        " config-lane time known: see the shared CONFIG_MEMBER_BOUNDS comment."),
    ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job", "opf-closure-e"): (
        "Standalone runs of the pre-split whole self-test only (2026-10-13 to 2026-10-15), no"
        " config-lane time known: see the shared CONFIG_MEMBER_BOUNDS comment."),
    ("tools/check_selftest_execution.py", "--suite", "child-status-coverage-selftest"): (
        "Standalone runs only (2026-10-29, CI 796 s, local 604.9 s), no config-lane time known:"
        " see this member's CONFIG_MEMBER_BOUNDS comment."),
}
# After the process-group kill: the bounded drain of output still held by a descendant that left
# the group (setsid or setpgid), the bounded reap of the leader, and the bounded wait for the group
# to be confirmed empty (_member_group_empty).
CONFIG_MEMBER_DRAIN_SECONDS = 30
CONFIG_MEMBER_REAP_SECONDS = 5
CONFIG_MEMBER_EMPTY_SECONDS = 5
# The corpus leak lanes' bound (_run_corpus, _run_corpus_imported).
CORPUS_BOUND = 300
# The maintenance probe's polling deadline for an unterminated final trace2 event to be completed
# by the process still appending to the file; a read finishing at or past it fails the probe, even
# one that found the event complete (_trace2_maintenance_children).
TRACE2_TAIL_SECONDS = 10
# Linux's PIDFD_SIGNAL_PROCESS_GROUP (include/uapi/linux/pidfd.h, Linux 6.9 and later): a
# pidfd_send_signal flag that signals the process group the pidfd's process leads.
PIDFD_SIGNAL_PROCESS_GROUP = getattr(signal, "PIDFD_SIGNAL_PROCESS_GROUP", 1 << 2)


def _config_member_bound(member):
    return CONFIG_MEMBER_BOUNDS.get(tuple(member), CONFIG_MEMBER_BOUND_DEFAULT)


def _roster_figures(roster):
    """The live roster figures derived from the frozen sizing account, and the problems that stop
    that account applying to roster (the registered roster, _registered_selftests()). Pure:
    nothing is launched or read. figures: members; malformed (members in the malformed lane);
    later (the sorted members outside SIZING_ACCOUNT_ROSTER); no_recorded_time (the account
    members it gives no time for, plus each later member SIZING_LATER_MEASURED does not cover);
    malformed_unmeasured (malformed-lane members outside SIZING_MALFORMED_MEASURED). problems, in
    this order: an account member no longer registered (the account's counts no longer say which
    registered members they cover); a measured member outside the account, or a malformed-lane
    measurement outside SIZING_MEASURED; a measured member whose malformed-lane membership
    differs from what was measured; a stale or unreasoned SIZING_LATER_MEASURED entry (not
    registered, or its citation not one nonblank line); a CONFIG_MEMBER_BOUNDS key in neither
    SIZING_MEASURED nor SIZING_LATER_MEASURED."""
    members = {tuple(argv) for argv in roster}
    malformed = {argv for argv in members
                 if _command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"]}
    problems = ["sizing account member not registered: {!r}".format(argv)
                for argv in sorted(SIZING_ACCOUNT_ROSTER - members)]
    problems += ["measured member outside the sizing account: {!r}".format(argv)
                 for argv in sorted(SIZING_MEASURED - SIZING_ACCOUNT_ROSTER)]
    problems += ["malformed-lane measurement outside SIZING_MEASURED: {!r}".format(argv)
                 for argv in sorted(SIZING_MALFORMED_MEASURED - SIZING_MEASURED)]
    problems += ["malformed-lane membership changed on a measured member: {!r}".format(argv)
                 for argv in sorted(SIZING_MEASURED)
                 if (_command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"])
                 != (argv in SIZING_MALFORMED_MEASURED)]
    # One nonblank line means no line-boundary character at all, not just LF and CR: splitlines
    # recognizes the full set (VT, FF, FS, GS, RS, NEL and the Unicode separators), and the
    # padding makes a leading or trailing boundary split too.
    problems += ["stale or unreasoned later measurement: {!r}".format(argv)
                 for argv, citation in sorted(SIZING_LATER_MEASURED.items())
                 if argv not in members or not citation.strip()
                 or len(("x" + citation + "x").splitlines()) != 1]
    problems += ["bound with no measurement behind it: {!r}".format(argv)
                 for argv in sorted(CONFIG_MEMBER_BOUNDS)
                 if argv not in SIZING_MEASURED and argv not in SIZING_LATER_MEASURED]
    later = sorted(members - SIZING_ACCOUNT_ROSTER)
    figures = {"members": len(members), "malformed": len(malformed), "later": later,
               "no_recorded_time": len(SIZING_ACCOUNT_ROSTER) - SIZING_ACCOUNT_TIMED
               + len([argv for argv in later if argv not in SIZING_LATER_MEASURED]),
               "malformed_unmeasured": len(malformed - SIZING_MALFORMED_MEASURED)}
    return figures, problems


# THE MEMBER REGISTRY. Every bounded child (_run_bounded) leads its own session and process group
# and is registered here when it starts (_start_member_group), as a _MemberGroup; it is removed
# only once its group is confirmed empty (_end_member_group). "closing" is set by main()'s finally
# and the suite's close (_close_members), by the failed-member branch of the config pool
# (_config_results) and by the first SIGINT or SIGTERM while main() runs (_close_on_signal); once
# it is set no member starts. The lock is held across the closing test, the Popen and the
# registration, so a member either is registered before the close takes its list or never starts.
# THE MAIN THREAD NEVER TAKES THE LOCK WHILE A SIGNAL CAN RAISE: every main-thread path that takes
# it during a run (the close, the failed-member branch) first stores closing = True as a PLAIN
# store, with no lock, so _close_on_signal is a no-op before the lock is ever taken there (a
# handler raising between the acquire and the protected range of a with statement would leave the
# lock held). Every acquire is also BOUNDED (_RegistryLock, REGISTRY_LOCK_SECONDS): one not granted
# raises RegistryLockTimeout, which each caller reports AND records in _MEMBERS["refused"]
# (_lock_refused), so no path blocks forever on it and a refusal at any of the five sites (a member
# start, a release, a kill sweep, any of the close's own three acquires, the config pool's
# failed-member branch) makes the run cannot-evaluate (_final_verdict) even when a later close gets
# the lock and ends cleanly. config/member-close-lock pins the bound;
# config/member-close-unresolved-verdict pins the persistence at each of the five sites
# ("start-timeout", "release-timeout", "sweep-refused", "failed-refused"), the close at each of its
# three acquires ("close-refused", "close-wait-refused", "close-left-refused").
REGISTRY_LOCK_SECONDS = 60


class RegistryLockTimeout(RuntimeError):
    """The member registry lock was not granted within REGISTRY_LOCK_SECONDS."""


class _RegistryLock:
    """The registry's threading.Lock, taken by a with statement under a bounded acquire."""

    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self, timeout=-1):
        return self._lock.acquire(True, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        if not self.acquire(REGISTRY_LOCK_SECONDS):
            raise RegistryLockTimeout("the member registry lock was not granted within {} s".format(
                REGISTRY_LOCK_SECONDS))
        return self

    def __exit__(self, *exc_info):
        self.release()


_MEMBERS_LOCK = _RegistryLock()
# "raised": the strongest SIGINT or SIGTERM raised so far in this run, by _close_on_signal or by
# _raise_held (None until one has), so a later drain never ranks below it. "held": EVERY SIGINT or
# SIGTERM _close_on_signal deferred because closing was set or one had already raised, in the
# order received (none is dropped); main() raises the strongest after its second close and the
# restore (_raise_held), and _reopen_members raises it when it clears closing. Inside a run, closing
# is cleared only by _reopen_members (the controls that drive the failed-member branch on purpose,
# and _closing_refusal_control, which sets and clears closing alone and never restores the whole
# registry, so a signal held inside it is raised right after it). "refused": every registry lock
# refusal of this run (_lock_refused).
_MEMBERS = {"closing": False, "raised": None, "held": [], "refused": [], "groups": set()}
# How often a member's wait (_run_member) looks at closing: a member whose wait sees it set stops
# waiting and runs its own cleanup at once, instead of waiting on to its bound.
MEMBER_WAIT_SLICE_SECONDS = 0.5


def _lock_refused(what, exc):
    """Record a registry lock refusal (exc, a RegistryLockTimeout, met by what) in
    _MEMBERS["refused"] and return the message: main()'s verdict reads every refusal of the run,
    so it stays cannot-evaluate even when a later close gets the lock and ends cleanly."""
    message = "{}: {}".format(what, exc)
    _MEMBERS["refused"].append(message)
    return message


class _MemberGroup:
    """One bounded child's process group: proc, the Popen of its leader (the group id is
    proc.pid); pidfd, os.pidfd_open of the leader taken at spawn while it was an unreaped child
    (None where unavailable); ended, set once its own thread's cleanup has run."""
    __slots__ = ("proc", "pidfd", "ended")

    def __init__(self, proc, pidfd):
        self.proc, self.pidfd, self.ended = proc, pidfd, False


def _start_member_group(argv, cwd, env):
    """Start argv as the leader of a new session and process group and register it; raise
    subprocess.SubprocessError, starting nothing, once the suite is closing (RegistryLockTimeout,
    starting nothing, when the registry lock is not granted)."""
    with _MEMBERS_LOCK:
        if _MEMBERS["closing"]:
            raise subprocess.SubprocessError("the suite is closing: no new member starts")
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace",
                                start_new_session=True)
        try:
            pidfd = os.pidfd_open(proc.pid)
        except (AttributeError, OSError):  # no pidfd here: the group is named by number
            pidfd = None
        group = _MemberGroup(proc, pidfd)
        _MEMBERS["groups"].add(group)
    return group


def _signal_member_group(group, sig):
    """Send sig to every process in group's process group; return False when it was not sent
    (below), and raise ProcessLookupError when the group has no process left. With a pidfd, the
    kernel addresses the group through the leader's own pid record (pidfd_send_signal with
    PIDFD_SIGNAL_PROCESS_GROUP), which a reused pid number never names, so this is exact before
    AND after the leader is reaped. Without one (no pidfd, or a kernel before Linux 6.9 refusing
    the flag with EINVAL) the group is named by number (os.killpg), which is exact while the
    leader is unreaped (its pid, and with it the group id, stays allocated until the reap); after
    the reap only signal 0 is sent by number (a reused number is then only probed, never
    signalled), and any other signal returns False unsent."""
    if group.pidfd is not None:
        try:
            signal.pidfd_send_signal(group.pidfd, sig, None, PIDFD_SIGNAL_PROCESS_GROUP)
            return True
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise
    if sig and group.proc.returncode is not None:
        return False
    os.killpg(group.proc.pid, sig)
    return True


def _signal_member_groups():
    """SIGKILL every registered member group, under the registry lock (so a group whose pidfd is
    being released is never signalled through it); return the problems met, a lock not granted
    among them."""
    problems = []
    try:
        with _MEMBERS_LOCK:
            for group in list(_MEMBERS["groups"]):
                try:
                    _signal_member_group(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception as exc:  # reported, never raised
                    problems.append("group {} kill refused ({}: {})".format(
                        group.proc.pid, type(exc).__name__, exc))
    except RegistryLockTimeout as exc:
        problems.append(_lock_refused("no group signalled", exc))
    return problems


def _stop_member_group(group):
    """Bounded cleanup of a bounded child that must end, on EVERY exit of its wait: SIGKILL its
    whole process group (_signal_member_group; never conditioned on the leader's returncode, so a
    descendant left in the group after the leader exited or was reaped dies too), falling back to
    the leader alone when the group kill is refused; then drain its output for at most
    CONFIG_MEMBER_DRAIN_SECONDS and reap the leader for at most CONFIG_MEMBER_REAP_SECONDS.
    Returns the problems met ([] when clean); a step that raises is recorded and the cleanup goes
    on. It runs on the member's own thread, never the main thread (_run_bounded), so no signal
    handler raises inside it."""
    proc, problems = group.proc, []
    try:
        _signal_member_group(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:  # any refusal is reported, never raised
        problems.append("group kill refused ({}: {}); killed the leader alone, so a "
                        "descendant in its group may survive".format(type(exc).__name__, exc))
        try:
            proc.kill()
        except Exception as kill_exc:
            problems.append("leader kill failed ({}: {})".format(type(kill_exc).__name__,
                                                                 kill_exc))
    try:
        proc.communicate(timeout=CONFIG_MEMBER_DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        problems.append("output still held after the {} s drain; the pipes are closed "
                        "unread".format(CONFIG_MEMBER_DRAIN_SECONDS))
    except Exception as exc:
        problems.append("drain failed ({}: {})".format(type(exc).__name__, exc))
    if proc.returncode is None:
        try:
            proc.wait(timeout=CONFIG_MEMBER_REAP_SECONDS)
        except Exception as exc:
            problems.append("leader not reaped within {} s ({}: {})".format(
                CONFIG_MEMBER_REAP_SECONDS, type(exc).__name__, exc))
    return problems


def _member_group_empty(group):
    """None once group's process group is confirmed empty (signal 0 finds no process in it,
    polled for at most CONFIG_MEMBER_EMPTY_SECONDS), else the problem. Without a pidfd and after
    the leader's reap, the probe is by number, so a reused number reads as not empty (a false
    alarm, never a kill)."""
    deadline = time.monotonic() + CONFIG_MEMBER_EMPTY_SECONDS
    while True:
        try:
            _signal_member_group(group, 0)
        except ProcessLookupError:
            return None
        except Exception as exc:
            return "group {} emptiness not confirmed ({}: {})".format(
                group.proc.pid, type(exc).__name__, exc)
        if time.monotonic() >= deadline:
            return ("group {} still has a process {} s after the cleanup; it stays registered "
                    "for the suite's close".format(group.proc.pid, CONFIG_MEMBER_EMPTY_SECONDS))
        time.sleep(0.05)


def _release_member_group(group):
    """Remove a group confirmed empty from the registry and close its pidfd, exactly once; None,
    or the problem when the registry lock is not granted (the group then stays registered, and the
    refusal is recorded for main()'s verdict, _lock_refused)."""
    try:
        with _MEMBERS_LOCK:
            _MEMBERS["groups"].discard(group)
            fd, group.pidfd = group.pidfd, None
    except RegistryLockTimeout as exc:
        return _lock_refused("group {} not released".format(group.proc.pid), exc)
    if fd is not None:
        os.close(fd)
    return None


def _end_member_group(group, sink, banner, timed_out):
    """Kill and reap group (_stop_member_group), release it once it is confirmed empty
    (_member_group_empty; a group not confirmed stays registered for the suite's close), and
    close its pipes. Every problem, and an exception escaping the cleanup, is reported as ONE line
    on sink through _report_guarded: banner, a header naming the outcome (a recorded timeout when
    timed_out is truthy, else the wait that ended otherwise), then the problems; none is raised,
    so the outcome the cleanup follows (a return, or an exception already propagating) stands."""
    try:
        problems = _stop_member_group(group)
    except BaseException as exc:
        problems = ["cleanup raised ({}: {})".format(type(exc).__name__, exc)]
    try:
        unconfirmed = _member_group_empty(group)
    except BaseException as exc:
        unconfirmed = "emptiness check raised ({}: {})".format(type(exc).__name__, exc)
    group.ended = True
    if unconfirmed is None:
        unconfirmed = _release_member_group(group)
    if unconfirmed is not None:
        problems.append(unconfirmed)
    for stream in (group.proc.stdout, group.proc.stderr):
        try:
            stream.close()
        except OSError:
            pass
    if problems:
        _report_guarded("{}cleanup after {}: {}".format(
            banner, "that timeout was incomplete (the timeout stays cannot-evaluate)"
            if timed_out else "the wait ended was incomplete (that outcome stands)",
            "; ".join(problems)), sink)


def _close_members():
    """THE SUITE'S CLOSE (main() runs it twice on every exit): set closing, FIRST as a plain store
    with no lock held (so _close_on_signal is a no-op before this takes the registry lock), then
    under the lock (so no member starts after it); SIGKILL every registered group, wait (bounded)
    until every registered group's own thread has run its cleanup, then SIGKILL and probe whatever
    is still registered, releasing each group now confirmed empty. Returns the problems met, a
    group still not confirmed empty and a registry lock not granted (the close then stops) among
    them. Each of its three with statements takes the lock through the bounded acquire, and a
    refusal at each is pinned ("close-refused", "close-wait-refused", "close-left-refused")."""
    _MEMBERS["closing"] = True
    problems = []
    try:
        with _MEMBERS_LOCK:
            _MEMBERS["closing"] = True
        problems += _signal_member_groups()
        deadline = time.monotonic() + (CONFIG_MEMBER_DRAIN_SECONDS + CONFIG_MEMBER_REAP_SECONDS
                                       + CONFIG_MEMBER_EMPTY_SECONDS + 10)
        while True:
            with _MEMBERS_LOCK:
                pending = [group for group in _MEMBERS["groups"] if not group.ended]
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        problems += ["group {} cleanup still running at the close's deadline".format(
            group.proc.pid) for group in pending]
        with _MEMBERS_LOCK:
            left = [group for group in _MEMBERS["groups"] if group.ended]
        if left:
            problems += _signal_member_groups()
            for group in left:
                unconfirmed = _member_group_empty(group)
                if unconfirmed is None:
                    unconfirmed = _release_member_group(group)
                if unconfirmed is not None:
                    problems.append(unconfirmed)
    except RegistryLockTimeout as exc:
        problems.append(_lock_refused("close stopped", exc))
    return problems


def _close_on_signal(signum, frame):
    """main()'s SIGINT and SIGTERM handler, FIRST RAISE THEN DEFER: the first one, while closing is
    clear and none has raised, sets closing (no member starts after it), records itself in raised
    and raises (_raise_signal_exception), so main()'s finally closes every member group. Every
    later one (once one has raised, or once closing is set by main()'s finally, the close or the
    config pool's failed-member branch) is DEFERRED, never raised: each deferred one is appended to
    held, in order, and main() raises the strongest of the run's signals once, after its second
    close and the restore (_raise_held: SIGTERM over SIGINT), so no second or later signal can cut
    either close short, and none is dropped. It never takes the registry lock, and it never raises
    while the main thread holds it: every main-thread path that takes the lock during a run sets
    closing first. (Residual: CPython clears a signal's pending flag before it calls the handler, so
    a handler cut off at its own entry, before its first statement, by a newly arrived signal whose
    handler raises is lost; the sweep injects nothing inside this function for that reason.)"""
    if _MEMBERS["closing"] or _MEMBERS["raised"]:
        _MEMBERS["held"].append(signum)
        return
    _MEMBERS["closing"] = True
    _MEMBERS["raised"] = signum
    _raise_signal_exception(signum)


def _raise_signal_exception(signum):
    """Raise what signum's default disposition means here: KeyboardInterrupt for SIGINT (Python's
    default handler), SystemExit(128 + signum) for SIGTERM (143, the default action's exit code)."""
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def _raise_held():
    """THE DEFERRED-SIGNAL PRECEDENCE. When _close_on_signal deferred any signal (held, every one in
    the order received), clear held, record the strongest of the run's signals in raised, and raise
    it once: the strongest of the one raised before (raised, by _close_on_signal or by an earlier
    drain) and every held one, SIGTERM over SIGINT whatever the order, so SystemExit 143 whenever a
    SIGTERM was received and KeyboardInterrupt otherwise. Recording it keeps the precedence across
    every drain: a SIGTERM held inside a control and raised by _reopen_members still outranks a
    SIGINT held during main()'s later closes, so a terminate request delivered before the restore
    is not downgraded to a KeyboardInterrupt a caller may catch, and a later SIGTERM replaces an
    earlier SIGINT's exception still propagating. Residual, disclosed by main(): a SIGINT
    delivered after SIGINT's restore takes the restored disposition instead, and one handled at
    or after this drain's entry raises before held is read, so the signals still held, a SIGTERM
    among them, are then dropped. Nothing when none was
    deferred: the exception already propagating, if any, stands. main() calls it after its second
    close and the restore, and _reopen_members when it clears closing. config/member-close-handlers
    pins the precedence across a control and main()'s closes (the "split-..." scenarios)."""
    held, _MEMBERS["held"] = _MEMBERS["held"], []
    if held:
        _MEMBERS["raised"] = (signal.SIGTERM if signal.SIGTERM in [_MEMBERS["raised"]] + held
                              else signal.SIGINT)
        _raise_signal_exception(_MEMBERS["raised"])


def _reopen_members():
    """Clear closing after a control drove _config_results into its failed-member branch on
    purpose and caught the raise (production never clears it inside a run: that raise ends the
    suite), or after _closing_refusal_control. A SIGINT or SIGTERM deferred while closing was set
    is raised now (_raise_held, which records it, so main()'s final drain never ranks below it),
    closing set again, instead of being lost."""
    _MEMBERS["closing"] = False
    if _MEMBERS["held"]:
        _MEMBERS["closing"] = True
        _raise_held()


def _install_signal_handlers(previous):
    """Install _close_on_signal for SIGINT (when it has Python's default handler) and SIGTERM
    (when it has the default action), on the main thread only, recording {signum: previous} in
    previous for _restore_signal_handlers BEFORE each install, so a signal raising right after the
    install still leaves the caller's disposition recorded. A signal the caller set to be ignored,
    or gave its own handler, is left as it is."""
    if threading.current_thread() is not threading.main_thread():
        return
    for name, default in (("SIGINT", signal.default_int_handler), ("SIGTERM", signal.SIG_DFL)):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        current = signal.getsignal(signum)
        if current == default:
            previous[signum] = current
            signal.signal(signum, _close_on_signal)


def _restore_signal_handlers(previous):
    """Restore the dispositions _install_signal_handlers recorded, ONCE, after main()'s second
    close, in REVERSE order of installation, so SIGTERM is restored first and SIGINT (whose restored
    handler raises) last: a SIGINT arriving between the two finds _close_on_signal and is deferred,
    and one arriving after SIGINT's restore raises with SIGTERM already restored, never leaving
    _close_on_signal installed. main() then raises a deferred signal (_raise_held).
    config/member-close-signal-sweep pins the order (the signal.signal calls made here are
    recorded) and a signal at every point of this function."""
    for signum in reversed(list(previous)):
        signal.signal(signum, previous[signum])


def _finish_members():
    """Run the suite's close (_close_members) and report its problems on stdout; return them.
    Idempotent: main() runs it twice, with the close handlers installed throughout."""
    problems = _close_members()
    if problems:
        _report_guarded("MEMBER CLOSE: {}".format("; ".join(problems)), sys.stdout)
    return problems


def _final_verdict(code, unresolved, refused=()):
    """main()'s exit code after the suite's close. code is _suite_main's (0 when nothing was found);
    unresolved, the problems main()'s second close still met (a group not confirmed empty, a kill
    refused, a registry lock not granted), and refused, every registry lock refusal of the run
    (_lock_refused at its five sites: a member start, a release, a kill sweep, any of the close's
    own three acquires and the config pool's failed-member branch, even one a later close made
    good), make the
    run cannot-evaluate: 0 becomes 2, a definite failure's 1 and a harness error's 2
    stand, and the problems are listed. The PASS line is printed here, after the close, and only
    for a run that is still 0."""
    problems = list(unresolved) + [entry for entry in refused if entry not in unresolved]
    if problems:
        note = ("the suite's close left member process groups unresolved" if unresolved else
                "the member registry lock was refused during the run")
        print(("  also cannot evaluate ({}):" if code == 1 else
               "SELF-TEST CANNOT EVALUATE: {}:").format(note), flush=True)
        for problem in problems:
            print("  - " + problem, flush=True)
        return code or 2
    if code == 0:
        print("SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
              "tools/selftest_checks.toml".format(len(EXECUTED)), flush=True)
    return code


def _report_guarded(text, sink):
    """Print a secondary diagnostic line on sink. A stream that refuses it loses that line: the
    diagnostic never replaces the result it accompanies or an exception already propagating."""
    try:
        print(text, file=sink, flush=True)
    except Exception:  # a refused secondary diagnostic is dropped, never raised
        pass


def _run_bounded(argv, cwd, env, bound, label, banner, cannot_evaluate=None, report=None):
    """Run argv under a time bound; return (returncode, stdout, stderr), or (message, "", "") for
    a launch error (str(exc)) or a timeout ("TIMEOUT: ..."). The child leads its own session and
    process group (the start_new_session and killpg pattern of opf/tools/_opf_pack_manifest.py),
    so its cleanup kills the WHOLE group: a grandchild holding the output pipe, or one left behind
    by a leader that already exited, dies with it.
    A timeout is RECORDED FIRST, in cannot_evaluate (default CANNOT_EVALUATE), and announced as
    banner + message on report (default sys.stdout, never stderr: the cannot-evaluate path is a
    stdout report plus exit 2, and the execution gate requires the suite's error stream to hold
    only declared bytes); only then is the child cleaned up. An OSError, SubprocessError or
    ValueError from the wait is returned as its message; any other exception from the wait or the
    timeout handler (the message, the record, the announcement) propagates unchanged.
    THE MEMBER NEVER RUNS ON THE MAIN THREAD. Called from the main thread, this starts the child,
    waits for it and cleans it up on a separate thread and joins it (the result, or the exception
    it raised, is handed back); called from any other thread (the config lanes' pool workers) it
    does so inline. CPython raises a signal handler's exception only on the main thread, so no
    interrupt can land between Popen and the registration, inside communicate() (whose own
    interrupt handling reaps the leader), or inside the cleanup. An interrupt reaching the main
    thread while it waits propagates at once; the member's group stays registered and main()'s
    close kills it (_close_members). The wait is taken in slices of MEMBER_WAIT_SLICE_SECONDS, and
    once closing is set the member stops waiting and runs its own cleanup (its result is then the
    message "<label> was stopped by the suite's close (no result)"), so after an interrupt or a
    failed member no member thread waits on to its bound.
    CLEANUP ON EVERY EXIT OF THE WAIT: the child is registered when it starts
    (_start_member_group), and a finally ends its group however the wait ends, a normal return
    included (_end_member_group: the group SIGKILL, the bounded drain and reap, the bounded
    confirmation that the group is empty, then the release). THE OUTCOME WINS: the return or the
    exception already propagating is never replaced by the cleanup; a cleanup problem is reported
    as one line on the same stream (_report_guarded).
    config/member-timeout-record-before-cleanup, config/member-timeout-cleanup-on-raise,
    config/member-timeout-diagnostic-guarded, config/member-timeout-interrupt-cleanup,
    config/member-off-main-thread, config/member-grandchild-after-normal-exit and
    config/member-close-on-interrupt pin that order and those guards.
    DISCLOSED RESIDUALS: without a pidfd process-group signal (no os.pidfd_open, or Linux before
    6.9) a group whose leader was already reaped (a normal return) is not killed, since its
    number could then name a reused group, so a descendant left in it survives there, the group
    stays registered (the emptiness probe finds it) and the close's problems make the run
    cannot-evaluate; config/member-grandchild-after-normal-exit pins BOTH paths on every kernel
    (the pidfd path where the kernel has it, and the fallback forced with a flag the kernel
    refuses with EINVAL): retention, then release once the survivor is killed. Which path the CI
    runner's kernel takes is not known here. A thread other than the member's own could also read
    the leader as unreaped just before the member thread reaps it and signal the number then. A
    descendant that moved itself to another process group (setsid or setpgid) survives the kill
    and can hold the member's output pipe: on a normal wait the member then waits to its bound, and
    after the bound, or once closing is set (an interrupt, a failed member, the close), its cleanup
    drains for at most CONFIG_MEMBER_DRAIN_SECONDS before the pipes are closed. After an interrupt
    the member thread therefore ends within MEMBER_WAIT_SLICE_SECONDS + CONFIG_MEMBER_DRAIN_SECONDS
    + CONFIG_MEMBER_REAP_SECONDS + CONFIG_MEMBER_EMPTY_SECONDS (about 40.5 s), inside the close's
    own wait (that sum minus the slice, plus 10 s); config/member-close-on-interrupt pins that
    with a setsid pipe holder. In its own session the child has no controlling terminal: a read of
    an inherited terminal fails."""
    args = (argv, cwd, env, bound, label, banner, cannot_evaluate, report)
    if threading.current_thread() is not threading.main_thread():
        return _run_member(*args)
    outcome = []

    def member():
        try:
            outcome.append((_run_member(*args), None))
        except BaseException as exc:  # handed back to the main thread and raised there
            outcome.append((None, exc))

    worker = threading.Thread(target=member, name="bounded member")
    worker.start()
    worker.join()
    result, exc = outcome[0]
    if exc is not None:
        raise exc
    return result


def _run_member(argv, cwd, env, bound, label, banner, cannot_evaluate, report):
    """_run_bounded's body, on a thread other than the main thread."""
    sink = sys.stdout if report is None else report
    try:
        group = _start_member_group(argv, cwd, env)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc), "", ""
    except RegistryLockTimeout as exc:
        message = "TIMEOUT: {} (cannot evaluate)".format(_lock_refused(
            "{} not started".format(label), exc))
        (CANNOT_EVALUATE if cannot_evaluate is None else cannot_evaluate).append(message)
        print(banner + message, file=sink, flush=True)
        return message, "", ""
    proc, recorded = group.proc, []
    deadline = time.monotonic() + bound
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=min(
                    MEMBER_WAIT_SLICE_SECONDS, max(deadline - time.monotonic(), 0)))
                break
            except subprocess.TimeoutExpired:
                if _MEMBERS["closing"]:
                    return "{} was stopped by the suite's close (no result)".format(label), "", ""
                if time.monotonic() < deadline:
                    continue
                message = "TIMEOUT: {} reached its {} s bound (cannot evaluate)".format(label,
                                                                                      bound)
                (CANNOT_EVALUATE if cannot_evaluate is None else cannot_evaluate).append(message)
                recorded.append(message)
                print(banner + message, file=sink, flush=True)
                return message, "", ""
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                return str(exc), "", ""
        return proc.returncode, stdout, stderr
    finally:
        _end_member_group(group, sink, banner, recorded)


def _run_config_member(member, env, bound=None, cannot_evaluate=None, report=None):
    """Run one registered member under its bound (_config_member_bound) through _run_bounded;
    return its exit code, or a string for a launch error or a timeout. A member that exits
    non-zero has its output printed on report (default sys.stdout, never stderr), so the
    execution gate reads a real member failure as the suite's FAIL (exit 1), not as undeclared
    stderr (cannot evaluate)."""
    bound = _config_member_bound(member) if bound is None else bound
    rc, stdout, stderr = _run_bounded(
        [sys.executable, "-I", "-B", str(ROOT / member[0]), *member[1:]], ROOT, env, bound,
        " ".join(member), "CONFIG-INJECTION ", cannot_evaluate, report)
    if not isinstance(rc, str) and rc:
        print("CONFIG-INJECTION {}:\n{}".format(member, stdout + stderr),
              file=sys.stdout if report is None else report, flush=True)
    return rc


def _split_offenders(results, recorded=None):
    """The registered argv whose config run was not clean, split in results order into
    (definite, timed_out, timeout messages). A member is timed_out when its run only TIMED OUT:
    its exit-code slot is a timeout message (which names it) recorded in recorded (default
    CANNOT_EVALUATE), with no hook, monitor or exposure evidence. A timed-out member that also
    leaked, an unrecorded timeout message, and every other unclean result are definite."""
    recorded = CANNOT_EVALUATE if recorded is None else recorded
    clean = (0, b"", b"", b"")
    definite, timed_out, causes = [], [], []
    for argv, value in results.items():
        if value == clean:
            continue
        if (isinstance(value, tuple) and len(value) == 4 and _is_timeout(value[0])
                and value[1:] == (b"", b"", b"") and value[0] in recorded):
            timed_out.append(argv)
            causes.append(value[0])
        else:
            definite.append(argv)
    return definite, timed_out, tuple(causes)


def _registered_offenders(results, recorded=None):
    """(offenders, cause) for a config lane's check (_split_offenders). When any definite
    offender exists, offenders lists ONLY the definite ones and cause is (): the FAIL never names
    a member whose run only timed out, which is reported by its own recorded timeout message on
    the cannot-evaluate list instead. Otherwise offenders lists the timed-out members and cause
    their timeout messages, so the finding is attributed to those timeouts."""
    definite, timed_out, causes = _split_offenders(results, recorded)
    if definite:
        return definite, ()
    return timed_out, causes


def _pid_state(pid, proc_root="/proc"):
    """"alive", "dead", or "cannot-evaluate: ..." for pid, read from proc_root/<pid>/stat. A
    zombie or dead state is "dead" (a killed orphan awaiting its reaper); an absent entry is
    "dead" only while proc_root/self proves the process table readable. Unreadable or malformed
    evidence is cannot-evaluate, never "dead"."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8") as stat:
            state = stat.read().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        if os.path.isdir(os.path.join(proc_root, "self")):
            return "dead"
        return "cannot-evaluate: {} has no self entry".format(proc_root)
    except (OSError, ValueError, IndexError) as exc:
        return "cannot-evaluate: {}: {}".format(type(exc).__name__, exc)
    if state in ("Z", "X", "x"):
        return "dead"
    if state in ("R", "S", "D", "T", "t", "W", "P", "I", "K"):
        return "alive"
    return "cannot-evaluate: unknown state {!r}".format(state)


# The child for config/member-timeout-main-verdict: main() runs with its lanes replaced by one
# real config-member timeout under the production defaults (the default bound lookup with a
# short default, CANNOT_EVALUATE, sys.stdout), a finding attributed to it, and in the definite
# scenario one unrelated definite finding; the expectation set is the executed set.
_MAIN_VERDICT_CHILD = "\n".join((
    "import os, sys",
    "sys.path.insert(0, sys.argv[1])",
    "import selftest_git_fixture_env as suite",
    "member, scenario = sys.argv[2], sys.argv[3]",
    "suite.CONFIG_MEMBER_BOUND_DEFAULT = 5",
    "def lanes(base):",
    "    suite.check('probe/attributed-rc', suite._run_config_member((member,), dict(os.environ)), 0)",
    "    if scenario == 'definite':",
    "        suite.check('probe/definite', 1, 0)",
    "suite._expected_check_ids = lambda: set(suite.EXECUTED)",
    "sys.exit(suite.main(None, lanes))",
))
MAIN_VERDICT_EXPECTED = [(True, True, True, False, False, True, ""),
                         (True, True, False, True, True, True, "")]


# The children for config/lane-return-fail-closed: main() runs with an injected lane whose
# return value must never read as a pass. Four vectors, each exiting 0 on an engine whose
# _suite_main returns a non-None lane value unchecked (the reverted guard), and 2 here: a
# lane that executes nothing and returns 0; one that runs every expected id with failures
# and returns 0; a missing expectation manifest with a 0 return; and a pending
# cannot-evaluate record with a 0 return. The child takes the vector index, the probe id,
# the pending record text and the absent manifest name through argv, so all four vectors
# share one child source with no vector-specific literals.
_LANE_RETURN_CHILD = "\n".join((
    "import sys",
    "sys.path.insert(0, sys.argv[1])",
    "import selftest_git_fixture_env as suite",
    "vector, probe_id = int(sys.argv[2]), sys.argv[4]",
    "if vector == 2:",
    "    suite.CHECKS_MANIFEST = suite.Path(sys.argv[3]) / sys.argv[6]",
    "else:",
    "    suite._expected_check_ids = lambda: {probe_id}",
    "if vector == 3:",
    "    suite.CANNOT_EVALUATE.append(sys.argv[5])",
    "def lanes(base):",
    "    if vector == 1:",
    "        for check_id in sorted(suite._expected_check_ids()):",
    "            suite.check(check_id, False, True)",
    "    if vector == 3:",
    "        suite.check(probe_id, True, True)",
    "    return 0",
    "sys.exit(suite.main(None, lanes))",
))
# The vector index each child receives, with the name its observation row carries.
LANE_RETURN_VECTORS = (
    (0, "runs-nothing-0"),
    (1, "all-expected-fail-0"),
    (2, "missing-manifest-0"),
    (3, "pending-cannot-evaluate-0"),
)
LANE_RETURN_EXPECTED = [("runs-nothing-0", 2, True, False),
                        ("all-expected-fail-0", 2, True, False),
                        ("missing-manifest-0", 2, True, False),
                        ("pending-cannot-evaluate-0", 2, True, False)]


def _lane_return_fail_closed(fixture):
    """One (vector name, exit code, harness error named, PASS line printed) observation per
    LANE_RETURN_VECTORS row, or that row timeout message when its child reached the bound;
    config/lane-return-fail-closed compares this against LANE_RETURN_EXPECTED: every vector
    must exit 2 with the harness error on its own (captured) stderr and no PASS line."""
    observed = []
    pending = "TIMEOUT: injected pending record (cannot evaluate)"
    for index, name in LANE_RETURN_VECTORS:
        rc, out, err = _run_bounded(
            [sys.executable, "-I", "-B", "-c", _LANE_RETURN_CHILD, str(ROOT / "tools"),
             str(index), str(fixture), "probe/lane-return", pending,
             "absent-selftest-checks.toml"], str(fixture), dict(os.environ), 300,
            "lane-return child ({})".format(name), "CONTROL ")
        if _is_timeout(rc):
            observed.append(rc)
            continue
        observed.append((name, rc, "SELF-TEST HARNESS ERROR" in err,
                         "SELF-TEST PASS" in out))
    return observed


def _partial_timeout_cause(observed, expected):
    """The timeout messages a per-scenario observation list is attributed to: its timed-out
    entries, and only when every entry that did NOT time out equals its own expectation. A
    definite mismatch in any other entry returns (), so the finding stays definite (exit 1)."""
    timeouts = tuple(entry for entry in observed if _is_timeout(entry))
    if timeouts and len(observed) == len(expected) and all(
            _is_timeout(entry) or entry == want for entry, want in zip(observed, expected)):
        return timeouts
    return ()


def _main_verdict_scenarios(sleeper, fixture, run=None):
    """Run _MAIN_VERDICT_CHILD for the attributed and the definite scenario through run (default
    _run_bounded); return (observed, cause) for config/member-timeout-main-verdict. An entry is
    the scenario's observation tuple, or its timeout message when that child reached its own
    bound; cause is _partial_timeout_cause against MAIN_VERDICT_EXPECTED, so one scenario's
    timeout never hides the other scenario's definite mismatch."""
    run = _run_bounded if run is None else run
    observed = []
    for scenario, want_rc in (("attributed", 2), ("definite", 1)):
        line = "CONFIG-INJECTION TIMEOUT: {} reached its 5 s bound (cannot evaluate)\n".format(
            sleeper)
        rc, out, err = run(
            [sys.executable, "-I", "-B", "-c", _MAIN_VERDICT_CHILD, str(ROOT / "tools"),
             str(sleeper), scenario], str(fixture), dict(os.environ), 300,
            "member-timeout-main-verdict child ({})".format(scenario), "CONTROL ")
        if _is_timeout(rc):
            observed.append(rc)
            continue
        observed.append((rc == want_rc, line in out, "SELF-TEST CANNOT EVALUATE" in out,
                         "SELF-TEST FAIL" in out, "probe/definite" in out,
                         "probe/attributed-rc" in out, err))
    return observed, _partial_timeout_cause(observed, MAIN_VERDICT_EXPECTED)


# The member fixture for config/member-grandchild-after-normal-exit and the _CLOSE_PROBE_CHILD
# checks, run as <fixture> <folder> <tag> <mode>: it records "pid starttime" in
# <folder>/<tag>.leader, starts a grandchild in the member's own process group with its stdio on
# /dev/null (so the grandchild never holds the member's pipes) that records itself in
# <folder>/<tag>.grandchild and sleeps, then either sleeps (mode "sleep") or exits 0 once the
# grandchild is recorded (mode "exit"). Mode "setsid" instead starts a grandchild that calls
# setsid (leaving the member's group, so the group kill never reaches it) and keeps the member's
# stdout, a pipe holder, and then sleeps.
_GROUP_MEMBER_FIXTURE = "\n".join((
    "import os, subprocess, sys, time",
    "def stamp(path):",
    "    with open('/proc/self/stat', encoding='utf-8') as stat:",
    "        start = stat.read().rsplit(')', 1)[1].split()[19]",
    "    with open(path + '.tmp', 'w', encoding='utf-8') as out:",
    "        out.write('{} {}'.format(os.getpid(), start))",
    "    os.replace(path + '.tmp', path)",
    "if sys.argv[1] in ('--grandchild', '--setsid'):",
    "    if sys.argv[1] == '--setsid':",
    "        os.setsid()",
    "    stamp(sys.argv[2])",
    "    time.sleep(600)",
    "    sys.exit(0)",
    "folder, tag, mode = sys.argv[1:4]",
    "stamp(os.path.join(folder, tag + '.leader'))",
    "holder = mode == 'setsid'",
    "subprocess.Popen([sys.executable, '-I', '-B', __file__, '--setsid' if holder else",
    "                  '--grandchild', os.path.join(folder, tag + '.grandchild')],",
    "                 stdin=subprocess.DEVNULL, stdout=None if holder else subprocess.DEVNULL,",
    "                 stderr=subprocess.DEVNULL)",
    "deadline = time.monotonic() + 60",
    "while not os.path.exists(os.path.join(folder, tag + '.grandchild')):",
    "    if time.monotonic() > deadline:",
    "        sys.exit(3)",
    "    time.sleep(0.05)",
    "if mode in ('sleep', 'setsid'):",
    "    time.sleep(600)",
))

# The child for the member-close checks (_member_close_controls): main() with its lanes replaced,
# in its own interpreter (the suite's signal handlers are process-wide), with a 90 s member bound,
# a 3 s drain and a 2 s registry-lock bound. Scenarios: "pooled", two members through the
# production _config_results pool, a REAL SIGINT to the main thread once both run; "communicate",
# one member through _run_bounded from the main thread, the SIGINT sent once the member thread is
# inside the real subprocess communicate(); "leader-first", the same with the member's leader
# killed alone first, so communicate() itself reaps it; "setsid", the same with a setsid pipe
# holder (the member thread must stop waiting at once, not at its bound); "sigterm", the same with
# a process-directed SIGTERM; "install", a real SIGINT delivered inside the installation of the
# SIGINT handler; "close", a member left running on a non-main thread when the lanes return and a
# real SIGINT at the ENTRY of each close (both deferred, the handler installed at both entries,
# and _raise_held raising once after the second close); "split-<inside>-<after>" (T for a SIGTERM,
# I for a SIGINT, each sent to the main thread and handled before the next is sent), the signals
# <inside> sent inside _closing_refusal_control (held there and raised by _reopen_members right
# after it), then the signals <after> one at the ENTRY of each of main()'s closes in turn (held
# there): "split-T-I", "split-T-II", "split-TI-I" and "split-IT-I" must each end in SystemExit 143,
# the precedence holding across the control's drain and main()'s final one;
# "lock", the registry lock wrapped so a real SIGINT is raised right after an acquire by the main
# thread: _close_members called directly with closing clear, a 2 s loop of _close_members under
# real SIGINTs from another thread (each interrupted close must leave the lock free), a member
# start and a close with the lock held by another thread (each must give up at the lock bound),
# then main() with the wrapper armed for the close; "failed", the production pool with member a
# failing its positive control (ValueError) once member b's worker is running (so the branch's
# cancel_futures never cancels b) and member b reaching its member only after the kill sweep.
# Member b times the sweep in stages: it first waits on member a's raise (set however member a's
# branch ends; its expiry is a harness handshake when member a's positive control was observed
# at all, and the "missing" member a observed when the code under test never ran it), then on
# the sweep wrapper's entry (the failed-member branch calling the kill sweep), then on the
# wrapper's release of the harness lock holders (harness-owned), then on the real sweep's
# return. TIMING ALONE IS NEVER A DEFINITE FINDING: every expiry of one of these bounds,
# whoever was slow, only ever records WHICH bound was reached, and the parent grades every
# reached bound cannot-evaluate (fail closed, never a pass, _scenario_timeout_record); the
# monitor likewise records under "unsent" every signal it owed but returned without sending
# (a reached registration or in-communicate bound), and the parent stands down every field
# outside the _STATE_EVIDENCE allowlist while that allowlist stays compared; a
# definite finding needs non-timing evidence the code under test produced (its outcome, the
# registry and recorded process state it left, a refusal record, the "evidence" flags below).
# Member a takes its "sent" stamp only AFTER every harness preparation of its own (the lock
# holder's start and acquire), right before its injected failure or its deliberate handshake
# expiry raises, and the sweep wrapper adds the measured release time to "stalls", which the
# timeliness report discounts, so a harness-held release rarely reaches the completion budget;
# when anything does reach it, that too is cannot-evaluate, never definite. The sweep wrapper
# appends to "branch_swept" when its caller is _config_results itself, and the report's
# "evidence" extra carries, read AFTER main() ended, whether the failed-member branch called
# the kill sweep and that the wrapper's caller check discriminates (a sweep called here,
# outside _config_results, is not counted). Both appends run synchronously inside the sweep
# call, with no bound between the call and the append, and every bound of this scenario
# raises INTO the failed-member handler (the injected ValueError, a HandshakeTimeout and the
# positive control's own TimeoutExpired all surface where the pool's results are consumed),
# so a bound can only enter the branch, never skip it: a False flag is a code fact, which is
# why "evidence" sits on the _STATE_EVIDENCE allowlist, kept compared under any recorded
# timeout. Whether member a's positive control ran and whether member a raised is the
# "member_a" extra, OFF the allowlist: the positive control's 60 s subprocess bound sits
# between member a's worker entering run and those flags, so a merely slow git leaves both
# False with the code healthy (round 17), and no earlier flag is bound-free either (an
# entered-run marker would rest on the pool consuming results in order, a property of the
# code under test, and would still leave a window between the worker's dequeue and the
# marker). A WRONG member_a IS NEVER DEFINITE: member a takes its "sent" stamp only after
# a_arrived is set (failed-signal's interrupt is armed by member a too), and a_raises is set
# in a finally the pool's waiting shutdown joins, so any member_a other than [True, True]
# leaves the send list empty, the timeliness field False and the completion budget
# recorded, which stands member_a down; only a right member_a is ever compared. A pool that
# never ran member a is therefore fail closed, never a pass: the main thread blocks on
# member a's pending result until the parent's 300 s bound ends the child (cannot
# evaluate, no report), or member b's 120 s wait records the "member a observed" bound and
# the budget is reached, which stands the run down;
# "failed-signal", the same with the lock wrapper armed for the failed-member branch;
# "failed-handshake", the same with member b never reporting running, so member a's handshake
# (1 s here) expires and member a raises HandshakeTimeout, never the injected failure;
# "failed-stalled", the deterministic twin of the slow positive control: ONE member, whose
# injected failure raises BEFORE a_arrived is set, before any handshake and before any send,
# so member a's arrival and raise and the send list hold, in every run on any host, exactly
# the state a merely slow git leaves them in, while the branch still sweeps and every
# compared field (member_a [False, False] included) is structural; the empty send list is
# the scenario itself, deliberate (_DELIBERATE_EMPTY_SEND), so nothing stands down in a
# healthy run and a producer tied to those facts on code this run executes, not also
# dependent on any other state that differs from a slow "failed" run, mismatches
# definitely (what it does not reach is THE RESIDUAL stated in _allowlist_producers);
# only a positive control slower than its 60 s bound is recorded (as the reached bound
# "positive control a observed") and stands the run down, cannot-evaluate, never definite. Every
# wait the child makes is bounded, and an expiry is recorded under one of two keys by WHO sets
# the state waited on. HARNESS-ARRANGED state (member b arrived or running, member a's raise
# reaching member b once its positive control was observed, a lock holder's own acquire granted,
# the sweep wrapper's release of those holders, the members' grandchild records
# written by the fixture, a monitor, holder or sender thread finished) expires into the extras
# ("handshake"): the harness could not arrange the scenario, so the parent records that run
# as a harness timeout, cannot-evaluate (_handshake_expired), unless it is the scenario's own
# deliberate expiry; a kind naming a thread its bounded join left alive (its entry ends
# " finished") replaces the whole observation, and every other kind stands down every field
# outside the _STATE_EVIDENCE allowlist (_scenario_timeout_record). State only the CODE UNDER TEST sets (the members' registration,
# _start_member_group; a member thread inside the real communicate, which _run_bounded owns;
# member a's positive control observed at all; the failed-member branch entering its kill sweep
# and the real sweep returning; a delivered signal's deferral growing
# held) expires into the extras ("missing"), naming the reached bound; the parent grades BOTH
# keys cannot-evaluate (_scenario_timeout_record): a reached bound is timing alone, whoever was
# slow, so it fails the run closed and is never a pass, and it is never a definite finding
# either; the defect has to show in the _STATE_EVIDENCE fields, which stay compared beside
# the recorded timeout;
# "unresolved", a member exiting 0 whose group's emptiness probes raise PermissionError (main()
# must exit 2 and never print PASS), then the injection removed and _close_members releasing the
# group; "release-timeout", a member exiting 0 whose release is refused (another thread takes the
# registry lock right after the member's kill and holds it past a 0.2 s lock bound, freeing it once
# _run_bounded returns, so main()'s closes then release the group cleanly), and "start-timeout",
# a member start refused the same way (its timeout recorded only in a private list);
# "sweep-refused", "close-refused", "close-wait-refused", "close-left-refused" and "failed-refused",
# the same refusal (lock bound 0.2 s) at the first close's kill sweep, at each of the first close's
# own three acquires (its first, the wait for the members' cleanup, the sweep of the groups left;
# the extras name the refused with statement's line offset, _close_lock_sites), and at the
# failed-member branch's acquire (member a failing as in "failed", the lanes catching the ValueError and clearing
# closing with _reopen_members, as the suite's own failed-member controls do), each of which
# main() must still end with exit 2, the CANNOT EVALUATE line, no PASS line and one recorded
# refusal; "closing-control", _closing_refusal_control with a real SIGINT at its _run_bounded
# entry: the start refused with the SIGINT held and closing set inside, then the SIGINT raised
# right after the control (nothing after it in the lanes runs), so main() ends in
# KeyboardInterrupt; "late-monitor", the monitor's in_communicate observation gated (held) until
# main()'s teardown releases it right before the post-main worker joins, the member's own 10 s
# bound ending main() first. The teardown installs a deferring SIGINT/SIGTERM handler across the
# joins (with a 0.05 s drain for a process-directed signal still in flight) and then reinstalls
# exactly what main() left, so a worker's signal delivered during a join is absorbed and recorded
# as the "late <name>" handshake instead of destroying the report; a non-deliberate late
# signal is timing alone (no stamp can prove who was slow), so _scenario_timeout_record grades
# it cannot-evaluate, never definite: every field outside the _STATE_EVIDENCE allowlist is
# stood down and the allowlist stays compared; the few statements between
# main()'s end and that install are the disclosed residual window. It prints one PROBE-REPORT
# line: the outcome, how many groups are still
# registered after main(), every recorded process's state (_recorded_states), whether main() ended
# within 30 s of every signal or injected failure (less the stalls the harness's own lock-holder
# releases measurably spent inside that window; a False here is the completion budget
# reached, which the parent records and stands down as cannot-evaluate, never definite), and
# the extras: the handlers restored after
# main() and a real SIGINT raising again, the lock free, the PASS and CANNOT EVALUATE lines, and
# each scenario's own observations.
_CLOSE_PROBE_CHILD = "\n".join((
    "import contextlib, io, json, os, random, signal, sys, threading, time",
    "sys.path.insert(0, sys.argv[1])",
    "import selftest_git_fixture_env as suite",
    "scenario, folder, fixture = sys.argv[2], sys.argv[3], sys.argv[4]",
    "suite.CONFIG_MEMBER_BOUND_DEFAULT = 90",
    "suite.CONFIG_MEMBER_DRAIN_SECONDS = 3",
    "suite.REGISTRY_LOCK_SECONDS = 0.2 if scenario.endswith('-refused') else 2",
    "main_id, sent, extra, fire, workers, stalls = threading.main_thread().ident, [], {}, [], [], []",
    "def interrupt():",
    "    sent.append(time.monotonic())",
    "    signal.pthread_kill(main_id, signal.SIGINT)",
    "class HandshakeTimeout(Exception):",
    "    pass",
    "def handshake(event, bound, what):",
    "    if not event.wait(bound):",
    "        extra.setdefault('handshake', []).append(what)",
    "        raise HandshakeTimeout('{} not within {} s'.format(what, bound))",
    "SIGNALS = {'I': signal.SIGINT, 'T': signal.SIGTERM}",
    "def deliver(code):",
    "    count = len(suite._MEMBERS['held'])",
    "    sent.append(time.monotonic())",
    "    signal.pthread_kill(main_id, SIGNALS[code])",
    "    deadline = time.monotonic() + 10",
    "    while len(suite._MEMBERS['held']) == count:",
    "        if time.monotonic() >= deadline:",
    "            extra.setdefault('missing', []).append('signal ' + code + ' deferred')",
    "            break",
    "real_acquire = suite._MEMBERS_LOCK.acquire",
    "def acquire(timeout=-1):",
    "    got = real_acquire(timeout)",
    "    if got and fire and threading.current_thread() is threading.main_thread():",
    "        del fire[:]",
    "        interrupt()",
    "    return got",
    "suite._MEMBERS_LOCK.acquire = acquire",
    "def lock_free():",
    "    free = suite._MEMBERS_LOCK.acquire(0)",
    "    if free:",
    "        suite._MEMBERS_LOCK.release()",
    "    return free",
    "def hold_lock(bound=30):",
    "    taken, done = threading.Event(), threading.Event()",
    "    def holder():",
    "        got = real_acquire(10)",
    "        if got:",
    "            taken.set()",
    "        if not done.wait(bound):",
    "            extra.setdefault('handshake', []).append('lock hold expired')",
    "        if got:",
    "            suite._MEMBERS_LOCK.release()",
    "    thread = threading.Thread(target=holder, daemon=True)",
    "    thread.start()",
    "    if not taken.wait(30):",
    "        extra.setdefault('handshake', []).append('lock held')",
    "    def release():",
    "        done.set()",
    "        thread.join(30)",
    "        if thread.is_alive():",
    "            extra.setdefault('handshake', []).append('lock holder finished')",
    "    return release",
    "def over_bound(started, bound, what, sink=None):",
    "    if time.monotonic() - started < bound:",
    "        return False",
    "    (extra.setdefault('missing', []) if sink is None else sink).append(what)",
    "    return True",
    "def ready(tags, bound=60):",
    "    deadline = time.monotonic() + bound",
    "    while len(suite._MEMBERS['groups']) != len(tags):",
    "        if time.monotonic() >= deadline:",
    "            extra.setdefault('missing', []).append('members registered')",
    "            return False",
    "        time.sleep(0.05)",
    "    while not all(os.path.exists(os.path.join(folder, tag + '.grandchild'))",
    "                  for tag in tags):",
    "        if time.monotonic() >= deadline:",
    "            extra.setdefault('handshake', []).append('member records')",
    "            return False",
    "        time.sleep(0.05)",
    "    return True",
    "def in_communicate():",
    "    for ident, frame in sys._current_frames().items():",
    "        while ident != main_id and frame is not None:",
    "            if frame.f_code.co_name == 'communicate' and frame.f_code.co_filename.endswith(",
    "                    'subprocess.py'):",
    "                return True",
    "            frame = frame.f_back",
    "    return False",
    "gate = threading.Event()",
    "real_in_communicate = in_communicate",
    "def gated_in_communicate():",
    "    seen = real_in_communicate()",
    "    if seen:",
    "        gate.wait(150)",
    "    return seen",
    "if scenario == 'late-monitor':",
    "    in_communicate = gated_in_communicate",
    "def monitor(tags, bound=60):",
    "    owed = 'SIGTERM' if scenario == 'sigterm' else 'SIGINT'",
    "    if not ready(tags, bound):",
    "        extra.setdefault('unsent', []).append(owed)",
    "        return",
    "    deadline = time.monotonic() + bound",
    "    while scenario != 'pooled' and not in_communicate():",
    "        if time.monotonic() >= deadline:",
    "            extra.setdefault('missing', []).append('member in communicate')",
    "            extra.setdefault('unsent', []).append(owed)",
    "            return",
    "        time.sleep(0.01)",
    "    if scenario == 'leader-first':",
    "        with open(os.path.join(folder, 'a.leader'), encoding='utf-8') as record:",
    "            os.kill(int(record.read().split()[0]), signal.SIGKILL)",
    "    if scenario == 'sigterm':",
    "        sent.append(time.monotonic())",
    "        os.kill(os.getpid(), signal.SIGTERM)",
    "    else:",
    "        interrupt()",
    "def member(tag, mode='sleep'):",
    "    return (fixture, folder, tag, mode)",
    "def argv(tag, mode='sleep'):",
    "    return [sys.executable, '-I', '-B', *member(tag, mode)]",
    "def pool(tags):",
    "    poison = suite.Path(folder) / 'poison'",
    "    poison.mkdir()",
    "    marker, monitor_marker = poison / 'marker', poison / 'monitor'",
    "    marker.write_bytes(b'')",
    "    monitor_marker.write_bytes(b'')",
    "    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': folder,",
    "           'XDG_CONFIG_HOME': folder}",
    "    suite._config_results(tuple(member(tag) for tag in tags), env, marker, monitor_marker)",
    "def stray():",
    "    thread = threading.Thread(target=suite._run_bounded, args=(argv('a'), folder,",
    "                              dict(os.environ), 30, 'close probe', 'PROBE '))",
    "    workers.append(('stray member', thread))",
    "    thread.start()",
    "    ready(('a',))",
    "def direct():",
    "    previous = signal.signal(signal.SIGINT, suite._close_on_signal)",
    "    try:",
    "        suite._MEMBERS.update(closing=False, raised=None, held=[])",
    "        fire.append(True)",
    "        try:",
    "            problems, raised = suite._close_members(), None",
    "        except KeyboardInterrupt:",
    "            problems, raised = None, 'KeyboardInterrupt'",
    "        del fire[:]",
    "        return [raised, lock_free(), problems]",
    "    finally:",
    "        suite._MEMBERS['closing'] = True",
    "        signal.signal(signal.SIGINT, previous)",
    "def loop(seconds):",
    "    stop, hits, stuck = threading.Event(), [0], []",
    "    def sender():",
    "        while not stop.is_set():",
    "            signal.pthread_kill(main_id, signal.SIGINT)",
    "            time.sleep(random.uniform(0, 0.002))",
    "    previous = signal.signal(signal.SIGINT, suite._close_on_signal)",
    "    thread = threading.Thread(target=sender, daemon=True)",
    "    thread.start()",
    "    end = time.monotonic() + seconds",
    "    try:",
    "        while True:",
    "            try:",
    "                if time.monotonic() >= end:",
    "                    break",
    "                suite._MEMBERS.update(closing=False, raised=None, held=[])",
    "                problems = suite._close_members()",
    "                if problems:",
    "                    stuck.append(problems)",
    "                    break",
    "            except KeyboardInterrupt:",
    "                hits[0] += 1",
    "    finally:",
    "        suite._MEMBERS['closing'] = True",
    "        stop.set()",
    "        thread.join(30)",
    "        if thread.is_alive():",
    "            extra.setdefault('handshake', []).append('signal sender finished')",
    "        signal.signal(signal.SIGINT, previous)",
    "    print('LOCK-LOOP interrupts {}'.format(hits[0]), flush=True)",
    "    return [stuck, lock_free()]",
    "def held_elsewhere():",
    "    release = hold_lock()",
    "    try:",
    "        suite._MEMBERS['closing'], record, started = False, [], time.monotonic()",
    "        rc = suite._run_bounded([sys.executable, '-I', '-B', '-c', 'pass'], folder,",
    "                                dict(os.environ), 30, 'held probe', 'PROBE ', record)[0]",
    "        problems = suite._close_members()",
    "        over_bound(started, 20, 'held probe within bound')",
    "        return [suite._is_timeout(rc), record == [rc],",
    "                ['not granted' in problem for problem in problems]]",
    "    finally:",
    "        release()",
    "        suite._MEMBERS['closing'] = True",
    "def lanes(base):",
    "    if scenario == 'pooled':",
    "        thread = threading.Thread(target=monitor, args=(('a', 'b'),), daemon=True)",
    "        workers.append(('monitor', thread))",
    "        thread.start()",
    "        pool(('a', 'b'))",
    "    elif scenario == 'failed-refused':",
    "        try:",
    "            pool(('a', 'b'))",
    "        except ValueError as exc:",
    "            extra['caught'] = str(exc)",
    "        suite._reopen_members()",
    "    elif scenario == 'failed-stalled':",
    "        pool(('a',))",
    "    elif scenario.startswith('failed'):",
    "        pool(('a', 'b'))",
    "    elif scenario == 'closing-control' or scenario.startswith('split-'):",
    "        inside, after = scenario.split('-')[1:] if scenario != 'closing-control' else ('', '')",
    "        real_bounded, real_close = suite._run_bounded, suite._close_members",
    "        def close():",
    "            index = len(extra.get('closes', []))",
    "            if index < len(after):",
    "                deliver(after[index])",
    "            extra.setdefault('closes', []).append(list(suite._MEMBERS['held']))",
    "            return real_close()",
    "        if after:",
    "            suite._close_members = close",
    "        def bounded(*args, **kwargs):",
    "            if inside:",
    "                for code in inside:",
    "                    deliver(code)",
    "            else:",
    "                interrupt()",
    "            result = real_bounded(*args, **kwargs)",
    "            extra['control'] = [result[0], list(suite._MEMBERS['held']),",
    "                                suite._MEMBERS['closing']]",
    "            return result",
    "        suite._run_bounded = bounded",
    "        try:",
    "            extra['rc'] = suite._closing_refusal_control(suite.Path(folder),",
    "                                                         suite.subprocess.Popen)",
    "        finally:",
    "            suite._run_bounded = real_bounded",
    "        extra['after'] = True",
    "    elif scenario in ('close-refused', 'sweep-refused'):",
    "        name = '_close_members' if scenario == 'close-refused' else '_signal_member_groups'",
    "        real_step = getattr(suite, name)",
    "        def refused_once():",
    "            setattr(suite, name, real_step)",
    "            release = hold_lock()",
    "            try:",
    "                return real_step()",
    "            finally:",
    "                release()",
    "        setattr(suite, name, refused_once)",
    "        sent.append(time.monotonic())",
    "    elif scenario in ('close-wait-refused', 'close-left-refused'):",
    "        real_close, sites, releases = suite._close_members, [], []",
    "        target = 2 if scenario == 'close-wait-refused' else 3",
    "        def site_acquire(timeout=-1):",
    "            caller = sys._getframe(2)",
    "            if caller.f_code is real_close.__code__ and caller.f_lineno not in sites:",
    "                sites.append(caller.f_lineno)",
    "                if len(sites) == target:",
    "                    extra['site'] = caller.f_lineno - real_close.__code__.co_firstlineno",
    "                    releases.append(hold_lock())",
    "            return acquire(timeout)",
    "        def refused_at_site():",
    "            suite._close_members, suite._MEMBERS_LOCK.acquire = real_close, site_acquire",
    "            try:",
    "                return real_close()",
    "            finally:",
    "                suite._MEMBERS_LOCK.acquire = acquire",
    "                for release in releases:",
    "                    release()",
    "        suite._close_members = refused_at_site",
    "        sent.append(time.monotonic())",
    "    elif scenario in ('close', 'lock'):",
    "        stray()",
    "        if scenario == 'lock':",
    "            fire.append(True)",
    "        else:",
    "            real_close = suite._close_members",
    "            def close():",
    "                extra.setdefault('protected', []).append(",
    "                    signal.getsignal(signal.SIGINT) is suite._close_on_signal)",
    "                interrupt()",
    "                return real_close()",
    "            suite._close_members = close",
    "    elif scenario == 'unresolved':",
    "        suite._run_bounded([sys.executable, '-I', '-B', '-c', 'pass'], folder,",
    "                           dict(os.environ), 90, 'unresolved probe', 'PROBE ')",
    "        sent.append(time.monotonic())",
    "    elif scenario.endswith('-timeout'):",
    "        suite.REGISTRY_LOCK_SECONDS = 0.2",
    "        record, real_stop, releases = [], suite._stop_member_group, []",
    "        def stop(group):",
    "            problems = real_stop(group)",
    "            releases.append(hold_lock())",
    "            return problems",
    "        if scenario == 'start-timeout':",
    "            releases.append(hold_lock())",
    "        else:",
    "            suite._stop_member_group = stop",
    "        try:",
    "            rc = suite._run_bounded([sys.executable, '-I', '-B', '-c', 'pass'], folder,",
    "                                    dict(os.environ), 90, 'lock probe', 'PROBE ', record)[0]",
    "        finally:",
    "            for release in releases:",
    "                release()",
    "        extra['run'] = [str(rc)[:9], len(record)]",
    "        sent.append(time.monotonic())",
    "    else:",
    "        thread = threading.Thread(target=monitor, args=(('a',),), daemon=True)",
    "        workers.append(('monitor', thread))",
    "        thread.start()",
    "        suite._run_bounded(argv('a', 'setsid' if scenario == 'setsid' else 'sleep'), folder,",
    "                           dict(os.environ), 10 if scenario == 'late-monitor' else 90,",
    "                           'probe', 'PROBE ')",
    "real_signal, real_pidfd, real_killpg = signal.signal, signal.pidfd_send_signal, os.killpg",
    "if scenario == 'lock':",
    "    extra['monitor_pin'] = []",
    "    for pin_tags in (('never',), ()):",
    "        monitor(pin_tags, 0.1)",
    "        extra['monitor_pin'].append([extra.pop('missing', None), extra.pop('unsent', None),",
    "                                     len(sent)])",
    "    release, expiry_deadline = hold_lock(0.2), time.monotonic() + 30",
    "    while (time.monotonic() < expiry_deadline",
    "           and 'lock hold expired' not in (extra.get('handshake') or ())):",
    "        time.sleep(0.01)",
    "    release()",
    "    # seen and free are read only AFTER release() joined the holder: a holder paused",
    "    # between recording its expiry and releasing the lock can never pin free=False, and",
    "    # a holder the join left alive is 'lock holder finished', whole replacement.",
    "    seen, free = (extra.get('handshake') or []).count('lock hold expired'), lock_free()",
    "    if seen:",
    "        extra['handshake'].remove('lock hold expired')",
    "        if not extra['handshake']:",
    "            del extra['handshake']",
    "    else:",
    "        extra.setdefault('handshake', []).append('expiry pin')",
    "    extra['expiry_pin'] = [min(seen, 1), free]",
    "    pin_sink = []",
    "    extra['bound_pin'] = [over_bound(time.monotonic() - 1, 0.5, 'pin', pin_sink),",
    "                          list(pin_sink), over_bound(time.monotonic(), 3600, 'pin',",
    "                          pin_sink), list(pin_sink)]",
    "    extra['direct'], extra['loop'], extra['held'] = direct(), loop(2), held_elsewhere()",
    "    del sent[:]",
    "elif scenario == 'install':",
    "    def installing(signum, handler):",
    "        old = real_signal(signum, handler)",
    "        if handler is suite._close_on_signal and signum == signal.SIGINT and not sent:",
    "            interrupt()",
    "        return old",
    "    signal.signal = installing",
    "elif scenario.startswith('failed'):",
    "    swept, b_running, b_arrived = threading.Event(), threading.Event(), threading.Event()",
    "    a_arrived, a_raises, late, releases = threading.Event(), threading.Event(), [], []",
    "    sweep_entered, released, branch_swept = threading.Event(), threading.Event(), []",
    "    real_sweep, real_observed = suite._signal_member_groups, suite._require_wrapper_observed",
    "    real_member = suite._run_config_member",
    "    def sweep():",
    "        frame = sys._getframe(1)",
    "        while frame is not None and frame.f_code.co_name != '_config_results':",
    "            frame = frame.f_back",
    "        if frame is not None:",
    "            branch_swept.append(True)",
    "        sweep_entered.set()",
    "        if releases:",
    "            start = time.monotonic()",
    "            for release in releases:",
    "                release()",
    "            del releases[:]",
    "            stalls.append(time.monotonic() - start)",
    "        released.set()",
    "        problems = real_sweep()",
    "        swept.set()",
    "        return problems",
    "    def observed(bypass, what):",
    "        real_observed(bypass, what)",
    "        if what.startswith('positive control') and what.endswith(' a sleep'):",
    "            if scenario == 'failed-stalled':",
    "                # The deterministic twin of a merely slow git (round 20): member a",
    "                # fails WITHOUT arriving, so a_arrived, a_raises and the send list",
    "                # leave this run in the exact state a slow positive control leaves",
    "                # a 'failed' run in, in every run, on any host.",
    "                raise ValueError('injected failed member a')",
    "            a_arrived.set()",
    "            try:",
    "                if scenario == 'failed-handshake':",
    "                    try:",
    "                        handshake(b_arrived, 30, 'member b arrived')",
    "                        handshake(b_running, 1, 'member b running')",
    "                    finally:",
    "                        sent.append(time.monotonic())",
    "                else:",
    "                    handshake(b_running, 30, 'member b running')",
    "                    if scenario == 'failed-signal':",
    "                        fire.append(True)",
    "                    if scenario == 'failed-refused':",
    "                        releases.append(hold_lock())",
    "                    sent.append(time.monotonic())",
    "                raise ValueError('injected failed member a')",
    "            finally:",
    "                a_raises.set()",
    "        if what.startswith('positive control') and what.endswith(' b sleep'):",
    "            b_arrived.set()",
    "            if scenario != 'failed-handshake':",
    "                b_running.set()",
    "            if not a_raises.wait(120):",
    "                if a_arrived.is_set():",
    "                    extra.setdefault('handshake', []).append('member a raised')",
    "                else:",
    "                    extra.setdefault('missing', []).append('member a observed')",
    "            elif not sweep_entered.wait(30):",
    "                extra.setdefault('missing', []).append('kill sweep')",
    "            elif not released.wait(30):",
    "                extra.setdefault('handshake', []).append('lock released')",
    "            elif not swept.wait(30):",
    "                extra.setdefault('missing', []).append('kill sweep finished')",
    "    def run_member(member_argv, env, *rest, **kwargs):",
    "        rc = real_member(member_argv, env, *rest, **kwargs)",
    "        late.append([member_argv[2], rc])",
    "        return rc",
    "    suite._signal_member_groups, suite._require_wrapper_observed = sweep, observed",
    "    suite._run_config_member = run_member",
    "    extra['late'] = late",
    "elif scenario == 'unresolved':",
    "    def unreadable(real):",
    "        def probe(target, sig, *rest):",
    "            if sig == 0:",
    "                raise PermissionError(1, 'injected unreadable group')",
    "            return real(target, sig, *rest)",
    "        return probe",
    "    signal.pidfd_send_signal, os.killpg = unreadable(real_pidfd), unreadable(real_killpg)",
    "suite._expected_check_ids = lambda: set(suite.EXECUTED)",
    "captured = io.StringIO()",
    "try:",
    "    with contextlib.redirect_stdout(captured):",
    "        outcome = 'returned {}'.format(suite.main(None, lanes))",
    "except KeyboardInterrupt:",
    "    outcome = 'KeyboardInterrupt'",
    "except SystemExit as exc:",
    "    outcome = 'SystemExit {}'.format(exc.code)",
    "except Exception as exc:",
    "    outcome = '{}: {}'.format(type(exc).__name__, exc)",
    "ended = time.monotonic()",
    "del fire[:]",
    "def absorb(signum, frame):",
    "    extra.setdefault('handshake', []).append('late ' + signal.Signals(signum).name)",
    "left = [real_signal(signal.SIGINT, absorb), real_signal(signal.SIGTERM, absorb)]",
    "signal.signal, signal.pidfd_send_signal, os.killpg = real_signal, real_pidfd, real_killpg",
    "gate.set()",
    "for name, thread in workers:",
    "    thread.join(120)",
    "    if thread.is_alive():",
    "        extra.setdefault('handshake', []).append(name + ' finished')",
    "time.sleep(0.05)",
    "real_signal(signal.SIGINT, left[0])",
    "real_signal(signal.SIGTERM, left[1])",
    "registered = len(suite._MEMBERS['groups'])",
    "if scenario.startswith('failed'):",
    "    # member a's arrival and raise sit BEHIND the positive control's 60 s subprocess",
    "    # bound: a merely slow git leaves both False with the code healthy, so they are",
    "    # the member_a extra, off the _STATE_EVIDENCE allowlist (a wrong member_a always",
    "    # comes with the completion budget recorded, so it is never definite; in",
    "    # 'failed-stalled' the empty send list is the scenario itself, deliberate, and",
    "    # member_a [False, False] is compared definitely). evidence keeps only the",
    "    # branch facts no bound shapes; producer-map pins what it reads, and the",
    "    # 'failed-stalled' run pins part of the same class BY VALUE; what neither",
    "    # reaches is THE RESIDUAL the suite's _allowlist_producers states.",
    "    if scenario == 'failed-stalled' and outcome.startswith('TimeoutExpired'):",
    "        # Only a positive control slower than its 60 s bound reaches here: the",
    "        # reached bound is recorded and the run stands down, cannot-evaluate,",
    "        # never a definite mismatch on a merely slow host.",
    "        extra.setdefault('missing', []).append('positive control a observed')",
    "    extra['member_a'] = [a_arrived.is_set(), a_raises.is_set()]",
    "    extra['evidence'] = [bool(branch_swept)]",
    "    witnessed = len(branch_swept)",
    "    sweep()",
    "    extra['evidence'].append(len(branch_swept) == witnessed)",
    "if scenario == 'unresolved':",
    "    problems = suite._close_members()",
    "    extra['released'] = [problems, len(suite._MEMBERS['groups'])]",
    "if scenario.endswith(('-timeout', '-refused')):",
    "    extra['refused'] = len(suite._MEMBERS['refused'])",
    "restored = [signal.getsignal(signal.SIGINT) is signal.default_int_handler,",
    "            signal.getsignal(signal.SIGTERM) == signal.SIG_DFL]",
    "try:",
    "    signal.pthread_kill(main_id, signal.SIGINT)",
    "    restored.append(False)",
    "except KeyboardInterrupt:",
    "    restored.append(True)",
    "extra.update(restored=restored, lock_free=lock_free(), lines=[",
    "    'SELF-TEST PASS' in captured.getvalue(),",
    "    'SELF-TEST CANNOT EVALUATE' in captured.getvalue()])",
    "print(captured.getvalue(), end='', flush=True)",
    "print('PROBE-REPORT ' + json.dumps([outcome, registered, suite._recorded_states(folder),",
    "      bool(sent) and all(ended - when - sum(stalls) < 30 for when in sent), extra]), flush=True)",
))


# The child config/member-close-signal-sweep runs (python3 -I -B -c, argv: the tools directory):
# THE SIGNAL-BOUNDARY SWEEP of main(), over two suite bodies: "body" returns 0; "branch" drives
# the config pool's REAL failed-member branch (_config_results with an empty roster and a stub pool
# whose map raises ValueError, so main() ends in that ValueError when no signal is sent). The
# registry holds one group whose own cleanup has run, and the only stubs are the process-touching
# ones (the group kill is recorded, its emptiness probe finds the group empty), the pool, a recorder
# around _close_members and a recorder of the signal.signal calls _restore_signal_handlers makes;
# main(), _finish_members, _close_members, the kill sweep, the release, the bounded registry lock
# (0.01 s here), the failed-member branch and the handlers are the production ones. Each run
# delivers REAL signals (signal.raise_signal, so the handler installed at that point runs) at one
# point of the window from the body's first line through the end of _restore_signal_handlers:
# "line", a SIGINT, and separately a SIGTERM, at every line event (sys.settrace); "instruction", the
# same before every bytecode instruction (sys.monitoring); "pair", a first signal at every line
# event and a second of the other kind at every later line event of that run (sys.monitoring:
# CPython drops a trace function that raised, so sys.settrace cannot deliver a second signal after a
# first that raised). A signal is sent only while its handler is _close_on_signal, or, for SIGINT,
# the restored default handler (a SIGTERM restored to its default action would end the child);
# nothing is injected inside _close_on_signal itself (a handler cut off at its entry is the residual
# main() discloses; after its entry it has no signal-delivery point before it records the signal).
# Every run must end with EXACTLY the outcome the deferred-signal precedence gives for the signals
# sent (_raise_held: SIGTERM over SIGINT whatever the order; a SIGINT after SIGINT's restore raises
# there, and the strongest held one, if any, replaces it; with no signal, the body's own outcome),
# no close cut short, at least one close completed clean, the registry empty, the lock free, the
# handlers restored by exactly the calls SIGTERM then SIGINT and nothing left deferred. It prints
# one SWEEP-REPORT line: each body's baseline run, per body, mode and first signal the runs made and
# the first five failing ones (point, offset of the second, the signals sent with how each was
# handled and whether it raised, the outcome, the expected one, each close's problems, groups left,
# the restores), the functions the window reached and the seconds taken. Model-only points: a signal
# at the failed-member branch's except line or at its plain closing store raises before the branch's
# cancel and kill sweep (main()'s closes still end every group, which the run requires); those
# instructions (exception match, forward jump, constant and global loads, the dict store) include no
# call, function entry or backward jump, the points where CPython runs a Python signal handler, so a
# real signal cannot raise there.
_CLOSE_SWEEP_CHILD = "\n".join((
    "import concurrent.futures, contextlib, io, json, shutil, signal, sys, tempfile, time",
    "sys.path.insert(0, sys.argv[1])",
    "import selftest_git_fixture_env as suite",
    "suite.REGISTRY_LOCK_SECONDS = 0.01",
    "tempfile.tempdir = tempfile.mkdtemp(prefix='member-close-sweep-')",
    "M, TOOL = sys.monitoring, sys.monitoring.DEBUGGER_ID",
    "SUITE_FILE = suite.main.__code__.co_filename",
    "RESTORE = suite._restore_signal_handlers.__code__",
    "HANDLER = suite._close_on_signal.__code__",
    "OUTCOME = {signal.SIGINT: 'KeyboardInterrupt', signal.SIGTERM: 'SystemExit 143'}",
    "def body(report_path, lanes):",
    "    code = 0",
    "    return code",
    "class Pool:",
    "    def __init__(self, max_workers):",
    "        pass",
    "    def map(self, fn, items):",
    "        raise ValueError('injected failed member')",
    "    def shutdown(self, wait=True, cancel_futures=False):",
    "        pass",
    "concurrent.futures.ThreadPoolExecutor = Pool",
    "def branch(report_path, lanes):",
    "    suite._config_results((), {}, None, None)",
    "    return 0",
    "BODIES = {body.__code__: 'returned 0', branch.__code__: 'ValueError: injected failed member'}",
    "class Proc:",
    "    pid, returncode, stdout, stderr = 0, 0, None, None",
    "def signal_group(group, sig):",
    "    if sig == 0:",
    "        raise ProcessLookupError(3, 'the group is empty')",
    "    st['kills'] += 1",
    "    return True",
    "real_close, real_signal = suite._close_members, signal.signal",
    "def close():",
    "    st['closes'].append(None)",
    "    problems = real_close()",
    "    st['closes'][-1] = problems",
    "    return problems",
    "def recording_signal(signum, handler):",
    "    if sys._getframe(1).f_code is RESTORE:",
    "        st['restores'].append(int(signum))",
    "    return real_signal(signum, handler)",
    "suite._close_members, signal.signal = close, recording_signal",
    "suite._signal_member_group = signal_group",
    "suite._expected_check_ids = lambda: set(suite.EXECUTED)",
    "st = {}",
    "def send(sig):",
    "    handler = signal.getsignal(sig)",
    "    how = ('ours' if handler is suite._close_on_signal else",
    "           'restored' if handler is signal.default_int_handler else None)",
    "    if how is None:",
    "        return",
    "    entry = [int(sig), how, False]",
    "    st['sent'].append(entry)",
    "    try:",
    "        signal.raise_signal(sig)",
    "    except BaseException:",
    "        entry[2] = True",
    "        raise",
    "def step(code):",
    "    if code.co_filename != SUITE_FILE and code not in BODIES:",
    "        return False",
    "    if not st['open']:",
    "        if code is not st['body']:",
    "            return True",
    "        st['open'] = True",
    "    if st['shut'] or code is HANDLER:",
    "        return True",
    "    if code is RESTORE:",
    "        st['restoring'] = True",
    "    elif st['restoring']:",
    "        st['shut'] = True",
    "        return True",
    "    st['n'] += 1",
    "    st['names'].add(code.co_name)",
    "    plan = st['plan']",
    "    if plan[0] == st['n']:",
    "        st['first'] = st['n']",
    "        send(plan[1])",
    "    elif st['sent'] and plan[2] and st['n'] == st['first'] + plan[2]:",
    "        send(plan[3])",
    "    return True",
    "def tracer(frame, event, arg):",
    "    if event == 'call':",
    "        code = frame.f_code",
    "        return tracer if code.co_filename == SUITE_FILE or code in BODIES else None",
    "    if event == 'line':",
    "        step(frame.f_code)",
    "    return tracer",
    "def on_event(code, where):",
    "    return None if step(code) else M.DISABLE",
    "def lock_free():",
    "    free = suite._MEMBERS_LOCK.acquire(0)",
    "    if free:",
    "        suite._MEMBERS_LOCK.release()",
    "    return free",
    "def strongest(sigs):",
    "    return OUTCOME[signal.SIGTERM if signal.SIGTERM in sigs else signal.SIGINT]",
    "def expected(sent, baseline):",
    "    ours = [sig for sig, how, raised in sent if how == 'ours']",
    "    if any(how == 'restored' for sig, how, raised in sent):",
    "        held = [sig for sig, how, raised in sent if how == 'ours' and not raised]",
    "        return strongest(ours) if held else 'KeyboardInterrupt'",
    "    return strongest(ours) if ours else baseline",
    "def run(how, suite_body, plan):",
    "    st.clear()",
    "    st.update(open=False, shut=False, restoring=False, n=0, first=0, plan=plan, sent=[],",
    "              closes=[], kills=0, names=set(), restores=[], body=suite_body.__code__)",
    "    group = suite._MemberGroup(Proc(), None)",
    "    group.ended = True",
    "    suite._MEMBERS['groups'].clear()",
    "    suite._MEMBERS['groups'].add(group)",
    "    suite._suite_main = suite_body",
    "    real_signal(signal.SIGINT, signal.default_int_handler)",
    "    real_signal(signal.SIGTERM, signal.SIG_DFL)",
    "    if how == 'trace':",
    "        sys.settrace(tracer)",
    "    else:",
    "        M.restart_events()",
    "        M.set_events(TOOL, M.events.LINE if how == 'line' else M.events.INSTRUCTION)",
    "    try:",
    "        with contextlib.redirect_stdout(io.StringIO()):",
    "            outcome = 'returned {}'.format(suite.main(None, None))",
    "    except KeyboardInterrupt:",
    "        outcome = 'KeyboardInterrupt'",
    "    except SystemExit as exc:",
    "        outcome = 'SystemExit {}'.format(exc.code)",
    "    except BaseException as exc:",
    "        outcome = '{}: {}'.format(type(exc).__name__, exc)",
    "    finally:",
    "        sys.settrace(None)",
    "        M.set_events(TOOL, 0)",
    "    sent, closes = st['sent'], st['closes']",
    "    want = expected(sent, BODIES[st['body']])",
    "    good = (outcome == want and None not in closes and [] in closes",
    "            and (sent or len(closes) == 2) and not suite._MEMBERS['groups'] and lock_free()",
    "            and st['restores'] == [signal.SIGTERM, signal.SIGINT]",
    "            and signal.getsignal(signal.SIGINT) is signal.default_int_handler",
    "            and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL",
    "            and suite._MEMBERS['held'] == [])",
    "    return (st['first'] != 0, sent, good,",
    "            [outcome, want, closes, len(suite._MEMBERS['groups']), st['restores']])",
    "def sweep_mode(suite_body, how, pairs, first, second):",
    "    points, bad, point = 0, [], 1",
    "    while len(bad) < 5:",
    "        offset = 1 if pairs else 0",
    "        while True:",
    "            reached, sent, good, seen = run(how, suite_body, (point, first, offset, second))",
    "            if not sent:",
    "                break",
    "            names.update(st['names'])",
    "            points += 1",
    "            if not good:",
    "                bad.append([point, offset, sent, seen])",
    "            if not pairs or len(sent) < 2 or len(bad) >= 5:",
    "                break",
    "            offset += 1",
    "        if not reached:",
    "            break",
    "        point += 1",
    "    return [points, bad]",
    "def sweep():",
    "    report = {}",
    "    for prefix, suite_body in (('', body), ('branch ', branch)):",
    "        baseline = run('trace', suite_body, (0, 0, 0, 0))",
    "        report[prefix + 'baseline'] = [baseline[2], baseline[3]]",
    "        for how, pairs, label in (('trace', False, 'line'),",
    "                                  ('instruction', False, 'instruction'),",
    "                                  ('line', True, 'pair')):",
    "            for first, second in ((signal.SIGINT, signal.SIGTERM),",
    "                                  (signal.SIGTERM, signal.SIGINT)):",
    "                report['{}{} {}'.format(prefix, label, signal.Signals(first).name)] = (",
    "                    sweep_mode(suite_body, how, pairs, first, second))",
    "    report['names'] = sorted(names)",
    "    return report",
    "M.use_tool_id(TOOL, 'member close sweep')",
    "M.register_callback(TOOL, M.events.LINE, on_event)",
    "M.register_callback(TOOL, M.events.INSTRUCTION, on_event)",
    "started, names = time.monotonic(), set()",
    "try:",
    "    report = sweep()",
    "finally:",
    "    shutil.rmtree(tempfile.tempdir, ignore_errors=True)",
    "report['seconds'] = round(time.monotonic() - started, 1)",
    "print('SWEEP-REPORT ' + json.dumps(report), flush=True)",
))


def _read_record(path):
    """The (pid, start time) pair a _GROUP_MEMBER_FIXTURE record holds, read as bytes."""
    with open(path, "rb") as record:
        pid, start = record.read().decode("ascii").split()
    return int(pid), start


def _recorded_states(folder):
    """{record name: state} for every <tag>.leader and <tag>.grandchild record in folder (written
    by _GROUP_MEMBER_FIXTURE): "alive" only when /proc shows that pid live (_pid_state) AND its
    start time equals the recorded one, so a reused pid never reads as the recorded process;
    otherwise "dead", or "cannot-evaluate: ..." for unreadable evidence."""
    states = {}
    for name in sorted(os.listdir(folder)):
        if not name.endswith((".leader", ".grandchild")):
            continue
        try:
            pid, start = _read_record(os.path.join(folder, name))
            state = _pid_state(pid)
            if state == "alive":
                with open("/proc/{}/stat".format(pid), "rb") as stat:
                    if stat.read().decode("ascii", "replace").rsplit(")", 1)[1].split()[19] \
                            != start:
                        state = "dead"
        except FileNotFoundError:
            state = "dead"
        except (OSError, ValueError, IndexError) as exc:
            state = "cannot-evaluate: {}: {}".format(type(exc).__name__, exc)
        states[name] = state
    return states


def _kill_recorded(folder):
    """Harness cleanup: SIGKILL every recorded process still alive with its recorded start time,
    through a pidfd opened BEFORE the start time is re-read, so the signal reaches that process
    and never a later one reusing its pid. Never builds its list from a command-line pattern."""
    for name, state in _recorded_states(folder).items():
        if state != "alive":
            continue
        try:
            fd = os.pidfd_open(_read_record(os.path.join(folder, name))[0])
        except (OSError, ValueError):
            continue
        try:
            if _recorded_states(folder).get(name) == "alive":
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except OSError:
            pass
        finally:
            os.close(fd)


def _pidfd_group_signal_supported():
    """True when this process can signal a process group through a pidfd
    (_signal_member_group's exact path): os.pidfd_open exists and the kernel accepts
    PIDFD_SIGNAL_PROCESS_GROUP. Probed with signal 0 through a pidfd of this process: the flag
    addresses the group this pid leads, so ProcessLookupError (no such group: this process leads
    none) or success both mean the flag was accepted; a kernel without it raises EINVAL."""
    try:
        fd = os.pidfd_open(os.getpid())
    except (AttributeError, OSError):
        return False
    try:
        signal.pidfd_send_signal(fd, 0, None, PIDFD_SIGNAL_PROCESS_GROUP)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


# The _CLOSE_PROBE_CHILD handshakes whose expiry is the scenario itself: in "failed-handshake"
# member b never reports running, so member a's 1 s handshake expires and nothing is injected; in
# "late-monitor" the monitor deliberately fires only after main() ended, so its SIGINT is absorbed
# by the teardown's deferring handler and recorded as the "late SIGINT" handshake.
_DELIBERATE_HANDSHAKES = dict([("failed-handshake", ("member b running",)),
                               ("late-monitor", ("late SIGINT",))])

# The scenarios whose EMPTY send list is the scenario itself: "failed-stalled" owes no signal
# by construction (its one member fails without arriving, deterministically, so nothing is
# ever sent and the completion budget measures nothing), the same way _DELIBERATE_HANDSHAKES
# names each scenario's own deliberate expiry. Only the completion-budget record is exempt:
# a reached code-owned bound, an owed signal or an expired handshake in such a run is still
# recorded and stands the observation down. The "record-map" vectors pin both halves.
_DELIBERATE_EMPTY_SEND = ("failed-stalled",)


def _handshake_expired(scenario, extra):
    """The harness-timeout message for a _CLOSE_PROBE_CHILD run one of whose handshakes expired
    (extra["handshake"], beyond the scenario's deliberate one); None when every handshake was
    met. The "handshake" extras hold ONLY state the harness's own threads arrange (member b's
    arrival and report, member a's raise reaching member b once member a's positive control was
    observed, a holder thread's own acquire granted, the sweep wrapper's release of those
    holders, the members' grandchild records written by the fixture, a monitor, holder or sender
    thread finished) and the signals the teardown absorbed after main() ("late <name>");
    progress the code under test owns (the members' registration, a member thread inside the
    real communicate, member a's positive control observed at all, the failed-member branch
    entering its kill sweep and the real sweep returning, a delivered signal's deferral) expires
    into the "missing" extras instead, which _scenario_timeout_record records as its own
    reached-bound timeout, never folded here: timing alone is never a definite finding, so a
    reached code-owned bound is cannot-evaluate too, fail closed, never a pass, and the defect
    has to show in the _STATE_EVIDENCE fields, which stay compared beside the recorded timeout. A
    handshake the handshake() helper raised expired
    BEFORE the injected failure could be sent, so that failure is never sent after one of those;
    a holder or release expiry ("lock held", "lock holder finished", "lock released") only
    records its entry and the run continues, member a's raise included. How the parent grades
    each expiry, including the late-signal rule, is _scenario_timeout_record's."""
    expired = [what for what in extra.get("handshake") or ()
               if what not in _DELIBERATE_HANDSHAKES.get(scenario, ())]
    if not expired:
        return None
    return "TIMEOUT: member-close child ({}): handshake not met: {} (cannot evaluate)".format(
        scenario, ", ".join(expired))


# THE ALLOWLIST: the only fields of a _CLOSE_PROBE_CHILD observation that stay definite when
# the observation carries ANY recorded timeout (a reached code-owned bound, an owed signal
# never sent, the completion budget, an expired harness handshake of any kind, the expiry
# pin). A field may sit here only because its value is read from FINAL state, after main()
# ended and after every worker, holder and sender thread was joined, so no scheduling delay
# can have produced it; every field outside this tuple (the outcome, the registry count, the
# recorded process states, the timeliness, the printed lines and every other extras key) can
# be shaped by WHEN something ran, so under any recorded timeout it stands down (_stood_down),
# cannot-evaluate, never definite, with no per-site choice a new timeout site could forget.
# Each entry's justification traces its producer to its read with no bound, timeout or
# scheduling delay between the fact and the read, and names the vector pinning it:
# - "restored": whether main() left the handlers restored. Producer: main()'s restore path
#   either ran or did not by the time main() ended; read by getsignal and a real SIGINT
#   raised AFTER the report's join loop, a direct read of final handler state with no wait
#   in it. A thread a bounded join left alive, the one remaining way timing could still
#   change the handlers, replaces the whole observation (the alive-kind record-map vector),
#   so only a defect in the restore itself, never a slow thread, makes this false (the
#   restoration and stray-defect record-map vectors keep that defect definite).
# - "lock_free": the registry lock's final state. Producer: the last joined holder's
#   release (or leak); read by a zero-timeout acquire AFTER the join loop, so the read
#   itself carries no bound, and a joined holder has released or leaked for good. A holder
#   still alive is "lock holder finished", whole replacement (the alive-kind record-map
#   vector); the expiry-pin record-map vectors pin the stand-down of the timing-shaped
#   pin fields beside it.
# - "evidence": whether the failed-member branch itself called the kill sweep, and that
#   the sweep wrapper's caller check discriminates. Producer: the wrapper appends to
#   branch_swept synchronously inside the sweep call (the frame walk is pure computation),
#   so the append IS the fact and no bound sits between the fact and the post-main read;
#   every bound of the failed scenarios raises INTO the handler that calls the sweep, so a
#   bound can only make the branch run, never skip it, and the second flag comes from a
#   direct post-main call. The slow-positive-control record-map vectors are CLASSIFIER
#   pins (the mapping, on a hand-written observation): flags at their want beside the
#   60 s positive-control bound make the run cannot-evaluate, never a definite mismatch,
#   while a false flag beside the same record stays definite. That the child's flags
#   ARE at their want then rests on the producer pin ("producer-map" below) and the
#   "failed-stalled" value pin, short of THE RESIDUAL (_allowlist_producers). Member a's own
#   arrival and raise came OFF this entry (round 17): that 60 s bound sits between
#   member a's worker entering run and those flags, so a merely slow git leaves them
#   False with the code healthy; they are the "member_a" extra, never definite when
#   wrong (a wrong member_a leaves the send list empty, so the completion budget is
#   recorded beside it and it stands down).
# THE PRODUCER PIN, two independent nets, neither complete: the "producer-map" entry
# (_allowlist_producers) pins, read from the child's source and fail closed on the
# constructs it refuses, the exact names every write of each entry above reads, closed
# over their origins through the forms it traces, so a producer re-tied to timing
# (member a's arrival or raise, the send list, a stall) through one of those forms, by a
# read that adds a name to the pinned signature, fails definitively in a healthy run
# too; "producer-oracle" pins that discrimination, one probe per rule it names, on
# mutated copies of the child source. THE VALUE PIN: the
# "failed-stalled" scenario holds member a's arrival and raise and the send list,
# deterministically and in every run, in the exact state a merely slow git leaves them
# in, with every compared field structural, so an edit tying an allowlisted field to
# those facts, on code that run executes, yields there the value a slow-git run would
# give it (a wrong VALUE, reflection and module attributes included) unless it also
# depends on ANY other state that differs between the two runs (the scenario's name,
# member b's presence or absence, the exception outcome's text and similar); a stall's
# presence is pinned the same way by "failed-refused" (stalls nonempty) beside "failed"
# (stalls empty). Neither net catches more than its own mechanics reach: the value pin
# catches a producer only by a compared field differing from its want (a missing report
# included) in a run the scenarios make within their bounds, and the oracle only by a
# refusal or by a change to a pinned signature (a traced read adding a name to it, or a
# name leaving it). So ANY producer that neither net catches passes both nets, whatever the
# condition under which it changes an allowlisted field (what a slow positive control
# alters: arrival, raise, send list, member b's presence, the exception outcome's text; a
# stall's length; the Python version, wall clock or environment; or anything else) and
# whatever carries it (a form the oracle neither traces nor refuses, or a traced test whose
# names are all pinned already, such as pinned_version's if sys.version_info >= (3, 99) or
# a pure-local gate such as if len(branch_swept) > 1): THE RESIDUAL, stated in
# _allowlist_producers beside the oracle's DISCLOSED BOUNDARIES, whose named residual
# probes are examples of it, not the class. Only a run in which that condition holds where
# the producer runs can catch it (a slow positive control is one such run, for a condition
# it alters), and such a catch is never a pin.
# A handshake kind naming a thread its bounded join left ALIVE (the report's join loop,
# hold_lock's release and loop's sender join all record "<name> finished" exactly then) can
# still poison any field, the allowlist included (a live holder owns the lock; a live
# monitor can raise), so it replaces the whole observation (_scenario_timeout_record).
# The record-map pins this tuple's exact membership VERBATIM and the stand-down of every
# extras key the child writes, so adding any key (late, direct, member_a or a new one) or
# removing one fails a vector; the producer-map pins what each kept key reads.
_STATE_EVIDENCE = ("evidence", "lock_free", "restored")


def _stood_down(entry, message):
    """entry (a 7-field observation, or its want) stood down under a recorded timeout: every
    field outside the _STATE_EVIDENCE allowlist becomes message (the outcome, the registry
    count, the recorded process states, the timeliness) or is dropped (every extras key off
    the allowlist), and the extras carry stood_down=message. _scenario_timeout_record applies
    it to the observed side and _stood_down_wants mirrors it onto the want, keyed on that
    marker, so every comparison timing could shape is neutral on both sides while a mismatch
    in an allowlisted field stays a definite finding."""
    down = {key: value for key, value in entry[6].items() if key in _STATE_EVIDENCE}
    down["stood_down"] = message
    return entry[:1] + (message, message, message, message, message, down)


def _scenario_timeout_record(scenario, observation, extra, recorded=None):
    """The got entry for one _CLOSE_PROBE_CHILD observation, under the rule that TIMING ALONE
    IS NEVER A DEFINITE FINDING: an observation whose only evidence of a defect is timing (a
    deadline missed, a signal late, a signal never sent, a completion budget exceeded, a
    merely slow thread) is recorded as a timeout (default CANNOT_EVALUATE), which fails the
    run closed (exit 2) and is never a pass. Every timeout record, whatever produced it, is
    graded by ONE rule instead of a per-site choice of fields: a "missing" entry (a bounded
    wait on code-owned state reached its bound) and an "unsent" entry (the monitor returned
    without sending a signal the harness owed) are popped and recorded; a False timeliness
    field (the completion budget) is recorded, unless the unsent record already covers the
    empty send list or the scenario owes no signal by construction (_DELIBERATE_EMPTY_SEND,
    whose other records stay graded like any scenario's); a non-deliberate expired handshake
    is recorded by _handshake_expired.
    When ANYTHING was recorded, an expired kind naming a thread its bounded join left alive
    (its entry ends " finished") replaces the whole observation with the recorded message (a
    live thread can poison every field, the allowlist included), and otherwise the
    observation is stood down to the last recorded message by _stood_down: only the
    _STATE_EVIDENCE allowlist stays compared, mirrored on the wants side by
    _stood_down_wants, so a restoration defect, a held lock or a false evidence flag beside
    the timeout is STILL A DEFINITE FINDING while every field timing could shape is neutral.
    No field stays definite through a per-timeout decision: a new timeout site gets the
    stand-down for free, and widening or narrowing the allowlist fails the "record-map"
    vectors, which pin each mapping, the _STATE_EVIDENCE tuple's exact membership verbatim
    and the stand-down of every extras key the child writes (member_a, late, direct and the
    rest), so admitting any existing child field fails a vector too."""
    sink = CANNOT_EVALUATE if recorded is None else recorded
    messages = []
    missing = extra.pop("missing", None)
    if missing:
        messages.append("TIMEOUT: member-close child ({}): code-owned bound reached: {} "
                        "(cannot evaluate)".format(scenario, ", ".join(missing)))
    unsent = extra.pop("unsent", None)
    if unsent:
        messages.append("TIMEOUT: member-close child ({}): owed signal never sent: {} "
                        "(cannot evaluate)".format(scenario, ", ".join(unsent)))
    if (not unsent and scenario not in _DELIBERATE_EMPTY_SEND
            and isinstance(observation, tuple) and len(observation) == 7
            and observation[5] is False):
        messages.append("TIMEOUT: member-close child ({}): completion budget reached "
                        "(cannot evaluate)".format(scenario))
    expired_message = _handshake_expired(scenario, extra)
    if expired_message is not None:
        messages.append(expired_message)
    sink.extend(messages)
    if not messages:
        return observation
    message = messages[-1]
    if not (isinstance(observation, tuple) and len(observation) == 7):
        return message
    expired = [what for what in extra.get("handshake") or ()
               if what not in _DELIBERATE_HANDSHAKES.get(scenario, ())]
    if any(what.endswith(" finished") for what in expired):
        return message
    return _stood_down(observation, message)


def _stood_down_wants(observed, wants):
    """The wants with _scenario_timeout_record's stand-downs mirrored, because timing alone
    is never a definite finding: an observed entry _stood_down reshaped carries the recorded
    timeout message under its "stood_down" extras key, and the matching want is reshaped by
    the same _stood_down with the same allowlist, so every comparison timing could shape is
    neutral while the _STATE_EVIDENCE fields are still compared and a mismatch there stays a
    definite finding. Every other entry's want is returned unchanged; an observed extras
    dict can carry the marker through no other path (the child never writes a "stood_down"
    extra, its own "unsent" extra is a list of signal names and is popped before comparison,
    and a whole-observation replacement is a plain message, never a tuple). The "record-map"
    entry pins the substitutions and the pass-through."""
    adjusted = []
    for entry, want in zip(observed, wants):
        if (isinstance(entry, tuple) and len(entry) == 7
                and isinstance(want, tuple) and len(want) == 7):
            extras = entry[6] if isinstance(entry[6], dict) else {}
            if _is_timeout(extras.get("stood_down")):
                want = _stood_down(want, extras["stood_down"])
        adjusted.append(want)
    return adjusted


def _close_lock_sites(close=None):
    """Every acquisition of the registry lock in _close_members, read from the source, in source
    order, each as (line offset from its def line, form): form "with" for a with statement
    directly on _MEMBERS_LOCK (the bounded acquire, _RegistryLock.__enter__) and "acquire" for a
    direct _MEMBERS_LOCK.acquire(...) call; EVERY other reference to the lock's name in the
    function (a bare release, the raw ._lock, an alias, the name passed on, an ATTRIBUTE named
    _MEMBERS_LOCK, as in a module-object access, or the STRING constant "_MEMBERS_LOCK", as in a
    globals() subscript or a getattr call) comes back as "unrecognized", never skipped. config/member-close-unresolved-verdict requires exactly one "with" per
    refused-site scenario ("close-refused", "close-wait-refused" and "close-left-refused" refuse
    the first, second and third through the bounded acquire), so a fourth acquisition, a direct
    acquire call or an unrecognized form fails that pin definitively, and a site that stopped
    taking the lock through the bounded acquire is refused nowhere and fails its scenario. Raises
    LookupError, which the caller records as cannot-evaluate, when the source cannot be read or
    no def NAMED _close_members sits at its recorded line (a def under any other name there, the
    renamed-def mutant, is never accepted), never an empty fail-open result. close (default
    _close_members) is the function whose recorded file and line are read: the "close-oracle"
    control passes probes written to the fixture, so the renamed-def mutant stays pinned.
    Syntactic limit, disclosed: only references inside _close_members' own body that spell the
    name _MEMBERS_LOCK as the bare global name, as an attribute of any value, or as that exact
    string constant are seen; an acquisition through another name bound to the lock (an alias
    made outside the function, a parameter, a container holding it), through a string built at
    run time, or inside a helper it calls is outside this oracle (the helpers' own acquisitions
    have their own refusal scenarios)."""
    close = _close_members if close is None else close
    first = close.__code__.co_firstlineno
    filename = close.__code__.co_filename
    try:
        with open(filename, encoding="utf-8") as source:
            tree = ast.parse(source.read())
    except (OSError, SyntaxError, ValueError) as exc:
        raise LookupError("the close-lock-site oracle cannot read {}: {}".format(filename, exc))
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.lineno == first
                and node.name == "_close_members"):
            break
    else:
        raise LookupError("the close-lock-site oracle found no def named _close_members at line "
                          "{} of {}: it cannot read _close_members".format(first, filename))
    parents = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    sites = []
    for name in ast.walk(node):
        if (isinstance(name, ast.Attribute) and name.attr == "_MEMBERS_LOCK") or (
                isinstance(name, ast.Constant) and name.value == "_MEMBERS_LOCK"):
            sites.append((name.lineno - first, "unrecognized"))
            continue
        if not (isinstance(name, ast.Name) and name.id == "_MEMBERS_LOCK"):
            continue
        parent = parents.get(name)
        grand = parents.get(parent)
        if isinstance(parent, ast.withitem) and parent.context_expr is name:
            sites.append((parents[parent].lineno - first, "with"))
        elif (isinstance(parent, ast.Attribute) and parent.attr == "acquire"
                and isinstance(grand, ast.Call) and grand.func is parent):
            sites.append((grand.lineno - first, "acquire"))
        else:
            sites.append((name.lineno - first, "unrecognized"))
    return sorted(sites)


def _sweep_call_sites(config=None):
    """Every reference to _signal_member_groups in _config_results, read from the source in
    source order like _close_lock_sites, each as (line offset from the def line, form): form
    "except-before-wait" for a call directly on that name inside an except handler of a try
    statement whose finally block waits for the workers (a shutdown(...) call carrying
    wait=True in its finalbody), AND with no worker wait inside the handler itself before
    the call: shutdown(...) waits unless it carries a literal wait=False, so any shutdown
    call positioned before the sweep call in the handler without that literal refuses the
    form (the "sweep-oracle" bare, positional and cancel probes pin this rule: a bare
    shutdown(), a positional shutdown(True, ...) and shutdown(wait=True,
    cancel_futures=False) each refuse, so a detector counting only a literal wait=True,
    or exempting a literal False on any keyword, fails them). The except handler's body
    then runs the kill sweep
    before any wait for the workers, so the sweep precedes that wait STRUCTURALLY, a
    source-order fact with no
    deadline involved. EVERY other reference (a call outside such a handler, the call moved
    after the wait into the finally block, a call after a handler-body wait, a bare name, an
    attribute or a string constant spelling of the name) comes back "unrecognized", never
    skipped. config/member-failed-no-late-launch requires exactly one "except-before-wait"
    site ("sweep-site"), so the mutant that moves the kill sweep after the worker wait,
    turns the handler's own shutdown into a waiting one, or removes the sweep, fails that
    pin definitively. Raises LookupError, which the caller records as cannot-evaluate (never
    a fail-open empty result), when the source cannot be read or no def NAMED
    _config_results sits at its recorded line; the "sweep-oracle" entry pins this
    discrimination on compiled probes. The same syntactic limits as _close_lock_sites apply:
    a sweep called through another name bound to it, or a worker wait spelled through
    anything but a shutdown call (a join, a future's result), is outside this oracle (the
    sweep wrapper's runtime caller witness, the second "evidence" flag, and member b's
    staged bounds cover the ordering dynamically, fail closed)."""
    config = _config_results if config is None else config
    first = config.__code__.co_firstlineno
    filename = config.__code__.co_filename
    try:
        with open(filename, encoding="utf-8") as source:
            tree = ast.parse(source.read())
    except (OSError, SyntaxError, ValueError) as exc:
        raise LookupError("the sweep-site oracle cannot read {}: {}".format(filename, exc))
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.lineno == first
                and node.name == "_config_results"):
            break
    else:
        raise LookupError("the sweep-site oracle found no def named _config_results at line "
                          "{} of {}: it cannot read _config_results".format(first, filename))
    parents = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def waited_finally(handler):
        wrapper = parents.get(handler)
        if not isinstance(wrapper, ast.Try):
            return False
        for final in wrapper.finalbody:
            for inner in ast.walk(final):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "shutdown"
                        and any(key.arg == "wait" and isinstance(key.value, ast.Constant)
                                and key.value.value is True for key in inner.keywords)):
                    return True
        return False

    def waits_before(handler, call):
        # A worker wait inside the handler ITSELF, before the sweep call, breaks the pinned
        # ordering exactly as the finally wait would. shutdown(...) waits unless it carries
        # a literal wait=False, so any shutdown call in the handler without that literal,
        # positioned before the sweep call, refuses the form (fail closed: the site comes
        # back "unrecognized" and the one-site pin fails definitively). The "bare" and
        # "positional" sweep-oracle probes pin the literal rule: a detector counting only
        # a literal wait=True accepts both and fails their vectors; the "cancel" probe
        # (wait=True, cancel_futures=False) pins that the exemption reads the wait
        # keyword itself, so a detector exempting any literal False keyword fails it.
        for inner in ast.walk(handler):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "shutdown"
                    and not any(key.arg == "wait" and isinstance(key.value, ast.Constant)
                                and key.value.value is False for key in inner.keywords)
                    and (inner.lineno, inner.col_offset) < (call.lineno, call.col_offset)):
                return True
        return False

    sites = []
    for name in ast.walk(node):
        if (isinstance(name, ast.Attribute) and name.attr == "_signal_member_groups") or (
                isinstance(name, ast.Constant) and name.value == "_signal_member_groups"):
            sites.append((name.lineno - first, "unrecognized"))
            continue
        if not (isinstance(name, ast.Name) and name.id == "_signal_member_groups"):
            continue
        parent = parents.get(name)
        form = "unrecognized"
        if isinstance(parent, ast.Call) and parent.func is name:
            ancestor = parents.get(parent)
            while ancestor is not None and not isinstance(
                    ancestor, (ast.ExceptHandler, ast.FunctionDef)):
                ancestor = parents.get(ancestor)
            if (isinstance(ancestor, ast.ExceptHandler) and waited_finally(ancestor)
                    and not waits_before(ancestor, parent)):
                form = "except-before-wait"
        sites.append((name.lineno - first, form))
    return sorted(sites)


# The methods through which a name of the child is taken to gain a value: a call of one of
# these on the bare name (anywhere in the child, a helper's body included) is one of that
# name's origins, with its arguments' reads, in _allowlist_producers.
_PRODUCER_MUTATORS = ("append", "extend", "insert", "add", "update", "setdefault")

# Reflective builtins that can reach any binding without spelling its name: ANY use of one of
# these names in the child is "unrecognized" (fail closed), so a producer reaching state
# through them is refused. getattr, setattr and delattr are refused the same way unless
# called directly on an import-bound module object, whose attribute state is a disclosed
# boundary of the oracle.
_REFLECTIVE_NAMES = ("globals", "vars", "locals", "eval", "exec", "compile", "__import__")


def _allowlist_producers(source=None):
    """The producer pin of the _STATE_EVIDENCE allowlist, read from the _CLOSE_PROBE_CHILD
    source (source, default _CLOSE_PROBE_CHILD) and never run: a dict giving, for each
    allowlisted extras key, the sorted names its producers READ, and under "unrecognized"
    every construct this oracle refuses, as (line, form). The record-map vectors pin the
    classifier (which fields a recorded timeout keeps); this pins what feeds the kept
    fields, and the "failed-stalled" scenario pins part of the same class BY VALUE (its run
    holds member a's arrival and raise and the send list in the slow-git state while every
    compared field is structural), so a child that ties evidence, lock_free or restored to
    member a's arrival or raise or to the send list fails a check even in a healthy run,
    where no timeout ever exercises the classifier, when this oracle traces the tie
    through a read that adds a name to a pinned signature or the stalled run executes it
    as a slow run would; THE RESIDUAL below is what neither net catches. A stall's
    presence is pinned by "failed-refused" (stalls nonempty) beside "failed" (stalls
    empty).
    A producer is every write of an allowlisted key: extra['<key>'] = value (a tuple target
    is paired element by element), extra['<key>'].<mutator>(args) and extra.update(<key>=
    value). Its reads are the names loaded in the written value, plus the names loaded in
    the test of every if, while or conditional expression enclosing the write and in every
    operand short-circuiting ahead of it inside an enclosing and/or, closed transitively
    over each read name's origins in the child: an assignment to the name at module scope
    or under a global declaration (paired the same way; a walrus included), a subscript
    store into it (the index's reads included), a _PRODUCER_MUTATORS call on it, a call
    taking the bare non-module name as an argument (the whole call is read: the callee may
    bind or mutate it), each origin with its own enclosing tests up to the enclosing def,
    and, for a module-level def, the names its body loads that it does not bind.
    FAIL CLOSED on the extras dict: EVERY reference through the NAME extra that could
    write an allowlisted key comes back "unrecognized", never skipped: a subscript with a
    non-constant key, an allowlisted key read in any other way (an alias), deleted or
    augmented, a method call other than setdefault, pop, get and update, or one of those
    naming an allowlisted key or a non-constant key (update with positional or **
    arguments included), an attribute not called, a rebinding other than the one binding
    to {}, and a bare use other than as an element of the report list passed to json.dumps
    (reaching the dict WITHOUT the name extra is reflection or a module attribute, below).
    FAIL CLOSED on the carriers: for every name in an allowlisted signature whose origins
    show a mutable literal (branch_swept and restored in the committed child, plus
    whatever an edit routes into a signature), EVERY occurrence of that name outside the
    recognised origin forms is "<name> escape", never skipped: a del, a non-mutator
    method call (clear, pop), a bound method or attribute taken, an alias made of it, a
    bare use in a test or anywhere else. Recognised: an origin target, a mutator
    receiver, a bare call argument (an origin itself) and a traced producer value.
    FAIL CLOSED on reflection: any use of a _REFLECTIVE_NAMES name anywhere in the child
    is "unrecognized", and so is getattr, setattr or delattr except called directly on an
    import-bound module object. Raises LookupError, which the caller records as
    cannot-evaluate, when the source does not parse. Each "producer-oracle" probe (listed
    beside the probes in _member_close_controls) names the rule it is built to pin; that
    is one probe per named rule, not a proof that every rule has one.
    DISCLOSED BOUNDARIES, each named here exactly because this oracle does NOT read it:
    state routed through an attribute of an import-bound module object (a store like
    sys.<attr> = ..., the suite's patched attributes, getattr or setattr on a module) is
    not traced; control reaching a write or an origin through anything but the enclosing
    if, while and conditional tests and short-circuits is not read (an early return or
    raise, a for loop, a try, a with, a match, an assert, a def whose call sites gate
    it); a name bound inside a def is not expanded (its loads are reported as the bare
    name); the suite code the child calls is not read (the reads stop at the name suite).
    Two probes pin boundaries at their empty diff: a module attribute store (moduleattr,
    which feeds no allowlisted field) and a for gate on member a's arrival in the
    post-main reads (forloop, which the "failed-stalled" value pin catches, arrival being
    absent there).
    THE RESIDUAL, a disclosure and not a guarantee, stated as the literal complement of
    what the two nets catch, each by its own mechanics. The value pin catches a producer
    only by a compared field differing from its want (a missing report included) in a run
    the scenarios make within their bounds (the deterministic "failed-stalled" included).
    This oracle catches a producer only by a refusal (an "unrecognized" entry) or by a
    change to a pinned signature: a traced read that adds a name to it, or a name leaving
    it. So ANY producer that neither net catches passes BOTH nets, whatever the condition
    under which it changes evidence, lock_free or restored: what a slow positive control
    alters (member a's arrival or raise, the send list, member b's presence or absence
    (b_arrived, b_running, late), the exception outcome's text (ValueError in the stalled
    run, TimeoutExpired in a slow one)), the scenario's name, a stall's length, the Python
    version, wall clock or environment, timing of any other kind, or anything else; and
    whatever carries it: a form this oracle neither traces nor refuses (a for, try or
    assert gate, an early return, a module attribute, a form not listed here), or a form it
    traces whose reads are all names already in the pinned signature. Two EXAMPLES of the
    latter, not the class: pinned_version (if sys.version_info >= (3, 99):
    branch_swept[:] = [] before the evidence read, a traced if whose test loads only sys,
    already pinned, under a condition no run on a Python before 3.99 reproduces) and a
    pure-local gate (a traced test loading only pinned names, such as if
    len(branch_swept) > 1:, wherever no run within the bounds makes it hold). The value pin
    catches a producer keyed on one of the facts named above only where some run within its
    bounds reproduces that fact (failed-stalled for arrival, raise and the send list, on
    the code that run executes, where forloop shows the catch, so it is outside THE
    RESIDUAL; failed-handshake for a handshake expiry; failed-refused for a stall's
    presence); the stalled run never executes member b's path or run_member (its one member
    raises inside observed first; its pinned late=[] shows it), and differs from a slow
    "failed" run in the other facts named above. The residual probes named here are
    EXAMPLES, not the class: round 20's R1 (a for gate on member a's raise after
    run_member's late append, raisegate), R2 (the same gate spelled try/assert, trygate)
    and R4 (a for gate on the send list there, sendgate), and its gate on the scenario's
    name (scenariogate: a for gate on scenario == 'failed-stalled' or member a's arrival
    before the evidence read); round 21's gate on member b's absence (bgate: a for gate on
    member a's arrival and b_arrived before the evidence read) and on the outcome text
    (outcomegate: a for gate on outcome.startswith('TimeoutExpired') and member a's arrival
    before the evidence read); round 22's gate on the outcome text alone (outcomeonlygate:
    a for gate on outcome.startswith('TimeoutExpired') before the evidence read, tied to no
    arrival, raise or send list); round 24's gate on a stall's length (stallgate: a for
    gate on max(stalls) > 0.5 before the evidence read, stalls holding how long
    "failed-refused" took to release its lock holders, a length no scenario arranges and a
    slow positive control, which leaves stalls empty, does not produce); round 25's
    pinned_version, above. The nine residual probes are recorded in "producer-oracle" at
    their empty diff, so an oracle or scenario change that starts catching one fails that
    pin and must update this text. Only a run in which the residual producer's condition
    holds where the producer runs can catch it (a slow positive control is one such run,
    for a condition it alters: a "failed" run whose member-a positive control reaches its
    60 s bound can leave the field wrong beside the recorded timeout and, the allowlist
    staying compared, report a definite mismatch); such a catch is intermittent, depends on
    the host or the condition, and is never a pin."""
    source = _CLOSE_PROBE_CHILD if source is None else source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise LookupError("the allowlist producer oracle cannot parse the child: {}".format(exc))
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    imports, declared = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.add((alias.asname or alias.name).partition(".")[0])
        elif isinstance(node, ast.Global):
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, defs):
                owner = parents.get(owner)
            if owner is not None:
                declared.setdefault(owner, set()).update(node.names)

    def module_scope(node):
        ancestor = parents.get(node)
        while ancestor is not None:
            if isinstance(ancestor, defs):
                return False
            ancestor = parents.get(ancestor)
        return True

    def global_store(name, node):
        # A store that binds the module-level name: at module scope, or under a global
        # declaration of that name in the enclosing def (its VALUE reads are traced; the
        # call sites gating such a def are a disclosed control boundary).
        ancestor = parents.get(node)
        while ancestor is not None:
            if isinstance(ancestor, defs):
                return isinstance(ancestor, defs[:2]) and name in declared.get(ancestor, ())
            ancestor = parents.get(ancestor)
        return True

    def loads(node):
        return {name.id for name in ast.walk(node)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)}

    def tests(node):
        found, below, ancestor = set(), node, parents.get(node)
        while ancestor is not None and not isinstance(ancestor, defs):
            if isinstance(ancestor, (ast.If, ast.While, ast.IfExp)) and below is not ancestor.test:
                found |= loads(ancestor.test)
            elif isinstance(ancestor, ast.BoolOp) and below in ancestor.values:
                for prior in ancestor.values[:ancestor.values.index(below)]:
                    found |= loads(prior)
            below, ancestor = ancestor, parents.get(ancestor)
        return found

    def free(function):
        bound = {arg.arg for arg in ast.walk(function.args) if isinstance(arg, ast.arg)}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                bound.add(node.id)
            elif isinstance(node, defs[:2]) and node is not function:
                bound.add(node.name)
        return {name for statement in function.body for name in loads(statement)} - bound

    def paired(target, value):
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                return [pair for part, piece in zip(target.elts, value.elts)
                        for pair in paired(part, piece)]
            return [pair for part in target.elts for pair in paired(part, value)]
        if isinstance(target, ast.Starred):
            return paired(target.value, value)
        return [(target, value)]

    # Beside each origin, "sanctioned" collects the exact Name nodes the recognised forms
    # account for; the carrier sweep below refuses every other occurrence of a carrier.
    origins, sanctioned = {}, set()
    unrecognized, bindings = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _REFLECTIVE_NAMES:
            unrecognized.append((node.lineno, node.id + " reference"))
        elif isinstance(node, ast.Name) and node.id in ("getattr", "setattr", "delattr"):
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node and parent.args
                    and isinstance(parent.args[0], ast.Name) and parent.args[0].id in imports):
                unrecognized.append((node.lineno, node.id + " reference"))
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and node.value is not None:
            for target in getattr(node, "targets", None) or [node.target]:
                for part, value in paired(target, node.value):
                    if isinstance(part, ast.Name) and global_store(part.id, node):
                        origins.setdefault(part.id, []).append((value, node))
                        sanctioned.add(id(part))
                    elif isinstance(part, ast.Subscript) and isinstance(part.value, ast.Name):
                        origins.setdefault(part.value.id, []).extend(
                            [(value, node), (part.slice, node)])
                        sanctioned.add(id(part.value))
        elif (isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name)
                and global_store(node.target.id, node)):
            origins.setdefault(node.target.id, []).append((node.value, node))
            sanctioned.add(id(node.target))
        elif isinstance(node, defs[:2]) and module_scope(node):
            origins.setdefault(node.name, []).append((node, node))
        elif isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.attr in _PRODUCER_MUTATORS):
                origins.setdefault(node.func.value.id, []).extend(
                    (value, node) for value in node.args + [key.value for key in node.keywords])
                sanctioned.add(id(node.func.value))
            receiver = node.func
            while isinstance(receiver, (ast.Attribute, ast.Subscript)):
                receiver = receiver.value
            if not (isinstance(receiver, ast.Name) and receiver.id == "extra"):
                # A bare name passed to any call (the extras writers aside, which the scan
                # below traces) may be bound or mutated by the callee: the whole call is
                # that name's origin. An import-bound module passed on is the disclosed
                # module-attribute boundary instead.
                for value in node.args + [key.value for key in node.keywords]:
                    part = value.value if isinstance(value, ast.Starred) else value
                    if isinstance(part, ast.Name) and part.id not in imports:
                        origins.setdefault(part.id, []).append((node, node))
                        sanctioned.add(id(part))

    writers = {key: [] for key in _STATE_EVIDENCE}
    for name in ast.walk(tree):
        if not (isinstance(name, ast.Name) and name.id == "extra"):
            continue
        parent, form = parents.get(name), None
        grand = parents.get(parent)
        if isinstance(name.ctx, ast.Store):
            statement = grand if isinstance(parent, (ast.Tuple, ast.List)) else parent
            values = [value for target in getattr(statement, "targets", ())
                      for part, value in paired(target, statement.value) if part is name]
            if (values and not bindings and module_scope(statement)
                    and isinstance(values[0], ast.Dict) and not values[0].keys):
                bindings.append(name.lineno)
            else:
                form = "rebinding"
        elif isinstance(parent, ast.Subscript) and parent.value is name:
            key = parent.slice.value if isinstance(parent.slice, ast.Constant) else None
            if not isinstance(key, str):
                form = "dynamic key"
            elif key not in _STATE_EVIDENCE:
                pass
            elif isinstance(parent.ctx, ast.Store):
                statement = (parents.get(grand) if isinstance(grand, (ast.Tuple, ast.List))
                             else grand)
                values = [value for target in getattr(statement, "targets", ())
                          for part, value in paired(target, statement.value) if part is parent]
                if values:
                    writers[key].extend(values)
                    sanctioned.update(id(v) for v in values if isinstance(v, ast.Name))
                else:
                    form = key + " store"
            elif (isinstance(parent.ctx, ast.Load) and isinstance(grand, ast.Attribute)
                    and grand.attr in _PRODUCER_MUTATORS
                    and isinstance(parents.get(grand), ast.Call)
                    and parents[grand].func is grand):
                values = (parents[grand].args
                          + [keyword.value for keyword in parents[grand].keywords])
                writers[key].extend(values)
                sanctioned.update(id(v) for v in values if isinstance(v, ast.Name))
            else:
                form = key + " reference"
        elif isinstance(parent, ast.Attribute) and parent.value is name:
            call = grand if isinstance(grand, ast.Call) and grand.func is parent else None
            if call is None or parent.attr not in ("setdefault", "pop", "get", "update"):
                form = "." + parent.attr
            elif parent.attr == "update":
                if call.args or any(keyword.arg is None for keyword in call.keywords):
                    form = ".update arguments"
                for keyword in call.keywords:
                    if keyword.arg in _STATE_EVIDENCE:
                        writers[keyword.arg].append(keyword.value)
                        if isinstance(keyword.value, ast.Name):
                            sanctioned.add(id(keyword.value))
            elif not (call.args and isinstance(call.args[0], ast.Constant)
                      and isinstance(call.args[0].value, str)
                      and call.args[0].value not in _STATE_EVIDENCE):
                form = "." + parent.attr + " key"
        elif not (isinstance(parent, ast.List) and isinstance(grand, ast.Call)
                  and isinstance(grand.func, ast.Attribute) and grand.func.attr == "dumps"
                  and isinstance(grand.func.value, ast.Name) and grand.func.value.id == "json"):
            form = "bare"
        if form is not None:
            unrecognized.append((name.lineno, form))

    def signature(values):
        seen, pending = set(), set()
        for value in values:
            pending |= loads(value) | tests(value)
        while pending:
            read = pending.pop()
            if read in seen:
                continue
            seen.add(read)
            for value, site in origins.get(read, ()):
                pending |= (free(value) if isinstance(value, defs[:2])
                            else loads(value) | tests(site))
        return sorted(seen)

    found = {key: signature(values) for key, values in writers.items()}
    # THE CARRIER SWEEP, closed world: a carrier is a signature name whose origins show a
    # mutable literal (so its OBJECT can carry state into a producer; extra has its own
    # scan above, and an import-bound module is the disclosed boundary). Every occurrence
    # of a carrier the recognised forms did not account for is refused, so a deletion, a
    # clear, a pop, an alias or any new spelling of a touch fails the pin instead of
    # passing silently.
    mutable = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.SetComp, ast.DictComp)
    closure = {name for key in _STATE_EVIDENCE for name in found[key]}
    carriers = {name for name in closure
                if name != "extra" and name not in imports
                and any(isinstance(value, mutable) for value, _ in origins.get(name, ()))}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in carriers
                and id(node) not in sanctioned):
            unrecognized.append((node.lineno, node.id + " escape"))
    found["unrecognized"] = sorted(unrecognized)
    return found


def _slow_control_record(source=None):
    """The "failed-stalled" slow-control record, read from the _CLOSE_PROBE_CHILD source
    (source, default _CLOSE_PROBE_CHILD) and never run: the sorted names of the facts that do
    NOT hold, so the committed child gives []. The record is what keeps a stalled run on a
    merely slow host cannot-evaluate: its positive control reaching the 60 s bound ends main()
    in TimeoutExpired, the deliberate empty send list records no budget, and without the
    reached bound "positive control a observed" in the "missing" extras nothing would stand
    the run down, so its outcome would be a definite mismatch. No healthy run reaches the
    record, so this reads it instead. A BINDING of a name below is every node of the child
    that binds it: a store or del, an import (plain, dotted, which binds its first name,
    or with as), a def or class name, an except-as name, a match capture and a parameter;
    a wildcard import is NOT one (its names are not in the source; NOT READ, below).
    "record": exactly one statement of the child is extra.setdefault('missing',
    []).append('positive control a observed'). "gate": its
    enclosing if, with no else, tests exactly scenario == 'failed-stalled' and
    outcome.startswith('TimeoutExpired') and holds that statement alone, inside the body of
    the module-level if scenario.startswith('failed'), and scenario has exactly one
    binding, the statement scenario, folder, fixture = sys.argv[2], sys.argv[3],
    sys.argv[4]. "kept": every use of the key 'missing' in the child (the constant
    'missing' anywhere, a keyword missing= on any call) is either the call
    extra.setdefault('missing', []) inside, through conditional expressions only, the
    receiver of an append call, or sits in the body of the module-level if scenario ==
    'lock', which no failed scenario enters (scenario's one binding is part of "gate"), so
    no statement of the child, after the gate or in a def called after it, pops, deletes,
    clears or rebinds the record THROUGH THE KEY; removing it without the key (clear,
    popitem, a rebinding of extra, a dynamic key, __delitem__) is refused by "producer-map"
    (_allowlist_producers, the extras dict's fail-closed rules), not here, and reaching the
    dict or the record's list without the name extra (a module attribute such as
    sys.modules, gc) is read by neither. "outcome": the child has exactly one module-level
    try calling main(), every binding of outcome is inside it, and that try statement is
    exactly the committed one: its body, its handler sequence (KeyboardInterrupt,
    SystemExit, then Exception, each with its committed name and its one committed
    statement, the last outcome = '{}: {}'.format(type(exc).__name__, exc)), no else and
    no finally. So a TimeoutExpired that reaches this try out of main() is caught by no
    earlier handler and no nested try, and yields the prefix the gate tests unless an edit
    in the NOT READ class below changes what the try or the gate evaluates. NOT READ, each
    named route an EXAMPLE of that class, a route that can keep the record out of the
    report while this reader still gives []: whether a TimeoutExpired reaches this try at
    all (the suite code main() runs, and the child's replacements of suite attributes on
    that path, such as the sweep wrapper, can swallow or replace it); a binding made by a
    wildcard import (from <module> import *), which can bind outcome or scenario unseen
    (sys.outcome = ..., then from sys import *); a rebinding, by ANY binding form (a
    wildcard import included) or by a module-attribute store (through the builtins module
    or any other), of ANY name the pinned try loads (contextlib and redirect_stdout,
    captured, suite, lanes, type, and the handlers' exception names KeyboardInterrupt,
    SystemExit and Exception), the try itself still parsing to the committed one
    (KeyboardInterrupt = TimeoutExpired makes the first handler catch the timeout; a
    contextlib whose redirect_stdout raises on exit replaces it inside the try); a store
    into the child's own globals through a module attribute (__main__.outcome,
    __main__.scenario, sys.modules['__main__'].outcome), which binds no name, so neither
    "gate" nor "outcome" sees it; a store into an attribute of an object the try or the
    gate evaluates (the exception class's __name__ or __str__:
    suite.subprocess.TimeoutExpired.__name__ = 'Slow' turns the handler's outcome into
    'Slow: ...'), which binds no name either; a write to the child's namespace through a
    function's __globals__ or a frame's globals (handle.__globals__['outcome'] = ...,
    sys._getframe().f_globals['outcome'] = ...), which binds no name either; an exit or
    raise between the gate and the report (an assert, raise, sys.exit or os._exit after the
    record, in the child or a def it calls), so no PROBE-REPORT line is printed and the run
    reads as a missing report, not a timeout; and any change to how the report is written
    (a rebinding or replacement of print, sys.stdout or json.dumps). THE NOT READ CLASS,
    exact by construction: this reader does not read ANY edit, by any route, that changes
    what the pinned try or the gate evaluates, or whether the record reaches the printed
    report, without a change it parses as a fact that does not hold (it then still gives
    []); every route named above is an EXAMPLE of that class, not its bound. The probes
    exceptbind and ctxclass (the rebinding route), wildcard and mainattr (the wildcard and
    own-globals routes), exitafter, clsname (the attribute-store route) and funcglobals
    (the __globals__ route) each hold ONE instance AT [] (disclosed, not read); the class,
    not those instances, is what this reader does not read.
    Raises LookupError, which the caller records as cannot-evaluate, when the source does
    not parse."""
    source = _CLOSE_PROBE_CHILD if source is None else source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise LookupError("the slow-control reader cannot parse the child: {}".format(exc))
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def shape(text):
        return ast.dump(ast.parse(text).body[0])

    def binds(name):
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                bound = None if isinstance(node.ctx, ast.Load) else node.id
            elif isinstance(node, ast.alias):
                bound = (node.asname or node.name).partition(".")[0]
            elif isinstance(node, ast.MatchMapping):
                bound = node.rest
            elif isinstance(node, ast.arg):
                bound = node.arg
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                   ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                bound = node.name
            else:
                continue
            if bound == name:
                found.append(node)
        return found

    def statement(node):
        while node is not None and not isinstance(node, ast.stmt):
            node = parents.get(node)
        return node

    def appended(use):
        # The key as extra.setdefault('missing', []) inside, through conditional
        # expressions only, the receiver of an append call: a use that only adds.
        call = parents.get(use)
        if not (isinstance(call, ast.Call) and ast.dump(call) == keyed):
            return False
        receiver = parents.get(call)
        while isinstance(receiver, ast.IfExp):
            receiver = parents.get(receiver)
        caller = parents.get(receiver)
        return (isinstance(receiver, ast.Attribute) and receiver.attr == "append"
                and isinstance(caller, ast.Call) and caller.func is receiver)

    record = shape("extra.setdefault('missing', []).append('positive control a observed')")
    gate = shape("if scenario == 'failed-stalled' and outcome.startswith('TimeoutExpired'):"
                 "\n    extra.setdefault('missing', []).append('positive control a observed')")
    failed = ast.dump(ast.parse("scenario.startswith('failed')", mode="eval").body)
    bound = shape("scenario, folder, fixture = sys.argv[2], sys.argv[3], sys.argv[4]")
    lock = ast.dump(ast.parse("scenario == 'lock'", mode="eval").body)
    keyed = ast.dump(ast.parse("extra.setdefault('missing', [])", mode="eval").body)
    run = shape("try:\n    with contextlib.redirect_stdout(captured):\n"
                "        outcome = 'returned {}'.format(suite.main(None, lanes))\n"
                "except KeyboardInterrupt:\n    outcome = 'KeyboardInterrupt'\n"
                "except SystemExit as exc:\n    outcome = 'SystemExit {}'.format(exc.code)\n"
                "except Exception as exc:\n"
                "    outcome = '{}: {}'.format(type(exc).__name__, exc)")
    missing = []
    records = [node for node in ast.walk(tree)
               if isinstance(node, ast.stmt) and ast.dump(node) == record]
    if len(records) != 1:
        missing.append("record")
    branch = parents.get(records[0]) if records else None
    outer = parents.get(branch)
    scenarios = binds("scenario")
    if not (branch is not None and ast.dump(branch) == gate
            and isinstance(outer, ast.If) and outer in tree.body
            and ast.dump(outer.test) == failed and branch in outer.body
            and len(scenarios) == 1 and ast.dump(statement(scenarios[0])) == bound):
        missing.append("gate")
    locked = {node for test in tree.body
              if isinstance(test, ast.If) and ast.dump(test.test) == lock
              for held in test.body for node in ast.walk(held)}
    uses = [node for node in ast.walk(tree)
            if (isinstance(node, ast.Constant) and node.value == "missing")
            or (isinstance(node, ast.keyword) and node.arg == "missing")]
    if not all(appended(use) or use in locked for use in uses):
        missing.append("kept")
    runs = [node for node in tree.body if isinstance(node, ast.Try)
            and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "main" for call in ast.walk(node))]
    if not (len(runs) == 1
            and all(any(node is part for part in ast.walk(runs[0]))
                    for node in binds("outcome"))
            and ast.dump(runs[0]) == run):
        missing.append("outcome")
    return sorted(missing)


def _oracle_down_sites(got, oracle_down):
    """config/member-close-unresolved-verdict's stand-down when the close-lock-site oracle raised
    LookupError (oracle_down, its recorded timeout message): ONLY the site= entries, the one
    field the oracle informs, are stood down. The "close-sites" entry becomes the timeout message
    (attributed, cannot-evaluate). Each refused-site scenario's observation keeps every definite
    field, and its recorded site entry, which cannot be checked without the oracle, is replaced
    by the timeout message on the observed side and expected verbatim on the wants side, so the
    one comparison the oracle informs is neutral while every other mismatch stays definite; a
    scenario that never recorded a site entry (the refusal never reached its target acquire)
    keeps that absence, a definite mismatch the child reports without the oracle. Returns the
    site= expectations for ("close-wait-refused", "close-left-refused"). The synthetic
    "down-map" entry pins this mapping on a probe got whatever the real oracle's health."""
    got["close-sites"] = oracle_down
    for name in ("close-wait-refused", "close-left-refused"):
        entry = got.get(name)
        if isinstance(entry, tuple) and len(entry) == 7 and "site" in entry[6]:
            entry[6]["site"] = oracle_down
    return [oracle_down, oracle_down]


def _closing_refusal_control(fixture, spawn):
    """The config/member-closing-refuses-start control: _run_bounded with closing set and Popen
    replaced by spawn (the caller's stand-in, which may record any start); returns _run_bounded's
    code, the launch-error message when nothing started. It sets ONLY closing and clears only closing, through
    _reopen_members, never by restoring the whole registry: a SIGINT or SIGTERM received inside the
    control is deferred (held, as everywhere closing is set) and raised right after it, never
    erased. Closing already set on entry (never the case inside a run) is left set. The probe
    scenario "closing-control" pins it with a real SIGINT inside the control, and the "split-..."
    scenarios a SIGTERM held here outranking a SIGINT held during main()'s later closes."""
    import io
    from unittest.mock import patch

    reopen = not _MEMBERS["closing"]
    _MEMBERS["closing"] = True
    try:
        with patch.object(subprocess, "Popen", spawn):
            return _run_bounded([sys.executable, "-I", "-B", "-c", "pass"], str(fixture),
                                dict(os.environ), 60, "closing control", "CONTROL ", [],
                                io.StringIO())[0]
    finally:
        if reopen:
            _reopen_members()


# The _CLOSE_SWEEP_CHILD report config/member-close-signal-sweep requires: each body's clean
# baseline (outcome, expected outcome, both closes clean, no group left, restores SIGTERM then
# SIGINT), every mode with runs and none failing, and every window function reached.
_SWEEP_MODES = tuple(body + mode + " " + name for body in ("", "branch ")
                     for mode in ("line", "instruction", "pair") for name in ("SIGINT", "SIGTERM"))
_SWEEP_REACHED = ("__enter__", "__exit__", "_close_members", "_config_results", "_finish_members",
                  "_member_group_empty", "_release_member_group", "_restore_signal_handlers",
                  "_signal_member_groups", "body", "branch", "main")
_SWEEP_WANT = (
    [[True, [outcome, outcome, [[], []], 0, [int(signal.SIGTERM), int(signal.SIGINT)]]]
     for outcome in ("returned 0", "ValueError: injected failed member")],
    [[mode, True, []] for mode in _SWEEP_MODES], [])


def _sweep_observed(rc, out, err):
    """config/member-close-signal-sweep's observation of one _CLOSE_SWEEP_CHILD run, and the parsed
    report (None when not read). The child must have EXITED 0 before its report is read at all: a
    report followed by any other exit is a failure, returned with the exit code and stderr; a
    timeout is returned as is (cannot evaluate). The observation is the two baselines, per mode
    whether it made runs and its failing runs, and the window functions it never reached."""
    if _is_timeout(rc):
        return rc, None
    if rc != 0:
        return "sweep child exited {}: err={!r}".format(rc, err[-400:]), None
    try:
        sweep = json.loads(out.rpartition("SWEEP-REPORT ")[2].partition("\n")[0])
        return ([sweep["baseline"], sweep["branch baseline"]],
                [[mode, sweep[mode][0] > 0, sweep[mode][1]] for mode in _SWEEP_MODES],
                sorted(set(_SWEEP_REACHED) - set(sweep["names"]))), sweep
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        return "no report ({}): rc={} err={!r}".format(exc, rc, err[-400:]), None


def _member_timeout_timely(elapsed, budget, recorded=None):
    """True when elapsed stayed under the budget, else the budget-timeout message, recorded
    (default CANNOT_EVALUATE: fail closed, never a pass, never definite) and used as the
    stood-down value on BOTH sides of the comparison, because timing alone is never a
    definite finding. config/member-timeout-cannot-evaluate pins this producer on a synthetic
    over-budget value against a private record, so a mutant that stops recording or stops
    reporting the reached budget fails that check definitively whatever the real control's
    own timing."""
    if elapsed < budget:
        return True
    message = ("TIMEOUT: member-timeout control: finished after {:.1f} s, past its {} s "
               "budget (cannot evaluate)".format(elapsed, budget))
    (CANNOT_EVALUATE if recorded is None else recorded).append(message)
    return message


def _verdict(failures, cannot_evaluate, caused):
    """The suite exit code. A finding is attributed to a timeout when caused maps it to timeout
    messages that were all recorded in cannot_evaluate (check() derives that from a result that is
    a timeout message naming the member, _registered_offenders from a lane whose only offenders
    timed out clean); every other finding is definite. 1 when any definite finding exists (a
    definite finding outranks cannot-evaluate; the timeouts are listed beside it); 2 when a time
    bound was reached and every finding is attributed to one; 0 when clean (main prints the PASS
    line)."""
    recorded = set(cannot_evaluate)
    attributed = [finding for finding in failures
                  if caused.get(finding) and set(caused[finding]) <= recorded]
    definite = [finding for finding in failures if finding not in attributed]
    note = "a member reached its time bound, so this run is no verdict on that member"
    if definite:
        print("SELF-TEST FAIL:")
        for failure in definite:
            print("  - " + failure)
    if cannot_evaluate:
        print(("  also cannot evaluate ({}):" if definite else
               "SELF-TEST CANNOT EVALUATE: {}:").format(note))
        for entry in cannot_evaluate:
            print("  - " + entry)
        if attributed:
            print("  findings attributed to those timeouts:")
            for finding in attributed:
                print("  - " + finding)
    if definite:
        return 1
    return 2 if cannot_evaluate else 0


# These registered self-tests exercise data/text/filesystem fixtures, not a git
# lifecycle. They still require a successful observed system-config-lane run.
# New modules and removed wrappers are NOT implicitly exempt.
OPF_LIFECYCLE_EXEMPTIONS = {
    "opf/tools/_child_status.py": ("Child-status probe vectors over forked and trampolined interpreter and bash "
                                   "children; no git."),
    "opf/tools/_journal.py": "Descriptor-helper vectors over in-process pipes with patched os primitives.",
    "opf/tools/_opf_adopt.py": ("Adoption vocabulary and validator vectors; its observe vector suite runs "
                                "git only inside _opf_adopt_observe._git_archive_fixture's lifecycle."),
    "opf/tools/_opf_adopt_apply.py": "Apply-shell refusal, evidence and journal vectors over temporary filesystem fixtures.",
    "opf/tools/_opf_adopt_discover.py": "Read-only source-discovery vectors over temporary filesystem fixtures.",
    "opf/tools/_opf_adopt_hook.py": "Pure enable-hook merge vectors over synthetic registration bytes.",
    "opf/tools/_opf_adopt_state.py": ("Containment vectors over temporary product trees in a replaced "
                                       "environment (private empty HOME, no system git config, C locale, "
                                       "umask 022); its fixtures commit through plumbing only "
                                       "(_opf_adopt_apply._selftest_git_commit and the S16 add, "
                                       "write-tree, commit-tree, update-ref helper), never a hook."),
    "opf/tools/_opf_adopt_complete.py": "Completion-check vectors over temporary post-apply filesystem fixtures.",
    "opf/tools/_opf_import_batch.py": "Pure batch parsing, canonical emission and digest vectors over in-memory bytes.",
    "opf/tools/_opf_import_complete.py": "Import-completion vectors over temporary post-import filesystem fixtures.",
    "opf/tools/_opf_init.py": "Canonical model bytes, defaults and validator vectors.",
    "opf/tools/_opf_init_contract.py": "KEEP contract validation over synthetic models.",
    "opf/tools/_opf_pack_manifest.py": "Pack parsing and digest vectors over filesystem fixtures.",
    "opf/tools/_opf_release_history.py": "Pure history parsing, canonical emission and digest vectors over in-memory bytes.",
    "opf/tools/check_opf_homes.py": "Homes contract and schema boundary vectors.",
    "opf/tools/check_opf_init_contract.py": "Source-free contract matcher vectors.",
    "opf/tools/check_opf_init_observe.py": "Observation vectors with mocked git subprocesses.",
    "opf/tools/check_opf_init_p0.py": "P0 store validation and runner registration vectors.",
    "opf/tools/check_opf_prompt_pack.py": "Prompt-pack schema and drift vectors over filesystem fixtures.",
    "opf/tools/selftest_commonmark_conformance.py": "CommonMark parser conformance vectors.",
    "opf/tools/selftest_commonmark_headings.py": "Heading selection and vendor-manifest fixtures.",
}


def _opf_lifecycle_delegates(trees):
    """Derive wrapper/delegate edges from registered modules, never helper names.
    Recognize HOME-setting patch.dict and fixture_git_lifecycle contexts, including
    a shared wrapper that calls its callback argument. Reject references to a
    discovered delegate outside those edges, naming the bypassing function.
    Residual: dynamic lookup, import aliases and unregistered modules are outside
    this syntactic graph; runtime environment and config probes remain necessary.
    """
    functions = {(module, node.name): node for module, tree in trees.items()
                 for node in tree.body if isinstance(node, ast.FunctionDef)}

    def target(module, node):
        if isinstance(node, ast.Name):
            key = (module, node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            key = (node.value.id, node.attr)
        else:
            return None
        return key if key in functions else None

    def body_nodes(node):
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                yield from body_nodes(child)

    delegates, callbacks, allowed = {}, {}, set()

    def register(module, entry, callee, reference):
        key = (module, entry)
        if key in delegates and delegates[key] != callee:
            raise ValueError("ambiguous lifecycle delegate: " + module + "." + entry)
        delegates[key] = callee
        allowed.add(reference)

    for (module, entry), fn in functions.items():
        for node in body_nodes(fn):
            if not isinstance(node, ast.With):
                continue
            contexts = [item.context_expr for item in node.items]
            if not any(isinstance(ctx, ast.Call) and (
                    ast.unparse(ctx.func).split(".")[-1] == "fixture_git_lifecycle"
                    or (ast.unparse(ctx.func) == "patch.dict" and ctx.args
                        and ast.unparse(ctx.args[0]) == "os.environ"
                        and any(kw.arg == "HOME" for kw in ctx.keywords)))
                       for ctx in contexts):
                continue
            for ret in body_nodes(node):
                if not isinstance(ret, ast.Return) or not isinstance(ret.value, ast.Call):
                    continue
                call = ret.value
                callee = target(module, call.func)
                if callee is not None and callee[0] == module:
                    register(module, entry, callee, call.func)
                elif isinstance(call.func, ast.Name):
                    params = [p.arg for p in fn.args.posonlyargs + fn.args.args]
                    if call.func.id in params:
                        callbacks[module, entry] = params.index(call.func.id)

    # Follow callback registrations using the helpers derived above, including
    # cross-module OPF wrappers. No helper spelling is part of the inventory.
    for (module, entry), fn in functions.items():
        for node in body_nodes(fn):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            helper = target(module, call.func)
            if helper not in callbacks:
                continue
            index = callbacks[helper]
            if index >= len(call.args):
                raise ValueError("unsupported lifecycle callback: " + module + "." + entry)
            callee = target(module, call.args[index])
            if callee is None or callee[0] != module:
                raise ValueError("unresolved lifecycle callback: " + module + "." + entry)
            register(module, entry, callee, call.args[index])

    protected = set(delegates.values())
    for module, tree in trees.items():
        for owner in tree.body:
            entry = owner.name if isinstance(owner, ast.FunctionDef) else "<module>"
            for node in ast.walk(owner):
                if target(module, node) in protected and node not in allowed:
                    raise ValueError("lifecycle bypass: {}.{} -> {}".format(
                        module, entry, ".".join(target(module, node))))
    return delegates


# The retired ingest execution coordinator was the one registered module with a two-level wrapper
# route (self_test -> self_test_isolated, and _self_test_main -> _self_test_main_isolated, which calls
# self_test). This synthetic mirror of that shape keeps the graph helper's rename and bypass
# discriminators. It is parsed only, never executed.
_LIFECYCLE_GRAPH_FIXTURE = """\
import os
import tempfile
from unittest.mock import patch


def self_test(only=None):
    with tempfile.TemporaryDirectory(prefix="probe-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return self_test_isolated(only)


def self_test_isolated(only=None):
    return 0


def _red_on_revert_main():
    return 0


def _self_test_main(args):
    with tempfile.TemporaryDirectory(prefix="probe-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _self_test_main_isolated(args)


def _self_test_main_isolated(args):
    rc = self_test()
    return rc if rc != 0 or "--red-on-revert" not in args else _red_on_revert_main()
"""


def _lifecycle_finding(roster, config_results, definite, problems, recorded=None, probes=()):
    """(got, cause) for env/registered-opf-lifecycles. definite holds the paths missing for a
    reason other than their config run; each roster argv whose config run was not clean adds its
    path to it unless that run only timed out (_split_offenders). probes holds the recorded
    timeout messages of the lifecycle probes that reached their bound, kept out of problems
    (_lifecycle_problems in tools/selftest_git_fixture_env_config_system.py). With any definite
    path or problem, got lists ONLY the definite paths and cause is (): a member whose run only
    timed out, or a probe that reached its bound, is reported by its own timeout message on the
    cannot-evaluate list, never in this FAIL. Otherwise got lists the timed-out paths and the
    probes' timeout messages, and cause every one of those timeout messages."""
    offenders, timed_out, causes = _split_offenders(
        {argv: config_results.get(argv) for argv in roster}, recorded)
    definite = set(definite) | {_command_identity(argv)[0] for argv in offenders}
    if definite or problems:
        return (sorted(definite), problems), ()
    return ((sorted({_command_identity(argv)[0] for argv in timed_out}), problems + list(probes)),
            causes + tuple(probes))


def _build_decoy(base):
    """A committed decoy caller repository whose .git the poisoned lanes aim at; None on a setup
    failure (a harness error, exit 2, never a silent skip)."""
    repo = base / "caller"
    env = _git_fixture_env.git_fixture_env()
    try:
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True, timeout=60, env=env)
        (repo / "committed.txt").write_text("caller line\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "committed.txt"],
                       check=True, capture_output=True, timeout=60, env=env)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Selftest",
             "-c", "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "seed"],
            check=True, capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot build the decoy caller repository: {}"
              .format(exc), file=sys.stderr)
        return None
    return repo


def _snapshot_git_dir(repo):
    """Byte snapshot of the decoy's .git: relative path -> sha256 of content, over every regular
    file, so ANY write into the caller repository (index, refs, objects, logs, config) surfaces as
    an inequality rather than being sampled around."""
    gitdir = repo / ".git"
    result = {}
    for path in sorted(gitdir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            result[str(path.relative_to(gitdir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _run_corpus(poison_key, poison_value, cwd):
    """Run the corpus self-test isolated with exactly ONE repository-selecting poison on top of an
    otherwise scrubbed environment, so the lane measures that poison alone. Returns the child's
    return code, a loud string on a launch failure, or the timeout message _run_bounded recorded
    as cannot-evaluate."""
    env = _git_fixture_env.git_fixture_env()
    env[poison_key] = poison_value
    rc, _, _ = _run_bounded([sys.executable, "-I", "-B", str(CORPUS_SELFTEST)], str(cwd), env,
                            CORPUS_BOUND, "corpus self-test under " + poison_key, "CORPUS ")
    return rc if _is_timeout(rc) or not isinstance(rc, str) else "corpus launch failed: " + rc


def _run_corpus_imported(poison_key, poison_value, cwd):
    """Run the corpus suite through its IMPORTED surface (run_self_test(), which bypasses
    __main__ and its entry-point scrub) isolated with exactly ONE repository-selecting poison,
    so the fixture-lifecycle scrub in GitTests.setUp must hold on its own. Returns the child's
    return code, a loud string on a launch failure, or the timeout message _run_bounded recorded
    as cannot-evaluate."""
    env = _git_fixture_env.git_fixture_env()
    env[poison_key] = poison_value
    code = "\n".join((
        "import sys",
        "sys.path.insert(0, sys.argv[1])",
        "import os, selftest_aiqt_corpus",
        "saved = dict(os.environ)",
        "rc = selftest_aiqt_corpus.run_self_test()",
        "sys.exit(rc if dict(os.environ) == saved else 1)",
    ))
    rc, _, _ = _run_bounded([sys.executable, "-I", "-B", "-c", code, str(ROOT / "tools")],
                            str(cwd), env, CORPUS_BOUND,
                            "imported corpus self-test under " + poison_key, "CORPUS ")
    return (rc if _is_timeout(rc) or not isinstance(rc, str)
            else "imported corpus launch failed: " + rc)


def _auto_maintenance_children(workdir, env):
    """The argv of every maintenance/gc child a traced fixture commit spawns under env: [] is
    the pinned (F-367) behaviour; a non-empty list is the defective behaviour the red leg
    reproduces (an UNPINNED commit spawns the DETACHED `git maintenance run --auto` child; the
    red leg demonstrates that child LAUNCHING, this single-file seed staying under the
    automatic-work thresholds, and once those thresholds are met the child can repack or prune
    the fixture's .git/objects after the commit returned, racing a later copytree/rmtree/read
    of that repository). A launch failure or an unreadable, empty or malformed trace is a loud
    string, never a silent pass."""
    workdir.mkdir()
    trace = workdir / "trace2-events.jsonl"
    repo = workdir / "repo"
    env = dict(env, GIT_TRACE2_EVENT=str(trace))
    try:
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True, timeout=60, env=env)
        (repo / "seed.txt").write_text("seed line\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "seed.txt"],
                       check=True, capture_output=True, timeout=60, env=env)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Selftest",
             "-c", "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "seed"],
            check=True, capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return "maintenance probe failed: %s" % (exc,)
    return _trace2_maintenance_children(trace)


def _trace2_maintenance_children(trace, bound=TRACE2_TAIL_SECONDS, clock=time.monotonic,
                                 sleep=time.sleep):
    """The argv of every maintenance/gc child_start event in the trace2 event file trace. The
    DETACHED child an unpinned commit launches inherits GIT_TRACE2_EVENT and keeps appending
    events after the commit returned, so a read can land while it is mid-write and see an
    unterminated final event. The file is then read again until it ends at a newline. The bound
    is a polling deadline, bound seconds by clock after the first clock reading: clock is read
    after EVERY read, and a read whose reading is at or past the deadline fails the probe loudly
    whether or not that read ended at a newline (a tail completed only after the bound is a
    failure, never accepted; an event still unterminated there is reported as such), so the
    probe can overrun the bound by one sleep and one read but never accepts a read it cannot
    place before the deadline. Only newline-terminated events are parsed, split at LF alone
    (JSON escapes every LF inside a string, while str.splitlines would also cut an event at a
    raw U+2028, U+2029 or U+0085, which JSON allows unescaped inside a string); a malformed
    event (bad JSON or UTF-8, nesting too deep to parse, not an object, an object without a
    string event (a child_start whose tag was lost would otherwise hide its child), a child_start
    argv that is missing or not a list of strings) and an empty file (no event recorded, so no
    evidence either way) are loud strings.
    The red leg's evidence, the commit's own child_start event, is complete before the commit
    exits; the wait only covers events the detached child is still writing. Residuals: a writer
    that leaves the tail unterminated past the bound (a stalled or killed child) fails the probe
    loudly rather than passing it, and events appended after the final read are not seen."""
    deadline = clock() + bound
    while True:
        try:
            data = trace.read_bytes()
        except OSError as exc:
            return "maintenance probe failed: %s" % (exc,)
        late = clock() >= deadline
        if not data:
            return "empty trace2 event file: no event recorded"
        if data.endswith(b"\n"):
            if late:
                return "trace2 tail completed only after the %s s bound" % (bound,)
            break
        if late:
            return "unterminated trace2 event after %s s: %r" % (
                bound, data[data.rfind(b"\n") + 1:][:80])
        sleep(0.05)
    children = []
    for line in data.split(b"\n")[:-1]:
        try:
            event = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            return "unparseable trace2 event: %s" % (exc,)
        if not isinstance(event, dict):
            return "unparseable trace2 event: not a JSON object: %r" % (line[:80],)
        kind = event.get("event")
        if not isinstance(kind, str):
            return "unparseable trace2 event: event is not a string: %r" % (line[:80],)
        if kind == "child_start":
            argv = event.get("argv")
            if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
                return ("unparseable trace2 event: child_start argv is not a list of strings: %r"
                        % (line[:80],))
            if set(("maintenance", "gc")) & set(argv):
                children.append(argv)
    return children


# ---------- layer 4: the repo-wide maintenance-pin tripwire scan (F-367) ----------
# Round 1 of the F-367 fix showed that a HAND enumeration of fixture harnesses misses members:
# tools/selftest_ci_status.py committed through a hand-rolled environment and an absolute git
# executable, bypassing the shared scrub, the lifecycle PATH wrapper, and every reviewed funnel.
# This scan derives the member set MECHANICALLY from the source instead. It is a TRIPWIRE over
# the RESOLVABLE Python launch forms, not a completeness proof: every process launch under
# tools/ and opf/tools/ that this scan CAN resolve to a maintenance-triggering git run must be
# effectively pinned or covered, and every launch form it CANNOT resolve must be a loud
# cannot-evaluate FINDING, never a silent skip, unless a written justification in
# _SCAN_ALLOWED_UNPINNED covers that launch's KIND (a stale or dangling justification is itself
# a finding). The refused-loudly set: os-level launchers (os.system / os.exec* / os.spawn* /
# os.popen / os.posix_spawn* / any .spawn spelling, pty.spawn included), asyncio's
# create_subprocess_exec/_shell by any object spelling, shell-string launches (shell=True,
# subprocess.getoutput/getstatusoutput), dynamic launcher access (exec or eval anywhere in the
# scanned trees, getattr(subprocess, ...) / getattr(os, ...) / getattr(asyncio, ...) except
# a literal non-launcher attribute, subprocess.__dict__ / os.__dict__ / asyncio.__dict__,
# vars(subprocess) / vars(os) / vars(asyncio)), a launch with
# **-expanded keywords or an executable= override, an argv or argv head the resolver cannot
# reduce to literals (a helper return, another module's value, a parameterized subcommand
# slot), a tracked argv or env NAME whose resolution is invalidated (never trusted): a
# subscript write, an insert/remove/pop/clear/sort/reverse/update/setdefault call, a del,
# or a non-additive augmented assignment in its own scope; a bare-name alias binding of it
# in its own scope; a bare-name REBINDING of it through a walrus target, a for-statement
# target, a `with ... as` target, or an `except ... as` name in its own scope; or ANY
# mutation, growth, aliasing, or global/nonlocal declaration of
# it inside a nested same-module scope (this scan does not order cross-scope calls, so a
# module constant a helper function grows, or an enclosing argv a closure rewrites, is
# invalidated outright; a nested LOCAL that merely shadows the name through one of those
# forms is indistinguishable at these bounds and invalidates too, fail-closed), a tracked
# argv or env bound or grown only AFTER the launch point (a pin appended
# after the launch is never credited to it), and import/alias spellings
# that would re-spell a launch away from the module-qualified form this scan reads.
# A RESOLVED git launch is judged on its EFFECTIVE argv with git's own LAST-VALUE-WINS config
# semantics (verified on git 2.53): the global-option region is parsed positionally, each F-367
# pin counts only as a `-c key=value` pair in OPTION position (a pin string inside a -m message
# argument never counts), a NO_AUTO_MAINTENANCE splice counts only through its RESOLVED literal
# elements, a LATER `-c` that re-sets a pin key to a non-pin value re-enables maintenance and
# is a finding NO env coverage can absorb (-c outranks every environment-scope pin), and a
# pin key whose LAST option-position setting is a `--config-env` - or a `-c` whose
# launch-time value is unreadable behind a readable "key=" prefix - is the same finding
# (that value is unreadable here; -c and --config-env apply in
# command-line order on git 2.53, last value wins, so a LATER `-c` re-pin restores the
# pin). A subcommand WORD that matches an alias.* defined in the SAME argv - or ANY parse
# that ends at an unresolved region while the argv is alias-defining or POSSIBLY
# alias-defining, whatever the pin state or env coverage - is a FINDING no pin or
# coverage absorbs. A pre-pass reads every resolvable `-c`/`--config-env` alias.* pair,
# one spelled AFTER an unresolved slot included, and treats a resolvable
# `-c`/`--config-env` marker whose VALUE slot's config KEY is unreadable (no resolvable
# literal and no literal "key=" prefix; an unknown-length value region included) as
# POSSIBLY alias-defining, fail-closed: that unreadable value can BE an alias.*
# definition. An unreadable VALUE slot whose config KEY head is readable - a literal
# "key=" prefix ahead of an unreadable tail, credited only when the tail is a credited
# str form (_scan_provably_str; any other tail can carry an __radd__ that REPLACES the
# whole value at launch time, so the prefix proves nothing and the slot reads as
# having no readable key, fail-closed above; a credited tail that evaluates to a str
# SUBCLASS can still replace it, the disclosed residual below) - does NOT end the
# parse. For `-c` the first '='
# ends the key (git 2.53), so the prefix fixes the WHOLE key: an alias.* key records
# the defined name (the alias verdict never needs the target), a pin key fails closed
# as stomped unless a LATER `-c` re-pins it (above), and any other key is passed over.
# For `--config-env` git ends the key at the LAST '=' (verified on git 2.53:
# `--config-env alias.seed=a.x=VAR` defines the alias `seed=a.x`, and the word
# `seed=a.x` invokes it), so a fully resolved value is split there and a prefix fixes
# the key only through the prefix's OWN last '=': the launch-time key is either exactly
# the prefix up to that '=' (a tail without '=') or runs past the WHOLE prefix, so the
# head read is that part - the SECTION at most, never the alias name. An alias.* head
# ENDS the parse as the alias finding (the defined name is unreadable, so any later
# word can invoke it), a head that IS a pin key fails closed as stomped the same way,
# and any other head is passed over (`gc.auto=0=` + tail names the key `gc.auto=0` or
# a longer one, never the pin key `gc.auto`; verified on git 2.53). A JOINED
# `--config-env=` token whose tail is unreadable behind a readable `--config-env=`
# head (an f-string with that literal head, or a concatenation credited as above) is
# read through that head exactly like the separate marker and its value slot; git
# rejects a joined `-c` spelling (verified on git 2.53), so `-c` has no joined form.
# The alias verdict is unconditional because
# the alias value is a new command line this scan cannot read,
# a non-shell alias's own `-c` pairs apply AFTER the outer options with last-value-wins,
# and a `!` shell alias runs an arbitrary command line that INHERITS the propagated command
# scope (an ordinary `!` alias sees the outer `-c` pins through GIT_CONFIG_PARAMETERS) yet
# can drop or override it (`!env -u GIT_CONFIG_PARAMETERS git ...` strips exactly those
# pins; both behaviours verified on git 2.53), so the expansion CAN defeat every argv and
# environment pin. An alias.* whose name matches a git BUILT-IN (alias.version=..., then
# `version`) stays this same conservative finding: git 2.53 ignores an alias that shadows
# a built-in and runs the built-in, but the built-in set belongs to whatever git RUNS at
# launch time and is unreadable here, so no built-in name list is trusted (disclosed; a
# reviewed _SCAN_ALLOWED_UNPINNED justification can absorb a specific audited site). A
# launch whose resolved SUBCOMMAND is not maintenance-triggering is out of scope (a trigger
# word in operand position, `git show commit`, is not a launch of that trigger). A scrub or
# lifecycle call covers a launch only from a covering SCOPE: the launch's own function, an
# enclosing function, the module scope, or a call reached through every visible same-module
# caller (a self-test's scrub call never covers a production launch elsewhere in the file;
# scrub-first placement in the named entries is separately enforced by the layer-2 scope/
# checks above); a merely-imported scrub covers nothing. An env= is pin-carrying only when
# it provably derives from git_fixture_env() or from a scope-covered scrubbed os.environ
# WITHOUT overriding any GIT_CONFIG_COUNT/KEY_n/VALUE_n or GIT_CONFIG_PARAMETERS pin
# variable on the way.
# DISCLOSED RESIDUAL (syntactic bounds, the same stance as the routing checks above): this scan
# reads direct, literal Python launch forms only. Element-level literal resolution stays within
# literal lists, same-scope assignments with their additive-augmented/append/extend growth (in
# source order), module-level constants, and one level of same-module function returns for
# env= values. Out of this scan's reach and NOT flagged: a launcher REFERENCED as a value
# rather than called (an alias `launch = subprocess.run`, a launcher stored in a container, a
# multiprocessing or asyncio target=), a git launch INSIDE a launched script or behind a
# non-git wrapper program (an `env`/`sh`/`bash`/interpreter head ends the analysis at that
# head), an alias.* definition whose `-c`/`--config-env` MARKER slot is itself
# unresolved (the pair reads as ordinary unresolved slots, so it cannot mark the argv
# alias-defining; a joined `--config-env=` token is such an unresolved marker when its
# readable head stops short of the full `--config-env=` or its concatenation's right
# operand is not a credited str form, and that list is not exhaustive: a joined token
# spelled through a Name bound elsewhere, `%` formatting or `.format` is not followed
# either, so after the pins it scans clean inside this same residual; an unreadable
# VALUE slot behind a RESOLVABLE
# marker, or behind a readable joined `--config-env=` head, is in reach,
# above: a readable credited "key=" prefix keeps the parse going - except a
# `--config-env` alias.* prefix head, which is the alias finding outright, above -
# and anything else marks the
# argv POSSIBLY alias-defining, the alias finding at any unresolved ending), a
# credited "key=" or `--config-env=` prefix REPLACED at launch time through a str
# SUBCLASS (_scan_provably_str credits a `str(...)` call and an f-string of a single
# replacement field, yet str() returns whatever the operand's __str__ returns and a
# lone replacement field whatever a __format__ returns, so either can be an instance
# of a str subclass; when that subclass overrides __radd__, Python calls it BEFORE
# str.__add__ and it can return any value in place of prefix + tail, so an alias.* or
# pin-key definition can stand behind a prefix this scan read as harmless, and a
# concatenation credited through such an operand proves nothing either - its own
# __add__ or __radd__ decides the value; verified on Python 3.14), an
# unresolved argv tail AFTER the three pins
# are effective in option position with NO resolvable alias.* definition and NO
# possibly-alias-defining unreadable option value anywhere in the
# same argv (the pinned-funnel idiom passes subcommand and operands there; the parse ends
# at the first unresolved token, so even a RESOLVED re-enable spelled after it is out of
# this scan's reach; with a resolvable same-argv alias.* definition or a possibly
# alias-defining unreadable option value - spelled before OR
# after that token - the parse end is the alias finding instead, above), a mutation of a
# tracked argv or env
# reached WITHOUT a bare-name binding: through tuple unpacking, a container element, an
# object attribute, a function that receives the object as an argument, or another module (a
# bare-name alias IS tracked and invalidates resolution, and so does a bare-name REBINDING
# through an assignment, a walrus target, a for-statement target, a `with ... as` target, or
# an `except ... as` name), a mutation spelled as a direct mutator METHOD outside the tracked
# enumeration (append/extend/insert/remove/pop/clear/sort/reverse/update/setdefault/
# __setitem__/__delitem__ are read; dict.popitem, or an in-place dunder called as a method,
# env.__ior__(...) or args.__iadd__(...), is not), a bare-name binding this scan does not
# read as one (a tuple or starred unpacking target, a match-case capture pattern), and a
# function PARAMETER that shares its name with a module-level variable (parameters are not
# read as bindings, so the launch resolves through the module value while Python passes the
# caller's argument). Those forms stay covered by review posture, not
# mechanics.
_SCAN_DIRS = ("tools", "opf/tools")
_SCAN_LAUNCH_NAMES = frozenset(("run", "Popen", "call", "check_call", "check_output",
                                "getoutput", "getstatusoutput"))
_SCAN_SHELL_LAUNCH_NAMES = frozenset(("getoutput", "getstatusoutput"))
_SCAN_OS_LAUNCH_NAMES = frozenset((
    "system", "popen", "posix_spawn", "posix_spawnp", "startfile", "spawn",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"))
_SCAN_ASYNC_LAUNCH_NAMES = frozenset(("create_subprocess_exec", "create_subprocess_shell"))
_SCAN_TRIGGERS = frozenset(("commit", "merge", "rebase", "am", "cherry-pick", "pull",
                            "fetch", "gc", "maintenance"))
# key (git config keys are case-insensitive) -> required value, from the shared constant.
_SCAN_PIN_VALUES = {key.lower(): value for key, value in _git_fixture_env._NO_AUTO_MAINTENANCE}
# The seven process-environment pin variables, plus GIT_CONFIG_PARAMETERS: an env derivation
# that overrides ANY of them (git_fixture_env(GIT_CONFIG_COUNT="0"), dict(os.environ,
# GIT_CONFIG_COUNT="0")) disables the injected config wholesale, and git reads
# GIT_CONFIG_PARAMETERS with the same command scope AFTER the COUNT/KEY_n/VALUE_n family, so
# an override there re-enables maintenance OVER the injected pins (verified on git 2.53);
# either way the derivation can never count as pin-carrying. GIT_CONFIG_GLOBAL /
# GIT_CONFIG_SYSTEM / GIT_CONFIG_NOSYSTEM overrides stay OUT of this set: they select
# FILE-scope config, which every command-scope pin outranks, so they cannot re-enable
# maintenance (verified on git 2.53: a global or system config file carrying
# maintenance.auto=true spawns no maintenance child under the pinned fixture env).
_SCAN_PIN_ENV_VARS = frozenset(_git_fixture_env._MAINTENANCE_PIN_VARS) | frozenset(
    ("GIT_CONFIG_PARAMETERS",))
_SCAN_COVERED_ENV_CALLS = frozenset(("git_fixture_env",))
_SCAN_SCRUB_NAMES = frozenset(("scrub_git_environment", "fixture_git_lifecycle"))
_SCAN_RESOLVE_DEPTH = 6
# Marker for an unknown-length argv region the resolver could not reduce to elements (a helper
# return, a name assigned more than once, an unresolved splice). Distinct from a single opaque
# SLOT (one list element whose VALUE is unresolved, e.g. str(repo)), which stays one argv item.
_SCAN_OPEN = "<unresolved argv region>"
# A pin key whose LAST option-position setting is a --config-env, or a `-c` whose value
# is unreadable behind a readable "key=" prefix, carries a launch-time
# value this scan cannot read: the sentinel never equals a pin value, so the
# verdict fails closed as stomped unless a LATER `-c` re-pins the key (-c and --config-env
# apply in command-line order on git 2.53, last value wins).
_SCAN_CONFIG_ENV_VALUE = "<config-env launch-time value>"
# git GLOBAL options that consume the FOLLOWING argv slot; `--opt=value` spellings are single
# slots and every other `-`-leading token is treated as a bare flag (git itself REJECTS a
# joined `-ckey=value` spelling, verified on git 2.53, so it cannot launch anything). `-c` and
# `--config-env` are handled separately (their values carry, or override, the pins).
# `--attr-source` consumes the following tree slot (verified on git 2.53: `git
# --attr-source HEAD commit ...` reads HEAD as the option value and commit as the
# subcommand).
_SCAN_GIT_OPTION_ARG = frozenset(("-C", "--git-dir", "--work-tree", "--namespace",
                                  "--super-prefix", "--attr-source"))
_SCAN_GIT_HEAD_NAMES = ("git", "GIT", "_GIT", "GIT_BIN")
# Each entry: (file, dotted qualname, covered launch KINDS, justification). One entry covers
# ONLY the launch kinds it declares, so a justification written for an opaque python replay
# can never silently absorb a git-headed or resolvable-triggering launch that later appears in
# the same function (the round-3 BLOCKER mechanism). Kinds: "os" (os-level launcher), "shell"
# (shell-string launch), "async" (asyncio launcher), "dynamic" (exec/eval or dynamic launcher
# access), "unresolved" (a launch whose head, argv, or keywords the resolver cannot read:
# unknown head, no visible argv, **-expanded keywords, executable= override), "git" (a
# git-headed launch whose argv past the option region stays unresolved), "git-triggering" (a
# RESOLVED maintenance-triggering git launch without effective pins - the red-leg probe only).
# An entry that covers "dynamic" carries a fifth field, the exact number of dynamic sites (exec,
# eval, and the dynamic launcher accesses) its justification audits in that function, stated per
# entry; the scan counts that function's dynamic sites and reports a finding when the count
# differs, so a planted extra exec in an allowlisted function is loud rather than absorbed by a
# function-wide justification (a count above the sites left is a stale pin, reported the same way).
_SCAN_ALLOWED_UNPINNED = (
    ("tools/selftest_git_fixture_env.py", "_auto_maintenance_children", ("git-triggering",),
     "the F-367 probe's own traced commit: the green leg passes the pinned fixture env and the"
     " red leg strips exactly the maintenance pins, so pinning this argv would blind both legs"),
    ("tools/aiqt_corpus.py", "git", ("git",),
     "production read-only helper over the real repository (rev-parse/show/ls-files style"
     " reads); its callers never pass a maintenance-triggering subcommand, and production"
     " launches stay unchanged by design"),
    ("tools/check_branch_root.py", "_git", ("git",),
     "production gate probe over the real checkout (rev-parse/merge-base family reads under"
     " _git_env); read-only by design, and production launches stay unchanged"),
    ("tools/check_msg_leaks.py", "_run_git", ("git",),
     "production gate read helper (log/rev-parse over the real repository); read-only by"
     " design, and production launches stay unchanged"),
    ("tools/check_record_sections.py", "_git", ("git",),
     "shared gate/self-test funnel under _clean_env (GIT_* scrubbed, PATH kept):"
     " production callers pass graph reads only (rev-parse/show/ls-tree/merge-base/"
     " cat-file family), and the self-test's fixture init/config/add/commit calls are"
     " reachable only from the lifecycle-wrapped self-test entry, whose PATH-front"
     " wrapper pins every bare git; production launches stay unchanged by design"),
    ("tools/check_release_build.py", "_git", ("git",),
     "production gate read funnel over the real checkout (rev-parse/cat-file/show/"
     " ls-tree/diff-tree/merge-base and `tag -l` listing reads); read-only by design,"
     " and production launches stay unchanged"),
    ("tools/check_release_cut.py", "git", ("git",),
     "production gate read funnel under git_environment() and --no-replace-objects"
     " (rev-parse/cat-file/ls-tree/ls-files/symbolic-ref/check-ref-format/rev-list/"
     " merge-base reads); read-only by design, and production launches stay unchanged"),
    ("tools/check_release_delta.py", "_git", ("git",),
     "production gate read funnel (rev-parse/cat-file/ls-tree/show and `worktree list`"
     " reads); read-only by design, and production launches stay unchanged"),
    ("tools/check_release_delta.py", "_git_raw", ("git",),
     "production gate read funnel with replace refs and grafts disabled"
     " (--no-replace-objects plus GIT_GRAFT_FILE pinned to os.devnull; diff-tree and"
     " merge-base reads for the post-release revision binding, QA round-2); read-only by"
     " design, and production launches stay unchanged"),
    ("tools/check_release_delta.py", "_stage1_git", ("git",),
     "stage-1 re-execution read funnel (a design decision): rev-parse and"
     " cat-file --batch reads only, with --no-replace-objects and -c core.commitGraph=false -c"
     " core.fsmonitor=false in option position plus an INLINE GIT_* scrub (GIT_NO_REPLACE_OBJECTS=1,"
     " GIT_GRAFT_FILE pinned to os.devnull); the env is built inline rather than through"
     " _substitution_free_env because stage 1 runs before that function is defined and may import"
     " nothing from the checkout; read-only by design, never a maintenance-triggering subcommand"),
    ("tools/check_release_delta.py", "_index_materialized_tree", ("git",),
     "throwaway-index staging funnel: the loop variable carries exactly `init -q` and"
     " `add --force -A` over a raw-materialized temp tree under a scrubbed env; neither"
     " subcommand is maintenance-triggering and no commit is ever made there"),
    ("tools/check_version_monotonicity.py", "_git", ("git",),
     "production gate read funnel (rev-parse/cat-file/ls-tree/show/merge-base reads);"
     " read-only by design, and production launches stay unchanged"),
    ("opf/tools/_opf_observe.py", "_run_git_config_discovery", ("git",),
     "production read-only config/index-discovery funnel under --no-pager (config reads,"
     " ls-files, and the no-lazy-fetch cat-file -e availability probe), never a"
     " maintenance-triggering subcommand, and production launches stay unchanged by design"),
    ("opf/tools/check_opf_init.py", "_suite_isolated.git_input", ("git",),
     "stdin-fed fixture plumbing (update-index --index-info style calls) under"
     " _opf_observe._scrubbed_env; its callers pass plumbing subcommands only, never a"
     " maintenance-triggering one"),
    # Cannot-evaluate launches (argv or head outside the resolver's bounds), each audited:
    ("tools/check_git_option_table.py", "_git", ("unresolved",),
     "cannot-evaluate funnel: the argv tail is the caller's *args; every caller passes"
     " option-table introspection forms (init -q --template= of a scratch probe repo,"
     " --version, and per-subcommand -h / --git-completion-helper probes that exit inside"
     " option parsing), never a real maintenance-triggering run"),
    ("tools/check_newtab.py", "_self_test", ("unresolved",),
     "cannot-evaluate head: the launch iterates a literal command table whose heads are all"
     " sys.executable (the copied-tools self-test children); no git launch"),
    ("tools/check_release_build.py", "_materialized_check", ("unresolved",),
     "cannot-evaluate argv: replays the registry-enumerated gate commands (the generators'"
     " python3 --check forms and the manifest-integrity commands) inside a throwaway"
     " materialized checkout; this entry covers ONLY that replayed-command launch - the"
     " checkout's own staging launches beside it are resolved and enforced by this scan,"
     " never absorbed here: the maintenance-capable add and commit argvs carry the three"
     " F-367 pins as literal `-c` pairs in option position, and the bare `git init -q`"
     " carries none (init is not maintenance-triggering)"),
    ("tools/selftest_aiqt_hooks.py", "_main_isolated", ("unresolved",),
     "cannot-evaluate head: replays the REGISTERED hook entry's own dispatcher command (a"
     " python3 hook-script argv from the hooks registry), not a git launch; the suite's"
     " lifecycle PATH wrapper pins any git a hook child resolves through PATH"),
    ("tools/measure_hook_latency.py", "_sample", ("unresolved",),
     "cannot-evaluate head: the timing funnel launches the command each hooks.json PreToolUse"
     " entry declares (python3 running the dispatcher, or the self-test's stub dispatcher)"
     " and that interpreter's -I -c version probe, never git; the dispatcher's own git"
     " calls run inside the launched script, and this tool's fixture git calls go through"
     " _git, whose argv carries gc.auto=0, gc.autoDetach=false and maintenance.auto=false"
     " as literal `-c` pairs in option position"),
    ("tools/selftest_git_fixture_env.py", "_start_member_group", ("unresolved",),
     "cannot-evaluate head: the bounded-run funnel (_run_bounded) receives its argv from its"
     " callers, and every caller passes a sys.executable head (_run_config_member's registered members,"
     " the corpus leak lanes and the member-timeout controls' fixtures); no git launch"),
    ("opf/tools/_opf_adopt_hook.py", "self_test", ("unresolved",),
     "cannot-evaluate argv: the sandbox-mock seam canary calls subprocess.Popen(None)"
     " to prove the mock net denies the subprocess seam; with the mocks in place the"
     " deny stub raises before any launch reaches the seam, and with a REVERTED mock"
     " the real Popen raises TypeError at once (None is not a valid argv), so no"
     " process is ever launched and no git run can occur"),
    ("opf/tools/_opf_observe.py", "_capture_bounded", ("unresolved",),
     "cannot-evaluate funnel: the bounded-read launcher receives argv and env from its"
     " callers; every caller is a read-only observation (rev-parse/config/cat-file family),"
     " and the lazy-fetch-enabled fixture caller passes the three pins via config_overrides"),
    ("opf/tools/_opf_pack_manifest.py", "_runner_check.run_shell", ("unresolved",),
     "cannot-evaluate head: a PATH-resolved bash running this self-test's planted"
     " runner-script bodies; the bodies drive the manifest runner under test, not git"),
    ("opf/tools/check_opf_init_p0.py", "runner_check.run_shell", ("unresolved",),
     "cannot-evaluate head: a PATH-resolved bash running this self-test's planted"
     " runner-script bodies; the bodies drive the runner under test, not git"),
    ("opf/tools/selftest_commonmark_conformance.py", "_run_matrix", ("unresolved",),
     "cannot-evaluate head: re-spawns this conformance harness under each named PATH-resolved"
     " python interpreter (an optional maintainer matrix); not a git launch"),
    ("opf/tools/_opf_adopt_observe.py", "_runner_check.run_shell", ("unresolved",),
     "cannot-evaluate head: a PATH-resolved bash running this self-test's interception probe"
     " and the real opf/tools/run_all_checks.sh text or a red-leg text mutant of it; every"
     " gate is a python3 run (the shimmed python3 fixture, a planted `exit 0` stub, or in"
     " the scrubbed-environment leg sys.executable under env -i on a planted recursion"
     " probe), the fixture's only real child is this module's own --vectors-only leg (its"
     " git launches are scanned at their own sites), and no body spells a git command"),
    ("opf/tools/_opf_adopt_observe.py", "_git_archive_fixture_isolated.git", ("unresolved",),
     "cannot-evaluate head: os.path.abspath of shutil.which(\"git\", path=os.defpath); the"
     " argv carries gc.auto=0, gc.autoDetach=false and maintenance.auto=false as literal"
     " `-c` pairs in option position ahead of the caller's *args, under a private"
     " HOME/XDG_CONFIG_HOME set by _git_archive_fixture, and the callers pass only"
     " init/hash-object/update-index/write-tree/commit-tree/archive (none"
     " maintenance-triggering)"),
    ("opf/tools/_opf_adopt_observe.py", "self_test", ("unresolved",),
     "cannot-evaluate head: os.path.abspath of shutil.which(\"openssl\", path=os.defpath),"
     " the `openssl req -x509` builder of the disposable fixture TLS key/certificate pairs"
     " (env PATH=os.defpath, OPENSSL_CONF=os.devnull); not a git launch"),
    ("opf/tools/_opf_adopt_observe.py", "self_test.run_case.execute_mutant", ("os",),
     "cannot-evaluate os launch: the TG-15/no-process-execution red-leg mutant of _unpack"
     " calls os.system(\"true\"); it is patched in only for that case's mutated run, whose"
     " gather runs inside deny_effects with os.system replaced by a refusing stub, so it"
     " raises before any launch, and unrefused it runs the shell no-op `true`, never git"),
    ("opf/tools/_opf_pack_manifest.py", "_runner_red_checks.checked_environment", ("os",),
     "cannot-evaluate pass-through: the credential-red-check re-invokes the SAVED"
     " subprocess.Popen inside its patched side_effect after asserting the launch"
     " environment; the argv is the runner check's own bash runner-script launch, not git"),
    ("opf/tools/check_opf_init_p0.py", "runner_red_checks.checked_environment", ("os",),
     "cannot-evaluate pass-through: the credential-red-check re-invokes the SAVED"
     " subprocess.Popen inside its patched side_effect after asserting the launch"
     " environment; the argv is the runner check's own bash runner-script launch, not git"),
    ("opf/tools/_opf_emit.py", "_run_fixture_process.exec_subject", ("os",),
     "cannot-evaluate os launch: the fork()ed subject leg of the shared fixture tree owner"
     " rewires stdio onto the capture files and replaces the child image with os.execvpe"
     " (the guardian pidfd/pdeathsig/setsid ownership scheme requires an exec-in-child"
     " image replacement no subprocess list-argv launch can spell); every entry into it is"
     " run_status_owned, whose own argv head is sys.executable running the _fixture_main"
     " supervisor (the opf.py watchdog checks patch it only with wrappers that replay"
     " sys.executable argvs through the saved launcher), and the raw in-child replay runs"
     " the fixture argvs its callers spell: sys.executable CLI/crash subjects throughout"),
    # In-process exec loaders and mutant builders (the dynamic tripwire), each audited: each
    # exec compiles THIS repo's own tracked source, an AST/text mutant of it, or a red-leg
    # candidate body spelled as a string literal in the self-test itself, into a module
    # object, in-process; none launches a process at exec time. A launch the executed
    # TRACKED source spells is scanned at its own source location like every other launch;
    # a launch spelled only inside such a string-literal candidate body is outside the
    # scan's reach and is covered by its entry's audited justification instead (the
    # check_opf_init_observe.py red-leg candidate spells a subprocess.run whose argv
    # heads are sys.executable commands, never git).
    ("tools/selftest_git_fixture_env_core.py", "_system_pin_checks", ("dynamic",),
     "exec of an AST-built PATH-removal mutant of the lifecycle helper's own source, for the"
     " wrapper red leg",
     1),
    ("tools/selftest_git_fixture_env_core.py", "_manifest_extra_setup_failures", ("dynamic",),
     "exec of one registry-derived fixture-setup expression against a stubbed _git, proving"
     " the setup guard refuses a failed git",
     1),
    ("opf/tools/check_opf_init_observe.py", "load_candidate", ("dynamic",),
     "exec-based loader for the copied candidate module under test",
     1),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.candidate", ("dynamic",),
     "exec-based builder for the shared-capture candidate module under test",
     1),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.policy", ("dynamic",),
     "exec-based builder for the policy candidate module under test",
     1),
    ("opf/tools/check_opf_init_p0.py", "red_on_revert", ("dynamic",),
     "exec of the production module's source with one guard reverted (red-leg mutant)",
     1),
    ("opf/tools/_opf_adopt_observe.py", "_cancellation_self_test", ("dynamic",),
     "exec of text mutants of this module's own function and class sources (and of"
     " _opf_adopt.gather_release) with descriptor-cleanup, restore, lock, rollback, TLS, or"
     " resolver-publication sites reverted (red-leg mutants); the exec only defines the"
     " mutant function or class",
     2),
    ("opf/tools/_opf_adopt_observe.py", "self_test.source_mutant", ("dynamic",),
     "exec of this module's own function source with exactly one guard site replaced"
     " (red-leg mutant); the exec only defines the mutant function",
     1),
    ("opf/tools/_opf_emit.py", "_fixture_main", ("dynamic",),
     "exec-based definition of one assertion fixture inside the isolated fixture CHILD:"
     " the body arrives as the `-c` text of an explicit [sys.executable, -I, -B, -c, ...]"
     " argv and is compiled into a single _opf_fixture function (the exec only defines"
     " it; the call is the next statement); every body is a string spelled beside its"
     " run_status_owned call in this repo's tools, and the audited bodies drive imported"
     " repo modules and CLI cases in-process, spelling no process launch of their own",
     1),
    ("opf/tools/_opf_manifest_regressions.py", "_validator_namespace", ("dynamic",),
     "exec-based builder of the manifest-validator namespace: the compiled AST is"
     " inspect.getsource(_opf_store) (or a reviewed AST mutant of it) filtered to its"
     " FunctionDef nodes only, so the exec only defines the validator functions over"
     " dict(vars(_opf_store)); a launch the tracked source spells is scanned at its own"
     " source location, and the census red-leg mutants alter finding emission only",
     1),
    ("opf/tools/opf.py", "_watchdog_completion_case", ("dynamic",),
     "exec of _FixtureProcess._finish_close rebuilt from _opf_emit's own AST with exactly"
     " one pinned QA38/QA39/QA40/QA41/QA51 mutation inserted (red-leg mutants); each exec"
     " only defines the mutant member (for QA51, a one-member class) into dict(vars(emit))"
     " for the behavioural matrix to drive in-process",
     5),
    ("opf/tools/opf.py", "_watchdog_completion_case.displaced_outward", ("dynamic",),
     "exec of the two disclosed leg-11 QA37 vector sources (string literals in this"
     " self-test); the exec only defines each vector's mutant function, whose body calls"
     " the planted wait/abandon_unfinished stubs and _cleanup_boundary, never a process"
     " launch",
     1),
    ("opf/tools/check_opf_record.py", "flip_t70", ("dynamic",),
     "exec of _opf_oplock._acquire_body's own source with the fix-15 head's pre-unwind"
     " removal tagging restored at its failure handler (red-leg mutant); the exec only"
     " defines the mutant function",
     1),
    # #378 close-vector revert builders: each exec compiles inspect.getsource of this tool's own
    # tracked function (or, for check_release_cut's sweep reverts, this tool's own tracked file)
    # with one literal replacement spelled beside it; no replacement text spells a launch, and
    # every launch the tracked source spells is scanned at its own source location.
    ("opf/tools/_opf_emit.py", "_st_guardian_close_reuse", ("dynamic",),
     "exec of _FixtureProcess._guardian's own source with its ownership-first subject_fd close"
     " put back as the close-then-rebind body (red-leg mutant); the exec only defines the"
     " mutant function, which the vector drives with fork, waitid and _exit stubbed",
     1),
    ("opf/tools/check_opf_prompt_pack.py", "_close_vectors", ("dynamic",),
     "exec of _read_regular's own source with its closefd=False fdopen put back as the pre-fix"
     " fdopen ownership (red-leg mutant); the exec only defines the mutant function",
     1),
    ("tools/check_footer.py", "_close_vectors", ("dynamic",),
     "exec of _read_regular_page's own source with its closefd=False fdopen put back as the"
     " pre-fix fdopen ownership (red-leg mutant); the exec only defines the mutant function",
     1),
    ("tools/check_release_cut.py", "_close_vectors", ("dynamic",),
     "exec of working_blob's own source with its closefd=False fdopen put back as the pre-fix"
     " fdopen ownership (red-leg mutant); the exec only defines the mutant function",
     1),
    ("tools/check_release_cut.py", "_self_test_isolated", ("dynamic",),
     "exec of this tool's own source with one _CLOSE_SWEEP_REVERTS literal applied to the"
     " sweep code (red-leg mutant) under a non-__main__ __name__, so main() never runs; the"
     " module body only binds imports, constants and definitions and puts opf/tools on"
     " sys.path, and the self-test then calls the mutant's _close_sweep_shapes_red, an AST"
     " sweep over synthetic shapes",
     1),
    ("opf/tools/_opf_init_substrate.py", "_t_s23_plan_close_reuse", ("dynamic",),
     "exec of begin_operation's own source with its ownership-first plan_fd close put back as"
     " the close-then-rebind body (red-leg mutant); the exec only defines the mutant function",
     1),
    ("tools/pin.py", "_recover_close_vectors", ("dynamic",),
     "exec of do_recover's own source with its four ownership-first root_fd closes and its"
     " owned-number handler guard put back as the pre-fix bodies (red-leg mutant); the exec"
     " only defines the mutant function",
     1),
)


def _scan_alias_findings(rel, tree):
    """Import and alias spellings that would re-spell a process launch away from the
    module-qualified form this scan reads, as findings. Only LAUNCH-CAPABLE names are flagged:
    `from subprocess import PIPE` imports no launcher and is harmless, while a from-import
    (bare, aliased, or *) of a launcher, `import subprocess as x`, or a plain-name alias
    `x = subprocess` hides launches."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os",
                                                                 "asyncio"):
            launchers = (_SCAN_LAUNCH_NAMES if node.module == "subprocess"
                         else (_SCAN_ASYNC_LAUNCH_NAMES if node.module == "asyncio"
                               else _SCAN_OS_LAUNCH_NAMES))
            for alias in node.names:
                if alias.name == "*" or alias.name in launchers:
                    out.append("%s:%d <module>: from-import of %s.%s hides launches from the"
                               " maintenance-pin scan; call the module-qualified launcher"
                               " directly" % (rel, node.lineno, node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" and alias.asname:
                    out.append("%s:%d <module>: subprocess imported as %r hides launches from"
                               " the maintenance-pin scan" % (rel, node.lineno, alias.asname))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = getattr(node, "value", None)
            if isinstance(value, ast.Name) and value.id == "subprocess":
                out.append("%s:%d <module>: aliasing subprocess to another name hides launches"
                           " from the maintenance-pin scan" % (rel, node.lineno))
    return out


def _scan_module_consts(tree):
    """Module-level single-target assignments, name to value node. A name the module scope
    binds more than once, grows (an append/extend or an augmented assignment), or mutates
    in place, or that ANY nested same-module scope mutates, grows, aliases, or declares
    global (a helper function extending a module-level argv re-enables maintenance without
    ever touching the module scope), is EXCLUDED outright: argv mutation invalidates
    resolution at EVERY scope, so an initial value the module - or any function in it -
    later rewrites is never trusted (a module-scope launch additionally resolves through
    the module scope itself, where growth is read in source order and bounded to the
    launch point)."""
    consts = dict()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value
    for name in list(consts):
        plain, extend, mutated = _scan_local_assigns(tree, name)
        if mutated or extend or len(plain) != 1:
            del consts[name]
    return consts


def _scan_function_defs(tree):
    """Every function definition in the module, by bare name (any nesting), for the bounded
    return-value resolution of env= expressions."""
    defs = dict()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    return defs


def _scan_local_assigns(func_node, name, launch=None):
    """(plain assignment value nodes, splice extension value nodes, mutated) for Name <name>
    in the scope's own statements, in SOURCE order (an extend that adds the pins before the
    extend that adds the subcommand must be read in exactly that order, never reversed);
    <func_node> may be the MODULE node for a module-scope launch, so module-level argv
    mutation is tracked exactly like function-scope mutation. Nested function and class
    bodies are other scopes: they contribute no plain or extend hits, but they ARE read
    for mutation (below). `mutated` is True when the scope rewrites the value in place
    through a form the resolver does not model (a subscript or slice write, an insert/
    remove/pop/clear/sort/reverse/update/setdefault call, a del, or a NON-additive
    augmented assignment: only += is growth - `args *= 0` EMPTIES the list, so every
    other operator invalidates), when the scope binds the object to ANOTHER bare name (an
    alias `other = name`: a later mutation through the alias is invisible to this
    per-name reading, so the alias itself invalidates), when the scope REBINDS the name
    through a walrus target, a for-statement target, a `with ... as` target, or an
    `except ... as` name (this scan does not order control flow or merge branches, so a
    rebound name is invalidated outright, fail-closed, exactly like a second
    assignment), when a NESTED same-module scope
    mutates, grows, aliases, or declares global/nonlocal the name (this scan does not
    order cross-scope calls, so a helper that grows a module constant or a closure that
    rewrites an enclosing argv invalidates it outright; a nested local that merely
    shadows the name through one of those forms is indistinguishable at these bounds and
    invalidates too, fail-closed), or - when <launch> is the
    launch point's (lineno, col_offset) - when ANY binding or growth of the name sits AFTER
    that point: this scan does not order control flow, so a pin appended after the launch
    is never credited to it and the resolution is invalidated outright. Two DISCLOSED
    residuals bound this reading (review posture, not mechanics): a mutator METHOD
    outside the enumerated set is NOT read as mutation (dict.popitem, or an in-place
    dunder called as a method: env.__ior__(...), args.__iadd__(...)), and a function
    PARAMETER is never read as a binding, so a parameter that shares its name with a
    module-level constant leaves this scope's reading empty and the callers fall back
    to the MODULE value while Python passes the caller's argument."""
    plain, extend, mutated = [], [], False
    if func_node is None:
        return plain, extend, mutated
    hits = []
    nested = []
    stack = list(func_node.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            nested.append(node)
            continue
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                hits.append((node.lineno, node.col_offset, "plain", node.value))
            if any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                   and t.value.id == name for t in node.targets):
                mutated = True
            if isinstance(node.value, ast.Name) and node.value.id == name and any(
                    not (isinstance(t, ast.Name) and t.id == name) for t in node.targets):
                mutated = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name \
                    and node.value is not None:
                hits.append((node.lineno, node.col_offset, "plain", node.value))
            elif isinstance(node.target, ast.Subscript) \
                    and isinstance(node.target.value, ast.Name) \
                    and node.target.value.id == name:
                mutated = True
            elif isinstance(node.value, ast.Name) and node.value.id == name:
                mutated = True
        elif isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                # A walrus REBINDING of the name: a binding this reading does not
                # order or merge, so it invalidates outright, fail-closed.
                mutated = True
            elif isinstance(node.value, ast.Name) and node.value.id == name:
                mutated = True
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                # A for-target REBINDING of the name: same invalidation.
                mutated = True
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            if any(isinstance(item.optional_vars, ast.Name)
                   and item.optional_vars.id == name for item in node.items):
                # A `with ... as` REBINDING of the name: same invalidation.
                mutated = True
        elif isinstance(node, ast.ExceptHandler):
            if node.name == name:
                # An `except ... as` REBINDING of the name: same invalidation.
                mutated = True
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if isinstance(node.op, ast.Add):
                    hits.append((node.lineno, node.col_offset, "extend", node.value))
                else:
                    mutated = True
            elif isinstance(node.target, ast.Subscript) \
                    and isinstance(node.target.value, ast.Name) \
                    and node.target.value.id == name:
                mutated = True
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    mutated = True
                elif isinstance(target, ast.Subscript) \
                        and isinstance(target.value, ast.Name) and target.value.id == name:
                    mutated = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == name:
            if node.func.attr == "append" and node.args:
                hits.append((node.lineno, node.col_offset, "extend",
                             ast.List(elts=list(node.args), ctx=ast.Load())))
            elif node.func.attr == "extend" and node.args:
                hits.append((node.lineno, node.col_offset, "extend", node.args[0]))
            elif node.func.attr in ("insert", "remove", "pop", "clear", "sort", "reverse",
                                    "update", "setdefault", "__setitem__", "__delitem__"):
                mutated = True
        stack.extend(ast.iter_child_nodes(node))
    # Nested function and class bodies contribute no hits, but a closure or helper CAN
    # mutate, grow, or alias the same OBJECT through the free (or global) variable, and
    # this scan does not order cross-scope calls: any such form inside a nested scope -
    # a shadowing nested local included, indistinguishable at these bounds - invalidates
    # the resolution outright, fail-closed.
    for scope in nested:
        if mutated:
            break
        for node in ast.walk(scope):
            if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                mutated = True
            elif isinstance(node, ast.AugAssign) and (
                    (isinstance(node.target, ast.Name) and node.target.id == name)
                    or (isinstance(node.target, ast.Subscript)
                        and isinstance(node.target.value, ast.Name)
                        and node.target.value.id == name)):
                mutated = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                if any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                       and t.value.id == name for t in targets):
                    mutated = True
                value = getattr(node, "value", None)
                if isinstance(value, ast.Name) and value.id == name:
                    mutated = True
            elif isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Name) \
                    and node.value.id == name:
                mutated = True
            elif isinstance(node, ast.Delete) and any(
                    (isinstance(t, ast.Name) and t.id == name)
                    or (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                        and t.value.id == name) for t in node.targets):
                mutated = True
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == name and node.func.attr in (
                        "append", "extend", "insert", "remove", "pop", "clear", "sort",
                        "reverse", "update", "setdefault", "__setitem__", "__delitem__"):
                mutated = True
            if mutated:
                break
    if launch is not None:
        for lineno, col, _kind, _value in hits:
            if (lineno, col) > launch:
                mutated = True
                break
    for _, _, kind, value in sorted(hits, key=lambda hit: (hit[0], hit[1])):
        (plain if kind == "plain" else extend).append(value)
    return plain, extend, mutated


def _scan_flatten(expr, func_node, module_consts, depth, launch=None):
    """(ordered argv entries, open) within the resolver's bounds: literal lists and tuples,
    + concatenation, list()/tuple() wrapping, starred splices, and Name resolution through a
    single same-scope assignment (with its append/extend growth as tail entries) or a
    module-level literal (a function PARAMETER is not read as a binding, so a parameter
    that shares its name with a module-level constant resolves through the MODULE value
    while Python passes the caller's argument, and a mutator method outside
    _scan_local_assigns' enumerated set - args.__iadd__(...) - is not read as mutation:
    both disclosed residuals, held by review posture, not mechanics). Each entry is a
    single-slot AST node, or the _SCAN_OPEN marker where
    an unknown-length region the resolver cannot reduce sits, so a POSITIONAL reading knows
    exactly where its knowledge ends (a marker at the head hides the program; a marker
    after effective pins ENDS the positional parse there, so anything spelled beyond it -
    a resolved re-enable included - is out of the parse's reach: the disclosed
    unresolved-tail residual, held by review posture, not mechanics)."""
    if depth <= 0:
        return [_SCAN_OPEN], True
    if isinstance(expr, (ast.List, ast.Tuple)):
        entries, opened = [], False
        for elt in expr.elts:
            if isinstance(elt, ast.Starred):
                inner, inner_open = _scan_flatten(elt.value, func_node, module_consts,
                                                  depth - 1, launch)
                entries.extend(inner)
                opened = opened or inner_open
            else:
                entries.append(elt)
        return entries, opened
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left, lopen = _scan_flatten(expr.left, func_node, module_consts, depth - 1, launch)
        right, ropen = _scan_flatten(expr.right, func_node, module_consts, depth - 1,
                                     launch)
        return left + right, lopen or ropen
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
            and expr.func.id in ("list", "tuple") and len(expr.args) == 1 and not expr.keywords:
        return _scan_flatten(expr.args[0], func_node, module_consts, depth - 1, launch)
    if isinstance(expr, ast.Name):
        plain, extend, mutated = _scan_local_assigns(func_node, expr.id, launch)
        if mutated:
            # An in-place rewrite (args[6] = ..., args.insert(...)) invalidates every resolved
            # element: an unknown-length region, never the pre-mutation value.
            return [_SCAN_OPEN], True
        if not plain and expr.id in module_consts:
            plain = [module_consts[expr.id]]
        if len(plain) != 1:
            # No assignment in reach, or more than one (source order between them and the
            # launch is not tracked): an unknown-length region, never a guessed merge.
            return [_SCAN_OPEN], True
        entries, opened = _scan_flatten(plain[0], func_node, module_consts, depth - 1,
                                        launch)
        for value in extend:
            more, more_open = _scan_flatten(value, func_node, module_consts, depth - 1,
                                            launch)
            entries.extend(more)
            opened = opened or more_open
        return entries, opened
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) \
            and expr.value.id in ("self", "cls"):
        # A class-level constant (the scan loop merges the innermost enclosing class's
        # single-target assignments into the consts mapping under "self."/"cls." keys).
        target = expr.value.id + "." + expr.attr
        if target in module_consts:
            return _scan_flatten(module_consts[target], func_node, module_consts,
                                 depth - 1, launch)
    return [_SCAN_OPEN], True


def _scan_program_kind(value):
    """bare-git / abs-git / non-git for a literal program string."""
    if value == "git":
        return "bare-git"
    if value.endswith("/git"):
        return "abs-git"
    return "non-git"


def _scan_head_kind(node, func_node, module_consts, depth, launch=None):
    """bare-git / abs-git / named-git / non-git / unknown for the first argv entry."""
    if node is None or node is _SCAN_OPEN or depth <= 0:
        return "unknown"
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        value = node.value
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                return "unknown"
        return _scan_program_kind(value)
    if isinstance(node, ast.Call):
        callee = node.func
        callee_name = callee.attr if isinstance(callee, ast.Attribute) else (
            callee.id if isinstance(callee, ast.Name) else None)
        if callee_name == "which" and len(node.args) == 1 and not node.keywords \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            return _scan_program_kind(node.args[0].value)
        if callee_name in ("str", "abspath", "realpath", "fspath") and len(node.args) == 1:
            return _scan_head_kind(node.args[0], func_node, module_consts, depth - 1,
                                   launch)
        return "unknown"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # A pathlib join: the RIGHTMOST component names the program.
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return "abs-git" if node.right.value == "git" else "non-git"
        return "unknown"
    if isinstance(node, ast.Name):
        plain, _, mutated = _scan_local_assigns(func_node, node.id, launch)
        if mutated:
            return "unknown"
        if not plain and node.id in module_consts:
            plain = [module_consts[node.id]]
        kinds = set(_scan_head_kind(value, func_node, module_consts, depth - 1, launch)
                    for value in plain)
        if len(kinds) == 1 and "unknown" not in kinds:
            return kinds.pop()
        if node.id in _SCAN_GIT_HEAD_NAMES:
            return "named-git"
        return "unknown"
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
            target = node.value.id + "." + node.attr
            if target in module_consts:
                return _scan_head_kind(module_consts[target], func_node, module_consts,
                                       depth - 1, launch)
        if node.attr == "executable":
            return "non-git"
        if node.attr in _SCAN_GIT_HEAD_NAMES:
            return "named-git"
        return "unknown"
    return "unknown"


def _scan_element_literal(node, func_node, module_consts, depth, launch=None):
    """The literal string a single argv ENTRY resolves to within the resolver's bounds, or None
    for an opaque slot (one argv element whose value the scan cannot read)."""
    if node is None or node is _SCAN_OPEN or depth <= 0:
        return None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            try:
                return node.value.decode("ascii")
            except UnicodeDecodeError:
                return None
        return None
    if isinstance(node, ast.Name):
        plain, extend, mutated = _scan_local_assigns(func_node, node.id, launch)
        if not plain and node.id in module_consts:
            plain = [module_consts[node.id]]
        if len(plain) == 1 and not extend and not mutated:
            return _scan_element_literal(plain[0], func_node, module_consts, depth - 1,
                                         launch)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id in ("self", "cls"):
        target = node.value.id + "." + node.attr
        if target in module_consts:
            return _scan_element_literal(module_consts[target], func_node, module_consts,
                                         depth - 1, launch)
    return None


def _scan_provably_str(node, func_node, module_consts, depth, launch=None):
    """True when this `+` RIGHT operand is a CREDITED str form: a str literal, an
    f-string, a call spelled `str(...)` (trusted as the builtin), a concatenation of
    credited operands, or an expression the resolver reads to a literal string. A str
    literal, a resolved literal, and an f-string with a literal part or with two or
    more replacement fields evaluate to an exact str, so the left operand stays a true
    prefix of the launch-time value. A `str(...)` call and an f-string of a single
    replacement field are credited WITHOUT that proof - DISCLOSED RESIDUAL, not
    closed: str() returns whatever the operand's __str__ returns and a lone
    replacement field whatever a __format__ returns, so either can be an instance of a
    str SUBCLASS; when that subclass overrides __radd__, Python calls it BEFORE
    str.__add__ (a right operand whose type is a subclass of the left operand's type
    and overrides the reflected method goes first) and it can return any value at
    all, REPLACING the credited prefix. A concatenation credited through such an
    operand proves nothing either: its own __add__ or __radd__ decides the value
    (verified on Python 3.14). Anything else - a call, a name, an attribute this scan
    cannot read - can carry an __radd__ that REPLACES the whole value at launch time
    (str defines no numeric __add__ slot, so the + operator consults the right
    operand's __radd__ before str's sequence concatenation and no TypeError protects
    the prefix; calling str.__add__ directly would raise instead), and
    is not credited."""
    if depth <= 0 or node is None or node is _SCAN_OPEN:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "str":
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _scan_provably_str(node.left, func_node, module_consts, depth - 1,
                                  launch) \
            and _scan_provably_str(node.right, func_node, module_consts, depth - 1,
                                   launch)
    return _scan_element_literal(node, func_node, module_consts, depth,
                                 launch) is not None


def _scan_value_key_prefix(node, func_node, module_consts, depth, launch=None):
    """The literal string a -c/--config-env VALUE expression provably STARTS with, within
    the resolver's bounds, or None: a "key=" + tail concatenation (or an f-string with a
    literal head) fixes the launch-time value's leading characters even when the tail is
    unreadable. What that prefix fixes differs by marker (git 2.53): for `-c` the first
    '=' ends the key, so a prefix through '=' fixes the WHOLE key; for `--config-env`
    git ends the key at the LAST '=', so a prefix fixes the key only through the
    prefix's own last '=' - the section at most, never the alias name - and the
    callers route it fail-closed. A prefix without '=' proves nothing about the key -
    the unreadable remainder can complete ANY key, an alias.* one included - and every
    caller stays fail-closed on it. A `+` contributes a prefix only when its RIGHT
    operand is a credited str form (_scan_provably_str above): any other right side
    can carry an __radd__ that REPLACES the whole value at launch time, so the left
    side proves nothing there and the prefix stays unreadable, fail-closed; a credited
    right side can still replace it through a str subclass (the disclosed residual
    there). An f-string whose first part is literal evaluates to an exact str that
    starts with that part."""
    if depth <= 0 or node is None or node is _SCAN_OPEN:
        return None
    text = _scan_element_literal(node, func_node, module_consts, depth, launch)
    if text is not None:
        return text
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) \
            and _scan_provably_str(node.right, func_node, module_consts, depth - 1,
                                   launch):
        return _scan_value_key_prefix(node.left, func_node, module_consts, depth - 1,
                                      launch)
    if isinstance(node, ast.JoinedStr) and node.values \
            and isinstance(node.values[0], ast.Constant) \
            and isinstance(node.values[0].value, str):
        return node.values[0].value
    return None


def _scan_joined_config_env_head(node, func_node, module_consts, depth, launch=None):
    """The readable VALUE head of a JOINED `--config-env=` argv entry whose tail the
    resolver cannot read, or None: the text after `--config-env=` in the entry's
    readable prefix (_scan_value_key_prefix above - an f-string with that literal
    head, or a concatenation whose right operand is a credited str form). git reads
    the joined spelling like the separate `--config-env` marker and its value slot,
    so the callers route this head exactly like a separate value slot's "key="
    prefix. A readable prefix that stops short of the full `--config-env=` proves no
    marker and yields None (an unresolved slot, disclosed above); git rejects a
    joined `-c` spelling (verified on git 2.53), so `-c` has no joined form."""
    prefix = _scan_value_key_prefix(node, func_node, module_consts, depth, launch)
    if prefix is None or not prefix.startswith("--config-env="):
        return None
    return prefix[len("--config-env="):]


def _scan_git_argv_state(entries, func_node, module_consts, launch=None):
    """(status, subcommand) for a git-headed argv parsed POSITIONALLY through git's
    global-option region with git's own LAST-VALUE-WINS config semantics (verified on git
    2.53: a later `-c gc.auto=6700` overrides an earlier `-c gc.auto=0`, and -c and
    --config-env for the SAME key apply in command-line order, the later one winning):
    'pinned' when every F-367 pin key's LAST resolved option-position value is the pin
    value at the point the parse ends - the resolved subcommand, or the start of an
    unresolved region (a tail is trusted only AFTER effective pins AND only in an
    argv that is neither alias-defining nor possibly alias-defining, below - the
    pinned-funnel idiom's subcommand-and-operands slot; the parse ENDS there, so a
    re-enable spelled
    after that point, even a resolved one, is out of this parse's reach: the disclosed
    unresolved-tail residual); 'stomped' when a pin key's LAST resolved value is NOT the
    pin value, or its LAST option-position setting is a `--config-env` or a `-c`
    whose value is unreadable behind a readable "key=" prefix (that value is
    read at launch time, unreadable here, and it outranks every
    environment-scope pin, so no env coverage can absorb it); 'subcommand' with the
    resolved subcommand token when the pins are not effective; 'alias' when the
    resolved subcommand WORD matches an alias.* name defined in this SAME argv,
    whatever the pin state (a name matching a git BUILT-IN included: git 2.53
    ignores an alias that shadows a built-in and runs the built-in, but the
    launch-time git's built-in set is unreadable here, so no name list is
    trusted and the conservative finding stays, disclosed above), or when the
    parse ends at ANY unresolved region - an unresolved slot, an unknown-length
    region, or a dangling or unknown-length option-value slot - while the argv
    is alias-defining or POSSIBLY alias-defining: the pre-pass below reads
    every resolvable `-c`/`--config-env` alias.* pair, one spelled AFTER an
    unresolved slot this positional parse stops at included, and marks the
    argv possibly alias-defining when a resolvable `-c`/`--config-env` marker
    carries a VALUE slot whose config KEY is unreadable (that value can BE an
    alias.* definition this parse cannot see), whatever the pin state or env
    coverage
    (the invoked alias VALUE is a new command line this parse cannot read: a
    non-shell alias's own `-c` pairs apply AFTER the outer options with
    last-value-wins, and a `!` shell alias runs an arbitrary command line that
    inherits the propagated command scope, GIT_CONFIG_PARAMETERS, yet can drop
    or override it - an ordinary `!` alias INHERITS the `-c` pins and one that
    strips GIT_CONFIG_PARAMETERS removes them, verified on git 2.53 - so the
    expansion CAN defeat every argv and environment pin: never 'pinned',
    whatever the outer options say). An unreadable `-c` VALUE slot whose
    config KEY IS readable - a literal "key=" prefix ahead of an unreadable
    tail; for `-c` the first '=' ends the key (git 2.53) - never ends the
    parse: an alias.* key records the defined name (the alias verdict needs
    the name, never the target), a pin key is recorded on the unreadable-value
    sentinel (stomped above, unless a LATER `-c` re-pins it), and any other
    key is passed over. An unreadable `--config-env` VALUE slot behind a
    readable "key=" prefix fixes the launch-time key only through the prefix's
    own LAST '=' (git ends a --config-env key at the LAST '=', verified on git
    2.53, so the key is either exactly the prefix up to that '=' or runs past
    the whole prefix): an alias.* head ends the parse AS the alias finding
    (the defined name is unreadable, so any later word can invoke it), a head
    that IS a pin key records the same sentinel, and any other head is passed
    over. A JOINED `--config-env=` token whose tail is unreadable behind a
    readable `--config-env=` head is read through that head exactly like the
    separate marker and its value slot, in the pre-pass and here. Either
    prefix is credited only when the unreadable tail is a credited str form
    (_scan_provably_str: an __radd__ on any other tail REPLACES the whole
    value at launch time; a credited tail can still replace it through a str
    subclass, the residual disclosed there); otherwise the slot reads as
    having no readable key, above. 'opaque'
    when an unknown-length region, an unresolved slot, or a
    truncated option reaches the parser before the pins are effective in an argv
    that is neither alias-defining nor possibly alias-defining (with either, those
    endings are the alias verdict, above), or when a
    SUBCOMMAND that matches no same-argv alias.* name is reached after an alias.*
    option with the pins not effective (an alias.* option never ENDS the parse: config
    evaluation continues through it with last-value-wins, so an adverse pin BEFORE the
    alias is still overridden by a later re-pin and a stomp AFTER the alias is still
    detected; a word other than the defined names is not remapped by the same-argv
    aliases, but with the pins not effective this parse keeps it coverage-gated,
    fail-closed); 'end'
    for a fully resolved argv that never reaches a subcommand."""
    pin_state = dict()
    alias_seen = False
    alias_names = set()
    # Pre-pass: alias.* definitions readable ANYWHERE in the argv. The positional
    # parse below stops at the first unresolved entry, so an alias.* pair spelled
    # AFTER such a slot is invisible to it while git can still read the pair as
    # global-option config at launch time (the slot may resolve to an option
    # there). Whether a pair sits inside the global-option region cannot be
    # established past an unresolved slot, so ANY resolvable `-c`/`--config-env`
    # pair carrying an alias.* key marks the argv alias-defining, fail-closed -
    # and a resolvable marker whose VALUE slot's config KEY is unreadable (no
    # resolvable literal and no literal "key=" prefix; an unknown-length value
    # region included) marks it POSSIBLY alias-defining too: that unreadable
    # value can BE an alias.* definition this pre-pass cannot see (the round-8
    # escape). Over-reading a pair git would treat as subcommand operands can
    # only ADD a conservative finding when the parse also ends at an unresolved
    # region, never hide one (a parse that never ends unresolved never consults
    # this flag).
    alias_defined = False
    for pre in range(1, len(entries)):
        pre_entry = entries[pre]
        if pre_entry is _SCAN_OPEN:
            continue
        pre_token = _scan_element_literal(pre_entry, func_node, module_consts,
                                          _SCAN_RESOLVE_DEPTH, launch)
        if pre_token is None:
            # A JOINED `--config-env=` token with a readable head over an
            # unreadable tail reads exactly like a separate value slot's "key="
            # prefix: an alias.* head marks the argv alias-defining, and a head
            # with no '=' leaves the key unreadable - POSSIBLY alias-defining.
            pre_joined = _scan_joined_config_env_head(pre_entry, func_node,
                                                      module_consts,
                                                      _SCAN_RESOLVE_DEPTH, launch)
            if pre_joined is not None and (
                    "=" not in pre_joined
                    or pre_joined.partition("=")[0].lower().startswith("alias.")):
                alias_defined = True
                break
            continue
        pre_config_env = pre_token == "--config-env" \
            or pre_token.startswith("--config-env=")
        pre_prefix_only = False
        if pre_token.startswith("--config-env="):
            pre_value = pre_token.partition("=")[2]
        elif pre_token in ("-c", "--config-env"):
            if pre + 1 >= len(entries):
                # A dangling marker at the very end of the argv has no value
                # slot at all: git rejects it before running anything, so
                # nothing can be defined there.
                continue
            if entries[pre + 1] is _SCAN_OPEN:
                alias_defined = True
                break
            pre_value = _scan_element_literal(entries[pre + 1], func_node,
                                              module_consts, _SCAN_RESOLVE_DEPTH,
                                              launch)
            if pre_value is None:
                pre_prefix_only = True
                pre_value = _scan_value_key_prefix(entries[pre + 1], func_node,
                                                   module_consts,
                                                   _SCAN_RESOLVE_DEPTH, launch)
                if pre_value is None or "=" not in pre_value:
                    alias_defined = True
                    break
        else:
            continue
        # `-c` ends its key at the FIRST '='; `--config-env` ends its key at the
        # LAST '=' (verified on git 2.53), so a fully resolved --config-env value
        # is split with rpartition. A prefix-only value (a readable "key=" head
        # over an unreadable tail) fixes only the key's leading characters - its
        # first-'=' head is what the launch-time key is guaranteed to start with
        # under either split - so it decides the SECTION at most and is checked
        # with partition for both markers.
        if pre_config_env and not pre_prefix_only:
            pre_key = pre_value.rpartition("=")[0]
        else:
            pre_key = pre_value.partition("=")[0]
        if pre_key.lower().startswith("alias."):
            alias_defined = True
            break

    def _verdict(sub):
        for key, value in _SCAN_PIN_VALUES.items():
            got = pin_state.get(key)
            if got is not None and got != value:
                return "stomped", sub
        if all(pin_state.get(key) == value for key, value in _SCAN_PIN_VALUES.items()):
            return "pinned", sub
        return None

    index = 1
    while index < len(entries):
        entry = entries[index]
        token = None
        joined = None
        if entry is not _SCAN_OPEN:
            token = _scan_element_literal(entry, func_node, module_consts,
                                          _SCAN_RESOLVE_DEPTH, launch)
            if token is None:
                joined = _scan_joined_config_env_head(entry, func_node,
                                                      module_consts,
                                                      _SCAN_RESOLVE_DEPTH, launch)
        if token is None and joined is None:
            if alias_defined:
                # The unresolved region can spell a defined (or possibly defined)
                # alias name and the
                # alias expansion can defeat every argv and environment pin
                # (docstring above): the alias finding, whatever the pin state
                # (pinned, stomped, or neither) and whatever env= coverage the
                # launch would otherwise get credit for.
                return "alias", None
            state = _verdict(None)
            return state or ("opaque", None)
        if token == "-c":
            if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                # A dangling or unknown-length VALUE slot ends the parse; with the
                # argv (possibly) alias-defining, the ending is the alias finding,
                # exactly like the unresolved-slot ending above.
                return ("alias" if alias_defined else "opaque"), None
            value = _scan_element_literal(entries[index + 1], func_node, module_consts,
                                          _SCAN_RESOLVE_DEPTH, launch)
            unreadable = value is None
            if unreadable:
                # The launch-time value is unreadable: read the config KEY from the
                # value expression's literal "key=" prefix instead (the first '='
                # ends the key). With no readable key the value can BE an alias.*
                # definition - the pre-pass marked this argv possibly
                # alias-defining - so the parse ends as the alias finding.
                value = _scan_value_key_prefix(entries[index + 1], func_node,
                                               module_consts, _SCAN_RESOLVE_DEPTH,
                                               launch)
                if value is None or "=" not in value:
                    return ("alias" if alias_defined else "opaque"), None
            key = value.partition("=")[0].lower()
            if key.startswith("alias."):
                # A command-scope alias definition is CONFIG, not a parse terminator:
                # git keeps applying later -c/--config-env values with last-value-wins,
                # so the parse continues through it (an adverse pin before the alias is
                # still overridden by a later re-pin; a stomp after it is still
                # detected). The defined NAME is recorded: a word equal to it INVOKES
                # the alias (the unconditional 'alias' verdict below), and any other
                # word stays untrusted while the pins are not effective. An
                # unreadable value with a readable "alias.<name>=" prefix records
                # the same name: only the alias TARGET is unreadable there, which
                # the alias verdict never needs.
                alias_seen = True
                alias_names.add(key[len("alias."):])
                index += 2
                continue
            if key in _SCAN_PIN_VALUES:
                # Track the LAST value per pin key, exactly as git will apply it. An
                # unreadable launch-time value re-setting a pin key records the
                # sentinel instead: it never equals the pin value, so the verdict
                # fails closed as stomped unless a LATER `-c` re-pins the key,
                # exactly like --config-env below.
                pin_state[key] = _SCAN_CONFIG_ENV_VALUE if unreadable \
                    else value.partition("=")[2]
            index += 2
            continue
        if joined is not None or token == "--config-env" \
                or token.startswith("--config-env="):
            prefix_only = False
            if joined is not None:
                # A JOINED `--config-env=` token with an unreadable tail: its
                # readable head after `--config-env=` is read exactly like a
                # separate value slot's "key=" prefix below, and a head with no
                # '=' leaves the key unreadable (the pre-pass marked this argv
                # possibly alias-defining), so the parse ends fail-closed.
                prefix_only = True
                value = joined
                if "=" not in value:
                    return ("alias" if alias_defined else "opaque"), None
                step = 1
            elif token == "--config-env":
                if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                    # Same fail-closed ending as the -c value slot above.
                    return ("alias" if alias_defined else "opaque"), None
                value = _scan_element_literal(entries[index + 1], func_node, module_consts,
                                              _SCAN_RESOLVE_DEPTH, launch)
                if value is None:
                    # Only the config KEY is ever needed here (the effective value
                    # is a launch-time environment read either way): a literal
                    # "key=" prefix supplies its leading characters, and with no
                    # readable prefix the slot can define an alias.*, so the parse
                    # ends as the alias finding, fail-closed.
                    prefix_only = True
                    value = _scan_value_key_prefix(entries[index + 1], func_node,
                                                   module_consts,
                                                   _SCAN_RESOLVE_DEPTH, launch)
                    if value is None or "=" not in value:
                        return ("alias" if alias_defined else "opaque"), None
                step = 2
            else:
                value = token.partition("=")[2]
                step = 1
            # git ends a --config-env key at the LAST '=' (verified on git 2.53:
            # `--config-env alias.seed=a.x=VAR` defines the alias `seed=a.x` and
            # the word `seed=a.x` invokes it), so a fully resolved value is split
            # with rpartition. A prefix-only value fixes the launch-time key only
            # through the prefix's own LAST '=' - the SECTION at most, never the
            # alias NAME: the key is either exactly the prefix up to that '=' (a
            # tail without '=') or runs past the WHOLE prefix (a tail with one), so
            # it names a pin key only when that part IS one (`gc.auto=0=` + tail
            # names `gc.auto=0` or longer, never `gc.auto`). An alias.* head ends
            # the parse AS the alias finding (the defined name is unreadable, so
            # any later word can invoke it), a pin-key head records the unreadable
            # sentinel exactly like the resolved pin-key case below, and any other
            # head is passed over.
            if prefix_only:
                key = value.rpartition("=")[0].lower()
                if key.startswith("alias."):
                    return "alias", None
                if key in _SCAN_PIN_VALUES:
                    pin_state[key] = _SCAN_CONFIG_ENV_VALUE
                index += step
                continue
            key = value.rpartition("=")[0].lower()
            if key in _SCAN_PIN_VALUES:
                # The effective value is an environment VARIABLE read at launch time,
                # unreadable here. git applies -c and --config-env in COMMAND-LINE order
                # (last value wins, verified on git 2.53), so this slot decides the key
                # only when it is the key's LAST option-position setting: record the
                # unreadable sentinel and keep parsing - a LATER `-c` re-pin restores
                # the pin, and a key left on the sentinel fails _verdict as stomped.
                pin_state[key] = _SCAN_CONFIG_ENV_VALUE
                index += step
                continue
            if key.startswith("alias."):
                # An alias defined through --config-env: same continuation as -c above
                # (the alias NAME is read from the key - everything before the LAST
                # '=' - and recorded exactly like -c; only its TARGET is unreadable,
                # which the alias verdict never needs).
                alias_seen = True
                alias_names.add(key[len("alias."):])
            index += step
            continue
        if token in _SCAN_GIT_OPTION_ARG:
            # Consumes exactly the following slot (its value may stay opaque: one slot).
            if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                # A dangling or unknown-length option-value slot ends the parse;
                # with the argv (possibly) alias-defining, the ending is the alias
                # finding, exactly like the unresolved-slot ending above.
                return ("alias" if alias_defined else "opaque"), None
            index += 2
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        if token.lower() in alias_names:
            # This word INVOKES an alias defined in this same argv: the alias value
            # is a new command line this parse cannot read, and it can defeat every
            # argv and environment pin (docstring above) - never 'pinned'.
            return "alias", None
        state = _verdict(token)
        if state is not None:
            return state
        if alias_seen:
            # The pins are not effective and an alias definition precedes this word: it
            # may be remapped to ANY subcommand, so it is never trusted as a harmless
            # one - coverage-gated opaque, exactly like an unresolved subcommand slot.
            return "opaque", None
        return "subcommand", token
    return "end", None


def _scan_direct_calls(scope_body, wanted):
    """True when one of <wanted> is actually CALLED (a bare name or an attribute) among
    this scope's OWN statements; nested function and class bodies are other scopes and are
    skipped. A merely-imported or merely-referenced scrub name proves nothing about the
    launch environment, so it covers nothing."""
    stack = list(scope_body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in wanted:
                return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _scan_scope_coverage(tree, wanted):
    """(module-scope covered, covered function names) for calls of <wanted>: a scrub or
    lifecycle call covers exactly the SCOPE that makes it, never the whole module, so a
    self-test's scrub call cannot silently cover a production launch elsewhere in the same
    file. A function NAME is covered when EVERY same-module definition of that bare name
    calls <wanted> in its own scope (coverage is keyed by bare name, so two unrelated
    same-named methods share one verdict: one definition's scrub never covers the
    other's launch - all must scrub, fail-closed), or when every visible
    same-module call site of its name sits in a covered scope (a least fixpoint, so a
    function with NO visible call site - an entry point, an exported or dynamically
    dispatched callback - is never covered by other scopes' calls). Definitions and call
    sites are matched by bare name (instantiating a same-module class counts as a call
    site of __init__, since construction RUNS it): the same syntactic bounds as the rest
    of this scan, fail-closed on anything it cannot see."""
    defs = _scan_function_defs(tree)
    class_inits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == "__init__" for stmt in node.body):
            class_inits.add(node.name)
    module_covered = _scan_direct_calls(tree.body, wanted)
    covered = set(name for name, nodes in defs.items()
                  if all(_scan_direct_calls(node.body, wanted) for node in nodes))
    sites = dict()
    stack = [(tree, ())]
    while stack:
        node, chain = stack.pop()
        for child in ast.iter_child_nodes(node):
            child_chain = chain
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_chain = chain + (child.name,)
            if isinstance(child, ast.Call):
                func = child.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if name in defs:
                    sites.setdefault(name, []).append(chain)
                if name in class_inits:
                    sites.setdefault("__init__", []).append(chain)
            stack.append((child, child_chain))
    changed = True
    while changed:
        changed = False
        for name in defs:
            if name in covered:
                continue
            chains = sites.get(name)
            if not chains:
                continue
            if all((module_covered if not chain
                    else any(part in covered for part in chain))
                   for chain in chains):
                covered.add(name)
                changed = True
    return module_covered, frozenset(covered)


def _scan_kw_stomps_pins(keywords):
    """True when a call's keyword overrides could rewrite an F-367 pin variable: an explicit
    GIT_CONFIG_COUNT/KEY_n/VALUE_n keyword (git_fixture_env(GIT_CONFIG_COUNT="0") and
    dict(os.environ, GIT_CONFIG_COUNT="0") each disable EVERY env pin at the launch,
    verified on git 2.53), an explicit GIT_CONFIG_PARAMETERS keyword (read by git with
    command scope AFTER the COUNT/KEY_n/VALUE_n family, so it re-enables maintenance OVER
    the injected pins, verified on git 2.53), or a **-expansion this scan cannot read."""
    for keyword in keywords:
        if keyword.arg is None or keyword.arg in _SCAN_PIN_ENV_VARS:
            return True
    return False


def _scan_env_covered(expr, func_node, module_consts, func_defs, scope_scrubbed, depth,
                      launch=None):
    """True when the env= expression provably derives from a pin-carrying source: a
    git_fixture_env(...) call (directly, through dict()/copy() derivation, a same-scope name,
    or one level of same-module function returns), or os.environ at a launch whose SCOPE is
    covered by a call of the in-place scrub or the lifecycle wrapper (both leave the pins
    set in os.environ; a self-test's scrub call elsewhere in the file covers nothing here) -
    in every case only when no keyword override on the way rewrites a pin variable
    (_scan_kw_stomps_pins): git_fixture_env(GIT_CONFIG_COUNT="0"),
    dict(os.environ, GIT_CONFIG_COUNT="0") and any GIT_CONFIG_PARAMETERS override carry NO
    effective pins. An env NAME counts only while its resolution is intact: an in-place
    mutation (env.update / env.setdefault / a subscript write / a del), ANY splice growth
    or augmented assignment (env |= {...} rewrites entries wholesale), an alias binding,
    a rebinding through a walrus target, a for-statement target, a `with ... as` target,
    or an `except ... as` name,
    or - via <launch>, the launch point's (lineno, col_offset) - any binding of the name
    at or after the launch invalidates coverage outright (this scan does not order
    control flow or read mutation arguments, so a mutated derivation is never trusted,
    fail-closed). <launch> bounds the launch's OWN scope only: it does not follow the
    one-level function-return resolution into another scope's line numbers. Two
    DISCLOSED residuals bound this reading (review posture, not mechanics): a mutator
    METHOD outside _scan_local_assigns' enumerated set is not read as mutation
    (dict.popitem, or an in-place dunder called as a method: env.__ior__(...)), and a
    function PARAMETER is not read as a binding, so an env parameter that shares its
    name with a module-level variable resolves through the MODULE value while Python
    passes the caller's argument."""
    if expr is None or depth <= 0:
        return False
    if isinstance(expr, ast.Call):
        callee = expr.func
        callee_name = None
        if isinstance(callee, ast.Name):
            callee_name = callee.id
        elif isinstance(callee, ast.Attribute):
            callee_name = callee.attr
        if callee_name in _SCAN_COVERED_ENV_CALLS:
            return not _scan_kw_stomps_pins(expr.keywords)
        if callee_name == "dict" and expr.args:
            if _scan_kw_stomps_pins(expr.keywords):
                return False
            return _scan_env_covered(expr.args[0], func_node, module_consts, func_defs,
                                     scope_scrubbed, depth - 1, launch)
        if callee_name == "copy" and isinstance(callee, ast.Attribute):
            return _scan_env_covered(callee.value, func_node, module_consts, func_defs,
                                     scope_scrubbed, depth - 1, launch)
        returns = []
        for definition in func_defs.get(callee_name, []):
            for node in ast.walk(definition):
                if isinstance(node, ast.Return) and node.value is not None:
                    returns.append((node.value, definition))
        if returns and all(
                _scan_env_covered(value, definition, module_consts, func_defs,
                                  scope_scrubbed, depth - 1)
                for value, definition in returns):
            return True
        return False
    if isinstance(expr, ast.Name):
        plain, extend, mutated = _scan_local_assigns(func_node, expr.id, launch)
        if mutated or extend:
            return False
        if not plain and expr.id in module_consts:
            plain = [module_consts[expr.id]]
        return bool(plain) and all(
            _scan_env_covered(value, func_node, module_consts, func_defs,
                              scope_scrubbed, depth - 1, launch) for value in plain)
    if isinstance(expr, ast.Attribute):
        return (expr.attr == "environ" and isinstance(expr.value, ast.Name)
                and expr.value.id == "os" and scope_scrubbed)
    if isinstance(expr, ast.IfExp):
        return all(_scan_env_covered(branch, func_node, module_consts, func_defs,
                                     scope_scrubbed, depth - 1, launch)
                   for branch in (expr.body, expr.orelse))
    return False


def _scan_launches(tree):
    """(node, flavor, launcher, dotted qualname, qualname parts, enclosing function node,
    innermost enclosing class node) for every launch-shaped or tripwire-shaped node in the
    module: direct
    subprocess.<launcher>(...) calls (flavor 'subprocess'), os-level launcher calls by any
    object spelling (flavor 'os', pty.spawn included), asyncio create_subprocess_* calls by
    any object spelling (flavor 'async'), and dynamic launcher access (flavor 'dynamic'):
    exec/eval anywhere, getattr(subprocess, ...) / getattr(os, ...) /
    getattr(asyncio, ...) except a literal
    NON-launcher attribute (getattr(os, "O_NOFOLLOW", 0) reads a constant, not a launcher),
    subprocess.__dict__ / os.__dict__ / asyncio.__dict__, and vars(subprocess) / vars(os) /
    vars(asyncio)."""
    found = []
    stack = [(tree, (), None, None)]
    while stack:
        node, parts, func_node, class_node = stack.pop()
        for child in ast.iter_child_nodes(node):
            child_parts, child_func, child_class = parts, func_node, class_node
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_parts, child_func = parts + (child.name,), child
            elif isinstance(child, ast.ClassDef):
                child_parts, child_class = parts + (child.name,), child
            if isinstance(child, ast.Call):
                qualname = ".".join(parts) if parts else "<module>"
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr in _SCAN_LAUNCH_NAMES \
                        and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                    found.append((child, "subprocess", func.attr, qualname, parts,
                                  func_node, class_node))
                else:
                    name = func.attr if isinstance(func, ast.Attribute) else (
                        func.id if isinstance(func, ast.Name) else None)
                    if name in _SCAN_OS_LAUNCH_NAMES:
                        found.append((child, "os", name, qualname, parts, func_node,
                                      class_node))
                    elif name in _SCAN_ASYNC_LAUNCH_NAMES:
                        found.append((child, "async", name, qualname, parts, func_node,
                                      class_node))
                    elif isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                        found.append((child, "dynamic", func.id, qualname, parts,
                                      func_node, class_node))
                    elif isinstance(func, ast.Name) and func.id == "getattr" \
                            and child.args and isinstance(child.args[0], ast.Name) \
                            and child.args[0].id in ("subprocess", "os", "asyncio"):
                        module = child.args[0].id
                        launchers = (_SCAN_LAUNCH_NAMES if module == "subprocess"
                                     else (_SCAN_ASYNC_LAUNCH_NAMES if module == "asyncio"
                                           else _SCAN_OS_LAUNCH_NAMES))
                        harmless = (len(child.args) >= 2
                                    and isinstance(child.args[1], ast.Constant)
                                    and isinstance(child.args[1].value, str)
                                    and child.args[1].value not in launchers)
                        if not harmless:
                            found.append((child, "dynamic", "getattr on " + module,
                                          qualname, parts, func_node, class_node))
                    elif isinstance(func, ast.Name) and func.id == "vars" \
                            and child.args and isinstance(child.args[0], ast.Name) \
                            and child.args[0].id in ("subprocess", "os", "asyncio"):
                        found.append((child, "dynamic", "vars(" + child.args[0].id + ")",
                                      qualname, parts, func_node, class_node))
            elif isinstance(child, ast.Attribute) and child.attr == "__dict__" \
                    and isinstance(child.value, ast.Name) \
                    and child.value.id in ("subprocess", "os", "asyncio"):
                qualname = ".".join(parts) if parts else "<module>"
                found.append((child, "dynamic", child.value.id + ".__dict__", qualname,
                              parts, func_node, class_node))
            stack.append((child, child_parts, child_func, child_class))
    return found


def _scan_env_defeats_wrapper(expr, func_node, module_consts, depth):
    """True when a hand-rolled env= provably drops or replaces the lifecycle wrapper's PATH,
    so the wrapper rule below must not cover the launch: it hand-sets its own "PATH" entry (a
    dict literal key or a dict()/call keyword named "PATH": the wrapper directory is gone from
    the front of the search path), or it is a fully LITERAL environment that carries no PATH
    and no inherited-environment expansion at all (env=dict(HOME=...): git then resolves
    through the libc default path, skipping the wrapper directory entirely, and every
    process-wide pin variable is dropped with the rest of os.environ, verified). An env the
    resolver cannot read stays wrapper-covered: the disclosed residual."""
    if expr is None or expr is _SCAN_OPEN or depth <= 0:
        return False
    if isinstance(expr, ast.Name):
        plain, _, mutated = _scan_local_assigns(func_node, expr.id)
        if mutated:
            return False
        if not plain and expr.id in module_consts:
            plain = [module_consts[expr.id]]
        return any(_scan_env_defeats_wrapper(value, func_node, module_consts, depth - 1)
                   for value in plain)
    if isinstance(expr, ast.Dict):
        expanded = False
        for key in expr.keys:
            if key is None:
                expanded = True
            elif isinstance(key, ast.Constant) and key.value == "PATH":
                return True
        return not expanded
    if isinstance(expr, ast.Call):
        callee = expr.func
        callee_name = callee.attr if isinstance(callee, ast.Attribute) else (
            callee.id if isinstance(callee, ast.Name) else None)
        for keyword in expr.keywords:
            if keyword.arg == "PATH":
                return True
        if callee_name == "dict":
            if any(keyword.arg is None for keyword in expr.keywords):
                return False
            if expr.args:
                return _scan_env_defeats_wrapper(expr.args[0], func_node, module_consts,
                                                 depth - 1)
            return True
        if callee_name == "copy" and isinstance(callee, ast.Attribute):
            return _scan_env_defeats_wrapper(callee.value, func_node, module_consts,
                                             depth - 1)
        return False
    return False


def _maintenance_pin_scan(root, allow_missing_files=False, planted_entries=()):
    """The sorted findings of the repo-wide F-367 tripwire scan under <root>: [] means every
    launch under _SCAN_DIRS this scan can resolve to a maintenance-triggering git run is
    effectively pinned or covered, every launch form it CANNOT resolve is justified for
    exactly its launch KIND, and no justification is stale. Unreadable or unparseable sources
    are loud findings, never silent passes. Stale allowlist enforcement covers unused entries
    for scanned files AND entries whose file is missing (deleted or renamed);
    allow_missing_files=True relaxes ONLY the latter, for planted synthetic trees that do not
    carry the real allowlisted files. The real-tree green leg runs strict. planted_entries adds
    allowlist entries for a planted contract tree only."""
    findings = []
    allowed = dict()
    pinned = dict()
    for entry in _SCAN_ALLOWED_UNPINNED + tuple(planted_entries):
        rel, qualname, kinds = entry[:3]
        allowed[(rel, qualname)] = frozenset(kinds)
        if "dynamic" in kinds:
            # One rule for every dynamic entry: it pins its exact dynamic-site count.
            if len(entry) == 5 and type(entry[4]) is int and entry[4] >= 1:
                pinned[(rel, qualname)] = entry[4]
            else:
                findings.append("%s %s: _SCAN_ALLOWED_UNPINNED dynamic entry without its"
                                " dynamic-site count (a fifth field, an int >= 1)"
                                % (rel, qualname))
    used, scanned, scanned_bases = set(), set(), []
    dynamic_sites = dict()

    def absorbed(key, kind):
        # A justification covers ONLY the launch kinds its entry declares; a kind-mismatched
        # launch falls through to its loud finding (and the unused entry then reads stale).
        if kind in allowed.get(key, frozenset()):
            used.add(key)
            return True
        return False

    for base in _SCAN_DIRS:
        basedir = root / base
        if not basedir.is_dir():
            findings.append("%s: scan directory missing under %s" % (base, root))
            continue
        scanned_bases.append(base)
        for path in sorted(basedir.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            scanned.add(rel)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
                findings.append("%s: unreadable or unparseable: %s" % (rel, exc))
                continue
            findings.extend(_scan_alias_findings(rel, tree))
            module_consts = _scan_module_consts(tree)
            func_defs = _scan_function_defs(tree)
            scrub_module, scrub_covered = _scan_scope_coverage(tree, _SCAN_SCRUB_NAMES)
            life_module, life_covered = _scan_scope_coverage(
                tree, frozenset(("fixture_git_lifecycle",)))
            class_consts = dict()
            for call, flavor, launcher, qualname, parts, func_node, class_node \
                    in _scan_launches(tree):
                consts = module_consts
                if class_node is not None:
                    if id(class_node) not in class_consts:
                        merged = dict(module_consts)
                        for stmt in class_node.body:
                            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                                    and isinstance(stmt.targets[0], ast.Name):
                                merged["self." + stmt.targets[0].id] = stmt.value
                                merged["cls." + stmt.targets[0].id] = stmt.value
                        class_consts[id(class_node)] = merged
                    consts = class_consts[id(class_node)]
                key = (rel, qualname)
                # A module-scope launch resolves through the module scope itself, so
                # module-level argv mutation is tracked exactly like function-scope
                # mutation; the launch point bounds every resolution (a pin appended
                # after the launch is never credited to it); and scrub/lifecycle
                # coverage is per SCOPE, never smeared over the whole module.
                scope_node = func_node if func_node is not None else tree
                launch_at = (call.lineno, call.col_offset)
                scope_scrubbed = scrub_module or any(
                    part in scrub_covered for part in parts)
                scope_lifecycle = life_module or any(
                    part in life_covered for part in parts)
                if flavor == "os":
                    if not absorbed(key, "os"):
                        findings.append(
                            "%s:%d %s: os-level process launch (%s) is outside this scan's"
                            " contract (cannot evaluate); use a direct subprocess list-argv"
                            " launch or justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname, launcher))
                    continue
                if flavor == "async":
                    if not absorbed(key, "async"):
                        findings.append(
                            "%s:%d %s: asyncio launcher (%s) is outside this scan's contract"
                            " (cannot evaluate); use a direct subprocess list-argv launch or"
                            " justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname, launcher))
                    continue
                if flavor == "dynamic":
                    dynamic_sites.setdefault(key, []).append(call.lineno)
                    if not absorbed(key, "dynamic"):
                        findings.append(
                            "%s:%d %s: dynamic launcher access (%s) defeats the"
                            " maintenance-pin tripwire (cannot evaluate); spell launches as"
                            " direct module-qualified list-argv calls or justify it in"
                            " _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname, launcher))
                    continue
                shell_mode = launcher in _SCAN_SHELL_LAUNCH_NAMES
                expanded_kw = False
                executable_kw = False
                for keyword in call.keywords:
                    if keyword.arg == "shell" and not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is False):
                        shell_mode = True
                    if keyword.arg is None:
                        expanded_kw = True
                    if keyword.arg == "executable":
                        executable_kw = True
                if shell_mode:
                    if not absorbed(key, "shell"):
                        findings.append(
                            "%s:%d %s: shell-string launch (%s) is outside this scan's"
                            " contract (cannot evaluate the effective argv); use a direct"
                            " list-argv launch or justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname, launcher))
                    continue
                if expanded_kw:
                    if not absorbed(key, "unresolved"):
                        findings.append(
                            "%s:%d %s: launch with **-expanded keywords (cannot evaluate"
                            " shell=, env=, or executable=); pass the keywords literally or"
                            " justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname))
                    continue
                if executable_kw:
                    if not absorbed(key, "unresolved"):
                        findings.append(
                            "%s:%d %s: launch whose executable= keyword overrides the"
                            " program behind the argv head (cannot evaluate); drop the"
                            " override or justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname))
                    continue
                argv = call.args[0] if call.args else None
                if argv is None:
                    for keyword in call.keywords:
                        if keyword.arg == "args":
                            argv = keyword.value
                if argv is None:
                    if not absorbed(key, "unresolved"):
                        findings.append(
                            "%s:%d %s: launch without a visible argv (cannot evaluate); pass"
                            " the argv positionally or as args=, or justify it in"
                            " _SCAN_ALLOWED_UNPINNED" % (rel, call.lineno, qualname))
                    continue
                entries, _opened = _scan_flatten(argv, scope_node, consts,
                                                 _SCAN_RESOLVE_DEPTH, launch_at)
                head = _scan_head_kind(entries[0] if entries else None, scope_node,
                                       consts, _SCAN_RESOLVE_DEPTH, launch_at)
                if head == "non-git":
                    continue
                if head == "unknown":
                    if not absorbed(key, "unresolved"):
                        findings.append(
                            "%s:%d %s: launch whose argv head this scan cannot resolve"
                            " (cannot evaluate whether it is git); resolve the head to a"
                            " literal or justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname))
                    continue
                status, subcommand = _scan_git_argv_state(entries, scope_node, consts,
                                                          launch_at)
                if status == "pinned":
                    continue
                if status in ("subcommand", "stomped") and subcommand is not None \
                        and subcommand not in _SCAN_TRIGGERS:
                    continue
                if status == "end":
                    continue
                if status == "stomped":
                    # git's last value wins and -c/--config-env outrank every
                    # environment-scope pin, so NO covered env, called scrub, or lifecycle
                    # wrapper below can protect a launch that re-sets a pin key.
                    if not absorbed(key, "git-triggering"):
                        findings.append(
                            "%s:%d %s: git launch overrides an F-367 pin key in option"
                            " position (git's last value wins, and -c/--config-env outrank"
                            " every environment pin, so no scrub or covered env protects"
                            " it); remove the override or justify it in"
                            " _SCAN_ALLOWED_UNPINNED" % (rel, call.lineno, qualname))
                    continue
                if status == "alias":
                    # The argv defines an alias.* - or carries a `-c`/`--config-env`
                    # value slot whose config KEY this scan cannot read, which can
                    # define one - and the parse reaches a word that
                    # can invoke it, or ends at an unresolved region that can spell
                    # it: the alias expansion is a command line this scan cannot
                    # read, a non-shell alias's `-c` pairs apply after the outer
                    # options with last-value-wins, and a `!` alias runs an
                    # arbitrary command line that inherits the propagated command
                    # scope (GIT_CONFIG_PARAMETERS) yet can drop or override it
                    # (git 2.53), so no argv pin, environment pin, scrub, or
                    # covered env is trusted to protect the launch.
                    if not absorbed(key, "git"):
                        findings.append(
                            "%s:%d %s: git launch that defines an alias.* in its own"
                            " argv (or carries an option value that can define one: a"
                            " -c/--config-env value slot whose config key this scan"
                            " cannot read) and reaches a word or unresolved region"
                            " that can invoke it (cannot evaluate the alias expansion,"
                            " which can defeat every argv and environment pin); drop"
                            " the same-argv alias, resolve the value's config key, or"
                            " justify it in _SCAN_ALLOWED_UNPINNED"
                            % (rel, call.lineno, qualname))
                    continue
                # Remaining: a maintenance-triggering subcommand, or a git argv this scan
                # cannot resolve past the option region. Both are acceptable only under a
                # pin-carrying environment, a CALLED scrub, the lifecycle wrapper, or a
                # kind-matched justification.
                env_expr, has_env = None, False
                for keyword in call.keywords:
                    if keyword.arg == "env":
                        has_env = True
                        env_expr = keyword.value
                if has_env and isinstance(env_expr, ast.Constant) and env_expr.value is None:
                    has_env = False
                if has_env and _scan_env_covered(env_expr, scope_node, consts,
                                                 func_defs, scope_scrubbed,
                                                 _SCAN_RESOLVE_DEPTH, launch_at):
                    continue
                if not has_env and scope_scrubbed:
                    continue
                if absorbed(key, "git-triggering" if status == "subcommand" else "git"):
                    continue
                if head == "bare-git" and scope_lifecycle and not (
                        has_env and _scan_env_defeats_wrapper(env_expr, scope_node,
                                                              consts,
                                                              _SCAN_RESOLVE_DEPTH)):
                    continue
                if status == "subcommand":
                    findings.append(
                        "%s:%d %s: maintenance-triggering git launch (subcommand %r) without"
                        " the effective F-367 pins; pin the argv with the three `-c"
                        " key=value` pairs in option position, route it through a covered env"
                        " or a called scrub, or justify it in _SCAN_ALLOWED_UNPINNED"
                        % (rel, call.lineno, qualname, subcommand))
                else:
                    findings.append(
                        "%s:%d %s: git launch whose subcommand this scan cannot resolve"
                        " (cannot evaluate; no effective F-367 pins in option position);"
                        " resolve or pin the argv, route it through a covered env or a called"
                        " scrub, or justify it in _SCAN_ALLOWED_UNPINNED"
                        % (rel, call.lineno, qualname))
    for key, count in sorted(pinned.items()):
        lines = dynamic_sites.get(key, [])
        if lines and len(lines) != count:
            findings.append("%s %s: %d dynamic sites (lines %s), but its _SCAN_ALLOWED_UNPINNED"
                            " entry pins %d; a site beyond the pin is not covered by the"
                            " audited justification (justify it and raise the pin, or remove"
                            " it), and a pin above the count is stale"
                            % (key[0], key[1], len(lines),
                               ",".join(str(line) for line in sorted(lines)), count))
    for rel, qualname in sorted(allowed):
        if rel in scanned:
            if (rel, qualname) not in used:
                findings.append("%s %s: stale _SCAN_ALLOWED_UNPINNED entry (no matching"
                                " cannot-evaluate or unpinned launch); remove it so the"
                                " justification cannot rot" % (rel, qualname))
        elif not allow_missing_files and any(
                rel.startswith(base + "/") for base in scanned_bases):
            findings.append("%s %s: stale _SCAN_ALLOWED_UNPINNED entry (allowlisted file"
                            " missing: deleted or renamed); remove or retarget it so the"
                            " justification cannot rot" % (rel, qualname))
    return sorted(findings)


# Each entry: (case id, planted files as (relative path, source) pairs, expectation), and
# optionally a fourth field, allowlist entries planted beside _SCAN_ALLOWED_UNPINNED. An
# expectation of None demands a CLEAN scan (a harmless form review showed being wrongly
# flagged, or a protected form the round-3 parser fixes stopped rejecting); a string demands at
# least one finding containing it (an evasion form round-2 or round-3 review showed being
# silently accepted). The stale-missing case runs under the strict allowlist file-existence
# enforcement; every other planted tree relaxes only that (its tree deliberately carries none
# of the real allowlisted files).
_SCAN_CONTRACT_CASES = (
    ("os-system",
     (("tools/planted.py", "\n".join((
         "import os", "", "",
         "def _seed():",
         "    os.system('git commit --allow-empty -m x')", ""))),),
     "os-level process launch"),
    ("os-execvp",
     (("tools/planted.py", "\n".join((
         "import os", "", "",
         "def _seed():",
         "    os.execvp('git', ['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "os-level process launch"),
    ("shell-true",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run('git commit --allow-empty -m x', shell=True)", ""))),),
     "shell-string launch"),
    ("getoutput",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.getoutput('git commit --allow-empty -m x')", ""))),),
     "shell-string launch"),
    ("helper-argv",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _argv():",
         "    return ['git', 'commit', '--allow-empty', '-m', 'x']", "", "",
         "def _seed():",
         "    subprocess.run(_argv())", ""))),),
     "cannot resolve"),
    ("helper-argv-imported",
     (("tools/planted_helper.py", "\n".join((
         "def commit_argv():",
         "    return ['git', 'commit', '--allow-empty', '-m', 'x']", ""))),
      ("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from planted_helper import commit_argv", "", "",
         "def _seed():",
         "    subprocess.run(commit_argv())", ""))),),
     "cannot resolve"),
    ("param-subcommand",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(subcommand):",
         "    subprocess.run(['git', subcommand, '--allow-empty', '-m', 'x'])", ""))),),
     "cannot resolve"),
    ("pins-in-messages",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'gc.auto=0',",
         "                    '-m', 'gc.autoDetach=false', '-m', 'maintenance.auto=false'])",
         ""))),),
     "maintenance-triggering git launch"),
    ("empty-pin-splice",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "NO_AUTO_MAINTENANCE = []", "", "",
         "def _seed(repo):",
         "    subprocess.run(['git', '-C', str(repo)] + NO_AUTO_MAINTENANCE",
         "                   + ['commit', '-q', '-m', 'x'])", ""))),),
     "maintenance-triggering git launch"),
    ("scrub-imported-not-called",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "def _seed():",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "maintenance-triggering git launch"),
    ("alias-assign",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "sp = subprocess", "", "",
         "def _seed():",
         "    sp.run(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "aliasing subprocess"),
    ("launcher-from-import",
     (("tools/planted.py", "\n".join((
         "from subprocess import run", "", "",
         "def _seed():",
         "    run(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "from-import"),
    ("read-only-operand",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _probe():",
         "    subprocess.run(['git', 'show', 'commit'])", ""))),),
     None),
    ("harmless-import",
     (("tools/planted.py", "\n".join((
         "from subprocess import PIPE", "",
         "print(PIPE)", ""))),),
     None),
    ("pins-module-splice",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "NO_AUTO_MAINTENANCE = ['-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                       '-c', 'maintenance.auto=false']", "", "",
         "def _seed(repo):",
         "    subprocess.run(['git', '-C', str(repo)] + NO_AUTO_MAINTENANCE",
         "                   + ['commit', '-q', '-m', 'x'])", ""))),),
     None),
    ("scrub-called",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "def main():",
         "    scrub_git_environment()",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     None),
    ("stale-missing",
     (("tools/placeholder.py", "pass\n"),),
     "allowlisted file missing"),
    ("stale-unused",
     (("tools/aiqt_corpus.py", "pass\n"),),
     "no matching"),
    # A dynamic entry pins its function's dynamic-site count: a second exec planted into an
    # allowlisted function (here tools/pin.py _recover_close_vectors, pinned at 1) is a finding,
    # the same function holding exactly its pinned site is clean, and the entry with no site
    # left still reads stale.
    ("dynamic-pin-extra-exec",
     (("tools/pin.py", "\n".join((
         "def _recover_close_vectors(source, ns):",
         "    exec(compile(source, 'pin.py', 'exec'), ns)",
         "    exec(\"import os; os.system('git gc')\")", ""))),),
     "2 dynamic sites"),
    ("dynamic-pin-exact",
     (("tools/pin.py", "\n".join((
         "def _recover_close_vectors(source, ns):",
         "    exec(compile(source, 'pin.py', 'exec'), ns)", ""))),),
     None),
    ("dynamic-pin-stale",
     (("tools/pin.py", "\n".join((
         "def _recover_close_vectors(source, ns):",
         "    return compile(source, 'pin.py', 'exec')", ""))),),
     "no matching"),
    # A dynamic entry with no fifth field (its site count) is itself a finding, even when the
    # function it justifies holds exactly one site.
    ("dynamic-pin-missing-count",
     (("tools/planted.py", "\n".join((
         "def _seed(source, ns):",
         "    exec(compile(source, 'planted.py', 'exec'), ns)", ""))),),
     "dynamic entry without its dynamic-site count",
     (("tools/planted.py", "_seed", ("dynamic",), "planted: an exec with no pinned count"),)),
    # Round-3 forms: git's LAST-VALUE-WINS config semantics, dynamic launcher access, and the
    # launch keywords, environments, and mutations round-3 review showed silently accepted
    # (each red without the round-3 parser and tripwire changes).
    ("dup-key-reenable",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false', '-c', 'gc.auto=1',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "overrides an F-367 pin key"),
    ("config-env-reenable",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env=maintenance.auto=MAINT_OVERRIDE',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "overrides an F-367 pin key"),
    ("argv-stomp-covered-env",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'maintenance.auto=true',",
         "                    'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env())", ""))),),
     "overrides an F-367 pin key"),
    ("fixture-env-pin-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env(GIT_CONFIG_COUNT='0'))", ""))),),
     "maintenance-triggering git launch"),
    ("environ-derivation-stomp",
     (("tools/planted.py", "\n".join((
         "import os", "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "def main():",
         "    scrub_git_environment()",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=dict(os.environ, GIT_CONFIG_COUNT='0'))", ""))),),
     "maintenance-triggering git launch"),
    ("argv-mutated-in-place",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "            '-c', 'maintenance.auto=false', 'commit', '--allow-empty', '-m', 'x']",
         "    args[6] = 'maintenance.auto=true'",
         "    subprocess.run(args)", ""))),),
     "cannot resolve"),
    ("extend-order-pinned",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git']",
         "    args.extend(['-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                 '-c', 'maintenance.auto=false'])",
         "    args.extend(['commit', '--allow-empty', '-m', 'x'])",
         "    subprocess.run(args)", ""))),),
     None),
    ("getattr-launcher",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    getattr(subprocess, 'run')(['git', 'commit', '--allow-empty', '-m', 'x'])",
         ""))),),
     "dynamic launcher access"),
    ("module-dict-launcher",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.__dict__['run'](['git', 'commit', '--allow-empty', '-m', 'x'])",
         ""))),),
     "dynamic launcher access"),
    ("vars-launcher",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    vars(subprocess)['run'](['git', 'commit', '--allow-empty', '-m', 'x'])",
         ""))),),
     "dynamic launcher access"),
    ("exec-form",
     (("tools/planted.py", "\n".join((
         "def _seed():",
         "    exec(\"import subprocess; subprocess.run(['git', 'commit', '-m', 'x'])\")",
         ""))),),
     "dynamic launcher access (exec)"),
    ("eval-form",
     (("tools/planted.py", "\n".join((
         "def _seed():",
         "    eval(\"__import__('subprocess').run(['git', 'commit', '-m', 'x'])\")", ""))),),
     "dynamic launcher access (eval)"),
    ("getattr-harmless-constant",
     (("tools/planted.py", "\n".join((
         "import os", "",
         "_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)", "",
         "print(_O_NOFOLLOW)", ""))),),
     None),
    ("async-exec",
     (("tools/planted.py", "\n".join((
         "import asyncio", "", "",
         "async def _seed():",
         "    proc = await asyncio.create_subprocess_exec(",
         "        'git', 'commit', '--allow-empty', '-m', 'x')",
         "    await proc.wait()", ""))),),
     "asyncio launcher"),
    ("async-shell",
     (("tools/planted.py", "\n".join((
         "import asyncio", "", "",
         "async def _seed():",
         "    proc = await asyncio.create_subprocess_shell('git commit --allow-empty -m x')",
         "    await proc.wait()", ""))),),
     "asyncio launcher"),
    ("executable-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['x', 'commit', '--allow-empty', '-m', 'x'],",
         "                   executable='/usr/bin/git')", ""))),),
     "executable= keyword overrides"),
    ("expanded-keywords",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "OPTS = dict(shell=True)", "", "",
         "def _seed():",
         "    subprocess.run(['git commit --allow-empty -m x'], **OPTS)", ""))),),
     "**-expanded keywords"),
    ("pty-spawn",
     (("tools/planted.py", "\n".join((
         "import pty", "", "",
         "def _seed():",
         "    pty.spawn(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "os-level process launch"),
    ("wrapper-env-omits-path",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import fixture_git_lifecycle", "", "",
         "def main():",
         "    with fixture_git_lifecycle():",
         "        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                       env=dict(HOME='/tmp'))", ""))),),
     "maintenance-triggering git launch"),
    ("allowlist-kind-mismatch",
     (("tools/aiqt_corpus.py", "\n".join((
         "import subprocess", "", "",
         "def git(*args):",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "maintenance-triggering git launch"),
    # Round-4 forms: the env-derivation, alias, module-scope, launch-point, scrub-scope and
    # --config-env order gaps round-4 review demonstrated (each red without the round-4
    # parser, resolver and coverage changes).
    ("fixture-env-parameters-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env(",
         "                       GIT_CONFIG_PARAMETERS='maintenance.auto=true'))", ""))),),
     "maintenance-triggering git launch"),
    ("alias-keeps-detected-stomp",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'maintenance.auto=true',",
         "                    '-c', 'alias.unrelated=status',",
         "                    'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env())", ""))),),
     "overrides an F-367 pin key"),
    ("module-argv-index-write",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "ARGS = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "        '-c', 'maintenance.auto=false', 'commit', '--allow-empty', '-m', 'x']",
         "ARGS[6] = 'maintenance.auto=true'", "",
         "subprocess.run(ARGS)", ""))),),
     "cannot resolve"),
    ("module-extend-reenable",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "ARGS = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "        '-c', 'maintenance.auto=false']",
         "ARGS.extend(['-c', 'maintenance.auto=true'])", "",
         "subprocess.run(ARGS + ['commit', '--allow-empty', '-m', 'x'])", ""))),),
     "overrides an F-367 pin key"),
    ("pins-appended-after-launch",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'maintenance.auto=true']",
         "    subprocess.run(args + ['commit', '--allow-empty', '-m', 'x'])",
         "    args.extend(['-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                 '-c', 'maintenance.auto=false'])", ""))),),
     "cannot resolve"),
    ("config-env-then-repin",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '--config-env=maintenance.auto=MAINT_OVERRIDE',",
         "                    '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     None),
    ("selftest-scrub-not-production",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "def production_read(repo):",
         "    subprocess.run(['git', '-C', str(repo), 'commit', '--allow-empty',",
         "                    '-m', 'x'])", "", "",
         "def self_test():",
         "    scrub_git_environment()", ""))),),
     "maintenance-triggering git launch"),
    # Round-5 forms: the env-mutation, alias-evaluation, cross-scope argv-mutation,
    # same-named-method scrub-coverage and dynamic-asyncio gaps round-5 review
    # demonstrated (each red without the round-5 resolver, parser and coverage changes,
    # except the two clean guards, which hold the fixes to no new false flags).
    ("fixture-env-update-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    env = git_fixture_env()",
         "    env.update(GIT_CONFIG_COUNT='0')",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=env)", ""))),),
     "maintenance-triggering git launch"),
    ("fixture-env-setdefault-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    env = git_fixture_env()",
         "    env.setdefault('GIT_CONFIG_PARAMETERS', 'maintenance.auto=true')",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=env)", ""))),),
     "maintenance-triggering git launch"),
    ("fixture-env-union-override",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    env = git_fixture_env()",
         "    env |= dict(GIT_CONFIG_COUNT='0')",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=env)", ""))),),
     "maintenance-triggering git launch"),
    ("env-rebound-after-launch",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(env):",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=env)",
         "    env = git_fixture_env()", ""))),),
     "maintenance-triggering git launch"),
    ("alias-before-adverse-pin",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'alias.unrelated=status',",
         "                    '-c', 'maintenance.auto=true',",
         "                    'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env())", ""))),),
     "overrides an F-367 pin key"),
    ("alias-then-full-repin",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'maintenance.auto=true',",
         "                    '-c', 'alias.unrelated=status',",
         "                    '-c', 'maintenance.auto=false', '-c', 'gc.auto=0',",
         "                    '-c', 'gc.autoDetach=false',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     None),
    ("module-argv-extended-elsewhere",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "ARGS = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "        '-c', 'maintenance.auto=false']", "", "",
         "def _poison():",
         "    ARGS.extend(['-c', 'maintenance.auto=true'])", "", "",
         "def _seed():",
         "    subprocess.run(ARGS + ['commit', '--allow-empty', '-m', 'x'])", ""))),),
     "cannot resolve"),
    ("module-argv-written-elsewhere",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "ARGS = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "        '-c', 'maintenance.auto=false', 'commit', '--allow-empty',",
         "        '-m', 'x']", "", "",
         "def _poison():",
         "    ARGS[6] = 'maintenance.auto=true'", "", "",
         "def _seed():",
         "    subprocess.run(ARGS)", ""))),),
     "cannot resolve"),
    ("argv-alias-mutation",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "            '-c', 'maintenance.auto=false', 'commit', '--allow-empty',",
         "            '-m', 'x']",
         "    alias = args",
         "    alias[6] = 'maintenance.auto=true'",
         "    subprocess.run(args)", ""))),),
     "cannot resolve"),
    ("argv-mult-augassign",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "            '-c', 'maintenance.auto=false']",
         "    args *= 0",
         "    args.extend(['git', 'commit', '--allow-empty', '-m', 'x'])",
         "    subprocess.run(args)", ""))),),
     "cannot resolve"),
    ("scrub-same-name-other-class",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "class Tests:",
         "    def seed(self):",
         "        scrub_git_environment()", "", "",
         "class Fixture:",
         "    def seed(self):",
         "        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'])",
         ""))),),
     "maintenance-triggering git launch"),
    ("getattr-asyncio-launcher",
     (("tools/planted.py", "\n".join((
         "import asyncio", "", "",
         "async def _seed():",
         "    proc = await getattr(asyncio, 'create_subprocess_exec')(",
         "        'git', 'commit', '--allow-empty', '-m', 'x')",
         "    await proc.wait()", ""))),),
     "dynamic launcher access"),
    ("getattr-asyncio-harmless",
     (("tools/planted.py", "\n".join((
         "import asyncio", "",
         "_HAS_RUNNER = getattr(asyncio, 'Runner', None)", "",
         "print(_HAS_RUNNER)", ""))),),
     None),
    # Round-6 forms: the same-argv alias remap and the bare-name rebinding gaps round-6
    # review demonstrated (each red without the round-6 parser and resolver changes).
    ("alias-remap-defeats-pins",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    'seed', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("alias-open-tail",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(word):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    word])", ""))),),
     "defines an alias.* in its own argv"),
    # Round-7 forms: the alias-defining argv gaps round-7 review demonstrated (the
    # first two red without the alias pre-pass and the unconditional
    # unresolved-region alias verdict; the third pins the DISCLOSED conservative
    # finding for an alias whose name matches a git built-in: git 2.53 ignores the
    # alias and runs the built-in, but the launch-time built-in set is unreadable
    # statically, so the conservative finding must not silently regress to clean).
    ("alias-open-tail-covered-env",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(word):",
         "    env = git_fixture_env()",
         "    subprocess.run(['git', '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    word], env=env)", ""))),),
     "defines an alias.* in its own argv"),
    ("alias-after-unresolved-slot",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(flag):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false', flag,",
         "                    '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    'seed', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("alias-builtin-name-conservative",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'alias.version=!exit 73', 'version'])", ""))),),
     "defines an alias.* in its own argv"),
    # Round-8 forms: the unreadable option-VALUE-slot gaps round-8 review demonstrated
    # (each red without the round-8 changes: the possibly-alias pre-pass marking, the
    # alias routing of every unresolved parse ending, and the config-KEY "key=" prefix
    # resolution; the readable-key clean guard holds the prefix resolution to no false
    # flag on the shipped `-c 'core.hooksPath=' + str(...)` idiom).
    ("alias-unreadable-config-value-covered-env",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(value):",
         "    env = git_fixture_env()",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    '-c', value, 'seed', '--allow-empty', '-m', 'x'],",
         "                   env=env)", ""))),),
     "defines an alias.* in its own argv"),
    ("alias-unreadable-config-env-value-scrubbed",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import scrub_git_environment", "", "",
         "def main(envvar):",
         "    scrub_git_environment()",
         "    subprocess.run(['git', '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    '--config-env', envvar, 'seed', '--allow-empty', '-m', 'x'])",
         ""))),),
     "defines an alias.* in its own argv"),
    ("alias-open-option-value-slot",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(rest):",
         "    subprocess.run(['git', '-c', 'alias.seed=-c maintenance.auto=true commit',",
         "                    '-C', *rest], env=git_fixture_env())", ""))),),
     "defines an alias.* in its own argv"),
    ("possibly-alias-unreadable-value-pinned-funnel",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(args, value):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false', *args,",
         "                    '-c', value])", ""))),),
     "defines an alias.* in its own argv"),
    ("possibly-alias-unreadable-value-covered-env",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(value):",
         "    subprocess.run(['git', '-c', value, 'x'], env=git_fixture_env())", ""))),),
     "defines an alias.* in its own argv"),
    ("unreadable-value-readable-key-pinned",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(root):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'core.hooksPath=' + str(root),",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     None),
    ("unreadable-value-repins-pin-key",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(level):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'maintenance.auto=' + str(level),",
         "                    'commit', '--allow-empty', '-m', 'x'],",
         "                   env=git_fixture_env())", ""))),),
     "overrides an F-367 pin key"),
    ("alias-name-readable-target-unreadable",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(target):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'alias.seed=' + target, 'seed'])", ""))),),
     "defines an alias.* in its own argv"),
    ("argv-walrus-rebind",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "            '-c', 'maintenance.auto=false', 'commit', '--allow-empty',",
         "            '-m', 'x']",
         "    (args := ['git', 'maintenance', 'run'])",
         "    subprocess.run(args)", ""))),),
     "cannot resolve"),
    ("argv-for-rebind",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    args = ['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "            '-c', 'maintenance.auto=false', 'commit', '--allow-empty',",
         "            '-m', 'x']",
         "    for args in [['git', 'maintenance', 'run']]:",
         "        pass",
         "    subprocess.run(args)", ""))),),
     "cannot resolve"),
    ("env-walrus-rebind",
     (("tools/planted.py", "\n".join((
         "import os", "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed():",
         "    env = git_fixture_env()",
         "    (env := dict(os.environ))",
         "    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                   env=env)", ""))),),
     "maintenance-triggering git launch"),
    ("env-with-rebind",
     (("tools/planted.py", "\n".join((
         "import subprocess", "",
         "from _git_fixture_env import git_fixture_env", "", "",
         "def _seed(handle):",
         "    env = git_fixture_env()",
         "    with handle as env:",
         "        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'x'],",
         "                       env=env)", ""))),),
     "maintenance-triggering git launch"),
    # Round-9 forms: the --config-env key-split, --attr-source value-slot and
    # str-prefix __radd__ gaps round-9 review demonstrated (each red without the
    # round-9 changes: git ends a --config-env key at the LAST '=', so the alias
    # named by a fully resolved value is read with rpartition and a prefix-only
    # value fixes the section at most, never the alias name; `--attr-source`
    # consumes the following tree slot; and a `+` value prefix is trusted only
    # when its right operand is a credited str form - an __radd__ on any other
    # right operand replaces the whole value).
    ("config-env-alias-subkey-unreadable",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env', 'alias.seed=' + var,",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-alias-section-str-prefix",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env', 'alias.seed=' + str(var),",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-alias-subkey-literal",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env', 'alias.seed=a.x=FOO',",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-alias-subkey-joined",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env=alias.seed=a.x=FOO',",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("attr-source-value-slot",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed():",
         "    subprocess.run(['git', '--attr-source', 'HEAD',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "maintenance-triggering git launch"),
    ("radd-value-replaces-str-prefix",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "class _K:",
         "    def __radd__(self, other):",
         "        return 'maintenance.auto=true'", "", "",
         "def _seed():",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '-c', 'core.hooksPath=' + _K(),",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    # Round-10 forms (each red on f879054a): a JOINED `--config-env=` token whose
    # tail is unreadable behind a readable `--config-env=` head read as one
    # unresolved slot there, so the parse ended pinned after the three pins; it now
    # reads through that head exactly like the separate form (an alias.* head, an
    # unreadable key, a pin-key head). And a prefix-only `--config-env` value whose
    # head fixes a non-alias section is read through the head's LAST '=':
    # `gc.auto=0=` + tail names the key `gc.auto=0` or a longer one, never the pin
    # key gc.auto, which f879054a failed as stomped.
    ("config-env-joined-alias-fstring",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    f'--config-env=alias.seed={var}',",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-joined-alias-concat",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env=alias.seed=' + str(var),",
         "                    'seed=a.x', '--allow-empty', '-m', 'x'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-joined-unreadable-key",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    f'--config-env={var}', 'seed'])", ""))),),
     "defines an alias.* in its own argv"),
    ("config-env-joined-pin-key-head",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    f'--config-env=gc.auto={var}',",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     "overrides an F-367 pin key"),
    ("config-env-prefix-nonalias-section",
     (("tools/planted.py", "\n".join((
         "import subprocess", "", "",
         "def _seed(var):",
         "    subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'gc.autoDetach=false',",
         "                    '-c', 'maintenance.auto=false',",
         "                    '--config-env', 'gc.auto=0=' + str(var),",
         "                    'commit', '--allow-empty', '-m', 'x'])", ""))),),
     None),
)


def _scan_contract_failures(base):
    """The failures of the layer-4 contract table over isolated planted trees: every launch
    form round-2 or round-3 review showed the scan silently accepting must be a loud finding,
    every harmless or correctly-protected form it showed wrongly flagged must stay clean, and
    the stale enforcement must fire for a deleted or renamed allowlisted file. [] is the
    passing value; a failing case reports its planted tree's actual findings, loudly, never a
    silent pass."""
    failures = []
    for case_id, files, expect, *planted in _SCAN_CONTRACT_CASES:
        root = base / case_id
        (root / "tools").mkdir(parents=True)
        (root / "opf" / "tools").mkdir(parents=True)
        for rel, source in files:
            (root / rel).write_text(source, encoding="utf-8")
        got = _maintenance_pin_scan(root,
                                    allow_missing_files=(case_id != "stale-missing"),
                                    planted_entries=planted[0] if planted else ())
        if expect is None:
            if got:
                failures.append("%s: expected a clean scan, got %r" % (case_id, got))
        elif not any(expect in finding for finding in got):
            failures.append("%s: expected a finding containing %r, got %r"
                            % (case_id, expect, got))
    return failures


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a
    # RuntimeError) on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def main(report_path=None, lanes=None):
    """The suite's entry: _suite_main (the lanes and the verdict) inside ONE top-level try whose
    finally ends every member group still registered (_finish_members, _close_members), on every
    exit: a return, a timeout, KeyboardInterrupt, SystemExit or any other exception. While it runs,
    the first SIGINT or SIGTERM raises and every later one is deferred, never raised
    (_close_on_signal, first raise then defer). THE FALLBACK CLOSE PROTECTS THE CLOSING STORE: the
    store closing = True is the first statement of a finally that is itself inside the try whose
    finally runs the second close, so a signal raising at that store (closing still clear, none
    raised yet) skips at most the first close, and the handler has then set closing itself; once
    closing is set or one signal has raised, no signal raises, so neither close can be cut short
    and the main thread never takes the registry lock while a handler could raise. (A try statement
    as the finally's first statement would not do under the sweep's line-event model: CPython 3.14
    leaves that line's NOP outside every protected range, so a raise delivered there escapes main()
    with both closes skipped.) The close handlers stay installed through BOTH closes and the
    caller's dispositions are restored ONCE, after the second (_restore_signal_handlers), even when
    the installation itself was interrupted. THE DEFERRED-SIGNAL PRECEDENCE (_raise_held): every
    signal received while closing is set or after one has raised is kept, in the order received,
    and none is dropped; after the restore, when any was deferred, _raise_held raises once the
    strongest of the run's signals, SIGTERM over SIGINT whatever the order (SystemExit 143 whenever
    a SIGTERM was received, else KeyboardInterrupt), and records it, so a SIGTERM a control's
    _reopen_members already raised still outranks a SIGINT held during the closes; when none was
    deferred, the exception already propagating stands. Only then is the exit code final
    (_final_verdict): problems the second close still meets, and any registry lock refusal of the
    run, make a clean run cannot-evaluate (exit 2), and the PASS line is printed only after the
    close. config/member-close-signal-sweep pins this by a sweep over two suite bodies (one
    returning 0, one driving the config pool's failed-member branch): a real SIGINT, and separately
    a SIGTERM, at every line event from the suite body through the restore (sys.settrace), at every
    bytecode instruction of that range (sys.monitoring), and a second signal at every line after
    each first (sys.monitoring, since CPython drops a trace function that raised), each run
    requiring a completed close, an empty registry, a free lock, the handlers restored in reverse
    order, nothing left deferred and exactly the outcome this precedence gives for the signals
    sent. config/member-close-handlers pins a SIGINT during the installation, a
    SIGTERM, the restored handlers and a real member closed under SIGINTs at the entry of each
    close; config/member-close-lock the lock; config/member-close-unresolved-verdict the verdict.
    DISCLOSED RESIDUALS: the guarantee holds at the interpreter's signal-delivery points
    (eval-breaker granularity) on the main thread, where CPython runs Python signal handlers; a
    signal delivered to the process before the handlers are installed takes the default
    disposition, and one delivered after the restore takes the restored disposition (a SIGINT
    after SIGINT's restore raises KeyboardInterrupt there, replacing an exception already
    propagating when nothing was deferred; one handled at or after the final drain's entry
    preempts that drain, so the signals still deferred, a SIGTERM among them, are dropped). A
    signal whose own Python handler is cut off at its entry by a
    newly arrived one that raises is lost (CPython has already cleared its pending flag;
    _close_on_signal). SIGKILL, and a SIGINT or SIGTERM whose disposition the caller had already
    changed (left as it is), are outside this, and so is SIGHUP (like every other signal but SIGINT
    and SIGTERM, it keeps the caller's disposition, and its default action ends the process with no
    close); an exception set into the main thread from outside (PyThreadState_SetAsyncExc) is not a
    signal and is not limited to one."""
    _MEMBERS.update(closing=False, raised=None, held=[])
    del _MEMBERS["refused"][:]
    previous, code, unresolved = {}, None, []
    try:
        try:
            try:
                _install_signal_handlers(previous)
                code = _suite_main(report_path, lanes)
            finally:
                _MEMBERS["closing"] = True
                _finish_members()
        finally:
            unresolved = _finish_members()
    finally:
        try:
            _restore_signal_handlers(previous)
        finally:
            _raise_held()
    return _final_verdict(code, unresolved, list(_MEMBERS["refused"]))


def _suite_main(report_path, lanes):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    if shutil.which("git") is None:
        print("SELF-TEST HARNESS ERROR: git is not available on PATH", file=sys.stderr)
        return 2
    if lanes is None:
        print("SELF-TEST HARNESS ERROR: tools/selftest_git_fixture_env.py is the shared"
              " suite engine and runs no checks of its own; run one of the registered"
              " runners (" + ", ".join(_SUITE_RUNNERS) + ")."
              " Nothing was run (cannot evaluate).", file=sys.stderr)
        return 2
    try:
        raw = tempfile.mkdtemp(prefix="git-fixture-env-selftest-")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: no writable temp directory: {}".format(exc),
              file=sys.stderr)
        return 2
    try:
        code = lanes(Path(raw))
        if code is not None:
            # A lane return value can only FAIL the suite, never pass it: None falls
            # through to the expected-id reconciliation and the verdict below; the
            # exact int 2 is a harness error the lane reported for itself; any other
            # value (0, 1, a bool, a non-int) is itself a harness error. Whatever a
            # lane returns, the only exit taken here is 2.
            if type(code) is not int or code != 2:
                print("SELF-TEST HARNESS ERROR: the lane returned {!r}; a lane may"
                      " return only None (reconcile the executed set and judge the"
                      " verdict) or 2 (a harness error the lane reported itself)."
                      " Fail closed (cannot evaluate).".format(code), file=sys.stderr)
            return 2
    finally:
        shutil.rmtree(raw, ignore_errors=True)

    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    # 0 here is not yet a pass: main() prints the PASS line only after its close (_final_verdict).
    return _verdict(FAILURES, CANNOT_EVALUATE, TIMEOUT_CAUSED)


def _parse_argv(argv):
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return argv[1]
    print("usage: a git-fixture-env suite runner accepts [--execution-report ABS_PATH] "
          "(the report path must be absolute)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(_parse_argv(sys.argv[1:])))
