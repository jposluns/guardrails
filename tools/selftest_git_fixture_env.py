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
leak cannot silently return. Fourth, a repo-wide maintenance-pin completeness scan (F-367),
run as an enforceable CONTRACT: every git launch under tools/ and opf/tools/ must be a direct
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

Exit 2 is a harness/setup error, including bad arguments, no git binary, no writable temp directory,
a failed report write, or an unreadable, malformed, or suite-missing expectation manifest.
"""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("error: selftest_git_fixture_env.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_fixture_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
CORPUS_SELFTEST = ROOT / "tools" / "selftest_aiqt_corpus.py"
SUITE_ID = "git-fixture-env-selftest"
FAILURES = []
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


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


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
    Reuse CI-parity's fail-closed shell/YAML grammar. Only the standalone runner's
    validated directory binding and terminal exit need normalization.
    Declaration coverage only: selection uses a selftest_ basename or an explicit
    --self-test, --selftest or --suite flag, not fixture-building behaviour.
    Other entry modes, unregistered entries and conditional reachability are outside
    this inventory. Manifest runners also run directly, because the
    execution gate deliberately sanitizes its child environment.
    """
    from check_ci_parity import extract_local, extract_ci, _strip_comment, _tokenize, normalize
    from check_selftest_execution import _manifest_suites

    commands = set()
    for relative, extract in (("tools/run_all_checks.sh", extract_local),
                              ("opf/tools/run_all_checks.sh", extract_local),
                              (".github/workflows/quality.yml", extract_ci)):
        source = (root / relative).read_text(encoding="utf-8")
        if relative.startswith("opf/"):
            binding = 'here="$(cd "$(dirname "$0")" && pwd)" || exit 2'
            lines = source.splitlines()
            if lines.count(binding) != 1 or lines[-1] != "exit 0":
                raise ValueError("unsupported standalone runner scaffold")
            lines[lines.index(binding)] = "# validated standalone directory binding"
            lines[-1] = "# validated terminal exit"
            source = "\n".join(lines).replace('"$here/', '"opf/tools/')
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

    original = Path.read_text
    local = "tools/run_all_checks.sh"
    opf = "opf/tools/run_all_checks.sh"
    ci = ".github/workflows/quality.yml"
    local_text = (ROOT / local).read_text(encoding="utf-8")
    ci_text = (ROOT / ci).read_text(encoding="utf-8")
    binding = 'here="$(cd "$(dirname "$0")" && pwd)" || exit 2'

    def refusal():
        try:
            _registered_selftests()
        except ValueError as exc:
            return str(exc)
        return ""

    # Require THIS guard's diagnostic: an unrelated downstream refusal is not
    # evidence that the intended guard ran. Bad commands augment a valid roster.
    for check_id, relative, text, diagnostic in (
            ("roster/empty-local-refused", local, "", local + ": empty registry"),
            ("roster/unparseable-local-refused", local,
             local_text + '\nrun_gate "cont" python3 -I -B tools/check_secrets.py \\\n  --self-test\n',
             local + ": registry diagnostics:"),
            ("roster/empty-opf-refused", opf, binding + "\nexit 0\n",
             opf + ": empty registry"),
            ("roster/standalone-scaffold-refused", opf, binding + "\n" + binding + "\nexit 0\n",
             "unsupported standalone runner scaffold"),
            ("roster/unparseable-ci-refused", ci,
             ci_text + '\n      - run: python3 -I -B tools/check_secrets.py --self-test | cat\n',
             ci + ": registry diagnostics:"),
            ("roster/dynamic-arguments-refused", local,
             local_text + '\nrun_gate "dynamic" python3 -I -B tools/check_secrets.py --self-test --base "$MODE"\n',
             "dynamic self-test arguments:"),
            ("roster/launcher-refused", local,
             local_text + '\nrun_gate "launcher" python3 -B -I tools/check_secrets.py --self-test\n',
             "unsupported self-test launcher:"),
            ("roster/empty-selftests-refused", local,
             'run_gate "live" python3 -I -B tools/check_secrets.py\n',
             local + ": empty self-test roster"),
            ("roster/prefixed-invalid-suite-refused", local,
             local_text + '\nrun_gate "bad-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-selftest --extra\n',
             "unparseable suite invocation:"),
    ):
        def read(path, *args, **kwargs):
            return text if path == ROOT / relative else original(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
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
    def read(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return value + extra if path == ROOT / local else value
    with patch.object(Path, "read_text", read):
        roster = _registered_selftests()
    check("roster/registered-arguments", ("tools/check_secrets.py", "--self-test",
                                         "--red-on-revert") in roster, True)

    suites = check_selftest_execution._manifest_suites(CHECKS_MANIFEST)
    extra = '\nrun_gate "new-suite" python3 -I -B ./tools/check_selftest_execution.py --suite roster-probe\n'
    with patch.object(Path, "read_text", read), patch.object(
            check_selftest_execution, "_manifest_suites", return_value=suites + [
                {"id": "roster-probe", "runner": "tools/check_secrets.py",
                 "expected-check-ids": ["probe"]}]):
        roster = _registered_selftests()
    check("roster/prefixed-suite-expanded",
          ("./tools/check_selftest_execution.py", "--suite", "roster-probe") in roster
          and ("tools/check_secrets.py",) in roster, True)

    extra = '\nrun_gate "recursive-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-selftest\n'
    with patch.object(Path, "read_text", read):
        roster = _registered_selftests()
    check("roster/prefixed-recursion-excluded",
          ("tools/check_selftest_execution.py", "--suite", "git-fixture-env-selftest") not in roster
          and ("./tools/check_selftest_execution.py", "--suite", "git-fixture-env-selftest") not in roster
          and ("tools/selftest_git_fixture_env.py",) not in roster, True)


def _opf_both_legs():
    code = "\n".join((
        "import json, os, sys",
        "from unittest.mock import patch",
        "sys.path.insert(0, sys.argv[1])",
        "import _opf_ingest_apply as module",
        "saved = dict(os.environ)",
        "seen = []",
        "class StopProbe(Exception): pass",
        "def observe():",
        "    home = os.environ.get('HOME')",
        "    seen.append([home != saved.get('HOME'), os.path.isdir(home),",
        "                 home == os.environ.get('XDG_CONFIG_HOME'),",
        "                 os.environ.get('GIT_CONFIG_NOSYSTEM') == '1'])",
        "    return 0",
        "def red():",
        "    observe()",
        "    raise StopProbe()",
        "with patch.object(module, 'self_test', observe), patch.object(module, '_red_on_revert_main', red):",
        "    try: module.main(['--self-test', '--red-on-revert'])",
        "    except StopProbe: pass",
        "print(json.dumps([seen, dict(os.environ) == saved]))",
    ))
    env = dict(os.environ, HOME="/caller-home", XDG_CONFIG_HOME="/caller-xdg",
               GIT_CONFIG_NOSYSTEM="0")
    try:
        child = subprocess.run([sys.executable, "-I", "-B", "-c", code,
                                str(ROOT / "opf" / "tools")], env=env,
                               capture_output=True, text=True, timeout=60)
        try:
            got = (child.returncode, json.loads(child.stdout))
        except ValueError:
            got = (child.returncode, child.stdout, child.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        got = str(exc)
    check("env/opf-ingest-apply-both-legs", got,
          (0, [[[True, True, True, True], [True, True, True, True]], True]))


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
    tree = ast.parse(Path(_git_fixture_env.__file__).read_text(encoding="utf-8"))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(ast.unparse(t) == "os.environ['PATH']" for t in node.targets)]
    if len(assignments) != 1:
        raise ValueError("cannot uniquely mutate lifecycle PATH installation")
    assignments[0].value = ast.parse('saved.get("PATH", os.defpath)', mode="eval").body
    mutant = types.ModuleType("fixture_path_mutant")
    exec(compile(ast.fix_missing_locations(tree), "<path-removal-mutant>", "exec"),
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
    check("config/registered-combined", [argv for argv, value in combined.items()
                                       if value != (0, b"", b"", b"")], [])
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
            ("config/check_opf_import/rc", "config/check_opf_import/hooks",
             "opf/tools/check_opf_import.py"),
            ("config/check_opf_ingest/rc", "config/check_opf_ingest/hooks",
             "opf/tools/check_opf_ingest.py"),
            ("config/_opf_ingest_apply/rc", "config/_opf_ingest_apply/hooks",
             "opf/tools/_opf_ingest_apply.py"),
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
    check("config/registered-malformed", [argv for argv, value in malformed_results.items()
                                        if value != (0, b"", b"", b"")], [])
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
            ("config/check_opf_import/malformed", "opf/tools/check_opf_import.py"),
            ("config/check_opf_ingest/malformed", "opf/tools/check_opf_ingest.py"),
            ("config/_opf_ingest_apply/malformed", "opf/tools/_opf_ingest_apply.py"),
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
    check("config/registered-system", [argv for argv, value in system_results.items()
                                     if value != (0, b"", b"", b"")], [])
    return system_results


def _run_config_member(member, env):
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(ROOT / member[0]), *member[1:]],
                              cwd=ROOT, env=env, capture_output=True, text=True, errors="replace", timeout=1200)
        if proc.returncode:
            print("CONFIG-INJECTION {}:\n{}".format(member, proc.stdout + proc.stderr),
                  file=sys.stderr)
        return proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


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
    tree = ast.parse((ROOT / "tools" / "gen_manifest.py").read_text(encoding="utf-8"))
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
                             "<fixture-setup-probe>", "exec"),
                     {"_git": failed_git, fixture: Path("/unused-fixture")})
            except subprocess.CalledProcessError:
                refused = True
        check(check_id, refused, True)


