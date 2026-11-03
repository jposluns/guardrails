#!/usr/bin/env python3
"""Site fast-lane scope decision for the Quality workflow (SITE-FASTLANE).

The maintainer's recorded decision (2026-11-03) authorizes skipping the Quality workflow's heavy
jobs when, and only when, every path a pull request changes is in the site allowlist: under site/
or opf/site/, or one of the three files a site-only change regenerates (.aiqt/manifest.toml,
.aiqt/release/root.txt, .aiqt/release/announce-snippet.txt). This tool makes that decision and
nothing else; the scope job of .github/workflows/quality.yml runs it on a pull request only, and
the Quality gates job passes a skipped heavy job only when this decision read true.

The decision is a pure function over the changed-path list, fail-closed: an EMPTY list, any path
outside the allowlist, a path that is not a plain repo-relative file path (absolute, empty, parent
climbing, a backslash, a trailing slash, or not a string), or any error reading the diff decides
false, the full suite. The allowlisted trees admit only paths strictly below them, so the bare
directory names "site" and "site/" decide false. A rename out of an allowlisted tree is caught
because the diff is read with rename detection off (--no-renames): both sides of a rename appear
as a delete and an add, and the non-site side decides false.

The CLI reads the changed paths from git against the merge base it is given (the workflow passes
the pull request's base commit) with: git diff --name-only --no-renames -z <merge-base> HEAD.
It prints exactly one line, site_only=true or site_only=false, and exits 0 only on a clean
decision; it exits 2 on cannot-evaluate (a bad revision, a git failure, undecodable path bytes, a
missing git), which the workflow treats as site_only=false.

The scope job runs --ci-decide, the one entry the workflow's parity grammar admits as a single
command line: it reads EVENT_NAME, BASE_SHA and GITHUB_OUTPUT from the environment, appends
site_only=true or site_only=false to GITHUB_OUTPUT, and exits 0; any event but pull_request, and
any cannot-evaluate from the diff read, writes false, so the full suite runs. It exits nonzero
only when it cannot write the decision at all, which fails the scope job and so fails Quality
gates (fail-closed, never a silent fast lane).

Usage:
  ci_site_scope.py --merge-base REV        decide against REV, print site_only=true|false
  ci_site_scope.py --ci-decide             the scope job entry (environment-driven, above)
  ci_site_scope.py --self-test             the self-test
  ci_site_scope.py --execution-report ABS  the self-test, also writing the executed check ids
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: ci_site_scope.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: ci_site_scope.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_CANNOT_EVALUATE = 0, 2
GIT_TIMEOUT = 60

# The site allowlist (SITE-FASTLANE): the two site trees, strictly below each prefix, and the
# exact files a site-only change regenerates. Module globals, so the self-test's revert cases can
# patch each rule out and observe its vector flip.
SITE_TREES = ("site/", "opf/site/")
SITE_REGENERATED = frozenset({
    ".aiqt/manifest.toml",
    ".aiqt/release/root.txt",
    ".aiqt/release/announce-snippet.txt",
})
# Site-tree files a HEAVY suite reads as a verdict input, enumerated by the gate-discipline
# coverage account recorded with this change: opf/tools/check_opf_homes.py (the opf-tooling-a job)
# reads the five OPF homes contract pages, and tools/check_opf_declaration.py (the
# selftest-execution job) reads opf/site/tooling.html. A change to one of these could change a
# skipped job's result, so the allowlist excludes them and such a change runs the full suite.
SITE_HEAVY_READ = frozenset({
    "opf/site/adopt.html",
    "opf/site/adopt.md",
    "opf/site/core-files.html",
    "opf/site/disclosure.html",
    "opf/site/record-indexes.html",
    "opf/site/tooling.html",
})
REQUIRE_NONEMPTY = True


def _plain_relative(path):
    """True only for a plain repo-relative file path: a non-empty str, no absolute or drive form,
    no backslash, no empty segment, and no . or .. segment."""
    if not isinstance(path, str) or not path:
        return False
    if path.startswith("/") or "\\" in path or ":" in path.split("/", 1)[0]:
        return False
    segments = path.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            return False
    return True


def _allowed(path):
    """True only for a path the site allowlist admits: strictly below one of SITE_TREES and not a
    file a heavy suite reads, or one of SITE_REGENERATED exactly."""
    if path in SITE_HEAVY_READ:
        return False
    for prefix in SITE_TREES:
        if path.startswith(prefix) and len(path) > len(prefix):
            return True
    return path in SITE_REGENERATED


def site_only(paths):
    """The pure decision: True only when paths is a non-empty list of plain repo-relative paths,
    every one admitted by the allowlist. Anything else decides False (the full suite)."""
    try:
        items = list(paths)
    except TypeError:
        return False
    if REQUIRE_NONEMPTY and not items:
        return False
    for path in items:
        if not _plain_relative(path):
            return False
        if not _allowed(path):
            return False
    return True


def changed_paths(root, merge_base):
    """(paths, None) read from git against merge_base, or (None, reason) on any error. Rename
    detection is off, so both sides of a rename appear; -z gives exact NUL-separated paths."""
    if not isinstance(merge_base, str) or not merge_base or merge_base.startswith("-"):
        return None, "merge base must be a non-empty revision, not an option"
    argv = ["git", "-C", str(root), "diff", "--name-only", "--no-renames", "-z",
            merge_base, "HEAD", "--"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "cannot run git: {}".format(exc)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, "git diff exited {}: {}".format(proc.returncode, tail[-1] if tail else "")
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "undecodable path bytes in the diff: {}".format(exc)
    return [item for item in text.split("\0") if item], None


def decide(root, merge_base):
    """The CLI decision: print site_only=true|false; exit 0 on a clean decision, 2 otherwise."""
    paths, problem = changed_paths(root, merge_base)
    if problem is not None:
        print("cannot evaluate: {}".format(problem), file=sys.stderr)
        print("site_only=false")
        return EXIT_CANNOT_EVALUATE
    print("site_only=true" if site_only(paths) else "site_only=false")
    return EXIT_OK


def ci_decide():
    """The scope job entry: read EVENT_NAME, BASE_SHA and GITHUB_OUTPUT from the environment,
    append site_only=true or site_only=false to GITHUB_OUTPUT, print the decision, exit 0. Any
    event but pull_request, and any cannot-evaluate, decides false (the full suite). Exits 1 only
    when the decision cannot be written at all, which fails the scope job."""
    output = os.environ.get("GITHUB_OUTPUT", "")
    if not output:
        print("cannot write the decision: GITHUB_OUTPUT is not set", file=sys.stderr)
        return 1
    event = os.environ.get("EVENT_NAME", "")
    verdict = False
    if event != "pull_request":
        note = "event {!r} never takes the fast lane".format(event)
    else:
        paths, problem = changed_paths(ROOT, os.environ.get("BASE_SHA", ""))
        if problem is not None:
            note = "cannot evaluate, treated as false: {}".format(problem)
        else:
            verdict = site_only(paths)
            note = "clean decision over {} changed paths".format(len(paths))
    line = "site_only=true" if verdict else "site_only=false"
    try:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print("cannot write the decision to GITHUB_OUTPUT: {}".format(exc), file=sys.stderr)
        return 1
    print("{} ({})".format(line, note))
    return 0


# SELF-TEST: production code ends here. Every fixture below is synthetic. Each case asserts
# through the instrumented check() choke point, and the executed id set is reconciled against
# tools/selftest_checks.toml (suite ci-site-scope-selftest), both in-run and by
# tools/check_selftest_execution.py. The revert/ cases patch one rule out of this module's
# globals (no code is generated or evaluated) and require its fixture to flip.

SUITE_ID = "ci-site-scope-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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


def _patched(**replacements):
    """Replace module-level names (a rule patched out) for the duration of the block, then
    restore them."""
    import contextlib

    @contextlib.contextmanager
    def manager():
        names, absent = globals(), object()
        saved = dict((name, names.get(name, absent)) for name in replacements)
        names.update(replacements)
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is absent:
                    del names[name]
                else:
                    names[name] = value
    return manager()


def _git_env():
    """A scrubbed environment for fixture git: no inherited GIT_* variable, no user or system
    config, a fixed identity, and the PATH needed to find git."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "fixture"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "fixture@example.invalid"
    return env


