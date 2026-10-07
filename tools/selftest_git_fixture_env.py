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

  selftest_git_fixture_env.py                              exit 0 on pass, 1 on assertion failure
  selftest_git_fixture_env.py --execution-report ABS_PATH  also write the executed check IDs as JSON

Exit 2 is cannot-evaluate: a harness/setup error, including bad arguments, no git binary, no writable
temp directory, a failed report write, or an unreadable, malformed, or suite-missing expectation
manifest; and a bounded child that reached its time bound (see _run_bounded): a config-injection
member (_run_config_member) or a corpus leak lane (_run_corpus, _run_corpus_imported). A member
reached its time bound, so this run is no verdict on that member: the timeout is listed under SELF-TEST
CANNOT EVALUATE, never as a failure of that member. A finding is folded into the cannot-evaluate
list only when it is attributed to a recorded timeout (see _timeout_cause and
_registered_offenders: the finding's own result is that timeout's message, which names the member,
or it lists only members whose run timed out with no hook, monitor or exposure evidence, or, for a
check over several scenarios, every scenario that did not time out matched; see
_partial_timeout_cause); every other finding is definite and outranks cannot-evaluate, so the run
exits 1 (SELF-TEST FAIL, with the timeouts listed beside it). A definite finding over a lane never
names a member whose run only timed out: that member is listed by its own timeout message under
cannot evaluate (_registered_offenders, _lifecycle_finding). tools/check_selftest_execution.py treats any child exit outside {0, 1}
as CANNOT EVALUATE and itself exits 2 (its self-test leg st/child-rc2-2), so a timeout never reads as
a pass or as a code failure there either.

Only those children are bounded this way. The suite's own git probes and fixture setup commands
keep their 60 s subprocess timeouts, and reaching one of those raises (a traceback, exit 1) instead
of becoming cannot-evaluate; the other helper children keep the timeout handling each documents.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: selftest_git_fixture_env.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import ast
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: selftest_git_fixture_env.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_fixture_env  # noqa: E402
import _selftest_exit_report  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opf" / "tools"))
import _optlevel  # noqa: E402  level-0 parses for the mutants, shared with opf/tools

ROOT = Path(__file__).resolve().parents[1]
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
CORPUS_SELFTEST = ROOT / "tools" / "selftest_aiqt_corpus.py"
SUITE_ID = "git-fixture-env-selftest"
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
        ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-selftest"):
            "This suite cannot recursively launch itself; its controls run here directly.",
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
                              (".github/workflows/quality.yml", extract_ci)):
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
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(run, prepared))
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