# These registered self-tests exercise data/text/filesystem fixtures, not a git
# lifecycle. They still require a successful observed system-config-lane run.
# New modules and removed wrappers are NOT implicitly exempt.
OPF_LIFECYCLE_EXEMPTIONS = {
    "opf/tools/_opf_adopt.py": "In-memory adoption vocabulary and validator vectors.",
    "opf/tools/_opf_init.py": "Canonical model bytes, defaults and validator vectors.",
    "opf/tools/_opf_init_contract.py": "KEEP contract validation over synthetic models.",
    "opf/tools/_opf_pack_manifest.py": "Pack parsing and digest vectors over filesystem fixtures.",
    "opf/tools/check_opf_homes.py": "Homes contract and schema boundary vectors.",
    "opf/tools/check_opf_homes_migrate.py": "Homes planning over materialized store fixtures.",
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


def _opf_lifecycle_graph_checks():
    """Mutation D uses the real registered route; rename cases guard discovery."""
    import copy
    names = denial = None
    expected_names = "derivable lifecycle graph"
    expected_denial = "named wrapper bypass"
    try:
        relative = "opf/tools/_opf_ingest_apply.py"
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        module = Path(relative).stem
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
        renamed.body.append(ast.parse("def unrelated_isolated(): pass").body[0])
        expected_names = dict(delegates)
        expected_names[module, entry] = (module, "body_without_a_suffix")
        names = _opf_lifecycle_delegates({module: renamed})

        mutant = copy.deepcopy(tree)
        route = next(node for node in mutant.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == entry]
        if len(calls) != 1:
            raise ValueError("cannot uniquely mutate ingest-apply wrapper route")
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
    missing = set(OPF_LIFECYCLE_EXEMPTIONS) - paths
    for argv in roster:
        if config_results.get(argv) != (0, b"", b"", b""):
            missing.add(_command_identity(argv)[0])
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
                missing.add(relative)
        elif reason is not None:
            missing.add(relative)  # A stale exemption must be reviewed and removed.
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
            ("env/opf-import-home-lifecycle", "check_opf_import", "_self_test"),
            ("env/opf-ingest-home-lifecycle", "check_opf_ingest", "_self_test"),
            ("env/opf-ingest-apply-home-lifecycle", "_opf_ingest_apply", "self_test"),
            ("env/opf-tooling-home-lifecycle", "opf", "run_self_tests"),
    ):
        if (module, entry) not in results:
            problems.append("{}.{}: missing lifecycle observation".format(module, entry))
        check(check_id, results.get((module, entry)), (0, [[[True, True, True, True]], True]))
    check("env/registered-opf-lifecycles", (sorted(missing), problems), ([], []))


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
    return code, or a loud string on a launch failure or timeout."""
    env = _git_fixture_env.git_fixture_env()
    env[poison_key] = poison_value
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(CORPUS_SELFTEST)],
                              cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300)
        return proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return "corpus launch failed: {}".format(exc)


def _run_corpus_imported(poison_key, poison_value, cwd):
    """Run the corpus suite through its IMPORTED surface (run_self_test(), which bypasses
    __main__ and its entry-point scrub) isolated with exactly ONE repository-selecting poison,
    so the fixture-lifecycle scrub in GitTests.setUp must hold on its own. Returns the child's
    return code, or a loud string on a launch failure or timeout."""
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
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(ROOT / "tools")],
                              cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300)
        return proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return "imported corpus launch failed: {}".format(exc)


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


# ---------- layer 4: the repo-wide maintenance-pin completeness scan (F-367) ----------
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
# scanned trees, getattr(subprocess, ...) / getattr(os, ...) except a literal non-launcher
# attribute, subprocess.__dict__ / os.__dict__, vars(subprocess) / vars(os)), a launch with
# **-expanded keywords or an executable= override, an argv or argv head the resolver cannot
# reduce to literals (a helper return, another module's value, a parameterized subcommand
# slot), a tracked argv list mutated in place (a subscript write, insert/remove/pop/clear/
# sort/reverse: the resolved value is invalidated, never trusted), and import/alias spellings
# that would re-spell a launch away from the module-qualified form this scan reads.
# A RESOLVED git launch is judged on its EFFECTIVE argv with git's own LAST-VALUE-WINS config
# semantics (verified on git 2.53): the global-option region is parsed positionally, each F-367
# pin counts only as a `-c key=value` pair in OPTION position (a pin string inside a -m message
# argument never counts), a NO_AUTO_MAINTENANCE splice counts only through its RESOLVED literal
# elements, a LATER `-c` that re-sets a pin key to a non-pin value re-enables maintenance and
# is a finding NO env coverage can absorb (-c outranks every environment-scope pin), and a
# `--config-env` naming a pin key is the same finding (its launch-time value outranks -c). A
# launch whose resolved SUBCOMMAND is not maintenance-triggering is out of scope (a trigger
# word in operand position, `git show commit`, is not a launch of that trigger). A scrub covers
# a launch only when the module actually CALLS it (scrub-first placement in the named entries
# is separately enforced by the layer-2 scope/ checks above); a merely-imported scrub covers
# nothing. An env= is pin-carrying only when it provably derives from git_fixture_env() or
# from a scrubbed os.environ WITHOUT overriding any GIT_CONFIG_COUNT/KEY_n/VALUE_n pin
# variable on the way.
# DISCLOSED RESIDUAL (syntactic bounds, the same stance as the routing checks above): this scan
# reads direct, literal Python launch forms only. Element-level literal resolution stays within
# literal lists, same-scope assignments with their augmented/append/extend growth (in source
# order), module-level constants, and one level of same-module function returns for env=
# values. Out of this scan's reach and NOT flagged: a launcher REFERENCED as a value rather
# than called (an alias `launch = subprocess.run`, a launcher stored in a container, a
# multiprocessing or asyncio target=), a git launch INSIDE a launched script or behind a
# non-git wrapper program (an `env`/`sh`/`bash`/interpreter head ends the analysis at that
# head), an unresolved `-c` VALUE slot, and an unresolved argv tail AFTER the three pins are
# effective in option position (the pinned-funnel idiom passes subcommand and operands there;
# a RESOLVED later re-enable is still caught). Those forms stay covered by review posture, not
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
# The seven process-environment pin variables: an env derivation that overrides ANY of them
# (git_fixture_env(GIT_CONFIG_COUNT="0"), dict(os.environ, GIT_CONFIG_COUNT="0")) disables the
# injected config wholesale, so it can never count as pin-carrying.
_SCAN_PIN_ENV_VARS = frozenset(_git_fixture_env._MAINTENANCE_PIN_VARS)
_SCAN_COVERED_ENV_CALLS = frozenset(("git_fixture_env",))
_SCAN_SCRUB_NAMES = frozenset(("scrub_git_environment", "fixture_git_lifecycle"))
_SCAN_RESOLVE_DEPTH = 6
# Marker for an unknown-length argv region the resolver could not reduce to elements (a helper
# return, a name assigned more than once, an unresolved splice). Distinct from a single opaque
# SLOT (one list element whose VALUE is unresolved, e.g. str(repo)), which stays one argv item.
_SCAN_OPEN = "<unresolved argv region>"
# git GLOBAL options that consume the FOLLOWING argv slot; `--opt=value` spellings are single
# slots and every other `-`-leading token is treated as a bare flag (git itself REJECTS a
# joined `-ckey=value` spelling, verified on git 2.53, so it cannot launch anything). `-c` and
# `--config-env` are handled separately (their values carry, or override, the pins).
_SCAN_GIT_OPTION_ARG = frozenset(("-C", "--git-dir", "--work-tree", "--namespace",
                                  "--super-prefix"))
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
     " checkout's own staging init/add/commit launches beside it carry the three F-367 pins"
     " as literal argv in option position, resolved and enforced by this scan, never"
     " absorbed here"),
    ("tools/selftest_aiqt_hooks.py", "_main_isolated", ("unresolved",),
     "cannot-evaluate head: replays the REGISTERED hook entry's own dispatcher command (a"
     " python3 hook-script argv from the hooks registry), not a git launch; the suite's"
     " lifecycle PATH wrapper pins any git a hook child resolves through PATH"),
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
    ("opf/tools/_opf_pack_manifest.py", "_runner_red_checks.checked_environment", ("os",),
     "cannot-evaluate pass-through: the credential-red-check re-invokes the SAVED"
     " subprocess.Popen inside its patched side_effect after asserting the launch"
     " environment; the argv is the runner check's own bash runner-script launch, not git"),
    ("opf/tools/check_opf_init_p0.py", "runner_red_checks.checked_environment", ("os",),
     "cannot-evaluate pass-through: the credential-red-check re-invokes the SAVED"
     " subprocess.Popen inside its patched side_effect after asserting the launch"
     " environment; the argv is the runner check's own bash runner-script launch, not git"),
    # In-process exec loaders and mutant builders (the dynamic tripwire), each audited: each
    # exec compiles THIS repo's own tracked source (or an AST/text mutant of it) into a module
    # object, in-process; none launches a process itself, and any launch the executed source
    # spells is scanned at its own source location like every other launch.
    ("tools/selftest_git_fixture_env.py", "_system_pin_checks", ("dynamic",),
     "exec of an AST-built PATH-removal mutant of the lifecycle helper's own source, for the"
     " wrapper red leg"),
    ("tools/selftest_git_fixture_env.py", "_manifest_extra_setup_failures", ("dynamic",),
     "exec of one registry-derived fixture-setup expression against a stubbed _git, proving"
     " the setup guard refuses a failed git"),
    ("opf/tools/check_opf_init_observe.py", "load_candidate", ("dynamic",),
     "exec-based loader for the copied candidate module under test"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.candidate", ("dynamic",),
     "exec-based builder for the shared-capture candidate module under test"),
    ("opf/tools/check_opf_init_observe.py", "shared_tests.policy", ("dynamic",),
     "exec-based builder for the policy candidate module under test"),
    ("opf/tools/check_opf_homes_migrate.py", "reverted_evidence_rule", ("dynamic",),
     "exec of the production function's source with one anchor reverted (red-leg mutant)"),
    ("opf/tools/check_opf_homes_migrate.py", "reverted_archive_order", ("dynamic",),
     "exec of the production function's source with the sort reverted (red-leg mutant)"),
    ("opf/tools/_opf_ingest_apply.py", "_load_revert_candidate", ("dynamic",),
     "exec-based loader compiling a revert candidate's source into a fresh module"),
    ("opf/tools/check_opf_init_p0.py", "red_on_revert", ("dynamic",),
     "exec of the production module's source with one guard reverted (red-leg mutant)"),
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
    """Module-level single-target assignments, name to value node."""
    consts = dict()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value
    return consts


def _scan_function_defs(tree):
    """Every function definition in the module, by bare name (any nesting), for the bounded
    return-value resolution of env= expressions."""
    defs = dict()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    return defs


def _scan_local_assigns(func_node, name):
    """(plain assignment value nodes, splice extension value nodes, mutated) for Name <name>
    in the function's own scope, in SOURCE order (an extend that adds the pins before the
    extend that adds the subcommand must be read in exactly that order, never reversed);
    nested function and class bodies are other scopes and are skipped. `mutated` is True when
    the scope also rewrites the value in place through a form the resolver does not model (a
    subscript or slice write, an insert/remove/pop/clear/sort/reverse call, a del): any
    resolved value is then invalidated, never trusted."""
    plain, extend, mutated = [], [], False
    if func_node is None:
        return plain, extend, mutated
    hits = []
    stack = list(func_node.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                hits.append((node.lineno, node.col_offset, "plain", node.value))
            if any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                   and t.value.id == name for t in node.targets):
                mutated = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name \
                    and node.value is not None:
                hits.append((node.lineno, node.col_offset, "plain", node.value))
            elif isinstance(node.target, ast.Subscript) \
                    and isinstance(node.target.value, ast.Name) \
                    and node.target.value.id == name:
                mutated = True
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                hits.append((node.lineno, node.col_offset, "extend", node.value))
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
                                    "__setitem__", "__delitem__"):
                mutated = True
        stack.extend(ast.iter_child_nodes(node))
    for _, _, kind, value in sorted(hits, key=lambda hit: (hit[0], hit[1])):
        (plain if kind == "plain" else extend).append(value)
    return plain, extend, mutated


def _scan_flatten(expr, func_node, module_consts, depth):
    """(ordered argv entries, open) within the resolver's bounds: literal lists and tuples,
    + concatenation, list()/tuple() wrapping, starred splices, and Name resolution through a
    single same-scope assignment (with its append/extend growth as tail entries) or a
    module-level literal. Each entry is a single-slot AST node, or the _SCAN_OPEN marker where
    an unknown-length region the resolver cannot reduce sits, so a POSITIONAL reading knows
    exactly where its knowledge ends (a marker at the head hides the program; a marker after
    the pins hides nothing that could re-enable maintenance)."""
    if depth <= 0:
        return [_SCAN_OPEN], True
    if isinstance(expr, (ast.List, ast.Tuple)):
        entries, opened = [], False
        for elt in expr.elts:
            if isinstance(elt, ast.Starred):
                inner, inner_open = _scan_flatten(elt.value, func_node, module_consts, depth - 1)
                entries.extend(inner)
                opened = opened or inner_open
            else:
                entries.append(elt)
        return entries, opened
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left, lopen = _scan_flatten(expr.left, func_node, module_consts, depth - 1)
        right, ropen = _scan_flatten(expr.right, func_node, module_consts, depth - 1)
        return left + right, lopen or ropen
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
            and expr.func.id in ("list", "tuple") and len(expr.args) == 1 and not expr.keywords:
        return _scan_flatten(expr.args[0], func_node, module_consts, depth - 1)
    if isinstance(expr, ast.Name):
        plain, extend, mutated = _scan_local_assigns(func_node, expr.id)
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
        entries, opened = _scan_flatten(plain[0], func_node, module_consts, depth - 1)
        for value in extend:
            more, more_open = _scan_flatten(value, func_node, module_consts, depth - 1)
            entries.extend(more)
            opened = opened or more_open
        return entries, opened
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) \
            and expr.value.id in ("self", "cls"):
        # A class-level constant (the scan loop merges the innermost enclosing class's
        # single-target assignments into the consts mapping under "self."/"cls." keys).
        target = expr.value.id + "." + expr.attr
        if target in module_consts:
            return _scan_flatten(module_consts[target], func_node, module_consts, depth - 1)
    return [_SCAN_OPEN], True


def _scan_program_kind(value):
    """bare-git / abs-git / non-git for a literal program string."""
    if value == "git":
        return "bare-git"
    if value.endswith("/git"):
        return "abs-git"
    return "non-git"


def _scan_head_kind(node, func_node, module_consts, depth):
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
            return _scan_head_kind(node.args[0], func_node, module_consts, depth - 1)
        return "unknown"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # A pathlib join: the RIGHTMOST component names the program.
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return "abs-git" if node.right.value == "git" else "non-git"
        return "unknown"
    if isinstance(node, ast.Name):
        plain, _, mutated = _scan_local_assigns(func_node, node.id)
        if mutated:
            return "unknown"
        if not plain and node.id in module_consts:
            plain = [module_consts[node.id]]
        kinds = set(_scan_head_kind(value, func_node, module_consts, depth - 1)
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
                                       depth - 1)
        if node.attr == "executable":
            return "non-git"
        if node.attr in _SCAN_GIT_HEAD_NAMES:
            return "named-git"
        return "unknown"
    return "unknown"


def _scan_element_literal(node, func_node, module_consts, depth):
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
        plain, extend, mutated = _scan_local_assigns(func_node, node.id)
        if not plain and node.id in module_consts:
            plain = [module_consts[node.id]]
        if len(plain) == 1 and not extend and not mutated:
            return _scan_element_literal(plain[0], func_node, module_consts, depth - 1)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id in ("self", "cls"):
        target = node.value.id + "." + node.attr
        if target in module_consts:
            return _scan_element_literal(module_consts[target], func_node, module_consts,
                                         depth - 1)
    return None


def _scan_git_argv_state(entries, func_node, module_consts):
    """(status, subcommand) for a git-headed argv parsed POSITIONALLY through git's
    global-option region with git's own LAST-VALUE-WINS config semantics (verified on git
    2.53: a later `-c gc.auto=6700` overrides an earlier `-c gc.auto=0`, and a `--config-env`
    value outranks a `-c` for the same key): 'pinned' when every F-367 pin key's LAST resolved
    option-position value is the pin value at the point the parse ends - the resolved
    subcommand, or the start of an unresolved region (a tail is trusted only AFTER effective
    pins, the pinned-funnel idiom's subcommand-and-operands slot; a RESOLVED later re-enable
    never survives this parse); 'stomped' when a pin key's LAST resolved value is NOT the pin
    value, or a `--config-env` names a pin key (its launch-time value outranks -c and every
    environment-scope pin, so no env coverage can absorb it); 'subcommand' with the resolved
    subcommand token when the pins are not effective; 'opaque' when an unknown-length region,
    an unresolved slot, an unreadable or alias-remapping `-c` value, or a truncated option
    reaches the parser before the pins are effective; 'end' for a fully resolved argv that
    never reaches a subcommand."""
    pin_state = dict()

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
        if entry is not _SCAN_OPEN:
            token = _scan_element_literal(entry, func_node, module_consts,
                                          _SCAN_RESOLVE_DEPTH)
        if token is None:
            return _verdict(None) or ("opaque", None)
        if token == "-c":
            if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                return "opaque", None
            value = _scan_element_literal(entries[index + 1], func_node, module_consts,
                                          _SCAN_RESOLVE_DEPTH)
            if value is None:
                return "opaque", None
            key = value.partition("=")[0].lower()
            if key.startswith("alias."):
                # A command-scope alias can remap ANY later word to another subcommand.
                return "opaque", None
            if key in _SCAN_PIN_VALUES:
                # Track the LAST value per pin key, exactly as git will apply it.
                pin_state[key] = value.partition("=")[2]
            index += 2
            continue
        if token == "--config-env" or token.startswith("--config-env="):
            if token == "--config-env":
                if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                    return "opaque", None
                value = _scan_element_literal(entries[index + 1], func_node, module_consts,
                                              _SCAN_RESOLVE_DEPTH)
                step = 2
            else:
                value = token.partition("=")[2]
                step = 1
            if value is None:
                return "opaque", None
            key = value.partition("=")[0].lower()
            if key in _SCAN_PIN_VALUES:
                # The effective value is an environment VARIABLE read at launch time: this
                # scan cannot see it, and it outranks -c, so the pin is gone either way.
                return "stomped", None
            if key.startswith("alias."):
                return "opaque", None
            index += step
            continue
        if token in _SCAN_GIT_OPTION_ARG:
            # Consumes exactly the following slot (its value may stay opaque: one slot).
            if index + 1 >= len(entries) or entries[index + 1] is _SCAN_OPEN:
                return "opaque", None
            index += 2
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        return _verdict(token) or ("subcommand", token)
    return "end", None


def _scan_calls_module(tree, wanted):
    """True when the module contains an actual CALL of one of <wanted> (a bare name or an
    attribute). A merely-imported or merely-referenced scrub name proves nothing about the
    launch environment, so it covers nothing."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in wanted:
            return True
    return False


