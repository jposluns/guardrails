#!/usr/bin/env python3
"""First-push helper: run the pre-push message/metadata check and the push in ONE process, so the
check GATES the push rather than merely preceding it.

The failure this closes: a check run on its own line, with `git push` on the next line of the same
command or script, can pass control to the push while the check failed (a separate step is not
sequencing that preserves the gate's failure). Here the push is reachable only through this process,
after it has OBSERVED exit 0 from the check it launched itself; a finding (exit 1) and a
cannot-evaluate (exit 2 or anything else) both refuse the push.

  push_branch.py --body-file <abs path> --base <40-hex sha> [--title <text>] [--remote <name>]
  push_branch.py --self-test

The check is tools/check_msg_leaks.py --pre-push (the script beside this one), over the repository
containing the current working directory: the drafted PR body file (composed with --title when one
is given), the changelog.toml release items added in base..HEAD, the commit messages in base..HEAD,
and the current branch name, against the hashed leak denylist, the STRUCTURAL patterns and the
narrow AI-attribution layer. Every input is explicit; there is no ambient default for the body or
the base. On a pass this prints the body file's sha256, so the body later given to the PR can be
compared against the same bytes, then runs `git push --set-upstream <remote> HEAD:refs/heads/<branch>`
for the branch HEAD is on and returns the push's own exit status.

FAIL-CLOSED: an unknown, repeated or valueless argument, a working directory outside a git
repository, a detached HEAD, any non-zero exit from the check, and an unreadable body file after the
check each refuse the push (nothing is pushed), exit 2 (exit 1 when the check itself found
something). The refusal is printed; the check's own output is passed through.

HONEST SCOPE. This helper gates only pushes made THROUGH it: a plain `git push`, an IDE push, or any
later push bypasses it entirely, and the CI scan after the PR opens is the backstop there. A body
edited between this run and the PR creation is caught only by comparing the printed digest or by the
CI run on the edited event. The check's own residuals (paraphrased attribution, unlisted AI names,
obfuscated denylist terms) carry over.

The --self-test builds throwaway git repositories and a bare remote under a scrubbed fixture
environment (tools/_git_fixture_env.py) and asserts, for every finding shape and fail-closed shape,
that the helper exits non-zero AND the remote gained no ref, and that a clean branch pushes.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: push_branch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).resolve()
CHECK = HERE.parent / "check_msg_leaks.py"
# A branch name a push could write through this helper must be a single plain ref component chain:
# check-ref-format validates it below before it is spliced into the refspec.


def _refuse(message):
    print("REFUSED: {}; nothing was pushed.".format(message))


def _toplevel():
    """The repository root containing the working directory; None on any failure, never a guess.
    Each read-only git launch below is written out with its own literal argv (one subcommand per
    site), so what runs is auditable from the source."""
    try:
        proc = subprocess.run(["git", "-C", os.getcwd(), "rev-parse", "--show-toplevel"],
                              capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _current_branch(toplevel):
    """The branch HEAD is on; None on a detached HEAD or any failure."""
    try:
        proc = subprocess.run(["git", "-C", toplevel, "symbolic-ref", "--short", "HEAD"],
                              capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _valid_branch(toplevel, branch):
    """True only when git itself accepts branch as a branch name (it is spliced into the refspec)."""
    try:
        proc = subprocess.run(["git", "-C", toplevel, "check-ref-format", "--branch", branch],
                              capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return False
    return proc.returncode == 0


def parse_argv(argv):
    """Strict flags: --body-file and --base required, --title and --remote optional, each with one
    value, each at most once; anything else refuses (an ignored flag could silently drop an input)."""
    opts = {"body-file": None, "base": None, "title": None, "remote": None}
    arguments = list(argv)
    while arguments:
        flag = arguments.pop(0)
        name = flag[2:] if flag.startswith("--") else None
        if name not in opts:
            return None, "unknown argument"
        if opts[name] is not None:
            return None, "repeated argument: --{}".format(name)
        if not arguments:
            return None, "--{} requires a value".format(name)
        opts[name] = arguments.pop(0)
    if opts["body-file"] is None or opts["base"] is None:
        return None, "--body-file and --base are required"
    if opts["remote"] is None:
        opts["remote"] = "origin"
    return opts, None


def run_push(argv):
    """The gated first push. The check runs as a child of THIS process; the push is reachable only
    after this process observed the check's exit 0, so a failed check cannot be sequenced around."""
    opts, problem = parse_argv(argv)
    if problem is not None:
        _refuse(problem)
        return 2
    toplevel = _toplevel()
    if not toplevel:
        _refuse("the working directory is not inside a git repository")
        return 2
    check_argv = [sys.executable, "-I", "-B", str(CHECK), "--pre-push", "--repo", toplevel,
                  "--body-file", opts["body-file"], "--base", opts["base"]]
    if opts["title"] is not None:
        check_argv += ["--title", opts["title"]]
    try:
        check = subprocess.run(check_argv)       # output passes through; the exit is the verdict
    except OSError as exc:
        _refuse("could not launch the pre-push check ({})".format(type(exc).__name__))
        return 2
    if check.returncode != 0:
        _refuse("the pre-push check did not pass (exit {})".format(check.returncode))
        return 1 if check.returncode == 1 else 2
    branch = _current_branch(toplevel)
    if not branch:
        _refuse("cannot establish the current branch (detached HEAD)")
        return 2
    if not _valid_branch(toplevel, branch):
        _refuse("the current branch name is not a valid branch ref")
        return 2
    try:
        digest = hashlib.sha256(Path(opts["body-file"]).read_bytes()).hexdigest()
    except OSError as exc:
        _refuse("the body file became unreadable after the check ({})".format(type(exc).__name__))
        return 2
    print("pre-push check passed; body sha256: {}".format(digest))
    pushed = subprocess.run(["git", "-C", toplevel, "push", "--set-upstream", opts["remote"],
                             "HEAD:refs/heads/" + branch])
    if pushed.returncode != 0:
        print("push exited non-zero ({})".format(pushed.returncode))
        return 2
    print("push completed; upstream set")
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return run_push(argv)