def _wrapper_bypass_control(base, env, marker, monitor_marker):
    """POSITIVE CONTROL for the fall-through tripwire and its fail-closed guard.
    Mechanism leg: hold an observer-shaped wrapper open for writing from a live
    child (the ETXTBSY condition a concurrently forked child creates) and exec
    git through the member-shaped PATH: CPython's exec PATH search skips the
    busy wrapper and continues, so without the tripwire the REAL git would run
    unobserved, a false clean. The tripwire must intercept (rc 66, bypass
    recorded, wrapper silent), _require_wrapper_observed must refuse the
    recorded bypass, and the released leg must run the wrapper itself with the
    tripwire silent; this leg's PATH is pinned to the two controlled
    directories only.
    Production leg: _config_results itself runs one synthetic member whose
    member launch holds the REAL observer wrapper open for writing and launches
    git; the run must fail closed with the bypass diagnostic naming the member,
    never return a clean result."""
    import shlex
    from unittest.mock import patch

    probe = base / "bypass-control"
    trap = probe / "fallback-trap"
    trap.mkdir(parents=True)
    wrapper_log = probe / "wrapper-log"
    bypass_log = probe / "bypass-log"
    for path in (wrapper_log, bypass_log):
        path.write_bytes(b"")
    wrapper = probe / "git"
    wrapper.write_text("#!/bin/sh\nprintf 'wrapper\\n' >> {}\n".format(
        shlex.quote(str(wrapper_log))), encoding="utf-8")
    wrapper.chmod(0o700)
    tripwire = trap / "git"
    tripwire.write_text("#!/bin/sh\nprintf 'bypassed\\n' >> {}\nexit 66\n".format(
        shlex.quote(str(bypass_log))), encoding="utf-8")
    tripwire.chmod(0o700)
    probe_env = {"PATH": str(probe) + os.pathsep + str(trap)}

    def launch():
        try:
            done = subprocess.run(["git"], env=probe_env, capture_output=True, timeout=60)
            rc = done.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            rc = "control launch failed: {}".format(exc)
        return (rc, bypass_log.read_bytes(), wrapper_log.read_bytes())

    holder_code = "\n".join((
        "import sys",
        "handle = open(sys.argv[1], 'ab')",
        "print('held', flush=True)",
        "sys.stdin.read()",
        "handle.close()",
    ))
    holder = subprocess.Popen([sys.executable, "-I", "-B", "-c", holder_code, str(wrapper)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        try:
            _await_holder_ready(holder)
            held = launch()
        except HolderNotReady as exc:
            held = "HolderNotReady: {}".format(exc)
        try:
            _require_wrapper_observed(bypass_log, "control")
            refused = "guard accepted a recorded bypass"
        except ValueError as exc:
            refused = str(exc).startswith("config observer wrapper was bypassed")
    finally:
        holder.stdin.close()
        holder.stdout.close()
        try:
            holder.wait(timeout=60)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=60)
    for path in (wrapper_log, bypass_log):
        path.write_bytes(b"")
    released = launch()

    def bypassing_member(argv, member_env):
        busy = Path(member_env["PATH"].split(os.pathsep)[0]) / "git"
        with open(busy, "ab"):
            done = subprocess.run(["git", "config", "--get", "user.name"], env=member_env,
                                  capture_output=True, timeout=60)
        return done.returncode

    outcome = "member bypass returned a clean result"
    try:
        with patch.object(sys.modules[__name__], "_run_config_member", bypassing_member):
            _config_results((("tools/bypass-control-probe", "--self-test"),),
                            env, marker, monitor_marker)
    except ValueError as exc:
        message = str(exc)
        outcome = (message.startswith("config observer wrapper was bypassed")
                   and "member run: tools/bypass-control-probe --self-test" in message)
    check("config/wrapper-bypass-fails-closed",
          (held, refused, released, outcome),
          ((66, b"bypassed\n", b""), True, (0, b"", b"wrapper\n"), True))


_WRITE_RECORDERS = []
_WRITE_AUDIT_INSTALLED = False


def _dispatch_write_audit(event, args):
    """Process-wide audit hook (CPython cannot remove one); inert unless a
    recorder is registered in _WRITE_RECORDERS."""
    for recorder in tuple(_WRITE_RECORDERS):
        recorder(event, args)


def _prepared_before_pool_control(env, marker, monitor_marker):
    """DETERMINISTIC guard for the root-cause ordering in _config_results (no
    stress, no timing): every file a member will exec (the observer wrapper,
    the tripwire, the copied hooks and fsmonitor) must be written and chmodded
    on the MAIN thread before the worker pool is constructed. A CPython audit
    hook records every open-for-write, chmod, rename, link and symlink with its
    thread and whether the pool exists yet; each synthetic member snapshots the
    executable files in its private tree from inside the pool. Moving member
    preparation back into the pooled body turns this red on every run, whether
    or not a fork happens to race a write. The red member chmods its own
    wrapper from inside the pool and must be flagged, so a recorder that sees
    nothing cannot pass; the required files and the every-executable-was-seen
    leg keep the clean member from passing vacuously. File-descriptor-only
    operations (os.fchmod, a write through an inherited descriptor) are outside
    what these audit events name."""
    import concurrent.futures
    import threading
    from unittest.mock import patch

    global _WRITE_AUDIT_INSTALLED
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    target = {"open": 0, "os.chmod": 0, "os.rename": 1, "os.link": 1, "os.symlink": 1}
    main_thread = threading.main_thread().ident
    pool_started = []
    events = []
    snapshots = {}

    def record(event, args):
        if event not in target or (event == "open" and not args[2] & write_flags):
            return
        path = args[target[event]]
        if not isinstance(path, int):
            events.append((os.path.abspath(os.fsdecode(path)), event,
                           threading.get_ident() == main_thread and not pool_started))

    class MarkedPool(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            pool_started.append(True)
            super().__init__(*args, **kwargs)

    def snapshot_member(argv, member_env):
        private = member_env["PATH"].split(os.pathsep)[0]
        found = set()
        for folder, _, names in os.walk(private):
            for name in names:
                path = os.path.join(folder, name)
                if os.path.isfile(path) and os.stat(path).st_mode & 0o111:
                    found.add(os.path.abspath(path))
        snapshots[argv[0]] = (private, found)
        if argv[0].endswith("-red"):
            os.chmod(os.path.join(private, "git"), 0o700)
        return 0

    required = ["git", os.path.join("fallback-trap", "git"), os.path.join("caller", "fsmonitor"),
                os.path.join("caller", "config-hooks", "pre-commit")]

    def verdict(member):
        if member not in snapshots:
            return "member did not run: " + member
        private, found = snapshots[member]
        written = {path for path, _, _ in events}
        return (sorted(set(required) - {os.path.relpath(p, private) for p in found}),
                sorted(os.path.relpath(p, private) for p in found - written),
                sorted({(os.path.relpath(p, private), event)
                        for p, event, early in events if p in found and not early}))

    clean, red = "tools/prepared-before-pool-probe", "tools/prepared-before-pool-probe-red"
    if not _WRITE_AUDIT_INSTALLED:
        sys.addaudithook(_dispatch_write_audit)
        _WRITE_AUDIT_INSTALLED = True
    _WRITE_RECORDERS.append(record)
    try:
        with patch.object(concurrent.futures, "ThreadPoolExecutor", MarkedPool), patch.object(
                sys.modules[__name__], "_run_config_member", snapshot_member):
            _config_results(((clean, "--self-test"), (red, "--self-test")),
                            env, marker, monitor_marker)
        got = (bool(pool_started), verdict(clean), verdict(red))
    except ValueError as exc:
        got = "config run raised: {}".format(exc)
    finally:
        _WRITE_RECORDERS.remove(record)
    check("config/executables-prepared-before-pool", got,
          (True, ([], [], []), ([], [], [("git", "os.chmod")])))


def _member_result(results, member, column):
    """Legacy IDs report derived runs, including every registered argument variant."""
    values = [value[column] for argv, value in results.items() if _command_identity(argv)[0] == member]
    if not values:
        return "missing registered member: " + member
    want = 0 if column == 0 else b""
    return want if all(value == want for value in values) else values


def _roster_checks():
    from unittest.mock import patch
    import check_ci_parity
    import check_selftest_execution

    local = "tools/run_all_checks.sh"
    opf = "opf/tools/run_all_checks.sh"
    ci = ".github/workflows/quality.yml"
    real_reader = check_ci_parity.read_runner_text
    local_text, local_diagnostic = real_reader(ROOT / local, local)
    ci_text, ci_diagnostic = real_reader(ROOT / ci, ci)
    # The live registries must come through the byte-level reader clean;
    # everything below mutates these texts.
    check("roster/live-registry-reads", (local_diagnostic, ci_diagnostic),
          (None, None))
    if local_diagnostic is not None or ci_diagnostic is not None:
        return
    binding = 'here="$(cd "$(dirname "$0")" && pwd)" || exit 2'
    # The valid failure-state initializers, so an empty-roster fixture reaches
    # its own guard rather than the missing-initializer diagnostic.
    state = 'failed=0\nfailed_names=""\n'
    # The real dispatcher definition, so a fixture that declares a gate
    # reaches its own guard rather than the definition-order diagnostic.
    definition_start = local_text.index("run_gate() {")
    definition = local_text[
        definition_start:local_text.index("\n}\n", definition_start) + 3]

    def add_local(command):
        # Keep the exact terminal summary last so each fixture reaches its own guard.
        summary = 'if [ "$failed" -ne 0 ]; then\n'
        if local_text.count(summary) != 1:  # explicit, not an assert: python -O strips asserts
            raise AssertionError("the runner's terminal summary is not unique")
        return local_text.replace(summary, command + "\n" + summary, 1)

    def refusal():
        try:
            _registered_selftests()
        except ValueError as exc:
            return str(exc)
        return ""

    scratch = Path(tempfile.mkdtemp(prefix="roster-fixture-"))

    def disk_reader(name, relative, data):
        """Write the fixture to a REAL file and redirect only the path, so
        the raw fixture bytes flow through the actual byte-level reader;
        injecting already-decoded text here formerly hid Path.read_text()'s
        universal-newline translation from every roster fixture."""
        fixture = scratch / name.replace("/", "-")
        fixture.write_bytes(data if isinstance(data, bytes)
                            else data.encode("utf-8"))
        target = ROOT / relative

        def read(path, source, reader=None):
            return real_reader(fixture if path == target else path,
                               source, reader=reader)
        return read

    # Require THIS guard's diagnostic: an unrelated downstream refusal is not
    # evidence that the intended guard ran. Bad commands augment a valid roster.
    for check_id, relative, text, diagnostic in (
            ("roster/empty-local-refused", local, state, local + ": empty registry"),
            ("roster/unparseable-local-refused", local,
             add_local('\nrun_gate "cont" python3 -I -B tools/check_secrets.py \\\n  --self-test\n'),
             local + ": registry diagnostics:"),
            ("roster/empty-opf-refused", opf, binding + "\n" + state + "exit 0\n",
             opf + ": empty registry"),
            ("roster/standalone-scaffold-refused", opf, binding + "\n" + binding + "\nexit 0\n",
             "unsupported standalone runner scaffold"),
            # The byte-level reader refuses the form feed on the raw
            # bytes at read time; the shared adapter's raw-text screen
            # stays behind it for text that arrives already decoded
            # (vector 26 pins that screen directly).
            ("roster/standalone-text-format-refused", opf,
             binding + "\n" + state
             + 'run_gate "probe-selftest" python3 -I -B tools/check_secrets.py --self-test\n'
             + "# hidden\fexit 0\n",
             opf + ": registry diagnostics:"),
            ("roster/unparseable-ci-refused", ci,
             ci_text + '\n      - run: python3 -I -B tools/check_secrets.py --self-test | cat\n',
             ci + ": registry diagnostics:"),
            # A reviewed masked expression survives extraction only in the CI
            # workflow; the selection guard must still refuse the masked
            # member as a dynamic self-test argument.
            ("roster/dynamic-arguments-refused", ci,
             ci_text + '\n      - name: Dynamic probe'
                       '\n        run: python3 -I -B tools/check_secrets.py'
                       ' --self-test --base "$PUSH_BEFORE"\n',
             "dynamic self-test arguments:"),
            # In the LOCAL runner the same spelling is refused at extraction:
            # the gate-word character allowlist admits no expansion, so a
            # masked flag value never reaches selection there.
            ("roster/local-masked-flag-refused", local,
             add_local('\nrun_gate "dynamic" python3 -I -B tools/check_secrets.py --self-test --base "$PUSH_BEFORE"\n'),
             local + ": registry diagnostics:"),
            ("roster/launcher-refused", local,
             add_local('\nrun_gate "launcher" python3 -B -I tools/check_secrets.py --self-test\n'),
             "unsupported self-test launcher:"),
            ("roster/empty-selftests-refused", local,
             state + definition
             + 'run_gate "live" python3 -I -B tools/check_secrets.py\n',
             local + ": empty self-test roster"),
            ("roster/prefixed-invalid-suite-refused", local,
             add_local('\nrun_gate "bad-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-selftest --extra\n'),
             "unparseable suite invocation:"),
            # codex qa10 MAJOR-1: Path.read_text() translated a planted
            # carriage return into a newline BEFORE any screen ran, so a
            # \r-hidden gate line in a REAL runner file on disk read as a
            # clean extra gate and the roster stayed green. Every fixture
            # in this table is now written to disk and read through the
            # actual byte-level reader; the two \r fixtures fail against
            # any decode-first reader, whose universal-newline translation
            # erases the byte ahead of a text screen.
            ("roster/local-carriage-return-refused", local,
             add_local('# hidden\rrun_gate "cr-probe" python3 -I -B tools/check_secrets.py --self-test'),
             local + ": registry diagnostics:"),
            ("roster/opf-carriage-return-refused", opf,
             binding + "\n" + state + definition
             + 'run_gate "probe-selftest" python3 -I -B tools/check_secrets.py --self-test\n'
             + '# hidden\rrun_gate "cr-shadow" python3 -I -B tools/check_secrets.py --self-test\n'
             + "exit 0\n",
             opf + ": registry diagnostics:"),
            # U+2028 (spelled via chr to keep this source ASCII) survives a
            # UTF-8 decode, so only the byte-level screen refuses it at
            # read time with the raw-byte diagnostic.
            ("roster/ci-line-separator-refused", ci,
             ci_text + "# probe" + chr(0x2028) + "trailer\n",
             ci + ": registry diagnostics:"),
    ):
        with patch.object(check_ci_parity, "read_runner_text",
                          disk_reader(check_id, relative, text)):
            got = refusal()
        check(check_id, got.startswith(diagnostic), True)

    # A parser/member provenance mismatch must not silently drop a selected member.
    extract = check_ci_parity.extract_local
    def missing_origin(text):
        result = extract(text)
        return result._replace(origins={})
    with patch.object(check_ci_parity, "extract_local", missing_origin):
        got = refusal()
    check("roster/missing-origin-refused",
          got.startswith("cannot recover exact self-test arguments:"), True)

    stale = ("tools/check_secrets.py", "--self-test", "--stale-exclusion")
    with patch.dict(CONFIG_EXCLUSIONS["all"], {stale: "Synthetic stale exclusion."}):
        got = refusal()
    check("roster/stale-exclusion-refused",
          got.startswith("stale or unreasoned all exclusion:"), True)

    extra = '\nrun_gate "argument-probe" python3 -I -B tools/check_secrets.py --self-test --red-on-revert\n'
    read = disk_reader("roster-registered-arguments", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read):
        roster = _registered_selftests()
    check("roster/registered-arguments", ("tools/check_secrets.py", "--self-test",
                                         "--red-on-revert") in roster, True)

    suites = check_selftest_execution._manifest_suites(CHECKS_MANIFEST)
    extra = '\nrun_gate "new-suite" python3 -I -B ./tools/check_selftest_execution.py --suite roster-probe\n'
    read = disk_reader("roster-prefixed-suite-expanded", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read), patch.object(
            check_selftest_execution, "_manifest_suites", return_value=suites + [
                {"id": "roster-probe", "runner": "tools/check_secrets.py",
                 "expected-check-ids": ["probe"]}]):
        roster = _registered_selftests()
    check("roster/prefixed-suite-expanded",
          ("./tools/check_selftest_execution.py", "--suite", "roster-probe") in roster
          and ("tools/check_secrets.py",) in roster, True)

    extra = '\nrun_gate "recursive-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-selftest\n'
    read = disk_reader("roster-prefixed-recursion", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read):
        roster = _registered_selftests()
    check("roster/prefixed-recursion-excluded",
          ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-selftest") not in roster
          and ("./tools/check_selftest_execution.py", "--suite", "git-fixture-env-selftest") not in roster
          and ("tools/selftest_git_fixture_env.py",) not in roster, True)
    shutil.rmtree(scratch, ignore_errors=True)


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


def _system_pin_checks(base):
    import types
    # Level 0 parse and compile: the mutant must not follow -O/-OO.
    tree = _optlevel.parse(Path(_git_fixture_env.__file__).read_text(encoding="utf-8"))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(ast.unparse(t) == "os.environ['PATH']" for t in node.targets)]
    if len(assignments) != 1:
        raise ValueError("cannot uniquely mutate lifecycle PATH installation")
    assignments[0].value = _optlevel.parse('saved.get("PATH", os.defpath)', mode="eval").body
    mutant = types.ModuleType("fixture_path_mutant")
    exec(compile(ast.fix_missing_locations(tree), "<path-removal-mutant>", "exec", optimize=0),
         mutant.__dict__)
    pins, restored = _system_pin_probe(base, _git_fixture_env.fixture_git_lifecycle)
    check("env/lifecycle-system-pins", pins, (0, ["1", os.devnull]))
    check("env/lifecycle-restores-caller", restored, True)
    pins, restored = _system_pin_probe(base, mutant.fixture_git_lifecycle)
    check("env/lifecycle-path-removal-red", (pins, restored), ((0, ["", ""]), True))


def _config_injection_lane(base):
    """CONFIG-INJECTION: no inherited GIT_* pins may hide the caller's on-disk poison.
    Each member gets rc and hook-byte assertions; neither one substitutes for the other.
    A real commit first proves the marker hooks execute under this caller configuration.
    Hooks/ignore poison cannot see read-only git calls. The fsmonitor marker covers
    index reads; malformed HOME/XDG config also covers reads such as rev-parse.
    Commands come from the shell/Quality registries, with exact script arguments.
    Literal legacy IDs below report results; they never select which commands run.
    The legacy /hooks IDs assert both hook and fsmonitor marker bytes. Separate
    malformed-config runs exclude the two real-checkout archive readers, which
    retain caller config by contract. System poison uses a PATH shim, never /etc.
    Hardcoded absolute executables, a replaced PATH, and repository-local config
    remain outside the shim's coverage; see _config_results."""
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
    check("config/injection-control", bool(marker.read_bytes()), True)
    (control / "untracked").write_text("dirt\n", encoding="utf-8")
    # Exercise each on-disk source independently, including HOME's fallback.
    for ignore_id, attributes_id, mode in (
            ("config/explicit-ignore-control", "config/explicit-attributes-control", "explicit"),
            ("config/xdg-ignore-control", "config/xdg-attributes-control", "xdg"),
            ("config/home-ignore-control", "config/home-attributes-control", "home"),
    ):
        probe_env = dict(env)
        if mode != "explicit":
            probe_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                             GIT_CONFIG_NOSYSTEM="1")
        if mode == "home":
            probe_env.pop("XDG_CONFIG_HOME")
        probe = subprocess.run(["git", "-C", str(control), "status", "--porcelain"],
                               env=probe_env, capture_output=True, timeout=60, check=True)
        check(ignore_id, probe.stdout, b"")
        probe = subprocess.run(["git", "-C", str(control), "check-attr", "fixture-poison", "--", "seed"],
                               env=probe_env, capture_output=True, timeout=60, check=True)
        check(attributes_id, probe.stdout, b"seed: fixture-poison: set\n")
    for archive_id, pin in (
            ("config/archive-attributes-live", False),
            ("config/archive-attributes-neutralized", True),
    ):
        import io
        import tarfile
        args = ["-c", "core.attributesFile=/dev/null"] if pin else []
        archive = subprocess.run(["git", "-C", str(control), "-c", "gc.auto=0",
                                  "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                  *args, "archive", "HEAD"],
                                 env=env, capture_output=True, timeout=60, check=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            names = tar.getnames()
        check(archive_id, "seed" in names, pin)
    monitor_marker.write_bytes(b"")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=env,
                           capture_output=True, timeout=60)
    check("config/combined-fsmonitor-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    _wrapper_bypass_control(base, env, marker, monitor_marker)
    _prepared_before_pool_control(env, marker, monitor_marker)
    combined = _config_results(roster, env, marker, monitor_marker)
    offenders, cause = _registered_offenders(combined)
    check("config/registered-combined", offenders, [], cause)
    for rc_id, hooks_id, member in (
            ("config/selftest_aiqt_corpus/rc", "config/selftest_aiqt_corpus/hooks",
             "tools/selftest_aiqt_corpus.py"),
            ("config/selftest_orch_hooks/rc", "config/selftest_orch_hooks/hooks",
             "tools/selftest_orch_hooks.py"),
            ("config/selftest_aiqt_hooks/rc", "config/selftest_aiqt_hooks/hooks",
             "tools/selftest_aiqt_hooks.py"),
            ("config/_qa_adapter/rc", "config/_qa_adapter/hooks",
             "tools/_qa_adapter.py"),
            ("config/check_record_drift/rc", "config/check_record_drift/hooks",
             "tools/check_record_drift.py"),
            ("config/check_mistakes_register/rc", "config/check_mistakes_register/hooks",
             "tools/check_mistakes_register.py"),
            ("config/check_version_monotonicity/rc", "config/check_version_monotonicity/hooks",
             "tools/check_version_monotonicity.py"),
            ("config/check_release_build/rc", "config/check_release_build/hooks",
             "tools/check_release_build.py"),
            ("config/check_release_delta/rc", "config/check_release_delta/hooks",
             "tools/check_release_delta.py"),
            ("config/check_record_sections/rc", "config/check_record_sections/hooks",
             "tools/check_record_sections.py"),
            ("config/check_portability/rc", "config/check_portability/hooks",
             "tools/check_portability.py"),
            ("config/gen_manifest/rc", "config/gen_manifest/hooks",
             "tools/gen_manifest.py"),
            ("config/check_manifest/rc", "config/check_manifest/hooks",
             "tools/check_manifest.py"),
            ("config/check_branch_root/rc", "config/check_branch_root/hooks",
             "tools/check_branch_root.py"),
            ("config/check_gensrc_failclose/rc", "config/check_gensrc_failclose/hooks",
             "tools/check_gensrc_failclose.py"),
            ("config/selftest_ci_status/rc", "config/selftest_ci_status/hooks",
             "tools/selftest_ci_status.py"),
            ("config/check_opf_init/rc", "config/check_opf_init/hooks",
             "opf/tools/check_opf_init.py"),
            ("config/check_opf_upgrade/rc", "config/check_opf_upgrade/hooks",
             "opf/tools/check_opf_upgrade.py"),
            ("config/check_opf_doctor/rc", "config/check_opf_doctor/hooks",
             "opf/tools/check_opf_doctor.py"),
            ("config/_opf_init_operation/rc", "config/_opf_init_operation/hooks",
             "opf/tools/_opf_init_operation.py"),
            ("config/_opf_oplock/rc", "config/_opf_oplock/hooks",
             "opf/tools/_opf_oplock.py"),
            ("config/_opf_observe/rc", "config/_opf_observe/hooks",
             "opf/tools/_opf_observe.py"),
            ("config/opf/rc", "config/opf/hooks",
             "opf/tools/opf.py"),
    ):
        check(rc_id, _member_result(combined, member, 0), 0)
        check(hooks_id, (_member_result(combined, member, 1),
                         _member_result(combined, member, 2)), (b"", b""))

    # No hooks or ignore poison in these lanes: read-only production helpers
    # must be tested independently of fixture writes.
    for path in (ignore, attributes, xdg / "git" / "ignore", xdg / "git" / "attributes",
                 home / ".config" / "git" / "ignore", home / ".config" / "git" / "attributes",
                 home / ".config" / "git" / "config"):
        path.unlink()
    fsconfig = '[core]\n\tfsmonitor = {}\n'.format(json.dumps(str(monitor)))
    malformed = "[invalid\n"
    (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
    (xdg / "git" / "config").write_text(fsconfig, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=env,
                           capture_output=True, timeout=60)
    check("config/fsmonitor-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    (home / ".gitconfig").write_text(malformed, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "rev-parse", "HEAD"], env=env,
                           capture_output=True, timeout=60)
    check("config/malformed-control", probe.returncode != 0, True)
    (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
    (xdg / "git" / "config").write_text(malformed, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "rev-parse", "HEAD"], env=env,
                           capture_output=True, timeout=60)
    check("config/xdg-malformed-control", probe.returncode != 0, True)

    for fs_rc_id, fs_marker_id, member in (
            ("config/check_manifest/fsmonitor-rc", "config/check_manifest/fsmonitor",
             "tools/check_manifest.py"),
            ("config/gen_manifest/fsmonitor-rc", "config/gen_manifest/fsmonitor",
             "tools/gen_manifest.py"),
            ("config/check_record_sections/fsmonitor-rc", "config/check_record_sections/fsmonitor",
             "tools/check_record_sections.py"),
            ("config/_qa_adapter/fsmonitor-rc", "config/_qa_adapter/fsmonitor",
             "tools/_qa_adapter.py"),
    ):
        (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
        (xdg / "git" / "config").write_text(fsconfig, encoding="utf-8")
        monitor_marker.write_bytes(b"")
        rc = _run_config_member((member, "--self-test"), env)
        check(fs_rc_id, rc, 0)
        check(fs_marker_id, monitor_marker.read_bytes(), b"")
    (home / ".gitconfig").write_text(malformed, encoding="utf-8")
    (xdg / "git" / "config").write_text(malformed, encoding="utf-8")
    malformed_results = _config_results(
        [argv for argv in roster if _command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"]],
        env, marker, monitor_marker)
    offenders, cause = _registered_offenders(malformed_results)
    check("config/registered-malformed", offenders, [], cause)
    for malformed_id, member in (
            ("config/selftest_aiqt_corpus/malformed", "tools/selftest_aiqt_corpus.py"),
            ("config/selftest_orch_hooks/malformed", "tools/selftest_orch_hooks.py"),
            ("config/selftest_aiqt_hooks/malformed", "tools/selftest_aiqt_hooks.py"),
            ("config/_qa_adapter/malformed", "tools/_qa_adapter.py"),
            ("config/check_record_drift/malformed", "tools/check_record_drift.py"),
            ("config/check_mistakes_register/malformed", "tools/check_mistakes_register.py"),
            ("config/check_version_monotonicity/malformed", "tools/check_version_monotonicity.py"),
            ("config/check_record_sections/malformed", "tools/check_record_sections.py"),
            ("config/check_portability/malformed", "tools/check_portability.py"),
            ("config/gen_manifest/malformed", "tools/gen_manifest.py"),
            ("config/check_manifest/malformed", "tools/check_manifest.py"),
            ("config/check_branch_root/malformed", "tools/check_branch_root.py"),
            ("config/check_gensrc_failclose/malformed", "tools/check_gensrc_failclose.py"),
            ("config/selftest_ci_status/malformed", "tools/selftest_ci_status.py"),
            ("config/check_opf_init/malformed", "opf/tools/check_opf_init.py"),
            ("config/check_opf_upgrade/malformed", "opf/tools/check_opf_upgrade.py"),
            ("config/check_opf_doctor/malformed", "opf/tools/check_opf_doctor.py"),
            ("config/_opf_init_operation/malformed", "opf/tools/_opf_init_operation.py"),
            ("config/_opf_oplock/malformed", "opf/tools/_opf_oplock.py"),
            ("config/_opf_observe/malformed", "opf/tools/_opf_observe.py"),
            ("config/opf/malformed", "opf/tools/opf.py"),
    ):
        check(malformed_id, _member_result(malformed_results, member, 0), 0)

    # Simulate an installed system config without writing /etc. Reassert its path
    # only when a child did not disable or explicitly replace system configuration.
    system = base / "system-gitconfig"
    system.write_text(config, encoding="utf-8")
    empty_home = base / "system-lane-home"
    empty_home.mkdir()
    bin_dir = base / "system-bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    if real_git is None:
        raise ValueError("system lane requires git")
    wrapper = bin_dir / "git"
    wrapper.write_text(
        '#!/bin/sh\n'
        'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then\n'
        '  export GIT_CONFIG_SYSTEM={}\n'
        'fi\nexec {} "$@"\n'.format(shlex.quote(str(system)), shlex.quote(real_git)),
        encoding="utf-8")
    wrapper.chmod(0o700)
    system_env = dict(env, HOME=str(empty_home), XDG_CONFIG_HOME=str(empty_home),
                      PATH=str(bin_dir) + os.pathsep + env.get("PATH", os.defpath))
    monitor_marker.write_bytes(b"")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=system_env,
                           capture_output=True, timeout=60)
    check("config/system-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    system_results = _config_results(roster, system_env, marker, monitor_marker, system=True)
    offenders, cause = _registered_offenders(system_results)
    check("config/registered-system", offenders, [], cause)
    return system_results


# Per-member time bounds (seconds) for _run_config_member. A member absent here keeps
# CONFIG_MEMBER_BOUND_DEFAULT. config/member-bound-table pins this table (every key an exact
# registered roster argv, the one override, the default); it does not test any sizing.
#
# CONFIG_MEMBER_BOUND_DEFAULT is a DELIBERATE SHORTER DEADLINE, not a bound sized above every
# member's own end-to-end budget. Budgets the members grant themselves, READ FROM SOURCE (not
# measured): opf/tools/opf.py --self-test grants its blocked-isolation case 31 * 180 + 30 = 5610 s
# (the isolation matrix's 31 cases at 180 s each plus a 30 s launch margin), above this default;
# the tools/check_selftest_execution.py members set no internal bound of their own (the CI job
# timeout is their outer bound). The kill-timeout rule ("A kill timeout outlives the wait it
# bounds", AGENTS.md) allows a deadline below a callee's budget only where cutting the work short
# is the explicit intent, and only when reaching it is attributed to the deadline rather than
# reported as the callee's failure.
# CHOICE: the default is NOT raised above 5610 s; it stays a deliberate shorter deadline, and
# cutting a member short is the explicit intent here because this lane owns no member's verdict.
# Every roster member is a registered step or the runner behind a registered --suite step
# (_registered_selftests reads the CI workflow, both standalone runners and the suite manifest),
# and that step runs the member to completion under no deadline from this suite, so no member's
# pass or fail depends on this bound. This lane asks a different question (does the
# member stay clean under injected caller hooks, ignore files, attributes and fsmonitor), it
# reruns each member in three config lanes (combined, malformed, system), and the bound caps how
# long one stalled rerun can hold that answer back. Cutting a rerun short leaves the question
# unanswered, never answered wrongly: reaching the bound is cannot-evaluate (exit 2 when nothing
# definite was found), never a FAIL of that member and never a pass.
CONFIG_MEMBER_BOUND_DEFAULT = 1200
CONFIG_MEMBER_BOUNDS = {
    # MEASURED (2026-10-07, nice 10, load5 about 12 to 14 on a 16-CPU host): one complete run took
    # 582.30 s wall and 425.55 s CPU and passed; two plain runs were still running at 585 s and
    # 590 s. The previous 1200 s default was reached twice at load5 above 20 (2026-10-07).
    # ESTIMATED (assumes wall time grows roughly in proportion to load once load exceeds the CPU
    # count): 3600 s, about 6.2 times the measured run, covers load5 up to about 80; it also
    # exceeds the command's own T75 wait chain (540 s per call, 4 calls: 2160 s).
    ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert"): 3600,
}
# After the process-group kill, the bounded drain of output still held by a descendant that left
# the group (setsid or setpgid), and the bounded reap of the leader after it.
CONFIG_MEMBER_DRAIN_SECONDS = 30
CONFIG_MEMBER_REAP_SECONDS = 5
# The corpus leak lanes' bound (_run_corpus, _run_corpus_imported).
CORPUS_BOUND = 300


def _config_member_bound(member):
    return CONFIG_MEMBER_BOUNDS.get(tuple(member), CONFIG_MEMBER_BOUND_DEFAULT)


def _stop_member_group(proc):
    """Bounded cleanup of a bounded child that must end: SIGKILL its process group, falling back
    to the leader alone when the group kill is refused, then drain its output for at most
    CONFIG_MEMBER_DRAIN_SECONDS and reap the leader for at most CONFIG_MEMBER_REAP_SECONDS.
    Returns the problems met ([] when clean). It never raises, so a cleanup failure is reported
    beside the timeout and cannot turn it into a FAIL or a traceback."""
    problems = []
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:  # any refusal is reported, never raised
        problems.append("group kill refused ({}: {}); killed the leader alone, so a descendant "
                        "in its group may survive".format(type(exc).__name__, exc))
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
    so reaching the bound kills the WHOLE group: a grandchild holding the output pipe dies with it
    instead of surviving the timeout. The numeric killpg cannot name a reused group: communicate()
    raised before reaping the leader, so its pid, and with it the group id, stays allocated until
    the cleanup reaps it.
    A timeout is RECORDED FIRST, in cannot_evaluate (default CANNOT_EVALUATE), and announced as
    banner + message on report (default sys.stdout, never stderr: the cannot-evaluate path is a
    stdout report plus exit 2, and the execution gate requires the suite's error stream to hold
    only declared bytes); only then is the child cleaned up (_stop_member_group, bounded), in a
    finally that runs whatever the record or the announcement does (a raising record or stream
    still has the child killed and reaped before its exception propagates). A cleanup problem is
    printed on the same stream as a second line through _report_guarded: a stream that refuses
    that line loses it, so the diagnostic never replaces the timeout return or the exception
    already propagating. Any other exception from the wait, including a BaseException such as
    KeyboardInterrupt, also has the group killed and reaped first; an OSError, SubprocessError or
    ValueError is then returned as its message, and anything else propagates unchanged.
    config/member-timeout-record-before-cleanup, config/member-timeout-cleanup-on-raise,
    config/member-timeout-diagnostic-guarded and config/member-timeout-interrupt-cleanup pin
    that order and those guards.
    DISCLOSED RESIDUAL: a descendant that moved itself to another process group survives the
    kill; its pipe is drained for at most CONFIG_MEMBER_DRAIN_SECONDS before the pipes are
    closed. In its own session the child has no controlling terminal: a read of an inherited
    terminal fails."""
    sink = sys.stdout if report is None else report
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace",
                                start_new_session=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc), "", ""
    try:
        try:
            stdout, stderr = proc.communicate(timeout=bound)
        except subprocess.TimeoutExpired:
            message = "TIMEOUT: {} reached its {} s bound (cannot evaluate)".format(label, bound)
            try:
                (CANNOT_EVALUATE if cannot_evaluate is None else cannot_evaluate).append(message)
                print(banner + message, file=sink, flush=True)
            finally:
                problems = _stop_member_group(proc)
                if problems:
                    _report_guarded("{}cleanup after that timeout was incomplete (the timeout "
                                    "stays cannot-evaluate): {}".format(banner,
                                                                        "; ".join(problems)),
                                    sink)
            return message, "", ""
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            _stop_member_group(proc)
            return str(exc), "", ""
        except BaseException:
            # An interrupt (KeyboardInterrupt, SystemExit) or any other exception in the wait:
            # the group is killed and reaped, then the exception propagates unchanged.
            _stop_member_group(proc)
            raise
        return proc.returncode, stdout, stderr
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


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


def _main_verdict_control(sleeper, fixture, run=None):
    """The production config/member-timeout-main-verdict check: _main_verdict_scenarios through
    run (default _run_bounded), checked with its own partial cause. _mixed_timeout_controls runs
    this same function over an injected runner, so the cause this check passes is pinned."""
    observed, cause = _main_verdict_scenarios(sleeper, fixture, run)
    check("config/member-timeout-main-verdict", observed, MAIN_VERDICT_EXPECTED, cause)
    return observed


def _mixed_timeout_controls(sleeper):
    """config/member-timeout-main-verdict-mixed: _main_verdict_control (the production check
    call, against a private finding record) over an injected runner in which one scenario's
    child reaches its bound while the other returns, in BOTH scenario orders: a definite
    mismatch beside the timeout (the attributed child times out and the definite child exits 2,
    or the definite child times out and the attributed child exits 1) keeps the finding definite
    and _verdict exits 1; a correct result beside the timeout is attributed and exits 2.
    config/timeout-offenders-split: a lane with one member that only timed out and one definite
    offender lists ONLY the definite one, in _registered_offenders and in _lifecycle_finding, in
    both results orders; with only the timed-out member the finding is attributed; an
    unrecorded timeout message is definite; a lifecycle probe problem beside a member that only
    timed out keeps the lifecycle finding definite."""
    import contextlib
    import io
    from unittest.mock import patch

    this = sys.modules[__name__]

    line = "CONFIG-INJECTION TIMEOUT: {} reached its 5 s bound (cannot evaluate)\n".format(sleeper)
    right = {"attributed": (2, line + "SELF-TEST CANNOT EVALUATE\n  - probe/attributed-rc\n", ""),
             "definite": (1, line + "SELF-TEST FAIL:\n  - probe/definite\n  - "
                          "probe/attributed-rc\n", "")}
    codes = []
    for plan in ({"attributed": "timeout", "definite": (2, "", "")},
                 {"attributed": (1, "", ""), "definite": "timeout"},
                 {"attributed": "timeout", "definite": "right"},
                 {"attributed": "right", "definite": "timeout"}):
        def fake(argv, cwd, env, bound, label, banner, plan=plan):
            outcome = plan[argv[-1]]
            if outcome == "timeout":
                return "TIMEOUT: {} reached its {} s bound (cannot evaluate)".format(
                    label, bound), "", ""
            return right[argv[-1]] if outcome == "right" else outcome

        failures, caused, executed = [], {}, []
        with patch.object(this, "FAILURES", failures), \
                patch.object(this, "TIMEOUT_CAUSED", caused), \
                patch.object(this, "EXECUTED", executed), \
                patch.object(this, "_EXECUTED_SET", set()):
            observed = _main_verdict_control(sleeper, sleeper.parent, fake)
        timeouts = [entry for entry in observed if _is_timeout(entry)]
        with contextlib.redirect_stdout(io.StringIO()):
            code = _verdict(failures, timeouts, caused)
        codes.append((executed, len(failures), [len(cause) for cause in caused.values()], code))
    ran = ["config/member-timeout-main-verdict"]
    check("config/member-timeout-main-verdict-mixed", codes,
          [(ran, 1, [], 1), (ran, 1, [], 1), (ran, 1, [1], 2), (ran, 1, [1], 2)])

    timeout = "TIMEOUT: opf/tools/x.py --self-test reached its 1 s bound (cannot evaluate)"
    stalled = (("opf/tools/x.py", "--self-test"), (timeout, b"", b"", b""))
    failed = (("opf/tools/y.py", "--self-test"), (1, b"", b"", b""))
    got = []
    for pairs in ((stalled, failed), (failed, stalled), (stalled,)):
        results = dict(pairs)
        got.append((_registered_offenders(results, [timeout]),
                    _lifecycle_finding(list(results), results, set(), [], [timeout])))
    got.append(_registered_offenders(dict((stalled,)), []))
    got.append(_lifecycle_finding([stalled[0]], dict((stalled,)), set(), ["m.e: (1, [])"],
                                  [timeout]))
    check("config/timeout-offenders-split", got,
          [(([("opf/tools/y.py", "--self-test")], ()), ((["opf/tools/y.py"], []), ())),
           (([("opf/tools/y.py", "--self-test")], ()), ((["opf/tools/y.py"], []), ())),
           (([("opf/tools/x.py", "--self-test")], (timeout,)),
            ((["opf/tools/x.py"], []), (timeout,))),
           ([("opf/tools/x.py", "--self-test")], ()),
           (([], ["m.e: (1, [])"]), ())])


def _timeout_cleanup_order_controls(sleeper):
    """config/member-timeout-record-before-cleanup: when _run_bounded's cleanup starts, the
    timeout is already in the record (a cleanup moved ahead of the record fails this).
    config/member-timeout-cleanup-on-raise: when the announcement stream raises, or the record
    itself raises, the exception still propagates AND the child has been killed and reaped
    (returncode -SIGKILL) first (a cleanup skipped by the raise leaves returncode None and fails
    this). config/member-timeout-diagnostic-guarded: with a cleanup problem injected and a
    stream that refuses the cleanup diagnostic, a successful record still returns the timeout
    tuple, and a failed record still propagates the record's own exception, the child reaped in
    both. config/member-timeout-interrupt-cleanup: a KeyboardInterrupt injected into the wait
    still propagates, after the child has been killed and reaped. Every child spawned here is
    killed afterwards whatever the code under test did."""
    import contextlib
    import io
    from unittest.mock import patch

    this = sys.modules[__name__]
    real_stop = _stop_member_group
    real_popen = subprocess.Popen
    spawned, interrupt = [], []

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        spawned.append(child)
        if interrupt:
            interrupt_first_wait(child)
        return child

    class RaisingStream:
        def write(self, text):
            raise RuntimeError("injected announcement failure")

        def flush(self):
            pass

    class RaisingRecord:
        def append(self, entry):
            raise RuntimeError("injected record failure")

    class RefusingCleanupReport(io.StringIO):
        def write(self, text):
            if "cleanup after that timeout" in text:
                raise OSError("injected cleanup-report failure")
            return super().write(text)

    class InjectedInterrupt(KeyboardInterrupt):
        pass

    def interrupt_first_wait(child):
        real_communicate, calls = child.communicate, []

        def communicate(*c_args, **c_kwargs):
            calls.append(c_kwargs.get("timeout"))
            if len(calls) == 1:
                raise InjectedInterrupt("injected interrupt in the wait")
            return real_communicate(*c_args, **c_kwargs)

        child.communicate = communicate

    def stop_with_problem(proc):
        return real_stop(proc) + ["injected cleanup problem"]

    argv = [sys.executable, "-I", "-B", str(sleeper)]
    snapshots = []
    recorded = []

    def stop_after_record(proc):
        snapshots.append(list(recorded))
        return real_stop(proc)

    order, outcomes, guarded, interrupted = "not run", [], [], "not run"
    try:
        with patch.object(subprocess, "Popen", spawn), \
                patch.object(this, "_stop_member_group", stop_after_record), \
                contextlib.redirect_stderr(io.StringIO()):
            rc, _, _ = _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                    "record-order control", "CONTROL ", recorded, io.StringIO())
        order = (_is_timeout(rc), recorded == [rc], snapshots == [[rc]])
        for record, stream in (([], RaisingStream()), (RaisingRecord(), io.StringIO())):
            first = len(spawned)
            try:
                with patch.object(subprocess, "Popen", spawn), \
                        contextlib.redirect_stderr(io.StringIO()):
                    _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                 "cleanup-on-raise control", "CONTROL ", record, stream)
                raised = None
            except RuntimeError as exc:
                raised = str(exc)
            outcomes.append((raised, [child.returncode for child in spawned[first:]],
                             [_is_timeout(entry) for entry in record]
                             if isinstance(record, list) else None))
        for record in ([], RaisingRecord()):
            first, stream, rc, raised = len(spawned), RefusingCleanupReport(), None, None
            try:
                with patch.object(subprocess, "Popen", spawn), \
                        patch.object(this, "_stop_member_group", stop_with_problem), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc, _, _ = _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                            "diagnostic-guard control", "CONTROL ", record,
                                            stream)
            except Exception as exc:
                raised = "{}: {}".format(type(exc).__name__, exc)
            guarded.append((raised, _is_timeout(rc),
                            [entry == rc for entry in record] if isinstance(record, list)
                            else None,
                            stream.getvalue() == ("CONTROL {}\n".format(rc)
                                                  if _is_timeout(rc) else ""),
                            [child.returncode for child in spawned[first:]]))
        first, raised = len(spawned), None
        interrupt.append(True)
        try:
            with patch.object(subprocess, "Popen", spawn), \
                    contextlib.redirect_stderr(io.StringIO()):
                _run_bounded(argv, str(sleeper.parent), dict(os.environ), 60,
                             "interrupt control", "CONTROL ", [], io.StringIO())
        except (InjectedInterrupt, Exception) as exc:
            raised = "{}: {}".format(type(exc).__name__, exc)
        finally:
            interrupt.clear()
        interrupted = (raised, [child.returncode for child in spawned[first:]])
    finally:
        for child in spawned:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    child.kill()
                try:
                    child.wait(timeout=CONFIG_MEMBER_REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
    check("config/member-timeout-record-before-cleanup", order, (True, True, True))
    check("config/member-timeout-cleanup-on-raise", outcomes,
          [("injected announcement failure", [-signal.SIGKILL], [True]),
           ("injected record failure", [-signal.SIGKILL], None)])
    check("config/member-timeout-diagnostic-guarded", guarded,
          [(None, True, [True], True, [-signal.SIGKILL]),
           ("RuntimeError: injected record failure", False, None, True, [-signal.SIGKILL])])
    check("config/member-timeout-interrupt-cleanup", interrupted,
          ("InjectedInterrupt: injected interrupt in the wait", [-signal.SIGKILL]))


def _member_timeout_controls(base):
    """Controls for the bounded member runs.
    config/member-timeout-cannot-evaluate: a fixture member spawns a sleeping grandchild that
    inherits (holds) its stdout, then sleeps past a short bound; the timeout comes back as the
    cannot-evaluate marker and is recorded, its diagnostic on the control's own stream and nothing
    on the suite's stderr. config/member-timeout-kills-group: the grandchild is "dead" afterwards
    (_pid_state; unreadable /proc evidence is cannot-evaluate and fails the check closed).
    config/pid-state-fail-closed: _pid_state over a scratch process table.
    config/member-timeout-cleanup-failure: with the group kill refused (PermissionError injected),
    the timeout stays recorded and cannot-evaluate, the refusal is reported as a second stdout
    line, nothing reaches stderr, and the leader is killed alone.
    config/member-failure-diagnostic-stdout: a member exiting non-zero prints its output on
    stdout, never stderr. config/timeout-verdict-exit and config/timeout-attribution: _verdict's
    ordering and the attribution helpers. config/member-timeout-main-verdict: main() in a child,
    with production defaults, exits 2 for a timeout with only attributed findings and 1 when a
    definite finding is also present, the diagnostic on stdout and stderr empty; one scenario's
    timeout never hides the other's definite mismatch (_mixed_timeout_controls). The timeout
    record and cleanup order, the guarded cleanup diagnostic and the cleanup on an interrupted
    wait are pinned by _timeout_cleanup_order_controls.
    config/member-bound-table pins the per-member bound table against the registered roster. A
    fixture that never got going inside its bound is attributed to that timeout, never a red."""
    import contextlib
    import io
    import time
    from unittest.mock import patch

    fixture = base / "timeout-fixture"
    fixture.mkdir()
    pidfile = fixture / "grandchild.pid"
    member = fixture / "member.py"
    member.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-I', '-B', '-c', "
        "'import os, sys, time; open(sys.argv[1] + \".tmp\", \"w\").write(str(os.getpid())); "
        "os.replace(sys.argv[1] + \".tmp\", sys.argv[1]); time.sleep(600)', sys.argv[1]])\n"
        "time.sleep(600)\n", encoding="utf-8")
    recorded = []
    diagnostic = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(member), str(pidfile)), dict(os.environ), bound=20,
                                cannot_evaluate=recorded, report=diagnostic)
    elapsed = time.monotonic() - started
    check("config/member-timeout-cannot-evaluate",
          (isinstance(rc, str) and rc.startswith("TIMEOUT: "), recorded == [rc],
           diagnostic.getvalue() == "CONFIG-INJECTION {}\n".format(rc), leaked.getvalue(),
           elapsed < 20 + CONFIG_MEMBER_DRAIN_SECONDS + CONFIG_MEMBER_REAP_SECONDS + 30),
          (True, True, True, "", True))
    cause = None
    try:
        grandchild = int(pidfile.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # The fixture itself never got going inside its bound: host load, no verdict either way.
        message = ("TIMEOUT: member-timeout control: the grandchild never started within the "
                   "20 s bound ({})".format(exc))
        CANNOT_EVALUATE.append(message)
        got, cause = "not observed: " + message, (message,)
    else:
        deadline = time.monotonic() + 10
        got = _pid_state(grandchild)
        while got == "alive" and time.monotonic() < deadline:
            time.sleep(0.2)
            got = _pid_state(grandchild)
        if got == "alive":
            os.kill(grandchild, signal.SIGKILL)
    check("config/member-timeout-kills-group", got, "dead", cause)

    table = fixture / "proc"
    for pid, content in (("101", "101 (sleep) S 1 2 3\n"), ("102", "102 (a) b) Z 1\n"),
                         ("103", "103 no-state\n"), ("104", "104 (x) Q 1\n")):
        (table / pid).mkdir(parents=True)
        (table / pid / "stat").write_text(content, encoding="utf-8")
    (table / "105" / "stat").mkdir(parents=True)
    states = [_pid_state(pid, str(table)) for pid in (101, 102, 103, 104, 105, 106)]
    (table / "self").mkdir()
    states.append(_pid_state(106, str(table)))
    check("config/pid-state-fail-closed",
          [state.split(":")[0] for state in states],
          ["alive", "dead", "cannot-evaluate", "cannot-evaluate", "cannot-evaluate",
           "cannot-evaluate", "dead"])

    sleeper = fixture / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    refused = []

    def refuse(pgid, sig):
        refused.append(pgid)
        raise PermissionError(1, "injected group-kill refusal")

    recorded = []
    diagnostic = io.StringIO()
    with patch.object(os, "killpg", refuse), \
            contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(sleeper),), dict(os.environ), bound=3,
                                cannot_evaluate=recorded, report=diagnostic)
    lines = diagnostic.getvalue().splitlines()
    check("config/member-timeout-cleanup-failure",
          (isinstance(rc, str) and rc.startswith("TIMEOUT: "), recorded == [rc],
           lines[:1] == ["CONFIG-INJECTION " + str(rc)],
           len(lines) == 2 and "PermissionError" in lines[1] and "cannot-evaluate" in lines[1],
           leaked.getvalue(), [_pid_state(pgid) for pgid in refused[:1]]),
          (True, True, True, True, "", ["dead"]))

    failing = fixture / "failing.py"
    failing.write_text("import sys\nprint('member-out')\nprint('member-err', file=sys.stderr)\n"
                       "sys.exit(3)\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(failing),), dict(os.environ), bound=120)
    check("config/member-failure-diagnostic-stdout",
          (rc, "member-out" in out.getvalue(), "member-err" in out.getvalue(), leaked.getvalue()),
          (3, True, True, ""), (rc,) if _is_timeout(rc) else None)

    codes = []
    timeout = "TIMEOUT: m reached its 1 s bound (cannot evaluate)"
    attributed = "config/m/rc: got {!r}, want 0".format(timeout)
    for failures, timeouts, caused in (
            ([], [], {}), (["a finding"], [], {}), ([], [timeout], {}),
            (["a finding"], [timeout], {}),
            ([attributed], [timeout], {attributed: (timeout,)}),
            ([attributed, "a finding"], [timeout], {attributed: (timeout,)}),
            ([attributed], [], {attributed: (timeout,)})):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = _verdict(failures, timeouts, caused)
        codes.append((code, ("CANNOT EVALUATE" in out.getvalue(),
                             "SELF-TEST FAIL" in out.getvalue(),
                             "no verdict on that member" in out.getvalue())))
    check("config/timeout-verdict-exit", codes,
          [(0, (False, False, False)), (1, (False, True, False)), (2, (True, False, True)),
           (1, (False, True, True)), (2, (True, False, True)), (1, (False, True, True)),
           (1, (False, True, False))])

    clean = (0, b"", b"", b"")
    check("config/timeout-attribution",
          ([_timeout_cause(got) for got in (timeout, [0, timeout], [1, timeout], [False, timeout],
                                            "[Errno 2] launch", [0], 0)],
           [_registered_offenders(results, [timeout]) for results in (
               {("a",): clean}, {("a",): (timeout, b"", b"", b"")},
               {("a",): (timeout, b"hook\n", b"", b"")},
               {("a",): (timeout, b"", b"", b""), ("b",): (1, b"", b"", b"")},
               {("a",): ("[Errno 2] launch", b"", b"", b"")}, {("a",): None})]),
          ([(timeout,), (timeout,), (), (), (), (), ()],
           [([], ()), ([("a",)], (timeout,)), ([("a",)], ()), ([("b",)], ()),
            ([("a",)], ()), ([("a",)], ())]))

    _main_verdict_control(sleeper, fixture)
    _mixed_timeout_controls(sleeper)
    _timeout_cleanup_order_controls(sleeper)

    roster = set(_registered_selftests())
    record = ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert")
    check("config/member-bound-table",
          (sorted(key for key in CONFIG_MEMBER_BOUNDS if key not in roster),
           _config_member_bound(record), CONFIG_MEMBER_BOUND_DEFAULT,
           sorted({_config_member_bound(argv) for argv in roster if argv != record})),
          ([], 3600, 1200, [1200]))


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


def _manifest_setup_failures(base):
    """A failed add or commit must raise even when the earlier init succeeded."""
    import gen_manifest

    original = gen_manifest._git
    try:
        for check_id, operation in (
                ("setup/gen-manifest-add-checked", "add"),
                ("setup/gen-manifest-commit-checked", "commit"),
        ):
            def fake_git(root, *args):
                return subprocess.CompletedProcess(args, int(args[0] == operation))
            gen_manifest._git = fake_git
            try:
                gen_manifest._build_fixture(base / ("failed-" + operation))
            except subprocess.CalledProcessError:
                refused = True
            else:
                refused = False
            check(check_id, refused, True)
    finally:
        gen_manifest._git = original



def _manifest_extra_setup_failures():
    """Execute each standalone setup expression with a failing git result.
    These are the eight stale/binok/marker calls outside _build_fixture; parsing
    the actual expressions catches removal of their check_returncode calls.
    This bounded probe does not simulate the rest of the generator's self-test.
    """
    tree = _optlevel.parse((ROOT / "tools" / "gen_manifest.py").read_text(encoding="utf-8"))
    owners = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_self_test_main_isolated"]
    for check_id, fixture, operation in (
            ("setup/gen-manifest-stale-add", "stale", "add"),
            ("setup/gen-manifest-stale-commit", "stale", "commit"),
            ("setup/gen-manifest-binok-init", "binok", "init"),
            ("setup/gen-manifest-binok-add", "binok", "add"),
            ("setup/gen-manifest-binok-commit", "binok", "commit"),
            ("setup/gen-manifest-marker-init", "marker", "init"),
            ("setup/gen-manifest-marker-add", "marker", "add"),
            ("setup/gen-manifest-marker-commit", "marker", "commit"),
    ):
        expressions = [n for owner in owners for n in ast.walk(owner)
                       if isinstance(n, ast.Expr) and any(
                           isinstance(call, ast.Call) and _call_name(call) == "_git"
                           and len(call.args) >= 2 and isinstance(call.args[0], ast.Name)
                           and call.args[0].id == fixture
                           and isinstance(call.args[1], ast.Constant)
                           and call.args[1].value == operation for call in ast.walk(n))]
        refused = False
        if len(expressions) == 1:
            def failed_git(*args):
                return subprocess.CompletedProcess(args, 1)
            try:
                exec(compile(ast.Module(body=expressions, type_ignores=[]),
                             "<fixture-setup-probe>", "exec", optimize=0),
                     {"_git": failed_git, fixture: Path("/unused-fixture")})
            except subprocess.CalledProcessError:
                refused = True
        check(check_id, refused, True)


# These registered self-tests exercise data/text/filesystem fixtures, not a git
# lifecycle. They still require a successful observed system-config-lane run.
# New modules and removed wrappers are NOT implicitly exempt.
OPF_LIFECYCLE_EXEMPTIONS = {
    "opf/tools/_journal.py": "Descriptor-helper vectors over in-process pipes with patched os primitives.",
    "opf/tools/_opf_adopt.py": ("Adoption vocabulary and validator vectors; its observe vector suite runs "
                                "git only inside _opf_adopt_observe._git_archive_fixture's lifecycle."),
    "opf/tools/_opf_adopt_apply.py": "Apply-shell refusal, evidence and journal vectors over temporary filesystem fixtures.",
    "opf/tools/_opf_adopt_hook.py": "Pure enable-hook merge vectors over synthetic registration bytes.",
    "opf/tools/_opf_init.py": "Canonical model bytes, defaults and validator vectors.",
    "opf/tools/_opf_init_contract.py": "KEEP contract validation over synthetic models.",
    "opf/tools/_opf_pack_manifest.py": "Pack parsing and digest vectors over filesystem fixtures.",
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


def _opf_lifecycle_graph_checks():
    """Mutation D uses a synthetic route mirroring the retired two-level wrapper shape (the fixture
    above); rename cases guard discovery."""
    import copy
    names = denial = None
    expected_names = "derivable lifecycle graph"
    expected_denial = "named wrapper bypass"
    try:
        tree = _optlevel.parse(_LIFECYCLE_GRAPH_FIXTURE)
        module = "lifecycle_fixture"
        entry = "self_test"
        delegates = _opf_lifecycle_delegates({module: tree})
        delegate = delegates[module, entry][1]
        route_name = delegates[module, "_self_test_main"][1]
        renamed = copy.deepcopy(tree)
        for node in ast.walk(renamed):
            if isinstance(node, ast.FunctionDef) and node.name == delegate:
                node.name = "body_without_a_suffix"
            elif isinstance(node, ast.Name) and node.id == delegate:
                node.id = "body_without_a_suffix"
        renamed.body.append(_optlevel.parse("def unrelated_isolated(): pass").body[0])
        expected_names = dict(delegates)
        expected_names[module, entry] = (module, "body_without_a_suffix")
        names = _opf_lifecycle_delegates({module: renamed})

        mutant = copy.deepcopy(tree)
        route = next(node for node in mutant.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == entry]
        if len(calls) != 1:
            raise ValueError("cannot uniquely mutate the fixture wrapper route")
        calls[0].func.id = delegate
        expected_denial = "lifecycle bypass: {}.{} -> {}.{}".format(
            module, route.name, module, delegate)
        try:
            _opf_lifecycle_delegates({module: mutant})
        except ValueError as exc:
            denial = str(exc)
    except (OSError, SyntaxError, ValueError, KeyError, StopIteration) as exc:
        names = denial = str(exc)
    check("env/opf-lifecycle-names-independent", names, expected_names)
    check("env/opf-lifecycle-bypass-denied", denial, expected_denial)


def _opf_home_lifecycles(config_results):
    """Account for every registered OPF module and its observed command variants.
    Wrapped entries must isolate before delegation and restore after an exception.
    Explicit non-git exemptions still run under the config observer; its absolute
    executable/replaced-PATH residual applies. The derived graph rejects direct
    wrapper bypasses; dynamic routes retain the graph helper's disclosed residual.
    """
    code = "\n".join((
        "import importlib, inspect, json, os, sys",
        "sys.path.insert(0, sys.argv[1])",
        "module = importlib.import_module(sys.argv[2])",
        "entry, delegate = sys.argv[3:5]",
        "saved = dict(os.environ)",
        "seen = []",
        "class StopProbe(Exception): pass",
        "def stop(*args):",
        "    home = os.environ.get('HOME')",
        "    seen.append([home != saved.get('HOME'),",
        "                 home == os.environ.get('XDG_CONFIG_HOME'), os.path.isdir(home),",
        "                 os.environ.get('GIT_CONFIG_NOSYSTEM') == '1'])",
        "    raise StopProbe()",
        "setattr(module, delegate, stop)",
        "try:",
        "    fn = getattr(module, entry)",
        "    args = [None for p in inspect.signature(fn).parameters.values()",
        "            if p.default is inspect.Parameter.empty]",
        "    fn(*args)",
        "except StopProbe:",
        "    pass",
        "print(json.dumps([seen, dict(os.environ) == saved]))",
    ))
    results = {}
    roster = [argv for argv in _registered_selftests()
              if _command_identity(argv)[0].startswith("opf/tools/")]
    paths = {_command_identity(argv)[0] for argv in roster}
    # Paths missing for a reason other than their config run; _lifecycle_finding adds the config
    # runs and keeps a member whose run only timed out out of a definite finding.
    definite = set(OPF_LIFECYCLE_EXEMPTIONS) - paths
    problems = []
    try:
        trees = {Path(relative).stem: ast.parse(
            (ROOT / relative).read_text(encoding="utf-8")) for relative in sorted(paths)}
        registrations = _opf_lifecycle_delegates(trees)
    except (OSError, SyntaxError, ValueError) as exc:
        registrations = {}
        problems.append(str(exc))
    for relative in sorted(paths):
        module = Path(relative).stem
        delegates = {entry: callee[1] for (owner, entry), callee in registrations.items()
                     if owner == module}
        reason = OPF_LIFECYCLE_EXEMPTIONS.get(relative)
        if not delegates:
            if not reason or not reason.strip():
                definite.add(relative)
        elif reason is not None:
            definite.add(relative)  # A stale exemption must be reviewed and removed.
        for entry, delegate in sorted(delegates.items()):
            env = dict(os.environ, HOME="/caller-home", XDG_CONFIG_HOME="/caller-xdg",
                       GIT_CONFIG_NOSYSTEM="0")
            try:
                child = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", code,
                     str(ROOT / "opf" / "tools"), module, entry, delegate],
                    env=env, capture_output=True, text=True, timeout=60)
                try:
                    got = (child.returncode, json.loads(child.stdout))
                except ValueError:
                    got = (child.returncode, child.stdout, child.stderr)
            except (OSError, subprocess.SubprocessError) as exc:
                got = str(exc)
            results[module, entry] = got
    if not paths or not results:
        problems.append("no registered lifecycle observations")
    problems.extend("{}.{}: {!r}".format(module, entry, value)
                    for (module, entry), value in sorted(results.items())
                    if value != (0, [[[True, True, True, True]], True]))
    for check_id, module, entry in (
            ("env/opf-upgrade-home-lifecycle", "check_opf_upgrade", "_suite"),
            ("env/opf-tooling-home-lifecycle", "opf", "run_self_tests"),
    ):
        if (module, entry) not in results:
            problems.append("{}.{}: missing lifecycle observation".format(module, entry))
        check(check_id, results.get((module, entry)), (0, [[[True, True, True, True]], True]))
    got, cause = _lifecycle_finding(roster, config_results, definite, problems)
    check("env/registered-opf-lifecycles", got, ([], []), cause)


def _lifecycle_finding(roster, config_results, definite, problems, recorded=None):
    """(got, cause) for env/registered-opf-lifecycles. definite holds the paths missing for a
    reason other than their config run; each roster argv whose config run was not clean adds its
    path to it unless that run only timed out (_split_offenders). With any definite path or
    problem, got lists ONLY the definite paths and cause is (): a member whose run only timed out
    is reported by its own timeout message on the cannot-evaluate list, never in this FAIL.
    Otherwise got lists the timed-out paths and cause their timeout messages."""
    offenders, timed_out, causes = _split_offenders(
        {argv: config_results.get(argv) for argv in roster}, recorded)
    definite = set(definite) | {_command_identity(argv)[0] for argv in offenders}
    if definite or problems:
        return (sorted(definite), problems), ()
    return (sorted({_command_identity(argv)[0] for argv in timed_out}), problems), causes


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
    of that repository). A launch failure or an unreadable trace is a loud string, never a
    silent pass."""
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
        lines = trace.read_text(encoding="utf-8").splitlines()
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return "maintenance probe failed: %s" % (exc,)
    children = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError as exc:
            return "unparseable trace2 event: %s" % (exc,)
        if event.get("event") == "child_start":
            argv = event.get("argv") or []
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
    ("tools/selftest_git_fixture_env.py", "_run_bounded", ("unresolved",),
     "cannot-evaluate head: the bounded-run funnel receives its argv from its callers, and"
     " every caller passes a sys.executable head (_run_config_member's registered members,"
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
    ("opf/tools/check_opf_upgrade.py", "_suite_isolated", ("unresolved",),
     "cannot-evaluate argv: replays the doctor's own printed `git -C <fixture>"
     " --literal-pathspecs add ...` advice command (and its -f-stripped control) inside the"
     " fixture repo; add is not maintenance-triggering and the argv shape is validated by the"
     " preceding checks"),
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
     " the fixture argvs its callers spell: sys.executable CLI/crash subjects throughout,"
     " plus the worklog upgrade fixture's git init/add/commit whose argv carries the three"
     " F-367 pins as literal `-c` pairs in option position under a private HOME/XDG env"),
    # In-process exec loaders and mutant builders (the dynamic tripwire), each audited: each
    # exec compiles THIS repo's own tracked source, an AST/text mutant of it, or a red-leg
    # candidate body spelled as a string literal in the self-test itself, into a module
    # object, in-process; none launches a process at exec time. A launch the executed
    # TRACKED source spells is scanned at its own source location like every other launch;
    # a launch spelled only inside such a string-literal candidate body is outside the
    # scan's reach and is covered by its entry's audited justification instead (the
    # check_opf_init_observe.py red-leg candidate spells a subprocess.run whose argv
    # heads are sys.executable commands, never git).
    ("tools/selftest_git_fixture_env.py", "_system_pin_checks", ("dynamic",),
     "exec of an AST-built PATH-removal mutant of the lifecycle helper's own source, for the"
     " wrapper red leg",
     1),
    ("tools/selftest_git_fixture_env.py", "_manifest_extra_setup_failures", ("dynamic",),
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


class _SuiteLanesReplaced(Exception):
    """Raised by main() right after a caller-supplied lanes callable ran, to skip the suite's own
    lanes and fall through to main()'s own verdict path (config/member-timeout-main-verdict)."""


def main(report_path=None, lanes=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    if shutil.which("git") is None:
        print("SELF-TEST HARNESS ERROR: git is not available on PATH", file=sys.stderr)
        return 2
    try:
        raw = tempfile.mkdtemp(prefix="git-fixture-env-selftest-")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: no writable temp directory: {}".format(exc),
              file=sys.stderr)
        return 2
    try:
        base = Path(raw)
        if lanes is not None:
            lanes(base)
            raise _SuiteLanesReplaced

        # ---------- layer 1: the scrub itself ----------
        ambient_home = os.environ.get("HOME")
        restore = _poison_environ()
        try:
            env = _git_fixture_env.git_fixture_env(GIT_AUTHOR_NAME="Fixture Author")
        finally:
            restore()
        check("env/repo-selectors-dropped",
              sorted(k for k, v in POISON_VARS.items() if env.get(k) == v), [])
        check("env/allowlist-drops-novel-var", "GIT_FIXTURE_NOVEL_VARIABLE" in env, False)
        check("env/config-pins",
              (env.get("GIT_CONFIG_NOSYSTEM"), env.get("GIT_CONFIG_GLOBAL"),
               env.get("GIT_CONFIG_SYSTEM")),
              ("1", os.devnull, os.devnull))
        check("env/scratch-home",
              (env.get("HOME") == env.get("XDG_CONFIG_HOME"),
               bool(env.get("HOME")) and os.path.isdir(env.get("HOME", "")),
               env.get("HOME") != ambient_home),
              (True, True, True))
        check("env/override-kept", env.get("GIT_AUTHOR_NAME"), "Fixture Author")
        check("env/non-git-preserved", env.get(SENTINEL), "kept")

        # F-367: a fixture commit must not spawn automatic maintenance (an unpinned commit
        # LAUNCHES the DETACHED `git maintenance run --auto` / `git gc --auto` child; once the
        # automatic-work thresholds are met, that child can repack or prune the fixture's
        # .git/objects after the commit returned, racing a later copytree/rmtree/read of that
        # repository). The green leg proves the pinned env spawns none; the red leg strips
        # exactly the maintenance pins and must OBSERVE the child launching, so the probe is
        # proven discriminating, never vacuous.
        check("env/no-auto-maintenance",
              _auto_maintenance_children(base / "maintenance-green",
                                         _git_fixture_env.git_fixture_env()), [])
        unpinned = _git_fixture_env.git_fixture_env()
        for name in _git_fixture_env._MAINTENANCE_PIN_VARS:
            del unpinned[name]
        red = _auto_maintenance_children(base / "maintenance-red", unpinned)
        check("env/no-auto-maintenance-red",
              red if isinstance(red, str) else bool(red), True)

        # Layer 4, the tripwire leg: the repo-wide scan must report NO unpinned
        # maintenance-capable launch (green), and a planted unpinned fixture commit must be
        # reported while its pinned twin is not (the red leg proves the scan discriminating,
        # never vacuous).
        check("env/maintenance-pin-scan", _maintenance_pin_scan(ROOT), [])
        planted_root = base / "pin-scan-planted"
        (planted_root / "tools").mkdir(parents=True)
        (planted_root / "opf" / "tools").mkdir(parents=True)
        (planted_root / "tools" / "planted_unpinned.py").write_text(
            "import subprocess\n\n\n"
            "def _seed(repo):\n"
            "    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'x'],\n"
            "                   check=True, capture_output=True, timeout=30)\n",
            encoding="utf-8")
        (planted_root / "tools" / "planted_pinned.py").write_text(
            "import subprocess\n\n\n"
            "def _seed(repo):\n"
            "    subprocess.run(['git', '-C', str(repo), '-c', 'gc.auto=0',\n"
            "                    '-c', 'gc.autoDetach=false',\n"
            "                    '-c', 'maintenance.auto=false',\n"
            "                    'commit', '-q', '-m', 'x'],\n"
            "                   check=True, capture_output=True, timeout=30)\n",
            encoding="utf-8")
        planted = _maintenance_pin_scan(planted_root, allow_missing_files=True)
        got = "planted scan: %r" % (planted,)
        if len(planted) == 1 and "tools/planted_unpinned.py" in planted[0] \
                and "planted_pinned" not in planted[0]:
            got = True
        check("env/maintenance-pin-scan-red", got, True)

        # Layer 4, the contract leg: the planted red/green table over every launch form and
        # false-flag form round-2 review demonstrated (see _SCAN_CONTRACT_CASES).
        check("env/maintenance-pin-scan-contract",
              _scan_contract_failures(base / "pin-scan-contract"), [])

        # The in-place form, in a CHILD interpreter under a fully poisoned environment: after
        # scrub_git_environment() the only GIT_-prefixed variables left are the three pins, and
        # HOME has left the poisoned value for the scratch home.
        child_code = "\n".join((
            "import json, os, sys",
            "sys.path.insert(0, sys.argv[1])",
            "import _git_fixture_env",
            "_git_fixture_env.scrub_git_environment()",
            "left = sorted([k, v] for k, v in os.environ.items() if k.startswith('GIT_'))",
            "print(json.dumps([left, os.environ.get('HOME')]))",
        ))
        child_env = dict(os.environ)
        child_env.update(POISON_VARS)
        child_env["HOME"] = "/decoy/home"
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-c", child_code, str(ROOT / "tools")],
                env=child_env, capture_output=True, text=True, timeout=60)
            payload = json.loads(child.stdout)
            got = (child.returncode, payload[0], payload[1] != "/decoy/home")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            got = "scrub child failed: {}".format(exc)
        check("env/scrub-in-place-child", got,
              (0, [["GIT_CONFIG_COUNT", "3"], ["GIT_CONFIG_GLOBAL", os.devnull],
                   ["GIT_CONFIG_KEY_0", "gc.auto"], ["GIT_CONFIG_KEY_1", "gc.autoDetach"],
                   ["GIT_CONFIG_KEY_2", "maintenance.auto"], ["GIT_CONFIG_NOSYSTEM", "1"],
                   ["GIT_CONFIG_SYSTEM", os.devnull], ["GIT_CONFIG_VALUE_0", "0"],
                   ["GIT_CONFIG_VALUE_1", "false"], ["GIT_CONFIG_VALUE_2", "false"]], True))

        # caller_env_without_git(), in a CHILD interpreter under full poison, with HOME at a
        # scratch caller home whose .gitconfig carries the trust surface: after the in-place
        # scrub, the caller env drops every GIT_* variable, retains the PRE-scrub HOME (while the
        # process environment stays scrubbed), and a `git config --global` read under it sees the
        # caller's global config again (the mechanism behind safe.directory trust on a checkout
        # owned by another uid), while the fixture env still cannot.
        caller_home = base / "caller-home"
        caller_home.mkdir()
        (caller_home / ".gitconfig").write_text(
            "[safe]\n\tdirectory = *\n[aiqt]\n\tfixtureprobe = caller\n", encoding="utf-8")
        trust_code = "\n".join((
            "import json, os, subprocess, sys",
            "sys.path.insert(0, sys.argv[1])",
            "import _git_fixture_env",
            "pre_home = os.environ.get('HOME')",
            "_git_fixture_env.scrub_git_environment()",
            "_git_fixture_env.scrub_git_environment()",
            "caller = _git_fixture_env.caller_env_without_git()",
            "fixture = _git_fixture_env.git_fixture_env()",
            "probe = ['git', 'config', '--global', 'aiqt.fixtureprobe']",
            "c = subprocess.run(probe, env=caller, capture_output=True, text=True, timeout=60)",
            "f = subprocess.run(probe, env=fixture, capture_output=True, text=True, timeout=60)",
            "print(json.dumps([sorted(k for k in caller if k.startswith('GIT_')),",
            "                  caller.get('HOME') == pre_home,",
            "                  os.environ.get('HOME') == pre_home,",
            "                  [c.returncode, c.stdout.strip()], f.returncode == 0]))",
        ))
        trust_env = dict(os.environ)
        trust_env.update(POISON_VARS)
        trust_env["HOME"] = str(caller_home)
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-c", trust_code, str(ROOT / "tools")],
                env=trust_env, capture_output=True, text=True, timeout=120)
            payload = json.loads(child.stdout)
            got = (child.returncode, payload[0], payload[1], payload[2],
                   payload[3], payload[4])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            got = "caller-env child failed: {}".format(exc)
        check("env/caller-env-restores-trust-config", got,
              (0, [], True, False, [0, "caller"], False))

        # Stop the OPF suite at its first in-process invocation, after its fixture
        # isolation. Observe the actual inherited environment and its finally restoration.
        opf_code = "\n".join((
            "import json, os, sys",
            "sys.path.insert(0, sys.argv[1])",
            "import check_opf_init",
            "saved = dict(os.environ)",
            "seen = []",
            "class StopProbe(Exception): pass",
            "def invoke(args):",
            "    seen.append(dict(os.environ))",
            "    raise StopProbe()",
            "check_opf_init._suite(invoke)",
            "expected = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,",
            "            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',",
            "            'GIT_OPTIONAL_LOCKS': '0'}",
            "print(json.dumps([len(seen) == 1 and",
            "    {k: v for k, v in seen[0].items() if k.startswith('GIT_')} == expected,",
            "    bool(seen) and seen[0].get('PATH') == saved.get('PATH'),",
            "    dict(os.environ) == saved]))",
        ))
        opf_env = dict(os.environ)
        opf_env.update(POISON_VARS)
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-B", "-c", opf_code, str(ROOT / "opf" / "tools")],
                env=opf_env, capture_output=True, text=True, timeout=60)
            got = (child.returncode, json.loads(child.stdout))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            got = "OPF isolation child failed: {}".format(exc)
        check("env/opf-init-inherited-allowlist", got, (0, [True, True, True]))

        _system_pin_checks(base)

        # ---------- layer 2: per-member routing ----------
        for check_id, member_rel, scrub_names in (
                ("route/selftest-aiqt-corpus", "tools/selftest_aiqt_corpus.py",
                 ("git_fixture_env", "scrub_git_environment")),
                ("route/selftest-orch-hooks", "tools/selftest_orch_hooks.py",
                 ("scrub_git_environment",)),
                ("route/qa-adapter", "tools/_qa_adapter.py", ("git_fixture_env",)),
                ("route/check-record-drift", "tools/check_record_drift.py",
                 ("scrub_git_environment",)),
                ("route/check-mistakes-register", "tools/check_mistakes_register.py",
                 ("scrub_git_environment",)),
                ("route/check-version-monotonicity", "tools/check_version_monotonicity.py",
                 ("scrub_git_environment",)),
                ("route/check-release-build", "tools/check_release_build.py",
                 ("scrub_git_environment",)),
                ("route/check-release-delta", "tools/check_release_delta.py",
                 ("scrub_git_environment",)),
                ("route/check-opf-upgrade", "opf/tools/check_opf_upgrade.py",
                 ("_scrubbed_env",)),
                ("route/selftest-aiqt-hooks", "tools/selftest_aiqt_hooks.py",
                 ("scrub_git_environment",)),
        ):
            check(check_id, _calls_any(ROOT / member_rel, scrub_names), True)

        for check_id, member, owner, binding, factory, launches in (
                ("route/check-record-sections", "tools/check_record_sections.py",
                 "_selftest_git", "env", "git_fixture_env", True),
                ("route/check-portability", "tools/check_portability.py",
                 "_self_test_main_isolated", "git_env", "git_fixture_env", True),
                ("route/gen-manifest", "tools/gen_manifest.py",
                 "_git", "env", "git_fixture_env", "computed"),
                ("route/check-release-delta-env", "tools/check_release_delta.py",
                 "_selftest_env", "env", "git_fixture_env", False),
                ("route/check-release-delta-init", "tools/check_release_delta.py",
                 "_git_init_commit", "env", "git_fixture_env", True),
                ("route/check-release-delta-spy", "tools/check_release_delta.py",
                 "_spy_index", "senv", "git_fixture_env", True),
                ("route/check-release-build-attestation", "tools/check_release_build.py",
                 "_self_test_main_isolated", "ge", "git_fixture_env", "attestation"),
                ("route/check-branch-root", "tools/check_branch_root.py",
                 "_fixture_git", "env", "git_fixture_env", "computed"),
                ("route/check-gensrc-failclose", "tools/check_gensrc_failclose.py",
                 "_git_fixture", "env", "git_fixture_env", True),
                ("route/qa-adapter-fixture-env", "tools/_qa_adapter.py",
                 "_self_test_isolated", "genv", "git_fixture_env", True),
                ("route/check-opf-init", "opf/tools/check_opf_init.py",
                 "_suite_isolated", "fixture_env", "_scrubbed_env", False),
        ):
            check(check_id, _binding_calls(ROOT / member, owner, binding, factory, launches), True)
        for check_id, member, owner in (
                ("scope/check-manifest", "tools/check_manifest.py", "self_test_main"),
                ("scope/check-record-sections", "tools/check_record_sections.py", "self_test"),
                ("scope/qa-adapter", "tools/_qa_adapter.py", "_self_test"),
                ("scope/gen-manifest", "tools/gen_manifest.py", "self_test_main"),
        ):
            check(check_id, _scrub_scoped_first(ROOT / member, owner,
                                               "fixture_git_lifecycle"), True)
        check("trust/caller-env-archive-only", _caller_env_archive_only(), True)

        check("route/selftest-aiqt-hooks-fixture-env",
              _archive_reads_use_caller_env(ROOT / "tools" / "selftest_aiqt_hooks.py",
                                           fixture_calls=True), True)
        check("route/selftest-aiqt-corpus-fixture-env",
              _scrub_scoped_first(CORPUS_SELFTEST, "GitTests._init_repo", "git_fixture_env"),
              True)

        # Function-SCOPED routing: _calls_any proves the call SITE exists somewhere in the file,
        # so the corpus row (either name accepted) stayed green with only one of its two
        # protections removed. Each named self-test entry must itself lead with the in-place
        # scrub, a top-level call with no process-launching call before it.
        for check_id, member_rel, func_name in (
                ("scope/check-portability", "tools/check_portability.py", "self_test_main"),
                ("scope/selftest-aiqt-corpus-setup", "tools/selftest_aiqt_corpus.py", "GitTests.setUp"),
                ("scope/selftest-orch-hooks", "tools/selftest_orch_hooks.py", "main"),
                ("scope/selftest-aiqt-hooks", "tools/selftest_aiqt_hooks.py", "main"),
                ("scope/check-record-drift", "tools/check_record_drift.py", "self_test"),
                ("scope/check-mistakes-register", "tools/check_mistakes_register.py",
                 "self_test"),
                ("scope/check-version-monotonicity", "tools/check_version_monotonicity.py",
                 "self_test_main"),
                ("scope/check-release-build", "tools/check_release_build.py", "self_test_main"),
                ("scope/check-release-delta", "tools/check_release_delta.py", "self_test_main"),
        ):
            check(check_id, _scrub_scoped_first(ROOT / member_rel, func_name,
                                                "scrub_git_environment"), True)

        # The REAL-repository reads (the safe.directory regression): every literal
        # `git ... archive ...` launch in the two members that read the checkout under test must
        # pass env=caller_env_without_git(), or a checkout trusted only through the caller's
        # global config regresses to a rc=128 refusal under the in-place scrub.
        for check_id, member_rel in (
                ("trust/check-release-build-archive-env", "tools/check_release_build.py"),
                ("trust/check-release-delta-archive-env", "tools/check_release_delta.py"),
        ):
            check(check_id, _archive_reads_use_caller_env(ROOT / member_rel), True)

        _roster_checks()
        _opf_lifecycle_graph_checks()
        _member_timeout_controls(base)
        config_results = _config_injection_lane(base)
        _manifest_setup_failures(base)
        _manifest_extra_setup_failures()
        _opf_home_lifecycles(config_results)

        # ---------- layer 3: the end-to-end leak probe ----------
        decoy = _build_decoy(base)
        if decoy is None:
            return 2
        neutral_cwd = base / "neutral"
        neutral_cwd.mkdir()
        index_path = decoy / ".git" / "index"
        index_before = index_path.read_bytes()
        snap_before = _snapshot_git_dir(decoy)

        rc = _run_corpus("GIT_INDEX_FILE", str(index_path), neutral_cwd)
        check("leak/index-poison-fixture-green", rc, 0)
        check("leak/index-poison-caller-unchanged",
              (index_path.read_bytes() == index_before,
               _snapshot_git_dir(decoy) == snap_before),
              (True, True))

        rc = _run_corpus("GIT_DIR", str(decoy / ".git"), neutral_cwd)
        check("leak/gitdir-poison-fixture-green", rc, 0)
        check("leak/gitdir-poison-caller-unchanged", _snapshot_git_dir(decoy) == snap_before, True)

        # The IMPORTED corpus surface: run_self_test() bypasses __main__ and its entry-point
        # scrub, so this lane stays green only if the fixture lifecycle (GitTests.setUp) scrubs
        # itself.
        rc = _run_corpus_imported("GIT_DIR", str(decoy / ".git"), neutral_cwd)
        check("leak/imported-gitdir-poison-fixture-green", rc, 0)
        check("leak/imported-gitdir-poison-caller-unchanged",
              _snapshot_git_dir(decoy) == snap_before, True)
    except _SuiteLanesReplaced:
        pass
    finally:
        shutil.rmtree(raw, ignore_errors=True)

    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    code = _verdict(FAILURES, CANNOT_EVALUATE, TIMEOUT_CAUSED)
    if code:
        return code
    print("SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
          "tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


def _parse_argv(argv):
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return argv[1]
    print("usage: selftest_git_fixture_env.py [--execution-report ABS_PATH] "
          "(the report path must be absolute)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(_parse_argv(sys.argv[1:])))