def _fixture_git(path, *args, env):
    # The maintenance pins ride the argv in option position, so a fixture command can never spawn
    # a detached maintenance child that outlives the fixture directory.
    subprocess.run(["git", "-C", str(path), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                    "-c", "maintenance.auto=false", *args], check=True, capture_output=True,
                   timeout=GIT_TIMEOUT, env=env)


def _plant_tool(repo):
    """Copy this tool and its exit-report sibling into repo/tools, so the planted copy's own ROOT
    is the fixture repo and the CLI runs there exactly as the workflow runs it here."""
    here = Path(__file__).resolve().parent
    os.makedirs(repo / "tools", exist_ok=True)
    shutil.copy(here / "ci_site_scope.py", repo / "tools" / "ci_site_scope.py")
    shutil.copy(here / "_selftest_exit_report.py", repo / "tools" / "_selftest_exit_report.py")


def _fixture_repo(base, env):
    """A synthetic repo: one base commit holding a site file, a tools file, the three regenerated
    records, and a planted copy of this tool. Returns (repo path, base revision)."""
    repo = base / "repo"
    os.makedirs(repo / "site")
    os.makedirs(repo / "opf" / "site")
    os.makedirs(repo / ".aiqt" / "release")
    os.makedirs(repo / "tools")
    _plant_tool(repo)
    (repo / "site" / "index.html").write_text("a\n")
    (repo / "opf" / "site" / "a.css").write_text("a\n")
    (repo / ".aiqt" / "manifest.toml").write_text("a\n")
    (repo / ".aiqt" / "release" / "root.txt").write_text("a\n")
    (repo / ".aiqt" / "release" / "announce-snippet.txt").write_text("a\n")
    (repo / "tools" / "t.py").write_text("a\n")
    _fixture_git(repo, "init", "-q", "-b", "main", env=env)
    _fixture_git(repo, "add", "-A", env=env)
    _fixture_git(repo, "commit", "-q", "-m", "base", env=env)
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    return repo, out.stdout.strip()