def _scan_kw_stomps_pins(keywords):
    """True when a call's keyword overrides could rewrite an F-367 pin variable: an explicit
    GIT_CONFIG_COUNT/KEY_n/VALUE_n keyword (git_fixture_env(GIT_CONFIG_COUNT="0") and
    dict(os.environ, GIT_CONFIG_COUNT="0") each disable EVERY env pin at the launch, verified
    on git 2.53) or a **-expansion this scan cannot read."""
    for keyword in keywords:
        if keyword.arg is None or keyword.arg in _SCAN_PIN_ENV_VARS:
            return True
    return False


def _scan_env_covered(expr, func_node, module_consts, func_defs, module_scrubbed, depth):
    """True when the env= expression provably derives from a pin-carrying source: a
    git_fixture_env(...) call (directly, through dict()/copy() derivation, a same-scope name,
    or one level of same-module function returns), or os.environ in a module that CALLS the
    in-place scrub or the lifecycle wrapper (both leave the pins set in os.environ) - in every
    case only when no keyword override on the way rewrites a pin variable
    (_scan_kw_stomps_pins): git_fixture_env(GIT_CONFIG_COUNT="0") and
    dict(os.environ, GIT_CONFIG_COUNT="0") carry NO effective pins."""
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
                                     module_scrubbed, depth - 1)
        if callee_name == "copy" and isinstance(callee, ast.Attribute):
            return _scan_env_covered(callee.value, func_node, module_consts, func_defs,
                                     module_scrubbed, depth - 1)
        returns = []
        for definition in func_defs.get(callee_name, []):
            for node in ast.walk(definition):
                if isinstance(node, ast.Return) and node.value is not None:
                    returns.append((node.value, definition))
        if returns and all(
                _scan_env_covered(value, definition, module_consts, func_defs,
                                  module_scrubbed, depth - 1)
                for value, definition in returns):
            return True
        return False
    if isinstance(expr, ast.Name):
        plain, _, mutated = _scan_local_assigns(func_node, expr.id)
        if mutated:
            return False
        if not plain and expr.id in module_consts:
            plain = [module_consts[expr.id]]
        return bool(plain) and all(
            _scan_env_covered(value, func_node, module_consts, func_defs,
                              module_scrubbed, depth - 1) for value in plain)
    if isinstance(expr, ast.Attribute):
        return (expr.attr == "environ" and isinstance(expr.value, ast.Name)
                and expr.value.id == "os" and module_scrubbed)
    if isinstance(expr, ast.IfExp):
        return all(_scan_env_covered(branch, func_node, module_consts, func_defs,
                                     module_scrubbed, depth - 1)
                   for branch in (expr.body, expr.orelse))
    return False


