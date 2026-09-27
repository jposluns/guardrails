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
end-to-end probes prove execution for the corpus member; the other members' fixture behaviour rides
their own self-tests. The trust check covers LITERAL subprocess.run launches (a launch built
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
    for stmt in owner.body:
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
                ("route/check-opf-init", "opf/tools/check_opf_init.py",
                 ("_scrubbed_env",)),
                ("route/selftest-aiqt-hooks", "tools/selftest_aiqt_hooks.py",
                 ("scrub_git_environment",)),
        ):
            check(check_id, _calls_any(ROOT / member_rel, scrub_names), True)

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
