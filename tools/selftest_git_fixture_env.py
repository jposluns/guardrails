#!/usr/bin/env python3
"""Behavioural self-test for tools/_git_fixture_env.py and its routing across the fixture class.

An inherited repository-selecting git variable (GIT_INDEX_FILE, which git itself exports to hook
children; GIT_DIR; GIT_WORK_TREE; the GIT_CONFIG_* family; ...) redirects a self-test fixture's
git init/add/commit into the CALLER's repository. This suite locks the fix in three layers. First,
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
leak cannot silently return.

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
    # The valid failure-state initializers, so an empty-roster fixture reaches
    # its own guard rather than the missing-initializer diagnostic.
    state = 'failed=0\nfailed_names=""\n'

    def add_local(command):
        # Keep the exact terminal summary last so each fixture reaches its own guard.
        summary = 'if [ "$failed" -ne 0 ]; then\n'
        assert local_text.count(summary) == 1
        return local_text.replace(summary, command + "\n" + summary, 1)

    def refusal():
        try:
            _registered_selftests()
        except ValueError as exc:
            return str(exc)
        return ""

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
             state + 'run_gate "live" python3 -I -B tools/check_secrets.py\n',
             local + ": empty self-test roster"),
            ("roster/prefixed-invalid-suite-refused", local,
             add_local('\nrun_gate "bad-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-selftest --extra\n'),
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
        return add_local(extra) if path == ROOT / local else value
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
    for args in (("init", "-q"), ("add", "-f", "seed", "survivor", ".gitattributes"),
                 ("-c", "user.name=Selftest", "-c", "user.email=selftest@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "control")):
        subprocess.run(["git", "-C", str(control), *args], env=env, check=True,
                       capture_output=True, timeout=60)
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
        archive = subprocess.run(["git", "-C", str(control), *args, "archive", "HEAD"],
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
              (0, [["GIT_CONFIG_GLOBAL", os.devnull], ["GIT_CONFIG_NOSYSTEM", "1"],
                   ["GIT_CONFIG_SYSTEM", os.devnull]], True))

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