def _scan_launches(tree):
    """(node, flavor, launcher, dotted qualname, enclosing function node, innermost enclosing
    class node) for every launch-shaped or tripwire-shaped node in the module: direct
    subprocess.<launcher>(...) calls (flavor 'subprocess'), os-level launcher calls by any
    object spelling (flavor 'os', pty.spawn included), asyncio create_subprocess_* calls by
    any object spelling (flavor 'async'), and dynamic launcher access (flavor 'dynamic'):
    exec/eval anywhere, getattr(subprocess, ...) / getattr(os, ...) except a literal
    NON-launcher attribute (getattr(os, "O_NOFOLLOW", 0) reads a constant, not a launcher),
    subprocess.__dict__ / os.__dict__, and vars(subprocess) / vars(os)."""
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
                    found.append((child, "subprocess", func.attr, qualname, func_node,
                                  class_node))
                else:
                    name = func.attr if isinstance(func, ast.Attribute) else (
                        func.id if isinstance(func, ast.Name) else None)
                    if name in _SCAN_OS_LAUNCH_NAMES:
                        found.append((child, "os", name, qualname, func_node, class_node))
                    elif name in _SCAN_ASYNC_LAUNCH_NAMES:
                        found.append((child, "async", name, qualname, func_node, class_node))
                    elif isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                        found.append((child, "dynamic", func.id, qualname, func_node,
                                      class_node))
                    elif isinstance(func, ast.Name) and func.id == "getattr" \
                            and child.args and isinstance(child.args[0], ast.Name) \
                            and child.args[0].id in ("subprocess", "os"):
                        module = child.args[0].id
                        launchers = (_SCAN_LAUNCH_NAMES if module == "subprocess"
                                     else _SCAN_OS_LAUNCH_NAMES)
                        harmless = (len(child.args) >= 2
                                    and isinstance(child.args[1], ast.Constant)
                                    and isinstance(child.args[1].value, str)
                                    and child.args[1].value not in launchers)
                        if not harmless:
                            found.append((child, "dynamic", "getattr on " + module,
                                          qualname, func_node, class_node))
                    elif isinstance(func, ast.Name) and func.id == "vars" \
                            and child.args and isinstance(child.args[0], ast.Name) \
                            and child.args[0].id in ("subprocess", "os"):
                        found.append((child, "dynamic", "vars(" + child.args[0].id + ")",
                                      qualname, func_node, class_node))
            elif isinstance(child, ast.Attribute) and child.attr == "__dict__" \
                    and isinstance(child.value, ast.Name) \
                    and child.value.id in ("subprocess", "os"):
                qualname = ".".join(parts) if parts else "<module>"
                found.append((child, "dynamic", child.value.id + ".__dict__", qualname,
                              func_node, class_node))
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