def _expect(condition, message, failures):
    if not condition:
        failures.append(str(message))


def self_test():
    """Hermetic end-to-end vectors over throwaway repositories and a bare remote: every finding
    shape refuses the push and leaves the remote without the ref; every fail-closed shape does the
    same with exit 2; a clean branch (an empty body included) pushes and prints the body digest."""
    import tempfile
    from _git_fixture_env import git_fixture_env
    from check_leaks import term_hash

    def fixture_git(args, cwd):
        proc = subprocess.run(["git", "-C", str(cwd), *args], env=git_fixture_env(),
                              capture_output=True, encoding="utf-8")
        if proc.returncode != 0:
            raise AssertionError("fixture git {} failed: {}{}".format(args[:1], proc.stdout, proc.stderr))
        return proc.stdout

    def fixture_git_rc(args, cwd):
        return subprocess.run(["git", "-C", str(cwd), *args], env=git_fixture_env(),
                              capture_output=True, encoding="utf-8").returncode

    def helper(args, cwd, **env_overrides):
        return subprocess.run([sys.executable, "-I", "-B", str(HERE), *args], cwd=str(cwd),
                              env=git_fixture_env(**env_overrides), capture_output=True,
                              encoding="utf-8")

    failures = []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        origin = tmp / "origin.git"
        repo = tmp / "repo"
        fixture_git(["init", "-q", "--bare", str(origin)], tmp)
        fixture_git(["init", "-q", "-b", "main", str(repo)], tmp)
        fixture_git(["config", "user.name", "Fixture Maintainer"], repo)
        fixture_git(["config", "user.email", "fixture@example.org"], repo)
        fixture_git(["remote", "add", "origin", str(origin)], repo)
        (repo / "tools").mkdir()
        (repo / "tools" / "leak-hashes.txt").write_text(
            "# maxn 3\n" + term_hash("alpha bravo") + "\n", encoding="utf-8")
        (repo / "changelog.toml").write_text(
            'title = "t"\n[[release]]\nitems = ["base item"]\n', encoding="utf-8")
        (repo / "README.md").write_text("fixture\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "base commit"], repo)
        base = fixture_git(["rev-parse", "HEAD"], repo).strip()
        clean_body = tmp / "body-clean.md"
        clean_body.write_text("A normal body.\n", encoding="utf-8")

        def branch_with_commit(name, message="a clean message", changelog=None):
            fixture_git(["checkout", "-q", "main"], repo)
            fixture_git(["checkout", "-q", "-b", name], repo)
            if changelog is not None:
                (repo / "changelog.toml").write_text(changelog, encoding="utf-8")
            (repo / "work.txt").write_text("work on " + name + "\n", encoding="utf-8")
            fixture_git(["add", "-A"], repo)
            fixture_git(["commit", "-q", "-m", message], repo)

        def ref_exists(name):
            return fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                   "refs/heads/" + name], origin) == 0

        # 1. clean branch, clean body: the push happens, the digest is printed
        branch_with_commit("feat-clean")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 0, ("clean push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-clean"), "clean push must create the remote ref", failures)
        digest = hashlib.sha256(clean_body.read_bytes()).hexdigest()
        _expect("body sha256: " + digest in done.stdout, "digest line missing on a clean push", failures)

        # 2. an empty body file is a valid empty body: still clean
        empty_body = tmp / "body-empty.md"
        empty_body.write_bytes(b"")
        branch_with_commit("feat-empty")
        done = helper(["--body-file", str(empty_body), "--base", base], repo)
        _expect(done.returncode == 0, ("empty body", done.returncode, done.stderr), failures)
        _expect(ref_exists("feat-empty"), "empty-body clean push must create the remote ref", failures)

        # findings: each refuses (exit 1) and the remote gains no ref
        finding_vectors = []
        trailer_body = tmp / "body-trailer.md"
        trailer_body.write_text("Done.\n\nCo-authored-by: Claude <c@example.org>\n", encoding="utf-8")
        branch_with_commit("feat-trailer")
        finding_vectors.append(("feat-trailer", ["--body-file", str(trailer_body), "--base", base]))
        deny_body = tmp / "body-deny.md"
        deny_body.write_text("mentions alpha bravo here\n", encoding="utf-8")
        branch_with_commit("feat-denylist")
        finding_vectors.append(("feat-denylist", ["--body-file", str(deny_body), "--base", base]))
        branch_with_commit("feat-changelog", changelog='title = "t"\n[[release]]\nitems = '
                           '["base item", "notes drafted using ChatGPT"]\n')
        finding_vectors.append(("feat-changelog", ["--body-file", str(clean_body), "--base", base]))
        branch_with_commit("feat-msg", message="touch alpha bravo")
        finding_vectors.append(("feat-msg", ["--body-file", str(clean_body), "--base", base]))
        branch_with_commit("alpha-bravo-fix")
        finding_vectors.append(("alpha-bravo-fix", ["--body-file", str(clean_body), "--base", base]))
        for name, args in finding_vectors:
            fixture_git(["checkout", "-q", name], repo)
            done = helper(args, repo)
            _expect(done.returncode == 1, (name, done.returncode, done.stdout, done.stderr), failures)
            _expect("REFUSED" in done.stdout, (name, "refusal line missing"), failures)
            _expect("body sha256" not in done.stdout, (name, "digest printed on a refusal"), failures)
            _expect(not ref_exists(name), (name, "a finding must not push"), failures)

        # fail-closed: each refuses with exit 2 and the remote gains no ref
        branch_with_commit("feat-failclosed")
        link_body = tmp / "body-link.md"
        link_body.symlink_to(clean_body)
        binary_body = tmp / "body-binary.md"
        binary_body.write_bytes(b"\xff\xfe\x00")
        for label, args in (
                ("relative body", ["--body-file", "body-clean.md", "--base", base]),
                ("missing body", ["--body-file", str(tmp / "absent.md"), "--base", base]),
                ("symlink body", ["--body-file", str(link_body), "--base", base]),
                ("non-UTF-8 body", ["--body-file", str(binary_body), "--base", base]),
                ("non-SHA base", ["--body-file", str(clean_body), "--base", "HEAD"]),
                ("unknown flag", ["--body-file", str(clean_body), "--base", base, "--force", "x"]),
                ("valueless flag", ["--body-file", str(clean_body), "--base"]),
                ("missing required", ["--base", base])):
            done = helper(args, repo)
            _expect(done.returncode == 2, (label, done.returncode, done.stdout, done.stderr), failures)
            _expect("REFUSED" in done.stdout, (label, "refusal line missing"), failures)
            _expect(not ref_exists("feat-failclosed"), (label, "a refusal must not push"), failures)
        branch_with_commit("feat-badtoml", changelog="not toml [")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2, ("bad changelog", done.returncode, done.stderr), failures)
        _expect(not ref_exists("feat-badtoml"), "a malformed changelog must not push", failures)
        fixture_git(["checkout", "-q", "--detach", "main"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2, ("detached HEAD", done.returncode, done.stderr), failures)
        norepo = tmp / "norepo"
        norepo.mkdir()
        # The ceiling stops repository discovery at tmp, so the vector holds even where the
        # temporary directory itself sits under some outer repository.
        done = helper(["--body-file", str(clean_body), "--base", base], norepo,
                      GIT_CEILING_DIRECTORIES=str(tmp))
        _expect(done.returncode == 2, ("outside a repository", done.returncode, done.stderr), failures)
    if failures:
        print("SELF-TEST FAIL ({} failure(s)):".format(len(failures)))
        for failure in failures:
            print("  " + failure)
        return 1
    print("SELF-TEST PASS: a clean branch (an empty body included) pushes and prints the body "
          "digest; every finding shape (an attribution trailer in the body, a denylist term in the "
          "body, a changelog item added in base..HEAD, a commit message in the range, the branch "
          "name) refuses with exit 1 and no remote ref; and every fail-closed shape (a relative, "
          "missing, symlinked or non-UTF-8 body, a non-SHA base, an unknown or valueless flag, a "
          "missing required flag, a malformed changelog, a detached HEAD, a non-repository working "
          "directory) refuses with exit 2 and no remote ref")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