def _cli(repo, merge_base):
    """Run the planted CLI copy as a child exactly as the workflow does; (exit code, stdout)."""
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(repo / "tools" / "ci_site_scope.py"),
         "--merge-base", merge_base],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=str(repo), env=_git_env())
    return proc.returncode, proc.stdout.strip()


def _cli_outcome(repo, merge_base):
    rc, out = _cli(repo, merge_base)
    return rc, out.splitlines()[-1] if out else ""


def _ci_decide_outcome(repo, event, base, output):
    """Run the planted copy's --ci-decide with a controlled environment; (exit code, what the
    output file holds afterwards, or None when it does not exist)."""
    env = _git_env()
    env.pop("GITHUB_OUTPUT", None)
    if event is not None:
        env["EVENT_NAME"] = event
    if base is not None:
        env["BASE_SHA"] = base
    if output is not None:
        env["GITHUB_OUTPUT"] = str(output)
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(repo / "tools" / "ci_site_scope.py"), "--ci-decide"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=str(repo), env=env)
    recorded = None
    if output is not None and os.path.isfile(str(output)):
        with open(str(output), encoding="utf-8") as handle:
            recorded = handle.read()
    return proc.returncode, recorded


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    expected = _expected_check_ids()
    if expected is None:
        return 2
    # ---------- the pure decision, every branch ----------
    check("decide/empty-list-false", site_only([]), False)
    check("decide/not-iterable-false", site_only(None), False)
    check("decide/site-file-true", site_only(["site/index.html"]), True)
    check("decide/site-nested-true", site_only(["site/a/b/c.js"]), True)
    check("decide/opf-site-file-true", site_only(["opf/site/a.css"]), True)
    check("decide/regen-manifest-true", site_only([".aiqt/manifest.toml"]), True)
    check("decide/regen-root-true", site_only([".aiqt/release/root.txt"]), True)
    check("decide/regen-announce-true", site_only([".aiqt/release/announce-snippet.txt"]), True)
    check("decide/every-allowlisted-true",
          site_only(["site/index.html", "opf/site/a.css", ".aiqt/manifest.toml",
                     ".aiqt/release/root.txt", ".aiqt/release/announce-snippet.txt"]), True)
    check("decide/non-site-path-false", site_only(["tools/t.py"]), False)
    check("decide/one-outside-poisons-false", site_only(["site/index.html", "tools/t.py"]), False)
    # the prefix boundary: just inside, exactly at, and just outside each tree
    check("decide/tree-boundary",
          [site_only(["site/x"]), site_only(["site/"]), site_only(["site"]),
           site_only(["sitex/a"]), site_only(["site.html"])],
          [True, False, False, False, False])
    check("decide/opf-tree-boundary",
          [site_only(["opf/site/x"]), site_only(["opf/site/"]), site_only(["opf/site"]),
           site_only(["opf/sitex/a"])],
          [True, False, False, False])
    # the six site-tree pages a heavy suite reads as a verdict input are excluded
    check("decide/heavy-read-page-false",
          [site_only([path]) for path in sorted(SITE_HEAVY_READ)], [False] * 6)
    check("decide/heavy-read-poisons-false",
          site_only(["site/index.html", "opf/site/tooling.html"]), False)
    # the regenerated records match exactly, never as a prefix or a sibling
    check("decide/regen-exact-only",
          [site_only([".aiqt/manifest.toml.bak"]), site_only([".aiqt/release/root.txt2"]),
           site_only([".aiqt/release/"]), site_only([".aiqt/manifest.tom"])],
          [False, False, False, False])
    # path shapes that are not plain repo-relative file paths
    check("decide/absolute-false", site_only(["/site/index.html"]), False)
    check("decide/parent-climb-false", site_only(["site/../tools/t.py"]), False)
    check("decide/dot-segment-false", site_only(["site/./a"]), False)
    check("decide/empty-segment-false", site_only(["site//a"]), False)
    check("decide/backslash-false", site_only(["site\\a"]), False)
    check("decide/drive-colon-false", site_only(["c:/site/a"]), False)
    check("decide/empty-string-false", site_only([""]), False)
    check("decide/non-string-false", site_only([b"site/a"]), False)
    # ---------- revert cases: each rule patched out flips its vector ----------
    with _patched(_allowed=lambda path: True):
        check("revert/allowlist-dropped-red", site_only(["tools/t.py"]), True)
    with _patched(REQUIRE_NONEMPTY=False):
        check("revert/nonempty-rule-dropped-red", site_only([]), True)
    with _patched(_plain_relative=lambda path: isinstance(path, str) and bool(path)):
        check("revert/path-shape-rule-dropped-red", site_only(["site/../site/a"]), True)
    with _patched(SITE_TREES=("site/", "opf/site/", "tools/")):
        check("revert/tree-widened-red", site_only(["tools/t.py"]), True)
    with _patched(SITE_REGENERATED=frozenset({".aiqt/manifest.toml", "VERSION"})):
        check("revert/regenerated-widened-red", site_only(["VERSION"]), True)
    with _patched(SITE_HEAVY_READ=frozenset()):
        check("revert/heavy-read-exclusion-dropped-red",
              site_only(["opf/site/tooling.html"]), True)
    # ---------- the CLI over synthetic repos, as the workflow runs it ----------
    git_probe = 1
    try:
        git_probe = subprocess.run(["git", "--version"], capture_output=True,
                                   timeout=GIT_TIMEOUT).returncode
    except OSError:
        git_probe = 1
    if git_probe != 0:
        FAILURES.append("cli/: git is unavailable, the CLI vectors cannot run")
    else:
        env = _git_env()
        with tempfile.TemporaryDirectory() as scratch:
            repo, base = _fixture_repo(Path(scratch), env)
            (repo / "site" / "index.html").write_text("b\n")
            (repo / "opf" / "site" / "a.css").write_text("b\n")
            (repo / ".aiqt" / "manifest.toml").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "site change", env=env)
            check("cli/site-only-true-exit-0", _cli_outcome(repo, base), (0, "site_only=true"))
            check("cli/empty-diff-false-exit-0", _cli_outcome(repo, "HEAD"),
                  (0, "site_only=false"))
            check("cli/bad-revision-exit-2", _cli_outcome(repo, "no-such-revision"),
                  (2, "site_only=false"))
            check("cli/option-revision-exit-2", _cli_outcome(repo, "--cached"),
                  (2, "site_only=false"))
            # ---------- the --ci-decide entry the scope job runs ----------
            # the head still holds only site-allowlisted changes over base here
            check("cidecide/pull-request-site-only-true",
                  _ci_decide_outcome(repo, "pull_request", base,
                                     Path(scratch) / "gh-output-true"),
                  (0, "site_only=true\n"))
            check("cidecide/push-false",
                  _ci_decide_outcome(repo, "push", base, Path(scratch) / "gh-output-push"),
                  (0, "site_only=false\n"))
            check("cidecide/bad-base-false-exit-0",
                  _ci_decide_outcome(repo, "pull_request", "no-such-revision",
                                     Path(scratch) / "gh-output-bad"),
                  (0, "site_only=false\n"))
            check("cidecide/missing-base-false-exit-0",
                  _ci_decide_outcome(repo, "pull_request", None,
                                     Path(scratch) / "gh-output-nobase"),
                  (0, "site_only=false\n"))
            check("cidecide/no-output-exit-1",
                  _ci_decide_outcome(repo, "pull_request", base, None), (1, None))
            unwritable = Path(scratch) / "gh-output-dir"
            os.makedirs(unwritable)
            check("cidecide/unwritable-output-exit-1",
                  _ci_decide_outcome(repo, "pull_request", base, unwritable)[0], 1)
            (repo / "tools" / "t.py").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "tool change", env=env)
            check("cli/mixed-change-false-exit-0", _cli_outcome(repo, base),
                  (0, "site_only=false"))
            check("cidecide/pull-request-mixed-false",
                  _ci_decide_outcome(repo, "pull_request", base,
                                     Path(scratch) / "gh-output-mixed"),
                  (0, "site_only=false\n"))
        with tempfile.TemporaryDirectory() as scratch:
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "mv", "site/index.html", "tools/index.html", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "rename out", env=env)
            check("cli/rename-out-of-tree-false-exit-0", _cli_outcome(repo, base),
                  (0, "site_only=false"))
            _fixture_git(repo, "mv", "tools/index.html", "opf/site/index.html", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "rename within allowlist", env=env)
            head_minus_one = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD^"], check=True, capture_output=True,
                text=True, timeout=GIT_TIMEOUT, env=env).stdout.strip()
            check("cli/rename-into-tree-false-exit-0", _cli_outcome(repo, head_minus_one),
                  (0, "site_only=false"))
        with tempfile.TemporaryDirectory() as scratch:
            # not a repo at all: the diff read fails, the decision is cannot-evaluate
            bare = Path(scratch) / "notrepo"
            os.makedirs(bare)
            _plant_tool(bare)
            check("cli/outside-repo-exit-2", _cli_outcome(bare, "HEAD"),
                  (2, "site_only=false"))
    # usage errors are cannot-evaluate through main, with no decision line; the usage text goes to
    # the error stream, captured here so this suite's own error stream stays clean for the
    # execution-reconciliation harness
    import contextlib
    import io

    def _usage_outcome(argv):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = main(argv)
        return code, stream.getvalue().startswith("usage: ci_site_scope.py")
    check("launch/no-arguments-exit-2", _usage_outcome([]), (2, True))
    check("launch/unknown-flag-exit-2", _usage_outcome(["--unknown"]), (2, True))
    check("launch/merge-base-missing-value-exit-2", _usage_outcome(["--merge-base"]), (2, True))
    check("launch/empty-merge-base-exit-2", _usage_outcome(["--merge-base", ""]), (2, True))

    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("FAIL: ci_site_scope self-test")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS: ci_site_scope self-test: {} unique checks executed (the pure decision admits only "
          "non-empty lists of plain repo-relative paths strictly below site/ or opf/site/ or "
          "exactly one of the three regenerated records, each boundary tested just inside, at, and "
          "outside; each rule patched out flips its vector; the CLI decides over synthetic repos, "
          "reads renames with detection off, and exits 2 on a bad revision, an option-shaped "
          "revision, a missing repo and a usage error); execution set reconciled against "
          "tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


USAGE = ("usage: ci_site_scope.py [--merge-base REV | --ci-decide | --self-test | "
         "--execution-report ABS_PATH]")


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(argv[1])
    if len(argv) == 2 and argv[0] == "--merge-base" and argv[1]:
        return decide(ROOT, argv[1])
    if argv == ["--ci-decide"]:
        return ci_decide()
    print(USAGE, file=sys.stderr)
    return EXIT_CANNOT_EVALUATE


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