def _maintenance_pin_scan(root, allow_missing_files=False):
    """The sorted findings of the repo-wide F-367 tripwire scan under <root>: [] means every
    launch under _SCAN_DIRS this scan can resolve to a maintenance-triggering git run is
    effectively pinned or covered, every launch form it CANNOT resolve is justified for
    exactly its launch KIND, and no justification is stale. Unreadable or unparseable sources
    are loud findings, never silent passes. Stale allowlist enforcement covers unused entries
    for scanned files AND entries whose file is missing (deleted or renamed);
    allow_missing_files=True relaxes ONLY the latter, for planted synthetic trees that do not
    carry the real allowlisted files. The real-tree green leg runs strict."""
    findings = []
    allowed = dict()
    for rel, qualname, kinds, _justification in _SCAN_ALLOWED_UNPINNED:
        allowed[(rel, qualname)] = frozenset(kinds)
    used, scanned, scanned_bases = set(), set(), []

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
            module_scrubbed = _scan_calls_module(tree, _SCAN_SCRUB_NAMES)
            module_lifecycle = _scan_calls_module(tree,
                                                  frozenset(("fixture_git_lifecycle",)))
            class_consts = dict()
            for call, flavor, launcher, qualname, func_node, class_node \
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
                entries, _opened = _scan_flatten(argv, func_node, consts,
                                                 _SCAN_RESOLVE_DEPTH)
                head = _scan_head_kind(entries[0] if entries else None, func_node,
                                       consts, _SCAN_RESOLVE_DEPTH)
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
                status, subcommand = _scan_git_argv_state(entries, func_node, consts)
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
                if has_env and _scan_env_covered(env_expr, func_node, consts,
                                                 func_defs, module_scrubbed,
                                                 _SCAN_RESOLVE_DEPTH):
                    continue
                if not has_env and module_scrubbed:
                    continue
                if absorbed(key, "git-triggering" if status == "subcommand" else "git"):
                    continue
                if head == "bare-git" and module_lifecycle and not (
                        has_env and _scan_env_defeats_wrapper(env_expr, func_node,
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


# Each entry: (case id, planted files as (relative path, source) pairs, expectation). An
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
)


def _scan_contract_failures(base):
    """The failures of the layer-4 contract table over isolated planted trees: every launch
    form round-2 or round-3 review showed the scan silently accepting must be a loud finding,
    every harmless or correctly-protected form it showed wrongly flagged must stay clean, and
    the stale enforcement must fire for a deleted or renamed allowlisted file. [] is the
    passing value; a failing case reports its planted tree's actual findings, loudly, never a
    silent pass."""
    failures = []
    for case_id, files, expect in _SCAN_CONTRACT_CASES:
        root = base / case_id
        (root / "tools").mkdir(parents=True)
        (root / "opf" / "tools").mkdir(parents=True)
        for rel, source in files:
            (root / rel).write_text(source, encoding="utf-8")
        got = _maintenance_pin_scan(root,
                                    allow_missing_files=(case_id != "stale-missing"))
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


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump({"format_version": 1, "suite": SUITE_ID, "check_ids": EXECUTED}, handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report {}: {}".format(
            report_path, exc), file=sys.stderr)
        return False
    return True


def main(report_path=None):
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

        # Layer 4, the class-completeness leg: the repo-wide scan must report NO unpinned
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
        _opf_both_legs()
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
    finally:
        shutil.rmtree(raw, ignore_errors=True)

    if not _write_report(report_path):
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
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
    sys.exit(main(_parse_argv(sys.argv[1:])))
